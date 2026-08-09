"""Freeze and run the full-model CERA backend/SillyTavern qualification.

The live command is deliberately two-step.  ``freeze`` must complete against a
clean exact Git tree before ``live`` can construct provider transports.  The
live phase performs 10 ordinary plus 10 adult direct-backend requests, then 5
ordinary plus 5 adult requests through a disposable SillyTavern copy.  Each
phase is one retained ``cera-alpha`` story branch.  No automatic retry or
fallback exists; one explicit creator Regenerate may follow a noncritical
rejected first pass, and both outcomes remain in evidence.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from http.cookiejar import CookieJar
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from typing import Any
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, Request, build_opener, urlopen

from cera.errors import ContractValidationError, StateConflictError
from cera.pi_scene.context import initial_hanezawa_doorway_seed
from cera.pi_scene.http import PiSceneHttpAdapter, PiSceneServerConfigV1, build_pi_scene_server
from cera.pi_scene.http_contracts import LeanSceneRequestControlsV2
from cera.pi_scene.qualification import (
    DEEPSEEK_HTTP_OPERATION_CEILING,
    DEEPSEEK_PER_INVOCATION_CEILING,
    SOL_FAMILY_CEILING,
    ClientResponseV1,
    FullModelQualificationRunner,
    QualificationFixtureV1,
    QualificationPhase,
    build_qualification_manifest,
    load_qualification_fixtures,
    load_qualification_manifest,
    verify_qualification_artifacts,
    write_qualification_manifest,
)
from cera.pi_scene.qualification_isolation import (
    MANIFEST_NAME as ISOLATED_ST_MANIFEST_NAME,
    qualification_sillytavern_command,
    stage_qualification_sillytavern,
    verify_qualification_sillytavern,
)
from cera.serialization import canonical_bytes, canonical_sha256
from scripts.run_pi_scene_lean_server import (
    DEFAULT_PI,
    DEFAULT_SILLYTAVERN,
    _stop_process,
    accepted_logic_route,
    build_live_runtime,
    build_session_context_provider,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FIXTURES = ROOT / "evaluation" / "fixtures" / "pi_scene_full_model_qualification_v1.json"
PI_PACKAGE_ROOT = Path(
    r"C:\Users\Ted\AppData\Roaming\npm\node_modules\@earendil-works\pi-coding-agent"
)


def _repository_artifacts(fixture_path: Path) -> dict[str, tuple[Path, ...]]:
    relative_fixture = fixture_path.resolve().relative_to(ROOT.resolve())
    return {
        "source": (
            Path("src/cera/pi_scene"),
            Path("src/cera/cognition"),
            Path("src/cera/semantic_validation"),
            Path("src/cera/adult_pipeline"),
            Path("src/cera/sequence_first"),
        ),
        "prompts": (
            Path("src/cera/cognition/prompting.py"),
            Path("src/cera/semantic_validation/prompting.py"),
            Path("src/cera/sequence_first/prompting.py"),
            Path("src/cera/pi_scene/pi_adapter.py"),
            Path("src/cera/pi_scene/full_model_adult_runtime.py"),
        ),
        "tools": (
            Path("scripts/run_pi_scene_lean_server.py"),
            Path("scripts/run_pi_scene_full_model_qualification.py"),
            Path("integrations/pi/cera-scene-view.ts"),
            Path("integrations/sillytavern/creator-review-extension"),
            Path("integrations/sillytavern/cera-review-proxy-plugin"),
        ),
        "configuration": (
            Path("pyproject.toml"),
            Path("integrations/sillytavern/pi_scene_lean_v1_profile.json"),
        ),
        "fixtures": (relative_fixture,),
        "genesis": (Path("genesis/packages"),),
        "adult_craft": (Path("adult/catalog/adult_craft_v1"),),
    }


def _external_artifacts(isolated_sillytavern_root: Path) -> dict[str, tuple[Path, ...]]:
    node = shutil.which("node")
    if node is None:
        raise ContractValidationError("Node.js is unavailable for isolated SillyTavern")
    return {
        "provider_runtime_tools": (
            Path(sys.executable),
            DEFAULT_PI,
            PI_PACKAGE_ROOT / "package.json",
            PI_PACKAGE_ROOT / "dist",
            Path(node),
        ),
        "sillytavern_executable_tree_binding": (
            isolated_sillytavern_root / ISOLATED_ST_MANIFEST_NAME,
        ),
    }


class DirectCeraQualificationClient:
    def __init__(self, *, base_url: str, token: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token

    def complete(
        self,
        *,
        fixture: QualificationFixtureV1,
        session_id: str,
        payload: dict[str, Any] | Any,
    ) -> ClientResponseV1:
        del fixture, session_id
        return _post_json(
            self.base_url + "/v1/chat/completions",
            token=self.token,
            payload=dict(payload),
            transport="direct_cera_backend",
        )

    def regenerate(
        self,
        *,
        fixture: QualificationFixtureV1,
        review_id: str,
    ) -> ClientResponseV1:
        del fixture
        return _post_json(
            self.base_url + f"/v1/cera/reviews/{review_id}/decision",
            token=self.token,
            payload={"action": "regenerate"},
            transport="direct_cera_review_action",
            path=f"/v1/cera/reviews/{review_id}/decision",
        )

    def verify_ready(self) -> dict[str, Any]:
        health = _get_json(self.base_url + "/health", token=self.token)
        models = _get_json(self.base_url + "/v1/models", token=self.token)
        if health.get("status") != "ok" or health.get("loopback_only") is not True:
            raise StateConflictError("CERA qualification health boundary changed")
        available = {value.get("id") for value in models.get("data", []) if isinstance(value, dict)}
        if "cera-alpha" not in available:
            raise StateConflictError("CERA qualification model catalog omitted cera-alpha")
        return {
            "health_sha256": canonical_sha256(health),
            "models_sha256": canonical_sha256(models),
        }


class IsolatedSillyTavernQualificationClient:
    def __init__(self, *, origin: str, cera_base_url: str, cera_token: str) -> None:
        self.origin = origin.rstrip("/")
        self.cera_base_url = cera_base_url.rstrip("/")
        self.cera_token = cera_token
        self._opener = build_opener(HTTPCookieProcessor(CookieJar()))
        request = Request(self.origin + "/csrf-token", method="GET")
        with self._opener.open(request, timeout=30) as response:
            value = json.loads(response.read().decode("utf-8"))
        token = value.get("token") if isinstance(value, dict) else None
        if not isinstance(token, str) or not token:
            raise StateConflictError("isolated SillyTavern did not issue a CSRF token")
        self._csrf_token = token

    def complete(
        self,
        *,
        fixture: QualificationFixtureV1,
        session_id: str,
        payload: dict[str, Any] | Any,
    ) -> ClientResponseV1:
        value = dict(payload)
        custom_body = "\n".join(
            (
                f"cera_session_id: {session_id}",
                f"cera_profile_id: {value['cera_profile_id']}",
                f"cera_character_autonomy: {value['cera_character_autonomy']}",
                f"cera_adult_craft_mode: {fixture.adult_craft_mode}",
                f"cera_prompt_handling: {value['cera_prompt_handling']}",
                f"cera_reasoning_effort: {value['cera_reasoning_effort']}",
                f"cera_scene_depth: {value['cera_scene_depth']}",
            )
        )
        proxy_payload = {
            "chat_completion_source": "custom",
            "custom_url": self.cera_base_url + "/v1",
            "custom_include_headers": f"Authorization: Bearer {self.cera_token}",
            "custom_include_body": custom_body,
            "model": value["model"],
            "messages": value["messages"],
            "stream": False,
            "temperature": 0.7,
            "max_tokens": 8192,
        }
        started = time.perf_counter_ns()
        request = Request(
            self.origin + "/api/backends/chat-completions/generate",
            data=json.dumps(proxy_payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "X-CSRF-Token": self._csrf_token,
            },
            method="POST",
        )
        try:
            with self._opener.open(request, timeout=900) as response:
                status = response.status
                body = _decode_json_object(response.read())
        except HTTPError as exc:
            status = exc.code
            body = _decode_json_object(exc.read())
        duration_ms = max(0, (time.perf_counter_ns() - started) // 1_000_000)
        return ClientResponseV1(
            transport="isolated_sillytavern_custom_proxy",
            path="/api/backends/chat-completions/generate",
            status_code=status,
            duration_ms=duration_ms,
            body=body,
        )

    def regenerate(
        self,
        *,
        fixture: QualificationFixtureV1,
        review_id: str,
    ) -> ClientResponseV1:
        del fixture
        path = f"/api/plugins/cera-review/v1/cera/reviews/{review_id}/decision"
        return self._relay(
            path=path,
            method="POST",
            payload={"action": "regenerate"},
        )

    def probe_relay(
        self,
        *,
        decline_review_id: str,
        regenerate_review_id: str,
    ) -> dict[str, Any]:
        health = self._relay(
            path="/api/plugins/cera-review/health",
            method="GET",
        )
        decline_review = self._relay(
            path=f"/api/plugins/cera-review/v1/cera/reviews/{decline_review_id}",
            method="GET",
        )
        regenerate_review = self._relay(
            path=f"/api/plugins/cera-review/v1/cera/reviews/{regenerate_review_id}",
            method="GET",
        )
        decline = self._relay(
            path=(
                "/api/plugins/cera-review/v1/cera/reviews/"
                f"{decline_review_id}/decision"
            ),
            method="POST",
            payload={"action": "decline"},
        )
        regenerate = self._relay(
            path=(
                "/api/plugins/cera-review/v1/cera/reviews/"
                f"{regenerate_review_id}/decision"
            ),
            method="POST",
            payload={"action": "regenerate"},
        )
        observed = (health, decline_review, regenerate_review, decline, regenerate)
        if any(value.status_code != 200 for value in observed):
            raise StateConflictError("qualification CERA review relay failed")
        if (
            decline_review.body.get("review_id") != decline_review_id
            or regenerate_review.body.get("review_id") != regenerate_review_id
            or decline.body.get("review_id") != decline_review_id
            or regenerate.body.get("review_id") != regenerate_review_id
        ):
            raise StateConflictError("qualification CERA review relay changed identity")
        return {
            "health_sha256": canonical_sha256(health.body),
            "decline_review_sha256": canonical_sha256(decline_review.body),
            "regenerate_review_sha256": canonical_sha256(regenerate_review.body),
            "decline_decision_sha256": canonical_sha256(decline.body),
            "regenerate_decision_sha256": canonical_sha256(regenerate.body),
            "provider_calls": 0,
        }

    def _relay(
        self,
        *,
        path: str,
        method: str,
        payload: dict[str, Any] | None = None,
    ) -> ClientResponseV1:
        started = time.perf_counter_ns()
        request = Request(
            self.origin + path,
            data=None if payload is None else json.dumps(payload).encode("utf-8"),
            headers={
                "Accept": "application/json",
                "X-Cera-Authorization": f"Bearer {self.cera_token}",
                "X-CSRF-Token": self._csrf_token,
                **({} if payload is None else {"Content-Type": "application/json"}),
            },
            method=method,
        )
        try:
            with self._opener.open(request, timeout=900) as response:
                status = response.status
                body = _decode_json_object(response.read())
        except HTTPError as exc:
            status = exc.code
            body = _decode_json_object(exc.read())
        return ClientResponseV1(
            transport="isolated_sillytavern_cera_review_relay",
            path=path,
            status_code=status,
            duration_ms=max(0, (time.perf_counter_ns() - started) // 1_000_000),
            body=body,
        )


def freeze(
    *,
    output_root: Path,
    fixture_path: Path,
    qualification_id: str,
    sillytavern_source: Path,
) -> dict[str, Any]:
    root = output_root.resolve()
    if root.exists():
        raise StateConflictError("qualification output root already exists")
    _assert_clean_exact_repository(ROOT)
    commit = _git("rev-parse", "HEAD")
    tree = _git("rev-parse", "HEAD^{tree}")
    root.mkdir(parents=True)
    isolated_root = root / "isolated_sillytavern"
    stage_qualification_sillytavern(
        sillytavern_source.resolve(),
        isolated_root,
        repository_proxy_root=(
            ROOT / "integrations" / "sillytavern" / "cera-review-proxy-plugin"
        ),
        repository_extension_root=(
            ROOT / "integrations" / "sillytavern" / "creator-review-extension"
        ),
    )
    manifest = build_qualification_manifest(
        repository_root=ROOT,
        qualification_id=qualification_id,
        source_commit=commit,
        source_tree=tree,
        fixture_path=fixture_path,
        repository_artifacts=_repository_artifacts(fixture_path),
        external_artifacts=_external_artifacts(isolated_root),
    )
    write_qualification_manifest(root / "QUALIFICATION_MANIFEST.json", manifest)
    verify_qualification_artifacts(manifest, repository_root=ROOT)
    return manifest


def provider_free_check(
    *,
    fixture_path: Path,
    manifest_path: Path | None = None,
) -> dict[str, Any]:
    fixtures = load_qualification_fixtures(fixture_path)
    result: dict[str, Any] = {
        "status": "provider_free_ready",
        "fixtures": len(fixtures),
        "backend": sum(value.phase is QualificationPhase.BACKEND for value in fixtures),
        "sillytavern": sum(value.phase is QualificationPhase.SILLYTAVERN for value in fixtures),
        "sol_ceiling": SOL_FAMILY_CEILING,
        "deepseek_http_operation_ceiling": DEEPSEEK_HTTP_OPERATION_CEILING,
        "deepseek_per_invocation_ceiling": DEEPSEEK_PER_INVOCATION_CEILING,
        "provider_calls": 0,
    }
    if manifest_path is not None:
        manifest = load_qualification_manifest(manifest_path)
        verify_qualification_artifacts(manifest, repository_root=ROOT)
        isolated = verify_qualification_sillytavern(
            manifest_path.resolve().parent / "isolated_sillytavern"
        )
        result["manifest_sha256"] = manifest["manifest_sha256"]
        result["isolated_sillytavern_manifest_sha256"] = isolated[
            "manifest_sha256"
        ]
    result["result_sha256"] = canonical_sha256(result)
    return result


@dataclass(slots=True)
class _LiveCeraService:
    runtime: Any
    server: ThreadingHTTPServer
    worker: Thread
    base_url: str

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.worker.join(timeout=10)
        self.runtime.close()


def _start_cera_service(
    *,
    runtime_root: Path,
    token: str,
    approved_origin: str,
    port: int,
    sol_ceiling: int,
    deepseek_ceiling: int,
    seed_runtime_root: Path | None = None,
) -> _LiveCeraService:
    runtime = build_live_runtime(
        runtime_root,
        sol_ceiling=sol_ceiling,
        deepseek_ceiling=deepseek_ceiling,
        deepseek_per_invocation_ceiling=DEEPSEEK_PER_INVOCATION_CEILING,
        seed_runtime_root=seed_runtime_root,
    )
    try:
        adapter = PiSceneHttpAdapter(
            coordinator=runtime.coordinator,
            request_context_provider=build_session_context_provider(
                runtime.store,
                initial_hanezawa_doorway_seed(),
                workspace_resolver=runtime.world_resolver,
            ),
            readable_debug=runtime.readable_debug,
            logic_route_resolver=lambda turn: accepted_logic_route(runtime.store, turn),
            full_model_controller=runtime.full_model_controller,
        )
        server = build_pi_scene_server(
            adapter,
            PiSceneServerConfigV1(
                host="127.0.0.1",
                port=port,
                authorization_token=token,
                approved_origins=(approved_origin,),
            ),
        )
        worker = Thread(target=server.serve_forever, daemon=True)
        worker.start()
        return _LiveCeraService(
            runtime=runtime,
            server=server,
            worker=worker,
            base_url=f"http://127.0.0.1:{port}",
        )
    except BaseException:
        runtime.close()
        raise


class _FakeRelayUpstream:
    def __init__(
        self,
        *,
        port: int,
        decline_review_id: str,
        regenerate_review_id: str,
    ) -> None:
        expected_reviews = {
            f"/v1/cera/reviews/{decline_review_id}": decline_review_id,
            f"/v1/cera/reviews/{regenerate_review_id}": regenerate_review_id,
        }

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                if self.path in {"/v1/health", "/health"}:
                    self._json({"status": "ok", "provider_calls": 0})
                elif self.path in expected_reviews:
                    review_id = expected_reviews[self.path]
                    self._json(
                        {
                            "review_id": review_id,
                            "state": "review_ready",
                            "story_state_committed": False,
                        }
                    )
                else:
                    self.send_error(404)

            def do_POST(self) -> None:
                decision_paths = {
                    f"/v1/cera/reviews/{decline_review_id}/decision": (
                        decline_review_id,
                        "decline",
                    ),
                    f"/v1/cera/reviews/{regenerate_review_id}/decision": (
                        regenerate_review_id,
                        "regenerate",
                    ),
                }
                if self.path not in decision_paths:
                    self.send_error(404)
                    return
                review_id, action = decision_paths[self.path]
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                if payload.get("action") != action:
                    self.send_error(422)
                    return
                self._json(
                    {
                        "status": "review_transitioned",
                        "review_id": review_id,
                        "state": "declined" if action == "decline" else "review_ready",
                        "story_state_committed": False,
                        "provider_calls": 0,
                    }
                )

            def _json(self, payload: dict[str, Any]) -> None:
                data = canonical_bytes(payload)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, _format: str, *_arguments: object) -> None:
                return

        self.server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self.worker = Thread(target=self.server.serve_forever, daemon=True)
        self._started = False

    def start(self) -> None:
        self.worker.start()
        self._started = True

    def close(self) -> None:
        if not self._started:
            return
        self.server.shutdown()
        self.server.server_close()
        self.worker.join(timeout=10)
        self._started = False


def _start_qualification_sillytavern(
    root: Path,
    *,
    port: int,
    cera_port: int,
    log_root: Path,
) -> subprocess.Popen[str]:
    node = shutil.which("node")
    if node is None:
        raise ContractValidationError("Node.js is unavailable")
    command = qualification_sillytavern_command(
        root,
        node_executable=Path(node),
        port=port,
    )
    if not 1 <= cera_port <= 65535 or cera_port == 5101:
        raise ContractValidationError("qualification CERA loopback port is invalid")
    environment = dict(os.environ)
    environment["CERA_REVIEW_LOOPBACK_ROOT"] = f"http://127.0.0.1:{cera_port}"
    log_root.mkdir(parents=True, exist_ok=False)
    stdout = (log_root / "stdout.log").open("w", encoding="utf-8")
    stderr = (log_root / "stderr.log").open("w", encoding="utf-8")
    try:
        process = subprocess.Popen(
            command,
            cwd=root,
            stdout=stdout,
            stderr=stderr,
            env=environment,
            text=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    finally:
        stdout.close()
        stderr.close()
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise StateConflictError("qualification SillyTavern exited during startup")
        try:
            with urlopen(f"http://127.0.0.1:{port}/csrf-token", timeout=1) as response:
                if response.status == 200:
                    return process
        except OSError:
            time.sleep(0.2)
    _stop_process(process)
    raise StateConflictError("qualification SillyTavern did not become ready")


def _available_port_excluding(*excluded: int) -> int:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port = int(sock.getsockname()[1])
    if port in excluded:
        return _available_port_excluding(*excluded)
    return port


def _reset_disposable_sillytavern_data(root: Path) -> None:
    isolated_root = root.resolve()
    data_root = (isolated_root / "data").resolve()
    if (
        data_root.parent != isolated_root
        or not data_root.is_dir()
        or data_root.is_symlink()
        or isolated_root.name != "isolated_sillytavern"
    ):
        raise StateConflictError("qualification SillyTavern data reset target is unsafe")
    shutil.rmtree(data_root)
    data_root.mkdir()


def _provider_free_chat_isolation_proof(
    runtime: Any,
    *,
    parent_session_id: str,
) -> dict[str, Any]:
    controls = LeanSceneRequestControlsV2(
        schema_version=LeanSceneRequestControlsV2.SCHEMA_VERSION,
        session_id=parent_session_id,
        scene_depth="auto",
        regeneration_key=None,
        character_autonomy="both",
        prompt_handling="adjustment",
        reasoning_effort="xhigh",
        scene_change=False,
        adult_craft_mode="off",
    )
    parent = runtime.world_resolver.resolve(controls)
    parent_head = runtime.store.load_head(
        world_id=parent.world_id,
        branch_id=parent.branch_id,
    )
    if parent_head.generation != 10:
        raise StateConflictError("SillyTavern campaign accepted head is not generation ten")
    new_controls = LeanSceneRequestControlsV2(
        schema_version=LeanSceneRequestControlsV2.SCHEMA_VERSION,
        session_id=parent_session_id + "-new",
        scene_depth="auto",
        regeneration_key=None,
        character_autonomy="both",
        prompt_handling="adjustment",
        reasoning_effort="xhigh",
        scene_change=False,
        adult_craft_mode="off",
    )
    new_chat = runtime.world_resolver.resolve(new_controls)
    new_head = runtime.store.load_head(
        world_id=new_chat.world_id,
        branch_id=new_chat.branch_id,
    )
    if new_chat.world_id == parent.world_id or new_head.generation != 0:
        raise StateConflictError("new chat inherited accepted campaign state")
    fork = runtime.world_resolver.fork(
        parent_session_id=parent_session_id,
        child_session_id=parent_session_id + "-fork",
        selected_parent_accepted_turn_id=parent_head.accepted_turn_id,
        selected_parent_accepted_head_sha256=parent_head.accepted_head_sha256,
    )
    fork_head = runtime.store.load_head(world_id=fork.world_id, branch_id=fork.branch_id)
    if (
        fork.world_id != parent.world_id
        or fork.branch_id == parent.branch_id
        or fork_head.generation != parent_head.generation
    ):
        raise StateConflictError("fork did not copy the selected accepted campaign state")
    body = {
        "schema_version": "cera.pi_scene.qualification_chat_isolation.v1",
        "parent_session_id_sha256": canonical_sha256(parent_session_id),
        "parent_world_id_sha256": canonical_sha256(parent.world_id),
        "parent_branch_id_sha256": canonical_sha256(parent.branch_id),
        "parent_generation": parent_head.generation,
        "new_chat_world_differs": True,
        "new_chat_generation": new_head.generation,
        "fork_world_matches_parent": True,
        "fork_branch_differs": True,
        "fork_generation": fork_head.generation,
        "provider_calls": 0,
    }
    return {**body, "binding_sha256": canonical_sha256(body)}


def live(
    *,
    output_root: Path,
    fixture_path: Path,
) -> dict[str, Any]:
    root = output_root.resolve()
    manifest_path = root / "QUALIFICATION_MANIFEST.json"
    manifest = load_qualification_manifest(manifest_path)
    _assert_clean_exact_repository(
        ROOT,
        expected_commit=str(manifest["source_commit"]),
        expected_tree=str(manifest["source_tree"]),
    )
    verify_qualification_artifacts(manifest, repository_root=ROOT)
    fixtures = load_qualification_fixtures(fixture_path)
    if manifest["fixture_set_sha256"] != __import__(
        "cera.serialization", fromlist=["bytes_sha256"]
    ).bytes_sha256(fixture_path.read_bytes()):
        raise StateConflictError("qualification fixture binding changed")
    runtime_root_a = root / "runtime-a"
    runtime_root_b = root / "runtime-b"
    evidence_root = root / "evidence"
    if runtime_root_a.exists() or runtime_root_b.exists() or evidence_root.exists():
        raise StateConflictError("qualification live runtime is not fresh")

    isolated_root = root / "isolated_sillytavern"
    isolated_manifest = verify_qualification_sillytavern(isolated_root)
    _atomic_write_json(
        root / "ISOLATED_SILLYTAVERN_BINDING.json",
        {
            "schema_version": "cera.pi_scene.qualification_sillytavern_binding.v1",
            "manifest_sha256": isolated_manifest["manifest_sha256"],
            "user_data_copied": isolated_manifest["user_data_copied"],
            "only_repository_cera_integrations": isolated_manifest[
                "only_repository_cera_integrations"
            ],
        },
    )
    cera_token = secrets.token_urlsafe(32)
    decline_probe_id = "review-0123456789abcdef0123456789ab"
    regenerate_probe_id = "review-fedcba9876543210fedcba987654"
    relay_port = _available_port_excluding(5101)
    relay_st_port = _available_port_excluding(5101, relay_port)
    fake_relay = _FakeRelayUpstream(
        port=relay_port,
        decline_review_id=decline_probe_id,
        regenerate_review_id=regenerate_probe_id,
    )
    relay_process: subprocess.Popen[str] | None = None
    try:
        fake_relay.start()
        relay_process = _start_qualification_sillytavern(
            isolated_root,
            port=relay_st_port,
            cera_port=relay_port,
            log_root=root / "relay-preflight-sillytavern-logs",
        )
        relay_client = IsolatedSillyTavernQualificationClient(
            origin=f"http://127.0.0.1:{relay_st_port}",
            cera_base_url=f"http://127.0.0.1:{relay_port}",
            cera_token=cera_token,
        )
        relay_proof = relay_client.probe_relay(
            decline_review_id=decline_probe_id,
            regenerate_review_id=regenerate_probe_id,
        )
        _atomic_write_json(root / "REVIEW_RELAY_PREFLIGHT.json", relay_proof)
    finally:
        fake_relay.close()
        if relay_process is not None:
            _stop_process(relay_process)
    _reset_disposable_sillytavern_data(isolated_root)
    verify_qualification_sillytavern(isolated_root)

    cera_port = _available_port_excluding(5101, relay_port, relay_st_port)
    st_port = _available_port_excluding(5101, relay_port, relay_st_port, cera_port)
    st_origin = f"http://127.0.0.1:{st_port}"
    st_process = _start_qualification_sillytavern(
        isolated_root,
        port=st_port,
        cera_port=cera_port,
        log_root=root / "sillytavern-logs",
    )
    service: _LiveCeraService | None = None
    try:
        service = _start_cera_service(
            runtime_root=runtime_root_a,
            token=cera_token,
            approved_origin=st_origin,
            port=cera_port,
            sol_ceiling=SOL_FAMILY_CEILING,
            deepseek_ceiling=DEEPSEEK_HTTP_OPERATION_CEILING,
        )
        direct = DirectCeraQualificationClient(
            base_url=service.base_url,
            token=cera_token,
        )
        readiness = direct.verify_ready()
        _atomic_write_json(root / "LOOPBACK_PREFLIGHT.json", readiness)
        runner = FullModelQualificationRunner(
            manifest=manifest,
            runtime_root=runtime_root_a,
            evidence_root=evidence_root,
        )
        backend_campaign = runner.start_phase(QualificationPhase.BACKEND, fixtures)
        backend_campaign.run_segment(
            client=direct,
            runtime_root=runtime_root_a,
            turn_count=20,
        )
        backend = backend_campaign.finish()

        sillytavern = IsolatedSillyTavernQualificationClient(
            origin=st_origin,
            cera_base_url=service.base_url,
            cera_token=cera_token,
        )
        st_campaign = runner.start_phase(QualificationPhase.SILLYTAVERN, fixtures)
        st_campaign.run_segment(
            client=sillytavern,
            runtime_root=runtime_root_a,
            turn_count=5,
        )
        service.close()
        service = None
        remaining_sol = SOL_FAMILY_CEILING - (
            backend_campaign.sol_operations + st_campaign.sol_operations
        )
        remaining_deepseek = DEEPSEEK_HTTP_OPERATION_CEILING - (
            backend_campaign.deepseek_operations + st_campaign.deepseek_operations
        )
        if remaining_sol < 1 or remaining_deepseek < DEEPSEEK_PER_INVOCATION_CEILING:
            raise StateConflictError("qualification restart lacks remaining provider authority")
        service = _start_cera_service(
            runtime_root=runtime_root_b,
            token=cera_token,
            approved_origin=st_origin,
            port=cera_port,
            sol_ceiling=SOL_FAMILY_CEILING,
            deepseek_ceiling=DEEPSEEK_HTTP_OPERATION_CEILING,
            seed_runtime_root=runtime_root_a,
        )
        restarted_sillytavern = IsolatedSillyTavernQualificationClient(
            origin=st_origin,
            cera_base_url=service.base_url,
            cera_token=cera_token,
        )
        st_campaign.run_segment(
            client=restarted_sillytavern,
            runtime_root=runtime_root_b,
            turn_count=5,
            restarted=True,
        )
        st_result = st_campaign.finish()
        isolation_proof = _provider_free_chat_isolation_proof(
            service.runtime,
            parent_session_id=st_campaign.session_id,
        )
        _atomic_write_json(root / "CHAT_ISOLATION_PROOF.json", isolation_proof)
        if st_process.poll() is not None:
            raise StateConflictError("isolated SillyTavern exited during qualification")
        result = {
            "schema_version": "cera.pi_scene.full_model_complete_qualification.v1",
            "qualification_id": manifest["qualification_id"],
            "manifest_sha256": manifest["manifest_sha256"],
            "status": "passed",
            "backend_result_sha256": backend["result_sha256"],
            "sillytavern_result_sha256": st_result["result_sha256"],
            "backend_passed": backend["passed_fixtures"],
            "sillytavern_passed": st_result["passed_fixtures"],
            "sol_operations": (int(backend["sol_operations"]) + int(st_result["sol_operations"])),
            "deepseek_http_operations": (
                int(backend["deepseek_http_operations"])
                + int(st_result["deepseek_http_operations"])
            ),
            "backend_sequential_turns": backend["passed_fixtures"],
            "sillytavern_sequential_turns": st_result["passed_fixtures"],
            "sillytavern_runtime_restarts": st_result["restart_count"],
            "review_relay_preflight_sha256": canonical_sha256(relay_proof),
            "chat_isolation_proof_sha256": canonical_sha256(isolation_proof),
            "isolated_sillytavern_manifest_sha256": isolated_manifest["manifest_sha256"],
            "installed_sillytavern_mutated": False,
        }
        result["result_sha256"] = canonical_sha256(result)
        _atomic_write_json(root / "QUALIFICATION_RESULT.json", result)
        return result
    finally:
        if service is not None:
            service.close()
        _stop_process(st_process)


def _post_json(
    url: str,
    *,
    token: str,
    payload: dict[str, Any],
    transport: str,
    path: str = "/v1/chat/completions",
) -> ClientResponseV1:
    started = time.perf_counter_ns()
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=900) as response:
            status = response.status
            body = _decode_json_object(response.read())
    except HTTPError as exc:
        status = exc.code
        body = _decode_json_object(exc.read())
    duration_ms = max(0, (time.perf_counter_ns() - started) // 1_000_000)
    return ClientResponseV1(
        transport=transport,
        path=path,
        status_code=status,
        duration_ms=duration_ms,
        body=body,
    )


def _get_json(url: str, *, token: str) -> dict[str, Any]:
    request = Request(
        url,
        headers={"Authorization": f"Bearer {token}"},
        method="GET",
    )
    with urlopen(request, timeout=30) as response:
        if response.status != 200:
            raise StateConflictError("qualification preflight HTTP status changed")
        return _decode_json_object(response.read())


def _decode_json_object(data: bytes) -> dict[str, Any]:
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StateConflictError("qualification HTTP response is not JSON") from exc
    if not isinstance(value, dict):
        raise StateConflictError("qualification HTTP response is not an object")
    return value


def _assert_clean_exact_repository(
    root: Path,
    *,
    expected_commit: str | None = None,
    expected_tree: str | None = None,
) -> None:
    status = _git("status", "--porcelain", "--untracked-files=all")
    if status:
        raise StateConflictError("qualification requires an exact clean repository")
    commit = _git("rev-parse", "HEAD")
    tree = _git("rev-parse", "HEAD^{tree}")
    if expected_commit is not None and commit != expected_commit:
        raise StateConflictError("qualification source commit changed after freeze")
    if expected_tree is not None and tree != expected_tree:
        raise StateConflictError("qualification source tree changed after freeze")


def _git(*arguments: str) -> str:
    completed = subprocess.run(
        ("git", "-C", str(ROOT), *arguments),
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return completed.stdout.strip()


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    if path.exists():
        raise StateConflictError(f"qualification artifact already exists: {path.name}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("xb") as stream:
        stream.write(canonical_bytes(payload) + b"\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="mode", required=True)

    freeze_parser = subparsers.add_parser("freeze")
    freeze_parser.add_argument("--output-root", type=Path, required=True)
    freeze_parser.add_argument("--qualification-id", required=True)
    freeze_parser.add_argument("--fixture-set", type=Path, default=DEFAULT_FIXTURES)
    freeze_parser.add_argument(
        "--sillytavern-source",
        type=Path,
        default=DEFAULT_SILLYTAVERN,
    )

    check_parser = subparsers.add_parser("provider-free-check")
    check_parser.add_argument("--fixture-set", type=Path, default=DEFAULT_FIXTURES)
    check_parser.add_argument("--manifest", type=Path)

    live_parser = subparsers.add_parser("live")
    live_parser.add_argument("--output-root", type=Path, required=True)
    live_parser.add_argument("--fixture-set", type=Path, default=DEFAULT_FIXTURES)

    args = parser.parse_args(argv)
    if args.mode == "freeze":
        result = freeze(
            output_root=args.output_root,
            fixture_path=args.fixture_set,
            qualification_id=args.qualification_id,
            sillytavern_source=args.sillytavern_source,
        )
    elif args.mode == "provider-free-check":
        result = provider_free_check(
            fixture_path=args.fixture_set,
            manifest_path=args.manifest,
        )
    else:
        result = live(
            output_root=args.output_root,
            fixture_path=args.fixture_set,
        )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
