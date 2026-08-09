"""Branch-scoped Genesis materialization and bounded Codex world access.

The module deliberately owns only filesystem custody.  Callers supply story
state and settings as ordinary files after creation; this layer never infers
story meaning or promotes a record.  A new chat is seeded from the newest
revision in an explicitly accepted Genesis catalog.  A fork copies the entire
parent workspace and changes only the two branch-identity documents.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import shutil
from threading import RLock
from typing import Any, Mapping
from uuid import uuid4

from cera.errors import ContractValidationError, StateConflictError
from cera.serialization import canonical_sha256, to_primitive
from cera.continuous.sessions import ContinuousSessionRole
from cera.continuous.world_mcp import (
    ContinuousWorldMcpBridge,
    ContinuousWorldToolDispatcher,
    WORLD_MCP_MAXIMUM_CALLS,
)

from ._world_workspace_files import (
    SHA256_PATTERN,
    assert_plain_tree,
    is_link_or_reparse,
    read_json_object,
    safe_slug,
    sha256_bytes,
    tree_sha256,
    write_json,
)
from .genesis_catalog import (
    AcceptedGenesisCatalog,
    AcceptedGenesisRevisionV1,
    GenesisRevisionPinV1,
)


@dataclass(frozen=True, slots=True)
class NewChatWorkspaceRequestV1:
    chat_id: str
    world_id: str
    branch_id: str
    settings: Mapping[str, Any]
    initial_scene_id: str = "scene-001"
    genesis_package_id: str | None = None


@dataclass(frozen=True, slots=True)
class ForkChatWorkspaceRequestV1:
    parent_chat_id: str
    world_id: str
    parent_branch_id: str
    child_chat_id: str
    child_branch_id: str


@dataclass(frozen=True, slots=True)
class BranchWorldWorkspaceV1:
    chat_id: str
    world_id: str
    branch_id: str
    branch_root: Path
    genesis_pin: GenesisRevisionPinV1
    genesis_projection_tree_sha256: str
    workspace_sha256: str


class BranchBoundWorldMcpFactory:
    """Construct request-local, read-only Planner lookup for exactly one branch."""

    def __init__(self, workspace: BranchWorldWorkspaceV1) -> None:
        self.workspace = workspace

    def dispatcher(
        self, *, turn_id: str | None = None, maximum_calls: int = WORLD_MCP_MAXIMUM_CALLS
    ) -> ContinuousWorldToolDispatcher:
        return ContinuousWorldToolDispatcher(
            self.workspace.branch_root,
            ContinuousSessionRole.PLANNER,
            current_turn_id=turn_id,
            maximum_calls=maximum_calls,
        )

    def bridge(
        self, *, turn_id: str | None = None, maximum_calls: int = WORLD_MCP_MAXIMUM_CALLS
    ) -> ContinuousWorldMcpBridge:
        return ContinuousWorldMcpBridge(
            self.dispatcher(turn_id=turn_id, maximum_calls=maximum_calls)
        )


class PiSceneWorldWorkspaceManager:
    """Atomic new-chat/fork materialization with restart-safe identity custody."""

    _WORKSPACE_SCHEMA = "cera.pi_scene_world_workspace.v1"
    _WORLD_STATE_SCHEMA = "cera.pi_scene_world_state.v1"
    _IDENTITY_FILES = frozenset({"WORKSPACE.json", "ACTIVE/WORLD_STATE.json"})

    def __init__(self, runtime_root: Path, accepted_genesis_root: Path) -> None:
        if not runtime_root.is_absolute():
            raise ContractValidationError("Pi Scene world runtime root must be absolute")
        runtime_root.mkdir(parents=True, exist_ok=True)
        self.runtime_root = runtime_root.resolve()
        if is_link_or_reparse(self.runtime_root):
            raise StateConflictError("Pi Scene world runtime root cannot be linked")
        self.catalog = AcceptedGenesisCatalog(accepted_genesis_root)
        self._lock = RLock()

    def create_new_chat(
        self, request: NewChatWorkspaceRequestV1
    ) -> BranchWorldWorkspaceV1:
        chat_id, world_id, branch_id = self._validated_identity(
            request.chat_id, request.world_id, request.branch_id
        )
        safe_slug(request.initial_scene_id, "initial_scene_id")
        target = self._branch_root(world_id, branch_id)
        with self._lock:
            if target.exists():
                opened = self.open(chat_id=chat_id, world_id=world_id, branch_id=branch_id)
                if (
                    request.genesis_package_id is not None
                    and opened.genesis_pin.package_id != request.genesis_package_id
                ):
                    raise StateConflictError("existing chat is pinned to another Genesis package")
                return opened
            selected = self.catalog.newest(package_id=request.genesis_package_id)
            staging = self._staging_root(world_id, branch_id)
            try:
                self._materialize_genesis(staging, selected)
                write_json(
                    staging / "ACTIVE" / "Settings" / "CHAT_SETTINGS.json",
                    {
                        "schema_version": "cera.pi_scene_chat_settings.v1",
                        "_cera_revision": 1,
                        "settings": to_primitive(request.settings),
                    },
                )
                write_json(
                    staging / "ACTIVE" / "WORLD_STATE.json",
                    {
                        "schema_version": self._WORLD_STATE_SCHEMA,
                        "_cera_revision": 1,
                        "chat_id": chat_id,
                        "world_id": world_id,
                        "branch_id": branch_id,
                        "genesis_revision_id": selected.pin.revision_id,
                        "current_scene_id": request.initial_scene_id,
                        "accepted_turn_ids": [],
                    },
                )
                (staging / "DERIVED").mkdir(parents=True, exist_ok=True)
                workspace = self._workspace_payload(
                    kind="new_chat",
                    chat_id=chat_id,
                    world_id=world_id,
                    branch_id=branch_id,
                    genesis_pin=selected.pin,
                    genesis_projection_tree_sha256=tree_sha256(
                        staging / "ACTIVE" / "GenesisRecords"
                    ),
                    parent_chat_id=None,
                    parent_branch_id=None,
                    source_tree_sha256=selected.pin.package_tree_sha256,
                    inherited_files_sha256=None,
                )
                write_json(staging / "WORKSPACE.json", workspace)
                assert_plain_tree(staging, "new chat workspace")
                self._publish_staging(staging, target)
            except BaseException:
                self._remove_staging(staging)
                raise
            return self.open(chat_id=chat_id, world_id=world_id, branch_id=branch_id)

    def fork_chat(
        self, request: ForkChatWorkspaceRequestV1
    ) -> BranchWorldWorkspaceV1:
        parent_chat_id, world_id, parent_branch_id = self._validated_identity(
            request.parent_chat_id, request.world_id, request.parent_branch_id
        )
        child_chat_id = safe_slug(request.child_chat_id, "child_chat_id")
        child_branch_id = safe_slug(request.child_branch_id, "child_branch_id")
        if child_branch_id == parent_branch_id:
            raise ContractValidationError("fork child branch must differ from its parent")
        parent = self.open(
            chat_id=parent_chat_id, world_id=world_id, branch_id=parent_branch_id
        )
        target = self._branch_root(world_id, child_branch_id)
        with self._lock:
            if target.exists():
                child = self.open(
                    chat_id=child_chat_id, world_id=world_id, branch_id=child_branch_id
                )
                metadata = read_json_object(
                    target / "WORKSPACE.json", "child workspace"
                )
                if (
                    metadata["parent_chat_id"] != parent_chat_id
                    or metadata["parent_branch_id"] != parent_branch_id
                ):
                    raise StateConflictError("existing child belongs to another fork source")
                return child
            assert_plain_tree(parent.branch_root, "parent chat workspace")
            parent_tree_sha256 = tree_sha256(parent.branch_root)
            inherited_sha256 = tree_sha256(
                parent.branch_root, omitted=self._IDENTITY_FILES
            )
            staging = self._staging_root(world_id, child_branch_id)
            self._remove_staging(staging)
            try:
                shutil.copytree(parent.branch_root, staging, copy_function=shutil.copy2)
                assert_plain_tree(staging, "fork staging workspace")
                state_path = staging / "ACTIVE" / "WORLD_STATE.json"
                state = read_json_object(state_path, "forked WORLD_STATE")
                state["chat_id"] = child_chat_id
                state["branch_id"] = child_branch_id
                write_json(state_path, state)
                workspace = self._workspace_payload(
                    kind="fork",
                    chat_id=child_chat_id,
                    world_id=world_id,
                    branch_id=child_branch_id,
                    genesis_pin=parent.genesis_pin,
                    genesis_projection_tree_sha256=(
                        parent.genesis_projection_tree_sha256
                    ),
                    parent_chat_id=parent_chat_id,
                    parent_branch_id=parent_branch_id,
                    source_tree_sha256=parent_tree_sha256,
                    inherited_files_sha256=inherited_sha256,
                )
                write_json(staging / "WORKSPACE.json", workspace)
                if tree_sha256(staging, omitted=self._IDENTITY_FILES) != inherited_sha256:
                    raise StateConflictError("fork changed inherited workspace files")
                if tree_sha256(parent.branch_root) != parent_tree_sha256:
                    raise StateConflictError("parent workspace changed during fork")
                self._publish_staging(staging, target)
            except BaseException:
                self._remove_staging(staging)
                raise
            return self.open(
                chat_id=child_chat_id, world_id=world_id, branch_id=child_branch_id
            )

    def open(
        self, *, chat_id: str, world_id: str, branch_id: str
    ) -> BranchWorldWorkspaceV1:
        chat_id, world_id, branch_id = self._validated_identity(chat_id, world_id, branch_id)
        root = self._branch_root(world_id, branch_id)
        assert_plain_tree(root, "Pi Scene branch workspace")
        metadata = read_json_object(root / "WORKSPACE.json", "workspace identity")
        expected_fields = {
            "schema_version",
            "kind",
            "chat_id",
            "world_id",
            "branch_id",
            "genesis_pin",
            "genesis_projection_tree_sha256",
            "parent_chat_id",
            "parent_branch_id",
            "source_tree_sha256",
            "inherited_files_sha256",
            "workspace_sha256",
        }
        if set(metadata) != expected_fields or metadata["schema_version"] != self._WORKSPACE_SCHEMA:
            raise StateConflictError("workspace identity fields changed")
        if (
            metadata["chat_id"] != chat_id
            or metadata["world_id"] != world_id
            or metadata["branch_id"] != branch_id
        ):
            raise PermissionError("workspace identity does not match the requested chat")
        stored_sha256 = metadata.pop("workspace_sha256")
        if not isinstance(stored_sha256, str) or canonical_sha256(metadata) != stored_sha256:
            raise StateConflictError("workspace identity hash changed")
        if metadata["kind"] == "new_chat":
            if (
                metadata["parent_chat_id"] is not None
                or metadata["parent_branch_id"] is not None
                or metadata["inherited_files_sha256"] is not None
            ):
                raise StateConflictError("new-chat workspace carries fork custody")
        elif metadata["kind"] == "fork":
            if (
                not isinstance(metadata["parent_chat_id"], str)
                or not isinstance(metadata["parent_branch_id"], str)
                or not isinstance(metadata["inherited_files_sha256"], str)
                or SHA256_PATTERN.fullmatch(metadata["inherited_files_sha256"]) is None
            ):
                raise StateConflictError("fork workspace lacks parent custody")
            safe_slug(metadata["parent_chat_id"], "parent_chat_id")
            safe_slug(metadata["parent_branch_id"], "parent_branch_id")
        else:
            raise StateConflictError("workspace kind is invalid")
        if (
            not isinstance(metadata["source_tree_sha256"], str)
            or SHA256_PATTERN.fullmatch(metadata["source_tree_sha256"]) is None
        ):
            raise StateConflictError("workspace source-tree hash is invalid")
        projection_sha256 = metadata["genesis_projection_tree_sha256"]
        if (
            not isinstance(projection_sha256, str)
            or SHA256_PATTERN.fullmatch(projection_sha256) is None
            or tree_sha256(root / "ACTIVE" / "GenesisRecords")
            != projection_sha256
        ):
            raise StateConflictError("branch-local Genesis projection changed")
        pin = GenesisRevisionPinV1.from_dict(metadata["genesis_pin"])
        pin_on_disk = GenesisRevisionPinV1.from_dict(
            read_json_object(root / "GENESIS_PIN.json", "local Genesis pin")
        )
        if pin != pin_on_disk:
            raise StateConflictError("workspace and local Genesis pins disagree")
        package_root = root / "GENESIS_PACKAGE"
        if (
            tree_sha256(package_root) != pin.package_tree_sha256
            or sha256_bytes((package_root / "manifest.json").read_bytes())
            != pin.manifest_sha256
        ):
            raise StateConflictError("branch-local Genesis package changed")
        state = read_json_object(
            root / "ACTIVE" / "WORLD_STATE.json", "WORLD_STATE"
        )
        if (
            state.get("chat_id") != chat_id
            or state.get("world_id") != world_id
            or state.get("branch_id") != branch_id
            or state.get("genesis_revision_id") != pin.revision_id
        ):
            raise StateConflictError("branch world-state identity changed")
        return BranchWorldWorkspaceV1(
            chat_id=chat_id,
            world_id=world_id,
            branch_id=branch_id,
            branch_root=root,
            genesis_pin=pin,
            genesis_projection_tree_sha256=projection_sha256,
            workspace_sha256=stored_sha256,
        )

    def mcp_factory(self, workspace: BranchWorldWorkspaceV1) -> BranchBoundWorldMcpFactory:
        reopened = self.open(
            chat_id=workspace.chat_id,
            world_id=workspace.world_id,
            branch_id=workspace.branch_id,
        )
        if reopened.workspace_sha256 != workspace.workspace_sha256:
            raise StateConflictError("branch workspace handle is stale")
        return BranchBoundWorldMcpFactory(reopened)

    def _materialize_genesis(
        self, staging: Path, selected: AcceptedGenesisRevisionV1
    ) -> None:
        shutil.copytree(
            selected.package_root,
            staging / "GENESIS_PACKAGE",
            copy_function=shutil.copy2,
        )
        write_json(staging / "GENESIS_PIN.json", to_primitive(selected.pin))
        modules = read_json_object(
            selected.package_root / "manifest.json", "selected Genesis manifest"
        )["modules"]
        for module in modules:
            source_relative = Path(module["relative_path"]).as_posix()
            source = selected.package_root.joinpath(*Path(source_relative).parts)
            payload = read_json_object(source, "selected Genesis module")
            records = payload.get("records")
            if not isinstance(records, list):
                raise ContractValidationError("Genesis module records are invalid")
            module_key = source_relative.removeprefix("modules/").removesuffix(".json")
            for index, record in enumerate(records):
                if not isinstance(record, dict):
                    raise ContractValidationError("Genesis record is invalid")
                record_id = record.get("record_id")
                if not isinstance(record_id, str) or not record_id.strip():
                    raise ContractValidationError("Genesis record identity is invalid")
                owners = record.get("knowledge_owner_ids")
                owner = record.get("owner_id")
                knowledge_owner = owner
                if knowledge_owner is None and isinstance(owners, list) and len(owners) == 1:
                    knowledge_owner = owners[0]
                projection = {
                    "schema_version": "cera.pi_scene_genesis_record_projection.v1",
                    "_cera_revision": 1,
                    "genesis_revision_id": selected.pin.revision_id,
                    "source_relative_path": source_relative,
                    "source_content_sha256": module["content_sha256"],
                    "visibility": record.get("visibility", "system_private"),
                    "knowledge_owner_id": knowledge_owner,
                    "record": record,
                }
                filename = f"{index:04d}-{hashlib.sha256(record_id.encode('utf-8')).hexdigest()}.json"
                write_json(
                    staging / "ACTIVE" / "GenesisRecords" / module_key / filename,
                    projection,
                )

    def _workspace_payload(
        self,
        *,
        kind: str,
        chat_id: str,
        world_id: str,
        branch_id: str,
        genesis_pin: GenesisRevisionPinV1,
        genesis_projection_tree_sha256: str,
        parent_chat_id: str | None,
        parent_branch_id: str | None,
        source_tree_sha256: str,
        inherited_files_sha256: str | None,
    ) -> dict[str, Any]:
        payload = {
            "schema_version": self._WORKSPACE_SCHEMA,
            "kind": kind,
            "chat_id": chat_id,
            "world_id": world_id,
            "branch_id": branch_id,
            "genesis_pin": to_primitive(genesis_pin),
            "genesis_projection_tree_sha256": genesis_projection_tree_sha256,
            "parent_chat_id": parent_chat_id,
            "parent_branch_id": parent_branch_id,
            "source_tree_sha256": source_tree_sha256,
            "inherited_files_sha256": inherited_files_sha256,
        }
        return {**payload, "workspace_sha256": canonical_sha256(payload)}

    def _validated_identity(self, chat_id: str, world_id: str, branch_id: str) -> tuple[str, str, str]:
        return (
            safe_slug(chat_id, "chat_id"),
            safe_slug(world_id, "world_id"),
            safe_slug(branch_id, "branch_id"),
        )

    def _branch_root(self, world_id: str, branch_id: str) -> Path:
        world_id = safe_slug(world_id, "world_id")
        branch_id = safe_slug(branch_id, "branch_id")
        root = self.runtime_root / world_id / branch_id
        if root.parent.parent != self.runtime_root:
            raise PermissionError("world workspace escaped its configured root")
        return root

    def _staging_root(self, world_id: str, branch_id: str) -> Path:
        world_root = self.runtime_root / safe_slug(world_id, "world_id")
        world_root.mkdir(parents=True, exist_ok=True)
        if is_link_or_reparse(world_root):
            raise StateConflictError("world directory cannot be linked")
        return world_root / f".{safe_slug(branch_id, 'branch_id')}-{uuid4().hex}.tmp"

    def _publish_staging(self, staging: Path, target: Path) -> None:
        if target.exists():
            raise StateConflictError("world branch appeared during materialization")
        os.replace(staging, target)

    def _remove_staging(self, staging: Path) -> None:
        if not staging.exists():
            return
        if staging.parent.parent != self.runtime_root or not staging.name.endswith(".tmp"):
            raise StateConflictError("refused to remove an unconfined staging directory")
        shutil.rmtree(staging)
