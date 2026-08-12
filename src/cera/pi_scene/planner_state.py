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
from cera.serialization import (
    bytes_sha256,
    canonical_bytes,
    canonical_sha256,
    re_is_sha256,
    text_sha256,
)

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
_INCOMPATIBLE_RECEIPT_SCHEMA = "cera.pi_scene.incompatible_planner_thread.v1"
_INCOMPATIBLE_RECEIPT_KEYS = frozenset(
    {
        "schema_version",
        "session_id",
        "reasoning_effort",
        "prior_compatibility_sha256",
        "new_compatibility_sha256",
        "thread_id_sha256",
        "prior_state_sha256",
        "prior_state_bytes_sha256",
        "disposition",
        "receipt_sha256",
    }
)
_ATOMIC_TEMP_NAME = re.compile(r"\.planner-state-[0-9a-f]{32}\.tmp")
_NEVER_RESUME_ARCHIVE_STEM = re.compile(r"thread-[0-9a-f]{24}")
_INCOMPATIBLE_ARCHIVE_STEM = re.compile(
    r"thread-[0-9a-f]{24}-to-[0-9a-f]{16}"
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
            self._reconcile_retired_archives(
                path,
                session_id=session_id,
                reasoning_effort=reasoning_effort,
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
            self._reconcile_retired_archives(
                path,
                session_id=state.session_id,
                reasoning_effort=state.reasoning_effort,
            )
            for directory in (
                "INTERRUPTED_TRANSPORT_THREADS",
                "COMPLETED_UNCOMMITTED_THREADS",
                "INCOMPATIBLE_THREADS",
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
                            else (
                                "Planner cannot re-persist a completed uncommitted thread"
                                if directory == "COMPLETED_UNCOMMITTED_THREADS"
                                else "Planner cannot re-persist an incompatible provider thread"
                            )
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

    def active_compatibility_sha256(
        self,
        *,
        session_id: str,
        reasoning_effort: str,
    ) -> str | None:
        """Read only the verified active contract identity for upgrade routing."""

        _validate_lookup(session_id, reasoning_effort, "0" * 64)
        path = self._path(session_id, reasoning_effort)
        with self._lock:
            self._reconcile_retired_archives(
                path,
                session_id=session_id,
                reasoning_effort=reasoning_effort,
            )
            if not path.exists():
                return None
            state = _decode_state(path)
            if state.session_id != session_id or state.reasoning_effort != reasoning_effort:
                raise StateConflictError("Planner active state changed lookup custody")
            return state.compatibility_sha256

    def retire_incompatible_thread(
        self,
        *,
        session_id: str,
        reasoning_effort: str,
        prior_compatibility_sha256: str,
        new_compatibility_sha256: str,
    ) -> bool:
        """Move one verified old-contract thread into a never-resume archive.

        The receipt is durably created before the atomic move.  A restart in
        that narrow interval reconciles the move from the receipt and exact
        active bytes; neither state nor receipt is overwritten.
        """

        _validate_lookup(session_id, reasoning_effort, prior_compatibility_sha256)
        _validate_lookup(session_id, reasoning_effort, new_compatibility_sha256)
        if prior_compatibility_sha256 == new_compatibility_sha256:
            raise ContractValidationError(
                "Planner incompatible retirement requires a compatibility mismatch"
            )
        path = self._path(session_id, reasoning_effort)
        with self._lock:
            self._reconcile_retired_archives(
                path,
                session_id=session_id,
                reasoning_effort=reasoning_effort,
            )
            state = _decode_state(path) if path.exists() else None
            completed = self._matching_incompatible_retirements(
                path,
                prior_compatibility_sha256=prior_compatibility_sha256,
                new_compatibility_sha256=new_compatibility_sha256,
            )
            if state is None:
                return False
            if state.compatibility_sha256 != prior_compatibility_sha256 and completed:
                if state.compatibility_sha256 == new_compatibility_sha256:
                    return False
                raise StateConflictError(
                    "Planner incompatible retirement no longer matches active compatibility"
                )
            if (
                state.session_id != session_id
                or state.reasoning_effort != reasoning_effort
                or state.compatibility_sha256 != prior_compatibility_sha256
            ):
                raise StateConflictError("Planner incompatible retirement changed active custody")
            expected = canonical_bytes(state.to_payload())
            archive_root = path.parent / "INCOMPATIBLE_THREADS"
            archive_root.mkdir(parents=True, exist_ok=True)
            if archive_root.is_symlink():
                raise StateConflictError("Planner incompatible-thread archive is unsafe")
            stem = _incompatible_archive_stem(state, new_compatibility_sha256)
            archive_path = archive_root / f"{stem}.json"
            receipt_path = archive_root / f"{stem}.receipt.json"
            receipt = _incompatible_receipt(
                state,
                new_compatibility_sha256=new_compatibility_sha256,
            )
            if archive_path.exists():
                raise StateConflictError(
                    "Planner incompatible archive already exists while thread is active"
                )
            if receipt_path.exists():
                if _read_incompatible_receipt(receipt_path) != receipt:
                    raise StateConflictError("Planner incompatible-thread receipt changed")
            else:
                _atomic_new_json(receipt_path, receipt)
            if path.read_bytes() != expected:
                raise StateConflictError(
                    "Planner active thread changed before incompatible retirement"
                )
            try:
                os.rename(path, archive_path)
            except FileExistsError as exc:
                raise StateConflictError(
                    "Planner incompatible archive cannot be overwritten"
                ) from exc
            return True

    def _matching_incompatible_retirements(
        self,
        active_path: Path,
        *,
        prior_compatibility_sha256: str,
        new_compatibility_sha256: str,
    ) -> tuple[dict[str, str], ...]:
        archive_root = active_path.parent / "INCOMPATIBLE_THREADS"
        if not archive_root.is_dir():
            return ()
        matches = tuple(
            receipt
            for receipt_path in sorted(archive_root.glob("thread-*.receipt.json"))
            for receipt in (_read_incompatible_receipt(receipt_path),)
            if receipt["prior_compatibility_sha256"] == prior_compatibility_sha256
            and receipt["new_compatibility_sha256"] == new_compatibility_sha256
        )
        if len(matches) > 1:
            raise StateConflictError("Planner incompatible retirement identity is ambiguous")
        return matches

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
            self._reconcile_retired_archives(
                path,
                session_id=state.session_id,
                reasoning_effort=state.reasoning_effort,
            )
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
            self._reconcile_retired_archives(
                active_path,
                session_id=session_id,
                reasoning_effort=reasoning_effort,
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
                if state.compatibility_sha256 == compatibility_sha256
                and text_sha256(state.thread_id) == thread_id_sha256
            )
            return len(matches) == 1

    def _reconcile_retired_archives(
        self,
        active_path: Path,
        *,
        session_id: str,
        reasoning_effort: str,
    ) -> None:
        """Reconcile all never-resume archives under one identity census."""

        incompatible_states = self._reconcile_incompatible_archives(
            active_path,
            session_id=session_id,
            reasoning_effort=reasoning_effort,
        )
        state_sha256s = {state.state_sha256 for state in incompatible_states}
        thread_id_sha256s = {
            text_sha256(state.thread_id) for state in incompatible_states
        }
        self._reconcile_interrupted_archives(
            active_path,
            session_id=session_id,
            reasoning_effort=reasoning_effort,
            state_sha256s=state_sha256s,
            thread_id_sha256s=thread_id_sha256s,
        )
        if active_path.exists():
            active_state = _decode_state(active_path)
            if (
                active_state.state_sha256 in state_sha256s
                or text_sha256(active_state.thread_id) in thread_id_sha256s
            ):
                raise StateConflictError(
                    "Planner active thread collides with a never-resume identity"
                )

    def _reconcile_interrupted_archives(
        self,
        active_path: Path,
        *,
        session_id: str,
        reasoning_effort: str,
        state_sha256s: set[str],
        thread_id_sha256s: set[str],
    ) -> None:
        """Finish archive-first moves without reviving a retired thread."""

        archive_stems: set[str] = set()
        missing_receipts: list[tuple[Path, dict[str, str]]] = []
        atomic_receipts: list[tuple[Path, Path]] = []
        for directory, disposition in (
            (
                "INTERRUPTED_TRANSPORT_THREADS",
                "interrupted_provider_transport_never_resume",
            ),
            (
                "COMPLETED_UNCOMMITTED_THREADS",
                "completed_uncommitted_never_resume",
            ),
        ):
            missing, atomic = self._reconcile_never_resume_archives(
                active_path,
                session_id=session_id,
                reasoning_effort=reasoning_effort,
                directory=directory,
                disposition=disposition,
                archive_stems=archive_stems,
                state_sha256s=state_sha256s,
                thread_id_sha256s=thread_id_sha256s,
            )
            missing_receipts.extend(missing)
            atomic_receipts.extend(atomic)
        for temporary, receipt_path in atomic_receipts:
            if receipt_path.exists():
                if receipt_path.read_bytes() != temporary.read_bytes():
                    raise StateConflictError(
                        "Planner interrupted atomic receipt conflicts"
                    )
                temporary.unlink()
            else:
                os.replace(temporary, receipt_path)
        for receipt_path, expected in missing_receipts:
            if not receipt_path.exists():
                _atomic_write_json(receipt_path, expected)

    def _reconcile_incompatible_archives(
        self,
        active_path: Path,
        *,
        session_id: str,
        reasoning_effort: str,
    ) -> tuple[PlannerThreadStateV1, ...]:
        """Finish receipt-first incompatible moves after a process interruption."""

        archive_root = active_path.parent / "INCOMPATIBLE_THREADS"
        if not archive_root.exists():
            return ()
        if archive_root.is_symlink() or not archive_root.is_dir():
            raise StateConflictError("Planner incompatible-thread archive is unsafe")
        archive_paths, receipt_paths, atomic_temps = _closed_archive_entry_census(
            archive_root,
            stem_pattern=_INCOMPATIBLE_ARCHIVE_STEM,
        )
        receipts: dict[str, dict[str, str]] = {}
        for receipt_path in sorted(receipt_paths.values()):
            receipt = _read_incompatible_receipt(receipt_path)
            if (
                receipt["session_id"] != session_id
                or receipt["reasoning_effort"] != reasoning_effort
            ):
                raise StateConflictError("Planner incompatible receipt changed custody")
            stem = receipt_path.name.removesuffix(".receipt.json")
            if stem in receipts:
                raise StateConflictError("Planner incompatible receipt identity collides")
            receipts[stem] = receipt

        active_bytes = active_path.read_bytes() if active_path.exists() else None
        active_state = _decode_state(active_path) if active_bytes is not None else None
        atomic_stems: set[str] = set()
        for temporary in atomic_temps:
            receipt = _read_incompatible_receipt(temporary)
            if (
                receipt["session_id"] != session_id
                or receipt["reasoning_effort"] != reasoning_effort
                or temporary.read_bytes() != canonical_bytes(receipt)
            ):
                raise StateConflictError(
                    "Planner incompatible atomic receipt changed custody"
                )
            stem = (
                f"thread-{receipt['prior_state_sha256'][:24]}-to-"
                f"{receipt['new_compatibility_sha256'][:16]}"
            )
            receipt_path = archive_root / f"{stem}.receipt.json"
            archive_path = archive_root / f"{stem}.json"
            if stem in atomic_stems:
                raise StateConflictError(
                    "Planner incompatible atomic receipt identity is ambiguous"
                )
            atomic_stems.add(stem)
            candidate_path = archive_path if archive_path.exists() else active_path
            if not candidate_path.exists():
                raise StateConflictError(
                    "Planner incompatible atomic receipt lacks its exact prior state"
                )
            candidate_bytes = candidate_path.read_bytes()
            candidate_state = _decode_state(candidate_path)
            if (
                receipt
                != _incompatible_receipt(
                    candidate_state,
                    new_compatibility_sha256=receipt["new_compatibility_sha256"],
                )
                or bytes_sha256(candidate_bytes) != receipt["prior_state_bytes_sha256"]
                or _incompatible_archive_stem(
                    candidate_state,
                    receipt["new_compatibility_sha256"],
                )
                != stem
            ):
                raise StateConflictError(
                    "Planner incompatible atomic receipt changed custody"
                )
            if receipt_path.exists():
                if receipt_path.read_bytes() != temporary.read_bytes():
                    raise StateConflictError(
                        "Planner incompatible atomic receipt conflicts"
                    )
            else:
                try:
                    os.link(temporary, receipt_path)
                except FileExistsError as exc:
                    raise StateConflictError(
                        "Planner incompatible atomic receipt cannot be published"
                    ) from exc
                receipt_paths[stem] = receipt_path
            receipts[stem] = receipt

        if set(archive_paths) - set(receipts):
            raise StateConflictError("Planner incompatible archive lacks its receipt")

        states: list[PlannerThreadStateV1] = []
        state_sha256s: set[str] = set()
        thread_id_sha256s: set[str] = set()
        for stem, receipt in receipts.items():
            archive_path = archive_root / f"{stem}.json"
            archive_bytes = archive_path.read_bytes() if archive_path.exists() else None
            state: PlannerThreadStateV1 | None = None
            if archive_bytes is not None:
                state = _decode_state(archive_path)
            elif active_state is not None and (
                active_state.state_sha256 == receipt["prior_state_sha256"]
                and bytes_sha256(active_bytes or b"") == receipt["prior_state_bytes_sha256"]
            ):
                state = active_state
                try:
                    os.rename(active_path, archive_path)
                except FileExistsError as exc:
                    raise StateConflictError(
                        "Planner incompatible archive cannot be overwritten"
                    ) from exc
                archive_bytes = active_bytes
                active_bytes = None
                active_state = None
            else:
                raise StateConflictError("Planner incompatible receipt lacks its exact prior state")
            assert state is not None and archive_bytes is not None
            expected = _incompatible_receipt(
                state,
                new_compatibility_sha256=receipt["new_compatibility_sha256"],
            )
            if (
                receipt != expected
                or state.compatibility_sha256 != receipt["prior_compatibility_sha256"]
                or bytes_sha256(archive_bytes) != receipt["prior_state_bytes_sha256"]
                or _incompatible_archive_stem(
                    state,
                    receipt["new_compatibility_sha256"],
                )
                != stem
            ):
                raise StateConflictError("Planner incompatible archive changed custody")
            thread_id_sha256 = text_sha256(state.thread_id)
            if (
                state.state_sha256 in state_sha256s
                or thread_id_sha256 in thread_id_sha256s
            ):
                raise StateConflictError(
                    "Planner incompatible archive identity is ambiguous"
                )
            state_sha256s.add(state.state_sha256)
            thread_id_sha256s.add(thread_id_sha256)
            states.append(state)
        for temporary in atomic_temps:
            temporary.unlink()
        return tuple(states)

    def _reconcile_never_resume_archives(
        self,
        active_path: Path,
        *,
        session_id: str,
        reasoning_effort: str,
        directory: str,
        disposition: str,
        archive_stems: set[str],
        state_sha256s: set[str],
        thread_id_sha256s: set[str],
    ) -> tuple[
        tuple[tuple[Path, dict[str, str]], ...],
        tuple[tuple[Path, Path], ...],
    ]:
        archive_root = active_path.parent / directory
        if not archive_root.exists():
            return (), ()
        if archive_root.is_symlink() or not archive_root.is_dir():
            raise StateConflictError("Planner interrupted-thread archive is unsafe")
        archive_paths, receipt_paths, atomic_temps = _closed_archive_entry_census(
            archive_root,
            stem_pattern=_NEVER_RESUME_ARCHIVE_STEM,
        )
        if set(receipt_paths) - set(archive_paths):
            raise StateConflictError(
                "Planner interrupted-thread receipt lacks its archive"
            )
        missing_receipts: list[tuple[Path, dict[str, str]]] = []
        atomic_receipts: list[tuple[Path, Path]] = []
        expected_receipts: dict[bytes, tuple[Path, dict[str, str]]] = {}
        for stem, archive_path in sorted(archive_paths.items()):
            state = _decode_state(archive_path)
            if state.session_id != session_id or state.reasoning_effort != reasoning_effort:
                raise StateConflictError("Planner interrupted archive changed custody")
            if stem != f"thread-{state.state_sha256[:24]}":
                raise StateConflictError(
                    "Planner interrupted archive filename changed custody"
                )
            thread_id_sha256 = text_sha256(state.thread_id)
            if (
                stem in archive_stems
                or state.state_sha256 in state_sha256s
                or thread_id_sha256 in thread_id_sha256s
            ):
                raise StateConflictError(
                    "Planner interrupted archive identity is ambiguous"
                )
            archive_stems.add(stem)
            state_sha256s.add(state.state_sha256)
            thread_id_sha256s.add(thread_id_sha256)
            receipt_path = archive_root / f"{stem}.receipt.json"
            expected = _never_resume_receipt(state, disposition=disposition)
            expected_receipts[canonical_bytes(expected)] = (receipt_path, expected)
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
                missing_receipts.append((receipt_path, expected))
        for temporary in atomic_temps:
            match = expected_receipts.get(temporary.read_bytes())
            if match is None:
                raise StateConflictError(
                    "Planner interrupted atomic receipt changed custody"
                )
            receipt_path, expected = match
            if any(target == receipt_path for _, target in atomic_receipts):
                raise StateConflictError(
                    "Planner interrupted atomic receipt identity is ambiguous"
                )
            if receipt_path.exists():
                if receipt_path.read_bytes() != temporary.read_bytes():
                    raise StateConflictError(
                        "Planner interrupted atomic receipt conflicts"
                    )
            atomic_receipts.append((temporary, receipt_path))
        return tuple(missing_receipts), tuple(atomic_receipts)

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


def _closed_archive_entry_census(
    archive_root: Path,
    *,
    stem_pattern: re.Pattern[str],
) -> tuple[dict[str, Path], dict[str, Path], tuple[Path, ...]]:
    archives: dict[str, Path] = {}
    receipts: dict[str, Path] = {}
    atomic_temps: list[Path] = []
    for path in sorted(archive_root.iterdir(), key=lambda value: value.name):
        is_junction = getattr(path, "is_junction", None)
        if (
            path.is_symlink()
            or (callable(is_junction) and is_junction())
            or not path.is_file()
        ):
            raise StateConflictError("Planner archive contains an unsafe entry")
        name = path.name
        if _ATOMIC_TEMP_NAME.fullmatch(name) is not None:
            atomic_temps.append(path)
            continue
        if name.endswith(".receipt.json"):
            stem = name.removesuffix(".receipt.json")
            target = receipts
        elif name.endswith(".json"):
            stem = name.removesuffix(".json")
            target = archives
        else:
            raise StateConflictError("Planner archive contains an unknown entry")
        if stem_pattern.fullmatch(stem) is None or stem in target:
            raise StateConflictError("Planner archive entry name changed custody")
        target[stem] = path
    return archives, receipts, tuple(atomic_temps)


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


def _incompatible_archive_stem(
    state: PlannerThreadStateV1,
    new_compatibility_sha256: str,
) -> str:
    return f"thread-{state.state_sha256[:24]}-to-{new_compatibility_sha256[:16]}"


def _incompatible_receipt(
    state: PlannerThreadStateV1,
    *,
    new_compatibility_sha256: str,
) -> dict[str, str]:
    if (
        not re_is_sha256(new_compatibility_sha256)
        or new_compatibility_sha256 == state.compatibility_sha256
    ):
        raise ContractValidationError(
            "Planner incompatible receipt requires a new compatibility hash"
        )
    state_bytes = canonical_bytes(state.to_payload())
    body = {
        "schema_version": _INCOMPATIBLE_RECEIPT_SCHEMA,
        "session_id": state.session_id,
        "reasoning_effort": state.reasoning_effort,
        "prior_compatibility_sha256": state.compatibility_sha256,
        "new_compatibility_sha256": new_compatibility_sha256,
        "thread_id_sha256": text_sha256(state.thread_id),
        "prior_state_sha256": state.state_sha256,
        "prior_state_bytes_sha256": bytes_sha256(state_bytes),
        "disposition": "incompatible_contract_never_resume",
    }
    return {**body, "receipt_sha256": canonical_sha256(body)}


def _read_incompatible_receipt(path: Path) -> dict[str, str]:
    if path.is_symlink() or not path.is_file():
        raise StateConflictError("Planner incompatible-thread receipt is unsafe")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise StateConflictError("Planner incompatible-thread receipt is unreadable") from exc
    if (
        not isinstance(value, dict)
        or set(value) != _INCOMPATIBLE_RECEIPT_KEYS
        or any(not isinstance(item, str) for item in value.values())
        or value["schema_version"] != _INCOMPATIBLE_RECEIPT_SCHEMA
        or value["disposition"] != "incompatible_contract_never_resume"
        or any(
            not re_is_sha256(value[key])
            for key in (
                "prior_compatibility_sha256",
                "new_compatibility_sha256",
                "thread_id_sha256",
                "prior_state_sha256",
                "prior_state_bytes_sha256",
                "receipt_sha256",
            )
        )
    ):
        raise StateConflictError("Planner incompatible-thread receipt changed shape")
    body = {key: item for key, item in value.items() if key != "receipt_sha256"}
    if value["receipt_sha256"] != canonical_sha256(body):
        raise StateConflictError("Planner incompatible-thread receipt hash changed")
    return value


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


def _atomic_new_json(path: Path, payload: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".planner-state-{uuid4().hex}.tmp"
    try:
        with temporary.open("xb") as handle:
            handle.write(canonical_bytes(payload))
            handle.flush()
            os.fsync(handle.fileno())
        try:
            # A same-directory hard-link publication is atomic and refuses an
            # existing destination on both Windows and POSIX.  The flushed
            # temporary remains available until the immutable name exists.
            os.link(temporary, path)
        except FileExistsError as exc:
            raise StateConflictError("Planner immutable receipt cannot be overwritten") from exc
    finally:
        temporary.unlink(missing_ok=True)
