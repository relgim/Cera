"""Pure validation for durable ordinary and adult request progress."""

from __future__ import annotations

import re
from typing import Any

from cera.adult_pipeline.contracts import AdultFilterConflictClass
from cera.errors import StateConflictError
from cera.serialization import canonical_sha256, re_is_sha256

from .contracts import SceneRoute
from .request_binding import PiSceneRequestBindingV1


def validate_request_progress(
    progress: Any,
    binding: PiSceneRequestBindingV1,
) -> None:
    if isinstance(progress, dict) and progress.get("schema_version") == (
        "cera.pi_scene.http_adult_progress.v1"
    ):
        _validate_adult_progress(progress, binding)
        return
    required = {
        "schema_version",
        "review_id",
        "candidate_id",
        "candidate_sha256",
        "world_id",
        "branch_id",
        "route",
        "exact_user_source_sha256",
        "controls_sha256",
        "review_state",
        "accepted_turn_id",
        "accepted_receipt_sha256",
        "recording_status",
    }
    if not isinstance(progress, dict) or set(progress) != required:
        raise StateConflictError("Pi Scene request review-progress shape changed")
    if progress["schema_version"] != "cera.pi_scene.http_review_progress.v1":
        raise StateConflictError("Pi Scene request review-progress schema changed")
    if not re.fullmatch(r"review-[a-f0-9]{28}", progress["review_id"] or ""):
        raise StateConflictError("Pi Scene request review identity is invalid")
    if not isinstance(progress["candidate_id"], str) or not progress["candidate_id"]:
        raise StateConflictError("Pi Scene request candidate identity is invalid")
    for field_name in (
        "candidate_sha256",
        "exact_user_source_sha256",
        "controls_sha256",
    ):
        if not re_is_sha256(progress[field_name] or ""):
            raise StateConflictError(f"Pi Scene request {field_name} is invalid")
    actual_route = progress["route"]
    route_matches = actual_route == binding.route.value or (
        binding.route_intent == "automatic"
        and actual_route in {SceneRoute.ORDINARY.value, SceneRoute.ADULT.value}
    )
    if (
        progress["world_id"] != binding.world_id
        or progress["branch_id"] != binding.branch_id
        or not route_matches
        or progress["controls_sha256"] != binding.controls_sha256
    ):
        raise StateConflictError("Pi Scene request review-progress custody changed")
    accepted_turn = progress["accepted_turn_id"]
    accepted_sha = progress["accepted_receipt_sha256"]
    if (accepted_turn is None) != (accepted_sha is None):
        raise StateConflictError("Pi Scene accepted review-progress binding is partial")
    if accepted_turn is not None and (
        not isinstance(accepted_turn, str) or not re_is_sha256(accepted_sha or "")
    ):
        raise StateConflictError("Pi Scene accepted review-progress binding is invalid")
    if progress["recording_status"] is not None and not isinstance(
        progress["recording_status"], str
    ):
        raise StateConflictError("Pi Scene review-progress recording status is invalid")


