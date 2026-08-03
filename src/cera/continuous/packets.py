"""Closed Python-owned Planner packet contracts for continuous turns."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, ClassVar, Mapping

from cera.errors import ContractValidationError
from cera.serialization import (
    canonical_bytes,
    canonical_sha256,
    re_is_sha256,
    text_sha256,
    to_primitive,
)

from .contracts import FinalInformationVisibility, FinalSequenceV1
from .evidence import (
    CompactAcceptedHeadReceiptV1,
    StableAcceptedContextReferenceV1,
)


class ContinuousPlannerPacketKind(StrEnum):
    FIRST_TURN_INITIALIZATION = "first_turn_initialization"
    LEAN_CONTINUATION = "lean_continuous_continuation"
    PROJECTION_ASSISTED = "projection_assisted_continuation"
    SCENE_CHANGE = "scene_change"


class LeanContinuationSourceClassification(StrEnum):
    ACCEPTED_FINAL_SEQUENCE = "accepted_final_sequence_authority"
    STRICT_VALIDATED_PROVISIONAL_PLAN = (
        "strict_validated_provisional_plan_context"
    )


@dataclass(frozen=True, slots=True)
class LeanContinuationAuthorityV1:
    """Small prior-turn authority; never a replay of the prior plan or prose."""

    SCHEMA_VERSION: ClassVar[str] = "cera.lean_continuation_authority.v1"

    schema_version: str
    source_classification: LeanContinuationSourceClassification
    source_turn_id: str
    source_sequence_sha256: str
    active_cast_ids: tuple[str, ...]
    public_continuation_anchor: str | None
    active_cast_reference_keys: tuple[str, ...]
    continuation_anchor_reference_key: str | None
    authority_sha256: str

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("lean continuation authority schema changed")
        if not isinstance(self.source_classification, LeanContinuationSourceClassification):
            raise ContractValidationError(
                "lean continuation source classification is invalid"
            )
        if not isinstance(self.source_turn_id, str) or not self.source_turn_id.strip():
            raise ContractValidationError("lean continuation source turn is invalid")
        if not re_is_sha256(self.source_sequence_sha256):
            raise ContractValidationError("lean continuation source hash is invalid")
        if (
            not self.active_cast_ids
            or len(self.active_cast_ids) != len(set(self.active_cast_ids))
            or "character:ted" in self.active_cast_ids
            or any(
                not isinstance(value, str)
                or not value.startswith("character:")
                or not value.strip()
                for value in self.active_cast_ids
            )
        ):
            raise ContractValidationError("lean continuation active cast is invalid")
        if (
            not self.active_cast_reference_keys
            or len(self.active_cast_reference_keys)
            != len(set(self.active_cast_reference_keys))
            or any(
                not value.startswith("binding_accepted_ref_")
                for value in self.active_cast_reference_keys
            )
        ):
            raise ContractValidationError(
                "lean continuation active-cast references are invalid"
            )
        if self.public_continuation_anchor is None:
            if self.continuation_anchor_reference_key is not None:
                raise ContractValidationError(
                    "lean continuation anchor key lacks a public anchor"
                )
        elif (
            not isinstance(self.public_continuation_anchor, str)
            or not self.public_continuation_anchor.strip()
            or len(self.public_continuation_anchor) > 4_000
            or self.continuation_anchor_reference_key is None
            or not self.continuation_anchor_reference_key.startswith(
                "binding_accepted_ref_"
            )
        ):
            raise ContractValidationError("lean continuation public anchor is invalid")
        expected = canonical_sha256(self._unsigned_payload())
        if self.authority_sha256 != expected:
            raise ContractValidationError("lean continuation authority hash changed")

    def _unsigned_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.SCHEMA_VERSION,
            "source_classification": self.source_classification.value,
            "source_turn_id": self.source_turn_id,
            "source_sequence_sha256": self.source_sequence_sha256,
            "active_cast_ids": self.active_cast_ids,
            "public_continuation_anchor": self.public_continuation_anchor,
            "active_cast_reference_keys": self.active_cast_reference_keys,
            "continuation_anchor_reference_key": (
                self.continuation_anchor_reference_key
            ),
        }

    def to_payload(self) -> dict[str, Any]:
        return {**self._unsigned_payload(), "authority_sha256": self.authority_sha256}


def build_accepted_lean_continuation_authority(
    *,
    receipt: CompactAcceptedHeadReceiptV1,
    references: tuple[StableAcceptedContextReferenceV1, ...],
    final_sequence: FinalSequenceV1,
) -> LeanContinuationAuthorityV1:
    """Derive active cast and an optional public stop from accepted exact facts."""

    if (
        receipt.accepted_turn_id != final_sequence.accepted_turn_id
        or tuple(value.reference_key for value in references)
        != receipt.stable_reference_keys
    ):
        raise ContractValidationError(
            "lean continuation accepted source custody changed"
        )
    active_cast_ids = tuple(
        sorted(
            {
                identity
                for reference in references
                for identity in reference.roles.involved_ids
                if identity != "character:ted"
            }
        )
    )
    active_cast_reference_keys = tuple(
        reference.reference_key
        for reference in references
        if set(reference.roles.involved_ids).intersection(active_cast_ids)
    )
    last_item = final_sequence.items[-1]
    anchor_reference = next(
        (
            reference
            for reference in references
            if reference.source_item_key == last_item.item_key
            and reference.field_name == "resulting_state"
            and reference.field_value == final_sequence.final_stop_state
            and reference.visibility is FinalInformationVisibility.PUBLIC
        ),
        None,
    )
    unsigned = {
        "schema_version": LeanContinuationAuthorityV1.SCHEMA_VERSION,
        "source_classification": (
            LeanContinuationSourceClassification.ACCEPTED_FINAL_SEQUENCE.value
        ),
        "source_turn_id": final_sequence.accepted_turn_id,
        "source_sequence_sha256": final_sequence.sequence_sha256,
        "active_cast_ids": active_cast_ids,
        "public_continuation_anchor": (
            anchor_reference.field_value if anchor_reference is not None else None
        ),
        "active_cast_reference_keys": active_cast_reference_keys,
        "continuation_anchor_reference_key": (
            anchor_reference.reference_key if anchor_reference is not None else None
        ),
    }
    return LeanContinuationAuthorityV1(
        schema_version=LeanContinuationAuthorityV1.SCHEMA_VERSION,
        source_classification=(
            LeanContinuationSourceClassification.ACCEPTED_FINAL_SEQUENCE
        ),
        source_turn_id=final_sequence.accepted_turn_id,
        source_sequence_sha256=final_sequence.sequence_sha256,
        active_cast_ids=active_cast_ids,
        public_continuation_anchor=unsigned["public_continuation_anchor"],
        active_cast_reference_keys=active_cast_reference_keys,
        continuation_anchor_reference_key=unsigned[
            "continuation_anchor_reference_key"
        ],
        authority_sha256=canonical_sha256(unsigned),
    )


@dataclass(frozen=True, slots=True)
class LeanSceneChangeContextV1:
    """Closed derived scene handoff; exact accepted pairs never enter this payload."""

    SCHEMA_VERSION: ClassVar[str] = "cera.lean_scene_change_context.v1"

    schema_version: str
    completed_scene_id: str
    accepted_turn_ids: tuple[str, ...]
    shortest_complete_summary: str
    ending_state: str
    transition_context: str
    summary_authority_classification: str
    summary_revision: int
    regeneration_identity_sha256: str
    first_user_message_of_new_scene: str
    exact_prior_pairs_included: bool = False

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("lean scene-change schema changed")
        if not all(
            isinstance(value, str) and value.strip()
            for value in (
                self.completed_scene_id,
                self.shortest_complete_summary,
                self.ending_state,
                self.transition_context,
                self.first_user_message_of_new_scene,
            )
        ):
            raise ContractValidationError("lean scene-change context is incomplete")
        if not self.accepted_turn_ids or len(self.accepted_turn_ids) != len(
            set(self.accepted_turn_ids)
        ) or any(
            not isinstance(value, str) or not value.strip()
            for value in self.accepted_turn_ids
        ):
            raise ContractValidationError("lean scene-change accepted turns are invalid")
        if self.summary_authority_classification != "non_authoritative_derived_view":
            raise ContractValidationError("lean scene-change authority changed")
        if type(self.summary_revision) is not int or self.summary_revision < 1:
            raise ContractValidationError("lean scene-change revision is invalid")
        if not re_is_sha256(self.regeneration_identity_sha256):
            raise ContractValidationError("lean scene-change regeneration hash is invalid")
        if self.exact_prior_pairs_included is not False:
            raise ContractValidationError("lean scene-change contains exact prior pairs")

    def to_payload(self) -> dict[str, Any]:
        return to_primitive(self)

    @property
    def context_sha256(self) -> str:
        return canonical_sha256(self.to_payload())


@dataclass(frozen=True, slots=True)
class ContinuousPlannerTurnPacketV1:
    """One discriminated, closed Planner packet built only after Python binding."""

    SCHEMA_VERSION: ClassVar[str] = "cera.continuous_planner_turn_packet.v1"
    COMMON_FIELDS: ClassVar[frozenset[str]] = frozenset(
        {
            "schema_version",
            "packet_kind",
            "world_id",
            "branch_id",
            "session_id",
            "request_id",
            "scene_id",
            "turn_id",
            "context_mode",
            "current_user_message",
            "request_local_evidence_bindings",
            "current_source_binding_key",
            "mechanical_connective_binding_key",
            "protected_user_source_claims",
            "ingress_source_units",
            "ingress_custody",
            "character_summary_bindings",
        }
    )
    ALLOWED_FIELDS_BY_KIND: ClassVar[
        Mapping[ContinuousPlannerPacketKind, frozenset[str]]
    ] = {
        ContinuousPlannerPacketKind.FIRST_TURN_INITIALIZATION: COMMON_FIELDS,
        ContinuousPlannerPacketKind.LEAN_CONTINUATION: COMMON_FIELDS
        | {
            "compact_accepted_head_receipt",
            "stable_accepted_reference_keys",
            "lean_continuation_authority",
        },
        ContinuousPlannerPacketKind.PROJECTION_ASSISTED: COMMON_FIELDS
        | {
            "compact_accepted_head_receipt",
            "stable_accepted_reference_keys",
            "lean_continuation_authority",
            "projection_assisted",
        },
        ContinuousPlannerPacketKind.SCENE_CHANGE: COMMON_FIELDS
        | {
            "compact_accepted_head_receipt",
            "stable_accepted_reference_keys",
            "lean_continuation_authority",
            "scene_change_envelope_sha256",
        },
    }
    EVIDENCE_BINDING_FIELDS: ClassVar[frozenset[str]] = frozenset(
        {
            "binding_key",
            "kind",
            "source_identity",
            "source_sha256",
            "relative_path",
            "record_revision",
            "record_type",
            "authority_classification",
            "visibility",
            "knowledge_owner_id",
            "accepted_turn_id",
            "acceptance_receipt_sha256",
            "accepted_envelope_sha256",
            "provider_thread_sha256",
            "session_snapshot_sha256",
            "synchronization_receipt_sha256",
            "stable_reference_only",
        }
    )
    PROTECTED_CLAIM_FIELDS: ClassVar[frozenset[str]] = frozenset(
        {
            "claim_key",
            "kind",
            "source_binding_key",
            "source_sha256",
            "source_start",
            "source_end",
            "exact_text",
            "speaker_id",
            "source_unit_key",
            "source_unit_kind",
            "deterministic_projection_rule",
        }
    )
    INGRESS_SOURCE_UNIT_FIELDS: ClassVar[frozenset[str]] = frozenset(
        {
            "schema_version",
            "source_unit_key",
            "kind",
            "source_start",
            "source_end",
            "exact_text",
            "speaker_id",
            "actor_id",
            "classification_basis",
        }
    )
    CHARACTER_SUMMARY_BINDING_FIELDS: ClassVar[frozenset[str]] = frozenset(
        {
            "character_id",
            "binding_key",
            "source_path",
            "source_revision",
            "source_sha256",
        }
    )
    PROJECTION_FACT_FIELDS: ClassVar[frozenset[str]] = frozenset(
        {
            "reference_key",
            "accepted_turn_id",
            "source_item_key",
            "field_name",
            "field_value",
            "visibility",
            "knowledge_owner_id",
            "roles",
        }
    )
    ROLE_FIELDS: ClassVar[frozenset[str]] = frozenset(
        {
            "schema_version",
            "action_owner_ids",
            "state_owner_ids",
            "speaker_ids",
            "affected_ids",
            "addressed_ids",
            "observing_ids",
            "referenced_ids",
        }
    )

    schema_version: str
    packet_kind: ContinuousPlannerPacketKind
    world_id: str
    branch_id: str
    session_id: str
    request_id: str
    scene_id: str
    turn_id: str
    context_mode: str
    current_user_message: str
    request_local_evidence_bindings: tuple[Mapping[str, Any], ...]
    current_source_binding_key: str
    mechanical_connective_binding_key: str
    protected_user_source_claims: tuple[Mapping[str, Any], ...]
    ingress_source_units: tuple[Mapping[str, Any], ...]
    ingress_custody: Mapping[str, Any]
    character_summary_bindings: tuple[Mapping[str, Any], ...]
    compact_accepted_head_receipt: CompactAcceptedHeadReceiptV1 | None = None
    stable_accepted_reference_keys: tuple[str, ...] = ()
    lean_continuation_authority: LeanContinuationAuthorityV1 | None = None
    projection_assisted_trigger: str | None = None
    projection_reference_keys: tuple[str, ...] = ()
    projection_facts: tuple[Mapping[str, Any], ...] = ()
    scene_change_envelope_sha256: str | None = None

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("continuous Planner packet schema changed")
        if not all(
            isinstance(value, str) and value.strip()
            for value in (
                self.world_id,
                self.branch_id,
                self.session_id,
                self.request_id,
                self.scene_id,
                self.turn_id,
                self.context_mode,
                self.current_user_message,
            )
        ):
            raise ContractValidationError("continuous Planner packet scope is incomplete")
        if not self.current_source_binding_key.startswith("binding_source_"):
            raise ContractValidationError("continuous Planner source binding changed")
        if not self.mechanical_connective_binding_key.startswith(
            "binding_mechanical_"
        ):
            raise ContractValidationError("continuous Planner mechanical binding changed")
        expected_custody_fields = {
            "receipt_id",
            "receipt_sha256",
            "raw_source_sha256",
            "protected_user_id",
            "source_unit_keys",
        }
        if set(self.ingress_custody) != expected_custody_fields:
            raise ContractValidationError("continuous Planner ingress custody is open")
        if self.ingress_custody.get("protected_user_id") != "character:ted":
            raise ContractValidationError("continuous Planner protected user changed")
        for field in ("receipt_sha256", "raw_source_sha256"):
            if not re_is_sha256(self.ingress_custody.get(field)):
                raise ContractValidationError("continuous Planner ingress hash is invalid")
        if self.ingress_custody.get("raw_source_sha256") != text_sha256(
            self.current_user_message
        ):
            raise ContractValidationError("continuous Planner current source changed")
        if len(self.stable_accepted_reference_keys) != len(
            set(self.stable_accepted_reference_keys)
        ) or any(
            not value.startswith("binding_accepted_ref_")
            for value in self.stable_accepted_reference_keys
        ):
            raise ContractValidationError("continuous Planner stable keys are invalid")
        if len(self.projection_reference_keys) != len(
            set(self.projection_reference_keys)
        ):
            raise ContractValidationError("continuous Planner projection keys are duplicated")
        self._validate_closed_mappings(
            "request evidence binding",
            self.request_local_evidence_bindings,
            self.EVIDENCE_BINDING_FIELDS,
        )
        self._validate_closed_mappings(
            "protected-user claim",
            self.protected_user_source_claims,
            self.PROTECTED_CLAIM_FIELDS,
        )
        self._validate_closed_mappings(
            "ingress source unit",
            self.ingress_source_units,
            self.INGRESS_SOURCE_UNIT_FIELDS,
        )
        self._validate_closed_mappings(
            "character-summary binding",
            self.character_summary_bindings,
            self.CHARACTER_SUMMARY_BINDING_FIELDS,
        )
        self._validate_closed_mappings(
            "projection fact",
            self.projection_facts,
            self.PROJECTION_FACT_FIELDS,
        )
        binding_keys = tuple(
            value.get("binding_key") for value in self.request_local_evidence_bindings
        )
        if len(binding_keys) != len(set(binding_keys)):
            raise ContractValidationError("continuous Planner evidence keys are duplicated")
        binding_by_key = {
            value.get("binding_key"): value
            for value in self.request_local_evidence_bindings
        }
        for key, kind in (
            (self.current_source_binding_key, "current_user_source"),
            (
                self.mechanical_connective_binding_key,
                "mechanical_connective_allowance",
            ),
        ):
            if binding_by_key.get(key, {}).get("kind") != kind:
                raise ContractValidationError(
                    "continuous Planner required evidence binding is missing"
                )
        source_manifest = binding_by_key[self.current_source_binding_key]
        if source_manifest.get("source_sha256") != self.ingress_custody.get(
            "raw_source_sha256"
        ):
            raise ContractValidationError(
                "continuous Planner source binding hash changed"
            )
        if tuple(self.ingress_custody.get("source_unit_keys", ())) != tuple(
            value.get("source_unit_key") for value in self.ingress_source_units
        ):
            raise ContractValidationError(
                "continuous Planner ingress source-unit custody changed"
            )
        for unit in self.ingress_source_units:
            start = unit.get("source_start")
            end = unit.get("source_end")
            exact = unit.get("exact_text")
            if (
                type(start) is not int
                or type(end) is not int
                or not 0 <= start < end <= len(self.current_user_message)
                or self.current_user_message[start:end] != exact
            ):
                raise ContractValidationError(
                    "continuous Planner ingress source unit changed"
                )
        source_unit_keys = {
            value.get("source_unit_key") for value in self.ingress_source_units
        }
        for claim in self.protected_user_source_claims:
            start = claim.get("source_start")
            end = claim.get("source_end")
            if (
                type(start) is not int
                or type(end) is not int
                or not 0 <= start < end <= len(self.current_user_message)
                or claim.get("source_binding_key") != self.current_source_binding_key
                or claim.get("source_sha256")
                != self.ingress_custody.get("raw_source_sha256")
                or claim.get("source_unit_key") not in source_unit_keys
                or self.current_user_message[start:end] != claim.get("exact_text")
            ):
                raise ContractValidationError(
                    "continuous Planner protected-user claim changed"
                )
        summary_ids = tuple(
            value.get("character_id") for value in self.character_summary_bindings
        )
        if len(summary_ids) != len(set(summary_ids)):
            raise ContractValidationError(
                "continuous Planner character-summary bindings are duplicated"
            )
        for summary in self.character_summary_bindings:
            evidence = binding_by_key.get(summary.get("binding_key"))
            if evidence is None or (
                evidence.get("relative_path") != summary.get("source_path")
                or evidence.get("record_revision") != summary.get("source_revision")
                or evidence.get("source_sha256") != summary.get("source_sha256")
                or evidence.get("knowledge_owner_id") != summary.get("character_id")
            ):
                raise ContractValidationError(
                    "continuous Planner character-summary evidence changed"
                )
        for fact in self.projection_facts:
            roles = fact.get("roles")
            if not isinstance(roles, Mapping) or set(roles) != self.ROLE_FIELDS:
                raise ContractValidationError(
                    "continuous Planner projection roles are open"
                )
            if roles.get("schema_version") != "cera.character_role_ledger.v1":
                raise ContractValidationError(
                    "continuous Planner projection role schema changed"
                )
            for field in self.ROLE_FIELDS - {"schema_version"}:
                identities = roles.get(field)
                if (
                    not isinstance(identities, (list, tuple))
                    or any(
                        not isinstance(value, str) or not value.strip()
                        for value in identities
                    )
                    or len(identities) != len(set(identities))
                ):
                    raise ContractValidationError(
                        "continuous Planner projection roles contain caller data"
                    )
            if (
                not isinstance(fact.get("reference_key"), str)
                or not fact["reference_key"].startswith(
                    "binding_accepted_ref_"
                )
                or not all(
                    isinstance(fact.get(field), str)
                    and bool(fact[field].strip())
                    for field in (
                        "accepted_turn_id",
                        "source_item_key",
                        "field_name",
                        "field_value",
                        "visibility",
                    )
                )
            ):
                raise ContractValidationError(
                    "continuous Planner projection fact changed"
                )
        self._validate_kind()
        payload = self.to_payload()
        if set(payload) != self.ALLOWED_FIELDS_BY_KIND[self.packet_kind]:
            raise ContractValidationError("continuous Planner packet field set changed")

    @staticmethod
    def _validate_closed_mappings(
        label: str,
        values: tuple[Mapping[str, Any], ...],
        expected_fields: frozenset[str],
    ) -> None:
        for value in values:
            if not isinstance(value, Mapping) or set(value) != expected_fields:
                raise ContractValidationError(
                    f"continuous Planner {label} schema is open"
                )
            for field, item in value.items():
                if field == "roles":
                    continue
                if isinstance(item, (Mapping, list, tuple, set, frozenset)):
                    raise ContractValidationError(
                        f"continuous Planner {label} contains nested caller data"
                    )

    def _validate_kind(self) -> None:
        has_head = self.compact_accepted_head_receipt is not None
        has_lean_authority = self.lean_continuation_authority is not None
        has_projection = bool(
            self.projection_assisted_trigger
            or self.projection_reference_keys
            or self.projection_facts
        )
        has_scene_change = self.scene_change_envelope_sha256 is not None
        if has_head and tuple(
            self.compact_accepted_head_receipt.stable_reference_keys
        ) != self.stable_accepted_reference_keys:
            raise ContractValidationError("continuous Planner compact head keys changed")
        if has_head and (
            self.compact_accepted_head_receipt.world_id != self.world_id
            or self.compact_accepted_head_receipt.branch_id != self.branch_id
            or (
                self.packet_kind is not ContinuousPlannerPacketKind.SCENE_CHANGE
                and self.compact_accepted_head_receipt.scene_id != self.scene_id
            )
        ):
            raise ContractValidationError("continuous Planner compact head scope changed")
        if has_lean_authority:
            authority = self.lean_continuation_authority
            if (
                not has_head
                or authority is None
                or authority.source_turn_id
                != self.compact_accepted_head_receipt.accepted_turn_id
                or not set(authority.active_cast_reference_keys).issubset(
                    self.stable_accepted_reference_keys
                )
                or (
                    authority.continuation_anchor_reference_key is not None
                    and authority.continuation_anchor_reference_key
                    not in self.stable_accepted_reference_keys
                )
            ):
                raise ContractValidationError(
                    "continuous Planner lean authority custody changed"
                )
        stable_manifest_keys = {
            value.get("binding_key")
            for value in self.request_local_evidence_bindings
            if value.get("stable_reference_only") is True
        }
        if stable_manifest_keys != set(self.stable_accepted_reference_keys):
            raise ContractValidationError(
                "continuous Planner stable binding manifest changed"
            )
        if has_head:
            for key in self.stable_accepted_reference_keys:
                binding = next(
                    value
                    for value in self.request_local_evidence_bindings
                    if value.get("binding_key") == key
                )
                if (
                    binding.get("kind") != "accepted_session_envelope"
                    or binding.get("authority_classification")
                    != "accepted_session_authority"
                    or binding.get("accepted_turn_id")
                    != self.compact_accepted_head_receipt.accepted_turn_id
                    or binding.get("accepted_envelope_sha256")
                    != self.compact_accepted_head_receipt.accepted_envelope_sha256
                    or binding.get("acceptance_receipt_sha256")
                    != self.compact_accepted_head_receipt.acceptance_receipt_sha256
                    or binding.get("provider_thread_sha256")
                    != self.compact_accepted_head_receipt.provider_thread_sha256
                    or binding.get("session_snapshot_sha256")
                    != self.compact_accepted_head_receipt.session_snapshot_sha256
                    or binding.get("synchronization_receipt_sha256")
                    != self.compact_accepted_head_receipt.synchronization_receipt_sha256
                ):
                    raise ContractValidationError(
                        "continuous Planner stable binding custody changed"
                    )
        if self.packet_kind is ContinuousPlannerPacketKind.FIRST_TURN_INITIALIZATION:
            if (
                self.context_mode != "lean_continuous"
                or has_head
                or has_lean_authority
                or self.stable_accepted_reference_keys
                or has_projection
                or has_scene_change
            ):
                raise ContractValidationError("first-turn packet carries continuation state")
        elif self.packet_kind is ContinuousPlannerPacketKind.LEAN_CONTINUATION:
            if (
                self.context_mode != "lean_continuous"
                or not has_head
                or not has_lean_authority
                or not self.stable_accepted_reference_keys
                or has_projection
                or has_scene_change
            ):
                raise ContractValidationError("lean packet contract changed")
        elif self.packet_kind is ContinuousPlannerPacketKind.PROJECTION_ASSISTED:
            fact_keys = tuple(value.get("reference_key") for value in self.projection_facts)
            if (
                not has_head
                or not has_lean_authority
                or self.context_mode != "projection_assisted"
                or not isinstance(self.projection_assisted_trigger, str)
                or not self.projection_assisted_trigger.strip()
                or not self.projection_reference_keys
                or fact_keys != self.projection_reference_keys
                or not set(self.projection_reference_keys).issubset(
                    self.stable_accepted_reference_keys
                )
                or has_scene_change
            ):
                raise ContractValidationError("projection-assisted packet contract changed")
        elif self.packet_kind is ContinuousPlannerPacketKind.SCENE_CHANGE:
            if (
                self.context_mode != "lean_continuous"
                or has_projection
                or not re_is_sha256(self.scene_change_envelope_sha256)
            ):
                raise ContractValidationError("Scene Change packet contract changed")
            if has_head != bool(self.stable_accepted_reference_keys):
                raise ContractValidationError("Scene Change accepted head is incomplete")
            if has_head != has_lean_authority:
                raise ContractValidationError("Scene Change lean authority is incomplete")
        else:
            raise ContractValidationError("continuous Planner packet kind is invalid")

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": self.SCHEMA_VERSION,
            "packet_kind": self.packet_kind.value,
            "world_id": self.world_id,
            "branch_id": self.branch_id,
            "session_id": self.session_id,
            "request_id": self.request_id,
            "scene_id": self.scene_id,
            "turn_id": self.turn_id,
            "context_mode": self.context_mode,
            "current_user_message": self.current_user_message,
            "request_local_evidence_bindings": tuple(
                dict(value) for value in self.request_local_evidence_bindings
            ),
            "current_source_binding_key": self.current_source_binding_key,
            "mechanical_connective_binding_key": (
                self.mechanical_connective_binding_key
            ),
            "protected_user_source_claims": tuple(
                dict(value) for value in self.protected_user_source_claims
            ),
            "ingress_source_units": tuple(
                dict(value) for value in self.ingress_source_units
            ),
            "ingress_custody": dict(self.ingress_custody),
            "character_summary_bindings": tuple(
                dict(value) for value in self.character_summary_bindings
            ),
        }
        if self.packet_kind in {
            ContinuousPlannerPacketKind.LEAN_CONTINUATION,
            ContinuousPlannerPacketKind.PROJECTION_ASSISTED,
            ContinuousPlannerPacketKind.SCENE_CHANGE,
        }:
            payload["compact_accepted_head_receipt"] = (
                to_primitive(self.compact_accepted_head_receipt)
                if self.compact_accepted_head_receipt is not None
                else None
            )
            payload["stable_accepted_reference_keys"] = (
                self.stable_accepted_reference_keys
            )
            payload["lean_continuation_authority"] = (
                self.lean_continuation_authority.to_payload()
                if self.lean_continuation_authority is not None
                else None
            )
        if self.packet_kind is ContinuousPlannerPacketKind.PROJECTION_ASSISTED:
            payload["projection_assisted"] = {
                "trigger": self.projection_assisted_trigger,
                "reference_keys": self.projection_reference_keys,
                "facts": tuple(dict(value) for value in self.projection_facts),
            }
        if self.packet_kind is ContinuousPlannerPacketKind.SCENE_CHANGE:
            payload["scene_change_envelope_sha256"] = (
                self.scene_change_envelope_sha256
            )
        return payload

    @property
    def packet_bytes(self) -> int:
        return len(canonical_bytes(self.to_payload()))

    @property
    def packet_sha256(self) -> str:
        return canonical_sha256(self.to_payload())


def build_continuous_planner_turn_packet(
    *,
    world_id: str,
    branch_id: str,
    session_id: str,
    request_id: str,
    scene_id: str,
    turn_id: str,
    context_mode: str,
    current_user_message: str,
    request_local_evidence_bindings: tuple[Mapping[str, Any], ...],
    current_source_binding_key: str,
    mechanical_connective_binding_key: str,
    protected_user_source_claims: tuple[Mapping[str, Any], ...],
    ingress_source_units: tuple[Mapping[str, Any], ...],
    ingress_custody: Mapping[str, Any],
    character_summary_bindings: tuple[Mapping[str, Any], ...],
    compact_accepted_head_receipt: CompactAcceptedHeadReceiptV1 | None,
    stable_accepted_reference_keys: tuple[str, ...],
    projection_assisted_trigger: str | None,
    projection_reference_keys: tuple[str, ...],
    projection_facts: tuple[Mapping[str, Any], ...],
    scene_change_envelope_sha256: str | None,
    lean_continuation_authority: LeanContinuationAuthorityV1 | None = None,
) -> ContinuousPlannerTurnPacketV1:
    if scene_change_envelope_sha256 is not None:
        kind = ContinuousPlannerPacketKind.SCENE_CHANGE
    elif projection_assisted_trigger is not None or projection_reference_keys:
        kind = ContinuousPlannerPacketKind.PROJECTION_ASSISTED
    elif compact_accepted_head_receipt is not None:
        kind = ContinuousPlannerPacketKind.LEAN_CONTINUATION
    else:
        kind = ContinuousPlannerPacketKind.FIRST_TURN_INITIALIZATION
    return ContinuousPlannerTurnPacketV1(
        schema_version=ContinuousPlannerTurnPacketV1.SCHEMA_VERSION,
        packet_kind=kind,
        world_id=world_id,
        branch_id=branch_id,
        session_id=session_id,
        request_id=request_id,
        scene_id=scene_id,
        turn_id=turn_id,
        context_mode=context_mode,
        current_user_message=current_user_message,
        request_local_evidence_bindings=request_local_evidence_bindings,
        current_source_binding_key=current_source_binding_key,
        mechanical_connective_binding_key=mechanical_connective_binding_key,
        protected_user_source_claims=protected_user_source_claims,
        ingress_source_units=ingress_source_units,
        ingress_custody=ingress_custody,
        character_summary_bindings=character_summary_bindings,
        compact_accepted_head_receipt=compact_accepted_head_receipt,
        stable_accepted_reference_keys=stable_accepted_reference_keys,
        lean_continuation_authority=lean_continuation_authority,
        projection_assisted_trigger=projection_assisted_trigger,
        projection_reference_keys=projection_reference_keys,
        projection_facts=projection_facts,
        scene_change_envelope_sha256=scene_change_envelope_sha256,
    )
