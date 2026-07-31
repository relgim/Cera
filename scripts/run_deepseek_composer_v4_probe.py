"""Run one non-story DeepSeek v4 JSON/typed-decoder canary."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

from cera.composer import DeepSeekCompositionDraftV4
from cera.composer.deepseek import (
    DEEPSEEK_COMPOSER_PROMPT_VERSION,
    deepseek_composition_draft_v4_json_schema,
)
from cera.providers import (
    DeepSeekChatTransport,
    DeepSeekMessage,
    ProviderOutputMode,
    ProviderSchemaDialect,
    deepseek_composer_candidate,
    project_provider_output_schema,
)
from cera.schema import from_mapping
from cera.serialization import canonical_json, domain_sha256, to_primitive


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = (
    ROOT
    / "evaluation"
    / "evidence"
    / "deepseek_composer_v4_probe_2026-07-29_v1"
)


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

    schema = deepseek_composition_draft_v4_json_schema()
    projection = project_provider_output_schema(
        schema,
        ProviderSchemaDialect.DEEPSEEK_JSON_OBJECT_PROMPT_V1,
    )
    route = deepseek_composer_candidate(model="deepseek-v4-pro")
    summary: dict[str, object] = {
        "schema_version": "cera.deepseek_composer_v4_probe.v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "failed_before_dispatch",
        "prompt_version": DEEPSEEK_COMPOSER_PROMPT_VERSION,
        "authoritative_schema_sha256": projection.authoritative_schema_sha256,
        "provider_schema_sha256": projection.provider_schema_sha256,
        "external_provider_calls": 0,
        "automatic_retry_count": 0,
        "story_authority_writes": 0,
        "retains_prompt": False,
        "retains_raw_output": False,
        "retains_story_prose": False,
        "error_code": None,
        "error_message": None,
    }
    exit_code = 1
    try:
        expected = {
            "schema_version": "cera.deepseek_composition_draft.v4",
            "story_segments": [
                {"segment_key": "segment_1", "text": "The synthetic guide nods."}
            ],
            "source_coverage": [
                {
                    "source_unit_id": "source_unit:probe",
                    "segment_keys": ["segment_1"],
                }
            ],
            "realization_segments": [
                {
                    "owner_id": "character:guide",
                    "kind": "action",
                    "segment_key": "segment_1",
                    "source_unit_id": None,
                    "beat_id": "beat:probe",
                }
            ],
            "specificity_coverage": [],
            "terminal_segment_key": "segment_1",
        }
        result = DeepSeekChatTransport(route).invoke(
            (
                DeepSeekMessage(
                    "system",
                    "Non-story CERA JSON compatibility canary. Return JSON only.",
                ),
                DeepSeekMessage(
                    "user",
                    "Return exactly this synthetic object, with no extra fields: "
                    + canonical_json(expected)
                    + "\nPortable schema: "
                    + canonical_json(projection.provider_schema),
                ),
            ),
            output_mode=ProviderOutputMode.JSON_OBJECT,
            thinking_enabled=False,
        )
        draft = from_mapping(DeepSeekCompositionDraftV4, result.parsed_json or {})
        if (
            len(draft.story_segments) != 1
            or draft.terminal_segment_key != "segment_1"
            or draft.source_coverage[0].segment_keys != ("segment_1",)
        ):
            raise RuntimeError("DeepSeek v4 canary changed the fixed contract")
        summary.update(
            {
                "status": "passed",
                "external_provider_calls": result.receipt.external_provider_calls,
                "provider_receipt": to_primitive(result.receipt),
                "draft_sha256": domain_sha256(
                    "cera.deepseek_composition_draft.v4", draft
                ),
                "segment_count": len(draft.story_segments),
                "source_obligation_count": len(draft.source_coverage),
                "specificity_obligation_count": len(draft.specificity_coverage),
            }
        )
        exit_code = 0
    except Exception as exc:
        receipt = getattr(exc, "provider_call_receipt", None)
        code = getattr(exc, "code", None)
        summary.update(
            {
                "status": "failed",
                "error_code": (
                    code.value
                    if code is not None and hasattr(code, "value")
                    else type(exc).__name__
                ),
                "error_message": str(exc),
                "external_provider_calls": (
                    receipt.external_provider_calls if receipt is not None else 0
                ),
                "provider_receipt": (
                    to_primitive(receipt) if receipt is not None else None
                ),
            }
        )
    _write_evidence(output, summary)
    print(canonical_json(summary))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
