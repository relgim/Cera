"""Repository-local, hash-verifying prompt module registry loader."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

from cera.errors import ContractValidationError
from cera.schema import from_mapping
from cera.serialization import text_sha256

from .models import (
    PromptCompilerState,
    PromptModuleEnabledState,
    PromptModuleManifestEntry,
    PromptModuleRegistryManifest,
)


PROMPT_REGISTRY_TECHNICAL_SAFETY_POLICY_VERSION = "cera.prompt_registry_technical_safety.v1"
MAX_REGISTRY_MANIFEST_BYTES = 524_288
MAX_REGISTRY_MODULES = 128
MAX_SINGLE_MODULE_BYTES = 1_048_576
MAX_TOTAL_MODULE_BYTES = 8_388_608


@dataclass(frozen=True, slots=True)
class LoadedPromptModule:
    entry: PromptModuleManifestEntry
    content: str
    content_bytes: int


class PromptModuleRegistry:
    def __init__(
        self,
        *,
        root: Path,
        manifest: PromptModuleRegistryManifest,
        modules: tuple[LoadedPromptModule, ...],
    ) -> None:
        self.root = root.resolve()
        self.manifest = manifest
        self.modules = modules
        self._by_id = {value.entry.module_id: value for value in modules}
        if len(self._by_id) != len(modules):
            raise ContractValidationError("loaded prompt modules contain duplicate IDs")

    def get(self, module_id: str) -> LoadedPromptModule:
        try:
            return self._by_id[module_id]
        except KeyError as exc:
            raise ContractValidationError("unknown prompt module ID") from exc


def load_prompt_module_registry(
    manifest_path: str | Path,
    *,
    compiler_state: PromptCompilerState,
) -> PromptModuleRegistry:
    path = Path(manifest_path).resolve()
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ContractValidationError("unable to read prompt registry manifest") from exc
    if len(raw) > MAX_REGISTRY_MANIFEST_BYTES:
        raise ContractValidationError("prompt registry manifest exceeds technical safety limit")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractValidationError("prompt registry manifest is invalid JSON") from exc
    if not isinstance(payload, dict):
        raise ContractValidationError("prompt registry manifest must be an object")
    manifest = from_mapping(PromptModuleRegistryManifest, payload)
    if len(manifest.modules) > MAX_REGISTRY_MODULES:
        raise ContractValidationError("prompt registry exceeds technical module-count limit")
    required_state = {
        PromptCompilerState.FIXTURE_ONLY: PromptModuleEnabledState.FIXTURE_ONLY,
        PromptCompilerState.SHADOW_ONLY: PromptModuleEnabledState.SHADOW_ONLY,
    }[compiler_state]
    root = path.parent.resolve()
    loaded: list[LoadedPromptModule] = []
    total_bytes = 0
    for entry in sorted(manifest.modules, key=lambda value: value.deterministic_order):
        if entry.enabled_state is PromptModuleEnabledState.DISABLED:
            continue
        if entry.enabled_state is not required_state:
            raise ContractValidationError("prompt module state differs from compiler state")
        module_path = (root / entry.relative_path).resolve()
        if root != module_path and root not in module_path.parents:
            raise ContractValidationError("prompt module escaped registry root")
        try:
            module_bytes = module_path.read_bytes()
        except OSError as exc:
            raise ContractValidationError("prompt module file is missing") from exc
        if len(module_bytes) > MAX_SINGLE_MODULE_BYTES:
            raise ContractValidationError("prompt module exceeds technical safety limit")
        total_bytes += len(module_bytes)
        if total_bytes > MAX_TOTAL_MODULE_BYTES:
            raise ContractValidationError("prompt registry exceeds technical aggregate limit")
        try:
            content = module_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ContractValidationError("prompt module must be UTF-8") from exc
        if text_sha256(content) != entry.sha256:
            raise ContractValidationError("prompt module hash mismatch")
        marker = f"[{required_state.value.upper()}]"
        if marker not in content:
            raise ContractValidationError("inactive prompt module lacks its state marker")
        loaded.append(LoadedPromptModule(entry, content, len(module_bytes)))
    if not loaded:
        raise ContractValidationError("prompt registry has no modules for compiler state")
    return PromptModuleRegistry(root=root, manifest=manifest, modules=tuple(loaded))

