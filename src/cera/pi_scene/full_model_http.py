"""HTTP projections and replay validation for full-model adult outcomes.

This module keeps the protected adult response contract separate from the
generic loopback server.  It does not dispatch providers or mutate accepted
state; it only validates durable custody and builds the client projection.
"""

from __future__ import annotations

import json
import time
from collections.abc import Mapping
from typing import Any, cast

from cera.adult_pipeline.acceptance import AdultAcceptedTurnEnvelopeV1
from cera.adult_pipeline.contracts import AdultSceneRequestV1, BoundAdultPromotionV1
from cera.errors import ContractValidationError, StateConflictError
from cera.schema import from_mapping
from cera.serialization import canonical_sha256, text_sha256, to_primitive

from .adult_full_model_review import AdultBoundRejectedReviewV1
from .adult_orchestration import RejectedAdultRouteOperationV1
from .adult_review import AdultProvisionalAcceptanceBlockedV1
from .contracts import SceneRoute
from .full_model_controller import (
    AcceptedAdultTurnV1,
    AdultRepairAttemptTelemetryV1,
    FullModelSceneController,
    RejectedAdultTurnV1,
)
from .http_contracts import PI_SCENE_AUTO_MODEL, PI_SCENE_PROFILE, PiSceneChatRequestV1
from .request_binding import PiSceneRequestBindingV1
from .review_store import LeanSceneTurnInputV1


def adult_journal_progress(
    outcome: AcceptedAdultTurnV1 | RejectedAdultTurnV1,
    *,
    controls_sha256: str,
) -> dict[str, Any]:
    """Build the protected durable progress binding for one adult outcome."""

    operation = outcome.outcome
    prepared = operation.prepared
    rejected_payload = to_primitive(operation) if isinstance(outcome, RejectedAdultTurnV1) else None
    accepted = outcome if isinstance(outcome, AcceptedAdultTurnV1) else None
    rejected = outcome if isinstance(outcome, RejectedAdultTurnV1) else None
    return {
        "schema_version": "cera.pi_scene.http_adult_progress.v1",
        "request_id": prepared.request_id,
        "candidate_id": prepared.candidate_id,
        "operation_sha256": operation.outcome_sha256,
        "planner_provider_operations": outcome.planner_provider_operations,
        "regenerate_enabled": outcome.regenerate_enabled,
        "repair_attempts": [to_primitive(value) for value in outcome.repair_attempts],
        "world_id": prepared.route_state.world_id,
        "branch_id": prepared.route_state.branch_id,
        "actual_route": SceneRoute.ADULT.value,
        "exact_user_source_sha256": text_sha256(prepared.scene_request.exact_current_source),
        "controls_sha256": controls_sha256,
        "outcome_status": "accepted" if accepted is not None else "filter_rejected",
        "accepted_turn_id": None if accepted is None else accepted.envelope.accepted_turn_id,
        "accepted_receipt_sha256": (
            None if accepted is None else accepted.promotion.receipt.accepted_head_after_sha256
        ),
        "promotion_bundle_sha256": (
            None if accepted is None else canonical_sha256(accepted.envelope.promotion_bundle)
        ),
        "protected_rejected_outcome": rejected_payload,
        "protected_rejected_outcome_sha256": (
            None if rejected_payload is None else canonical_sha256(rejected_payload)
        ),
        "public_review_id": (None if rejected is None else rejected.public_review_id),
        "review_sha256": (None if rejected is None else rejected.review.review_sha256),
    }


def recover_adult_journal_response(
    *,
    controller: FullModelSceneController,
    request: PiSceneChatRequestV1,
    turn: LeanSceneTurnInputV1,
    binding_request_id: str,
    progress: Mapping[str, Any],
) -> dict[str, Any]:
    """Recover one exact progressed response without provider redispatch."""

    if (
        progress.get("request_id") != binding_request_id
        or progress.get("world_id") != turn.world_id
        or progress.get("branch_id") != turn.branch_id
        or progress.get("exact_user_source_sha256") != text_sha256(request.exact_user_source)
        or progress.get("controls_sha256") != canonical_sha256(to_primitive(request.controls))
    ):
        raise StateConflictError("adult request replay changed its exact turn custody")
    return _recover_adult_progress(
        controller=controller,
        request_id=binding_request_id,
        world_id=turn.world_id,
        branch_id=turn.branch_id,
        controls_sha256=canonical_sha256(to_primitive(request.controls)),
        progress=progress,
    )


