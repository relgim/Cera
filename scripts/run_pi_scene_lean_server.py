"""Launch or smoke-test the non-production Queue 0072 Pi Scene route."""

from __future__ import annotations

import argparse
from contextlib import ExitStack
from dataclasses import dataclass
from http.cookiejar import CookieJar
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
from threading import Thread
import time
from typing import Any
from urllib.request import HTTPCookieProcessor, Request, build_opener, urlopen

from cera.continuous.call_ledger import ContinuousProviderCallLedger
from cera.errors import ContractValidationError, StateConflictError
from cera.pi_scene.codex_planner import RetainedCodexPlannerAdapter
from cera.pi_scene.context import AcceptedBranchContextProvider, initial_hana_seed
from cera.pi_scene.contracts import RecordingStatus, SceneRoute
from cera.pi_scene.http import (
    PI_SCENE_ADULT_MODEL,
    PI_SCENE_ORDINARY_MODEL,
    PI_SCENE_PROFILE,
    PiSceneHttpAdapter,
    PiSceneServerConfigV1,
    build_pi_scene_server,
)
from cera.pi_scene.operation_ledger import PiProviderOperationLedger
from cera.pi_scene.pi_adapter import PiSceneAdapter
from cera.pi_scene.runtime import (
    LeanPiSceneCoordinator,
    repair_latest_ordinary_recording_from_output,
)
from cera.pi_scene.sillytavern_isolation import (
    isolated_sillytavern_command,
    stage_isolated_sillytavern,
    verify_isolated_sillytavern,
)
from cera.pi_scene.store import LeanSceneStore
from cera.pi_scene.writer_view import WriterViewMaterializer
from cera.reasoner_session.codex_stored import OpenAICodexStoredThreadBackend
from cera.sequence_first.prompting import PLANNER_BASE_INSTRUCTIONS
from cera.sequence_first.provider import SequenceFirstPlannerCodexBackend
from cera.sequence_first.sessions import PersistentPlannerSession
from cera.serialization import canonical_bytes, canonical_sha256


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PI = Path(r"C:\Users\Ted\AppData\Roaming\npm\pi.cmd")
DEFAULT_EXTENSION = ROOT / "integrations" / "pi" / "cera-scene-view.ts"
DEFAULT_SILLYTAVERN = Path(r"E:\AIChatBot\SillyTavern")


@dataclass(slots=True)
class LivePiSceneRuntime:
    stack: ExitStack
    coordinator: LeanPiSceneCoordinator
    store: LeanSceneStore
    sol_ledger: ContinuousProviderCallLedger
    deepseek_ledger: PiProviderOperationLedger

    def close(self) -> None:
        self.stack.close()


def _initialize_live_runtime_roots(runtime_root: Path) -> tuple[Path, Path, Path]:
    root = runtime_root.resolve()
    root.mkdir(parents=True, exist_ok=False)
    lifecycle_root = root / "codex_lifecycle"
    lifecycle_root.mkdir()
    operation_root = root / "codex_operations"
    operation_root.mkdir()
    return root, lifecycle_root, operation_root


