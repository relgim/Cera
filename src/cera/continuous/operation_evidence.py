"""Local immutable custody for one provider operation at the dispatch boundary."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
from threading import RLock
from typing import Any, Mapping

from cera.errors import ContractValidationError, StateConflictError
from cera.serialization import canonical_bytes, canonical_sha256, to_primitive


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _exact_raw_bytes(value: Any) -> bytes:
    output_text = getattr(value, "output_text", None)
    if isinstance(output_text, str):
        return output_text.encode("utf-8")
    return canonical_bytes(to_primitive(value))


def _atomic_new_file(path: Path, payload: bytes) -> None:
    if path.exists():
        raise StateConflictError(f"operation evidence artifact already exists: {path.name}")
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.rename(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


@dataclass(frozen=True, slots=True)
class ProviderOperationEvidenceRequestV1:
    """Exact pre-dispatch material plus closed operation custody."""

    capture: "ProviderOperationEvidenceStoreV1"
    request_bytes: bytes
    structured_output_schema: Mapping[str, Any] | None
    prompt_version: str
    schema_version: str | None
    operation_workspace: str
    role: str
    attempt: int
    archival_policy: str

    def __post_init__(self) -> None:
        if not self.request_bytes:
            raise ContractValidationError("provider evidence request bytes are empty")
        if not all(
            isinstance(value, str) and value.strip()
            for value in (
                self.prompt_version,
                self.operation_workspace,
                self.role,
                self.archival_policy,
            )
        ):
            raise ContractValidationError("provider evidence request identity is incomplete")
        if type(self.attempt) is not int or self.attempt < 1:
            raise ContractValidationError("provider evidence attempt is invalid")


class ProviderOperationEvidenceStoreV1:
    """Add-only call directories whose terminal manifests bind ledger evidence."""

    SCHEMA_VERSION = "cera.provider_operation_evidence.v1"
    THREAD_LIFECYCLE_ROLES = frozenset({"planner", "validator", "reader"})

    def __init__(self, root: Path, *, stage: str) -> None:
        if not root.is_absolute() or not stage.strip():
            raise ContractValidationError("operation evidence root/stage is invalid")
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.stage = stage
        self._turn: str | None = None
        self._attempt = 1
        self._latest_by_role: dict[str, tuple[str, str | None]] = {}
        self._lock = RLock()

    def begin_turn(self, turn: str) -> None:
        if not isinstance(turn, str) or not turn.strip():
            raise ContractValidationError("operation evidence turn is invalid")
        with self._lock:
            self._turn = turn
            self._attempt = 1

    def set_attempt(self, attempt: int) -> None:
        if type(attempt) is not int or attempt < 1:
            raise ContractValidationError("operation evidence attempt is invalid")
        with self._lock:
            if self._turn is None:
                raise StateConflictError("operation evidence turn was not started")
            self._attempt = attempt

    @property
    def attempt(self) -> int:
        return self._attempt

    def has_provider_call_for_thread(
        self,
        *,
        role: str,
        thread_identity_sha256: str,
    ) -> bool:
        if role not in self.THREAD_LIFECYCLE_ROLES:
            raise StateConflictError("provider evidence lifecycle role is not closed")
        with self._lock:
            for call_root in self.root.iterdir():
                manifest_path = call_root / "PRE_DISPATCH.json"
                if not call_root.is_dir() or not manifest_path.is_file():
                    continue
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                if (
                    manifest.get("role") == role
                    and manifest.get("thread_identity_sha256")
                    == thread_identity_sha256
                ):
                    return True
        return False

    def request(
        self,
        *,
        request_bytes: bytes,
        structured_output_schema: Mapping[str, Any] | None,
        prompt_version: str,
        schema_version: str | None,
        operation_workspace: str,
        role: str,
        archival_policy: str,
        attempt: int | None = None,
    ) -> ProviderOperationEvidenceRequestV1:
        return ProviderOperationEvidenceRequestV1(
            capture=self,
            request_bytes=request_bytes,
            structured_output_schema=structured_output_schema,
            prompt_version=prompt_version,
            schema_version=schema_version,
            operation_workspace=operation_workspace,
            role=role,
            attempt=self._attempt if attempt is None else attempt,
            archival_policy=archival_policy,
        )

    def prepare(
        self,
        request: ProviderOperationEvidenceRequestV1,
        *,
        call_id: str,
        owner: str,
        operation: str,
        route: str,
        model: str,
        effort: str | None,
        stored_thread_sha256: str | None,
    ) -> None:
        with self._lock:
            if request.capture is not self or self._turn is None:
                raise ContractValidationError("provider evidence custody changed")
            call_root = self.root / call_id
            call_root.mkdir(parents=False, exist_ok=False)
            _atomic_new_file(call_root / "REQUEST.bin", request.request_bytes)
            schema_bytes = (
                b"null"
                if request.structured_output_schema is None
                else canonical_bytes(dict(request.structured_output_schema))
            )
            _atomic_new_file(call_root / "STRUCTURED_OUTPUT_SCHEMA.json", schema_bytes)
            manifest = {
                "schema_version": self.SCHEMA_VERSION,
                "call_id": call_id,
                "stage": self.stage,
                "turn": self._turn,
                "attempt": request.attempt,
                "role": request.role,
                "operation": operation,
                "route": route,
                "model": model,
                "effort": effort,
                "prompt_version": request.prompt_version,
                "output_schema_version": request.schema_version,
                "operation_workspace": request.operation_workspace,
                "thread_identity_sha256": stored_thread_sha256,
                "archival_policy": request.archival_policy,
                "request_sha256": _sha256_bytes(request.request_bytes),
                "structured_output_schema_sha256": _sha256_bytes(schema_bytes),
            }
            _atomic_new_file(call_root / "PRE_DISPATCH.json", canonical_bytes(manifest))
            self._latest_by_role[request.role] = (call_id, stored_thread_sha256)

    def terminal(
        self,
        *,
        call_id: str,
        ledger_event: Mapping[str, Any],
        raw_result: Any = None,
        finalized_result: Any = None,
        failure: BaseException | None = None,
    ) -> None:
        with self._lock:
            call_root = self.root / call_id
            if not call_root.is_dir():
                raise StateConflictError("provider evidence call directory is absent")
            if raw_result is not None:
                raw_bytes = _exact_raw_bytes(raw_result)
                _atomic_new_file(call_root / "RAW_PROVIDER_RESULT.bin", raw_bytes)
                parsed = getattr(raw_result, "parsed_json", None)
                if parsed is None and finalized_result is not None:
                    parsed = getattr(finalized_result, "value", finalized_result)
                _atomic_new_file(
                    call_root / "PARSED_OUTPUT.json",
                    canonical_bytes(to_primitive(parsed)),
                )
                receipt = getattr(raw_result, "receipt", None)
                telemetry = getattr(raw_result, "operation_telemetry", None)
                raw_sha256 = _sha256_bytes(raw_bytes)
            else:
                receipt = (
                    getattr(failure, "provider_call_receipt", None)
                    or getattr(failure, "failure_receipt", None)
                )
                telemetry = getattr(failure, "operation_telemetry", None)
                raw_sha256 = None
            failure_receipt = None
            if failure is not None:
                failure_receipt = {
                    "failure_type": type(failure).__name__,
                    "message": str(failure),
                    "external_provider_calls_observed": getattr(
                        failure, "external_provider_calls_observed", None
                    ),
                    "provider_receipt": to_primitive(receipt),
                    "operation_telemetry": to_primitive(telemetry),
                }
                _atomic_new_file(
                    call_root / "FAILURE_RECEIPT.json",
                    canonical_bytes(failure_receipt),
                )
            terminal = {
                "schema_version": self.SCHEMA_VERSION,
                "call_id": call_id,
                "ledger_event": dict(ledger_event),
                "raw_provider_result_sha256": raw_sha256,
                "provider_receipt": to_primitive(receipt),
                "operation_telemetry": to_primitive(telemetry),
                "token_cache_latency_tool_data": {
                    "receipt": to_primitive(receipt),
                    "telemetry": to_primitive(telemetry),
                    "tool_call_count": getattr(raw_result, "tool_call_count", None),
                    "failed_tool_call_count": getattr(
                        raw_result, "failed_tool_call_count", None
                    ),
                    "tool_names": list(getattr(raw_result, "tool_names", ()) or ()),
                    "tool_server_names": list(
                        getattr(raw_result, "tool_server_names", ()) or ()
                    ),
                },
                "failure_receipt": failure_receipt,
            }
            _atomic_new_file(call_root / "TERMINAL.json", canonical_bytes(terminal))

    def record_archival(
        self,
        *,
        role: str,
        archived: bool,
        resumable: bool | None,
        disposition: str,
        thread_identity_sha256: str | None = None,
    ) -> None:
        with self._lock:
            if role not in self.THREAD_LIFECYCLE_ROLES:
                raise StateConflictError("provider evidence lifecycle role is not closed")
            latest = self._latest_by_role.get(role)
            call_id = None if latest is None else latest[0]
            call_thread_sha256 = None if latest is None else latest[1]
            if (
                thread_identity_sha256 is not None
                and thread_identity_sha256 != call_thread_sha256
            ):
                call_id = None
            if call_id is None:
                if (
                    thread_identity_sha256 is None
                    or len(thread_identity_sha256) != 64
                    or any(
                        value not in "0123456789abcdef"
                        for value in thread_identity_sha256
                    )
                ):
                    raise StateConflictError(
                        "provider evidence unbound archival lacks thread identity"
                    )
                path = self.root / (
                    f"THREAD_LIFECYCLE_{role}_{thread_identity_sha256}.json"
                )
            else:
                path = self.root / call_id / "ARCHIVAL.json"
            value = {
                "schema_version": self.SCHEMA_VERSION,
                "call_id": call_id,
                "role": role,
                "archived": archived,
                "resumable": resumable,
                "disposition": disposition,
                "provider_dispatched": call_id is not None,
            }
            effective_thread_sha256 = thread_identity_sha256 or call_thread_sha256
            if effective_thread_sha256 is not None:
                value["thread_identity_sha256"] = effective_thread_sha256
            payload = canonical_bytes(value)
            if path.exists():
                if path.read_bytes() != payload:
                    raise StateConflictError("provider evidence archival result changed")
                return
            _atomic_new_file(path, payload)

    def snapshot(self) -> dict[str, Any]:
        calls: list[dict[str, Any]] = []
        for call_root in sorted(path for path in self.root.iterdir() if path.is_dir()):
            files = {
                path.name: _sha256_bytes(path.read_bytes())
                for path in sorted(call_root.iterdir())
                if path.is_file() and not path.name.startswith(".")
            }
            calls.append(
                {
                    "call_id": call_root.name,
                    "relative_path": call_root.relative_to(self.root).as_posix(),
                    "directory_sha256": canonical_sha256(files),
                    "artifact_sha256_by_name": files,
                    "terminal": "TERMINAL.json" in files,
                }
            )
        thread_lifecycles = []
        for path in sorted(self.root.glob("THREAD_LIFECYCLE_*.json")):
            thread_lifecycles.append(
                {
                    "relative_path": path.name,
                    "sha256": _sha256_bytes(path.read_bytes()),
                    "value": json.loads(path.read_text(encoding="utf-8")),
                }
            )
        return {
            "schema_version": "cera.provider_operation_evidence_snapshot.v1",
            "stage": self.stage,
            "root": str(self.root),
            "calls": calls,
            "thread_lifecycles": thread_lifecycles,
        }
