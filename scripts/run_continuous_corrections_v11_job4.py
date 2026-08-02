"""Provider-free lifecycle-evidence audit for continuous correction Cycle 011."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
from pathlib import Path
import sys
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ROOT / "src"
for import_root in (SOURCE_ROOT, ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from cera.continuous.job4_terminal import (
    ContinuousJob4CapabilityCustody,
    ContinuousJob4PostconditionsV1,
    ContinuousJob4TerminalEvidenceV2,
)
from cera.serialization import canonical_bytes
from scripts.run_continuous_corrections_v10_job4 import (
    RecordingResult,
    digest,
    inspect_active_profile,
    sqlite_check,
)
from scripts.run_continuous_planner_validator_job4 import (
    build_canonical_job4_result,
    build_report,
    validate_declared_unittest_ids,
)


CYCLE_ID = "2026-08-01-continuous-planner-validator-v1-corrections-cycle-011"
TASK_ID = "continuous-corrections-v11-provider-free-lifecycle-evidence-audit"
AUDIT_VERSION = "v11"
SCRIPTED_COMPLETION_TEST = (
    "tests.test_pro_review_bridge.ProReviewRepositoryCycleTests."
    "test_28d_actual_scripted_v8_canary_completes_job4_transport"
)

CASES = (
    (
        1,
        "Missing source data terminalizes before provider work",
        "tests.test_continuous_job4_harness.ContinuousJob4HarnessTests."
        "test_missing_source_database_terminalizes_before_any_provider_work",
    ),
    (
        2,
        "Frozen terminal bytes republish without semantic re-entry",
        "tests.test_continuous_job4_harness.ContinuousJob4HarnessTests."
        "test_frozen_terminal_bytes_republish_without_semantic_reentry",
    ),
    (
        3,
        "Started restart terminalizes rather than rerunning",
        "tests.test_continuous_job4_harness.ContinuousJob4HarnessTests."
        "test_started_transaction_restart_terminalizes_instead_of_rerunning",
    ),
    (
        4,
        "Report construction failure still commits terminal evidence",
        "tests.test_continuous_job4_harness.ContinuousJob4HarnessTests."
        "test_report_construction_failure_still_commits_failed_terminal_result",
    ),
    (
        5,
        "Publication cuts recover exact frozen bytes",
        "tests.test_continuous_job4_harness.ContinuousJob4HarnessTests."
        "test_publication_cut_recovers_exact_frozen_bytes",
    ),
    (
        6,
        "Every canonical effect preserves exact nonzero evidence",
        "tests.test_continuous_job4_harness.ContinuousJob4HarnessTests."
        "test_terminal_effect_evidence_preserves_each_nonzero_canonical_effect",
    ),
    (
        7,
        "Counted capability ports preserve nonzero effects",
        "tests.test_continuous_job4_harness.ContinuousJob4HarnessTests."
        "test_counted_capability_ports_preserve_nonzero_terminal_effects",
    ),
    (
        8,
        "Denied capability ports fail closed with typed custody",
        "tests.test_continuous_job4_harness.ContinuousJob4HarnessTests."
        "test_capability_custody_structurally_denies_every_effect_port",
    ),
    (
        9,
        "Terminal archival invalidates resume and accepted ancestry",
        "tests.test_continuous_job4_harness.ContinuousJob4HarnessTests."
        "test_terminal_archive_invalidates_resume_and_accepted_ancestry",
    ),
    (
        10,
        "Live backend archival uses supported active-thread selection",
        "tests.test_codex_stored_thread_session.CodexStoredThreadSessionTests."
        "test_openai_backend_materializes_non_ephemeral_threads_and_archives_leaf",
    ),
    (
        11,
        "Actual CLI failure matrix completes and recovers",
        "tests.test_pro_review_bridge.ProReviewRepositoryCycleTests."
        "test_28d2_actual_cli_failure_matrix_completes_and_recovers",
    ),
    (
        12,
        "Terminal evidence is copied, reparsed, and revalidated",
        "tests.test_pro_review_bridge.ProReviewRepositoryCycleTests."
        "test_28i_terminal_evidence_is_required_copied_and_revalidated",
    ),
    (
        13,
        "Successful scripted ten-stage CLI crosses real completion",
        SCRIPTED_COMPLETION_TEST,
    ),
    (
        14,
        "Repository source inventory includes every runtime source",
        "tests.test_structural_contract_v2.StructuralV2FailureAndInventoryTests."
        "test_repository_source_inventory_includes_runtime_and_v2_packages",
    ),
    (
        15,
        "Controlling documentation is complete and linked",
        "tests.test_documentation.DocumentationTests."
        "test_authoritative_documentation_is_complete_and_linked",
    ),
    (
        16,
        "D-180 active runtime identity remains exact",
        "tests.test_active_runtime_profile.ActiveRuntimeProfileTests."
        "test_all_active_source_bindings_match_the_canonical_profile",
    ),
)


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
    terminal_path = source / "JOB4_TERMINAL_EVIDENCE.json"
    if any(path.exists() for path in (report_path, result_path, terminal_path)):
        raise SystemExit("refusing to overwrite Job 4 evidence")

    test_ids = tuple(case[2] for case in CASES)
    preflight = validate_declared_unittest_ids(test_ids)
    profile_before, before_status = inspect_active_profile()
    started_at = datetime.now(UTC).isoformat()
    started = time.perf_counter()
    suite = unittest.TestLoader().loadTestsFromNames(test_ids)
    test_result = unittest.TextTestRunner(
        verbosity=2,
        resultclass=RecordingResult,
    ).run(suite)
    profile_after, after_status = inspect_active_profile()
    database = sqlite_check(arguments.source_database.resolve())
    elapsed = time.perf_counter() - started

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
        test_result.wasSuccessful()
        and database_passed
        and profile_status == "verified"
    )
    postconditions = ContinuousJob4PostconditionsV1(
        execution_mode="provider_free_scripted_v8",
        source_database_sha256_before=database["source_sha256_before"],
        source_database_sha256_after=database["source_sha256_after"],
        disposable_database_sha256_before=database["disposable_sha256_before"],
        disposable_database_sha256_after=database["disposable_sha256_after"],
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
    capability_ledger = ContinuousJob4CapabilityCustody().evidence
    terminal = ContinuousJob4TerminalEvidenceV2.build(
        execution_status="completed" if execution_completed else "failed",
        provider_calls=0,
        capability_ledger=capability_ledger,
        postconditions=postconditions,
    )
    effects = terminal.effect_evidence.canonical_effects
    detail = {
        "schema_version": "cera.continuous_corrections_v11_job4_detail.v1",
        "cycle_id": CYCLE_ID,
        "task_id": TASK_ID,
        "audit_version": AUDIT_VERSION,
        "status": terminal.status,
        "execution_status": terminal.execution_status,
        "execution_mode": postconditions.execution_mode,
        "provider_calls": effects["provider_calls"],
        "scripted_transport_invocations": (
            postconditions.scripted_transport_invocations
        ),
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
        "capability_ledger": capability_ledger.to_dict(),
        "capability_ledger_sha256": capability_ledger.sha256,
        "terminal_evidence": terminal.to_dict(),
        "terminal_evidence_sha256": terminal.sha256,
        "calls": [],
        "turns": [],
        "failure": (
            None
            if terminal.status == "completed"
            else {"stage": "provider_free_lifecycle_evidence_audit"}
        ),
    }
    report = build_report(detail, task_id=TASK_ID)
    report += "\n## Audit assertions\n\n"
    report += "\n".join(
        f"{case['case_id']}. **{case['status']}** - {case['name']} "
        f"(`{case['test_id']}`)"
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
    terminal_path.write_bytes(canonical_bytes(terminal.to_dict()))
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
