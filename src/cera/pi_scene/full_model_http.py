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

from .adult_orchestration import RejectedAdultRouteOperationV1
from .contracts import SceneRoute
from .full_model_controller import (
    AcceptedAdultTurnV1,
    FullModelSceneController,
    RejectedAdultTurnV1,
)
from .http_contracts import PI_SCENE_AUTO_MODEL, PI_SCENE_PROFILE, PiSceneChatRequestV1
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
    return {
        "schema_version": "cera.pi_scene.http_adult_progress.v1",
        "request_id": prepared.request_id,
        "candidate_id": prepared.candidate_id,
        "operation_sha256": operation.outcome_sha256,
        "planner_provider_operations": outcome.planner_provider_operations,
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
    if progress.get("outcome_status") == "accepted":
        envelope, promotion = controller.store.load_promoted_adult_acceptance(
            world_id=turn.world_id,
            branch_id=turn.branch_id,
            request_id=binding_request_id,
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
        )

    raw = progress.get("protected_rejected_outcome")
    if not isinstance(raw, Mapping):
        raise StateConflictError("rejected adult replay lost its protected outcome")
    try:
        rejected = from_mapping(RejectedAdultRouteOperationV1, raw)
    except (ContractValidationError, TypeError, ValueError) as exc:
        raise StateConflictError("rejected adult replay outcome is invalid") from exc
    wrapped = RejectedAdultTurnV1(
        schema_version=RejectedAdultTurnV1.SCHEMA_VERSION,
        outcome=rejected,
        planner_provider_operations=int(progress["planner_provider_operations"]),
    )
    if adult_journal_progress(
        wrapped,
        controls_sha256=canonical_sha256(to_primitive(request.controls)),
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
        )
    return rejected_adult_completion_payload(outcome)


def accepted_adult_completion_payload(
    envelope: AdultAcceptedTurnEnvelopeV1,
    promotion: BoundAdultPromotionV1,
    *,
    operation_sha256: str,
    planner_provider_operations: int,
) -> dict[str, Any]:
    bundle = envelope.promotion_bundle
    scene_request = _accepted_scene_request(envelope)
    scene_output = envelope.scene_invocation.output
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
            "provider_operations": {
                "planner": planner_provider_operations,
                "adult_scene": envelope.scene_invocation.receipt.provider_operations,
                "adult_filter": envelope.filter_invocation.receipt.provider_operations,
                "recorder": 0,
            },
        },
    }


def rejected_adult_completion_payload(outcome: RejectedAdultTurnV1) -> dict[str, Any]:
    protected = outcome.outcome
    conflict = protected.conflict
    scene = protected.protected_execution.result.scene
    scene_request = scene.request
    scene_output = scene.invocation.output
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
            "accept_enabled": False,
            "provisional_accept_enabled": False,
            "regenerate_enabled": True,
            "replan_enabled": False,
            "operational_warnings": [],
            "provider_operations": {
                "planner": outcome.planner_provider_operations,
                "adult_scene": (
                    protected.protected_execution.result.scene.invocation.receipt.provider_operations
                ),
                "adult_filter": (
                    protected.protected_execution.result.filtered.invocation.receipt.provider_operations
                ),
                "recorder": 0,
            },
        },
    }


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
