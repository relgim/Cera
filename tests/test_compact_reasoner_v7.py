from __future__ import annotations

from dataclasses import replace
import unittest

from cera.contracts import (
    DecisionRoute,
    InteractionTopology,
    InteriorityLevel,
    NaturalStopReason,
    PromptTone,
    SceneFunction,
    SceneRunwayClass,
    SourceClaimAuthority,
    SourceClaimKind,
)
from cera.errors import ContractValidationError, ErrorCode
from cera.ids import IdKind
from cera.reasoner import (
    CodexReasonerDraftV7Compact,
    CompactBeatV7,
    CompactMaterialTransitionV7,
    CompactResponderV7,
    DraftSourceClaimV5,
    InterventionReason,
    ReasonerOutcomeStatus,
    codex_reasoner_draft_v7_compact_json_schema,
    compile_compact_v7_to_v6,
    derive_scene_cast_scope,
    normalized_reasoner_semantics,
)
from cera.providers import ProviderSchemaDialect, project_provider_output_schema
from cera.serialization import canonical_json

from tests.support import tid


class CompactReasonerV7Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.ted = tid(IdKind.CHARACTER, "ted")
        self.hana = tid(IdKind.CHARACTER, "hana")
        self.mia = tid(IdKind.CHARACTER, "mia")
        self.sakura = tid(IdKind.CHARACTER, "sakura")
        self.enne = tid(IdKind.CHARACTER, "enne")
        self.source = tid(IdKind.SOURCE_UNIT, "source")

    def ready(self, *, responders=None, beats=None):
        responders = responders or (
            CompactResponderV7(
                character_id=self.hana,
                intervention_reason=None,
                perception_basis="Hana and Mia share the accepted conversational moment.",
                intent="Keep the welcome warm and give Mia room to contribute.",
                tactic="Hana opens the exchange, then yields the floor naturally.",
                evidence_ids=(),
                knowledge_limits=("Do not infer Ted's response.",),
            ),
            CompactResponderV7(
                character_id=self.mia,
                intervention_reason=InterventionReason.CURRENT_SOURCE_ADDRESS,
                perception_basis="Mia is already present with Hana in the accepted scene.",
                intent="Continue the low-pressure welcome in her own reserved manner.",
                tactic="Mia answers after Hana without adding another family member.",
                evidence_ids=(),
                knowledge_limits=("Use only Mia-owned knowledge.",),
            ),
        )
        beats = beats or (
            CompactBeatV7(
                beat_key="hana_floor",
                actor_ids=(self.hana,),
                goal="Hana advances the current household conversation.",
                causal_basis="Hana owns the current floor in accepted continuity.",
                advances=("Hana offers one warm, bounded conversational opening.",),
                result="The floor can pass to Mia without requiring Ted to act.",
                evidence_ids=(),
                source_claim_keys=("continue",),
                protected_user_allowed_kinds=(),
                protected_user_source_claim_keys=(),
                continuity=("Hana and Mia remain in the entrance area.",),
                material_transitions=(),
                realization_space=("DeepSeek chooses Hana's exact wording and pacing.",),
            ),
            CompactBeatV7(
                beat_key="mia_follow",
                actor_ids=(self.mia,),
                goal="Mia contributes a distinct follow-through.",
                causal_basis="Mia is already present and has a conversational opening.",
                advances=("Mia adds one character-specific response to Hana.",),
                result="Their exchange reaches a natural Ted-facing handoff.",
                evidence_ids=(),
                source_claim_keys=("continue",),
                protected_user_allowed_kinds=(),
                protected_user_source_claim_keys=(),
                continuity=("No absent sister enters.",),
                material_transitions=(),
                realization_space=("DeepSeek owns Mia's exact prose and micro-action.",),
            ),
        )
        return CodexReasonerDraftV7Compact(
            schema_version=CodexReasonerDraftV7Compact.SCHEMA_VERSION,
            status=ReasonerOutcomeStatus.DECISION_READY,
            route=DecisionRoute.ORDINARY,
            scene_goal="Continue the accepted Hana-Mia exchange without inventing Ted.",
            reason_code="develop_current_floor_then_distinct_followthrough",
            responders=responders,
            floor_owner_id=responders[0].character_id,
            source_claims=(
                DraftSourceClaimV5(
                    claim_key="continue",
                    source_unit_id=self.source,
                    kind=SourceClaimKind.CREATIVE_DIRECTION,
                    authority=SourceClaimAuthority.SOURCE_AUTHORIZED,
                    quote="Continue the scene",
                    normalized_meaning="Continue with only the current characters.",
                    affected_character_ids=tuple(value.character_id for value in responders),
                    rationale="The exact current source controls continuation scope.",
                ),
            ),
            beats=beats,
            runway_class=SceneRunwayClass.DEVELOPED,
            continue_beyond_prompt_endpoint=True,
            stop_reason=NaturalStopReason.MEANINGFUL_USER_DECISION,
            stop_condition="Stop before Ted's next unsupplied meaningful choice.",
            interaction_topology=(
                InteractionTopology.SINGLE_NPC_FLOOR
                if len(responders) == 1
                else InteractionTopology.MULTI_NPC_SHARED_FLOOR
            ),
            scene_function=SceneFunction.ORDINARY_SOCIAL,
            tone=PromptTone.WARM,
            interiority_level=InteriorityLevel.LOW,
            essential_continuity=("Only Hana and Mia are currently active.",),
            future_segments=(),
            uncertainties=("Ted's next response remains unknown.",),
            prohibited_inferences=("Do not add an absent family member.",),
            insufficiencies=(),
            blocker_code=None,
            adult_craft_need=None,
            development_atoms=(),
            protected_user_boundary_acknowledged=True,
        )

    def test_exact_continuation_derives_hana_mia_without_hardcoding(self) -> None:
        scope = derive_scene_cast_scope(
            raw_message="Continue the scene with only the current characters as they talk to eachother.",
            protected_user_id=self.ted,
            world_known_npc_ids=(self.hana, self.mia, self.sakura, self.enne),
            accepted_active_npc_ids=(self.hana, self.mia),
        )
        self.assertEqual(scope.currently_active_character_ids, (self.ted, self.hana, self.mia))
        self.assertEqual(scope.eligible_responder_ids, (self.hana, self.mia))
        self.assertEqual(scope.scene_reachable_character_ids[-2:], (self.sakura, self.enne))

    def test_single_character_and_explicit_floor_selection(self) -> None:
        scope = derive_scene_cast_scope(
            raw_message="Mia answers the question.",
            protected_user_id=self.ted,
            world_known_npc_ids=(self.hana, self.mia, self.sakura),
            accepted_active_npc_ids=(),
            explicitly_named_npc_ids=(self.mia,),
        )
        self.assertEqual(scope.eligible_responder_ids, (self.mia,))
        responder = CompactResponderV7(
            self.mia,
            None,
            "Mia hears the cue.",
            "Answer.",
            "Speak.",
            (),
            ("No private transfer.",),
        )
        beat = CompactBeatV7(
            "mia",
            (self.mia,),
            "Answer.",
            "Mia owns the floor.",
            ("Mia responds.",),
            "The answer is complete.",
            (),
            ("continue",),
            (),
            (),
            (),
            (),
            ("Exact prose remains creative.",),
        )
        compiled = compile_compact_v7_to_v6(self.ready(responders=(responder,), beats=(beat,)))
        self.assertEqual(compiled.floor_owner_id, self.mia)
        self.assertIsNone(compiled.participation[0].intervention_reason)

    def test_absent_reference_does_not_override_current_only_scope(self) -> None:
        scope = derive_scene_cast_scope(
            raw_message="Continue with only the current characters; Sakura is elsewhere.",
            protected_user_id=self.ted,
            world_known_npc_ids=(self.hana, self.mia, self.sakura),
            accepted_active_npc_ids=(self.hana, self.mia),
            explicitly_named_npc_ids=(self.sakura,),
        )
        self.assertEqual(scope.exact_source_responder_ids, (self.sakura,))
        self.assertEqual(scope.eligible_responder_ids, (self.hana, self.mia))

    def test_ted_cannot_be_a_beat_actor_and_stop_boundary_survives(self) -> None:
        invalid = replace(self.ready().beats[0], actor_ids=(self.ted,))
        with self.assertRaisesRegex(ContractValidationError, "unselected actor"):
            self.ready(beats=(invalid, self.ready().beats[1]))
        compiled = compile_compact_v7_to_v6(self.ready())
        self.assertEqual(
            compiled.causal_runway.stop_condition,
            "Stop before Ted's next unsupplied meaningful choice.",
        )
        self.assertTrue(
            all(not block.protected_user_allowance.allowed_kinds for block in compiled.event_blocks)
        )

    def test_material_movement_and_every_causal_beat_survive_compilation(self) -> None:
        tray = tid(IdKind.MATERIAL, "tea_tray")
        first = replace(
            self.ready().beats[0],
            material_transitions=(
                CompactMaterialTransitionV7(tray, "on low table", "lifted by Hana"),
            ),
        )
        draft = self.ready(beats=(first, self.ready().beats[1]))
        compiled = compile_compact_v7_to_v6(draft)
        self.assertEqual(len(compiled.event_blocks), len(draft.beats))
        self.assertIn(
            f"{tray}: on low table -> lifted by Hana",
            compiled.event_blocks[0].writer_scaffold.physical_and_material_continuity,
        )
        self.assertEqual(
            normalized_reasoner_semantics(draft),
            normalized_reasoner_semantics(compiled),
        )

    def test_private_knowledge_and_false_presupposition_remain_explicit(self) -> None:
        responder = replace(
            self.ready().responders[1],
            knowledge_limits=(
                "Mia does not know Hana's owner-private suspicion.",
                "Treat the user's false presupposition as nonbinding.",
            ),
        )
        draft = self.ready(responders=(self.ready().responders[0], responder))
        compiled = compile_compact_v7_to_v6(draft)
        self.assertEqual(
            compiled.character_moves[1].knowledge_constraints,
            responder.knowledge_limits,
        )

    def test_ordinary_draft_cannot_activate_adult_craft(self) -> None:
        with self.assertRaisesRegex(ContractValidationError, "ordinary compact"):
            replace(self.ready(), adult_craft_need=object())

    def test_non_ready_reset_is_mandatory(self) -> None:
        ready = self.ready()
        with self.assertRaisesRegex(ContractValidationError, "non-ready"):
            replace(
                ready,
                status=ReasonerOutcomeStatus.INSUFFICIENT_EVIDENCE,
                insufficiencies=("Missing exact evidence.",),
            )
        insufficient = CodexReasonerDraftV7Compact(
            schema_version=CodexReasonerDraftV7Compact.SCHEMA_VERSION,
            status=ReasonerOutcomeStatus.INSUFFICIENT_EVIDENCE,
            route=None,
            scene_goal=None,
            reason_code=None,
            responders=(),
            floor_owner_id=None,
            source_claims=(),
            beats=(),
            runway_class=None,
            continue_beyond_prompt_endpoint=None,
            stop_reason=None,
            stop_condition=None,
            interaction_topology=None,
            scene_function=None,
            tone=None,
            interiority_level=None,
            essential_continuity=(),
            future_segments=(),
            uncertainties=(),
            prohibited_inferences=(),
            insufficiencies=("Missing exact evidence.",),
            blocker_code=None,
            adult_craft_need=None,
            development_atoms=(),
            protected_user_boundary_acknowledged=True,
        )
        self.assertEqual(compile_compact_v7_to_v6(insufficient).responding_npc_ids, ())

    def test_schema_and_compiler_are_deterministic_and_smaller_than_v6(self) -> None:
        first = codex_reasoner_draft_v7_compact_json_schema()
        second = codex_reasoner_draft_v7_compact_json_schema()
        self.assertEqual(canonical_json(first), canonical_json(second))
        project_provider_output_schema(first, ProviderSchemaDialect.OPENAI_STRUCTURED_OUTPUT_V1)
        from cera.reasoner import codex_reasoner_draft_v6_json_schema

        self.assertLess(
            len(canonical_json(first)),
            len(canonical_json(codex_reasoner_draft_v6_json_schema())),
        )
        self.assertEqual(
            compile_compact_v7_to_v6(self.ready()),
            compile_compact_v7_to_v6(self.ready()),
        )

    def test_sibling_scopes_do_not_share_accepted_active_cast(self) -> None:
        first = derive_scene_cast_scope(
            raw_message="Continue the scene.",
            protected_user_id=self.ted,
            world_known_npc_ids=(self.hana, self.mia, self.sakura),
            accepted_active_npc_ids=(self.hana, self.mia),
        )
        sibling = derive_scene_cast_scope(
            raw_message="Continue the scene.",
            protected_user_id=self.ted,
            world_known_npc_ids=(self.hana, self.mia, self.sakura),
            accepted_active_npc_ids=(self.sakura,),
        )
        self.assertNotEqual(first.eligible_responder_ids, sibling.eligible_responder_ids)


if __name__ == "__main__":
    unittest.main()