def build_live_runtime(
    runtime_root: Path,
    *,
    sol_ceiling: int,
    deepseek_ceiling: int,
    deepseek_per_invocation_ceiling: int = 6,
    seed_runtime_root: Path | None = None,
    inject_generation_two_recorder_failure: bool = False,
) -> LivePiSceneRuntime:
    runtime_root, lifecycle_root, operation_root = _initialize_live_runtime_roots(
        runtime_root
    )
    if seed_runtime_root is not None:
        _seed_live_runtime_state(runtime_root, seed_runtime_root)
    stack = ExitStack()
    try:
        from openai_codex import Codex, CodexConfig

        codex = stack.enter_context(
            Codex(CodexConfig(config_overrides=("mcp_servers={}",), env={}))
        )
        account = codex.account()
        if account.account is None:
            raise StateConflictError("ChatGPT Codex account is unavailable")
        sol_ledger = ContinuousProviderCallLedger(
            (runtime_root / "SOL_PROVIDER_CALLS.jsonl").resolve(),
            maximum_calls=sol_ceiling,
        )
        lifecycle = OpenAICodexStoredThreadBackend(
            codex=codex,
            model="gpt-5.6-sol",
            cwd=str(lifecycle_root),
            base_instructions=PLANNER_BASE_INSTRUCTIONS,
            service_name="cera_pi_scene_planner",
            service_tier="priority",
        )
        planner = RetainedCodexPlannerAdapter(
            PersistentPlannerSession(
                SequenceFirstPlannerCodexBackend(
                    lifecycle=lifecycle,
                    workspace=operation_root,
                    call_ledger=sol_ledger,
                )
            )
        )
        deepseek_ledger = PiProviderOperationLedger(
            (runtime_root / "DEEPSEEK_PROVIDER_OPERATIONS.jsonl").resolve(),
            maximum_operations=deepseek_ceiling,
            maximum_operations_per_invocation=deepseek_per_invocation_ceiling,
        )
        pi = PiSceneAdapter(
            pi_executable=DEFAULT_PI,
            extension_path=DEFAULT_EXTENSION,
            pi_version="0.84.1",
            operation_ledger=deepseek_ledger,
        )
        store = LeanSceneStore(runtime_root / "accepted_world")

        def fault(accepted, attempt_number):
            if (
                inject_generation_two_recorder_failure
                and accepted.generation == 2
                and attempt_number == 1
            ):
                return "injected_smoke_recorder_failure"
            return None

        coordinator = LeanPiSceneCoordinator(
            store=store,
            planner=planner,
            writer_views=WriterViewMaterializer(runtime_root / "writer_views"),
            pi=pi,
            session_root=runtime_root / "pi_sessions",
            recording_fault_injector=fault,
        )
        return LivePiSceneRuntime(
            stack=stack,
            coordinator=coordinator,
            store=store,
            sol_ledger=sol_ledger,
            deepseek_ledger=deepseek_ledger,
        )
    except BaseException:
        stack.close()
        raise


def _seed_live_runtime_state(runtime_root: Path, seed_runtime_root: Path) -> None:
    source_root = seed_runtime_root.resolve()
    target_root = runtime_root.resolve()
    if source_root == target_root or target_root.is_relative_to(source_root):
        raise ContractValidationError("live seed root overlaps its target")
    for name in ("accepted_world", "pi_sessions"):
        source = source_root / name
        target = target_root / name
        if not source.is_dir() or target.exists():
            raise StateConflictError(f"live seed {name} is unavailable or occupied")
        if any(path.is_symlink() for path in source.rglob("*")):
            raise StateConflictError(f"live seed {name} contains a symlink")
        shutil.copytree(source, target)


def _available_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _start_isolated_sillytavern(target_root: Path, *, port: int) -> subprocess.Popen:
    manifest = verify_isolated_sillytavern(target_root)
    if manifest.get("user_data_copied") is not False:
        raise StateConflictError("isolated SillyTavern contains user data")
    node = shutil.which("node")
    if node is None:
        raise StateConflictError("Node.js is unavailable for isolated SillyTavern")
    command = isolated_sillytavern_command(
        target_root,
        node_executable=Path(node),
        port=port,
    )
    logs = target_root / "cera-smoke-logs"
    logs.mkdir()
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    stdout = (logs / "sillytavern.stdout.log").open("wb")
    stderr = (logs / "sillytavern.stderr.log").open("wb")
    try:
        process = subprocess.Popen(
            command,
            cwd=str(target_root),
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            shell=False,
            creationflags=creationflags,
        )
    finally:
        stdout.close()
        stderr.close()
    deadline = time.monotonic() + 90
    url = f"http://127.0.0.1:{port}/"
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise StateConflictError(
                f"isolated SillyTavern exited during startup ({process.returncode})"
            )
        try:
            with urlopen(url, timeout=2) as response:
                if 200 <= response.status < 500:
                    return process
        except Exception:
            time.sleep(0.2)
    process.terminate()
    process.wait(timeout=10)
    raise StateConflictError("isolated SillyTavern did not become reachable")


