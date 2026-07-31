from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

from cera.adult_craft.catalog import AdultCraftCatalog, verify_source_integrity
from cera.adult_craft.models import (
    AdultCraftAxis,
    AdultCraftConcept,
    AdultCraftFamily,
    AdultCraftMode,
    AdultCraftNeed,
    AdultCraftNeedV3,
    BeatCraftRequirementV3,
    BeatCraftNeed,
    ChannelCraftNeed,
    CharacterCardSectionNeed,
    SceneChannelCraftNeedV3,
    OutcomeAuthorityState,
    OutcomeScope,
    RealizationChannel,
    SegmentCommitment,
    SemanticActionBinding,
    SemanticBeatSpan,
    SemanticRequiredTransition,
    SemanticSpecificityFinding,
    SemanticSpecificityFindingCode,
    SemanticSpecificityRequest,
    SemanticVerificationStage,
    SpecificityRegister,
)
from cera.adult_craft.repair import (
    BeatScopedRepairCoordinator,
    BeatScopedRepairProposal,
    FakeBeatScopedRepairPort,
    ReplacementSpan,
)
from cera.adult_craft.selector import AdultCraftSelector
from cera.adult_craft.specificity import SpecificityValidator
from cera.adult_craft.semantic import (
    ScriptedFakeSemanticSpecificityPort,
    SemanticSpecificityCoordinator,
)
from cera.composer import (
    CharacterMoveRealization,
    CharacterRealization,
    ComposerCandidate,
    ComposerContextAssembler,
    ComposerContextKind,
    ComposerContextSource,
    ComposerCoordinator,
    ComposerSubmission,
    FakeComposerFixture,
    FakeSceneComposerPort,
    RealizationKind,
    RealizationManifest,
    RealizationSpan,
    SourceUnitCoverage,
)
from cera.evidence import EvidenceFetchRequest
from cera.errors import ContractValidationError, ErrorCode
from cera.ids import IdKind, deterministic_id
from cera.serialization import canonical_json, domain_sha256, text_sha256
from cera.schema import from_mapping
from cera.reasoner import (
    FakeReasonerFixture,
    FakeSceneReasonerPort,
    FakeToolCall,
    FakeToolOperation,
    ReasonerOutcome,
)
from cera.runtime import LiveShapedTurnPipeline
from cera.runtime.pipeline import LiveShapedTurnFailure
from cera.realization import EchoAcceptingSceneRealizationVerifierPort
from scripts.compile_adult_craft import compile_catalog
import tests.test_composer as composer_support
import tests.test_live_shaped_pipeline as live_support
import tests.test_real_genesis_integration as real_support


ROOT = Path(__file__).resolve().parents[1]
CATALOG_ROOT = ROOT / "adult" / "catalog" / "adult_craft_v1"
PROVENANCE_ROOT = ROOT / "adult" / "provenance" / "sera_adult_source_v1"


class AdultCraftCatalogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = AdultCraftCatalog.load(CATALOG_ROOT)

    def test_twenty_four_sources_are_immutable_and_catalog_has_no_reference_dependency(self) -> None:
        verified = verify_source_integrity(
            PROVENANCE_ROOT / "files",
            PROVENANCE_ROOT / "SOURCE_INTEGRITY.json",
        )
        self.assertEqual(len(verified), 24)
        self.assertEqual(self.catalog.manifest.source_artifact_count, 24)
        self.assertEqual(self.catalog.manifest.compiled_fragment_count, 82)
        self.assertEqual(self.catalog.manifest.provenance_only_source_count, 2)
        self.assertEqual(self.catalog.manifest.audit_only_source_count, 1)
        self.assertEqual(self.catalog.manifest.runtime_external_path_dependencies, ())
        self.assertNotIn("E:\\", canonical_json(self.catalog.manifest))
        self.assertNotIn("adult_character_context.md", "\n".join(
            value.source.source_relative_path for value in self.catalog.fragments
        ))
        self.assertNotIn("adult_scene_director.md", "\n".join(
            value.source.source_relative_path for value in self.catalog.fragments
        ))

    def test_anal_and_toilet_are_separate_families(self) -> None:
        anal = {
            value.fragment_id
            for value in self.catalog.fragments
            if AdultCraftFamily.ANAL in value.families
        }
        toilet = {
            value.fragment_id
            for value in self.catalog.fragments
            if AdultCraftFamily.TOILET_SCAT in value.families
        }
        self.assertTrue(anal)
        self.assertTrue(toilet)
        self.assertTrue(anal.isdisjoint(toilet))

    def test_compiler_rebuild_is_byte_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "catalog"
            integrity = root / "SOURCE_INTEGRITY.json"
            rebuilt = compile_catalog(
                provenance_files=PROVENANCE_ROOT / "files",
                curation_path=CATALOG_ROOT / "CURATION.json",
                output_root=output,
                integrity_path=integrity,
            )
            self.assertEqual(rebuilt, self.catalog.manifest)
            self.assertEqual(
                (output / "manifest.json").read_bytes(),
                (CATALOG_ROOT / "manifest.json").read_bytes(),
            )

    def _need(self, request, decision, *, mode, families, axes, channels, concepts):
        beat = decision.current_segment.ordered_beats[0]
        return AdultCraftNeed(
            schema_version=AdultCraftNeed.SCHEMA_VERSION,
            craft_need_id=deterministic_id(
                IdKind.ADULT_CRAFT_NEED,
                "test.adult_craft_need",
                f"{request}|{mode.value}|{'-'.join(value.value for value in families)}",
            ),
            request_id=request,
            decision_id=decision.decision_id,
            sequence_plan_sha256=domain_sha256(
                "cera.sequence_plan.v1",
                (decision.current_segment, decision.future_segments),
            ),
            mode=mode,
            families=families,
            subfamilies=(),
            axes=axes,
            channel_needs=channels,
            beat_needs=(
                BeatCraftNeed(
                    beat.beat_id,
                    beat.actor_id,
                    "validated_current_beat",
                    concepts,
                    axes,
                    tuple(value.channel for value in channels),
                    4,
                ),
            ),
            climax=OutcomeScope(
                OutcomeAuthorityState.ALLOWED,
                SegmentCommitment.NOT_SELECTED,
            ),
            aftermath=OutcomeScope(
                OutcomeAuthorityState.ALLOWED,
                SegmentCommitment.NOT_SELECTED,
            ),
            character_card_sections=(
                CharacterCardSectionNeed(decision.responding_npc_ids[0], ("Embarrassed",)),
            ),
            all_participants_confirmed_adults=True,
            consent_valid_for_current_segment=True,
            blocked_nonconsensual_generation_excluded=True,
        )

    def test_on_and_ex_selection_are_plan_derived_and_budgeted(self) -> None:
        support = composer_support.ComposerTests("runTest")
        support.setUp()
        self.addCleanup(support.doCleanups)
        request = support.request("adult-craft-select", adult=True)
        decision = request.reasoner_outcome.decision
        assert decision is not None
        on_need = self._need(
            request.prepared_turn.request.request_id,
            decision,
            mode=AdultCraftMode.ON,
            families=(AdultCraftFamily.ANAL,),
            axes=(AdultCraftAxis.DIRECT_VOCABULARY,),
            channels=(
                ChannelCraftNeed(
                    RealizationChannel.NARRATION,
                    None,
                    SpecificityRegister.DIRECT,
                    (AdultCraftConcept.ANUS,),
                ),
            ),
            concepts=(AdultCraftConcept.ANUS,),
        )
        selector = AdultCraftSelector(self.catalog, maximum_craft_bytes=12_000)
        on = selector.select(on_need, decision, request_id=on_need.request_id)
        self.assertEqual(on.receipt.selected_modes, (AdultCraftMode.ON,))
        self.assertLessEqual(on.receipt.total_craft_bytes, 12_000)
        self.assertTrue(all(AdultCraftMode.ON in value.modes for value in on.fragments))
        self.assertEqual(
            on.receipt.selected_fragment_ids,
            tuple(value.context_id for value in on.craft_blocks),
        )

        ex_need = replace(
            on_need,
            craft_need_id=deterministic_id(
                IdKind.ADULT_CRAFT_NEED, "test.adult_craft_need", "ex-toilet"
            ),
            mode=AdultCraftMode.EX,
            families=(AdultCraftFamily.TOILET_SCAT,),
            axes=(
                AdultCraftAxis.CAUSAL_PHYSIOLOGY,
                AdultCraftAxis.MATERIAL_CONTINUITY,
            ),
            channel_needs=(
                ChannelCraftNeed(
                    RealizationChannel.NARRATION,
                    None,
                    SpecificityRegister.DIRECT,
                    (AdultCraftConcept.FECES,),
                ),
            ),
            beat_needs=(
                replace(
                    on_need.beat_needs[0],
                    object_concepts=(AdultCraftConcept.FECES,),
                    axes=(
                        AdultCraftAxis.CAUSAL_PHYSIOLOGY,
                        AdultCraftAxis.MATERIAL_CONTINUITY,
                    ),
                ),
            ),
        )
        ex = selector.select(ex_need, decision, request_id=ex_need.request_id)
        self.assertEqual(ex.receipt.selected_modes, (AdultCraftMode.EX,))
        self.assertTrue(
            any(AdultCraftFamily.TOILET_SCAT in value.families for value in ex.fragments)
        )
        self.assertTrue(all(AdultCraftFamily.ANAL not in value.families for value in ex.fragments))

    def test_v3_specificity_contract_preserves_same_channel_as_distinct_beat_obligations(self) -> None:
        support = composer_support.ComposerTests("runTest")
        support.setUp()
        self.addCleanup(support.doCleanups)
        request = support.request("adult-v3-beat-local", adult=True)
        decision = request.reasoner_outcome.decision
        assert decision is not None
        first = decision.current_segment.ordered_beats[0]
        second = replace(
            first,
            beat_id=deterministic_id(IdKind.BEAT, "test.beat", "adult-v3-second"),
            neutral_event="The second validated beat has a different required object.",
        )
        decision = replace(
            decision,
            current_segment=replace(
                decision.current_segment,
                ordered_beats=(first, second),
            ),
        )
        narration_feces = SceneChannelCraftNeedV3(
            RealizationChannel.NARRATION,
            SpecificityRegister.DIRECT,
            (AdultCraftConcept.FECES,),
            (AdultCraftConcept.FECES,),
        )
        narration_anus = SceneChannelCraftNeedV3(
            RealizationChannel.NARRATION,
            SpecificityRegister.DIRECT,
            (AdultCraftConcept.ANUS,),
            (AdultCraftConcept.ANUS,),
        )
        need = AdultCraftNeedV3(
            AdultCraftNeedV3.SCHEMA_VERSION,
            deterministic_id(
                IdKind.ADULT_CRAFT_NEED,
                "test.adult_craft_need.v3",
                "beat-local",
            ),
            request.prepared_turn.request.request_id,
            decision.decision_id,
            domain_sha256(
                "cera.sequence_plan.v1",
                (decision.current_segment, decision.future_segments),
            ),
            AdultCraftMode.EX,
            (AdultCraftFamily.TOILET_SCAT,),
            (),
            (
                BeatCraftRequirementV3(
                    first.beat_id,
                    first.actor_id,
                    "validated_first",
                    (AdultCraftConcept.FECES,),
                    (AdultCraftAxis.DIRECT_VOCABULARY,),
                    (narration_feces,),
                    5,
                ),
                BeatCraftRequirementV3(
                    second.beat_id,
                    second.actor_id,
                    "validated_second",
                    (AdultCraftConcept.ANUS,),
                    (AdultCraftAxis.DIRECT_VOCABULARY,),
                    (narration_anus,),
                    5,
                ),
            ),
            OutcomeScope(OutcomeAuthorityState.ALLOWED, SegmentCommitment.NOT_SELECTED),
            OutcomeScope(OutcomeAuthorityState.ALLOWED, SegmentCommitment.NOT_SELECTED),
            (),
        )
        selection = AdultCraftSelector(
            self.catalog,
            maximum_craft_bytes=20_000,
        ).select(need, decision, request_id=need.request_id)
        requirements = selection.specificity_contract.beat_requirements
        self.assertEqual(len(requirements), 2)
        self.assertEqual(
            tuple(value.beat_id for value in requirements),
            (first.beat_id, second.beat_id),
        )
        self.assertEqual(
            tuple(value.terms[0].concept for value in requirements),
            (AdultCraftConcept.FECES, AdultCraftConcept.ANUS),
        )
        self.assertNotEqual(
            requirements[0].obligation_key,
            requirements[1].obligation_key,
        )

    def test_blocked_or_unresolved_route_cannot_create_craft_need(self) -> None:
        support = composer_support.ComposerTests("runTest")
        support.setUp()
        self.addCleanup(support.doCleanups)
        request = support.request("adult-craft-block", adult=True)
        decision = request.reasoner_outcome.decision
        assert decision is not None
        need = self._need(
            request.prepared_turn.request.request_id,
            decision,
            mode=AdultCraftMode.ON,
            families=(AdultCraftFamily.ANAL,),
            axes=(AdultCraftAxis.DIRECT_VOCABULARY,),
            channels=(
                ChannelCraftNeed(
                    RealizationChannel.NARRATION,
                    None,
                    SpecificityRegister.DIRECT,
                    (AdultCraftConcept.ANUS,),
                ),
            ),
            concepts=(AdultCraftConcept.ANUS,),
        )
        with self.assertRaises(ContractValidationError):
            replace(need, consent_valid_for_current_segment=False)

    def test_craft_need_may_target_only_the_current_beats_that_need_craft(self) -> None:
        support = composer_support.ComposerTests("runTest")
        support.setUp()
        self.addCleanup(support.doCleanups)
        request = support.request("adult-craft-subset", adult=True)
        decision = request.reasoner_outcome.decision
        assert decision is not None
        first = decision.current_segment.ordered_beats[0]
        second = replace(
            first,
            beat_id=deterministic_id(IdKind.BEAT, "test.beat", "adult-craft-noncraft"),
            neutral_event="Hana takes a character-owned pause after the selected adult beat.",
        )
        decision = replace(
            decision,
            current_segment=replace(
                decision.current_segment,
                ordered_beats=(first, second),
            ),
        )
        need = self._need(
            request.prepared_turn.request.request_id,
            decision,
            mode=AdultCraftMode.ON,
            families=(AdultCraftFamily.ANAL,),
            axes=(AdultCraftAxis.DIRECT_VOCABULARY,),
            channels=(
                ChannelCraftNeed(
                    RealizationChannel.NARRATION,
                    None,
                    SpecificityRegister.DIRECT,
                    (AdultCraftConcept.ANUS,),
                ),
            ),
            concepts=(AdultCraftConcept.ANUS,),
        )
        result = AdultCraftSelector(self.catalog).select(
            need,
            decision,
            request_id=need.request_id,
        )
        self.assertEqual(
            tuple(value.beat_id for value in need.beat_needs),
            (first.beat_id,),
        )
        self.assertTrue(result.receipt.selected_fragment_ids)


