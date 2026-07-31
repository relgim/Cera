"""Provider-neutral records used by the transactional authority store."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import json

from cera.contracts import AcceptedStoryArtifact, CommitReceipt
from cera.errors import ContractValidationError
from cera.ids import (
    AUTHORITY_RECORD_ID_KINDS,
    IdKind,
    TypedId,
    require_kind,
)
from cera.serialization import canonical_json, domain_sha256, re_is_sha256, text_sha256


class CommitMode(StrEnum):
    APPEND = "append"
    REGENERATE = "regenerate"


class JournalStatus(StrEnum):
    PREPARED = "prepared"
    COMMITTED = "committed"
    ROLLED_BACK = "rolled_back"


class ReceiptCategory(StrEnum):
    VALIDATION = "validation"
    LOOKUP = "lookup"
    PROVIDER = "provider"


class PostPublicationWorkKind(StrEnum):
    RENDER = "render"
    CONSOLIDATE = "consolidate"
    DERIVED_VIEWS = "derived_views"


class PostPublicationWorkStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    NO_CHANGE = "no_change"
    FAILED = "failed"


def _canonical_payload(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value:
        raise ContractValidationError(f"{field_name} must be non-empty canonical JSON")
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ContractValidationError(f"{field_name} is not valid JSON") from exc
    if canonical_json(decoded) != value:
        raise ContractValidationError(f"{field_name} must use CERA canonical JSON")


@dataclass(frozen=True, slots=True)
class SourceRecord:
    source_id: TypedId
    request_id: TypedId
    branch_id: TypedId
    payload_json: str
    source_sha256: str

    def __post_init__(self) -> None:
        require_kind(self.source_id, IdKind.SOURCE, "source_id")
        require_kind(self.request_id, IdKind.REQUEST, "request_id")
        require_kind(self.branch_id, IdKind.BRANCH, "branch_id")
        _canonical_payload(self.payload_json, "payload_json")
        if not re_is_sha256(self.source_sha256):
            raise ContractValidationError("source_sha256 must be lowercase SHA-256")
        if text_sha256(self.payload_json) != self.source_sha256:
            raise ContractValidationError("source_sha256 does not match payload_json")

    @classmethod
    def from_payload(
        cls,
        *,
        source_id: TypedId,
        request_id: TypedId,
        branch_id: TypedId,
        payload: object,
    ) -> "SourceRecord":
        payload_json = canonical_json(payload)
        return cls(
            source_id=source_id,
            request_id=request_id,
            branch_id=branch_id,
            payload_json=payload_json,
            source_sha256=text_sha256(payload_json),
        )


@dataclass(frozen=True, slots=True)
class AuthorityRecord:
    record_id: TypedId
    branch_id: TypedId
    artifact_id: TypedId
    record_type: str
    payload_json: str
    payload_sha256: str
    supersedes: tuple[TypedId, ...] = ()

    def __post_init__(self) -> None:
        if self.record_id.kind not in AUTHORITY_RECORD_ID_KINDS:
            raise ContractValidationError(
                f"record_id has unsupported authority kind {self.record_id.kind.value}"
            )
        require_kind(self.branch_id, IdKind.BRANCH, "branch_id")
        require_kind(self.artifact_id, IdKind.ARTIFACT, "artifact_id")
        if not isinstance(self.record_type, str) or not self.record_type.strip():
            raise ContractValidationError("record_type must be non-empty")
        _canonical_payload(self.payload_json, "payload_json")
        if not re_is_sha256(self.payload_sha256):
            raise ContractValidationError("payload_sha256 must be lowercase SHA-256")
        if text_sha256(self.payload_json) != self.payload_sha256:
            raise ContractValidationError("payload_sha256 does not match payload_json")
        rendered = [str(record_id) for record_id in self.supersedes]
        if len(rendered) != len(set(rendered)):
            raise ContractValidationError("supersedes must not contain duplicates")

    @classmethod
    def from_payload(
        cls,
        *,
        record_id: TypedId,
        branch_id: TypedId,
        artifact_id: TypedId,
        record_type: str,
        payload: object,
        supersedes: tuple[TypedId, ...] = (),
    ) -> "AuthorityRecord":
        payload_json = canonical_json(payload)
        return cls(
            record_id=record_id,
            branch_id=branch_id,
            artifact_id=artifact_id,
            record_type=record_type,
            payload_json=payload_json,
            payload_sha256=text_sha256(payload_json),
            supersedes=supersedes,
        )


@dataclass(frozen=True, slots=True)
class ReceiptRecord:
    """Immutable, content-addressed audit evidence committed with a turn."""

    receipt_id: TypedId
    category: ReceiptCategory
    schema_version: str
    payload_json: str
    payload_sha256: str

    def __post_init__(self) -> None:
        expected_kind = {
            ReceiptCategory.VALIDATION: (
                IdKind.VALIDATION,
                IdKind.REALIZATION_VERIFICATION,
            ),
            ReceiptCategory.LOOKUP: IdKind.LOOKUP_RECEIPT,
            ReceiptCategory.PROVIDER: IdKind.PROVIDER_RECEIPT,
        }[self.category]
        if isinstance(expected_kind, tuple):
            if self.receipt_id.kind not in expected_kind:
                raise ContractValidationError(
                    "validation receipt has an unsupported typed ID"
                )
        else:
            require_kind(self.receipt_id, expected_kind, "receipt_id")
        if not isinstance(self.schema_version, str) or not self.schema_version.strip():
            raise ContractValidationError("receipt schema_version must be non-empty")
        _canonical_payload(self.payload_json, "receipt payload_json")
        if not re_is_sha256(self.payload_sha256):
            raise ContractValidationError("receipt payload_sha256 must be lowercase SHA-256")
        if text_sha256(self.payload_json) != self.payload_sha256:
            raise ContractValidationError("receipt payload_sha256 does not match payload_json")

    @classmethod
    def from_payload(
        cls,
        *,
        receipt_id: TypedId,
        category: ReceiptCategory,
        schema_version: str,
        payload: object,
    ) -> "ReceiptRecord":
        payload_json = canonical_json(payload)
        return cls(
            receipt_id=receipt_id,
            category=category,
            schema_version=schema_version,
            payload_json=payload_json,
            payload_sha256=text_sha256(payload_json),
        )


@dataclass(frozen=True, slots=True)
class PostPublicationWorkRequest:
    """Immutable work scheduled atomically with accepted story publication."""

    work_id: TypedId
    kind: PostPublicationWorkKind
    branch_id: TypedId
    artifact_id: TypedId
    request_json: str
    request_sha256: str
    depends_on_work_id: TypedId | None = None

    def __post_init__(self) -> None:
        require_kind(self.work_id, IdKind.POST_PUBLICATION_WORK, "work_id")
        require_kind(self.branch_id, IdKind.BRANCH, "branch_id")
        require_kind(self.artifact_id, IdKind.ARTIFACT, "artifact_id")
        _canonical_payload(self.request_json, "post-publication request_json")
        if not re_is_sha256(self.request_sha256):
            raise ContractValidationError(
                "post-publication request_sha256 must be lowercase SHA-256"
            )
        if text_sha256(self.request_json) != self.request_sha256:
            raise ContractValidationError(
                "post-publication request_sha256 does not match request_json"
            )
        if self.depends_on_work_id is not None:
            require_kind(
                self.depends_on_work_id,
                IdKind.POST_PUBLICATION_WORK,
                "depends_on_work_id",
            )
            if self.depends_on_work_id == self.work_id:
                raise ContractValidationError("post-publication work cannot depend on itself")
        if self.kind is PostPublicationWorkKind.DERIVED_VIEWS:
            if self.depends_on_work_id is None:
                raise ContractValidationError("derived-view work requires a dependency")
        elif self.depends_on_work_id is not None:
            raise ContractValidationError(
                "only derived-view work may declare a dependency"
            )
        payload = json.loads(self.request_json)
        if not isinstance(payload, dict):
            raise ContractValidationError("post-publication request must be an object")
        common_hash = payload.get("artifact_sha256")
        if not isinstance(common_hash, str) or not re_is_sha256(common_hash):
            raise ContractValidationError(
                "post-publication request requires an accepted-artifact SHA-256"
            )
        if self.kind is PostPublicationWorkKind.RENDER:
            expected = {"schema_version", "renderer_profile", "artifact_sha256"}
            if set(payload) != expected or payload.get("schema_version") != (
                "cera.post_publication_render_work.v1"
            ):
                raise ContractValidationError("render work request schema is invalid")
            if not isinstance(payload.get("renderer_profile"), str) or not payload[
                "renderer_profile"
            ].strip():
                raise ContractValidationError("render work requires a renderer profile")
        elif self.kind is PostPublicationWorkKind.CONSOLIDATE:
            expected = {
                "schema_version",
                "protected_user_id",
                "world_mode",
                "direct_event_id",
                "artifact_sha256",
            }
            if set(payload) != expected or payload.get("schema_version") != (
                "cera.post_publication_consolidation_work.v1"
            ):
                raise ContractValidationError("consolidation work request schema is invalid")
            TypedId.parse(payload.get("protected_user_id"), IdKind.CHARACTER)
            TypedId.parse(payload.get("direct_event_id"), IdKind.EVENT)
            if payload.get("world_mode") not in {"real", "synthetic_fixture"}:
                raise ContractValidationError("consolidation work world_mode is invalid")
        else:
            expected = {
                "schema_version",
                "artifact_sha256",
                "consolidation_work_id",
            }
            if set(payload) != expected or payload.get("schema_version") != (
                "cera.post_publication_view_work.v1"
            ):
                raise ContractValidationError("derived-view work request schema is invalid")
            declared_dependency = TypedId.parse(
                payload.get("consolidation_work_id"), IdKind.POST_PUBLICATION_WORK
            )
            if declared_dependency != self.depends_on_work_id:
                raise ContractValidationError(
                    "derived-view request dependency does not match its work binding"
                )

    @classmethod
    def from_payload(
        cls,
        *,
        work_id: TypedId,
        kind: PostPublicationWorkKind,
        branch_id: TypedId,
        artifact_id: TypedId,
        payload: object,
        depends_on_work_id: TypedId | None = None,
    ) -> "PostPublicationWorkRequest":
        request_json = canonical_json(payload)
        return cls(
            work_id=work_id,
            kind=kind,
            branch_id=branch_id,
            artifact_id=artifact_id,
            request_json=request_json,
            request_sha256=text_sha256(request_json),
            depends_on_work_id=depends_on_work_id,
        )


@dataclass(frozen=True, slots=True)
class PostPublicationWork:
    work_id: TypedId
    kind: PostPublicationWorkKind
    branch_id: TypedId
    artifact_id: TypedId
    request_json: str
    request_sha256: str
    depends_on_work_id: TypedId | None
    status: PostPublicationWorkStatus
    attempt_count: int
    result_json: str | None
    result_sha256: str | None
    error_json: str | None
    error_sha256: str | None

    def __post_init__(self) -> None:
        PostPublicationWorkRequest(
            work_id=self.work_id,
            kind=self.kind,
            branch_id=self.branch_id,
            artifact_id=self.artifact_id,
            request_json=self.request_json,
            request_sha256=self.request_sha256,
            depends_on_work_id=self.depends_on_work_id,
        )
        if self.attempt_count < 0:
            raise ContractValidationError("post-publication attempt_count cannot be negative")
        if (self.result_json is None) != (self.result_sha256 is None):
            raise ContractValidationError("post-publication result payload is incomplete")
        if (self.error_json is None) != (self.error_sha256 is None):
            raise ContractValidationError("post-publication error payload is incomplete")
        if self.result_json is not None:
            _canonical_payload(self.result_json, "post-publication result_json")
            if text_sha256(self.result_json) != self.result_sha256:
                raise ContractValidationError("post-publication result hash mismatch")
        if self.error_json is not None:
            _canonical_payload(self.error_json, "post-publication error_json")
            if text_sha256(self.error_json) != self.error_sha256:
                raise ContractValidationError("post-publication error hash mismatch")
        if self.status in {
            PostPublicationWorkStatus.COMPLETED,
            PostPublicationWorkStatus.NO_CHANGE,
        } and self.result_json is None:
            raise ContractValidationError("terminal successful work requires a result payload")
        if self.status in {
            PostPublicationWorkStatus.COMPLETED,
            PostPublicationWorkStatus.NO_CHANGE,
        } and self.error_json is not None:
            raise ContractValidationError("successful work cannot carry an error payload")
        if self.status is PostPublicationWorkStatus.FAILED and self.error_json is None:
            raise ContractValidationError("failed work requires an error payload")
        if self.status is PostPublicationWorkStatus.FAILED and self.result_json is not None:
            raise ContractValidationError("failed work cannot carry a result payload")
        if self.status in {
            PostPublicationWorkStatus.PENDING,
            PostPublicationWorkStatus.RUNNING,
        } and (self.result_json is not None or self.error_json is not None):
            raise ContractValidationError("nonterminal work cannot carry result or error payloads")


@dataclass(frozen=True, slots=True)
class TurnCommitBundle:
    """One validated source-to-artifact state transition.

    The bundle is storage input, not creative output. Its domain hash is the
    idempotency and transaction-integrity boundary.
    """

    transaction_id: TypedId
    idempotency_key: str
    mode: CommitMode
    branch_id: TypedId
    expected_generation: int
    expected_head_artifact_id: TypedId | None
    source: SourceRecord
    artifact: AcceptedStoryArtifact
    authority_records: tuple[AuthorityRecord, ...] = ()
    validation_receipt_ids: tuple[TypedId, ...] = ()
    lookup_receipt_ids: tuple[TypedId, ...] = ()
    provider_receipt_ids: tuple[TypedId, ...] = ()
    receipt_records: tuple[ReceiptRecord, ...] = ()
    post_publication_work_requests: tuple[PostPublicationWorkRequest, ...] = ()
    external_receipt_id: TypedId | None = None
    blocked_checkpoint_id: TypedId | None = None
    replaces_artifact_id: TypedId | None = None

    def __post_init__(self) -> None:
        require_kind(self.transaction_id, IdKind.TRANSACTION, "transaction_id")
        require_kind(self.branch_id, IdKind.BRANCH, "branch_id")
        if not isinstance(self.idempotency_key, str) or not self.idempotency_key.strip():
            raise ContractValidationError("idempotency_key must be non-empty")
        if len(self.idempotency_key) > 200:
            raise ContractValidationError("idempotency_key must be at most 200 characters")
        if self.expected_generation < 0:
            raise ContractValidationError("expected_generation cannot be negative")
        if self.expected_head_artifact_id is not None:
            require_kind(
                self.expected_head_artifact_id,
                IdKind.ARTIFACT,
                "expected_head_artifact_id",
            )
        if self.source.branch_id != self.branch_id:
            raise ContractValidationError("source branch does not match bundle branch")
        if self.artifact.branch_id != self.branch_id:
            raise ContractValidationError("artifact branch does not match bundle branch")
        if self.artifact.source_id != self.source.source_id:
            raise ContractValidationError("artifact source does not match bundle source")
        if self.artifact.transaction_id != self.transaction_id:
            raise ContractValidationError("artifact transaction does not match bundle")
        if self.mode is CommitMode.APPEND:
            if self.replaces_artifact_id is not None:
                raise ContractValidationError("append commit cannot replace an artifact")
            if self.artifact.parent_artifact_id != self.expected_head_artifact_id:
                raise ContractValidationError("append artifact parent must equal expected head")
        elif self.mode is CommitMode.REGENERATE:
            if self.replaces_artifact_id is None:
                raise ContractValidationError("regeneration requires replaces_artifact_id")
            require_kind(self.replaces_artifact_id, IdKind.ARTIFACT, "replaces_artifact_id")
            if self.replaces_artifact_id != self.expected_head_artifact_id:
                raise ContractValidationError("regeneration must replace the expected head")
        record_ids = [str(record.record_id) for record in self.authority_records]
        if len(record_ids) != len(set(record_ids)):
            raise ContractValidationError("authority_records contain duplicate record IDs")
        for record in self.authority_records:
            if record.branch_id != self.branch_id:
                raise ContractValidationError("authority record branch does not match bundle")
            if record.artifact_id != self.artifact.artifact_id:
                raise ContractValidationError("authority record artifact does not match bundle")
        self._validate_receipts(
            self.validation_receipt_ids,
            (IdKind.VALIDATION, IdKind.REALIZATION_VERIFICATION),
            "validation",
        )
        self._validate_receipts(self.lookup_receipt_ids, IdKind.LOOKUP_RECEIPT, "lookup")
        self._validate_receipts(self.provider_receipt_ids, IdKind.PROVIDER_RECEIPT, "provider")
        receipt_record_ids = [str(record.receipt_id) for record in self.receipt_records]
        if len(receipt_record_ids) != len(set(receipt_record_ids)):
            raise ContractValidationError("receipt_records contain duplicate receipt IDs")
        if self.receipt_records:
            declared = {
                *(str(value) for value in self.validation_receipt_ids),
                *(str(value) for value in self.lookup_receipt_ids),
                *(str(value) for value in self.provider_receipt_ids),
            }
            if set(receipt_record_ids) != declared:
                raise ContractValidationError(
                    "receipt_records must exactly cover every declared receipt ID"
                )
        work_ids = [str(value.work_id) for value in self.post_publication_work_requests]
        if len(work_ids) != len(set(work_ids)):
            raise ContractValidationError(
                "post_publication_work_requests contain duplicate work IDs"
            )
        work_kinds = [value.kind for value in self.post_publication_work_requests]
        if len(work_kinds) != len(set(work_kinds)):
            raise ContractValidationError(
                "a turn may schedule at most one post-publication item of each kind"
            )
        work_by_id = {
            value.work_id: value for value in self.post_publication_work_requests
        }
        for work in self.post_publication_work_requests:
            if work.branch_id != self.branch_id or work.artifact_id != self.artifact.artifact_id:
                raise ContractValidationError(
                    "post-publication work must bind the committed branch and artifact"
                )
            work_payload = json.loads(work.request_json)
            if work_payload["artifact_sha256"] != self.artifact_sha256:
                raise ContractValidationError(
                    "post-publication work changed the accepted-artifact binding"
                )
            if work.kind is PostPublicationWorkKind.CONSOLIDATE:
                direct_event_id = TypedId.parse(
                    work_payload["direct_event_id"], IdKind.EVENT
                )
                if direct_event_id not in {
                    record.record_id
                    for record in self.authority_records
                    if record.record_id.kind is IdKind.EVENT
                }:
                    raise ContractValidationError(
                        "consolidation work must bind a direct event in the same commit"
                    )
            if work.depends_on_work_id is not None:
                dependency = work_by_id.get(work.depends_on_work_id)
                if dependency is None:
                    raise ContractValidationError(
                        "post-publication dependency must be scheduled in the same bundle"
                    )
                if dependency.kind is not PostPublicationWorkKind.CONSOLIDATE:
                    raise ContractValidationError(
                        "derived-view work must depend on consolidation work"
                    )
        if (self.external_receipt_id is None) != (self.blocked_checkpoint_id is None):
            raise ContractValidationError(
                "external receipt and blocked checkpoint bindings must be supplied together"
            )
        if self.external_receipt_id is not None:
            require_kind(
                self.external_receipt_id,
                IdKind.EXTERNAL_RECEIPT,
                "external_receipt_id",
            )
            assert self.blocked_checkpoint_id is not None
            require_kind(
                self.blocked_checkpoint_id,
                IdKind.CHECKPOINT,
                "blocked_checkpoint_id",
            )

    @staticmethod
    def _validate_receipts(
        receipt_ids: tuple[TypedId, ...],
        expected_kind: IdKind | tuple[IdKind, ...],
        label: str,
    ) -> None:
        rendered = [str(receipt_id) for receipt_id in receipt_ids]
        if len(rendered) != len(set(rendered)):
            raise ContractValidationError(f"{label} receipt IDs contain duplicates")
        for receipt_id in receipt_ids:
            if isinstance(expected_kind, tuple):
                if receipt_id.kind not in expected_kind:
                    raise ContractValidationError(
                        f"{label}_receipt_ids contains an unsupported typed ID"
                    )
            else:
                require_kind(receipt_id, expected_kind, f"{label}_receipt_ids")

    @property
    def artifact_sha256(self) -> str:
        return domain_sha256("cera.accepted_story_artifact.v1", self.artifact)

    @property
    def bundle_sha256(self) -> str:
        return domain_sha256("cera.turn_commit_bundle.v2", self)


@dataclass(frozen=True, slots=True)
class BranchState:
    world_id: TypedId
    branch_id: TypedId
    parent_branch_id: TypedId | None
    fork_artifact_id: TypedId | None
    head_artifact_id: TypedId | None
    generation: int
    status: str
    authority_revision: int = 0


@dataclass(frozen=True, slots=True)
class JournalEntry:
    transaction_id: TypedId
    idempotency_key: str
    branch_id: TypedId
    bundle_sha256: str
    status: JournalStatus
    receipt_id: TypedId | None
    rollback_reason: str | None


@dataclass(frozen=True, slots=True)
class RecoveryReport:
    rolled_back_transaction_ids: tuple[TypedId, ...]


@dataclass(frozen=True, slots=True)
class StoredCommit:
    receipt: CommitReceipt
    exact_replay: bool