def _stop_process(process: subprocess.Popen | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _json_request(
    url: str,
    *,
    token: str,
    payload: dict[str, Any] | None = None,
    origin: str = "http://127.0.0.1:8000",
) -> dict[str, Any]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Origin": origin,
    }
    request = Request(
        url,
        data=data,
        headers=headers,
        method="GET" if payload is None else "POST",
    )
    with urlopen(request, timeout=900) as response:
        content_type = response.headers.get("Content-Type", "")
        if not content_type.startswith("application/json"):
            raise StateConflictError("Pi Scene HTTP response is not JSON")
        value = json.loads(response.read().decode("utf-8"))
        if not isinstance(value, dict):
            raise StateConflictError("Pi Scene HTTP response is not an object")
        return value


class _IsolatedSillyTavernClient:
    """Use the isolated server's real Custom chat-completion proxy."""

    def __init__(self, origin: str) -> None:
        self.origin = origin.rstrip("/")
        self._opener = build_opener(HTTPCookieProcessor(CookieJar()))
        request = Request(self.origin + "/csrf-token", method="GET")
        with self._opener.open(request, timeout=30) as response:
            value = json.loads(response.read().decode("utf-8"))
        token = value.get("token") if isinstance(value, dict) else None
        if not isinstance(token, str) or not token:
            raise StateConflictError("isolated SillyTavern did not issue a CSRF token")
        self._csrf_token = token
        self.completed_requests = 0

    def complete(
        self,
        *,
        cera_base: str,
        authorization_token: str,
        model: str,
        source: str,
        session_id: str,
    ) -> dict[str, Any]:
        payload = {
            "chat_completion_source": "custom",
            "custom_url": cera_base.rstrip("/") + "/v1",
            "custom_include_headers": (
                f"Authorization: Bearer {authorization_token}"
            ),
            "custom_include_body": (
                f"cera_session_id: {session_id}\n"
                f"cera_profile_id: {PI_SCENE_PROFILE}"
            ),
            "model": model,
            "messages": [{"role": "user", "content": source}],
            "stream": False,
            "temperature": 0.7,
            "max_tokens": 4096,
        }
        request = Request(
            self.origin + "/api/backends/chat-completions/generate",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "X-CSRF-Token": self._csrf_token,
            },
            method="POST",
        )
        with self._opener.open(request, timeout=900) as response:
            content_type = response.headers.get("Content-Type", "")
            if not content_type.startswith("application/json"):
                raise StateConflictError("isolated SillyTavern response is not JSON")
            value = json.loads(response.read().decode("utf-8"))
        if not isinstance(value, dict):
            raise StateConflictError("isolated SillyTavern response is not an object")
        if value.get("error"):
            raise StateConflictError("isolated SillyTavern reported an upstream error")
        self.completed_requests += 1
        return value


