from __future__ import annotations

import json
import os
import tempfile
import unittest
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from cera.adult_pipeline.contracts import (
    AdultContextFactV1,
    AdultFilterConflictClass,
    AdultNextRoute,
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
from cera.pi_scene.adult_operation_store import (
    ProtectedAdultOperationController,
    ProtectedAdultOperationStore,
)
from cera.pi_scene.adult_orchestration import (
    AdultRouteOperationOutcomeV1,
    AutomaticAdultRouteOrchestrator,
    PreparedAdultRouteOperationV1,
    classify_prepared_adult_execution,
)
from cera.pi_scene.contracts import SceneRoute
from cera.pi_scene.full_model_controller import (
    AcceptedAdultTurnV1,
    AdultExecutionContextV1,
    FullModelSceneController,
)
from cera.pi_scene.http import PiSceneCommittedStateError, PiSceneHttpAdapter
from cera.pi_scene.http_contracts import (
    PI_SCENE_ADULT_MODEL,
    PI_SCENE_AUTO_MODEL,
    PI_SCENE_PROFILE,
)
from cera.pi_scene.request_journal import PiSceneRequestJournal
from cera.pi_scene.review_store import LeanSceneTurnInputV1
from cera.pi_scene.runtime import LeanPiSceneCoordinator, PlannerTurnOutputV1
from cera.pi_scene.store import LeanSceneStore
from cera.pi_scene.writer_view import WriterViewMaterializer
from cera.provider_dispatch_guard import PROVIDER_DISPATCH_DISABLED_ENV
from cera.semantic_validation import SemanticVerdict
from cera.serialization import canonical_json, to_primitive

from . import test_pi_scene_atomic_adult_store as atomic_support
from .test_adult_pipeline_pi_integration import (
    PROTECTED_PROSE,
    _FakeStructuredTransport,
)
from .test_adult_turn_preparation import CATALOG_ROOT, _plan, _turn
from .test_cognition_contracts import _plan as _ordinary_plan
from .test_pi_scene_adult_orchestration import _RejectingTransport
from .test_pi_scene_adult_review import _ConflictTransport
from .test_pi_scene_lean_v1 import FakePi
from .test_pi_scene_lean_v1 import turn as lean_turn
from .test_pi_scene_semantic_runtime import _SemanticValidator


class _HandoffPlanner:
    external_provider_boundary = False

    def __init__(self) -> None:
        self.calls = 0

    def plan(self, _request):  # type: ignore[no-untyped-def]
        self.calls += 1
        value = to_primitive(_plan())
        return PlannerTurnOutputV1(
            sequence=value["sequence"],
            decision_bundle=value,
            provider_operations=1,
        )


class _NeverPlanner:
    external_provider_boundary = False

    def plan(self, _request):  # type: ignore[no-untyped-def]
        raise AssertionError("accepted adult continuation invoked Codex")


class _OrdinaryPlanner:
    external_provider_boundary = False

    def __init__(self) -> None:
        self.calls = 0

    def plan(self, _request):  # type: ignore[no-untyped-def]
        self.calls += 1
        value = to_primitive(_ordinary_plan())
        return PlannerTurnOutputV1(
            sequence=value["sequence"],
            decision_bundle=value,
            provider_operations=1,
        )


class _NeverFullModelController:
    def complete(self, **_kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("explicit compatibility route entered full-model routing")


class _AdultContinuingTransport(_FakeStructuredTransport):
    def invoke_structured_role(self, **kwargs):  # type: ignore[no-untyped-def]
        result = super().invoke_structured_role(**kwargs)
        if kwargs["role"].value != "adult_scene":
            return result
        payload = json.loads(result.raw_json)
        payload["next_route"] = "adult"
        payload["next_route_reason"] = "The accepted adult sequence remains active."
        return replace(result, raw_json=canonical_json(payload))


def _controller_facts() -> tuple[AdultContextFactV1, ...]:
    return (
        AdultContextFactV1(
            evidence_ref="evidence:public",
            subject_id="character:hana",
            authoritative_fact="Hana is present in the current room.",
            visibility="public",
        ),
        AdultContextFactV1(
            evidence_ref="evidence:private",
            subject_id="character:hana",
            authoritative_fact="Hana carries private current context.",
            visibility="adult_role_private",
        ),
    )


class PiSceneFullModelControllerTests(unittest.TestCase):
    def _coordinator(
        self,
        root: Path,
        *,
        store: LeanSceneStore,
        planner,
    ) -> tuple[LeanPiSceneCoordinator, FakePi]:  # type: ignore[no-untyped-def]
        pi = FakePi()
        return (
            LeanPiSceneCoordinator(
                store=store,
                planner=planner,
                writer_views=WriterViewMaterializer(root / "ordinary-views"),
                pi=pi,
                session_root=root / "ordinary-sessions",
                semantic_validator=_SemanticValidator(SemanticVerdict.PASS),
            ),
            pi,
        )

    def _adult_factory(
        self,
        root: Path,
        store: LeanSceneStore,
        transports: list[_FakeStructuredTransport],
        transport_factory=None,  # type: ignore[no-untyped-def]
    ):
        def build(turn, _request_id, candidate_id):  # type: ignore[no-untyped-def]
            selected_factory = _FakeStructuredTransport if transport_factory is None else transport_factory
            transport = selected_factory()
            transports.append(transport)
            context = AdultRoleViewContextV1(
                world_id=turn.world_id,
                branch_id=turn.branch_id,
                scene_id=turn.scene_id,
                turn_id=f"turn:pending:{candidate_id[-12:]}",
                candidate_id=candidate_id,
                current_state=dict(turn.current_state),
                characters={key: dict(value) for key, value in turn.characters.items()},
            )
            materializer = WriterViewMaterializer(root / "protected" / candidate_id[-16:])
            session_root = root / "protected-sessions" / candidate_id[-16:]
            scene = PiDeepSeekAdultScenePort(
                transport=transport,
                materializer=materializer,
                context=context,
                session_root=session_root,
                accepted_parent_session=store.load_accepted_pi_session(
                    world_id=turn.world_id,
                    branch_id=turn.branch_id,
                ),
            )
            filter_port = PiDeepSeekAdultFilterPort(
                transport=transport,
                materializer=materializer,
                context=context,
                session_root=session_root,
            )
            integration = AdultPipelineIntegrationV1(
                pipeline=AdultPipeline(scene=scene, filter_port=filter_port),
                scene_port=scene,
                filter_port=filter_port,
                craft_retrieval=CatalogAdultCraftRetrieval(CATALOG_ROOT),
            )
            return AutomaticAdultRouteOrchestrator(
                route_state=store,
                preparation_builder=build_adult_turn_preparation_builder(
                    integration.craft_retrieval
                ),
                pipeline=integration,
            )

        return build

    def _adult_regeneration_executor(
        self,
        root: Path,
        *,
        transport_factory=_FakeStructuredTransport,  # type: ignore[no-untyped-def]
    ) -> tuple[
        Callable[
            [PreparedAdultRouteOperationV1, LeanSceneTurnInputV1],
            AdultRouteOperationOutcomeV1,
        ],
        list[_FakeStructuredTransport],
    ]:
        transports: list[_FakeStructuredTransport] = []

        def execute(
            prepared: PreparedAdultRouteOperationV1,
            frozen_turn: LeanSceneTurnInputV1,
        ) -> AdultRouteOperationOutcomeV1:
            transport = transport_factory()
            transports.append(transport)
            context = AdultRoleViewContextV1(
                world_id=frozen_turn.world_id,
                branch_id=frozen_turn.branch_id,
                scene_id=frozen_turn.scene_id,
                turn_id=f"turn:regenerate:{prepared.candidate_id[-12:]}",
                candidate_id=prepared.candidate_id,
                current_state=dict(frozen_turn.current_state),
                characters={
                    key: dict(value) for key, value in frozen_turn.characters.items()
                },
                relationships={
                    key: dict(value) for key, value in frozen_turn.relationships.items()
                },
                recent_prose=tuple(frozen_turn.recent_prose),
                relevant_memories={
                    key: dict(value)
                    for key, value in frozen_turn.relevant_memories.items()
                },
                voice_examples=dict(frozen_turn.voice_examples),
                accepted_records=(),
            )
            scope = prepared.operation_sha256[:20]
            materializer = WriterViewMaterializer(root / "regen" / scope / "views")
            session_root = root / "regen" / scope / "sessions"
            scene = PiDeepSeekAdultScenePort(
                transport=transport,
                materializer=materializer,
                context=context,
                session_root=session_root,
                accepted_parent_session=None,
            )
            filter_port = PiDeepSeekAdultFilterPort(
                transport=transport,
                materializer=materializer,
                context=context,
                session_root=session_root,
            )
            integration = AdultPipelineIntegrationV1(
                pipeline=AdultPipeline(scene=scene, filter_port=filter_port),
                scene_port=scene,
                filter_port=filter_port,
                craft_retrieval=CatalogAdultCraftRetrieval(CATALOG_ROOT),
            )
            protected = integration.execute(
                request_id=prepared.request_id,
                candidate_id=prepared.candidate_id,
                world_id=prepared.route_state.world_id,
                branch_id=prepared.route_state.branch_id,
                accepted_head_sha256=prepared.route_state.accepted_head_sha256,
                scene_request=prepared.scene_request,
            )
            return classify_prepared_adult_execution(prepared, protected)

        return execute, transports

    @staticmethod
    def _http_payload() -> dict[str, object]:
        return {
            "model": PI_SCENE_AUTO_MODEL,
            "messages": [
                {
                    "role": "user",
                    "content": (
                        "Continue the buildup with a slow rub, an action-bound sound, "
                        "and a clear aftermath."
                    ),
                }
            ],
            "stream": False,
            "cera_profile_id": PI_SCENE_PROFILE,
            "cera_session_id": "session-adult-preparation",
            "cera_character_autonomy": "BOTH",
            "cera_scene_depth": "MEDIUM",
            "cera_adult_craft_mode": "EX",
        }

    def test_ordinary_plan_uses_one_codex_call_then_writer_validator_recorder(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path(r"D:\Cera\tmp")) as temporary:
            root = Path(temporary)
            store = LeanSceneStore(root / "world")
            planner = _OrdinaryPlanner()
            ordinary, ordinary_pi = self._coordinator(root, store=store, planner=planner)

            def no_adult(*_args):  # type: ignore[no-untyped-def]
                raise AssertionError("ordinary plan entered the adult pipeline")

            controller = FullModelSceneController(
                ordinary=ordinary,
                store=store,
                adult_orchestrator_factory=no_adult,
                adult_context_provider=lambda _turn, _route: AdultExecutionContextV1(
                    accepted_safe_projection="The accepted public scene remains current.",
                    protected_adult_continuity=None,
                    current_facts=_controller_facts(),
                    product_story_boundaries=("Preserve accepted branch authority.",),
                ),
            )

            ordinary_turn = replace(
                _turn(),
                current_state={
                    "public_scene_state": "Hana remains in the current room.",
                    "accepted_present_character_ids": ["character:hana"],
                },
            )
            result = controller.complete(
                request_id="request:full-model-ordinary",
                turn=ordinary_turn,
            )

            self.assertEqual(planner.calls, 1)
            self.assertIsNotNone(result.accepted_receipt)
            self.assertEqual([value.purpose for value in ordinary_pi.calls], ["writer", "recorder"])

    def test_codex_handoff_runs_no_ordinary_writer_and_promotes_atomically(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path(r"D:\Cera\tmp")) as temporary:
            root = Path(temporary)
            store = LeanSceneStore(root / "world")
            planner = _HandoffPlanner()
            ordinary, ordinary_pi = self._coordinator(root, store=store, planner=planner)
            transports: list[_FakeStructuredTransport] = []
            controller = FullModelSceneController(
                ordinary=ordinary,
                store=store,
                adult_orchestrator_factory=self._adult_factory(root, store, transports),
                adult_context_provider=lambda _turn, _route: AdultExecutionContextV1(
                    accepted_safe_projection="The accepted public scene remains current.",
                    protected_adult_continuity=None,
                    current_facts=_controller_facts(),
                    product_story_boundaries=("Preserve accepted branch authority.",),
                ),
            )

            with patch.dict(
                os.environ,
                {PROVIDER_DISPATCH_DISABLED_ENV: "1"},
                clear=False,
            ):
                result = controller.complete(
                    request_id="request:full-model-handoff",
                    turn=_turn(),
                )

            self.assertIsInstance(result, AcceptedAdultTurnV1)
            assert isinstance(result, AcceptedAdultTurnV1)
            self.assertEqual(planner.calls, 1)
            self.assertEqual(ordinary_pi.calls, [])
            self.assertEqual(len(transports), 1)
            self.assertEqual(len(transports[0].calls), 2)
            self.assertEqual(result.exact_story_prose, PROTECTED_PROSE)
            self.assertEqual(
                store.load_head(world_id="world:test", branch_id="branch:test").generation,
                1,
            )

    def test_http_adult_handoff_replays_after_restart_without_providers(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path(r"D:\Cera\tmp")) as temporary:
            root = Path(temporary)
            store = LeanSceneStore(root / "world")
            planner = _HandoffPlanner()
            ordinary, _ = self._coordinator(root, store=store, planner=planner)
            transports: list[_FakeStructuredTransport] = []

            def controller_for(coordinator):  # type: ignore[no-untyped-def]
                return FullModelSceneController(
                    ordinary=coordinator,
                    store=store,
                    adult_orchestrator_factory=self._adult_factory(
                        root,
                        store,
                        transports,
                    ),
                    adult_context_provider=lambda _turn, _route: AdultExecutionContextV1(
                        accepted_safe_projection="The accepted public scene remains current.",
                        protected_adult_continuity=None,
                        current_facts=_controller_facts(),
                        product_story_boundaries=("Preserve accepted branch authority.",),
                    ),
                )

            def route(turn):  # type: ignore[no-untyped-def]
                return SceneRoute(
                    store.current_logic_route(
                        world_id=turn.world_id,
                        branch_id=turn.branch_id,
                    ).current_logic_route.value
                )

            adapter = PiSceneHttpAdapter(
                coordinator=ordinary,
                session_id="session-adult-preparation",
                context_provider=lambda *_args: replace(_turn(), request_controls=None),
                logic_route_resolver=route,
                full_model_controller=controller_for(ordinary),
            )
            payload = self._http_payload()
            with patch.dict(
                os.environ,
                {PROVIDER_DISPATCH_DISABLED_ENV: "1"},
                clear=False,
            ):
                first = adapter.complete(payload)
                replay = adapter.complete(payload)

            self.assertEqual(first, replay)
            self.assertEqual(first["choices"][0]["message"]["content"], PROTECTED_PROSE)
            self.assertEqual(first["cera"]["status"], "accepted")
            self.assertEqual(
                first["cera"]["creator_trace"]["logic_owner"],
                "deepseek_adult_scene",
            )
            self.assertEqual(
                first["cera"]["creator_trace"]["validation"]["verdict"],
                "pass",
            )
            self.assertEqual(
                first["cera"]["creator_trace"]["provider_operations"]["planner"],
                1,
            )
            self.assertEqual(planner.calls, 1)
            self.assertEqual(len(transports), 1)

            restarted_planner = _NeverPlanner()
            restarted, _ = self._coordinator(
                root,
                store=store,
                planner=restarted_planner,
            )
            restarted_adapter = PiSceneHttpAdapter(
                coordinator=restarted,
                session_id="session-adult-preparation",
                context_provider=lambda *_args: replace(_turn(), request_controls=None),
                logic_route_resolver=route,
                full_model_controller=controller_for(restarted),
            )
            with patch.dict(
                os.environ,
                {PROVIDER_DISPATCH_DISABLED_ENV: "1"},
                clear=False,
            ):
                after_restart = restarted_adapter.complete(payload)
            self.assertEqual(after_restart, first)
            self.assertEqual(len(transports), 1)

    def test_explicit_adult_compatibility_route_does_not_enter_auto_controller(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path(r"D:\Cera\tmp")) as temporary:
            root = Path(temporary)
            store = LeanSceneStore(root / "world")
            ordinary, _ = self._coordinator(
                root,
                store=store,
                planner=_OrdinaryPlanner(),
            )
            payload = self._http_payload()
            payload["model"] = PI_SCENE_ADULT_MODEL
            response = PiSceneHttpAdapter(
                coordinator=ordinary,
                session_id="session-adult-preparation",
                context_provider=lambda *_args: lean_turn(
                    source=(
                        "Continue the buildup with a slow rub, an action-bound sound, "
                        "and a clear aftermath."
                    ),
                    adult=True,
                ),
                logic_route_resolver=lambda _turn: SceneRoute.ORDINARY,
                full_model_controller=_NeverFullModelController(),  # type: ignore[arg-type]
            ).complete(payload)

            self.assertEqual(response["cera"]["route_mode"], "adult")
            self.assertEqual(response["cera"]["status"], "review_ready")
            self.assertFalse(response["cera"]["story_state_committed"])
            self.assertEqual(
                store.load_head(world_id="world:test", branch_id="branch:test").generation,
                0,
            )

    def test_http_recovers_atomic_accept_after_terminal_response_crash(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path(r"D:\Cera\tmp")) as temporary:
            root = Path(temporary)
            store = LeanSceneStore(root / "world")
            planner = _HandoffPlanner()
            ordinary, _ = self._coordinator(root, store=store, planner=planner)
            transports: list[_FakeStructuredTransport] = []
            controller = FullModelSceneController(
                ordinary=ordinary,
                store=store,
                adult_orchestrator_factory=self._adult_factory(root, store, transports),
                adult_context_provider=lambda _turn, _route: AdultExecutionContextV1(
                    accepted_safe_projection="The accepted public scene remains current.",
                    protected_adult_continuity=None,
                    current_facts=_controller_facts(),
                    product_story_boundaries=("Preserve accepted branch authority.",),
                ),
            )

            def route(turn):  # type: ignore[no-untyped-def]
                return SceneRoute(
                    store.current_logic_route(
                        world_id=turn.world_id,
                        branch_id=turn.branch_id,
                    ).current_logic_route.value
                )

            journal_root = root / "request-journal"
            first_journal = PiSceneRequestJournal(journal_root)
            adapter = PiSceneHttpAdapter(
                coordinator=ordinary,
                session_id="session-adult-preparation",
                context_provider=lambda *_args: replace(_turn(), request_controls=None),
                logic_route_resolver=route,
                full_model_controller=controller,
                request_journal=first_journal,
            )
            payload = self._http_payload()
            with (
                patch.dict(
                    os.environ,
                    {PROVIDER_DISPATCH_DISABLED_ENV: "1"},
                    clear=False,
                ),
                patch.object(
                    first_journal,
                    "complete",
                    side_effect=OSError("injected response failure"),
                ),
                self.assertRaises(PiSceneCommittedStateError),
            ):
                adapter.complete(payload)

            self.assertEqual(planner.calls, 1)
            self.assertEqual(len(transports), 1)

    def test_http_recovers_pending_journal_after_post_promotion_crash(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path(r"D:\Cera\tmp")) as temporary:
            root = Path(temporary)
            store = LeanSceneStore(root / "world")
            custody_root = root / "protected-adult"
            custody = ProtectedAdultOperationStore(custody_root)
            planner = _HandoffPlanner()
            ordinary, _ = self._coordinator(root, store=store, planner=planner)
            transports: list[_FakeStructuredTransport] = []

            def controller_for(coordinator, operation_store):  # type: ignore[no-untyped-def]
                return FullModelSceneController(
                    ordinary=coordinator,
                    store=store,
                    adult_orchestrator_factory=self._adult_factory(
                        root,
                        store,
                        transports,
                    ),
                    adult_context_provider=lambda _turn, _route: AdultExecutionContextV1(
                        accepted_safe_projection="The accepted public scene remains current.",
                        protected_adult_continuity=None,
                        current_facts=_controller_facts(),
                        product_story_boundaries=("Preserve accepted branch authority.",),
                    ),
                    adult_operation_controller_factory=(
                        lambda: ProtectedAdultOperationController(operation_store)
                    ),
                )

            def route(turn):  # type: ignore[no-untyped-def]
                return SceneRoute(
                    store.current_logic_route(
                        world_id=turn.world_id,
                        branch_id=turn.branch_id,
                    ).current_logic_route.value
                )

            journal_root = root / "request-journal"
            adapter = PiSceneHttpAdapter(
                coordinator=ordinary,
                session_id="session-adult-preparation",
                context_provider=lambda *_args: replace(_turn(), request_controls=None),
                logic_route_resolver=route,
                full_model_controller=controller_for(ordinary, custody),
                request_journal=PiSceneRequestJournal(journal_root),
            )
            payload = self._http_payload()
            with (
                patch.dict(
                    os.environ,
                    {PROVIDER_DISPATCH_DISABLED_ENV: "1"},
                    clear=False,
                ),
                patch.object(
                    custody,
                    "mark_accepted",
                    side_effect=OSError("injected post-promotion failure"),
                ),
                self.assertRaisesRegex(OSError, "post-promotion"),
            ):
                adapter.complete(payload)

            self.assertEqual(planner.calls, 1)
            self.assertEqual(len(transports), 1)
            restarted, _ = self._coordinator(
                root,
                store=store,
                planner=_NeverPlanner(),
            )
            recovered_adapter = PiSceneHttpAdapter(
                coordinator=restarted,
                session_id="session-adult-preparation",
                context_provider=lambda *_args: replace(_turn(), request_controls=None),
                logic_route_resolver=route,
                full_model_controller=controller_for(
                    restarted,
                    ProtectedAdultOperationStore(custody_root),
                ),
                request_journal=PiSceneRequestJournal(journal_root),
            )
            recovered = recovered_adapter.complete(payload)

            self.assertEqual(recovered["cera"]["status"], "accepted")
            self.assertEqual(recovered["choices"][0]["message"]["content"], PROTECTED_PROSE)
            self.assertEqual(len(transports), 1)

    def test_http_recovers_executed_pass_before_atomic_promotion(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path(r"D:\Cera\tmp")) as temporary:
            root = Path(temporary)
            store = LeanSceneStore(root / "world")
            custody_root = root / "protected-adult"
            custody = ProtectedAdultOperationStore(custody_root)
            planner = _HandoffPlanner()
            ordinary, _ = self._coordinator(root, store=store, planner=planner)
            transports: list[_FakeStructuredTransport] = []

            def controller_for(coordinator, operation_store):  # type: ignore[no-untyped-def]
                return FullModelSceneController(
                    ordinary=coordinator,
                    store=store,
                    adult_orchestrator_factory=self._adult_factory(root, store, transports),
                    adult_context_provider=lambda _turn, _route: AdultExecutionContextV1(
                        accepted_safe_projection="The accepted public scene remains current.",
                        protected_adult_continuity=None,
                        current_facts=_controller_facts(),
                        product_story_boundaries=("Preserve accepted branch authority.",),
                    ),
                    adult_operation_controller_factory=(
                        lambda: ProtectedAdultOperationController(operation_store)
                    ),
                )

            def route(turn):  # type: ignore[no-untyped-def]
                return SceneRoute(
                    store.current_logic_route(
                        world_id=turn.world_id,
                        branch_id=turn.branch_id,
                    ).current_logic_route.value
                )

            journal_root = root / "request-journal"
            payload = self._http_payload()
            adapter = PiSceneHttpAdapter(
                coordinator=ordinary,
                session_id="session-adult-preparation",
                context_provider=lambda *_args: replace(_turn(), request_controls=None),
                logic_route_resolver=route,
                full_model_controller=controller_for(ordinary, custody),
                request_journal=PiSceneRequestJournal(journal_root),
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
                    side_effect=OSError("injected pre-promotion failure"),
                ),
                self.assertRaisesRegex(OSError, "pre-promotion"),
            ):
                adapter.complete(payload)

            restarted, _ = self._coordinator(root, store=store, planner=_NeverPlanner())
            recovered = PiSceneHttpAdapter(
                coordinator=restarted,
                session_id="session-adult-preparation",
                context_provider=lambda *_args: replace(_turn(), request_controls=None),
                logic_route_resolver=route,
                full_model_controller=controller_for(
                    restarted,
                    ProtectedAdultOperationStore(custody_root),
                ),
                request_journal=PiSceneRequestJournal(journal_root),
            ).complete(payload)

            self.assertEqual(recovered["cera"]["status"], "accepted")
            self.assertEqual(len(transports), 1)

    def test_http_recovers_executed_rejection_before_progress_binding(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path(r"D:\Cera\tmp")) as temporary:
            root = Path(temporary)
            store = LeanSceneStore(root / "world")
            custody_root = root / "protected-adult"
            custody = ProtectedAdultOperationStore(custody_root)
            planner = _HandoffPlanner()
            ordinary, _ = self._coordinator(root, store=store, planner=planner)
            transports: list[_FakeStructuredTransport] = []

            def controller_for(coordinator, operation_store):  # type: ignore[no-untyped-def]
                return FullModelSceneController(
                    ordinary=coordinator,
                    store=store,
                    adult_orchestrator_factory=self._adult_factory(root, store, transports),
                    adult_context_provider=lambda _turn, _route: AdultExecutionContextV1(
                        accepted_safe_projection="The accepted public scene remains current.",
                        protected_adult_continuity=None,
                        current_facts=_controller_facts(),
                        product_story_boundaries=("Preserve accepted branch authority.",),
                    ),
                    adult_operation_controller_factory=(
                        lambda: ProtectedAdultOperationController(operation_store)
                    ),
                )

            def route(turn):  # type: ignore[no-untyped-def]
                return SceneRoute(
                    store.current_logic_route(
                        world_id=turn.world_id,
                        branch_id=turn.branch_id,
                    ).current_logic_route.value
                )

            journal_root = root / "request-journal"
            first_journal = PiSceneRequestJournal(journal_root)
            payload = self._http_payload()
            adapter = PiSceneHttpAdapter(
                coordinator=ordinary,
                session_id="session-adult-preparation",
                context_provider=lambda *_args: replace(_turn(), request_controls=None),
                logic_route_resolver=route,
                full_model_controller=controller_for(ordinary, custody),
                request_journal=first_journal,
            )
            with (
                patch.dict(
                    os.environ,
                    {PROVIDER_DISPATCH_DISABLED_ENV: "1"},
                    clear=False,
                ),
                patch(
                    "tests.test_pi_scene_full_model_controller._FakeStructuredTransport",
                    _RejectingTransport,
                ),
                patch.object(
                    first_journal,
                    "bind_progress",
                    side_effect=OSError("injected pre-progress failure"),
                ),
                self.assertRaisesRegex(OSError, "pre-progress"),
            ):
                adapter.complete(payload)

            restarted, _ = self._coordinator(root, store=store, planner=_NeverPlanner())
            recovered_adapter = PiSceneHttpAdapter(
                coordinator=restarted,
                session_id="session-adult-preparation",
                context_provider=lambda *_args: replace(_turn(), request_controls=None),
                logic_route_resolver=route,
                full_model_controller=controller_for(
                    restarted,
                    ProtectedAdultOperationStore(custody_root),
                ),
                request_journal=PiSceneRequestJournal(journal_root),
            )
            recovered = recovered_adapter.complete(payload)

            self.assertEqual(recovered["cera"]["status"], "validation_rejected")
            self.assertFalse(recovered["cera"]["story_state_committed"])
            safe_conflict = recovered["cera"]["creator_trace"]["validation"]["conflict"]
            self.assertNotIn("exact_quote", safe_conflict)
            self.assertEqual(
                recovered["cera"]["creator_trace"]["provider_operations"]["planner"],
                1,
            )
            self.assertEqual(len(transports), 1)
            self.assertFalse(recovered["cera"]["regenerate_enabled"])
            review_id = recovered["cera"]["provisional_review_id"]
            self.assertFalse(recovered_adapter.get_review(review_id)["regenerate_enabled"])
            first_decline = recovered_adapter.decide(review_id, {"action": "decline"})
            replayed_decline = recovered_adapter.decide(review_id, {"action": "decline"})
            self.assertEqual(first_decline, replayed_decline)
            self.assertEqual(first_decline["review"]["state"], "declined")

    def test_adult_review_get_and_regenerate_are_restart_safe_and_accounted(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path(r"D:\Cera\tmp")) as temporary:
            root = Path(temporary)
            store = LeanSceneStore(root / "world")
            custody_root = root / "protected-adult"
            planner = _HandoffPlanner()
            ordinary, _ = self._coordinator(root, store=store, planner=planner)
            transports: list[_FakeStructuredTransport] = []
            executor, regenerate_transports = self._adult_regeneration_executor(root)

            def build_controller(coordinator):  # type: ignore[no-untyped-def]
                return FullModelSceneController(
                    ordinary=coordinator,
                    store=store,
                    adult_orchestrator_factory=self._adult_factory(
                        root,
                        store,
                        transports,
                    ),
                    adult_context_provider=lambda _turn, _route: AdultExecutionContextV1(
                        accepted_safe_projection="The accepted public scene remains current.",
                        protected_adult_continuity=None,
                        current_facts=_controller_facts(),
                        product_story_boundaries=("Preserve accepted branch authority.",),
                    ),
                    adult_operation_controller_factory=lambda: ProtectedAdultOperationController(
                        ProtectedAdultOperationStore(custody_root)
                    ),
                    adult_regeneration_executor=executor,
                )

            def route(turn):  # type: ignore[no-untyped-def]
                return SceneRoute(
                    store.current_logic_route(
                        world_id=turn.world_id,
                        branch_id=turn.branch_id,
                    ).current_logic_route.value
                )

            adapter = PiSceneHttpAdapter(
                coordinator=ordinary,
                session_id="session-adult-preparation",
                context_provider=lambda *_args: replace(_turn(), request_controls=None),
                logic_route_resolver=route,
                full_model_controller=build_controller(ordinary),
                request_journal=PiSceneRequestJournal(root / "journal"),
            )
            with patch(
                "tests.test_pi_scene_full_model_controller._FakeStructuredTransport",
                _RejectingTransport,
            ):
                rejected = adapter.complete(self._http_payload())

            review_id = rejected["cera"]["provisional_review_id"]
            self.assertRegex(review_id, r"\Areview-[a-f0-9]{28}\Z")
            restarted, _ = self._coordinator(
                root,
                store=store,
                planner=_NeverPlanner(),
            )
            restarted_adapter = PiSceneHttpAdapter(
                coordinator=restarted,
                session_id="session-adult-preparation",
                context_provider=lambda *_args: replace(_turn(), request_controls=None),
                logic_route_resolver=route,
                full_model_controller=build_controller(restarted),
            )
            safe_review = restarted_adapter.get_review(review_id)
            self.assertEqual(safe_review["schema_version"], "cera.pi_scene.review.v1")
            self.assertEqual(safe_review["state"], "review_ready")
            self.assertIsNone(safe_review["story_text"])
            self.assertTrue(safe_review["regenerate_enabled"])
            self.assertNotIn("adult-review:", canonical_json(safe_review))
            blocked = restarted_adapter.decide(
                review_id,
                {"action": "accept_provisional"},
            )
            self.assertEqual(blocked["disposition"], "reprojection_required")
            self.assertFalse(blocked["story_state_committed"])
            self.assertIsNone(
                store.load_head(world_id="world:test", branch_id="branch:test").receipt
            )

            regenerated = restarted_adapter.decide(review_id, {"action": "regenerate"})
            replay = restarted_adapter.decide(review_id, {"action": "regenerate"})
            self.assertEqual(len(regenerate_transports), 1)
            self.assertTrue(regenerated["story_state_committed"])
            self.assertEqual(
                regenerated["successor"]["choices"][0]["message"]["content"],
                PROTECTED_PROSE,
            )
            operations = regenerated["successor"]["cera"]["provider_operations"]
            self.assertEqual(operations["planner"], 1)
            self.assertEqual(operations["adult_scene"], 4)
            self.assertEqual(operations["adult_filter"], 6)
            repairs = regenerated["successor"]["cera"]["repair_attempts"]
            self.assertEqual(len(repairs), 1)
            self.assertEqual(repairs[0]["public_review_id"], review_id)
            self.assertEqual(
                replay["successor"]["cera"]["accepted_receipt_sha256"],
                regenerated["successor"]["cera"]["accepted_receipt_sha256"],
            )

    def test_accepted_adult_regenerate_precedes_the_ordinary_handler_and_replays(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path(r"D:\Cera\tmp")) as temporary:
            root = Path(temporary)
            store = LeanSceneStore(root / "world")
            planner = _HandoffPlanner()
            ordinary, _ = self._coordinator(root, store=store, planner=planner)
            initial_transports: list[_FakeStructuredTransport] = []
            executor, regenerate_transports = self._adult_regeneration_executor(
                root,
                transport_factory=_AdultContinuingTransport,
            )
            controller = FullModelSceneController(
                ordinary=ordinary,
                store=store,
                adult_orchestrator_factory=self._adult_factory(
                    root,
                    store,
                    initial_transports,
                    transport_factory=_AdultContinuingTransport,
                ),
                adult_context_provider=lambda _turn, _route: AdultExecutionContextV1(
                    accepted_safe_projection="The accepted public scene remains current.",
                    protected_adult_continuity=None,
                    current_facts=_controller_facts(),
                    product_story_boundaries=("Preserve accepted branch authority.",),
                ),
                adult_regeneration_executor=executor,
            )

            def route(turn):  # type: ignore[no-untyped-def]
                return SceneRoute(
                    store.current_logic_route(
                        world_id=turn.world_id,
                        branch_id=turn.branch_id,
                    ).current_logic_route.value
                )

            adapter = PiSceneHttpAdapter(
                coordinator=ordinary,
                session_id="session-adult-preparation",
                context_provider=lambda *_args: replace(_turn(), request_controls=None),
                logic_route_resolver=route,
                full_model_controller=controller,
                request_journal=PiSceneRequestJournal(root / "journal"),
            )
            first = adapter.complete(self._http_payload())
            regenerate_payload = dict(self._http_payload())
            regenerate_payload["cera_regeneration_key"] = "adult_regen_one"
            with patch.object(
                adapter,
                "_regenerate_from_chat_request",
                side_effect=AssertionError("adult Regenerate entered ordinary routing"),
            ):
                regenerated = adapter.complete(regenerate_payload)
                replay = adapter.complete(regenerate_payload)

            self.assertEqual(len(initial_transports), 1)
            self.assertEqual(len(regenerate_transports), 1)
            self.assertEqual(regenerated, replay)
            self.assertEqual(regenerated["cera"]["generation"], first["cera"]["generation"])
            self.assertEqual(
                regenerated["cera"]["accepted_turn_id"],
                first["cera"]["accepted_turn_id"],
            )
            self.assertEqual(regenerated["cera"]["provider_operations"]["planner"], 0)
            self.assertEqual(regenerated["cera"]["repair_attempts"], [])

    def test_critical_adult_repair_preserves_first_pass_and_ledger_totals(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path(r"D:\Cera\tmp")) as temporary:
            root = Path(temporary)
            store = LeanSceneStore(root / "world")
            ordinary, _ = self._coordinator(
                root,
                store=store,
                planner=_HandoffPlanner(),
            )
            first_pass: list[_FakeStructuredTransport] = []
            executor, repairs = self._adult_regeneration_executor(root)
            controller = FullModelSceneController(
                ordinary=ordinary,
                store=store,
                adult_orchestrator_factory=self._adult_factory(
                    root,
                    store,
                    first_pass,
                    transport_factory=lambda: _ConflictTransport(
                        AdultFilterConflictClass.CURRENT_DATA_CONFLICT
                    ),
                ),
                adult_context_provider=lambda _turn, _route: AdultExecutionContextV1(
                    accepted_safe_projection="The accepted public scene remains current.",
                    protected_adult_continuity=None,
                    current_facts=_controller_facts(),
                    product_story_boundaries=("Preserve accepted branch authority.",),
                ),
                adult_regeneration_executor=executor,
            )

            def route(turn):  # type: ignore[no-untyped-def]
                return SceneRoute(
                    store.current_logic_route(
                        world_id=turn.world_id,
                        branch_id=turn.branch_id,
                    ).current_logic_route.value
                )

            response = PiSceneHttpAdapter(
                coordinator=ordinary,
                session_id="session-adult-preparation",
                context_provider=lambda *_args: replace(_turn(), request_controls=None),
                logic_route_resolver=route,
                full_model_controller=controller,
            ).complete(self._http_payload())

            self.assertEqual(response["cera"]["status"], "accepted")
            self.assertEqual(len(first_pass), 1)
            self.assertEqual(len(repairs), 1)
            operations = response["cera"]["provider_operations"]
            self.assertEqual(operations["planner"], 1)
            self.assertEqual(operations["adult_scene"], 4)
            self.assertEqual(operations["adult_filter"], 6)
            repair_attempts = response["cera"]["repair_attempts"]
            self.assertEqual(len(repair_attempts), 1)
            self.assertEqual(
                repair_attempts[0]["conflict_class"],
                AdultFilterConflictClass.CURRENT_DATA_CONFLICT.value,
            )
            self.assertRegex(
                repair_attempts[0]["public_review_id"],
                r"\Areview-[a-f0-9]{28}\Z",
            )

    def test_accepted_adult_route_bypasses_codex_for_the_complete_message(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path(r"D:\Cera\tmp")) as temporary:
            root = Path(temporary)
            support = atomic_support.PiSceneAtomicAdultStoreTests(
                methodName="test_atomic_promotion_restart_replay_route_session_and_privacy"
            )
            seeded, _, envelope = support._execution_and_envelope(next_route=AdultNextRoute.ADULT)
            self.addCleanup(support.doCleanups)
            store = LeanSceneStore(root / "world")
            store.promote_adult_acceptance_envelope(envelope)
            ordinary, ordinary_pi = self._coordinator(
                root,
                store=store,
                planner=_NeverPlanner(),
            )
            transports: list[_FakeStructuredTransport] = []
            controller = FullModelSceneController(
                ordinary=ordinary,
                store=store,
                adult_orchestrator_factory=self._adult_factory(root, store, transports),
                adult_context_provider=lambda _turn, route: AdultExecutionContextV1(
                    accepted_safe_projection="The safe adult projection remains current.",
                    protected_adult_continuity=(
                        PROTECTED_PROSE
                        if route.current_logic_route is AdultNextRoute.ADULT
                        else None
                    ),
                    current_facts=_controller_facts(),
                    product_story_boundaries=("Preserve accepted branch authority.",),
                ),
            )
            continuation = replace(
                _turn(),
                world_id=seeded.context.world_id,
                branch_id=seeded.context.branch_id,
                scene_id=seeded.context.scene_id,
                exact_user_source="Continue the accepted private scene.",
                recent_prose=(PROTECTED_PROSE,),
            )

            with patch.dict(
                os.environ,
                {PROVIDER_DISPATCH_DISABLED_ENV: "1"},
                clear=False,
            ):
                result = controller.complete(
                    request_id="request:full-model-continuation",
                    turn=continuation,
                )

            self.assertIsInstance(result, AcceptedAdultTurnV1)
            self.assertEqual(ordinary_pi.calls, [])
            self.assertEqual(len(transports[0].calls), 2)
            self.assertEqual(
                store.load_head(
                    world_id=seeded.context.world_id,
                    branch_id=seeded.context.branch_id,
                ).generation,
                2,
            )


if __name__ == "__main__":
    unittest.main()
