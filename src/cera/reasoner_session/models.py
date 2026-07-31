"""Provider-neutral, branch-bound Reasoner session contracts.

These records describe context custody and lifecycle only.  They never make
provider conversation, rejected prose, or hidden reasoning authoritative.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar

from cera.errors import ContractValidationError
from cera.ids import (
    AUTHORITY_RECORD_ID_KINDS,
    IdKind,
    TypedId,
    require_kind,
)
from cera.schema import require_schema
from cera.serialization import domain_sha256, re_is_sha256, text_sha256


def _nonempty(value: str, field: str, *, maximum: int = 256) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ContractValidationError(
            f"{field} must be 1..{maximum} non-whitespace characters"
        )


def _hash(value: str, field: str) -> None:
    if not re_is_sha256(value):
        raise ContractValidationError(f"{field} must be lowercase SHA-256")


def _optional_nonnegative(value: int | None, field: str) -> None:
    if value is not None and (type(value) is not int or value < 0):
        raise ContractValidationError(f"{field} must be null or non-negative")


class SessionRole(StrEnum):
    SCENE_REASONER = "scene_reasoner"
    REALIZATION_VERIFIER = "realization_verifier"


class SessionStatus(StrEnum):
    ACTIVE = "active"
    ROTATED = "rotated"
    INVALIDATED = "invalidated"


class CheckpointStatus(StrEnum):
    ACCEPTED = "accepted"
    CANDIDATE = "candidate"
    REJECTED = "rejected"
    INVALIDATED = "invalidated"


class SessionTurnMode(StrEnum):
    APPEND = "append"
    REGENERATE = "regenerate"


class EvidenceDelivery(StrEnum):
    EXACT_INLINE = "exact_inline"
    MATERIALIZED_REFERENCE = "materialized_reference"


class ConstraintScope(StrEnum):
    BRANCH = "branch"
    GLOBAL = "global"


class ConstraintStatus(StrEnum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"


class CompactionState(StrEnum):
    NONE = "none"
    PROVIDER_REPORTED = "provider_reported"
    RECONSTRUCTED = "reconstructed"
    UNKNOWN = "unknown"


class ProviderThreadStorageMode(StrEnum):
    """How the provider conversation is materialized.

    CERA's branch/session implementation requires a stored rollout.  An
    ephemeral provider thread cannot satisfy restart or exact-checkpoint fork
    semantics and is therefore intentionally absent from this enum.
    """

    STORED_LOCAL = "stored_local"


class ProviderThreadRole(StrEnum):
    ROOT = "root"
    CANDIDATE = "candidate"
    BRANCH_ROOT = "branch_root"
    RECONSTRUCTED_ROOT = "reconstructed_root"


class ProviderThreadCustodyEventKind(StrEnum):
    ALLOCATED = "allocated"
    RESUMED = "resumed"
    ACCEPTED = "accepted"
    REJECTED_DELETED = "rejected_deleted"
    FAILED_DELETED = "failed_deleted"
    REJECTED_ARCHIVED = "rejected_archived"
    FAILED_ARCHIVED = "failed_archived"
    ARCHIVED = "archived"
    MISSING = "missing"


@dataclass(frozen=True, slots=True)
class ProviderThreadCustodyDescriptor:
    """Privacy-safe description returned by a session adapter.

    Raw provider identifiers remain in :class:`ProviderSessionHandle` for
    resume/fork operations.  This descriptor is safe to persist in audit
    evidence because it contains only hashes and lifecycle semantics.
    """

    SCHEMA_VERSION: ClassVar[str] = "cera.provider_thread_custody_descriptor.v1"

    schema_version: str
    provider_thread_id_sha256: str
    parent_provider_thread_id_sha256: str | None
    storage_mode: ProviderThreadStorageMode
    role: ProviderThreadRole
    raw_context_retained: bool
    provider_context_is_story_authority: bool

    def __post_init__(self) -> None:
        require_schema(self.schema_version, self.SCHEMA_VERSION, type(self).__name__)
        _hash(self.provider_thread_id_sha256, "provider_thread_id_sha256")
        if self.parent_provider_thread_id_sha256 is not None:
            _hash(
                self.parent_provider_thread_id_sha256,
                "parent_provider_thread_id_sha256",
            )
        if self.storage_mode is not ProviderThreadStorageMode.STORED_LOCAL:
            raise ContractValidationError("reasoner session thread must be stored")
        if self.provider_context_is_story_authority:
            raise ContractValidationError("provider context cannot be story authority")


@dataclass(frozen=True, slots=True)
class ProviderThreadCustodyEvent:
    """Append-only lifecycle evidence for one provider checkpoint thread."""

    SCHEMA_VERSION: ClassVar[str] = "cera.provider_thread_custody_event.v1"

    schema_version: str
    event_id: TypedId
    session_id: TypedId
    checkpoint_id: TypedId
    event_kind: ProviderThreadCustodyEventKind
    descriptor: ProviderThreadCustodyDescriptor
    reason_code: str
    created_at: str

    def __post_init__(self) -> None:
        require_schema(self.schema_version, self.SCHEMA_VERSION, type(self).__name__)
        require_kind(self.event_id, IdKind.SESSION_RECEIPT, "event_id")
        require_kind(self.session_id, IdKind.SESSION, "session_id")
        require_kind(self.checkpoint_id, IdKind.CHECKPOINT, "checkpoint_id")
        _nonempty(self.reason_code, "reason_code", maximum=128)
        _nonempty(self.created_at, "created_at", maximum=64)
        retained_events = {
            ProviderThreadCustodyEventKind.ALLOCATED,
            ProviderThreadCustodyEventKind.RESUMED,
            ProviderThreadCustodyEventKind.ACCEPTED,
            ProviderThreadCustodyEventKind.REJECTED_ARCHIVED,
            ProviderThreadCustodyEventKind.FAILED_ARCHIVED,
            ProviderThreadCustodyEventKind.ARCHIVED,
        }
        if self.descriptor.raw_context_retained != (self.event_kind in retained_events):
            raise ContractValidationError(
                "provider custody event retention does not match its lifecycle state"
            )

    @property
    def event_sha256(self) -> str:
        return domain_sha256(self.SCHEMA_VERSION, self)


@dataclass(frozen=True, slots=True)
class ReasonerSessionCompatibility:
    """Immutable route and authority identity for compatible context reuse."""

    SCHEMA_VERSION: ClassVar[str] = "cera.reasoner_session_compatibility.v1"

    schema_version: str
    world_id: TypedId
    branch_id: TypedId
    role: SessionRole
    provider: str
    model: str
    reasoning_effort: str
    service_tier: str
    transport_version: str
    adapter_version: str
    prompt_version: str
    provider_schema_sha256: str
    base_instruction_sha256: str
    tool_contract_version: str
    genesis_revision_id: TypedId
    authority_policy_version: str
    privacy_projection_version: str
    protected_user_id: TypedId
    autonomy_profile_version: str

    def __post_init__(self) -> None:
        require_schema(self.schema_version, self.SCHEMA_VERSION, type(self).__name__)
        require_kind(self.world_id, IdKind.WORLD, "world_id")
        require_kind(self.branch_id, IdKind.BRANCH, "branch_id")
        require_kind(
            self.genesis_revision_id, IdKind.GENESIS_REVISION, "genesis_revision_id"
        )
        require_kind(self.protected_user_id, IdKind.CHARACTER, "protected_user_id")
        for field, value in (
            ("provider", self.provider),
            ("model", self.model),
            ("reasoning_effort", self.reasoning_effort),
            ("service_tier", self.service_tier),
            ("transport_version", self.transport_version),
            ("adapter_version", self.adapter_version),
            ("prompt_version", self.prompt_version),
            ("tool_contract_version", self.tool_contract_version),
            ("authority_policy_version", self.authority_policy_version),
            ("privacy_projection_version", self.privacy_projection_version),
            ("autonomy_profile_version", self.autonomy_profile_version),
        ):
            _nonempty(value, field)
        _hash(self.provider_schema_sha256, "provider_schema_sha256")
        _hash(self.base_instruction_sha256, "base_instruction_sha256")

    @property
    def compatibility_sha256(self) -> str:
        return domain_sha256(self.SCHEMA_VERSION, self)


@dataclass(frozen=True, slots=True)
class ProviderSessionHandle:
    """Opaque provider handle.  Receipts expose only its hash."""

    SCHEMA_VERSION: ClassVar[str] = "cera.provider_session_handle.v1"

    schema_version: str
    provider_session_id: str
    provider_thread_id: str
    provider_turn_id: str | None

    def __post_init__(self) -> None:
        require_schema(self.schema_version, self.SCHEMA_VERSION, type(self).__name__)
        _nonempty(self.provider_session_id, "provider_session_id", maximum=512)
        _nonempty(self.provider_thread_id, "provider_thread_id", maximum=512)
        if self.provider_turn_id is not None:
            _nonempty(self.provider_turn_id, "provider_turn_id", maximum=512)

    @property
    def handle_sha256(self) -> str:
        return domain_sha256(self.SCHEMA_VERSION, self)


@dataclass(frozen=True, slots=True)
class ContextEvidenceBinding:
    record_id: TypedId
    version: int
    payload_sha256: str
    delivery: EvidenceDelivery
    exact_section_ids: tuple[str, ...]
    materialized_checkpoint_id: TypedId | None = None

    def __post_init__(self) -> None:
        if self.record_id.kind not in AUTHORITY_RECORD_ID_KINDS:
            raise ContractValidationError("evidence binding requires an authority record ID")
        if type(self.version) is not int or self.version < 1:
            raise ContractValidationError("evidence version must be positive")
        _hash(self.payload_sha256, "evidence payload_sha256")
        if len(self.exact_section_ids) != len(set(self.exact_section_ids)) or any(
            not value.strip() for value in self.exact_section_ids
        ):
            raise ContractValidationError("evidence exact section IDs are invalid")
        if self.delivery is EvidenceDelivery.MATERIALIZED_REFERENCE:
            if self.materialized_checkpoint_id is None:
                raise ContractValidationError(
                    "materialized evidence reference requires its checkpoint"
                )
            require_kind(
                self.materialized_checkpoint_id,
                IdKind.CHECKPOINT,
                "materialized_checkpoint_id",
            )
        elif self.materialized_checkpoint_id is not None:
            raise ContractValidationError(
                "inline evidence cannot claim an earlier materialization checkpoint"
            )


@dataclass(frozen=True, slots=True)
class ConstraintBinding:
    constraint_id: TypedId
    record_sha256: str

    def __post_init__(self) -> None:
        require_kind(
            self.constraint_id, IdKind.CREATOR_CONSTRAINT, "constraint_id"
        )
        _hash(self.record_sha256, "constraint record_sha256")


@dataclass(frozen=True, slots=True)
class SessionReconstructionBundle:
    """Python-authorized state used to create or rotate a provider session."""

    SCHEMA_VERSION: ClassVar[str] = "cera.session_reconstruction_bundle.v1"

    schema_version: str
    bundle_id: TypedId
    compatibility_sha256: str
    world_id: TypedId
    branch_id: TypedId
    accepted_head_artifact_id: TypedId | None
    generation: int
    authority_revision: int
    evidence_snapshot_token: TypedId
    evidence: tuple[ContextEvidenceBinding, ...]
    active_constraints: tuple[ConstraintBinding, ...]
    accepted_receipt_ids: tuple[TypedId, ...]
    authoritative_context_sha256: str
    created_at: str

    def __post_init__(self) -> None:
        require_schema(self.schema_version, self.SCHEMA_VERSION, type(self).__name__)
        require_kind(self.bundle_id, IdKind.CONTEXT_DELTA, "bundle_id")
        require_kind(self.world_id, IdKind.WORLD, "world_id")
        require_kind(self.branch_id, IdKind.BRANCH, "branch_id")
        require_kind(self.evidence_snapshot_token, IdKind.SNAPSHOT, "evidence_snapshot_token")
        if self.accepted_head_artifact_id is not None:
            require_kind(
                self.accepted_head_artifact_id,
                IdKind.ARTIFACT,
                "accepted_head_artifact_id",
            )
        if self.generation < 0 or self.authority_revision < 0:
            raise ContractValidationError("reconstruction generation/revision is invalid")
        _hash(self.compatibility_sha256, "compatibility_sha256")
        _hash(self.authoritative_context_sha256, "authoritative_context_sha256")
        evidence_ids = [str(value.record_id) for value in self.evidence]
        constraint_ids = [str(value.constraint_id) for value in self.active_constraints]
        receipt_ids = [str(value) for value in self.accepted_receipt_ids]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ContractValidationError("reconstruction evidence is duplicated")
        if len(constraint_ids) != len(set(constraint_ids)):
            raise ContractValidationError("reconstruction constraints are duplicated")
        if len(receipt_ids) != len(set(receipt_ids)):
            raise ContractValidationError("reconstruction receipts are duplicated")
        for receipt_id in self.accepted_receipt_ids:
            require_kind(receipt_id, IdKind.SESSION_RECEIPT, "accepted_receipt_ids")
        _nonempty(self.created_at, "created_at", maximum=64)

    @property
    def bundle_sha256(self) -> str:
        return domain_sha256(self.SCHEMA_VERSION, self)


@dataclass(frozen=True, slots=True)
class ContextAuthorityDelta:
    """Current-turn authority declaration; older conversation cannot override it."""

    SCHEMA_VERSION: ClassVar[str] = "cera.context_authority_delta.v1"

    schema_version: str
    delta_id: TypedId
    session_id: TypedId
    parent_checkpoint_id: TypedId
    world_id: TypedId
    branch_id: TypedId
    request_id: TypedId
    turn_mode: SessionTurnMode
    replaces_artifact_id: TypedId | None
    generation: int
    accepted_head_artifact_id: TypedId | None
    authority_revision: int
    evidence_snapshot_token: TypedId
    current_evidence: tuple[ContextEvidenceBinding, ...]
    revoked_evidence_ids: tuple[TypedId, ...]
    active_constraints: tuple[ConstraintBinding, ...]
    source_sha256: str
    turn_packet_sha256: str
    created_at: str

    def __post_init__(self) -> None:
        require_schema(self.schema_version, self.SCHEMA_VERSION, type(self).__name__)
        require_kind(self.delta_id, IdKind.CONTEXT_DELTA, "delta_id")
        require_kind(self.session_id, IdKind.SESSION, "session_id")
        require_kind(self.parent_checkpoint_id, IdKind.CHECKPOINT, "parent_checkpoint_id")
        require_kind(self.world_id, IdKind.WORLD, "world_id")
        require_kind(self.branch_id, IdKind.BRANCH, "branch_id")
        require_kind(self.request_id, IdKind.REQUEST, "request_id")
        require_kind(self.evidence_snapshot_token, IdKind.SNAPSHOT, "evidence_snapshot_token")
        if self.accepted_head_artifact_id is not None:
            require_kind(
                self.accepted_head_artifact_id,
                IdKind.ARTIFACT,
                "accepted_head_artifact_id",
            )
        if self.turn_mode is SessionTurnMode.APPEND:
            if self.replaces_artifact_id is not None:
                raise ContractValidationError("append context cannot replace an artifact")
        else:
            if self.replaces_artifact_id is None:
                raise ContractValidationError("regeneration context requires replaced artifact")
            require_kind(
                self.replaces_artifact_id,
                IdKind.ARTIFACT,
                "replaces_artifact_id",
            )
            if self.replaces_artifact_id != self.accepted_head_artifact_id:
                raise ContractValidationError(
                    "regeneration must replace the current accepted branch head"
                )
        if self.generation < 0 or self.authority_revision < 0:
            raise ContractValidationError("context generation/revision is invalid")
        _hash(self.source_sha256, "source_sha256")
        _hash(self.turn_packet_sha256, "turn_packet_sha256")
        current = [str(value.record_id) for value in self.current_evidence]
        revoked = [str(value) for value in self.revoked_evidence_ids]
        constraints = [str(value.constraint_id) for value in self.active_constraints]
        if len(current) != len(set(current)):
            raise ContractValidationError("current evidence is duplicated")
        if len(revoked) != len(set(revoked)):
            raise ContractValidationError("revoked evidence is duplicated")
        if set(current) & set(revoked):
            raise ContractValidationError("evidence cannot be current and revoked")
        if len(constraints) != len(set(constraints)):
            raise ContractValidationError("active constraints are duplicated")
        for value in self.revoked_evidence_ids:
            if value.kind not in AUTHORITY_RECORD_ID_KINDS:
                raise ContractValidationError(
                    "revoked evidence requires authority-record IDs"
                )
        _nonempty(self.created_at, "created_at", maximum=64)

    @property
    def delta_sha256(self) -> str:
        return domain_sha256(self.SCHEMA_VERSION, self)


@dataclass(frozen=True, slots=True)
class ReasonerSessionCheckpoint:
    SCHEMA_VERSION: ClassVar[str] = "cera.reasoner_session_checkpoint.v1"

    schema_version: str
    checkpoint_id: TypedId
    session_id: TypedId
    parent_checkpoint_id: TypedId | None
    branch_id: TypedId
    request_id: TypedId | None
    review_id: TypedId | None
    turn_mode: SessionTurnMode | None
    replaces_artifact_id: TypedId | None
    provider_handle: ProviderSessionHandle
    status: CheckpointStatus
    accepted_head_artifact_id: TypedId | None
    generation: int
    authority_revision: int
    context_manifest_sha256: str
    accepted_receipt_id: TypedId | None
    rejection_receipt_id: TypedId | None
    status_reason: str | None
    created_at: str
    updated_at: str

    def __post_init__(self) -> None:
        require_schema(self.schema_version, self.SCHEMA_VERSION, type(self).__name__)
        require_kind(self.checkpoint_id, IdKind.CHECKPOINT, "checkpoint_id")
        require_kind(self.session_id, IdKind.SESSION, "session_id")
        require_kind(self.branch_id, IdKind.BRANCH, "branch_id")
        if self.parent_checkpoint_id is not None:
            require_kind(
                self.parent_checkpoint_id, IdKind.CHECKPOINT, "parent_checkpoint_id"
            )
        if self.request_id is not None:
            require_kind(self.request_id, IdKind.REQUEST, "request_id")
        if self.review_id is not None:
            require_kind(self.review_id, IdKind.REVIEW_PACKET, "review_id")
        if self.replaces_artifact_id is not None:
            require_kind(
                self.replaces_artifact_id,
                IdKind.ARTIFACT,
                "replaces_artifact_id",
            )
        if self.accepted_head_artifact_id is not None:
            require_kind(
                self.accepted_head_artifact_id,
                IdKind.ARTIFACT,
                "accepted_head_artifact_id",
            )
        if self.accepted_receipt_id is not None:
            require_kind(
                self.accepted_receipt_id,
                IdKind.SESSION_RECEIPT,
                "accepted_receipt_id",
            )
        if self.rejection_receipt_id is not None:
            require_kind(
                self.rejection_receipt_id,
                IdKind.REJECTION,
                "rejection_receipt_id",
            )
        if self.generation < 0 or self.authority_revision < 0:
            raise ContractValidationError("checkpoint generation/revision is invalid")
        _hash(self.context_manifest_sha256, "context_manifest_sha256")
        for value, field in ((self.created_at, "created_at"), (self.updated_at, "updated_at")):
            _nonempty(value, field, maximum=64)
        if self.parent_checkpoint_id is None:
            if (
                self.status is not CheckpointStatus.ACCEPTED
                or self.request_id is not None
                or self.turn_mode is not None
                or self.replaces_artifact_id is not None
            ):
                raise ContractValidationError(
                    "a root checkpoint must be accepted and request-free"
                )
        elif self.request_id is None:
            raise ContractValidationError("a non-root checkpoint requires request_id")
        elif self.turn_mode is None:
            raise ContractValidationError("a non-root checkpoint requires turn_mode")
        if self.turn_mode is SessionTurnMode.APPEND and self.replaces_artifact_id is not None:
            raise ContractValidationError("append checkpoint cannot replace an artifact")
        if self.turn_mode is SessionTurnMode.REGENERATE and self.replaces_artifact_id is None:
            raise ContractValidationError("regeneration checkpoint requires replaced artifact")
        if self.status is CheckpointStatus.CANDIDATE:
            if self.accepted_receipt_id is not None or self.rejection_receipt_id is not None:
                raise ContractValidationError("candidate checkpoint cannot carry a receipt")
        elif self.status is CheckpointStatus.ACCEPTED:
            if self.rejection_receipt_id is not None:
                raise ContractValidationError("accepted checkpoint cannot carry rejection")
            if self.parent_checkpoint_id is not None and self.accepted_receipt_id is None:
                raise ContractValidationError("promoted checkpoint requires accepted receipt")
        elif self.status is CheckpointStatus.REJECTED:
            if self.rejection_receipt_id is None or self.accepted_receipt_id is not None:
                raise ContractValidationError("rejected checkpoint requires only rejection receipt")
        elif self.accepted_receipt_id is not None or self.rejection_receipt_id is not None:
            raise ContractValidationError("invalidated checkpoint cannot carry promotion receipts")
        if self.status_reason is not None:
            _nonempty(self.status_reason, "status_reason", maximum=512)

    @property
    def checkpoint_sha256(self) -> str:
        return domain_sha256(self.SCHEMA_VERSION, self)


@dataclass(frozen=True, slots=True)
class ReasonerSessionLedger:
    SCHEMA_VERSION: ClassVar[str] = "cera.reasoner_session_ledger.v1"

    schema_version: str
    session_id: TypedId
    compatibility: ReasonerSessionCompatibility
    status: SessionStatus
    accepted_checkpoint_id: TypedId
    provider_root_handle: ProviderSessionHandle
    accumulated_turns: int
    rotated_from_session_id: TypedId | None
    status_reason: str | None
    created_at: str
    updated_at: str

    def __post_init__(self) -> None:
        require_schema(self.schema_version, self.SCHEMA_VERSION, type(self).__name__)
        require_kind(self.session_id, IdKind.SESSION, "session_id")
        require_kind(
            self.accepted_checkpoint_id, IdKind.CHECKPOINT, "accepted_checkpoint_id"
        )
        if self.rotated_from_session_id is not None:
            require_kind(
                self.rotated_from_session_id,
                IdKind.SESSION,
                "rotated_from_session_id",
            )
            if self.rotated_from_session_id == self.session_id:
                raise ContractValidationError("session cannot rotate from itself")
        if self.accumulated_turns < 0:
            raise ContractValidationError("accumulated_turns cannot be negative")
        if self.status is SessionStatus.ACTIVE and self.status_reason is not None:
            raise ContractValidationError("active session cannot carry terminal reason")
        if self.status is not SessionStatus.ACTIVE and self.status_reason is None:
            raise ContractValidationError("terminal session requires status_reason")
        if self.status_reason is not None:
            _nonempty(self.status_reason, "status_reason", maximum=512)
        for value, field in ((self.created_at, "created_at"), (self.updated_at, "updated_at")):
            _nonempty(value, field, maximum=64)

    @property
    def ledger_sha256(self) -> str:
        return domain_sha256(self.SCHEMA_VERSION, self)


@dataclass(frozen=True, slots=True)
class CreatorConstraintRecord:
    """Explicit creator feedback used as control, never story truth."""

    SCHEMA_VERSION: ClassVar[str] = "cera.creator_constraint_record.v1"

    schema_version: str
    constraint_id: TypedId
    scope: ConstraintScope
    world_id: TypedId
    branch_id: TypedId | None
    source_review_id: TypedId
    source_diagnostic_id: TypedId
    source_candidate_sha256: str
    owner: str
    creator_feedback: str
    creator_feedback_sha256: str
    normalized_instruction: str
    normalized_instruction_sha256: str
    status: ConstraintStatus
    superseded_by_constraint_id: TypedId | None
    story_authority: bool
    character_knowledge: bool
    created_at: str
    updated_at: str

    def __post_init__(self) -> None:
        require_schema(self.schema_version, self.SCHEMA_VERSION, type(self).__name__)
        require_kind(self.constraint_id, IdKind.CREATOR_CONSTRAINT, "constraint_id")
        require_kind(self.world_id, IdKind.WORLD, "world_id")
        require_kind(self.source_review_id, IdKind.REVIEW_PACKET, "source_review_id")
        require_kind(
            self.source_diagnostic_id, IdKind.REVIEW_FINDING, "source_diagnostic_id"
        )
        if self.scope is ConstraintScope.BRANCH:
            if self.branch_id is None:
                raise ContractValidationError("branch constraint requires branch_id")
            require_kind(self.branch_id, IdKind.BRANCH, "branch_id")
        elif self.branch_id is not None:
            raise ContractValidationError("global constraint cannot bind one branch")
        if self.superseded_by_constraint_id is not None:
            require_kind(
                self.superseded_by_constraint_id,
                IdKind.CREATOR_CONSTRAINT,
                "superseded_by_constraint_id",
            )
            if self.superseded_by_constraint_id == self.constraint_id:
                raise ContractValidationError("constraint cannot supersede itself")
        _hash(self.source_candidate_sha256, "source_candidate_sha256")
        _nonempty(self.owner, "owner", maximum=96)
        if (
            not self.creator_feedback.strip()
            or len(self.creator_feedback) > 8_000
            or text_sha256(self.creator_feedback) != self.creator_feedback_sha256
        ):
            raise ContractValidationError("creator constraint feedback is invalid")
        if (
            not self.normalized_instruction.strip()
            or len(self.normalized_instruction) > 8_000
            or text_sha256(self.normalized_instruction)
            != self.normalized_instruction_sha256
        ):
            raise ContractValidationError("creator normalized instruction is invalid")
        if self.story_authority or self.character_knowledge:
            raise ContractValidationError(
                "creator constraint cannot be story authority or character knowledge"
            )
        if self.status is ConstraintStatus.ACTIVE:
            if self.superseded_by_constraint_id is not None:
                raise ContractValidationError("active constraint cannot be superseded")
        elif self.superseded_by_constraint_id is None:
            raise ContractValidationError("superseded constraint requires its successor")
        for value, field in ((self.created_at, "created_at"), (self.updated_at, "updated_at")):
            _nonempty(value, field, maximum=64)

    @property
    def record_sha256(self) -> str:
        return domain_sha256(self.SCHEMA_VERSION, self)


@dataclass(frozen=True, slots=True)
class AcceptedTurnReceipt:
    SCHEMA_VERSION: ClassVar[str] = "cera.accepted_turn_session_receipt.v1"

    schema_version: str
    receipt_id: TypedId
    session_id: TypedId
    checkpoint_id: TypedId
    review_id: TypedId
    request_id: TypedId
    branch_id: TypedId
    artifact_id: TypedId
    replaces_artifact_id: TypedId | None
    generation_before: int
    generation_after: int
    authority_revision_after: int
    accepted_prose_sha256: str
    accepted_event_state_sha256: str
    commit_sha256: str
    provider_calls: int
    automatic_retries: int
    created_at: str

    def __post_init__(self) -> None:
        require_schema(self.schema_version, self.SCHEMA_VERSION, type(self).__name__)
        require_kind(self.receipt_id, IdKind.SESSION_RECEIPT, "receipt_id")
        require_kind(self.session_id, IdKind.SESSION, "session_id")
        require_kind(self.checkpoint_id, IdKind.CHECKPOINT, "checkpoint_id")
        require_kind(self.review_id, IdKind.REVIEW_PACKET, "review_id")
        require_kind(self.request_id, IdKind.REQUEST, "request_id")
        require_kind(self.branch_id, IdKind.BRANCH, "branch_id")
        require_kind(self.artifact_id, IdKind.ARTIFACT, "artifact_id")
        if self.replaces_artifact_id is not None:
            require_kind(
                self.replaces_artifact_id,
                IdKind.ARTIFACT,
                "replaces_artifact_id",
            )
        if self.generation_before < 0 or self.generation_after != self.generation_before + 1:
            raise ContractValidationError("accepted receipt generation transition is invalid")
        if self.authority_revision_after < 0:
            raise ContractValidationError("accepted receipt authority revision is invalid")
        for value, field in (
            (self.accepted_prose_sha256, "accepted_prose_sha256"),
            (self.accepted_event_state_sha256, "accepted_event_state_sha256"),
            (self.commit_sha256, "commit_sha256"),
        ):
            _hash(value, field)
        if self.provider_calls != 0 or self.automatic_retries != 0:
            raise ContractValidationError(
                "accepted receipt injection cannot call providers or retry"
            )
        _nonempty(self.created_at, "created_at", maximum=64)

    @property
    def receipt_sha256(self) -> str:
        return domain_sha256(self.SCHEMA_VERSION, self)


@dataclass(frozen=True, slots=True)
class RejectedCandidateReceipt:
    SCHEMA_VERSION: ClassVar[str] = "cera.rejected_candidate_session_receipt.v1"

    schema_version: str
    receipt_id: TypedId
    session_id: TypedId
    checkpoint_id: TypedId
    parent_accepted_checkpoint_id: TypedId
    review_id: TypedId
    request_id: TypedId
    branch_id: TypedId
    candidate_sha256: str
    replaces_artifact_id: TypedId | None
    reason_codes: tuple[str, ...]
    constraint_ids: tuple[TypedId, ...]
    raw_candidate_retained: bool
    created_at: str

    def __post_init__(self) -> None:
        require_schema(self.schema_version, self.SCHEMA_VERSION, type(self).__name__)
        require_kind(self.receipt_id, IdKind.REJECTION, "receipt_id")
        require_kind(self.session_id, IdKind.SESSION, "session_id")
        require_kind(self.checkpoint_id, IdKind.CHECKPOINT, "checkpoint_id")
        require_kind(
            self.parent_accepted_checkpoint_id,
            IdKind.CHECKPOINT,
            "parent_accepted_checkpoint_id",
        )
        require_kind(self.review_id, IdKind.REVIEW_PACKET, "review_id")
        require_kind(self.request_id, IdKind.REQUEST, "request_id")
        require_kind(self.branch_id, IdKind.BRANCH, "branch_id")
        _hash(self.candidate_sha256, "candidate_sha256")
        if self.replaces_artifact_id is not None:
            require_kind(
                self.replaces_artifact_id,
                IdKind.ARTIFACT,
                "replaces_artifact_id",
            )
        if len(self.reason_codes) != len(set(self.reason_codes)) or any(
            not value.strip() or len(value) > 96 for value in self.reason_codes
        ):
            raise ContractValidationError("rejection reason codes are invalid")
        rendered = [str(value) for value in self.constraint_ids]
        if len(rendered) != len(set(rendered)):
            raise ContractValidationError("rejection constraints are duplicated")
        for value in self.constraint_ids:
            require_kind(value, IdKind.CREATOR_CONSTRAINT, "constraint_ids")
        if self.raw_candidate_retained:
            raise ContractValidationError(
                "v1 rejected-candidate receipts intentionally retain no raw prose"
            )
        _nonempty(self.created_at, "created_at", maximum=64)

    @property
    def receipt_sha256(self) -> str:
        return domain_sha256(self.SCHEMA_VERSION, self)


@dataclass(frozen=True, slots=True)
class SessionUsageReceiptV2:
    """Privacy-safe usage and lifecycle observability with explicit unknowns."""

    SCHEMA_VERSION: ClassVar[str] = "cera.reasoner_session_usage_receipt.v2"

    schema_version: str
    receipt_id: TypedId
    session_id: TypedId
    checkpoint_id: TypedId
    request_id: TypedId | None
    branch_id: TypedId
    provider: str
    model: str
    reasoning_effort: str
    compatibility_sha256: str
    prompt_version: str
    provider_schema_sha256: str
    base_instruction_sha256: str
    provider_thread_id_sha256: str
    wall_microseconds: int | None
    queue_microseconds: int | None
    provider_microseconds: int | None
    ttft_microseconds: int | None
    input_tokens: int | None
    cached_input_tokens: int | None
    cache_write_tokens: int | None
    uncached_input_tokens: int | None
    output_tokens: int | None
    reasoning_tokens: int | None
    prompt_bytes: int
    packet_bytes: int
    schema_bytes: int
    tool_definition_bytes: int
    session_age_turns: int
    accumulated_turns: int
    compaction_state: CompactionState
    unsupported_fields: tuple[str, ...]
    raw_prompt_retained: bool
    raw_output_retained: bool
    created_at: str

    def __post_init__(self) -> None:
        require_schema(self.schema_version, self.SCHEMA_VERSION, type(self).__name__)
        require_kind(self.receipt_id, IdKind.TELEMETRY_EVENT, "receipt_id")
        require_kind(self.session_id, IdKind.SESSION, "session_id")
        require_kind(self.checkpoint_id, IdKind.CHECKPOINT, "checkpoint_id")
        require_kind(self.branch_id, IdKind.BRANCH, "branch_id")
        if self.request_id is not None:
            require_kind(self.request_id, IdKind.REQUEST, "request_id")
        for value, field in (
            (self.provider, "provider"),
            (self.model, "model"),
            (self.reasoning_effort, "reasoning_effort"),
            (self.prompt_version, "prompt_version"),
        ):
            _nonempty(value, field)
        for value, field in (
            (self.compatibility_sha256, "compatibility_sha256"),
            (self.provider_schema_sha256, "provider_schema_sha256"),
            (self.base_instruction_sha256, "base_instruction_sha256"),
            (self.provider_thread_id_sha256, "provider_thread_id_sha256"),
        ):
            _hash(value, field)
        for field, value in (
            ("wall_microseconds", self.wall_microseconds),
            ("queue_microseconds", self.queue_microseconds),
            ("provider_microseconds", self.provider_microseconds),
            ("ttft_microseconds", self.ttft_microseconds),
            ("input_tokens", self.input_tokens),
            ("cached_input_tokens", self.cached_input_tokens),
            ("cache_write_tokens", self.cache_write_tokens),
            ("uncached_input_tokens", self.uncached_input_tokens),
            ("output_tokens", self.output_tokens),
            ("reasoning_tokens", self.reasoning_tokens),
        ):
            _optional_nonnegative(value, field)
        for field, value in (
            ("prompt_bytes", self.prompt_bytes),
            ("packet_bytes", self.packet_bytes),
            ("schema_bytes", self.schema_bytes),
            ("tool_definition_bytes", self.tool_definition_bytes),
            ("session_age_turns", self.session_age_turns),
            ("accumulated_turns", self.accumulated_turns),
        ):
            if type(value) is not int or value < 0:
                raise ContractValidationError(f"{field} must be non-negative")
        if self.input_tokens is not None and self.cached_input_tokens is not None:
            if self.cached_input_tokens > self.input_tokens:
                raise ContractValidationError("cached input exceeds total input")
            expected = self.input_tokens - self.cached_input_tokens
            if self.uncached_input_tokens is not None and self.uncached_input_tokens != expected:
                raise ContractValidationError("uncached input accounting is inconsistent")
        if len(self.unsupported_fields) != len(set(self.unsupported_fields)) or any(
            not value.strip() for value in self.unsupported_fields
        ):
            raise ContractValidationError("unsupported usage fields are invalid")
        if self.raw_prompt_retained or self.raw_output_retained:
            raise ContractValidationError("privacy-safe usage receipt cannot retain content")
        _nonempty(self.created_at, "created_at", maximum=64)

    @property
    def receipt_sha256(self) -> str:
        return domain_sha256(self.SCHEMA_VERSION, self)
