from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from cera.errors import StateConflictError
from cera.pi_scene.runtime import PlannerTurnOutputV1
from cera.semantic_validation import SemanticVerdict

from .test_pi_scene_lean_v1 import turn
from .test_pi_scene_semantic_runtime import _runtime, _SemanticValidator


class PiSceneBoundLogicOwnerTests(unittest.TestCase):
    def test_bound_plan_realizes_without_a_second_planner_call(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path(r"D:\Cera\tmp")) as temporary:
            coordinator, _, planner = _runtime(
                Path(temporary),
                _SemanticValidator(SemanticVerdict.PASS),
            )
            exact_turn = turn()
            planned = coordinator.plan_ordinary_logic(exact_turn)
            accepted = coordinator.start_ordinary_from_plan(exact_turn, planned)

            self.assertIsNotNone(accepted.accepted_receipt)
            self.assertEqual(planner.calls, 1)

    def test_bound_plan_rejects_another_turn_or_accepted_head(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path(r"D:\Cera\tmp")) as temporary:
            coordinator, _, _ = _runtime(
                Path(temporary),
                _SemanticValidator(SemanticVerdict.PASS),
            )
            exact_turn = turn()
            planned = coordinator.plan_ordinary_logic(exact_turn)

            with self.assertRaisesRegex(StateConflictError, "another turn or head"):
                coordinator.start_ordinary_from_plan(
                    replace(exact_turn, exact_user_source="A different source."),
                    planned,
                )

    def test_adult_handoff_cannot_be_sent_to_the_ordinary_writer(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path(r"D:\Cera\tmp")) as temporary:
            coordinator, _, _ = _runtime(
                Path(temporary),
                _SemanticValidator(SemanticVerdict.PASS),
            )
            exact_turn = turn()
            planned = coordinator.plan_ordinary_logic(exact_turn)
            bundle = dict(planned.output.decision_bundle or {})
            bundle["route_transition"] = {
                "from_route": "ordinary",
                "to_route": "adult",
                "boundary_item_key": "natural_stop",
                "non_graphic_handoff_summary": "The adult logic owner takes over.",
                "character_effect_refs": [],
                "return_condition": "Return after the adult sequence ends.",
            }
            handoff = replace(
                planned,
                output=PlannerTurnOutputV1(
                    sequence=planned.output.sequence,
                    decision_bundle=bundle,
                    provider_operations=planned.output.provider_operations,
                    validation_evidence=planned.output.validation_evidence,
                ),
            )

            with self.assertRaisesRegex(StateConflictError, "adult logic-owner handoff"):
                coordinator.start_ordinary_from_plan(exact_turn, handoff)


if __name__ == "__main__":
    unittest.main()
