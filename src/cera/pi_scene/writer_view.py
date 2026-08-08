"""Materialize the minimum branch-scoped, read-only Pi Writer view."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import shutil
from typing import Any, Mapping, Sequence
from uuid import uuid4

from cera.errors import ContractValidationError, StateConflictError
from cera.serialization import canonical_json, canonical_sha256, text_sha256

from .contracts import SceneRoute


_FORBIDDEN_KEY_PARTS = (
    "api_key",
    "apikey",
    "authorization",
    "access_token",
    "auth_token",
    "bearer",
    "credential",
    "password",
    "refresh_token",
    "secret",
    "session_token",
)


@dataclass(frozen=True, slots=True)
class WriterViewInputV1:
    world_id: str
    branch_id: str
    scene_id: str
    turn_id: str
    candidate_id: str
    route: SceneRoute
    user_prompt: str
    primary_authority: Mapping[str, Any]
    current_state: Mapping[str, Any]
    characters: Mapping[str, Mapping[str, Any]]
    relationships: Mapping[str, Mapping[str, Any]]
    recent_prose: Sequence[Mapping[str, Any] | str]
    relevant_memories: Mapping[str, Mapping[str, Any]]
    voice_examples: Mapping[str, Mapping[str, Any] | str]
    craft_index: Mapping[str, Any]
    accepted_records: Sequence[Mapping[str, Any]]

    def __post_init__(self) -> None:
        for field_name in (
            "world_id",
            "branch_id",
            "scene_id",
            "turn_id",
            "candidate_id",
            "user_prompt",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ContractValidationError(f"Writer-view {field_name} is empty")
        if not self.primary_authority:
            raise ContractValidationError("Writer view requires primary authority")
        if not self.current_state:
            raise ContractValidationError("Writer view requires current state")
        _reject_secret_keys(self.primary_authority)
        _reject_secret_keys(self.current_state)
        _reject_secret_keys(self.characters)
        _reject_secret_keys(self.relationships)
        _reject_secret_keys(self.relevant_memories)
        _reject_secret_keys(self.craft_index)


@dataclass(frozen=True, slots=True)
class MaterializedWriterViewV1:
    root: Path
    manifest_path: Path
    manifest_sha256: str
    file_count: int


class WriterViewMaterializer:
    """Create an immutable candidate view without linking source repositories."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def materialize(self, source: WriterViewInputV1) -> MaterializedWriterViewV1:
        final = (
            self.root
            / f"world-{text_sha256(source.world_id)[:16]}"
            / f"branch-{text_sha256(source.branch_id)[:16]}"
            / f"candidate-{text_sha256(source.candidate_id)[:20]}"
        ).resolve()
        if not final.is_relative_to(self.root):
            raise ContractValidationError("Writer view escaped its configured root")
        if final.exists():
            return verify_writer_view(final)

        final.parent.mkdir(parents=True, exist_ok=True)
        stage = final.parent / f".{final.name}.{uuid4().hex}.tmp"
        stage.mkdir(parents=False, exist_ok=False)
        try:
            self._write_view(stage, source)
            os.replace(stage, final)
        except Exception:
            if stage.exists():
                shutil.rmtree(stage)
            raise
        return verify_writer_view(final)

    @staticmethod
    def _write_view(root: Path, source: WriterViewInputV1) -> None:
        _write_text(root / "USER_PROMPT.txt", source.user_prompt)
        _write_json(
            root / "TURN.json",
            {
                "schema_version": "cera.pi_scene.writer_view_turn.v1",
                "world_id": source.world_id,
                "branch_id": source.branch_id,
                "scene_id": source.scene_id,
                "turn_id": source.turn_id,
                "candidate_id": source.candidate_id,
            },
        )
        _write_json(
            root / "ROUTE.json",
            {
                "schema_version": "cera.pi_scene.writer_view_route.v1",
                "route": source.route.value,
                "authority": (
                    "codex_sequence" if source.route is SceneRoute.ORDINARY else "adult_handoff"
                ),
                "creator_review_required": True,
                "ted_restrictions": "warn_only",
            },
        )
        _write_json(
            root
            / (
                "PRIMARY_SEQUENCE.json"
                if source.route is SceneRoute.ORDINARY
                else "ADULT_HANDOFF.json"
            ),
            source.primary_authority,
        )
        _write_json(root / "CURRENT_STATE.json", source.current_state)
        _write_named_mapping(root / "characters", source.characters)
        _write_named_mapping(root / "relationships", source.relationships)
        _write_numbered(root / "recent_prose", source.recent_prose)
        _write_named_mapping(root / "relevant_memories", source.relevant_memories)
        _write_named_mapping(root / "voice_examples", source.voice_examples)
        _write_json(root / "craft" / "index.json", source.craft_index)
        _write_numbered(root / "accepted_records", source.accepted_records)

        files: list[dict[str, Any]] = []
        for path in sorted(value for value in root.rglob("*") if value.is_file()):
            if path.is_symlink():
                raise ContractValidationError("Writer view cannot contain symlinks")
            relative = path.relative_to(root).as_posix()
            data = path.read_bytes()
            files.append(
                {
                    "path": relative,
                    "sha256": text_sha256(data.decode("utf-8")),
                    "bytes": len(data),
                }
            )
        manifest = {
            "schema_version": "cera.pi_scene.writer_view_manifest.v1",
            "scope": {
                "world_id_sha256": text_sha256(source.world_id),
                "branch_id_sha256": text_sha256(source.branch_id),
                "turn_id_sha256": text_sha256(source.turn_id),
                "candidate_id_sha256": text_sha256(source.candidate_id),
                "route": source.route.value,
            },
            "allowed_tools": ["read", "list", "find", "search"],
            "files": files,
        }
        _write_json(root / "MANIFEST.json", manifest)


