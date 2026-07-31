"""Run five one-shot Sol-medium calls through a test-only branch thread tree.

This live diagnostic qualifies the Codex SDK thread-fork transport and the
current v24 Reasoner contract. It never calls DeepSeek, publishes prose, or
writes story state. A structurally accepted candidate becomes only the parent
thread for the next benchmark turn; that benchmark continuity is not canon and
does not bypass CERA's creator-review or Python-commit requirements.
"""

from __future__ import annotations

from datetime import datetime, timezone
import argparse
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import time
from typing import Any

from cera.providers import CodexSDKTransport, codex_reasoner_candidate
from cera.providers.codex import CodexWorkerResult
from cera.providers.codex_sdk_compat import install_early_turn_completion_buffer
from cera.providers.codex_worker import _BASE_INSTRUCTIONS_BY_ROLE
from cera.reasoner import CodexSceneReasonerPort
from cera.reasoner.codex import build_codex_reasoner_packet, build_codex_reasoner_prompt
from cera.runtime import HanezawaHumanTestWorld
from cera.serialization import canonical_json, text_sha256, to_primitive

from scripts.run_codex_branch_session_ab import codex_config, outcome_summary, prepare_message


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = (
    ROOT
    / "evaluation"
    / "evidence"
    / "branch_bound_reasoner_live_five_2026-07-31_v2"
)
BENCHMARK_ID = "branch-bound-reasoner-live-five-2026-07-31-v2"
PACKET_MARKER = "The complete authoritative packet follows as canonical JSON:"
MESSAGES = (
    "Hello, my name is Ted. Is this the Hanezawa household?",
    (
        "Sakura has confirmed the address but remains cautious at the threshold. "
        'Ted says, "I am Ted, the housemate expected today."'
    ),
    (
        "Adjustment: preserve Sakura's established distrust and keep any trauma "
        "response subtle rather than making her openly welcoming. Ted waits "
        "outside without entering."
    ),
    (
        'Ted says, "I understand. You can verify me before letting me in." '
        "Continue according to Sakura's character and the household situation."
    ),
    (
        "Tomi hears Ted's name from inside and interrupts because she wants to "
        "see the expected housemate. Sakura still controls the doorway and "
        "responds according to her established character."
    ),
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def split_active_prompt(prompt: str) -> tuple[str, str]:
    delimiter = "\n" + PACKET_MARKER + "\n"
    if prompt.count(delimiter) != 1:
        raise RuntimeError("active Reasoner prompt cannot be split exactly once")
    stable, packet_json = prompt.split(delimiter, 1)
    return stable, PACKET_MARKER + "\n" + packet_json


class SplitPromptTransport:
    """Move the byte-identical stable v24 prefix into thread instructions."""

    def __init__(self, transport: CodexSDKTransport, stable_instructions: str) -> None:
        self.transport = transport
        self.route = transport.route
        self.stable_instructions = stable_instructions

    def invoke(self, prompt: str, **kwargs):
        stable, variable = split_active_prompt(prompt)
        if stable != self.stable_instructions:
            raise RuntimeError("Reasoner stable instructions changed within the batch")
        return self.transport.invoke(variable, **kwargs)


class ForkingSessionRunner:
    """One ephemeral root turn followed by candidate-fork attempts."""

    def __init__(self, *, workspace: Path, stable_instructions: str) -> None:
        self.workspace = workspace
        self.stable_instructions = stable_instructions
        self._codex_context = None
        self.codex = None
        self.compatibility_state = None
        self.root_thread = None
        self.active_thread = None
        self.accepted_thread = None
        self.thread_start_count = 0
        self.thread_fork_count = 0
        self.root_dispatched = False

    def __enter__(self) -> "ForkingSessionRunner":
        from openai_codex import Codex, CodexConfig

        self._codex_context = Codex(
            CodexConfig(config_overrides=("mcp_servers={}",), env={})
        )
        self.codex = self._codex_context.__enter__()
        self.compatibility_state = install_early_turn_completion_buffer(self.codex)
        account = self.codex.account()
        if account.account is None:
            raise RuntimeError("ChatGPT Codex session is unavailable")
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        if self._codex_context is not None:
            self._codex_context.__exit__(exc_type, exc, traceback)

    def start_root(self, route) -> None:
        from openai_codex.api import ApprovalMode

        assert self.codex is not None
        base = (
            _BASE_INSTRUCTIONS_BY_ROLE["scene_reasoner"]
            + "\n\n"
            + self.stable_instructions
        )
        self.root_thread = self.codex.thread_start(
            model=route.model_name,
            cwd=str(self.workspace),
            ephemeral=True,
            base_instructions=base,
            config=codex_config(),
            service_name="cera_branch_bound_reasoner_live_five",
            approval_mode=ApprovalMode.deny_all,
        )
        self.accepted_thread = self.root_thread
        self.thread_start_count = 1

    def fork_candidate(self, route) -> tuple[str, str]:
        from openai_codex.api import ApprovalMode

        assert self.codex is not None and self.accepted_thread is not None
        parent_id = self.accepted_thread.id
        if self.accepted_thread is self.root_thread and not self.root_dispatched:
            # App-server can fork only a thread with an existing rollout. The
            # cold first turn establishes the root; every later candidate is a
            # real fork from a structurally accepted benchmark parent.
            self.active_thread = self.root_thread
            self.root_dispatched = True
            return text_sha256(parent_id), text_sha256(parent_id)
        self.active_thread = self.codex.thread_fork(
            parent_id,
            approval_mode=ApprovalMode.deny_all,
            cwd=str(self.workspace),
            ephemeral=True,
            model=route.model_name,
            config=codex_config(),
        )
        self.thread_fork_count += 1
        return text_sha256(parent_id), text_sha256(self.active_thread.id)

    def accept_active_for_benchmark(self) -> None:
        if self.active_thread is None:
            raise RuntimeError("no active candidate thread")
        self.accepted_thread = self.active_thread

    def discard_active(self) -> None:
        self.active_thread = None

    def run(self, *, route, prompt, output_schema, workspace, mcp_binding):
        from openai_codex.api import ReasoningEffort

        if mcp_binding is not None:
            raise ValueError("branch-session live five is deliberately no-MCP")
        if workspace != self.workspace or self.active_thread is None:
            raise ValueError("branch-session candidate workspace/thread is invalid")
        started = time.perf_counter()
        result = self.active_thread.run(
            prompt,
            effort=ReasoningEffort(route.reasoning_effort),
            output_schema=output_schema,
        )
        wall_ms = int((time.perf_counter() - started) * 1000)
        thread_state = self.active_thread.read()
        if thread_state.thread.model_provider != "openai":
            raise RuntimeError("Codex thread used an unexpected provider")
        if thread_state.thread.cli_version != route.transport_version:
            raise RuntimeError("Codex thread used an unexpected runtime version")
        if result.final_response is None or result.usage is None:
            raise RuntimeError("Codex thread omitted response or usage")
        if any(
            getattr((item.root if hasattr(item, "root") else item), "type", None)
            == "mcpToolCall"
            for item in result.items
        ):
            raise RuntimeError("Codex thread unexpectedly used MCP")
        usage = result.usage.last
        assert self.compatibility_state is not None
        return CodexWorkerResult(
            output_text=result.final_response,
            provider_request_id=result.id,
            returned_model=route.model_name,
            duration_ms=result.duration_ms or wall_ms,
            input_tokens=usage.input_tokens,
            cached_input_tokens=usage.cached_input_tokens,
            output_tokens=usage.output_tokens,
            reasoning_output_tokens=usage.reasoning_output_tokens,
            transport_version=thread_state.thread.cli_version,
            transport_compatibility_id=self.compatibility_state.compatibility_id,
            transport_compatibility_source_sha256=(
                self.compatibility_state.source_sha256
            ),
            transport_compatibility_activated=True,
            buffered_early_completion_count=(
                self.compatibility_state.buffered_early_completion_count
            ),
            pre_registered_turn_count=max(
                1, self.compatibility_state.pre_registered_turn_count
            ),
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        raise SystemExit(f"refusing to overwrite live evidence: {output}")
    output.mkdir(parents=True)

    summary: dict[str, Any] = {
        "schema_version": "cera.branch_bound_reasoner_live_five.v1",
        "benchmark_id": BENCHMARK_ID,
        "started_at": utc_now(),
        "status": "running",
        "model": "gpt-5.6-sol",
        "effort": "medium",
        "reasoner_adapter": "cera.codex_python_sdk_reasoner.v24",
        "reasoner_prompt": "cera.codex_scene_reasoner_prompt.v24",
        "calls_authorized": 5,
        "provider_calls": 0,
        "retry_count": 0,
        "fallback_count": 0,
        "deepseek_calls": 0,
        "story_state_committed": False,
        "production_route_changed": False,
        "sillytavern_changed": False,
        "benchmark_continuity_is_canon": False,
        "turns": [],
    }
    write_json(output / "summary.json", summary)

    with TemporaryDirectory(prefix="cera-branch-live-five-") as temporary:
        temporary_path = Path(temporary)
        world = HanezawaHumanTestWorld.initialize(
            ROOT, temporary_path / "world.sqlite3", replace=True
        )
        workspace = temporary_path / "reasoner_workspace"
        workspace.mkdir()
        requests = tuple(
            prepare_message(
                world,
                message=message,
                session_id="branch-bound-reasoner-live-five",
            ).application_request.reasoner_request
            for message in MESSAGES
        )
        packets = tuple(
            build_codex_reasoner_packet(request, evidence_tools_available=False)
            for request in requests
        )
        prompts = tuple(build_codex_reasoner_prompt(packet) for packet in packets)
        stable_instructions, _ = split_active_prompt(prompts[0])
        if any(split_active_prompt(prompt)[0] != stable_instructions for prompt in prompts):
            raise RuntimeError("active Reasoner stable prefix varied across five turns")
        summary["stable_instructions_sha256"] = text_sha256(stable_instructions)
        summary["stable_instructions_bytes"] = len(
            stable_instructions.encode("utf-8")
        )
        route = codex_reasoner_candidate(model="gpt-5.6-sol", effort="medium")

        with ForkingSessionRunner(
            workspace=workspace, stable_instructions=stable_instructions
        ) as runner:
            runner.start_root(route)
            transport = SplitPromptTransport(
                CodexSDKTransport(route, workspace=workspace, runner=runner),
                stable_instructions,
            )
            port = CodexSceneReasonerPort(transport, evidence_tools_enabled=False)
            for index, (message, request, packet, prompt) in enumerate(
                zip(MESSAGES, requests, packets, prompts), start=1
            ):
                record: dict[str, Any] = {
                    "index": index,
                    "message_sha256": text_sha256(message),
                    "request_sha256": request.request_sha256,
                    "packet_sha256": text_sha256(canonical_json(packet)),
                    "packet_bytes": len(canonical_json(packet).encode("utf-8")),
                    "legacy_full_prompt_sha256": text_sha256(prompt),
                    "legacy_full_prompt_bytes": len(prompt.encode("utf-8")),
                    "variable_prompt_sha256": text_sha256(split_active_prompt(prompt)[1]),
                    "variable_prompt_bytes": len(
                        split_active_prompt(prompt)[1].encode("utf-8")
                    ),
                    "status": "started",
                }
                turn_started = time.perf_counter()
                try:
                    parent_hash, candidate_hash = runner.fork_candidate(route)
                    record["parent_thread_sha256"] = parent_hash
                    record["candidate_thread_sha256"] = candidate_hash
                    summary["provider_calls"] += 1
                    call = port.reason(request, world.service)
                    receipt = call.provider_call_receipt
                    runner.accept_active_for_benchmark()
                    record.update(
                        {
                            "status": "passed",
                            "benchmark_parent_promoted": True,
                            "provider_receipt": to_primitive(receipt),
                            "duration_seconds": round(receipt.duration_ms / 1000, 3),
                            "input_tokens": receipt.input_tokens,
                            "cached_input_tokens": receipt.cached_input_tokens,
                            "uncached_input_tokens": (
                                receipt.input_tokens - receipt.cached_input_tokens
                            ),
                            "output_tokens": receipt.output_tokens,
                            "reasoning_output_tokens": receipt.reasoning_output_tokens,
                            "outcome": outcome_summary(call),
                        }
                    )
                except Exception as exc:
                    runner.discard_active()
                    record.update(
                        {
                            "status": "failed",
                            "benchmark_parent_promoted": False,
                            "error_type": type(exc).__name__,
                            "error_message": str(exc) or type(exc).__name__,
                        }
                    )
                    if getattr(exc, "provider_call_receipt", None) is not None:
                        record["provider_receipt"] = to_primitive(
                            exc.provider_call_receipt
                        )
                record["wall_seconds"] = round(
                    time.perf_counter() - turn_started, 3
                )
                summary["turns"].append(record)
                summary["thread_start_count"] = runner.thread_start_count
                summary["thread_fork_count"] = runner.thread_fork_count
                write_json(output / "summary.json", summary)
                print(
                    "CERA_BRANCH_LIVE_FIVE_TURN="
                    + canonical_json(
                        {
                            "index": index,
                            "status": record["status"],
                            "duration_seconds": record.get("duration_seconds"),
                            "cached_input_tokens": record.get("cached_input_tokens"),
                            "thread_fork_count": runner.thread_fork_count,
                        }
                    ),
                    flush=True,
                )

        summary["finished_at"] = utc_now()
        summary["passed"] = sum(
            value["status"] == "passed" for value in summary["turns"]
        )
        summary["failed"] = len(summary["turns"]) - summary["passed"]
        summary["status"] = (
            "passed" if summary["passed"] == len(MESSAGES) else "completed_with_failures"
        )
        summary["database_validation"] = {
            "artifact_count": world.store.table_count("artifacts"),
            "integrity_check": list(world.store.integrity_check()),
            "foreign_key_findings": len(world.store.foreign_key_check()),
        }
        for field in (
            "duration_seconds",
            "wall_seconds",
            "input_tokens",
            "cached_input_tokens",
            "uncached_input_tokens",
            "output_tokens",
            "reasoning_output_tokens",
        ):
            summary["total_" + field] = round(
                sum(value.get(field, 0) for value in summary["turns"]), 3
            )
    write_json(output / "summary.json", summary)
    print(
        "CERA_BRANCH_LIVE_FIVE_RESULT="
        + canonical_json(
            {
                "status": summary["status"],
                "passed": summary["passed"],
                "failed": summary["failed"],
                "provider_calls": summary["provider_calls"],
                "total_wall_seconds": summary["total_wall_seconds"],
            }
        ),
        flush=True,
    )
    return 0 if summary["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
