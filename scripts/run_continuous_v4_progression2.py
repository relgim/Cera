"""Freeze Queue 0033 Progression 2 no-follow and reparse evidence.

This runner is provider-free.  It executes the inherited corrected selected
matrix plus the complete branch-materialization adversarial module, emits
exact per-test diagnostics, and records an independent Windows junction/file-
symlink capability probe.  Evidence is published once by same-parent atomic
directory rename.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
for import_root in (ROOT / "src", ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from cera.continuous.job4_diagnostics import run_job4_unittest_diagnostics
from cera.continuous.path_custody import inspect_no_follow
from cera.errors import StateConflictError
from cera.serialization import canonical_sha256, text_sha256
from scripts import run_cera_sillytavern_continuous_manual as manual
from scripts.run_continuous_v4_progression1 import (
    CORRECTED_BASE_TEST_ID,
    CORRECTED_TARGETS,
    HISTORICAL_CYCLE,
    HISTORICAL_RUN001,
)


CHECKPOINT_ID = "2026-08-02-continuous-sillytavern-overnight-v4-001"
PROGRESSION_ID = "continuous-no-follow-snapshot-reparse-and-atomic-path-custody-v4"
OUTPUT = (
    ROOT
    / ".chatgpt"
    / "pro-review"
    / "checkpoints"
    / CHECKPOINT_ID
    / "PROGRESSION_2_DIAGNOSTICS"
)
TEMP_OUTPUT = OUTPUT.with_name(OUTPUT.name + ".tmp-v4-p2-gate-001")
PATH_TARGETS = CORRECTED_TARGETS + (
    "tests.test_continuous_branch_materialization",
)
EXPECTED_SELECTED_TEST_COUNT = 87


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git(*arguments: str) -> str:
    result = subprocess.run(
        ("git", "-C", str(ROOT), *arguments),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError("Git identity validation failed")
    return result.stdout.strip()


def _tree_hash(root: Path) -> dict[str, Any]:
    files = {
        path.relative_to(root).as_posix(): _sha256(path)
        for path in sorted(item for item in root.rglob("*") if item.is_file())
    }
    return {"file_count": len(files), "sha256": canonical_sha256(files)}


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    if path.exists():
        raise RuntimeError(f"refusing to overwrite evidence: {path.name}")
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as connection:
        connection.settimeout(0.2)
        return connection.connect_ex(("127.0.0.1", port)) == 0


def _sqlite_state(relative_path: str) -> dict[str, Any]:
    path = ROOT / relative_path
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        quick_check = connection.execute("PRAGMA quick_check").fetchone()[0]
        foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
    finally:
        connection.close()
    return {
        "relative_path": relative_path,
        "sha256": _sha256(path),
        "quick_check": quick_check,
        "foreign_key_findings": len(foreign_keys),
    }


def _source_hashes() -> dict[str, str]:
    paths = (
        "src/cera/continuous/path_custody.py",
        "src/cera/continuous/sessions.py",
        "src/cera/continuous/evidence.py",
        "src/cera/continuous/world.py",
        "src/cera/continuous/runtime.py",
        "src/cera/continuous/__init__.py",
        "src/cera/registry.py",
        "tests/test_continuous_snapshot_path_custody.py",
        "tests/test_continuous_branch_materialization.py",
        "tests/test_continuous_job4_harness.py",
        "tests/test_pro_review_bridge.py",
        "docs/contracts/SCHEMA_CATALOG.md",
        "docs/authority/DECISIONS_AND_SUPERSESSIONS.md",
        "docs/implementation/CONTINUOUS_NO_FOLLOW_PATH_CUSTODY_V4_RESULT.md",
        "scripts/run_continuous_v4_progression2.py",
    )
    return {relative: _sha256(ROOT / relative) for relative in paths}


def _remove_alias(path: Path) -> None:
    if os.name == "nt" and os.path.isjunction(path):
        path.rmdir()
    elif path.is_symlink():
        path.unlink()


def _capability_probe() -> dict[str, Any]:
    with TemporaryDirectory(prefix="cera-v4-p2-reparse-") as directory:
        root = Path(directory)
        target = root / "junction-target"
        target.mkdir()
        link = root / "junction-link"
        if os.name == "nt":
            result = subprocess.run(
                [
                    "cmd.exe",
                    "/d",
                    "/c",
                    "mklink",
                    "/J",
                    str(link),
                    str(target),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode != 0 or not os.path.isjunction(link):
                raise RuntimeError(
                    "required Windows junction capability is unavailable: "
                    + json.dumps(
                        {
                            "returncode": result.returncode,
                            "stdout_sha256": text_sha256(result.stdout),
                            "stderr_sha256": text_sha256(result.stderr),
                            "isjunction": os.path.isjunction(link),
                        },
                        sort_keys=True,
                    )
                )
            junction_mechanism = "windows_mklink_j"
        else:
            os.symlink(target, link, target_is_directory=True)
            junction_mechanism = "posix_directory_symlink"
        try:
            tag = int(getattr(os.lstat(link), "st_reparse_tag", 0))
            try:
                inspect_no_follow(link)
            except StateConflictError as exc:
                junction_rejection = type(exc).__name__
            else:
                raise RuntimeError("junction/reparse probe was not rejected")
        finally:
            _remove_alias(link)

        file_target = root / "file-target.json"
        file_target.write_text("{}\n", encoding="utf-8")
        file_link = root / "file-link.json"
        file_symlink: dict[str, Any]
        try:
            os.symlink(file_target, file_link, target_is_directory=False)
        except OSError as exc:
            file_symlink = {
                "supported": False,
                "error_type": type(exc).__name__,
                "errno": exc.errno,
                "winerror": getattr(exc, "winerror", None),
            }
        else:
            try:
                try:
                    inspect_no_follow(file_link)
                except StateConflictError as exc:
                    rejection = type(exc).__name__
                else:
                    raise RuntimeError("file-symlink probe was not rejected")
                file_symlink = {
                    "supported": True,
                    "reparse_tag": int(
                        getattr(os.lstat(file_link), "st_reparse_tag", 0)
                    ),
                    "rejection_error_type": rejection,
                }
            finally:
                _remove_alias(file_link)
        return {
            "schema_version": "cera.continuous_v4_path_capability_receipt.v1",
            "platform": os.name,
            "junction_or_directory_alias": {
                "mechanism": junction_mechanism,
                "reparse_tag": tag,
                "rejection_error_type": junction_rejection,
                "verified": True,
            },
            "file_symlink": file_symlink,
            "status": "passed",
        }


def main() -> int:
    if OUTPUT.exists() or TEMP_OUTPUT.exists():
        raise RuntimeError("Progression 2 diagnostic identity is already occupied")
    TEMP_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    TEMP_OUTPUT.mkdir()

    cycle_before = _tree_hash(HISTORICAL_CYCLE)
    run001_before = _tree_hash(HISTORICAL_RUN001)
    isolation_before = manual.isolation_inventory()
    database_before = _sqlite_state(
        isolation_before["persistent_human_test_database"]["path"]
    )

    stream = io.StringIO()
    started = time.perf_counter_ns()
    result, diagnostics = run_job4_unittest_diagnostics(
        PATH_TARGETS,
        verbosity=2,
        stream=stream,
    )
    elapsed_ns = time.perf_counter_ns() - started
    if (
        not result.wasSuccessful()
        or len(diagnostics.records) != EXPECTED_SELECTED_TEST_COUNT
        or diagnostics.status_counts
        != {
            "passed": EXPECTED_SELECTED_TEST_COUNT,
            "failed": 0,
            "error": 0,
            "skipped": 0,
        }
    ):
        raise RuntimeError(
            "Progression 2 exact selected matrix failed: "
            + json.dumps(
                {
                    "tests_run": result.testsRun,
                    "record_count": len(diagnostics.records),
                    "status_counts": diagnostics.status_counts,
                    "output_sha256": text_sha256(stream.getvalue()),
                },
                sort_keys=True,
            )
        )
    records = {value.test_id: value for value in diagnostics.records}
    if CORRECTED_BASE_TEST_ID not in records:
        raise RuntimeError("inherited ten-stage transaction test is absent")

    capability = _capability_probe()
    diagnostics_path = TEMP_OUTPUT / "SELECTED_TEST_DIAGNOSTICS.json"
    capability_path = TEMP_OUTPUT / "PATH_CAPABILITY_RECEIPT.json"
    _write_json(diagnostics_path, diagnostics.to_dict())
    _write_json(capability_path, capability)

    isolation_after = manual.isolation_inventory()
    database_after = _sqlite_state(
        isolation_after["persistent_human_test_database"]["path"]
    )
    isolation = {
        "cycle23_unchanged": cycle_before == _tree_hash(HISTORICAL_CYCLE),
        "historical_run001_unchanged": run001_before
        == _tree_hash(HISTORICAL_RUN001),
        "persistent_database_unchanged": database_before == database_after,
        "active_runtime_unchanged": isolation_before["active_runtime"]
        == isolation_after["active_runtime"],
        "installed_sillytavern_unchanged": isolation_before[
            "installed_sillytavern"
        ]
        == isolation_after["installed_sillytavern"],
        "background_ports_unchanged": all(
            isolation_before["loopback_ports"][str(port)]
            == isolation_after["loopback_ports"][str(port)]
            for port in (5101, 8000)
        ),
        "qualification_ports_closed": all(
            not _port_open(port) for port in (5113, 5114, 5115)
        ),
    }
    isolation["passed"] = all(isolation.values())
    if not isolation["passed"]:
        raise RuntimeError("Progression 2 isolation changed")
    if database_after["quick_check"] != "ok" or database_after[
        "foreign_key_findings"
    ]:
        raise RuntimeError("persistent database integrity changed")

    base_record = records[CORRECTED_BASE_TEST_ID]
    gate = {
        "schema_version": "cera.continuous_v4_progression2_gate.v1",
        "checkpoint_id": CHECKPOINT_ID,
        "progression_id": PROGRESSION_ID,
        "status": "passed",
        "starting_git_sha": _git("rev-parse", "HEAD"),
        "starting_git_tree_sha": _git("rev-parse", "HEAD^{tree}"),
        "selected_matrix": {
            "declared_targets": list(PATH_TARGETS),
            "selected_test_count": EXPECTED_SELECTED_TEST_COUNT,
            "passed_test_count": EXPECTED_SELECTED_TEST_COUNT,
            "elapsed_ns": elapsed_ns,
            "selection_root_sha256": diagnostics.selection_root_sha256,
            "records_root_sha256": diagnostics.records_root_sha256,
            "diagnostics_sha256": diagnostics.sha256,
            "terminal_output_sha256": text_sha256(stream.getvalue()),
        },
        "inherited_scripted_ten_stage_base_transaction": {
            "test_id": CORRECTED_BASE_TEST_ID,
            "status": base_record.status,
            "record_sha256": base_record.record_sha256,
            "local_scripted_stage_count": 10,
        },
        "path_capability_receipt_sha256": _sha256(capability_path),
        "selected_test_diagnostics_sha256": _sha256(diagnostics_path),
        "source_hashes": _source_hashes(),
        "isolation": isolation,
        "persistent_database": database_after,
        "cycle23_tree": cycle_before,
        "historical_run001_tree": run001_before,
        "external_provider_calls": 0,
        "retry_count": 0,
        "fallback_count": 0,
        "story_writes": 0,
        "persistent_database_writes": 0,
        "active_route_changes": 0,
    }
    _write_json(TEMP_OUTPUT / "PROGRESSION_2_GATE.json", gate)
    os.replace(TEMP_OUTPUT, OUTPUT)
    print(json.dumps(gate, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
