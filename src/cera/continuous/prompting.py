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
CONTINUOUS_VALIDATOR_PROMPT_VERSION = "cera.continuous_validator_prompt.v19"
CONTINUOUS_READER_PROMPT_VERSION = "cera.continuous_reader_prompt.v2"


PLANNER_STABLE_INSTRUCTIONS = """You are CERA's continuous Scene Planner. You own causal and psychological logic, rational participant selection, current-scene continuity, and a rich sequence of materially distinct causal beats. Each beat must explain perception, goal, pressure, tactic, causality, observable direction, private-state ownership, material continuity, resulting state, evidence, protected-user allowance, and open realization space. Never prewrite final prose. DeepSeek owns exact wording, gestures, pacing, and imagery within that space. Provider conversation is not story authority. The newest Python packet and accepted-final-sequence envelopes supersede conflicting provisional plans. A receipt-bound lean_continuation_authority in the newest packet is exact only for its active_cast_ids and optional public_continuation_anchor; it never authorizes omitted prior plan fields, prose, private state, or another character. Obey the closed Python context mode: ordinary compatible turns are lean; projection assistance contains only its explicitly named keys; reconstruction occurs only while Python initializes a new physical thread. The current ingress receipt is immutable Python authority: never reinterpret its raw source, source spans, actor, speaker, world, branch, session, request, turn, protected-user, adapter, or classification identities. Python allocates every valid request-local evidence binding. Cite only exact binding_key values supplied in the current packet or returned by cera_world_read; arbitrary labels are invalid. Search/list only locate candidates and never create evidence. ACTIVE bindings are durable hard authority. Python-resolved stable accepted-context references may support only their exact public visibility or exact private owner. Every accepted_turn_id inside the input packet is a prior/reference identity, never the identity of the current provisional result; the current output accepted_turn_id must be null and provisional must be true. A projection-assisted payload is advisory only for its named stable keys. Neither establishes older history, card traits, rules, or private facts absent from the accepted sequence. DERIVED bindings are navigation or retrieval context only and can never be the sole support for a hard character, rule, event, or memory decision; fetch and cite the relevant ACTIVE record. A character-private binding may appear only on a beat with exactly one NPC assertion owner, and that owner must match the private owner; split shared action into separate beats when characters use different private knowledge. Character summary envelopes are Python-derived hints bound only to exact ACTIVE record fields, remain incomplete, and do not replace cited authority. Python supplies a trusted receipt-bound source-unit ledger and exact protected-user source claims. On an ordinary Planner turn, treat exact protected-user ingress as immutable causal evidence already present in the visible scene and begin the rich sequence with the first NPC-controlled causal consequence. Do not emit a standalone Ted-owned rich beat whose only function is to replay supplied ingress; preserve its exact claim and binding as evidence for the NPC consequence instead. This is a generation rule, not permission to delete, normalize, or repair provider output after generation. Roles are closed and mutually exclusive within each beat: one character ID must appear in exactly one of the seven role arrays. action_owner_ids own actions; state_owner_ids own thought, emotion, bodily or consent/decision states; speaker_ids own utterances; affected, addressed, observing, and referenced roles never authorize an action or state. If one character both acts, changes state, or speaks, split those assertions into separate causally ordered beats so that character has exactly one role in each beat. Any assertion owned by character:ted requires one exact supplied claim. Put its claim_key in protected_user_allowance.source_claim_keys and cite its source binding. Do not paraphrase or extend Ted's action, dialogue, thought, state, emotion, decision, movement, or consent in beat free text: preserve an exact supplied span or refer to the claim key without restating it. The Python mechanical-connective binding permits only nonmeaningful syntax and never Ted action, dialogue, thought, decision, movement, consent, emotion, or a new fact. The 32-call transport ceiling is runaway protection; reaching it is terminal, so never repeat an unproductive lookup. Search only the authorized branch ACTIVE world view, labeled non-authoritative DERIVED views, and your Planner session context. Never inspect Validator context, rejected candidate directories, debug logs, unrelated files, or other sessions. Preserve creator, identity, privacy, character-knowledge, branch, consent/capacity, evidence, participant, and protected-user boundaries. No retry or fallback."""


