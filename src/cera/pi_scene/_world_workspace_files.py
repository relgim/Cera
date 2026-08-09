"""Private no-link filesystem primitives for Pi Scene world workspaces."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping

from cera.errors import ContractValidationError, StateConflictError
from cera.serialization import canonical_bytes, canonical_sha256, text_sha256


SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
_REPARSE_POINT = 0x400


def required_identity(value: str, field: str) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > 512
        or "\x00" in value
    ):
        raise ContractValidationError(f"{field} must be a bounded non-empty identity")
    return value


def lean_scene_branch_keys(world_id: str, branch_id: str) -> tuple[str, str]:
    """Mirror LeanSceneStore's collision-checked physical path convention."""

    world_id = required_identity(world_id, "world_id")
    branch_id = required_identity(branch_id, "branch_id")
    return (
        f"world-{text_sha256(world_id)[:24]}",
        f"branch-{text_sha256(branch_id)[:24]}",
    )


def lean_scene_branch_root(root: Path, world_id: str, branch_id: str) -> Path:
    world_key, branch_key = lean_scene_branch_keys(world_id, branch_id)
    candidate = (root / world_key / branch_key).resolve()
    if not candidate.is_relative_to(root.resolve()):
        raise ContractValidationError("branch store escaped its configured root")
    return candidate


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def read_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractValidationError(f"{label} is not valid UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise ContractValidationError(f"{label} must be a JSON object")
    return payload


def is_link_or_reparse(path: Path) -> bool:
    try:
        stat = path.lstat()
    except OSError as exc:
        raise StateConflictError(
            f"workspace path cannot be inspected: {path.name}"
        ) from exc
    return path.is_symlink() or bool(
        getattr(stat, "st_file_attributes", 0) & _REPARSE_POINT
    )


def assert_plain_tree(root: Path, label: str) -> None:
    if not root.is_dir() or is_link_or_reparse(root):
        raise StateConflictError(f"{label} root is unavailable or linked")
    for path in root.rglob("*"):
        if is_link_or_reparse(path):
            raise StateConflictError(f"{label} contains a link or reparse point")
        if not path.is_dir() and not path.is_file():
            raise StateConflictError(
                f"{label} contains an unsupported filesystem entry"
            )


def file_manifest(
    root: Path, *, omitted: frozenset[str] = frozenset()
) -> tuple[dict[str, Any], ...]:
    assert_plain_tree(root, "workspace")
    rows: list[dict[str, Any]] = []
    for path in sorted(value for value in root.rglob("*") if value.is_file()):
        relative = path.relative_to(root).as_posix()
        if relative in omitted:
            continue
        raw = path.read_bytes()
        rows.append(
            {
                "path": relative,
                "byte_count": len(raw),
                "content_sha256": sha256_bytes(raw),
            }
        )
    return tuple(rows)


def tree_sha256(root: Path, *, omitted: frozenset[str] = frozenset()) -> str:
    return canonical_sha256(file_manifest(root, omitted=omitted))


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_bytes(dict(payload)))
