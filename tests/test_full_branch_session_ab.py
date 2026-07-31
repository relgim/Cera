from __future__ import annotations

from dataclasses import dataclass, field
import inspect
import unittest

from scripts.run_full_branch_session_ab import (
    bind_provisional_history,
    provisional_context_entry,
)
from scripts.run_codex_branch_session_ab import POLICIES
from scripts.run_codex_branch_session_ab import InProcessSessionRunner
import scripts.run_codex_branch_session_ab as codex_session_ab
import scripts.run_luna_max_fast_three_run as luna_fast


@dataclass(frozen=True)
class FakeDossier:
    scene_anchors: tuple[str, ...] = ()


@dataclass(frozen=True)
class FakeReasonerRequest:
    seed_dossier: FakeDossier = field(default_factory=FakeDossier)


@dataclass(frozen=True)
class FakeComposerPlan:
    established_scene_context: tuple[str, ...] = ()


@dataclass(frozen=True)
class FakeApplicationRequest:
    reasoner_request: FakeReasonerRequest = field(
        default_factory=FakeReasonerRequest
    )
    composer_plan: FakeComposerPlan = field(default_factory=FakeComposerPlan)


class FullBranchSessionABTests(unittest.TestCase):
    def test_comparison_policies_have_only_the_intended_thread_difference(self) -> None:
        self.assertEqual(
            [(value.key, value.reuse_thread) for value in POLICIES],
            [("fresh_each_turn", False), ("continued_thread", True)],
        )

    def test_failed_pipeline_invalidates_continued_thread(self) -> None:
        runner = InProcessSessionRunner(
            reuse_thread=True,
            workspace=__import__("pathlib").Path("diagnostic"),
        )
        runner.thread = object()
        runner.invalidate_thread()
        self.assertIsNone(runner.thread)
        self.assertEqual(runner.thread_invalidation_count, 1)

    def test_optional_fast_service_tier_is_explicit_and_non_empty(self) -> None:
        runner = InProcessSessionRunner(
            reuse_thread=False,
            workspace=__import__("pathlib").Path("diagnostic"),
            service_tier="priority",
        )
        self.assertEqual(runner.service_tier, "priority")
        with self.assertRaises(ValueError):
            InProcessSessionRunner(
                reuse_thread=False,
                workspace=__import__("pathlib").Path("diagnostic"),
                service_tier=" ",
            )

    def test_luna_three_run_uses_actual_highest_and_fast_tier(self) -> None:
        self.assertEqual(luna_fast.MODEL, "gpt-5.6-luna")
        self.assertEqual(luna_fast.EFFORT, "max")
        self.assertEqual(luna_fast.SERVICE_TIER, "priority")
        self.assertEqual(luna_fast.RUN_COUNT, 3)

    def test_run_policy_defaults_preserve_sol_medium_route(self) -> None:
        signature = inspect.signature(codex_session_ab.run_policy)
        self.assertEqual(signature.parameters["model"].default, "gpt-5.6-sol")
        self.assertEqual(signature.parameters["effort"].default, "medium")
        self.assertIsNone(signature.parameters["service_tier"].default)
        self.assertFalse(signature.parameters["stop_on_failure"].default)
        self.assertIsNone(signature.parameters["maximum_output_tokens"].default)

    def test_no_history_preserves_original_inputs(self) -> None:
        request = FakeApplicationRequest()
        reasoner, composer = bind_provisional_history(request, ())
        self.assertIs(reasoner, request.reasoner_request)
        self.assertIs(composer, request.composer_plan)

    def test_history_is_explicitly_identical_for_reasoner_and_composer(self) -> None:
        request = FakeApplicationRequest(
            reasoner_request=FakeReasonerRequest(FakeDossier(("seed",))),
            composer_plan=FakeComposerPlan(("continuity",)),
        )
        history = (provisional_context_entry(1, "Sakura answers Ted."),)
        reasoner, composer = bind_provisional_history(request, history)
        self.assertEqual(reasoner.seed_dossier.scene_anchors, ("seed", *history))
        self.assertEqual(
            composer.established_scene_context,
            ("continuity", *history),
        )
        self.assertIn("not been accepted as durable canon", history[0])
        self.assertIn("Sakura answers Ted.", history[0])

    def test_binding_does_not_mutate_original_request(self) -> None:
        request = FakeApplicationRequest()
        bind_provisional_history(
            request,
            (provisional_context_entry(1, "visible"),),
        )
        self.assertEqual(request.reasoner_request.seed_dossier.scene_anchors, ())
        self.assertEqual(request.composer_plan.established_scene_context, ())


if __name__ == "__main__":
    unittest.main()
