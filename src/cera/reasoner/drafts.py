"""Minimal provider-facing Scene Reasoner draft and Python compiler.

The provider owns semantic choices. Python owns canonical identities, citation
bookkeeping, normalized relationship labels, and authoritative domain records.
"""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Mapping
from typing import ClassVar

from cera.adult_craft.models import (
    AdultCraftAxis,
    AdultCraftConcept,
    AdultCraftFamily,
    AdultCraftMode,
    AdultCraftNeedV2,
    AdultCraftNeedV3,
    BeatCraftRequirementV2,
    BeatCraftRequirementV3,
    CharacterCardSectionNeed,
    CharacterChannelCraftNeedV2,
    CharacterChannelCraftNeedV3,
    OutcomeScope,
    SceneChannelCraftNeedV2,
    SceneChannelCraftNeedV3,
)
from cera.contracts import (
    BeatState,
    BehavioralScenePlan,
    CausalRunwayContract,
    CharacterMove,
    CurrentSegment,
    DecisionRoute,
    DevelopmentAtomKind,
    DevelopmentAtomProposal,
    DevelopmentAtomStrength,
    FutureSegment,
    InteractionTopology,
    InteriorityLevel,
    NaturalStopReason,
    ProtectedUserAllowanceKind,
    PromptTone,
    ProtectedUserRealizationAllowance,
    SceneDecision,
    SceneEventBlock,
    SceneFunction,
    SceneRunwayClass,
    SequenceBeat,
    SourceClaimAuthority,
    SourceClaimDecision,
    SourceClaimKind,
    SourceClaimLedger,
    WriterScaffold,
    validate_claim_authority,
)
from cera.errors import ContractValidationError, ErrorCode
from cera.evidence import ExactEvidence
from cera.ids import IdKind, TypedId, deterministic_id, require_kind
from cera.schema import require_schema
from cera.serialization import canonical_sha256, domain_sha256, text_sha256

from .models import (
    InterventionReason,
    ParticipationRole,
    ParticipationSelection,
    ReasonerEvidenceCitation,
    ReasonerOutcome,
    ReasonerOutcomeStatus,
    ProtectedUserClaimKind,
    ProtectedUserSourceClaim,
    SceneReasonerRequest,
)


@dataclass(frozen=True, slots=True)
class DraftParticipationV2:
    character_id: TypedId
    intervention_reason: InterventionReason | None
    evidence_ids: tuple[TypedId, ...]

    def __post_init__(self) -> None:
        require_kind(self.character_id, IdKind.CHARACTER, "character_id")
        if self.intervention_reason is InterventionReason.FLOOR_OWNER:
            raise ContractValidationError(
                "floor_owner is Python-derived and cannot be provider-authored"
            )
        for evidence_id in self.evidence_ids:
            require_kind(evidence_id, IdKind.EVIDENCE, "evidence_ids")
        _unique(self.evidence_ids, "participation evidence")


@dataclass(frozen=True, slots=True)
class DraftCharacterMoveV2:
    character_id: TypedId
    perception: str
    selected_intent: str
    action_direction: str
    evidence_ids: tuple[TypedId, ...]
    knowledge_constraints: tuple[str, ...]

    def __post_init__(self) -> None:
        require_kind(self.character_id, IdKind.CHARACTER, "character_id")
        _non_empty(self.perception, "perception")
        _non_empty(self.selected_intent, "selected_intent")
        _non_empty(self.action_direction, "action_direction")
        for evidence_id in self.evidence_ids:
            require_kind(evidence_id, IdKind.EVIDENCE, "evidence_ids")
        _unique(self.evidence_ids, "move evidence")


@dataclass(frozen=True, slots=True)
class DraftSequenceBeatV2:
    beat_key: str
    actor_id: TypedId
    state: BeatState
    neutral_event: str
    evidence_ids: tuple[TypedId, ...]

    def __post_init__(self) -> None:
        _local_key(self.beat_key, "beat_key")
        if self.actor_id.kind not in {IdKind.CHARACTER, IdKind.MATERIAL}:
            raise ContractValidationError(
                "draft beat actor must be a character or material"
            )
        _non_empty(self.neutral_event, "neutral_event")
        for evidence_id in self.evidence_ids:
            require_kind(evidence_id, IdKind.EVIDENCE, "evidence_ids")
        _unique(self.evidence_ids, "beat evidence")


@dataclass(frozen=True, slots=True)
class DraftFutureSegmentV2:
    segment_key: str
    activation_conditions: tuple[str, ...]
    invalidation_conditions: tuple[str, ...]
    possible_consequences: tuple[str, ...]
    open_user_choice: str

    def __post_init__(self) -> None:
        _local_key(self.segment_key, "segment_key")
        _non_empty(self.open_user_choice, "open_user_choice")


@dataclass(frozen=True, slots=True)
class AdultBeatCraftDraftV2:
    beat_key: str
    action_family: str
    object_concepts: tuple[AdultCraftConcept, ...]
    axes: tuple[AdultCraftAxis, ...]
    channel_requirements: tuple[
        CharacterChannelCraftNeedV2 | SceneChannelCraftNeedV2, ...
    ]
    priority: int

    def __post_init__(self) -> None:
        _local_key(self.beat_key, "beat_key")
        _non_empty(self.action_family, "action_family")
        if not self.axes or not self.channel_requirements:
            raise ContractValidationError(
                "adult beat draft needs axes and channel requirements"
            )
        for concept in self.object_concepts:
            if not isinstance(concept, AdultCraftConcept):
                raise ContractValidationError("adult object concept is invalid")
        _unique(self.object_concepts, "adult object concepts")
        _unique(self.axes, "adult axes")
        _unique(
            (
                f"{value.channel.value}|{getattr(value, 'character_id', None)}"
                for value in self.channel_requirements
            ),
            "adult channel requirements",
        )
        if not 1 <= self.priority <= 5:
            raise ContractValidationError("adult beat priority must be 1..5")


@dataclass(frozen=True, slots=True)
class AdultCraftNeedDraftV2:
    mode: AdultCraftMode
    families: tuple[AdultCraftFamily, ...]
    subfamilies: tuple[str, ...]
    beat_requirements: tuple[AdultBeatCraftDraftV2, ...]
    climax: OutcomeScope
    aftermath: OutcomeScope
    character_card_sections: tuple[CharacterCardSectionNeed, ...]

    def __post_init__(self) -> None:
        if not self.families or not self.beat_requirements:
            raise ContractValidationError(
                "adult craft draft requires families and current beat needs"
            )
        _unique(self.families, "adult families")
        _unique(self.subfamilies, "adult subfamilies")
        _unique(
            (value.beat_key for value in self.beat_requirements),
            "adult beat keys",
        )
        _unique(
            (value.character_id for value in self.character_card_sections),
            "adult card characters",
        )


@dataclass(frozen=True, slots=True)
class AdultBeatCraftDraftV3:
    beat_key: str
    action_family: str
    object_concepts: tuple[AdultCraftConcept, ...]
    axes: tuple[AdultCraftAxis, ...]
    channel_requirements: tuple[
        CharacterChannelCraftNeedV3 | SceneChannelCraftNeedV3, ...
    ]
    priority: int

    def __post_init__(self) -> None:
        _local_key(self.beat_key, "beat_key")
        _non_empty(self.action_family, "action_family")
        if not self.axes or not self.channel_requirements:
            raise ContractValidationError(
                "adult beat draft needs axes and channel requirements"
            )
        _unique(self.object_concepts, "adult object concepts")
        _unique(self.axes, "adult axes")
        _unique(
            (
                f"{value.channel.value}|{getattr(value, 'character_id', None)}"
                for value in self.channel_requirements
            ),
            "adult channel requirements",
        )
        if not 1 <= self.priority <= 5:
            raise ContractValidationError("adult beat priority must be 1..5")


@dataclass(frozen=True, slots=True)
class AdultCraftNeedDraftV3:
    mode: AdultCraftMode
    families: tuple[AdultCraftFamily, ...]
    subfamilies: tuple[str, ...]
    beat_requirements: tuple[AdultBeatCraftDraftV3, ...]
    climax: OutcomeScope
    aftermath: OutcomeScope
    character_card_sections: tuple[CharacterCardSectionNeed, ...]

    def __post_init__(self) -> None:
        if not self.families or not self.beat_requirements:
            raise ContractValidationError(
                "adult craft draft requires families and current beat needs"
            )
        _unique(self.families, "adult families")
        _unique(self.subfamilies, "adult subfamilies")
        _unique(
            (value.beat_key for value in self.beat_requirements),
            "adult beat keys",
        )
        _unique(
            (value.character_id for value in self.character_card_sections),
            "adult card characters",
        )


