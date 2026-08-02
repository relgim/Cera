"""Run the identity-bound two-pass Continuous V3 SillyTavern qualification.

The parent campaign launches every immutable run in a new Python process.  A
single run owns a fresh database copy, continuous world, branch, provider
sessions, adapter, and loopback HTTP server.  There is no retry or fallback.
"""

from __future__ import annotations

import argparse
from contextlib import ExitStack
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
from threading import Thread
import time
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from cera.continuous.call_ledger import ContinuousProviderCallLedger
from cera.continuous.codex_stored import CodexContinuousStoredSessionPort
from cera.continuous.prompting import PLANNER_STABLE_INSTRUCTIONS, VALIDATOR_STABLE_INSTRUCTIONS
from cera.continuous.sessions import (
    ContinuousSessionCoordinator,
    ContinuousSessionRole,
    assert_separate_role_sessions,
)
from cera.continuous.thread_lineage import ContinuousThreadLineageLedger
from cera.continuous.world import ContinuousWorldStore
from cera.creator_review import CreatorReviewAction
from cera.providers.codex_worker import _BASE_INSTRUCTIONS_BY_ROLE
from cera.reasoner_session import OpenAICodexStoredThreadBackend
from cera.serialization import bytes_sha256, canonical_bytes, canonical_sha256
from cera.sillytavern.campaign import (
    CONTINUOUS_V3_CALL_SCHEDULE,
    CONTINUOUS_V3_RUN_IDENTITIES,
    ContinuousV3TwoRunCampaign,
)
from cera.sillytavern.continuous_test import (
    CONTINUOUS_V3_TEST_FIXTURE,
    ContinuousSillyTavernTestAdapter,
)
from cera.sillytavern.models import CERA_CONTINUOUS_V3_TEST_MODEL
from cera.sillytavern.server import CeraSillyTavernServerConfig, build_server

from run_continuous_planner_validator_job4 import (
    BRANCH_ID,
    ROOT,
    WORLD_ID,
    JobHarness,
    _terminalize_known_thread_sessions,
    compatibility,
    seed_world,
)


CAMPAIGN_ID = "2026-08-02-continuous-sillytavern-two-run-v1"
CYCLE_ID = "2026-08-02-continuous-sillytavern-two-run-v1-cycle-001"
TASK_ID = "continuous-sillytavern-two-consecutive-runs-live-qualification-v1"
PROFILE_ID = "cera.sillytavern.continuous_v3_test.v1"
FIXED_PORT = 5113


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(canonical_bytes(payload) + b"\n")
    temporary.replace(path)


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON object required: {path}")
    return value


def validate_authority(
    cycle: Path,
    *,
    expected_checkpoint_sha: str,
    expected_authorization_sha256: str,
) -> dict[str, Any]:
    manifest = read_json(cycle / "CYCLE_MANIFEST.json")
    if (
        manifest.get("cycle_id") != CYCLE_ID
        or manifest.get("cycle_sequence") != 21
        or manifest.get("checkpoint", {}).get("git_sha") != expected_checkpoint_sha
        or manifest.get("job4", {}).get("task_id") != TASK_ID
        or manifest.get("job4", {}).get("authorization_record_sha256")
        != expected_authorization_sha256
    ):
        raise ValueError("continuous SillyTavern campaign authority changed")
    authorization = cycle / "source" / "JOB4_AUTHORIZATION.json"
    if not authorization.is_file() or bytes_sha256(authorization.read_bytes()) != expected_authorization_sha256:
        raise ValueError("continuous SillyTavern campaign authorization bytes changed")
    return manifest