def verify_writer_view(root: Path) -> MaterializedWriterViewV1:
    root = root.resolve()
    manifest_path = root / "MANIFEST.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StateConflictError("Writer-view manifest is unreadable") from exc
    if not isinstance(manifest, dict) or manifest.get("schema_version") != (
        "cera.pi_scene.writer_view_manifest.v1"
    ):
        raise StateConflictError("Writer-view manifest identity changed")
    if manifest.get("allowed_tools") != ["read", "list", "find", "search"]:
        raise StateConflictError("Writer-view tool boundary changed")
    entries = manifest.get("files")
    if not isinstance(entries, list):
        raise StateConflictError("Writer-view manifest file list is invalid")
    listed: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            raise StateConflictError("Writer-view file entry is invalid")
        relative = entry["path"]
        path = resolve_confined_path(root, relative, require_file=True)
        if path.is_symlink():
            raise StateConflictError("Writer view contains a symlink")
        data = path.read_text(encoding="utf-8")
        if text_sha256(data) != entry.get("sha256"):
            raise StateConflictError("Writer-view file hash changed")
        if len(data.encode("utf-8")) != entry.get("bytes"):
            raise StateConflictError("Writer-view file size changed")
        listed.add(relative)
    actual = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path != manifest_path
    }
    if actual != listed:
        raise StateConflictError("Writer-view manifest occupancy changed")
    manifest_text = canonical_json(manifest)
    if manifest_path.read_text(encoding="utf-8") != manifest_text:
        raise StateConflictError("Writer-view manifest is not canonical")
    return MaterializedWriterViewV1(
        root=root,
        manifest_path=manifest_path,
        manifest_sha256=text_sha256(manifest_text),
        file_count=len(entries) + 1,
    )


def resolve_confined_path(
    root: Path,
    relative: str,
    *,
    require_file: bool = False,
) -> Path:
    if not isinstance(relative, str) or not relative.strip():
        raise ContractValidationError("Writer-view relative path is empty")
    normalized = relative.removeprefix("@").replace("\\", "/")
    if (
        normalized.startswith("/")
        or re.match(r"^[A-Za-z]:", normalized)
        or normalized.startswith("//")
        or "\x00" in normalized
        or any(part in {"", ".", ".."} for part in normalized.split("/"))
    ):
        raise ContractValidationError("Writer-view path is not a safe relative path")
    root = root.resolve(strict=True)
    candidate = root
    for part in normalized.split("/"):
        candidate = candidate / part
        if candidate.is_symlink():
            raise ContractValidationError("Writer-view path contains a symbolic link")
    candidate = candidate.resolve(strict=True)
    if not candidate.is_relative_to(root):
        raise ContractValidationError("Writer-view path escaped its root")
    if require_file and not candidate.is_file():
        raise ContractValidationError("Writer-view path is not a file")
    return candidate


def _write_named_mapping(root: Path, values: Mapping[str, Any]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    for key, value in sorted(values.items()):
        filename = f"{_slug(key)}-{text_sha256(key)[:10]}.json"
        payload = value if isinstance(value, Mapping) else {"text": str(value)}
        _write_json(root / filename, payload)


def _write_numbered(root: Path, values: Sequence[Mapping[str, Any] | str]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    for index, value in enumerate(values, start=1):
        if isinstance(value, Mapping):
            _write_json(root / f"{index:04d}.json", value)
        else:
            _write_text(root / f"{index:04d}.txt", str(value))


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    _reject_secret_keys(value)
    _write_text(path, canonical_json(dict(value)))


def _write_text(path: Path, value: str) -> None:
    if not isinstance(value, str):
        raise ContractValidationError("Writer-view text value is invalid")
    data = value.encode("utf-8")
    if len(data) > 2_000_000:
        raise ContractValidationError("one Writer-view file exceeds two megabytes")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="") as handle:
        handle.write(value)


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")[:40]
    return slug or "item"


def _reject_secret_keys(value: Any, path: str = "root") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = str(key).lower().replace("-", "_")
            if any(part in normalized for part in _FORBIDDEN_KEY_PARTS):
                raise ContractValidationError(f"Writer view contains forbidden key at {path}")
            _reject_secret_keys(item, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _reject_secret_keys(item, f"{path}[{index}]")