@dataclass(frozen=True, slots=True)
class CodexReasonerDraftV2:
    """Provider-neutral semantic draft; never durable authority."""

    SCHEMA_VERSION: ClassVar[str] = "cera.codex_reasoner_draft.v2"

    schema_version: str
    status: ReasonerOutcomeStatus
    route: DecisionRoute | None
    scene_intent: str | None
    responding_npc_ids: tuple[TypedId, ...]
    floor_owner_id: TypedId | None
    participation: tuple[DraftParticipationV2, ...]
    character_moves: tuple[DraftCharacterMoveV2, ...]
    current_beats: tuple[DraftSequenceBeatV2, ...]
    stop_before: str | None
    future_segments: tuple[DraftFutureSegmentV2, ...]
    writer_must_preserve: tuple[str, ...]
    uncertainties: tuple[str, ...]
    prohibited_inferences: tuple[str, ...]
    insufficiencies: tuple[str, ...]
    blocker_code: ErrorCode | None
    adult_craft_need: AdultCraftNeedDraftV2 | None
    protected_user_boundary_acknowledged: bool

    def __post_init__(self) -> None:
        require_schema(self.schema_version, self.SCHEMA_VERSION, type(self).__name__)
        if not self.protected_user_boundary_acknowledged:
            raise ContractValidationError(
                "reasoner draft must acknowledge protected-user ownership"
            )
        for character_id in self.responding_npc_ids:
            require_kind(character_id, IdKind.CHARACTER, "responding_npc_ids")
        _unique(self.responding_npc_ids, "responding NPCs")
        _unique(
            (value.character_id for value in self.participation),
            "participation characters",
        )
        _unique(
            (value.character_id for value in self.character_moves),
            "move characters",
        )
        _unique((value.beat_key for value in self.current_beats), "current beat keys")
        _unique(
            (value.segment_key for value in self.future_segments),
            "future segment keys",
        )
        _unique(self.writer_must_preserve, "writer preservation requirements")
        _unique(self.uncertainties, "reasoner uncertainties")
        _unique(self.prohibited_inferences, "prohibited inferences")
        _unique(self.insufficiencies, "reasoner insufficiencies")
        if self.status is ReasonerOutcomeStatus.DECISION_READY:
            if (
                self.route is None
                or self.scene_intent is None
                or self.floor_owner_id is None
                or not self.responding_npc_ids
                or not self.current_beats
                or self.stop_before is None
            ):
                raise ContractValidationError(
                    "decision-ready draft lacks required semantic content"
                )
            if self.route not in {
                DecisionRoute.ORDINARY,
                DecisionRoute.CONSENT_VALID_ADULT,
            }:
                raise ContractValidationError(
                    "scene reasoner draft cannot select an aftermath route"
                )
            if self.insufficiencies or self.blocker_code is not None:
                raise ContractValidationError(
                    "decision-ready draft cannot carry failure state"
                )
            if set(value.character_id for value in self.character_moves) != set(
                self.responding_npc_ids
            ):
                raise ContractValidationError(
                    "draft moves must map one-to-one to responders"
                )
            if set(value.character_id for value in self.participation) != set(
                self.responding_npc_ids
            ):
                raise ContractValidationError(
                    "draft participation must map one-to-one to responders"
                )
            if self.floor_owner_id not in self.responding_npc_ids:
                raise ContractValidationError(
                    "draft floor owner must be a responder"
                )
            for participant in self.participation:
                if (
                    participant.character_id != self.floor_owner_id
                    and participant.intervention_reason is None
                ):
                    raise ContractValidationError(
                        "secondary participant requires a semantic intervention reason"
                    )
            if self.route is DecisionRoute.ORDINARY and self.adult_craft_need is not None:
                raise ContractValidationError(
                    "ordinary draft cannot carry adult craft"
                )
            if (
                self.route is DecisionRoute.CONSENT_VALID_ADULT
                and self.adult_craft_need is None
            ):
                raise ContractValidationError(
                    "active adult draft requires beat-local craft semantics"
                )
        elif self.status is ReasonerOutcomeStatus.INSUFFICIENT_EVIDENCE:
            if not self.insufficiencies:
                raise ContractValidationError(
                    "insufficient draft requires explicit insufficiencies"
                )
            if self.blocker_code is not None:
                raise ContractValidationError(
                    "insufficient draft cannot carry a blocker code"
                )
            self._require_empty_decision()
        else:
            if self.blocker_code is not ErrorCode.BLOCKED_NONCONSENSUAL_EVENT:
                raise ContractValidationError(
                    "blocked draft requires the supported blocker code"
                )
            if self.insufficiencies:
                raise ContractValidationError(
                    "blocked draft cannot carry insufficiencies"
                )
            self._require_empty_decision()

    def _require_empty_decision(self) -> None:
        if any(
            (
                self.route is not None,
                self.scene_intent is not None,
                bool(self.responding_npc_ids),
                self.floor_owner_id is not None,
                bool(self.participation),
                bool(self.character_moves),
                bool(self.current_beats),
                self.stop_before is not None,
                bool(self.future_segments),
                self.adult_craft_need is not None,
            )
        ):
            raise ContractValidationError(
                "non-ready reasoner draft cannot carry decision content"
            )

    @property
    def draft_sha256(self) -> str:
        return domain_sha256("cera.codex_reasoner_draft.v2", self)


@dataclass(frozen=True, slots=True)
class CodexReasonerDraftV3(CodexReasonerDraftV2):
    """Active semantic draft with explicit lexical craft obligations."""

    SCHEMA_VERSION: ClassVar[str] = "cera.codex_reasoner_draft.v3"

    adult_craft_need: AdultCraftNeedDraftV3 | None

    @property
    def draft_sha256(self) -> str:
        return domain_sha256("cera.codex_reasoner_draft.v3", self)


@dataclass(frozen=True, slots=True)
class DraftProtectedUserSourceClaimV4:
    source_unit_id: TypedId
    kind: ProtectedUserClaimKind
    quote: str

    def __post_init__(self) -> None:
        require_kind(self.source_unit_id, IdKind.SOURCE_UNIT, "source_unit_id")
        _non_empty(self.quote, "protected-user source quote")
        if len(self.quote) > 500:
            raise ContractValidationError(
                "protected-user source quote exceeds 500 characters"
            )


@dataclass(frozen=True, slots=True)
class CodexReasonerDraftV4(CodexReasonerDraftV3):
    """Active draft with exact-source protected-user allow-list anchors."""

    SCHEMA_VERSION: ClassVar[str] = "cera.codex_reasoner_draft.v4"

    protected_user_source_claims: tuple[DraftProtectedUserSourceClaimV4, ...]

    def __post_init__(self) -> None:
        # ``@dataclass(slots=True)`` replaces the decorated class object.  A
        # zero-argument ``super()`` therefore retains the pre-replacement
        # ``__class__`` cell and fails when a provider payload is decoded into
        # the active subclass.  Call the inherited validator explicitly so the
        # active DTO follows the same domain path under every Python 3.12
        # construction route.
        CodexReasonerDraftV2.__post_init__(self)
        _unique(
            (
                f"{value.source_unit_id}|{value.kind.value}|{value.quote}"
                for value in self.protected_user_source_claims
            ),
            "protected-user source claim anchors",
        )
        if self.status is not ReasonerOutcomeStatus.DECISION_READY and (
            self.protected_user_source_claims
        ):
            raise ContractValidationError(
                "non-ready draft cannot carry protected-user source claims"
            )

    @property
    def draft_sha256(self) -> str:
        return domain_sha256("cera.codex_reasoner_draft.v4", self)


@dataclass(frozen=True, slots=True)
class DraftSourceClaimV5:
    claim_key: str
    source_unit_id: TypedId
    kind: SourceClaimKind
    authority: SourceClaimAuthority
    quote: str
    normalized_meaning: str
    affected_character_ids: tuple[TypedId, ...]
    rationale: str

    def __post_init__(self) -> None:
        _local_key(self.claim_key, "claim_key")
        require_kind(self.source_unit_id, IdKind.SOURCE_UNIT, "source_unit_id")
        _non_empty(self.quote, "source claim quote")
        _non_empty(self.normalized_meaning, "source claim meaning")
        _non_empty(self.rationale, "source claim rationale")
        if len(self.quote) > 500:
            raise ContractValidationError("source claim quote exceeds 500 characters")
        for character_id in self.affected_character_ids:
            require_kind(character_id, IdKind.CHARACTER, "affected_character_ids")
        _unique(self.affected_character_ids, "source claim affected characters")


@dataclass(frozen=True, slots=True)
class DraftProtectedUserAllowanceV5:
    allowed_kinds: tuple[ProtectedUserAllowanceKind, ...]
    source_claim_keys: tuple[str, ...]
    explanation: str

    def __post_init__(self) -> None:
        _unique(self.allowed_kinds, "protected-user allowance kinds")
        for value in self.source_claim_keys:
            _local_key(value, "source_claim_keys")
        _unique(self.source_claim_keys, "protected-user allowance claim keys")
        _non_empty(self.explanation, "protected-user allowance explanation")


@dataclass(frozen=True, slots=True)
class DraftWriterScaffoldV5:
    viewpoint_character_ids: tuple[TypedId, ...]
    motivation_and_subtext: tuple[str, ...]
    voice_and_interiority: tuple[str, ...]
    physical_and_material_continuity: tuple[str, ...]
    transition_obligations: tuple[str, ...]
    open_realization_space: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.viewpoint_character_ids or not self.open_realization_space:
            raise ContractValidationError(
                "writer scaffold requires viewpoint and creative space"
            )
        for character_id in self.viewpoint_character_ids:
            require_kind(character_id, IdKind.CHARACTER, "viewpoint_character_ids")
        _unique(self.viewpoint_character_ids, "writer scaffold viewpoints")
        for field_name in (
            "motivation_and_subtext",
            "voice_and_interiority",
            "physical_and_material_continuity",
            "transition_obligations",
            "open_realization_space",
        ):
            _unique(getattr(self, field_name), field_name)


@dataclass(frozen=True, slots=True)
class DraftSceneEventBlockV5:
    block_key: str
    actor_ids: tuple[TypedId, ...]
    purpose: str
    causal_basis: str
    event_advances: tuple[str, ...]
    resulting_state: str
    evidence_ids: tuple[TypedId, ...]
    source_claim_keys: tuple[str, ...]
    protected_user_allowance: DraftProtectedUserAllowanceV5
    writer_scaffold: DraftWriterScaffoldV5

    def __post_init__(self) -> None:
        _local_key(self.block_key, "block_key")
        if not self.actor_ids or not self.event_advances:
            raise ContractValidationError("scene block requires actors and advances")
        for actor_id in self.actor_ids:
            if actor_id.kind not in {IdKind.CHARACTER, IdKind.MATERIAL}:
                raise ContractValidationError("scene block actor kind is invalid")
        for evidence_id in self.evidence_ids:
            require_kind(evidence_id, IdKind.EVIDENCE, "scene block evidence_ids")
        for value in self.source_claim_keys:
            _local_key(value, "source_claim_keys")
        _unique(self.actor_ids, "scene block actors")
        _unique(self.event_advances, "scene block advances")
        _unique(self.evidence_ids, "scene block evidence")
        _unique(self.source_claim_keys, "scene block source claims")
        _non_empty(self.purpose, "scene block purpose")
        _non_empty(self.causal_basis, "scene block causal basis")
        _non_empty(self.resulting_state, "scene block resulting state")


@dataclass(frozen=True, slots=True)
class DraftCausalRunwayV5:
    runway_class: SceneRunwayClass
    development_obligations: tuple[str, ...]
    continue_beyond_prompt_endpoint: bool
    stop_reason: NaturalStopReason
    stop_condition: str

    def __post_init__(self) -> None:
        if not self.development_obligations:
            raise ContractValidationError("causal runway needs development obligations")
        _unique(self.development_obligations, "causal runway obligations")
        _non_empty(self.stop_condition, "causal runway stop condition")


