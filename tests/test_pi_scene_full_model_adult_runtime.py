from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

from cera.adult_pipeline.contracts import AdultNextRoute, AdultRouteStateSnapshotV1
from cera.adult_pipeline.craft_catalog import CatalogAdultCraftRetrieval
from cera.adult_pipeline.integration import AdultPipelineIntegrationV1
from cera.adult_pipeline.pi_roles import (
    PiDeepSeekAdultFilterPort,
    PiDeepSeekAdultScenePort,
)
from cera.adult_pipeline.pipeline import AdultPipeline
from cera.errors import ContractValidationError, StateConflictError
from cera.pi_scene.adult_orchestration import PreparedAdultRouteOperationV1
from cera.pi_scene.full_model_adult_runtime import FullModelAdultRuntimeFactory
from cera.pi_scene.full_model_controller import FullModelSceneController
from cera.pi_scene.http_contracts import LeanSceneRequestControlsV2
from cera.pi_scene.pi_adapter import PiSceneAdapter
from cera.pi_scene.review_store import LeanSceneTurnInputV1
from cera.pi_scene.store import AcceptedPiSessionV1
from cera.pi_scene.writer_view import WriterViewMaterializer
from cera.serialization import canonical_json, canonical_sha256, text_sha256
from scripts.run_pi_scene_lean_server import (
    FullModelProviderComponents,
    build_live_runtime,
)
from tests.test_adult_pipeline_pi_integration import _FakeStructuredTransport
from tests.test_pi_scene_full_model_launcher import (
    _FakeLunaBackend,
    _FakeReaderBackend,
    _OfflineLifecycle,
)
from tests.test_pi_scene_lean_v1 import FakePi

ROOT = Path(__file__).parents[1]
CATALOG_ROOT = ROOT / "adult" / "catalog" / "adult_craft_v1"
PROTECTED_PROSE = "PROTECTED_ADULT_EXACT_PROSE_SENTINEL_2026"


def _route_state(*, route: AdultNextRoute = AdultNextRoute.ADULT) -> AdultRouteStateSnapshotV1:
    has_head = route is AdultNextRoute.ADULT
    return AdultRouteStateSnapshotV1(
        schema_version=AdultRouteStateSnapshotV1.SCHEMA_VERSION,
        world_id="world:test",
        branch_id="branch:test",
        accepted_head_sha256=text_sha256("accepted-head") if has_head else None,
        current_logic_route=route,
        source_promotion_sha256=text_sha256("adult-promotion") if has_head else None,
    )


def _safe_records() -> tuple[dict[str, Any], ...]:
    return (
        {
            "receipt": {
                "route": "ordinary",
                "accepted_turn_id": "turn:ordinary:1",
                "exact_accepted_prose": "Hana answered from the doorway.",
            },
            "recording_status": "complete",
        },
        {
            "receipt": {
                "route": "adult",
                "accepted_turn_id": "turn:adult:2",
                "exact_accepted_prose_sha256": text_sha256(PROTECTED_PROSE),
            },
            "recording_status": "complete",
            "adult_projection": {
                "non_explicit_summary": "A private adult interaction occurred.",
            },
        },
    )


def _protected_records() -> tuple[dict[str, Any], ...]:
    return (
        {
            "receipt": {
                "route": "ordinary",
                "accepted_turn_id": "turn:ordinary:1",
                "exact_accepted_prose": "Hana answered from the doorway.",
            },
            "recording_status": "complete",
        },
        {
            "receipt": {
                "route": "adult",
                "accepted_turn_id": "turn:adult:2",
                "exact_accepted_prose": PROTECTED_PROSE,
            },
            "recording_status": "complete",
            "adult_full_record": {
                "exact_story_prose": PROTECTED_PROSE,
                "private_detail": "protected full record detail",
            },
            "adult_projection": {
                "non_explicit_summary": "A private adult interaction occurred.",
            },
        },
    )


