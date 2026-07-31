"""Consent-valid adult-route authority, context, synchronization, and mechanics contracts.

These records are operational and non-prose-bearing. They do not create a
second character-psychology authority and cannot commit story truth.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar

from cera.contracts import BeatState
from cera.composer import ComposerSourcePacket
from cera.errors import ContractValidationError
from cera.ids import IdKind, TypedId, require_kind
from cera.kernel import (
    AdultCapacityStatus,
    AdultConsentStatus,
    AdultFreedomToStop,
    AdultIdentityStatus,
    AdultPressureStatus,
    PreparedTurn,
)
from cera.reasoner import ReasonerOutcome, ReasonerSourceView, SceneReasonerReceipt
from cera.schema import require_schema
from cera.serialization import domain_sha256, re_is_sha256


class AdultScenarioKind(StrEnum):
    CONSENSUAL_ACTIVITY = "consensual_activity"
    CONSENSUAL_ROLEPLAY = "consensual_roleplay"
    UNCERTAIN_OR_ABSENT_CONSENT = "uncertain_or_absent_consent"
    WITHDRAWN_CONSENT = "withdrawn_consent"
    ACTUAL_NONCONSENSUAL_CONDUCT = "actual_nonconsensual_conduct"


class ProviderCapabilityStatus(StrEnum):
    NOT_EVALUATED = "not_evaluated"
    QUALIFIED = "qualified"
    NOT_QUALIFIED = "not_qualified"


class SceneEligibilityStatus(StrEnum):
    ELIGIBLE_AND_PRESENT = "eligible_and_present"
    ABSENT = "absent"
    INELIGIBLE = "ineligible"


class AdultSemanticDimension(StrEnum):
    DESIRE = "desire"
    ATTRACTION = "attraction"
    PLEASURE = "pleasure"
    BODILY_RESPONSE = "bodily_response"
    PREFERENCE = "preference"
    EXPERIENCE = "experience"
    INTENTION = "intention"
    RELATIONSHIP_MEANING = "relationship_meaning"


class SemanticAssertionState(StrEnum):
    UNKNOWN = "unknown"
    EXPLICITLY_ESTABLISHED = "explicitly_established"
    EXPLICITLY_NEGATED = "explicitly_negated"


class SemanticAssertionProvenance(StrEnum):
    NOT_ESTABLISHED = "not_established"
    CURRENT_CREATOR_SOURCE = "current_creator_source"
    ACCEPTED_CURRENT_SCENE = "accepted_current_scene"


class AdultObservableCode(StrEnum):
    SOURCE_AUTHORED_PROTECTED_EVENT = "source_authored_protected_event"
    SOURCE_AUTHORED_BODY_STATE = "source_authored_body_state"
    SOURCE_AUTHORED_PRIVATE_STATE = "source_authored_private_state"
    CONSENSUAL_ROLEPLAY_SIGNAL = "consensual_roleplay_signal"


class AdultContentFamily(StrEnum):
    GENERAL_INTIMACY = "general_intimacy"
    ROLEPLAY = "roleplay"
    BODY_RESPONSE_CONTINUITY = "body_response_continuity"
    FLUID_CONTINUITY = "fluid_continuity"
    TOILET_CONTINUITY = "toilet_continuity"
    INFIDELITY_DRAMA = "infidelity_drama"


class ContentActivationAuthority(StrEnum):
    CURRENT_EXPLICIT_SOURCE = "current_explicit_source"
    CURRENT_OBJECTIVE_SCENE = "current_objective_scene"
    PRIVATE_SUSPICION = "private_suspicion"
    HISTORICAL_CONTEXT_ONLY = "historical_context_only"
    BODY_STATE_ONLY = "body_state_only"


@dataclass(frozen=True, slots=True)
class AdultSemanticAssertion:
    subject_id: TypedId
    dimension: AdultSemanticDimension
    state: SemanticAssertionState
    provenance: SemanticAssertionProvenance
    source_unit_ids: tuple[TypedId, ...]
    evidence_ids: tuple[TypedId, ...]

    def __post_init__(self) -> None:
        require_kind(self.subject_id, IdKind.CHARACTER, "subject_id")
        for source_unit_id in self.source_unit_ids:
            require_kind(source_unit_id, IdKind.SOURCE_UNIT, "source_unit_ids")
        for evidence_id in self.evidence_ids:
            require_kind(evidence_id, IdKind.EVIDENCE, "evidence_ids")
        _unique((str(value) for value in self.source_unit_ids), "assertion source units")
        _unique((str(value) for value in self.evidence_ids), "assertion evidence")
        if self.state is SemanticAssertionState.UNKNOWN:
            if self.provenance is not SemanticAssertionProvenance.NOT_ESTABLISHED:
                raise ContractValidationError("unknown semantic state cannot claim provenance")
            if self.source_unit_ids or self.evidence_ids:
                raise ContractValidationError("unknown semantic state cannot cite authority")
        else:
            if self.provenance is SemanticAssertionProvenance.NOT_ESTABLISHED:
                raise ContractValidationError("established semantic state requires provenance")
            if not (self.source_unit_ids or self.evidence_ids):
                raise ContractValidationError("established semantic state requires exact authority")


@dataclass(frozen=True, slots=True)
class AdultRouteSourceUnit:
    source_unit_id: TypedId
    source_unit_sha256: str
    ordinal: int
    progression_state: BeatState
    participant_ids: tuple[TypedId, ...]
    observable_code: AdultObservableCode
    private_state_owner_ids: tuple[TypedId, ...]
    consent_status: AdultConsentStatus
    capacity_status: AdultCapacityStatus
    pressure_status: AdultPressureStatus
    freedom_to_stop: AdultFreedomToStop

    def __post_init__(self) -> None:
        require_kind(self.source_unit_id, IdKind.SOURCE_UNIT, "source_unit_id")
        _sha256(self.source_unit_sha256, "source_unit_sha256")
        if self.ordinal < 0 or not self.participant_ids:
            raise ContractValidationError("adult source unit requires ordinal and participants")
        for value in (*self.participant_ids, *self.private_state_owner_ids):
            require_kind(value, IdKind.CHARACTER, "adult source participant IDs")
        _unique((str(value) for value in self.participant_ids), "adult source participants")
        _unique(
            (str(value) for value in self.private_state_owner_ids),
            "private-state owners",
        )
        if not set(self.private_state_owner_ids).issubset(self.participant_ids):
            raise ContractValidationError("private-state owner must be a source participant")


@dataclass(frozen=True, slots=True)
class AdultContentTrigger:
    family: AdultContentFamily
    authority: ContentActivationAuthority
    source_unit_id: TypedId | None
    evidence_id: TypedId | None

    def __post_init__(self) -> None:
        if self.source_unit_id is not None:
            require_kind(self.source_unit_id, IdKind.SOURCE_UNIT, "source_unit_id")
        if self.evidence_id is not None:
            require_kind(self.evidence_id, IdKind.EVIDENCE, "evidence_id")
        if self.authority is ContentActivationAuthority.CURRENT_EXPLICIT_SOURCE:
            if self.source_unit_id is None or self.evidence_id is not None:
                raise ContractValidationError("current-source trigger requires only a source unit")
        elif self.authority is ContentActivationAuthority.CURRENT_OBJECTIVE_SCENE:
            if self.evidence_id is None or self.source_unit_id is not None:
                raise ContractValidationError("current-scene trigger requires only evidence")


@dataclass(frozen=True, slots=True)
class AdultRouteInput:
    prepared_turn: PreparedTurn
    participant_ids: tuple[TypedId, ...]
    scenario_kind: AdultScenarioKind
    scenario_source_unit_ids: tuple[TypedId, ...]
    source_units: tuple[AdultRouteSourceUnit, ...]
    semantic_assertions: tuple[AdultSemanticAssertion, ...]
    content_triggers: tuple[AdultContentTrigger, ...]
    provider_capability: ProviderCapabilityStatus
    synthetic_fixture: bool

    def __post_init__(self) -> None:
        if not self.participant_ids or not self.source_units:
            raise ContractValidationError("adult route input requires participants and source units")
        for participant_id in self.participant_ids:
            require_kind(participant_id, IdKind.CHARACTER, "participant_ids")
        for source_unit_id in self.scenario_source_unit_ids:
            require_kind(source_unit_id, IdKind.SOURCE_UNIT, "scenario_source_unit_ids")
        _unique((str(value) for value in self.participant_ids), "adult participants")
        _unique(
            (str(value) for value in self.scenario_source_unit_ids),
            "scenario source units",
        )
        _unique(
            (f"{value.subject_id}|{value.dimension.value}" for value in self.semantic_assertions),
            "semantic assertion dimensions",
        )
        _unique(
            (f"{value.family.value}|{value.authority.value}" for value in self.content_triggers),
            "content triggers",
        )


@dataclass(frozen=True, slots=True)
class AdultParticipantDecision:
    participant_id: TypedId
    identity_status: AdultIdentityStatus
    scene_eligibility: SceneEligibilityStatus
    consent_status: AdultConsentStatus
    capacity_status: AdultCapacityStatus
    pressure_status: AdultPressureStatus
    freedom_to_stop: AdultFreedomToStop
    evidence_ids: tuple[TypedId, ...]

    def __post_init__(self) -> None:
        require_kind(self.participant_id, IdKind.CHARACTER, "participant_id")
        for evidence_id in self.evidence_ids:
            require_kind(evidence_id, IdKind.EVIDENCE, "evidence_ids")
        _unique((str(value) for value in self.evidence_ids), "participant evidence")


@dataclass(frozen=True, slots=True)
class AdultAuthorityDecision:
    SCHEMA_VERSION: ClassVar[str] = "cera.adult_authority_decision.v1"

    schema_version: str
    adult_authority_id: TypedId
    request_id: TypedId
    snapshot_token: TypedId
    scenario_kind: AdultScenarioKind
    participants: tuple[AdultParticipantDecision, ...]
    semantic_assertions: tuple[AdultSemanticAssertion, ...]
    source_units: tuple[AdultRouteSourceUnit, ...]
    provider_capability: ProviderCapabilityStatus
    route_permitted: bool
    authority: str
    truth_status: str
    synthetic_fixture: bool
    story_state_committed: bool

    def __post_init__(self) -> None:
        require_schema(self.schema_version, self.SCHEMA_VERSION, type(self).__name__)
        require_kind(self.adult_authority_id, IdKind.ADULT_AUTHORITY, "adult_authority_id")
        require_kind(self.request_id, IdKind.REQUEST, "request_id")
        require_kind(self.snapshot_token, IdKind.SNAPSHOT, "snapshot_token")
        if not self.route_permitted or self.story_state_committed:
            raise ContractValidationError("adult authority decision must be permitted and non-committing")
        if self.authority != "synthetic_fixture" or self.truth_status != "noncanonical":
            raise ContractValidationError("Phase 7 permits synthetic noncanonical authority only")
        if not self.synthetic_fixture:
            raise ContractValidationError("Phase 7 cannot create real adult authority")

    @property
    def authority_sha256(self) -> str:
        return domain_sha256("cera.adult_authority_decision.v1", self)


@dataclass(frozen=True, slots=True)
class AdultCausalLedgerUnit:
    source_unit_id: TypedId
    source_unit_sha256: str
    ordinal: int
    progression_state: BeatState
    participant_ids: tuple[TypedId, ...]
    observable_code: AdultObservableCode
    private_state_owner_ids: tuple[TypedId, ...]
    consent_status: AdultConsentStatus
    capacity_status: AdultCapacityStatus
    pressure_status: AdultPressureStatus
    freedom_to_stop: AdultFreedomToStop

    def __post_init__(self) -> None:
        require_kind(self.source_unit_id, IdKind.SOURCE_UNIT, "source_unit_id")
        _sha256(self.source_unit_sha256, "source_unit_sha256")
        if self.ordinal < 0 or not self.participant_ids:
            raise ContractValidationError("ledger unit requires ordinal and participants")
        for value in (*self.participant_ids, *self.private_state_owner_ids):
            require_kind(value, IdKind.CHARACTER, "ledger participant IDs")
        _unique((str(value) for value in self.participant_ids), "ledger participants")
        _unique((str(value) for value in self.private_state_owner_ids), "ledger private owners")
        if not set(self.private_state_owner_ids).issubset(self.participant_ids):
            raise ContractValidationError("ledger private owner must be a participant")


@dataclass(frozen=True, slots=True)
class AdultCausalLedger:
    SCHEMA_VERSION: ClassVar[str] = "cera.adult_causal_ledger.v1"

    schema_version: str
    adult_plan_id: TypedId
    request_id: TypedId
    snapshot_token: TypedId
    source_sha256: str
    authority_sha256: str
    scenario_kind: AdultScenarioKind
    units: tuple[AdultCausalLedgerUnit, ...]
    prohibited_inferences: tuple[str, ...]
    contains_exact_protected_prose: bool

    def __post_init__(self) -> None:
        require_schema(self.schema_version, self.SCHEMA_VERSION, type(self).__name__)
        require_kind(self.adult_plan_id, IdKind.ADULT_PLAN, "adult_plan_id")
        require_kind(self.request_id, IdKind.REQUEST, "request_id")
        require_kind(self.snapshot_token, IdKind.SNAPSHOT, "snapshot_token")
        _sha256(self.source_sha256, "source_sha256")
        _sha256(self.authority_sha256, "authority_sha256")
        if not self.units or self.contains_exact_protected_prose:
            raise ContractValidationError("safe adult ledger requires units and no exact prose")
        _unique((str(value.source_unit_id) for value in self.units), "ledger units")
        _unique(self.prohibited_inferences, "prohibited inferences")

    @property
    def ledger_sha256(self) -> str:
        return domain_sha256("cera.adult_causal_ledger.v1", self)


@dataclass(frozen=True, slots=True)
class AdultRouteContext:
    SCHEMA_VERSION: ClassVar[str] = "cera.adult_route_context.v1"

    schema_version: str
    context_id: TypedId
    request_id: TypedId
    snapshot_token: TypedId
    active: bool
    selected_families: tuple[AdultContentFamily, ...]
    craft_reference_ids: tuple[TypedId, ...]
    rejected_triggers: tuple[str, ...]
    profile_version: str
    actual_examples_imported: bool
    contains_exact_protected_prose: bool
    inherited_provider_conversation_state: bool

    def __post_init__(self) -> None:
        require_schema(self.schema_version, self.SCHEMA_VERSION, type(self).__name__)
        require_kind(self.context_id, IdKind.ADULT_CONTEXT, "context_id")
        require_kind(self.request_id, IdKind.REQUEST, "request_id")
        require_kind(self.snapshot_token, IdKind.SNAPSHOT, "snapshot_token")
        for reference_id in self.craft_reference_ids:
            require_kind(reference_id, IdKind.CRAFT_REFERENCE, "craft_reference_ids")
        _unique((value.value for value in self.selected_families), "selected families")
        _unique((str(value) for value in self.craft_reference_ids), "craft references")
        _unique(self.rejected_triggers, "rejected triggers")
        if self.actual_examples_imported or self.contains_exact_protected_prose:
            raise ContractValidationError("Phase 7 context cannot import examples or exact prose")
        if self.inherited_provider_conversation_state:
            raise ContractValidationError("adult context cannot inherit provider conversation state")
        if not self.active and (self.selected_families or self.craft_reference_ids):
            raise ContractValidationError("inactive adult context must be empty")

    @property
    def context_sha256(self) -> str:
        return domain_sha256("cera.adult_route_context.v1", self)


@dataclass(frozen=True, slots=True)
class AdultDualRepresentationReceipt:
    SCHEMA_VERSION: ClassVar[str] = "cera.adult_dual_representation_receipt.v1"

    schema_version: str
    validation_receipt_id: TypedId
    request_id: TypedId
    snapshot_token: TypedId
    authority_sha256: str
    safe_ledger_sha256: str
    safe_source_view_sha256: str
    exact_envelope_sha256: str
    synchronization_sha256: str
    status: str
    exact_protected_text_stored: bool
    external_provider_calls: int
    story_state_committed: bool

    def __post_init__(self) -> None:
        require_schema(self.schema_version, self.SCHEMA_VERSION, type(self).__name__)
        require_kind(self.validation_receipt_id, IdKind.VALIDATION, "validation_receipt_id")
        require_kind(self.request_id, IdKind.REQUEST, "request_id")
        require_kind(self.snapshot_token, IdKind.SNAPSHOT, "snapshot_token")
        for value in (
            self.authority_sha256,
            self.safe_ledger_sha256,
            self.safe_source_view_sha256,
            self.exact_envelope_sha256,
            self.synchronization_sha256,
        ):
            _sha256(value, "dual-representation receipt hash")
        if self.status != "synchronized" or self.exact_protected_text_stored:
            raise ContractValidationError("dual-representation receipt cannot store protected text")
        if self.external_provider_calls != 0 or self.story_state_committed:
            raise ContractValidationError("Phase 7 synchronization cannot call or commit")


@dataclass(frozen=True, slots=True)
class AdultRoutePreparation:
    authority: AdultAuthorityDecision
    ledger: AdultCausalLedger
    context: AdultRouteContext
    reasoner_source_view: ReasonerSourceView
    composer_source_packet: ComposerSourcePacket
    receipt: AdultDualRepresentationReceipt


@dataclass(frozen=True, slots=True)
class AdultUnitTreatment:
    source_unit_id: TypedId
    progression_state: BeatState
    participant_ids: tuple[TypedId, ...]
    craft_reference_ids: tuple[TypedId, ...]
    preserve_source_outcome: bool
    no_adjacent_psychology_inference: bool

    def __post_init__(self) -> None:
        require_kind(self.source_unit_id, IdKind.SOURCE_UNIT, "source_unit_id")
        for value in self.participant_ids:
            require_kind(value, IdKind.CHARACTER, "participant_ids")
        for value in self.craft_reference_ids:
            require_kind(value, IdKind.CRAFT_REFERENCE, "craft_reference_ids")
        _unique((str(value) for value in self.participant_ids), "treatment participants")
        _unique((str(value) for value in self.craft_reference_ids), "treatment craft references")
        if not self.preserve_source_outcome or not self.no_adjacent_psychology_inference:
            raise ContractValidationError("adult treatment must preserve source and psychology boundary")


@dataclass(frozen=True, slots=True)
class AdultMechanicsRequest:
    SCHEMA_VERSION: ClassVar[str] = "cera.adult_mechanics_request.v1"

    schema_version: str
    prepared_turn: PreparedTurn
    adult_authority_id: TypedId
    authority_sha256: str
    ledger_sha256: str
    context_sha256: str
    reasoner_outcome: ReasonerOutcome
    reasoner_receipt: SceneReasonerReceipt
    unit_ids: tuple[TypedId, ...]
    hard_boundaries: tuple[str, ...]

    def __post_init__(self) -> None:
        from cera.reasoner import ReasonerOutcomeStatus

        require_schema(self.schema_version, self.SCHEMA_VERSION, type(self).__name__)
        require_kind(self.adult_authority_id, IdKind.ADULT_AUTHORITY, "adult_authority_id")
        if self.reasoner_receipt.snapshot_token != self.prepared_turn.evidence_snapshot.snapshot_token:
            raise ContractValidationError("adult mechanics snapshot mismatch")
        for value in (self.authority_sha256, self.ledger_sha256, self.context_sha256):
            _sha256(value, "adult mechanics request hash")
        if self.reasoner_outcome.status is not ReasonerOutcomeStatus.DECISION_READY:
            raise ContractValidationError("adult mechanics require validated reasoner decision")
        if self.reasoner_receipt.outcome_sha256 != self.reasoner_outcome.outcome_sha256:
            raise ContractValidationError("adult mechanics reasoner receipt mismatch")
        if not self.unit_ids or not self.hard_boundaries:
            raise ContractValidationError("adult mechanics request requires units and boundaries")
        for unit_id in self.unit_ids:
            require_kind(unit_id, IdKind.SOURCE_UNIT, "unit_ids")
        _unique((str(value) for value in self.unit_ids), "adult mechanics units")

    @property
    def request_sha256(self) -> str:
        return domain_sha256("cera.adult_mechanics_request.v1", self)


@dataclass(frozen=True, slots=True)
class AdultMechanicsProposal:
    SCHEMA_VERSION: ClassVar[str] = "cera.adult_mechanics_proposal.v1"

    schema_version: str
    adult_plan_id: TypedId
    authority_sha256: str
    ledger_sha256: str
    context_sha256: str
    decision_sha256: str
    sequence_plan_sha256: str
    unit_treatments: tuple[AdultUnitTreatment, ...]
    no_psychology_override: bool
    contains_story_prose: bool
    advisory_metadata: tuple[object, ...]

    def __post_init__(self) -> None:
        require_schema(self.schema_version, self.SCHEMA_VERSION, type(self).__name__)
        require_kind(self.adult_plan_id, IdKind.ADULT_PLAN, "adult_plan_id")
        for value in (
            self.authority_sha256,
            self.ledger_sha256,
            self.context_sha256,
            self.decision_sha256,
            self.sequence_plan_sha256,
        ):
            _sha256(value, "adult mechanics proposal hash")
        if not self.unit_treatments or not self.no_psychology_override:
            raise ContractValidationError("adult mechanics cannot replace reasoner psychology")
        if self.contains_story_prose:
            raise ContractValidationError("adult mechanics proposal cannot contain story prose")
        _unique((str(value.source_unit_id) for value in self.unit_treatments), "unit treatments")

    @property
    def proposal_sha256(self) -> str:
        return domain_sha256("cera.adult_mechanics_proposal.v1", self)


@dataclass(frozen=True, slots=True)
class AdultMechanicsReceipt:
    SCHEMA_VERSION: ClassVar[str] = "cera.adult_mechanics_receipt.v1"

    schema_version: str
    provider_receipt_id: TypedId
    adapter_role: str
    fixture_id: str
    fixture_sha256: str
    request_sha256: str
    proposal_sha256: str
    authority_sha256: str
    ledger_sha256: str
    context_sha256: str
    retained_advisory_count: int
    quarantined_advisory_sha256: tuple[str, ...]
    external_provider_calls: int
    story_state_committed: bool

    def __post_init__(self) -> None:
        require_schema(self.schema_version, self.SCHEMA_VERSION, type(self).__name__)
        require_kind(self.provider_receipt_id, IdKind.PROVIDER_RECEIPT, "provider_receipt_id")
        if self.adapter_role != "fake_adult_mechanics":
            raise ContractValidationError("Phase 7 receipt requires fake adult mechanics role")
        _non_empty(self.fixture_id, "fixture_id")
        for value in (
            self.fixture_sha256,
            self.request_sha256,
            self.proposal_sha256,
            self.authority_sha256,
            self.ledger_sha256,
            self.context_sha256,
            *self.quarantined_advisory_sha256,
        ):
            _sha256(value, "adult mechanics receipt hash")
        if self.retained_advisory_count < 0:
            raise ContractValidationError("retained advisory count cannot be negative")
        if self.external_provider_calls != 0 or self.story_state_committed:
            raise ContractValidationError("fake adult mechanics cannot call or commit")

    @property
    def receipt_sha256(self) -> str:
        return domain_sha256("cera.adult_mechanics_receipt.v1", self)


@dataclass(frozen=True, slots=True)
class AdultMechanicsResult:
    proposal: AdultMechanicsProposal
    receipt: AdultMechanicsReceipt
    retained_advisory_metadata: tuple[object, ...]
    quarantined_advisory_metadata: tuple[object, ...]


def _sha256(value: str, field_name: str) -> None:
    if not re_is_sha256(value):
        raise ContractValidationError(f"{field_name} must be SHA-256")


def _non_empty(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ContractValidationError(f"{field_name} must be non-empty")


def _unique(values, field_name: str) -> None:
    items = list(values)
    if len(items) != len(set(items)):
        raise ContractValidationError(f"{field_name} must not contain duplicates")
