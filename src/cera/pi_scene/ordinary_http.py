"""Pure OpenAI-compatible projections for ordinary Pi Scene reviews."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from cera.errors import StateConflictError
from cera.generated.ordinary_review_contracts_v2 import (
    validate_ordinary_review_checks_v1,
    validate_ordinary_review_lifecycle_v1,
    validate_ordinary_review_v2,
)
from cera.reader_validation import ReaderStatus
from cera.semantic_validation import SemanticVerdict
from cera.serialization import to_primitive

from .contracts import RecordingStatus, SceneRoute
from .creator_trace import cognition_creator_trace
from .http_contracts import PI_SCENE_ADULT_MODEL, PI_SCENE_ORDINARY_MODEL, PI_SCENE_PROFILE
from .review_lifecycle import (
    OrdinaryReviewMode,
    OrdinaryReviewPhase,
    OrdinaryValidationOwner,
)
from .review_store import LeanReviewRecordV1, LeanReviewState


def ordinary_review_payload(
    review: LeanReviewRecordV1,
    *,
    recording_status: str | None,
    provider_attempts: Sequence[LeanReviewRecordV1] | None = None,
    validation_provider_operation_attempts: (Sequence[Mapping[str, int] | None] | None) = None,
    terminal_decision: dict[str, str] | None = None,
    recording_repair_authorized: bool = False,
) -> dict[str, Any]:
    """Project one review after its recording status was read by the adapter."""

    if review.review_phase is not OrdinaryReviewPhase.LEGACY:
        return _ordinary_review_payload_v2(
            review,
            recording_status=recording_status,
            provider_attempts=provider_attempts,
            validation_provider_operation_attempts=(validation_provider_operation_attempts),
            terminal_decision=terminal_decision,
            recording_repair_authorized=recording_repair_authorized,
        )

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
    attempt_payloads, provider_operations = _provider_attempt_accounting(
        review,
        provider_attempts=provider_attempts,
        validation_provider_operation_attempts=validation_provider_operation_attempts,
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
            _safe_creator_guidance_projection(review.creator_guidance)
            if review.review_phase is not OrdinaryReviewPhase.LEGACY
            else None
            if review.creator_guidance is None
            else to_primitive(review.creator_guidance)
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
        "provider_attempts": attempt_payloads,
        "provider_operations": provider_operations,
    }


def _ordinary_review_payload_for_detached_hash(
    review: LeanReviewRecordV1,
    *,
    recording_status: str | None,
    provider_attempts: Sequence[LeanReviewRecordV1] | None,
    validation_provider_operation_attempts: (Sequence[Mapping[str, int] | None] | None),
    recording_repair_authorized: bool,
) -> dict[str, Any]:
    """Build the private null-link basis for the detached decision hash."""

    if review.review_phase is OrdinaryReviewPhase.LEGACY:
        raise StateConflictError("legacy review has no v2 detached decision hash")
    return _ordinary_review_payload_v2(
        review,
        recording_status=recording_status,
        provider_attempts=provider_attempts,
        validation_provider_operation_attempts=(validation_provider_operation_attempts),
        terminal_decision=None,
        recording_repair_authorized=recording_repair_authorized,
        validate_contract=False,
    )


def ordinary_completion_payload(
    review: LeanReviewRecordV1,
    *,
    provider_attempts: Sequence[LeanReviewRecordV1] | None = None,
    validation_provider_operation_attempts: (Sequence[Mapping[str, int] | None] | None) = None,
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
    attempt_payloads, provider_operations = _provider_attempt_accounting(
        review,
        provider_attempts=provider_attempts,
        validation_provider_operation_attempts=validation_provider_operation_attempts,
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
        "candidate_sha256": candidate.candidate_sha256,
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
            _safe_creator_guidance_projection(review.creator_guidance)
            if review.review_phase is not OrdinaryReviewPhase.LEGACY
            else None
            if review.creator_guidance is None
            else to_primitive(review.creator_guidance)
        ),
        "operational_warnings": [],
        "provider_attempts": attempt_payloads,
        "provider_operations": provider_operations,
    }
    if review.review_phase is not OrdinaryReviewPhase.LEGACY:
        projected_review = _ordinary_review_payload_v2(
            review,
            recording_status=recording_status,
            provider_attempts=provider_attempts,
            validation_provider_operation_attempts=(validation_provider_operation_attempts),
            terminal_decision=None,
            recording_repair_authorized=False,
        )
        if projected_review["state"] != "checks_pending":
            raise StateConflictError(
                "Pi Scene completion lifecycle escaped its provisional initial state"
            )
        cera_payload["review_lifecycle"] = validate_ordinary_review_lifecycle_v1(
            {
                "schema_version": "cera.pi_scene.review_lifecycle.v1",
                "review_id": review.review_id,
                "review_url": f"/v1/cera/reviews/{review.review_id}",
                "review_mode": projected_review["review_mode"],
                "state": projected_review["state"],
                "gate_status": projected_review["gate_status"],
                "checks": projected_review["checks"],
                "acceptance": projected_review["acceptance"],
                "actions": projected_review["actions"],
                "terminal_decision": None,
            }
        )
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
        trace["reader_validation"] = safe_reader_validation_trace(review.reader_validation)
        trace["python_qualification_sha256"] = (
            None
            if review.python_qualification is None
            else review.python_qualification.qualification_sha256
        )
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


def _ordinary_review_payload_v2(
    review: LeanReviewRecordV1,
    *,
    recording_status: str | None,
    provider_attempts: Sequence[LeanReviewRecordV1] | None,
    validation_provider_operation_attempts: (Sequence[Mapping[str, int] | None] | None),
    terminal_decision: dict[str, str] | None,
    recording_repair_authorized: bool,
    validate_contract: bool = True,
) -> dict[str, Any]:
    if type(recording_repair_authorized) is not bool:
        raise StateConflictError("Pi Scene recording-repair authority changed shape")
    candidate = review.candidate
    checks = _review_checks_v1(review)
    gate_status = _review_gate_status(review, checks)
    acceptance = _review_acceptance(review)
    actions = _review_actions(
        review,
        gate_status=gate_status,
        recording_repair_authorized=recording_repair_authorized,
    )
    attempt_payloads, provider_operations = _provider_attempt_accounting(
        review,
        provider_attempts=provider_attempts,
        validation_provider_operation_attempts=validation_provider_operation_attempts,
    )
    mode = _review_mode(review)
    if terminal_decision is not None and set(terminal_decision) != {
        "decision_sha256",
        "url",
    }:
        raise StateConflictError("Pi Scene terminal-decision link changed shape")
    payload = {
        "schema_version": "cera.pi_scene.review.v2",
        "review_id": review.review_id,
        "state": _public_review_state(review),
        "review_mode": mode.value,
        "route": candidate.route.value,
        "story_text": candidate.story_text,
        "candidate_id": candidate.candidate_id,
        "candidate_sha256": candidate.candidate_sha256,
        "primary_authority_kind": candidate.primary_authority_kind,
        "primary_authority_sha256": candidate.primary_authority_sha256,
        "warnings": [to_primitive(value) for value in candidate.warnings],
        "recording_status": recording_status,
        "gate_status": gate_status,
        "checks": checks,
        "acceptance": acceptance,
        "actions": actions,
        "request_controls": (
            None
            if review.turn_input.request_controls is None
            else to_primitive(review.turn_input.request_controls)
        ),
        "creator_guidance": (_safe_creator_guidance_projection(review.creator_guidance)),
        "provider_attempts": attempt_payloads,
        "provider_operations": provider_operations,
        "terminal_decision": terminal_decision,
    }
    if not validate_contract:
        # The detached terminal-decision hash is defined over the same review
        # with only its terminal pointer replaced by null.  That intermediate
        # is intentionally not a public review.v2 value.
        return payload
    return dict(validate_ordinary_review_v2(payload))


def _review_checks_v1(review: LeanReviewRecordV1) -> dict[str, Any]:
    semantic = review.semantic_validation
    reader = review.reader_validation
    qualification = review.python_qualification
    failures_by_owner = {
        owner: tuple(
            {
                "code": failure.failure_code,
                "concise_explanation": failure.concise_explanation,
            }
            for failure in review.validation_failures
            if failure.owner is owner
        )
        for owner in OrdinaryValidationOwner
    }
    luna_failures: tuple[dict[str, str], ...]
    if semantic is None:
        luna_failures = failures_by_owner[OrdinaryValidationOwner.LUNA]
    else:
        values: list[dict[str, str]] = []
        conflict = semantic.verdict.conflict
        if conflict is not None:
            values.append(
                {
                    "code": conflict.conflict_class.value,
                    "concise_explanation": conflict.concise_explanation,
                }
            )
        values.extend(
            {
                "code": flag.flag_code,
                "concise_explanation": flag.concise_explanation,
            }
            for flag in semantic.verdict.review_flags
        )
        luna_failures = tuple(values)
    reader_failures = (
        failures_by_owner[OrdinaryValidationOwner.READER]
        if reader is None
        else tuple(
            {
                "code": issue.issue_code,
                "concise_explanation": issue.concise_explanation,
            }
            for issue in reader.verdict.issues
        )
    )
    python_failures = failures_by_owner[OrdinaryValidationOwner.PYTHON]
    return dict(
        validate_ordinary_review_checks_v1(
            {
                "schema_version": "cera.pi_scene.review_checks.v1",
                "luna": _check_lane(
                    role="luna_semantic_validator",
                    required=True,
                    status=(
                        "pending"
                        if semantic is None and not luna_failures
                        else "inconclusive"
                        if semantic is None
                        else "pass"
                        if semantic.verdict.verdict is SemanticVerdict.PASS
                        else "reject"
                    ),
                    verdict_sha256=(None if semantic is None else semantic.binding_sha256),
                    failures=luna_failures,
                    provider_stage_retry_status=review.luna_provider_stage_retry_status,
                ),
                "reader": _check_lane(
                    role="codex_reader_severe_quality",
                    required=True,
                    status=(
                        "pending"
                        if reader is None and not reader_failures
                        else "inconclusive"
                        if reader is None
                        else "pass"
                        if reader.verdict.status is ReaderStatus.ACCEPTED
                        else "reject"
                    ),
                    verdict_sha256=(None if reader is None else reader.binding_sha256),
                    failures=reader_failures,
                    provider_stage_retry_status=review.reader_provider_stage_retry_status,
                ),
                "adult_filter": _check_lane(
                    role="protected_adult_filter",
                    required=False,
                    status="not_applicable",
                    verdict_sha256=None,
                    failures=(),
                    provider_stage_retry_status=None,
                ),
                "python": _check_lane(
                    role="python_deterministic_custody_privacy",
                    required=True,
                    status=(
                        "pass"
                        if qualification is not None
                        else "inconclusive"
                        if python_failures
                        else "pending"
                    ),
                    verdict_sha256=(
                        None if qualification is None else qualification.qualification_sha256
                    ),
                    failures=python_failures,
                    provider_stage_retry_status=None,
                ),
            }
        )
    )


def _check_lane(
    *,
    role: str,
    required: bool,
    status: str,
    verdict_sha256: str | None,
    failures: Sequence[dict[str, str]],
    provider_stage_retry_status: object,
) -> dict[str, Any]:
    return {
        "role": role,
        "required": required,
        "status": status,
        "verdict_sha256": verdict_sha256,
        "failures": [dict(value) for value in failures],
        "provider_stage_retry_status": (
            None
            if provider_stage_retry_status is None
            else to_primitive(provider_stage_retry_status)
        ),
    }


def _review_gate_status(
    review: LeanReviewRecordV1,
    checks: dict[str, Any],
) -> str:
    required = tuple(
        value
        for key, value in checks.items()
        if key != "schema_version" and value["required"] is True
    )
    if review.validation_failures or any(
        value["provider_stage_retry_status"] is not None for value in required
    ):
        return "blocked"
    statuses = {value["status"] for value in required}
    if "reject" in statuses:
        return "reject"
    if "inconclusive" in statuses:
        return "inconclusive"
    if "pending" in statuses:
        return "pending"
    return "pass"


def _review_acceptance(review: LeanReviewRecordV1) -> dict[str, Any] | None:
    accepted = review.accepted_receipt
    if accepted is None:
        return None
    mode = {
        "automatic_accept": "automatic",
        "accept": "manual",
        "provisional_accept": "auditable_override",
    }.get(accepted.creator_action)
    if mode is None:
        raise StateConflictError("Pi Scene lifecycle acceptance mode changed")
    return {
        "mode": mode,
        "accepted_turn_id": accepted.accepted_turn_id,
        "accepted_receipt_sha256": accepted.receipt_sha256,
        "canon_status": ("provisional" if mode == "auditable_override" else "accepted"),
    }


def _review_actions(
    review: LeanReviewRecordV1,
    *,
    gate_status: str,
    recording_repair_authorized: bool,
) -> dict[str, Any]:
    unresolved = review.state == LeanReviewState.REVIEW_READY
    rejected = (
        unresolved
        and review.review_phase is OrdinaryReviewPhase.VALIDATION_REJECTED
        and gate_status == "reject"
        and review.python_qualification is not None
    )
    manual_pass = (
        unresolved
        and review.review_phase is OrdinaryReviewPhase.AWAITING_MANUAL_ACCEPT
        and gate_status == "pass"
    )
    return {
        "accept_enabled": manual_pass,
        "regenerate_enabled": rejected or manual_pass,
        "decline_enabled": rejected or manual_pass,
        "replan_enabled": False,
        "auditable_override_enabled": rejected,
        "auditable_override_action": "accept_provisional" if rejected else None,
        "repair_recording_enabled": recording_repair_authorized,
    }


def _review_mode(review: LeanReviewRecordV1) -> OrdinaryReviewMode:
    value = getattr(review.turn_input.request_controls, "review_mode", "automatic")
    try:
        return OrdinaryReviewMode(value)
    except ValueError as exc:
        raise StateConflictError("Pi Scene review mode changed") from exc


def _public_review_state(review: LeanReviewRecordV1) -> str:
    if review.state != LeanReviewState.REVIEW_READY:
        if review.state == LeanReviewState.REPAIRED:
            return "accepted"
        return review.state
    if review.review_phase in {
        OrdinaryReviewPhase.WRITER_FROZEN,
        OrdinaryReviewPhase.VALIDATING,
        OrdinaryReviewPhase.VALIDATION_BLOCKED,
    }:
        return "checks_pending"
    return "review_ready"


def _safe_creator_guidance_projection(guidance: Any) -> dict[str, str] | None:
    """Expose only a content-free binding for protected creator guidance."""

    if guidance is None:
        return None
    action = getattr(guidance, "action", None)
    text_sha256 = getattr(guidance, "text_sha256", None)
    if action not in {"regenerate", "replan"} or not isinstance(text_sha256, str):
        raise StateConflictError("Pi Scene creator guidance lost its safe binding")
    return {
        "schema_version": "cera.pi_scene.creator_guidance_projection.v1",
        "action": action,
        "text_sha256": text_sha256,
    }


def _provider_attempt_accounting(
    review: LeanReviewRecordV1,
    *,
    provider_attempts: Sequence[LeanReviewRecordV1] | None,
    validation_provider_operation_attempts: (Sequence[Mapping[str, int] | None] | None) = None,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Return request-total ordinary provider accounting for HTTP projections."""

    attempts = (review,) if provider_attempts is None else tuple(provider_attempts)
    if not attempts or attempts[-1] != review:
        raise StateConflictError("Pi Scene provider-attempt accounting lost its terminal review")
    reader_role_present = any(
        attempt.review_phase is not OrdinaryReviewPhase.LEGACY
        or attempt.reader_validation is not None
        for attempt in attempts
    )
    if validation_provider_operation_attempts is None:
        validation_operation_attempts: tuple[Mapping[str, int] | None, ...] = (None,) * len(
            attempts
        )
    else:
        validation_operation_attempts = tuple(validation_provider_operation_attempts)
        if len(validation_operation_attempts) != len(attempts):
            raise StateConflictError(
                "Pi Scene validation provider accounting changed attempt scope"
            )
    for attempt, operations in zip(attempts, validation_operation_attempts, strict=True):
        if operations is None:
            continue
        if (
            attempt.review_phase is OrdinaryReviewPhase.LEGACY
            or set(operations) != {"validator", "reader"}
            or any(type(value) is not int or value < 0 for value in operations.values())
        ):
            raise StateConflictError("Pi Scene validation provider accounting changed shape")
    attempt_payloads: list[dict[str, Any]] = []
    for index, (attempt, validation_operations) in enumerate(
        zip(attempts, validation_operation_attempts, strict=True),
        start=1,
    ):
        lifecycle = attempt.review_phase is not OrdinaryReviewPhase.LEGACY
        provider_counts = {
            "planner": attempt.result.planner_provider_operations,
            "writer": attempt.result.writer_provider_operations,
            "validator": (
                validation_operations["validator"]
                if lifecycle and validation_operations is not None
                else 1
                if attempt.semantic_validation is not None
                else 0
            ),
        }
        if reader_role_present:
            provider_counts["reader"] = (
                validation_operations["reader"]
                if lifecycle and validation_operations is not None
                else 1
                if attempt.reader_validation is not None
                else 0
            )
        attempt_payloads.append(
            {
                "attempt_number": index,
                "candidate_id": attempt.candidate.candidate_id,
                "disposition": _provider_attempt_disposition(attempt),
                "provider_operations": provider_counts,
            }
        )
    provider_operations = {
        role: sum(
            attempt_payload["provider_operations"][role] for attempt_payload in attempt_payloads
        )
        for role in (
            "planner",
            "writer",
            "validator",
            *(("reader",) if reader_role_present else ()),
        )
    }
    provider_operations["recorder"] = (
        0
        if review.accepted_receipt is None or review.recording_attempt is None
        else review.recording_attempt.provider_operations
    )
    return attempt_payloads, provider_operations