@dataclass(frozen=True, slots=True)
class CodexReasonerDraftV5:
    """Claim-aware broad-block draft for the active behavioral route."""

    SCHEMA_VERSION: ClassVar[str] = "cera.codex_reasoner_draft.v5"

    schema_version: str
    status: ReasonerOutcomeStatus
    route: DecisionRoute | None
    scene_intent: str | None
    responding_npc_ids: tuple[TypedId, ...]
    floor_owner_id: TypedId | None
    participation: tuple[DraftParticipationV2, ...]
    character_moves: tuple[DraftCharacterMoveV2, ...]
    source_claims: tuple[DraftSourceClaimV5, ...]
    event_blocks: tuple[DraftSceneEventBlockV5, ...]
    causal_runway: DraftCausalRunwayV5 | None
    interaction_topology: InteractionTopology | None
    scene_function: SceneFunction | None
    tone: PromptTone | None
    interiority_level: InteriorityLevel | None
    future_segments: tuple[DraftFutureSegmentV2, ...]
    writer_must_preserve: tuple[str, ...]
    uncertainties: tuple[str, ...]
    prohibited_inferences: tuple[str, ...]
    insufficiencies: tuple[str, ...]
    blocker_code: ErrorCode | None
    adult_craft_need: AdultCraftNeedDraftV3 | None
    protected_user_boundary_acknowledged: bool

    def __post_init__(self) -> None:
        require_schema(self.schema_version, self.SCHEMA_VERSION, type(self).__name__)
        if not self.protected_user_boundary_acknowledged:
            raise ContractValidationError(
                "reasoner draft must acknowledge protected-user ownership"
            )
        for character_id in self.responding_npc_ids:
            require_kind(character_id, IdKind.CHARACTER, "responding_npc_ids")
        _unique(self.responding_npc_ids, "responding NPCs")
        _unique((value.character_id for value in self.participation), "participation characters")
        _unique((value.character_id for value in self.character_moves), "move characters")
        _unique((value.claim_key for value in self.source_claims), "source claim keys")
        _unique((value.block_key for value in self.event_blocks), "scene block keys")
        _unique((value.segment_key for value in self.future_segments), "future segment keys")
        for field_name in (
            "writer_must_preserve",
            "uncertainties",
            "prohibited_inferences",
            "insufficiencies",
        ):
            _unique(getattr(self, field_name), field_name)
        if self.status is ReasonerOutcomeStatus.DECISION_READY:
            if (
                self.route is None
                or self.scene_intent is None
                or self.floor_owner_id is None
                or not self.responding_npc_ids
                or not self.source_claims
                or not self.event_blocks
                or self.causal_runway is None
                or self.interaction_topology is None
                or self.scene_function is None
                or self.tone is None
                or self.interiority_level is None
            ):
                raise ContractValidationError(
                    "decision-ready behavioral draft lacks required content"
                )
            if self.route not in {DecisionRoute.ORDINARY, DecisionRoute.CONSENT_VALID_ADULT}:
                raise ContractValidationError("behavioral draft cannot select aftermath")
            if self.insufficiencies or self.blocker_code is not None:
                raise ContractValidationError("ready behavioral draft carries failure state")
            if set(value.character_id for value in self.character_moves) != set(self.responding_npc_ids):
                raise ContractValidationError("draft moves must map one-to-one to responders")
            if set(value.character_id for value in self.participation) != set(self.responding_npc_ids):
                raise ContractValidationError("draft participation must map one-to-one to responders")
            if self.floor_owner_id not in self.responding_npc_ids:
                raise ContractValidationError("floor owner must be a responder")
            if len(self.responding_npc_ids) == 1 and self.interaction_topology is not InteractionTopology.SINGLE_NPC_FLOOR:
                raise ContractValidationError("single responder requires single-NPC topology")
            if len(self.responding_npc_ids) > 1 and self.interaction_topology is InteractionTopology.SINGLE_NPC_FLOOR:
                raise ContractValidationError("multiple responders cannot use single-NPC topology")
            for participant in self.participation:
                if participant.character_id != self.floor_owner_id and participant.intervention_reason is None:
                    raise ContractValidationError("secondary participant requires intervention reason")
            claim_keys = {value.claim_key for value in self.source_claims}
            selected = set(self.responding_npc_ids)
            for block in self.event_blocks:
                character_actors = {
                    value for value in block.actor_ids if value.kind is IdKind.CHARACTER
                }
                if not character_actors or not character_actors.issubset(selected):
                    raise ContractValidationError("scene block contains an unselected actor")
                if not set(block.source_claim_keys).issubset(claim_keys):
                    raise ContractValidationError("scene block cites an unknown source claim")
                if not set(block.protected_user_allowance.source_claim_keys).issubset(claim_keys):
                    raise ContractValidationError("protected-user allowance cites an unknown claim")
            block_keys = {value.block_key for value in self.event_blocks}
            if self.adult_craft_need is not None:
                if self.route is not DecisionRoute.CONSENT_VALID_ADULT:
                    raise ContractValidationError("adult craft requires an adult route")
                if not {value.beat_key for value in self.adult_craft_need.beat_requirements}.issubset(block_keys):
                    raise ContractValidationError("adult craft cites an unknown scene block")
            elif self.route is DecisionRoute.CONSENT_VALID_ADULT:
                raise ContractValidationError("adult route requires craft semantics")
        elif self.status is ReasonerOutcomeStatus.INSUFFICIENT_EVIDENCE:
            if not self.insufficiencies or self.blocker_code is not None:
                raise ContractValidationError("insufficient draft has invalid failure fields")
            self._require_empty_decision()
        else:
            if self.blocker_code is not ErrorCode.BLOCKED_NONCONSENSUAL_EVENT or self.insufficiencies:
                raise ContractValidationError("blocked draft has invalid failure fields")
            self._require_empty_decision()

    def _require_empty_decision(self) -> None:
        if any(
            (
                self.route is not None,
                self.scene_intent is not None,
                bool(self.responding_npc_ids),
                self.floor_owner_id is not None,
                bool(self.participation),
                bool(self.character_moves),
                bool(self.source_claims),
                bool(self.event_blocks),
                self.causal_runway is not None,
                self.interaction_topology is not None,
                self.scene_function is not None,
                self.tone is not None,
                self.interiority_level is not None,
                bool(self.future_segments),
                self.adult_craft_need is not None,
            )
        ):
            raise ContractValidationError("non-ready behavioral draft carries decision content")

    @property
    def draft_sha256(self) -> str:
        return domain_sha256(self.SCHEMA_VERSION, self)


@dataclass(frozen=True, slots=True)
class DraftDevelopmentAtomV6:
    """Provider-authored one-step interpretation bound to current scene blocks."""

    atom_key: str
    owner_character_id: TypedId
    kind: DevelopmentAtomKind
    strength_before: DevelopmentAtomStrength
    strength_after: DevelopmentAtomStrength
    summary: str
    source_block_keys: tuple[str, ...]
    evidence_ids: tuple[TypedId, ...]
    predecessor_development_ids: tuple[TypedId, ...]
    inference_limit: str

    def __post_init__(self) -> None:
        _local_key(self.atom_key, "atom_key")
        require_kind(self.owner_character_id, IdKind.CHARACTER, "owner_character_id")
        if not self.source_block_keys:
            raise ContractValidationError("development atom requires source blocks")
        for block_key in self.source_block_keys:
            _local_key(block_key, "source_block_keys")
        for evidence_id in self.evidence_ids:
            require_kind(evidence_id, IdKind.EVIDENCE, "development evidence_ids")
        for predecessor_id in self.predecessor_development_ids:
            require_kind(
                predecessor_id,
                IdKind.DEVELOPMENT,
                "predecessor_development_ids",
            )
        _unique(self.source_block_keys, "development source blocks")
        _unique(self.evidence_ids, "development evidence")
        _unique(self.predecessor_development_ids, "development predecessors")
        _non_empty(self.summary, "development atom summary")
        _non_empty(self.inference_limit, "development atom inference limit")
        order = tuple(DevelopmentAtomStrength)
        if order.index(self.strength_after) - order.index(self.strength_before) != 1:
            raise ContractValidationError(
                "draft development atom must advance exactly one strength level"
            )
        if (
            self.strength_before is DevelopmentAtomStrength.ABSENT
            and self.predecessor_development_ids
        ):
            raise ContractValidationError(
                "absent development cannot cite a predecessor"
            )
        if (
            self.strength_before is not DevelopmentAtomStrength.ABSENT
            and not self.predecessor_development_ids
        ):
            raise ContractValidationError(
                "existing development requires predecessor evidence"
            )


@dataclass(frozen=True, slots=True)
class CodexReasonerDraftV6(CodexReasonerDraftV5):
    """Behavioral draft with explicit, non-authoritative atomic development."""

    SCHEMA_VERSION: ClassVar[str] = "cera.codex_reasoner_draft.v6"

    development_atoms: tuple[DraftDevelopmentAtomV6, ...] = ()

    def __post_init__(self) -> None:
        CodexReasonerDraftV5.__post_init__(self)
        _unique((value.atom_key for value in self.development_atoms), "development atom keys")
        if self.status is not ReasonerOutcomeStatus.DECISION_READY:
            if self.development_atoms:
                raise ContractValidationError(
                    "non-ready behavioral draft carries development atoms"
                )
            return
        selected = set(self.responding_npc_ids)
        block_keys = {value.block_key for value in self.event_blocks}
        available_evidence = {
            evidence_id
            for block in self.event_blocks
            for evidence_id in block.evidence_ids
        }
        available_evidence.update(
            evidence_id
            for move in self.character_moves
            for evidence_id in move.evidence_ids
        )
        for atom in self.development_atoms:
            if atom.owner_character_id not in selected:
                raise ContractValidationError(
                    "development atom owner is not a selected responder"
                )
            if not set(atom.source_block_keys).issubset(block_keys):
                raise ContractValidationError(
                    "development atom cites an unknown source block"
                )
            if not set(atom.evidence_ids).issubset(available_evidence):
                raise ContractValidationError(
                    "development atom cites evidence unused by its scene plan"
                )