def run_live_smoke(
    runtime_root: Path,
    *,
    sillytavern_source: Path = DEFAULT_SILLYTAVERN,
    sol_ceiling: int = 12,
    deepseek_ceiling: int = 60,
    deepseek_per_invocation_ceiling: int = 6,
    resume_from_runtime_root: Path | None = None,
) -> dict[str, Any]:
    runtime = build_live_runtime(
        runtime_root,
        sol_ceiling=sol_ceiling,
        deepseek_ceiling=deepseek_ceiling,
        deepseek_per_invocation_ceiling=deepseek_per_invocation_ceiling,
        seed_runtime_root=resume_from_runtime_root,
        inject_generation_two_recorder_failure=(resume_from_runtime_root is None),
    )
    token = secrets.token_urlsafe(32)
    session_id = "cera-pi-scene-smoke"
    st_process = None
    try:
        isolated_st_root = runtime_root / "isolated_sillytavern"
        st_manifest = stage_isolated_sillytavern(sillytavern_source, isolated_st_root)
        st_port = _available_loopback_port()
        st_origin = f"http://127.0.0.1:{st_port}"
        st_process = _start_isolated_sillytavern(isolated_st_root, port=st_port)
        context = AcceptedBranchContextProvider(runtime.store, initial_hana_seed())
        adapter = PiSceneHttpAdapter(
            coordinator=runtime.coordinator,
            session_id=session_id,
            context_provider=context,
        )
        server = build_pi_scene_server(
            adapter,
            PiSceneServerConfigV1(
                host="127.0.0.1",
                port=0,
                authorization_token=token,
                approved_origins=(st_origin,),
            ),
        )
        worker = Thread(target=server.serve_forever, daemon=True)
        worker.start()
        st_client = _IsolatedSillyTavernClient(st_origin)
    except BaseException:
        _stop_process(st_process)
        runtime.close()
        raise
    base = f"http://127.0.0.1:{server.server_address[1]}"
    evidence: list[dict[str, Any]] = []

    def create(model: str, source: str) -> dict[str, Any]:
        value = st_client.complete(
            cera_base=base,
            authorization_token=token,
            model=model,
            source=source,
            session_id=session_id,
        )
        text = value["choices"][0]["message"]["content"]
        if not isinstance(text, str) or len(text.strip()) < 80:
            raise StateConflictError("live Pi Writer returned an unusable scene")
        lowered = text.lower()
        if any(marker in lowered for marker in ("i can't help", "i cannot help", "unable to comply")):
            raise StateConflictError("live Pi Writer returned a refusal")
        return value

    def decide(review_id: str, action: str, **extra: Any) -> dict[str, Any]:
        return _json_request(
            f"{base}/v1/cera/reviews/{review_id}/decision",
            token=token,
            payload={"action": action, **extra},
            origin=st_origin,
        )

    try:
        health = _json_request(base + "/health", token=token, origin=st_origin)
        if not health.get("loopback_only") or not health.get("authorization_required"):
            raise StateConflictError("loopback health boundary changed")

        if resume_from_runtime_root is None:
            ordinary_one = create(
                PI_SCENE_ORDINARY_MODEL,
                "Hana continues the conversation naturally and returns the next choice to Ted.",
            )
            ordinary_one_review = ordinary_one["cera"]["provisional_review_id"]
            accepted_one = decide(ordinary_one_review, "accept")
            if accepted_one["review"]["recording_status"] != RecordingStatus.COMPLETE.value:
                raise StateConflictError("first ordinary Recorder did not complete")
            evidence.append(_smoke_step("ordinary_accept_1", accepted_one))

            adult_one = create(
                PI_SCENE_ADULT_MODEL,
                "Both adults explicitly choose to become more intimate while Hana keeps the ability to pause.",
            )
            adult_one_review = adult_one["cera"]["provisional_review_id"]
            accepted_two = decide(adult_one_review, "accept")
            if accepted_two["review"]["recording_status"] != RecordingStatus.PENDING_REPAIR.value:
                raise StateConflictError("injected Recorder failure did not remain repairable")
            repaired_two = decide(adult_one_review, "repair_recording")
            if repaired_two["review"]["recording_status"] != RecordingStatus.COMPLETE.value:
                raise StateConflictError("Recorder repair did not complete")
            evidence.append(_smoke_step("adult_accept_1_repaired", repaired_two))
        else:
            seed_head = runtime.store.load_head(
                world_id="world-pi-scene-smoke", branch_id="branch-main"
            )
            if seed_head.generation != 2 or seed_head.receipt is None:
                raise StateConflictError("resume seed is not the accepted generation-two frontier")
            repair_turn = context(
                SceneRoute.ADULT,
                seed_head.receipt.exact_user_source,
                (),
            )
            repaired_attempt = runtime.coordinator.repair_latest_recording(repair_turn)
            if repaired_attempt.status is not RecordingStatus.COMPLETE:
                raise StateConflictError("resumed Recorder repair did not complete")
            evidence.append(
                {
                    "label": "adult_accept_1_repaired_after_restart",
                    "accepted_turn_id": seed_head.receipt.accepted_turn_id,
                    "accepted_receipt_sha256": seed_head.receipt.receipt_sha256,
                    "route": seed_head.receipt.route.value,
                    "recording_status": repaired_attempt.status.value,
                    "warnings": [],
                    "provider_operations": repaired_attempt.provider_operations,
                }
            )

        adult_two = create(
            PI_SCENE_ADULT_MODEL,
            "The adults continue one mutually chosen intimate beat and stop when Hana makes her next choice clear.",
        )
        adult_two_review = adult_two["cera"]["provisional_review_id"]
        accepted_three = decide(adult_two_review, "accept")
        if accepted_three["review"]["recording_status"] != RecordingStatus.COMPLETE.value:
            raise StateConflictError("second adult Recorder did not complete")
        evidence.append(_smoke_step("adult_accept_2", accepted_three))

        ordinary_two = create(
            PI_SCENE_ORDINARY_MODEL,
            "Return to an ordinary conversation as Hana reflects non-explicitly and leaves the floor to Ted.",
        )
        original_review_id = ordinary_two["cera"]["provisional_review_id"]
        original_review = _json_request(
            f"{base}/v1/cera/reviews/{original_review_id}",
            token=token,
            origin=st_origin,
        )
        regenerated = decide(
            original_review_id,
            "regenerate",
            force_rehydrate=True,
        )
        successor = regenerated.get("successor")
        if not isinstance(successor, dict):
            raise StateConflictError("Regenerate did not produce a successor")
        successor_review_id = successor["cera"]["provisional_review_id"]
        successor_review = _json_request(
            f"{base}/v1/cera/reviews/{successor_review_id}",
            token=token,
            origin=st_origin,
        )
        if (
            successor_review["primary_authority_sha256"]
            != original_review["primary_authority_sha256"]
        ):
            raise StateConflictError("Regenerate changed the exact Codex sequence")
        accepted_four = decide(successor_review_id, "accept")
        if accepted_four["review"]["recording_status"] != RecordingStatus.COMPLETE.value:
            raise StateConflictError("final ordinary Recorder did not complete")
        evidence.append(_smoke_step("ordinary_accept_2_rehydrated", accepted_four))

        head = runtime.store.load_head(
            world_id="world-pi-scene-smoke",
            branch_id="branch-main",
        )
        if head.generation != 4:
            raise StateConflictError("live smoke accepted generation differs from four")
        accepted_payloads = runtime.store.recent_accepted_payloads(
            world_id="world-pi-scene-smoke",
            branch_id="branch-main",
            limit=6,
            adult_full=True,
        )
        route_sequence = [value["receipt"]["route"] for value in accepted_payloads]
        if route_sequence != ["ordinary", "adult", "adult", "ordinary"]:
            raise StateConflictError("ordinary/adult transition sequence changed")
        final_writer_receipt = accepted_payloads[-1]["receipt"]["writer_receipt"]
        if final_writer_receipt["rehydrated"] is not True:
            raise StateConflictError("final accepted Pi session was not rehydrated")
        expected_sol_calls = 2 if resume_from_runtime_root is None else 1
        if runtime.sol_ledger.dispatched_call_count != expected_sol_calls:
            raise StateConflictError("live smoke Sol call count differs from its route")
        if st_process.poll() is not None:
            raise StateConflictError("isolated SillyTavern exited during the live smoke")
        expected_st_requests = 4 if resume_from_runtime_root is None else 2
        if st_client.completed_requests != expected_st_requests:
            raise StateConflictError("isolated SillyTavern request count differs from its route")

        result = {
            "schema_version": "cera.pi_scene.live_smoke_evidence.v1",
            "status": "passed",
            "accepted_turns": 4,
            "routes": route_sequence,
            "sol_calls": runtime.sol_ledger.dispatched_call_count,
            "deepseek_operations": runtime.deepseek_ledger.operation_count,
            "deepseek_cached_input_tokens": sum(
                int(value.get("cached_input_tokens", 0))
                for value in runtime.deepseek_ledger.events
                if value.get("event") == "provider_operation_completed"
            ),
            "regenerate_exact_sequence": True,
            "pi_reset_rehydrated": True,
            "recorder_failure_repaired": True,
            "route_transition": "ordinary-adult-adult-ordinary",
            "isolated_sillytavern_started": True,
            "isolated_sillytavern_routed_chat_completions": st_client.completed_requests,
            "resumed_from_accepted_generation": (
                None if resume_from_runtime_root is None else 2
            ),
            "isolated_sillytavern_user_data_copied": st_manifest["user_data_copied"],
            "isolated_sillytavern_manifest_sha256": st_manifest["manifest_sha256"],
            "accepted_head_sha256": head.accepted_head_sha256,
            "steps": evidence,
        }
        result["evidence_sha256"] = canonical_sha256(result)
        (runtime_root / "SMOKE_EVIDENCE.json").write_bytes(canonical_bytes(result) + b"\n")
        return result
    finally:
        try:
            server.shutdown()
            server.server_close()
            worker.join(timeout=10)
        finally:
            _stop_process(st_process)
            runtime.close()


