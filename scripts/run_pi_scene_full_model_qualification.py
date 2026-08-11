"""Freeze and run the full-model CERA backend/SillyTavern qualification.

The live command is deliberately two-step.  ``freeze`` must complete against a
clean exact Git tree before ``live`` can construct provider transports.  The
live phase performs 10 ordinary plus 10 adult direct-backend requests, then 5
ordinary plus 5 adult requests through a disposable SillyTavern copy.  Each
phase is one retained ``cera-alpha`` story branch. No automatic retry or
fallback exists. Any of the seven provider stages may expose an exact generated
manual ``provider_retry`` action, with at most two Retry actions and three
attempts for that unique stage occurrence. Exact manual ``resume_prepared`` and
one nonrecursive Recorder ``repair_recording`` control have separate budgets.
Automatic behavior is GET-only; the runner waits for one hash-only external
approval, revalidates authority, POSTs once, and reconciles through authenticated
GET. One externally authorized Regenerate may follow a noncritical rejected
first pass; every earlier outcome remains in evidence.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import importlib.util
import json
import os
import secrets
import shutil
import subprocess
import sys
import time
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from http.cookiejar import CookieJar
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from math import isfinite
from pathlib import Path
from threading import Thread
from typing import Any, cast
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, Request, build_opener, urlopen

from cera.continuous.path_policy import preflight_windows_legacy_paths
from cera.errors import ContractValidationError, StateConflictError
from cera.generated.ordinary_review_contracts_v2 import (
    validate_ordinary_review_decision_v2,
    validate_ordinary_review_lifecycle_v1,
    validate_ordinary_review_v2,
)
from cera.generated.provider_stage_retry_contracts_v1 import (
    validate_provider_stage_retry_action_v1,
    validate_provider_stage_retry_status_envelope_v1,
)
from cera.pi_scene.context import initial_hanezawa_doorway_seed
from cera.pi_scene.http import PiSceneHttpAdapter, PiSceneServerConfigV1, build_pi_scene_server
from cera.pi_scene.http_contracts import LeanSceneRequestControlsV3
from cera.pi_scene.provider_stage_retry_http import (
    validate_ordinary_successor_completion,
)
from cera.pi_scene.qualification import (
    DEEPSEEK_HTTP_OPERATION_CEILING,
    DEEPSEEK_PER_INVOCATION_CEILING,
    QUALIFICATION_HTTP_HARD_TIMEOUT_SECONDS,
    QUALIFICATION_MANIFEST_SCHEMA,
    QUALIFICATION_MAX_SEQUENTIAL_PROVIDER_STAGES,
    QUALIFICATION_PROVIDER_STAGE_HARD_TIMEOUT_SECONDS,
    SOL_FAMILY_CEILING,
    ClientResponseV1,
    FullModelQualificationRunner,
    ManualActionRequiredError,
    QualificationFixtureV1,
    QualificationFixtureV2,
    QualificationManualActionAuthorizationV1,
    QualificationManualActionRequestV1,
    QualificationPhase,
    build_qualification_manifest,
    load_qualification_fixtures,
    load_qualification_manifest,
    qualification_fixture_manifest_metadata,
    verify_qualification_artifacts,
    write_qualification_manifest,
)
from cera.pi_scene.qualification_isolation import (
    MANIFEST_NAME as ISOLATED_ST_MANIFEST_NAME,
)
from cera.pi_scene.qualification_isolation import (
    qualification_sillytavern_command,
    run_staged_sillytavern_node_suites,
    stage_qualification_sillytavern,
    verify_qualification_sillytavern,
)
from cera.serialization import canonical_bytes, canonical_sha256, re_is_sha256, text_sha256
from scripts.run_pi_scene_lean_server import (
    DEFAULT_PI,
    DEFAULT_SILLYTAVERN,
    _stop_process,
    accepted_logic_route,
    build_live_runtime,
    build_session_context_provider,
)

ROOT = Path(__file__).resolve().parents[1]
LEGACY_FIXTURES_V1 = ROOT / "evaluation" / "fixtures" / "pi_scene_full_model_qualification_v1.json"
DEFAULT_FIXTURES = ROOT / "evaluation" / "fixtures" / "pi_scene_full_model_qualification_v2.json"
PI_PACKAGE_ROOT = Path(
    r"C:\Users\Ted\AppData\Roaming\npm\node_modules\@earendil-works\pi-coding-agent"
)

_CODEX_RUNTIME_DISTRIBUTIONS = (
    ("openai_codex", "openai-codex"),
    ("codex_cli_bin", "openai-codex-cli-bin"),
    ("mcp", "mcp"),
)

# Preserve one explicit relationship among the entrypoint-level names used by
# frozen qualification checks while sourcing all values from the runner.
_QUALIFICATION_TIMEOUT_CONTRACT = (
    QUALIFICATION_MAX_SEQUENTIAL_PROVIDER_STAGES,
    QUALIFICATION_PROVIDER_STAGE_HARD_TIMEOUT_SECONDS,
    QUALIFICATION_HTTP_HARD_TIMEOUT_SECONDS,
)

DEFAULT_MANUAL_ACTION_WAIT_SECONDS = 86_400.0
_MANUAL_ACTION_RECEIPT_DIRECTORY = "manual_action_receipts"
_MANUAL_PENDING_FIELDS = frozenset(
    {
        "schema_version",
        "qualification_id_sha256",
        "phase",
        "turn_index",
        "fixture_id_sha256",
        "action_family",
        "action_kind",
        "action_id_sha256",
        "exact_action_sha256",
        "chain_id_sha256",
        "review_id_sha256",
        "candidate_sha256",
        "authority_sha256",
        "stage",
        "provider",
        "model_family",
        "retry_action_ordinal",
        "status",
        "checkpoint_sha256",
    }
)


def _manual_receipt_paths(output_root: Path, checkpoint_sha256: str) -> tuple[Path, Path, Path]:
    if not re_is_sha256(checkpoint_sha256):
        raise ContractValidationError("manual-action checkpoint hash is invalid")
    root = output_root.resolve() / _MANUAL_ACTION_RECEIPT_DIRECTORY
    return (
        root / f"{checkpoint_sha256}.pending.json",
        root / f"{checkpoint_sha256}.approval.json",
        root / f"{checkpoint_sha256}.consumed.json",
    )


def _read_manual_receipt(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise StateConflictError(f"manual-action receipt is unreadable: {path.name}") from exc
    if len(raw) > 32_768:
        raise StateConflictError("manual-action receipt exceeded its closed size")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StateConflictError("manual-action receipt is not valid JSON") from exc
    if not isinstance(value, dict):
        raise StateConflictError("manual-action receipt is not an object")
    return value


def _validate_pending_manual_receipt(value: Mapping[str, Any]) -> dict[str, Any]:
    pending = dict(value)
    if set(pending) != _MANUAL_PENDING_FIELDS:
        raise StateConflictError("manual-action pending receipt fields changed")
    checkpoint = pending.get("checkpoint_sha256")
    binding = {
        key: item for key, item in pending.items() if key not in {"status", "checkpoint_sha256"}
    }
    if (
        pending.get("schema_version") != QualificationManualActionRequestV1.SCHEMA_VERSION
        or pending.get("status") != "manual_action_required"
        or not isinstance(checkpoint, str)
        or not re_is_sha256(checkpoint)
        or canonical_sha256(binding) != checkpoint
        or pending.get("phase") not in {phase.value for phase in QualificationPhase}
        or type(pending.get("turn_index")) is not int
        or cast(int, pending["turn_index"]) < 1
        or pending.get("action_family")
        not in {"provider_stage_control", "semantic_regenerate", "recording_repair"}
        or not isinstance(pending.get("action_kind"), str)
    ):
        raise StateConflictError("manual-action pending receipt binding changed")
    for key in (
        "qualification_id_sha256",
        "fixture_id_sha256",
        "action_id_sha256",
        "exact_action_sha256",
        "authority_sha256",
    ):
        if not isinstance(pending.get(key), str) or not re_is_sha256(cast(str, pending[key])):
            raise StateConflictError("manual-action pending receipt hash changed")
    for key in ("chain_id_sha256", "review_id_sha256", "candidate_sha256"):
        item = pending.get(key)
        if item is not None and (not isinstance(item, str) or not re_is_sha256(item)):
            raise StateConflictError("manual-action optional receipt hash changed")
    family = cast(str, pending["action_family"])
    kind = cast(str, pending["action_kind"])
    allowed_kinds = {
        "provider_stage_control": {
            "provider_retry",
            "resume_prepared",
            "repair_recording",
        },
        "semantic_regenerate": {"regenerate"},
        "recording_repair": {"repair_recording"},
    }
    provider_context = (
        pending["stage"],
        pending["provider"],
        pending["model_family"],
    )
    if kind not in allowed_kinds[family]:
        raise StateConflictError("manual-action pending family and kind disagree")
    if family == "provider_stage_control":
        if (
            pending["chain_id_sha256"] is None
            or pending["review_id_sha256"] is not None
            or not all(isinstance(item, str) and item.strip() for item in provider_context)
            or (
                kind == "provider_retry"
                and (
                    type(pending["retry_action_ordinal"]) is not int
                    or pending["retry_action_ordinal"] not in {1, 2}
                )
            )
            or (kind != "provider_retry" and pending["retry_action_ordinal"] is not None)
        ):
            raise StateConflictError("manual provider-stage pending context changed")
    elif (
        pending["chain_id_sha256"] is not None
        or pending["review_id_sha256"] is None
        or pending["candidate_sha256"] is None
        or any(item is not None for item in provider_context)
        or pending["retry_action_ordinal"] is not None
    ):
        raise StateConflictError("manual review-action pending context changed")
    return pending


def _manual_approval_projection(pending: Mapping[str, Any]) -> dict[str, Any]:
    safe = _validate_pending_manual_receipt(pending)
    return {
        "schema_version": "cera.pi_scene.qualification_manual_action_approval.v1",
        "status": "externally_authorized",
        "checkpoint_sha256": safe["checkpoint_sha256"],
        "pending_receipt_sha256": canonical_sha256(safe),
        **{
            key: safe[key]
            for key in (
                "qualification_id_sha256",
                "phase",
                "turn_index",
                "fixture_id_sha256",
                "action_family",
                "action_kind",
                "action_id_sha256",
                "exact_action_sha256",
                "chain_id_sha256",
                "review_id_sha256",
                "candidate_sha256",
                "authority_sha256",
                "stage",
                "provider",
                "model_family",
                "retry_action_ordinal",
            )
        },
    }


def _manual_consumed_projection(approval: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "cera.pi_scene.qualification_manual_action_consumed.v1",
        "status": "consumed_for_one_dispatch",
        "checkpoint_sha256": approval["checkpoint_sha256"],
        "approval_receipt_sha256": canonical_sha256(approval),
        **{
            key: approval[key]
            for key in (
                "qualification_id_sha256",
                "phase",
                "turn_index",
                "fixture_id_sha256",
                "action_family",
                "action_kind",
                "action_id_sha256",
                "exact_action_sha256",
                "chain_id_sha256",
                "review_id_sha256",
                "candidate_sha256",
                "authority_sha256",
                "stage",
                "provider",
                "model_family",
                "retry_action_ordinal",
            )
        },
    }


class HashOnlyFileManualActionAuthorizer:
    """Wait for one external hash-only approval and consume it exactly once."""

    def __init__(self, *, output_root: Path, timeout_seconds: float) -> None:
        if (
            type(timeout_seconds) not in {int, float}
            or not isfinite(timeout_seconds)
            or timeout_seconds < 0
        ):
            raise ContractValidationError("manual-action wait timeout is not bounded")
        self.output_root = output_root.resolve()
        self.timeout_seconds = float(timeout_seconds)

    def authorize(
        self,
        request: QualificationManualActionRequestV1,
    ) -> QualificationManualActionAuthorizationV1 | None:
        pending_path, approval_path, consumed_path = _manual_receipt_paths(
            self.output_root,
            request.checkpoint_sha256,
        )
        expected_pending = request.pending_projection()
        if pending_path.exists():
            if _read_manual_receipt(pending_path) != expected_pending:
                raise StateConflictError("manual-action pending receipt changed")
        else:
            _atomic_write_json(pending_path, expected_pending)
        if consumed_path.exists():
            raise StateConflictError("manual-action checkpoint was already consumed")

        expected_approval = _manual_approval_projection(expected_pending)
        deadline = time.monotonic() + self.timeout_seconds
        while True:
            if approval_path.exists():
                observed = _read_manual_receipt(approval_path)
                if observed != expected_approval:
                    raise StateConflictError("manual-action approval receipt changed")
                return QualificationManualActionAuthorizationV1.approve(
                    request,
                    authorization_source="external_manual_receipt",
                    authorization_id="manual-receipt-" + canonical_sha256(observed),
                )
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ManualActionRequiredError(request.checkpoint_sha256)
            time.sleep(min(0.25, remaining))

    def mark_consumed(
        self,
        request: QualificationManualActionRequestV1,
        authorization: QualificationManualActionAuthorizationV1,
    ) -> None:
        pending_path, approval_path, consumed_path = _manual_receipt_paths(
            self.output_root,
            request.checkpoint_sha256,
        )
        if authorization.authorization_source != "external_manual_receipt":
            raise StateConflictError("manual-action receipt source changed before consumption")
        pending = _read_manual_receipt(pending_path)
        expected_approval = _manual_approval_projection(pending)
        if _read_manual_receipt(approval_path) != expected_approval:
            raise StateConflictError("manual-action approval changed before consumption")
        if consumed_path.exists():
            raise StateConflictError("manual-action approval was already consumed")
        _atomic_write_json(consumed_path, _manual_consumed_projection(expected_approval))


def approve_manual_action(*, output_root: Path, checkpoint_sha256: str) -> dict[str, Any]:
    pending_path, approval_path, consumed_path = _manual_receipt_paths(
        output_root,
        checkpoint_sha256,
    )
    if not pending_path.exists():
        raise StateConflictError("manual-action pending checkpoint does not exist")
    if approval_path.exists() or consumed_path.exists():
        raise StateConflictError("manual-action checkpoint is already approved or consumed")
    pending = _validate_pending_manual_receipt(_read_manual_receipt(pending_path))
    if pending["checkpoint_sha256"] != checkpoint_sha256:
        raise StateConflictError("manual-action checkpoint filename binding changed")
    approval = _manual_approval_projection(pending)
    _atomic_write_json(approval_path, approval)
    result = {
        "schema_version": "cera.pi_scene.qualification_manual_action_approval_result.v1",
        "status": "externally_authorized",
        "checkpoint_sha256": checkpoint_sha256,
        "approval_receipt_sha256": canonical_sha256(approval),
        "phase": pending["phase"],
        "turn_index": pending["turn_index"],
        "action_kind": pending["action_kind"],
    }
    return {**result, "result_sha256": canonical_sha256(result)}


def _preflight_runtime_path_budget(output_root: Path) -> None:
    """Reject a qualification root that cannot safely host request custody."""

    journal_parent = (
        output_root.resolve()
        / "runtime-a"
        / "accepted_world"
        / "http_request_journal"
        / "PROTECTED_ADULT"
        / f"s-{'f' * 16}"
        / f"w-{'f' * 16}"
        / f"b-{'f' * 16}"
    )
    target = journal_parent / f"request-{'f' * 64}.json"
    preflight_windows_legacy_paths(
        target,
        target.with_suffix(".claim"),
        journal_parent / f".request-journal-{'f' * 32}.tmp",
        label="qualification request journal",
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
        "fixtures": (
            relative_fixture,
            Path("evaluation/fixtures/pi_scene_full_model_qualification_v1.json"),
        ),
        "genesis": (Path("genesis/packages"),),
        "adult_craft": (Path("adult/catalog/adult_craft_v1"),),
    }


def _installed_distribution_artifacts(
    import_name: str,
    distribution_name: str,
) -> tuple[Path, Path]:
    spec = importlib.util.find_spec(import_name)
    if spec is None or spec.submodule_search_locations is None:
        raise ContractValidationError(
            f"qualification provider runtime package is unavailable: {import_name}"
        )
    package_locations = tuple(Path(value).resolve() for value in spec.submodule_search_locations)
    if len(package_locations) != 1 or not package_locations[0].is_dir():
        raise ContractValidationError(
            f"qualification provider runtime package location is ambiguous: {import_name}"
        )

    try:
        distribution = importlib.metadata.distribution(distribution_name)
    except importlib.metadata.PackageNotFoundError as exc:
        raise ContractValidationError(
            f"qualification provider runtime distribution is unavailable: {distribution_name}"
        ) from exc
    files = distribution.files
    if files is None:
        raise ContractValidationError(
            f"qualification provider runtime distribution has no file inventory: "
            f"{distribution_name}"
        )
    metadata_relative_roots = {
        Path(str(value)).parts[0]
        for value in files
        if Path(str(value)).parts and Path(str(value)).parts[0].endswith(".dist-info")
    }
    metadata_roots = {
        Path(str(distribution.locate_file(value))).resolve() for value in metadata_relative_roots
    }
    if len(metadata_roots) != 1:
        raise ContractValidationError(
            f"qualification provider runtime metadata location is ambiguous: {distribution_name}"
        )
    metadata_root = next(iter(metadata_roots))
    if not metadata_root.is_dir() or metadata_root.parent != package_locations[0].parent:
        raise ContractValidationError(
            f"qualification provider runtime package/metadata boundary changed: {distribution_name}"
        )
    return package_locations[0], metadata_root


def _external_artifacts(isolated_sillytavern_root: Path) -> dict[str, tuple[Path, ...]]:
    node = shutil.which("node")
    if node is None:
        raise ContractValidationError("Node.js is unavailable for isolated SillyTavern")
    python_executable = Path(sys.executable).resolve()
    codex_runtime: list[Path] = []
    for import_name, distribution_name in _CODEX_RUNTIME_DISTRIBUTIONS:
        codex_runtime.extend(_installed_distribution_artifacts(import_name, distribution_name))
    return {
        "frozen_python_interpreter": (python_executable,),
        "provider_runtime_tools": (
            python_executable,
            DEFAULT_PI,
            PI_PACKAGE_ROOT / "package.json",
            PI_PACKAGE_ROOT / "dist",
            Path(node),
            *codex_runtime,
        ),
        "sillytavern_executable_tree_binding": (
            isolated_sillytavern_root / ISOLATED_ST_MANIFEST_NAME,
        ),
    }


def _assert_frozen_python_interpreter(manifest: dict[str, Any]) -> None:
    categories = manifest.get("artifact_categories")
    entries = categories.get("frozen_python_interpreter") if isinstance(categories, dict) else None
    if not isinstance(entries, list) or len(entries) != 1:
        raise StateConflictError("qualification frozen Python interpreter binding is missing")
    entry = entries[0]
    if (
        not isinstance(entry, dict)
        or entry.get("location") != "external"
        or not isinstance(entry.get("path"), str)
    ):
        raise StateConflictError("qualification frozen Python interpreter binding is invalid")
    frozen = Path(entry["path"]).resolve()
    current = Path(sys.executable).resolve()
    if current != frozen:
        raise StateConflictError(
            "qualification live process is not using the frozen Python interpreter"
        )


def _frozen_node_executable(manifest: dict[str, Any]) -> Path:
    current = shutil.which("node")
    if current is None:
        raise StateConflictError("qualification frozen Node.js executable is unavailable")
    resolved = Path(current).resolve()
    categories = manifest.get("artifact_categories")
    entries = categories.get("provider_runtime_tools") if isinstance(categories, dict) else None
    if not isinstance(entries, list) or not any(
        isinstance(entry, dict)
        and entry.get("location") == "external"
        and isinstance(entry.get("path"), str)
        and Path(entry["path"]).resolve() == resolved
        for entry in entries
    ):
        raise StateConflictError("qualification current Node.js is not frozen in the manifest")
    return resolved


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
        return self.review_action(
            fixture=fixture,
            review_id=review_id,
            action={"action": "regenerate"},
        )

    def review(self, *, review_id: str) -> ClientResponseV1:
        path = f"/v1/cera/reviews/{review_id}"
        return _get_json_response(
            self.base_url + path,
            token=self.token,
            transport="direct_cera_review_status",
            path=path,
        )

    def review_action(
        self,
        *,
        fixture: QualificationFixtureV1,
        review_id: str,
        action: dict[str, Any] | Any,
    ) -> ClientResponseV1:
        del fixture
        path = f"/v1/cera/reviews/{review_id}/decision"
        return _post_json(
            self.base_url + f"/v1/cera/reviews/{review_id}/decision",
            token=self.token,
            payload=dict(action),
            transport="direct_cera_review_action",
            path=path,
        )

    def terminal_review_decision(self, *, review_id: str) -> ClientResponseV1:
        path = f"/v1/cera/reviews/{review_id}/terminal-decision"
        return _get_json_response(
            self.base_url + path,
            token=self.token,
            transport="direct_cera_terminal_review_decision",
            path=path,
        )

    def provider_stage_retry_status(self, *, chain_id: str) -> ClientResponseV1:
        path = f"/v1/cera/provider-stage-retries/{chain_id}"
        return _get_json_response(
            self.base_url + path,
            token=self.token,
            transport="direct_cera_provider_stage_retry_status",
            path=path,
        )

    def provider_stage_retry_action(
        self,
        *,
        chain_id: str,
        action: dict[str, Any] | Any,
    ) -> ClientResponseV1:
        exact = validate_provider_stage_retry_action_v1(action)
        if exact["chain_id"] != chain_id:
            raise StateConflictError("qualification provider-stage action changed chain")
        path = f"/v1/cera/provider-stage-retries/{chain_id}/actions/{exact['action_id']}"
        return _post_json(
            self.base_url + path,
            token=self.token,
            payload=dict(exact),
            transport="direct_cera_provider_stage_retry_action",
            path=path,
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
                f"cera_review_mode: {value['cera_review_mode']}",
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
            with self._opener.open(
                request,
                timeout=QUALIFICATION_HTTP_HARD_TIMEOUT_SECONDS,
            ) as response:
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
        return self.review_action(
            fixture=fixture,
            review_id=review_id,
            action={"action": "regenerate"},
        )

    def review(self, *, review_id: str) -> ClientResponseV1:
        return self._relay(
            path=f"/api/plugins/cera-review/v1/cera/reviews/{review_id}",
            method="GET",
        )

    def review_action(
        self,
        *,
        fixture: QualificationFixtureV1,
        review_id: str,
        action: dict[str, Any] | Any,
    ) -> ClientResponseV1:
        del fixture
        path = f"/api/plugins/cera-review/v1/cera/reviews/{review_id}/decision"
        return self._relay(
            path=path,
            method="POST",
            payload=dict(action),
        )

    def terminal_review_decision(self, *, review_id: str) -> ClientResponseV1:
        return self._relay(
            path=(f"/api/plugins/cera-review/v1/cera/reviews/{review_id}/terminal-decision"),
            method="GET",
        )

    def provider_stage_retry_status(self, *, chain_id: str) -> ClientResponseV1:
        return self._relay(
            path=(f"/api/plugins/cera-review/v1/cera/provider-stage-retries/{chain_id}"),
            method="GET",
        )

    def provider_stage_retry_action(
        self,
        *,
        chain_id: str,
        action: dict[str, Any] | Any,
    ) -> ClientResponseV1:
        exact = validate_provider_stage_retry_action_v1(action)
        if exact["chain_id"] != chain_id:
            raise StateConflictError("qualification provider-stage relay action changed chain")
        return self._relay(
            path=(
                "/api/plugins/cera-review/v1/cera/provider-stage-retries/"
                f"{chain_id}/actions/{exact['action_id']}"
            ),
            method="POST",
            payload=dict(exact),
        )

    def probe_provider_stage_relay(
        self,
        *,
        decline_review_id: str,
        regenerate_review_id: str,
        exhaustion_chain_id: str,
        successor_source_chain_id: str,
        successor_later_chain_id: str,
        recovery_chain_id: str,
        blocked_chain_id: str,
        resume_prepared_chain_id: str,
        repair_source_chain_id: str,
        repair_successor_chain_id: str,
        repair_terminal_chain_id: str,
    ) -> dict[str, Any]:
        health = self._relay(path="/api/plugins/cera-review/health", method="GET")
        decline_review = self._relay(
            path=f"/api/plugins/cera-review/v1/cera/reviews/{decline_review_id}",
            method="GET",
        )
        regenerate_review = self._relay(
            path=f"/api/plugins/cera-review/v1/cera/reviews/{regenerate_review_id}",
            method="GET",
        )
        decline = self._relay(
            path=f"/api/plugins/cera-review/v1/cera/reviews/{decline_review_id}/decision",
            method="POST",
            payload={"action": "decline"},
        )
        regenerate = self._relay(
            path=f"/api/plugins/cera-review/v1/cera/reviews/{regenerate_review_id}/decision",
            method="POST",
            payload={"action": "regenerate"},
        )
        decline_terminal = self.terminal_review_decision(review_id=decline_review_id)
        regenerate_terminal = self.terminal_review_decision(review_id=regenerate_review_id)

        exhaustion_1 = self.provider_stage_retry_status(chain_id=exhaustion_chain_id)
        exhaustion_1_envelope = validate_provider_stage_retry_status_envelope_v1(exhaustion_1.body)
        exhaustion_1_action = validate_provider_stage_retry_action_v1(
            exhaustion_1_envelope["actions"][0]
        )
        ambiguous_post = self.provider_stage_retry_action(
            chain_id=exhaustion_chain_id,
            action=exhaustion_1_action,
        )
        exhaustion_2 = self.provider_stage_retry_status(chain_id=exhaustion_chain_id)
        exhaustion_2_envelope = validate_provider_stage_retry_status_envelope_v1(exhaustion_2.body)
        exhaustion_2_action = validate_provider_stage_retry_action_v1(
            exhaustion_2_envelope["actions"][0]
        )
        exhaustion_post = self.provider_stage_retry_action(
            chain_id=exhaustion_chain_id,
            action=exhaustion_2_action,
        )
        exhaustion_terminal = self.provider_stage_retry_status(chain_id=exhaustion_chain_id)
        terminal_envelope = validate_provider_stage_retry_status_envelope_v1(
            exhaustion_terminal.body
        )

        successor_source = self.provider_stage_retry_status(chain_id=successor_source_chain_id)
        source_envelope = validate_provider_stage_retry_status_envelope_v1(successor_source.body)
        source_action = validate_provider_stage_retry_action_v1(source_envelope["actions"][0])
        successor_post = self.provider_stage_retry_action(
            chain_id=successor_source_chain_id,
            action=source_action,
        )
        successor_from_source = self.provider_stage_retry_status(chain_id=successor_source_chain_id)
        later_envelope = validate_provider_stage_retry_status_envelope_v1(
            successor_from_source.body
        )
        if later_envelope["status"]["chain_id"] != successor_later_chain_id:
            raise StateConflictError("qualification relay changed later-stage successor")
        later_reload = self.provider_stage_retry_status(chain_id=successor_later_chain_id)
        reloaded_envelope = validate_provider_stage_retry_status_envelope_v1(later_reload.body)
        later_action = validate_provider_stage_retry_action_v1(reloaded_envelope["actions"][0])
        later_post = self.provider_stage_retry_action(
            chain_id=successor_later_chain_id,
            action=later_action,
        )
        terminal_completion = self.provider_stage_retry_status(chain_id=successor_source_chain_id)
        terminal_completion_decision = validate_ordinary_review_decision_v2(
            terminal_completion.body
        )
        terminal_completion_review = terminal_completion_decision.get("review")
        if not isinstance(terminal_completion_review, dict):
            raise StateConflictError("qualification terminal completion lost review")
        accepted_terminal_reload = self.terminal_review_decision(
            review_id=terminal_completion_review["review_id"]
        )

        recovery = self.provider_stage_retry_status(chain_id=recovery_chain_id)
        recovery_envelope = validate_provider_stage_retry_status_envelope_v1(recovery.body)
        blocked_1 = self.provider_stage_retry_status(chain_id=blocked_chain_id)
        blocked_2 = self.provider_stage_retry_status(chain_id=blocked_chain_id)
        blocked_envelope_1 = validate_provider_stage_retry_status_envelope_v1(blocked_1.body)
        blocked_envelope_2 = validate_provider_stage_retry_status_envelope_v1(blocked_2.body)

        prepared = self.provider_stage_retry_status(chain_id=resume_prepared_chain_id)
        prepared_envelope = validate_provider_stage_retry_status_envelope_v1(prepared.body)
        prepared_action = validate_provider_stage_retry_action_v1(prepared_envelope["actions"][0])
        prepared_post = self.provider_stage_retry_action(
            chain_id=resume_prepared_chain_id,
            action=prepared_action,
        )
        prepared_completion = self.provider_stage_retry_status(chain_id=resume_prepared_chain_id)

        repair = self.provider_stage_retry_status(chain_id=repair_source_chain_id)
        repair_envelope = validate_provider_stage_retry_status_envelope_v1(repair.body)
        repair_action = validate_provider_stage_retry_action_v1(repair_envelope["actions"][0])
        repair_post = self.provider_stage_retry_action(
            chain_id=repair_source_chain_id,
            action=repair_action,
        )
        repair_successor = self.provider_stage_retry_status(chain_id=repair_source_chain_id)
        repair_successor_envelope = validate_provider_stage_retry_status_envelope_v1(
            repair_successor.body
        )
        if repair_successor_envelope["status"]["chain_id"] != repair_successor_chain_id:
            raise StateConflictError("qualification relay changed Recorder repair successor")
        repair_successor_action = validate_provider_stage_retry_action_v1(
            repair_successor_envelope["actions"][0]
        )
        repair_successor_post = self.provider_stage_retry_action(
            chain_id=repair_successor_chain_id,
            action=repair_successor_action,
        )
        repair_completion = self.provider_stage_retry_status(chain_id=repair_source_chain_id)
        repair_completion_decision = validate_ordinary_review_decision_v2(repair_completion.body)
        repair_completion_review = repair_completion_decision.get("review")
        if not isinstance(repair_completion_review, dict):
            raise StateConflictError("qualification Recorder repair lost review")
        repair_completion_reload = self.terminal_review_decision(
            review_id=repair_completion_review["review_id"]
        )
        repair_terminal = self.provider_stage_retry_status(chain_id=repair_terminal_chain_id)
        repair_terminal_envelope = validate_provider_stage_retry_status_envelope_v1(
            repair_terminal.body
        )

        if (
            any(
                value.status_code != 200
                for value in (
                    health,
                    decline_review,
                    regenerate_review,
                    decline,
                    regenerate,
                    decline_terminal,
                    regenerate_terminal,
                    exhaustion_1,
                    exhaustion_2,
                    exhaustion_post,
                    exhaustion_terminal,
                    successor_source,
                    successor_post,
                    successor_from_source,
                    later_reload,
                    later_post,
                    terminal_completion,
                    accepted_terminal_reload,
                    recovery,
                    blocked_1,
                    blocked_2,
                    prepared,
                    prepared_post,
                    prepared_completion,
                    repair,
                    repair_post,
                    repair_successor,
                    repair_successor_post,
                    repair_completion,
                    repair_completion_reload,
                    repair_terminal,
                )
                if value is not ambiguous_post
            )
            or ambiguous_post.status_code != 504
            or exhaustion_1_envelope["status"]["state"] != "eligible"
            or exhaustion_2_envelope["status"]["state"] != "eligible"
            or exhaustion_1_action["retry_action_ordinal"] != 1
            or exhaustion_2_action["retry_action_ordinal"] != 2
            or terminal_envelope["status"]["state"] != "attempts_exhausted"
            or terminal_envelope["actions"] != []
            or source_action["retry_action_ordinal"] != 1
            or later_action["retry_action_ordinal"] != 1
            or later_envelope["status"]["stage"] != "reader"
            or later_envelope["status"]["model_family"] != "sol"
            or recovery_envelope["status"]["state"] != "recovery_required"
            or recovery_envelope["actions"] != []
            or blocked_envelope_1 != blocked_envelope_2
            or blocked_envelope_1["status"]["state"] != "blocked_ambiguous"
            or prepared_action["action_kind"] != "resume_prepared"
            or prepared_action["consumes_retry_action"] is not False
            or prepared_envelope["status"]["stage"] != "reader"
            or prepared_envelope["status"]["model_family"] != "sol"
            or prepared_envelope["status"]["retry_actions_accepted"] != 0
            or repair_action["action_kind"] != "repair_recording"
            or repair_action["consumes_retry_action"] is not False
            or repair_successor_action["action_kind"] != "provider_retry"
            or repair_successor_action["retry_action_ordinal"] != 1
            or repair_terminal_envelope["status"]["state"] != "recording_repair_required"
            or repair_terminal_envelope["actions"] != []
        ):
            raise StateConflictError("qualification generic provider-stage relay changed")
        validated_reviews = {
            "decline_review": validate_ordinary_review_v2(decline_review.body),
            "regenerate_review": validate_ordinary_review_v2(regenerate_review.body),
        }
        validated_decisions = {
            "decline": validate_ordinary_review_decision_v2(decline.body),
            "decline_terminal": validate_ordinary_review_decision_v2(decline_terminal.body),
            "regenerate": validate_ordinary_review_decision_v2(
                regenerate.body,
                successor_validator=validate_ordinary_successor_completion,
            ),
            "regenerate_terminal": validate_ordinary_review_decision_v2(
                regenerate_terminal.body,
                successor_validator=validate_ordinary_successor_completion,
            ),
            "terminal_completion": terminal_completion_decision,
            "accepted_terminal_reload": validate_ordinary_review_decision_v2(
                accepted_terminal_reload.body
            ),
            "prepared_completion": validate_ordinary_review_decision_v2(prepared_completion.body),
            "repair_completion": repair_completion_decision,
            "repair_completion_reload": validate_ordinary_review_decision_v2(
                repair_completion_reload.body
            ),
        }
        if (
            validated_reviews["decline_review"]["gate_status"] != "reject"
            or validated_reviews["regenerate_review"]["gate_status"] != "reject"
            or validated_decisions["decline"]["creator_action"] != "decline"
            or validated_decisions["regenerate"]["creator_action"] != "regenerate"
            or validated_decisions["decline_terminal"] != validated_decisions["decline"]
            or validated_decisions["regenerate_terminal"] != validated_decisions["regenerate"]
            or validated_decisions["terminal_completion"]["creator_action"] != "automatic_accept"
            or validated_decisions["repair_completion"]["creator_action"] != "repair_recording"
            or validated_decisions["accepted_terminal_reload"]
            != validated_decisions["terminal_completion"]
            or validated_decisions["repair_completion_reload"]
            != validated_decisions["repair_completion"]
        ):
            raise StateConflictError("qualification ordinary review relay changed")
        values = {
            "health": health.body,
            "decline_review": decline_review.body,
            "regenerate_review": regenerate_review.body,
            "decline": decline.body,
            "regenerate": regenerate.body,
            "decline_terminal": decline_terminal.body,
            "regenerate_terminal": regenerate_terminal.body,
            "ambiguous_post": ambiguous_post.body,
            "exhaustion_terminal": exhaustion_terminal.body,
            "successor_post": successor_post.body,
            "later_post": later_post.body,
            "terminal_completion": terminal_completion.body,
            "accepted_terminal_reload": accepted_terminal_reload.body,
            "recovery": recovery.body,
            "blocked": blocked_2.body,
            "prepared_post": prepared_post.body,
            "prepared_completion": prepared_completion.body,
            "repair_post": repair_post.body,
            "repair_successor_post": repair_successor_post.body,
            "repair_completion": repair_completion.body,
            "repair_completion_reload": repair_completion_reload.body,
            "repair_terminal": repair_terminal.body,
        }
        return {
            "schema_version": "cera.pi_scene.qualification_provider_stage_relay.v1",
            **{f"{key}_sha256": canonical_sha256(value) for key, value in values.items()},
            "exact_action_sha256s": [
                canonical_sha256(value)
                for value in (
                    exhaustion_1_action,
                    exhaustion_2_action,
                    source_action,
                    later_action,
                    prepared_action,
                    repair_action,
                    repair_successor_action,
                )
            ],
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
            with self._opener.open(
                request,
                timeout=QUALIFICATION_HTTP_HARD_TIMEOUT_SECONDS,
            ) as response:
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
    spent_manifest_paths: tuple[Path, ...] = (),
) -> dict[str, Any]:
    root = output_root.resolve()
    _preflight_runtime_path_budget(root)
    if root.exists():
        raise StateConflictError("qualification output root already exists")
    fixtures = load_qualification_fixtures(fixture_path)
    if not fixtures or not all(isinstance(value, QualificationFixtureV2) for value in fixtures):
        raise StateConflictError("live qualification requires novel stress fixtures v2")
    spent_manifests = _load_spent_qualification_manifests(
        output_root=root,
        explicit_paths=spent_manifest_paths,
    )
    _assert_clean_exact_repository(ROOT)
    commit = _git("rev-parse", "HEAD")
    tree = _git("rev-parse", "HEAD^{tree}")
    root.mkdir(parents=True)
    isolated_root = root / "isolated_sillytavern"
    stage_qualification_sillytavern(
        sillytavern_source.resolve(),
        isolated_root,
        repository_proxy_root=(ROOT / "integrations" / "sillytavern" / "cera-review-proxy-plugin"),
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
        spent_manifests=spent_manifests,
    )
    write_qualification_manifest(root / "QUALIFICATION_MANIFEST.json", manifest)
    verify_qualification_artifacts(manifest, repository_root=ROOT)
    return manifest


def _load_spent_qualification_manifests(
    *,
    output_root: Path,
    explicit_paths: tuple[Path, ...],
) -> tuple[dict[str, Any], ...]:
    """Load the cumulative v5 fixture authority from this root family.

    Older manifests used the retired v1 fixture suite, which the v2 fixture
    envelope already binds as its baseline. Every v5 sibling is included
    automatically so a caller cannot accidentally omit an earlier novel suite.
    Explicit paths extend the authority to campaigns outside the output-root
    parent.
    """

    candidates: dict[Path, None] = {}
    parent = output_root.resolve().parent
    if parent.is_dir():
        for child in parent.iterdir():
            if child.is_symlink() or not child.is_dir():
                continue
            candidate = child / "QUALIFICATION_MANIFEST.json"
            if not candidate.exists():
                continue
            if candidate.is_symlink() or not candidate.is_file():
                raise StateConflictError("qualification spent manifest is unavailable or linked")
            try:
                raw = json.loads(candidate.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ContractValidationError("qualification spent manifest is unreadable") from exc
            if (
                isinstance(raw, Mapping)
                and raw.get("schema_version") == QUALIFICATION_MANIFEST_SCHEMA
            ):
                candidates[candidate.resolve()] = None
    for path in explicit_paths:
        if path.is_symlink():
            raise StateConflictError("qualification explicit spent manifest is linked")
        resolved = path.resolve()
        if not resolved.is_file():
            raise StateConflictError(
                "qualification explicit spent manifest is unavailable or linked"
            )
        candidates[resolved] = None
    return tuple(
        load_qualification_manifest(path)
        for path in sorted(candidates, key=lambda value: str(value).casefold())
    )


def provider_free_check(
    *,
    fixture_path: Path,
    manifest_path: Path | None = None,
) -> dict[str, Any]:
    fixtures = load_qualification_fixtures(fixture_path)
    fixture_metadata = qualification_fixture_manifest_metadata(fixtures)
    result: dict[str, Any] = {
        "status": "provider_free_ready",
        "fixtures": len(fixtures),
        "backend": sum(value.phase is QualificationPhase.BACKEND for value in fixtures),
        "sillytavern": sum(value.phase is QualificationPhase.SILLYTAVERN for value in fixtures),
        "fixture_schema_version": fixture_metadata["fixture_schema_version"],
        "novelty_set_sha256": fixture_metadata["novelty_set_sha256"],
        "stress_coverage": fixture_metadata["stress_coverage"],
        "sol_ceiling": SOL_FAMILY_CEILING,
        "deepseek_http_operation_ceiling": DEEPSEEK_HTTP_OPERATION_CEILING,
        "deepseek_per_invocation_ceiling": DEEPSEEK_PER_INVOCATION_CEILING,
        "manual_provider_stage_retry_authorized": True,
        "provider_stage_retry_stages": [
            "planner",
            "semantic_validator",
            "reader",
            "writer",
            "recorder",
            "adult_scene",
            "adult_filter",
        ],
        "maximum_manual_retry_actions_per_stage_occurrence": 2,
        "maximum_provider_attempts_per_stage_occurrence": 3,
        "maximum_manual_resume_prepared_actions_per_stage_occurrence": 1,
        "maximum_manual_recording_repair_actions_per_request": 1,
        "maximum_recording_repair_successor_attempts": 3,
        "maximum_recording_repair_successor_retry_actions": 2,
        "recursive_recording_repair": False,
        "automatic_provider_stage_retry_actions": 0,
        "automatic_provider_stage_control_actions": 0,
        "automatic_manual_actions": 0,
        "provider_bearing_actions_require_exact_external_authorization": True,
        "manual_action_receipt_custody": "hash_only",
        "provider_calls": 0,
    }
    if manifest_path is not None:
        manifest = load_qualification_manifest(manifest_path)
        if manifest["fixture_set_sha256"] != __import__(
            "cera.serialization", fromlist=["bytes_sha256"]
        ).bytes_sha256(fixture_path.read_bytes()):
            raise StateConflictError("qualification fixture binding changed")
        for key, expected in fixture_metadata.items():
            if manifest[key] != expected:
                raise StateConflictError("qualification fixture novelty binding changed")
        verify_qualification_artifacts(manifest, repository_root=ROOT)
        isolated = verify_qualification_sillytavern(
            manifest_path.resolve().parent / "isolated_sillytavern"
        )
        node_proof = run_staged_sillytavern_node_suites(
            manifest_path.resolve().parent / "isolated_sillytavern",
            node_executable=_frozen_node_executable(manifest),
        )
        result["manifest_sha256"] = manifest["manifest_sha256"]
        result["isolated_sillytavern_manifest_sha256"] = isolated["manifest_sha256"]
        result["staged_node_suite_proof_sha256"] = node_proof["proof_sha256"]
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
        provider_stage_retry = getattr(runtime, "provider_stage_retry", None)
        retry_http_kwargs = (
            {} if provider_stage_retry is None else provider_stage_retry.http_adapter_kwargs()
        )
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
            transport_retry_reinitializer=runtime.transport_retry_reinitializer,
            transport_provider_ledger_snapshot=(runtime.transport_provider_ledger_snapshot),
            transport_retry_active_thread_snapshot=(runtime.transport_retry_active_thread_snapshot),
            transport_retry_fresh_thread_initializer=(
                runtime.transport_retry_fresh_thread_initializer
            ),
            transport_completed_planner_abandoner=(runtime.transport_completed_planner_abandoner),
            **retry_http_kwargs,
        )
        if provider_stage_retry is not None:
            provider_stage_retry.bind_http_adapter(adapter)
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


def _relay_review_controls_v3() -> dict[str, Any]:
    return {
        "schema_version": "cera.pi_scene.request_controls.v3",
        "session_id": "qualification-relay-preflight",
        "scene_depth": "auto",
        "regeneration_key": None,
        "character_autonomy": "both",
        "prompt_handling": "adjustment",
        "reasoning_effort": "medium",
        "scene_change": False,
        "adult_craft_mode": "off",
        "review_mode": "automatic",
    }


def _relay_review_actions(*, rejected: bool = False) -> dict[str, Any]:
    return {
        "accept_enabled": False,
        "regenerate_enabled": rejected,
        "decline_enabled": rejected,
        "replan_enabled": False,
        "auditable_override_enabled": rejected,
        "auditable_override_action": "accept_provisional" if rejected else None,
        "repair_recording_enabled": False,
    }


def _relay_review_checks(
    *,
    pending: bool = False,
    reader_rejected: bool = False,
) -> dict[str, Any]:
    def lane(
        *,
        role: str,
        status: str,
        verdict_sha256: str | None,
        failures: list[dict[str, str]],
    ) -> dict[str, Any]:
        return {
            "role": role,
            "required": True,
            "status": status,
            "verdict_sha256": verdict_sha256,
            "failures": failures,
            "provider_stage_retry_status": None,
        }

    if pending:
        luna = lane(
            role="luna_semantic_validator",
            status="pending",
            verdict_sha256=None,
            failures=[],
        )
        reader = lane(
            role="codex_reader_severe_quality",
            status="pending",
            verdict_sha256=None,
            failures=[],
        )
        python_status = "pending"
        python_sha256 = None
    else:
        luna = lane(
            role="luna_semantic_validator",
            status="pass",
            verdict_sha256="1" * 64,
            failures=[],
        )
        reader = lane(
            role="codex_reader_severe_quality",
            status="reject" if reader_rejected else "pass",
            verdict_sha256="2" * 64,
            failures=(
                [
                    {
                        "code": "severe_incompleteness",
                        "concise_explanation": ("The candidate omitted one required decision."),
                    }
                ]
                if reader_rejected
                else []
            ),
        )
        python_status = "pass"
        python_sha256 = "3" * 64
    return {
        "schema_version": "cera.pi_scene.review_checks.v1",
        "luna": luna,
        "reader": reader,
        "adult_filter": {
            "role": "protected_adult_filter",
            "required": False,
            "status": "not_applicable",
            "verdict_sha256": None,
            "failures": [],
            "provider_stage_retry_status": None,
        },
        "python": lane(
            role="python_deterministic_custody_privacy",
            status=python_status,
            verdict_sha256=python_sha256,
            failures=[],
        ),
    }


def _relay_review_v2(
    review_id: str,
    *,
    state: str,
    reader_rejected: bool,
) -> dict[str, Any]:
    candidate_id = "candidate-" + text_sha256(f"relay-candidate:{review_id}")[:28]
    accepted = state == "accepted"
    review = {
        "schema_version": "cera.pi_scene.review.v2",
        "review_id": review_id,
        "state": state,
        "review_mode": "automatic",
        "route": "ordinary",
        "story_text": (
            "The qualification relay keeps the provisional scene concise and leaves "
            "the next meaningful choice open."
        ),
        "candidate_id": candidate_id,
        "candidate_sha256": text_sha256(f"relay-candidate-body:{candidate_id}"),
        "primary_authority_kind": "codex_cognition_plan",
        "primary_authority_sha256": text_sha256(f"relay-primary-authority:{candidate_id}"),
        "warnings": [],
        "recording_status": "complete" if accepted else None,
        "gate_status": "reject" if reader_rejected else "pass",
        "checks": _relay_review_checks(reader_rejected=reader_rejected),
        "acceptance": (
            {
                "mode": "automatic",
                "accepted_turn_id": f"turn-{text_sha256(review_id)[:16]}",
                "accepted_receipt_sha256": text_sha256(f"relay-receipt:{review_id}"),
                "canon_status": "accepted",
            }
            if accepted
            else None
        ),
        "actions": _relay_review_actions(rejected=reader_rejected and state == "review_ready"),
        "request_controls": _relay_review_controls_v3(),
        "creator_guidance": None,
        "provider_attempts": [
            {
                "attempt_number": 1,
                "candidate_id": candidate_id,
                "disposition": ("reader_rejected" if reader_rejected else "checks_passed"),
                "provider_operations": {
                    "planner": 1,
                    "writer": 1,
                    "validator": 1,
                    "reader": 1,
                },
            }
        ],
        "provider_operations": {
            "planner": 1,
            "writer": 1,
            "validator": 1,
            "reader": 1,
            "recorder": 1 if accepted else 0,
        },
        "terminal_decision": None,
    }
    if state == "review_ready":
        return dict(validate_ordinary_review_v2(review))
    return review


def _relay_provisional_completion(review_id: str) -> dict[str, Any]:
    candidate_id = "candidate-" + text_sha256(f"relay-successor:{review_id}")[:28]
    lifecycle = validate_ordinary_review_lifecycle_v1(
        {
            "schema_version": "cera.pi_scene.review_lifecycle.v1",
            "review_id": review_id,
            "review_url": f"/v1/cera/reviews/{review_id}",
            "review_mode": "automatic",
            "state": "checks_pending",
            "gate_status": "pending",
            "checks": _relay_review_checks(pending=True),
            "acceptance": None,
            "actions": _relay_review_actions(),
            "terminal_decision": None,
        }
    )
    completion = {
        "id": "chatcmpl-" + text_sha256(f"relay-completion:{review_id}")[:24],
        "object": "chat.completion",
        "created": 0,
        "model": "cera-pi-scene-ordinary",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": (
                        "The regenerated qualification candidate remains provisional "
                        "until every required review lane joins."
                    ),
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        "cera": {
            "profile_id": "cera.pi_scene.lean.v1",
            "route_mode": "ordinary",
            "status": "review_ready",
            "provisional": True,
            "story_state_committed": False,
            "provisional_review_id": review_id,
            "review_url": f"/v1/cera/reviews/{review_id}",
            "candidate_id": candidate_id,
            "candidate_sha256": text_sha256(f"relay-candidate-body:{candidate_id}"),
            "request_controls": _relay_review_controls_v3(),
            "review_lifecycle": lifecycle,
        },
    }
    return validate_ordinary_successor_completion(completion)


def _relay_review_decision_v2(
    review_id: str,
    *,
    creator_action: str,
    state: str,
    reader_rejected: bool,
    successor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    review = _relay_review_v2(
        review_id,
        state=state,
        reader_rejected=reader_rejected,
    )
    committed = state == "accepted"
    decision: dict[str, Any] = {
        "schema_version": "cera.pi_scene.review_decision.v2",
        "status": "story_committed" if committed else "review_transitioned",
        "creator_action": creator_action,
        "story_state_committed": committed,
        "retry_mode": "not_applicable",
        "review": review,
        "successor": successor,
        "operational_warnings": [],
    }
    if committed:
        acceptance = review["acceptance"]
        if not isinstance(acceptance, dict):
            raise StateConflictError("qualification relay accepted review lost custody")
        decision.update(
            {
                "accepted_receipt_sha256": acceptance["accepted_receipt_sha256"],
                "accepted_turn_id": acceptance["accepted_turn_id"],
            }
        )
    basis = deepcopy(decision)
    basis["review"]["terminal_decision"] = None
    decision_sha256 = canonical_sha256(basis)
    decision["review"]["terminal_decision"] = {
        "decision_sha256": decision_sha256,
        "url": f"/v1/cera/reviews/{review_id}/terminal-decision",
    }
    return dict(
        validate_ordinary_review_decision_v2(
            decision,
            successor_validator=(
                validate_ordinary_successor_completion if successor is not None else None
            ),
        )
    )


class _GenericFakeRelayUpstreamV1:
    """Provider-free upstream for exact generic Retry relay qualification."""

    def __init__(
        self,
        *,
        port: int,
        decline_review_id: str,
        regenerate_review_id: str,
        authorization_token: str,
        chain_id: str,
    ) -> None:
        self.exhaustion_chain_id = chain_id
        self.successor_source_chain_id = "stage-retry-" + "4" * 64
        self.successor_later_chain_id = "stage-retry-" + "5" * 64
        self.recovery_chain_id = "stage-retry-" + "6" * 64
        self.blocked_chain_id = "stage-retry-" + "7" * 64
        self.resume_prepared_chain_id = "stage-retry-" + "a" * 64
        self.repair_source_chain_id = "stage-retry-" + "b" * 64
        self.repair_successor_chain_id = "stage-retry-" + "c" * 64
        self.repair_terminal_chain_id = "stage-retry-" + "d" * 64
        self._request_sha256 = "8" * 64
        self._private_sentinel = "RAW PRIVATE RELAY PROVIDER STORY SENTINEL"
        self._state: dict[str, Any] = {
            "authenticated_gets": 0,
            "authenticated_posts": 0,
            "exact_action_posts": 0,
            "duplicate_posts": 0,
            "provider_calls": 0,
        }
        self._latest = {
            self.exhaustion_chain_id: self.exhaustion_chain_id,
            self.successor_source_chain_id: self.successor_source_chain_id,
            self.successor_later_chain_id: self.successor_later_chain_id,
            self.recovery_chain_id: self.recovery_chain_id,
            self.blocked_chain_id: self.blocked_chain_id,
            self.resume_prepared_chain_id: self.resume_prepared_chain_id,
            self.repair_source_chain_id: self.repair_source_chain_id,
            self.repair_successor_chain_id: self.repair_successor_chain_id,
            self.repair_terminal_chain_id: self.repair_terminal_chain_id,
        }
        self._envelopes: dict[str, dict[str, Any]] = {}
        self._completed_chains: dict[str, dict[str, Any]] = {}
        self._action_results: dict[str, tuple[int, dict[str, Any]]] = {}
        self.completion_review_id = "review-" + "e" * 28
        self.repair_review_id = "review-" + "f" * 28
        self._completion = _relay_review_decision_v2(
            self.completion_review_id,
            creator_action="automatic_accept",
            state="accepted",
            reader_rejected=False,
        )
        self._repair_completion = _relay_review_decision_v2(
            self.repair_review_id,
            creator_action="repair_recording",
            state="accepted",
            reader_rejected=False,
        )
        self._envelopes[self.exhaustion_chain_id] = self._envelope(
            chain_id=self.exhaustion_chain_id,
            stage="planner",
            attempts=1,
            retries=0,
            state="eligible",
        )
        self._envelopes[self.successor_source_chain_id] = self._envelope(
            chain_id=self.successor_source_chain_id,
            stage="writer",
            attempts=1,
            retries=0,
            state="eligible",
        )
        self._envelopes[self.recovery_chain_id] = self._envelope(
            chain_id=self.recovery_chain_id,
            stage="adult_filter",
            attempts=1,
            retries=0,
            state="recovery_required",
        )
        self._envelopes[self.blocked_chain_id] = self._envelope(
            chain_id=self.blocked_chain_id,
            stage="recorder",
            attempts=1,
            retries=0,
            state="blocked_ambiguous",
        )
        self._envelopes[self.resume_prepared_chain_id] = self._envelope(
            chain_id=self.resume_prepared_chain_id,
            stage="reader",
            attempts=1,
            retries=0,
            state="in_progress",
            control_action="resume_prepared",
            operations_observed=0,
        )
        self._envelopes[self.repair_source_chain_id] = self._envelope(
            chain_id=self.repair_source_chain_id,
            stage="recorder",
            attempts=3,
            retries=2,
            state="recording_repair_required",
            control_action="repair_recording",
        )
        self._envelopes[self.repair_terminal_chain_id] = self._envelope(
            chain_id=self.repair_terminal_chain_id,
            stage="recorder",
            attempts=3,
            retries=2,
            state="recording_repair_required",
        )
        decline_review = _relay_review_v2(
            decline_review_id,
            state="review_ready",
            reader_rejected=True,
        )
        regenerate_review = _relay_review_v2(
            regenerate_review_id,
            state="review_ready",
            reader_rejected=True,
        )
        decline_decision = _relay_review_decision_v2(
            decline_review_id,
            creator_action="decline",
            state="declined",
            reader_rejected=True,
        )
        regenerate_decision = _relay_review_decision_v2(
            regenerate_review_id,
            creator_action="regenerate",
            state="regenerated",
            reader_rejected=True,
            successor=_relay_provisional_completion("review-" + "9" * 28),
        )
        expected_reviews = {
            f"/v1/cera/reviews/{decline_review_id}": decline_review,
            f"/v1/cera/reviews/{regenerate_review_id}": regenerate_review,
        }
        terminal_decisions = {
            f"/v1/cera/reviews/{decline_review_id}/terminal-decision": (decline_decision),
            f"/v1/cera/reviews/{regenerate_review_id}/terminal-decision": (regenerate_decision),
            f"/v1/cera/reviews/{self.completion_review_id}/terminal-decision": (self._completion),
            f"/v1/cera/reviews/{self.repair_review_id}/terminal-decision": (
                self._repair_completion
            ),
        }
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                if self.path in {"/v1/health", "/health"}:
                    self._json({"status": "ok", "provider_calls": 0})
                    return
                if self.path in expected_reviews:
                    if not self._authorized():
                        return
                    owner._state["authenticated_gets"] += 1
                    self._json(expected_reviews[self.path])
                    return
                if self.path in terminal_decisions:
                    if not self._authorized():
                        return
                    owner._state["authenticated_gets"] += 1
                    self._json(terminal_decisions[self.path])
                    return
                prefix = "/v1/cera/provider-stage-retries/"
                if not self.path.startswith(prefix):
                    self.send_error(404)
                    return
                if not self._authorized():
                    return
                chain = self.path.removeprefix(prefix)
                if "/" in chain or chain not in owner._latest:
                    self.send_error(404)
                    return
                owner._state["authenticated_gets"] += 1
                current = owner._resolve_latest(chain)
                if current in owner._completed_chains:
                    self._json(owner._completed_chains[current])
                    return
                self._json(owner._envelopes[current])

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
                if self.path in decision_paths:
                    if not self._authorized():
                        return
                    review_id, action = decision_paths[self.path]
                    payload = self._payload()
                    if payload.get("action") != action:
                        self.send_error(422)
                        return
                    owner._state["authenticated_posts"] += 1
                    self._json(decline_decision if action == "decline" else regenerate_decision)
                    return
                prefix = "/v1/cera/provider-stage-retries/"
                if not self.path.startswith(prefix) or "/actions/" not in self.path:
                    self.send_error(404)
                    return
                if not self._authorized():
                    return
                tail = self.path.removeprefix(prefix)
                chain, action_id = tail.split("/actions/", 1)
                if chain not in owner._envelopes:
                    self.send_error(404)
                    return
                payload = self._payload()
                expected = owner._envelopes[chain]["actions"]
                if (
                    len(expected) != 1
                    or payload != expected[0]
                    or payload.get("action_id") != action_id
                ):
                    self.send_error(422)
                    return
                owner._state["authenticated_posts"] += 1
                prior = owner._action_results.get(action_id)
                if prior is not None:
                    owner._state["duplicate_posts"] += 1
                    self._json(prior[1], status=prior[0])
                    return
                owner._state["exact_action_posts"] += 1
                status_code, result = owner._execute_action(chain)
                owner._action_results[action_id] = (status_code, result)
                self._json(result, status=status_code)

            def _authorized(self) -> bool:
                if self.headers.get("Authorization") == f"Bearer {authorization_token}":
                    return True
                self.send_error(401)
                return False

            def _payload(self) -> dict[str, Any]:
                length = int(self.headers.get("Content-Length", "0"))
                value = json.loads(self.rfile.read(length).decode("utf-8"))
                if not isinstance(value, dict):
                    raise StateConflictError("fake relay body is not an object")
                return value

            def _json(self, payload: dict[str, Any], *, status: int = 200) -> None:
                data = canonical_bytes(payload)
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, _format: str, *_arguments: object) -> None:
                return

        self.server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self.worker = Thread(target=self.server.serve_forever, daemon=True)
        self._started = False

    def _execute_action(self, chain_id: str) -> tuple[int, dict[str, Any]]:
        status = self._envelopes[chain_id]["status"]
        if chain_id == self.exhaustion_chain_id:
            retries = int(status["retry_actions_accepted"]) + 1
            attempts = int(status["stage_attempts_total"]) + 1
            state = "eligible" if retries == 1 else "attempts_exhausted"
            self._envelopes[chain_id] = self._envelope(
                chain_id=chain_id,
                stage="planner",
                attempts=attempts,
                retries=retries,
                state=state,
            )
            return (504 if retries == 1 else 200), self._envelopes[chain_id]
        if chain_id == self.successor_source_chain_id:
            self._envelopes[self.successor_later_chain_id] = self._envelope(
                chain_id=self.successor_later_chain_id,
                stage="reader",
                attempts=1,
                retries=0,
                state="eligible",
            )
            self._latest[chain_id] = self.successor_later_chain_id
            return 200, self._envelopes[self.successor_later_chain_id]
        if chain_id == self.successor_later_chain_id:
            del self._envelopes[chain_id]
            self._completed_chains[chain_id] = dict(self._completion)
            return 200, dict(self._completion)
        if chain_id == self.resume_prepared_chain_id:
            del self._envelopes[chain_id]
            self._completed_chains[chain_id] = dict(self._completion)
            return 200, dict(self._completion)
        if chain_id == self.repair_source_chain_id:
            self._envelopes[self.repair_successor_chain_id] = self._envelope(
                chain_id=self.repair_successor_chain_id,
                stage="recorder",
                attempts=1,
                retries=0,
                state="eligible",
            )
            self._latest[chain_id] = self.repair_successor_chain_id
            return 200, self._envelopes[self.repair_successor_chain_id]
        if chain_id == self.repair_successor_chain_id:
            del self._envelopes[chain_id]
            self._completed_chains[chain_id] = dict(self._repair_completion)
            return 200, dict(self._repair_completion)
        raise StateConflictError("fake relay accepted an action on a terminal chain")

    def _resolve_latest(self, chain_id: str) -> str:
        current = chain_id
        seen: set[str] = set()
        while self._latest[current] != current:
            if current in seen:
                raise StateConflictError("fake relay successor cycle")
            seen.add(current)
            current = self._latest[current]
        return current

    def _envelope(
        self,
        *,
        chain_id: str,
        stage: str,
        attempts: int,
        retries: int,
        state: str,
        control_action: str | None = None,
        operations_observed: int | None = None,
    ) -> dict[str, Any]:
        provider = "codex" if stage in {"planner", "semantic_validator", "reader"} else "deepseek"
        model_family = (
            "sol"
            if stage in {"planner", "reader"}
            else "luna"
            if stage == "semantic_validator"
            else "deepseek_v4"
        )
        chain_sha256 = text_sha256(f"{chain_id}:{attempts}:{retries}:{state}")
        failure_category: str | None = "provider_unavailable"
        available_actions: list[str] = []
        actions: list[dict[str, Any]] = []
        if state == "eligible":
            available_actions = ["provider_retry"]
            actions = [
                {
                    "schema_version": "cera.provider_stage_retry_action.v1",
                    "action_id": f"stage-action-{text_sha256(f'{chain_id}:{retries + 1}')}",
                    "chain_id": chain_id,
                    "action_family": "provider_stage_control",
                    "action_kind": "provider_retry",
                    "automatic": False,
                    "provider_dispatch_authorized": True,
                    "consumes_retry_action": True,
                    "retry_action_ordinal": retries + 1,
                    "whole_request_replay_authorized": False,
                    "provider_substitution_authorized": False,
                    "expected_chain_sha256": chain_sha256,
                }
            ]
        elif state == "in_progress" and control_action == "resume_prepared":
            failure_category = None
            available_actions = ["resume_prepared"]
            actions = [
                {
                    "schema_version": "cera.provider_stage_retry_action.v1",
                    "action_id": f"stage-action-{text_sha256(f'{chain_id}:resume')}",
                    "chain_id": chain_id,
                    "action_family": "provider_stage_control",
                    "action_kind": "resume_prepared",
                    "automatic": False,
                    "provider_dispatch_authorized": True,
                    "consumes_retry_action": False,
                    "retry_action_ordinal": None,
                    "whole_request_replay_authorized": False,
                    "provider_substitution_authorized": False,
                    "expected_chain_sha256": chain_sha256,
                }
            ]
        elif state == "blocked_ambiguous":
            failure_category = "dispatch_ambiguous"
            available_actions = ["check_status"]
            actions = [
                {
                    "schema_version": "cera.provider_stage_retry_action.v1",
                    "action_id": f"stage-action-{text_sha256(f'{chain_id}:check')}",
                    "chain_id": chain_id,
                    "action_family": "provider_stage_control",
                    "action_kind": "check_status",
                    "automatic": False,
                    "provider_dispatch_authorized": False,
                    "consumes_retry_action": False,
                    "retry_action_ordinal": None,
                    "whole_request_replay_authorized": False,
                    "provider_substitution_authorized": False,
                    "expected_chain_sha256": chain_sha256,
                }
            ]
        elif state == "recovery_required":
            failure_category = "configuration_failed"
        elif state == "recording_repair_required" and control_action == "repair_recording":
            available_actions = ["repair_recording"]
            actions = [
                {
                    "schema_version": "cera.provider_stage_retry_action.v1",
                    "action_id": f"stage-action-{text_sha256(f'{chain_id}:repair')}",
                    "chain_id": chain_id,
                    "action_family": "provider_stage_control",
                    "action_kind": "repair_recording",
                    "automatic": False,
                    "provider_dispatch_authorized": True,
                    "consumes_retry_action": False,
                    "retry_action_ordinal": None,
                    "whole_request_replay_authorized": False,
                    "provider_substitution_authorized": False,
                    "expected_chain_sha256": chain_sha256,
                }
            ]
        status = {
            "schema_version": "cera.provider_stage_retry_status.v1",
            "chain_id": chain_id,
            "provider": provider,
            "model_family": model_family,
            "stage": stage,
            "state": state,
            "maximum_attempts": 3,
            "stage_attempts_total": attempts,
            "retry_actions_accepted": retries,
            "provider_operations_observed_total": (
                attempts if operations_observed is None else operations_observed
            ),
            "provider_operations_conservative_total": (
                attempts if operations_observed is None else operations_observed
            ),
            "story_state_committed": stage == "recorder",
            "branch_preserved_at_last_accepted_head": True,
            "failure_category": failure_category,
            "available_actions": available_actions,
            "technical_details": {
                "schema_version": "cera.provider_stage_retry_technical_details.v1",
                "request_occurrence_sha256": "a" * 64,
                "request_sha256": self._request_sha256,
                "stage_input_sha256": text_sha256(f"relay:{stage}"),
                "accepted_state_sha256": "b" * 64,
                "chain_sha256": chain_sha256,
            },
        }
        envelope = {
            "schema_version": "cera.provider_stage_retry_status_envelope.v1",
            "status": status,
            "actions": actions,
        }
        return dict(validate_provider_stage_retry_status_envelope_v1(envelope))

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

    def retry_proof(self) -> dict[str, Any]:
        public_projection = {
            "envelopes": self._envelopes,
            "completed_chains": self._completed_chains,
            "completion": self._completion,
            "repair_completion": self._repair_completion,
        }
        body = {
            "schema_version": "cera.pi_scene.qualification_fake_provider_stage_relay.v1",
            "chain_id_hashes": [
                text_sha256(value)
                for value in (
                    self.exhaustion_chain_id,
                    self.successor_source_chain_id,
                    self.successor_later_chain_id,
                    self.recovery_chain_id,
                    self.blocked_chain_id,
                    self.resume_prepared_chain_id,
                    self.repair_source_chain_id,
                    self.repair_successor_chain_id,
                    self.repair_terminal_chain_id,
                )
            ],
            **self._state,
            "private_sentinel_absent": self._private_sentinel
            not in json.dumps(public_projection, sort_keys=True),
        }
        if (
            body["exact_action_posts"] != 7
            or body["provider_calls"] != 0
            or body["private_sentinel_absent"] is not True
        ):
            raise StateConflictError("qualification generic fake relay proof is incomplete")
        return {**body, "proof_sha256": canonical_sha256(body)}


def _start_qualification_sillytavern(
    root: Path,
    *,
    port: int,
    cera_port: int,
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
    # SillyTavern debug output can contain the complete retained request and
    # response. Qualification custody permits no raw ordinary/adult prose in
    # its output tree, so the disposable process has no durable console sink.
    process = subprocess.Popen(
        command,
        cwd=root,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=environment,
        text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
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
    _stop_process(cast(Any, process))
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
    controls = LeanSceneRequestControlsV3(
        schema_version=LeanSceneRequestControlsV3.SCHEMA_VERSION,
        session_id=parent_session_id,
        scene_depth="auto",
        regeneration_key=None,
        character_autonomy="both",
        prompt_handling="adjustment",
        reasoning_effort="xhigh",
        scene_change=False,
        review_mode="automatic",
        adult_craft_mode="off",
    )
    parent = runtime.world_resolver.resolve(controls)
    parent_head = runtime.store.load_head(
        world_id=parent.world_id,
        branch_id=parent.branch_id,
    )
    if parent_head.generation != 10:
        raise StateConflictError("SillyTavern campaign accepted head is not generation ten")
    new_controls = LeanSceneRequestControlsV3(
        schema_version=LeanSceneRequestControlsV3.SCHEMA_VERSION,
        session_id=parent_session_id + "-new",
        scene_depth="auto",
        regeneration_key=None,
        character_autonomy="both",
        prompt_handling="adjustment",
        reasoning_effort="xhigh",
        scene_change=False,
        review_mode="automatic",
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
    manual_action_wait_seconds: float = DEFAULT_MANUAL_ACTION_WAIT_SECONDS,
) -> dict[str, Any]:
    root = output_root.resolve()
    manifest_path = root / "QUALIFICATION_MANIFEST.json"
    manifest = load_qualification_manifest(manifest_path)
    _assert_clean_exact_repository(
        ROOT,
        expected_commit=str(manifest["source_commit"]),
        expected_tree=str(manifest["source_tree"]),
    )
    _assert_frozen_python_interpreter(manifest)
    verify_qualification_artifacts(manifest, repository_root=ROOT)
    fixtures = load_qualification_fixtures(fixture_path)
    if not fixtures or not all(isinstance(value, QualificationFixtureV2) for value in fixtures):
        raise StateConflictError("live qualification requires novel stress fixtures v2")
    if manifest["fixture_set_sha256"] != __import__(
        "cera.serialization", fromlist=["bytes_sha256"]
    ).bytes_sha256(fixture_path.read_bytes()):
        raise StateConflictError("qualification fixture binding changed")
    fixture_metadata = qualification_fixture_manifest_metadata(fixtures)
    for key, expected in fixture_metadata.items():
        if manifest[key] != expected:
            raise StateConflictError("qualification fixture novelty binding changed")
    runtime_root_a = root / "runtime-a"
    runtime_root_b = root / "runtime-b"
    evidence_root = root / "evidence"
    if runtime_root_a.exists() or runtime_root_b.exists() or evidence_root.exists():
        raise StateConflictError("qualification live runtime is not fresh")

    isolated_root = root / "isolated_sillytavern"
    isolated_manifest = verify_qualification_sillytavern(isolated_root)
    staged_node_proof = run_staged_sillytavern_node_suites(
        isolated_root,
        node_executable=_frozen_node_executable(manifest),
    )
    _atomic_write_json(root / "STAGED_NODE_SUITE_PROOF.json", staged_node_proof)
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
    retry_probe_id = "stage-retry-" + "1" * 64
    relay_port = _available_port_excluding(5101)
    relay_st_port = _available_port_excluding(5101, relay_port)
    fake_relay = _GenericFakeRelayUpstreamV1(
        port=relay_port,
        decline_review_id=decline_probe_id,
        regenerate_review_id=regenerate_probe_id,
        authorization_token=cera_token,
        chain_id=retry_probe_id,
    )
    relay_process: subprocess.Popen[str] | None = None
    try:
        fake_relay.start()
        relay_process = _start_qualification_sillytavern(
            isolated_root,
            port=relay_st_port,
            cera_port=relay_port,
        )
        relay_client = IsolatedSillyTavernQualificationClient(
            origin=f"http://127.0.0.1:{relay_st_port}",
            cera_base_url=f"http://127.0.0.1:{relay_port}",
            cera_token=cera_token,
        )
        relay_proof = relay_client.probe_provider_stage_relay(
            decline_review_id=decline_probe_id,
            regenerate_review_id=regenerate_probe_id,
            exhaustion_chain_id=fake_relay.exhaustion_chain_id,
            successor_source_chain_id=fake_relay.successor_source_chain_id,
            successor_later_chain_id=fake_relay.successor_later_chain_id,
            recovery_chain_id=fake_relay.recovery_chain_id,
            blocked_chain_id=fake_relay.blocked_chain_id,
            resume_prepared_chain_id=fake_relay.resume_prepared_chain_id,
            repair_source_chain_id=fake_relay.repair_source_chain_id,
            repair_successor_chain_id=fake_relay.repair_successor_chain_id,
            repair_terminal_chain_id=fake_relay.repair_terminal_chain_id,
        )
        relay_proof = {
            **relay_proof,
            "fake_upstream_retry_proof": fake_relay.retry_proof(),
        }
        relay_proof["proof_sha256"] = canonical_sha256(relay_proof)
        _atomic_write_json(root / "REVIEW_RELAY_PREFLIGHT.json", relay_proof)
    finally:
        fake_relay.close()
        if relay_process is not None:
            _stop_process(cast(Any, relay_process))
    _reset_disposable_sillytavern_data(isolated_root)
    verify_qualification_sillytavern(isolated_root)

    cera_port = _available_port_excluding(5101, relay_port, relay_st_port)
    st_port = _available_port_excluding(5101, relay_port, relay_st_port, cera_port)
    st_origin = f"http://127.0.0.1:{st_port}"
    st_process = _start_qualification_sillytavern(
        isolated_root,
        port=st_port,
        cera_port=cera_port,
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
            manual_action_authorizer=HashOnlyFileManualActionAuthorizer(
                output_root=root,
                timeout_seconds=manual_action_wait_seconds,
            ),
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
        retry_actions = int(backend["provider_stage_retry_actions"]) + int(
            st_result["provider_stage_retry_actions"]
        )
        retry_chains = int(backend["provider_stage_retry_chains"]) + int(
            st_result["provider_stage_retry_chains"]
        )
        retry_chain_actions = int(backend["provider_stage_retry_chain_actions"]) + int(
            st_result["provider_stage_retry_chain_actions"]
        )
        resume_prepared_actions = int(backend["provider_stage_resume_prepared_actions"]) + int(
            st_result["provider_stage_resume_prepared_actions"]
        )
        repair_recording_actions = int(backend["provider_stage_repair_recording_actions"]) + int(
            st_result["provider_stage_repair_recording_actions"]
        )
        control_actions = int(backend["provider_stage_control_actions"]) + int(
            st_result["provider_stage_control_actions"]
        )
        regenerate_actions = int(backend["explicit_regenerate_actions"]) + int(
            st_result["explicit_regenerate_actions"]
        )
        review_recording_repair_actions = int(backend["review_recording_repair_actions"]) + int(
            st_result["review_recording_repair_actions"]
        )
        externally_authorized_manual_actions = int(
            backend["externally_authorized_manual_actions"]
        ) + int(st_result["externally_authorized_manual_actions"])
        control_chain_actions = int(backend["provider_stage_control_chain_actions"]) + int(
            st_result["provider_stage_control_chain_actions"]
        )
        terminal_critical_failures = int(
            backend["provider_stage_retry_terminal_critical_failures"]
        ) + int(st_result["provider_stage_retry_terminal_critical_failures"])
        if (
            retry_actions != retry_chain_actions
            or control_actions != control_chain_actions
            or control_actions != retry_actions + resume_prepared_actions + repair_recording_actions
            or externally_authorized_manual_actions
            != control_actions + regenerate_actions + review_recording_repair_actions
            or terminal_critical_failures != 0
        ):
            raise StateConflictError("qualification provider-stage Retry evidence is incomplete")
        backend_timing = cast(dict[str, Any], backend["phase_timing_evidence"])
        sillytavern_timing = cast(dict[str, Any], st_result["phase_timing_evidence"])
        result = {
            "schema_version": "cera.pi_scene.full_model_complete_qualification.v4",
            "qualification_id": manifest["qualification_id"],
            "manifest_sha256": manifest["manifest_sha256"],
            "status": "passed",
            "backend_result_sha256": backend["result_sha256"],
            "sillytavern_result_sha256": st_result["result_sha256"],
            "backend_passed": backend["passed_fixtures"],
            "sillytavern_passed": st_result["passed_fixtures"],
            "sol_operations": (int(backend["sol_operations"]) + int(st_result["sol_operations"])),
            "sol_submitted_operations": (
                int(backend["sol_submitted_operations"])
                + int(st_result["sol_submitted_operations"])
            ),
            "sol_charged_operations": (
                int(backend["sol_charged_operations"]) + int(st_result["sol_charged_operations"])
            ),
            "deepseek_http_operations": (
                int(backend["deepseek_http_operations"])
                + int(st_result["deepseek_http_operations"])
            ),
            "backend_sequential_turns": backend["passed_fixtures"],
            "sillytavern_sequential_turns": st_result["passed_fixtures"],
            "sillytavern_runtime_restarts": st_result["restart_count"],
            "provider_stage_retry_actions": retry_actions,
            "provider_stage_retry_chains": retry_chains,
            "provider_stage_retry_chain_actions": retry_chain_actions,
            "provider_stage_resume_prepared_actions": resume_prepared_actions,
            "provider_stage_repair_recording_actions": repair_recording_actions,
            "provider_stage_control_actions": control_actions,
            "explicit_regenerate_actions": regenerate_actions,
            "review_recording_repair_actions": review_recording_repair_actions,
            "externally_authorized_manual_actions": externally_authorized_manual_actions,
            "provider_stage_control_chain_actions": control_chain_actions,
            "provider_stage_retry_terminal_critical_failures": terminal_critical_failures,
            "automatic_provider_stage_retry_actions": 0,
            "automatic_provider_stage_control_actions": 0,
            "automatic_manual_actions": 0,
            "recursive_recording_repair": False,
            "fallback_used": False,
            "timing_evidence": {
                "schema_version": "cera.pi_scene.complete_qualification_timing.v1",
                "backend_total": dict(cast(dict[str, Any], backend_timing["http_total"])),
                "sillytavern_total": dict(cast(dict[str, Any], sillytavern_timing["http_total"])),
                "sillytavern_overhead": dict(
                    cast(dict[str, Any], sillytavern_timing["sillytavern_overhead"])
                ),
                "backend_provider_transport_latency_sha256": canonical_sha256(
                    backend["provider_transport_latency_evidence"]
                ),
                "sillytavern_provider_transport_latency_sha256": canonical_sha256(
                    st_result["provider_transport_latency_evidence"]
                ),
                "overhead_estimated": False,
            },
            "review_relay_preflight_sha256": canonical_sha256(relay_proof),
            "chat_isolation_proof_sha256": canonical_sha256(isolation_proof),
            "isolated_sillytavern_manifest_sha256": isolated_manifest["manifest_sha256"],
            "staged_node_suite_proof_sha256": staged_node_proof["proof_sha256"],
            "installed_sillytavern_mutated": False,
        }
        result["result_sha256"] = canonical_sha256(result)
        _atomic_write_json(root / "QUALIFICATION_RESULT.json", result)
        return result
    finally:
        if service is not None:
            service.close()
        _stop_process(cast(Any, st_process))


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
        with urlopen(
            request,
            timeout=QUALIFICATION_HTTP_HARD_TIMEOUT_SECONDS,
        ) as response:
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


def _get_json_response(
    url: str,
    *,
    token: str,
    transport: str,
    path: str,
) -> ClientResponseV1:
    started = time.perf_counter_ns()
    request = Request(
        url,
        headers={"Authorization": f"Bearer {token}"},
        method="GET",
    )
    try:
        with urlopen(request, timeout=30) as response:
            status = response.status
            body = _decode_json_object(response.read())
    except HTTPError as exc:
        status = exc.code
        body = _decode_json_object(exc.read())
    return ClientResponseV1(
        transport=transport,
        path=path,
        status_code=status,
        duration_ms=max(0, (time.perf_counter_ns() - started) // 1_000_000),
        body=body,
    )


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
        "--spent-manifest",
        action="append",
        default=[],
        type=Path,
    )
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
    live_parser.add_argument(
        "--manual-action-wait-seconds",
        type=float,
        default=DEFAULT_MANUAL_ACTION_WAIT_SECONDS,
    )

    approve_parser = subparsers.add_parser("approve-manual-action")
    approve_parser.add_argument("--output-root", type=Path, required=True)
    approve_parser.add_argument("--checkpoint-sha256", required=True)

    args = parser.parse_args(argv)
    if args.mode == "freeze":
        result = freeze(
            output_root=args.output_root,
            fixture_path=args.fixture_set,
            qualification_id=args.qualification_id,
            sillytavern_source=args.sillytavern_source,
            spent_manifest_paths=tuple(args.spent_manifest),
        )
    elif args.mode == "provider-free-check":
        result = provider_free_check(
            fixture_path=args.fixture_set,
            manifest_path=args.manifest,
        )
    elif args.mode == "approve-manual-action":
        result = approve_manual_action(
            output_root=args.output_root,
            checkpoint_sha256=args.checkpoint_sha256,
        )
    else:
        result = live(
            output_root=args.output_root,
            fixture_path=args.fixture_set,
            manual_action_wait_seconds=args.manual_action_wait_seconds,
        )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
