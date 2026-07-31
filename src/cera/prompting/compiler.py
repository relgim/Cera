"""Inactive deterministic modular Composer prompt compiler."""

from __future__ import annotations

from dataclasses import dataclass
import json
from math import ceil

from cera.contracts import AdultRenderingMode
from cera.errors import ContractValidationError
from cera.serialization import canonical_json, domain_sha256

from .models import (
    CompiledModularPrompt,
    PromptCompilationReceipt,
    PromptCompilerState,
    PromptCraftItem,
    PromptMaterialBlock,
    PromptModuleContribution,
    PromptSelectionRequest,
)
from .registry import (
    PROMPT_REGISTRY_TECHNICAL_SAFETY_POLICY_VERSION,
    PromptModuleRegistry,
)
from .selector import PromptModuleSelector


MODULAR_COMPOSER_COMPILER_VERSION = "cera.modular_composer_compiler.v1"


@dataclass(frozen=True, slots=True)
class ModularComposerCompileRequest:
    selection: PromptSelectionRequest
    protected_source_json: str
    sequence_plan_json: str
    participant_boundaries_json: str
    character_expression_blocks: tuple[PromptMaterialBlock, ...]
    continuity_evidence_blocks: tuple[PromptMaterialBlock, ...]
    craft_items: tuple[PromptCraftItem, ...]
    output_obligations_json: str
    output_schema_json: str
    conditional_future_events_present: bool

    def __post_init__(self) -> None:
        for name in (
            "protected_source_json",
            "sequence_plan_json",
            "participant_boundaries_json",
            "output_obligations_json",
            "output_schema_json",
        ):
            _require_json_object(getattr(self, name), name)
        if self.conditional_future_events_present:
            raise ContractValidationError(
                "conditional future events cannot enter creative Composer material"
            )