def execution_manifest(source_db: Path, *, checkpoint_sha: str) -> dict[str, Any]:
    source_files = (
        "src/cera/sillytavern/models.py",
        "src/cera/sillytavern/server.py",
        "src/cera/sillytavern/continuous_test.py",
        "src/cera/sillytavern/campaign.py",
        "src/cera/continuous/runtime.py",
        "src/cera/continuous/provider.py",
        "src/cera/continuous/prompting.py",
        "src/cera/continuous/packets.py",
        "scripts/run_continuous_planner_validator_job4.py",
        "scripts/run_sillytavern_continuous_v3_campaign.py",
        "docs/implementation/CONTINUOUS_LEAN_CONTEXT_SHORT_CANARY_V3_SPEC.md",
    )
    files = {
        relative: bytes_sha256((ROOT / relative).read_bytes())
        for relative in source_files
    }
    payload: dict[str, Any] = {
        "schema_version": "cera.sillytavern_continuous_v3_execution_manifest.v1",
        "campaign_id": CAMPAIGN_ID,
        "checkpoint_git_sha": checkpoint_sha,
        "profile_id": PROFILE_ID,
        "host": "127.0.0.1",
        "port": FIXED_PORT,
        "model": CERA_CONTINUOUS_V3_TEST_MODEL,
        "planner": {"model": "gpt-5.6-sol", "effort": "medium", "fast": False},
        "composer": {"model": "deepseek-v4-flash", "thinking": False},
        "validator": {"model": "gpt-5.6-terra", "effort": "high", "fast": False},
        "fixture": list(CONTINUOUS_V3_TEST_FIXTURE),
        "call_schedule": list(CONTINUOUS_V3_CALL_SCHEDULE),
        "source_database_sha256": bytes_sha256(source_db.read_bytes()),
        "source_files": files,
    }
    payload["execution_identity_sha256"] = canonical_sha256(payload)
    return payload


