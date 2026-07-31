"""Run a three-turn provisional CERA session A/B without publication.

Both policies execute the real Sol-medium Reasoner and DeepSeek V4 Flash
Composer.  The accepted in-memory prose candidate from each turn is supplied
to the next turn as testing-only visible continuity.  The only policy
difference is whether the Sol Reasoner receives a fresh ephemeral Codex thread
for every turn or continues one newly-created ephemeral thread for all turns.

The realization verifier is a test fake, so this benchmark measures Reasoner,
Composer, prompt/context continuity, latency, and cache behavior.  It does not
qualify semantic verification or publish story state.
"""

from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import time
from typing import Any

from cera.composer import (
    ComposerContextAssembler,
    ComposerCoordinator,
    DeepSeekSceneComposerPort,
)
from cera.kernel import TurnKernel
from cera.providers import (
    CodexSDKTransport,
    DeepSeekChatTransport,
    codex_reasoner_candidate,
    deepseek_composer_candidate,
)
from cera.realization import EchoAcceptingSceneRealizationVerifierPort
from cera.reasoner import CodexSceneReasonerPort, ReasonerCoordinator
from cera.runtime import HanezawaHumanTestWorld, LiveShapedTurnPipeline
from cera.serialization import canonical_json, text_sha256, to_primitive

from scripts.run_codex_branch_session_ab import (
    InProcessSessionRunner,
    MESSAGES,
    NamespacedTransport,
    POLICIES,
    ROOT,
    RunPolicy,
    outcome_summary,
    prepare_message,
)


DEFAULT_OUTPUT = (
    ROOT
    / "evaluation"
    / "evidence"
    / "full_branch_session_ab_2026-07-30_v1"
)
BENCHMARK_ID = "full-branch-session-ab-2026-07-30-v1"


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


def provisional_context_entry(turn_index: int, prose: str) -> str:
    """Mark visible prior prose as current context but never durable canon."""

    return (
        f"TEST-ONLY VISIBLE PROVISIONAL REPLY {turn_index}: The following prose "
        "was displayed immediately before the current user cue. It is valid "
        "conversational continuity for resolving direct references in this test, "
        "but it has not been accepted as durable canon and must not be cited as "
        f"Genesis evidence. Exact displayed prose follows:\n{prose}"
    )


def bind_provisional_history(application_request, history: tuple[str, ...]):
    """Return pipeline inputs with identical explicit history for both policies."""

    if not history:
        return (
            application_request.reasoner_request,
            application_request.composer_plan,
        )
    reasoner_request = application_request.reasoner_request
    dossier = reasoner_request.seed_dossier
    reasoner_request = replace(
        reasoner_request,
        seed_dossier=replace(
            dossier,
            scene_anchors=(*dossier.scene_anchors, *history),
        ),
    )
    composer_plan = replace(
        application_request.composer_plan,
        established_scene_context=(
            *application_request.composer_plan.established_scene_context,
            *history,
        ),
    )
    return reasoner_request, composer_plan


def provider_metrics(receipt) -> dict[str, Any]:
    return {
        "provider_receipt": to_primitive(receipt),
        "duration_seconds": round(receipt.duration_ms / 1000, 3),
        "input_tokens": receipt.input_tokens,
        "cached_input_tokens": receipt.cached_input_tokens,
        "uncached_input_tokens": (
            receipt.input_tokens - receipt.cached_input_tokens
        ),
        "output_tokens": receipt.output_tokens,
        "reasoning_output_tokens": receipt.reasoning_output_tokens,
    }


