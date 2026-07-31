"""Typed, non-authoritative contracts for consent-valid adult prose craft.

The models in this module carry technique requirements and validation evidence.
They cannot establish scene events, consent, character psychology, or durable
story truth.  Blocked non-consensual generation is deliberately unrepresentable
as an accepted craft need.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar

from cera.errors import ContractValidationError
from cera.ids import IdKind, TypedId, require_kind
from cera.schema import require_schema
from cera.serialization import domain_sha256, re_is_sha256, text_sha256


class AdultCraftMode(StrEnum):
    ON = "adult_on"
    EX = "adult_ex"


class AdultCraftAssetClass(StrEnum):
    STYLE_KERNEL = "style_kernel"
    VOCABULARY = "vocabulary"
    CAUSALITY_GUARD = "causality_guard"
    DEVELOPMENT_GUIDE = "development_guide"
    MICRO_EXAMPLE = "micro_example"


class AdultCraftFamily(StrEnum):
    GENERAL = "general"
    ANAL = "anal"
    TOILET_SCAT = "toilet_scat"
    REPRODUCTIVE_CLIMAX = "reproductive_climax"
    BDSM_OBJECTIFICATION = "bdsm_objectification"
    BODY_MODIFICATION = "body_modification"
    DEGRADATION_CORRUPTION = "degradation_corruption"
    INFIDELITY_DRAMA = "infidelity_drama"


class AdultCraftAxis(StrEnum):
    DIRECT_VOCABULARY = "direct_vocabulary"
    BUILDUP = "buildup"
    CAUSAL_PHYSIOLOGY = "causal_physiology"
    VOCALIZATION = "vocalization"
    ACTION_BOUND_SOUND = "action_bound_sound"
    CLIMAX = "climax"
    AFTERMATH = "aftermath"
    MATERIAL_CONTINUITY = "material_continuity"
    PSYCHOLOGICAL_CONTRAST = "psychological_contrast"
    REALISM_AGENCY = "realism_agency"


class AdultCraftConcept(StrEnum):
    ANATOMY = "anatomy"
    CLITORIS = "clitoris"
    ANUS = "anus"
    PENIS = "penis"
    SEMEN = "semen"
    LUBRICATION = "lubrication"
    ACTION = "action"
    VOCALIZATION = "vocalization"
    SOUND_EFFECT = "sound_effect"
    BODILY_FLUIDS = "bodily_fluids"
    SQUIRTING = "squirting"
    URINE = "urine"
    FECES = "feces"
    SALIVA = "saliva"
    TEARS = "tears"
    CERVIX = "cervix"
    WOMB = "womb"
    MATERIAL_CONTINUITY = "material_continuity"
    BUILDUP = "buildup"
    AFTERMATH = "aftermath"


class RealizationChannel(StrEnum):
    NARRATION = "narration"
    DIALOGUE = "dialogue"
    INNER_VOICE = "inner_voice"
    SOUND_EFFECT = "sound_effect"
    PHYSIOLOGY = "physiology"


class SpecificityRegister(StrEnum):
    INDIRECT = "indirect"
    DIRECT = "direct"
    VULGAR = "vulgar"


class OutcomeAuthorityState(StrEnum):
    REQUIRED = "required"
    ALLOWED = "allowed"
    PROHIBITED = "prohibited"
    UNRESOLVED = "unresolved"


class SegmentCommitment(StrEnum):
    SELECTED = "selected"
    CONDITIONAL = "conditional"
    NOT_SELECTED = "not_selected"


class SemanticVerificationStage(StrEnum):
    INITIAL = "initial"
    POST_REPAIR = "post_repair"


class SemanticSpecificityAdapterRole(StrEnum):
    SCRIPTED_FAKE = "scripted_fake_semantic_specificity"


class SemanticSpecificityFindingCode(StrEnum):
    CENTRAL_ACT_AMBIGUOUS = "central_act_ambiguous"
    ACTOR_ACTION_OBJECT_MISMATCH = "actor_action_object_mismatch"
    REQUIRED_TRANSITION_MISSING = "required_transition_missing"
    REQUIRED_TRANSITION_OUT_OF_ORDER = "required_transition_out_of_order"
    MATERIAL_OUTCOME_CONTRADICTED = "material_outcome_contradicted"
    COVERAGE_DECLARATION_FALSE = "coverage_declaration_false"


@dataclass(frozen=True, slots=True)
class CraftVocabularyGroup:
    concept: AdultCraftConcept
    terms: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.terms:
            raise ContractValidationError("vocabulary group requires terms")
        for term in self.terms:
            _non_empty(term, "vocabulary term")
        _unique((term.casefold() for term in self.terms), "vocabulary terms")


@dataclass(frozen=True, slots=True)
class CraftSourceBinding:
    source_relative_path: str
    source_sha256: str
    heading_path: tuple[str, ...]
    line_start: int
    line_end: int

    def __post_init__(self) -> None:
        _relative_path(self.source_relative_path)
        _sha256(self.source_sha256, "source_sha256")
        if not self.heading_path or self.line_start < 1 or self.line_end < self.line_start:
            raise ContractValidationError("craft source binding has invalid heading or lines")


@dataclass(frozen=True, slots=True)
class AdultCraftFragment:
    SCHEMA_VERSION: ClassVar[str] = "cera.adult_craft_fragment.v1"

    schema_version: str
    fragment_id: TypedId
    catalog_version: str
    asset_class: AdultCraftAssetClass
    modes: tuple[AdultCraftMode, ...]
    families: tuple[AdultCraftFamily, ...]
    axes: tuple[AdultCraftAxis, ...]
    channels: tuple[RealizationChannel, ...]
    coverage_concepts: tuple[AdultCraftConcept, ...]
    vocabulary_groups: tuple[CraftVocabularyGroup, ...]
    activation_requirements: tuple[str, ...]
    prohibited_inferences: tuple[str, ...]
    source: CraftSourceBinding
    craft_text: str
    craft_text_sha256: str
    craft_only: bool
    evaluation_evidence: bool
    blocked_nonconsensual_generation_excluded: bool

    def __post_init__(self) -> None:
        require_schema(self.schema_version, self.SCHEMA_VERSION, type(self).__name__)
        require_kind(self.fragment_id, IdKind.CRAFT_REFERENCE, "fragment_id")
        _non_empty(self.catalog_version, "catalog_version")
        if not self.modes or not self.families or not self.axes or not self.channels:
            raise ContractValidationError("craft fragment requires modes, families, axes, and channels")
        _unique((value.value for value in self.modes), "fragment modes")
        _unique((value.value for value in self.families), "fragment families")
        _unique((value.value for value in self.axes), "fragment axes")
        _unique((value.value for value in self.channels), "fragment channels")
        _unique((value.value for value in self.coverage_concepts), "fragment coverage concepts")
        _unique((value.concept.value for value in self.vocabulary_groups), "vocabulary concepts")
        _unique(self.activation_requirements, "activation requirements")
        _unique(self.prohibited_inferences, "prohibited inferences")
        _non_empty(self.craft_text, "craft_text")
        _sha256(self.craft_text_sha256, "craft_text_sha256")
        if text_sha256(self.craft_text) != self.craft_text_sha256:
            raise ContractValidationError("craft text hash mismatch")
        if not self.craft_only or self.evaluation_evidence:
            raise ContractValidationError("adult fragments must be craft-only, never evaluation evidence")
        if not self.blocked_nonconsensual_generation_excluded:
            raise ContractValidationError("blocked non-consensual generation must remain excluded")

    @property
    def fragment_sha256(self) -> str:
        return domain_sha256("cera.adult_craft_fragment.v1", self)


@dataclass(frozen=True, slots=True)
class AdultCraftFragmentIndexEntry:
    fragment_id: TypedId
    relative_path: str
    fragment_sha256: str
    craft_text_sha256: str

    def __post_init__(self) -> None:
        require_kind(self.fragment_id, IdKind.CRAFT_REFERENCE, "fragment_id")
        _relative_path(self.relative_path)
        _sha256(self.fragment_sha256, "fragment_sha256")
        _sha256(self.craft_text_sha256, "craft_text_sha256")


@dataclass(frozen=True, slots=True)
class AdultCraftCatalogManifest:
    SCHEMA_VERSION: ClassVar[str] = "cera.adult_craft_catalog_manifest.v1"

    schema_version: str
    catalog_id: TypedId
    catalog_version: str
    compiler_version: str
    source_integrity_sha256: str
    curation_sha256: str
    source_artifact_count: int
    compiled_fragment_count: int
    provenance_only_source_count: int
    audit_only_source_count: int
    fragment_entries: tuple[AdultCraftFragmentIndexEntry, ...]
    runtime_external_path_dependencies: tuple[str, ...]
    coverage_declarations_are_audit_claims_only: bool
    blocked_nonconsensual_generation_excluded: bool

    def __post_init__(self) -> None:
        require_schema(self.schema_version, self.SCHEMA_VERSION, type(self).__name__)
        require_kind(self.catalog_id, IdKind.CRAFT_CATALOG, "catalog_id")
        _non_empty(self.catalog_version, "catalog_version")
        _non_empty(self.compiler_version, "compiler_version")
        _sha256(self.source_integrity_sha256, "source_integrity_sha256")
        _sha256(self.curation_sha256, "curation_sha256")
        if self.source_artifact_count != 24:
            raise ContractValidationError("adult source catalog requires exactly 24 audited artifacts")
        if self.compiled_fragment_count != len(self.fragment_entries):
            raise ContractValidationError("compiled fragment count does not match index")
        if min(
            self.compiled_fragment_count,
            self.provenance_only_source_count,
            self.audit_only_source_count,
        ) < 0:
            raise ContractValidationError("catalog counts cannot be negative")
        _unique((str(value.fragment_id) for value in self.fragment_entries), "fragment IDs")
        _unique((value.relative_path for value in self.fragment_entries), "fragment paths")
        if self.runtime_external_path_dependencies:
            raise ContractValidationError("runtime adult catalog cannot depend on external paths")
        if not self.coverage_declarations_are_audit_claims_only:
            raise ContractValidationError("coverage declarations cannot be promoted to proof")
        if not self.blocked_nonconsensual_generation_excluded:
            raise ContractValidationError("blocked non-consensual generation must remain excluded")

    @property
    def manifest_sha256(self) -> str:
        return domain_sha256("cera.adult_craft_catalog_manifest.v1", self)


@dataclass(frozen=True, slots=True)
class ChannelCraftNeed:
    channel: RealizationChannel
    character_id: TypedId | None
    minimum_register: SpecificityRegister
    required_concepts: tuple[AdultCraftConcept, ...]

    def __post_init__(self) -> None:
        if self.character_id is not None:
            require_kind(self.character_id, IdKind.CHARACTER, "channel character_id")
        if self.channel in {RealizationChannel.DIALOGUE, RealizationChannel.INNER_VOICE}:
            if self.character_id is None:
                raise ContractValidationError("dialogue and inner voice needs require a character")
        elif self.character_id is not None:
            raise ContractValidationError("non-character channel need cannot name a character")
        _unique((value.value for value in self.required_concepts), "channel required concepts")


@dataclass(frozen=True, slots=True)
class BeatCraftNeed:
    beat_id: TypedId
    actor_id: TypedId
    action_family: str
    object_concepts: tuple[AdultCraftConcept, ...]
    axes: tuple[AdultCraftAxis, ...]
    channels: tuple[RealizationChannel, ...]
    priority: int

    def __post_init__(self) -> None:
        require_kind(self.beat_id, IdKind.BEAT, "beat_id")
        if self.actor_id.kind not in {IdKind.CHARACTER, IdKind.MATERIAL}:
            raise ContractValidationError("beat craft actor must be a character or material")
        _non_empty(self.action_family, "action_family")
        _unique((value.value for value in self.object_concepts), "beat object concepts")
        _unique((value.value for value in self.axes), "beat axes")
        _unique((value.value for value in self.channels), "beat channels")
        if not 1 <= self.priority <= 5:
            raise ContractValidationError("beat craft priority must be 1..5")


@dataclass(frozen=True, slots=True)
class OutcomeScope:
    authority_state: OutcomeAuthorityState
    current_segment_commitment: SegmentCommitment

    def __post_init__(self) -> None:
        if (
            self.authority_state is OutcomeAuthorityState.PROHIBITED
            and self.current_segment_commitment is SegmentCommitment.SELECTED
        ):
            raise ContractValidationError("a prohibited outcome cannot be selected")
        if (
            self.authority_state is OutcomeAuthorityState.UNRESOLVED
            and self.current_segment_commitment is SegmentCommitment.SELECTED
        ):
            raise ContractValidationError("an unresolved outcome cannot be selected")


@dataclass(frozen=True, slots=True)
class CharacterCardSectionNeed:
    character_id: TypedId
    section_queries: tuple[str, ...]

    def __post_init__(self) -> None:
        require_kind(self.character_id, IdKind.CHARACTER, "character_id")
        if not self.section_queries:
            raise ContractValidationError("character card selection requires section queries")
        _unique(self.section_queries, "character card section queries")


@dataclass(frozen=True, slots=True)
class CharacterChannelCraftNeedV2:
    """Character-owned realization channel.

    The discriminator makes the owner requirement representable in provider
    JSON Schema instead of relying on a nullable cross-field convention.
    """

    channel: RealizationChannel
    character_id: TypedId
    minimum_register: SpecificityRegister
    required_concepts: tuple[AdultCraftConcept, ...]

    def __post_init__(self) -> None:
        if self.channel not in {
            RealizationChannel.DIALOGUE,
            RealizationChannel.INNER_VOICE,
        }:
            raise ContractValidationError(
                "character channel need must be dialogue or inner_voice"
            )
        require_kind(self.character_id, IdKind.CHARACTER, "channel character_id")
        _unique(
            (value.value for value in self.required_concepts),
            "character-channel concepts",
        )


@dataclass(frozen=True, slots=True)
class SceneChannelCraftNeedV2:
    """Ownerless scene realization channel."""

    channel: RealizationChannel
    minimum_register: SpecificityRegister
    required_concepts: tuple[AdultCraftConcept, ...]

    def __post_init__(self) -> None:
        if self.channel not in {
            RealizationChannel.NARRATION,
            RealizationChannel.SOUND_EFFECT,
            RealizationChannel.PHYSIOLOGY,
        }:
            raise ContractValidationError(
                "scene channel need must be narration, sound_effect, or physiology"
            )
        _unique(
            (value.value for value in self.required_concepts),
            "scene-channel concepts",
        )


@dataclass(frozen=True, slots=True)
class BeatCraftRequirementV2:
    """All adult craft semantics for one current decision beat."""

    beat_id: TypedId
    actor_id: TypedId
    action_family: str
    object_concepts: tuple[AdultCraftConcept, ...]
    axes: tuple[AdultCraftAxis, ...]
    channel_requirements: tuple[
        CharacterChannelCraftNeedV2 | SceneChannelCraftNeedV2, ...
    ]
    priority: int

    def __post_init__(self) -> None:
        require_kind(self.beat_id, IdKind.BEAT, "beat_id")
        if self.actor_id.kind not in {IdKind.CHARACTER, IdKind.MATERIAL}:
            raise ContractValidationError(
                "beat craft actor must be a character or material"
            )
        _non_empty(self.action_family, "action_family")
        if not self.axes or not self.channel_requirements:
            raise ContractValidationError(
                "beat craft requirement needs axes and channel requirements"
            )
        _unique((value.value for value in self.object_concepts), "beat object concepts")
        _unique((value.value for value in self.axes), "beat axes")
        _unique(
            (
                f"{value.channel.value}|"
                f"{getattr(value, 'character_id', None)}"
                for value in self.channel_requirements
            ),
            "beat channel requirements",
        )
        if not 1 <= self.priority <= 5:
            raise ContractValidationError("beat craft priority must be 1..5")

    @property
    def channels(self) -> tuple[RealizationChannel, ...]:
        return tuple(value.channel for value in self.channel_requirements)


@dataclass(frozen=True, slots=True)
class AdultCraftNeedV2:
    """Python-bound adult craft need with beat-local discriminated channels.

    Route authorization booleans were removed: Python preflight already owns
    them and the Reasoner cannot strengthen or restate that authority.
    Global axes/channel summaries are derived views over beat requirements.
    """

    SCHEMA_VERSION: ClassVar[str] = "cera.adult_craft_need.v2"

    schema_version: str
    craft_need_id: TypedId
    request_id: TypedId
    decision_id: TypedId
    sequence_plan_sha256: str
    mode: AdultCraftMode
    families: tuple[AdultCraftFamily, ...]
    subfamilies: tuple[str, ...]
    beat_requirements: tuple[BeatCraftRequirementV2, ...]
    climax: OutcomeScope
    aftermath: OutcomeScope
    character_card_sections: tuple[CharacterCardSectionNeed, ...]

    def __post_init__(self) -> None:
        require_schema(self.schema_version, self.SCHEMA_VERSION, type(self).__name__)
        require_kind(self.craft_need_id, IdKind.ADULT_CRAFT_NEED, "craft_need_id")
        require_kind(self.request_id, IdKind.REQUEST, "request_id")
        require_kind(self.decision_id, IdKind.DECISION, "decision_id")
        _sha256(self.sequence_plan_sha256, "sequence_plan_sha256")
        if not self.families or not self.beat_requirements:
            raise ContractValidationError(
                "adult craft need v2 requires families and beat requirements"
            )
        _unique((value.value for value in self.families), "craft-need families")
        _unique(self.subfamilies, "craft-need subfamilies")
        _unique(
            (str(value.beat_id) for value in self.beat_requirements),
            "craft-need beats",
        )
        _unique(
            (str(value.character_id) for value in self.character_card_sections),
            "craft-need character cards",
        )

    @property
    def axes(self) -> tuple[AdultCraftAxis, ...]:
        return tuple(
            dict.fromkeys(
                axis
                for requirement in self.beat_requirements
                for axis in requirement.axes
            )
        )

    @property
    def channel_needs(self) -> tuple[ChannelCraftNeed, ...]:
        """Compatibility projection for the existing selector.

        The authoritative v2 representation remains beat-local.  This derived
        view is never serialized into a provider contract.
        """

        return tuple(
            dict.fromkeys(
                ChannelCraftNeed(
                    channel=channel.channel,
                    character_id=getattr(channel, "character_id", None),
                    minimum_register=channel.minimum_register,
                    required_concepts=channel.required_concepts,
                )
                for requirement in self.beat_requirements
                for channel in requirement.channel_requirements
            )
        )

    @property
    def beat_needs(self) -> tuple[BeatCraftRequirementV2, ...]:
        return self.beat_requirements

    @property
    def need_sha256(self) -> str:
        return domain_sha256("cera.adult_craft_need.v2", self)


@dataclass(frozen=True, slots=True)
class CharacterChannelCraftNeedV3:
    """Character-owned craft need with separate semantic and lexical duties."""

    channel: RealizationChannel
    character_id: TypedId
    minimum_register: SpecificityRegister
    semantic_concepts: tuple[AdultCraftConcept, ...]
    lexical_concepts: tuple[AdultCraftConcept, ...]

    def __post_init__(self) -> None:
        if self.channel not in {
            RealizationChannel.DIALOGUE,
            RealizationChannel.INNER_VOICE,
        }:
            raise ContractValidationError(
                "character channel need must be dialogue or inner_voice"
            )
        require_kind(self.character_id, IdKind.CHARACTER, "channel character_id")
        _validate_v3_concepts(self.semantic_concepts, self.lexical_concepts)


@dataclass(frozen=True, slots=True)
class SceneChannelCraftNeedV3:
    """Ownerless scene craft need with explicit lexical ownership."""

    channel: RealizationChannel
    minimum_register: SpecificityRegister
    semantic_concepts: tuple[AdultCraftConcept, ...]
    lexical_concepts: tuple[AdultCraftConcept, ...]

    def __post_init__(self) -> None:
        if self.channel not in {
            RealizationChannel.NARRATION,
            RealizationChannel.SOUND_EFFECT,
            RealizationChannel.PHYSIOLOGY,
        }:
            raise ContractValidationError(
                "scene channel need must be narration, sound_effect, or physiology"
            )
        _validate_v3_concepts(self.semantic_concepts, self.lexical_concepts)


@dataclass(frozen=True, slots=True)
class BeatCraftRequirementV3:
    """Beat-local craft semantics whose vocabulary obligations are explicit."""

    beat_id: TypedId
    actor_id: TypedId
    action_family: str
    object_concepts: tuple[AdultCraftConcept, ...]
    axes: tuple[AdultCraftAxis, ...]
    channel_requirements: tuple[
        CharacterChannelCraftNeedV3 | SceneChannelCraftNeedV3, ...
    ]
    priority: int

    def __post_init__(self) -> None:
        require_kind(self.beat_id, IdKind.BEAT, "beat_id")
        if self.actor_id.kind not in {IdKind.CHARACTER, IdKind.MATERIAL}:
            raise ContractValidationError(
                "beat craft actor must be a character or material"
            )
        _non_empty(self.action_family, "action_family")
        if not self.axes or not self.channel_requirements:
            raise ContractValidationError(
                "beat craft requirement needs axes and channel requirements"
            )
        _unique((value.value for value in self.object_concepts), "beat object concepts")
        _unique((value.value for value in self.axes), "beat axes")
        _unique(
            (
                f"{value.channel.value}|"
                f"{getattr(value, 'character_id', None)}"
                for value in self.channel_requirements
            ),
            "beat channel requirements",
        )
        if not 1 <= self.priority <= 5:
            raise ContractValidationError("beat craft priority must be 1..5")

    @property
    def channels(self) -> tuple[RealizationChannel, ...]:
        return tuple(value.channel for value in self.channel_requirements)


@dataclass(frozen=True, slots=True)
class AdultCraftNeedV3:
    """Active craft contract separating meaning from required word choice.

    ``semantic_concepts`` select craft technique and semantic verification.
    Only ``lexical_concepts`` create deterministic vocabulary requirements.
    """

    SCHEMA_VERSION: ClassVar[str] = "cera.adult_craft_need.v3"

    schema_version: str
    craft_need_id: TypedId
    request_id: TypedId
    decision_id: TypedId
    sequence_plan_sha256: str
    mode: AdultCraftMode
    families: tuple[AdultCraftFamily, ...]
    subfamilies: tuple[str, ...]
    beat_requirements: tuple[BeatCraftRequirementV3, ...]
    climax: OutcomeScope
    aftermath: OutcomeScope
    character_card_sections: tuple[CharacterCardSectionNeed, ...]

    def __post_init__(self) -> None:
        require_schema(self.schema_version, self.SCHEMA_VERSION, type(self).__name__)
        require_kind(self.craft_need_id, IdKind.ADULT_CRAFT_NEED, "craft_need_id")
        require_kind(self.request_id, IdKind.REQUEST, "request_id")
        require_kind(self.decision_id, IdKind.DECISION, "decision_id")
        _sha256(self.sequence_plan_sha256, "sequence_plan_sha256")
        if not self.families or not self.beat_requirements:
            raise ContractValidationError(
                "adult craft need v3 requires families and beat requirements"
            )
        _unique((value.value for value in self.families), "craft-need families")
        _unique(self.subfamilies, "craft-need subfamilies")
        _unique(
            (str(value.beat_id) for value in self.beat_requirements),
            "craft-need beats",
        )
        _unique(
            (str(value.character_id) for value in self.character_card_sections),
            "craft-need character cards",
        )

    @property
    def axes(self) -> tuple[AdultCraftAxis, ...]:
        return tuple(
            dict.fromkeys(
                axis
                for requirement in self.beat_requirements
                for axis in requirement.axes
            )
        )

    @property
    def channel_needs(self) -> tuple[
        CharacterChannelCraftNeedV3 | SceneChannelCraftNeedV3, ...
    ]:
        return tuple(
            dict.fromkeys(
                channel
                for requirement in self.beat_requirements
                for channel in requirement.channel_requirements
            )
        )

    @property
    def beat_needs(self) -> tuple[BeatCraftRequirementV3, ...]:
        return self.beat_requirements

    @property
    def need_sha256(self) -> str:
        return domain_sha256("cera.adult_craft_need.v3", self)


@dataclass(frozen=True, slots=True)
class AdultCraftNeed:
    SCHEMA_VERSION: ClassVar[str] = "cera.adult_craft_need.v1"

    schema_version: str
    craft_need_id: TypedId
    request_id: TypedId
    decision_id: TypedId
    sequence_plan_sha256: str
    mode: AdultCraftMode
    families: tuple[AdultCraftFamily, ...]
    subfamilies: tuple[str, ...]
    axes: tuple[AdultCraftAxis, ...]
    channel_needs: tuple[ChannelCraftNeed, ...]
    beat_needs: tuple[BeatCraftNeed, ...]
    climax: OutcomeScope
    aftermath: OutcomeScope
    character_card_sections: tuple[CharacterCardSectionNeed, ...]
    all_participants_confirmed_adults: bool
    consent_valid_for_current_segment: bool
    blocked_nonconsensual_generation_excluded: bool

    def __post_init__(self) -> None:
        require_schema(self.schema_version, self.SCHEMA_VERSION, type(self).__name__)
        require_kind(self.craft_need_id, IdKind.ADULT_CRAFT_NEED, "craft_need_id")
        require_kind(self.request_id, IdKind.REQUEST, "request_id")
        require_kind(self.decision_id, IdKind.DECISION, "decision_id")
        _sha256(self.sequence_plan_sha256, "sequence_plan_sha256")
        if not self.families or not self.axes or not self.channel_needs or not self.beat_needs:
            raise ContractValidationError("adult craft need requires families, axes, channels, and beats")
        _unique((value.value for value in self.families), "craft-need families")
        _unique(self.subfamilies, "craft-need subfamilies")
        _unique((value.value for value in self.axes), "craft-need axes")
        _unique((str(value.beat_id) for value in self.beat_needs), "craft-need beats")
        _unique(
            (f"{value.channel.value}|{value.character_id}" for value in self.channel_needs),
            "craft-need channels",
        )
        _unique(
            (str(value.character_id) for value in self.character_card_sections),
            "craft-need character cards",
        )
        if not (
            self.all_participants_confirmed_adults
            and self.consent_valid_for_current_segment
            and self.blocked_nonconsensual_generation_excluded
        ):
            raise ContractValidationError("adult craft need cannot represent a blocked or unresolved route")

    @property
    def need_sha256(self) -> str:
        return domain_sha256("cera.adult_craft_need.v1", self)


@dataclass(frozen=True, slots=True)
class AdultCraftSelectionReceipt:
    SCHEMA_VERSION: ClassVar[str] = "cera.adult_craft_selection_receipt.v1"

    schema_version: str
    selection_id: TypedId
    catalog_id: TypedId
    catalog_manifest_sha256: str
    craft_need_id: TypedId
    craft_need_sha256: str
    selected_fragment_ids: tuple[TypedId, ...]
    selected_fragment_sha256: tuple[str, ...]
    selected_modes: tuple[AdultCraftMode, ...]
    covered_requirements: tuple[str, ...]
    uncovered_requirements: tuple[str, ...]
    total_craft_bytes: int
    maximum_craft_bytes: int
    selection_policy_version: str
    story_state_committed: bool
    external_provider_calls: int

    def __post_init__(self) -> None:
        require_schema(self.schema_version, self.SCHEMA_VERSION, type(self).__name__)
        require_kind(self.selection_id, IdKind.CRAFT_SELECTION, "selection_id")
        require_kind(self.catalog_id, IdKind.CRAFT_CATALOG, "catalog_id")
        require_kind(self.craft_need_id, IdKind.ADULT_CRAFT_NEED, "craft_need_id")
        _sha256(self.catalog_manifest_sha256, "catalog_manifest_sha256")
        _sha256(self.craft_need_sha256, "craft_need_sha256")
        for value in self.selected_fragment_ids:
            require_kind(value, IdKind.CRAFT_REFERENCE, "selected_fragment_ids")
        for value in self.selected_fragment_sha256:
            _sha256(value, "selected_fragment_sha256")
        if len(self.selected_fragment_ids) != len(self.selected_fragment_sha256):
            raise ContractValidationError("selected fragment IDs and hashes differ in length")
        _unique((str(value) for value in self.selected_fragment_ids), "selected fragment IDs")
        _unique(self.covered_requirements, "covered requirements")
        _unique(self.uncovered_requirements, "uncovered requirements")
        if self.uncovered_requirements:
            raise ContractValidationError("accepted craft selection cannot leave requirements uncovered")
        if not self.selected_fragment_ids or not self.selected_modes:
            raise ContractValidationError("craft selection requires fragments and modes")
        if not 0 < self.total_craft_bytes <= self.maximum_craft_bytes:
            raise ContractValidationError("craft selection byte budget is invalid")
        _non_empty(self.selection_policy_version, "selection_policy_version")
        if self.story_state_committed or self.external_provider_calls:
            raise ContractValidationError("craft selection cannot call providers or commit story state")

    @property
    def selection_sha256(self) -> str:
        return domain_sha256("cera.adult_craft_selection_receipt.v1", self)


@dataclass(frozen=True, slots=True)
class SpecificityTermRequirement:
    concept: AdultCraftConcept
    alternatives: tuple[str, ...]
    minimum_matches: int

    def __post_init__(self) -> None:
        if not self.alternatives:
            raise ContractValidationError("specificity term requirement needs alternatives")
        _unique((value.casefold() for value in self.alternatives), "specificity alternatives")
        if not 1 <= self.minimum_matches <= len(self.alternatives):
            raise ContractValidationError("specificity minimum matches is invalid")


@dataclass(frozen=True, slots=True)
class SpecificityBeatRequirement:
    beat_id: TypedId
    channel: RealizationChannel
    character_id: TypedId | None
    minimum_register: SpecificityRegister
    terms: tuple[SpecificityTermRequirement, ...]

    def __post_init__(self) -> None:
        require_kind(self.beat_id, IdKind.BEAT, "beat_id")
        if self.character_id is not None:
            require_kind(self.character_id, IdKind.CHARACTER, "character_id")
        if self.channel in {RealizationChannel.DIALOGUE, RealizationChannel.INNER_VOICE}:
            if self.character_id is None:
                raise ContractValidationError("character channel specificity requires an owner")
        elif self.character_id is not None:
            raise ContractValidationError("non-character specificity channel cannot name an owner")
        _unique((value.concept.value for value in self.terms), "beat specificity concepts")

    @property
    def obligation_key(self) -> str:
        """Stable provider-neutral local key for this Python-owned obligation."""

        digest = domain_sha256(
            "cera.specificity_obligation.v1",
            {
                "beat_id": str(self.beat_id),
                "channel": self.channel.value,
                "character_id": (
                    str(self.character_id) if self.character_id is not None else None
                ),
                "minimum_register": self.minimum_register.value,
                "terms": tuple(
                    {
                        "concept": value.concept.value,
                        "alternatives": value.alternatives,
                        "minimum_matches": value.minimum_matches,
                    }
                    for value in self.terms
                ),
            },
        )
        return f"spec_{digest[:24]}"


@dataclass(frozen=True, slots=True)
class SpecificityContract:
    SCHEMA_VERSION: ClassVar[str] = "cera.specificity_contract.v1"

    schema_version: str
    specificity_contract_id: TypedId
    request_id: TypedId
    decision_id: TypedId
    craft_need_sha256: str
    craft_selection_sha256: str
    beat_requirements: tuple[SpecificityBeatRequirement, ...]
    climax: OutcomeScope
    aftermath: OutcomeScope
    conditional_layers_not_quotas: bool
    bodily_response_not_consent: bool
    blocked_nonconsensual_generation_excluded: bool

    def __post_init__(self) -> None:
        require_schema(self.schema_version, self.SCHEMA_VERSION, type(self).__name__)
        require_kind(
            self.specificity_contract_id,
            IdKind.SPECIFICITY_CONTRACT,
            "specificity_contract_id",
        )
        require_kind(self.request_id, IdKind.REQUEST, "request_id")
        require_kind(self.decision_id, IdKind.DECISION, "decision_id")
        _sha256(self.craft_need_sha256, "craft_need_sha256")
        _sha256(self.craft_selection_sha256, "craft_selection_sha256")
        _unique(
            (
                f"{value.beat_id}|{value.channel.value}|{value.character_id}"
                for value in self.beat_requirements
            ),
            "specificity beat/channel requirements",
        )
        if not (
            self.conditional_layers_not_quotas
            and self.bodily_response_not_consent
            and self.blocked_nonconsensual_generation_excluded
        ):
            raise ContractValidationError("specificity contract weakened a hard adult boundary")

    @property
    def contract_sha256(self) -> str:
        return domain_sha256("cera.specificity_contract.v1", self)


@dataclass(frozen=True, slots=True)
class SpecificityFinding:
    beat_id: TypedId
    channel: RealizationChannel
    character_id: TypedId | None
    concept: str
    code: str

    def __post_init__(self) -> None:
        require_kind(self.beat_id, IdKind.BEAT, "beat_id")
        if self.character_id is not None:
            require_kind(self.character_id, IdKind.CHARACTER, "character_id")
        _non_empty(self.concept, "finding concept")
        _non_empty(self.code, "finding code")


@dataclass(frozen=True, slots=True)
class SpecificityValidationReceipt:
    SCHEMA_VERSION: ClassVar[str] = "cera.specificity_validation_receipt.v1"

    schema_version: str
    validation_receipt_id: TypedId
    specificity_contract_sha256: str
    candidate_sha256: str
    manifest_sha256: str
    status: str
    findings: tuple[SpecificityFinding, ...]
    repairable_beat_ids: tuple[TypedId, ...]
    semantic_quality_proven: bool
    story_state_committed: bool

    def __post_init__(self) -> None:
        require_schema(self.schema_version, self.SCHEMA_VERSION, type(self).__name__)
        require_kind(self.validation_receipt_id, IdKind.SPECIFICITY_VALIDATION, "validation_receipt_id")
        _sha256(self.specificity_contract_sha256, "specificity_contract_sha256")
        _sha256(self.candidate_sha256, "candidate_sha256")
        _sha256(self.manifest_sha256, "manifest_sha256")
        if self.status not in {"accepted", "repair_required"}:
            raise ContractValidationError("unsupported specificity validation status")
        if (self.status == "accepted") != (not self.findings):
            raise ContractValidationError("specificity status does not match findings")
        for value in self.repairable_beat_ids:
            require_kind(value, IdKind.BEAT, "repairable_beat_ids")
        _unique((str(value) for value in self.repairable_beat_ids), "repairable beat IDs")
        if set(self.repairable_beat_ids) != {value.beat_id for value in self.findings}:
            raise ContractValidationError("repairable beats must exactly cover findings")
        if self.semantic_quality_proven or self.story_state_committed:
            raise ContractValidationError("specificity validation proves neither semantics nor commit")

    @property
    def receipt_sha256(self) -> str:
        return domain_sha256("cera.specificity_validation_receipt.v1", self)


@dataclass(frozen=True, slots=True)
class SemanticActionBinding:
    beat_id: TypedId
    actor_id: TypedId
    action_family: str
    object_concepts: tuple[AdultCraftConcept, ...]
    required_material_outcomes: tuple[AdultCraftConcept, ...]

    def __post_init__(self) -> None:
        require_kind(self.beat_id, IdKind.BEAT, "beat_id")
        if self.actor_id.kind not in {IdKind.CHARACTER, IdKind.MATERIAL}:
            raise ContractValidationError("semantic actor must be a character or material")
        _non_empty(self.action_family, "semantic action family")
        _unique((value.value for value in self.object_concepts), "semantic object concepts")
        _unique(
            (value.value for value in self.required_material_outcomes),
            "semantic material outcomes",
        )
        if not set(self.required_material_outcomes).issubset(set(self.object_concepts)):
            raise ContractValidationError("material outcomes must be locked object concepts")


@dataclass(frozen=True, slots=True)
class SemanticRequiredTransition:
    from_beat_id: TypedId
    to_beat_id: TypedId

    def __post_init__(self) -> None:
        require_kind(self.from_beat_id, IdKind.BEAT, "from_beat_id")
        require_kind(self.to_beat_id, IdKind.BEAT, "to_beat_id")
        if self.from_beat_id == self.to_beat_id:
            raise ContractValidationError("semantic transition cannot point to itself")


@dataclass(frozen=True, slots=True)
class SemanticBeatSpan:
    beat_id: TypedId
    start: int
    end: int
    exact_text: str
    exact_text_sha256: str
    channels: tuple[RealizationChannel, ...]

    def __post_init__(self) -> None:
        require_kind(self.beat_id, IdKind.BEAT, "beat_id")
        if self.start < 0 or self.end <= self.start:
            raise ContractValidationError("semantic beat span is invalid")
        _non_empty(self.exact_text, "semantic beat text")
        _sha256(self.exact_text_sha256, "semantic beat text hash")
        if text_sha256(self.exact_text) != self.exact_text_sha256:
            raise ContractValidationError("semantic beat text hash mismatch")
        if len(self.exact_text) != self.end - self.start:
            raise ContractValidationError("semantic beat text does not match its locked span")
        if not self.channels:
            raise ContractValidationError("semantic beat span requires channels")
        _unique((value.value for value in self.channels), "semantic beat channels")


@dataclass(frozen=True, slots=True)
class SemanticSpecificityRequest:
    SCHEMA_VERSION: ClassVar[str] = "cera.semantic_specificity_request.v1"

    schema_version: str
    semantic_request_id: TypedId
    story_request_id: TypedId
    decision_id: TypedId
    specificity_contract_id: TypedId
    specificity_contract_sha256: str
    candidate_sha256: str
    manifest_sha256: str
    stage: SemanticVerificationStage
    selected_character_ids: tuple[TypedId, ...]
    beat_spans: tuple[SemanticBeatSpan, ...]
    action_bindings: tuple[SemanticActionBinding, ...]
    required_transitions: tuple[SemanticRequiredTransition, ...]
    prior_semantic_receipt_sha256: str | None
    coverage_declarations_advisory_only: bool
    authority_additions_forbidden: bool
    story_state_committed: bool

    def __post_init__(self) -> None:
        require_schema(self.schema_version, self.SCHEMA_VERSION, type(self).__name__)
        require_kind(self.semantic_request_id, IdKind.SEMANTIC_SPECIFICITY, "semantic_request_id")
        require_kind(self.story_request_id, IdKind.REQUEST, "story_request_id")
        require_kind(self.decision_id, IdKind.DECISION, "decision_id")
        require_kind(
            self.specificity_contract_id,
            IdKind.SPECIFICITY_CONTRACT,
            "specificity_contract_id",
        )
        for value in (
            self.specificity_contract_sha256,
            self.candidate_sha256,
            self.manifest_sha256,
        ):
            _sha256(value, "semantic specificity hash")
        for value in self.selected_character_ids:
            require_kind(value, IdKind.CHARACTER, "selected_character_ids")
        _unique((str(value) for value in self.selected_character_ids), "semantic selected characters")
        if not self.beat_spans or not self.action_bindings:
            raise ContractValidationError("semantic verification requires beat spans and bindings")
        span_ids = tuple(value.beat_id for value in self.beat_spans)
        binding_ids = tuple(value.beat_id for value in self.action_bindings)
        _unique((str(value) for value in span_ids), "semantic beat spans")
        _unique((str(value) for value in binding_ids), "semantic action bindings")
        if set(span_ids) != set(binding_ids):
            raise ContractValidationError("semantic spans and action bindings must cover the same beats")
        _unique(
            (f"{value.from_beat_id}|{value.to_beat_id}" for value in self.required_transitions),
            "semantic transitions",
        )
        if self.stage is SemanticVerificationStage.INITIAL:
            if self.prior_semantic_receipt_sha256 is not None:
                raise ContractValidationError("initial semantic verification cannot name a prior receipt")
        elif self.prior_semantic_receipt_sha256 is None:
            raise ContractValidationError("post-repair semantic verification requires prior receipt")
        else:
            _sha256(self.prior_semantic_receipt_sha256, "prior semantic receipt hash")
        if not self.coverage_declarations_advisory_only or not self.authority_additions_forbidden:
            raise ContractValidationError("semantic verifier cannot trust declarations or add authority")
        if self.story_state_committed:
            raise ContractValidationError("semantic verification cannot run on committed story state")

    @property
    def request_sha256(self) -> str:
        return domain_sha256("cera.semantic_specificity_request.v1", self)


@dataclass(frozen=True, slots=True)
class SemanticSpecificityFinding:
    beat_id: TypedId
    code: SemanticSpecificityFindingCode

    def __post_init__(self) -> None:
        require_kind(self.beat_id, IdKind.BEAT, "beat_id")


@dataclass(frozen=True, slots=True)
class SemanticSpecificityResult:
    SCHEMA_VERSION: ClassVar[str] = "cera.semantic_specificity_result.v1"

    schema_version: str
    result_id: TypedId
    semantic_request_id: TypedId
    semantic_request_sha256: str
    stage: SemanticVerificationStage
    status: str
    findings: tuple[SemanticSpecificityFinding, ...]
    repairable_beat_ids: tuple[TypedId, ...]
    authority_changed: bool
    coverage_declarations_treated_as_proof: bool

    def __post_init__(self) -> None:
        require_schema(self.schema_version, self.SCHEMA_VERSION, type(self).__name__)
        require_kind(self.result_id, IdKind.SEMANTIC_SPECIFICITY, "result_id")
        require_kind(self.semantic_request_id, IdKind.SEMANTIC_SPECIFICITY, "semantic_request_id")
        _sha256(self.semantic_request_sha256, "semantic request hash")
        expected = (
            "accepted"
            if not self.findings
            else "repair_required"
            if self.stage is SemanticVerificationStage.INITIAL
            else "rejected"
        )
        if self.status != expected:
            raise ContractValidationError("semantic result status does not match stage/findings")
        for value in self.repairable_beat_ids:
            require_kind(value, IdKind.BEAT, "repairable_beat_ids")
        _unique((str(value) for value in self.repairable_beat_ids), "semantic repairable beats")
        expected_repairable = (
            {value.beat_id for value in self.findings}
            if self.stage is SemanticVerificationStage.INITIAL
            else set()
        )
        if set(self.repairable_beat_ids) != expected_repairable:
            raise ContractValidationError("semantic repairable beats do not match stage/findings")
        if self.authority_changed or self.coverage_declarations_treated_as_proof:
            raise ContractValidationError("semantic result cannot change authority or trust declarations")

    @property
    def result_sha256(self) -> str:
        return domain_sha256("cera.semantic_specificity_result.v1", self)


@dataclass(frozen=True, slots=True)
class SemanticSpecificityReceipt:
    SCHEMA_VERSION: ClassVar[str] = "cera.semantic_specificity_receipt.v1"

    schema_version: str
    receipt_id: TypedId
    semantic_request_id: TypedId
    semantic_request_sha256: str
    result_id: TypedId
    result_sha256: str
    adapter_role: SemanticSpecificityAdapterRole
    adapter_version: str
    adapter_evidence_id: str
    adapter_evidence_sha256: str
    invocation_count: int
    external_provider_calls: int
    retry_count: int
    fallback_used: bool
    exact_beat_text_retained: bool
    raw_candidate_retained: bool
    story_state_committed: bool

    def __post_init__(self) -> None:
        require_schema(self.schema_version, self.SCHEMA_VERSION, type(self).__name__)
        require_kind(self.receipt_id, IdKind.SEMANTIC_SPECIFICITY, "receipt_id")
        require_kind(self.semantic_request_id, IdKind.SEMANTIC_SPECIFICITY, "semantic_request_id")
        require_kind(self.result_id, IdKind.SEMANTIC_SPECIFICITY, "result_id")
        for value in (
            self.semantic_request_sha256,
            self.result_sha256,
            self.adapter_evidence_sha256,
        ):
            _sha256(value, "semantic receipt hash")
        _non_empty(self.adapter_version, "semantic adapter version")
        _non_empty(self.adapter_evidence_id, "semantic adapter evidence ID")
        if self.invocation_count != 1:
            raise ContractValidationError("semantic verifier requires exactly one invocation per request")
        if self.adapter_role is SemanticSpecificityAdapterRole.SCRIPTED_FAKE:
            if self.external_provider_calls != 0:
                raise ContractValidationError("fake semantic verifier cannot call a provider")
        if self.retry_count or self.fallback_used:
            raise ContractValidationError("semantic verifier cannot retry or fall back")
        if self.exact_beat_text_retained or self.raw_candidate_retained:
            raise ContractValidationError("semantic receipt cannot retain candidate content")
        if self.story_state_committed:
            raise ContractValidationError("semantic verification cannot commit story state")

    @property
    def receipt_sha256(self) -> str:
        return domain_sha256("cera.semantic_specificity_receipt.v1", self)


@dataclass(frozen=True, slots=True)
class BeatScopedRepairRequest:
    SCHEMA_VERSION: ClassVar[str] = "cera.beat_scoped_repair_request.v2"

    schema_version: str
    repair_request_id: TypedId
    request_id: TypedId
    target_beat_id: TypedId
    composer_request_sha256: str
    original_candidate_sha256: str
    original_manifest_sha256: str
    specificity_contract_sha256: str
    specificity_validation_receipt_sha256: str
    semantic_specificity_receipt_sha256: str
    original_span_start: int
    original_span_end: int
    original_span_sha256: str
    attempt: int
    maximum_attempts: int
    non_target_content_locked: bool
    full_reply_replacement_forbidden: bool
    story_state_committed: bool

    def __post_init__(self) -> None:
        require_schema(self.schema_version, self.SCHEMA_VERSION, type(self).__name__)
        require_kind(self.repair_request_id, IdKind.BEAT_REPAIR, "repair_request_id")
        require_kind(self.request_id, IdKind.REQUEST, "request_id")
        require_kind(self.target_beat_id, IdKind.BEAT, "target_beat_id")
        for value in (
            self.composer_request_sha256,
            self.original_candidate_sha256,
            self.original_manifest_sha256,
            self.specificity_contract_sha256,
            self.specificity_validation_receipt_sha256,
            self.semantic_specificity_receipt_sha256,
            self.original_span_sha256,
        ):
            _sha256(value, "beat repair hash")
        if self.original_span_start < 0 or self.original_span_end <= self.original_span_start:
            raise ContractValidationError("beat repair span is invalid")
        if self.attempt != 1 or self.maximum_attempts != 1:
            raise ContractValidationError("beat repair permits exactly one bounded attempt")
        if not self.non_target_content_locked or not self.full_reply_replacement_forbidden:
            raise ContractValidationError("beat repair must lock all non-target content")
        if self.story_state_committed:
            raise ContractValidationError("beat repair cannot operate on committed story state")

    @property
    def request_sha256(self) -> str:
        return domain_sha256("cera.beat_scoped_repair_request.v2", self)


@dataclass(frozen=True, slots=True)
class BeatScopedRepairReceipt:
    SCHEMA_VERSION: ClassVar[str] = "cera.beat_scoped_repair_receipt.v1"

    schema_version: str
    repair_receipt_id: TypedId
    repair_request_sha256: str
    original_candidate_sha256: str
    repaired_candidate_sha256: str
    repaired_manifest_sha256: str
    specificity_validation_receipt_sha256: str
    status: str
    attempt_count: int
    recursive_retry_permitted: bool
    external_provider_calls: int
    story_state_committed: bool

    def __post_init__(self) -> None:
        require_schema(self.schema_version, self.SCHEMA_VERSION, type(self).__name__)
        require_kind(self.repair_receipt_id, IdKind.BEAT_REPAIR, "repair_receipt_id")
        for value in (
            self.repair_request_sha256,
            self.original_candidate_sha256,
            self.repaired_candidate_sha256,
            self.repaired_manifest_sha256,
            self.specificity_validation_receipt_sha256,
        ):
            _sha256(value, "beat repair receipt hash")
        if self.status not in {"accepted_for_full_revalidation", "rejected"}:
            raise ContractValidationError("unsupported beat repair status")
        if self.attempt_count != 1 or self.recursive_retry_permitted:
            raise ContractValidationError("beat repair cannot recurse")
        if self.external_provider_calls or self.story_state_committed:
            raise ContractValidationError("provider-free repair cannot call providers or commit")

    @property
    def receipt_sha256(self) -> str:
        return domain_sha256("cera.beat_scoped_repair_receipt.v1", self)


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


def _validate_v3_concepts(
    semantic_concepts: tuple[AdultCraftConcept, ...],
    lexical_concepts: tuple[AdultCraftConcept, ...],
) -> None:
    _unique(
        (value.value for value in semantic_concepts),
        "channel semantic concepts",
    )
    _unique(
        (value.value for value in lexical_concepts),
        "channel lexical concepts",
    )
    if not set(lexical_concepts).issubset(semantic_concepts):
        raise ContractValidationError(
            "channel lexical concepts must be a subset of semantic concepts"
        )


def _relative_path(value: str) -> None:
    _non_empty(value, "relative path")
    normalized = value.replace("\\", "/")
    if normalized.startswith("/") or ":" in normalized or ".." in normalized.split("/"):
        raise ContractValidationError("catalog paths must be repository-relative")
