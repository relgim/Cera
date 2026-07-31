"""Compare fresh-per-turn and continued-thread CERA Reasoner behavior.

This is a bounded diagnostic, not an active-route implementation.  It prepares
three real V1.2 Long Reasoner packets in disposable worlds and calls Sol-medium
once for each packet.  Run A creates a fresh ephemeral thread per turn inside
one warm Codex process.  Run B creates one fresh ephemeral thread and continues
it for all three turns.

The continued thread may retain prior provisional Reasoner drafts as
non-authoritative reference context.  The newest CERA packet remains the only
authority and Python still validates every draft.  No Composer, verifier,
publication, retry, fallback, or story-state commit is involved.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, replace
from datetime import datetime, timezone
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
from cera.reasoner.codex import (
    build_codex_reasoner_packet,
    build_codex_reasoner_prompt,
)
from cera.runtime import HanezawaHumanTestWorld
from cera.serialization import canonical_json, text_sha256, to_primitive
from cera.sillytavern import (
    CERA_VIRTUAL_MODEL,
    CeraSillyTavernAdapter,
    ChatMessage,
    SillyTavernChatRequest,
    SillyTavernTurnReply,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = (
    ROOT
    / "evaluation"
    / "evidence"
    / "codex_branch_session_ab_2026-07-30_v1"
)
BENCHMARK_ID = "codex-branch-session-ab-2026-07-30-v1"
MESSAGES = (
    "Hello, my name is Ted. Is this the hanezawa household?",
    "Respond accordingly to Sakuras Response.",
    "Respond accordingly to Sakuras Response.",
)
PROVISIONAL_HISTORY_INSTRUCTION = (
    "TEST-ONLY CONTINUED-THREAD POLICY: Earlier CERA prompts and Reasoner drafts "
    "in this thread are non-authoritative provisional context. You may use them "
    "only to resolve a direct reference such as 'Sakura's Response'. The newest "
    "CERA packet is the complete current authority and supersedes any conflict. "
    "Never cite a prior draft as evidence, convert it into canon, or carry an old "
    "fact that the newest packet does not authorize."
)


@dataclass(frozen=True, slots=True)
class RunPolicy:
    key: str
    reuse_thread: bool
    description: str


POLICIES = (
    RunPolicy(
        "fresh_each_turn",
        False,
        "one warm Codex process; a fresh ephemeral thread for every turn",
    ),
    RunPolicy(
        "continued_thread",
        True,
        "one warm Codex process; one new ephemeral thread continued for all turns",
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


class CapturePreparedExecutor:
    def __init__(self) -> None:
        self.prepared = None

    def execute(
        self,
        prepared,
        *,
        workspace_root: Path,
        reasoning_effort: str = "medium",
    ) -> SillyTavernTurnReply:
        del reasoning_effort
        del workspace_root
        self.prepared = prepared
        request_id = (
            prepared.application_request.reasoner_request.prepared_turn.request.request_id
        )
        return SillyTavernTurnReply(
            prose="CERA session A/B preparation capture.",
            request_id=str(request_id),
            artifact_id="session-ab-preparation-capture",
            generation=1,
            provider_calls=0,
            exact_replay=True,
        )


def prepare_message(
    world: HanezawaHumanTestWorld,
    *,
    message: str,
    session_id: str,
):
    capture = CapturePreparedExecutor()
    adapter = CeraSillyTavernAdapter(world, capture)
    adapter.complete(
        SillyTavernChatRequest(
            model=CERA_VIRTUAL_MODEL,
            messages=(ChatMessage(role="user", content=message),),
            stream=False,
            cera_session_id=session_id,
            cera_scene_depth="long",
            cera_character_autonomy="both",
            cera_prompt_handling="adjustment",
        )
    )
    if capture.prepared is None:
        raise RuntimeError("CERA did not produce the requested prepared turn")
    return capture.prepared


def codex_config() -> dict[str, Any]:
    return {
        "web_search": "disabled",
        "features": {
            "apps": False,
            "apply_patch_freeform": False,
            "collab": False,
            "connectors": False,
            "multi_agent": False,
            "multi_agent_v2": False,
            "plugins": False,
            "search_tool": False,
            "shell_tool": False,
            "skill_search": False,
            "standalone_web_search": False,
            "tool_search": False,
            "unified_exec": False,
            "web_search": False,
        },
        "default_permissions": "cera-no-files",
        "permissions": {
            "cera-no-files": {
                "description": "CERA session A/B without filesystem or network",
                "filesystem": {
                    ":root": "deny",
                    ":minimal": "read",
                    ":tmpdir": "deny",
                    ":slash_tmp": "deny",
                },
                "network": {"enabled": False},
            }
        },
    }


class InProcessSessionRunner:
    """One app-server process with selectable fresh or continued thread policy."""

    def __init__(
        self,
        *,
        reuse_thread: bool,
        workspace: Path,
        service_tier: str | None = None,
    ) -> None:
        if service_tier is not None and not service_tier.strip():
            raise ValueError("Codex service tier must be non-empty when supplied")
        self.reuse_thread = reuse_thread
        self.workspace = workspace
        self.service_tier = service_tier
        self._codex_context = None
        self.codex = None
        self.thread = None
        self.compatibility_state = None
        self.thread_start_count = 0
        self.thread_invalidation_count = 0
        self.turn_count = 0

    def invalidate_thread(self) -> None:
        """Prevent a failed pipeline stage from contaminating the next turn."""

        if self.thread is not None:
            self.thread = None
            self.thread_invalidation_count += 1

    def __enter__(self) -> "InProcessSessionRunner":
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

    def _start_thread(self, route):
        from openai_codex.api import ApprovalMode

        assert self.codex is not None
        base = (
            _BASE_INSTRUCTIONS_BY_ROLE["scene_reasoner"]
            + "\n\n"
            + PROVISIONAL_HISTORY_INSTRUCTION
        )
        self.thread_start_count += 1
        return self.codex.thread_start(
            model=route.model_name,
            cwd=str(self.workspace),
            ephemeral=True,
            base_instructions=base,
            config=codex_config(),
            service_name="cera_reasoner_session_ab_diagnostic",
            approval_mode=ApprovalMode.deny_all,
        )

    def run(
        self,
        *,
        route,
        prompt: str,
        output_schema: dict[str, Any],
        workspace: Path,
        mcp_binding,
    ) -> CodexWorkerResult:
        from openai_codex.api import ReasoningEffort

        if mcp_binding is not None:
            raise ValueError("session A/B diagnostic is deliberately no-MCP")
        if workspace != self.workspace:
            raise ValueError("session A/B workspace binding changed")
        if self.thread is None or not self.reuse_thread:
            self.thread = self._start_thread(route)
        self.turn_count += 1
        started = time.perf_counter()
        result = self.thread.run(
            prompt,
            effort=ReasoningEffort(route.reasoning_effort),
            output_schema=output_schema,
            service_tier=self.service_tier,
        )
        wall_ms = int((time.perf_counter() - started) * 1000)
        thread_state = self.thread.read()
        if thread_state.thread.model_provider != "openai":
            raise RuntimeError("Codex thread used an unexpected provider")
        if thread_state.thread.cli_version != route.transport_version:
            raise RuntimeError("Codex thread used an unexpected runtime version")
        if result.final_response is None or result.usage is None:
            raise RuntimeError("Codex thread omitted its response or usage")
        usage = result.usage.last
        if any(
            getattr((item.root if hasattr(item, "root") else item), "type", None)
            == "mcpToolCall"
            for item in result.items
        ):
            raise RuntimeError("session A/B Codex thread unexpectedly used MCP")
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
            transport_compatibility_id=(
                self.compatibility_state.compatibility_id
            ),
            transport_compatibility_source_sha256=(
                self.compatibility_state.source_sha256
            ),
            transport_compatibility_activated=True,
            buffered_early_completion_count=(
                self.compatibility_state.buffered_early_completion_count
            ),
            pre_registered_turn_count=max(
                1,
                self.compatibility_state.pre_registered_turn_count,
            ),
        )


class NamespacedTransport:
    def __init__(self, transport: CodexSDKTransport, namespace: str) -> None:
        self.transport = transport
        self.route = transport.route
        self.namespace = namespace

    def invoke(self, prompt: str, **kwargs):
        return self.transport.invoke(
            f"CERA non-semantic session A/B namespace: {self.namespace}\n{prompt}",
            **kwargs,
        )


def outcome_summary(call) -> dict[str, Any]:
    outcome = call.outcome
    decision = outcome.decision
    plan = outcome.behavioral_scene_plan
    return {
        "status": outcome.status.value,
        "floor_owner_id": (
            str(decision.floor_owner_id)
            if decision is not None and decision.floor_owner_id is not None
            else None
        ),
        "responding_npc_ids": (
            [str(value) for value in decision.responding_npc_ids]
            if decision is not None
            else []
        ),
        "scene_intent": decision.scene_intent if decision is not None else None,
        "event_blocks": (
            [
                {
                    "purpose": value.purpose,
                    "causal_basis": value.causal_basis,
                    "event_advances": list(value.event_advances),
                    "resulting_state": value.resulting_state,
                }
                for value in plan.event_blocks
            ]
            if plan is not None
            else []
        ),
        "runway": to_primitive(plan.runway) if plan is not None else None,
        "future_segments": (
            [to_primitive(value) for value in decision.future_segments]
            if decision is not None
            else []
        ),
        "insufficiencies": list(outcome.insufficiencies),
        "hard_citation_count": len(outcome.hard_citations),
    }


def run_policy(
    policy: RunPolicy,
    output_dir: Path,
    *,
    turn_indices: tuple[int, ...],
    model: str = "gpt-5.6-sol",
    effort: str = "medium",
    service_tier: str | None = None,
    stop_on_failure: bool = False,
    maximum_output_tokens: int | None = None,
) -> dict[str, Any]:
    started_at = utc_now()
    run_record: dict[str, Any] = {
        "schema_version": "cera.codex_branch_session_ab_run.v1",
        "policy": to_primitive(policy),
        "started_at": started_at,
        "status": "running",
        "turns": [],
        "provider_calls": 0,
        "retry_count": 0,
        "fallback_count": 0,
        "story_state_committed": False,
    }
    write_json(output_dir / f"{policy.key}.json", run_record)
    with TemporaryDirectory(prefix=f"cera-session-ab-{policy.key}-") as temporary:
        temporary_path = Path(temporary)
        world = HanezawaHumanTestWorld.initialize(
            ROOT,
            temporary_path / "world.sqlite3",
            replace=True,
        )
        workspace = temporary_path / "reasoner_workspace"
        workspace.mkdir()
        route_overrides: dict[str, Any] = {
            "route_id": f"codex_reasoner_session_ab_{policy.key}",
            "adapter_id": "cera.codex_session_ab_diagnostic.v1",
            "prompt_version": "cera.codex_scene_reasoner_prompt.v23.session_ab_test_v1",
        }
        if maximum_output_tokens is not None:
            route_overrides["maximum_output_tokens"] = maximum_output_tokens
        route = replace(
            codex_reasoner_candidate(model=model, effort=effort),
            **route_overrides,
        )
        with InProcessSessionRunner(
            reuse_thread=policy.reuse_thread,
            workspace=workspace,
            service_tier=service_tier,
        ) as runner:
            transport = NamespacedTransport(
                CodexSDKTransport(route, workspace=workspace, runner=runner),
                f"{BENCHMARK_ID}:{policy.key}",
            )
            port = CodexSceneReasonerPort(
                transport,
                evidence_tools_enabled=False,
            )
            for index in turn_indices:
                message = MESSAGES[index - 1]
                turn_started = time.perf_counter()
                record: dict[str, Any] = {
                    "index": index,
                    "message": message,
                    "message_sha256": text_sha256(message),
                    "status": "started",
                }
                try:
                    prepared = prepare_message(
                        world,
                        message=message,
                        session_id=f"session-ab-{policy.key}",
                    )
                    request = prepared.application_request.reasoner_request
                    packet = build_codex_reasoner_packet(
                        request,
                        evidence_tools_available=False,
                    )
                    prompt = build_codex_reasoner_prompt(packet)
                    record["dispatch_started"] = True
                    run_record["provider_calls"] += 1
                    call = port.reason(request, world.service)
                    receipt = call.provider_call_receipt
                    record.update(
                        {
                            "status": "passed",
                            "request_sha256": request.request_sha256,
                            "packet_sha256": text_sha256(canonical_json(packet)),
                            "packet_bytes": len(
                                canonical_json(packet).encode("utf-8")
                            ),
                            "prompt_sha256": text_sha256(prompt),
                            "prompt_bytes": len(prompt.encode("utf-8")),
                            "provider_receipt": to_primitive(receipt),
                            "duration_seconds": round(
                                receipt.duration_ms / 1000,
                                3,
                            ),
                            "input_tokens": receipt.input_tokens,
                            "cached_input_tokens": receipt.cached_input_tokens,
                            "uncached_input_tokens": (
                                receipt.input_tokens - receipt.cached_input_tokens
                            ),
                            "output_tokens": receipt.output_tokens,
                            "reasoning_output_tokens": (
                                receipt.reasoning_output_tokens
                            ),
                            "outcome": outcome_summary(call),
                        }
                    )
                except Exception as exc:
                    record.update(
                        {
                            "status": "failed",
                            "error_type": type(exc).__name__,
                            "error_message": str(exc) or type(exc).__name__,
                        }
                    )
                    if getattr(exc, "provider_call_receipt", None) is not None:
                        record["provider_receipt"] = to_primitive(
                            exc.provider_call_receipt
                        )
                record["wall_seconds"] = round(
                    time.perf_counter() - turn_started,
                    3,
                )
                run_record["turns"].append(record)
                run_record["thread_start_count"] = runner.thread_start_count
                write_json(output_dir / f"{policy.key}.json", run_record)
                print(
                    "CERA_SESSION_AB_TURN="
                    + canonical_json(
                        {
                            "policy": policy.key,
                            "index": index,
                            "status": record["status"],
                            "duration_seconds": record.get("duration_seconds"),
                            "cached_input_tokens": record.get(
                                "cached_input_tokens"
                            ),
                            "thread_start_count": runner.thread_start_count,
                        }
                    ),
                    flush=True,
                )
                if record["status"] == "failed" and stop_on_failure:
                    runner.invalidate_thread()
                    break
        run_record["thread_start_count"] = runner.thread_start_count
        run_record["status"] = (
            "passed"
            if len(run_record["turns"]) == len(turn_indices)
            and all(value["status"] == "passed" for value in run_record["turns"])
            else "failed"
        )
        run_record["finished_at"] = utc_now()
        run_record["database_validation"] = {
            "artifact_count": world.store.table_count("artifacts"),
            "integrity_check": list(world.store.integrity_check()),
            "foreign_key_findings": len(world.store.foreign_key_check()),
        }
    write_json(output_dir / f"{policy.key}.json", run_record)
    return run_record


def aggregate(runs: list[dict[str, Any]]) -> dict[str, Any]:
    def total(run, field: str) -> int | float:
        return sum(value.get(field, 0) for value in run["turns"])

    return {
        value["policy"]["key"]: {
            "status": value["status"],
            "completed_turns": len(value["turns"]),
            "provider_calls": value["provider_calls"],
            "thread_start_count": value.get("thread_start_count", 0),
            "duration_seconds": round(total(value, "duration_seconds"), 3),
            "wall_seconds": round(total(value, "wall_seconds"), 3),
            "input_tokens": total(value, "input_tokens"),
            "cached_input_tokens": total(value, "cached_input_tokens"),
            "uncached_input_tokens": total(value, "uncached_input_tokens"),
            "output_tokens": total(value, "output_tokens"),
            "reasoning_output_tokens": total(
                value,
                "reasoning_output_tokens",
            ),
        }
        for value in runs
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--policy",
        action="append",
        choices=tuple(value.key for value in POLICIES),
        help="Run only the selected policy; may be repeated.",
    )
    parser.add_argument(
        "--turn-index",
        action="append",
        type=int,
        choices=range(1, len(MESSAGES) + 1),
        help="Run only the selected turn index; may be repeated.",
    )
    parser.add_argument("--model", default="gpt-5.6-sol")
    parser.add_argument("--effort", default="medium")
    parser.add_argument("--service-tier")
    parser.add_argument("--maximum-output-tokens", type=int)
    parser.add_argument(
        "--stop-on-failure",
        action="store_true",
        help="Stop the policy after its first failed turn.",
    )
    args = parser.parse_args()
    selected_policies = tuple(
        value
        for value in POLICIES
        if args.policy is None or value.key in args.policy
    )
    selected_turn_indices = tuple(
        sorted(set(args.turn_index or range(1, len(MESSAGES) + 1)))
    )
    output_dir = args.output.resolve()
    if output_dir.exists():
        raise SystemExit(f"refusing to overwrite session A/B evidence: {output_dir}")
    output_dir.mkdir(parents=True)
    summary: dict[str, Any] = {
        "schema_version": "cera.codex_branch_session_ab_summary.v1",
        "benchmark_id": BENCHMARK_ID,
        "status": "running",
        "started_at": utc_now(),
        "model": args.model,
        "effort": args.effort,
        "service_tier": args.service_tier,
        "maximum_output_tokens": args.maximum_output_tokens,
        "depth": "long",
        "messages": list(MESSAGES),
        "policies": to_primitive(selected_policies),
        "selected_turn_indices": list(selected_turn_indices),
        "test_only_provisional_history_instruction": (
            PROVISIONAL_HISTORY_INSTRUCTION
        ),
        "provider_stages": [
            {
                "policy": policy.key,
                "turn": index,
                "provider": "openai_codex",
                "model": args.model,
                "effort": args.effort,
                "service_tier": args.service_tier,
                "maximum_output_tokens": args.maximum_output_tokens,
                "dispatch_started": False,
            }
            for policy in selected_policies
            for index in selected_turn_indices
        ],
        "retry_count": 0,
        "fallback_count": 0,
        "story_state_committed": False,
        "production_route_changed": False,
    }
    write_json(output_dir / "summary.json", summary)
    runs = []
    for policy in selected_policies:
        run = run_policy(
            policy,
            output_dir,
            turn_indices=selected_turn_indices,
            model=args.model,
            effort=args.effort,
            service_tier=args.service_tier,
            stop_on_failure=args.stop_on_failure,
            maximum_output_tokens=args.maximum_output_tokens,
        )
        runs.append(run)
        dispatched_turns = {
            value["index"]
            for value in run["turns"]
            if value.get("dispatch_started", False)
        }
        for stage in summary["provider_stages"]:
            if stage["policy"] == policy.key:
                stage["dispatch_started"] = stage["turn"] in dispatched_turns
        write_json(output_dir / "summary.json", summary)
    summary["runs"] = runs
    summary["aggregate"] = aggregate(runs)
    summary["status"] = (
        "passed"
        if len(runs) == len(selected_policies)
        and all(value["status"] == "passed" for value in runs)
        else "completed_with_failures"
    )
    summary["finished_at"] = utc_now()
    summary["total_provider_calls"] = sum(
        value["provider_calls"] for value in runs
    )
    write_json(output_dir / "summary.json", summary)
    print("CERA_SESSION_AB_RESULT=" + canonical_json(summary["aggregate"]), flush=True)
    return 0 if summary["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