def recover_adult_journal_response_from_binding(
    *,
    controller: FullModelSceneController,
    binding: PiSceneRequestBindingV1,
    progress: Mapping[str, Any],
) -> dict[str, Any]:
    """Recover progressed adult output after raw request custody was redacted."""

    if (
        progress.get("request_id") != binding.request_id
        or progress.get("world_id") != binding.world_id
        or progress.get("branch_id") != binding.branch_id
        or progress.get("controls_sha256") != binding.controls_sha256
    ):
        raise StateConflictError("adult progressed replay changed branch custody")
    return _recover_adult_progress(
        controller=controller,
        request_id=binding.request_id,
        world_id=binding.world_id,
        branch_id=binding.branch_id,
        controls_sha256=binding.controls_sha256,
        progress=progress,
    )


def _recover_adult_progress(
    *,
    controller: FullModelSceneController,
    request_id: str,
    world_id: str,
    branch_id: str,
    controls_sha256: str,
    progress: Mapping[str, Any],
) -> dict[str, Any]:
    repair_attempts = _decode_repair_attempts(progress.get("repair_attempts"))
    if progress.get("outcome_status") == "accepted":
        envelope, promotion = controller.store.load_promoted_adult_acceptance(
            world_id=world_id,
            branch_id=branch_id,
            request_id=request_id,
        )
        expected = {
            "candidate_id": envelope.candidate_id,
            "accepted_turn_id": envelope.accepted_turn_id,
            "accepted_receipt_sha256": promotion.receipt.accepted_head_after_sha256,
            "promotion_bundle_sha256": canonical_sha256(envelope.promotion_bundle),
        }
        if any(progress.get(key) != value for key, value in expected.items()):
            raise StateConflictError("accepted adult replay differs from branch state")
        return accepted_adult_completion_payload(
            envelope,
            promotion,
            operation_sha256=str(progress["operation_sha256"]),
            planner_provider_operations=int(progress["planner_provider_operations"]),
            regenerate_enabled=bool(progress["regenerate_enabled"]),
            repair_attempts=repair_attempts,
        )

    raw = progress.get("protected_rejected_outcome")
    if not isinstance(raw, Mapping):
        raise StateConflictError("rejected adult replay lost its protected outcome")
    try:
        rejected = from_mapping(RejectedAdultRouteOperationV1, raw)
    except (ContractValidationError, TypeError, ValueError) as exc:
        raise StateConflictError("rejected adult replay outcome is invalid") from exc
    public_review_id = progress.get("public_review_id")
    if not isinstance(public_review_id, str):
        raise StateConflictError("rejected adult replay lost its public review identity")
    bound = controller.get_adult_review(public_review_id)
    wrapped = RejectedAdultTurnV1(
        schema_version=RejectedAdultTurnV1.SCHEMA_VERSION,
        outcome=rejected,
        planner_provider_operations=int(progress["planner_provider_operations"]),
        review=bound.review,
        public_review_id=bound.public_review_id,
        regenerate_enabled=bound.regenerate_available,
        repair_attempts=repair_attempts,
    )
    if adult_journal_progress(
        wrapped,
        controls_sha256=controls_sha256,
    ) != dict(progress):
        raise StateConflictError("rejected adult replay binding changed")
    return rejected_adult_completion_payload(wrapped)


def adult_completion_payload(
    outcome: AcceptedAdultTurnV1 | RejectedAdultTurnV1,
) -> dict[str, Any]:
    if isinstance(outcome, AcceptedAdultTurnV1):
        return accepted_adult_completion_payload(
            outcome.envelope,
            outcome.promotion,
            operation_sha256=outcome.outcome.outcome_sha256,
            planner_provider_operations=outcome.planner_provider_operations,
            regenerate_enabled=outcome.regenerate_enabled,
            repair_attempts=outcome.repair_attempts,
        )
    return rejected_adult_completion_payload(outcome)


