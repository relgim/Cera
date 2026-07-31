"""Shared provider-draft fixtures for Structural Contract v2 tests."""

from __future__ import annotations

from cera.adult_craft import AdultCraftNeedV2
from cera.composer import ComposerSubmission
from cera.reasoner import ReasonerOutcome, ReasonerOutcomeStatus


def reasoner_draft_from_outcome(outcome: ReasonerOutcome) -> dict[str, object]:
    if outcome.status is not ReasonerOutcomeStatus.DECISION_READY:
        return {
            "schema_version": "cera.codex_reasoner_draft.v2",
            "status": outcome.status.value,
            "route": None,
            "scene_intent": None,
            "responding_npc_ids": [],
            "floor_owner_id": None,
            "participation": [],
            "character_moves": [],
            "current_beats": [],
            "stop_before": None,
            "future_segments": [],
            "writer_must_preserve": [],
            "uncertainties": [],
            "prohibited_inferences": [],
            "insufficiencies": list(outcome.insufficiencies),
            "blocker_code": (
                outcome.blocker_code.value
                if outcome.blocker_code is not None
                else None
            ),
            "adult_craft_need": None,
            "protected_user_boundary_acknowledged": True,
        }
    decision = outcome.decision
    assert decision is not None
    beat_keys = {
        value.beat_id: f"beat_{index + 1}"
        for index, value in enumerate(decision.current_segment.ordered_beats)
    }
    participation = []
    for value in outcome.participation:
        participation.append(
            {
                "character_id": str(value.character_id),
                "intervention_reason": (
                    None
                    if value.character_id == decision.floor_owner_id
                    else value.intervention_reason.value
                ),
                "evidence_ids": [str(item) for item in value.evidence_ids],
            }
        )
    adult = None
    if outcome.adult_craft_need is not None:
        need = outcome.adult_craft_need
        requirements = []
        if isinstance(need, AdultCraftNeedV2):
            for value in need.beat_requirements:
                requirements.append(
                    {
                        "beat_key": beat_keys[value.beat_id],
                        "action_family": value.action_family,
                        "object_concepts": [
                            item.value for item in value.object_concepts
                        ],
                        "axes": [item.value for item in value.axes],
                        "channel_requirements": [
                            _channel_requirement(item)
                            for item in value.channel_requirements
                        ],
                        "priority": value.priority,
                    }
                )
        else:
            channel_by_kind = {
                value.channel.value: value for value in need.channel_needs
            }
            for value in need.beat_needs:
                requirements.append(
                    {
                        "beat_key": beat_keys[value.beat_id],
                        "action_family": value.action_family,
                        "object_concepts": [
                            item.value for item in value.object_concepts
                        ],
                        "axes": [item.value for item in value.axes],
                        "channel_requirements": [
                            _legacy_channel_requirement(channel_by_kind[item.value])
                            for item in value.channels
                        ],
                        "priority": value.priority,
                    }
                )
        adult = {
            "mode": need.mode.value,
            "families": [value.value for value in need.families],
            "subfamilies": list(need.subfamilies),
            "beat_requirements": requirements,
            "climax": {
                "authority_state": need.climax.authority_state.value,
                "current_segment_commitment": (
                    need.climax.current_segment_commitment.value
                ),
            },
            "aftermath": {
                "authority_state": need.aftermath.authority_state.value,
                "current_segment_commitment": (
                    need.aftermath.current_segment_commitment.value
                ),
            },
            "character_card_sections": [
                {
                    "character_id": str(value.character_id),
                    "section_queries": list(value.section_queries),
                }
                for value in need.character_card_sections
            ],
        }
    elif decision.route.value == "consent_valid_adult":
        adult = {
            "mode": "adult_on",
            "families": ["general"],
            "subfamilies": ["consent-valid current segment"],
            "beat_requirements": [
                {
                    "beat_key": beat_keys[value.beat_id],
                    "action_family": "validated_current_beat",
                    "object_concepts": ["anatomy"],
                    "axes": ["direct_vocabulary"],
                    "channel_requirements": [
                        {
                            "channel": "narration",
                            "minimum_register": "direct",
                            "required_concepts": ["anatomy"],
                        }
                    ],
                    "priority": 3,
                }
                for value in decision.current_segment.ordered_beats
            ],
            "climax": {
                "authority_state": "allowed",
                "current_segment_commitment": "not_selected",
            },
            "aftermath": {
                "authority_state": "allowed",
                "current_segment_commitment": "not_selected",
            },
            "character_card_sections": [],
        }
    return {
        "schema_version": "cera.codex_reasoner_draft.v2",
        "status": outcome.status.value,
        "route": decision.route.value,
        "scene_intent": decision.scene_intent,
        "responding_npc_ids": [
            str(value) for value in decision.responding_npc_ids
        ],
        "floor_owner_id": str(decision.floor_owner_id),
        "participation": participation,
        "character_moves": [
            {
                "character_id": str(value.character_id),
                "perception": value.perception,
                "selected_intent": value.selected_intent,
                "action_direction": value.action_direction,
                "evidence_ids": [
                    str(item) for item in value.evidence_ids
                ],
                "knowledge_constraints": list(value.knowledge_constraints),
            }
            for value in decision.character_moves
        ],
        "current_beats": [
            {
                "beat_key": beat_keys[value.beat_id],
                "actor_id": str(value.actor_id),
                "state": value.state.value,
                "neutral_event": value.neutral_event,
                "evidence_ids": [
                    str(item) for item in value.evidence_ids
                ],
            }
            for value in decision.current_segment.ordered_beats
        ],
        "stop_before": decision.current_segment.stop_before,
        "future_segments": [
            {
                "segment_key": f"future_{index + 1}",
                "activation_conditions": list(value.activation_conditions),
                "invalidation_conditions": list(value.invalidation_conditions),
                "possible_consequences": list(value.possible_consequences),
                "open_user_choice": value.open_user_choice,
            }
            for index, value in enumerate(decision.future_segments)
        ],
        "writer_must_preserve": list(decision.writer_must_preserve),
        "uncertainties": list(decision.uncertainties),
        "prohibited_inferences": list(decision.prohibited_inferences),
        "insufficiencies": [],
        "blocker_code": None,
        "adult_craft_need": adult,
        "protected_user_boundary_acknowledged": True,
    }


