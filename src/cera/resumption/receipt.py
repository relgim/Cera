"""Python-owned external receipt decoding, validation, and idempotency."""

from __future__ import annotations

from datetime import UTC, datetime
import hmac
from typing import Mapping

from cera.contracts import (
    ConceptionStatus,
    ExposureStatus,
    ExternalCompletionReceipt,
    ExternalReceiptValidationReceipt,
)
from cera.errors import ContractValidationError, ErrorCode, TransactionError
from cera.ids import IdKind, TypedId, deterministic_id
from cera.schema import from_mapping
from cera.serialization import text_sha256
from cera.storage.sqlite_store import SQLiteAuthorityStore

from .models import (
    CONTROLLED_EVENT_CLASSIFICATIONS,
    BlockedTurnStatus,
    ExternalReceiptStatus,
    ExternalReceiptSubmission,
    ResumptionFailure,
    StoredBlockedTurn,
    StoredExternalReceipt,
)


def _utc(value: datetime | None = None) -> datetime:
    candidate = value or datetime.now(UTC)
    if candidate.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware")
    return candidate.astimezone(UTC)


class ExternalReceiptCoordinator:
    def __init__(self, store: SQLiteAuthorityStore) -> None:
        self.store = store

    def decode_submission(
        self, payload: Mapping[str, object] | None, callback_correlation_token: str
    ) -> ExternalReceiptSubmission:
        if payload is None:
            raise ResumptionFailure(ErrorCode.RECEIPT_MISSING, "external receipt is missing")
        try:
            receipt = from_mapping(ExternalCompletionReceipt, payload)
            return ExternalReceiptSubmission(receipt, callback_correlation_token)
        except (ContractValidationError, TypeError, ValueError) as exc:
            raise ResumptionFailure(ErrorCode.RECEIPT_MALFORMED, str(exc)) from exc

    def validate_and_store(
        self,
        submission: ExternalReceiptSubmission,
        *,
        now: datetime | None = None,
    ) -> StoredExternalReceipt:
        receipt = submission.receipt
        try:
            blocked = self.store.get_blocked_turn_for_external_request(
                receipt.external_request_id
            )
        except TransactionError as exc:
            raise ResumptionFailure(ErrorCode.RECEIPT_INTEGRITY_FAILED, str(exc)) from exc
        if blocked.status is BlockedTurnStatus.ACCEPTED:
            assert blocked.accepted_receipt_id is not None
            accepted = self.store.get_external_receipt(blocked.accepted_receipt_id)
            if (
                accepted.receipt.receipt_id == receipt.receipt_id
                and accepted.receipt.idempotency_key == receipt.idempotency_key
                and accepted.receipt.receipt_sha256 == receipt.receipt_sha256
            ):
                return StoredExternalReceipt(
                    receipt=accepted.receipt,
                    status=accepted.status,
                    validation_receipt=accepted.validation_receipt,
                    aftermath_decision=accepted.aftermath_decision,
                    aftermath_reasoner_receipt=accepted.aftermath_reasoner_receipt,
                    commit_bundle=accepted.commit_bundle,
                    transaction_id=accepted.transaction_id,
                    exact_replay=True,
                )
            raise ResumptionFailure(
                ErrorCode.RECEIPT_DUPLICATE_CONFLICT,
                "accepted checkpoint received a conflicting duplicate receipt",
            )
        self._validate_bindings(blocked, submission, now=_utc(now))
        try:
            stored = self.store.receive_external_receipt(receipt)
        except TransactionError as exc:
            raise ResumptionFailure(ErrorCode.RECEIPT_DUPLICATE_CONFLICT, str(exc)) from exc
        if stored.status is not ExternalReceiptStatus.RECEIPT_RECEIVED:
            return stored
        validation = ExternalReceiptValidationReceipt(
            schema_version=ExternalReceiptValidationReceipt.SCHEMA_VERSION,
            validation_receipt_id=deterministic_id(
                IdKind.VALIDATION,
                "cera.external_receipt_validation.v1",
                receipt.receipt_sha256,
            ),
            external_request_id=receipt.external_request_id,
            receipt_id=receipt.receipt_id,
            receipt_sha256=receipt.receipt_sha256,
            checkpoint_id=receipt.checkpoint_id,
            checkpoint_sha256=receipt.checkpoint_sha256,
            status="validated_pending_commit",
            validated_fields=(
                "schema_and_content_hash",
                "external_request_and_callback",
                "request_source_branch_generation",
                "starting_artifact_and_checkpoint",
                "registry_and_controlled_events",
                "consent_capacity_and_response_separation",
                "reproductive_exposure_and_conception_separation",
                "branch_head_freshness",
                "idempotency_identity",
            ),
            story_state_committed=False,
        )
        return self.store.mark_receipt_validated(validation)

    def _validate_bindings(
        self,
        blocked: StoredBlockedTurn,
        submission: ExternalReceiptSubmission,
        *,
        now: datetime,
    ) -> None:
        bundle = blocked.bundle
        checkpoint = bundle.checkpoint
        request = bundle.external_request
        receipt = submission.receipt
        if blocked.status is BlockedTurnStatus.ACCEPTED:
            raise ResumptionFailure(ErrorCode.RECEIPT_STALE, "checkpoint is already accepted")
        if not hmac.compare_digest(
            text_sha256(submission.callback_correlation_token),
            text_sha256(request.callback_correlation_token),
        ):
            raise ResumptionFailure(ErrorCode.RECEIPT_INTEGRITY_FAILED, "callback token mismatch")
        try:
            expires_at = datetime.fromisoformat(request.expires_at).astimezone(UTC)
        except ValueError as exc:
            raise ResumptionFailure(ErrorCode.RECEIPT_INTEGRITY_FAILED, "invalid request expiry") from exc
        if now > expires_at:
            raise ResumptionFailure(ErrorCode.RECEIPT_STALE, "external request expired")
        bindings = (
            receipt.external_request_id == request.external_request_id,
            receipt.allowed_event_registry_version == request.allowed_event_registry_version,
            receipt.world_id == checkpoint.world_id,
            receipt.genesis_revision_id == checkpoint.genesis_revision_id,
            receipt.protected_user_id == checkpoint.protected_user_id,
            receipt.request_id == checkpoint.request_id,
            receipt.source_sha256 == checkpoint.source_sha256,
            receipt.branch_id == checkpoint.branch_id,
            receipt.generation_id == checkpoint.generation_id,
            receipt.starting_artifact_id == checkpoint.starting_artifact_id,
            receipt.starting_artifact_sha256 == checkpoint.starting_artifact_sha256,
            receipt.checkpoint_id == checkpoint.checkpoint_id,
            receipt.checkpoint_sha256 == checkpoint.checkpoint_sha256,
        )
        if not all(bindings):
            code = (
                ErrorCode.RECEIPT_WRONG_BRANCH
                if receipt.branch_id != checkpoint.branch_id
                else ErrorCode.RECEIPT_INTEGRITY_FAILED
            )
            raise ResumptionFailure(code, "external receipt binding mismatch")
        branch = self.store.get_branch(checkpoint.branch_id)
        if branch.head_artifact_id != checkpoint.starting_artifact_id:
            raise ResumptionFailure(ErrorCode.RECEIPT_STALE, "branch head changed")
        self._validate_controlled_events(receipt)

    @staticmethod
    def _validate_controlled_events(receipt: ExternalCompletionReceipt) -> None:
        for step in receipt.ordered_events:
            if step.classification not in CONTROLLED_EVENT_CLASSIFICATIONS:
                raise ResumptionFailure(
                    ErrorCode.RECEIPT_MALFORMED,
                    f"unsupported controlled event classification: {step.classification}",
                )
            if not step.actor_ids or not step.target_ids:
                raise ResumptionFailure(
                    ErrorCode.RECEIPT_MALFORMED,
                    "external event steps require actor and target IDs",
                )
            target_characters = {
                value for value in step.target_ids if value.kind is IdKind.CHARACTER
            }
            if not target_characters.issubset(set(step.knowledge_owners)):
                raise ResumptionFailure(
                    ErrorCode.RECEIPT_INTEGRITY_FAILED,
                    "direct target knowledge is missing from event step",
                )
            phrases = (
                *step.resistance_or_freeze,
                *step.calls_for_help,
                *step.defensive_actions,
                *step.material_changes,
            )
            if any(not value.strip() or len(value) > 240 or "\n" in value for value in phrases):
                raise ResumptionFailure(
                    ErrorCode.RECEIPT_MALFORMED,
                    "receipt facts must be short non-graphic single-line claims",
                )
            if (
                step.conception_status is ConceptionStatus.ESTABLISHED
                and step.reproductive_exposure is not ExposureStatus.ESTABLISHED
            ):
                raise ResumptionFailure(
                    ErrorCode.RECEIPT_INTEGRITY_FAILED,
                    "conception cannot be established without established exposure",
                )
        if any(
            not value.strip() or len(value) > 240 or "\n" in value
            for value in receipt.immediate_aftermath_facts
        ):
            raise ResumptionFailure(
                ErrorCode.RECEIPT_MALFORMED,
                "immediate aftermath facts must be short non-graphic single-line claims",
            )


