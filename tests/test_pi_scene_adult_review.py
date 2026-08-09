from __future__ import annotations

import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from typing import cast
from unittest.mock import patch

from cera.adult_pipeline.contracts import (
    AdultContextFactV1,
    AdultFilterConflictClass,
    AdultNextRoute,
    AdultProviderRole,
    AdultRouteStateSnapshotV1,
)
from cera.adult_pipeline.craft_catalog import CatalogAdultCraftRetrieval
from cera.adult_pipeline.integration import AdultPipelineIntegrationV1
from cera.adult_pipeline.pi_roles import (
    AdultRoleViewContextV1,
    PiDeepSeekAdultFilterPort,
    PiDeepSeekAdultScenePort,
    StructuredAdultRoleResultV1,
)
from cera.adult_pipeline.pipeline import AdultPipeline
from cera.adult_pipeline.preparation import build_adult_turn_preparation_builder
from cera.errors import StateConflictError
from cera.pi_scene.adult_operation_contracts import AdultOperationReviewState
from cera.pi_scene.adult_operation_store import ProtectedAdultOperationStore
from cera.pi_scene.adult_orchestration import (
    AdultRouteOperationOutcomeV1,
    AutomaticAdultRouteOrchestrator,
    PreparedAdultRouteOperationV1,
)
from cera.pi_scene.adult_review import (
    AdultAutomaticRepairDisposition,
    AdultProvisionalAcceptanceDisposition,
    AdultRejectedReviewController,
    adult_regenerate_candidate_id,
)
from cera.pi_scene.writer_view import WriterViewMaterializer
from cera.provider_dispatch_guard import PROVIDER_DISPATCH_DISABLED_ENV
from cera.serialization import canonical_json, text_sha256
from tests.test_adult_pipeline_pi_integration import (
    PROTECTED_PROSE,
    _FakeStructuredTransport,
)
from tests.test_adult_turn_preparation import CATALOG_ROOT, PROTECTED_A, _turn
from tests.test_pi_scene_adult_orchestration import _RouteState


class _ConflictTransport(_FakeStructuredTransport):
    def __init__(self, conflict_class: AdultFilterConflictClass) -> None:
        super().__init__()
        self.conflict_class = conflict_class

    def invoke_structured_role(self, **kwargs: object) -> StructuredAdultRoleResultV1:
        result = cast(
            StructuredAdultRoleResultV1,
            super().invoke_structured_role(**kwargs),  # type: ignore[no-untyped-call]
        )
        if kwargs["role"] is not AdultProviderRole.FILTER:
            return result
        return replace(
            result,
            raw_json=canonical_json(
                {
                    "verdict": "reject",
                    "conflict": {
                        "conflict_class": self.conflict_class.value,
                        "concise_explanation": "The frozen candidate conflicts with authority.",
                        "decision_key": "decision_one",
                        "exact_quote": None,
                    },
                }
            ),
        )


class _LocalCreatorAuthorizer:
    def __init__(self, allowed: bool) -> None:
        self.allowed = allowed
        self.calls: list[tuple[str, str, str]] = []

    def authorize_protected_candidate(
        self,
        *,
        review_id: str,
        request_id: str,
        candidate_id: str,
    ) -> bool:
        self.calls.append((review_id, request_id, candidate_id))
        return self.allowed


class AdultRejectedReviewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = ProtectedAdultOperationStore(self.root / "protected-runtime")

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
            source_promotion_sha256=text_sha256("accepted-promotion"),
        )

    @staticmethod
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

    def _operation(
        self,
        *,
        candidate_id: str,
        conflict_class: AdultFilterConflictClass | None,
    ) -> tuple[PreparedAdultRouteOperationV1, AdultRouteOperationOutcomeV1]:
        transport = (
            _FakeStructuredTransport()
            if conflict_class is None
            else _ConflictTransport(conflict_class)
        )
        fixture_root = self.root / "fixture" / text_sha256(candidate_id)[:12]
        context = AdultRoleViewContextV1(
            world_id="world:test",
            branch_id="branch:test",
            scene_id="scene:test",
            turn_id="turn:adult-review",
            candidate_id=candidate_id,
            current_state={"location": "private room"},
            characters={"character:hana": {"name": "Hana", "age": 38}},
        )
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
        orchestrator = AutomaticAdultRouteOrchestrator(
            route_state=_RouteState(self._snapshot()),
            preparation_builder=build_adult_turn_preparation_builder(integration.craft_retrieval),
            pipeline=integration,
        )
        prepared = orchestrator.prepare(
            request_id="request:adult-review",
            candidate_id=candidate_id,
            turn_input=_turn(craft_mode="off"),
            accepted_safe_projection="The accepted private interaction remains current.",
            protected_adult_continuity=PROTECTED_A,
            current_facts=self._facts(),
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

    def _record_rejection(
        self,
        *,
        candidate_id: str,
        conflict_class: AdultFilterConflictClass = AdultFilterConflictClass.LOGIC_NOT_REALIZED,
    ) -> PreparedAdultRouteOperationV1:
        prepared, outcome = self._operation(
            candidate_id=candidate_id,
            conflict_class=conflict_class,
        )
        self.store.begin(prepared)
        self.store.record_execution(outcome)
        return prepared

    def test_stable_safe_review_and_local_only_exact_candidate_view(self) -> None:
        prepared = self._record_rejection(candidate_id="candidate:review-local")
        denied = _LocalCreatorAuthorizer(False)
        controller = AdultRejectedReviewController(
            self.store,
            local_creator_authorizer=denied,
        )
        first = controller.safe_review(
            request_id=prepared.request_id,
            candidate_id=prepared.candidate_id,
        )
        restarted = AdultRejectedReviewController(
            ProtectedAdultOperationStore(self.root / "protected-runtime"),
            local_creator_authorizer=_LocalCreatorAuthorizer(True),
        )
        replayed = restarted.safe_review(
            request_id=prepared.request_id,
            candidate_id=prepared.candidate_id,
        )

        self.assertEqual(first, replayed)
        safe_json = canonical_json(first)
        self.assertNotIn(PROTECTED_PROSE, safe_json)
        self.assertNotIn(PROTECTED_A, safe_json)
        self.assertNotIn(prepared.scene_request.exact_current_source, safe_json)
        with self.assertRaisesRegex(StateConflictError, "local creator access"):
            controller.protected_candidate(
                review_id=first.review_id,
                request_id=prepared.request_id,
                candidate_id=prepared.candidate_id,
            )
        exact = restarted.protected_candidate(
            review_id=first.review_id,
            request_id=prepared.request_id,
            candidate_id=prepared.candidate_id,
        )
        self.assertEqual(exact.exact_story_prose, PROTECTED_PROSE)
        self.assertEqual(exact.exact_story_prose_sha256, text_sha256(PROTECTED_PROSE))

    def test_decline_is_hash_bound_idempotent_and_keeps_review_identity(self) -> None:
        prepared = self._record_rejection(candidate_id="candidate:decline")
        controller = AdultRejectedReviewController(self.store)
        review = controller.safe_review(
            request_id=prepared.request_id,
            candidate_id=prepared.candidate_id,
        )

        first = controller.decline(
            review_id=review.review_id,
            request_id=prepared.request_id,
            candidate_id=prepared.candidate_id,
        )
        second = controller.decline(
            review_id=review.review_id,
            request_id=prepared.request_id,
            candidate_id=prepared.candidate_id,
        )

        self.assertEqual(first, second)
        self.assertEqual(first.review_id, review.review_id)
        self.assertEqual(first.operation.state, AdultOperationReviewState.DECLINED)
        record = self.store.lookup(
            request_id=prepared.request_id,
            candidate_id=prepared.candidate_id,
        )
        self.assertIsNotNone(record.decision)
        with self.assertRaisesRegex(StateConflictError, "cannot be regenerated"):
            controller.regenerate(
                review_id=review.review_id,
                request_id=prepared.request_id,
                candidate_id=prepared.candidate_id,
                execute=lambda _value: (_ for _ in ()).throw(
                    AssertionError("declined candidate dispatched")
                ),
            )

    def test_regenerate_is_one_fresh_complete_feedback_free_successor_and_replays(self) -> None:
        prepared = self._record_rejection(candidate_id="candidate:regenerate")
        controller = AdultRejectedReviewController(self.store)
        review = controller.safe_review(
            request_id=prepared.request_id,
            candidate_id=prepared.candidate_id,
        )
        successor_id = adult_regenerate_candidate_id(review.review_id)
        expected_prepared, passed = self._operation(
            candidate_id=successor_id,
            conflict_class=None,
        )
        self.assertEqual(expected_prepared.scene_request, prepared.scene_request)
        execute_calls = 0

        def execute(value: PreparedAdultRouteOperationV1) -> AdultRouteOperationOutcomeV1:
            nonlocal execute_calls
            execute_calls += 1
            self.assertEqual(value, expected_prepared)
            return passed

        first = controller.regenerate(
            review_id=review.review_id,
            request_id=prepared.request_id,
            candidate_id=prepared.candidate_id,
            execute=execute,
        )
        replayed = controller.regenerate(
            review_id=review.review_id,
            request_id=prepared.request_id,
            candidate_id=prepared.candidate_id,
            execute=lambda _value: (_ for _ in ()).throw(
                AssertionError("completed Regenerate redispatched")
            ),
        )

        self.assertEqual(execute_calls, 1)
        self.assertFalse(first.replayed)
        self.assertTrue(replayed.replayed)
        self.assertEqual(first.successor_operation, replayed.successor_operation)
        self.assertEqual(
            first.successor_operation.state,
            AdultOperationReviewState.EXECUTED_PASSED,
        )
        self.assertIsNone(first.successor_rejected_review)
        parent = self.store.lookup(
            request_id=prepared.request_id,
            candidate_id=prepared.candidate_id,
        )
        self.assertEqual(parent.state, AdultOperationReviewState.REPAIRED)
        assert parent.repair is not None
        self.assertNotEqual(
            parent.repair.predecessor_operation_id, parent.repair.successor_operation_id
        )

    def test_automatic_repair_is_critical_only_and_cannot_repair_successor(self) -> None:
        quality = self._record_rejection(candidate_id="candidate:quality-only")
        controller = AdultRejectedReviewController(self.store)
        quality_review = controller.safe_review(
            request_id=quality.request_id,
            candidate_id=quality.candidate_id,
        )
        skipped = controller.automatic_repair_if_critical(
            review_id=quality_review.review_id,
            request_id=quality.request_id,
            candidate_id=quality.candidate_id,
            execute=lambda _value: (_ for _ in ()).throw(
                AssertionError("quality-only rejection dispatched")
            ),
        )
        self.assertEqual(
            skipped.disposition,
            AdultAutomaticRepairDisposition.CREATOR_REVIEW_REQUIRED,
        )

        critical = self._record_rejection(
            candidate_id="candidate:critical",
            conflict_class=AdultFilterConflictClass.CURRENT_DATA_CONFLICT,
        )
        critical_review = controller.safe_review(
            request_id=critical.request_id,
            candidate_id=critical.candidate_id,
        )
        successor_id = adult_regenerate_candidate_id(critical_review.review_id)
        expected_prepared, rejected_again = self._operation(
            candidate_id=successor_id,
            conflict_class=AdultFilterConflictClass.CURRENT_DATA_CONFLICT,
        )
        repaired = controller.automatic_repair_if_critical(
            review_id=critical_review.review_id,
            request_id=critical.request_id,
            candidate_id=critical.candidate_id,
            execute=lambda value: (
                rejected_again
                if value == expected_prepared
                else (_ for _ in ()).throw(AssertionError("repair preparation changed"))
            ),
        )
        self.assertEqual(
            repaired.disposition,
            AdultAutomaticRepairDisposition.AUTO_REPAIRED,
        )
        assert repaired.regenerate is not None
        successor_review = repaired.regenerate.successor_rejected_review
        assert successor_review is not None
        limited = controller.automatic_repair_if_critical(
            review_id=successor_review.review_id,
            request_id=critical.request_id,
            candidate_id=successor_id,
            execute=lambda _value: (_ for _ in ()).throw(
                AssertionError("repair successor redispatched")
            ),
        )
        self.assertEqual(
            limited.disposition,
            AdultAutomaticRepairDisposition.REPAIR_LIMIT_REACHED,
        )

    def test_provisional_acceptance_requires_reprojection_and_creates_no_effect(self) -> None:
        prepared = self._record_rejection(candidate_id="candidate:provisional")
        controller = AdultRejectedReviewController(self.store)
        review = controller.safe_review(
            request_id=prepared.request_id,
            candidate_id=prepared.candidate_id,
        )
        before = self.store.lookup(
            request_id=prepared.request_id,
            candidate_id=prepared.candidate_id,
        )
        blocked = controller.provisional_acceptance_disposition(
            review_id=review.review_id,
            request_id=prepared.request_id,
            candidate_id=prepared.candidate_id,
        )
        after = self.store.lookup(
            request_id=prepared.request_id,
            candidate_id=prepared.candidate_id,
        )

        self.assertEqual(before, after)
        self.assertEqual(
            blocked.disposition,
            AdultProvisionalAcceptanceDisposition.REPROJECTION_REQUIRED,
        )
        self.assertFalse(blocked.accepted_effect_created)
        self.assertEqual(
            blocked.required_artifacts,
            (
                "protected_full_record",
                "non_explicit_codex_projection",
                "route_transition",
            ),
        )
        self.assertEqual(after.state, AdultOperationReviewState.EXECUTED_REJECTED)

    def test_wrong_review_identity_is_rejected_before_action(self) -> None:
        prepared = self._record_rejection(candidate_id="candidate:wrong-review")
        controller = AdultRejectedReviewController(self.store)
        with self.assertRaisesRegex(StateConflictError, "review identity is invalid"):
            controller.decline(
                review_id="adult-review:" + "0" * 64,
                request_id=prepared.request_id,
                candidate_id=prepared.candidate_id,
            )
        self.assertEqual(
            self.store.lookup(
                request_id=prepared.request_id,
                candidate_id=prepared.candidate_id,
            ).state,
            AdultOperationReviewState.EXECUTED_REJECTED,
        )


if __name__ == "__main__":
    unittest.main()
