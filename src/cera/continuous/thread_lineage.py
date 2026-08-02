"""Closed physical-thread lineage and terminal-disposition custody."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar, Mapping

from cera.errors import ContractValidationError, StateConflictError
from cera.serialization import canonical_sha256, re_is_sha256, to_primitive


_ROLES = frozenset({"planner", "validator"})
_PURPOSES = frozenset(
    {
        "primary_planner",
        "primary_validator",
        "accepted_checkpoint_fork_child",
        "planner_reconstruction",
    }
)
_CREATION_OPERATIONS = frozenset({"create", "resume", "fork", "reconstruct"})
_EVENT_TYPES = frozenset(
    {
        "created",
        "resumed",
        "forked",
        "reconstructed",
        "adopted",
        "superseded",
        "abandoned",
        "archive_recorded",
        "authorized_active",
    }
)


@dataclass(frozen=True, slots=True)
class ContinuousThreadLifecycleEventV1:
    event_type: str
    reason_sha256: str | None = None
    related_thread_sha256: str | None = None

    def __post_init__(self) -> None:
        if self.event_type not in _EVENT_TYPES:
            raise ContractValidationError("continuous thread lifecycle event is invalid")
        for value in (self.reason_sha256, self.related_thread_sha256):
            if value is not None and not re_is_sha256(value):
                raise ContractValidationError(
                    "continuous thread lifecycle event hash is invalid"
                )

    @property
    def event_sha256(self) -> str:
        return canonical_sha256(to_primitive(self))


@dataclass(frozen=True, slots=True)
class ContinuousThreadArchiveLineageEvidenceV1:
    """Deeply immutable copy of one physical thread's archive evidence."""

    SCHEMA_VERSION: ClassVar[str] = "cera.continuous_thread_archive_evidence.v1"

    schema_version: str
    role: str
    provider_thread_id_sha256: str
    archive_reason_sha256: str
    archive_request_completed: bool
    resume_succeeded_after_archive: bool | None
    backend_selectable_after_archive: bool | None
    coordinator_selectable_as_accepted_ancestry: bool
    archive_error_type: str | None
    resume_error_type: str | None
    selection_error_type: str | None
    verified: bool

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION or self.role not in _ROLES:
            raise ContractValidationError(
                "continuous lineage archive schema or role changed"
            )
        if any(
            not re_is_sha256(value)
            for value in (
                self.provider_thread_id_sha256,
                self.archive_reason_sha256,
            )
        ):
            raise ContractValidationError(
                "continuous lineage archive hash is invalid"
            )
        if any(
            type(value) is not bool
            for value in (
                self.archive_request_completed,
                self.coordinator_selectable_as_accepted_ancestry,
                self.verified,
            )
        ):
            raise ContractValidationError(
                "continuous lineage archive boolean is invalid"
            )
        for value in (
            self.resume_succeeded_after_archive,
            self.backend_selectable_after_archive,
        ):
            if value is not None and type(value) is not bool:
                raise ContractValidationError(
                    "continuous lineage archive probe is invalid"
                )
        for value in (
            self.archive_error_type,
            self.resume_error_type,
            self.selection_error_type,
        ):
            if value is not None and (
                not isinstance(value, str) or not value.strip() or len(value) > 128
            ):
                raise ContractValidationError(
                    "continuous lineage archive error identity is invalid"
                )
        derived_verified = (
            self.archive_request_completed
            and self.resume_succeeded_after_archive is False
            and self.backend_selectable_after_archive is False
            and self.coordinator_selectable_as_accepted_ancestry is False
            and self.archive_error_type is None
            and self.resume_error_type is None
            and self.selection_error_type is None
        )
        if self.verified is not derived_verified:
            raise ContractValidationError(
                "continuous lineage archive status is not derived"
            )

    @classmethod
    def from_dict(
        cls, raw: object
    ) -> "ContinuousThreadArchiveLineageEvidenceV1":
        if not isinstance(raw, Mapping):
            raise ContractValidationError(
                "continuous lineage archive evidence is not an object"
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
                "continuous lineage archive evidence fields changed"
            )
        return cls(**{field: raw[field] for field in expected})

    def to_dict(self) -> dict[str, Any]:
        return to_primitive(self)


