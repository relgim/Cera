"""Blocked-turn, external receipt, and resumption contracts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import json
from typing import ClassVar

from cera.errors import ContractValidationError, ErrorCode
from cera.ids import IdKind, TypedId
from cera.schema import require_schema
from cera.serialization import canonical_json, domain_sha256, text_sha256, to_primitive

from ._validation import kind, non_empty, sha256, unique_ids
from .reasoning import BeatState, DecisionRoute, SceneDecision


class ConsentStatus(StrEnum):
    REFUSED = "refused"
    WITHDRAWN = "withdrawn"
    ABSENT = "absent"
    UNKNOWN = "unknown"


class CapacityStatus(StrEnum):
    CLEAR = "clear"
    IMPAIRED = "impaired"
    INCAPACITATED = "incapacitated"
    UNKNOWN = "unknown"


class PressureStatus(StrEnum):
    PRESENT = "present"
    DISQUALIFYING = "disqualifying"
    UNKNOWN = "unknown"


class FreedomToStop(StrEnum):
    LIMITED = "limited"
    ABSENT = "absent"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class ConsentCapacityState:
    consent: ConsentStatus
    capacity: CapacityStatus
    pressure: PressureStatus
    freedom_to_stop: FreedomToStop


@dataclass(frozen=True, slots=True)
class BlockedTurnCheckpoint:
    SCHEMA_VERSION: ClassVar[str] = "cera.blocked_turn_checkpoint.v1"

    schema_version: str
    checkpoint_id: TypedId
    world_id: TypedId
    genesis_revision_id: TypedId
    protected_user_id: TypedId
    request_id: TypedId
    source_sha256: str
    branch_id: TypedId
    generation_id: TypedId
    starting_artifact_id: TypedId | None
    starting_artifact_sha256: str | None
    classification: str
    boundary_source_unit_id: TypedId
    facts_established_through_boundary: tuple[str, ...]
    actual_consent_capacity: ConsentCapacityState
    story_state_committed: bool
    checkpoint_sha256: str

    @classmethod
    def create(cls, **values) -> "BlockedTurnCheckpoint":
        payload = to_primitive(values)
        values["checkpoint_sha256"] = domain_sha256(
            "cera.blocked_turn_checkpoint.integrity.v1", payload
        )
        return cls(**values)

    def __post_init__(self) -> None:
        require_schema(self.schema_version, self.SCHEMA_VERSION, type(self).__name__)
        kind(self.checkpoint_id, IdKind.CHECKPOINT, "checkpoint_id")
        kind(self.world_id, IdKind.WORLD, "world_id")
        kind(self.genesis_revision_id, IdKind.GENESIS_REVISION, "genesis_revision_id")
        kind(self.protected_user_id, IdKind.CHARACTER, "protected_user_id")
        kind(self.request_id, IdKind.REQUEST, "request_id")
        kind(self.branch_id, IdKind.BRANCH, "branch_id")
        kind(self.generation_id, IdKind.GENERATION, "generation_id")
        if (self.starting_artifact_id is None) != (self.starting_artifact_sha256 is None):
            raise ContractValidationError("starting artifact ID and hash must be supplied together")
        if self.starting_artifact_id is not None:
            kind(self.starting_artifact_id, IdKind.ARTIFACT, "starting_artifact_id")
        kind(self.boundary_source_unit_id, IdKind.SOURCE_UNIT, "boundary_source_unit_id")
        sha256(self.source_sha256, "source_sha256")
        if self.starting_artifact_sha256 is not None:
            sha256(self.starting_artifact_sha256, "starting_artifact_sha256")
        sha256(self.checkpoint_sha256, "checkpoint_sha256")
        if self.classification != "blocked_nonconsensual_event":
            raise ValueError("checkpoint classification must be blocked_nonconsensual_event")
        if self.story_state_committed:
            raise ValueError("blocked checkpoint cannot claim committed story state")
        if self.checkpoint_sha256 != self.calculated_sha256:
            raise ContractValidationError("checkpoint_sha256 does not match checkpoint content")

    @property
    def calculated_sha256(self) -> str:
        payload = to_primitive(self)
        payload.pop("checkpoint_sha256")
        return domain_sha256("cera.blocked_turn_checkpoint.integrity.v1", payload)


@dataclass(frozen=True, slots=True)
class RejectedTurnReceipt:
    SCHEMA_VERSION: ClassVar[str] = "cera.rejected_turn_receipt.v1"

    schema_version: str
    rejection_id: TypedId
    request_id: TypedId
    checkpoint_id: TypedId
    error_code: ErrorCode
    story_state_committed: bool
    provider_calls_after_boundary: int

    def __post_init__(self) -> None:
        require_schema(self.schema_version, self.SCHEMA_VERSION, type(self).__name__)
        kind(self.rejection_id, IdKind.REJECTION, "rejection_id")
        kind(self.request_id, IdKind.REQUEST, "request_id")
        kind(self.checkpoint_id, IdKind.CHECKPOINT, "checkpoint_id")
        if self.error_code is not ErrorCode.BLOCKED_NONCONSENSUAL_EVENT:
            raise ValueError("rejected turn requires blocked-event error code")
        if self.story_state_committed or self.provider_calls_after_boundary != 0:
            raise ValueError("blocked rejection must have zero commit and zero provider calls")


class ProjectionConfidence(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(frozen=True, slots=True)
class DerivedInterpretation:
    owner_id: TypedId
    hypothesis: str
    evidence_ids: tuple[TypedId, ...]
    confidence: ProjectionConfidence

    def __post_init__(self) -> None:
        kind(self.owner_id, IdKind.CHARACTER, "owner_id")
        non_empty(self.hypothesis, "hypothesis")
        for evidence_id in self.evidence_ids:
            kind(evidence_id, IdKind.EVIDENCE, "evidence_ids")
        unique_ids(self.evidence_ids, "evidence_ids")


@dataclass(frozen=True, slots=True)
class TemporaryAftermathProjection:
    """Derived, non-authoritative, regenerable scratch projection."""
    SCHEMA_VERSION: ClassVar[str] = "cera.temporary_aftermath_projection.v1"

    schema_version: str
    projection_id: TypedId
    checkpoint_id: TypedId
    branch_id: TypedId
    status: str
    established_facts: tuple[str, ...]
    character_knowledge: tuple[str, ...]
    derived_interpretations: tuple[DerivedInterpretation, ...]
    uncertainties: tuple[str, ...]
    prohibited_assumptions: tuple[str, ...]
    retrieval_visibility: str
    expires_at: str
    delete_after_reconciliation: bool

    def __post_init__(self) -> None:
        require_schema(self.schema_version, self.SCHEMA_VERSION, type(self).__name__)
        kind(self.projection_id, IdKind.PROJECTION, "projection_id")
        kind(self.checkpoint_id, IdKind.CHECKPOINT, "checkpoint_id")
        kind(self.branch_id, IdKind.BRANCH, "branch_id")
        if self.status != "noncanonical_hypothesis":
            raise ValueError("temporary projection must remain noncanonical")
        if self.retrieval_visibility != "scratch_only":
            raise ValueError("temporary projection must be scratch_only")
        if not self.delete_after_reconciliation:
            raise ValueError("temporary projection must be deleted after reconciliation")
        non_empty(self.expires_at, "expires_at")

    @property
    def projection_sha256(self) -> str:
        return domain_sha256("cera.temporary_aftermath_projection.v1", self)


@dataclass(frozen=True, slots=True)
class ExternalEventRequest:
    """Provider-neutral correlation envelope; contains no generation prompt."""
    SCHEMA_VERSION: ClassVar[str] = "cera.external_event_request.v1"

    schema_version: str
    external_request_id: TypedId
    world_id: TypedId
    genesis_revision_id: TypedId
    protected_user_id: TypedId
    request_id: TypedId
    source_sha256: str
    branch_id: TypedId
    generation_id: TypedId
    starting_artifact_id: TypedId | None
    starting_artifact_sha256: str | None
    checkpoint_id: TypedId
    checkpoint_sha256: str
    required_receipt_schema: str
    allowed_event_registry_version: str
    expires_at: str
    callback_correlation_token: str

    def __post_init__(self) -> None:
        require_schema(self.schema_version, self.SCHEMA_VERSION, type(self).__name__)
        kind(self.external_request_id, IdKind.EXTERNAL_REQUEST, "external_request_id")
        kind(self.world_id, IdKind.WORLD, "world_id")
        kind(self.genesis_revision_id, IdKind.GENESIS_REVISION, "genesis_revision_id")
        kind(self.protected_user_id, IdKind.CHARACTER, "protected_user_id")
        kind(self.request_id, IdKind.REQUEST, "request_id")
        kind(self.branch_id, IdKind.BRANCH, "branch_id")
        kind(self.generation_id, IdKind.GENERATION, "generation_id")
        if (self.starting_artifact_id is None) != (self.starting_artifact_sha256 is None):
            raise ContractValidationError("starting artifact ID and hash must be supplied together")
        if self.starting_artifact_id is not None:
            kind(self.starting_artifact_id, IdKind.ARTIFACT, "starting_artifact_id")
        kind(self.checkpoint_id, IdKind.CHECKPOINT, "checkpoint_id")
        sha256(self.source_sha256, "source_sha256")
        if self.starting_artifact_sha256 is not None:
            sha256(self.starting_artifact_sha256, "starting_artifact_sha256")
        sha256(self.checkpoint_sha256, "checkpoint_sha256")
        if self.required_receipt_schema != ExternalCompletionReceipt.SCHEMA_VERSION:
            raise ValueError("required_receipt_schema is unsupported")
        non_empty(self.allowed_event_registry_version, "allowed_event_registry_version")
        non_empty(self.expires_at, "expires_at")
        non_empty(self.callback_correlation_token, "callback_correlation_token")

    @property
    def external_request_sha256(self) -> str:
        return domain_sha256("cera.external_event_request.v1", self)


class ObservedResponse(StrEnum):
    NONE_OBSERVED = "none_observed"
    RESPONSE_OBSERVED = "response_observed"
    UNKNOWN = "unknown"


class InjuryStatus(StrEnum):
    NONE_ESTABLISHED = "none_established"
    POSSIBLE = "possible"
    ESTABLISHED = "established"
    UNKNOWN = "unknown"


class ExposureStatus(StrEnum):
    NOT_APPLICABLE = "not_applicable"
    NONE_ESTABLISHED = "none_established"
    POSSIBLE = "possible"
    ESTABLISHED = "established"
    UNKNOWN = "unknown"


class ConceptionStatus(StrEnum):
    NOT_APPLICABLE = "not_applicable"
    NOT_ESTABLISHED = "not_established"
    ESTABLISHED = "established"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class ExternalEventStep:
    event_step_id: TypedId
    classification: str
    actor_ids: tuple[TypedId, ...]
    target_ids: tuple[TypedId, ...]
    state: BeatState
    consent_status: ConsentStatus
    resistance_or_freeze: tuple[str, ...]
    calls_for_help: tuple[str, ...]
    observed_response: ObservedResponse
    defensive_actions: tuple[str, ...]
    material_changes: tuple[str, ...]
    injury_status: InjuryStatus
    reproductive_exposure: ExposureStatus
    conception_status: ConceptionStatus
    knowledge_owners: tuple[TypedId, ...]

    def __post_init__(self) -> None:
        kind(self.event_step_id, IdKind.EXTERNAL_STEP, "event_step_id")
        non_empty(self.classification, "classification")
        for value in (*self.actor_ids, *self.target_ids):
            if value.kind not in (IdKind.CHARACTER, IdKind.MATERIAL):
                raise ValueError("event participant IDs must be character or material IDs")
        for value in self.knowledge_owners:
            kind(value, IdKind.CHARACTER, "knowledge_owners")
        unique_ids(self.actor_ids, "actor_ids")
        unique_ids(self.target_ids, "target_ids")
        unique_ids(self.knowledge_owners, "knowledge_owners")


class EventEndState(StrEnum):
    ENDED = "ended"
    INTERRUPTED = "interrupted"
    ESCAPED = "escaped"
    UNKNOWN = "unknown"


class CurrentSafety(StrEnum):
    SAFE = "safe"
    UNSAFE = "unsafe"
    UNCERTAIN = "uncertain"


@dataclass(frozen=True, slots=True)
class EventEnd:
    state: EventEndState
    current_safety: CurrentSafety


@dataclass(frozen=True, slots=True)
class ExternalCompletionReceipt:
    """Provider-neutral non-graphic claim awaiting validation and commit."""
    SCHEMA_VERSION: ClassVar[str] = "cera.external_completion_receipt.v1"

    schema_version: str
    receipt_id: TypedId
    idempotency_key: str
    external_request_id: TypedId
    allowed_event_registry_version: str
    world_id: TypedId
    genesis_revision_id: TypedId
    protected_user_id: TypedId
    request_id: TypedId
    source_sha256: str
    branch_id: TypedId
    generation_id: TypedId
    starting_artifact_id: TypedId | None
    starting_artifact_sha256: str | None
    checkpoint_id: TypedId
    checkpoint_sha256: str
    ordered_events: tuple[ExternalEventStep, ...]
    event_end: EventEnd
    immediate_aftermath_facts: tuple[str, ...]
    receipt_sha256: str

    @classmethod
    def create(cls, **values) -> "ExternalCompletionReceipt":
        payload = to_primitive(values)
        values["receipt_sha256"] = domain_sha256(
            "cera.external_completion_receipt.integrity.v1", payload
        )
        return cls(**values)

    def __post_init__(self) -> None:
        require_schema(self.schema_version, self.SCHEMA_VERSION, type(self).__name__)
        kind(self.receipt_id, IdKind.EXTERNAL_RECEIPT, "receipt_id")
        kind(self.external_request_id, IdKind.EXTERNAL_REQUEST, "external_request_id")
        kind(self.world_id, IdKind.WORLD, "world_id")
        kind(self.genesis_revision_id, IdKind.GENESIS_REVISION, "genesis_revision_id")
        kind(self.protected_user_id, IdKind.CHARACTER, "protected_user_id")
        kind(self.request_id, IdKind.REQUEST, "request_id")
        kind(self.branch_id, IdKind.BRANCH, "branch_id")
        kind(self.generation_id, IdKind.GENERATION, "generation_id")
        if (self.starting_artifact_id is None) != (self.starting_artifact_sha256 is None):
            raise ContractValidationError("starting artifact ID and hash must be supplied together")
        if self.starting_artifact_id is not None:
            kind(self.starting_artifact_id, IdKind.ARTIFACT, "starting_artifact_id")
        kind(self.checkpoint_id, IdKind.CHECKPOINT, "checkpoint_id")
        non_empty(self.idempotency_key, "idempotency_key")
        non_empty(self.allowed_event_registry_version, "allowed_event_registry_version")
        sha256(self.source_sha256, "source_sha256")
        if self.starting_artifact_sha256 is not None:
            sha256(self.starting_artifact_sha256, "starting_artifact_sha256")
        sha256(self.checkpoint_sha256, "checkpoint_sha256")
        sha256(self.receipt_sha256, "receipt_sha256")
        if not self.ordered_events:
            raise ValueError("ordered_events must not be empty")
        unique_ids((event.event_step_id for event in self.ordered_events), "ordered_events")
        if self.receipt_sha256 != self.calculated_sha256:
            raise ContractValidationError("receipt_sha256 does not match receipt content")

    @property
    def calculated_sha256(self) -> str:
        payload = to_primitive(self)
        payload.pop("receipt_sha256")
        return domain_sha256("cera.external_completion_receipt.integrity.v1", payload)


class ProjectionReconciliationStatus(StrEnum):
    CONFIRMED = "confirmed"
    CONTRADICTED = "contradicted"
    UNKNOWN = "unknown"
    DISCARDED = "discarded"


@dataclass(frozen=True, slots=True)
class ProjectionReconciliation:
    projection_item: str
    status: ProjectionReconciliationStatus
    receipt_step_ids: tuple[TypedId, ...]

    def __post_init__(self) -> None:
        non_empty(self.projection_item, "projection_item")
        for step_id in self.receipt_step_ids:
            kind(step_id, IdKind.EXTERNAL_STEP, "receipt_step_ids")
        unique_ids(self.receipt_step_ids, "receipt_step_ids")


class AftermathRecordType(StrEnum):
    OBJECTIVE_EVENT = "objective_event"
    CHARACTER_MEMORY = "character_memory"
    RELATIONSHIP_EVIDENCE = "relationship_evidence"
    MATERIAL_STATE = "material_state"
    DEVELOPMENT_OVERLAY = "development_overlay"


_AFTERMATH_RECORD_KINDS = {
    AftermathRecordType.OBJECTIVE_EVENT: IdKind.EVENT,
    AftermathRecordType.CHARACTER_MEMORY: IdKind.MEMORY,
    AftermathRecordType.RELATIONSHIP_EVIDENCE: IdKind.RELATIONSHIP,
    AftermathRecordType.MATERIAL_STATE: IdKind.MATERIAL,
    AftermathRecordType.DEVELOPMENT_OVERLAY: IdKind.DEVELOPMENT,
}


@dataclass(frozen=True, slots=True)
class AftermathAuthorityCandidate:
    record_id: TypedId
    record_type: AftermathRecordType
    payload_json: str
    payload_sha256: str
    supersedes: tuple[TypedId, ...] = ()

    def __post_init__(self) -> None:
        kind(self.record_id, _AFTERMATH_RECORD_KINDS[self.record_type], "record_id")
        non_empty(self.payload_json, "payload_json")
        try:
            decoded = json.loads(self.payload_json)
        except json.JSONDecodeError as exc:
            raise ContractValidationError("aftermath candidate payload is invalid JSON") from exc
        if canonical_json(decoded) != self.payload_json:
            raise ContractValidationError("aftermath candidate payload must be canonical JSON")
        sha256(self.payload_sha256, "payload_sha256")
        if text_sha256(self.payload_json) != self.payload_sha256:
            raise ContractValidationError("aftermath candidate payload hash mismatch")
        unique_ids(self.supersedes, "supersedes")


@dataclass(frozen=True, slots=True)
class AftermathDecision:
    """Non-graphic reasoner proposal bound to one validated receipt."""

    SCHEMA_VERSION: ClassVar[str] = "cera.aftermath_decision.v1"

    schema_version: str
    receipt_id: TypedId
    receipt_sha256: str
    checkpoint_id: TypedId
    checkpoint_sha256: str
    projection_id: TypedId | None
    projection_sha256: str | None
    scene_decision: SceneDecision
    projection_reconciliation: tuple[ProjectionReconciliation, ...]
    authority_candidates: tuple[AftermathAuthorityCandidate, ...]
    uncertainties: tuple[str, ...]
    composer_must_preserve: tuple[str, ...]
    contains_graphic_detail: bool
    protected_user_action_authored: bool

    def __post_init__(self) -> None:
        require_schema(self.schema_version, self.SCHEMA_VERSION, type(self).__name__)
        kind(self.receipt_id, IdKind.EXTERNAL_RECEIPT, "receipt_id")
        kind(self.checkpoint_id, IdKind.CHECKPOINT, "checkpoint_id")
        sha256(self.receipt_sha256, "receipt_sha256")
        sha256(self.checkpoint_sha256, "checkpoint_sha256")
        if (self.projection_id is None) != (self.projection_sha256 is None):
            raise ContractValidationError("projection ID and hash must be supplied together")
        if self.projection_id is not None:
            kind(self.projection_id, IdKind.PROJECTION, "projection_id")
            assert self.projection_sha256 is not None
            sha256(self.projection_sha256, "projection_sha256")
        if self.scene_decision.route is not DecisionRoute.AFTERMATH:
            raise ContractValidationError("aftermath decision requires aftermath scene route")
        unique_ids(
            (candidate.record_id for candidate in self.authority_candidates),
            "authority_candidates",
        )
        event_count = sum(
            candidate.record_type is AftermathRecordType.OBJECTIVE_EVENT
            for candidate in self.authority_candidates
        )
        if event_count != 1:
            raise ContractValidationError("aftermath decision requires one objective event candidate")
        if self.contains_graphic_detail or self.protected_user_action_authored:
            raise ContractValidationError(
                "aftermath decision must be non-graphic and cannot author the protected user"
            )

    @property
    def decision_sha256(self) -> str:
        return domain_sha256("cera.aftermath_decision.v1", self)


@dataclass(frozen=True, slots=True)
class ExternalReceiptValidationReceipt:
    SCHEMA_VERSION: ClassVar[str] = "cera.external_receipt_validation.v1"

    schema_version: str
    validation_receipt_id: TypedId
    external_request_id: TypedId
    receipt_id: TypedId
    receipt_sha256: str
    checkpoint_id: TypedId
    checkpoint_sha256: str
    status: str
    validated_fields: tuple[str, ...]
    story_state_committed: bool

    def __post_init__(self) -> None:
        require_schema(self.schema_version, self.SCHEMA_VERSION, type(self).__name__)
        kind(self.validation_receipt_id, IdKind.VALIDATION, "validation_receipt_id")
        kind(self.external_request_id, IdKind.EXTERNAL_REQUEST, "external_request_id")
        kind(self.receipt_id, IdKind.EXTERNAL_RECEIPT, "receipt_id")
        kind(self.checkpoint_id, IdKind.CHECKPOINT, "checkpoint_id")
        sha256(self.receipt_sha256, "receipt_sha256")
        sha256(self.checkpoint_sha256, "checkpoint_sha256")
        if self.status != "validated_pending_commit" or self.story_state_committed:
            raise ContractValidationError("receipt validation is advisory until atomic commit")
        if not self.validated_fields:
            raise ContractValidationError("receipt validation must name validated bindings")
