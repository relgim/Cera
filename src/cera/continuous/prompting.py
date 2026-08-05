"""Canonical prompt assembly for the shadow continuous sessions."""

from __future__ import annotations

import json
from typing import Any, Iterable

from cera.serialization import canonical_bytes, canonical_sha256, to_primitive

from .contracts import (
    ACTIVE_VALIDATOR_WRITER_HARD_CLASSES,
    CharacterSummaryEnvelopeV1,
    CompactWriterBriefV1,
    CompactWriterVoiceCueV1,
    PromptComponentUsageV1,
    RichPlannerSequenceV1,
    ValidatorTaskMode,
    WriterRealizationBoundaryV1,
    WriterRecallDirectiveV1,
)
from .packets import (
    ContinuousPlannerPacketKind,
    ContinuousPlannerTurnPacketV1,
    LeanSceneChangeContextV1,
)


CONTINUOUS_PLANNER_PROMPT_VERSION = "cera.continuous_planner_prompt.v17"
CONTINUOUS_VALIDATOR_PROMPT_VERSION = "cera.continuous_validator_prompt.v36"
WRITER_BEAT_REALIZATION_CONSTRAINTS_VERSION = (
    "cera.writer_beat_realization_constraints.v2"
)

VALIDATOR_IDENTITY_INSTRUCTIONS = (
    "Copy package_id, world_id, and branch_id exactly from the current Validator "
    "request. Their submitted schema constants are Python-owned."
)
CONTINUOUS_READER_PROMPT_VERSION = "cera.continuous_reader_prompt.v5"


PLANNER_STABLE_INSTRUCTIONS = """You are CERA's continuous Scene Planner. You own causal and psychological logic, rational participant selection, current-scene continuity, and a rich sequence of materially distinct causal beats. Each beat must explain perception, goal, pressure, tactic, causality, observable direction, private-state ownership, material continuity, resulting state, evidence, protected-user allowance, and open realization space. Never prewrite final prose. DeepSeek owns exact wording, gestures, pacing, and imagery within that space. Provider conversation is not story authority. The newest Python packet and accepted-final-sequence envelopes supersede conflicting provisional plans. A receipt-bound lean_continuation_authority in the newest packet is exact only for its active_cast_ids and optional public_continuation_anchor; it never authorizes omitted prior plan fields, prose, private state, or another character. Obey the closed Python context mode: ordinary compatible turns are lean; projection assistance contains only its explicitly named keys; reconstruction occurs only while Python initializes a new physical thread. The current ingress receipt is immutable Python authority: never reinterpret its raw source, source spans, actor, speaker, world, branch, session, request, turn, protected-user, adapter, or classification identities. Python allocates every valid request-local evidence binding. Cite only exact binding_key values supplied in the current packet or returned by cera_world_read; arbitrary labels are invalid. Search/list only locate candidates and never create evidence. ACTIVE bindings are durable hard authority. Every beat with an NPC assertion owner must cite at least one exact ACTIVE world-record binding for that NPC; current ingress, mechanical allowance, DERIVED context, or another character's binding is not enough. Every private-state beat must cite the exact character-private ACTIVE binding whose knowledge_owner_id matches its sole NPC assertion owner. When one character has a visible action and another has a private reaction, emit separate beats and cite each owner's ACTIVE binding on that owner's beat. Python-resolved stable accepted-context references may support only their exact public visibility or exact private owner. Every accepted_turn_id inside the input packet is a prior/reference identity, never the identity of the current provisional result; the current output accepted_turn_id must be null and provisional must be true. A projection-assisted payload is advisory only for its named stable keys. Neither establishes older history, card traits, rules, or private facts absent from the accepted sequence. DERIVED bindings are navigation or retrieval context only and can never be the sole support for a hard character, rule, event, or memory decision; fetch and cite the relevant ACTIVE record. A character-private binding may appear only on a beat with exactly one NPC assertion owner, and that owner must match the private owner; split shared action into separate beats when characters use different private knowledge. Character summary envelopes are Python-derived hints bound only to exact ACTIVE record fields, remain incomplete, and do not replace cited authority. Python supplies a trusted receipt-bound source-unit ledger and exact protected-user source claims. On an ordinary Planner turn, treat exact protected-user ingress as immutable causal evidence already present in the visible scene and begin the rich sequence with the first NPC-controlled causal consequence. Do not emit a standalone Ted-owned rich beat whose only function is to replay supplied ingress; preserve its exact claim and binding as evidence for the NPC consequence instead. This is a generation rule, not permission to delete, normalize, or repair provider output after generation. Roles are closed and mutually exclusive within each beat: one character ID must appear in exactly one of the seven role arrays. action_owner_ids own actions; state_owner_ids own thought, emotion, bodily or consent/decision states; speaker_ids own utterances; affected, addressed, observing, and referenced roles never authorize an action or state. If one character both acts, changes state, or speaks, split those assertions into separate causally ordered beats so that character has exactly one role in each beat. Any assertion owned by character:ted requires one exact supplied claim. Put its claim_key in protected_user_allowance.source_claim_keys and cite its source binding. Do not paraphrase or extend Ted's action, dialogue, thought, state, emotion, decision, movement, or consent in beat free text: preserve an exact supplied span or refer to the claim key without restating it. The Python mechanical-connective binding permits only nonmeaningful syntax and never Ted action, dialogue, thought, decision, movement, consent, emotion, or a new fact. The 32-call transport ceiling is runaway protection; reaching it is terminal, so never repeat an unproductive lookup. Search only the authorized branch ACTIVE world view, labeled non-authoritative DERIVED views, and your Planner session context. Never inspect Validator context, rejected candidate directories, debug logs, unrelated files, or other sessions. Preserve creator, identity, privacy, character-knowledge, branch, consent/capacity, evidence, participant, and protected-user boundaries. No retry or fallback."""