def _provider_attempt_disposition(review: LeanReviewRecordV1) -> str:
    if review.review_phase is OrdinaryReviewPhase.LEGACY:
        return (
            "semantic_pass"
            if review.semantic_validation is not None
            and review.semantic_validation.verdict.verdict is SemanticVerdict.PASS
            else "semantic_rejected"
        )
    if review.validation_failures or review.review_phase is (
        OrdinaryReviewPhase.VALIDATION_BLOCKED
    ):
        return "checks_blocked"
    semantic = review.semantic_validation
    reader = review.reader_validation
    luna_rejected = semantic is not None and semantic.verdict.verdict is SemanticVerdict.REJECT
    reader_rejected = reader is not None and reader.verdict.status is not ReaderStatus.ACCEPTED
    if luna_rejected and reader_rejected:
        return "luna_reader_rejected"
    if luna_rejected:
        return "luna_rejected"
    if reader_rejected:
        return "reader_rejected"
    if semantic is not None and reader is not None and review.python_qualification is not None:
        return "checks_passed"
    return "checks_pending"


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


def safe_reader_validation_trace(validation: Any) -> dict[str, Any] | None:
    if validation is None:
        return None
    return {
        "role": "codex_reader_severe_quality",
        "verdict": validation.verdict.status.value,
        "binding_sha256": validation.binding_sha256,
        "issues": [
            {
                "code": issue.issue_code,
                "concise_explanation": issue.concise_explanation,
            }
            for issue in validation.verdict.issues
        ],
    }


def attach_debug_path(response: dict[str, Any], debug_path: str) -> None:
    cera_payload = response.get("cera")
    if not isinstance(cera_payload, dict):
        raise StateConflictError("CERA response lost its metadata object")
    cera_payload["debug_log_path"] = debug_path
    trace = cera_payload.get("creator_trace")
    if isinstance(trace, dict):
        trace["debug_log_path"] = debug_path
