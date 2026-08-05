"""Request-local authoritative evidence bindings for continuous planning."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
import json
import os
from pathlib import Path
import re
from typing import Any, ClassVar, Iterable

from cera.errors import ContractValidationError, StateConflictError
from cera.serialization import (
    canonical_bytes,
    canonical_sha256,
    domain_sha256,
    re_is_sha256,
    text_sha256,
    to_primitive,
)

from .contracts import (
    AcceptedFinalSequenceEnvelopeV1,
    CharacterRoleLedgerV1,
    CharacterSummaryEnvelopeV1,
    DiagnosticGroundingStatus,
    DiagnosticProtectedSemanticAdjudicationV1,
    DiagnosticStorySegmentV1,
    FinalInformationVisibility,
    FinalSequenceItemV1,
    IngressSourceUnitKind,
    IngressSourceUnitV1,
    PersistenceRecordClass,
    ProtectedUserAllowanceMode,
    ProtectedSemanticAdjudicationV1,
    ProtectedSemanticRelationKind,
    PresentationRealizationClass,
    PresentationRealizationSegmentV1,
    ProtectedUserRealizationSpanV1,
    ProtectedUserSourceClaimKind,
    ProtectedUserSourceClaimV1,
    RichPlannerSequenceV1,
    SourceGroundedPublicStateReceiptV1,
    StoryRealizationKind,
    StoryRealizationSegmentV1,
    ValidatorFinalizationPackageV1,
)
from .record_policy import validate_persistence_field_path
from .path_policy import preflight_windows_legacy_paths
from .path_custody import (
    capture_target_custody,
    ensure_parent_chain,
    inspect_leaf,
    lexical_absolute,
    lexical_target,
    locked_directory_chain,
    normalized_relative_path,
    safe_create_new_bytes,
    safe_read_bytes,
    safe_replace,
    unlink_if_identity,
    verify_target_custody,
)


def _read_branch_file_no_follow(
    branch_root: Path, relative_path: str
) -> tuple[Path, str]:
    root = lexical_absolute(branch_root)
    normalized = normalized_relative_path(relative_path)
    encoded, _identity = safe_read_bytes(root, normalized)
    try:
        text = encoded.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ContractValidationError(
            "continuous branch evidence is not UTF-8"
        ) from exc
    return lexical_target(root, normalized), text


def _validate_reference_only_roles(
    *,
    roles: CharacterRoleLedgerV1,
    allowed_character_ids: tuple[str, ...],
    reference_only_character_ids: tuple[str, ...],
    error_prefix: str,
) -> None:
    allowed = set(allowed_character_ids)
    allowed.add("character:ted")
    reference_only = set(reference_only_character_ids)
    if reference_only & allowed:
        raise PermissionError(
            f"{error_prefix} reference-only cast overlaps active cast"
        )
    if set(roles.involved_ids) - allowed - reference_only:
        raise PermissionError(
            f"{error_prefix} introduced an inactive character role"
        )
    non_reference_fields = (
        "action_owner_ids",
        "state_owner_ids",
        "speaker_ids",
        "affected_ids",
        "addressed_ids",
        "observing_ids",
    )
    if any(
        character_id in getattr(roles, field)
        for character_id in reference_only
        for field in non_reference_fields
    ):
        raise PermissionError(
            f"{error_prefix} activated a reference-only character"
        )


@dataclass(frozen=True, slots=True)
class AcceptedSessionFactV1:
    SCHEMA_VERSION: ClassVar[str] = "cera.accepted_session_fact.v2"

    fact_key: str
    source_item_key: str
    field_name: str
    value: str
    visibility: FinalInformationVisibility
    knowledge_owner_id: str | None
    roles: CharacterRoleLedgerV1
    protected_user_source_claim_keys: tuple[str, ...]
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("accepted-session fact schema changed")
        if not self.fact_key.startswith("accepted_fact_"):
            raise ContractValidationError("accepted-session fact key is invalid")
        if not self.source_item_key or not self.field_name or not self.value.strip():
            raise ContractValidationError("accepted-session fact is incomplete")
        if self.visibility is FinalInformationVisibility.PUBLIC:
            if self.knowledge_owner_id is not None:
                raise ContractValidationError("public accepted fact has a private owner")
        elif self.knowledge_owner_id is None:
            raise ContractValidationError("private accepted fact lacks its owner")
        if (
            self.knowledge_owner_id is not None
            and self.knowledge_owner_id
            not in set(self.roles.involved_ids)
        ):
            raise ContractValidationError(
                "private accepted fact owner has no declared character role"
            )


@dataclass(frozen=True, slots=True)
class AcceptedSessionProjectionV1:
    """Exact accepted current-scene context visible to one audience."""

    SCHEMA_VERSION: ClassVar[str] = "cera.accepted_session_projection.v5"

    schema_version: str
    projection_key: str
    world_id: str
    branch_id: str
    request_turn_id: str
    accepted_turn_id: str
    scene_id: str
    knowledge_owner_id: str | None
    facts: tuple[AcceptedSessionFactV1, ...]
    accepted_pair_sha256: str
    accepted_event_sha256: str
    accepted_envelope_sha256: str
    acceptance_receipt_sha256: str
    provider_thread_sha256: str
    session_snapshot_sha256: str
    synchronization_receipt_sha256: str

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("accepted-session projection schema changed")
        if not all(
            isinstance(value, str) and value.strip()
            for value in (
                self.projection_key,
                self.world_id,
                self.branch_id,
                self.request_turn_id,
                self.accepted_turn_id,
                self.scene_id,
            )
        ):
            raise ContractValidationError("accepted-session projection identity is incomplete")
        if not self.projection_key.startswith("projection_session_"):
            raise ContractValidationError("accepted-session projection key is invalid")
        hashes = (
            self.accepted_pair_sha256,
            self.accepted_event_sha256,
            self.accepted_envelope_sha256,
            self.acceptance_receipt_sha256,
            self.provider_thread_sha256,
            self.session_snapshot_sha256,
            self.synchronization_receipt_sha256,
        )
        if any(not re_is_sha256(value) for value in hashes):
            raise ContractValidationError("accepted-session projection hash is invalid")
        if not self.facts:
            raise ContractValidationError("accepted-session projection is empty")
        for fact in self.facts:
            owner = fact.knowledge_owner_id
            if self.knowledge_owner_id is None and owner is not None:
                raise ContractValidationError("public accepted projection contains private state")
            if self.knowledge_owner_id is not None and owner not in {
                None,
                self.knowledge_owner_id,
            }:
                raise ContractValidationError("owner accepted projection contains another owner's state")
        if self.knowledge_owner_id is not None and not any(
            fact.knowledge_owner_id == self.knowledge_owner_id
            for fact in self.facts
        ):
            raise ContractValidationError(
                "owner accepted projection has no fact owned by that character"
            )

    @property
    def projection_sha256(self) -> str:
        return domain_sha256(self.SCHEMA_VERSION, self)


@dataclass(frozen=True, slots=True)
class StableAcceptedContextReferenceV1:
    """Python-owned accepted fact address; prompt use never needs its payload."""

    SCHEMA_VERSION: ClassVar[str] = "cera.stable_accepted_context_reference.v1"

    schema_version: str
    reference_key: str
    world_id: str
    branch_id: str
    planner_session_id_sha256: str
    provider_thread_sha256: str
    accepted_turn_id: str
    scene_id: str
    accepted_ancestry_sha256: str
    accepted_envelope_sha256: str
    accepted_pair_sha256: str
    accepted_event_sha256: str
    acceptance_receipt_sha256: str
    injection_receipt_sha256: str
    session_snapshot_sha256: str
    synchronization_receipt_sha256: str
    source_item_key: str
    field_name: str
    field_value: str
    field_value_sha256: str
    visibility: FinalInformationVisibility
    knowledge_owner_id: str | None
    roles: CharacterRoleLedgerV1
    protected_user_source_claim_keys: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("stable accepted-reference schema changed")
        if not self.reference_key.startswith("binding_accepted_ref_"):
            raise ContractValidationError("stable accepted-reference key is invalid")
        if not all(
            isinstance(value, str) and value.strip()
            for value in (
                self.world_id,
                self.branch_id,
                self.accepted_turn_id,
                self.scene_id,
                self.source_item_key,
                self.field_name,
                self.field_value,
            )
        ):
            raise ContractValidationError("stable accepted-reference is incomplete")
        for value in (
            self.planner_session_id_sha256,
            self.provider_thread_sha256,
            self.accepted_ancestry_sha256,
            self.accepted_envelope_sha256,
            self.accepted_pair_sha256,
            self.accepted_event_sha256,
            self.acceptance_receipt_sha256,
            self.injection_receipt_sha256,
            self.session_snapshot_sha256,
            self.synchronization_receipt_sha256,
            self.field_value_sha256,
        ):
            if not re_is_sha256(value):
                raise ContractValidationError("stable accepted-reference hash is invalid")
        if self.field_value_sha256 != text_sha256(self.field_value):
            raise ContractValidationError("stable accepted-reference value changed")
        if self.visibility is FinalInformationVisibility.PUBLIC:
            if self.knowledge_owner_id is not None:
                raise ContractValidationError(
                    "public stable accepted-reference has a private owner"
                )
        elif (
            self.knowledge_owner_id is None
            or self.knowledge_owner_id not in set(self.roles.involved_ids)
        ):
            raise ContractValidationError(
                "private stable accepted-reference owner is invalid"
            )

    @property
    def reference_sha256(self) -> str:
        return domain_sha256(self.SCHEMA_VERSION, self)

    def injection_descriptor(self) -> dict[str, Any]:
        """Map the accepted sequence field to a key without duplicating its value."""

        return {
            "reference_key": self.reference_key,
            "accepted_turn_id": self.accepted_turn_id,
            "source_item_key": self.source_item_key,
            "field_name": self.field_name,
            "visibility": self.visibility.value,
            "knowledge_owner_id": self.knowledge_owner_id,
            "roles": to_primitive(self.roles),
        }


@dataclass(frozen=True, slots=True)
class ValidatorCitedAcceptedEvidenceV1:
    """Minimal exact-value closure for one Planner-cited stable reference."""

    SCHEMA_VERSION: ClassVar[str] = "cera.validator_cited_accepted_evidence.v1"

    schema_version: str
    request_turn_id: str
    binding_key: str
    binding_kind: str
    authority_classification: str
    cited_by_beat_keys: tuple[str, ...]
    accepted_reference: StableAcceptedContextReferenceV1
    closure_sha256: str

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError(
                "Validator cited accepted-evidence schema changed"
            )
        if not self.request_turn_id.strip():
            raise ContractValidationError(
                "Validator cited accepted-evidence request is incomplete"
            )
        if (
            self.binding_key != self.accepted_reference.reference_key
            or self.binding_kind != EvidenceBindingKind.ACCEPTED_SESSION_ENVELOPE.value
            or self.authority_classification
            != EvidenceAuthorityClass.ACCEPTED_SESSION_AUTHORITY.value
            or not self.cited_by_beat_keys
            or len(self.cited_by_beat_keys) != len(set(self.cited_by_beat_keys))
        ):
            raise ContractValidationError(
                "Validator cited accepted-evidence authority changed"
            )
        expected = canonical_sha256(
            {
                "schema_version": self.SCHEMA_VERSION,
                "request_turn_id": self.request_turn_id,
                "binding_key": self.binding_key,
                "binding_kind": self.binding_kind,
                "authority_classification": self.authority_classification,
                "cited_by_beat_keys": self.cited_by_beat_keys,
                "accepted_reference": to_primitive(self.accepted_reference),
            }
        )
        if self.closure_sha256 != expected:
            raise ContractValidationError(
                "Validator cited accepted-evidence closure changed"
            )


@dataclass(frozen=True, slots=True)
class CompactAcceptedHeadReceiptV1:
    """Lean prompt receipt: exact custody and keys, never prior fact payloads."""

    SCHEMA_VERSION: ClassVar[str] = "cera.compact_accepted_head_receipt.v1"

    schema_version: str
    world_id: str
    branch_id: str
    scene_id: str
    accepted_turn_id: str
    planner_session_id_sha256: str
    provider_thread_sha256: str
    accepted_ancestry_sha256: str
    accepted_envelope_sha256: str
    accepted_pair_sha256: str
    accepted_event_sha256: str
    acceptance_receipt_sha256: str
    injection_receipt_sha256: str
    session_snapshot_sha256: str
    synchronization_receipt_sha256: str
    stable_reference_keys: tuple[str, ...]
    receipt_sha256: str

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("compact accepted-head receipt schema changed")
        if not all(
            isinstance(value, str) and value.strip()
            for value in (
                self.world_id,
                self.branch_id,
                self.scene_id,
                self.accepted_turn_id,
            )
        ):
            raise ContractValidationError("compact accepted-head scope is incomplete")
        if not self.stable_reference_keys or len(self.stable_reference_keys) != len(
            set(self.stable_reference_keys)
        ):
            raise ContractValidationError("compact accepted-head keys are invalid")
        if any(
            not value.startswith("binding_accepted_ref_")
            for value in self.stable_reference_keys
        ):
            raise ContractValidationError("compact accepted-head key changed")
        for value in (
            self.planner_session_id_sha256,
            self.provider_thread_sha256,
            self.accepted_ancestry_sha256,
            self.accepted_envelope_sha256,
            self.accepted_pair_sha256,
            self.accepted_event_sha256,
            self.acceptance_receipt_sha256,
            self.injection_receipt_sha256,
            self.session_snapshot_sha256,
            self.synchronization_receipt_sha256,
            self.receipt_sha256,
        ):
            if not re_is_sha256(value):
                raise ContractValidationError("compact accepted-head hash is invalid")
        expected = canonical_sha256(
            {
                "schema_version": self.SCHEMA_VERSION,
                "world_id": self.world_id,
                "branch_id": self.branch_id,
                "scene_id": self.scene_id,
                "accepted_turn_id": self.accepted_turn_id,
                "planner_session_id_sha256": self.planner_session_id_sha256,
                "provider_thread_sha256": self.provider_thread_sha256,
                "accepted_ancestry_sha256": self.accepted_ancestry_sha256,
                "accepted_envelope_sha256": self.accepted_envelope_sha256,
                "accepted_pair_sha256": self.accepted_pair_sha256,
                "accepted_event_sha256": self.accepted_event_sha256,
                "acceptance_receipt_sha256": self.acceptance_receipt_sha256,
                "injection_receipt_sha256": self.injection_receipt_sha256,
                "session_snapshot_sha256": self.session_snapshot_sha256,
                "synchronization_receipt_sha256": self.synchronization_receipt_sha256,
                "stable_reference_keys": self.stable_reference_keys,
            }
        )
        if self.receipt_sha256 != expected:
            raise ContractValidationError("compact accepted-head receipt changed")


class StableAcceptedContextReferenceStore:
    """Atomic branch-local custody for stable accepted references."""

    def __init__(self, branch_root: Path) -> None:
        if not branch_root.is_absolute():
            raise ContractValidationError(
                "stable accepted-reference root must be absolute"
            )
        self.branch_root = lexical_absolute(branch_root)
        self.root = self.branch_root / "PLANNER_SESSION" / "ACCEPTED_REFERENCES"

    def path_for(
        self,
        accepted_turn_id: str,
        provider_thread_sha256: str,
    ) -> Path:
        if not re_is_sha256(provider_thread_sha256):
            raise ContractValidationError(
                "stable accepted-reference thread hash is invalid"
            )
        return self.root / (
            f"{text_sha256(accepted_turn_id)[:24]}."
            f"{provider_thread_sha256[:24]}.json"
        )

    def save(
        self,
        *,
        receipt: CompactAcceptedHeadReceiptV1,
        references: tuple[StableAcceptedContextReferenceV1, ...],
    ) -> Path:
        if tuple(value.reference_key for value in references) != receipt.stable_reference_keys:
            raise StateConflictError("stable accepted-reference set changed")
        payload = {
            "schema_version": "cera.stable_accepted_context_reference_set.v1",
            "receipt": to_primitive(receipt),
            "references": tuple(to_primitive(value) for value in references),
        }
        encoded = canonical_bytes(payload) + b"\n"
        path = self.path_for(
            receipt.accepted_turn_id,
            receipt.provider_thread_sha256,
        )
        relative_path = path.relative_to(self.branch_root).as_posix()
        temporary = path.with_suffix(".tmp")
        temporary_relative = temporary.relative_to(self.branch_root).as_posix()
        preflight_windows_legacy_paths(
            path,
            temporary,
            label="stable accepted-reference artifact",
        )
        ensure_parent_chain(self.branch_root, relative_path)
        final_custody = capture_target_custody(
            self.branch_root, relative_path
        )
        temporary_custody = capture_target_custody(
            self.branch_root, temporary_relative
        )
        inspect_leaf(self.branch_root, relative_path)
        inspect_leaf(self.branch_root, temporary_relative)
        with locked_directory_chain(
            self.branch_root,
            final_custody.verified_parent_relative_path,
            create_missing=False,
        ):
            verify_target_custody(self.branch_root, final_custody)
            verify_target_custody(self.branch_root, temporary_custody)
            if inspect_leaf(self.branch_root, relative_path).identity is not None:
                existing, _identity = safe_read_bytes(
                    self.branch_root, relative_path
                )
                if existing != encoded:
                    raise StateConflictError(
                        "stable accepted-reference bytes changed"
                    )
                return path
            temporary_leaf = inspect_leaf(
                self.branch_root, temporary_relative
            )
            if temporary_leaf.identity is not None:
                temporary_bytes, temporary_identity = safe_read_bytes(
                    self.branch_root, temporary_relative
                )
                if temporary_bytes != encoded:
                    raise StateConflictError(
                        "stable accepted-reference temporary custody changed"
                    )
            else:
                temporary_identity = safe_create_new_bytes(
                    self.branch_root, temporary_relative, encoded
                )
            try:
                verify_target_custody(self.branch_root, final_custody)
                verify_target_custody(self.branch_root, temporary_custody)
                safe_replace(
                    self.branch_root,
                    temporary_relative_path=temporary_relative,
                    final_relative_path=relative_path,
                    expected_temporary_identity=temporary_identity,
                )
            except BaseException:
                unlink_if_identity(
                    self.branch_root,
                    temporary_relative,
                    temporary_identity,
                )
                raise
            if inspect_leaf(self.branch_root, temporary_relative).identity is not None:
                raise StateConflictError(
                    "stable accepted-reference temporary custody changed"
                )
        return path

    def load(
        self,
        accepted_turn_id: str,
        *,
        provider_thread_sha256: str,
    ) -> tuple[
        CompactAcceptedHeadReceiptV1,
        tuple[StableAcceptedContextReferenceV1, ...],
    ]:
        path = self.path_for(accepted_turn_id, provider_thread_sha256)
        relative_path = path.relative_to(self.branch_root).as_posix()
        encoded, _identity = safe_read_bytes(self.branch_root, relative_path)
        raw = json.loads(encoded.decode("utf-8"))
        if (
            not isinstance(raw, dict)
            or raw.get("schema_version")
            != "cera.stable_accepted_context_reference_set.v1"
        ):
            raise ContractValidationError("stable accepted-reference set schema changed")
        from cera.schema import from_mapping

        receipt = from_mapping(CompactAcceptedHeadReceiptV1, raw.get("receipt"))
        references = tuple(
            from_mapping(StableAcceptedContextReferenceV1, value)
            for value in raw.get("references", ())
        )
        if (
            receipt.accepted_turn_id != accepted_turn_id
            or receipt.provider_thread_sha256 != provider_thread_sha256
            or tuple(value.reference_key for value in references)
            != receipt.stable_reference_keys
        ):
            raise StateConflictError("stable accepted-reference index changed")
        return receipt, references


def reconstruction_reference_synchronization_sha256(
    *,
    initialization_receipt_sha256: str,
    accepted_turn_id: str,
    accepted_envelope_sha256: str,
) -> str:
    """Bind reconstructed accepted authority to one new physical thread."""

    return canonical_sha256(
        {
            "schema_version": "cera.reconstruction_reference_synchronization.v1",
            "initialization_receipt_sha256": initialization_receipt_sha256,
            "accepted_turn_id": accepted_turn_id,
            "accepted_envelope_sha256": accepted_envelope_sha256,
        }
    )


def provider_fork_reference_synchronization_sha256(
    *,
    transfer_receipt_sha256: str,
    accepted_turn_id: str,
    accepted_envelope_sha256: str,
) -> str:
    """Bind child accepted-reference custody to one value-free fork transfer."""

    return canonical_sha256(
        {
            "schema_version": "cera.provider_fork_reference_synchronization.v1",
            "transfer_receipt_sha256": transfer_receipt_sha256,
            "accepted_turn_id": accepted_turn_id,
            "accepted_envelope_sha256": accepted_envelope_sha256,
        }
    )


def rebind_stable_accepted_context_references_for_reconstruction(
    *,
    source_receipt: CompactAcceptedHeadReceiptV1,
    source_references: tuple[StableAcceptedContextReferenceV1, ...],
    planner_session_id: str,
    provider_thread_sha256: str,
    accepted_turn_ids: tuple[str, ...],
    initialization_receipt_sha256: str,
    session_snapshot_sha256: str,
    target_world_id: str | None = None,
    target_branch_id: str | None = None,
    synchronization_kind: str = "reconstruction",
) -> tuple[
    CompactAcceptedHeadReceiptV1,
    tuple[StableAcceptedContextReferenceV1, ...],
]:
    """Rebind immutable facts to a new thread and, when needed, branch keys."""

    if not accepted_turn_ids or source_receipt.accepted_turn_id not in accepted_turn_ids:
        raise StateConflictError(
            "reconstruction reference is outside the accepted tail"
        )
    if tuple(value.reference_key for value in source_references) != (
        source_receipt.stable_reference_keys
    ):
        raise StateConflictError("reconstruction reference set changed")
    world_id = target_world_id or source_receipt.world_id
    branch_id = target_branch_id or source_receipt.branch_id
    if not world_id.strip() or not branch_id.strip():
        raise ContractValidationError(
            "reconstruction reference target scope is incomplete"
        )
    accepted_ancestry_sha256 = canonical_sha256(
        {
            "world_id": world_id,
            "branch_id": branch_id,
            "accepted_turn_ids": accepted_turn_ids,
            "accepted_head_envelope_sha256": source_receipt.accepted_envelope_sha256,
        }
    )
    if synchronization_kind == "reconstruction":
        synchronization_receipt_sha256 = (
            reconstruction_reference_synchronization_sha256(
                initialization_receipt_sha256=initialization_receipt_sha256,
                accepted_turn_id=source_receipt.accepted_turn_id,
                accepted_envelope_sha256=source_receipt.accepted_envelope_sha256,
            )
        )
    elif synchronization_kind == "accepted_checkpoint_fork":
        synchronization_receipt_sha256 = (
            provider_fork_reference_synchronization_sha256(
                transfer_receipt_sha256=initialization_receipt_sha256,
                accepted_turn_id=source_receipt.accepted_turn_id,
                accepted_envelope_sha256=source_receipt.accepted_envelope_sha256,
            )
        )
    else:
        raise ContractValidationError(
            "stable accepted-reference synchronization kind is invalid"
        )
    rebound = tuple(
        replace(
            value,
            reference_key=_reconstruction_target_reference_key(
                value,
                target_world_id=world_id,
                target_branch_id=branch_id,
            ),
            world_id=world_id,
            branch_id=branch_id,
            planner_session_id_sha256=text_sha256(planner_session_id),
            provider_thread_sha256=provider_thread_sha256,
            accepted_ancestry_sha256=accepted_ancestry_sha256,
            injection_receipt_sha256=initialization_receipt_sha256,
            session_snapshot_sha256=session_snapshot_sha256,
            synchronization_receipt_sha256=synchronization_receipt_sha256,
        )
        for value in source_references
    )
    receipt_payload = {
        "schema_version": CompactAcceptedHeadReceiptV1.SCHEMA_VERSION,
        "world_id": world_id,
        "branch_id": branch_id,
        "scene_id": source_receipt.scene_id,
        "accepted_turn_id": source_receipt.accepted_turn_id,
        "planner_session_id_sha256": text_sha256(planner_session_id),
        "provider_thread_sha256": provider_thread_sha256,
        "accepted_ancestry_sha256": accepted_ancestry_sha256,
        "accepted_envelope_sha256": source_receipt.accepted_envelope_sha256,
        "accepted_pair_sha256": source_receipt.accepted_pair_sha256,
        "accepted_event_sha256": source_receipt.accepted_event_sha256,
        "acceptance_receipt_sha256": source_receipt.acceptance_receipt_sha256,
        "injection_receipt_sha256": initialization_receipt_sha256,
        "session_snapshot_sha256": session_snapshot_sha256,
        "synchronization_receipt_sha256": synchronization_receipt_sha256,
        "stable_reference_keys": tuple(value.reference_key for value in rebound),
    }
    return (
        CompactAcceptedHeadReceiptV1(
            **receipt_payload,
            receipt_sha256=canonical_sha256(receipt_payload),
        ),
        rebound,
    )


def stable_reference_descriptors_for_reconstruction_target(
    *,
    source_references: tuple[StableAcceptedContextReferenceV1, ...],
    target_world_id: str,
    target_branch_id: str,
) -> tuple[dict[str, Any], ...]:
    """Return value-free descriptors re-keyed for one reconstruction target."""

    return tuple(
        {
            **value.injection_descriptor(),
            "reference_key": _reconstruction_target_reference_key(
                value,
                target_world_id=target_world_id,
                target_branch_id=target_branch_id,
            ),
        }
        for value in source_references
    )


def _reconstruction_target_reference_key(
    value: StableAcceptedContextReferenceV1,
    *,
    target_world_id: str,
    target_branch_id: str,
) -> str:
    if (
        value.world_id == target_world_id
        and value.branch_id == target_branch_id
    ):
        return value.reference_key
    return "binding_accepted_ref_" + canonical_sha256(
        {
            "schema_version": "cera.reconstruction_branch_reference.v1",
            "source_reference_key": value.reference_key,
            "target_world_id": target_world_id,
            "target_branch_id": target_branch_id,
            "accepted_turn_id": value.accepted_turn_id,
            "source_item_key": value.source_item_key,
            "field_name": value.field_name,
            "visibility": value.visibility.value,
            "knowledge_owner_id": value.knowledge_owner_id,
        }
    )[:20]


def project_final_sequence_facts(
    item: FinalSequenceItemV1,
) -> tuple[AcceptedSessionFactV1, ...]:
    """Split one accepted item into canonical field-level facts."""

    scopes = {value.field_name: value for value in item.field_scopes}
    facts: list[AcceptedSessionFactV1] = []
    for field_name in (
        "realized_event",
        "valid_deepseek_additions",
        "knowledge_changes",
        "material_changes",
        "resulting_state",
    ):
        raw = getattr(item, field_name)
        values = raw if isinstance(raw, tuple) else (raw,)
        scope = scopes.get(field_name)
        if not values or scope is None:
            continue
        for index, value in enumerate(values):
            facts.append(
                AcceptedSessionFactV1(
                    fact_key="accepted_fact_"
                    + canonical_sha256(
                        {
                            "source_item_key": item.item_key,
                            "field_name": field_name,
                            "index": index,
                            "value": value,
                            "scope": scope,
                        }
                    )[:20],
                    source_item_key=item.item_key,
                    field_name=field_name,
                    value=value,
                    visibility=scope.visibility,
                    knowledge_owner_id=scope.knowledge_owner_id,
                    roles=scope.roles,
                    protected_user_source_claim_keys=scope.protected_user_source_claim_keys,
                )
            )
    return tuple(facts)


def validate_stable_accepted_context_reference_facts(
    *,
    envelope: AcceptedFinalSequenceEnvelopeV1,
    references: tuple[StableAcceptedContextReferenceV1, ...],
) -> None:
    """Prove every stored value and owner is an exact accepted field fact."""

    accepted_facts = tuple(
        fact
        for item in envelope.complete_final_sequence.items
        for fact in project_final_sequence_facts(item)
    )
    if len(accepted_facts) != len(references) or any(
        (
            reference.accepted_turn_id != envelope.accepted_turn_id
            or reference.accepted_envelope_sha256 != envelope.envelope_sha256
            or reference.source_item_key != fact.source_item_key
            or reference.field_name != fact.field_name
            or reference.field_value != fact.value
            or reference.visibility is not fact.visibility
            or reference.knowledge_owner_id != fact.knowledge_owner_id
            or reference.roles != fact.roles
            or reference.protected_user_source_claim_keys
            != fact.protected_user_source_claim_keys
        )
        for reference, fact in zip(references, accepted_facts, strict=True)
    ):
        raise StateConflictError(
            "stable accepted-reference facts changed from the accepted sequence"
        )


def build_stable_accepted_context_references(
    *,
    world_id: str,
    branch_id: str,
    scene_id: str,
    planner_session_id: str,
    provider_thread_sha256: str,
    accepted_turn_ids: tuple[str, ...],
    accepted_turn_id: str,
    accepted_envelope_sha256: str,
    accepted_pair_sha256: str,
    accepted_event_sha256: str,
    acceptance_receipt_sha256: str,
    injection_receipt_sha256: str,
    session_snapshot_sha256: str,
    synchronization_receipt_sha256: str,
    facts: tuple[AcceptedSessionFactV1, ...],
) -> tuple[CompactAcceptedHeadReceiptV1, tuple[StableAcceptedContextReferenceV1, ...]]:
    """Allocate deterministic stable keys after the acceptance chain closes."""

    if not facts:
        raise ContractValidationError("accepted turn produced no stable reference facts")
    if not accepted_turn_ids or accepted_turn_ids[-1] != accepted_turn_id:
        raise StateConflictError("accepted-reference ancestry head changed")
    ancestry_sha256 = canonical_sha256(
        {
            "world_id": world_id,
            "branch_id": branch_id,
            "accepted_turn_ids": accepted_turn_ids,
            "accepted_head_envelope_sha256": accepted_envelope_sha256,
        }
    )
    references: list[StableAcceptedContextReferenceV1] = []
    descriptors = stable_reference_descriptors_for_facts(
        world_id=world_id,
        branch_id=branch_id,
        accepted_turn_id=accepted_turn_id,
        facts=facts,
    )
    for fact, descriptor in zip(facts, descriptors, strict=True):
        reference_key = str(descriptor["reference_key"])
        references.append(
            StableAcceptedContextReferenceV1(
                schema_version=StableAcceptedContextReferenceV1.SCHEMA_VERSION,
                reference_key=reference_key,
                world_id=world_id,
                branch_id=branch_id,
                planner_session_id_sha256=text_sha256(planner_session_id),
                provider_thread_sha256=provider_thread_sha256,
                accepted_turn_id=accepted_turn_id,
                scene_id=scene_id,
                accepted_ancestry_sha256=ancestry_sha256,
                accepted_envelope_sha256=accepted_envelope_sha256,
                accepted_pair_sha256=accepted_pair_sha256,
                accepted_event_sha256=accepted_event_sha256,
                acceptance_receipt_sha256=acceptance_receipt_sha256,
                injection_receipt_sha256=injection_receipt_sha256,
                session_snapshot_sha256=session_snapshot_sha256,
                synchronization_receipt_sha256=synchronization_receipt_sha256,
                source_item_key=fact.source_item_key,
                field_name=fact.field_name,
                field_value=fact.value,
                field_value_sha256=text_sha256(fact.value),
                visibility=fact.visibility,
                knowledge_owner_id=fact.knowledge_owner_id,
                roles=fact.roles,
                protected_user_source_claim_keys=(
                    fact.protected_user_source_claim_keys
                ),
            )
        )
    keys = tuple(value.reference_key for value in references)
    receipt_payload = {
        "schema_version": CompactAcceptedHeadReceiptV1.SCHEMA_VERSION,
        "world_id": world_id,
        "branch_id": branch_id,
        "scene_id": scene_id,
        "accepted_turn_id": accepted_turn_id,
        "planner_session_id_sha256": text_sha256(planner_session_id),
        "provider_thread_sha256": provider_thread_sha256,
        "accepted_ancestry_sha256": ancestry_sha256,
        "accepted_envelope_sha256": accepted_envelope_sha256,
        "accepted_pair_sha256": accepted_pair_sha256,
        "accepted_event_sha256": accepted_event_sha256,
        "acceptance_receipt_sha256": acceptance_receipt_sha256,
        "injection_receipt_sha256": injection_receipt_sha256,
        "session_snapshot_sha256": session_snapshot_sha256,
        "synchronization_receipt_sha256": synchronization_receipt_sha256,
        "stable_reference_keys": keys,
    }
    receipt = CompactAcceptedHeadReceiptV1(
        **receipt_payload,
        receipt_sha256=canonical_sha256(receipt_payload),
    )
    return receipt, tuple(references)


def stable_reference_descriptors_for_facts(
    *,
    world_id: str,
    branch_id: str,
    accepted_turn_id: str,
    facts: tuple[AcceptedSessionFactV1, ...],
) -> tuple[dict[str, Any], ...]:
    """Descriptors are injected beside the one accepted sequence, without values."""

    return tuple(
        {
            "reference_key": "binding_accepted_ref_"
            + canonical_sha256(
                {
                    "world_id": world_id,
                    "branch_id": branch_id,
                    "accepted_turn_id": accepted_turn_id,
                    "source_item_key": fact.source_item_key,
                    "field_name": fact.field_name,
                    "fact_key": fact.fact_key,
                    "visibility": fact.visibility.value,
                    "knowledge_owner_id": fact.knowledge_owner_id,
                }
            )[:20],
            "accepted_turn_id": accepted_turn_id,
            "source_item_key": fact.source_item_key,
            "field_name": fact.field_name,
            "visibility": fact.visibility.value,
            "knowledge_owner_id": fact.knowledge_owner_id,
            "roles": to_primitive(fact.roles),
        }
        for fact in facts
    )


class EvidenceBindingKind(StrEnum):
    CURRENT_USER_SOURCE = "current_user_source"
    MECHANICAL_CONNECTIVE_ALLOWANCE = "mechanical_connective_allowance"
    WORLD_RECORD = "world_record"
    ACCEPTED_SESSION_ENVELOPE = "accepted_session_envelope"


class EvidenceVisibility(StrEnum):
    PUBLIC = "public"
    CHARACTER_PRIVATE = "character_private"
    CREATOR_PRIVATE = "creator_private"


class EvidenceAuthorityClass(StrEnum):
    CURRENT_SOURCE = "current_source"
    PYTHON_MECHANICAL_ALLOWANCE = "python_mechanical_allowance"
    ACTIVE_AUTHORITY = "active_authority"
    DERIVED_RETRIEVAL_CONTEXT = "derived_retrieval_context"
    ACCEPTED_SESSION_AUTHORITY = "accepted_session_authority"


@dataclass(frozen=True, slots=True)
class RequestEvidenceBindingV1:
    """One Python-allocated handle valid only for one request."""

    SCHEMA_VERSION: ClassVar[str] = "cera.request_evidence_binding.v4"

    schema_version: str
    binding_key: str
    kind: EvidenceBindingKind
    world_id: str
    branch_id: str
    turn_id: str
    source_identity: str | None
    source_sha256: str
    protected_user_allowance_scope: str | None
    authority_classification: EvidenceAuthorityClass
    relative_path: str | None
    record_revision: int | None
    record_type: str | None
    visibility: EvidenceVisibility
    knowledge_owner_id: str | None
    exact_read_operation_sha256: str | None
    accepted_turn_id: str | None = None
    acceptance_receipt_sha256: str | None = None
    accepted_envelope_sha256: str | None = None
    provider_thread_sha256: str | None = None
    session_snapshot_sha256: str | None = None
    synchronization_receipt_sha256: str | None = None

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("evidence binding schema changed")
        if not self.binding_key.startswith("binding_") or not self.binding_key.replace("_", "").isalnum():
            raise ContractValidationError("evidence binding key is invalid")
        if not all(isinstance(value, str) and value.strip() for value in (self.world_id, self.branch_id, self.turn_id)):
            raise ContractValidationError("evidence binding scope is incomplete")
        if not re_is_sha256(self.source_sha256):
            raise ContractValidationError("evidence source hash is invalid")
        if self.kind in {
            EvidenceBindingKind.CURRENT_USER_SOURCE,
            EvidenceBindingKind.MECHANICAL_CONNECTIVE_ALLOWANCE,
        }:
            if not self.source_identity or not self.protected_user_allowance_scope:
                raise ContractValidationError("current source binding is incomplete")
            if any(value is not None for value in (
                self.relative_path,
                self.record_revision,
                self.record_type,
                self.knowledge_owner_id,
                self.exact_read_operation_sha256,
                self.accepted_turn_id,
                self.acceptance_receipt_sha256,
                self.accepted_envelope_sha256,
                self.provider_thread_sha256,
                self.session_snapshot_sha256,
                self.synchronization_receipt_sha256,
            )):
                raise ContractValidationError("current source binding carries world-record fields")
            if self.visibility is not EvidenceVisibility.PUBLIC:
                raise ContractValidationError("current source binding visibility changed")
            expected_authority = (
                EvidenceAuthorityClass.CURRENT_SOURCE
                if self.kind is EvidenceBindingKind.CURRENT_USER_SOURCE
                else EvidenceAuthorityClass.PYTHON_MECHANICAL_ALLOWANCE
            )
            if self.authority_classification is not expected_authority:
                raise ContractValidationError(
                    "source or mechanical binding authority changed"
                )
        elif self.kind is EvidenceBindingKind.WORLD_RECORD:
            if self.source_identity is not None or self.protected_user_allowance_scope is not None:
                raise ContractValidationError("world binding carries current-source fields")
            if not self.relative_path or not self.record_type or not self.exact_read_operation_sha256:
                raise ContractValidationError("world binding is not an exact fetched record")
            if not re_is_sha256(self.exact_read_operation_sha256):
                raise ContractValidationError("world binding read operation hash is invalid")
            if self.record_revision is not None and (
                type(self.record_revision) is not int or self.record_revision < 1
            ):
                raise ContractValidationError("world binding revision is invalid")
            if self.visibility is EvidenceVisibility.CHARACTER_PRIVATE and not self.knowledge_owner_id:
                raise ContractValidationError("private evidence requires its knowledge owner")
            if self.visibility is EvidenceVisibility.PUBLIC and self.knowledge_owner_id is not None:
                raise ContractValidationError("public evidence cannot carry a private owner")
            relative = self.relative_path.replace("\\", "/")
            expected_authority = (
                EvidenceAuthorityClass.ACTIVE_AUTHORITY
                if relative.startswith("ACTIVE/")
                else EvidenceAuthorityClass.DERIVED_RETRIEVAL_CONTEXT
            )
            if self.authority_classification is not expected_authority:
                raise ContractValidationError(
                    "world binding authority does not match its exact path"
                )
            if any(
                value is not None
                for value in (
                    self.accepted_turn_id,
                    self.acceptance_receipt_sha256,
                    self.accepted_envelope_sha256,
                    self.provider_thread_sha256,
                    self.session_snapshot_sha256,
                    self.synchronization_receipt_sha256,
                )
            ):
                raise ContractValidationError("world binding carries accepted-session fields")
        else:
            if self.source_identity is not None or self.protected_user_allowance_scope is not None:
                raise ContractValidationError("accepted-session binding carries source fields")
            if any(
                value is not None
                for value in (
                    self.relative_path,
                    self.record_revision,
                    self.record_type,
                    self.exact_read_operation_sha256,
                )
            ):
                raise ContractValidationError("accepted-session binding carries world-record fields")
            required_hashes = (
                self.acceptance_receipt_sha256,
                self.accepted_envelope_sha256,
                self.provider_thread_sha256,
                self.session_snapshot_sha256,
                self.synchronization_receipt_sha256,
            )
            if not self.accepted_turn_id or any(not re_is_sha256(value) for value in required_hashes):
                raise ContractValidationError("accepted-session binding is incomplete")
            if self.authority_classification is not EvidenceAuthorityClass.ACCEPTED_SESSION_AUTHORITY:
                raise ContractValidationError("accepted-session authority changed")
            if self.visibility is EvidenceVisibility.CREATOR_PRIVATE:
                raise ContractValidationError("accepted-session evidence cannot be creator-private")

    @property
    def binding_sha256(self) -> str:
        return domain_sha256(self.SCHEMA_VERSION, self)


class RequestEvidenceBindingRegistry:
    """Mutable request ledger whose exported bindings are immutable records."""

    SCHEMA_VERSION = "cera.request_evidence_binding_registry.v10"

    def __init__(self, *, world_id: str, branch_id: str, turn_id: str) -> None:
        if not all(isinstance(value, str) and value.strip() for value in (world_id, branch_id, turn_id)):
            raise ContractValidationError("evidence registry scope is incomplete")
        self.world_id = world_id
        self.branch_id = branch_id
        self.turn_id = turn_id
        self._bindings: dict[str, RequestEvidenceBindingV1] = {}
        self._source_units: dict[str, IngressSourceUnitV1] = {}
        self._protected_user_claims: dict[str, ProtectedUserSourceClaimV1] = {}
        self._story_segments: dict[str, StoryRealizationSegmentV1] = {}
        self._accepted_session_projections: dict[str, AcceptedSessionProjectionV1] = {}
        self._stable_accepted_context_references: dict[
            str, StableAcceptedContextReferenceV1
        ] = {}

    @staticmethod
    def current_source_key(*, world_id: str, branch_id: str, turn_id: str, source_sha256: str) -> str:
        return "binding_source_" + canonical_sha256(
            {"world_id": world_id, "branch_id": branch_id, "turn_id": turn_id, "source_sha256": source_sha256}
        )[:20]

    @staticmethod
    def world_record_key(*, world_id: str, branch_id: str, turn_id: str, relative_path: str, source_sha256: str) -> str:
        return "binding_record_" + canonical_sha256(
            {
                "world_id": world_id,
                "branch_id": branch_id,
                "turn_id": turn_id,
                "relative_path": relative_path,
                "source_sha256": source_sha256,
            }
        )[:20]

    def allocate_current_source(
        self,
        *,
        source_identity: str,
        source_text: str,
        protected_user_allowance_scope: str,
        source_units: tuple[IngressSourceUnitV1, ...],
    ) -> RequestEvidenceBindingV1:
        source_sha256 = text_sha256(source_text)
        binding = RequestEvidenceBindingV1(
            schema_version=RequestEvidenceBindingV1.SCHEMA_VERSION,
            binding_key=self.current_source_key(
                world_id=self.world_id,
                branch_id=self.branch_id,
                turn_id=self.turn_id,
                source_sha256=source_sha256,
            ),
            kind=EvidenceBindingKind.CURRENT_USER_SOURCE,
            world_id=self.world_id,
            branch_id=self.branch_id,
            turn_id=self.turn_id,
            source_identity=source_identity,
            source_sha256=source_sha256,
            protected_user_allowance_scope=protected_user_allowance_scope,
            authority_classification=EvidenceAuthorityClass.CURRENT_SOURCE,
            relative_path=None,
            record_revision=None,
            record_type=None,
            visibility=EvidenceVisibility.PUBLIC,
            knowledge_owner_id=None,
            exact_read_operation_sha256=None,
        )
        result = self._add(binding)
        previous_end = -1
        for unit in source_units:
            if unit.source_end > len(source_text) or source_text[
                unit.source_start : unit.source_end
            ] != unit.exact_text:
                raise ContractValidationError("ingress source unit changed exact source bytes")
            if unit.source_start < previous_end:
                raise ContractValidationError("ingress source units overlap or are unordered")
            previous_end = unit.source_end
            if unit.source_unit_key in self._source_units:
                raise ContractValidationError("ingress source-unit key is duplicated")
            self._source_units[unit.source_unit_key] = unit
            protected_owner = unit.actor_id == "character:ted" or unit.speaker_id == "character:ted"
            if not protected_owner:
                continue
            kind = (
                ProtectedUserSourceClaimKind.DIALOGUE
                if unit.kind is IngressSourceUnitKind.DIALOGUE
                else ProtectedUserSourceClaimKind.ACTION_OR_STATE
            )
            claim_key = "claim_source_" + canonical_sha256(
                {
                    "source_binding_key": result.binding_key,
                    "source_sha256": source_sha256,
                    "source_unit_sha256": unit.source_unit_sha256,
                }
            )[:20]
            self._protected_user_claims[claim_key] = ProtectedUserSourceClaimV1(
                schema_version=ProtectedUserSourceClaimV1.SCHEMA_VERSION,
                claim_key=claim_key,
                kind=kind,
                source_binding_key=result.binding_key,
                source_sha256=source_sha256,
                source_start=unit.source_start,
                source_end=unit.source_end,
                exact_text=unit.exact_text,
                source_unit_key=unit.source_unit_key,
                source_unit_kind=unit.kind,
                speaker_id="character:ted",
                deterministic_projection_rule="explicit_ingress_source_unit",
            )
        return result

    def allocate_mechanical_connective_allowance(
        self,
        *,
        allowance_scope: str = (
            "nonmeaningful continuity only; no action, dialogue, thought, decision, "
            "movement, consent, or new story fact for the protected user"
        ),
    ) -> RequestEvidenceBindingV1:
        source_identity = f"python_mechanical_allowance:{self.turn_id}"
        source_sha256 = canonical_sha256(
            {
                "source_identity": source_identity,
                "allowance_scope": allowance_scope,
                "world_id": self.world_id,
                "branch_id": self.branch_id,
                "turn_id": self.turn_id,
            }
        )
        binding = RequestEvidenceBindingV1(
            schema_version=RequestEvidenceBindingV1.SCHEMA_VERSION,
            binding_key="binding_mechanical_"
            + canonical_sha256(
                {
                    "world_id": self.world_id,
                    "branch_id": self.branch_id,
                    "turn_id": self.turn_id,
                    "scope": allowance_scope,
                }
            )[:20],
            kind=EvidenceBindingKind.MECHANICAL_CONNECTIVE_ALLOWANCE,
            world_id=self.world_id,
            branch_id=self.branch_id,
            turn_id=self.turn_id,
            source_identity=source_identity,
            source_sha256=source_sha256,
            protected_user_allowance_scope=allowance_scope,
            authority_classification=EvidenceAuthorityClass.PYTHON_MECHANICAL_ALLOWANCE,
            relative_path=None,
            record_revision=None,
            record_type=None,
            visibility=EvidenceVisibility.PUBLIC,
            knowledge_owner_id=None,
            exact_read_operation_sha256=None,
        )
        return self._add(binding)

    def allocate_world_record(
        self,
        *,
        relative_path: str,
        source_sha256: str,
        record_revision: int | None,
        record_type: str,
        visibility: EvidenceVisibility,
        knowledge_owner_id: str | None,
        exact_read_operation_sha256: str,
    ) -> RequestEvidenceBindingV1:
        binding = RequestEvidenceBindingV1(
            schema_version=RequestEvidenceBindingV1.SCHEMA_VERSION,
            binding_key=self.world_record_key(
                world_id=self.world_id,
                branch_id=self.branch_id,
                turn_id=self.turn_id,
                relative_path=relative_path,
                source_sha256=source_sha256,
            ),
            kind=EvidenceBindingKind.WORLD_RECORD,
            world_id=self.world_id,
            branch_id=self.branch_id,
            turn_id=self.turn_id,
            source_identity=None,
            source_sha256=source_sha256,
            protected_user_allowance_scope=None,
            authority_classification=(
                EvidenceAuthorityClass.ACTIVE_AUTHORITY
                if relative_path.replace("\\", "/").startswith("ACTIVE/")
                else EvidenceAuthorityClass.DERIVED_RETRIEVAL_CONTEXT
            ),
            relative_path=relative_path,
            record_revision=record_revision,
            record_type=record_type,
            visibility=visibility,
            knowledge_owner_id=knowledge_owner_id,
            exact_read_operation_sha256=exact_read_operation_sha256,
        )
        prior = self._bindings.get(binding.binding_key)
        if prior is not None and replace(
            binding,
            exact_read_operation_sha256=prior.exact_read_operation_sha256,
        ) == prior:
            return prior
        return self._add(binding)

    def allocate_accepted_session_envelope(
        self,
        *,
        accepted_turn_id: str,
        acceptance_receipt_sha256: str,
        accepted_envelope_sha256: str,
        provider_thread_sha256: str,
        session_snapshot_sha256: str,
        synchronization_receipt_sha256: str,
        knowledge_owner_id: str | None,
        source_projection_sha256: str | None = None,
    ) -> RequestEvidenceBindingV1:
        key = "binding_session_" + canonical_sha256(
            {
                "world_id": self.world_id,
                "branch_id": self.branch_id,
                "turn_id": self.turn_id,
                "accepted_turn_id": accepted_turn_id,
                "accepted_envelope_sha256": accepted_envelope_sha256,
                "knowledge_owner_id": knowledge_owner_id,
            }
        )[:20]
        return self._add(
            RequestEvidenceBindingV1(
                schema_version=RequestEvidenceBindingV1.SCHEMA_VERSION,
                binding_key=key,
                kind=EvidenceBindingKind.ACCEPTED_SESSION_ENVELOPE,
                world_id=self.world_id,
                branch_id=self.branch_id,
                turn_id=self.turn_id,
                source_identity=None,
                source_sha256=source_projection_sha256 or accepted_envelope_sha256,
                protected_user_allowance_scope=None,
                authority_classification=EvidenceAuthorityClass.ACCEPTED_SESSION_AUTHORITY,
                relative_path=None,
                record_revision=None,
                record_type=None,
                visibility=(
                    EvidenceVisibility.CHARACTER_PRIVATE
                    if knowledge_owner_id is not None
                    else EvidenceVisibility.PUBLIC
                ),
                knowledge_owner_id=knowledge_owner_id,
                exact_read_operation_sha256=None,
                accepted_turn_id=accepted_turn_id,
                acceptance_receipt_sha256=acceptance_receipt_sha256,
                accepted_envelope_sha256=accepted_envelope_sha256,
                provider_thread_sha256=provider_thread_sha256,
                session_snapshot_sha256=session_snapshot_sha256,
                synchronization_receipt_sha256=synchronization_receipt_sha256,
            )
        )

    def allocate_accepted_session_projection(
        self, projection: AcceptedSessionProjectionV1
    ) -> RequestEvidenceBindingV1:
        if (
            projection.world_id,
            projection.branch_id,
            projection.request_turn_id,
        ) != (self.world_id, self.branch_id, self.turn_id):
            raise StateConflictError("accepted-session projection belongs to another request")
        binding = self.allocate_accepted_session_envelope(
            accepted_turn_id=projection.accepted_turn_id,
            acceptance_receipt_sha256=projection.acceptance_receipt_sha256,
            accepted_envelope_sha256=projection.accepted_envelope_sha256,
            provider_thread_sha256=projection.provider_thread_sha256,
            session_snapshot_sha256=projection.session_snapshot_sha256,
            synchronization_receipt_sha256=projection.synchronization_receipt_sha256,
            knowledge_owner_id=projection.knowledge_owner_id,
            source_projection_sha256=projection.projection_sha256,
        )
        prior = self._accepted_session_projections.get(binding.binding_key)
        if prior is not None and prior != projection:
            raise StateConflictError("accepted-session projection binding collision")
        self._accepted_session_projections[binding.binding_key] = projection
        return binding

    def allocate_stable_accepted_context_reference(
        self,
        reference: StableAcceptedContextReferenceV1,
        *,
        current_provider_thread_sha256: str,
        current_accepted_ancestry_sha256: str,
    ) -> RequestEvidenceBindingV1:
        """Resolve a stored stable key without placing its fact payload in prompt."""

        if (
            reference.world_id != self.world_id
            or reference.branch_id != self.branch_id
            or reference.provider_thread_sha256
            != current_provider_thread_sha256
            or reference.accepted_ancestry_sha256
            != current_accepted_ancestry_sha256
        ):
            raise StateConflictError(
                "stable accepted-reference is stale, foreign, or outside ancestry"
            )
        binding = RequestEvidenceBindingV1(
            schema_version=RequestEvidenceBindingV1.SCHEMA_VERSION,
            binding_key=reference.reference_key,
            kind=EvidenceBindingKind.ACCEPTED_SESSION_ENVELOPE,
            world_id=self.world_id,
            branch_id=self.branch_id,
            turn_id=self.turn_id,
            source_identity=None,
            source_sha256=reference.reference_sha256,
            protected_user_allowance_scope=None,
            authority_classification=EvidenceAuthorityClass.ACCEPTED_SESSION_AUTHORITY,
            relative_path=None,
            record_revision=None,
            record_type=None,
            visibility=(
                EvidenceVisibility.CHARACTER_PRIVATE
                if reference.knowledge_owner_id is not None
                else EvidenceVisibility.PUBLIC
            ),
            knowledge_owner_id=reference.knowledge_owner_id,
            exact_read_operation_sha256=None,
            accepted_turn_id=reference.accepted_turn_id,
            acceptance_receipt_sha256=reference.acceptance_receipt_sha256,
            accepted_envelope_sha256=reference.accepted_envelope_sha256,
            provider_thread_sha256=reference.provider_thread_sha256,
            session_snapshot_sha256=reference.session_snapshot_sha256,
            synchronization_receipt_sha256=(
                reference.synchronization_receipt_sha256
            ),
        )
        prior = self._stable_accepted_context_references.get(reference.reference_key)
        if prior is not None and prior != reference:
            raise StateConflictError("stable accepted-reference key collision")
        self._stable_accepted_context_references[reference.reference_key] = reference
        return self._add(binding)

    def allocate_initial_projection(
        self,
        *,
        branch_root: Path,
        relative_path: str,
        record_type: str,
        visibility: EvidenceVisibility,
        knowledge_owner_id: str | None,
    ) -> RequestEvidenceBindingV1:
        target, text = _read_branch_file_no_follow(branch_root, relative_path)
        normalized_relative = normalized_relative_path(relative_path)
        revision = None
        if target.suffix.casefold() == ".json":
            payload = json.loads(text)
            revision = payload.get("_cera_revision") if isinstance(payload, dict) else None
        return self.allocate_world_record(
            relative_path=normalized_relative,
            source_sha256=text_sha256(text),
            record_revision=revision,
            record_type=record_type,
            visibility=visibility,
            knowledge_owner_id=knowledge_owner_id,
            exact_read_operation_sha256=canonical_sha256(
                {
                    "operation": "deterministic_initial_projection",
                    "relative_path": normalized_relative,
                    "source_sha256": text_sha256(text),
                    "turn_id": self.turn_id,
                }
            ),
        )

    def _add(self, binding: RequestEvidenceBindingV1) -> RequestEvidenceBindingV1:
        prior = self._bindings.get(binding.binding_key)
        if prior is not None and prior != binding:
            raise StateConflictError("evidence binding key collision")
        self._bindings[binding.binding_key] = binding
        return binding

    def import_bindings(self, values: Iterable[RequestEvidenceBindingV1]) -> None:
        for value in values:
            if (value.world_id, value.branch_id, value.turn_id) != (
                self.world_id,
                self.branch_id,
                self.turn_id,
            ):
                raise StateConflictError("evidence binding belongs to another request")
            self._add(value)

    def import_provider_debug(self, value: Any) -> None:
        if not isinstance(value, dict):
            return
        for raw in value.get("evidence_bindings", ()):
            if not isinstance(raw, dict):
                raise ContractValidationError("provider evidence binding descriptor is invalid")
            if raw.get("kind") != EvidenceBindingKind.WORLD_RECORD.value:
                raise PermissionError(
                    "provider debug may return only exact world-record bindings"
                )
            self.import_bindings(
                (
                    RequestEvidenceBindingV1(
                        schema_version=raw["schema_version"],
                        binding_key=raw["binding_key"],
                        kind=EvidenceBindingKind(raw["kind"]),
                        world_id=raw["world_id"],
                        branch_id=raw["branch_id"],
                        turn_id=raw["turn_id"],
                        source_identity=raw.get("source_identity"),
                        source_sha256=raw["source_sha256"],
                        protected_user_allowance_scope=raw.get("protected_user_allowance_scope"),
                        authority_classification=EvidenceAuthorityClass(
                            raw["authority_classification"]
                        ),
                        relative_path=raw.get("relative_path"),
                        record_revision=raw.get("record_revision"),
                        record_type=raw.get("record_type"),
                        visibility=EvidenceVisibility(raw["visibility"]),
                        knowledge_owner_id=raw.get("knowledge_owner_id"),
                        exact_read_operation_sha256=raw.get("exact_read_operation_sha256"),
                        accepted_turn_id=raw.get("accepted_turn_id"),
                        acceptance_receipt_sha256=raw.get("acceptance_receipt_sha256"),
                        accepted_envelope_sha256=raw.get("accepted_envelope_sha256"),
                        provider_thread_sha256=raw.get("provider_thread_sha256"),
                        session_snapshot_sha256=raw.get("session_snapshot_sha256"),
                        synchronization_receipt_sha256=raw.get("synchronization_receipt_sha256"),
                    ),
                )
            )

    @property
    def bindings(self) -> tuple[RequestEvidenceBindingV1, ...]:
        return tuple(self._bindings[key] for key in sorted(self._bindings))

    def prompt_manifest(self) -> tuple[dict[str, Any], ...]:
        return tuple(
            {
                "binding_key": value.binding_key,
                "kind": value.kind.value,
                "source_identity": value.source_identity,
                "source_sha256": value.source_sha256,
                "relative_path": value.relative_path,
                "record_revision": value.record_revision,
                "record_type": value.record_type,
                "authority_classification": value.authority_classification.value,
                "visibility": value.visibility.value,
                "knowledge_owner_id": value.knowledge_owner_id,
                "accepted_turn_id": value.accepted_turn_id,
                "acceptance_receipt_sha256": value.acceptance_receipt_sha256,
                "accepted_envelope_sha256": value.accepted_envelope_sha256,
                "provider_thread_sha256": value.provider_thread_sha256,
                "session_snapshot_sha256": value.session_snapshot_sha256,
                "synchronization_receipt_sha256": value.synchronization_receipt_sha256,
                "stable_reference_only": (
                    value.binding_key in self._stable_accepted_context_references
                ),
            }
            for value in self.bindings
        )

    def validator_binding_manifest(
        self,
        sequence: RichPlannerSequenceV1,
    ) -> tuple[dict[str, Any], ...]:
        """Hide uncited stable-reference metadata from the Validator request."""

        cited = {
            key
            for beat in sequence.beats
            for key in beat.source_evidence_bindings
        }
        return tuple(
            value
            for value in self.prompt_manifest()
            if not value["stable_reference_only"]
            or value["binding_key"] in cited
        )

    def validator_cited_accepted_evidence_closure(
        self,
        sequence: RichPlannerSequenceV1,
    ) -> tuple[ValidatorCitedAcceptedEvidenceV1, ...]:
        """Resolve exact values only for stable keys the Planner actually cited."""

        if (sequence.world_id, sequence.branch_id) != (
            self.world_id,
            self.branch_id,
        ):
            raise StateConflictError(
                "Validator cited accepted-evidence changed request scope"
            )
        cited_by_key: dict[str, list[str]] = {}
        citation_order: list[str] = []
        for beat in sequence.beats:
            for key in beat.source_evidence_bindings:
                if key not in self._stable_accepted_context_references:
                    continue
                if key not in cited_by_key:
                    cited_by_key[key] = []
                    citation_order.append(key)
                cited_by_key[key].append(beat.beat_key)
        closure = []
        beats_by_key = {value.beat_key: value for value in sequence.beats}
        for key in citation_order:
            reference = self._stable_accepted_context_references[key]
            binding = self._bindings.get(key)
            if (
                binding is None
                or binding.source_sha256 != reference.reference_sha256
                or binding.world_id != reference.world_id
                or binding.branch_id != reference.branch_id
                or binding.provider_thread_sha256
                != reference.provider_thread_sha256
                or binding.accepted_envelope_sha256
                != reference.accepted_envelope_sha256
                or binding.acceptance_receipt_sha256
                != reference.acceptance_receipt_sha256
                or binding.session_snapshot_sha256
                != reference.session_snapshot_sha256
                or binding.synchronization_receipt_sha256
                != reference.synchronization_receipt_sha256
                or binding.visibility
                is not (
                    EvidenceVisibility.CHARACTER_PRIVATE
                    if reference.knowledge_owner_id is not None
                    else EvidenceVisibility.PUBLIC
                )
                or binding.knowledge_owner_id != reference.knowledge_owner_id
            ):
                raise StateConflictError(
                    "Validator cited accepted-evidence binding changed"
                )
            beat_keys = tuple(cited_by_key[key])
            if reference.knowledge_owner_id is not None and any(
                reference.knowledge_owner_id
                not in beats_by_key[beat_key].roles.assertion_owner_ids
                for beat_key in beat_keys
            ):
                raise PermissionError(
                    "Validator cited private accepted evidence left its owner"
                )
            payload = {
                "schema_version": ValidatorCitedAcceptedEvidenceV1.SCHEMA_VERSION,
                "request_turn_id": self.turn_id,
                "binding_key": key,
                "binding_kind": binding.kind.value,
                "authority_classification": (
                    binding.authority_classification.value
                ),
                "cited_by_beat_keys": beat_keys,
                "accepted_reference": reference,
            }
            primitive = {
                **payload,
                "accepted_reference": to_primitive(reference),
            }
            closure.append(
                ValidatorCitedAcceptedEvidenceV1(
                    **payload,
                    closure_sha256=canonical_sha256(primitive),
                )
            )
        return tuple(closure)

    def protected_user_claim_manifest(self) -> tuple[dict[str, Any], ...]:
        return tuple(
            {
                "claim_key": value.claim_key,
                "kind": value.kind.value,
                "source_binding_key": value.source_binding_key,
                "source_sha256": value.source_sha256,
                "source_start": value.source_start,
                "source_end": value.source_end,
                "exact_text": value.exact_text,
                "speaker_id": value.speaker_id,
                "source_unit_key": value.source_unit_key,
                "source_unit_kind": value.source_unit_kind.value,
                "deterministic_projection_rule": value.deterministic_projection_rule,
            }
            for value in sorted(
                self._protected_user_claims.values(),
                key=lambda item: (item.source_start, item.source_end, item.claim_key),
            )
        )

    def accepted_session_projection_manifest(self) -> tuple[dict[str, Any], ...]:
        return tuple(
            {
                "binding_key": key,
                "projection_sha256": value.projection_sha256,
                "projection": value,
            }
            for key, value in sorted(self._accepted_session_projections.items())
        )

    def validate_sequence(self, sequence: RichPlannerSequenceV1, *, branch_root: Path) -> None:
        if (sequence.world_id, sequence.branch_id) != (self.world_id, self.branch_id):
            raise StateConflictError("Planner sequence changed evidence scope")
        for beat in sequence.beats:
            resolved = []
            for key in beat.source_evidence_bindings:
                try:
                    binding = self._bindings[key]
                except KeyError as exc:
                    raise StateConflictError("Planner cited an unallocated evidence binding") from exc
                if binding.kind is EvidenceBindingKind.WORLD_RECORD:
                    self._validate_world_binding(binding, branch_root)
                    if (
                        binding.visibility is EvidenceVisibility.CHARACTER_PRIVATE
                        and binding.knowledge_owner_id
                        not in beat.roles.assertion_owner_ids
                    ):
                        raise PermissionError("private evidence transferred to a non-owner actor")
                elif binding.kind is EvidenceBindingKind.ACCEPTED_SESSION_ENVELOPE:
                    projection = self._accepted_session_projections.get(key)
                    reference = self._stable_accepted_context_references.get(key)
                    projection_valid = (
                        projection is not None
                        and projection.projection_sha256 == binding.source_sha256
                        and projection.accepted_turn_id == binding.accepted_turn_id
                        and projection.knowledge_owner_id == binding.knowledge_owner_id
                    )
                    reference_valid = (
                        reference is not None
                        and reference.reference_sha256 == binding.source_sha256
                        and reference.accepted_turn_id == binding.accepted_turn_id
                        and reference.knowledge_owner_id == binding.knowledge_owner_id
                        and reference.provider_thread_sha256
                        == binding.provider_thread_sha256
                        and reference.synchronization_receipt_sha256
                        == binding.synchronization_receipt_sha256
                    )
                    if not (projection_valid or reference_valid):
                        raise StateConflictError(
                            "accepted-session binding lacks exact projection or stable-reference custody"
                        )
                resolved.append(binding)
            if not resolved:
                raise StateConflictError("Planner beat has no resolved evidence")
            npc_actors = {
                value
                for value in beat.roles.assertion_owner_ids
                if value != "character:ted"
            }
            active_bindings = [
                value
                for value in resolved
                if value.kind is EvidenceBindingKind.WORLD_RECORD
                and value.authority_classification
                is EvidenceAuthorityClass.ACTIVE_AUTHORITY
            ]
            accepted_session_bindings = [
                value
                for value in resolved
                if value.kind is EvidenceBindingKind.ACCEPTED_SESSION_ENVELOPE
            ]
            if npc_actors and not active_bindings:
                if not accepted_session_bindings or any(
                    value.visibility is EvidenceVisibility.CHARACTER_PRIVATE
                    and (
                        len(npc_actors) != 1
                        or value.knowledge_owner_id != next(iter(npc_actors))
                    )
                    for value in accepted_session_bindings
                ):
                    raise StateConflictError(
                        "hard character decision lacks exact ACTIVE or owner-bound accepted-session authority"
                    )
            private_bindings = [
                value
                for value in resolved
                if value.visibility is EvidenceVisibility.CHARACTER_PRIVATE
            ]
            if private_bindings:
                if len(npc_actors) != 1:
                    raise PermissionError(
                        "private evidence beat must have exactly one NPC actor"
                    )
                actor = next(iter(npc_actors))
                if any(value.knowledge_owner_id != actor for value in private_bindings):
                    raise PermissionError(
                        "private evidence transferred outside its exact actor owner"
                    )
            self._validate_protected_user_claims(beat)
            if beat.protected_user_allowance.source_binding_keys:
                allowance_bindings = []
                for key in beat.protected_user_allowance.source_binding_keys:
                    binding = self._bindings.get(key)
                    if binding is None or key not in beat.source_evidence_bindings:
                        raise PermissionError(
                            "protected-user allowance lacks a cited Python binding"
                        )
                    allowance_bindings.append(binding)
                if (
                    beat.protected_user_allowance.mode
                    is ProtectedUserAllowanceMode.EXACT_SOURCE_ONLY
                    and any(
                        value.kind is not EvidenceBindingKind.CURRENT_USER_SOURCE
                        for value in allowance_bindings
                    )
                ):
                    raise PermissionError(
                        "exact protected-user action lacks current-source authority"
                    )
                if (
                    beat.protected_user_allowance.mode
                    is ProtectedUserAllowanceMode.MINIMAL_NONBRANCHING_CONNECTIVE
                    and any(
                        value.kind
                        is not EvidenceBindingKind.MECHANICAL_CONNECTIVE_ALLOWANCE
                        for value in allowance_bindings
                    )
                ):
                    raise PermissionError(
                        "minimal connective lacks Python mechanical authority"
                    )
            if "character:ted" in beat.roles.assertion_owner_ids and (
                beat.protected_user_allowance.mode
                is not ProtectedUserAllowanceMode.EXACT_SOURCE_ONLY
            ):
                raise PermissionError(
                    "protected-user actor requires exact current-source authority"
                )

    def _validate_protected_user_claims(self, beat: Any) -> None:
        claim_keys = beat.protected_user_allowance.source_claim_keys
        claims: list[ProtectedUserSourceClaimV1] = []
        for key in claim_keys:
            claim = self._protected_user_claims.get(key)
            if claim is None:
                raise PermissionError("protected-user beat cites an unknown source claim")
            if claim.source_binding_key not in beat.source_evidence_bindings:
                raise PermissionError("protected-user source claim binding was not cited")
            claims.append(claim)
        if claims and beat.protected_user_allowance.mode is not ProtectedUserAllowanceMode.EXACT_SOURCE_ONLY:
            raise PermissionError("semantic protected-user claims require exact-source mode")
        if (
            beat.protected_user_allowance.mode
            is ProtectedUserAllowanceMode.EXACT_SOURCE_ONLY
            and not claims
        ):
            raise PermissionError(
                "exact protected-user authority requires a typed source claim"
            )
        if "character:ted" in beat.roles.assertion_owner_ids:
            if len(claims) != 1:
                raise PermissionError(
                    "protected-user actor beat requires one exact supplied event or utterance"
                )
            if _normalized_text(
                beat.observable_action_or_dialogue_direction
            ) != _normalized_text(claims[0].exact_text):
                raise PermissionError(
                    "protected-user actor direction must equal its exact supplied claim"
                )

    def validate_composer_realization(
        self,
        *,
        story_text: str,
        realizations: tuple[ProtectedUserRealizationSpanV1, ...],
        story_segments: tuple[StoryRealizationSegmentV1, ...],
    ) -> None:
        if not story_segments:
            raise PermissionError("Composer omitted the exhaustive story-segment ledger")
        cursor = 0
        pending_segments: dict[str, StoryRealizationSegmentV1] = {}
        protected_segment_spans: dict[tuple[int, int], tuple[str, ...]] = {}
        for segment in story_segments:
            prior = self._story_segments.get(segment.segment_key)
            if (
                segment.segment_key in pending_segments
                or (prior is not None and prior != segment)
            ):
                raise PermissionError("Composer story segment key is duplicated")
            if segment.output_start != cursor or segment.output_end > len(story_text):
                raise PermissionError("Composer story segments are not gap-free")
            if story_text[segment.output_start : segment.output_end] != segment.exact_text:
                raise PermissionError("Composer story segment changed exact output bytes")
            cursor = segment.output_end
            pending_segments[segment.segment_key] = segment
            if (
                re.search(r"\bTed\b", segment.exact_text, re.IGNORECASE)
                and "character:ted"
                not in set(segment.roles.involved_ids)
            ):
                raise PermissionError(
                    "Composer story segment omitted explicit protected-user involvement"
                )
            protected = "character:ted" in segment.roles.assertion_owner_ids
            if protected:
                if len(segment.protected_user_source_claim_keys) != 1:
                    raise PermissionError(
                        "protected-user story segment requires one supplied claim"
                    )
                claim = self._protected_user_claims.get(
                    segment.protected_user_source_claim_keys[0]
                )
                if claim is None or segment.exact_text != claim.exact_text:
                    raise PermissionError(
                        "protected-user story segment invented or paraphrased source"
                    )
                if (
                    segment.kind is StoryRealizationKind.DIALOGUE
                ) != (claim.kind is ProtectedUserSourceClaimKind.DIALOGUE):
                    raise PermissionError(
                        "protected-user story segment changed claim semantics"
                    )
                protected_segment_spans[
                    (segment.output_start, segment.output_end)
                ] = segment.protected_user_source_claim_keys
            elif segment.protected_user_source_claim_keys:
                raise PermissionError(
                    "non-protected story segment carried protected-user claims"
                )
        if cursor != len(story_text):
            raise PermissionError("Composer story segments do not cover complete output")
        observed: dict[tuple[int, int], ProtectedUserRealizationSpanV1] = {}
        for realization in realizations:
            claim = self._protected_user_claims.get(realization.claim_key)
            if claim is None:
                raise PermissionError(
                    "Composer cited an unknown protected-user source claim"
                )
            if realization.kind is not claim.kind or realization.exact_text != claim.exact_text:
                raise PermissionError(
                    "Composer changed protected-user claim kind or exact text"
                )
            if realization.output_end > len(story_text) or story_text[
                realization.output_start : realization.output_end
            ] != realization.exact_text:
                raise PermissionError(
                    "Composer protected-user realization span changed"
                )
            span = (realization.output_start, realization.output_end)
            if span in observed:
                raise PermissionError(
                    "Composer protected-user realization span is duplicated"
                )
            observed[span] = realization
            if protected_segment_spans.get(span) != (realization.claim_key,):
                raise PermissionError(
                    "protected-user exact occurrence disagrees with actor/speaker segment"
                )
        if set(observed) != set(protected_segment_spans):
            raise PermissionError(
                "protected-user realization and story-segment ledgers disagree"
            )
        declared_spans = set(observed)
        for claim in self._protected_user_claims.values():
            start = 0
            while True:
                found = story_text.find(claim.exact_text, start)
                if found < 0:
                    break
                span = (found, found + len(claim.exact_text))
                if span not in declared_spans:
                    raise PermissionError(
                        "Composer copied protected-user source without a typed realization"
                    )
                start = span[1]
        if self._story_segments and self._story_segments != pending_segments:
            raise PermissionError("Composer story-segment ledger changed after validation")
        self._story_segments = pending_segments

    def validate_validator_semantics(
        self,
        *,
        story_text: str,
        story_segments: tuple[StoryRealizationSegmentV1, ...],
        allowed_character_ids: tuple[str, ...],
        reference_only_character_ids: tuple[str, ...] = (),
    ) -> tuple[ProtectedUserRealizationSpanV1, ...]:
        """Mechanically verify Validator semantics against immutable Writer text.

        This method deliberately does not parse names, grammar, pronouns, or prose
        meaning. Semantic classification belongs solely to the Validator.
        """

        if not story_segments:
            raise PermissionError(
                "Semantic Validator omitted the exhaustive story-span ledger"
            )
        cursor = 0
        pending_segments: dict[str, StoryRealizationSegmentV1] = {}
        realizations: list[ProtectedUserRealizationSpanV1] = []
        for segment in story_segments:
            if segment.segment_key in pending_segments:
                raise PermissionError(
                    "Semantic Validator story span key is duplicated"
                )
            if segment.output_start != cursor or segment.output_end > len(story_text):
                raise PermissionError(
                    "Semantic Validator story spans are not gap-free"
                )
            if story_text[segment.output_start : segment.output_end] != segment.exact_text:
                raise PermissionError(
                    "Semantic Validator story span changed exact Writer text"
                )
            _validate_reference_only_roles(
                roles=segment.roles,
                allowed_character_ids=allowed_character_ids,
                reference_only_character_ids=reference_only_character_ids,
                error_prefix="Semantic Validator",
            )
            cursor = segment.output_end
            pending_segments[segment.segment_key] = segment
            protected = "character:ted" in segment.roles.assertion_owner_ids
            if protected:
                if len(segment.protected_user_source_claim_keys) != 1:
                    raise PermissionError(
                        "protected-user Validator span requires one supplied claim"
                    )
                claim = self._protected_user_claims.get(
                    segment.protected_user_source_claim_keys[0]
                )
                if claim is None or segment.exact_text != claim.exact_text:
                    raise PermissionError(
                        "protected-user Validator span invented or paraphrased source"
                    )
                if (
                    segment.kind is StoryRealizationKind.DIALOGUE
                ) != (claim.kind is ProtectedUserSourceClaimKind.DIALOGUE):
                    raise PermissionError(
                        "protected-user Validator span changed claim semantics"
                    )
                realizations.append(
                    ProtectedUserRealizationSpanV1(
                        schema_version=ProtectedUserRealizationSpanV1.SCHEMA_VERSION,
                        claim_key=claim.claim_key,
                        kind=claim.kind,
                        output_start=segment.output_start,
                        output_end=segment.output_end,
                        exact_text=segment.exact_text,
                    )
                )
            elif segment.protected_user_source_claim_keys:
                raise PermissionError(
                    "non-protected Validator span carried protected-user claims"
                )
        if cursor != len(story_text):
            raise PermissionError(
                "Semantic Validator story spans do not cover complete Writer text"
            )
        if self._story_segments and self._story_segments != pending_segments:
            raise PermissionError(
                "Semantic Validator story-span ledger changed after validation"
            )
        self._story_segments = pending_segments
        return tuple(realizations)

    def validate_validator_realization_boundary(
        self,
        *,
        story_text: str,
        story_segments: tuple[StoryRealizationSegmentV1, ...],
        presentation_segments: tuple[PresentationRealizationSegmentV1, ...],
        package: ValidatorFinalizationPackageV1,
        presentation_adjudications: tuple[
            ProtectedSemanticAdjudicationV1, ...
        ],
        allowed_character_ids: tuple[str, ...],
        reference_only_character_ids: tuple[str, ...] = (),
        source_grounded_public_state_receipts: tuple[
            SourceGroundedPublicStateReceiptV1, ...
        ] = (),
    ) -> tuple[ProtectedUserRealizationSpanV1, ...]:
        """Validate complete bytes, then retain only story/material authority."""

        material_keys = {value.segment_key for value in story_segments}
        presentation_keys = {value.segment_key for value in presentation_segments}
        if material_keys & presentation_keys:
            raise PermissionError(
                "presentation and story/material segment keys overlap"
            )
        if any(value.roles.is_empty for value in story_segments):
            raise PermissionError(
                "actorless presentation escaped into story/material authority"
            )
        ephemeral_presentation = tuple(
            StoryRealizationSegmentV1(
                schema_version=StoryRealizationSegmentV1.SCHEMA_VERSION,
                segment_key=value.segment_key,
                kind=value.kind,
                output_start=value.output_start,
                output_end=value.output_end,
                exact_text=value.exact_text,
                roles=value.roles,
                protected_user_source_claim_keys=(),
            )
            for value in presentation_segments
        )
        complete_segments = tuple(
            sorted(
                (*story_segments, *ephemeral_presentation),
                key=lambda value: value.output_start,
            )
        )
        material_adjudications = package.protected_semantic_adjudications
        if {value.segment_key for value in material_adjudications} != material_keys:
            raise PermissionError(
                "finalization carried presentation or omitted material adjudication"
            )
        if {value.segment_key for value in presentation_adjudications} != presentation_keys:
            raise PermissionError(
                "presentation adjudications do not cover presentation spans"
            )
        realizations = self.validate_validator_semantics(
            story_text=story_text,
            story_segments=complete_segments,
            allowed_character_ids=allowed_character_ids,
            reference_only_character_ids=reference_only_character_ids,
        )
        complete_package = replace(
            package,
            protected_semantic_adjudications=(
                *material_adjudications,
                *presentation_adjudications,
            ),
        )
        self._validate_protected_semantics(
            complete_package,
            presentation_segments=presentation_segments,
            source_grounded_public_state_receipts=(
                source_grounded_public_state_receipts
            ),
        )
        self._story_segments = {
            value.segment_key: value for value in story_segments
        }
        if presentation_keys & set(self._story_segments):
            raise PermissionError(
                "presentation detail leaked into accepted story authority"
            )
        return realizations

    def validate_validator_diagnostics(
        self,
        *,
        story_text: str,
        diagnostic_story_segments: tuple[DiagnosticStorySegmentV1, ...],
        diagnostic_protected_semantic_adjudications: tuple[
            DiagnosticProtectedSemanticAdjudicationV1, ...
        ],
        allowed_character_ids: tuple[str, ...],
        reference_only_character_ids: tuple[str, ...] = (),
    ) -> None:
        """Validate rejected-only evidence without adding it to authority state."""

        if not diagnostic_story_segments:
            raise PermissionError(
                "Semantic Validator omitted rejected diagnostic story spans"
            )
        cursor = 0
        segments: dict[str, DiagnosticStorySegmentV1] = {}
        for segment in diagnostic_story_segments:
            if segment.segment_key in segments:
                raise PermissionError(
                    "Semantic Validator diagnostic story span key is duplicated"
                )
            if segment.output_start != cursor or segment.output_end > len(story_text):
                raise PermissionError(
                    "Semantic Validator diagnostic spans are not gap-free"
                )
            if (
                story_text[segment.output_start : segment.output_end]
                != segment.exact_text
                or segment.exact_text_sha256 != text_sha256(segment.exact_text)
            ):
                raise PermissionError(
                    "Semantic Validator diagnostic span changed exact Writer text"
                )
            _validate_reference_only_roles(
                roles=segment.roles,
                allowed_character_ids=allowed_character_ids,
                reference_only_character_ids=reference_only_character_ids,
                error_prefix="Semantic Validator diagnostic",
            )
            protected = "character:ted" in segment.roles.assertion_owner_ids
            if (
                segment.grounding_status
                is DiagnosticGroundingStatus.UNGROUNDED_PROTECTED_USER_ASSERTION
            ):
                if not protected or segment.protected_user_source_claim_keys:
                    raise PermissionError(
                        "ungrounded diagnostic does not describe a zero-claim Ted assertion"
                    )
            elif protected:
                if len(segment.protected_user_source_claim_keys) != 1:
                    raise PermissionError(
                        "grounded protected diagnostic requires one supplied claim"
                    )
                claim = self._protected_user_claims.get(
                    segment.protected_user_source_claim_keys[0]
                )
                if claim is None:
                    raise PermissionError(
                        "grounded protected diagnostic lacks a supplied ingress citation"
                    )
                if (
                    segment.kind is StoryRealizationKind.DIALOGUE
                ) != (claim.kind is ProtectedUserSourceClaimKind.DIALOGUE):
                    raise PermissionError(
                        "grounded protected diagnostic changed claim semantics"
                    )
            cursor = segment.output_end
            segments[segment.segment_key] = segment
        if cursor != len(story_text):
            raise PermissionError(
                "Semantic Validator diagnostic spans do not cover complete Writer text"
            )
        adjudications = {
            value.segment_key: value
            for value in diagnostic_protected_semantic_adjudications
        }
        if (
            len(adjudications) != len(
                diagnostic_protected_semantic_adjudications
            )
            or set(adjudications) != set(segments)
        ):
            raise PermissionError(
                "Semantic Validator did not adjudicate every diagnostic span"
            )
        relation_fields = {
            ProtectedSemanticRelationKind.AFFECTED_BY_NPC: "affected_ids",
            ProtectedSemanticRelationKind.ADDRESSED_BY_NPC: "addressed_ids",
            ProtectedSemanticRelationKind.OBSERVED_BY_NPC: "observing_ids",
            ProtectedSemanticRelationKind.REFERENCED_ONLY_BY_NPC: "referenced_ids",
        }
        for segment_key, segment in segments.items():
            adjudication = adjudications[segment_key]
            if (
                adjudication.protected_user_id != "character:ted"
                or adjudication.output_start != segment.output_start
                or adjudication.output_end != segment.output_end
                or adjudication.exact_text_sha256
                != segment.exact_text_sha256
                or adjudication.grounding_status is not segment.grounding_status
            ):
                raise PermissionError(
                    "diagnostic adjudication changed its exact Writer span"
                )
            protected = "character:ted" in segment.roles.assertion_owner_ids
            npc_owners = tuple(
                value
                for value in segment.roles.assertion_owner_ids
                if value != "character:ted"
            )
            if adjudication.relation is ProtectedSemanticRelationKind.PROTECTED_ASSERTION:
                if (
                    not protected
                    or adjudication.npc_assertion_owner_ids
                    or adjudication.protected_user_source_claim_keys
                    != segment.protected_user_source_claim_keys
                ):
                    raise PermissionError(
                        "diagnostic protected assertion disagrees with span roles"
                    )
                continue
            if protected:
                raise PermissionError(
                    "diagnostic Ted assertion was not classified as protected"
                )
            if adjudication.relation is ProtectedSemanticRelationKind.NONE:
                if "character:ted" in segment.roles.involved_ids:
                    raise PermissionError(
                        "diagnostic protected-user role was mislabeled as absent"
                    )
                continue
            if (
                adjudication.relation
                is ProtectedSemanticRelationKind.NEUTRAL_PRESENTATION_REFERENCE
            ):
                ted_role_fields = tuple(
                    field
                    for field in (
                        "action_owner_ids",
                        "state_owner_ids",
                        "speaker_ids",
                        "affected_ids",
                        "addressed_ids",
                        "observing_ids",
                        "referenced_ids",
                    )
                    if "character:ted" in getattr(segment.roles, field)
                )
                if (
                    segment.kind is not StoryRealizationKind.NARRATION
                    or ted_role_fields != ("referenced_ids",)
                    or segment.roles.assertion_owner_ids
                    or adjudication.npc_assertion_owner_ids
                    or adjudication.protected_user_source_claim_keys
                ):
                    raise PermissionError(
                        "diagnostic neutral protected reference changed its closed narration roles"
                    )
                continue
            role_field = relation_fields[adjudication.relation]
            ted_role_fields = tuple(
                field
                for field in relation_fields.values()
                if "character:ted" in getattr(segment.roles, field)
            )
            if (
                ted_role_fields != (role_field,)
                or set(adjudication.npc_assertion_owner_ids) != set(npc_owners)
                or not npc_owners
            ):
                raise PermissionError(
                    "diagnostic non-owning relation lacks an NPC predicate owner"
                )

        # Deliberately do not assign ``self._story_segments``.  Diagnostic
        # evidence is rejected-candidate provenance, never accepted authority.

    def validate_traceability(
        self,
        sequence: RichPlannerSequenceV1,
        package: ValidatorFinalizationPackageV1,
        *,
        branch_root: Path | None = None,
    ) -> None:
        if package.complete_final_sequence is None:
            return
        self._validate_protected_semantics(package)
        beats = {value.beat_key: value for value in sequence.beats}
        items = {value.item_key: value for value in package.complete_final_sequence.items}
        cited_story_segments: set[str] = set()
        for item in items.values():
            for beat_key in item.planner_beat_keys:
                if beat_key not in beats:
                    raise StateConflictError("final sequence cites an unknown Planner beat")
            allowed_claim_keys = {
                claim_key
                for beat_key in item.planner_beat_keys
                for claim_key in beats[beat_key].protected_user_allowance.source_claim_keys
            }
            if not set(item.protected_user_source_claim_keys).issubset(
                allowed_claim_keys
            ):
                raise StateConflictError(
                    "final sequence used a protected-user claim outside its Planner allowance"
                )
            for beat_key in item.planner_beat_keys:
                if not beats[beat_key].source_evidence_bindings:
                    raise StateConflictError("final sequence traces to an ungrounded Planner beat")
            segments = []
            for segment_key in item.story_segment_keys:
                segment = self._story_segments.get(segment_key)
                if segment is None:
                    raise StateConflictError(
                        "final sequence cites an unknown Validator story segment"
                    )
                segments.append(segment)
                cited_story_segments.add(segment_key)
            segment_by_key = {segment.segment_key: segment for segment in segments}
            for scope in item.field_scopes:
                scoped_segments = []
                for segment_key in scope.story_segment_keys:
                    segment = segment_by_key.get(segment_key)
                    if segment is None:
                        raise StateConflictError(
                            "final field scope cites a segment outside its item"
                        )
                    scoped_segments.append(segment)
                role_fields = (
                    "action_owner_ids",
                    "state_owner_ids",
                    "speaker_ids",
                    "affected_ids",
                    "addressed_ids",
                    "observing_ids",
                    "referenced_ids",
                )
                scoped_roles = {
                    field: {
                        identity
                        for segment in scoped_segments
                        for identity in getattr(segment.roles, field)
                    }
                    for field in role_fields
                }
                scoped_claims = {
                    claim
                    for segment in scoped_segments
                    for claim in segment.protected_user_source_claim_keys
                }
                if (
                    any(
                        set(getattr(scope.roles, field)) != scoped_roles[field]
                        for field in role_fields
                    )
                    or set(scope.protected_user_source_claim_keys) != scoped_claims
                ):
                    raise StateConflictError(
                        "final field changed Validator role or claim ownership"
                    )
                raw_values = getattr(item, scope.field_name)
                field_values = raw_values if isinstance(raw_values, tuple) else (raw_values,)
                if "character:ted" in scope.roles.assertion_owner_ids:
                    exact_claim_texts = {
                        self._protected_user_claims[key].exact_text
                        for key in scope.protected_user_source_claim_keys
                        if key in self._protected_user_claims
                    }
                    if not exact_claim_texts or any(
                        value not in exact_claim_texts for value in field_values
                    ):
                        raise StateConflictError(
                            "protected-user final field invented or paraphrased supplied content"
                        )
            expected_roles = {
                field: {
                    identity
                    for segment in segments
                    for identity in getattr(segment.roles, field)
                }
                for field in role_fields
            }
            expected_segment_claims = {
                claim
                for segment in segments
                for claim in segment.protected_user_source_claim_keys
            }
            if (
                any(
                    set(getattr(item.roles, field)) != expected_roles[field]
                    for field in role_fields
                )
                or set(item.protected_user_source_claim_keys)
                != expected_segment_claims
            ):
                raise StateConflictError(
                    "final sequence changed Validator role or claim ownership"
                )
        if cited_story_segments != set(self._story_segments):
            raise StateConflictError(
                "complete final sequence does not cover every Validator story segment"
            )
        for operation in package.world_edit_operations:
            item = items.get(operation.source_final_sequence_item)
            if item is None or not item.planner_beat_keys:
                raise StateConflictError("world edit has no evidence-grounded final-sequence source")
            scope = next(
                (
                    value
                    for value in item.field_scopes
                    if value.field_name == operation.source_final_field_name
                ),
                None,
            )
            source_value = getattr(item, operation.source_final_field_name)
            source_values = source_value if isinstance(source_value, tuple) else (source_value,)
            if (
                scope is None
                or set(operation.protected_user_source_claim_keys)
                != set(scope.protected_user_source_claim_keys)
                or operation.value not in source_values
                or operation.reason
                != f"Persist accepted final field {operation.source_final_field_name}."
            ):
                raise StateConflictError(
                    "world edit changed final-field value, reason, or claim provenance"
                )
            directive = next(
                (
                    value
                    for value in scope.persistence_directives
                    if value.directive_key == operation.persistence_directive_key
                ),
                None,
            )
            if directive is None:
                raise StateConflictError(
                    "world edit lacks an exact field-level persistence directive"
                )
            if branch_root is None:
                raise StateConflictError(
                    "persistence target validation lacks the exact branch root"
                )
            self._validate_persistence_target(
                branch_root=branch_root,
                item=item,
                scope=scope,
                directive=directive,
            )
        for created in package.created_field_log:
            item = items.get(created.source_final_sequence_item)
            scope = next(
                (
                    value
                    for value in item.field_scopes
                    if value.field_name == created.source_final_field_name
                ),
                None,
            ) if item is not None else None
            source_value = (
                getattr(item, created.source_final_field_name)
                if item is not None
                else None
            )
            source_values = source_value if isinstance(source_value, tuple) else (source_value,)
            if (
                item is None
                or scope is None
                or set(created.protected_user_source_claim_keys)
                != set(scope.protected_user_source_claim_keys)
                or created.value not in source_values
                or created.reason
                != f"Persist accepted final field {created.source_final_field_name}."
            ):
                raise StateConflictError(
                    "created field changed final-field value, reason, or claim provenance"
                )
        event_claims = {
            claim
            for item in items.values()
            for claim in item.protected_user_source_claim_keys
        }
        if package.event_record is None or set(
            package.event_record.protected_user_source_claim_keys
        ) != event_claims:
            raise StateConflictError("event changed protected-user claim provenance")
        if package.event_record.final_sequence_item_keys != tuple(items):
            raise StateConflictError("event changed final-sequence item provenance")
        expected_event_summary = " ".join(
            items[key].realized_event
            for key in package.event_record.final_sequence_item_keys
        )
        if package.event_record.summary != expected_event_summary:
            raise StateConflictError("event summary changed final-sequence facts")

    def _validate_protected_semantics(
        self,
        package: ValidatorFinalizationPackageV1,
        *,
        presentation_segments: tuple[PresentationRealizationSegmentV1, ...] = (),
        source_grounded_public_state_receipts: tuple[
            SourceGroundedPublicStateReceiptV1, ...
        ] = (),
    ) -> None:
        adjudications = {
            value.segment_key: value
            for value in package.protected_semantic_adjudications
        }
        if set(adjudications) != set(self._story_segments):
            raise StateConflictError(
                "Validator did not adjudicate every exact Writer span"
            )
        relation_fields = {
            ProtectedSemanticRelationKind.AFFECTED_BY_NPC: "affected_ids",
            ProtectedSemanticRelationKind.ADDRESSED_BY_NPC: "addressed_ids",
            ProtectedSemanticRelationKind.OBSERVED_BY_NPC: "observing_ids",
            ProtectedSemanticRelationKind.REFERENCED_ONLY_BY_NPC: "referenced_ids",
        }
        presentation_by_key = {
            value.segment_key: value for value in presentation_segments
        }
        source_grounding_by_key = {
            value.segment_key: value
            for value in source_grounded_public_state_receipts
        }
        if len(source_grounding_by_key) != len(
            source_grounded_public_state_receipts
        ) or not set(source_grounding_by_key).issubset(presentation_by_key):
            raise StateConflictError(
                "source-grounded public state is duplicated or not presentation-only"
            )
        if any(
            key not in adjudications
            or adjudications[key].relation
            is not ProtectedSemanticRelationKind.NEUTRAL_PRESENTATION_REFERENCE
            for key in source_grounding_by_key
        ):
            raise StateConflictError(
                "source-grounded public state changed its protected relation"
            )
        for segment_key, segment in self._story_segments.items():
            adjudication = adjudications[segment_key]
            source_grounding = source_grounding_by_key.get(segment_key)
            if (
                adjudication.protected_user_id != "character:ted"
                or adjudication.output_start != segment.output_start
                or adjudication.output_end != segment.output_end
                or adjudication.exact_text_sha256 != text_sha256(segment.exact_text)
            ):
                raise StateConflictError(
                    "protected semantic adjudication changed the exact Writer span"
                )
            npc_owners = tuple(
                value
                for value in segment.roles.assertion_owner_ids
                if value != adjudication.protected_user_id
            )
            if adjudication.relation is ProtectedSemanticRelationKind.PROTECTED_ASSERTION:
                if (
                    adjudication.protected_user_id
                    not in segment.roles.assertion_owner_ids
                    or adjudication.npc_assertion_owner_ids
                    or adjudication.protected_user_source_claim_keys
                    != segment.protected_user_source_claim_keys
                ):
                    raise StateConflictError(
                        "protected-user assertion disagrees with Validator roles"
                    )
                claim = self._protected_user_claims.get(
                    adjudication.protected_user_source_claim_keys[0]
                )
                if claim is None or segment.exact_text != claim.exact_text:
                    raise StateConflictError(
                        "protected semantic assertion lacks exact ingress authority"
                    )
                continue
            if adjudication.relation is ProtectedSemanticRelationKind.NONE:
                if (
                    adjudication.protected_user_id in segment.roles.involved_ids
                ):
                    raise StateConflictError(
                        "protected-user role was mislabeled as absent"
                    )
                continue
            if (
                adjudication.relation
                is ProtectedSemanticRelationKind.NEUTRAL_PRESENTATION_REFERENCE
            ):
                presentation = presentation_by_key.get(segment_key)
                ted_role_fields = tuple(
                    field
                    for field in (
                        "action_owner_ids",
                        "state_owner_ids",
                        "speaker_ids",
                        "affected_ids",
                        "addressed_ids",
                        "observing_ids",
                        "referenced_ids",
                    )
                    if adjudication.protected_user_id
                    in getattr(segment.roles, field)
                )
                if source_grounding is not None:
                    source_unit = self._source_units.get(
                        source_grounding.source_unit_key
                    )
                    if (
                        presentation is None
                        or presentation.presentation_class
                        is not PresentationRealizationClass.NONPERSISTENT_SPATIAL_PHRASING
                        or segment.kind is not StoryRealizationKind.NARRATION
                        or ted_role_fields != ("referenced_ids",)
                        or segment.roles.assertion_owner_ids
                        or adjudication.npc_assertion_owner_ids
                        or adjudication.protected_user_source_claim_keys
                        or source_unit is None
                        or source_grounding.adjudication_key
                        != adjudication.adjudication_key
                        or source_grounding.output_start
                        != adjudication.output_start
                        or source_grounding.output_end != adjudication.output_end
                        or source_grounding.exact_text_sha256
                        != adjudication.exact_text_sha256
                        or source_grounding.protected_user_id
                        != adjudication.protected_user_id
                    ):
                        raise StateConflictError(
                            "source-grounded public state lacks exact current-source presentation custody"
                        )
                    continue
                if (
                    presentation is None
                    or presentation.presentation_class
                    is not PresentationRealizationClass.NONPERSISTENT_ATMOSPHERE
                    or segment.kind is not StoryRealizationKind.NARRATION
                    or ted_role_fields != ("referenced_ids",)
                    or segment.roles.assertion_owner_ids
                    or adjudication.npc_assertion_owner_ids
                    or adjudication.protected_user_source_claim_keys
                ):
                    raise StateConflictError(
                        "neutral protected reference escaped nonpersistent presentation narration"
                    )
                continue
            role_field = relation_fields[adjudication.relation]
            ted_role_fields = tuple(
                field
                for field in (
                    "affected_ids",
                    "addressed_ids",
                    "observing_ids",
                    "referenced_ids",
                )
                if adjudication.protected_user_id in getattr(segment.roles, field)
            )
            if (
                ted_role_fields != (role_field,)
                or adjudication.protected_user_id
                in segment.roles.assertion_owner_ids
                or set(adjudication.npc_assertion_owner_ids) != set(npc_owners)
                or not npc_owners
            ):
                raise StateConflictError(
                    "non-owning protected relation lacks an exact NPC-owned predicate"
                )

    @staticmethod
    def _validate_persistence_target(
        *, branch_root: Path,
        item: FinalSequenceItemV1,
        scope: Any,
        directive: Any,
    ) -> None:
        validate_persistence_field_path(
            directive.target_record_class,
            directive.field_path,
        )
        _target, target_text = _read_branch_file_no_follow(
            branch_root, "ACTIVE/" + directive.target_file.replace("\\", "/")
        )
        payload = json.loads(target_text)
        if not isinstance(payload, dict):
            raise StateConflictError(
                "persistence directive target is not a mutable semantic record"
            )
        if payload.get("_cera_revision") != directive.expected_file_revision:
            raise StateConflictError("persistence directive revision is stale")
        identity_fields = {
            PersistenceRecordClass.CHARACTER: "character_id",
            PersistenceRecordClass.RELATIONSHIP: "relationship_id",
            PersistenceRecordClass.RULE: "rule_id",
            PersistenceRecordClass.LOCATION: "location_id",
            PersistenceRecordClass.EVENT: "event_id",
            PersistenceRecordClass.SCENE: "scene_id",
        }
        if payload.get(identity_fields[directive.target_record_class]) != (
            directive.target_record_id
        ):
            raise StateConflictError(
                "persistence directive targets the wrong record identity"
            )
        subjects = set(directive.target_subject_ids)
        if directive.target_record_class is PersistenceRecordClass.CHARACTER:
            if subjects != {directive.target_record_id} or not subjects.issubset(
                set(scope.roles.involved_ids)
            ):
                raise StateConflictError(
                    "character persistence targets the wrong character"
                )
            if (
                scope.visibility is FinalInformationVisibility.CHARACTER_PRIVATE
                and scope.knowledge_owner_id != directive.target_record_id
            ):
                raise StateConflictError(
                    "private character persistence targets another owner"
                )
        elif directive.target_record_class is PersistenceRecordClass.RELATIONSHIP:
            participants = payload.get("participant_ids")
            involved = set(scope.roles.involved_ids)
            if (
                not isinstance(participants, list)
                or len(participants) != 2
                or len(subjects) != 2
                or set(participants) != subjects
                or not subjects.issubset(involved)
            ):
                raise StateConflictError(
                    "relationship persistence participants are not justified by the final field"
                )
            if (
                scope.visibility is FinalInformationVisibility.CHARACTER_PRIVATE
                and (
                    scope.knowledge_owner_id not in subjects
                    or scope.knowledge_owner_id not in involved
                )
            ):
                raise StateConflictError(
                    "private relationship persistence changed owner scope"
                )
        prior_found, prior = _try_json_pointer(payload, directive.field_path)
        if directive.operation.value == "add":
            if prior_found:
                raise StateConflictError(
                    "add persistence directive target already exists"
                )
        else:
            if (
                not prior_found
                or canonical_sha256(prior)
                != directive.expected_prior_value_sha256
            ):
                raise StateConflictError(
                    "replace persistence directive prior value changed"
                )

    def _validate_world_binding(self, binding: RequestEvidenceBindingV1, branch_root: Path) -> None:
        assert binding.relative_path is not None
        target, text = _read_branch_file_no_follow(
            branch_root, binding.relative_path
        )
        if text_sha256(text) != binding.source_sha256:
            raise StateConflictError("evidence binding content is stale")
        if target.suffix.casefold() == ".json":
            payload = json.loads(text)
            revision = payload.get("_cera_revision") if isinstance(payload, dict) else None
            if revision != binding.record_revision:
                raise StateConflictError("evidence binding revision is stale")

    @property
    def registry_sha256(self) -> str:
        return domain_sha256(
            self.SCHEMA_VERSION,
            {
                "world_id": self.world_id,
                "branch_id": self.branch_id,
                "turn_id": self.turn_id,
                "bindings": self.bindings,
                "source_units": tuple(
                    self._source_units[key] for key in sorted(self._source_units)
                ),
                "protected_user_claims": tuple(
                    self._protected_user_claims[key]
                    for key in sorted(self._protected_user_claims)
                ),
                "story_segments": tuple(
                    self._story_segments[key] for key in sorted(self._story_segments)
                ),
                "accepted_session_projections": tuple(
                    self._accepted_session_projections[key]
                    for key in sorted(self._accepted_session_projections)
                ),
            },
        )


def build_character_summary_envelope(
    *,
    branch_root: Path,
    source_path: str,
    character_id: str,
) -> CharacterSummaryEnvelopeV1:
    """Build, rather than trust, a summary envelope from one exact record."""

    normalized = source_path.replace("\\", "/")
    if not normalized.startswith("ACTIVE/"):
        raise ContractValidationError(
            "character summary source must be an ACTIVE authoritative record"
        )
    target, text = _read_branch_file_no_follow(branch_root, normalized)
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise ContractValidationError("character summary source must be a JSON object")
    if payload.get("character_id") != character_id:
        raise StateConflictError("character summary source belongs to another character")
    revision = payload.get("_cera_revision")
    if type(revision) is not int or revision < 1:
        raise ContractValidationError("character summary source revision is invalid")
    authority = "active_authoritative_record_fields"
    summary_pointer = "/reasoning_summary"
    changes_pointer = "/latest_accepted_changes"
    summary = _json_pointer_value(payload, summary_pointer)
    changes = _json_pointer_value(payload, changes_pointer)
    if not isinstance(summary, str) or not summary.strip():
        raise ContractValidationError("character summary field is absent")
    if not isinstance(changes, list) or not all(
        isinstance(value, str) and value.strip() for value in changes
    ):
        raise ContractValidationError("character latest-change field is invalid")
    source_sha256 = text_sha256(text)
    receipt = _character_summary_receipt(
        character_id=character_id,
        source_path=normalized,
        source_revision=revision,
        source_sha256=source_sha256,
        authority=authority,
        summary_pointer=summary_pointer,
        changes_pointer=changes_pointer,
        summary=summary,
        changes=tuple(changes),
    )
    envelope = CharacterSummaryEnvelopeV1(
        schema_version=CharacterSummaryEnvelopeV1.SCHEMA_VERSION,
        character_id=character_id,
        source_path_or_record_id=normalized,
        source_revision=revision,
        source_sha256=source_sha256,
        source_authority_classification=authority,
        summary_field_path=summary_pointer,
        latest_changes_field_path=changes_pointer,
        latest_accepted_changes=tuple(changes),
        summary=summary,
        derivation_receipt_sha256=receipt,
    )
    validate_character_summary_envelope(branch_root=branch_root, envelope=envelope)
    return envelope


def validate_character_summary_envelope(
    *,
    branch_root: Path,
    envelope: CharacterSummaryEnvelopeV1,
) -> Path:
    normalized = envelope.source_path_or_record_id.replace("\\", "/")
    target, text = _read_branch_file_no_follow(branch_root, normalized)
    if text_sha256(text) != envelope.source_sha256:
        raise StateConflictError("character summary source hash is stale")
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise ContractValidationError("character summary source must be an object")
    if payload.get("_cera_revision") != envelope.source_revision:
        raise StateConflictError("character summary source revision is stale")
    if payload.get("character_id") != envelope.character_id:
        raise StateConflictError("character summary identity does not match its source")
    expected_authority = (
        "active_authoritative_record_fields" if normalized.startswith("ACTIVE/") else None
    )
    if expected_authority != envelope.source_authority_classification:
        raise StateConflictError("character summary authority/path classification changed")
    if _json_pointer_value(payload, envelope.summary_field_path) != envelope.summary:
        raise StateConflictError("character summary text was not derived from its exact source")
    changes = _json_pointer_value(payload, envelope.latest_changes_field_path)
    if not isinstance(changes, list) or tuple(changes) != envelope.latest_accepted_changes:
        raise StateConflictError(
            "character latest changes were not derived from their exact source"
        )
    expected_receipt = _character_summary_receipt(
        character_id=envelope.character_id,
        source_path=normalized,
        source_revision=envelope.source_revision,
        source_sha256=envelope.source_sha256,
        authority=envelope.source_authority_classification,
        summary_pointer=envelope.summary_field_path,
        changes_pointer=envelope.latest_changes_field_path,
        summary=envelope.summary,
        changes=envelope.latest_accepted_changes,
    )
    if expected_receipt != envelope.derivation_receipt_sha256:
        raise StateConflictError("character summary derivation receipt changed")
    return target


def bind_character_summary_envelopes(
    *,
    registry: RequestEvidenceBindingRegistry,
    branch_root: Path,
    summaries: Iterable[CharacterSummaryEnvelopeV1],
) -> tuple[dict[str, Any], ...]:
    """Validate and bind exact character summaries through one shared path."""

    bindings: list[dict[str, Any]] = []
    for summary in summaries:
        if summary.source_path_or_record_id.startswith("record:"):
            raise StateConflictError(
                "continuous character summary requires a revision-bound world path"
            )
        summary_path = validate_character_summary_envelope(
            branch_root=branch_root,
            envelope=summary,
        )
        relative_path = summary_path.relative_to(branch_root).as_posix()
        binding = registry.allocate_initial_projection(
            branch_root=branch_root,
            relative_path=relative_path,
            record_type=(
                "characters"
                if relative_path.startswith("ACTIVE/Characters/")
                else "character_summaries"
            ),
            visibility=EvidenceVisibility.CHARACTER_PRIVATE,
            knowledge_owner_id=summary.character_id,
        )
        bindings.append(
            {
                "character_id": summary.character_id,
                "binding_key": binding.binding_key,
                "source_path": binding.relative_path,
                "source_revision": binding.record_revision,
                "source_sha256": binding.source_sha256,
            }
        )
    return tuple(bindings)


def _normalized_text(value: str) -> str:
    return " ".join(value.casefold().split())


def _character_summary_receipt(
    *,
    character_id: str,
    source_path: str,
    source_revision: int,
    source_sha256: str,
    authority: str,
    summary_pointer: str,
    changes_pointer: str,
    summary: str,
    changes: tuple[str, ...],
) -> str:
    return canonical_sha256(
        {
            "schema_version": CharacterSummaryEnvelopeV1.SCHEMA_VERSION,
            "character_id": character_id,
            "source_path_or_record_id": source_path,
            "source_revision": source_revision,
            "source_sha256": source_sha256,
            "source_authority_classification": authority,
            "summary_field_path": summary_pointer,
            "latest_changes_field_path": changes_pointer,
            "summary": summary,
            "latest_accepted_changes": changes,
        }
    )


def _json_pointer_value(payload: Any, pointer: str) -> Any:
    if not pointer.startswith("/") or pointer == "/":
        raise ContractValidationError("character summary field pointer is invalid")
    current = payload
    for encoded in pointer[1:].split("/"):
        key = encoded.replace("~1", "/").replace("~0", "~")
        if not isinstance(current, dict) or key not in current:
            raise StateConflictError("character summary field pointer is unavailable")
        current = current[key]
    return current


def _try_json_pointer(payload: Any, pointer: str) -> tuple[bool, Any]:
    if not pointer.startswith("/") or pointer == "/":
        raise ContractValidationError("persistence field pointer is invalid")
    current = payload
    for encoded in pointer[1:].split("/"):
        key = encoded.replace("~1", "/").replace("~0", "~")
        if not isinstance(current, dict) or key not in current:
            return False, None
        current = current[key]
    return True, current
