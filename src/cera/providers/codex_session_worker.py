"""Private persistent no-MCP Codex SDK worker for qualified process reuse.

The process owns one app-server connection but creates a fresh ephemeral Codex
thread for every newline-delimited request.  It never reuses conversation or
story state.  Any request failure terminates the worker after one bounded error
envelope; the parent never resubmits that request.
"""

from __future__ import annotations

import json
import sys
from importlib.metadata import version
from pathlib import Path
from typing import Any

from cera.provider_dispatch_guard import (
    assert_provider_dispatch_allowed,
    is_external_provider_boundary,
)

from .codex_runtime_policy import (
    codex_app_server_config_overrides,
    codex_app_server_environment,
    runtime_config_and_environment,
    start_codex_thread_without_environments,
    unsupported_completed_result_item_types,
    validate_codex_app_server_configuration,
    validate_codex_mcp_server_status,
    validate_codex_prompt_markers,
)

_PROTOCOL_VERSION = "cera.codex_persistent_no_mcp.v1"
_BASE_INSTRUCTIONS_BY_ROLE = {
    "scene_reasoner": """You are the CERA Scene Reasoner transport. Return only the requested JSON object. Do not use shell commands, files, web search, apps, skills, subagents, tools, or external context. Treat the supplied packet as the complete authority for this invocation. If evidence is insufficient, use the schema's uncertainty result instead of inventing facts.""",
    "scene_realization_verifier": """You are the CERA Scene Realization Verifier transport. Inspect only the supplied candidate and authority packet. Return only the requested JSON object. Do not generate, rewrite, continue, repair, or improve story prose. Do not use shell commands, files, web search, apps, skills, subagents, tools, or external context. If a semantic claim cannot be established from the packet, return the schema's inconclusive result rather than inventing evidence.""",
}


def _safe_error_text(exc: Exception, request: dict[str, Any] | None) -> str:
    message = " ".join(str(exc).split())
    if request is not None:
        prompt = request.get("prompt")
        if isinstance(prompt, str) and prompt:
            message = message.replace(prompt, "[prompt-redacted]")
    return message[:800] or "no-message"


