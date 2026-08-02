"""Restart-safe terminal publication for continuous Job 4.

The transaction is deliberately narrower than story authority.  It owns only
the already-authorized Job 4 terminal bytes and can recover publication of
those frozen bytes without repeating setup, provider work, or acceptance.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
from typing import Any, Mapping
from uuid import uuid4

from cera.serialization import bytes_sha256, canonical_bytes


_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ContinuousJob4TransactionError(RuntimeError):
    """The terminal transaction is incomplete, inconsistent, or conflicting."""


def _read_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContinuousJob4TransactionError(f"{label} is unreadable") from exc
    if not isinstance(value, dict):
        raise ContinuousJob4TransactionError(f"{label} must be an object")
    return value


def _closed(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    actual = set(value)
    if actual != expected:
        raise ContinuousJob4TransactionError(
            f"{label} fields differ: missing={sorted(expected - actual)}, "
            f"extra={sorted(actual - expected)}"
        )


def _identity(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContinuousJob4TransactionError(f"{label} is invalid")
    return value


def _sha256(value: object, label: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ContinuousJob4TransactionError(f"{label} is invalid")
    return value


def _atomic_immutable_write(path: Path, data: bytes) -> None:
    """Create complete bytes atomically; identical replay is idempotent."""

    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            current = path.read_bytes()
        except OSError as exc:
            raise ContinuousJob4TransactionError(
                f"existing transaction artifact is unreadable: {path.name}"
            ) from exc
        if current == data:
            return
        raise ContinuousJob4TransactionError(
            f"refusing to overwrite conflicting transaction artifact: {path.name}"
        )
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


@dataclass(frozen=True, slots=True)
class ContinuousJob4FrozenPublicationV1:
    detail_relative_path: str
    detail_sha256: str
    report_relative_path: str
    report_sha256: str
    result_relative_path: str
    result_sha256: str
    recovery_terminalization: bool

    SCHEMA_VERSION = "cera.continuous_job4_frozen_publication.v1"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.SCHEMA_VERSION,
            "detail_relative_path": self.detail_relative_path,
            "detail_sha256": self.detail_sha256,
            "report_relative_path": self.report_relative_path,
            "report_sha256": self.report_sha256,
            "result_relative_path": self.result_relative_path,
            "result_sha256": self.result_sha256,
            "recovery_terminalization": self.recovery_terminalization,
        }

    @classmethod
    def from_dict(cls, raw: object) -> "ContinuousJob4FrozenPublicationV1":
        if not isinstance(raw, Mapping):
            raise ContinuousJob4TransactionError("frozen publication must be an object")
        _closed(
            raw,
            {
                "schema_version",
                "detail_relative_path",
                "detail_sha256",
                "report_relative_path",
                "report_sha256",
                "result_relative_path",
                "result_sha256",
                "recovery_terminalization",
            },
            "frozen publication",
        )
        if raw["schema_version"] != cls.SCHEMA_VERSION:
            raise ContinuousJob4TransactionError("frozen publication schema changed")
        for name in (
            "detail_relative_path",
            "report_relative_path",
            "result_relative_path",
        ):
            value = _identity(raw[name], name)
            candidate = Path(value)
            if candidate.is_absolute() or ".." in candidate.parts:
                raise ContinuousJob4TransactionError(
                    f"{name} must stay inside the transaction directory"
                )
        if not isinstance(raw["recovery_terminalization"], bool):
            raise ContinuousJob4TransactionError(
                "recovery_terminalization must be a boolean"
            )
        return cls(
            detail_relative_path=raw["detail_relative_path"],
            detail_sha256=_sha256(raw["detail_sha256"], "detail_sha256"),
            report_relative_path=raw["report_relative_path"],
            report_sha256=_sha256(raw["report_sha256"], "report_sha256"),
            result_relative_path=raw["result_relative_path"],
            result_sha256=_sha256(raw["result_sha256"], "result_sha256"),
            recovery_terminalization=raw["recovery_terminalization"],
        )


@dataclass(frozen=True, slots=True)
class ContinuousJob4TerminalTransactionV1:
    cycle_directory: Path
    cycle_id: str
    task_id: str
    authorization_sha256: str
    runtime_root: Path
    is_new: bool

    SCHEMA_VERSION = "cera.continuous_job4_terminal_transaction.v1"
    STARTED_NAME = "JOB4_TERMINAL_TRANSACTION_STARTED.json"
    FROZEN_NAME = "JOB4_TERMINAL_BYTES_FROZEN.json"
    COMMITTED_NAME = "JOB4_PUBLICATION_COMMITTED.json"

    @property
    def root(self) -> Path:
        return self.cycle_directory / "transaction" / "job4"

    @property
    def started_path(self) -> Path:
        return self.root / self.STARTED_NAME

    @property
    def frozen_path(self) -> Path:
        return self.root / self.FROZEN_NAME

    @property
    def committed_path(self) -> Path:
        return self.root / self.COMMITTED_NAME

    @property
    def state(self) -> str:
        if self.committed_path.is_file():
            self.validate_committed()
            return "committed"
        if self.frozen_path.is_file():
            self.load_frozen()
            return "frozen"
        return "started"

    @classmethod
    def begin(
        cls,
        *,
        cycle_directory: Path,
        cycle_id: str,
        task_id: str,
        authorization_sha256: str,
        runtime_root: Path,
    ) -> "ContinuousJob4TerminalTransactionV1":
        cycle = cycle_directory.resolve()
        runtime = runtime_root.resolve()
        root = cycle / "transaction" / "job4"
        started_path = root / cls.STARTED_NAME
        expected = {
            "schema_version": cls.SCHEMA_VERSION,
            "cycle_id": _identity(cycle_id, "cycle_id"),
            "task_id": _identity(task_id, "task_id"),
            "authorization_sha256": _sha256(
                authorization_sha256, "authorization_sha256"
            ),
            "runtime_root": str(runtime),
            "semantic_reentry_allowed": False,
        }
        is_new = not started_path.exists()
        _atomic_immutable_write(started_path, canonical_bytes(expected) + b"\n")
        actual = _read_object(started_path, "terminal transaction start marker")
        if actual != expected:
            raise ContinuousJob4TransactionError(
                "terminal transaction identity conflicts with this invocation"
            )
        transaction = cls(
            cycle_directory=cycle,
            cycle_id=cycle_id,
            task_id=task_id,
            authorization_sha256=authorization_sha256,
            runtime_root=runtime,
            is_new=is_new,
        )
        # Validate any later markers before the caller decides whether recovery
        # or a new semantic execution is allowed.
        transaction.state
        return transaction

    def freeze(
        self,
        *,
        detail_bytes: bytes,
        report_bytes: bytes,
        result_bytes: bytes,
        recovery_terminalization: bool,
    ) -> ContinuousJob4FrozenPublicationV1:
        if self.committed_path.exists():
            raise ContinuousJob4TransactionError(
                "terminal publication is already committed"
            )
        if self.frozen_path.exists():
            existing = self.load_frozen()
            expected_hashes = (
                bytes_sha256(detail_bytes),
                bytes_sha256(report_bytes),
                bytes_sha256(result_bytes),
            )
            if expected_hashes != (
                existing.detail_sha256,
                existing.report_sha256,
                existing.result_sha256,
            ):
                raise ContinuousJob4TransactionError(
                    "terminal bytes are already frozen with a different identity"
                )
            return existing
        generation = "recovery" if recovery_terminalization else "primary"
        publication = ContinuousJob4FrozenPublicationV1(
            detail_relative_path=f"frozen/{generation}/JOB4_DETAIL.json",
            detail_sha256=bytes_sha256(detail_bytes),
            report_relative_path=f"frozen/{generation}/JOB4_REPORT.md",
            report_sha256=bytes_sha256(report_bytes),
            result_relative_path=f"frozen/{generation}/JOB4_RESULT.json",
            result_sha256=bytes_sha256(result_bytes),
            recovery_terminalization=recovery_terminalization,
        )
        _atomic_immutable_write(
            self.root / publication.detail_relative_path, detail_bytes
        )
        _atomic_immutable_write(
            self.root / publication.report_relative_path, report_bytes
        )
        _atomic_immutable_write(
            self.root / publication.result_relative_path, result_bytes
        )
        _atomic_immutable_write(
            self.frozen_path, canonical_bytes(publication.to_dict()) + b"\n"
        )
        return self.load_frozen()

    def load_frozen(self) -> ContinuousJob4FrozenPublicationV1:
        publication = ContinuousJob4FrozenPublicationV1.from_dict(
            _read_object(self.frozen_path, "terminal frozen marker")
        )
        for relative, expected, label in (
            (
                publication.detail_relative_path,
                publication.detail_sha256,
                "frozen detail",
            ),
            (
                publication.report_relative_path,
                publication.report_sha256,
                "frozen report",
            ),
            (
                publication.result_relative_path,
                publication.result_sha256,
                "frozen result",
            ),
        ):
            path = self.root / relative
            if not path.is_file() or bytes_sha256(path.read_bytes()) != expected:
                raise ContinuousJob4TransactionError(f"{label} bytes changed")
        return publication

    def publish_frozen(self) -> dict[str, Any]:
        publication = self.load_frozen()
        report_bytes = (self.root / publication.report_relative_path).read_bytes()
        result_bytes = (self.root / publication.result_relative_path).read_bytes()
        detail_bytes = (self.root / publication.detail_relative_path).read_bytes()
        report_path = self.cycle_directory / "source" / "JOB4_REPORT.md"
        result_path = self.cycle_directory / "source" / "JOB4_RESULT.json"
        _atomic_immutable_write(report_path, report_bytes)
        _atomic_immutable_write(result_path, result_bytes)
        detail_published = False
        try:
            _atomic_immutable_write(self.runtime_root / "JOB4_DETAIL.json", detail_bytes)
            detail_published = True
        except (OSError, ContinuousJob4TransactionError):
            # The immutable transaction copy is authoritative for recovery.
            # A disposable runtime-detail projection is useful but cannot strand
            # the canonical result/report when its directory is unavailable.
            detail_published = False
        committed = {
            "schema_version": "cera.continuous_job4_publication_commit.v1",
            "cycle_id": self.cycle_id,
            "task_id": self.task_id,
            "authorization_sha256": self.authorization_sha256,
            "frozen_marker_sha256": bytes_sha256(self.frozen_path.read_bytes()),
            "result_relative_path": "source/JOB4_RESULT.json",
            "result_sha256": publication.result_sha256,
            "report_relative_path": "source/JOB4_REPORT.md",
            "report_sha256": publication.report_sha256,
            "detail_transaction_relative_path": publication.detail_relative_path,
            "detail_sha256": publication.detail_sha256,
            "runtime_detail_published": detail_published,
            "semantic_work_repeated": False,
        }
        _atomic_immutable_write(
            self.committed_path, canonical_bytes(committed) + b"\n"
        )
        return self.validate_committed()

    def validate_committed(self) -> dict[str, Any]:
        value = _read_object(self.committed_path, "terminal publication commit marker")
        _closed(
            value,
            {
                "schema_version",
                "cycle_id",
                "task_id",
                "authorization_sha256",
                "frozen_marker_sha256",
                "result_relative_path",
                "result_sha256",
                "report_relative_path",
                "report_sha256",
                "detail_transaction_relative_path",
                "detail_sha256",
                "runtime_detail_published",
                "semantic_work_repeated",
            },
            "terminal publication commit marker",
        )
        if (
            value["schema_version"]
            != "cera.continuous_job4_publication_commit.v1"
            or value["cycle_id"] != self.cycle_id
            or value["task_id"] != self.task_id
            or value["authorization_sha256"] != self.authorization_sha256
            or value["result_relative_path"] != "source/JOB4_RESULT.json"
            or value["report_relative_path"] != "source/JOB4_REPORT.md"
            or value["semantic_work_repeated"] is not False
            or not isinstance(value["runtime_detail_published"], bool)
        ):
            raise ContinuousJob4TransactionError(
                "terminal publication commit identity is invalid"
            )
        publication = self.load_frozen()
        expected = {
            "frozen_marker_sha256": bytes_sha256(self.frozen_path.read_bytes()),
            "result_sha256": publication.result_sha256,
            "report_sha256": publication.report_sha256,
            "detail_transaction_relative_path": publication.detail_relative_path,
            "detail_sha256": publication.detail_sha256,
        }
        for field, expected_value in expected.items():
            if value[field] != expected_value:
                raise ContinuousJob4TransactionError(
                    f"terminal publication commit contradicts {field}"
                )
        for path, expected_hash, label in (
            (
                self.cycle_directory / value["result_relative_path"],
                publication.result_sha256,
                "published result",
            ),
            (
                self.cycle_directory / value["report_relative_path"],
                publication.report_sha256,
                "published report",
            ),
        ):
            if not path.is_file() or bytes_sha256(path.read_bytes()) != expected_hash:
                raise ContinuousJob4TransactionError(f"{label} bytes changed")
        return value

    def frozen_result(self) -> dict[str, Any]:
        publication = self.load_frozen()
        return _read_object(
            self.root / publication.result_relative_path,
            "frozen canonical result",
        )
