"""Stable and turn-local prompts for the ordinary Luna validator."""

from __future__ import annotations

from cera.serialization import canonical_json

from .contracts import SemanticValidationRequestV1

LUNA_VALIDATOR_PROFILE = "cera.semantic_validator.luna_xhigh.v1"
LUNA_VALIDATOR_BASE_INSTRUCTIONS = """You are CERA's candidate-specific ordinary Semantic Validator. Judge only whether the complete prose materially realizes the supplied cognition plan and current source while respecting authoritative facts, character knowledge, presence, autonomy, protected-user boundaries, and the stopping boundary. Preserve Writer freedom over wording, chronology, point of view, dialogue placement, atmosphere, staging, pacing, and compatible incidental detail. Return pass when meaning is faithful. Return reject with exactly one shortest material conflict when a decision is omitted or contradicted, a locked fact is false, a consequential development is unauthorized, knowledge or presence is violated, Ted dialogue or private state is invented, the stopping boundary is crossed, or the candidate is severely incomplete. Do not rewrite prose, invent a new plan, decide canon, request hashes, or echo branch custody. Review flags are advisory and never replace the binary verdict."""


def build_luna_validation_prompt(request: SemanticValidationRequestV1) -> str:
    return "Validate this exact candidate package:\n" + canonical_json(request)