def _turn() -> LeanSceneTurnInputV1:
    return LeanSceneTurnInputV1(
        world_id="world:test",
        branch_id="branch:test",
        scene_id="scene:test",
        exact_user_source="Continue from the accepted scene.",
        current_state={
            "accepted_present_character_ids": ["character:hana"],
            "public_scene_state": "Hana remains in the room.",
            "unresolved_threads": ["The conversation remains open."],
            "durable_changes": [
                {
                    "change_key": "private_unease",
                    "subject_ids": ["character:hana"],
                    "concise_change": "Hana privately remains uneasy.",
                    "visibility": "character_private",
                    "knowledge_owner_id": "character:hana",
                }
            ],
            "hard_boundaries": ["Preserve accepted branch authority."],
        },
        characters={
            "character:hana": {
                "name": "Hana",
                "age": 38,
                "visibility": "character_private",
                "knowledge_owner_id": "character:hana",
            }
        },
        relationships={
            "relationship:hana_ted": {
                "participants": ["character:hana", "character:ted"],
                "status": "current accepted relationship state",
                "visibility": "public",
            }
        },
        recent_prose=(PROTECTED_PROSE,),
        relevant_memories={
            "memory:hana_unease": {
                "summary": "Hana privately carries unresolved unease.",
                "visibility": "character_private",
                "knowledge_owner_id": "character:hana",
            }
        },
        voice_examples={"character:hana": "Hana speaks gently."},
        craft_index={},
        request_controls=LeanSceneRequestControlsV2(
            schema_version=LeanSceneRequestControlsV2.SCHEMA_VERSION,
            session_id="adult-runtime-test",
            character_autonomy="both",
            adult_craft_mode="ex",
        ),
    )


@dataclass
class _Store:
    route_state: AdultRouteStateSnapshotV1
    safe_records: tuple[dict[str, Any], ...] = _safe_records()
    protected_records: tuple[dict[str, Any], ...] = _protected_records()

    def current_logic_route(
        self,
        *,
        world_id: str,
        branch_id: str,
    ) -> AdultRouteStateSnapshotV1:
        if (world_id, branch_id) != (
            self.route_state.world_id,
            self.route_state.branch_id,
        ):
            raise AssertionError("test requested another branch")
        return self.route_state

    def recent_ordinary_context_payloads(
        self,
        *,
        world_id: str,
        branch_id: str,
        limit: int = 6,
    ) -> tuple[dict[str, Any], ...]:
        del world_id, branch_id, limit
        return self.safe_records

    def recent_adult_context_payloads(
        self,
        *,
        world_id: str,
        branch_id: str,
        limit: int = 6,
    ) -> tuple[dict[str, Any], ...]:
        del world_id, branch_id, limit
        return self.protected_records

    def load_accepted_pi_session(
        self,
        *,
        world_id: str,
        branch_id: str,
    ) -> AcceptedPiSessionV1:
        del world_id, branch_id
        session_id = "pi-session-accepted-parent"
        return AcceptedPiSessionV1(
            accepted_turn_id="turn:adult:2",
            session_id=session_id,
            session_path=r"D:\Cera\tmp\accepted-parent-session",
            session_id_sha256=text_sha256(session_id),
            accepted_receipt_sha256=self.route_state.accepted_head_sha256,
        )