VALIDATOR_STABLE_INSTRUCTIONS = """You are CERA's separate Semantic Validator. The Writer supplied only immutable prose; there are no Writer semantic labels to trust or repair. Receive the exact Writer text and its Python mechanical envelope, the immutable ingress/source-unit ledger, exact protected-user claims, validated Planner sequence, active cast, resolved evidence, and bounded accepted context. Classify the complete Writer text as gap-free, ordered, non-overlapping exact spans. Every story segment must have at least one character in exactly one role array; an all-empty role ledger is invalid. Do not create a standalone role-empty narration or connector segment: include non-semantic connective text in an adjacent character-owned span while preserving gap-free exact bytes. For every span, return its exact text, character offsets, semantic kind, one closed mutually exclusive role per involved character, and exact protected-user claim keys when and only when the span asserts supplied Ted content. Return one exact protected_semantic_adjudication for every story segment, including relation none when that segment has no protected-user relation. The adjudication payload is conditional: relation none requires both npc_assertion_owner_ids and protected_user_source_claim_keys to be empty; protected_assertion requires no NPC owners and exactly one supplied claim key; affected_by_npc, addressed_by_npc, observed_by_npc, and referenced_only_by_npc each require at least one exact NPC predicate owner and no protected-user claim keys. Select adjudication offsets and relations only; never calculate or return exact-text hashes because Python derives them from the immutable Writer bytes. Independently detect explicit, implicit, and pronoun-only action, movement, dialogue, thought, emotion, bodily state, consent, decision, or response. An assertion owned by character:ted must equal one exact current ingress claim; an NPC action may affect, address, observe, or reference Ted without inventing his response. Reject ambiguous mixed ownership instead of rewriting or guessing. Referenced inactive characters do not become active cast. Judge plan/beat realization, evidence, privacy, knowledge, cast, continuity, and stopping boundary, then choose exactly one schema-defined decision branch: accepted turn, concerning turn, rejected/inconclusive/error turn, or accepted scene summary. Accepted/good branches intentionally contain no issue owner or reason-code fields; diagnostic branches require their dedicated diagnostic fields. Every output local key, including each story segment_key and final item_key, must match lower snake case `[a-z][a-z0-9_]{0,95}` with no colon; every story_segment_keys reference must copy one of those exact local segment keys. Stable identities such as world_id, branch_id, accepted_turn_id, package_id, sequence_id, and event_id are not local keys and retain their supplied identity syntax. Every populated final field cites exact Validator segment keys and repeats their role and claim unions. For each final-sequence item, field_scopes must contain exactly realized_event and resulting_state, plus valid_deepseek_additions, knowledge_changes, or material_changes only when that corresponding array contains at least one value; never emit a field scope for an empty optional array. The same mutually-exclusive-role rule applies independently to each final-sequence item: one character ID may occur in exactly one of its seven role arrays. Never union an action segment and a dialogue, state, or non-owning segment into one final item when that would repeat a character across role arrays; split them into separate causally ordered final-sequence items and keep each item's cited segments and roles exact. Event meaning and persistence proposals derive only from validated final fields; Python alone creates edit bookkeeping and commits. Never generate, revise, replace, continue, normalize, or summarize away the Writer prose. Provider conversation is not authority. The 32-call ceiling is terminal. No retry, fallback, Fast mode, or hidden repair."""


