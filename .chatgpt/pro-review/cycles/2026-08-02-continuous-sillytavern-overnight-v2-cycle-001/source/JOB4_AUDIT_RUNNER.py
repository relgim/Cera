"""Cycle 22 provider-free readiness audit over the frozen CERA checkpoint.

This cycle-local wrapper adds the exact Queue 0027 audit matrix to the
repository-owned V10 scripted Job 4 runner.  The underlying runner retains
root transaction, terminal-evidence V5, lineage, publication, and recovery
custody.  This wrapper makes no provider call and changes no product source.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import socket
import subprocess
import sys
from typing import Any
import unittest
import zipfile


ROOT = Path(__file__).resolve().parents[5]
for import_root in (ROOT / "src", ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from cera.serialization import canonical_sha256
from scripts import run_continuous_planner_validator_job4 as base


CYCLE_ID = "2026-08-02-continuous-sillytavern-overnight-v2-cycle-001"
TASK_ID = "continuous-sillytavern-v2-provider-free-readiness-audit"
CHECKPOINT_SHA = "13c5c5ae664061a42763dab293bc0b249c44fb7e"
EXECUTION_SOURCE_SHA = "85b1bceca202f33d0ddd38cfb8f1e02fbea03a06"
EXECUTION_SOURCE_TREE = "2639732331487598dd11e3b22e96124065403b5c"
MANUAL_EXECUTION_ID = (
    "6477acd5f9d11d54ca2c1f2e3fc27464654bc7359bf827c84745352222912b65"
)
CHECKPOINT = (
    ROOT
    / ".chatgpt"
    / "pro-review"
    / "checkpoints"
    / "2026-08-02-continuous-sillytavern-overnight-v2-001"
)
PRIOR_CYCLE = (
    ROOT
    / ".chatgpt"
    / "pro-review"
    / "cycles"
    / "2026-08-02-continuous-sillytavern-two-run-v1-cycle-001"
)

EXPECTED_CHECKPOINT_HASHES = {
    "PROGRESSION_1_RESULT.md": "bb81ab8b23ed380c0c60158993f109a89401f12d6d066a6ab6baf699286e1a06",
    "PROGRESSION_2_RESULT.md": "b76856f62b292be485235e6b1306f6dfb0addad741bdd8721f5734c58a96ac90",
    "PROGRESSION_3_RESULT.md": "9362fd633dda0d05ffc592570066b8f51b3dce0def47b43a0b31b8e3141b0294",
    "PROGRESSION_3_READINESS/READINESS_MANIFEST.json": "ba93ffba0179aa98a5c785bfeae30e893e3a26bb4d1ee5c001d5462eca4eea5d",
    "PROGRESSION_3_READINESS/READINESS_RESULT.json": "901ddfa99779e30c90f3ceb92247af842e227b5ef574cb9a70612d871ce8cff1",
    "PROGRESSION_3_READINESS/runs/r1.zip": "9736ac452e69642f06aebd33321346767f5810d389c24aafb9253316aa5ffc2e",
    "PROGRESSION_3_READINESS/runs/r2.zip": "59a331d132cbce50122919a06087211f0eb81954a6ca39edf77eb157c3cbb298",
}

EXPECTED_QUEUE_HASHES = {
    ".chatgpt/pro-review/continuous-work/CURRENT.md": "cb1ac12ff1797da1aa2905051a34f11643b1cc1dca142a7084f4ce80ba26d3df",
    ".chatgpt/pro-review/continuous-work/WORK_QUEUE_0027.md": "9fbe141db0cb86d6dc74d00a4ecfa36fb96c5b4ba9efd85b574a753a96639f61",
    ".chatgpt/operations/PENDING_WORK_QUEUE_0024.md": "3cd6e35ad378d5eb756bb87b529b945febcb64f842eaaeb20a92bbd721cf9379",
    ".chatgpt/operations/CODEX_OVERNIGHT_COMMAND_0024.md": "50eb6ba5e9533316b7f463a05c6a1975daf2ef7edcd9d2d0d6dd7df63ac704ad",
}

SELECTED_TESTS = (
    (
        "Parent and child recompute complete execution authority before transport",
        "tests.test_sillytavern_continuous_v3.ContinuousV3CampaignStateTests.test_single_run_recomputes_identity_before_run_root_or_provider_setup",
    ),
    (
        "Campaign rechecks execution identity before every child dispatch",
        "tests.test_sillytavern_continuous_v3.ContinuousV3CampaignStateTests.test_campaign_recomputes_identity_before_each_child_dispatch",
    ),
    (
        "Synthetic Validator severity and hashes cannot enable Accept",
        "tests.test_sillytavern_continuous_v3.ContinuousSillyTavernV3Tests.test_synthetic_hash_and_severity_cannot_enable_accept",
    ),
    (
        "Stale exact-review binding fails before acceptance",
        "tests.test_sillytavern_continuous_v3.ContinuousSillyTavernV3Tests.test_stale_review_record_fails_before_acceptance",
    ),
    (
        "HTTP review and Accept traverse the exact ten-stage route",
        "tests.test_sillytavern_continuous_v3_integration.ContinuousV3HttpIntegrationTests.test_scripted_v3_crosses_exact_http_review_and_ten_stage_route",
    ),
    (
        "Immutable Run 001 role conflict fails once without repair",
        "tests.test_continuous_role_conflict_regression.ContinuousRoleConflictRegressionTests.test_invalid_post_provider_fixture_fails_once_without_repair_or_acceptance",
    ),
    (
        "Corrected five-beat Turn 1 passes the strict role path",
        "tests.test_continuous_role_conflict_regression.ContinuousRoleConflictRegressionTests.test_corrected_prompt_schema_and_split_sequence_pass_strict_path",
    ),
    (
        "Every physical role thread terminalizes before transport closes",
        "tests.test_continuous_role_conflict_regression.ContinuousRoleConflictRegressionTests.test_every_physical_thread_terminalizes_before_transport_close",
    ),
    (
        "Partial child recovery is immutable and chooses the next identity",
        "tests.test_continuous_role_conflict_regression.ContinuousRoleConflictRegressionTests.test_partial_child_result_recovery_is_exact_and_uses_next_identity",
    ),
    (
        "Unresolved prepared calls debit conservatively while pretransport failures do not",
        "tests.test_continuous_role_conflict_regression.ContinuousRoleConflictRegressionTests.test_unresolved_prepared_call_is_consumed_but_proven_pretransport_is_not",
    ),
    (
        "Same-execution recovery preserves the pass and restart boundary",
        "tests.test_continuous_role_conflict_regression.ContinuousRoleConflictRegressionTests.test_same_execution_recovery_preserves_one_pass_and_restart_boundary",
    ),
    (
        "Ordinary typed turns use exact review and strict Accept",
        "tests.test_sillytavern_continuous_manual.ContinuousManualHttpTests.test_arbitrary_three_turn_two_scene_route_uses_exact_review_and_accept",
    ),
    (
        "Current-scene cast is durable and Scene Change cast is explicit",
        "tests.test_sillytavern_continuous_manual.ContinuousManualHttpTests.test_current_scene_cast_is_durable_and_new_scene_cast_is_explicit",
    ),
    (
        "Restart blocks stale Accept and retains exact Decline",
        "tests.test_sillytavern_continuous_manual.ContinuousManualHttpTests.test_restart_blocks_stale_accept_but_allows_exact_decline",
    ),
    (
        "Route rejects model, profile, control, and non-loopback substitution",
        "tests.test_sillytavern_continuous_manual.ContinuousManualHttpTests.test_route_rejects_model_profile_controls_and_non_loopback_bind",
    ),
    (
        "Manual service start, health, restart, stop, and isolation are exact",
        "tests.test_sillytavern_continuous_manual.ContinuousManualLifecycleTests.test_real_process_start_health_restart_and_isolation",
    ),
    (
        "Two provider-free readiness runs pass with controlled restart and 20 local stages",
        "tests.test_sillytavern_continuous_manual.ContinuousManualReadinessTests.test_production_shaped_two_run_route_passes_locally_and_resets",
    ),
    (
        "Readiness manifest preserves Run 001 debit and next-unused V2 identity",
        "tests.test_sillytavern_continuous_manual.ContinuousManualReadinessTests.test_manifest_preserves_run001_debit_and_fresh_v2_identities",
    ),
    (
        "Active D-180 source bindings remain exact",
        "tests.test_active_runtime_profile.ActiveRuntimeProfileTests.test_all_active_source_bindings_match_the_canonical_profile",
    ),
    (
        "Controlling documentation remains complete and linked",
        "tests.test_documentation.DocumentationTests.test_authoritative_documentation_is_complete_and_linked",
    ),
    (
        "Repository source inventory includes every runtime source",
        "tests.test_structural_contract_v2.StructuralV2FailureAndInventoryTests.test_repository_source_inventory_includes_runtime_and_v2_packages",
    ),
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git(*arguments: str) -> str:
    return subprocess.run(
        ("git", "-C", str(ROOT), *arguments),
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout.strip()


def _assert_port(host: str, port: int, *, expected_open: bool) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.4)
        is_open = probe.connect_ex((host, port)) == 0
    if is_open is not expected_open:
        state = "open" if is_open else "closed"
        raise RuntimeError(f"port {port} is unexpectedly {state}")


def _validate_static_bindings(cycle: Path) -> dict[str, Any]:
    if _git("rev-parse", "HEAD") != CHECKPOINT_SHA:
        raise RuntimeError("checkpoint Git SHA changed")
    if _git("rev-parse", f"{EXECUTION_SOURCE_SHA}^{{tree}}") != EXECUTION_SOURCE_TREE:
        raise RuntimeError("execution-source tree changed")
    expected_delta = {
        "A\t.chatgpt/pro-review/checkpoints/2026-08-02-continuous-sillytavern-overnight-v2-001/PROGRESSION_3_READINESS/READINESS_MANIFEST.json",
        "A\t.chatgpt/pro-review/checkpoints/2026-08-02-continuous-sillytavern-overnight-v2-001/PROGRESSION_3_READINESS/READINESS_RESULT.json",
        "A\t.chatgpt/pro-review/checkpoints/2026-08-02-continuous-sillytavern-overnight-v2-001/PROGRESSION_3_READINESS/runs/r1.zip",
        "A\t.chatgpt/pro-review/checkpoints/2026-08-02-continuous-sillytavern-overnight-v2-001/PROGRESSION_3_READINESS/runs/r2.zip",
        "A\t.chatgpt/pro-review/checkpoints/2026-08-02-continuous-sillytavern-overnight-v2-001/PROGRESSION_3_RESULT.md",
        "A\t.chatgpt/pro-review/checkpoints/2026-08-02-continuous-sillytavern-overnight-v2-001/notifications/PROGRESSION_3_MESSAGE.md",
    }
    observed_delta = set(
        _git("diff", "--name-status", EXECUTION_SOURCE_SHA, CHECKPOINT_SHA).splitlines()
    )
    if observed_delta != expected_delta:
        raise RuntimeError("execution-source to evidence-freeze delta changed")

    for relative, expected in {**EXPECTED_CHECKPOINT_HASHES, **EXPECTED_QUEUE_HASHES}.items():
        root = CHECKPOINT if relative in EXPECTED_CHECKPOINT_HASHES else ROOT
        if _sha256(root / relative) != expected:
            raise RuntimeError(f"frozen binding changed: {relative}")

    manifest_path = CHECKPOINT / "PROGRESSION_3_READINESS" / "READINESS_MANIFEST.json"
    result_path = CHECKPOINT / "PROGRESSION_3_READINESS" / "READINESS_RESULT.json"
    readiness_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest_self = readiness_manifest.pop("readiness_manifest_sha256")
    if canonical_sha256(readiness_manifest) != manifest_self:
        raise RuntimeError("readiness manifest self-hash changed")
    readiness_manifest["readiness_manifest_sha256"] = manifest_self
    readiness_result = json.loads(result_path.read_text(encoding="utf-8"))
    result_self = readiness_result.pop("readiness_result_sha256")
    if canonical_sha256(readiness_result) != result_self:
        raise RuntimeError("readiness result self-hash changed")
    readiness_result["readiness_result_sha256"] = result_self
    if (
        manifest_self != "6b96cc2eb78d49ba0724c9342daffc25f94c661297e6c6504ed4006ab99a42a4"
        or result_self != "039b8735cda449fd56dee75520a3f613b09e97ec07dbda64154f1285683f48a4"
        or readiness_result.get("status") != "passed"
        or readiness_result.get("consecutive_passes") != 2
        or readiness_result.get("controlled_restart_between_passes") is not True
        or readiness_result.get("scripted_transport_invocations") != 20
        or readiness_result.get("external_provider_calls") != 0
        or readiness_result.get("live_dispatch_authorized") is not False
        or readiness_result.get("manual_route_execution_identity_sha256")
        != MANUAL_EXECUTION_ID
    ):
        raise RuntimeError("frozen readiness semantics changed")
    for archive_name in ("r1.zip", "r2.zip"):
        with zipfile.ZipFile(
            CHECKPOINT / "PROGRESSION_3_READINESS" / "runs" / archive_name, "r"
        ) as archive:
            if archive.testzip() is not None or not archive.namelist():
                raise RuntimeError(f"readiness archive is invalid: {archive_name}")

    manifest = json.loads((cycle / "CYCLE_MANIFEST.json").read_text(encoding="utf-8"))
    jobs = [(item["task_id"], item["sha256"]) for item in manifest["jobs_1_3"]]
    if (
        manifest.get("cycle_id") != CYCLE_ID
        or manifest.get("cycle_sequence") != 22
        or manifest.get("checkpoint", {}).get("git_sha") != CHECKPOINT_SHA
        or manifest.get("source_snapshot", {}).get("baseline_git_sha")
        != CHECKPOINT_SHA
        or manifest.get("job4", {}).get("task_id") != TASK_ID
        or jobs
        != [
            ("continuous-sillytavern-child-execution-authority-and-review-projection-v2", EXPECTED_CHECKPOINT_HASHES["PROGRESSION_1_RESULT.md"]),
            ("continuous-sillytavern-role-conflict-and-transport-live-regression-v2", EXPECTED_CHECKPOINT_HASHES["PROGRESSION_2_RESULT.md"]),
            ("continuous-sillytavern-general-manual-route-and-two-run-readiness-v2", EXPECTED_CHECKPOINT_HASHES["PROGRESSION_3_RESULT.md"]),
        ]
    ):
        raise RuntimeError("Cycle 22 manifest binding changed")
    prior = manifest.get("prior_cycle", {})
    if (
        prior.get("cycle_id")
        != "2026-08-02-continuous-sillytavern-two-run-v1-cycle-001"
        or prior.get("cycle_sequence") != 21
        or prior.get("job4_completion_receipt_sha256")
        != "cd56dcdab07bf1562116cf0c9242691047ba84fc42e950cb1bfd06df09c0cb74"
        or prior.get("response_consumption_receipt_sha256")
        != "4973cca7344eeea1b6ebbd3fe31883399f856b7c608b4ac1ea7f10ec3a42939e"
    ):
        raise RuntimeError("Cycle 21 predecessor chain changed")
    if (
        _sha256(PRIOR_CYCLE / "accepted" / "PRO_RESPONSE.md")
        != "c20c0ad2726908e8015cd2b88f0fcb645d272e6ac4c755e1e636b5835aaa4e11"
        or _sha256(PRIOR_CYCLE / "receipts" / "RESPONSE_CONSUMED.json")
        != "4973cca7344eeea1b6ebbd3fe31883399f856b7c608b4ac1ea7f10ec3a42939e"
    ):
        raise RuntimeError("Cycle 21 accepted response or receipt changed")

    run_root = (
        ROOT
        / "runtime"
        / "evaluation"
        / "2026-08-02-continuous-sillytavern-two-run-v1"
    )
    run = run_root / "runs" / "2026-08-02-continuous-sillytavern-two-run-v1-run-001"
    if (
        _sha256(run / "RUN_RESULT.json")
        != "f1617de44f31c9e0408eb3d75c422dc7a9ef33447cca2fe63fa26915e0950c22"
        or _sha256(run / "PROVIDER_CALL_LEDGER.jsonl")
        != "dfd2572e208104614fa9e5f2ed0e42246a7d4d445b201e906bbcac6842fc54fd"
        or _sha256(run_root / "EXECUTION_MANIFEST.json")
        != "3dfad6b541df37c1044348a5a05e308cb2e43cd5c9faf97bb4987c6cffc5c54d"
    ):
        raise RuntimeError("immutable Run 001 evidence changed")
    ledger = [
        json.loads(line)
        for line in (run / "PROVIDER_CALL_LEDGER.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    invoked = {event["call_id"] for event in ledger if event.get("state") == "transport_invoked"}
    if len(invoked) != 1:
        raise RuntimeError("immutable Run 001 debit is not exactly one")
    return {
        "checkpoint_git_sha": CHECKPOINT_SHA,
        "execution_source_git_sha": EXECUTION_SOURCE_SHA,
        "execution_source_tree_sha": EXECUTION_SOURCE_TREE,
        "manual_route_execution_identity_sha256": MANUAL_EXECUTION_ID,
        "readiness_manifest_sha256": EXPECTED_CHECKPOINT_HASHES[
            "PROGRESSION_3_READINESS/READINESS_MANIFEST.json"
        ],
        "readiness_result_sha256": EXPECTED_CHECKPOINT_HASHES[
            "PROGRESSION_3_READINESS/READINESS_RESULT.json"
        ],
        "immutable_run001_codex_family_calls": 1,
        "immutable_run001_deepseek_calls": 0,
        "remaining_codex_family_calls": 799,
        "remaining_deepseek_calls": 800,
    }


class RecordingResult(unittest.TextTestResult):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.passed: set[str] = set()

    def addSuccess(self, test: unittest.TestCase) -> None:
        super().addSuccess(test)
        self.passed.add(test.id())


def _run_scope_audit(cycle: Path) -> dict[str, Any]:
    cases: list[dict[str, Any]] = []
    try:
        bindings = _validate_static_bindings(cycle)
        cases.append(
            {
                "case_id": 1,
                "name": "Checkpoint, cycle, predecessor, readiness, Run 001, and budget bindings are exact",
                "test_id": "cycle22_static_authority_and_evidence_bindings",
                "status": "passed",
            }
        )
    except BaseException as exc:
        return {
            "status": "failed",
            "cases": [
                {
                    "case_id": 1,
                    "name": "Checkpoint, cycle, predecessor, readiness, Run 001, and budget bindings are exact",
                    "test_id": "cycle22_static_authority_and_evidence_bindings",
                    "status": "failed",
                    "error_type": type(exc).__name__,
                }
            ],
            "bindings": {},
            "selected_test_count": len(SELECTED_TESTS),
            "external_provider_calls": 0,
            "readiness_local_invocations": 0,
        }

    test_ids = tuple(test_id for _name, test_id in SELECTED_TESTS)
    base.validate_declared_unittest_ids(test_ids)
    suite = unittest.TestLoader().loadTestsFromNames(test_ids)
    outcome = unittest.TextTestRunner(verbosity=2, resultclass=RecordingResult).run(suite)
    passed = outcome.passed
    failed_ids = {test.id() for test, _detail in outcome.failures + outcome.errors}
    for number, (name, test_id) in enumerate(SELECTED_TESTS, start=2):
        cases.append(
            {
                "case_id": number,
                "name": name,
                "test_id": test_id,
                "status": "passed" if test_id in passed else "failed",
                **({"error_type": "TestFailure"} if test_id in failed_ids else {}),
            }
        )
    isolation_ok = True
    try:
        _assert_port("127.0.0.1", 5101, expected_open=True)
        _assert_port("127.0.0.1", 8000, expected_open=True)
        _assert_port("127.0.0.1", 5113, expected_open=False)
        _assert_port("127.0.0.1", 5114, expected_open=False)
        active_processes = tuple((ROOT / "runtime" / "manual").rglob("ACTIVE_PROCESS.json"))
        if active_processes:
            raise RuntimeError("manual route left an active-process record")
    except BaseException:
        isolation_ok = False
    cases.append(
        {
            "case_id": len(cases) + 1,
            "name": "Qualification listeners close while existing loopback services and route remain unchanged",
            "test_id": "cycle22_post_audit_service_and_orphan_gate",
            "status": "passed" if isolation_ok else "failed",
        }
    )
    status = "passed" if outcome.wasSuccessful() and isolation_ok else "failed"
    return {
        "status": status,
        "cases": cases,
        "bindings": bindings,
        "selected_test_count": len(SELECTED_TESTS),
        "selected_tests_passed": len(passed),
        "external_provider_calls": 0,
        "readiness_local_invocations": 20,
        "base_scripted_local_invocations": 10,
        "retry_count": 0,
        "fallback_count": 0,
        "hidden_repair_count": 0,
    }


_original_execute_scripted_job4 = base.execute_scripted_job4
_original_build_report = base.build_report
_original_build_canonical_result = base.build_canonical_job4_result


def _execute_scripted_job4_with_scope_audit(**kwargs: Any):
    audit = _run_scope_audit(kwargs["cycle"])
    kwargs["result"]["cycle22_scope_audit"] = audit
    if audit["status"] != "passed":
        raise RuntimeError("Cycle 22 provider-free scope audit failed")
    return _original_execute_scripted_job4(**kwargs)


def _build_report_with_scope_audit(
    result: dict[str, Any], *, task_id: str | None = None
) -> str:
    report = _original_build_report(result, task_id=task_id)
    audit = result.get("cycle22_scope_audit", {})
    lines = ["", "## Cycle 22 scope audit", ""]
    for case in audit.get("cases", []):
        lines.append(
            f"{case['case_id']}. **{case['status']}** - {case['name']} "
            f"(`{case['test_id']}`)"
        )
    lines.extend(
        (
            "",
            f"- Selected tests: `{audit.get('selected_tests_passed', 0)}/{audit.get('selected_test_count', 0)}`.",
            f"- Readiness local invocations: `{audit.get('readiness_local_invocations', 0)}`.",
            f"- Base scripted local invocations: `{audit.get('base_scripted_local_invocations', 0)}`.",
            f"- External provider calls: `{audit.get('external_provider_calls', 0)}`.",
            f"- Frozen bindings: `{json.dumps(audit.get('bindings', {}), sort_keys=True, separators=(',', ':'))}`.",
            "",
        )
    )
    return report + "\n" + "\n".join(lines)


def _build_canonical_result_with_scope_audit(
    result: dict[str, Any], *, task_id: str, report_sha256: str
) -> dict[str, Any]:
    canonical = _original_build_canonical_result(
        result, task_id=task_id, report_sha256=report_sha256
    )
    audit = result.get("cycle22_scope_audit", {})
    canonical["verification"].append(
        {
            "command": "continuous-sillytavern-v2-provider-free-readiness-audit",
            "status": "passed" if audit.get("status") == "passed" else "failed",
            "summary": (
                f"selected_tests={audit.get('selected_tests_passed', 0)}/"
                f"{audit.get('selected_test_count', 0)}; readiness_local_invocations="
                f"{audit.get('readiness_local_invocations', 0)}; "
                f"base_scripted_local_invocations={audit.get('base_scripted_local_invocations', 0)}; "
                f"external_provider_calls={audit.get('external_provider_calls', 0)}"
            ),
        }
    )
    return canonical


def main() -> int:
    base.execute_scripted_job4 = _execute_scripted_job4_with_scope_audit
    base.build_report = _build_report_with_scope_audit
    base.build_canonical_job4_result = _build_canonical_result_with_scope_audit
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
