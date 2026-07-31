"""Run a non-committing live CERA model/effort comparison.

The benchmark prepares one real V1.2 SillyTavern-shaped Auto turn in a
disposable database, then submits the exact same prepared turn twice for each
candidate route.  The first and second calls are independent samples; the
second is also the controlled prompt-cache observation because its Codex
prompt and output schema are byte-identical to the first.

Every sample uses the current DeepSeek V4 Flash thinking Composer and the
current independent Sol-medium verifier.  There is no retry, fallback,
publication, canonical story write, or production binding.  A failed stage is
retained as evidence and the matrix proceeds to the next independently planned
stage.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
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
from cera.evidence import EvidenceService
from cera.kernel import TurnKernel
from cera.providers import (
    CodexExecRunner,
    CodexSDKTransport,
    CodexStructuredOutputTransport,
    DeepSeekChatTransport,
    codex_cli_realization_verifier_candidate,
    codex_reasoner_candidate,
    deepseek_composer_candidate,
)
from cera.reasoner import CodexSceneReasonerPort, ReasonerCoordinator
from cera.realization import (
    CodexSceneRealizationVerifierPort,
    InMemoryRejectedCandidateReviewStore,
    SceneRealizationVerificationCoordinator,
)
from cera.runtime import HanezawaHumanTestWorld, LiveShapedTurnPipeline
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
    / "codex_reasoner_model_ladder_2026-07-30_v3"
)
BENCHMARK_ID = "codex-reasoner-model-ladder-2026-07-30-v3"
SOURCE_TEXT = "Hello, Hanezawa residence?"


@dataclass(frozen=True, slots=True)
class Candidate:
    key: str
    model: str
    effort: str


# Model family and effort do not form a formally ordered scalar.  This matrix
# includes every practical intermediate combination between the creator's
# Luna/high and Sol/medium endpoints without adding Max.
CANDIDATES = (
    Candidate("luna_high", "gpt-5.6-luna", "high"),
    Candidate("luna_xhigh", "gpt-5.6-luna", "xhigh"),
    Candidate("terra_low", "gpt-5.6-terra", "low"),
    Candidate("terra_medium", "gpt-5.6-terra", "medium"),
    Candidate("terra_high", "gpt-5.6-terra", "high"),
    Candidate("terra_xhigh", "gpt-5.6-terra", "xhigh"),
    Candidate("sol_low", "gpt-5.6-sol", "low"),
    Candidate("sol_medium", "gpt-5.6-sol", "medium"),
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, payload: Any) -> None:
    """Checkpoint JSON atomically so a process/API failure remains auditable."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def planned_provider_stages() -> list[dict[str, Any]]:
    stages = []
    for candidate in CANDIDATES:
        for sample in (1, 2):
            prefix = f"{candidate.key}_sample_{sample}"
            stages.extend(
                (
                    {
                        "stage": f"{prefix}_reasoner",
                        # The existing live-call budget calls all ChatGPT-authenticated
                        # Codex dispatches "sol".  Preserve that ledger category
                        # even when the concrete comparison model is Terra/Luna.
                        "provider_category": "sol",
                        "provider": "openai_codex",
                        "model": candidate.model,
                        "effort": candidate.effort,
                        "dispatch_started": False,
                        "status": "planned",
                    },
                    {
                        "stage": f"{prefix}_composer",
                        "provider_category": "deepseek",
                        "provider": "deepseek",
                        "model": "deepseek-v4-flash",
                        "effort": None,
                        "dispatch_started": False,
                        "status": "planned",
                    },
                    {
                        "stage": f"{prefix}_verifier",
                        "provider_category": "sol",
                        "provider": "openai_codex",
                        "model": "gpt-5.6-sol",
                        "effort": "medium",
                        "dispatch_started": False,
                        "status": "planned",
                    },
                )
            )
    return stages


def mark_stage(
    summary: dict[str, Any],
    output_dir: Path,
    stage_name: str,
    *,
    dispatch_started: bool | None = None,
    status: str | None = None,
) -> None:
    stage = next(
        value for value in summary["provider_stages"] if value["stage"] == stage_name
    )
    if dispatch_started is not None:
        stage["dispatch_started"] = dispatch_started
        if dispatch_started and stage["status"] == "planned":
            stage["status"] = "started"
    if status is not None:
        stage["status"] = status
    summary["updated_at"] = utc_now()
    write_json(output_dir / "summary.json", summary)


