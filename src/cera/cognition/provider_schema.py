"""Strict provider projection for :class:`CognitionPlanV1`."""

from __future__ import annotations

from cera.sequence_first.contracts import (
    CHARACTER_ID_JSON_PATTERN,
    LOCAL_KEY_JSON_PATTERN,
    PROTECTED_USER_ID,
    STABLE_IDENTITY_JSON_PATTERN,
    ProviderReferenceScopeV1,
)
from cera.sequence_first.provider import sequence_draft_json_schema

from .contracts import CognitionTurnContextV1


def _strict(properties: dict[str, object]) -> dict[str, object]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def _nullable(value: dict[str, object]) -> dict[str, object]:
    return {"anyOf": [value, {"type": "null"}]}


def _string_array(*, pattern: str | None = None) -> dict[str, object]:
    item: dict[str, object] = {"type": "string"}
    if pattern is not None:
        item["pattern"] = pattern
    return {"type": "array", "items": item}


def _text() -> dict[str, object]:
    return {"type": "string", "minLength": 1}


def _local_key() -> dict[str, object]:
    return {"type": "string", "pattern": LOCAL_KEY_JSON_PATTERN}


def _identity() -> dict[str, object]:
    return {"type": "string", "pattern": STABLE_IDENTITY_JSON_PATTERN}


def _certainty() -> dict[str, object]:
    return {
        "type": "string",
        "enum": ["unknown", "low", "moderate", "high", "established"],
    }


def cognition_plan_json_schema(
    *,
    context: CognitionTurnContextV1,
    reference_scope: ProviderReferenceScopeV1,
) -> dict[str, object]:
    """Project exact finite input scopes without exposing Python custody."""

    npc_ids = [value for value in reference_scope.known_character_ids if value != PROTECTED_USER_ID]
    owner_schema: dict[str, object] = {
        "type": "string",
        "pattern": CHARACTER_ID_JSON_PATTERN,
    }
    if npc_ids:
        owner_schema = {"type": "string", "enum": npc_ids}

    perceived_fact = _strict(
        {
            "source_ref": _identity(),
            "concise_perception": _text(),
            "certainty": _certainty(),
        }
    )
    observer_frame = _strict(
        {
            "directly_perceived": {
                "type": "array",
                "items": perceived_fact,
                "description": (
                    "One row per distinct perceived fact. The same source_ref may "
                    "support multiple rows when concise_perception or certainty "
                    "differs; never repeat an exact source_ref, concise_perception, "
                    "and certainty row."
                ),
            },
            "inferred_meanings": _string_array(),
            "unavailable_or_ambiguous": _string_array(),
            "draft_local_predecessor_item_keys": _string_array(pattern=LOCAL_KEY_JSON_PATTERN),
        }
    )
    pressure = _strict(
        {
            "kind": _local_key(),
            "level": {
                "type": "string",
                "enum": ["none", "low", "moderate", "high", "overwhelming"],
            },
            "direction": _local_key(),
            "evidence_refs": _string_array(pattern=STABLE_IDENTITY_JSON_PATTERN),
        }
    )
    response_layers = _strict(
        {
            "immediate_involuntary_reaction": _nullable(_text()),
            "conscious_interpretation": _text(),
            "subconscious_pressure": _nullable(_text()),
            "considered_judgment": _text(),
        }
    )
    autonomy = _strict(
        {
            "mind_precedence_applied": {
                "type": "boolean",
                "const": context.autonomy_mode.mind_precedence,
            },
            "body_precedence_applied": {
                "type": "boolean",
                "const": context.autonomy_mode.body_precedence,
            },
            "user_direction_disposition": {
                "type": "string",
                "enum": [
                    "source_direction",
                    "proposed_outcome",
                    "partially_realized",
                    "overridden",
                ],
            },
            "overwhelming_pressure_kind": _nullable(_local_key()),
            "concise_effect": _text(),
        }
    )
    close_alternative = _nullable(
        _strict(
            {
                "intent": _text(),
                "why_not_selected": _text(),
                "remains_realistically_available": {"type": "boolean"},
            }
        )
    )
    decision_record = _strict(
        {
            "decision_key": _local_key(),
            "owner_id": owner_schema,
            "causal_trigger_refs": _string_array(pattern=STABLE_IDENTITY_JSON_PATTERN),
            "observer_frame": observer_frame,
            "perceived_event_meaning": _text(),
            "knowledge_certainty": _certainty(),
            "personal_and_social_meaning": _text(),
            "response_layers": response_layers,
            "selected_intent": _text(),
            "concise_decision_basis": _text(),
            "decisive_factor_refs": _string_array(pattern=STABLE_IDENTITY_JSON_PATTERN),
            "material_pressures": {"type": "array", "items": pressure},
            "autonomy_application": autonomy,
            "anticipated_immediate_effect": _text(),
            "close_alternative": close_alternative,
            "uncertainty": _certainty(),
        }
    )
    provisional_id: dict[str, object] = _identity()
    provisional_dependencies: dict[str, object] = {
        "type": "array",
        "items": _strict(
            {
                "provisional_record_id": provisional_id,
                "assumed_value": {"type": "string", "enum": ["true", "false"]},
                "concise_dependency": _text(),
            }
        ),
    }
    provisional_ids = context.available_provisional_record_ids
    if provisional_ids:
        provisional_id["enum"] = list(provisional_ids)
        provisional_id.pop("pattern", None)
    else:
        provisional_dependencies["maxItems"] = 0

    route_transition = _nullable(
        _strict(
            {
                "from_route": {
                    "type": "string",
                    "const": context.logic_route.value,
                },
                "to_route": {"type": "string", "const": "adult"},
                "boundary_item_key": _local_key(),
                "non_graphic_handoff_summary": _text(),
                "character_effect_refs": _string_array(pattern=STABLE_IDENTITY_JSON_PATTERN),
                "return_condition": _text(),
            }
        )
    )
    return _strict(
        {
            "sequence": sequence_draft_json_schema(reference_scope=reference_scope),
            "decision_records": {"type": "array", "items": decision_record},
            "decision_item_links": {
                "type": "array",
                "items": _strict({"item_key": _local_key(), "decision_key": _local_key()}),
            },
            "provisional_dependencies": provisional_dependencies,
            "route_transition": route_transition,
        }
    )
