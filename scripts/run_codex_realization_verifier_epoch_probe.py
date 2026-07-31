"""Qualify completion-safe verifier transport across five bounded Codex epochs.

Ten synthetic non-story candidates are independently inspected through the
actual ``CodexSceneRealizationVerifierPort``.  One persistent no-MCP runner is
allowed two requests per worker/app-server process, so the probe must launch
exactly five clean process epochs.  Every request has a fresh evidence
identity, ephemeral thread, and isolated workspace.  There is no retry,
fallback, Genesis/story authority, database, or publication.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
from typing import Any

from cera.contracts import BeatState
from cera.ids import IdKind, deterministic_id
from cera.providers import (
    CodexSDKTransport,
    PersistentNoMcpCodexRunner,
    codex_realization_verifier_candidate,
)
from cera.providers.codex_sdk_compat import (
    CODEX_SDK_COMPATIBILITY_ID,
    EXPECTED_ROUTE_NOTIFICATION_SHA256,
    SUPPORTED_SDK_VERSION,
)
from cera.realization import (
    CodexSceneRealizationVerifierPort,
    RealizationBoundaryCheck,
    SceneRealizationBeatExpectation,
    SceneRealizationVerificationCoordinator,
    SceneRealizationVerificationFailure,
    SceneRealizationVerificationRequest,
)
from cera.serialization import canonical_json, text_sha256, to_primitive

from audit_continuous_live_call_budget import (
    DEFAULT_DEEPSEEK_LIMIT,
    DEFAULT_SOL_LIMIT,
    audit as audit_live_call_budget,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = (
    ROOT
    / "evaluation"
    / "evidence"
    / "persistent_codex_transport_probe_2026-07-29_v5"
)
CALL_COUNT = 10
EXPECTED_PROCESS_LAUNCHES = 5
PROBE_SENTENCES = (
    "Guide placed the amber card beside the lamp.",
    "Guide closed the silver notebook.",
    "Guide set the ceramic cup on the tray.",
    "Guide turned the wooden timer upright.",
    "Guide folded the blue cloth once.",
    "Guide moved the brass marker to the center.",
    "Guide placed the green token beside the ruler.",
    "Guide latched the empty document case.",
    "Guide aligned the white card with the mat.",
    "Guide returned the glass weight to its stand.",
)


def build_probe_request(
    qualification_id: str,
    sequence: int,
) -> SceneRealizationVerificationRequest:
    if not 1 <= sequence <= len(PROBE_SENTENCES):
        raise ValueError("verifier epoch probe sequence is out of range")
    namespace = "cera.non_story_realization_verifier_epoch_probe.v1"
    story_text = PROBE_SENTENCES[sequence - 1]
    actor_id = deterministic_id(
        IdKind.CHARACTER,
        namespace,
        f"{qualification_id}|guide",
    )
    return SceneRealizationVerificationRequest(
        schema_version=SceneRealizationVerificationRequest.SCHEMA_VERSION,
        request_id=deterministic_id(
            IdKind.REQUEST,
            namespace,
            f"{qualification_id}|request|{sequence}",
        ),
        branch_id=deterministic_id(
            IdKind.BRANCH,
            namespace,
            f"{qualification_id}|branch|{sequence}",
        ),
        generation_id=deterministic_id(
            IdKind.GENERATION,
            namespace,
            f"{qualification_id}|generation|{sequence}",
        ),
        candidate_sha256=text_sha256(story_text),
        story_text=story_text,
        expected_beats=(
            SceneRealizationBeatExpectation(
                beat_id=deterministic_id(
                    IdKind.BEAT,
                    namespace,
                    f"{qualification_id}|beat|{sequence}",
                ),
                actor_id=actor_id,
                neutral_event=story_text,
                required_state=BeatState.COMPLETED,
            ),
        ),
        selected_participant_ids=(actor_id,),
        protected_user_id=deterministic_id(
            IdKind.CHARACTER,
            namespace,
            f"{qualification_id}|protected-user",
        ),
        protected_user_authorities=(),
        required_boundary_checks=(
            RealizationBoundaryCheck.PROTECTED_USER_NO_UNSUPPLIED_REALIZATION,
        ),
        realization_anchors=(),
        hard_boundaries=(
            "The synthetic candidate contains no protected-user behavior; "
            "do not infer or add any.",
        ),
    )


def _write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _run_calls(
    *,
    route: Any,
    qualification_id: str,
    work_root: Path,
    output_path: Path,
    summary: dict[str, Any],
) -> bool:
    runner = PersistentNoMcpCodexRunner()
    try:
        for sequence in range(1, CALL_COUNT + 1):
            request = build_probe_request(qualification_id, sequence)
            workspace = work_root / f"call-{sequence}"
            workspace.mkdir(parents=True)
            call: dict[str, Any] = {
                "index": sequence,
                "role": "scene_realization_verifier",
                "evidence_identity": str(
                    deterministic_id(
                        IdKind.EVALUATION_RUN,
                        "cera.persistent_codex_verifier_epoch_probe.call.v1",
                        f"{qualification_id}|{sequence}",
                    )
                ),
                "request_id": str(request.request_id),
                "request_sha256": request.request_sha256,
                "candidate_sha256": request.candidate_sha256,
                "status": "prepared",
                "started_at": datetime.now(timezone.utc).isoformat(),
                "dispatch_started": False,
                "external_provider_calls_observed": 0,
                "automatic_retry_count": 0,
                "fallback_enabled": False,
                "story_authority_writes": 0,
                "workspace_retained": False,
                "transport_compatibility_activation_validated": False,
            }
            summary["calls"].append(call)
            _write(output_path, summary)
            try:
                call["status"] = "running"
                call["dispatch_started"] = True
                _write(output_path, summary)
                result = SceneRealizationVerificationCoordinator().execute(
                    request,
                    CodexSceneRealizationVerifierPort(
                        CodexSDKTransport(
                            route,
                            workspace=workspace,
                            runner=runner,
                        )
                    ),
                )
                call.update(
                    {
                        "status": "passed",
                        "finished_at": datetime.now(timezone.utc).isoformat(),
                        "external_provider_calls_observed": 1,
                        "provider_receipt": to_primitive(
                            result.provider_call_receipt
                        ),
                        "verification_receipt": to_primitive(result.receipt),
                        "process_launch_count_after_call": (
                            runner.process_launch_count
                        ),
                        "request_submission_count_after_call": (
                            runner.request_submission_count
                        ),
                        "transport_compatibility_activation_validated": True,
                    }
                )
            except Exception as exc:
                provider_receipt = getattr(
                    exc,
                    "provider_call_receipt",
                    None,
                )
                verification_receipt = getattr(exc, "receipt", None)
                observed = getattr(exc, "external_provider_calls", 0)
                if provider_receipt is not None:
                    observed = max(
                        observed,
                        provider_receipt.external_provider_calls,
                    )
                call.update(
                    {
                        "status": "failed",
                        "finished_at": datetime.now(timezone.utc).isoformat(),
                        "error_code": (
                            "CERA_VERIFIER_FAILED"
                            if isinstance(
                                exc,
                                SceneRealizationVerificationFailure,
                            )
                            else type(exc).__name__
                        ),
                        "error_class": type(exc).__name__,
                        "safe_diagnostics": list(
                            getattr(exc, "safe_diagnostics", ())
                        ),
                        "external_provider_calls_observed": observed,
                        "process_launch_count_after_call": (
                            runner.process_launch_count
                        ),
                        "request_submission_count_after_call": (
                            runner.request_submission_count
                        ),
                    }
                )
                if provider_receipt is not None:
                    call["provider_receipt"] = to_primitive(provider_receipt)
                if verification_receipt is not None:
                    call["verification_receipt"] = to_primitive(
                        verification_receipt
                    )
                summary.update(
                    {
                        "status": "failed",
                        "failed_call_index": sequence,
                        "finished_at": datetime.now(timezone.utc).isoformat(),
                        "successful_calls": sum(
                            value["status"] == "passed"
                            for value in summary["calls"]
                        ),
                        "sol_dispatches": sum(
                            value["dispatch_started"]
                            for value in summary["calls"]
                        ),
                        "deepseek_dispatches": 0,
                        "process_launch_count": runner.process_launch_count,
                        "request_submission_count": (
                            runner.request_submission_count
                        ),
                    }
                )
                _write(output_path, summary)
                return False
            _write(output_path, summary)
        summary.update(
            {
                "process_launch_count": runner.process_launch_count,
                "request_submission_count": runner.request_submission_count,
            }
        )
        _write(output_path, summary)
        return True
    finally:
        runner.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--confirm-live", action="store_true")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--qualification-id",
        default="persistent-codex-verifier-epoch-probe-v5",
    )
    parser.add_argument("--sol-call-limit", type=int, default=DEFAULT_SOL_LIMIT)
    parser.add_argument(
        "--deepseek-call-limit",
        type=int,
        default=DEFAULT_DEEPSEEK_LIMIT,
    )
    args = parser.parse_args()
    if not args.confirm_live:
        parser.error("live provider calls require --confirm-live")
    budget_before = audit_live_call_budget(
        sol_limit=args.sol_call_limit,
        deepseek_limit=args.deepseek_call_limit,
    )
    if budget_before["remaining"]["sol"] < CALL_COUNT:
        raise RuntimeError(
            "live call authority is insufficient for ten verifier epoch calls"
        )
    output_dir = args.output_dir.resolve()
    if output_dir.exists():
        raise RuntimeError(f"refusing to overwrite probe evidence: {output_dir}")
    output_dir.mkdir(parents=True)
    output_path = output_dir / "summary.json"
    route = codex_realization_verifier_candidate(
        model="gpt-5.6-sol",
        effort="medium",
    )
    summary: dict[str, Any] = {
        "schema_version": "cera.persistent_codex_verifier_epoch_probe.v2",
        "qualification_id": args.qualification_id,
        "status": "running",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "calls_required": CALL_COUNT,
        "attempts_per_call": 1,
        "retry_enabled": False,
        "fallback_enabled": False,
        "canonical_story_or_genesis_content_used": False,
        "synthetic_non_story_candidate_used": True,
        "mcp_enabled": False,
        "story_authority_writes": 0,
        "maximum_requests_per_process": (
            PersistentNoMcpCodexRunner.QUALIFIED_MAX_REQUESTS_PER_PROCESS
        ),
        "expected_process_launches": EXPECTED_PROCESS_LAUNCHES,
        "process_tree_cleanup_policy": "force_full_tree_before_replacement",
        "replacement_dispatch_requires_cleanup_confirmation": True,
        "returned_turn_pre_registration_required": True,
        "route_sha256": route.route_sha256,
        "model": route.model_name,
        "reasoning_effort": route.reasoning_effort,
        "sdk_compatibility": {
            "compatibility_id": CODEX_SDK_COMPATIBILITY_ID,
            "sdk_version": SUPPORTED_SDK_VERSION,
            "route_notification_source_sha256": (
                EXPECTED_ROUTE_NOTIFICATION_SHA256
            ),
            "activation_contract": (
                "each successful transport result requires matching worker "
                "activation evidence and a pre-registered returned-turn queue"
            ),
        },
        "budget_before": budget_before,
        "calls": [],
    }
    _write(output_path, summary)
    with tempfile.TemporaryDirectory(
        prefix="cera-verifier-epoch-probe-"
    ) as work:
        if not _run_calls(
            route=route,
            qualification_id=args.qualification_id,
            work_root=Path(work),
            output_path=output_path,
            summary=summary,
        ):
            return 1
    if (
        len(summary["calls"]) != CALL_COUNT
        or any(value["status"] != "passed" for value in summary["calls"])
        or summary["process_launch_count"] != EXPECTED_PROCESS_LAUNCHES
        or summary["request_submission_count"] != CALL_COUNT
        or [
            value["process_launch_count_after_call"]
            for value in summary["calls"]
        ]
        != [1, 1, 2, 2, 3, 3, 4, 4, 5, 5]
        or not all(
            value["transport_compatibility_activation_validated"]
            for value in summary["calls"]
        )
    ):
        raise RuntimeError(
            "verifier epoch probe did not preserve ten calls across five epochs"
        )
    summary.update(
        {
            "status": "passed",
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "successful_calls": CALL_COUNT,
            "sol_dispatches": CALL_COUNT,
            "deepseek_dispatches": 0,
        }
    )
    _write(output_path, summary)
    print(
        "CERA_CODEX_VERIFIER_EPOCH_PROBE="
        + canonical_json(
            {
                "status": "passed",
                "successful_calls": CALL_COUNT,
                "process_launch_count": summary["process_launch_count"],
                "request_submission_count": summary[
                    "request_submission_count"
                ],
                "sdk_compatibility_id": CODEX_SDK_COMPATIBILITY_ID,
            }
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