def accepted_adult_completion_payload(
    envelope: AdultAcceptedTurnEnvelopeV1,
    promotion: BoundAdultPromotionV1,
    *,
    operation_sha256: str,
    planner_provider_operations: int,
    regenerate_enabled: bool,
    repair_attempts: tuple[AdultRepairAttemptTelemetryV1, ...],
) -> dict[str, Any]:
    bundle = envelope.promotion_bundle
    scene_request = _accepted_scene_request(envelope)
    scene_output = envelope.scene_invocation.output
    provider_operations = {
        "planner": planner_provider_operations
        + sum(value.planner_provider_operations for value in repair_attempts),
        "adult_scene": envelope.scene_invocation.receipt.provider_operations
        + sum(value.adult_scene_provider_operations for value in repair_attempts),
        "adult_filter": envelope.filter_invocation.receipt.provider_operations
        + sum(value.adult_filter_provider_operations for value in repair_attempts),
        "recorder": 0,
    }
    creator_trace = {
        "schema_version": "cera.pi_scene.creator_adult_trace.v1",
        "logic_owner": bundle.logic_owner,
        "decision_records": [to_primitive(value) for value in scene_output.decision_path],
        "autonomy": {"mode": scene_request.autonomy_mode, "applications": []},
        "route_transition": {
            "to_route": scene_output.next_route.value,
            "reason": scene_output.next_route_reason,
            "return_to_codex": bundle.return_to_codex,
        },
        "validation": {
            "role": "deepseek_adult_filter",
            "verdict": "pass",
            "binding_sha256": bundle.filter_binding_sha256,
            "conflict": None,
        },
        "recording": {
            "status": envelope.recording_status,
            "recorder_required": False,
            "projection_status": "complete",
            "protected_record_status": "complete",
        },
        "provisional_dependencies": [],
        "provider_operations": provider_operations,
        "repair_attempts": [to_primitive(value) for value in repair_attempts],
    }
    return {
        "id": f"chatcmpl-cera-{bundle.scene_candidate_sha256[:24]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": PI_SCENE_AUTO_MODEL,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": bundle.exact_story_prose},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        "cera": {
            "profile_id": PI_SCENE_PROFILE,
            "route_mode": SceneRoute.ADULT.value,
            "logic_owner": bundle.logic_owner,
            "decision_records": [to_primitive(value) for value in scene_output.decision_path],
            "autonomy_application": {"mode": scene_request.autonomy_mode},
            "route_transition": {
                "to_route": scene_output.next_route.value,
                "reason": scene_output.next_route_reason,
            },
            "provisional_dependencies": [],
            "creator_trace": creator_trace,
            "provisional": False,
            "status": "accepted",
            "story_state_committed": True,
            "canon_status": "accepted",
            "candidate_id": envelope.candidate_id,
            "generation": envelope.generation,
            "accepted_turn_id": envelope.accepted_turn_id,
            "accepted_receipt_sha256": promotion.receipt.accepted_head_after_sha256,
            "recording_status": envelope.recording_status,
            "recorder_required": False,
            "adult_filter": {
                "verdict": "pass",
                "binding_sha256": bundle.filter_binding_sha256,
            },
            "current_logic_route": bundle.next_route.value,
            "return_to_codex": bundle.return_to_codex,
            "operation_sha256": operation_sha256,
            "protected_full_record_sha256": canonical_sha256(bundle.protected_full_record),
            "codex_projection_sha256": canonical_sha256(bundle.codex_projection),
            "operational_warnings": [],
            "regenerate_enabled": regenerate_enabled,
            "provider_operations": provider_operations,
            "repair_attempts": [to_primitive(value) for value in repair_attempts],
        },
    }


