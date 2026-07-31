from __future__ import annotations

import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import tempfile
import time

from cera.creator_review import (
    CorrectionDiagnosticKind,
    CreatorReviewAction,
    CreatorReviewAssessment,
    CreatorReviewCoordinator,
    CreatorReviewSeverity,
    CreatorReviewState,
    PublicationEligibility,
    ReviewIssueOwner,
)
from cera.ids import IdKind, TypedId
from cera.runtime import OrdinaryApplicationRequest
from cera.runtime import PostPublicationCoordinator
from cera.errors import StateConflictError
from cera.realization import EchoAcceptingSceneRealizationVerifierPort
from cera.reasoner_session import (
    CheckpointStatus,
    InMemoryReasonerSessionPort,
    NativeStoredReasonerSessionRuntime,
)
from cera.sillytavern import LiveSillyTavernTurnExecutor
import tests.test_live_shaped_pipeline as live_support


class CreatorReviewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.live = live_support.LiveShapedPipelineTests("runTest")
        self.live.setUp()
        self.addCleanup(self.live.doCleanups)

    @staticmethod
    def request(args) -> OrdinaryApplicationRequest:
        return OrdinaryApplicationRequest(
            schema_version=OrdinaryApplicationRequest.SCHEMA_VERSION,
            reasoner_request=args[0],
            composer_plan=args[2],
            renderer_profile=None,
            consolidation_requested=False,
        )

    def test_candidate_is_visible_before_verification_and_accept_commits_once(self) -> None:
        args = self.live.ordinary_case("creator-review-visible")
        request = self.request(args)
        coordinator = CreatorReviewCoordinator(self.live.real.sandbox.store)
        observed = []

        def display(candidate) -> None:
            observed.append(coordinator.record_provisional(request, candidate))

        result = self.live.pipeline.execute(
            args[0],
            args[1],
            args[2],
            args[3],
            provisional_candidate_callback=display,
        )
        self.assertEqual(len(observed), 1)
        record = observed[0]
        self.assertEqual(record.state, CreatorReviewState.VERIFYING_AND_PREPARING)
        self.assertEqual(self.live.real.sandbox.store.table_count("artifacts"), 0)
        self.assertEqual(
            self.live.real.sandbox.store.get_branch(record.branch_id).generation,
            0,
        )

        ready = coordinator.prepare_publication(record.review_id, request, result)
        self.assertEqual(ready.state, CreatorReviewState.REVIEW_READY)
        self.assertIsNotNone(ready.prepared_package)
        self.assertEqual(self.live.real.sandbox.store.table_count("artifacts"), 0)

        published = CreatorReviewCoordinator(
            self.live.real.sandbox.store
        ).accept(record.review_id)
        self.assertEqual(published.publication.receipt.generation_after, 1)
        self.assertEqual(self.live.real.sandbox.store.table_count("artifacts"), 1)
        timing = self.live.real.sandbox.store.get_creator_accept_timing(
            record.review_id
        )
        self.assertIsNotNone(timing)
        self.assertEqual(timing.provider_calls, 0)
        self.assertEqual(timing.automatic_retries, 0)
        self.assertGreaterEqual(timing.accept_to_commit_microseconds, 0)
        resolved = coordinator.store.get_creator_review(record.review_id)
        self.assertEqual(resolved.state, CreatorReviewState.ACCEPTED)
        self.assertIsNone(resolved.candidate_text)
        self.assertIsNone(resolved.live_result_json)

        replay = coordinator.accept(record.review_id)
        self.assertTrue(replay.publication.exact_replay)
        self.assertEqual(self.live.real.sandbox.store.table_count("artifacts"), 1)
        self.assertEqual(
            self.live.real.sandbox.store.get_creator_accept_timing(
                record.review_id
            ),
            timing,
        )

    def test_false_positive_accepts_unchanged_and_records_verifier_diagnostic(self) -> None:
        args = self.live.ordinary_case("creator-review-false-positive")
        request = self.request(args)
        coordinator = CreatorReviewCoordinator(self.live.real.sandbox.store)
        captured = []
        result = self.live.pipeline.execute(
            args[0],
            args[1],
            args[2],
            args[3],
            provisional_candidate_callback=lambda candidate: captured.append(
                coordinator.record_provisional(request, candidate)
            ),
        )
        ready = coordinator.prepare_publication(
            captured[0].review_id,
            request,
            result,
        )
        critical = CreatorReviewAssessment(
            schema_version=CreatorReviewAssessment.SCHEMA_VERSION,
            severity=CreatorReviewSeverity.CRITICAL,
            publication_eligibility=PublicationEligibility.ACCEPT_ALLOWED,
            issue_owner=ReviewIssueOwner.VERIFIER,
            reason_codes=("invented_history",),
            creator_reason="The verifier identified a possible unsupported detail.",
            verifier_status="verified_with_concern",
        )
        ready = replace(ready, assessment=critical)
        coordinator.store.replace_creator_review(
            ready,
            expected_states=(CreatorReviewState.REVIEW_READY,),
        )

        published = coordinator.accept(
            ready.review_id,
            action=CreatorReviewAction.FALSE_POSITIVE,
        )

        self.assertEqual(published.publication.receipt.generation_after, 1)
        resolved = coordinator.store.get_creator_review(ready.review_id)
        self.assertEqual(resolved.creator_action, CreatorReviewAction.FALSE_POSITIVE)
        diagnostics = coordinator.store.creator_correction_diagnostics(
            branch_id=ready.branch_id
        )
        self.assertEqual(len(diagnostics), 1)
        self.assertEqual(
            diagnostics[0].diagnostic_kind,
            CorrectionDiagnosticKind.FALSE_POSITIVE,
        )
        self.assertEqual(diagnostics[0].likely_owner, ReviewIssueOwner.VERIFIER)

    def test_false_positive_is_not_available_for_good_assessment(self) -> None:
        args = self.live.ordinary_case("creator-review-good-not-false-positive")
        request = self.request(args)
        coordinator = CreatorReviewCoordinator(self.live.real.sandbox.store)
        captured = []
        result = self.live.pipeline.execute(
            args[0], args[1], args[2], args[3],
            provisional_candidate_callback=lambda candidate: captured.append(
                coordinator.record_provisional(request, candidate)
            ),
        )
        ready = coordinator.prepare_publication(captured[0].review_id, request, result)
        with self.assertRaisesRegex(StateConflictError, "concern or critical"):
            coordinator.accept(
                ready.review_id,
                action=CreatorReviewAction.FALSE_POSITIVE,
            )

    def test_unresolved_branch_is_unique_and_decline_purges_prose(self) -> None:
        args = self.live.ordinary_case("creator-review-unique")
        request = self.request(args)
        coordinator = CreatorReviewCoordinator(self.live.real.sandbox.store)
        captured = []

        def display(candidate) -> None:
            captured.append(coordinator.record_provisional(request, candidate))

        self.live.pipeline.execute(
            args[0], args[1], args[2], args[3], provisional_candidate_callback=display
        )
        record = captured[0]
        current = coordinator.store.get_creator_review(record.review_id)
        self.assertEqual(current.candidate_text_sha256, record.candidate_text_sha256)
        error = coordinator.record_error(record.review_id, "test_interruption")
        self.assertEqual(error.state, CreatorReviewState.ERROR)
        declined = coordinator.decline(record.review_id)
        self.assertEqual(declined.state, CreatorReviewState.REJECTED)
        self.assertEqual(declined.creator_action, CreatorReviewAction.DECLINE)
        self.assertIsNone(declined.candidate_text)
        self.assertIsNone(
            coordinator.store.unresolved_creator_review_for_branch(record.branch_id)
        )

    def test_accept_recovers_a_restart_in_durable_committing_state(self) -> None:
        args = self.live.ordinary_case("creator-review-commit-restart")
        request = self.request(args)
        coordinator = CreatorReviewCoordinator(self.live.real.sandbox.store)
        captured = []
        result = self.live.pipeline.execute(
            args[0],
            args[1],
            args[2],
            args[3],
            provisional_candidate_callback=lambda candidate: captured.append(
                coordinator.record_provisional(request, candidate)
            ),
        )
        ready = coordinator.prepare_publication(
            captured[0].review_id,
            request,
            result,
        )
        committing = replace(
            ready,
            state=CreatorReviewState.COMMITTING,
            creator_action=CreatorReviewAction.ACCEPT,
        )
        coordinator.store.replace_creator_review(
            committing,
            expected_states=(CreatorReviewState.REVIEW_READY,),
        )

        published = CreatorReviewCoordinator(
            self.live.real.sandbox.store
        ).accept(ready.review_id)
        self.assertEqual(published.publication.receipt.generation_after, 1)
        self.assertEqual(
            coordinator.store.get_creator_review(ready.review_id).state,
            CreatorReviewState.ACCEPTED,
        )

    def test_stale_branch_head_blocks_prepared_package_without_story_write(self) -> None:
        first_args = self.live.ordinary_case("creator-review-stale")
        first_request = self.request(first_args)
        coordinator = CreatorReviewCoordinator(self.live.real.sandbox.store)
        captured = []
        first_result = self.live.pipeline.execute(
            first_args[0],
            first_args[1],
            first_args[2],
            first_args[3],
            provisional_candidate_callback=lambda candidate: captured.append(
                coordinator.record_provisional(first_request, candidate)
            ),
        )
        ready = coordinator.prepare_publication(
            captured[0].review_id,
            first_request,
            first_result,
        )
        second_args = self.live.ordinary_case("creator-review-stale-winner")
        second_result = self.live.pipeline.execute(*second_args[:4])
        PostPublicationCoordinator(self.live.real.sandbox.store).execute(second_result)

        with self.assertRaisesRegex(StateConflictError, "stale"):
            coordinator.accept(ready.review_id)
        self.assertEqual(
            coordinator.store.get_creator_review(ready.review_id).state,
            CreatorReviewState.REVIEW_READY,
        )
        self.assertEqual(self.live.real.sandbox.store.table_count("artifacts"), 1)

    def _live_executor_fixture(self, suffix: str):
        args = self.live.ordinary_case(suffix)
        request = self.request(args)
        verifier = EchoAcceptingSceneRealizationVerifierPort()
        executor = LiveSillyTavernTurnExecutor(
            SimpleNamespace(store=self.live.real.sandbox.store),
            reasoner_port_factory=lambda _workspace: args[1],
            composer_port_factory=lambda: args[3],
            verifier_port_factory=lambda _workspace: verifier,
            stage_audit_enabled=False,
        )
        prepared = SimpleNamespace(application_request=request)
        return args, request, verifier, executor, prepared

    def _wait_ready(self, review_id, *, timeout: float = 5.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            record = self.live.real.sandbox.store.get_creator_review(review_id)
            if record.state in {
                CreatorReviewState.REVIEW_READY,
                CreatorReviewState.ERROR,
            }:
                return record
            time.sleep(0.01)
        self.fail("creator review did not reach a terminal review state")

    def test_live_review_accept_promotes_the_bound_reasoner_checkpoint(self) -> None:
        args = self.live.ordinary_case("creator-review-session-promotion")
        request = self.request(args)
        verifier = EchoAcceptingSceneRealizationVerifierPort()
        session_runtime = NativeStoredReasonerSessionRuntime(
            self.live.real.sandbox.store,
            repository_root=Path(__file__).resolve().parents[1],
            session_port=InMemoryReasonerSessionPort(),
        )
        self.addCleanup(session_runtime.close)
        executor = LiveSillyTavernTurnExecutor(
            SimpleNamespace(store=self.live.real.sandbox.store),
            reasoner_port_factory=lambda _workspace: args[1],
            composer_port_factory=lambda: args[3],
            verifier_port_factory=lambda _workspace: verifier,
            reasoner_session_runtime=session_runtime,
            stage_audit_enabled=False,
        )
        prepared = SimpleNamespace(application_request=request)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "reasoner").mkdir()
            (root / "verifier").mkdir()
            first = executor.execute(prepared, workspace_root=root)
        review_id = TypedId.parse(first.provisional_review_id, IdKind.REVIEW_PACKET)
        self._wait_ready(review_id)
        candidate = self.live.real.sandbox.store.reasoner_checkpoint_for_review(
            review_id
        )
        self.assertIsNotNone(candidate)
        self.assertEqual(candidate.status, CheckpointStatus.CANDIDATE)

        executor.review_action(review_id, CreatorReviewAction.ACCEPT)
        accepted = self.live.real.sandbox.store.get_reasoner_checkpoint(
            candidate.checkpoint_id
        )
        ledger = self.live.real.sandbox.store.get_reasoner_session(
            candidate.session_id
        )
        self.assertEqual(accepted.status, CheckpointStatus.ACCEPTED)
        self.assertEqual(ledger.accepted_checkpoint_id, candidate.checkpoint_id)
        self.assertEqual(ledger.accumulated_turns, 1)

    def test_deepseek_button_reuses_plan_and_runs_one_manual_rewrite(self) -> None:
        args, _request, verifier, executor, prepared = self._live_executor_fixture(
            "creator-review-deepseek-rewrite"
        )
        with tempfile.TemporaryDirectory() as directory:
            first = executor.execute(prepared, workspace_root=Path(directory))
        review_id = TypedId.parse(first.provisional_review_id, IdKind.REVIEW_PACKET)
        self._wait_ready(review_id)
        reasoner_calls = args[4].calls
        composer_calls = len(args[5].calls)
        verifier_calls = verifier.invocation_count

        replacement = executor.review_action(
            review_id,
            CreatorReviewAction.DEEPSEEK_REWRITE,
            feedback="Keep the plan, but make the transitions less repetitive.",
        )
        self.assertNotEqual(replacement.review_id, review_id)
        self.assertEqual(args[4].calls, reasoner_calls)
        self.assertEqual(len(args[5].calls), composer_calls + 1)
        self.assertEqual(verifier.invocation_count, verifier_calls + 1)
        self.assertEqual(replacement.sequence_plan_sha256, self.live.real.sandbox.store.get_creator_review(review_id).sequence_plan_sha256)
        self.assertEqual(
            self.live.real.sandbox.store.get_creator_review(review_id).state,
            CreatorReviewState.REJECTED,
        )
        diagnostics = self.live.real.sandbox.store.creator_correction_diagnostics()
        self.assertEqual(len(diagnostics), 1)
        self.assertEqual(diagnostics[0].likely_owner.value, "composer")
        self.assertEqual(diagnostics[0].diagnostic_kind.value, "prose_realization")
        self.assertEqual(
            diagnostics[0].creator_feedback,
            "Keep the plan, but make the transitions less repetitive.",
        )

    def test_codex_and_correction_buttons_each_replan_once_as_non_story_feedback(self) -> None:
        args, _request, verifier, executor, prepared = self._live_executor_fixture(
            "creator-review-replan-actions"
        )
        with tempfile.TemporaryDirectory() as directory:
            first = executor.execute(prepared, workspace_root=Path(directory))
        review_id = TypedId.parse(first.provisional_review_id, IdKind.REVIEW_PACKET)
        self._wait_ready(review_id)
        reasoner_calls = args[4].calls
        composer_calls = len(args[5].calls)
        verifier_calls = verifier.invocation_count

        replacement = executor.review_action(
            review_id,
            CreatorReviewAction.CODEX_REPLAN,
            feedback="The causal sequence needs a different supported handoff.",
        )
        self.assertEqual(args[4].calls, reasoner_calls + 1)
        self.assertEqual(len(args[5].calls), composer_calls + 1)
        self.assertEqual(verifier.invocation_count, verifier_calls + 1)

        corrected = executor.review_action(
            replacement.review_id,
            CreatorReviewAction.CORRECTION_ADJUSTMENT,
            feedback="Preserve the source but correct the causal framing.",
        )
        self.assertNotEqual(corrected.review_id, replacement.review_id)
        self.assertEqual(args[4].calls, reasoner_calls + 2)
        self.assertEqual(len(args[5].calls), composer_calls + 2)
        self.assertEqual(verifier.invocation_count, verifier_calls + 2)
        self.assertNotIn(
            "Preserve the source but correct the causal framing.",
            corrected.candidate_text or "",
        )
        diagnostics = self.live.real.sandbox.store.creator_correction_diagnostics()
        self.assertEqual(
            [value.diagnostic_kind.value for value in diagnostics],
            ["causal_logic", "correction_adjustment"],
        )


if __name__ == "__main__":
    unittest.main()