def http_json(base: str, method: str, path: str, payload: dict[str, Any] | None = None) -> tuple[int, dict[str, Any]]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(
        base + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urlopen(request, timeout=1800) as response:
            return response.status, json.load(response)
    except HTTPError as exc:
        return exc.code, json.load(exc)


def sqlite_checks(path: Path) -> dict[str, Any]:
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        return {
            "integrity": connection.execute("PRAGMA integrity_check").fetchone()[0],
            "foreign_key_findings": len(connection.execute("PRAGMA foreign_key_check").fetchall()),
        }
    finally:
        connection.close()


def run_single(args: argparse.Namespace) -> int:
    cycle = args.cycle_directory.resolve()
    source_db = args.source_database.resolve()
    run_root = args.runtime_root.resolve()
    if args.run_id not in CONTINUOUS_V3_RUN_IDENTITIES:
        raise ValueError("unknown campaign run identity")
    validate_authority(
        cycle,
        expected_checkpoint_sha=args.expected_checkpoint_sha,
        expected_authorization_sha256=args.expected_authorization_sha256,
    )
    if run_root.exists():
        raise FileExistsError("refusing to overwrite immutable run evidence")
    run_root.mkdir(parents=True)
    result_path = run_root / "RUN_RESULT.json"
    copied_db = run_root / "hanezawa_disposable.sqlite3"
    shutil.copy2(source_db, copied_db)
    source_before = bytes_sha256(source_db.read_bytes())
    copy_before = bytes_sha256(copied_db.read_bytes())
    checks_before = sqlite_checks(copied_db)
    world = ContinuousWorldStore(run_root / "worlds")
    seed_world(world, ROOT)
    lifecycle_root = run_root / "provider_workspaces"
    lifecycle_root.mkdir()
    call_ledger = ContinuousProviderCallLedger(
        run_root / "PROVIDER_CALL_LEDGER.jsonl", maximum_calls=10
    )
    result: dict[str, Any] = {
        "schema_version": "cera.sillytavern_continuous_v3_run_result.v1",
        "run_id": args.run_id,
        "status": "running",
        "source_database_sha256_before": source_before,
        "disposable_database_sha256_before": copy_before,
        "sqlite_before": checks_before,
        "http": [],
    }
    write_json(run_root / "EXECUTION_MANIFEST.json", read_json(args.execution_manifest))
    planner_session = validator_session = None
    lineage = ContinuousThreadLineageLedger()
    harness = None
    server = None
    server_thread = None
    try:
        from openai_codex import Codex, CodexConfig

        with ExitStack() as stack:
            codex = stack.enter_context(
                Codex(CodexConfig(config_overrides=("mcp_servers={}",), env={}))
            )
            account = codex.account()
            if account.account is None:
                raise RuntimeError("ChatGPT Codex session is unavailable")
            planner_backend = OpenAICodexStoredThreadBackend(
                codex=codex,
                model="gpt-5.6-sol",
                cwd=str(lifecycle_root),
                base_instructions=(
                    _BASE_INSTRUCTIONS_BY_ROLE["scene_reasoner"]
                    + "\n\n"
                    + PLANNER_STABLE_INSTRUCTIONS
                ),
                service_name=f"cera_st_v3_planner_{args.run_id[-3:]}",
            )
            validator_backend = OpenAICodexStoredThreadBackend(
                codex=codex,
                model="gpt-5.6-terra",
                cwd=str(lifecycle_root),
                base_instructions=(
                    _BASE_INSTRUCTIONS_BY_ROLE["scene_realization_verifier"]
                    + "\n\n"
                    + VALIDATOR_STABLE_INSTRUCTIONS
                ),
                service_name=f"cera_st_v3_validator_{args.run_id[-3:]}",
            )
            planner_session = ContinuousSessionCoordinator(
                compatibility(world, ContinuousSessionRole.PLANNER),
                CodexContinuousStoredSessionPort(planner_backend),
                base_instructions=planner_backend.base_instructions,
            )
            validator_session = ContinuousSessionCoordinator(
                compatibility(world, ContinuousSessionRole.VALIDATOR),
                CodexContinuousStoredSessionPort(validator_backend),
                base_instructions=validator_backend.base_instructions,
            )
            planner_session.attach_thread_lineage(lineage, purpose="primary_planner")
            validator_session.attach_thread_lineage(lineage, purpose="primary_validator")
            planner_handle = planner_session.ensure_session().provider_thread_id
            validator_handle = validator_session.ensure_session().provider_thread_id
            assert_separate_role_sessions(planner_session, validator_session)
            harness = JobHarness(
                source_root=ROOT,
                cycle=cycle,
                world=world,
                planner_session=planner_session,
                validator_session=validator_session,
                planner_handle=planner_handle,
                validator_handle=validator_handle,
                lifecycle_root=lifecycle_root,
                call_ledger=call_ledger,
            )
            adapter = ContinuousSillyTavernTestAdapter(
                session_id=args.run_id,
                prepare_turn=harness.prepare_http_turn,
                accept_turn=harness.accept_http_turn,
                route_identity={
                    "profile_id": PROFILE_ID,
                    "run_id": args.run_id,
                    "execution_identity_sha256": args.execution_identity_sha256,
                },
            )
            server = build_server(
                adapter,
                CeraSillyTavernServerConfig(
                    host="127.0.0.1",
                    port=FIXED_PORT,
                    model=CERA_CONTINUOUS_V3_TEST_MODEL,
                    service="cera-sillytavern-continuous-v3-test",
                ),
            )
            server_thread = Thread(target=server.serve_forever, daemon=True)
            server_thread.start()
            base = f"http://127.0.0.1:{FIXED_PORT}"
            for path in ("/health", "/v1/models"):
                status, body = http_json(base, "GET", path)
                result["http"].append({"method": "GET", "path": path, "status": status, "body": body})
                if status != 200:
                    raise RuntimeError(f"campaign HTTP preflight failed: {path}")
            for turn_number, message in enumerate(CONTINUOUS_V3_TEST_FIXTURE, 1):
                status, reply = http_json(
                    base,
                    "POST",
                    "/v1/chat/completions",
                    {
                        "model": CERA_CONTINUOUS_V3_TEST_MODEL,
                        "stream": False,
                        "cera_session_id": args.run_id,
                        "cera_scene_change": turn_number == 3,
                        "messages": [{"role": "user", "content": message}],
                    },
                )
                result["http"].append({"method": "POST", "path": "/v1/chat/completions", "turn": turn_number, "status": status, "body": reply})
                if status != 200:
                    raise RuntimeError(f"turn {turn_number} HTTP completion failed")
                review_id = reply["cera"]["provisional_review_id"]
                status, review = http_json(base, "GET", f"/v1/cera/reviews/{review_id}")
                result["http"].append({"method": "GET", "path": f"/v1/cera/reviews/{review_id}", "turn": turn_number, "status": status, "body": review})
                if status != 200 or review.get("state") != "review_ready" or review.get("accept_enabled") is not True:
                    raise RuntimeError(f"turn {turn_number} review is not strictly accept eligible")
                status, decision = http_json(
                    base,
                    "POST",
                    f"/v1/cera/reviews/{review_id}/decision",
                    {"action": CreatorReviewAction.ACCEPT.value},
                )
                result["http"].append({"method": "POST", "path": f"/v1/cera/reviews/{review_id}/decision", "turn": turn_number, "status": status, "body": decision})
                if status != 200 or decision.get("status") != "accepted":
                    raise RuntimeError(f"turn {turn_number} strict acceptance failed")
            labels = tuple(record["label"] for record in harness.call_records)
            if labels != CONTINUOUS_V3_CALL_SCHEDULE or harness.provider_calls != 10:
                raise RuntimeError("campaign run did not use the exact ten-call schedule")
            if tuple(value["planner_packet_kind"] for value in harness.http_turn_results) != (
                "first_turn_initialization",
                "lean_continuous_continuation",
                "scene_change",
            ):
                raise RuntimeError("campaign V3 packet progression changed")
            if harness.planner_session.unsynchronized_accepted_turn_ids:
                raise RuntimeError("campaign accepted Planner context is unsynchronized")
            result["turns"] = harness.http_turn_results
            result["calls"] = harness.call_records
            result["scene_summary"] = {
                "present": harness.scene_change_envelope is not None,
                "accepted_pair_count": len(harness.accepted_pairs[:2]),
                "turn_3_excluded": (
                    harness.scene_change_envelope is not None
                    and CONTINUOUS_V3_TEST_FIXTURE[2]
                    not in harness.scene_change_envelope.previous_scene_summary.shortest_complete_summary
                ),
            }
            result["status"] = "passed"
    except BaseException as exc:
        result.update(
            {
                "status": "failed",
                "error_type": type(exc).__name__,
                "error_message": str(exc),
            }
        )
    finally:
        if server is not None:
            server.shutdown()
            server.server_close()
        if server_thread is not None:
            server_thread.join(timeout=5)
        if planner_session is not None or validator_session is not None:
            try:
                archived, evidence, lineage_receipt = _terminalize_known_thread_sessions(
                    thread_lineage=lineage,
                    planner_session=planner_session,
                    validator_session=validator_session,
                    reason=("sillytavern_v3_run_complete" if result["status"] == "passed" else "sillytavern_v3_run_failed"),
                )
                result["thread_archival"] = archived
                result["thread_archival_evidence"] = evidence
                result["thread_lineage"] = lineage_receipt.to_dict()
                if result["status"] == "passed" and not all(archived.values()):
                    result["status"] = "failed"
                    result["error_type"] = "StateConflictError"
                    result["error_message"] = "physical thread archival failed"
            except BaseException as exc:
                result["status"] = "failed"
                result["cleanup_error_type"] = type(exc).__name__
                result["cleanup_error_message"] = str(exc)
        result["provider_calls"] = call_ledger.dispatched_call_count
        result["source_database_sha256_after"] = bytes_sha256(source_db.read_bytes())
        result["disposable_database_sha256_after"] = bytes_sha256(copied_db.read_bytes())
        result["sqlite_after"] = sqlite_checks(copied_db)
        if (
            result["source_database_sha256_after"] != source_before
            or result["sqlite_after"] != checks_before
        ):
            result["status"] = "failed"
            result["database_drift"] = True
        write_json(result_path, result)
    return 0 if result["status"] == "passed" else 1


def run_campaign(args: argparse.Namespace) -> int:
    cycle = args.cycle_directory.resolve()
    source_db = args.source_database.resolve()
    campaign_root = args.runtime_root.resolve()
    if campaign_root.exists():
        raise FileExistsError("refusing to overwrite immutable campaign evidence")
    validate_authority(
        cycle,
        expected_checkpoint_sha=args.expected_checkpoint_sha,
        expected_authorization_sha256=args.expected_authorization_sha256,
    )
    campaign_root.mkdir(parents=True)
    manifest = execution_manifest(source_db, checkpoint_sha=args.expected_checkpoint_sha)
    manifest_path = campaign_root / "EXECUTION_MANIFEST.json"
    write_json(manifest_path, manifest)
    execution_identity = manifest["execution_identity_sha256"]
    campaign = ContinuousV3TwoRunCampaign(execution_identity)
    for index, run_id in enumerate(CONTINUOUS_V3_RUN_IDENTITIES):
        if campaign.complete:
            break
        if campaign.consecutive_passes == 1:
            campaign.record_controlled_restart(execution_identity_sha256=execution_identity)
        campaign.begin_run(run_id, execution_identity_sha256=execution_identity)
        run_root = campaign_root / "runs" / run_id
        command = [
            sys.executable,
            str(Path(__file__).resolve()),
            "--single-run",
            "--run-id", run_id,
            "--cycle-directory", str(cycle),
            "--source-database", str(source_db),
            "--runtime-root", str(run_root),
            "--expected-checkpoint-sha", args.expected_checkpoint_sha,
            "--expected-authorization-sha256", args.expected_authorization_sha256,
            "--execution-manifest", str(manifest_path),
            "--execution-identity-sha256", execution_identity,
        ]
        completed = subprocess.run(command, cwd=ROOT, check=False)
        run_result = read_json(run_root / "RUN_RESULT.json")
        for label in tuple(record["label"] for record in run_result.get("calls", ())):
            campaign.record_dispatch(label)
        passed = completed.returncode == 0 and run_result.get("status") == "passed"
        campaign.terminalize(
            passed=passed,
            reason="qualified" if passed else str(run_result.get("error_type", "run_failed")),
        )
        write_json(campaign_root / "CAMPAIGN_STATE.json", campaign.to_dict())
        if not passed:
            break
    result = {
        "schema_version": "cera.sillytavern_continuous_v3_campaign_result.v1",
        "campaign_id": CAMPAIGN_ID,
        "status": "completed_two_consecutive_runs_passed" if campaign.complete else "stopped_without_two_consecutive_passes",
        "execution_manifest_sha256": bytes_sha256(manifest_path.read_bytes()),
        "execution_identity_sha256": execution_identity,
        "campaign": campaign.to_dict(),
    }
    write_json(campaign_root / "CAMPAIGN_RESULT.json", result)
    return 0 if campaign.complete else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--confirm-live-two-run", action="store_true")
    parser.add_argument("--single-run", action="store_true")
    parser.add_argument("--run-id")
    parser.add_argument("--cycle-directory", type=Path, required=True)
    parser.add_argument("--source-database", type=Path, required=True)
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--expected-checkpoint-sha", required=True)
    parser.add_argument("--expected-authorization-sha256", required=True)
    parser.add_argument("--execution-manifest", type=Path)
    parser.add_argument("--execution-identity-sha256")
    args = parser.parse_args()
    if args.single_run:
        if not all((args.run_id, args.execution_manifest, args.execution_identity_sha256)):
            parser.error("single-run mode requires exact run and execution identities")
        return run_single(args)
    if not args.confirm_live_two_run:
        parser.error("live campaign requires --confirm-live-two-run")
    return run_campaign(args)


if __name__ == "__main__":
    raise SystemExit(main())