class AdultCraftContextAndRepairTests(unittest.TestCase):
    def test_live_shaped_catalog_route_uses_plan_selected_craft_and_cards(self) -> None:
        support = live_support.LiveShapedPipelineTests("runTest")
        support.setUp()
        self.addCleanup(support.doCleanups)
        args = list(support.adult_case("adult-catalog-live-shaped"))
        reasoner_request, _, plan, _, runner, _, _ = args
        outcome = runner.authoritative_fixture
        decision = outcome.decision
        assert decision is not None
        beat = decision.current_segment.ordered_beats[0]
        need = AdultCraftNeed(
            AdultCraftNeed.SCHEMA_VERSION,
            deterministic_id(IdKind.ADULT_CRAFT_NEED, "test.need", "live-shaped"),
            reasoner_request.prepared_turn.request.request_id,
            decision.decision_id,
            domain_sha256(
                "cera.sequence_plan.v1",
                (decision.current_segment, decision.future_segments),
            ),
            AdultCraftMode.EX,
            (AdultCraftFamily.GENERAL,),
            (),
            (AdultCraftAxis.BUILDUP,),
            (
                ChannelCraftNeed(
                    RealizationChannel.NARRATION,
                    None,
                    SpecificityRegister.INDIRECT,
                    (),
                ),
            ),
            (
                BeatCraftNeed(
                    beat.beat_id,
                    beat.actor_id,
                    "validated_character_response",
                    (AdultCraftConcept.BUILDUP,),
                    (AdultCraftAxis.BUILDUP,),
                    (RealizationChannel.NARRATION,),
                    3,
                ),
            ),
            OutcomeScope(OutcomeAuthorityState.ALLOWED, SegmentCommitment.NOT_SELECTED),
            OutcomeScope(OutcomeAuthorityState.ALLOWED, SegmentCommitment.NOT_SELECTED),
            (CharacterCardSectionNeed(decision.responding_npc_ids[0], ("Embarrassed",)),),
            True,
            True,
            True,
        )
        outcome = replace(outcome, adult_craft_need=need)
        plan = replace(plan, craft_blocks=())
        cited = outcome.hard_citations[0].evidence_id
        reasoner_fixture = FakeReasonerFixture(
            "adult-catalog-live-shaped",
            (
                FakeToolCall(
                    FakeToolOperation.FETCH_EVIDENCE,
                    EvidenceFetchRequest((cited,), ("claim", "knowledge")),
                ),
            ),
            outcome,
        )
        story = "The supplied source event is preserved. Hana gives a bounded answer."
        candidate = ComposerCandidate(
            ComposerCandidate.SCHEMA_VERSION,
            deterministic_id(IdKind.COMPOSER_CANDIDATE, "test.candidate", "adult-catalog"),
            story,
            text_sha256(story),
            True,
            False,
            False,
            False,
            False,
            False,
            False,
        )
        covered = "The supplied source event is preserved."
        manifest = RealizationManifest(
            RealizationManifest.SCHEMA_VERSION,
            deterministic_id(IdKind.REALIZATION_MANIFEST, "test.manifest", "adult-catalog"),
            candidate.candidate_sha256,
            domain_sha256("cera.scene_decision.v1", decision),
            need.sequence_plan_sha256,
            decision.floor_owner_id,
            tuple(
                CharacterMoveRealization(
                    move.character_id,
                    text_sha256(move.selected_intent),
                    text_sha256(move.action_direction),
                )
                for move in decision.character_moves
            ),
            tuple(
                CharacterRealization(character_id, True, False)
                for character_id in decision.responding_npc_ids
            ),
            tuple(value.beat_id for value in decision.current_segment.ordered_beats),
            (
                SourceUnitCoverage(
                    plan.source_packet.units[0].source_unit_id,
                    0,
                    0,
                    len(covered),
                    plan.source_packet.units[0].required_state,
                ),
            ),
            (
                RealizationSpan(
                    story.index("Hana gives"),
                    len(story),
                    decision.responding_npc_ids[0],
                    RealizationKind.ACTION,
                    None,
                    beat.beat_id,
                    beat.state,
                ),
            ),
            (),
            True,
            True,
            False,
        )
        composer_fixture = FakeComposerFixture(
            "adult-catalog-live-shaped",
            ComposerSubmission(candidate, manifest, ()),
        )
        pipeline = LiveShapedTurnPipeline(
            support.real.reasoner,
            ComposerContextAssembler(support.real.sandbox.service),
            ComposerCoordinator(),
            adult_craft_selector=AdultCraftSelector(
                AdultCraftCatalog.load(CATALOG_ROOT),
                maximum_craft_bytes=20_000,
            ),
            realization_verifier_port=EchoAcceptingSceneRealizationVerifierPort(),
        )
        result = pipeline.execute(
            reasoner_request,
            FakeSceneReasonerPort(reasoner_fixture),
            plan,
            FakeSceneComposerPort(composer_fixture),
            reasoner_fixture=reasoner_fixture,
            composer_fixture=composer_fixture,
            semantic_specificity_port=ScriptedFakeSemanticSpecificityPort(
                "adult-catalog-live-shaped"
            ),
        )
        self.assertIsNotNone(result.adult_craft)
        assert result.adult_craft is not None
        self.assertEqual(result.adult_craft.final_specificity_receipt.status, "accepted")
        self.assertFalse(result.receipt.story_state_committed)
        self.assertEqual(
            result.context.receipt.selected_craft_reference_ids,
            result.adult_craft.selection_receipt.selected_fragment_ids,
        )
        self.assertEqual(result.receipt.external_provider_calls, 0)
        self.assertEqual(support.real.sandbox.store.table_count("artifacts"), 0)
        packet_text = canonical_json(result.context.request.realization_context)
        self.assertIn("**Baseline:**", packet_text)
        self.assertIn("**Embarrassed**", packet_text)
        self.assertNotIn("**Speech drift under possessive development**", packet_text)

    def test_realization_card_sends_only_selected_sections(self) -> None:
        support = real_support.RealGenesisReasonerComposerTests("runTest")
        support.setUp()
        self.addCleanup(support.doCleanups)
        hana = real_support.CHARACTER_IDS["Hana"]
        source = "Ted asks Hana a simple question in the shared room."
        prepared, reasoner_request, reasoner_result = support.reasoner_request_and_result(
            "selected-card",
            (hana,),
            source,
            (support.relationship_record("Hana"),),
        )
        base_request, _ = support.compose(
            "selected-card",
            prepared,
            reasoner_request,
            reasoner_result,
            source,
        )
        assembled = ComposerContextAssembler(support.sandbox.service).assemble(
            base_request,
            reasoner_result,
            realization_card_sections=(
                CharacterCardSectionNeed(hana, ("Embarrassed",)),
            ),
        )
        voice = next(
            value
            for value in assembled.request.realization_context.blocks
            if value.kind is ComposerContextKind.CHARACTER_VOICE
        )
        self.assertIs(voice.source, ComposerContextSource.EVIDENCE_SECTION_VIEW)
        assert voice.evidence_section_view is not None
        selected = voice.evidence_section_view.selected_text
        self.assertIn("**Baseline:**", selected)
        self.assertIn("**Embarrassed**", selected)
        self.assertNotIn("**Speech drift under possessive development**", selected)
        self.assertNotIn("**Defending a daughter**", selected)

    def test_specificity_is_channel_scoped_and_one_repair_gets_full_revalidation(self) -> None:
        support = composer_support.ComposerTests("runTest")
        support.setUp()
        self.addCleanup(support.doCleanups)
        request = support.request("adult-craft-repair", adult=True)
        decision = request.reasoner_outcome.decision
        assert decision is not None
        beat = decision.current_segment.ordered_beats[0]
        catalog = AdultCraftCatalog.load(CATALOG_ROOT)
        need = AdultCraftNeed(
            AdultCraftNeed.SCHEMA_VERSION,
            deterministic_id(IdKind.ADULT_CRAFT_NEED, "test.need", "repair"),
            request.prepared_turn.request.request_id,
            decision.decision_id,
            request.sequence_plan_sha256,
            AdultCraftMode.ON,
            (AdultCraftFamily.ANAL,),
            (),
            (AdultCraftAxis.DIRECT_VOCABULARY, AdultCraftAxis.ACTION_BOUND_SOUND),
            (
                ChannelCraftNeed(
                    RealizationChannel.NARRATION,
                    None,
                    SpecificityRegister.DIRECT,
                    (AdultCraftConcept.ANUS,),
                ),
                ChannelCraftNeed(
                    RealizationChannel.SOUND_EFFECT,
                    None,
                    SpecificityRegister.DIRECT,
                    (AdultCraftConcept.SOUND_EFFECT,),
                ),
            ),
            (
                BeatCraftNeed(
                    beat.beat_id,
                    beat.actor_id,
                    "validated_motion",
                    (AdultCraftConcept.ANUS, AdultCraftConcept.SOUND_EFFECT),
                    (AdultCraftAxis.DIRECT_VOCABULARY, AdultCraftAxis.ACTION_BOUND_SOUND),
                    (RealizationChannel.NARRATION, RealizationChannel.SOUND_EFFECT),
                    5,
                ),
            ),
            OutcomeScope(OutcomeAuthorityState.ALLOWED, SegmentCommitment.NOT_SELECTED),
            OutcomeScope(OutcomeAuthorityState.ALLOWED, SegmentCommitment.NOT_SELECTED),
            (),
            True,
            True,
            True,
        )
        selection = AdultCraftSelector(catalog, maximum_craft_bytes=20_000).select(
            need,
            decision,
            request_id=need.request_id,
        )
        story = "Locked opening. Her anus tightened while the motion ended with a vague sound. Locked ending."
        action_start = story.index("Her anus")
        action_end = story.index(" while")
        sound_start = story.index("sound")
        sound_end = sound_start + len("sound")
        submission = support.submission(
            request,
            "adult-craft-repair",
            story_text=story,
            spans=(
                RealizationSpan(
                    action_start,
                    action_end,
                    decision.responding_npc_ids[0],
                    RealizationKind.ACTION,
                    None,
                    beat.beat_id,
                    beat.state,
                ),
                RealizationSpan(
                    sound_start,
                    sound_end,
                    decision.responding_npc_ids[0],
                    RealizationKind.SOUND_EFFECT,
                    None,
                    beat.beat_id,
                    beat.state,
                ),
            ),
        )
        validator = SpecificityValidator()
        failed = validator.validate(
            selection.specificity_contract,
            submission.candidate,
            submission.manifest,
        )
        self.assertEqual(failed.status, "repair_required")
        self.assertEqual({value.channel for value in failed.findings}, {RealizationChannel.SOUND_EFFECT})

        replacement = "Her anus tightened; *squelch* marked the motion."
        action_quote = "Her anus tightened"
        sound_quote = "squelch"
        proposal = BeatScopedRepairProposal(
            replacement,
            (
                ReplacementSpan(
                    replacement.index(action_quote),
                    replacement.index(action_quote) + len(action_quote),
                    decision.responding_npc_ids[0],
                    RealizationKind.ACTION,
                    None,
                    beat.beat_id,
                    beat.state,
                ),
                ReplacementSpan(
                    replacement.index(sound_quote),
                    replacement.index(sound_quote) + len(sound_quote),
                    decision.responding_npc_ids[0],
                    RealizationKind.SOUND_EFFECT,
                    None,
                    beat.beat_id,
                    beat.state,
                ),
            ),
        )
        coordinator = BeatScopedRepairCoordinator(validator)
        semantic_coordinator = SemanticSpecificityCoordinator()
        semantic_port = ScriptedFakeSemanticSpecificityPort("adult-repair-semantic")
        semantic_request = semantic_coordinator.build_request(
            request,
            need,
            selection.specificity_contract,
            submission.candidate,
            submission.manifest,
            stage=SemanticVerificationStage.INITIAL,
        )
        semantic_initial = semantic_coordinator.execute(semantic_request, semantic_port)
        repair_request = coordinator.build_request(
            request,
            submission,
            selection.specificity_contract,
            failed,
            semantic_initial.result,
            semantic_initial.receipt,
            beat.beat_id,
        )
        port = FakeBeatScopedRepairPort(proposal)
        repaired = coordinator.execute(
            repair_request,
            request,
            submission,
            selection.specificity_contract,
            semantic_initial.receipt,
            port,
        )
        self.assertEqual(port.call_count, 1)
        self.assertEqual(repaired.specificity_receipt.status, "accepted")
        semantic_post_request = semantic_coordinator.build_request(
            request,
            need,
            selection.specificity_contract,
            repaired.submission.candidate,
            repaired.submission.manifest,
            stage=SemanticVerificationStage.POST_REPAIR,
            prior_semantic_receipt_sha256=semantic_initial.receipt.receipt_sha256,
        )
        semantic_post = semantic_coordinator.execute(semantic_post_request, semantic_port)
        self.assertEqual(semantic_post.result.status, "accepted")
        self.assertTrue(repaired.submission.candidate.story_text.startswith("Locked opening. "))
        self.assertTrue(repaired.submission.candidate.story_text.endswith(" Locked ending."))
        fixture = FakeComposerFixture("adult-beat-repair-full-validation", repaired.submission)
        accepted = ComposerCoordinator().execute(
            request,
            FakeSceneComposerPort(fixture),
            fixture=fixture,
        )
        self.assertEqual(accepted.validation_receipt.status, "accepted_in_memory")
        self.assertFalse(accepted.validation_receipt.story_state_committed)


