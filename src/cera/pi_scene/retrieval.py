"""Branch-local current dossiers and bounded exact retrieval for Pi Scene.

The filesystem remains Python authority.  Dossiers are regenerable read models
of one accepted branch checkpoint; they never become story truth.  Search is
public by default and requires an exact character ID before private character
bytes are tested, preventing yes/no term probing across unrelated characters.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from cera.errors import ContractValidationError, StateConflictError
from cera.serialization import canonical_bytes, canonical_sha256, text_sha256, to_primitive

from ._world_workspace_files import is_link_or_reparse, read_json_object

if TYPE_CHECKING:
    from .review_store import LeanSceneTurnInputV1
    from .world_workspace import BranchWorldWorkspaceV1

DOSSIER_SCHEMA = "cera.pi_scene.current_character_dossier.v1"
DOSSIER_INDEX_SCHEMA = "cera.pi_scene.current_character_dossier_index.v1"
CONTEXT_CATALOG_SCHEMA = "cera.pi_scene.context_catalog.v1"
MAX_RETRIEVAL_CALLS = 16
MAX_TURN_CHARACTERS = 8
MAX_SEARCH_TERMS = 8
MAX_SEARCH_RESULTS = 20
MAX_EXACT_RECORD_BYTES = 1_048_576


@dataclass(frozen=True, slots=True)
class DossierBuildResultV1:
    world_id: str
    branch_id: str
    accepted_head_sha256: str | None
    dossier_index_path: Path
    dossier_index_sha256: str
    character_ids: tuple[str, ...]


class BranchCharacterDossierMaterializer:
    """Build complete per-character read models from one reduced branch turn."""

    def materialize(
        self,
        workspace: BranchWorldWorkspaceV1,
        turn: LeanSceneTurnInputV1,
    ) -> DossierBuildResultV1:
        if (turn.world_id, turn.branch_id) != (
            workspace.world_id,
            workspace.branch_id,
        ):
            raise StateConflictError("dossier turn changed workspace scope")
        current_state = dict(turn.current_state)
        lineage = current_state.get("accepted_lineage")
        if not isinstance(lineage, Mapping):
            raise ContractValidationError("dossier turn lacks accepted lineage")
        head_sha = lineage.get("next_parent_accepted_head_sha256")
        if head_sha is not None and (not isinstance(head_sha, str) or len(head_sha) != 64):
            raise ContractValidationError("dossier accepted head is invalid")
        root = workspace.branch_root / "DERIVED" / "CurrentCharacterDossiers"
        root.mkdir(parents=True, exist_ok=True)
        rows: dict[str, dict[str, Any]] = {}
        for character_id in sorted(turn.characters):
            if not character_id.startswith("character:"):
                raise ContractValidationError("dossier character identity is invalid")
            dossier = self._dossier(turn, character_id)
            digest = text_sha256(character_id)[:24]
            relative = f"DERIVED/CurrentCharacterDossiers/character-{digest}.json"
            target = workspace.branch_root.joinpath(*Path(relative).parts)
            _atomic_json(target, dossier)
            rows[character_id] = {
                "path": relative,
                "content_sha256": text_sha256(target.read_text(encoding="utf-8")),
                "dossier_sha256": dossier["dossier_sha256"],
                "visibility": "character_private",
                "knowledge_owner_id": character_id,
            }
        catalogs = {
            "schema_version": CONTEXT_CATALOG_SCHEMA,
            "_cera_revision": int(current_state.get("accepted_state_order", 0)) + 1,
            "world_id": turn.world_id,
            "branch_id": turn.branch_id,
            "accepted_head_sha256": head_sha,
            "visibility": "system_private",
            "voice_examples": to_primitive(turn.voice_examples),
            "craft_index": to_primitive(turn.craft_index),
            "unresolved_threads": to_primitive(current_state.get("unresolved_threads", [])),
        }
        _atomic_json(
            workspace.branch_root / "DERIVED" / "ContextCatalogs" / "CURRENT.json",
            catalogs,
        )
        index_payload = {
            "schema_version": DOSSIER_INDEX_SCHEMA,
            "_cera_revision": int(current_state.get("accepted_state_order", 0)) + 1,
            "world_id": turn.world_id,
            "branch_id": turn.branch_id,
            "accepted_head_sha256": head_sha,
            "genesis_revision": current_state.get("genesis_revision"),
            "characters": rows,
        }
        index = {**index_payload, "index_sha256": canonical_sha256(index_payload)}
        index_path = root / "INDEX.json"
        _atomic_json(index_path, index)
        return DossierBuildResultV1(
            world_id=turn.world_id,
            branch_id=turn.branch_id,
            accepted_head_sha256=head_sha,
            dossier_index_path=index_path,
            dossier_index_sha256=str(index["index_sha256"]),
            character_ids=tuple(rows),
        )

    def _dossier(
        self,
        turn: LeanSceneTurnInputV1,
        character_id: str,
    ) -> dict[str, Any]:
        state = dict(turn.current_state)
        relationships = {
            key: value
            for key, value in turn.relationships.items()
            if key == character_id or _contains_identity(value, character_id)
        }
        memories = {
            key: value
            for key, value in turn.relevant_memories.items()
            if key == character_id or _contains_identity(value, character_id)
        }
        durable = [
            value
            for value in state.get("durable_changes", [])
            if isinstance(value, Mapping) and character_id in value.get("subject_ids", [])
        ]
        provisional_ids = [
            value.get("provisional_canon_id")
            for value in state.get("provisional_canon_lineage", [])
            if isinstance(value, Mapping) and isinstance(value.get("provisional_canon_id"), str)
        ]
        payload = {
            "schema_version": DOSSIER_SCHEMA,
            "_cera_revision": int(state.get("accepted_state_order", 0)) + 1,
            "world_id": turn.world_id,
            "branch_id": turn.branch_id,
            "scene_id": turn.scene_id,
            "character_id": character_id,
            "visibility": "character_private",
            "knowledge_owner_id": character_id,
            "accepted_head_sha256": state.get("accepted_lineage", {}).get(
                "next_parent_accepted_head_sha256"
            ),
            "accepted_state_checkpoint_sha256": state.get("accepted_state_checkpoint_sha256"),
            "genesis_revision": state.get("genesis_revision"),
            "present_in_current_scene": character_id
            in state.get("accepted_present_character_ids", []),
            "public_scene_state": state.get("public_scene_state"),
            "character_context": to_primitive(turn.characters[character_id]),
            "relationship_context": to_primitive(relationships),
            "memory_context": to_primitive(memories),
            "durable_changes": to_primitive(durable),
            "provisional_dependency_ids": provisional_ids,
        }
        return {**payload, "dossier_sha256": canonical_sha256(payload)}


class BranchRetrievalService:
    """Bounded read-only retrieval against one exact materialized branch."""

    def __init__(
        self,
        workspace: BranchWorldWorkspaceV1,
        *,
        maximum_calls: int = MAX_RETRIEVAL_CALLS,
    ) -> None:
        if not 1 <= maximum_calls <= MAX_RETRIEVAL_CALLS:
            raise ContractValidationError("branch retrieval call ceiling is invalid")
        self.workspace = workspace
        self.maximum_calls = maximum_calls
        self._calls = 0
        self._lock = RLock()

    @property
    def call_count(self) -> int:
        return self._calls

    def get_turn_context(
        self,
        character_ids: Sequence[str] = (),
    ) -> dict[str, Any]:
        """Return current scene custody and complete dossiers in one default call."""

        with self._operation("get_turn_context"):
            index = self._index()
            state = read_json_object(
                self.workspace.branch_root / "ACTIVE" / "WORLD_STATE.json",
                "branch WORLD_STATE",
            )
            requested = tuple(dict.fromkeys(character_ids))
            if not requested:
                requested = tuple(
                    value
                    for value in self._accepted_present_character_ids()
                    if value in index["characters"]
                )
            if len(requested) > MAX_TURN_CHARACTERS:
                raise ContractValidationError("turn context character budget exceeded")
            dossiers = [self._dossier(value, index=index) for value in requested]
            return {
                "schema_version": "cera.pi_scene.turn_context.v1",
                "world_id": self.workspace.world_id,
                "branch_id": self.workspace.branch_id,
                "scene_id": state["current_scene_id"],
                "accepted_head_sha256": index["accepted_head_sha256"],
                "character_dossiers": dossiers,
                "omitted_character_ids": sorted(set(index["characters"]) - set(requested)),
            }

    def get_character_context(self, character_id: str) -> dict[str, Any]:
        with self._operation("get_character_context"):
            return self._dossier(character_id, index=self._index())

    def search_evidence(
        self,
        terms: Sequence[str],
        *,
        character_id: str | None = None,
        limit: int = MAX_SEARCH_RESULTS,
    ) -> dict[str, Any]:
        """Search public projections or one explicitly scoped private dossier."""

        if (
            not terms
            or len(terms) > MAX_SEARCH_TERMS
            or any(not isinstance(value, str) or not value.strip() for value in terms)
            or not 1 <= limit <= MAX_SEARCH_RESULTS
        ):
            raise ContractValidationError("branch evidence search arguments are invalid")
        with self._operation("search_evidence"):
            needles = tuple(value.casefold() for value in terms)
            rows: list[dict[str, Any]] = []
            if character_id is not None:
                dossier = self._dossier(character_id, index=self._index())
                text = json.dumps(dossier, ensure_ascii=False, sort_keys=True).casefold()
                if all(value in text for value in needles):
                    rows.append(
                        {
                            "kind": "character_dossier",
                            "character_id": character_id,
                            "dossier_sha256": dossier["dossier_sha256"],
                            "visibility": "character_private",
                            "knowledge_owner_id": character_id,
                        }
                    )
            else:
                # Unscoped lookup is intentionally public-only.  It never
                # evaluates terms against system/character/creator-private bytes.
                projections = self.workspace.branch_root / "ACTIVE" / "GenesisRecords"
                for path in sorted(projections.rglob("*.json")):
                    _assert_confined_record(path, self.workspace.branch_root)
                    projection = read_json_object(path, "Genesis projection")
                    if str(projection.get("visibility", "public")).casefold() != "public":
                        continue
                    record = projection.get("record")
                    text = json.dumps(record, ensure_ascii=False, sort_keys=True).casefold()
                    if not all(value in text for value in needles):
                        continue
                    rows.append(_record_descriptor(projection, path, self.workspace))
                    if len(rows) == limit:
                        break
            return {
                "terms": list(terms),
                "character_scope": character_id,
                "records": rows,
                "truncated": len(rows) == limit,
            }

    def get_exact_record(self, record_id: str) -> dict[str, Any]:
        if not isinstance(record_id, str) or not record_id.startswith("record:"):
            raise ContractValidationError("exact record identity is invalid")
        with self._operation("get_exact_record"):
            for path in sorted(
                (self.workspace.branch_root / "ACTIVE" / "GenesisRecords").rglob("*.json")
            ):
                _assert_confined_record(path, self.workspace.branch_root)
                if path.stat().st_size > MAX_EXACT_RECORD_BYTES:
                    raise StateConflictError("exact branch record exceeds its byte ceiling")
                projection = read_json_object(path, "Genesis projection")
                record = projection.get("record")
                if not isinstance(record, dict) or record.get("record_id") != record_id:
                    continue
                if str(projection.get("visibility", "public")).casefold() in {
                    "creator_private",
                    "creator-only",
                }:
                    raise PermissionError("creator-private record is not role-authorized")
                return {
                    **_record_descriptor(projection, path, self.workspace),
                    "record": record,
                }
            raise FileNotFoundError("exact branch record is unavailable")

    def get_relationship_context(self, character_id: str) -> Mapping[str, Any]:
        value = self.get_character_context(character_id)["relationship_context"]
        if not isinstance(value, Mapping):
            raise StateConflictError("character relationship context changed")
        return value

    def get_memory_context(self, character_id: str) -> Mapping[str, Any]:
        value = self.get_character_context(character_id)["memory_context"]
        if not isinstance(value, Mapping):
            raise StateConflictError("character memory context changed")
        return value

    def get_thread_context(self) -> tuple[str, ...]:
        with self._operation("get_thread_context"):
            catalog = self._catalog()
            return tuple(catalog.get("unresolved_threads", ()))

    def get_voice_examples(self, character_id: str) -> object:
        with self._operation("get_voice_examples"):
            catalog = self._catalog()
            values = catalog.get("voice_examples", {})
            if not isinstance(values, Mapping):
                raise StateConflictError("voice catalog changed")
            return values.get(character_id, values.get(character_id.split(":")[-1]))

    def get_craft_context(self) -> Mapping[str, Any]:
        with self._operation("get_craft_context"):
            value = self._catalog().get("craft_index", {})
            if not isinstance(value, Mapping):
                raise StateConflictError("craft catalog changed")
            return dict(value)

    def _index(self) -> dict[str, Any]:
        value = read_json_object(
            self.workspace.branch_root / "DERIVED" / "CurrentCharacterDossiers" / "INDEX.json",
            "character dossier index",
        )
        if (
            value.get("schema_version") != DOSSIER_INDEX_SCHEMA
            or value.get("world_id") != self.workspace.world_id
            or value.get("branch_id") != self.workspace.branch_id
            or not isinstance(value.get("characters"), dict)
        ):
            raise StateConflictError("character dossier index changed branch scope")
        stored_hash = value.get("index_sha256")
        payload = {key: item for key, item in value.items() if key != "index_sha256"}
        if not isinstance(stored_hash, str) or canonical_sha256(payload) != stored_hash:
            raise StateConflictError("character dossier index hash changed")
        return value

    def _dossier(self, character_id: str, *, index: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(character_id, str) or not character_id.startswith("character:"):
            raise ContractValidationError("character context identity is invalid")
        row = index["characters"].get(character_id)
        if not isinstance(row, Mapping) or not isinstance(row.get("path"), str):
            raise FileNotFoundError("character dossier is unavailable")
        target = self.workspace.branch_root.joinpath(*Path(row["path"]).parts).resolve()
        if not target.is_relative_to(self.workspace.branch_root) or target.is_symlink():
            raise PermissionError("character dossier escaped its branch")
        value = read_json_object(target, "character dossier")
        stored_hash = value.get("dossier_sha256")
        payload = {key: item for key, item in value.items() if key != "dossier_sha256"}
        if (
            value.get("schema_version") != DOSSIER_SCHEMA
            or value.get("world_id") != self.workspace.world_id
            or value.get("branch_id") != self.workspace.branch_id
            or value.get("character_id") != character_id
            or value.get("knowledge_owner_id") != character_id
            or not isinstance(stored_hash, str)
            or canonical_sha256(payload) != stored_hash
        ):
            raise StateConflictError("character dossier custody changed")
        return value

    def _accepted_present_character_ids(self) -> tuple[str, ...]:
        index = self._index()
        present = []
        for character_id in index["characters"]:
            dossier = self._dossier(character_id, index=index)
            if dossier["present_in_current_scene"]:
                present.append(character_id)
        return tuple(present)

    def _catalog(self) -> dict[str, Any]:
        value = read_json_object(
            self.workspace.branch_root / "DERIVED" / "ContextCatalogs" / "CURRENT.json",
            "branch context catalog",
        )
        if (
            value.get("schema_version") != CONTEXT_CATALOG_SCHEMA
            or value.get("world_id") != self.workspace.world_id
            or value.get("branch_id") != self.workspace.branch_id
        ):
            raise StateConflictError("branch context catalog changed scope")
        return value

    class _Operation:
        def __init__(self, owner: BranchRetrievalService, name: str) -> None:
            self.owner = owner
            self.name = name

        def __enter__(self) -> None:
            self.owner._lock.acquire()
            if self.owner._calls >= self.owner.maximum_calls:
                self.owner._lock.release()
                raise StateConflictError("branch retrieval reached its hard call ceiling")
            self.owner._calls += 1

        def __exit__(
            self,
            exc_type: object | None,
            exc: object | None,
            traceback: object | None,
        ) -> None:
            del exc_type, exc, traceback
            self.owner._lock.release()

    def _operation(self, name: str) -> BranchRetrievalService._Operation:
        return self._Operation(self, name)


def _contains_identity(value: object, identity: str) -> bool:
    if value == identity:
        return True
    if isinstance(value, Mapping):
        return any(_contains_identity(item, identity) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains_identity(item, identity) for item in value)
    return False


def _record_descriptor(
    projection: Mapping[str, Any],
    path: Path,
    workspace: BranchWorldWorkspaceV1,
) -> dict[str, Any]:
    record = projection.get("record")
    if not isinstance(record, Mapping):
        raise StateConflictError("Genesis projection record changed")
    return {
        "record_id": record.get("record_id"),
        "record_type": record.get("record_type"),
        "visibility": projection.get("visibility", "public"),
        "knowledge_owner_id": projection.get("knowledge_owner_id"),
        "path": path.relative_to(workspace.branch_root).as_posix(),
        "content_sha256": text_sha256(path.read_text(encoding="utf-8")),
    }


def _assert_confined_record(path: Path, branch_root: Path) -> None:
    resolved = path.resolve()
    if (
        not path.is_file()
        or is_link_or_reparse(path)
        or not resolved.is_relative_to(branch_root.resolve())
    ):
        raise PermissionError("branch retrieval record escaped its workspace")


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = path.parent / f".{path.name}.{uuid4().hex[:12]}.tmp"
    try:
        staging.write_bytes(canonical_bytes(dict(payload)))
        os.replace(staging, path)
    finally:
        if staging.exists():
            staging.unlink()
