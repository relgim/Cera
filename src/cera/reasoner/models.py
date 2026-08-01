"""Provider-neutral Scene Reasoner request, result, and receipt contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, ClassVar

from cera.adult_craft.models import AdultCraftNeed, AdultCraftNeedV2, AdultCraftNeedV3
from cera.contracts import (
    BehavioralScenePlan,
    BehavioralTurnControls,
    DecisionRoute,
    SceneDecision,
    SceneDepthMode,
    SourceUnitClassification,
    CreatorRevisionDirective,
    CreatorRevisionMode,
)
from cera.errors import ContractValidationError, ErrorCode
from cera.evidence import (
    EvidenceLookupReceipt,
    EvidenceObligation,
    EvidenceSnapshot,
    ExactEvidence,
)
from cera.ids import IdKind, TypedId, require_authority_record_kind, require_kind
from cera.kernel import (
    PreparedTurn,
    ProposedStateRecord,
    StateDeltaValidationReceipt,
    TurnRoute,
)
from cera.schema import require_schema
from cera.serialization import domain_sha256, re_is_sha256

if TYPE_CHECKING:
    from cera.providers import CodexOperationTelemetryV1, LiveProviderCallReceipt
    from .mcp_bridge import McpEvidenceBridgeReceipt


class ReasonerOutcomeStatus(StrEnum):
    DECISION_READY = "decision_ready"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    BLOCKED = "blocked"


class ReasonerAdapterRole(StrEnum):
    SCRIPTED_FAKE = "fake_scene_reasoner"
    CODEX = "codex_scene_reasoner"


class ReasonerSourceMode(StrEnum):
    ORDINARY_EXACT = "ordinary_exact"
    ADULT_NON_GRAPHIC_LEDGER = "adult_non_graphic_ledger"


@dataclass(frozen=True, slots=True)
class ReasonerSourceUnit:
    source_unit_id: TypedId
    classification: SourceUnitClassification
    safe_text: str

    def __post_init__(self) -> None:
        require_kind(self.source_unit_id, IdKind.SOURCE_UNIT, "source_unit_id")
        _non_empty(self.safe_text, "safe_text")


@dataclass(frozen=True, slots=True)
class ReasonerSourceView:
    mode: ReasonerSourceMode
    source_sha256: str
    units: tuple[ReasonerSourceUnit, ...]
    contains_exact_protected_adult_prose: bool

    def __post_init__(self) -> None:
        if not re_is_sha256(self.source_sha256):
            raise ContractValidationError("reasoner source hash must be SHA-256")
        if not self.units:
            raise ContractValidationError("reasoner source view requires units")
        _unique((str(value.source_unit_id) for value in self.units), "source units")
        if (
            self.mode is ReasonerSourceMode.ADULT_NON_GRAPHIC_LEDGER
            and self.contains_exact_protected_adult_prose
        ):
            raise ContractValidationError(
                "adult reasoner ledger cannot contain exact protected prose"
            )

    @property
    def source_view_sha256(self) -> str:
        return domain_sha256("cera.reasoner_source_view.v1", self)


@dataclass(frozen=True, slots=True)
class ReasonerSeedDossier:
    snapshot_token: TypedId
    aware_character_ids: tuple[TypedId, ...]
    exact_seed_evidence: tuple[ExactEvidence, ...]
    scene_anchors: tuple[str, ...]
    explicit_unknowns: tuple[str, ...]
    prohibited_inferences: tuple[str, ...]
    evidence_obligations: tuple[EvidenceObligation, ...] = ()
    seed_receipt_id: TypedId | None = None

    def __post_init__(self) -> None:
        require_kind(self.snapshot_token, IdKind.SNAPSHOT, "snapshot_token")
        for character_id in self.aware_character_ids:
            require_kind(character_id, IdKind.CHARACTER, "aware_character_ids")
        _unique((str(value) for value in self.aware_character_ids), "aware characters")
        _unique(
            (str(value.evidence_id) for value in self.exact_seed_evidence),
            "seed evidence",
        )
        _unique(
            (str(value.obligation_id) for value in self.evidence_obligations),
            "seed evidence obligations",
        )
        if self.seed_receipt_id is not None:
            require_kind(self.seed_receipt_id, IdKind.SEED_RECEIPT, "seed_receipt_id")


@dataclass(frozen=True, slots=True)
class SceneReasonerRequest:
    SCHEMA_VERSION: ClassVar[str] = "cera.scene_reasoner_request.v4"

    schema_version: str
    prepared_turn: PreparedTurn
    source_view: ReasonerSourceView
    seed_dossier: ReasonerSeedDossier
    hard_boundaries: tuple[str, ...]
    scene_depth_mode: SceneDepthMode = SceneDepthMode.AUTO
    behavioral_controls: BehavioralTurnControls = field(
        default_factory=BehavioralTurnControls.creator_default
    )
    creator_revision: CreatorRevisionDirective | None = None

    def __post_init__(self) -> None:
        require_schema(self.schema_version, self.SCHEMA_VERSION, type(self).__name__)
        if self.prepared_turn.request.source_sha256 != self.source_view.source_sha256:
            raise ContractValidationError("reasoner source hash does not match prepared turn")
        prepared_units = {
            value.source_unit_id: value.classification
            for value in self.prepared_turn.request.source_units
        }
        supplied_units = {
            value.source_unit_id: value.classification for value in self.source_view.units
        }
        if supplied_units != prepared_units:
            raise ContractValidationError("reasoner source units do not match prepared turn")
        snapshot_token = self.prepared_turn.evidence_snapshot.snapshot_token
        if self.seed_dossier.snapshot_token != snapshot_token:
            raise ContractValidationError("seed dossier snapshot does not match prepared turn")
        if self.prepared_turn.route is TurnRoute.CONSENT_VALID_ADULT:
            if self.source_view.mode is not ReasonerSourceMode.ADULT_NON_GRAPHIC_LEDGER:
                raise ContractValidationError("adult reasoner requires non-graphic ledger")
        elif self.source_view.mode is not ReasonerSourceMode.ORDINARY_EXACT:
            raise ContractValidationError("ordinary reasoner requires ordinary source view")
        if not self.hard_boundaries:
            raise ContractValidationError("reasoner request requires hard boundaries")
        if (
            self.creator_revision is not None
            and self.creator_revision.mode is CreatorRevisionMode.COMPOSER_REWRITE
        ):
            raise ContractValidationError(
                "Composer-only rewrite feedback cannot alter Reasoner input"
            )
        snapshot = self.prepared_turn.evidence_snapshot
        for seed in self.seed_dossier.exact_seed_evidence:
            if seed.metadata.genesis_revision_id != snapshot.genesis_revision_id:
                raise ContractValidationError("seed evidence Genesis revision mismatch")

    @property
    def request_sha256(self) -> str:
        return domain_sha256(self.SCHEMA_VERSION, self)


class ParticipationRole(StrEnum):
    LEAD = "lead"
    SECONDARY = "secondary"


class InterventionReason(StrEnum):
    FLOOR_OWNER = "floor_owner"
    DIRECT_STAKE = "direct_stake"
    CORRECTION = "correction"
    PROTECTION = "protection"
    ESTABLISHED_TACTIC = "established_tactic"
    CURRENT_SOURCE_ADDRESS = "current_source_address"


@dataclass(frozen=True, slots=True)
class ParticipationSelection:
    character_id: TypedId
    role: ParticipationRole
    intervention_reason: InterventionReason
    evidence_ids: tuple[TypedId, ...]

    def __post_init__(self) -> None:
        require_kind(self.character_id, IdKind.CHARACTER, "character_id")
        for evidence_id in self.evidence_ids:
            require_kind(evidence_id, IdKind.EVIDENCE, "evidence_ids")
        _unique((str(value) for value in self.evidence_ids), "participation evidence")
        if self.role is ParticipationRole.LEAD:
            if self.intervention_reason is not InterventionReason.FLOOR_OWNER:
                raise ContractValidationError("lead participant must own the floor")
        elif self.intervention_reason is InterventionReason.FLOOR_OWNER:
            raise ContractValidationError("secondary participant cannot own the floor")


@dataclass(frozen=True, slots=True)
class ReasonerEvidenceCitation:
    evidence_id: TypedId
    record_id: TypedId
    record_version: int

    def __post_init__(self) -> None:
        require_kind(self.evidence_id, IdKind.EVIDENCE, "evidence_id")
        require_authority_record_kind(self.record_id, "record_id")
        if self.record_version < 1:
            raise ContractValidationError("citation record version must be positive")


class ProtectedUserClaimKind(StrEnum):
    ACTION = "action"
    DIALOGUE = "dialogue"


@dataclass(frozen=True, slots=True)
class ProtectedUserSourceClaim:
    """Python-anchored exact source authority; never model-inferred behavior."""

    source_unit_id: TypedId
    kind: ProtectedUserClaimKind
    start: int
    end: int
    exact_text_sha256: str

    def __post_init__(self) -> None:
        require_kind(self.source_unit_id, IdKind.SOURCE_UNIT, "source_unit_id")
        if self.start < 0 or self.end <= self.start:
            raise ContractValidationError("protected-user source claim span is invalid")
        if not re_is_sha256(self.exact_text_sha256):
            raise ContractValidationError(
                "protected-user source claim hash is invalid"
            )


@dataclass(frozen=True, slots=True)
class ReasonerOutcome:
    SCHEMA_VERSION: ClassVar[str] = "cera.reasoner_outcome.v6"

    schema_version: str
    status: ReasonerOutcomeStatus
    decision: SceneDecision | None
    participation: tuple[ParticipationSelection, ...]
    hard_citations: tuple[ReasonerEvidenceCitation, ...]
    insufficiencies: tuple[str, ...]
    blocker_code: ErrorCode | None
    advisory_state_deltas: tuple[ProposedStateRecord, ...]
    protected_user_boundary_acknowledged: bool
    adult_craft_need: AdultCraftNeedV3 | AdultCraftNeedV2 | AdultCraftNeed | None = None
    protected_user_source_claims: tuple[ProtectedUserSourceClaim, ...] = ()
    behavioral_scene_plan: BehavioralScenePlan | None = None

    def __post_init__(self) -> None:
        require_schema(self.schema_version, self.SCHEMA_VERSION, type(self).__name__)
        if not self.protected_user_boundary_acknowledged:
            raise ContractValidationError("reasoner outcome must acknowledge protected-user ownership")
        _unique(
            (str(value.character_id) for value in self.participation),
            "participation characters",
        )
        _unique(
            (str(value.evidence_id) for value in self.hard_citations),
            "hard citations",
        )
        _unique(
            (
                f"{value.source_unit_id}|{value.kind.value}|{value.start}|{value.end}"
                for value in self.protected_user_source_claims
            ),
            "protected-user source claims",
        )
        if self.status is ReasonerOutcomeStatus.DECISION_READY:
            if self.decision is None or not self.participation:
                raise ContractValidationError("ready outcome requires decision and participation")
            if self.insufficiencies or self.blocker_code is not None:
                raise ContractValidationError("ready outcome cannot carry failure state")
            assert self.decision is not None
            if self.decision.route is DecisionRoute.ORDINARY and self.adult_craft_need is not None:
                raise ContractValidationError("ordinary reasoner outcome cannot carry adult craft need")
            if self.adult_craft_need is not None:
                need = self.adult_craft_need
                if self.decision.route is not DecisionRoute.CONSENT_VALID_ADULT:
                    raise ContractValidationError("adult craft need requires an adult decision")
                if need.decision_id != self.decision.decision_id:
                    raise ContractValidationError("adult craft need changed decision identity")
                sequence_sha256 = domain_sha256(
                    "cera.sequence_plan.v1",
                    (self.decision.current_segment, self.decision.future_segments),
                )
                if need.sequence_plan_sha256 != sequence_sha256:
                    raise ContractValidationError("adult craft need changed SequencePlan")
            if self.behavioral_scene_plan is not None and (
                self.behavioral_scene_plan.selected_character_ids
                != self.decision.responding_npc_ids
            ):
                raise ContractValidationError(
                    "behavioral scene plan changed the selected character set"
                )
        elif self.status is ReasonerOutcomeStatus.INSUFFICIENT_EVIDENCE:
            if self.decision is not None or not self.insufficiencies:
                raise ContractValidationError(
                    "insufficient outcome must preserve uncertainty without a decision"
                )
            if self.participation or self.hard_citations or self.blocker_code is not None:
                raise ContractValidationError("insufficient outcome cannot imply a decision")
            if self.advisory_state_deltas:
                raise ContractValidationError("insufficient outcome cannot propose durable state")
            if self.adult_craft_need is not None:
                raise ContractValidationError("insufficient outcome cannot select adult craft")
            if self.behavioral_scene_plan is not None:
                raise ContractValidationError("insufficient outcome cannot carry a scene plan")
        else:
            if self.decision is not None or self.blocker_code is None:
                raise ContractValidationError("blocked outcome requires only blocker code")
            if self.insufficiencies:
                raise ContractValidationError(
                    "blocked outcome cannot carry insufficiencies"
                )
            if self.participation or self.hard_citations or self.advisory_state_deltas:
                raise ContractValidationError("blocked outcome cannot carry decision content")
            if self.blocker_code is not ErrorCode.BLOCKED_NONCONSENSUAL_EVENT:
                raise ContractValidationError("reasoner blocked outcome has unsupported code")
            if self.adult_craft_need is not None:
                raise ContractValidationError("blocked outcome cannot select adult craft")
            if self.behavioral_scene_plan is not None:
                raise ContractValidationError("blocked outcome cannot carry a scene plan")

    @property
    def outcome_sha256(self) -> str:
        return domain_sha256("cera.reasoner_outcome.v6", self)


class ReasonerOutcomeV5(ReasonerOutcome):
    """Decoder for immutable historical v5 outcome records."""

    SCHEMA_VERSION: ClassVar[str] = "cera.reasoner_outcome.v5"

    @property
    def outcome_sha256(self) -> str:
        return domain_sha256("cera.reasoner_outcome.v5", self)


class ReasonerOutcomeV4(ReasonerOutcome):
    """Decoder for immutable historical v4 outcome records."""

    SCHEMA_VERSION: ClassVar[str] = "cera.reasoner_outcome.v4"

    @property
    def outcome_sha256(self) -> str:
        return domain_sha256("cera.reasoner_outcome.v4", self)


class ReasonerOutcomeV3(ReasonerOutcome):
    """Decoder for immutable historical v3 outcome records."""

    SCHEMA_VERSION: ClassVar[str] = "cera.reasoner_outcome.v3"

    @property
    def outcome_sha256(self) -> str:
        return domain_sha256("cera.reasoner_outcome.v3", self)


@dataclass(frozen=True, slots=True)
class SceneReasonerAdapterCall:
    """Transient provider-neutral result plus safe adapter evidence handles."""

    outcome: ReasonerOutcome
    adapter_role: ReasonerAdapterRole
    adapter_version: str
    adapter_evidence_id: str
    adapter_evidence_sha256: str
    provider_receipt_id: TypedId
    provider_receipt_sha256: str
    bridge_receipt_id: TypedId | None
    bridge_receipt_sha256: str | None
    external_provider_calls: int
    provider_call_receipt: LiveProviderCallReceipt | None = None
    mcp_bridge_receipt: McpEvidenceBridgeReceipt | None = None
    operation_telemetry: CodexOperationTelemetryV1 | None = None

    def __post_init__(self) -> None:
        _non_empty(self.adapter_version, "adapter_version")
        _non_empty(self.adapter_evidence_id, "adapter_evidence_id")
        for value in (
            self.adapter_evidence_sha256,
            self.provider_receipt_sha256,
        ):
            if not re_is_sha256(value):
                raise ContractValidationError("reasoner adapter evidence must be SHA-256")
        require_kind(
            self.provider_receipt_id,
            IdKind.PROVIDER_RECEIPT,
            "provider_receipt_id",
        )
        if (self.bridge_receipt_id is None) != (self.bridge_receipt_sha256 is None):
            raise ContractValidationError("reasoner bridge receipt ID/hash must be paired")
        if self.bridge_receipt_id is not None:
            require_kind(
                self.bridge_receipt_id,
                IdKind.MCP_BRIDGE_RECEIPT,
                "bridge_receipt_id",
            )
            assert self.bridge_receipt_sha256 is not None
            if not re_is_sha256(self.bridge_receipt_sha256):
                raise ContractValidationError("reasoner bridge receipt hash must be SHA-256")
        if self.adapter_role is ReasonerAdapterRole.SCRIPTED_FAKE:
            if (
                self.external_provider_calls != 0
                or self.bridge_receipt_id is not None
                or self.provider_call_receipt is not None
                or self.mcp_bridge_receipt is not None
                or self.operation_telemetry is not None
            ):
                raise ContractValidationError("fake reasoner cannot report live bridge evidence")
        else:
            if (
                self.external_provider_calls != 1
                or self.provider_call_receipt is None
            ):
                raise ContractValidationError("Codex reasoner requires one provider call")
            if (
                self.provider_call_receipt.provider_receipt_id
                != self.provider_receipt_id
                or self.provider_call_receipt.receipt_sha256
                != self.provider_receipt_sha256
            ):
                raise ContractValidationError("reasoner provider receipt does not match handle")
            if (self.bridge_receipt_id is None) != (self.mcp_bridge_receipt is None):
                raise ContractValidationError("reasoner bridge handle/payload must be paired")
            if self.mcp_bridge_receipt is not None and (
                self.mcp_bridge_receipt.bridge_receipt_id != self.bridge_receipt_id
                or self.mcp_bridge_receipt.receipt_sha256 != self.bridge_receipt_sha256
            ):
                raise ContractValidationError("reasoner bridge receipt does not match handle")
            if (
                self.operation_telemetry is not None
                and self.operation_telemetry.request_sha256
                != self.provider_call_receipt.request_sha256
            ):
                raise ContractValidationError(
                    "reasoner operation telemetry does not match provider request"
                )


@dataclass(frozen=True, slots=True)
class SceneReasonerReceipt:
    SCHEMA_VERSION: ClassVar[str] = "cera.scene_reasoner_receipt.v2"

    schema_version: str
    provider_receipt_id: TypedId
    reasoner_request_sha256: str
    snapshot_token: TypedId
    source_sha256: str
    source_view_sha256: str
    adapter_role: ReasonerAdapterRole
    adapter_version: str
    adapter_evidence_id: str
    adapter_evidence_sha256: str
    provider_receipt_sha256: str
    bridge_receipt_id: TypedId | None
    bridge_receipt_sha256: str | None
    outcome_sha256: str
    evidence_lookup_receipt_ids: tuple[TypedId, ...]
    tool_call_count: int
    cumulative_evidence_bytes: int
    outcome_status: ReasonerOutcomeStatus
    external_provider_calls: int

    def __post_init__(self) -> None:
        require_schema(self.schema_version, self.SCHEMA_VERSION, type(self).__name__)
        require_kind(
            self.provider_receipt_id,
            IdKind.PROVIDER_RECEIPT,
            "provider_receipt_id",
        )
        require_kind(self.snapshot_token, IdKind.SNAPSHOT, "snapshot_token")
        for value in (
            self.reasoner_request_sha256,
            self.source_sha256,
            self.source_view_sha256,
            self.adapter_evidence_sha256,
            self.provider_receipt_sha256,
            self.outcome_sha256,
        ):
            if not re_is_sha256(value):
                raise ContractValidationError("reasoner receipt hashes must be SHA-256")
        _non_empty(self.adapter_version, "adapter_version")
        _non_empty(self.adapter_evidence_id, "adapter_evidence_id")
        if (self.bridge_receipt_id is None) != (self.bridge_receipt_sha256 is None):
            raise ContractValidationError("reasoner bridge receipt ID/hash must be paired")
        if self.bridge_receipt_id is not None:
            require_kind(
                self.bridge_receipt_id,
                IdKind.MCP_BRIDGE_RECEIPT,
                "bridge_receipt_id",
            )
            assert self.bridge_receipt_sha256 is not None
            if not re_is_sha256(self.bridge_receipt_sha256):
                raise ContractValidationError("reasoner bridge receipt hash must be SHA-256")
        for receipt_id in self.evidence_lookup_receipt_ids:
            require_kind(receipt_id, IdKind.LOOKUP_RECEIPT, "evidence receipt IDs")
        _unique(
            (str(value) for value in self.evidence_lookup_receipt_ids),
            "evidence receipt IDs",
        )
        if min(self.tool_call_count, self.cumulative_evidence_bytes) < 0:
            raise ContractValidationError("reasoner receipt counts cannot be negative")
        if self.adapter_role is ReasonerAdapterRole.SCRIPTED_FAKE:
            if self.external_provider_calls != 0 or self.bridge_receipt_id is not None:
                raise ContractValidationError("fake reasoner cannot report live bridge evidence")
        else:
            if self.external_provider_calls != 1:
                raise ContractValidationError("Codex reasoner receipt requires one provider call")
            if self.tool_call_count > 0 and self.bridge_receipt_id is None:
                raise ContractValidationError("Codex tool calls require a bridge receipt")
            if self.tool_call_count == 0 and self.bridge_receipt_id is not None:
                # A zero-call bridge is still valid evidence that tools were
                # available but not used; an absent bridge means Python disabled
                # tool exposure because the exact dossier was sufficient.
                pass


@dataclass(frozen=True, slots=True)
class ReasonerExecutionResult:
    outcome: ReasonerOutcome
    receipt: SceneReasonerReceipt
    state_delta_validation_receipt_id: TypedId | None
    provider_call_receipt: LiveProviderCallReceipt | None
    mcp_bridge_receipt: McpEvidenceBridgeReceipt | None
    authorized_exact_evidence: tuple[ExactEvidence, ...]
    evidence_lookup_receipts: tuple[EvidenceLookupReceipt, ...] = ()
    state_delta_validation_receipt: StateDeltaValidationReceipt | None = None
    operation_telemetry: CodexOperationTelemetryV1 | None = None

    def __post_init__(self) -> None:
        _unique(
            (str(value.evidence_id) for value in self.authorized_exact_evidence),
            "retained exact evidence IDs",
        )
        if tuple(
            value.lookup_receipt_id for value in self.evidence_lookup_receipts
        ) != self.receipt.evidence_lookup_receipt_ids:
            raise ContractValidationError(
                "retained Reasoner lookup receipts do not match receipt handles"
            )
        if self.state_delta_validation_receipt_id is not None:
            require_kind(
                self.state_delta_validation_receipt_id,
                IdKind.VALIDATION,
                "state_delta_validation_receipt_id",
            )
        if (self.state_delta_validation_receipt_id is None) != (
            self.state_delta_validation_receipt is None
        ):
            raise ContractValidationError("state-delta receipt ID/payload must be paired")
        if (
            self.state_delta_validation_receipt is not None
            and self.state_delta_validation_receipt.validation_receipt_id
            != self.state_delta_validation_receipt_id
        ):
            raise ContractValidationError("state-delta receipt payload does not match its ID")
        if self.receipt.adapter_role is ReasonerAdapterRole.SCRIPTED_FAKE:
            if (
                self.provider_call_receipt is not None
                or self.mcp_bridge_receipt is not None
                or self.operation_telemetry is not None
            ):
                raise ContractValidationError("fake execution cannot retain live receipts")
        else:
            if self.provider_call_receipt is None:
                raise ContractValidationError("Codex execution requires a provider receipt")
            if self.operation_telemetry is not None and (
                self.operation_telemetry.request_sha256
                != self.provider_call_receipt.request_sha256
            ):
                raise ContractValidationError(
                    "Codex execution telemetry does not match provider receipt"
                )
            if (
                self.provider_call_receipt.provider_receipt_id
                != self.receipt.provider_receipt_id
                or self.provider_call_receipt.receipt_sha256
                != self.receipt.provider_receipt_sha256
            ):
                raise ContractValidationError("execution provider receipt does not match")
            if (self.receipt.bridge_receipt_id is None) != (
                self.mcp_bridge_receipt is None
            ):
                raise ContractValidationError("execution bridge receipt presence differs")
            if self.mcp_bridge_receipt is not None and (
                self.mcp_bridge_receipt.bridge_receipt_id
                != self.receipt.bridge_receipt_id
                or self.mcp_bridge_receipt.receipt_sha256
                != self.receipt.bridge_receipt_sha256
            ):
                raise ContractValidationError("execution bridge receipt does not match")


def _non_empty(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ContractValidationError(f"{field_name} must be non-empty")


def _unique(values, field_name: str) -> None:
    items = list(values)
    if len(items) != len(set(items)):
        raise ContractValidationError(f"{field_name} must not contain duplicates")