@dataclass(frozen=True, slots=True)
class ContinuousThreadLineageEntryV1:
    role: str
    purpose: str
    world_id: str
    branch_id: str
    session_compatibility_sha256: str
    provider_thread_sha256: str
    parent_provider_thread_sha256: str | None
    creation_operation: str
    lifecycle_events: tuple[ContinuousThreadLifecycleEventV1, ...]
    terminal_disposition: str
    archive_evidence: ContinuousThreadArchiveLineageEvidenceV1 | None

    def __post_init__(self) -> None:
        if self.role not in _ROLES or self.purpose not in _PURPOSES:
            raise ContractValidationError("continuous thread lineage role or purpose is invalid")
        if (self.role == "validator") is not (self.purpose == "primary_validator"):
            raise ContractValidationError(
                "continuous thread lineage role and purpose disagree"
            )
        if not self.world_id or not self.branch_id:
            raise ContractValidationError("continuous thread lineage scope is incomplete")
        for value in (
            self.session_compatibility_sha256,
            self.provider_thread_sha256,
        ):
            if not re_is_sha256(value):
                raise ContractValidationError("continuous thread lineage hash is invalid")
        if self.parent_provider_thread_sha256 is not None and not re_is_sha256(
            self.parent_provider_thread_sha256
        ):
            raise ContractValidationError("continuous thread lineage parent is invalid")
        if self.creation_operation not in _CREATION_OPERATIONS:
            raise ContractValidationError(
                "continuous thread lineage creation operation is invalid"
            )
        expected_operations = {
            "primary_planner": {"create", "resume"},
            "primary_validator": {"create", "resume"},
            "accepted_checkpoint_fork_child": {"fork"},
            "planner_reconstruction": {"reconstruct"},
        }[self.purpose]
        if self.creation_operation not in expected_operations:
            raise ContractValidationError(
                "continuous thread lineage purpose and creation disagree"
            )
        if (
            self.creation_operation in {"fork", "reconstruct"}
        ) is not (self.parent_provider_thread_sha256 is not None):
            raise ContractValidationError(
                "continuous thread lineage parent relationship changed"
            )
        if not self.lifecycle_events:
            raise ContractValidationError("continuous thread lineage has no lifecycle")
        expected_first = {
            "create": "created",
            "resume": "resumed",
            "fork": "forked",
            "reconstruct": "reconstructed",
        }[self.creation_operation]
        if self.lifecycle_events[0].event_type != expected_first:
            raise ContractValidationError("continuous thread creation event changed")
        physical_creation_events = {"created", "forked", "reconstructed"}
        if any(
            event.event_type in physical_creation_events
            for event in self.lifecycle_events[1:]
        ):
            raise ContractValidationError(
                "continuous thread physical creation event is duplicated"
            )
        if self.lifecycle_events[0].related_thread_sha256 != (
            self.parent_provider_thread_sha256
            if expected_first in {"forked", "reconstructed"}
            else None
        ):
            raise ContractValidationError(
                "continuous thread creation parent event changed"
            )
        archive_events = sum(
            event.event_type == "archive_recorded"
            for event in self.lifecycle_events
        )
        active_events = sum(
            event.event_type == "authorized_active"
            for event in self.lifecycle_events
        )
        if archive_events > 1 or active_events > 1:
            raise ContractValidationError(
                "continuous thread terminal event is duplicated"
            )
        if self.terminal_disposition not in {
            "authorized_active",
            "verified_archived",
            "invalid_unresolved",
            "invalid_archive_evidence",
            "invalid_contradictory",
        }:
            raise ContractValidationError(
                "continuous thread terminal disposition is invalid"
            )
        if self.terminal_disposition == "verified_archived":
            if not self._archive_verified() or archive_events != 1 or active_events:
                raise ContractValidationError(
                    "verified thread disposition lacks exact archive evidence"
                )
        elif self.terminal_disposition == "authorized_active":
            if (
                self.archive_evidence is not None
                or archive_events
                or active_events != 1
                or any(
                    event.event_type in {"abandoned", "superseded"}
                    for event in self.lifecycle_events
                )
            ):
                raise ContractValidationError(
                    "active thread also carries archive evidence"
                )

    def _archive_verified(self) -> bool:
        raw = self.archive_evidence
        return bool(
            raw is not None
            and raw.provider_thread_id_sha256 == self.provider_thread_sha256
            and raw.role == self.role
            and raw.verified is True
        )

    @property
    def entry_sha256(self) -> str:
        return canonical_sha256(to_primitive(self))


