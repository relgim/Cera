from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from cera.errors import StateConflictError
from cera.pi_scene.context import initial_hanezawa_doorway_seed
from cera.pi_scene.contracts import SceneRoute
from cera.pi_scene.http_contracts import LeanSceneRequestControlsV1
from cera.pi_scene.store import LeanSceneStore
from cera.pi_scene.world_planner import BranchBoundSequenceFirstPlannerBackend
from cera.pi_scene.world_runtime import PiSceneChatWorldResolver
from cera.pi_scene.world_workspace import PiSceneWorldWorkspaceManager
from cera.sequence_first.provider import SequenceFirstPlannerCodexBackend
from scripts.run_pi_scene_lean_server import build_session_context_provider

ROOT = Path(__file__).resolve().parents[1]
GENESIS_ROOT = ROOT / "genesis" / "packages"


def _controls(session_id: str) -> LeanSceneRequestControlsV1:
    return LeanSceneRequestControlsV1(
        schema_version=LeanSceneRequestControlsV1.SCHEMA_VERSION,
        session_id=session_id,
        character_autonomy="both",
    )


class PiSceneWorldRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.world_root = self.root / "accepted_world"
        self.store = LeanSceneStore(self.world_root)
        self.manager = PiSceneWorldWorkspaceManager(self.world_root, GENESIS_ROOT)
        self.resolver = PiSceneChatWorldResolver(
            manager=self.manager,
            store=self.store,
            scope_root=self.root / "chat_scopes",
            base_seed=initial_hanezawa_doorway_seed(),
        )

    def test_new_chat_uses_latest_genesis_and_returns_only_relevant_full_context(self) -> None:
        resolved = self.resolver.resolve_turn(
            route=SceneRoute.ORDINARY,
            source="Hello?",
            messages=({"role": "user", "content": "Hello?"},),
            controls=_controls("chat-alpha"),
        )

        self.assertEqual(resolved.scope.workspace.genesis_pin.revision_number, 2)
        self.assertEqual(
            set(resolved.turn.characters),
            {"character:sakura_hanezawa"},
        )
        self.assertEqual(
            resolved.turn.current_state["accepted_present_character_ids"],
            ["character:sakura_hanezawa"],
        )
        self.assertGreaterEqual(len(resolved.dossier_build.character_ids), 7)
        sakura = resolved.retrieval.get_character_context("character:sakura_hanezawa")
        self.assertTrue(sakura["character_context"]["genesis_record_projections"])
        self.assertTrue(sakura["relationship_context"])
        self.assertTrue(sakura["memory_context"])
        turn_context = resolved.retrieval.get_turn_context()
        self.assertEqual(
            [value["character_id"] for value in turn_context["character_dossiers"]],
            ["character:sakura_hanezawa"],
        )
        self.assertIn("character:hana_hanezawa", turn_context["omitted_character_ids"])

        restarted = PiSceneChatWorldResolver(
            manager=PiSceneWorldWorkspaceManager(self.world_root, GENESIS_ROOT),
            store=LeanSceneStore(self.world_root),
            scope_root=self.root / "chat_scopes",
            base_seed=initial_hanezawa_doorway_seed(),
        ).resolve(_controls("chat-alpha"))
        self.assertEqual(
            restarted.workspace.workspace_sha256,
            resolved.scope.workspace.workspace_sha256,
        )

    def test_separate_chats_are_separate_worlds_and_fork_retains_parent_world(self) -> None:
        parent = self.resolver.resolve(_controls("chat-parent"))
        sibling = self.resolver.resolve(_controls("chat-sibling"))
        self.assertNotEqual(parent.world_id, sibling.world_id)
        self.assertNotEqual(parent.workspace.branch_root, sibling.workspace.branch_root)

        child = self.resolver.fork(
            parent_session_id="chat-parent",
            child_session_id="chat-child",
            selected_parent_accepted_turn_id=None,
            selected_parent_accepted_head_sha256=None,
        )
        self.assertEqual(child.kind, "fork")
        self.assertEqual(child.world_id, parent.world_id)
        self.assertNotEqual(child.branch_id, parent.branch_id)
        self.assertEqual(child.workspace.genesis_pin, parent.workspace.genesis_pin)
        self.assertEqual(child.parent_session_id, "chat-parent")

        reopened = self.resolver.resolve(_controls("chat-child"))
        self.assertEqual(reopened.branch_id, child.branch_id)
        with self.assertRaisesRegex(StateConflictError, "historical selected fork"):
            self.resolver.fork(
                parent_session_id="chat-parent",
                child_session_id="chat-bad-fork",
                selected_parent_accepted_turn_id="turn:older",
                selected_parent_accepted_head_sha256="0" * 64,
            )

    def test_branch_mcp_uses_semantic_ids_and_closes_private_term_probe(self) -> None:
        scope = self.resolver.resolve(_controls("chat-private"))
        public = scope.workspace.branch_root / "ACTIVE" / "Events" / "public.json"
        private = scope.workspace.branch_root / "ACTIVE" / "Characters" / "sakura.json"
        legacy_private = (
            scope.workspace.branch_root / "ACTIVE" / "Characters" / "legacy-hana.json"
        )
        creator = scope.workspace.branch_root / "ACTIVE" / "Events" / "creator.json"
        public.parent.mkdir(parents=True, exist_ok=True)
        private.parent.mkdir(parents=True, exist_ok=True)
        public.write_text(
            '{"_cera_revision":1,"visibility":"public","marker":"public-beacon"}',
            encoding="utf-8",
        )
        private.write_text(
            '{"_cera_revision":1,"visibility":"character_private",'
            '"knowledge_owner_id":"character:sakura_hanezawa",'
            '"marker":"private-beacon"}',
            encoding="utf-8",
        )
        legacy_private.write_text(
            '{"_cera_revision":1,"character_id":"character:hana_hanezawa",'
            '"marker":"legacy-secret-marker"}',
            encoding="utf-8",
        )
        creator.write_text(
            '{"_cera_revision":1,"visibility":"creator_private","marker":"creator-beacon"}',
            encoding="utf-8",
        )
        dispatcher = self.manager.mcp_factory(scope.workspace).dispatcher(maximum_calls=12)
        self.assertEqual(dispatcher.evidence_registry.world_id, scope.world_id)
        self.assertEqual(dispatcher.evidence_registry.branch_id, scope.branch_id)
        self.assertEqual(
            dispatcher.invoke(
                "cera_world_search",
                {"terms": ["private-beacon"], "limit": 20},
            )["records"],
            [],
        )
        self.assertEqual(
            len(
                dispatcher.invoke(
                    "cera_world_search",
                    {
                        "terms": ["private-beacon"],
                        "knowledge_owner_id": "character:sakura_hanezawa",
                        "limit": 20,
                    },
                )["records"]
            ),
            1,
        )
        self.assertEqual(
            dispatcher.invoke(
                "cera_world_search",
                {
                    "terms": ["private-beacon"],
                    "knowledge_owner_id": "character:hana_hanezawa",
                    "limit": 20,
                },
            )["records"],
            [],
        )
        self.assertEqual(
            dispatcher.invoke(
                "cera_world_search",
                {"terms": ["creator-beacon"], "limit": 20},
            )["records"],
            [],
        )
        self.assertEqual(
            dispatcher.invoke(
                "cera_world_search",
                {"terms": ["legacy-secret-marker"], "limit": 20},
            )["records"],
            [],
        )
        self.assertEqual(
            len(
                dispatcher.invoke(
                    "cera_world_search",
                    {
                        "terms": ["legacy-secret-marker"],
                        "knowledge_owner_id": "character:hana_hanezawa",
                        "limit": 20,
                    },
                )["records"]
            ),
            1,
        )
        listed = dispatcher.invoke("cera_world_list", {"prefix": "ACTIVE/Events", "limit": 20})
        self.assertNotIn(
            "ACTIVE/Events/creator.json",
            {value["path"] for value in listed["records"]},
        )
        with self.assertRaisesRegex(PermissionError, "creator-private"):
            dispatcher.invoke("cera_world_read", {"path": "ACTIVE/Events/creator.json"})

    def test_launcher_context_seam_delegates_to_workspace_resolver(self) -> None:
        expected = object()

        class _Resolver:
            def resolve_turn(self, **kwargs):
                self.kwargs = kwargs
                return SimpleNamespace(turn=expected)

        resolver = _Resolver()
        provider = build_session_context_provider(
            self.store,
            initial_hanezawa_doorway_seed(),
            workspace_resolver=resolver,  # type: ignore[arg-type]
        )
        actual = provider(
            SceneRoute.ORDINARY,
            "Hello?",
            ({"role": "user", "content": "Hello?"},),
            _controls("chat-launcher"),
        )
        self.assertIs(actual, expected)
        self.assertEqual(resolver.kwargs["source"], "Hello?")


