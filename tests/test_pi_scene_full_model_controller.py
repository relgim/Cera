from __future__ import annotations

import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from cera.adult_pipeline.contracts import AdultContextFactV1, AdultNextRoute
from cera.adult_pipeline.craft_catalog import CatalogAdultCraftRetrieval
from cera.adult_pipeline.integration import AdultPipelineIntegrationV1
from cera.adult_pipeline.pi_roles import (
    AdultRoleViewContextV1,
    PiDeepSeekAdultFilterPort,
    PiDeepSeekAdultScenePort,
)
from cera.adult_pipeline.pipeline import AdultPipeline
from cera.adult_pipeline.preparation import build_adult_turn_preparation_builder
from cera.pi_scene.adult_orchestration import AutomaticAdultRouteOrchestrator
from cera.pi_scene.full_model_controller import (
    AcceptedAdultTurnV1,
    AdultExecutionContextV1,
    FullModelSceneController,
)
from cera.pi_scene.runtime import LeanPiSceneCoordinator, PlannerTurnOutputV1
from cera.pi_scene.store import LeanSceneStore
from cera.pi_scene.writer_view import WriterViewMaterializer
from cera.provider_dispatch_guard import PROVIDER_DISPATCH_DISABLED_ENV
from cera.semantic_validation import SemanticVerdict
from cera.serialization import to_primitive

from . import test_pi_scene_atomic_adult_store as atomic_support
from .test_adult_pipeline_pi_integration import (
    PROTECTED_PROSE,
    _FakeStructuredTransport,
)
from .test_adult_turn_preparation import CATALOG_ROOT, _plan, _turn
from .test_cognition_contracts import _plan as _ordinary_plan
from .test_pi_scene_lean_v1 import FakePi
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
    ):
        def build(turn, _request_id, candidate_id):  # type: ignore[no-untyped-def]
            transport = _FakeStructuredTransport()
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