def compile_reasoner_draft(
    request: SceneReasonerRequest,
    draft: (
        CodexReasonerDraftV2
        | CodexReasonerDraftV3
        | CodexReasonerDraftV4
        | CodexReasonerDraftV5
        | CodexReasonerDraftV6
    ),
    *,
    authorized_exact_evidence: tuple[ExactEvidence, ...],
) -> ReasonerOutcome:
    """Compile model semantics into Python-owned canonical domain records."""

    if isinstance(draft, CodexReasonerDraftV5):
        return _compile_reasoner_draft_v5(
            request,
            draft,
            authorized_exact_evidence=authorized_exact_evidence,
        )

    if draft.status is not ReasonerOutcomeStatus.DECISION_READY:
        return ReasonerOutcome(
            schema_version=ReasonerOutcome.SCHEMA_VERSION,
            status=draft.status,
            decision=None,
            participation=(),
            hard_citations=(),
            insufficiencies=draft.insufficiencies,
            blocker_code=draft.blocker_code,
            advisory_state_deltas=(),
            protected_user_boundary_acknowledged=True,
            adult_craft_need=None,
            protected_user_source_claims=(),
        )

    source_claims = _compile_protected_user_source_claims(request, draft)

    available = {value.evidence_id: value for value in authorized_exact_evidence}
    used_evidence = tuple(
        dict.fromkeys(
            (
                *(
                    evidence_id
                    for value in draft.participation
                    for evidence_id in value.evidence_ids
                ),
                *(
                    evidence_id
                    for value in draft.character_moves
                    for evidence_id in value.evidence_ids
                ),
                *(
                    evidence_id
                    for value in draft.current_beats
                    for evidence_id in value.evidence_ids
                ),
            )
        )
    )
    missing = tuple(value for value in used_evidence if value not in available)
    if missing:
        raise ContractValidationError(
            "reasoner draft cites evidence that was never exactly authorized"
        )

    decision_id = deterministic_id(
        IdKind.DECISION,
        "cera.scene_decision.v2",
        f"{request.request_sha256}|{draft.draft_sha256}",
    )
    current_segment_id = deterministic_id(
        IdKind.SEGMENT,
        "cera.current_segment.v2",
        f"{decision_id}|current",
    )
    beat_ids = {
        value.beat_key: deterministic_id(
            IdKind.BEAT,
            "cera.sequence_beat.v2",
            f"{decision_id}|{value.beat_key}",
        )
        for value in draft.current_beats
    }
    current_beats = tuple(
        SequenceBeat(
            beat_id=beat_ids[value.beat_key],
            actor_id=value.actor_id,
            state=value.state,
            neutral_event=value.neutral_event,
            evidence_ids=value.evidence_ids,
        )
        for value in draft.current_beats
    )
    future_segments = tuple(
        FutureSegment(
            segment_id=deterministic_id(
                IdKind.SEGMENT,
                "cera.future_segment.v2",
                f"{decision_id}|{value.segment_key}",
            ),
            status="conditional_plan_only",
            activation_conditions=value.activation_conditions,
            invalidation_conditions=value.invalidation_conditions,
            possible_consequences=value.possible_consequences,
            open_user_choice=value.open_user_choice,
        )
        for value in draft.future_segments
    )
    decision = SceneDecision(
        schema_version=SceneDecision.SCHEMA_VERSION,
        decision_id=decision_id,
        route=draft.route,
        scene_intent=draft.scene_intent or "",
        responding_npc_ids=draft.responding_npc_ids,
        floor_owner_id=draft.floor_owner_id,
        character_moves=tuple(
            CharacterMove(
                character_id=value.character_id,
                perception=value.perception,
                selected_intent=value.selected_intent,
                action_direction=value.action_direction,
                evidence_ids=value.evidence_ids,
                knowledge_constraints=value.knowledge_constraints,
            )
            for value in draft.character_moves
        ),
        current_segment=CurrentSegment(
            segment_id=current_segment_id,
            ordered_beats=current_beats,
            stop_before=draft.stop_before or "",
        ),
        future_segments=future_segments,
        writer_must_preserve=draft.writer_must_preserve,
        uncertainties=draft.uncertainties,
        prohibited_inferences=draft.prohibited_inferences,
        advisory_state_candidates=(),
    )
    participation = tuple(
        ParticipationSelection(
            character_id=value.character_id,
            role=(
                ParticipationRole.LEAD
                if value.character_id == draft.floor_owner_id
                else ParticipationRole.SECONDARY
            ),
            intervention_reason=(
                InterventionReason.FLOOR_OWNER
                if value.character_id == draft.floor_owner_id
                else value.intervention_reason
            ),
            evidence_ids=value.evidence_ids,
        )
        for value in draft.participation
    )
    citations = tuple(
        ReasonerEvidenceCitation(
            evidence_id=evidence_id,
            record_id=available[evidence_id].metadata.record_id,
            record_version=available[evidence_id].metadata.record_version,
        )
        for evidence_id in used_evidence
    )
    adult_need = (
        _compile_adult_craft_need(
            request,
            decision,
            draft.adult_craft_need,
            beat_ids,
        )
        if draft.adult_craft_need is not None
        else None
    )
    return ReasonerOutcome(
        schema_version=ReasonerOutcome.SCHEMA_VERSION,
        status=draft.status,
        decision=decision,
        participation=participation,
        hard_citations=citations,
        insufficiencies=(),
        blocker_code=None,
        advisory_state_deltas=(),
        protected_user_boundary_acknowledged=True,
        adult_craft_need=adult_need,
        protected_user_source_claims=source_claims,
    )


def _compile_reasoner_draft_v5(
    request: SceneReasonerRequest,
    draft: CodexReasonerDraftV5,
    *,
    authorized_exact_evidence: tuple[ExactEvidence, ...],
) -> ReasonerOutcome:
    if draft.status is not ReasonerOutcomeStatus.DECISION_READY:
        return ReasonerOutcome(
            schema_version=ReasonerOutcome.SCHEMA_VERSION,
            status=draft.status,
            decision=None,
            participation=(),
            hard_citations=(),
            insufficiencies=draft.insufficiencies,
            blocker_code=draft.blocker_code,
            advisory_state_deltas=(),
            protected_user_boundary_acknowledged=True,
            adult_craft_need=None,
            protected_user_source_claims=(),
            behavioral_scene_plan=None,
        )

    units = {value.source_unit_id: value for value in request.source_view.units}
    present = set(request.prepared_turn.present_character_ids)
    claim_ids: dict[str, TypedId] = {}
    claims: list[SourceClaimDecision] = []
    protected_claims: list[ProtectedUserSourceClaim] = []
    for value in draft.source_claims:
        unit = units.get(value.source_unit_id)
        if unit is None:
            raise ContractValidationError("source claim names an unknown source unit")
        if unit.safe_text.count(value.quote) != 1:
            raise ContractValidationError("source claim quote must occur exactly once")
        if not set(value.affected_character_ids).issubset(present):
            raise ContractValidationError("source claim affects a character outside the cast")
        start = unit.safe_text.index(value.quote)
        claim_id = deterministic_id(
            IdKind.SOURCE_CLAIM,
            "cera.source_claim.v1",
            f"{request.request_sha256}|{value.claim_key}|{value.quote}",
        )
        claim_ids[value.claim_key] = claim_id
        claims.append(
            SourceClaimDecision(
                claim_id=claim_id,
                source_unit_id=value.source_unit_id,
                kind=value.kind,
                authority=value.authority,
                start=start,
                end=start + len(value.quote),
                exact_text_sha256=text_sha256(value.quote),
                normalized_meaning=value.normalized_meaning,
                affected_character_ids=value.affected_character_ids,
                rationale=value.rationale,
            )
        )
        protected_kind = {
            SourceClaimKind.PROTECTED_USER_ACTION: ProtectedUserClaimKind.ACTION,
            SourceClaimKind.PROTECTED_USER_DIALOGUE: ProtectedUserClaimKind.DIALOGUE,
        }.get(value.kind)
        if (
            protected_kind is not None
            and value.authority is SourceClaimAuthority.SOURCE_AUTHORIZED
        ):
            protected_claims.append(
                ProtectedUserSourceClaim(
                    source_unit_id=value.source_unit_id,
                    kind=protected_kind,
                    start=start,
                    end=start + len(value.quote),
                    exact_text_sha256=text_sha256(value.quote),
                )
            )
    compiled_claims = tuple(claims)
    validate_claim_authority(request.behavioral_controls, compiled_claims)
    claim_ledger = SourceClaimLedger(
        schema_version=SourceClaimLedger.SCHEMA_VERSION,
        source_sha256=request.source_view.source_sha256,
        controls_sha256=request.behavioral_controls.controls_sha256,
        claims=compiled_claims,
        unresolved_questions=draft.uncertainties,
        prohibited_inferences=draft.prohibited_inferences,
    )

    available = {value.evidence_id: value for value in authorized_exact_evidence}
    draft_atoms = (
        draft.development_atoms
        if isinstance(draft, CodexReasonerDraftV6)
        else ()
    )
    used_evidence = tuple(
        dict.fromkeys(
            (
                *(evidence_id for value in draft.participation for evidence_id in value.evidence_ids),
                *(evidence_id for value in draft.character_moves for evidence_id in value.evidence_ids),
                *(evidence_id for value in draft.event_blocks for evidence_id in value.evidence_ids),
                *(evidence_id for value in draft_atoms for evidence_id in value.evidence_ids),
            )
        )
    )
    if any(value not in available for value in used_evidence):
        raise ContractValidationError(
            "behavioral reasoner draft cites evidence that was never exactly authorized"
        )

    decision_id = deterministic_id(
        IdKind.DECISION,
        "cera.scene_decision.v3",
        f"{request.request_sha256}|{draft.draft_sha256}",
    )
    block_ids = {
        value.block_key: deterministic_id(
            IdKind.SCENE_BLOCK,
            "cera.scene_event_block.v1",
            f"{decision_id}|{value.block_key}",
        )
        for value in draft.event_blocks
    }
    beat_ids = {
        key: deterministic_id(
            IdKind.BEAT,
            "cera.sequence_beat.behavioral.v1",
            f"{decision_id}|{key}",
        )
        for key in block_ids
    }
    compiled_blocks = tuple(
        SceneEventBlock(
            block_id=block_ids[value.block_key],
            actor_ids=value.actor_ids,
            purpose=value.purpose,
            causal_basis=value.causal_basis,
            event_advances=value.event_advances,
            resulting_state=value.resulting_state,
            evidence_ids=value.evidence_ids,
            source_claim_ids=tuple(claim_ids[key] for key in value.source_claim_keys),
            protected_user_allowance=ProtectedUserRealizationAllowance(
                allowed_kinds=value.protected_user_allowance.allowed_kinds,
                source_claim_ids=tuple(
                    claim_ids[key]
                    for key in value.protected_user_allowance.source_claim_keys
                ),
                explanation=value.protected_user_allowance.explanation,
            ),
            writer_scaffold=WriterScaffold(
                scaffold_id=deterministic_id(
                    IdKind.WRITER_SCAFFOLD,
                    "cera.writer_scaffold.v1",
                    f"{decision_id}|{value.block_key}",
                ),
                viewpoint_character_ids=value.writer_scaffold.viewpoint_character_ids,
                motivation_and_subtext=value.writer_scaffold.motivation_and_subtext,
                voice_and_interiority=value.writer_scaffold.voice_and_interiority,
                physical_and_material_continuity=(
                    value.writer_scaffold.physical_and_material_continuity
                ),
                transition_obligations=value.writer_scaffold.transition_obligations,
                open_realization_space=value.writer_scaffold.open_realization_space,
            ),
        )
        for value in draft.event_blocks
    )
    available_development_record_ids = {
        value.metadata.record_id
        for value in authorized_exact_evidence
        if value.metadata.record_type.value == "development"
    }
    for atom in draft_atoms:
        if not set(atom.predecessor_development_ids).issubset(
            available_development_record_ids
        ):
            raise ContractValidationError(
                "development predecessor was not exactly authorized"
            )
    compiled_atoms = tuple(
        DevelopmentAtomProposal(
            atom_id=deterministic_id(
                IdKind.DEVELOPMENT,
                "cera.development_atom_proposal.v1",
                f"{decision_id}|{value.atom_key}",
            ),
            owner_character_id=value.owner_character_id,
            kind=value.kind,
            strength_before=value.strength_before,
            strength_after=value.strength_after,
            summary=value.summary,
            source_scene_block_ids=tuple(
                block_ids[key] for key in value.source_block_keys
            ),
            evidence_record_ids=tuple(
                available[evidence_id].metadata.record_id
                for evidence_id in value.evidence_ids
            ),
            predecessor_development_ids=value.predecessor_development_ids,
            inference_limit=value.inference_limit,
        )
        for value in draft_atoms
    )
    assert draft.causal_runway is not None
    runway = CausalRunwayContract(
        runway_class=draft.causal_runway.runway_class,
        development_obligations=draft.causal_runway.development_obligations,
        continue_beyond_prompt_endpoint=(
            draft.causal_runway.continue_beyond_prompt_endpoint
        ),
        stop_reason=draft.causal_runway.stop_reason,
        stop_condition=draft.causal_runway.stop_condition,
    )
    selected = draft.responding_npc_ids
    eligible = request.prepared_turn.eligible_responding_npc_ids
    behavioral_plan = BehavioralScenePlan(
        schema_version=BehavioralScenePlan.SCHEMA_VERSION,
        claim_ledger=claim_ledger,
        event_blocks=compiled_blocks,
        runway=runway,
        interaction_topology=draft.interaction_topology,
        scene_function=draft.scene_function,
        tone=draft.tone,
        interiority_level=draft.interiority_level,
        selected_character_ids=selected,
        omitted_character_ids=tuple(value for value in eligible if value not in set(selected)),
        development_atoms=compiled_atoms,
    )

    current_beats = tuple(
        SequenceBeat(
            beat_id=beat_ids[value.block_key],
            actor_id=value.actor_ids[0],
            state=BeatState.ONGOING,
            neutral_event=value.purpose,
            evidence_ids=value.evidence_ids,
        )
        for value in draft.event_blocks
    )
    future_segments = tuple(
        FutureSegment(
            segment_id=deterministic_id(
                IdKind.SEGMENT,
                "cera.future_segment.v3",
                f"{decision_id}|{value.segment_key}",
            ),
            status="conditional_plan_only",
            activation_conditions=value.activation_conditions,
            invalidation_conditions=value.invalidation_conditions,
            possible_consequences=value.possible_consequences,
            open_user_choice=value.open_user_choice,
        )
        for value in draft.future_segments
    )
    decision = SceneDecision(
        schema_version=SceneDecision.SCHEMA_VERSION,
        decision_id=decision_id,
        route=draft.route,
        scene_intent=draft.scene_intent or "",
        responding_npc_ids=selected,
        floor_owner_id=draft.floor_owner_id,
        character_moves=tuple(
            CharacterMove(
                character_id=value.character_id,
                perception=value.perception,
                selected_intent=value.selected_intent,
                action_direction=value.action_direction,
                evidence_ids=value.evidence_ids,
                knowledge_constraints=value.knowledge_constraints,
            )
            for value in draft.character_moves
        ),
        current_segment=CurrentSegment(
            segment_id=deterministic_id(
                IdKind.SEGMENT,
                "cera.current_segment.v3",
                f"{decision_id}|current",
            ),
            ordered_beats=current_beats,
            stop_before=runway.stop_condition,
        ),
        future_segments=future_segments,
        writer_must_preserve=draft.writer_must_preserve,
        uncertainties=draft.uncertainties,
        prohibited_inferences=draft.prohibited_inferences,
        advisory_state_candidates=(),
    )
    participation = tuple(
        ParticipationSelection(
            character_id=value.character_id,
            role=(
                ParticipationRole.LEAD
                if value.character_id == draft.floor_owner_id
                else ParticipationRole.SECONDARY
            ),
            intervention_reason=(
                InterventionReason.FLOOR_OWNER
                if value.character_id == draft.floor_owner_id
                else value.intervention_reason
            ),
            evidence_ids=value.evidence_ids,
        )
        for value in draft.participation
    )
    citations = tuple(
        ReasonerEvidenceCitation(
            evidence_id=evidence_id,
            record_id=available[evidence_id].metadata.record_id,
            record_version=available[evidence_id].metadata.record_version,
        )
        for evidence_id in used_evidence
    )
    adult_need = (
        _compile_adult_craft_need(
            request,
            decision,
            draft.adult_craft_need,
            beat_ids,
        )
        if draft.adult_craft_need is not None
        else None
    )
    return ReasonerOutcome(
        schema_version=ReasonerOutcome.SCHEMA_VERSION,
        status=draft.status,
        decision=decision,
        participation=participation,
        hard_citations=citations,
        insufficiencies=(),
        blocker_code=None,
        advisory_state_deltas=(),
        protected_user_boundary_acknowledged=True,
        adult_craft_need=adult_need,
        protected_user_source_claims=tuple(protected_claims),
        behavioral_scene_plan=behavioral_plan,
    )