@dataclass(frozen=True, slots=True)
class ContinuousThreadLineageReceiptV1:
    SCHEMA_VERSION: ClassVar[str] = "cera.continuous_thread_lineage_receipt.v1"

    schema_version: str
    entries: tuple[ContinuousThreadLineageEntryV1, ...]
    authorized_active_threads: tuple[tuple[str, str], ...]
    status: str
    failure_codes: tuple[str, ...]
    receipt_sha256: str

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("continuous thread lineage schema changed")
        thread_ids = tuple(value.provider_thread_sha256 for value in self.entries)
        if (
            thread_ids != tuple(sorted(thread_ids))
            or len(thread_ids) != len(set(thread_ids))
        ):
            raise ContractValidationError(
                "continuous thread lineage entries are missing, duplicated, or unordered"
            )
        active_roles = tuple(value[0] for value in self.authorized_active_threads)
        active_ids = tuple(value[1] for value in self.authorized_active_threads)
        if (
            self.authorized_active_threads
            != tuple(sorted(self.authorized_active_threads))
            or len(active_roles) != len(set(active_roles))
            or any(role not in _ROLES for role in active_roles)
            or any(not re_is_sha256(value) for value in active_ids)
        ):
            raise ContractValidationError(
                "continuous thread authorized-active map is invalid"
            )
        derived = self._derived_failures()
        if self.failure_codes != derived:
            raise ContractValidationError(
                "continuous thread lineage failures are not derived"
            )
        expected_status = "verified" if not derived else "failed"
        if self.status != expected_status:
            raise ContractValidationError("continuous thread lineage status changed")
        payload = to_primitive(self)
        payload.pop("receipt_sha256")
        if self.receipt_sha256 != canonical_sha256(payload):
            raise ContractValidationError("continuous thread lineage receipt changed")

    def _derived_failures(self) -> tuple[str, ...]:
        return self.derive_failures(
            entries=self.entries,
            authorized_active_threads=self.authorized_active_threads,
        )

    @staticmethod
    def derive_failures(
        *,
        entries: tuple[ContinuousThreadLineageEntryV1, ...],
        authorized_active_threads: tuple[tuple[str, str], ...],
    ) -> tuple[str, ...]:
        failures: list[str] = []
        by_id = {value.provider_thread_sha256: value for value in entries}
        active = dict(authorized_active_threads)
        for role, thread_sha256 in authorized_active_threads:
            entry = by_id.get(thread_sha256)
            if entry is None:
                failures.append("unknown_authorized_active_thread")
            elif entry.role != role:
                failures.append("active_thread_role_mismatch")
        for entry in entries:
            if (
                entry.parent_provider_thread_sha256 is not None
                and entry.parent_provider_thread_sha256 not in by_id
            ):
                failures.append("orphaned_thread_parent")
            elif entry.parent_provider_thread_sha256 is not None:
                parent = by_id[entry.parent_provider_thread_sha256]
                if parent is entry:
                    failures.append("self_parented_thread")
                if parent.role != entry.role:
                    failures.append("parent_thread_role_mismatch")
                if parent.world_id != entry.world_id:
                    failures.append("parent_thread_world_mismatch")
                if (
                    entry.creation_operation == "fork"
                    and parent.branch_id == entry.branch_id
                ):
                    failures.append("fork_parent_branch_not_changed")
            expected_active = active.get(entry.role) == entry.provider_thread_sha256
            if expected_active and entry.terminal_disposition != "authorized_active":
                failures.append("active_thread_disposition_mismatch")
            if not expected_active and entry.terminal_disposition == "authorized_active":
                failures.append("unknown_active_thread")
            if entry.terminal_disposition == "invalid_unresolved":
                failures.append("unresolved_thread")
            elif entry.terminal_disposition == "invalid_archive_evidence":
                failures.append("unverified_archive")
            elif entry.terminal_disposition == "invalid_contradictory":
                failures.append("contradictory_thread_disposition")
        return tuple(dict.fromkeys(failures))

    @classmethod
    def from_dict(cls, raw: object) -> "ContinuousThreadLineageReceiptV1":
        if not isinstance(raw, Mapping):
            raise ContractValidationError("continuous thread lineage receipt is not an object")
        expected = {
            "schema_version",
            "entries",
            "authorized_active_threads",
            "status",
            "failure_codes",
            "receipt_sha256",
        }
        if set(raw) != expected:
            raise ContractValidationError("continuous thread lineage receipt fields changed")
        entries_raw = raw["entries"]
        if not isinstance(entries_raw, list):
            raise ContractValidationError("continuous thread lineage entries are invalid")
        entries = []
        for value in entries_raw:
            if not isinstance(value, Mapping):
                raise ContractValidationError("continuous thread lineage entry is invalid")
            expected_entry = {
                "role",
                "purpose",
                "world_id",
                "branch_id",
                "session_compatibility_sha256",
                "provider_thread_sha256",
                "parent_provider_thread_sha256",
                "creation_operation",
                "lifecycle_events",
                "terminal_disposition",
                "archive_evidence",
            }
            if set(value) != expected_entry:
                raise ContractValidationError(
                    "continuous thread lineage entry fields changed"
                )
            events_raw = value.get("lifecycle_events")
            if not isinstance(events_raw, list):
                raise ContractValidationError("continuous thread lifecycle is invalid")
            expected_event = {
                "event_type",
                "reason_sha256",
                "related_thread_sha256",
            }
            if any(
                not isinstance(event, Mapping) or set(event) != expected_event
                for event in events_raw
            ):
                raise ContractValidationError(
                    "continuous thread lifecycle event fields changed"
                )
            archive_raw = value.get("archive_evidence")
            entries.append(
                ContinuousThreadLineageEntryV1(
                    role=value.get("role"),
                    purpose=value.get("purpose"),
                    world_id=value.get("world_id"),
                    branch_id=value.get("branch_id"),
                    session_compatibility_sha256=value.get(
                        "session_compatibility_sha256"
                    ),
                    provider_thread_sha256=value.get("provider_thread_sha256"),
                    parent_provider_thread_sha256=value.get(
                        "parent_provider_thread_sha256"
                    ),
                    creation_operation=value.get("creation_operation"),
                    lifecycle_events=tuple(
                        ContinuousThreadLifecycleEventV1(
                            event_type=event.get("event_type"),
                            reason_sha256=event.get("reason_sha256"),
                            related_thread_sha256=event.get(
                                "related_thread_sha256"
                            ),
                        )
                        for event in events_raw
                    ),
                    terminal_disposition=value.get("terminal_disposition"),
                    archive_evidence=(
                        None
                        if archive_raw is None
                        else ContinuousThreadArchiveLineageEvidenceV1.from_dict(
                            archive_raw
                        )
                    ),
                )
            )
        active_raw = raw["authorized_active_threads"]
        if not isinstance(active_raw, list):
            raise ContractValidationError("continuous active thread map is invalid")
        if any(
            not isinstance(value, list) or len(value) != 2
            for value in active_raw
        ):
            raise ContractValidationError("continuous active thread entry is invalid")
        failure_codes_raw = raw["failure_codes"]
        if not isinstance(failure_codes_raw, list) or any(
            not isinstance(value, str) for value in failure_codes_raw
        ):
            raise ContractValidationError(
                "continuous thread lineage failure codes are invalid"
            )
        payload = {
            "schema_version": raw["schema_version"],
            "entries": tuple(entries),
            "authorized_active_threads": tuple(
                (value[0], value[1])
                for value in active_raw
            ),
            "status": raw["status"],
            "failure_codes": tuple(failure_codes_raw),
        }
        return cls(**payload, receipt_sha256=raw["receipt_sha256"])

    def to_dict(self) -> dict[str, Any]:
        return to_primitive(self)


