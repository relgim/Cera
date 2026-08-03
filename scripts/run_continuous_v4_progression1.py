"""Freeze Queue 0032 Progression 1 reproduction and exact test evidence.

This runner performs no provider construction or transport.  It first repeats
the immutable Cycle 23 selector failure in the disposable historical-source
clone, then executes the corrected selected matrix and the new diagnostic
contract gates from the current repository.  Evidence is published once by a
same-parent atomic directory rename.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import platform
import re
import socket
import sqlite3
import subprocess
import sys
import time
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
for import_root in (ROOT / "src", ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from cera.continuous.job4_diagnostics import (
    ContinuousJob4SourceFrameV1,
    ContinuousJob4TestDiagnosticsV1,
    ContinuousJob4TestRecordV1,
    run_job4_unittest_diagnostics,
)
from cera.serialization import canonical_sha256, text_sha256
from scripts import run_cera_sillytavern_continuous_manual as manual


CHECKPOINT_ID = "2026-08-02-continuous-sillytavern-overnight-v4-001"
PROGRESSION_ID = "continuous-cycle23-job4-exact-failure-evidence-and-reproduction-v4"
OUTPUT = (
    ROOT
    / ".chatgpt"
    / "pro-review"
    / "checkpoints"
    / CHECKPOINT_ID
    / "PROGRESSION_1_DIAGNOSTICS"
)
TEMP_OUTPUT = OUTPUT.with_name(OUTPUT.name + ".tmp-v4-p1-gate-001")

HISTORICAL_CLONE = Path("D:/CeraV4P1R1")
HISTORICAL_SOURCE_ARCHIVE = Path("D:/CeraV4P1R1-source-1ec.zip")
HISTORICAL_SOURCE_COMMIT = "1ec13ed351c7b79649b89c00351c32ee6f3cbcb4"
HISTORICAL_SOURCE_TREE = "4f63f68c7495be373616cdb7e3a4d47786cb4290"
HISTORICAL_ARCHIVE_SHA256 = (
    "251b4b2d755c4ca872b00faa551f5bcefea70271267a4ab4f3ac14c6ca6d8e4b"
)
HISTORICAL_TEST_SOURCE_SHA256 = (
    "6d6d61b1330de7fe639b3d4941a37a3e6222a8539a1035f2ba48d9bf064eacc5"
)
HISTORICAL_RUNNER_SHA256 = (
    "a3450cc45e480049541dcc66b66dc87a7c757d7ff3228b0b1f11f0a61da882fb"
)
HISTORICAL_RUNNER = (
    ROOT
    / ".chatgpt"
    / "pro-review"
    / "cycles"
    / "2026-08-02-continuous-sillytavern-overnight-v3-cycle-001"
    / "source"
    / "JOB4_AUDIT_RUNNER.py"
)
HISTORICAL_CYCLE = HISTORICAL_RUNNER.parents[1]
HISTORICAL_RUN001 = (
    ROOT
    / "runtime"
    / "evaluation"
    / "2026-08-02-continuous-sillytavern-two-run-v1"
)

HISTORICAL_TARGETS = (
    "tests.test_continuous_snapshot_path_custody",
    (
        "tests.test_pro_review_bridge.ProReviewBridgeTests."
        "test_28n_actual_scripted_v10_long_root_completes_chain_and_recovers"
    ),
    "tests.test_continuous_v2_execution_authority",
    "tests.test_continuous_v3_executable_readiness",
    "tests.test_continuous_role_conflict_regression",
    "tests.test_sillytavern_continuous_manual",
    "tests.test_sillytavern_continuous_v3",
    "tests.test_active_runtime_profile",
    "tests.test_documentation",
    (
        "tests.test_structural_contract_v2."
        "StructuralV2FailureAndInventoryTests."
        "test_repository_source_inventory_includes_runtime_and_v2_packages"
    ),
)
CORRECTED_TARGETS = (
    "tests.test_continuous_snapshot_path_custody",
    (
        "tests.test_pro_review_bridge.ProReviewRepositoryCycleTests."
        "test_28n_actual_scripted_v10_long_root_completes_chain_and_recovers"
    ),
    "tests.test_continuous_v2_execution_authority",
    "tests.test_continuous_v3_executable_readiness",
    "tests.test_continuous_role_conflict_regression",
    "tests.test_sillytavern_continuous_manual",
    "tests.test_sillytavern_continuous_v3",
    "tests.test_active_runtime_profile",
    "tests.test_documentation",
    (
        "tests.test_structural_contract_v2."
        "StructuralV2FailureAndInventoryTests."
        "test_repository_source_inventory_includes_runtime_and_v2_packages"
    ),
)
CONTRACT_TARGETS = (
    "tests.test_job4_diagnostics",
    (
        "tests.test_pro_review_bridge.ProReviewRepositoryCycleTests."
        "test_28c2_v3_test_diagnostics_cross_completion_and_recovery"
    ),
    (
        "tests.test_pro_review_bridge.ProReviewRepositoryCycleTests."
        "test_28c3_v3_test_diagnostics_artifact_tamper_fails_recovery"
    ),
    (
        "tests.test_pro_review_bridge.ProReviewRepositoryCycleTests."
        "test_28c4_v3_test_diagnostics_source_tamper_blocks_completion"
    ),
    (
        "tests.test_continuous_job4_harness.ContinuousJob4HarnessTests."
        "test_live_and_scripted_terminal_results_match_strict_cycle_schema"
    ),
    (
        "tests.test_continuous_job4_harness.ContinuousJob4HarnessTests."
        "test_strict_cycle_schema_rejects_legacy_canary_result_fields"
    ),
)

FAILED_TEST_ID = (
    "unittest.loader._FailedTest."
    "test_28n_actual_scripted_v10_long_root_completes_chain_and_recovers"
)
FAILED_MESSAGE = (
    "type object 'ProReviewBridgeTests' has no attribute "
    "'test_28n_actual_scripted_v10_long_root_completes_chain_and_recovers'"
)
FAILED_MESSAGE_SHA256 = (
    "1b925c54f1325c012cb37c75823f850bd69b3d036f7d3b8c93d65cc4eb2e8e62"
)
CORRECTED_BASE_TEST_ID = (
    "tests.test_pro_review_bridge.ProReviewRepositoryCycleTests."
    "test_28n_actual_scripted_v10_long_root_completes_chain_and_recovers"
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git(repository: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ("git", "-C", str(repository), *arguments),
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError("Git identity validation failed")
    return completed.stdout.strip()


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
        "src/cera/continuous/job4_diagnostics.py",
        "src/cera/continuous/job4_terminal.py",
        "scripts/run_continuous_planner_validator_job4.py",
        "scripts/run_continuous_v4_progression1.py",
        "tools/pro_review_cycle_core.py",
        "tools/pro_review_cycle.py",
        "tests/test_job4_diagnostics.py",
        "tests/test_pro_review_bridge.py",
        "docs/contracts/SCHEMA_CATALOG.md",
        "docs/operations/PRO_REVIEW_REPOSITORY_CYCLE.md",
    )
    return {relative: _sha256(ROOT / relative) for relative in paths}


def _run_historical_reproduction() -> dict[str, Any]:
    if _sha256(HISTORICAL_SOURCE_ARCHIVE) != HISTORICAL_ARCHIVE_SHA256:
        raise RuntimeError("historical source archive identity changed")
    if _git(HISTORICAL_CLONE, "rev-parse", "HEAD") != HISTORICAL_SOURCE_COMMIT:
        raise RuntimeError("historical clone commit changed")
    if (
        _git(HISTORICAL_CLONE, "rev-parse", "HEAD^{tree}")
        != HISTORICAL_SOURCE_TREE
    ):
        raise RuntimeError("historical clone tree changed")
    if (
        _sha256(HISTORICAL_CLONE / "tests/test_pro_review_bridge.py")
        != HISTORICAL_TEST_SOURCE_SHA256
    ):
        raise RuntimeError("historical test source changed")
    if _sha256(HISTORICAL_RUNNER) != HISTORICAL_RUNNER_SHA256:
        raise RuntimeError("immutable Cycle 23 runner changed")

    python = HISTORICAL_CLONE / ".venv" / "Scripts" / "python.exe"
    command = (str(python), "-m", "unittest", "-v", *HISTORICAL_TARGETS)
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join(
        (str(HISTORICAL_CLONE / "src"), str(HISTORICAL_CLONE))
    )
    started = time.perf_counter_ns()
    completed = subprocess.run(
        command,
        cwd=HISTORICAL_CLONE,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=1200,
    )
    elapsed_ns = time.perf_counter_ns() - started
    output = completed.stdout + completed.stderr
    required = (
        "Ran 71 tests in",
        "FAILED (errors=1)",
        "AttributeError: " + FAILED_MESSAGE,
    )
    if completed.returncode == 0 or any(value not in output for value in required):
        raise RuntimeError("Cycle 23 exact failure did not reproduce")
    if len(re.findall(r"^ERROR:", output, flags=re.MULTILINE)) != 1:
        raise RuntimeError("Cycle 23 reproduction error count changed")
    output_lines = tuple(line.strip() for line in output.splitlines())
    terminal_summary = (
        next(line for line in output_lines if line.startswith("Ran 71 tests in")),
        next(line for line in output_lines if line == "FAILED (errors=1)"),
        next(
            line
            for line in output_lines
            if line == "AttributeError: " + FAILED_MESSAGE
        ),
    )

    record = ContinuousJob4TestRecordV1(
        index=1,
        test_id=FAILED_TEST_ID,
        status="error",
        elapsed_ns=0,
        exception_type="builtins.AttributeError",
        message_sha256=text_sha256(FAILED_MESSAGE),
        source_frames=(
            ContinuousJob4SourceFrameV1(
                relative_path=(
                    ".chatgpt/pro-review/cycles/"
                    "2026-08-02-continuous-sillytavern-overnight-v3-cycle-001/"
                    "source/JOB4_AUDIT_RUNNER.py"
                ),
                function="TEST_TARGETS",
                line=73,
            ),
            ContinuousJob4SourceFrameV1(
                relative_path="tests/test_pro_review_bridge.py",
                function=(
                    "ProReviewRepositoryCycleTests."
                    "test_28n_actual_scripted_v10_long_root_completes_chain_and_recovers"
                ),
                line=1089,
            ),
        ),
    )
    if record.message_sha256 != FAILED_MESSAGE_SHA256:
        raise RuntimeError("Cycle 23 exception message identity changed")
    return {
        "schema_version": "cera.cycle23_exact_failure_reproduction.v1",
        "progression_id": PROGRESSION_ID,
        "status": "reproduced",
        "source_identity": {
            "git_sha": HISTORICAL_SOURCE_COMMIT,
            "git_tree_sha": HISTORICAL_SOURCE_TREE,
            "source_archive_sha256": HISTORICAL_ARCHIVE_SHA256,
            "historical_test_source_sha256": HISTORICAL_TEST_SOURCE_SHA256,
            "cycle23_runner_sha256": HISTORICAL_RUNNER_SHA256,
        },
        "environment": {
            "python_implementation": platform.python_implementation(),
            "python_version": platform.python_version(),
            "platform": platform.platform(),
            "historical_clone": HISTORICAL_CLONE.as_posix(),
            "python_executable_sha256": _sha256(python),
        },
        "command": list(command),
        "declared_test_targets": list(HISTORICAL_TARGETS),
        "selected_test_count": 71,
        "passed_test_count": 70,
        "failure_count": 0,
        "error_count": 1,
        "elapsed_ns": elapsed_ns,
        "return_code": completed.returncode,
        "terminal_output_sha256": text_sha256(output),
        "terminal_summary": list(terminal_summary),
        "failing_test_record": record.to_dict(),
        "external_provider_calls": 0,
        "retry_count": 0,
        "fallback_count": 0,
    }


def _run_matrix(
    targets: Sequence[str], *, expected_count: int
) -> tuple[dict[str, Any], ContinuousJob4TestDiagnosticsV1]:
    stream = io.StringIO()
    started = time.perf_counter_ns()
    result, diagnostics = run_job4_unittest_diagnostics(
        targets,
        verbosity=2,
        stream=stream,
    )
    elapsed_ns = time.perf_counter_ns() - started
    if (
        not result.wasSuccessful()
        or len(diagnostics.records) != expected_count
        or diagnostics.status_counts
        != {"passed": expected_count, "failed": 0, "error": 0, "skipped": 0}
    ):
        raise RuntimeError("Progression 1 corrected test matrix failed")
    return (
        {
            "declared_targets": list(targets),
            "selected_test_count": expected_count,
            "passed_test_count": expected_count,
            "elapsed_ns": elapsed_ns,
            "terminal_output_sha256": text_sha256(stream.getvalue()),
            "selection_root_sha256": diagnostics.selection_root_sha256,
            "records_root_sha256": diagnostics.records_root_sha256,
            "diagnostics_sha256": diagnostics.sha256,
        },
        diagnostics,
    )


def main() -> int:
    if OUTPUT.exists() or TEMP_OUTPUT.exists():
        raise RuntimeError("Progression 1 diagnostic identity is already occupied")
    TEMP_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    TEMP_OUTPUT.mkdir()

    cycle_before = _tree_hash(HISTORICAL_CYCLE)
    run001_before = _tree_hash(HISTORICAL_RUN001)
    isolation_before = manual.isolation_inventory()
    database_before = _sqlite_state(
        isolation_before["persistent_human_test_database"]["path"]
    )

    reproduction = _run_historical_reproduction()
    selected_summary, selected = _run_matrix(CORRECTED_TARGETS, expected_count=71)
    contract_summary, contract = _run_matrix(CONTRACT_TARGETS, expected_count=11)

    base_records = {
        record.test_id: record for record in selected.records
    }
    if (
        CORRECTED_BASE_TEST_ID not in base_records
        or base_records[CORRECTED_BASE_TEST_ID].status != "passed"
    ):
        raise RuntimeError("inherited scripted ten-stage base transaction is unproven")

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
        raise RuntimeError("Progression 1 isolation changed")
    if database_after["quick_check"] != "ok" or database_after[
        "foreign_key_findings"
    ]:
        raise RuntimeError("persistent database integrity changed")

    reproduction_path = TEMP_OUTPUT / "CYCLE23_REPRODUCTION.json"
    selected_path = TEMP_OUTPUT / "SELECTED_TEST_DIAGNOSTICS.json"
    contract_path = TEMP_OUTPUT / "CONTRACT_TEST_DIAGNOSTICS.json"
    _write_json(reproduction_path, reproduction)
    _write_json(selected_path, selected.to_dict())
    _write_json(contract_path, contract.to_dict())
    gate = {
        "schema_version": "cera.continuous_v4_progression1_gate.v1",
        "checkpoint_id": CHECKPOINT_ID,
        "progression_id": PROGRESSION_ID,
        "status": "passed",
        "starting_git_sha": _git(ROOT, "rev-parse", "HEAD"),
        "starting_git_tree_sha": _git(ROOT, "rev-parse", "HEAD^{tree}"),
        "cycle23_reproduction": {
            "status": reproduction["status"],
            "artifact_sha256": _sha256(reproduction_path),
            "failing_test_record_sha256": reproduction[
                "failing_test_record"
            ]["record_sha256"],
        },
        "corrected_selected_matrix": selected_summary,
        "diagnostic_contract_matrix": contract_summary,
        "inherited_scripted_ten_stage_base_transaction": {
            "test_id": CORRECTED_BASE_TEST_ID,
            "status": base_records[CORRECTED_BASE_TEST_ID].status,
            "record_sha256": base_records[CORRECTED_BASE_TEST_ID].record_sha256,
            "local_scripted_stage_count": 10,
        },
        "artifact_hashes": {
            "CYCLE23_REPRODUCTION.json": _sha256(reproduction_path),
            "SELECTED_TEST_DIAGNOSTICS.json": _sha256(selected_path),
            "CONTRACT_TEST_DIAGNOSTICS.json": _sha256(contract_path),
        },
        "source_hashes": _source_hashes(),
        "isolation": isolation,
        "persistent_database": database_after,
        "cycle23_tree": cycle_before,
        "historical_run001_tree": run001_before,
        "external_provider_calls": 0,
        "retry_count": 0,
        "fallback_count": 0,
        "hidden_repair_count": 0,
        "story_writes": 0,
        "persistent_database_writes": 0,
        "active_route_changes": 0,
    }
    _write_json(TEMP_OUTPUT / "PROGRESSION_1_GATE.json", gate)
    os.replace(TEMP_OUTPUT, OUTPUT)
    print(json.dumps(gate, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
