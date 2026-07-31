"""SQLite persistence helpers for blocked-turn resumption state."""

from __future__ import annotations

import json
import sqlite3

from cera.contracts import (
    AftermathDecision,
    ExternalCompletionReceipt,
    ExternalEventRequest,
    ExternalReceiptValidationReceipt,
    TemporaryAftermathProjection,
)
from cera.errors import TransactionError
from cera.ids import IdKind, TypedId, require_kind
from cera.resumption.models import (
    AftermathReasonerReceipt,
    BlockedTurnBundle,
    BlockedTurnStatus,
    ExternalReceiptStatus,
    StoredBlockedTurn,
    StoredExternalReceipt,
)
from cera.schema import from_mapping
from cera.serialization import canonical_json, text_sha256

from .models import TurnCommitBundle


class BlockedTurnStoreMixin:
    def store_blocked_turn(self, bundle: BlockedTurnBundle) -> StoredBlockedTurn:
        with self._connect() as connection:
            self._begin(connection)
            existing = connection.execute(
                "SELECT checkpoint_id FROM blocked_turns WHERE checkpoint_id = ? "
                "OR request_id = ? OR external_request_id = ?",
                (
                    str(bundle.checkpoint.checkpoint_id),
                    str(bundle.checkpoint.request_id),
                    str(bundle.external_request.external_request_id),
                ),
            ).fetchone()
            if existing is not None:
                stored = self._blocked_turn_in_connection(
                    connection,
                    TypedId.parse(existing["checkpoint_id"], IdKind.CHECKPOINT),
                    exact_replay=True,
                )
                if stored.bundle.bundle_sha256 != bundle.bundle_sha256:
                    connection.rollback()
                    raise TransactionError("blocked-turn identity was reused differently")
                connection.commit()
                return stored
            now = self._now()
            request = bundle.external_request
            connection.execute(
                "INSERT INTO blocked_turns("
                "checkpoint_id, request_id, branch_id, generation_id, source_sha256, "
                "starting_artifact_id, starting_artifact_sha256, checkpoint_json, "
                "checkpoint_sha256, rejection_json, external_request_id, "
                "external_request_json, external_request_sha256, callback_token_sha256, "
                "allowed_event_registry_version, status, accepted_receipt_id, "
                "transaction_id, created_at, updated_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
                "'awaiting_external_receipt', NULL, NULL, ?, ?)",
                (
                    str(bundle.checkpoint.checkpoint_id),
                    str(bundle.checkpoint.request_id),
                    str(bundle.checkpoint.branch_id),
                    str(bundle.checkpoint.generation_id),
                    bundle.checkpoint.source_sha256,
                    str(bundle.checkpoint.starting_artifact_id)
                    if bundle.checkpoint.starting_artifact_id is not None
                    else None,
                    bundle.checkpoint.starting_artifact_sha256,
                    canonical_json(bundle.checkpoint),
                    bundle.checkpoint.checkpoint_sha256,
                    canonical_json(bundle.rejection),
                    str(request.external_request_id),
                    canonical_json(request),
                    request.external_request_sha256,
                    text_sha256(request.callback_correlation_token),
                    request.allowed_event_registry_version,
                    now,
                    now,
                ),
            )
            if bundle.projection is not None:
                connection.execute(
                    "INSERT INTO temporary_aftermath_projections("
                    "projection_id, checkpoint_id, branch_id, projection_json, "
                    "projection_sha256, expires_at, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        str(bundle.projection.projection_id),
                        str(bundle.projection.checkpoint_id),
                        str(bundle.projection.branch_id),
                        canonical_json(bundle.projection),
                        bundle.projection.projection_sha256,
                        bundle.projection.expires_at,
                        now,
                    ),
                )
            connection.commit()
        return self.get_blocked_turn(bundle.checkpoint.checkpoint_id)

    def get_blocked_turn(self, checkpoint_id: TypedId) -> StoredBlockedTurn:
        require_kind(checkpoint_id, IdKind.CHECKPOINT, "checkpoint_id")
        with self._connect() as connection:
            return self._blocked_turn_in_connection(connection, checkpoint_id)

    def attach_temporary_projection(
        self, projection: TemporaryAftermathProjection
    ) -> StoredBlockedTurn:
        with self._connect() as connection:
            self._begin(connection)
            blocked = connection.execute(
                "SELECT branch_id, status FROM blocked_turns WHERE checkpoint_id = ?",
                (str(projection.checkpoint_id),),
            ).fetchone()
            if blocked is None:
                connection.rollback()
                raise TransactionError("projection checkpoint does not exist")
            if blocked["branch_id"] != str(projection.branch_id):
                connection.rollback()
                raise TransactionError("projection branch does not match checkpoint")
            if blocked["status"] != BlockedTurnStatus.AWAITING_EXTERNAL_RECEIPT.value:
                connection.rollback()
                raise TransactionError("projection can only attach before receipt arrival")
            existing = connection.execute(
                "SELECT projection_json FROM temporary_aftermath_projections "
                "WHERE checkpoint_id = ?",
                (str(projection.checkpoint_id),),
            ).fetchone()
            if existing is not None:
                stored = from_mapping(
                    TemporaryAftermathProjection,
                    json.loads(existing["projection_json"]),
                )
                if stored.projection_sha256 != projection.projection_sha256:
                    connection.rollback()
                    raise TransactionError("checkpoint projection was reused differently")
                connection.commit()
                return self.get_blocked_turn(projection.checkpoint_id)
            connection.execute(
                "INSERT INTO temporary_aftermath_projections("
                "projection_id, checkpoint_id, branch_id, projection_json, "
                "projection_sha256, expires_at, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    str(projection.projection_id),
                    str(projection.checkpoint_id),
                    str(projection.branch_id),
                    canonical_json(projection),
                    projection.projection_sha256,
                    projection.expires_at,
                    self._now(),
                ),
            )
            connection.commit()
        return self.get_blocked_turn(projection.checkpoint_id)

    def get_blocked_turn_for_external_request(
        self, external_request_id: TypedId
    ) -> StoredBlockedTurn:
        require_kind(external_request_id, IdKind.EXTERNAL_REQUEST, "external_request_id")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT checkpoint_id FROM blocked_turns WHERE external_request_id = ?",
                (str(external_request_id),),
            ).fetchone()
            if row is None:
                raise TransactionError("external request does not exist")
            return self._blocked_turn_in_connection(
                connection, TypedId.parse(row["checkpoint_id"], IdKind.CHECKPOINT)
            )

    def _blocked_turn_in_connection(
        self,
        connection: sqlite3.Connection,
        checkpoint_id: TypedId,
        *,
        exact_replay: bool = False,
    ) -> StoredBlockedTurn:
        row = connection.execute(
            "SELECT * FROM blocked_turns WHERE checkpoint_id = ?",
            (str(checkpoint_id),),
        ).fetchone()
        if row is None:
            raise TransactionError(f"blocked checkpoint does not exist: {checkpoint_id}")
        projection_row = connection.execute(
            "SELECT projection_json FROM temporary_aftermath_projections "
            "WHERE checkpoint_id = ?",
            (str(checkpoint_id),),
        ).fetchone()
        payload = {
            "checkpoint": json.loads(row["checkpoint_json"]),
            "rejection": json.loads(row["rejection_json"]),
            "external_request": json.loads(row["external_request_json"]),
            "projection": json.loads(projection_row["projection_json"])
            if projection_row is not None
            else None,
        }
        return StoredBlockedTurn(
            bundle=from_mapping(BlockedTurnBundle, payload),
            status=BlockedTurnStatus(row["status"]),
            accepted_receipt_id=TypedId.parse(
                row["accepted_receipt_id"], IdKind.EXTERNAL_RECEIPT
            )
            if row["accepted_receipt_id"]
            else None,
            transaction_id=TypedId.parse(row["transaction_id"], IdKind.TRANSACTION)
            if row["transaction_id"]
            else None,
            exact_replay=exact_replay,
        )

    def receive_external_receipt(
        self, receipt: ExternalCompletionReceipt
    ) -> StoredExternalReceipt:
        with self._connect() as connection:
            self._begin(connection)
            existing = connection.execute(
                "SELECT receipt_id FROM external_receipt_claims WHERE receipt_id = ? "
                "OR idempotency_key = ? OR external_request_id = ?",
                (
                    str(receipt.receipt_id),
                    receipt.idempotency_key,
                    str(receipt.external_request_id),
                ),
            ).fetchone()
            if existing is not None:
                stored = self._external_receipt_in_connection(
                    connection,
                    TypedId.parse(existing["receipt_id"], IdKind.EXTERNAL_RECEIPT),
                    exact_replay=True,
                )
                if stored.receipt.receipt_sha256 != receipt.receipt_sha256:
                    connection.rollback()
                    raise TransactionError("external receipt identity was reused differently")
                connection.commit()
                return stored
            blocked = connection.execute(
                "SELECT checkpoint_id, status FROM blocked_turns WHERE external_request_id = ?",
                (str(receipt.external_request_id),),
            ).fetchone()
            if blocked is None:
                connection.rollback()
                raise TransactionError("external receipt request does not exist")
            if blocked["status"] != BlockedTurnStatus.AWAITING_EXTERNAL_RECEIPT.value:
                connection.rollback()
                raise TransactionError("blocked turn is not awaiting a new receipt")
            now = self._now()
            connection.execute(
                "INSERT INTO external_receipt_claims("
                "receipt_id, idempotency_key, external_request_id, checkpoint_id, "
                "receipt_json, receipt_sha256, status, validation_receipt_json, "
                "aftermath_decision_json, aftermath_decision_sha256, commit_bundle_json, "
                "aftermath_reasoner_receipt_json, commit_bundle_sha256, transaction_id, "
                "created_at, updated_at"
                ") VALUES (?, ?, ?, ?, ?, ?, 'receipt_received', NULL, NULL, NULL, "
                "NULL, NULL, NULL, NULL, ?, ?)",
                (
                    str(receipt.receipt_id),
                    receipt.idempotency_key,
                    str(receipt.external_request_id),
                    blocked["checkpoint_id"],
                    canonical_json(receipt),
                    receipt.receipt_sha256,
                    now,
                    now,
                ),
            )
            connection.execute(
                "UPDATE blocked_turns SET status = 'receipt_received', updated_at = ? "
                "WHERE checkpoint_id = ?",
                (now, blocked["checkpoint_id"]),
            )
            connection.commit()
        return self.get_external_receipt(receipt.receipt_id)

    def mark_receipt_validated(
        self, validation: ExternalReceiptValidationReceipt
    ) -> StoredExternalReceipt:
        return self._advance_receipt(
            validation.receipt_id,
            expected=ExternalReceiptStatus.RECEIPT_RECEIVED,
            target=ExternalReceiptStatus.RECEIPT_VALIDATED_PENDING,
            assignments={"validation_receipt_json": canonical_json(validation)},
        )

    def store_aftermath_decision(
        self, decision: AftermathDecision, reasoner_receipt: AftermathReasonerReceipt
    ) -> StoredExternalReceipt:
        if reasoner_receipt.decision_sha256 != decision.decision_sha256:
            raise TransactionError("aftermath reasoner receipt does not bind decision")
        return self._advance_receipt(
            decision.receipt_id,
            expected=ExternalReceiptStatus.RECEIPT_VALIDATED_PENDING,
            target=ExternalReceiptStatus.AFTERMATH_DECIDED,
            assignments={
                "aftermath_decision_json": canonical_json(decision),
                "aftermath_decision_sha256": decision.decision_sha256,
                "aftermath_reasoner_receipt_json": canonical_json(reasoner_receipt),
            },
        )

    def stage_aftermath_commit(
        self, receipt_id: TypedId, bundle: TurnCommitBundle
    ) -> StoredExternalReceipt:
        if bundle.external_receipt_id != receipt_id:
            raise TransactionError("commit bundle does not bind the external receipt")
        return self._advance_receipt(
            receipt_id,
            expected=ExternalReceiptStatus.AFTERMATH_DECIDED,
            target=ExternalReceiptStatus.AFTERMATH_VALIDATED,
            assignments={
                "commit_bundle_json": canonical_json(bundle),
                "commit_bundle_sha256": bundle.bundle_sha256,
                "transaction_id": str(bundle.transaction_id),
            },
        )

    def _advance_receipt(
        self,
        receipt_id: TypedId,
        *,
        expected: ExternalReceiptStatus,
        target: ExternalReceiptStatus,
        assignments: dict[str, str],
    ) -> StoredExternalReceipt:
        require_kind(receipt_id, IdKind.EXTERNAL_RECEIPT, "receipt_id")
        allowed_columns = {
            "validation_receipt_json",
            "aftermath_decision_json",
            "aftermath_decision_sha256",
            "aftermath_reasoner_receipt_json",
            "commit_bundle_json",
            "commit_bundle_sha256",
            "transaction_id",
        }
        if not assignments or not set(assignments).issubset(allowed_columns):
            raise TransactionError("unsupported receipt lifecycle assignment")
        with self._connect() as connection:
            self._begin(connection)
            row = connection.execute(
                "SELECT checkpoint_id, status FROM external_receipt_claims WHERE receipt_id = ?",
                (str(receipt_id),),
            ).fetchone()
            if row is None:
                connection.rollback()
                raise TransactionError("external receipt does not exist")
            if row["status"] == target.value:
                connection.commit()
                return self.get_external_receipt(receipt_id, exact_replay=True)
            if row["status"] != expected.value:
                connection.rollback()
                raise TransactionError(
                    f"receipt transition requires {expected.value}, found {row['status']}"
                )
            now = self._now()
            columns = ", ".join(f"{column} = ?" for column in assignments)
            connection.execute(
                f"UPDATE external_receipt_claims SET {columns}, status = ?, updated_at = ? "
                "WHERE receipt_id = ?",
                (*assignments.values(), target.value, now, str(receipt_id)),
            )
            connection.execute(
                "UPDATE blocked_turns SET status = ?, updated_at = ? WHERE checkpoint_id = ?",
                (target.value, now, row["checkpoint_id"]),
            )
            connection.commit()
        return self.get_external_receipt(receipt_id)

    def get_external_receipt(
        self, receipt_id: TypedId, *, exact_replay: bool = False
    ) -> StoredExternalReceipt:
        require_kind(receipt_id, IdKind.EXTERNAL_RECEIPT, "receipt_id")
        with self._connect() as connection:
            return self._external_receipt_in_connection(
                connection, receipt_id, exact_replay=exact_replay
            )

    @staticmethod
    def _external_receipt_in_connection(
        connection: sqlite3.Connection,
        receipt_id: TypedId,
        *,
        exact_replay: bool = False,
    ) -> StoredExternalReceipt:
        row = connection.execute(
            "SELECT * FROM external_receipt_claims WHERE receipt_id = ?",
            (str(receipt_id),),
        ).fetchone()
        if row is None:
            raise TransactionError(f"external receipt does not exist: {receipt_id}")
        return StoredExternalReceipt(
            receipt=from_mapping(
                ExternalCompletionReceipt, json.loads(row["receipt_json"])
            ),
            status=ExternalReceiptStatus(row["status"]),
            validation_receipt=from_mapping(
                ExternalReceiptValidationReceipt,
                json.loads(row["validation_receipt_json"]),
            )
            if row["validation_receipt_json"]
            else None,
            aftermath_decision=from_mapping(
                AftermathDecision, json.loads(row["aftermath_decision_json"])
            )
            if row["aftermath_decision_json"]
            else None,
            aftermath_reasoner_receipt=from_mapping(
                AftermathReasonerReceipt,
                json.loads(row["aftermath_reasoner_receipt_json"]),
            )
            if row["aftermath_reasoner_receipt_json"]
            else None,
            commit_bundle=from_mapping(
                TurnCommitBundle, json.loads(row["commit_bundle_json"])
            )
            if row["commit_bundle_json"]
            else None,
            transaction_id=TypedId.parse(row["transaction_id"], IdKind.TRANSACTION)
            if row["transaction_id"]
            else None,
            exact_replay=exact_replay,
        )

    def artifact_sha256(self, artifact_id: TypedId) -> str:
        require_kind(artifact_id, IdKind.ARTIFACT, "artifact_id")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT artifact_sha256 FROM artifacts WHERE artifact_id = ?",
                (str(artifact_id),),
            ).fetchone()
        if row is None:
            raise TransactionError(f"artifact does not exist: {artifact_id}")
        return str(row["artifact_sha256"])

    def pending_resumptions(self) -> tuple[StoredExternalReceipt, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT receipt_id FROM external_receipt_claims WHERE status != 'committed' "
                "ORDER BY created_at, receipt_id"
            ).fetchall()
            return tuple(
                self._external_receipt_in_connection(
                    connection,
                    TypedId.parse(row["receipt_id"], IdKind.EXTERNAL_RECEIPT),
                )
                for row in rows
            )