def _validate_adult_progress(
    progress: dict[str, Any],
    binding: PiSceneRequestBindingV1,
) -> None:
    required = {
        "schema_version",
        "request_id",
        "candidate_id",
        "operation_sha256",
        "planner_provider_operations",
        "regenerate_enabled",
        "repair_attempts",
        "world_id",
        "branch_id",
        "actual_route",
        "exact_user_source_sha256",
        "controls_sha256",
        "outcome_status",
        "accepted_turn_id",
        "accepted_receipt_sha256",
        "promotion_bundle_sha256",
        "protected_rejected_outcome",
        "protected_rejected_outcome_sha256",
        "public_review_id",
        "review_sha256",
    }
    if set(progress) != required:
        raise StateConflictError("Pi Scene adult request-progress shape changed")
    if progress["request_id"] != binding.request_id:
        raise StateConflictError("Pi Scene adult request identity changed")
    if (
        progress["world_id"] != binding.world_id
        or progress["branch_id"] != binding.branch_id
        or progress["actual_route"] != SceneRoute.ADULT.value
        or progress["controls_sha256"] != binding.controls_sha256
    ):
        raise StateConflictError("Pi Scene adult request-progress custody changed")
    if binding.route is not SceneRoute.ADULT and binding.route_intent != "automatic":
        raise StateConflictError("Pi Scene adult result lacks protected route custody")
    if not isinstance(progress["candidate_id"], str) or not progress["candidate_id"]:
        raise StateConflictError("Pi Scene adult candidate identity is invalid")
    for field_name in (
        "operation_sha256",
        "exact_user_source_sha256",
        "controls_sha256",
    ):
        if not re_is_sha256(progress[field_name] or ""):
            raise StateConflictError(f"Pi Scene adult {field_name} is invalid")
    if (
        type(progress["planner_provider_operations"]) is not int
        or progress["planner_provider_operations"] < 0
    ):
        raise StateConflictError("Pi Scene adult Planner operation count is invalid")
    if type(progress["regenerate_enabled"]) is not bool:
        raise StateConflictError("Pi Scene adult Regenerate state is invalid")
    repair_attempts = progress["repair_attempts"]
    if not isinstance(repair_attempts, list) or len(repair_attempts) > 1:
        raise StateConflictError("Pi Scene adult repair-attempt custody is invalid")
    repair_fields = {
        "public_review_id",
        "conflict_class",
        "operation_sha256",
        "outcome_sha256",
        "planner_provider_operations",
        "adult_scene_provider_operations",
        "adult_filter_provider_operations",
    }
    for repair in repair_attempts:
        if not isinstance(repair, dict) or set(repair) != repair_fields:
            raise StateConflictError("Pi Scene adult repair-attempt shape changed")
        if (
            re.fullmatch(r"review-[a-f0-9]{28}", repair["public_review_id"] or "") is None
            or repair["conflict_class"] not in {value.value for value in AdultFilterConflictClass}
            or not re_is_sha256(repair["operation_sha256"] or "")
            or not re_is_sha256(repair["outcome_sha256"] or "")
        ):
            raise StateConflictError("Pi Scene adult repair-attempt identity is invalid")
        for field_name in (
            "planner_provider_operations",
            "adult_scene_provider_operations",
            "adult_filter_provider_operations",
        ):
            if type(repair[field_name]) is not int or repair[field_name] < 0:
                raise StateConflictError("Pi Scene adult repair-attempt count is invalid")
    status = progress["outcome_status"]
    accepted_values = (
        progress["accepted_turn_id"],
        progress["accepted_receipt_sha256"],
        progress["promotion_bundle_sha256"],
    )
    rejected = progress["protected_rejected_outcome"]
    rejected_sha = progress["protected_rejected_outcome_sha256"]
    public_review_id = progress["public_review_id"]
    review_sha256 = progress["review_sha256"]
    if status == "accepted":
        if (
            not isinstance(accepted_values[0], str)
            or not re_is_sha256(accepted_values[1] or "")
            or not re_is_sha256(accepted_values[2] or "")
            or rejected is not None
            or rejected_sha is not None
            or public_review_id is not None
            or review_sha256 is not None
        ):
            raise StateConflictError("Pi Scene accepted adult progress is invalid")
    elif status == "filter_rejected":
        if (
            any(value is not None for value in accepted_values)
            or not isinstance(rejected, dict)
            or not re_is_sha256(rejected_sha or "")
            or canonical_sha256(rejected) != rejected_sha
            or not re.fullmatch(r"review-[a-f0-9]{28}", public_review_id or "")
            or not re_is_sha256(review_sha256 or "")
            or public_review_id != f"review-{review_sha256[:28]}"
        ):
            raise StateConflictError("Pi Scene rejected adult progress is invalid")
    else:
        raise StateConflictError("Pi Scene adult outcome status changed")
