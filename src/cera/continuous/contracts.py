"""Shadow contracts for continuous Planner and Validator sessions.

These contracts are intentionally provider-neutral and non-authoritative.  A
Planner sequence is causal guidance for the Composer.  A Validator package is
a candidate publication package until Python validates it and the creator
chooses an accepting action.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re
from typing import Any, ClassVar, Mapping

from cera.creator_review.models import (
    CreatorReviewAssessment,
    CreatorReviewAction,
)
from cera.errors import ContractValidationError
from cera.serialization import canonical_sha256, domain_sha256, re_is_sha256, text_sha256

from .record_policy import (
    PERSISTENCE_POLICY_SHA256,
    validate_persistence_field_path,
)


LOCAL_KEY_BODY_PATTERN = r"[a-z][a-z0-9_]{0,95}"
LOCAL_KEY_JSON_PATTERN = rf"^{LOCAL_KEY_BODY_PATTERN}$"


_KEY = re.compile(rf"{LOCAL_KEY_BODY_PATTERN}\Z")
_IDENTITY = re.compile(r"[a-z][a-z0-9_.:-]{0,191}\Z")
_RELATIVE_PART = re.compile(r"[^\\/:*?\"<>|\x00-\x1f]+\Z")


def _text(value: str, field: str, *, maximum: int = 8_000) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ContractValidationError(f"{field} must be non-empty and bounded")


def _identity(value: str, field: str) -> None:
    if not isinstance(value, str) or _IDENTITY.fullmatch(value) is None:
        raise ContractValidationError(f"{field} is not a stable identity")


def _key(value: str, field: str) -> None:
    if not isinstance(value, str) or _KEY.fullmatch(value) is None:
        raise ContractValidationError(f"{field} is not a local key")


def _unique(values: tuple[Any, ...], field: str) -> None:
    rendered = tuple(str(value) for value in values)
    if len(rendered) != len(set(rendered)):
        raise ContractValidationError(f"{field} contains duplicates")


def _relative_path(value: str, field: str) -> None:
    _text(value, field, maximum=512)
    normalized = value.replace("\\", "/")
    parts = normalized.split("/")
    if (
        value.startswith(("/", "\\"))
        or re.match(r"^[A-Za-z]:", value)
        or any(part in {"", ".", ".."} for part in parts)
        or any(_RELATIVE_PART.fullmatch(part) is None for part in parts)
    ):
        raise ContractValidationError(f"{field} must remain under the world root")


class ProtectedUserAllowanceMode(StrEnum):
    NONE = "none"
    EXACT_SOURCE_ONLY = "exact_source_only"
    MINIMAL_NONBRANCHING_CONNECTIVE = "minimal_nonbranching_connective"


class ProtectedUserSourceClaimKind(StrEnum):
    ACTION_OR_STATE = "action_or_state"
    DIALOGUE = "dialogue"


class IngressSourceUnitKind(StrEnum):
    ACTION = "action"
    DIALOGUE = "dialogue"
    STATE = "state"
    NARRATION = "narration"
    INSTRUCTION = "instruction"


class ContinuousIngressAuthorityKind(StrEnum):
    PREPARED_INGRESS = "prepared_ingress"
    FROZEN_PYTHON_FIXTURE = "frozen_python_fixture"


@dataclass(frozen=True, slots=True)
class PreparedIngressClassifierDescriptorV1:
    """Closed identity for one repository-owned prepared-ingress classifier."""

    SCHEMA_VERSION: ClassVar[str] = "cera.prepared_ingress_classifier_descriptor.v1"

    schema_version: str
    classification_adapter_id: str
    implementation_module: str
    implementation_qualname: str
    implementation_source_sha256: str
    source_unit_schema_version: str
    classification_receipt_schema_version: str

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("prepared classifier descriptor schema changed")
        for field in (
            "classification_adapter_id",
            "source_unit_schema_version",
            "classification_receipt_schema_version",
        ):
            _identity(getattr(self, field), f"prepared_classifier_descriptor.{field}")
        for field in ("implementation_module", "implementation_qualname"):
            _text(
                getattr(self, field),
                f"prepared_classifier_descriptor.{field}",
                maximum=256,
            )
        if self.classification_adapter_id.startswith("cera.fixture."):
            raise ContractValidationError(
                "prepared classifier descriptor cannot claim fixture authority"
            )
        if not re_is_sha256(self.implementation_source_sha256):
            raise ContractValidationError(
                "prepared classifier descriptor source identity is invalid"
            )

    @property
    def descriptor_sha256(self) -> str:
        return domain_sha256(self.SCHEMA_VERSION, self)


@dataclass(frozen=True, slots=True)
class IngressSourceUnitV1:
    """Ingress-owned exact source classification; models cannot create it."""

    SCHEMA_VERSION: ClassVar[str] = "cera.continuous_ingress_source_unit.v1"

    schema_version: str
    source_unit_key: str
    kind: IngressSourceUnitKind
    source_start: int
    source_end: int
    exact_text: str
    actor_id: str | None
    speaker_id: str | None
    classification_basis: str

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("ingress source-unit schema changed")
        _key(self.source_unit_key, "ingress_source_unit.source_unit_key")
        if (
            type(self.source_start) is not int
            or type(self.source_end) is not int
            or self.source_start < 0
            or self.source_end <= self.source_start
        ):
            raise ContractValidationError("ingress source-unit span is invalid")
        if (
            not isinstance(self.exact_text, str)
            or not self.exact_text
            or len(self.exact_text) > 64_000
        ):
            raise ContractValidationError(
                "ingress_source_unit.exact_text must be non-empty and bounded"
            )
        if self.source_end - self.source_start != len(self.exact_text):
            raise ContractValidationError("ingress source-unit span length changed")
        for field in ("actor_id", "speaker_id"):
            value = getattr(self, field)
            if value is not None:
                _identity(value, f"ingress_source_unit.{field}")
        if self.kind is IngressSourceUnitKind.DIALOGUE:
            if self.speaker_id is None or self.actor_id is not None:
                raise ContractValidationError("dialogue source unit requires only a speaker")
        elif self.kind in {IngressSourceUnitKind.ACTION, IngressSourceUnitKind.STATE}:
            if self.actor_id is None or self.speaker_id is not None:
                raise ContractValidationError("action/state source unit requires only an actor")
        elif self.actor_id is not None or self.speaker_id is not None:
            raise ContractValidationError(
                "narration/instruction source unit cannot grant actor or speaker authority"
            )
        if self.classification_basis not in {
            "explicit_ingress_actor",
            "explicit_ingress_speaker",
            "explicit_ingress_narration",
            "explicit_ingress_instruction",
        }:
            raise ContractValidationError("ingress source-unit basis is not authoritative")

    @property
    def source_unit_sha256(self) -> str:
        return domain_sha256(self.SCHEMA_VERSION, self)


@dataclass(frozen=True, slots=True)
class ContinuousIngressClassificationReceiptV1:
    """Exact output of one trusted prepared-ingress classification adapter."""

    SCHEMA_VERSION: ClassVar[str] = "cera.continuous_ingress_classification_receipt.v2"

    schema_version: str
    envelope_sha256: str
    prepared_turn_sha256: str
    interpretation_receipt_sha256: str
    classification_adapter_id: str
    classifier_descriptor_sha256: str
    protected_user_id: str
    raw_source_sha256: str
    source_units: tuple[IngressSourceUnitV1, ...]

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError(
                "continuous ingress classification schema changed"
            )
        for field in (
            "envelope_sha256",
            "prepared_turn_sha256",
            "interpretation_receipt_sha256",
            "raw_source_sha256",
            "classifier_descriptor_sha256",
        ):
            if not re_is_sha256(getattr(self, field)):
                raise ContractValidationError(
                    f"continuous ingress classification {field} is invalid"
                )
        _identity(
            self.classification_adapter_id,
            "continuous_ingress_classification.classification_adapter_id",
        )
        _identity(
            self.protected_user_id,
            "continuous_ingress_classification.protected_user_id",
        )
        if not self.source_units:
            raise ContractValidationError(
                "continuous ingress classification has no source units"
            )
        _unique(
            tuple(value.source_unit_key for value in self.source_units),
            "continuous_ingress_classification.source_units",
        )

    @property
    def receipt_sha256(self) -> str:
        return domain_sha256(self.SCHEMA_VERSION, self)


@dataclass(frozen=True, slots=True)
class FrozenContinuousIngressFixtureV1:
    """Repository-owned exact fixture entry; prefixes never grant authority."""

    SCHEMA_VERSION: ClassVar[str] = "cera.frozen_continuous_ingress_fixture.v1"

    schema_version: str
    fixture_id: str
    fixture_schema_id: str
    world_id: str
    branch_id: str
    session_id: str
    request_id: str
    turn_id: str
    idempotency_key_sha256: str
    raw_source: str
    protected_user_id: str
    source_units: tuple[IngressSourceUnitV1, ...]

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("continuous fixture schema changed")
        for field in (
            "fixture_id",
            "fixture_schema_id",
            "world_id",
            "branch_id",
            "session_id",
            "request_id",
            "turn_id",
            "protected_user_id",
        ):
            _identity(getattr(self, field), f"continuous_fixture.{field}")
        if not re_is_sha256(self.idempotency_key_sha256):
            raise ContractValidationError(
                "continuous fixture idempotency identity is invalid"
            )
        if not self.raw_source or not self.source_units:
            raise ContractValidationError(
                "continuous fixture requires exact source and source units"
            )
        cursor = 0
        for unit in self.source_units:
            if (
                unit.source_start != cursor
                or unit.source_end > len(self.raw_source)
                or self.raw_source[unit.source_start : unit.source_end]
                != unit.exact_text
            ):
                raise ContractValidationError(
                    "continuous fixture source units are not gap-free exact source"
                )
            cursor = unit.source_end
        if cursor != len(self.raw_source):
            raise ContractValidationError(
                "continuous fixture source units do not cover the raw source"
            )

    @property
    def fixture_sha256(self) -> str:
        return domain_sha256(self.SCHEMA_VERSION, self)


@dataclass(frozen=True, slots=True)
class ContinuousIngressReceiptV2:
    """Restart-safe Python-owned custody receipt for continuous ingress."""

    SCHEMA_VERSION: ClassVar[str] = "cera.continuous_ingress_receipt.v3"

    schema_version: str
    receipt_id: str
    world_id: str
    branch_id: str
    session_id: str
    request_id: str
    turn_id: str
    idempotency_key_sha256: str
    raw_source_sha256: str
    protected_user_id: str
    authority_kind: ContinuousIngressAuthorityKind
    authority_identity_sha256: str
    classification_adapter_id: str
    classifier_descriptor_sha256: str | None
    prepared_envelope_sha256: str | None
    prepared_turn_sha256: str | None
    interpretation_receipt_sha256: str | None
    classification_receipt_sha256: str | None
    fixture_entry_sha256: str | None
    source_units: tuple[IngressSourceUnitV1, ...]

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("continuous ingress receipt schema changed")
        for field in (
            "receipt_id",
            "world_id",
            "branch_id",
            "session_id",
            "request_id",
            "turn_id",
            "protected_user_id",
            "classification_adapter_id",
        ):
            _identity(getattr(self, field), f"continuous_ingress_receipt.{field}")
        for field in (
            "idempotency_key_sha256",
            "raw_source_sha256",
            "authority_identity_sha256",
        ):
            if not re_is_sha256(getattr(self, field)):
                raise ContractValidationError(
                    f"continuous ingress receipt {field} is invalid"
                )
        if not self.source_units:
            raise ContractValidationError("continuous ingress receipt has no source units")
        _unique(
            tuple(value.source_unit_key for value in self.source_units),
            "continuous_ingress_receipt.source_units",
        )
        if (
            self.authority_kind is ContinuousIngressAuthorityKind.FROZEN_PYTHON_FIXTURE
            and self.fixture_entry_sha256 is None
        ):
            raise ContractValidationError(
                "frozen continuous ingress receipt lacks an exact registry entry"
            )
        prepared_fields = (
            self.prepared_envelope_sha256,
            self.prepared_turn_sha256,
            self.interpretation_receipt_sha256,
            self.classification_receipt_sha256,
        )
        if self.authority_kind is ContinuousIngressAuthorityKind.PREPARED_INGRESS:
            if any(value is None or not re_is_sha256(value) for value in prepared_fields):
                raise ContractValidationError(
                    "prepared continuous ingress receipt lacks exact source records"
                )
            if self.fixture_entry_sha256 is not None:
                raise ContractValidationError(
                    "prepared continuous ingress receipt cannot cite a fixture"
                )
            if not re_is_sha256(self.classifier_descriptor_sha256):
                raise ContractValidationError(
                    "prepared continuous ingress receipt lacks classifier identity"
                )
        else:
            if any(value is not None for value in prepared_fields):
                raise ContractValidationError(
                    "frozen continuous ingress receipt cannot cite prepared records"
                )
            if not re_is_sha256(self.fixture_entry_sha256):
                raise ContractValidationError(
                    "frozen continuous ingress registry hash is invalid"
                )
            if self.classifier_descriptor_sha256 is not None:
                raise ContractValidationError(
                    "frozen continuous ingress receipt cannot cite a prepared classifier"
                )
        if text_sha256("".join(value.exact_text for value in self.source_units)) != self.raw_source_sha256:
            raise ContractValidationError(
                "continuous ingress receipt source-unit bytes changed"
            )
        for unit in self.source_units:
            owner = unit.actor_id or unit.speaker_id
            if owner == "character:ted" and owner != self.protected_user_id:
                raise ContractValidationError(
                    "continuous ingress receipt changed protected-user identity"
                )

    @property
    def source_unit_ledger_sha256(self) -> str:
        return canonical_sha256(self.source_units)

    @property
    def receipt_sha256(self) -> str:
        return domain_sha256(self.SCHEMA_VERSION, self)


@dataclass(frozen=True, slots=True)
class CharacterRoleLedgerV1:
    """One closed distinction between assertion owners and non-owning roles."""

    SCHEMA_VERSION: ClassVar[str] = "cera.character_role_ledger.v1"

    schema_version: str = SCHEMA_VERSION
    action_owner_ids: tuple[str, ...] = ()
    state_owner_ids: tuple[str, ...] = ()
    speaker_ids: tuple[str, ...] = ()
    affected_ids: tuple[str, ...] = ()
    addressed_ids: tuple[str, ...] = ()
    observing_ids: tuple[str, ...] = ()
    referenced_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("character role ledger schema changed")
        fields = (
            "action_owner_ids",
            "state_owner_ids",
            "speaker_ids",
            "affected_ids",
            "addressed_ids",
            "observing_ids",
            "referenced_ids",
        )
        for field in fields:
            values = getattr(self, field)
            for value in values:
                _identity(value, f"character_roles.{field}")
            _unique(values, f"character_roles.{field}")
        all_roles = tuple(value for field in fields for value in getattr(self, field))
        if not all_roles:
            raise ContractValidationError("character role ledger is empty")
        if len(all_roles) != len(set(all_roles)):
            raise ContractValidationError(
                "one character cannot hold multiple roles in one scoped assertion"
            )

    @property
    def assertion_owner_ids(self) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(
                (*self.action_owner_ids, *self.state_owner_ids, *self.speaker_ids)
            )
        )

    @property
    def non_owning_ids(self) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(
                (
                    *self.affected_ids,
                    *self.addressed_ids,
                    *self.observing_ids,
                    *self.referenced_ids,
                )
            )
        )

    @property
    def involved_ids(self) -> tuple[str, ...]:
        return (*self.assertion_owner_ids, *self.non_owning_ids)


@dataclass(frozen=True, slots=True)
class ProtectedUserSourceClaimV1:
    """Ingress-owned exact protected-user event or utterance."""

    SCHEMA_VERSION: ClassVar[str] = "cera.protected_user_source_claim.v3"

    schema_version: str
    claim_key: str
    kind: ProtectedUserSourceClaimKind
    source_binding_key: str
    source_sha256: str
    source_start: int
    source_end: int
    exact_text: str
    source_unit_key: str
    source_unit_kind: IngressSourceUnitKind
    speaker_id: str = "character:ted"
    deterministic_projection_rule: str = "explicit_ingress_source_unit"

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("protected-user source claim schema changed")
        _key(self.claim_key, "protected_user_source_claim.claim_key")
        _key(self.source_binding_key, "protected_user_source_claim.source_binding_key")
        if not re_is_sha256(self.source_sha256):
            raise ContractValidationError("protected-user source hash is invalid")
        if (
            type(self.source_start) is not int
            or type(self.source_end) is not int
            or self.source_start < 0
            or self.source_end <= self.source_start
        ):
            raise ContractValidationError("protected-user source span is invalid")
        _text(self.exact_text, "protected_user_source_claim.exact_text", maximum=8_000)
        if self.source_end - self.source_start != len(self.exact_text):
            raise ContractValidationError("protected-user source span length changed")
        if self.speaker_id != "character:ted":
            raise ContractValidationError("protected-user source claim speaker changed")
        _key(self.source_unit_key, "protected_user_source_claim.source_unit_key")
        if self.source_unit_kind not in {
            IngressSourceUnitKind.ACTION,
            IngressSourceUnitKind.DIALOGUE,
            IngressSourceUnitKind.STATE,
        }:
            raise ContractValidationError("protected-user claim uses a non-authorizing source unit")
        if (
            self.kind is ProtectedUserSourceClaimKind.DIALOGUE
            and self.source_unit_kind is not IngressSourceUnitKind.DIALOGUE
        ) or (
            self.kind is ProtectedUserSourceClaimKind.ACTION_OR_STATE
            and self.source_unit_kind
            not in {IngressSourceUnitKind.ACTION, IngressSourceUnitKind.STATE}
        ):
            raise ContractValidationError("protected-user claim kind changed ingress meaning")
        _identity(
            self.deterministic_projection_rule,
            "protected_user_source_claim.deterministic_projection_rule",
        )

    @property
    def claim_sha256(self) -> str:
        return domain_sha256(self.SCHEMA_VERSION, self)


@dataclass(frozen=True, slots=True)
class ProtectedUserAllowanceV1:
    mode: ProtectedUserAllowanceMode
    source_binding_keys: tuple[str, ...]
    explanation: str
    source_claim_keys: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _text(self.explanation, "protected_user_allowance.explanation", maximum=1_000)
        for value in self.source_binding_keys:
            _key(value, "protected_user_allowance.source_binding_keys")
        _unique(self.source_binding_keys, "protected_user_allowance.source_binding_keys")
        for value in self.source_claim_keys:
            _key(value, "protected_user_allowance.source_claim_keys")
        _unique(self.source_claim_keys, "protected_user_allowance.source_claim_keys")
        if self.mode in {
            ProtectedUserAllowanceMode.EXACT_SOURCE_ONLY,
            ProtectedUserAllowanceMode.MINIMAL_NONBRANCHING_CONNECTIVE,
        } and not self.source_binding_keys:
            raise ContractValidationError(
                "protected-user allowance requires Python-owned bindings"
            )
        if self.mode is ProtectedUserAllowanceMode.NONE and self.source_binding_keys:
            raise ContractValidationError("no protected-user allowance cannot carry source bindings")
        if self.mode is ProtectedUserAllowanceMode.NONE and self.source_claim_keys:
            raise ContractValidationError("no protected-user allowance cannot carry source claims")
        if (
            self.mode is ProtectedUserAllowanceMode.MINIMAL_NONBRANCHING_CONNECTIVE
            and self.source_claim_keys
        ):
            raise ContractValidationError("mechanical connective cannot carry semantic source claims")


@dataclass(frozen=True, slots=True)
class RichSequenceBeatV1:
    """One causal unit; not a line of prose or a fixed-size micro-action."""

    beat_key: str
    roles: CharacterRoleLedgerV1
    evidence_grounded_perception: str
    immediate_goal: str
    relevant_character_pressures: tuple[str, ...]
    competing_obligation_or_constraint: str | None
    selected_tactic: str
    causal_explanation: str
    observable_action_or_dialogue_direction: str
    private_state_guidance: str
    physical_material_continuity: str
    resulting_state: str
    deepseek_realization_space: tuple[str, ...]
    protected_user_allowance: ProtectedUserAllowanceV1
    source_evidence_bindings: tuple[str, ...]

    def __post_init__(self) -> None:
        _key(self.beat_key, "beat_key")
        if not self.roles.assertion_owner_ids:
            raise ContractValidationError(
                "rich sequence beat requires an action, state, or dialogue owner"
            )
        for field in (
            "evidence_grounded_perception",
            "immediate_goal",
            "selected_tactic",
            "causal_explanation",
            "observable_action_or_dialogue_direction",
            "private_state_guidance",
            "physical_material_continuity",
            "resulting_state",
        ):
            _text(getattr(self, field), field, maximum=4_000)
        if self.competing_obligation_or_constraint is not None:
            _text(
                self.competing_obligation_or_constraint,
                "competing_obligation_or_constraint",
                maximum=2_000,
            )
        if not self.relevant_character_pressures or not self.deepseek_realization_space:
            raise ContractValidationError(
                "rich sequence beat requires pressure and realization space"
            )
        for field in ("relevant_character_pressures", "deepseek_realization_space"):
            values = getattr(self, field)
            for value in values:
                _text(value, field, maximum=2_000)
            _unique(values, field)
        if not self.source_evidence_bindings:
            raise ContractValidationError("rich sequence beat requires source/evidence bindings")
        for value in self.source_evidence_bindings:
            _key(value, "source_evidence_bindings")
        _unique(self.source_evidence_bindings, "source_evidence_bindings")
        reasoning = (
            self.evidence_grounded_perception,
            self.immediate_goal,
            self.selected_tactic,
            self.causal_explanation,
            self.resulting_state,
        )
        normalized = {" ".join(value.casefold().split()) for value in reasoning}
        if len(normalized) < 4 or any(len(value.split()) < 3 for value in reasoning):
            raise ContractValidationError("rich sequence beat is structurally shallow")
        if (
            "character:ted" in self.roles.assertion_owner_ids
            and self.protected_user_allowance.mode
            is not ProtectedUserAllowanceMode.EXACT_SOURCE_ONLY
        ):
            raise ContractValidationError(
                "protected user can be an actor only through exact supplied source"
            )


@dataclass(frozen=True, slots=True)
class RichPlannerSequenceV1:
    SCHEMA_VERSION: ClassVar[str] = "cera.rich_planner_sequence.v4"

    schema_version: str
    sequence_id: str
    world_id: str
    branch_id: str
    accepted_turn_id: str | None
    scene_id: str
    selected_character_ids: tuple[str, ...]
    omitted_character_ids: tuple[str, ...]
    beats: tuple[RichSequenceBeatV1, ...]
    final_stop_state: str
    unresolved_threads: tuple[str, ...]
    provisional: bool

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("rich Planner sequence schema version changed")
        for field in ("sequence_id", "world_id", "branch_id", "scene_id"):
            _identity(getattr(self, field), field)
        if self.accepted_turn_id is not None:
            raise ContractValidationError(
                "current Planner output accepted_turn_id must be null"
            )
        if self.provisional is not True:
            raise ContractValidationError(
                "current Planner output provisional must be true"
            )
        if not self.selected_character_ids or not self.beats:
            raise ContractValidationError("rich Planner sequence requires cast and beats")
        for field in ("selected_character_ids", "omitted_character_ids"):
            values = getattr(self, field)
            for value in values:
                _identity(value, field)
            _unique(values, field)
        if set(self.selected_character_ids).intersection(self.omitted_character_ids):
            raise ContractValidationError("selected and omitted characters overlap")
        _unique(tuple(value.beat_key for value in self.beats), "beat keys")
        selected = set(self.selected_character_ids)
        for beat in self.beats:
            npc_actors = {
                value
                for value in beat.roles.assertion_owner_ids
                if value != "character:ted"
            }
            if not npc_actors.issubset(selected):
                raise ContractValidationError("rich sequence beat names an unselected character")
        _text(self.final_stop_state, "final_stop_state", maximum=4_000)
        for value in self.unresolved_threads:
            _text(value, "unresolved_threads", maximum=2_000)
        _unique(self.unresolved_threads, "unresolved_threads")

    @property
    def sequence_sha256(self) -> str:
        return domain_sha256(self.SCHEMA_VERSION, self)


@dataclass(frozen=True, slots=True)
class CharacterSummaryEnvelopeV1:
    """Exact record-derived summary; the class name remains for API continuity."""

    SCHEMA_VERSION: ClassVar[str] = "cera.character_summary_envelope.v3"

    schema_version: str
    character_id: str
    source_path_or_record_id: str
    source_revision: int
    source_sha256: str
    source_authority_classification: str
    summary_field_path: str
    latest_changes_field_path: str
    latest_accepted_changes: tuple[str, ...]
    summary: str
    derivation_receipt_sha256: str
    incomplete: bool = True
    more_information_available: bool = True

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("character summary schema version changed")
        _identity(self.character_id, "character_id")
        if self.source_path_or_record_id.startswith("record:"):
            _identity(self.source_path_or_record_id, "source_path_or_record_id")
        else:
            _relative_path(self.source_path_or_record_id, "source_path_or_record_id")
        if type(self.source_revision) is not int or self.source_revision < 1:
            raise ContractValidationError("character summary revision must be positive")
        if not re_is_sha256(self.source_sha256):
            raise ContractValidationError("character summary source hash is invalid")
        if self.source_authority_classification != "active_authoritative_record_fields":
            raise ContractValidationError(
                "character summary authority classification is invalid"
            )
        for field in ("summary_field_path", "latest_changes_field_path"):
            value = getattr(self, field)
            if not isinstance(value, str) or not value.startswith("/"):
                raise ContractValidationError(
                    f"character summary {field} must be a JSON pointer"
                )
        for value in self.latest_accepted_changes:
            _text(value, "latest_accepted_changes", maximum=2_000)
        _unique(self.latest_accepted_changes, "latest_accepted_changes")
        _text(self.summary, "summary", maximum=16_000)
        if not self.incomplete or not self.more_information_available:
            raise ContractValidationError(
                "V2 character summary must disclose that it is incomplete and expandable"
            )
        if not re_is_sha256(self.derivation_receipt_sha256):
            raise ContractValidationError(
                "character summary derivation receipt hash is invalid"
            )
        expected = canonical_sha256(
            {
                "schema_version": self.SCHEMA_VERSION,
                "character_id": self.character_id,
                "source_path_or_record_id": self.source_path_or_record_id,
                "source_revision": self.source_revision,
                "source_sha256": self.source_sha256,
                "source_authority_classification": self.source_authority_classification,
                "summary_field_path": self.summary_field_path,
                "latest_changes_field_path": self.latest_changes_field_path,
                "summary": self.summary,
                "latest_accepted_changes": self.latest_accepted_changes,
            }
        )
        if self.derivation_receipt_sha256 != expected:
            raise ContractValidationError(
                "character summary derivation receipt does not match its exact fields"
            )

    @property
    def envelope_sha256(self) -> str:
        return domain_sha256(self.SCHEMA_VERSION, self)


@dataclass(frozen=True, slots=True)
class AcceptedFinalSequenceEnvelopeV1:
    SCHEMA_VERSION: ClassVar[str] = "cera.accepted_final_sequence_envelope.v1"

    schema_version: str
    accepted_turn_id: str
    user_message: str
    complete_final_sequence: FinalSequenceV1
    acceptance_receipt_sha256: str

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("accepted sequence envelope schema changed")
        _identity(self.accepted_turn_id, "accepted_turn_id")
        _text(self.user_message, "user_message", maximum=64_000)
        if self.complete_final_sequence.accepted_turn_id != self.accepted_turn_id:
            raise ContractValidationError("accepted sequence turn identity changed")
        if not re_is_sha256(self.acceptance_receipt_sha256):
            raise ContractValidationError("accepted sequence receipt hash is invalid")

    @property
    def envelope_sha256(self) -> str:
        return domain_sha256(self.SCHEMA_VERSION, self)

    def render_for_planner(
        self,
        *,
        stable_reference_descriptors: tuple[dict[str, Any], ...] = (),
    ) -> str:
        from cera.serialization import to_primitive
        import json

        return (
            "[TURN ACCEPTED]\n"
            f"[USER MESSAGE {self.accepted_turn_id}]\n{self.user_message}\n"
            f"[COMPLETE FINAL SEQUENCE {self.accepted_turn_id}]\n"
            + json.dumps(
                to_primitive(self.complete_final_sequence),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + f"\n[ACCEPTANCE IDENTITY {self.accepted_turn_id}]\n"
            + self.acceptance_receipt_sha256
            + f"\n[STABLE ACCEPTED REFERENCES {self.accepted_turn_id}]\n"
            + json.dumps(
                stable_reference_descriptors,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        )


class ValidatorTaskMode(StrEnum):
    FINALIZE_TURN = "finalize_turn"
    SCENE_SUMMARY = "scene_summary"


class ValidatorSemanticStatus(StrEnum):
    ACCEPTED = "accepted"
    CONCERN = "concern"
    REJECTED = "rejected"
    INCONCLUSIVE = "inconclusive"
    ERROR = "error"


class WorldEditOperationKind(StrEnum):
    ADD = "add"
    REPLACE = "replace"
    REMOVE = "remove"
    APPEND_UNIQUE = "append_unique"
    INCREMENT = "increment"
    CREATE_FILE = "create_file"


class FinalFieldName(StrEnum):
    """Closed vocabulary for fields that can become accepted final truth."""

    REALIZED_EVENT = "realized_event"
    VALID_DEEPSEEK_ADDITIONS = "valid_deepseek_additions"
    KNOWLEDGE_CHANGES = "knowledge_changes"
    MATERIAL_CHANGES = "material_changes"
    RESULTING_STATE = "resulting_state"


def _final_field_name(value: FinalFieldName | str, field: str) -> FinalFieldName:
    try:
        return FinalFieldName(value)
    except (TypeError, ValueError) as exc:
        raise ContractValidationError(f"{field} is invalid") from exc


class PersistenceRecordClass(StrEnum):
    """Closed record-class vocabulary; V1 enables only typed subject schemas."""

    CHARACTER = "character"
    RELATIONSHIP = "relationship"
    RULE = "rule"
    LOCATION = "location"
    EVENT = "event"
    SCENE = "scene"


@dataclass(frozen=True, slots=True)
class PersistenceDirectiveV1:
    """Validator-selected destination; Python derives the exact edit value."""

    SCHEMA_VERSION: ClassVar[str] = "cera.persistence_directive.v2"

    schema_version: str
    directive_key: str
    target_file: str
    target_record_class: PersistenceRecordClass
    persistence_policy_sha256: str
    target_record_id: str
    target_subject_ids: tuple[str, ...]
    expected_file_revision: int
    operation: WorldEditOperationKind
    field_path: str
    expected_prior_value_sha256: str | None
    source_value_index: int

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("persistence directive schema changed")
        _key(self.directive_key, "persistence_directive.directive_key")
        _relative_path(self.target_file, "persistence_directive.target_file")
        if self.target_record_class not in {
            PersistenceRecordClass.CHARACTER,
            PersistenceRecordClass.RELATIONSHIP,
        }:
            raise ContractValidationError(
                "continuous persistence V8 supports only character or relationship "
                "records with typed subject schemas"
            )
        if self.persistence_policy_sha256 != PERSISTENCE_POLICY_SHA256:
            raise ContractValidationError(
                "persistence directive uses a stale writable-path policy"
            )
        expected_category = {
            PersistenceRecordClass.CHARACTER: "Characters",
            PersistenceRecordClass.RELATIONSHIP: "Relationships",
        }[self.target_record_class]
        normalized = self.target_file.replace("\\", "/")
        if (
            normalized.split("/", 1)[0] != expected_category
            or not normalized.casefold().endswith(".json")
        ):
            raise ContractValidationError(
                "persistence directive record class disagrees with its target"
            )
        _identity(self.target_record_id, "persistence_directive.target_record_id")
        if not self.target_subject_ids:
            raise ContractValidationError("persistence directive requires exact subjects")
        for value in self.target_subject_ids:
            _identity(value, "persistence_directive.target_subject_ids")
        _unique(self.target_subject_ids, "persistence_directive.target_subject_ids")
        if type(self.expected_file_revision) is not int or self.expected_file_revision < 1:
            raise ContractValidationError(
                "persistence directive requires a positive record revision"
            )
        if self.operation not in {
            WorldEditOperationKind.ADD,
            WorldEditOperationKind.REPLACE,
        }:
            raise ContractValidationError(
                "continuous persistence supports only exact add or replace projections"
            )
        validate_persistence_field_path(self.target_record_class, self.field_path)
        if self.operation is WorldEditOperationKind.ADD:
            if self.expected_prior_value_sha256 is not None:
                raise ContractValidationError(
                    "add persistence directive cannot bind a prior value"
                )
        elif not re_is_sha256(self.expected_prior_value_sha256):
            raise ContractValidationError(
                "replace persistence directive requires the exact prior value hash"
            )
        if type(self.source_value_index) is not int or self.source_value_index < 0:
            raise ContractValidationError(
                "persistence directive source value index is invalid"
            )


@dataclass(frozen=True, slots=True)
class WorldEditOperationV1:
    operation_key: str
    target_file: str
    expected_file_revision: int | None
    operation: WorldEditOperationKind
    field_path: str
    value: Any
    reason: str
    source_final_sequence_item: str
    source_final_field_name: FinalFieldName = FinalFieldName.REALIZED_EVENT
    protected_user_source_claim_keys: tuple[str, ...] = ()
    persistence_directive_key: str | None = None

    def __post_init__(self) -> None:
        _key(self.operation_key, "operation_key")
        _relative_path(self.target_file, "target_file")
        normalized_target = self.target_file.replace("\\", "/")
        if (
            normalized_target.split("/", 1)[0]
            not in {"Characters", "Relationships", "Rules", "Locations", "Events", "Scenes"}
            or not normalized_target.casefold().endswith(".json")
        ):
            raise ContractValidationError(
                "world edit target must be a JSON record in an approved ACTIVE category"
            )
        if self.expected_file_revision is not None and (
            type(self.expected_file_revision) is not int
            or self.expected_file_revision < 0
        ):
            raise ContractValidationError("expected file revision is invalid")
        if self.operation is WorldEditOperationKind.CREATE_FILE:
            if self.expected_file_revision is not None or self.field_path != "/":
                raise ContractValidationError("create_file requires absent-file precondition and root path")
            if not isinstance(self.value, dict):
                raise ContractValidationError(
                    "mutable create_file value must be a revisioned JSON object"
                )
        else:
            if self.expected_file_revision is None:
                raise ContractValidationError("existing-file edit requires a revision precondition")
            if not self.field_path.startswith("/"):
                raise ContractValidationError("world edit field_path must be a JSON pointer")
            if self.field_path in {"/_cera_revision", "/schema_version"}:
                raise ContractValidationError("Validator cannot edit Python-owned file metadata")
        _text(self.reason, "operation.reason", maximum=2_000)
        _key(self.source_final_sequence_item, "source_final_sequence_item")
        object.__setattr__(
            self,
            "source_final_field_name",
            _final_field_name(
                self.source_final_field_name,
                "world edit source final field",
            ),
        )
        for value in self.protected_user_source_claim_keys:
            _key(value, "operation.protected_user_source_claim_keys")
        _unique(
            self.protected_user_source_claim_keys,
            "operation.protected_user_source_claim_keys",
        )
        if self.persistence_directive_key is not None:
            _key(
                self.persistence_directive_key,
                "operation.persistence_directive_key",
            )


@dataclass(frozen=True, slots=True)
class CreatedFieldLogEntryV1:
    target_file: str
    field_path: str
    value_type: str
    value: Any
    reason: str
    source_final_sequence_item: str
    source_final_field_name: FinalFieldName = FinalFieldName.REALIZED_EVENT
    protected_user_source_claim_keys: tuple[str, ...] = ()
    persistence_directive_key: str | None = None

    def __post_init__(self) -> None:
        _relative_path(self.target_file, "created_field.target_file")
        if not self.field_path.startswith("/"):
            raise ContractValidationError("created field path must be a JSON pointer")
        if self.value_type not in {"null", "boolean", "integer", "number", "string", "array", "object"}:
            raise ContractValidationError("created field value_type is invalid")
        _text(self.reason, "created_field.reason", maximum=2_000)
        _key(self.source_final_sequence_item, "created_field.source_final_sequence_item")
        object.__setattr__(
            self,
            "source_final_field_name",
            _final_field_name(
                self.source_final_field_name,
                "created field source final field",
            ),
        )
        for value in self.protected_user_source_claim_keys:
            _key(value, "created_field.protected_user_source_claim_keys")
        _unique(
            self.protected_user_source_claim_keys,
            "created_field.protected_user_source_claim_keys",
        )
        if self.persistence_directive_key is not None:
            _key(
                self.persistence_directive_key,
                "created_field.persistence_directive_key",
            )


class FinalInformationVisibility(StrEnum):
    PUBLIC = "public"
    CHARACTER_PRIVATE = "character_private"


@dataclass(frozen=True, slots=True)
class FinalFieldScopeV1:
    field_name: FinalFieldName
    visibility: FinalInformationVisibility
    knowledge_owner_id: str | None
    story_segment_keys: tuple[str, ...]
    roles: CharacterRoleLedgerV1
    protected_user_source_claim_keys: tuple[str, ...] = ()
    persistence_directives: tuple[PersistenceDirectiveV1, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "field_name",
            _final_field_name(
                self.field_name,
                "final field scope name",
            ),
        )
        if self.visibility is FinalInformationVisibility.PUBLIC:
            if self.knowledge_owner_id is not None:
                raise ContractValidationError("public final field cannot have a private owner")
        else:
            if self.knowledge_owner_id is None:
                raise ContractValidationError("private final field requires one owner")
            _identity(self.knowledge_owner_id, "final_field_scope.knowledge_owner_id")
        if not self.story_segment_keys:
            raise ContractValidationError("final field scope requires Composer segments")
        for value in self.story_segment_keys:
            _key(value, "final_field_scope.story_segment_keys")
        _unique(self.story_segment_keys, "final_field_scope.story_segment_keys")
        for value in self.protected_user_source_claim_keys:
            _key(value, "final_field_scope.protected_user_source_claim_keys")
        _unique(
            self.protected_user_source_claim_keys,
            "final_field_scope.protected_user_source_claim_keys",
        )
        if (
            "character:ted" in self.roles.assertion_owner_ids
        ) != bool(self.protected_user_source_claim_keys):
            raise ContractValidationError(
                "protected-user final field authorship must match exact claims"
            )
        _unique(
            tuple(value.directive_key for value in self.persistence_directives),
            "final_field_scope.persistence_directives",
        )


@dataclass(frozen=True, slots=True)
class FinalSequenceItemV1:
    item_key: str
    planner_beat_keys: tuple[str, ...]
    story_segment_keys: tuple[str, ...]
    realized_event: str
    valid_deepseek_additions: tuple[str, ...]
    omitted_or_contradicted_details: tuple[str, ...]
    private_state_owner_ids: tuple[str, ...]
    knowledge_changes: tuple[str, ...]
    material_changes: tuple[str, ...]
    resulting_state: str
    roles: CharacterRoleLedgerV1
    protected_user_source_claim_keys: tuple[str, ...] = ()
    field_scopes: tuple[FinalFieldScopeV1, ...] = ()

    def __post_init__(self) -> None:
        _key(self.item_key, "final_sequence.item_key")
        if not self.planner_beat_keys:
            raise ContractValidationError("final sequence item requires Planner bindings")
        for value in self.planner_beat_keys:
            _key(value, "final_sequence.planner_beat_keys")
        _unique(self.planner_beat_keys, "final_sequence.planner_beat_keys")
        if not self.story_segment_keys:
            raise ContractValidationError("final sequence item requires Composer segment bindings")
        for value in self.story_segment_keys:
            _key(value, "final_sequence.story_segment_keys")
        _unique(self.story_segment_keys, "final_sequence.story_segment_keys")
        for field in ("realized_event", "resulting_state"):
            _text(getattr(self, field), f"final_sequence.{field}", maximum=8_000)
        for field in (
            "valid_deepseek_additions",
            "omitted_or_contradicted_details",
            "knowledge_changes",
            "material_changes",
        ):
            values = getattr(self, field)
            for value in values:
                _text(value, f"final_sequence.{field}", maximum=4_000)
            _unique(values, f"final_sequence.{field}")
        for value in self.private_state_owner_ids:
            _identity(value, "final_sequence.private_state_owner_ids")
        _unique(self.private_state_owner_ids, "final_sequence.private_state_owner_ids")
        if len(self.private_state_owner_ids) > 1:
            raise ContractValidationError(
                "final sequence private state must have one exact owner"
            )
        for value in self.protected_user_source_claim_keys:
            _key(value, "final_sequence.protected_user_source_claim_keys")
        _unique(
            self.protected_user_source_claim_keys,
            "final_sequence.protected_user_source_claim_keys",
        )
        scope_names = tuple(value.field_name for value in self.field_scopes)
        _unique(scope_names, "final_sequence.field_scopes")
        required_scopes = {
            FinalFieldName.REALIZED_EVENT,
            FinalFieldName.RESULTING_STATE,
        }
        for field in (
            FinalFieldName.VALID_DEEPSEEK_ADDITIONS,
            FinalFieldName.KNOWLEDGE_CHANGES,
            FinalFieldName.MATERIAL_CHANGES,
        ):
            if getattr(self, field):
                required_scopes.add(field)
        if set(scope_names) != required_scopes:
            raise ContractValidationError("final sequence field visibility is incomplete")
        if {
            segment
            for scope in self.field_scopes
            for segment in scope.story_segment_keys
        } != set(self.story_segment_keys):
            raise ContractValidationError(
                "final sequence field scopes do not cover exact Composer segments"
            )
        scoped_roles = {
            field: {
                identity
                for scope in self.field_scopes
                for identity in getattr(scope.roles, field)
            }
            for field in (
                "action_owner_ids",
                "state_owner_ids",
                "speaker_ids",
                "affected_ids",
                "addressed_ids",
                "observing_ids",
                "referenced_ids",
            )
        }
        if any(
            scoped_roles[field] != set(getattr(self.roles, field))
            for field in scoped_roles
        ):
            raise ContractValidationError(
                "final sequence character roles disagree with field scopes"
            )
        if {
            claim
            for scope in self.field_scopes
            for claim in scope.protected_user_source_claim_keys
        } != set(self.protected_user_source_claim_keys):
            raise ContractValidationError(
                "final sequence claims disagree with field scopes"
            )
        scoped_private_owners = {
            value.knowledge_owner_id
            for value in self.field_scopes
            if value.visibility is FinalInformationVisibility.CHARACTER_PRIVATE
        }
        if scoped_private_owners != set(self.private_state_owner_ids):
            raise ContractValidationError("final private owners disagree with field scopes")
        if not set(self.private_state_owner_ids).issubset(
            set(self.roles.involved_ids)
        ):
            raise ContractValidationError(
                "final private owner has no declared character role"
            )
        protected_authorship = "character:ted" in self.roles.assertion_owner_ids
        if protected_authorship != bool(self.protected_user_source_claim_keys):
            raise ContractValidationError(
                "protected-user final authorship must match exact supplied claims"
            )


@dataclass(frozen=True, slots=True)
class FinalSequenceV1:
    SCHEMA_VERSION: ClassVar[str] = "cera.complete_final_sequence.v6"

    schema_version: str
    sequence_id: str
    accepted_turn_id: str
    items: tuple[FinalSequenceItemV1, ...]
    final_stop_state: str

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("complete final sequence schema changed")
        _identity(self.sequence_id, "final_sequence.sequence_id")
        _identity(self.accepted_turn_id, "final_sequence.accepted_turn_id")
        if not self.items:
            raise ContractValidationError("complete final sequence cannot be empty")
        _unique(tuple(value.item_key for value in self.items), "final sequence items")
        _text(self.final_stop_state, "final_sequence.final_stop_state", maximum=4_000)
        if self.final_stop_state != self.items[-1].resulting_state:
            raise ContractValidationError(
                "final stop state must be the exact last resulting state"
            )

    @property
    def sequence_sha256(self) -> str:
        return domain_sha256(self.SCHEMA_VERSION, self)


@dataclass(frozen=True, slots=True)
class EventItemRoleLedgerV1:
    """Python-derived role custody for one accepted final item."""

    SCHEMA_VERSION: ClassVar[str] = "cera.event_item_role_ledger.v1"

    schema_version: str
    final_sequence_item_key: str
    roles: CharacterRoleLedgerV1

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("event item role ledger schema changed")
        _key(self.final_sequence_item_key, "event_item_role.final_sequence_item_key")


@dataclass(frozen=True, slots=True)
class EventRecordCandidateV1:
    event_id: str
    accepted_turn_id: str
    scene_id: str
    participant_ids: tuple[str, ...]
    item_role_ledgers: tuple[EventItemRoleLedgerV1, ...]
    summary: str
    final_sequence_item_keys: tuple[str, ...]
    protected_user_source_claim_keys: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for field in ("event_id", "accepted_turn_id", "scene_id"):
            _identity(getattr(self, field), field)
        for value in self.participant_ids:
            _identity(value, "participant_ids")
        _unique(self.participant_ids, "participant_ids")
        if not self.final_sequence_item_keys:
            raise ContractValidationError("event record requires final sequence items")
        for value in self.final_sequence_item_keys:
            _key(value, "final_sequence_item_keys")
        _unique(self.final_sequence_item_keys, "final_sequence_item_keys")
        if tuple(value.final_sequence_item_key for value in self.item_role_ledgers) != (
            self.final_sequence_item_keys
        ):
            raise ContractValidationError(
                "event item role ledgers do not match final sequence items"
            )
        role_participants = {
            identity
            for value in self.item_role_ledgers
            for identity in value.roles.involved_ids
        }
        if set(self.participant_ids) != role_participants:
            raise ContractValidationError(
                "event participants do not equal event item roles"
            )
        _text(self.summary, "event.summary", maximum=16_000)
        for value in self.protected_user_source_claim_keys:
            _key(value, "event.protected_user_source_claim_keys")
        _unique(
            self.protected_user_source_claim_keys,
            "event.protected_user_source_claim_keys",
        )


@dataclass(frozen=True, slots=True)
class ProtectedUserRealizationSpanV1:
    """Historical/provider-neutral exact occurrence of one protected-user claim."""

    SCHEMA_VERSION: ClassVar[str] = "cera.protected_user_realization_span.v1"

    schema_version: str
    claim_key: str
    kind: ProtectedUserSourceClaimKind
    output_start: int
    output_end: int
    exact_text: str

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("protected-user realization span schema changed")
        _key(self.claim_key, "protected_user_realization.claim_key")
        if (
            type(self.output_start) is not int
            or type(self.output_end) is not int
            or self.output_start < 0
            or self.output_end <= self.output_start
        ):
            raise ContractValidationError("protected-user realization span is invalid")
        _text(self.exact_text, "protected_user_realization.exact_text", maximum=8_000)
        if self.output_end - self.output_start != len(self.exact_text):
            raise ContractValidationError(
                "protected-user realization span length changed"
            )


@dataclass(frozen=True, slots=True)
class WriterParagraphRangeV1:
    """Python-derived paragraph custody; it carries no prose semantics."""

    SCHEMA_VERSION: ClassVar[str] = "cera.writer_paragraph_range.v1"

    schema_version: str
    paragraph_index: int
    output_start: int
    output_end: int
    exact_text_sha256: str

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("Writer paragraph range schema changed")
        if (
            type(self.paragraph_index) is not int
            or self.paragraph_index < 0
            or type(self.output_start) is not int
            or type(self.output_end) is not int
            or self.output_start < 0
            or self.output_end <= self.output_start
        ):
            raise ContractValidationError("Writer paragraph range is invalid")
        if not re_is_sha256(self.exact_text_sha256):
            raise ContractValidationError("Writer paragraph hash is invalid")


@dataclass(frozen=True, slots=True)
class WriterMechanicalEnvelopeV1:
    """Exact Python custody for Writer bytes without semantic interpretation."""

    SCHEMA_VERSION: ClassVar[str] = "cera.writer_mechanical_envelope.v1"

    schema_version: str
    candidate_id: str
    story_text_sha256: str
    utf8_byte_count: int
    codepoint_count: int
    paragraph_ranges: tuple[WriterParagraphRangeV1, ...]

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("Writer mechanical envelope schema changed")
        _identity(self.candidate_id, "writer_mechanical_envelope.candidate_id")
        if not re_is_sha256(self.story_text_sha256):
            raise ContractValidationError("Writer text hash is invalid")
        if (
            type(self.utf8_byte_count) is not int
            or self.utf8_byte_count < 1
            or type(self.codepoint_count) is not int
            or self.codepoint_count < 1
        ):
            raise ContractValidationError("Writer text counts are invalid")
        if not self.paragraph_ranges:
            raise ContractValidationError("Writer envelope has no paragraph ranges")
        expected_index = 0
        prior_end = -1
        for value in self.paragraph_ranges:
            if value.paragraph_index != expected_index or value.output_start <= prior_end:
                raise ContractValidationError(
                    "Writer paragraph ranges are reordered or overlapping"
                )
            expected_index += 1
            prior_end = value.output_end

    @classmethod
    def from_story_text(
        cls,
        *,
        candidate_id: str,
        story_text: str,
    ) -> "WriterMechanicalEnvelopeV1":
        if (
            not isinstance(story_text, str)
            or not story_text.strip()
            or len(story_text) > 256_000
            or "\x00" in story_text
        ):
            raise ContractValidationError("Writer story text is mechanically invalid")
        try:
            encoded = story_text.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise ContractValidationError(
                "Writer story text is not valid UTF-8"
            ) from exc
        ranges: list[WriterParagraphRangeV1] = []
        cursor = 0
        for part in story_text.split("\n\n"):
            start = cursor
            end = start + len(part)
            if part:
                ranges.append(
                    WriterParagraphRangeV1(
                        schema_version=WriterParagraphRangeV1.SCHEMA_VERSION,
                        paragraph_index=len(ranges),
                        output_start=start,
                        output_end=end,
                        exact_text_sha256=text_sha256(part),
                    )
                )
            cursor = end + 2
        return cls(
            schema_version=cls.SCHEMA_VERSION,
            candidate_id=candidate_id,
            story_text_sha256=text_sha256(story_text),
            utf8_byte_count=len(encoded),
            codepoint_count=len(story_text),
            paragraph_ranges=tuple(ranges),
        )

    def validate_story_text(self, story_text: str) -> None:
        rebuilt = self.from_story_text(
            candidate_id=self.candidate_id,
            story_text=story_text,
        )
        if rebuilt != self:
            raise ContractValidationError(
                "Writer mechanical envelope does not match exact story bytes"
            )


class StoryRealizationKind(StrEnum):
    ACTION = "action"
    DIALOGUE = "dialogue"
    PRIVATE_STATE = "private_state"
    CONSENT_OR_DECISION = "consent_or_decision"
    NARRATION = "narration"


class RealizationAuthorityDisposition(StrEnum):
    """Validator-owned authority status for one exact Writer span."""

    PRESENTATION_ONLY = "presentation_only"
    STORY_MATERIAL_ASSERTION = "story_material_assertion"


class PresentationRealizationClass(StrEnum):
    """Closed transient classes that may remain visible without becoming canon."""

    EXPRESSION = "expression"
    GAZE = "gaze"
    BRIEF_PAUSE = "brief_pause"
    CADENCE = "cadence"
    ORDINARY_POSTURE = "ordinary_posture"
    NONPERSISTENT_ATMOSPHERE = "nonpersistent_atmosphere"


class ProhibitedWriterDetailClass(StrEnum):
    """Closed feedback classes for unsupported continuity-significant prose."""

    NEW_CONTINUITY_OBJECT = "new_continuity_object"
    UNSUPPORTED_TASK_OR_EVENT = "unsupported_task_or_event"
    RELOCATION_OR_DURABLE_POSITION = "relocation_or_durable_position"
    MATERIAL_OR_SCENE_STATE_CHANGE = "material_or_scene_state_change"
    RELATIONSHIP_MEMORY_OR_KNOWLEDGE = "relationship_memory_or_knowledge"
    UNAUTHORIZED_PRIVATE_FACT = "unauthorized_private_fact"
    PROTECTED_USER_BEHAVIOR = "protected_user_behavior"
    PLANNER_SEQUENCE_DEPARTURE = "planner_sequence_departure"
    STOPPING_BOUNDARY_VIOLATION = "stopping_boundary_violation"


@dataclass(frozen=True, slots=True)
class WriterRealizationBoundaryV1:
    """Closed shared Writer/Validator boundary for transient visible detail."""

    SCHEMA_VERSION: ClassVar[str] = "cera.writer_realization_boundary.v1"

    schema_version: str
    presentation_classes: tuple[PresentationRealizationClass, ...]
    continuity_significant_classes: tuple[ProhibitedWriterDetailClass, ...]
    presentation_enters_final_sequence: bool
    presentation_enters_events_or_material_changes: bool
    presentation_enters_memory_relationship_or_summary: bool
    presentation_enters_accepted_context_or_persistence: bool
    presentation_enters_canon: bool
    semantic_classifier: str

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("Writer realization boundary schema changed")
        if self.presentation_classes != tuple(PresentationRealizationClass):
            raise ContractValidationError(
                "Writer realization boundary changed its closed presentation classes"
            )
        if self.continuity_significant_classes != tuple(ProhibitedWriterDetailClass):
            raise ContractValidationError(
                "Writer realization boundary changed its closed prohibited classes"
            )
        if any(
            (
                self.presentation_enters_final_sequence,
                self.presentation_enters_events_or_material_changes,
                self.presentation_enters_memory_relationship_or_summary,
                self.presentation_enters_accepted_context_or_persistence,
                self.presentation_enters_canon,
            )
        ):
            raise ContractValidationError(
                "presentation-only realization cannot enter story authority"
            )
        if self.semantic_classifier != "independent_codex_semantic_validator":
            raise ContractValidationError(
                "Python cannot replace the semantic realization classifier"
            )

    @classmethod
    def default(cls) -> "WriterRealizationBoundaryV1":
        return cls(
            schema_version=cls.SCHEMA_VERSION,
            presentation_classes=tuple(PresentationRealizationClass),
            continuity_significant_classes=tuple(ProhibitedWriterDetailClass),
            presentation_enters_final_sequence=False,
            presentation_enters_events_or_material_changes=False,
            presentation_enters_memory_relationship_or_summary=False,
            presentation_enters_accepted_context_or_persistence=False,
            presentation_enters_canon=False,
            semantic_classifier="independent_codex_semantic_validator",
        )

    @property
    def boundary_sha256(self) -> str:
        return canonical_sha256(self)


@dataclass(frozen=True, slots=True)
class PresentationRealizationSegmentV1:
    """Exact visible span retained only as non-authoritative presentation."""

    SCHEMA_VERSION: ClassVar[str] = "cera.presentation_realization_segment.v1"

    schema_version: str
    segment_key: str
    presentation_class: PresentationRealizationClass
    kind: StoryRealizationKind
    output_start: int
    output_end: int
    exact_text: str
    exact_text_sha256: str
    roles: CharacterRoleLedgerV1

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError(
                "presentation realization segment schema changed"
            )
        _key(self.segment_key, "presentation_realization.segment_key")
        if (
            type(self.output_start) is not int
            or type(self.output_end) is not int
            or self.output_start < 0
            or self.output_end <= self.output_start
        ):
            raise ContractValidationError(
                "presentation realization segment span is invalid"
            )
        _text(
            self.exact_text,
            "presentation_realization.exact_text",
            maximum=64_000,
        )
        if (
            self.output_end - self.output_start != len(self.exact_text)
            or self.exact_text_sha256 != text_sha256(self.exact_text)
        ):
            raise ContractValidationError(
                "presentation realization exact-text custody changed"
            )
        if self.kind not in {
            StoryRealizationKind.ACTION,
            StoryRealizationKind.NARRATION,
        }:
            raise ContractValidationError(
                "dialogue, private state, and consent or decision cannot be "
                "presentation-only"
            )
        if "character:ted" in self.roles.assertion_owner_ids:
            raise ContractValidationError(
                "protected-user assertions cannot be presentation-only"
            )
        if self.kind is StoryRealizationKind.ACTION:
            valid = bool(self.roles.action_owner_ids) and not (
                self.roles.state_owner_ids or self.roles.speaker_ids
            )
        else:
            valid = not self.roles.assertion_owner_ids
        if not valid:
            raise ContractValidationError(
                "presentation realization kind disagrees with ownership roles"
            )


@dataclass(frozen=True, slots=True)
class WriterRecallOffendingSpanV1:
    """Exact rejected-candidate text carried only as non-authoritative feedback."""

    SCHEMA_VERSION: ClassVar[str] = "cera.writer_recall_offending_span.v1"

    schema_version: str
    segment_key: str
    output_start: int
    output_end: int
    exact_text: str
    exact_text_sha256: str
    prohibited_detail_classes: tuple[ProhibitedWriterDetailClass, ...]

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("Writer recall span schema changed")
        _key(self.segment_key, "writer_recall.segment_key")
        if (
            type(self.output_start) is not int
            or type(self.output_end) is not int
            or self.output_start < 0
            or self.output_end <= self.output_start
            or not isinstance(self.exact_text, str)
            or self.output_end - self.output_start != len(self.exact_text)
            or self.exact_text_sha256 != text_sha256(self.exact_text)
        ):
            raise ContractValidationError("Writer recall span custody changed")
        _text(self.exact_text, "writer_recall.exact_text", maximum=64_000)
        if not self.prohibited_detail_classes:
            raise ContractValidationError(
                "Writer recall span requires a prohibited-detail class"
            )
        _unique(
            self.prohibited_detail_classes,
            "writer_recall.prohibited_detail_classes",
        )


@dataclass(frozen=True, slots=True)
class WriterRecallDirectiveV1:
    """Bounded feedback for one fresh stateless Writer candidate."""

    SCHEMA_VERSION: ClassVar[str] = "cera.writer_recall_directive.v1"

    schema_version: str
    rejected_candidate_id: str
    rejected_story_text_sha256: str
    frozen_authority_package_sha256: str
    source_attempt_number: int
    next_attempt_number: int
    reason_codes: tuple[str, ...]
    offending_spans: tuple[WriterRecallOffendingSpanV1, ...]
    authoritative: bool
    attempts_may_merge: bool

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("Writer recall directive schema changed")
        _identity(self.rejected_candidate_id, "writer_recall.rejected_candidate_id")
        if not re_is_sha256(self.rejected_story_text_sha256) or not re_is_sha256(
            self.frozen_authority_package_sha256
        ):
            raise ContractValidationError("Writer recall hash custody is invalid")
        if (
            type(self.source_attempt_number) is not int
            or type(self.next_attempt_number) is not int
            or self.source_attempt_number not in {1, 2}
            or self.next_attempt_number != self.source_attempt_number + 1
            or self.next_attempt_number > 3
        ):
            raise ContractValidationError("Writer recall attempt custody changed")
        if not self.reason_codes or not self.offending_spans:
            raise ContractValidationError(
                "Writer recall requires typed reasons and exact offending spans"
            )
        for value in self.reason_codes:
            _key(value, "writer_recall.reason_codes")
        _unique(self.reason_codes, "writer_recall.reason_codes")
        if len({value.segment_key for value in self.offending_spans}) != len(
            self.offending_spans
        ):
            raise ContractValidationError("Writer recall offending spans are duplicated")
        if self.authoritative or self.attempts_may_merge:
            raise ContractValidationError(
                "Writer recall feedback is non-authoritative and cannot merge attempts"
            )

    @property
    def directive_sha256(self) -> str:
        return canonical_sha256(self)


@dataclass(frozen=True, slots=True)
class StoryRealizationSegmentV1:
    """Exhaustive Validator-owned semantics for one exact Writer span."""

    SCHEMA_VERSION: ClassVar[str] = "cera.story_realization_segment.v3"

    schema_version: str
    segment_key: str
    kind: StoryRealizationKind
    output_start: int
    output_end: int
    exact_text: str
    roles: CharacterRoleLedgerV1
    protected_user_source_claim_keys: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("story realization segment schema changed")
        _key(self.segment_key, "story_realization.segment_key")
        if (
            type(self.output_start) is not int
            or type(self.output_end) is not int
            or self.output_start < 0
            or self.output_end <= self.output_start
        ):
            raise ContractValidationError("story realization segment span is invalid")
        _text(self.exact_text, "story_realization.exact_text", maximum=64_000)
        if self.output_end - self.output_start != len(self.exact_text):
            raise ContractValidationError("story realization segment span length changed")
        if self.kind is StoryRealizationKind.ACTION:
            valid = bool(self.roles.action_owner_ids) and not (
                self.roles.state_owner_ids or self.roles.speaker_ids
            )
        elif self.kind is StoryRealizationKind.DIALOGUE:
            valid = len(self.roles.speaker_ids) == 1 and not (
                self.roles.action_owner_ids or self.roles.state_owner_ids
            )
        elif self.kind in {
            StoryRealizationKind.PRIVATE_STATE,
            StoryRealizationKind.CONSENT_OR_DECISION,
        }:
            valid = bool(self.roles.state_owner_ids) and not (
                self.roles.action_owner_ids or self.roles.speaker_ids
            )
        else:
            valid = not self.roles.assertion_owner_ids
        if not valid:
            raise ContractValidationError(
                "story realization kind disagrees with exact ownership roles"
            )
        for value in self.protected_user_source_claim_keys:
            _key(value, "story_realization.protected_user_source_claim_keys")
        _unique(
            self.protected_user_source_claim_keys,
            "story_realization.protected_user_source_claim_keys",
        )
        protected = "character:ted" in self.roles.assertion_owner_ids
        if protected != bool(self.protected_user_source_claim_keys):
            raise ContractValidationError(
                "protected-user realization requires exact supplied claim keys"
            )


class ProtectedSemanticRelationKind(StrEnum):
    NONE = "none"
    PROTECTED_ASSERTION = "protected_assertion"
    AFFECTED_BY_NPC = "affected_by_npc"
    ADDRESSED_BY_NPC = "addressed_by_npc"
    OBSERVED_BY_NPC = "observed_by_npc"
    REFERENCED_ONLY_BY_NPC = "referenced_only_by_npc"


@dataclass(frozen=True, slots=True)
class ProtectedSemanticAdjudicationV1:
    """Independent Validator judgment over one exact Writer span."""

    SCHEMA_VERSION: ClassVar[str] = "cera.protected_semantic_adjudication.v1"

    schema_version: str
    adjudication_key: str
    segment_key: str
    output_start: int
    output_end: int
    exact_text_sha256: str
    protected_user_id: str
    relation: ProtectedSemanticRelationKind
    npc_assertion_owner_ids: tuple[str, ...]
    protected_user_source_claim_keys: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError(
                "protected semantic adjudication schema changed"
            )
        _key(self.adjudication_key, "protected_semantic.adjudication_key")
        _key(self.segment_key, "protected_semantic.segment_key")
        if (
            type(self.output_start) is not int
            or type(self.output_end) is not int
            or self.output_start < 0
            or self.output_end <= self.output_start
        ):
            raise ContractValidationError(
                "protected semantic adjudication span is invalid"
            )
        if not re_is_sha256(self.exact_text_sha256):
            raise ContractValidationError(
                "protected semantic adjudication text hash is invalid"
            )
        _identity(self.protected_user_id, "protected_semantic.protected_user_id")
        for value in self.npc_assertion_owner_ids:
            _identity(value, "protected_semantic.npc_assertion_owner_ids")
            if value == self.protected_user_id:
                raise ContractValidationError(
                    "protected semantic NPC owner cannot be the protected user"
                )
        _unique(
            self.npc_assertion_owner_ids,
            "protected_semantic.npc_assertion_owner_ids",
        )
        for value in self.protected_user_source_claim_keys:
            _key(value, "protected_semantic.protected_user_source_claim_keys")
        _unique(
            self.protected_user_source_claim_keys,
            "protected_semantic.protected_user_source_claim_keys",
        )
        if self.relation is ProtectedSemanticRelationKind.PROTECTED_ASSERTION:
            if self.npc_assertion_owner_ids or len(
                self.protected_user_source_claim_keys
            ) != 1:
                raise ContractValidationError(
                    "protected assertion adjudication requires one exact claim"
                )
        elif self.relation is ProtectedSemanticRelationKind.NONE:
            if self.npc_assertion_owner_ids or self.protected_user_source_claim_keys:
                raise ContractValidationError(
                    "no-relation adjudication cannot carry owners or claims"
                )
        elif (
            not self.npc_assertion_owner_ids
            or self.protected_user_source_claim_keys
        ):
            raise ContractValidationError(
                "non-owning protected relation requires an exact NPC predicate owner"
            )


class DiagnosticGroundingStatus(StrEnum):
    """Grounding status for non-authoritative rejected-candidate evidence."""

    GROUNDED = "grounded"
    UNGROUNDED_PROTECTED_USER_ASSERTION = (
        "ungrounded_protected_user_assertion"
    )


class DiagnosticViolationClassification(StrEnum):
    """Closed violation class for rejected-only semantic adjudication."""

    NONE = "none"
    UNGROUNDED_PROTECTED_USER_ASSERTION = (
        "ungrounded_protected_user_assertion"
    )


@dataclass(frozen=True, slots=True)
class DiagnosticStorySegmentV1:
    """Exact Python-custodied span that can describe invalid Writer semantics.

    This contract is diagnostic only.  In particular, it can retain a Ted-owned
    assertion precisely because no ingress claim authorizes it.  It must never
    be converted into ``StoryRealizationSegmentV1``.
    """

    SCHEMA_VERSION: ClassVar[str] = "cera.diagnostic_story_segment.v1"

    schema_version: str
    segment_key: str
    kind: StoryRealizationKind
    output_start: int
    output_end: int
    exact_text: str
    exact_text_sha256: str
    roles: CharacterRoleLedgerV1
    grounding_status: DiagnosticGroundingStatus
    protected_user_source_claim_keys: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("diagnostic story segment schema changed")
        _key(self.segment_key, "diagnostic_story_segment.segment_key")
        if (
            type(self.output_start) is not int
            or type(self.output_end) is not int
            or self.output_start < 0
            or self.output_end <= self.output_start
        ):
            raise ContractValidationError("diagnostic story segment span is invalid")
        _text(self.exact_text, "diagnostic_story_segment.exact_text", maximum=64_000)
        if self.output_end - self.output_start != len(self.exact_text):
            raise ContractValidationError(
                "diagnostic story segment span length changed"
            )
        if self.exact_text_sha256 != text_sha256(self.exact_text):
            raise ContractValidationError(
                "diagnostic story segment exact-text hash changed"
            )
        if self.kind is StoryRealizationKind.ACTION:
            valid = bool(self.roles.action_owner_ids) and not (
                self.roles.state_owner_ids or self.roles.speaker_ids
            )
        elif self.kind is StoryRealizationKind.DIALOGUE:
            valid = len(self.roles.speaker_ids) == 1 and not (
                self.roles.action_owner_ids or self.roles.state_owner_ids
            )
        elif self.kind in {
            StoryRealizationKind.PRIVATE_STATE,
            StoryRealizationKind.CONSENT_OR_DECISION,
        }:
            valid = bool(self.roles.state_owner_ids) and not (
                self.roles.action_owner_ids or self.roles.speaker_ids
            )
        else:
            valid = not self.roles.assertion_owner_ids
        if not valid:
            raise ContractValidationError(
                "diagnostic story kind disagrees with exact ownership roles"
            )
        for value in self.protected_user_source_claim_keys:
            _key(value, "diagnostic_story_segment.protected_user_source_claim_keys")
        _unique(
            self.protected_user_source_claim_keys,
            "diagnostic_story_segment.protected_user_source_claim_keys",
        )
        protected = "character:ted" in self.roles.assertion_owner_ids
        if (
            self.grounding_status
            is DiagnosticGroundingStatus.UNGROUNDED_PROTECTED_USER_ASSERTION
        ):
            if not protected or self.protected_user_source_claim_keys:
                raise ContractValidationError(
                    "ungrounded protected diagnostic requires a Ted assertion with zero claims"
                )
        elif protected:
            if len(self.protected_user_source_claim_keys) != 1:
                raise ContractValidationError(
                    "grounded protected diagnostic requires one exact supplied claim"
                )
        elif self.protected_user_source_claim_keys:
            raise ContractValidationError(
                "non-protected diagnostic cannot carry protected-user claims"
            )


@dataclass(frozen=True, slots=True)
class DiagnosticProtectedSemanticAdjudicationV1:
    """Rejected-only protected semantic judgment with Python byte custody."""

    SCHEMA_VERSION: ClassVar[str] = (
        "cera.diagnostic_protected_semantic_adjudication.v1"
    )

    schema_version: str
    adjudication_key: str
    segment_key: str
    output_start: int
    output_end: int
    exact_text_sha256: str
    protected_user_id: str
    relation: ProtectedSemanticRelationKind
    grounding_status: DiagnosticGroundingStatus
    violation_classification: DiagnosticViolationClassification
    npc_assertion_owner_ids: tuple[str, ...]
    protected_user_source_claim_keys: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError(
                "diagnostic protected adjudication schema changed"
            )
        _key(self.adjudication_key, "diagnostic_protected.adjudication_key")
        _key(self.segment_key, "diagnostic_protected.segment_key")
        if (
            type(self.output_start) is not int
            or type(self.output_end) is not int
            or self.output_start < 0
            or self.output_end <= self.output_start
        ):
            raise ContractValidationError(
                "diagnostic protected adjudication span is invalid"
            )
        if not re_is_sha256(self.exact_text_sha256):
            raise ContractValidationError(
                "diagnostic protected adjudication text hash is invalid"
            )
        _identity(self.protected_user_id, "diagnostic_protected.protected_user_id")
        for value in self.npc_assertion_owner_ids:
            _identity(value, "diagnostic_protected.npc_assertion_owner_ids")
            if value == self.protected_user_id:
                raise ContractValidationError(
                    "diagnostic protected NPC owner cannot be the protected user"
                )
        _unique(
            self.npc_assertion_owner_ids,
            "diagnostic_protected.npc_assertion_owner_ids",
        )
        for value in self.protected_user_source_claim_keys:
            _key(value, "diagnostic_protected.protected_user_source_claim_keys")
        _unique(
            self.protected_user_source_claim_keys,
            "diagnostic_protected.protected_user_source_claim_keys",
        )
        ungrounded = (
            self.grounding_status
            is DiagnosticGroundingStatus.UNGROUNDED_PROTECTED_USER_ASSERTION
        )
        violation = (
            self.violation_classification
            is DiagnosticViolationClassification.UNGROUNDED_PROTECTED_USER_ASSERTION
        )
        if ungrounded != violation:
            raise ContractValidationError(
                "diagnostic grounding and violation classification disagree"
            )
        if ungrounded:
            if (
                self.relation is not ProtectedSemanticRelationKind.PROTECTED_ASSERTION
                or self.npc_assertion_owner_ids
                or self.protected_user_source_claim_keys
            ):
                raise ContractValidationError(
                    "ungrounded protected adjudication requires an unclaimed protected assertion"
                )
        elif self.relation is ProtectedSemanticRelationKind.PROTECTED_ASSERTION:
            if self.npc_assertion_owner_ids or len(
                self.protected_user_source_claim_keys
            ) != 1:
                raise ContractValidationError(
                    "grounded protected adjudication requires one exact claim"
                )
        elif self.relation is ProtectedSemanticRelationKind.NONE:
            if self.npc_assertion_owner_ids or self.protected_user_source_claim_keys:
                raise ContractValidationError(
                    "diagnostic no-relation adjudication cannot carry owners or claims"
                )
        elif (
            not self.npc_assertion_owner_ids
            or self.protected_user_source_claim_keys
        ):
            raise ContractValidationError(
                "diagnostic non-owning relation requires an exact NPC predicate owner"
            )


class ReaderVerdictStatus(StrEnum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    INCONCLUSIVE = "inconclusive"


@dataclass(frozen=True, slots=True)
class ReaderIssueReferenceV1:
    """One non-rewriting Reader issue bound to exact immutable Writer text."""

    SCHEMA_VERSION: ClassVar[str] = "cera.reader_issue_reference.v1"

    schema_version: str
    issue_code: str
    output_start: int
    output_end: int
    exact_text_sha256: str
    explanation: str

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("Reader issue reference schema changed")
        _key(self.issue_code, "reader_issue.issue_code")
        if (
            type(self.output_start) is not int
            or type(self.output_end) is not int
            or self.output_start < 0
            or self.output_end <= self.output_start
        ):
            raise ContractValidationError("Reader issue span is invalid")
        if not re_is_sha256(self.exact_text_sha256):
            raise ContractValidationError("Reader issue text hash is invalid")
        _text(self.explanation, "reader_issue.explanation", maximum=2_000)


@dataclass(frozen=True, slots=True)
class ReaderVerdictV1:
    """Whole-response quality judgment with no replacement-prose channel."""

    SCHEMA_VERSION: ClassVar[str] = "cera.reader_verdict.v1"

    schema_version: str
    verdict_id: str
    world_id: str
    branch_id: str
    turn_id: str
    candidate_id: str
    story_text_sha256: str
    verdict: ReaderVerdictStatus
    reason_codes: tuple[str, ...]
    issues: tuple[ReaderIssueReferenceV1, ...]
    scene_completeness_score: int
    character_voice_score: int
    dialogue_pacing_score: int
    readability_score: int

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("Reader verdict schema changed")
        for field in (
            "verdict_id",
            "world_id",
            "branch_id",
            "turn_id",
            "candidate_id",
        ):
            _identity(getattr(self, field), f"reader_verdict.{field}")
        if not re_is_sha256(self.story_text_sha256):
            raise ContractValidationError("Reader verdict text hash is invalid")
        for value in self.reason_codes:
            _key(value, "reader_verdict.reason_codes")
        _unique(self.reason_codes, "reader_verdict.reason_codes")
        _unique(
            tuple(
                (value.issue_code, value.output_start, value.output_end)
                for value in self.issues
            ),
            "reader_verdict.issues",
        )
        for field in (
            "scene_completeness_score",
            "character_voice_score",
            "dialogue_pacing_score",
            "readability_score",
        ):
            value = getattr(self, field)
            if type(value) is not int or not 0 <= value <= 100:
                raise ContractValidationError(f"Reader {field} is out of range")
        if self.verdict is ReaderVerdictStatus.ACCEPTED:
            if self.reason_codes or self.issues:
                raise ContractValidationError(
                    "accepted Reader verdict cannot carry rejection issues"
                )
        elif not self.reason_codes or not self.issues:
            raise ContractValidationError(
                "non-accepted Reader verdict requires reasons and exact issues"
            )

    def validate_story_text(self, story_text: str) -> None:
        if text_sha256(story_text) != self.story_text_sha256:
            raise ContractValidationError("Reader verdict changed Writer bytes")
        for issue in self.issues:
            if issue.output_end > len(story_text) or text_sha256(
                story_text[issue.output_start : issue.output_end]
            ) != issue.exact_text_sha256:
                raise ContractValidationError(
                    "Reader issue reference changed exact Writer bytes"
                )


@dataclass(frozen=True, slots=True)
class AcceptedTurnPairV1:
    accepted_turn_id: str
    user_message: str
    complete_final_sequence: FinalSequenceV1

    def __post_init__(self) -> None:
        _identity(self.accepted_turn_id, "accepted_turn_id")
        _text(self.user_message, "user_message", maximum=64_000)
        if self.complete_final_sequence.accepted_turn_id != self.accepted_turn_id:
            raise ContractValidationError("accepted pair sequence changed turn identity")


@dataclass(frozen=True, slots=True)
class SceneSummaryV1:
    SCHEMA_VERSION: ClassVar[str] = "cera.scene_summary.v1"

    schema_version: str
    summary_id: str
    completed_scene_id: str
    accepted_turn_ids: tuple[str, ...]
    shortest_complete_summary: str
    last_five_exact_pairs: tuple[AcceptedTurnPairV1, ...]
    ending_state: str
    transition_context: str

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("scene summary schema changed")
        _identity(self.summary_id, "summary_id")
        _identity(self.completed_scene_id, "completed_scene_id")
        if not self.accepted_turn_ids:
            raise ContractValidationError("scene summary requires accepted turns")
        for value in self.accepted_turn_ids:
            _identity(value, "accepted_turn_ids")
        _unique(self.accepted_turn_ids, "accepted_turn_ids")
        expected_tail = self.accepted_turn_ids[-5:]
        actual_tail = tuple(value.accepted_turn_id for value in self.last_five_exact_pairs)
        if actual_tail != expected_tail:
            raise ContractValidationError("scene summary exact-pair tail is incomplete or out of order")
        for field in ("shortest_complete_summary", "ending_state", "transition_context"):
            _text(getattr(self, field), field, maximum=32_000)


@dataclass(frozen=True, slots=True)
class SceneSummaryTurnProvenanceV2:
    accepted_turn_id: str
    accepted_pair_sha256: str
    accepted_event_sha256: tuple[str, ...]
    authority_basis: str

    def __post_init__(self) -> None:
        _identity(self.accepted_turn_id, "scene_summary_provenance.accepted_turn_id")
        if not re_is_sha256(self.accepted_pair_sha256):
            raise ContractValidationError("scene summary pair provenance hash is invalid")
        if any(not re_is_sha256(value) for value in self.accepted_event_sha256):
            raise ContractValidationError("scene summary event provenance hash is invalid")
        _unique(self.accepted_event_sha256, "scene summary event provenance")
        if self.authority_basis != "exact_accepted_pair":
            raise ContractValidationError(
                "scene summary authority must remain the exact accepted pair"
            )


@dataclass(frozen=True, slots=True)
class SceneSummaryDerivedViewV1:
    """Regenerable, explicitly non-authoritative view over accepted facts."""

    SCHEMA_VERSION: ClassVar[str] = "cera.scene_summary_derived_view.v2"

    schema_version: str
    authority_classification: str
    summary_revision: int
    regeneration_identity_sha256: str
    source_accepted_turn_ids: tuple[str, ...]
    source_turn_provenance: tuple[SceneSummaryTurnProvenanceV2, ...]
    summary: SceneSummaryV1

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("scene summary derived-view schema changed")
        if self.authority_classification != "non_authoritative_derived_view":
            raise ContractValidationError("scene summary authority classification changed")
        if type(self.summary_revision) is not int or self.summary_revision < 1:
            raise ContractValidationError("scene summary derived revision is invalid")
        if not re_is_sha256(self.regeneration_identity_sha256):
            raise ContractValidationError("scene summary regeneration identity is invalid")
        if self.source_accepted_turn_ids != self.summary.accepted_turn_ids:
            raise ContractValidationError("scene summary source allow-list changed")
        if tuple(value.accepted_turn_id for value in self.source_turn_provenance) != (
            self.source_accepted_turn_ids
        ):
            raise ContractValidationError(
                "scene summary per-turn provenance is incomplete or reordered"
            )


@dataclass(frozen=True, slots=True)
class ValidatorFinalizationPackageV1:
    SCHEMA_VERSION: ClassVar[str] = "cera.validator_finalization_package.v8"
    MAX_EDIT_OPERATIONS: ClassVar[int] = 100

    schema_version: str
    package_id: str
    world_id: str
    branch_id: str
    task_mode: ValidatorTaskMode
    semantic_status: ValidatorSemanticStatus
    complete_final_sequence: FinalSequenceV1 | None
    creator_review: CreatorReviewAssessment | None
    world_edit_operations: tuple[WorldEditOperationV1, ...]
    created_field_log: tuple[CreatedFieldLogEntryV1, ...]
    event_record: EventRecordCandidateV1 | None
    optional_scene_summary: SceneSummaryV1 | None
    protected_semantic_adjudications: tuple[
        ProtectedSemanticAdjudicationV1, ...
    ] = ()

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("Validator package schema changed")
        for field in ("package_id", "world_id", "branch_id"):
            _identity(getattr(self, field), field)
        if len(self.world_edit_operations) > self.MAX_EDIT_OPERATIONS:
            raise ContractValidationError("Validator edit operation ceiling exceeded")
        _unique(tuple(value.operation_key for value in self.world_edit_operations), "edit operation keys")
        if self.task_mode is ValidatorTaskMode.FINALIZE_TURN:
            if self.complete_final_sequence is None or self.creator_review is None or self.event_record is None:
                raise ContractValidationError("turn finalization package is incomplete")
            if not self.protected_semantic_adjudications:
                raise ContractValidationError(
                    "turn finalization lacks independent protected semantics"
                )
            if any(
                not isinstance(value, ProtectedSemanticAdjudicationV1)
                for value in self.protected_semantic_adjudications
            ):
                raise ContractValidationError(
                    "turn finalization accepts only canonical protected adjudications"
                )
            _unique(
                tuple(
                    value.adjudication_key
                    for value in self.protected_semantic_adjudications
                ),
                "protected semantic adjudication keys",
            )
            _unique(
                tuple(
                    value.segment_key
                    for value in self.protected_semantic_adjudications
                ),
                "protected semantic adjudication segments",
            )
            if self.optional_scene_summary is not None:
                raise ContractValidationError("turn finalization cannot create a scene summary")
            from cera.creator_review.models import CreatorReviewSeverity

            expected_semantics = {
                CreatorReviewSeverity.GOOD: ValidatorSemanticStatus.ACCEPTED,
                CreatorReviewSeverity.CONCERN: ValidatorSemanticStatus.CONCERN,
                # The semantic enum intentionally has no second Critical value.
                # A Critical assessment is a verifier-owned severity over a
                # semantically concerning candidate and may still be creator-
                # accepted as False Positive when publication eligibility allows.
                CreatorReviewSeverity.CRITICAL: ValidatorSemanticStatus.CONCERN,
            }
            expected = expected_semantics.get(self.creator_review.severity)
            if expected is not None and self.semantic_status is not expected:
                raise ContractValidationError(
                    "Validator semantic status and creator-review severity disagree"
                )
            participant_union = {
                identity
                for item in self.complete_final_sequence.items
                for identity in item.roles.involved_ids
            }
            if set(self.event_record.participant_ids) != participant_union:
                raise ContractValidationError(
                    "event participants do not equal justified final character roles"
                )
            final_roles = {
                value.item_key: value.roles
                for value in self.complete_final_sequence.items
            }
            if any(
                final_roles.get(value.final_sequence_item_key) != value.roles
                for value in self.event_record.item_role_ledgers
            ):
                raise ContractValidationError(
                    "event role custody changed final sequence roles"
                )
        else:
            if self.optional_scene_summary is None:
                raise ContractValidationError("scene-summary task requires a summary")
            if any((self.complete_final_sequence, self.creator_review, self.event_record)):
                raise ContractValidationError("scene-summary task cannot finalize a turn")
            if self.world_edit_operations or self.created_field_log:
                raise ContractValidationError("scene-summary task cannot edit world state")
            if self.protected_semantic_adjudications:
                raise ContractValidationError(
                    "scene-summary task cannot adjudicate Composer segments"
                )
        directives = {
            directive.directive_key: (item, scope, directive)
            for item in (
                self.complete_final_sequence.items
                if self.complete_final_sequence is not None
                else ()
            )
            for scope in item.field_scopes
            for directive in scope.persistence_directives
        }
        declared_directive_count = sum(
            len(scope.persistence_directives)
            for item in (
                self.complete_final_sequence.items
                if self.complete_final_sequence is not None
                else ()
            )
            for scope in item.field_scopes
        )
        if len(directives) != declared_directive_count:
            raise ContractValidationError("persistence directive keys are duplicated")
        if len(self.world_edit_operations) != len(directives):
            raise ContractValidationError(
                "Python-derived edits do not equal the persistable final fields"
            )
        for operation in self.world_edit_operations:
            if operation.operation not in {
                WorldEditOperationKind.ADD,
                WorldEditOperationKind.REPLACE,
            }:
                raise ContractValidationError(
                    "continuous finalization cannot use an untyped persistence transform"
                )
            if operation.persistence_directive_key not in directives:
                raise ContractValidationError(
                    "world edit lacks its field-level persistence directive"
                )
            item, scope, directive = directives[operation.persistence_directive_key]
            raw_values = getattr(item, scope.field_name)
            source_values = raw_values if isinstance(raw_values, tuple) else (raw_values,)
            if directive.source_value_index >= len(source_values):
                raise ContractValidationError(
                    "persistence directive selected a missing final-field value"
                )
            expected_value = source_values[directive.source_value_index]
            if (
                operation.target_file != directive.target_file
                or operation.expected_file_revision
                != directive.expected_file_revision
                or operation.operation is not directive.operation
                or operation.field_path != directive.field_path
                or operation.value != expected_value
                or operation.source_final_sequence_item != item.item_key
                or operation.source_final_field_name != scope.field_name
                or operation.protected_user_source_claim_keys
                != scope.protected_user_source_claim_keys
                or operation.reason
                != f"Persist accepted final field {scope.field_name}."
            ):
                raise ContractValidationError(
                    "world edit changed its field-level persistence directive"
                )
        created_pairs = {(value.target_file, value.field_path) for value in self.created_field_log}
        for operation in self.world_edit_operations:
            if operation.operation in {
                WorldEditOperationKind.ADD,
                WorldEditOperationKind.CREATE_FILE,
            } and (
                operation.target_file,
                operation.field_path,
            ) not in created_pairs:
                raise ContractValidationError("new semantic field is missing from created_field_log")
        for entry in self.created_field_log:
            matches = tuple(
                value
                for value in self.world_edit_operations
                if value.operation in {
                    WorldEditOperationKind.ADD,
                    WorldEditOperationKind.CREATE_FILE,
                }
                and value.target_file == entry.target_file
                and value.field_path == entry.field_path
            )
            if len(matches) != 1:
                raise ContractValidationError("created_field_log has no unique add operation")
            operation = matches[0]
            if operation.value != entry.value or json_value_type(entry.value) != entry.value_type:
                raise ContractValidationError("created_field_log changed the proposed value")
            if (
                operation.reason != entry.reason
                or operation.source_final_sequence_item
                != entry.source_final_sequence_item
                or operation.source_final_field_name
                != entry.source_final_field_name
                or operation.protected_user_source_claim_keys
                != entry.protected_user_source_claim_keys
                or operation.persistence_directive_key
                != entry.persistence_directive_key
            ):
                raise ContractValidationError("created_field_log changed edit provenance")
        if self.task_mode is ValidatorTaskMode.FINALIZE_TURN:
            assert self.complete_final_sequence is not None
            assert self.event_record is not None
            if self.event_record.accepted_turn_id != self.complete_final_sequence.accepted_turn_id:
                raise ContractValidationError("event record changed accepted-turn identity")
            item_keys = {value.item_key for value in self.complete_final_sequence.items}
            if set(self.event_record.final_sequence_item_keys) != item_keys:
                raise ContractValidationError("event record does not cover the complete final sequence")
            item_claim_keys = {
                claim_key
                for item in self.complete_final_sequence.items
                for claim_key in item.protected_user_source_claim_keys
            }
            if set(self.event_record.protected_user_source_claim_keys) != item_claim_keys:
                raise ContractValidationError(
                    "event record changed protected-user claim provenance"
                )
            for operation in self.world_edit_operations:
                if operation.source_final_sequence_item not in item_keys:
                    raise ContractValidationError("world edit cites an unknown final-sequence item")

    @property
    def package_sha256(self) -> str:
        return domain_sha256(self.SCHEMA_VERSION, self)

    def permits_disposable_acceptance(self, action: CreatorReviewAction) -> bool:
        from cera.creator_review.models import (
            CreatorReviewSeverity,
            PublicationEligibility,
        )

        if (
            self.task_mode is not ValidatorTaskMode.FINALIZE_TURN
            or self.creator_review is None
            or self.creator_review.publication_eligibility
            is not PublicationEligibility.ACCEPT_ALLOWED
        ):
            return False
        if action is CreatorReviewAction.ACCEPT:
            return (
                self.semantic_status is ValidatorSemanticStatus.ACCEPTED
                and self.creator_review.severity is CreatorReviewSeverity.GOOD
            )
        if action is CreatorReviewAction.FALSE_POSITIVE:
            return (
                self.creator_review.severity
                in {CreatorReviewSeverity.CONCERN, CreatorReviewSeverity.CRITICAL}
            )
        return False


@dataclass(frozen=True, slots=True)
class ValidatorRouteV1:
    planner_model: str
    planner_effort: str
    validator_model: str
    validator_effort: str
    fast_enabled: bool = False

    def __post_init__(self) -> None:
        for field in ("planner_model", "planner_effort", "validator_model", "validator_effort"):
            _text(getattr(self, field), field, maximum=96)
        if self.fast_enabled:
            raise ContractValidationError("continuous Validator Fast mode is prohibited")
        ceiling = {"gpt-5.6-terra": 1, "gpt-5.6-sol": 2}
        if ceiling.get(self.validator_model, 99) > ceiling["gpt-5.6-sol"]:
            raise ContractValidationError("Validator exceeds the Sol-medium quality ceiling")
        if self.validator_model == "gpt-5.6-sol" and self.validator_effort != "medium":
            raise ContractValidationError("Sol Validator is fixed to medium")


def validator_route_for(planner_model: str, planner_effort: str) -> ValidatorRouteV1:
    mapping = {
        ("gpt-5.6-sol", "xhigh"): ("gpt-5.6-sol", "medium"),
        ("gpt-5.6-sol", "medium"): ("gpt-5.6-terra", "high"),
    }
    try:
        model, effort = mapping[(planner_model, planner_effort)]
    except KeyError as exc:
        raise ContractValidationError(
            "unsupported continuous Planner-to-Validator route mapping"
        ) from exc
    return ValidatorRouteV1(
        planner_model=planner_model,
        planner_effort=planner_effort,
        validator_model=model,
        validator_effort=effort,
        fast_enabled=False,
    )


@dataclass(frozen=True, slots=True)
class PromptComponentUsageV1:
    component: str
    byte_count: int
    estimated_tokens: int

    def __post_init__(self) -> None:
        _key(self.component, "prompt component")
        if min(self.byte_count, self.estimated_tokens) < 0:
            raise ContractValidationError("prompt component usage cannot be negative")


def json_value_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    raise ContractValidationError("world edit value is not JSON-compatible")


def package_identity_payload(package: ValidatorFinalizationPackageV1) -> Mapping[str, Any]:
    return {
        "package_sha256": package.package_sha256,
        "world_id": package.world_id,
        "branch_id": package.branch_id,
        "task_mode": package.task_mode.value,
        "semantic_status": package.semantic_status.value,
        "operation_count": len(package.world_edit_operations),
        "payload_sha256": canonical_sha256(package),
    }