def composition_draft_from_submission(
    submission: ComposerSubmission,
) -> dict[str, object]:
    story = submission.candidate.story_text
    realization_anchors = [
        {
            "owner_id": str(value.owner_id),
            "kind": value.kind.value,
            "anchor": _anchor(story, value.start, value.end),
            "source_unit_id": (
                str(value.source_unit_id)
                if value.source_unit_id is not None
                else None
            ),
            "beat_id": (
                str(value.beat_id) if value.beat_id is not None else None
            ),
        }
        for value in submission.manifest.character_spans
    ]
    if not realization_anchors:
        participants = submission.manifest.participant_realizations
        beats = submission.manifest.realized_beat_ids
        for index, beat_id in enumerate(beats):
            owner = participants[index % len(participants)].character_id
            realization_anchors.append(
                {
                    "owner_id": str(owner),
                    "kind": "action",
                    "anchor": {"quote": story, "occurrence": 0},
                    "source_unit_id": None,
                    "beat_id": str(beat_id),
                }
            )
        covered = {value["owner_id"] for value in realization_anchors}
        for participant in participants:
            if str(participant.character_id) not in covered:
                realization_anchors.append(
                    {
                        "owner_id": str(participant.character_id),
                        "kind": "dialogue",
                        "anchor": {"quote": story, "occurrence": 0},
                        "source_unit_id": None,
                        "beat_id": str(beats[0]),
                    }
                )
    return {
        "schema_version": "cera.deepseek_composition_draft.v2",
        "story_text": story,
        "source_coverage_anchors": [
            {
                "source_unit_id": str(value.source_unit_id),
                "anchor": _anchor(story, value.start, value.end),
            }
            for value in submission.manifest.source_unit_coverage
        ],
        "realization_anchors": realization_anchors,
        "terminal_boundary_anchor": {
            "quote": story.rstrip(),
            "occurrence": 0,
        },
    }


def composition_draft_v5_from_submission(
    submission: ComposerSubmission,
) -> dict[str, object]:
    """Project an accepted fixture through the active provider DTO."""

    story = submission.candidate.story_text
    segment_key = "core"
    realizations = [
        {
            "owner_id": str(value.owner_id),
            "kind": value.kind.value,
            "segment_key": segment_key,
            "authority_id": str(
                value.source_unit_id
                if value.source_unit_id is not None
                else value.beat_id
            ),
        }
        for value in submission.manifest.character_spans
    ]
    if not realizations:
        participants = submission.manifest.participant_realizations
        beats = submission.manifest.realized_beat_ids
        for index, beat_id in enumerate(beats):
            owner = participants[index % len(participants)].character_id
            realizations.append(
                {
                    "owner_id": str(owner),
                    "kind": "action",
                    "segment_key": segment_key,
                    "authority_id": str(beat_id),
                }
            )
        covered = {value["owner_id"] for value in realizations}
        for participant in participants:
            if str(participant.character_id) not in covered:
                realizations.append(
                    {
                        "owner_id": str(participant.character_id),
                        "kind": "dialogue",
                        "segment_key": segment_key,
                        "authority_id": str(beats[0]),
                    }
                )
    return {
        "schema_version": "cera.deepseek_composition_draft.v5",
        "story_segments": [
            {"segment_key": segment_key, "text": story},
        ],
        "source_coverage": [
            {
                "source_unit_id": str(value.source_unit_id),
                "segment_keys": [segment_key],
            }
            for value in submission.manifest.source_unit_coverage
        ],
        "realization_segments": realizations,
        "specificity_coverage": [
            {
                "obligation_key": value.obligation_key,
                "segment_keys": [segment_key],
            }
            for value in submission.manifest.adult_specificity_coverage
        ],
        "terminal_segment_key": segment_key,
    }


def composition_draft_v6_from_submission(
    submission,
) -> dict[str, object]:
    """Translate a synthetic authoritative manifest into the active provider DTO."""

    payload = composition_draft_v5_from_submission(submission)
    payload["schema_version"] = "cera.deepseek_composition_draft.v6"
    for realization in payload["realization_segments"]:
        realization.pop("owner_id")
    return payload


def _anchor(story: str, start: int, end: int) -> dict[str, object]:
    left = start
    right = end
    quote = story[left:right]
    while story.find(quote) != story.rfind(quote):
        if left > 0:
            left -= 1
        if right < len(story):
            right += 1
        if left == 0 and right == len(story):
            break
        quote = story[left:right]
    return {"quote": story[left:right], "occurrence": 0}


def _channel_requirement(value) -> dict[str, object]:
    result = {
        "channel": value.channel.value,
        "minimum_register": value.minimum_register.value,
        "required_concepts": [
            item.value for item in value.required_concepts
        ],
    }
    if hasattr(value, "character_id"):
        result["character_id"] = str(value.character_id)
    return result


def _legacy_channel_requirement(value) -> dict[str, object]:
    result = {
        "channel": value.channel.value,
        "minimum_register": value.minimum_register.value,
        "required_concepts": [
            item.value for item in value.required_concepts
        ],
    }
    if value.character_id is not None:
        result["character_id"] = str(value.character_id)
    return result
