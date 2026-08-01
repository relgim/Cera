"""Direct provider-free integration audit for correction cycle 004.

The runner selects the exact v4 authority, realization, session-snapshot,
submission-boundary, and shared-coordinator tests. It constructs no live
provider transport and opens only a disposable read-only SQLite copy.
"""

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

from cera.serialization import canonical_bytes


CYCLE_ID = "2026-08-01-continuous-planner-validator-v1-corrections-cycle-004"
TASK_ID = "continuous-corrections-v4-provider-free-integration-audit"

CASES = (
    (
        1,
        "Attribution-aware protected-user source projection",
        "tests.test_continuous_corrections.ContinuousAuthorityV4Tests.test_source_projector_is_attribution_aware_and_keeps_doorway_questions",
    ),
    (
        2,
        "NPC and unattributed quotations cannot authorize Ted",
        "tests.test_continuous_corrections.ContinuousAuthorityV4Tests.test_source_projector_is_attribution_aware_and_keeps_doorway_questions",
    ),
    (
        3,
        "Every protected-user realization has an exact Composer span",
        "tests.test_continuous_corrections.ContinuousAuthorityV4Tests.test_composer_must_annotate_every_exact_protected_source_occurrence",
    ),
    (
        4,
        "Missing, duplicate, or wrong realization spans fail",
        "tests.test_continuous_corrections.ContinuousAuthorityV4Tests.test_composer_must_annotate_every_exact_protected_source_occurrence",
    ),
    (
        5,
        "Public accepted projection excludes private state",
        "tests.test_continuous_corrections.ContinuousAuthorityV4Tests.test_accepted_projection_rejects_public_or_cross_owner_private_state",
    ),
    (
        6,
        "Owner projection rejects cross-owner private state",
        "tests.test_continuous_corrections.ContinuousAuthorityV4Tests.test_accepted_projection_rejects_public_or_cross_owner_private_state",
    ),
    (
        7,
        "Accepted Planner snapshot proof is immutable",
        "tests.test_continuous_corrections.ContinuousAuthorityV4Tests.test_acceptance_snapshot_is_immutable_after_current_pointer_advances",
    ),
    (
        8,
        "Codex preflight is zero-call and thread_run is one-call",
        "tests.test_continuous_corrections.ContinuousCallAccountingTests.test_true_submission_marker_distinguishes_preflight_from_thread_run",
    ),
    (
        9,
        "Three-turn two-scene flow uses ten shared coordinator stages",
        "tests.test_continuous_corrections.ContinuousProviderFreeIntegrationTests.test_three_turn_two_scene_flow_keeps_separate_sessions_and_appends_once",
    ),
    (
        10,
        "Scene Change uses shared summary and continuation coordinator",
        "tests.test_continuous_world.ContinuousWorldTests.test_scene_change_uses_same_sessions_and_calls_validator_summary_before_planner",
    ),
    (
        11,
        "Exact Job 4 harness reaches the shared first provider boundary",
        "tests.test_continuous_job4_harness.ContinuousJob4HarnessTests.test_exact_job_harness_summary_path_reaches_first_provider_boundary",
    ),
    (
        12,
        "D-180 active profile remains unchanged",
        "tests.test_active_runtime_profile.ActiveRuntimeProfileTests.test_all_active_source_bindings_match_the_canonical_profile",
    ),
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


def sqlite_check(source: Path) -> dict[str, object]:
    source_before = digest(source)
    with tempfile.TemporaryDirectory(prefix="cera-corrections-v4-job4-") as directory:
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
        "source_unchanged": source_before == source_after,
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
    report_path = source / "JOB4_REPORT.md"
    result_path = source / "JOB4_RESULT.json"
    if report_path.exists() or result_path.exists():
        raise SystemExit("refusing to overwrite Job 4 evidence")

    started_at = datetime.now(UTC).isoformat()
    started = time.perf_counter()
    unique_tests = tuple(dict.fromkeys(case[2] for case in CASES))
    suite = unittest.TestLoader().loadTestsFromNames(unique_tests)
    result = unittest.TextTestRunner(
        verbosity=2, resultclass=RecordingResult
    ).run(suite)
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
    database = sqlite_check(arguments.source_database.resolve())
    database_passed = bool(
        database["source_unchanged"]
        and database["disposable_unchanged"]
        and database["integrity_check"] == "ok"
        and database["foreign_key_findings"] == 0
    )
    cases.append(
        {
            "case_id": 13,
            "name": "Source and disposable SQLite hashes unchanged",
            "test_id": "provider_free_read_only_sqlite_check",
            "status": "passed" if database_passed else "failed",
        }
    )
    completed = result.wasSuccessful() and database_passed
    elapsed = time.perf_counter() - started
    report_lines = [
        "# Continuous corrections v4 provider-free Job 4",
        "",
        f"- Cycle: `{CYCLE_ID}`",
        f"- Task: `{TASK_ID}`",
        f"- Status: `{'completed' if completed else 'failed'}`",
        "- Direct execution attempts: `1`",
        f"- Unique unittest methods: `{len(unique_tests)}`",
        f"- Labeled assertions: `{len(cases)}`",
        "- Provider calls: `0`",
        f"- Started UTC: `{started_at}`",
        f"- Elapsed seconds: `{elapsed:.3f}`",
        "",
        "## Labeled assertions",
        "",
    ]
    report_lines.extend(
        f"{case['case_id']}. **{case['status']}** - {case['name']} (`{case['test_id']}`)"
        for case in cases
    )
    report_lines.extend(
        [
            "",
            "## SQLite evidence",
            "",
            "```json",
            json.dumps(database, indent=2, sort_keys=True),
            "```",
            "",
            "The runner selected the corrected authority, realization, immutable-snapshot, true-submission, and shared-coordinator tests directly. All provider boundaries used scripted fakes or pre-provider sentinels. No live provider transport was constructed. No active route, story state, installed SillyTavern, deployment, remote, merge, or push effect occurred.",
            "",
        ]
    )
    report_path.write_text("\n".join(report_lines), encoding="utf-8", newline="\n")
    payload = {
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
                "command": "direct provider-free corrections-v4 audit",
                "status": "passed" if completed else "failed",
                "summary": (
                    f"{sum(case['status'] == 'passed' for case in cases)}/{len(cases)} "
                    f"labeled assertions across {len(unique_tests)} unique tests passed "
                    f"in {elapsed:.3f}s; direct attempts=1."
                ),
            }
        ],
    }
    result_path.write_bytes(canonical_bytes(payload) + b"\n")
    print(json.dumps(payload, sort_keys=True))
    return 0 if completed else 1


if __name__ == "__main__":
    raise SystemExit(main())
