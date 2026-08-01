"""Canonical prompt assembly for the shadow continuous sessions."""

from __future__ import annotations

import json
from typing import Any, Iterable

from cera.serialization import canonical_bytes, to_primitive

from .contracts import (
    AcceptedFinalSequenceEnvelopeV1,
    CharacterSummaryEnvelopeV1,
    PromptComponentUsageV1,
    RichPlannerSequenceV1,
    ValidatorTaskMode,
)


CONTINUOUS_PLANNER_PROMPT_VERSION = "cera.continuous_planner_prompt.v2"
CONTINUOUS_VALIDATOR_PROMPT_VERSION = "cera.continuous_validator_prompt.v2"


PLANNER_STABLE_INSTRUCTIONS = """You are CERA's continuous Scene Planner. You own causal and psychological logic, rational participant selection, current-scene continuity, and a rich sequence of materially distinct causal beats. Each beat must explain perception, goal, pressure, tactic, causality, observable direction, private-state ownership, material continuity, resulting state, evidence, protected-user allowance, and open realization space. Never prewrite final prose. DeepSeek owns exact wording, gestures, pacing, and imagery within that space. Provider conversation is not story authority. The newest Python packet and accepted-final-sequence envelopes supersede conflicting provisional plans. Python allocates every valid request-local evidence binding. Cite only exact binding_key values supplied in the current packet or returned by cera_world_read; arbitrary labels are invalid. Search/list only locate candidates and never create evidence. Read the exact record before a hard character, rule, event, memory, or private-state decision, then cite the returned binding. The 32-call transport ceiling is runaway protection; reaching it is terminal, so never repeat an unproductive lookup. Search only the authorized branch ACTIVE world view, labeled non-authoritative DERIVED views, and your Planner session context. Never inspect Validator context, rejected candidate directories, debug logs, unrelated files, or other sessions. Preserve creator, identity, privacy, character-knowledge, branch, consent/capacity, evidence, participant, and protected-user boundaries. No retry or fallback."""


VALIDATOR_STABLE_INSTRUCTIONS = """You are CERA's separate continuous Scene Validator. Compare the complete current user source, complete Planner sequence, resolved Python evidence-binding manifest, and complete DeepSeek realization. Return one closed typed package: complete final realized sequence, existing creator-review assessment, bounded semantic world edit operations, every created-field log, one event candidate, and an optional scene summary only in the explicit Scene Summary task. Each final-sequence item must retain Planner beat keys so Python can trace it and every edit back to exact resolved evidence. In Scene Summary mode, preserve the supplied accepted-turn IDs exactly; Python, not you, attaches the exact last-five pair payload. Preserve valid DeepSeek-added detail; identify omissions and contradictions; keep private states with their owner; track knowledge and material changes; and describe the final stop state. You may propose semantic edits but never apply them. Search/list locate records; cera_world_read returns the only valid exact-record bindings. The 32-call ceiling is terminal runaway protection. Search only the authorized branch ACTIVE view, labeled non-authoritative DERIVED views, your Validator context, and the current candidate view. Rejected context is advisory only and cannot enter an accepted-turn allow-list or scene summary. Never generate or revise story prose. No retry, fallback, Fast mode, or hidden repair."""


def _usage(name: str, payload: bytes) -> PromptComponentUsageV1:
    return PromptComponentUsageV1(
        component=name,
        byte_count=len(payload),
        estimated_tokens=(len(payload) + 3) // 4,
    )


