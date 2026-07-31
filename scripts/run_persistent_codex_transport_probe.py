"""Qualify persistent no-MCP Codex process reuse with four non-story calls.

Two sequential calls run through one fixed Scene Reasoner process, followed by
two sequential calls through a separate fixed Scene Realization Verifier
process.  Every call uses a fresh ephemeral thread and isolated workspace.
There is no retry, fallback, story/Genesis packet, MCP authority, or database.
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
    codex_reasoner_candidate,
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
    / "persistent_codex_transport_probe_2026-07-29_v1"
)


def _write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _run_role(
    *,
    role: str,
    route,
    qualification_id: str,
    work_root: Path,
    output_path: Path,
    summary: dict[str, Any],
) -> bool:
    runner = PersistentNoMcpCodexRunner()
    try:
        for sequence in (1, 2):
            call_index = len(summary["calls"]) + 1
            evidence_identity = deterministic_id(
                IdKind.EVALUATION_RUN,
                "cera.persistent_codex_transport_probe.call.v1",
                f"{qualification_id}|{role}|{sequence}",
            )
            prompt = (
                "This is a non-story CERA persistent-transport probe with no tools, "
                "Genesis, character, memory, or story authority. Return status ok and "
                "tools_used 0. Do not use tools."
            )
            workspace = work_root / role / f"call-{sequence}"
            workspace.mkdir(parents=True)
            call: dict[str, Any] = {
                "index": call_index,
                "role": role,
                "sequence_in_role_session": sequence,
                "evidence_identity": str(evidence_identity),
                "status": "running",
                "started_at": datetime.now(timezone.utc).isoformat(),
                "prompt_sha256": text_sha256(prompt),
                "schema_sha256": text_sha256(
                    canonical_json(codex_transport_probe_output_schema())
                ),
                "dispatch_started": True,
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
                result = CodexSDKTransport(
                    route,
                    workspace=workspace,
                    runner=runner,
                ).invoke(
                    prompt,
                    output_schema=codex_transport_probe_output_schema(),
                )
                if result.parsed_json != {"status": "ok", "tools_used": 0}:
                    raise RuntimeError("persistent Codex probe returned the wrong payload")
                call.update(
                    {
                        "status": "passed",
                        "finished_at": datetime.now(timezone.utc).isoformat(),
                        "external_provider_calls_observed": 1,
                        "output_sha256": result.receipt.output_sha256,
                        "provider_receipt": to_primitive(result.receipt),
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
                    observed = max(observed, provider_receipt.external_provider_calls)
                call.update(
                    {
                        "status": "failed",
                        "finished_at": datetime.now(timezone.utc).isoformat(),
                        "error_code": (
                            exc.code.value
                            if isinstance(exc, ProviderTransportError)
                            else "CERA_PERSISTENT_TRANSPORT_PROBE_FAILED"
                        ),
                        "error_class": type(exc).__name__,
                        "safe_diagnostics": list(diagnostics),
                        "external_provider_calls_observed": observed,
                    }
                )
                if provider_receipt is not None:
                    call["provider_receipt"] = to_primitive(provider_receipt)
                summary["status"] = "failed"
                summary["failed_call_index"] = call_index
                summary["finished_at"] = datetime.now(timezone.utc).isoformat()
                summary["successful_calls"] = sum(
                    value["status"] == "passed" for value in summary["calls"]
                )
                summary["sol_dispatches"] = sum(
                    value["dispatch_started"] for value in summary["calls"]
                )
                summary["deepseek_dispatches"] = 0
                summary["role_sessions"][role] = {
                    "process_launch_count": runner.process_launch_count,
                    "request_submission_count": runner.request_submission_count,
                }
                _write(output_path, summary)
                return False
            _write(output_path, summary)
        summary["role_sessions"][role] = {
            "process_launch_count": runner.process_launch_count,
            "request_submission_count": runner.request_submission_count,
        }
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
        default="persistent-codex-transport-probe-v1",
    )
    parser.add_argument("--sol-call-limit", type=int, default=DEFAULT_SOL_LIMIT)
    parser.add_argument(
        "--deepseek-call-limit", type=int, default=DEFAULT_DEEPSEEK_LIMIT
    )
    args = parser.parse_args()
    if not args.confirm_live:
        parser.error("live provider calls require --confirm-live")
    budget_before = audit_live_call_budget(
        sol_limit=args.sol_call_limit,
        deepseek_limit=args.deepseek_call_limit,
    )
    if budget_before["remaining"]["sol"] < 4:
        raise RuntimeError(
            "live call authority is insufficient for four persistent Sol probes"
        )
    output_dir = args.output_dir.resolve()
    if output_dir.exists():
        raise RuntimeError(f"refusing to overwrite probe evidence: {output_dir}")
    output_dir.mkdir(parents=True)
    output_path = output_dir / "summary.json"
    summary: dict[str, Any] = {
        "schema_version": "cera.persistent_codex_transport_probe.v1",
        "qualification_id": args.qualification_id,
        "status": "running",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "calls_required": 4,
        "attempts_per_call": 1,
        "retry_enabled": False,
        "fallback_enabled": False,
        "story_or_genesis_content_used": False,
        "mcp_enabled": False,
        "story_authority_writes": 0,
        "budget_before": budget_before,
        "calls": [],
        "role_sessions": {},
    }
    _write(output_path, summary)
    with tempfile.TemporaryDirectory(prefix="cera-persistent-codex-probe-") as work:
        work_root = Path(work)
        if not _run_role(
            role="scene_reasoner",
            route=codex_reasoner_candidate(model="gpt-5.6-sol", effort="medium"),
            qualification_id=args.qualification_id,
            work_root=work_root,
            output_path=output_path,
            summary=summary,
        ):
            return 1
        if not _run_role(
            role="scene_realization_verifier",
            route=codex_realization_verifier_candidate(
                model="gpt-5.6-sol",
                effort="medium",
            ),
            qualification_id=args.qualification_id,
            work_root=work_root,
            output_path=output_path,
            summary=summary,
        ):
            return 1
    if len(summary["calls"]) != 4 or any(
        value["status"] != "passed" for value in summary["calls"]
    ):
        raise RuntimeError("persistent transport probe did not pass four calls")
    if any(
        value != {"process_launch_count": 1, "request_submission_count": 2}
        for value in summary["role_sessions"].values()
    ):
        raise RuntimeError("persistent transport probe did not reuse one process per role")
    summary["status"] = "passed"
    summary["finished_at"] = datetime.now(timezone.utc).isoformat()
    summary["successful_calls"] = 4
    summary["sol_dispatches"] = 4
    summary["deepseek_dispatches"] = 0
    _write(output_path, summary)
    print(
        "CERA_PERSISTENT_CODEX_PROBE="
        + canonical_json(
            {
                "status": "passed",
                "successful_calls": 4,
                "role_sessions": summary["role_sessions"],
            }
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
