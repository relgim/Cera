from __future__ import annotations

import json
import unittest

from cera.errors import ContractValidationError
from cera.pi_scene.codex_planner import _accepted_evidence
from cera.serialization import canonical_json, canonical_sha256, text_sha256


class PiSceneCodexPlannerPrivacyTests(unittest.TestCase):
    def test_ordinary_source_is_hash_only_bounded_evidence(self) -> None:
        exact_source = "Continue the accepted scene. " * 200
        records = (
            {
                "receipt": {
                    "accepted_turn_id": "accepted-ordinary-0001",
                    "generation": 1,
                    "route": "ordinary",
                    "exact_user_source": exact_source,
                    "exact_accepted_prose": "Accepted prose.",
                    "primary_authority_json": "{}",
                },
                "ordinary_record": {"summary": "The accepted scene remains available."},
            },
        )

        evidence = _accepted_evidence(records)

        self.assertEqual(len(evidence), 1)
        self.assertNotIn(exact_source, evidence[0].exact_content)
        self.assertIn(text_sha256(exact_source), evidence[0].exact_content)
        self.assertLess(len(evidence[0].exact_content), 4_000)

    def test_hash_only_adult_receipt_is_valid_codex_evidence(self) -> None:
        protected_source = "PROTECTED ADULT SOURCE"
        protected_prose = "PROTECTED ADULT PROSE"
        protected_authority = "PROTECTED ADULT AUTHORITY"
        records = (
            {
                "receipt": {
                    "accepted_turn_id": "accepted-adult-0001",
                    "generation": 1,
                    "route": "adult",
                    "exact_user_source_sha256": text_sha256(protected_source),
                    "exact_accepted_prose_sha256": text_sha256(protected_prose),
                    "primary_authority_sha256": text_sha256(protected_authority),
                },
                "adult_projection": {
                    "items": [
                        {
                            "event_key": "event-1",
                            "non_explicit_summary": "A lasting relationship boundary changed.",
                            "lasting_story_meaning": "The change remains relevant.",
                        }
                    ]
                },
            },
        )

        evidence = _accepted_evidence(records)

        self.assertEqual(len(evidence), 1)
        self.assertIn("adult_projection", evidence[0].exact_content)
        for protected in (protected_source, protected_prose, protected_authority):
            self.assertNotIn(protected, evidence[0].exact_content)

    def test_oversized_ordinary_record_is_split_into_bounded_readable_evidence(self) -> None:
        exact_source = "Continue from the accepted scene."
        ordinary_record = {
            "schema_version": "cera.pi_scene.ordinary_record.v1",
            "primary_sequence_sha256": "1" * 64,
            "resulting_public_state": "Sakura remains near the radio console.",
            "secondary_canon": ["observatory detail " * 320],
            "unresolved_threads": ["The false distress signal remains unresolved."],
        }
        records = (
            {
                "receipt": {
                    "accepted_turn_id": "accepted-ordinary-0001",
                    "generation": 1,
                    "route": "ordinary",
                    "exact_user_source": exact_source,
                    "exact_accepted_prose": "Accepted prose.",
                    "primary_authority_json": "{}",
                },
                "ordinary_record": ordinary_record,
            },
        )

        evidence = _accepted_evidence(records)

        self.assertGreater(len(evidence), 2)
        self.assertTrue(all(len(value.exact_content) <= 4_000 for value in evidence))
        index = json.loads(evidence[0].exact_content)
        self.assertEqual(index["projection_kind"], "ordinary_record")
        self.assertEqual(index["projection_sha256"], canonical_sha256(ordinary_record))
        self.assertEqual(index["projection_part_count"], len(evidence) - 1)
        self.assertEqual(
            index["receipt"]["exact_user_source_sha256"],
            text_sha256(exact_source),
        )
        fragments = [
            json.loads(value.exact_content)
            for value in evidence[1:]
            if "canonical_value_fragment" in json.loads(value.exact_content)
        ]
        self.assertEqual(
            "".join(value["canonical_value_fragment"] for value in fragments),
            canonical_json(ordinary_record["secondary_canon"]),
        )

    def test_full_text_and_hash_mismatch_is_rejected(self) -> None:
        records = (
            {
                "receipt": {
                    "accepted_turn_id": "accepted-ordinary-0001",
                    "generation": 1,
                    "route": "ordinary",
                    "exact_accepted_prose": "accepted prose",
                    "exact_accepted_prose_sha256": "0" * 64,
                    "primary_authority_json": "{}",
                    "primary_authority_sha256": text_sha256("{}"),
                }
            },
        )

        with self.assertRaisesRegex(ContractValidationError, "hash changed"):
            _accepted_evidence(records)


if __name__ == "__main__":
    unittest.main()
