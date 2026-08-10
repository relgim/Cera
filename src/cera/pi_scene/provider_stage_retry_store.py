"""Crash-safe protected storage for bounded provider-stage retry chains.

The mutable ``STATE.json`` file in each chain is only a repairable cache.  The
authority for retry budget and state progression is an occurrence-scoped,
immutable authority record plus a contiguous, hash-linked sequence of
immutable state records.  Every filesystem operation below is routed through
the shared no-follow custody primitives.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from threading import RLock
from typing import Any

from cera.continuous.path_custody import (
    ensure_parent_chain,
    exclusive_no_follow_file_lock,
    inspect_leaf,
    inspect_no_follow,
    lexical_absolute,
    locked_directory_chain,
    safe_create_new_bytes,
    safe_read_bytes,
    safe_replace,
    unlink_if_identity,
)
from cera.errors import ContractValidationError, StateConflictError
from cera.serialization import bytes_sha256, canonical_bytes, canonical_sha256, domain_sha256

from .provider_stage_retry import (
    MAXIMUM_PROVIDER_STAGE_ATTEMPTS,
    MAXIMUM_SAFE_INTEGER,
    ProviderStage,
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
    provider_stage_retry_identity_from_payload,
    provider_stage_terminal_from_payload,
)

_AUTHORITY_SCHEMA = "cera.provider_stage_retry_authority.v1"
_INDEX_SCHEMA = "cera.provider_stage_retry_index.v1"
_STATE_RECORD_SCHEMA = "cera.provider_stage_retry_state_record.v1"
_STAGED_RESULT_SCHEMA = "cera.provider_stage_staged_result.v1"
_MAXIMUM_STATE_RECORDS = 20


class ProviderStageRetryStoreV1:
    """Protected byte custody and immutable retry-budget authority.

    ``request_occurrence_sha256`` must identify one durable stage occurrence,
    such as a hash of world, branch, request ID, and generation ordinal.  It
    must never be derived from repeatable request content alone.
    """

    def __init__(self, root: Path) -> None:
        if not isinstance(root, Path) or not root.is_absolute():
            raise ContractValidationError("provider-stage retry root must be absolute")
        self.root = lexical_absolute(root)
        self._initialize_root()
        self.chains_root = self.root / "CHAINS"
        self.claims_root = self.root / "CLAIMS"
        self.index_root = self.root / "INDEX"
        for relative in ("CHAINS", "CLAIMS", "INDEX"):
            with locked_directory_chain(self.root, relative, create_missing=True):
                pass
        self._lock = RLock()

    def freeze_input(
        self,
        identity: ProviderStageRetryIdentityV1,
        exact_input: bytes,
    ) -> ProviderStageProtectedCheckpointV1:
        """Freeze input and immutable occurrence authority before dispatch."""

        self._validate_identity(identity)
        self._validate_exact_input(identity, exact_input)
        with self._lock, self._claim(identity.chain_id):
            checkpoint, _ = self._bind_authority_locked(identity, exact_input)
            return checkpoint

    def begin(
        self,
        identity: ProviderStageRetryIdentityV1,
        exact_input: bytes,
    ) -> ProviderStageRetryChainV1:
        """Idempotently publish or recover an occurrence-scoped chain."""

        self._validate_identity(identity)
        self._validate_exact_input(identity, exact_input)
        with self._lock, self._claim(identity.chain_id):
            checkpoint, bound_identity = self._bind_authority_locked(identity, exact_input)
            existing = self._read_chain_locked(identity.chain_id, required=False)
            if existing is None:
                existing = ProviderStageRetryChainV1(
                    schema_version=ProviderStageRetryChainV1.SCHEMA_VERSION,
                    identity=bound_identity,
                    phase=ProviderStageRetryPhase.INPUT_FROZEN,
                    attempts=(),
                    result_checkpoint=None,
                    downstream_intent_sha256=None,
                    downstream_evidence_sha256=None,
                    block_reason=None,
                    block_evidence_sha256=None,
                )
                self._write_state_locked(existing, checkpoint)
            if existing.identity != bound_identity:
                raise StateConflictError("provider-stage retry authority changed")
            return self._reconcile_locked(existing)

    def read(self, chain_id: str) -> ProviderStageRetryChainV1:
        with self._lock, self._claim(chain_id):
            return self._reconcile_locked(self._require_chain_locked(chain_id))

    def load_input(self, chain_id: str) -> bytes:
        """Return the exact buffer that passed protected-input validation."""

        with self._lock, self._claim(chain_id):
            chain = self._require_chain_locked(chain_id)
            checkpoint = self._read_input_checkpoint_locked(chain_id)
            exact_input = self._require_input_locked(chain_id, checkpoint)
            if checkpoint.content_sha256 != chain.identity.stage_input_sha256:
                raise StateConflictError("provider-stage protected input identity changed")
            return exact_input

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
            with locked_directory_chain(
                self.root,
                self._attempt_relative(chain_id, attempt_number),
                create_missing=True,
            ):
                pass
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
                provider_operations_conservative=1,
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
        """Publish result bytes and safe metrics before binding chain state."""

        if not isinstance(exact_result, bytes) or not exact_result:
            raise ContractValidationError("provider-stage exact result must be non-empty bytes")
        with self._lock, self._claim(chain_id):
            chain = self._require_chain_locked(chain_id)
            current = self._require_current_attempt(chain, attempt_number)
            result_phases = {
                ProviderStageRetryPhase.RESULT_FROZEN,
                ProviderStageRetryPhase.DOWNSTREAM_INTENT_FROZEN,
                ProviderStageRetryPhase.DOWNSTREAM_BOUND,
                ProviderStageRetryPhase.SUCCEEDED,
            }
            if chain.phase not in {ProviderStageRetryPhase.DISPATCH_STARTED, *result_phases}:
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
                checkpoint = staged["checkpoint"]
                assert isinstance(checkpoint, ProviderStageProtectedCheckpointV1)
                return checkpoint
            checkpoint = ProviderStageProtectedCheckpointV1.create(
                checkpoint_kind=ProviderStageCheckpointKind.RESULT,
                chain_id=chain_id,
                attempt_number=attempt_number,
                content_sha256=bytes_sha256(exact_result),
                size_bytes=len(exact_result),
                evidence_sha256=result_evidence_sha256,
            )
            # Constructing the terminal attempt validates every metric before
            # either immutable result artifact can be published.
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
            payload = {**body, "staged_sha256": canonical_sha256(body)}
            self._publish_immutable(
                self._result_relative(chain_id, attempt_number),
                exact_result,
                artifact="provider-stage result",
            )
            self._publish_immutable_json(
                self._staged_result_relative(chain_id, attempt_number),
                payload,
                artifact="provider-stage result checkpoint",
            )
            staged = self._read_staged_result_locked(chain_id, attempt_number, required=True)
            assert staged is not None
            recovered = staged["checkpoint"]
            assert isinstance(recovered, ProviderStageProtectedCheckpointV1)
            return recovered

    def commit_staged_result(
        self,
        chain_id: str,
        *,
        attempt_number: int,
    ) -> ProviderStageRetryChainV1:
        with self._lock, self._claim(chain_id):
            return self._commit_staged_result_locked(
                self._require_chain_locked(chain_id), attempt_number
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
        if type(failure_class) is not ProviderStageFailureClass:
            raise ContractValidationError("provider-stage failure class is not closed")
        with self._lock, self._claim(chain_id):
            # Reconciliation gives a durable staged result precedence over any
            # later failure report for the same dispatch.
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
            updated_attempt = ProviderStageAttemptV1(
                schema_version=ProviderStageAttemptV1.SCHEMA_VERSION,
                attempt_number=current.attempt_number,
                phase=ProviderStageAttemptPhase.FAILED,
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
                failure_class=failure_class,
                failure_evidence_sha256=failure_evidence_sha256,
                owner_retirement_evidence_sha256=None,
                result_checkpoint_sha256=None,
            )
            updated = replace(
                chain,
                phase=ProviderStageRetryPhase.AWAITING_OWNER_RETIREMENT,
                attempts=(*chain.attempts[:-1], updated_attempt),
            )
            self._write_state_locked(updated, self._read_input_checkpoint_locked(chain_id))
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
            if current.phase is ProviderStageAttemptPhase.OWNER_RETIRED:
                if current.owner_retirement_evidence_sha256 == retirement_evidence_sha256:
                    return chain
                raise StateConflictError("provider-stage owner-retirement evidence changed")
            if chain.phase is not ProviderStageRetryPhase.AWAITING_OWNER_RETIREMENT:
                raise StateConflictError("provider-stage owner is not awaiting retirement")
            updated_attempt = replace(
                current,
                phase=ProviderStageAttemptPhase.OWNER_RETIRED,
                owner_retirement_evidence_sha256=retirement_evidence_sha256,
            )
            if current.failure_class is ProviderStageFailureClass.DISPATCH_AMBIGUOUS:
                # A dispatch with unknown completion is never retried.  Proving
                # the owner stopped closes live custody but cannot prove that
                # the previous provider operation did not complete.
                phase = ProviderStageRetryPhase.BLOCKED_AMBIGUOUS
                block_reason = ProviderStageBlockReason.DISPATCH_CUSTODY_AMBIGUOUS
                block_evidence = domain_sha256(
                    "cera.provider_stage_retry_dispatch_ambiguity_closed.v1",
                    {
                        "failure_evidence_sha256": current.failure_evidence_sha256,
                        "retirement_evidence_sha256": retirement_evidence_sha256,
                    },
                )
            elif attempt_number == MAXIMUM_PROVIDER_STAGE_ATTEMPTS:
                phase = (
                    ProviderStageRetryPhase.RECORDING_REPAIR_REQUIRED
                    if chain.identity.stage is ProviderStage.RECORDER
                    else ProviderStageRetryPhase.EXHAUSTED
                )
                block_reason = None
                block_evidence = None
            else:
                phase = ProviderStageRetryPhase.OWNER_RETIRED
                block_reason = None
                block_evidence = None
            updated = replace(
                chain,
                phase=phase,
                attempts=(*chain.attempts[:-1], updated_attempt),
                block_reason=block_reason,
                block_evidence_sha256=block_evidence,
            )
            self._write_state_locked(updated, self._read_input_checkpoint_locked(chain_id))
            self._ensure_terminal_locked(updated)
            return updated

    def freeze_downstream_intent(
        self,
        chain_id: str,
        *,
        downstream_intent_sha256: str,
    ) -> ProviderStageRetryChainV1:
        """Freeze idempotent sink intent before any external effect is applied."""

        with self._lock, self._claim(chain_id):
            chain = self._reconcile_locked(self._require_chain_locked(chain_id))
            if chain.phase in {
                ProviderStageRetryPhase.DOWNSTREAM_INTENT_FROZEN,
                ProviderStageRetryPhase.DOWNSTREAM_BOUND,
                ProviderStageRetryPhase.SUCCEEDED,
            }:
                if chain.downstream_intent_sha256 == downstream_intent_sha256:
                    return chain
                raise StateConflictError("provider-stage downstream intent changed")
            if chain.phase is not ProviderStageRetryPhase.RESULT_FROZEN:
                raise StateConflictError("provider-stage result is not ready for downstream intent")
            updated = replace(
                chain,
                phase=ProviderStageRetryPhase.DOWNSTREAM_INTENT_FROZEN,
                downstream_intent_sha256=downstream_intent_sha256,
            )
            self._write_state_locked(updated, self._read_input_checkpoint_locked(chain_id))
            return updated

    def bind_downstream(
        self,
        chain_id: str,
        *,
        downstream_evidence_sha256: str,
    ) -> ProviderStageRetryChainV1:
        """Bind a durable completion receipt matching the frozen sink intent."""

        with self._lock, self._claim(chain_id):
            chain = self._reconcile_locked(self._require_chain_locked(chain_id))
            if chain.phase in {
                ProviderStageRetryPhase.DOWNSTREAM_BOUND,
                ProviderStageRetryPhase.SUCCEEDED,
            }:
                if chain.downstream_evidence_sha256 == downstream_evidence_sha256:
                    return chain
                raise StateConflictError("provider-stage downstream completion changed")
            if chain.phase is not ProviderStageRetryPhase.DOWNSTREAM_INTENT_FROZEN:
                raise StateConflictError(
                    "provider-stage downstream intent is not awaiting completion"
                )
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
                raise StateConflictError(
                    "provider-stage downstream completion is not durably bound"
                )
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
        if type(reason) is not ProviderStageBlockReason:
            raise ContractValidationError("provider-stage block reason is not closed")
        with self._lock, self._claim(chain_id):
            chain = self._reconcile_locked(self._require_chain_locked(chain_id))
            if chain.phase is ProviderStageRetryPhase.BLOCKED_AMBIGUOUS:
                if chain.block_reason is reason and chain.block_evidence_sha256 == evidence_sha256:
                    return chain
                raise StateConflictError("provider-stage block evidence changed")
            if chain.phase in {
                ProviderStageRetryPhase.DOWNSTREAM_INTENT_FROZEN,
                ProviderStageRetryPhase.DOWNSTREAM_BOUND,
                ProviderStageRetryPhase.SUCCEEDED,
                ProviderStageRetryPhase.EXHAUSTED,
                ProviderStageRetryPhase.RECORDING_REPAIR_REQUIRED,
            }:
                raise StateConflictError(
                    "provider-stage effect or terminal boundary cannot become no-effect block"
                )
            if chain.phase is ProviderStageRetryPhase.DISPATCH_STARTED:
                raise StateConflictError(
                    "provider-stage dispatch must freeze conservative telemetry before blocking"
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
        """Return the exact buffer that passed staged-result validation."""

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
            exact_result = staged["exact_result"]
            assert isinstance(exact_result, bytes)
            return exact_result

    def terminal(self, chain_id: str) -> ProviderStageRetryTerminalV1 | None:
        with self._lock, self._claim(chain_id):
            chain = self._reconcile_locked(self._require_chain_locked(chain_id))
            return self._ensure_terminal_locked(chain)

    def _bind_authority_locked(
        self,
        identity: ProviderStageRetryIdentityV1,
        exact_input: bytes,
    ) -> tuple[ProviderStageProtectedCheckpointV1, ProviderStageRetryIdentityV1]:
        checkpoint = ProviderStageProtectedCheckpointV1.create(
            checkpoint_kind=ProviderStageCheckpointKind.INPUT,
            chain_id=identity.chain_id,
            attempt_number=None,
            content_sha256=bytes_sha256(exact_input),
            size_bytes=len(exact_input),
            evidence_sha256=identity.authority_sha256,
        )
        self._ensure_chain_directory(identity.chain_id)
        authority = self._read_authority_locked(identity.chain_id, required=False)
        index = self._read_index_locked(identity.logical_key_sha256, required=False)
        if authority is None:
            if index is not None:
                raise StateConflictError("provider-stage authority is missing behind its index")
            self._publish_immutable(
                self._input_relative(identity.chain_id),
                exact_input,
                artifact="provider-stage input",
            )
            self._publish_immutable_json(
                self._input_checkpoint_relative(identity.chain_id),
                checkpoint.to_payload(),
                artifact="provider-stage input checkpoint",
            )
            authority_payload = self._authority_payload(identity, checkpoint)
            self._publish_immutable_json(
                self._authority_relative(identity.chain_id),
                authority_payload,
                artifact="provider-stage authority",
            )
            self._publish_immutable_json(
                self._index_relative(identity.logical_key_sha256),
                self._index_payload(identity, authority_payload),
                artifact="provider-stage authority index",
            )
            authority = self._read_authority_locked(identity.chain_id, required=True)
            index = self._read_index_locked(identity.logical_key_sha256, required=True)
        assert authority is not None
        bound_identity = authority["identity"]
        bound_checkpoint = authority["input_checkpoint"]
        assert isinstance(bound_identity, ProviderStageRetryIdentityV1)
        assert isinstance(bound_checkpoint, ProviderStageProtectedCheckpointV1)
        if index is None:
            # Authority publication precedes index publication, so this is the
            # sole recoverable half-published ordering.
            self._publish_immutable_json(
                self._index_relative(bound_identity.logical_key_sha256),
                self._index_payload(bound_identity, authority),
                artifact="provider-stage authority index",
            )
            index = self._read_index_locked(bound_identity.logical_key_sha256, required=True)
        assert index is not None
        self._require_index_authority(index, authority)
        recovered_checkpoint = self._read_input_checkpoint_locked(identity.chain_id)
        if recovered_checkpoint != bound_checkpoint:
            raise StateConflictError("provider-stage input checkpoint authority changed")
        self._require_input_locked(identity.chain_id, bound_checkpoint)
        if identity != bound_identity:
            reason = (
                ProviderStageBlockReason.INPUT_CHANGED
                if identity.stage_input_sha256 != bound_identity.stage_input_sha256
                else ProviderStageBlockReason.AUTHORITY_CHANGED
            )
            evidence = domain_sha256(
                "cera.provider_stage_retry_authority_drift.v1",
                {
                    "reason": reason.value,
                    "bound_identity_sha256": canonical_sha256(bound_identity.to_payload()),
                    "presented_identity_sha256": canonical_sha256(identity.to_payload()),
                },
            )
            self._record_identity_drift_locked(
                bound_identity,
                bound_checkpoint,
                reason=reason,
                evidence_sha256=evidence,
            )
            message = (
                "input changed"
                if reason is ProviderStageBlockReason.INPUT_CHANGED
                else "authority changed"
            )
            raise StateConflictError(f"provider-stage retry {message}")
        return bound_checkpoint, bound_identity

    def _record_identity_drift_locked(
        self,
        identity: ProviderStageRetryIdentityV1,
        checkpoint: ProviderStageProtectedCheckpointV1,
        *,
        reason: ProviderStageBlockReason,
        evidence_sha256: str,
    ) -> None:
        chain = self._read_chain_locked(identity.chain_id, required=False)
        if chain is None:
            chain = ProviderStageRetryChainV1(
                schema_version=ProviderStageRetryChainV1.SCHEMA_VERSION,
                identity=identity,
                phase=ProviderStageRetryPhase.INPUT_FROZEN,
                attempts=(),
                result_checkpoint=None,
                downstream_intent_sha256=None,
                downstream_evidence_sha256=None,
                block_reason=None,
                block_evidence_sha256=None,
            )
            self._write_state_locked(chain, checkpoint)
        if chain.phase is ProviderStageRetryPhase.BLOCKED_AMBIGUOUS:
            if chain.block_reason is reason and chain.block_evidence_sha256 == evidence_sha256:
                return
            raise StateConflictError("provider-stage retry authority drift evidence changed")
        if chain.phase in {
            ProviderStageRetryPhase.DOWNSTREAM_INTENT_FROZEN,
            ProviderStageRetryPhase.DOWNSTREAM_BOUND,
            ProviderStageRetryPhase.SUCCEEDED,
            ProviderStageRetryPhase.EXHAUSTED,
        }:
            # Never rewrite a committed-effect or existing terminal history.
            return
        if chain.phase is ProviderStageRetryPhase.DISPATCH_STARTED:
            # Dispatch telemetry must be frozen by the runtime owner before a
            # safe block can claim no stage effect was committed.
            return
        blocked = replace(
            chain,
            phase=ProviderStageRetryPhase.BLOCKED_AMBIGUOUS,
            block_reason=reason,
            block_evidence_sha256=evidence_sha256,
        )
        self._write_state_locked(blocked, checkpoint)
        self._ensure_terminal_locked(blocked)

    def _reconcile_locked(self, chain: ProviderStageRetryChainV1) -> ProviderStageRetryChainV1:
        if chain.phase is ProviderStageRetryPhase.DISPATCH_STARTED:
            attempt_number = chain.attempts[-1].attempt_number
            staged = self._read_staged_result_locked(chain.chain_id, attempt_number, required=False)
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
        updated_attempt = ProviderStageAttemptV1(
            schema_version=ProviderStageAttemptV1.SCHEMA_VERSION,
            attempt_number=current.attempt_number,
            phase=ProviderStageAttemptPhase.RESULT_FROZEN,
            session_scope_sha256=current.session_scope_sha256,
            ledger_prefix_before_sha256=current.ledger_prefix_before_sha256,
            dispatch_evidence_sha256=current.dispatch_evidence_sha256,
            ledger_prefix_after_sha256=staged["ledger_prefix_after_sha256"],
            provider_operations_observed=staged["provider_operations_observed"],
            provider_operations_conservative=staged["provider_operations_conservative"],
            duration_ms=staged["duration_ms"],
            input_tokens=staged["input_tokens"],
            cached_input_tokens=staged["cached_input_tokens"],
            output_tokens=staged["output_tokens"],
            reasoning_tokens=staged["reasoning_tokens"],
            failure_class=None,
            failure_evidence_sha256=None,
            owner_retirement_evidence_sha256=None,
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
        relative = self._terminal_relative(chain.chain_id)
        existing_bytes = self._read_optional_bytes(relative)
        if chain.phase is ProviderStageRetryPhase.EXHAUSTED:
            terminal: ProviderStageRetryTerminalV1 = self._exhausted_terminal(chain)
        elif chain.phase is ProviderStageRetryPhase.BLOCKED_AMBIGUOUS:
            terminal = self._blocked_terminal(chain)
        else:
            if existing_bytes is not None:
                raise StateConflictError("active provider-stage chain has terminal evidence")
            return None
        expected = canonical_bytes(terminal.to_payload()) + b"\n"
        self._publish_immutable(relative, expected, artifact="provider-stage terminal")
        raw = self._decode_json_bytes(
            self._read_required_bytes(relative), artifact="provider-stage terminal"
        )
        try:
            recovered = provider_stage_terminal_from_payload(raw)
        except ContractValidationError as exc:
            raise StateConflictError("provider-stage terminal evidence changed") from exc
        if recovered != terminal:
            raise StateConflictError("provider-stage terminal evidence changed")
        return recovered

    @staticmethod
    def _exhausted_terminal(
        chain: ProviderStageRetryChainV1,
    ) -> ProviderStageRetryExhaustedV1:
        final = chain.attempts[-1]
        assert final.failure_class is not None
        evidence = domain_sha256(
            "cera.provider_stage_retry_exhausted_evidence.v1",
            {
                "chain_sha256": chain.chain_sha256,
                "attempt_chain_sha256": chain.attempt_chain_sha256,
                "final_failure_class": final.failure_class.value,
            },
        )
        return ProviderStageRetryExhaustedV1(
            schema_version=ProviderStageRetryExhaustedV1.SCHEMA_VERSION,
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
            final_failure_class=final.failure_class,
            request_sha256=chain.identity.request_sha256,
            stage_input_sha256=chain.identity.stage_input_sha256,
            attempt_chain_sha256=chain.attempt_chain_sha256,
            terminal_evidence_sha256=evidence,
        )

    @staticmethod
    def _blocked_terminal(chain: ProviderStageRetryChainV1) -> ProviderStageRetryBlockedV1:
        assert chain.block_reason is not None
        assert chain.block_evidence_sha256 is not None
        evidence = domain_sha256(
            "cera.provider_stage_retry_blocked_evidence.v1",
            {
                "chain_sha256": chain.chain_sha256,
                "attempt_chain_sha256": chain.attempt_chain_sha256,
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
            terminal_evidence_sha256=evidence,
        )

    def _write_state_locked(
        self,
        chain: ProviderStageRetryChainV1,
        input_checkpoint: ProviderStageProtectedCheckpointV1,
    ) -> None:
        authority = self._read_authority_locked(chain.chain_id, required=True)
        assert authority is not None
        authority_identity = authority["identity"]
        authority_checkpoint = authority["input_checkpoint"]
        if (
            authority_identity != chain.identity
            or authority_checkpoint != input_checkpoint
            or input_checkpoint.chain_id != chain.chain_id
        ):
            raise StateConflictError("provider-stage state lost immutable authority")
        records = self._read_state_records_locked(
            chain.chain_id,
            authority=authority,
            repair_projection=True,
        )
        if records and records[-1]["chain"] == chain:
            return
        sequence = len(records) + 1
        if sequence > _MAXIMUM_STATE_RECORDS:
            raise StateConflictError("provider-stage state record ceiling changed")
        previous_hash = None if not records else records[-1]["state_record_sha256"]
        body = {
            "schema_version": _STATE_RECORD_SCHEMA,
            "chain_id": chain.chain_id,
            "state_sequence": sequence,
            "previous_state_record_sha256": previous_hash,
            "authority_record_sha256": authority["authority_record_sha256"],
            "input_checkpoint_sha256": input_checkpoint.checkpoint_sha256,
            "chain": chain.to_payload(),
        }
        payload = {**body, "state_record_sha256": canonical_sha256(body)}
        relative = self._state_record_relative(chain.chain_id, sequence)
        self._publish_immutable_json(
            relative,
            payload,
            artifact="provider-stage state record",
        )
        recovered = self._read_state_records_locked(
            chain.chain_id,
            authority=authority,
            repair_projection=True,
        )
        if len(recovered) != sequence or recovered[-1]["chain"] != chain:
            raise StateConflictError("provider-stage state publication changed")

    def _read_chain_locked(
        self,
        chain_id: str,
        *,
        required: bool,
    ) -> ProviderStageRetryChainV1 | None:
        authority = self._read_authority_locked(chain_id, required=required)
        if authority is None:
            return None
        records = self._read_state_records_locked(
            chain_id,
            authority=authority,
            repair_projection=True,
        )
        if not records:
            if required:
                raise StateConflictError("provider-stage retry chain is unavailable")
            return None
        chain = records[-1]["chain"]
        assert isinstance(chain, ProviderStageRetryChainV1)
        checkpoint = authority["input_checkpoint"]
        assert isinstance(checkpoint, ProviderStageProtectedCheckpointV1)
        self._require_input_locked(chain_id, checkpoint)
        if checkpoint.content_sha256 != chain.identity.stage_input_sha256:
            raise StateConflictError("provider-stage protected input hash changed")
        if chain.result_checkpoint is not None:
            attempt_number = chain.result_checkpoint.attempt_number
            assert attempt_number is not None
            staged = self._read_staged_result_locked(chain_id, attempt_number, required=True)
            assert staged is not None
            if staged["checkpoint"] != chain.result_checkpoint:
                raise StateConflictError("provider-stage result checkpoint changed")
            self._require_staged_matches_attempt(staged, chain.attempts[-1])
        return chain

    def _require_chain_locked(self, chain_id: str) -> ProviderStageRetryChainV1:
        chain = self._read_chain_locked(chain_id, required=True)
        assert chain is not None
        return chain

    def _read_state_records_locked(
        self,
        chain_id: str,
        *,
        authority: Mapping[str, Any],
        repair_projection: bool,
    ) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        gap_seen = False
        for sequence in range(1, _MAXIMUM_STATE_RECORDS + 2):
            relative = self._state_record_relative(chain_id, sequence)
            data = self._read_optional_bytes(relative)
            if data is None:
                gap_seen = True
                continue
            if sequence > _MAXIMUM_STATE_RECORDS:
                raise StateConflictError("provider-stage state record ceiling changed")
            if gap_seen:
                raise StateConflictError("provider-stage state record sequence has a gap")
            record = self._decode_state_record(
                data,
                chain_id=chain_id,
                authority=authority,
                expected_sequence=sequence,
            )
            if records:
                if record["previous_state_record_sha256"] != records[-1]["state_record_sha256"]:
                    raise StateConflictError("provider-stage state predecessor changed")
                previous_chain = records[-1]["chain"]
                current_chain = record["chain"]
                assert isinstance(previous_chain, ProviderStageRetryChainV1)
                assert isinstance(current_chain, ProviderStageRetryChainV1)
                self._require_legal_successor(previous_chain, current_chain)
            elif record["previous_state_record_sha256"] is not None:
                raise StateConflictError("provider-stage initial state has a predecessor")
            records.append(record)
        projection_relative = self._state_projection_relative(chain_id)
        projection = self._read_optional_bytes(projection_relative)
        if not records:
            if projection is not None:
                raise StateConflictError(
                    "provider-stage state cache exists without immutable history"
                )
            return records
        latest_data = records[-1]["exact_bytes"]
        assert isinstance(latest_data, bytes)
        if projection != latest_data:
            if projection is not None:
                projected = self._decode_state_record(
                    projection,
                    chain_id=chain_id,
                    authority=authority,
                    expected_sequence=None,
                )
                projected_sequence = projected["state_sequence"]
                assert isinstance(projected_sequence, int)
                if (
                    projected_sequence > len(records)
                    or projected["exact_bytes"] != records[projected_sequence - 1]["exact_bytes"]
                ):
                    raise StateConflictError(
                        "provider-stage state cache is not an authoritative prior record"
                    )
            if repair_projection:
                self._replace_projection(projection_relative, latest_data)
        return records

    def _decode_state_record(
        self,
        data: bytes,
        *,
        chain_id: str,
        authority: Mapping[str, Any],
        expected_sequence: int | None,
    ) -> dict[str, Any]:
        raw = self._decode_json_bytes(data, artifact="provider-stage state record")
        required_fields = {
            "schema_version",
            "chain_id",
            "state_sequence",
            "previous_state_record_sha256",
            "authority_record_sha256",
            "input_checkpoint_sha256",
            "chain",
            "state_record_sha256",
        }
        self._require_exact_fields(raw, required_fields, "provider-stage state record")
        body = {key: raw[key] for key in required_fields if key != "state_record_sha256"}
        if (
            raw["schema_version"] != _STATE_RECORD_SCHEMA
            or raw["chain_id"] != chain_id
            or raw["state_record_sha256"] != canonical_sha256(body)
        ):
            raise StateConflictError("provider-stage state record hash changed")
        sequence = raw["state_sequence"]
        if (
            type(sequence) is not int
            or not 1 <= sequence <= _MAXIMUM_STATE_RECORDS
            or (expected_sequence is not None and sequence != expected_sequence)
        ):
            raise StateConflictError("provider-stage state record sequence changed")
        previous_hash = raw["previous_state_record_sha256"]
        if previous_hash is not None:
            self._require_sha256_value(previous_hash, "state predecessor")
        if (
            raw["authority_record_sha256"] != authority["authority_record_sha256"]
            or raw["input_checkpoint_sha256"] != authority["input_checkpoint"].checkpoint_sha256
        ):
            raise StateConflictError("provider-stage state authority binding changed")
        try:
            chain = provider_stage_chain_from_payload(raw["chain"])
        except ContractValidationError as exc:
            raise StateConflictError("provider-stage retry chain changed") from exc
        if chain.chain_id != chain_id or chain.identity != authority["identity"]:
            raise StateConflictError("provider-stage state chain authority changed")
        return {
            **raw,
            "chain": chain,
            "exact_bytes": data,
        }

    @staticmethod
    def _require_legal_successor(
        previous: ProviderStageRetryChainV1,
        current: ProviderStageRetryChainV1,
    ) -> None:
        if previous.identity != current.identity or previous.chain_id != current.chain_id:
            raise StateConflictError("provider-stage state successor changed authority")
        if previous.phase in {
            ProviderStageRetryPhase.SUCCEEDED,
            ProviderStageRetryPhase.EXHAUSTED,
            ProviderStageRetryPhase.RECORDING_REPAIR_REQUIRED,
            ProviderStageRetryPhase.BLOCKED_AMBIGUOUS,
        }:
            raise StateConflictError("terminal provider-stage state has a successor")
        if current.phase is ProviderStageRetryPhase.BLOCKED_AMBIGUOUS:
            if previous.phase in {
                ProviderStageRetryPhase.DISPATCH_STARTED,
                ProviderStageRetryPhase.DOWNSTREAM_INTENT_FROZEN,
                ProviderStageRetryPhase.DOWNSTREAM_BOUND,
            }:
                raise StateConflictError(
                    "provider-stage block crossed a downstream effect boundary"
                )
            same_attempts = current.attempts == previous.attempts
            retired_ambiguous = (
                previous.phase is ProviderStageRetryPhase.AWAITING_OWNER_RETIREMENT
                and len(previous.attempts) <= MAXIMUM_PROVIDER_STAGE_ATTEMPTS
                and previous.attempts[-1].failure_class
                is ProviderStageFailureClass.DISPATCH_AMBIGUOUS
                and current.attempts[:-1] == previous.attempts[:-1]
                and current.attempts[-1].phase is ProviderStageAttemptPhase.OWNER_RETIRED
                and current.attempts[-1]
                == replace(
                    previous.attempts[-1],
                    phase=ProviderStageAttemptPhase.OWNER_RETIRED,
                    owner_retirement_evidence_sha256=current.attempts[
                        -1
                    ].owner_retirement_evidence_sha256,
                )
            )
            blocked_expected = replace(
                previous,
                phase=ProviderStageRetryPhase.BLOCKED_AMBIGUOUS,
                attempts=current.attempts,
                block_reason=current.block_reason,
                block_evidence_sha256=current.block_evidence_sha256,
            )
            if current != blocked_expected or not (same_attempts or retired_ambiguous):
                raise StateConflictError("provider-stage blocked successor is not monotonic")
            return
        expected: ProviderStageRetryChainV1 | None = None
        if (
            previous.phase
            in {
                ProviderStageRetryPhase.INPUT_FROZEN,
                ProviderStageRetryPhase.OWNER_RETIRED,
            }
            and current.phase is ProviderStageRetryPhase.ATTEMPT_PREPARED
            and current.attempts[:-1] == previous.attempts
        ):
            expected = replace(
                previous,
                phase=current.phase,
                attempts=current.attempts,
            )
        elif (
            previous.phase is ProviderStageRetryPhase.ATTEMPT_PREPARED
            and current.phase is ProviderStageRetryPhase.DISPATCH_STARTED
            and current.attempts[:-1] == previous.attempts[:-1]
        ):
            expected_attempt = replace(
                previous.attempts[-1],
                phase=ProviderStageAttemptPhase.DISPATCH_STARTED,
                dispatch_evidence_sha256=current.attempts[-1].dispatch_evidence_sha256,
                provider_operations_conservative=1,
            )
            expected = replace(
                previous,
                phase=current.phase,
                attempts=(*previous.attempts[:-1], expected_attempt),
            )
        elif (
            previous.phase is ProviderStageRetryPhase.DISPATCH_STARTED
            and current.phase
            in {
                ProviderStageRetryPhase.AWAITING_OWNER_RETIREMENT,
                ProviderStageRetryPhase.RESULT_FROZEN,
            }
            and current.attempts[:-1] == previous.attempts[:-1]
            and ProviderStageRetryStoreV1._same_attempt_dispatch_identity(
                previous.attempts[-1], current.attempts[-1]
            )
        ):
            expected = replace(
                previous,
                phase=current.phase,
                attempts=current.attempts,
                result_checkpoint=current.result_checkpoint,
            )
        elif (
            previous.phase is ProviderStageRetryPhase.AWAITING_OWNER_RETIREMENT
            and current.phase
            in {
                ProviderStageRetryPhase.OWNER_RETIRED,
                ProviderStageRetryPhase.EXHAUSTED,
                ProviderStageRetryPhase.RECORDING_REPAIR_REQUIRED,
            }
            and current.attempts[:-1] == previous.attempts[:-1]
            and previous.attempts[-1].failure_class
            is not ProviderStageFailureClass.DISPATCH_AMBIGUOUS
        ):
            expected_attempt = replace(
                previous.attempts[-1],
                phase=ProviderStageAttemptPhase.OWNER_RETIRED,
                owner_retirement_evidence_sha256=current.attempts[
                    -1
                ].owner_retirement_evidence_sha256,
            )
            expected = replace(
                previous,
                phase=current.phase,
                attempts=(*previous.attempts[:-1], expected_attempt),
            )
        elif (
            previous.phase is ProviderStageRetryPhase.RESULT_FROZEN
            and current.phase is ProviderStageRetryPhase.DOWNSTREAM_INTENT_FROZEN
        ):
            expected = replace(
                previous,
                phase=current.phase,
                downstream_intent_sha256=current.downstream_intent_sha256,
            )
        elif (
            previous.phase is ProviderStageRetryPhase.DOWNSTREAM_INTENT_FROZEN
            and current.phase is ProviderStageRetryPhase.DOWNSTREAM_BOUND
        ):
            expected = replace(
                previous,
                phase=current.phase,
                downstream_evidence_sha256=current.downstream_evidence_sha256,
            )
        elif (
            previous.phase is ProviderStageRetryPhase.DOWNSTREAM_BOUND
            and current.phase is ProviderStageRetryPhase.SUCCEEDED
        ):
            expected = replace(previous, phase=current.phase)
        if expected != current:
            raise StateConflictError("provider-stage state successor is not monotonic")

    @staticmethod
    def _same_attempt_dispatch_identity(
        previous: ProviderStageAttemptV1,
        current: ProviderStageAttemptV1,
    ) -> bool:
        return (
            previous.attempt_number == current.attempt_number
            and previous.session_scope_sha256 == current.session_scope_sha256
            and previous.ledger_prefix_before_sha256 == current.ledger_prefix_before_sha256
            and previous.dispatch_evidence_sha256 == current.dispatch_evidence_sha256
        )

    def _read_authority_locked(
        self,
        chain_id: str,
        *,
        required: bool,
    ) -> dict[str, Any] | None:
        data = self._read_optional_bytes(self._authority_relative(chain_id))
        if data is None:
            if required:
                raise StateConflictError("provider-stage immutable authority is unavailable")
            return None
        raw = self._decode_json_bytes(data, artifact="provider-stage authority")
        required_fields = {
            "schema_version",
            "chain_id",
            "logical_key_sha256",
            "identity",
            "input_checkpoint",
            "authority_record_sha256",
        }
        self._require_exact_fields(raw, required_fields, "provider-stage authority")
        body = {key: raw[key] for key in required_fields if key != "authority_record_sha256"}
        if (
            raw["schema_version"] != _AUTHORITY_SCHEMA
            or raw["chain_id"] != chain_id
            or raw["authority_record_sha256"] != canonical_sha256(body)
        ):
            raise StateConflictError("provider-stage immutable authority hash changed")
        try:
            identity = provider_stage_retry_identity_from_payload(raw["identity"])
            checkpoint = provider_stage_checkpoint_from_payload(raw["input_checkpoint"])
        except ContractValidationError as exc:
            raise StateConflictError("provider-stage immutable authority changed") from exc
        if (
            identity.chain_id != chain_id
            or raw["logical_key_sha256"] != identity.logical_key_sha256
            or checkpoint.chain_id != chain_id
            or checkpoint.checkpoint_kind is not ProviderStageCheckpointKind.INPUT
            or checkpoint.content_sha256 != identity.stage_input_sha256
            or checkpoint.evidence_sha256 != identity.authority_sha256
        ):
            raise StateConflictError("provider-stage immutable authority binding changed")
        return {
            **raw,
            "identity": identity,
            "input_checkpoint": checkpoint,
            "exact_bytes": data,
        }

    def _read_index_locked(
        self,
        logical_key_sha256: str,
        *,
        required: bool,
    ) -> dict[str, Any] | None:
        data = self._read_optional_bytes(self._index_relative(logical_key_sha256))
        if data is None:
            if required:
                raise StateConflictError("provider-stage immutable index is unavailable")
            return None
        raw = self._decode_json_bytes(data, artifact="provider-stage authority index")
        required_fields = {
            "schema_version",
            "logical_key_sha256",
            "request_occurrence_sha256",
            "stage",
            "chain_id",
            "authority_record_sha256",
            "index_sha256",
        }
        self._require_exact_fields(raw, required_fields, "provider-stage authority index")
        body = {key: raw[key] for key in required_fields if key != "index_sha256"}
        if (
            raw["schema_version"] != _INDEX_SCHEMA
            or raw["logical_key_sha256"] != logical_key_sha256
            or raw["index_sha256"] != canonical_sha256(body)
        ):
            raise StateConflictError("provider-stage immutable index hash changed")
        self._require_sha256_value(raw["request_occurrence_sha256"], "request occurrence")
        self._require_sha256_value(raw["authority_record_sha256"], "authority record")
        self._compact_locator(raw["chain_id"])
        if not isinstance(raw["stage"], str):
            raise StateConflictError("provider-stage immutable index stage changed")
        return {**raw, "exact_bytes": data}

    @staticmethod
    def _require_index_authority(
        index: Mapping[str, Any],
        authority: Mapping[str, Any],
    ) -> None:
        identity = authority["identity"]
        assert isinstance(identity, ProviderStageRetryIdentityV1)
        if (
            index["logical_key_sha256"] != identity.logical_key_sha256
            or index["request_occurrence_sha256"] != identity.request_occurrence_sha256
            or index["stage"] != identity.stage.value
            or index["chain_id"] != identity.chain_id
            or index["authority_record_sha256"] != authority["authority_record_sha256"]
        ):
            raise StateConflictError("provider-stage index authority binding changed")

    @staticmethod
    def _authority_payload(
        identity: ProviderStageRetryIdentityV1,
        checkpoint: ProviderStageProtectedCheckpointV1,
    ) -> dict[str, Any]:
        body = {
            "schema_version": _AUTHORITY_SCHEMA,
            "chain_id": identity.chain_id,
            "logical_key_sha256": identity.logical_key_sha256,
            "identity": identity.to_payload(),
            "input_checkpoint": checkpoint.to_payload(),
        }
        return {**body, "authority_record_sha256": canonical_sha256(body)}

    @staticmethod
    def _index_payload(
        identity: ProviderStageRetryIdentityV1,
        authority: Mapping[str, Any],
    ) -> dict[str, Any]:
        body = {
            "schema_version": _INDEX_SCHEMA,
            "logical_key_sha256": identity.logical_key_sha256,
            "request_occurrence_sha256": identity.request_occurrence_sha256,
            "stage": identity.stage.value,
            "chain_id": identity.chain_id,
            "authority_record_sha256": authority["authority_record_sha256"],
        }
        return {**body, "index_sha256": canonical_sha256(body)}

    def _read_input_checkpoint_locked(
        self,
        chain_id: str,
    ) -> ProviderStageProtectedCheckpointV1:
        raw = self._decode_json_bytes(
            self._read_required_bytes(self._input_checkpoint_relative(chain_id)),
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
    ) -> bytes:
        exact_input = self._read_required_bytes(self._input_relative(chain_id))
        if (
            bytes_sha256(exact_input) != checkpoint.content_sha256
            or len(exact_input) != checkpoint.size_bytes
        ):
            raise StateConflictError("provider-stage protected input bytes changed")
        return exact_input

    def _read_staged_result_locked(
        self,
        chain_id: str,
        attempt_number: int,
        *,
        required: bool,
    ) -> dict[str, Any] | None:
        relative = self._staged_result_relative(chain_id, attempt_number)
        data = self._read_optional_bytes(relative)
        if data is None:
            if required:
                raise StateConflictError("provider-stage result checkpoint is unavailable")
            return None
        raw = self._decode_json_bytes(data, artifact="provider-stage result checkpoint")
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
        self._require_exact_fields(raw, required_fields, "provider-stage result checkpoint")
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
        exact_result = self._read_required_bytes(self._result_relative(chain_id, attempt_number))
        if (
            bytes_sha256(exact_result) != checkpoint.content_sha256
            or len(exact_result) != checkpoint.size_bytes
        ):
            raise StateConflictError("provider-stage exact result bytes changed")
        self._require_sha256_value(raw["ledger_prefix_after_sha256"], "result ledger prefix")
        for field_name in (
            "provider_operations_observed",
            "provider_operations_conservative",
            "duration_ms",
        ):
            self._require_nonnegative_safe(raw[field_name], field_name.replace("_", " "))
        for field_name in (
            "input_tokens",
            "cached_input_tokens",
            "output_tokens",
            "reasoning_tokens",
        ):
            self._require_optional_nonnegative_safe(raw[field_name], field_name.replace("_", " "))
        observed = raw["provider_operations_observed"]
        conservative = raw["provider_operations_conservative"]
        if observed < 1 or conservative != observed:
            raise StateConflictError("provider-stage result operation accounting changed")
        cached = raw["cached_input_tokens"]
        total_input = raw["input_tokens"]
        if cached is not None and (total_input is None or cached > total_input):
            raise StateConflictError("provider-stage cached input accounting changed")
        return {
            "checkpoint": checkpoint,
            "ledger_prefix_after_sha256": raw["ledger_prefix_after_sha256"],
            "provider_operations_observed": observed,
            "provider_operations_conservative": conservative,
            "duration_ms": raw["duration_ms"],
            "input_tokens": total_input,
            "cached_input_tokens": cached,
            "output_tokens": raw["output_tokens"],
            "reasoning_tokens": raw["reasoning_tokens"],
            "exact_result": exact_result,
        }

    @staticmethod
    def _require_staged_matches_attempt(
        staged: Mapping[str, Any],
        attempt: ProviderStageAttemptV1,
    ) -> None:
        checkpoint = staged["checkpoint"]
        assert isinstance(checkpoint, ProviderStageProtectedCheckpointV1)
        if (
            attempt.phase is not ProviderStageAttemptPhase.RESULT_FROZEN
            or attempt.result_checkpoint_sha256 != checkpoint.checkpoint_sha256
            or attempt.ledger_prefix_after_sha256 != staged["ledger_prefix_after_sha256"]
            or attempt.provider_operations_observed != staged["provider_operations_observed"]
            or attempt.provider_operations_conservative
            != staged["provider_operations_conservative"]
            or attempt.duration_ms != staged["duration_ms"]
            or attempt.input_tokens != staged["input_tokens"]
            or attempt.cached_input_tokens != staged["cached_input_tokens"]
            or attempt.output_tokens != staged["output_tokens"]
            or attempt.reasoning_tokens != staged["reasoning_tokens"]
        ):
            raise StateConflictError("provider-stage staged result metrics changed")

    @staticmethod
    def _require_current_attempt(
        chain: ProviderStageRetryChainV1,
        attempt_number: int,
    ) -> ProviderStageAttemptV1:
        if (
            type(attempt_number) is not int
            or not chain.attempts
            or chain.attempts[-1].attempt_number != attempt_number
        ):
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
            attempt.failure_class is not failure_class
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
    def _validate_identity(identity: ProviderStageRetryIdentityV1) -> None:
        if type(identity) is not ProviderStageRetryIdentityV1:
            raise ContractValidationError("provider-stage retry identity contract changed")

    @staticmethod
    def _validate_exact_input(
        identity: ProviderStageRetryIdentityV1,
        exact_input: bytes,
    ) -> None:
        if not isinstance(exact_input, bytes) or not exact_input:
            raise ContractValidationError("provider-stage exact input must be non-empty bytes")
        if bytes_sha256(exact_input) != identity.stage_input_sha256:
            raise ContractValidationError("provider-stage exact input hash changed")

    def _initialize_root(self) -> None:
        if self.root.parent == self.root:
            raise ContractValidationError("provider-stage retry root cannot be a filesystem root")
        parent_identity = inspect_no_follow(self.root.parent)
        if parent_identity is None or parent_identity.object_kind != "directory":
            raise StateConflictError("provider-stage retry root parent is unavailable")
        with locked_directory_chain(
            self.root.parent,
            self.root.name,
            create_missing=True,
        ):
            pass
        root_identity = inspect_no_follow(self.root)
        if root_identity is None or root_identity.object_kind != "directory":
            raise StateConflictError("provider-stage retry root is unsafe")

    def _ensure_chain_directory(self, chain_id: str) -> None:
        chain_relative = self._chain_relative(chain_id)
        for relative in (
            chain_relative,
            f"{chain_relative}/STATES",
            f"{chain_relative}/ATTEMPTS",
        ):
            with locked_directory_chain(
                self.root,
                relative,
                create_missing=True,
            ):
                pass

    @contextmanager
    def _claim(self, chain_id: str) -> Iterator[None]:
        relative = f"CLAIMS/{self._compact_locator(chain_id)}.lock"
        try:
            with exclusive_no_follow_file_lock(self.root, relative):
                yield
        except StateConflictError as exc:
            message = str(exc)
            if "already held" in message:
                raise StateConflictError(
                    "provider-stage retry chain is owned by another process"
                ) from exc
            raise

    def _publish_immutable_json(
        self,
        relative: str,
        payload: Mapping[str, Any],
        *,
        artifact: str,
    ) -> None:
        self._publish_immutable(
            relative,
            canonical_bytes(payload) + b"\n",
            artifact=artifact,
        )

    def _publish_immutable(
        self,
        relative: str,
        data: bytes,
        *,
        artifact: str,
    ) -> None:
        ensure_parent_chain(self.root, relative)
        existing = self._read_optional_bytes(relative)
        if existing is not None:
            if existing != data:
                raise StateConflictError(f"{artifact} changed")
            return
        pending = self._pending_relative(relative, data)
        pending_leaf = inspect_leaf(self.root, pending)
        if pending_leaf.identity is None:
            safe_create_new_bytes(self.root, pending, data)
        elif self._read_required_bytes(pending) != data:
            raise StateConflictError(f"{artifact} pending publication changed")
        parent = relative.rsplit("/", 1)[0] if "/" in relative else ""
        with locked_directory_chain(self.root, parent, create_missing=False):
            final_leaf = inspect_leaf(self.root, relative)
            if final_leaf.identity is not None:
                if self._read_required_bytes(relative) != data:
                    raise StateConflictError(f"{artifact} changed")
                pending_now = inspect_leaf(self.root, pending)
                if pending_now.identity is not None:
                    unlink_if_identity(self.root, pending, pending_now.identity)
                return
            pending_now = inspect_leaf(self.root, pending)
            if pending_now.identity is None or self._read_required_bytes(pending) != data:
                raise StateConflictError(f"{artifact} pending publication changed")
            safe_replace(
                self.root,
                temporary_relative_path=pending,
                final_relative_path=relative,
                expected_temporary_identity=pending_now.identity,
            )
        if self._read_required_bytes(relative) != data:
            raise StateConflictError(f"{artifact} changed after publication")

    def _replace_projection(self, relative: str, data: bytes) -> None:
        ensure_parent_chain(self.root, relative)
        pending = self._pending_relative(relative, data)
        pending_leaf = inspect_leaf(self.root, pending)
        if pending_leaf.identity is None:
            safe_create_new_bytes(self.root, pending, data)
        elif self._read_required_bytes(pending) != data:
            raise StateConflictError("provider-stage state cache pending bytes changed")
        parent = relative.rsplit("/", 1)[0] if "/" in relative else ""
        with locked_directory_chain(self.root, parent, create_missing=False):
            pending_now = inspect_leaf(self.root, pending)
            if pending_now.identity is None or self._read_required_bytes(pending) != data:
                raise StateConflictError("provider-stage state cache pending bytes changed")
            final = inspect_leaf(self.root, relative)
            if final.identity is not None and self._read_required_bytes(relative) == data:
                unlink_if_identity(self.root, pending, pending_now.identity)
                return
            safe_replace(
                self.root,
                temporary_relative_path=pending,
                final_relative_path=relative,
                expected_temporary_identity=pending_now.identity,
            )
        if self._read_required_bytes(relative) != data:
            raise StateConflictError("provider-stage state cache replacement changed")

    def _read_required_bytes(self, relative: str) -> bytes:
        data, _ = safe_read_bytes(self.root, relative)
        return data

    def _read_optional_bytes(self, relative: str) -> bytes | None:
        leaf = inspect_leaf(self.root, relative)
        if leaf.identity is None:
            return None
        data, _ = safe_read_bytes(self.root, relative)
        return data

    @staticmethod
    def _decode_json_bytes(data: bytes, *, artifact: str) -> dict[str, Any]:
        try:
            value = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise StateConflictError(f"{artifact} is unreadable") from exc
        if not isinstance(value, dict):
            raise StateConflictError(f"{artifact} is not an object")
        return value

    @staticmethod
    def _require_exact_fields(
        value: Mapping[str, Any],
        required: set[str],
        artifact: str,
    ) -> None:
        if set(value) != required:
            raise StateConflictError(f"{artifact} shape changed")

    @staticmethod
    def _require_sha256_value(value: Any, field_name: str) -> None:
        if (
            not isinstance(value, str)
            or len(value) != 64
            or any(character not in "0123456789abcdef" for character in value)
        ):
            raise StateConflictError(f"provider-stage {field_name} changed")

    @staticmethod
    def _require_nonnegative_safe(value: Any, field_name: str) -> None:
        if type(value) is not int or not 0 <= value <= MAXIMUM_SAFE_INTEGER:
            raise StateConflictError(f"provider-stage {field_name} changed")

    @classmethod
    def _require_optional_nonnegative_safe(cls, value: Any, field_name: str) -> None:
        if value is not None:
            cls._require_nonnegative_safe(value, field_name)

    @staticmethod
    def _pending_relative(relative: str, data: bytes) -> str:
        parent, leaf = relative.rsplit("/", 1) if "/" in relative else ("", relative)
        pending_leaf = f".{leaf}.{bytes_sha256(data)}.pending"
        return f"{parent}/{pending_leaf}" if parent else pending_leaf

    @staticmethod
    def _compact_locator(chain_id: str) -> str:
        prefix = "stage-retry-"
        digest = chain_id.removeprefix(prefix) if isinstance(chain_id, str) else ""
        if (
            not isinstance(chain_id, str)
            or not chain_id.startswith(prefix)
            or len(digest) != 64
            or any(value not in "0123456789abcdef" for value in digest)
        ):
            raise ContractValidationError("provider-stage retry chain identity is invalid")
        return f"c-{digest[:32]}"

    @staticmethod
    def _logical_locator(logical_key_sha256: str) -> str:
        if (
            not isinstance(logical_key_sha256, str)
            or len(logical_key_sha256) != 64
            or any(value not in "0123456789abcdef" for value in logical_key_sha256)
        ):
            raise ContractValidationError("provider-stage logical key is invalid")
        return f"i-{logical_key_sha256[:32]}"

    def _chain_relative(self, chain_id: str) -> str:
        return f"CHAINS/{self._compact_locator(chain_id)}"

    def _authority_relative(self, chain_id: str) -> str:
        return f"{self._chain_relative(chain_id)}/AUTHORITY.json"

    def _input_relative(self, chain_id: str) -> str:
        return f"{self._chain_relative(chain_id)}/INPUT.bin"

    def _input_checkpoint_relative(self, chain_id: str) -> str:
        return f"{self._chain_relative(chain_id)}/INPUT.json"

    def _state_projection_relative(self, chain_id: str) -> str:
        return f"{self._chain_relative(chain_id)}/STATE.json"

    def _state_record_relative(self, chain_id: str, sequence: int) -> str:
        if type(sequence) is not int or not 1 <= sequence <= _MAXIMUM_STATE_RECORDS + 1:
            raise ContractValidationError("provider-stage state sequence is invalid")
        return f"{self._chain_relative(chain_id)}/STATES/{sequence:06d}.json"

    def _terminal_relative(self, chain_id: str) -> str:
        return f"{self._chain_relative(chain_id)}/TERMINAL.json"

    def _attempt_relative(self, chain_id: str, attempt_number: int) -> str:
        if (
            type(attempt_number) is not int
            or not 1 <= attempt_number <= MAXIMUM_PROVIDER_STAGE_ATTEMPTS
        ):
            raise ContractValidationError("provider-stage attempt path is invalid")
        return f"{self._chain_relative(chain_id)}/ATTEMPTS/{attempt_number:02d}"

    def _result_relative(self, chain_id: str, attempt_number: int) -> str:
        return f"{self._attempt_relative(chain_id, attempt_number)}/RESULT.bin"

    def _staged_result_relative(self, chain_id: str, attempt_number: int) -> str:
        return f"{self._attempt_relative(chain_id, attempt_number)}/RESULT.json"

    def _index_relative(self, logical_key_sha256: str) -> str:
        return f"INDEX/{self._logical_locator(logical_key_sha256)}.json"
