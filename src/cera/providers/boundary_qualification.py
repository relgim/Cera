"""Fail-closed loader for the live relational protected-user boundary probe."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from cera.errors import ContractValidationError, IdentityError
from cera.ids import IdKind, TypedId
from cera.serialization import bytes_sha256, re_is_sha256


@dataclass(frozen=True, slots=True)
class RelationalBoundaryQualificationEvidence:
    schema_version: str
    qualification_id: str
    summary_sha256: str
    composer_prompt_version: str
    composer_dto_version: str
    composer_model: str
    verifier_model: str
    candidate_sha256: str
    deepseek_calls: int
    sol_calls: int


def load_relational_boundary_qualification(
    path: Path,
    *,
    expected_summary_sha256: str,
    expected_composer_prompt_version: str,
    expected_composer_dto_version: str,
    expected_composer_model: str,
    expected_verifier_model: str,
    expected_probe_schema: str = (
        "cera.deepseek_relational_boundary_probe.v1"
    ),
    expected_qualification_id: str = (
        "deepseek-relational-protected-user-boundary-v1"
    ),
) -> RelationalBoundaryQualificationEvidence:
    """Load only the exact passed two-stage, unchanged-candidate probe."""

    if not path.is_absolute() or not path.is_file():
        raise ContractValidationError(
            "relational boundary evidence must be an existing absolute file"
        )
    raw = path.read_bytes()
    actual_sha256 = bytes_sha256(raw)
    if actual_sha256 != expected_summary_sha256:
        raise ContractValidationError(
            "relational boundary evidence hash does not match"
        )
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ContractValidationError(
            "relational boundary evidence is not valid JSON"
        ) from None
    if not isinstance(payload, dict):
        raise ContractValidationError(
            "relational boundary evidence must be an object"
        )

    candidate_sha256 = payload.get("composer_candidate_sha256")
    verifier_candidate_sha256 = payload.get("verifier_candidate_sha256")
    if (
        payload.get("schema_version") != expected_probe_schema
        or payload.get("qualification_id") != expected_qualification_id
        or payload.get("goal_authority_id") != "D-150"
        or payload.get("status") != "passed"
        or payload.get("prompt_version")
        != expected_composer_prompt_version
        or payload.get("dto_version") != expected_composer_dto_version
        or payload.get("attempts_per_stage") != 1
        or payload.get("retry_enabled") is not False
        or payload.get("fallback_enabled") is not False
        or payload.get("deepseek_dispatches") != 1
        or payload.get("sol_dispatches") != 1
        or payload.get("canonical_story_or_genesis_content_used") is not False
        or payload.get("synthetic_non_story_candidate_used") is not True
        or payload.get("story_authority_writes") != 0
        or payload.get("retains_prompt") is not False
        or payload.get("retains_raw_provider_output") is not False
        or payload.get("retains_synthetic_candidate_text") is not False
        or payload.get("protected_user_semantic_boundary_verified") is not True
        or payload.get("candidate_passed_unchanged") is not True
        or candidate_sha256 != verifier_candidate_sha256
    ):
        raise ContractValidationError(
            "relational boundary qualification did not pass its safe contract"
        )
    _require_sha256(candidate_sha256, "relational candidate hash")
    if expected_probe_schema != (
        "cera.deepseek_relational_boundary_probe.v1"
    ):
        _require_sha256(
            payload.get("packet_sha256"),
            "relational Composer packet hash",
        )
        _require_sha256(
            payload.get("obligation_sha256"),
            "relational Composer obligation hash",
        )
    deepseek_route_sha256 = payload.get("deepseek_route_sha256")
    verifier_route_sha256 = payload.get("verifier_route_sha256")
    _require_sha256(deepseek_route_sha256, "DeepSeek route hash")
    _require_sha256(verifier_route_sha256, "verifier route hash")

    stages = payload.get("provider_stages")
    if not isinstance(stages, list) or len(stages) != 2:
        raise ContractValidationError(
            "relational boundary qualification requires exactly two stages"
        )
    composer, verifier = stages
    if not isinstance(composer, dict) or not isinstance(verifier, dict):
        raise ContractValidationError(
            "relational boundary stage evidence is malformed"
        )
    if (
        composer.get("stage") != "deepseek_composition"
        or composer.get("provider_category") != "deepseek"
        or composer.get("status") != "passed"
        or composer.get("dispatch_started") is not True
        or composer.get("external_provider_calls_observed") != 1
        or composer.get("candidate_sha256") != candidate_sha256
        or not isinstance(composer.get("segment_count"), int)
        or composer.get("segment_count") < 1
    ):
        raise ContractValidationError(
            "relational boundary Composer stage did not pass"
        )
    composer_receipt = composer.get("provider_receipt")
    _validate_provider_receipt(
        composer_receipt,
        provider="deepseek",
        role="scene_composer",
        model=expected_composer_model,
        route_sha256=deepseek_route_sha256,
        quota_metered=False,
    )

    persistent_runner = verifier.get("persistent_runner")
    if (
        verifier.get("stage") != "sol_verification"
        or verifier.get("provider_category") != "sol"
        or verifier.get("status") != "passed"
        or verifier.get("dispatch_started") is not True
        or verifier.get("external_provider_calls_observed") != 1
        or verifier.get("candidate_sha256") != candidate_sha256
        or verifier.get("candidate_passed_unchanged") is not True
        or persistent_runner
        != {"process_launch_count": 1, "request_submission_count": 1}
    ):
        raise ContractValidationError(
            "relational boundary Verifier stage did not pass unchanged"
        )
    verifier_provider_receipt = verifier.get("provider_receipt")
    _validate_provider_receipt(
        verifier_provider_receipt,
        provider="openai_codex",
        role="scene_realization_verifier",
        model=expected_verifier_model,
        route_sha256=verifier_route_sha256,
        quota_metered=True,
    )
    verification_receipt = verifier.get("verification_receipt")
    if not isinstance(verification_receipt, dict):
        raise ContractValidationError(
            "relational boundary verification receipt is missing"
        )
    if (
        verification_receipt.get("schema_version")
        != "cera.scene_realization_verification_receipt.v4"
        or verification_receipt.get("status") != "accepted"
        or verification_receipt.get("qualification_eligible") is not True
        or verification_receipt.get("external_provider_calls") != 1
        or verification_receipt.get("authoritative_store_writes") != 0
        or verification_receipt.get("retains_story_prose") is not False
        or verification_receipt.get("candidate_sha256") != candidate_sha256
        or verification_receipt.get("story_text_sha256") != candidate_sha256
        or verification_receipt.get("provider_receipt_id")
        != verifier_provider_receipt.get("provider_receipt_id")
        or verification_receipt.get("verifier_adapter_evidence_sha256")
        != verifier_route_sha256
        or verification_receipt.get("verified_boundary_checks")
        != ["protected_user_no_unsupplied_realization"]
        or not verification_receipt.get("verified_beat_ids")
        or not verification_receipt.get("verified_participant_ids")
        or verification_receipt.get("violation_codes") != []
        or verification_receipt.get("violation_finding_sha256s") != []
    ):
        raise ContractValidationError(
            "relational boundary semantic verification is incompatible"
        )

    return RelationalBoundaryQualificationEvidence(
        schema_version="cera.relational_boundary_qualification_evidence.v1",
        qualification_id=payload["qualification_id"],
        summary_sha256=actual_sha256,
        composer_prompt_version=expected_composer_prompt_version,
        composer_dto_version=expected_composer_dto_version,
        composer_model=expected_composer_model,
        verifier_model=expected_verifier_model,
        candidate_sha256=candidate_sha256,
        deepseek_calls=1,
        sol_calls=1,
    )


def _validate_provider_receipt(
    receipt: object,
    *,
    provider: str,
    role: str,
    model: str,
    route_sha256: str,
    quota_metered: bool,
) -> None:
    if not isinstance(receipt, dict):
        raise ContractValidationError(
            "relational boundary provider receipt is missing"
        )
    if (
        receipt.get("schema_version") != "cera.live_provider_call_receipt.v2"
        or receipt.get("provider") != provider
        or receipt.get("role") != role
        or receipt.get("requested_model") != model
        or receipt.get("returned_model") != model
        or receipt.get("route_sha256") != route_sha256
        or receipt.get("external_provider_calls") != 1
        or receipt.get("automatic_retry_count") != 0
        or receipt.get("story_authority_writes") != 0
        or receipt.get("quota_metered") is not quota_metered
        or any(
            receipt.get(field) is not False
            for field in (
                "retains_raw_source",
                "retains_story_prose",
                "retains_private_evidence",
                "retains_prompt",
                "retains_secret",
            )
        )
    ):
        raise ContractValidationError(
            "relational boundary provider receipt is incompatible"
        )
    try:
        TypedId.parse(
            receipt.get("provider_receipt_id"),
            IdKind.PROVIDER_RECEIPT,
        )
    except IdentityError:
        raise ContractValidationError(
            "relational boundary provider receipt identity is invalid"
        ) from None
    for field in (
        "provider_request_id_sha256",
        "request_sha256",
        "output_sha256",
    ):
        _require_sha256(
            receipt.get(field),
            f"relational boundary provider receipt {field}",
        )


def _require_sha256(value: Any, label: str) -> None:
    if not isinstance(value, str) or not re_is_sha256(value):
        raise ContractValidationError(f"{label} is invalid")