class SemanticSpecificityVerifierTests(unittest.TestCase):
    def _request(
        self,
        label: str,
        *,
        stage: SemanticVerificationStage = SemanticVerificationStage.INITIAL,
        with_transition: bool = False,
    ) -> SemanticSpecificityRequest:
        actor = deterministic_id(IdKind.CHARACTER, "test.semantic.actor", label)
        beat_one = deterministic_id(IdKind.BEAT, "test.semantic.beat", label + "-1")
        beat_two = deterministic_id(IdKind.BEAT, "test.semantic.beat", label + "-2")
        text_one = "The required direct term appears, but the central relation is unclear."
        text_two = "A later material state is declared."
        spans = [
            SemanticBeatSpan(
                beat_one,
                0,
                len(text_one),
                text_one,
                text_sha256(text_one),
                (RealizationChannel.NARRATION,),
            )
        ]
        bindings = [
            SemanticActionBinding(
                beat_one,
                actor,
                "locked_action",
                (AdultCraftConcept.ANUS, AdultCraftConcept.FECES),
                (AdultCraftConcept.FECES,),
            )
        ]
        transitions = ()
        if with_transition:
            spans.append(
                SemanticBeatSpan(
                    beat_two,
                    len(text_one),
                    len(text_one) + len(text_two),
                    text_two,
                    text_sha256(text_two),
                    (RealizationChannel.NARRATION,),
                )
            )
            bindings.append(
                SemanticActionBinding(
                    beat_two,
                    actor,
                    "locked_followup",
                    (AdultCraftConcept.MATERIAL_CONTINUITY,),
                    (AdultCraftConcept.MATERIAL_CONTINUITY,),
                )
            )
            transitions = (SemanticRequiredTransition(beat_one, beat_two),)
        request_hash = text_sha256(label + "-candidate")
        prior = text_sha256(label + "-prior") if stage is SemanticVerificationStage.POST_REPAIR else None
        return SemanticSpecificityRequest(
            SemanticSpecificityRequest.SCHEMA_VERSION,
            deterministic_id(IdKind.SEMANTIC_SPECIFICITY, "test.semantic.request", label),
            deterministic_id(IdKind.REQUEST, "test.semantic.story", label),
            deterministic_id(IdKind.DECISION, "test.semantic.decision", label),
            deterministic_id(IdKind.SPECIFICITY_CONTRACT, "test.semantic.contract", label),
            text_sha256(label + "-contract"),
            request_hash,
            text_sha256(label + "-manifest"),
            stage,
            (actor,),
            tuple(spans),
            tuple(bindings),
            transitions,
            prior,
            True,
            True,
            False,
        )

    def _verify(self, label, code, *, stage=SemanticVerificationStage.INITIAL, transition=False):
        request = self._request(label, stage=stage, with_transition=transition)
        finding = SemanticSpecificityFinding(request.beat_spans[-1].beat_id, code)
        port = ScriptedFakeSemanticSpecificityPort(
            label,
            initial_findings=(finding,) if stage is SemanticVerificationStage.INITIAL else (),
            post_repair_findings=(finding,) if stage is SemanticVerificationStage.POST_REPAIR else (),
        )
        result = SemanticSpecificityCoordinator().execute(request, port)
        self.assertEqual(port.call_count, 1)
        self.assertFalse(result.receipt.exact_beat_text_retained)
        self.assertFalse(result.receipt.raw_candidate_retained)
        self.assertNotIn(request.beat_spans[0].exact_text, canonical_json(result.receipt))
        return result

    def test_direct_term_can_still_fail_ambiguous_central_act(self) -> None:
        result = self._verify(
            "semantic-ambiguous",
            SemanticSpecificityFindingCode.CENTRAL_ACT_AMBIGUOUS,
        )
        self.assertEqual(result.result.status, "repair_required")

    def test_correct_term_can_fail_locked_actor_action_object_association(self) -> None:
        result = self._verify(
            "semantic-binding",
            SemanticSpecificityFindingCode.ACTOR_ACTION_OBJECT_MISMATCH,
        )
        self.assertEqual(result.result.findings[0].code.value, "actor_action_object_mismatch")

    def test_required_transition_missing_or_out_of_order_is_not_lexical_success(self) -> None:
        for code in (
            SemanticSpecificityFindingCode.REQUIRED_TRANSITION_MISSING,
            SemanticSpecificityFindingCode.REQUIRED_TRANSITION_OUT_OF_ORDER,
        ):
            with self.subTest(code=code.value):
                result = self._verify(
                    "semantic-transition-" + code.value,
                    code,
                    transition=True,
                )
                self.assertEqual(result.result.status, "repair_required")

    def test_required_material_result_contradiction_fails(self) -> None:
        result = self._verify(
            "semantic-material",
            SemanticSpecificityFindingCode.MATERIAL_OUTCOME_CONTRADICTED,
        )
        self.assertEqual(result.result.status, "repair_required")

    def test_false_composer_coverage_declaration_is_not_proof(self) -> None:
        result = self._verify(
            "semantic-coverage",
            SemanticSpecificityFindingCode.COVERAGE_DECLARATION_FALSE,
        )
        self.assertFalse(result.result.coverage_declarations_treated_as_proof)

    def test_post_repair_semantic_failure_is_terminal_not_repairable(self) -> None:
        result = self._verify(
            "semantic-post-repair",
            SemanticSpecificityFindingCode.CENTRAL_ACT_AMBIGUOUS,
            stage=SemanticVerificationStage.POST_REPAIR,
        )
        self.assertEqual(result.result.status, "rejected")
        self.assertEqual(result.result.repairable_beat_ids, ())

    def test_pipeline_ends_after_semantic_failure_on_only_repair(self) -> None:
        support = live_support.LiveShapedPipelineTests("runTest")
        support.setUp()
        self.addCleanup(support.doCleanups)
        args = list(support.adult_case("semantic-terminal-repair"))
        reasoner_request, _, plan, _, runner, _, _ = args
        outcome = runner.authoritative_fixture
        decision = outcome.decision
        assert decision is not None
        beat = decision.current_segment.ordered_beats[0]
        need = AdultCraftNeed(
            AdultCraftNeed.SCHEMA_VERSION,
            deterministic_id(IdKind.ADULT_CRAFT_NEED, "test.need", "semantic-terminal"),
            reasoner_request.prepared_turn.request.request_id,
            decision.decision_id,
            domain_sha256(
                "cera.sequence_plan.v1",
                (decision.current_segment, decision.future_segments),
            ),
            AdultCraftMode.ON,
            (AdultCraftFamily.ANAL,),
            (),
            (AdultCraftAxis.DIRECT_VOCABULARY,),
            (
                ChannelCraftNeed(
                    RealizationChannel.NARRATION,
                    None,
                    SpecificityRegister.DIRECT,
                    (AdultCraftConcept.ANUS,),
                ),
            ),
            (
                BeatCraftNeed(
                    beat.beat_id,
                    beat.actor_id,
                    "locked_action",
                    (AdultCraftConcept.ANUS,),
                    (AdultCraftAxis.DIRECT_VOCABULARY,),
                    (RealizationChannel.NARRATION,),
                    5,
                ),
            ),
            OutcomeScope(OutcomeAuthorityState.ALLOWED, SegmentCommitment.NOT_SELECTED),
            OutcomeScope(OutcomeAuthorityState.ALLOWED, SegmentCommitment.NOT_SELECTED),
            (CharacterCardSectionNeed(decision.responding_npc_ids[0], ("Embarrassed",)),),
            True,
            True,
            True,
        )
        outcome = replace(outcome, adult_craft_need=need)
        plan = replace(plan, craft_blocks=())
        cited = outcome.hard_citations[0].evidence_id
        reasoner_fixture = FakeReasonerFixture(
            "semantic-terminal-repair",
            (
                FakeToolCall(
                    FakeToolOperation.FETCH_EVIDENCE,
                    EvidenceFetchRequest((cited,), ("claim", "knowledge")),
                ),
            ),
            outcome,
        )
        prefix = "The supplied source event is preserved. "
        beat_text = "Hana names her anus but leaves the action unclear."
        story = prefix + beat_text
        candidate = ComposerCandidate(
            ComposerCandidate.SCHEMA_VERSION,
            deterministic_id(IdKind.COMPOSER_CANDIDATE, "test.candidate", "semantic-terminal"),
            story,
            text_sha256(story),
            True,
            False,
            False,
            False,
            False,
            False,
            False,
        )
        manifest = RealizationManifest(
            RealizationManifest.SCHEMA_VERSION,
            deterministic_id(IdKind.REALIZATION_MANIFEST, "test.manifest", "semantic-terminal"),
            candidate.candidate_sha256,
            domain_sha256("cera.scene_decision.v1", decision),
            need.sequence_plan_sha256,
            decision.floor_owner_id,
            tuple(
                CharacterMoveRealization(
                    move.character_id,
                    text_sha256(move.selected_intent),
                    text_sha256(move.action_direction),
                )
                for move in decision.character_moves
            ),
            tuple(
                CharacterRealization(character_id, True, False)
                for character_id in decision.responding_npc_ids
            ),
            tuple(value.beat_id for value in decision.current_segment.ordered_beats),
            (
                SourceUnitCoverage(
                    plan.source_packet.units[0].source_unit_id,
                    0,
                    0,
                    len(prefix.rstrip()),
                    plan.source_packet.units[0].required_state,
                ),
            ),
            (
                RealizationSpan(
                    len(prefix),
                    len(story),
                    decision.responding_npc_ids[0],
                    RealizationKind.ACTION,
                    None,
                    beat.beat_id,
                    beat.state,
                ),
            ),
            (),
            True,
            True,
            False,
        )
        composer_fixture = FakeComposerFixture(
            "semantic-terminal-repair",
            ComposerSubmission(candidate, manifest, ()),
        )
        finding = SemanticSpecificityFinding(
            beat.beat_id,
            SemanticSpecificityFindingCode.CENTRAL_ACT_AMBIGUOUS,
        )
        semantic_port = ScriptedFakeSemanticSpecificityPort(
            "semantic-terminal-repair",
            initial_findings=(finding,),
            post_repair_findings=(finding,),
        )
        replacement = "Hana names her anus and still leaves the locked action unclear."
        repair_port = FakeBeatScopedRepairPort(
            BeatScopedRepairProposal(
                replacement,
                (
                    ReplacementSpan(
                        0,
                        len(replacement),
                        decision.responding_npc_ids[0],
                        RealizationKind.ACTION,
                        None,
                        beat.beat_id,
                        beat.state,
                    ),
                ),
            )
        )
        pipeline = LiveShapedTurnPipeline(
            support.real.reasoner,
            ComposerContextAssembler(support.real.sandbox.service),
            ComposerCoordinator(),
            adult_craft_selector=AdultCraftSelector(
                AdultCraftCatalog.load(CATALOG_ROOT),
                maximum_craft_bytes=20_000,
            ),
            realization_verifier_port=EchoAcceptingSceneRealizationVerifierPort(),
        )
        with self.assertRaises(LiveShapedTurnFailure) as raised:
            pipeline.execute(
                reasoner_request,
                FakeSceneReasonerPort(reasoner_fixture),
                plan,
                FakeSceneComposerPort(composer_fixture),
                reasoner_fixture=reasoner_fixture,
                composer_fixture=composer_fixture,
                semantic_specificity_port=semantic_port,
                beat_repair_port=repair_port,
            )
        self.assertIs(
            raised.exception.envelope.error_code,
            ErrorCode.ADULT_SEMANTIC_SPECIFICITY_FAILED,
        )
        self.assertEqual(semantic_port.call_count, 2)
        self.assertEqual(repair_port.call_count, 1)
        self.assertEqual(support.real.sandbox.store.table_count("artifacts"), 0)


if __name__ == "__main__":
    unittest.main()
