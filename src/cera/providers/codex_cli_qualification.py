"""Fail-closed loader for immutable one-shot Codex CLI verifier evidence.

An accepted record qualifies only the exact caller-supplied historical route
and provider-schema hashes. It never follows or implicitly qualifies the
current active adapter after a version change.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from cera.errors import ContractValidationError, IdentityError
from cera.ids import IdKind, TypedId
from cera.serialization import bytes_sha256

from .codex_exec_contract import (
    CODEX_CLI_EXEC_COMPATIBILITY_ID,
    CODEX_CLI_EXEC_CONTRACT_SHA256,
    CODEX_CLI_EXEC_TRANSPORT_NAME,
    CODEX_CLI_EXEC_VERSION,
)


ACTIVE_CODEX_CLI_VERIFIER_SUMMARY_SHA256 = (
    "2253e1862cdb0a6b60b6d5dc40e6ff5f55800dd7f8aa259e4d7dd8b99864aca3"
)
ACTIVE_CODEX_CLI_VERIFIER_QUALIFICATION_ID = (
    "cera-cli-verifier-probe-2026-07-29-v1"
)
_CASE_EXPECTATIONS = (
    ("two_participant_three_beat_acceptance", "accepted", None, 2, 3),
    (
        "unsupplied_protected_user_action_rejection",
        "rejected",
        "protected_user_unsupplied_realization",
        1,
        1,
    ),
    (
        "exact_protected_dialogue_two_participant_acceptance",
        "accepted",
        None,
        2,
        2,
    ),
    (
        "three_participant_embodied_realization_acceptance",
        "accepted",
        None,
        3,
        3,
    ),
)


@dataclass(frozen=True, slots=True)
class CodexCliVerifierQualificationEvidence:
    schema_version: str
    qualification_id: str
    summary_sha256: str
    route_sha256: str
    requested_model: str
    verifier_calls: int
    accepted_cases: int
    rejected_cases: int
    transport_name: str
    transport_version: str
    transport_compatibility_id: str
    transport_compatibility_source_sha256: str


def load_codex_cli_verifier_qualification(
    path: Path,
    *,
    expected_summary_sha256: str,
    expected_route_sha256: str,
    expected_model: str = "gpt-5.6-sol",
    expected_provider_schema_sha256: str,
) -> CodexCliVerifierQualificationEvidence:
    """Validate the exact four-call, tool-free CLI verifier qualification."""

    if not path.is_absolute() or not path.is_file():
        raise ContractValidationError(
            "Codex CLI verifier evidence must be an existing absolute file"
        )
    raw = path.read_bytes()
    actual_sha256 = bytes_sha256(raw)
    if actual_sha256 != expected_summary_sha256:
        raise ContractValidationError(
            "Codex CLI verifier evidence hash does not match"
        )
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ContractValidationError(
            "Codex CLI verifier evidence is not valid JSON"
        ) from None
    if not isinstance(payload, dict):
        raise ContractValidationError(
            "Codex CLI verifier evidence must be an object"
        )
    if (
        payload.get("schema_version") != "cera.codex_cli_verifier_probe.v1"
        or payload.get("goal_authority_id") != "D-150"
        or payload.get("qualification_id")
        != ACTIVE_CODEX_CLI_VERIFIER_QUALIFICATION_ID
        or payload.get("status") != "passed"
        or payload.get("case_count") != 4
        or payload.get("passed_case_count") != 4
        or payload.get("failed_case_index") is not None
        or payload.get("external_provider_calls_observed") != 4
        or payload.get("model") != expected_model
        or payload.get("reasoning_effort") != "medium"
        or payload.get("route_sha256") != expected_route_sha256
        or payload.get("transport_name") != CODEX_CLI_EXEC_TRANSPORT_NAME
        or payload.get("transport_version") != CODEX_CLI_EXEC_VERSION
        or payload.get("pinned_cli_version") != CODEX_CLI_EXEC_VERSION
        or payload.get("transport_compatibility_id")
        != CODEX_CLI_EXEC_COMPATIBILITY_ID
        or payload.get("transport_compatibility_source_sha256")
        != CODEX_CLI_EXEC_CONTRACT_SHA256
        or payload.get("provider_schema_sha256")
        != expected_provider_schema_sha256
        or payload.get("automatic_retry_count") != 0
        or payload.get("fallback_enabled") is not False
        or payload.get("story_authority_writes") != 0
        or payload.get("all_workspaces_empty") is not True
        or any(
            payload.get(field) is not False
            for field in (
                "retains_prompt",
                "retains_raw_output",
                "retains_story_prose",
            )
        )
    ):
        raise ContractValidationError(
            "Codex CLI verifier qualification did not pass its pinned route"
        )

    stages = payload.get("provider_stages")
    calls = payload.get("calls")
    if (
        not isinstance(stages, list)
        or len(stages) != 4
        or not isinstance(calls, list)
        or len(calls) != 4
    ):
        raise ContractValidationError(
            "Codex CLI verifier qualification call inventory is incomplete"
        )
    for index, stage in enumerate(stages, start=1):
        if stage != {
            "stage": f"cli_verifier_probe_{index}",
            "provider_category": "sol",
            "dispatch_started": True,
        }:
            raise ContractValidationError(
                "Codex CLI verifier dispatch evidence is incompatible"
            )

    evidence_ids: set[str] = set()
    request_ids: set[str] = set()
    provider_receipt_ids: set[str] = set()
    provider_request_hashes: set[str] = set()
    accepted_cases = 0
    rejected_cases = 0
    for index, (call, expected) in enumerate(
        zip(calls, _CASE_EXPECTATIONS, strict=True),
        start=1,
    ):
        if not isinstance(call, dict):
            raise ContractValidationError(
                "Codex CLI verifier call evidence is malformed"
            )
        case_name, status, violation, participant_count, beat_count = expected
        if (
            call.get("index") != index
            or call.get("case_name") != case_name
            or call.get("status") != "passed"
            or call.get("dispatch_started") is not True
            or call.get("expected_status") != status
            or call.get("expected_violation") != violation
            or call.get("selected_participant_count") != participant_count
            or call.get("expected_beat_count") != beat_count
            or call.get("external_provider_calls_observed") != 1
            or call.get("automatic_retry_count") != 0
            or call.get("fallback_enabled") is not False
            or call.get("story_authority_writes") != 0
            or call.get("workspace_empty_after_call") is not True
            or any(
                call.get(field) is not False
                for field in (
                    "retains_prompt",
                    "retains_raw_output",
                    "retains_story_prose",
                )
            )
        ):
            raise ContractValidationError(
                "Codex CLI verifier call did not preserve its case contract"
            )
        evidence_id = _typed_identity(
            call.get("evidence_identity"),
            IdKind.EVALUATION_RUN,
            "evidence",
        )
        request_id = _typed_identity(
            call.get("request_id"),
            IdKind.REQUEST,
            "request",
        )
        if evidence_id in evidence_ids or request_id in request_ids:
            raise ContractValidationError(
                "Codex CLI verifier call identities are not unique"
            )
        evidence_ids.add(evidence_id)
        request_ids.add(request_id)
        for field in ("request_sha256", "candidate_sha256"):
            _sha256(call.get(field), f"Codex CLI verifier {field}")

        provider_receipt = call.get("provider_receipt")
        verification_receipt = call.get("verification_receipt")
        if not isinstance(provider_receipt, dict) or not isinstance(
            verification_receipt,
            dict,
        ):
            raise ContractValidationError(
                "Codex CLI verifier receipts are incomplete"
            )
        _provider_receipt(
            provider_receipt,
            expected_model=expected_model,
            expected_route_sha256=expected_route_sha256,
        )
        provider_receipt_id = provider_receipt["provider_receipt_id"]
        provider_request_hash = provider_receipt[
            "provider_request_id_sha256"
        ]
        if (
            provider_receipt_id in provider_receipt_ids
            or provider_request_hash in provider_request_hashes
        ):
            raise ContractValidationError(
                "Codex CLI verifier provider calls are not unique"
            )
        provider_receipt_ids.add(provider_receipt_id)
        provider_request_hashes.add(provider_request_hash)

        findings = verification_receipt.get("violation_finding_sha256s")
        codes = verification_receipt.get("violation_codes")
        if (
            verification_receipt.get("schema_version")
            != "cera.scene_realization_verification_receipt.v4"
            or verification_receipt.get("status") != status
            or verification_receipt.get("qualification_eligible") is not True
            or verification_receipt.get("external_provider_calls") != 1
            or verification_receipt.get("authoritative_store_writes") != 0
            or verification_receipt.get("retains_story_prose") is not False
            or verification_receipt.get("provider_receipt_id")
            != provider_receipt_id
            or verification_receipt.get("candidate_sha256")
            != call.get("candidate_sha256")
            or verification_receipt.get("story_text_sha256")
            != call.get("candidate_sha256")
            or verification_receipt.get("verification_request_sha256")
            != call.get("request_sha256")
            or verification_receipt.get("verifier_adapter_evidence_sha256")
            != expected_route_sha256
            or verification_receipt.get("verified_boundary_checks")
            != ["protected_user_no_unsupplied_realization"]
            or len(verification_receipt.get("verified_participant_ids", ()))
            != participant_count
            or len(verification_receipt.get("verified_beat_ids", ()))
            != beat_count
        ):
            raise ContractValidationError(
                "Codex CLI verifier semantic receipt is incompatible"
            )
        if status == "accepted":
            if codes != [] or findings != []:
                raise ContractValidationError(
                    "Codex CLI accepted case contains violation evidence"
                )
            accepted_cases += 1
        else:
            if codes != [violation] or not isinstance(findings, list) or not findings:
                raise ContractValidationError(
                    "Codex CLI rejected case lacks anchored violation evidence"
                )
            for finding in findings:
                _sha256(finding, "Codex CLI violation finding")
            rejected_cases += 1

    return CodexCliVerifierQualificationEvidence(
        schema_version="cera.codex_cli_verifier_qualification_evidence.v1",
        qualification_id=ACTIVE_CODEX_CLI_VERIFIER_QUALIFICATION_ID,
        summary_sha256=actual_sha256,
        route_sha256=expected_route_sha256,
        requested_model=expected_model,
        verifier_calls=4,
        accepted_cases=accepted_cases,
        rejected_cases=rejected_cases,
        transport_name=CODEX_CLI_EXEC_TRANSPORT_NAME,
        transport_version=CODEX_CLI_EXEC_VERSION,
        transport_compatibility_id=CODEX_CLI_EXEC_COMPATIBILITY_ID,
        transport_compatibility_source_sha256=(
            CODEX_CLI_EXEC_CONTRACT_SHA256
        ),
    )


def _typed_identity(value: object, kind: IdKind, label: str) -> str:
    if not isinstance(value, str):
        raise ContractValidationError(
            f"Codex CLI verifier {label} identity is invalid"
        )
    try:
        TypedId.parse(value, kind)
    except IdentityError:
        raise ContractValidationError(
            f"Codex CLI verifier {label} identity is invalid"
        ) from None
    return value


def _provider_receipt(
    receipt: dict[str, Any],
    *,
    expected_model: str,
    expected_route_sha256: str,
) -> None:
    if (
        receipt.get("schema_version") != "cera.live_provider_call_receipt.v2"
        or receipt.get("provider") != "openai_codex"
        or receipt.get("role") != "scene_realization_verifier"
        or receipt.get("requested_model") != expected_model
        or receipt.get("returned_model") != expected_model
        or receipt.get("route_sha256") != expected_route_sha256
        or receipt.get("external_provider_calls") != 1
        or receipt.get("automatic_retry_count") != 0
        or receipt.get("story_authority_writes") != 0
        or receipt.get("quota_metered") is not True
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
            "Codex CLI verifier provider receipt is incompatible"
        )
    _typed_identity(
        receipt.get("provider_receipt_id"),
        IdKind.PROVIDER_RECEIPT,
        "provider receipt",
    )
    for field in (
        "provider_request_id_sha256",
        "request_sha256",
        "output_sha256",
    ):
        _sha256(receipt.get(field), f"Codex CLI verifier receipt {field}")


def _sha256(value: object, label: str) -> None:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ContractValidationError(f"{label} is invalid")
