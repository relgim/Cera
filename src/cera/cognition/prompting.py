"""Stable and turn-local prompts for the full-model cognition planner."""

from __future__ import annotations

from cera.sequence_first.prompting import PLANNER_BASE_INSTRUCTIONS
from cera.serialization import canonical_json

from .contracts import CognitionTurnContextV1

COGNITION_PLANNER_PROFILE = "cera_full_model_cognition_planner_v5"

COGNITION_PLANNER_BASE_INSTRUCTIONS = (
    PLANNER_BASE_INSTRUCTIONS
    + " "
    + "Use get_turn_context as the default bounded retrieval call. Its returned "
    "character dossiers are complete for this request: never call "
    "get_character_context for a character ID it already returned. Expand only "
    "for a specific unresolved gap with narrow search_evidence, get_exact_record, "
    "relationship, memory, thread, voice, or craft context. A hard factual "
    "decision should fetch its exact record when available. Call get_exact_record "
    "only with an exact record_id returned by a prior successful search_evidence "
    "call in this operation. Cite only evidence "
    "references actually returned during this operation; they expire with this "
    "request. A truncated or omitted result is uncertainty, not proof that a "
    "character lacks knowledge. "
    + " "
    + "Return one cognition plan that wraps the ordered sequence with compact, "
    "auditable material-decision records. For each materially relevant actor, "
    "bind the exact source authority, build an observer frame, retrieve exact "
    "evidence when needed, appraise literal and personal meaning, distinguish "
    "involuntary body response from conscious interpretation and subconscious "
    "pressure, apply the supplied global autonomy mode, compare only genuinely "
    "plausible alternatives, select intent, then advance candidate-local state. "
    "In observer_frame.directly_perceived, author one row per distinct perceived "
    "fact. The same source_ref may support multiple rows when concise_perception "
    "or certainty differs, but never repeat the exact same source_ref, "
    "concise_perception, and certainty row. "
    "Reevaluate remaining actors after every material action. Qualitative "
    "pressure levels are exactly none, low, moderate, high, or overwhelming; "
    "never invent decimal psychology. A decision record is required for each "
    "NPC-owned material action, dialogue intent, remote communication, durable "
    "change, ordered entry, or ordered exit. Link material sequence items to one "
    "decision with decision_item_links; one decision may authorize multiple "
    "items. Never author a Ted decision. Preserve both conscious and "
    "subconscious effects when materially relevant, but keep rationale concise "
    "and do not reveal hidden chain-of-thought. If the current message crosses "
    "into the adult logic-owner boundary, stop the ordinary sequence at the "
    "boundary and return one non-graphic route_transition; do not continue the "
    "adult sequence. Provisional dependencies must select true or false for this "
    "lineage and cite only supplied provisional IDs. Do not return custody, "
    "paths, hashes, provider metadata, final prose, or persistence instructions."
)


def cognition_turn_prompt(context: CognitionTurnContextV1) -> str:
    return "[CURRENT COGNITION TURN]\n" + canonical_json(context)
