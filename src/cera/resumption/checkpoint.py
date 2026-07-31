"""Python-owned blocked-turn checkpoint and neutral request creation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import secrets
from cera.contracts import (
    BlockedTurnCheckpoint,
    ConsentCapacityState,
    ErrorEnvelope,
    ExternalCompletionReceipt,
    ExternalEventRequest,
    RejectedTurnReceipt,
)
from cera.errors import ErrorCode, RetryMode, TransactionError
from cera.ids import IdKind, TypedId, deterministic_id
from cera.kernel import TurnIntakeCommand
from cera.serialization import canonical_json, text_sha256
from cera.storage.sqlite_store import SQLiteAuthorityStore

from .fake import (
    AftermathReasonerUnavailable,
    FakeSceneReasonerResumptionPort,
)
from .models import (
    CONTROLLED_EVENT_REGISTRY_VERSION,
    BlockedTurnBundle,
    ResumptionFailure,
    StoredBlockedTurn,
)


def _utc(value: datetime | None = None) -> datetime:
    candidate = value or datetime.now(UTC)
    if candidate.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware")
    return candidate.astimezone(UTC)


class BlockedTurnCoordinator:
    def __init__(self, store: SQLiteAuthorityStore) -> None:
        self.store = store

    def checkpoint(
        self,
        command: TurnIntakeCommand,
        *,
        actual_consent_capacity: ConsentCapacityState,
        projection_port: FakeSceneReasonerResumptionPort | None = None,
        now: datetime | None = None,
        lifetime: timedelta = timedelta(hours=24),
        callback_correlation_token: str | None = None,
    ) -> StoredBlockedTurn:
        if not command.preflight_authority.blocked_nonconsensual_crossing_established:
            raise ResumptionFailure(
                ErrorCode.INTAKE_INVALID,
                "blocked checkpoint requires an established non-consensual crossing",
            )
        if lifetime <= timedelta(0):
            raise ResumptionFailure(ErrorCode.INTAKE_INVALID, "checkpoint lifetime must be positive")
        boundary = command.preflight_authority.blocker_boundary_unit_index
        if boundary is None or boundary >= len(command.source_units):
            raise ResumptionFailure(ErrorCode.INTAKE_INVALID, "blocker boundary is invalid")
        branch = self.store.get_branch(command.branch_id)
        if branch.world_id != command.world_id:
            raise ResumptionFailure(ErrorCode.STATE_CONFLICT, "branch belongs to another world")
        try:
            bound_revision = self.store.get_world_genesis_revision(command.world_id)
        except TransactionError as exc:
            raise ResumptionFailure(ErrorCode.STATE_CONFLICT, str(exc)) from exc
        if bound_revision != command.genesis_revision_id:
            raise ResumptionFailure(
                ErrorCode.STATE_CONFLICT, "checkpoint Genesis revision is not the world binding"
            )
        if (
            branch.generation != command.expected_generation
            or branch.head_artifact_id != command.expected_parent_artifact_id
        ):
            raise ResumptionFailure(ErrorCode.STATE_CONFLICT, "blocked request branch is stale")
        protected_payload = {
            "source_units": tuple(
                {
                    "classification": unit.classification.value,
                    "text": unit.text,
                }
                for unit in command.source_units
            )
        }
        source_sha256 = text_sha256(canonical_json(protected_payload))
        generation_id = deterministic_id(
            IdKind.GENERATION, "cera.turn.request.v1", str(command.request_id)
        )
        boundary_unit = command.source_units[boundary]
        boundary_source_unit_id = deterministic_id(
            IdKind.SOURCE_UNIT,
            "cera.turn.source_unit.v1",
            f"{command.request_id}|{boundary}|{boundary_unit.classification.value}|{text_sha256(boundary_unit.text)}",
        )
        starting_sha256 = (
            self.store.artifact_sha256(branch.head_artifact_id)
            if branch.head_artifact_id is not None
            else None
        )
        checkpoint_id = deterministic_id(
            IdKind.CHECKPOINT,
            "cera.blocked_turn_checkpoint.v1",
            f"{command.request_id}|{source_sha256}|{boundary_source_unit_id}",
        )
        checkpoint = BlockedTurnCheckpoint.create(
            schema_version=BlockedTurnCheckpoint.SCHEMA_VERSION,
            checkpoint_id=checkpoint_id,
            world_id=command.world_id,
            genesis_revision_id=command.genesis_revision_id,
            protected_user_id=command.protected_user_id,
            request_id=command.request_id,
            source_sha256=source_sha256,
            branch_id=command.branch_id,
            generation_id=generation_id,
            starting_artifact_id=branch.head_artifact_id,
            starting_artifact_sha256=starting_sha256,
            classification="blocked_nonconsensual_event",
            boundary_source_unit_id=boundary_source_unit_id,
            facts_established_through_boundary=command.preflight_authority.facts_established_before_blocker,
            actual_consent_capacity=actual_consent_capacity,
            story_state_committed=False,
        )
        try:
            existing = self.store.get_blocked_turn(checkpoint_id)
        except TransactionError:
            existing = None
        if existing is not None:
            if existing.bundle.checkpoint.checkpoint_sha256 != checkpoint.checkpoint_sha256:
                raise ResumptionFailure(
                    ErrorCode.RECEIPT_DUPLICATE_CONFLICT,
                    "blocked checkpoint identity was reused differently",
                )
            return StoredBlockedTurn(
                bundle=existing.bundle,
                status=existing.status,
                accepted_receipt_id=existing.accepted_receipt_id,
                transaction_id=existing.transaction_id,
                exact_replay=True,
            )
        rejection = RejectedTurnReceipt(
            schema_version=RejectedTurnReceipt.SCHEMA_VERSION,
            rejection_id=deterministic_id(
                IdKind.REJECTION, "cera.blocked_turn_rejection.v1", str(checkpoint_id)
            ),
            request_id=command.request_id,
            checkpoint_id=checkpoint_id,
            error_code=ErrorCode.BLOCKED_NONCONSENSUAL_EVENT,
            story_state_committed=False,
            provider_calls_after_boundary=0,
        )
        current = _utc(now)
        token = callback_correlation_token or secrets.token_urlsafe(32)
        external_request = ExternalEventRequest(
            schema_version=ExternalEventRequest.SCHEMA_VERSION,
            external_request_id=deterministic_id(
                IdKind.EXTERNAL_REQUEST,
                "cera.external_event_request.v1",
                checkpoint.checkpoint_sha256,
            ),
            world_id=command.world_id,
            genesis_revision_id=command.genesis_revision_id,
            protected_user_id=command.protected_user_id,
            request_id=command.request_id,
            source_sha256=source_sha256,
            branch_id=command.branch_id,
            generation_id=generation_id,
            starting_artifact_id=branch.head_artifact_id,
            starting_artifact_sha256=starting_sha256,
            checkpoint_id=checkpoint_id,
            checkpoint_sha256=checkpoint.checkpoint_sha256,
            required_receipt_schema=ExternalCompletionReceipt.SCHEMA_VERSION,
            allowed_event_registry_version=CONTROLLED_EVENT_REGISTRY_VERSION,
            expires_at=(current + lifetime).isoformat(timespec="microseconds"),
            callback_correlation_token=token,
        )
        stored = self.store.store_blocked_turn(
            BlockedTurnBundle(checkpoint, rejection, external_request, None)
        )
        if projection_port is not None and not stored.exact_replay:
            try:
                projection = projection_port.project_temporary_aftermath(checkpoint)
            except AftermathReasonerUnavailable:
                projection = None
            if projection is not None:
                stored = self.store.attach_temporary_projection(projection)
        return stored

    @staticmethod
    def public_error(stored: StoredBlockedTurn, trace_id: TypedId) -> ErrorEnvelope:
        return ErrorEnvelope(
            schema_version=ErrorEnvelope.SCHEMA_VERSION,
            error_code=ErrorCode.BLOCKED_NONCONSENSUAL_EVENT,
            message=(
                "Generation stopped at the consent boundary. No story or memory state "
                f"was committed. Trace: {trace_id}"
            ),
            stage="preflight_blocker",
            trace_id=trace_id,
            request_id=stored.bundle.checkpoint.request_id,
            branch_id=stored.bundle.checkpoint.branch_id,
            generation_id=stored.bundle.checkpoint.generation_id,
            retry_mode=RetryMode.MANUAL_AFTER_REVIEW,
            story_state_committed=False,
        )


