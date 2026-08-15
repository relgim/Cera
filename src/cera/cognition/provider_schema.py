"""Strict provider projection for :class:`CognitionPlanV1`."""

from __future__ import annotations

from typing import cast

from cera.sequence_first.contracts import (
    CHARACTER_ID_JSON_PATTERN,
    LOCAL_KEY_JSON_PATTERN,
    PROTECTED_USER_ID,
    STABLE_IDENTITY_JSON_PATTERN,
    ProviderReferenceScopeV1,
)
from cera.sequence_first.provider import sequence_draft_json_schema
from cera.serialization import canonical_json

from .citations import CognitionStaticCitationScopeV1, cognition_static_citation_scope
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


def _citation_description(scope: CognitionStaticCitationScopeV1) -> str:
    return (
        "Copy only the current source ref, a draft-local sequence item key, a "
        "static ref in citable_static_evidence_refs, or an evidence_ref returned "
        "by an eligible successful get_exact_record call in this request. The "
        "turn-local static lists are "
        + canonical_json(scope.to_payload())
        + ". Every context_only_static_evidence_refs value is context only and "
        "forbidden here; never count, truncate, or summarize content to change its "
        "class. Never use context_ref, a search locator, a dossier ref, an oversize "
        "exact-record ref, an expired ref, or an invented identifier. Private "
        "evidence may be cited only by a decision whose owner_id exactly matches "
        "its one knowledge owner."
    )


def _citation_ref(description: str) -> dict[str, object]:
    return {
        **_identity(),
        "description": description,
    }


def _citation_array(description: str) -> dict[str, object]:
    return {"type": "array", "items": _identity(), "description": description}


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

    static_scope = cognition_static_citation_scope(context.turn)
    citation_description = _citation_description(static_scope)
    npc_ids = [value for value in reference_scope.known_character_ids if value != PROTECTED_USER_ID]
    owner_schema: dict[str, object] = {
        "type": "string",
        "pattern": CHARACTER_ID_JSON_PATTERN,
    }
    if npc_ids:
        owner_schema = {"type": "string", "enum": npc_ids}

    perceived_fact = _strict(
        {
            "source_ref": _citation_ref(citation_description),
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
                "description": (
                    "Qualitative influence on this decision. A none-level pressure "
                    "may still cite evidence that supports the assessed absence of "
                    "that pressure."
                ),
            },
            "direction": _local_key(),
            "evidence_refs": _citation_array(citation_description),
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
    overwhelming_pressure_kind = _nullable(_local_key())
    overwhelming_pressure_kind["description"] = (
        "Null or the exact kind of a material_pressures row in this same decision "
        "record whose level is overwhelming. Never cite a pressure from another "
        "decision record."
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
            "overwhelming_pressure_kind": overwhelming_pressure_kind,
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
            "causal_trigger_refs": _citation_array(citation_description),
            "observer_frame": observer_frame,
            "perceived_event_meaning": _text(),
            "knowledge_certainty": _certainty(),
            "personal_and_social_meaning": _text(),
            "response_layers": response_layers,
            "selected_intent": _text(),
            "concise_decision_basis": _text(),
            "decisive_factor_refs": _citation_array(citation_description),
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
    sequence_schema = sequence_draft_json_schema(reference_scope=reference_scope)
    sequence_properties = cast(dict[str, object], sequence_schema["properties"])
    sequence_items = cast(dict[str, object], sequence_properties["items"])
    sequence_item = cast(dict[str, object], sequence_items["items"])
    sequence_item_properties = cast(dict[str, object], sequence_item["properties"])
    item_evidence_refs = (
        context.turn.current_source_key,
        *static_scope.citable_static_evidence_refs,
    )
    sequence_item_properties["evidence_keys"] = {
        "type": "array",
        "items": {"type": "string", "enum": list(item_evidence_refs)},
        "description": (
            "Hard sequence evidence may use only the current source ref or a ref "
            "in citable_static_evidence_refs; context_only_static_evidence_refs "
            "are forbidden. The exact static lists are "
            + canonical_json(static_scope.to_payload())
            + ". Draft-local sequence item keys remain causal_parent_item_key "
            "or cognition citation refs, never item evidence_keys."
        ),
    }
    return _strict(
        {
            "sequence": sequence_schema,
            "decision_records": {"type": "array", "items": decision_record},
            "decision_item_links": {
                "type": "array",
                "items": _strict({"item_key": _local_key(), "decision_key": _local_key()}),
            },
            "provisional_dependencies": provisional_dependencies,
            "route_transition": route_transition,
        }
    )
