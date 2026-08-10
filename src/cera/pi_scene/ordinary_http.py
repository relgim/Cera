"""Pure OpenAI-compatible projections for ordinary Pi Scene reviews."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from cera.errors import StateConflictError
from cera.serialization import to_primitive

from .contracts import RecordingStatus, SceneRoute
from .creator_trace import cognition_creator_trace
from .http_contracts import PI_SCENE_ADULT_MODEL, PI_SCENE_ORDINARY_MODEL, PI_SCENE_PROFILE
from .review_store import LeanReviewRecordV1, LeanReviewState


def ordinary_review_payload(
    review: LeanReviewRecordV1,
    *,
    recording_status: str | None,
) -> dict[str, Any]:
    """Project one review after its recording status was read by the adapter."""

    candidate = review.candidate
    validation = review.semantic_validation
    validation_payload = (
        None
        if validation is None
        else {
            "binding_sha256": validation.binding_sha256,
            "verdict": validation.verdict.verdict.value,
            "automatic_repair_eligible": validation.verdict.automatic_repair_eligible,
            "conflict": (
                None
                if validation.verdict.conflict is None
                else to_primitive(validation.verdict.conflict)
            ),
            "review_flags": [to_primitive(value) for value in validation.verdict.review_flags],
        }
    )
    rejected = validation is not None and validation.verdict.verdict.value == "reject"
    provisional_canon = (
        review.accepted_receipt is not None
        and review.accepted_receipt.creator_action == "provisional_accept"
    )
    return {
        "schema_version": "cera.pi_scene.review.v1",
        "review_id": review.review_id,
        "state": review.state,
        "provisional": review.state == LeanReviewState.REVIEW_READY,
        "route": candidate.route.value,
        "story_text": candidate.story_text,
        "candidate_id": candidate.candidate_id,
        "candidate_sha256": candidate.candidate_sha256,
        "primary_authority_kind": candidate.primary_authority_kind,
        "primary_authority_sha256": candidate.primary_authority_sha256,
        "warnings": [to_primitive(value) for value in candidate.warnings],
        "warnings_block_accept": False,
        "recording_status": recording_status,
        "story_state_committed": review.accepted_receipt is not None,
        "canon_status": (
            "provisional"
            if provisional_canon
            else "accepted"
            if review.accepted_receipt is not None
            else "unaccepted"
        ),
        "semantic_validation": validation_payload,
        "request_controls": (
            None
            if review.turn_input.request_controls is None
            else to_primitive(review.turn_input.request_controls)
        ),
        "creator_guidance": (
            None if review.creator_guidance is None else to_primitive(review.creator_guidance)
        ),
        "accept_enabled": review.state == LeanReviewState.REVIEW_READY and not rejected,
        "provisional_accept_enabled": (review.state == LeanReviewState.REVIEW_READY and rejected),
        "decline_enabled": review.state == LeanReviewState.REVIEW_READY,
        "regenerate_enabled": review.state == LeanReviewState.REVIEW_READY,
        "replan_enabled": (
            review.state == LeanReviewState.REVIEW_READY and candidate.route is SceneRoute.ORDINARY
        ),
        "repair_recording_enabled": recording_status
        in {
            RecordingStatus.PROJECTION_PENDING.value,
            RecordingStatus.PENDING_REPAIR.value,
        },
        "provider_operations": {
            "planner": review.result.planner_provider_operations,
            "writer": review.result.writer_provider_operations,
            "recorder": (
                0
                if review.recording_attempt is None
                else review.recording_attempt.provider_operations
            ),
        },
    }


def ordinary_completion_payload(
    review: LeanReviewRecordV1,
    *,
    provider_attempts: Sequence[LeanReviewRecordV1] | None = None,
) -> dict[str, Any]:
    """Project one immutable candidate/review as a chat completion."""

    candidate = review.candidate
    committed = review.accepted_receipt is not None
    validation = review.semantic_validation
    rejected = validation is not None and validation.verdict.verdict.value == "reject"
    recording_status = (
        None if review.recording_attempt is None else review.recording_attempt.status.value
    )
    provisional_canon = (
        review.accepted_receipt is not None
        and review.accepted_receipt.creator_action == "provisional_accept"
    )
    review_status = (
        "accepted" if committed else "validation_rejected" if rejected else "review_ready"
    )
    attempts = (review,) if provider_attempts is None else tuple(provider_attempts)
    if not attempts or attempts[-1] != review:
        raise StateConflictError("Pi Scene provider-attempt accounting lost its terminal review")
    attempt_payloads: list[dict[str, Any]] = [
        {
            "attempt_number": index,
            "candidate_id": attempt.candidate.candidate_id,
            "disposition": (
                "semantic_pass"
                if attempt.semantic_validation is not None
                and attempt.semantic_validation.verdict.verdict.value == "pass"
                else "semantic_rejected"
            ),
            "provider_operations": {
                "planner": attempt.result.planner_provider_operations,
                "writer": attempt.result.writer_provider_operations,
                "validator": 1 if attempt.semantic_validation is not None else 0,
            },
        }
        for index, attempt in enumerate(attempts, start=1)
    ]
    provider_operations = {
        role: sum(
            attempt_payload["provider_operations"][role] for attempt_payload in attempt_payloads
        )
        for role in ("planner", "writer", "validator")
    }
    provider_operations["recorder"] = (
        0
        if not committed or review.recording_attempt is None
        else review.recording_attempt.provider_operations
    )
    cera_payload: dict[str, Any] = {
        "profile_id": PI_SCENE_PROFILE,
        "route_mode": candidate.route.value,
        "provisional": not committed,
        "status": review_status,
        "story_state_committed": committed,
        "canon_status": ("provisional" if provisional_canon else "accepted" if committed else None),
        "provisional_review_id": None if committed else review.review_id,
        "review_url": f"/v1/cera/reviews/{review.review_id}",
        "candidate_id": candidate.candidate_id,
        "generation": candidate.generation,
        "warnings": [to_primitive(value) for value in candidate.warnings],
        "warnings_block_accept": False,
        "accepted_turn_id": (
            None if review.accepted_receipt is None else review.accepted_receipt.accepted_turn_id
        ),
        "accepted_receipt_sha256": (
            None if review.accepted_receipt is None else review.accepted_receipt.receipt_sha256
        ),
        "recording_status": recording_status,
        "semantic_validation": (
            None
            if validation is None
            else {
                "binding_sha256": validation.binding_sha256,
                "verdict": validation.verdict.verdict.value,
                "automatic_repair_eligible": validation.verdict.automatic_repair_eligible,
                "conflict": (
                    None
                    if validation.verdict.conflict is None
                    else to_primitive(validation.verdict.conflict)
                ),
                "review_flags": [to_primitive(value) for value in validation.verdict.review_flags],
            }
        ),
        "request_controls": (
            None
            if review.turn_input.request_controls is None
            else to_primitive(review.turn_input.request_controls)
        ),
        "creator_guidance": (
            None if review.creator_guidance is None else to_primitive(review.creator_guidance)
        ),
        "operational_warnings": [],
        "provider_attempts": attempt_payloads,
        "provider_operations": provider_operations,
    }
    response: dict[str, Any] = {
        "id": f"chatcmpl-cera-{candidate.candidate_sha256[:24]}",
        "object": "chat.completion",
        "created": review.created_unix_seconds,
        "model": (
            PI_SCENE_ORDINARY_MODEL
            if candidate.route is SceneRoute.ORDINARY
            else PI_SCENE_ADULT_MODEL
        ),
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": candidate.story_text},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        "cera": cera_payload,
    }
    if candidate.primary_authority_kind == "codex_cognition_plan":
        trace = cognition_creator_trace(candidate.primary_authority_json)
        applications = trace.pop("autonomy_application")
        controls = review.turn_input.request_controls
        trace["autonomy"] = {
            "mode": None if controls is None else controls.character_autonomy,
            "applications": applications,
        }
        trace["validation"] = safe_semantic_validation_trace(validation)
        trace["recording"] = {
            "status": recording_status,
            "recorder_required": committed,
            "projection_status": None,
            "protected_record_status": None,
        }
        trace["provider_operations"] = dict(cera_payload["provider_operations"])
        cera_payload["creator_trace"] = trace
        cera_payload.update(trace)
    return response


def safe_semantic_validation_trace(validation: Any) -> dict[str, Any] | None:
    if validation is None:
        return None
    conflict = validation.verdict.conflict
    safe_conflict = None
    if conflict is not None:
        safe_conflict = {
            "conflict_class": conflict.conflict_class.value,
            "decision_key": conflict.decision_key,
            "concise_explanation": conflict.concise_explanation,
        }
    return {
        "role": "luna_semantic_validator",
        "verdict": validation.verdict.verdict.value,
        "binding_sha256": validation.binding_sha256,
        "conflict": safe_conflict,
    }


def attach_debug_path(response: dict[str, Any], debug_path: str) -> None:
    cera_payload = response.get("cera")
    if not isinstance(cera_payload, dict):
        raise StateConflictError("CERA response lost its metadata object")
    cera_payload["debug_log_path"] = debug_path
    trace = cera_payload.get("creator_trace")
    if isinstance(trace, dict):
        trace["debug_log_path"] = debug_path
