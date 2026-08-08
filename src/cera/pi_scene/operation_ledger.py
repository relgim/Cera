"""Crash-conscious DeepSeek operation accounting for dedicated Pi sessions."""

from __future__ import annotations

from datetime import UTC, datetime
import json
import os
from pathlib import Path
from threading import RLock
from typing import Any, Mapping

from cera.errors import ContractValidationError, StateConflictError
from cera.serialization import canonical_bytes, canonical_sha256, text_sha256


class PiProviderOperationLedger:
    """Append one durable charge at every Pi ``turn_start`` event."""

    SCHEMA_VERSION = "cera.pi_scene.provider_operation_ledger.v1"

    def __init__(
        self,
        path: Path,
        *,
        maximum_operations: int,
        maximum_operations_per_invocation: int = 6,
    ) -> None:
        if not path.is_absolute():
            raise ContractValidationError("Pi provider ledger path must be absolute")
        if type(maximum_operations) is not int or maximum_operations < 1:
            raise ContractValidationError("Pi provider operation ceiling is invalid")
        if (
            type(maximum_operations_per_invocation) is not int
            or not 1 <= maximum_operations_per_invocation <= maximum_operations
        ):
            raise ContractValidationError("Pi per-invocation operation bound is invalid")
        self.path = path
        self.maximum_operations = maximum_operations
        self.maximum_operations_per_invocation = maximum_operations_per_invocation
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()

    def begin(
        self,
        *,
        candidate_id: str,
        purpose: str,
        route: str,
        request_sha256: str,
    ) -> str:
        with self._lock:
            if self.operation_count + self.maximum_operations_per_invocation > self.maximum_operations:
                raise StateConflictError(
                    "Pi invocation reservation would exceed the DeepSeek ceiling"
                )
            invocation_id = "piop-" + canonical_sha256(
                {
                    "candidate_id": candidate_id,
                    "purpose": purpose,
                    "route": route,
                    "request_sha256": request_sha256,
                    "event_index": len(self.events) + 1,
                }
            )[:28]
            self._append(
                {
                    "schema_version": self.SCHEMA_VERSION,
                    "event": "invocation_prepared",
                    "invocation_id": invocation_id,
                    "candidate_id_sha256": text_sha256(candidate_id),
                    "purpose": purpose,
                    "route": route,
                    "request_sha256": request_sha256,
                    "reserved_operations": self.maximum_operations_per_invocation,
                }
            )
            return invocation_id

    def observe_line(self, invocation_id: str, line: str) -> None:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            return
        if not isinstance(event, Mapping):
            return
        event_type = event.get("type")
        if event_type == "turn_start":
            with self._lock:
                current = self.started_for(invocation_id)
                if current >= self.maximum_operations_per_invocation:
                    raise StateConflictError("Pi exceeded its per-invocation operation bound")
                self._append(
                    {
                        "schema_version": self.SCHEMA_VERSION,
                        "event": "provider_operation_started",
                        "invocation_id": invocation_id,
                        "operation_index": current + 1,
                        "global_operation_index": self.operation_count + 1,
                    }
                )
        elif event_type == "message_end":
            message = event.get("message")
            if not isinstance(message, Mapping) or message.get("role") != "assistant":
                return
            usage = message.get("usage")
            if not isinstance(usage, Mapping):
                return
            completed = self.completed_for(invocation_id)
            started = self.started_for(invocation_id)
            if completed >= started:
                raise StateConflictError("Pi usage arrived without a charged operation")
            self._append(
                {
                    "schema_version": self.SCHEMA_VERSION,
                    "event": "provider_operation_completed",
                    "invocation_id": invocation_id,
                    "operation_index": completed + 1,
                    "usage_sha256": canonical_sha256(dict(usage)),
                    "input_tokens": _usage_int(usage, "input", "input_tokens", "prompt_tokens"),
                    "cached_input_tokens": _usage_int(
                        usage, "cacheRead", "cache_read", "cached_input_tokens", "prompt_cache_hit_tokens"
                    ),
                    "output_tokens": _usage_int(
                        usage, "output", "output_tokens", "completion_tokens"
                    ),
                    "reasoning_tokens": _usage_int(
                        usage, "reasoning", "reasoning_tokens", "reasoning_output_tokens"
                    ),
                    "finish_status": str(
                        message.get(
                            "stopReason",
                            message.get("stop_reason", message.get("finish_reason", "unknown")),
                        )
                    ),
                }
            )
        elif event_type in {
            "auto_retry_start",
            "compaction_start",
            "summarization_retry_attempt_start",
        }:
            self._append(
                {
                    "schema_version": self.SCHEMA_VERSION,
                    "event": "forbidden_automatic_operation_observed",
                    "invocation_id": invocation_id,
                    "pi_event_type": str(event_type),
                }
            )

    def finish(
        self,
        invocation_id: str,
        *,
        status: str,
        output_sha256: str | None = None,
        failure_type: str | None = None,
    ) -> None:
        if status not in {"completed", "failed"}:
            raise ContractValidationError("Pi invocation terminal status is invalid")
        self._append(
            {
                "schema_version": self.SCHEMA_VERSION,
                "event": f"invocation_{status}",
                "invocation_id": invocation_id,
                "started_operations": self.started_for(invocation_id),
                "completed_operations": self.completed_for(invocation_id),
                "output_sha256": output_sha256,
                "failure_type": failure_type,
            }
        )

    def assert_completed(self, invocation_id: str, *, parsed_operations: int) -> None:
        started = self.started_for(invocation_id)
        completed = self.completed_for(invocation_id)
        if started != parsed_operations or completed != parsed_operations:
            raise StateConflictError("Pi operation ledger differs from final JSON usage")
        if any(
            value.get("event") == "forbidden_automatic_operation_observed"
            and value.get("invocation_id") == invocation_id
            for value in self.events
        ):
            raise StateConflictError("Pi attempted a forbidden automatic operation")

    @property
    def events(self) -> tuple[dict[str, Any], ...]:
        if not self.path.is_file():
            return ()
        return tuple(
            json.loads(line)
            for line in self.path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        )

    @property
    def operation_count(self) -> int:
        return sum(value.get("event") == "provider_operation_started" for value in self.events)

    def started_for(self, invocation_id: str) -> int:
        return sum(
            value.get("event") == "provider_operation_started"
            and value.get("invocation_id") == invocation_id
            for value in self.events
        )

    def completed_for(self, invocation_id: str) -> int:
        return sum(
            value.get("event") == "provider_operation_completed"
            and value.get("invocation_id") == invocation_id
            for value in self.events
        )

    def _append(self, payload: Mapping[str, Any]) -> None:
        value = dict(payload)
        value["recorded_at_utc"] = datetime.now(UTC).isoformat(timespec="microseconds")
        with self._lock:
            with self.path.open("ab") as stream:
                stream.write(canonical_bytes(value) + b"\n")
                stream.flush()
                os.fsync(stream.fileno())


def _usage_int(usage: Mapping[str, Any], *names: str) -> int:
    for name in names:
        value = usage.get(name)
        if type(value) is int and value >= 0:
            return value
    return 0