def rejected_adult_completion_payload(outcome: RejectedAdultTurnV1) -> dict[str, Any]:
    protected = outcome.outcome
    conflict = protected.conflict
    scene = protected.protected_execution.result.scene
    scene_request = scene.request
    scene_output = scene.invocation.output
    provider_operations = {
        "planner": outcome.planner_provider_operations
        + sum(value.planner_provider_operations for value in outcome.repair_attempts),
        "adult_scene": scene.invocation.receipt.provider_operations
        + sum(value.adult_scene_provider_operations for value in outcome.repair_attempts),
        "adult_filter": protected.protected_execution.result.filtered.invocation.receipt.provider_operations
        + sum(value.adult_filter_provider_operations for value in outcome.repair_attempts),
        "recorder": 0,
    }
    safe_conflict = {
        "conflict_class": conflict.conflict_class.value,
        "decision_key": conflict.decision_key,
        "concise_explanation": conflict.concise_explanation,
    }
    creator_trace = {
        "schema_version": "cera.pi_scene.creator_adult_trace.v1",
        "logic_owner": "deepseek_adult_scene",
        "decision_records": [to_primitive(value) for value in scene_output.decision_path],
        "autonomy": {"mode": scene_request.autonomy_mode, "applications": []},
        "route_transition": {
            "to_route": scene_output.next_route.value,
            "reason": scene_output.next_route_reason,
            "return_to_codex": False,
        },
        "validation": {
            "role": "deepseek_adult_filter",
            "verdict": "reject",
            "binding_sha256": None,
            "conflict": safe_conflict,
        },
        "recording": {
            "status": None,
            "recorder_required": False,
            "projection_status": "not_produced",
            "protected_record_status": "not_produced",
        },
        "provisional_dependencies": [],
        "provider_operations": provider_operations,
        "repair_attempts": [to_primitive(value) for value in outcome.repair_attempts],
    }
    return {
        "id": f"chatcmpl-cera-{scene.candidate_sha256[:24]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": PI_SCENE_AUTO_MODEL,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": outcome.exact_story_prose},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        "cera": {
            "profile_id": PI_SCENE_PROFILE,
            "route_mode": SceneRoute.ADULT.value,
            "logic_owner": "deepseek_adult_scene",
            "decision_records": [to_primitive(value) for value in scene_output.decision_path],
            "autonomy_application": {"mode": scene_request.autonomy_mode},
            "route_transition": {
                "to_route": scene_output.next_route.value,
                "reason": scene_output.next_route_reason,
            },
            "provisional_dependencies": [],
            "creator_trace": creator_trace,
            "provisional": True,
            "status": "validation_rejected",
            "story_state_committed": False,
            "canon_status": None,
            "candidate_id": protected.prepared.candidate_id,
            "generation": None,
            "accepted_turn_id": None,
            "accepted_receipt_sha256": None,
            "recording_status": None,
            "recorder_required": False,
            "adult_filter": {"verdict": "reject", "conflict": to_primitive(conflict)},
            "current_logic_route": protected.prepared.route_state.current_logic_route.value,
            "return_to_codex": False,
            "operation_sha256": protected.outcome_sha256,
            "review_id": outcome.public_review_id,
            "provisional_review_id": outcome.public_review_id,
            "review_url": f"/v1/cera/reviews/{outcome.public_review_id}",
            "review_status": outcome.review.operation.state.value,
            "accept_enabled": False,
            "provisional_accept_enabled": False,
            "provisional_acceptance_status": (outcome.review.provisional_acceptance.value),
            "decline_enabled": (outcome.review.operation.state.value == "executed-rejected"),
            "regenerate_enabled": outcome.regenerate_enabled,
            "replan_enabled": False,
            "operational_warnings": [],
            "provider_operations": provider_operations,
            "repair_attempts": [to_primitive(value) for value in outcome.repair_attempts],
        },
    }


