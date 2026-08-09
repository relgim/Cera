from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from cera.pi_scene.contracts import SceneRoute
from cera.pi_scene.runtime import PlannerTurnOutputV1
from cera.pi_scene.writer_view import WriterViewInputV1, WriterViewMaterializer
from cera.serialization import to_primitive

from .test_cognition_contracts import _plan


class PiSceneCognitionAuthorityTests(unittest.TestCase):
    def test_planner_output_binds_decisions_to_exact_sequence(self) -> None:
        plan = to_primitive(_plan())
        output = PlannerTurnOutputV1(
            sequence=plan["sequence"],
            decision_bundle=plan,
            provider_operations=1,
        )
        self.assertEqual(output.decision_bundle["sequence"], output.sequence)
        changed = dict(plan)
        changed["sequence"] = {"changed": True}
        with self.assertRaisesRegex(Exception, "changed its exact sequence"):
            PlannerTurnOutputV1(
                sequence=plan["sequence"],
                decision_bundle=changed,
                provider_operations=1,
            )

    def test_writer_gets_compact_decisions_and_projected_sequence(self) -> None:
        plan = to_primitive(_plan())
        with tempfile.TemporaryDirectory() as temporary:
            view = WriterViewMaterializer(Path(temporary)).materialize(
                WriterViewInputV1(
                    world_id="world:test",
                    branch_id="branch:main",
                    scene_id="scene:door",
                    turn_id="turn:one",
                    candidate_id="candidate:one",
                    route=SceneRoute.ORDINARY,
                    user_prompt="Ted asks Sakura to open the door.",
                    primary_authority=plan,
                    current_state={"public_scene_state": "The door is closed."},
                    characters={},
                    relationships={},
                    recent_prose=(),
                    relevant_memories={},
                    voice_examples={},
                    craft_index={},
                    accepted_records=(),
                )
            )
            decision = json.loads((view.root / "DECISION_BUNDLE.json").read_text(encoding="utf-8"))
            response = json.loads(
                (view.root / "RESPONSE_SEQUENCE.json").read_text(encoding="utf-8")
            )
            authority = json.loads(
                (view.root / "zz_CURRENT_TURN_AUTHORITY.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                decision["decision_records"][0]["decision_key"],
                "sakura_door_response",
            )
            self.assertNotIn("observer_frame", decision["decision_records"][0])
            self.assertEqual(
                response["surface_realization_items"][0]["item_key"],
                "sakura_checks_door",
            )
            self.assertEqual(
                authority["decision_context_path"],
                "DECISION_BUNDLE.json",
            )


if __name__ == "__main__":
    unittest.main()
