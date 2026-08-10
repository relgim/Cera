"""Deterministic bridge from one frozen Pi candidate to Reader input."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from cera.cognition import CognitionPlanV1
from cera.errors import ContractValidationError
from cera.schema import from_mapping
from cera.serialization import canonical_sha256, text_sha256

from .contracts import (
    ReaderCharacterContextV1,
    ReaderRelationshipContextV1,
    ReaderValidationCustodyV1,
    ReaderValidationRequestV1,
)

if TYPE_CHECKING:
    from cera.pi_scene.contracts import LeanCandidateV1
    from cera.pi_scene.review_store import LeanSceneTurnInputV1


def build_reader_validation_input(
    *,
    candidate: LeanCandidateV1,
    turn_input: LeanSceneTurnInputV1,
) -> tuple[ReaderValidationRequestV1, ReaderValidationCustodyV1]:
    """Build a Reader package with no semantic-validator result dependency."""

    if (
        candidate.route.value != "ordinary"
        or candidate.primary_authority_kind != "codex_cognition_plan"
    ):
        raise ContractValidationError("Reader validation requires an ordinary cognition candidate")
    if (
        candidate.world_id != turn_input.world_id
        or candidate.branch_id != turn_input.branch_id
        or candidate.scene_id != turn_input.scene_id
        or candidate.exact_user_source != turn_input.exact_user_source
    ):
        raise ContractValidationError("Reader validation turn input changed candidate identity")
    try:
        authority = json.loads(candidate.primary_authority_json)
    except json.JSONDecodeError as exc:
        raise ContractValidationError("Reader cognition authority is invalid") from exc
    if not isinstance(authority, Mapping):
        raise ContractValidationError("Reader cognition authority is not an object")
    plan = from_mapping(CognitionPlanV1, authority)
    public_state = turn_input.current_state.get("public_scene_state")
    if not isinstance(public_state, str) or not public_state.strip():
        raise ContractValidationError("Reader validation requires public scene state")
    prior_prose = _immediate_prior_prose(turn_input.recent_prose)
    request = ReaderValidationRequestV1(
        schema_version=ReaderValidationRequestV1.SCHEMA_VERSION,
        cognition_plan=plan,
        exact_candidate_prose=candidate.story_text,
        immediate_prior_accepted_prose=prior_prose,
        current_public_state=public_state,
        scene_depth=_scene_depth(turn_input),
        character_context=_character_context(plan, turn_input),
        relationship_context=_relationship_context(plan, turn_input.relationships),
    )
    custody = ReaderValidationCustodyV1(
        schema_version=ReaderValidationCustodyV1.SCHEMA_VERSION,
        request_id=f"request:reader-{candidate.candidate_sha256[:24]}",
        candidate_id=candidate.candidate_id,
        world_id=candidate.world_id,
        branch_id=candidate.branch_id,
        accepted_head_sha256=candidate.accepted_head_before_sha256,
        candidate_sha256=candidate.candidate_sha256,
        cognition_plan_sha256=canonical_sha256(plan),
        candidate_prose_sha256=text_sha256(candidate.story_text),
        immediate_prior_accepted_prose_sha256=(
            None if prior_prose is None else text_sha256(prior_prose)
        ),
        validation_request_sha256=canonical_sha256(request),
    )
    return request, custody


def _scene_depth(turn_input: LeanSceneTurnInputV1) -> str:
    controls = turn_input.request_controls
    if controls is not None:
        return controls.scene_depth
    projected = turn_input.current_state.get("request_controls")
    if isinstance(projected, Mapping):
        value = projected.get("scene_depth")
        if isinstance(value, str):
            return value.lower()
    return "auto"


def _immediate_prior_prose(values: Sequence[Mapping[str, Any] | str]) -> str | None:
    for value in reversed(tuple(values)):
        if isinstance(value, str) and value.strip():
            return value
        if isinstance(value, Mapping):
            prose = value.get("exact_accepted_prose")
            if isinstance(prose, str) and prose.strip():
                return prose
    return None


def _character_context(
    plan: CognitionPlanV1,
    turn_input: LeanSceneTurnInputV1,
) -> tuple[ReaderCharacterContextV1, ...]:
    selected_ids = plan.sequence.responding_character_ids
    if not selected_ids:
        selected_ids = tuple(dict.fromkeys(decision.owner_id for decision in plan.decision_records))
    output: list[ReaderCharacterContextV1] = []
    for character_id in selected_ids:
        raw = turn_input.characters.get(character_id)
        if not isinstance(raw, Mapping):
            raise ContractValidationError(f"Reader validation lacks context for {character_id}")
        display_name = _optional_mapping_text(raw, "name") or character_id.partition(":")[2]
        output.append(
            ReaderCharacterContextV1(
                character_id=character_id,
                display_name=display_name,
                role=_optional_mapping_text(raw, "role"),
                character_logic=(
                    _optional_mapping_text(raw, "character_logic")
                    or _optional_mapping_text(raw, "personality")
                ),
                voice=_optional_mapping_text(raw, "voice"),
                voice_guidance=_voice_guidance(
                    character_id=character_id,
                    display_name=display_name,
                    values=turn_input.voice_examples,
                ),
            )
        )
    if not output:
        raise ContractValidationError("Reader validation plan has no NPC owner context")
    return tuple(output)


def _relationship_context(
    plan: CognitionPlanV1,
    relationships: Mapping[str, Mapping[str, Any]],
) -> tuple[ReaderRelationshipContextV1, ...]:
    owners = set(plan.sequence.responding_character_ids)
    output: list[ReaderRelationshipContextV1] = []
    for relationship_id, raw in relationships.items():
        participants = raw.get("participants")
        if (
            isinstance(participants, Sequence)
            and not isinstance(participants, (str, bytes))
            and owners
            and not owners.intersection(value for value in participants if isinstance(value, str))
        ):
            continue
        summary = _optional_mapping_text(raw, "summary")
        if summary is None:
            continue
        output.append(
            ReaderRelationshipContextV1(
                relationship_id=_stable_context_id("relationship", relationship_id),
                concise_context=summary,
            )
        )
    return tuple(output)


def _voice_guidance(
    *,
    character_id: str,
    display_name: str,
    values: Mapping[str, Mapping[str, Any] | str],
) -> str | None:
    local_id = character_id.partition(":")[2].lower()
    name_tokens = {token for token in re.split(r"[^a-z0-9]+", display_name.lower()) if token}
    accepted_keys = {character_id.lower(), local_id, *name_tokens}
    for raw_key, raw_value in values.items():
        if not isinstance(raw_key, str) or raw_key.lower() not in accepted_keys:
            continue
        if isinstance(raw_value, str) and raw_value.strip():
            return raw_value
        if isinstance(raw_value, Mapping):
            for field_name in ("guidance", "voice", "example", "text"):
                value = _optional_mapping_text(raw_value, field_name)
                if value is not None:
                    return value
    return None


def _optional_mapping_text(value: Mapping[str, Any], field_name: str) -> str | None:
    item = value.get(field_name)
    return item if isinstance(item, str) and item.strip() else None


def _stable_context_id(namespace: str, value: str) -> str:
    normalized = re.sub(r"[^a-z0-9_.:-]+", "_", value.lower()).strip("_")
    if not normalized or not normalized[0].isalpha():
        normalized = f"{namespace}:{text_sha256(value)[:24]}"
    if len(normalized) > 191:
        normalized = f"{namespace}:{text_sha256(value)}"
    return normalized


__all__ = ["build_reader_validation_input"]
