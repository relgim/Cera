from __future__ import annotations

import json
from pathlib import Path
import shutil
import tempfile
import unittest

from cera.contracts import (
    AdultRenderingMode,
    InteractionTopology,
    InteriorityLevel,
    PromptTone,
    SceneDepthMode,
    SceneFunction,
)
from cera.errors import ContractValidationError
from cera.ids import IdKind, TypedId
from cera.prompting.models import PromptSelectionRequest
from cera.prompting.runtime import (
    DEFAULT_RUNTIME_PROMPT_REGISTRY,
    RuntimePromptRegistry,
    compile_runtime_system_prompt,
)


class RuntimeModularComposerPromptTests(unittest.TestCase):
    def selection(
        self,
        *,
        depth: SceneDepthMode = SceneDepthMode.AUTO,
        adult: AdultRenderingMode = AdultRenderingMode.OFF,
        topology: InteractionTopology = InteractionTopology.SINGLE_NPC_FLOOR,
        scene: SceneFunction = SceneFunction.ORDINARY_SOCIAL,
        characters: tuple[TypedId, ...] = (TypedId(IdKind.CHARACTER, "sakura"),),
    ) -> PromptSelectionRequest:
        return PromptSelectionRequest(
            schema_version=PromptSelectionRequest.SCHEMA_VERSION,
            scene_depth_mode=depth,
            adult_rendering_mode=adult,
            interaction_topology=topology,
            scene_function=scene,
            tone=PromptTone.NEUTRAL,
            interiority_level=InteriorityLevel.MEDIUM,
            selected_character_ids=characters,
            adult_route_eligible=adult is not AdultRenderingMode.OFF,
            route_blocked=False,
            target_provider_model="deepseek-v4-flash",
            sequence_plan_sha256="a" * 64,
        )

    def test_registry_is_hash_bound_and_has_no_external_dependency(self) -> None:
        registry = RuntimePromptRegistry.load()
        self.assertEqual(len(registry.modules), 20)
        self.assertTrue(registry.manifest_sha256)
        self.assertEqual(
            registry.provenance_sha256,
            "fca74e32462c4687d9810e3bf89d60cda0fbe8cf5b35c569c20dc1de3602f8a4",
        )

    def test_all_depth_and_adult_modes_select_exactly_seven_modules(self) -> None:
        for depth in SceneDepthMode:
            for adult in AdultRenderingMode:
                with self.subTest(depth=depth, adult=adult):
                    result = compile_runtime_system_prompt(
                        self.selection(depth=depth, adult=adult)
                    )
                    self.assertEqual(len(result.receipt.selected_module_ids), 7)
                    self.assertTrue(result.receipt.provider_dispatch_allowed)
                    self.assertFalse(result.receipt.creative_prompt_limit_enforced)
                    self.assertFalse(result.receipt.content_truncated)
                    self.assertIn("[ACTIVE_RUNTIME_MODULE]", result.system_prompt)

    def test_selector_is_python_owned_and_changes_only_requested_layers(self) -> None:
        ordinary = compile_runtime_system_prompt(self.selection())
        multi = compile_runtime_system_prompt(
            self.selection(
                topology=InteractionTopology.MULTI_NPC_SHARED_FLOOR,
                characters=(
                    TypedId(IdKind.CHARACTER, "sakura"),
                    TypedId(IdKind.CHARACTER, "tomi"),
                ),
            )
        )
        ordinary_ids = set(ordinary.receipt.selected_module_ids)
        multi_ids = set(multi.receipt.selected_module_ids)
        self.assertEqual(len(ordinary_ids.symmetric_difference(multi_ids)), 2)

    def test_runtime_modules_have_no_numeric_beat_quota(self) -> None:
        registry = RuntimePromptRegistry.load()
        text = "\n".join(value.content.lower() for value in registry.modules)
        self.assertNotIn("three to five", text)
        self.assertNotIn("five to eight", text)
        self.assertNotIn("eight to twelve", text)
        self.assertIn("no fixed beat", text)

    def test_active_authority_requires_zero_inference_for_protected_user_staging(self) -> None:
        compiled = compile_runtime_system_prompt(self.selection())
        self.assertIn("composer.authority.v6", compiled.receipt.selected_module_ids)
        authority = compiled.system_prompt.casefold()
        for required in (
            "closed-world",
            "hand or limb",
            "facial expression",
            "eye direction",
            "micro-movement",
            "perception does not create new",
            "world and continuity facts are also closed-world",
            "left/right",
            "never choose a plausible value",
        ):
            self.assertIn(required, authority)

    def test_short_and_medium_are_distinct_creator_depth_profiles(self) -> None:
        short = compile_runtime_system_prompt(
            self.selection(depth=SceneDepthMode.SHORT)
        )
        medium = compile_runtime_system_prompt(
            self.selection(depth=SceneDepthMode.MEDIUM)
        )
        self.assertIn("composer.depth.short.v2", short.receipt.selected_module_ids)
        self.assertIn("composer.depth.medium.v2", medium.receipt.selected_module_ids)
        self.assertNotEqual(short.system_prompt, medium.system_prompt)

    def test_module_or_manifest_mutation_fails_closed(self) -> None:
        source_root = DEFAULT_RUNTIME_PROMPT_REGISTRY.parent
        with tempfile.TemporaryDirectory() as directory:
            copied = Path(directory) / "runtime_v2"
            shutil.copytree(source_root, copied)
            module = copied / "modules" / "depth" / "auto_v2.md"
            module.write_text(module.read_text(encoding="utf-8") + "\nmutation", encoding="utf-8")
            with self.assertRaises(ContractValidationError):
                RuntimePromptRegistry.load(copied / "MANIFEST.json")

        with tempfile.TemporaryDirectory() as directory:
            copied = Path(directory) / "runtime_v2"
            shutil.copytree(source_root, copied)
            manifest = copied / "MANIFEST.json"
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            payload["modules"][0]["relative_path"] = "../escape.md"
            manifest.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(ContractValidationError):
                RuntimePromptRegistry.load(manifest)


if __name__ == "__main__":
    unittest.main()
