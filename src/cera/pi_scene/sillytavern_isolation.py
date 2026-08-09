"""Create and verify a disposable SillyTavern code copy without user data."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
from typing import Any

from cera.errors import ContractValidationError, StateConflictError
from cera.serialization import bytes_sha256, canonical_bytes, canonical_sha256


_EXCLUDED_DIRECTORIES = frozenset(
    {".git", ".gemini", ".github", ".vscode", "backups", "colab", "data", "plugins", "tests"}
)
_MANIFEST_NAME = "CERA_ISOLATED_COPY_MANIFEST.json"


def stage_isolated_sillytavern(source_root: Path, target_root: Path) -> dict[str, Any]:
    source = source_root.resolve()
    target = target_root.resolve()
    if not source.is_dir() or not (source / "server.js").is_file():
        raise ContractValidationError("SillyTavern source root is invalid")
    if not (source / "default" / "config.yaml").is_file():
        raise ContractValidationError("SillyTavern default config is unavailable")
    if target.exists():
        raise StateConflictError("isolated SillyTavern target already exists")
    if target == source or target.is_relative_to(source) or source.is_relative_to(target):
        raise ContractValidationError("isolated SillyTavern target overlaps the source")

    def ignore(directory: str, names: list[str]) -> set[str]:
        directory_path = Path(directory)
        at_source_root = directory_path == source
        ignored = {
            name
            for name in names
            if (at_source_root and (name in _EXCLUDED_DIRECTORIES or name == "config.yaml"))
            or name.endswith((".stdout.log", ".stderr.log"))
        }
        return ignored

    shutil.copytree(source, target, ignore=ignore, copy_function=shutil.copy2)
    shutil.copy2(source / "default" / "config.yaml", target / "config.yaml")
    (target / "data").mkdir()
    (target / "plugins").mkdir()
    copied_entries = _isolated_tree_entries(target)
    copied_files = [entry["path"] for entry in copied_entries]
    if any(value.startswith(("data/", "plugins/", "backups/", ".git/")) for value in copied_files):
        raise StateConflictError("isolated SillyTavern copy contains excluded user material")
    manifest = {
        "schema_version": "cera.pi_scene.isolated_sillytavern_copy.v2",
        "source_package_json_sha256": bytes_sha256((source / "package.json").read_bytes()),
        "source_package_lock_sha256": bytes_sha256((source / "package-lock.json").read_bytes()),
        "source_server_sha256": bytes_sha256((source / "server.js").read_bytes()),
        "default_config_sha256": bytes_sha256((source / "default" / "config.yaml").read_bytes()),
        "copied_file_count": len(copied_files),
        "copied_tree_sha256": canonical_sha256(copied_entries),
        "excluded_directories": sorted(_EXCLUDED_DIRECTORIES),
        "user_data_copied": False,
        "plugins_copied": False,
        "production": False,
        "loopback_launch_required": True,
    }
    manifest["manifest_sha256"] = canonical_sha256(manifest)
    (target / _MANIFEST_NAME).write_bytes(
        canonical_bytes(manifest) + b"\n"
    )
    return manifest


def verify_isolated_sillytavern(target_root: Path) -> dict[str, Any]:
    target = target_root.resolve()
    manifest_path = target / _MANIFEST_NAME
    if not manifest_path.is_file():
        raise StateConflictError("isolated SillyTavern manifest is missing")
    value = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise StateConflictError("isolated SillyTavern manifest is invalid")
    if value.get("schema_version") != "cera.pi_scene.isolated_sillytavern_copy.v2":
        raise StateConflictError("isolated SillyTavern manifest version is unsupported")
    expected = value.get("manifest_sha256")
    unsigned = {key: item for key, item in value.items() if key != "manifest_sha256"}
    if expected != canonical_sha256(unsigned):
        raise StateConflictError("isolated SillyTavern manifest binding changed")
    if value.get("user_data_copied") is not False or value.get("plugins_copied") is not False:
        raise StateConflictError("isolated SillyTavern copy claims protected material")
    for required in ("server.js", "package.json", "node_modules", "public", "src", "data"):
        if not (target / required).exists():
            raise StateConflictError(f"isolated SillyTavern copy lacks {required}")
    if not (target / "data").is_dir():
        raise StateConflictError("isolated SillyTavern data root is invalid")
    for protected in ("data", "plugins"):
        if any((target / protected).iterdir()):
            raise StateConflictError(
                f"isolated SillyTavern {protected} root contains protected material"
            )
    entries = _isolated_tree_entries(target)
    if len(entries) != value.get("copied_file_count"):
        raise StateConflictError("isolated SillyTavern copied-file count changed")
    if canonical_sha256(entries) != value.get("copied_tree_sha256"):
        raise StateConflictError("isolated SillyTavern executable tree changed")
    exact_files = {
        "server.js": "source_server_sha256",
        "package.json": "source_package_json_sha256",
        "package-lock.json": "source_package_lock_sha256",
        "config.yaml": "default_config_sha256",
    }
    entry_hashes = {entry["path"]: entry["sha256"] for entry in entries}
    for relative, manifest_field in exact_files.items():
        if entry_hashes.get(relative) != value.get(manifest_field):
            raise StateConflictError(
                f"isolated SillyTavern {relative} binding changed"
            )
    return value


def _isolated_tree_entries(root: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix().casefold()):
        if path.is_symlink():
            raise StateConflictError("isolated SillyTavern tree contains a symlink")
        if not path.is_file() or path.name == _MANIFEST_NAME:
            continue
        data = path.read_bytes()
        entries.append(
            {
                "path": path.relative_to(root).as_posix(),
                "bytes": len(data),
                "sha256": bytes_sha256(data),
            }
        )
    return entries


def isolated_sillytavern_command(
    target_root: Path,
    *,
    node_executable: Path,
    port: int,
) -> tuple[str, ...]:
    target = target_root.resolve()
    verify_isolated_sillytavern(target)
    if not node_executable.is_file() or not 1 <= port <= 65535:
        raise ContractValidationError("isolated SillyTavern launch configuration is invalid")
    return (
        str(node_executable.resolve()),
        str(target / "server.js"),
        "--port",
        str(port),
        "--dataRoot",
        str(target / "data"),
        "--configPath",
        str(target / "config.yaml"),
        "--no-listen",
        "--no-browserLaunchEnabled",
        "--whitelist",
    )
