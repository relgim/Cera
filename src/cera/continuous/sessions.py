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
import re
from typing import Any, Callable, ClassVar, Mapping, Protocol, runtime_checkable

from cera.errors import ContractValidationError, StateConflictError
from cera.schema import from_mapping
from cera.serialization import (
    bytes_sha256,
    canonical_bytes,
    canonical_sha256,
    domain_sha256,
    re_is_sha256,
    text_sha256,
    to_primitive,
)

from .contracts import AcceptedFinalSequenceEnvelopeV1, CharacterSummaryEnvelopeV1
from .path_policy import (
    CONTINUOUS_WINDOWS_LEGACY_PATH_MAX_CHARACTERS,
    preflight_windows_legacy_paths,
)
from .thread_lineage import ContinuousThreadLineageLedger


class ContinuousSessionRole(StrEnum):
    PLANNER = "planner"
    VALIDATOR = "validator"


class PlannerContextMode(StrEnum):
    """Closed D-200 Planner context modes.

    The values are deliberately disjoint.  In particular, loss or
    incompatibility is reconstruction and can never be relabeled as a
    projection-assisted ordinary turn.
    """

    LEAN_CONTINUOUS = "lean_continuous"
    PROJECTION_ASSISTED = "projection_assisted"
    RECONSTRUCTION = "reconstruction"


