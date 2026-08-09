from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from cera.pi_scene.http import PiSceneHttpAdapter
from cera.pi_scene.http_contracts import PI_SCENE_ORDINARY_MODEL, PI_SCENE_PROFILE
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

    def __init__(self) -> None:
        self.calls = 0

    def plan(self, request):
        del request
        self.calls += 1
        plan = to_primitive(_plan())
        return PlannerTurnOutputV1(
            sequence=plan["sequence"],
            decision_bundle=plan,
            provider_operations=1,
        )


class _SemanticValidator:
    external_provider_boundary = False

    def __init__(self, *verdicts: SemanticVerdict) -> None:
        if not verdicts:
            raise ValueError("at least one verdict is required")
        self.verdicts = verdicts
        self.calls = 0

    def validate(self, request, custody):
        self.calls += 1
        verdict = self.verdicts[min(self.calls - 1, len(self.verdicts) - 1)]
        conflict = (
            None
            if verdict is SemanticVerdict.PASS
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
                verdict=verdict,
                conflict=conflict,
            ),
        )


def _runtime(root: Path, validator: _SemanticValidator):
    store = LeanSceneStore(root / "world")
    planner = _CognitionPlanner()
    coordinator = LeanPiSceneCoordinator(
        store=store,
        planner=planner,
        writer_views=WriterViewMaterializer(root / "views"),
        pi=FakePi(),
        session_root=root / "sessions",
        semantic_validator=validator,
    )
    return coordinator, store, planner


