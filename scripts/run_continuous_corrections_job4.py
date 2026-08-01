"""Run the provider-free D-186 corrections integration audit.

The audit invokes only deterministic tests and read-only SQLite checks. It
does not construct provider transports, mutate an active route, or write story
state. The review-cycle source directory is the only durable output location.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import time
import unittest

from cera.serialization import canonical_bytes


ROOT = Path(__file__).resolve().parents[1]
CYCLE_ID = "2026-08-01-continuous-planner-validator-v1-corrections-cycle-001"
TASK_ID = "continuous-corrections-provider-free-integration-audit-v1"


CASES = (
    (1, "Three-turn, two-scene continuous flow", "tests.test_continuous_corrections.ContinuousProviderFreeIntegrationTests.test_three_turn_two_scene_flow_keeps_separate_sessions_and_appends_once"),
    (2, "Separate persistent fake Planner and Validator sessions", "tests.test_continuous_corrections.ContinuousProviderFreeIntegrationTests.test_three_turn_two_scene_flow_keeps_separate_sessions_and_appends_once"),
    (3, "Exact accepted-final append once", "tests.test_continuous_corrections.ContinuousProviderFreeIntegrationTests.test_three_turn_two_scene_flow_keeps_separate_sessions_and_appends_once"),
    (4, "Authoritative current-source and exact-read binding resolution", "tests.test_continuous_corrections.ContinuousEvidenceCorrectionTests.test_exact_read_allocates_authoritative_request_local_binding"),
    (5, "Invented evidence binding rejection", "tests.test_continuous_corrections.ContinuousEvidenceCorrectionTests.test_invented_and_stale_bindings_fail"),
    (6, "Stale revision rejection", "tests.test_continuous_corrections.ContinuousEvidenceCorrectionTests.test_invented_and_stale_bindings_fail"),
    (7, "Private-knowledge transfer rejection", "tests.test_continuous_corrections.ContinuousEvidenceCorrectionTests.test_sibling_binding_and_private_knowledge_transfer_fail"),
    (8, "Concern or Critical False Positive acceptance", "tests.test_continuous_world.ContinuousWorldTests.test_false_positive_accepts_eligible_concern_and_records_diagnostic"),
    (9, "Good assessment requires ordinary Accept", "tests.test_continuous_world.ContinuousWorldTests.test_false_positive_rejects_good_assessment"),
    (10, "Scene Summary is derived and non-authoritative", "tests.test_continuous_corrections.ContinuousWorldHardeningTests.test_scene_summary_is_regenerable_derived_view_and_never_changes_active"),
    (11, "New-scene prompt excluded from prior summary", "tests.test_continuous_world.ContinuousWorldTests.test_scene_change_uses_allow_list_tail_and_excludes_new_prompt"),
    (12, "Crash recovery at every promotion cut point", "tests.test_continuous_corrections.ContinuousWorldHardeningTests.test_restart_recovery_covers_every_promotion_cut_point"),
    (13, "Post-dispatch Planner failure counted", "tests.test_continuous_corrections.ContinuousCallAccountingTests.test_post_dispatch_failure_matrix_is_conservatively_counted"),
    (14, "Post-dispatch DeepSeek failure counted", "tests.test_continuous_corrections.ContinuousCallAccountingTests.test_post_dispatch_failure_matrix_is_conservatively_counted"),
    (15, "Post-dispatch Validator failure counted", "tests.test_continuous_corrections.ContinuousCallAccountingTests.test_post_dispatch_failure_matrix_is_conservatively_counted"),
    (16, "Pre-provider failure has safe root diagnostic", "tests.test_continuous_corrections.ContinuousWorldHardeningTests.test_embedded_secret_redaction_and_root_attribute_diagnostic"),
    (17, "Embedded-secret redaction", "tests.test_continuous_corrections.ContinuousWorldHardeningTests.test_embedded_secret_redaction_and_root_attribute_diagnostic"),
)


class RecordingResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.passed: set[str] = set()

    def addSuccess(self, test):
        super().addSuccess(test)
        self.passed.add(test.id())


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sqlite_check(source: Path) -> dict[str, object]:
    before = digest(source)
    with tempfile.TemporaryDirectory(prefix="cera-corrections-job4-") as directory:
        copied = Path(directory) / "disposable.sqlite3"
        shutil.copy2(source, copied)
        copy_before = digest(copied)
        with sqlite3.connect(f"file:{copied.as_posix()}?mode=ro", uri=True) as connection:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
            foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
        copy_after = digest(copied)
    after = digest(source)
    return {
        "source_path": str(source),
        "source_sha256_before": before,
        "source_sha256_after": after,
        "source_unchanged": before == after,
        "disposable_sha256_before": copy_before,
        "disposable_sha256_after": copy_after,
        "disposable_unchanged": copy_before == copy_after,
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
    database = arguments.source_database.resolve()
    started = time.perf_counter()

    unique_tests = tuple(dict.fromkeys(case[2] for case in CASES))
    suite = unittest.TestLoader().loadTestsFromNames(unique_tests)
    runner = unittest.TextTestRunner(verbosity=2, resultclass=RecordingResult)
    test_result = runner.run(suite)
    passed = set(test_result.passed)
    cases = [
        {
            "case_id": number,
            "name": name,
            "test_id": test_id,
            "status": "passed" if test_id in passed else "failed",
        }
        for number, name, test_id in CASES
    ]
    database_result = sqlite_check(database)
    database_passed = bool(
        database_result["source_unchanged"]
        and database_result["disposable_unchanged"]
        and database_result["integrity_check"] == "ok"
        and database_result["foreign_key_findings"] == 0
    )
    cases.append(
        {
            "case_id": 18,
            "name": "Source and disposable SQLite hashes unchanged",
            "test_id": "provider_free_read_only_sqlite_check",
            "status": "passed" if database_passed else "failed",
        }
    )
    completed = test_result.wasSuccessful() and database_passed
    elapsed = time.perf_counter() - started
    report_path = source / "JOB4_REPORT.md"
    lines = [
        "# Continuous corrections provider-free Job 4",
        "",
        f"- Cycle: `{CYCLE_ID}`",
        f"- Task: `{TASK_ID}`",
        f"- Status: `{'completed' if completed else 'failed'}`",
        "- Provider calls: `0`",
        f"- Elapsed seconds: `{elapsed:.3f}`",
        "",
        "## Cases",
        "",
    ]
    lines.extend(
        f"{case['case_id']}. **{case['status']}** - {case['name']} (`{case['test_id']}`)"
        for case in cases
    )
    lines.extend(
        [
            "",
            "## SQLite evidence",
            "",
            "```json",
            json.dumps(database_result, indent=2, sort_keys=True),
            "```",
            "",
            "No provider transport was constructed or called. No active route, story state, installed SillyTavern, deployment, remote, merge, or push effect occurred.",
            "",
        ]
    )
    report_path.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    result = {
        "schema_version": "cera.pro_review_job4_result.v1",
        "cycle_id": CYCLE_ID,
        "task_id": TASK_ID,
        "status": "completed" if completed else "failed",
        "report_relative_path": "source/JOB4_REPORT.md",
        "report_sha256": digest(report_path),
        "effects": {
            "provider_calls": 0,
            "story_database_writes": 0,
            "active_route_changes": 0,
            "deployment_remote_or_push_effects": 0,
        },
        "verification": [
            {
                "command": "provider-free 18-case corrections integration audit",
                "status": "passed" if completed else "failed",
                "summary": f"{sum(case['status'] == 'passed' for case in cases)}/18 cases passed in {elapsed:.3f}s.",
            },
            {
                "command": "source-and-copy-sqlite-hash-check",
                "status": "passed" if database_passed else "failed",
                "summary": "Source and disposable SQLite remained byte-identical and passed read-only integrity checks.",
            },
        ],
    }
    (source / "JOB4_RESULT.json").write_bytes(canonical_bytes(result) + b"\n")
    print(json.dumps(result, sort_keys=True))
    return 0 if completed else 1


if __name__ == "__main__":
    raise SystemExit(main())
