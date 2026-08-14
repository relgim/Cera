"""Callable provider boundaries for one durably reserved stage attempt.

The generic Retry executor owns attempt authorization.  This module only
adapts one already-authorized Codex or Pi/DeepSeek call into its closed typed
outcome.  It deliberately does not inspect exception messages, error-code
wording, or diagnostics to decide Retry eligibility.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from threading import Lock
from typing import Protocol

from cera.errors import ContractValidationError, StateConflictError
from cera.providers.models import (
    LiveProviderCallReceipt,
    ProviderCallResult,
    ProviderFailureCallReceipt,
    ProviderName,
    ProviderRetryableFailureCategory,
    ProviderTransportError,
)
from cera.serialization import bytes_sha256, canonical_sha256, domain_sha256, re_is_sha256

from .contracts import PiWriterReceiptV1
from .pi_adapter import PiSceneInvocationResultV1
from .provider_stage_retry import (
    MAXIMUM_PROVIDER_STAGE_ATTEMPTS,
    MAXIMUM_SAFE_INTEGER,
    NON_RETRYABLE_PROVIDER_STAGE_FAILURES,
    PRETRANSPORT_PROVIDER_STAGE_FAILURES,
    ProviderStage,
    ProviderStageFailureClass,
)
from .provider_stage_retry_executor import (
    ProviderStageAttemptMetricsV1,
    ProviderStageAttemptOutcomeV1,
    ProviderStageClosedFailureV1,
    ProviderStageDispatchAmbiguousV1,
    ProviderStageNonRetryableFailureV1,
    ProviderStagePreparationV1,
    ProviderStagePretransportFailureV1,
    ProviderStagePretransportNonRetryableFailureV1,
    ProviderStageSemanticDisposition,
    ProviderStageSuccessfulResultV1,
)


class ProviderStageBoundaryKind(StrEnum):
    """Closed physical provider boundary used by the stage owners."""

    CODEX = "codex"
    PI_DEEPSEEK = "pi_deepseek"


class ProviderStageResultContractError(ContractValidationError):
    """A completed provider result failed its stage-owned output contract."""


_CODEX_STAGES = frozenset(
    {
        ProviderStage.PLANNER,
        ProviderStage.SEMANTIC_VALIDATOR,
        ProviderStage.READER,
    }
)
_PI_DEEPSEEK_STAGES = frozenset(set(ProviderStage) - _CODEX_STAGES)

_RETRYABLE_FAILURE_CLASS = {
    ProviderRetryableFailureCategory.TRANSPORT_TIMEOUT: (
        ProviderStageFailureClass.TRANSPORT_TIMEOUT
    ),
    ProviderRetryableFailureCategory.PROVIDER_UNAVAILABLE: (
        ProviderStageFailureClass.PROVIDER_UNAVAILABLE
    ),
    ProviderRetryableFailureCategory.PROVIDER_PROCESS_FAILED: (
        ProviderStageFailureClass.PROVIDER_PROCESS_FAILED
    ),
    ProviderRetryableFailureCategory.PROVIDER_STREAM_INCOMPLETE: (
        ProviderStageFailureClass.PROVIDER_STREAM_INCOMPLETE
    ),
    ProviderRetryableFailureCategory.PROVIDER_COMPLETION_INCOMPLETE: (
        ProviderStageFailureClass.PROVIDER_COMPLETION_INCOMPLETE
    ),
    ProviderRetryableFailureCategory.PROVIDER_OUTPUT_INVALID: (
        ProviderStageFailureClass.PROVIDER_OUTPUT_INVALID
    ),
}


def _require_sha256(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not re_is_sha256(value):
        raise ContractValidationError(f"provider-stage adapter {field_name} must be SHA-256")


def _require_nonnegative(value: int, field_name: str) -> None:
    if type(value) is not int or value < 0:
        raise ContractValidationError(f"provider-stage adapter {field_name} must be non-negative")


@dataclass(frozen=True, slots=True)
class ProviderStageLedgerSnapshotV1:
    """Exact durable provider-operation prefix at one local instant."""

    prefix_sha256: str
    provider_operations_total: int

    def __post_init__(self) -> None:
        _require_sha256(self.prefix_sha256, "ledger prefix")
        _require_nonnegative(self.provider_operations_total, "ledger operation total")


@dataclass(frozen=True, slots=True)
class ProviderStageReceiptMetricsV1:
    """Content-free operation and token totals from one provider receipt."""

    receipt_evidence_sha256: str
    provider_operations: int
    duration_ms: int
    input_tokens: int | None = None
    cached_input_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_tokens: int | None = None

    def __post_init__(self) -> None:
        _require_sha256(self.receipt_evidence_sha256, "receipt evidence")
        if type(self.provider_operations) is not int or self.provider_operations < 1:
            raise ContractValidationError(
                "provider-stage receipt requires at least one provider operation"
            )
        _require_nonnegative(self.duration_ms, "receipt duration")
        for value, field_name in (
            (self.input_tokens, "receipt input tokens"),
            (self.cached_input_tokens, "receipt cached input tokens"),
            (self.output_tokens, "receipt output tokens"),
            (self.reasoning_tokens, "receipt reasoning tokens"),
        ):
            if value is not None:
                _require_nonnegative(value, field_name)
        if self.cached_input_tokens is not None and (
            self.input_tokens is None or self.cached_input_tokens > self.input_tokens
        ):
            raise ContractValidationError("provider-stage receipt cached input exceeds total input")


@dataclass(frozen=True, slots=True)
class ProviderStageAttemptOwnerBindingV1:
    """Exact occurrence, session, and ledger identity for one fresh owner."""

    chain_id: str
    attempt_number: int
    stage: ProviderStage
    boundary_kind: ProviderStageBoundaryKind
    stage_input_sha256: str
    session_scope_sha256: str
    ledger_before: ProviderStageLedgerSnapshotV1
    maximum_provider_operations: int

    def __post_init__(self) -> None:
        if (
            not isinstance(self.chain_id, str)
            or not self.chain_id.startswith("stage-retry-")
            or not re_is_sha256(self.chain_id.removeprefix("stage-retry-"))
        ):
            raise ContractValidationError("provider-stage adapter chain ID is invalid")
        if (
            type(self.attempt_number) is not int
            or not 1 <= self.attempt_number <= MAXIMUM_PROVIDER_STAGE_ATTEMPTS
        ):
            raise ContractValidationError("provider-stage adapter attempt is invalid")
        if type(self.stage) is not ProviderStage:
            raise ContractValidationError("provider-stage adapter stage is not closed")
        if type(self.boundary_kind) is not ProviderStageBoundaryKind:
            raise ContractValidationError("provider-stage adapter boundary is not closed")
        expected_stages = (
            _CODEX_STAGES
            if self.boundary_kind is ProviderStageBoundaryKind.CODEX
            else _PI_DEEPSEEK_STAGES
        )
        if self.stage not in expected_stages:
            raise ContractValidationError("provider-stage adapter owner substitution detected")
        _require_sha256(self.stage_input_sha256, "stage input")
        _require_sha256(self.session_scope_sha256, "session scope")
        if type(self.ledger_before) is not ProviderStageLedgerSnapshotV1:
            raise ContractValidationError("provider-stage adapter ledger binding changed")
        if (
            type(self.maximum_provider_operations) is not int
            or not 1 <= self.maximum_provider_operations <= MAXIMUM_SAFE_INTEGER
        ):
            raise ContractValidationError(
                "provider-stage adapter maximum provider operations is invalid"
            )
        if (
            self.boundary_kind is ProviderStageBoundaryKind.CODEX
            and self.maximum_provider_operations != 1
        ):
            raise ContractValidationError(
                "Codex provider-stage owners must reserve exactly one operation"
            )


@dataclass(frozen=True, slots=True)
class ProviderStageOwnerRetirementV1:
    """Exact publication-fence request passed to the injected owner retiree."""

    chain_id: str
    attempt_number: int
    stage: ProviderStage
    boundary_kind: ProviderStageBoundaryKind
    session_scope_sha256: str
    ledger_prefix_before_sha256: str
    failure_class: ProviderStageFailureClass

    def __post_init__(self) -> None:
        if (
            type(self.stage) is not ProviderStage
            or type(self.boundary_kind) is not ProviderStageBoundaryKind
        ):
            raise ContractValidationError("provider-stage retirement owner changed")
        if type(self.failure_class) is not ProviderStageFailureClass:
            raise ContractValidationError("provider-stage retirement failure is not closed")
        _require_sha256(self.session_scope_sha256, "retirement session scope")
        _require_sha256(
            self.ledger_prefix_before_sha256,
            "retirement ledger prefix before",
        )


class ProviderStageNonRetryableClassifierPort(Protocol):
    """Optional exact upstream classifier; returning ``None`` selects generic."""

    def __call__(
        self,
        failure: ProviderTransportError,
    ) -> ProviderStageFailureClass | None: ...


class ProviderStagePreparedCallableDispatchV1[RequestT, ResultT]:
    """One-shot prepared dispatch; only ``invoke`` may contact the provider."""

    def __init__(
        self,
        owner: CallableProviderStageAttemptOwnerV1[RequestT, ResultT],
        request: RequestT,
        dispatch_evidence_sha256: str,
    ) -> None:
        _require_sha256(dispatch_evidence_sha256, "prepared dispatch evidence")
        self._owner = owner
        self._request = request
        self._dispatch_evidence_sha256 = dispatch_evidence_sha256
        self._lock = Lock()
        self._invoked = False

    @property
    def dispatch_evidence_sha256(self) -> str:
        return self._dispatch_evidence_sha256

    def invoke(self) -> ProviderStageAttemptOutcomeV1:
        with self._lock:
            if self._invoked:
                raise StateConflictError("provider-stage prepared dispatch was already invoked")
            self._invoked = True
        try:
            return self._owner._invoke(self._request)
        except Exception as exc:
            # No ordinary adapter defect may strand durable state at
            # DISPATCH_STARTED.  Once the executor reserves dispatch, an
            # otherwise-unclassified escape is conservatively ambiguous.
            return self._owner._unknown_failure(exc, duration_ms=0)


class CallableProviderStageAttemptOwnerV1[RequestT, ResultT]:
    """Composable exact owner for one Codex or Pi/DeepSeek stage attempt."""

    def __init__(
        self,
        *,
        binding: ProviderStageAttemptOwnerBindingV1,
        read_session_scope_sha256: Callable[[], str],
        read_ledger_snapshot: Callable[[], ProviderStageLedgerSnapshotV1],
        build_request: Callable[[bytes], RequestT],
        invoke_provider: Callable[[RequestT], ResultT],
        serialize_result: Callable[[ResultT], bytes],
        result_receipt_metrics: Callable[[ResultT], ProviderStageReceiptMetricsV1],
        semantic_disposition: Callable[[ResultT], ProviderStageSemanticDisposition],
        retire_owner: Callable[[ProviderStageOwnerRetirementV1], str],
        validate_result: Callable[[ResultT], None] | None = None,
        classify_non_retryable: ProviderStageNonRetryableClassifierPort | None = None,
        clock_ns: Callable[[], int] | None = None,
    ) -> None:
        if type(binding) is not ProviderStageAttemptOwnerBindingV1:
            raise ContractValidationError("provider-stage adapter binding changed")
        for callback, field_name in (
            (read_session_scope_sha256, "session reader"),
            (read_ledger_snapshot, "ledger reader"),
            (build_request, "request builder"),
            (invoke_provider, "provider invocation"),
            (serialize_result, "result serializer"),
            (result_receipt_metrics, "receipt reader"),
            (semantic_disposition, "semantic classifier"),
            (retire_owner, "owner retirement"),
        ):
            if not callable(callback):
                raise ContractValidationError(
                    f"provider-stage adapter {field_name} must be callable"
                )
        if classify_non_retryable is not None and not callable(classify_non_retryable):
            raise ContractValidationError(
                "provider-stage adapter non-Retry classifier must be callable"
            )
        if validate_result is not None and not callable(validate_result):
            raise ContractValidationError(
                "provider-stage adapter result validator must be callable"
            )
        self.binding = binding
        self._read_session_scope_sha256 = read_session_scope_sha256
        self._read_ledger_snapshot = read_ledger_snapshot
        # This callable is local preparation only.  It may decode or validate
        # the frozen packet, but must never start a provider lifecycle, model
        # call, or Pi process.  Only ``_invoke_provider`` is reachable from the
        # prepared dispatch's one-shot ``invoke`` method.
        self._build_request = build_request
        self._invoke_provider = invoke_provider
        self._serialize_result = serialize_result
        self._result_receipt_metrics = result_receipt_metrics
        self._semantic_disposition = semantic_disposition
        self._retire_owner = retire_owner
        self._validate_result = validate_result
        self._classify_non_retryable = classify_non_retryable
        self._clock_ns = clock_ns or time.perf_counter_ns
        self._prepare_lock = Lock()
        self._preparation: ProviderStagePreparationV1 | None = None

    @property
    def session_scope_sha256(self) -> str:
        return self.binding.session_scope_sha256

    @property
    def ledger_prefix_before_sha256(self) -> str:
        return self.binding.ledger_before.prefix_sha256

    @property
    def maximum_provider_operations(self) -> int:
        return self.binding.maximum_provider_operations

    def prepare(
        self,
        *,
        chain_id: str,
        attempt_number: int,
        exact_input: bytes,
    ) -> ProviderStagePreparationV1:
        self._require_attempt(chain_id, attempt_number, exact_input)
        with self._prepare_lock:
            if self._preparation is not None:
                return self._preparation
            try:
                started = self._clock_ns()
            except Exception as exc:
                clock_failure_class = ProviderStageFailureClass.CONFIGURATION_FAILED
                self._preparation = ProviderStagePretransportNonRetryableFailureV1(
                    failure_class=clock_failure_class,
                    failure_evidence_sha256=self._local_failure_evidence(
                        exc,
                        failure_class=clock_failure_class,
                        ledger_after=self.binding.ledger_before,
                        disposition="local_clock_start_failure",
                    ),
                    ledger_prefix_after_sha256=self.ledger_prefix_before_sha256,
                    duration_ms=0,
                )
                return self._preparation
            try:
                request = self._build_request(exact_input)
            except ProviderTransportError as exc:
                elapsed = self._elapsed_or_zero(started)
                failure_class = _retryable_failure_class(exc)
                try:
                    current = self._read_ledger_snapshot()
                except Exception:
                    current = self.binding.ledger_before
                if (
                    failure_class in PRETRANSPORT_PROVIDER_STAGE_FAILURES
                    and exc.external_provider_calls_observed == 0
                    and current == self.binding.ledger_before
                ):
                    self._preparation = ProviderStagePretransportFailureV1(
                        failure_class=failure_class,
                        failure_evidence_sha256=self._failure_evidence(
                            exc,
                            failure_class=failure_class,
                            ledger_after=current,
                            disposition="pretransport_retryable",
                        ),
                        ledger_prefix_after_sha256=current.prefix_sha256,
                        duration_ms=elapsed,
                    )
                    return self._preparation
                non_retryable = self._pretransport_non_retryable_class(exc)
                if (
                    current != self.binding.ledger_before
                    or exc.external_provider_calls_observed != 0
                ):
                    non_retryable = ProviderStageFailureClass.CUSTODY_FAILED
                self._preparation = ProviderStagePretransportNonRetryableFailureV1(
                    failure_class=non_retryable,
                    failure_evidence_sha256=self._failure_evidence(
                        exc,
                        failure_class=non_retryable,
                        ledger_after=current,
                        disposition="pretransport_non_retryable",
                    ),
                    ledger_prefix_after_sha256=current.prefix_sha256,
                    duration_ms=elapsed,
                )
                return self._preparation
            except Exception as exc:
                elapsed = self._elapsed_or_zero(started)
                try:
                    current = self._read_ledger_snapshot()
                except Exception:
                    current = self.binding.ledger_before
                failure_class = (
                    ProviderStageFailureClass.CUSTODY_FAILED
                    if current != self.binding.ledger_before
                    else ProviderStageFailureClass.PROVIDER_FAILURE_NOT_RETRYABLE
                )
                self._preparation = ProviderStagePretransportNonRetryableFailureV1(
                    failure_class=failure_class,
                    failure_evidence_sha256=self._local_failure_evidence(
                        exc,
                        failure_class=failure_class,
                        ledger_after=current,
                        disposition="local_build_failure",
                    ),
                    ledger_prefix_after_sha256=current.prefix_sha256,
                    duration_ms=elapsed,
                )
                return self._preparation
            dispatch_evidence = domain_sha256(
                "cera.provider_stage_callable_dispatch.v1",
                {
                    "chain_id": chain_id,
                    "attempt_number": attempt_number,
                    "stage": self.binding.stage.value,
                    "boundary_kind": self.binding.boundary_kind.value,
                    "stage_input_sha256": self.binding.stage_input_sha256,
                    "session_scope_sha256": self.session_scope_sha256,
                    "ledger_prefix_before_sha256": self.ledger_prefix_before_sha256,
                    "maximum_provider_operations": self.maximum_provider_operations,
                },
            )
            self._preparation = ProviderStagePreparedCallableDispatchV1(
                self,
                request,
                dispatch_evidence,
            )
            return self._preparation

    def retire(
        self,
        *,
        chain_id: str,
        attempt_number: int,
        failure_class: ProviderStageFailureClass,
    ) -> str:
        self._require_attempt_identity(chain_id, attempt_number)
        request = ProviderStageOwnerRetirementV1(
            chain_id=chain_id,
            attempt_number=attempt_number,
            stage=self.binding.stage,
            boundary_kind=self.binding.boundary_kind,
            session_scope_sha256=self.session_scope_sha256,
            ledger_prefix_before_sha256=self.ledger_prefix_before_sha256,
            failure_class=failure_class,
        )
        injected_evidence = self._retire_owner(request)
        _require_sha256(injected_evidence, "injected retirement evidence")
        return domain_sha256(
            "cera.provider_stage_owner_retirement.v1",
            {
                "request": request,
                "injected_evidence_sha256": injected_evidence,
            },
        )

    def _invoke(self, request: RequestT) -> ProviderStageAttemptOutcomeV1:
        try:
            live_session = self._read_session_scope_sha256()
            _require_sha256(live_session, "live session scope")
            live_before = self._read_ledger_snapshot()
        except Exception as exc:
            return self._known_local_failure(
                exc,
                after=self.binding.ledger_before,
                duration_ms=0,
                failure_class=ProviderStageFailureClass.CUSTODY_FAILED,
                disposition="pre_dispatch_identity_unavailable",
            )
        if live_session != self.session_scope_sha256 or live_before != self.binding.ledger_before:
            return ProviderStageNonRetryableFailureV1(
                failure_class=ProviderStageFailureClass.CUSTODY_FAILED,
                failure_evidence_sha256=domain_sha256(
                    "cera.provider_stage_pre_dispatch_custody_failure.v1",
                    {
                        "chain_id": self.binding.chain_id,
                        "attempt_number": self.binding.attempt_number,
                        "expected_session_scope_sha256": self.session_scope_sha256,
                        "actual_session_scope_sha256": live_session,
                        "expected_ledger_prefix_sha256": self.ledger_prefix_before_sha256,
                        "actual_ledger_prefix_sha256": live_before.prefix_sha256,
                    },
                ),
                metrics=ProviderStageAttemptMetricsV1(
                    ledger_prefix_after_sha256=live_before.prefix_sha256,
                    provider_operations_observed=0,
                    provider_operations_conservative=0,
                    duration_ms=0,
                ),
            )

        try:
            started = self._clock_ns()
        except Exception as exc:
            return self._known_local_failure(
                exc,
                after=live_before,
                duration_ms=0,
                disposition="pre_provider_clock_failed",
            )
        try:
            result = self._invoke_provider(request)
        except ProviderTransportError as exc:
            try:
                duration_ms = self._elapsed_ms(started)
            except Exception as clock_exc:
                try:
                    _, after = self._read_post_dispatch_identity()
                    self._operation_delta(after)
                except Exception as accounting_exc:
                    return self._ambiguity_without_snapshot(accounting_exc, duration_ms=0)
                return self._known_local_failure(
                    clock_exc,
                    after=after,
                    duration_ms=0,
                    disposition="post_failure_clock_failed",
                )
            return self._transport_failure(exc, duration_ms=duration_ms)
        except Exception as exc:
            duration_ms = self._elapsed_ms(started)
            return self._unknown_failure(exc, duration_ms=duration_ms)
        try:
            duration_ms = self._elapsed_ms(started)
        except Exception as exc:
            try:
                _, after = self._read_post_dispatch_identity()
                self._operation_delta(after)
            except Exception as accounting_exc:
                return self._ambiguity_without_snapshot(accounting_exc, duration_ms=0)
            return self._known_local_failure(
                exc,
                after=after,
                duration_ms=0,
                disposition="post_result_clock_failed",
            )

        try:
            live_after_session, after = self._read_post_dispatch_identity()
            if self._operation_delta(after) > self.maximum_provider_operations:
                return self._known_local_failure(
                    StateConflictError(
                        "provider-stage ledger exceeded the frozen operation ceiling"
                    ),
                    after=after,
                    duration_ms=duration_ms,
                    failure_class=ProviderStageFailureClass.CUSTODY_FAILED,
                    disposition="provider_operation_ceiling_exceeded",
                )
        except Exception as exc:
            return self._ambiguity_without_snapshot(exc, duration_ms=duration_ms)
        try:
            receipt = self._result_receipt_metrics(result)
        except Exception as exc:
            return self._known_local_failure(
                exc,
                after=after,
                duration_ms=duration_ms,
                disposition="result_receipt_processing_failed",
            )
        try:
            metrics = self._closed_metrics(
                after,
                duration_ms=duration_ms,
                receipt=receipt,
            )
        except Exception as exc:
            return self._ambiguity(
                exc,
                after=after,
                duration_ms=duration_ms,
                receipt=receipt,
                disposition="result_receipt_accounting_unresolved",
            )
        if live_after_session != self.session_scope_sha256:
            return self._custody_failure(
                actual_session_scope_sha256=live_after_session,
                metrics=metrics,
                disposition="post_result_session_changed",
            )
        if self._validate_result is not None:
            try:
                self._validate_result(result)
            except ProviderStageResultContractError as exc:
                return ProviderStageClosedFailureV1(
                    failure_class=ProviderStageFailureClass.PROVIDER_OUTPUT_INVALID,
                    failure_evidence_sha256=domain_sha256(
                        "cera.provider_stage_result_contract_failure.v1",
                        {
                            "chain_id": self.binding.chain_id,
                            "attempt_number": self.binding.attempt_number,
                            "stage": self.binding.stage.value,
                            "failure_class": (
                                ProviderStageFailureClass.PROVIDER_OUTPUT_INVALID.value
                            ),
                            "exception_type": _exception_identity(exc),
                            "receipt_evidence_sha256": receipt.receipt_evidence_sha256,
                            "ledger_prefix_after_sha256": (metrics.ledger_prefix_after_sha256),
                            "provider_operations_observed": (metrics.provider_operations_observed),
                        },
                    ),
                    metrics=metrics,
                )
            except Exception as exc:
                return self._known_local_failure(
                    exc,
                    after=after,
                    duration_ms=duration_ms,
                    disposition="result_contract_validator_failed",
                )
        try:
            exact_result = self._serialize_result(result)
            if not isinstance(exact_result, bytes) or not exact_result:
                raise ContractValidationError(
                    "provider-stage result serializer returned empty or non-byte output"
                )
            semantic = self._semantic_disposition(result)
            if type(semantic) is not ProviderStageSemanticDisposition:
                raise ContractValidationError(
                    "provider-stage semantic classifier returned an open value"
                )
        except Exception as exc:
            return self._known_local_failure(
                exc,
                after=after,
                duration_ms=duration_ms,
                disposition="result_processing_failed",
            )
        return ProviderStageSuccessfulResultV1(
            exact_result=exact_result,
            result_evidence_sha256=domain_sha256(
                "cera.provider_stage_callable_result.v1",
                {
                    "chain_id": self.binding.chain_id,
                    "attempt_number": self.binding.attempt_number,
                    "stage": self.binding.stage.value,
                    "session_scope_sha256": self.session_scope_sha256,
                    "stage_input_sha256": self.binding.stage_input_sha256,
                    "result_sha256": bytes_sha256(exact_result),
                    "receipt_evidence_sha256": receipt.receipt_evidence_sha256,
                    "ledger_prefix_after_sha256": metrics.ledger_prefix_after_sha256,
                    "semantic_disposition": semantic.value,
                },
            ),
            metrics=metrics,
            semantic_disposition=semantic,
        )

    def _transport_failure(
        self,
        failure: ProviderTransportError,
        *,
        duration_ms: int,
    ) -> ProviderStageAttemptOutcomeV1:
        try:
            live_after_session, after = self._read_post_dispatch_identity()
            delta = self._operation_delta(after)
        except Exception as exc:
            return self._ambiguity_without_snapshot(exc, duration_ms=duration_ms)
        if delta > self.maximum_provider_operations:
            return self._known_local_failure(
                StateConflictError("provider-stage ledger exceeded the frozen operation ceiling"),
                after=after,
                duration_ms=duration_ms,
                failure_class=ProviderStageFailureClass.CUSTODY_FAILED,
                disposition="provider_operation_ceiling_exceeded",
            )
        try:
            receipt = _provider_failure_receipt_metrics(failure)
        except Exception as exc:
            return self._known_local_failure(
                exc,
                after=after,
                duration_ms=duration_ms,
                disposition="failure_receipt_processing_failed",
            )
        if not _transport_observation_is_closed(failure, delta) or (
            receipt is not None and receipt.provider_operations != delta
        ):
            return self._ambiguity(
                failure,
                after=after,
                duration_ms=duration_ms,
                receipt=receipt,
                disposition="typed_transport_accounting_unresolved",
            )
        metrics = self._closed_metrics(
            after,
            duration_ms=duration_ms,
            receipt=receipt,
        )
        if live_after_session != self.session_scope_sha256:
            return self._custody_failure(
                actual_session_scope_sha256=live_after_session,
                metrics=metrics,
                disposition="post_failure_session_changed",
            )
        retryable = _retryable_failure_class(failure)
        if retryable is not None:
            if (
                retryable
                in {
                    ProviderStageFailureClass.PROVIDER_STREAM_INCOMPLETE,
                    ProviderStageFailureClass.PROVIDER_COMPLETION_INCOMPLETE,
                    ProviderStageFailureClass.PROVIDER_OUTPUT_INVALID,
                }
                and metrics.provider_operations_observed < 1
            ):
                return ProviderStageNonRetryableFailureV1(
                    failure_class=(ProviderStageFailureClass.PROVIDER_FAILURE_NOT_RETRYABLE),
                    failure_evidence_sha256=self._failure_evidence(
                        failure,
                        failure_class=(ProviderStageFailureClass.PROVIDER_FAILURE_NOT_RETRYABLE),
                        ledger_after=after,
                        disposition="post_operation_retry_claim_without_operation",
                    ),
                    metrics=metrics,
                )
            return ProviderStageClosedFailureV1(
                failure_class=retryable,
                failure_evidence_sha256=self._failure_evidence(
                    failure,
                    failure_class=retryable,
                    ledger_after=after,
                    disposition="retryable_closed",
                ),
                metrics=metrics,
            )
        try:
            non_retryable = (
                None
                if self._classify_non_retryable is None
                else self._classify_non_retryable(failure)
            )
        except Exception as exc:
            return self._known_local_failure(
                exc,
                after=after,
                duration_ms=duration_ms,
                disposition="non_retryable_classifier_failed",
            )
        if non_retryable is None:
            non_retryable = ProviderStageFailureClass.PROVIDER_FAILURE_NOT_RETRYABLE
        if non_retryable not in NON_RETRYABLE_PROVIDER_STAGE_FAILURES:
            return self._known_local_failure(
                failure,
                after=after,
                duration_ms=duration_ms,
                disposition="non_retryable_classifier_open_value",
            )
        return ProviderStageNonRetryableFailureV1(
            failure_class=non_retryable,
            failure_evidence_sha256=self._failure_evidence(
                failure,
                failure_class=non_retryable,
                ledger_after=after,
                disposition="non_retryable_closed",
            ),
            metrics=metrics,
        )

    def _unknown_failure(
        self,
        failure: Exception,
        *,
        duration_ms: int,
    ) -> ProviderStageDispatchAmbiguousV1 | ProviderStageNonRetryableFailureV1:
        try:
            _, after = self._read_post_dispatch_identity()
        except Exception as exc:
            return self._ambiguity_without_snapshot(exc, duration_ms=duration_ms)
        return self._ambiguity(
            failure,
            after=after,
            duration_ms=duration_ms,
            receipt=None,
            disposition="untyped_transport_unresolved",
        )

    def _known_local_failure(
        self,
        failure: Exception,
        *,
        after: ProviderStageLedgerSnapshotV1,
        duration_ms: int,
        disposition: str,
        failure_class: ProviderStageFailureClass = (
            ProviderStageFailureClass.PROVIDER_FAILURE_NOT_RETRYABLE
        ),
    ) -> ProviderStageNonRetryableFailureV1:
        metrics = self._closed_metrics(after, duration_ms=duration_ms, receipt=None)
        return ProviderStageNonRetryableFailureV1(
            failure_class=failure_class,
            failure_evidence_sha256=domain_sha256(
                "cera.provider_stage_local_processing_failure.v1",
                {
                    "chain_id": self.binding.chain_id,
                    "attempt_number": self.binding.attempt_number,
                    "stage": self.binding.stage.value,
                    "failure_class": failure_class.value,
                    "exception_type": _exception_identity(failure),
                    "ledger_prefix_after_sha256": after.prefix_sha256,
                    "provider_operations_observed": (metrics.provider_operations_observed),
                    "disposition": disposition,
                },
            ),
            metrics=metrics,
        )

    def _ambiguity(
        self,
        failure: Exception,
        *,
        after: ProviderStageLedgerSnapshotV1,
        duration_ms: int,
        receipt: ProviderStageReceiptMetricsV1 | None,
        disposition: str,
    ) -> ProviderStageDispatchAmbiguousV1 | ProviderStageNonRetryableFailureV1:
        try:
            delta = self._operation_delta(after)
        except Exception as exc:
            return self._ambiguity_without_snapshot(exc, duration_ms=duration_ms)
        if delta > self.maximum_provider_operations:
            return self._known_local_failure(
                StateConflictError("provider-stage ledger exceeded the frozen operation ceiling"),
                after=after,
                duration_ms=duration_ms,
                failure_class=ProviderStageFailureClass.CUSTODY_FAILED,
                disposition="provider_operation_ceiling_exceeded",
            )
        token_receipt = (
            receipt if receipt is not None and receipt.provider_operations == delta else None
        )
        return ProviderStageDispatchAmbiguousV1(
            failure_evidence_sha256=domain_sha256(
                "cera.provider_stage_dispatch_ambiguity.v1",
                {
                    "chain_id": self.binding.chain_id,
                    "attempt_number": self.binding.attempt_number,
                    "exception_type": _exception_identity(failure),
                    "disposition": disposition,
                    "ledger_prefix_before_sha256": self.ledger_prefix_before_sha256,
                    "ledger_prefix_after_sha256": after.prefix_sha256,
                    "provider_operations_observed": delta,
                    "maximum_provider_operations": self.maximum_provider_operations,
                },
            ),
            metrics=ProviderStageAttemptMetricsV1(
                ledger_prefix_after_sha256=after.prefix_sha256,
                provider_operations_observed=delta,
                provider_operations_conservative=max(
                    delta,
                    self.maximum_provider_operations,
                ),
                duration_ms=(duration_ms if token_receipt is None else token_receipt.duration_ms),
                input_tokens=(None if token_receipt is None else token_receipt.input_tokens),
                cached_input_tokens=(
                    None if token_receipt is None else token_receipt.cached_input_tokens
                ),
                output_tokens=(None if token_receipt is None else token_receipt.output_tokens),
                reasoning_tokens=(
                    None if token_receipt is None else token_receipt.reasoning_tokens
                ),
            ),
        )

    def _ambiguity_without_snapshot(
        self,
        failure: Exception,
        *,
        duration_ms: int,
    ) -> ProviderStageDispatchAmbiguousV1:
        # The last proven durable prefix remains the exact observable prefix.
        # The configured per-owner ceiling stays reserved until reconciliation.
        return ProviderStageDispatchAmbiguousV1(
            failure_evidence_sha256=domain_sha256(
                "cera.provider_stage_ledger_snapshot_ambiguity.v1",
                {
                    "chain_id": self.binding.chain_id,
                    "attempt_number": self.binding.attempt_number,
                    "exception_type": _exception_identity(failure),
                    "last_proven_ledger_prefix_sha256": self.ledger_prefix_before_sha256,
                    "maximum_provider_operations": self.maximum_provider_operations,
                },
            ),
            metrics=ProviderStageAttemptMetricsV1(
                ledger_prefix_after_sha256=self.ledger_prefix_before_sha256,
                provider_operations_observed=0,
                provider_operations_conservative=self.maximum_provider_operations,
                duration_ms=duration_ms,
            ),
        )

    def _closed_metrics(
        self,
        after: ProviderStageLedgerSnapshotV1,
        *,
        duration_ms: int,
        receipt: ProviderStageReceiptMetricsV1 | None,
    ) -> ProviderStageAttemptMetricsV1:
        delta = self._operation_delta(after)
        if receipt is not None and receipt.provider_operations != delta:
            raise StateConflictError("provider-stage receipt differs from the durable ledger delta")
        return ProviderStageAttemptMetricsV1(
            ledger_prefix_after_sha256=after.prefix_sha256,
            provider_operations_observed=delta,
            provider_operations_conservative=delta,
            duration_ms=duration_ms if receipt is None else receipt.duration_ms,
            input_tokens=None if receipt is None else receipt.input_tokens,
            cached_input_tokens=None if receipt is None else receipt.cached_input_tokens,
            output_tokens=None if receipt is None else receipt.output_tokens,
            reasoning_tokens=None if receipt is None else receipt.reasoning_tokens,
        )

    def _custody_failure(
        self,
        *,
        actual_session_scope_sha256: str,
        metrics: ProviderStageAttemptMetricsV1,
        disposition: str,
    ) -> ProviderStageNonRetryableFailureV1:
        return ProviderStageNonRetryableFailureV1(
            failure_class=ProviderStageFailureClass.CUSTODY_FAILED,
            failure_evidence_sha256=domain_sha256(
                "cera.provider_stage_post_dispatch_custody_failure.v1",
                {
                    "chain_id": self.binding.chain_id,
                    "attempt_number": self.binding.attempt_number,
                    "expected_session_scope_sha256": self.session_scope_sha256,
                    "actual_session_scope_sha256": actual_session_scope_sha256,
                    "ledger_prefix_after_sha256": metrics.ledger_prefix_after_sha256,
                    "disposition": disposition,
                },
            ),
            metrics=metrics,
        )

    def _read_post_dispatch_identity(
        self,
    ) -> tuple[str, ProviderStageLedgerSnapshotV1]:
        session_scope_sha256 = self._read_session_scope_sha256()
        _require_sha256(session_scope_sha256, "post-dispatch session scope")
        return session_scope_sha256, self._read_ledger_snapshot()

    def _operation_delta(self, after: ProviderStageLedgerSnapshotV1) -> int:
        if type(after) is not ProviderStageLedgerSnapshotV1:
            raise ContractValidationError("provider-stage ledger reader returned an open value")
        delta = (
            after.provider_operations_total - self.binding.ledger_before.provider_operations_total
        )
        if delta < 0:
            raise StateConflictError("provider-stage ledger operation total moved backwards")
        return delta

    def _failure_evidence(
        self,
        failure: ProviderTransportError,
        *,
        failure_class: ProviderStageFailureClass,
        ledger_after: ProviderStageLedgerSnapshotV1,
        disposition: str,
    ) -> str:
        receipt = failure.provider_call_receipt
        return domain_sha256(
            "cera.provider_stage_typed_transport_failure.v1",
            {
                "chain_id": self.binding.chain_id,
                "attempt_number": self.binding.attempt_number,
                "stage": self.binding.stage.value,
                "failure_class": failure_class.value,
                "provider_error_code": failure.code.value,
                "retryable_category": (
                    None
                    if failure.retryable_failure_category is None
                    else failure.retryable_failure_category.value
                ),
                "external_provider_calls_observed": (failure.external_provider_calls_observed),
                "provider_receipt_sha256": (None if receipt is None else receipt.receipt_sha256),
                "ledger_prefix_after_sha256": ledger_after.prefix_sha256,
                "disposition": disposition,
            },
        )

    def _local_failure_evidence(
        self,
        failure: Exception,
        *,
        failure_class: ProviderStageFailureClass,
        ledger_after: ProviderStageLedgerSnapshotV1,
        disposition: str,
    ) -> str:
        return domain_sha256(
            "cera.provider_stage_local_pretransport_failure.v1",
            {
                "chain_id": self.binding.chain_id,
                "attempt_number": self.binding.attempt_number,
                "stage": self.binding.stage.value,
                "failure_class": failure_class.value,
                "exception_type": _exception_identity(failure),
                "ledger_prefix_before_sha256": self.ledger_prefix_before_sha256,
                "ledger_prefix_after_sha256": ledger_after.prefix_sha256,
                "disposition": disposition,
            },
        )

    def _pretransport_non_retryable_class(
        self,
        failure: ProviderTransportError,
    ) -> ProviderStageFailureClass:
        if self._classify_non_retryable is not None:
            try:
                classified = self._classify_non_retryable(failure)
            except Exception:
                classified = None
            if classified in NON_RETRYABLE_PROVIDER_STAGE_FAILURES:
                return classified
        return ProviderStageFailureClass.PROVIDER_FAILURE_NOT_RETRYABLE

    def _require_attempt(
        self,
        chain_id: str,
        attempt_number: int,
        exact_input: bytes,
    ) -> None:
        self._require_attempt_identity(chain_id, attempt_number)
        if not isinstance(exact_input, bytes) or not exact_input:
            raise ContractValidationError("provider-stage adapter exact input is empty")
        if bytes_sha256(exact_input) != self.binding.stage_input_sha256:
            raise StateConflictError("provider-stage adapter exact input changed")

    def _require_attempt_identity(self, chain_id: str, attempt_number: int) -> None:
        if chain_id != self.binding.chain_id or attempt_number != self.binding.attempt_number:
            raise StateConflictError("provider-stage adapter attempt identity changed")

    def _elapsed_ms(self, started_ns: int) -> int:
        completed_ns = self._clock_ns()
        if type(started_ns) is not int or type(completed_ns) is not int:
            raise ContractValidationError("provider-stage adapter clock returned an open value")
        return max(0, (completed_ns - started_ns) // 1_000_000)

    def _elapsed_or_zero(self, started_ns: int) -> int:
        try:
            return self._elapsed_ms(started_ns)
        except Exception:
            return 0


def codex_provider_result_receipt_metrics(
    result: ProviderCallResult,
) -> ProviderStageReceiptMetricsV1:
    """Read one Codex receipt and its operation-local telemetry."""

    if not isinstance(result, ProviderCallResult):
        raise ContractValidationError("Codex provider-stage result type changed")
    receipt = result.receipt
    if receipt.provider is not ProviderName.OPENAI_CODEX:
        raise ContractValidationError("Codex provider-stage receipt owner changed")
    telemetry = result.operation_telemetry
    return ProviderStageReceiptMetricsV1(
        receipt_evidence_sha256=domain_sha256(
            "cera.provider_stage_codex_receipt.v1",
            {
                "receipt_sha256": receipt.receipt_sha256,
                "operation_telemetry_sha256": (
                    None if telemetry is None else telemetry.telemetry_sha256
                ),
            },
        ),
        provider_operations=receipt.external_provider_calls,
        duration_ms=receipt.duration_ms,
        input_tokens=receipt.input_tokens,
        cached_input_tokens=receipt.cached_input_tokens,
        output_tokens=receipt.output_tokens,
        reasoning_tokens=receipt.reasoning_output_tokens,
    )


def pi_provider_result_receipt_metrics(
    result: PiSceneInvocationResultV1,
) -> ProviderStageReceiptMetricsV1:
    """Read all model operations and cumulative tokens from one Pi turn."""

    if not isinstance(result, PiSceneInvocationResultV1):
        raise ContractValidationError("Pi provider-stage result type changed")
    receipt = result.writer_receipt
    if receipt.provider != "deepseek":
        raise ContractValidationError("Pi provider-stage receipt owner changed")
    return _pi_receipt_metrics(receipt)


def _pi_receipt_metrics(receipt: PiWriterReceiptV1) -> ProviderStageReceiptMetricsV1:
    return ProviderStageReceiptMetricsV1(
        receipt_evidence_sha256=domain_sha256(
            "cera.provider_stage_pi_receipt.v1",
            {"receipt_sha256": canonical_sha256(receipt)},
        ),
        provider_operations=receipt.provider_operations,
        duration_ms=receipt.duration_ms,
        input_tokens=receipt.input_tokens,
        cached_input_tokens=receipt.cached_input_tokens,
        output_tokens=receipt.output_tokens,
        reasoning_tokens=receipt.reasoning_tokens,
    )


def _provider_failure_receipt_metrics(
    failure: ProviderTransportError,
) -> ProviderStageReceiptMetricsV1 | None:
    receipt = failure.provider_call_receipt
    call_receipt: LiveProviderCallReceipt | None
    if isinstance(receipt, ProviderFailureCallReceipt):
        call_receipt = receipt.call_receipt
        receipt_evidence = receipt.receipt_sha256
    elif isinstance(receipt, LiveProviderCallReceipt):
        call_receipt = receipt
        receipt_evidence = receipt.receipt_sha256
    elif receipt is not None:
        raise ContractValidationError("provider-stage failure receipt has an unsupported type")
    else:
        call_receipt = None
        receipt_evidence = None
    telemetry = failure.operation_telemetry
    if call_receipt is not None:
        if telemetry is not None:
            telemetry_tokens = (
                telemetry.cumulative_input_tokens,
                telemetry.cumulative_cached_input_tokens,
                telemetry.cumulative_output_tokens,
                telemetry.cumulative_reasoning_tokens,
            )
            receipt_tokens = (
                call_receipt.input_tokens,
                call_receipt.cached_input_tokens,
                call_receipt.output_tokens,
                call_receipt.reasoning_output_tokens,
            )
            if telemetry_tokens != receipt_tokens:
                raise StateConflictError(
                    "provider-stage failure receipt differs from Codex telemetry"
                )
        assert receipt_evidence is not None
        return ProviderStageReceiptMetricsV1(
            receipt_evidence_sha256=domain_sha256(
                "cera.provider_stage_failure_receipt.v1",
                {
                    "receipt_sha256": receipt_evidence,
                    "operation_telemetry_sha256": (
                        None if telemetry is None else telemetry.telemetry_sha256
                    ),
                },
            ),
            provider_operations=call_receipt.external_provider_calls,
            duration_ms=call_receipt.duration_ms,
            input_tokens=call_receipt.input_tokens,
            cached_input_tokens=call_receipt.cached_input_tokens,
            output_tokens=call_receipt.output_tokens,
            reasoning_tokens=call_receipt.reasoning_output_tokens,
        )
    if telemetry is None:
        return None
    return ProviderStageReceiptMetricsV1(
        receipt_evidence_sha256=domain_sha256(
            "cera.provider_stage_failure_telemetry.v1",
            {"operation_telemetry_sha256": telemetry.telemetry_sha256},
        ),
        provider_operations=telemetry.provider_attempt_count,
        duration_ms=max(
            0,
            (telemetry.provider_completion_unix_us - telemetry.request_start_unix_us) // 1000,
        ),
        input_tokens=telemetry.cumulative_input_tokens,
        cached_input_tokens=telemetry.cumulative_cached_input_tokens,
        output_tokens=telemetry.cumulative_output_tokens,
        reasoning_tokens=telemetry.cumulative_reasoning_tokens,
    )


def _retryable_failure_class(
    failure: ProviderTransportError,
) -> ProviderStageFailureClass | None:
    category = failure.retryable_failure_category
    if category is None:
        return None
    return _RETRYABLE_FAILURE_CLASS[category]


def _transport_observation_is_closed(
    failure: ProviderTransportError,
    ledger_operation_delta: int,
) -> bool:
    observed = failure.external_provider_calls_observed
    # ProviderTransportError exposes a crossed-boundary observation, not Pi's
    # internal multi-operation total.  The injected durable ledger owns the
    # exact count.
    return (observed == 0 and ledger_operation_delta == 0) or (
        observed == 1 and ledger_operation_delta >= 1
    )


def _exception_identity(failure: BaseException) -> str:
    return f"{type(failure).__module__}.{type(failure).__qualname__}"


__all__ = [
    "CallableProviderStageAttemptOwnerV1",
    "ProviderStageAttemptOwnerBindingV1",
    "ProviderStageBoundaryKind",
    "ProviderStageLedgerSnapshotV1",
    "ProviderStageNonRetryableClassifierPort",
    "ProviderStageOwnerRetirementV1",
    "ProviderStagePreparedCallableDispatchV1",
    "ProviderStageReceiptMetricsV1",
    "codex_provider_result_receipt_metrics",
    "pi_provider_result_receipt_metrics",
]