class CapturePreparedExecutor:
    """Capture current SillyTavern preparation without invoking a provider."""

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
            prose="CERA benchmark preparation capture.",
            request_id=str(request_id),
            artifact_id="benchmark-preparation-capture",
            generation=1,
            provider_calls=0,
            exact_replay=True,
        )


class CacheNamespacedCodexTransport:
    """Give each route one new CERA prompt prefix, reused only by sample 2.

    Codex itself may still cache its platform-owned instruction baseline.  This
    wrapper makes the CERA-owned user prompt cold for sample 1 without changing
    its story semantics, then presents byte-identical input for sample 2.
    """

    def __init__(self, transport: CodexSDKTransport, namespace: str) -> None:
        if not namespace.strip():
            raise ValueError("cache namespace must be non-empty")
        self.transport = transport
        self.route = transport.route
        self.namespace = namespace

    def invoke(self, prompt: str, **kwargs):
        return self.transport.invoke(
            f"CERA non-semantic cache namespace: {self.namespace}\n{prompt}",
            **kwargs,
        )


def prepare_current_turn(world: HanezawaHumanTestWorld):
    capture = CapturePreparedExecutor()
    adapter = CeraSillyTavernAdapter(world, capture)
    request = SillyTavernChatRequest(
        model=CERA_VIRTUAL_MODEL,
        messages=(ChatMessage(role="user", content=SOURCE_TEXT),),
        stream=False,
        cera_session_id="model-ladder-v1",
        cera_scene_depth="auto",
        cera_character_autonomy="both",
        cera_prompt_handling="adjustment",
    )
    adapter.complete(request)
    if capture.prepared is None:
        raise RuntimeError("current SillyTavern preparation was not captured")
    return capture.prepared


def safe_error(exc: Exception, *, stage: str) -> dict[str, Any]:
    envelope = getattr(exc, "envelope", None)
    return {
        "stage": stage,
        "type": type(exc).__name__,
        "message": str(exc) or type(exc).__name__,
        "error_code": (
            getattr(getattr(envelope, "code", None), "value", None)
            if envelope is not None
            else getattr(getattr(exc, "code", None), "value", None)
        ),
        "safe_diagnostics": list(
            getattr(exc, "safe_diagnostics", ())
            or getattr(envelope, "details", ())
            or ()
        ),
        "external_provider_calls_observed": getattr(
            exc, "external_provider_calls_observed", None
        ),
        "privacy_safe_receipt_payloads": to_primitive(
            getattr(exc, "privacy_safe_receipt_payloads", ())
        ),
        "realization_verification_receipt": to_primitive(
            getattr(exc, "realization_verification_receipt", None)
        ),
    }


def provider_metrics(receipt) -> dict[str, Any] | None:
    if receipt is None:
        return None
    return {
        "provider_receipt": to_primitive(receipt),
        "duration_seconds": round(receipt.duration_ms / 1000, 3),
        "input_tokens": receipt.input_tokens,
        "cached_input_tokens": receipt.cached_input_tokens,
        "cache_ratio": (
            round(receipt.cached_input_tokens / receipt.input_tokens, 4)
            if receipt.input_tokens
            else 0.0
        ),
        "uncached_input_tokens": receipt.input_tokens - receipt.cached_input_tokens,
        "output_tokens": receipt.output_tokens,
        "reasoning_output_tokens": receipt.reasoning_output_tokens,
        "estimated_cost_microusd": receipt.cost_microusd,
        "quota_metered": receipt.quota_metered,
    }


def reasoner_metrics(reasoned) -> dict[str, Any]:
    outcome = reasoned.outcome
    decision = outcome.decision
    plan = outcome.behavioral_scene_plan
    return {
        "status": outcome.status.value,
        "responding_npc_ids": (
            [str(value) for value in decision.responding_npc_ids]
            if decision is not None
            else []
        ),
        "floor_owner_id": (
            str(decision.floor_owner_id)
            if decision is not None and decision.floor_owner_id is not None
            else None
        ),
        "sequence_beat_count": (
            len(decision.current_segment.ordered_beats) if decision is not None else 0
        ),
        "future_segment_count": (
            len(decision.future_segments) if decision is not None else 0
        ),
        "event_block_count": len(plan.event_blocks) if plan is not None else 0,
        "event_advance_count": (
            sum(len(value.event_advances) for value in plan.event_blocks)
            if plan is not None
            else 0
        ),
        "runway_class": plan.runway.runway_class.value if plan is not None else None,
        "continue_beyond_prompt_endpoint": (
            plan.runway.continue_beyond_prompt_endpoint if plan is not None else None
        ),
        "hard_citation_count": len(outcome.hard_citations),
        "tool_call_count": reasoned.receipt.tool_call_count,
        "advisory_state_delta_count": len(outcome.advisory_state_deltas),
    }


