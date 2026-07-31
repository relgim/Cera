"""Active, hash-bound modular prompt compiler for the DeepSeek Composer.

This is deliberately separate from the v1 fixture/shadow compiler.  It selects
instructions from Python-owned semantic selectors and never lets a model name a
module, truncate material, change Adult mode, or change the scene plan.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import json
from pathlib import Path
from typing import ClassVar

from cera.errors import ContractValidationError
from cera.serialization import canonical_json, domain_sha256, text_sha256

from .models import PromptSelectionRequest


RUNTIME_PROMPT_REGISTRY_SCHEMA = "cera.runtime_prompt_registry.v3"
RUNTIME_PROMPT_COMPILER_VERSION = "cera.runtime_modular_composer_compiler.v3"
DEFAULT_RUNTIME_PROMPT_REGISTRY = (
    Path(__file__).resolve().parents[3]
    / "config"
    / "cera"
    / "prompts"
    / "composer"
    / "runtime_v2"
    / "MANIFEST.json"
)
_ACTIVE_MARKER = "[ACTIVE_RUNTIME_MODULE]"


class RuntimePromptLayer(StrEnum):
    AUTHORITY = "authority"
    CAUSAL_REALIZATION = "causal_realization"
    STRUCTURED_OUTPUT = "structured_output"
    DEPTH = "depth"
    ADULT = "adult"
    TOPOLOGY = "topology"
    SCENE = "scene"


_ALWAYS_LAYERS = {
    RuntimePromptLayer.AUTHORITY,
    RuntimePromptLayer.CAUSAL_REALIZATION,
    RuntimePromptLayer.STRUCTURED_OUTPUT,
}
_REQUIRED_LAYERS = tuple(RuntimePromptLayer)


@dataclass(frozen=True, slots=True)
class RuntimePromptModule:
    module_id: str
    layer: RuntimePromptLayer
    relative_path: str
    sha256: str
    deterministic_order: int
    selector_value: str | None
    source_provenance: str
    content: str

    def __post_init__(self) -> None:
        if not self.module_id or not self.source_provenance:
            raise ContractValidationError("runtime prompt module metadata is incomplete")
        if len(self.sha256) != 64 or text_sha256(self.content) != self.sha256:
            raise ContractValidationError("runtime prompt module hash mismatch")
        if self.deterministic_order < 0:
            raise ContractValidationError("runtime prompt order is invalid")
        if _ACTIVE_MARKER not in self.content:
            raise ContractValidationError("runtime prompt module lacks active marker")
        if self.layer in _ALWAYS_LAYERS and self.selector_value is not None:
            raise ContractValidationError("stable runtime module cannot have selector")
        if self.layer not in _ALWAYS_LAYERS and not self.selector_value:
            raise ContractValidationError("conditional runtime module needs selector")


@dataclass(frozen=True, slots=True)
class RuntimePromptRegistry:
    registry_version: str
    provenance_sha256: str
    modules: tuple[RuntimePromptModule, ...]
    manifest_sha256: str

    @classmethod
    def load(cls, path: str | Path = DEFAULT_RUNTIME_PROMPT_REGISTRY) -> "RuntimePromptRegistry":
        manifest_path = Path(path).resolve()
        try:
            raw = manifest_path.read_bytes()
            payload = json.loads(raw.decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ContractValidationError("runtime prompt manifest is unreadable") from exc
        if not isinstance(payload, dict) or payload.get("schema_version") != RUNTIME_PROMPT_REGISTRY_SCHEMA:
            raise ContractValidationError("runtime prompt manifest schema is invalid")
        if payload.get("runtime_external_path_dependencies") != []:
            raise ContractValidationError("runtime prompt registry cannot depend on external paths")
        if payload.get("creative_prompt_limit_enforced") is not False:
            raise ContractValidationError("runtime prompt registry cannot silently limit creative material")
        if payload.get("fixed_example_count_enforced") is not False:
            raise ContractValidationError("runtime prompt registry cannot impose example quotas")
        root = manifest_path.parent
        modules: list[RuntimePromptModule] = []
        for item in payload.get("modules", []):
            if not isinstance(item, dict) or set(item) != {
                "module_id", "layer", "relative_path", "sha256",
                "deterministic_order", "selector_value", "source_provenance",
            }:
                raise ContractValidationError("runtime prompt manifest module shape is invalid")
            relative = Path(item["relative_path"])
            if relative.is_absolute() or ".." in relative.parts or relative.suffix != ".md":
                raise ContractValidationError("runtime prompt module path is invalid")
            module_path = (root / relative).resolve()
            if root != module_path and root not in module_path.parents:
                raise ContractValidationError("runtime prompt module escaped registry root")
            try:
                content = module_path.read_text(encoding="utf-8")
                layer = RuntimePromptLayer(item["layer"])
            except (OSError, UnicodeDecodeError, ValueError) as exc:
                raise ContractValidationError("runtime prompt module is invalid") from exc
            modules.append(
                RuntimePromptModule(
                    module_id=item["module_id"],
                    layer=layer,
                    relative_path=item["relative_path"],
                    sha256=item["sha256"],
                    deterministic_order=item["deterministic_order"],
                    selector_value=item["selector_value"],
                    source_provenance=item["source_provenance"],
                    content=content,
                )
            )
        if not modules:
            raise ContractValidationError("runtime prompt registry is empty")
        ids = tuple(value.module_id for value in modules)
        paths = tuple(value.relative_path for value in modules)
        orders = tuple(value.deterministic_order for value in modules)
        if len(ids) != len(set(ids)) or len(paths) != len(set(paths)) or len(orders) != len(set(orders)):
            raise ContractValidationError("runtime prompt module identity/order is not unique")
        return cls(
            registry_version=payload["registry_version"],
            provenance_sha256=payload["provenance_sha256"],
            modules=tuple(sorted(modules, key=lambda value: value.deterministic_order)),
            manifest_sha256=text_sha256(raw.decode("utf-8")),
        )

    def select(self, request: PromptSelectionRequest) -> tuple[RuntimePromptModule, ...]:
        selector = {
            RuntimePromptLayer.DEPTH: request.scene_depth_mode.value,
            RuntimePromptLayer.ADULT: request.adult_rendering_mode.value,
            RuntimePromptLayer.TOPOLOGY: request.interaction_topology.value,
            RuntimePromptLayer.SCENE: request.scene_function.value,
        }
        selected: list[RuntimePromptModule] = []
        for layer in _REQUIRED_LAYERS:
            matches = tuple(
                value
                for value in self.modules
                if value.layer is layer
                and (layer in _ALWAYS_LAYERS or value.selector_value == selector[layer])
            )
            if len(matches) != 1:
                raise ContractValidationError(
                    f"runtime prompt selection requires one {layer.value} module"
                )
            selected.append(matches[0])
        return tuple(sorted(selected, key=lambda value: value.deterministic_order))


@dataclass(frozen=True, slots=True)
class RuntimePromptCompilationReceipt:
    SCHEMA_VERSION: ClassVar[str] = "cera.runtime_prompt_compilation_receipt.v3"

    schema_version: str
    compiler_version: str
    registry_version: str
    registry_manifest_sha256: str
    selection_request_sha256: str
    selected_module_ids: tuple[str, ...]
    selected_module_sha256s: tuple[str, ...]
    system_prompt_sha256: str
    system_prompt_bytes: int
    provider_dispatch_allowed: bool
    creative_prompt_limit_enforced: bool
    content_truncated: bool

    @property
    def receipt_sha256(self) -> str:
        return domain_sha256(self.SCHEMA_VERSION, self)


@dataclass(frozen=True, slots=True)
class CompiledRuntimePrompt:
    system_prompt: str
    receipt: RuntimePromptCompilationReceipt


def compile_runtime_system_prompt(
    request: PromptSelectionRequest,
    *,
    registry_path: str | Path = DEFAULT_RUNTIME_PROMPT_REGISTRY,
) -> CompiledRuntimePrompt:
    registry = RuntimePromptRegistry.load(registry_path)
    selected = registry.select(request)
    system_prompt = "\n\n".join(value.content.strip() for value in selected)
    receipt = RuntimePromptCompilationReceipt(
        schema_version=RuntimePromptCompilationReceipt.SCHEMA_VERSION,
        compiler_version=RUNTIME_PROMPT_COMPILER_VERSION,
        registry_version=registry.registry_version,
        registry_manifest_sha256=registry.manifest_sha256,
        selection_request_sha256=request.selection_sha256,
        selected_module_ids=tuple(value.module_id for value in selected),
        selected_module_sha256s=tuple(value.sha256 for value in selected),
        system_prompt_sha256=text_sha256(system_prompt),
        system_prompt_bytes=len(system_prompt.encode("utf-8")),
        provider_dispatch_allowed=True,
        creative_prompt_limit_enforced=False,
        content_truncated=False,
    )
    return CompiledRuntimePrompt(system_prompt=system_prompt, receipt=receipt)


def runtime_prompt_receipt_payload(receipt: RuntimePromptCompilationReceipt) -> dict[str, object]:
    """Privacy-safe, canonical packet projection of the active prompt receipt."""

    return json.loads(canonical_json(receipt))
