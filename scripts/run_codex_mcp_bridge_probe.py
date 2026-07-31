"""Explicit non-story Codex/MCP bridge probe; never imported by tests."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import tempfile

from cera.evidence import (
    EvidenceAccessScope,
    EvidenceRequesterRole,
    EvidenceSnapshot,
    EvidenceWorldMode,
)
from cera.ids import IdKind, deterministic_id
from cera.providers import (
    CodexSDKTransport,
    codex_mcp_probe_output_schema,
    codex_reasoner_candidate,
)
from cera.reasoner import RequestBoundMcpEvidenceBridge
from cera.serialization import domain_sha256


def _id(kind: IdKind, suffix: str):
    return deterministic_id(kind, "cera.codex_mcp_bridge_probe.v1", suffix)


class SnapshotOnlyEvidenceTools:
    """Synthetic fixture with no story, Genesis, database, or exact evidence."""

    def __init__(self, snapshot: EvidenceSnapshot) -> None:
        self.snapshot = snapshot
        self.tool_call_count = 0
        self.exact_evidence: dict = {}
        self.cumulative_returned_bytes = 0

    def get_turn_snapshot(self) -> EvidenceSnapshot:
        self.tool_call_count += 1
        return self.snapshot

    def resolve_entities(self, _request):
        raise AssertionError("snapshot-only probe forbids entity resolution")

    def search_evidence(self, _request):
        raise AssertionError("snapshot-only probe forbids search")

    def fetch_evidence(self, _request):
        raise AssertionError("snapshot-only probe forbids fetch")

    def get_character_sections(self, _request):
        raise AssertionError("snapshot-only probe forbids character lookup")

    def get_continuity(self, _request):
        raise AssertionError("snapshot-only probe forbids continuity traversal")


def _snapshot() -> EvidenceSnapshot:
    return EvidenceSnapshot(
        schema_version=EvidenceSnapshot.SCHEMA_VERSION,
        snapshot_token=_id(IdKind.SNAPSHOT, "snapshot"),
        request_id=_id(IdKind.REQUEST, "request"),
        world_id=_id(IdKind.WORLD, "world"),
        branch_id=_id(IdKind.BRANCH, "branch"),
        generation=0,
        branch_head_artifact_id=None,
        genesis_revision_id=_id(IdKind.GENESIS_REVISION, "genesis"),
        access_scope=EvidenceAccessScope(
            requester_role=EvidenceRequesterRole.SYSTEM_REASONER,
            perspective_id=None,
            permitted_private_owner_ids=(),
            allow_system_private=False,
            allowed_content_classes=("ordinary",),
            allow_audit_history=False,
        ),
        visibility_policy_version="cera.synthetic_mcp_probe.v1",
        world_mode=EvidenceWorldMode.SYNTHETIC_FIXTURE,
        authority_revision=0,
    )


def run_probe() -> dict[str, object]:
    snapshot = _snapshot()
    reasoner_request_sha256 = domain_sha256(
        "cera.codex_mcp_bridge_probe.request.v1",
        {
            "purpose": "non-story request-bound MCP transport probe",
            "snapshot_binding_sha256": snapshot.binding_sha256,
        },
    )
    tools = SnapshotOnlyEvidenceTools(snapshot)
    bridge = RequestBoundMcpEvidenceBridge(
        tools,
        reasoner_request_sha256=reasoner_request_sha256,
        snapshot=snapshot,
        minimum_tool_calls=1,
    )
    expected_token = str(snapshot.snapshot_token)
    schema = codex_mcp_probe_output_schema()
    with bridge:
        with tempfile.TemporaryDirectory(prefix="cera_codex_mcp_probe_") as directory:
            result = CodexSDKTransport(
                codex_reasoner_candidate(model="gpt-5.6-sol", effort="medium"),
                workspace=Path(directory),
            ).invoke(
                (
                    "This is a synthetic transport integration probe with no story, "
                    "character, Genesis, or adult content. Call "
                    "cera_get_turn_snapshot exactly once. Copy its snapshot_token into "
                    "the result, set status to ok, set tool_calls to 1, and do not call "
                    "any other tool."
                ),
                output_schema=schema,
                mcp_binding=bridge.runtime_binding,
            )
        bridge_receipt = bridge.finalize(result)

    expected_payload = {
        "status": "ok",
        "snapshot_token": expected_token,
        "tool_calls": 1,
    }
    valid = (
        result.parsed_json == expected_payload
        and tools.tool_call_count == 1
        and result.tool_call_count == 1
        and result.failed_tool_call_count == 0
        and len(bridge_receipt.calls) == 1
        and bridge_receipt.authoritative_store_writes == 0
    )
    if not valid:
        raise RuntimeError("Codex MCP bridge probe did not satisfy its exact contract")
    provider_receipt = result.receipt
    return {
        "schema_version": "cera.codex_mcp_bridge_probe.v1",
        "provider": provider_receipt.provider.value,
        "model": provider_receipt.requested_model,
        "payload_valid": True,
        "tool_server_names": result.tool_server_names,
        "tool_names": result.tool_names,
        "tool_call_count": result.tool_call_count,
        "failed_tool_call_count": result.failed_tool_call_count,
        "duration_ms": provider_receipt.duration_ms,
        "input_tokens": provider_receipt.input_tokens,
        "cached_input_tokens": provider_receipt.cached_input_tokens,
        "output_tokens": provider_receipt.output_tokens,
        "reasoning_output_tokens": provider_receipt.reasoning_output_tokens,
        "external_provider_calls": provider_receipt.external_provider_calls,
        "automatic_retry_count": provider_receipt.automatic_retry_count,
        "story_authority_writes": provider_receipt.story_authority_writes,
        "bridge_binding_sha256": bridge_receipt.bridge_binding_sha256,
        "bridge_receipt_sha256": bridge_receipt.receipt_sha256,
        "credential_retained": bridge_receipt.credential_retained,
        "raw_source_retained": bridge_receipt.raw_source_retained,
        "story_prose_retained": bridge_receipt.story_prose_retained,
        "private_evidence_retained": bridge_receipt.private_evidence_retained,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--confirm-live",
        action="store_true",
        help="Required acknowledgement that the probe consumes ChatGPT Codex quota.",
    )
    args = parser.parse_args()
    if not args.confirm_live:
        parser.error("--confirm-live is required")
    print(json.dumps(run_probe(), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