VALIDATOR_STABLE_INSTRUCTIONS = """You are CERA's separate Semantic Validator. The Writer supplied only immutable prose; there are no Writer semantic labels to trust or repair. Receive the exact Writer text and its Python mechanical envelope, the immutable ingress/source-unit ledger, exact protected-user claims, validated Planner sequence, active cast, resolved evidence, bounded accepted context, and the closed Writer realization boundary. Classify the complete Writer text as gap-free, ordered, non-overlapping exact realization_segments. For every accepted or concern span, select story_material_assertion or presentation_only. A story_material_assertion has causal, durable, or future continuity significance and requires exact Planner/evidence authority. Presentation_only is compatible soft noncanonical drift: expression, gaze, pause, cadence, posture, micro-action, ambient texture, a generic incidental prop, vague low-stakes conversational color, harmless wording or order variation, or nonpersistent spatial phrasing. A broad subjective assessment such as an evening being quiet, tiring, pleasant, or uneventful is presentation-only when the Planner authorized a general answer and the phrase creates no causal or persistent fact. A generic prop such as a dish towel in a kitchen is presentation-only when it is not causally used or retained. Mentioning or offering an incidental household item as optional low-stakes invitation color is also soft presentation when the prose does not prepare, transfer, acquire, consume, inventory, causally require, or retain that item for future continuity. Do not classify such an optional mention as new_continuity_object or unsupported_task_or_event merely because the Planner did not name the item. Generic local staging that only gives concrete prose shape to an already-authorized mandatory action may also be split into presentation_only incidental_prop or nonpersistent_spatial_phrasing spans when it adds no second task or outcome, retained inventory or location, contradiction, or future reliance. The authorized action itself remains story_material_assertion and must be supported by separate exact material spans. An additional preparation, transfer, acquisition, consumption, inventory change, task, outcome, durable location, or future reliance remains a hard material assertion. Presentation-only prose remains visible but never enters final sequence, events, material changes, memory, relationships, summaries, accepted context, persistence, or canon. A consequential object, specific task or event, durable relocation or position, material change, relationship/memory/knowledge fact, unauthorized private fact, protected-user behavior, mandatory-beat omission or reversal, or stopping-boundary violation is a hard violation and never presentation-only. The Validator, not Python, classifies meaning.

A compatible presentation-only span does not become hard merely because the Planner did not enumerate its exact staging. Planner language such as no unsupported movement or object interaction constrains causal or durable movement, relocation, object use, and future continuity; it does not prohibit a transient turn, posture change, glance, pause, or other harmless scene-local micro-action. If a span fits a soft presentation class, does not contradict durable state, and does not omit, reverse, or materially replace a mandatory causal beat, classify it presentation_only. Use mandatory_beat_omission_or_reversal only for that material beat failure. The legacy broad planner_sequence_departure label is not an active Validator recall class.

When one sentence combines an authorized material event with generic local realization scaffold, split it at exact codepoint boundaries. Keep the causal verb and other text needed to establish the authorized event in story_material_assertion spans; place only the nonretained prop, surface, container, or transient spatial phrasing in presentation_only spans. Final fields may cite only the material spans. Do not make the authorized event itself presentation-only, and do not use this split for a new task, new outcome, retained material fact, protected-user assertion, unauthorized private fact, or future continuity obligation.

Accepted and concern decisions use realization_segments and protected_semantic_adjudications. Every story_material_assertion segment must have at least one character in exactly one role array. Presentation_only may use action for a transient NPC-owned visible behavior, dialogue with exactly one NPC speaker for optional low-stakes color, narration with non-owning active-cast roles for nonpersistent framing, or actorless narration with all seven role arrays empty only when presentation_class is nonpersistent_atmosphere. Private_state and consent_or_decision are always story_material_assertion. Set presentation_class to one exact allowed class only for presentation_only; set it to null for story_material_assertion. Soft drift does not cause rejection or Writer recall. Accept the first candidate that is hard-safe and substantially realizes the required causal beats and stopping point; do not demand exact screenplay wording or optimize among multiple safe candidates. For every realization span, return character offsets, semantic kind, one closed mutually exclusive role per involved character, and exact protected-user claim keys when and only when the span asserts supplied Ted content. Return one exact protected_semantic_adjudication for every realization segment, including relation none when that segment has no protected-user relation. Every active adjudication must also return protected_user_source_unit_keys. Keep that array empty except for source_grounded_public_state. The adjudication payload is conditional: relation none requires Ted to be absent and owner, claim, and source-unit arrays to be empty; protected_assertion requires no NPC owners, exactly one supplied claim key, and no source-unit key; affected_by_npc, addressed_by_npc, observed_by_npc, and referenced_only_by_npc each require at least one exact NPC predicate owner and empty claim and source-unit arrays. Use neutral_presentation_reference only for presentation_only narration classified nonpersistent_atmosphere where Ted appears only in referenced_ids, no character owns an action, state, or speech predicate, and all owner, claim, and source-unit arrays are empty. Use source_grounded_public_state only when Codex independently judges that presentation_only narration is a minimal semantically equivalent restatement of public state explicitly established by one exact current ingress source unit. It requires presentation_class nonpersistent_spatial_phrasing, Ted only in referenced_ids, no assertion owner, no NPC owner, no claim, and exactly that one source_unit_key. `Ted stood at the threshold` may restate source-established waiting at the threshold; `Ted stepped inside`, `Ted hesitated`, `Ted smiled back`, a Ted response, emotion, thought, decision, consent, bodily state, speech, or causal transition does not. Exact Ted dialogue remains protected_assertion and must match one exact supplied claim. Both presentation relations are noncanonical and never create an event, memory, persistence, or accepted protected-user authority.

Rejected, inconclusive, and error decisions instead use diagnostic_story_segments and diagnostic_protected_semantic_adjudications and must not return realization_segments. Diagnostic spans contain offsets, kind, roles, grounding_status, and claim keys but never exact_text or hashes; Python derives both from immutable Writer bytes. A Writer-attributable rejection sets writer_recall_eligibility to eligible and identifies only exact offending diagnostic segment keys with one or more prohibited_detail_classes from the closed boundary. Inconclusive, error, Python, authority, transport, contract, branch, or accounting defects are recall-ineligible and return no writer_recall_violations. Recall feedback is diagnostic only and cannot add facts, change the frozen Planner package, merge attempts, patch rejected prose, or enter accepted ancestry.

Use grounding_status ungrounded_protected_user_assertion only for a character:ted assertion with zero supplied claim keys. Never use it for a non-Ted assertion or with any claim. All other diagnostic spans use grounded and retain the normal exact-claim rule. Every diagnostic adjudication repeats that grounding status and uses violation_classification ungrounded_protected_user_assertion exactly for that violation; otherwise use none. An ungrounded Ted assertion still has relation protected_assertion: relation describes what the prose asserts, while grounding and violation classification prove that the assertion is unauthorized. Select adjudication offsets and relations only; never calculate or return exact-text hashes because Python derives them from the immutable Writer bytes. Independently detect explicit, implicit, and pronoun-only action, movement, dialogue, thought, emotion, bodily state, consent, decision, or response. An assertion owned by character:ted must equal one exact current ingress claim to be accepted; an NPC action may affect, address, observe, or reference Ted without inventing his response. Reject ambiguous mixed ownership instead of rewriting or guessing. Referenced inactive characters do not become active cast. Judge plan/beat realization, evidence, privacy, knowledge, cast, continuity, and stopping boundary, then choose exactly one schema-defined decision branch: accepted turn, concerning turn, rejected/inconclusive/error turn, or accepted scene summary. Accepted/good branches intentionally contain no issue owner or reason-code fields; diagnostic branches require their dedicated diagnostic fields. Every output local key, including each segment_key and final item_key, must match lower snake case `[a-z][a-z0-9_]{0,95}` with no colon; every story_segment_keys reference must copy one exact story_material_assertion segment key. Stable identities such as world_id, branch_id, accepted_turn_id, package_id, sequence_id, and event_id are not local keys and retain their supplied identity syntax. Every populated final field cites exact story_material_assertion segment keys and repeats their role and claim unions. Presentation-only segment keys must not appear anywhere in complete_final_sequence, creator review reasons, event_record, or persistence. For each final-sequence item, field_scopes must contain exactly realized_event and resulting_state, plus valid_deepseek_additions, knowledge_changes, or material_changes only when that corresponding array contains at least one value; never emit a field scope for an empty optional array. The same mutually-exclusive-role rule applies independently to each final-sequence item: one character ID may occur in exactly one of its seven role arrays. Never union an action segment and a dialogue, state, or non-owning segment into one final item when that would repeat a character across role arrays; split them into separate causally ordered final-sequence items and keep each item's cited segments and roles exact. Event meaning and persistence proposals derive only from validated final fields; diagnostic and presentation-only spans cannot enter final fields, events, edits, memory, accepted context, or commits. Return only event identity, ordered final-item keys, and protected-claim provenance in event_record; the active schema intentionally has no event summary because Python derives it exactly from the accepted realized_event fields. Python alone creates event text, edit bookkeeping, and commits. Never generate, revise, replace, continue, normalize, or summarize away the Writer prose. Provider conversation is not authority. The 32-call ceiling is terminal. No retry, fallback, Fast mode, or hidden repair."""

