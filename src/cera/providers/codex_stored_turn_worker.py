"""Private one-turn worker for an existing stored Codex checkpoint thread."""

from __future__ import annotations

import json
from importlib.metadata import version
from pathlib import Path
import sys
import time

from .codex_worker import _runtime_config_and_environment, _safe_error_text
from .codex_observability import (
    CodexOperationTelemetryV1,
    CodexToolTimingV1,
    CodexUsageAccumulator,
)
from cera.serialization import text_sha256, to_primitive


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
            request_start_us = time.time_ns() // 1_000
            turn = thread.turn(
                request["prompt"],
                effort=ReasoningEffort(request["effort"]),
                output_schema=request["output_schema"],
                service_tier=request["service_tier"],
            )
            from openai_codex._run import _collect_turn_result
            from openai_codex.generated.v2_all import (
                ItemCompletedNotification,
                ItemStartedNotification,
                ThreadTokenUsageUpdatedNotification,
                TurnCompletedNotification,
            )

            events = []
            usage_accumulator = CodexUsageAccumulator()
            item_start_times: dict[str, int] = {}
            first_reasoning_us = None
            first_structured_output_us = None
            final_evidence_result_us = None
            provider_completion_us = None
            stream = turn.stream()
            try:
                for event in stream:
                    events.append(event)
                    payload = event.payload
                    observed_us = time.time_ns() // 1_000
                    if isinstance(payload, ItemStartedNotification):
                        item = (
                            payload.item.root
                            if hasattr(payload.item, "root")
                            else payload.item
                        )
                        item_type = getattr(item, "type", None)
                        item_id = getattr(item, "id", None)
                        started_us = payload.started_at_ms * 1_000
                        if isinstance(item_id, str):
                            item_start_times[item_id] = started_us
                        if item_type == "reasoning" and first_reasoning_us is None:
                            first_reasoning_us = started_us
                        if (
                            item_type == "agentMessage"
                            and first_structured_output_us is None
                        ):
                            first_structured_output_us = started_us
                    elif isinstance(payload, ItemCompletedNotification):
                        item = (
                            payload.item.root
                            if hasattr(payload.item, "root")
                            else payload.item
                        )
                        item_type = getattr(item, "type", None)
                        completed_us = payload.completed_at_ms * 1_000
                        if item_type == "reasoning" and first_reasoning_us is None:
                            first_reasoning_us = completed_us
                        if (
                            item_type == "agentMessage"
                            and first_structured_output_us is None
                        ):
                            first_structured_output_us = completed_us
                        if item_type == "mcpToolCall":
                            final_evidence_result_us = completed_us
                    elif isinstance(payload, ThreadTokenUsageUpdatedNotification):
                        usage = payload.token_usage
                        usage_accumulator.observe(
                            observed_at_unix_us=observed_us,
                            last={
                                "input_tokens": usage.last.input_tokens,
                                "cached_input_tokens": usage.last.cached_input_tokens,
                                "output_tokens": usage.last.output_tokens,
                                "reasoning_output_tokens": (
                                    usage.last.reasoning_output_tokens
                                ),
                            },
                            total={
                                "input_tokens": usage.total.input_tokens,
                                "cached_input_tokens": usage.total.cached_input_tokens,
                                "output_tokens": usage.total.output_tokens,
                                "reasoning_output_tokens": (
                                    usage.total.reasoning_output_tokens
                                ),
                            },
                        )
                    elif isinstance(payload, TurnCompletedNotification):
                        provider_completion_us = observed_us
            finally:
                stream.close()
            result = _collect_turn_result(iter(events), turn_id=turn.id)
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
        cumulative = usage_accumulator.cumulative
        if cumulative is None:
            raise RuntimeError("Codex stored turn omitted cumulative usage")
        (
            cumulative_input_tokens,
            cumulative_cached_input_tokens,
            cumulative_uncached_input_tokens,
            cumulative_output_tokens,
            cumulative_reasoning_tokens,
        ) = cumulative
        mcp_server_names: list[str] = []
        mcp_tool_names: list[str] = []
        mcp_tool_timings: list[CodexToolTimingV1] = []
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
            item_id = getattr(item, "id", None)
            completed_at_us = None
            for event in events:
                payload = event.payload
                if not isinstance(payload, ItemCompletedNotification):
                    continue
                completed_item = (
                    payload.item.root
                    if hasattr(payload.item, "root")
                    else payload.item
                )
                if getattr(completed_item, "id", None) == item_id:
                    completed_at_us = payload.completed_at_ms * 1_000
                    break
            mcp_tool_timings.append(
                CodexToolTimingV1(
                    sequence=len(mcp_tool_timings) + 1,
                    server_name=item.server,
                    tool_name=item.tool,
                    started_at_unix_us=(
                        item_start_times.get(item_id)
                        if isinstance(item_id, str)
                        else None
                    ),
                    completed_at_unix_us=completed_at_us,
                    status=str(status),
                )
            )
        telemetry = CodexOperationTelemetryV1(
            schema_version=CodexOperationTelemetryV1.SCHEMA_VERSION,
            request_sha256=None,
            provider_operation_id_sha256=text_sha256(result.id),
            provider_thread_id_sha256=text_sha256(request["provider_thread_id"]),
            provider_root_thread_id_sha256=None,
            accepted_parent_checkpoint_id_sha256=None,
            candidate_checkpoint_id_sha256=None,
            model=request["model"],
            reasoning_effort=request["effort"],
            fast_mode_enabled=False,
            packet_ready_unix_us=None,
            request_start_unix_us=request_start_us,
            first_reasoning_item_unix_us=first_reasoning_us,
            first_structured_output_item_unix_us=first_structured_output_us,
            final_evidence_result_unix_us=final_evidence_result_us,
            provider_completion_unix_us=(
                provider_completion_us or time.time_ns() // 1_000
            ),
            python_parse_start_unix_us=None,
            python_parse_completion_unix_us=None,
            python_validation_start_unix_us=None,
            python_validation_completion_unix_us=None,
            usage_steps=usage_accumulator.steps,
            cumulative_input_tokens=cumulative_input_tokens,
            cumulative_cached_input_tokens=cumulative_cached_input_tokens,
            cumulative_uncached_input_tokens=cumulative_uncached_input_tokens,
            cumulative_output_tokens=cumulative_output_tokens,
            cumulative_reasoning_tokens=cumulative_reasoning_tokens,
            tool_timings=tuple(mcp_tool_timings),
            tool_call_count=len(mcp_tool_timings),
            provider_attempt_count=1,
            finish_status=getattr(result.status, "value", str(result.status)),
            transport_error=None,
            unsupported_fields=(
                "request_sha256",
                "packet_ready_unix_us",
                "provider_root_thread_id_sha256",
                "accepted_parent_checkpoint_id_sha256",
                "candidate_checkpoint_id_sha256",
                "python_parse_start_unix_us",
                "python_parse_completion_unix_us",
                "python_validation_start_unix_us",
                "python_validation_completion_unix_us",
            ),
            retains_prompt=False,
            retains_output=False,
            retains_reasoning=False,
            retains_tool_arguments=False,
        )
        payload = {
            "output_text": result.final_response,
            "provider_request_id": result.id,
            "returned_model": request["model"],
            "duration_ms": result.duration_ms or 0,
            "input_tokens": cumulative_input_tokens,
            "cached_input_tokens": cumulative_cached_input_tokens,
            "output_tokens": cumulative_output_tokens,
            "reasoning_output_tokens": cumulative_reasoning_tokens,
            "last_step_output_tokens": result.usage.last.output_tokens,
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
            "operation_telemetry": to_primitive(telemetry),
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
