"""Closed JSON Schema projection for the Codex-owned AdultCraftNeed."""

from __future__ import annotations

from cera.ids import IdKind

from .models import (
    AdultCraftAxis,
    AdultCraftConcept,
    AdultCraftFamily,
    AdultCraftMode,
    AdultCraftNeed,
    AdultCraftNeedV2,
    AdultCraftNeedV3,
    OutcomeAuthorityState,
    RealizationChannel,
    SegmentCommitment,
    SpecificityRegister,
)


PROVIDER_BINDING_PLACEHOLDER = "python_computed_after_decode"


def adult_craft_need_v3_json_schema() -> dict[str, object]:
    """Closed schema with semantic and lexical concepts independently owned."""

    text = {"type": "string", "minLength": 1, "maxLength": 120}
    text_array = {"type": "array", "maxItems": 24, "items": text}
    concept_array = _enum_array(AdultCraftConcept, 20)
    outcome = _closed(
        {
            "authority_state": {
                "enum": [value.value for value in OutcomeAuthorityState]
            },
            "current_segment_commitment": {
                "enum": [value.value for value in SegmentCommitment]
            },
        }
    )
    shared_channel = {
        "minimum_register": {
            "enum": [value.value for value in SpecificityRegister]
        },
        "semantic_concepts": concept_array,
        "lexical_concepts": concept_array,
    }
    character_channel = _closed(
        {
            "channel": {
                "enum": [
                    RealizationChannel.DIALOGUE.value,
                    RealizationChannel.INNER_VOICE.value,
                ]
            },
            "character_id": _id(IdKind.CHARACTER),
            **shared_channel,
        }
    )
    scene_channel = _closed(
        {
            "channel": {
                "enum": [
                    RealizationChannel.NARRATION.value,
                    RealizationChannel.SOUND_EFFECT.value,
                    RealizationChannel.PHYSIOLOGY.value,
                ]
            },
            **shared_channel,
        }
    )
    beat_requirement = _closed(
        {
            "beat_id": _id(IdKind.BEAT),
            "actor_id": _id(IdKind.CHARACTER, IdKind.MATERIAL),
            "action_family": text,
            "object_concepts": concept_array,
            "axes": _enum_array(AdultCraftAxis, 10, minimum=1),
            "channel_requirements": {
                "type": "array",
                "minItems": 1,
                "maxItems": 10,
                "items": {"oneOf": [character_channel, scene_channel]},
            },
            "priority": {"type": "integer", "minimum": 1, "maximum": 5},
        }
    )
    card_need = _closed(
        {"character_id": _id(IdKind.CHARACTER), "section_queries": text_array}
    )
    return _closed(
        {
            "schema_version": {"const": AdultCraftNeedV3.SCHEMA_VERSION},
            "craft_need_id": _id(IdKind.ADULT_CRAFT_NEED),
            "request_id": _id(IdKind.REQUEST),
            "decision_id": _id(IdKind.DECISION),
            "sequence_plan_sha256": {
                "type": "string",
                "pattern": "^[0-9a-f]{64}$",
            },
            "mode": {"enum": [value.value for value in AdultCraftMode]},
            "families": _enum_array(AdultCraftFamily, 8, minimum=1),
            "subfamilies": text_array,
            "beat_requirements": {
                "type": "array",
                "minItems": 1,
                "maxItems": 32,
                "items": beat_requirement,
            },
            "climax": outcome,
            "aftermath": outcome,
            "character_card_sections": {
                "type": "array",
                "maxItems": 8,
                "items": card_need,
            },
        }
    )


def adult_craft_need_v2_json_schema() -> dict[str, object]:
    """Closed schema with channel ownership encoded as discriminated unions."""

    text = {"type": "string", "minLength": 1, "maxLength": 120}
    text_array = {"type": "array", "maxItems": 24, "items": text}
    concept_array = _enum_array(AdultCraftConcept, 20)
    outcome = _closed(
        {
            "authority_state": {
                "enum": [value.value for value in OutcomeAuthorityState]
            },
            "current_segment_commitment": {
                "enum": [value.value for value in SegmentCommitment]
            },
        }
    )
    character_channel = _closed(
        {
            "channel": {
                "enum": [
                    RealizationChannel.DIALOGUE.value,
                    RealizationChannel.INNER_VOICE.value,
                ]
            },
            "character_id": _id(IdKind.CHARACTER),
            "minimum_register": {
                "enum": [value.value for value in SpecificityRegister]
            },
            "required_concepts": concept_array,
        }
    )
    scene_channel = _closed(
        {
            "channel": {
                "enum": [
                    RealizationChannel.NARRATION.value,
                    RealizationChannel.SOUND_EFFECT.value,
                    RealizationChannel.PHYSIOLOGY.value,
                ]
            },
            "minimum_register": {
                "enum": [value.value for value in SpecificityRegister]
            },
            "required_concepts": concept_array,
        }
    )
    beat_requirement = _closed(
        {
            "beat_id": _id(IdKind.BEAT),
            "actor_id": _id(IdKind.CHARACTER, IdKind.MATERIAL),
            "action_family": text,
            "object_concepts": concept_array,
            "axes": _enum_array(AdultCraftAxis, 10, minimum=1),
            "channel_requirements": {
                "type": "array",
                "minItems": 1,
                "maxItems": 10,
                "items": {"oneOf": [character_channel, scene_channel]},
            },
            "priority": {"type": "integer", "minimum": 1, "maximum": 5},
        }
    )
    card_need = _closed(
        {"character_id": _id(IdKind.CHARACTER), "section_queries": text_array}
    )
    return _closed(
        {
            "schema_version": {"const": AdultCraftNeedV2.SCHEMA_VERSION},
            "craft_need_id": _id(IdKind.ADULT_CRAFT_NEED),
            "request_id": _id(IdKind.REQUEST),
            "decision_id": _id(IdKind.DECISION),
            "sequence_plan_sha256": {
                "type": "string",
                "pattern": "^[0-9a-f]{64}$",
            },
            "mode": {"enum": [value.value for value in AdultCraftMode]},
            "families": _enum_array(AdultCraftFamily, 8, minimum=1),
            "subfamilies": text_array,
            "beat_requirements": {
                "type": "array",
                "minItems": 1,
                "maxItems": 32,
                "items": beat_requirement,
            },
            "climax": outcome,
            "aftermath": outcome,
            "character_card_sections": {
                "type": "array",
                "maxItems": 8,
                "items": card_need,
            },
        }
    )