class PiSceneSemanticRuntimeTests(unittest.TestCase):
    def test_pass_auto_accepts_and_records_without_creator_click(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            validator = _SemanticValidator(SemanticVerdict.PASS)
            coordinator, store, _ = _runtime(root, validator)
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

    def test_one_failed_repair_stays_inspectable_then_auto_declines(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            validator = _SemanticValidator(SemanticVerdict.REJECT)
            coordinator, store, _ = _runtime(root, validator)
            review = coordinator.start_ordinary(turn())
            self.assertEqual(review.state, LeanReviewState.REVIEW_READY)
            self.assertIsNotNone(review.result.repaired_from_candidate_id)
            self.assertIsNotNone(review.semantic_validation)
            self.assertEqual(validator.calls, 2)
            original_id = review.result.repaired_from_candidate_id
            original = next(
                value
                for value in coordinator._reviews.values()
                if value.candidate.candidate_id == original_id
            )
            self.assertEqual(original.state, LeanReviewState.REPAIRED)
            self.assertIsNone(
                store.load_head(
                    world_id=review.candidate.world_id,
                    branch_id=review.candidate.branch_id,
                ).receipt
            )

            restarted, _, _ = _runtime(root, validator)
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

    def test_one_complete_repair_passes_and_auto_accepts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            validator = _SemanticValidator(
                SemanticVerdict.REJECT,
                SemanticVerdict.PASS,
            )
            coordinator, store, _ = _runtime(root, validator)
            accepted = coordinator.start_ordinary(turn())
            self.assertEqual(accepted.state, LeanReviewState.ACCEPTED)
            self.assertIsNotNone(accepted.result.repaired_from_candidate_id)
            self.assertEqual(validator.calls, 2)
            self.assertEqual(
                store.load_head(
                    world_id=accepted.candidate.world_id,
                    branch_id=accepted.candidate.branch_id,
                ).accepted_turn_id,
                accepted.candidate.turn_id,
            )

            restarted, _, _ = _runtime(root, validator)
            recovered = restarted.get_review(accepted.review_id)
            self.assertEqual(recovered.state, LeanReviewState.ACCEPTED)
            attempts = restarted.provider_operation_attempts(recovered)
            self.assertEqual(len(attempts), 2)
            self.assertEqual(attempts[0].state, LeanReviewState.REPAIRED)
            self.assertEqual(
                attempts[0].candidate.candidate_id,
                recovered.result.repaired_from_candidate_id,
            )
            self.assertEqual(attempts[1], recovered)

    def test_http_accounts_for_both_critical_repair_attempts_after_restart(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            coordinator, _, _ = _runtime(
                root,
                _SemanticValidator(
                    SemanticVerdict.REJECT,
                    SemanticVerdict.PASS,
                ),
            )
            payload = {
                "model": PI_SCENE_ORDINARY_MODEL,
                "messages": [{"role": "user", "content": "Continue the scene."}],
                "stream": False,
                "cera_profile_id": PI_SCENE_PROFILE,
                "cera_session_id": "semantic-repair-accounting",
            }
            first = PiSceneHttpAdapter(
                coordinator=coordinator,
                session_id="semantic-repair-accounting",
                context_provider=lambda *_args: turn(),
            ).complete(payload)

            self.assertEqual(
                first["cera"]["provider_operations"],
                {"planner": 1, "writer": 2, "validator": 2, "recorder": 1},
            )
            self.assertEqual(
                first["cera"]["provider_attempts"],
                [
                    {
                        "attempt_number": 1,
                        "candidate_id": first["cera"]["provider_attempts"][0]["candidate_id"],
                        "disposition": "semantic_rejected",
                        "provider_operations": {
                            "planner": 1,
                            "writer": 1,
                            "validator": 1,
                        },
                    },
                    {
                        "attempt_number": 2,
                        "candidate_id": first["cera"]["candidate_id"],
                        "disposition": "semantic_pass",
                        "provider_operations": {
                            "planner": 0,
                            "writer": 1,
                            "validator": 1,
                        },
                    },
                ],
            )
            self.assertNotEqual(
                first["cera"]["provider_attempts"][0]["candidate_id"],
                first["cera"]["candidate_id"],
            )

            restarted, _, restarted_planner = _runtime(
                root,
                _SemanticValidator(SemanticVerdict.PASS),
            )
            replay = PiSceneHttpAdapter(
                coordinator=restarted,
                session_id="semantic-repair-accounting",
                context_provider=lambda *_args: turn(),
            ).complete(payload)
            self.assertEqual(replay, first)
            self.assertEqual(restarted_planner.calls, 0)

    def test_provider_operation_attempts_are_one_for_an_unrepaired_pass(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            coordinator, _, _ = _runtime(
                Path(temporary),
                _SemanticValidator(SemanticVerdict.PASS),
            )
            accepted = coordinator.start_ordinary(turn())
            self.assertEqual(coordinator.provider_operation_attempts(accepted), (accepted,))

    def test_regenerate_reruns_the_logic_owner_before_the_writer(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            validator = _SemanticValidator(SemanticVerdict.REJECT)
            coordinator, _, planner = _runtime(root, validator)
            first = coordinator.start_ordinary(turn())
            self.assertEqual(planner.calls, 1)

            decision = coordinator.regenerate(first.review_id)
            assert decision.successor is not None
            self.assertEqual(planner.calls, 2)
            self.assertEqual(
                decision.successor.result.regenerated_from_candidate_id,
                first.candidate.candidate_id,
            )
            self.assertGreater(
                decision.successor.result.planner_provider_operations,
                0,
            )

    def test_completion_metadata_reports_auto_accept_and_rejection_truthfully(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            accepted, _, _ = _runtime(
                Path(temporary) / "accepted",
                _SemanticValidator(SemanticVerdict.PASS),
            )
            accepted_review = accepted.start_ordinary(turn())
            accepted_payload = PiSceneHttpAdapter._completion_payload(accepted_review)
            self.assertEqual(accepted_payload["cera"]["status"], "accepted")
            self.assertTrue(accepted_payload["cera"]["story_state_committed"])
            self.assertFalse(accepted_payload["cera"]["provisional"])
            self.assertEqual(
                accepted_payload["cera"]["semantic_validation"]["verdict"],
                "pass",
            )

            rejected, _, _ = _runtime(
                Path(temporary) / "rejected",
                _SemanticValidator(SemanticVerdict.REJECT),
            )
            rejected_review = rejected.start_ordinary(turn())
            rejected_payload = PiSceneHttpAdapter._completion_payload(rejected_review)
            self.assertEqual(
                rejected_payload["cera"]["status"],
                "validation_rejected",
            )
            self.assertFalse(rejected_payload["cera"]["story_state_committed"])
            self.assertTrue(rejected_payload["cera"]["provisional"])

    def test_creator_can_accept_a_rejected_candidate_only_as_provisional(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            coordinator, _, _ = _runtime(
                root,
                _SemanticValidator(SemanticVerdict.REJECT),
            )
            review = coordinator.start_ordinary(turn())
            adapter = PiSceneHttpAdapter(
                coordinator=coordinator,
                session_id="session-test",
                context_provider=lambda *_args: turn(),
            )
            result = adapter.decide(
                review.review_id,
                {"action": "accept_provisional"},
            )
            self.assertTrue(result["story_state_committed"])
            self.assertEqual(result["review"]["canon_status"], "provisional")
            accepted = coordinator.get_review(review.review_id)
            assert accepted.accepted_receipt is not None
            self.assertEqual(
                accepted.accepted_receipt.creator_action,
                "provisional_accept",
            )


if __name__ == "__main__":
    unittest.main()