def _compile_protected_user_source_claims(
    request: SceneReasonerRequest,
    draft: CodexReasonerDraftV2 | CodexReasonerDraftV3 | CodexReasonerDraftV4,
) -> tuple[ProtectedUserSourceClaim, ...]:
    supplied = tuple(getattr(draft, "protected_user_source_claims", ()))
    if not supplied:
        return ()
    units = {value.source_unit_id: value for value in request.source_view.units}
    compiled: list[ProtectedUserSourceClaim] = []
    for value in supplied:
        unit = units.get(value.source_unit_id)
        if unit is None:
            raise ContractValidationError(
                "protected-user source claim names an unknown source unit"
            )
        if request.source_view.mode.value != "ordinary_exact":
            raise ContractValidationError(
                "protected-user quote claims require ordinary exact source"
            )
        if unit.safe_text.count(value.quote) != 1:
            raise ContractValidationError(
                "protected-user source quote must occur exactly once"
            )
        start = unit.safe_text.index(value.quote)
        compiled.append(
            ProtectedUserSourceClaim(
                source_unit_id=value.source_unit_id,
                kind=value.kind,
                start=start,
                end=start + len(value.quote),
                exact_text_sha256=text_sha256(value.quote),
            )
        )
    return tuple(compiled)


def _compile_adult_craft_need(
    request: SceneReasonerRequest,
    decision: SceneDecision,
    draft: AdultCraftNeedDraftV2 | AdultCraftNeedDraftV3,
    beat_ids: dict[str, TypedId],
) -> AdultCraftNeedV2 | AdultCraftNeedV3:
    unknown = set(value.beat_key for value in draft.beat_requirements) - set(beat_ids)
    if unknown:
        raise ContractValidationError(
            "adult craft draft names a future or invented beat key"
        )
    beats_by_key = {
        value.beat_key: value for value in request_draft_beats(decision, beat_ids)
    }
    requirement_type = (
        BeatCraftRequirementV3
        if isinstance(draft, AdultCraftNeedDraftV3)
        else BeatCraftRequirementV2
    )
    requirements = tuple(
        requirement_type(
            beat_id=beat_ids[value.beat_key],
            actor_id=beats_by_key[value.beat_key].actor_id,
            action_family=value.action_family,
            object_concepts=value.object_concepts,
            axes=value.axes,
            channel_requirements=value.channel_requirements,
            priority=value.priority,
        )
        for value in draft.beat_requirements
    )
    sequence_sha256 = domain_sha256(
        "cera.sequence_plan.v1",
        (decision.current_segment, decision.future_segments),
    )
    need_type = (
        AdultCraftNeedV3
        if isinstance(draft, AdultCraftNeedDraftV3)
        else AdultCraftNeedV2
    )
    return need_type(
        schema_version=need_type.SCHEMA_VERSION,
        craft_need_id=deterministic_id(
            IdKind.ADULT_CRAFT_NEED,
            need_type.SCHEMA_VERSION,
            f"{request.prepared_turn.request.request_id}|{decision.decision_id}|{sequence_sha256}",
        ),
        request_id=request.prepared_turn.request.request_id,
        decision_id=decision.decision_id,
        sequence_plan_sha256=sequence_sha256,
        mode=draft.mode,
        families=draft.families,
        subfamilies=draft.subfamilies,
        beat_requirements=requirements,
        climax=draft.climax,
        aftermath=draft.aftermath,
        character_card_sections=draft.character_card_sections,
    )


def request_draft_beats(
    decision: SceneDecision, beat_ids: dict[str, TypedId]
) -> tuple[DraftSequenceBeatV2, ...]:
    """Reconstruct key/actor bindings without exposing provider IDs.

    The dict preserves insertion order from the original draft compiler.
    """

    by_id = {value.beat_id: value for value in decision.current_segment.ordered_beats}
    return tuple(
        DraftSequenceBeatV2(
            beat_key=key,
            actor_id=by_id[beat_id].actor_id,
            state=by_id[beat_id].state,
            neutral_event=by_id[beat_id].neutral_event,
            evidence_ids=by_id[beat_id].evidence_ids,
        )
        for key, beat_id in beat_ids.items()
    )


