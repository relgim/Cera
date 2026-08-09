from __future__ import annotations

import os
import tempfile
import unittest
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import cast

from cera.adult_pipeline.acceptance import AdultIntegratedExecutionV1
from cera.adult_pipeline.contracts import (
    AdultContextFactV1,
    AdultEntryReason,
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
from cera.cognition.contracts import CognitionPlanV1
from cera.errors import ContractValidationError, StateConflictError
from cera.pi_scene.adult_orchestration import (
    AdultRouteOperationOutcomeV1,
    AutomaticAdultRouteOrchestrator,
    PassedAdultRouteOperationV1,
    PreparedAdultRouteOperationV1,
    RejectedAdultRouteOperationV1,
    classify_prepared_adult_execution,
)
from cera.pi_scene.review_store import LeanSceneTurnInputV1
from cera.pi_scene.writer_view import WriterViewMaterializer
from cera.provider_dispatch_guard import PROVIDER_DISPATCH_DISABLED_ENV
from cera.schema import from_mapping
from cera.serialization import canonical_json, text_sha256, to_primitive
from tests.test_adult_pipeline_pi_integration import (
    PROTECTED_PROSE,
    _FakeStructuredTransport,
)
from tests.test_adult_turn_preparation import CATALOG_ROOT, PROTECTED_A, _plan, _turn


def _adult_facts() -> tuple[AdultContextFactV1, ...]:
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
            authoritative_fact="Hana carries private current context.",
            visibility="adult_role_private",
        ),
    )


class _RouteState:
    def __init__(self, snapshot: AdultRouteStateSnapshotV1) -> None:
        self.snapshot = snapshot
        self.calls = 0

    def current_logic_route(
        self,
        *,
        world_id: str,
        branch_id: str,
    ) -> AdultRouteStateSnapshotV1:
        self.calls += 1
        if world_id != self.snapshot.world_id or branch_id != self.snapshot.branch_id:
            raise AssertionError("orchestrator requested another branch")
        return self.snapshot


class _CognitionProbe:
    def __init__(self, plan: CognitionPlanV1, *, fail_if_called: bool = False) -> None:
        self.plan = plan
        self.fail_if_called = fail_if_called
        self.calls = 0

    def __call__(self) -> CognitionPlanV1:
        self.calls += 1
        if self.fail_if_called:
            raise AssertionError("accepted adult continuation called Codex")
        return self.plan


class _PipelineSpy:
    def __init__(self, integration: AdultPipelineIntegrationV1) -> None:
        self.integration = integration
        self.prepare_calls = 0
        self.execute_calls = 0

    def prepare_scene_request(self, source):  # type: ignore[no-untyped-def]
        self.prepare_calls += 1
        return self.integration.prepare_scene_request(source)

    def execute(self, **kwargs):  # type: ignore[no-untyped-def]
        self.execute_calls += 1
        return self.integration.execute(**kwargs)


class _RejectingTransport(_FakeStructuredTransport):
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
                        "conflict_class": "logic_not_realized",
                        "concise_explanation": "The material decision was not realized.",
                        "decision_key": "decision_one",
                        "exact_quote": None,
                    },
                }
            ),
        )


class AdultRouteOrchestrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    @staticmethod
    def _snapshot(route: AdultNextRoute) -> AdultRouteStateSnapshotV1:
        accepted = route is AdultNextRoute.ADULT
        return AdultRouteStateSnapshotV1(
            schema_version=AdultRouteStateSnapshotV1.SCHEMA_VERSION,
            world_id="world:test",
            branch_id="branch:test",
            accepted_head_sha256=text_sha256("accepted-head") if accepted else None,
            current_logic_route=route,
            source_promotion_sha256=(text_sha256("accepted-adult-promotion") if accepted else None),
        )

    def _integration(
        self,
        *,
        transport: _FakeStructuredTransport | None = None,
    ) -> AdultPipelineIntegrationV1:
        selected_transport = transport or _FakeStructuredTransport()
        context = AdultRoleViewContextV1(
            world_id="world:test",
            branch_id="branch:test",
            scene_id="scene:test",
            turn_id="turn:adult-test",
            candidate_id="candidate:adult-test",
            current_state={"location": "private room"},
            characters={"character:hana": {"name": "Hana", "age": 38}},
        )
        materializer = WriterViewMaterializer(self.root / "protected" / "views")
        session_root = self.root / "protected" / "sessions"
        scene = PiDeepSeekAdultScenePort(
            transport=selected_transport,
            materializer=materializer,
            context=context,
            session_root=session_root,
        )
        filter_port = PiDeepSeekAdultFilterPort(
            transport=selected_transport,
            materializer=materializer,
            context=context,
            session_root=session_root,
        )
        return AdultPipelineIntegrationV1(
            pipeline=AdultPipeline(scene=scene, filter_port=filter_port),
            scene_port=scene,
            filter_port=filter_port,
            craft_retrieval=CatalogAdultCraftRetrieval(CATALOG_ROOT),
        )

    def _orchestrator(
        self,
        route: AdultNextRoute,
        *,
        transport: _FakeStructuredTransport | None = None,
    ) -> tuple[AutomaticAdultRouteOrchestrator, _RouteState, _PipelineSpy]:
        integration = self._integration(transport=transport)
        route_state = _RouteState(self._snapshot(route))
        pipeline = _PipelineSpy(integration)
        return (
            AutomaticAdultRouteOrchestrator(
                route_state=route_state,
                preparation_builder=build_adult_turn_preparation_builder(
                    integration.craft_retrieval
                ),
                pipeline=pipeline,
            ),
            route_state,
            pipeline,
        )

    @staticmethod
    def _prepare(
        orchestrator: AutomaticAdultRouteOrchestrator,
        *,
        turn: LeanSceneTurnInputV1 | None = None,
        cognition: Callable[[], CognitionPlanV1] | None = None,
    ) -> PreparedAdultRouteOperationV1:
        return orchestrator.prepare(
            request_id="request:adult-test",
            candidate_id="candidate:adult-test",
            turn_input=turn or _turn(craft_mode="off"),
            accepted_safe_projection="The accepted private interaction remains current.",
            protected_adult_continuity=PROTECTED_A,
            current_facts=_adult_facts(),
            product_story_boundaries=("Preserve accepted branch authority.",),
            cognition_handoff_factory=cognition,
        )

    @staticmethod
    def _execute(
        orchestrator: AutomaticAdultRouteOrchestrator,
        prepared: PreparedAdultRouteOperationV1,
    ) -> AdultRouteOperationOutcomeV1:
        with unittest.mock.patch.dict(
            os.environ,
            {PROVIDER_DISPATCH_DISABLED_ENV: "1"},
            clear=False,
        ):
            return orchestrator.execute_prepared(prepared)

    def test_accepted_adult_route_bypasses_codex_and_runs_scene_then_filter(self) -> None:
        transport = _FakeStructuredTransport()
        orchestrator, route_state, pipeline = self._orchestrator(
            AdultNextRoute.ADULT,
            transport=transport,
        )
        cognition = _CognitionProbe(_plan(), fail_if_called=True)

        prepared = self._prepare(orchestrator, cognition=cognition)
        outcome = self._execute(orchestrator, prepared)

        self.assertTrue(prepared.bypassed_codex)
        self.assertEqual(
            prepared.scene_request.entry_reason,
            AdultEntryReason.ACCEPTED_ADULT_CONTINUATION,
        )
        self.assertEqual(cognition.calls, 0)
        self.assertIsInstance(outcome, PassedAdultRouteOperationV1)
        assert isinstance(outcome, PassedAdultRouteOperationV1)
        self.assertEqual(outcome.promotion_envelope.exact_story_prose, PROTECTED_PROSE)
        self.assertEqual(
            outcome.protected_exact_story_prose_sha256,
            text_sha256(PROTECTED_PROSE),
        )
        self.assertEqual(
            [value[0] for value in transport.calls],
            [AdultProviderRole.SCENE, AdultProviderRole.FILTER],
        )
        self.assertEqual(route_state.calls, 2)
        self.assertEqual(pipeline.prepare_calls, 1)
        self.assertEqual(pipeline.execute_calls, 1)

    def test_ordinary_route_calls_codex_once_and_preserves_exact_handoff(self) -> None:
        orchestrator, _, _ = self._orchestrator(AdultNextRoute.ORDINARY)
        plan = _plan()
        cognition = _CognitionProbe(plan)

        prepared = self._prepare(orchestrator, cognition=cognition)
        outcome = self._execute(orchestrator, prepared)

        self.assertFalse(prepared.bypassed_codex)
        self.assertEqual(cognition.calls, 1)
        self.assertEqual(
            prepared.scene_request.entry_reason,
            AdultEntryReason.CODEX_ADULT_HANDOFF,
        )
        self.assertEqual(prepared.scene_request.adult_handoff, canonical_json(plan))
        self.assertIsInstance(outcome, PassedAdultRouteOperationV1)

    def test_adult_craft_mode_never_selects_the_route_or_codex_owner(self) -> None:
        orchestrator, _, pipeline = self._orchestrator(AdultNextRoute.ADULT)
        cognition = _CognitionProbe(_plan(), fail_if_called=True)

        prepared = tuple(
            self._prepare(
                orchestrator,
                turn=_turn(craft_mode=mode),
                cognition=cognition,
            )
            for mode in ("off", "on", "ex")
        )

        self.assertEqual(cognition.calls, 0)
        self.assertEqual(pipeline.prepare_calls, 3)
        self.assertTrue(all(value.bypassed_codex for value in prepared))
        self.assertEqual(
            tuple(value.scene_request.retrieved_craft.mode.value for value in prepared),
            ("off", "on", "ex"),
        )
        self.assertEqual(
            {value.scene_request.entry_reason for value in prepared},
            {AdultEntryReason.ACCEPTED_ADULT_CONTINUATION},
        )

    def test_filter_rejection_is_typed_and_exposes_no_promotion_envelope(self) -> None:
        orchestrator, _, _ = self._orchestrator(
            AdultNextRoute.ADULT,
            transport=_RejectingTransport(),
        )
        prepared = self._prepare(
            orchestrator, cognition=_CognitionProbe(_plan(), fail_if_called=True)
        )

        outcome = self._execute(orchestrator, prepared)

        self.assertIsInstance(outcome, RejectedAdultRouteOperationV1)
        assert isinstance(outcome, RejectedAdultRouteOperationV1)
        self.assertEqual(
            outcome.conflict.conflict_class,
            AdultFilterConflictClass.LOGIC_NOT_REALIZED,
        )
        self.assertFalse(hasattr(outcome, "promotion_envelope"))
        self.assertEqual(
            outcome.protected_exact_story_prose_sha256,
            text_sha256(PROTECTED_PROSE),
        )

    def test_route_change_after_preparation_fails_before_scene_or_filter(self) -> None:
        transport = _FakeStructuredTransport()
        orchestrator, route_state, pipeline = self._orchestrator(
            AdultNextRoute.ADULT,
            transport=transport,
        )
        prepared = self._prepare(
            orchestrator, cognition=_CognitionProbe(_plan(), fail_if_called=True)
        )
        route_state.snapshot = AdultRouteStateSnapshotV1(
            schema_version=AdultRouteStateSnapshotV1.SCHEMA_VERSION,
            world_id="world:test",
            branch_id="branch:test",
            accepted_head_sha256=text_sha256("new-head"),
            current_logic_route=AdultNextRoute.ORDINARY,
            source_promotion_sha256=text_sha256("new-promotion"),
        )

        with self.assertRaisesRegex(StateConflictError, "changed after preparation"):
            self._execute(orchestrator, prepared)

        self.assertEqual(pipeline.execute_calls, 0)
        self.assertEqual(transport.calls, [])

    def test_restart_round_trip_reclassifies_exact_execution_without_dispatch(self) -> None:
        orchestrator, _, pipeline = self._orchestrator(AdultNextRoute.ADULT)
        prepared = self._prepare(
            orchestrator, cognition=_CognitionProbe(_plan(), fail_if_called=True)
        )
        outcome = self._execute(orchestrator, prepared)
        assert isinstance(outcome, PassedAdultRouteOperationV1)
        calls_before_replay = pipeline.execute_calls
        recovered_prepared = from_mapping(
            PreparedAdultRouteOperationV1,
            to_primitive(prepared),
        )
        recovered_execution = from_mapping(
            AdultIntegratedExecutionV1,
            to_primitive(outcome.protected_execution),
        )

        replayed = classify_prepared_adult_execution(
            recovered_prepared,
            recovered_execution,
        )

        self.assertIsInstance(replayed, PassedAdultRouteOperationV1)
        self.assertEqual(replayed.outcome_sha256, outcome.outcome_sha256)
        self.assertEqual(pipeline.execute_calls, calls_before_replay)

    def test_cross_branch_execution_cannot_be_replayed(self) -> None:
        orchestrator, _, _ = self._orchestrator(AdultNextRoute.ADULT)
        prepared = self._prepare(
            orchestrator, cognition=_CognitionProbe(_plan(), fail_if_called=True)
        )
        outcome = self._execute(orchestrator, prepared)
        assert isinstance(outcome, PassedAdultRouteOperationV1)
        other_snapshot = replace(prepared.route_state, branch_id="branch:other")
        other_prepared = PreparedAdultRouteOperationV1.create(
            request_id=prepared.request_id,
            candidate_id=prepared.candidate_id,
            route_state=other_snapshot,
            scene_request=prepared.scene_request,
        )

        with self.assertRaisesRegex(StateConflictError, "another prepared operation"):
            classify_prepared_adult_execution(
                other_prepared,
                outcome.protected_execution,
            )

    def test_adult_route_without_accepted_promotion_custody_fails_closed(self) -> None:
        integration = self._integration()
        route_state = _RouteState(
            AdultRouteStateSnapshotV1(
                schema_version=AdultRouteStateSnapshotV1.SCHEMA_VERSION,
                world_id="world:test",
                branch_id="branch:test",
                accepted_head_sha256=None,
                current_logic_route=AdultNextRoute.ADULT,
                source_promotion_sha256=None,
            )
        )
        pipeline = _PipelineSpy(integration)
        orchestrator = AutomaticAdultRouteOrchestrator(
            route_state=route_state,
            preparation_builder=build_adult_turn_preparation_builder(integration.craft_retrieval),
            pipeline=pipeline,
        )
        cognition = _CognitionProbe(_plan(), fail_if_called=True)

        with self.assertRaisesRegex(StateConflictError, "promotion custody"):
            self._prepare(orchestrator, cognition=cognition)

        self.assertEqual(cognition.calls, 0)
        self.assertEqual(pipeline.prepare_calls, 0)
        self.assertEqual(pipeline.execute_calls, 0)

    def test_prepared_operation_tampering_is_rejected(self) -> None:
        orchestrator, _, _ = self._orchestrator(AdultNextRoute.ADULT)
        prepared = self._prepare(
            orchestrator, cognition=_CognitionProbe(_plan(), fail_if_called=True)
        )

        with self.assertRaisesRegex(ContractValidationError, "binding changed"):
            replace(prepared, operation_sha256=text_sha256("tampered"))


if __name__ == "__main__":
    unittest.main()