class ContinuousSessionInitializationKind(StrEnum):
    FIRST_THREAD_INITIALIZATION = "first_thread_initialization"
    RECONSTRUCTION_INITIALIZATION = "reconstruction_initialization"
    ACCEPTED_CHECKPOINT_FORK_INITIALIZATION = (
        "accepted_checkpoint_fork_initialization"
    )


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
    SCHEMA_VERSION: ClassVar[str] = "cera.continuous_session_compatibility.v3"

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
    default_context_mode: PlannerContextMode = PlannerContextMode.LEAN_CONTINUOUS
    allowed_context_modes: tuple[PlannerContextMode, ...] = (
        PlannerContextMode.LEAN_CONTINUOUS,
        PlannerContextMode.PROJECTION_ASSISTED,
        PlannerContextMode.RECONSTRUCTION,
    )

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
        if self.default_context_mode is not PlannerContextMode.LEAN_CONTINUOUS:
            raise ContractValidationError(
                "continuous Planner default context mode must be lean_continuous"
            )
        if (
            not self.allowed_context_modes
            or len(self.allowed_context_modes) != len(set(self.allowed_context_modes))
            or self.default_context_mode not in self.allowed_context_modes
        ):
            raise ContractValidationError(
                "continuous compatibility context-mode allow-list is invalid"
            )
        if set(self.allowed_context_modes) - set(PlannerContextMode):
            raise ContractValidationError(
                "continuous compatibility context mode is unsupported"
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
class ContinuousSessionInitializationReceiptV1:
    """One physical-thread initialization, separate from ordinary turn usage."""

    SCHEMA_VERSION: ClassVar[str] = "cera.continuous_session_initialization_receipt.v1"

    schema_version: str
    world_id: str
    branch_id: str
    role: ContinuousSessionRole
    context_mode: PlannerContextMode
    provider_session_id_sha256: str
    provider_thread_sha256: str
    parent_provider_thread_sha256: str | None
    branch_receipt_sha256: str | None
    stable_instruction_sha256: str
    stable_instruction_bytes: int
    stable_instruction_estimated_tokens: int
    reconstruction_payload_sha256: str | None
    reconstruction_bytes: int
    reconstruction_estimated_tokens: int
    accepted_tail_turn_ids: tuple[str, ...]
    character_summary_envelope_sha256s: tuple[str, ...]
    receipt_sha256: str

    @property
    def packet_kind(self) -> ContinuousSessionInitializationKind:
        if self.context_mode is PlannerContextMode.RECONSTRUCTION:
            return ContinuousSessionInitializationKind.RECONSTRUCTION_INITIALIZATION
        if self.branch_receipt_sha256 is not None:
            return (
                ContinuousSessionInitializationKind.ACCEPTED_CHECKPOINT_FORK_INITIALIZATION
            )
        return ContinuousSessionInitializationKind.FIRST_THREAD_INITIALIZATION

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError(
                "continuous session initialization receipt schema changed"
            )
        if not all(
            isinstance(value, str) and value.strip()
            for value in (self.world_id, self.branch_id)
        ):
            raise ContractValidationError(
                "continuous session initialization scope is incomplete"
            )
        hashes = (
            self.provider_session_id_sha256,
            self.provider_thread_sha256,
            self.stable_instruction_sha256,
            self.receipt_sha256,
        )
        if any(not re_is_sha256(value) for value in hashes):
            raise ContractValidationError(
                "continuous session initialization hash is invalid"
            )
        for value in (
            self.parent_provider_thread_sha256,
            self.branch_receipt_sha256,
            self.reconstruction_payload_sha256,
        ):
            if value is not None and not re_is_sha256(value):
                raise ContractValidationError(
                    "continuous session initialization optional hash is invalid"
                )
        for field_name in (
            "stable_instruction_bytes",
            "stable_instruction_estimated_tokens",
            "reconstruction_bytes",
            "reconstruction_estimated_tokens",
        ):
            value = getattr(self, field_name)
            if type(value) is not int or value < 0:
                raise ContractValidationError(
                    "continuous session initialization size is invalid"
                )
        if self.stable_instruction_estimated_tokens != (
            self.stable_instruction_bytes + 3
        ) // 4:
            raise ContractValidationError(
                "continuous stable-instruction token estimate changed"
            )
        if self.reconstruction_estimated_tokens != (
            self.reconstruction_bytes + 3
        ) // 4:
            raise ContractValidationError(
                "continuous reconstruction token estimate changed"
            )
        if len(self.accepted_tail_turn_ids) != len(set(self.accepted_tail_turn_ids)):
            raise ContractValidationError(
                "continuous reconstruction accepted tail is duplicated"
            )
        if len(self.character_summary_envelope_sha256s) != len(
            set(self.character_summary_envelope_sha256s)
        ):
            raise ContractValidationError(
                "continuous reconstruction summary delivery is duplicated"
            )
        if any(
            not re_is_sha256(value)
            for value in self.character_summary_envelope_sha256s
        ):
            raise ContractValidationError(
                "continuous reconstruction summary hash is invalid"
            )
        branch_fork_initialization = (
            self.branch_receipt_sha256 is not None
            and self.context_mode is PlannerContextMode.LEAN_CONTINUOUS
        )
        if self.context_mode is PlannerContextMode.RECONSTRUCTION:
            if (
                self.reconstruction_payload_sha256 is None
                or self.reconstruction_bytes <= 0
            ):
                raise ContractValidationError(
                    "continuous reconstruction lacks its exact payload"
                )
        elif branch_fork_initialization:
            if (
                self.context_mode is not PlannerContextMode.LEAN_CONTINUOUS
                or self.parent_provider_thread_sha256 is None
                or self.reconstruction_payload_sha256 is not None
                or self.reconstruction_bytes != 0
                or self.reconstruction_estimated_tokens != 0
                or not self.accepted_tail_turn_ids
            ):
                raise ContractValidationError(
                    "accepted-checkpoint fork initialization custody changed"
                )
        elif any(
            (
                self.reconstruction_payload_sha256 is not None,
                self.reconstruction_bytes != 0,
                self.reconstruction_estimated_tokens != 0,
                bool(self.accepted_tail_turn_ids),
                bool(self.character_summary_envelope_sha256s),
            )
        ):
            raise ContractValidationError(
                "ordinary session initialization carries reconstruction state"
            )
        expected = canonical_sha256(
            {
                "schema_version": self.SCHEMA_VERSION,
                "world_id": self.world_id,
                "branch_id": self.branch_id,
                "role": self.role.value,
                "context_mode": self.context_mode.value,
                "provider_session_id_sha256": self.provider_session_id_sha256,
                "provider_thread_sha256": self.provider_thread_sha256,
                "parent_provider_thread_sha256": self.parent_provider_thread_sha256,
                "branch_receipt_sha256": self.branch_receipt_sha256,
                "stable_instruction_sha256": self.stable_instruction_sha256,
                "stable_instruction_bytes": self.stable_instruction_bytes,
                "stable_instruction_estimated_tokens": self.stable_instruction_estimated_tokens,
                "reconstruction_payload_sha256": self.reconstruction_payload_sha256,
                "reconstruction_bytes": self.reconstruction_bytes,
                "reconstruction_estimated_tokens": self.reconstruction_estimated_tokens,
                "accepted_tail_turn_ids": self.accepted_tail_turn_ids,
                "character_summary_envelope_sha256s": self.character_summary_envelope_sha256s,
            }
        )
        if self.receipt_sha256 != expected:
            raise ContractValidationError(
                "continuous session initialization receipt binding changed"
            )


@dataclass(frozen=True, slots=True)
class ContinuousSessionInitializationPacketV1:
    """Closed first-thread, reconstruction, or accepted-fork initialization."""

    SCHEMA_VERSION: ClassVar[str] = (
        "cera.continuous_session_initialization_packet.v1"
    )
    ALLOWED_FIELDS: ClassVar[frozenset[str]] = frozenset(
        {"schema_version", "packet_kind", "initialization_receipt"}
    )

    schema_version: str
    packet_kind: ContinuousSessionInitializationKind
    initialization_receipt: ContinuousSessionInitializationReceiptV1

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError(
                "continuous session initialization packet schema changed"
            )
        if self.packet_kind is not self.initialization_receipt.packet_kind:
            raise ContractValidationError(
                "continuous session initialization packet kind changed"
            )
        if set(self.to_payload()) != self.ALLOWED_FIELDS:
            raise ContractValidationError(
                "continuous session initialization packet fields changed"
            )

    @classmethod
    def from_receipt(
        cls,
        receipt: ContinuousSessionInitializationReceiptV1,
    ) -> "ContinuousSessionInitializationPacketV1":
        return cls(
            schema_version=cls.SCHEMA_VERSION,
            packet_kind=receipt.packet_kind,
            initialization_receipt=receipt,
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.SCHEMA_VERSION,
            "packet_kind": self.packet_kind.value,
            "initialization_receipt": to_primitive(
                self.initialization_receipt
            ),
        }

    @property
    def packet_sha256(self) -> str:
        return canonical_sha256(self.to_payload())

    @property
    def packet_bytes(self) -> int:
        return len(canonical_bytes(self.to_payload()))


@dataclass(frozen=True, slots=True)
class ContinuousBranchForkReceiptV1:
    """Historical V1 fork custody retained for immutable-artifact decoding."""

    SCHEMA_VERSION: ClassVar[str] = "cera.continuous_branch_fork_receipt.v1"

    schema_version: str
    world_id: str
    parent_branch_id: str
    child_branch_id: str
    accepted_checkpoint_turn_id: str
    accepted_ancestry_sha256: str
    parent_provider_thread_sha256: str
    privacy_boundary_sha256: str
    receipt_sha256: str

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("continuous branch-fork receipt schema changed")
        if not all(
            isinstance(value, str) and value.strip()
            for value in (
                self.world_id,
                self.parent_branch_id,
                self.child_branch_id,
                self.accepted_checkpoint_turn_id,
            )
        ):
            raise ContractValidationError("continuous branch-fork scope is incomplete")
        if self.parent_branch_id == self.child_branch_id:
            raise ContractValidationError("continuous branch fork did not create a branch")
        if any(
            not re_is_sha256(value)
            for value in (
                self.accepted_ancestry_sha256,
                self.parent_provider_thread_sha256,
                self.privacy_boundary_sha256,
                self.receipt_sha256,
            )
        ):
            raise ContractValidationError("continuous branch-fork hash is invalid")
        expected = canonical_sha256(
            {
                "schema_version": self.SCHEMA_VERSION,
                "world_id": self.world_id,
                "parent_branch_id": self.parent_branch_id,
                "child_branch_id": self.child_branch_id,
                "accepted_checkpoint_turn_id": self.accepted_checkpoint_turn_id,
                "accepted_ancestry_sha256": self.accepted_ancestry_sha256,
                "parent_provider_thread_sha256": self.parent_provider_thread_sha256,
                "privacy_boundary_sha256": self.privacy_boundary_sha256,
            }
        )
        if self.receipt_sha256 != expected:
            raise ContractValidationError("continuous branch-fork receipt changed")


@dataclass(frozen=True, slots=True)
class ContinuousBranchForkReceiptV2:
    """Python custody for a provider fork at one accepted checkpoint."""

    SCHEMA_VERSION: ClassVar[str] = "cera.continuous_branch_fork_receipt.v2"

    schema_version: str
    world_id: str
    parent_branch_id: str
    child_branch_id: str
    accepted_checkpoint_turn_id: str
    accepted_ancestry_sha256: str
    parent_provider_thread_sha256: str
    privacy_boundary_sha256: str
    branch_materialization_receipt_sha256: str
    receipt_sha256: str

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("continuous branch-fork receipt schema changed")
        if not all(
            isinstance(value, str) and value.strip()
            for value in (
                self.world_id,
                self.parent_branch_id,
                self.child_branch_id,
                self.accepted_checkpoint_turn_id,
            )
        ):
            raise ContractValidationError("continuous branch-fork scope is incomplete")
        if self.parent_branch_id == self.child_branch_id:
            raise ContractValidationError("continuous branch fork did not create a branch")
        if any(
            not re_is_sha256(value)
            for value in (
                self.accepted_ancestry_sha256,
                self.parent_provider_thread_sha256,
                self.privacy_boundary_sha256,
                self.branch_materialization_receipt_sha256,
                self.receipt_sha256,
            )
        ):
            raise ContractValidationError("continuous branch-fork hash is invalid")
        expected = canonical_sha256(
            {
                "schema_version": self.SCHEMA_VERSION,
                "world_id": self.world_id,
                "parent_branch_id": self.parent_branch_id,
                "child_branch_id": self.child_branch_id,
                "accepted_checkpoint_turn_id": self.accepted_checkpoint_turn_id,
                "accepted_ancestry_sha256": self.accepted_ancestry_sha256,
                "parent_provider_thread_sha256": self.parent_provider_thread_sha256,
                "privacy_boundary_sha256": self.privacy_boundary_sha256,
                "branch_materialization_receipt_sha256": (
                    self.branch_materialization_receipt_sha256
                ),
            }
        )
        if self.receipt_sha256 != expected:
            raise ContractValidationError("continuous branch-fork receipt changed")


def continuous_branch_privacy_boundary_sha256(
    *,
    parent_compatibility: ContinuousSessionCompatibilityV1,
    child_compatibility: ContinuousSessionCompatibilityV1,
    accepted_checkpoint_turn_id: str,
    accepted_ancestry_sha256: str,
    parent_provider_thread_sha256: str,
) -> str:
    """Return the one active privacy identity for an accepted-checkpoint fork."""

    if (
        parent_compatibility.role is not ContinuousSessionRole.PLANNER
        or child_compatibility.role is not ContinuousSessionRole.PLANNER
        or parent_compatibility.world_id != child_compatibility.world_id
        or parent_compatibility.branch_id == child_compatibility.branch_id
    ):
        raise StateConflictError("continuous branch privacy scope is invalid")
    if not accepted_checkpoint_turn_id.strip() or any(
        not re_is_sha256(value)
        for value in (accepted_ancestry_sha256, parent_provider_thread_sha256)
    ):
        raise ContractValidationError("continuous branch privacy identity is incomplete")
    return canonical_sha256(
        {
            "schema_version": "cera.continuous_branch_privacy_boundary.v2",
            "policy_identity": "accepted_checkpoint_exact_ancestry_owner_isolation",
            "world_id": parent_compatibility.world_id,
            "parent_branch_id": parent_compatibility.branch_id,
            "child_branch_id": child_compatibility.branch_id,
            "accepted_checkpoint_turn_id": accepted_checkpoint_turn_id,
            "accepted_ancestry_sha256": accepted_ancestry_sha256,
            "parent_provider_thread_sha256": parent_provider_thread_sha256,
            "authority_policy_version": parent_compatibility.authority_policy_version,
            "privacy_policy_version": parent_compatibility.privacy_policy_version,
            "protected_user_policy_version": (
                parent_compatibility.protected_user_policy_version
            ),
            "session_policy_version": parent_compatibility.session_policy_version,
            "child_authority_policy_version": child_compatibility.authority_policy_version,
            "child_privacy_policy_version": child_compatibility.privacy_policy_version,
            "child_protected_user_policy_version": (
                child_compatibility.protected_user_policy_version
            ),
            "child_session_policy_version": child_compatibility.session_policy_version,
        }
    )


@dataclass(frozen=True, slots=True)
class ContinuousBranchReferenceTransferReceiptV1:
    """Exact child-thread custody for value-free accepted-reference rebinding."""

    SCHEMA_VERSION: ClassVar[str] = "cera.continuous_branch_reference_transfer.v1"

    schema_version: str
    world_id: str
    child_branch_id: str
    accepted_turn_id: str
    accepted_envelope_sha256: str
    child_provider_thread_sha256: str
    branch_receipt_sha256: str
    parent_reference_keys: tuple[str, ...]
    child_reference_keys: tuple[str, ...]
    child_descriptor_set_sha256: str
    injected_context_sha256: str
    operation_receipt_sha256: str

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError(
                "continuous branch-reference transfer schema changed"
            )
        if not all(
            isinstance(value, str) and value.strip()
            for value in (self.world_id, self.child_branch_id, self.accepted_turn_id)
        ):
            raise ContractValidationError(
                "continuous branch-reference transfer scope is incomplete"
            )
        for keys in (self.parent_reference_keys, self.child_reference_keys):
            if not keys or len(keys) != len(set(keys)) or any(
                not value.startswith("binding_accepted_ref_") for value in keys
            ):
                raise ContractValidationError(
                    "continuous branch-reference transfer keys are invalid"
                )
        if len(self.parent_reference_keys) != len(self.child_reference_keys):
            raise ContractValidationError(
                "continuous branch-reference transfer mapping is incomplete"
            )
        if set(self.parent_reference_keys).intersection(self.child_reference_keys):
            raise ContractValidationError(
                "continuous branch-reference transfer reused a parent key"
            )
        for value in (
            self.accepted_envelope_sha256,
            self.child_provider_thread_sha256,
            self.branch_receipt_sha256,
            self.child_descriptor_set_sha256,
            self.injected_context_sha256,
            self.operation_receipt_sha256,
        ):
            if not re_is_sha256(value):
                raise ContractValidationError(
                    "continuous branch-reference transfer hash is invalid"
                )
        expected = canonical_sha256(
            {
                "schema_version": self.SCHEMA_VERSION,
                "world_id": self.world_id,
                "child_branch_id": self.child_branch_id,
                "accepted_turn_id": self.accepted_turn_id,
                "accepted_envelope_sha256": self.accepted_envelope_sha256,
                "child_provider_thread_sha256": self.child_provider_thread_sha256,
                "branch_receipt_sha256": self.branch_receipt_sha256,
                "parent_reference_keys": self.parent_reference_keys,
                "child_reference_keys": self.child_reference_keys,
                "child_descriptor_set_sha256": self.child_descriptor_set_sha256,
                "injected_context_sha256": self.injected_context_sha256,
            }
        )
        if self.operation_receipt_sha256 != expected:
            raise ContractValidationError(
                "continuous branch-reference transfer receipt changed"
            )


@dataclass(frozen=True, slots=True)
class CharacterSummaryDeliveryReceiptV1:
    """Planner-thread delivery custody; never suppresses Composer context."""

    SCHEMA_VERSION: ClassVar[str] = "cera.character_summary_delivery_receipt.v1"

    schema_version: str
    character_id: str
    source_path_or_record_id: str
    source_revision: int
    source_sha256: str
    envelope_sha256: str
    selected_content_sha256: str
    provider_thread_sha256: str
    delivery_reason: str
    planner_prompt_sha256: str
    receipt_sha256: str

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("character-summary delivery schema changed")
        if not self.character_id or not self.source_path_or_record_id:
            raise ContractValidationError("character-summary delivery identity is incomplete")
        if type(self.source_revision) is not int or self.source_revision < 1:
            raise ContractValidationError("character-summary delivery revision is invalid")
        if self.delivery_reason not in {
            "first_relevant_appearance",
            "newly_relevant_character",
            "material_revision_change",
            "scene_change",
            "reconstruction",
            "accepted_checkpoint_fork",
            "planner_requested_exact_evidence",
        }:
            raise ContractValidationError("character-summary delivery reason is invalid")
        if any(
            not re_is_sha256(value)
            for value in (
                self.source_sha256,
                self.envelope_sha256,
                self.selected_content_sha256,
                self.provider_thread_sha256,
                self.planner_prompt_sha256,
                self.receipt_sha256,
            )
        ):
            raise ContractValidationError("character-summary delivery hash is invalid")
        expected = canonical_sha256(
            {
                "schema_version": self.SCHEMA_VERSION,
                "character_id": self.character_id,
                "source_path_or_record_id": self.source_path_or_record_id,
                "source_revision": self.source_revision,
                "source_sha256": self.source_sha256,
                "envelope_sha256": self.envelope_sha256,
                "selected_content_sha256": self.selected_content_sha256,
                "provider_thread_sha256": self.provider_thread_sha256,
                "delivery_reason": self.delivery_reason,
                "planner_prompt_sha256": self.planner_prompt_sha256,
            }
        )
        if self.receipt_sha256 != expected:
            raise ContractValidationError("character-summary delivery receipt changed")


@dataclass(frozen=True, slots=True)
class ContinuousReconstructionAcceptedTurnV1:
    envelope: AcceptedFinalSequenceEnvelopeV1
    synchronization_receipt_sha256: str
    stable_reference_descriptors: tuple[Mapping[str, Any], ...]

    def __post_init__(self) -> None:
        if not re_is_sha256(self.synchronization_receipt_sha256):
            raise ContractValidationError(
                "reconstruction accepted turn lacks synchronization custody"
            )
        keys = tuple(
            value.get("reference_key")
            for value in self.stable_reference_descriptors
            if isinstance(value, Mapping)
        )
        if len(keys) != len(self.stable_reference_descriptors) or any(
            not isinstance(value, str)
            or not value.startswith("binding_accepted_ref_")
            for value in keys
        ):
            raise ContractValidationError(
                "reconstruction stable-reference descriptor is invalid"
            )
        if len(keys) != len(set(keys)):
            raise ContractValidationError(
                "reconstruction stable-reference descriptor is duplicated"
            )


@dataclass(frozen=True, slots=True)
class ContinuousSessionReconstructionBundleV1:
    """Bounded Python-owned context for a newly created physical thread."""

    SCHEMA_VERSION: ClassVar[str] = "cera.continuous_session_reconstruction_bundle.v1"

    schema_version: str
    world_id: str
    branch_id: str
    accepted_tail: tuple[ContinuousReconstructionAcceptedTurnV1, ...]
    character_summaries: tuple[CharacterSummaryEnvelopeV1, ...]
    accepted_ancestry_sha256: str
    reconstruction_reason: str

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("continuous reconstruction bundle schema changed")
        if not self.world_id or not self.branch_id:
            raise ContractValidationError("continuous reconstruction bundle scope is incomplete")
        if not self.accepted_tail or len(self.accepted_tail) > 5:
            raise ContractValidationError(
                "continuous reconstruction accepted tail must contain one to five turns"
            )
        turn_ids = tuple(value.envelope.accepted_turn_id for value in self.accepted_tail)
        if len(turn_ids) != len(set(turn_ids)):
            raise ContractValidationError("continuous reconstruction tail is duplicated")
        if not re_is_sha256(self.accepted_ancestry_sha256):
            raise ContractValidationError("continuous reconstruction ancestry is invalid")
        expected_ancestry = canonical_sha256(
            {
                "world_id": self.world_id,
                "branch_id": self.branch_id,
                "accepted_tail": tuple(
                    (
                        value.envelope.accepted_turn_id,
                        value.envelope.envelope_sha256,
                        value.synchronization_receipt_sha256,
                    )
                    for value in self.accepted_tail
                ),
            }
        )
        if self.accepted_ancestry_sha256 != expected_ancestry:
            raise ContractValidationError("continuous reconstruction ancestry changed")
        if self.reconstruction_reason not in {
            "lost_thread",
            "archived_thread",
            "incompatible_thread",
            "deliberate_restart",
            "non_forkable_branch",
        }:
            raise ContractValidationError("continuous reconstruction reason is invalid")
        summary_ids = tuple(value.character_id for value in self.character_summaries)
        if len(summary_ids) != len(set(summary_ids)):
            raise ContractValidationError("continuous reconstruction summaries are duplicated")

    @property
    def bundle_sha256(self) -> str:
        return domain_sha256(self.SCHEMA_VERSION, self)

    def render_for_planner(self) -> str:
        accepted = tuple(
            {
                "synchronization_receipt_sha256": value.synchronization_receipt_sha256,
                "accepted_context": value.envelope.render_for_planner(
                    stable_reference_descriptors=tuple(
                        dict(item) for item in value.stable_reference_descriptors
                    )
                ),
            }
            for value in self.accepted_tail
        )
        payload = {
            "schema_version": self.SCHEMA_VERSION,
            "world_id": self.world_id,
            "branch_id": self.branch_id,
            "accepted_ancestry_sha256": self.accepted_ancestry_sha256,
            "reconstruction_reason": self.reconstruction_reason,
            "accepted_tail": accepted,
            "character_summaries": tuple(
                to_primitive(value) for value in self.character_summaries
            ),
            "authority_note": (
                "Python-owned accepted ancestry only; provider transcript is advisory."
            ),
        }
        return "[SESSION RECONSTRUCTION]\n" + canonical_bytes(payload).decode("utf-8")


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
            "branch_reference_rebinding",
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
    SCHEMA_VERSION: ClassVar[str] = "cera.continuous_session_snapshot.v2"

    schema_version: str
    compatibility: ContinuousSessionCompatibilityV1
    handle: ContinuousSessionHandleV1
    context_events: tuple[ContinuousContextEventV1, ...]
    accepted_turn_ids: tuple[str, ...]
    initialization_receipt: ContinuousSessionInitializationReceiptV1 | None = None
    character_summary_deliveries: tuple[CharacterSummaryDeliveryReceiptV1, ...] = ()

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
        if self.initialization_receipt is not None and (
            self.initialization_receipt.world_id != self.compatibility.world_id
            or self.initialization_receipt.branch_id != self.compatibility.branch_id
            or self.initialization_receipt.role is not self.compatibility.role
            or self.initialization_receipt.provider_thread_sha256
            != self.handle.provider_thread_id_sha256
        ):
            raise ContractValidationError(
                "continuous initialization receipt changed session identity"
            )
        delivered: set[tuple[str, str, int, str]] = set()
        for receipt in self.character_summary_deliveries:
            if receipt.provider_thread_sha256 != self.handle.provider_thread_id_sha256:
                raise ContractValidationError(
                    "character-summary delivery belongs to another physical thread"
                )
            key = (
                receipt.character_id,
                receipt.source_path_or_record_id,
                receipt.source_revision,
                receipt.source_sha256,
            )
            if key in delivered:
                raise ContractValidationError(
                    "character-summary delivery identity is duplicated"
                )
            delivered.add(key)

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


CONTINUOUS_ACCEPTED_SNAPSHOT_PATH_POLICY_VERSION = (
    "cera.continuous_accepted_snapshot_path_policy.v1"
)
CONTINUOUS_ACCEPTED_SNAPSHOT_MAX_RESOLVED_CHARS = (
    CONTINUOUS_WINDOWS_LEGACY_PATH_MAX_CHARACTERS
)
CONTINUOUS_ACCEPTED_SNAPSHOT_PATH_POLICY_SHA256 = canonical_sha256(
    {
        "policy_version": CONTINUOUS_ACCEPTED_SNAPSHOT_PATH_POLICY_VERSION,
        "maximum_resolved_path_characters": (
            CONTINUOUS_ACCEPTED_SNAPSHOT_MAX_RESOLVED_CHARS
        ),
        "accepted_directory": "PLANNER_SESSION/ACCEPTED/v2",
        "turn_locator_sha256_characters": 16,
        "snapshot_locator_sha256_characters": 24,
        "temporary_name_rule": "same_directory_append_dot_tmp",
        "full_hashes_are_authority": True,
    }
)


def _accepted_snapshot_relative_path(
    accepted_turn_id: str, snapshot_sha256: str
) -> str:
    """Return a compact locator; full identities remain envelope authority."""

    return (
        "PLANNER_SESSION/ACCEPTED/v2/"
        + text_sha256(accepted_turn_id)[:16]
        + "-"
        + snapshot_sha256[:24]
        + ".json"
    )


def _same_directory_temporary_path(path: Path) -> Path:
    return path.with_name(path.name + ".tmp")


@dataclass(frozen=True, slots=True)
class ContinuousAcceptedSnapshotPathPlanV1:
    """Resolved Windows-legacy-safe acceptance paths validated before mutation."""

    SCHEMA_VERSION: ClassVar[str] = "cera.continuous_accepted_snapshot_path_plan.v1"

    schema_version: str
    path_policy_sha256: str
    maximum_resolved_path_characters: int
    current_relative_path: str
    current_temporary_relative_path: str
    immutable_relative_path: str
    immutable_temporary_relative_path: str
    current_resolved_path_characters: int
    current_temporary_resolved_path_characters: int
    immutable_resolved_path_characters: int
    immutable_temporary_resolved_path_characters: int
    plan_sha256: str

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("continuous snapshot path plan schema changed")
        if self.path_policy_sha256 != CONTINUOUS_ACCEPTED_SNAPSHOT_PATH_POLICY_SHA256:
            raise ContractValidationError("continuous snapshot path policy changed")
        if (
            self.maximum_resolved_path_characters
            != CONTINUOUS_ACCEPTED_SNAPSHOT_MAX_RESOLVED_CHARS
        ):
            raise ContractValidationError("continuous snapshot path budget changed")
        if self.current_relative_path != "PLANNER_SESSION/SESSION_SNAPSHOT.json":
            raise ContractValidationError("continuous current snapshot path changed")
        if (
            self.current_temporary_relative_path
            != "PLANNER_SESSION/SESSION_SNAPSHOT.json.tmp"
        ):
            raise ContractValidationError("continuous current snapshot temporary path changed")
        if re.fullmatch(
            r"PLANNER_SESSION/ACCEPTED/v2/[0-9a-f]{16}-[0-9a-f]{24}\.json",
            self.immutable_relative_path,
        ) is None:
            raise ContractValidationError("continuous compact snapshot path changed")
        if self.immutable_temporary_relative_path != self.immutable_relative_path + ".tmp":
            raise ContractValidationError("continuous immutable temporary path changed")
        path_lengths = (
            self.current_resolved_path_characters,
            self.current_temporary_resolved_path_characters,
            self.immutable_resolved_path_characters,
            self.immutable_temporary_resolved_path_characters,
        )
        if any(
            type(value) is not int
            or value < 1
            or value > self.maximum_resolved_path_characters
            for value in path_lengths
        ):
            raise ContractValidationError("continuous snapshot path exceeds legacy budget")
        expected = canonical_sha256(
            {
                "schema_version": self.SCHEMA_VERSION,
                "path_policy_sha256": self.path_policy_sha256,
                "maximum_resolved_path_characters": (
                    self.maximum_resolved_path_characters
                ),
                "current_relative_path": self.current_relative_path,
                "current_temporary_relative_path": (
                    self.current_temporary_relative_path
                ),
                "immutable_relative_path": self.immutable_relative_path,
                "immutable_temporary_relative_path": (
                    self.immutable_temporary_relative_path
                ),
                "current_resolved_path_characters": (
                    self.current_resolved_path_characters
                ),
                "current_temporary_resolved_path_characters": (
                    self.current_temporary_resolved_path_characters
                ),
                "immutable_resolved_path_characters": (
                    self.immutable_resolved_path_characters
                ),
                "immutable_temporary_resolved_path_characters": (
                    self.immutable_temporary_resolved_path_characters
                ),
            }
        )
        if self.plan_sha256 != expected:
            raise ContractValidationError("continuous snapshot path plan binding changed")


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


@dataclass(frozen=True, slots=True)
class ContinuousSessionSnapshotReceiptV2:
    """Compact-locator receipt retaining complete acceptance authority."""

    SCHEMA_VERSION: ClassVar[str] = "cera.continuous_session_snapshot_receipt.v2"

    schema_version: str
    world_id: str
    branch_id: str
    role: ContinuousSessionRole
    accepted_turn_id: str
    accepted_turn_id_sha256: str
    accepted_envelope_sha256: str
    provider_thread_sha256: str
    snapshot_sha256: str
    injection_receipt: ContinuousContextInjectionReceiptV1
    injection_operation_receipt_sha256: str
    encoded_snapshot_file_sha256: str
    immutable_relative_path: str
    immutable_file_sha256: str
    current_relative_path: str
    path_plan: ContinuousAcceptedSnapshotPathPlanV1
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
            self.accepted_turn_id_sha256,
            self.accepted_envelope_sha256,
            self.provider_thread_sha256,
            self.snapshot_sha256,
            self.injection_operation_receipt_sha256,
            self.encoded_snapshot_file_sha256,
            self.immutable_file_sha256,
            self.receipt_sha256,
        ):
            if not re_is_sha256(value):
                raise ContractValidationError("continuous snapshot receipt hash is invalid")
        if self.accepted_turn_id_sha256 != text_sha256(self.accepted_turn_id):
            raise ContractValidationError("continuous snapshot accepted-turn identity changed")
        if self.immutable_relative_path != _accepted_snapshot_relative_path(
            self.accepted_turn_id, self.snapshot_sha256
        ):
            raise ContractValidationError("continuous compact snapshot locator changed")
        if (
            self.current_relative_path != "PLANNER_SESSION/SESSION_SNAPSHOT.json"
            or self.path_plan.current_relative_path != self.current_relative_path
            or self.path_plan.immutable_relative_path != self.immutable_relative_path
        ):
            raise ContractValidationError("continuous snapshot path-plan binding changed")
        if (
            self.injection_receipt.accepted_turn_id != self.accepted_turn_id
            or self.injection_receipt.accepted_envelope_sha256
            != self.accepted_envelope_sha256
            or self.injection_receipt.provider_thread_sha256
            != self.provider_thread_sha256
            or self.injection_receipt.operation_receipt_sha256
            != self.injection_operation_receipt_sha256
        ):
            raise ContractValidationError("continuous snapshot injection receipt changed")
        payload = {
            "schema_version": self.SCHEMA_VERSION,
            "world_id": self.world_id,
            "branch_id": self.branch_id,
            "role": self.role.value,
            "accepted_turn_id": self.accepted_turn_id,
            "accepted_turn_id_sha256": self.accepted_turn_id_sha256,
            "accepted_envelope_sha256": self.accepted_envelope_sha256,
            "provider_thread_sha256": self.provider_thread_sha256,
            "snapshot_sha256": self.snapshot_sha256,
            "injection_receipt": to_primitive(self.injection_receipt),
            "injection_operation_receipt_sha256": (
                self.injection_operation_receipt_sha256
            ),
            "encoded_snapshot_file_sha256": self.encoded_snapshot_file_sha256,
            "immutable_relative_path": self.immutable_relative_path,
            "immutable_file_sha256": self.immutable_file_sha256,
            "current_relative_path": self.current_relative_path,
            "path_plan": to_primitive(self.path_plan),
        }
        if self.receipt_sha256 != canonical_sha256(payload):
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

    def _resolve_owned_path(self, relative_path: str) -> Path:
        path = (self.branch_root / relative_path).resolve()
        if self.branch_root != path and self.branch_root not in path.parents:
            raise StateConflictError("continuous snapshot path escaped branch custody")
        return path

    @staticmethod
    def _snapshot_envelope(snapshot: ContinuousSessionSnapshotV1) -> dict[str, Any]:
        return {
            "schema_version": "cera.continuous_session_snapshot_envelope.v1",
            "snapshot": to_primitive(snapshot),
            "snapshot_sha256": snapshot.snapshot_sha256,
        }

    @staticmethod
    def _validate_resolved_path_budget(*paths: Path) -> tuple[int, ...]:
        return preflight_windows_legacy_paths(
            *paths,
            label="continuous snapshot",
        )

    def preflight_acceptance_paths(
        self, *, accepted_turn_id: str, snapshot_sha256: str
    ) -> ContinuousAcceptedSnapshotPathPlanV1:
        """Validate all final/temp paths before acceptance mutates any file."""

        if not isinstance(accepted_turn_id, str) or not accepted_turn_id.strip():
            raise ContractValidationError("continuous accepted snapshot turn is empty")
        if not re_is_sha256(snapshot_sha256):
            raise ContractValidationError("continuous accepted snapshot hash is invalid")
        current_relative = "PLANNER_SESSION/SESSION_SNAPSHOT.json"
        immutable_relative = _accepted_snapshot_relative_path(
            accepted_turn_id, snapshot_sha256
        )
        current_path = self._resolve_owned_path(current_relative)
        current_temporary_path = _same_directory_temporary_path(current_path).resolve()
        immutable_path = self._resolve_owned_path(immutable_relative)
        immutable_temporary_path = _same_directory_temporary_path(
            immutable_path
        ).resolve()
        lengths = self._validate_resolved_path_budget(
            current_path,
            current_temporary_path,
            immutable_path,
            immutable_temporary_path,
        )
        payload = {
            "schema_version": ContinuousAcceptedSnapshotPathPlanV1.SCHEMA_VERSION,
            "path_policy_sha256": CONTINUOUS_ACCEPTED_SNAPSHOT_PATH_POLICY_SHA256,
            "maximum_resolved_path_characters": (
                CONTINUOUS_ACCEPTED_SNAPSHOT_MAX_RESOLVED_CHARS
            ),
            "current_relative_path": current_relative,
            "current_temporary_relative_path": (
                _same_directory_temporary_path(Path(current_relative)).as_posix()
            ),
            "immutable_relative_path": immutable_relative,
            "immutable_temporary_relative_path": (
                _same_directory_temporary_path(Path(immutable_relative)).as_posix()
            ),
            "current_resolved_path_characters": lengths[0],
            "current_temporary_resolved_path_characters": lengths[1],
            "immutable_resolved_path_characters": lengths[2],
            "immutable_temporary_resolved_path_characters": lengths[3],
        }
        return ContinuousAcceptedSnapshotPathPlanV1(
            **payload,
            plan_sha256=canonical_sha256(payload),
        )

    def preflight_acceptance_capacity(
        self, *, accepted_turn_id: str
    ) -> ContinuousAcceptedSnapshotPathPlanV1:
        """Prove fixed-width acceptance paths before story or session mutation.

        The snapshot digest is not available until the accepted envelope has
        been journaled and injected.  Its locator contribution is fixed at 24
        hexadecimal characters, so a full-width sentinel proves the exact path
        lengths without pretending to identify the later immutable artifact.
        """

        return self.preflight_acceptance_paths(
            accepted_turn_id=accepted_turn_id,
            snapshot_sha256="0" * 64,
        )

    def _publish_current_snapshot(
        self,
        snapshot: ContinuousSessionSnapshotV1,
        *,
        path: Path,
        temporary: Path,
    ) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        encoded = canonical_bytes(self._snapshot_envelope(snapshot)) + b"\n"
        with temporary.open("wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        if self._failpoint is not None:
            self._failpoint("before_snapshot_replace")
        os.replace(temporary, path)
        if self._failpoint is not None:
            self._failpoint("after_snapshot_replace")
        return path

    def save(self, snapshot: ContinuousSessionSnapshotV1) -> Path:
        path = self.path_for(snapshot.compatibility.role).resolve()
        temporary = _same_directory_temporary_path(path).resolve()
        self._validate_resolved_path_budget(path, temporary)
        return self._publish_current_snapshot(
            snapshot, path=path, temporary=temporary
        )

    def save_for_acceptance(
        self,
        snapshot: ContinuousSessionSnapshotV1,
        *,
        accepted_turn_id: str,
        accepted_envelope_sha256: str,
        injection_receipt: ContinuousContextInjectionReceiptV1,
    ) -> ContinuousSessionSnapshotReceiptV2:
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
        snapshot_envelope = self._snapshot_envelope(snapshot)
        encoded_snapshot = canonical_bytes(snapshot_envelope) + b"\n"
        encoded_snapshot_file_sha256 = bytes_sha256(encoded_snapshot)
        path_plan = self.preflight_acceptance_paths(
            accepted_turn_id=accepted_turn_id,
            snapshot_sha256=snapshot.snapshot_sha256,
        )
        immutable_relative = path_plan.immutable_relative_path
        immutable_path = self._resolve_owned_path(immutable_relative)
        immutable_temporary = self._resolve_owned_path(
            path_plan.immutable_temporary_relative_path
        )
        current_path = self._resolve_owned_path(path_plan.current_relative_path)
        current_temporary = self._resolve_owned_path(
            path_plan.current_temporary_relative_path
        )
        envelope = {
            "schema_version": "cera.continuous_session_snapshot_envelope.v2",
            "path_policy_sha256": CONTINUOUS_ACCEPTED_SNAPSHOT_PATH_POLICY_SHA256,
            "accepted_turn_id": accepted_turn_id,
            "accepted_turn_id_sha256": text_sha256(accepted_turn_id),
            "accepted_envelope_sha256": accepted_envelope_sha256,
            "provider_thread_sha256": snapshot.handle.provider_thread_id_sha256,
            "injection_receipt": to_primitive(injection_receipt),
            "encoded_snapshot_file_sha256": encoded_snapshot_file_sha256,
            "snapshot_envelope": snapshot_envelope,
        }
        encoded = canonical_bytes(envelope) + b"\n"
        if immutable_path.is_symlink():
            raise StateConflictError("immutable accepted snapshot is a symlink")
        if current_path.is_symlink() or current_temporary.is_symlink():
            raise StateConflictError("continuous current snapshot path is a symlink")
        if current_temporary.exists():
            raise StateConflictError(
                "continuous current snapshot temporary path is occupied"
            )
        if immutable_path.exists():
            existing = immutable_path.read_bytes()
            if existing != encoded:
                try:
                    existing_envelope = json.loads(existing.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    existing_envelope = {}
                if (
                    existing_envelope.get("accepted_turn_id_sha256")
                    != text_sha256(accepted_turn_id)
                    or existing_envelope.get("snapshot_envelope", {}).get(
                        "snapshot_sha256"
                    )
                    != snapshot.snapshot_sha256
                ):
                    raise StateConflictError(
                        "continuous compact snapshot locator collision"
                    )
                raise StateConflictError("immutable accepted snapshot changed")
        else:
            if immutable_temporary.exists() or immutable_temporary.is_symlink():
                raise StateConflictError(
                    "continuous immutable snapshot temporary path is occupied"
                )
            immutable_path.parent.mkdir(parents=True, exist_ok=True)
            with immutable_temporary.open("wb") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(immutable_temporary, immutable_path)
        current_path = self._publish_current_snapshot(
            snapshot,
            path=current_path,
            temporary=current_temporary,
        )
        immutable_file_sha256 = bytes_sha256(encoded)
        payload = {
            "schema_version": ContinuousSessionSnapshotReceiptV2.SCHEMA_VERSION,
            "world_id": snapshot.compatibility.world_id,
            "branch_id": snapshot.compatibility.branch_id,
            "role": snapshot.compatibility.role,
            "accepted_turn_id": accepted_turn_id,
            "accepted_turn_id_sha256": text_sha256(accepted_turn_id),
            "accepted_envelope_sha256": accepted_envelope_sha256,
            "provider_thread_sha256": snapshot.handle.provider_thread_id_sha256,
            "snapshot_sha256": snapshot.snapshot_sha256,
            "injection_receipt": injection_receipt,
            "injection_operation_receipt_sha256": injection_receipt.operation_receipt_sha256,
            "encoded_snapshot_file_sha256": encoded_snapshot_file_sha256,
            "immutable_relative_path": immutable_relative,
            "immutable_file_sha256": immutable_file_sha256,
            "current_relative_path": current_path.relative_to(self.branch_root).as_posix(),
            "path_plan": path_plan,
        }
        primitive = {
            **payload,
            "role": snapshot.compatibility.role.value,
            "injection_receipt": to_primitive(injection_receipt),
            "path_plan": to_primitive(path_plan),
        }
        return ContinuousSessionSnapshotReceiptV2(
            **payload,
            receipt_sha256=canonical_sha256(primitive),
        )

    def load_immutable(
        self,
        receipt: ContinuousSessionSnapshotReceiptV1
        | ContinuousSessionSnapshotReceiptV2,
    ) -> ContinuousSessionSnapshotV1:
        path = (self.branch_root / receipt.immutable_relative_path).resolve()
        if self.branch_root not in path.parents or not path.is_file() or path.is_symlink():
            raise StateConflictError("immutable accepted snapshot bytes changed")
        encoded = path.read_bytes()
        if bytes_sha256(encoded) != receipt.immutable_file_sha256:
            raise StateConflictError("immutable accepted snapshot bytes changed")
        envelope = json.loads(encoded.decode("utf-8"))
        if isinstance(receipt, ContinuousSessionSnapshotReceiptV1):
            if (
                envelope.get("schema_version")
                != "cera.continuous_session_snapshot_envelope.v1"
            ):
                raise ContractValidationError("continuous snapshot envelope changed")
            snapshot_envelope = envelope
        else:
            if (
                envelope.get("schema_version")
                != "cera.continuous_session_snapshot_envelope.v2"
                or envelope.get("path_policy_sha256")
                != CONTINUOUS_ACCEPTED_SNAPSHOT_PATH_POLICY_SHA256
                or envelope.get("accepted_turn_id") != receipt.accepted_turn_id
                or envelope.get("accepted_turn_id_sha256")
                != receipt.accepted_turn_id_sha256
                or envelope.get("accepted_envelope_sha256")
                != receipt.accepted_envelope_sha256
                or envelope.get("provider_thread_sha256")
                != receipt.provider_thread_sha256
                or envelope.get("encoded_snapshot_file_sha256")
                != receipt.encoded_snapshot_file_sha256
            ):
                raise StateConflictError("immutable accepted snapshot authority changed")
            stored_injection = from_mapping(
                ContinuousContextInjectionReceiptV1,
                envelope.get("injection_receipt"),
            )
            if stored_injection != receipt.injection_receipt:
                raise StateConflictError("immutable accepted snapshot injection changed")
            snapshot_envelope = envelope.get("snapshot_envelope")
            if not isinstance(snapshot_envelope, Mapping):
                raise ContractValidationError(
                    "continuous encoded snapshot envelope is invalid"
                )
            encoded_snapshot = canonical_bytes(snapshot_envelope) + b"\n"
            if (
                bytes_sha256(encoded_snapshot)
                != receipt.encoded_snapshot_file_sha256
            ):
                raise StateConflictError("immutable encoded snapshot bytes changed")
            actual_paths = (
                self._resolve_owned_path(receipt.path_plan.current_relative_path),
                self._resolve_owned_path(
                    receipt.path_plan.current_temporary_relative_path
                ),
                path,
                self._resolve_owned_path(
                    receipt.path_plan.immutable_temporary_relative_path
                ),
            )
            if tuple(len(str(value)) for value in actual_paths) != (
                receipt.path_plan.current_resolved_path_characters,
                receipt.path_plan.current_temporary_resolved_path_characters,
                receipt.path_plan.immutable_resolved_path_characters,
                receipt.path_plan.immutable_temporary_resolved_path_characters,
            ):
                raise StateConflictError("immutable accepted snapshot root changed")
        if (
            snapshot_envelope.get("schema_version")
            != "cera.continuous_session_snapshot_envelope.v1"
        ):
            raise ContractValidationError("continuous encoded snapshot schema changed")
        snapshot = from_mapping(
            ContinuousSessionSnapshotV1, snapshot_envelope.get("snapshot")
        )
        if (
            snapshot_envelope.get("snapshot_sha256") != snapshot.snapshot_sha256
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

    def create(
        self,
        compatibility: ContinuousSessionCompatibilityV1,
        *,
        base_instructions: str = "",
    ) -> ContinuousSessionHandleV1: ...

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
        self.base_instructions: dict[str, str] = {}

    def create(
        self,
        compatibility: ContinuousSessionCompatibilityV1,
        *,
        base_instructions: str = "",
    ) -> ContinuousSessionHandleV1:
        self._counter += 1
        thread_id = f"fake-continuous-{compatibility.role.value}-{self._counter}"
        self._valid.add(thread_id)
        self._parents[thread_id] = None
        self.model_visible_context[thread_id] = []
        self.base_instructions[thread_id] = base_instructions
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
        child = self.create(
            compatibility,
            base_instructions=self.base_instructions.get(parent.provider_thread_id, ""),
        )
        self._parents[child.provider_thread_id] = parent.provider_thread_id
        self.model_visible_context[child.provider_thread_id] = list(
            self.model_visible_context.get(parent.provider_thread_id, ())
        )
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
    base_instructions: str = ""
    _events: list[ContinuousContextEventV1] = field(default_factory=list)
    _accepted_envelopes: dict[str, str] = field(default_factory=dict)
    _initialization_receipt: ContinuousSessionInitializationReceiptV1 | None = None
    _summary_deliveries: dict[
        tuple[str, str, int, str], CharacterSummaryDeliveryReceiptV1
    ] = field(default_factory=dict)
    _terminally_archived: bool = False
    _thread_lineage: ContinuousThreadLineageLedger | None = None
    _lineage_purpose: str | None = None
    _lineage_parent_provider_thread_sha256: str | None = None
    _lineage_creation_operation: str = "create"
    _lineage_registered: bool = False

    def attach_thread_lineage(
        self,
        ledger: ContinuousThreadLineageLedger,
        *,
        purpose: str,
        creation_operation: str | None = None,
        parent_provider_thread_sha256: str | None = None,
    ) -> None:
        if self._thread_lineage is not None and self._thread_lineage is not ledger:
            raise StateConflictError("continuous session changed thread-lineage owner")
        self._thread_lineage = ledger
        self._lineage_purpose = purpose
        self._lineage_creation_operation = (
            creation_operation
            if creation_operation is not None
            else "resume"
            if self.handle is not None
            else "create"
        )
        self._lineage_parent_provider_thread_sha256 = (
            parent_provider_thread_sha256
        )
        if self.handle is not None:
            self._register_thread_lineage()

    def _register_thread_lineage(self) -> None:
        if self._lineage_registered:
            return
        if self._thread_lineage is None:
            return
        if self.handle is None or self._lineage_purpose is None:
            raise StateConflictError("continuous thread lineage registration is incomplete")
        self._thread_lineage.register_thread(
            role=self.compatibility.role.value,
            purpose=self._lineage_purpose,
            world_id=self.compatibility.world_id,
            branch_id=self.compatibility.branch_id,
            session_compatibility_sha256=self.compatibility.compatibility_sha256,
            provider_thread_sha256=self.handle.provider_thread_id_sha256,
            parent_provider_thread_sha256=(
                self._lineage_parent_provider_thread_sha256
            ),
            creation_operation=self._lineage_creation_operation,
        )
        self._lineage_registered = True

    def install_base_instructions(self, value: str) -> None:
        if self.handle is not None or self._initialization_receipt is not None:
            if self.base_instructions != value:
                raise StateConflictError(
                    "continuous stored-thread base instructions changed"
                )
            return
        if not isinstance(value, str) or not value.strip():
            raise ContractValidationError(
                "continuous stored-thread base instructions are required"
            )
        self.base_instructions = value

    def ensure_session(self) -> ContinuousSessionHandleV1:
        if self._terminally_archived:
            raise StateConflictError("continuous provider session is terminally archived")
        if self.handle is None:
            self.handle = self.port.create(
                self.compatibility,
                base_instructions=self.base_instructions,
            )
            self._register_thread_lineage()
            self._initialization_receipt = self._build_initialization_receipt(
                context_mode=self.compatibility.default_context_mode,
                reconstruction_payload=None,
                accepted_tail_turn_ids=(),
                character_summary_envelope_sha256s=(),
                parent_provider_thread_sha256=None,
                branch_receipt_sha256=None,
            )
        else:
            if not self.port.resume(self.handle):
                raise StateConflictError("continuous provider session cannot be resumed")
            self._register_thread_lineage()
            if self._thread_lineage is not None:
                self._thread_lineage.record_resume(
                    self.handle.provider_thread_id_sha256
                )
        return self.handle

    def _build_initialization_receipt(
        self,
        *,
        context_mode: PlannerContextMode,
        reconstruction_payload: str | None,
        accepted_tail_turn_ids: tuple[str, ...],
        character_summary_envelope_sha256s: tuple[str, ...],
        parent_provider_thread_sha256: str | None,
        branch_receipt_sha256: str | None,
    ) -> ContinuousSessionInitializationReceiptV1:
        if self.handle is None:
            raise StateConflictError("continuous initialization lacks a thread")
        stable_bytes = len(self.base_instructions.encode("utf-8"))
        reconstruction_bytes = (
            len(reconstruction_payload.encode("utf-8"))
            if reconstruction_payload is not None
            else 0
        )
        payload = {
            "schema_version": ContinuousSessionInitializationReceiptV1.SCHEMA_VERSION,
            "world_id": self.compatibility.world_id,
            "branch_id": self.compatibility.branch_id,
            "role": self.compatibility.role,
            "context_mode": context_mode,
            "provider_session_id_sha256": text_sha256(
                self.handle.provider_session_id
            ),
            "provider_thread_sha256": self.handle.provider_thread_id_sha256,
            "parent_provider_thread_sha256": parent_provider_thread_sha256,
            "branch_receipt_sha256": branch_receipt_sha256,
            "stable_instruction_sha256": text_sha256(self.base_instructions),
            "stable_instruction_bytes": stable_bytes,
            "stable_instruction_estimated_tokens": (stable_bytes + 3) // 4,
            "reconstruction_payload_sha256": (
                text_sha256(reconstruction_payload)
                if reconstruction_payload is not None
                else None
            ),
            "reconstruction_bytes": reconstruction_bytes,
            "reconstruction_estimated_tokens": (reconstruction_bytes + 3) // 4,
            "accepted_tail_turn_ids": accepted_tail_turn_ids,
            "character_summary_envelope_sha256s": (
                character_summary_envelope_sha256s
            ),
        }
        primitive = {
            **payload,
            "role": self.compatibility.role.value,
            "context_mode": context_mode.value,
        }
        return ContinuousSessionInitializationReceiptV1(
            **payload,
            receipt_sha256=canonical_sha256(primitive),
        )

    @property
    def initialization_receipt(self) -> ContinuousSessionInitializationReceiptV1:
        self.ensure_session()
        assert self._initialization_receipt is not None
        return self._initialization_receipt

    def select_character_summaries(
        self,
        summaries: tuple[Any, ...],
        *,
        context_mode: PlannerContextMode,
        scene_change: bool = False,
        explicit_need_character_ids: tuple[str, ...] = (),
    ) -> tuple[tuple[Any, ...], tuple[str, ...]]:
        """Return Planner deliveries and deterministic reasons without mutating."""

        self.ensure_session()
        selected: list[Any] = []
        reasons: list[str] = []
        delivered_by_character = {
            receipt.character_id: receipt
            for receipt in self._summary_deliveries.values()
        }
        explicit = set(explicit_need_character_ids)
        for summary in summaries:
            prior = delivered_by_character.get(summary.character_id)
            selected_content_sha256 = canonical_sha256(
                {
                    "summary_field_path": summary.summary_field_path,
                    "latest_changes_field_path": summary.latest_changes_field_path,
                    "summary": summary.summary,
                    "latest_accepted_changes": summary.latest_accepted_changes,
                }
            )
            reason: str | None = None
            if context_mode is PlannerContextMode.RECONSTRUCTION:
                reason = "reconstruction"
            elif summary.character_id in explicit:
                reason = "planner_requested_exact_evidence"
            elif scene_change:
                reason = "scene_change"
            elif prior is None:
                reason = (
                    "first_relevant_appearance"
                    if not self._summary_deliveries
                    else "newly_relevant_character"
                )
            elif (
                prior.selected_content_sha256 != selected_content_sha256
            ):
                reason = "material_revision_change"
            if reason is not None:
                selected.append(summary)
                reasons.append(reason)
        return tuple(selected), tuple(reasons)

    def record_character_summary_deliveries(
        self,
        summaries: tuple[Any, ...],
        reasons: tuple[str, ...],
        *,
        planner_prompt_sha256: str,
    ) -> tuple[CharacterSummaryDeliveryReceiptV1, ...]:
        if len(summaries) != len(reasons):
            raise ContractValidationError(
                "character-summary delivery reasons are incomplete"
            )
        handle = self.ensure_session()
        receipts: list[CharacterSummaryDeliveryReceiptV1] = []
        for summary, reason in zip(summaries, reasons, strict=True):
            key = (
                summary.character_id,
                summary.source_path_or_record_id,
                summary.source_revision,
                summary.source_sha256,
            )
            if key in self._summary_deliveries:
                continue
            payload = {
                "schema_version": CharacterSummaryDeliveryReceiptV1.SCHEMA_VERSION,
                "character_id": summary.character_id,
                "source_path_or_record_id": summary.source_path_or_record_id,
                "source_revision": summary.source_revision,
                "source_sha256": summary.source_sha256,
                "envelope_sha256": summary.envelope_sha256,
                "selected_content_sha256": canonical_sha256(
                    {
                        "summary_field_path": summary.summary_field_path,
                        "latest_changes_field_path": summary.latest_changes_field_path,
                        "summary": summary.summary,
                        "latest_accepted_changes": summary.latest_accepted_changes,
                    }
                ),
                "provider_thread_sha256": handle.provider_thread_id_sha256,
                "delivery_reason": reason,
                "planner_prompt_sha256": planner_prompt_sha256,
            }
            receipt = CharacterSummaryDeliveryReceiptV1(
                **payload,
                receipt_sha256=canonical_sha256(payload),
            )
            self._summary_deliveries[key] = receipt
            receipts.append(receipt)
        return tuple(receipts)

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

        evidence = ContinuousThreadArchiveEvidenceV1(
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
        if self._thread_lineage is not None:
            self._thread_lineage.record_archive(evidence)
        return evidence

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
        self,
        envelope: AcceptedFinalSequenceEnvelopeV1,
        *,
        stable_reference_descriptors: tuple[dict[str, Any], ...] = (),
    ) -> bool:
        """Append one accepted envelope to the physical Planner thread.

        The accepted ledger entry must already exist.  Repeated calls after a
        completed synchronization are no-ops; a transport failure remains
        terminal and is never retried automatically.
        """

        return self.synchronize_accepted_final_sequence_with_receipt(
            envelope,
            stable_reference_descriptors=stable_reference_descriptors,
        ) is not None

    def synchronize_accepted_final_sequence_with_receipt(
        self,
        envelope: AcceptedFinalSequenceEnvelopeV1,
        *,
        stable_reference_descriptors: tuple[dict[str, Any], ...] = (),
    ) -> ContinuousContextInjectionReceiptV1 | None:
        if self.compatibility.role is not ContinuousSessionRole.PLANNER:
            raise StateConflictError("accepted-final synchronization belongs to Planner")
        if self._accepted_envelopes.get(envelope.accepted_turn_id) != envelope.envelope_sha256:
            raise StateConflictError("unknown accepted-final envelope cannot synchronize")
        if envelope.accepted_turn_id not in self.unsynchronized_accepted_turn_ids:
            return None
        handle = self.ensure_session()
        rendered = envelope.render_for_planner(
            stable_reference_descriptors=stable_reference_descriptors
        )
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
            initialization_receipt=self._initialization_receipt,
            character_summary_deliveries=tuple(self._summary_deliveries.values()),
        )

    def checkpoint(self, store: ContinuousSessionSnapshotStore) -> Path:
        return store.save(self.snapshot())

    @classmethod
    def resume_compatible(
        cls,
        snapshot: ContinuousSessionSnapshotV1,
        port: ContinuousStoredSessionPort,
        *,
        expected_compatibility: ContinuousSessionCompatibilityV1,
        base_instructions: str = "",
    ) -> "ContinuousSessionCoordinator":
        if (
            snapshot.compatibility.compatibility_sha256
            != expected_compatibility.compatibility_sha256
        ):
            raise StateConflictError(
                "continuous session snapshot is incompatible with the active contract"
            )
        if not port.resume(snapshot.handle):
            raise StateConflictError("continuous compatible resume lost provider thread")
        if (
            snapshot.initialization_receipt is not None
            and snapshot.initialization_receipt.stable_instruction_sha256
            != text_sha256(base_instructions)
        ):
            raise StateConflictError(
                "continuous compatible resume changed stable instructions"
            )
        coordinator = cls(
            compatibility=expected_compatibility,
            port=port,
            handle=snapshot.handle,
            base_instructions=base_instructions,
        )
        coordinator._events.extend(snapshot.context_events)
        coordinator._accepted_envelopes.update(
            {
                event.turn_or_scene_id: event.payload_sha256
                for event in snapshot.context_events
                if event.event_type == "accepted_final_sequence"
            }
        )
        coordinator._initialization_receipt = snapshot.initialization_receipt
        coordinator._summary_deliveries.update(
            {
                (
                    receipt.character_id,
                    receipt.source_path_or_record_id,
                    receipt.source_revision,
                    receipt.source_sha256,
                ): receipt
                for receipt in snapshot.character_summary_deliveries
            }
        )
        return coordinator

    @classmethod
    def reconstruct_new_thread(
        cls,
        *,
        port: ContinuousStoredSessionPort,
        expected_compatibility: ContinuousSessionCompatibilityV1,
        base_instructions: str,
        bundle: ContinuousSessionReconstructionBundleV1,
        parent_provider_thread_sha256: str | None = None,
        branch_receipt_sha256: str | None = None,
        thread_lineage: ContinuousThreadLineageLedger | None = None,
        lifecycle_failpoint: Callable[[str], None] | None = None,
    ) -> "ContinuousSessionCoordinator":
        """Create a new physical thread from bounded Python-owned authority."""

        if (
            PlannerContextMode.RECONSTRUCTION
            not in expected_compatibility.allowed_context_modes
        ):
            raise StateConflictError("continuous reconstruction mode is not allowed")
        if (
            bundle.world_id != expected_compatibility.world_id
            or bundle.branch_id != expected_compatibility.branch_id
        ):
            raise StateConflictError("continuous reconstruction changed world or branch")
        reconstruction_payload = bundle.render_for_planner()
        accepted_tail_turn_ids = tuple(
            value.envelope.accepted_turn_id for value in bundle.accepted_tail
        )
        coordinator = cls(
            compatibility=expected_compatibility,
            port=port,
            base_instructions=base_instructions,
        )
        coordinator.handle = port.create(
            expected_compatibility,
            base_instructions=base_instructions,
        )
        try:
            if thread_lineage is not None:
                coordinator.attach_thread_lineage(
                    thread_lineage,
                    purpose="planner_reconstruction",
                    creation_operation="reconstruct",
                    parent_provider_thread_sha256=parent_provider_thread_sha256,
                )
            if lifecycle_failpoint is not None:
                lifecycle_failpoint("after_physical_reconstruction_creation")
            coordinator._populate_reconstructed_thread(
                bundle=bundle,
                reconstruction_payload=reconstruction_payload,
                accepted_tail_turn_ids=accepted_tail_turn_ids,
                parent_provider_thread_sha256=parent_provider_thread_sha256,
                branch_receipt_sha256=branch_receipt_sha256,
            )
            if lifecycle_failpoint is not None:
                lifecycle_failpoint("after_reconstruction_summary_delivery")
        except BaseException as exc:
            if thread_lineage is not None:
                thread_lineage.record_abandoned(
                    coordinator.handle.provider_thread_id_sha256,
                    reason_sha256=text_sha256("reconstruction_setup_failed"),
                )
            evidence = coordinator.archive_and_verify_terminal(
                "reconstruction_setup_failed"
            )
            if not evidence.verified:
                raise StateConflictError(
                    "reconstruction failed and new-thread archival was not verified"
                ) from exc
            raise
        return coordinator

    def _populate_reconstructed_thread(
        self,
        *,
        bundle: ContinuousSessionReconstructionBundleV1,
        reconstruction_payload: str,
        accepted_tail_turn_ids: tuple[str, ...],
        parent_provider_thread_sha256: str | None,
        branch_receipt_sha256: str | None,
    ) -> None:
        if self.handle is None:
            raise StateConflictError("reconstruction thread is unavailable")
        self.port.append_context(self.handle, reconstruction_payload)
        for accepted in bundle.accepted_tail:
            turn_id = accepted.envelope.accepted_turn_id
            envelope_hash = accepted.envelope.envelope_sha256
            self._accepted_envelopes[turn_id] = envelope_hash
            self._events.extend(
                (
                    ContinuousContextEventV1(
                        event_type="accepted_final_sequence",
                        turn_or_scene_id=turn_id,
                        payload_sha256=envelope_hash,
                    ),
                    ContinuousContextEventV1(
                        event_type="accepted_final_sequence_synchronized",
                        turn_or_scene_id=turn_id,
                        payload_sha256=envelope_hash,
                    ),
                )
            )
        self._initialization_receipt = (
            self._build_initialization_receipt(
                context_mode=PlannerContextMode.RECONSTRUCTION,
                reconstruction_payload=reconstruction_payload,
                accepted_tail_turn_ids=accepted_tail_turn_ids,
                character_summary_envelope_sha256s=(
                    tuple(
                        value.envelope_sha256
                        for value in bundle.character_summaries
                    )
                ),
                parent_provider_thread_sha256=parent_provider_thread_sha256,
                branch_receipt_sha256=branch_receipt_sha256,
            )
        )
        reconstruction_prompt_sha256 = text_sha256(reconstruction_payload)
        for summary in bundle.character_summaries:
            payload = {
                "schema_version": CharacterSummaryDeliveryReceiptV1.SCHEMA_VERSION,
                "character_id": summary.character_id,
                "source_path_or_record_id": summary.source_path_or_record_id,
                "source_revision": summary.source_revision,
                "source_sha256": summary.source_sha256,
                "envelope_sha256": summary.envelope_sha256,
                "selected_content_sha256": canonical_sha256(
                    {
                        "summary_field_path": summary.summary_field_path,
                        "latest_changes_field_path": summary.latest_changes_field_path,
                        "summary": summary.summary,
                        "latest_accepted_changes": summary.latest_accepted_changes,
                    }
                ),
                "provider_thread_sha256": self.handle.provider_thread_id_sha256,
                "delivery_reason": "reconstruction",
                "planner_prompt_sha256": reconstruction_prompt_sha256,
            }
            delivery = CharacterSummaryDeliveryReceiptV1(
                **payload,
                receipt_sha256=canonical_sha256(payload),
            )
            self._summary_deliveries[
                (
                    summary.character_id,
                    summary.source_path_or_record_id,
                    summary.source_revision,
                    summary.source_sha256,
                )
            ] = delivery

    def build_branch_fork_receipt(
        self,
        compatibility: ContinuousSessionCompatibilityV1,
        *,
        branch_materialization_receipt_sha256: str,
    ) -> ContinuousBranchForkReceiptV2:
        """Issue the sole valid Python-owned receipt for the current checkpoint."""

        if (
            compatibility.role is not self.compatibility.role
            or compatibility.world_id != self.compatibility.world_id
            or compatibility.branch_id == self.compatibility.branch_id
        ):
            raise StateConflictError("continuous branch-fork target scope is invalid")
        if not re_is_sha256(branch_materialization_receipt_sha256):
            raise ContractValidationError(
                "continuous branch-fork materialization receipt is invalid"
            )
        parent = self.ensure_session()
        accepted_turn_ids = tuple(self._accepted_envelopes)
        if not accepted_turn_ids:
            raise StateConflictError(
                "continuous branch-fork receipt requires an accepted checkpoint"
            )
        accepted_ancestry_sha256 = canonical_sha256(
            {
                "accepted_turn_ids": accepted_turn_ids,
                "accepted_envelopes": tuple(
                    (turn_id, self._accepted_envelopes[turn_id])
                    for turn_id in accepted_turn_ids
                ),
            }
        )
        payload = {
            "schema_version": ContinuousBranchForkReceiptV2.SCHEMA_VERSION,
            "world_id": self.compatibility.world_id,
            "parent_branch_id": self.compatibility.branch_id,
            "child_branch_id": compatibility.branch_id,
            "accepted_checkpoint_turn_id": accepted_turn_ids[-1],
            "accepted_ancestry_sha256": accepted_ancestry_sha256,
            "parent_provider_thread_sha256": parent.provider_thread_id_sha256,
            "privacy_boundary_sha256": continuous_branch_privacy_boundary_sha256(
                parent_compatibility=self.compatibility,
                child_compatibility=compatibility,
                accepted_checkpoint_turn_id=accepted_turn_ids[-1],
                accepted_ancestry_sha256=accepted_ancestry_sha256,
                parent_provider_thread_sha256=parent.provider_thread_id_sha256,
            ),
            "branch_materialization_receipt_sha256": (
                branch_materialization_receipt_sha256
            ),
        }
        return ContinuousBranchForkReceiptV2(
            **payload,
            receipt_sha256=canonical_sha256(payload),
        )

    def fork_for_branch(
        self,
        compatibility: ContinuousSessionCompatibilityV1,
        *,
        branch_receipt: ContinuousBranchForkReceiptV2,
        inherited_summary_envelope_sha256s: tuple[str, ...] = (),
        lifecycle_failpoint: Callable[[str], None] | None = None,
    ) -> "ContinuousSessionCoordinator":
        if compatibility.role is not self.compatibility.role:
            raise StateConflictError("continuous branch fork changed session role")
        if compatibility.world_id != self.compatibility.world_id:
            raise StateConflictError("continuous branch fork changed world")
        parent = self.ensure_session()
        accepted_turn_ids = tuple(self._accepted_envelopes)
        if not accepted_turn_ids:
            raise StateConflictError(
                "continuous provider fork requires an accepted checkpoint"
            )
        expected_ancestry = canonical_sha256(
            {
                "accepted_turn_ids": accepted_turn_ids,
                "accepted_envelopes": tuple(
                    (turn_id, self._accepted_envelopes[turn_id])
                    for turn_id in accepted_turn_ids
                ),
            }
        )
        expected_privacy_boundary = continuous_branch_privacy_boundary_sha256(
            parent_compatibility=self.compatibility,
            child_compatibility=compatibility,
            accepted_checkpoint_turn_id=accepted_turn_ids[-1],
            accepted_ancestry_sha256=expected_ancestry,
            parent_provider_thread_sha256=parent.provider_thread_id_sha256,
        )
        if (
            branch_receipt.world_id != compatibility.world_id
            or branch_receipt.parent_branch_id != self.compatibility.branch_id
            or branch_receipt.child_branch_id != compatibility.branch_id
            or branch_receipt.accepted_checkpoint_turn_id != accepted_turn_ids[-1]
            or branch_receipt.accepted_ancestry_sha256 != expected_ancestry
            or branch_receipt.parent_provider_thread_sha256
            != parent.provider_thread_id_sha256
            or branch_receipt.privacy_boundary_sha256
            != expected_privacy_boundary
            or not re_is_sha256(
                branch_receipt.branch_materialization_receipt_sha256
            )
        ):
            raise StateConflictError(
                "continuous branch-fork receipt changed accepted ancestry"
            )
        if (
            len(inherited_summary_envelope_sha256s)
            != len(set(inherited_summary_envelope_sha256s))
            or any(
                not re_is_sha256(value)
                for value in inherited_summary_envelope_sha256s
            )
            or set(inherited_summary_envelope_sha256s)
            - {
                value.envelope_sha256
                for value in self._summary_deliveries.values()
            }
        ):
            raise StateConflictError(
                "continuous branch fork summary inheritance is invalid"
            )
        child = self.port.fork_branch(parent, compatibility)
        coordinator = ContinuousSessionCoordinator(
            compatibility=compatibility,
            port=self.port,
            handle=child,
            base_instructions=self.base_instructions,
        )
        try:
            if self._thread_lineage is not None:
                coordinator.attach_thread_lineage(
                    self._thread_lineage,
                    purpose="accepted_checkpoint_fork_child",
                    creation_operation="fork",
                    parent_provider_thread_sha256=parent.provider_thread_id_sha256,
                )
            if lifecycle_failpoint is not None:
                lifecycle_failpoint("after_physical_fork_creation")
            self._populate_forked_coordinator(
                coordinator=coordinator,
                child=child,
                parent=parent,
                accepted_turn_ids=accepted_turn_ids,
                inherited_summary_envelope_sha256s=(
                    inherited_summary_envelope_sha256s
                ),
                branch_receipt=branch_receipt,
            )
            if lifecycle_failpoint is not None:
                lifecycle_failpoint("after_summary_delivery_reconstruction")
        except BaseException as exc:
            if self._thread_lineage is not None:
                self._thread_lineage.record_abandoned(
                    child.provider_thread_id_sha256,
                    reason_sha256=text_sha256("fork_setup_failed"),
                )
            evidence = coordinator.archive_and_verify_terminal(
                "fork_setup_failed"
            )
            if not evidence.verified:
                raise StateConflictError(
                    "fork setup failed and child archival was not verified"
                ) from exc
            raise
        return coordinator

    def _populate_forked_coordinator(
        self,
        *,
        coordinator: "ContinuousSessionCoordinator",
        child: ContinuousSessionHandleV1,
        parent: ContinuousSessionHandleV1,
        accepted_turn_ids: tuple[str, ...],
        inherited_summary_envelope_sha256s: tuple[str, ...],
        branch_receipt: ContinuousBranchForkReceiptV2,
    ) -> None:
        coordinator._events.extend(
            event
            for event in self._events
            if event.event_type
            in {"accepted_final_sequence", "accepted_final_sequence_synchronized"}
        )
        coordinator._accepted_envelopes.update(self._accepted_envelopes)
        for key, prior in self._summary_deliveries.items():
            if prior.envelope_sha256 not in inherited_summary_envelope_sha256s:
                continue
            payload = {
                "schema_version": CharacterSummaryDeliveryReceiptV1.SCHEMA_VERSION,
                "character_id": prior.character_id,
                "source_path_or_record_id": prior.source_path_or_record_id,
                "source_revision": prior.source_revision,
                "source_sha256": prior.source_sha256,
                "envelope_sha256": prior.envelope_sha256,
                "selected_content_sha256": prior.selected_content_sha256,
                "provider_thread_sha256": child.provider_thread_id_sha256,
                "delivery_reason": "accepted_checkpoint_fork",
                "planner_prompt_sha256": prior.planner_prompt_sha256,
            }
            coordinator._summary_deliveries[key] = CharacterSummaryDeliveryReceiptV1(
                **payload,
                receipt_sha256=canonical_sha256(payload),
            )
        coordinator._initialization_receipt = coordinator._build_initialization_receipt(
            context_mode=PlannerContextMode.LEAN_CONTINUOUS,
            reconstruction_payload=None,
            accepted_tail_turn_ids=accepted_turn_ids,
            character_summary_envelope_sha256s=tuple(
                value.envelope_sha256
                for value in coordinator._summary_deliveries.values()
            ),
            parent_provider_thread_sha256=parent.provider_thread_id_sha256,
            branch_receipt_sha256=branch_receipt.receipt_sha256,
        )

    def establish_branch_reference_rebinding(
        self,
        envelope: AcceptedFinalSequenceEnvelopeV1,
        *,
        branch_receipt: ContinuousBranchForkReceiptV2,
        parent_reference_keys: tuple[str, ...],
        child_reference_descriptors: tuple[dict[str, Any], ...],
    ) -> ContinuousBranchReferenceTransferReceiptV1:
        """Append one value-free parent-to-child key transfer to a forked thread."""

        if self.compatibility.role is not ContinuousSessionRole.PLANNER:
            raise StateConflictError("branch-reference transfer belongs to Planner")
        initialization = self.initialization_receipt
        if (
            initialization.branch_receipt_sha256 != branch_receipt.receipt_sha256
            or initialization.parent_provider_thread_sha256
            != branch_receipt.parent_provider_thread_sha256
            or branch_receipt.child_branch_id != self.compatibility.branch_id
            or self._accepted_envelopes.get(envelope.accepted_turn_id)
            != envelope.envelope_sha256
        ):
            raise StateConflictError(
                "branch-reference transfer changed fork or accepted checkpoint"
            )
        child_keys = tuple(
            str(value.get("reference_key", ""))
            for value in child_reference_descriptors
        )
        if (
            len(parent_reference_keys) != len(child_reference_descriptors)
            or len(parent_reference_keys) != len(set(parent_reference_keys))
            or len(child_keys) != len(set(child_keys))
            or any(
                not value.startswith("binding_accepted_ref_")
                for value in (*parent_reference_keys, *child_keys)
            )
            or set(parent_reference_keys).intersection(child_keys)
        ):
            raise StateConflictError("branch-reference transfer mapping is invalid")
        if any(
            value.event_type == "branch_reference_rebinding"
            and value.turn_or_scene_id == envelope.accepted_turn_id
            for value in self._events
        ):
            raise StateConflictError(
                "branch-reference transfer was already established"
            )
        descriptor_set_sha256 = canonical_sha256(child_reference_descriptors)
        rendered = "[BRANCH ACCEPTED REFERENCE REBINDING]\n" + canonical_bytes(
            {
                "schema_version": "cera.branch_reference_rebinding_context.v1",
                "world_id": self.compatibility.world_id,
                "child_branch_id": self.compatibility.branch_id,
                "accepted_turn_id": envelope.accepted_turn_id,
                "accepted_envelope_sha256": envelope.envelope_sha256,
                "branch_receipt_sha256": branch_receipt.receipt_sha256,
                "parent_reference_keys_revoked": parent_reference_keys,
                "child_reference_descriptors": child_reference_descriptors,
            }
        ).decode("utf-8")
        handle = self.ensure_session()
        self.port.append_context(handle, rendered)
        payload = {
            "schema_version": ContinuousBranchReferenceTransferReceiptV1.SCHEMA_VERSION,
            "world_id": self.compatibility.world_id,
            "child_branch_id": self.compatibility.branch_id,
            "accepted_turn_id": envelope.accepted_turn_id,
            "accepted_envelope_sha256": envelope.envelope_sha256,
            "child_provider_thread_sha256": handle.provider_thread_id_sha256,
            "branch_receipt_sha256": branch_receipt.receipt_sha256,
            "parent_reference_keys": parent_reference_keys,
            "child_reference_keys": child_keys,
            "child_descriptor_set_sha256": descriptor_set_sha256,
            "injected_context_sha256": text_sha256(rendered),
        }
        receipt = ContinuousBranchReferenceTransferReceiptV1(
            **payload,
            operation_receipt_sha256=canonical_sha256(payload),
        )
        self._events.append(
            ContinuousContextEventV1(
                event_type="branch_reference_rebinding",
                turn_or_scene_id=envelope.accepted_turn_id,
                payload_sha256=receipt.operation_receipt_sha256,
            )
        )
        return receipt


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
