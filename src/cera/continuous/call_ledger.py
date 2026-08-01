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
from cera.serialization import canonical_bytes, canonical_sha256, re_is_sha256, to_primitive


class ProviderCallState(StrEnum):
    PREPARED = "prepared_not_invoked"
    PRETRANSPORT_FAILED = "pretransport_failed"
    TRANSPORT_INVOKED = "transport_invoked"
    DISPATCH_INITIATED = "transport_invoked"  # decode-only source compatibility
    PROVIDER_COMPLETED = "provider_completed"
    PROVIDER_FAILED = "provider_failed"
    POST_VALIDATION_FAILED = "provider_completed_post_validation_failed"
    ACCEPTED = "typed_accepted"


@dataclass(frozen=True, slots=True)
class ProviderCallLedgerEventV1:
    SCHEMA_VERSION: ClassVar[str] = "cera.continuous_provider_call_ledger_event.v2"

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
        dispatch: Callable[[], T],
        finalize: Callable[[T], R],
        receipt_of: Callable[[T], Any] = lambda value: getattr(value, "receipt", None),
        telemetry_of: Callable[[T], Any] = lambda value: getattr(value, "operation_telemetry", None),
        stored_thread_sha256: str | None = None,
    ) -> R:
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
        self._record(call_id, owner, operation, ProviderCallState.PREPARED, route, model, effort, stored_thread_sha256=stored_thread_sha256)
        try:
            raw = dispatch()
        except BaseException as exc:
            observed = getattr(exc, "external_provider_calls_observed", None)
            if observed == 0 or (
                observed is None
                and isinstance(exc, (ContractValidationError, StateConflictError))
            ):
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
                raise
            self._record(call_id, owner, operation, ProviderCallState.TRANSPORT_INVOKED, route, model, effort, stored_thread_sha256=stored_thread_sha256)
            self._record(
                call_id,
                owner,
                operation,
                ProviderCallState.PROVIDER_FAILED,
                route,
                model,
                effort,
                failure_receipt_sha256=_safe_hash(
                    getattr(exc, "provider_call_receipt", None)
                    or getattr(exc, "failure_receipt", None)
                ),
                operation_telemetry_sha256=_safe_hash(
                    getattr(exc, "operation_telemetry", None)
                ),
                failure_type=type(exc).__name__,
                stored_thread_sha256=stored_thread_sha256,
            )
            raise
        self._record(call_id, owner, operation, ProviderCallState.TRANSPORT_INVOKED, route, model, effort, stored_thread_sha256=stored_thread_sha256)
        receipt_hash = _safe_hash(receipt_of(raw))
        telemetry_hash = _safe_hash(telemetry_of(raw))
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
            self._record(call_id, owner, operation, ProviderCallState.POST_VALIDATION_FAILED, route, model, effort, provider_receipt_sha256=receipt_hash, operation_telemetry_sha256=telemetry_hash, tool_sequence_sha256=tool_sequence_hash, stored_thread_sha256=stored_thread_sha256, failure_type=type(exc).__name__)
            raise
        self._record(call_id, owner, operation, ProviderCallState.ACCEPTED, route, model, effort, provider_receipt_sha256=receipt_hash, operation_telemetry_sha256=telemetry_hash, tool_sequence_sha256=tool_sequence_hash, stored_thread_sha256=stored_thread_sha256)
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
        return len({value["call_id"] for value in self.events if value["state"] == ProviderCallState.TRANSPORT_INVOKED.value})

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
