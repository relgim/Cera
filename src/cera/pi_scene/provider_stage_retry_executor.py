"""Single-attempt executor for exact, stage-local provider Retry custody.

This module intentionally contains no retry loop and no provider-specific
error-text classification.  A provider adapter must return one explicit typed
outcome.  Only a backend-issued manual ``provider_retry`` action can prepare a
second or third attempt.
"""

from __future__ import annotations

import secrets
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from enum import StrEnum
from threading import Lock
from typing import ClassVar, Protocol

from cera.errors import ContractValidationError, StateConflictError
from cera.generated.provider_stage_retry_contracts_v1 import (
    ProviderStageRetryActionV1,
    ProviderStageRetryStatusEnvelopeV1,
    validate_provider_stage_retry_action_v1,
    validate_provider_stage_retry_status_envelope_v1,
    validate_provider_stage_retry_status_v1,
)
from cera.serialization import canonical_sha256, domain_sha256, re_is_sha256

from .provider_stage_retry import (
    MAXIMUM_PROVIDER_STAGE_ATTEMPTS,
    NON_RETRYABLE_PROVIDER_STAGE_FAILURES,
    PRETRANSPORT_PROVIDER_STAGE_FAILURES,
    RETRYABLE_PROVIDER_STAGE_FAILURES,
    ProviderStage,
    ProviderStageAttemptPhase,
    ProviderStageFailureClass,
    ProviderStageRetryChainV1,
    ProviderStageRetryPhase,
)
from .provider_stage_retry_packets import ProviderStageFrozenPacketV1
from .provider_stage_retry_port import ProviderStageRetryStorePort
from .provider_stage_retry_scope import ProviderStageRetryOccurrenceScopeV1


def _require_sha256(value: str, field_name: str) -> None:
    if not re_is_sha256(value):
        raise ContractValidationError(f"provider-stage {field_name} must be SHA-256")


def _require_nonnegative(value: int, field_name: str) -> None:
    if type(value) is not int or value < 0:
        raise ContractValidationError(f"provider-stage {field_name} must be non-negative")


class ProviderStageSemanticDisposition(StrEnum):
    """Semantic meaning of a structurally successful provider result."""

    NOT_APPLICABLE = "not_applicable"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class ProviderStageAttemptMetricsV1:
    """Closed accounting for one submitted or possibly submitted operation."""

    ledger_prefix_after_sha256: str
    provider_operations_observed: int
    provider_operations_conservative: int
    duration_ms: int
    input_tokens: int | None = None
    cached_input_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_tokens: int | None = None

    def __post_init__(self) -> None:
        _require_sha256(self.ledger_prefix_after_sha256, "ledger prefix after")
        _require_nonnegative(self.provider_operations_observed, "observed operations")
        _require_nonnegative(
            self.provider_operations_conservative,
            "conservative operations",
        )
        if self.provider_operations_conservative < self.provider_operations_observed:
            raise ContractValidationError("provider-stage conservative accounting undercounts")
        _require_nonnegative(self.duration_ms, "duration")
        for value, field_name in (
            (self.input_tokens, "input tokens"),
            (self.cached_input_tokens, "cached input tokens"),
            (self.output_tokens, "output tokens"),
            (self.reasoning_tokens, "reasoning tokens"),
        ):
            if value is not None:
                _require_nonnegative(value, field_name)
        if self.cached_input_tokens is not None and (
            self.input_tokens is None or self.cached_input_tokens > self.input_tokens
        ):
            raise ContractValidationError("provider-stage cached input exceeds total input")


@dataclass(frozen=True, slots=True)
class ProviderStageSuccessfulResultV1:
    """Structurally successful provider output, including semantic rejection."""

    exact_result: bytes = field(repr=False)
    result_evidence_sha256: str
    metrics: ProviderStageAttemptMetricsV1
    semantic_disposition: ProviderStageSemanticDisposition

    def __post_init__(self) -> None:
        if not isinstance(self.exact_result, bytes) or not self.exact_result:
            raise ContractValidationError("provider-stage result must be non-empty bytes")
        _require_sha256(self.result_evidence_sha256, "result evidence")
        if type(self.metrics) is not ProviderStageAttemptMetricsV1:
            raise ContractValidationError("provider-stage success metrics changed")
        if type(self.semantic_disposition) is not ProviderStageSemanticDisposition:
            raise ContractValidationError("provider-stage semantic disposition is not closed")
        if (
            self.metrics.provider_operations_observed < 1
            or self.metrics.provider_operations_conservative
            != self.metrics.provider_operations_observed
        ):
            raise ContractValidationError(
                "provider-stage success lacks closed observed operation accounting"
            )


@dataclass(frozen=True, slots=True)
class ProviderStageClosedFailureV1:
    """Confirmed retryable failure with closed provider disposition."""

    failure_class: ProviderStageFailureClass
    failure_evidence_sha256: str
    metrics: ProviderStageAttemptMetricsV1

    def __post_init__(self) -> None:
        if self.failure_class not in RETRYABLE_PROVIDER_STAGE_FAILURES:
            raise ContractValidationError("provider-stage failure is not closed and retryable")
        _require_sha256(self.failure_evidence_sha256, "failure evidence")
        if type(self.metrics) is not ProviderStageAttemptMetricsV1:
            raise ContractValidationError("provider-stage failure metrics changed")
        if (
            self.metrics.provider_operations_conservative
            != self.metrics.provider_operations_observed
        ):
            raise ContractValidationError("closed provider-stage failure has unresolved accounting")
        if (
            self.failure_class
            in {
                ProviderStageFailureClass.PROVIDER_STREAM_INCOMPLETE,
                ProviderStageFailureClass.PROVIDER_COMPLETION_INCOMPLETE,
                ProviderStageFailureClass.PROVIDER_OUTPUT_INVALID,
            }
            and self.metrics.provider_operations_observed < 1
        ):
            raise ContractValidationError(
                "provider-stage post-operation failure lacks an observed provider operation"
            )


