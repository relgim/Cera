from __future__ import annotations

import unittest
from dataclasses import replace
from pathlib import Path
from typing import ClassVar

from cera.adult_pipeline.contracts import (
    AdultContextFactV1,
    AdultCraftMode,
    AdultEntryReason,
)
from cera.adult_pipeline.craft_catalog import CatalogAdultCraftRetrieval
from cera.adult_pipeline.preparation import (
    AdultTurnPreparationBuilder,
    build_adult_turn_preparation_builder,
)
from cera.cognition.contracts import (
    AutonomyApplicationV1,
    CloseAlternativeV1,
    CognitionPlanV1,
    DecisionItemLinkV1,
    DecisionRecordV1,
    KnowledgeCertainty,
    LogicRoute,
    MaterialPressureV1,
    ObserverFrameV1,
    PerceivedFactV1,
    PressureLevel,
    ResponseLayersV1,
    RouteTransitionProposalV1,
    UserDirectionDisposition,
)
from cera.errors import ContractValidationError
from cera.pi_scene.http_contracts import (
    LeanSceneRequestControlsV1,
    LeanSceneRequestControlsV2,
)
from cera.pi_scene.review_store import LeanSceneTurnInputV1
from cera.sequence_first.contracts import ItemKind, SequenceDraftV1, SequenceItemV1
from cera.serialization import canonical_json, text_sha256

ROOT = Path(__file__).parents[1]
CATALOG_ROOT = ROOT / "adult" / "catalog" / "adult_craft_v1"
HANA = "character:hana"
PROTECTED_A = "PROTECTED_EXACT_ADULT_CONTINUITY_SENTINEL_ALPHA"
PROTECTED_B = "PROTECTED_EXACT_ADULT_CONTINUITY_SENTINEL_BRAVO"


def _turn(*, craft_mode: str = "ex", controls_version: int = 2) -> LeanSceneTurnInputV1:
    controls: LeanSceneRequestControlsV1
    if controls_version == 2:
        controls = LeanSceneRequestControlsV2(
            schema_version=LeanSceneRequestControlsV2.SCHEMA_VERSION,
            session_id="session-adult-preparation",
            scene_depth="medium",
            character_autonomy="both",
            adult_craft_mode=craft_mode,
        )
    else:
        controls = LeanSceneRequestControlsV1(
            schema_version=LeanSceneRequestControlsV1.SCHEMA_VERSION,
            session_id="session-adult-preparation-v1",
            scene_depth="short",
            character_autonomy="mind",
        )
    return LeanSceneTurnInputV1(
        world_id="world:test",
        branch_id="branch:test",
        scene_id="scene:test",
        exact_user_source=(
            "Continue the buildup with a slow rub, an action-bound sound, and a clear aftermath."
        ),
        current_state={"public_state": "Hana and Ted remain in the private room."},
        characters={HANA: {"name": "Hana"}},
        relationships={},
        recent_prose=(),
        relevant_memories={},
        voice_examples={},
        craft_index={},
        request_controls=controls,
    )


def _facts() -> tuple[AdultContextFactV1, ...]:
    return (
        AdultContextFactV1(
            evidence_ref="evidence:public_scene",
            subject_id=HANA,
            authoritative_fact="Hana is present in her room.",
            visibility="public",
        ),
        AdultContextFactV1(
            evidence_ref="evidence:hana_private",
            subject_id=HANA,
            authoritative_fact="Hana privately carries unresolved unease.",
            visibility="adult_role_private",
        ),
    )


def _decision() -> DecisionRecordV1:
    return DecisionRecordV1(
        decision_key="hana_boundary_response",
        owner_id=HANA,
        causal_trigger_refs=("source:current",),
        observer_frame=ObserverFrameV1(
            directly_perceived=(
                PerceivedFactV1(
                    source_ref="source:current",
                    concise_perception="The interaction reaches a private boundary.",
                    certainty=KnowledgeCertainty.ESTABLISHED,
                ),
            ),
            inferred_meanings=("The next part belongs to the adult logic owner.",),
            unavailable_or_ambiguous=("The later result remains unresolved.",),
        ),
        perceived_event_meaning="The ordinary interaction reaches its route boundary.",
        knowledge_certainty=KnowledgeCertainty.HIGH,
        personal_and_social_meaning="Hana's established outlook shapes her response.",
        response_layers=ResponseLayersV1(
            immediate_involuntary_reaction="Her posture briefly tightens.",
            conscious_interpretation="She recognizes the change in the interaction.",
            subconscious_pressure="Private unease remains present.",
            considered_judgment="She responds according to her current understanding.",
        ),
        selected_intent="Carry her exact non-graphic response to the boundary.",
        concise_decision_basis="Her knowledge and private unease are both material.",
        decisive_factor_refs=("source:current",),
        material_pressures=(
            MaterialPressureV1(
                kind="private_unease",
                level=PressureLevel.MODERATE,
                direction="remain_cautious",
                evidence_refs=("source:current",),
            ),
        ),
        autonomy_application=AutonomyApplicationV1(
            mind_precedence_applied=True,
            body_precedence_applied=True,
            user_direction_disposition=UserDirectionDisposition.PARTIALLY_REALIZED,
            overwhelming_pressure_kind=None,
            concise_effect="Hana's mind and body retain priority through the boundary.",
        ),
        anticipated_immediate_effect="The adult role receives her intact decision state.",
        close_alternative=CloseAlternativeV1(
            intent="Pause before the boundary.",
            why_not_selected="The current state supports a bounded continuation.",
            remains_realistically_available=True,
        ),
        uncertainty=KnowledgeCertainty.MODERATE,
    )


