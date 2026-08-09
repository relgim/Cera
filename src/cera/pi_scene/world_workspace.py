"""Branch-scoped Genesis materialization and bounded Codex world access.

The module deliberately owns only filesystem custody.  Callers supply story
state and settings as ordinary files after creation; this layer never infers
story meaning or promotes a record.  A new chat is seeded from the newest
revision in an explicitly accepted Genesis catalog.  A fork copies the entire
parent workspace, rebinds accepted-receipt custody to the child branch, and
archives the parent's soft provider session so it cannot cross branches.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Any
from uuid import uuid4

from cera.continuous.sessions import ContinuousSessionRole
from cera.continuous.world_mcp import (
    WORLD_MCP_MAXIMUM_CALLS,
    ContinuousWorldMcpBridge,
    ContinuousWorldToolDispatcher,
)
from cera.errors import ContractValidationError, StateConflictError
from cera.providers import CodexMcpRuntimeBinding
from cera.serialization import canonical_sha256, to_primitive

from ._world_workspace_files import (
    SHA256_PATTERN,
    assert_plain_tree,
    is_link_or_reparse,
    lean_scene_branch_keys,
    lean_scene_branch_root,
    read_json_object,
    required_identity,
    sha256_bytes,
    tree_sha256,
    write_json,
)
from .genesis_catalog import (
    AcceptedGenesisCatalog,
    AcceptedGenesisRevisionV1,
    GenesisRevisionPinV1,
)
from .lineage import (
    LeanActiveLineageV1,
    accepted_object_directory_name,
    active_lineage_from_payload,
    active_lineage_payload,
)
from .store import LeanSceneStore


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


@dataclass(frozen=True, slots=True)
class PinnedGenesisContextMappingsV1:
    """Exact provider-free Genesis groupings for PiSceneContextSeedV1 fields."""

    genesis_revision: str
    genesis_projection_tree_sha256: str
    characters: Mapping[str, Mapping[str, object]]
    relationships: Mapping[str, Mapping[str, object]]
    relevant_memories: Mapping[str, Mapping[str, object]]

    def seed_mapping_fields(self) -> dict[str, object]:
        """Return only the Genesis-owned ``PiSceneContextSeedV1`` fields."""

        return {
            "genesis_revision": self.genesis_revision,
            "characters": self.characters,
            "relationships": self.relationships,
            "relevant_memories": self.relevant_memories,
        }

    @property
    def mappings_sha256(self) -> str:
        return canonical_sha256(
            {
                "genesis_revision": self.genesis_revision,
                "genesis_projection_tree_sha256": (
                    self.genesis_projection_tree_sha256
                ),
                "characters": self.characters,
                "relationships": self.relationships,
                "relevant_memories": self.relevant_memories,
            }
        )


class BranchBoundWorldMcpFactory:
    """Construct request-local, read-only Planner lookup for exactly one branch."""

    def __init__(
        self,
        workspace: BranchWorldWorkspaceV1,
        *,
        raw_bridge_factory: Callable[
            [ContinuousWorldToolDispatcher], ContinuousWorldMcpBridge
        ] = ContinuousWorldMcpBridge,
    ) -> None:
        self.workspace = workspace
        self.raw_bridge_factory = raw_bridge_factory

    def dispatcher(
        self, *, turn_id: str | None = None, maximum_calls: int = WORLD_MCP_MAXIMUM_CALLS
    ) -> ContinuousWorldToolDispatcher:
        return ContinuousWorldToolDispatcher(
            self.workspace.branch_root,
            ContinuousSessionRole.PLANNER,
            world_id=self.workspace.world_id,
            branch_id=self.workspace.branch_id,
            current_turn_id=turn_id,
            maximum_calls=maximum_calls,
            require_private_search_scope=True,
        )

    def bridge(
        self, *, turn_id: str | None = None, maximum_calls: int = WORLD_MCP_MAXIMUM_CALLS
    ) -> RequestBoundWorldMcpBridge:
        dispatcher = self.dispatcher(
            turn_id=turn_id, maximum_calls=maximum_calls
        )
        return RequestBoundWorldMcpBridge(
            lambda: self.raw_bridge_factory(dispatcher)
        )


class RequestBoundWorldMcpBridge:
    """Lazy one-turn MCP bridge that always closes after finalize or abort."""

    def __init__(self, bridge_factory: Callable[[], ContinuousWorldMcpBridge]) -> None:
        self._bridge_factory = bridge_factory
        self._bridge: ContinuousWorldMcpBridge | None = None
        self._terminal = False

    @property
    def runtime_binding(self) -> CodexMcpRuntimeBinding:
        if self._terminal:
            raise StateConflictError("request-bound world MCP bridge is terminal")
        if self._bridge is None:
            try:
                bridge = self._bridge_factory()
            except BaseException:
                self._terminal = True
                raise
            self._bridge = bridge
            try:
                bridge.start()
            except BaseException:
                self._terminal = True
                bridge.stop(suppress_errors=True)
                raise
        try:
            return self._bridge.runtime_binding
        except BaseException:
            bridge = self._bridge
            self._terminal = True
            bridge.stop(suppress_errors=True)
            raise

    def finalize(self, provider_result: Any) -> dict[str, Any]:
        if self._terminal or self._bridge is None:
            raise StateConflictError(
                "request-bound world MCP bridge was not started or is terminal"
            )
        bridge = self._bridge
        self._terminal = True
        try:
            return bridge.finalize(provider_result)
        finally:
            bridge.stop(suppress_errors=False)

    def abort(self) -> None:
        if self._terminal:
            return
        self._terminal = True
        if self._bridge is not None:
            self._bridge.stop(suppress_errors=True)

    def __enter__(self) -> RequestBoundWorldMcpBridge:
        return self

    def __exit__(
        self,
        exc_type: object | None,
        exc: object | None,
        traceback: object | None,
    ) -> None:
        del exc_type, exc, traceback
        self.abort()


class PiSceneWorldWorkspaceManager:
    """Atomic new-chat/fork materialization with restart-safe identity custody."""

    _WORKSPACE_SCHEMA = "cera.pi_scene_world_workspace.v1"
    _WORLD_STATE_SCHEMA = "cera.pi_scene_world_state.v1"
    _IDENTITY_FILES = frozenset(
        {"WORKSPACE.json", "BRANCH_IDENTITY.json", "ACTIVE/WORLD_STATE.json"}
    )

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
        required_identity(request.initial_scene_id, "initial_scene_id")
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
                    staging / "BRANCH_IDENTITY.json",
                    {"world_id": world_id, "branch_id": branch_id},
                )
                (staging / "accepted").mkdir(parents=True, exist_ok=True)
                (staging / "sessions").mkdir(parents=True, exist_ok=True)
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
        child_chat_id = required_identity(request.child_chat_id, "child_chat_id")
        child_branch_id = required_identity(request.child_branch_id, "child_branch_id")
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
                write_json(
                    staging / "BRANCH_IDENTITY.json",
                    {"world_id": world_id, "branch_id": child_branch_id},
                )
                selected_payloads = LeanSceneStore(
                    self.runtime_root
                ).accepted_branch_payloads(
                    world_id=world_id,
                    branch_id=parent_branch_id,
                )
                rewritten_receipts = self._rebind_accepted_receipts(
                    staging,
                    source_world_id=world_id,
                    source_branch_id=parent_branch_id,
                    child_branch_id=child_branch_id,
                    selected_receipt_sha256s=tuple(
                        str(value["accepted_receipt_sha256"])
                        for value in selected_payloads
                    ),
                )
                rewritten_sessions = self._isolate_forked_session_cache(staging)
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
                rewritten_paths = self._IDENTITY_FILES.union(
                    rewritten_receipts, rewritten_sessions
                )
                if tree_sha256(staging, omitted=rewritten_paths) != tree_sha256(
                    parent.branch_root,
                    omitted=rewritten_paths,
                ):
                    raise StateConflictError("fork changed non-identity workspace files")
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
            required_identity(metadata["parent_chat_id"], "parent_chat_id")
            required_identity(metadata["parent_branch_id"], "parent_branch_id")
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
        branch_identity = read_json_object(
            root / "BRANCH_IDENTITY.json", "Lean Scene branch identity"
        )
        if branch_identity != {"world_id": world_id, "branch_id": branch_id}:
            raise StateConflictError("Lean Scene branch identity changed")
        # Prove that the same physical root and receipt chain are immediately
        # consumable by the authoritative lean accepted-state store.
        LeanSceneStore(self.runtime_root).load_head(
            world_id=world_id, branch_id=branch_id
        )
        return BranchWorldWorkspaceV1(
            chat_id=chat_id,
            world_id=world_id,
            branch_id=branch_id,
            branch_root=root,
            genesis_pin=pin,
            genesis_projection_tree_sha256=projection_sha256,
            workspace_sha256=stored_sha256,
        )

    def mcp_factory(
        self,
        workspace: BranchWorldWorkspaceV1,
        *,
        raw_bridge_factory: Callable[
            [ContinuousWorldToolDispatcher], ContinuousWorldMcpBridge
        ] = ContinuousWorldMcpBridge,
    ) -> BranchBoundWorldMcpFactory:
        reopened = self.open(
            chat_id=workspace.chat_id,
            world_id=workspace.world_id,
            branch_id=workspace.branch_id,
        )
        if reopened.workspace_sha256 != workspace.workspace_sha256:
            raise StateConflictError("branch workspace handle is stale")
        return BranchBoundWorldMcpFactory(
            reopened, raw_bridge_factory=raw_bridge_factory
        )

    def load_genesis_context_mappings(
        self, workspace: BranchWorldWorkspaceV1
    ) -> PinnedGenesisContextMappingsV1:
        """Group exact pinned projections without creating semantic summaries.

        Story-start scene, presence, public state, voice examples, craft, and
        adult handoff remain explicit caller inputs.  This method supplies only
        the three Genesis mappings consumed by ``PiSceneContextSeedV1``.
        """

        opened = self.open(
            chat_id=workspace.chat_id,
            world_id=workspace.world_id,
            branch_id=workspace.branch_id,
        )
        if opened.workspace_sha256 != workspace.workspace_sha256:
            raise StateConflictError("branch workspace handle is stale")
        grouped: dict[str, dict[str, list[dict[str, Any]]]] = {
            "characters": {},
            "relationships": {},
            "relevant_memories": {},
        }
        projections = opened.branch_root / "ACTIVE" / "GenesisRecords"
        for path in sorted(projections.rglob("*.json")):
            projection = read_json_object(path, "Genesis record projection")
            if (
                projection.get("schema_version")
                != "cera.pi_scene_genesis_record_projection.v1"
                or projection.get("genesis_revision_id")
                != opened.genesis_pin.revision_id
                or not isinstance(projection.get("record"), dict)
            ):
                raise StateConflictError("Genesis context projection changed")
            record = projection["record"]
            subject_ids = record.get("subject_ids")
            ids: set[str] = set()
            if isinstance(subject_ids, list):
                ids.update(
                    value
                    for value in subject_ids
                    if isinstance(value, str) and value.startswith("character:")
                )
            for name in (
                "owner_id",
                "relationship_from_id",
                "relationship_to_id",
            ):
                value = record.get(name)
                if isinstance(value, str) and value.startswith("character:"):
                    ids.add(value)
            owners = record.get("knowledge_owner_ids")
            if isinstance(owners, list):
                ids.update(
                    value
                    for value in owners
                    if isinstance(value, str) and value.startswith("character:")
                )
            record_type = str(record.get("record_type", ""))
            source_path = str(projection.get("source_relative_path", ""))
            if record_type == "memory_seed" or source_path.startswith(
                "modules/memories/"
            ):
                category = "relevant_memories"
                owner_ids: set[str] = set()
                owner_id = record.get("owner_id")
                if isinstance(owner_id, str) and owner_id.startswith("character:"):
                    owner_ids.add(owner_id)
                if isinstance(owners, list):
                    owner_ids.update(
                        value
                        for value in owners
                        if isinstance(value, str)
                        and value.startswith("character:")
                    )
                target_ids = owner_ids or ids
            elif (
                "relationship" in record_type
                or source_path == "modules/relationships_family.json"
                or record.get("relationship_from_id") is not None
                or record.get("relationship_to_id") is not None
            ):
                category = "relationships"
                target_ids = ids
            else:
                category = "characters"
                target_ids = ids
            for character_id in sorted(target_ids):
                grouped[category].setdefault(character_id, []).append(projection)
        if not grouped["characters"]:
            raise StateConflictError("pinned Genesis contains no character projections")

        def mappings(category: str) -> dict[str, Mapping[str, object]]:
            return {
                character_id: {
                    "character_id": character_id,
                    "genesis_record_projections": tuple(records),
                }
                for character_id, records in sorted(grouped[category].items())
            }

        return PinnedGenesisContextMappingsV1(
            genesis_revision=opened.genesis_pin.revision_id,
            genesis_projection_tree_sha256=(
                opened.genesis_projection_tree_sha256
            ),
            characters=mappings("characters"),
            relationships=mappings("relationships"),
            relevant_memories=mappings("relevant_memories"),
        )

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
                filename = f"{index:04d}.json"
                write_json(
                    staging / "ACTIVE" / "GenesisRecords" / module_key / filename,
                    projection,
                )

    def _rebind_accepted_receipts(
        self,
        staging: Path,
        *,
        source_world_id: str,
        source_branch_id: str,
        child_branch_id: str,
        selected_receipt_sha256s: tuple[str, ...],
    ) -> frozenset[str]:
        """Rebind copied immutable prose custody to the child receipt chain.

        Exact accepted prose, Writer receipts, recording heads/bundles, and turn
        identities remain unchanged.  Only branch identity and the mechanical
        parent receipt hash chain change.  The source/new hashes are preserved
        in a separate fork receipt.
        """

        all_paths = sorted((staging / "accepted").glob("*/ACCEPTED_RECEIPT.json"))
        by_sha256: dict[str, tuple[Path, dict[str, Any]]] = {}
        for path in all_paths:
            raw = read_json_object(path, "accepted receipt")
            receipt_sha256 = canonical_sha256(raw)
            if receipt_sha256 in by_sha256:
                raise StateConflictError("fork source contains duplicate accepted objects")
            by_sha256[receipt_sha256] = (path, raw)
        if any(value not in by_sha256 for value in selected_receipt_sha256s):
            raise StateConflictError("fork selected lineage references a missing receipt")

        # Preserve inactive sibling evidence outside the child's live accepted
        # object set.  The source branch is never changed or deleted.
        inactive_root = staging / "FORK_SOURCE_INACTIVE_ACCEPTED"
        rewritten: set[str] = {"FORK_REBINDING.json"}
        selected_set = set(selected_receipt_sha256s)
        for receipt_sha256, (path, _) in by_sha256.items():
            if receipt_sha256 in selected_set:
                continue
            source_dir = path.parent
            target_dir = inactive_root / source_dir.name
            if target_dir.exists():
                raise StateConflictError("fork inactive accepted archive is occupied")
            target_dir.parent.mkdir(parents=True, exist_ok=True)
            for item in source_dir.rglob("*"):
                if item.is_file():
                    relative_inside = item.relative_to(source_dir)
                    rewritten.add(item.relative_to(staging).as_posix())
                    rewritten.add((target_dir / relative_inside).relative_to(staging).as_posix())
            os.replace(source_dir, target_dir)

        paths = [by_sha256[value] for value in selected_receipt_sha256s]
        source_parent_turn: str | None = None
        source_parent_sha256: str | None = None
        child_parent_turn: str | None = None
        child_parent_sha256: str | None = None
        entries: list[dict[str, Any]] = []
        for generation, (path, raw) in enumerate(paths, start=1):
            if (
                raw.get("world_id") != source_world_id
                or raw.get("branch_id") != source_branch_id
                or raw.get("generation") != generation
                or raw.get("parent_accepted_turn_id") != source_parent_turn
                or raw.get("parent_accepted_head_sha256")
                != source_parent_sha256
                or not isinstance(raw.get("accepted_turn_id"), str)
            ):
                raise StateConflictError(
                    "source accepted receipt chain is invalid for fork"
                )
            source_sha256 = canonical_sha256(raw)
            child = {
                **raw,
                "branch_id": child_branch_id,
                "parent_accepted_turn_id": child_parent_turn,
                "parent_accepted_head_sha256": child_parent_sha256,
            }
            child_sha256 = canonical_sha256(child)
            write_json(path, child)
            self._rebind_selected_accepted_artifacts(
                path.parent,
                child_branch_id=child_branch_id,
                child_parent_sha256=child_parent_sha256,
                child_receipt_sha256=child_sha256,
            )
            for changed_path in (
                path.parent / "SEMANTIC_VALIDATION.json",
                path.parent / "PROVISIONAL_CANON.json",
                *path.parent.glob("RECORDING_BUNDLE_*/BUNDLE_MANIFEST.json"),
            ):
                if changed_path.is_file():
                    rewritten.add(changed_path.relative_to(staging).as_posix())
            source_dir = path.parent
            target_dir = source_dir
            if source_dir.name.endswith(f"-{source_sha256[:24]}"):
                target_dir = source_dir.parent / accepted_object_directory_name(
                    generation=generation,
                    accepted_turn_id=raw["accepted_turn_id"],
                    receipt_sha256=child_sha256,
                )
                if target_dir.exists():
                    raise StateConflictError("fork child accepted object path is occupied")
                for item in source_dir.rglob("*"):
                    if item.is_file():
                        relative_inside = item.relative_to(source_dir)
                        rewritten.add(item.relative_to(staging).as_posix())
                        rewritten.add(
                            (target_dir / relative_inside).relative_to(staging).as_posix()
                        )
                os.replace(source_dir, target_dir)
                path = target_dir / "ACCEPTED_RECEIPT.json"
            relative = path.relative_to(staging).as_posix()
            rewritten.add(relative)
            entries.append(
                {
                    "generation": generation,
                    "accepted_turn_id": raw["accepted_turn_id"],
                    "relative_path": relative,
                    "source_receipt_sha256": source_sha256,
                    "child_receipt_sha256": child_sha256,
                }
            )
            source_parent_turn = raw["accepted_turn_id"]
            source_parent_sha256 = source_sha256
            child_parent_turn = raw["accepted_turn_id"]
            child_parent_sha256 = child_sha256

        active_path = staging / "ACTIVE_LINEAGE.json"
        source_lineage = (
            active_lineage_from_payload(read_json_object(active_path, "active lineage"))
            if active_path.exists()
            else None
        )
        if source_lineage is not None and (
            source_lineage.world_id != source_world_id
            or source_lineage.branch_id != source_branch_id
            or source_lineage.selected_receipt_sha256 != source_parent_sha256
        ):
            raise StateConflictError("source active lineage is stale")
        if entries or source_lineage is not None:
            previous_entry = entries[-2] if len(entries) > 1 else None
            child_lineage = LeanActiveLineageV1(
                schema_version=LeanActiveLineageV1.SCHEMA_VERSION,
                world_id=source_world_id,
                branch_id=child_branch_id,
                revision=1 if source_lineage is None else source_lineage.revision + 1,
                switch_kind="fork",
                selected_generation=len(entries),
                selected_turn_id=child_parent_turn,
                selected_receipt_sha256=child_parent_sha256,
                previous_generation=max(0, len(entries) - 1),
                previous_turn_id=(
                    None if previous_entry is None else previous_entry["accepted_turn_id"]
                ),
                previous_receipt_sha256=(
                    None
                    if previous_entry is None
                    else previous_entry["child_receipt_sha256"]
                ),
            )
            write_json(active_path, active_lineage_payload(child_lineage))
            rewritten.add("ACTIVE_LINEAGE.json")
        cache_path = staging / "BRANCH_HEAD_CACHE.json"
        if cache_path.exists():
            cache = read_json_object(cache_path, "source branch-head cache")
            expected_cache = {
                "schema_version": "cera.pi_scene.branch_head_cache.v1",
                "generation": len(entries),
                "accepted_turn_id": source_parent_turn,
                "accepted_head_sha256": source_parent_sha256,
            }
            if cache != expected_cache:
                raise StateConflictError("source branch-head cache is stale")
            write_json(
                cache_path,
                {
                    **expected_cache,
                    "accepted_head_sha256": child_parent_sha256,
                },
            )
            rewritten.add("BRANCH_HEAD_CACHE.json")
        payload = {
            "schema_version": "cera.pi_scene_fork_rebinding.v1",
            "source_world_id": source_world_id,
            "source_branch_id": source_branch_id,
            "child_branch_id": child_branch_id,
            "accepted_receipts": entries,
        }
        write_json(
            staging / "FORK_REBINDING.json",
            {**payload, "rebinding_sha256": canonical_sha256(payload)},
        )
        return frozenset(rewritten)

    @staticmethod
    def _rebind_selected_accepted_artifacts(
        turn_dir: Path,
        *,
        child_branch_id: str,
        child_parent_sha256: str | None,
        child_receipt_sha256: str,
    ) -> None:
        """Rebind hashes mechanically derived from a forked receipt.

        Exact prose, model output, and Recorder-authored meaning are untouched.
        Only Python custody fields whose source receipt hash changed are
        rewritten in the child copy.
        """

        semantic_path = turn_dir / "SEMANTIC_VALIDATION.json"
        if semantic_path.exists():
            semantic = read_json_object(semantic_path, "semantic validation")
            validation = semantic.get("validation")
            if not isinstance(validation, dict):
                raise StateConflictError("fork semantic validation is invalid")
            custody = validation.get("custody")
            if not isinstance(custody, dict):
                raise StateConflictError("fork semantic custody is invalid")
            custody["branch_id"] = child_branch_id
            custody["accepted_head_sha256"] = child_parent_sha256
            semantic_body = {
                "schema_version": semantic.get("schema_version"),
                "candidate_sha256": semantic.get("candidate_sha256"),
                "validation": validation,
            }
            write_json(
                semantic_path,
                {
                    **semantic_body,
                    "artifact_sha256": canonical_sha256(semantic_body),
                },
            )
            provisional_path = turn_dir / "PROVISIONAL_CANON.json"
            if provisional_path.exists():
                provisional = read_json_object(
                    provisional_path,
                    "provisional canon",
                )
                provisional["validation_binding_sha256"] = canonical_sha256(validation)
                provisional_body = {
                    key: value
                    for key, value in provisional.items()
                    if key != "artifact_sha256"
                }
                write_json(
                    provisional_path,
                    {
                        **provisional_body,
                        "artifact_sha256": canonical_sha256(provisional_body),
                    },
                )

        for manifest_path in sorted(
            turn_dir.glob("RECORDING_BUNDLE_*/BUNDLE_MANIFEST.json")
        ):
            manifest = read_json_object(manifest_path, "recording bundle manifest")
            if "accepted_receipt_sha256" in manifest:
                manifest["accepted_receipt_sha256"] = child_receipt_sha256
                write_json(manifest_path, manifest)

    def _isolate_forked_session_cache(self, staging: Path) -> frozenset[str]:
        """Preserve parent cache evidence without sharing its live provider session."""

        active = staging / "sessions" / "ACCEPTED_SESSION.json"
        if not active.exists():
            return frozenset()
        archived = staging / "sessions" / "FORK_SOURCE_ACCEPTED_SESSION.json"
        if archived.exists():
            raise StateConflictError("fork source session archive path is occupied")
        os.replace(active, archived)
        return frozenset(
            {
                "sessions/ACCEPTED_SESSION.json",
                "sessions/FORK_SOURCE_ACCEPTED_SESSION.json",
            }
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

    def _validated_identity(
        self, chat_id: str, world_id: str, branch_id: str
    ) -> tuple[str, str, str]:
        return (
            required_identity(chat_id, "chat_id"),
            required_identity(world_id, "world_id"),
            required_identity(branch_id, "branch_id"),
        )

    def _branch_root(self, world_id: str, branch_id: str) -> Path:
        return lean_scene_branch_root(self.runtime_root, world_id, branch_id)

    def _staging_root(self, world_id: str, branch_id: str) -> Path:
        world_key = lean_scene_branch_keys(world_id, branch_id)[0]
        world_root = self.runtime_root / world_key
        world_root.mkdir(parents=True, exist_ok=True)
        if is_link_or_reparse(world_root):
            raise StateConflictError("world directory cannot be linked")
        return world_root / f".{uuid4().hex[:12]}.tmp"

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
