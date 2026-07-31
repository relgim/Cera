"""Deterministic deployment-readiness assessment without deployment authority."""

from __future__ import annotations

from cera.ids import IdKind, deterministic_id
from cera.serialization import domain_sha256

from .models import (
    DeploymentReadinessInput,
    DeploymentReadinessReport,
    DeploymentReadinessStatus,
)


def assess_deployment_readiness(value: DeploymentReadinessInput) -> DeploymentReadinessReport:
    blockers: list[str] = []
    missing_roles = sorted(
        role.value for role in set(value.required_roles).difference(value.qualified_roles)
    )
    if missing_roles:
        blockers.append("provider roles are not qualified: " + ", ".join(missing_roles))
    gates = (
        (value.matched_human_review_complete, "matched blinded human review is incomplete"),
        (value.telemetry_privacy_review_complete, "telemetry privacy review is incomplete"),
        (value.production_world_authorized, "production world binding is not authorized"),
        (value.sillytavern_integration_authorized, "SillyTavern integration is not authorized"),
        (value.credential_storage_approved, "credential storage is not approved"),
        (value.creator_fresh_chat_accepted, "creator fresh-chat acceptance is missing"),
        (value.deployment_authorized, "deployment is not authorized"),
    )
    blockers.extend(message for passed, message in gates if not passed)
    assessment_key = domain_sha256(
        "cera.deployment_readiness_key.v1",
        {
            "promotion_assessment_ids": value.promotion_assessment_ids,
            "required_roles": value.required_roles,
            "qualified_roles": value.qualified_roles,
            "blockers": tuple(blockers),
        },
    )
    assessment_id = deterministic_id(
        IdKind.PROMOTION_ASSESSMENT,
        "phase10-deployment-readiness",
        assessment_key,
    )
    status = (
        DeploymentReadinessStatus.BLOCKED
        if blockers
        else DeploymentReadinessStatus.ELIGIBLE_FOR_CREATOR_DECISION
    )
    return DeploymentReadinessReport(
        schema_version=DeploymentReadinessReport.SCHEMA_VERSION,
        assessment_id=assessment_id,
        status=status,
        blockers=tuple(blockers),
        creator_decision_required=True,
        deployment_executed=False,
    )
