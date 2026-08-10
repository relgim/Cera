"""Durable custody for one retained Planner thread per chat and effort."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
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
            self._reconcile_interrupted_archives(
                path,
                session_id=session_id,
                reasoning_effort=reasoning_effort,
                compatibility_sha256=compatibility_sha256,
            )
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
            self._reconcile_interrupted_archives(
                path,
                session_id=state.session_id,
                reasoning_effort=state.reasoning_effort,
                compatibility_sha256=state.compatibility_sha256,
            )
            for directory in (
                "INTERRUPTED_TRANSPORT_THREADS",
                "COMPLETED_UNCOMMITTED_THREADS",
            ):
                archive_root = path.parent / directory
                if archive_root.is_dir():
                    retired_ids = {
                        _decode_state(value).thread_id
                        for value in archive_root.glob("thread-*.json")
                        if not value.name.endswith(".receipt.json")
                    }
                    if state.thread_id in retired_ids:
                        raise StateConflictError(
                            "Planner cannot re-persist an interrupted provider thread"
                            if directory == "INTERRUPTED_TRANSPORT_THREADS"
                            else "Planner cannot re-persist a completed uncommitted thread"
                        )
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
            # Keep the same-parent atomic temp name compact.  Repeating the
            # full hash-heavy state filename here previously crossed legacy
            # Windows MAX_PATH in isolated qualification roots.
            temporary = path.parent / f".planner-state-{uuid4().hex}.tmp"
            try:
                with temporary.open("xb") as handle:
                    handle.write(payload)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)

    def archive_interrupted_transport_thread(
        self,
        state: PlannerThreadStateV1,
    ) -> None:
        """Atomically remove one failed soft thread from active selection.

        The exact prior state bytes and a hash-only interrupted disposition are
        retained.  No provider lifecycle operation is performed here.
        """

        self._archive_never_resume_thread(
            state,
            directory="INTERRUPTED_TRANSPORT_THREADS",
            disposition="interrupted_provider_transport_never_resume",
        )

    def archive_completed_uncommitted_thread(
        self,
        state: PlannerThreadStateV1,
    ) -> None:
        """Retire a thread whose completed plan never entered durable progress."""

        self._archive_never_resume_thread(
            state,
            directory="COMPLETED_UNCOMMITTED_THREADS",
            disposition="completed_uncommitted_never_resume",
        )

    def _archive_never_resume_thread(
        self,
        state: PlannerThreadStateV1,
        *,
        directory: str,
        disposition: str,
    ) -> None:
        path = self._path(state.session_id, state.reasoning_effort)
        expected = canonical_bytes(state.to_payload())
        archive_root = path.parent / directory
        archive_path = archive_root / f"thread-{state.state_sha256[:24]}.json"
        receipt_path = archive_root / f"thread-{state.state_sha256[:24]}.receipt.json"
        receipt = _never_resume_receipt(state, disposition=disposition)
        with self._lock:
            archive_root.mkdir(parents=True, exist_ok=True)
            if archive_root.is_symlink():
                raise StateConflictError("Planner interrupted-thread archive is unsafe")
            if archive_path.exists():
                if archive_path.read_bytes() != expected:
                    raise StateConflictError("Planner interrupted-thread archive changed")
            else:
                if not path.exists() or path.read_bytes() != expected:
                    raise StateConflictError(
                        "Planner active thread changed before transport archival"
                    )
                os.replace(path, archive_path)
            if receipt_path.exists():
                try:
                    existing = json.loads(receipt_path.read_text(encoding="utf-8"))
                except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                    raise StateConflictError(
                        "Planner interrupted-thread receipt is unreadable"
                    ) from exc
                if existing != receipt:
                    raise StateConflictError("Planner interrupted-thread receipt changed")
            else:
                _atomic_write_json(receipt_path, receipt)

    def has_interrupted_thread_hash(
        self,
        *,
        session_id: str,
        reasoning_effort: str,
        compatibility_sha256: str,
        thread_id_sha256: str,
    ) -> bool:
        """Verify an exact thread is durably archived and never active."""

        return self._has_never_resume_thread_hash(
            session_id=session_id,
            reasoning_effort=reasoning_effort,
            compatibility_sha256=compatibility_sha256,
            thread_id_sha256=thread_id_sha256,
            directory="INTERRUPTED_TRANSPORT_THREADS",
            disposition="interrupted_provider_transport_never_resume",
        )

    def has_completed_uncommitted_thread_hash(
        self,
        *,
        session_id: str,
        reasoning_effort: str,
        compatibility_sha256: str,
        thread_id_sha256: str,
    ) -> bool:
        return self._has_never_resume_thread_hash(
            session_id=session_id,
            reasoning_effort=reasoning_effort,
            compatibility_sha256=compatibility_sha256,
            thread_id_sha256=thread_id_sha256,
            directory="COMPLETED_UNCOMMITTED_THREADS",
            disposition="completed_uncommitted_never_resume",
        )

    def _has_never_resume_thread_hash(
        self,
        *,
        session_id: str,
        reasoning_effort: str,
        compatibility_sha256: str,
        thread_id_sha256: str,
        directory: str,
        disposition: str,
    ) -> bool:
        _validate_lookup(session_id, reasoning_effort, compatibility_sha256)
        if not re_is_sha256(thread_id_sha256):
            raise ContractValidationError("Planner retired thread hash is invalid")
        active_path = self._path(session_id, reasoning_effort)
        with self._lock:
            self._reconcile_never_resume_archives(
                active_path,
                session_id=session_id,
                reasoning_effort=reasoning_effort,
                compatibility_sha256=compatibility_sha256,
                directory=directory,
                disposition=disposition,
            )
            if active_path.exists():
                active = _decode_state(active_path)
                if text_sha256(active.thread_id) == thread_id_sha256:
                    return False
            archive_root = active_path.parent / directory
            if not archive_root.is_dir():
                return False
            matches = tuple(
                state
                for path in archive_root.glob("thread-*.json")
                if not path.name.endswith(".receipt.json")
                for state in (_decode_state(path),)
                if text_sha256(state.thread_id) == thread_id_sha256
            )
            return len(matches) == 1

    def _reconcile_interrupted_archives(
        self,
        active_path: Path,
        *,
        session_id: str,
        reasoning_effort: str,
        compatibility_sha256: str,
    ) -> None:
        """Finish a crash-interrupted archive move without reviving its thread."""

        self._reconcile_never_resume_archives(
            active_path,
            session_id=session_id,
            reasoning_effort=reasoning_effort,
            compatibility_sha256=compatibility_sha256,
            directory="INTERRUPTED_TRANSPORT_THREADS",
            disposition="interrupted_provider_transport_never_resume",
        )
        self._reconcile_never_resume_archives(
            active_path,
            session_id=session_id,
            reasoning_effort=reasoning_effort,
            compatibility_sha256=compatibility_sha256,
            directory="COMPLETED_UNCOMMITTED_THREADS",
            disposition="completed_uncommitted_never_resume",
        )

    def _reconcile_never_resume_archives(
        self,
        active_path: Path,
        *,
        session_id: str,
        reasoning_effort: str,
        compatibility_sha256: str,
        directory: str,
        disposition: str,
    ) -> None:
        archive_root = active_path.parent / directory
        if not archive_root.exists():
            return
        if archive_root.is_symlink() or not archive_root.is_dir():
            raise StateConflictError("Planner interrupted-thread archive is unsafe")
        for archive_path in sorted(archive_root.glob("thread-*.json")):
            if archive_path.name.endswith(".receipt.json"):
                continue
            state = _decode_state(archive_path)
            if (
                state.session_id != session_id
                or state.reasoning_effort != reasoning_effort
                or state.compatibility_sha256 != compatibility_sha256
            ):
                raise StateConflictError("Planner interrupted archive changed custody")
            receipt_path = archive_path.with_name(
                archive_path.name.removesuffix(".json") + ".receipt.json"
            )
            expected = _never_resume_receipt(state, disposition=disposition)
            if receipt_path.exists():
                try:
                    existing = json.loads(receipt_path.read_text(encoding="utf-8"))
                except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                    raise StateConflictError(
                        "Planner interrupted-thread receipt is unreadable"
                    ) from exc
                if existing != expected:
                    raise StateConflictError("Planner interrupted-thread receipt changed")
            else:
                _atomic_write_json(receipt_path, expected)

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


def _never_resume_receipt(
    state: PlannerThreadStateV1,
    *,
    disposition: str,
) -> dict[str, str]:
    if disposition not in {
        "interrupted_provider_transport_never_resume",
        "completed_uncommitted_never_resume",
    }:
        raise ContractValidationError("Planner never-resume disposition is invalid")
    body = {
        "schema_version": (
            "cera.pi_scene.interrupted_planner_thread.v1"
            if disposition == "interrupted_provider_transport_never_resume"
            else "cera.pi_scene.completed_uncommitted_planner_thread.v1"
        ),
        "session_id": state.session_id,
        "reasoning_effort": state.reasoning_effort,
        "compatibility_sha256": state.compatibility_sha256,
        "thread_id_sha256": text_sha256(state.thread_id),
        "prior_state_sha256": state.state_sha256,
        "disposition": disposition,
    }
    return {**body, "receipt_sha256": canonical_sha256(body)}


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
    return isinstance(value, str) and re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,95}", value) is not None


def _atomic_write_json(path: Path, payload: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".planner-state-{uuid4().hex}.tmp"
    try:
        with temporary.open("xb") as handle:
            handle.write(canonical_bytes(payload))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
