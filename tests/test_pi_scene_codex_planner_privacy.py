from __future__ import annotations

import unittest

from cera.errors import ContractValidationError
from cera.pi_scene.codex_planner import _accepted_evidence
from cera.serialization import text_sha256


class PiSceneCodexPlannerPrivacyTests(unittest.TestCase):
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
