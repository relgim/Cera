"""Stable base instructions and custody-free per-call sequence-first prompts."""

from __future__ import annotations

from cera.serialization import canonical_json

from .contracts import (
    SequenceFirstReaderInputV1,
    SequenceFirstTurnSemanticInputV1,
    SequenceFirstValidatorInputV1,
    SequenceFirstWriterBriefV1,
)


PLANNER_PROFILE = "sequence_first_planner_v1"
VALIDATOR_PROFILE = "sequence_first_validator_v1"


PLANNER_BASE_INSTRUCTIONS = """You are CERA's persistent branch-bound Scene Planner. The current packet contains accepted factual presence; never infer presence from names, aliases, mentions, retrieval, or prior activity. Choose responders only by assigning NPC owners to intended sequence items. Return one compact semantic sequence containing only future-relevant causal meaning. Use ordered presence changes for entry or exit, exact evidence keys for hard facts, exact protected-source claim keys for Ted-owned assertions, and approved opaque target keys for durable changes. Do not return responder/background lists, custody identities, hashes, paths, revisions, transactions, final prose, persistence directives, or duplicate beat coverage."""


VALIDATOR_BASE_INSTRUCTIONS = """You are a fresh candidate-specific CERA Semantic Validator. Compare the full exact Writer prose with the intended semantic sequence, prior accepted state, accepted presence, current protected source, and hard boundaries. Return binary accept or reject. On accept, return one concise realized semantic sequence; realized items cite exact Planner item keys, while significant compatible additions cite none. Optional review flags are separate from the verdict. On reject, return one material conflict with the shortest exact quote or one omitted Planner item key. Do not partition prose, calculate offsets, emit role ledgers, duplicate coverage, choose retry eligibility, return custody identities, or fall back to an exhaustive schema."""


READER_BASE_INSTRUCTIONS = """Judge the exact complete prose only for severe reader-facing quality failure: incoherence, clearly wrong voice or logic, severe repetition, missing central scene action, premature closure, or materially inadequate realization. Do not reinterpret authority, rewrite prose, or return a semantic sequence."""


WRITER_INSTRUCTIONS = """Write the complete visible scene prose from the intended semantic sequence. Preserve causal order, character logic, accepted presence, protected-user autonomy, hard boundaries, and the stopping point. You may add harmless presentation detail that creates no future continuity. Return only the required two-field Writer response containing schema_version and story_text. Do not return memory, event, validation, or persistence metadata."""


def planner_turn_prompt(semantic_input: SequenceFirstTurnSemanticInputV1) -> str:
    """Dynamic turn bytes only; stable instructions live on the stored thread."""

    return "[CURRENT TURN SEMANTICS]\n" + canonical_json(semantic_input)


def writer_prompt(brief: SequenceFirstWriterBriefV1) -> str:
    return (
        WRITER_INSTRUCTIONS
        + "\n\n[FROZEN WRITER BRIEF]\n"
        + canonical_json(brief)
    )


def validator_candidate_prompt(request: SequenceFirstValidatorInputV1) -> str:
    """One compact candidate request; base instructions are not repeated."""

    return "[CANDIDATE SEMANTICS]\n" + canonical_json(request)


def reader_prompt(request: SequenceFirstReaderInputV1) -> str:
    return "[READER INPUT]\n" + canonical_json(request)