def _smoke_step(label: str, value: dict[str, Any]) -> dict[str, Any]:
    review = value["review"]
    return {
        "label": label,
        "accepted_turn_id": value.get("accepted_turn_id"),
        "accepted_receipt_sha256": value.get("accepted_receipt_sha256"),
        "route": review["route"],
        "recording_status": review["recording_status"],
        "warnings": [warning["warning_code"] for warning in review["warnings"]],
        "provider_operations": review["provider_operations"],
    }


def repair_existing_smoke(runtime_root: Path) -> dict[str, Any]:
    """Finish one explicit structural Recorder repair with zero provider work."""

    root = runtime_root.resolve()
    evidence_path = root / "SMOKE_EVIDENCE.json"
    if evidence_path.exists():
        raise StateConflictError("smoke evidence is already published")
    store = LeanSceneStore(root / "accepted_world")
    context = AcceptedBranchContextProvider(store, initial_hana_seed())
    head = store.load_head(world_id="world-pi-scene-smoke", branch_id="branch-main")
    if head.generation != 4 or head.receipt is None:
        raise StateConflictError("existing smoke is not at accepted generation four")
    if head.recording_status is not RecordingStatus.PENDING_REPAIR:
        raise StateConflictError("existing smoke does not require final recording repair")

    sessions = sorted((root / "pi_sessions").rglob("*.jsonl"))
    if not sessions:
        raise StateConflictError("existing smoke omitted Pi session evidence")
    output_text = _last_assistant_text(sessions[-1])
    turn = context(SceneRoute.ORDINARY, head.receipt.exact_user_source, ())
    repaired = repair_latest_ordinary_recording_from_output(
        store=store,
        turn=turn,
        output_text=output_text,
    )
    if repaired.status is not RecordingStatus.COMPLETE or repaired.provider_operations != 0:
        raise StateConflictError("provider-free Recorder repair did not complete")

    accepted = store.recent_accepted_payloads(
        world_id="world-pi-scene-smoke",
        branch_id="branch-main",
        limit=6,
        adult_full=True,
    )
    routes = [value["receipt"]["route"] for value in accepted]
    if routes != ["ordinary", "adult", "adult", "ordinary"]:
        raise StateConflictError("existing smoke route sequence changed")
    for value in accepted:
        route = value["receipt"]["route"]
        if route == "ordinary" and "ordinary_record" not in value:
            raise StateConflictError("existing smoke retained an incomplete ordinary record")
        if route == "adult" and not {
            "adult_full_record",
            "adult_projection",
        }.issubset(value):
            raise StateConflictError("existing smoke retained an incomplete adult record")
    if accepted[-1]["receipt"]["writer_receipt"]["rehydrated"] is not True:
        raise StateConflictError("existing smoke final Writer was not rehydrated")

    sol_events = _read_jsonl(root / "SOL_PROVIDER_CALLS.jsonl")
    deepseek_events = _read_jsonl(root / "DEEPSEEK_PROVIDER_OPERATIONS.jsonl")
    sol_calls = sum(value.get("state") == "transport_invoked" for value in sol_events)
    deepseek_operations = sum(
        value.get("event") == "provider_operation_started" for value in deepseek_events
    )
    if sol_calls != 1 or deepseek_operations != 12:
        raise StateConflictError("existing smoke provider accounting changed")
    if any(value.get("event") == "invocation_failed" for value in deepseek_events):
        raise StateConflictError("existing smoke contains a failed Pi invocation")
    st_manifest = verify_isolated_sillytavern(root / "isolated_sillytavern")
    if not (root / "isolated_sillytavern" / "cera-smoke-logs").is_dir():
        raise StateConflictError("existing smoke omitted SillyTavern launch evidence")

    current_head = store.load_head(
        world_id="world-pi-scene-smoke", branch_id="branch-main"
    )
    result = {
        "schema_version": "cera.pi_scene.live_smoke_evidence.v1",
        "status": "passed",
        "accepted_turns": 4,
        "routes": routes,
        "sol_calls": sol_calls,
        "deepseek_operations": deepseek_operations,
        "deepseek_cached_input_tokens": sum(
            int(value.get("cached_input_tokens", 0))
            for value in deepseek_events
            if value.get("event") == "provider_operation_completed"
        ),
        "regenerate_exact_sequence": True,
        "pi_reset_rehydrated": True,
        "recorder_failure_repaired": True,
        "provider_free_final_recording_repair": True,
        "route_transition": "ordinary-adult-adult-ordinary",
        "isolated_sillytavern_started": True,
        "isolated_sillytavern_routed_chat_completions": 2,
        "resumed_from_accepted_generation": 2,
        "isolated_sillytavern_user_data_copied": st_manifest["user_data_copied"],
        "isolated_sillytavern_manifest_sha256": st_manifest["manifest_sha256"],
        "accepted_head_sha256": current_head.accepted_head_sha256,
        "steps": [
            {
                "accepted_turn_id": value["receipt"]["accepted_turn_id"],
                "route": value["receipt"]["route"],
                "recording_status": "complete",
            }
            for value in accepted
        ],
    }
    result["evidence_sha256"] = canonical_sha256(result)
    evidence_path.write_bytes(canonical_bytes(result) + b"\n")
    return result


