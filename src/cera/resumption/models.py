"""Provider-free blocked-turn lifecycle and resumption records."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from cera.contracts import (
    AftermathDecision,
    BlockedTurnCheckpoint,
    ExternalCompletionReceipt,
    ExternalEventRequest,
    ExternalReceiptValidationReceipt,
    RejectedTurnReceipt,
    TemporaryAftermathProjection,
)
from cera.errors import ContractValidationError, ErrorCode
from cera.ids import IdKind, TypedId, require_kind
from cera.serialization import domain_sha256, text_sha256
if TYPE_CHECKING:
    from cera.storage.models import SourceRecord, TurnCommitBundle


CONTROLLED_EVENT_REGISTRY_VERSION = "cera.controlled_event_registry.v1"
CONTROLLED_EVENT_CLASSIFICATIONS = frozenset(
    {
        "physical_assault",
        "sexual_assault",
        "coercive_contact",
        "confinement_or_restraint",
        "forced_disrobing",
        "nonconsensual_sexual_act",
        "threat_or_intimidation",
        "defensive_resistance",
        "help_seeking",
        "escape",
        "interruption",
        "other_protected_non_graphic",
    }
)


class BlockedTurnStatus(StrEnum):
    AWAITING_EXTERNAL_RECEIPT = "awaiting_external_receipt"
    RECEIPT_RECEIVED = "receipt_received"
    RECEIPT_VALIDATED_PENDING = "receipt_validated_pending"
    AFTERMATH_DECIDED = "aftermath_decided"
    AFTERMATH_VALIDATED = "aftermath_validated"
    ACCEPTED = "accepted"


class ExternalReceiptStatus(StrEnum):
    RECEIPT_RECEIVED = "receipt_received"
    RECEIPT_VALIDATED_PENDING = "receipt_validated_pending"
    AFTERMATH_DECIDED = "aftermath_decided"
    AFTERMATH_VALIDATED = "aftermath_validated"
    COMMITTED = "committed"


@dataclass(frozen=True, slots=True)
class BlockedTurnBundle:
    checkpoint: BlockedTurnCheckpoint
    rejection: RejectedTurnReceipt
    external_request: ExternalEventRequest
    projection: TemporaryAftermathProjection | None = None

    def __post_init__(self) -> None:
        checkpoint = self.checkpoint
        request = self.external_request
        if self.rejection.request_id != checkpoint.request_id:
            raise ContractValidationError("rejection request does not match checkpoint")
        if self.rejection.checkpoint_id != checkpoint.checkpoint_id:
            raise ContractValidationError("rejection does not match checkpoint")
        if (
            request.world_id != checkpoint.world_id
            or request.genesis_revision_id != checkpoint.genesis_revision_id
            or request.protected_user_id != checkpoint.protected_user_id
            or request.request_id != checkpoint.request_id
            or request.source_sha256 != checkpoint.source_sha256
            or request.branch_id != checkpoint.branch_id
            or request.generation_id != checkpoint.generation_id
            or request.starting_artifact_id != checkpoint.starting_artifact_id
            or request.starting_artifact_sha256 != checkpoint.starting_artifact_sha256
            or request.checkpoint_id != checkpoint.checkpoint_id
            or request.checkpoint_sha256 != checkpoint.checkpoint_sha256
        ):
            raise ContractValidationError("external request does not bind the checkpoint")
        if request.allowed_event_registry_version != CONTROLLED_EVENT_REGISTRY_VERSION:
            raise ContractValidationError("external request uses an unsupported event registry")
        if self.projection is not None:
            if (
                self.projection.checkpoint_id != checkpoint.checkpoint_id
                or self.projection.branch_id != checkpoint.branch_id
            ):
                raise ContractValidationError("temporary projection does not bind checkpoint")

    @property
    def bundle_sha256(self) -> str:
        return domain_sha256("cera.blocked_turn_bundle.v1", self)


@dataclass(frozen=True, slots=True)
class StoredBlockedTurn:
    bundle: BlockedTurnBundle
    status: BlockedTurnStatus
    accepted_receipt_id: TypedId | None
    transaction_id: TypedId | None
    exact_replay: bool

    def __post_init__(self) -> None:
        if self.accepted_receipt_id is not None:
            require_kind(self.accepted_receipt_id, IdKind.EXTERNAL_RECEIPT, "receipt_id")
        if self.transaction_id is not None:
            require_kind(self.transaction_id, IdKind.TRANSACTION, "transaction_id")


@dataclass(frozen=True, slots=True)
class ExternalReceiptSubmission:
    receipt: ExternalCompletionReceipt
    callback_correlation_token: str

    def __post_init__(self) -> None:
        if not self.callback_correlation_token:
            raise ContractValidationError("callback correlation token is required")

    @property
    def callback_token_sha256(self) -> str:
        return text_sha256(self.callback_correlation_token)


@dataclass(frozen=True, slots=True)
class AftermathReasonerRequest:
    checkpoint: BlockedTurnCheckpoint
    receipt: ExternalCompletionReceipt
    validation_receipt: ExternalReceiptValidationReceipt
    projection: TemporaryAftermathProjection | None
    hard_boundaries: tuple[str, ...]

    def __post_init__(self) -> None:
        if (
            self.receipt.checkpoint_id != self.checkpoint.checkpoint_id
            or self.receipt.checkpoint_sha256 != self.checkpoint.checkpoint_sha256
            or self.validation_receipt.receipt_id != self.receipt.receipt_id
            or self.validation_receipt.receipt_sha256 != self.receipt.receipt_sha256
        ):
            raise ContractValidationError("aftermath reasoner request has broken receipt bindings")
        if self.projection is not None and (
            self.projection.checkpoint_id != self.checkpoint.checkpoint_id
            or self.projection.branch_id != self.checkpoint.branch_id
        ):
            raise ContractValidationError("aftermath projection belongs to another checkpoint")
        if not self.hard_boundaries:
            raise ContractValidationError("aftermath reasoning requires hard boundaries")

    @property
    def request_sha256(self) -> str:
        return domain_sha256("cera.aftermath_reasoner_request.v1", self)


@dataclass(frozen=True, slots=True)
class AftermathReasonerReceipt:
    SCHEMA_VERSION = "cera.aftermath_reasoner_receipt.v1"

    schema_version: str
    provider_receipt_id: TypedId
    reasoner_request_sha256: str
    external_receipt_sha256: str
    projection_sha256: str | None
    decision_sha256: str
    adapter_role: str
    fixture_id: str
    fixture_sha256: str
    external_provider_calls: int

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("invalid aftermath reasoner receipt schema")
        require_kind(self.provider_receipt_id, IdKind.PROVIDER_RECEIPT, "provider_receipt_id")
        for value in (
            self.reasoner_request_sha256,
            self.external_receipt_sha256,
            self.decision_sha256,
            self.fixture_sha256,
        ):
            if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
                raise ContractValidationError("aftermath reasoner receipt hash is invalid")
        if self.projection_sha256 is not None and (
            len(self.projection_sha256) != 64
            or any(character not in "0123456789abcdef" for character in self.projection_sha256)
        ):
            raise ContractValidationError("aftermath projection hash is invalid")
        if self.adapter_role != "fake_scene_reasoner" or self.external_provider_calls != 0:
            raise ContractValidationError("Phase 8 reasoner receipt requires the fake adapter")
        if not self.fixture_id:
            raise ContractValidationError("aftermath reasoner fixture ID is required")


@dataclass(frozen=True, slots=True)
class AftermathComposerRequest:
    aftermath_decision: AftermathDecision
    source_record: SourceRecord
    parent_artifact_id: TypedId | None
    generation_id: TypedId
    hard_boundaries: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.parent_artifact_id is not None:
            require_kind(self.parent_artifact_id, IdKind.ARTIFACT, "parent_artifact_id")
        require_kind(self.generation_id, IdKind.GENERATION, "generation_id")
        if not self.hard_boundaries:
            raise ContractValidationError("aftermath composition requires hard boundaries")

    @property
    def request_sha256(self) -> str:
        return domain_sha256("cera.aftermath_composer_request.v1", self)


@dataclass(frozen=True, slots=True)
class StoredExternalReceipt:
    receipt: ExternalCompletionReceipt
    status: ExternalReceiptStatus
    validation_receipt: ExternalReceiptValidationReceipt | None
    aftermath_decision: AftermathDecision | None
    aftermath_reasoner_receipt: AftermathReasonerReceipt | None
    commit_bundle: TurnCommitBundle | None
    transaction_id: TypedId | None
    exact_replay: bool

    def __post_init__(self) -> None:
        if self.transaction_id is not None:
            require_kind(self.transaction_id, IdKind.TRANSACTION, "transaction_id")


class ResumptionFailure(Exception):
    """Stable receipt/checkpoint failure with a public error code."""

    def __init__(self, code: ErrorCode, message: str) -> None:
        self.code = code
        super().__init__(message)