def run_policy(
    policy: RunPolicy,
    output_dir: Path,
    *,
    turn_indices: tuple[int, ...],
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "schema_version": "cera.full_branch_session_ab_run.v1",
        "policy": to_primitive(policy),
        "started_at": utc_now(),
        "status": "running",
        "turns": [],
        "sol_calls": 0,
        "deepseek_calls": 0,
        "retry_count": 0,
        "fallback_count": 0,
        "story_state_committed": False,
        "semantic_verifier_qualified": False,
    }
    write_json(output_dir / f"{policy.key}.json", record)
    provisional_history: list[str] = []
    with TemporaryDirectory(prefix=f"cera-full-session-ab-{policy.key}-") as temporary:
        temporary_path = Path(temporary)
        world = HanezawaHumanTestWorld.initialize(
            ROOT,
            temporary_path / "world.sqlite3",
            replace=True,
        )
        workspace = temporary_path / "reasoner_workspace"
        workspace.mkdir()
        route = replace(
            codex_reasoner_candidate(model="gpt-5.6-sol", effort="medium"),
            route_id=f"codex_reasoner_full_session_ab_{policy.key}",
            adapter_id="cera.codex_full_session_ab_diagnostic.v1",
            prompt_version="cera.codex_scene_reasoner_prompt.v24.full_session_ab_v2",
        )
        with InProcessSessionRunner(
            reuse_thread=policy.reuse_thread,
            workspace=workspace,
        ) as runner:
            reasoner_port = CodexSceneReasonerPort(
                NamespacedTransport(
                    CodexSDKTransport(route, workspace=workspace, runner=runner),
                    f"{BENCHMARK_ID}:{policy.key}",
                ),
                evidence_tools_enabled=False,
            )
            composer_port = DeepSeekSceneComposerPort(
                DeepSeekChatTransport(
                    deepseek_composer_candidate(model="deepseek-v4-flash")
                )
            )
            pipeline = LiveShapedTurnPipeline(
                ReasonerCoordinator(world.service, TurnKernel(world.service)),
                ComposerContextAssembler(world.service),
                ComposerCoordinator(),
                realization_verifier_port=(
                    EchoAcceptingSceneRealizationVerifierPort()
                ),
            )
            for index in turn_indices:
                message = MESSAGES[index - 1]
                turn_started = time.perf_counter()
                turn: dict[str, Any] = {
                    "index": index,
                    "message": message,
                    "message_sha256": text_sha256(message),
                    "history_entries_supplied": len(provisional_history),
                    "history_sha256": (
                        text_sha256(canonical_json(provisional_history))
                        if provisional_history
                        else None
                    ),
                    "status": "started",
                }
                sol_before = runner.turn_count
                try:
                    prepared = prepare_message(
                        world,
                        message=message,
                        session_id=f"full-session-ab-{policy.key}",
                    )
                    reasoner_request, composer_plan = bind_provisional_history(
                        prepared.application_request,
                        tuple(provisional_history),
                    )
                    result = pipeline.execute(
                        reasoner_request,
                        reasoner_port,
                        composer_plan,
                        composer_port,
                    )
                    reasoner_receipt = result.reasoner.provider_call_receipt
                    composer_receipt = result.composer.provider_call_receipt
                    record["sol_calls"] += 1
                    record["deepseek_calls"] += 1
                    prose = result.accepted_artifact.accepted_prose
                    provisional_history.append(
                        provisional_context_entry(index, prose)
                    )
                    turn.update(
                        {
                            "status": "passed",
                            "reasoner": {
                                **provider_metrics(reasoner_receipt),
                                "outcome": outcome_summary(result.reasoner),
                            },
                            "composer": {
                                **provider_metrics(composer_receipt),
                                "accepted_prose": prose,
                                "accepted_prose_sha256": (
                                    result.accepted_artifact.prose_sha256
                                ),
                                "accepted_prose_characters": len(prose),
                                "accepted_prose_words": len(prose.split()),
                            },
                            "fake_verifier_invocations": (
                                pipeline.realization_verifier_port.invocation_count
                            ),
                        }
                    )
                except Exception as exc:
                    sol_after = runner.turn_count
                    if sol_after > sol_before:
                        record["sol_calls"] += sol_after - sol_before
                    provider_receipt = getattr(exc, "provider_call_receipt", None)
                    if provider_receipt is not None:
                        # A Composer-stage receipt proves its provider dispatch.
                        requested_model = getattr(
                            provider_receipt,
                            "requested_model",
                            "",
                        )
                        if str(requested_model).startswith("deepseek"):
                            record["deepseek_calls"] += 1
                    turn.update(
                        {
                            "status": "failed",
                            "error_type": type(exc).__name__,
                            "error_message": str(exc) or type(exc).__name__,
                            "retained_evidence_handles": to_primitive(
                                getattr(exc, "retained_evidence_handles", ())
                            ),
                            "privacy_safe_receipt_payloads": to_primitive(
                                getattr(exc, "privacy_safe_receipt_payloads", ())
                            ),
                        }
                    )
                    if policy.reuse_thread:
                        runner.invalidate_thread()
                turn["wall_seconds"] = round(
                    time.perf_counter() - turn_started,
                    3,
                )
                turn["thread_start_count"] = runner.thread_start_count
                turn["thread_invalidation_count"] = (
                    runner.thread_invalidation_count
                )
                record["turns"].append(turn)
                record["thread_start_count"] = runner.thread_start_count
                write_json(output_dir / f"{policy.key}.json", record)
                print(
                    "CERA_FULL_SESSION_AB_TURN="
                    + canonical_json(
                        {
                            "policy": policy.key,
                            "index": index,
                            "status": turn["status"],
                            "wall_seconds": turn["wall_seconds"],
                            "thread_start_count": runner.thread_start_count,
                            "provisional_history_entries": len(
                                provisional_history
                            ),
                        }
                    ),
                    flush=True,
                )
        record["status"] = (
            "passed"
            if len(record["turns"]) == len(turn_indices)
            and all(value["status"] == "passed" for value in record["turns"])
            else "completed_with_failures"
        )
        record["finished_at"] = utc_now()
        record["database_validation"] = {
            "artifact_count": world.store.table_count("artifacts"),
            "integrity_check": list(world.store.integrity_check()),
            "foreign_key_findings": len(world.store.foreign_key_check()),
        }
    write_json(output_dir / f"{policy.key}.json", record)
    return record


