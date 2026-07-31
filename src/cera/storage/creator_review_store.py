"""SQLite persistence for branch-bound provisional creator review state."""

from __future__ import annotations

import json

from cera.creator_review import (
    CreatorAcceptTimingReceipt,
    CreatorCorrectionDiagnostic,
    CreatorReviewRecord,
    CreatorReviewState,
)
from cera.errors import StateConflictError, TransactionError
from cera.ids import IdKind, TypedId, require_kind
from cera.schema import from_mapping
from cera.serialization import canonical_json


class CreatorReviewStoreMixin:
    def put_creator_accept_timing(
        self,
        receipt: CreatorAcceptTimingReceipt,
    ) -> None:
        payload = canonical_json(receipt)
        try:
            with self._connect() as connection:
                self._begin(connection)
                existing = connection.execute(
                    "SELECT receipt_sha256 FROM creator_accept_timing_receipts "
                    "WHERE review_id = ?",
                    (str(receipt.review_id),),
                ).fetchone()
                if existing is not None:
                    if existing["receipt_sha256"] != receipt.receipt_sha256:
                        raise StateConflictError(
                            "creator acceptance already has different timing evidence"
                        )
                    connection.commit()
                    return
                connection.execute(
                    "INSERT INTO creator_accept_timing_receipts("
                    "receipt_id, review_id, package_id, branch_id, "
                    "accept_to_commit_microseconds, receipt_json, receipt_sha256, "
                    "started_at, committed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        str(receipt.receipt_id),
                        str(receipt.review_id),
                        str(receipt.package_id),
                        str(receipt.branch_id),
                        receipt.accept_to_commit_microseconds,
                        payload,
                        receipt.receipt_sha256,
                        receipt.started_at,
                        receipt.committed_at,
                    ),
                )
                connection.commit()
        except StateConflictError:
            raise
        except Exception as exc:
            raise TransactionError("creator accept timing write failed") from exc

    def get_creator_accept_timing(
        self,
        review_id: TypedId,
    ) -> CreatorAcceptTimingReceipt | None:
        require_kind(review_id, IdKind.REVIEW_PACKET, "review_id")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT receipt_json, receipt_sha256 FROM "
                "creator_accept_timing_receipts WHERE review_id = ?",
                (str(review_id),),
            ).fetchone()
        if row is None:
            return None
        receipt = from_mapping(
            CreatorAcceptTimingReceipt,
            json.loads(row["receipt_json"]),
        )
        if receipt.receipt_sha256 != row["receipt_sha256"]:
            raise TransactionError("creator accept timing receipt hash mismatch")
        return receipt

    def put_creator_correction_diagnostic(
        self,
        diagnostic: CreatorCorrectionDiagnostic,
    ) -> None:
        payload = canonical_json(diagnostic)
        try:
            with self._connect() as connection:
                self._begin(connection)
                existing = connection.execute(
                    "SELECT record_sha256 FROM creator_correction_diagnostics "
                    "WHERE diagnostic_id = ?",
                    (str(diagnostic.diagnostic_id),),
                ).fetchone()
                if existing is not None:
                    if existing["record_sha256"] != diagnostic.diagnostic_sha256:
                        raise StateConflictError(
                            "creator diagnostic identity was reused differently"
                        )
                    connection.commit()
                    return
                connection.execute(
                    "INSERT INTO creator_correction_diagnostics("
                    "diagnostic_id, review_id, branch_id, issue_owner, "
                    "record_json, record_sha256, created_at"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        str(diagnostic.diagnostic_id),
                        str(diagnostic.review_id),
                        str(diagnostic.branch_id),
                        diagnostic.likely_owner.value,
                        payload,
                        diagnostic.diagnostic_sha256,
                        diagnostic.created_at,
                    ),
                )
                connection.commit()
        except StateConflictError:
            raise
        except Exception as exc:
            raise TransactionError("creator correction diagnostic write failed") from exc

    def creator_correction_diagnostics(
        self,
        *,
        branch_id: TypedId | None = None,
    ) -> tuple[CreatorCorrectionDiagnostic, ...]:
        if branch_id is not None:
            require_kind(branch_id, IdKind.BRANCH, "branch_id")
        with self._connect() as connection:
            if branch_id is None:
                rows = connection.execute(
                    "SELECT record_json, record_sha256 FROM "
                    "creator_correction_diagnostics ORDER BY created_at, diagnostic_id"
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT record_json, record_sha256 FROM "
                    "creator_correction_diagnostics WHERE branch_id = ? "
                    "ORDER BY created_at, diagnostic_id",
                    (str(branch_id),),
                ).fetchall()
        diagnostics = tuple(
            from_mapping(CreatorCorrectionDiagnostic, json.loads(row["record_json"]))
            for row in rows
        )
        if any(
            value.diagnostic_sha256 != row["record_sha256"]
            for value, row in zip(diagnostics, rows, strict=True)
        ):
            raise TransactionError("creator correction diagnostic hash mismatch")
        return diagnostics

    def create_creator_review(self, record: CreatorReviewRecord) -> None:
        payload = canonical_json(record)
        try:
            with self._connect() as connection:
                self._begin(connection)
                connection.execute(
                    "INSERT INTO creator_review_records("
                    "review_id, world_id, branch_id, request_id, generation_id, "
                    "expected_generation, expected_head_artifact_id, candidate_sha256, "
                    "state, record_json, record_sha256, created_at, updated_at, resolved_at"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        str(record.review_id),
                        str(record.world_id),
                        str(record.branch_id),
                        str(record.request_id),
                        str(record.generation_id),
                        record.expected_generation,
                        (
                            str(record.expected_head_artifact_id)
                            if record.expected_head_artifact_id is not None
                            else None
                        ),
                        record.candidate_sha256,
                        record.state.value,
                        payload,
                        record.record_sha256,
                        record.created_at,
                        record.updated_at,
                        record.resolved_at,
                    ),
                )
                connection.commit()
        except Exception as exc:
            if "idx_creator_review_one_unresolved_branch" in str(exc) or (
                "UNIQUE constraint failed: creator_review_records.branch_id" in str(exc)
            ):
                raise StateConflictError(
                    "branch already has an unresolved creator review"
                ) from exc
            if isinstance(exc, (StateConflictError, TransactionError)):
                raise
            raise TransactionError("creator review creation failed") from exc

    def get_creator_review(self, review_id: TypedId) -> CreatorReviewRecord:
        require_kind(review_id, IdKind.REVIEW_PACKET, "review_id")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT record_json, record_sha256 FROM creator_review_records "
                "WHERE review_id = ?",
                (str(review_id),),
            ).fetchone()
        if row is None:
            raise TransactionError(f"unknown creator review: {review_id}")
        record = from_mapping(CreatorReviewRecord, json.loads(row["record_json"]))
        if record.record_sha256 != row["record_sha256"]:
            raise TransactionError("creator review payload hash mismatch")
        return record

    def unresolved_creator_review_for_branch(
        self, branch_id: TypedId
    ) -> CreatorReviewRecord | None:
        require_kind(branch_id, IdKind.BRANCH, "branch_id")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT record_json, record_sha256 FROM creator_review_records "
                "WHERE branch_id = ? AND state IN ("
                "'provisional_visible','verifying_and_preparing','review_ready',"
                "'awaiting_feedback','committing','error') "
                "ORDER BY created_at DESC LIMIT 1",
                (str(branch_id),),
            ).fetchone()
        if row is None:
            return None
        record = from_mapping(CreatorReviewRecord, json.loads(row["record_json"]))
        if record.record_sha256 != row["record_sha256"]:
            raise TransactionError("creator review payload hash mismatch")
        return record

    def replace_creator_review(
        self,
        record: CreatorReviewRecord,
        *,
        expected_states: tuple[CreatorReviewState, ...],
    ) -> None:
        if not expected_states:
            raise ValueError("creator review replacement requires expected states")
        payload = canonical_json(record)
        placeholders = ",".join("?" for _ in expected_states)
        params = (
            record.state.value,
            payload,
            record.record_sha256,
            record.updated_at,
            record.resolved_at,
            str(record.review_id),
            *(value.value for value in expected_states),
        )
        try:
            with self._connect() as connection:
                self._begin(connection)
                cursor = connection.execute(
                    "UPDATE creator_review_records SET state = ?, record_json = ?, "
                    "record_sha256 = ?, updated_at = ?, resolved_at = ? "
                    f"WHERE review_id = ? AND state IN ({placeholders})",
                    params,
                )
                if cursor.rowcount != 1:
                    connection.rollback()
                    raise StateConflictError(
                        "creator review state changed before the requested transition"
                    )
                connection.commit()
        except StateConflictError:
            raise
        except Exception as exc:
            raise TransactionError("creator review transition failed") from exc

    def creator_review_counts(self, branch_id: TypedId) -> dict[str, int]:
        require_kind(branch_id, IdKind.BRANCH, "branch_id")
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT state, COUNT(*) AS total FROM creator_review_records "
                "WHERE branch_id = ? GROUP BY state",
                (str(branch_id),),
            ).fetchall()
        return {str(row["state"]): int(row["total"]) for row in rows}
