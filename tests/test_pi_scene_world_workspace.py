from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest

from cera.errors import (
    CanonicalizationError,
    ContractValidationError,
    StateConflictError,
)
from cera.pi_scene.world_workspace import (
    AcceptedGenesisCatalog,
    ForkChatWorkspaceRequestV1,
    NewChatWorkspaceRequestV1,
    PiSceneWorldWorkspaceManager,
    RequestBoundWorldMcpBridge,
)
from cera.pi_scene.contracts import LeanCandidateV1, PiWriterReceiptV1, SceneRoute
from cera.pi_scene.store import LeanSceneStore
from cera.serialization import canonical_json, text_sha256


ROOT = Path(__file__).resolve().parents[1]
GENESIS_ROOT = ROOT / "genesis" / "packages"


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _raw_manifest(root: Path, *, omitted: frozenset[str] = frozenset()) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file() and path.relative_to(root).as_posix() not in omitted
    }


class _FakeRawBridge:
    def __init__(self, dispatcher) -> None:
        self.dispatcher = dispatcher
        self.starts = 0
        self.stops: list[bool] = []
        self.finalizations: list[object] = []

    def start(self):
        self.starts += 1
        return self

    @property
    def runtime_binding(self):
        return "fake-runtime-binding"

    def finalize(self, provider_result):
        self.finalizations.append(provider_result)
        return {"finalized": True}

    def stop(self, *, suppress_errors: bool = False) -> None:
        self.stops.append(suppress_errors)


class _FailingBindingBridge(_FakeRawBridge):
    @property
    def runtime_binding(self):
        raise RuntimeError("binding failed")


class _FailingFinalizeBridge(_FakeRawBridge):
    def finalize(self, provider_result):
        self.finalizations.append(provider_result)
        raise RuntimeError("finalize failed")


def _pending_candidate(*, world_id: str, branch_id: str) -> LeanCandidateV1:
    authority = {
        "items": [
            {
                "item_key": "hana_answers",
                "owner_id": "character:hana_hanezawa",
                "kind": "dialogue_intent",
                "summary": "Hana answers and returns the floor.",
            }
        ],
        "durable_changes": [],
        "presence_changes": [],
        "resulting_public_state": "The conversation remains open.",
        "unresolved_threads": ["Ted may respond."],
        "stopping_boundary": "Stop with the floor returned to Ted.",
    }
    authority_json = canonical_json(authority)
    prose = "Hana answers, then leaves Ted room to respond."
    writer_receipt = PiWriterReceiptV1(
        schema_version=PiWriterReceiptV1.SCHEMA_VERSION,
        route=SceneRoute.ORDINARY,
        provider="test-provider",
        model="test-model",
        pi_version="test-pi",
        session_id_sha256=text_sha256("session-1"),
        parent_session_id_sha256=None,
        request_sha256=text_sha256("request-1"),
        output_sha256=text_sha256(prose),
        provider_operations=1,
        tool_call_count=0,
        failed_tool_call_count=0,
        input_tokens=10,
        cached_input_tokens=0,
        output_tokens=5,
        reasoning_tokens=0,
        duration_ms=1,
        finish_status="stop",
        rehydrated=False,
    )
    return LeanCandidateV1(
        schema_version=LeanCandidateV1.SCHEMA_VERSION,
        request_id="request-1",
        candidate_id="candidate-1",
        turn_id="turn-1",
        world_id=world_id,
        branch_id=branch_id,
        scene_id="scene-entryway",
        generation=1,
        parent_accepted_turn_id=None,
        accepted_head_before_sha256=None,
        exact_user_source="Hello?",
        exact_user_source_sha256=text_sha256("Hello?"),
        route=SceneRoute.ORDINARY,
        primary_authority_kind="codex_sequence",
        primary_authority_json=authority_json,
        primary_authority_sha256=text_sha256(authority_json),
        writer_view_manifest_sha256=text_sha256("writer-view-1"),
        story_text=prose,
        story_text_sha256=text_sha256(prose),
        writer_receipt=writer_receipt,
    )