def codex_reasoner_draft_v2_json_schema() -> dict[str, object]:
    """Closed provider schema containing only model-owned semantics."""

    from cera.adult_craft.models import (
        AdultCraftConcept,
        OutcomeAuthorityState,
        RealizationChannel,
        SegmentCommitment,
        SpecificityRegister,
    )

    text = {"type": "string", "minLength": 1, "maxLength": 500}
    nullable_text = {"anyOf": [text, {"type": "null"}]}
    nullable_character = {
        "anyOf": [_id_schema(IdKind.CHARACTER), {"type": "null"}]
    }
    evidence_ids = _array(
        _id_schema(IdKind.EVIDENCE),
        16,
        semantic_set=True,
    )
    participation = _closed(
        {
            "character_id": _id_schema(IdKind.CHARACTER),
            "intervention_reason": {
                "anyOf": [
                    {
                        "enum": [
                            value.value
                            for value in InterventionReason
                            if value is not InterventionReason.FLOOR_OWNER
                        ]
                    },
                    {"type": "null"},
                ]
            },
            "evidence_ids": evidence_ids,
        }
    )
    move = _closed(
        {
            "character_id": _id_schema(IdKind.CHARACTER),
            "perception": text,
            "selected_intent": text,
            "action_direction": text,
            "evidence_ids": evidence_ids,
            "knowledge_constraints": _array(text, 24, semantic_set=True),
        }
    )
    beat = _closed(
        {
            "beat_key": _local_key_schema(),
            "actor_id": _id_schema(IdKind.CHARACTER, IdKind.MATERIAL),
            "state": {"enum": [value.value for value in BeatState]},
            "neutral_event": text,
            "evidence_ids": evidence_ids,
        }
    )
    future = _closed(
        {
            "segment_key": _local_key_schema(),
            "activation_conditions": _array(text, 16, semantic_set=True),
            "invalidation_conditions": _array(text, 16, semantic_set=True),
            "possible_consequences": _array(text, 16, semantic_set=True),
            "open_user_choice": text,
        }
    )
    character_channel = _closed(
        {
            "channel": {
                "enum": [
                    RealizationChannel.DIALOGUE.value,
                    RealizationChannel.INNER_VOICE.value,
                ]
            },
            "character_id": _id_schema(IdKind.CHARACTER),
            "minimum_register": {
                "enum": [value.value for value in SpecificityRegister]
            },
            "required_concepts": _array(
                {"enum": [value.value for value in AdultCraftConcept]},
                20,
                semantic_set=True,
            ),
        }
    )
    scene_channel = _closed(
        {
            "channel": {
                "enum": [
                    RealizationChannel.NARRATION.value,
                    RealizationChannel.SOUND_EFFECT.value,
                    RealizationChannel.PHYSIOLOGY.value,
                ]
            },
            "minimum_register": {
                "enum": [value.value for value in SpecificityRegister]
            },
            "required_concepts": _array(
                {"enum": [value.value for value in AdultCraftConcept]},
                20,
                semantic_set=True,
            ),
        }
    )
    adult_beat = _closed(
        {
            "beat_key": _local_key_schema(),
            "action_family": {"type": "string", "minLength": 1, "maxLength": 120},
            "object_concepts": _array(
                {"enum": [value.value for value in AdultCraftConcept]},
                20,
                semantic_set=True,
            ),
            "axes": _array(
                {"enum": [value.value for value in AdultCraftAxis]},
                10,
                minimum=1,
                semantic_set=True,
            ),
            "channel_requirements": _array(
                {"oneOf": [character_channel, scene_channel]}, 10, minimum=1
            ),
            "priority": {"type": "integer", "minimum": 1, "maximum": 5},
        }
    )
    outcome = _closed(
        {
            "authority_state": {
                "enum": [value.value for value in OutcomeAuthorityState]
            },
            "current_segment_commitment": {
                "enum": [value.value for value in SegmentCommitment]
            },
        }
    )
    adult_need = _closed(
        {
            "mode": {"enum": [value.value for value in AdultCraftMode]},
            "families": _array(
                {"enum": [value.value for value in AdultCraftFamily]},
                8,
                minimum=1,
                semantic_set=True,
            ),
            "subfamilies": _array(
                {"type": "string", "minLength": 1, "maxLength": 120},
                24,
                semantic_set=True,
            ),
            "beat_requirements": _array(adult_beat, 32, minimum=1),
            "climax": outcome,
            "aftermath": outcome,
            "character_card_sections": _array(
                _closed(
                    {
                        "character_id": _id_schema(IdKind.CHARACTER),
                        "section_queries": _array(
                            {
                                "type": "string",
                                "minLength": 1,
                                "maxLength": 120,
                            },
                            24,
                            minimum=1,
                            semantic_set=True,
                        ),
                    }
                ),
                8,
            ),
        }
    )
    properties = {
        "schema_version": {"const": CodexReasonerDraftV2.SCHEMA_VERSION},
        "status": {"enum": [value.value for value in ReasonerOutcomeStatus]},
        "route": {
            "anyOf": [
                {
                    "enum": [
                        DecisionRoute.ORDINARY.value,
                        DecisionRoute.CONSENT_VALID_ADULT.value,
                    ]
                },
                {"type": "null"},
            ]
        },
        "scene_intent": nullable_text,
        "responding_npc_ids": _array(
            _id_schema(IdKind.CHARACTER), 8, semantic_set=True
        ),
        "floor_owner_id": nullable_character,
        "participation": _array(participation, 8),
        "character_moves": _array(move, 8),
        "current_beats": _array(beat, 32),
        "stop_before": nullable_text,
        "future_segments": _array(future, 16),
        "writer_must_preserve": _array(text, 32, semantic_set=True),
        "uncertainties": _array(text, 32, semantic_set=True),
        "prohibited_inferences": _array(text, 32, semantic_set=True),
        "insufficiencies": _array(text, 32, semantic_set=True),
        "blocker_code": {
            "anyOf": [
                {"const": ErrorCode.BLOCKED_NONCONSENSUAL_EVENT.value},
                {"type": "null"},
            ]
        },
        "adult_craft_need": {
            "anyOf": [adult_need, {"type": "null"}]
        },
        "protected_user_boundary_acknowledged": {"const": True},
    }
    return _add_structured_output_types({
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        **_closed(properties),
    })


def codex_reasoner_draft_v3_json_schema() -> dict[str, object]:
    """Active provider schema with semantic/lexical craft concepts split."""

    schema = codex_reasoner_draft_v2_json_schema()
    schema["properties"]["schema_version"] = {
        "type": "string",
        "const": CodexReasonerDraftV3.SCHEMA_VERSION,
    }
    adult = schema["properties"]["adult_craft_need"]["anyOf"][0]
    channels = adult["properties"]["beat_requirements"]["items"]["properties"][
        "channel_requirements"
    ]["items"]["oneOf"]
    for channel_schema in channels:
        properties = channel_schema["properties"]
        concepts = properties.pop("required_concepts")
        properties["semantic_concepts"] = {
            **concepts,
            "description": (
                "Semantic craft needs used for technique selection and semantic "
                "verification; distinct values; these do not require exact words."
            ),
        }
        properties["lexical_concepts"] = {
            **concepts,
            "description": (
                "Distinct subset of semantic_concepts that genuinely requires "
                "explicit vocabulary in this channel; use an empty array when "
                "semantic realization is sufficient."
            ),
        }
        channel_schema["required"] = list(properties)
    return schema


def codex_reasoner_draft_v4_json_schema(
    *,
    allowed_evidence_ids: tuple[TypedId, ...] | None = None,
) -> dict[str, object]:
    """Active provider schema with exact-source user-claim allow-list anchors."""

    schema = codex_reasoner_draft_v3_json_schema()
    schema["properties"]["schema_version"] = {
        "type": "string",
        "const": CodexReasonerDraftV4.SCHEMA_VERSION,
    }
    schema["properties"]["protected_user_source_claims"] = _array(
        _closed(
            {
                "source_unit_id": _id_schema(IdKind.SOURCE_UNIT),
                "kind": {
                    "type": "string",
                    "enum": [value.value for value in ProtectedUserClaimKind],
                },
                "quote": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 500,
                    "description": (
                        "Exact uniquely occurring text copied from the named ordinary "
                        "source unit. This authorizes only that supplied user action or "
                        "dialogue; use an empty array when no protected-user wording "
                        "needs realization."
                    ),
                },
            }
        ),
        32,
        semantic_set=True,
    )
    schema["required"].append("protected_user_source_claims")
    if allowed_evidence_ids is not None:
        if any(value.kind is not IdKind.EVIDENCE for value in allowed_evidence_ids):
            raise ContractValidationError(
                "reasoner provider evidence allow-list requires evidence IDs"
            )
        rendered = tuple(dict.fromkeys(str(value) for value in allowed_evidence_ids))
        evidence_arrays = (
            schema["properties"]["participation"]["items"]["properties"]["evidence_ids"],
            schema["properties"]["character_moves"]["items"]["properties"]["evidence_ids"],
            schema["properties"]["current_beats"]["items"]["properties"]["evidence_ids"],
        )
        for evidence_array in evidence_arrays:
            if rendered:
                evidence_array["items"] = {
                    "type": "string",
                    "enum": list(rendered),
                    "description": (
                        "Exact request-authorized evidence identity; do not invent or "
                        "transform an ID."
                    ),
                }
            else:
                evidence_array["maxItems"] = 0
    return schema


