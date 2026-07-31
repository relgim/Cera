"""Explicit live transport probe; never imported or run by the test suite."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import tempfile

from cera.providers import (
    CodexSDKTransport,
    DeepSeekChatTransport,
    DeepSeekMessage,
    ProviderOutputMode,
    codex_transport_probe_output_schema,
    codex_reasoner_candidate,
    deepseek_composer_candidate,
)


def _codex_probe(model: str) -> dict[str, object]:
    schema = codex_transport_probe_output_schema()
    with tempfile.TemporaryDirectory(prefix="cera_codex_probe_") as directory:
        result = CodexSDKTransport(
            codex_reasoner_candidate(model=model, effort="medium"),
            workspace=Path(directory),
        ).invoke(
            "Return status ok and tools_used 0. Do not use tools.",
            output_schema=schema,
        )
    receipt = result.receipt
    return {
        "provider": receipt.provider.value,
        "model": receipt.requested_model,
        "model_identity_source": receipt.model_identity_source.value,
        "model_identity_verified": receipt.model_identity_verified,
        "payload_valid": result.parsed_json == {"status": "ok", "tools_used": 0},
        "duration_ms": receipt.duration_ms,
        "input_tokens": receipt.input_tokens,
        "cached_input_tokens": receipt.cached_input_tokens,
        "output_tokens": receipt.output_tokens,
        "reasoning_output_tokens": receipt.reasoning_output_tokens,
        "external_provider_calls": receipt.external_provider_calls,
        "automatic_retry_count": receipt.automatic_retry_count,
        "story_authority_writes": receipt.story_authority_writes,
        "quota_metered": receipt.quota_metered,
    }


def _deepseek_probe(model: str, *, thinking_enabled: bool) -> dict[str, object]:
    result = DeepSeekChatTransport(deepseek_composer_candidate(model=model)).invoke(
        (
            DeepSeekMessage(
                "system",
                "This is a transport probe. Return exactly one JSON object with status ok.",
            ),
            DeepSeekMessage("user", "Run the transport probe."),
        ),
        output_mode=ProviderOutputMode.JSON_OBJECT,
        thinking_enabled=thinking_enabled,
    )
    receipt = result.receipt
    return {
        "provider": receipt.provider.value,
        "model": receipt.returned_model,
        "model_identity_source": receipt.model_identity_source.value,
        "model_identity_verified": receipt.model_identity_verified,
        "payload_valid": result.parsed_json == {"status": "ok"},
        "thinking_enabled": thinking_enabled,
        "duration_ms": receipt.duration_ms,
        "input_tokens": receipt.input_tokens,
        "cached_input_tokens": receipt.cached_input_tokens,
        "output_tokens": receipt.output_tokens,
        "reasoning_output_tokens": receipt.reasoning_output_tokens,
        "cost_microusd_estimate": receipt.cost_microusd,
        "external_provider_calls": receipt.external_provider_calls,
        "automatic_retry_count": receipt.automatic_retry_count,
        "story_authority_writes": receipt.story_authority_writes,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", choices=("codex", "deepseek", "all"), required=True)
    parser.add_argument(
        "--confirm-live",
        action="store_true",
        help="Required acknowledgement that the command consumes provider quota/credit.",
    )
    parser.add_argument(
        "--deepseek-model",
        choices=("deepseek-v4-flash", "deepseek-v4-pro"),
        help="Limit a DeepSeek probe to one model.",
    )
    parser.add_argument(
        "--deepseek-thinking",
        action="store_true",
        help="Enable DeepSeek thinking for the DeepSeek probe.",
    )
    args = parser.parse_args()
    if not args.confirm_live:
        parser.error("--confirm-live is required")

    rows: list[dict[str, object]] = []
    if args.provider in {"codex", "all"}:
        for model in ("gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"):
            rows.append(_codex_probe(model))
    if args.provider in {"deepseek", "all"}:
        models = (
            (args.deepseek_model,)
            if args.deepseek_model is not None
            else ("deepseek-v4-flash", "deepseek-v4-pro")
        )
        for model in models:
            rows.append(
                _deepseek_probe(
                    model,
                    thinking_enabled=args.deepseek_thinking,
                )
            )
    print(json.dumps({"schema_version": "cera.provider_transport_probe.v2", "rows": rows}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