class AcceptedGenesisCatalogTests(unittest.TestCase):
    def test_newest_creator_canon_revision_is_selected_and_verified(self) -> None:
        selected = AcceptedGenesisCatalog(GENESIS_ROOT).newest()

        self.assertEqual(selected.pin.revision_number, 2)
        self.assertEqual(
            selected.pin.revision_id,
            "genesis_revision:0f473a00-3ba4-5da8-bea9-5e251c0b3d56",
        )
        self.assertEqual(selected.package_root.name, "hanezawa_core_v1_2")

    def test_ambiguous_revision_number_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            shutil.copytree(GENESIS_ROOT / "hanezawa_core_v1_1", root / "v1")
            shutil.copytree(GENESIS_ROOT / "hanezawa_core_v1_2", root / "v2")
            shutil.copytree(GENESIS_ROOT / "hanezawa_core_v1_2", root / "v2-copy")

            with self.assertRaisesRegex(StateConflictError, "ambiguous"):
                AcceptedGenesisCatalog(root).newest()

    def test_undeclared_or_hash_changed_package_file_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            package = root / "v2"
            shutil.copytree(GENESIS_ROOT / "hanezawa_core_v1_2", package)
            (package / "unexpected.json").write_text("{}", encoding="utf-8")

            with self.assertRaisesRegex(StateConflictError, "undeclared"):
                AcceptedGenesisCatalog(root).newest()


class PiSceneWorldWorkspaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.runtime_root = Path(self.temporary.name) / "worlds"
        self.manager = PiSceneWorldWorkspaceManager(self.runtime_root, GENESIS_ROOT)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _new_chat(
        self,
        *,
        chat_id: str = "chat-a",
        world_id: str = "world-a",
        branch_id: str = "main",
    ):
        return self.manager.create_new_chat(
            NewChatWorkspaceRequestV1(
                chat_id=chat_id,
                world_id=world_id,
                branch_id=branch_id,
                settings={"autonomy": "both", "depth": "auto"},
            )
        )

    def test_new_chat_is_confined_pinned_and_idempotent_across_restart(self) -> None:
        first = self._new_chat()
        before = _raw_manifest(first.branch_root)

        self.assertEqual(
            first.branch_root,
            self.runtime_root
            / f"world-{text_sha256('world-a')[:24]}"
            / f"branch-{text_sha256('main')[:24]}",
        )
        self.assertEqual(first.genesis_pin.revision_number, 2)
        self.assertEqual(
            _json(first.branch_root / "ACTIVE" / "WORLD_STATE.json")["branch_id"],
            "main",
        )
        self.assertEqual(
            _json(first.branch_root / "ACTIVE" / "Settings" / "CHAT_SETTINGS.json")[
                "settings"
            ]["autonomy"],
            "both",
        )
        self.assertEqual(
            _raw_manifest(first.branch_root / "GENESIS_PACKAGE"),
            _raw_manifest(GENESIS_ROOT / "hanezawa_core_v1_2"),
        )

        restarted = PiSceneWorldWorkspaceManager(self.runtime_root, GENESIS_ROOT)
        opened = restarted.create_new_chat(
            NewChatWorkspaceRequestV1(
                chat_id="chat-a",
                world_id="world-a",
                branch_id="main",
                # Seed settings are creation-only.  Restart cannot rewrite them.
                settings={"autonomy": "off"},
            )
        )
        self.assertEqual(opened.workspace_sha256, first.workspace_sha256)
        self.assertEqual(_raw_manifest(opened.branch_root), before)

    def test_context_loader_groups_exact_genesis_records_by_real_ids(self) -> None:
        workspace = self._new_chat()
        first = self.manager.load_genesis_context_mappings(workspace)
        restarted = PiSceneWorldWorkspaceManager(self.runtime_root, GENESIS_ROOT)
        second = restarted.load_genesis_context_mappings(workspace)

        self.assertEqual(first.mappings_sha256, second.mappings_sha256)
        self.assertEqual(
            set(first.seed_mapping_fields()),
            {
                "genesis_revision",
                "characters",
                "relationships",
                "relevant_memories",
            },
        )
        self.assertIn("character:sakura_hanezawa", first.characters)
        self.assertIn("character:hana_hanezawa", first.characters)
        self.assertNotIn("character:sakura", first.characters)
        self.assertNotIn("character:hana", first.characters)
        self.assertIn("character:sakura_hanezawa", first.relationships)
        self.assertIn("character:sakura_hanezawa", first.relevant_memories)
        for category in (
            first.characters,
            first.relationships,
            first.relevant_memories,
        ):
            for character_id, grouped in category.items():
                self.assertTrue(character_id.startswith("character:"))
                self.assertEqual(
                    set(grouped),
                    {"character_id", "genesis_record_projections"},
                )
                self.assertEqual(grouped["character_id"], character_id)
                self.assertTrue(grouped["genesis_record_projections"])

        sakura_group = first.characters["character:sakura_hanezawa"]
        exact_projection = next(
            _json(path)
            for path in sorted(
                (workspace.branch_root / "ACTIVE" / "GenesisRecords").rglob(
                    "*.json"
                )
            )
            if _json(path) in sakura_group["genesis_record_projections"]
        )
        self.assertIn(
            exact_projection, sakura_group["genesis_record_projections"]
        )

    def test_invalid_identity_and_cross_chat_open_fail_closed(self) -> None:
        self._new_chat()
        with self.assertRaises(ContractValidationError):
            self.manager.create_new_chat(
                NewChatWorkspaceRequestV1(
                    chat_id="",
                    world_id="world-b",
                    branch_id="main",
                    settings={},
                )
            )
        with self.assertRaises(PermissionError):
            self.manager.open(chat_id="chat-b", world_id="world-a", branch_id="main")

    def test_path_like_world_and_branch_ids_are_hash_confined(self) -> None:
        workspace = self._new_chat(
            chat_id="chat/path-like",
            world_id="../outside-world",
            branch_id="..\\outside-branch",
        )

        self.assertTrue(workspace.branch_root.is_relative_to(self.runtime_root))
        self.assertNotIn("outside-world", workspace.branch_root.parts)
        self.assertNotIn("outside-branch", workspace.branch_root.parts)
        self.assertEqual(
            _json(workspace.branch_root / "BRANCH_IDENTITY.json"),
            {
                "world_id": "../outside-world",
                "branch_id": "..\\outside-branch",
            },
        )

    def test_failed_seed_is_not_published(self) -> None:
        with self.assertRaises(CanonicalizationError):
            self.manager.create_new_chat(
                NewChatWorkspaceRequestV1(
                    chat_id="chat-b",
                    world_id="world-b",
                    branch_id="main",
                    settings={"unsupported": object()},
                )
            )
        self.assertFalse((self.runtime_root / "world-b" / "main").exists())
        self.assertEqual(list((self.runtime_root / "world-b").glob("*.tmp")), [])

    def test_fork_copies_complete_state_and_rewrites_only_identity_files(self) -> None:
        parent = self._new_chat()
        additions = {
            "ACCEPTED/turn-001.json": b'{"accepted":true}',
            "PENDING/recording-turn-002.json": b'{"status":"pending"}',
            "PROVISIONAL/canon-001.json": b'{"truth":"provisional"}',
            "ACTIVE/Settings/branch-controls.json": b'{"autonomy":"mind"}',
        }
        for relative, raw in additions.items():
            target = parent.branch_root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
        omitted = frozenset(
            {
                "WORKSPACE.json",
                "BRANCH_IDENTITY.json",
                "ACTIVE/WORLD_STATE.json",
                "FORK_REBINDING.json",
            }
        )
        inherited = _raw_manifest(parent.branch_root, omitted=omitted)

        request = ForkChatWorkspaceRequestV1(
            parent_chat_id="chat-a",
            world_id="world-a",
            parent_branch_id="main",
            child_chat_id="chat-a-fork",
            child_branch_id="fork-001",
        )
        child = self.manager.fork_chat(request)

        self.assertEqual(_raw_manifest(child.branch_root, omitted=omitted), inherited)
        child_state = _json(child.branch_root / "ACTIVE" / "WORLD_STATE.json")
        self.assertEqual(child_state["chat_id"], "chat-a-fork")
        self.assertEqual(child_state["branch_id"], "fork-001")
        self.assertEqual(child.genesis_pin, parent.genesis_pin)
        child_pending = child.branch_root / "PENDING" / "recording-turn-002.json"
        child_pending.write_bytes(b'{"status":"repaired"}')
        self.assertEqual(
            (parent.branch_root / "PENDING" / "recording-turn-002.json").read_bytes(),
            additions["PENDING/recording-turn-002.json"],
        )

        # Idempotent replay returns the original child even after the parent moves on.
        (parent.branch_root / "ACCEPTED" / "turn-003.json").write_text(
            "{}", encoding="utf-8"
        )
        restarted = PiSceneWorldWorkspaceManager(self.runtime_root, GENESIS_ROOT)
        replayed = restarted.fork_chat(request)
        self.assertEqual(replayed.workspace_sha256, child.workspace_sha256)
        self.assertFalse((replayed.branch_root / "ACCEPTED" / "turn-003.json").exists())

    def test_fork_identity_conflict_is_rejected(self) -> None:
        self._new_chat()
        first = ForkChatWorkspaceRequestV1(
            parent_chat_id="chat-a",
            world_id="world-a",
            parent_branch_id="main",
            child_chat_id="chat-a-fork",
            child_branch_id="fork-001",
        )
        self.manager.fork_chat(first)

        with self.assertRaises(PermissionError):
            self.manager.fork_chat(
                ForkChatWorkspaceRequestV1(
                    parent_chat_id="chat-a",
                    world_id="world-a",
                    parent_branch_id="main",
                    child_chat_id="different-chat",
                    child_branch_id="fork-001",
                )
            )

    def test_lean_store_pending_acceptance_and_session_are_forked_safely(self) -> None:
        parent = self.manager.create_new_chat(
            NewChatWorkspaceRequestV1(
                chat_id="chat-store",
                world_id="world-test",
                branch_id="branch-main",
                settings={"autonomy": "both"},
            )
        )
        store = LeanSceneStore(self.runtime_root)
        accepted = store.accept(
            _pending_candidate(world_id="world-test", branch_id="branch-main")
        )
        session_path = parent.branch_root / "pi-session-cache"
        session_path.mkdir()
        store.promote_pi_session(
            accepted, session_id="session-1", session_path=session_path
        )
        provisional = parent.branch_root / "PROVISIONAL" / "canon-1.json"
        provisional.parent.mkdir(parents=True)
        provisional.write_text('{"status":"provisional"}', encoding="utf-8")
        settings = parent.branch_root / "ACTIVE" / "Settings" / "extra.json"
        settings.write_text('{"depth":"auto"}', encoding="utf-8")
        parent_head_before = store.load_head(
            world_id="world-test", branch_id="branch-main"
        )

        child = self.manager.fork_chat(
            ForkChatWorkspaceRequestV1(
                parent_chat_id="chat-store",
                world_id="world-test",
                parent_branch_id="branch-main",
                child_chat_id="chat-store-fork",
                child_branch_id="branch-fork",
            )
        )
        child_head = store.load_head(
            world_id="world-test", branch_id="branch-fork"
        )
        parent_head_after = store.load_head(
            world_id="world-test", branch_id="branch-main"
        )

        self.assertEqual(child_head.accepted_turn_id, accepted.accepted_turn_id)
        self.assertEqual(child_head.receipt.exact_accepted_prose, accepted.exact_accepted_prose)
        self.assertEqual(child_head.receipt.branch_id, "branch-fork")
        self.assertNotEqual(child_head.accepted_head_sha256, accepted.receipt_sha256)
        self.assertEqual(
            _json(child.branch_root / "BRANCH_HEAD_CACHE.json")[
                "accepted_head_sha256"
            ],
            child_head.accepted_head_sha256,
        )
        self.assertEqual(
            parent_head_after.accepted_head_sha256,
            parent_head_before.accepted_head_sha256,
        )
        self.assertEqual(
            (child.branch_root / "PROVISIONAL" / "canon-1.json").read_bytes(),
            provisional.read_bytes(),
        )
        self.assertEqual(
            (child.branch_root / "ACTIVE" / "Settings" / "extra.json").read_bytes(),
            settings.read_bytes(),
        )
        self.assertIsNone(
            store.load_accepted_pi_session(
                world_id="world-test", branch_id="branch-fork"
            )
        )
        self.assertTrue(
            (
                child.branch_root
                / "sessions"
                / "FORK_SOURCE_ACCEPTED_SESSION.json"
            ).is_file()
        )
        self.assertIsNotNone(
            store.load_accepted_pi_session(
                world_id="world-test", branch_id="branch-main"
            )
        )

    def test_tampered_workspace_or_genesis_is_rejected_on_restart(self) -> None:
        workspace = self._new_chat()
        identity_path = workspace.branch_root / "WORKSPACE.json"
        identity = _json(identity_path)
        identity["parent_chat_id"] = "tampered"
        identity_path.write_text(json.dumps(identity), encoding="utf-8")
        with self.assertRaisesRegex(StateConflictError, "hash changed"):
            self.manager.open(chat_id="chat-a", world_id="world-a", branch_id="main")

        # Restore identity, then prove the pinned package is independently checked.
        identity["parent_chat_id"] = None
        identity_path.write_text(
            json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        package_manifest = workspace.branch_root / "GENESIS_PACKAGE" / "manifest.json"
        package_manifest.write_bytes(package_manifest.read_bytes() + b" ")
        with self.assertRaisesRegex(StateConflictError, "Genesis package changed"):
            self.manager.open(chat_id="chat-a", world_id="world-a", branch_id="main")

    def test_tampered_genesis_projection_is_rejected_on_restart(self) -> None:
        workspace = self._new_chat()
        record = next(
            (workspace.branch_root / "ACTIVE" / "GenesisRecords").rglob("*.json")
        )
        record.write_bytes(record.read_bytes() + b" ")

        with self.assertRaisesRegex(StateConflictError, "projection changed"):
            self.manager.open(chat_id="chat-a", world_id="world-a", branch_id="main")

    def test_branch_bound_dispatcher_cannot_see_another_chat(self) -> None:
        first = self._new_chat()
        second = self._new_chat(chat_id="chat-b", world_id="world-b")
        private = second.branch_root / "ACTIVE" / "Events" / "other-chat.json"
        private.parent.mkdir(parents=True, exist_ok=True)
        private.write_text('{"marker":"only-in-chat-b"}', encoding="utf-8")

        factory = self.manager.mcp_factory(first)
        dispatcher = factory.dispatcher(turn_id="turn-001", maximum_calls=4)
        search = dispatcher.invoke(
            "cera_world_search",
            {"terms": ["only-in-chat-b"], "record_types": None, "limit": 20},
        )
        self.assertEqual(search["records"], [])
        with self.assertRaises(PermissionError):
            dispatcher.invoke(
                "cera_world_read",
                {"path": "../world-b/main/ACTIVE/Events/other-chat.json"},
            )

        listed = dispatcher.invoke(
            "cera_world_list", {"prefix": "ACTIVE/GenesisRecords", "limit": 1}
        )
        self.assertEqual(len(listed["records"]), 1)
        exact = dispatcher.invoke(
            "cera_world_read", {"path": listed["records"][0]["path"]}
        )
        self.assertEqual(
            exact["content"]["genesis_revision_id"], first.genesis_pin.revision_id
        )
        self.assertIsInstance(
            factory.bridge(turn_id="turn-002"), RequestBoundWorldMcpBridge
        )

    def test_request_bound_bridge_is_lazy_one_use_and_always_stops(self) -> None:
        workspace = self._new_chat()
        raw_bridges: list[_FakeRawBridge] = []

        def create_raw(dispatcher):
            bridge = _FakeRawBridge(dispatcher)
            raw_bridges.append(bridge)
            return bridge

        request_bridge = self.manager.mcp_factory(
            workspace, raw_bridge_factory=create_raw
        ).bridge(turn_id="turn-001", maximum_calls=3)
        self.assertEqual(raw_bridges, [])
        self.assertEqual(request_bridge.runtime_binding, "fake-runtime-binding")
        self.assertEqual(request_bridge.runtime_binding, "fake-runtime-binding")
        self.assertEqual(len(raw_bridges), 1)
        self.assertEqual(raw_bridges[0].starts, 1)

        self.assertEqual(request_bridge.finalize(object()), {"finalized": True})
        self.assertEqual(raw_bridges[0].stops, [False])
        with self.assertRaisesRegex(StateConflictError, "terminal"):
            _ = request_bridge.runtime_binding
        with self.assertRaisesRegex(StateConflictError, "terminal"):
            request_bridge.finalize(object())

    def test_request_bound_bridge_is_terminal_after_binding_or_finalize_failure(self) -> None:
        workspace = self._new_chat()
        factory_calls = 0

        def fail_factory():
            nonlocal factory_calls
            factory_calls += 1
            raise RuntimeError("factory failed")

        factory_failure = RequestBoundWorldMcpBridge(fail_factory)
        with self.assertRaisesRegex(RuntimeError, "factory failed"):
            _ = factory_failure.runtime_binding
        with self.assertRaisesRegex(StateConflictError, "terminal"):
            _ = factory_failure.runtime_binding
        self.assertEqual(factory_calls, 1)

        binding_bridges: list[_FailingBindingBridge] = []

        def create_binding_failure(dispatcher):
            bridge = _FailingBindingBridge(dispatcher)
            binding_bridges.append(bridge)
            return bridge

        binding_failure = self.manager.mcp_factory(
            workspace, raw_bridge_factory=create_binding_failure
        ).bridge(turn_id="turn-binding-failure")
        with self.assertRaisesRegex(RuntimeError, "binding failed"):
            _ = binding_failure.runtime_binding
        self.assertEqual(binding_bridges[0].stops, [True])
        with self.assertRaisesRegex(StateConflictError, "terminal"):
            _ = binding_failure.runtime_binding

        finalize_bridges: list[_FailingFinalizeBridge] = []

        def create_finalize_failure(dispatcher):
            bridge = _FailingFinalizeBridge(dispatcher)
            finalize_bridges.append(bridge)
            return bridge

        finalize_failure = self.manager.mcp_factory(
            workspace, raw_bridge_factory=create_finalize_failure
        ).bridge(turn_id="turn-finalize-failure")
        self.assertEqual(finalize_failure.runtime_binding, "fake-runtime-binding")
        with self.assertRaisesRegex(RuntimeError, "finalize failed"):
            finalize_failure.finalize(object())
        self.assertEqual(finalize_bridges[0].stops, [False])
        with self.assertRaisesRegex(StateConflictError, "terminal"):
            _ = finalize_failure.runtime_binding

    @unittest.skipUnless(hasattr(os, "symlink"), "symlink API unavailable")
    def test_fork_rejects_linked_parent_content_when_supported(self) -> None:
        parent = self._new_chat()
        outside = Path(self.temporary.name) / "outside.json"
        outside.write_text("{}", encoding="utf-8")
        link = parent.branch_root / "ACTIVE" / "Events" / "linked.json"
        link.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.symlink(outside, link)
        except OSError:
            self.skipTest("host does not grant symlink creation")
        with self.assertRaisesRegex(StateConflictError, "link or reparse"):
            self.manager.fork_chat(
                ForkChatWorkspaceRequestV1(
                    parent_chat_id="chat-a",
                    world_id="world-a",
                    parent_branch_id="main",
                    child_chat_id="chat-a-fork",
                    child_branch_id="fork-001",
                )
            )


if __name__ == "__main__":
    unittest.main()
