"""Authenticated HTTP control for stage-local provider Retry.

The controller returns one of two already-public response shapes: the generated
provider-stage status envelope or a normal OpenAI chat completion.  Status GETs
may perform provider-free restart reconciliation, but only an exact
backend-issued ``provider_retry`` action accepted by POST can cross a provider
boundary or resume the protected request into a later provider stage.

The cursor in this module contains only control identities and hashes.  Exact
request bytes and terminal responses remain in the route-specific protected
continuation port.
"""

from __future__ import annotations

import json
import msvcrt
import os
import re
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Any, ClassVar, Protocol
from uuid import uuid4

from cera.errors import ContractValidationError, StateConflictError
from cera.generated.provider_stage_retry_contracts_v1 import (
    ProviderStageRetryActionV1,
    ProviderStageRetryStatusEnvelopeV1,
    validate_provider_stage_retry_action_v1,
    validate_provider_stage_retry_status_envelope_v1,
)
from cera.serialization import (
    canonical_bytes,
    canonical_sha256,
    re_is_sha256,
    to_primitive,
)

from .provider_stage_retry import (
    ProviderStage,
    ProviderStageRetryChainV1,
    ProviderStageRetryPhase,
)
from .provider_stage_retry_runtime import ProviderStageRetryRuntimeServiceV1
from .runtime import ProviderStageRetryPendingError

_CHAIN_ID = re.compile(r"stage-retry-[a-f0-9]{64}")
_ACTION_ID = re.compile(r"stage-action-[a-f0-9]{64}")
_RESULT_READY_PHASES = frozenset(
    {
        ProviderStageRetryPhase.RESULT_FROZEN,
        ProviderStageRetryPhase.DOWNSTREAM_INTENT_FROZEN,
        ProviderStageRetryPhase.DOWNSTREAM_BOUND,
        ProviderStageRetryPhase.SUCCEEDED,
    }
)


class ProviderStageRetryHttpNotFoundError(LookupError):
    """Public provider-stage Retry identity is unavailable."""


@dataclass(frozen=True, slots=True)
class ProviderStageRetryHttpRequestIdentityV1:
    """Protected request identity projected without request content."""

    request_id: str
    request_sha256: str
    world_id: str
    branch_id: str

    def __post_init__(self) -> None:
        for value, field_name in (
            (self.request_id, "request ID"),
            (self.world_id, "world ID"),
            (self.branch_id, "branch ID"),
        ):
            if (
                not isinstance(value, str)
                or not value.strip()
                or len(value) > 512
                or "\x00" in value
            ):
                raise ContractValidationError(f"provider-stage HTTP {field_name} is invalid")
        if not re_is_sha256(self.request_sha256):
            raise ContractValidationError("provider-stage HTTP request identity must be SHA-256")


@dataclass(frozen=True, slots=True)
class ProviderStageRetryHttpBranchBarrierV1:
    """Content-free active request barrier for one exact branch."""

    request_id: str
    world_id: str
    branch_id: str

    def __post_init__(self) -> None:
        ProviderStageRetryHttpRequestIdentityV1(
            request_id=self.request_id,
            request_sha256="0" * 64,
            world_id=self.world_id,
            branch_id=self.branch_id,
        )


