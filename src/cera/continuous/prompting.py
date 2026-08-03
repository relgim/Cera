"""Canonical prompt assembly for the shadow continuous sessions."""

from __future__ import annotations

import json
from typing import Any, Iterable

from cera.serialization import canonical_bytes, to_primitive

from .contracts import (
    CharacterSummaryEnvelopeV1,
    PromptComponentUsageV1,
    RichPlannerSequenceV1,
    ValidatorTaskMode,
)
from .packets import (
    ContinuousPlannerPacketKind,
    ContinuousPlannerTurnPacketV1,
    LeanSceneChangeContextV1,
)


CONTINUOUS_PLANNER_PROMPT_VERSION = "cera.continuous_planner_prompt.v15"
CONTINUOUS_VALIDATOR_PROMPT_VERSION = "cera.continuous_validator_prompt.v10"


PLANNER_STABLE_INSTRUCTIONS = """You are CERA's continuous Scene Planner. You own causal and psychological logic, rational participant selection, current-scene continuity, and a rich sequence of materially distinct causal beats. Each beat must explain perception, goal, pressure, tactic, causality, observable direction, private-state ownership, material continuity, resulting state, evidence, protected-user allowance, and open realization space. Never prewrite final prose. DeepSeek owns exact wording, gestures, pacing, and imagery within that space. Provider conversation is not story authority. The newest Python packet and accepted-final-sequence envelopes supersede conflicting provisional plans. A receipt-bound lean_continuation_authority in the newest packet is exact only for its active_cast_ids and optional public_continuation_anchor; it never authorizes omitted prior plan fields, prose, private state, or another character. Obey the closed Python context mode: ordinary compatible turns are lean; projection assistance contains only its explicitly named keys; reconstruction occurs only while Python initializes a new physical thread. The current ingress receipt is immutable Python authority: never reinterpret its raw source, source spans, actor, speaker, world, branch, session, request, turn, protected-user, adapter, or classification identities. Python allocates every valid request-local evidence binding. Cite only exact binding_key values supplied in the current packet or returned by cera_world_read; arbitrary labels are invalid. Search/list only locate candidates and never create evidence. ACTIVE bindings are durable hard authority. Python-resolved stable accepted-context references may support only their exact public visibility or exact private owner. Every accepted_turn_id inside the input packet is a prior/reference identity, never the identity of the current provisional result; the current output accepted_turn_id must be null and provisional must be true. A projection-assisted payload is advisory only for its named stable keys. Neither establishes older history, card traits, rules, or private facts absent from the accepted sequence. DERIVED bindings are navigation or retrieval context only and can never be the sole support for a hard character, rule, event, or memory decision; fetch and cite the relevant ACTIVE record. A character-private binding may appear only on a beat with exactly one NPC assertion owner, and that owner must match the private owner; split shared action into separate beats when characters use different private knowledge. Character summary envelopes are Python-derived hints bound only to exact ACTIVE record fields, remain incomplete, and do not replace cited authority. Python supplies a trusted receipt-bound source-unit ledger and exact protected-user source claims. On an ordinary Planner turn, treat exact protected-user ingress as immutable causal evidence already present in the visible scene and begin the rich sequence with the first NPC-controlled causal consequence. Do not emit a standalone Ted-owned rich beat whose only function is to replay supplied ingress; preserve its exact claim and binding as evidence for the NPC consequence instead. This is a generation rule, not permission to delete, normalize, or repair provider output after generation. Roles are closed and mutually exclusive within each beat: one character ID must appear in exactly one of the seven role arrays. action_owner_ids own actions; state_owner_ids own thought, emotion, bodily or consent/decision states; speaker_ids own utterances; affected, addressed, observing, and referenced roles never authorize an action or state. If one character both acts, changes state, or speaks, split those assertions into separate causally ordered beats so that character has exactly one role in each beat. Any assertion owned by character:ted requires one exact supplied claim. Put its claim_key in protected_user_allowance.source_claim_keys and cite its source binding. Do not paraphrase or extend Ted's action, dialogue, thought, state, emotion, decision, movement, or consent in beat free text: preserve an exact supplied span or refer to the claim key without restating it. The Python mechanical-connective binding permits only nonmeaningful syntax and never Ted action, dialogue, thought, decision, movement, consent, emotion, or a new fact. The 32-call transport ceiling is runaway protection; reaching it is terminal, so never repeat an unproductive lookup. Search only the authorized branch ACTIVE world view, labeled non-authoritative DERIVED views, and your Planner session context. Never inspect Validator context, rejected candidate directories, debug logs, unrelated files, or other sessions. Preserve creator, identity, privacy, character-knowledge, branch, consent/capacity, evidence, participant, and protected-user boundaries. No retry or fallback."""


