"""Provider-neutral persistence port for stage-local Retry authority."""

from __future__ import annotations

from typing import Protocol

from .provider_stage_retry import (
    ProviderStageBlockReason,
    ProviderStageFailureClass,
    ProviderStageProtectedCheckpointV1,
    ProviderStageRetryChainV1,
    ProviderStageRetryIdentityV1,
    ProviderStageRetryTerminalV1,
)


class ProviderStageRetryStorePort(Protocol):
    """Persistence-only API used by a generic provider-stage executor.

    Implementations never dispatch providers.  A runtime must commit an
    invocation reservation before transport and must supply durable owner
    retirement evidence before a manual Retry action can be accepted.
    """

    def freeze_input(
        self,
        identity: ProviderStageRetryIdentityV1,
        exact_input: bytes,
    ) -> ProviderStageProtectedCheckpointV1: ...

    def begin(
        self,
        identity: ProviderStageRetryIdentityV1,
        exact_input: bytes,
    ) -> ProviderStageRetryChainV1: ...

    def read(self, chain_id: str) -> ProviderStageRetryChainV1: ...

    def load_input(self, chain_id: str) -> bytes: ...

    def prepare_attempt(
        self,
        chain_id: str,
        *,
        session_scope_sha256: str,
        ledger_prefix_before_sha256: str,
    ) -> ProviderStageRetryChainV1: ...

    def accept_retry(
        self,
        chain_id: str,
        *,
        retry_action_sha256: str,
        session_scope_sha256: str,
        ledger_prefix_before_sha256: str,
    ) -> ProviderStageRetryChainV1: ...

    def retry_actions_accepted(self, chain_id: str) -> int: ...

    def mark_dispatch_started(
        self,
        chain_id: str,
        *,
        attempt_number: int,
        dispatch_evidence_sha256: str,
    ) -> ProviderStageRetryChainV1: ...

    def mark_pretransport_failed(
        self,
        chain_id: str,
        *,
        attempt_number: int,
        failure_class: ProviderStageFailureClass,
        failure_evidence_sha256: str,
        ledger_prefix_after_sha256: str,
        duration_ms: int,
    ) -> ProviderStageRetryChainV1: ...

    def mark_attempt_failed(
        self,
        chain_id: str,
        *,
        attempt_number: int,
        failure_class: ProviderStageFailureClass,
        failure_evidence_sha256: str,
        ledger_prefix_after_sha256: str,
        provider_operations_observed: int,
        provider_operations_conservative: int,
        duration_ms: int,
        input_tokens: int | None = None,
        cached_input_tokens: int | None = None,
        output_tokens: int | None = None,
        reasoning_tokens: int | None = None,
    ) -> ProviderStageRetryChainV1: ...

    def mark_owner_retired(
        self,
        chain_id: str,
        *,
        attempt_number: int,
        retirement_evidence_sha256: str,
    ) -> ProviderStageRetryChainV1: ...

    def freeze_result(
        self,
        chain_id: str,
        *,
        attempt_number: int,
        exact_result: bytes,
        result_evidence_sha256: str,
        ledger_prefix_after_sha256: str,
        provider_operations_observed: int,
        provider_operations_conservative: int,
        duration_ms: int,
        input_tokens: int | None = None,
        cached_input_tokens: int | None = None,
        output_tokens: int | None = None,
        reasoning_tokens: int | None = None,
    ) -> ProviderStageRetryChainV1: ...

    def freeze_downstream_intent(
        self,
        chain_id: str,
        *,
        downstream_intent_sha256: str,
    ) -> ProviderStageRetryChainV1: ...

    def bind_downstream(
        self,
        chain_id: str,
        *,
        downstream_evidence_sha256: str,
    ) -> ProviderStageRetryChainV1: ...

    def mark_succeeded(self, chain_id: str) -> ProviderStageRetryChainV1: ...

    def block_ambiguous(
        self,
        chain_id: str,
        *,
        reason: ProviderStageBlockReason,
        evidence_sha256: str,
    ) -> ProviderStageRetryChainV1: ...

    def resolve_blocked_failure(
        self,
        chain_id: str,
        *,
        resolution_evidence_sha256: str,
        failure_class: ProviderStageFailureClass,
        failure_evidence_sha256: str,
        ledger_prefix_after_sha256: str,
        provider_operations_observed: int,
        provider_operations_conservative: int,
        duration_ms: int,
        input_tokens: int | None = None,
        cached_input_tokens: int | None = None,
        output_tokens: int | None = None,
        reasoning_tokens: int | None = None,
    ) -> ProviderStageRetryChainV1: ...

    def resolve_blocked_result(
        self,
        chain_id: str,
        *,
        resolution_evidence_sha256: str,
        exact_result: bytes,
        result_evidence_sha256: str,
        ledger_prefix_after_sha256: str,
        provider_operations_observed: int,
        provider_operations_conservative: int,
        duration_ms: int,
        input_tokens: int | None = None,
        cached_input_tokens: int | None = None,
        output_tokens: int | None = None,
        reasoning_tokens: int | None = None,
    ) -> ProviderStageRetryChainV1: ...

    def resolve_blocked_recording_repair(
        self,
        chain_id: str,
        *,
        resolution_evidence_sha256: str,
        failure_class: ProviderStageFailureClass,
        failure_evidence_sha256: str,
        ledger_prefix_after_sha256: str,
        provider_operations_observed: int,
        provider_operations_conservative: int,
        duration_ms: int,
        input_tokens: int | None = None,
        cached_input_tokens: int | None = None,
        output_tokens: int | None = None,
        reasoning_tokens: int | None = None,
    ) -> ProviderStageRetryChainV1: ...

    def load_result(self, chain_id: str) -> bytes: ...

    def terminal(self, chain_id: str) -> ProviderStageRetryTerminalV1 | None: ...