def adult_review_payload(bound: AdultBoundRejectedReviewV1) -> dict[str, Any]:
    """Return the non-explicit creator-review surface for one rejection."""

    review = bound.review
    operation_state = review.operation.state.value
    active = operation_state == "executed-rejected"
    state = (
        "review_ready" if active else "declined" if operation_state == "declined" else "rejected"
    )
    protected = bound.outcome
    scene = protected.protected_execution.result.scene
    return {
        "schema_version": "cera.pi_scene.review.v1",
        "review_id": bound.public_review_id,
        "state": state,
        "provisional": active,
        "route": "adult",
        # Exact protected prose stays in the already-rendered completion and
        # protected runtime.  Polling GET never becomes a second prose channel.
        "story_text": None,
        "story_state_committed": False,
        "candidate_id": review.operation.candidate_id,
        "candidate_sha256": scene.candidate_sha256,
        "primary_authority_kind": "deepseek_adult_scene_filter",
        "primary_authority_sha256": review.operation.operation_sha256,
        "warnings": [
            {
                "warning_code": review.conflict_class.value,
                "severity": "review",
            }
        ],
        "warnings_block_accept": True,
        "recording_status": None,
        "canon_status": "unaccepted",
        "semantic_validation": {
            "binding_sha256": review.operation.outcome_sha256,
            "verdict": "reject",
            "automatic_repair_eligible": review.automatic_repair_eligible,
            "conflict": {
                "conflict_class": review.conflict_class.value,
                "conflict_anchor": review.conflict_anchor.value,
            },
            "review_flags": [],
        },
        "request_controls": None,
        "creator_guidance": None,
        "accept_enabled": False,
        "provisional_accept_enabled": False,
        "decline_enabled": active,
        "regenerate_enabled": active and bound.regenerate_available,
        "replan_enabled": False,
        "repair_recording_enabled": False,
        "provisional_acceptance_status": review.provisional_acceptance.value,
        "automatic_repair_limit": 1,
        "operation_state": operation_state,
        "provider_operations": {
            "planner": bound.planner_provider_operations,
            "writer": scene.invocation.receipt.provider_operations,
            "adult_filter": (
                protected.protected_execution.result.filtered.invocation.receipt.provider_operations
            ),
            "recorder": 0,
        },
    }


def adult_provisional_blocked_payload(
    *,
    public_review_id: str,
    blocked: AdultProvisionalAcceptanceBlockedV1,
) -> dict[str, Any]:
    """Project the typed fail-closed result without its protected identity."""

    return {
        "schema_version": blocked.schema_version,
        "review_id": public_review_id,
        "disposition": blocked.disposition.value,
        "reason_code": blocked.reason_code,
        "required_artifacts": list(blocked.required_artifacts),
        "story_state_committed": False,
        "accepted_effect_created": blocked.accepted_effect_created,
        "accept_enabled": False,
        "next_action": "protected_reprojection_provider_operation_required",
    }


def _decode_repair_attempts(
    raw: object,
) -> tuple[AdultRepairAttemptTelemetryV1, ...]:
    if not isinstance(raw, list) or len(raw) > 1:
        raise StateConflictError("adult repair-attempt custody is invalid")
    decoded: list[AdultRepairAttemptTelemetryV1] = []
    for value in raw:
        if not isinstance(value, Mapping):
            raise StateConflictError("adult repair-attempt custody is invalid")
        try:
            decoded.append(from_mapping(AdultRepairAttemptTelemetryV1, value))
        except (ContractValidationError, TypeError, ValueError) as exc:
            raise StateConflictError("adult repair-attempt custody is invalid") from exc
    return tuple(decoded)


def _accepted_scene_request(envelope: AdultAcceptedTurnEnvelopeV1) -> AdultSceneRequestV1:
    try:
        raw = json.loads(envelope.primary_handoff_json)
    except json.JSONDecodeError as exc:
        raise StateConflictError("accepted adult request is not valid JSON") from exc
    if not isinstance(raw, Mapping):
        raise StateConflictError("accepted adult request is not an object")
    try:
        return cast(AdultSceneRequestV1, from_mapping(AdultSceneRequestV1, raw))
    except (ContractValidationError, TypeError, ValueError) as exc:
        raise StateConflictError("accepted adult request is invalid") from exc
