"""Privacy-safe root diagnostics for failures before per-turn recorders exist."""

from __future__ import annotations

from datetime import UTC, datetime
import os
import re
from pathlib import Path
import traceback
from typing import Any, Callable, TypeVar

from cera.errors import ContractValidationError
from cera.serialization import canonical_bytes

from .world import redact_secrets


T = TypeVar("T")


class ContinuousRootDiagnosticRecorder:
    SCHEMA_VERSION = "cera.continuous_root_diagnostic.v1"

    def __init__(self, runtime_root: Path) -> None:
        if not runtime_root.is_absolute():
            raise ContractValidationError("root diagnostic path must be absolute")
        self.path = runtime_root / "ROOT_DIAGNOSTIC.json"
        self._payload: dict[str, Any] = {
            "schema_version": self.SCHEMA_VERSION,
            "state": "initialized",
            "current_stage": "root_initialization",
            "current_operation": "create_safe_root_diagnostic",
            "failure": None,
            "history": [],
            "provider_dispatch_started": False,
            "created_at_utc": _utc_now(),
            "updated_at_utc": _utc_now(),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._write()

    def run(self, stage: str, operation: str, function: Callable[[], T]) -> T:
        self._payload["state"] = "running"
        self._payload["current_stage"] = stage
        self._payload["current_operation"] = operation
        self._payload["history"].append({"stage": stage, "operation": operation, "state": "started", "at_utc": _utc_now()})
        self._write()
        try:
            result = function()
        except BaseException as exc:
            self.record_failure(stage, operation, exc)
            raise
        self._payload["history"].append({"stage": stage, "operation": operation, "state": "completed", "at_utc": _utc_now()})
        self._payload["state"] = "ready"
        self._write()
        return result

    def mark_provider_dispatch_started(self) -> None:
        self._payload["provider_dispatch_started"] = True
        self._write()

    def record_failure(self, stage: str, operation: str, error: BaseException) -> None:
        self._payload["state"] = "failed"
        self._payload["current_stage"] = stage
        self._payload["current_operation"] = operation
        self._payload["failure"] = {
            "stage": stage,
            "operation": operation,
            "exception_type": type(error).__name__,
            "contract_name": _safe_contract_name(error),
            "stack_frames": [
                {"file": frame.filename, "line": frame.lineno, "function": frame.name}
                for frame in traceback.extract_tb(error.__traceback__)
            ],
        }
        self._payload["history"].append({"stage": stage, "operation": operation, "state": "failed", "error_type": type(error).__name__, "at_utc": _utc_now()})
        self._write()

    def _write(self) -> None:
        self._payload["updated_at_utc"] = _utc_now()
        temporary = self.path.with_name(self.path.name + ".tmp")
        with temporary.open("wb") as stream:
            stream.write(canonical_bytes(redact_secrets(self._payload)) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, self.path)


def _safe_contract_name(error: BaseException) -> str | None:
    if not isinstance(error, AttributeError):
        return None
    match = re.search(r"attribute ['\"]([A-Za-z_][A-Za-z0-9_]*)['\"]", str(error))
    return match.group(1) if match else None


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")
