"""Durable conservative accounting for continuous-route provider dispatches."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
import json
import os
from pathlib import Path
from threading import RLock
from typing import Any, Callable, ClassVar, TypeVar

from cera.errors import ContractValidationError, StateConflictError
from cera.serialization import (
    canonical_bytes,
    canonical_sha256,
    re_is_sha256,
    text_sha256,
    to_primitive,
)

from .operation_evidence import ProviderOperationEvidenceRequestV1


class ProviderCallState(StrEnum):
    PREPARED = "prepared_not_invoked"
    WORKER_STARTED = "worker_started_not_invoked"
    WORKER_PREFLIGHT = "worker_preflight_not_invoked"
    PRETRANSPORT_FAILED = "pretransport_failed"
    TRANSPORT_INVOKED = "transport_invoked"
    DISPATCH_INITIATED = "transport_invoked"  # decode-only source compatibility
    PROVIDER_COMPLETED = "provider_completed"
    PROVIDER_FAILED = "provider_failed"
    POST_VALIDATION_FAILED = "provider_completed_post_validation_failed"
    ACCEPTED = "typed_accepted"


@dataclass(frozen=True, slots=True)
class ProviderCallLedgerEventV1:
    SCHEMA_VERSION: ClassVar[str] = "cera.continuous_provider_call_ledger_event.v3"

    schema_version: str
    call_id: str
    event_index: int
    owner: str
    operation: str
    state: ProviderCallState
    route: str
    model: str
    effort: str | None
    provider_receipt_sha256: str | None
    failure_receipt_sha256: str | None
    operation_telemetry_sha256: str | None
    tool_sequence_sha256: str | None
    stored_thread_sha256: str | None
    failure_type: str | None
    recorded_at_utc: str

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("provider call ledger schema changed")
        if not all(
            isinstance(value, str) and value.strip()
            for value in (self.call_id, self.owner, self.operation, self.route, self.model)
        ):
            raise ContractValidationError("provider call ledger identity is incomplete")
        if type(self.event_index) is not int or self.event_index < 1:
            raise ContractValidationError("provider call event index is invalid")
        for value in (
            self.provider_receipt_sha256,
            self.failure_receipt_sha256,
            self.operation_telemetry_sha256,
            self.tool_sequence_sha256,
            self.stored_thread_sha256,
        ):
            if value is not None and not re_is_sha256(value):
                raise ContractValidationError("provider call ledger hash is invalid")
        if self.owner in {"planner", "validator", "scene_summary"} and not self.stored_thread_sha256:
            raise ContractValidationError("Codex call ledger event lacks stored-thread identity")


T = TypeVar("T")
R = TypeVar("R")


@dataclass(frozen=True, slots=True)
class ProviderCallStageMarkersV1:
    mark_worker_started: Callable[[], None]
    mark_worker_preflight: Callable[[], None]
    mark_transport_invoked: Callable[[], None]


class ContinuousProviderCallLedger:
    """Append-only events; only transport invocation consumes the call count."""

    def __init__(self, path: Path, *, maximum_calls: int | None = None) -> None:
        if not path.is_absolute():
            raise ContractValidationError("provider call ledger path must be absolute")
        self.path = path
        if maximum_calls is not None and (
            type(maximum_calls) is not int or maximum_calls < 1
        ):
            raise ContractValidationError("provider call ledger ceiling is invalid")
        self.maximum_calls = maximum_calls
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()

    def execute(
        self,
        *,
        owner: str,
        operation: str,
        route: str,
        model: str,
        effort: str | None,
        dispatch: Callable[[], T] | None = None,
        dispatch_with_invocation_marker: (
            Callable[[Callable[[], None]], T] | None
        ) = None,
        dispatch_with_stage_markers: (
            Callable[[ProviderCallStageMarkersV1], T] | None
        ) = None,
        finalize: Callable[[T], R],
        receipt_of: Callable[[T], Any] = lambda value: getattr(value, "receipt", None),
        telemetry_of: Callable[[T], Any] = lambda value: getattr(value, "operation_telemetry", None),
        stored_thread_sha256: str | None = None,
        operation_evidence: ProviderOperationEvidenceRequestV1 | None = None,
    ) -> R:
        selected_dispatches = sum(
            value is not None
            for value in (
                dispatch,
                dispatch_with_invocation_marker,
                dispatch_with_stage_markers,
            )
        )
        if selected_dispatches != 1:
            raise ContractValidationError(
                "provider call requires exactly one dispatch contract"
            )
        if (
            self.maximum_calls is not None
            and self.dispatched_call_count >= self.maximum_calls
        ):
            raise StateConflictError("provider call ledger ceiling reached before dispatch")
        call_id = "call_" + canonical_sha256(
            {
                "owner": owner,
                "operation": operation,
                "route": route,
                "model": model,
                "effort": effort,
                "next": self.event_count + 1,
            }
        )[:24]
        if operation_evidence is not None:
            operation_evidence.capture.prepare(
                operation_evidence,
                call_id=call_id,
                owner=owner,
                operation=operation,
                route=route,
                model=model,
                effort=effort,
                stored_thread_sha256=stored_thread_sha256,
            )
        self._record(call_id, owner, operation, ProviderCallState.PREPARED, route, model, effort, stored_thread_sha256=stored_thread_sha256)
        invocation_marked = False
        worker_started_marked = False
        worker_preflight_marked = False
        use_worker_stages = dispatch_with_stage_markers is not None

        def mark_worker_started() -> None:
            nonlocal worker_started_marked
            with self._lock:
                if worker_started_marked:
                    return
                self._record(
                    call_id, owner, operation, ProviderCallState.WORKER_STARTED,
                    route, model, effort, stored_thread_sha256=stored_thread_sha256,
                )
                worker_started_marked = True

        def mark_worker_preflight() -> None:
            nonlocal worker_preflight_marked
            with self._lock:
                if worker_preflight_marked:
                    return
                mark_worker_started()
                self._record(
                    call_id, owner, operation, ProviderCallState.WORKER_PREFLIGHT,
                    route, model, effort, stored_thread_sha256=stored_thread_sha256,
                )
                worker_preflight_marked = True

        def mark_transport_invoked() -> None:
            nonlocal invocation_marked
            with self._lock:
                if invocation_marked:
                    return
                if use_worker_stages:
                    mark_worker_preflight()
                self._record(
                    call_id,
                    owner,
                    operation,
                    ProviderCallState.TRANSPORT_INVOKED,
                    route,
                    model,
                    effort,
                    stored_thread_sha256=stored_thread_sha256,
                )
                invocation_marked = True

        try:
            if dispatch_with_stage_markers is not None:
                raw = dispatch_with_stage_markers(
                    ProviderCallStageMarkersV1(
                        mark_worker_started=mark_worker_started,
                        mark_worker_preflight=mark_worker_preflight,
                        mark_transport_invoked=mark_transport_invoked,
                    )
                )
            elif dispatch_with_invocation_marker is not None:
                raw = dispatch_with_invocation_marker(mark_transport_invoked)
            else:
                assert dispatch is not None
                raw = dispatch()
        except BaseException as exc:
            observed = getattr(exc, "external_provider_calls_observed", None)
            receipt = (
                getattr(exc, "provider_call_receipt", None)
                or getattr(exc, "failure_receipt", None)
            )
            telemetry = getattr(exc, "operation_telemetry", None)
            stronger_invocation_evidence = (
                invocation_marked
                or observed == 1
                or receipt is not None
                or telemetry is not None
            )
            proven_local_pretransport = (
                not stronger_invocation_evidence
                and (
                    observed == 0
                    or (
                        observed is None
                        and isinstance(
                            exc, (ContractValidationError, StateConflictError)
                        )
                    )
                )
            )
            if proven_local_pretransport:
                self._record(
                    call_id,
                    owner,
                    operation,
                    ProviderCallState.PRETRANSPORT_FAILED,
                    route,
                    model,
                    effort,
                    failure_type=type(exc).__name__,
                    stored_thread_sha256=stored_thread_sha256,
                )
                if operation_evidence is not None:
                    operation_evidence.capture.terminal(
                        call_id=call_id,
                        ledger_event=self.events[-1],
                        failure=exc,
                    )
                raise
            mark_transport_invoked()
            self._record(
                call_id,
                owner,
                operation,
                ProviderCallState.PROVIDER_FAILED,
                route,
                model,
                effort,
                failure_receipt_sha256=_safe_hash(receipt),
                operation_telemetry_sha256=_safe_hash(telemetry),
                failure_type=type(exc).__name__,
                stored_thread_sha256=stored_thread_sha256,
            )
            if operation_evidence is not None:
                operation_evidence.capture.terminal(
                    call_id=call_id,
                    ledger_event=self.events[-1],
                    failure=exc,
                )
            raise
        mark_transport_invoked()
        receipt = receipt_of(raw)
        telemetry = telemetry_of(raw)
        receipt_hash = _safe_hash(receipt)
        telemetry_hash = _safe_hash(telemetry)
        tool_sequence_hash = _safe_hash(
            {
                "tool_names": getattr(raw, "tool_names", ()),
                "tool_server_names": getattr(raw, "tool_server_names", ()),
            }
        )
        self._record(call_id, owner, operation, ProviderCallState.PROVIDER_COMPLETED, route, model, effort, provider_receipt_sha256=receipt_hash, operation_telemetry_sha256=telemetry_hash, tool_sequence_sha256=tool_sequence_hash, stored_thread_sha256=stored_thread_sha256)
        try:
            result = finalize(raw)
        except BaseException as exc:
            _bind_completed_provider_failure_evidence(
                exc,
                raw=raw,
                receipt=receipt,
                telemetry=telemetry,
            )
            self._record(call_id, owner, operation, ProviderCallState.POST_VALIDATION_FAILED, route, model, effort, provider_receipt_sha256=receipt_hash, operation_telemetry_sha256=telemetry_hash, tool_sequence_sha256=tool_sequence_hash, stored_thread_sha256=stored_thread_sha256, failure_type=type(exc).__name__)
            if operation_evidence is not None:
                operation_evidence.capture.terminal(
                    call_id=call_id,
                    ledger_event=self.events[-1],
                    raw_result=raw,
                    failure=exc,
                )
            raise
        self._record(call_id, owner, operation, ProviderCallState.ACCEPTED, route, model, effort, provider_receipt_sha256=receipt_hash, operation_telemetry_sha256=telemetry_hash, tool_sequence_sha256=tool_sequence_hash, stored_thread_sha256=stored_thread_sha256)
        if operation_evidence is not None:
            operation_evidence.capture.terminal(
                call_id=call_id,
                ledger_event=self.events[-1],
                raw_result=raw,
                finalized_result=result,
            )
        return result

    def _record(
        self,
        call_id: str,
        owner: str,
        operation: str,
        state: ProviderCallState,
        route: str,
        model: str,
        effort: str | None,
        *,
        provider_receipt_sha256: str | None = None,
        failure_receipt_sha256: str | None = None,
        operation_telemetry_sha256: str | None = None,
        tool_sequence_sha256: str | None = None,
        stored_thread_sha256: str | None = None,
        failure_type: str | None = None,
    ) -> None:
        if not all(isinstance(value, str) and value.strip() for value in (owner, operation, route, model)):
            raise ContractValidationError("provider call ledger identity is incomplete")
        with self._lock:
            event = ProviderCallLedgerEventV1(
                schema_version=ProviderCallLedgerEventV1.SCHEMA_VERSION,
                call_id=call_id,
                event_index=self.event_count + 1,
                owner=owner,
                operation=operation,
                state=state,
                route=route,
                model=model,
                effort=effort,
                provider_receipt_sha256=provider_receipt_sha256,
                failure_receipt_sha256=failure_receipt_sha256,
                operation_telemetry_sha256=operation_telemetry_sha256,
                tool_sequence_sha256=tool_sequence_sha256,
                stored_thread_sha256=stored_thread_sha256,
                failure_type=failure_type,
                recorded_at_utc=datetime.now(UTC).isoformat(timespec="microseconds"),
            )
            with self.path.open("ab") as stream:
                stream.write(canonical_bytes(to_primitive(event)) + b"\n")
                stream.flush()
                os.fsync(stream.fileno())

    @property
    def events(self) -> tuple[dict[str, Any], ...]:
        if not self.path.is_file():
            return ()
        return tuple(json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines() if line.strip())

    @property
    def event_count(self) -> int:
        return len(self.events)

    @property
    def dispatched_call_count(self) -> int:
        events = self.events
        invoked = {
            value["call_id"]
            for value in events
            if value["state"] == ProviderCallState.TRANSPORT_INVOKED.value
        }
        invoked.update(self.unresolved_prepared_call_ids)
        return len(invoked)

    @property
    def unresolved_prepared_call_ids(self) -> tuple[str, ...]:
        terminal_by_call: dict[str, str] = {}
        for value in self.events:
            terminal_by_call[value["call_id"]] = value["state"]
        return tuple(
            sorted(
                call_id
                for call_id, state in terminal_by_call.items()
                if state
                in {
                    ProviderCallState.PREPARED.value,
                    ProviderCallState.WORKER_STARTED.value,
                    ProviderCallState.WORKER_PREFLIGHT.value,
                }
            )
        )

    def terminal_state(self, call_id: str) -> ProviderCallState:
        values = [value for value in self.events if value["call_id"] == call_id]
        if not values:
            raise StateConflictError("provider call is absent from ledger")
        return ProviderCallState(values[-1]["state"])


def _safe_hash(value: Any) -> str | None:
    if value is None:
        return None
    try:
        return canonical_sha256(to_primitive(value))
    except Exception:
        return canonical_sha256({"type": type(value).__name__})


def _bind_completed_provider_failure_evidence(
    exc: BaseException,
    *,
    raw: Any,
    receipt: Any,
    telemetry: Any,
) -> None:
    """Retain completed-call custody when local post-processing rejects the result."""

    output_text = getattr(raw, "output_text", None)
    output_sha256: str | None = None
    output_bytes: int | None = None
    if isinstance(output_text, str):
        output_sha256 = text_sha256(output_text)
        output_bytes = len(output_text.encode("utf-8"))
    elif isinstance(receipt, dict):
        candidate = receipt.get("output_sha256")
        if isinstance(candidate, str) and re_is_sha256(candidate):
            output_sha256 = candidate
    else:
        candidate = getattr(receipt, "output_sha256", None)
        if isinstance(candidate, str) and re_is_sha256(candidate):
            output_sha256 = candidate

    exc.external_provider_calls_observed = 1
    exc.provider_call_receipt = receipt
    exc.operation_telemetry = telemetry
    exc.completed_provider_result = raw
    exc.provider_output_sha256 = output_sha256
    exc.provider_output_bytes = output_bytes


def completed_provider_failure_evidence(exc: BaseException) -> dict[str, Any]:
    """Project bounded terminal evidence without retaining raw provider content."""

    completed = getattr(exc, "completed_provider_result", None)
    if completed is None or getattr(exc, "external_provider_calls_observed", 0) != 1:
        raise ContractValidationError(
            "completed provider failure evidence is unavailable"
        )
    receipt = getattr(exc, "provider_call_receipt", None)
    telemetry = getattr(exc, "operation_telemetry", None)

    def field(value: Any, name: str) -> Any:
        if isinstance(value, dict):
            return value.get(name)
        return getattr(value, name, None)

    input_tokens = field(telemetry, "cumulative_input_tokens")
    if input_tokens is None:
        input_tokens = field(receipt, "input_tokens")
    cached_input_tokens = field(telemetry, "cumulative_cached_input_tokens")
    if cached_input_tokens is None:
        cached_input_tokens = field(receipt, "cached_input_tokens")
    uncached_input_tokens = field(telemetry, "cumulative_uncached_input_tokens")
    if (
        uncached_input_tokens is None
        and type(input_tokens) is int
        and type(cached_input_tokens) is int
    ):
        uncached_input_tokens = input_tokens - cached_input_tokens
    output_tokens = field(telemetry, "cumulative_output_tokens")
    if output_tokens is None:
        output_tokens = field(receipt, "output_tokens")
    reasoning_tokens = field(telemetry, "cumulative_reasoning_tokens")
    if reasoning_tokens is None:
        reasoning_tokens = field(receipt, "reasoning_output_tokens")

    return {
        "schema_version": "cera.completed_provider_failure_evidence.v1",
        "external_provider_calls_observed": 1,
        "provider_receipt": to_primitive(receipt) if receipt is not None else None,
        "operation_telemetry": (
            to_primitive(telemetry) if telemetry is not None else None
        ),
        "input_tokens": input_tokens,
        "cached_input_tokens": cached_input_tokens,
        "uncached_input_tokens": uncached_input_tokens,
        "output_tokens": output_tokens,
        "reasoning_tokens": reasoning_tokens,
        "provider_output_sha256": getattr(exc, "provider_output_sha256", None),
        "provider_output_bytes": getattr(exc, "provider_output_bytes", None),
        "completed_result_retained_in_memory": True,
        "raw_output_in_primary_receipt": False,
    }