VALIDATOR_STABLE_INSTRUCTIONS = """You are CERA's separate continuous Scene Validator. Compare the immutable ingress receipt and complete source-unit ledger, complete Planner sequence, resolved Python evidence bindings, exact protected-user claims, complete DeepSeek realization, exhaustive story-segment role ledgers, and protected-user realization spans. For every stable accepted-reference key cited by the Planner, Python supplies one cited-only exact-value authority record. Independently compare the Planner beat's perception, goal, tactic, causality, action/dialogue direction, private state, material continuity, and resulting state with that exact cited field. A structurally valid key never makes an incompatible claim valid: classify the candidate as a concern and do not preserve the incompatible claim in an accepted final sequence. Never infer an uncited accepted value, another owner's private value, a prior projection, prior prose, or full history. Return one closed typed finalization package. Independently classify every exact Composer segment for its semantic relation to character:ted; do not copy or trust the Composer's role label as your answer. A protected assertion includes Ted's explicit, implicit, or pronoun-only action, movement, dialogue, thought, emotion, bodily state, consent, decision, or response and must equal one exact current ingress claim. Otherwise choose exactly one closed non-owning relation: affected_by_npc, addressed_by_npc, observed_by_npc, or referenced_only_by_npc, and name the exact NPC assertion owners; use none only when Ted is absent. Every Planner beat, Composer segment, final field, final item, event, accepted fact, and persistence directive must preserve the closed role model: action_owner_ids own actions; state_owner_ids own thoughts, emotions, bodily states, consent, and decisions; speaker_ids own utterances; affected_ids, addressed_ids, observing_ids, and referenced_ids never authorize an assertion. An NPC action may affect or address Ted without inventing his response. Every populated final field cites exact Composer segment keys and repeats their exact role and claim unions. Split distinct ownership and public/private information into separate fields or items. Event participants equal the complete involved-role union and the event summary is the exact ordered realized_event projection. For persistence, select only an exact add or replace projection from one final-field value into a character or relationship record and declare the current Python-provided persistence-policy hash, exact record identity, subject identities, approved semantic JSON path, current revision, and prior-value hash when replacing. Character writes are limited to reasoning_summary, latest_accepted_changes, turn_claims, accepted_facts, development, or state. Relationship writes are limited to observations, accepted_facts, development, relationship_state, or state, and both participants must be justified by the cited final field's closed roles and owner scope. Identity, schema, revision, visibility, knowledge-owner, participant, source, Genesis, provenance, authority, and index metadata are immutable. Rule, location, event, and scene record classes remain disabled until their subject schemas are separately typed. Do not author edit-operation or created-field bookkeeping; Python derives it and validates the complete post-edit record before publication. Never request remove, increment, append, or create-file semantics. ACTIVE evidence is hard authority; DERIVED evidence only locates authority; private evidence remains owner-scoped. Final stop state equals the last resulting state. Scene Summary preserves the exact accepted-turn allow-list and exact tail; Python attaches provenance. You may propose but never apply persistence directives. Never generate or revise prose. Preserve creator, identity, privacy, knowledge, branch, consent/capacity, participant, protected-user, evidence, and atomic-publication boundaries. The 32-call ceiling is terminal. No retry, fallback, Fast mode, or hidden repair."""


def _usage(name: str, payload: bytes) -> PromptComponentUsageV1:
    return PromptComponentUsageV1(
        component=name,
        byte_count=len(payload),
        estimated_tokens=(len(payload) + 3) // 4,
    )