VALIDATOR_STABLE_INSTRUCTIONS += """

Naming an unresolved future option or explicitly leaving the next choice with Ted does not itself assert that Ted makes a decision, gives consent, or performs an action. A source-grounded handoff such as `the doorway remained Ted's to cross` may be presentation_only nonpersistent_spatial_phrasing with source_grounded_public_state when the exact current ingress source establishes both his unchanged public position and that the next choice remains open. Do not label that wording consent_or_decision, protected_user_behavior, relocation_or_durable_position, or stopping_boundary_violation. A stopping-boundary violation occurs only when the prose supplies or resolves the protected choice, commits Ted to an outcome, or continues causally beyond the point that required his unsupplied choice. `Ted crossed the doorway`, `Ted chose to enter`, or narration that proceeds as though he did remains prohibited. This allowance is noncanonical presentation and creates no accepted protected-user authority, event, memory, material state, summary, or persistence."""

VALIDATOR_STABLE_INSTRUCTIONS += """

For every diagnostic span, derive the protected relation from that span's roles exactly. Use protected_assertion if and only if character:ted is an action_owner, state_owner, or speaker. Use none if and only if character:ted is absent from all seven role arrays. Use neutral_presentation_reference only for grounded narration where character:ted appears only in referenced_ids, no character owns an action, state, or speech predicate, and both owner and claim arrays are empty. Otherwise, affected_by_npc, addressed_by_npc, observed_by_npc, and referenced_only_by_npc require character:ted in affected_ids, addressed_ids, observing_ids, and referenced_ids respectively, with character:ted in no other non-owning role. For those four NPC-owned non-owning relations, npc_assertion_owner_ids must equal all non-Ted action_owner, state_owner, and speaker IDs in that exact span and must be nonempty. If one span cannot satisfy this one-to-one relation and role mapping, split it at an exact text boundary before returning the diagnostic result."""

