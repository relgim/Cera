"""Run three fresh full CERA samples with Luna-max on the Fast tier.

This diagnostic uses one byte-identical Long doorway request in a disposable
V1.2 world.  Every sample receives a fresh ephemeral Luna thread while the
supervised app-server process remains warm.  The current non-thinking DeepSeek
Flash Composer and independent Sol-medium verifier complete the route.

No retry, fallback, publication, production binding, or story-state commit is
permitted.  A failed sample remains terminal evidence and the next sample is a
new independent attempt, never a retry of the failed generation.
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
    CodexExecRunner,
    CodexSDKTransport,
    CodexStructuredOutputTransport,
    DeepSeekChatTransport,
    codex_cli_realization_verifier_candidate,
    codex_reasoner_candidate,
    deepseek_composer_candidate,
)
from cera.realization import (
    CodexSceneRealizationVerifierPort,
    InMemoryRejectedCandidateReviewStore,
    SceneRealizationVerificationCoordinator,
)
from cera.reasoner import CodexSceneReasonerPort, ReasonerCoordinator
from cera.runtime import HanezawaHumanTestWorld, LiveShapedTurnPipeline
from cera.serialization import canonical_json, text_sha256, to_primitive

from scripts.run_codex_branch_session_ab import (
    InProcessSessionRunner,
    NamespacedTransport,
    ROOT,
    outcome_summary,
    prepare_message,
)
from cera.providers.codex import _SubprocessCodexRunner


MODEL = "gpt-5.6-luna"
EFFORT = "max"
SERVICE_TIER = "priority"
SERVICE_TIER_DISPLAY = "Fast"
SOURCE_TEXT = "Hello, my name is Ted. Is this the hanezawa household?"
RUN_COUNT = 3
BENCHMARK_ID = "cera-luna-max-fast-three-run-2026-07-30-v3"
DEFAULT_OUTPUT = (
    ROOT / "evaluation" / "evidence" / "luna_max_fast_three_run_2026-07-30_v3"
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


def provider_metrics(receipt) -> dict[str, Any]:
    return {
        "provider_receipt": to_primitive(receipt),
        "duration_seconds": round(receipt.duration_ms / 1000, 3),
        "input_tokens": receipt.input_tokens,
        "cached_input_tokens": receipt.cached_input_tokens,
        "uncached_input_tokens": receipt.input_tokens - receipt.cached_input_tokens,
        "output_tokens": receipt.output_tokens,
        "reasoning_output_tokens": receipt.reasoning_output_tokens,
    }


def story_metrics(prose: str) -> dict[str, Any]:
    return {
        "characters": len(prose),
        "words": len(prose.split()),
        "paragraphs": len(
            [value for value in prose.replace("\r\n", "\n").split("\n\n") if value.strip()]
        ),
        "prose_sha256": text_sha256(prose),
    }


def safe_failure(exc: Exception) -> dict[str, Any]:
    envelope = getattr(exc, "envelope", None)
    receipt = getattr(exc, "provider_call_receipt", None)
    return {
        "type": type(exc).__name__,
        "message": str(exc) or type(exc).__name__,
        "error_code": (
            getattr(getattr(envelope, "error_code", None), "value", None)
            or getattr(getattr(exc, "code", None), "value", None)
        ),
        "safe_diagnostics": list(getattr(exc, "safe_diagnostics", ())),
        "external_provider_calls_observed": getattr(
            exc, "external_provider_calls_observed", 0
        ),
        "provider_call_receipt": to_primitive(receipt),
        "retained_evidence_handles": to_primitive(
            getattr(exc, "retained_evidence_handles", ())
        ),
        "privacy_safe_receipt_payloads": to_primitive(
            getattr(exc, "privacy_safe_receipt_payloads", ())
        ),
    }


def verify_luna_capability(codex) -> dict[str, Any]:
    response = codex.models(include_hidden=True)
    model = next(
        (
            value
            for value in response.data
            if (getattr(value, "model", None) or getattr(value, "id", None))
            == MODEL
        ),
        None,
    )
    if model is None:
        raise RuntimeError("Luna 5.6 is unavailable in the current Codex session")
    efforts = tuple(
        value.reasoning_effort.value for value in model.supported_reasoning_efforts
    )
    tiers = tuple(value.id for value in (model.service_tiers or ()))
    if EFFORT not in efforts:
        raise RuntimeError("Luna does not advertise max reasoning effort")
    if SERVICE_TIER not in tiers:
        raise RuntimeError("Luna does not advertise the priority/Fast tier")
    return {
        "model": MODEL,
        "display_name": model.display_name,
        "supported_reasoning_efforts": list(efforts),
        "selected_reasoning_effort": EFFORT,
        "service_tiers": [
            {
                "id": value.id,
                "name": value.name,
                "description": value.description,
            }
            for value in (model.service_tiers or ())
        ],
        "selected_service_tier": SERVICE_TIER,
        "selected_service_tier_display": SERVICE_TIER_DISPLAY,
        "service_tier_request_verified_by_receipt": False,
        "verification_note": (
            "The app-server capability list advertises the tier and accepted "
            "the explicit turn request; the current provider receipt does not "
            "echo the served service tier."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--confirm-live", action="store_true")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    if not args.confirm_live:
        parser.error("live provider calls require --confirm-live")
    output_dir = args.output.resolve()
    if output_dir.exists():
        raise SystemExit(f"refusing to overwrite Luna evidence: {output_dir}")
    output_dir.mkdir(parents=True)

    summary: dict[str, Any] = {
        "schema_version": "cera.luna_max_fast_three_run_summary.v1",
        "benchmark_id": BENCHMARK_ID,
        "status": "running",
        "started_at": utc_now(),
        "source_text": SOURCE_TEXT,
        "source_sha256": text_sha256(SOURCE_TEXT),
        "depth": "long",
        "reasoner": {
            "model": MODEL,
            "effort": EFFORT,
            "service_tier": SERVICE_TIER,
            "service_tier_display": SERVICE_TIER_DISPLAY,
        },
        "composer": {"model": "deepseek-v4-flash", "thinking": False},
        "verifier": {"model": "gpt-5.6-sol", "effort": "medium"},
        "planned_runs": RUN_COUNT,
        "samples": [],
        "provider_stages": [
            {
                "sample": sample,
                "stage": stage,
                "provider": provider,
                "model": model,
                "dispatch_started": False,
                "status": "planned",
            }
            for sample in range(1, RUN_COUNT + 1)
            for stage, provider, model in (
                ("reasoner", "openai_codex", MODEL),
                ("composer", "deepseek", "deepseek-v4-flash"),
                ("verifier", "openai_codex", "gpt-5.6-sol"),
            )
        ],
        "retry_count": 0,
        "fallback_count": 0,
        "story_state_committed": False,
        "story_authority_writes": 0,
        "production_route_changed": False,
        "prior_failed_harness_evidence": [
            "evaluation/evidence/luna_max_fast_three_run_2026-07-30_v1",
            "evaluation/evidence/luna_max_fast_three_run_2026-07-30_v2",
        ],
    }
    write_json(output_dir / "summary.json", summary)

    with TemporaryDirectory(prefix="cera-luna-max-fast-") as temporary:
        temporary_path = Path(temporary)
        world = HanezawaHumanTestWorld.initialize(
            ROOT, temporary_path / "world.sqlite3", replace=True
        )
        prepared = prepare_message(
            world,
            message=SOURCE_TEXT,
            session_id="luna-max-fast-three-run",
        )
        request = prepared.application_request.reasoner_request
        plan = prepared.application_request.composer_plan
        workspace = temporary_path / "reasoner_workspace"
        workspace.mkdir()
        route = replace(
            codex_reasoner_candidate(model=MODEL, effort=EFFORT),
            route_id="codex_reasoner_luna_max_fast_three_run",
            adapter_id="cera.codex_luna_max_fast_diagnostic.v1",
            prompt_version="cera.codex_scene_reasoner_prompt.v24.luna_max_fast_v1",
        )
        verifier_runner = CodexExecRunner()
        rejected_store = InMemoryRejectedCandidateReviewStore()
        coordinator = ReasonerCoordinator(world.service, TurnKernel(world.service))

        with InProcessSessionRunner(
            reuse_thread=False,
            workspace=workspace,
            service_tier=SERVICE_TIER,
        ) as runner:
            assert runner.codex is not None
            summary["capability"] = verify_luna_capability(runner.codex)
            for sample_index in range(1, RUN_COUNT + 1):
                record: dict[str, Any] = {
                    "sample": sample_index,
                    "status": "started",
                    "started_at": utc_now(),
                    "reasoner_service_tier_requested": SERVICE_TIER,
                    "story_state_committed": False,
                }
                sample_started = time.perf_counter()
                provisional: list[Any] = []
                stage_map = {
                    value["stage"]: value
                    for value in summary["provider_stages"]
                    if value["sample"] == sample_index
                }
                verifier_workspace = temporary_path / f"verifier_{sample_index}"
                verifier_workspace.mkdir()
                reasoner_workspace = temporary_path / f"reasoner_{sample_index}"
                reasoner_workspace.mkdir()
                reasoner_port = CodexSceneReasonerPort(
                    NamespacedTransport(
                        CodexSDKTransport(
                            route,
                            workspace=reasoner_workspace,
                            runner=_SubprocessCodexRunner(
                                service_tier=SERVICE_TIER
                            ),
                        ),
                        BENCHMARK_ID,
                    ),
                    evidence_tools_enabled=False,
                )
                pipeline = LiveShapedTurnPipeline(
                    coordinator,
                    ComposerContextAssembler(world.service),
                    ComposerCoordinator(),
                    realization_verification_coordinator=(
                        SceneRealizationVerificationCoordinator(rejected_store)
                    ),
                    realization_verifier_port=CodexSceneRealizationVerifierPort(
                        CodexStructuredOutputTransport(
                            codex_cli_realization_verifier_candidate(
                                model="gpt-5.6-sol", effort="medium"
                            ),
                            workspace=verifier_workspace,
                            runner=verifier_runner,
                        )
                    ),
                )
                composer_port = DeepSeekSceneComposerPort(
                    DeepSeekChatTransport(
                        deepseek_composer_candidate(model="deepseek-v4-flash")
                    )
                )

                def capture(value) -> None:
                    provisional.append(value)
                    stage_map["composer"]["status"] = "completed"
                    stage_map["verifier"]["dispatch_started"] = True
                    stage_map["verifier"]["status"] = "started"
                    write_json(output_dir / "summary.json", summary)

                try:
                    stage_map["reasoner"]["dispatch_started"] = True
                    stage_map["reasoner"]["status"] = "started"
                    write_json(output_dir / "summary.json", summary)
                    reasoner_started = time.perf_counter()
                    reasoned = coordinator.execute(request, reasoner_port)
                    record["reasoner_wall_seconds"] = round(
                        time.perf_counter() - reasoner_started, 3
                    )
                    stage_map["reasoner"]["status"] = "completed"
                    record["reasoner"] = {
                        **provider_metrics(reasoned.provider_call_receipt),
                        "outcome": outcome_summary(reasoned),
                    }

                    stage_map["composer"]["dispatch_started"] = True
                    stage_map["composer"]["status"] = "started"
                    write_json(output_dir / "summary.json", summary)
                    pipeline_started = time.perf_counter()
                    result = pipeline.execute(
                        request,
                        None,
                        plan,
                        composer_port,
                        precomputed_reasoner_result=reasoned,
                        provisional_candidate_callback=capture,
                    )
                    record["pipeline_wall_seconds"] = round(
                        time.perf_counter() - pipeline_started, 3
                    )
                    stage_map["verifier"]["status"] = "completed"
                    prose = result.accepted_artifact.accepted_prose
                    record["composer"] = {
                        **provider_metrics(result.composer.provider_call_receipt),
                        **story_metrics(prose),
                        "accepted_prose": prose,
                    }
                    record["verifier"] = {
                        **provider_metrics(
                            result.realization_verification.provider_call_receipt
                        ),
                        "accepted": result.realization_verification.accepted,
                    }
                    record["status"] = "passed"
                except Exception as exc:
                    record["status"] = "failed"
                    record["failure"] = safe_failure(exc)
                    for stage in stage_map.values():
                        if stage["status"] == "started":
                            stage["status"] = "failed"
                    if provisional:
                        value = provisional[0]
                        prose = value.composer.candidate.story_text
                        record["composer"] = {
                            **provider_metrics(value.composer.provider_call_receipt),
                            **story_metrics(prose),
                            "accepted_prose": prose,
                        }
                record["total_wall_seconds"] = round(
                    time.perf_counter() - sample_started, 3
                )
                record["finished_at"] = utc_now()
                summary["samples"].append(record)
                write_json(output_dir / "samples" / f"sample_{sample_index}.json", record)
                write_json(output_dir / "summary.json", summary)
                print(
                    "CERA_LUNA_MAX_FAST_SAMPLE="
                    + canonical_json(
                        {
                            "sample": sample_index,
                            "status": record["status"],
                            "wall_seconds": record["total_wall_seconds"],
                            "reasoner_seconds": record.get("reasoner", {}).get(
                                "duration_seconds"
                            ),
                            "composer_seconds": record.get("composer", {}).get(
                                "duration_seconds"
                            ),
                            "verifier_seconds": record.get("verifier", {}).get(
                                "duration_seconds"
                            ),
                            "story_words": record.get("composer", {}).get("words"),
                        }
                    ),
                    flush=True,
                )

        summary["database_validation"] = {
            "artifact_count": world.store.table_count("artifacts"),
            "integrity_check": list(world.store.integrity_check()),
            "foreign_key_findings": len(world.store.foreign_key_check()),
        }

    passed = sum(value["status"] == "passed" for value in summary["samples"])
    summary["passed_runs"] = passed
    summary["failed_runs"] = RUN_COUNT - passed
    summary["status"] = "passed" if passed == RUN_COUNT else "completed_with_failures"
    summary["finished_at"] = utc_now()
    summary["total_luna_calls"] = sum(
        value["dispatch_started"]
        for value in summary["provider_stages"]
        if value["stage"] == "reasoner"
    )
    summary["total_deepseek_calls"] = sum(
        value["dispatch_started"]
        for value in summary["provider_stages"]
        if value["stage"] == "composer"
    )
    summary["total_sol_verifier_calls"] = sum(
        value["dispatch_started"]
        for value in summary["provider_stages"]
        if value["stage"] == "verifier"
    )
    write_json(output_dir / "summary.json", summary)
    print(
        "CERA_LUNA_MAX_FAST_RESULT="
        + canonical_json(
            {
                "status": summary["status"],
                "passed_runs": summary["passed_runs"],
                "failed_runs": summary["failed_runs"],
                "luna_calls": summary["total_luna_calls"],
                "deepseek_calls": summary["total_deepseek_calls"],
                "sol_verifier_calls": summary["total_sol_verifier_calls"],
            }
        ),
        flush=True,
    )
    return 0 if summary["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
