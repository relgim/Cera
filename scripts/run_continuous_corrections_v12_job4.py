"""Provider-free correction audit with root terminal-transaction custody.

Unlike the historical V10/V11 audit runners, this executable never writes a
canonical Job 4 artifact directly.  It starts the shared one-shot transaction
before audit preflight and routes every terminal byte through the same frozen
publication path used by the continuous canary.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
from pathlib import Path
import sys
import time
from typing import Any
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
from cera.continuous.job4_transaction import (
    ContinuousJob4TerminalTransactionV1,
    ContinuousJob4TransactionError,
)
from cera.serialization import canonical_sha256
from scripts.run_continuous_corrections_v10_job4 import (
    RecordingResult,
    inspect_active_profile,
    sqlite_check,
)
from scripts.run_continuous_planner_validator_job4 import (
    _apply_terminal_evidence,
    _record_terminal_failure,
    build_report,
    freeze_terminal_publication,
    git_head,
    validate_declared_unittest_ids,
)


AUDIT_VERSION = "v12"
EXECUTION_MODE = "provider_free_scripted_v8"
SCRIPTED_COMPLETION_TEST = (
    "tests.test_pro_review_bridge.ProReviewRepositoryCycleTests."
    "test_28d_actual_scripted_v8_canary_completes_job4_transport"
)

# The default audit registry remains explicit and repository-owned.  Later V12
# progressions may extend it before Cycle 012 Job 4 is executed.
CASES = (
    (
        1,
        "Exact correction runner owns every failure through one transaction",
        "tests.test_pro_review_bridge.ProReviewRepositoryCycleTests."
        "test_28j_v12_exact_correction_runner_failure_matrix",
    ),
    (
        2,
        "Frozen terminal bytes republish without semantic re-entry",
        "tests.test_continuous_job4_harness.ContinuousJob4HarnessTests."
        "test_frozen_terminal_bytes_republish_without_semantic_reentry",
    ),
    (
        3,
        "Started restart terminalizes instead of rerunning",
        "tests.test_continuous_job4_harness.ContinuousJob4HarnessTests."
        "test_started_transaction_restart_terminalizes_instead_of_rerunning",
    ),
    (
        4,
        "Publication cuts recover exact frozen bytes",
        "tests.test_continuous_job4_harness.ContinuousJob4HarnessTests."
        "test_publication_cut_recovers_exact_frozen_bytes",
    ),
    (
        5,
        "Terminal evidence survives repository completion and recovery",
        "tests.test_pro_review_bridge.ProReviewRepositoryCycleTests."
        "test_28i_terminal_evidence_is_required_copied_and_revalidated",
    ),
    (
        6,
        "Successful scripted canary crosses the real completion path",
        SCRIPTED_COMPLETION_TEST,
    ),
    (
        7,
        "Repository source inventory includes every runtime source",
        "tests.test_structural_contract_v2.StructuralV2FailureAndInventoryTests."
        "test_repository_source_inventory_includes_runtime_and_v2_packages",
    ),
    (
        8,
        "Controlling documentation is complete and linked",
        "tests.test_documentation.DocumentationTests."
        "test_authoritative_documentation_is_complete_and_linked",
    ),
    (
        9,
        "D-180 active runtime identity remains exact",
        "tests.test_active_runtime_profile.ActiveRuntimeProfileTests."
        "test_all_active_source_bindings_match_the_canonical_profile",
    ),
)

_TEST_FIXTURE_CASES = (
    (
        1,
        "Bounded exact-runner transaction fixture",
        "tests.test_continuous_job4_harness.ContinuousJob4HarnessTests."
        "test_frozen_terminal_bytes_republish_without_semantic_reentry",
    ),
)
TEST_FIXTURE_SHA256 = canonical_sha256(
    {
        "schema_version": "cera.continuous_corrections_v12_test_fixture.v1",
        "cases": _TEST_FIXTURE_CASES,
    }
)

FAILPOINTS = frozenset(
    {
        "unittest_preflight",
        "active_profile_before",
        "unittest_loading",
        "unittest_execution",
        "unittest_result_collection",
        "active_profile_after",
        "sqlite_inspection",
        "terminal_evidence_construction",
        "terminal_evidence_serialization",
        "report_construction",
        "canonical_projection",
        "detail_serialization",
        "result_serialization",
        "report_write",
        "result_write",
        "terminal_artifact_write",
        "publication_commit_marker",
    }
)


class _AuditFaultInjector:
    """One-shot test seam for the exact provider-free audit runner."""

    def __init__(self, selected: str | None) -> None:
        if selected is not None and selected not in FAILPOINTS:
            raise ValueError("provider-free audit failpoint is unsupported")
        self.selected = selected
        self.consumed = False

    def hit(self, name: str) -> None:
        if self.selected == name and not self.consumed:
            self.consumed = True
            raise RuntimeError(
                f"provider-free correction audit injected failure: {name}"
            )


def _audit_report(result: dict[str, Any], task_id: str) -> str:
    report = build_report(result, task_id=task_id)
    cases = result.get("audit_cases", [])
    report += "\n## Audit assertions\n\n"
    report += "\n".join(
        f"{case['case_id']}. **{case['status']}** - {case['name']} "
        f"(`{case['test_id']}`)"
        for case in cases
    )
    report += "\n\n## SQLite evidence\n\n```json\n"
    report += json.dumps(
        result.get("sqlite_evidence"), indent=2, sort_keys=True
    )
    report += "\n```\n\n"
    report += f"Started UTC: `{result.get('audit_started_at')}`  \n"
    report += f"Elapsed seconds: `{result.get('audit_elapsed_seconds', 0):.3f}`  \n"
    report += (
        "Preflight-resolved tests: "
        f"`{result.get('preflight_resolved_tests', 0)}`\n"
    )
    return report


def _validate_cycle_authority(
    cycle: Path,
    *,
    expected_cycle_id: str,
    expected_task_id: str,
    expected_authorization_sha256: str,
    expected_checkpoint_sha: str,
) -> None:
    if git_head(ROOT) != expected_checkpoint_sha:
        raise ValueError("repository HEAD does not match the frozen checkpoint")
    manifest = json.loads(
        (cycle / "CYCLE_MANIFEST.json").read_text(encoding="utf-8")
    )
    if (
        manifest.get("cycle_id") != expected_cycle_id
        or manifest.get("job4", {}).get("task_id") != expected_task_id
        or manifest.get("job4", {}).get("authorization_record_sha256")
        != expected_authorization_sha256
    ):
        raise ValueError("Job 4 cycle identity changed")
    if not (cycle / "receipts" / "TRIGGER_SENT.json").is_file():
        raise ValueError("Job 4 cannot start before the Pro trigger receipt")


def _empty_database_evidence() -> dict[str, object]:
    return {
        "source_sha256_before": None,
        "source_sha256_after": None,
        "disposable_sha256_before": None,
        "disposable_sha256_after": None,
        "integrity_check": None,
        "foreign_key_findings": None,
    }


def _postconditions(
    *,
    database: dict[str, object],
    profile_before: str | None,
    profile_after: str | None,
    profile_status: str,
    scripted_completed: bool,
    scripted_transport_invocations: int,
) -> ContinuousJob4PostconditionsV1:
    return ContinuousJob4PostconditionsV1(
        execution_mode=EXECUTION_MODE,
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
        call_ledger_dispatches=scripted_transport_invocations,
        scripted_transport_invocations=scripted_transport_invocations,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cycle-directory", type=Path, required=True)
    parser.add_argument("--source-database", type=Path, required=True)
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--expected-checkpoint-sha", required=True)
    parser.add_argument("--expected-cycle-id", required=True)
    parser.add_argument("--expected-task-id", required=True)
    parser.add_argument("--expected-authorization-sha256", required=True)
    parser.add_argument(
        "--provider-free-test-failpoint", choices=sorted(FAILPOINTS)
    )
    parser.add_argument("--provider-free-test-fixture-sha256")
    args = parser.parse_args()

    fixture_requested = args.provider_free_test_fixture_sha256 is not None
    if fixture_requested and (
        args.provider_free_test_fixture_sha256 != TEST_FIXTURE_SHA256
    ):
        parser.error("provider-free test fixture identity changed")
    if args.provider_free_test_failpoint is not None and not fixture_requested:
        parser.error("provider-free failpoints require the frozen test fixture")

    cycle = args.cycle_directory.resolve()
    runtime_root = args.runtime_root.resolve()

    # This is deliberately the first repository mutation and precedes all
    # authority reads, unittest work, profile inspection, and SQLite work.
    try:
        transaction = ContinuousJob4TerminalTransactionV1.begin(
            cycle_directory=cycle,
            cycle_id=args.expected_cycle_id,
            task_id=args.expected_task_id,
            authorization_sha256=args.expected_authorization_sha256,
            runtime_root=runtime_root,
        )
    except ContinuousJob4TransactionError as exc:
        raise SystemExit(str(exc)) from exc
    if transaction.state == "committed":
        raise SystemExit("Job 4 terminal publication is already committed")
    if transaction.state == "frozen":
        transaction.publish_frozen()
        return 0 if transaction.frozen_result().get("status") == "completed" else 1

    recovery_terminalization = not transaction.is_new
    fault = _AuditFaultInjector(args.provider_free_test_failpoint)
    capability_ledger = ContinuousJob4CapabilityCustody().evidence
    database = _empty_database_evidence()
    profile_before: str | None = None
    profile_after: str | None = None
    profile_status = "failed"
    preflight: dict[str, int] = {}
    passed: set[str] = set()
    selected_cases = _TEST_FIXTURE_CASES if fixture_requested else CASES
    test_result: unittest.TestResult | None = None
    started_at = datetime.now(UTC).isoformat()
    started = time.perf_counter()
    failure: BaseException | None = None
    failure_stage: str | None = None

    if recovery_terminalization:
        failure = RuntimeError("started Job 4 transaction recovered without re-entry")
        failure_stage = "restart_recovery"
    else:
        try:
            _validate_cycle_authority(
                cycle,
                expected_cycle_id=args.expected_cycle_id,
                expected_task_id=args.expected_task_id,
                expected_authorization_sha256=args.expected_authorization_sha256,
                expected_checkpoint_sha=args.expected_checkpoint_sha,
            )
            test_ids = tuple(case[2] for case in selected_cases)
            fault.hit("unittest_preflight")
            preflight = validate_declared_unittest_ids(test_ids)

            fault.hit("active_profile_before")
            profile_before, before_status = inspect_active_profile()
            if before_status != "verified":
                raise RuntimeError("active profile inspection before execution failed")

            fault.hit("unittest_loading")
            suite = unittest.TestLoader().loadTestsFromNames(test_ids)
            fault.hit("unittest_execution")
            test_result = unittest.TextTestRunner(
                verbosity=2,
                resultclass=RecordingResult,
            ).run(suite)
            fault.hit("unittest_result_collection")
            passed = set(getattr(test_result, "passed", set()))
            if not test_result.wasSuccessful():
                raise RuntimeError("provider-free audit unittest execution failed")

            fault.hit("active_profile_after")
            profile_after, after_status = inspect_active_profile()
            if after_status != "verified":
                raise RuntimeError("active profile inspection after execution failed")
            profile_status = (
                "verified"
                if profile_before == profile_after
                else "failed"
            )
            if profile_status != "verified":
                raise RuntimeError("active profile changed during audit execution")

            fault.hit("sqlite_inspection")
            database = sqlite_check(args.source_database.resolve())
        except BaseException as exc:
            failure = exc
            failure_stage = (
                fault.selected
                if fault.consumed and fault.selected is not None
                else "provider_free_audit_execution"
            )

    database_passed = bool(
        database["source_sha256_before"] is not None
        and database["source_sha256_before"] == database["source_sha256_after"]
        and database["disposable_sha256_before"]
        == database["disposable_sha256_after"]
        and database["integrity_check"] == "ok"
        and database["foreign_key_findings"] == 0
    )
    audit_cases = [
        {
            "case_id": number,
            "name": name,
            "test_id": test_id,
            "status": "passed" if test_id in passed else "failed",
        }
        for number, name, test_id in selected_cases
    ]
    audit_cases.append(
        {
            "case_id": len(selected_cases) + 1,
            "name": "Source and disposable SQLite evidence remains exact",
            "test_id": "provider_free_read_only_sqlite_check",
            "status": "passed" if database_passed else "failed",
        }
    )
    scripted_completed = (
        (SCRIPTED_COMPLETION_TEST in passed)
        or (fixture_requested and failure is None and database_passed)
    )
    scripted_transport_invocations = (
        10 if SCRIPTED_COMPLETION_TEST in passed else 0
    )
    execution_completed = bool(
        failure is None
        and test_result is not None
        and test_result.wasSuccessful()
        and database_passed
        and profile_status == "verified"
    )
    postconditions = _postconditions(
        database=database,
        profile_before=profile_before,
        profile_after=profile_after,
        profile_status=profile_status,
        scripted_completed=scripted_completed,
        scripted_transport_invocations=scripted_transport_invocations,
    )
    try:
        fault.hit("terminal_evidence_construction")
        terminal = ContinuousJob4TerminalEvidenceV2.build(
            execution_status="completed" if execution_completed else "failed",
            provider_calls=0,
            capability_ledger=capability_ledger,
            postconditions=postconditions,
        )
    except BaseException as exc:
        if failure is None:
            failure = exc
            failure_stage = "terminal_evidence_construction"
        terminal = ContinuousJob4TerminalEvidenceV2.build(
            execution_status="failed",
            provider_calls=0,
            capability_ledger=capability_ledger,
            postconditions=postconditions,
        )

    detail: dict[str, Any] = {
        "schema_version": "cera.continuous_corrections_v12_job4_detail.v1",
        "cycle_id": args.expected_cycle_id,
        "task_id": args.expected_task_id,
        "authorization_sha256": args.expected_authorization_sha256,
        "audit_version": AUDIT_VERSION,
        "execution_mode": EXECUTION_MODE,
        "provider_calls": 0,
        "scripted_transport_invocations": (
            postconditions.scripted_transport_invocations
        ),
        "story_database_writes": 0,
        "active_route_changes": postconditions.active_route_changes,
        "deployment_remote_or_push_effects": 0,
        "source_database_unchanged": database_passed,
        "copy_database_unchanged": database_passed,
        "active_route_unchanged": postconditions.active_route_changes == 0,
        "thread_archival": dict(postconditions.thread_archival),
        "capability_ledger": capability_ledger.to_dict(),
        "capability_ledger_sha256": capability_ledger.sha256,
        "calls": [],
        "turns": [],
        "failure": None,
        "audit_cases": audit_cases,
        "sqlite_evidence": database,
        "audit_started_at": started_at,
        "audit_elapsed_seconds": time.perf_counter() - started,
        "preflight_resolved_tests": len(preflight),
        "root_terminal_transaction": {
            "schema_version": ContinuousJob4TerminalTransactionV1.SCHEMA_VERSION,
            "recovery_terminalization": recovery_terminalization,
            "semantic_work_repeated": False,
        },
    }
    _apply_terminal_evidence(detail, terminal=terminal)
    if failure is not None:
        _record_terminal_failure(
            detail,
            failure,
            stage=failure_stage or "provider_free_audit_execution",
        )

    try:
        canonical_result = freeze_terminal_publication(
            transaction,
            detail,
            task_id=args.expected_task_id,
            recovery_terminalization=recovery_terminalization,
            fault_injector=fault,
            report_builder=_audit_report,
        )
    except (ContinuousJob4TransactionError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(canonical_result, sort_keys=True))
    return 0 if canonical_result["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
