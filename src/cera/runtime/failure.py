"""Privacy-safe failure evidence and restart-auditable stage records."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import json
import re
from typing import ClassVar

from cera.errors import ContractValidationError, ErrorCode
from cera.ids import IdKind, TypedId, require_kind
from cera.schema import require_schema
from cera.serialization import canonical_json, domain_sha256, re_is_sha256, to_primitive


_SAFE_RECEIPT_SCHEMAS = frozenset(
    {
        "cera.live_provider_call_receipt.v2",
        "cera.provider_failure_call_receipt.v1",
        "cera.mcp_evidence_bridge_receipt.v2",
        "cera.evidence_lookup_receipt.v1",
        "cera.seed_dossier_receipt.v1",
        "cera.scene_reasoner_receipt.v2",
        "cera.composer_context_assembly_receipt.v1",
        "cera.scene_composer_receipt.v2",
        "cera.composer_validation_receipt.v1",
        "cera.final_story_acceptance_receipt.v1",
        "cera.scene_realization_verification_receipt.v4",
        "cera.scene_realization_verification_receipt.v5",
        "cera.adult_craft_selection_receipt.v1",
        "cera.specificity_validation_receipt.v1",
        "cera.semantic_specificity_receipt.v1",
        "cera.beat_scoped_repair_receipt.v1",
    }
)
_FORBIDDEN_RECEIPT_KEYS = frozenset(
    {
        "story_text",
        "accepted_prose",
        "exact_text",
        "selected_text",
        "sections_json",
        "craft_text",
        "prompt",
        "messages",
        "parsed_json",
        "output_text",
        "raw_source",
        "private_evidence",
        "secret",
        "authorization_header",
    }
)


@dataclass(frozen=True, slots=True)
class FailureEvidenceHandle:
    evidence_kind: str
    evidence_id: TypedId
    evidence_sha256: str

    def __post_init__(self) -> None:
        if not self.evidence_kind.strip():
            raise ContractValidationError("failure evidence kind is required")
        if not re_is_sha256(self.evidence_sha256):
            raise ContractValidationError("failure evidence hash is invalid")


@dataclass(frozen=True, slots=True)
class PrivacySafeReceiptPayload:
    """Canonical safe receipt payload retained across a later failed stage."""

    evidence_kind: str
    evidence_id: TypedId
    evidence_sha256: str
    receipt_schema_version: str
    payload_json: str
    payload_sha256: str

    def __post_init__(self) -> None:
        if not self.evidence_kind.strip():
            raise ContractValidationError("safe receipt evidence kind is required")
        if not re_is_sha256(self.evidence_sha256) or not re_is_sha256(
            self.payload_sha256
        ):
            raise ContractValidationError("safe receipt payload hash is invalid")
        if self.receipt_schema_version not in _SAFE_RECEIPT_SCHEMAS:
            raise ContractValidationError("receipt schema is not privacy-safe for retention")
        try:
            payload = json.loads(self.payload_json)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ContractValidationError("safe receipt payload is not valid JSON") from exc
        if not isinstance(payload, dict) or canonical_json(payload) != self.payload_json:
            raise ContractValidationError("safe receipt payload must be canonical JSON")
        if payload.get("schema_version") != self.receipt_schema_version:
            raise ContractValidationError("safe receipt payload schema changed")
        if str(self.evidence_id) not in self.payload_json:
            raise ContractValidationError("safe receipt payload does not contain its evidence ID")
        if _contains_forbidden_key(payload):
            raise ContractValidationError("safe receipt payload contains protected content")
        expected = domain_sha256("cera.privacy_safe_receipt_payload.v1", payload)
        if self.payload_sha256 != expected:
            raise ContractValidationError("safe receipt payload hash changed")

    @classmethod
    def from_receipt(
        cls,
        evidence_kind: str,
        evidence_id: TypedId,
        evidence_sha256: str,
        receipt,
    ) -> "PrivacySafeReceiptPayload":
        payload = to_primitive(receipt)
        if not isinstance(payload, dict):
            raise ContractValidationError("safe receipt must serialize to an object")
        payload_json = canonical_json(payload)
        return cls(
            evidence_kind=evidence_kind,
            evidence_id=evidence_id,
            evidence_sha256=evidence_sha256,
            receipt_schema_version=str(payload.get("schema_version", "")),
            payload_json=payload_json,
            payload_sha256=domain_sha256(
                "cera.privacy_safe_receipt_payload.v1", payload
            ),
        )


@dataclass(frozen=True, slots=True)
class TurnFailureEvidenceBundle:
    SCHEMA_VERSION: ClassVar[str] = "cera.turn_failure_evidence_bundle.v1"

    schema_version: str
    failure_bundle_id: TypedId
    request_id: TypedId
    branch_id: TypedId
    generation_id: TypedId
    stage: str
    error_code: ErrorCode
    safe_message: str
    input_sha256: str | None
    output_sha256: str | None
    retained_evidence: tuple[FailureEvidenceHandle, ...]
    external_provider_calls_observed: int
    story_state_committed: bool
    authoritative_store_writes: int
    retains_raw_source: bool
    retains_story_prose: bool
    retains_private_evidence: bool
    retains_prompt: bool
    retains_secret: bool

    def __post_init__(self) -> None:
        require_schema(self.schema_version, self.SCHEMA_VERSION, type(self).__name__)
        require_kind(
            self.failure_bundle_id,
            IdKind.FAILURE_BUNDLE,
            "failure_bundle_id",
        )
        require_kind(self.request_id, IdKind.REQUEST, "request_id")
        require_kind(self.branch_id, IdKind.BRANCH, "branch_id")
        require_kind(self.generation_id, IdKind.GENERATION, "generation_id")
        if not self.stage.strip() or not self.safe_message.strip():
            raise ContractValidationError(
                "failure evidence requires stage and safe message"
            )
        for value in (self.input_sha256, self.output_sha256):
            if value is not None and not re_is_sha256(value):
                raise ContractValidationError("failure input/output hash is invalid")
        keys = tuple(
            (value.evidence_kind, str(value.evidence_id))
            for value in self.retained_evidence
        )
        if len(keys) != len(set(keys)):
            raise ContractValidationError(
                "failure evidence handles must not be duplicated"
            )
        if (
            self.external_provider_calls_observed < 0
            or self.authoritative_store_writes != 0
            or self.story_state_committed
        ):
            raise ContractValidationError(
                "failure evidence cannot claim a story-authority mutation"
            )
        if any(
            (
                self.retains_raw_source,
                self.retains_story_prose,
                self.retains_private_evidence,
                self.retains_prompt,
                self.retains_secret,
            )
        ):
            raise ContractValidationError(
                "failure evidence bundle cannot retain protected content"
            )

    @property
    def bundle_sha256(self) -> str:
        return domain_sha256("cera.turn_failure_evidence_bundle.v1", self)


@dataclass(frozen=True, slots=True)
class TurnFailureEvidenceBundleV2:
    """Failure bundle that preserves safe receipt payloads, never story prose."""

    SCHEMA_VERSION: ClassVar[str] = "cera.turn_failure_evidence_bundle.v2"

    schema_version: str
    failure_bundle_id: TypedId
    request_id: TypedId
    branch_id: TypedId
    generation_id: TypedId
    stage: str
    error_code: ErrorCode
    safe_message: str
    input_sha256: str | None
    output_sha256: str | None
    retained_evidence: tuple[FailureEvidenceHandle, ...]
    retained_receipt_payloads: tuple[PrivacySafeReceiptPayload, ...]
    external_provider_calls_observed: int
    story_state_committed: bool
    authoritative_store_writes: int
    retains_raw_source: bool
    retains_story_prose: bool
    retains_private_evidence: bool
    retains_prompt: bool
    retains_secret: bool

    def __post_init__(self) -> None:
        require_schema(self.schema_version, self.SCHEMA_VERSION, type(self).__name__)
        require_kind(self.failure_bundle_id, IdKind.FAILURE_BUNDLE, "failure_bundle_id")
        require_kind(self.request_id, IdKind.REQUEST, "request_id")
        require_kind(self.branch_id, IdKind.BRANCH, "branch_id")
        require_kind(self.generation_id, IdKind.GENERATION, "generation_id")
        if not self.stage.strip() or not self.safe_message.strip():
            raise ContractValidationError("failure evidence requires stage and safe message")
        for value in (self.input_sha256, self.output_sha256):
            if value is not None and not re_is_sha256(value):
                raise ContractValidationError("failure input/output hash is invalid")
        handle_keys = tuple(
            (value.evidence_kind, str(value.evidence_id))
            for value in self.retained_evidence
        )
        if len(handle_keys) != len(set(handle_keys)):
            raise ContractValidationError("failure evidence handles must not be duplicated")
        payload_keys = tuple(
            (value.evidence_kind, str(value.evidence_id))
            for value in self.retained_receipt_payloads
        )
        if len(payload_keys) != len(set(payload_keys)):
            raise ContractValidationError("safe receipt payloads must not be duplicated")
        handles = {
            (value.evidence_kind, str(value.evidence_id)): value
            for value in self.retained_evidence
        }
        for payload in self.retained_receipt_payloads:
            handle = handles.get((payload.evidence_kind, str(payload.evidence_id)))
            if handle is None or handle.evidence_sha256 != payload.evidence_sha256:
                raise ContractValidationError(
                    "safe receipt payload must match a retained evidence handle"
                )
        if (
            self.external_provider_calls_observed < 0
            or self.authoritative_store_writes != 0
            or self.story_state_committed
        ):
            raise ContractValidationError(
                "failure evidence cannot claim a story-authority mutation"
            )
        if any(
            (
                self.retains_raw_source,
                self.retains_story_prose,
                self.retains_private_evidence,
                self.retains_prompt,
                self.retains_secret,
            )
        ):
            raise ContractValidationError(
                "failure evidence bundle cannot retain protected content"
            )

    @property
    def bundle_sha256(self) -> str:
        return domain_sha256("cera.turn_failure_evidence_bundle.v2", self)


_SAFE_DIAGNOSTIC_CODE = re.compile(r"^[A-Z][A-Z0-9_]{2,95}$")


@dataclass(frozen=True, slots=True)
class TurnFailureEvidenceBundleV3:
    """Failure evidence with restart-persistent value-free diagnostics."""

    SCHEMA_VERSION: ClassVar[str] = "cera.turn_failure_evidence_bundle.v3"

    schema_version: str
    failure_bundle_id: TypedId
    request_id: TypedId
    branch_id: TypedId
    generation_id: TypedId
    stage: str
    error_code: ErrorCode
    safe_message: str
    input_sha256: str | None
    output_sha256: str | None
    retained_evidence: tuple[FailureEvidenceHandle, ...]
    retained_receipt_payloads: tuple[PrivacySafeReceiptPayload, ...]
    safe_diagnostic_codes: tuple[str, ...]
    external_provider_calls_observed: int
    story_state_committed: bool
    authoritative_store_writes: int
    retains_raw_source: bool
    retains_story_prose: bool
    retains_private_evidence: bool
    retains_prompt: bool
    retains_secret: bool

    def __post_init__(self) -> None:
        require_schema(
            self.schema_version,
            self.SCHEMA_VERSION,
            type(self).__name__,
        )
        require_kind(
            self.failure_bundle_id,
            IdKind.FAILURE_BUNDLE,
            "failure_bundle_id",
        )
        require_kind(self.request_id, IdKind.REQUEST, "request_id")
        require_kind(self.branch_id, IdKind.BRANCH, "branch_id")
        require_kind(
            self.generation_id,
            IdKind.GENERATION,
            "generation_id",
        )
        if not self.stage.strip() or not self.safe_message.strip():
            raise ContractValidationError(
                "failure evidence requires stage and safe message"
            )
        for value in (self.input_sha256, self.output_sha256):
            if value is not None and not re_is_sha256(value):
                raise ContractValidationError(
                    "failure input/output hash is invalid"
                )
        handle_keys = tuple(
            (value.evidence_kind, str(value.evidence_id))
            for value in self.retained_evidence
        )
        if len(handle_keys) != len(set(handle_keys)):
            raise ContractValidationError(
                "failure evidence handles must not be duplicated"
            )
        payload_keys = tuple(
            (value.evidence_kind, str(value.evidence_id))
            for value in self.retained_receipt_payloads
        )
        if len(payload_keys) != len(set(payload_keys)):
            raise ContractValidationError(
                "safe receipt payloads must not be duplicated"
            )
        handles = {
            (value.evidence_kind, str(value.evidence_id)): value
            for value in self.retained_evidence
        }
        for payload in self.retained_receipt_payloads:
            handle = handles.get(
                (payload.evidence_kind, str(payload.evidence_id))
            )
            if (
                handle is None
                or handle.evidence_sha256 != payload.evidence_sha256
            ):
                raise ContractValidationError(
                    "safe receipt payload must match a retained evidence handle"
                )
        if len(self.safe_diagnostic_codes) != len(
            set(self.safe_diagnostic_codes)
        ) or any(
            _SAFE_DIAGNOSTIC_CODE.fullmatch(value) is None
            for value in self.safe_diagnostic_codes
        ):
            raise ContractValidationError(
                "failure diagnostic codes must be distinct value-free tokens"
            )
        if (
            self.external_provider_calls_observed < 0
            or self.authoritative_store_writes != 0
            or self.story_state_committed
        ):
            raise ContractValidationError(
                "failure evidence cannot claim a story-authority mutation"
            )
        if any(
            (
                self.retains_raw_source,
                self.retains_story_prose,
                self.retains_private_evidence,
                self.retains_prompt,
                self.retains_secret,
            )
        ):
            raise ContractValidationError(
                "failure evidence bundle cannot retain protected content"
            )

    @property
    def bundle_sha256(self) -> str:
        return domain_sha256(
            "cera.turn_failure_evidence_bundle.v3",
            self,
        )


class TurnStageStatus(StrEnum):
    STARTED = "started"
    COMPLETED = "completed"
    FAILED = "failed"


def _contains_forbidden_key(value) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).casefold()
            if normalized in _FORBIDDEN_RECEIPT_KEYS:
                return True
            if normalized.startswith("retains_") and item is True:
                return True
            if _contains_forbidden_key(item):
                return True
    elif isinstance(value, list):
        return any(_contains_forbidden_key(item) for item in value)
    return False


@dataclass(frozen=True, slots=True)
class TurnStageAuditEntry:
    SCHEMA_VERSION: ClassVar[str] = "cera.turn_stage_audit_entry.v1"

    schema_version: str
    journal_id: TypedId
    request_id: TypedId
    branch_id: TypedId
    generation_id: TypedId
    sequence: int
    stage: str
    status: TurnStageStatus
    input_sha256: str | None
    output_sha256: str | None
    failure_bundle_id: TypedId | None
    external_provider_calls_observed: int
    story_state_committed: bool

    def __post_init__(self) -> None:
        require_schema(self.schema_version, self.SCHEMA_VERSION, type(self).__name__)
        require_kind(self.journal_id, IdKind.STAGE_JOURNAL, "journal_id")
        require_kind(self.request_id, IdKind.REQUEST, "request_id")
        require_kind(self.branch_id, IdKind.BRANCH, "branch_id")
        require_kind(self.generation_id, IdKind.GENERATION, "generation_id")
        if self.sequence < 1 or not self.stage.strip():
            raise ContractValidationError("stage journal sequence/stage is invalid")
        for value in (self.input_sha256, self.output_sha256):
            if value is not None and not re_is_sha256(value):
                raise ContractValidationError("stage journal hash is invalid")
        if self.status is TurnStageStatus.FAILED:
            if self.failure_bundle_id is None:
                raise ContractValidationError(
                    "failed stage requires a failure evidence bundle"
                )
        elif self.failure_bundle_id is not None:
            raise ContractValidationError(
                "non-failed stage cannot carry failure evidence"
            )
        if self.failure_bundle_id is not None:
            require_kind(
                self.failure_bundle_id,
                IdKind.FAILURE_BUNDLE,
                "failure_bundle_id",
            )
        if self.external_provider_calls_observed < 0 or self.story_state_committed:
            raise ContractValidationError(
                "pre-publication stage journal cannot claim a commit"
            )

    @property
    def entry_sha256(self) -> str:
        return domain_sha256("cera.turn_stage_audit_entry.v1", self)
