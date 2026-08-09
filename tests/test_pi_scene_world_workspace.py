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
from cera.continuous.world_mcp import ContinuousWorldMcpBridge
from cera.pi_scene.world_workspace import (
    AcceptedGenesisCatalog,
    ForkChatWorkspaceRequestV1,
    NewChatWorkspaceRequestV1,
    PiSceneWorldWorkspaceManager,
)


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

    def _new_chat(self, *, chat_id: str = "chat-a", world_id: str = "world-a", branch_id: str = "main"):
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

    def test_invalid_identity_and_cross_chat_open_fail_closed(self) -> None:
        self._new_chat()
        with self.assertRaises(ContractValidationError):
            self.manager.create_new_chat(
                NewChatWorkspaceRequestV1(
                    chat_id="../chat-b",
                    world_id="world-b",
                    branch_id="main",
                    settings={},
                )
            )
        with self.assertRaises(PermissionError):
            self.manager.open(chat_id="chat-b", world_id="world-a", branch_id="main")

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
        omitted = frozenset({"WORKSPACE.json", "ACTIVE/WORLD_STATE.json"})
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
        self.assertIsInstance(factory.bridge(turn_id="turn-002"), ContinuousWorldMcpBridge)

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