class BranchBoundPlannerBackendTests(unittest.TestCase):
    def test_retained_backend_refreshes_request_bridge_and_aborts_failures(self) -> None:
        class _Bridge:
            def __init__(self) -> None:
                self.aborts = 0

            def abort(self) -> None:
                self.aborts += 1

        class _Factory:
            def __init__(self) -> None:
                self.bridges = []

            def bridge(self, **_kwargs):
                value = _Bridge()
                self.bridges.append(value)
                return value

        factory = _Factory()
        reference_scope = SimpleNamespace(
            known_character_ids=("character:sakura_hanezawa",)
        )
        with tempfile.TemporaryDirectory() as raw:
            backend = BranchBoundSequenceFirstPlannerBackend(
                world_mcp_factory=factory,  # type: ignore[arg-type]
                lifecycle=object(),  # type: ignore[arg-type]
                workspace=Path(raw),
                call_ledger=object(),  # type: ignore[arg-type]
            )
            with patch.object(
                SequenceFirstPlannerCodexBackend,
                "run_planner_turn",
                autospec=True,
            ) as delegated:
                delegated.side_effect = lambda current, **_kwargs: current.world_bridge
                first = backend.run_planner_turn(
                    thread_id="thread-1",
                    prompt="one",
                    reference_scope=reference_scope,  # type: ignore[arg-type]
                )
                second = backend.run_planner_turn(
                    thread_id="thread-1",
                    prompt="two",
                    reference_scope=reference_scope,  # type: ignore[arg-type]
                )
        self.assertIs(first, factory.bridges[0])
        self.assertIs(second, factory.bridges[1])
        self.assertIsNone(backend.world_bridge)
        self.assertEqual([value.aborts for value in factory.bridges], [0, 0])

        with patch.object(
            SequenceFirstPlannerCodexBackend,
            "run_planner_turn",
            side_effect=RuntimeError("provider failed"),
        ):
            with self.assertRaisesRegex(RuntimeError, "provider failed"):
                backend.run_planner_turn(
                    thread_id="thread-1",
                    prompt="three",
                    reference_scope=reference_scope,  # type: ignore[arg-type]
                )
        self.assertEqual(factory.bridges[-1].aborts, 1)
        self.assertIsNone(backend.world_bridge)


if __name__ == "__main__":
    unittest.main()
