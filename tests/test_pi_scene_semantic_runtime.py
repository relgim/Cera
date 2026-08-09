from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from cera.pi_scene.review_store import LeanReviewState
from cera.pi_scene.runtime import LeanPiSceneCoordinator, PlannerTurnOutputV1
from cera.pi_scene.store import LeanSceneStore
from cera.pi_scene.writer_view import WriterViewMaterializer
from cera.semantic_validation import (
    BoundSemanticValidationV1,
    SemanticConflictClass,
    SemanticConflictV1,
    SemanticValidationVerdictV1,
    SemanticVerdict,
)
from cera.serialization import to_primitive

from .test_cognition_contracts import _plan
from .test_pi_scene_lean_v1 import FakePi, turn


class _CognitionPlanner:
    external_provider_boundary = False

    def plan(self, request):
        del request
        plan = to_primitive(_plan())
        return PlannerTurnOutputV1(
            sequence=plan["sequence"],
            decision_bundle=plan,
            provider_operations=1,
        )


class _SemanticValidator:
    external_provider_boundary = False

    def __init__(self, verdict: SemanticVerdict) -> None:
        self.verdict = verdict
        self.calls = 0

    def validate(self, request, custody):
        self.calls += 1
        conflict = (
            None
            if self.verdict is SemanticVerdict.PASS
            else SemanticConflictV1(
                conflict_class=SemanticConflictClass.OMITTED_DECISION,
                concise_explanation="The candidate omitted Sakura's decision.",
                exact_quote=None,
                decision_key="sakura_door_response",
            )
        )
        return BoundSemanticValidationV1(
            request=request,
            custody=custody,
            verdict=SemanticValidationVerdictV1(
                schema_version=SemanticValidationVerdictV1.SCHEMA_VERSION,
                verdict=self.verdict,
                conflict=conflict,
            ),
        )


def _runtime(root: Path, validator: _SemanticValidator):
    store = LeanSceneStore(root / "world")
    coordinator = LeanPiSceneCoordinator(
        store=store,
        planner=_CognitionPlanner(),
        writer_views=WriterViewMaterializer(root / "views"),
        pi=FakePi(),
        session_root=root / "sessions",
        semantic_validator=validator,
    )
    return coordinator, store


class PiSceneSemanticRuntimeTests(unittest.TestCase):
    def test_pass_auto_accepts_and_records_without_creator_click(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            validator = _SemanticValidator(SemanticVerdict.PASS)
            coordinator, store = _runtime(root, validator)
            review = coordinator.start_ordinary(turn())
            self.assertEqual(review.state, LeanReviewState.ACCEPTED)
            self.assertIsNotNone(review.semantic_validation)
            self.assertIsNotNone(review.accepted_receipt)
            self.assertEqual(validator.calls, 1)
            self.assertIsNone(
                coordinator.unresolved_review(
                    world_id=review.candidate.world_id,
                    branch_id=review.candidate.branch_id,
                )
            )
            self.assertEqual(
                store.load_head(
                    world_id=review.candidate.world_id,
                    branch_id=review.candidate.branch_id,
                ).accepted_turn_id,
                review.candidate.turn_id,
            )

    def test_reject_stays_inspectable_then_auto_declines_on_next_turn(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            validator = _SemanticValidator(SemanticVerdict.REJECT)
            coordinator, store = _runtime(root, validator)
            review = coordinator.start_ordinary(turn())
            self.assertEqual(review.state, LeanReviewState.REVIEW_READY)
            self.assertIsNotNone(review.semantic_validation)
            self.assertIsNone(
                store.load_head(
                    world_id=review.candidate.world_id,
                    branch_id=review.candidate.branch_id,
                ).receipt
            )

            restarted, _ = _runtime(root, validator)
            recovered = restarted.get_review(review.review_id)
            self.assertEqual(recovered.semantic_validation, review.semantic_validation)
            decision = restarted.accept_unresolved_for_new_turn(
                world_id=review.candidate.world_id,
                branch_id=review.candidate.branch_id,
            )
            assert decision is not None
            self.assertEqual(decision.review.state, LeanReviewState.DECLINED)
            self.assertIsNone(
                store.load_head(
                    world_id=review.candidate.world_id,
                    branch_id=review.candidate.branch_id,
                ).receipt
            )


if __name__ == "__main__":
    unittest.main()