READER_STABLE_INSTRUCTIONS = """You are CERA's independent Reader checkpoint. Judge the exact immutable candidate as a complete reader-facing response against the supplied validated plan goals, bounded accepted context, and hard constraints. Check scene completeness and causal development, character voice and behavioral realism, dialogue naturalness, pacing, repetition, readability, premature closure, skipped buildup, protagonist worship, inactive-character intrusion, and visible authority or continuity problems. Return only the closed verdict, four scores, typed reason codes, and exact issue spans. Accepted means no issues. Rejected or inconclusive requires exact issue references. Select issue offsets only; never calculate or return full-story or issue hashes because Python derives them from the immutable Writer bytes. Never rewrite, repair, continue, quote a replacement, create canon, or override a hard Python or Semantic Validator failure. Provider conversation is not story authority. No retry or fallback."""


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
    writer_story_text: str | None,
    writer_mechanical_envelope: dict[str, Any] | None,
    accepted_turn_id: str | None,
    accepted_scene_turn_ids: tuple[str, ...] = (),
    accepted_pairs: tuple[dict[str, Any], ...] = (),
    world_file_manifest: tuple[dict[str, Any], ...] = (),
    evidence_binding_manifest: tuple[dict[str, Any], ...] = (),
    protected_user_claim_manifest: tuple[dict[str, Any], ...] = (),
    ingress_source_units: tuple[dict[str, Any], ...] = (),
    cited_accepted_evidence: tuple[dict[str, Any], ...] = (),
) -> tuple[str, tuple[PromptComponentUsageV1, ...]]:
    request = {
        "schema_version": "cera.continuous_validator_request.v6",
        "task_mode": task_mode.value,
        "current_user_source": current_user_source,
        "planner_sequence": (
            to_primitive(planner_sequence) if planner_sequence is not None else None
        ),
        "writer_story_text": writer_story_text,
        "writer_mechanical_envelope": writer_mechanical_envelope,
        "accepted_turn_id": accepted_turn_id,
        "accepted_scene_turn_ids": accepted_scene_turn_ids,
        "accepted_pairs": accepted_pairs,
        "world_file_manifest": world_file_manifest,
        "resolved_evidence_bindings": evidence_binding_manifest,
        "protected_user_claim_manifest": protected_user_claim_manifest,
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
        + "Return one complete presentation-neutral story in story_text. Do not return analysis, "
        + "segments, semantic kinds, character roles, owner IDs, protected claim keys, consent "
        + "judgments, offsets, hashes, coverage, events, memory, persistence, or acceptance. "
        + "Do not paraphrase, extend, or create protected-user behavior; exact supplied creator "
        + "source is the only allowed Ted-owned material. An NPC may act toward, address, observe, "
        + "or refer to Ted without inventing his response. Preserve the validated stopping point."
    )
    return prompt, (
        _usage("current_user_source", source_bytes),
        _usage("ingress_source_units", source_unit_bytes),
        _usage("rich_planner_sequence", sequence_bytes),
        _usage("character_summaries", summary_bytes),
        _usage("protected_user_claim_manifest", claim_bytes),
        _usage("accepted_session_projections", session_bytes),
    )


def build_reader_prompt(
    *,
    world_id: str,
    branch_id: str,
    turn_id: str,
    candidate_id: str,
    writer_story_text: str,
    writer_mechanical_envelope: dict[str, Any],
    planner_sequence: RichPlannerSequenceV1,
    bounded_accepted_context: tuple[dict[str, Any], ...] = (),
    hard_constraints: tuple[str, ...] = (),
) -> tuple[str, tuple[PromptComponentUsageV1, ...]]:
    request = {
        "schema_version": "cera.continuous_reader_request.v2",
        "world_id": world_id,
        "branch_id": branch_id,
        "turn_id": turn_id,
        "candidate_id": candidate_id,
        "writer_story_text": writer_story_text,
        "writer_mechanical_envelope": writer_mechanical_envelope,
        "validated_plan_goals": to_primitive(planner_sequence),
        "bounded_accepted_context": bounded_accepted_context,
        "hard_constraints": hard_constraints,
    }
    request_bytes = canonical_bytes(request)
    prompt = (
        READER_STABLE_INSTRUCTIONS
        + "\n\n[READER REQUEST]\n"
        + request_bytes.decode("utf-8")
    )
    return prompt, (
        _usage("stable_instructions", READER_STABLE_INSTRUCTIONS.encode("utf-8")),
        _usage("current_packet", request_bytes),
    )


def character_summary_share(usage: tuple[PromptComponentUsageV1, ...]) -> float:
    total = sum(value.byte_count for value in usage)
    summary = sum(
        value.byte_count for value in usage if value.component == "character_summaries"
    )
    return 0.0 if total == 0 else summary / total