def _record_stage(request: dict[str, Any], stage: str, sequence: int) -> None:
    progress_path = Path(request["progress_path"])
    progress_path.write_text(
        json.dumps(
            {
                "schema_version": "cera.codex_worker_progress.v1",
                "stage": stage,
            },
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )


def _validate_request(request: dict[str, Any]) -> None:
    required = {
        "protocol_version",
        "model",
        "effort",
        "role",
        "prompt",
        "output_schema",
        "workspace",
        "progress_path",
        "transport_version",
        "mcp_binding",
    }
    if set(request) != required:
        raise ValueError("persistent Codex request has an invalid field set")
    if request["protocol_version"] != _PROTOCOL_VERSION:
        raise ValueError("persistent Codex protocol version is unsupported")
    if request["mcp_binding"] is not None:
        raise ValueError("persistent Codex worker cannot receive MCP authority")
    validate_codex_prompt_markers(request.get("prompt"))
    role = request["role"]
    if role not in _BASE_INSTRUCTIONS_BY_ROLE:
        raise ValueError("persistent Codex role is unsupported")
    workspace = Path(request["workspace"])
    progress_path = Path(request["progress_path"])
    if (
        not workspace.is_absolute()
        or not workspace.is_dir()
        or progress_path != workspace / ".cera_codex_worker_progress.json"
        or set(workspace.iterdir()) != {progress_path}
    ):
        raise ValueError("persistent Codex workspace is not isolated")


def _config() -> dict[str, Any]:
    config, _environment = runtime_config_and_environment(None)
    return config


def _run_request(
    codex,
    request: dict[str, Any],
    *,
    sequence: int,
    compatibility_state,
) -> dict[str, Any]:
    assert_provider_dispatch_allowed(
        "providers.codex.session_worker.request",
        external_provider_boundary=is_external_provider_boundary(codex),
    )
    from openai_codex.api import ReasoningEffort

    _validate_request(request)
    validate_codex_app_server_configuration(
        codex,
        cwd=request["workspace"],
    )
    expected_version = request["transport_version"]
    _record_stage(request, "thread_start", sequence)
    thread = start_codex_thread_without_environments(
        codex,
        model=request["model"],
        cwd=request["workspace"],
        ephemeral=True,
        base_instructions=_BASE_INSTRUCTIONS_BY_ROLE[request["role"]],
        config=_config(),
        service_name=f"cera_{request['role']}_persistent_qualification",
    )
    validate_codex_mcp_server_status(codex, thread_id=thread.id)
    _record_stage(request, "thread_run", sequence)
    result = thread.run(
        request["prompt"],
        effort=ReasoningEffort(request["effort"]),
        output_schema=request["output_schema"],
    )
    _record_stage(request, "thread_read", sequence)
    thread_state = thread.read()
    _record_stage(request, "model_provider_check", sequence)
    if thread_state.thread.model_provider != "openai":
        raise RuntimeError("Codex thread used an unexpected model provider")
    _record_stage(request, "cli_version_check", sequence)
    if thread_state.thread.cli_version != expected_version:
        raise RuntimeError("Codex CLI runtime version does not match the SDK route")
    _record_stage(request, "result_check", sequence)
    if result.final_response is None or result.usage is None:
        raise RuntimeError("Codex turn omitted response or usage")
    usage = result.usage.last
    mcp_server_names: list[str] = []
    mcp_tool_names: list[str] = []
    failed_mcp_calls = 0
    unsupported_item_types = unsupported_completed_result_item_types(result.items)
    for wrapped in result.items:
        item = wrapped.root if hasattr(wrapped, "root") else wrapped
        if getattr(item, "type", None) != "mcpToolCall":
            continue
        mcp_server_names.append(item.server)
        mcp_tool_names.append(item.tool)
        status = getattr(item.status, "value", item.status)
        if status != "completed" or item.error is not None:
            failed_mcp_calls += 1
    return {
        "output_text": result.final_response,
        "provider_request_id": result.id,
        "returned_model": request["model"],
        "duration_ms": result.duration_ms or 0,
        "input_tokens": usage.input_tokens,
        "cached_input_tokens": usage.cached_input_tokens,
        "output_tokens": usage.output_tokens,
        "reasoning_output_tokens": usage.reasoning_output_tokens,
        "transport_version": thread_state.thread.cli_version,
        "mcp_server_names": mcp_server_names,
        "mcp_tool_names": mcp_tool_names,
        "mcp_tool_call_count": len(mcp_tool_names),
        "mcp_failed_tool_call_count": failed_mcp_calls,
        "unsupported_item_types": unsupported_item_types,
        "transport_compatibility_id": compatibility_state.compatibility_id,
        "transport_compatibility_source_sha256": (compatibility_state.source_sha256),
        "transport_compatibility_activated": True,
        "buffered_early_completion_count": (compatibility_state.buffered_early_completion_count),
        "pre_registered_turn_count": (compatibility_state.pre_registered_turn_count),
    }


def main() -> int:
    assert_provider_dispatch_allowed("providers.codex.session_worker.sdk_start")
    request: dict[str, Any] | None = None
    stage = "sdk_import"
    try:
        from openai_codex import Codex, CodexConfig

        first_line = sys.stdin.readline()
        if not first_line:
            return 0
        request = json.loads(first_line)
        _validate_request(request)
        expected_version = request["transport_version"]
        session_identity = (
            request["role"],
            request["model"],
            request["effort"],
        )
        if version("openai-codex") != expected_version:
            raise RuntimeError("Codex SDK version does not match the qualified route")
        app_server_cwd = Path(request["workspace"]).resolve()
        app_server_overrides = codex_app_server_config_overrides(app_server_cwd)
        stage = "sdk_start"
        with Codex(
            CodexConfig(
                config_overrides=app_server_overrides,
                cwd=str(app_server_cwd),
                env=codex_app_server_environment(),
            )
        ) as codex:
            stage = "sdk_compatibility_install"
            from cera.providers.codex_sdk_compat import (
                install_early_turn_completion_buffer,
            )

            compatibility_state = install_early_turn_completion_buffer(codex)
            validate_codex_app_server_configuration(
                codex,
                cwd=app_server_cwd,
            )
            validate_codex_mcp_server_status(codex)
            stage = "account_check"
            account = codex.account()
            if account.account is None:
                raise RuntimeError("ChatGPT Codex session is unavailable")
            sequence = 0
            line = first_line
            while line:
                if not line.strip():
                    line = sys.stdin.readline()
                    continue
                request = json.loads(line)
                _validate_request(request)
                if request["transport_version"] != expected_version:
                    raise ValueError("persistent Codex transport version changed")
                if (
                    request["role"],
                    request["model"],
                    request["effort"],
                ) != session_identity:
                    raise ValueError("persistent Codex role/model/effort identity changed")
                sequence += 1
                stage = "request_execution"
                result = _run_request(
                    codex,
                    request,
                    sequence=sequence,
                    compatibility_state=compatibility_state,
                )
                _record_stage(request, "response_encode", sequence)
                sys.stdout.write(
                    json.dumps(
                        {"ok": True, "result": result},
                        # The parent/worker framing protocol is ASCII JSON so
                        # Windows code pages cannot corrupt provider prose.
                        ensure_ascii=True,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    + "\n"
                )
                sys.stdout.flush()
                line = sys.stdin.readline()
        return 0
    except Exception as exc:
        envelope = {
            "ok": False,
            "error_class": type(exc).__name__,
            "stage": stage,
            "message": _safe_error_text(exc, request),
        }
        sys.stdout.write(
            json.dumps(envelope, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n"
        )
        sys.stdout.flush()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