def codex_reasoner_draft_v5_json_schema(
    *,
    allowed_evidence_ids: tuple[TypedId, ...] | None = None,
) -> dict[str, object]:
    """Claim-aware broad-block provider schema for the behavioral route."""

    schema = codex_reasoner_draft_v4_json_schema(
        allowed_evidence_ids=allowed_evidence_ids
    )
    properties = schema["properties"]
    properties["schema_version"] = {
        "type": "string",
        "const": CodexReasonerDraftV5.SCHEMA_VERSION,
    }
    for removed in ("current_beats", "stop_before", "protected_user_source_claims"):
        properties.pop(removed)

    claim_schema = _closed(
        {
            "claim_key": _local_key_schema(),
            "source_unit_id": _id_schema(IdKind.SOURCE_UNIT),
            "kind": {
                "type": "string",
                "enum": [value.value for value in SourceClaimKind],
            },
            "authority": {
                "type": "string",
                "enum": [value.value for value in SourceClaimAuthority],
            },
            "quote": {
                "type": "string",
                "minLength": 1,
                "maxLength": 500,
                "description": "Exact uniquely occurring quote from the named source unit.",
            },
            "normalized_meaning": {"type": "string", "minLength": 1},
            "affected_character_ids": _array(
                _id_schema(IdKind.CHARACTER),
                8,
                semantic_set=True,
            ),
            "rationale": {"type": "string", "minLength": 1},
        }
    )
    allowance_schema = _closed(
        {
            "allowed_kinds": _array(
                {
                    "type": "string",
                    "enum": [value.value for value in ProtectedUserAllowanceKind],
                },
                4,
                semantic_set=True,
            ),
            "source_claim_keys": _array(
                _local_key_schema(),
                16,
                semantic_set=True,
            ),
            "explanation": {"type": "string", "minLength": 1},
        }
    )
    string_set = lambda maximum=16: _array(
        {"type": "string", "minLength": 1},
        maximum,
        semantic_set=True,
    )
    scaffold_schema = _closed(
        {
            "viewpoint_character_ids": _array(
                _id_schema(IdKind.CHARACTER),
                8,
                minimum=1,
                semantic_set=True,
            ),
            "motivation_and_subtext": string_set(),
            "voice_and_interiority": string_set(),
            "physical_and_material_continuity": string_set(),
            "transition_obligations": string_set(),
            "open_realization_space": _array(
                {"type": "string", "minLength": 1},
                16,
                minimum=1,
                semantic_set=True,
            ),
        }
    )
    event_evidence = _array(
        _id_schema(IdKind.EVIDENCE),
        32,
        semantic_set=True,
    )
    properties["source_claims"] = _array(
        claim_schema,
        64,
        semantic_set=True,
    )
    properties["event_blocks"] = _array(
        _closed(
            {
                "block_key": _local_key_schema(),
                "actor_ids": _array(
                    {
                        "type": "string",
                        "pattern": "^(character|material):[A-Za-z0-9][A-Za-z0-9._-]{0,127}$",
                    },
                    8,
                    minimum=1,
                    semantic_set=True,
                ),
                "purpose": {"type": "string", "minLength": 1},
                "causal_basis": {"type": "string", "minLength": 1},
                "event_advances": _array(
                    {"type": "string", "minLength": 1},
                    24,
                    minimum=1,
                    semantic_set=True,
                ),
                "resulting_state": {"type": "string", "minLength": 1},
                "evidence_ids": event_evidence,
                "source_claim_keys": _array(
                    _local_key_schema(),
                    32,
                    semantic_set=True,
                ),
                "protected_user_allowance": allowance_schema,
                "writer_scaffold": scaffold_schema,
            }
        ),
        16,
    )
    properties["causal_runway"] = {
        "anyOf": [
            _closed(
                {
                    "runway_class": {
                        "type": "string",
                        "enum": [value.value for value in SceneRunwayClass],
                    },
                    "development_obligations": _array(
                        {"type": "string", "minLength": 1},
                        16,
                        minimum=1,
                        semantic_set=True,
                    ),
                    "continue_beyond_prompt_endpoint": {"type": "boolean"},
                    "stop_reason": {
                        "type": "string",
                        "enum": [value.value for value in NaturalStopReason],
                    },
                    "stop_condition": {"type": "string", "minLength": 1},
                }
            ),
            {"type": "null"},
        ]
    }
    for name, enum_type in (
        ("interaction_topology", InteractionTopology),
        ("scene_function", SceneFunction),
        ("tone", PromptTone),
        ("interiority_level", InteriorityLevel),
    ):
        properties[name] = {
            "anyOf": [
                {"type": "string", "enum": [value.value for value in enum_type]},
                {"type": "null"},
            ]
        }
    schema["required"] = list(properties)
    if allowed_evidence_ids is not None:
        if any(value.kind is not IdKind.EVIDENCE for value in allowed_evidence_ids):
            raise ContractValidationError(
                "reasoner provider evidence allow-list requires evidence IDs"
            )
        rendered = tuple(dict.fromkeys(str(value) for value in allowed_evidence_ids))
        if rendered:
            event_evidence["items"] = {
                "type": "string",
                "enum": list(rendered),
                "description": "Exact request-authorized evidence identity.",
            }
        else:
            event_evidence["maxItems"] = 0
    return schema


def codex_reasoner_draft_v6_json_schema(
    *,
    allowed_evidence_ids: tuple[TypedId, ...] | None = None,
) -> dict[str, object]:
    """Behavioral provider schema with bounded atomic-development proposals."""

    schema = codex_reasoner_draft_v5_json_schema(
        allowed_evidence_ids=allowed_evidence_ids
    )
    properties = schema["properties"]
    properties["schema_version"] = {
        "type": "string",
        "const": CodexReasonerDraftV6.SCHEMA_VERSION,
    }
    atom_evidence = _array(
        _id_schema(IdKind.EVIDENCE),
        32,
        semantic_set=True,
    )
    properties["development_atoms"] = _array(
        _closed(
            {
                "atom_key": _local_key_schema(),
                "owner_character_id": _id_schema(IdKind.CHARACTER),
                "kind": {
                    "type": "string",
                    "enum": [value.value for value in DevelopmentAtomKind],
                },
                "strength_before": {
                    "type": "string",
                    "enum": [value.value for value in DevelopmentAtomStrength],
                },
                "strength_after": {
                    "type": "string",
                    "enum": [value.value for value in DevelopmentAtomStrength],
                },
                "summary": {"type": "string", "minLength": 1},
                "source_block_keys": _array(
                    _local_key_schema(),
                    16,
                    minimum=1,
                    semantic_set=True,
                ),
                "evidence_ids": atom_evidence,
                "predecessor_development_ids": _array(
                    _id_schema(IdKind.DEVELOPMENT),
                    16,
                    semantic_set=True,
                ),
                "inference_limit": {"type": "string", "minLength": 1},
            }
        ),
        24,
        semantic_set=True,
    )
    schema["required"] = list(properties)
    if allowed_evidence_ids is not None:
        rendered = tuple(dict.fromkeys(str(value) for value in allowed_evidence_ids))
        if rendered:
            atom_evidence["items"] = {
                "type": "string",
                "enum": list(rendered),
                "description": "Exact request-authorized evidence identity.",
            }
        else:
            atom_evidence["maxItems"] = 0
    return schema


def _closed(properties: dict[str, object]) -> dict[str, object]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
        "required": list(properties),
    }


def _array(
    items: dict[str, object],
    maximum: int,
    *,
    minimum: int = 0,
    semantic_set: bool = False,
) -> dict[str, object]:
    schema = {
        "type": "array",
        "minItems": minimum,
        "maxItems": maximum,
        "items": items,
    }
    if semantic_set:
        # OpenAI Structured Outputs does not support JSON Schema uniqueItems.
        # This description teaches the provider; Python still enforces it.
        schema["description"] = (
            "Semantic set: every item must be distinct; do not repeat values."
        )
    return schema


class ReasonerDraftSemanticError(ContractValidationError):
    """Safe field diagnostics for model-authored cross-field semantics."""

    def __init__(self, diagnostics: tuple[str, ...]) -> None:
        self.diagnostics = diagnostics
        super().__init__(
            "reasoner draft violates status or semantic-set contract: "
            + ",".join(diagnostics)
        )


def safe_reasoner_contract_diagnostics(
    error: ContractValidationError | IdentityError,
) -> tuple[str, ...]:
    """Reduce domain exceptions to value-free failure evidence.

    Provider-authored values and story text must never be copied into a durable
    failure bundle.  The authoritative validators intentionally keep their
    human-readable exceptions, so this adapter maps their stable contract
    meanings to field/category codes instead of retaining ``str(error)``.
    """

    if isinstance(error, ReasonerDraftSemanticError):
        return error.diagnostics

    message = str(error)
    exact = {
        "draft moves must map one-to-one to responders": (
            "character_moves:responder_set_mismatch",
        ),
        "draft participation must map one-to-one to responders": (
            "participation:responder_set_mismatch",
        ),
        "floor owner must be a responder": (
            "floor_owner_id:not_selected_responder",
        ),
        "single responder requires single-NPC topology": (
            "interaction_topology:responder_count_mismatch",
        ),
        "multiple responders cannot use single-NPC topology": (
            "interaction_topology:responder_count_mismatch",
        ),
        "secondary participant requires intervention reason": (
            "participation.intervention_reason:required_for_secondary",
        ),
        "scene block contains an unselected actor": (
            "event_blocks.actor_ids:unselected_actor",
        ),
        "scene block cites an unknown source claim": (
            "event_blocks.source_claim_keys:unknown_claim",
        ),
        "protected-user allowance cites an unknown claim": (
            "event_blocks.protected_user_allowance.source_claim_keys:unknown_claim",
        ),
        "adult craft cites an unknown scene block": (
            "adult_craft_need.beat_requirements:unknown_scene_block",
        ),
        "source claim names an unknown source unit": (
            "source_claims.source_unit_id:unknown_source_unit",
        ),
        "source claim quote must occur exactly once": (
            "source_claims.quote:occurrence_mismatch",
        ),
        "source claim affects a character outside the cast": (
            "source_claims.affected_character_ids:outside_present_cast",
        ),
        "behavioral reasoner draft cites evidence that was never exactly authorized": (
            "evidence_ids:not_exactly_authorized",
        ),
        "development predecessor was not exactly authorized": (
            "development_atoms.predecessor_development_ids:not_exactly_authorized",
        ),
        "development atom owner is not a selected responder": (
            "development_atoms.owner_character_id:unselected_responder",
        ),
        "development atom cites an unknown source block": (
            "development_atoms.source_block_keys:unknown_scene_block",
        ),
        "development atom cites evidence unused by its scene plan": (
            "development_atoms.evidence_ids:unused_by_scene_plan",
        ),
    }
    if message in exact:
        return exact[message]

    if "contains unknown fields:" in message:
        return ("provider_payload:unknown_fields",)
    if "is missing required fields:" in message:
        return ("provider_payload:missing_required_fields",)
    if "invalid enum value" in message:
        return ("provider_payload:invalid_enum",)
    if "is not a valid typed ID" in message:
        return ("provider_payload:invalid_typed_id",)
    if "does not match its union" in message:
        return ("provider_payload:union_mismatch",)
    if "must not contain duplicates" in message:
        return ("provider_payload:duplicate_items",)
    if "must be an array" in message or "must be an object" in message:
        return ("provider_payload:container_type_mismatch",)
    if "must be string" in message or "must be boolean" in message:
        return ("provider_payload:scalar_type_mismatch",)
    return ("reasoner_output:domain_contract_violation",)