def story_metrics(story_text: str) -> dict[str, Any]:
    return {
        "characters": len(story_text),
        "words": len(story_text.split()),
        "paragraphs": len(
            [value for value in story_text.replace("\r\n", "\n").split("\n\n") if value.strip()]
        ),
        "dialogue_quote_characters": story_text.count('"'),
        "story_sha256": text_sha256(story_text),
    }


def run_sample(
    *,
    candidate: Candidate,
    sample: int,
    prepared,
    world: HanezawaHumanTestWorld,
    output_dir: Path,
    summary: dict[str, Any],
    verifier_runner: CodexExecRunner,
) -> dict[str, Any]:
    prefix = f"{candidate.key}_sample_{sample}"
    reasoner_stage = f"{prefix}_reasoner"
    composer_stage = f"{prefix}_composer"
    verifier_stage = f"{prefix}_verifier"
    started = utc_now()
    record: dict[str, Any] = {
        "schema_version": "cera.codex_reasoner_model_ladder_sample.v1",
        "benchmark_id": BENCHMARK_ID,
        "candidate": to_primitive(candidate),
        "sample": sample,
        "cache_position": "cold_observation" if sample == 1 else "warm_observation",
        "started_at": started,
        "status": "started",
        "source_text": SOURCE_TEXT,
        "source_sha256": text_sha256(SOURCE_TEXT),
        "story_state_committed": False,
        "story_authority_writes": 0,
    }

    request = prepared.application_request.reasoner_request
    plan = prepared.application_request.composer_plan
    coordinator = ReasonerCoordinator(world.service, TurnKernel(world.service))

    with TemporaryDirectory(prefix=f"cera-ladder-{prefix}-") as temporary:
        workspace = Path(temporary)
        (workspace / "reasoner").mkdir()
        (workspace / "verifier").mkdir()

        mark_stage(
            summary,
            output_dir,
            reasoner_stage,
            dispatch_started=True,
        )
        reasoner_started = time.perf_counter()
        try:
            reasoner_port = CodexSceneReasonerPort(
                CacheNamespacedCodexTransport(
                    CodexSDKTransport(
                        codex_reasoner_candidate(
                            model=candidate.model,
                            effort=candidate.effort,
                        ),
                        workspace=workspace / "reasoner",
                    ),
                    f"{BENCHMARK_ID}:{candidate.key}",
                ),
                evidence_tools_enabled=True,
            )
            reasoned = coordinator.execute(request, reasoner_port)
            record["reasoner_wall_seconds"] = round(
                time.perf_counter() - reasoner_started, 3
            )
            record["reasoner"] = {
                "metrics": reasoner_metrics(reasoned),
                "outcome": to_primitive(reasoned.outcome),
                "reasoner_receipt": to_primitive(reasoned.receipt),
                "mcp_bridge_receipt": to_primitive(reasoned.mcp_bridge_receipt),
                **(
                    provider_metrics(reasoned.provider_call_receipt)
                    or {}
                ),
            }
            mark_stage(summary, output_dir, reasoner_stage, status="completed")
        except Exception as exc:
            record["reasoner_wall_seconds"] = round(
                time.perf_counter() - reasoner_started, 3
            )
            record["status"] = "failed"
            record["failure"] = safe_error(exc, stage="scene_reasoner")
            record["finished_at"] = utc_now()
            mark_stage(summary, output_dir, reasoner_stage, status="failed")
            write_json(output_dir / "samples" / f"{prefix}.json", record)
            return record

        rejected_store = InMemoryRejectedCandidateReviewStore()
        verifier = CodexSceneRealizationVerifierPort(
            CodexStructuredOutputTransport(
                codex_cli_realization_verifier_candidate(
                    model="gpt-5.6-sol",
                    effort="medium",
                ),
                workspace=workspace / "verifier",
                runner=verifier_runner,
            )
        )
        pipeline = LiveShapedTurnPipeline(
            coordinator,
            ComposerContextAssembler(world.service),
            ComposerCoordinator(),
            realization_verification_coordinator=(
                SceneRealizationVerificationCoordinator(rejected_store)
            ),
            realization_verifier_port=verifier,
        )
        composer = DeepSeekSceneComposerPort(
            DeepSeekChatTransport(
                deepseek_composer_candidate(model="deepseek-v4-flash")
            ),
            thinking_enabled=True,
        )
        captured: list[Any] = []
        pipeline_started = time.perf_counter()

        def capture_provisional(value) -> None:
            captured.append(value)
            record["composer_ready_wall_seconds"] = round(
                time.perf_counter() - pipeline_started, 3
            )
            mark_stage(summary, output_dir, composer_stage, status="completed")
            mark_stage(
                summary,
                output_dir,
                verifier_stage,
                dispatch_started=True,
            )

        mark_stage(
            summary,
            output_dir,
            composer_stage,
            dispatch_started=True,
        )
        try:
            result = pipeline.execute(
                request,
                None,
                plan,
                composer,
                precomputed_reasoner_result=reasoned,
                provisional_candidate_callback=capture_provisional,
            )
            record["pipeline_wall_seconds"] = round(
                time.perf_counter() - pipeline_started, 3
            )
            record["status"] = "passed"
            record["verification"] = {
                "accepted": result.realization_verification.accepted,
                "receipt": to_primitive(result.realization_verification.receipt),
                **(
                    provider_metrics(
                        result.realization_verification.provider_call_receipt
                    )
                    or {}
                ),
            }
            mark_stage(summary, output_dir, verifier_stage, status="completed")
        except Exception as exc:
            record["pipeline_wall_seconds"] = round(
                time.perf_counter() - pipeline_started, 3
            )
            record["status"] = "failed"
            failure_stage = (
                "scene_realization_verification"
                if captured
                else "scene_composer_or_context"
            )
            record["failure"] = safe_error(exc, stage=failure_stage)
            if captured:
                mark_stage(summary, output_dir, verifier_stage, status="failed")
            else:
                mark_stage(summary, output_dir, composer_stage, status="failed")

        if captured:
            provisional = captured[0]
            story_text = provisional.composer.candidate.story_text
            composer_receipt = provisional.composer.provider_call_receipt
            record["composer"] = {
                "story_text": story_text,
                "story_metrics": story_metrics(story_text),
                "manifest": to_primitive(provisional.composer.manifest),
                "validation_receipt": to_primitive(
                    provisional.composer.validation_receipt
                ),
                **(provider_metrics(composer_receipt) or {}),
            }

        record["total_wall_seconds"] = round(
            record["reasoner_wall_seconds"] + record.get("pipeline_wall_seconds", 0),
            3,
        )
        record["finished_at"] = utc_now()
        write_json(output_dir / "samples" / f"{prefix}.json", record)
        return record


