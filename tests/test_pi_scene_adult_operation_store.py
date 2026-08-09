from __future__ import annotations

import json
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from cera.adult_pipeline.contracts import (
    AdultContextFactV1,
    AdultNextRoute,
    AdultRouteStateSnapshotV1,
)
from cera.adult_pipeline.craft_catalog import CatalogAdultCraftRetrieval
from cera.adult_pipeline.integration import AdultPipelineIntegrationV1
from cera.adult_pipeline.pi_roles import (
    AdultRoleViewContextV1,
    PiDeepSeekAdultFilterPort,
    PiDeepSeekAdultScenePort,
)
from cera.adult_pipeline.pipeline import AdultPipeline
from cera.adult_pipeline.preparation import build_adult_turn_preparation_builder
from cera.errors import StateConflictError
from cera.pi_scene.adult_operation_contracts import (
    AdultOperationDecisionAction,
    AdultOperationDecisionV1,
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
from cera.pi_scene.writer_view import WriterViewMaterializer
from cera.provider_dispatch_guard import PROVIDER_DISPATCH_DISABLED_ENV
from cera.serialization import canonical_json, canonical_sha256, text_sha256
from tests.test_adult_pipeline_pi_integration import (
    PROTECTED_PROSE,
    _FakeStructuredTransport,
)
from tests.test_adult_turn_preparation import CATALOG_ROOT, PROTECTED_A, _turn
from tests.test_pi_scene_adult_orchestration import _RejectingTransport, _RouteState


def _facts() -> tuple[AdultContextFactV1, ...]:
    return (
        AdultContextFactV1(
            evidence_ref="evidence:public",
            subject_id="character:hana",
            authoritative_fact="Hana is present in the private room.",
            visibility="public",
        ),
        AdultContextFactV1(
            evidence_ref="evidence:private",
            subject_id="character:hana",
            authoritative_fact="Hana retains private current context.",
            visibility="adult_role_private",
        ),
    )


class AdultOperationStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.runtime_root = self.root / "custody-runtime"
        self.store = ProtectedAdultOperationStore(self.runtime_root)

    def tearDown(self) -> None:
        self.temp.cleanup()

    @staticmethod
    def _snapshot() -> AdultRouteStateSnapshotV1:
        return AdultRouteStateSnapshotV1(
            schema_version=AdultRouteStateSnapshotV1.SCHEMA_VERSION,
            world_id="world:test",
            branch_id="branch:test",
            accepted_head_sha256=text_sha256("accepted-head"),
            current_logic_route=AdultNextRoute.ADULT,
            source_promotion_sha256=text_sha256("accepted-adult-promotion"),
        )

    def _operation(
        self,
        *,
        candidate_id: str,
        passed: bool,
    ) -> tuple[PreparedAdultRouteOperationV1, AdultRouteOperationOutcomeV1]:
        transport = _FakeStructuredTransport() if passed else _RejectingTransport()
        context = AdultRoleViewContextV1(
            world_id="world:test",
            branch_id="branch:test",
            scene_id="scene:test",
            turn_id="turn:adult-store",
            candidate_id=candidate_id,
            current_state={"location": "private room"},
            characters={"character:hana": {"name": "Hana", "age": 38}},
        )
        fixture_root = self.root / "fixture-provider" / text_sha256(candidate_id)[:12]
        materializer = WriterViewMaterializer(fixture_root / "views")
        scene = PiDeepSeekAdultScenePort(
            transport=transport,
            materializer=materializer,
            context=context,
            session_root=fixture_root / "sessions",
        )
        filter_port = PiDeepSeekAdultFilterPort(
            transport=transport,
            materializer=materializer,
            context=context,
            session_root=fixture_root / "sessions",
        )
        integration = AdultPipelineIntegrationV1(
            pipeline=AdultPipeline(scene=scene, filter_port=filter_port),
            scene_port=scene,
            filter_port=filter_port,
            craft_retrieval=CatalogAdultCraftRetrieval(CATALOG_ROOT),
        )
        route_state = _RouteState(self._snapshot())
        orchestrator = AutomaticAdultRouteOrchestrator(
            route_state=route_state,
            preparation_builder=build_adult_turn_preparation_builder(integration.craft_retrieval),
            pipeline=integration,
        )
        prepared = orchestrator.prepare(
            request_id="request:adult-store",
            candidate_id=candidate_id,
            turn_input=_turn(craft_mode="off"),
            accepted_safe_projection="The accepted private interaction remains current.",
            protected_adult_continuity=PROTECTED_A,
            current_facts=_facts(),
            product_story_boundaries=("Preserve accepted branch authority.",),
            cognition_handoff_factory=None,
        )
        with patch.dict(
            os.environ,
            {PROVIDER_DISPATCH_DISABLED_ENV: "1"},
            clear=False,
        ):
            outcome = orchestrator.execute_prepared(prepared)
        return prepared, outcome

    @staticmethod
    def _accept_decision() -> AdultOperationDecisionV1:
        return AdultOperationDecisionV1(
            schema_version=AdultOperationDecisionV1.SCHEMA_VERSION,
            action=AdultOperationDecisionAction.ACCEPT,
            decision_id="decision:accept-adult-store",
            decision_request_sha256=text_sha256("accept-request"),
            accepted_binding_sha256=text_sha256("atomic-accepted-receipt"),
        )

    @staticmethod
    def _decline_decision() -> AdultOperationDecisionV1:
        return AdultOperationDecisionV1(
            schema_version=AdultOperationDecisionV1.SCHEMA_VERSION,
            action=AdultOperationDecisionAction.DECLINE,
            decision_id="decision:decline-adult-store",
            decision_request_sha256=text_sha256("decline-request"),
            accepted_binding_sha256=None,
        )

    def test_controller_persists_prepared_before_dispatch_and_execution_after(self) -> None:
        prepared, outcome = self._operation(candidate_id="candidate:passed", passed=True)
        controller = ProtectedAdultOperationController(self.store)
        prepare_calls = 0
        execute_calls = 0

        def prepare() -> PreparedAdultRouteOperationV1:
            nonlocal prepare_calls
            prepare_calls += 1
            return prepared

        def execute(value: PreparedAdultRouteOperationV1) -> AdultRouteOperationOutcomeV1:
            nonlocal execute_calls
            execute_calls += 1
            before = self.store.lookup(
                request_id=value.request_id,
                candidate_id=value.candidate_id,
            )
            self.assertEqual(before.state, AdultOperationReviewState.PREPARED)
            prepared_path = (
                self.store.root / "RECORDS" / f"op-{value.operation_sha256}" / "PREPARED.json"
            )
            self.assertTrue(prepared_path.is_file())
            self.assertFalse(prepared_path.with_name("EXECUTION.json").exists())
            return outcome

        begin = controller.recover_or_prepare(
            request_id=prepared.request_id,
            candidate_id=prepared.candidate_id,
            prepare=prepare,
        )
        resolved = controller.execute_new(begin, execute=execute)

        self.assertTrue(begin.created)
        self.assertFalse(resolved.replayed)
        self.assertEqual(resolved.record.state, AdultOperationReviewState.EXECUTED_PASSED)
        self.assertEqual(prepare_calls, 1)
        self.assertEqual(execute_calls, 1)
        execution_path = (
            self.store.root / "RECORDS" / f"op-{prepared.operation_sha256}" / "EXECUTION.json"
        )
        self.assertTrue(execution_path.is_file())

    def test_restart_replays_exact_completed_outcome_without_callbacks(self) -> None:
        prepared, outcome = self._operation(candidate_id="candidate:restart", passed=True)
        self.store.begin(prepared)
        self.store.record_execution(outcome)
        restarted = ProtectedAdultOperationStore(self.runtime_root)
        controller = ProtectedAdultOperationController(restarted)

        def forbidden_prepare() -> PreparedAdultRouteOperationV1:
            raise AssertionError("restart called prepare")

        def forbidden_execute(
            value: PreparedAdultRouteOperationV1,
        ) -> AdultRouteOperationOutcomeV1:
            del value
            raise AssertionError("restart redispatched providers")

        begin = controller.recover_or_prepare(
            request_id=prepared.request_id,
            candidate_id=prepared.candidate_id,
            prepare=forbidden_prepare,
        )
        replayed = controller.execute_new(begin, execute=forbidden_execute)

        self.assertFalse(begin.created)
        self.assertTrue(replayed.replayed)
        self.assertEqual(replayed.record.outcome, outcome)

    def test_prepared_only_restart_blocks_ambiguous_provider_redispatch(self) -> None:
        prepared, _ = self._operation(candidate_id="candidate:pending", passed=True)
        self.store.begin(prepared)
        restarted = ProtectedAdultOperationStore(self.runtime_root)
        controller = ProtectedAdultOperationController(restarted)
        execute_calls = 0

        begin = controller.recover_or_prepare(
            request_id=prepared.request_id,
            candidate_id=prepared.candidate_id,
            prepare=lambda: prepared,
        )

        def execute(value: PreparedAdultRouteOperationV1) -> AdultRouteOperationOutcomeV1:
            nonlocal execute_calls
            del value
            execute_calls += 1
            raise AssertionError("uncertain operation redispatched")

        with self.assertRaises(AdultOperationDispatchUncertainError):
            controller.execute_new(begin, execute=execute)
        self.assertEqual(execute_calls, 0)

    def test_accept_and_decline_are_terminal_hash_bound_and_idempotent(self) -> None:
        prepared, outcome = self._operation(candidate_id="candidate:accept", passed=True)
        self.store.begin(prepared)
        self.store.record_execution(outcome)
        decision = self._accept_decision()
        accepted = self.store.mark_accepted(
            request_id=prepared.request_id,
            candidate_id=prepared.candidate_id,
            decision=decision,
        )
        replayed = self.store.mark_accepted(
            request_id=prepared.request_id,
            candidate_id=prepared.candidate_id,
            decision=decision,
        )
        replayed_execution = self.store.record_execution(outcome)

        self.assertEqual(accepted.state, AdultOperationReviewState.ACCEPTED)
        self.assertEqual(replayed, accepted)
        self.assertEqual(replayed_execution.state, AdultOperationReviewState.ACCEPTED)
        with self.assertRaisesRegex(StateConflictError, "decision changed"):
            self.store.mark_declined(
                request_id=prepared.request_id,
                candidate_id=prepared.candidate_id,
                decision=self._decline_decision(),
            )

    def test_rejection_can_decline_but_cannot_accept(self) -> None:
        prepared, outcome = self._operation(candidate_id="candidate:rejected", passed=False)
        self.store.begin(prepared)
        rejected = self.store.record_execution(outcome)
        self.assertEqual(rejected.state, AdultOperationReviewState.EXECUTED_REJECTED)

        with self.assertRaisesRegex(StateConflictError, "cannot be accepted"):
            self.store.mark_accepted(
                request_id=prepared.request_id,
                candidate_id=prepared.candidate_id,
                decision=self._accept_decision(),
            )
        declined = self.store.mark_declined(
            request_id=prepared.request_id,
            candidate_id=prepared.candidate_id,
            decision=self._decline_decision(),
        )
        self.assertEqual(declined.state, AdultOperationReviewState.DECLINED)

    def test_same_request_candidate_with_different_preparation_is_ambiguous(self) -> None:
        prepared, _ = self._operation(candidate_id="candidate:ambiguous", passed=True)
        self.store.begin(prepared)
        changed_snapshot = replace(
            prepared.route_state,
            accepted_head_sha256=text_sha256("another-head"),
            source_promotion_sha256=text_sha256("another-promotion"),
        )
        conflicting = PreparedAdultRouteOperationV1.create(
            request_id=prepared.request_id,
            candidate_id=prepared.candidate_id,
            route_state=changed_snapshot,
            scene_request=prepared.scene_request,
        )

        with self.assertRaisesRegex(StateConflictError, "different prepared bytes"):
            self.store.begin(conflicting)

    def test_closed_decode_rejects_tamper_even_with_recomputed_outer_hashes(self) -> None:
        prepared, _ = self._operation(candidate_id="candidate:tamper", passed=True)
        self.store.begin(prepared)
        path = self.store.root / "RECORDS" / f"op-{prepared.operation_sha256}" / "PREPARED.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["value"]["unknown_injected_field"] = "tamper"
        payload["value_sha256"] = canonical_sha256(payload["value"])
        body = {key: payload[key] for key in payload if key != "artifact_sha256"}
        payload["artifact_sha256"] = canonical_sha256(body)
        path.write_text(canonical_json(payload) + "\n", encoding="utf-8", newline="\n")

        with self.assertRaisesRegex(StateConflictError, "prepared artifact is invalid"):
            ProtectedAdultOperationStore(self.runtime_root).lookup(
                request_id=prepared.request_id,
                candidate_id=prepared.candidate_id,
            )

    def test_closed_record_rejects_unknown_artifact(self) -> None:
        prepared, _ = self._operation(candidate_id="candidate:unknown-file", passed=True)
        self.store.begin(prepared)
        operation_root = self.store.root / "RECORDS" / f"op-{prepared.operation_sha256}"
        (operation_root / "UNEXPECTED.json").write_text("{}\n", encoding="utf-8")

        with self.assertRaisesRegex(StateConflictError, "unknown artifact"):
            ProtectedAdultOperationStore(self.runtime_root).lookup(
                request_id=prepared.request_id,
                candidate_id=prepared.candidate_id,
            )

    def test_safe_summary_and_paths_never_expose_exact_adult_content(self) -> None:
        prepared, outcome = self._operation(candidate_id="candidate:privacy", passed=True)
        self.store.begin(prepared)
        self.store.record_execution(outcome)
        summary = self.store.safe_summary(
            request_id=prepared.request_id,
            candidate_id=prepared.candidate_id,
        )
        rendered = canonical_json(summary)

        self.assertNotIn(PROTECTED_PROSE, rendered)
        self.assertNotIn(PROTECTED_A, rendered)
        self.assertNotIn(prepared.scene_request.exact_current_source, rendered)
        self.assertEqual(
            summary.protected_exact_story_prose_sha256,
            text_sha256(PROTECTED_PROSE),
        )
        for path in self.runtime_root.rglob("*"):
            if path.is_file():
                self.assertTrue(path.is_relative_to(self.store.root))
        self.assertFalse((self.runtime_root / "ORDINARY").exists())

    def test_one_complete_repair_links_frozen_successor_without_merge(self) -> None:
        original, rejected = self._operation(
            candidate_id="candidate:repair-original",
            passed=False,
        )
        successor, passed = self._operation(
            candidate_id="candidate:repair-successor",
            passed=True,
        )
        self.assertEqual(original.scene_request, successor.scene_request)
        self.store.begin(original)
        self.store.record_execution(rejected)

        begin = self.store.begin_repair(
            request_id=original.request_id,
            candidate_id=original.candidate_id,
            successor=successor,
            repair_request_sha256=text_sha256("one-complete-repair"),
        )
        parent = self.store.lookup(
            request_id=original.request_id,
            candidate_id=original.candidate_id,
        )
        child = self.store.lookup(
            request_id=successor.request_id,
            candidate_id=successor.candidate_id,
        )

        self.assertTrue(begin.created)
        self.assertEqual(parent.state, AdultOperationReviewState.REPAIRED)
        self.assertIsNotNone(parent.repair)
        self.assertIsNotNone(child.predecessor_repair)
        self.assertEqual(child.state, AdultOperationReviewState.PREPARED)
        resolved = ProtectedAdultOperationController(self.store).execute_new(
            begin,
            execute=lambda value: passed if value == successor else rejected,
        )
        self.assertEqual(resolved.record.state, AdultOperationReviewState.EXECUTED_PASSED)

        replayed = self.store.begin_repair(
            request_id=original.request_id,
            candidate_id=original.candidate_id,
            successor=successor,
            repair_request_sha256=text_sha256("one-complete-repair"),
        )
        self.assertFalse(replayed.created)

    def test_second_or_partial_repair_is_rejected_before_new_claim(self) -> None:
        original, rejected = self._operation(
            candidate_id="candidate:repair-one",
            passed=False,
        )
        successor, successor_rejected = self._operation(
            candidate_id="candidate:repair-two",
            passed=False,
        )
        third, _ = self._operation(candidate_id="candidate:repair-three", passed=True)
        self.store.begin(original)
        self.store.record_execution(rejected)
        begin = self.store.begin_repair(
            request_id=original.request_id,
            candidate_id=original.candidate_id,
            successor=successor,
            repair_request_sha256=text_sha256("first-repair"),
        )
        ProtectedAdultOperationController(self.store).execute_new(
            begin,
            execute=lambda value: successor_rejected,
        )

        with self.assertRaisesRegex(StateConflictError, "another repair"):
            self.store.begin_repair(
                request_id=original.request_id,
                candidate_id=original.candidate_id,
                successor=third,
                repair_request_sha256=text_sha256("second-repair"),
            )
        self.assertIsNone(
            self.store.lookup_optional(
                request_id=third.request_id,
                candidate_id=third.candidate_id,
            )
        )
        with self.assertRaisesRegex(StateConflictError, "cannot be repaired again"):
            self.store.begin_repair(
                request_id=successor.request_id,
                candidate_id=successor.candidate_id,
                successor=third,
                repair_request_sha256=text_sha256("repair-of-repair"),
            )

        changed_request = replace(
            third.scene_request,
            accepted_safe_continuity="A different frozen safe projection.",
        )
        partial = PreparedAdultRouteOperationV1.create(
            request_id=successor.request_id,
            candidate_id="candidate:partial-repair",
            route_state=successor.route_state,
            scene_request=changed_request,
        )
        with self.assertRaisesRegex(StateConflictError, "cannot be repaired again"):
            self.store.begin_repair(
                request_id=successor.request_id,
                candidate_id=successor.candidate_id,
                successor=partial,
                repair_request_sha256=text_sha256("partial-repair"),
            )

    def test_repair_restart_fails_closed_when_successor_artifact_is_missing(self) -> None:
        original, rejected = self._operation(
            candidate_id="candidate:repair-missing-original",
            passed=False,
        )
        successor, _ = self._operation(
            candidate_id="candidate:repair-missing-successor",
            passed=True,
        )
        self.store.begin(original)
        self.store.record_execution(rejected)
        self.store.begin_repair(
            request_id=original.request_id,
            candidate_id=original.candidate_id,
            successor=successor,
            repair_request_sha256=text_sha256("missing-successor-repair"),
        )
        successor_prepared = (
            self.store.root / "RECORDS" / f"op-{successor.operation_sha256}" / "PREPARED.json"
        )
        successor_prepared.unlink()
        successor_prepared.parent.rmdir()

        with self.assertRaisesRegex(StateConflictError, "successor operation is missing"):
            ProtectedAdultOperationStore(self.runtime_root).lookup(
                request_id=original.request_id,
                candidate_id=original.candidate_id,
            )


if __name__ == "__main__":
    unittest.main()
