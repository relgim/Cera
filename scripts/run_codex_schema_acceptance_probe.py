"""Run one non-story Sol schema-acceptance probe and retain only safe evidence.

This command is deliberately separate from story qualification.  It submits the
complete authoritative CodexReasonerDraftV6 schema through the versioned OpenAI
projection, asks for a semantically minimal insufficient-evidence draft, and
then decodes the result through the authoritative Python dataclass validator.

The output directory is immutable by convention: this script refuses to write
when the requested target already exists.  It never retains the prompt or raw
provider output.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile

from cera.errors import ContractValidationError
from cera.providers import (
    CodexSDKTransport,
    ProviderSchemaDialect,
    ProviderTransportError,
    codex_reasoner_candidate,
    project_provider_output_schema,
)
from cera.reasoner import (
    CodexReasonerDraftV6,
    codex_reasoner_draft_v6_json_schema,
)
from cera.schema import from_mapping
from cera.serialization import canonical_json, to_primitive


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = (
    ROOT
    / "evaluation"
    / "evidence"
    / "codex_schema_acceptance_2026-07-30_reasoner_v6_v1"
)


def _probe_prompt() -> str:
    return """\
This is a non-story structured-output compatibility probe. No tools are available.
Return one JSON object matching the supplied schema and these exact semantics:
- schema_version: cera.codex_reasoner_draft.v6
- status: insufficient_evidence
- route, scene_intent, floor_owner_id, stop_before, blocker_code, and adult_craft_need: null
- responding_npc_ids, participation, character_moves, source_claims, event_blocks,
  future_segments, writer_must_preserve, uncertainties, prohibited_inferences,
  and development_atoms: empty arrays
- causal_runway, interaction_topology, scene_function, tone, and interiority_level: null
- insufficiencies: an array containing exactly "probe has no story evidence"
- protected_user_source_claims: an empty array
- protected_user_boundary_acknowledged: true
Do not invent decision content. Return only the schema-conforming JSON object.
"""


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
    parser.add_argument(
        "--confirm-live",
        action="store_true",
        help="Required acknowledgement that this makes one quota-metered Codex call.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="New evidence directory; existing targets are never overwritten.",
    )
    args = parser.parse_args()
    if not args.confirm_live:
        parser.error("--confirm-live is required")

    output = args.output.resolve()
    if output.exists():
        parser.error(f"evidence target already exists: {output}")

    authoritative = codex_reasoner_draft_v6_json_schema()
    projection = project_provider_output_schema(
        authoritative,
        ProviderSchemaDialect.OPENAI_STRUCTURED_OUTPUT_V1,
    )
    route = codex_reasoner_candidate(model="gpt-5.6-sol", effort="medium")
    summary: dict[str, object] = {
        "schema_version": "cera.codex_schema_acceptance_probe.v2",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "probe_kind": "non_story_full_reasoner_schema",
        "status": "failed_before_dispatch",
        "provider_schema_accepted": False,
        "authoritative_domain_accepted": False,
        "authoritative_schema_sha256": projection.authoritative_schema_sha256,
        "provider_schema_sha256": projection.provider_schema_sha256,
        "provider_schema_dialect": projection.dialect.value,
        "transformed_one_of_count": projection.transformed_one_of_count,
        "route_sha256": route.route_sha256,
        "provider": route.provider.value,
        "model": route.model_name,
        "reasoning_effort": route.reasoning_effort,
        "external_provider_calls": 0,
        "automatic_retry_count": 0,
        "story_authority_writes": 0,
        "retains_prompt": False,
        "retains_raw_output": False,
        "error_code": None,
        "error_message": None,
    }

    exit_code = 1
    try:
        with tempfile.TemporaryDirectory(prefix="cera_schema_probe_") as directory:
            result = CodexSDKTransport(
                route,
                workspace=Path(directory),
            ).invoke(
                _probe_prompt(),
                output_schema=authoritative,
            )
        receipt = result.receipt
        summary.update(
            {
                "status": "provider_schema_accepted",
                "provider_schema_accepted": True,
                "external_provider_calls": receipt.external_provider_calls,
                "provider_receipt": to_primitive(receipt),
                "typed_output_sha256": receipt.output_sha256,
            }
        )
        try:
            draft = from_mapping(CodexReasonerDraftV6, result.parsed_json or {})
        except ContractValidationError as exc:
            summary.update(
                {
                    "status": "provider_schema_accepted_domain_rejected",
                    "error_code": "CERA_REASONER_CONTRACT_INVALID",
                    "error_message": str(exc),
                }
            )
        else:
            summary.update(
                {
                    "status": "passed",
                    "authoritative_domain_accepted": True,
                    "draft_sha256": draft.draft_sha256,
                }
            )
            exit_code = 0
    except ProviderTransportError as exc:
        summary.update(
            {
                "status": "provider_schema_rejected_or_transport_failed",
                "error_code": exc.code.value,
                "error_message": str(exc),
            }
        )
    except (ContractValidationError, OSError) as exc:
        summary.update(
            {
                "status": "failed_before_dispatch",
                "error_code": type(exc).__name__,
                "error_message": str(exc),
            }
        )

    _write_evidence(output, summary)
    print(canonical_json(summary))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
