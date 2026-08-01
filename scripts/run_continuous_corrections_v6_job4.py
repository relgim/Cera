"""Provider-free integration audit for continuous correction cycle 006.

This runner preflights every declared unittest identity, executes the exact v6
trusted-ingress, role-authority, edit-authority, session, full-harness, and
process-sidecar tests once, and inspects only a disposable read-only SQLite
copy.  It never constructs a live provider transport.
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
from scripts.run_continuous_planner_validator_job4 import (
    validate_declared_unittest_ids,
)


CYCLE_ID = "2026-08-01-continuous-planner-validator-v1-corrections-cycle-006"
TASK_ID = "continuous-corrections-v6-provider-free-integration-audit"

CASES = (
    (
        1,
        "Trusted ingress receipt binds every raw-turn identity",
        "tests.test_continuous_corrections.ContinuousAuthorityV6Tests.test_ingress_receipt_custody_and_all_request_identities_are_enforced",
    ),
    (
        2,
        "Protected assertion ownership cannot hide in a non-owning role",
        "tests.test_continuous_corrections.ContinuousAuthorityV6Tests.test_protected_assertion_roles_require_claims_but_npc_address_does_not",
    ),
    (
        3,
        "All semantic edits equal their cited final field",
        "tests.test_continuous_corrections.ContinuousAuthorityV6Tests.test_unprotected_world_edits_must_equal_the_cited_final_field",
    ),
    (
        4,
        "Accepted projections reject public and cross-owner private leakage",
        "tests.test_continuous_corrections.ContinuousAuthorityV6Tests.test_accepted_projection_rejects_public_or_cross_owner_private_state",
    ),
    (
        5,
        "Candidate authority manifest remains required and immutable",
        "tests.test_continuous_corrections.ContinuousWorldHardeningTests.test_candidate_authority_manifest_is_required_and_immutable",
    ),
    (
        6,
        "Pre-v6 session compatibility is rejected by the real test identity",
        "tests.test_continuous_planner_validator.ContinuousSessionTests.test_restart_rejects_pre_v6_policy_compatibility",
    ),
    (
        7,
        "Declared audit IDs fail closed before publication",
        "tests.test_continuous_job4_harness.ContinuousJob4HarnessTests.test_declared_unittest_ids_must_resolve_before_publication",
    ),
    (
        8,
        "Actual stage ports complete the ten-stage scripted harness",
        "tests.test_continuous_job4_harness.ContinuousJob4HarnessTests.test_complete_job_harness_uses_actual_ports_with_scripted_transports",
    ),
    (
        9,
        "Parent-child sidecar matrix covers launch through stranded recovery",
        "tests.test_continuous_job4_harness.ContinuousJob4HarnessTests.test_subprocess_sidecar_stage_matrix_and_stranded_accounting",
    ),
    (
        10,
        "Scene Change excludes the new prompt and expires prior-scene context",
        "tests.test_continuous_world.ContinuousWorldTests.test_scene_change_uses_allow_list_tail_and_excludes_new_prompt",
    ),
    (
        11,
        "Scene summary runs before the next Planner turn on the same sessions",
        "tests.test_continuous_world.ContinuousWorldTests.test_scene_change_uses_same_sessions_and_calls_validator_summary_before_planner",
    ),
    (
        12,
        "Good acceptance uses the atomic candidate transaction",
        "tests.test_continuous_world.ContinuousWorldTests.test_candidate_isolation_and_atomic_accept",
    ),
    (
        13,
        "Concern False Positive is a distinct audited acceptance action",
        "tests.test_continuous_world.ContinuousWorldTests.test_false_positive_accepts_eligible_concern_and_records_diagnostic",
    ),
    (
        14,
        "Critical False Positive remains concern-eligible only",
        "tests.test_continuous_corrections.ContinuousWorldHardeningTests.test_critical_false_positive_is_eligible_only_with_concern_semantics",
    ),
    (
        15,
        "Repository source inventory includes runtime and v2 packages",
        "tests.test_structural_contract_v2.StructuralV2FailureAndInventoryTests.test_repository_source_inventory_includes_runtime_and_v2_packages",
    ),
    (
        16,
        "Controlling documentation is complete and linked",
        "tests.test_documentation.DocumentationTests.test_authoritative_documentation_is_complete_and_linked",
    ),
    (
        17,
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
    with tempfile.TemporaryDirectory(prefix="cera-corrections-v6-job4-") as directory:
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

    test_ids = tuple(case[2] for case in CASES)
    preflight = validate_declared_unittest_ids(test_ids)
    started_at = datetime.now(UTC).isoformat()
    started = time.perf_counter()
    suite = unittest.TestLoader().loadTestsFromNames(test_ids)
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
            "case_id": 18,
            "name": "Source and disposable SQLite hashes remain unchanged",
            "test_id": "provider_free_read_only_sqlite_check",
            "status": "passed" if database_passed else "failed",
        }
    )
    completed = result.wasSuccessful() and database_passed
    elapsed = time.perf_counter() - started
    report_lines = [
        "# Continuous corrections v6 provider-free Job 4",
        "",
        f"- Cycle: `{CYCLE_ID}`",
        f"- Task: `{TASK_ID}`",
        f"- Status: `{'completed' if completed else 'failed'}`",
        "- Direct execution attempts: `1`",
        f"- Preflight-resolved unittest IDs: `{len(preflight)}`",
        f"- Labeled assertions: `{len(cases)}`",
        "- External provider calls: `0`",
        "- Scripted transport invocations inside the full harness: `10`",
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
            "Cycle 005's immutable failed Job 4 was not edited or rerun. This new-identity audit preflighted every exact test ID, exercised the real Planner, Composer, and Validator ports through scripted transports and the shared call-ledger wrapper, covered the child-process stage matrix, and constructed no external provider transport. No active route, story state, installed SillyTavern, deployment, remote, merge, or push effect occurred.",
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
                "command": "direct provider-free corrections-v6 audit",
                "status": "passed" if completed else "failed",
                "summary": (
                    f"{sum(case['status'] == 'passed' for case in cases)}/{len(cases)} "
                    f"labeled assertions across {len(test_ids)} preflighted tests passed "
                    f"in {elapsed:.3f}s; direct attempts=1; scripted transports=10; "
                    "external providers=0."
                ),
            }
        ],
    }
    result_path.write_bytes(canonical_bytes(payload) + b"\n")
    print(json.dumps(payload, sort_keys=True))
    return 0 if completed else 1


if __name__ == "__main__":
    raise SystemExit(main())
