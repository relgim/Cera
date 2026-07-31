"""Deterministic intake, preflight, and state-delta contracts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar

from cera.contracts import ErrorEnvelope, SourceUnitClassification, TurnRequest
from cera.evidence import EvidenceSnapshot, EvidenceWorldMode
from cera.errors import ContractValidationError
from cera.ids import AUTHORITY_RECORD_ID_KINDS, IdKind, TypedId, require_kind
from cera.schema import require_schema
from cera.storage import SourceRecord


class TurnRoute(StrEnum):
    ORDINARY = "ordinary"
    CONSENT_VALID_ADULT = "consent_valid_adult"


class RequestedContentClass(StrEnum):
    ORDINARY = "ordinary"
    ADULT = "adult"


class AdultIdentityStatus(StrEnum):
    CONFIRMED_ADULT = "confirmed_adult"
    NOT_ELIGIBLE = "not_eligible"
    UNKNOWN = "unknown"


class AdultConsentStatus(StrEnum):
    GRANTED = "granted"
    REFUSED = "refused"
    WITHDRAWN = "withdrawn"
    ABSENT = "absent"
    UNKNOWN = "unknown"


class AdultCapacityStatus(StrEnum):
    CLEAR = "clear"
    IMPAIRED = "impaired"
    INCAPACITATED = "incapacitated"
    UNKNOWN = "unknown"


class AdultPressureStatus(StrEnum):
    NONE = "none"
    PRESENT = "present"
    DISQUALIFYING = "disqualifying"
    UNKNOWN = "unknown"


class AdultFreedomToStop(StrEnum):
    PRESENT = "present"
    LIMITED = "limited"
    ABSENT = "absent"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class ParticipantAdultAuthority:
    participant_id: TypedId
    identity_status: AdultIdentityStatus
    consent_status: AdultConsentStatus
    capacity_status: AdultCapacityStatus
    pressure_status: AdultPressureStatus
    freedom_to_stop: AdultFreedomToStop
    evidence_ids: tuple[TypedId, ...]

    def __post_init__(self) -> None:
        require_kind(self.participant_id, IdKind.CHARACTER, "participant_id")
        for evidence_id in self.evidence_ids:
            require_kind(evidence_id, IdKind.EVIDENCE, "evidence_ids")
        _unique((str(value) for value in self.evidence_ids), "adult evidence IDs")

    @property
    def consent_valid(self) -> bool:
        return (
            self.identity_status is AdultIdentityStatus.CONFIRMED_ADULT
            and self.consent_status is AdultConsentStatus.GRANTED
            and self.capacity_status is AdultCapacityStatus.CLEAR
            and self.pressure_status is AdultPressureStatus.NONE
            and self.freedom_to_stop is AdultFreedomToStop.PRESENT
            and bool(self.evidence_ids)
        )


@dataclass(frozen=True, slots=True)
class IntakeSourceUnit:
    classification: SourceUnitClassification
    text: str

    def __post_init__(self) -> None:
        _non_empty(self.text, "source unit text")


@dataclass(frozen=True, slots=True)
class PreflightAuthority:
    requested_content_class: RequestedContentClass
    adult_participants: tuple[ParticipantAdultAuthority, ...] = ()
    blocked_nonconsensual_crossing_established: bool = False
    blocker_boundary_unit_index: int | None = None
    facts_established_before_blocker: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _unique(
            (str(value.participant_id) for value in self.adult_participants),
            "adult participants",
        )
        if self.blocked_nonconsensual_crossing_established:
            if self.blocker_boundary_unit_index is None or self.blocker_boundary_unit_index < 0:
                raise ContractValidationError("blocked crossing requires a boundary unit index")
        elif self.blocker_boundary_unit_index is not None:
            raise ContractValidationError("blocker boundary requires an established crossing")


@dataclass(frozen=True, slots=True)
class TurnIntakeCommand:
    world_id: TypedId
    request_id: TypedId
    session_id: TypedId
    branch_id: TypedId
    expected_generation: int
    expected_parent_artifact_id: TypedId | None
    genesis_revision_id: TypedId
    protected_user_id: TypedId
    present_character_ids: tuple[TypedId, ...]
    requested_responding_npc_ids: tuple[TypedId, ...]
    source_units: tuple[IntakeSourceUnit, ...]
    requested_route_hints: tuple[str, ...]
    idempotency_key: str
    preflight_authority: PreflightAuthority
    world_mode: EvidenceWorldMode

    def __post_init__(self) -> None:
        require_kind(self.world_id, IdKind.WORLD, "world_id")
        require_kind(self.request_id, IdKind.REQUEST, "request_id")
        require_kind(self.session_id, IdKind.SESSION, "session_id")
        require_kind(self.branch_id, IdKind.BRANCH, "branch_id")
        if self.expected_generation < 0:
            raise ContractValidationError("expected_generation cannot be negative")
        if self.expected_parent_artifact_id is not None:
            require_kind(
                self.expected_parent_artifact_id,
                IdKind.ARTIFACT,
                "expected_parent_artifact_id",
            )
        require_kind(
            self.genesis_revision_id,
            IdKind.GENESIS_REVISION,
            "genesis_revision_id",
        )
        require_kind(self.protected_user_id, IdKind.CHARACTER, "protected_user_id")
        for character_id in (*self.present_character_ids, *self.requested_responding_npc_ids):
            require_kind(character_id, IdKind.CHARACTER, "character IDs")
        if not self.source_units:
            raise ContractValidationError("turn intake requires source units")
        _unique((str(value) for value in self.present_character_ids), "present characters")
        _unique(
            (str(value) for value in self.requested_responding_npc_ids),
            "requested responding NPCs",
        )
        _unique(self.requested_route_hints, "route hints")
        _non_empty(self.idempotency_key, "idempotency_key")
        if len(self.idempotency_key) > 200:
            raise ContractValidationError("idempotency_key must be at most 200 characters")


@dataclass(frozen=True, slots=True)
class PreparedTurn:
    SCHEMA_VERSION: ClassVar[str] = "cera.prepared_turn.v1"

    schema_version: str
    request: TurnRequest
    source_record: SourceRecord
    evidence_snapshot: EvidenceSnapshot
    route: TurnRoute
    present_character_ids: tuple[TypedId, ...]
    eligible_responding_npc_ids: tuple[TypedId, ...]
    adult_authority: tuple[ParticipantAdultAuthority, ...]
    lookup_receipt_ids: tuple[TypedId, ...]
    provider_calls_made: int
    story_state_committed: bool

    def __post_init__(self) -> None:
        require_schema(self.schema_version, self.SCHEMA_VERSION, type(self).__name__)
        if self.provider_calls_made != 0 or self.story_state_committed:
            raise ContractValidationError("prepared Phase 4 turn cannot call or commit")
        for receipt_id in self.lookup_receipt_ids:
            require_kind(receipt_id, IdKind.LOOKUP_RECEIPT, "lookup_receipt_ids")
        _unique((str(value) for value in self.lookup_receipt_ids), "lookup receipt IDs")


class StateMutationTarget(StrEnum):
    BRANCH_OVERLAY = "branch_overlay"
    GENESIS = "genesis"


class ProtectedUserProvenance(StrEnum):
    EXPLICIT_USER_SOURCE = "explicit_user_source"
    MODEL_INFERRED = "model_inferred"
    NOT_APPLICABLE = "not_applicable"


@dataclass(frozen=True, slots=True)
class ProposedStateRecord:
    record_id: TypedId
    branch_id: TypedId
    mutation_target: StateMutationTarget
    subject_ids: tuple[TypedId, ...]
    evidence_ids: tuple[TypedId, ...]
    protected_user_provenance: ProtectedUserProvenance

    def __post_init__(self) -> None:
        if self.record_id.kind not in AUTHORITY_RECORD_ID_KINDS:
            raise ContractValidationError("unsupported proposed state record kind")
        require_kind(self.branch_id, IdKind.BRANCH, "branch_id")
        if not self.subject_ids or not self.evidence_ids:
            raise ContractValidationError("state proposal requires subjects and evidence")
        for evidence_id in self.evidence_ids:
            require_kind(evidence_id, IdKind.EVIDENCE, "evidence_ids")
        _unique((str(value) for value in self.subject_ids), "state subjects")
        _unique((str(value) for value in self.evidence_ids), "state evidence")


@dataclass(frozen=True, slots=True)
class StateDeltaValidationReceipt:
    validation_receipt_id: TypedId
    request_id: TypedId
    branch_id: TypedId
    accepted_record_ids: tuple[TypedId, ...]
    status: str
    story_state_committed: bool

    def __post_init__(self) -> None:
        require_kind(
            self.validation_receipt_id,
            IdKind.VALIDATION,
            "validation_receipt_id",
        )
        require_kind(self.request_id, IdKind.REQUEST, "request_id")
        require_kind(self.branch_id, IdKind.BRANCH, "branch_id")
        if self.status != "validated_advisory_only" or self.story_state_committed:
            raise ContractValidationError("state-delta validation cannot commit")


class TurnKernelFailure(Exception):
    def __init__(self, envelope: ErrorEnvelope) -> None:
        self.envelope = envelope
        super().__init__(envelope.message)


def _non_empty(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ContractValidationError(f"{field_name} must be non-empty")


def _unique(values, field_name: str) -> None:
    items = list(values)
    if len(items) != len(set(items)):
        raise ContractValidationError(f"{field_name} must not contain duplicates")
