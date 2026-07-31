from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import shutil
from types import SimpleNamespace
import tempfile
import unittest

from cera.adult_craft.catalog import AdultCraftCatalog
from cera.adult_craft.models import AdultCraftMode
from cera.adult_craft.selector import AdultCraftSelectionResult
from cera.composer.deepseek import (
    DEEPSEEK_COMPOSER_ADAPTER_VERSION,
    DEEPSEEK_COMPOSER_PACKET_VERSION,
    DEEPSEEK_COMPOSER_PROMPT_VERSION,
    build_deepseek_composer_messages,
)
from cera.contracts import AdultRenderingMode, SceneDepthMode
from cera.errors import ContractValidationError
from cera.ids import TypedId
from cera.prompting import (
    InteractionTopology,
    InteriorityLevel,
    ModularComposerCompileRequest,
    ModularComposerCompiler,
    PromptCompilerState,
    PromptCraftItem,
    PromptMaterialBlock,
    PromptMaterialKind,
    PromptSelectionRequest,
    PromptTone,
    SceneFunction,
    load_prompt_module_registry,
    prompt_craft_items_from_selection,
)
from cera.providers import DEFAULT_DEEPSEEK_COMPOSER_MODEL, deepseek_composer_candidate
from cera.registry import build_schema_registry
from cera.serialization import canonical_json, text_sha256, to_primitive


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = ROOT / "tests" / "fixtures" / "modular_composer"
FIXTURE_MANIFEST = FIXTURE_ROOT / "MANIFEST.json"
CATALOG_ROOT = ROOT / "adult" / "catalog" / "adult_craft_v1"
ACTIVE_SYSTEM_SHA256 = "01e2c7d2ae1021e0a1001f2eaa619960ae057a7db2b2e44c4c01912b2e388681"


class ModularComposerPromptingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = load_prompt_module_registry(
            FIXTURE_MANIFEST,
            compiler_state=PromptCompilerState.FIXTURE_ONLY,
        )
        self.compiler = ModularComposerCompiler(state=PromptCompilerState.FIXTURE_ONLY)
        self.sakura = TypedId.parse("character:sakura_hanezawa")
        self.mia = TypedId.parse("character:mia_hanezawa")

    def selection(
        self,
        *,
        depth: SceneDepthMode = SceneDepthMode.AUTO,
        adult: AdultRenderingMode = AdultRenderingMode.OFF,
        topology: InteractionTopology = InteractionTopology.SINGLE_NPC_FLOOR,
        scene: SceneFunction = SceneFunction.ORDINARY_SOCIAL,
        selected: tuple[TypedId, ...] | None = None,
        reasoner_modules: tuple[str, ...] = (),
    ) -> PromptSelectionRequest:
        return PromptSelectionRequest(
            schema_version=PromptSelectionRequest.SCHEMA_VERSION,
            scene_depth_mode=depth,
            adult_rendering_mode=adult,
            interaction_topology=topology,
            scene_function=scene,
            tone=PromptTone.TENSE if scene is SceneFunction.CONFRONTATION_BOUNDARY else PromptTone.NEUTRAL,
            interiority_level=InteriorityLevel.MEDIUM,
            selected_character_ids=selected or (self.sakura,),
            adult_route_eligible=adult is not AdultRenderingMode.OFF,
            route_blocked=False,
            target_provider_model=DEFAULT_DEEPSEEK_COMPOSER_MODEL,
            sequence_plan_sha256="a" * 64,
            reasoner_requested_module_ids=reasoner_modules,
        )

    def material(
        self,
        material_id: str = "voice.sakura.baseline",
        *,
        owner: TypedId | None = None,
        applicable: tuple[TypedId, ...] | None = None,
        private: bool = True,
    ) -> PromptMaterialBlock:
        owner = self.sakura if owner is None else owner
        applicable = applicable or (owner,)
        text = "Synthetic noncanonical selected-character expression fixture."
        return PromptMaterialBlock(
            material_id=material_id,
            kind=PromptMaterialKind.CHARACTER_EXPRESSION,
            owner_character_id=owner,
            applicable_character_ids=applicable,
            private_to_owner=private,
            text=text,
            text_sha256=text_sha256(text),
            source_provenance="tests/fixtures/modular_composer",
            selection_reason="synthetic selected speaker",
        )

    def craft(
        self,
        mode: AdultRenderingMode,
        index: int,
        *,
        example: bool = False,
    ) -> PromptCraftItem:
        text = f"Synthetic noncanonical craft fixture {mode.value}-{index}."
        return PromptCraftItem(
            fragment_id=f"craft_reference:synthetic-{mode.value}-{index}",
            version="v1",
            fragment_sha256=text_sha256(text),
            allowed_adult_rendering_modes=(mode,),
            deterministic_order=index,
            text=text,
            source_provenance="tests/fixtures/modular_composer",
            selection_reason=f"synthetic distinct skill {index}",
            noncanonical=True,
            noncopyable=True,
            is_example=example,
        )

    def compile_request(
        self,
        selection: PromptSelectionRequest,
        *,
        craft: tuple[PromptCraftItem, ...] | None = None,
        character_blocks: tuple[PromptMaterialBlock, ...] | None = None,
        future: bool = False,
    ):
        if craft is None:
            if selection.adult_rendering_mode is AdultRenderingMode.OFF:
                craft = ()
            elif selection.adult_rendering_mode is AdultRenderingMode.ON:
                craft = (self.craft(AdultRenderingMode.ON, 0),)
            else:
                craft = (
                    self.craft(AdultRenderingMode.EX, 0),
                    self.craft(AdultRenderingMode.EX, 1, example=True),
                    self.craft(AdultRenderingMode.EX, 2, example=True),
                )
        if character_blocks is None:
            character_blocks = tuple(
                self.material(
                    f"voice.{str(character).split(':', 1)[1]}.baseline",
                    owner=character,
                    applicable=(character,),
                )
                for character in selection.selected_character_ids
            )
        request = ModularComposerCompileRequest(
            selection=selection,
            protected_source_json=canonical_json(
                {"synthetic": True, "source_units": [{"id": "source_unit:fixture"}]}
            ),
            sequence_plan_json=canonical_json(
                {
                    "sha256": selection.sequence_plan_sha256,
                    "current_beats": ["beat:fixture"],
                    "stop_before": "protected_user_next_choice",
                }
            ),
            participant_boundaries_json=canonical_json(
                {
                    "protected_user_id": "character:ted",
                    "selected_npc_ids": [str(value) for value in selection.selected_character_ids],
                }
            ),
            character_expression_blocks=character_blocks,
            continuity_evidence_blocks=(),
            craft_items=craft,
            output_obligations_json=canonical_json(
                {"required_authority_ids": ["beat:fixture"], "source_coverage": "empty"}
            ),
            output_schema_json=canonical_json(
                {"type": "object", "required": ["story_segments", "terminal_segment_key"]}
            ),
            conditional_future_events_present=future,
        )
        return self.compiler.compile(self.registry, request)

    def test_active_composer_route_and_prompt_are_unchanged_by_scaffold(self) -> None:
        messages = build_deepseek_composer_messages({})
        self.assertEqual(DEEPSEEK_COMPOSER_PROMPT_VERSION, "cera.deepseek_scene_composer_prompt.v18")
        self.assertEqual(DEEPSEEK_COMPOSER_ADAPTER_VERSION, "cera.deepseek_scene_composer.v19")
        self.assertEqual(DEEPSEEK_COMPOSER_PACKET_VERSION, "cera.deepseek_scene_composer_packet.v12")
        self.assertEqual(text_sha256(messages[0].content), ACTIVE_SYSTEM_SHA256)
        self.assertEqual(DEFAULT_DEEPSEEK_COMPOSER_MODEL, "deepseek-v4-flash")
        self.assertEqual(deepseek_composer_candidate().model_name, "deepseek-v4-flash")

    def test_compiler_is_fixture_only_and_cannot_dispatch_publish_retry_or_detail(self) -> None:
        result = self.compile_request(self.selection())
        receipt = result.receipt
        self.assertFalse(receipt.provider_dispatch_allowed)
        self.assertEqual(receipt.external_provider_calls, 0)
        self.assertEqual(receipt.story_authority_writes, 0)
        self.assertEqual(receipt.automatic_detailer_calls, 0)
        self.assertEqual(receipt.automatic_retry_count, 0)
        self.assertFalse(receipt.fallback_enabled)
        self.assertFalse(receipt.adult_publication_activated)

    def test_selection_is_deterministic_and_all_depth_adult_combinations_compile(self) -> None:
        observed = {}
        for depth in SceneDepthMode:
            for adult in AdultRenderingMode:
                selection = self.selection(depth=depth, adult=adult)
                first = self.compile_request(selection)
                second = self.compile_request(selection)
                self.assertEqual(first, second)
                ids = tuple(value.module_id for value in first.receipt.selected_prompt_modules)
                observed[(depth.value, adult.value)] = ids
                self.assertIn(f"composer.depth.{depth.value}.v1", ids)
                self.assertIn(f"composer.adult.{adult.value}.v1", ids)
        self.assertEqual(len(observed), 12)

    def test_depth_and_adult_are_independent(self) -> None:
        auto = self.compile_request(self.selection(depth=SceneDepthMode.AUTO, adult=AdultRenderingMode.ON))
        epic = self.compile_request(self.selection(depth=SceneDepthMode.EPIC, adult=AdultRenderingMode.ON))
        auto_ids = {value.module_id for value in auto.receipt.selected_prompt_modules}
        epic_ids = {value.module_id for value in epic.receipt.selected_prompt_modules}
        self.assertIn("composer.adult.on.v1", auto_ids & epic_ids)
        self.assertNotEqual(auto_ids, epic_ids)

    def test_adult_off_has_no_craft_examples_or_explicit_vocabulary_module(self) -> None:
        result = self.compile_request(self.selection(adult=AdultRenderingMode.OFF))
        self.assertEqual(result.receipt.selected_craft_fragment_ids, ())
        self.assertEqual(result.receipt.selected_example_ids, ())
        self.assertNotIn("CRAFT ITEM", result.system_prompt)
        self.assertNotIn("explicit-vocabulary", result.system_prompt)

    def test_adult_mode_is_exact_and_cannot_bypass_route(self) -> None:
        for mode in (AdultRenderingMode.ON, AdultRenderingMode.EX):
            result = self.compile_request(self.selection(adult=mode))
            self.assertIs(result.receipt.adult_rendering_mode, mode)
            for item in json.loads(result.user_packet_json)["craft_items"]:
                self.assertTrue(item["noncanonical"])
                self.assertTrue(item["noncopyable"])
        with self.assertRaises(ContractValidationError):
            replace(
                self.selection(adult=AdultRenderingMode.ON),
                adult_route_eligible=False,
            )

    def test_ex_cannot_change_sequence_plan_or_selected_cast(self) -> None:
        selection = self.selection(adult=AdultRenderingMode.EX)
        result = self.compile_request(selection)
        self.assertEqual(result.receipt.sequence_plan_sha256, selection.sequence_plan_sha256)
        expected_cast = result.receipt.selected_cast_sha256
        more_detail = self.compile_request(
            selection,
            craft=tuple(self.craft(AdultRenderingMode.EX, index, example=index > 1) for index in range(6)),
        )
        self.assertEqual(more_detail.receipt.sequence_plan_sha256, selection.sequence_plan_sha256)
        self.assertEqual(more_detail.receipt.selected_cast_sha256, expected_cast)

    def test_variable_fragments_and_examples_have_telemetry_without_creative_limits(self) -> None:
        result = self.compile_request(
            self.selection(adult=AdultRenderingMode.EX),
            craft=tuple(self.craft(AdultRenderingMode.EX, index, example=index % 2 == 0) for index in range(7)),
        )
        receipt = result.receipt
        self.assertEqual(len(receipt.selected_craft_fragment_ids), 7)
        self.assertEqual(len(receipt.selected_example_ids), 4)
        self.assertFalse(receipt.creative_prompt_limit_enforced)
        self.assertFalse(receipt.fixed_example_count_enforced)
        self.assertFalse(receipt.content_truncated)
        self.assertGreater(receipt.total_prompt_bytes, receipt.module_content_bytes)
        self.assertGreater(receipt.estimated_total_tokens, 0)

    def test_reasoner_module_names_and_paths_are_not_accepted(self) -> None:
        with self.assertRaises(ContractValidationError):
            self.selection(reasoner_modules=("composer.scene.invented",))

    def test_only_selected_owner_private_material_enters_packet(self) -> None:
        selected = self.compile_request(self.selection(), character_blocks=(self.material(),))
        packet = json.loads(selected.user_packet_json)
        self.assertEqual(packet["character_expression"][0]["owner_character_id"], str(self.sakura))
        with self.assertRaises(ContractValidationError):
            self.compile_request(
                self.selection(),
                character_blocks=(
                    self.material("voice.mia.private", owner=self.mia, applicable=(self.mia,)),
                ),
            )

    def test_future_events_are_rejected_and_output_teaching_is_retained(self) -> None:
        with self.assertRaises(ContractValidationError):
            self.compile_request(self.selection(), future=True)
        result = self.compile_request(self.selection())
        packet = json.loads(result.user_packet_json)
        self.assertFalse(packet["conditional_future_events_included"])
        self.assertIn("required_authority_ids", packet["output_obligations"])
        self.assertIn("story_segments", packet["output_schema"]["required"])
        self.assertIn("composer.core.structured_output.v1", result.system_prompt)

    def test_manifest_missing_hash_path_order_and_conflict_fail_closed(self) -> None:
        mutations = ("missing", "hash", "path", "order", "conflict")
        for mutation in mutations:
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary) / "registry"
                shutil.copytree(FIXTURE_ROOT, root)
                manifest_path = root / "MANIFEST.json"
                payload = json.loads(manifest_path.read_text(encoding="utf-8"))
                if mutation == "missing":
                    (root / payload["modules"][0]["relative_path"]).unlink()
                elif mutation == "hash":
                    payload["modules"][0]["sha256"] = "0" * 64
                elif mutation == "path":
                    payload["modules"][0]["relative_path"] = "../escape.md"
                elif mutation == "order":
                    payload["modules"][1]["deterministic_order"] = 50
                else:
                    payload["modules"][0]["conflicts"] = ["composer.core.structured_output.v1"]
                if mutation != "missing":
                    manifest_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
                if mutation == "conflict":
                    mutated = load_prompt_module_registry(
                        manifest_path,
                        compiler_state=PromptCompilerState.FIXTURE_ONLY,
                    )
                    with self.assertRaises(ContractValidationError):
                        self.compiler.compile(mutated, ModularComposerCompileRequest(
                            selection=self.selection(),
                            protected_source_json="{}",
                            sequence_plan_json="{}",
                            participant_boundaries_json="{}",
                            character_expression_blocks=(self.material(),),
                            continuity_evidence_blocks=(),
                            craft_items=(),
                            output_obligations_json="{}",
                            output_schema_json="{}",
                            conditional_future_events_present=False,
                        ))
                else:
                    with self.assertRaises(ContractValidationError):
                        load_prompt_module_registry(
                            manifest_path,
                            compiler_state=PromptCompilerState.FIXTURE_ONLY,
                        )

    def test_current_adult_catalog_selection_shape_maps_without_count_cap(self) -> None:
        catalog = AdultCraftCatalog.load(CATALOG_ROOT)
        fragments = tuple(
            value for value in catalog.fragments if AdultCraftMode.EX in value.modes
        )[:4]
        selection = AdultCraftSelectionResult(
            fragments=fragments,
            craft_blocks=(),
            specificity_contract=None,  # bridge does not own or alter this contract
            receipt=SimpleNamespace(
                selected_modes=(AdultCraftMode.EX,),
                selected_fragment_ids=tuple(value.fragment_id for value in fragments),
                selected_fragment_sha256=tuple(value.fragment_sha256 for value in fragments),
                covered_requirements=tuple(
                    sorted({f"family:{family.value}" for value in fragments for family in value.families})
                ),
            ),
        )
        items = prompt_craft_items_from_selection(selection)
        self.assertEqual(tuple(value.fragment_id for value in items), tuple(str(value.fragment_id) for value in fragments))
        self.assertEqual(len(items), 4)
        self.assertTrue(all(value.noncanonical and value.noncopyable for value in items))

    def test_new_prompt_records_are_schema_registered(self) -> None:
        registry = build_schema_registry()
        self.assertIn("cera.prompt_module_registry_manifest.v1", registry.versions)
        self.assertIn("cera.prompt_selection_request.v1", registry.versions)
        compiled = self.compile_request(self.selection())
        self.assertEqual(registry.decode(to_primitive(compiled.receipt)), compiled.receipt)


if __name__ == "__main__":
    unittest.main()
