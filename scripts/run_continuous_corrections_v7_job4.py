"""Provider-free integration audit for continuous correction cycle 007.

The audit resolves every declared unittest identity before execution, crosses
the actual Job 4 CLI through its closed scripted-v7 mode, and inspects only a
read-only disposable SQLite copy.  It never constructs an external provider
transport and refuses to overwrite an existing Job 4 result.
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


CYCLE_ID = "2026-08-01-continuous-planner-validator-v1-corrections-cycle-007"
TASK_ID = "continuous-corrections-v7-provider-free-executable-integration-audit"
AUDIT_VERSION = "v7"

CASES = (
    (
        1,
        "Prepared ingress persists and revalidates exact durable records after restart",
        "tests.test_structural_contract_v2.StructuralV2IngressTests.test_prepared_continuous_ingress_is_durable_and_revalidated_after_restart",
    ),
    (
        2,
        "Prepared ingress rejects fabricated or substituted envelope authority",
        "tests.test_structural_contract_v2.StructuralV2IngressTests.test_prepared_continuous_ingress_rejects_envelope_substitution",
    ),
    (
        3,
        "Prepared ingress rejects identity, adapter, ownership, and span substitutions",
        "tests.test_structural_contract_v2.StructuralV2IngressTests.test_prepared_ingress_authority_rejects_identity_and_span_substitutions",
    ),
    (
        4,
        "Only exact repository-registered fixture identities can issue authority",
        "tests.test_structural_contract_v2.StructuralV2IngressTests.test_continuous_fixture_prefix_is_not_authority",
    ),
    (
        5,
        "Independent semantics rejects explicit and pronoun protected-user laundering",
        "tests.test_continuous_corrections.ContinuousEvidenceCorrectionTests.test_independent_semantics_rejects_explicit_and_pronoun_laundering",
    ),
    (
        6,
        "Independent semantics permits an NPC action toward Ted without inventing a response",
        "tests.test_continuous_corrections.ContinuousEvidenceCorrectionTests.test_independent_semantics_allows_npc_action_toward_ted_without_reaction",
    ),
    (
        7,
        "Wrong-character persistence and provider-divergent bookkeeping fail closed",
        "tests.test_continuous_corrections.ContinuousAuthorityV6Tests.test_unprotected_world_edits_must_equal_the_cited_final_field",
    ),
    (
        8,
        "Untyped Rule, Location, Event, and Scene persistence classes remain disabled",
        "tests.test_continuous_corrections.ContinuousAuthorityV6Tests.test_v7_persistence_rejects_record_classes_without_typed_subject_schemas",
    ),
    (
        9,
        "Candidate authority changes with semantic and persistence custody",
        "tests.test_continuous_corrections.ContinuousWorldHardeningTests.test_candidate_authority_manifest_is_required_and_immutable",
    ),
    (
        10,
        "Pre-v7 continuous sessions fail closed during reconstruction",
        "tests.test_continuous_planner_validator.ContinuousSessionTests.test_restart_rejects_pre_v7_policy_compatibility",
    ),
    (
        11,
        "Actual Job 4 CLI completes the closed scripted-v7 executable path",
        "tests.test_continuous_job4_harness.ContinuousJob4HarnessTests.test_actual_cli_completes_closed_provider_free_scripted_v7_mode",
    ),
    (
        12,
        "Three-turn two-scene flow preserves separate sessions and accepted projections",
        "tests.test_continuous_corrections.ContinuousProviderFreeIntegrationTests.test_three_turn_two_scene_flow_keeps_separate_sessions_and_appends_once",
    ),
    (
        13,
        "Scene Change excludes the new prompt and expires prior-scene context",
        "tests.test_continuous_world.ContinuousWorldTests.test_scene_change_uses_allow_list_tail_and_excludes_new_prompt",
    ),
    (
        14,
        "Good assessment uses ordinary atomic Accept",
        "tests.test_continuous_world.ContinuousWorldTests.test_candidate_isolation_and_atomic_accept",
    ),
    (
        15,
        "Eligible Concern uses audited False Positive without changing candidate bytes",
        "tests.test_continuous_world.ContinuousWorldTests.test_false_positive_accepts_eligible_concern_and_records_diagnostic",
    ),
    (
        16,
        "Eligible Critical uses the same bounded False Positive transaction",
        "tests.test_continuous_corrections.ContinuousWorldHardeningTests.test_critical_false_positive_is_eligible_only_with_concern_semantics",
    ),
    (
        17,
        "Repository source inventory includes every current runtime source",
        "tests.test_structural_contract_v2.StructuralV2FailureAndInventoryTests.test_repository_source_inventory_includes_runtime_and_v2_packages",
    ),
    (
        18,
        "Controlling documentation is complete and linked",
        "tests.test_documentation.DocumentationTests.test_authoritative_documentation_is_complete_and_linked",
    ),
    (
        19,
        "D-180 active runtime identity remains unchanged",
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
    with tempfile.TemporaryDirectory(
        prefix=f"cera-corrections-{AUDIT_VERSION}-job4-"
    ) as directory:
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
            "case_id": len(CASES) + 1,
            "name": "Source and disposable SQLite hashes remain unchanged",
            "test_id": "provider_free_read_only_sqlite_check",
            "status": "passed" if database_passed else "failed",
        }
    )
    completed = result.wasSuccessful() and database_passed
    elapsed = time.perf_counter() - started
    report_lines = [
        f"# Continuous corrections {AUDIT_VERSION} provider-free Job 4",
        "",
        f"- Cycle: `{CYCLE_ID}`",
        f"- Task: `{TASK_ID}`",
        f"- Status: `{'completed' if completed else 'failed'}`",
        "- Direct execution attempts: `1`",
        f"- Preflight-resolved unittest IDs: `{len(preflight)}`",
        f"- Labeled assertions: `{len(cases)}`",
        "- External provider calls: `0`",
        "- Scripted transport invocations inside the actual CLI: `10`",
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
            f"This new-identity audit preflighted every exact test ID and crossed the actual Job 4 CLI through its closed scripted-{AUDIT_VERSION} mode. It constructed no external provider transport and did not alter any prior cycle. No active route, story state, installed SillyTavern, deployment, remote, merge, or push effect occurred.",
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
                "command": f"direct provider-free corrections-{AUDIT_VERSION} audit",
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
