"""Durable custody for one retained Planner thread per chat and effort."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
from threading import RLock
from typing import Callable
from uuid import uuid4

from cera.errors import ContractValidationError, StateConflictError
from cera.sequence_first.sessions import PersistentPlannerSession, PlannerThreadBackendPort
from cera.serialization import canonical_bytes, canonical_sha256, re_is_sha256, text_sha256


PLANNER_THREAD_STATE_SCHEMA = "cera.pi_scene.planner_thread_state.v1"
_EFFORTS = frozenset({"medium", "high", "xhigh"})
_STATE_KEYS = frozenset(
    {
        "schema_version",
        "session_id",
        "reasoning_effort",
        "compatibility_sha256",
        "thread_id",
        "state_sha256",
    }
)


@dataclass(frozen=True, slots=True)
class PlannerThreadStateV1:
    session_id: str
    reasoning_effort: str
    compatibility_sha256: str
    thread_id: str

    def __post_init__(self) -> None:
        if not _valid_session_id(self.session_id):
            raise ContractValidationError("Planner state session identity is invalid")
        if self.reasoning_effort not in _EFFORTS:
            raise ContractValidationError("Planner state reasoning effort is invalid")
        if not re_is_sha256(self.compatibility_sha256):
            raise ContractValidationError("Planner state compatibility hash is invalid")
        if not isinstance(self.thread_id, str) or not self.thread_id.strip():
            raise ContractValidationError("Planner state thread identity is empty")

    @property
    def unsigned_payload(self) -> dict[str, str]:
        return {
            "schema_version": PLANNER_THREAD_STATE_SCHEMA,
            "session_id": self.session_id,
            "reasoning_effort": self.reasoning_effort,
            "compatibility_sha256": self.compatibility_sha256,
            "thread_id": self.thread_id,
        }

    @property
    def state_sha256(self) -> str:
        return canonical_sha256(self.unsigned_payload)

    def to_payload(self) -> dict[str, str]:
        return {**self.unsigned_payload, "state_sha256": self.state_sha256}


class PlannerThreadStateStore:
    """Atomically persists a closed, hash-bound Planner thread record.

    A record is immutable for one ``(session_id, reasoning_effort)`` pair.  A
    missing or non-resumable provider thread is an explicit error; callers must
    never overwrite it with a new thread under the same chat identity.
    """

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        if self.root.is_symlink():
            raise StateConflictError("Planner state root must not be a symlink")
        self._lock = RLock()

    def load(
        self,
        *,
        session_id: str,
        reasoning_effort: str,
        compatibility_sha256: str,
    ) -> PlannerThreadStateV1 | None:
        _validate_lookup(session_id, reasoning_effort, compatibility_sha256)
        path = self._path(session_id, reasoning_effort)
        with self._lock:
            if not path.exists():
                return None
            state = _decode_state(path)
            if (
                state.session_id != session_id
                or state.reasoning_effort != reasoning_effort
                or state.compatibility_sha256 != compatibility_sha256
            ):
                raise StateConflictError(
                    "Planner state does not match the requested chat compatibility"
                )
            return state

    def persist(self, state: PlannerThreadStateV1) -> None:
        path = self._path(state.session_id, state.reasoning_effort)
        payload = canonical_bytes(state.to_payload())
        with self._lock:
            if path.exists():
                existing = path.read_bytes()
                if existing == payload:
                    return
                raise StateConflictError(
                    "Planner thread identity is already bound for this chat and effort"
                )
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.parent.is_symlink():
                raise StateConflictError("Planner state directory must not be a symlink")
            temporary = path.parent / f".{path.name}.{uuid4().hex}.tmp"
            try:
                with temporary.open("xb") as handle:
                    handle.write(payload)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)

    def session_factory(
        self,
        *,
        compatibility_sha256: str,
    ) -> Callable[[str, str, PlannerThreadBackendPort], PersistentPlannerSession]:
        if not re_is_sha256(compatibility_sha256):
            raise ContractValidationError("Planner compatibility hash is invalid")

        def build(
            session_id: str,
            reasoning_effort: str,
            backend: PlannerThreadBackendPort,
        ) -> PersistentPlannerSession:
            state = self.load(
                session_id=session_id,
                reasoning_effort=reasoning_effort,
                compatibility_sha256=compatibility_sha256,
            )

            def persist(thread_id: str) -> None:
                self.persist(
                    PlannerThreadStateV1(
                        session_id=session_id,
                        reasoning_effort=reasoning_effort,
                        compatibility_sha256=compatibility_sha256,
                        thread_id=thread_id,
                    )
                )

            return PersistentPlannerSession(
                backend,
                restored_thread_id=None if state is None else state.thread_id,
                persist_thread_id=persist,
            )

        return build

    def _path(self, session_id: str, reasoning_effort: str) -> Path:
        _validate_lookup(session_id, reasoning_effort, "0" * 64)
        key = text_sha256(f"{session_id}\0{reasoning_effort}")
        return self.root / f"session-{key[:24]}" / "PLANNER_THREAD_STATE.json"


def _decode_state(path: Path) -> PlannerThreadStateV1:
    if path.is_symlink() or not path.is_file():
        raise StateConflictError("Planner state file is unavailable or unsafe")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise StateConflictError("Planner state file is unreadable") from exc
    if not isinstance(value, dict) or set(value) != _STATE_KEYS:
        raise StateConflictError("Planner state schema is not closed")
    if not all(isinstance(item, str) for item in value.values()):
        raise StateConflictError("Planner state fields must be exact strings")
    if value["schema_version"] != PLANNER_THREAD_STATE_SCHEMA:
        raise StateConflictError("Planner state schema version is unsupported")
    state = PlannerThreadStateV1(
        session_id=value["session_id"],
        reasoning_effort=value["reasoning_effort"],
        compatibility_sha256=value["compatibility_sha256"],
        thread_id=value["thread_id"],
    )
    if value["state_sha256"] != state.state_sha256:
        raise StateConflictError("Planner state hash verification failed")
    return state


def _validate_lookup(
    session_id: str,
    reasoning_effort: str,
    compatibility_sha256: str,
) -> None:
    if not _valid_session_id(session_id):
        raise ContractValidationError("Planner state session identity is invalid")
    if reasoning_effort not in _EFFORTS:
        raise ContractValidationError("Planner state reasoning effort is invalid")
    if not re_is_sha256(compatibility_sha256):
        raise ContractValidationError("Planner state compatibility hash is invalid")


def _valid_session_id(value: object) -> bool:
    if not isinstance(value, str) or not 1 <= len(value) <= 96:
        return False
    return value[0].isalnum() and all(
        character.isalnum() or character in {"_", "-"} for character in value
    )
