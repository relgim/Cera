"""Python-owned semantic-selector to allowlisted prompt-module mapping."""

from __future__ import annotations

from cera.errors import ContractValidationError

from .models import (
    PromptModuleLayer,
    PromptModuleReference,
    PromptSelectionRequest,
    PromptSelectionResult,
)
from .registry import LoadedPromptModule, PromptModuleRegistry


_REQUIRED_LAYERS = (
    PromptModuleLayer.CORE_AUTHORITY,
    PromptModuleLayer.STRUCTURED_OUTPUT,
    PromptModuleLayer.DEPTH,
    PromptModuleLayer.ADULT_RENDERING,
    PromptModuleLayer.INTERACTION_TOPOLOGY,
    PromptModuleLayer.SCENE_FUNCTION,
)


class PromptModuleSelector:
    def select(
        self,
        registry: PromptModuleRegistry,
        request: PromptSelectionRequest,
    ) -> PromptSelectionResult:
        candidates = tuple(
            value for value in registry.modules if _matches(value, request)
        )
        selected: list[LoadedPromptModule] = []
        for layer in _REQUIRED_LAYERS:
            matches = tuple(value for value in candidates if value.entry.layer is layer)
            if len(matches) != 1:
                raise ContractValidationError(
                    f"prompt selector requires exactly one {layer.value} module"
                )
            selected.append(matches[0])
        selected.sort(key=lambda value: value.entry.deterministic_order)
        selected_ids = {value.entry.module_id for value in selected}
        for module in selected:
            if not set(module.entry.dependencies).issubset(selected_ids):
                raise ContractValidationError("selected prompt module dependency is absent")
            if set(module.entry.conflicts).intersection(selected_ids):
                raise ContractValidationError("selected prompt modules conflict")
        references = tuple(
            PromptModuleReference(
                module_id=value.entry.module_id,
                version=value.entry.version,
                layer=value.entry.layer,
                sha256=value.entry.sha256,
                deterministic_order=value.entry.deterministic_order,
                relative_path=value.entry.relative_path,
                source_provenance=value.entry.source_provenance,
                selection_reason=_selection_reason(value.entry.layer, request),
                noncanonical=value.entry.noncanonical,
                noncopyable=value.entry.noncopyable,
            )
            for value in selected
        )
        return PromptSelectionResult(request.selection_sha256, references)


def _matches(module: LoadedPromptModule, request: PromptSelectionRequest) -> bool:
    entry = module.entry
    return (
        (not entry.allowed_scene_depth_modes or request.scene_depth_mode in entry.allowed_scene_depth_modes)
        and (
            not entry.allowed_adult_rendering_modes
            or request.adult_rendering_mode in entry.allowed_adult_rendering_modes
        )
        and (
            not entry.allowed_interaction_topologies
            or request.interaction_topology in entry.allowed_interaction_topologies
        )
        and (
            not entry.allowed_scene_functions
            or request.scene_function in entry.allowed_scene_functions
        )
    )


def _selection_reason(layer: PromptModuleLayer, request: PromptSelectionRequest) -> str:
    return {
        PromptModuleLayer.CORE_AUTHORITY: "stable_core_required",
        PromptModuleLayer.STRUCTURED_OUTPUT: "structured_output_teaching_required",
        PromptModuleLayer.DEPTH: f"scene_depth_mode:{request.scene_depth_mode.value}",
        PromptModuleLayer.ADULT_RENDERING: (
            f"adult_rendering_mode:{request.adult_rendering_mode.value}"
        ),
        PromptModuleLayer.INTERACTION_TOPOLOGY: (
            f"interaction_topology:{request.interaction_topology.value}"
        ),
        PromptModuleLayer.SCENE_FUNCTION: f"scene_function:{request.scene_function.value}",
    }[layer]