def _last_assistant_text(path: Path) -> str:
    output = ""
    for event in _read_jsonl(path):
        if event.get("type") != "message":
            continue
        message = event.get("message")
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        content = message.get("content")
        if isinstance(content, str):
            output = content
        elif isinstance(content, list):
            output = "".join(
                str(block.get("text", ""))
                for block in content
                if isinstance(block, dict) and block.get("type") == "text"
            )
    if not output.strip():
        raise StateConflictError("Pi session omitted final assistant text")
    return output


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    values = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    if any(not isinstance(value, dict) for value in values):
        raise StateConflictError("JSONL evidence contains a non-object")
    return values


def serve(runtime_root: Path, *, port: int, session_id: str) -> None:
    token = __import__("os").environ.get("CERA_PI_SCENE_TOKEN", "")
    if len(token) < 24:
        raise ContractValidationError("CERA_PI_SCENE_TOKEN must contain at least 24 characters")
    runtime = build_live_runtime(
        runtime_root,
        sol_ceiling=12,
        deepseek_ceiling=60,
    )
    adapter = PiSceneHttpAdapter(
        coordinator=runtime.coordinator,
        session_id=session_id,
        context_provider=AcceptedBranchContextProvider(runtime.store, initial_hana_seed()),
    )
    server = build_pi_scene_server(
        adapter,
        PiSceneServerConfigV1(
            host="127.0.0.1",
            port=port,
            authorization_token=token,
            approved_origins=("http://127.0.0.1:8000",),
        ),
    )
    try:
        server.serve_forever()
    finally:
        server.server_close()
        runtime.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("live-smoke", "repair-existing-smoke", "serve"))
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--port", type=int, default=5127)
    parser.add_argument("--session-id", default="cera-pi-scene-test")
    parser.add_argument("--sillytavern-source", type=Path, default=DEFAULT_SILLYTAVERN)
    parser.add_argument("--sol-ceiling", type=int, default=12)
    parser.add_argument("--deepseek-ceiling", type=int, default=60)
    parser.add_argument("--deepseek-per-invocation-ceiling", type=int, default=6)
    parser.add_argument("--resume-from-runtime-root", type=Path)
    args = parser.parse_args()
    if args.mode == "live-smoke":
        print(
            json.dumps(
                run_live_smoke(
                    args.runtime_root,
                    sillytavern_source=args.sillytavern_source,
                    sol_ceiling=args.sol_ceiling,
                    deepseek_ceiling=args.deepseek_ceiling,
                    deepseek_per_invocation_ceiling=args.deepseek_per_invocation_ceiling,
                    resume_from_runtime_root=args.resume_from_runtime_root,
                ),
                indent=2,
                sort_keys=True,
            )
        )
    elif args.mode == "repair-existing-smoke":
        print(json.dumps(repair_existing_smoke(args.runtime_root), indent=2, sort_keys=True))
    else:
        serve(args.runtime_root, port=args.port, session_id=args.session_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
