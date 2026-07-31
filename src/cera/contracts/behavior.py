"""Provider-neutral behavioral authority and scene-development contracts.

These contracts separate what the user supplied from what the Reasoner may
infer, what the Composer may creatively realize, and what may later become
durable truth.  They deliberately contain no provider transport details.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar

from cera.errors import ContractValidationError
from cera.ids import IdKind, TypedId, require_authority_record_kind, require_kind
from cera.schema import require_schema
from cera.serialization import domain_sha256, re_is_sha256


class CharacterAutonomyMode(StrEnum):
    """Which NPC-authored internal channels reject user-authored outcomes."""

    OFF = "off"
    MIND = "mind"
    BODY = "body"
    BOTH = "both"

    @property
    def protects_mind(self) -> bool:
        return self in {CharacterAutonomyMode.MIND, CharacterAutonomyMode.BOTH}

    @property
    def protects_body(self) -> bool:
        return self in {CharacterAutonomyMode.BODY, CharacterAutonomyMode.BOTH}


class PromptHandlingMode(StrEnum):
    """How freely CERA may reshape supplied sequencing before composition."""

    ADJUSTMENT = "adjustment"
    MODIFICATION = "modification"


class InteractionTopology(StrEnum):
    SINGLE_NPC_FLOOR = "single_npc_floor"
    MULTI_NPC_SHARED_FLOOR = "multi_npc_shared_floor"
    NPC_TO_NPC_EXCHANGE = "npc_to_npc_exchange"


class SceneFunction(StrEnum):
    ORDINARY_SOCIAL = "ordinary_social"
    EMOTIONAL_VULNERABILITY = "emotional_vulnerability"
    CONFRONTATION_BOUNDARY = "confrontation_boundary"
    URGENT_PHYSICAL_ACTION = "urgent_physical_action"
    AFTERMATH_RECOVERY = "aftermath_recovery"


class PromptTone(StrEnum):
    NEUTRAL = "neutral"
    WARM = "warm"
    PLAYFUL = "playful"
    TENSE = "tense"
    URGENT = "urgent"
    SOMBER = "somber"


class InteriorityLevel(StrEnum):
    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class SourceClaimKind(StrEnum):
    PROTECTED_USER_ACTION = "protected_user_action"
    PROTECTED_USER_DIALOGUE = "protected_user_dialogue"
    NPC_OBSERVABLE_ACTION = "npc_observable_action"
    NPC_DIALOGUE = "npc_dialogue"
    NPC_MIND_STATE = "npc_mind_state"
    NPC_INVOLUNTARY_BODY_STATE = "npc_involuntary_body_state"
    MATERIAL_STATE = "material_state"
    TIME_PLACE_CONTEXT = "time_place_context"
    RELATIONSHIP_OR_HISTORY = "relationship_or_history"
    CREATIVE_DIRECTION = "creative_direction"
    META_INSTRUCTION = "meta_instruction"


class SourceClaimAuthority(StrEnum):
    SOURCE_AUTHORIZED = "source_authorized"
    NONBINDING_INPUT = "nonbinding_input"
    MODIFICATION_PROPOSAL = "modification_proposal"
    REQUIRES_CREATOR_CONFIRMATION = "requires_creator_confirmation"
    HARD_REJECTED = "hard_rejected"


class ProtectedUserAllowanceKind(StrEnum):
    EXACT_SUPPLIED_ACTION = "exact_supplied_action"
    EXACT_SUPPLIED_DIALOGUE = "exact_supplied_dialogue"
    MAINTAIN_ESTABLISHED_POSITION = "maintain_established_position"
    MINIMAL_NONBRANCHING_CONNECTIVE_MOTION = "minimal_nonbranching_connective_motion"


class SceneRunwayClass(StrEnum):
    ATOMIC = "atomic"
    DEVELOPED = "developed"
    DOMINO = "domino"
    MULTI_SCENE = "multi_scene"


class NaturalStopReason(StrEnum):
    CAUSAL_UNIT_COMPLETE = "causal_unit_complete"
    MEANINGFUL_USER_DECISION = "meaningful_user_decision"
    HARD_AUTHORITY_BOUNDARY = "hard_authority_boundary"
    INTENTIONAL_UNCERTAINTY = "intentional_uncertainty"
    SCENE_TRANSITION_COMPLETE = "scene_transition_complete"


class DevelopmentAtomKind(StrEnum):
    INVOLUNTARY_BODY_SIGNAL = "involuntary_body_signal"
    IMMEDIATE_AFFECT = "immediate_affect"
    NOTICED_SIGNAL = "noticed_signal"
    INTERPRETATION_HYPOTHESIS = "interpretation_hypothesis"
    MICRO_DEVELOPMENT = "micro_development"
    RELATIONSHIP_OBSERVATION = "relationship_observation"
    REPRODUCTIVE_STATE = "reproductive_state"


class DevelopmentAtomStrength(StrEnum):
    ABSENT = "absent"
    TRACE = "trace"
    EMERGING = "emerging"
    SUPPORTED = "supported"
    ESTABLISHED = "established"


@dataclass(frozen=True, slots=True)
class BehavioralTurnControls:
    SCHEMA_VERSION: ClassVar[str] = "cera.behavioral_turn_controls.v1"

    schema_version: str
    character_autonomy_mode: CharacterAutonomyMode
    prompt_handling_mode: PromptHandlingMode
    selective_interiority_enabled: bool
    provisional_display_required: bool
    creator_acceptance_required: bool

    def __post_init__(self) -> None:
        require_schema(self.schema_version, self.SCHEMA_VERSION, type(self).__name__)
        if not self.provisional_display_required or not self.creator_acceptance_required:
            raise ContractValidationError(
                "behavioral route requires provisional display and creator acceptance"
            )

    @classmethod
    def creator_default(cls) -> "BehavioralTurnControls":
        return cls(
            schema_version=cls.SCHEMA_VERSION,
            character_autonomy_mode=CharacterAutonomyMode.BOTH,
            prompt_handling_mode=PromptHandlingMode.ADJUSTMENT,
            selective_interiority_enabled=True,
            provisional_display_required=True,
            creator_acceptance_required=True,
        )

    @property
    def controls_sha256(self) -> str:
        return domain_sha256(self.SCHEMA_VERSION, self)


@dataclass(frozen=True, slots=True)
class SourceClaimDecision:
    claim_id: TypedId
    source_unit_id: TypedId
    kind: SourceClaimKind
    authority: SourceClaimAuthority
    start: int
    end: int
    exact_text_sha256: str
    normalized_meaning: str
    affected_character_ids: tuple[TypedId, ...]
    rationale: str

    def __post_init__(self) -> None:
        require_kind(self.claim_id, IdKind.SOURCE_CLAIM, "claim_id")
        require_kind(self.source_unit_id, IdKind.SOURCE_UNIT, "source_unit_id")
        if self.start < 0 or self.end <= self.start:
            raise ContractValidationError("source claim span is invalid")
        if not re_is_sha256(self.exact_text_sha256):
            raise ContractValidationError("source claim hash is invalid")
        _non_empty(self.normalized_meaning, "normalized_meaning")
        _non_empty(self.rationale, "rationale")
        for character_id in self.affected_character_ids:
            require_kind(character_id, IdKind.CHARACTER, "affected_character_ids")
        _unique(self.affected_character_ids, "affected characters")


@dataclass(frozen=True, slots=True)
class SourceClaimLedger:
    SCHEMA_VERSION: ClassVar[str] = "cera.source_claim_ledger.v1"

    schema_version: str
    source_sha256: str
    controls_sha256: str
    claims: tuple[SourceClaimDecision, ...]
    unresolved_questions: tuple[str, ...]
    prohibited_inferences: tuple[str, ...]

    def __post_init__(self) -> None:
        require_schema(self.schema_version, self.SCHEMA_VERSION, type(self).__name__)
        if not re_is_sha256(self.source_sha256) or not re_is_sha256(
            self.controls_sha256
        ):
            raise ContractValidationError("source claim ledger hashes are invalid")
        if not self.claims:
            raise ContractValidationError("source claim ledger requires claims")
        _unique((value.claim_id for value in self.claims), "source claim IDs")
        spans = tuple(
            (value.source_unit_id, value.start, value.end) for value in self.claims
        )
        _unique(spans, "source claim spans")
        _unique(self.unresolved_questions, "unresolved questions")
        _unique(self.prohibited_inferences, "prohibited inferences")

    @property
    def ledger_sha256(self) -> str:
        return domain_sha256(self.SCHEMA_VERSION, self)


@dataclass(frozen=True, slots=True)
class ProtectedUserRealizationAllowance:
    allowed_kinds: tuple[ProtectedUserAllowanceKind, ...]
    source_claim_ids: tuple[TypedId, ...]
    explanation: str

    def __post_init__(self) -> None:
        _unique(self.allowed_kinds, "protected-user allowance kinds")
        for claim_id in self.source_claim_ids:
            require_kind(claim_id, IdKind.SOURCE_CLAIM, "source_claim_ids")
        _unique(self.source_claim_ids, "protected-user source claims")
        _non_empty(self.explanation, "protected-user allowance explanation")
        exact = {
            ProtectedUserAllowanceKind.EXACT_SUPPLIED_ACTION,
            ProtectedUserAllowanceKind.EXACT_SUPPLIED_DIALOGUE,
        }
        if exact.intersection(self.allowed_kinds) and not self.source_claim_ids:
            raise ContractValidationError(
                "exact protected-user allowances require anchored source claims"
            )


@dataclass(frozen=True, slots=True)
class WriterScaffold:
    scaffold_id: TypedId
    viewpoint_character_ids: tuple[TypedId, ...]
    motivation_and_subtext: tuple[str, ...]
    voice_and_interiority: tuple[str, ...]
    physical_and_material_continuity: tuple[str, ...]
    transition_obligations: tuple[str, ...]
    open_realization_space: tuple[str, ...]

    def __post_init__(self) -> None:
        require_kind(self.scaffold_id, IdKind.WRITER_SCAFFOLD, "scaffold_id")
        if not self.viewpoint_character_ids:
            raise ContractValidationError("writer scaffold requires a viewpoint")
        for character_id in self.viewpoint_character_ids:
            require_kind(character_id, IdKind.CHARACTER, "viewpoint_character_ids")
        _unique(self.viewpoint_character_ids, "writer scaffold viewpoints")
        if not self.open_realization_space:
            raise ContractValidationError(
                "writer scaffold must preserve creative realization space"
            )
        for field_name in (
            "motivation_and_subtext",
            "voice_and_interiority",
            "physical_and_material_continuity",
            "transition_obligations",
            "open_realization_space",
        ):
            _unique(getattr(self, field_name), field_name)


@dataclass(frozen=True, slots=True)
class SceneEventBlock:
    block_id: TypedId
    actor_ids: tuple[TypedId, ...]
    purpose: str
    causal_basis: str
    event_advances: tuple[str, ...]
    resulting_state: str
    evidence_ids: tuple[TypedId, ...]
    source_claim_ids: tuple[TypedId, ...]
    protected_user_allowance: ProtectedUserRealizationAllowance
    writer_scaffold: WriterScaffold

    def __post_init__(self) -> None:
        require_kind(self.block_id, IdKind.SCENE_BLOCK, "block_id")
        if not self.actor_ids or not self.event_advances:
            raise ContractValidationError("scene block requires actors and event advances")
        for actor_id in self.actor_ids:
            if actor_id.kind not in {IdKind.CHARACTER, IdKind.MATERIAL}:
                raise ContractValidationError("scene block actor kind is invalid")
        _unique(self.actor_ids, "scene block actors")
        _non_empty(self.purpose, "scene block purpose")
        _non_empty(self.causal_basis, "scene block causal basis")
        _non_empty(self.resulting_state, "scene block resulting state")
        _unique(self.event_advances, "scene block advances")
        for evidence_id in self.evidence_ids:
            require_kind(evidence_id, IdKind.EVIDENCE, "scene block evidence_ids")
        for claim_id in self.source_claim_ids:
            require_kind(claim_id, IdKind.SOURCE_CLAIM, "scene block source_claim_ids")
        _unique(self.evidence_ids, "scene block evidence")
        _unique(self.source_claim_ids, "scene block source claims")
        if not set(self.writer_scaffold.viewpoint_character_ids).issubset(
            set(self.actor_ids)
        ):
            raise ContractValidationError(
                "writer scaffold viewpoint must be a scene-block actor"
            )


@dataclass(frozen=True, slots=True)
class CausalRunwayContract:
    runway_class: SceneRunwayClass
    development_obligations: tuple[str, ...]
    continue_beyond_prompt_endpoint: bool
    stop_reason: NaturalStopReason
    stop_condition: str

    def __post_init__(self) -> None:
        if not self.development_obligations:
            raise ContractValidationError("causal runway requires development obligations")
        _unique(self.development_obligations, "causal runway obligations")
        _non_empty(self.stop_condition, "causal runway stop condition")


@dataclass(frozen=True, slots=True)
class BehavioralScenePlan:
    SCHEMA_VERSION: ClassVar[str] = "cera.behavioral_scene_plan.v2"

    schema_version: str
    claim_ledger: SourceClaimLedger
    event_blocks: tuple[SceneEventBlock, ...]
    runway: CausalRunwayContract
    interaction_topology: InteractionTopology
    scene_function: SceneFunction
    tone: PromptTone
    interiority_level: InteriorityLevel
    selected_character_ids: tuple[TypedId, ...]
    omitted_character_ids: tuple[TypedId, ...]
    development_atoms: tuple["DevelopmentAtomProposal", ...] = ()

    def __post_init__(self) -> None:
        require_schema(self.schema_version, self.SCHEMA_VERSION, type(self).__name__)
        if not self.event_blocks or not self.selected_character_ids:
            raise ContractValidationError("behavioral scene plan requires blocks and cast")
        _unique((value.block_id for value in self.event_blocks), "scene block IDs")
        for character_id in (*self.selected_character_ids, *self.omitted_character_ids):
            require_kind(character_id, IdKind.CHARACTER, "behavioral scene cast")
        _unique(self.selected_character_ids, "selected character IDs")
        _unique(self.omitted_character_ids, "omitted character IDs")
        if set(self.selected_character_ids).intersection(self.omitted_character_ids):
            raise ContractValidationError("selected and omitted characters overlap")
        _unique((value.atom_id for value in self.development_atoms), "development atom IDs")
        selected = set(self.selected_character_ids)
        for block in self.event_blocks:
            character_actors = {
                value for value in block.actor_ids if value.kind is IdKind.CHARACTER
            }
            if not character_actors.issubset(selected):
                raise ContractValidationError("scene block contains an unselected character")
        block_ids = {value.block_id for value in self.event_blocks}
        for atom in self.development_atoms:
            if atom.owner_character_id not in selected:
                raise ContractValidationError(
                    "development atom owner is not a selected character"
                )
            if not set(atom.source_scene_block_ids).issubset(block_ids):
                raise ContractValidationError(
                    "development atom cites a scene block outside the plan"
                )

    @property
    def plan_sha256(self) -> str:
        return domain_sha256(self.SCHEMA_VERSION, self)


@dataclass(frozen=True, slots=True)
class DevelopmentAtomProposal:
    """Provisional one-step change; never durable merely because it was proposed."""

    atom_id: TypedId
    owner_character_id: TypedId
    kind: DevelopmentAtomKind
    strength_before: DevelopmentAtomStrength
    strength_after: DevelopmentAtomStrength
    summary: str
    source_scene_block_ids: tuple[TypedId, ...]
    evidence_record_ids: tuple[TypedId, ...]
    predecessor_development_ids: tuple[TypedId, ...]
    inference_limit: str
    durable_authority: bool = False

    def __post_init__(self) -> None:
        require_kind(self.atom_id, IdKind.DEVELOPMENT, "atom_id")
        require_kind(
            self.owner_character_id,
            IdKind.CHARACTER,
            "owner_character_id",
        )
        _non_empty(self.summary, "development atom summary")
        _non_empty(self.inference_limit, "development atom inference limit")
        if not self.source_scene_block_ids:
            raise ContractValidationError(
                "development atom requires at least one source scene block"
            )
        for block_id in self.source_scene_block_ids:
            require_kind(block_id, IdKind.SCENE_BLOCK, "source_scene_block_ids")
        for record_id in self.evidence_record_ids:
            require_authority_record_kind(record_id, "evidence_record_ids")
        for development_id in self.predecessor_development_ids:
            require_kind(
                development_id,
                IdKind.DEVELOPMENT,
                "predecessor_development_ids",
            )
        _unique(self.evidence_record_ids, "development evidence records")
        _unique(self.source_scene_block_ids, "development source scene blocks")
        _unique(self.predecessor_development_ids, "development predecessors")
        order = tuple(DevelopmentAtomStrength)
        if order.index(self.strength_after) - order.index(self.strength_before) != 1:
            raise ContractValidationError(
                "one event must advance exactly one development strength level"
            )
        if (
            self.strength_before is DevelopmentAtomStrength.ABSENT
            and self.predecessor_development_ids
        ):
            raise ContractValidationError(
                "an absent development atom cannot have a predecessor"
            )
        if (
            self.strength_before is not DevelopmentAtomStrength.ABSENT
            and not self.predecessor_development_ids
        ):
            raise ContractValidationError(
                "an advancing development atom requires predecessor evidence"
            )
        if self.durable_authority:
            raise ContractValidationError(
                "development proposal cannot claim durable authority"
            )


@dataclass(frozen=True, slots=True)
class EffectiveCharacterProjection:
    SCHEMA_VERSION: ClassVar[str] = "cera.effective_character_projection.v1"

    schema_version: str
    character_id: TypedId
    genesis_revision_id: TypedId
    branch_id: TypedId
    generation: int
    snapshot_token: TypedId
    invariant_record_ids: tuple[TypedId, ...]
    current_state_record_ids: tuple[TypedId, ...]
    voice_record_ids: tuple[TypedId, ...]
    development_record_ids: tuple[TypedId, ...]
    selected_section_names: tuple[str, ...]
    explicit_unknowns: tuple[str, ...]
    prohibited_inferences: tuple[str, ...]

    def __post_init__(self) -> None:
        require_schema(self.schema_version, self.SCHEMA_VERSION, type(self).__name__)
        require_kind(self.character_id, IdKind.CHARACTER, "character_id")
        require_kind(
            self.genesis_revision_id,
            IdKind.GENESIS_REVISION,
            "genesis_revision_id",
        )
        require_kind(self.branch_id, IdKind.BRANCH, "branch_id")
        require_kind(self.snapshot_token, IdKind.SNAPSHOT, "snapshot_token")
        if self.generation < 0:
            raise ContractValidationError("character projection generation is invalid")
        for field_name in (
            "invariant_record_ids",
            "current_state_record_ids",
            "voice_record_ids",
            "development_record_ids",
        ):
            values = getattr(self, field_name)
            for record_id in values:
                require_authority_record_kind(record_id, field_name)
            _unique(values, field_name)
        if not self.selected_section_names:
            raise ContractValidationError("character projection requires selected sections")
        _unique(self.selected_section_names, "character projection sections")
        _unique(self.explicit_unknowns, "character projection unknowns")
        _unique(self.prohibited_inferences, "character projection prohibitions")

    @property
    def projection_sha256(self) -> str:
        return domain_sha256(self.SCHEMA_VERSION, self)


def validate_claim_authority(
    controls: BehavioralTurnControls,
    claims: tuple[SourceClaimDecision, ...],
) -> None:
    """Enforce autonomy and prompt-handling ownership after model decoding."""

    for claim in claims:
        if (
            controls.character_autonomy_mode.protects_mind
            and claim.kind is SourceClaimKind.NPC_MIND_STATE
            and claim.authority is SourceClaimAuthority.SOURCE_AUTHORIZED
        ):
            raise ContractValidationError(
                "Mind autonomy forbids user-authored NPC mind state as authority"
            )
        if (
            controls.character_autonomy_mode.protects_body
            and claim.kind is SourceClaimKind.NPC_INVOLUNTARY_BODY_STATE
            and claim.authority is SourceClaimAuthority.SOURCE_AUTHORIZED
        ):
            raise ContractValidationError(
                "Body autonomy forbids user-authored involuntary NPC body state as authority"
            )
        if (
            claim.authority is SourceClaimAuthority.MODIFICATION_PROPOSAL
            and controls.prompt_handling_mode is not PromptHandlingMode.MODIFICATION
        ):
            raise ContractValidationError(
                "modification proposal requires Modification prompt handling"
            )


def _non_empty(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ContractValidationError(f"{field_name} must be non-empty")


def _unique(values, field_name: str) -> None:
    items = tuple(values)
    if len(items) != len(set(items)):
        raise ContractValidationError(f"{field_name} must not contain duplicates")
