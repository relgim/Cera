"""Branch-bound continuous session custody for the shadow route.

The ledger deliberately stores context events rather than treating provider
conversation as canon.  Live adapters use the same stored Codex thread
backend as :mod:`cera.reasoner_session`; the in-memory adapter below allows the
state machine to be proven without provider calls.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
import json
import os
from pathlib import Path
from typing import Callable, ClassVar, Protocol, runtime_checkable

from cera.errors import ContractValidationError, StateConflictError
from cera.schema import from_mapping
from cera.serialization import (
    canonical_bytes,
    canonical_sha256,
    domain_sha256,
    re_is_sha256,
    text_sha256,
    to_primitive,
)

from .contracts import AcceptedFinalSequenceEnvelopeV1


class ContinuousSessionRole(StrEnum):
    PLANNER = "planner"
    VALIDATOR = "validator"


@dataclass(frozen=True, slots=True)
class ContinuousThreadArchiveEvidenceV1:
    """Privacy-safe proof that one stored role thread is terminally isolated."""

    role: ContinuousSessionRole
    provider_thread_id_sha256: str
    archive_reason_sha256: str
    archive_request_completed: bool
    resume_succeeded_after_archive: bool | None
    backend_selectable_after_archive: bool | None
    coordinator_selectable_as_accepted_ancestry: bool
    archive_error_type: str | None = None
    resume_error_type: str | None = None
    selection_error_type: str | None = None

    SCHEMA_VERSION: ClassVar[str] = "cera.continuous_thread_archive_evidence.v1"

    def __post_init__(self) -> None:
        for field_name in (
            "provider_thread_id_sha256",
            "archive_reason_sha256",
        ):
            if not re_is_sha256(getattr(self, field_name)):
                raise ContractValidationError(
                    f"continuous archive evidence {field_name} is invalid"
                )
        for field_name in (
            "archive_request_completed",
            "coordinator_selectable_as_accepted_ancestry",
        ):
            if not isinstance(getattr(self, field_name), bool):
                raise ContractValidationError(
                    f"continuous archive evidence {field_name} is invalid"
                )
        for field_name in (
            "resume_succeeded_after_archive",
            "backend_selectable_after_archive",
        ):
            value = getattr(self, field_name)
            if value is not None and not isinstance(value, bool):
                raise ContractValidationError(
                    f"continuous archive evidence {field_name} is invalid"
                )
        for field_name in (
            "archive_error_type",
            "resume_error_type",
            "selection_error_type",
        ):
            value = getattr(self, field_name)
            if value is not None and (
                not isinstance(value, str) or not value.strip() or len(value) > 128
            ):
                raise ContractValidationError(
                    f"continuous archive evidence {field_name} is invalid"
                )

    @property
    def verified(self) -> bool:
        return (
            self.archive_request_completed
            and self.resume_succeeded_after_archive is False
            and self.backend_selectable_after_archive is False
            and self.coordinator_selectable_as_accepted_ancestry is False
            and self.archive_error_type is None
            and self.resume_error_type is None
            and self.selection_error_type is None
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.SCHEMA_VERSION,
            "role": self.role.value,
            "provider_thread_id_sha256": self.provider_thread_id_sha256,
            "archive_reason_sha256": self.archive_reason_sha256,
            "archive_request_completed": self.archive_request_completed,
            "resume_succeeded_after_archive": self.resume_succeeded_after_archive,
            "backend_selectable_after_archive": self.backend_selectable_after_archive,
            "coordinator_selectable_as_accepted_ancestry": (
                self.coordinator_selectable_as_accepted_ancestry
            ),
            "archive_error_type": self.archive_error_type,
            "resume_error_type": self.resume_error_type,
            "selection_error_type": self.selection_error_type,
            "verified": self.verified,
        }

    @classmethod
    def from_dict(cls, raw: object) -> "ContinuousThreadArchiveEvidenceV1":
        if not isinstance(raw, dict):
            raise ContractValidationError(
                "continuous archive evidence must be an object"
            )
        expected = {
            "schema_version",
            "role",
            "provider_thread_id_sha256",
            "archive_reason_sha256",
            "archive_request_completed",
            "resume_succeeded_after_archive",
            "backend_selectable_after_archive",
            "coordinator_selectable_as_accepted_ancestry",
            "archive_error_type",
            "resume_error_type",
            "selection_error_type",
            "verified",
        }
        if set(raw) != expected:
            raise ContractValidationError(
                "continuous archive evidence fields changed"
            )
        if raw["schema_version"] != cls.SCHEMA_VERSION:
            raise ContractValidationError(
                "continuous archive evidence schema changed"
            )
        try:
            role = ContinuousSessionRole(raw["role"])
        except (TypeError, ValueError) as exc:
            raise ContractValidationError(
                "continuous archive evidence role is invalid"
            ) from exc
        instance = cls(
            role=role,
            provider_thread_id_sha256=raw["provider_thread_id_sha256"],
            archive_reason_sha256=raw["archive_reason_sha256"],
            archive_request_completed=raw["archive_request_completed"],
            resume_succeeded_after_archive=raw[
                "resume_succeeded_after_archive"
            ],
            backend_selectable_after_archive=raw[
                "backend_selectable_after_archive"
            ],
            coordinator_selectable_as_accepted_ancestry=raw[
                "coordinator_selectable_as_accepted_ancestry"
            ],
            archive_error_type=raw["archive_error_type"],
            resume_error_type=raw["resume_error_type"],
            selection_error_type=raw["selection_error_type"],
        )
        if (
            not isinstance(raw["verified"], bool)
            or raw["verified"] != instance.verified
        ):
            raise ContractValidationError(
                "continuous archive evidence verification is contradictory"
            )
        return instance


def unavailable_thread_archive_evidence(
    role: ContinuousSessionRole,
    *,
    error_type: str = "Unavailable",
) -> ContinuousThreadArchiveEvidenceV1:
    """Build complete negative custody when no terminal handle was available."""

    return ContinuousThreadArchiveEvidenceV1(
        role=role,
        provider_thread_id_sha256=text_sha256(
            f"continuous-job4-unavailable-{role.value}"
        ),
        archive_reason_sha256=text_sha256(
            "continuous_job4_terminal_archive_evidence_unavailable"
        ),
        archive_request_completed=False,
        resume_succeeded_after_archive=None,
        backend_selectable_after_archive=None,
        coordinator_selectable_as_accepted_ancestry=False,
        archive_error_type=error_type,
        resume_error_type=None,
        selection_error_type=None,
    )


@dataclass(frozen=True, slots=True)
class ContinuousSessionCompatibilityV1:
    SCHEMA_VERSION: ClassVar[str] = "cera.continuous_session_compatibility.v2"

    schema_version: str
    world_id: str
    branch_id: str
    role: ContinuousSessionRole
    provider: str
    model: str
    reasoning_effort: str
    prompt_version: str
    output_schema_version: str
    world_directory_identity_sha256: str
    authority_policy_version: str
    privacy_policy_version: str
    protected_user_policy_version: str
    session_policy_version: str
    ingress_classifier_registry_sha256: str
    persistence_policy_sha256: str

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("continuous compatibility schema changed")
        for field in (
            "world_id",
            "branch_id",
            "provider",
            "model",
            "reasoning_effort",
            "prompt_version",
            "output_schema_version",
            "authority_policy_version",
            "privacy_policy_version",
            "protected_user_policy_version",
            "session_policy_version",
        ):
            value = getattr(self, field)
            if not isinstance(value, str) or not value.strip() or len(value) > 192:
                raise ContractValidationError(f"continuous compatibility {field} is invalid")
        for field in (
            "world_directory_identity_sha256",
            "ingress_classifier_registry_sha256",
            "persistence_policy_sha256",
        ):
            if not re_is_sha256(getattr(self, field)):
                raise ContractValidationError(
                    f"continuous compatibility {field} is invalid"
                )

    @property
    def compatibility_sha256(self) -> str:
        return domain_sha256(self.SCHEMA_VERSION, self)


@dataclass(frozen=True, slots=True)
class ContinuousSessionHandleV1:
    SCHEMA_VERSION: ClassVar[str] = "cera.continuous_session_handle.v1"

    schema_version: str
    provider_session_id: str
    provider_thread_id: str
    provider_thread_id_sha256: str

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("continuous session handle schema changed")
        for field in ("provider_session_id", "provider_thread_id"):
            value = getattr(self, field)
            if not isinstance(value, str) or not value.strip():
                raise ContractValidationError(f"continuous {field} is invalid")
        if self.provider_thread_id_sha256 != text_sha256(self.provider_thread_id):
            raise ContractValidationError("continuous provider thread hash changed")


@dataclass(frozen=True, slots=True)
class ContinuousContextEventV1:
    event_type: str
    turn_or_scene_id: str
    payload_sha256: str
    supersedes_payload_sha256: str | None = None

    def __post_init__(self) -> None:
        if self.event_type not in {
            "planner_provisional_sequence",
            "accepted_final_sequence",
            "accepted_final_sequence_synchronized",
            "validator_candidate",
            "validator_rejected_candidate",
            "scene_change",
            "scene_summary",
            "character_summary",
        }:
            raise ContractValidationError("continuous context event type is invalid")
        if not self.turn_or_scene_id.strip():
            raise ContractValidationError("continuous context event identity is empty")
        if not re_is_sha256(self.payload_sha256):
            raise ContractValidationError("continuous context event hash is invalid")
        if self.supersedes_payload_sha256 is not None and not re_is_sha256(
            self.supersedes_payload_sha256
        ):
            raise ContractValidationError("superseded context hash is invalid")


@dataclass(frozen=True, slots=True)
class ContinuousSessionSnapshotV1:
    SCHEMA_VERSION: ClassVar[str] = "cera.continuous_session_snapshot.v1"

    schema_version: str
    compatibility: ContinuousSessionCompatibilityV1
    handle: ContinuousSessionHandleV1
    context_events: tuple[ContinuousContextEventV1, ...]
    accepted_turn_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("continuous session snapshot schema changed")
        if len(self.accepted_turn_ids) != len(set(self.accepted_turn_ids)):
            raise ContractValidationError("continuous accepted turn IDs are duplicated")
        accepted_events = tuple(
            value.turn_or_scene_id
            for value in self.context_events
            if value.event_type == "accepted_final_sequence"
        )
        if accepted_events != self.accepted_turn_ids:
            raise ContractValidationError("continuous accepted-event index drifted")

    @property
    def snapshot_sha256(self) -> str:
        return domain_sha256(self.SCHEMA_VERSION, self)


@dataclass(frozen=True, slots=True)
class ContinuousContextInjectionReceiptV1:
    SCHEMA_VERSION: ClassVar[str] = "cera.continuous_context_injection_receipt.v1"

    schema_version: str
    accepted_turn_id: str
    accepted_envelope_sha256: str
    provider_thread_sha256: str
    injected_context_sha256: str
    operation_receipt_sha256: str

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("continuous injection receipt schema changed")
        if not self.accepted_turn_id.strip():
            raise ContractValidationError("continuous injection turn is empty")
        for value in (
            self.accepted_envelope_sha256,
            self.provider_thread_sha256,
            self.injected_context_sha256,
            self.operation_receipt_sha256,
        ):
            if not re_is_sha256(value):
                raise ContractValidationError("continuous injection receipt hash is invalid")
        expected = canonical_sha256(
            {
                "schema_version": self.SCHEMA_VERSION,
                "accepted_turn_id": self.accepted_turn_id,
                "accepted_envelope_sha256": self.accepted_envelope_sha256,
                "provider_thread_sha256": self.provider_thread_sha256,
                "injected_context_sha256": self.injected_context_sha256,
            }
        )
        if self.operation_receipt_sha256 != expected:
            raise ContractValidationError("continuous injection receipt binding changed")


@dataclass(frozen=True, slots=True)
class ContinuousSessionSnapshotReceiptV1:
    """Immutable acceptance-bound snapshot evidence, separate from current pointer."""

    SCHEMA_VERSION: ClassVar[str] = "cera.continuous_session_snapshot_receipt.v1"

    schema_version: str
    world_id: str
    branch_id: str
    role: ContinuousSessionRole
    accepted_turn_id: str
    accepted_envelope_sha256: str
    provider_thread_sha256: str
    snapshot_sha256: str
    injection_operation_receipt_sha256: str
    immutable_relative_path: str
    immutable_file_sha256: str
    current_relative_path: str
    receipt_sha256: str

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("continuous snapshot receipt schema changed")
        if self.role is not ContinuousSessionRole.PLANNER:
            raise ContractValidationError("accepted snapshot receipt must be Planner-owned")
        if not all(
            isinstance(value, str) and value.strip()
            for value in (self.world_id, self.branch_id, self.accepted_turn_id)
        ):
            raise ContractValidationError("continuous snapshot receipt scope is incomplete")
        for value in (
            self.accepted_envelope_sha256,
            self.provider_thread_sha256,
            self.snapshot_sha256,
            self.injection_operation_receipt_sha256,
            self.immutable_file_sha256,
            self.receipt_sha256,
        ):
            if not re_is_sha256(value):
                raise ContractValidationError("continuous snapshot receipt hash is invalid")
        if not self.immutable_relative_path.startswith("PLANNER_SESSION/ACCEPTED/"):
            raise ContractValidationError("continuous immutable snapshot path changed")
        if self.current_relative_path != "PLANNER_SESSION/SESSION_SNAPSHOT.json":
            raise ContractValidationError("continuous current snapshot pointer changed")
        expected = canonical_sha256(
            {
                "schema_version": self.SCHEMA_VERSION,
                "world_id": self.world_id,
                "branch_id": self.branch_id,
                "role": self.role.value,
                "accepted_turn_id": self.accepted_turn_id,
                "accepted_envelope_sha256": self.accepted_envelope_sha256,
                "provider_thread_sha256": self.provider_thread_sha256,
                "snapshot_sha256": self.snapshot_sha256,
                "injection_operation_receipt_sha256": self.injection_operation_receipt_sha256,
                "immutable_relative_path": self.immutable_relative_path,
                "immutable_file_sha256": self.immutable_file_sha256,
                "current_relative_path": self.current_relative_path,
            }
        )
        if self.receipt_sha256 != expected:
            raise ContractValidationError("continuous snapshot receipt binding changed")


class ContinuousSessionSnapshotStore:
    """Durably checkpoint a role thread without granting it story authority."""

    def __init__(
        self,
        branch_root: Path,
        *,
        failpoint: Callable[[str], None] | None = None,
    ) -> None:
        self.branch_root = branch_root.resolve()
        self._failpoint = failpoint

    def path_for(self, role: ContinuousSessionRole) -> Path:
        directory = (
            "PLANNER_SESSION"
            if role is ContinuousSessionRole.PLANNER
            else "VALIDATOR_SESSION"
        )
        return self.branch_root / directory / "SESSION_SNAPSHOT.json"

    def save(self, snapshot: ContinuousSessionSnapshotV1) -> Path:
        path = self.path_for(snapshot.compatibility.role)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": "cera.continuous_session_snapshot_envelope.v1",
            "snapshot": to_primitive(snapshot),
            "snapshot_sha256": snapshot.snapshot_sha256,
        }
        temporary = path.with_suffix(".tmp")
        with temporary.open("wb") as stream:
            stream.write(canonical_bytes(payload) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        if self._failpoint is not None:
            self._failpoint("before_snapshot_replace")
        os.replace(temporary, path)
        if self._failpoint is not None:
            self._failpoint("after_snapshot_replace")
        return path

    def save_for_acceptance(
        self,
        snapshot: ContinuousSessionSnapshotV1,
        *,
        accepted_turn_id: str,
        accepted_envelope_sha256: str,
        injection_receipt: ContinuousContextInjectionReceiptV1,
    ) -> ContinuousSessionSnapshotReceiptV1:
        if snapshot.compatibility.role is not ContinuousSessionRole.PLANNER:
            raise StateConflictError("accepted checkpoint requires a Planner snapshot")
        if (
            injection_receipt.accepted_turn_id != accepted_turn_id
            or injection_receipt.accepted_envelope_sha256 != accepted_envelope_sha256
            or injection_receipt.provider_thread_sha256
            != snapshot.handle.provider_thread_id_sha256
        ):
            raise StateConflictError("accepted snapshot injection identity changed")
        matching = {
            event.event_type: event.payload_sha256
            for event in snapshot.context_events
            if event.turn_or_scene_id == accepted_turn_id
            and event.event_type
            in {"accepted_final_sequence", "accepted_final_sequence_synchronized"}
        }
        if matching != {
            "accepted_final_sequence": accepted_envelope_sha256,
            "accepted_final_sequence_synchronized": accepted_envelope_sha256,
        }:
            raise StateConflictError("accepted snapshot lacks exact synchronized events")
        current_path = self.save(snapshot)
        envelope = {
            "schema_version": "cera.continuous_session_snapshot_envelope.v1",
            "snapshot": to_primitive(snapshot),
            "snapshot_sha256": snapshot.snapshot_sha256,
        }
        immutable_relative = (
            "PLANNER_SESSION/ACCEPTED/"
            + text_sha256(accepted_turn_id)[:24]
            + "/"
            + snapshot.snapshot_sha256
            + ".snapshot.json"
        )
        immutable_path = self.branch_root / immutable_relative
        immutable_path.parent.mkdir(parents=True, exist_ok=True)
        encoded = canonical_bytes(envelope) + b"\n"
        if immutable_path.exists():
            if immutable_path.read_bytes() != encoded:
                raise StateConflictError("immutable accepted snapshot changed")
        else:
            temporary = immutable_path.with_suffix(".tmp")
            with temporary.open("wb") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, immutable_path)
        immutable_file_sha256 = text_sha256(encoded.decode("utf-8"))
        payload = {
            "schema_version": ContinuousSessionSnapshotReceiptV1.SCHEMA_VERSION,
            "world_id": snapshot.compatibility.world_id,
            "branch_id": snapshot.compatibility.branch_id,
            "role": snapshot.compatibility.role,
            "accepted_turn_id": accepted_turn_id,
            "accepted_envelope_sha256": accepted_envelope_sha256,
            "provider_thread_sha256": snapshot.handle.provider_thread_id_sha256,
            "snapshot_sha256": snapshot.snapshot_sha256,
            "injection_operation_receipt_sha256": injection_receipt.operation_receipt_sha256,
            "immutable_relative_path": immutable_relative,
            "immutable_file_sha256": immutable_file_sha256,
            "current_relative_path": current_path.relative_to(self.branch_root).as_posix(),
        }
        primitive = {
            **payload,
            "role": snapshot.compatibility.role.value,
        }
        return ContinuousSessionSnapshotReceiptV1(
            **payload,
            receipt_sha256=canonical_sha256(primitive),
        )

    def load_immutable(
        self, receipt: ContinuousSessionSnapshotReceiptV1
    ) -> ContinuousSessionSnapshotV1:
        path = (self.branch_root / receipt.immutable_relative_path).resolve()
        if (
            self.branch_root not in path.parents
            or not path.is_file()
            or path.is_symlink()
            or text_sha256(path.read_text(encoding="utf-8"))
            != receipt.immutable_file_sha256
        ):
            raise StateConflictError("immutable accepted snapshot bytes changed")
        envelope = json.loads(path.read_text(encoding="utf-8"))
        snapshot = from_mapping(ContinuousSessionSnapshotV1, envelope.get("snapshot"))
        if (
            envelope.get("snapshot_sha256") != snapshot.snapshot_sha256
            or snapshot.snapshot_sha256 != receipt.snapshot_sha256
            or snapshot.handle.provider_thread_id_sha256
            != receipt.provider_thread_sha256
            or snapshot.compatibility.world_id != receipt.world_id
            or snapshot.compatibility.branch_id != receipt.branch_id
        ):
            raise StateConflictError("immutable accepted snapshot identity changed")
        return snapshot

    def load(self, role: ContinuousSessionRole) -> ContinuousSessionSnapshotV1:
        path = self.path_for(role)
        if not path.is_file():
            raise StateConflictError("continuous session snapshot is unavailable")
        envelope = json.loads(path.read_text(encoding="utf-8"))
        if envelope.get("schema_version") != "cera.continuous_session_snapshot_envelope.v1":
            raise ContractValidationError("continuous snapshot envelope changed")
        snapshot = from_mapping(ContinuousSessionSnapshotV1, envelope.get("snapshot"))
        if envelope.get("snapshot_sha256") != snapshot.snapshot_sha256:
            raise StateConflictError("continuous session snapshot hash changed")
        return snapshot


@runtime_checkable
class ContinuousStoredSessionPort(Protocol):
    """Stored provider context only; no story or world authority."""

    def create(self, compatibility: ContinuousSessionCompatibilityV1) -> ContinuousSessionHandleV1: ...

    def resume(self, handle: ContinuousSessionHandleV1) -> bool: ...

    def fork_branch(
        self,
        parent: ContinuousSessionHandleV1,
        compatibility: ContinuousSessionCompatibilityV1,
    ) -> ContinuousSessionHandleV1: ...

    def append_context(self, handle: ContinuousSessionHandleV1, text: str) -> None: ...

    def archive(self, handle: ContinuousSessionHandleV1, reason: str) -> None: ...

    def selectable_as_active_or_accepted_ancestry(
        self, handle: ContinuousSessionHandleV1
    ) -> bool: ...


class InMemoryContinuousStoredSessionPort:
    """Provider-free stored-thread seam with physical-thread continuity."""

    def __init__(self) -> None:
        self._counter = 0
        self._valid: set[str] = set()
        self._parents: dict[str, str | None] = {}
        self.provider_calls = 0
        self.operations: list[tuple[str, str]] = []
        self.model_visible_context: dict[str, list[str]] = {}

    def create(self, compatibility: ContinuousSessionCompatibilityV1) -> ContinuousSessionHandleV1:
        self._counter += 1
        thread_id = f"fake-continuous-{compatibility.role.value}-{self._counter}"
        self._valid.add(thread_id)
        self._parents[thread_id] = None
        self.model_visible_context[thread_id] = []
        self.operations.append(("create", text_sha256(thread_id)))
        return ContinuousSessionHandleV1(
            schema_version=ContinuousSessionHandleV1.SCHEMA_VERSION,
            provider_session_id=f"fake-epoch-{self._counter}",
            provider_thread_id=thread_id,
            provider_thread_id_sha256=text_sha256(thread_id),
        )

    def resume(self, handle: ContinuousSessionHandleV1) -> bool:
        self.operations.append(("resume", handle.provider_thread_id_sha256))
        return handle.provider_thread_id in self._valid

    def fork_branch(
        self,
        parent: ContinuousSessionHandleV1,
        compatibility: ContinuousSessionCompatibilityV1,
    ) -> ContinuousSessionHandleV1:
        if not self.resume(parent):
            raise StateConflictError("continuous parent session is unavailable")
        child = self.create(compatibility)
        self._parents[child.provider_thread_id] = parent.provider_thread_id
        self.operations.append(("fork_branch", child.provider_thread_id_sha256))
        return child

    def archive(self, handle: ContinuousSessionHandleV1, reason: str) -> None:
        self._valid.discard(handle.provider_thread_id)
        self.operations.append(("archive", handle.provider_thread_id_sha256))

    def selectable_as_active_or_accepted_ancestry(
        self, handle: ContinuousSessionHandleV1
    ) -> bool:
        selectable = handle.provider_thread_id in self._valid
        self.operations.append(
            (
                "selectable" if selectable else "not_selectable",
                handle.provider_thread_id_sha256,
            )
        )
        return selectable

    def append_context(self, handle: ContinuousSessionHandleV1, text: str) -> None:
        if handle.provider_thread_id not in self._valid:
            raise StateConflictError("continuous session is unavailable")
        if not isinstance(text, str) or not text.strip():
            raise ContractValidationError("continuous appended context is empty")
        self.model_visible_context[handle.provider_thread_id].append(text)
        self.operations.append(("append_context", text_sha256(text)))


@dataclass(slots=True)
class ContinuousSessionCoordinator:
    """Maintain one physical role thread and typed non-authoritative context."""

    compatibility: ContinuousSessionCompatibilityV1
    port: ContinuousStoredSessionPort
    handle: ContinuousSessionHandleV1 | None = None
    _events: list[ContinuousContextEventV1] = field(default_factory=list)
    _accepted_envelopes: dict[str, str] = field(default_factory=dict)
    _terminally_archived: bool = False

    def ensure_session(self) -> ContinuousSessionHandleV1:
        if self._terminally_archived:
            raise StateConflictError("continuous provider session is terminally archived")
        if self.handle is None:
            self.handle = self.port.create(self.compatibility)
        elif not self.port.resume(self.handle):
            raise StateConflictError("continuous provider session cannot be resumed")
        return self.handle

    def archive_and_verify_terminal(
        self, reason: str
    ) -> ContinuousThreadArchiveEvidenceV1:
        """Archive once and prove both provider and local ancestry isolation.

        The coordinator is invalidated before the request.  Even a failed
        provider archive can therefore never make this object select the old
        handle as accepted ancestry again.  Provider request, resume, and
        active-selection failures are represented explicitly rather than
        being mistaken for successful archival.
        """

        if self._terminally_archived:
            raise StateConflictError("continuous provider session was already archived")
        if self.handle is None:
            raise StateConflictError("continuous provider session was never created")
        if not isinstance(reason, str) or not reason.strip():
            raise ContractValidationError("continuous archive reason is required")
        handle = self.handle
        self._terminally_archived = True

        archive_completed = False
        resume_succeeded: bool | None = None
        backend_selectable: bool | None = None
        archive_error: str | None = None
        resume_error: str | None = None
        selection_error: str | None = None
        try:
            self.port.archive(handle, reason)
            archive_completed = True
        except BaseException as exc:
            archive_error = type(exc).__name__
        if archive_completed:
            try:
                resume_succeeded = self.port.resume(handle)
            except BaseException as exc:
                resume_error = type(exc).__name__
            try:
                backend_selectable = (
                    self.port.selectable_as_active_or_accepted_ancestry(handle)
                )
            except BaseException as exc:
                selection_error = type(exc).__name__

        return ContinuousThreadArchiveEvidenceV1(
            role=self.compatibility.role,
            provider_thread_id_sha256=handle.provider_thread_id_sha256,
            archive_reason_sha256=text_sha256(reason),
            archive_request_completed=archive_completed,
            resume_succeeded_after_archive=resume_succeeded,
            backend_selectable_after_archive=backend_selectable,
            coordinator_selectable_as_accepted_ancestry=False,
            archive_error_type=archive_error,
            resume_error_type=resume_error,
            selection_error_type=selection_error,
        )

    def record_planner_provisional(self, turn_id: str, sequence_sha256: str) -> None:
        if self.compatibility.role is not ContinuousSessionRole.PLANNER:
            raise StateConflictError("Validator session cannot record a Planner sequence")
        self.ensure_session()
        self._events.append(
            ContinuousContextEventV1(
                event_type="planner_provisional_sequence",
                turn_or_scene_id=turn_id,
                payload_sha256=sequence_sha256,
            )
        )

    def append_accepted_final_sequence(
        self, envelope: AcceptedFinalSequenceEnvelopeV1
    ) -> bool:
        if self.compatibility.role is not ContinuousSessionRole.PLANNER:
            raise StateConflictError("accepted final sequence belongs only in Planner context")
        self.ensure_session()
        prior = self._accepted_envelopes.get(envelope.accepted_turn_id)
        if prior is not None:
            if prior != envelope.envelope_sha256:
                raise StateConflictError("accepted final sequence changed after synchronization")
            return False
        provisional = next(
            (
                value.payload_sha256
                for value in reversed(self._events)
                if value.event_type == "planner_provisional_sequence"
                and value.turn_or_scene_id == envelope.accepted_turn_id
            ),
            None,
        )
        self._events.append(
            ContinuousContextEventV1(
                event_type="accepted_final_sequence",
                turn_or_scene_id=envelope.accepted_turn_id,
                payload_sha256=envelope.envelope_sha256,
                supersedes_payload_sha256=provisional,
            )
        )
        self._accepted_envelopes[envelope.accepted_turn_id] = envelope.envelope_sha256
        return True

    def record_validator_candidate(
        self,
        turn_id: str,
        package_sha256: str,
        *,
        rejected: bool = False,
    ) -> None:
        if self.compatibility.role is not ContinuousSessionRole.VALIDATOR:
            raise StateConflictError("Planner cannot inspect Validator candidates")
        self.ensure_session()
        self._events.append(
            ContinuousContextEventV1(
                event_type=(
                    "validator_rejected_candidate" if rejected else "validator_candidate"
                ),
                turn_or_scene_id=turn_id,
                payload_sha256=package_sha256,
            )
        )

    def mark_accepted_final_sequences_synchronized(
        self, envelopes: tuple[AcceptedFinalSequenceEnvelopeV1, ...]
    ) -> None:
        if self.compatibility.role is not ContinuousSessionRole.PLANNER:
            raise StateConflictError("accepted-final synchronization belongs to Planner")
        synchronized = {
            value.turn_or_scene_id
            for value in self._events
            if value.event_type == "accepted_final_sequence_synchronized"
        }
        for envelope in envelopes:
            expected = self._accepted_envelopes.get(envelope.accepted_turn_id)
            if expected != envelope.envelope_sha256:
                raise StateConflictError("unknown accepted-final envelope cannot synchronize")
            if envelope.accepted_turn_id in synchronized:
                continue
            self._events.append(
                ContinuousContextEventV1(
                    event_type="accepted_final_sequence_synchronized",
                    turn_or_scene_id=envelope.accepted_turn_id,
                    payload_sha256=envelope.envelope_sha256,
                )
            )
            synchronized.add(envelope.accepted_turn_id)

    def synchronize_accepted_final_sequence(
        self, envelope: AcceptedFinalSequenceEnvelopeV1
    ) -> bool:
        """Append one accepted envelope to the physical Planner thread.

        The accepted ledger entry must already exist.  Repeated calls after a
        completed synchronization are no-ops; a transport failure remains
        terminal and is never retried automatically.
        """

        return self.synchronize_accepted_final_sequence_with_receipt(envelope) is not None

    def synchronize_accepted_final_sequence_with_receipt(
        self, envelope: AcceptedFinalSequenceEnvelopeV1
    ) -> ContinuousContextInjectionReceiptV1 | None:
        if self.compatibility.role is not ContinuousSessionRole.PLANNER:
            raise StateConflictError("accepted-final synchronization belongs to Planner")
        if self._accepted_envelopes.get(envelope.accepted_turn_id) != envelope.envelope_sha256:
            raise StateConflictError("unknown accepted-final envelope cannot synchronize")
        if envelope.accepted_turn_id not in self.unsynchronized_accepted_turn_ids:
            return None
        handle = self.ensure_session()
        rendered = envelope.render_for_planner()
        self.port.append_context(handle, rendered)
        self.mark_accepted_final_sequences_synchronized((envelope,))
        receipt_payload = {
            "schema_version": ContinuousContextInjectionReceiptV1.SCHEMA_VERSION,
            "accepted_turn_id": envelope.accepted_turn_id,
            "accepted_envelope_sha256": envelope.envelope_sha256,
            "provider_thread_sha256": handle.provider_thread_id_sha256,
            "injected_context_sha256": text_sha256(rendered),
        }
        return ContinuousContextInjectionReceiptV1(
            **receipt_payload,
            operation_receipt_sha256=canonical_sha256(receipt_payload),
        )

    @property
    def unsynchronized_accepted_turn_ids(self) -> tuple[str, ...]:
        synchronized = {
            value.turn_or_scene_id
            for value in self._events
            if value.event_type == "accepted_final_sequence_synchronized"
        }
        return tuple(
            value for value in self._accepted_envelopes if value not in synchronized
        )

    def record_scene_change(self, scene_id: str, payload_sha256: str) -> None:
        self.ensure_session()
        self._events.append(
            ContinuousContextEventV1(
                event_type="scene_change",
                turn_or_scene_id=scene_id,
                payload_sha256=payload_sha256,
            )
        )

    def record_scene_summary(self, scene_id: str, payload_sha256: str) -> None:
        if self.compatibility.role is not ContinuousSessionRole.VALIDATOR:
            raise StateConflictError("scene-summary authority belongs to Validator context")
        self.ensure_session()
        self._events.append(
            ContinuousContextEventV1(
                event_type="scene_summary",
                turn_or_scene_id=scene_id,
                payload_sha256=payload_sha256,
            )
        )

    def snapshot(self) -> ContinuousSessionSnapshotV1:
        handle = self.ensure_session()
        return ContinuousSessionSnapshotV1(
            schema_version=ContinuousSessionSnapshotV1.SCHEMA_VERSION,
            compatibility=self.compatibility,
            handle=handle,
            context_events=tuple(self._events),
            accepted_turn_ids=tuple(self._accepted_envelopes),
        )

    def checkpoint(self, store: ContinuousSessionSnapshotStore) -> Path:
        return store.save(self.snapshot())

    @classmethod
    def reconstruct(
        cls,
        snapshot: ContinuousSessionSnapshotV1,
        port: ContinuousStoredSessionPort,
        *,
        expected_compatibility: ContinuousSessionCompatibilityV1,
    ) -> "ContinuousSessionCoordinator":
        if (
            snapshot.compatibility.compatibility_sha256
            != expected_compatibility.compatibility_sha256
        ):
            raise StateConflictError(
                "continuous session snapshot is incompatible with the active contract"
            )
        if not port.resume(snapshot.handle):
            raise StateConflictError("continuous session reconstruction lost provider thread")
        coordinator = cls(
            compatibility=expected_compatibility,
            port=port,
            handle=snapshot.handle,
        )
        coordinator._events.extend(snapshot.context_events)
        coordinator._accepted_envelopes.update(
            {
                event.turn_or_scene_id: event.payload_sha256
                for event in snapshot.context_events
                if event.event_type == "accepted_final_sequence"
            }
        )
        return coordinator

    def fork_for_branch(
        self, compatibility: ContinuousSessionCompatibilityV1
    ) -> "ContinuousSessionCoordinator":
        if compatibility.role is not self.compatibility.role:
            raise StateConflictError("continuous branch fork changed session role")
        if compatibility.world_id != self.compatibility.world_id:
            raise StateConflictError("continuous branch fork changed world")
        parent = self.ensure_session()
        child = self.port.fork_branch(parent, compatibility)
        return ContinuousSessionCoordinator(
            compatibility=compatibility,
            port=self.port,
            handle=child,
        )


def assert_separate_role_sessions(
    planner: ContinuousSessionCoordinator,
    validator: ContinuousSessionCoordinator,
) -> None:
    if planner.compatibility.role is not ContinuousSessionRole.PLANNER:
        raise StateConflictError("Planner session has the wrong role")
    if validator.compatibility.role is not ContinuousSessionRole.VALIDATOR:
        raise StateConflictError("Validator session has the wrong role")
    if (
        planner.handle is not None
        and validator.handle is not None
        and planner.handle.provider_thread_id == validator.handle.provider_thread_id
    ):
        raise StateConflictError("Planner and Validator cannot share one provider thread")


class WorldPathAccessPolicyV1:
    """Role-specific deny-by-default path authorization."""

    def __init__(self, branch_root: str) -> None:
        normalized = branch_root.replace("\\", "/").rstrip("/")
        if not normalized:
            raise ContractValidationError("branch root is required")
        self.branch_root = normalized

    def authorize(
        self,
        role: ContinuousSessionRole,
        candidate_path: str,
        *,
        current_turn_id: str | None = None,
    ) -> str:
        normalized = candidate_path.replace("\\", "/")
        prefix = self.branch_root + "/"
        if not normalized.startswith(prefix) or "/../" in f"/{normalized}/":
            raise PermissionError("continuous session path escaped its branch root")
        relative = normalized[len(prefix) :]
        first = relative.split("/", 1)[0]
        if role is ContinuousSessionRole.PLANNER:
            if first not in {"ACTIVE", "DERIVED", "PLANNER_SESSION"}:
                raise PermissionError("Planner cannot inspect Validator, candidate, debug, or unrelated paths")
        else:
            allowed = {"ACTIVE", "DERIVED", "VALIDATOR_SESSION"}
            if current_turn_id is not None:
                allowed.add("CANDIDATES")
                if first == "CANDIDATES" and not relative.startswith(
                    f"CANDIDATES/{current_turn_id}/"
                ):
                    raise PermissionError("Validator cannot inspect another candidate turn")
            if first not in allowed:
                raise PermissionError("Validator path is outside its authorized world view")
        return normalized
