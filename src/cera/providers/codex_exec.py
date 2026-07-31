"""One-shot Codex CLI structured-output runner.

This runner deliberately bypasses the Python SDK/app-server notification
router.  It uses the pinned CLI bundled with ``openai-codex-cli-bin``, consumes
the documented JSONL event stream, and reads the final schema-constrained
message from a temporary file.  It never retries, resumes, or falls back.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from typing import Any

from cera.errors import ErrorCode
from cera.serialization import canonical_json

from .codex import (
    CodexMcpRuntimeBinding,
    CodexWorkerResult,
    _terminate_codex_worker_tree,
)
from .codex_exec_contract import (
    CODEX_CLI_EXEC_COMPATIBILITY_ID,
    CODEX_CLI_EXEC_CONTRACT_SHA256,
    CODEX_CLI_EXEC_TRANSPORT_NAME,
    CODEX_CLI_EXEC_VERSION,
)
from .models import LiveProviderRoute, ProviderTransportError


_ALLOWED_NON_TOOL_ITEM_TYPES = frozenset({"agent_message", "reasoning"})
_REDACTED_ENVIRONMENT_KEYS = frozenset(
    {
        "OPENAI_API_KEY",
        "CODEX_API_KEY",
        "DEEPSEEK_API_KEY",
        "CERA_REQUEST_EVIDENCE_TOKEN",
    }
)


class CodexExecRunner:
    """Run one ChatGPT-authenticated Codex request in one CLI process."""

    def __init__(self) -> None:
        self.process_launch_count = 0
        self.request_submission_count = 0

    def run(
        self,
        *,
        route: LiveProviderRoute,
        prompt: str,
        output_schema: dict[str, Any],
        workspace: Path,
        mcp_binding: CodexMcpRuntimeBinding | None,
    ) -> CodexWorkerResult:
        if mcp_binding is not None:
            raise ProviderTransportError(
                ErrorCode.REASONER_CONTRACT_INVALID,
                "Codex CLI exec verifier cannot receive MCP authority",
            )
        if (
            route.transport_name != CODEX_CLI_EXEC_TRANSPORT_NAME
            or route.transport_version != CODEX_CLI_EXEC_VERSION
            or route.transport_compatibility_id
            != CODEX_CLI_EXEC_COMPATIBILITY_ID
            or route.transport_compatibility_source_sha256
            != CODEX_CLI_EXEC_CONTRACT_SHA256
        ):
            raise ProviderTransportError(
                ErrorCode.REASONER_CONTRACT_INVALID,
                "Codex CLI exec route is not pinned to the active contract",
            )
        if any(workspace.iterdir()):
            raise ProviderTransportError(
                ErrorCode.REASONER_CONTRACT_INVALID,
                "Codex CLI exec workspace must begin empty",
            )

        binary = _resolve_and_validate_codex_binary()
        environment = _codex_exec_environment()
        started = time.perf_counter()
        with tempfile.TemporaryDirectory(
            prefix="cera-codex-exec-control-"
        ) as control:
            control_root = Path(control)
            schema_path = control_root / "output-schema.json"
            output_path = control_root / "final-response.json"
            schema_path.write_text(
                canonical_json(output_schema),
                encoding="utf-8",
            )
            command = _build_codex_exec_command(
                binary=binary,
                route=route,
                workspace=workspace,
                schema_path=schema_path,
                output_path=output_path,
            )
            popen_kwargs: dict[str, Any] = {}
            if sys.platform == "win32":
                popen_kwargs["creationflags"] = (
                    subprocess.CREATE_NEW_PROCESS_GROUP
                    | getattr(subprocess, "CREATE_NO_WINDOW", 0)
                )
            else:
                popen_kwargs["start_new_session"] = True
            process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                cwd=workspace,
                env=environment,
                **popen_kwargs,
            )
            self.process_launch_count += 1
            self.request_submission_count += 1
            try:
                stdout, _ = process.communicate(
                    input=prompt + "\n",
                    timeout=route.timeout_seconds,
                )
            except subprocess.TimeoutExpired:
                _terminate_codex_worker_tree(process)
                raise ProviderTransportError(
                    ErrorCode.REASONER_UNAVAILABLE,
                    "Codex CLI exec qualification call timed out",
                    safe_diagnostics=(
                        "transport:timeout",
                        "worker_stage:cli_exec",
                        "transport_mode:cli_exec_one_shot",
                    ),
                    external_provider_calls_observed=1,
                ) from None
            if process.returncode != 0:
                raise ProviderTransportError(
                    ErrorCode.REASONER_UNAVAILABLE,
                    "Codex CLI exec qualification worker failed",
                    safe_diagnostics=(
                        "transport:cli_exec_failed",
                        "worker_stage:process_exit",
                        "transport_mode:cli_exec_one_shot",
                    ),
                    external_provider_calls_observed=1,
                )
            try:
                thread_id, usage = _parse_codex_exec_jsonl(stdout)
                output_text = output_path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError, ValueError):
                raise ProviderTransportError(
                    ErrorCode.REASONER_CONTRACT_INVALID,
                    "Codex CLI exec returned invalid structured evidence",
                    safe_diagnostics=(
                        "transport:invalid_cli_exec_evidence",
                        "transport_mode:cli_exec_one_shot",
                    ),
                    external_provider_calls_observed=1,
                ) from None
        if any(workspace.iterdir()):
            raise ProviderTransportError(
                ErrorCode.REASONER_CONTRACT_INVALID,
                "Codex CLI exec mutated the isolated workspace",
                safe_diagnostics=(
                    "transport:workspace_mutation",
                    "transport_mode:cli_exec_one_shot",
                ),
                external_provider_calls_observed=1,
            )
        elapsed_ms = max(0, round((time.perf_counter() - started) * 1000))
        return CodexWorkerResult(
            output_text=output_text,
            provider_request_id=f"cli-thread:{thread_id}",
            returned_model=route.model_name,
            duration_ms=elapsed_ms,
            input_tokens=usage["input_tokens"],
            cached_input_tokens=usage["cached_input_tokens"],
            output_tokens=usage["output_tokens"],
            reasoning_output_tokens=usage["reasoning_output_tokens"],
            transport_version=CODEX_CLI_EXEC_VERSION,
            transport_compatibility_id=(
                CODEX_CLI_EXEC_COMPATIBILITY_ID
            ),
            transport_compatibility_source_sha256=(
                CODEX_CLI_EXEC_CONTRACT_SHA256
            ),
            transport_compatibility_activated=True,
            buffered_early_completion_count=0,
            pre_registered_turn_count=0,
        )


def _resolve_and_validate_codex_binary() -> Path:
    try:
        from codex_cli_bin import bundled_codex_path

        binary = Path(bundled_codex_path()).resolve()
    except (ImportError, OSError):
        raise ProviderTransportError(
            ErrorCode.REASONER_UNAVAILABLE,
            "Pinned Codex CLI runtime is unavailable",
            safe_diagnostics=("transport:cli_runtime_unavailable",),
            external_provider_calls_observed=0,
        ) from None
    if not binary.is_file():
        raise ProviderTransportError(
            ErrorCode.REASONER_UNAVAILABLE,
            "Pinned Codex CLI runtime is unavailable",
            safe_diagnostics=("transport:cli_runtime_unavailable",),
            external_provider_calls_observed=0,
        )
    try:
        completed = subprocess.run(
            [str(binary), "--version"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            timeout=10,
            check=False,
            creationflags=(
                getattr(subprocess, "CREATE_NO_WINDOW", 0)
                if sys.platform == "win32"
                else 0
            ),
        )
    except (OSError, subprocess.TimeoutExpired):
        raise ProviderTransportError(
            ErrorCode.REASONER_UNAVAILABLE,
            "Pinned Codex CLI runtime could not be verified",
            safe_diagnostics=("transport:cli_runtime_unavailable",),
            external_provider_calls_observed=0,
        ) from None
    if (
        completed.returncode != 0
        or completed.stdout.strip()
        != f"codex-cli {CODEX_CLI_EXEC_VERSION}"
    ):
        raise ProviderTransportError(
            ErrorCode.REASONER_CONTRACT_INVALID,
            "Pinned Codex CLI runtime version is incompatible",
            safe_diagnostics=("transport:cli_runtime_version_mismatch",),
            external_provider_calls_observed=0,
        )
    return binary


def _codex_exec_environment() -> dict[str, str]:
    environment = os.environ.copy()
    for key in tuple(environment):
        if key.upper() in _REDACTED_ENVIRONMENT_KEYS:
            environment.pop(key, None)
    return environment


def _build_codex_exec_command(
    *,
    binary: Path,
    route: LiveProviderRoute,
    workspace: Path,
    schema_path: Path,
    output_path: Path,
) -> list[str]:
    return [
        str(binary),
        "exec",
        "--ephemeral",
        "--ignore-user-config",
        "--ignore-rules",
        "--skip-git-repo-check",
        "--json",
        "--color",
        "never",
        "--sandbox",
        "read-only",
        "--model",
        route.model_name,
        "--cd",
        str(workspace),
        "--config",
        f'model_reasoning_effort="{route.reasoning_effort}"',
        "--config",
        'approval_policy="never"',
        "--config",
        'web_search="disabled"',
        "--config",
        "mcp_servers={}",
        "--output-schema",
        str(schema_path),
        "--output-last-message",
        str(output_path),
        "-",
    ]


def _parse_codex_exec_jsonl(value: str) -> tuple[str, dict[str, int]]:
    thread_id: str | None = None
    turn_started = 0
    usage: dict[str, int] | None = None
    turn_completed = 0
    for raw_line in value.splitlines():
        if not raw_line.strip():
            continue
        try:
            event = json.loads(raw_line)
        except json.JSONDecodeError:
            raise ValueError("Codex CLI JSONL contained invalid JSON") from None
        if not isinstance(event, dict):
            raise ValueError("Codex CLI JSONL event must be an object")
        event_type = event.get("type")
        if event_type == "thread.started":
            candidate = event.get("thread_id")
            if thread_id is not None or not isinstance(candidate, str) or not candidate:
                raise ValueError("Codex CLI thread identity is invalid")
            thread_id = candidate
        elif event_type == "turn.started":
            turn_started += 1
        elif event_type in {"item.started", "item.updated", "item.completed"}:
            item = event.get("item")
            item_type = item.get("type") if isinstance(item, dict) else None
            if item_type not in _ALLOWED_NON_TOOL_ITEM_TYPES:
                raise ValueError("Codex CLI verifier attempted tool or state use")
        elif event_type == "turn.completed":
            turn_completed += 1
            candidate_usage = event.get("usage")
            if not isinstance(candidate_usage, dict):
                raise ValueError("Codex CLI completion usage is missing")
            usage = {}
            for key in (
                "input_tokens",
                "cached_input_tokens",
                "output_tokens",
                "reasoning_output_tokens",
            ):
                counter = candidate_usage.get(key)
                if type(counter) is not int or counter < 0:
                    raise ValueError("Codex CLI completion usage is invalid")
                usage[key] = counter
            if usage["cached_input_tokens"] > usage["input_tokens"]:
                raise ValueError("Codex CLI cached tokens exceed input tokens")
        elif event_type in {"turn.failed", "error"}:
            raise ValueError("Codex CLI reported a failed turn")
        else:
            raise ValueError("Codex CLI JSONL event type is unsupported")
    if (
        thread_id is None
        or turn_started != 1
        or turn_completed != 1
        or usage is None
    ):
        raise ValueError("Codex CLI JSONL lifecycle is incomplete")
    return thread_id, usage
