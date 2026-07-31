"""Read-side authority access and rebuildable evidence search index."""

from __future__ import annotations

import json
import re
import sqlite3

from cera.errors import ErrorCode, EvidenceServiceError, TransactionError
from cera.genesis.models import GenesisPackageClass
from cera.ids import IdKind, TypedId, require_kind
from cera.serialization import canonical_json

from .models import AuthorityRecord


class EvidenceStoreMixin:
    """SQLite evidence primitives; authorization remains in EvidenceService."""

    def genesis_package_class(self, revision_id: TypedId) -> GenesisPackageClass:
        require_kind(revision_id, IdKind.GENESIS_REVISION, "revision_id")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT package_class FROM genesis_revisions WHERE revision_id = ?",
                (str(revision_id),),
            ).fetchone()
        if row is None:
            raise TransactionError(f"Genesis revision does not exist: {revision_id}")
        return GenesisPackageClass(row["package_class"])

    def authority_records_at_head(
        self,
        branch_id: TypedId,
        head_artifact_id: TypedId | None,
    ) -> tuple[AuthorityRecord, ...]:
        require_kind(branch_id, IdKind.BRANCH, "branch_id")
        if head_artifact_id is None:
            return ()
        require_kind(head_artifact_id, IdKind.ARTIFACT, "head_artifact_id")
        with self._connect() as connection:
            head = connection.execute(
                "SELECT artifact_id FROM artifacts WHERE artifact_id = ?",
                (str(head_artifact_id),),
            ).fetchone()
            if head is None:
                raise TransactionError("snapshot head artifact does not exist")
            rows = connection.execute(
                "WITH RECURSIVE artifact_lineage(artifact_id, parent_artifact_id, depth) AS ("
                " SELECT artifact_id, parent_artifact_id, 0 FROM artifacts WHERE artifact_id = ?"
                " UNION ALL"
                " SELECT a.artifact_id, a.parent_artifact_id, artifact_lineage.depth + 1"
                " FROM artifacts a JOIN artifact_lineage"
                " ON a.artifact_id = artifact_lineage.parent_artifact_id"
                "), branch_lineage(branch_id, parent_branch_id, created_at, visible_until) AS ("
                " SELECT branch_id, parent_branch_id, created_at, NULL"
                " FROM branches WHERE branch_id = ?"
                " UNION ALL"
                " SELECT parent.branch_id, parent.parent_branch_id, parent.created_at,"
                " child.created_at"
                " FROM branches parent JOIN branch_lineage child"
                " ON parent.branch_id = child.parent_branch_id"
                ") SELECT ar.record_id, ar.branch_id, ar.artifact_id, ar.record_type, "
                "ar.payload_json, ar.payload_sha256, ar.supersedes_json "
                "FROM authority_records ar"
                " JOIN artifact_lineage ON artifact_lineage.artifact_id = ar.artifact_id"
                " JOIN branch_lineage ON branch_lineage.branch_id = ar.branch_id"
                " WHERE branch_lineage.visible_until IS NULL"
                " OR ar.created_at <= branch_lineage.visible_until "
                "ORDER BY artifact_lineage.depth DESC, ar.created_at, ar.record_id",
                (str(head_artifact_id), str(branch_id)),
            ).fetchall()
        return tuple(
            AuthorityRecord(
                record_id=TypedId.parse(row["record_id"]),
                branch_id=TypedId.parse(row["branch_id"], IdKind.BRANCH),
                artifact_id=TypedId.parse(row["artifact_id"], IdKind.ARTIFACT),
                record_type=row["record_type"],
                payload_json=row["payload_json"],
                payload_sha256=row["payload_sha256"],
                supersedes=tuple(
                    TypedId.parse(value) for value in json.loads(row["supersedes_json"])
                ),
            )
            for row in rows
        )

    def rebuild_evidence_search_index(self) -> int:
        rows = self._authoritative_search_rows()
        with self._connect() as connection:
            self._begin(connection)
            connection.execute("DELETE FROM evidence_search_fts")
            connection.executemany(
                "INSERT INTO evidence_search_fts("
                "record_key, source_kind, record_id, record_sha256, searchable_text"
                ") VALUES (?, ?, ?, ?, ?)",
                rows,
            )
            connection.commit()
        return len(rows)

    def validate_evidence_search_index(self) -> None:
        expected = self._authoritative_search_rows()
        with self._connect() as connection:
            actual = tuple(
                tuple(row)
                for row in connection.execute(
                    "SELECT record_key, source_kind, record_id, record_sha256, "
                    "searchable_text FROM evidence_search_fts ORDER BY record_key"
                ).fetchall()
            )
        if actual != expected:
            raise EvidenceServiceError(
                ErrorCode.EVIDENCE_INDEX_INVALID,
                "evidence search index is stale or corrupt; explicit rebuild required",
            )

    def evidence_index_candidates(self, terms: tuple[str, ...]) -> tuple[str, ...]:
        self.validate_evidence_search_index()
        with self._connect() as connection:
            if not terms:
                rows = connection.execute(
                    "SELECT record_key FROM evidence_search_fts ORDER BY record_key"
                ).fetchall()
            else:
                expression = " AND ".join(_fts_term_expression(term) for term in terms)
                rows = connection.execute(
                    "SELECT record_key FROM evidence_search_fts "
                    "WHERE evidence_search_fts MATCH ? ORDER BY rank, record_key",
                    (expression,),
                ).fetchall()
        return tuple(row["record_key"] for row in rows)

    def _authoritative_search_rows(
        self,
    ) -> tuple[tuple[str, str, str, str, str], ...]:
        rows: list[tuple[str, str, str, str, str]] = []
        with self._connect() as connection:
            genesis_rows = connection.execute(
                "SELECT record_id, record_sha256, record_json FROM genesis_records"
            ).fetchall()
            authority_rows = connection.execute(
                "SELECT record_id, payload_sha256, payload_json FROM authority_records"
            ).fetchall()
        for row in genesis_rows:
            payload = json.loads(row["record_json"])
            searchable = _searchable_from_genesis(payload)
            rows.append(
                (
                    f"genesis|{row['record_id']}",
                    "genesis",
                    row["record_id"],
                    row["record_sha256"],
                    searchable,
                )
            )
        for row in authority_rows:
            payload = json.loads(row["payload_json"])
            if payload.get("schema_version") != "cera.evidence_document.v1":
                continue
            searchable = _searchable_from_document(payload)
            rows.append(
                (
                    f"authority|{row['record_id']}",
                    "authority",
                    row["record_id"],
                    row["payload_sha256"],
                    searchable,
                )
            )
        rows.sort(key=lambda value: value[0])
        return tuple(rows)

    def evidence_index_row_count(self) -> int:
        with self._connect() as connection:
            return int(connection.execute("SELECT COUNT(*) FROM evidence_search_fts").fetchone()[0])


