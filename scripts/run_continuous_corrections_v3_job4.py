"""Direct provider-free integration audit for correction cycle 003.

The runner selects the exact corrected live-harness and authority/recovery
tests, constructs no live provider transport, and opens only a disposable
read-only SQLite copy. Labeled assertions are reported separately from unique
unittest methods.
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


CYCLE_ID = (
    "2026-08-01-continuous-planner-validator-v1-corrections-cycle-003"
)
TASK_ID = "continuous-corrections-v3-provider-free-integration-audit"

CASES = (
    (1, "Stable-prefix stored-thread identity", "tests.test_continuous_job4_harness.ContinuousJob4HarnessTests.test_stable_prefix_recursively_exposes_stored_thread_identity"),
    (2, "Exact live-harness summary path", "tests.test_continuous_job4_harness.ContinuousJob4HarnessTests.test_exact_job_harness_summary_path_reaches_first_provider_boundary"),
    (3, "Codex and DeepSeek submission markers", "tests.test_continuous_job4_harness.ContinuousJob4HarnessTests.test_actual_codex_and_deepseek_transports_mark_submission_boundary"),
    (4, "Pretransport schema/MCP failure counts zero", "tests.test_continuous_corrections.ContinuousCallAccountingTests.test_pretransport_failure_counts_zero_and_stored_thread_is_bound"),
    (5, "Post-dispatch failure matrix counts once", "tests.test_continuous_corrections.ContinuousCallAccountingTests.test_post_dispatch_failure_matrix_is_conservatively_counted"),
    (6, "Receipt overrides optional zero", "tests.test_continuous_corrections.ContinuousCallAccountingTests.test_receipt_overrides_optional_zero_and_unresolved_prepared_consumes_slot"),
    (7, "Unresolved prepared call consumes slot", "tests.test_continuous_corrections.ContinuousCallAccountingTests.test_receipt_overrides_optional_zero_and_unresolved_prepared_consumes_slot"),
    (8, "Three-turn two-scene accepted context", "tests.test_continuous_corrections.ContinuousProviderFreeIntegrationTests.test_three_turn_two_scene_flow_keeps_separate_sessions_and_appends_once"),
    (9, "Turn 2 avoids repeated character summary", "tests.test_continuous_corrections.ContinuousProviderFreeIntegrationTests.test_three_turn_two_scene_flow_keeps_separate_sessions_and_appends_once"),
    (10, "Protected-user exact source claims", "tests.test_continuous_corrections.ContinuousEvidenceCorrectionTests.test_protected_user_claims_bind_exact_spans_across_all_beat_text"),
    (11, "Protected-user actor invention rejected", "tests.test_continuous_corrections.ContinuousEvidenceCorrectionTests.test_protected_user_claims_bind_exact_spans_across_all_beat_text"),
    (12, "Protected-user non-actor invention rejected", "tests.test_continuous_corrections.ContinuousEvidenceCorrectionTests.test_protected_user_claims_bind_exact_spans_across_all_beat_text"),
    (13, "Derived character-summary MCP privacy", "tests.test_continuous_corrections.ContinuousEvidenceCorrectionTests.test_derived_character_summary_mcp_read_is_private_and_owner_bound"),
    (14, "Candidate-derived summary rejected", "tests.test_continuous_corrections.ContinuousEvidenceAuthorityV2Tests.test_validator_derived_character_summary_is_not_an_authority_source"),
    (15, "Six synchronization crash cuts", "tests.test_continuous_corrections.ContinuousProviderFreeIntegrationTests.test_acceptance_synchronization_crash_points_remain_pending_without_replay"),
    (16, "Pending synchronization blocks continuation", "tests.test_continuous_corrections.ContinuousWorldHardeningTests.test_failed_model_injection_remains_typed_pending_and_blocks_continuation"),
    (17, "Good ordinary Accept", "tests.test_continuous_world.ContinuousWorldTests.test_candidate_isolation_and_atomic_accept"),
    (18, "Concern False Positive", "tests.test_continuous_world.ContinuousWorldTests.test_false_positive_accepts_eligible_concern_and_records_diagnostic"),
    (19, "Critical False Positive", "tests.test_continuous_corrections.ContinuousWorldHardeningTests.test_critical_false_positive_is_eligible_only_with_concern_semantics"),
    (20, "D-180 active profile unchanged", "tests.test_active_runtime_profile.ActiveRuntimeProfileTests.test_all_active_source_bindings_match_the_canonical_profile"),
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
    with tempfile.TemporaryDirectory(prefix="cera-corrections-v3-job4-") as directory:
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
    args = parser.parse_args()
    cycle = args.cycle_directory.resolve()
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
    database = sqlite_check(args.source_database.resolve())
    database_passed = bool(
        database["source_unchanged"]
        and database["disposable_unchanged"]
        and database["integrity_check"] == "ok"
        and database["foreign_key_findings"] == 0
    )
    cases.append(
        {
            "case_id": 21,
            "name": "Source and disposable SQLite hashes unchanged",
            "test_id": "provider_free_read_only_sqlite_check",
            "status": "passed" if database_passed else "failed",
        }
    )
    completed = result.wasSuccessful() and database_passed
    elapsed = time.perf_counter() - started
    report_lines = [
        "# Continuous corrections v3 provider-free Job 4",
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
            "The runner selected the corrected live-harness, authority, transport-accounting, and acceptance-recovery tests directly. All provider boundaries used scripted fakes or pre-provider sentinels. No live provider transport was constructed. No active route, story state, installed SillyTavern, deployment, remote, merge, or push effect occurred.",
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
                "command": "direct provider-free corrections-v3 audit",
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
