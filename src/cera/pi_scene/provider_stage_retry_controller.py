"""Provider-free controller primitives for stage-local retry orchestration."""

from __future__ import annotations

from pathlib import Path

from cera.errors import ContractValidationError

from .provider_stage_retry import (
    ProviderStageBlockReason,
    ProviderStageFailureClass,
    ProviderStageRecoveryAction,
    ProviderStageRetryChainV1,
    ProviderStageRetryIdentityV1,
    ProviderStageRetryPhase,
    ProviderStageRetryRecoveryV1,
    ProviderStageRetryTerminalV1,
)
from .provider_stage_retry_store import ProviderStageRetryStoreV1


class ProviderStageRetryControllerV1:
    """Deterministic authority for one initial attempt plus at most two retries.

    This controller never calls a provider.  Runtime owners must supply exact
    stage input/result bytes, safe hashes, attempt telemetry, and proof that a
    failed owner was retired before requesting another fresh attempt.
    """

    def __init__(
        self,
        root: Path | None = None,
        *,
        store: ProviderStageRetryStoreV1 | None = None,
    ) -> None:
        if (root is None) == (store is None):
            raise ContractValidationError("provide exactly one provider-stage retry root or store")
        if store is None:
            assert root is not None
            self.store = ProviderStageRetryStoreV1(root)
        else:
            self.store = store

    def begin(
        self,
        identity: ProviderStageRetryIdentityV1,
        exact_input: bytes,
    ) -> ProviderStageRetryChainV1:
        return self.store.begin(identity, exact_input)

    def prepare_attempt(
        self,
        chain_id: str,
        *,
        session_scope_sha256: str,
        ledger_prefix_before_sha256: str,
    ) -> ProviderStageRetryChainV1:
        return self.store.prepare_attempt(
            chain_id,
            session_scope_sha256=session_scope_sha256,
            ledger_prefix_before_sha256=ledger_prefix_before_sha256,
        )

    def mark_dispatch_started(
        self,
        chain_id: str,
        *,
        attempt_number: int,
        dispatch_evidence_sha256: str,
    ) -> ProviderStageRetryChainV1:
        return self.store.mark_dispatch_started(
            chain_id,
            attempt_number=attempt_number,
            dispatch_evidence_sha256=dispatch_evidence_sha256,
        )

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
    ) -> ProviderStageRetryChainV1:
        return self.store.mark_attempt_failed(
            chain_id,
            attempt_number=attempt_number,
            failure_class=failure_class,
            failure_evidence_sha256=failure_evidence_sha256,
            ledger_prefix_after_sha256=ledger_prefix_after_sha256,
            provider_operations_observed=provider_operations_observed,
            provider_operations_conservative=provider_operations_conservative,
            duration_ms=duration_ms,
            input_tokens=input_tokens,
            cached_input_tokens=cached_input_tokens,
            output_tokens=output_tokens,
            reasoning_tokens=reasoning_tokens,
        )

    def mark_owner_retired(
        self,
        chain_id: str,
        *,
        attempt_number: int,
        retirement_evidence_sha256: str,
    ) -> ProviderStageRetryChainV1:
        return self.store.mark_owner_retired(
            chain_id,
            attempt_number=attempt_number,
            retirement_evidence_sha256=retirement_evidence_sha256,
        )

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
    ) -> ProviderStageRetryChainV1:
        return self.store.freeze_result(
            chain_id,
            attempt_number=attempt_number,
            exact_result=exact_result,
            result_evidence_sha256=result_evidence_sha256,
            ledger_prefix_after_sha256=ledger_prefix_after_sha256,
            provider_operations_observed=provider_operations_observed,
            provider_operations_conservative=provider_operations_conservative,
            duration_ms=duration_ms,
            input_tokens=input_tokens,
            cached_input_tokens=cached_input_tokens,
            output_tokens=output_tokens,
            reasoning_tokens=reasoning_tokens,
        )

    def bind_downstream(
        self,
        chain_id: str,
        *,
        downstream_evidence_sha256: str,
    ) -> ProviderStageRetryChainV1:
        return self.store.bind_downstream(
            chain_id,
            downstream_evidence_sha256=downstream_evidence_sha256,
        )

    def freeze_downstream_intent(
        self,
        chain_id: str,
        *,
        downstream_intent_sha256: str,
    ) -> ProviderStageRetryChainV1:
        return self.store.freeze_downstream_intent(
            chain_id,
            downstream_intent_sha256=downstream_intent_sha256,
        )

    def mark_succeeded(self, chain_id: str) -> ProviderStageRetryChainV1:
        return self.store.mark_succeeded(chain_id)

    def block_ambiguous(
        self,
        chain_id: str,
        *,
        reason: ProviderStageBlockReason,
        evidence_sha256: str,
    ) -> ProviderStageRetryChainV1:
        return self.store.block_ambiguous(
            chain_id,
            reason=reason,
            evidence_sha256=evidence_sha256,
        )

    def recover(self, chain_id: str) -> ProviderStageRetryRecoveryV1:
        """Reconcile durable result/terminal artifacts and name the next safe action."""

        chain = self.store.read(chain_id)
        attempt_number: int | None
        if chain.phase in {
            ProviderStageRetryPhase.INPUT_FROZEN,
            ProviderStageRetryPhase.OWNER_RETIRED,
        }:
            action = ProviderStageRecoveryAction.PREPARE_ATTEMPT
            attempt_number = len(chain.attempts) + 1
        elif chain.phase is ProviderStageRetryPhase.ATTEMPT_PREPARED:
            action = ProviderStageRecoveryAction.DISPATCH_PREPARED_ATTEMPT
            attempt_number = chain.attempts[-1].attempt_number
        elif chain.phase is ProviderStageRetryPhase.DISPATCH_STARTED:
            action = ProviderStageRecoveryAction.RESOLVE_AMBIGUOUS_DISPATCH
            attempt_number = chain.attempts[-1].attempt_number
        elif chain.phase is ProviderStageRetryPhase.AWAITING_OWNER_RETIREMENT:
            action = ProviderStageRecoveryAction.RETIRE_FAILED_OWNER
            attempt_number = chain.attempts[-1].attempt_number
        elif chain.phase is ProviderStageRetryPhase.RESULT_FROZEN:
            action = ProviderStageRecoveryAction.FREEZE_DOWNSTREAM_INTENT
            attempt_number = chain.attempts[-1].attempt_number
        elif chain.phase is ProviderStageRetryPhase.DOWNSTREAM_INTENT_FROZEN:
            action = ProviderStageRecoveryAction.RECONCILE_DOWNSTREAM
            attempt_number = chain.attempts[-1].attempt_number
        elif chain.phase is ProviderStageRetryPhase.DOWNSTREAM_BOUND:
            action = ProviderStageRecoveryAction.FINALIZE_SUCCESS
            attempt_number = chain.attempts[-1].attempt_number
        elif chain.phase is ProviderStageRetryPhase.SUCCEEDED:
            action = ProviderStageRecoveryAction.REPLAY_SUCCESS
            attempt_number = chain.attempts[-1].attempt_number
        elif chain.phase is ProviderStageRetryPhase.EXHAUSTED:
            action = ProviderStageRecoveryAction.REPORT_EXHAUSTED
            attempt_number = chain.attempts[-1].attempt_number
        else:
            action = ProviderStageRecoveryAction.REPORT_BLOCKED_AMBIGUOUS
            attempt_number = None if not chain.attempts else chain.attempts[-1].attempt_number
        return ProviderStageRetryRecoveryV1(
            schema_version=ProviderStageRetryRecoveryV1.SCHEMA_VERSION,
            chain_id=chain.chain_id,
            phase=chain.phase,
            action=action,
            attempt_number=attempt_number,
            attempts_remaining=chain.attempts_remaining,
            chain_sha256=chain.chain_sha256,
        )

    def status(self, chain_id: str) -> ProviderStageRetryChainV1:
        return self.store.read(chain_id)

    def terminal(self, chain_id: str) -> ProviderStageRetryTerminalV1 | None:
        return self.store.terminal(chain_id)

    def load_protected_input(self, chain_id: str) -> bytes:
        return self.store.load_input(chain_id)

    def load_protected_result(self, chain_id: str) -> bytes:
        return self.store.load_result(chain_id)
