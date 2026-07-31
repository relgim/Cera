"""SQLite custody for branch-bound Reasoner session state."""

from __future__ import annotations

from dataclasses import replace
import json

from cera.errors import StateConflictError, TransactionError
from cera.ids import IdKind, TypedId, require_kind
from cera.reasoner_session.models import (
    AcceptedTurnReceipt,
    CheckpointStatus,
    ConstraintStatus,
    CreatorConstraintRecord,
    ProviderThreadCustodyEvent,
    ProviderThreadCustodyEventKind,
    ReasonerSessionCheckpoint,
    ReasonerSessionLedger,
    RejectedCandidateReceipt,
    SessionStatus,
    SessionUsageReceiptV2,
)
from cera.schema import from_mapping
from cera.serialization import canonical_json, text_sha256


class ReasonerSessionStoreMixin:
    def create_reasoner_session(
        self,
        ledger: ReasonerSessionLedger,
        root_checkpoint: ReasonerSessionCheckpoint,
        custody_event: ProviderThreadCustodyEvent | None = None,
    ) -> None:
        self._assert_custody_binding(
            custody_event,
            root_checkpoint,
            {ProviderThreadCustodyEventKind.ALLOCATED},
        )
        if (
            ledger.status is not SessionStatus.ACTIVE
            or root_checkpoint.status is not CheckpointStatus.ACCEPTED
            or root_checkpoint.parent_checkpoint_id is not None
            or ledger.session_id != root_checkpoint.session_id
            or ledger.compatibility.branch_id != root_checkpoint.branch_id
            or ledger.accepted_checkpoint_id != root_checkpoint.checkpoint_id
            or ledger.provider_root_handle.provider_session_id
            != root_checkpoint.provider_handle.provider_session_id
        ):
            raise StateConflictError("reasoner session root bindings are invalid")
        ledger_json = canonical_json(ledger)
        checkpoint_json = canonical_json(root_checkpoint)
        try:
            with self._connect() as connection:
                self._begin(connection)
                connection.execute(
                    "INSERT INTO reasoner_sessions("
                    "session_id, world_id, branch_id, role, compatibility_sha256, "
                    "status, accepted_checkpoint_id, provider_session_id, "
                    "provider_root_thread_id, accumulated_turns, rotated_from_session_id, "
                    "record_json, record_sha256, created_at, updated_at"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        str(ledger.session_id),
                        str(ledger.compatibility.world_id),
                        str(ledger.compatibility.branch_id),
                        ledger.compatibility.role.value,
                        ledger.compatibility.compatibility_sha256,
                        ledger.status.value,
                        str(ledger.accepted_checkpoint_id),
                        ledger.provider_root_handle.provider_session_id,
                        ledger.provider_root_handle.provider_thread_id,
                        ledger.accumulated_turns,
                        (
                            str(ledger.rotated_from_session_id)
                            if ledger.rotated_from_session_id is not None
                            else None
                        ),
                        ledger_json,
                        ledger.ledger_sha256,
                        ledger.created_at,
                        ledger.updated_at,
                    ),
                )
                self._insert_checkpoint(connection, root_checkpoint, checkpoint_json)
                if custody_event is not None:
                    self._insert_custody_event(connection, custody_event)
                connection.commit()
        except StateConflictError:
            raise
        except Exception as exc:
            if "idx_reasoner_session_one_active_compatibility" in str(exc) or (
                "UNIQUE constraint failed" in str(exc)
                and "reasoner_sessions" in str(exc)
            ):
                raise StateConflictError(
                    "branch already has an active compatible reasoner session"
                ) from exc
            raise TransactionError("reasoner session creation failed") from exc

    def get_reasoner_session(self, session_id: TypedId) -> ReasonerSessionLedger:
        require_kind(session_id, IdKind.SESSION, "session_id")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT record_json, record_sha256 FROM reasoner_sessions "
                "WHERE session_id = ?",
                (str(session_id),),
            ).fetchone()
        if row is None:
            raise TransactionError(f"unknown reasoner session: {session_id}")
        ledger = from_mapping(ReasonerSessionLedger, json.loads(row["record_json"]))
        if ledger.ledger_sha256 != row["record_sha256"]:
            raise TransactionError("reasoner session ledger hash mismatch")
        return ledger

    def active_reasoner_session(
        self,
        branch_id: TypedId,
        compatibility_sha256: str,
        *,
        role: str = "scene_reasoner",
    ) -> ReasonerSessionLedger | None:
        require_kind(branch_id, IdKind.BRANCH, "branch_id")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT record_json, record_sha256 FROM reasoner_sessions "
                "WHERE branch_id = ? AND role = ? AND compatibility_sha256 = ? "
                "AND status = 'active'",
                (str(branch_id), role, compatibility_sha256),
            ).fetchone()
        if row is None:
            return None
        ledger = from_mapping(ReasonerSessionLedger, json.loads(row["record_json"]))
        if ledger.ledger_sha256 != row["record_sha256"]:
            raise TransactionError("active reasoner session hash mismatch")
        return ledger

    def active_reasoner_session_for_branch_role(
        self,
        branch_id: TypedId,
        *,
        role: str = "scene_reasoner",
    ) -> ReasonerSessionLedger | None:
        require_kind(branch_id, IdKind.BRANCH, "branch_id")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT record_json, record_sha256 FROM reasoner_sessions "
                "WHERE branch_id = ? AND role = ? AND status = 'active'",
                (str(branch_id), role),
            ).fetchone()
        if row is None:
            return None
        ledger = from_mapping(ReasonerSessionLedger, json.loads(row["record_json"]))
        if ledger.ledger_sha256 != row["record_sha256"]:
            raise TransactionError("active branch reasoner session hash mismatch")
        return ledger

    def get_reasoner_checkpoint(
        self, checkpoint_id: TypedId
    ) -> ReasonerSessionCheckpoint:
        require_kind(checkpoint_id, IdKind.CHECKPOINT, "checkpoint_id")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT record_json, record_sha256 FROM reasoner_session_checkpoints "
                "WHERE checkpoint_id = ?",
                (str(checkpoint_id),),
            ).fetchone()
        if row is None:
            raise TransactionError(f"unknown reasoner checkpoint: {checkpoint_id}")
        value = from_mapping(ReasonerSessionCheckpoint, json.loads(row["record_json"]))
        if value.checkpoint_sha256 != row["record_sha256"]:
            raise TransactionError("reasoner checkpoint hash mismatch")
        return value

    def reasoner_checkpoints(
        self, session_id: TypedId
    ) -> tuple[ReasonerSessionCheckpoint, ...]:
        require_kind(session_id, IdKind.SESSION, "session_id")
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT record_json, record_sha256 FROM reasoner_session_checkpoints "
                "WHERE session_id = ? ORDER BY created_at, checkpoint_id",
                (str(session_id),),
            ).fetchall()
        values = tuple(
            from_mapping(ReasonerSessionCheckpoint, json.loads(row["record_json"]))
            for row in rows
        )
        if any(
            value.checkpoint_sha256 != row["record_sha256"]
            for value, row in zip(values, rows, strict=True)
        ):
            raise TransactionError("reasoner checkpoint inventory hash mismatch")
        return values

    def reasoner_checkpoint_for_review(
        self, review_id: TypedId
    ) -> ReasonerSessionCheckpoint | None:
        require_kind(review_id, IdKind.REVIEW_PACKET, "review_id")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT record_json, record_sha256 FROM "
                "reasoner_session_checkpoints WHERE review_id = ?",
                (str(review_id),),
            ).fetchone()
        if row is None:
            return None
        value = from_mapping(ReasonerSessionCheckpoint, json.loads(row["record_json"]))
        if value.checkpoint_sha256 != row["record_sha256"]:
            raise TransactionError("reasoner review checkpoint hash mismatch")
        return value

    def create_candidate_checkpoint(
        self,
        checkpoint: ReasonerSessionCheckpoint,
        *,
        expected_accepted_checkpoint_id: TypedId,
        custody_event: ProviderThreadCustodyEvent | None = None,
    ) -> None:
        self._assert_custody_binding(
            custody_event,
            checkpoint,
            {ProviderThreadCustodyEventKind.ALLOCATED},
        )
        if (
            checkpoint.status is not CheckpointStatus.CANDIDATE
        ):
            raise StateConflictError("candidate checkpoint bindings are invalid")
        payload = canonical_json(checkpoint)
        try:
            with self._connect() as connection:
                self._begin(connection)
                session = connection.execute(
                    "SELECT status, accepted_checkpoint_id, branch_id FROM reasoner_sessions "
                    "WHERE session_id = ?",
                    (str(checkpoint.session_id),),
                ).fetchone()
                if session is None or session["status"] != SessionStatus.ACTIVE.value:
                    raise StateConflictError("reasoner session is not active")
                if session["accepted_checkpoint_id"] != str(expected_accepted_checkpoint_id):
                    raise StateConflictError("accepted reasoner checkpoint changed")
                if session["branch_id"] != str(checkpoint.branch_id):
                    raise StateConflictError("candidate crossed its branch boundary")
                parent = connection.execute(
                    "SELECT status FROM reasoner_session_checkpoints "
                    "WHERE checkpoint_id = ? AND session_id = ?",
                    (str(checkpoint.parent_checkpoint_id), str(checkpoint.session_id)),
                ).fetchone()
                if parent is None or parent["status"] != CheckpointStatus.ACCEPTED.value:
                    raise StateConflictError("candidate parent is not accepted")
                self._insert_checkpoint(connection, checkpoint, payload)
                if custody_event is not None:
                    self._insert_custody_event(connection, custody_event)
                connection.commit()
        except StateConflictError:
            raise
        except Exception as exc:
            if "idx_reasoner_session_one_candidate" in str(exc) or (
                "UNIQUE constraint failed" in str(exc)
                and "reasoner_session_checkpoints.session_id" in str(exc)
            ):
                raise StateConflictError("reasoner session already has a candidate") from exc
            raise TransactionError("candidate checkpoint creation failed") from exc

    def bind_candidate_review(
        self,
        checkpoint: ReasonerSessionCheckpoint,
        *,
        expected_review_id: TypedId | None = None,
    ) -> None:
        if checkpoint.status is not CheckpointStatus.CANDIDATE or checkpoint.review_id is None:
            raise StateConflictError("only a candidate can bind a creator review")
        payload = canonical_json(checkpoint)
        try:
            with self._connect() as connection:
                self._begin(connection)
                row = connection.execute(
                    "SELECT status, review_id FROM reasoner_session_checkpoints "
                    "WHERE checkpoint_id = ?",
                    (str(checkpoint.checkpoint_id),),
                ).fetchone()
                if row is None or row["status"] != CheckpointStatus.CANDIDATE.value:
                    raise StateConflictError("candidate is no longer open")
                expected = str(expected_review_id) if expected_review_id is not None else None
                if row["review_id"] != expected:
                    raise StateConflictError("candidate review binding changed")
                connection.execute(
                    "UPDATE reasoner_session_checkpoints SET review_id = ?, "
                    "record_json = ?, record_sha256 = ?, updated_at = ? "
                    "WHERE checkpoint_id = ?",
                    (
                        str(checkpoint.review_id),
                        payload,
                        checkpoint.checkpoint_sha256,
                        checkpoint.updated_at,
                        str(checkpoint.checkpoint_id),
                    ),
                )
                connection.commit()
        except StateConflictError:
            raise
        except Exception as exc:
            raise TransactionError("candidate review binding failed") from exc

    def accept_candidate_checkpoint(
        self,
        ledger: ReasonerSessionLedger,
        checkpoint: ReasonerSessionCheckpoint,
        receipt: AcceptedTurnReceipt,
        custody_event: ProviderThreadCustodyEvent | None = None,
    ) -> None:
        self._assert_custody_binding(
            custody_event,
            checkpoint,
            {ProviderThreadCustodyEventKind.ACCEPTED},
        )
        if (
            ledger.status is not SessionStatus.ACTIVE
            or checkpoint.status is not CheckpointStatus.ACCEPTED
            or ledger.accepted_checkpoint_id != checkpoint.checkpoint_id
            or receipt.checkpoint_id != checkpoint.checkpoint_id
            or receipt.receipt_id != checkpoint.accepted_receipt_id
            or receipt.session_id != ledger.session_id
        ):
            raise StateConflictError("accepted candidate bindings are invalid")
        ledger_json = canonical_json(ledger)
        checkpoint_json = canonical_json(checkpoint)
        receipt_json = canonical_json(receipt)
        try:
            with self._connect() as connection:
                self._begin(connection)
                current = connection.execute(
                    "SELECT status, accepted_checkpoint_id FROM reasoner_sessions "
                    "WHERE session_id = ?",
                    (str(ledger.session_id),),
                ).fetchone()
                if current is None or current["status"] != SessionStatus.ACTIVE.value:
                    raise StateConflictError("reasoner session is not active")
                active_checkpoint = connection.execute(
                    "SELECT status, accepted_head_artifact_id, generation, authority_revision "
                    "FROM reasoner_session_checkpoints WHERE checkpoint_id = ?",
                    (current["accepted_checkpoint_id"],),
                ).fetchone()
                if (
                    active_checkpoint is None
                    or active_checkpoint["status"] != CheckpointStatus.ACCEPTED.value
                    or (
                        checkpoint.replaces_artifact_id is None
                        and current["accepted_checkpoint_id"]
                        != str(checkpoint.parent_checkpoint_id)
                    )
                    or (
                        checkpoint.replaces_artifact_id is not None
                        and active_checkpoint["accepted_head_artifact_id"]
                        != str(checkpoint.replaces_artifact_id)
                    )
                    or active_checkpoint["generation"] != receipt.generation_before
                ):
                    raise StateConflictError("accepted checkpoint changed before promotion")
                prior = connection.execute(
                    "SELECT status, review_id FROM reasoner_session_checkpoints "
                    "WHERE checkpoint_id = ?",
                    (str(checkpoint.checkpoint_id),),
                ).fetchone()
                if prior is None or prior["status"] != CheckpointStatus.CANDIDATE.value:
                    raise StateConflictError("candidate is no longer promotable")
                if prior["review_id"] != str(receipt.review_id):
                    raise StateConflictError("accepted receipt changed creator review")
                connection.execute(
                    "INSERT INTO reasoner_accepted_turn_receipts("
                    "receipt_id, session_id, checkpoint_id, review_id, branch_id, "
                    "artifact_id, receipt_json, receipt_sha256, created_at"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        str(receipt.receipt_id),
                        str(receipt.session_id),
                        str(receipt.checkpoint_id),
                        str(receipt.review_id),
                        str(receipt.branch_id),
                        str(receipt.artifact_id),
                        receipt_json,
                        receipt.receipt_sha256,
                        receipt.created_at,
                    ),
                )
                self._update_checkpoint(connection, checkpoint, checkpoint_json)
                if custody_event is not None:
                    self._insert_custody_event(connection, custody_event)
                connection.execute(
                    "UPDATE reasoner_sessions SET status = ?, accepted_checkpoint_id = ?, "
                    "accumulated_turns = ?, record_json = ?, record_sha256 = ?, "
                    "updated_at = ? WHERE session_id = ?",
                    (
                        ledger.status.value,
                        str(ledger.accepted_checkpoint_id),
                        ledger.accumulated_turns,
                        ledger_json,
                        ledger.ledger_sha256,
                        ledger.updated_at,
                        str(ledger.session_id),
                    ),
                )
                connection.commit()
        except StateConflictError:
            raise
        except Exception as exc:
            raise TransactionError("candidate promotion failed") from exc

    def reject_candidate_checkpoint(
        self,
        checkpoint: ReasonerSessionCheckpoint,
        receipt: RejectedCandidateReceipt,
        constraints: tuple[CreatorConstraintRecord, ...] = (),
        *,
        supersedes: tuple[TypedId, ...] = (),
        custody_event: ProviderThreadCustodyEvent | None = None,
    ) -> None:
        self._assert_custody_binding(
            custody_event,
            checkpoint,
            {
                ProviderThreadCustodyEventKind.REJECTED_DELETED,
                ProviderThreadCustodyEventKind.REJECTED_ARCHIVED,
            },
        )
        if (
            checkpoint.status is not CheckpointStatus.REJECTED
            or receipt.checkpoint_id != checkpoint.checkpoint_id
            or receipt.receipt_id != checkpoint.rejection_receipt_id
            or tuple(value.constraint_id for value in constraints)
            != receipt.constraint_ids
        ):
            raise StateConflictError("rejected candidate bindings are invalid")
        checkpoint_json = canonical_json(checkpoint)
        receipt_json = canonical_json(receipt)
        try:
            with self._connect() as connection:
                self._begin(connection)
                prior = connection.execute(
                    "SELECT status, review_id FROM reasoner_session_checkpoints "
                    "WHERE checkpoint_id = ?",
                    (str(checkpoint.checkpoint_id),),
                ).fetchone()
                if prior is None or prior["status"] != CheckpointStatus.CANDIDATE.value:
                    raise StateConflictError("candidate is no longer rejectable")
                if prior["review_id"] != str(receipt.review_id):
                    raise StateConflictError("rejection changed creator review")
                self._insert_constraints(
                    connection, constraints, supersedes=supersedes
                )
                connection.execute(
                    "INSERT INTO reasoner_rejected_candidate_receipts("
                    "receipt_id, session_id, checkpoint_id, review_id, branch_id, "
                    "receipt_json, receipt_sha256, created_at"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        str(receipt.receipt_id),
                        str(receipt.session_id),
                        str(receipt.checkpoint_id),
                        str(receipt.review_id),
                        str(receipt.branch_id),
                        receipt_json,
                        receipt.receipt_sha256,
                        receipt.created_at,
                    ),
                )
                self._update_checkpoint(connection, checkpoint, checkpoint_json)
                if custody_event is not None:
                    self._insert_custody_event(connection, custody_event)
                connection.commit()
        except StateConflictError:
            raise
        except Exception as exc:
            raise TransactionError("candidate rejection failed") from exc

    def invalidate_candidate_checkpoint(
        self,
        checkpoint: ReasonerSessionCheckpoint,
        custody_event: ProviderThreadCustodyEvent | None = None,
    ) -> None:
        self._assert_custody_binding(
            custody_event,
            checkpoint,
            {
                ProviderThreadCustodyEventKind.FAILED_DELETED,
                ProviderThreadCustodyEventKind.FAILED_ARCHIVED,
            },
        )
        if checkpoint.status is not CheckpointStatus.INVALIDATED:
            raise StateConflictError("candidate invalidation requires invalidated status")
        payload = canonical_json(checkpoint)
        try:
            with self._connect() as connection:
                self._begin(connection)
                row = connection.execute(
                    "SELECT status FROM reasoner_session_checkpoints "
                    "WHERE checkpoint_id = ?",
                    (str(checkpoint.checkpoint_id),),
                ).fetchone()
                if row is None or row["status"] != CheckpointStatus.CANDIDATE.value:
                    raise StateConflictError("candidate is no longer invalidatable")
                self._update_checkpoint(connection, checkpoint, payload)
                if custody_event is not None:
                    self._insert_custody_event(connection, custody_event)
                connection.commit()
        except StateConflictError:
            raise
        except Exception as exc:
            raise TransactionError("candidate invalidation failed") from exc

    def rotate_reasoner_session(
        self,
        terminal_ledger: ReasonerSessionLedger,
        new_ledger: ReasonerSessionLedger,
        root_checkpoint: ReasonerSessionCheckpoint,
        root_custody_event: ProviderThreadCustodyEvent | None = None,
    ) -> None:
        self._assert_custody_binding(
            root_custody_event,
            root_checkpoint,
            {ProviderThreadCustodyEventKind.ALLOCATED},
        )
        if (
            terminal_ledger.status not in {SessionStatus.ROTATED, SessionStatus.INVALIDATED}
            or new_ledger.status is not SessionStatus.ACTIVE
            or new_ledger.rotated_from_session_id != terminal_ledger.session_id
            or new_ledger.compatibility.world_id
            != terminal_ledger.compatibility.world_id
            or new_ledger.compatibility.branch_id
            != terminal_ledger.compatibility.branch_id
            or new_ledger.compatibility.role != terminal_ledger.compatibility.role
            or root_checkpoint.session_id != new_ledger.session_id
            or root_checkpoint.status is not CheckpointStatus.ACCEPTED
        ):
            raise StateConflictError("reasoner session rotation bindings are invalid")
        terminal_json = canonical_json(terminal_ledger)
        new_json = canonical_json(new_ledger)
        root_json = canonical_json(root_checkpoint)
        try:
            with self._connect() as connection:
                self._begin(connection)
                current = connection.execute(
                    "SELECT status, accepted_checkpoint_id FROM reasoner_sessions "
                    "WHERE session_id = ?",
                    (str(terminal_ledger.session_id),),
                ).fetchone()
                if current is None or current["status"] != SessionStatus.ACTIVE.value:
                    raise StateConflictError("reasoner session is not rotatable")
                if current["accepted_checkpoint_id"] != str(
                    terminal_ledger.accepted_checkpoint_id
                ):
                    raise StateConflictError("rotation changed accepted checkpoint")
                connection.execute(
                    "UPDATE reasoner_sessions SET status = ?, record_json = ?, "
                    "record_sha256 = ?, updated_at = ? WHERE session_id = ?",
                    (
                        terminal_ledger.status.value,
                        terminal_json,
                        terminal_ledger.ledger_sha256,
                        terminal_ledger.updated_at,
                        str(terminal_ledger.session_id),
                    ),
                )
                self._insert_session_row(connection, new_ledger, new_json)
                self._insert_checkpoint(connection, root_checkpoint, root_json)
                if root_custody_event is not None:
                    self._insert_custody_event(connection, root_custody_event)
                connection.commit()
        except StateConflictError:
            raise
        except Exception as exc:
            raise TransactionError("reasoner session rotation failed") from exc

    def invalidate_reasoner_session(self, ledger: ReasonerSessionLedger) -> None:
        if ledger.status is not SessionStatus.INVALIDATED:
            raise StateConflictError("session invalidation requires invalidated status")
        payload = canonical_json(ledger)
        try:
            with self._connect() as connection:
                self._begin(connection)
                cursor = connection.execute(
                    "UPDATE reasoner_sessions SET status = ?, record_json = ?, "
                    "record_sha256 = ?, updated_at = ? "
                    "WHERE session_id = ? AND status = 'active'",
                    (
                        ledger.status.value,
                        payload,
                        ledger.ledger_sha256,
                        ledger.updated_at,
                        str(ledger.session_id),
                    ),
                )
                if cursor.rowcount != 1:
                    raise StateConflictError("reasoner session is no longer active")
                connection.commit()
        except StateConflictError:
            raise
        except Exception as exc:
            raise TransactionError("reasoner session invalidation failed") from exc

    def active_creator_constraints(
        self, world_id: TypedId, branch_id: TypedId
    ) -> tuple[CreatorConstraintRecord, ...]:
        require_kind(world_id, IdKind.WORLD, "world_id")
        require_kind(branch_id, IdKind.BRANCH, "branch_id")
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT record_json, record_sha256 FROM creator_constraints "
                "WHERE world_id = ? AND status = 'active' "
                "AND (scope = 'global' OR branch_id = ?) "
                "ORDER BY created_at, constraint_id",
                (str(world_id), str(branch_id)),
            ).fetchall()
        values = tuple(
            from_mapping(CreatorConstraintRecord, json.loads(row["record_json"]))
            for row in rows
        )
        if any(
            value.record_sha256 != row["record_sha256"]
            for value, row in zip(values, rows, strict=True)
        ):
            raise TransactionError("creator constraint hash mismatch")
        return values

    def put_session_usage_receipt(self, receipt: SessionUsageReceiptV2) -> None:
        payload = canonical_json(receipt)
        try:
            with self._connect() as connection:
                self._begin(connection)
                connection.execute(
                    "INSERT INTO reasoner_session_usage_receipts("
                    "receipt_id, session_id, checkpoint_id, branch_id, receipt_json, "
                    "receipt_sha256, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        str(receipt.receipt_id),
                        str(receipt.session_id),
                        str(receipt.checkpoint_id),
                        str(receipt.branch_id),
                        payload,
                        receipt.receipt_sha256,
                        receipt.created_at,
                    ),
                )
                connection.commit()
        except Exception as exc:
            raise TransactionError("reasoner session usage write failed") from exc

    def get_accepted_turn_session_receipt(
        self, checkpoint_id: TypedId
    ) -> AcceptedTurnReceipt | None:
        return self._get_checkpoint_receipt(
            checkpoint_id,
            table="reasoner_accepted_turn_receipts",
            model_type=AcceptedTurnReceipt,
        )

    def get_rejected_candidate_session_receipt(
        self, checkpoint_id: TypedId
    ) -> RejectedCandidateReceipt | None:
        return self._get_checkpoint_receipt(
            checkpoint_id,
            table="reasoner_rejected_candidate_receipts",
            model_type=RejectedCandidateReceipt,
        )

    def reasoner_session_usage_receipts(
        self, session_id: TypedId
    ) -> tuple[SessionUsageReceiptV2, ...]:
        require_kind(session_id, IdKind.SESSION, "session_id")
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT receipt_json, receipt_sha256 FROM "
                "reasoner_session_usage_receipts WHERE session_id = ? "
                "ORDER BY created_at, receipt_id",
                (str(session_id),),
            ).fetchall()
        values = tuple(
            from_mapping(SessionUsageReceiptV2, json.loads(row["receipt_json"]))
            for row in rows
        )
        if any(
            value.receipt_sha256 != row["receipt_sha256"]
            for value, row in zip(values, rows, strict=True)
        ):
            raise TransactionError("reasoner session usage receipt hash mismatch")
        return values

    def provider_thread_custody_events(
        self, checkpoint_id: TypedId
    ) -> tuple[ProviderThreadCustodyEvent, ...]:
        require_kind(checkpoint_id, IdKind.CHECKPOINT, "checkpoint_id")
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT event_json, event_sha256 FROM "
                "reasoner_provider_thread_custody_events WHERE checkpoint_id = ? "
                "ORDER BY created_at, event_id",
                (str(checkpoint_id),),
            ).fetchall()
        values = tuple(
            from_mapping(ProviderThreadCustodyEvent, json.loads(row["event_json"]))
            for row in rows
        )
        if any(
            value.event_sha256 != row["event_sha256"]
            for value, row in zip(values, rows, strict=True)
        ):
            raise TransactionError("provider thread custody event hash mismatch")
        return values

    def put_provider_thread_custody_event(
        self, event: ProviderThreadCustodyEvent
    ) -> None:
        checkpoint = self.get_reasoner_checkpoint(event.checkpoint_id)
        self._assert_custody_binding(
            event,
            checkpoint,
            {
                ProviderThreadCustodyEventKind.RESUMED,
                ProviderThreadCustodyEventKind.ARCHIVED,
                ProviderThreadCustodyEventKind.MISSING,
            },
        )
        try:
            with self._connect() as connection:
                self._begin(connection)
                self._insert_custody_event(connection, event)
                connection.commit()
        except StateConflictError:
            raise
        except Exception as exc:
            raise TransactionError("provider thread custody event write failed") from exc

    def _get_checkpoint_receipt(self, checkpoint_id, *, table, model_type):
        require_kind(checkpoint_id, IdKind.CHECKPOINT, "checkpoint_id")
        with self._connect() as connection:
            row = connection.execute(
                f"SELECT receipt_json, receipt_sha256 FROM {table} "
                "WHERE checkpoint_id = ?",
                (str(checkpoint_id),),
            ).fetchone()
        if row is None:
            return None
        value = from_mapping(model_type, json.loads(row["receipt_json"]))
        if value.receipt_sha256 != row["receipt_sha256"]:
            raise TransactionError("reasoner checkpoint receipt hash mismatch")
        return value

    @staticmethod
    def _insert_custody_event(connection, value: ProviderThreadCustodyEvent) -> None:
        payload = canonical_json(value)
        connection.execute(
            "INSERT INTO reasoner_provider_thread_custody_events("
            "event_id, session_id, checkpoint_id, event_kind, "
            "provider_thread_id_sha256, parent_provider_thread_id_sha256, "
            "storage_mode, raw_context_retained, "
            "provider_context_is_story_authority, event_json, event_sha256, created_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                str(value.event_id),
                str(value.session_id),
                str(value.checkpoint_id),
                value.event_kind.value,
                value.descriptor.provider_thread_id_sha256,
                value.descriptor.parent_provider_thread_id_sha256,
                value.descriptor.storage_mode.value,
                int(value.descriptor.raw_context_retained),
                int(value.descriptor.provider_context_is_story_authority),
                payload,
                value.event_sha256,
                value.created_at,
            ),
        )

    @staticmethod
    def _assert_custody_binding(
        event: ProviderThreadCustodyEvent | None,
        checkpoint: ReasonerSessionCheckpoint,
        allowed: set[ProviderThreadCustodyEventKind],
    ) -> None:
        if event is None:
            return
        if (
            event.session_id != checkpoint.session_id
            or event.checkpoint_id != checkpoint.checkpoint_id
            or event.event_kind not in allowed
            or event.descriptor.provider_thread_id_sha256
            != text_sha256(checkpoint.provider_handle.provider_thread_id)
        ):
            raise StateConflictError("provider thread custody event binding is invalid")

    @staticmethod
    def _insert_checkpoint(connection, value, payload: str) -> None:
        connection.execute(
            "INSERT INTO reasoner_session_checkpoints("
            "checkpoint_id, session_id, parent_checkpoint_id, branch_id, request_id, "
            "review_id, turn_mode, replaces_artifact_id, status, accepted_head_artifact_id, generation, authority_revision, "
            "provider_session_id, provider_thread_id, context_manifest_sha256, "
            "record_json, record_sha256, created_at, updated_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                str(value.checkpoint_id),
                str(value.session_id),
                str(value.parent_checkpoint_id) if value.parent_checkpoint_id else None,
                str(value.branch_id),
                str(value.request_id) if value.request_id else None,
                str(value.review_id) if value.review_id else None,
                value.turn_mode.value if value.turn_mode is not None else None,
                str(value.replaces_artifact_id) if value.replaces_artifact_id else None,
                value.status.value,
                (
                    str(value.accepted_head_artifact_id)
                    if value.accepted_head_artifact_id
                    else None
                ),
                value.generation,
                value.authority_revision,
                value.provider_handle.provider_session_id,
                value.provider_handle.provider_thread_id,
                value.context_manifest_sha256,
                payload,
                value.checkpoint_sha256,
                value.created_at,
                value.updated_at,
            ),
        )

    @staticmethod
    def _update_checkpoint(connection, value, payload: str) -> None:
        connection.execute(
            "UPDATE reasoner_session_checkpoints SET review_id = ?, status = ?, "
            "accepted_head_artifact_id = ?, generation = ?, authority_revision = ?, "
            "provider_session_id = ?, provider_thread_id = ?, "
            "context_manifest_sha256 = ?, record_json = ?, record_sha256 = ?, "
            "updated_at = ? WHERE checkpoint_id = ?",
            (
                str(value.review_id) if value.review_id else None,
                value.status.value,
                (
                    str(value.accepted_head_artifact_id)
                    if value.accepted_head_artifact_id
                    else None
                ),
                value.generation,
                value.authority_revision,
                value.provider_handle.provider_session_id,
                value.provider_handle.provider_thread_id,
                value.context_manifest_sha256,
                payload,
                value.checkpoint_sha256,
                value.updated_at,
                str(value.checkpoint_id),
            ),
        )

    @staticmethod
    def _insert_session_row(connection, ledger, payload: str) -> None:
        connection.execute(
            "INSERT INTO reasoner_sessions("
            "session_id, world_id, branch_id, role, compatibility_sha256, status, "
            "accepted_checkpoint_id, provider_session_id, provider_root_thread_id, "
            "accumulated_turns, rotated_from_session_id, record_json, record_sha256, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                str(ledger.session_id),
                str(ledger.compatibility.world_id),
                str(ledger.compatibility.branch_id),
                ledger.compatibility.role.value,
                ledger.compatibility.compatibility_sha256,
                ledger.status.value,
                str(ledger.accepted_checkpoint_id),
                ledger.provider_root_handle.provider_session_id,
                ledger.provider_root_handle.provider_thread_id,
                ledger.accumulated_turns,
                str(ledger.rotated_from_session_id) if ledger.rotated_from_session_id else None,
                payload,
                ledger.ledger_sha256,
                ledger.created_at,
                ledger.updated_at,
            ),
        )

    def _insert_constraints(
        self,
        connection,
        constraints: tuple[CreatorConstraintRecord, ...],
        *,
        supersedes: tuple[TypedId, ...],
    ) -> None:
        if not constraints and supersedes:
            raise StateConflictError("constraint supersession requires a successor")
        if len(constraints) > 1 and supersedes:
            raise StateConflictError("constraint supersession requires one successor")
        for constraint in constraints:
            payload = canonical_json(constraint)
            connection.execute(
                "INSERT INTO creator_constraints("
                "constraint_id, scope, world_id, branch_id, source_review_id, "
                "source_diagnostic_id, owner, status, superseded_by_constraint_id, "
                "record_json, record_sha256, created_at, updated_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    str(constraint.constraint_id),
                    constraint.scope.value,
                    str(constraint.world_id),
                    str(constraint.branch_id) if constraint.branch_id else None,
                    str(constraint.source_review_id),
                    str(constraint.source_diagnostic_id),
                    constraint.owner,
                    constraint.status.value,
                    None,
                    payload,
                    constraint.record_sha256,
                    constraint.created_at,
                    constraint.updated_at,
                ),
            )
        if supersedes:
            successor = constraints[0]
            for constraint_id in supersedes:
                require_kind(
                    constraint_id, IdKind.CREATOR_CONSTRAINT, "supersedes"
                )
                row = connection.execute(
                    "SELECT record_json FROM creator_constraints "
                    "WHERE constraint_id = ? AND status = 'active'",
                    (str(constraint_id),),
                ).fetchone()
                if row is None:
                    raise StateConflictError("superseded constraint is not active")
                prior = from_mapping(
                    CreatorConstraintRecord, json.loads(row["record_json"])
                )
                if prior.world_id != successor.world_id:
                    raise StateConflictError("constraint supersession crossed worlds")
                updated = replace(
                    prior,
                    status=ConstraintStatus.SUPERSEDED,
                    superseded_by_constraint_id=successor.constraint_id,
                    updated_at=successor.created_at,
                )
                connection.execute(
                    "UPDATE creator_constraints SET status = ?, "
                    "superseded_by_constraint_id = ?, record_json = ?, "
                    "record_sha256 = ?, updated_at = ? WHERE constraint_id = ?",
                    (
                        updated.status.value,
                        str(successor.constraint_id),
                        canonical_json(updated),
                        updated.record_sha256,
                        updated.updated_at,
                        str(prior.constraint_id),
                    ),
                )