def _searchable_from_genesis(payload: dict[str, object]) -> str:
    return _normalized_search_text(
        payload.get("claim", ""),
        payload.get("payload_json", ""),
        payload.get("tags", ()),
        payload.get("subject_ids", ()),
        payload.get("record_type", ""),
    )


def _searchable_from_document(payload: dict[str, object]) -> str:
    return _normalized_search_text(
        payload.get("title", ""),
        payload.get("abstract", ""),
        payload.get("claim", ""),
        payload.get("sections_json", ""),
        payload.get("tags", ()),
        payload.get("subject_ids", ()),
        payload.get("record_type", ""),
    )


def _normalized_search_text(*values: object) -> str:
    parts: list[str] = []
    for value in values:
        if isinstance(value, str):
            parts.append(value)
        elif isinstance(value, (list, tuple)):
            parts.extend(str(item) for item in value)
        else:
            parts.append(canonical_json(value))
    return " ".join(" ".join(parts).split()).casefold()


def _fts_literal(value: str) -> str:
    normalized = " ".join(value.split()).replace('"', '""')
    return f'"{normalized}"'


def _fts_term_expression(value: str) -> str:
    """Match either the exact phrase or all normalized lexical tokens.

    Semantic paraphrases still come from an explicit bounded query plan. This
    function only prevents harmless punctuation, inflection, and phrase-order
    differences from turning the index into an exact-test-phrase oracle.
    """

    normalized = " ".join(value.split())
    tokens = tuple(
        dict.fromkeys(
            token
            for token in re.findall(r"[A-Za-z0-9]+", normalized.casefold())
            if len(token) > 1
        )
    )
    phrase = _fts_literal(normalized)
    if len(tokens) <= 1:
        return phrase
    token_expression = " AND ".join(_fts_literal(token) for token in tokens)
    return f"({phrase} OR ({token_expression}))"