class ContinuousThreadLineageLedger:
    """Mutable Python operation ledger that freezes into one immutable receipt."""

    def __init__(self) -> None:
        self._threads: dict[str, dict[str, Any]] = {}
        self._frozen_receipt: ContinuousThreadLineageReceiptV1 | None = None

    def register_thread(
        self,
        *,
        role: str,
        purpose: str,
        world_id: str,
        branch_id: str,
        session_compatibility_sha256: str,
        provider_thread_sha256: str,
        parent_provider_thread_sha256: str | None,
        creation_operation: str,
    ) -> None:
        self._assert_mutable()
        if provider_thread_sha256 in self._threads:
            raise StateConflictError("physical thread is already registered")
        first_event = {
            "create": "created",
            "resume": "resumed",
            "fork": "forked",
            "reconstruct": "reconstructed",
        }.get(creation_operation)
        if first_event is None:
            raise ContractValidationError("thread creation operation is invalid")
        entry = ContinuousThreadLineageEntryV1(
            role=role,
            purpose=purpose,
            world_id=world_id,
            branch_id=branch_id,
            session_compatibility_sha256=session_compatibility_sha256,
            provider_thread_sha256=provider_thread_sha256,
            parent_provider_thread_sha256=parent_provider_thread_sha256,
            creation_operation=creation_operation,
            lifecycle_events=(
                ContinuousThreadLifecycleEventV1(
                    event_type=first_event,
                    related_thread_sha256=parent_provider_thread_sha256,
                ),
            ),
            terminal_disposition="invalid_unresolved",
            archive_evidence=None,
        )
        self._threads[provider_thread_sha256] = {
            "base": entry,
            "events": list(entry.lifecycle_events),
            "archive_evidence": None,
        }

    def record_resume(self, provider_thread_sha256: str) -> None:
        self._event(provider_thread_sha256, "resumed")

    def record_adoption(
        self,
        provider_thread_sha256: str,
        *,
        reason_sha256: str,
        superseded_thread_sha256: str | None = None,
    ) -> None:
        self._assert_mutable()
        if (
            provider_thread_sha256 == superseded_thread_sha256
            or provider_thread_sha256 not in self._threads
            or (
                superseded_thread_sha256 is not None
                and superseded_thread_sha256 not in self._threads
            )
        ):
            raise StateConflictError("thread adoption relationship is invalid")
        adopted: ContinuousThreadLineageEntryV1 = self._threads[
            provider_thread_sha256
        ]["base"]
        if adopted.role != "planner":
            raise StateConflictError("only a Planner thread can be adopted")
        if superseded_thread_sha256 is not None:
            superseded: ContinuousThreadLineageEntryV1 = self._threads[
                superseded_thread_sha256
            ]["base"]
            if superseded.role != adopted.role or superseded.world_id != adopted.world_id:
                raise StateConflictError("thread adoption parent scope changed")
        self._event(
            provider_thread_sha256,
            "adopted",
            reason_sha256=reason_sha256,
            related_thread_sha256=superseded_thread_sha256,
        )
        if superseded_thread_sha256 is not None:
            self._event(
                superseded_thread_sha256,
                "superseded",
                reason_sha256=reason_sha256,
                related_thread_sha256=provider_thread_sha256,
            )

    def record_abandoned(
        self, provider_thread_sha256: str, *, reason_sha256: str
    ) -> None:
        self._event(
            provider_thread_sha256,
            "abandoned",
            reason_sha256=reason_sha256,
        )

    def record_archive(self, evidence: Any) -> None:
        self._assert_mutable()
        raw = evidence.to_dict() if hasattr(evidence, "to_dict") else evidence
        archive = ContinuousThreadArchiveLineageEvidenceV1.from_dict(raw)
        thread_sha256 = archive.provider_thread_id_sha256
        if thread_sha256 not in self._threads:
            raise StateConflictError("archive evidence belongs to an unknown thread")
        state = self._threads[thread_sha256]
        if state["archive_evidence"] is not None:
            raise StateConflictError("thread archive evidence is duplicated")
        state["archive_evidence"] = archive
        state["events"].append(
            ContinuousThreadLifecycleEventV1(
                event_type="archive_recorded",
                reason_sha256=archive.archive_reason_sha256,
            )
        )

    def finalize(
        self, *, authorized_active_threads: Mapping[str, str]
    ) -> ContinuousThreadLineageReceiptV1:
        return self.freeze(authorized_active_threads=authorized_active_threads)

    def archive_evidence_for(
        self, provider_thread_sha256: str
    ) -> ContinuousThreadArchiveLineageEvidenceV1 | None:
        state = self._threads.get(provider_thread_sha256)
        if state is None:
            raise StateConflictError(
                "archive lookup belongs to an unknown physical thread"
            )
        return state["archive_evidence"]

    def freeze(
        self, *, authorized_active_threads: Mapping[str, str]
    ) -> ContinuousThreadLineageReceiptV1:
        """Freeze after deriving failure codes without accepting caller claims."""

        if self._frozen_receipt is not None:
            expected = tuple(sorted(authorized_active_threads.items()))
            if expected != self._frozen_receipt.authorized_active_threads:
                raise StateConflictError("frozen thread lineage active map changed")
            return self._frozen_receipt
        active = tuple(sorted(authorized_active_threads.items()))
        active_ids = {value for _role, value in active}
        entries = []
        for thread_sha256 in sorted(self._threads):
            state = self._threads[thread_sha256]
            base: ContinuousThreadLineageEntryV1 = state["base"]
            archive = state["archive_evidence"]
            events = list(state["events"])
            if thread_sha256 in active_ids and archive is not None:
                disposition = "invalid_contradictory"
            elif thread_sha256 in active_ids:
                disposition = "authorized_active"
                events.append(ContinuousThreadLifecycleEventV1("authorized_active"))
            elif archive is None:
                disposition = "invalid_unresolved"
            elif archive.verified is True:
                disposition = "verified_archived"
            else:
                disposition = "invalid_archive_evidence"
            entries.append(
                ContinuousThreadLineageEntryV1(
                    role=base.role,
                    purpose=base.purpose,
                    world_id=base.world_id,
                    branch_id=base.branch_id,
                    session_compatibility_sha256=base.session_compatibility_sha256,
                    provider_thread_sha256=base.provider_thread_sha256,
                    parent_provider_thread_sha256=base.parent_provider_thread_sha256,
                    creation_operation=base.creation_operation,
                    lifecycle_events=tuple(events),
                    terminal_disposition=disposition,
                    archive_evidence=archive,
                )
            )
        entries_tuple = tuple(entries)
        failures = ContinuousThreadLineageReceiptV1.derive_failures(
            entries=entries_tuple,
            authorized_active_threads=active,
        )
        payload = {
            "schema_version": ContinuousThreadLineageReceiptV1.SCHEMA_VERSION,
            "entries": entries_tuple,
            "authorized_active_threads": active,
            "status": "verified" if not failures else "failed",
            "failure_codes": failures,
        }
        receipt = ContinuousThreadLineageReceiptV1(
            **payload,
            receipt_sha256=canonical_sha256(payload),
        )
        self._frozen_receipt = receipt
        return receipt

    def _event(
        self,
        provider_thread_sha256: str,
        event_type: str,
        *,
        reason_sha256: str | None = None,
        related_thread_sha256: str | None = None,
    ) -> None:
        self._assert_mutable()
        if provider_thread_sha256 not in self._threads:
            raise StateConflictError("thread lifecycle event belongs to an unknown thread")
        self._threads[provider_thread_sha256]["events"].append(
            ContinuousThreadLifecycleEventV1(
                event_type=event_type,
                reason_sha256=reason_sha256,
                related_thread_sha256=related_thread_sha256,
            )
        )

    def _assert_mutable(self) -> None:
        if self._frozen_receipt is not None:
            raise StateConflictError("continuous thread lineage is frozen")
