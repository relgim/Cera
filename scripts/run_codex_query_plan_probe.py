"""Run one non-story Sol query-plan -> exact-fetch MCP canary.

The probe binds a disposable Hanezawa V1.2 database and one immutable snapshot.
Codex must use the bounded paraphrase query-plan tool, treat its result as a
reference, and fetch an advertised exact section before returning the selected
evidence ID. The evidence directory is append-only by convention and contains
safe receipts only, never prompts, exact evidence text, or raw provider output.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile

from cera.evaluation import RealGenesisSandbox
from cera.genesis.hanezawa_builder import CHARACTER_IDS
from cera.ids import IdKind, deterministic_id
from cera.ingress import RawTurnEnvelope, RawTurnIngressFacade
from cera.kernel import PreflightAuthority, RequestedContentClass, TurnKernel
from cera.providers import (
    CodexSDKTransport,
    codex_query_plan_probe_output_schema,
    codex_reasoner_candidate,
)
from cera.reasoner import (
    McpEvidenceToolName,
    ReasonerEvidenceTools,
    RequestBoundMcpEvidenceBridge,
    SeedDossierAssembler,
)
from cera.serialization import canonical_json, to_primitive


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = (
    ROOT
    / "evaluation"
    / "evidence"
    / "codex_query_plan_probe_2026-07-29_v6"
)


def _id(kind: IdKind, suffix: str):
    return deterministic_id(kind, "cera.codex_query_plan_probe.v2", suffix)


def _write_evidence(output: Path, payload: dict[str, object]) -> None:
    if output.exists():
        raise FileExistsError(f"probe evidence target already exists: {output}")
    output.mkdir(parents=True, exist_ok=False)
    (output / "summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--confirm-live", action="store_true")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    if not args.confirm_live:
        parser.error("--confirm-live is required")
    output = args.output.resolve()
    if output.exists():
        parser.error(f"evidence target already exists: {output}")

    summary: dict[str, object] = {
        "schema_version": "cera.codex_query_plan_probe.v2",
        "goal_authority_id": "D-150",
        "provider_category": "sol",
        "dispatch_started": False,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "failed_before_dispatch",
        "external_provider_calls": 0,
        "automatic_retry_count": 0,
        "story_authority_writes": 0,
        "retains_prompt": False,
        "retains_raw_output": False,
        "retains_exact_evidence": False,
        "error_code": None,
        "error_message": None,
    }
    exit_code = 1
    bridge = None
    try:
        with RealGenesisSandbox.create(ROOT, revision="v1_2") as sandbox:
            state = sandbox.store.get_branch(sandbox.branch_id)
            ted = sandbox.protected_user_id()
            hana = CHARACTER_IDS["Hana"]
            envelope = RawTurnEnvelope(
                schema_version=RawTurnEnvelope.SCHEMA_VERSION,
                world_id=sandbox.world_id,
                request_id=_id(IdKind.REQUEST, f"{output.name}-request"),
                session_id=_id(IdKind.SESSION, f"{output.name}-session"),
                branch_id=sandbox.branch_id,
                expected_generation=state.generation,
                expected_parent_artifact_id=state.head_artifact_id,
                genesis_revision_id=sandbox.revision_id,
                protected_user_id=ted,
                present_character_ids=(ted, hana),
                eligible_responder_ids=(hana,),
                raw_message="Non-story retrieval canary for a paraphrased long silence in contact.",
                idempotency_key=f"codex-query-plan-probe-{output.name}",
                world_mode=sandbox.open_snapshot("probe-mode").world_mode,
                access_scope=sandbox.system_scope(),
                preflight_authority=PreflightAuthority(RequestedContentClass.ORDINARY),
                hard_boundaries=(
                    "This is a retrieval canary, not story generation.",
                    "Search references require exact expansion before use.",
                ),
            )
            ingress = RawTurnIngressFacade(
                turn_kernel=TurnKernel(sandbox.service),
                seed_assembler=SeedDossierAssembler(sandbox.service),
            ).prepare(envelope)
            request = ingress.reasoner_request
            tools = ReasonerEvidenceTools(sandbox.service, request)
            target_record = sandbox.record_with_payload_value("memory_id", "H-M08")
            expected_evidence_id = sandbox.evidence_id(
                request.prepared_turn.evidence_snapshot,
                target_record.record_id,
            )
            bridge = RequestBoundMcpEvidenceBridge(
                tools,
                reasoner_request_sha256=request.request_sha256,
                snapshot=request.prepared_turn.evidence_snapshot,
                minimum_tool_calls=2,
                maximum_tool_calls=2,
            )
            route = codex_reasoner_candidate(model="gpt-5.6-sol", effort="medium")
            with bridge:
                with tempfile.TemporaryDirectory(
                    prefix="cera-codex-query-plan-probe-"
                ) as workspace:
                    summary["dispatch_started"] = True
                    result = CodexSDKTransport(
                        route,
                        workspace=Path(workspace),
                    ).invoke(
                        (
                            "This is a non-story retrieval canary. Call "
                            "cera_search_query_plan exactly once with primary_terms "
                            "[\"long silence\", \"contact\"], alternate_term_sets "
                            "[[\"missed calls\"], [\"years without answering\"]], "
                            f"entity_ids [\"{hana}\"], record_types [\"memory\"], "
                            "limit 4, and maximum_variants 3. Do not supply an "
                            "ambiguity_policy field; Python owns that policy. From its privacy-filtered "
                            "references, select the sole candidate whose advertised title "
                            "and abstract concern the missed-calls period. Then call "
                            "cera_fetch_evidence exactly once for that returned evidence_id "
                            "with exactly evidence_id set to that returned ID and sections "
                            "[\"remembered_content\"], which the reference "
                            "advertises. Return status ok, that evidence_id, tool_calls 2, "
                            "and exact_fetch_completed true. Do not call another tool."
                        ),
                        output_schema=codex_query_plan_probe_output_schema(),
                        mcp_binding=bridge.runtime_binding,
                    )
            bridge_receipt = bridge.finalize(result)
            parsed = result.parsed_json or {}
            valid = (
                parsed.get("status") == "ok"
                and parsed.get("evidence_id") == str(expected_evidence_id)
                and parsed.get("tool_calls") == 2
                and parsed.get("exact_fetch_completed") is True
                and expected_evidence_id in tools.exact_records
                and result.tool_call_count == 2
                and result.failed_tool_call_count == 0
                and len(bridge_receipt.calls) == 2
                and tuple(value.tool_name for value in bridge_receipt.calls)
                == (
                    McpEvidenceToolName.SEARCH_QUERY_PLAN,
                    McpEvidenceToolName.FETCH_EVIDENCE,
                )
            )
            if not valid:
                raise RuntimeError("query-plan canary did not satisfy the exact contract")
            summary.update(
                {
                    "status": "passed",
                    "external_provider_calls": result.receipt.external_provider_calls,
                    "provider_receipt": to_primitive(result.receipt),
                    "bridge_receipt": to_primitive(bridge_receipt),
                    "expected_evidence_id": str(expected_evidence_id),
                    "tool_names": list(result.tool_names),
                    "tool_call_count": result.tool_call_count,
                    "failed_tool_call_count": result.failed_tool_call_count,
                    "exact_fetch_count": len(tools.exact_records),
                    "sandbox_deleted_after_exit": True,
                }
            )
            exit_code = 0
    except Exception as exc:
        envelope = getattr(exc, "envelope", None)
        code = getattr(exc, "code", None)
        provider_receipt = getattr(exc, "provider_call_receipt", None)
        safe_calls = (
            tuple(getattr(getattr(bridge, "dispatcher", None), "calls", ()))
            if bridge is not None
            else ()
        )
        summary.update(
            {
                "status": "failed",
                "error_code": (
                    code.value
                    if code is not None and hasattr(code, "value")
                    else type(exc).__name__
                ),
                "error_message": (
                    envelope.message if envelope is not None else str(exc)
                ),
                "external_provider_calls": (
                    provider_receipt.external_provider_calls
                    if provider_receipt is not None
                    else 0
                ),
                "provider_receipt": (
                    to_primitive(provider_receipt)
                    if provider_receipt is not None
                    else None
                ),
                "safe_tool_call_receipts": to_primitive(safe_calls),
                "observed_tool_names": list(
                    getattr(exc, "mcp_tool_names", ())
                ),
                "observed_tool_call_count": getattr(
                    exc, "mcp_tool_call_count", 0
                ),
                "observed_failed_tool_call_count": getattr(
                    exc, "mcp_failed_tool_call_count", 0
                ),
            }
        )
    _write_evidence(output, summary)
    print(canonical_json(summary))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
