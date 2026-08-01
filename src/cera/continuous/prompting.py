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


CONTINUOUS_PLANNER_PROMPT_VERSION = "cera.continuous_planner_prompt.v6"
CONTINUOUS_VALIDATOR_PROMPT_VERSION = "cera.continuous_validator_prompt.v6"


PLANNER_STABLE_INSTRUCTIONS = """You are CERA's continuous Scene Planner. You own causal and psychological logic, rational participant selection, current-scene continuity, and a rich sequence of materially distinct causal beats. Each beat must explain perception, goal, pressure, tactic, causality, observable direction, private-state ownership, material continuity, resulting state, evidence, protected-user allowance, and open realization space. Never prewrite final prose. DeepSeek owns exact wording, gestures, pacing, and imagery within that space. Provider conversation is not story authority. The newest Python packet and accepted-final-sequence envelopes supersede conflicting provisional plans. Python allocates every valid request-local evidence binding. Cite only exact binding_key values supplied in the current packet or returned by cera_world_read; arbitrary labels are invalid. Search/list only locate candidates and never create evidence. ACTIVE bindings are durable hard authority. Receipt-bound accepted-session facts may support only their exact public visibility or the exact private owner named on the projection; they do not establish older history, card traits, rules, or private facts absent from that accepted sequence. DERIVED bindings are navigation or retrieval context only and can never be the sole support for a hard character, rule, event, or memory decision; fetch and cite the relevant ACTIVE record. A character-private binding may appear only on a beat with exactly one NPC actor, and that actor must be its owner; split shared action into separate beats when actors use different private knowledge. Character summary envelopes are Python-derived hints bound only to exact ACTIVE record fields, remain incomplete, and do not replace cited authority. Python supplies an ingress-owned source-unit ledger and exact protected-user source claims. Only units explicitly owned by character:ted authorize Ted action or dialogue. Put their claim_key values in protected_user_allowance.source_claim_keys and cite their source binding. Do not paraphrase or extend Ted's action or dialogue in beat free text: preserve an exact supplied span or refer to the claim key without restating it. The Python mechanical-connective binding permits only nonmeaningful syntax and never Ted action, dialogue, thought, decision, movement, consent, emotion, or a new fact. The 32-call transport ceiling is runaway protection; reaching it is terminal, so never repeat an unproductive lookup. Search only the authorized branch ACTIVE world view, labeled non-authoritative DERIVED views, and your Planner session context. Never inspect Validator context, rejected candidate directories, debug logs, unrelated files, or other sessions. Preserve creator, identity, privacy, character-knowledge, branch, consent/capacity, evidence, participant, and protected-user boundaries. No retry or fallback."""


