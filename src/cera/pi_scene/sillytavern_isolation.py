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
    copied_files = sorted(
        path.relative_to(target).as_posix()
        for path in target.rglob("*")
        if path.is_file()
    )
    if any(value.startswith(("data/", "plugins/", "backups/", ".git/")) for value in copied_files):
        raise StateConflictError("isolated SillyTavern copy contains excluded user material")
    manifest = {
        "schema_version": "cera.pi_scene.isolated_sillytavern_copy.v1",
        "source_package_json_sha256": bytes_sha256((source / "package.json").read_bytes()),
        "source_package_lock_sha256": bytes_sha256((source / "package-lock.json").read_bytes()),
        "source_server_sha256": bytes_sha256((source / "server.js").read_bytes()),
        "default_config_sha256": bytes_sha256((source / "default" / "config.yaml").read_bytes()),
        "copied_file_count": len(copied_files),
        "excluded_directories": sorted(_EXCLUDED_DIRECTORIES),
        "user_data_copied": False,
        "plugins_copied": False,
        "production": False,
        "loopback_launch_required": True,
    }
    manifest["manifest_sha256"] = canonical_sha256(manifest)
    (target / "CERA_ISOLATED_COPY_MANIFEST.json").write_bytes(
        canonical_bytes(manifest) + b"\n"
    )
    return manifest


def verify_isolated_sillytavern(target_root: Path) -> dict[str, Any]:
    target = target_root.resolve()
    manifest_path = target / "CERA_ISOLATED_COPY_MANIFEST.json"
    if not manifest_path.is_file():
        raise StateConflictError("isolated SillyTavern manifest is missing")
    value = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise StateConflictError("isolated SillyTavern manifest is invalid")
    expected = value.pop("manifest_sha256", None)
    if expected != canonical_sha256(value):
        raise StateConflictError("isolated SillyTavern manifest binding changed")
    value["manifest_sha256"] = expected
    if value.get("user_data_copied") is not False or value.get("plugins_copied") is not False:
        raise StateConflictError("isolated SillyTavern copy claims protected material")
    for required in ("server.js", "package.json", "node_modules", "public", "src", "data"):
        if not (target / required).exists():
            raise StateConflictError(f"isolated SillyTavern copy lacks {required}")
    if not (target / "data").is_dir():
        raise StateConflictError("isolated SillyTavern data root is invalid")
    return value


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