class ModularComposerCompiler:
    """Compiles fixtures or shadow artifacts; provider dispatch is impossible in v1."""

    def __init__(self, *, state: PromptCompilerState) -> None:
        self.state = state
        self.selector = PromptModuleSelector()

    def compile(
        self,
        registry: PromptModuleRegistry,
        request: ModularComposerCompileRequest,
    ) -> CompiledModularPrompt:
        selection = self.selector.select(registry, request.selection)
        selected_ids = set(request.selection.selected_character_ids)
        materials = (
            *request.character_expression_blocks,
            *request.continuity_evidence_blocks,
        )
        for block in materials:
            if block.private_to_owner and block.owner_character_id not in selected_ids:
                raise ContractValidationError("unselected owner-private prompt material")
            if not set(block.applicable_character_ids).issubset(selected_ids):
                raise ContractValidationError("prompt material names an unselected character")
        craft_items = tuple(sorted(request.craft_items, key=lambda value: value.deterministic_order))
        if len({value.deterministic_order for value in craft_items}) != len(craft_items):
            raise ContractValidationError("craft item order must be unique")
        mode = request.selection.adult_rendering_mode
        if mode is AdultRenderingMode.OFF and craft_items:
            raise ContractValidationError("Adult OFF cannot include craft fragments or examples")
        if mode is not AdultRenderingMode.OFF and not craft_items:
            raise ContractValidationError("Adult ON/EX fixture requires selected craft support")
        for item in craft_items:
            if mode not in item.allowed_adult_rendering_modes:
                raise ContractValidationError("craft item mode differs from creator transport mode")

        module_sections = []
        contributions = []
        for reference in selection.selected_prompt_modules:
            loaded = registry.get(reference.module_id)
            module_sections.append(
                f"## PROMPT MODULE {reference.module_id}@{reference.version}\n{loaded.content.strip()}"
            )
            contributions.append(
                PromptModuleContribution(
                    reference=reference,
                    content_bytes=loaded.content_bytes,
                    estimated_tokens=_estimate_tokens(loaded.content),
                )
            )
        craft_sections = [
            (
                f"## CRAFT ITEM {item.fragment_id}@{item.version}\n"
                "[NONCANONICAL][NONCOPYABLE]\n"
                f"{item.text.strip()}"
            )
            for item in craft_items
        ]
        system_prompt = "\n\n".join((*module_sections, *craft_sections))
        user_payload = {
            "schema_version": "cera.modular_composer_fixture_packet.v1",
            "compiler_state": self.state.value,
            "target_provider_model": request.selection.target_provider_model,
            "selectors": {
                "scene_depth_mode": request.selection.scene_depth_mode.value,
                "adult_rendering_mode": request.selection.adult_rendering_mode.value,
                "interaction_topology": request.selection.interaction_topology.value,
                "scene_function": request.selection.scene_function.value,
                "tone": request.selection.tone.value,
                "interiority_level": request.selection.interiority_level.value,
            },
            "selected_prompt_modules": [
                {
                    "module_id": value.module_id,
                    "version": value.version,
                    "sha256": value.sha256,
                    "selection_reason": value.selection_reason,
                    "deterministic_order": value.deterministic_order,
                }
                for value in selection.selected_prompt_modules
            ],
            "protected_source": json.loads(request.protected_source_json),
            "sequence_plan": json.loads(request.sequence_plan_json),
            "participant_boundaries": json.loads(request.participant_boundaries_json),
            "character_expression": [_material_payload(value) for value in request.character_expression_blocks],
            "continuity_and_evidence": [_material_payload(value) for value in request.continuity_evidence_blocks],
            "craft_items": [
                {
                    "fragment_id": value.fragment_id,
                    "version": value.version,
                    "fragment_sha256": value.fragment_sha256,
                    "selection_reason": value.selection_reason,
                    "source_provenance": value.source_provenance,
                    "noncanonical": value.noncanonical,
                    "noncopyable": value.noncopyable,
                    "is_example": value.is_example,
                }
                for value in craft_items
            ],
            "output_obligations": json.loads(request.output_obligations_json),
            "output_schema": json.loads(request.output_schema_json),
            "conditional_future_events_included": False,
        }
        user_packet_json = canonical_json(user_payload)
        module_bytes = sum(value.content_bytes for value in contributions)
        dynamic_bytes = sum(len(value.text.encode("utf-8")) for value in materials) + sum(
            len(value.text.encode("utf-8")) for value in craft_items
        )
        system_bytes = len(system_prompt.encode("utf-8"))
        user_bytes = len(user_packet_json.encode("utf-8"))
        receipt = PromptCompilationReceipt(
            schema_version=PromptCompilationReceipt.SCHEMA_VERSION,
            compiler_version=MODULAR_COMPOSER_COMPILER_VERSION,
            compiler_state=self.state,
            registry_manifest_sha256=registry.manifest.manifest_sha256,
            selection_request_sha256=request.selection.selection_sha256,
            sequence_plan_sha256=request.selection.sequence_plan_sha256,
            selected_cast_sha256=domain_sha256(
                "cera.prompt_selected_cast.v1",
                request.selection.selected_character_ids,
            ),
            target_provider_model=request.selection.target_provider_model,
            scene_depth_mode=request.selection.scene_depth_mode,
            adult_rendering_mode=request.selection.adult_rendering_mode,
            selected_prompt_modules=selection.selected_prompt_modules,
            module_contributions=tuple(contributions),
            selected_material_ids=tuple(value.material_id for value in materials),
            selected_craft_fragment_ids=tuple(value.fragment_id for value in craft_items),
            selected_example_ids=tuple(value.fragment_id for value in craft_items if value.is_example),
            module_content_bytes=module_bytes,
            dynamic_material_bytes=dynamic_bytes,
            system_prompt_bytes=system_bytes,
            user_packet_bytes=user_bytes,
            total_prompt_bytes=system_bytes + user_bytes,
            estimated_total_tokens=_estimate_tokens(system_prompt + "\n" + user_packet_json),
            technical_safety_policy_version=PROMPT_REGISTRY_TECHNICAL_SAFETY_POLICY_VERSION,
            creative_prompt_limit_enforced=False,
            fixed_example_count_enforced=False,
            content_truncated=False,
            provider_dispatch_allowed=False,
            external_provider_calls=0,
            story_authority_writes=0,
            automatic_detailer_calls=0,
            automatic_retry_count=0,
            fallback_enabled=False,
            adult_publication_activated=False,
        )
        return CompiledModularPrompt(system_prompt, user_packet_json, receipt)


def _material_payload(value: PromptMaterialBlock) -> dict[str, object]:
    return {
        "material_id": value.material_id,
        "kind": value.kind.value,
        "owner_character_id": str(value.owner_character_id) if value.owner_character_id else None,
        "applicable_character_ids": [str(item) for item in value.applicable_character_ids],
        "private_to_owner": value.private_to_owner,
        "text": value.text,
        "text_sha256": value.text_sha256,
        "source_provenance": value.source_provenance,
        "selection_reason": value.selection_reason,
    }


def _require_json_object(value: str, name: str) -> None:
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ContractValidationError(f"{name} must be valid JSON") from exc
    if not isinstance(parsed, dict):
        raise ContractValidationError(f"{name} must contain a JSON object")


def _estimate_tokens(value: str) -> int:
    """Diagnostic estimate only; never a selection or truncation rule."""

    return max(1, ceil(len(value.encode("utf-8")) / 4))

