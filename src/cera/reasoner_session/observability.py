"""Projection of existing provider receipts into session-aware telemetry."""

from __future__ import annotations

from cera.errors import ContractValidationError
from cera.evaluation import EvaluationRole
from cera.ids import IdKind, deterministic_id
from cera.providers.models import LiveProviderCallReceipt, ProviderName
from cera.serialization import text_sha256

from .models import (
    CompactionState,
    ReasonerSessionCheckpoint,
    ReasonerSessionLedger,
    SessionUsageReceiptV2,
)


def project_session_usage_receipt(
    *,
    ledger: ReasonerSessionLedger,
    checkpoint: ReasonerSessionCheckpoint,
    provider_receipt: LiveProviderCallReceipt,
    prompt_bytes: int,
    packet_bytes: int,
    schema_bytes: int,
    tool_definition_bytes: int,
    wall_microseconds: int | None,
    created_at: str,
    queue_microseconds: int | None = None,
    ttft_microseconds: int | None = None,
    cache_write_tokens: int | None = None,
    compaction_state: CompactionState = CompactionState.UNKNOWN,
) -> SessionUsageReceiptV2:
    """Retain exact supported metrics and label unavailable fields explicitly."""

    if (
        provider_receipt.provider is not ProviderName.OPENAI_CODEX
        or provider_receipt.role is not EvaluationRole.SCENE_REASONER
    ):
        raise ContractValidationError(
            "Reasoner session usage requires a Codex Scene Reasoner receipt"
        )
    if (
        checkpoint.session_id != ledger.session_id
        or checkpoint.branch_id != ledger.compatibility.branch_id
        or provider_receipt.requested_model != ledger.compatibility.model
    ):
        raise ContractValidationError("provider usage changed session identity")
    unsupported: list[str] = []
    for name, value in (
        ("queue_microseconds", queue_microseconds),
        ("ttft_microseconds", ttft_microseconds),
        ("cache_write_tokens", cache_write_tokens),
    ):
        if value is None:
            unsupported.append(name)
    return SessionUsageReceiptV2(
        schema_version=SessionUsageReceiptV2.SCHEMA_VERSION,
        receipt_id=deterministic_id(
            IdKind.TELEMETRY_EVENT,
            SessionUsageReceiptV2.SCHEMA_VERSION,
            (
                f"{ledger.session_id}|{checkpoint.checkpoint_id}|"
                f"{provider_receipt.provider_receipt_id}"
            ),
        ),
        session_id=ledger.session_id,
        checkpoint_id=checkpoint.checkpoint_id,
        request_id=checkpoint.request_id,
        branch_id=checkpoint.branch_id,
        provider=provider_receipt.provider.value,
        model=provider_receipt.requested_model,
        reasoning_effort=ledger.compatibility.reasoning_effort,
        compatibility_sha256=ledger.compatibility.compatibility_sha256,
        prompt_version=ledger.compatibility.prompt_version,
        provider_schema_sha256=ledger.compatibility.provider_schema_sha256,
        base_instruction_sha256=ledger.compatibility.base_instruction_sha256,
        provider_thread_id_sha256=text_sha256(
            checkpoint.provider_handle.provider_thread_id
        ),
        wall_microseconds=wall_microseconds,
        queue_microseconds=queue_microseconds,
        provider_microseconds=provider_receipt.duration_ms * 1_000,
        ttft_microseconds=ttft_microseconds,
        input_tokens=provider_receipt.input_tokens,
        cached_input_tokens=provider_receipt.cached_input_tokens,
        cache_write_tokens=cache_write_tokens,
        uncached_input_tokens=(
            provider_receipt.input_tokens - provider_receipt.cached_input_tokens
        ),
        output_tokens=provider_receipt.output_tokens,
        reasoning_tokens=provider_receipt.reasoning_output_tokens,
        prompt_bytes=prompt_bytes,
        packet_bytes=packet_bytes,
        schema_bytes=schema_bytes,
        tool_definition_bytes=tool_definition_bytes,
        session_age_turns=ledger.accumulated_turns,
        accumulated_turns=ledger.accumulated_turns,
        compaction_state=compaction_state,
        unsupported_fields=tuple(unsupported),
        raw_prompt_retained=False,
        raw_output_retained=False,
        created_at=created_at,
    )