def adult_craft_need_v2_provider_draft_json_schema() -> dict[str, object]:
    schema = adult_craft_need_v2_json_schema()
    properties = schema["properties"]
    for field_name in (
        "craft_need_id",
        "request_id",
        "decision_id",
        "sequence_plan_sha256",
    ):
        properties[field_name] = {"const": PROVIDER_BINDING_PLACEHOLDER}
    return schema


def adult_craft_need_json_schema() -> dict[str, object]:
    text = {"type": "string", "minLength": 1, "maxLength": 120}
    # Provider structured outputs reject ``uniqueItems``; typed decoding
    # remains the authority for semantic uniqueness.
    text_array = {"type": "array", "maxItems": 24, "items": text}
    concept_array = _enum_array(AdultCraftConcept, 20)
    outcome = _closed(
        {
            "authority_state": {"enum": [value.value for value in OutcomeAuthorityState]},
            "current_segment_commitment": {
                "enum": [value.value for value in SegmentCommitment]
            },
        }
    )
    channel_need = _closed(
        {
            "channel": {"enum": [value.value for value in RealizationChannel]},
            "character_id": {"anyOf": [_id(IdKind.CHARACTER), {"type": "null"}]},
            "minimum_register": {"enum": [value.value for value in SpecificityRegister]},
            "required_concepts": concept_array,
        }
    )
    beat_need = _closed(
        {
            "beat_id": _id(IdKind.BEAT),
            "actor_id": _id(IdKind.CHARACTER, IdKind.MATERIAL),
            "action_family": text,
            "object_concepts": concept_array,
            "axes": _enum_array(AdultCraftAxis, 10),
            "channels": _enum_array(RealizationChannel, 5, minimum=1),
            "priority": {"type": "integer", "minimum": 1, "maximum": 5},
        }
    )
    card_need = _closed(
        {"character_id": _id(IdKind.CHARACTER), "section_queries": text_array}
    )
    return _closed(
        {
            "schema_version": {"const": AdultCraftNeed.SCHEMA_VERSION},
            "craft_need_id": _id(IdKind.ADULT_CRAFT_NEED),
            "request_id": _id(IdKind.REQUEST),
            "decision_id": _id(IdKind.DECISION),
            "sequence_plan_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
            "mode": {"enum": [value.value for value in AdultCraftMode]},
            "families": _enum_array(AdultCraftFamily, 8, minimum=1),
            "subfamilies": text_array,
            "axes": _enum_array(AdultCraftAxis, 10, minimum=1),
            "channel_needs": {"type": "array", "minItems": 1, "maxItems": 16, "items": channel_need},
            "beat_needs": {"type": "array", "minItems": 1, "maxItems": 32, "items": beat_need},
            "climax": outcome,
            "aftermath": outcome,
            "character_card_sections": {"type": "array", "maxItems": 8, "items": card_need},
            "all_participants_confirmed_adults": {"const": True},
            "consent_valid_for_current_segment": {"const": True},
            "blocked_nonconsensual_generation_excluded": {"const": True},
        }
    )


def adult_craft_need_provider_draft_json_schema() -> dict[str, object]:
    """Provider-facing craft need with Python-owned identity bindings.

    A provider cannot know the canonical hash of a SceneDecision while it is
    authoring that decision.  It also does not own request or decision
    identity.  The closed provider schema therefore requires an unmistakable
    placeholder for those four fields; the Codex adapter replaces them only
    after the SceneDecision has passed typed decoding.
    """

    schema = adult_craft_need_json_schema()
    properties = schema["properties"]
    for field_name in (
        "craft_need_id",
        "request_id",
        "decision_id",
        "sequence_plan_sha256",
    ):
        properties[field_name] = {"const": PROVIDER_BINDING_PLACEHOLDER}
    return schema


def _closed(properties: dict[str, object]) -> dict[str, object]:
    return {"type": "object", "additionalProperties": False, "properties": properties, "required": list(properties)}


def _id(*kinds: IdKind) -> dict[str, object]:
    alternatives = "|".join(value.value for value in kinds)
    return {"type": "string", "pattern": rf"^(?:{alternatives}):[A-Za-z0-9][A-Za-z0-9._-]{{0,127}}$"}


def _enum_array(enum_type, maximum: int, *, minimum: int = 0) -> dict[str, object]:
    return {
        "type": "array",
        "minItems": minimum,
        "maxItems": maximum,
        "items": {"enum": [value.value for value in enum_type]},
    }
