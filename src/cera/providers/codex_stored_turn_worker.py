"""Private one-turn worker for an existing stored Codex checkpoint thread."""

from __future__ import annotations

import json
from importlib.metadata import version
from pathlib import Path
import sys

from .codex_worker import _runtime_config_and_environment, _safe_error_text


def _validate_request(request: dict) -> None:
    required = {
        "model",
        "effort",
        "service_tier",
        "role",
        "prompt",
        "output_schema",
        "workspace",
        "progress_path",
        "transport_version",
        "mcp_binding",
        "provider_thread_id",
    }
    if set(request) != required:
        raise ValueError("stored Codex request has an invalid field set")
    if request["role"] != "scene_reasoner":
        raise ValueError("stored Codex worker is Reasoner-only")
    if request["service_tier"] not in {None, "priority"}:
        raise ValueError("stored Codex service tier is unsupported")
    if not isinstance(request["provider_thread_id"], str) or not request[
        "provider_thread_id"
    ].strip():
        raise ValueError("stored Codex request omitted its thread ID")
    workspace = Path(request["workspace"])
    progress_path = Path(request["progress_path"])
    if (
        not workspace.is_absolute()
        or not workspace.is_dir()
        or progress_path != workspace / ".cera_codex_worker_progress.json"
        or set(workspace.iterdir()) != {progress_path}
    ):
        raise ValueError("stored Codex workspace is not isolated")


def main() -> int:
    request: dict | None = None
    stage = "request_decode"
    try:
        request = json.loads(sys.stdin.read())
        _validate_request(request)
        workspace = Path(request["workspace"])
        progress_path = Path(request["progress_path"])

        def record_stage(value: str) -> None:
            nonlocal stage
            stage = value
            progress_path.write_text(
                json.dumps(
                    {
                        "schema_version": "cera.codex_worker_progress.v1",
                        "stage": value,
                    },
                    ensure_ascii=True,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                encoding="utf-8",
            )

        record_stage("sdk_import")
        from openai_codex import Codex, CodexConfig
        from openai_codex.api import ApprovalMode, ReasoningEffort

        record_stage("transport_version_check")
        if version("openai-codex") != request["transport_version"]:
            raise RuntimeError("Codex SDK version does not match the stored route")
        config, codex_environment = _runtime_config_and_environment(
            request["mcp_binding"]
        )
        record_stage("sdk_start")
        with Codex(
            CodexConfig(
                config_overrides=("mcp_servers={}",),
                env=codex_environment,
            )
        ) as codex:
            record_stage("sdk_compatibility_install")
            from cera.providers.codex_sdk_compat import (
                install_early_turn_completion_buffer,
            )

            compatibility_state = install_early_turn_completion_buffer(codex)
            record_stage("account_check")
            if codex.account().account is None:
                raise RuntimeError("ChatGPT Codex session is unavailable")
            record_stage("thread_resume")
            thread = codex.thread_resume(
                request["provider_thread_id"],
                model=request["model"],
                cwd=str(workspace),
                config=config,
                service_tier=request["service_tier"],
                approval_mode=ApprovalMode.deny_all,
            )
            if thread.id != request["provider_thread_id"]:
                raise RuntimeError("Codex resumed a different stored thread")
            record_stage("thread_run")
            result = thread.run(
                request["prompt"],
                effort=ReasoningEffort(request["effort"]),
                output_schema=request["output_schema"],
                service_tier=request["service_tier"],
            )
            record_stage("thread_read")
            thread_state = thread.read()
            record_stage("model_provider_check")
            if thread_state.thread.model_provider != "openai":
                raise RuntimeError("Codex thread used an unexpected model provider")
            record_stage("cli_version_check")
            if thread_state.thread.cli_version != request["transport_version"]:
                raise RuntimeError("Codex CLI runtime version does not match stored route")

        record_stage("result_check")
        if result.final_response is None or result.usage is None:
            raise RuntimeError("Codex stored turn omitted response or usage")
        usage = result.usage.last
        mcp_server_names: list[str] = []
        mcp_tool_names: list[str] = []
        failed_mcp_calls = 0
        for wrapped in result.items:
            item = wrapped.root if hasattr(wrapped, "root") else wrapped
            if getattr(item, "type", None) != "mcpToolCall":
                continue
            mcp_server_names.append(item.server)
            mcp_tool_names.append(item.tool)
            status = getattr(item.status, "value", item.status)
            if status != "completed":
                failed_mcp_calls += 1
        payload = {
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
            "transport_compatibility_id": compatibility_state.compatibility_id,
            "transport_compatibility_source_sha256": compatibility_state.source_sha256,
            "transport_compatibility_activated": True,
            "buffered_early_completion_count": (
                compatibility_state.buffered_early_completion_count
            ),
            "pre_registered_turn_count": compatibility_state.pre_registered_turn_count,
        }
        record_stage("response_encode")
        sys.stdout.write(
            json.dumps(
                payload,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 0
    except Exception as exc:
        sys.stderr.write(
            "codex qualification worker failed: "
            f"stage={stage}; {_safe_error_text(exc, request)}"
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
