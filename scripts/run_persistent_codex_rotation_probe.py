"""Qualify two bounded verifier epochs with mandatory full-tree cleanup.

Four non-story Scene Realization Verifier calls are submitted through one
``PersistentNoMcpCodexRunner``.  The runner must use exactly two process
launches and dispatch exactly two requests per process.  Rotation occurs before
the third dispatch, requires confirmed worker/app-server tree termination, and
is not a retry or fallback.  Every request receives a fresh isolated workspace
and ephemeral Codex thread.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
from typing import Any

from cera.ids import IdKind, deterministic_id
from cera.providers import (
    CodexSDKTransport,
    PersistentNoMcpCodexRunner,
    ProviderTransportError,
    codex_realization_verifier_candidate,
    codex_transport_probe_output_schema,
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
    / "persistent_codex_transport_probe_2026-07-29_v3"
)
CALL_COUNT = 4
EXPECTED_PROCESS_LAUNCHES = 2


def _write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _run_calls(
    *,
    qualification_id: str,
    work_root: Path,
    output_path: Path,
    summary: dict[str, Any],
) -> bool:
    runner = PersistentNoMcpCodexRunner()
    route = codex_realization_verifier_candidate(
        model="gpt-5.6-sol",
        effort="medium",
    )
    try:
        for sequence in range(1, CALL_COUNT + 1):
            prompt = (
                "This is a non-story CERA verifier process-rotation probe with no "
                "tools, Genesis, character, memory, candidate prose, or story "
                f"authority. Probe sequence {sequence}. Return status ok and "
                "tools_used 0. Do not use tools."
            )
            workspace = work_root / f"call-{sequence}"
            workspace.mkdir(parents=True)
            call: dict[str, Any] = {
                "index": sequence,
                "role": "scene_realization_verifier",
                "sequence_in_probe": sequence,
                "evidence_identity": str(
                    deterministic_id(
                        IdKind.EVALUATION_RUN,
                        "cera.persistent_codex_rotation_probe.call.v1",
                        f"{qualification_id}|{sequence}",
                    )
                ),
                "status": "prepared",
                "started_at": datetime.now(timezone.utc).isoformat(),
                "prompt_sha256": text_sha256(prompt),
                "schema_sha256": text_sha256(
                    canonical_json(codex_transport_probe_output_schema())
                ),
                "dispatch_started": False,
                "external_provider_calls_observed": 0,
                "automatic_retry_count": 0,
                "fallback_enabled": False,
                "story_authority_writes": 0,
                "workspace_retained": False,
            }
            summary["calls"].append(call)
            _write(output_path, summary)
            result = None
            try:
                call["status"] = "running"
                call["dispatch_started"] = True
                _write(output_path, summary)
                result = CodexSDKTransport(
                    route,
                    workspace=workspace,
                    runner=runner,
                ).invoke(
                    prompt,
                    output_schema=codex_transport_probe_output_schema(),
                )
                if result.parsed_json != {"status": "ok", "tools_used": 0}:
                    raise RuntimeError(
                        "persistent Codex rotation probe returned the wrong payload"
                    )
                call.update(
                    {
                        "status": "passed",
                        "finished_at": datetime.now(timezone.utc).isoformat(),
                        "external_provider_calls_observed": 1,
                        "output_sha256": result.receipt.output_sha256,
                        "provider_receipt": to_primitive(result.receipt),
                        "process_launch_count_after_call": (
                            runner.process_launch_count
                        ),
                        "request_submission_count_after_call": (
                            runner.request_submission_count
                        ),
                    }
                )
            except Exception as exc:
                diagnostics = tuple(getattr(exc, "safe_diagnostics", ()))
                observed = getattr(exc, "external_provider_calls_observed", 0)
                provider_receipt = (
                    result.receipt
                    if result is not None
                    else getattr(exc, "provider_call_receipt", None)
                )
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
                            exc.code.value
                            if isinstance(exc, ProviderTransportError)
                            else "CERA_PERSISTENT_ROTATION_PROBE_FAILED"
                        ),
                        "error_class": type(exc).__name__,
                        "safe_diagnostics": list(diagnostics),
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
        default="persistent-codex-tree-cleanup-probe-v3",
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
            "live call authority is insufficient for four persistent Sol probes"
        )
    output_dir = args.output_dir.resolve()
    if output_dir.exists():
        raise RuntimeError(f"refusing to overwrite probe evidence: {output_dir}")
    output_dir.mkdir(parents=True)
    output_path = output_dir / "summary.json"
    summary: dict[str, Any] = {
        "schema_version": "cera.persistent_codex_tree_cleanup_probe.v1",
        "qualification_id": args.qualification_id,
        "status": "running",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "calls_required": CALL_COUNT,
        "attempts_per_call": 1,
        "retry_enabled": False,
        "fallback_enabled": False,
        "story_or_genesis_content_used": False,
        "mcp_enabled": False,
        "story_authority_writes": 0,
        "maximum_requests_per_process": (
            PersistentNoMcpCodexRunner.QUALIFIED_MAX_REQUESTS_PER_PROCESS
        ),
        "expected_process_launches": EXPECTED_PROCESS_LAUNCHES,
        "process_tree_cleanup_policy": "force_full_tree_before_replacement",
        "replacement_dispatch_requires_cleanup_confirmation": True,
        "budget_before": budget_before,
        "calls": [],
    }
    _write(output_path, summary)
    with tempfile.TemporaryDirectory(
        prefix="cera-persistent-codex-rotation-probe-"
    ) as work:
        if not _run_calls(
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
        != [1, 1, 2, 2]
    ):
        raise RuntimeError(
            "persistent transport rotation did not preserve two-call epochs"
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
        "CERA_PERSISTENT_CODEX_ROTATION_PROBE="
        + canonical_json(
            {
                "status": "passed",
                "successful_calls": CALL_COUNT,
                "process_launch_count": summary["process_launch_count"],
                "request_submission_count": summary["request_submission_count"],
            }
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
