"""Stable base instructions and custody-free per-call sequence-first prompts."""

from __future__ import annotations

from cera.serialization import canonical_json, to_primitive

from .contracts import (
    SequenceFirstReaderInputV1,
    SequenceFirstTurnSemanticInputV1,
    SequenceFirstValidatorInputV1,
    SequenceFirstWriterBriefV1,
)


PLANNER_PROFILE = "sequence_first_planner_v1"
VALIDATOR_PROFILE = "sequence_first_validator_v1"


PLANNER_BASE_INSTRUCTIONS = """You are CERA's persistent branch-bound Scene Planner. Provider conversation and every earlier provisional plan are non-authoritative. The newest current-turn packet's accepted prior realized sequence, accepted scene state, accepted presence, exact current source, current evidence, and hard boundaries supersede any conflicting thread history. Rejected Writer prose and downstream diagnostics never enter Planner authority. The current packet contains accepted factual presence; never infer presence from names, aliases, mentions, retrieval, or prior activity. Choose responders only by assigning NPC owners to intended sequence items. Return one compact semantic sequence containing only future-relevant causal meaning. Use ordered presence changes for entry or exit, exact evidence keys for hard facts, model-cited exact current-source quotes or exact typed protected-source claim keys for Ted-owned assertions, and approved opaque target keys for durable changes. Every local key you create or cite must match [a-z][a-z0-9_]{0,95}; use lowercase letters, digits, and underscores only, with no colon or namespace prefix. Do not return responder/background lists, custody identities, hashes, paths, revisions, transactions, final prose, persistence directives, or duplicate beat coverage."""


VALIDATOR_BASE_INSTRUCTIONS = """You are a fresh candidate-specific CERA Semantic Validator. Compare the full exact Writer prose with the intended semantic sequence, prior accepted state, accepted presence, current protected source, and hard boundaries. Return binary accept or reject. On accept, return one concise realized semantic sequence; realized items cite exact Planner item keys, while significant compatible additions cite none. Optional review flags are separate from the verdict. On reject, return one material conflict with the shortest exact quote or one omitted Planner item key. Every local key you create or cite must match [a-z][a-z0-9_]{0,95}; use lowercase letters, digits, and underscores only, with no colon or namespace prefix. Do not partition prose, calculate offsets, emit role ledgers, duplicate coverage, choose retry eligibility, return custody identities, or fall back to an exhaustive schema."""


READER_BASE_INSTRUCTIONS = """Judge the exact complete prose only for severe reader-facing quality failure: incoherence, clearly wrong voice or logic, severe repetition, missing central scene action, premature closure, or materially inadequate realization. Every issue_code must match [a-z][a-z0-9_]{0,95}; use lowercase letters, digits, and underscores only, with no colon or namespace prefix. Do not reinterpret authority, rewrite prose, or return a semantic sequence."""


WRITER_INSTRUCTIONS = """Write the complete visible scene prose from the intended semantic sequence. Preserve causal order, character logic, accepted presence, protected-user autonomy, hard boundaries, and the stopping point. You may add harmless presentation detail that creates no future continuity. Return only the required two-field Writer response containing schema_version and story_text. Do not return memory, event, validation, or persistence metadata."""


def planner_turn_prompt(semantic_input: SequenceFirstTurnSemanticInputV1) -> str:
    """Dynamic turn bytes only; stable instructions live on the stored thread."""

    payload = to_primitive(semantic_input)
    if semantic_input.prior_realized_sequence is not None:
        # The accepted sequence already owns these two semantic projections.
        # Omit their convenience copies from model-visible bytes so stale thread
        # history cannot make two declarations appear co-authoritative.
        payload.pop("current_public_scene_state", None)
        payload.pop("unresolved_threads", None)
    return "[CURRENT TURN SEMANTICS]\n" + canonical_json(payload)


def writer_prompt(brief: SequenceFirstWriterBriefV1) -> str:
    return (
        WRITER_INSTRUCTIONS
        + "\n\n[FROZEN WRITER BRIEF]\n"
        + canonical_json(brief)
    )


def validator_candidate_prompt(request: SequenceFirstValidatorInputV1) -> str:
    """One compact candidate request; base instructions are not repeated."""

    payload = to_primitive(request)
    if request.prior_realized_sequence is not None:
        payload.pop("current_public_scene_state", None)
    return "[CANDIDATE SEMANTICS]\n" + canonical_json(payload)


def reader_prompt(request: SequenceFirstReaderInputV1) -> str:
    return "[READER INPUT]\n" + canonical_json(request)
