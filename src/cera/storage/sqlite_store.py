"""SQLite WAL implementation of CERA's transactional authority store."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
import json
from pathlib import Path
import sqlite3
from typing import Iterator

from cera.contracts import AcceptedStoryArtifact, CommitReceipt
from cera.errors import StateConflictError, TransactionError
from cera.ids import IdKind, TypedId, deterministic_id, require_kind
from cera.schema import from_mapping
from cera.serialization import canonical_json, domain_sha256, text_sha256, to_primitive

from .genesis_store import GenesisStoreMixin
from .evidence_store import EvidenceStoreMixin
from .evidence_store import _searchable_from_document
from .blocked_store import BlockedTurnStoreMixin
from .consolidation_store import ConsolidationStoreMixin
from .post_publication_store import PostPublicationStoreMixin
from .stage_audit_store import StageAuditStoreMixin
from .creator_review_store import CreatorReviewStoreMixin
from .reasoner_session_store import ReasonerSessionStoreMixin
from .migrations import MIGRATIONS, apply_migrations
from .models import (
    BranchState,
    CommitMode,
    JournalEntry,
    JournalStatus,
    RecoveryReport,
    ReceiptCategory,
    ReceiptRecord,
    StoredCommit,
    TurnCommitBundle,
)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


def _typed(value: str | None, kind: IdKind) -> TypedId | None:
    return None if value is None else TypedId.parse(value, kind)


class SQLiteAuthorityStore(
    ReasonerSessionStoreMixin,
    CreatorReviewStoreMixin,
    StageAuditStoreMixin,
    PostPublicationStoreMixin,
    ConsolidationStoreMixin,
    BlockedTurnStoreMixin,
    EvidenceStoreMixin,
    GenesisStoreMixin,
):
    """Branch-aware store with explicit prepare/finalize recovery semantics.

    Each method opens a short-lived connection. A prepared transaction is
    therefore durable independently of finalization and can be audited or
    rolled back after a process restart.
    """

    def __init__(
        self,
        database_path: str | Path,
        *,
        busy_timeout_ms: int = 5_000,
        allow_synthetic_genesis: bool = False,
    ) -> None:
        self.database_path = Path(database_path).resolve()
        if busy_timeout_ms <= 0:
            raise ValueError("busy_timeout_ms must be positive")
        self.busy_timeout_ms = busy_timeout_ms
        self.allow_synthetic_genesis = allow_synthetic_genesis
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            hashes = {version: text_sha256(sql) for version, sql in MIGRATIONS.items()}
            apply_migrations(connection, _utc_now(), hashes)

    def _new_connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.database_path,
            timeout=self.busy_timeout_ms / 1_000,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(f"PRAGMA busy_timeout = {self.busy_timeout_ms}")
        journal_mode = str(connection.execute("PRAGMA journal_mode = WAL").fetchone()[0])
        if journal_mode.lower() != "wal":
            connection.close()
            raise TransactionError(f"SQLite refused WAL mode: {journal_mode}")
        connection.execute("PRAGMA synchronous = FULL")
        return connection

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = self._new_connection()
        try:
            yield connection
        finally:
            connection.close()

    @staticmethod
    def _begin(connection: sqlite3.Connection) -> None:
        connection.execute("BEGIN IMMEDIATE")

    @staticmethod
    def _now() -> str:
        return _utc_now()

    def create_world(self, world_id: TypedId) -> None:
        require_kind(world_id, IdKind.WORLD, "world_id")
        with self._connect() as connection:
            self._begin(connection)
            try:
                connection.execute(
                    "INSERT INTO worlds(world_id, created_at) VALUES (?, ?)",
                    (str(world_id), _utc_now()),
                )
                connection.commit()
            except sqlite3.IntegrityError as exc:
                connection.rollback()
                raise TransactionError(f"world creation failed: {exc}") from exc

    def create_root_branch(self, world_id: TypedId, branch_id: TypedId) -> BranchState:
        require_kind(world_id, IdKind.WORLD, "world_id")
        require_kind(branch_id, IdKind.BRANCH, "branch_id")
        with self._connect() as connection:
            self._begin(connection)
            try:
                connection.execute(
                    "INSERT INTO branches("
                    "branch_id, world_id, parent_branch_id, fork_artifact_id, "
                    "head_artifact_id, generation, fork_parent_generation, status, created_at"
                    ") VALUES (?, ?, NULL, NULL, NULL, 0, NULL, 'active', ?)",
                    (str(branch_id), str(world_id), _utc_now()),
                )
                connection.commit()
            except sqlite3.IntegrityError as exc:
                connection.rollback()
                raise TransactionError(f"root branch creation failed: {exc}") from exc
        return self.get_branch(branch_id)

    def branches_for_world(self, world_id: TypedId) -> tuple[BranchState, ...]:
        """Return every branch in deterministic creation order for one world."""

        require_kind(world_id, IdKind.WORLD, "world_id")
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT branch_id FROM branches WHERE world_id = ? "
                "ORDER BY created_at, branch_id",
                (str(world_id),),
            ).fetchall()
        return tuple(
            self.get_branch(TypedId.parse(row["branch_id"], IdKind.BRANCH))
            for row in rows
        )

    def fork_branch(self, parent_branch_id: TypedId, child_branch_id: TypedId) -> BranchState:
        require_kind(parent_branch_id, IdKind.BRANCH, "parent_branch_id")
        require_kind(child_branch_id, IdKind.BRANCH, "child_branch_id")
        with self._connect() as connection:
            self._begin(connection)
            parent = connection.execute(
                "SELECT world_id, head_artifact_id, generation, status FROM branches "
                "WHERE branch_id = ?",
                (str(parent_branch_id),),
            ).fetchone()
            if parent is None:
                connection.rollback()
                raise TransactionError(f"parent branch does not exist: {parent_branch_id}")
            if parent["status"] != "active":
                connection.rollback()
                raise TransactionError("cannot fork a closed branch")
            try:
                connection.execute(
                    "INSERT INTO branches("
                    "branch_id, world_id, parent_branch_id, fork_artifact_id, "
                    "head_artifact_id, generation, fork_parent_generation, status, created_at"
                    ") VALUES (?, ?, ?, ?, ?, 0, ?, 'active', ?)",
                    (
                        str(child_branch_id),
                        parent["world_id"],
                        str(parent_branch_id),
                        parent["head_artifact_id"],
                        parent["head_artifact_id"],
                        parent["generation"],
                        _utc_now(),
                    ),
                )
                connection.commit()
            except sqlite3.IntegrityError as exc:
                connection.rollback()
                raise TransactionError(f"branch fork failed: {exc}") from exc
        return self.get_branch(child_branch_id)

    def get_branch(self, branch_id: TypedId) -> BranchState:
        require_kind(branch_id, IdKind.BRANCH, "branch_id")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT world_id, branch_id, parent_branch_id, fork_artifact_id, "
                "head_artifact_id, generation, status, authority_revision "
                "FROM branches WHERE branch_id = ?",
                (str(branch_id),),
            ).fetchone()
        if row is None:
            raise TransactionError(f"branch does not exist: {branch_id}")
        return BranchState(
            world_id=TypedId.parse(row["world_id"], IdKind.WORLD),
            branch_id=TypedId.parse(row["branch_id"], IdKind.BRANCH),
            parent_branch_id=_typed(row["parent_branch_id"], IdKind.BRANCH),
            fork_artifact_id=_typed(row["fork_artifact_id"], IdKind.ARTIFACT),
            head_artifact_id=_typed(row["head_artifact_id"], IdKind.ARTIFACT),
            generation=int(row["generation"]),
            status=row["status"],
            authority_revision=int(row["authority_revision"]),
        )

    def get_artifact_parent_id(self, artifact_id: TypedId) -> TypedId | None:
        require_kind(artifact_id, IdKind.ARTIFACT, "artifact_id")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT parent_artifact_id FROM artifacts WHERE artifact_id = ?",
                (str(artifact_id),),
            ).fetchone()
        if row is None:
            raise TransactionError(f"artifact does not exist: {artifact_id}")
        return _typed(row["parent_artifact_id"], IdKind.ARTIFACT)

    def get_artifact(self, artifact_id: TypedId) -> AcceptedStoryArtifact:
        require_kind(artifact_id, IdKind.ARTIFACT, "artifact_id")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM artifacts WHERE artifact_id = ?",
                (str(artifact_id),),
            ).fetchone()
        if row is None:
            raise TransactionError(f"artifact does not exist: {artifact_id}")
        artifact = from_mapping(
            AcceptedStoryArtifact,
            {
                "schema_version": AcceptedStoryArtifact.SCHEMA_VERSION,
                "artifact_id": row["artifact_id"],
                "branch_id": row["branch_id"],
                "generation_id": row["generation_id"],
                "parent_artifact_id": row["parent_artifact_id"],
                "source_id": row["source_id"],
                "decision_id": row["decision_id"],
                "accepted_prose": row["accepted_prose"],
                "prose_sha256": row["prose_sha256"],
                "responding_npc_ids": json.loads(row["responding_npc_ids_json"]),
                "realized_beat_ids": json.loads(row["realized_beat_ids_json"]),
                "validation_receipt_id": row["validation_receipt_id"],
                "transaction_id": row["transaction_id"],
                "status": row["status"],
            },
        )
        if domain_sha256("cera.accepted_story_artifact.v1", artifact) != row["artifact_sha256"]:
            raise TransactionError("stored accepted artifact hash is inconsistent")
        return artifact

    def get_commit_for_artifact(self, artifact_id: TypedId) -> StoredCommit:
        require_kind(artifact_id, IdKind.ARTIFACT, "artifact_id")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT j.receipt_id FROM artifacts a "
                "JOIN transaction_journal j ON j.transaction_id = a.transaction_id "
                "WHERE a.artifact_id = ? AND j.status = 'committed'",
                (str(artifact_id),),
            ).fetchone()
            if row is None:
                raise TransactionError(
                    f"committed artifact does not exist: {artifact_id}"
                )
            receipt = self._receipt_in_connection(connection, row["receipt_id"])
        return StoredCommit(receipt=receipt, exact_replay=True)

    def find_committed_turn(
        self,
        *,
        request_id: TypedId,
        branch_id: TypedId,
        idempotency_key: str,
        source_sha256: str,
    ) -> StoredCommit | None:
        require_kind(request_id, IdKind.REQUEST, "request_id")
        require_kind(branch_id, IdKind.BRANCH, "branch_id")
        if not idempotency_key.strip() or not source_sha256.strip():
            raise ValueError("committed-turn lookup requires key and source hash")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT j.receipt_id, j.branch_id, s.request_id, s.payload_json "
                "FROM transaction_journal j "
                "JOIN sources s ON s.transaction_id = j.transaction_id "
                "WHERE j.idempotency_key = ? AND j.status = 'committed'",
                (idempotency_key,),
            ).fetchone()
            if row is None:
                return None
            if (
                row["branch_id"] != str(branch_id)
                or row["request_id"] != str(request_id)
                or json.loads(row["payload_json"]).get("source_sha256")
                != source_sha256
            ):
                raise TransactionError(
                    "idempotency key is already committed to another turn identity"
                )
            receipt = self._receipt_in_connection(connection, row["receipt_id"])
        return StoredCommit(receipt=receipt, exact_replay=True)

    def prepare_commit(self, bundle: TurnCommitBundle) -> JournalEntry:
        with self._connect() as connection:
            self._begin(connection)
            existing = connection.execute(
                "SELECT * FROM transaction_journal WHERE idempotency_key = ? "
                "OR transaction_id = ?",
                (bundle.idempotency_key, str(bundle.transaction_id)),
            ).fetchone()
            if existing is not None:
                entry = self._journal_from_row(existing)
                if (
                    existing["idempotency_key"] != bundle.idempotency_key
                    or existing["transaction_id"] != str(bundle.transaction_id)
                    or existing["bundle_sha256"] != bundle.bundle_sha256
                ):
                    connection.rollback()
                    raise TransactionError("idempotency key or transaction ID was reused differently")
                connection.commit()
                return entry
            self._assert_branch_expectation(connection, bundle)
            connection.execute(
                "INSERT INTO transaction_journal("
                "transaction_id, idempotency_key, branch_id, expected_generation, "
                "expected_head_artifact_id, bundle_sha256, status, receipt_id, "
                "rollback_reason, prepared_at, completed_at"
                ") VALUES (?, ?, ?, ?, ?, ?, 'prepared', NULL, NULL, ?, NULL)",
                (
                    str(bundle.transaction_id),
                    bundle.idempotency_key,
                    str(bundle.branch_id),
                    bundle.expected_generation,
                    str(bundle.expected_head_artifact_id)
                    if bundle.expected_head_artifact_id is not None
                    else None,
                    bundle.bundle_sha256,
                    _utc_now(),
                ),
            )
            connection.commit()
        return self.get_journal(bundle.transaction_id)

    def finalize_commit(self, bundle: TurnCommitBundle) -> StoredCommit:
        with self._connect() as connection:
            self._begin(connection)
            journal = connection.execute(
                "SELECT * FROM transaction_journal WHERE transaction_id = ?",
                (str(bundle.transaction_id),),
            ).fetchone()
            if journal is None:
                connection.rollback()
                raise TransactionError("transaction must be prepared before finalization")
            if (
                journal["idempotency_key"] != bundle.idempotency_key
                or journal["bundle_sha256"] != bundle.bundle_sha256
            ):
                connection.rollback()
                raise TransactionError("prepared transaction does not match commit bundle")
            if journal["status"] == JournalStatus.COMMITTED.value:
                receipt = self._receipt_in_connection(connection, journal["receipt_id"])
                connection.commit()
                return StoredCommit(receipt=receipt, exact_replay=True)
            if journal["status"] == JournalStatus.ROLLED_BACK.value:
                connection.rollback()
                raise TransactionError("rolled-back transaction cannot be retried automatically")
            try:
                self._assert_branch_expectation(connection, bundle)
                self._assert_regeneration_parent(connection, bundle)
            except StateConflictError as exc:
                connection.execute(
                    "UPDATE transaction_journal SET status = 'rolled_back', "
                    "rollback_reason = ?, completed_at = ? WHERE transaction_id = ?",
                    (str(exc), _utc_now(), str(bundle.transaction_id)),
                )
                connection.commit()
                raise
            try:
                receipt = self._apply_bundle(connection, bundle)
                self._after_artifact_insert(connection, bundle)
                self._complete_external_resumption(connection, bundle)
                connection.execute(
                    "UPDATE branches SET head_artifact_id = ?, generation = generation + 1 "
                    "WHERE branch_id = ?",
                    (str(bundle.artifact.artifact_id), str(bundle.branch_id)),
                )
                connection.execute(
                    "INSERT INTO commit_receipts("
                    "commit_id, transaction_id, branch_id, receipt_json, "
                    "transaction_sha256, created_at"
                    ") VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        str(receipt.commit_id),
                        str(receipt.transaction_id),
                        str(receipt.branch_id),
                        canonical_json(receipt),
                        receipt.transaction_sha256,
                        _utc_now(),
                    ),
                )
                connection.execute(
                    "UPDATE transaction_journal SET status = 'committed', receipt_id = ?, "
                    "completed_at = ? WHERE transaction_id = ?",
                    (str(receipt.commit_id), _utc_now(), str(bundle.transaction_id)),
                )
                connection.commit()
                return StoredCommit(receipt=receipt, exact_replay=False)
            except Exception as exc:
                connection.rollback()
                if isinstance(exc, (TransactionError, StateConflictError)):
                    raise
                raise TransactionError(f"atomic commit failed and was rolled back: {exc}") from exc

    def commit_turn(self, bundle: TurnCommitBundle) -> StoredCommit:
        prepared = self.prepare_commit(bundle)
        if prepared.status is JournalStatus.ROLLED_BACK:
            raise TransactionError("rolled-back transaction requires creator review and a new key")
        return self.finalize_commit(bundle)

    def _assert_branch_expectation(
        self, connection: sqlite3.Connection, bundle: TurnCommitBundle
    ) -> None:
        row = connection.execute(
            "SELECT head_artifact_id, generation, status FROM branches WHERE branch_id = ?",
            (str(bundle.branch_id),),
        ).fetchone()
        if row is None:
            raise StateConflictError(f"branch does not exist: {bundle.branch_id}")
        if row["status"] != "active":
            raise StateConflictError("branch is not active")
        expected_head = (
            str(bundle.expected_head_artifact_id)
            if bundle.expected_head_artifact_id is not None
            else None
        )
        if int(row["generation"]) != bundle.expected_generation:
            raise StateConflictError("branch generation changed")
        if row["head_artifact_id"] != expected_head:
            raise StateConflictError("branch head changed")

    @staticmethod
    def _assert_regeneration_parent(
        connection: sqlite3.Connection, bundle: TurnCommitBundle
    ) -> None:
        if bundle.mode is not CommitMode.REGENERATE:
            return
        replaced = connection.execute(
            "SELECT parent_artifact_id FROM artifacts WHERE artifact_id = ?",
            (str(bundle.replaces_artifact_id),),
        ).fetchone()
        if replaced is None:
            raise StateConflictError("regeneration target does not exist")
        expected_parent = replaced["parent_artifact_id"]
        actual_parent = (
            str(bundle.artifact.parent_artifact_id)
            if bundle.artifact.parent_artifact_id is not None
            else None
        )
        if actual_parent != expected_parent:
            raise StateConflictError("regenerated artifact must be a sibling of its target")

    def _apply_bundle(
        self, connection: sqlite3.Connection, bundle: TurnCommitBundle
    ) -> CommitReceipt:
        now = _utc_now()
        connection.execute(
            "INSERT INTO sources("
            "source_id, request_id, branch_id, payload_json, source_sha256, "
            "transaction_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                str(bundle.source.source_id),
                str(bundle.source.request_id),
                str(bundle.source.branch_id),
                bundle.source.payload_json,
                bundle.source.source_sha256,
                str(bundle.transaction_id),
                now,
            ),
        )
        connection.execute(
            "INSERT INTO generations("
            "generation_id, branch_id, source_id, expected_generation, "
            "resulting_generation, mode, parent_artifact_id, replaces_artifact_id, "
            "transaction_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                str(bundle.artifact.generation_id),
                str(bundle.branch_id),
                str(bundle.source.source_id),
                bundle.expected_generation,
                bundle.expected_generation + 1,
                bundle.mode.value,
                str(bundle.artifact.parent_artifact_id)
                if bundle.artifact.parent_artifact_id is not None
                else None,
                str(bundle.replaces_artifact_id)
                if bundle.replaces_artifact_id is not None
                else None,
                str(bundle.transaction_id),
                now,
            ),
        )
        artifact = bundle.artifact
        connection.execute(
            "INSERT INTO artifacts("
            "artifact_id, branch_id, generation_id, parent_artifact_id, source_id, "
            "decision_id, accepted_prose, prose_sha256, artifact_sha256, "
            "responding_npc_ids_json, realized_beat_ids_json, validation_receipt_id, "
            "transaction_id, status, created_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                str(artifact.artifact_id),
                str(artifact.branch_id),
                str(artifact.generation_id),
                str(artifact.parent_artifact_id)
                if artifact.parent_artifact_id is not None
                else None,
                str(artifact.source_id),
                str(artifact.decision_id),
                artifact.accepted_prose,
                artifact.prose_sha256,
                bundle.artifact_sha256,
                canonical_json(artifact.responding_npc_ids),
                canonical_json(artifact.realized_beat_ids),
                str(artifact.validation_receipt_id),
                str(artifact.transaction_id),
                artifact.status,
                now,
            ),
        )
        for record in bundle.authority_records:
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
            if payload.get("schema_version") == "cera.evidence_document.v1":
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
                "INSERT INTO turn_receipt_records("
                "receipt_id, transaction_id, category, schema_version, payload_json, "
                "payload_sha256, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
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
        for work in bundle.post_publication_work_requests:
            connection.execute(
                "INSERT INTO post_publication_work("
                "work_id, artifact_id, branch_id, work_kind, request_json, "
                "request_sha256, depends_on_work_id, status, attempt_count, "
                "result_json, result_sha256, error_json, error_sha256, "
                "created_at, updated_at, completed_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', 0, NULL, NULL, NULL, NULL, ?, ?, NULL)",
                (
                    str(work.work_id),
                    str(work.artifact_id),
                    str(work.branch_id),
                    work.kind.value,
                    work.request_json,
                    work.request_sha256,
                    str(work.depends_on_work_id)
                    if work.depends_on_work_id is not None
                    else None,
                    now,
                    now,
                ),
            )
        superseded = tuple(
            record_id
            for record in bundle.authority_records
            for record_id in record.supersedes
        )
        return CommitReceipt(
            schema_version=CommitReceipt.SCHEMA_VERSION,
            commit_id=deterministic_id(
                IdKind.COMMIT, "cera.transaction", str(bundle.transaction_id)
            ),
            transaction_id=bundle.transaction_id,
            branch_id=bundle.branch_id,
            source_id=bundle.source.source_id,
            source_sha256=bundle.source.source_sha256,
            expected_previous_artifact_id=bundle.expected_head_artifact_id,
            new_artifact_id=bundle.artifact.artifact_id,
            new_artifact_sha256=bundle.artifact_sha256,
            generation_before=bundle.expected_generation,
            generation_after=bundle.expected_generation + 1,
            inserted_record_ids=tuple(
                record.record_id for record in bundle.authority_records
            ),
            superseded_record_ids=superseded,
            validation_receipt_ids=bundle.validation_receipt_ids,
            lookup_receipt_ids=bundle.lookup_receipt_ids,
            provider_receipt_ids=bundle.provider_receipt_ids,
            external_receipt_ids=(bundle.external_receipt_id,)
            if bundle.external_receipt_id is not None
            else (),
            transaction_sha256=bundle.bundle_sha256,
            outcome="committed",
        )

    def _after_artifact_insert(
        self, connection: sqlite3.Connection, bundle: TurnCommitBundle
    ) -> None:
        """Test seam for simulating a process failure inside finalization."""

    @staticmethod
    def _complete_external_resumption(
        connection: sqlite3.Connection, bundle: TurnCommitBundle
    ) -> None:
        if bundle.external_receipt_id is None:
            return
        assert bundle.blocked_checkpoint_id is not None
        row = connection.execute(
            "SELECT status, checkpoint_id, transaction_id FROM external_receipt_claims "
            "WHERE receipt_id = ?",
            (str(bundle.external_receipt_id),),
        ).fetchone()
        if row is None:
            raise TransactionError("external receipt claim does not exist")
        if row["checkpoint_id"] != str(bundle.blocked_checkpoint_id):
            raise TransactionError("external receipt claim belongs to another checkpoint")
        if row["status"] not in ("aftermath_validated", "committed"):
            raise TransactionError("external receipt is not ready for atomic resumption")
        if row["transaction_id"] not in (None, str(bundle.transaction_id)):
            raise TransactionError("external receipt is bound to another transaction")
        if row["status"] == "committed":
            return
        now = _utc_now()
        connection.execute(
            "UPDATE external_receipt_claims SET status = 'committed', transaction_id = ?, "
            "updated_at = ? WHERE receipt_id = ?",
            (str(bundle.transaction_id), now, str(bundle.external_receipt_id)),
        )
        connection.execute(
            "UPDATE blocked_turns SET status = 'accepted', accepted_receipt_id = ?, "
            "transaction_id = ?, updated_at = ? WHERE checkpoint_id = ?",
            (
                str(bundle.external_receipt_id),
                str(bundle.transaction_id),
                now,
                str(bundle.blocked_checkpoint_id),
            ),
        )
        connection.execute(
            "DELETE FROM temporary_aftermath_projections WHERE checkpoint_id = ?",
            (str(bundle.blocked_checkpoint_id),),
        )

    def get_journal(self, transaction_id: TypedId) -> JournalEntry:
        require_kind(transaction_id, IdKind.TRANSACTION, "transaction_id")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM transaction_journal WHERE transaction_id = ?",
                (str(transaction_id),),
            ).fetchone()
        if row is None:
            raise TransactionError(f"transaction does not exist: {transaction_id}")
        return self._journal_from_row(row)

    @staticmethod
    def _journal_from_row(row: sqlite3.Row) -> JournalEntry:
        return JournalEntry(
            transaction_id=TypedId.parse(row["transaction_id"], IdKind.TRANSACTION),
            idempotency_key=row["idempotency_key"],
            branch_id=TypedId.parse(row["branch_id"], IdKind.BRANCH),
            bundle_sha256=row["bundle_sha256"],
            status=JournalStatus(row["status"]),
            receipt_id=_typed(row["receipt_id"], IdKind.COMMIT),
            rollback_reason=row["rollback_reason"],
        )

    def get_receipt(self, commit_id: TypedId) -> CommitReceipt:
        require_kind(commit_id, IdKind.COMMIT, "commit_id")
        with self._connect() as connection:
            return self._receipt_in_connection(connection, str(commit_id))

    def get_turn_receipt_record(self, receipt_id: TypedId) -> ReceiptRecord:
        if receipt_id.kind not in {
            IdKind.VALIDATION,
            IdKind.REALIZATION_VERIFICATION,
            IdKind.LOOKUP_RECEIPT,
            IdKind.PROVIDER_RECEIPT,
        }:
            raise ValueError("unsupported turn receipt ID kind")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT receipt_id, category, schema_version, payload_json, payload_sha256 "
                "FROM turn_receipt_records WHERE receipt_id = ?",
                (str(receipt_id),),
            ).fetchone()
        if row is None:
            raise TransactionError(f"turn receipt record does not exist: {receipt_id}")
        return ReceiptRecord(
            receipt_id=TypedId.parse(row["receipt_id"]),
            category=ReceiptCategory(row["category"]),
            schema_version=row["schema_version"],
            payload_json=row["payload_json"],
            payload_sha256=row["payload_sha256"],
        )

    @staticmethod
    def _receipt_in_connection(
        connection: sqlite3.Connection, commit_id: str | None
    ) -> CommitReceipt:
        if commit_id is None:
            raise TransactionError("committed journal has no receipt ID")
        row = connection.execute(
            "SELECT receipt_json FROM commit_receipts WHERE commit_id = ?",
            (commit_id,),
        ).fetchone()
        if row is None:
            raise TransactionError(f"commit receipt does not exist: {commit_id}")
        return from_mapping(CommitReceipt, json.loads(row["receipt_json"]))

    def recover_prepared(self, *, reason: str = "restart recovery") -> RecoveryReport:
        if not reason.strip():
            raise ValueError("recovery reason must be non-empty")
        with self._connect() as connection:
            self._begin(connection)
            rows = connection.execute(
                "SELECT transaction_id FROM transaction_journal "
                "WHERE status = 'prepared' ORDER BY prepared_at, transaction_id"
            ).fetchall()
            transaction_ids = tuple(
                TypedId.parse(row["transaction_id"], IdKind.TRANSACTION) for row in rows
            )
            connection.execute(
                "UPDATE transaction_journal SET status = 'rolled_back', "
                "rollback_reason = ?, completed_at = ? WHERE status = 'prepared'",
                (reason, _utc_now()),
            )
            connection.commit()
        return RecoveryReport(rolled_back_transaction_ids=transaction_ids)

    def visible_artifact_ids(self, branch_id: TypedId) -> tuple[TypedId, ...]:
        state = self.get_branch(branch_id)
        if state.head_artifact_id is None:
            return ()
        with self._connect() as connection:
            rows = connection.execute(
                "WITH RECURSIVE lineage(artifact_id, parent_artifact_id, depth) AS ("
                " SELECT artifact_id, parent_artifact_id, 0 FROM artifacts WHERE artifact_id = ?"
                " UNION ALL"
                " SELECT a.artifact_id, a.parent_artifact_id, lineage.depth + 1"
                " FROM artifacts a JOIN lineage ON a.artifact_id = lineage.parent_artifact_id"
                ") SELECT artifact_id FROM lineage ORDER BY depth DESC",
                (str(state.head_artifact_id),),
            ).fetchall()
        return tuple(TypedId.parse(row["artifact_id"], IdKind.ARTIFACT) for row in rows)

    def visible_record_ids(self, branch_id: TypedId) -> tuple[TypedId, ...]:
        state = self.get_branch(branch_id)
        return tuple(
            record.record_id
            for record in self.authority_records_at_head(branch_id, state.head_artifact_id)
        )

    def table_count(self, table_name: str) -> int:
        allowed = {
            "worlds",
            "branches",
            "sources",
            "generations",
            "artifacts",
            "authority_records",
            "transaction_journal",
            "commit_receipts",
            "turn_receipt_records",
            "consolidation_receipt_records",
            "post_publication_work",
            "post_publication_attempts",
            "blocked_turns",
            "temporary_aftermath_projections",
            "external_receipt_claims",
            "consolidation_journal",
            "consolidation_receipts",
            "derived_views",
            "reasoner_sessions",
            "reasoner_session_checkpoints",
            "creator_constraints",
            "reasoner_accepted_turn_receipts",
            "reasoner_rejected_candidate_receipts",
            "reasoner_session_usage_receipts",
        }
        if table_name not in allowed:
            raise ValueError("unsupported table name")
        with self._connect() as connection:
            return int(connection.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0])

    def integrity_check(self) -> tuple[str, ...]:
        with self._connect() as connection:
            return tuple(str(row[0]) for row in connection.execute("PRAGMA integrity_check"))

    def foreign_key_check(self) -> tuple[tuple[object, ...], ...]:
        with self._connect() as connection:
            return tuple(tuple(row) for row in connection.execute("PRAGMA foreign_key_check"))

    def backup_to(self, destination: str | Path) -> Path:
        destination_path = Path(destination).resolve()
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as source:
            source.execute("PRAGMA wal_checkpoint(FULL)")
            target = sqlite3.connect(destination_path)
            try:
                source.backup(target)
            finally:
                target.close()
        return destination_path
