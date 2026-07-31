"""Typed contracts for the inactive modular Composer prompt seam.

These records are provider-free.  They cannot dispatch a model, publish a
story, select participants, establish consent, or mutate the SequencePlan.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import PurePosixPath
import re
from typing import ClassVar

from cera.contracts import AdultRenderingMode, SceneDepthMode
from cera.errors import ContractValidationError
from cera.ids import IdKind, TypedId, require_kind
from cera.schema import require_schema
from cera.serialization import domain_sha256, text_sha256


_TOKEN_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,127}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class PromptModuleLayer(StrEnum):
    CORE_AUTHORITY = "core_authority"
    STRUCTURED_OUTPUT = "structured_output"
    DEPTH = "depth"
    ADULT_RENDERING = "adult_rendering"
    INTERACTION_TOPOLOGY = "interaction_topology"
    SCENE_FUNCTION = "scene_function"


class PromptModuleEnabledState(StrEnum):
    """There is deliberately no active state in scaffolding v1."""

    DISABLED = "disabled"
    FIXTURE_ONLY = "fixture_only"
    SHADOW_ONLY = "shadow_only"


class PromptCompilerState(StrEnum):
    """Compiler v1 cannot create a provider-dispatchable result."""

    FIXTURE_ONLY = "fixture_only"
    SHADOW_ONLY = "shadow_only"


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


class PromptMaterialKind(StrEnum):
    CHARACTER_EXPRESSION = "character_expression"
    CONTINUITY = "continuity"
    EVIDENCE = "evidence"


def _non_empty(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ContractValidationError(f"{name} must be non-empty")


def _token(value: str, name: str) -> None:
    if not isinstance(value, str) or _TOKEN_RE.fullmatch(value) is None:
        raise ContractValidationError(f"{name} is invalid")


def _sha256(value: str, name: str) -> None:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise ContractValidationError(f"{name} must be a lowercase SHA-256")


def _unique(values, name: str) -> None:
    materialized = tuple(values)
    if len(materialized) != len(set(materialized)):
        raise ContractValidationError(f"{name} must be unique")


def _relative_path(value: str) -> None:
    _non_empty(value, "relative_path")
    if "\\" in value or ":" in value:
        raise ContractValidationError("prompt module path must use repository-local POSIX form")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or path.suffix != ".md":
        raise ContractValidationError("prompt module path is not an allowlisted Markdown path")


@dataclass(frozen=True, slots=True)
class PromptModuleManifestEntry:
    module_id: str
    version: str
    layer: PromptModuleLayer
    relative_path: str
    sha256: str
    deterministic_order: int
    allowed_scene_depth_modes: tuple[SceneDepthMode, ...]
    allowed_adult_rendering_modes: tuple[AdultRenderingMode, ...]
    allowed_interaction_topologies: tuple[InteractionTopology, ...]
    allowed_scene_functions: tuple[SceneFunction, ...]
    dependencies: tuple[str, ...]
    conflicts: tuple[str, ...]
    noncanonical: bool
    noncopyable: bool
    source_provenance: str
    enabled_state: PromptModuleEnabledState

    def __post_init__(self) -> None:
        _token(self.module_id, "module_id")
        _token(self.version, "module version")
        _relative_path(self.relative_path)
        _sha256(self.sha256, "module sha256")
        if type(self.deterministic_order) is not int or self.deterministic_order < 0:
            raise ContractValidationError("module deterministic order is invalid")
        _unique((value.value for value in self.allowed_scene_depth_modes), "depth selectors")
        _unique((value.value for value in self.allowed_adult_rendering_modes), "adult selectors")
        _unique((value.value for value in self.allowed_interaction_topologies), "topology selectors")
        _unique((value.value for value in self.allowed_scene_functions), "scene selectors")
        _unique(self.dependencies, "module dependencies")
        _unique(self.conflicts, "module conflicts")
        for value in (*self.dependencies, *self.conflicts):
            _token(value, "module relationship")
        if self.module_id in {*self.dependencies, *self.conflicts}:
            raise ContractValidationError("prompt module cannot depend on or conflict with itself")
        _non_empty(self.source_provenance, "source_provenance")
        self._validate_layer_selector()

    def _validate_layer_selector(self) -> None:
        constrained = (
            bool(self.allowed_scene_depth_modes),
            bool(self.allowed_adult_rendering_modes),
            bool(self.allowed_interaction_topologies),
            bool(self.allowed_scene_functions),
        )
        expected = {
            PromptModuleLayer.CORE_AUTHORITY: (False, False, False, False),
            PromptModuleLayer.STRUCTURED_OUTPUT: (False, False, False, False),
            PromptModuleLayer.DEPTH: (True, False, False, False),
            PromptModuleLayer.ADULT_RENDERING: (False, True, False, False),
            PromptModuleLayer.INTERACTION_TOPOLOGY: (False, False, True, False),
            PromptModuleLayer.SCENE_FUNCTION: (False, False, False, True),
        }[self.layer]
        if constrained != expected:
            raise ContractValidationError("module selector constraints do not match its layer")
        selected = {
            PromptModuleLayer.DEPTH: self.allowed_scene_depth_modes,
            PromptModuleLayer.ADULT_RENDERING: self.allowed_adult_rendering_modes,
            PromptModuleLayer.INTERACTION_TOPOLOGY: self.allowed_interaction_topologies,
            PromptModuleLayer.SCENE_FUNCTION: self.allowed_scene_functions,
        }.get(self.layer, ())
        if len(selected) > 1:
            raise ContractValidationError("one prompt module may bind only one selector value")


@dataclass(frozen=True, slots=True)
class PromptModuleRegistryManifest:
    SCHEMA_VERSION: ClassVar[str] = "cera.prompt_module_registry_manifest.v1"

    schema_version: str
    registry_version: str
    modules: tuple[PromptModuleManifestEntry, ...]
    runtime_external_path_dependencies: tuple[str, ...]
    creative_prompt_limit_enforced: bool
    fixed_example_count_enforced: bool

    def __post_init__(self) -> None:
        require_schema(self.schema_version, self.SCHEMA_VERSION, type(self).__name__)
        _token(self.registry_version, "registry_version")
        if not self.modules:
            raise ContractValidationError("prompt module registry cannot be empty")
        _unique((value.module_id for value in self.modules), "prompt module IDs")
        _unique((value.relative_path for value in self.modules), "prompt module paths")
        _unique((value.deterministic_order for value in self.modules), "prompt module order")
        if self.runtime_external_path_dependencies:
            raise ContractValidationError("prompt registry cannot depend on external paths")
        if self.creative_prompt_limit_enforced or self.fixed_example_count_enforced:
            raise ContractValidationError("scaffolding cannot enforce creative size or example limits")
        by_id = {value.module_id: value for value in self.modules}
        for module in self.modules:
            for dependency in module.dependencies:
                if dependency not in by_id:
                    raise ContractValidationError("prompt module dependency is unknown")
                if by_id[dependency].deterministic_order >= module.deterministic_order:
                    raise ContractValidationError("prompt module dependency must be ordered earlier")
            for conflict in module.conflicts:
                if conflict not in by_id:
                    raise ContractValidationError("prompt module conflict is unknown")

    @property
    def manifest_sha256(self) -> str:
        return domain_sha256(self.SCHEMA_VERSION, self)


@dataclass(frozen=True, slots=True)
class PromptModuleReference:
    module_id: str
    version: str
    layer: PromptModuleLayer
    sha256: str
    deterministic_order: int
    relative_path: str
    source_provenance: str
    selection_reason: str
    noncanonical: bool
    noncopyable: bool

    def __post_init__(self) -> None:
        _token(self.module_id, "module reference ID")
        _token(self.version, "module reference version")
        _sha256(self.sha256, "module reference sha256")
        _relative_path(self.relative_path)
        _non_empty(self.source_provenance, "module reference provenance")
        _non_empty(self.selection_reason, "module selection reason")


@dataclass(frozen=True, slots=True)
class PromptModuleContribution:
    reference: PromptModuleReference
    content_bytes: int
    estimated_tokens: int

    def __post_init__(self) -> None:
        if self.content_bytes < 1 or self.estimated_tokens < 1:
            raise ContractValidationError("prompt module contribution must be positive")


@dataclass(frozen=True, slots=True)
class PromptMaterialBlock:
    material_id: str
    kind: PromptMaterialKind
    owner_character_id: TypedId | None
    applicable_character_ids: tuple[TypedId, ...]
    private_to_owner: bool
    text: str
    text_sha256: str
    source_provenance: str
    selection_reason: str

    def __post_init__(self) -> None:
        _token(self.material_id, "material_id")
        if self.owner_character_id is not None:
            require_kind(self.owner_character_id, IdKind.CHARACTER, "material owner")
        for value in self.applicable_character_ids:
            require_kind(value, IdKind.CHARACTER, "material applicability")
        _unique((str(value) for value in self.applicable_character_ids), "material characters")
        if self.private_to_owner:
            if self.owner_character_id is None or self.owner_character_id not in self.applicable_character_ids:
                raise ContractValidationError("private prompt material must bind its owner")
        _non_empty(self.text, "material text")
        _sha256(self.text_sha256, "material text sha256")
        if text_sha256(self.text) != self.text_sha256:
            raise ContractValidationError("prompt material text hash mismatch")
        _non_empty(self.source_provenance, "material provenance")
        _non_empty(self.selection_reason, "material selection reason")


@dataclass(frozen=True, slots=True)
class PromptCraftItem:
    fragment_id: str
    version: str
    fragment_sha256: str
    allowed_adult_rendering_modes: tuple[AdultRenderingMode, ...]
    deterministic_order: int
    text: str
    source_provenance: str
    selection_reason: str
    noncanonical: bool
    noncopyable: bool
    is_example: bool

    def __post_init__(self) -> None:
        _non_empty(self.fragment_id, "craft fragment ID")
        _token(self.version, "craft fragment version")
        _sha256(self.fragment_sha256, "craft fragment sha256")
        if not self.allowed_adult_rendering_modes:
            raise ContractValidationError("craft item requires an Adult mode")
        if AdultRenderingMode.OFF in self.allowed_adult_rendering_modes:
            raise ContractValidationError("Adult OFF cannot authorize craft material")
        _unique((value.value for value in self.allowed_adult_rendering_modes), "craft modes")
        if type(self.deterministic_order) is not int or self.deterministic_order < 0:
            raise ContractValidationError("craft item order is invalid")
        _non_empty(self.text, "craft text")
        _non_empty(self.source_provenance, "craft provenance")
        _non_empty(self.selection_reason, "craft selection reason")
        if not self.noncanonical or not self.noncopyable:
            raise ContractValidationError("craft items must be noncanonical and noncopyable")


@dataclass(frozen=True, slots=True)
class PromptSelectionRequest:
    SCHEMA_VERSION: ClassVar[str] = "cera.prompt_selection_request.v1"

    schema_version: str
    scene_depth_mode: SceneDepthMode
    adult_rendering_mode: AdultRenderingMode
    interaction_topology: InteractionTopology
    scene_function: SceneFunction
    tone: PromptTone
    interiority_level: InteriorityLevel
    selected_character_ids: tuple[TypedId, ...]
    adult_route_eligible: bool
    route_blocked: bool
    target_provider_model: str
    sequence_plan_sha256: str
    reasoner_requested_module_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        require_schema(self.schema_version, self.SCHEMA_VERSION, type(self).__name__)
        if not self.selected_character_ids:
            raise ContractValidationError("prompt selection requires selected characters")
        for value in self.selected_character_ids:
            require_kind(value, IdKind.CHARACTER, "selected_character_ids")
        _unique((str(value) for value in self.selected_character_ids), "selected characters")
        _non_empty(self.target_provider_model, "target_provider_model")
        _sha256(self.sequence_plan_sha256, "sequence_plan_sha256")
        if self.reasoner_requested_module_ids:
            raise ContractValidationError("Reasoner cannot select prompt modules")
        if self.adult_rendering_mode is not AdultRenderingMode.OFF:
            if not self.adult_route_eligible or self.route_blocked:
                raise ContractValidationError("Adult rendering cannot bypass route eligibility")
        count = len(self.selected_character_ids)
        if count == 1 and self.interaction_topology is not InteractionTopology.SINGLE_NPC_FLOOR:
            raise ContractValidationError("single selected NPC requires single_npc_floor")
        if count > 1 and self.interaction_topology is InteractionTopology.SINGLE_NPC_FLOOR:
            raise ContractValidationError("multi-NPC selection cannot use single_npc_floor")

    @property
    def selection_sha256(self) -> str:
        return domain_sha256(self.SCHEMA_VERSION, self)


@dataclass(frozen=True, slots=True)
class PromptSelectionResult:
    request_sha256: str
    selected_prompt_modules: tuple[PromptModuleReference, ...]

    def __post_init__(self) -> None:
        _sha256(self.request_sha256, "selection request sha256")
        if not self.selected_prompt_modules:
            raise ContractValidationError("prompt selection cannot be empty")
        _unique((value.module_id for value in self.selected_prompt_modules), "selected modules")
        orders = tuple(value.deterministic_order for value in self.selected_prompt_modules)
        if orders != tuple(sorted(orders)):
            raise ContractValidationError("selected prompt modules are out of order")


@dataclass(frozen=True, slots=True)
class PromptCompilationReceipt:
    SCHEMA_VERSION: ClassVar[str] = "cera.prompt_compilation_receipt.v1"

    schema_version: str
    compiler_version: str
    compiler_state: PromptCompilerState
    registry_manifest_sha256: str
    selection_request_sha256: str
    sequence_plan_sha256: str
    selected_cast_sha256: str
    target_provider_model: str
    scene_depth_mode: SceneDepthMode
    adult_rendering_mode: AdultRenderingMode
    selected_prompt_modules: tuple[PromptModuleReference, ...]
    module_contributions: tuple[PromptModuleContribution, ...]
    selected_material_ids: tuple[str, ...]
    selected_craft_fragment_ids: tuple[str, ...]
    selected_example_ids: tuple[str, ...]
    module_content_bytes: int
    dynamic_material_bytes: int
    system_prompt_bytes: int
    user_packet_bytes: int
    total_prompt_bytes: int
    estimated_total_tokens: int
    technical_safety_policy_version: str
    creative_prompt_limit_enforced: bool
    fixed_example_count_enforced: bool
    content_truncated: bool
    provider_dispatch_allowed: bool
    external_provider_calls: int
    story_authority_writes: int
    automatic_detailer_calls: int
    automatic_retry_count: int
    fallback_enabled: bool
    adult_publication_activated: bool

    def __post_init__(self) -> None:
        require_schema(self.schema_version, self.SCHEMA_VERSION, type(self).__name__)
        _token(self.compiler_version, "compiler_version")
        for value in (
            self.registry_manifest_sha256,
            self.selection_request_sha256,
            self.sequence_plan_sha256,
            self.selected_cast_sha256,
        ):
            _sha256(value, "prompt receipt hash")
        _non_empty(self.target_provider_model, "receipt provider model")
        if tuple(value.reference for value in self.module_contributions) != self.selected_prompt_modules:
            raise ContractValidationError("module contributions do not bind selected modules")
        _unique(self.selected_material_ids, "selected material IDs")
        _unique(self.selected_craft_fragment_ids, "selected craft IDs")
        _unique(self.selected_example_ids, "selected example IDs")
        if not set(self.selected_example_ids).issubset(self.selected_craft_fragment_ids):
            raise ContractValidationError("example IDs must be selected craft items")
        counters = (
            self.module_content_bytes,
            self.dynamic_material_bytes,
            self.system_prompt_bytes,
            self.user_packet_bytes,
            self.total_prompt_bytes,
            self.estimated_total_tokens,
            self.external_provider_calls,
            self.story_authority_writes,
            self.automatic_detailer_calls,
            self.automatic_retry_count,
        )
        if min(counters) < 0 or self.total_prompt_bytes != self.system_prompt_bytes + self.user_packet_bytes:
            raise ContractValidationError("prompt receipt counters are inconsistent")
        if (
            self.creative_prompt_limit_enforced
            or self.fixed_example_count_enforced
            or self.content_truncated
            or self.provider_dispatch_allowed
            or self.external_provider_calls
            or self.story_authority_writes
            or self.automatic_detailer_calls
            or self.automatic_retry_count
            or self.fallback_enabled
            or self.adult_publication_activated
        ):
            raise ContractValidationError("scaffolding receipt cannot claim active behavior")

    @property
    def receipt_sha256(self) -> str:
        return domain_sha256(self.SCHEMA_VERSION, self)


@dataclass(frozen=True, slots=True)
class CompiledModularPrompt:
    system_prompt: str
    user_packet_json: str
    receipt: PromptCompilationReceipt

    def __post_init__(self) -> None:
        _non_empty(self.system_prompt, "compiled system prompt")
        _non_empty(self.user_packet_json, "compiled user packet")