VALIDATOR_STABLE_INSTRUCTIONS += """

All canonical and diagnostic offsets are zero-based Python Unicode-codepoint indices into the exact writer_story_text, not JSON bytes, escaped text, display columns, or an imagined terminator. For a Writer text of N codepoints, every span must satisfy 0 <= output_start < output_end <= N. Gap-free coverage means the first output_start is 0, every later output_start equals the prior output_end, and the final output_end equals N exactly. Recheck the supplied Writer mechanical envelope's codepoint_count before returning; never add one for a closing quote, newline, or end marker that is not present in writer_story_text. Keep Python's strict span validation authoritative and return no guessed or out-of-bounds offset."""

VALIDATOR_STABLE_INSTRUCTIONS += """

Every canonical realization span and every diagnostic span must cover at least one non-whitespace character of the immutable writer_story_text. Never return a standalone span containing only spaces, tabs, line breaks, or other formatting separators. Preserve ordered, non-overlapping, gap-free coverage by attaching each separator to the immediately preceding or following substantive semantic span; do not discard, normalize, or invent characters."""

VALIDATOR_STABLE_INSTRUCTIONS += """

For every canonical and diagnostic span, the semantic kind and assertion-owner roles must satisfy the exact closed matrix. Kind action requires at least one action_owner and no state_owner or speaker. Kind dialogue requires exactly one speaker and no action_owner or state_owner. Kind private_state or consent_or_decision requires at least one state_owner and no action_owner or speaker. Kind narration requires no action_owner, state_owner, or speaker. Narration normally contains at least one active character in affected_ids, addressed_ids, observing_ids, or referenced_ids. The sole empty-ledger exception is actorless ambient narration: on an accepted or concern branch it must be presentation_only nonpersistent_atmosphere; on a rejected branch it may remain a grounded diagnostic narration span. It is metadata-only and must not appear in final fields, events, memory, persistence, summary authority, or canon. Never label a state-owned description as narration. If exact text contains more than one of these semantic kinds, split it at exact codepoint boundaries until every span satisfies exactly one row."""


