"""Private subprocess entrypoint for one bounded Codex SDK turn."""

from __future__ import annotations

import json
import sys
from importlib.metadata import version
from pathlib import Path

from cera.provider_dispatch_guard import assert_provider_dispatch_allowed
from cera.providers.codex_runtime_policy import (
    codex_app_server_config_overrides,
    codex_app_server_environment,
    start_codex_thread_without_environments,
    validate_codex_app_server_configuration,
    validate_codex_mcp_server_status,
    validate_codex_prompt_markers,
)
from cera.providers.codex_runtime_policy import (
    runtime_config_and_environment as _runtime_config_and_environment,
)
from cera.providers.codex_runtime_policy import (
    unsupported_completed_result_item_types as _unsupported_completed_result_item_types,
)

CODEX_REASONER_BASE_INSTRUCTIONS = """You are the CERA Scene Reasoner transport. Return only the requested JSON object. Do not use shell commands, files, web search, apps, skills, subagents, or external context. When the request-bound CERA evidence server is present, it is the only permitted tool source. Treat the supplied packet plus evidence returned by that server as the complete authority for this invocation. If evidence is insufficient, use the schema's uncertainty result instead of inventing facts."""

_BASE_INSTRUCTIONS_BY_ROLE = {
    "scene_reasoner": CODEX_REASONER_BASE_INSTRUCTIONS,
    "scene_realization_verifier": """You are the CERA Scene Realization Verifier transport. Inspect only the supplied candidate and authority packet. Return only the requested JSON object. Do not generate, rewrite, continue, repair, or improve story prose. Do not use shell commands, files, web search, apps, skills, subagents, tools, or external context. If a semantic claim cannot be established from the packet, return the schema's inconclusive result rather than inventing evidence.""",
}


def _safe_error_text(exc: Exception, request: dict | None) -> str:
    """Return a bounded diagnostic with prompt and bearer material removed."""

    message = " ".join(str(exc).split())
    if request is not None:
        prompt = request.get("prompt")
        if isinstance(prompt, str) and prompt:
            message = message.replace(prompt, "[prompt-redacted]")
        binding = request.get("mcp_binding")
        if isinstance(binding, dict):
            token = binding.get("bearer_token")
            if isinstance(token, str) and token:
                message = message.replace(token, "[credential-redacted]")
    return message[:800] or "no-message"


def main() -> int:
    assert_provider_dispatch_allowed("providers.codex.worker.sdk_start")
    stage = "request_decode"
    request = None
    try:
        request = json.loads(sys.stdin.read())
        model = request["model"]
        effort_name = request["effort"]
        service_tier = request.get("service_tier")
        if service_tier not in {None, "priority"}:
            raise ValueError("Codex worker service tier is unsupported")
        role = request["role"]
        prompt = request["prompt"]
        validate_codex_prompt_markers(prompt)
        output_schema = request["output_schema"]
        workspace = Path(request["workspace"])
        progress_path = Path(request["progress_path"])
        expected_transport_version = request["transport_version"]
        mcp_binding = request.get("mcp_binding")
        try:
            base_instructions = _BASE_INSTRUCTIONS_BY_ROLE[role]
        except KeyError:
            raise ValueError("Codex qualification role is not supported") from None
        expected_progress_path = workspace / ".cera_codex_worker_progress.json"
        if (
            not workspace.is_absolute()
            or not workspace.is_dir()
            or progress_path != expected_progress_path
            or set(workspace.iterdir()) != {progress_path}
        ):
            raise ValueError("qualification workspace is not isolated")

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
        from openai_codex.api import ReasoningEffort

        record_stage("transport_version_check")
        if version("openai-codex") != expected_transport_version:
            raise RuntimeError("Codex SDK version does not match the qualified route")

        config, codex_environment = _runtime_config_and_environment(mcp_binding)
        app_server_overrides = codex_app_server_config_overrides(workspace)
        record_stage("sdk_start")
        with Codex(
            CodexConfig(
                config_overrides=app_server_overrides,
                cwd=str(workspace),
                env=codex_app_server_environment(codex_environment),
            )
        ) as codex:
            record_stage("sdk_compatibility_install")
            from cera.providers.codex_sdk_compat import (
                install_early_turn_completion_buffer,
            )

            compatibility_state = install_early_turn_completion_buffer(codex)
            validate_codex_app_server_configuration(
                codex,
                cwd=workspace,
            )
            validate_codex_mcp_server_status(codex)
            record_stage("account_check")
            account = codex.account()
            if account.account is None:
                raise RuntimeError("ChatGPT Codex session is unavailable")
            record_stage("thread_start")
            thread = start_codex_thread_without_environments(
                codex,
                model=model,
                cwd=str(workspace),
                ephemeral=True,
                base_instructions=base_instructions,
                config=config,
                service_name=f"cera_{role}_qualification",
                service_tier=service_tier,
            )
            validate_codex_mcp_server_status(
                codex,
                thread_id=thread.id,
                request_server_name=(None if mcp_binding is None else mcp_binding["server_name"]),
                request_tool_names=(() if mcp_binding is None else mcp_binding["enabled_tools"]),
            )
            record_stage("thread_run")
            result = thread.run(
                prompt,
                effort=ReasoningEffort(effort_name),
                output_schema=output_schema,
                service_tier=service_tier,
            )
            record_stage("thread_read")
            thread_state = thread.read()
            record_stage("model_provider_check")
            if thread_state.thread.model_provider != "openai":
                raise RuntimeError("Codex thread used an unexpected model provider")
            record_stage("cli_version_check")
            if thread_state.thread.cli_version != expected_transport_version:
                raise RuntimeError("Codex CLI runtime version does not match the SDK route")
        record_stage("result_check")
        if result.final_response is None or result.usage is None:
            raise RuntimeError("Codex turn omitted response or usage")
        usage = result.usage.last
        mcp_server_names: list[str] = []
        mcp_tool_names: list[str] = []
        failed_mcp_calls = 0
        unsupported_item_types = _unsupported_completed_result_item_types(result.items)
        for wrapped in result.items:
            item = wrapped.root if hasattr(wrapped, "root") else wrapped
            if getattr(item, "type", None) != "mcpToolCall":
                continue
            mcp_server_names.append(item.server)
            mcp_tool_names.append(item.tool)
            status = getattr(item.status, "value", item.status)
            if status != "completed" or item.error is not None:
                failed_mcp_calls += 1
        response = {
            "output_text": result.final_response,
            "provider_request_id": result.id,
            "returned_model": model,
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
            "buffered_early_completion_count": (
                compatibility_state.buffered_early_completion_count
            ),
            "pre_registered_turn_count": (compatibility_state.pre_registered_turn_count),
        }
        record_stage("response_encode")
        # The parent/worker framing protocol is ASCII JSON so Windows code
        # pages cannot corrupt provider prose.
        sys.stdout.write(json.dumps(response, ensure_ascii=True, sort_keys=True))
        return 0
    except Exception as exc:
        diagnostic = (
            "codex qualification worker failed: "
            f"{type(exc).__name__}@{stage}: {_safe_error_text(exc, request)}\n"
        )
        sys.stderr.write(diagnostic.encode("ascii", "backslashreplace").decode("ascii"))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
