"""Provider-neutral semantic contracts for CERA character cognition.

The objects in this module describe observable, testable decision rationale.
They intentionally do not contain world paths, branch hashes, provider
receipts, transaction identifiers, or hidden chain-of-thought.  Python-owned
custody lives in :mod:`cera.cognition.custody`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar

from cera.errors import ContractValidationError
from cera.sequence_first.contracts import (
    SequenceDraftV1,
    SequenceFirstTurnSemanticInputV1,
)
from cera.serialization import canonical_sha256

_LOCAL_KEY = re.compile(r"[a-z][a-z0-9_]{0,95}\Z")
_IDENTITY = re.compile(r"[a-z][a-z0-9_.:-]{0,191}\Z")


def _text(value: str, field: str, *, maximum: int = 2_000) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ContractValidationError(f"{field} must be non-empty and bounded")


def _optional_text(value: str | None, field: str, *, maximum: int = 2_000) -> None:
    if value is not None:
        _text(value, field, maximum=maximum)


def _local_key(value: str, field: str) -> None:
    if not isinstance(value, str) or _LOCAL_KEY.fullmatch(value) is None:
        raise ContractValidationError(f"{field} must be a local key")


def _identity(value: str, field: str) -> None:
    if not isinstance(value, str) or _IDENTITY.fullmatch(value) is None:
        raise ContractValidationError(f"{field} must be a stable identity")


def _character_id(value: str, field: str) -> None:
    _identity(value, field)
    if not value.startswith("character:"):
        raise ContractValidationError(f"{field} must be a character identity")


def _unique(values: tuple[str, ...], field: str) -> None:
    if len(values) != len(set(values)):
        raise ContractValidationError(f"{field} contains duplicates")


class PressureLevel(StrEnum):
    NONE = "none"
    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"
    OVERWHELMING = "overwhelming"


class KnowledgeCertainty(StrEnum):
    UNKNOWN = "unknown"
    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"
    ESTABLISHED = "established"


class CharacterAutonomyMode(StrEnum):
    OFF = "off"
    MIND = "mind"
    BODY = "body"
    BOTH = "both"

    @property
    def mind_precedence(self) -> bool:
        return self in {self.MIND, self.BOTH}

    @property
    def body_precedence(self) -> bool:
        return self in {self.BODY, self.BOTH}


class LogicRoute(StrEnum):
    ORDINARY = "ordinary"
    ADULT = "adult"


class UserDirectionDisposition(StrEnum):
    SOURCE_DIRECTION = "source_direction"
    PROPOSED_OUTCOME = "proposed_outcome"
    PARTIALLY_REALIZED = "partially_realized"
    OVERRIDDEN = "overridden"


class ProvisionalTruthValue(StrEnum):
    TRUE = "true"
    FALSE = "false"


@dataclass(frozen=True, slots=True)
class PerceivedFactV1:
    source_ref: str
    concise_perception: str
    certainty: KnowledgeCertainty

    def __post_init__(self) -> None:
        _identity(self.source_ref, "perceived_fact.source_ref")
        _text(
            self.concise_perception,
            "perceived_fact.concise_perception",
            maximum=1_000,
        )


@dataclass(frozen=True, slots=True)
class ObserverFrameV1:
    directly_perceived: tuple[PerceivedFactV1, ...]
    inferred_meanings: tuple[str, ...]
    unavailable_or_ambiguous: tuple[str, ...]
    draft_local_predecessor_item_keys: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        fact_keys = tuple(
            (
                value.source_ref,
                value.concise_perception,
                value.certainty.value,
            )
            for value in self.directly_perceived
        )
        if len(fact_keys) != len(set(fact_keys)):
            raise ContractValidationError(
                "observer_frame.directly_perceived contains duplicate facts"
            )
        for field in ("inferred_meanings", "unavailable_or_ambiguous"):
            values = getattr(self, field)
            _unique(values, f"observer_frame.{field}")
            for value in values:
                _text(value, f"observer_frame.{field}", maximum=1_000)
        _unique(
            self.draft_local_predecessor_item_keys,
            "observer_frame.draft_local_predecessor_item_keys",
        )
        for value in self.draft_local_predecessor_item_keys:
            _local_key(value, "observer_frame.draft_local_predecessor_item_keys")


@dataclass(frozen=True, slots=True)
class MaterialPressureV1:
    kind: str
    level: PressureLevel
    direction: str
    evidence_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        _local_key(self.kind, "material_pressure.kind")
        _local_key(self.direction, "material_pressure.direction")
        _unique(self.evidence_refs, "material_pressure.evidence_refs")
        for value in self.evidence_refs:
            _identity(value, "material_pressure.evidence_refs")


@dataclass(frozen=True, slots=True)
class ResponseLayersV1:
    immediate_involuntary_reaction: str | None
    conscious_interpretation: str
    subconscious_pressure: str | None
    considered_judgment: str

    def __post_init__(self) -> None:
        _optional_text(
            self.immediate_involuntary_reaction,
            "response_layers.immediate_involuntary_reaction",
            maximum=1_000,
        )
        _text(
            self.conscious_interpretation,
            "response_layers.conscious_interpretation",
            maximum=1_000,
        )
        _optional_text(
            self.subconscious_pressure,
            "response_layers.subconscious_pressure",
            maximum=1_000,
        )
        _text(
            self.considered_judgment,
            "response_layers.considered_judgment",
            maximum=1_000,
        )


@dataclass(frozen=True, slots=True)
class AutonomyApplicationV1:
    mind_precedence_applied: bool
    body_precedence_applied: bool
    user_direction_disposition: UserDirectionDisposition
    overwhelming_pressure_kind: str | None
    concise_effect: str

    def __post_init__(self) -> None:
        if type(self.mind_precedence_applied) is not bool:
            raise ContractValidationError(
                "autonomy_application.mind_precedence_applied must be boolean"
            )
        if type(self.body_precedence_applied) is not bool:
            raise ContractValidationError(
                "autonomy_application.body_precedence_applied must be boolean"
            )
        if self.overwhelming_pressure_kind is not None:
            _local_key(
                self.overwhelming_pressure_kind,
                "autonomy_application.overwhelming_pressure_kind",
            )
        _text(
            self.concise_effect,
            "autonomy_application.concise_effect",
            maximum=1_000,
        )


@dataclass(frozen=True, slots=True)
class CloseAlternativeV1:
    intent: str
    why_not_selected: str
    remains_realistically_available: bool

    def __post_init__(self) -> None:
        _text(self.intent, "close_alternative.intent", maximum=1_000)
        _text(
            self.why_not_selected,
            "close_alternative.why_not_selected",
            maximum=1_000,
        )
        if type(self.remains_realistically_available) is not bool:
            raise ContractValidationError(
                "close_alternative.remains_realistically_available must be boolean"
            )


@dataclass(frozen=True, slots=True)
class DecisionRecordV1:
    """Compact material rationale, not hidden chain-of-thought."""

    decision_key: str
    owner_id: str
    causal_trigger_refs: tuple[str, ...]
    observer_frame: ObserverFrameV1
    perceived_event_meaning: str
    knowledge_certainty: KnowledgeCertainty
    personal_and_social_meaning: str
    response_layers: ResponseLayersV1
    selected_intent: str
    concise_decision_basis: str
    decisive_factor_refs: tuple[str, ...]
    material_pressures: tuple[MaterialPressureV1, ...]
    autonomy_application: AutonomyApplicationV1
    anticipated_immediate_effect: str
    close_alternative: CloseAlternativeV1 | None
    uncertainty: KnowledgeCertainty

    def __post_init__(self) -> None:
        _local_key(self.decision_key, "decision_record.decision_key")
        _character_id(self.owner_id, "decision_record.owner_id")
        if self.owner_id == "character:ted":
            raise ContractValidationError(
                "CERA cannot author a material decision for the protected user"
            )
        for field in ("causal_trigger_refs", "decisive_factor_refs"):
            values = getattr(self, field)
            if not values:
                raise ContractValidationError(f"decision_record.{field} is empty")
            _unique(values, f"decision_record.{field}")
            for value in values:
                _identity(value, f"decision_record.{field}")
        for field in (
            "perceived_event_meaning",
            "personal_and_social_meaning",
            "selected_intent",
            "concise_decision_basis",
            "anticipated_immediate_effect",
        ):
            _text(getattr(self, field), f"decision_record.{field}")
        pressure_kinds = tuple(value.kind for value in self.material_pressures)
        _unique(pressure_kinds, "decision_record.material_pressures")
        overwhelming_kinds = {
            value.kind
            for value in self.material_pressures
            if value.level is PressureLevel.OVERWHELMING
        }
        named_override = self.autonomy_application.overwhelming_pressure_kind
        if named_override is not None and named_override not in overwhelming_kinds:
            raise ContractValidationError(
                "autonomy override must cite an overwhelming material pressure"
            )


@dataclass(frozen=True, slots=True)
class DecisionItemLinkV1:
    item_key: str
    decision_key: str

    def __post_init__(self) -> None:
        _local_key(self.item_key, "decision_item_link.item_key")
        _local_key(self.decision_key, "decision_item_link.decision_key")


@dataclass(frozen=True, slots=True)
class ProvisionalDependencyV1:
    provisional_record_id: str
    assumed_value: ProvisionalTruthValue
    concise_dependency: str

    def __post_init__(self) -> None:
        _identity(
            self.provisional_record_id,
            "provisional_dependency.provisional_record_id",
        )
        _text(
            self.concise_dependency,
            "provisional_dependency.concise_dependency",
            maximum=1_000,
        )


@dataclass(frozen=True, slots=True)
class RouteTransitionProposalV1:
    from_route: LogicRoute
    to_route: LogicRoute
    boundary_item_key: str
    non_graphic_handoff_summary: str
    character_effect_refs: tuple[str, ...]
    return_condition: str

    def __post_init__(self) -> None:
        if self.from_route is self.to_route:
            raise ContractValidationError("route transition must change logic owner")
        _local_key(self.boundary_item_key, "route_transition.boundary_item_key")
        _text(
            self.non_graphic_handoff_summary,
            "route_transition.non_graphic_handoff_summary",
            maximum=2_000,
        )
        _unique(self.character_effect_refs, "route_transition.character_effect_refs")
        for value in self.character_effect_refs:
            _identity(value, "route_transition.character_effect_refs")
        _text(
            self.return_condition,
            "route_transition.return_condition",
            maximum=1_000,
        )


@dataclass(frozen=True, slots=True)
class CognitionTurnContextV1:
    """Complete semantic input for one logic-owner operation."""

    SCHEMA_VERSION: ClassVar[str] = "cera.cognition.turn_context.v1"

    turn: SequenceFirstTurnSemanticInputV1
    autonomy_mode: CharacterAutonomyMode
    logic_route: LogicRoute
    available_provisional_record_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _unique(
            self.available_provisional_record_ids,
            "cognition_turn_context.available_provisional_record_ids",
        )
        for value in self.available_provisional_record_ids:
            _identity(
                value,
                "cognition_turn_context.available_provisional_record_ids",
            )


@dataclass(frozen=True, slots=True)
class CognitionPlanV1:
    """One provider-authored cognition result wrapping legacy sequence semantics."""

    SCHEMA_VERSION: ClassVar[str] = "cera.cognition.plan.v1"

    sequence: SequenceDraftV1
    decision_records: tuple[DecisionRecordV1, ...]
    decision_item_links: tuple[DecisionItemLinkV1, ...]
    provisional_dependencies: tuple[ProvisionalDependencyV1, ...]
    route_transition: RouteTransitionProposalV1 | None = None

    def __post_init__(self) -> None:
        decision_keys = tuple(value.decision_key for value in self.decision_records)
        _unique(decision_keys, "cognition_plan.decision_records")
        item_keys = tuple(value.item_key for value in self.decision_item_links)
        _unique(item_keys, "cognition_plan.decision_item_links")
        provisional_ids = tuple(
            value.provisional_record_id for value in self.provisional_dependencies
        )
        _unique(provisional_ids, "cognition_plan.provisional_dependencies")
        known_decisions = set(decision_keys)
        known_items = {value.item_key for value in self.sequence.items}
        for value in self.decision_item_links:
            if value.decision_key not in known_decisions:
                raise ContractValidationError("decision item link cites an unknown decision")
            if value.item_key not in known_items:
                raise ContractValidationError("decision item link cites an unknown sequence item")
        if self.route_transition is not None:
            if self.route_transition.boundary_item_key not in known_items:
                raise ContractValidationError("route transition cites an unknown sequence item")

    @property
    def semantic_sha256(self) -> str:
        return canonical_sha256(self)
