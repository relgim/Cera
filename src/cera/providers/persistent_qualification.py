"""Validate the immutable live evidence that authorizes persistent Codex reuse."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cera.errors import ContractValidationError, IdentityError
from cera.ids import IdKind, TypedId
from cera.serialization import bytes_sha256

from .codex_sdk_compat import (
    EXPECTED_ROUTE_NOTIFICATION_SHA256,
    LEGACY_CODEX_SDK_COMPATIBILITY_ID,
    PREVIOUS_CODEX_SDK_COMPATIBILITY_ID,
    SUPPORTED_SDK_VERSION,
)

_ROLE_SEQUENCE = (
    ("scene_reasoner", 1),
    ("scene_reasoner", 2),
    ("scene_realization_verifier", 1),
    ("scene_realization_verifier", 2),
)


@dataclass(frozen=True, slots=True)
class PersistentCodexQualificationEvidence:
    schema_version: str
    qualification_id: str
    summary_sha256: str
    requested_model: str
    reasoner_calls: int
    verifier_calls: int


@dataclass(frozen=True, slots=True)
class PersistentCodexRotationQualificationEvidence:
    schema_version: str
    qualification_id: str
    summary_sha256: str
    requested_model: str
    verifier_calls: int
    process_launches: int
    maximum_requests_per_process: int


@dataclass(frozen=True, slots=True)
class PersistentCodexTreeCleanupQualificationEvidence:
    schema_version: str
    qualification_id: str
    summary_sha256: str
    requested_model: str
    verifier_calls: int
    process_launches: int
    maximum_requests_per_process: int
    cleanup_policy: str


@dataclass(frozen=True, slots=True)
class PersistentCodexVerifierEpochQualificationEvidence:
    schema_version: str
    qualification_id: str
    summary_sha256: str
    requested_model: str
    verifier_calls: int
    process_launches: int
    maximum_requests_per_process: int
    cleanup_policy: str
    sdk_compatibility_id: str
    sdk_version: str
    route_notification_source_sha256: str


@dataclass(frozen=True, slots=True)
class PersistentCodexCompletionRegistrationQualificationEvidence:
    schema_version: str
    qualification_id: str
    summary_sha256: str
    route_sha256: str
    requested_model: str
    verifier_calls: int
    process_launches: int
    maximum_requests_per_process: int
    cleanup_policy: str
    sdk_compatibility_id: str
    sdk_version: str
    route_notification_source_sha256: str


def load_persistent_codex_qualification(
    path: Path,
    *,
    expected_summary_sha256: str,
    expected_model: str,
) -> PersistentCodexQualificationEvidence:
    """Fail closed unless ``path`` proves the exact four-call reuse contract."""

    if not path.is_absolute() or not path.is_file():
        raise ContractValidationError(
            "persistent Codex qualification evidence must be an existing absolute file"
        )
    raw = path.read_bytes()
    actual_sha256 = bytes_sha256(raw)
    if actual_sha256 != expected_summary_sha256:
        raise ContractValidationError("persistent Codex qualification evidence hash does not match")
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ContractValidationError(
            "persistent Codex qualification evidence is not valid JSON"
        ) from None
    if not isinstance(payload, dict):
        raise ContractValidationError("persistent Codex qualification evidence must be an object")
    if (
        payload.get("schema_version") != "cera.persistent_codex_transport_probe.v1"
        or payload.get("status") != "passed"
        or payload.get("calls_required") != 4
        or payload.get("successful_calls") != 4
        or payload.get("sol_dispatches") != 4
        or payload.get("deepseek_dispatches") != 0
        or payload.get("attempts_per_call") != 1
        or payload.get("retry_enabled") is not False
        or payload.get("fallback_enabled") is not False
        or payload.get("story_or_genesis_content_used") is not False
        or payload.get("mcp_enabled") is not False
        or payload.get("story_authority_writes") != 0
    ):
        raise ContractValidationError(
            "persistent Codex qualification did not pass the required safe route"
        )
    qualification_id = payload.get("qualification_id")
    if not isinstance(qualification_id, str) or not qualification_id.strip():
        raise ContractValidationError("persistent Codex qualification identity is missing")
    role_sessions = payload.get("role_sessions")
    if not isinstance(role_sessions, dict) or set(role_sessions) != {
        "scene_reasoner",
        "scene_realization_verifier",
    }:
        raise ContractValidationError("persistent Codex qualification role sessions are incomplete")
    expected_session = {"process_launch_count": 1, "request_submission_count": 2}
    if any(value != expected_session for value in role_sessions.values()):
        raise ContractValidationError("persistent Codex qualification did not prove process reuse")

    calls = payload.get("calls")
    if not isinstance(calls, list) or len(calls) != len(_ROLE_SEQUENCE):
        raise ContractValidationError("persistent Codex qualification requires exactly four calls")
    evidence_identities: set[str] = set()
    receipt_ids: set[str] = set()
    request_ids: set[str] = set()
    for index, (call, role_sequence) in enumerate(
        zip(calls, _ROLE_SEQUENCE, strict=True),
        start=1,
    ):
        if not isinstance(call, dict):
            raise ContractValidationError(
                "persistent Codex qualification call evidence is malformed"
            )
        expected_role, expected_sequence = role_sequence
        if (
            call.get("index") != index
            or call.get("role") != expected_role
            or call.get("sequence_in_role_session") != expected_sequence
            or call.get("status") != "passed"
            or call.get("dispatch_started") is not True
            or call.get("external_provider_calls_observed") != 1
            or call.get("automatic_retry_count") != 0
            or call.get("fallback_enabled") is not False
            or call.get("story_authority_writes") != 0
            or call.get("workspace_retained") is not False
        ):
            raise ContractValidationError(
                "persistent Codex qualification call did not pass its safe contract"
            )
        evidence_identity = call.get("evidence_identity")
        try:
            TypedId.parse(evidence_identity, IdKind.EVALUATION_RUN)
        except IdentityError:
            raise ContractValidationError(
                "persistent Codex qualification evidence identity is invalid"
            ) from None
        if evidence_identity in evidence_identities:
            raise ContractValidationError(
                "persistent Codex qualification evidence identities are invalid"
            )
        evidence_identities.add(evidence_identity)
        receipt = call.get("provider_receipt")
        if not isinstance(receipt, dict):
            raise ContractValidationError(
                "persistent Codex qualification provider receipt is missing"
            )
        _validate_provider_receipt(
            receipt,
            expected_role=expected_role,
            expected_model=expected_model,
        )
        receipt_id = receipt.get("provider_receipt_id")
        request_id = receipt.get("provider_request_id_sha256")
        if receipt_id in receipt_ids or request_id in request_ids:
            raise ContractValidationError(
                "persistent Codex qualification provider calls are not unique"
            )
        receipt_ids.add(receipt_id)
        request_ids.add(request_id)

    return PersistentCodexQualificationEvidence(
        schema_version="cera.persistent_codex_qualification_evidence.v1",
        qualification_id=qualification_id,
        summary_sha256=actual_sha256,
        requested_model=expected_model,
        reasoner_calls=2,
        verifier_calls=2,
    )


def load_persistent_codex_rotation_qualification(
    path: Path,
    *,
    expected_summary_sha256: str,
    expected_model: str,
) -> PersistentCodexRotationQualificationEvidence:
    """Fail closed unless ``path`` proves two bounded verifier epochs."""

    if not path.is_absolute() or not path.is_file():
        raise ContractValidationError(
            "persistent Codex rotation evidence must be an existing absolute file"
        )
    raw = path.read_bytes()
    actual_sha256 = bytes_sha256(raw)
    if actual_sha256 != expected_summary_sha256:
        raise ContractValidationError("persistent Codex rotation evidence hash does not match")
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ContractValidationError(
            "persistent Codex rotation evidence is not valid JSON"
        ) from None
    if not isinstance(payload, dict):
        raise ContractValidationError("persistent Codex rotation evidence must be an object")
    qualification_id = _validate_rotation_payload(
        payload,
        expected_model=expected_model,
        expected_schema="cera.persistent_codex_rotation_probe.v1",
        require_cleanup_policy=False,
    )
    return PersistentCodexRotationQualificationEvidence(
        schema_version="cera.persistent_codex_rotation_qualification_evidence.v1",
        qualification_id=qualification_id,
        summary_sha256=actual_sha256,
        requested_model=expected_model,
        verifier_calls=4,
        process_launches=2,
        maximum_requests_per_process=2,
    )


def load_persistent_codex_tree_cleanup_qualification(
    path: Path,
    *,
    expected_summary_sha256: str,
    expected_model: str,
) -> PersistentCodexTreeCleanupQualificationEvidence:
    """Fail closed unless ``path`` proves replacement after full-tree cleanup."""

    if not path.is_absolute() or not path.is_file():
        raise ContractValidationError(
            "persistent Codex tree-cleanup evidence must be an existing absolute file"
        )
    raw = path.read_bytes()
    actual_sha256 = bytes_sha256(raw)
    if actual_sha256 != expected_summary_sha256:
        raise ContractValidationError("persistent Codex tree-cleanup evidence hash does not match")
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ContractValidationError(
            "persistent Codex tree-cleanup evidence is not valid JSON"
        ) from None
    if not isinstance(payload, dict):
        raise ContractValidationError("persistent Codex tree-cleanup evidence must be an object")
    qualification_id = _validate_rotation_payload(
        payload,
        expected_model=expected_model,
        expected_schema="cera.persistent_codex_tree_cleanup_probe.v1",
        require_cleanup_policy=True,
    )
    return PersistentCodexTreeCleanupQualificationEvidence(
        schema_version=("cera.persistent_codex_tree_cleanup_qualification_evidence.v1"),
        qualification_id=qualification_id,
        summary_sha256=actual_sha256,
        requested_model=expected_model,
        verifier_calls=4,
        process_launches=2,
        maximum_requests_per_process=2,
        cleanup_policy="force_full_tree_before_replacement",
    )


def load_persistent_codex_verifier_epoch_qualification(
    path: Path,
    *,
    expected_summary_sha256: str,
    expected_model: str,
) -> PersistentCodexVerifierEpochQualificationEvidence:
    """Fail closed unless the real verifier passed three shimmed epochs."""

    if not path.is_absolute() or not path.is_file():
        raise ContractValidationError(
            "persistent Codex verifier-epoch evidence must be an existing absolute file"
        )
    raw = path.read_bytes()
    actual_sha256 = bytes_sha256(raw)
    if actual_sha256 != expected_summary_sha256:
        raise ContractValidationError(
            "persistent Codex verifier-epoch evidence hash does not match"
        )
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ContractValidationError(
            "persistent Codex verifier-epoch evidence is not valid JSON"
        ) from None
    if not isinstance(payload, dict):
        raise ContractValidationError("persistent Codex verifier-epoch evidence must be an object")
    compatibility = payload.get("sdk_compatibility")
    if (
        payload.get("schema_version") != "cera.persistent_codex_verifier_epoch_probe.v1"
        or payload.get("status") != "passed"
        or payload.get("calls_required") != 6
        or payload.get("successful_calls") != 6
        or payload.get("sol_dispatches") != 6
        or payload.get("deepseek_dispatches") != 0
        or payload.get("attempts_per_call") != 1
        or payload.get("retry_enabled") is not False
        or payload.get("fallback_enabled") is not False
        or payload.get("canonical_story_or_genesis_content_used") is not False
        or payload.get("synthetic_non_story_candidate_used") is not True
        or payload.get("mcp_enabled") is not False
        or payload.get("story_authority_writes") != 0
        or payload.get("maximum_requests_per_process") != 2
        or payload.get("expected_process_launches") != 3
        or payload.get("process_launch_count") != 3
        or payload.get("request_submission_count") != 6
        or payload.get("process_tree_cleanup_policy") != "force_full_tree_before_replacement"
        or payload.get("replacement_dispatch_requires_cleanup_confirmation") is not True
        or payload.get("model") != expected_model
        or payload.get("reasoning_effort") != "medium"
        or not isinstance(compatibility, dict)
        or compatibility.get("compatibility_id") != LEGACY_CODEX_SDK_COMPATIBILITY_ID
        or compatibility.get("sdk_version") != SUPPORTED_SDK_VERSION
        or compatibility.get("route_notification_source_sha256")
        != EXPECTED_ROUTE_NOTIFICATION_SHA256
        or compatibility.get("activation_contract")
        != ("each successful transport result requires matching worker activation evidence")
    ):
        raise ContractValidationError(
            "persistent Codex verifier-epoch qualification did not pass its safe route"
        )
    qualification_id = payload.get("qualification_id")
    if not isinstance(qualification_id, str) or not qualification_id.strip():
        raise ContractValidationError(
            "persistent Codex verifier-epoch qualification identity is missing"
        )
    route_sha256 = payload.get("route_sha256")
    _validate_sha256(
        route_sha256,
        "persistent Codex verifier-epoch route hash",
    )

    calls = payload.get("calls")
    if not isinstance(calls, list) or len(calls) != 6:
        raise ContractValidationError(
            "persistent Codex verifier-epoch qualification requires exactly six calls"
        )
    evidence_identities: set[str] = set()
    request_ids: set[str] = set()
    receipt_ids: set[str] = set()
    provider_request_ids: set[str] = set()
    expected_launches = (1, 1, 2, 2, 3, 3)
    for index, call in enumerate(calls, start=1):
        if not isinstance(call, dict):
            raise ContractValidationError("persistent Codex verifier-epoch call is malformed")
        if (
            call.get("index") != index
            or call.get("role") != "scene_realization_verifier"
            or call.get("status") != "passed"
            or call.get("dispatch_started") is not True
            or call.get("external_provider_calls_observed") != 1
            or call.get("automatic_retry_count") != 0
            or call.get("fallback_enabled") is not False
            or call.get("story_authority_writes") != 0
            or call.get("workspace_retained") is not False
            or call.get("transport_compatibility_activation_validated") is not True
            or call.get("process_launch_count_after_call") != expected_launches[index - 1]
            or call.get("request_submission_count_after_call") != index
        ):
            raise ContractValidationError(
                "persistent Codex verifier-epoch call did not preserve bounded epochs"
            )
        evidence_identity = call.get("evidence_identity")
        request_id = call.get("request_id")
        try:
            TypedId.parse(evidence_identity, IdKind.EVALUATION_RUN)
            TypedId.parse(request_id, IdKind.REQUEST)
        except IdentityError:
            raise ContractValidationError(
                "persistent Codex verifier-epoch typed identity is invalid"
            ) from None
        if evidence_identity in evidence_identities or request_id in request_ids:
            raise ContractValidationError(
                "persistent Codex verifier-epoch identities are not unique"
            )
        evidence_identities.add(evidence_identity)
        request_ids.add(request_id)
        for field in ("request_sha256", "candidate_sha256"):
            _validate_sha256(
                call.get(field),
                f"persistent Codex verifier-epoch {field}",
            )

        provider_receipt = call.get("provider_receipt")
        verification_receipt = call.get("verification_receipt")
        if not isinstance(provider_receipt, dict) or not isinstance(
            verification_receipt,
            dict,
        ):
            raise ContractValidationError("persistent Codex verifier-epoch receipts are incomplete")
        _validate_provider_receipt(
            provider_receipt,
            expected_role="scene_realization_verifier",
            expected_model=expected_model,
        )
        if (
            verification_receipt.get("schema_version")
            != "cera.scene_realization_verification_receipt.v4"
            or verification_receipt.get("status") != "accepted"
            or verification_receipt.get("qualification_eligible") is not True
            or verification_receipt.get("external_provider_calls") != 1
            or verification_receipt.get("authoritative_store_writes") != 0
            or verification_receipt.get("retains_story_prose") is not False
            or verification_receipt.get("provider_receipt_id")
            != provider_receipt.get("provider_receipt_id")
            or verification_receipt.get("candidate_sha256") != call.get("candidate_sha256")
            or verification_receipt.get("story_text_sha256") != call.get("candidate_sha256")
            or verification_receipt.get("verification_request_sha256") != call.get("request_sha256")
            or verification_receipt.get("verifier_adapter_evidence_sha256") != route_sha256
            or verification_receipt.get("violation_codes") != []
            or verification_receipt.get("violation_finding_sha256s") != []
        ):
            raise ContractValidationError(
                "persistent Codex verifier-epoch semantic receipt is incompatible"
            )
        provider_receipt_id = provider_receipt.get("provider_receipt_id")
        provider_request_id = provider_receipt.get("provider_request_id_sha256")
        if provider_receipt_id in receipt_ids or provider_request_id in provider_request_ids:
            raise ContractValidationError(
                "persistent Codex verifier-epoch provider calls are not unique"
            )
        receipt_ids.add(provider_receipt_id)
        provider_request_ids.add(provider_request_id)

    return PersistentCodexVerifierEpochQualificationEvidence(
        schema_version=("cera.persistent_codex_verifier_epoch_qualification_evidence.v1"),
        qualification_id=qualification_id,
        summary_sha256=actual_sha256,
        requested_model=expected_model,
        verifier_calls=6,
        process_launches=3,
        maximum_requests_per_process=2,
        cleanup_policy="force_full_tree_before_replacement",
        sdk_compatibility_id=LEGACY_CODEX_SDK_COMPATIBILITY_ID,
        sdk_version=SUPPORTED_SDK_VERSION,
        route_notification_source_sha256=(EXPECTED_ROUTE_NOTIFICATION_SHA256),
    )


def load_persistent_codex_completion_registration_qualification(
    path: Path,
    *,
    expected_summary_sha256: str,
    expected_model: str,
    expected_route_sha256: str,
) -> PersistentCodexCompletionRegistrationQualificationEvidence:
    """Load a five-epoch proof for its exact immutable route binding.

    The caller must supply the route hash recorded for that qualification;
    changing the active verifier adapter does not retroactively promote it.
    """

    if not path.is_absolute() or not path.is_file():
        raise ContractValidationError(
            "Codex completion-registration evidence must be an existing absolute file"
        )
    raw = path.read_bytes()
    actual_sha256 = bytes_sha256(raw)
    if actual_sha256 != expected_summary_sha256:
        raise ContractValidationError("Codex completion-registration evidence hash does not match")
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ContractValidationError(
            "Codex completion-registration evidence is not valid JSON"
        ) from None
    if not isinstance(payload, dict):
        raise ContractValidationError("Codex completion-registration evidence must be an object")
    compatibility = payload.get("sdk_compatibility")
    if (
        payload.get("schema_version") != "cera.persistent_codex_verifier_epoch_probe.v2"
        or payload.get("qualification_id") != "persistent-codex-verifier-epoch-probe-v5"
        or payload.get("status") != "passed"
        or payload.get("calls_required") != 10
        or payload.get("successful_calls") != 10
        or payload.get("sol_dispatches") != 10
        or payload.get("deepseek_dispatches") != 0
        or payload.get("attempts_per_call") != 1
        or payload.get("retry_enabled") is not False
        or payload.get("fallback_enabled") is not False
        or payload.get("canonical_story_or_genesis_content_used") is not False
        or payload.get("synthetic_non_story_candidate_used") is not True
        or payload.get("mcp_enabled") is not False
        or payload.get("story_authority_writes") != 0
        or payload.get("maximum_requests_per_process") != 2
        or payload.get("expected_process_launches") != 5
        or payload.get("process_launch_count") != 5
        or payload.get("request_submission_count") != 10
        or payload.get("process_tree_cleanup_policy") != "force_full_tree_before_replacement"
        or payload.get("replacement_dispatch_requires_cleanup_confirmation") is not True
        or payload.get("returned_turn_pre_registration_required") is not True
        or payload.get("model") != expected_model
        or payload.get("reasoning_effort") != "medium"
        or payload.get("route_sha256") != expected_route_sha256
        or not isinstance(compatibility, dict)
        or compatibility.get("compatibility_id")
        != PREVIOUS_CODEX_SDK_COMPATIBILITY_ID
        or compatibility.get("sdk_version") != SUPPORTED_SDK_VERSION
        or compatibility.get("route_notification_source_sha256")
        != EXPECTED_ROUTE_NOTIFICATION_SHA256
        or compatibility.get("activation_contract")
        != (
            "each successful transport result requires matching worker "
            "activation evidence and a pre-registered returned-turn queue"
        )
    ):
        raise ContractValidationError(
            "Codex completion-registration qualification did not pass its safe route"
        )
    _validate_sha256(
        expected_route_sha256,
        "Codex completion-registration route hash",
    )

    calls = payload.get("calls")
    if not isinstance(calls, list) or len(calls) != 10:
        raise ContractValidationError(
            "Codex completion-registration qualification requires ten calls"
        )
    evidence_identities: set[str] = set()
    request_ids: set[str] = set()
    receipt_ids: set[str] = set()
    provider_request_ids: set[str] = set()
    expected_launches = (1, 1, 2, 2, 3, 3, 4, 4, 5, 5)
    for index, call in enumerate(calls, start=1):
        if not isinstance(call, dict):
            raise ContractValidationError("Codex completion-registration call is malformed")
        if (
            call.get("index") != index
            or call.get("role") != "scene_realization_verifier"
            or call.get("status") != "passed"
            or call.get("dispatch_started") is not True
            or call.get("external_provider_calls_observed") != 1
            or call.get("automatic_retry_count") != 0
            or call.get("fallback_enabled") is not False
            or call.get("story_authority_writes") != 0
            or call.get("workspace_retained") is not False
            or call.get("transport_compatibility_activation_validated") is not True
            or call.get("process_launch_count_after_call") != expected_launches[index - 1]
            or call.get("request_submission_count_after_call") != index
        ):
            raise ContractValidationError(
                "Codex completion-registration call did not preserve bounded epochs"
            )
        evidence_identity = call.get("evidence_identity")
        request_id = call.get("request_id")
        try:
            TypedId.parse(evidence_identity, IdKind.EVALUATION_RUN)
            TypedId.parse(request_id, IdKind.REQUEST)
        except IdentityError:
            raise ContractValidationError(
                "Codex completion-registration typed identity is invalid"
            ) from None
        if evidence_identity in evidence_identities or request_id in request_ids:
            raise ContractValidationError("Codex completion-registration identities are not unique")
        evidence_identities.add(evidence_identity)
        request_ids.add(request_id)
        for field in ("request_sha256", "candidate_sha256"):
            _validate_sha256(
                call.get(field),
                f"Codex completion-registration {field}",
            )

        provider_receipt = call.get("provider_receipt")
        verification_receipt = call.get("verification_receipt")
        if not isinstance(provider_receipt, dict) or not isinstance(
            verification_receipt,
            dict,
        ):
            raise ContractValidationError("Codex completion-registration receipts are incomplete")
        _validate_provider_receipt(
            provider_receipt,
            expected_role="scene_realization_verifier",
            expected_model=expected_model,
        )
        if (
            provider_receipt.get("route_sha256") != expected_route_sha256
            or verification_receipt.get("schema_version")
            != "cera.scene_realization_verification_receipt.v4"
            or verification_receipt.get("status") != "accepted"
            or verification_receipt.get("qualification_eligible") is not True
            or verification_receipt.get("external_provider_calls") != 1
            or verification_receipt.get("authoritative_store_writes") != 0
            or verification_receipt.get("retains_story_prose") is not False
            or verification_receipt.get("provider_receipt_id")
            != provider_receipt.get("provider_receipt_id")
            or verification_receipt.get("candidate_sha256") != call.get("candidate_sha256")
            or verification_receipt.get("story_text_sha256") != call.get("candidate_sha256")
            or verification_receipt.get("verification_request_sha256") != call.get("request_sha256")
            or verification_receipt.get("verifier_adapter_evidence_sha256") != expected_route_sha256
            or verification_receipt.get("violation_codes") != []
            or verification_receipt.get("violation_finding_sha256s") != []
        ):
            raise ContractValidationError(
                "Codex completion-registration semantic receipt is incompatible"
            )
        provider_receipt_id = provider_receipt.get("provider_receipt_id")
        provider_request_id = provider_receipt.get("provider_request_id_sha256")
        if provider_receipt_id in receipt_ids or provider_request_id in provider_request_ids:
            raise ContractValidationError(
                "Codex completion-registration provider calls are not unique"
            )
        receipt_ids.add(provider_receipt_id)
        provider_request_ids.add(provider_request_id)

    return PersistentCodexCompletionRegistrationQualificationEvidence(
        schema_version=("cera.persistent_codex_completion_registration_qualification_evidence.v1"),
        qualification_id=payload["qualification_id"],
        summary_sha256=actual_sha256,
        route_sha256=expected_route_sha256,
        requested_model=expected_model,
        verifier_calls=10,
        process_launches=5,
        maximum_requests_per_process=2,
        cleanup_policy="force_full_tree_before_replacement",
        sdk_compatibility_id=PREVIOUS_CODEX_SDK_COMPATIBILITY_ID,
        sdk_version=SUPPORTED_SDK_VERSION,
        route_notification_source_sha256=(EXPECTED_ROUTE_NOTIFICATION_SHA256),
    )


def _validate_rotation_payload(
    payload: dict[str, Any],
    *,
    expected_model: str,
    expected_schema: str,
    require_cleanup_policy: bool,
) -> str:
    if (
        payload.get("schema_version") != expected_schema
        or payload.get("status") != "passed"
        or payload.get("calls_required") != 4
        or payload.get("successful_calls") != 4
        or payload.get("sol_dispatches") != 4
        or payload.get("deepseek_dispatches") != 0
        or payload.get("attempts_per_call") != 1
        or payload.get("retry_enabled") is not False
        or payload.get("fallback_enabled") is not False
        or payload.get("story_or_genesis_content_used") is not False
        or payload.get("mcp_enabled") is not False
        or payload.get("story_authority_writes") != 0
        or payload.get("maximum_requests_per_process") != 2
        or payload.get("expected_process_launches") != 2
        or payload.get("process_launch_count") != 2
        or payload.get("request_submission_count") != 4
        or (
            require_cleanup_policy
            and (
                payload.get("process_tree_cleanup_policy") != "force_full_tree_before_replacement"
                or payload.get("replacement_dispatch_requires_cleanup_confirmation") is not True
            )
        )
    ):
        raise ContractValidationError(
            "persistent Codex process-epoch qualification did not pass its safe route"
        )
    qualification_id = payload.get("qualification_id")
    if not isinstance(qualification_id, str) or not qualification_id.strip():
        raise ContractValidationError(
            "persistent Codex process-epoch qualification identity is missing"
        )
    calls = payload.get("calls")
    if not isinstance(calls, list) or len(calls) != 4:
        raise ContractValidationError(
            "persistent Codex process-epoch qualification requires exactly four calls"
        )
    evidence_identities: set[str] = set()
    receipt_ids: set[str] = set()
    request_ids: set[str] = set()
    expected_launches = (1, 1, 2, 2)
    for index, call in enumerate(calls, start=1):
        if not isinstance(call, dict):
            raise ContractValidationError(
                "persistent Codex process-epoch qualification call is malformed"
            )
        if (
            call.get("index") != index
            or call.get("role") != "scene_realization_verifier"
            or call.get("sequence_in_probe") != index
            or call.get("status") != "passed"
            or call.get("dispatch_started") is not True
            or call.get("external_provider_calls_observed") != 1
            or call.get("automatic_retry_count") != 0
            or call.get("fallback_enabled") is not False
            or call.get("story_authority_writes") != 0
            or call.get("workspace_retained") is not False
            or call.get("process_launch_count_after_call") != expected_launches[index - 1]
            or call.get("request_submission_count_after_call") != index
        ):
            raise ContractValidationError(
                "persistent Codex process-epoch call did not preserve bounded epochs"
            )
        evidence_identity = call.get("evidence_identity")
        try:
            TypedId.parse(evidence_identity, IdKind.EVALUATION_RUN)
        except IdentityError:
            raise ContractValidationError(
                "persistent Codex process-epoch evidence identity is invalid"
            ) from None
        if evidence_identity in evidence_identities:
            raise ContractValidationError(
                "persistent Codex process-epoch identities are not unique"
            )
        evidence_identities.add(evidence_identity)
        receipt = call.get("provider_receipt")
        if not isinstance(receipt, dict):
            raise ContractValidationError(
                "persistent Codex process-epoch provider receipt is missing"
            )
        _validate_provider_receipt(
            receipt,
            expected_role="scene_realization_verifier",
            expected_model=expected_model,
        )
        receipt_id = receipt.get("provider_receipt_id")
        request_id = receipt.get("provider_request_id_sha256")
        if receipt_id in receipt_ids or request_id in request_ids:
            raise ContractValidationError(
                "persistent Codex process-epoch provider calls are not unique"
            )
        receipt_ids.add(receipt_id)
        request_ids.add(request_id)
    return qualification_id


def _validate_provider_receipt(
    receipt: dict[str, Any],
    *,
    expected_role: str,
    expected_model: str,
) -> None:
    if (
        receipt.get("schema_version") != "cera.live_provider_call_receipt.v2"
        or receipt.get("provider") != "openai_codex"
        or receipt.get("role") != expected_role
        or receipt.get("requested_model") != expected_model
        or receipt.get("returned_model") != expected_model
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
            "persistent Codex qualification provider receipt is incompatible"
        )
    provider_receipt_id = receipt.get("provider_receipt_id")
    try:
        TypedId.parse(provider_receipt_id, IdKind.PROVIDER_RECEIPT)
    except IdentityError:
        raise ContractValidationError(
            "persistent Codex qualification provider receipt identity is invalid"
        ) from None
    for field in (
        "provider_request_id_sha256",
        "request_sha256",
        "output_sha256",
    ):
        _validate_sha256(
            receipt.get(field),
            "persistent Codex qualification provider receipt hash",
        )


def _validate_sha256(value: object, field_name: str) -> None:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ContractValidationError(f"{field_name} is invalid")
