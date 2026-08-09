from __future__ import annotations

import unittest

from cera.pi_scene.creator_trace import cognition_creator_trace
from cera.serialization import canonical_json, to_primitive

from .test_cognition_contracts import _plan


class PiSceneCreatorTraceTests(unittest.TestCase):
    def test_trace_exposes_explicit_decisions_without_hidden_reasoning(self) -> None:
        plan = _plan()
        trace = cognition_creator_trace(canonical_json(to_primitive(plan)))

        self.assertEqual(trace["logic_owner"], "codex_cognition")
        self.assertEqual(
            trace["decision_records"][0]["decision_key"],
            plan.decision_records[0].decision_key,
        )
        self.assertEqual(
            trace["autonomy_application"][0]["owner_id"],
            plan.decision_records[0].owner_id,
        )
        self.assertNotIn("chain_of_thought", trace)


if __name__ == "__main__":
    unittest.main()