def build_planner_turn_prompt(
    *,
    current_packet: ContinuousPlannerTurnPacketV1,
    character_summaries: Iterable[CharacterSummaryEnvelopeV1] = (),
    scene_change_context: LeanSceneChangeContextV1 | None = None,
) -> tuple[str, tuple[PromptComponentUsageV1, ...]]:
    if not isinstance(current_packet, ContinuousPlannerTurnPacketV1):
        raise TypeError("current_packet must be a validated continuous Planner packet")
    if scene_change_context is not None and not isinstance(
        scene_change_context, LeanSceneChangeContextV1
    ):
        raise TypeError("scene_change_context must be a validated lean scene context")
    components: list[tuple[str, bytes]] = []
    summaries = tuple(character_summaries)
    expected_summary_bindings = tuple(
        (
            value["character_id"],
            value["source_path"],
            value["source_revision"],
            value["source_sha256"],
        )
        for value in current_packet.character_summary_bindings
    )
    actual_summary_bindings = tuple(
        (
            value.character_id,
            value.source_path_or_record_id.replace("\\", "/"),
            value.source_revision,
            value.source_sha256,
        )
        for value in summaries
    )
    if actual_summary_bindings != expected_summary_bindings:
        raise ValueError(
            "Planner character summaries do not match the Python-bound packet"
        )
    summary_bytes = canonical_bytes(tuple(to_primitive(value) for value in summaries))
    packet_bytes = canonical_bytes(current_packet.to_payload())
    scene_bytes = canonical_bytes(
        scene_change_context.to_payload() if scene_change_context is not None else {}
    )
    mode_bytes = canonical_bytes(
        {
            "context_mode": current_packet.context_mode,
            "projection_assisted_trigger": (
                current_packet.projection_assisted_trigger
            ),
            "projection_reference_keys": current_packet.projection_reference_keys,
        }
    )
    prior_identity_bytes = canonical_bytes(
        (
            {
                "prior_reference_accepted_turn_id": (
                    current_packet.compact_accepted_head_receipt.accepted_turn_id
                ),
                "meaning": (
                    "accepted continuity reference only; never copy into the "
                    "current Planner output"
                ),
                "current_output_contract": {
                    "accepted_turn_id": None,
                    "provisional": True,
                },
            }
            if current_packet.compact_accepted_head_receipt is not None
            else {}
        )
    )
    is_scene_change = (
        current_packet.packet_kind is ContinuousPlannerPacketKind.SCENE_CHANGE
    )
    if is_scene_change != (scene_change_context is not None):
        raise ValueError("scene-change packet and context must be supplied together")
    if scene_change_context is not None and (
        current_packet.scene_change_envelope_sha256
        != scene_change_context.context_sha256
    ):
        raise ValueError("scene-change packet hash does not bind the supplied context")
    components.extend(
        (
            ("context_mode", mode_bytes),
            ("character_summaries", summary_bytes),
            ("scene_change", scene_bytes),
            ("prior_reference_identity_semantics", prior_identity_bytes),
            ("current_packet", packet_bytes),
        )
    )
    prompt = (
        "[PLANNER CONTEXT MODE]\n"
        + mode_bytes.decode("utf-8")
        + "\n\n[CHARACTER CARD SUMMARIES - EACH IS INCOMPLETE]\n"
        + summary_bytes.decode("utf-8")
        + "\n\n[SCENE CHANGE CONTEXT]\n"
        + scene_bytes.decode("utf-8")
        + "\n\n[PRIOR ACCEPTED CONTEXT IDENTITY SEMANTICS]\n"
        + prior_identity_bytes.decode("utf-8")
        + "\n\n[CURRENT AUTHORITATIVE TURN PACKET]\n"
        + packet_bytes.decode("utf-8")
    )
    return prompt, tuple(_usage(name, payload) for name, payload in components)


def planner_base_instruction_usage() -> PromptComponentUsageV1:
    """One-time stored/base bytes, deliberately excluded from turn submissions."""

    return _usage(
        "base_stable_instructions",
        PLANNER_STABLE_INSTRUCTIONS.encode("utf-8"),
    )


def prompt_text_usage(
    component: str,
    prompt: str,
) -> PromptComponentUsageV1:
    """Measure exact text separately from logical component bytes."""

    if not isinstance(component, str) or not component.strip():
        raise ValueError("prompt text usage component is required")
    return _usage(component, prompt.encode("utf-8"))


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
    cited_accepted_evidence: tuple[dict[str, Any], ...] = (),
) -> tuple[str, tuple[PromptComponentUsageV1, ...]]:
    request = {
        "schema_version": "cera.continuous_validator_request.v4",
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
        "cited_accepted_evidence": cited_accepted_evidence,
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
        + "its exact text and one closed roles ledger. action_owner_ids own actions; state_owner_ids "
        + "own thought, emotion, bodily state, consent, and decisions; speaker_ids own dialogue. "
        + "affected_ids, addressed_ids, observing_ids, and referenced_ids are non-owning roles. "
        + "Any assertion owned by character:ted must exactly equal one supplied claim and cite only "
        + "that claim. An NPC action may affect or address Ted without inventing Ted's response. "
        + "Your segment roles are advisory metadata, not final semantic authority: the separate "
        + "Validator independently adjudicates every exact segment, and Python rejects disagreement."
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
