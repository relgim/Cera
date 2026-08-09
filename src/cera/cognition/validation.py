"""Deterministic cross-link validation for CERA cognition plans."""

from __future__ import annotations

from dataclasses import dataclass

from cera.errors import ContractValidationError
from cera.sequence_first.contracts import (
    PROTECTED_USER_ID,
    ItemKind,
    SequenceFirstTurnSemanticInputV1,
)

from .contracts import (
    AutonomyApplicationV1,
    CharacterAutonomyMode,
    CognitionPlanV1,
    LogicRoute,
)

_MATERIAL_RESPONSE_KINDS = frozenset(
    {
        ItemKind.ACTION,
        ItemKind.DIALOGUE_INTENT,
        ItemKind.REMOTE_COMMUNICATION,
    }
)


@dataclass(frozen=True, slots=True)
class CognitionValidationContextV1:
    autonomy_mode: CharacterAutonomyMode
    logic_route: LogicRoute
    available_evidence_refs: tuple[str, ...]
    available_provisional_record_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if len(self.available_evidence_refs) != len(set(self.available_evidence_refs)):
            raise ContractValidationError("cognition validation evidence refs contain duplicates")
        if len(self.available_provisional_record_ids) != len(
            set(self.available_provisional_record_ids)
        ):
            raise ContractValidationError(
                "cognition validation provisional refs contain duplicates"
            )


def validate_cognition_plan(
    plan: CognitionPlanV1,
    *,
    turn: SequenceFirstTurnSemanticInputV1,
    context: CognitionValidationContextV1,
) -> None:
    """Validate a plan without interpreting its prose-like semantic fields."""

    turn.validate_intended(plan.sequence)
    decisions = {value.decision_key: value for value in plan.decision_records}
    items = {value.item_key: value for value in plan.sequence.items}
    links = {value.item_key: value.decision_key for value in plan.decision_item_links}

    available_refs = set(context.available_evidence_refs)
    available_refs.add(turn.current_source_key)
    available_refs.update(value.evidence_key for value in turn.evidence_records)
    available_refs.update(item.item_key for item in plan.sequence.items)

    for decision in plan.decision_records:
        if decision.owner_id not in turn.known_character_ids:
            raise ContractValidationError("decision owner is not a known character")
        refs = {
            *decision.causal_trigger_refs,
            *decision.decisive_factor_refs,
            *(value.source_ref for value in decision.observer_frame.directly_perceived),
            *(ref for pressure in decision.material_pressures for ref in pressure.evidence_refs),
        }
        if not refs.issubset(available_refs):
            raise ContractValidationError("decision cites unavailable evidence")
        _validate_autonomy(decision.autonomy_application, context.autonomy_mode)
        for predecessor in decision.observer_frame.draft_local_predecessor_item_keys:
            if predecessor not in items:
                raise ContractValidationError(
                    "observer frame cites an unknown draft-local predecessor"
                )

    presence_owner_items = {
        change.effective_after_item_key for change in plan.sequence.presence_changes
    }
    required_item_keys = {
        item.item_key
        for item in plan.sequence.items
        if item.owner_id not in {None, PROTECTED_USER_ID}
        and (
            item.kind in _MATERIAL_RESPONSE_KINDS
            or bool(item.durable_change_keys)
            or item.item_key in presence_owner_items
        )
    }
    missing = required_item_keys - set(links)
    if missing:
        raise ContractValidationError(
            "material sequence items lack decision links: " + ", ".join(sorted(missing))
        )
    for item_key, decision_key in links.items():
        item = items[item_key]
        decision = decisions[decision_key]
        if item.owner_id == PROTECTED_USER_ID:
            raise ContractValidationError(
                "protected-user source items cannot receive CERA decision ownership"
            )
        if item.owner_id is not None and item.owner_id != decision.owner_id:
            raise ContractValidationError(
                "decision owner does not match linked sequence item owner"
            )

    known_provisional = set(context.available_provisional_record_ids)
    for dependency in plan.provisional_dependencies:
        if dependency.provisional_record_id not in known_provisional:
            raise ContractValidationError("plan depends on an unavailable provisional record")

    transition = plan.route_transition
    if transition is not None:
        if transition.from_route is not context.logic_route:
            raise ContractValidationError(
                "route transition does not begin at the current logic owner"
            )
        if transition.to_route is not LogicRoute.ADULT:
            raise ContractValidationError(
                "ordinary cognition may hand off only to the adult logic owner"
            )


def _validate_autonomy(
    application: AutonomyApplicationV1,
    mode: CharacterAutonomyMode,
) -> None:
    if application.mind_precedence_applied is not mode.mind_precedence:
        raise ContractValidationError("decision applied the wrong mind precedence")
    if application.body_precedence_applied is not mode.body_precedence:
        raise ContractValidationError("decision applied the wrong body precedence")
