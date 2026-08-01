"""Provider-free terminal-effect audit for continuous correction cycle 010."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ROOT / "src"
for import_root in (SOURCE_ROOT, ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from cera.active_runtime_validation import active_runtime_status
from cera.continuous.job4_terminal import (
    ContinuousJob4OperationalCountersV1,
    ContinuousJob4PostconditionsV1,
    ContinuousJob4TerminalEvidenceV1,
)
from cera.serialization import canonical_bytes
from scripts.run_continuous_planner_validator_job4 import (
    build_canonical_job4_result,
    build_report,
    validate_declared_unittest_ids,
)


CYCLE_ID = "2026-08-01-continuous-planner-validator-v1-corrections-cycle-010"
TASK_ID = "continuous-corrections-v10-provider-free-terminal-effect-audit"
AUDIT_VERSION = "v10"
SCRIPTED_COMPLETION_TEST = (
    "tests.test_pro_review_bridge.ProReviewRepositoryCycleTests."
    "test_28d_actual_scripted_v8_canary_completes_job4_transport"
)

CASES = (
    (1, "All four canonical effects preserve exact nonzero evidence", "tests.test_continuous_job4_harness.ContinuousJob4HarnessTests.test_terminal_effect_evidence_preserves_each_nonzero_canonical_effect"),
    (2, "Missing, boolean, negative, malformed, and contradictory effects fail", "tests.test_continuous_job4_harness.ContinuousJob4HarnessTests.test_terminal_effect_evidence_rejects_missing_boolean_negative_and_contradictory_values"),
    (3, "Every mandatory postcondition controls terminal status", "tests.test_continuous_job4_harness.ContinuousJob4HarnessTests.test_terminal_postconditions_force_failure_for_every_mandatory_class"),
    (4, "Completed terminal results contain no failed mandatory verification", "tests.test_continuous_job4_harness.ContinuousJob4HarnessTests.test_completed_terminal_result_cannot_contain_a_failed_mandatory_verification"),
    (5, "Live and scripted terminal results share the strict DTO", "tests.test_continuous_job4_harness.ContinuousJob4HarnessTests.test_live_and_scripted_terminal_results_match_strict_cycle_schema"),
    (6, "Legacy canary-only fields remain rejected by the strict decoder", "tests.test_continuous_job4_harness.ContinuousJob4HarnessTests.test_strict_cycle_schema_rejects_legacy_canary_result_fields"),
    (7, "Completed live-shaped results cross complete-job4", "tests.test_pro_review_bridge.ProReviewRepositoryCycleTests.test_28a_completed_live_shaped_canary_result_completes_job4"),
    (8, "Failed live-shaped results cross complete-job4", "tests.test_pro_review_bridge.ProReviewRepositoryCycleTests.test_28b_failed_live_shaped_canary_result_completes_job4"),
    (9, "Failed scripted results cross complete-job4", "tests.test_pro_review_bridge.ProReviewRepositoryCycleTests.test_28c_failed_scripted_canary_result_completes_job4"),
    (10, "The actual scripted-V8 CLI crosses the real completion path", SCRIPTED_COMPLETION_TEST),
    (11, "complete-job4 rejects both historical unknown fields", "tests.test_pro_review_bridge.ProReviewRepositoryCycleTests.test_28e_complete_job4_rejects_legacy_canary_fields"),
    (12, "Story effects survive artifact copy, receipt, and recovery", "tests.test_pro_review_bridge.ProReviewRepositoryCycleTests.test_28f_story_effect_evidence_survives_completion_copy_receipt_and_recovery"),
    (13, "Active-profile inspection failure remains a route effect", "tests.test_pro_review_bridge.ProReviewRepositoryCycleTests.test_28g_active_profile_failure_survives_completion_as_route_effect"),
    (14, "Operational effects survive repository completion", "tests.test_pro_review_bridge.ProReviewRepositoryCycleTests.test_28h_operational_effect_evidence_survives_completion"),
    (15, "Repository source inventory includes every runtime source", "tests.test_structural_contract_v2.StructuralV2FailureAndInventoryTests.test_repository_source_inventory_includes_runtime_and_v2_packages"),
    (16, "Controlling documentation is complete and linked", "tests.test_documentation.DocumentationTests.test_authoritative_documentation_is_complete_and_linked"),
    (17, "D-180 active runtime identity remains exact", "tests.test_active_runtime_profile.ActiveRuntimeProfileTests.test_all_active_source_bindings_match_the_canonical_profile"),
)


class RecordingResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.passed: set[str] = set()

    def addSuccess(self, test):
        super().addSuccess(test)
        self.passed.add(test.id())


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inspect_active_profile() -> tuple[str | None, str]:
    try:
        status = active_runtime_status()
    except Exception:
        return None, "failed"
    value = status.get("profile_sha256")
    if not isinstance(value, str) or len(value) != 64:
        return None, "failed"
    return value, "verified"


def sqlite_check(source: Path) -> dict[str, object]:
    source_before = digest(source)
    with tempfile.TemporaryDirectory(prefix="cera-corrections-v10-job4-") as directory:
        copied = Path(directory) / "disposable.sqlite3"
        shutil.copy2(source, copied)
        copy_before = digest(copied)
        connection = sqlite3.connect(f"file:{copied.as_posix()}?mode=ro", uri=True)
        try:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
            foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
        finally:
            connection.close()
        copy_after = digest(copied)
    source_after = digest(source)
    return {
        "source_sha256_before": source_before,
        "source_sha256_after": source_after,
        "disposable_sha256_before": copy_before,
        "disposable_sha256_after": copy_after,
        "integrity_check": integrity,
        "foreign_key_findings": len(foreign_keys),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cycle-directory", type=Path, required=True)
    parser.add_argument("--source-database", type=Path, required=True)
    arguments = parser.parse_args()
    cycle = arguments.cycle_directory.resolve()
    source = cycle / "source"
    source.mkdir(parents=True, exist_ok=True)
    report_path = source / "JOB4_REPORT.md"
    result_path = source / "JOB4_RESULT.json"
    if report_path.exists() or result_path.exists():
        raise SystemExit("refusing to overwrite Job 4 evidence")

    test_ids = tuple(case[2] for case in CASES)
    preflight = validate_declared_unittest_ids(test_ids)
    profile_before, before_status = inspect_active_profile()
    started_at = datetime.now(UTC).isoformat()
    started = time.perf_counter()
    suite = unittest.TestLoader().loadTestsFromNames(test_ids)
    result = unittest.TextTestRunner(
        verbosity=2, resultclass=RecordingResult
    ).run(suite)
    profile_after, after_status = inspect_active_profile()
    database = sqlite_check(arguments.source_database.resolve())
    elapsed = time.perf_counter() - started

    passed = set(result.passed)
    cases = [
        {
            "case_id": number,
            "name": name,
            "test_id": test_id,
            "status": "passed" if test_id in passed else "failed",
        }
        for number, name, test_id in CASES
    ]
    database_passed = bool(
        database["source_sha256_before"] == database["source_sha256_after"]
        and database["disposable_sha256_before"]
        == database["disposable_sha256_after"]
        and database["integrity_check"] == "ok"
        and database["foreign_key_findings"] == 0
    )
    cases.append(
        {
            "case_id": len(CASES) + 1,
            "name": "Source and disposable SQLite evidence remains exact",
            "test_id": "provider_free_read_only_sqlite_check",
            "status": "passed" if database_passed else "failed",
        }
    )
    scripted_completed = SCRIPTED_COMPLETION_TEST in passed
    profile_status = (
        "verified"
        if before_status == after_status == "verified"
        else "failed"
    )
    execution_completed = bool(
        result.wasSuccessful() and database_passed and profile_status == "verified"
    )
    postconditions = ContinuousJob4PostconditionsV1(
        execution_mode="provider_free_scripted_v8",
        source_database_sha256_before=database["source_sha256_before"],
        source_database_sha256_after=database["source_sha256_after"],
        disposable_database_sha256_before=database[
            "disposable_sha256_before"
        ],
        disposable_database_sha256_after=database[
            "disposable_sha256_after"
        ],
        database_integrity_check=database["integrity_check"],
        database_foreign_key_findings=database["foreign_key_findings"],
        active_profile_sha256_before=profile_before,
        active_profile_sha256_after=profile_after,
        active_profile_inspection_status=profile_status,
        thread_archival={
            "planner": scripted_completed,
            "validator": scripted_completed,
        },
        accepted_session_synchronized=scripted_completed,
        accepted_final_sequences_injected=scripted_completed,
        call_ledger_dispatches=10 if scripted_completed else 0,
        scripted_transport_invocations=10 if scripted_completed else 0,
    )
    counters = ContinuousJob4OperationalCountersV1(
        live_story_writes=0,
        production_database_writes=0,
        deployment_operations=0,
        remote_operations=0,
        merge_operations=0,
        push_operations=0,
        service_changes=0,
        installed_sillytavern_changes=0,
    )
    terminal = ContinuousJob4TerminalEvidenceV1.build(
        execution_status="completed" if execution_completed else "failed",
        provider_calls=0,
        operational_counters=counters,
        postconditions=postconditions,
    )
    effects = terminal.effect_evidence.canonical_effects
    detail = {
        "cycle_id": CYCLE_ID,
        "task_id": TASK_ID,
        "status": terminal.status,
        "execution_status": terminal.execution_status,
        "execution_mode": postconditions.execution_mode,
        "provider_calls": effects["provider_calls"],
        "scripted_transport_invocations": postconditions.scripted_transport_invocations,
        "story_database_writes": effects["story_database_writes"],
        "active_route_changes": effects["active_route_changes"],
        "deployment_remote_or_push_effects": effects[
            "deployment_remote_or_push_effects"
        ],
        "source_database_unchanged": (
            postconditions.source_database_sha256_before
            == postconditions.source_database_sha256_after
        ),
        "copy_database_unchanged": (
            postconditions.disposable_database_sha256_before
            == postconditions.disposable_database_sha256_after
        ),
        "active_route_unchanged": postconditions.active_route_changes == 0,
        "thread_archival": dict(postconditions.thread_archival),
        "terminal_evidence": terminal.to_dict(),
        "terminal_evidence_sha256": terminal.sha256,
        "calls": [],
        "turns": [],
        "failure": (
            None
            if terminal.status == "completed"
            else {"stage": "provider_free_terminal_effect_audit"}
        ),
    }
    report = build_report(detail, task_id=TASK_ID)
    report += "\n## Audit assertions\n\n"
    report += "\n".join(
        f"{case['case_id']}. **{case['status']}** - {case['name']} (`{case['test_id']}`)"
        for case in cases
    )
    report += (
        "\n\n## SQLite evidence\n\n```json\n"
        + json.dumps(database, indent=2, sort_keys=True)
        + "\n```\n\n"
        + f"Started UTC: `{started_at}`  \n"
        + f"Elapsed seconds: `{elapsed:.3f}`  \n"
        + f"Preflight-resolved tests: `{len(preflight)}`\n"
    )
    report_path.write_text(report, encoding="utf-8", newline="\n")
    payload = build_canonical_job4_result(
        detail,
        task_id=TASK_ID,
        report_sha256=digest(report_path),
    )
    result_path.write_bytes(canonical_bytes(payload) + b"\n")
    print(json.dumps(payload, sort_keys=True))
    return 0 if terminal.status == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
