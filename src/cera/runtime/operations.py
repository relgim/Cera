"""Secret-safe operator boundary for durable post-publication work."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import json
from typing import ClassVar

from cera.composer import PresentationRendererPort
from cera.consolidation import DerivedConsolidatorPort
from cera.errors import ErrorCode, RetryMode
from cera.ids import IdKind, TypedId, deterministic_id, require_kind
from cera.serialization import domain_sha256, re_is_sha256, text_sha256
from cera.storage import (
    PostPublicationWork,
    PostPublicationWorkKind,
    PostPublicationWorkStatus,
    SQLiteAuthorityStore,
)

from .post_publication import PostPublicationCoordinator


class PostPublicationOperation(StrEnum):
    DISPATCH_PENDING = "dispatch_pending"
    RETRY_FAILED = "retry_failed"
    RECOVER_INTERRUPTED = "recover_interrupted"


@dataclass(frozen=True, slots=True)
class PostPublicationWorkSummary:
    SCHEMA_VERSION: ClassVar[str] = "cera.post_publication_work_summary.v1"

    schema_version: str
    work_id: TypedId
    kind: PostPublicationWorkKind
    branch_id: TypedId
    artifact_id: TypedId
    status: PostPublicationWorkStatus
    attempt_count: int
    depends_on_work_id: TypedId | None
    dependency_status: PostPublicationWorkStatus | None
    request_sha256: str
    result_sha256: str | None
    error_sha256: str | None
    retry_requires_authorization: bool

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ValueError("post-publication work summary schema is invalid")
        require_kind(self.work_id, IdKind.POST_PUBLICATION_WORK, "work_id")
        require_kind(self.branch_id, IdKind.BRANCH, "branch_id")
        require_kind(self.artifact_id, IdKind.ARTIFACT, "artifact_id")
        if self.depends_on_work_id is not None:
            require_kind(
                self.depends_on_work_id,
                IdKind.POST_PUBLICATION_WORK,
                "depends_on_work_id",
            )
        if self.attempt_count < 0:
            raise ValueError("work summary attempt_count cannot be negative")
        for value in (self.request_sha256, self.result_sha256, self.error_sha256):
            if value is not None and not re_is_sha256(value):
                raise ValueError("work summary hashes must be SHA-256")
        if self.retry_requires_authorization != (
            self.status is PostPublicationWorkStatus.FAILED
        ):
            raise ValueError("work summary retry flag is inconsistent")


@dataclass(frozen=True, slots=True)
class PostPublicationWorkErrorEnvelope:
    SCHEMA_VERSION: ClassVar[str] = "cera.post_publication_work_error_envelope.v1"

    schema_version: str
    error_code: ErrorCode
    message: str
    trace_id: TypedId
    work_id: TypedId
    kind: PostPublicationWorkKind
    branch_id: TypedId
    artifact_id: TypedId
    story_state_retained: bool
    retry_mode: RetryMode
    details: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ValueError("post-publication error-envelope schema is invalid")
        require_kind(self.trace_id, IdKind.TRACE, "trace_id")
        require_kind(self.work_id, IdKind.POST_PUBLICATION_WORK, "work_id")
        require_kind(self.branch_id, IdKind.BRANCH, "branch_id")
        require_kind(self.artifact_id, IdKind.ARTIFACT, "artifact_id")
        if not self.message.strip():
            raise ValueError("post-publication error message must be non-empty")
        if not self.story_state_retained:
            raise ValueError("post-publication failures must retain accepted story truth")
        if self.retry_mode is not RetryMode.MANUAL_AFTER_REVIEW:
            raise ValueError("post-publication failure retry must require manual review")
        if self.details:
            raise ValueError("operator error envelopes cannot expose internal details")


@dataclass(frozen=True, slots=True)
class PostPublicationStatusReport:
    SCHEMA_VERSION: ClassVar[str] = "cera.post_publication_status_report.v1"

    schema_version: str
    report_id: TypedId
    branch_id: TypedId
    artifact_id: TypedId
    work_items: tuple[PostPublicationWorkSummary, ...]
    errors: tuple[PostPublicationWorkErrorEnvelope, ...]
    pending_count: int
    running_count: int
    failed_count: int
    terminal_count: int
    story_state_retained: bool

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ValueError("post-publication report schema is invalid")
        require_kind(self.report_id, IdKind.VALIDATION, "report_id")
        require_kind(self.branch_id, IdKind.BRANCH, "branch_id")
        require_kind(self.artifact_id, IdKind.ARTIFACT, "artifact_id")
        if not self.story_state_retained:
            raise ValueError("operator status must retain accepted story truth")
        if any(
            value.branch_id != self.branch_id or value.artifact_id != self.artifact_id
            for value in self.work_items
        ):
            raise ValueError("status report work identity mismatch")
        counts = {
            PostPublicationWorkStatus.PENDING: self.pending_count,
            PostPublicationWorkStatus.RUNNING: self.running_count,
            PostPublicationWorkStatus.FAILED: self.failed_count,
        }
        for status, expected in counts.items():
            if expected != sum(value.status is status for value in self.work_items):
                raise ValueError("status report count mismatch")
        if self.terminal_count != sum(
            value.status
            in {
                PostPublicationWorkStatus.COMPLETED,
                PostPublicationWorkStatus.NO_CHANGE,
            }
            for value in self.work_items
        ):
            raise ValueError("status report terminal count mismatch")
        if {value.work_id for value in self.errors} != {
            value.work_id
            for value in self.work_items
            if value.status is PostPublicationWorkStatus.FAILED
        }:
            raise ValueError("status report errors must exactly cover failed work")

    @property
    def report_sha256(self) -> str:
        return domain_sha256("cera.post_publication_status_report.v1", self)


@dataclass(frozen=True, slots=True)
class PostPublicationOperationReceipt:
    SCHEMA_VERSION: ClassVar[str] = "cera.post_publication_operation_receipt.v1"

    schema_version: str
    receipt_id: TypedId
    operation: PostPublicationOperation
    requested_work_ids: tuple[TypedId, ...]
    affected_work_ids: tuple[TypedId, ...]
    authorization_reason_sha256: str | None
    report_sha256s: tuple[str, ...]
    story_state_retained: bool

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ValueError("post-publication operation receipt schema is invalid")
        require_kind(self.receipt_id, IdKind.VALIDATION, "receipt_id")
        for value in (*self.requested_work_ids, *self.affected_work_ids):
            require_kind(value, IdKind.POST_PUBLICATION_WORK, "operation work ID")
        if len(self.requested_work_ids) != len(set(self.requested_work_ids)):
            raise ValueError("requested work IDs cannot contain duplicates")
        if len(self.affected_work_ids) != len(set(self.affected_work_ids)):
            raise ValueError("affected work IDs cannot contain duplicates")
        if self.authorization_reason_sha256 is not None and not re_is_sha256(
            self.authorization_reason_sha256
        ):
            raise ValueError("authorization reason hash must be SHA-256")
        if any(not re_is_sha256(value) for value in self.report_sha256s):
            raise ValueError("operation report hashes must be SHA-256")
        if not self.story_state_retained:
            raise ValueError("operator commands cannot undo accepted story truth")


@dataclass(frozen=True, slots=True)
class PostPublicationOperationResult:
    receipt: PostPublicationOperationReceipt
    reports: tuple[PostPublicationStatusReport, ...]

    def __post_init__(self) -> None:
        if self.receipt.report_sha256s != tuple(
            value.report_sha256 for value in self.reports
        ):
            raise ValueError("operation receipt does not bind its status reports")


class PostPublicationOperationsService:
    """No-prose service boundary for inspection and explicit work commands."""

    ACTIONABLE = (
        PostPublicationWorkStatus.PENDING,
        PostPublicationWorkStatus.RUNNING,
        PostPublicationWorkStatus.FAILED,
    )

    def __init__(self, store: SQLiteAuthorityStore) -> None:
        self.store = store
        self.coordinator = PostPublicationCoordinator(store)

    def inspect_artifact(self, artifact_id: TypedId) -> PostPublicationStatusReport:
        require_kind(artifact_id, IdKind.ARTIFACT, "artifact_id")
        work = self.store.post_publication_work_for_artifact(artifact_id)
        if not work:
            artifact = self.store.get_artifact(artifact_id)
            return self._report(artifact.branch_id, artifact_id, ())
        return self._report(work[0].branch_id, artifact_id, work)

    def list_actionable(
        self, *, branch_id: TypedId | None = None
    ) -> tuple[PostPublicationStatusReport, ...]:
        work = self.store.list_post_publication_work(
            statuses=self.ACTIONABLE,
            branch_id=branch_id,
        )
        artifact_ids = tuple(dict.fromkeys(value.artifact_id for value in work))
        return tuple(self.inspect_artifact(value) for value in artifact_ids)

    def dispatch_pending(
        self,
        artifact_id: TypedId,
        *,
        renderer: PresentationRendererPort | None = None,
        consolidator: DerivedConsolidatorPort | None = None,
    ) -> PostPublicationOperationResult:
        before = self.inspect_artifact(artifact_id)
        requested = tuple(
            value.work_id
            for value in before.work_items
            if value.status is PostPublicationWorkStatus.PENDING
        )
        self.coordinator.resume(
            artifact_id,
            renderer=renderer,
            consolidator=consolidator,
        )
        after = self.inspect_artifact(artifact_id)
        return self._operation_result(
            PostPublicationOperation.DISPATCH_PENDING,
            requested,
            before,
            after,
            authorization_reason=None,
        )

    def retry_failed(
        self,
        artifact_id: TypedId,
        *,
        work_ids: tuple[TypedId, ...],
        authorization_reason: str,
        renderer: PresentationRendererPort | None = None,
        consolidator: DerivedConsolidatorPort | None = None,
    ) -> PostPublicationOperationResult:
        if not work_ids:
            raise ValueError("retry_failed requires at least one exact work ID")
        if not authorization_reason.strip():
            raise ValueError("retry_failed requires a non-empty authorization reason")
        before = self.inspect_artifact(artifact_id)
        failed_ids = {
            value.work_id
            for value in before.work_items
            if value.status is PostPublicationWorkStatus.FAILED
        }
        if set(work_ids) - failed_ids:
            raise ValueError("retry_failed accepts only failed work from this artifact")
        self.coordinator.resume(
            artifact_id,
            renderer=renderer,
            consolidator=consolidator,
            retry_failed_work_ids=work_ids,
            authorization_reason=authorization_reason,
        )
        after = self.inspect_artifact(artifact_id)
        return self._operation_result(
            PostPublicationOperation.RETRY_FAILED,
            work_ids,
            before,
            after,
            authorization_reason=authorization_reason,
        )

    def recover_interrupted(
        self, *, authorization_reason: str
    ) -> PostPublicationOperationResult:
        if not authorization_reason.strip():
            raise ValueError("restart recovery requires an authorization reason")
        running = self.store.list_post_publication_work(
            statuses=(PostPublicationWorkStatus.RUNNING,)
        )
        artifact_ids = tuple(dict.fromkeys(value.artifact_id for value in running))
        before_by_artifact = {
            value: self.inspect_artifact(value) for value in artifact_ids
        }
        recovered = self.store.recover_interrupted_post_publication_work(
            reason=authorization_reason
        )
        reports = tuple(self.inspect_artifact(value) for value in artifact_ids)
        before_items = {
            item.work_id: item
            for report in before_by_artifact.values()
            for item in report.work_items
        }
        affected = tuple(
            value
            for value in recovered
            if value in before_items
        )
        return self._receipt_result(
            operation=PostPublicationOperation.RECOVER_INTERRUPTED,
            requested_work_ids=tuple(value.work_id for value in running),
            affected_work_ids=affected,
            authorization_reason=authorization_reason,
            reports=reports,
        )

    def _report(
        self,
        branch_id: TypedId,
        artifact_id: TypedId,
        work: tuple[PostPublicationWork, ...],
    ) -> PostPublicationStatusReport:
        by_id = {value.work_id: value for value in work}
        summaries = tuple(
            PostPublicationWorkSummary(
                schema_version=PostPublicationWorkSummary.SCHEMA_VERSION,
                work_id=value.work_id,
                kind=value.kind,
                branch_id=value.branch_id,
                artifact_id=value.artifact_id,
                status=value.status,
                attempt_count=value.attempt_count,
                depends_on_work_id=value.depends_on_work_id,
                dependency_status=(
                    None
                    if value.depends_on_work_id is None
                    else by_id[value.depends_on_work_id].status
                ),
                request_sha256=value.request_sha256,
                result_sha256=value.result_sha256,
                error_sha256=value.error_sha256,
                retry_requires_authorization=(
                    value.status is PostPublicationWorkStatus.FAILED
                ),
            )
            for value in work
        )
        errors = tuple(self._error(value) for value in work if value.error_json is not None)
        report_key = domain_sha256(
            "cera.post_publication_status_report_key.v1",
            {
                "artifact_id": artifact_id,
                "work": summaries,
                "errors": errors,
            },
        )
        return PostPublicationStatusReport(
            schema_version=PostPublicationStatusReport.SCHEMA_VERSION,
            report_id=deterministic_id(
                IdKind.VALIDATION,
                "cera.post_publication_status_report.v1",
                report_key,
            ),
            branch_id=branch_id,
            artifact_id=artifact_id,
            work_items=summaries,
            errors=errors,
            pending_count=sum(
                value.status is PostPublicationWorkStatus.PENDING for value in work
            ),
            running_count=sum(
                value.status is PostPublicationWorkStatus.RUNNING for value in work
            ),
            failed_count=sum(
                value.status is PostPublicationWorkStatus.FAILED for value in work
            ),
            terminal_count=sum(
                value.status
                in {
                    PostPublicationWorkStatus.COMPLETED,
                    PostPublicationWorkStatus.NO_CHANGE,
                }
                for value in work
            ),
            story_state_retained=True,
        )

    @staticmethod
    def _error(work: PostPublicationWork) -> PostPublicationWorkErrorEnvelope:
        payload = json.loads(work.error_json)
        interrupted = payload.get("stage") == "restart_recovery"
        return PostPublicationWorkErrorEnvelope(
            schema_version=PostPublicationWorkErrorEnvelope.SCHEMA_VERSION,
            error_code=(
                ErrorCode.POST_PUBLICATION_WORK_INTERRUPTED
                if interrupted
                else ErrorCode.POST_PUBLICATION_WORK_FAILED
            ),
            message=payload["message"],
            trace_id=deterministic_id(
                IdKind.TRACE,
                "cera.post_publication_work_error.v1",
                f"{work.work_id}|{work.error_sha256}",
            ),
            work_id=work.work_id,
            kind=work.kind,
            branch_id=work.branch_id,
            artifact_id=work.artifact_id,
            story_state_retained=True,
            retry_mode=RetryMode.MANUAL_AFTER_REVIEW,
        )

    def _operation_result(
        self,
        operation: PostPublicationOperation,
        requested: tuple[TypedId, ...],
        before: PostPublicationStatusReport,
        after: PostPublicationStatusReport,
        *,
        authorization_reason: str | None,
    ) -> PostPublicationOperationResult:
        before_by_id = {value.work_id: value for value in before.work_items}
        affected = tuple(
            value.work_id
            for value in after.work_items
            if before_by_id.get(value.work_id) != value
        )
        return self._receipt_result(
            operation=operation,
            requested_work_ids=requested,
            affected_work_ids=affected,
            authorization_reason=authorization_reason,
            reports=(after,),
        )

    @staticmethod
    def _receipt_result(
        *,
        operation: PostPublicationOperation,
        requested_work_ids: tuple[TypedId, ...],
        affected_work_ids: tuple[TypedId, ...],
        authorization_reason: str | None,
        reports: tuple[PostPublicationStatusReport, ...],
    ) -> PostPublicationOperationResult:
        reason_sha256 = (
            None
            if authorization_reason is None
            else text_sha256(authorization_reason.strip())
        )
        report_hashes = tuple(value.report_sha256 for value in reports)
        receipt_key = domain_sha256(
            "cera.post_publication_operation_receipt_key.v1",
            {
                "operation": operation,
                "requested_work_ids": requested_work_ids,
                "affected_work_ids": affected_work_ids,
                "authorization_reason_sha256": reason_sha256,
                "report_sha256s": report_hashes,
            },
        )
        receipt = PostPublicationOperationReceipt(
            schema_version=PostPublicationOperationReceipt.SCHEMA_VERSION,
            receipt_id=deterministic_id(
                IdKind.VALIDATION,
                "cera.post_publication_operation_receipt.v1",
                receipt_key,
            ),
            operation=operation,
            requested_work_ids=requested_work_ids,
            affected_work_ids=affected_work_ids,
            authorization_reason_sha256=reason_sha256,
            report_sha256s=report_hashes,
            story_state_retained=True,
        )
        return PostPublicationOperationResult(receipt=receipt, reports=reports)