def _plan() -> CognitionPlanV1:
    sequence = SequenceDraftV1(
        items=(
            SequenceItemV1(
                item_key="hana_reaches_boundary",
                kind=ItemKind.ACTION,
                concise_meaning="Hana's non-graphic response reaches the adult boundary.",
                owner_response_semantics="Her cognition and immediate effects remain intact.",
                owner_id=HANA,
                evidence_keys=("source:current",),
            ),
            SequenceItemV1(
                item_key="adult_boundary_stop",
                kind=ItemKind.STOPPING_BOUNDARY,
                concise_meaning="Ordinary cognition stops before adult realization.",
            ),
        ),
        durable_changes=(),
        presence_changes=(),
        resulting_public_state="The interaction has reached a private boundary.",
        unresolved_threads=("The protected continuation remains unresolved.",),
        stopping_boundary="Stop before the adult logic owner begins realization.",
    )
    return CognitionPlanV1(
        sequence=sequence,
        decision_records=(_decision(),),
        decision_item_links=(
            DecisionItemLinkV1(
                item_key="hana_reaches_boundary",
                decision_key="hana_boundary_response",
            ),
        ),
        provisional_dependencies=(),
        route_transition=RouteTransitionProposalV1(
            from_route=LogicRoute.ORDINARY,
            to_route=LogicRoute.ADULT,
            boundary_item_key="adult_boundary_stop",
            non_graphic_handoff_summary=(
                "Hana reaches the private boundary with her unease and decision intact."
            ),
            character_effect_refs=(HANA,),
            return_condition="Return after the protected sequence reaches a stable boundary.",
        ),
    )


