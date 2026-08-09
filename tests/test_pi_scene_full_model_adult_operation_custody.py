from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from cera.pi_scene.adult_operation_contracts import (
    AdultOperationDispatchUncertainError,
    AdultOperationReviewState,
)
from cera.pi_scene.adult_operation_store import (
    ProtectedAdultOperationController,
    ProtectedAdultOperationStore,
)
from cera.pi_scene.adult_orchestration import (
    AdultRouteOperationOutcomeV1,
    AutomaticAdultRouteOrchestrator,
    PreparedAdultRouteOperationV1,
)
from cera.pi_scene.full_model_controller import (
    AcceptedAdultTurnV1,
    AdultExecutionContextV1,
    FullModelSceneController,
    RejectedAdultTurnV1,
)
from cera.pi_scene.store import LeanSceneStore
from cera.provider_dispatch_guard import PROVIDER_DISPATCH_DISABLED_ENV
from cera.serialization import canonical_sha256

from . import test_pi_scene_full_model_controller as support
from .test_adult_turn_preparation import _turn
from .test_pi_scene_adult_orchestration import _RejectingTransport


class _AuditedOrchestrator:
    def __init__(
        self,
        *,
        delegate: AutomaticAdultRouteOrchestrator,
        custody: ProtectedAdultOperationStore,
        fail_after_dispatch: bool = False,
        forbid_dispatch: bool = False,
    ) -> None:
        self.delegate = delegate
        self.custody = custody
        self.fail_after_dispatch = fail_after_dispatch
        self.forbid_dispatch = forbid_dispatch
        self.execute_calls = 0
        self.prepared: PreparedAdultRouteOperationV1 | None = None

    def prepare(self, **kwargs: object) -> PreparedAdultRouteOperationV1:
        self.prepared = self.delegate.prepare(**kwargs)
        return self.prepared

    def execute_prepared(
        self,
        prepared: PreparedAdultRouteOperationV1,
    ) -> AdultRouteOperationOutcomeV1:
        before = self.custody.lookup(
            request_id=prepared.request_id,
            candidate_id=prepared.candidate_id,
        )
        if before.state is not AdultOperationReviewState.PREPARED:
            raise AssertionError("adult dispatch began without durable prepared custody")
        if self.forbid_dispatch:
            raise AssertionError("completed or uncertain adult operation was redispatched")
        self.execute_calls += 1
        outcome = self.delegate.execute_prepared(prepared)
        if self.fail_after_dispatch:
            raise OSError("simulated crash after provider completion before execution custody")
        return outcome