READER_STABLE_INSTRUCTIONS = """You are CERA's independent minimum-quality Reader checkpoint. Judge the exact immutable candidate as a complete reader-facing response after the Semantic Validator has already accepted hard authority and continuity. Reject only a severe visible quality failure: incoherent or unreadable prose, severe repetition or mechanical phrasing, a missing central scene event, clearly wrong character logic or voice, premature scene closure, or development materially below the selected depth. Minor style preference, harmless brevity, soft noncanonical detail, ordinary wording variation, and a merely imperfect sentence are accepted. Do not reclassify semantic authority. Return exactly one schema-defined decision branch plus the four scores. The accepted branch contains only its schema version and accepted verdict; it has no reason_codes or issues fields. The rejected branch requires nonempty typed reason_codes and nonempty exact issue spans for a severe visible failure. The inconclusive branch requires nonempty typed reason_codes, has no issues field, is not a Writer failure, and cannot authorize recall. Every reason_codes value and every issue_code is a local key and must match lower snake case `[a-z][a-z0-9_]{0,95}` exactly. Select issue offsets only; never calculate or return full-story or issue hashes because Python derives them from the immutable Writer bytes. Never rewrite, repair, continue, quote a replacement, create canon, or override a hard Python or Semantic Validator failure. Provider conversation is not story authority. No retry, fallback, or post-generation normalization."""