class AdultTurnPreparationTests(unittest.TestCase):
    retrieval: ClassVar[CatalogAdultCraftRetrieval]
    builder: ClassVar[AdultTurnPreparationBuilder]

    @classmethod
    def setUpClass(cls) -> None:
        cls.retrieval = CatalogAdultCraftRetrieval(CATALOG_ROOT)
        cls.builder = build_adult_turn_preparation_builder(cls.retrieval)

    def test_cognition_handoff_preserves_complete_exact_plan(self) -> None:
        plan = _plan()
        boundaries = ("Stop at the established conversational floor.",)
        result = self.builder.from_cognition_handoff(
            turn_input=_turn(),
            cognition_plan=plan,
            accepted_safe_projection="The accepted public scene remains current.",
            protected_adult_continuity=None,
            current_facts=_facts(),
            product_story_boundaries=boundaries,
        )

        self.assertEqual(result.entry_reason, AdultEntryReason.CODEX_ADULT_HANDOFF)
        self.assertEqual(result.adult_handoff, canonical_json(plan))
        self.assertIn("hana_boundary_response", result.adult_handoff or "")
        self.assertIn("Private unease remains present.", result.adult_handoff or "")
        self.assertIn("adult_boundary_stop", result.adult_handoff or "")
        self.assertEqual(result.current_context[:-1], _facts())
        self.assertEqual(result.current_context[-1].evidence_ref, "source:current")
        self.assertIn(
            text_sha256(_turn().exact_user_source),
            result.current_context[-1].authoritative_fact,
        )
        self.assertEqual(result.hard_boundaries, boundaries)
        self.assertEqual(result.autonomy_mode, "both")
        self.assertEqual(result.depth_mode, "medium")
        self.assertEqual(result.craft_mode, AdultCraftMode.EX)
        self.assertIn("buildup", result.craft_concept_keys)
        self.assertIn("rub", result.craft_keyword_keys)

    def test_adult_continuation_bypasses_codex_and_separates_protected_bytes(self) -> None:
        result = self.builder.from_accepted_adult_continuation(
            turn_input=_turn(craft_mode="on"),
            accepted_safe_projection="A private interaction remains in progress.",
            protected_adult_continuity=PROTECTED_A,
            current_facts=_facts(),
            product_story_boundaries=("Preserve the locked-room story state.",),
        )

        self.assertEqual(
            result.entry_reason,
            AdultEntryReason.ACCEPTED_ADULT_CONTINUATION,
        )
        self.assertIsNone(result.adult_handoff)
        self.assertEqual(result.accepted_protected_continuity, PROTECTED_A)
        safe_surface = canonical_json(replace(result, accepted_protected_continuity=None))
        self.assertNotIn(PROTECTED_A, safe_surface)
        self.assertNotIn(PROTECTED_A, " ".join(result.craft_concept_keys))
        self.assertNotIn(PROTECTED_A, " ".join(result.craft_keyword_keys))

    def test_protected_continuity_change_has_no_safe_or_query_effect(self) -> None:
        first = self.builder.from_accepted_adult_continuation(
            turn_input=_turn(craft_mode="ex"),
            accepted_safe_projection="The safe projection mentions an aftermath.",
            protected_adult_continuity=PROTECTED_A,
            current_facts=_facts(),
            product_story_boundaries=("Keep the current scene location.",),
        )
        second = self.builder.from_accepted_adult_continuation(
            turn_input=_turn(craft_mode="ex"),
            accepted_safe_projection="The safe projection mentions an aftermath.",
            protected_adult_continuity=PROTECTED_B,
            current_facts=_facts(),
            product_story_boundaries=("Keep the current scene location.",),
        )

        self.assertEqual(
            replace(first, accepted_protected_continuity=None),
            replace(second, accepted_protected_continuity=None),
        )
        self.assertNotEqual(
            first.accepted_protected_continuity,
            second.accepted_protected_continuity,
        )

    def test_off_mode_has_no_query_keys_and_cannot_change_route(self) -> None:
        off = self.builder.from_accepted_adult_continuation(
            turn_input=_turn(craft_mode="off"),
            accepted_safe_projection="A safe accepted projection.",
            protected_adult_continuity=PROTECTED_A,
            current_facts=_facts(),
            product_story_boundaries=(),
        )
        on = self.builder.from_accepted_adult_continuation(
            turn_input=_turn(craft_mode="on"),
            accepted_safe_projection="A safe accepted projection.",
            protected_adult_continuity=PROTECTED_A,
            current_facts=_facts(),
            product_story_boundaries=(),
        )

        self.assertEqual(off.craft_mode, AdultCraftMode.OFF)
        self.assertEqual(off.craft_concept_keys, ())
        self.assertEqual(off.craft_keyword_keys, ())
        self.assertEqual(off.entry_reason, on.entry_reason)
        self.assertEqual(off.adult_handoff, on.adult_handoff)

    def test_legacy_controls_default_craft_off_without_changing_controls(self) -> None:
        result = self.builder.from_accepted_adult_continuation(
            turn_input=_turn(controls_version=1),
            accepted_safe_projection="A safe accepted projection.",
            protected_adult_continuity=PROTECTED_A,
            current_facts=_facts(),
            product_story_boundaries=(),
        )
        self.assertEqual(result.autonomy_mode, "mind")
        self.assertEqual(result.depth_mode, "short")
        self.assertEqual(result.craft_mode, AdultCraftMode.OFF)
        self.assertEqual(result.craft_concept_keys, ())
        self.assertEqual(result.craft_keyword_keys, ())

    def test_private_facts_retain_character_ownership_and_order(self) -> None:
        facts = _facts()
        result = self.builder.from_cognition_handoff(
            turn_input=_turn(),
            cognition_plan=_plan(),
            accepted_safe_projection="The accepted public scene remains current.",
            protected_adult_continuity=None,
            current_facts=facts,
            product_story_boundaries=(),
        )
        self.assertEqual(result.current_context[:-1], facts)
        private = result.current_context[1]
        self.assertEqual(private.subject_id, HANA)
        self.assertEqual(private.visibility, "adult_role_private")

        invalid = replace(private, subject_id="world:test")
        with self.assertRaisesRegex(ContractValidationError, "character owner"):
            self.builder.from_cognition_handoff(
                turn_input=_turn(),
                cognition_plan=_plan(),
                accepted_safe_projection="The accepted public scene remains current.",
                protected_adult_continuity=None,
                current_facts=(facts[0], invalid),
                product_story_boundaries=(),
            )

    def test_exact_protected_continuity_cannot_escape_dedicated_field(self) -> None:
        with self.assertRaisesRegex(ContractValidationError, "dedicated field"):
            self.builder.from_accepted_adult_continuation(
                turn_input=_turn(),
                accepted_safe_projection=f"Safe projection leaked {PROTECTED_A}",
                protected_adult_continuity=PROTECTED_A,
                current_facts=_facts(),
                product_story_boundaries=(),
            )

        leaking_fact = replace(_facts()[1], authoritative_fact=PROTECTED_A)
        with self.assertRaisesRegex(ContractValidationError, "dedicated field"):
            self.builder.from_accepted_adult_continuation(
                turn_input=_turn(),
                accepted_safe_projection="A safe accepted projection.",
                protected_adult_continuity=PROTECTED_A,
                current_facts=(_facts()[0], leaking_fact),
                product_story_boundaries=(),
            )

    def test_hard_boundaries_are_exact_product_story_inputs_only(self) -> None:
        boundaries = (
            "Preserve the accepted room location.",
            "Stop at the next meaningful user choice.",
        )
        result = self.builder.from_accepted_adult_continuation(
            turn_input=_turn(),
            accepted_safe_projection="A safe accepted projection.",
            protected_adult_continuity=PROTECTED_A,
            current_facts=_facts(),
            product_story_boundaries=boundaries,
        )
        self.assertEqual(result.hard_boundaries, boundaries)

    def test_cognition_entry_rejects_missing_or_wrong_route_transition(self) -> None:
        with self.assertRaisesRegex(ContractValidationError, "requires a route transition"):
            self.builder.from_cognition_handoff(
                turn_input=_turn(),
                cognition_plan=replace(_plan(), route_transition=None),
                accepted_safe_projection="A safe accepted projection.",
                protected_adult_continuity=None,
                current_facts=_facts(),
                product_story_boundaries=(),
            )

        transition = _plan().route_transition
        assert transition is not None
        wrong = replace(
            transition,
            from_route=LogicRoute.ADULT,
            to_route=LogicRoute.ORDINARY,
        )
        with self.assertRaisesRegex(ContractValidationError, "ordinary to adult"):
            self.builder.from_cognition_handoff(
                turn_input=_turn(),
                cognition_plan=replace(_plan(), route_transition=wrong),
                accepted_safe_projection="A safe accepted projection.",
                protected_adult_continuity=None,
                current_facts=_facts(),
                product_story_boundaries=(),
            )

    def test_cognition_handoff_rejects_sequence_after_declared_boundary(self) -> None:
        plan = _plan()
        transition = plan.route_transition
        assert transition is not None
        moved_boundary = replace(
            transition,
            boundary_item_key="hana_reaches_boundary",
        )
        with self.assertRaisesRegex(ContractValidationError, "after its route boundary"):
            self.builder.from_cognition_handoff(
                turn_input=_turn(),
                cognition_plan=replace(plan, route_transition=moved_boundary),
                accepted_safe_projection="A safe accepted projection.",
                protected_adult_continuity=None,
                current_facts=_facts(),
                product_story_boundaries=(),
            )

    def test_catalog_queries_are_deterministic_and_bounded(self) -> None:
        turn = replace(
            _turn(),
            exact_user_source=(
                "anatomy clitoris anus penis semen lubrication action vocalization "
                "sound effect bodily fluids squirting urine feces saliva tears cervix "
                "womb material continuity buildup aftermath degradation corruption "
                "reproductive climax bdsm objectification body modification"
            ),
        )
        first = self.builder.from_accepted_adult_continuation(
            turn_input=turn,
            accepted_safe_projection="A safe accepted projection.",
            protected_adult_continuity=PROTECTED_A,
            current_facts=_facts(),
            product_story_boundaries=(),
        )
        second = self.builder.from_accepted_adult_continuation(
            turn_input=turn,
            accepted_safe_projection="A safe accepted projection.",
            protected_adult_continuity=PROTECTED_A,
            current_facts=_facts(),
            product_story_boundaries=(),
        )
        self.assertEqual(first.craft_concept_keys, second.craft_concept_keys)
        self.assertEqual(first.craft_keyword_keys, second.craft_keyword_keys)
        self.assertLessEqual(len(first.craft_concept_keys), 16)
        self.assertLessEqual(len(first.craft_keyword_keys), 16)

    def test_direct_builder_factory_uses_verified_catalog(self) -> None:
        builder = AdultTurnPreparationBuilder.from_catalog_root(CATALOG_ROOT)
        result = builder.from_cognition_handoff(
            turn_input=_turn(craft_mode="on"),
            cognition_plan=_plan(),
            accepted_safe_projection="A safe accepted projection.",
            protected_adult_continuity=None,
            current_facts=_facts(),
            product_story_boundaries=(),
        )
        self.assertEqual(result.entry_reason, AdultEntryReason.CODEX_ADULT_HANDOFF)


if __name__ == "__main__":
    unittest.main()