class ProviderStageRetryHttpContinuationPort(Protocol):
    """Protected route-specific continuation and completion custody.

    Implementations must advance ``latest_chain_for_chain`` before a missing
    later stage can dispatch.  This makes a lost POST discoverable from an old
    public chain ID without relying on the secondary HTTP cursor.
    """

    def latest_chain_for_chain(self, chain_id: str) -> object: ...

    def continuation_epoch_for_chain(self, chain_id: str) -> object | None: ...

    def load_terminal_completion(self, chain_id: str) -> Mapping[str, Any] | None: ...

    def resume_succeeded_chain(self, chain_id: str) -> object: ...

    def project_terminal_completion(
        self,
        *,
        chain_id: str,
        result: object,
    ) -> Mapping[str, Any]: ...

    def bind_terminal_completion(
        self,
        *,
        chain_id: str,
        completion: Mapping[str, Any],
    ) -> Mapping[str, Any]: ...

    def terminalize_failure(
        self,
        *,
        chain_id: str,
        envelope: Mapping[str, Any],
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class ProviderStageRetryHttpRegistrationV1:
    stage: ProviderStage
    continuation: ProviderStageRetryHttpContinuationPort

    def __post_init__(self) -> None:
        if type(self.stage) is not ProviderStage or self.continuation is None:
            raise ContractValidationError(
                "provider-stage HTTP continuation registration is invalid"
            )
        required = (
            "latest_chain_for_chain",
            "continuation_epoch_for_chain",
            "load_terminal_completion",
            "resume_succeeded_chain",
            "project_terminal_completion",
            "bind_terminal_completion",
            "terminalize_failure",
        )
        if any(not callable(getattr(self.continuation, name, None)) for name in required):
            raise ContractValidationError("provider-stage HTTP continuation port is incomplete")


class ProviderStageRetryHttpContinuationRegistryV1:
    """Exhaustive route-specific continuation registry for all six stages."""

    def __init__(self, registrations: Iterable[ProviderStageRetryHttpRegistrationV1]) -> None:
        by_stage: dict[ProviderStage, ProviderStageRetryHttpContinuationPort] = {}
        for registration in registrations:
            if type(registration) is not ProviderStageRetryHttpRegistrationV1:
                raise ContractValidationError(
                    "provider-stage HTTP continuation registration changed"
                )
            if registration.stage in by_stage:
                raise ContractValidationError(
                    "provider-stage HTTP continuation stage was duplicated"
                )
            by_stage[registration.stage] = registration.continuation
        if frozenset(by_stage) != frozenset(ProviderStage):
            raise ContractValidationError(
                "provider-stage HTTP continuation registry is not exhaustive"
            )
        self._by_stage = by_stage

    def for_stage(self, stage: ProviderStage) -> ProviderStageRetryHttpContinuationPort:
        if type(stage) is not ProviderStage:
            raise ContractValidationError("provider-stage HTTP stage is not closed")
        return self._by_stage[stage]

    @property
    def unique_ports(self) -> tuple[ProviderStageRetryHttpContinuationPort, ...]:
        unique: dict[int, ProviderStageRetryHttpContinuationPort] = {}
        for port in self._by_stage.values():
            unique.setdefault(id(port), port)
        return tuple(unique.values())


@dataclass(frozen=True, slots=True)
class ProviderStageRetryHttpCursorV1:
    """Hash-only durable cursor from any prior chain to current request state."""

    SCHEMA_VERSION: ClassVar[str] = "cera.provider_stage_retry_http_cursor.v1"

    request_identity: ProviderStageRetryHttpRequestIdentityV1
    chain_ids: tuple[str, ...]
    latest_chain_id: str
    submitted_actions: tuple[ProviderStageRetryActionV1, ...]
    terminal_completion_sha256: str | None
    continuation_epoch_sha256: str | None

    def __post_init__(self) -> None:
        if type(self.request_identity) is not ProviderStageRetryHttpRequestIdentityV1:
            raise ContractValidationError("provider-stage HTTP cursor identity changed")
        if (
            not self.chain_ids
            or len(set(self.chain_ids)) != len(self.chain_ids)
            or any(_CHAIN_ID.fullmatch(value) is None for value in self.chain_ids)
            or self.latest_chain_id not in self.chain_ids
        ):
            raise ContractValidationError("provider-stage HTTP cursor chain history is invalid")
        action_ids: set[str] = set()
        for raw in self.submitted_actions:
            action = validate_provider_stage_retry_action_v1(raw)
            if (
                action["action_kind"] != "provider_retry"
                or action["chain_id"] not in self.chain_ids
                or action["action_id"] in action_ids
            ):
                raise ContractValidationError(
                    "provider-stage HTTP cursor action history is invalid"
                )
            action_ids.add(action["action_id"])
        if self.terminal_completion_sha256 is not None and not re_is_sha256(
            self.terminal_completion_sha256
        ):
            raise ContractValidationError("provider-stage HTTP terminal completion hash is invalid")
        if self.continuation_epoch_sha256 is not None and not re_is_sha256(
            self.continuation_epoch_sha256
        ):
            raise ContractValidationError("provider-stage HTTP continuation epoch is invalid")

    def action(self, action_id: str) -> ProviderStageRetryActionV1 | None:
        return next(
            (action for action in self.submitted_actions if action["action_id"] == action_id),
            None,
        )

    def to_payload(self) -> dict[str, Any]:
        body = {
            "schema_version": self.SCHEMA_VERSION,
            "request_identity": {
                "request_id": self.request_identity.request_id,
                "request_sha256": self.request_identity.request_sha256,
                "world_id": self.request_identity.world_id,
                "branch_id": self.request_identity.branch_id,
            },
            "chain_ids": list(self.chain_ids),
            "latest_chain_id": self.latest_chain_id,
            "submitted_actions": [dict(value) for value in self.submitted_actions],
            "terminal_completion_sha256": self.terminal_completion_sha256,
            "continuation_epoch_sha256": self.continuation_epoch_sha256,
        }
        return {**body, "cursor_sha256": canonical_sha256(body)}


class ProtectedProviderStageRetryHttpCursorStoreV1:
    """Atomic trusted-local cursor; no prompt, provider output, or story prose."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.cursors_root = self.root / "cursors"
        self.barriers_root = self.root / "barriers"
        self.locks_root = self.root / "locks"
        self.cursors_root.mkdir(parents=True, exist_ok=True)
        self.barriers_root.mkdir(parents=True, exist_ok=True)
        self.locks_root.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()

    def begin_request(
        self,
        *,
        request_id: str,
        world_id: str,
        branch_id: str,
    ) -> ProviderStageRetryHttpBranchBarrierV1:
        """Freeze the exact HTTP request barrier before any stage dispatch."""

        barrier = ProviderStageRetryHttpBranchBarrierV1(
            request_id=request_id,
            world_id=world_id,
            branch_id=branch_id,
        )
        branch_sha256 = _branch_sha256(world_id=world_id, branch_id=branch_id)
        body = {
            "schema_version": "cera.provider_stage_retry_http_branch_barrier.v1",
            "request_id": request_id,
            "world_id": world_id,
            "branch_id": branch_id,
            "active": True,
        }
        payload = {**body, "barrier_sha256": canonical_sha256(body)}
        with self._claim(f"branch:{branch_sha256}"):
            path = self._barrier_path(branch_sha256)
            if path.exists():
                current_payload = self._read_json(path)
                if current_payload == payload:
                    return self._decode_barrier(current_payload)
                current = self._decode_barrier(current_payload)
                if current.request_id != request_id:
                    raise StateConflictError(
                        "provider-stage HTTP branch has an unresolved protected request"
                    )
                raise StateConflictError("provider-stage HTTP branch barrier changed")
            self._atomic_write(path, payload)
            return barrier

    def active_request_for_branch(
        self,
        *,
        world_id: str,
        branch_id: str,
    ) -> ProviderStageRetryHttpBranchBarrierV1 | None:
        branch_sha256 = _branch_sha256(world_id=world_id, branch_id=branch_id)
        with self._claim(f"branch:{branch_sha256}"):
            path = self._barrier_path(branch_sha256)
            return None if not path.exists() else self._decode_barrier(self._read_json(path))

    def release_request(
        self,
        *,
        request_id: str,
        world_id: str,
        branch_id: str,
    ) -> None:
        """Release only after a terminal response has durable route custody."""

        branch_sha256 = _branch_sha256(world_id=world_id, branch_id=branch_id)
        with self._claim(f"branch:{branch_sha256}"):
            path = self._barrier_path(branch_sha256)
            if not path.exists():
                return
            current = self._decode_barrier(self._read_json(path))
            if current.request_id != request_id:
                raise StateConflictError(
                    "provider-stage HTTP branch barrier changed terminal request"
                )
            path.unlink()

    def freeze(
        self,
        *,
        identity: ProviderStageRetryHttpRequestIdentityV1,
        chain_id: str,
    ) -> ProviderStageRetryHttpCursorV1:
        _require_chain_id(chain_id)
        with self._claim(identity.request_sha256):
            current = self._read_optional_unlocked(identity.request_sha256)
            if current is not None:
                if current.request_identity != identity or chain_id not in current.chain_ids:
                    raise StateConflictError("provider-stage HTTP request cursor changed identity")
                return current
            cursor = ProviderStageRetryHttpCursorV1(
                request_identity=identity,
                chain_ids=(chain_id,),
                latest_chain_id=chain_id,
                submitted_actions=(),
                terminal_completion_sha256=None,
                continuation_epoch_sha256=None,
            )
            self._write_unlocked(cursor)
            return self._read_unlocked(identity.request_sha256)

    def remember_action(
        self,
        *,
        request_sha256: str,
        action: object,
    ) -> ProviderStageRetryHttpCursorV1:
        validated = validate_provider_stage_retry_action_v1(action)
        if validated["action_kind"] != "provider_retry":
            raise StateConflictError("provider-stage HTTP POST is not Provider Retry")
        with self._claim(request_sha256):
            current = self._read_unlocked(request_sha256)
            prior = current.action(validated["action_id"])
            if prior is not None:
                if prior != validated:
                    raise StateConflictError("provider-stage HTTP action identity changed")
                return current
            if validated["chain_id"] not in current.chain_ids:
                raise StateConflictError("provider-stage HTTP action changed request custody")
            updated = ProviderStageRetryHttpCursorV1(
                request_identity=current.request_identity,
                chain_ids=current.chain_ids,
                latest_chain_id=current.latest_chain_id,
                submitted_actions=(*current.submitted_actions, validated),
                terminal_completion_sha256=current.terminal_completion_sha256,
                continuation_epoch_sha256=current.continuation_epoch_sha256,
            )
            self._write_unlocked(updated)
            return self._read_unlocked(request_sha256)

    def bind_successor(
        self,
        *,
        request_sha256: str,
        source_chain_id: str,
        successor_chain_id: str,
        continuation_epoch_sha256: str | None = None,
    ) -> ProviderStageRetryHttpCursorV1:
        _require_chain_id(source_chain_id)
        _require_chain_id(successor_chain_id)
        if continuation_epoch_sha256 is not None and not re_is_sha256(continuation_epoch_sha256):
            raise ContractValidationError("provider-stage HTTP continuation epoch must be SHA-256")
        with self._claim(request_sha256):
            current = self._read_unlocked(request_sha256)
            if source_chain_id not in current.chain_ids:
                raise StateConflictError("provider-stage HTTP successor lacks source custody")
            if current.terminal_completion_sha256 is not None and continuation_epoch_sha256 is None:
                raise StateConflictError(
                    "provider-stage HTTP terminal request cannot gain a successor"
                )
            if continuation_epoch_sha256 is not None and current.continuation_epoch_sha256 not in {
                None,
                continuation_epoch_sha256,
            }:
                raise StateConflictError("provider-stage HTTP continuation action identity changed")
            chains = current.chain_ids
            if successor_chain_id not in chains:
                chains = (*chains, successor_chain_id)
            updated = ProviderStageRetryHttpCursorV1(
                request_identity=current.request_identity,
                chain_ids=chains,
                latest_chain_id=successor_chain_id,
                submitted_actions=current.submitted_actions,
                terminal_completion_sha256=None,
                continuation_epoch_sha256=(
                    current.continuation_epoch_sha256
                    if continuation_epoch_sha256 is None
                    else continuation_epoch_sha256
                ),
            )
            self._write_unlocked(updated)
            return self._read_unlocked(request_sha256)

    def bind_terminal(
        self,
        *,
        request_sha256: str,
        completion_sha256: str,
    ) -> ProviderStageRetryHttpCursorV1:
        if not re_is_sha256(completion_sha256):
            raise ContractValidationError("provider-stage HTTP completion hash must be SHA-256")
        with self._claim(request_sha256):
            current = self._read_unlocked(request_sha256)
            if (
                current.terminal_completion_sha256 not in {None, completion_sha256}
                and current.continuation_epoch_sha256 is None
            ):
                raise StateConflictError("provider-stage HTTP terminal completion changed")
            updated = ProviderStageRetryHttpCursorV1(
                request_identity=current.request_identity,
                chain_ids=current.chain_ids,
                latest_chain_id=current.latest_chain_id,
                submitted_actions=current.submitted_actions,
                terminal_completion_sha256=completion_sha256,
                continuation_epoch_sha256=None,
            )
            self._write_unlocked(updated)
            return self._read_unlocked(request_sha256)

    def read_optional(self, request_sha256: str) -> ProviderStageRetryHttpCursorV1 | None:
        if not re_is_sha256(request_sha256):
            raise ContractValidationError("provider-stage HTTP request identity must be SHA-256")
        with self._claim(request_sha256):
            return self._read_optional_unlocked(request_sha256)

    def _read_optional_unlocked(
        self,
        request_sha256: str,
    ) -> ProviderStageRetryHttpCursorV1 | None:
        path = self._cursor_path(request_sha256)
        return None if not path.exists() else self._decode(self._read_json(path))

    def _read_unlocked(self, request_sha256: str) -> ProviderStageRetryHttpCursorV1:
        current = self._read_optional_unlocked(request_sha256)
        if current is None:
            raise StateConflictError("provider-stage HTTP request cursor is unavailable")
        return current

    def _write_unlocked(self, cursor: ProviderStageRetryHttpCursorV1) -> None:
        self._atomic_write(
            self._cursor_path(cursor.request_identity.request_sha256),
            cursor.to_payload(),
        )

    @staticmethod
    def _decode(payload: Mapping[str, Any]) -> ProviderStageRetryHttpCursorV1:
        expected = {
            "schema_version",
            "request_identity",
            "chain_ids",
            "latest_chain_id",
            "submitted_actions",
            "terminal_completion_sha256",
            "continuation_epoch_sha256",
            "cursor_sha256",
        }
        if set(payload) != expected:
            raise StateConflictError("provider-stage HTTP cursor shape changed")
        body = {key: value for key, value in payload.items() if key != "cursor_sha256"}
        if (
            body["schema_version"] != ProviderStageRetryHttpCursorV1.SCHEMA_VERSION
            or canonical_sha256(body) != payload["cursor_sha256"]
        ):
            raise StateConflictError("provider-stage HTTP cursor hash changed")
        raw_identity = body["request_identity"]
        if not isinstance(raw_identity, dict) or set(raw_identity) != {
            "request_id",
            "request_sha256",
            "world_id",
            "branch_id",
        }:
            raise StateConflictError("provider-stage HTTP cursor identity changed")
        raw_chains = body["chain_ids"]
        raw_actions = body["submitted_actions"]
        if not isinstance(raw_chains, list) or not isinstance(raw_actions, list):
            raise StateConflictError("provider-stage HTTP cursor history changed")
        try:
            return ProviderStageRetryHttpCursorV1(
                request_identity=ProviderStageRetryHttpRequestIdentityV1(**raw_identity),
                chain_ids=tuple(raw_chains),
                latest_chain_id=body["latest_chain_id"],
                submitted_actions=tuple(
                    validate_provider_stage_retry_action_v1(value) for value in raw_actions
                ),
                terminal_completion_sha256=body["terminal_completion_sha256"],
                continuation_epoch_sha256=body["continuation_epoch_sha256"],
            )
        except (ContractValidationError, TypeError, ValueError) as exc:
            raise StateConflictError("provider-stage HTTP cursor is invalid") from exc

    def _cursor_path(self, request_sha256: str) -> Path:
        if not re_is_sha256(request_sha256):
            raise ContractValidationError("provider-stage HTTP request identity must be SHA-256")
        return self.cursors_root / f"{request_sha256}.json"

    def _barrier_path(self, branch_sha256: str) -> Path:
        if not re_is_sha256(branch_sha256):
            raise ContractValidationError("provider-stage HTTP branch identity must be SHA-256")
        return self.barriers_root / f"{branch_sha256}.json"

    @staticmethod
    def _decode_barrier(payload: Mapping[str, Any]) -> ProviderStageRetryHttpBranchBarrierV1:
        expected = {
            "schema_version",
            "request_id",
            "world_id",
            "branch_id",
            "active",
            "barrier_sha256",
        }
        body = {key: value for key, value in payload.items() if key != "barrier_sha256"}
        if (
            set(payload) != expected
            or body.get("schema_version") != "cera.provider_stage_retry_http_branch_barrier.v1"
            or body.get("active") is not True
            or canonical_sha256(body) != payload.get("barrier_sha256")
        ):
            raise StateConflictError("provider-stage HTTP branch barrier changed")
        try:
            return ProviderStageRetryHttpBranchBarrierV1(
                request_id=body["request_id"],
                world_id=body["world_id"],
                branch_id=body["branch_id"],
            )
        except (ContractValidationError, TypeError, ValueError) as exc:
            raise StateConflictError("provider-stage HTTP branch barrier is invalid") from exc

    @contextmanager
    def _claim(self, identity: str) -> Iterator[None]:
        if not isinstance(identity, str) or not identity or "\x00" in identity:
            raise ContractValidationError("provider-stage HTTP claim identity is invalid")
        path = self.locks_root / f"{canonical_sha256({'identity': identity})}.lock"
        with self._lock:
            with path.open("a+b") as handle:
                handle.seek(0, os.SEEK_END)
                if handle.tell() == 0:
                    handle.write(b"\x00")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
                try:
                    yield
                finally:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        try:
            value: object = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise StateConflictError("provider-stage HTTP cursor is unreadable") from exc
        if not isinstance(value, dict):
            raise StateConflictError("provider-stage HTTP cursor is not an object")
        return value

    @staticmethod
    def _atomic_write(path: Path, payload: Mapping[str, Any]) -> None:
        temporary = path.parent / f".{path.name}.{uuid4().hex}.tmp"
        try:
            with temporary.open("xb") as handle:
                handle.write(canonical_bytes(payload))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            if temporary.exists():
                temporary.unlink()


class ProviderStageRetryHttpControllerV1:
    """Envelope-or-completion controller for authenticated loopback routes."""

    def __init__(
        self,
        *,
        runtime: ProviderStageRetryRuntimeServiceV1,
        cursor_store: ProtectedProviderStageRetryHttpCursorStoreV1,
        registrations: Iterable[ProviderStageRetryHttpRegistrationV1],
    ) -> None:
        if type(runtime) is not ProviderStageRetryRuntimeServiceV1:
            raise ContractValidationError("provider-stage HTTP runtime authority changed")
        if type(cursor_store) is not ProtectedProviderStageRetryHttpCursorStoreV1:
            raise ContractValidationError("provider-stage HTTP cursor custody changed")
        self._runtime = runtime
        self._cursors = cursor_store
        self._continuations = ProviderStageRetryHttpContinuationRegistryV1(registrations)
        self._lock = RLock()

    def assert_request_allowed(
        self,
        *,
        request_id: str,
        world_id: str,
        branch_id: str,
    ) -> None:
        """Block a different request while any generic branch barrier remains."""

        barrier = self._cursors.active_request_for_branch(
            world_id=world_id,
            branch_id=branch_id,
        )
        if barrier is None:
            return
        if (barrier.world_id, barrier.branch_id) != (world_id, branch_id):
            raise StateConflictError("provider-stage HTTP branch barrier changed scope")
        if request_id != barrier.request_id:
            raise StateConflictError(
                "provider-stage HTTP branch has an unresolved protected request"
            )

    def begin_request(
        self,
        *,
        request_id: str,
        world_id: str,
        branch_id: str,
    ) -> None:
        self.assert_request_allowed(
            request_id=request_id,
            world_id=world_id,
            branch_id=branch_id,
        )
        self._cursors.begin_request(
            request_id=request_id,
            world_id=world_id,
            branch_id=branch_id,
        )

    def active_request_for_branch(
        self,
        *,
        world_id: str,
        branch_id: str,
    ) -> ProviderStageRetryHttpBranchBarrierV1 | None:
        """Return the authenticated request-level barrier without provider work."""

        return self._cursors.active_request_for_branch(
            world_id=world_id,
            branch_id=branch_id,
        )

    def complete_request(
        self,
        *,
        request_id: str,
        world_id: str,
        branch_id: str,
    ) -> None:
        self._cursors.release_request(
            request_id=request_id,
            world_id=world_id,
            branch_id=branch_id,
        )

    def capture_pending(self, envelope: object) -> dict[str, Any]:
        """Bind a just-raised stage status to its pre-dispatch HTTP barrier."""

        canonical = validate_provider_stage_retry_status_envelope_v1(envelope)
        status = canonical["status"]
        chain_id = status.get("chain_id")
        if not isinstance(chain_id, str):
            raise ContractValidationError("provider-stage HTTP pending chain is invalid")
        chain, identity, port, _ = self._resolve_request(chain_id)
        barrier = self._cursors.active_request_for_branch(
            world_id=identity.world_id,
            branch_id=identity.branch_id,
        )
        if barrier is None or barrier.request_id != identity.request_id:
            raise StateConflictError("provider-stage HTTP pending status lacks its request barrier")
        authoritative = self._runtime.canonical_status(chain_id=chain_id)
        if authoritative != canonical:
            raise StateConflictError("provider-stage HTTP pending envelope is not authoritative")
        self._terminalize_failure_if_needed(
            chain=chain,
            envelope=canonical,
            port=port,
        )
        return dict(canonical)

    def get(self, chain_id: str) -> dict[str, Any]:
        """Return terminal completion or provider-free reconciled status."""

        _require_chain_id(chain_id)
        with self._lock:
            _, identity, _, cursor = self._resolve_request(chain_id)
            chain = self._runtime.read_chain(cursor.latest_chain_id)
            port = self._continuations.for_stage(chain.identity.stage)
            terminal = self._terminal_first(
                chain_id=chain.chain_id,
                identity=identity,
                port=port,
                cursor=cursor,
            )
            if terminal is not None:
                return terminal
            chain, envelope = self._provider_free_status(cursor.latest_chain_id)
            self._terminalize_failure_if_needed(
                chain=chain,
                envelope=envelope,
                port=port,
            )
            latest = self._validated_latest_chain(
                source_chain=chain,
                identity=identity,
                port=port,
            )
            if latest.chain_id != chain.chain_id:
                cursor = self._cursors.bind_successor(
                    request_sha256=identity.request_sha256,
                    source_chain_id=chain.chain_id,
                    successor_chain_id=latest.chain_id,
                )
                terminal = self._terminal_first(
                    chain_id=latest.chain_id,
                    identity=identity,
                    port=self._continuations.for_stage(latest.identity.stage),
                    cursor=cursor,
                )
                if terminal is not None:
                    return terminal
                latest_port = self._continuations.for_stage(latest.identity.stage)
                latest, envelope = self._provider_free_status(latest.chain_id)
                self._terminalize_failure_if_needed(
                    chain=latest,
                    envelope=envelope,
                    port=latest_port,
                )
            return dict(envelope)

    def post(
        self,
        *,
        chain_id: str,
        action_id: str,
        body: object,
    ) -> dict[str, Any]:
        """Execute or replay one exact backend-issued manual Provider Retry."""

        _require_chain_id(chain_id)
        _require_action_id(action_id)
        action = validate_provider_stage_retry_action_v1(body)
        if (
            action["chain_id"] != chain_id
            or action["action_id"] != action_id
            or action["action_kind"] != "provider_retry"
        ):
            raise StateConflictError(
                "provider-stage HTTP POST is not the exact Provider Retry action"
            )
        with self._lock:
            source, identity, port, cursor = self._resolve_request(chain_id)
            stored = cursor.action(action_id)
            if stored is not None and stored != action:
                raise StateConflictError("provider-stage HTTP action replay changed")

            latest = self._validated_latest_chain(
                source_chain=source,
                identity=identity,
                port=port,
            )
            if latest.chain_id != cursor.latest_chain_id:
                cursor = self._cursors.bind_successor(
                    request_sha256=identity.request_sha256,
                    source_chain_id=source.chain_id,
                    successor_chain_id=latest.chain_id,
                )

            if stored is not None and latest.chain_id != chain_id:
                return self._continue_or_status(
                    chain=latest,
                    identity=identity,
                    port=self._continuations.for_stage(latest.identity.stage),
                )

            if stored is None:
                _, current = self._provider_free_status(chain_id)
                issued = current.get("actions")
                if not isinstance(issued, list) or issued != [dict(action)]:
                    raise StateConflictError(
                        "provider-stage HTTP Retry action is stale or not backend-issued"
                    )
                self._cursors.remember_action(
                    request_sha256=identity.request_sha256,
                    action=action,
                )

            executed = self._runtime.execute_manual_retry(action)
            if executed.phase in _RESULT_READY_PHASES:
                self._runtime.finalize_result_once(executed.chain_id)
                executed = self._runtime.read_chain(executed.chain_id)
            if executed.phase is not ProviderStageRetryPhase.SUCCEEDED:
                envelope = self._runtime.canonical_status(chain_id=executed.chain_id)
                self._terminalize_failure_if_needed(
                    chain=executed,
                    envelope=envelope,
                    port=port,
                )
                return dict(envelope)
            return self._continue_or_status(
                chain=executed,
                identity=identity,
                port=port,
            )

    def _continue_or_status(
        self,
        *,
        chain: ProviderStageRetryChainV1,
        identity: ProviderStageRetryHttpRequestIdentityV1,
        port: ProviderStageRetryHttpContinuationPort,
    ) -> dict[str, Any]:
        terminal = self._terminal_first(
            chain_id=chain.chain_id,
            identity=identity,
            port=port,
            cursor=self._cursors.freeze(identity=identity, chain_id=chain.chain_id),
        )
        if terminal is not None:
            return terminal
        chain, envelope = self._provider_free_status(chain.chain_id)
        if chain.phase is not ProviderStageRetryPhase.SUCCEEDED:
            self._terminalize_failure_if_needed(
                chain=chain,
                envelope=envelope,
                port=port,
            )
            return dict(envelope)
        try:
            result = port.resume_succeeded_chain(chain.chain_id)
        except ProviderStageRetryPendingError as exc:
            return self._bind_successor_envelope(
                source_chain=chain,
                identity=identity,
                port=port,
                envelope=exc.envelope,
            )
        raw = to_primitive(result)
        if isinstance(raw, dict) and raw.get("schema_version") == (
            "cera.provider_stage_retry_status_envelope.v1"
        ):
            return self._bind_successor_envelope(
                source_chain=chain,
                identity=identity,
                port=port,
                envelope=raw,
            )
        projected = port.project_terminal_completion(
            chain_id=chain.chain_id,
            result=result,
        )
        completion = _project_terminal_result(projected, identity=identity)
        bound = port.bind_terminal_completion(
            chain_id=chain.chain_id,
            completion=completion,
        )
        durable = _project_terminal_result(bound, identity=identity)
        if durable != completion:
            raise StateConflictError(
                "provider-stage HTTP terminal completion changed during binding"
            )
        self._cursors.bind_terminal(
            request_sha256=identity.request_sha256,
            completion_sha256=canonical_sha256(durable),
        )
        if _terminal_result_releases_request(durable):
            self.complete_request(
                request_id=identity.request_id,
                world_id=identity.world_id,
                branch_id=identity.branch_id,
            )
        return durable

    def _bind_successor_envelope(
        self,
        *,
        source_chain: ProviderStageRetryChainV1,
        identity: ProviderStageRetryHttpRequestIdentityV1,
        port: ProviderStageRetryHttpContinuationPort,
        envelope: object,
    ) -> dict[str, Any]:
        successor = validate_provider_stage_retry_status_envelope_v1(envelope)
        status = successor["status"]
        successor_chain_id = status.get("chain_id")
        request_sha256 = _status_request_sha256(successor)
        if (
            not isinstance(successor_chain_id, str)
            or successor_chain_id == source_chain.chain_id
            or request_sha256 != identity.request_sha256
        ):
            raise StateConflictError(
                "provider-stage HTTP successor changed protected request identity"
            )
        latest = self._validated_latest_chain(
            source_chain=source_chain,
            identity=identity,
            port=port,
        )
        if latest.chain_id != successor_chain_id:
            raise StateConflictError(
                "provider-stage HTTP successor lacks durable continuation custody"
            )
        self._cursors.bind_successor(
            request_sha256=identity.request_sha256,
            source_chain_id=source_chain.chain_id,
            successor_chain_id=successor_chain_id,
        )
        authoritative = self._runtime.canonical_status(chain_id=successor_chain_id)
        if authoritative != successor:
            raise StateConflictError("provider-stage HTTP successor envelope is not authoritative")
        successor_port = self._continuations.for_stage(latest.identity.stage)
        self._terminalize_failure_if_needed(
            chain=latest,
            envelope=successor,
            port=successor_port,
        )
        return dict(successor)

    @staticmethod
    def _terminalize_failure_if_needed(
        *,
        chain: ProviderStageRetryChainV1,
        envelope: Mapping[str, Any],
        port: ProviderStageRetryHttpContinuationPort,
    ) -> None:
        """Apply route privacy policy before exposing a stable failure.

        Ambiguous custody remains reconcilable and therefore keeps exact
        protected input.  The three stable failure states are read-only at the
        Provider Retry route; their route adapter may retain separately scoped
        Recorder repair authority while redacting raw request custody.
        """

        status = envelope.get("status")
        if not isinstance(status, Mapping):
            raise StateConflictError("provider-stage terminal status changed shape")
        state = status.get("state")
        if state not in {
            "attempts_exhausted",
            "recording_repair_required",
            "recovery_required",
        }:
            return
        if status.get("chain_id") != chain.chain_id:
            raise StateConflictError("provider-stage terminal status changed chain identity")
        port.terminalize_failure(
            chain_id=chain.chain_id,
            envelope=envelope,
        )

    def _resolve_request(
        self,
        chain_id: str,
    ) -> tuple[
        ProviderStageRetryChainV1,
        ProviderStageRetryHttpRequestIdentityV1,
        ProviderStageRetryHttpContinuationPort,
        ProviderStageRetryHttpCursorV1,
    ]:
        try:
            chain = self._runtime.read_chain(chain_id)
            scope = self._runtime.scope_for_chain(chain_id)
        except (ContractValidationError, StateConflictError) as exc:
            raise ProviderStageRetryHttpNotFoundError(
                "provider-stage Retry identity is unavailable"
            ) from exc
        if chain.identity != scope.identity:
            raise StateConflictError("provider-stage HTTP scope changed chain identity")
        identity = ProviderStageRetryHttpRequestIdentityV1(
            request_id=scope.request_id,
            request_sha256=scope.request_sha256,
            world_id=scope.world_id,
            branch_id=scope.branch_id,
        )
        port = self._continuations.for_stage(chain.identity.stage)
        cursor = self._cursors.read_optional(identity.request_sha256)
        if cursor is None:
            cursor = self._cursors.freeze(identity=identity, chain_id=chain_id)
        elif cursor.request_identity != identity:
            raise StateConflictError("provider-stage HTTP cursor changed request identity")
        elif chain_id not in cursor.chain_ids:
            continuation_epoch_sha256 = self._continuation_epoch_sha256(
                chain_id=chain_id,
                identity=identity,
                port=port,
            )
            if continuation_epoch_sha256 is not None and (
                cursor.terminal_completion_sha256 is not None
                or cursor.continuation_epoch_sha256 is not None
            ):
                cursor = self._cursors.bind_successor(
                    request_sha256=identity.request_sha256,
                    source_chain_id=cursor.latest_chain_id,
                    successor_chain_id=chain_id,
                    continuation_epoch_sha256=continuation_epoch_sha256,
                )
            else:
                prior = self._runtime.read_chain(cursor.latest_chain_id)
                prior_port = self._continuations.for_stage(prior.identity.stage)
                proven = self._validated_latest_chain(
                    source_chain=prior,
                    identity=identity,
                    port=prior_port,
                )
                if proven.chain_id != chain_id:
                    raise StateConflictError(
                        "provider-stage HTTP cursor lacks durable successor custody"
                    )
                cursor = self._cursors.bind_successor(
                    request_sha256=identity.request_sha256,
                    source_chain_id=cursor.latest_chain_id,
                    successor_chain_id=chain_id,
                )
        return chain, identity, port, cursor

    @staticmethod
    def _continuation_epoch_sha256(
        *,
        chain_id: str,
        identity: ProviderStageRetryHttpRequestIdentityV1,
        port: ProviderStageRetryHttpContinuationPort,
    ) -> str | None:
        value = port.continuation_epoch_for_chain(chain_id)
        if value is None:
            return None
        projected = to_primitive(value)
        if not isinstance(projected, dict):
            raise StateConflictError("provider-stage HTTP continuation epoch changed shape")
        action_id = projected.get("action_id")
        review_id = projected.get("review_id")
        request_id = projected.get("request_id")
        action_sha256 = projected.get("normalized_action_sha256")
        projected_world_id = projected.get("world_id")
        projected_branch_id = projected.get("branch_id")
        if (
            not isinstance(action_id, str)
            or not action_id.strip()
            or len(action_id) > 512
            or "\x00" in action_id
            or not isinstance(review_id, str)
            or re.fullmatch(r"review-[a-f0-9]{28}", review_id) is None
            or request_id != identity.request_id
            or projected_world_id not in {None, identity.world_id}
            or projected_branch_id not in {None, identity.branch_id}
            or not isinstance(action_sha256, str)
            or not re_is_sha256(action_sha256)
            or _contains_forbidden_review_key(projected)
        ):
            raise StateConflictError(
                "provider-stage HTTP continuation epoch changed protected action"
            )
        # The route port authenticates the complete action/chain binding.  The
        # HTTP cursor deliberately derives its epoch only from the stable action
        # identity: Adult Scene and Filter (and ordinary semantic-action stages)
        # have distinct chain-specific hashes while continuing the same exact
        # creator action.
        return canonical_sha256(
            {
                "schema_version": "cera.provider_stage_retry_http_continuation_epoch.v1",
                "request_sha256": identity.request_sha256,
                "request_id": identity.request_id,
                "world_id": identity.world_id,
                "branch_id": identity.branch_id,
                "action_id": action_id,
                "review_id": review_id,
                "normalized_action_sha256": action_sha256,
            }
        )

    def _validated_latest_chain(
        self,
        *,
        source_chain: ProviderStageRetryChainV1,
        identity: ProviderStageRetryHttpRequestIdentityV1,
        port: ProviderStageRetryHttpContinuationPort,
    ) -> ProviderStageRetryChainV1:
        latest_value = port.latest_chain_for_chain(source_chain.chain_id)
        latest_id = (
            latest_value
            if isinstance(latest_value, str)
            else getattr(latest_value, "chain_id", None)
        )
        if not isinstance(latest_id, str):
            raise StateConflictError("provider-stage HTTP latest-chain identity is unavailable")
        _require_chain_id(latest_id)
        latest = self._runtime.read_chain(latest_id)
        latest_scope = self._runtime.scope_for_chain(latest_id)
        if (
            latest.identity != latest_scope.identity
            or latest_scope.request_sha256 != identity.request_sha256
            or latest_scope.request_id != identity.request_id
            or (latest_scope.world_id, latest_scope.branch_id)
            != (identity.world_id, identity.branch_id)
        ):
            raise StateConflictError("provider-stage HTTP latest chain changed protected request")
        return latest

    def _provider_free_status(
        self,
        chain_id: str,
    ) -> tuple[ProviderStageRetryChainV1, ProviderStageRetryStatusEnvelopeV1]:
        chain = self._runtime.recover_incomplete(chain_id)
        if chain.phase in _RESULT_READY_PHASES:
            self._runtime.finalize_result_once(chain_id)
            chain = self._runtime.read_chain(chain_id)
        envelope = self._runtime.canonical_status(chain_id=chain_id)
        actions = envelope["actions"]
        if (
            chain.phase is ProviderStageRetryPhase.BLOCKED_AMBIGUOUS
            and len(actions) == 1
            and actions[0].get("action_kind") == "check_status"
        ):
            chain = self._runtime.check_status(actions[0])
            if chain.phase in _RESULT_READY_PHASES:
                self._runtime.finalize_result_once(chain_id)
                chain = self._runtime.read_chain(chain_id)
            envelope = self._runtime.canonical_status(chain_id=chain_id)
        return chain, envelope

    def _terminal_first(
        self,
        *,
        chain_id: str,
        identity: ProviderStageRetryHttpRequestIdentityV1,
        port: ProviderStageRetryHttpContinuationPort,
        cursor: ProviderStageRetryHttpCursorV1,
    ) -> dict[str, Any] | None:
        raw = port.load_terminal_completion(chain_id)
        if raw is None:
            if cursor.terminal_completion_sha256 is not None:
                raise StateConflictError(
                    "provider-stage HTTP terminal marker lost protected completion"
                )
            return None
        completion = _project_terminal_result(raw, identity=identity)
        completion_sha256 = canonical_sha256(completion)
        if cursor.terminal_completion_sha256 not in {None, completion_sha256}:
            raise StateConflictError("provider-stage HTTP protected completion changed cursor")
        self._cursors.bind_terminal(
            request_sha256=identity.request_sha256,
            completion_sha256=completion_sha256,
        )
        if _terminal_result_releases_request(completion):
            self.complete_request(
                request_id=identity.request_id,
                world_id=identity.world_id,
                branch_id=identity.branch_id,
            )
        return completion


def provider_stage_retry_chain_id(path: str) -> str | None:
    prefix = "/v1/cera/provider-stage-retries/"
    if not isinstance(path, str) or not path.startswith(prefix):
        return None
    value = path[len(prefix) :]
    return value if _CHAIN_ID.fullmatch(value) is not None else None


def provider_stage_retry_action_identity(path: str) -> tuple[str, str] | None:
    prefix = "/v1/cera/provider-stage-retries/"
    if not isinstance(path, str) or not path.startswith(prefix):
        return None
    remainder = path[len(prefix) :]
    parts = remainder.split("/actions/")
    if (
        len(parts) != 2
        or _CHAIN_ID.fullmatch(parts[0]) is None
        or _ACTION_ID.fullmatch(parts[1]) is None
    ):
        return None
    return parts[0], parts[1]


def _project_terminal_result(
    value: object,
    *,
    identity: ProviderStageRetryHttpRequestIdentityV1,
) -> dict[str, Any]:
    projected = to_primitive(value)
    if not isinstance(projected, dict):
        raise ContractValidationError("provider-stage HTTP terminal result must be an object")
    if projected.get("schema_version") == "cera.pi_scene.review_decision.v1":
        return _project_review_decision(projected, identity=identity)
    return _project_standard_chat_completion(projected, identity=identity)


def _project_standard_chat_completion(
    projected: Mapping[str, Any],
    *,
    identity: ProviderStageRetryHttpRequestIdentityV1,
) -> dict[str, Any]:
    if set(projected) != {
        "id",
        "object",
        "created",
        "model",
        "choices",
        "usage",
        "cera",
    }:
        raise ContractValidationError("provider-stage HTTP terminal completion shape changed")
    choices = projected.get("choices")
    cera = projected.get("cera")
    usage = projected.get("usage")
    if (
        projected.get("object") != "chat.completion"
        or not isinstance(projected.get("id"), str)
        or not projected["id"].startswith("chatcmpl-")
        or type(projected.get("created")) is not int
        or projected["created"] < 0
        or not isinstance(projected.get("model"), str)
        or not isinstance(choices, list)
        or len(choices) != 1
        or not isinstance(choices[0], dict)
        or set(choices[0]) != {"index", "message", "finish_reason"}
        or choices[0].get("index") != 0
        or choices[0].get("finish_reason") != "stop"
        or not isinstance(choices[0].get("message"), dict)
        or set(choices[0]["message"]) != {"role", "content"}
        or choices[0]["message"].get("role") != "assistant"
        or not isinstance(choices[0]["message"].get("content"), str)
        or not choices[0]["message"]["content"].strip()
        or not isinstance(cera, dict)
        or not isinstance(cera.get("profile_id"), str)
        or not cera["profile_id"].startswith("cera.pi_scene.")
        or _contains_forbidden_review_key(cera)
        or not isinstance(usage, dict)
        or set(usage) != {"prompt_tokens", "completion_tokens", "total_tokens"}
        or any(type(usage[key]) is not int or usage[key] < 0 for key in usage)
    ):
        raise ContractValidationError(
            "provider-stage HTTP terminal result is not an allowed response"
        )
    prior_request_id = cera.get("request_id")
    prior_request_sha256 = cera.get("provider_stage_request_sha256")
    if prior_request_id not in {None, identity.request_id} or prior_request_sha256 not in {
        None,
        identity.request_sha256,
    }:
        raise StateConflictError(
            "provider-stage HTTP completion changed protected request identity"
        )
    cera["request_id"] = identity.request_id
    cera["provider_stage_request_sha256"] = identity.request_sha256
    return dict(projected)


def _project_review_decision(
    projected: Mapping[str, Any],
    *,
    identity: ProviderStageRetryHttpRequestIdentityV1,
) -> dict[str, Any]:
    base_keys = {
        "schema_version",
        "status",
        "creator_action",
        "story_state_committed",
        "retry_mode",
        "review",
        "successor",
        "operational_warnings",
    }
    committed_keys = {"accepted_receipt_sha256", "accepted_turn_id"}
    keys = set(projected)
    committed = projected.get("story_state_committed")
    expected = base_keys | committed_keys if committed is True else base_keys
    action = projected.get("creator_action")
    warnings = projected.get("operational_warnings")
    if (
        keys != expected
        or projected.get("schema_version") != "cera.pi_scene.review_decision.v1"
        or projected.get("status")
        != ("story_committed" if committed is True else "review_transitioned")
        or action
        not in {
            "accept",
            "accept_provisional",
            "decline",
            "regenerate",
            "replan",
            "repair_recording",
        }
        or type(committed) is not bool
        or projected.get("retry_mode") != "not_applicable"
        or not isinstance(warnings, list)
        or len(warnings) > 32
        or any(
            not isinstance(value, str) or len(value) > 240 or "\x00" in value for value in warnings
        )
    ):
        raise ContractValidationError("provider-stage HTTP review decision is not authoritative")
    review = _project_review_payload(projected.get("review"))
    successor_value = projected.get("successor")
    successor = (
        None
        if successor_value is None
        else _project_standard_chat_completion(
            _require_mapping(successor_value, "review-decision successor"),
            identity=identity,
        )
    )
    if successor is not None and action not in {"regenerate", "replan"}:
        raise ContractValidationError(
            "provider-stage HTTP review decision has an invalid successor"
        )
    result: dict[str, Any] = {
        "schema_version": "cera.pi_scene.review_decision.v1",
        "status": projected["status"],
        "creator_action": action,
        "story_state_committed": committed,
        "retry_mode": "not_applicable",
        "review": review,
        "successor": successor,
        "operational_warnings": list(warnings),
    }
    if committed:
        receipt = projected.get("accepted_receipt_sha256")
        turn_id = projected.get("accepted_turn_id")
        if (
            not isinstance(receipt, str)
            or not re_is_sha256(receipt)
            or not isinstance(turn_id, str)
            or not turn_id.strip()
        ):
            raise ContractValidationError(
                "provider-stage HTTP committed review decision changed receipt"
            )
        result["accepted_receipt_sha256"] = receipt
        result["accepted_turn_id"] = turn_id
    return result


def _project_review_payload(value: object) -> dict[str, Any]:
    review = _require_mapping(value, "review-decision review")
    base_keys = {
        "schema_version",
        "review_id",
        "state",
        "provisional",
        "route",
        "story_text",
        "candidate_id",
        "candidate_sha256",
        "primary_authority_kind",
        "primary_authority_sha256",
        "warnings",
        "warnings_block_accept",
        "recording_status",
        "story_state_committed",
        "canon_status",
        "semantic_validation",
        "request_controls",
        "creator_guidance",
        "accept_enabled",
        "provisional_accept_enabled",
        "decline_enabled",
        "regenerate_enabled",
        "replan_enabled",
        "repair_recording_enabled",
        "provider_operations",
    }
    adult_keys = {
        "provisional_acceptance_status",
        "automatic_repair_limit",
        "operation_state",
    }
    if frozenset(review) not in {frozenset(base_keys), frozenset(base_keys | adult_keys)}:
        raise ContractValidationError("provider-stage HTTP review payload shape changed")
    review_id = review.get("review_id")
    booleans = {
        "provisional",
        "warnings_block_accept",
        "story_state_committed",
        "accept_enabled",
        "provisional_accept_enabled",
        "decline_enabled",
        "regenerate_enabled",
        "replan_enabled",
        "repair_recording_enabled",
    }
    provider_operations = review.get("provider_operations")
    if (
        review.get("schema_version") != "cera.pi_scene.review.v1"
        or not isinstance(review_id, str)
        or re.fullmatch(r"review-[a-f0-9]{28}", review_id) is None
        or review.get("state")
        not in {"review_ready", "accepted", "declined", "rejected", "regenerated", "replanned"}
        or review.get("route") not in {"ordinary", "adult"}
        or not (review.get("story_text") is None or isinstance(review.get("story_text"), str))
        or not isinstance(review.get("candidate_id"), str)
        or not isinstance(review.get("candidate_sha256"), str)
        or not re_is_sha256(review["candidate_sha256"])
        or not isinstance(review.get("primary_authority_kind"), str)
        or not isinstance(review.get("primary_authority_sha256"), str)
        or not re_is_sha256(review["primary_authority_sha256"])
        or not isinstance(review.get("warnings"), list)
        or any(type(review.get(key)) is not bool for key in booleans)
        or not isinstance(provider_operations, dict)
        or any(
            not isinstance(key, str) or type(count) is not int or count < 0
            for key, count in provider_operations.items()
        )
        or _contains_forbidden_review_key(review)
    ):
        raise ContractValidationError("provider-stage HTTP review payload is invalid")
    return dict(review)


def _contains_forbidden_review_key(value: object) -> bool:
    if isinstance(value, Mapping):
        for key, item in value.items():
            name = str(key).lower()
            if (
                name == "raw"
                or name.startswith("raw_")
                or name.startswith("exact_")
                or name.endswith("_path")
                or name in {"prompt", "provider_output", "protected_full_record"}
                or _contains_forbidden_review_key(item)
            ):
                return True
    elif isinstance(value, list):
        return any(_contains_forbidden_review_key(item) for item in value)
    return False


def _require_mapping(value: object, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ContractValidationError(f"provider-stage HTTP {field_name} must be an object")
    return value


def _terminal_result_releases_request(value: Mapping[str, Any]) -> bool:
    if value.get("object") == "chat.completion":
        cera = value.get("cera")
        if not isinstance(cera, Mapping):
            raise StateConflictError("provider-stage terminal completion lost CERA metadata")
        return cera.get("provisional") is not True
    if value.get("schema_version") == "cera.pi_scene.review_decision.v1":
        successor = value.get("successor")
        if successor is None:
            return True
        if not isinstance(successor, Mapping):
            raise StateConflictError("provider-stage review decision successor changed shape")
        cera = successor.get("cera")
        if not isinstance(cera, Mapping):
            raise StateConflictError("provider-stage review successor lost CERA metadata")
        return cera.get("provisional") is not True
    raise StateConflictError("provider-stage terminal result changed response family")


def _status_request_sha256(envelope: Mapping[str, Any]) -> str:
    status = envelope.get("status")
    technical = status.get("technical_details") if isinstance(status, dict) else None
    value = technical.get("request_sha256") if isinstance(technical, dict) else None
    if not isinstance(value, str) or not re_is_sha256(value):
        raise ContractValidationError("provider-stage HTTP status lacks request identity")
    return value


def _branch_sha256(*, world_id: str, branch_id: str) -> str:
    for value, field_name in ((world_id, "world ID"), (branch_id, "branch ID")):
        if not isinstance(value, str) or not value.strip() or len(value) > 512 or "\x00" in value:
            raise ContractValidationError(f"provider-stage HTTP {field_name} is invalid")
    return canonical_sha256(
        {
            "schema_version": "cera.provider_stage_retry_http_branch_identity.v1",
            "world_id": world_id,
            "branch_id": branch_id,
        }
    )


def _require_chain_id(chain_id: str) -> None:
    if not isinstance(chain_id, str) or _CHAIN_ID.fullmatch(chain_id) is None:
        raise ProviderStageRetryHttpNotFoundError("provider-stage Retry identity is unavailable")


def _require_action_id(action_id: str) -> None:
    if not isinstance(action_id, str) or _ACTION_ID.fullmatch(action_id) is None:
        raise ProviderStageRetryHttpNotFoundError(
            "provider-stage Retry action identity is unavailable"
        )


__all__ = [
    "ProtectedProviderStageRetryHttpCursorStoreV1",
    "ProviderStageRetryHttpBranchBarrierV1",
    "ProviderStageRetryHttpContinuationPort",
    "ProviderStageRetryHttpControllerV1",
    "ProviderStageRetryHttpNotFoundError",
    "ProviderStageRetryHttpRegistrationV1",
    "ProviderStageRetryHttpRequestIdentityV1",
    "provider_stage_retry_action_identity",
    "provider_stage_retry_chain_id",
]