def validate_reasoner_draft_payload_semantics(payload: Mapping[str, object]) -> None:
    """Validate raw model semantics without retaining or echoing provider content.

    JSON Schema can require every property but cannot express the status matrix
    or set uniqueness in the active provider dialect.  Diagnostics therefore
    contain only stable field paths and codes, never field values.
    """

    diagnostics: list[str] = []
    status = payload.get("status")
    route = payload.get("route")
    behavioral_v5 = payload.get("schema_version") in {
        CodexReasonerDraftV5.SCHEMA_VERSION,
        CodexReasonerDraftV6.SCHEMA_VERSION,
    }

    def add(path: str, code: str) -> None:
        value = f"{path}:{code}"
        if value not in diagnostics:
            diagnostics.append(value)

    def nonempty_text(name: str) -> bool:
        value = payload.get(name)
        return isinstance(value, str) and bool(value.strip())

    def nonempty_list(name: str) -> bool:
        value = payload.get(name)
        return isinstance(value, list) and bool(value)

    if status == ReasonerOutcomeStatus.DECISION_READY.value:
        for name in (("scene_intent",) if behavioral_v5 else ("scene_intent", "stop_before")):
            if not nonempty_text(name):
                add(name, "required_for_decision_ready")
        required_lists = ["responding_npc_ids", "participation", "character_moves"]
        required_lists.extend(
            ("source_claims", "event_blocks")
            if behavioral_v5
            else ("current_beats",)
        )
        for name in required_lists:
            if not nonempty_list(name):
                add(name, "required_for_decision_ready")
        if behavioral_v5 and payload.get("causal_runway") is None:
            add("causal_runway", "required_for_decision_ready")
        if behavioral_v5:
            for name in (
                "interaction_topology",
                "scene_function",
                "tone",
                "interiority_level",
            ):
                if payload.get(name) is None:
                    add(name, "required_for_decision_ready")
        if payload.get("floor_owner_id") is None:
            add("floor_owner_id", "required_for_decision_ready")
        if route not in {
            DecisionRoute.ORDINARY.value,
            DecisionRoute.CONSENT_VALID_ADULT.value,
        }:
            add("route", "required_for_decision_ready")
        if payload.get("insufficiencies") not in ([], ()):
            add("insufficiencies", "forbidden_for_decision_ready")
        if payload.get("blocker_code") is not None:
            add("blocker_code", "forbidden_for_decision_ready")
        if route == DecisionRoute.ORDINARY.value and payload.get("adult_craft_need") is not None:
            add("adult_craft_need", "forbidden_for_ordinary")
        if (
            route == DecisionRoute.CONSENT_VALID_ADULT.value
            and payload.get("adult_craft_need") is None
        ):
            add("adult_craft_need", "required_for_consent_valid_adult")
    elif status == ReasonerOutcomeStatus.INSUFFICIENT_EVIDENCE.value:
        if not nonempty_list("insufficiencies"):
            add("insufficiencies", "required_for_insufficient_evidence")
        if payload.get("blocker_code") is not None:
            add("blocker_code", "forbidden_for_insufficient_evidence")
        _diagnose_non_ready_content(payload, diagnostics, add)
    elif status == ReasonerOutcomeStatus.BLOCKED.value:
        if payload.get("blocker_code") != ErrorCode.BLOCKED_NONCONSENSUAL_EVENT.value:
            add("blocker_code", "required_for_blocked")
        if payload.get("insufficiencies") not in ([], ()):
            add("insufficiencies", "forbidden_for_blocked")
        _diagnose_non_ready_content(payload, diagnostics, add)

    _diagnose_semantic_set_duplicates(payload, add)
    if diagnostics:
        raise ReasonerDraftSemanticError(tuple(diagnostics))


def _diagnose_non_ready_content(payload, diagnostics, add) -> None:
    del diagnostics
    behavioral_v5 = payload.get("schema_version") in {
        CodexReasonerDraftV5.SCHEMA_VERSION,
        CodexReasonerDraftV6.SCHEMA_VERSION,
    }
    empty_fields = {
        "route": None,
        "scene_intent": None,
        "responding_npc_ids": [],
        "floor_owner_id": None,
        "participation": [],
        "character_moves": [],
        "future_segments": [],
        "adult_craft_need": None,
    }
    if behavioral_v5:
        empty_fields.update(
            {
                "source_claims": [],
                "event_blocks": [],
                "causal_runway": None,
                "interaction_topology": None,
                "scene_function": None,
                "tone": None,
                "interiority_level": None,
            }
        )
        if payload.get("schema_version") == CodexReasonerDraftV6.SCHEMA_VERSION:
            empty_fields["development_atoms"] = []
    else:
        empty_fields.update({"current_beats": [], "stop_before": None})
    for name, empty in empty_fields.items():
        if payload.get(name) != empty:
            add(name, "forbidden_for_non_ready_status")
    if payload.get("protected_user_source_claims", []) not in ([], ()):
        add("protected_user_source_claims", "forbidden_for_non_ready_status")


def _diagnose_semantic_set_duplicates(payload, add) -> None:
    def check(path: str, value, *, key=None) -> None:
        if not isinstance(value, list):
            return
        normalized = []
        for item in value:
            if key is not None and isinstance(item, Mapping):
                normalized.append(key(item))
            else:
                normalized.append(repr(item))
        if len(normalized) != len(set(normalized)):
            add(path, "duplicate_items")

    for name in (
        "responding_npc_ids",
        "writer_must_preserve",
        "uncertainties",
        "prohibited_inferences",
        "insufficiencies",
    ):
        check(name, payload.get(name))
    check("participation", payload.get("participation"), key=lambda item: item.get("character_id"))
    check("character_moves", payload.get("character_moves"), key=lambda item: item.get("character_id"))
    check("current_beats", payload.get("current_beats"), key=lambda item: item.get("beat_key"))
    check("source_claims", payload.get("source_claims"), key=lambda item: item.get("claim_key"))
    check("event_blocks", payload.get("event_blocks"), key=lambda item: item.get("block_key"))
    check(
        "development_atoms",
        payload.get("development_atoms"),
        key=lambda item: item.get("atom_key"),
    )
    check("future_segments", payload.get("future_segments"), key=lambda item: item.get("segment_key"))
    check(
        "protected_user_source_claims",
        payload.get("protected_user_source_claims"),
        key=lambda item: (
            item.get("source_unit_id"),
            item.get("kind"),
            item.get("quote"),
        ),
    )
    for collection_name in ("participation", "character_moves", "current_beats"):
        values = payload.get(collection_name)
        if not isinstance(values, list):
            continue
        for index, item in enumerate(values):
            if isinstance(item, Mapping):
                check(f"{collection_name}[{index}].evidence_ids", item.get("evidence_ids"))
    blocks = payload.get("event_blocks")
    if isinstance(blocks, list):
        for index, item in enumerate(blocks):
            if not isinstance(item, Mapping):
                continue
            prefix = f"event_blocks[{index}]"
            for name in ("actor_ids", "event_advances", "evidence_ids", "source_claim_keys"):
                check(f"{prefix}.{name}", item.get(name))
            allowance = item.get("protected_user_allowance")
            if isinstance(allowance, Mapping):
                check(f"{prefix}.protected_user_allowance.allowed_kinds", allowance.get("allowed_kinds"))
                check(f"{prefix}.protected_user_allowance.source_claim_keys", allowance.get("source_claim_keys"))
            scaffold = item.get("writer_scaffold")
            if isinstance(scaffold, Mapping):
                for name in (
                    "viewpoint_character_ids",
                    "motivation_and_subtext",
                    "voice_and_interiority",
                    "physical_and_material_continuity",
                    "transition_obligations",
                    "open_realization_space",
                ):
                    check(f"{prefix}.writer_scaffold.{name}", scaffold.get(name))
    runway = payload.get("causal_runway")
    if isinstance(runway, Mapping):
        check("causal_runway.development_obligations", runway.get("development_obligations"))
    atoms = payload.get("development_atoms")
    if isinstance(atoms, list):
        for index, atom in enumerate(atoms):
            if not isinstance(atom, Mapping):
                continue
            prefix = f"development_atoms[{index}]"
            for name in (
                "source_block_keys",
                "evidence_ids",
                "predecessor_development_ids",
            ):
                check(f"{prefix}.{name}", atom.get(name))
    adult = payload.get("adult_craft_need")
    if not isinstance(adult, Mapping):
        return
    for name in ("families", "subfamilies"):
        check(f"adult_craft_need.{name}", adult.get(name))
    beats = adult.get("beat_requirements")
    check("adult_craft_need.beat_requirements", beats, key=lambda item: item.get("beat_key"))
    if isinstance(beats, list):
        for beat_index, beat in enumerate(beats):
            if not isinstance(beat, Mapping):
                continue
            prefix = f"adult_craft_need.beat_requirements[{beat_index}]"
            for name in ("object_concepts", "axes"):
                check(f"{prefix}.{name}", beat.get(name))
            channels = beat.get("channel_requirements")
            check(
                f"{prefix}.channel_requirements",
                channels,
                key=lambda item: (item.get("channel"), item.get("character_id")),
            )
            if isinstance(channels, list):
                for channel_index, channel in enumerate(channels):
                    if isinstance(channel, Mapping):
                        channel_prefix = (
                            f"{prefix}.channel_requirements[{channel_index}]"
                        )
                        for concept_field in (
                            "required_concepts",
                            "semantic_concepts",
                            "lexical_concepts",
                        ):
                            if concept_field in channel:
                                check(
                                    f"{channel_prefix}.{concept_field}",
                                    channel.get(concept_field),
                                )
                        semantic = channel.get("semantic_concepts")
                        lexical = channel.get("lexical_concepts")
                        if (
                            isinstance(semantic, list)
                            and isinstance(lexical, list)
                            and not set(lexical).issubset(semantic)
                        ):
                            add(
                                f"{channel_prefix}.lexical_concepts",
                                "not_subset_of_semantic_concepts",
                            )
    sections = adult.get("character_card_sections")
    check(
        "adult_craft_need.character_card_sections",
        sections,
        key=lambda item: item.get("character_id"),
    )
    if isinstance(sections, list):
        for index, section in enumerate(sections):
            if isinstance(section, Mapping):
                check(
                    f"adult_craft_need.character_card_sections[{index}].section_queries",
                    section.get("section_queries"),
                )


def _id_schema(*kinds: IdKind) -> dict[str, object]:
    alternatives = "|".join(value.value for value in kinds)
    return {
        "type": "string",
        "pattern": rf"^(?:{alternatives}):[A-Za-z0-9][A-Za-z0-9._-]{{0,127}}$",
    }


def _local_key_schema() -> dict[str, object]:
    return {
        "type": "string",
        "pattern": "^[A-Za-z][A-Za-z0-9_-]{0,63}$",
    }


def _local_key(value: str, field_name: str) -> None:
    import re

    if not isinstance(value, str) or re.fullmatch(
        r"[A-Za-z][A-Za-z0-9_-]{0,63}", value
    ) is None:
        raise ContractValidationError(f"{field_name} is not a valid local key")


def _non_empty(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ContractValidationError(f"{field_name} must be non-empty")


def _unique(values, field_name: str) -> None:
    items = tuple(values)
    if len(items) != len(set(items)):
        raise ContractValidationError(f"{field_name} must not contain duplicates")


def _add_structured_output_types(value):
    if isinstance(value, list):
        return [_add_structured_output_types(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {
        key: _add_structured_output_types(item)
        for key, item in value.items()
    }
    if "type" not in result:
        sample = None
        if "const" in result:
            sample = result["const"]
        elif isinstance(result.get("enum"), list) and result["enum"]:
            sample = result["enum"][0]
        if isinstance(sample, bool):
            result["type"] = "boolean"
        elif isinstance(sample, str):
            result["type"] = "string"
        elif isinstance(sample, int):
            result["type"] = "integer"
        elif sample is None and ("const" in result or "enum" in result):
            result["type"] = "null"
    return result
