"""Request-local authoritative evidence bindings for continuous planning."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import json
from pathlib import Path
import re
from typing import Any, ClassVar, Iterable

from cera.errors import ContractValidationError, StateConflictError
from cera.serialization import canonical_sha256, domain_sha256, re_is_sha256, text_sha256

from .contracts import (
    CharacterRoleLedgerV1,
    CharacterSummaryEnvelopeV1,
    FinalInformationVisibility,
    FinalSequenceItemV1,
    IngressSourceUnitKind,
    IngressSourceUnitV1,
    PersistenceRecordClass,
    ProtectedUserAllowanceMode,
    ProtectedSemanticRelationKind,
    ProtectedUserRealizationSpanV1,
    ProtectedUserSourceClaimKind,
    ProtectedUserSourceClaimV1,
    RichPlannerSequenceV1,
    StoryRealizationKind,
    StoryRealizationSegmentV1,
    ValidatorFinalizationPackageV1,
)
from .record_policy import validate_persistence_field_path


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

    SCHEMA_VERSION = "cera.request_evidence_binding_registry.v9"

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

    def allocate_initial_projection(
        self,
        *,
        branch_root: Path,
        relative_path: str,
        record_type: str,
        visibility: EvidenceVisibility,
        knowledge_owner_id: str | None,
    ) -> RequestEvidenceBindingV1:
        target = (branch_root / relative_path).resolve()
        if branch_root.resolve() not in target.parents or not target.is_file() or target.is_symlink():
            raise StateConflictError("initial evidence projection is unavailable or escaped branch")
        text = target.read_text(encoding="utf-8")
        revision = None
        if target.suffix.casefold() == ".json":
            payload = json.loads(text)
            revision = payload.get("_cera_revision") if isinstance(payload, dict) else None
        return self.allocate_world_record(
            relative_path=target.relative_to(branch_root).as_posix(),
            source_sha256=text_sha256(text),
            record_revision=revision,
            record_type=record_type,
            visibility=visibility,
            knowledge_owner_id=knowledge_owner_id,
            exact_read_operation_sha256=canonical_sha256(
                {
                    "operation": "deterministic_initial_projection",
                    "relative_path": target.relative_to(branch_root).as_posix(),
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
            }
            for value in self.bindings
        )

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
                    if (
                        projection is None
                        or projection.projection_sha256 != binding.source_sha256
                        or projection.accepted_turn_id != binding.accepted_turn_id
                        or projection.knowledge_owner_id != binding.knowledge_owner_id
                    ):
                        raise StateConflictError(
                            "accepted-session binding lacks its exact scoped projection"
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
                if len(npc_actors) != 1 or not any(
                    value.visibility is EvidenceVisibility.CHARACTER_PRIVATE
                    and value.knowledge_owner_id == next(iter(npc_actors))
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
            expected_claim_keys = {
                claim_key
                for beat_key in item.planner_beat_keys
                for claim_key in beats[beat_key].protected_user_allowance.source_claim_keys
            }
            if set(item.protected_user_source_claim_keys) != expected_claim_keys:
                raise StateConflictError(
                    "final sequence changed protected-user claim provenance"
                )
            for beat_key in item.planner_beat_keys:
                if not beats[beat_key].source_evidence_bindings:
                    raise StateConflictError("final sequence traces to an ungrounded Planner beat")
            segments = []
            for segment_key in item.story_segment_keys:
                segment = self._story_segments.get(segment_key)
                if segment is None:
                    raise StateConflictError(
                        "final sequence cites an unknown Composer story segment"
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
                        "final field changed Composer role or claim ownership"
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
                if any(
                    re.search(r"\bTed\b", value, re.IGNORECASE)
                    for value in field_values
                ) and "character:ted" not in set(scope.roles.involved_ids):
                    raise StateConflictError(
                        "final field omitted explicit protected-user involvement"
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
                    "final sequence changed Composer role or claim ownership"
                )
        if cited_story_segments != set(self._story_segments):
            raise StateConflictError(
                "complete final sequence does not cover every Composer story segment"
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
        self, package: ValidatorFinalizationPackageV1
    ) -> None:
        adjudications = {
            value.segment_key: value
            for value in package.protected_semantic_adjudications
        }
        if set(adjudications) != set(self._story_segments):
            raise StateConflictError(
                "Validator did not independently adjudicate every Composer segment"
            )
        relation_fields = {
            ProtectedSemanticRelationKind.AFFECTED_BY_NPC: "affected_ids",
            ProtectedSemanticRelationKind.ADDRESSED_BY_NPC: "addressed_ids",
            ProtectedSemanticRelationKind.OBSERVED_BY_NPC: "observing_ids",
            ProtectedSemanticRelationKind.REFERENCED_ONLY_BY_NPC: "referenced_ids",
        }
        for segment_key, segment in self._story_segments.items():
            adjudication = adjudications[segment_key]
            if (
                adjudication.protected_user_id != "character:ted"
                or adjudication.output_start != segment.output_start
                or adjudication.output_end != segment.output_end
                or adjudication.exact_text_sha256 != text_sha256(segment.exact_text)
            ):
                raise StateConflictError(
                    "protected semantic adjudication changed the exact Composer span"
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
                        "protected-user assertion was laundered through Composer roles"
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
                    or re.search(
                        r"\bTed\b", segment.exact_text, re.IGNORECASE
                    )
                ):
                    raise StateConflictError(
                        "protected-user involvement was mislabeled as absent"
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
        active_root = (branch_root / "ACTIVE").resolve()
        target = (active_root / directive.target_file).resolve()
        if active_root not in target.parents or not target.is_file() or target.is_symlink():
            raise StateConflictError(
                "persistence directive target is unavailable or escaped branch"
            )
        payload = json.loads(target.read_text(encoding="utf-8"))
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
        target = (branch_root / binding.relative_path).resolve()
        if branch_root.resolve() not in target.parents or not target.is_file() or target.is_symlink():
            raise StateConflictError("evidence binding record is unavailable or escaped branch")
        text = target.read_text(encoding="utf-8")
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
    target = (branch_root / normalized).resolve()
    if branch_root.resolve() not in target.parents or not target.is_file() or target.is_symlink():
        raise StateConflictError("character summary source is unavailable or escaped branch")
    text = target.read_text(encoding="utf-8")
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
    target = (branch_root / normalized).resolve()
    if branch_root.resolve() not in target.parents or not target.is_file() or target.is_symlink():
        raise StateConflictError("character summary source is unavailable or escaped branch")
    text = target.read_text(encoding="utf-8")
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
