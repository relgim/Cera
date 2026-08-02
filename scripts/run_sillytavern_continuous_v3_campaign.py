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
from cera.serialization import bytes_sha256, canonical_bytes, canonical_sha256, re_is_sha256
from cera.sillytavern.campaign import (
    CONTINUOUS_V3_CALL_SCHEDULE,
    CONTINUOUS_V3_RUN_IDENTITIES,
    CampaignRunRecord,
    CampaignRunState,
    ContinuousV3TwoRunCampaign,
)
from cera.sillytavern.continuous_test import (
    CONTINUOUS_V3_TEST_FIXTURE,
    ContinuousSillyTavernTestAdapter,
)
from cera.sillytavern.models import CERA_CONTINUOUS_V3_TEST_MODEL
from cera.sillytavern.server import CeraSillyTavernServerConfig, build_server

if __package__:
    from scripts.run_continuous_planner_validator_job4 import (
        BRANCH_ID,
        ROOT,
        WORLD_ID,
        JobHarness,
        _terminalize_known_thread_sessions,
        compatibility,
        seed_world,
    )
else:
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

_EXPECTED_OWNER_BY_CALL = {
    "planner": "planner",
    "deepseek": "composer",
    "validator": "validator",
}


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


def read_jsonl(path: Path) -> tuple[dict[str, Any], ...]:
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"JSON object required on every line: {path}")
        records.append(value)
    return tuple(records)


def _call_owner(label: str) -> str:
    for token, owner in _EXPECTED_OWNER_BY_CALL.items():
        if token in label:
            return owner
    raise ValueError(f"unsupported campaign call label: {label}")