@dataclass(frozen=True, slots=True)
class ProviderStagePretransportFailureV1:
    """Proven failure before provider transport; it consumes zero operations."""

    failure_class: ProviderStageFailureClass
    failure_evidence_sha256: str
    ledger_prefix_after_sha256: str
    duration_ms: int

    def __post_init__(self) -> None:
        if self.failure_class not in PRETRANSPORT_PROVIDER_STAGE_FAILURES:
            raise ContractValidationError("failure cannot occur before provider transport")
        _require_sha256(self.failure_evidence_sha256, "pretransport failure evidence")
        _require_sha256(self.ledger_prefix_after_sha256, "pretransport ledger prefix after")
        _require_nonnegative(self.duration_ms, "pretransport duration")


@dataclass(frozen=True, slots=True)
class ProviderStagePretransportNonRetryableFailureV1:
    """Known non-Retry failure before transport; provider accounting is zero."""

    failure_class: ProviderStageFailureClass
    failure_evidence_sha256: str
    ledger_prefix_after_sha256: str
    duration_ms: int

    def __post_init__(self) -> None:
        if self.failure_class not in NON_RETRYABLE_PROVIDER_STAGE_FAILURES:
            raise ContractValidationError("pretransport failure is not a known non-Retry terminal")
        _require_sha256(self.failure_evidence_sha256, "pretransport non-Retry evidence")
        _require_sha256(
            self.ledger_prefix_after_sha256,
            "pretransport non-Retry ledger prefix after",
        )
        _require_nonnegative(self.duration_ms, "pretransport non-Retry duration")


@dataclass(frozen=True, slots=True)
class ProviderStageNonRetryableFailureV1:
    """Accepted stage attempt with a known terminal non-Retry disposition."""

    failure_class: ProviderStageFailureClass
    failure_evidence_sha256: str
    metrics: ProviderStageAttemptMetricsV1

    def __post_init__(self) -> None:
        if self.failure_class not in NON_RETRYABLE_PROVIDER_STAGE_FAILURES:
            raise ContractValidationError(
                "provider-stage failure is not a known non-Retry terminal"
            )
        _require_sha256(self.failure_evidence_sha256, "non-Retry failure evidence")
        if type(self.metrics) is not ProviderStageAttemptMetricsV1:
            raise ContractValidationError("provider-stage non-Retry failure metrics changed")
        if (
            self.metrics.provider_operations_conservative
            != self.metrics.provider_operations_observed
        ):
            raise ContractValidationError(
                "non-Retry failure lacks closed provider-operation accounting"
            )


@dataclass(frozen=True, slots=True)
class ProviderStageDispatchAmbiguousV1:
    """Unresolved transport disposition; another provider call is forbidden."""

    failure_evidence_sha256: str
    metrics: ProviderStageAttemptMetricsV1

    def __post_init__(self) -> None:
        _require_sha256(self.failure_evidence_sha256, "dispatch ambiguity evidence")
        if type(self.metrics) is not ProviderStageAttemptMetricsV1:
            raise ContractValidationError("provider-stage ambiguity metrics changed")
        if self.metrics.provider_operations_conservative < 1:
            raise ContractValidationError("provider-stage ambiguity lacks conservative accounting")


type ProviderStageAttemptOutcomeV1 = (
    ProviderStageSuccessfulResultV1
    | ProviderStageClosedFailureV1
    | ProviderStageNonRetryableFailureV1
    | ProviderStageDispatchAmbiguousV1
)


class PreparedProviderStageDispatchPort(Protocol):
    """One prepared invocation that has not yet contacted the provider."""

    @property
    def dispatch_evidence_sha256(self) -> str: ...

    def invoke(self) -> ProviderStageAttemptOutcomeV1: ...


type ProviderStagePreparationV1 = (
    PreparedProviderStageDispatchPort
    | ProviderStagePretransportFailureV1
    | ProviderStagePretransportNonRetryableFailureV1
)


class ProviderStageAttemptOwnerPort(Protocol):
    """Fresh owner for exactly one stage attempt and physical session scope.

    ``prepare`` performs local deterministic preparation only.  Provider
    transport is forbidden until the executor durably wins
    ``mark_dispatch_started`` and calls the returned dispatch's ``invoke``.
    """

    @property
    def session_scope_sha256(self) -> str: ...

    @property
    def ledger_prefix_before_sha256(self) -> str: ...

    @property
    def maximum_provider_operations(self) -> int: ...

    def prepare(
        self,
        *,
        chain_id: str,
        attempt_number: int,
        exact_input: bytes,
    ) -> ProviderStagePreparationV1: ...

    def retire(
        self,
        *,
        chain_id: str,
        attempt_number: int,
        failure_class: ProviderStageFailureClass,
    ) -> str:
        """Idempotently fence this exact owner and return durable evidence."""
        ...


