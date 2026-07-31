"""Provider-neutral contracts for deferred derived-memory consolidation."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import json
from typing import ClassVar

from cera.contracts import EvidenceRecordType, Visibility
from cera.errors import ContractValidationError
from cera.evidence.models import (
    EvidenceDocument,
    EvidenceLookupReceipt,
    EvidenceSnapshot,
    ExactEvidence,
)
from cera.ids import IdKind, TypedId, require_kind
from cera.schema import require_schema
from cera.serialization import canonical_json, domain_sha256, re_is_sha256, text_sha256
from cera.storage.models import ReceiptRecord


DERIVED_RECORD_TYPES = (
    EvidenceRecordType.MEMORY,
    EvidenceRecordType.RELATIONSHIP,
    EvidenceRecordType.THREAD,
    EvidenceRecordType.DEVELOPMENT,
)


class ConsolidationStatus(StrEnum):
    PREPARED = "prepared"
    COMMITTED = "committed"
    ROLLED_BACK = "rolled_back"


class DerivedViewKind(StrEnum):
    PUBLIC_SUMMARY = "public_summary"
    OWNER_SUMMARY = "owner_summary"
    SYSTEM_INDEX = "system_index"


@dataclass(frozen=True, slots=True)
class DerivedConsolidationRequest:
    SCHEMA_VERSION: ClassVar[str] = "cera.derived_consolidation_request.v2"

    schema_version: str
    request_id: TypedId
    trace_id: TypedId
    protected_user_id: TypedId
    snapshot: EvidenceSnapshot
    exact_evidence: tuple[ExactEvidence, ...]
    lookup_receipt_ids: tuple[TypedId, ...]
    eligible_record_types: tuple[EvidenceRecordType, ...]
    maximum_records: int
    hard_boundaries: tuple[str, ...]
    lookup_receipts: tuple[EvidenceLookupReceipt, ...] = ()

    def __post_init__(self) -> None:
        require_schema(self.schema_version, self.SCHEMA_VERSION, type(self).__name__)
        require_kind(self.request_id, IdKind.REQUEST, "request_id")
        require_kind(self.trace_id, IdKind.TRACE, "trace_id")
        require_kind(self.protected_user_id, IdKind.CHARACTER, "protected_user_id")
        if self.snapshot.request_id != self.request_id:
            raise ContractValidationError("consolidation snapshot request mismatch")
        if self.snapshot.branch_head_artifact_id is None:
            raise ContractValidationError("consolidation requires a committed story artifact")
        if not self.exact_evidence:
            raise ContractValidationError("consolidation requires expanded exact evidence")
        if not self.lookup_receipt_ids:
            raise ContractValidationError("consolidation requires lookup receipts")
        if not self.eligible_record_types:
            raise ContractValidationError("consolidation requires eligible record types")
        if any(value not in DERIVED_RECORD_TYPES for value in self.eligible_record_types):
            raise ContractValidationError("consolidation cannot create direct or Genesis facts")
        if type(self.maximum_records) is not int or not 1 <= self.maximum_records <= 20:
            raise ContractValidationError("maximum_records must be between 1 and 20")
        if not self.hard_boundaries:
            raise ContractValidationError("consolidation requires hard boundaries")
        _unique((str(value.evidence_id) for value in self.exact_evidence), "exact evidence")
        _unique((value.value for value in self.eligible_record_types), "eligible record types")
        _unique((str(value) for value in self.lookup_receipt_ids), "lookup receipts")
        _unique(self.hard_boundaries, "hard boundaries")
        for receipt_id in self.lookup_receipt_ids:
            require_kind(receipt_id, IdKind.LOOKUP_RECEIPT, "lookup_receipt_ids")
        if self.lookup_receipts and tuple(
            value.lookup_receipt_id for value in self.lookup_receipts
        ) != self.lookup_receipt_ids:
            raise ContractValidationError(
                "consolidation lookup receipt payloads do not match their handles"
            )
        for evidence in self.exact_evidence:
            require_kind(evidence.evidence_id, IdKind.EVIDENCE, "exact evidence ID")
            if evidence.metadata.genesis_revision_id != self.snapshot.genesis_revision_id:
                raise ContractValidationError("exact evidence Genesis revision mismatch")
            _canonical_object(evidence.sections_json, "exact evidence sections")

    @property
    def request_sha256(self) -> str:
        return domain_sha256("cera.derived_consolidation_request.v2", self)


@dataclass(frozen=True, slots=True)
class ConsolidationProposal:
    SCHEMA_VERSION: ClassVar[str] = "cera.consolidation_proposal.v1"

    schema_version: str
    decision_id: TypedId
    records: tuple[EvidenceDocument, ...]
    source_evidence_ids: tuple[TypedId, ...]
    uncertainties: tuple[str, ...]
    no_change_reason: str | None
    genesis_rewrite_proposed: bool
    clinical_diagnosis_proposed: bool
    accepted_story_change_proposed: bool

    def __post_init__(self) -> None:
        require_schema(self.schema_version, self.SCHEMA_VERSION, type(self).__name__)
        require_kind(self.decision_id, IdKind.DECISION, "decision_id")
        for evidence_id in self.source_evidence_ids:
            require_kind(evidence_id, IdKind.EVIDENCE, "source_evidence_ids")
        _unique((str(value.record_id) for value in self.records), "proposal records")
        _unique((str(value) for value in self.source_evidence_ids), "source evidence IDs")
        _unique(self.uncertainties, "uncertainties")
        if self.records and self.no_change_reason is not None:
            raise ContractValidationError("record proposal cannot also report no change")
        if not self.records:
            _non_empty(self.no_change_reason, "no_change_reason")
        if self.records and not self.source_evidence_ids:
            raise ContractValidationError("derived records require source evidence IDs")
        if (
            self.genesis_rewrite_proposed
            or self.clinical_diagnosis_proposed
            or self.accepted_story_change_proposed
        ):
            raise ContractValidationError(
                "consolidation cannot rewrite Genesis, diagnose, or change accepted prose"
            )

    @property
    def proposal_sha256(self) -> str:
        return domain_sha256("cera.consolidation_proposal.v1", self)


@dataclass(frozen=True, slots=True)
class ConsolidationReasonerReceipt:
    SCHEMA_VERSION: ClassVar[str] = "cera.consolidation_reasoner_receipt.v1"

    schema_version: str
    provider_receipt_id: TypedId
    request_sha256: str
    snapshot_token: TypedId
    snapshot_binding_sha256: str
    fixture_id: str
    fixture_sha256: str
    proposal_sha256: str
    source_evidence_ids: tuple[TypedId, ...]
    lookup_receipt_ids: tuple[TypedId, ...]
    external_provider_calls: int

    def __post_init__(self) -> None:
        require_schema(self.schema_version, self.SCHEMA_VERSION, type(self).__name__)
        require_kind(self.provider_receipt_id, IdKind.PROVIDER_RECEIPT, "provider_receipt_id")
        require_kind(self.snapshot_token, IdKind.SNAPSHOT, "snapshot_token")
        for value in (
            self.request_sha256,
            self.snapshot_binding_sha256,
            self.fixture_sha256,
            self.proposal_sha256,
        ):
            if not re_is_sha256(value):
                raise ContractValidationError("consolidation receipt hashes must be SHA-256")
        _non_empty(self.fixture_id, "fixture_id")
        for evidence_id in self.source_evidence_ids:
            require_kind(evidence_id, IdKind.EVIDENCE, "source_evidence_ids")
        for receipt_id in self.lookup_receipt_ids:
            require_kind(receipt_id, IdKind.LOOKUP_RECEIPT, "lookup_receipt_ids")
        _unique((str(value) for value in self.source_evidence_ids), "source evidence IDs")
        _unique((str(value) for value in self.lookup_receipt_ids), "lookup receipts")
        if self.external_provider_calls != 0:
            raise ContractValidationError("provider-free consolidator cannot report provider calls")


@dataclass(frozen=True, slots=True)
class ConsolidationExecutionResult:
    proposal: ConsolidationProposal
    receipt: ConsolidationReasonerReceipt


@dataclass(frozen=True, slots=True)
class ConsolidationValidationReceipt:
    SCHEMA_VERSION: ClassVar[str] = "cera.consolidation_validation_receipt.v1"

    schema_version: str
    validation_receipt_id: TypedId
    request_sha256: str
    proposal_sha256: str
    snapshot_token: TypedId
    validated_record_ids: tuple[TypedId, ...]
    superseded_record_ids: tuple[TypedId, ...]
    status: str
    authority_store_writes: int

    def __post_init__(self) -> None:
        require_schema(self.schema_version, self.SCHEMA_VERSION, type(self).__name__)
        require_kind(
            self.validation_receipt_id,
            IdKind.VALIDATION,
            "validation_receipt_id",
        )
        require_kind(self.snapshot_token, IdKind.SNAPSHOT, "snapshot_token")
        if not re_is_sha256(self.request_sha256) or not re_is_sha256(self.proposal_sha256):
            raise ContractValidationError("validation receipt hashes must be SHA-256")
        _unique((str(value) for value in self.validated_record_ids), "validated records")
        _unique((str(value) for value in self.superseded_record_ids), "superseded records")
        if self.status not in {"validated_pending_commit", "validated_no_change"}:
            raise ContractValidationError("consolidation validation status is invalid")
        if self.status == "validated_no_change" and (
            self.validated_record_ids or self.superseded_record_ids
        ):
            raise ContractValidationError("no-change validation cannot carry record IDs")
        if self.authority_store_writes != 0:
            raise ContractValidationError("validation cannot write authority")


@dataclass(frozen=True, slots=True)
class ConsolidationBundle:
    SCHEMA_VERSION: ClassVar[str] = "cera.consolidation_bundle.v2"

    schema_version: str
    transaction_id: TypedId
    idempotency_key: str
    request_id: TypedId
    branch_id: TypedId
    expected_generation: int
    expected_head_artifact_id: TypedId
    expected_authority_revision: int
    genesis_revision_id: TypedId
    snapshot_token: TypedId
    snapshot_binding_sha256: str
    request_sha256: str
    proposal_sha256: str
    documents: tuple[EvidenceDocument, ...]
    validation_receipt_id: TypedId
    reasoner_receipt_id: TypedId
    lookup_receipt_ids: tuple[TypedId, ...]
    receipt_records: tuple[ReceiptRecord, ...] = ()

    def __post_init__(self) -> None:
        require_schema(self.schema_version, self.SCHEMA_VERSION, type(self).__name__)
        require_kind(self.transaction_id, IdKind.TRANSACTION, "transaction_id")
        require_kind(self.request_id, IdKind.REQUEST, "request_id")
        require_kind(self.branch_id, IdKind.BRANCH, "branch_id")
        require_kind(
            self.expected_head_artifact_id,
            IdKind.ARTIFACT,
            "expected_head_artifact_id",
        )
        require_kind(
            self.genesis_revision_id,
            IdKind.GENESIS_REVISION,
            "genesis_revision_id",
        )
        require_kind(self.snapshot_token, IdKind.SNAPSHOT, "snapshot_token")
        require_kind(self.validation_receipt_id, IdKind.VALIDATION, "validation_receipt_id")
        require_kind(self.reasoner_receipt_id, IdKind.PROVIDER_RECEIPT, "reasoner_receipt_id")
        _non_empty(self.idempotency_key, "idempotency_key")
        if len(self.idempotency_key) > 200:
            raise ContractValidationError("idempotency_key must be at most 200 characters")
        if self.expected_generation < 0 or self.expected_authority_revision < 0:
            raise ContractValidationError("consolidation revisions cannot be negative")
        if not self.documents:
            raise ContractValidationError("consolidation bundle requires records")
        for value in (
            self.snapshot_binding_sha256,
            self.request_sha256,
            self.proposal_sha256,
        ):
            if not re_is_sha256(value):
                raise ContractValidationError("consolidation bundle hashes must be SHA-256")
        _unique((str(value.record_id) for value in self.documents), "bundle records")
        _unique((str(value) for value in self.lookup_receipt_ids), "lookup receipts")
        for receipt_id in self.lookup_receipt_ids:
            require_kind(receipt_id, IdKind.LOOKUP_RECEIPT, "lookup_receipt_ids")
        if self.receipt_records:
            expected_receipt_ids = {
                str(self.validation_receipt_id),
                str(self.reasoner_receipt_id),
                *(str(value) for value in self.lookup_receipt_ids),
            }
            actual_receipt_ids = [str(value.receipt_id) for value in self.receipt_records]
            if len(actual_receipt_ids) != len(set(actual_receipt_ids)):
                raise ContractValidationError(
                    "consolidation receipt records contain duplicate IDs"
                )
            if set(actual_receipt_ids) != expected_receipt_ids:
                raise ContractValidationError(
                    "consolidation receipt records must cover every receipt handle"
                )
        for document in self.documents:
            if document.record_type not in DERIVED_RECORD_TYPES:
                raise ContractValidationError("bundle contains a non-derived record type")
            if document.branch_origin_id != self.branch_id:
                raise ContractValidationError("bundle record branch mismatch")
            if document.genesis_revision_id != self.genesis_revision_id:
                raise ContractValidationError("bundle record Genesis mismatch")

    @property
    def bundle_sha256(self) -> str:
        return domain_sha256("cera.consolidation_bundle.v2", self)


@dataclass(frozen=True, slots=True)
class ConsolidationStagingResult:
    validation_receipt: ConsolidationValidationReceipt
    bundle: ConsolidationBundle | None

    def __post_init__(self) -> None:
        if (self.bundle is None) != (
            self.validation_receipt.status == "validated_no_change"
        ):
            raise ContractValidationError("staging result bundle/status mismatch")


@dataclass(frozen=True, slots=True)
class ConsolidationCommitReceipt:
    SCHEMA_VERSION: ClassVar[str] = "cera.consolidation_commit_receipt.v1"

    schema_version: str
    commit_id: TypedId
    transaction_id: TypedId
    request_id: TypedId
    branch_id: TypedId
    head_artifact_id: TypedId
    generation: int
    authority_revision_before: int
    authority_revision_after: int
    genesis_revision_id: TypedId
    inserted_record_ids: tuple[TypedId, ...]
    superseded_record_ids: tuple[TypedId, ...]
    validation_receipt_id: TypedId
    reasoner_receipt_id: TypedId
    lookup_receipt_ids: tuple[TypedId, ...]
    transaction_sha256: str
    story_artifact_writes: int
    genesis_writes: int
    outcome: str

    def __post_init__(self) -> None:
        require_schema(self.schema_version, self.SCHEMA_VERSION, type(self).__name__)
        require_kind(self.commit_id, IdKind.COMMIT, "commit_id")
        require_kind(self.transaction_id, IdKind.TRANSACTION, "transaction_id")
        require_kind(self.request_id, IdKind.REQUEST, "request_id")
        require_kind(self.branch_id, IdKind.BRANCH, "branch_id")
        require_kind(self.head_artifact_id, IdKind.ARTIFACT, "head_artifact_id")
        require_kind(
            self.genesis_revision_id,
            IdKind.GENESIS_REVISION,
            "genesis_revision_id",
        )
        require_kind(self.validation_receipt_id, IdKind.VALIDATION, "validation_receipt_id")
        require_kind(self.reasoner_receipt_id, IdKind.PROVIDER_RECEIPT, "reasoner_receipt_id")
        if self.generation < 0:
            raise ContractValidationError("consolidation generation cannot be negative")
        if self.authority_revision_after != self.authority_revision_before + 1:
            raise ContractValidationError("authority revision must advance exactly once")
        _unique((str(value) for value in self.inserted_record_ids), "inserted records")
        _unique((str(value) for value in self.superseded_record_ids), "superseded records")
        _unique((str(value) for value in self.lookup_receipt_ids), "lookup receipts")
        if not re_is_sha256(self.transaction_sha256):
            raise ContractValidationError("transaction_sha256 must be SHA-256")
        if self.story_artifact_writes != 0 or self.genesis_writes != 0:
            raise ContractValidationError("consolidation cannot write story artifacts or Genesis")
        if self.outcome != "committed":
            raise ContractValidationError("consolidation receipt outcome must be committed")


@dataclass(frozen=True, slots=True)
class StoredConsolidation:
    receipt: ConsolidationCommitReceipt
    exact_replay: bool


@dataclass(frozen=True, slots=True)
class DerivedView:
    SCHEMA_VERSION: ClassVar[str] = "cera.derived_view.v1"

    schema_version: str
    branch_id: TypedId
    head_artifact_id: TypedId
    authority_revision: int
    view_key: str
    view_kind: DerivedViewKind
    visibility: Visibility
    owner_id: TypedId | None
    payload_json: str
    source_set_sha256: str
    view_sha256: str

    def __post_init__(self) -> None:
        require_schema(self.schema_version, self.SCHEMA_VERSION, type(self).__name__)
        require_kind(self.branch_id, IdKind.BRANCH, "branch_id")
        require_kind(self.head_artifact_id, IdKind.ARTIFACT, "head_artifact_id")
        if self.authority_revision < 0:
            raise ContractValidationError("view authority revision cannot be negative")
        _non_empty(self.view_key, "view_key")
        _canonical_object(self.payload_json, "view payload")
        if not re_is_sha256(self.source_set_sha256) or not re_is_sha256(self.view_sha256):
            raise ContractValidationError("derived view hashes must be SHA-256")
        if text_sha256(self.payload_json) != self.view_sha256:
            raise ContractValidationError("derived view hash does not match payload")
        if self.view_kind is DerivedViewKind.OWNER_SUMMARY:
            if self.owner_id is None or self.visibility is not Visibility.OWNER_PRIVATE:
                raise ContractValidationError("owner summary requires owner-private scope")
        elif self.owner_id is not None:
            raise ContractValidationError("non-owner view cannot carry owner_id")
        if self.view_kind is DerivedViewKind.SYSTEM_INDEX:
            if self.visibility is not Visibility.SYSTEM_PRIVATE:
                raise ContractValidationError("system index must be system-private")
        elif self.view_kind is DerivedViewKind.PUBLIC_SUMMARY:
            if self.visibility is not Visibility.PUBLIC:
                raise ContractValidationError("public summary must be public")

    @classmethod
    def create(
        cls,
        *,
        branch_id: TypedId,
        head_artifact_id: TypedId,
        authority_revision: int,
        view_key: str,
        view_kind: DerivedViewKind,
        visibility: Visibility,
        owner_id: TypedId | None,
        payload: object,
        source_set_sha256: str,
    ) -> "DerivedView":
        payload_json = canonical_json(payload)
        return cls(
            schema_version=cls.SCHEMA_VERSION,
            branch_id=branch_id,
            head_artifact_id=head_artifact_id,
            authority_revision=authority_revision,
            view_key=view_key,
            view_kind=view_kind,
            visibility=visibility,
            owner_id=owner_id,
            payload_json=payload_json,
            source_set_sha256=source_set_sha256,
            view_sha256=text_sha256(payload_json),
        )


@dataclass(frozen=True, slots=True)
class DerivedViewBuildReceipt:
    SCHEMA_VERSION: ClassVar[str] = "cera.derived_view_build_receipt.v1"

    schema_version: str
    validation_receipt_id: TypedId
    branch_id: TypedId
    head_artifact_id: TypedId
    authority_revision: int
    source_set_sha256: str
    view_keys: tuple[str, ...]
    evidence_index_rows: int
    authority_store_writes: int

    def __post_init__(self) -> None:
        require_schema(self.schema_version, self.SCHEMA_VERSION, type(self).__name__)
        require_kind(self.validation_receipt_id, IdKind.VALIDATION, "validation_receipt_id")
        require_kind(self.branch_id, IdKind.BRANCH, "branch_id")
        require_kind(self.head_artifact_id, IdKind.ARTIFACT, "head_artifact_id")
        if self.authority_revision < 0 or self.evidence_index_rows < 0:
            raise ContractValidationError("view build counters cannot be negative")
        if not re_is_sha256(self.source_set_sha256):
            raise ContractValidationError("source_set_sha256 must be SHA-256")
        _unique(self.view_keys, "view keys")
        if self.authority_store_writes != 0:
            raise ContractValidationError("view rebuilding cannot write authority")


def _non_empty(value: str | None, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ContractValidationError(f"{field_name} must be non-empty")


def _canonical_object(value: str, field_name: str) -> dict[str, object]:
    try:
        decoded = json.loads(value)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ContractValidationError(f"{field_name} must be canonical JSON") from exc
    if not isinstance(decoded, dict) or canonical_json(decoded) != value:
        raise ContractValidationError(f"{field_name} must be a canonical JSON object")
    return decoded


def _unique(values, field_name: str) -> None:
    items = list(values)
    if len(items) != len(set(items)):
        raise ContractValidationError(f"{field_name} must not contain duplicates")