def sample_summary(record: dict[str, Any]) -> dict[str, Any]:
    reasoner = record.get("reasoner", {})
    composer = record.get("composer", {})
    verification = record.get("verification", {})
    return {
        "candidate": record["candidate"],
        "sample": record["sample"],
        "cache_position": record["cache_position"],
        "status": record["status"],
        "reasoner_wall_seconds": record.get("reasoner_wall_seconds"),
        "total_wall_seconds": record.get("total_wall_seconds"),
        "codex_input_tokens": reasoner.get("input_tokens"),
        "codex_cached_input_tokens": reasoner.get("cached_input_tokens"),
        "codex_cache_ratio": reasoner.get("cache_ratio"),
        "codex_output_tokens": reasoner.get("output_tokens"),
        "codex_reasoning_output_tokens": reasoner.get("reasoning_output_tokens"),
        "event_block_count": reasoner.get("metrics", {}).get("event_block_count"),
        "event_advance_count": reasoner.get("metrics", {}).get(
            "event_advance_count"
        ),
        "story_words": composer.get("story_metrics", {}).get("words"),
        "deepseek_cached_input_tokens": composer.get("cached_input_tokens"),
        "deepseek_estimated_cost_microusd": composer.get(
            "estimated_cost_microusd"
        ),
        "verification_accepted": verification.get("accepted"),
        "failure": record.get("failure"),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--confirm-live", action="store_true")
    parser.add_argument("--describe", action="store_true")
    args = parser.parse_args()

    if args.describe:
        print(
            canonical_json(
                {
                    "benchmark_id": BENCHMARK_ID,
                    "source_text": SOURCE_TEXT,
                    "candidates": to_primitive(CANDIDATES),
                    "samples_per_candidate": 2,
                    "planned_provider_calls": len(CANDIDATES) * 2 * 3,
                    "planned_codex_calls": len(CANDIDATES) * 2 * 2,
                    "planned_deepseek_calls": len(CANDIDATES) * 2,
                }
            )
        )
        return 0
    if not args.confirm_live:
        parser.error("live comparison requires --confirm-live")

    output_dir = args.output_dir.resolve()
    if output_dir.exists():
        raise FileExistsError(
            f"refusing to retry or overwrite model-ladder evidence: {output_dir}"
        )
    output_dir.mkdir(parents=True)
    summary: dict[str, Any] = {
        "schema_version": "cera.codex_reasoner_model_ladder_summary.v1",
        "benchmark_id": BENCHMARK_ID,
        "goal_authority_id": "D-167",
        "current_creator_authorization": (
            "2026-07-30 two-sample Luna-high through Sol-medium comparison"
        ),
        "status": "running",
        "started_at": utc_now(),
        "updated_at": utc_now(),
        "source_text": SOURCE_TEXT,
        "source_sha256": text_sha256(SOURCE_TEXT),
        "depth": "auto",
        "character_autonomy": "both",
        "prompt_handling": "adjustment",
        "candidates": to_primitive(CANDIDATES),
        "samples_per_candidate": 2,
        "cache_method": (
            "byte-identical prepared Reasoner request and schema; observed provider "
            "cached_input_tokens determine whether a cache hit occurred"
        ),
        "provider_stages": planned_provider_stages(),
        "samples": [],
        "story_state_committed": False,
        "story_authority_writes": 0,
        "production_bound": False,
        "retry_count": 0,
        "fallback_count": 0,
    }
    write_json(output_dir / "summary.json", summary)

    with TemporaryDirectory(prefix="cera-model-ladder-world-") as temporary:
        database = Path(temporary) / "hanezawa_model_ladder.sqlite3"
        world = HanezawaHumanTestWorld.initialize(ROOT, database, replace=True)
        prepared = prepare_current_turn(world)
        request = prepared.application_request.reasoner_request
        summary["prepared_turn"] = {
            "request_id": str(request.prepared_turn.request.request_id),
            "request_sha256": request.request_sha256,
            "seed_record_count": len(request.seed_dossier.exact_seed_evidence),
            "seed_dossier_bytes": len(
                canonical_json(request.seed_dossier).encode("utf-8")
            ),
            "present_character_ids": [
                str(value) for value in request.prepared_turn.present_character_ids
            ],
            "eligible_responder_ids": [
                str(value)
                for value in request.prepared_turn.eligible_responding_npc_ids
            ],
            "response_profile_version": (
                prepared.application_request.composer_plan.response_profile_version
            ),
        }
        write_json(output_dir / "summary.json", summary)

        verifier_runner = CodexExecRunner()
        for candidate in CANDIDATES:
            for sample in (1, 2):
                record = run_sample(
                    candidate=candidate,
                    sample=sample,
                    prepared=prepared,
                    world=world,
                    output_dir=output_dir,
                    summary=summary,
                    verifier_runner=verifier_runner,
                )
                summary["samples"].append(sample_summary(record))
                write_json(output_dir / "summary.json", summary)
                print(
                    "CERA_MODEL_LADDER_SAMPLE="
                    + canonical_json(summary["samples"][-1]),
                    flush=True,
                )

        branch = world.store.get_branch(
            request.prepared_turn.request.branch_id
        )
        no_story_writes = (
            branch.generation == 0
            and branch.head_artifact_id is None
            and world.store.table_count("artifacts") == 0
        )
        summary["disposable_database_validation"] = {
            "branch_generation": branch.generation,
            "branch_head_artifact_id": (
                str(branch.head_artifact_id)
                if branch.head_artifact_id is not None
                else None
            ),
            "artifact_count": world.store.table_count("artifacts"),
            "integrity_check": list(world.store.integrity_check()),
            "foreign_key_findings": len(world.store.foreign_key_check()),
            "no_story_writes": no_story_writes,
        }
        if not no_story_writes:
            raise RuntimeError("model ladder unexpectedly mutated story authority")

    passed = sum(value["status"] == "passed" for value in summary["samples"])
    failed = len(summary["samples"]) - passed
    summary.update(
        {
            "status": "completed" if failed == 0 else "completed_with_failures",
            "finished_at": utc_now(),
            "passed_samples": passed,
            "failed_samples": failed,
            "planned_samples": len(CANDIDATES) * 2,
        }
    )
    write_json(output_dir / "summary.json", summary)
    print(
        "CERA_MODEL_LADDER_RESULT="
        + canonical_json(
            {
                "status": summary["status"],
                "passed_samples": passed,
                "failed_samples": failed,
                "output_dir": str(output_dir),
            }
        ),
        flush=True,
    )
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
