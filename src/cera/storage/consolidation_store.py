"""Deferred derived-authority transactions and regenerable branch views."""

from __future__ import annotations

import json
import sqlite3

from cera.consolidation.models import (
    ConsolidationBundle,
    ConsolidationCommitReceipt,
    ConsolidationStatus,
    DerivedView,
    DerivedViewBuildReceipt,
    DerivedViewKind,
    StoredConsolidation,
)
from cera.contracts import EvidenceAuthority, EvidenceRecordType, Visibility
from cera.errors import StateConflictError, TransactionError
from cera.evidence.models import EvidenceDocument
from cera.ids import IdKind, TypedId, deterministic_id, require_kind
from cera.schema import from_mapping
from cera.serialization import canonical_json, domain_sha256, text_sha256

from .models import AuthorityRecord
from .evidence_store import _searchable_from_document


_DERIVED_TYPES = {
    EvidenceRecordType.MEMORY,
    EvidenceRecordType.RELATIONSHIP,
    EvidenceRecordType.THREAD,
    EvidenceRecordType.DEVELOPMENT,
}


class ConsolidationStoreMixin:
    """SQLite primitives mixed into :class:`SQLiteAuthorityStore`."""

    def prepare_consolidation(self, bundle: ConsolidationBundle) -> ConsolidationStatus:
        with self._connect() as connection:
            self._begin(connection)
            existing = connection.execute(
                "SELECT * FROM consolidation_journal WHERE idempotency_key = ? "
                "OR transaction_id = ? OR request_id = ?",
                (
                    bundle.idempotency_key,
                    str(bundle.transaction_id),
                    str(bundle.request_id),
                ),
            ).fetchone()
            if existing is not None:
                if (
                    existing["idempotency_key"] != bundle.idempotency_key
                    or existing["transaction_id"] != str(bundle.transaction_id)
                    or existing["request_id"] != str(bundle.request_id)
                    or existing["bundle_sha256"] != bundle.bundle_sha256
                ):
                    connection.rollback()
                    raise TransactionError(
                        "consolidation identity or idempotency key was reused differently"
                    )
                connection.commit()
                return ConsolidationStatus(existing["status"])
            self._assert_consolidation_expectation(connection, bundle)
            connection.execute(
                "INSERT INTO consolidation_journal("
                "transaction_id, idempotency_key, request_id, branch_id, "
                "expected_generation, expected_head_artifact_id, "
                "expected_authority_revision, genesis_revision_id, snapshot_token, "
                "request_sha256, proposal_sha256, bundle_json, bundle_sha256, status, "
                "receipt_id, rollback_reason, prepared_at, completed_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'prepared', "
                "NULL, NULL, ?, NULL)",
                (
                    str(bundle.transaction_id),
                    bundle.idempotency_key,
                    str(bundle.request_id),
                    str(bundle.branch_id),
                    bundle.expected_generation,
                    str(bundle.expected_head_artifact_id),
                    bundle.expected_authority_revision,
                    str(bundle.genesis_revision_id),
                    str(bundle.snapshot_token),
                    bundle.request_sha256,
                    bundle.proposal_sha256,
                    canonical_json(bundle),
                    bundle.bundle_sha256,
                    self._now(),
                ),
            )
            connection.commit()
        return ConsolidationStatus.PREPARED

    def finalize_consolidation(self, bundle: ConsolidationBundle) -> StoredConsolidation:
        with self._connect() as connection:
            self._begin(connection)
            row = connection.execute(
                "SELECT * FROM consolidation_journal WHERE transaction_id = ?",
                (str(bundle.transaction_id),),
            ).fetchone()
            if row is None:
                connection.rollback()
                raise TransactionError("consolidation must be prepared before finalization")
            if (
                row["idempotency_key"] != bundle.idempotency_key
                or row["request_id"] != str(bundle.request_id)
                or row["bundle_sha256"] != bundle.bundle_sha256
            ):
                connection.rollback()
                raise TransactionError("prepared consolidation does not match bundle")
            status = ConsolidationStatus(row["status"])
            if status is ConsolidationStatus.COMMITTED:
                receipt = self._consolidation_receipt_in_connection(
                    connection, row["receipt_id"]
                )
                connection.commit()
                return StoredConsolidation(receipt=receipt, exact_replay=True)
            if status is ConsolidationStatus.ROLLED_BACK:
                connection.rollback()
                raise TransactionError("rolled-back consolidation requires a new identity")
            try:
                self._assert_consolidation_expectation(connection, bundle)
            except StateConflictError as exc:
                connection.execute(
                    "UPDATE consolidation_journal SET status = 'rolled_back', "
                    "rollback_reason = ?, completed_at = ? WHERE transaction_id = ?",
                    (str(exc), self._now(), str(bundle.transaction_id)),
                )
                connection.commit()
                raise
            try:
                now = self._now()
                for document in bundle.documents:
                    record = AuthorityRecord.from_payload(
                        record_id=document.record_id,
                        branch_id=bundle.branch_id,
                        artifact_id=bundle.expected_head_artifact_id,
                        record_type=document.record_type.value,
                        payload=document,
                        supersedes=document.supersedes,
                    )
                    connection.execute(
                        "INSERT INTO authority_records("
                        "record_id, branch_id, artifact_id, record_type, payload_json, "
                        "payload_sha256, supersedes_json, transaction_id, created_at"
                        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            str(record.record_id),
                            str(record.branch_id),
                            str(record.artifact_id),
                            record.record_type,
                            record.payload_json,
                            record.payload_sha256,
                            canonical_json(record.supersedes),
                            str(bundle.transaction_id),
                            now,
                        ),
                    )
                    payload = json.loads(record.payload_json)
                    connection.execute(
                        "INSERT INTO evidence_search_fts("
                        "record_key, source_kind, record_id, record_sha256, searchable_text"
                        ") VALUES (?, 'authority', ?, ?, ?)",
                        (
                            f"authority|{record.record_id}",
                            str(record.record_id),
                            record.payload_sha256,
                            _searchable_from_document(payload),
                        ),
                    )
                for receipt_record in bundle.receipt_records:
                    connection.execute(
                        "INSERT INTO consolidation_receipt_records("
                        "receipt_id, transaction_id, category, schema_version, "
                        "payload_json, payload_sha256, created_at"
                        ") VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (
                            str(receipt_record.receipt_id),
                            str(bundle.transaction_id),
                            receipt_record.category.value,
                            receipt_record.schema_version,
                            receipt_record.payload_json,
                            receipt_record.payload_sha256,
                            now,
                        ),
                    )
                self._after_consolidation_records_insert(connection, bundle)
                updated = connection.execute(
                    "UPDATE branches SET authority_revision = authority_revision + 1 "
                    "WHERE branch_id = ? AND generation = ? AND head_artifact_id = ? "
                    "AND authority_revision = ?",
                    (
                        str(bundle.branch_id),
                        bundle.expected_generation,
                        str(bundle.expected_head_artifact_id),
                        bundle.expected_authority_revision,
                    ),
                )
                if updated.rowcount != 1:
                    raise StateConflictError("branch authority changed during consolidation")
                receipt = self._make_consolidation_receipt(bundle)
                connection.execute(
                    "INSERT INTO consolidation_receipts("
                    "commit_id, transaction_id, request_id, branch_id, receipt_json, "
                    "transaction_sha256, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        str(receipt.commit_id),
                        str(receipt.transaction_id),
                        str(receipt.request_id),
                        str(receipt.branch_id),
                        canonical_json(receipt),
                        receipt.transaction_sha256,
                        now,
                    ),
                )
                connection.execute(
                    "UPDATE consolidation_journal SET status = 'committed', receipt_id = ?, "
                    "completed_at = ? WHERE transaction_id = ?",
                    (str(receipt.commit_id), now, str(bundle.transaction_id)),
                )
                connection.commit()
                return StoredConsolidation(receipt=receipt, exact_replay=False)
            except Exception as exc:
                connection.rollback()
                if isinstance(exc, (StateConflictError, TransactionError)):
                    raise
                raise TransactionError(
                    f"consolidation transaction failed and was rolled back: {exc}"
                ) from exc

    def commit_consolidation(self, bundle: ConsolidationBundle) -> StoredConsolidation:
        status = self.prepare_consolidation(bundle)
        if status is ConsolidationStatus.ROLLED_BACK:
            raise TransactionError("rolled-back consolidation requires a new identity")
        return self.finalize_consolidation(bundle)

    def pending_consolidations(self) -> tuple[ConsolidationBundle, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT bundle_json FROM consolidation_journal "
                "WHERE status = 'prepared' ORDER BY prepared_at, transaction_id"
            ).fetchall()
        return tuple(
            from_mapping(ConsolidationBundle, json.loads(row["bundle_json"])) for row in rows
        )

    def get_consolidation_receipt(
        self, commit_id: TypedId
    ) -> ConsolidationCommitReceipt:
        require_kind(commit_id, IdKind.COMMIT, "commit_id")
        with self._connect() as connection:
            return self._consolidation_receipt_in_connection(connection, str(commit_id))

    def find_consolidation_by_request_id(
        self, request_id: TypedId
    ) -> StoredConsolidation | None:
        require_kind(request_id, IdKind.REQUEST, "request_id")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT receipt_id, status FROM consolidation_journal WHERE request_id = ?",
                (str(request_id),),
            ).fetchone()
            if row is None or row["status"] != ConsolidationStatus.COMMITTED.value:
                return None
            receipt = self._consolidation_receipt_in_connection(
                connection, row["receipt_id"]
            )
        return StoredConsolidation(receipt=receipt, exact_replay=True)

    @staticmethod
    def _consolidation_receipt_in_connection(
        connection: sqlite3.Connection, commit_id: str
    ) -> ConsolidationCommitReceipt:
        row = connection.execute(
            "SELECT receipt_json FROM consolidation_receipts WHERE commit_id = ?",
            (commit_id,),
        ).fetchone()
        if row is None:
            raise TransactionError("consolidation receipt does not exist")
        return from_mapping(ConsolidationCommitReceipt, json.loads(row["receipt_json"]))

    def _assert_consolidation_expectation(
        self, connection: sqlite3.Connection, bundle: ConsolidationBundle
    ) -> None:
        branch = connection.execute(
            "SELECT world_id, head_artifact_id, generation, authority_revision, status "
            "FROM branches WHERE branch_id = ?",
            (str(bundle.branch_id),),
        ).fetchone()
        if branch is None:
            raise StateConflictError("consolidation branch does not exist")
        if branch["status"] != "active":
            raise StateConflictError("consolidation branch is not active")
        if (
            int(branch["generation"]) != bundle.expected_generation
            or branch["head_artifact_id"] != str(bundle.expected_head_artifact_id)
            or int(branch["authority_revision"]) != bundle.expected_authority_revision
        ):
            raise StateConflictError("consolidation snapshot is stale")
        binding = connection.execute(
            "SELECT revision_id FROM world_genesis_bindings WHERE world_id = ?",
            (branch["world_id"],),
        ).fetchone()
        if binding is None or binding["revision_id"] != str(bundle.genesis_revision_id):
            raise StateConflictError("consolidation Genesis binding changed")

    @staticmethod
    def _make_consolidation_receipt(
        bundle: ConsolidationBundle,
    ) -> ConsolidationCommitReceipt:
        superseded = tuple(
            record_id for document in bundle.documents for record_id in document.supersedes
        )
        return ConsolidationCommitReceipt(
            schema_version=ConsolidationCommitReceipt.SCHEMA_VERSION,
            commit_id=deterministic_id(
                IdKind.COMMIT, "cera.consolidation.commit.v1", str(bundle.transaction_id)
            ),
            transaction_id=bundle.transaction_id,
            request_id=bundle.request_id,
            branch_id=bundle.branch_id,
            head_artifact_id=bundle.expected_head_artifact_id,
            generation=bundle.expected_generation,
            authority_revision_before=bundle.expected_authority_revision,
            authority_revision_after=bundle.expected_authority_revision + 1,
            genesis_revision_id=bundle.genesis_revision_id,
            inserted_record_ids=tuple(document.record_id for document in bundle.documents),
            superseded_record_ids=superseded,
            validation_receipt_id=bundle.validation_receipt_id,
            reasoner_receipt_id=bundle.reasoner_receipt_id,
            lookup_receipt_ids=bundle.lookup_receipt_ids,
            transaction_sha256=bundle.bundle_sha256,
            story_artifact_writes=0,
            genesis_writes=0,
            outcome="committed",
        )

    def _after_consolidation_records_insert(
        self, connection: sqlite3.Connection, bundle: ConsolidationBundle
    ) -> None:
        """Test seam for a failure after inserts but before branch revision advance."""

    def rebuild_derived_views(self, branch_id: TypedId) -> DerivedViewBuildReceipt:
        require_kind(branch_id, IdKind.BRANCH, "branch_id")
        branch = self.get_branch(branch_id)
        if branch.head_artifact_id is None:
            raise TransactionError("derived views require a committed branch head")
        documents = self._current_derived_documents(branch_id, branch.head_artifact_id)
        source_set_sha256 = domain_sha256(
            "cera.derived_view.source_set.v1",
            tuple((str(value.record_id), value.record_sha256) for value in documents),
        )
        views = self._build_derived_views(
            branch_id=branch_id,
            head_artifact_id=branch.head_artifact_id,
            authority_revision=branch.authority_revision,
            documents=documents,
            source_set_sha256=source_set_sha256,
        )
        with self._connect() as connection:
            self._begin(connection)
            connection.execute("DELETE FROM derived_views WHERE branch_id = ?", (str(branch_id),))
            now = self._now()
            for view in views:
                connection.execute(
                    "INSERT INTO derived_views("
                    "branch_id, head_artifact_id, authority_revision, view_key, view_kind, "
                    "visibility, owner_id, payload_json, source_set_sha256, view_sha256, built_at"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        str(view.branch_id),
                        str(view.head_artifact_id),
                        view.authority_revision,
                        view.view_key,
                        view.view_kind.value,
                        view.visibility.value,
                        str(view.owner_id) if view.owner_id is not None else None,
                        view.payload_json,
                        view.source_set_sha256,
                        view.view_sha256,
                        now,
                    ),
                )
            connection.commit()
        index_rows = self.rebuild_evidence_search_index()
        return DerivedViewBuildReceipt(
            schema_version=DerivedViewBuildReceipt.SCHEMA_VERSION,
            validation_receipt_id=deterministic_id(
                IdKind.VALIDATION,
                "cera.derived_view.build.v1",
                f"{branch_id}|{branch.head_artifact_id}|{branch.authority_revision}|"
                f"{source_set_sha256}",
            ),
            branch_id=branch_id,
            head_artifact_id=branch.head_artifact_id,
            authority_revision=branch.authority_revision,
            source_set_sha256=source_set_sha256,
            view_keys=tuple(value.view_key for value in views),
            evidence_index_rows=index_rows,
            authority_store_writes=0,
        )

    def get_derived_view(
        self,
        branch_id: TypedId,
        view_key: str,
        *,
        perspective_id: TypedId | None = None,
        allow_system_private: bool = False,
    ) -> DerivedView:
        require_kind(branch_id, IdKind.BRANCH, "branch_id")
        if perspective_id is not None:
            require_kind(perspective_id, IdKind.CHARACTER, "perspective_id")
        branch = self.get_branch(branch_id)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM derived_views WHERE branch_id = ? AND head_artifact_id = ? "
                "AND authority_revision = ? AND view_key = ?",
                (
                    str(branch_id),
                    str(branch.head_artifact_id),
                    branch.authority_revision,
                    view_key,
                ),
            ).fetchone()
        if row is None:
            raise TransactionError("derived view is absent or stale and must be rebuilt")
        view = DerivedView(
            schema_version=DerivedView.SCHEMA_VERSION,
            branch_id=branch_id,
            head_artifact_id=TypedId.parse(row["head_artifact_id"], IdKind.ARTIFACT),
            authority_revision=int(row["authority_revision"]),
            view_key=row["view_key"],
            view_kind=DerivedViewKind(row["view_kind"]),
            visibility=Visibility(row["visibility"]),
            owner_id=(
                TypedId.parse(row["owner_id"], IdKind.CHARACTER)
                if row["owner_id"] is not None
                else None
            ),
            payload_json=row["payload_json"],
            source_set_sha256=row["source_set_sha256"],
            view_sha256=row["view_sha256"],
        )
        if view.visibility is Visibility.SYSTEM_PRIVATE and not allow_system_private:
            raise TransactionError("system-private derived view access denied")
        if view.visibility is Visibility.OWNER_PRIVATE and perspective_id != view.owner_id:
            raise TransactionError("owner-private derived view access denied")
        return view

    def _current_derived_documents(
        self, branch_id: TypedId, head_artifact_id: TypedId
    ) -> tuple[EvidenceDocument, ...]:
        documents: list[EvidenceDocument] = []
        for record in self.authority_records_at_head(branch_id, head_artifact_id):
            payload = json.loads(record.payload_json)
            if payload.get("schema_version") != EvidenceDocument.SCHEMA_VERSION:
                continue
            document = from_mapping(EvidenceDocument, payload)
            if (
                document.record_type in _DERIVED_TYPES
                and document.authority
                in {
                    EvidenceAuthority.VALIDATED_DERIVED,
                    EvidenceAuthority.SYNTHETIC_FIXTURE,
                }
            ):
                documents.append(document)
        superseded = {
            record_id for document in documents for record_id in document.supersedes
        }
        return tuple(
            sorted(
                (value for value in documents if value.record_id not in superseded),
                key=lambda value: str(value.record_id),
            )
        )

    @staticmethod
    def _build_derived_views(
        *,
        branch_id: TypedId,
        head_artifact_id: TypedId,
        authority_revision: int,
        documents: tuple[EvidenceDocument, ...],
        source_set_sha256: str,
    ) -> tuple[DerivedView, ...]:
        def entry(document: EvidenceDocument) -> dict[str, object]:
            return {
                "record_id": str(document.record_id),
                "record_version": document.record_version,
                "record_type": document.record_type.value,
                "title": document.title,
                "abstract": document.abstract,
                "claim": document.claim,
                "owner_id": str(document.owner_id) if document.owner_id else None,
                "knowledge_owner_ids": [str(value) for value in document.knowledge_owner_ids],
                "visibility": document.visibility.value,
                "certainty": document.certainty.value,
                "source_refs": [str(value) for value in document.source_refs],
                "tags": list(document.tags),
            }

        common = {
            "authoritative": False,
            "branch_id": str(branch_id),
            "head_artifact_id": str(head_artifact_id),
            "authority_revision": authority_revision,
            "source_set_sha256": source_set_sha256,
        }
        public_documents = tuple(
            value
            for value in documents
            if value.visibility is Visibility.PUBLIC and not value.knowledge_owner_ids
        )
        views: list[DerivedView] = [
            DerivedView.create(
                branch_id=branch_id,
                head_artifact_id=head_artifact_id,
                authority_revision=authority_revision,
                view_key="public_summary",
                view_kind=DerivedViewKind.PUBLIC_SUMMARY,
                visibility=Visibility.PUBLIC,
                owner_id=None,
                payload={**common, "entries": [entry(value) for value in public_documents]},
                source_set_sha256=source_set_sha256,
            ),
            DerivedView.create(
                branch_id=branch_id,
                head_artifact_id=head_artifact_id,
                authority_revision=authority_revision,
                view_key="system_index",
                view_kind=DerivedViewKind.SYSTEM_INDEX,
                visibility=Visibility.SYSTEM_PRIVATE,
                owner_id=None,
                payload={**common, "entries": [entry(value) for value in documents]},
                source_set_sha256=source_set_sha256,
            ),
        ]
        owner_ids = sorted(
            {
                value
                for document in documents
                for value in (
                    *((document.owner_id,) if document.owner_id is not None else ()),
                    *document.knowledge_owner_ids,
                )
            },
            key=str,
        )
        for owner_id in owner_ids:
            owner_documents = tuple(
                value
                for value in documents
                if value.visibility is not Visibility.SYSTEM_PRIVATE
                and (
                    (value.visibility is Visibility.OWNER_PRIVATE and value.owner_id == owner_id)
                    or (
                        value.visibility in {Visibility.PUBLIC, Visibility.SHARED}
                        and (
                            not value.knowledge_owner_ids
                            or owner_id in value.knowledge_owner_ids
                        )
                    )
                )
            )
            views.append(
                DerivedView.create(
                    branch_id=branch_id,
                    head_artifact_id=head_artifact_id,
                    authority_revision=authority_revision,
                    view_key=f"owner_summary|{owner_id}",
                    view_kind=DerivedViewKind.OWNER_SUMMARY,
                    visibility=Visibility.OWNER_PRIVATE,
                    owner_id=owner_id,
                    payload={**common, "owner_id": str(owner_id), "entries": [
                        entry(value) for value in owner_documents
                    ]},
                    source_set_sha256=source_set_sha256,
                )
            )
        return tuple(views)
