"""Crash-safe protected storage for bounded provider-stage retry chains."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from threading import RLock
from typing import Any
from uuid import uuid4

from cera.errors import ContractValidationError, StateConflictError
from cera.serialization import bytes_sha256, canonical_bytes, canonical_sha256, domain_sha256

from .provider_stage_retry import (
    MAXIMUM_PROVIDER_STAGE_ATTEMPTS,
    ProviderStageAttemptPhase,
    ProviderStageAttemptV1,
    ProviderStageBlockReason,
    ProviderStageCheckpointKind,
    ProviderStageFailureClass,
    ProviderStageProtectedCheckpointV1,
    ProviderStageRetryBlockedV1,
    ProviderStageRetryChainV1,
    ProviderStageRetryExhaustedV1,
    ProviderStageRetryIdentityV1,
    ProviderStageRetryPhase,
    ProviderStageRetryTerminalV1,
    provider_stage_chain_from_payload,
    provider_stage_checkpoint_from_payload,
    provider_stage_terminal_from_payload,
)

_STATE_SCHEMA = "cera.provider_stage_retry_state.v1"
_STAGED_RESULT_SCHEMA = "cera.provider_stage_staged_result.v1"


class ProviderStageRetryStoreV1:
    """Exact byte custody plus privacy-safe, atomically replaced state.

    One crash-released OS claim protects each logical chain.  A compact
    128-bit directory locator avoids repeating the complete chain digest in
    every Windows path; every artifact still carries and validates the full
    chain ID, so a locator collision fails closed.
    """

    def __init__(self, root: Path) -> None:
        if not root.is_absolute():
            raise ContractValidationError("provider-stage retry root must be absolute")
        self.root = root.resolve()
        self.chains_root = self.root / "CHAINS"
        self.claims_root = self.root / "CLAIMS"
        self.chains_root.mkdir(parents=True, exist_ok=True)
        self.claims_root.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()

    def freeze_input(
        self,
        identity: ProviderStageRetryIdentityV1,
        exact_input: bytes,
    ) -> ProviderStageProtectedCheckpointV1:
        """Freeze exact protected input before any safe chain state is published."""

        self._validate_exact_input(identity, exact_input)
        with self._lock, self._claim(identity.chain_id):
            return self._freeze_input_locked(identity, exact_input)

    def begin(
        self,
        identity: ProviderStageRetryIdentityV1,
        exact_input: bytes,
    ) -> ProviderStageRetryChainV1:
        """Idempotently publish or recover an input-frozen logical chain."""

        self._validate_exact_input(identity, exact_input)
        with self._lock, self._claim(identity.chain_id):
            checkpoint = self._freeze_input_locked(identity, exact_input)
            existing = self._read_chain_locked(identity.chain_id, required=False)
            if existing is not None:
                if existing.identity != identity:
                    raise StateConflictError("provider-stage retry identity changed")
                self._require_input_locked(identity.chain_id, checkpoint)
                return self._reconcile_locked(existing)
            chain = ProviderStageRetryChainV1(
                schema_version=ProviderStageRetryChainV1.SCHEMA_VERSION,
                identity=identity,
                phase=ProviderStageRetryPhase.INPUT_FROZEN,
                attempts=(),
                result_checkpoint=None,
                downstream_evidence_sha256=None,
                block_reason=None,
                block_evidence_sha256=None,
            )
            self._write_state_locked(chain, checkpoint)
            return chain

    def read(self, chain_id: str) -> ProviderStageRetryChainV1:
        """Return the safe chain projection after deterministic reconciliation."""

        with self._lock, self._claim(chain_id):
            chain = self._require_chain_locked(chain_id)
            return self._reconcile_locked(chain)

    def load_input(self, chain_id: str) -> bytes:
        """Read exact protected input; callers must not project these bytes."""

        with self._lock, self._claim(chain_id):
            chain = self._require_chain_locked(chain_id)
            checkpoint = self._read_input_checkpoint_locked(chain_id)
            self._require_input_locked(chain_id, checkpoint)
            if checkpoint.content_sha256 != chain.identity.stage_input_sha256:
                raise StateConflictError("provider-stage protected input identity changed")
            return self._input_path(chain_id).read_bytes()

    def prepare_attempt(
        self,
        chain_id: str,
        *,
        session_scope_sha256: str,
        ledger_prefix_before_sha256: str,
    ) -> ProviderStageRetryChainV1:
        with self._lock, self._claim(chain_id):
            chain = self._reconcile_locked(self._require_chain_locked(chain_id))
            if chain.phase is ProviderStageRetryPhase.ATTEMPT_PREPARED:
                current = chain.attempts[-1]
                if (
                    current.session_scope_sha256 == session_scope_sha256
                    and current.ledger_prefix_before_sha256 == ledger_prefix_before_sha256
                ):
                    return chain
                raise StateConflictError("provider-stage prepared attempt identity changed")
            if chain.phase is ProviderStageRetryPhase.INPUT_FROZEN:
                attempt_number = 1
            elif chain.phase is ProviderStageRetryPhase.OWNER_RETIRED:
                attempt_number = len(chain.attempts) + 1
                if chain.attempts[-1].ledger_prefix_after_sha256 != ledger_prefix_before_sha256:
                    raise StateConflictError("provider-stage retry ledger prefix changed")
            else:
                raise StateConflictError("provider-stage chain cannot prepare another attempt")
            if attempt_number > MAXIMUM_PROVIDER_STAGE_ATTEMPTS:
                raise StateConflictError("provider-stage retry attempt ceiling is exhausted")
            attempt = ProviderStageAttemptV1(
                schema_version=ProviderStageAttemptV1.SCHEMA_VERSION,
                attempt_number=attempt_number,
                phase=ProviderStageAttemptPhase.PREPARED,
                session_scope_sha256=session_scope_sha256,
                ledger_prefix_before_sha256=ledger_prefix_before_sha256,
                dispatch_evidence_sha256=None,
                ledger_prefix_after_sha256=None,
                provider_operations_observed=0,
                provider_operations_conservative=0,
                duration_ms=None,
                input_tokens=None,
                cached_input_tokens=None,
                output_tokens=None,
                reasoning_tokens=None,
                failure_class=None,
                failure_evidence_sha256=None,
                owner_retirement_evidence_sha256=None,
                result_checkpoint_sha256=None,
            )
            updated = replace(
                chain,
                phase=ProviderStageRetryPhase.ATTEMPT_PREPARED,
                attempts=(*chain.attempts, attempt),
            )
            self._write_state_locked(updated, self._read_input_checkpoint_locked(chain_id))
            return updated

    def mark_dispatch_started(
        self,
        chain_id: str,
        *,
        attempt_number: int,
        dispatch_evidence_sha256: str,
    ) -> ProviderStageRetryChainV1:
        with self._lock, self._claim(chain_id):
            chain = self._reconcile_locked(self._require_chain_locked(chain_id))
            current = self._require_current_attempt(chain, attempt_number)
            if chain.phase is ProviderStageRetryPhase.DISPATCH_STARTED:
                if current.dispatch_evidence_sha256 == dispatch_evidence_sha256:
                    return chain
                raise StateConflictError("provider-stage dispatch evidence changed")
            if chain.phase is not ProviderStageRetryPhase.ATTEMPT_PREPARED:
                raise StateConflictError("provider-stage attempt is not prepared")
            updated_attempt = replace(
                current,
                phase=ProviderStageAttemptPhase.DISPATCH_STARTED,
                dispatch_evidence_sha256=dispatch_evidence_sha256,
            )
            updated = replace(
                chain,
                phase=ProviderStageRetryPhase.DISPATCH_STARTED,
                attempts=(*chain.attempts[:-1], updated_attempt),
            )
            self._write_state_locked(updated, self._read_input_checkpoint_locked(chain_id))
            return updated

    def stage_result(
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
    ) -> ProviderStageProtectedCheckpointV1:
        """Write exact result bytes and their complete binding before state mutation."""

        if not isinstance(exact_result, bytes) or not exact_result:
            raise ContractValidationError("provider-stage exact result must be non-empty bytes")
        with self._lock, self._claim(chain_id):
            chain = self._require_chain_locked(chain_id)
            current = self._require_current_attempt(chain, attempt_number)
            if chain.phase not in {
                ProviderStageRetryPhase.DISPATCH_STARTED,
                ProviderStageRetryPhase.RESULT_FROZEN,
                ProviderStageRetryPhase.DOWNSTREAM_BOUND,
                ProviderStageRetryPhase.SUCCEEDED,
            }:
                raise StateConflictError("provider-stage result has no dispatched attempt")
            if current.phase is ProviderStageAttemptPhase.RESULT_FROZEN:
                staged = self._read_staged_result_locked(chain_id, attempt_number, required=True)
                assert staged is not None
                self._require_staged_arguments(
                    staged,
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
                recovered_checkpoint = staged["checkpoint"]
                assert isinstance(
                    recovered_checkpoint,
                    ProviderStageProtectedCheckpointV1,
                )
                return recovered_checkpoint
            checkpoint = ProviderStageProtectedCheckpointV1.create(
                checkpoint_kind=ProviderStageCheckpointKind.RESULT,
                chain_id=chain_id,
                attempt_number=attempt_number,
                content_sha256=bytes_sha256(exact_result),
                size_bytes=len(exact_result),
                evidence_sha256=result_evidence_sha256,
            )
            # Validate every safe metric before any immutable result artifact
            # is published.  A bad caller value must not poison retry custody.
            ProviderStageAttemptV1(
                schema_version=ProviderStageAttemptV1.SCHEMA_VERSION,
                attempt_number=current.attempt_number,
                phase=ProviderStageAttemptPhase.RESULT_FROZEN,
                session_scope_sha256=current.session_scope_sha256,
                ledger_prefix_before_sha256=current.ledger_prefix_before_sha256,
                dispatch_evidence_sha256=current.dispatch_evidence_sha256,
                ledger_prefix_after_sha256=ledger_prefix_after_sha256,
                provider_operations_observed=provider_operations_observed,
                provider_operations_conservative=provider_operations_conservative,
                duration_ms=duration_ms,
                input_tokens=input_tokens,
                cached_input_tokens=cached_input_tokens,
                output_tokens=output_tokens,
                reasoning_tokens=reasoning_tokens,
                failure_class=None,
                failure_evidence_sha256=None,
                owner_retirement_evidence_sha256=None,
                result_checkpoint_sha256=checkpoint.checkpoint_sha256,
            )
            body = {
                "schema_version": _STAGED_RESULT_SCHEMA,
                "checkpoint": checkpoint.to_payload(),
                "ledger_prefix_after_sha256": ledger_prefix_after_sha256,
                "provider_operations_observed": provider_operations_observed,
                "provider_operations_conservative": provider_operations_conservative,
                "duration_ms": duration_ms,
                "input_tokens": input_tokens,
                "cached_input_tokens": cached_input_tokens,
                "output_tokens": output_tokens,
                "reasoning_tokens": reasoning_tokens,
            }
            staged_payload = {**body, "staged_sha256": canonical_sha256(body)}
            result_path = self._result_path(chain_id, attempt_number)
            staged_path = self._staged_result_path(chain_id, attempt_number)
            _write_immutable_bytes(result_path, exact_result, artifact="provider-stage result")
            _write_immutable_json(
                staged_path,
                staged_payload,
                artifact="provider-stage result checkpoint",
            )
            staged = self._read_staged_result_locked(chain_id, attempt_number, required=True)
            assert staged is not None
            recovered_checkpoint = staged["checkpoint"]
            assert isinstance(recovered_checkpoint, ProviderStageProtectedCheckpointV1)
            return recovered_checkpoint

    def commit_staged_result(
        self,
        chain_id: str,
        *,
        attempt_number: int,
    ) -> ProviderStageRetryChainV1:
        """Bind an already durable exact result into safe chain state."""

        with self._lock, self._claim(chain_id):
            chain = self._require_chain_locked(chain_id)
            return self._commit_staged_result_locked(chain, attempt_number)

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
        """Result-first convenience operation; either half is restart-safe."""

        self.stage_result(
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
        return self.commit_staged_result(chain_id, attempt_number=attempt_number)

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
        with self._lock, self._claim(chain_id):
            chain = self._reconcile_locked(self._require_chain_locked(chain_id))
            current = self._require_current_attempt(chain, attempt_number)
            if current.phase in {
                ProviderStageAttemptPhase.FAILED,
                ProviderStageAttemptPhase.OWNER_RETIRED,
            }:
                self._require_failure_arguments(
                    current,
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
                return chain
            if chain.phase is not ProviderStageRetryPhase.DISPATCH_STARTED:
                raise StateConflictError("provider-stage failure has no active dispatch")
            updated_attempt = replace(
                current,
                phase=ProviderStageAttemptPhase.FAILED,
                ledger_prefix_after_sha256=ledger_prefix_after_sha256,
                provider_operations_observed=provider_operations_observed,
                provider_operations_conservative=provider_operations_conservative,
                duration_ms=duration_ms,
                input_tokens=input_tokens,
                cached_input_tokens=cached_input_tokens,
                output_tokens=output_tokens,
                reasoning_tokens=reasoning_tokens,
                failure_class=failure_class,
                failure_evidence_sha256=failure_evidence_sha256,
            )
            phase = (
                ProviderStageRetryPhase.EXHAUSTED
                if attempt_number == MAXIMUM_PROVIDER_STAGE_ATTEMPTS
                else ProviderStageRetryPhase.ATTEMPT_FAILED_RETRYABLE
            )
            updated = replace(
                chain,
                phase=phase,
                attempts=(*chain.attempts[:-1], updated_attempt),
            )
            self._write_state_locked(updated, self._read_input_checkpoint_locked(chain_id))
            self._ensure_terminal_locked(updated)
            return updated

    def mark_owner_retired(
        self,
        chain_id: str,
        *,
        attempt_number: int,
        retirement_evidence_sha256: str,
    ) -> ProviderStageRetryChainV1:
        with self._lock, self._claim(chain_id):
            chain = self._reconcile_locked(self._require_chain_locked(chain_id))
            current = self._require_current_attempt(chain, attempt_number)
            if chain.phase is ProviderStageRetryPhase.OWNER_RETIRED:
                if current.owner_retirement_evidence_sha256 == retirement_evidence_sha256:
                    return chain
                raise StateConflictError("provider-stage owner-retirement evidence changed")
            if chain.phase is not ProviderStageRetryPhase.ATTEMPT_FAILED_RETRYABLE:
                raise StateConflictError("provider-stage owner is not awaiting retirement")
            updated_attempt = replace(
                current,
                phase=ProviderStageAttemptPhase.OWNER_RETIRED,
                owner_retirement_evidence_sha256=retirement_evidence_sha256,
            )
            updated = replace(
                chain,
                phase=ProviderStageRetryPhase.OWNER_RETIRED,
                attempts=(*chain.attempts[:-1], updated_attempt),
            )
            self._write_state_locked(updated, self._read_input_checkpoint_locked(chain_id))
            return updated

    def bind_downstream(
        self,
        chain_id: str,
        *,
        downstream_evidence_sha256: str,
    ) -> ProviderStageRetryChainV1:
        with self._lock, self._claim(chain_id):
            chain = self._reconcile_locked(self._require_chain_locked(chain_id))
            if chain.phase in {
                ProviderStageRetryPhase.DOWNSTREAM_BOUND,
                ProviderStageRetryPhase.SUCCEEDED,
            }:
                if chain.downstream_evidence_sha256 == downstream_evidence_sha256:
                    return chain
                raise StateConflictError("provider-stage downstream evidence changed")
            if chain.phase is not ProviderStageRetryPhase.RESULT_FROZEN:
                raise StateConflictError("provider-stage result is not ready for downstream work")
            updated = replace(
                chain,
                phase=ProviderStageRetryPhase.DOWNSTREAM_BOUND,
                downstream_evidence_sha256=downstream_evidence_sha256,
            )
            self._write_state_locked(updated, self._read_input_checkpoint_locked(chain_id))
            return updated

    def mark_succeeded(self, chain_id: str) -> ProviderStageRetryChainV1:
        with self._lock, self._claim(chain_id):
            chain = self._reconcile_locked(self._require_chain_locked(chain_id))
            if chain.phase is ProviderStageRetryPhase.SUCCEEDED:
                return chain
            if chain.phase is not ProviderStageRetryPhase.DOWNSTREAM_BOUND:
                raise StateConflictError("provider-stage downstream effect is not bound")
            updated = replace(chain, phase=ProviderStageRetryPhase.SUCCEEDED)
            self._write_state_locked(updated, self._read_input_checkpoint_locked(chain_id))
            return updated

    def block_ambiguous(
        self,
        chain_id: str,
        *,
        reason: ProviderStageBlockReason,
        evidence_sha256: str,
    ) -> ProviderStageRetryChainV1:
        with self._lock, self._claim(chain_id):
            chain = self._reconcile_locked(self._require_chain_locked(chain_id))
            if chain.phase is ProviderStageRetryPhase.BLOCKED_AMBIGUOUS:
                if chain.block_reason == reason and chain.block_evidence_sha256 == evidence_sha256:
                    return chain
                raise StateConflictError("provider-stage block evidence changed")
            if chain.phase in {
                ProviderStageRetryPhase.SUCCEEDED,
                ProviderStageRetryPhase.EXHAUSTED,
            }:
                raise StateConflictError("terminal provider-stage chain cannot become blocked")
            if chain.phase is ProviderStageRetryPhase.DISPATCH_STARTED:
                raise StateConflictError(
                    "provider-stage dispatch must be conservatively accounted before blocking"
                )
            updated = replace(
                chain,
                phase=ProviderStageRetryPhase.BLOCKED_AMBIGUOUS,
                block_reason=reason,
                block_evidence_sha256=evidence_sha256,
            )
            self._write_state_locked(updated, self._read_input_checkpoint_locked(chain_id))
            self._ensure_terminal_locked(updated)
            return updated

    def load_result(self, chain_id: str) -> bytes:
        """Read the exact protected successful result without provider replay."""

        with self._lock, self._claim(chain_id):
            chain = self._reconcile_locked(self._require_chain_locked(chain_id))
            if chain.result_checkpoint is None:
                raise StateConflictError("provider-stage result checkpoint is unavailable")
            attempt_number = chain.result_checkpoint.attempt_number
            assert attempt_number is not None
            staged = self._read_staged_result_locked(chain_id, attempt_number, required=True)
            assert staged is not None
            if staged["checkpoint"] != chain.result_checkpoint:
                raise StateConflictError("provider-stage exact result changed after binding")
            return self._result_path(chain_id, attempt_number).read_bytes()

    def terminal(self, chain_id: str) -> ProviderStageRetryTerminalV1 | None:
        with self._lock, self._claim(chain_id):
            chain = self._reconcile_locked(self._require_chain_locked(chain_id))
            return self._ensure_terminal_locked(chain)

    def _freeze_input_locked(
        self,
        identity: ProviderStageRetryIdentityV1,
        exact_input: bytes,
    ) -> ProviderStageProtectedCheckpointV1:
        checkpoint = ProviderStageProtectedCheckpointV1.create(
            checkpoint_kind=ProviderStageCheckpointKind.INPUT,
            chain_id=identity.chain_id,
            attempt_number=None,
            content_sha256=bytes_sha256(exact_input),
            size_bytes=len(exact_input),
            evidence_sha256=identity.authority_sha256,
        )
        chain_root = self._chain_root(identity.chain_id)
        chain_root.mkdir(parents=True, exist_ok=True)
        _write_immutable_bytes(
            self._input_path(identity.chain_id),
            exact_input,
            artifact="provider-stage input",
        )
        _write_immutable_json(
            self._input_checkpoint_path(identity.chain_id),
            checkpoint.to_payload(),
            artifact="provider-stage input checkpoint",
        )
        recovered = self._read_input_checkpoint_locked(identity.chain_id)
        self._require_input_locked(identity.chain_id, recovered)
        if recovered != checkpoint:
            raise StateConflictError("provider-stage protected input checkpoint changed")
        return recovered

    def _reconcile_locked(self, chain: ProviderStageRetryChainV1) -> ProviderStageRetryChainV1:
        if chain.phase is ProviderStageRetryPhase.DISPATCH_STARTED:
            attempt_number = chain.attempts[-1].attempt_number
            staged = self._read_staged_result_locked(
                chain.chain_id,
                attempt_number,
                required=False,
            )
            if staged is not None:
                chain = self._commit_staged_result_locked(chain, attempt_number)
        self._ensure_terminal_locked(chain)
        return chain

    def _commit_staged_result_locked(
        self,
        chain: ProviderStageRetryChainV1,
        attempt_number: int,
    ) -> ProviderStageRetryChainV1:
        current = self._require_current_attempt(chain, attempt_number)
        if current.phase is ProviderStageAttemptPhase.RESULT_FROZEN:
            return chain
        if chain.phase is not ProviderStageRetryPhase.DISPATCH_STARTED:
            raise StateConflictError("provider-stage result cannot bind from this phase")
        staged = self._read_staged_result_locked(chain.chain_id, attempt_number, required=True)
        assert staged is not None
        checkpoint = staged["checkpoint"]
        assert isinstance(checkpoint, ProviderStageProtectedCheckpointV1)
        updated_attempt = replace(
            current,
            phase=ProviderStageAttemptPhase.RESULT_FROZEN,
            ledger_prefix_after_sha256=staged["ledger_prefix_after_sha256"],
            provider_operations_observed=staged["provider_operations_observed"],
            provider_operations_conservative=staged["provider_operations_conservative"],
            duration_ms=staged["duration_ms"],
            input_tokens=staged["input_tokens"],
            cached_input_tokens=staged["cached_input_tokens"],
            output_tokens=staged["output_tokens"],
            reasoning_tokens=staged["reasoning_tokens"],
            result_checkpoint_sha256=checkpoint.checkpoint_sha256,
        )
        updated = replace(
            chain,
            phase=ProviderStageRetryPhase.RESULT_FROZEN,
            attempts=(*chain.attempts[:-1], updated_attempt),
            result_checkpoint=checkpoint,
        )
        self._write_state_locked(updated, self._read_input_checkpoint_locked(chain.chain_id))
        return updated

    def _ensure_terminal_locked(
        self,
        chain: ProviderStageRetryChainV1,
    ) -> ProviderStageRetryTerminalV1 | None:
        path = self._terminal_path(chain.chain_id)
        if chain.phase is ProviderStageRetryPhase.EXHAUSTED:
            terminal: ProviderStageRetryTerminalV1 = self._exhausted_terminal(chain)
        elif chain.phase is ProviderStageRetryPhase.BLOCKED_AMBIGUOUS:
            terminal = self._blocked_terminal(chain)
        else:
            if path.exists():
                raise StateConflictError("active provider-stage chain has terminal evidence")
            return None
        _write_immutable_json(
            path,
            terminal.to_payload(),
            artifact="provider-stage terminal",
        )
        try:
            raw = _read_json(path, artifact="provider-stage terminal")
            recovered = provider_stage_terminal_from_payload(raw)
        except ContractValidationError as exc:
            raise StateConflictError("provider-stage terminal evidence changed") from exc
        if recovered != terminal:
            raise StateConflictError("provider-stage terminal evidence changed")
        return recovered

    @staticmethod
    def _exhausted_terminal(chain: ProviderStageRetryChainV1) -> ProviderStageRetryExhaustedV1:
        final = chain.attempts[-1]
        assert final.failure_class is not None
        terminal_evidence_sha256 = domain_sha256(
            "cera.provider_stage_retry_exhausted_evidence.v1",
            {
                "chain_sha256": chain.chain_sha256,
                "attempt_chain_sha256": chain.attempt_chain_sha256,
                "phase": chain.phase.value,
            },
        )
        return ProviderStageRetryExhaustedV1(
            schema_version=ProviderStageRetryExhaustedV1.SCHEMA_VERSION,
            severity="critical",
            provider=chain.identity.provider,
            model_family=chain.identity.model_family,
            stage=chain.identity.stage,
            maximum_attempts=MAXIMUM_PROVIDER_STAGE_ATTEMPTS,
            attempts_total=MAXIMUM_PROVIDER_STAGE_ATTEMPTS,
            retries_consumed=MAXIMUM_PROVIDER_STAGE_ATTEMPTS - 1,
            story_state_committed=chain.identity.story_state_committed,
            failed_stage_effect_committed=False,
            provider_operations_observed_total=chain.provider_operations_observed_total,
            provider_operations_conservative_total=(chain.provider_operations_conservative_total),
            final_failure_class=final.failure_class,
            request_sha256=chain.identity.request_sha256,
            stage_input_sha256=chain.identity.stage_input_sha256,
            attempt_chain_sha256=chain.attempt_chain_sha256,
            terminal_evidence_sha256=terminal_evidence_sha256,
        )

    @staticmethod
    def _blocked_terminal(chain: ProviderStageRetryChainV1) -> ProviderStageRetryBlockedV1:
        assert chain.block_reason is not None
        terminal_evidence_sha256 = domain_sha256(
            "cera.provider_stage_retry_blocked_evidence.v1",
            {
                "chain_sha256": chain.chain_sha256,
                "attempt_chain_sha256": chain.attempt_chain_sha256,
                "phase": chain.phase.value,
                "block_reason": chain.block_reason.value,
                "block_evidence_sha256": chain.block_evidence_sha256,
            },
        )
        return ProviderStageRetryBlockedV1(
            schema_version=ProviderStageRetryBlockedV1.SCHEMA_VERSION,
            severity="critical",
            provider=chain.identity.provider,
            model_family=chain.identity.model_family,
            stage=chain.identity.stage,
            maximum_attempts=MAXIMUM_PROVIDER_STAGE_ATTEMPTS,
            attempts_total=chain.attempts_total,
            retries_consumed=chain.retries_consumed,
            story_state_committed=chain.identity.story_state_committed,
            failed_stage_effect_committed=False,
            provider_operations_observed_total=chain.provider_operations_observed_total,
            provider_operations_conservative_total=(chain.provider_operations_conservative_total),
            block_reason=chain.block_reason,
            request_sha256=chain.identity.request_sha256,
            stage_input_sha256=chain.identity.stage_input_sha256,
            attempt_chain_sha256=chain.attempt_chain_sha256,
            terminal_evidence_sha256=terminal_evidence_sha256,
        )

    def _write_state_locked(
        self,
        chain: ProviderStageRetryChainV1,
        input_checkpoint: ProviderStageProtectedCheckpointV1,
    ) -> None:
        if input_checkpoint.chain_id != chain.chain_id:
            raise StateConflictError("provider-stage state lost protected input custody")
        body = {
            "schema_version": _STATE_SCHEMA,
            "chain": chain.to_payload(),
            "input_checkpoint_sha256": input_checkpoint.checkpoint_sha256,
        }
        _atomic_write_json(
            self._state_path(chain.chain_id),
            {**body, "state_sha256": canonical_sha256(body)},
        )

    def _read_chain_locked(
        self,
        chain_id: str,
        *,
        required: bool,
    ) -> ProviderStageRetryChainV1 | None:
        path = self._state_path(chain_id)
        if not path.exists():
            if required:
                raise StateConflictError("provider-stage retry chain is unavailable")
            return None
        raw = _read_json(path, artifact="provider-stage state")
        required_fields = {
            "schema_version",
            "chain",
            "input_checkpoint_sha256",
            "state_sha256",
        }
        if set(raw) != required_fields:
            raise StateConflictError("provider-stage retry state shape changed")
        body = {key: raw[key] for key in required_fields if key != "state_sha256"}
        if raw["schema_version"] != _STATE_SCHEMA or raw["state_sha256"] != canonical_sha256(body):
            raise StateConflictError("provider-stage retry state hash changed")
        try:
            chain = provider_stage_chain_from_payload(raw["chain"])
        except ContractValidationError as exc:
            raise StateConflictError("provider-stage retry chain changed") from exc
        if chain.chain_id != chain_id:
            raise StateConflictError("provider-stage compact locator collision detected")
        checkpoint = self._read_input_checkpoint_locked(chain_id)
        if raw["input_checkpoint_sha256"] != checkpoint.checkpoint_sha256:
            raise StateConflictError("provider-stage input checkpoint binding changed")
        self._require_input_locked(chain_id, checkpoint)
        if checkpoint.content_sha256 != chain.identity.stage_input_sha256:
            raise StateConflictError("provider-stage input hash changed")
        if chain.result_checkpoint is not None:
            attempt_number = chain.result_checkpoint.attempt_number
            assert attempt_number is not None
            staged = self._read_staged_result_locked(chain_id, attempt_number, required=True)
            assert staged is not None
            if staged["checkpoint"] != chain.result_checkpoint:
                raise StateConflictError("provider-stage result checkpoint changed")
        return chain

    def _require_chain_locked(self, chain_id: str) -> ProviderStageRetryChainV1:
        chain = self._read_chain_locked(chain_id, required=True)
        assert chain is not None
        return chain

    def _read_input_checkpoint_locked(
        self,
        chain_id: str,
    ) -> ProviderStageProtectedCheckpointV1:
        raw = _read_json(
            self._input_checkpoint_path(chain_id),
            artifact="provider-stage input checkpoint",
        )
        try:
            checkpoint = provider_stage_checkpoint_from_payload(raw)
        except ContractValidationError as exc:
            raise StateConflictError("provider-stage input checkpoint changed") from exc
        if (
            checkpoint.chain_id != chain_id
            or checkpoint.checkpoint_kind is not ProviderStageCheckpointKind.INPUT
        ):
            raise StateConflictError("provider-stage input checkpoint lost custody")
        return checkpoint

    def _require_input_locked(
        self,
        chain_id: str,
        checkpoint: ProviderStageProtectedCheckpointV1,
    ) -> None:
        try:
            exact_input = self._input_path(chain_id).read_bytes()
        except OSError as exc:
            raise StateConflictError("provider-stage protected input is unavailable") from exc
        if (
            bytes_sha256(exact_input) != checkpoint.content_sha256
            or len(exact_input) != checkpoint.size_bytes
        ):
            raise StateConflictError("provider-stage protected input bytes changed")

    def _read_staged_result_locked(
        self,
        chain_id: str,
        attempt_number: int,
        *,
        required: bool,
    ) -> dict[str, Any] | None:
        path = self._staged_result_path(chain_id, attempt_number)
        if not path.exists():
            if required:
                raise StateConflictError("provider-stage result checkpoint is unavailable")
            return None
        raw = _read_json(path, artifact="provider-stage result checkpoint")
        required_fields = {
            "schema_version",
            "checkpoint",
            "ledger_prefix_after_sha256",
            "provider_operations_observed",
            "provider_operations_conservative",
            "duration_ms",
            "input_tokens",
            "cached_input_tokens",
            "output_tokens",
            "reasoning_tokens",
            "staged_sha256",
        }
        if set(raw) != required_fields:
            raise StateConflictError("provider-stage result checkpoint shape changed")
        body = {key: raw[key] for key in required_fields if key != "staged_sha256"}
        if raw["schema_version"] != _STAGED_RESULT_SCHEMA or raw[
            "staged_sha256"
        ] != canonical_sha256(body):
            raise StateConflictError("provider-stage result checkpoint hash changed")
        try:
            checkpoint = provider_stage_checkpoint_from_payload(raw["checkpoint"])
        except ContractValidationError as exc:
            raise StateConflictError("provider-stage result checkpoint changed") from exc
        if (
            checkpoint.chain_id != chain_id
            or checkpoint.attempt_number != attempt_number
            or checkpoint.checkpoint_kind is not ProviderStageCheckpointKind.RESULT
        ):
            raise StateConflictError("provider-stage result checkpoint lost custody")
        result_path = self._result_path(chain_id, attempt_number)
        try:
            exact_result = result_path.read_bytes()
        except OSError as exc:
            raise StateConflictError("provider-stage exact result is unavailable") from exc
        if (
            bytes_sha256(exact_result) != checkpoint.content_sha256
            or len(exact_result) != checkpoint.size_bytes
        ):
            raise StateConflictError("provider-stage exact result bytes changed")
        _require_sha256_value(raw["ledger_prefix_after_sha256"], "result ledger prefix")
        _require_nonnegative(raw["provider_operations_observed"], "observed operations")
        _require_nonnegative(
            raw["provider_operations_conservative"],
            "conservative operations",
        )
        _require_nonnegative(raw["duration_ms"], "duration")
        for field_name in (
            "input_tokens",
            "cached_input_tokens",
            "output_tokens",
            "reasoning_tokens",
        ):
            _require_optional_nonnegative(raw[field_name], field_name.replace("_", " "))
        if raw["provider_operations_conservative"] < raw["provider_operations_observed"]:
            raise StateConflictError("provider-stage result accounting undercounts")
        return {
            "checkpoint": checkpoint,
            "ledger_prefix_after_sha256": raw["ledger_prefix_after_sha256"],
            "provider_operations_observed": raw["provider_operations_observed"],
            "provider_operations_conservative": raw["provider_operations_conservative"],
            "duration_ms": raw["duration_ms"],
            "input_tokens": raw["input_tokens"],
            "cached_input_tokens": raw["cached_input_tokens"],
            "output_tokens": raw["output_tokens"],
            "reasoning_tokens": raw["reasoning_tokens"],
            "exact_result": exact_result,
        }

    @staticmethod
    def _require_current_attempt(
        chain: ProviderStageRetryChainV1,
        attempt_number: int,
    ) -> ProviderStageAttemptV1:
        if not chain.attempts or chain.attempts[-1].attempt_number != attempt_number:
            raise StateConflictError("provider-stage attempt is not current")
        return chain.attempts[-1]

    @staticmethod
    def _require_failure_arguments(
        attempt: ProviderStageAttemptV1,
        *,
        failure_class: ProviderStageFailureClass,
        failure_evidence_sha256: str,
        ledger_prefix_after_sha256: str,
        provider_operations_observed: int,
        provider_operations_conservative: int,
        duration_ms: int,
        input_tokens: int | None,
        cached_input_tokens: int | None,
        output_tokens: int | None,
        reasoning_tokens: int | None,
    ) -> None:
        if (
            attempt.failure_class != failure_class
            or attempt.failure_evidence_sha256 != failure_evidence_sha256
            or attempt.ledger_prefix_after_sha256 != ledger_prefix_after_sha256
            or attempt.provider_operations_observed != provider_operations_observed
            or attempt.provider_operations_conservative != provider_operations_conservative
            or attempt.duration_ms != duration_ms
            or attempt.input_tokens != input_tokens
            or attempt.cached_input_tokens != cached_input_tokens
            or attempt.output_tokens != output_tokens
            or attempt.reasoning_tokens != reasoning_tokens
        ):
            raise StateConflictError("provider-stage failure evidence changed")

    @staticmethod
    def _require_staged_arguments(
        staged: Mapping[str, Any],
        *,
        exact_result: bytes,
        result_evidence_sha256: str,
        ledger_prefix_after_sha256: str,
        provider_operations_observed: int,
        provider_operations_conservative: int,
        duration_ms: int,
        input_tokens: int | None,
        cached_input_tokens: int | None,
        output_tokens: int | None,
        reasoning_tokens: int | None,
    ) -> None:
        checkpoint = staged["checkpoint"]
        assert isinstance(checkpoint, ProviderStageProtectedCheckpointV1)
        if (
            staged["exact_result"] != exact_result
            or checkpoint.evidence_sha256 != result_evidence_sha256
            or staged["ledger_prefix_after_sha256"] != ledger_prefix_after_sha256
            or staged["provider_operations_observed"] != provider_operations_observed
            or staged["provider_operations_conservative"] != provider_operations_conservative
            or staged["duration_ms"] != duration_ms
            or staged["input_tokens"] != input_tokens
            or staged["cached_input_tokens"] != cached_input_tokens
            or staged["output_tokens"] != output_tokens
            or staged["reasoning_tokens"] != reasoning_tokens
        ):
            raise StateConflictError("provider-stage staged result changed")

    @staticmethod
    def _validate_exact_input(
        identity: ProviderStageRetryIdentityV1,
        exact_input: bytes,
    ) -> None:
        if not isinstance(exact_input, bytes) or not exact_input:
            raise ContractValidationError("provider-stage exact input must be non-empty bytes")
        if bytes_sha256(exact_input) != identity.stage_input_sha256:
            raise ContractValidationError("provider-stage exact input hash changed")

    def _claim(self, chain_id: str) -> _CrashReleasedClaim:
        compact = self._compact_locator(chain_id)
        return _CrashReleasedClaim(self.claims_root / f"{compact}.lock")

    def _chain_root(self, chain_id: str) -> Path:
        path = (self.chains_root / self._compact_locator(chain_id)).resolve()
        if not path.is_relative_to(self.chains_root):
            raise ContractValidationError("provider-stage retry path escaped its root")
        return path

    @staticmethod
    def _compact_locator(chain_id: str) -> str:
        prefix = "stage-retry-"
        digest = chain_id.removeprefix(prefix)
        if (
            not chain_id.startswith(prefix)
            or len(digest) != 64
            or any(value not in "0123456789abcdef" for value in digest)
        ):
            raise ContractValidationError("provider-stage retry chain identity is invalid")
        return f"c-{digest[:32]}"

    def _input_path(self, chain_id: str) -> Path:
        return self._chain_root(chain_id) / "INPUT.bin"

    def _input_checkpoint_path(self, chain_id: str) -> Path:
        return self._chain_root(chain_id) / "INPUT.json"

    def _state_path(self, chain_id: str) -> Path:
        return self._chain_root(chain_id) / "STATE.json"

    def _terminal_path(self, chain_id: str) -> Path:
        return self._chain_root(chain_id) / "TERMINAL.json"

    def _attempt_root(self, chain_id: str, attempt_number: int) -> Path:
        if type(attempt_number) is not int or not 1 <= attempt_number <= 3:
            raise ContractValidationError("provider-stage attempt path is invalid")
        return self._chain_root(chain_id) / "ATTEMPTS" / f"{attempt_number:02d}"

    def _result_path(self, chain_id: str, attempt_number: int) -> Path:
        return self._attempt_root(chain_id, attempt_number) / "RESULT.bin"

    def _staged_result_path(self, chain_id: str, attempt_number: int) -> Path:
        return self._attempt_root(chain_id, attempt_number) / "RESULT.json"


class _CrashReleasedClaim:
    """Cross-process non-blocking claim whose OS lock is released on crash."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.descriptor: int | None = None

    def __enter__(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = os.open(
                self.path,
                os.O_CREAT | os.O_RDWR | getattr(os, "O_BINARY", 0),
                0o600,
            )
            if os.fstat(descriptor).st_size == 0:
                os.write(descriptor, b"\0")
                os.fsync(descriptor)
            os.lseek(descriptor, 0, os.SEEK_SET)
            _lock_descriptor(descriptor)
        except OSError as exc:
            try:
                os.close(descriptor)
            except (UnboundLocalError, OSError):
                pass
            raise StateConflictError(
                "provider-stage retry chain is owned by another process"
            ) from exc
        self.descriptor = descriptor

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        del exc_type, exc, traceback
        descriptor = self.descriptor
        if descriptor is not None:
            _unlock_descriptor(descriptor)
            os.close(descriptor)
            self.descriptor = None


def _lock_descriptor(descriptor: int) -> None:
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        return
    fcntl = __import__("fcntl")
    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock_descriptor(descriptor: int) -> None:
    if os.name == "nt":
        import msvcrt

        os.lseek(descriptor, 0, os.SEEK_SET)
        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        return
    fcntl = __import__("fcntl")
    fcntl.flock(descriptor, fcntl.LOCK_UN)


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".provider-stage-{uuid4().hex}.tmp"
    data = canonical_bytes(payload) + b"\n"
    try:
        with temporary.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _write_immutable_json(
    path: Path,
    payload: Mapping[str, Any],
    *,
    artifact: str,
) -> None:
    _write_immutable_bytes(path, canonical_bytes(payload) + b"\n", artifact=artifact)


def _write_immutable_bytes(path: Path, payload: bytes, *, artifact: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            existing = path.read_bytes()
        except OSError as exc:
            raise StateConflictError(f"{artifact} is unreadable") from exc
        if existing != payload:
            raise StateConflictError(f"{artifact} changed")
        return
    temporary = path.parent / f".provider-stage-{uuid4().hex}.tmp"
    try:
        with temporary.open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.rename(temporary, path)
        except FileExistsError:
            if path.read_bytes() != payload:
                raise StateConflictError(f"{artifact} changed") from None
    finally:
        temporary.unlink(missing_ok=True)


def _read_json(path: Path, *, artifact: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StateConflictError(f"{artifact} is unreadable") from exc
    if not isinstance(value, dict):
        raise StateConflictError(f"{artifact} is not an object")
    return value


def _require_sha256_value(value: Any, field_name: str) -> None:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise StateConflictError(f"provider-stage {field_name} changed")


def _require_nonnegative(value: Any, field_name: str) -> None:
    if type(value) is not int or value < 0:
        raise StateConflictError(f"provider-stage {field_name} changed")


def _require_optional_nonnegative(value: Any, field_name: str) -> None:
    if value is not None:
        _require_nonnegative(value, field_name)
