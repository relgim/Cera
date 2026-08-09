from __future__ import annotations

import unittest
from dataclasses import replace

from cera.adult_pipeline import (
    AdultCraftExcerptV1,
    AdultCraftMode,
    AdultCraftQueryV1,
    AdultCraftSelectionV1,
    AdultEntryReason,
    AdultNextRoute,
    AdultProjectionEffectV1,
    AdultProjectionPresenceChangeV1,
    AdultProviderReceiptV1,
    AdultProviderRole,
    AdultRouteTransitionV1,
    AdultSceneOutputV1,
    AdultSessionScope,
    retrieve_bounded_adult_craft,
)
from cera.errors import ContractValidationError
from cera.serialization import canonical_sha256, text_sha256
from tests.test_adult_pipeline_runtime import pass_decision, scene_output, scene_request


class AdultPipelineContractTests(unittest.TestCase):
    def test_craft_modes_have_bounded_non_route_semantics(self) -> None:
        off = scene_request(craft_mode=AdultCraftMode.OFF).retrieved_craft
        on = scene_request(craft_mode=AdultCraftMode.ON).retrieved_craft
        ex = scene_request(craft_mode=AdultCraftMode.EX).retrieved_craft

        self.assertEqual(off.excerpts, ())
        self.assertEqual(off.covered_axes, ())
        self.assertTrue({"direct_vocabulary", "clarity"}.issubset(on.covered_axes))
        self.assertTrue(
            {
                "buildup",
                "physiology",
                "continuity",
                "sound",
                "climax",
                "aftermath",
            }.issubset(ex.covered_axes)
        )
        for selection in (off, on, ex):
            self.assertNotIn("route", selection.__dataclass_fields__)

    def test_craft_retrieval_binds_exact_query_and_mode(self) -> None:
        query = AdultCraftQueryV1(
            schema_version=AdultCraftQueryV1.SCHEMA_VERSION,
            mode=AdultCraftMode.ON,
            concept_keys=("pacing",),
            keyword_keys=("scene",),
        )
        selection = AdultCraftSelectionV1(
            schema_version=AdultCraftSelectionV1.SCHEMA_VERSION,
            mode=AdultCraftMode.ON,
            query_sha256=canonical_sha256(query),
            covered_axes=("direct_vocabulary", "clarity"),
            excerpts=(
                AdultCraftExcerptV1(
                    craft_ref="craft:pacing",
                    concept_keys=("pacing",),
                    excerpt="Use direct vocabulary and clear staging.",
                ),
            ),
        )

        class Retrieval:
            def retrieve_adult_craft(self, request):  # type: ignore[no-untyped-def]
                self.request = request
                return selection

        self.assertEqual(retrieve_bounded_adult_craft(query, Retrieval()), selection)

        class DriftedRetrieval:
            def retrieve_adult_craft(self, request):  # type: ignore[no-untyped-def]
                return replace(selection, query_sha256=text_sha256("another query"))

        with self.assertRaisesRegex(ContractValidationError, "another query"):
            retrieve_bounded_adult_craft(query, DriftedRetrieval())

    def test_codex_handoff_entry_requires_handoff_and_other_entries_forbid_it(self) -> None:
        with self.assertRaisesRegex(ContractValidationError, "adult_handoff"):
            replace(scene_request(), adult_handoff=None)
        with self.assertRaisesRegex(ContractValidationError, "only a Codex handoff"):
            replace(
                scene_request(entry_reason=AdultEntryReason.ACCEPTED_ADULT_CONTINUATION),
                adult_handoff="unexpected",
            )
        with self.assertRaisesRegex(ContractValidationError, "protected_continuity"):
            replace(
                scene_request(entry_reason=AdultEntryReason.ACCEPTED_ADULT_CONTINUATION),
                accepted_protected_continuity=None,
            )

    def test_scene_output_requires_single_named_logic_owner(self) -> None:
        with self.assertRaisesRegex(ContractValidationError, "logic owner"):
            replace(scene_output(), logic_owner="codex")

    def test_return_to_codex_must_match_route(self) -> None:
        output = scene_output(next_route=AdultNextRoute.ADULT)
        with self.assertRaisesRegex(ContractValidationError, "return_to_codex"):
            AdultRouteTransitionV1(
                schema_version=AdultRouteTransitionV1.SCHEMA_VERSION,
                current_route=AdultNextRoute.ADULT,
                next_route=output.next_route,
                return_to_codex=True,
                concise_reason=output.next_route_reason,
            )

    def test_v2_presence_and_durable_effects_are_event_scoped(self) -> None:
        decision = pass_decision(scene_output())
        assert decision.passed is not None
        with self.assertRaisesRegex(ContractValidationError, "unknown event"):
            replace(
                decision.passed.codex_projection,
                presence_changes=(
                    AdultProjectionPresenceChangeV1(
                        character_id="character:hana",
                        direction="leave",
                        effective_after_event_key="unknown_event",
                    ),
                ),
            )
        with self.assertRaisesRegex(ContractValidationError, "unknown event"):
            replace(
                decision.passed.codex_projection,
                durable_effects=(
                    AdultProjectionEffectV1(
                        effect_key="unknown_effect",
                        effect_kind="material",
                        source_event_key="unknown_event",
                        subject_ids=("character:hana",),
                        non_explicit_effect="A durable condition changes.",
                        target_key="state:hana_condition",
                        visibility="public",
                        knowledge_owner_id=None,
                    ),
                ),
            )

    def test_filter_receipt_must_be_candidate_scoped_and_terminalized(self) -> None:
        decision = pass_decision(scene_output())
        base = AdultProviderReceiptV1(
            schema_version=AdultProviderReceiptV1.SCHEMA_VERSION,
            role=AdultProviderRole.FILTER,
            session_scope=AdultSessionScope.CANDIDATE,
            provider="fake",
            model="fake",
            session_id_sha256=text_sha256("filter"),
            request_sha256=text_sha256("request"),
            output_sha256=canonical_sha256(decision),
            provider_operations=1,
            finish_status="complete",
            session_terminalized=True,
        )
        with self.assertRaisesRegex(ContractValidationError, "must be terminalized"):
            replace(base, session_terminalized=False)
        with self.assertRaisesRegex(ContractValidationError, "candidate session scope"):
            replace(base, session_scope=AdultSessionScope.ACCEPTED_BRANCH)

    def test_filter_output_is_not_a_scene_output_contract(self) -> None:
        # A simple structural guard against reintroducing a second logic owner.
        self.assertNotIn("logic_owner", AdultProviderReceiptV1.__dataclass_fields__)
        self.assertNotIn("exact_story_prose", AdultProjectionEffectV1.__dataclass_fields__)
        self.assertIn("logic_owner", AdultSceneOutputV1.__dataclass_fields__)


if __name__ == "__main__":
    unittest.main()