PLANNER_STABLE_INSTRUCTIONS += """

selected_character_ids contains active NPC participants only and must never contain character:ted. An NPC may address, affect, observe, or reference Ted without selecting him. Any other nonselected character named by a beat must be listed in omitted_character_ids and may appear only in referenced_ids; that does not activate the character, give them scene knowledge, or permit action, state, speech, observation, address, or effect roles."""

VALIDATOR_STABLE_INSTRUCTIONS += """

A Planner-authorized omitted character may appear in Validator roles only in referenced_ids. This is a reference-only allowance, not active cast: never assign that character action, state, speech, affected, addressed, or observing roles, and never infer their scene knowledge, presence, or participation. Do not reject an exact authorized offscreen reference merely because the character is outside active cast."""


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
    package_id: str,
    world_id: str,
    branch_id: str,
    candidate_id: str | None,
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
    realization_boundary: WriterRealizationBoundaryV1 | None = None,
) -> tuple[str, tuple[PromptComponentUsageV1, ...]]:
    boundary = realization_boundary or WriterRealizationBoundaryV1.default()
    boundary_payload = to_primitive(boundary)
    boundary_payload["continuity_significant_classes"] = [
        value.value for value in ACTIVE_VALIDATOR_WRITER_HARD_CLASSES
    ]
    request = {
        "schema_version": "cera.continuous_validator_request.v13",
        "task_mode": task_mode.value,
        "package_id": package_id,
        "world_id": world_id,
        "branch_id": branch_id,
        "candidate_id": candidate_id,
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
        "writer_realization_boundary": boundary_payload,
        "authority_note": (
            "Only supplied accepted turn IDs and pairs authorize a scene summary; "
            "rejected or remembered candidates are non-authoritative."
        ),
    }
    request_bytes = canonical_bytes(request)
    prompt = (
        VALIDATOR_STABLE_INSTRUCTIONS
        + "\n\n"
        + VALIDATOR_IDENTITY_INSTRUCTIONS
        + "\n\n[VALIDATOR REQUEST]\n"
        + request_bytes.decode("utf-8")
    )
    return prompt, (
        _usage(
            "stable_instructions",
            (VALIDATOR_STABLE_INSTRUCTIONS + "\n\n" + VALIDATOR_IDENTITY_INSTRUCTIONS).encode("utf-8"),
        ),
        _usage("current_packet", request_bytes),
    )