def build_planner_turn_prompt(
    *,
    current_packet: dict[str, Any],
    accepted_envelopes: Iterable[AcceptedFinalSequenceEnvelopeV1] = (),
    character_summaries: Iterable[CharacterSummaryEnvelopeV1] = (),
    scene_change_envelope: dict[str, Any] | None = None,
) -> tuple[str, tuple[PromptComponentUsageV1, ...]]:
    components: list[tuple[str, bytes]] = []
    accepted = tuple(accepted_envelopes)
    summaries = tuple(character_summaries)
    accepted_bytes = canonical_bytes(tuple(to_primitive(value) for value in accepted))
    summary_bytes = canonical_bytes(tuple(to_primitive(value) for value in summaries))
    packet_bytes = canonical_bytes(current_packet)
    scene_bytes = canonical_bytes(scene_change_envelope or {})
    components.extend(
        (
            ("stable_instructions", PLANNER_STABLE_INSTRUCTIONS.encode("utf-8")),
            ("accepted_final_sequences", accepted_bytes),
            ("character_summaries", summary_bytes),
            ("scene_change", scene_bytes),
            ("current_packet", packet_bytes),
        )
    )
    prompt = (
        PLANNER_STABLE_INSTRUCTIONS
        + "\n\n[ACCEPTED FINAL SEQUENCE ENVELOPES]\n"
        + accepted_bytes.decode("utf-8")
        + "\n\n[CHARACTER CARD SUMMARIES - EACH IS INCOMPLETE]\n"
        + summary_bytes.decode("utf-8")
        + "\n\n[SCENE CHANGE CONTEXT]\n"
        + scene_bytes.decode("utf-8")
        + "\n\n[CURRENT AUTHORITATIVE TURN PACKET]\n"
        + packet_bytes.decode("utf-8")
    )
    return prompt, tuple(_usage(name, payload) for name, payload in components)


def build_validator_prompt(
    *,
    task_mode: ValidatorTaskMode,
    current_user_source: str | None,
    planner_sequence: RichPlannerSequenceV1 | None,
    deepseek_realization: str | None,
    accepted_turn_id: str | None,
    accepted_scene_turn_ids: tuple[str, ...] = (),
    accepted_pairs: tuple[dict[str, Any], ...] = (),
    world_file_manifest: tuple[dict[str, Any], ...] = (),
    evidence_binding_manifest: tuple[dict[str, Any], ...] = (),
) -> tuple[str, tuple[PromptComponentUsageV1, ...]]:
    request = {
        "schema_version": "cera.continuous_validator_request.v1",
        "task_mode": task_mode.value,
        "current_user_source": current_user_source,
        "planner_sequence": (
            to_primitive(planner_sequence) if planner_sequence is not None else None
        ),
        "deepseek_realization": deepseek_realization,
        "accepted_turn_id": accepted_turn_id,
        "accepted_scene_turn_ids": accepted_scene_turn_ids,
        "accepted_pairs": accepted_pairs,
        "world_file_manifest": world_file_manifest,
        "resolved_evidence_bindings": evidence_binding_manifest,
        "authority_note": (
            "Only supplied accepted turn IDs and pairs authorize a scene summary; "
            "rejected or remembered candidates are non-authoritative."
        ),
    }
    request_bytes = canonical_bytes(request)
    prompt = VALIDATOR_STABLE_INSTRUCTIONS + "\n\n[VALIDATOR REQUEST]\n" + request_bytes.decode("utf-8")
    return prompt, (
        _usage("stable_instructions", VALIDATOR_STABLE_INSTRUCTIONS.encode("utf-8")),
        _usage("current_packet", request_bytes),
    )


def build_continuous_composer_prompt(
    *,
    current_user_source: str,
    planner_sequence: RichPlannerSequenceV1,
    character_summaries: Iterable[CharacterSummaryEnvelopeV1] = (),
) -> tuple[str, tuple[PromptComponentUsageV1, ...]]:
    summaries = tuple(character_summaries)
    source_bytes = current_user_source.encode("utf-8")
    sequence_bytes = canonical_bytes(planner_sequence)
    summary_bytes = canonical_bytes(summaries)
    prompt = (
        "[CURRENT USER SOURCE]\n"
        + current_user_source
        + "\n\n[COMPLETE RICH PLANNER SEQUENCE]\n"
        + sequence_bytes.decode("utf-8")
        + "\n\n[SELECTED CHARACTER SUMMARIES - EACH IS INCOMPLETE]\n"
        + summary_bytes.decode("utf-8")
        + "\n\nRealize the full sequence while retaining the declared DeepSeek realization space."
    )
    return prompt, (
        _usage("current_user_source", source_bytes),
        _usage("rich_planner_sequence", sequence_bytes),
        _usage("character_summaries", summary_bytes),
    )


def character_summary_share(usage: tuple[PromptComponentUsageV1, ...]) -> float:
    total = sum(value.byte_count for value in usage)
    summary = sum(
        value.byte_count for value in usage if value.component == "character_summaries"
    )
    return 0.0 if total == 0 else summary / total
