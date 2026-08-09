"""Deterministic bridge from a cognition candidate to Luna validation input."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from cera.cognition import CognitionPlanV1
from cera.errors import ContractValidationError
from cera.schema import from_mapping
from cera.semantic_validation import (
    SemanticValidationCustodyV1,
    SemanticValidationRequestV1,
    ValidationEvidenceV1,
)
from cera.serialization import canonical_sha256, text_sha256

from .contracts import LeanCandidateV1, SceneRoute


def build_semantic_validation_input(
    *,
    candidate: LeanCandidateV1,
    current_state: Mapping[str, Any],
    validation_evidence: Sequence[Mapping[str, Any]],
) -> tuple[SemanticValidationRequestV1, SemanticValidationCustodyV1]:
    if (
        candidate.route is not SceneRoute.ORDINARY
        or candidate.primary_authority_kind != "codex_cognition_plan"
    ):
        raise ContractValidationError("semantic validation input requires a cognition candidate")
    try:
        authority = json.loads(candidate.primary_authority_json)
    except json.JSONDecodeError as exc:
        raise ContractValidationError("cognition candidate authority is invalid") from exc
    if not isinstance(authority, Mapping):
        raise ContractValidationError("cognition candidate authority is not an object")
    plan = from_mapping(CognitionPlanV1, authority)
    public_state = current_state.get("public_scene_state")
    if not isinstance(public_state, str) or not public_state.strip():
        raise ContractValidationError("semantic validation requires public scene state")
    boundaries = _text_tuple(current_state.get("hard_boundaries", ()))
    boundaries = tuple(
        dict.fromkeys(
            (
                "Do not invent Ted speech or dialogue.",
                "Do not invent Ted thoughts or feelings.",
                *boundaries,
            )
        )
    )
    evidence = tuple(from_mapping(ValidationEvidenceV1, value) for value in validation_evidence)
    request = SemanticValidationRequestV1(
        schema_version=SemanticValidationRequestV1.SCHEMA_VERSION,
        cognition_plan=plan,
        exact_current_source=candidate.exact_user_source,
        exact_candidate_prose=candidate.story_text,
        current_public_state=public_state,
        hard_boundaries=boundaries,
        selected_evidence=evidence,
    )
    custody = SemanticValidationCustodyV1(
        request_id=f"request:validate-{candidate.candidate_sha256[:24]}",
        candidate_id=candidate.candidate_id,
        world_id=candidate.world_id,
        branch_id=candidate.branch_id,
        accepted_head_sha256=candidate.accepted_head_before_sha256,
        cognition_plan_sha256=canonical_sha256(plan),
        candidate_prose_sha256=text_sha256(candidate.story_text),
        validation_request_sha256=canonical_sha256(request),
    )
    return request, custody


def _text_tuple(value: object) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple)):
        raise ContractValidationError("hard boundaries must be an ordered list")
    output = tuple(value)
    if any(not isinstance(item, str) or not item.strip() for item in output):
        raise ContractValidationError("hard boundaries contain invalid text")
    return output