def compile_compact_writer_brief(
    *,
    current_user_source: str,
    planner_sequence: RichPlannerSequenceV1,
) -> CompactWriterBriefV1:
    """Project only the Planner decisions needed by the stochastic prose Writer."""

    if not isinstance(planner_sequence, RichPlannerSequenceV1):
        raise TypeError("planner_sequence must be a validated RichPlannerSequenceV1")
    if len(planner_sequence.beats) > 5:
        raise ValueError("compact Writer Brief supports at most five causal beats")

    mandatory = tuple(
        (
            f"Beat {index}: {beat.observable_action_or_dialogue_direction} "
            f"Private-state direction: {beat.private_state_guidance} "
            f"Material continuity: {beat.physical_material_continuity}"
        )
        for index, beat in enumerate(planner_sequence.beats, start=1)
    )
    if len(mandatory) == 1:
        mandatory += (
            "Result: leave the scene in this state: "
            + planner_sequence.beats[0].resulting_state,
        )

    voice_cues: list[CompactWriterVoiceCueV1] = []
    for character_id in planner_sequence.selected_character_ids:
        cues = tuple(
            dict.fromkeys(
                beat.selected_tactic
                for beat in planner_sequence.beats
                if character_id in beat.roles.assertion_owner_ids
            )
        )[:4]
        if not cues:
            cues = (
                "Keep this active character consistent with the scene objective; "
                "do not invent a new motive or private fact.",
            )
        voice_cues.append(
            CompactWriterVoiceCueV1(
                schema_version=CompactWriterVoiceCueV1.SCHEMA_VERSION,
                character_id=character_id,
                cues=cues,
            )
        )

    continuity_boundaries = tuple(
        dict.fromkeys(
            "Preserve this durable continuity boundary: "
            + beat.physical_material_continuity
            for beat in planner_sequence.beats
        )
    )
    active_cast_text = ", ".join(planner_sequence.selected_character_ids)
    hard_boundaries = (
        "Do not invent, paraphrase, extend, or imply Ted's action, dialogue, "
        "thought, emotion, bodily state, consent, decision, movement, or response "
        "beyond the exact current user source.",
        "Only these active characters may participate in the visible scene: "
        + active_cast_text
        + ". A reference to another character does not make them active.",
        "Do not create a consequential unsupported event, object, task, relocation, "
        "relationship change, knowledge change, private fact, or material state.",
        "Realize every mandatory causal beat substantially and do not reverse, "
        "replace, or skip its central event.",
        *continuity_boundaries,
    )
    return CompactWriterBriefV1(
        schema_version=CompactWriterBriefV1.SCHEMA_VERSION,
        current_user_source=current_user_source,
        active_cast=planner_sequence.selected_character_ids,
        scene_objective=planner_sequence.beats[0].immediate_goal,
        mandatory_causal_beats=mandatory,
        active_character_voice_cues=tuple(voice_cues),
        hard_boundaries=hard_boundaries,
        stopping_boundary=planner_sequence.final_stop_state,
        presentation_freedom=(
            "Use natural wording, pacing, sentence order, dialogue texture, "
            "micro-actions, gaze, expression, posture, cadence, ambient texture, "
            "generic incidental props, and other nonpersistent scene-local color. "
            "These details must remain harmless and must not become causal or durable facts. "
            "Generic local staging may give concrete prose shape to an authorized beat, "
            "but it must not add another task or outcome, retained inventory or location, "
            "contradiction, or future continuity obligation."
        ),
    )


def _compact_writer_recall_feedback(
    directive: WriterRecallDirectiveV1 | None,
) -> dict[str, Any]:
    if directive is None:
        return {}
    return {
        "reason_codes": directive.reason_codes,
        "offending_spans": tuple(
            {
                "exact_text": value.exact_text,
                "failure_classes": tuple(
                    item.value for item in value.prohibited_detail_classes
                ),
            }
            for value in directive.offending_spans
        ),
        "instruction": (
            "Create a fresh candidate from the unchanged brief. Do not continue, "
            "patch, quote, or merge the rejected candidate."
        ),
    }


