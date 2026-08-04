from __future__ import annotations

import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
FAILED_FIXTURE_V2_PATH = (
    ROOT / "tests" / "fixtures" / "runtime_model_v3_reader_cases_v2.json"
)
FAILED_FIXTURE_V3_PATH = (
    ROOT / "tests" / "fixtures" / "runtime_model_v3_reader_cases_v3.json"
)
FIXTURE_PATH = ROOT / "tests" / "fixtures" / "runtime_model_v3_reader_cases_v4.json"
HISTORICAL_RUNNER_PATH = (
    ROOT
    / ".chatgpt"
    / "operations"
    / "provider-campaigns"
    / "2026-08-03-cera-runtime-model-v3-live-qualification-v1"
    / "run_stage4.py"
)


class RuntimeModelV3ReaderFixtureAuthorityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

    def test_fixture_set_is_closed_and_authority_explicit(self) -> None:
        self.assertEqual(
            self.fixture["schema_version"],
            "cera.runtime_model_v3_reader_qualification_fixture_set.v3",
        )
        self.assertEqual(
            self.fixture["fixture_set_id"],
            "runtime_model_v3_reader_cases_v4",
        )
        self.assertEqual(
            set(self.fixture),
            {"schema_version", "fixture_set_id", "authority_context", "cases"},
        )
        authority = self.fixture["authority_context"]
        self.assertEqual(authority["accepted_context"], [])
        self.assertEqual(authority["protected_user_source_claims"], [])
        self.assertEqual(len(authority["hard_constraints"]), 2)

        cases = self.fixture["cases"]
        self.assertEqual(len(cases), 4)
        self.assertEqual(
            [value["case_id"] for value in cases],
            [
                "strong_complete_npc_only",
                "skipped_buildup_premature_closure",
                "repetitive_mechanical",
                "unrealistic_reaction",
            ],
        )
        self.assertEqual(
            len({value["case_id"] for value in cases}),
            len(cases),
        )
        for value in cases:
            required = {
                "case_id",
                "story_text",
                "plan_direction",
                "expected_verdict",
                "authority_class",
            }
            self.assertTrue(required <= set(value))
            self.assertTrue(set(value) <= required | {"planner_beats"})
            self.assertIn(value["expected_verdict"], {"accepted", "rejected"})
            self.assertTrue(value["story_text"].strip())
            self.assertTrue(value["plan_direction"].strip())

    def test_positive_case_contains_no_unsupplied_protected_user_assertion(self) -> None:
        positive = self.fixture["cases"][0]
        self.assertEqual(positive["expected_verdict"], "accepted")
        self.assertEqual(positive["authority_class"], "npc_only_no_prior_context")
        story = positive["story_text"]
        self.assertNotIn("Ted", story)
        self.assertNotIn("you asked", story.casefold())
        self.assertNotIn("you said", story.casefold())
        self.assertNotIn("you did", story.casefold())
        self.assertIn("without closing the conversation", story)
        self.assertNotIn("tired", story.casefold())
        self.assertNotIn("genuine", story.casefold())

    def test_positive_case_has_gap_ordered_exclusive_role_plan(self) -> None:
        positive = self.fixture["cases"][0]
        beats = positive["planner_beats"]
        self.assertEqual(
            [value["beat_key"] for value in beats],
            [
                "hana_finishes_cup_action",
                "hana_comments_on_evening",
                "hana_leaves_open_stop",
            ],
        )
        role_fields = {
            "action_owner_ids",
            "state_owner_ids",
            "speaker_ids",
            "affected_ids",
            "addressed_ids",
            "observing_ids",
            "referenced_ids",
        }
        self.assertEqual(len(beats), 3)
        for beat in beats:
            self.assertEqual(
                set(beat), {"beat_key", "observable_direction", "roles"}
            )
            self.assertEqual(set(beat["roles"]), role_fields)
            memberships = sum(
                character_id == "character:hana_hanezawa"
                for values in beat["roles"].values()
                for character_id in values
            )
            self.assertEqual(memberships, 1)
        self.assertEqual(
            [
                bool(value["roles"]["speaker_ids"])
                for value in beats
            ],
            [False, True, False],
        )

    def test_failed_v2_and_v3_fixtures_are_preserved(self) -> None:
        failed_v2 = json.loads(FAILED_FIXTURE_V2_PATH.read_text(encoding="utf-8"))
        failed_v3 = json.loads(FAILED_FIXTURE_V3_PATH.read_text(encoding="utf-8"))
        self.assertEqual(failed_v2["fixture_set_id"], "runtime_model_v3_reader_cases_v2")
        self.assertNotIn("planner_beats", failed_v2["cases"][0])
        self.assertEqual(failed_v3["fixture_set_id"], "runtime_model_v3_reader_cases_v3")
        self.assertIn("planner_beats", failed_v3["cases"][0])
        self.assertIn("tired but genuine smile", failed_v3["cases"][0]["story_text"])
        self.assertIn("small smile", self.fixture["cases"][0]["story_text"])

    def test_historical_failed_fixture_source_is_not_rewritten(self) -> None:
        historical = HISTORICAL_RUNNER_PATH.read_text(encoding="utf-8")
        self.assertIn('"strong_complete",', historical)
        self.assertIn("but I am glad you asked.", historical)
        self.assertNotIn("strong_complete_npc_only", historical)


if __name__ == "__main__":
    unittest.main()
