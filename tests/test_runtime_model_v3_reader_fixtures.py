from __future__ import annotations

import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = ROOT / "tests" / "fixtures" / "runtime_model_v3_reader_cases_v2.json"
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
            "cera.runtime_model_v3_reader_qualification_fixture_set.v1",
        )
        self.assertEqual(
            self.fixture["fixture_set_id"],
            "runtime_model_v3_reader_cases_v2",
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
            self.assertEqual(
                set(value),
                {
                    "case_id",
                    "story_text",
                    "plan_direction",
                    "expected_verdict",
                    "authority_class",
                },
            )
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

    def test_historical_failed_fixture_source_is_not_rewritten(self) -> None:
        historical = HISTORICAL_RUNNER_PATH.read_text(encoding="utf-8")
        self.assertIn('"strong_complete",', historical)
        self.assertIn("but I am glad you asked.", historical)
        self.assertNotIn("strong_complete_npc_only", historical)


if __name__ == "__main__":
    unittest.main()