def recover_prior_campaign(
    prior_root: Path,
    *,
    execution_identity_sha256: str,
) -> tuple[ContinuousV3TwoRunCampaign, dict[str, Any]]:
    """Recover terminal history from immutable per-run evidence.

    Run 001 exposed that the original parent result omitted a dispatched call
    when the provider completed but domain validation failed.  Recovery trusts
    the append-only provider ledger and per-run result, records the discrepancy,
    and never rewrites the historical files.
    """

    root = prior_root.resolve()
    campaign_path = root / "CAMPAIGN_RESULT.json"
    manifest_path = root / "EXECUTION_MANIFEST.json"
    prior = read_json(campaign_path)
    manifest = read_json(manifest_path)
    if prior.get("campaign_id") != CAMPAIGN_ID:
        raise ValueError("prior campaign identity changed")
    prior_execution_identity = manifest.get("execution_identity_sha256")
    if (
        not isinstance(prior_execution_identity, str)
        or not re_is_sha256(prior_execution_identity)
        or prior.get("execution_identity_sha256") != prior_execution_identity
    ):
        raise ValueError("prior campaign execution manifest is invalid")
    prior_campaign = prior.get("campaign")
    if not isinstance(prior_campaign, dict):
        raise ValueError("prior campaign state is missing")
    raw_runs = prior_campaign.get("runs")
    if not isinstance(raw_runs, list) or not raw_runs:
        raise ValueError("prior campaign has no recoverable runs")
    if len(raw_runs) >= len(CONTINUOUS_V3_RUN_IDENTITIES):
        raise ValueError("prior campaign exhausted all immutable run identities")
    prior_recovery_path = root / "PRIOR_CAMPAIGN_RECOVERY.json"
    inherited_run_roots: dict[str, Path] = {}
    inherited_run_entries: dict[str, dict[str, Any]] = {}
    prior_recovery_sha256 = None
    if prior_recovery_path.is_file():
        prior_recovery = read_json(prior_recovery_path)
        prior_recovery_sha256 = bytes_sha256(prior_recovery_path.read_bytes())
        inherited = prior_recovery.get("runs")
        if not isinstance(inherited, list):
            raise ValueError("prior campaign recovery run index is malformed")
        for item in inherited:
            if not isinstance(item, dict):
                raise ValueError("prior campaign recovery run entry is malformed")
            run_id = item.get("run_id")
            evidence_root = item.get("evidence_root")
            if not isinstance(run_id, str) or not isinstance(evidence_root, str):
                raise ValueError("prior campaign recovery evidence root is invalid")
            inherited_run_roots[run_id] = Path(evidence_root).resolve()
            inherited_run_entries[run_id] = item

    recovered: list[CampaignRunRecord] = []
    recovery_runs: list[dict[str, Any]] = []
    total_calls = 0
    for index, raw in enumerate(raw_runs):
        if not isinstance(raw, dict):
            raise ValueError("prior campaign run is malformed")
        run_id = CONTINUOUS_V3_RUN_IDENTITIES[index]
        if raw.get("run_id") != run_id or raw.get("state") not in {"passed", "failed"}:
            raise ValueError("prior campaign run identity or terminal state changed")
        local_run_root = root / "runs" / run_id
        run_root = (
            local_run_root
            if local_run_root.is_dir()
            else inherited_run_roots.get(run_id, local_run_root)
        )
        if not run_root.is_dir():
            raise ValueError("prior immutable run evidence is unavailable")
        result_path = run_root / "RUN_RESULT.json"
        ledger_path = run_root / "PROVIDER_CALL_LEDGER.jsonl"
        run_manifest_path = run_root / "EXECUTION_MANIFEST.json"
        result = read_json(result_path)
        run_manifest = read_json(run_manifest_path)
        inherited_entry = inherited_run_entries.get(run_id)
        if inherited_entry is not None:
            expected_hashes = {
                "run_result_sha256": result_path,
                "provider_ledger_sha256": ledger_path,
                "execution_manifest_sha256": run_manifest_path,
            }
            if any(
                inherited_entry.get(key) != bytes_sha256(path.read_bytes())
                for key, path in expected_hashes.items()
            ):
                raise ValueError("inherited immutable run evidence hash changed")
        if result.get("run_id") != run_id or result.get("status") != raw.get("state"):
            raise ValueError("prior run result disagrees with terminal campaign history")
        events = read_jsonl(ledger_path)
        invoked: list[dict[str, Any]] = []
        seen_call_ids: set[str] = set()
        for event in events:
            if event.get("state") != "transport_invoked":
                continue
            call_id = event.get("call_id")
            if not isinstance(call_id, str) or call_id in seen_call_ids:
                raise ValueError("prior provider ledger call identity is invalid")
            seen_call_ids.add(call_id)
            invoked.append(event)
        provider_calls = result.get("provider_calls")
        if provider_calls != len(invoked):
            raise ValueError("prior run provider count disagrees with its immutable ledger")
        raw_result_calls = result.get("calls")
        if raw_result_calls is None:
            calls = CONTINUOUS_V3_CALL_SCHEDULE[: len(invoked)]
            call_source = "recovered_from_transport_invoked_prefix"
        else:
            if not isinstance(raw_result_calls, list):
                raise ValueError("prior run call records are malformed")
            calls = tuple(record.get("label") for record in raw_result_calls)
            call_source = "run_result_call_records"
        if calls != CONTINUOUS_V3_CALL_SCHEDULE[: len(calls)] or len(calls) != len(invoked):
            raise ValueError("prior run calls are not an exact schedule prefix")
        for label, event in zip(calls, invoked, strict=True):
            if event.get("owner") != _call_owner(label):
                raise ValueError("prior provider ledger owner disagrees with schedule")
        total_calls += len(calls)
        old_identity = run_manifest.get("execution_identity_sha256")
        if not isinstance(old_identity, str) or not re_is_sha256(old_identity):
            raise ValueError("prior run execution identity is invalid")
        state = CampaignRunState(raw["state"])
        recovered.append(
            CampaignRunRecord(
                run_id=run_id,
                state=state,
                execution_identity_sha256=old_identity,
                calls=calls,
                terminal_reason=str(raw.get("terminal_reason") or "historical_terminal"),
            )
        )
        recovery_runs.append(
            {
                "run_id": run_id,
                "evidence_root": str(run_root),
                "state": state.value,
                "execution_identity_sha256": old_identity,
                "calls": list(calls),
                "call_source": call_source,
                "run_result_sha256": bytes_sha256(result_path.read_bytes()),
                "provider_ledger_sha256": bytes_sha256(ledger_path.read_bytes()),
                "execution_manifest_sha256": bytes_sha256(run_manifest_path.read_bytes()),
                "recorded_parent_call_count": len(raw.get("calls", ())),
                "immutable_ledger_call_count": len(invoked),
            }
        )
    if total_calls > 40:
        raise ValueError("recovered campaign exceeds the provider-call ceiling")
    campaign = ContinuousV3TwoRunCampaign(
        execution_identity_sha256,
        runs=recovered,
        consecutive_passes=0,
        total_provider_calls=total_calls,
    )
    recovery: dict[str, Any] = {
        "schema_version": "cera.sillytavern_continuous_v3_prior_recovery.v1",
        "prior_campaign_id": CAMPAIGN_ID,
        "prior_campaign_result_sha256": bytes_sha256(campaign_path.read_bytes()),
        "prior_execution_manifest_sha256": bytes_sha256(manifest_path.read_bytes()),
        "prior_recovery_sha256": prior_recovery_sha256,
        "new_execution_identity_sha256": execution_identity_sha256,
        "execution_change_resets_consecutive_passes": True,
        "recovered_total_provider_calls": total_calls,
        "runs": recovery_runs,
    }
    recovery["recovery_sha256"] = canonical_sha256(recovery)
    return campaign, recovery


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
    supplied_manifest = read_json(args.execution_manifest)
    if supplied_manifest.get("execution_identity_sha256") != args.execution_identity_sha256:
        raise ValueError("single-run execution identity disagrees with its manifest")
    write_json(run_root / "EXECUTION_MANIFEST.json", supplied_manifest)
    planner_session = validator_session = None
    lineage = ContinuousThreadLineageLedger()
    harness = None
    server = None
    server_thread = None
    provider_stack: ExitStack | None = None
    try:
        from openai_codex import Codex, CodexConfig

        provider_stack = ExitStack()
        stack = provider_stack
        if provider_stack is not None:
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
        if provider_stack is not None:
            try:
                provider_stack.close()
            except BaseException as exc:
                result["status"] = "failed"
                result["provider_context_close_error_type"] = type(exc).__name__
                result["provider_context_close_error_message"] = str(exc)
        if harness is not None:
            result["calls"] = harness.call_records
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
    manifest = execution_manifest(source_db, checkpoint_sha=args.execution_checkpoint_sha)
    manifest_path = campaign_root / "EXECUTION_MANIFEST.json"
    write_json(manifest_path, manifest)
    execution_identity = manifest["execution_identity_sha256"]
    prior_recovery = None
    if args.prior_campaign_root is None:
        campaign = ContinuousV3TwoRunCampaign(execution_identity)
    else:
        campaign, prior_recovery = recover_prior_campaign(
            args.prior_campaign_root,
            execution_identity_sha256=execution_identity,
        )
        write_json(campaign_root / "PRIOR_CAMPAIGN_RECOVERY.json", prior_recovery)
    for run_id in CONTINUOUS_V3_RUN_IDENTITIES[len(campaign.runs) :]:
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
        "prior_campaign_recovery_sha256": (
            None if prior_recovery is None else prior_recovery["recovery_sha256"]
        ),
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
    parser.add_argument("--execution-checkpoint-sha")
    parser.add_argument("--expected-authorization-sha256", required=True)
    parser.add_argument("--prior-campaign-root", type=Path)
    parser.add_argument("--execution-manifest", type=Path)
    parser.add_argument("--execution-identity-sha256")
    args = parser.parse_args()
    if args.execution_checkpoint_sha is None:
        args.execution_checkpoint_sha = args.expected_checkpoint_sha
    if args.single_run:
        if not all((args.run_id, args.execution_manifest, args.execution_identity_sha256)):
            parser.error("single-run mode requires exact run and execution identities")
        return run_single(args)
    if not args.confirm_live_two_run:
        parser.error("live campaign requires --confirm-live-two-run")
    return run_campaign(args)


if __name__ == "__main__":
    raise SystemExit(main())