@dataclass(frozen=True, slots=True)
class ProviderStageAmbiguityResolutionV1:
    """Provider-free evidence resolving an already fenced ambiguous dispatch."""

    resolution_evidence_sha256: str
    disposition: (
        ProviderStageSuccessfulResultV1
        | ProviderStageClosedFailureV1
        | ProviderStageNonRetryableFailureV1
    )

    def __post_init__(self) -> None:
        _require_sha256(self.resolution_evidence_sha256, "ambiguity resolution evidence")
        if type(self.disposition) not in {
            ProviderStageSuccessfulResultV1,
            ProviderStageClosedFailureV1,
            ProviderStageNonRetryableFailureV1,
        }:
            raise ContractValidationError("provider-stage ambiguity resolution is not closed")


class ProviderStageRetryExecutorV1:
    """Backend authority joining exact packets, one attempt, and shared status."""

    _begin_locks_guard: ClassVar[Lock] = Lock()
    _begin_locks: ClassVar[dict[str, Lock]] = {}

    def __init__(
        self,
        store: ProviderStageRetryStorePort,
        *,
        dispatch_nonce_factory: Callable[[], str] | None = None,
    ) -> None:
        self._store = store
        self._dispatch_nonce_factory = dispatch_nonce_factory or (lambda: secrets.token_hex(32))

    def execute_initial(
        self,
        *,
        scope: ProviderStageRetryOccurrenceScopeV1,
        packet: ProviderStageFrozenPacketV1,
        owner: ProviderStageAttemptOwnerPort,
    ) -> ProviderStageRetryChainV1:
        """Run only the initial attempt, or replay already-durable chain state."""

        self._require_scope_packet(scope, packet)
        with self._begin_lock(scope.identity.chain_id):
            chain = self._store.begin(scope.identity, packet.exact_bytes)
        if chain.phase is ProviderStageRetryPhase.ATTEMPT_PREPARED:
            current = chain.attempts[-1]
            if (
                current.session_scope_sha256 == owner.session_scope_sha256
                and current.ledger_prefix_before_sha256 == owner.ledger_prefix_before_sha256
            ):
                return self._execute_prepared(chain, owner)
            return chain
        if chain.phase is not ProviderStageRetryPhase.INPUT_FROZEN:
            return chain
        try:
            prepared = self._store.prepare_attempt(
                chain.chain_id,
                session_scope_sha256=owner.session_scope_sha256,
                ledger_prefix_before_sha256=owner.ledger_prefix_before_sha256,
            )
        except StateConflictError:
            concurrent = self._store.read(chain.chain_id)
            if concurrent.phase is not ProviderStageRetryPhase.INPUT_FROZEN:
                return concurrent
            raise
        return self._execute_prepared(prepared, owner)

    def execute_manual_retry(
        self,
        *,
        action: object,
        owner: ProviderStageAttemptOwnerPort,
    ) -> ProviderStageRetryChainV1:
        """Accept and run at most one manually authorized Retry attempt."""

        validated = validate_provider_stage_retry_action_v1(action)
        self._require_action_kind(validated, "provider_retry")
        chain_id = validated["chain_id"]
        chain = self._store.read(chain_id)
        # The persisted action and resulting attempt are committed in one
        # transaction.  Derive the accepted count from this same immutable
        # chain snapshot so a concurrent accept cannot combine a stale chain
        # with a newer COUNT(*) result.
        retry_count = chain.retries_consumed
        ordinal = validated["retry_action_ordinal"]
        assert isinstance(ordinal, int)
        action_sha256 = canonical_sha256(validated)

        if ordinal <= retry_count:
            prior = self._eligible_chain_before_retry(chain, ordinal)
            expected = self._build_action(
                prior,
                action_kind="provider_retry",
                retry_action_ordinal=ordinal,
            )
            if validated != expected or len(chain.attempts) <= ordinal:
                raise StateConflictError("provider-stage Retry action was not backend-issued")
            persisted = chain.attempts[ordinal]
            accepted = self._store.accept_retry(
                chain_id,
                retry_action_sha256=action_sha256,
                session_scope_sha256=persisted.session_scope_sha256,
                ledger_prefix_before_sha256=persisted.ledger_prefix_before_sha256,
            )
            if accepted.phase is ProviderStageRetryPhase.ATTEMPT_PREPARED and (
                persisted.session_scope_sha256 == owner.session_scope_sha256
                and persisted.ledger_prefix_before_sha256 == owner.ledger_prefix_before_sha256
            ):
                return self._execute_prepared(accepted, owner)
            return accepted
        if ordinal != retry_count + 1 or ordinal > 2:
            raise StateConflictError("provider-stage Retry ordinal changed")
        if chain.phase is not ProviderStageRetryPhase.OWNER_RETIRED:
            raise StateConflictError("provider-stage chain is not eligible for Retry")
        expected = self._build_action(
            chain,
            action_kind="provider_retry",
            retry_action_ordinal=ordinal,
        )
        if validated != expected:
            raise StateConflictError("provider-stage Retry action is stale or not backend-issued")
        try:
            prepared = self._store.accept_retry(
                chain_id,
                retry_action_sha256=action_sha256,
                session_scope_sha256=owner.session_scope_sha256,
                ledger_prefix_before_sha256=owner.ledger_prefix_before_sha256,
            )
        except StateConflictError:
            # A concurrent duplicate may have durably accepted this exact
            # action with another fresh owner.  Re-read, verify the action
            # against history, and never let the losing owner dispatch.
            replay = self._store.read(chain_id)
            replay_count = replay.retries_consumed
            if replay_count < ordinal or len(replay.attempts) <= ordinal:
                raise
            replay_prior = self._eligible_chain_before_retry(replay, ordinal)
            replay_expected = self._build_action(
                replay_prior,
                action_kind="provider_retry",
                retry_action_ordinal=ordinal,
            )
            if validated != replay_expected:
                raise
            persisted = replay.attempts[ordinal]
            return self._store.accept_retry(
                chain_id,
                retry_action_sha256=action_sha256,
                session_scope_sha256=persisted.session_scope_sha256,
                ledger_prefix_before_sha256=persisted.ledger_prefix_before_sha256,
            )
        return self._execute_prepared(prepared, owner)

    def resume_prepared_attempt(
        self,
        *,
        chain_id: str,
        owner: ProviderStageAttemptOwnerPort,
    ) -> ProviderStageRetryChainV1:
        """Explicitly resume one prepared, never-dispatched persisted owner."""

        chain = self._store.read(chain_id)
        if chain.phase is not ProviderStageRetryPhase.ATTEMPT_PREPARED:
            raise StateConflictError("provider-stage attempt is not safely resumable")
        current = chain.attempts[-1]
        if (
            current.session_scope_sha256 != owner.session_scope_sha256
            or current.ledger_prefix_before_sha256 != owner.ledger_prefix_before_sha256
        ):
            raise StateConflictError("provider-stage prepared owner changed")
        return self._execute_prepared(chain, owner)

    def close_dispatch_ambiguity(
        self,
        *,
        chain_id: str,
        owner: ProviderStageAttemptOwnerPort,
        ambiguity: ProviderStageDispatchAmbiguousV1,
    ) -> ProviderStageRetryChainV1:
        """Fence an invocation that escaped a typed adapter outcome."""

        return self.recover_dispatched_attempt(
            chain_id=chain_id,
            owner=owner,
            outcome=ambiguity,
        )

    def recover_dispatched_attempt(
        self,
        *,
        chain_id: str,
        owner: ProviderStageAttemptOwnerPort,
        outcome: ProviderStageAttemptOutcomeV1,
    ) -> ProviderStageRetryChainV1:
        """Persist one injected provider-free disposition after restart."""

        chain = self._store.read(chain_id)
        if chain.phase is not ProviderStageRetryPhase.DISPATCH_STARTED:
            raise StateConflictError("provider-stage dispatch is not awaiting a disposition")
        current = chain.attempts[-1]
        if (
            current.session_scope_sha256 != owner.session_scope_sha256
            or current.ledger_prefix_before_sha256 != owner.ledger_prefix_before_sha256
            or current.provider_operations_conservative != owner.maximum_provider_operations
        ):
            raise StateConflictError("provider-stage recovered owner changed")
        attempt_number = current.attempt_number
        if type(outcome) is ProviderStageSuccessfulResultV1:
            return self._freeze_result(chain.chain_id, attempt_number, outcome)
        if type(outcome) is ProviderStageNonRetryableFailureV1:
            return self._close_non_retryable_failure(chain, owner, outcome)
        if isinstance(
            outcome,
            (ProviderStageClosedFailureV1, ProviderStageDispatchAmbiguousV1),
        ):
            return self._close_failure(chain, owner, outcome)
        raise ContractValidationError(
            "provider-stage restart reconciler returned an open disposition"
        )

    def retire_failed_owner(
        self,
        *,
        chain_id: str,
        owner: ProviderStageAttemptOwnerPort,
    ) -> ProviderStageRetryChainV1:
        """Provider-free restart completion for an already closed failure."""

        chain = self._store.read(chain_id)
        if chain.phase is not ProviderStageRetryPhase.AWAITING_OWNER_RETIREMENT:
            return chain
        current = chain.attempts[-1]
        if (
            current.session_scope_sha256 != owner.session_scope_sha256
            or current.ledger_prefix_before_sha256 != owner.ledger_prefix_before_sha256
            or current.failure_class is None
        ):
            raise StateConflictError("provider-stage retiring owner changed")
        return self._retire(chain, owner, current.failure_class)

    def reconcile_ambiguous(
        self,
        *,
        action: object,
        resolution: ProviderStageAmbiguityResolutionV1 | None,
    ) -> ProviderStageRetryChainV1:
        """Check or resolve ambiguity without dispatching a provider."""

        validated = validate_provider_stage_retry_action_v1(action)
        self._require_action_kind(validated, "check_status")
        chain = self._store.read(validated["chain_id"])
        if chain.phase is not ProviderStageRetryPhase.BLOCKED_AMBIGUOUS:
            # Idempotent replay after a prior provider-free resolution.
            return chain
        expected = self._build_action(
            chain,
            action_kind="check_status",
            retry_action_ordinal=None,
        )
        if validated != expected:
            raise StateConflictError(
                "provider-stage Check Status action is stale or not backend-issued"
            )
        if resolution is None:
            return chain
        if type(resolution) is not ProviderStageAmbiguityResolutionV1:
            raise ContractValidationError("provider-stage ambiguity resolution changed")
        disposition = resolution.disposition
        metrics = disposition.metrics
        if type(disposition) is ProviderStageSuccessfulResultV1:
            return self._store.resolve_blocked_result(
                chain.chain_id,
                resolution_evidence_sha256=resolution.resolution_evidence_sha256,
                exact_result=disposition.exact_result,
                result_evidence_sha256=disposition.result_evidence_sha256,
                ledger_prefix_after_sha256=metrics.ledger_prefix_after_sha256,
                provider_operations_observed=metrics.provider_operations_observed,
                provider_operations_conservative=metrics.provider_operations_conservative,
                duration_ms=metrics.duration_ms,
                input_tokens=metrics.input_tokens,
                cached_input_tokens=metrics.cached_input_tokens,
                output_tokens=metrics.output_tokens,
                reasoning_tokens=metrics.reasoning_tokens,
            )
        if type(disposition) is ProviderStageNonRetryableFailureV1:
            return self._store.resolve_blocked_non_retryable_failure(
                chain.chain_id,
                resolution_evidence_sha256=resolution.resolution_evidence_sha256,
                failure_class=disposition.failure_class,
                failure_evidence_sha256=disposition.failure_evidence_sha256,
                ledger_prefix_after_sha256=metrics.ledger_prefix_after_sha256,
                provider_operations_observed=metrics.provider_operations_observed,
                provider_operations_conservative=metrics.provider_operations_conservative,
                duration_ms=metrics.duration_ms,
                input_tokens=metrics.input_tokens,
                cached_input_tokens=metrics.cached_input_tokens,
                output_tokens=metrics.output_tokens,
                reasoning_tokens=metrics.reasoning_tokens,
            )
        assert type(disposition) is ProviderStageClosedFailureV1
        if (
            chain.identity.stage is ProviderStage.RECORDER
            and chain.attempts_total == MAXIMUM_PROVIDER_STAGE_ATTEMPTS
        ):
            return self._store.resolve_blocked_recording_repair(
                chain.chain_id,
                resolution_evidence_sha256=resolution.resolution_evidence_sha256,
                failure_class=disposition.failure_class,
                failure_evidence_sha256=disposition.failure_evidence_sha256,
                ledger_prefix_after_sha256=metrics.ledger_prefix_after_sha256,
                provider_operations_observed=metrics.provider_operations_observed,
                provider_operations_conservative=metrics.provider_operations_conservative,
                duration_ms=metrics.duration_ms,
                input_tokens=metrics.input_tokens,
                cached_input_tokens=metrics.cached_input_tokens,
                output_tokens=metrics.output_tokens,
                reasoning_tokens=metrics.reasoning_tokens,
            )
        return self._store.resolve_blocked_failure(
            chain.chain_id,
            resolution_evidence_sha256=resolution.resolution_evidence_sha256,
            failure_class=disposition.failure_class,
            failure_evidence_sha256=disposition.failure_evidence_sha256,
            ledger_prefix_after_sha256=metrics.ledger_prefix_after_sha256,
            provider_operations_observed=metrics.provider_operations_observed,
            provider_operations_conservative=metrics.provider_operations_conservative,
            duration_ms=metrics.duration_ms,
            input_tokens=metrics.input_tokens,
            cached_input_tokens=metrics.cached_input_tokens,
            output_tokens=metrics.output_tokens,
            reasoning_tokens=metrics.reasoning_tokens,
        )

    def bind_downstream_once(
        self,
        *,
        chain_id: str,
        downstream_intent_sha256: str,
        downstream_evidence_sha256: str,
    ) -> ProviderStageRetryChainV1:
        """Bind an already-frozen result to one idempotent downstream effect."""

        chain = self._store.freeze_downstream_intent(
            chain_id,
            downstream_intent_sha256=downstream_intent_sha256,
        )
        chain = self._store.bind_downstream(
            chain.chain_id,
            downstream_evidence_sha256=downstream_evidence_sha256,
        )
        return self._store.mark_succeeded(chain.chain_id)

    def load_exact_result(self, chain_id: str) -> bytes:
        return self._store.load_result(chain_id)

    def record_late_result(
        self,
        *,
        chain_id: str,
        attempt_number: int,
        result: ProviderStageSuccessfulResultV1,
    ) -> ProviderStageRetryChainV1:
        """Attempt to bind a late result; the fenced store must reject it."""

        chain = self._store.read(chain_id)
        if not 1 <= attempt_number <= len(chain.attempts):
            raise StateConflictError("provider-stage late result attempt is unavailable")
        if chain.attempts[attempt_number - 1].phase in {
            ProviderStageAttemptPhase.FAILED,
            ProviderStageAttemptPhase.OWNER_RETIRED,
        }:
            raise StateConflictError("provider-stage late result owner is fenced")
        return self._freeze_result(chain_id, attempt_number, result)

    def status_envelope(
        self,
        scope: ProviderStageRetryOccurrenceScopeV1,
    ) -> ProviderStageRetryStatusEnvelopeV1:
        """Build and generated-schema validate one authoritative UI envelope."""

        chain = self._store.read(scope.identity.chain_id)
        if chain.identity != scope.identity:
            raise StateConflictError("provider-stage status scope changed")
        if (
            chain.attempts_total < 1
            and chain.phase is not ProviderStageRetryPhase.RECOVERY_REQUIRED
        ):
            raise StateConflictError("provider-stage status is unavailable before attempt one")
        retry_count = chain.retries_consumed
        state, failure_category, available_action = self._public_state(chain)
        status_payload: dict[str, object] = {
            "schema_version": "cera.provider_stage_retry_status.v1",
            "chain_id": chain.chain_id,
            "provider": chain.identity.provider.value,
            "model_family": chain.identity.model_family.value,
            "stage": chain.identity.stage.value,
            "state": state,
            "maximum_attempts": MAXIMUM_PROVIDER_STAGE_ATTEMPTS,
            "stage_attempts_total": chain.attempts_total,
            "retry_actions_accepted": retry_count,
            "provider_operations_observed_total": chain.provider_operations_observed_total,
            "provider_operations_conservative_total": (
                chain.provider_operations_conservative_total
            ),
            "story_state_committed": chain.identity.story_state_committed,
            "branch_preserved_at_last_accepted_head": True,
            "failure_category": failure_category,
            "available_actions": [] if available_action is None else [available_action],
            "technical_details": {
                "schema_version": "cera.provider_stage_retry_technical_details.v1",
                "request_occurrence_sha256": chain.identity.request_occurrence_sha256,
                "request_sha256": chain.identity.request_sha256,
                "stage_input_sha256": chain.identity.stage_input_sha256,
                "accepted_state_sha256": scope.accepted_state_sha256,
                "chain_sha256": chain.chain_sha256,
            },
        }
        status = validate_provider_stage_retry_status_v1(status_payload)
        action_payloads: list[dict[str, object]] = []
        if available_action is not None:
            action = self._build_action(
                chain,
                action_kind=available_action,
                retry_action_ordinal=(
                    retry_count + 1 if available_action == "provider_retry" else None
                ),
            )
            action_payloads.append(dict(action))
        envelope: dict[str, object] = {
            "schema_version": "cera.provider_stage_retry_status_envelope.v1",
            "status": dict(status),
            "actions": action_payloads,
        }
        return validate_provider_stage_retry_status_envelope_v1(envelope)

    def _execute_prepared(
        self,
        chain: ProviderStageRetryChainV1,
        owner: ProviderStageAttemptOwnerPort,
    ) -> ProviderStageRetryChainV1:
        if chain.phase is not ProviderStageRetryPhase.ATTEMPT_PREPARED:
            raise StateConflictError("provider-stage attempt is not prepared")
        attempt = chain.attempts[-1]
        if (
            attempt.session_scope_sha256 != owner.session_scope_sha256
            or attempt.ledger_prefix_before_sha256 != owner.ledger_prefix_before_sha256
        ):
            raise StateConflictError("provider-stage attempt owner changed")
        exact_input = self._store.load_input(chain.chain_id)
        preparation = owner.prepare(
            chain_id=chain.chain_id,
            attempt_number=attempt.attempt_number,
            exact_input=exact_input,
        )
        if isinstance(preparation, ProviderStagePretransportFailureV1):
            failed = self._store.mark_pretransport_failed(
                chain.chain_id,
                attempt_number=attempt.attempt_number,
                failure_class=preparation.failure_class,
                failure_evidence_sha256=preparation.failure_evidence_sha256,
                ledger_prefix_after_sha256=preparation.ledger_prefix_after_sha256,
                duration_ms=preparation.duration_ms,
            )
            return self._retire(failed, owner, preparation.failure_class)
        if isinstance(preparation, ProviderStagePretransportNonRetryableFailureV1):
            failed = self._store.mark_pretransport_non_retryable_failed(
                chain.chain_id,
                attempt_number=attempt.attempt_number,
                failure_class=preparation.failure_class,
                failure_evidence_sha256=preparation.failure_evidence_sha256,
                ledger_prefix_after_sha256=preparation.ledger_prefix_after_sha256,
                duration_ms=preparation.duration_ms,
            )
            return self._retire(failed, owner, preparation.failure_class)

        dispatch_evidence = preparation.dispatch_evidence_sha256
        _require_sha256(dispatch_evidence, "dispatch evidence")
        nonce = self._dispatch_nonce_factory()
        if not isinstance(nonce, str) or not nonce or "\x00" in nonce:
            raise ContractValidationError("provider-stage dispatch nonce is invalid")
        reservation_evidence = domain_sha256(
            "cera.provider_stage_retry_dispatch_reservation.v1",
            {
                "chain_id": chain.chain_id,
                "attempt_number": attempt.attempt_number,
                "prepared_dispatch_evidence_sha256": dispatch_evidence,
                "nonce": nonce,
            },
        )
        try:
            dispatched = self._store.mark_dispatch_started(
                chain.chain_id,
                attempt_number=attempt.attempt_number,
                dispatch_evidence_sha256=reservation_evidence,
                maximum_provider_operations=owner.maximum_provider_operations,
            )
        except StateConflictError:
            # Unique reservation evidence makes the durable state transition a
            # dispatch claim.  A concurrent loser observes the winner's state
            # and must not invoke even when both prepared the same owner.
            live = self._store.read(chain.chain_id)
            if live.phase is not ProviderStageRetryPhase.ATTEMPT_PREPARED:
                return live
            raise
        outcome = preparation.invoke()
        if isinstance(outcome, ProviderStageSuccessfulResultV1):
            return self._freeze_result(chain.chain_id, attempt.attempt_number, outcome)
        if isinstance(outcome, ProviderStageNonRetryableFailureV1):
            return self._close_non_retryable_failure(dispatched, owner, outcome)
        if isinstance(outcome, (ProviderStageClosedFailureV1, ProviderStageDispatchAmbiguousV1)):
            return self._close_failure(dispatched, owner, outcome)
        raise ContractValidationError("provider-stage adapter returned an unknown disposition")

    def _close_failure(
        self,
        chain: ProviderStageRetryChainV1,
        owner: ProviderStageAttemptOwnerPort,
        outcome: ProviderStageClosedFailureV1 | ProviderStageDispatchAmbiguousV1,
    ) -> ProviderStageRetryChainV1:
        attempt_number = chain.attempts[-1].attempt_number
        metrics = outcome.metrics
        failure_class = (
            outcome.failure_class
            if type(outcome) is ProviderStageClosedFailureV1
            else ProviderStageFailureClass.DISPATCH_AMBIGUOUS
        )
        failed = self._store.mark_attempt_failed(
            chain.chain_id,
            attempt_number=attempt_number,
            failure_class=failure_class,
            failure_evidence_sha256=outcome.failure_evidence_sha256,
            ledger_prefix_after_sha256=metrics.ledger_prefix_after_sha256,
            provider_operations_observed=metrics.provider_operations_observed,
            provider_operations_conservative=metrics.provider_operations_conservative,
            duration_ms=metrics.duration_ms,
            input_tokens=metrics.input_tokens,
            cached_input_tokens=metrics.cached_input_tokens,
            output_tokens=metrics.output_tokens,
            reasoning_tokens=metrics.reasoning_tokens,
        )
        return self._retire(failed, owner, failure_class)

    def _close_non_retryable_failure(
        self,
        chain: ProviderStageRetryChainV1,
        owner: ProviderStageAttemptOwnerPort,
        outcome: ProviderStageNonRetryableFailureV1,
    ) -> ProviderStageRetryChainV1:
        attempt_number = chain.attempts[-1].attempt_number
        metrics = outcome.metrics
        failed = self._store.mark_non_retryable_failed(
            chain.chain_id,
            attempt_number=attempt_number,
            failure_class=outcome.failure_class,
            failure_evidence_sha256=outcome.failure_evidence_sha256,
            ledger_prefix_after_sha256=metrics.ledger_prefix_after_sha256,
            provider_operations_observed=metrics.provider_operations_observed,
            provider_operations_conservative=metrics.provider_operations_conservative,
            duration_ms=metrics.duration_ms,
            input_tokens=metrics.input_tokens,
            cached_input_tokens=metrics.cached_input_tokens,
            output_tokens=metrics.output_tokens,
            reasoning_tokens=metrics.reasoning_tokens,
        )
        return self._retire(failed, owner, outcome.failure_class)

    def _retire(
        self,
        chain: ProviderStageRetryChainV1,
        owner: ProviderStageAttemptOwnerPort,
        failure_class: ProviderStageFailureClass,
    ) -> ProviderStageRetryChainV1:
        attempt_number = chain.attempts[-1].attempt_number
        retirement_evidence = owner.retire(
            chain_id=chain.chain_id,
            attempt_number=attempt_number,
            failure_class=failure_class,
        )
        _require_sha256(retirement_evidence, "owner-retirement evidence")
        return self._store.mark_owner_retired(
            chain.chain_id,
            attempt_number=attempt_number,
            retirement_evidence_sha256=retirement_evidence,
        )

    def _freeze_result(
        self,
        chain_id: str,
        attempt_number: int,
        result: ProviderStageSuccessfulResultV1,
    ) -> ProviderStageRetryChainV1:
        return self._store.freeze_result(
            chain_id,
            attempt_number=attempt_number,
            exact_result=result.exact_result,
            result_evidence_sha256=result.result_evidence_sha256,
            ledger_prefix_after_sha256=result.metrics.ledger_prefix_after_sha256,
            provider_operations_observed=result.metrics.provider_operations_observed,
            provider_operations_conservative=(result.metrics.provider_operations_conservative),
            duration_ms=result.metrics.duration_ms,
            input_tokens=result.metrics.input_tokens,
            cached_input_tokens=result.metrics.cached_input_tokens,
            output_tokens=result.metrics.output_tokens,
            reasoning_tokens=result.metrics.reasoning_tokens,
        )

    @staticmethod
    def _require_scope_packet(
        scope: ProviderStageRetryOccurrenceScopeV1,
        packet: ProviderStageFrozenPacketV1,
    ) -> None:
        if (
            type(scope) is not ProviderStageRetryOccurrenceScopeV1
            or type(packet) is not ProviderStageFrozenPacketV1
            or scope.stage is not packet.stage
            or scope.stage_input_sha256 != packet.stage_input_sha256
        ):
            raise StateConflictError("provider-stage frozen packet changed occurrence scope")

    @staticmethod
    def _require_action_kind(action: ProviderStageRetryActionV1, kind: str) -> None:
        if action["action_kind"] != kind:
            raise StateConflictError(f"provider-stage action is not {kind}")

    @staticmethod
    def _build_action(
        chain: ProviderStageRetryChainV1,
        *,
        action_kind: str,
        retry_action_ordinal: int | None,
    ) -> ProviderStageRetryActionV1:
        digest = domain_sha256(
            "cera.provider_stage_retry_backend_action.v1",
            {
                "chain_id": chain.chain_id,
                "action_kind": action_kind,
                "retry_action_ordinal": retry_action_ordinal,
                "expected_chain_sha256": chain.chain_sha256,
            },
        )
        provider_retry = action_kind == "provider_retry"
        payload: dict[str, object] = {
            "schema_version": "cera.provider_stage_retry_action.v1",
            "action_id": "stage-action-" + digest,
            "chain_id": chain.chain_id,
            "action_family": "provider_stage_control",
            "action_kind": action_kind,
            "automatic": False,
            "provider_dispatch_authorized": provider_retry,
            "consumes_retry_action": provider_retry,
            "retry_action_ordinal": retry_action_ordinal,
            "whole_request_replay_authorized": False,
            "provider_substitution_authorized": False,
            "expected_chain_sha256": chain.chain_sha256,
        }
        return validate_provider_stage_retry_action_v1(payload)

    @classmethod
    def _begin_lock(cls, chain_id: str) -> Lock:
        # One trusted local backend process is the approved deployment model.
        # This prevents duplicate initial requests from racing same-chain
        # protected-blob publication before SQLite identity custody exists.
        with cls._begin_locks_guard:
            return cls._begin_locks.setdefault(chain_id, Lock())

    @staticmethod
    def _eligible_chain_before_retry(
        chain: ProviderStageRetryChainV1,
        retry_action_ordinal: int,
    ) -> ProviderStageRetryChainV1:
        if not 1 <= retry_action_ordinal <= 2 or len(chain.attempts) <= retry_action_ordinal:
            raise StateConflictError("provider-stage Retry history is incomplete")
        retained = chain.attempts[:retry_action_ordinal]
        if retained[-1].phase is not ProviderStageAttemptPhase.OWNER_RETIRED:
            raise StateConflictError("provider-stage prior Retry owner was not fenced")
        return replace(
            chain,
            phase=ProviderStageRetryPhase.OWNER_RETIRED,
            attempts=retained,
            result_checkpoint=None,
            downstream_intent_sha256=None,
            downstream_evidence_sha256=None,
            block_reason=None,
            block_evidence_sha256=None,
        )

    @staticmethod
    def _public_state(
        chain: ProviderStageRetryChainV1,
    ) -> tuple[str, str | None, str | None]:
        if chain.phase is ProviderStageRetryPhase.OWNER_RETIRED:
            failure = chain.attempts[-1].failure_class
            if failure not in RETRYABLE_PROVIDER_STAGE_FAILURES:
                raise StateConflictError("eligible provider-stage failure is not closed")
            return "eligible", failure.value, "provider_retry"
        if chain.phase in {
            ProviderStageRetryPhase.ATTEMPT_PREPARED,
            ProviderStageRetryPhase.DISPATCH_STARTED,
            ProviderStageRetryPhase.AWAITING_OWNER_RETIREMENT,
        }:
            return "in_progress", None, None
        if chain.phase in {
            ProviderStageRetryPhase.RESULT_FROZEN,
            ProviderStageRetryPhase.DOWNSTREAM_INTENT_FROZEN,
            ProviderStageRetryPhase.DOWNSTREAM_BOUND,
        }:
            return "in_progress", None, None
        if chain.phase is ProviderStageRetryPhase.SUCCEEDED:
            return "succeeded", None, None
        if chain.phase is ProviderStageRetryPhase.BLOCKED_AMBIGUOUS:
            return "blocked_ambiguous", "dispatch_ambiguous", "check_status"
        if chain.phase is ProviderStageRetryPhase.EXHAUSTED:
            failure = chain.attempts[-1].failure_class
            if failure not in RETRYABLE_PROVIDER_STAGE_FAILURES:
                raise StateConflictError("exhausted provider-stage failure is not closed")
            return "attempts_exhausted", failure.value, None
        if chain.phase is ProviderStageRetryPhase.RECORDING_REPAIR_REQUIRED:
            failure = chain.attempts[-1].failure_class
            if failure not in RETRYABLE_PROVIDER_STAGE_FAILURES:
                raise StateConflictError("Recorder repair failure is not closed")
            return "recording_repair_required", failure.value, "repair_recording"
        if chain.phase is ProviderStageRetryPhase.RECOVERY_REQUIRED:
            if chain.block_reason is not None:
                failure_category = chain.block_reason.value
            else:
                failure = chain.attempts[-1].failure_class
                if failure not in NON_RETRYABLE_PROVIDER_STAGE_FAILURES:
                    raise StateConflictError("provider-stage recovery reason is not closed")
                failure_category = failure.value
            return "recovery_required", failure_category, None
        raise StateConflictError("provider-stage public status is unavailable")


__all__ = [
    "PreparedProviderStageDispatchPort",
    "ProviderStageAmbiguityResolutionV1",
    "ProviderStageAttemptMetricsV1",
    "ProviderStageAttemptOutcomeV1",
    "ProviderStageAttemptOwnerPort",
    "ProviderStageClosedFailureV1",
    "ProviderStageDispatchAmbiguousV1",
    "ProviderStageNonRetryableFailureV1",
    "ProviderStagePretransportFailureV1",
    "ProviderStagePretransportNonRetryableFailureV1",
    "ProviderStagePreparationV1",
    "ProviderStageRetryExecutorV1",
    "ProviderStageSemanticDisposition",
    "ProviderStageSuccessfulResultV1",
]
