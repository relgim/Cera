"""Live chat-to-world resolution for the Pi Scene route.

One SillyTavern chat is durably bound to one semantic world/branch pair.  A
new chat receives the newest accepted Genesis revision once; later requests
open that exact workspace.  Forks retain the parent world and receive a new
branch, with an exact selected-head check before any copy occurs.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from threading import RLock

from cera.errors import ContractValidationError, StateConflictError
from cera.serialization import canonical_bytes, canonical_sha256, text_sha256, to_primitive

from .context import AcceptedBranchContextProvider, PiSceneContextSeedV1
from .contracts import SceneRoute
from .http_contracts import LeanSceneRequestControlsV1
from .retrieval import (
    BranchCharacterDossierMaterializer,
    BranchRetrievalService,
    DossierBuildResultV1,
)
from .review_store import LeanSceneTurnInputV1
from .store import LeanSceneStore
from .world_workspace import (
    BranchBoundWorldMcpFactory,
    BranchWorldWorkspaceV1,
    ForkChatWorkspaceRequestV1,
    NewChatWorkspaceRequestV1,
    PiSceneWorldWorkspaceManager,
)

CHAT_SCOPE_SCHEMA = "cera.pi_scene.chat_world_scope.v1"
DEFAULT_INITIAL_PRESENT_CHARACTER_IDS = ("character:sakura_hanezawa",)
DEFAULT_ADULT_PARTICIPANT_IDS = (
    "character:ted",
    "character:hana_hanezawa",
)


@dataclass(frozen=True, slots=True)
class ChatWorldScopeV1:
    session_id: str
    world_id: str
    branch_id: str
    scene_id: str
    workspace: BranchWorldWorkspaceV1
    kind: str
    parent_session_id: str | None
    selected_parent_accepted_turn_id: str | None
    selected_parent_accepted_head_sha256: str | None


@dataclass(frozen=True, slots=True)
class ResolvedWorldTurnV1:
    scope: ChatWorldScopeV1
    turn: LeanSceneTurnInputV1
    dossier_build: DossierBuildResultV1
    retrieval: BranchRetrievalService
    planner_mcp_factory: BranchBoundWorldMcpFactory


class PiSceneChatWorldResolver:
    """Create/open/fork exact chat workspaces and build bounded turn context."""

    def __init__(
        self,
        *,
        manager: PiSceneWorldWorkspaceManager,
        store: LeanSceneStore,
        scope_root: Path,
        base_seed: PiSceneContextSeedV1,
        initial_present_character_ids: Sequence[str] = (*DEFAULT_INITIAL_PRESENT_CHARACTER_IDS,),
        adult_participant_ids: Sequence[str] = (*DEFAULT_ADULT_PARTICIPANT_IDS,),
        dossier_materializer: BranchCharacterDossierMaterializer | None = None,
    ) -> None:
        self.manager = manager
        self.store = store
        self.scope_root = scope_root.resolve()
        self.scope_root.mkdir(parents=True, exist_ok=True)
        self.base_seed = base_seed
        self.initial_present_character_ids = _character_ids(
            initial_present_character_ids,
            "initial present characters",
        )
        self.adult_participant_ids = tuple(adult_participant_ids)
        if not self.adult_participant_ids or any(
            not isinstance(value, str) or not value.startswith("character:")
            for value in self.adult_participant_ids
        ):
            raise ContractValidationError("adult participant identities are invalid")
        self.dossier_materializer = dossier_materializer or BranchCharacterDossierMaterializer()
        self._lock = RLock()

    def resolve(self, controls: LeanSceneRequestControlsV1) -> ChatWorldScopeV1:
        """Open an existing exact scope, or atomically establish a new chat."""

        session_id = _session_id(controls.session_id)
        with self._lock:
            existing = self._load_scope(session_id)
            if existing is not None:
                return existing
            world_id, branch_id, scene_id = new_chat_semantic_scope(session_id)
            workspace = self.manager.create_new_chat(
                NewChatWorkspaceRequestV1(
                    chat_id=session_id,
                    world_id=world_id,
                    branch_id=branch_id,
                    settings=to_primitive(controls),
                    initial_scene_id=scene_id,
                )
            )
            self._publish_scope(
                session_id=session_id,
                world_id=world_id,
                branch_id=branch_id,
                scene_id=scene_id,
                workspace=workspace,
                kind="new_chat",
                parent_session_id=None,
                selected_parent_accepted_turn_id=None,
                selected_parent_accepted_head_sha256=None,
            )
            loaded = self._load_scope(session_id)
            if loaded is None:
                raise StateConflictError("new chat scope publication disappeared")
            return loaded

    def fork(
        self,
        *,
        parent_session_id: str,
        child_session_id: str,
        selected_parent_accepted_turn_id: str | None,
        selected_parent_accepted_head_sha256: str | None,
    ) -> ChatWorldScopeV1:
        """Fork the selected current head; fail closed for historical snapshots.

        The workspace manager currently copies one complete current branch.  A
        request for any older accepted head is therefore rejected rather than
        silently forking the latest state.
        """

        parent_session_id = _session_id(parent_session_id)
        child_session_id = _session_id(child_session_id)
        if parent_session_id == child_session_id:
            raise ContractValidationError("fork child chat must differ from parent")
        if (selected_parent_accepted_turn_id is None) != (
            selected_parent_accepted_head_sha256 is None
        ):
            raise ContractValidationError("fork selected head custody is incomplete")
        with self._lock:
            parent = self._load_scope(parent_session_id)
            if parent is None:
                raise FileNotFoundError("fork parent chat scope is unavailable")
            existing = self._load_scope(child_session_id)
            if existing is not None:
                if existing.parent_session_id != parent_session_id:
                    raise StateConflictError("fork child already belongs to another parent")
                return existing
            head = self.store.load_head(
                world_id=parent.world_id,
                branch_id=parent.branch_id,
            )
            if selected_parent_accepted_turn_id is None:
                selected_parent_accepted_turn_id = head.accepted_turn_id
                selected_parent_accepted_head_sha256 = head.accepted_head_sha256
            if (
                selected_parent_accepted_turn_id != head.accepted_turn_id
                or selected_parent_accepted_head_sha256 != head.accepted_head_sha256
            ):
                raise StateConflictError(
                    "historical selected fork point is not materializable; current head required"
                )
            child_branch_id = fork_branch_semantic_id(child_session_id)
            child_scene_id = parent.scene_id
            workspace = self.manager.fork_chat(
                ForkChatWorkspaceRequestV1(
                    parent_chat_id=parent_session_id,
                    world_id=parent.world_id,
                    parent_branch_id=parent.branch_id,
                    child_chat_id=child_session_id,
                    child_branch_id=child_branch_id,
                )
            )
            self._publish_scope(
                session_id=child_session_id,
                world_id=parent.world_id,
                branch_id=child_branch_id,
                scene_id=child_scene_id,
                workspace=workspace,
                kind="fork",
                parent_session_id=parent_session_id,
                selected_parent_accepted_turn_id=selected_parent_accepted_turn_id,
                selected_parent_accepted_head_sha256=(selected_parent_accepted_head_sha256),
            )
            loaded = self._load_scope(child_session_id)
            if loaded is None:
                raise StateConflictError("fork chat scope publication disappeared")
            return loaded

    def resolve_turn(
        self,
        *,
        route: SceneRoute,
        source: str,
        messages: Sequence[Mapping[str, str]],
        controls: LeanSceneRequestControlsV1,
    ) -> ResolvedWorldTurnV1:
        scope = self.resolve(controls)
        mappings = self.manager.load_genesis_context_mappings(scope.workspace)
        missing = sorted(set(self.initial_present_character_ids) - set(mappings.characters))
        if missing:
            raise StateConflictError(
                "pinned Genesis lacks required initial characters: " + ", ".join(missing)
            )
        full_seed = replace(
            self.base_seed,
            world_id=scope.world_id,
            branch_id=scope.branch_id,
            scene_id=scope.scene_id,
            accepted_present_character_ids=self.initial_present_character_ids,
            characters=mappings.characters,
            relationships=mappings.relationships,
            relevant_memories=mappings.relevant_memories,
            adult_handoff={
                **dict(self.base_seed.adult_handoff),
                "participant_ids": list(self.adult_participant_ids),
            },
            genesis_revision=mappings.genesis_revision,
        )
        full_turn = AcceptedBranchContextProvider(self.store, full_seed)(
            route,
            source,
            messages,
        )
        dossier_build = self.dossier_materializer.materialize(
            scope.workspace,
            full_turn,
        )
        present = tuple(
            value
            for value in full_turn.current_state.get("accepted_present_character_ids", ())
            if isinstance(value, str) and value in full_turn.characters
        )
        relevant = list(present)
        if route is SceneRoute.ADULT:
            for value in self.adult_participant_ids:
                if value != "character:ted" and value in full_turn.characters:
                    relevant.append(value)
        relevant_ids = tuple(dict.fromkeys(relevant))
        filtered = replace(
            full_turn,
            characters={
                key: full_turn.characters[key]
                for key in relevant_ids
                if key in full_turn.characters
            },
            relationships={
                key: value
                for key, value in full_turn.relationships.items()
                if key in relevant_ids
                or any(_contains_identity(value, target) for target in relevant_ids)
            },
            relevant_memories={
                key: value
                for key, value in full_turn.relevant_memories.items()
                if key in relevant_ids
                or any(_contains_identity(value, target) for target in relevant_ids)
            },
            request_controls=controls,
        )
        return ResolvedWorldTurnV1(
            scope=scope,
            turn=filtered,
            dossier_build=dossier_build,
            retrieval=BranchRetrievalService(scope.workspace),
            planner_mcp_factory=self.manager.mcp_factory(scope.workspace),
        )

    def _scope_path(self, session_id: str) -> Path:
        return self.scope_root / f"chat-{text_sha256(session_id)[:32]}.json"

    def _load_scope(self, session_id: str) -> ChatWorldScopeV1 | None:
        path = self._scope_path(session_id)
        if not path.exists():
            return None
        if path.is_symlink():
            raise StateConflictError("chat scope cannot be linked")
        raw = json.loads(path.read_text(encoding="utf-8"))
        expected = {
            "schema_version",
            "session_id",
            "world_id",
            "branch_id",
            "scene_id",
            "workspace_sha256",
            "genesis_revision_id",
            "kind",
            "parent_session_id",
            "selected_parent_accepted_turn_id",
            "selected_parent_accepted_head_sha256",
            "scope_sha256",
        }
        if not isinstance(raw, dict) or set(raw) != expected:
            raise StateConflictError("chat world scope fields changed")
        stored = raw.pop("scope_sha256")
        if canonical_sha256(raw) != stored or raw["session_id"] != session_id:
            raise StateConflictError("chat world scope hash or identity changed")
        workspace = self.manager.open(
            chat_id=session_id,
            world_id=raw["world_id"],
            branch_id=raw["branch_id"],
        )
        if (
            workspace.workspace_sha256 != raw["workspace_sha256"]
            or workspace.genesis_pin.revision_id != raw["genesis_revision_id"]
        ):
            raise StateConflictError("chat scope and workspace custody disagree")
        return ChatWorldScopeV1(
            session_id=session_id,
            world_id=raw["world_id"],
            branch_id=raw["branch_id"],
            scene_id=raw["scene_id"],
            workspace=workspace,
            kind=raw["kind"],
            parent_session_id=raw["parent_session_id"],
            selected_parent_accepted_turn_id=raw["selected_parent_accepted_turn_id"],
            selected_parent_accepted_head_sha256=raw["selected_parent_accepted_head_sha256"],
        )

    def _publish_scope(
        self,
        *,
        session_id: str,
        world_id: str,
        branch_id: str,
        scene_id: str,
        workspace: BranchWorldWorkspaceV1,
        kind: str,
        parent_session_id: str | None,
        selected_parent_accepted_turn_id: str | None,
        selected_parent_accepted_head_sha256: str | None,
    ) -> None:
        payload = {
            "schema_version": CHAT_SCOPE_SCHEMA,
            "session_id": session_id,
            "world_id": world_id,
            "branch_id": branch_id,
            "scene_id": scene_id,
            "workspace_sha256": workspace.workspace_sha256,
            "genesis_revision_id": workspace.genesis_pin.revision_id,
            "kind": kind,
            "parent_session_id": parent_session_id,
            "selected_parent_accepted_turn_id": selected_parent_accepted_turn_id,
            "selected_parent_accepted_head_sha256": (selected_parent_accepted_head_sha256),
        }
        value = {**payload, "scope_sha256": canonical_sha256(payload)}
        path = self._scope_path(session_id)
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        except FileExistsError:
            return
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(canonical_bytes(value))
                stream.flush()
                os.fsync(stream.fileno())
        except BaseException:
            path.unlink(missing_ok=True)
            raise


def new_chat_semantic_scope(session_id: str) -> tuple[str, str, str]:
    session_id = _session_id(session_id)
    digest = text_sha256(session_id)
    return (
        f"world-hanezawa-chat-{digest[:24]}",
        f"branch-chat-{digest}",
        f"scene-hanezawa-entryway-{digest[:24]}",
    )


def fork_branch_semantic_id(child_session_id: str) -> str:
    child_session_id = _session_id(child_session_id)
    return f"branch-fork-{text_sha256(child_session_id)}"


def _session_id(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,95}", value):
        raise ContractValidationError("Pi Scene session scope identity is invalid")
    return value


def _character_ids(values: Sequence[str], label: str) -> tuple[str, ...]:
    output = tuple(values)
    if (
        not output
        or any(not isinstance(value, str) or not value.startswith("character:") for value in output)
        or len(output) != len(set(output))
    ):
        raise ContractValidationError(f"{label} are invalid")
    return output


def _contains_identity(value: object, identity: str) -> bool:
    if value == identity:
        return True
    if isinstance(value, Mapping):
        return any(_contains_identity(item, identity) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains_identity(item, identity) for item in value)
    return False