def build_continuous_composer_prompt(
    *,
    current_user_source: str,
    ingress_source_units: tuple[dict[str, Any], ...],
    planner_sequence: RichPlannerSequenceV1,
    character_summaries: Iterable[CharacterSummaryEnvelopeV1] = (),
    protected_user_claim_manifest: tuple[dict[str, Any], ...] = (),
    accepted_session_projections: tuple[dict[str, Any], ...] = (),
    realization_boundary: WriterRealizationBoundaryV1 | None = None,
    writer_recall_directive: WriterRecallDirectiveV1 | None = None,
) -> tuple[str, tuple[PromptComponentUsageV1, ...]]:
    boundary = realization_boundary or WriterRealizationBoundaryV1.default()
    summaries = tuple(character_summaries)
    brief = compile_compact_writer_brief(
        current_user_source=current_user_source,
        planner_sequence=planner_sequence,
    )
    brief_bytes = canonical_bytes(brief)
    recall_bytes = canonical_bytes(
        _compact_writer_recall_feedback(writer_recall_directive)
    )
    frozen_authority_package_sha256 = continuous_writer_authority_package_sha256(
        current_user_source=current_user_source,
        ingress_source_units=ingress_source_units,
        planner_sequence=planner_sequence,
        character_summaries=summaries,
        protected_user_claim_manifest=protected_user_claim_manifest,
        accepted_session_projections=accepted_session_projections,
        realization_boundary=boundary,
    )
    if (
        writer_recall_directive is not None
        and writer_recall_directive.frozen_authority_package_sha256
        != frozen_authority_package_sha256
    ):
        raise ValueError(
            "Writer recall directive changed the frozen authority package"
        )
    prompt = (
        "[COMPACT WRITER BRIEF]\n"
        + brief_bytes.decode("utf-8")
        + "\n\n[BOUNDED NON-AUTHORITATIVE RECALL FEEDBACK]\n"
        + recall_bytes.decode("utf-8")
        + "\n\nWrite one complete, natural, presentation-neutral story response in story_text. "
        + "Substantially realize the mandatory causal beats and stop at the stated boundary. "
        + "Use the presentation freedom instead of copying the brief like a screenplay. "
        + "Harmless local variation is welcome; hard boundaries are not optional. "
        + "Return prose only through the supplied Writer output schema. Do not return analysis, "
        + "labels, IDs, hashes, offsets, evidence, events, memory, persistence, or acceptance."
    )
    return prompt, (
        _usage("compact_writer_brief", brief_bytes),
        _usage("writer_recall_feedback", recall_bytes),
    )


def writer_beat_realization_constraints(
    planner_sequence: RichPlannerSequenceV1,
) -> dict[str, Any]:
    """Project existing Planner constraints without interpreting or expanding them."""

    if not isinstance(planner_sequence, RichPlannerSequenceV1):
        raise TypeError("planner_sequence must be a validated RichPlannerSequenceV1")
    return {
        "schema_version": WRITER_BEAT_REALIZATION_CONSTRAINTS_VERSION,
        "sequence_id": planner_sequence.sequence_id,
        "beats": tuple(
            {
                "beat_key": beat.beat_key,
                "roles": to_primitive(beat.roles),
                "observable_action_or_dialogue_direction": (
                    beat.observable_action_or_dialogue_direction
                ),
                "private_state_guidance": beat.private_state_guidance,
                "physical_material_continuity": beat.physical_material_continuity,
                "deepseek_realization_space": beat.deepseek_realization_space,
                "protected_user_allowance": to_primitive(
                    beat.protected_user_allowance
                ),
            }
            for beat in planner_sequence.beats
        ),
        "final_stop_state": planner_sequence.final_stop_state,
    }


def continuous_writer_authority_package_sha256(
    *,
    current_user_source: str,
    ingress_source_units: tuple[dict[str, Any], ...],
    planner_sequence: RichPlannerSequenceV1,
    character_summaries: Iterable[CharacterSummaryEnvelopeV1] = (),
    protected_user_claim_manifest: tuple[dict[str, Any], ...] = (),
    accepted_session_projections: tuple[dict[str, Any], ...] = (),
    realization_boundary: WriterRealizationBoundaryV1 | None = None,
) -> str:
    """Hash only immutable Writer authority; recall feedback is excluded."""

    boundary = realization_boundary or WriterRealizationBoundaryV1.default()
    beat_constraints = writer_beat_realization_constraints(planner_sequence)
    return canonical_sha256(
        {
            "current_user_source": current_user_source,
            "ingress_source_units": ingress_source_units,
            "planner_sequence": planner_sequence,
            "character_summaries": tuple(character_summaries),
            "protected_user_claim_manifest": protected_user_claim_manifest,
            "accepted_session_projections": accepted_session_projections,
            "writer_realization_boundary": boundary,
            "writer_beat_realization_constraints": beat_constraints,
        }
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
