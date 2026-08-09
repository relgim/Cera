from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from cera.pi_scene.review_store import LeanReviewState
from cera.semantic_validation import SemanticVerdict

from .test_pi_scene_lean_v1 import turn
from .test_pi_scene_semantic_runtime import _runtime, _SemanticValidator


class PiScenePostAcceptTransitionTests(unittest.TestCase):
    def test_trailing_predecessor_snapshot_failure_cannot_hide_accepted_successor(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            coordinator, store, _ = _runtime(
                Path(temporary),
                _SemanticValidator(
                    SemanticVerdict.REJECT,
                    SemanticVerdict.REJECT,
                    SemanticVerdict.PASS,
                ),
            )
            rejected = coordinator.start_ordinary(turn())
            self.assertEqual(rejected.state, LeanReviewState.REVIEW_READY)

            persist = coordinator._persist_review_state

            def fail_only_after_successor_accept() -> None:
                if any(
                    value.result.successor is not None
                    and value.result.successor.accepted_receipt is not None
                    for value in coordinator._decisions.values()
                ):
                    raise OSError("injected trailing predecessor snapshot failure")
                persist()

            coordinator._persist_review_state = fail_only_after_successor_accept
            decision = coordinator.regenerate(rejected.review_id)

            assert decision.successor is not None
            self.assertEqual(decision.successor.state, LeanReviewState.ACCEPTED)
            self.assertEqual(
                decision.operational_warnings,
                ("review_state_cleanup_pending",),
            )
            self.assertEqual(
                store.load_head(
                    world_id=decision.successor.candidate.world_id,
                    branch_id=decision.successor.candidate.branch_id,
                ).accepted_turn_id,
                decision.successor.candidate.turn_id,
            )


if __name__ == "__main__":
    unittest.main()