class PiSceneFullModelAdultRuntimeTests(unittest.TestCase):
    def _factory(self, root: Path, store: _Store) -> FullModelAdultRuntimeFactory:
        return FullModelAdultRuntimeFactory(
            store=store,
            pi_adapter=cast(PiSceneAdapter, FakePi()),
            catalog_root=CATALOG_ROOT,
            protected_runtime_root=root / "protected-adult",
        )

    def test_context_separates_safe_projection_and_protected_adult_continuity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = _Store(_route_state())
            factory = self._factory(Path(temporary), store)
            context = factory.execution_context(_turn(), store.route_state)

        self.assertNotIn(PROTECTED_PROSE, context.accepted_safe_projection)
        self.assertIsNotNone(context.protected_adult_continuity)
        assert context.protected_adult_continuity is not None
        self.assertIn(PROTECTED_PROSE, context.protected_adult_continuity)
        self.assertNotIn(
            PROTECTED_PROSE,
            "\n".join(value.authoritative_fact for value in context.current_facts),
        )
        self.assertEqual(
            context.product_story_boundaries,
            ("Preserve accepted branch authority.",),
        )
        private = tuple(
            value
            for value in context.current_facts
            if "privately" in value.authoritative_fact.casefold()
        )
        self.assertTrue(private)
        self.assertTrue(
            all(
                value.visibility == "adult_role_private" and value.subject_id == "character:hana"
                for value in private
            )
        )
        public_relationship = tuple(
            value
            for value in context.current_facts
            if "current accepted relationship state" in value.authoritative_fact
        )
        self.assertEqual(len(public_relationship), 1)
        self.assertEqual(public_relationship[0].visibility, "public")

    def test_candidate_factory_binds_exact_request_turn_route_and_parent_session(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = _Store(_route_state())
            factory = self._factory(root, store)
            turn = _turn()
            first_scope = factory.candidate_scope(
                turn,
                "request:adult:one",
                "candidate:adult:one",
            )
            second_scope = factory.candidate_scope(
                turn,
                "request:adult:two",
                "candidate:adult:one",
            )
            orchestrator = factory.orchestrator(
                turn,
                "request:adult:one",
                "candidate:adult:one",
            )

            integration = orchestrator.pipeline
            scene = integration.scene_port  # type: ignore[attr-defined]
            self.assertEqual(scene.context.candidate_id, "candidate:adult:one")
            self.assertEqual(scene.context.world_id, turn.world_id)
            self.assertIn(PROTECTED_PROSE, scene.context.recent_prose)
            self.assertIn(PROTECTED_PROSE, str(scene.context.accepted_records))
            self.assertIsNotNone(scene.accepted_parent_session)
            self.assertEqual(
                scene.accepted_parent_session.accepted_receipt_sha256,
                store.route_state.accepted_head_sha256,
            )
            self.assertEqual(
                scene.materializer.root,
                first_scope.protected_root / "WRITER_VIEWS",
            )
            self.assertNotEqual(first_scope.protected_root, second_scope.protected_root)
            self.assertFalse((root / "protected-adult").exists())

    def test_context_fails_closed_if_safe_store_exposes_exact_adult_prose(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            safe = list(_safe_records())
            safe[1] = {
                **safe[1],
                "leaked_exact_prose": PROTECTED_PROSE,
            }
            store = _Store(_route_state(), safe_records=tuple(safe))
            factory = self._factory(Path(temporary), store)
            with self.assertRaisesRegex(StateConflictError, "escaped"):
                factory.execution_context(_turn(), store.route_state)

    def test_catalog_is_verified_before_candidate_construction(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaises(ContractValidationError):
                FullModelAdultRuntimeFactory(
                    store=_Store(_route_state()),
                    pi_adapter=cast(PiSceneAdapter, FakePi()),
                    catalog_root=root / "missing-catalog",
                    protected_runtime_root=root / "protected-adult",
                )
            self.assertFalse((root / "protected-adult").exists())

    def test_regenerate_uses_only_frozen_capsule_and_a_fresh_scene_session(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path(r"D:\Cera\tmp")) as temporary:
            root = Path(temporary)
            store = _Store(_route_state())
            factory = self._factory(root, store)
            turn = _turn()
            context = factory.execution_context(turn, store.route_state)
            orchestrator = factory.orchestrator(
                turn,
                "request:adult:original",
                "candidate:adult:original",
            )
            prepared = orchestrator.prepare(
                request_id="request:adult:original",
                candidate_id="candidate:adult:original",
                turn_input=turn,
                accepted_safe_projection=context.accepted_safe_projection,
                protected_adult_continuity=context.protected_adult_continuity,
                current_facts=context.current_facts,
                product_story_boundaries=context.product_story_boundaries,
                cognition_handoff_factory=None,
            )
            self.assertIsInstance(prepared, PreparedAdultRouteOperationV1)

            # These later branch values and the accepted Pi session must be
            # irrelevant to the frozen Regenerate request.
            store.route_state = replace(
                store.route_state,
                accepted_head_sha256=text_sha256("later-head"),
                source_promotion_sha256=text_sha256("later-promotion"),
            )
            store.safe_records = ({"later": "safe"},)
            store.protected_records = ({"later": "protected"},)
            captured: list[dict[str, Any]] = []
            evidence_refs = tuple(
                value.evidence_ref for value in prepared.scene_request.current_context
            )

            class PreparedTransport(_FakeStructuredTransport):
                def invoke_structured_role(self, **kwargs):  # type: ignore[no-untyped-def]
                    result = super().invoke_structured_role(**kwargs)
                    payload = json.loads(result.raw_json)
                    if kwargs["role"].value == "adult_scene":
                        payload["decision_path"][0]["evidence_refs"] = list(evidence_refs)
                    else:
                        payload["protected_record"]["current_data_uses"] = [
                            {
                                "evidence_ref": evidence_ref,
                                "decision_key": "decision_one",
                                "concise_use": "Frozen current data informed the decision.",
                            }
                            for evidence_ref in evidence_refs
                        ]
                    return replace(result, raw_json=canonical_json(payload))

            def capture_build(**kwargs: Any):  # type: ignore[no-untyped-def]
                captured.append(kwargs)
                protected_root = cast(Path, kwargs["protected_runtime_root"])
                role_context = kwargs["context"]
                transport = PreparedTransport()
                materializer = WriterViewMaterializer(protected_root / "WRITER_VIEWS")
                scene = PiDeepSeekAdultScenePort(
                    transport=transport,
                    materializer=materializer,
                    context=role_context,
                    session_root=protected_root / "SESSIONS",
                    accepted_parent_session=kwargs["accepted_parent_session"],
                )
                filter_port = PiDeepSeekAdultFilterPort(
                    transport=transport,
                    materializer=materializer,
                    context=role_context,
                    session_root=protected_root / "SESSIONS",
                )
                return AdultPipelineIntegrationV1(
                    pipeline=AdultPipeline(scene=scene, filter_port=filter_port),
                    scene_port=scene,
                    filter_port=filter_port,
                    craft_retrieval=CatalogAdultCraftRetrieval(CATALOG_ROOT),
                )

            with (
                patch(
                    "cera.pi_scene.full_model_adult_runtime.build_pi_adult_pipeline_integration",
                    side_effect=capture_build,
                ),
                patch.object(
                    store,
                    "current_logic_route",
                    side_effect=AssertionError("Regenerate read the current head"),
                ),
                patch.object(
                    store,
                    "recent_ordinary_context_payloads",
                    side_effect=AssertionError("Regenerate read later accepted records"),
                ),
                patch.object(
                    store,
                    "recent_adult_context_payloads",
                    side_effect=AssertionError("Regenerate read later protected records"),
                ),
                patch.object(
                    store,
                    "load_accepted_pi_session",
                    side_effect=AssertionError("Regenerate continued the current Pi session"),
                ),
            ):
                outcome = factory.regeneration_executor(prepared, turn)

            self.assertEqual(outcome.prepared, prepared)
            self.assertEqual(len(captured), 1)
            built = captured[0]
            self.assertIsNone(built["accepted_parent_session"])
            self.assertEqual(built["context"].accepted_records, ())
            self.assertEqual(
                canonical_sha256(built["context"].current_state),
                canonical_sha256(turn.current_state),
            )
            self.assertEqual(tuple(built["context"].recent_prose), turn.recent_prose)

    def test_launcher_exposes_controller_without_dispatch_under_kill_switch(self) -> None:
        pi = FakePi()
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.dict("os.environ", {"CERA_PROVIDER_DISPATCH_DISABLED": "1"}),
        ):
            root = Path(temporary) / "runtime"
            runtime = build_live_runtime(
                root,
                sol_ceiling=4,
                deepseek_ceiling=8,
                provider_components=FullModelProviderComponents(
                    planner_lifecycle=_OfflineLifecycle(),
                    luna_backend=_FakeLunaBackend(),
                    reader_backend=_FakeReaderBackend(),
                    pi=pi,
                    external_provider_boundary=False,
                ),
            )
            try:
                self.assertIsInstance(runtime.full_model_controller, FullModelSceneController)
                self.assertIs(runtime.full_model_controller.ordinary, runtime.coordinator)
                self.assertIs(runtime.full_model_controller.store, runtime.store)
                self.assertEqual(runtime.sol_ledger.dispatched_call_count, 0)
                self.assertEqual(runtime.deepseek_ledger.operation_count, 0)
                self.assertEqual(pi.calls, [])
                self.assertFalse((root / "protected_adult").exists())
            finally:
                runtime.close()


if __name__ == "__main__":
    unittest.main()