VALIDATOR_STABLE_INSTRUCTIONS = """You are CERA's separate continuous Scene Validator. Compare the complete current user source, ingress-owned source-unit ledger, complete Planner sequence, resolved Python evidence-binding manifest, exact Python protected-user claim manifest, complete DeepSeek realization, its exhaustive actor/speaker story-segment ledger, and its exact protected-user realization spans. Return one closed typed package: complete final realized sequence, existing creator-review assessment, bounded semantic world edit operations, every created-field log, one event candidate, and an optional scene summary only in the explicit Scene Summary task. Each final-sequence item must retain Planner beat keys; exact actor_ids and subject_ids; and one visibility/owner scope for every populated factual field. Every field scope must cite exact Composer story_segment_keys and repeat only the actors, subjects, and protected-user claims derived from those segments. Split public/private information and distinct authorship into separately scoped fields or items. A field authored by character:ted may contain only one exact supplied claim, never a summary or extension. The final stop state must exactly equal the last item's resulting_state. Event participants must equal the union of final actors and subjects, and the event summary must be the exact ordered concatenation of cited realized_event fields. Every edit and created field must name one source final field and preserve that field's exact claim provenance. When the source field carries a protected-user claim, the persisted value must equal that exact field value and the reason must be `Persist accepted final field <field_name>.` ACTIVE bindings are hard authority; DERIVED bindings are retrieval context and cannot alone authorize a hard fact. Private evidence remains owner-scoped. Exact protected-user action requires current-source authority; a Python mechanical allowance cannot authorize action or dialogue. Do not invent, paraphrase, extend, or misattribute protected-user action, dialogue, thought, decision, emotion, consent, or movement. In Scene Summary mode, preserve the supplied accepted-turn IDs exactly; Python, not you, attaches every exact accepted-pair provenance record and the exact last-five pair payload. Preserve valid DeepSeek-added detail; identify omissions and contradictions; track knowledge and material changes; and describe the final stop state. You may propose semantic edits but never apply them. Search/list locate records; cera_world_read returns the only valid exact-record bindings. The 32-call ceiling is terminal runaway protection. Search only the authorized branch ACTIVE view, labeled non-authoritative DERIVED views, your Validator context, and the current candidate view. Rejected context is advisory only and cannot enter an accepted-turn allow-list or scene summary. Never generate or revise story prose. No retry, fallback, Fast mode, or hidden repair."""


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
    protected_user_claim_manifest: tuple[dict[str, Any], ...] = (),
    deepseek_protected_user_realizations: tuple[dict[str, Any], ...] = (),
    deepseek_story_segments: tuple[dict[str, Any], ...] = (),
    ingress_source_units: tuple[dict[str, Any], ...] = (),
    accepted_session_projections: tuple[dict[str, Any], ...] = (),
) -> tuple[str, tuple[PromptComponentUsageV1, ...]]:
    request = {
        "schema_version": "cera.continuous_validator_request.v3",
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
        "protected_user_claim_manifest": protected_user_claim_manifest,
        "deepseek_protected_user_realizations": deepseek_protected_user_realizations,
        "deepseek_story_segments": deepseek_story_segments,
        "ingress_source_units": ingress_source_units,
        "accepted_session_projections": accepted_session_projections,
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
    ingress_source_units: tuple[dict[str, Any], ...],
    planner_sequence: RichPlannerSequenceV1,
    character_summaries: Iterable[CharacterSummaryEnvelopeV1] = (),
    protected_user_claim_manifest: tuple[dict[str, Any], ...] = (),
    accepted_session_projections: tuple[dict[str, Any], ...] = (),
) -> tuple[str, tuple[PromptComponentUsageV1, ...]]:
    summaries = tuple(character_summaries)
    source_bytes = current_user_source.encode("utf-8")
    sequence_bytes = canonical_bytes(planner_sequence)
    summary_bytes = canonical_bytes(summaries)
    claim_bytes = canonical_bytes(protected_user_claim_manifest)
    session_bytes = canonical_bytes(accepted_session_projections)
    source_unit_bytes = canonical_bytes(ingress_source_units)
    prompt = (
        "[CURRENT USER SOURCE]\n"
        + current_user_source
        + "\n\n[INGRESS-OWNED SOURCE UNITS]\n"
        + source_unit_bytes.decode("utf-8")
        + "\n\n[COMPLETE RICH PLANNER SEQUENCE]\n"
        + sequence_bytes.decode("utf-8")
        + "\n\n[SELECTED CHARACTER SUMMARIES - EACH IS INCOMPLETE]\n"
        + summary_bytes.decode("utf-8")
        + "\n\n[EXACT PROTECTED-USER CLAIM MANIFEST]\n"
        + claim_bytes.decode("utf-8")
        + "\n\n[OWNER-SCOPED ACCEPTED-SESSION PROJECTIONS]\n"
        + session_bytes.decode("utf-8")
        + "\n\nRealize the full sequence while retaining the declared DeepSeek realization space. "
        + "For every exact protected-user claim copied into story_text, return one exact "
        + "protected_user_realizations span with the same claim_key, kind, exact_text, and "
        + "zero-based Python string offsets. Do not paraphrase, extend, or otherwise create "
        + "protected-user behavior. Also return story_segments that cover story_text from "
        + "offset zero to its exact end with no gaps or overlaps. Every segment must preserve "
        + "its exact text and declare its actor_ids, subject_ids, and dialogue speaker. Mentioning "
        + "Ted as a target or topic makes him a subject, not the actor. Any segment acted or spoken by "
        + "character:ted must exactly equal one supplied claim and cite only that claim."
    )
    return prompt, (
        _usage("current_user_source", source_bytes),
        _usage("ingress_source_units", source_unit_bytes),
        _usage("rich_planner_sequence", sequence_bytes),
        _usage("character_summaries", summary_bytes),
        _usage("protected_user_claim_manifest", claim_bytes),
        _usage("accepted_session_projections", session_bytes),
    )


def character_summary_share(usage: tuple[PromptComponentUsageV1, ...]) -> float:
    total = sum(value.byte_count for value in usage)
    summary = sum(
        value.byte_count for value in usage if value.component == "character_summaries"
    )
    return 0.0 if total == 0 else summary / total