def aggregate(runs: list[dict[str, Any]]) -> dict[str, Any]:
    aggregated: dict[str, Any] = {}
    for run in runs:
        reasoner = [
            value["reasoner"]
            for value in run["turns"]
            if value["status"] == "passed"
        ]
        composer = [
            value["composer"]
            for value in run["turns"]
            if value["status"] == "passed"
        ]
        aggregated[run["policy"]["key"]] = {
            "status": run["status"],
            "completed_turns": len(run["turns"]),
            "passed_turns": len(reasoner),
            "thread_start_count": run.get("thread_start_count", 0),
            "sol_calls": run["sol_calls"],
            "deepseek_calls": run["deepseek_calls"],
            "wall_seconds": round(
                sum(value["wall_seconds"] for value in run["turns"]),
                3,
            ),
            "reasoner_duration_seconds": round(
                sum(value["duration_seconds"] for value in reasoner),
                3,
            ),
            "reasoner_input_tokens": sum(
                value["input_tokens"] for value in reasoner
            ),
            "reasoner_cached_input_tokens": sum(
                value["cached_input_tokens"] for value in reasoner
            ),
            "composer_duration_seconds": round(
                sum(value["duration_seconds"] for value in composer),
                3,
            ),
            "composer_words": sum(
                value["accepted_prose_words"] for value in composer
            ),
        }
    return aggregated


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--confirm-live", action="store_true")
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
    args = parser.parse_args()
    if not args.confirm_live:
        parser.error("live provider calls require --confirm-live")
    output_dir = args.output.resolve()
    if output_dir.exists():
        raise SystemExit(f"refusing to overwrite full session A/B evidence: {output_dir}")
    output_dir.mkdir(parents=True)
    selected_policies = tuple(
        value
        for value in POLICIES
        if args.policy is None or value.key in args.policy
    )
    selected_turn_indices = tuple(
        sorted(set(args.turn_index or range(1, len(MESSAGES) + 1)))
    )
    summary: dict[str, Any] = {
        "schema_version": "cera.full_branch_session_ab_summary.v1",
        "benchmark_id": BENCHMARK_ID,
        "status": "running",
        "started_at": utc_now(),
        "reasoner": {"model": "gpt-5.6-sol", "effort": "medium"},
        "composer": {"model": "deepseek-v4-flash", "thinking": False},
        "realization_verifier": "scripted_echo_fake",
        "depth": "long",
        "messages": list(MESSAGES),
        "selected_turn_indices": list(selected_turn_indices),
        "policies": to_primitive(selected_policies),
        "retry_count": 0,
        "fallback_count": 0,
        "story_state_committed": False,
        "production_route_changed": False,
        "qualification_limit": (
            "The fake verifier proves wiring only; it does not qualify semantic "
            "realization or production publication."
        ),
    }
    write_json(output_dir / "summary.json", summary)
    runs = []
    for policy in selected_policies:
        runs.append(
            run_policy(
                policy,
                output_dir,
                turn_indices=selected_turn_indices,
            )
        )
    summary["runs"] = runs
    summary["aggregate"] = aggregate(runs)
    summary["status"] = (
        "passed"
        if all(value["status"] == "passed" for value in runs)
        else "completed_with_failures"
    )
    summary["finished_at"] = utc_now()
    summary["total_sol_calls"] = sum(value["sol_calls"] for value in runs)
    summary["total_deepseek_calls"] = sum(
        value["deepseek_calls"] for value in runs
    )
    write_json(output_dir / "summary.json", summary)
    print(
        "CERA_FULL_SESSION_AB_RESULT=" + canonical_json(summary["aggregate"]),
        flush=True,
    )
    return 0 if summary["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