class FullModelAdultOperationCustodyTests(unittest.TestCase):
    def _controller(
        self,
        *,
        root: Path,
        store: LeanSceneStore,
        custody: ProtectedAdultOperationStore,
        planner: object,
        fail_after_dispatch: bool = False,
        forbid_dispatch: bool = False,
    ) -> tuple[FullModelSceneController, list[_AuditedOrchestrator], list[Any]]:
        helper = support.PiSceneFullModelControllerTests()
        ordinary, _ = helper._coordinator(root, store=store, planner=planner)
        transports: list[Any] = []
        build_delegate = helper._adult_factory(root, store, transports)
        audited: list[_AuditedOrchestrator] = []

        def build(turn, request_id, candidate_id):  # type: ignore[no-untyped-def]
            wrapper = _AuditedOrchestrator(
                delegate=build_delegate(turn, request_id, candidate_id),
                custody=custody,
                fail_after_dispatch=fail_after_dispatch,
                forbid_dispatch=forbid_dispatch,
            )
            audited.append(wrapper)
            return wrapper

        controller = FullModelSceneController(
            ordinary=ordinary,
            store=store,
            adult_orchestrator_factory=build,
            adult_context_provider=lambda _turn, _route: AdultExecutionContextV1(
                accepted_safe_projection="The accepted public scene remains current.",
                protected_adult_continuity=None,
                current_facts=support._controller_facts(),
                product_story_boundaries=("Preserve accepted branch authority.",),
            ),
            adult_operation_controller_factory=lambda: ProtectedAdultOperationController(
                custody
            ),
        )
        return controller, audited, transports

    def test_completed_pass_replays_without_adult_redispatch_then_binds_accept(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path(r"D:\Cera\tmp")) as temporary:
            root = Path(temporary)
            store = LeanSceneStore(root / "world")
            custody = ProtectedAdultOperationStore(root / "protected-adult")
            first, first_audits, first_transports = self._controller(
                root=root,
                store=store,
                custody=custody,
                planner=support._HandoffPlanner(),
            )
            with (
                patch.dict(
                    os.environ,
                    {PROVIDER_DISPATCH_DISABLED_ENV: "1"},
                    clear=False,
                ),
                patch.object(
                    store,
                    "promote_adult_acceptance_envelope",
                    side_effect=OSError("simulated crash before atomic promotion"),
                ),
                self.assertRaisesRegex(OSError, "before atomic promotion"),
            ):
                first.complete(request_id="request:adult-custody-replay", turn=_turn())

            self.assertEqual(first_audits[0].execute_calls, 1)
            self.assertEqual(len(first_transports[0].calls), 2)
            prepared = first_audits[0].prepared
            assert prepared is not None
            pending = custody.lookup(
                request_id="request:adult-custody-replay",
                candidate_id=prepared.candidate_id,
            )
            self.assertEqual(pending.state, AdultOperationReviewState.EXECUTED_PASSED)

            # Recovery uses the same deterministic request/candidate identity.  The
            # newly constructed provider adapter exists but may not be dispatched.
            restarted, restarted_audits, restarted_transports = self._controller(
                root=root,
                store=store,
                custody=ProtectedAdultOperationStore(root / "protected-adult"),
                planner=support._HandoffPlanner(),
                forbid_dispatch=True,
            )
            result = restarted.complete(
                request_id="request:adult-custody-replay",
                turn=_turn(),
            )

            self.assertIsInstance(result, AcceptedAdultTurnV1)
            assert isinstance(result, AcceptedAdultTurnV1)
            self.assertEqual(restarted_audits[0].execute_calls, 0)
            self.assertEqual(restarted_transports[0].calls, [])
            record = restarted_audits[0].custody.lookup(
                request_id="request:adult-custody-replay",
                candidate_id=result.outcome.prepared.candidate_id,
            )
            self.assertEqual(record.state, AdultOperationReviewState.ACCEPTED)
            assert record.decision is not None
            self.assertEqual(
                record.decision.accepted_binding_sha256,
                canonical_sha256(result.promotion),
            )

    def test_prepared_only_restart_fails_closed_without_redispatch(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path(r"D:\Cera\tmp")) as temporary:
            root = Path(temporary)
            store = LeanSceneStore(root / "world")
            custody = ProtectedAdultOperationStore(root / "protected-adult")
            first, first_audits, first_transports = self._controller(
                root=root,
                store=store,
                custody=custody,
                planner=support._HandoffPlanner(),
                fail_after_dispatch=True,
            )
            with (
                patch.dict(
                    os.environ,
                    {PROVIDER_DISPATCH_DISABLED_ENV: "1"},
                    clear=False,
                ),
                self.assertRaisesRegex(OSError, "after provider completion"),
            ):
                first.complete(request_id="request:adult-custody-uncertain", turn=_turn())
            self.assertEqual(first_audits[0].execute_calls, 1)
            self.assertEqual(len(first_transports[0].calls), 2)

            restarted, restarted_audits, restarted_transports = self._controller(
                root=root,
                store=store,
                custody=ProtectedAdultOperationStore(root / "protected-adult"),
                planner=support._HandoffPlanner(),
                forbid_dispatch=True,
            )
            with self.assertRaises(AdultOperationDispatchUncertainError):
                restarted.complete(
                    request_id="request:adult-custody-uncertain",
                    turn=_turn(),
                )
            self.assertEqual(restarted_audits[0].execute_calls, 0)
            self.assertEqual(restarted_transports[0].calls, [])

    def test_filter_rejection_is_durable_and_has_no_accepted_effect(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path(r"D:\Cera\tmp")) as temporary:
            root = Path(temporary)
            store = LeanSceneStore(root / "world")
            custody = ProtectedAdultOperationStore(root / "protected-adult")
            controller, audits, _ = self._controller(
                root=root,
                store=store,
                custody=custody,
                planner=support._HandoffPlanner(),
            )
            with (
                patch.dict(
                    os.environ,
                    {PROVIDER_DISPATCH_DISABLED_ENV: "1"},
                    clear=False,
                ),
                patch.object(support, "_FakeStructuredTransport", _RejectingTransport),
            ):
                result = controller.complete(
                    request_id="request:adult-custody-rejected",
                    turn=_turn(),
                )

            self.assertIsInstance(result, RejectedAdultTurnV1)
            prepared = audits[0].prepared
            assert prepared is not None
            record = custody.lookup(
                request_id="request:adult-custody-rejected",
                candidate_id=prepared.candidate_id,
            )
            self.assertEqual(record.state, AdultOperationReviewState.EXECUTED_REJECTED)
            self.assertIsNone(record.decision)
            self.assertEqual(
                store.load_head(world_id="world:test", branch_id="branch:test").generation,
                0,
            )

    def test_committed_promotion_recovers_missing_operation_decision_without_calls(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path(r"D:\Cera\tmp")) as temporary:
            root = Path(temporary)
            store = LeanSceneStore(root / "world")
            custody = ProtectedAdultOperationStore(root / "protected-adult")
            first, first_audits, first_transports = self._controller(
                root=root,
                store=store,
                custody=custody,
                planner=support._HandoffPlanner(),
            )
            with (
                patch.dict(
                    os.environ,
                    {PROVIDER_DISPATCH_DISABLED_ENV: "1"},
                    clear=False,
                ),
                patch.object(
                    custody,
                    "mark_accepted",
                    side_effect=OSError("simulated crash after atomic promotion"),
                ),
                self.assertRaisesRegex(OSError, "after atomic promotion"),
            ):
                first.complete(
                    request_id="request:adult-custody-post-promotion",
                    turn=_turn(),
                )

            self.assertEqual(first_audits[0].execute_calls, 1)
            self.assertEqual(len(first_transports[0].calls), 2)
            self.assertEqual(
                store.load_head(world_id="world:test", branch_id="branch:test").generation,
                1,
            )
            prepared = first_audits[0].prepared
            assert prepared is not None
            self.assertEqual(
                custody.lookup(
                    request_id="request:adult-custody-post-promotion",
                    candidate_id=prepared.candidate_id,
                ).state,
                AdultOperationReviewState.EXECUTED_PASSED,
            )

            restarted_custody = ProtectedAdultOperationStore(root / "protected-adult")
            restarted, restarted_audits, restarted_transports = self._controller(
                root=root,
                store=store,
                custody=restarted_custody,
                planner=support._NeverPlanner(),
                forbid_dispatch=True,
            )
            recovered = restarted.recover_committed_adult(
                request_id="request:adult-custody-post-promotion",
                turn=_turn(),
            )

            self.assertIsInstance(recovered, AcceptedAdultTurnV1)
            self.assertEqual(restarted_audits, [])
            self.assertEqual(restarted_transports, [])
            assert recovered is not None
            terminal = restarted_custody.lookup(
                request_id="request:adult-custody-post-promotion",
                candidate_id=recovered.envelope.candidate_id,
            )
            self.assertEqual(terminal.state, AdultOperationReviewState.ACCEPTED)

            replayed = restarted.recover_committed_adult(
                request_id="request:adult-custody-post-promotion",
                turn=_turn(),
            )
            self.assertEqual(replayed, recovered)


if __name__ == "__main__":
    unittest.main()
