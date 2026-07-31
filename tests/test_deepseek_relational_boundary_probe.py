from __future__ import annotations

from pathlib import Path
import runpy
import sys
import unittest

from cera.composer import RealizationKind
from cera.realization import RealizationBoundaryCheck


ROOT = Path(__file__).resolve().parents[1]


class DeepSeekRelationalBoundaryProbeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        scripts = str(ROOT / "scripts")
        sys.path.insert(0, scripts)
        try:
            cls.namespace = runpy.run_path(
                str(
                    ROOT
                    / "scripts"
                    / "run_deepseek_relational_boundary_probe.py"
                )
            )
        finally:
            sys.path.remove(scripts)
        cls.build_packet = staticmethod(cls.namespace["build_probe_packet"])
        cls.build_request = staticmethod(
            cls.namespace["build_verification_request"]
        )

    def test_packet_uses_active_prompt_and_one_way_npc_decision(self) -> None:
        packet = self.build_packet()
        dto = packet["composition_dto"]
        self.assertEqual(
            packet["prompt_version"],
            "cera.deepseek_scene_composer_prompt.v26",
        )
        self.assertEqual(
            self.namespace["DeepSeekCompositionDraftV6"].SCHEMA_VERSION,
            "cera.deepseek_composition_draft.v6",
        )
        self.assertTrue(dto["creator_event_coverage_required"])
        self.assertEqual(dto["specificity_contract"], None)
        self.assertEqual(len(dto["identity"]["selected_npc_ids"]), 1)
        self.assertTrue(
            any(
                "mutual or reciprocal interaction" in value
                for value in dto["decision"]["prohibited_inferences"]
            )
        )
        self.assertIn("output_schema", packet)
        self.assertEqual(
            packet["output_obligations"]["source_coverage"]["mode"],
            "exact_sequence",
        )
        self.assertEqual(
            len(packet["output_obligations"]["source_coverage"]["values"]),
            2,
        )
        self.assertEqual(
            packet["output_obligations"]["specificity_coverage"]["mode"],
            "must_be_empty",
        )
        self.assertEqual(
            packet["output_obligations"]["specificity_coverage"]["values"],
            [],
        )
        self.assertTrue(
            packet["output_obligations"]["source_coverage"][
                "each_returned_entry_requires_one_or_more_existing_segment_keys"
            ]
        )
        self.assertEqual(
            packet["output_obligations"]["obligation_sha256"],
            self.namespace["build_probe_obligations"]().obligation_sha256,
        )
        source_schema = packet["output_schema"]["properties"][
            "source_coverage"
        ]
        self.assertEqual(source_schema["minItems"], 2)
        self.assertEqual(source_schema["maxItems"], 2)
        self.assertEqual(
            len(
                packet["output_obligations"][
                    "required_beat_authority_owner_associations"
                ]
            ),
            1,
        )

    def test_verification_request_keeps_exact_user_claims_bounded(self) -> None:
        packet = self.build_packet()
        request = self.build_request(
            "Sakura remains at the doorway. She asks who authorized the visit."
        )
        self.assertEqual(len(request.expected_beats), 1)
        self.assertEqual(len(request.selected_participant_ids), 1)
        self.assertEqual(len(request.protected_user_authorities), 2)
        action_authority, dialogue_authority = (
            request.protected_user_authorities
        )
        self.assertEqual(
            action_authority.allowed_kinds,
            (RealizationKind.ACTION,),
        )
        self.assertEqual(
            dialogue_authority.allowed_kinds,
            (RealizationKind.DIALOGUE, RealizationKind.ACTION),
        )
        self.assertEqual(
            sum(
                len(value.claims)
                for value in request.protected_user_authorities
            ),
            3,
        )
        self.assertEqual(
            request.required_boundary_checks,
            (
                RealizationBoundaryCheck
                .PROTECTED_USER_NO_UNSUPPLIED_REALIZATION,
            ),
        )
        self.assertIn(
            "relational or reciprocal wording",
            request.hard_boundaries[0],
        )
        self.assertNotIn("gaze", packet["composition_dto"]["hard_boundaries"][0])


if __name__ == "__main__":
    unittest.main()
