"""Durable, independently recoverable work after ordinary publication."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import json

from cera.composer import PresentationRendererPort, RenderedStory
from cera.consolidation import (
    ConsolidationStagingResult,
    DerivedConsolidationRequest,
    DerivedConsolidatorPort,
    DerivedViewBuildReceipt,
    StoredConsolidation,
)
from cera.consolidation.validator import ConsolidationValidator
from cera.contracts import EvidenceRecordType
from cera.evidence import (
    EvidenceAccessScope,
    EvidenceFetchRequest,
    EvidenceRequesterRole,
    EvidenceService,
    EvidenceWorldMode,
)
from cera.ids import IdKind, TypedId, deterministic_id
from cera.schema import from_mapping
from cera.serialization import canonical_json, text_sha256
from cera.storage import (
    PostPublicationWork,
    PostPublicationWorkKind,
    PostPublicationWorkRequest,
    PostPublicationWorkStatus,
    SQLiteAuthorityStore,
    StoredCommit,
)

from .commit import OrdinaryTurnCommitCoordinator
from .models import IngressPublicationEvidence, LiveShapedTurnResult


class PostPublicationStatus(StrEnum):
    NOT_REQUESTED = "not_requested"
    PENDING = "pending"
    COMPLETED = "completed"
    NO_CHANGE = "no_change"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class PostPublicationResult:
    publication: StoredCommit
    rendered_story: RenderedStory | None
    render_status: PostPublicationStatus
    render_error: str | None
    consolidation_request: DerivedConsolidationRequest | None
    consolidation_staging: ConsolidationStagingResult | None
    consolidation_commit: StoredConsolidation | None
    consolidation_status: PostPublicationStatus
    consolidation_error: str | None
    derived_view_receipt: DerivedViewBuildReceipt | None
    derived_view_status: PostPublicationStatus
    derived_view_error: str | None
    work_items: tuple[PostPublicationWork, ...]

    def __post_init__(self) -> None:
        if self.publication.receipt.outcome != "committed":
            raise ValueError("post-publication work requires a committed artifact")
        _status_payload(self.render_status, self.rendered_story, self.render_error, "render")
        _status_payload(
            self.derived_view_status,
            self.derived_view_receipt,
            self.derived_view_error,
            "derived view",
            supports_no_change=True,
        )
        if self.consolidation_status in {
            PostPublicationStatus.NOT_REQUESTED,
            PostPublicationStatus.PENDING,
        }:
            if any(
                value is not None
                for value in (
                    self.consolidation_request,
                    self.consolidation_staging,
                    self.consolidation_commit,
                    self.consolidation_error,
                )
            ):
                raise ValueError("inactive consolidation cannot carry output")
        elif self.consolidation_status is PostPublicationStatus.FAILED:
            if self.consolidation_error is None or self.consolidation_commit is not None:
                raise ValueError("failed consolidation must carry only an error")
        elif self.consolidation_status is PostPublicationStatus.NO_CHANGE:
            if self.consolidation_commit is not None or self.consolidation_error is not None:
                raise ValueError("no-change consolidation state is inconsistent")
            if (self.consolidation_request is None) != (
                self.consolidation_staging is None
            ):
                raise ValueError("no-change replay evidence must be supplied together")
            if (
                self.consolidation_staging is not None
                and self.consolidation_staging.bundle is not None
            ):
                raise ValueError("no-change consolidation cannot carry a bundle")
        elif self.consolidation_commit is None or self.consolidation_error is not None:
            raise ValueError("completed consolidation state is inconsistent")
        elif not self.consolidation_commit.exact_replay and (
            self.consolidation_request is None
            or self.consolidation_staging is None
            or self.consolidation_staging.bundle is None
        ):
            raise ValueError("new consolidation commit requires request and staging evidence")


class PostPublicationCoordinator:
    """Publish once, then dispatch durable work without hidden retries."""

    def __init__(self, store: SQLiteAuthorityStore) -> None:
        self.store = store
        self.publication = OrdinaryTurnCommitCoordinator(store)

    def execute(
        self,
        result: LiveShapedTurnResult,
        *,
        renderer: PresentationRendererPort | None = None,
        renderer_profile: str | None = None,
        consolidator: DerivedConsolidatorPort | None = None,
        ingress_evidence: IngressPublicationEvidence | None = None,
    ) -> PostPublicationResult:
        if renderer is not None and (
            renderer_profile is None or not renderer_profile.strip()
        ):
            raise ValueError("renderer profile is required")
        if renderer is None and renderer_profile is not None:
            raise ValueError("renderer profile cannot be supplied without a renderer")
        work_requests = _work_requests(
            result,
            renderer_profile=renderer_profile if renderer is not None else None,
            consolidation_requested=consolidator is not None,
        )
        published = self.publication.commit(
            result,
            post_publication_work_requests=work_requests,
            ingress_evidence=ingress_evidence,
        )
        return self._dispatch(
            published,
            renderer=renderer,
            consolidator=consolidator,
            retry_failed_work_ids=(),
            authorization_reason=None,
        )

    def resume(
        self,
        artifact_id: TypedId,
        *,
        renderer: PresentationRendererPort | None = None,
        consolidator: DerivedConsolidatorPort | None = None,
        retry_failed_work_ids: tuple[TypedId, ...] = (),
        authorization_reason: str | None = None,
    ) -> PostPublicationResult:
        if retry_failed_work_ids and (
            authorization_reason is None or not authorization_reason.strip()
        ):
            raise ValueError("failed-work retry requires an authorization reason")
        if len({str(value) for value in retry_failed_work_ids}) != len(
            retry_failed_work_ids
        ):
            raise ValueError("retry_failed_work_ids cannot contain duplicates")
        published = self.store.get_commit_for_artifact(artifact_id)
        return self._dispatch(
            published,
            renderer=renderer,
            consolidator=consolidator,
            retry_failed_work_ids=retry_failed_work_ids,
            authorization_reason=authorization_reason,
        )

    def _dispatch(
        self,
        published: StoredCommit,
        *,
        renderer: PresentationRendererPort | None,
        consolidator: DerivedConsolidatorPort | None,
        retry_failed_work_ids: tuple[TypedId, ...],
        authorization_reason: str | None,
    ) -> PostPublicationResult:
        artifact_id = published.receipt.new_artifact_id
        artifact = self.store.get_artifact(artifact_id)
        retry_ids = frozenset(retry_failed_work_ids)
        work = {
            value.kind: value
            for value in self.store.post_publication_work_for_artifact(artifact_id)
        }
        scheduled_ids = frozenset(value.work_id for value in work.values())
        unknown_retry_ids = retry_ids - scheduled_ids
        if unknown_retry_ids:
            raise ValueError("retry work IDs must belong to the selected artifact")
        nonfailed_retry_ids = {
            value.work_id
            for value in work.values()
            if value.work_id in retry_ids
            and value.status is not PostPublicationWorkStatus.FAILED
        }
        if nonfailed_retry_ids:
            raise ValueError("only failed work IDs may be explicitly retried")

        rendered, render_status, render_error = self._dispatch_render(
            work.get(PostPublicationWorkKind.RENDER),
            artifact,
            renderer,
            retry_ids,
            authorization_reason,
        )
        (
            consolidation_request,
            consolidation_staging,
            consolidation_commit,
            consolidation_status,
            consolidation_error,
        ) = self._dispatch_consolidation(
            work.get(PostPublicationWorkKind.CONSOLIDATE),
            published,
            consolidator,
            retry_ids,
            authorization_reason,
        )
        view_receipt, view_status, view_error = self._dispatch_views(
            work.get(PostPublicationWorkKind.DERIVED_VIEWS),
            retry_ids,
            authorization_reason,
        )
        return PostPublicationResult(
            publication=published,
            rendered_story=rendered,
            render_status=render_status,
            render_error=render_error,
            consolidation_request=consolidation_request,
            consolidation_staging=consolidation_staging,
            consolidation_commit=consolidation_commit,
            consolidation_status=consolidation_status,
            consolidation_error=consolidation_error,
            derived_view_receipt=view_receipt,
            derived_view_status=view_status,
            derived_view_error=view_error,
            work_items=self.store.post_publication_work_for_artifact(artifact_id),
        )

    def _dispatch_render(
        self,
        work: PostPublicationWork | None,
        artifact,
        renderer: PresentationRendererPort | None,
        retry_ids: frozenset[TypedId],
        authorization_reason: str | None,
    ) -> tuple[RenderedStory | None, PostPublicationStatus, str | None]:
        if work is None:
            return None, PostPublicationStatus.NOT_REQUESTED, None
        if work.status is PostPublicationWorkStatus.COMPLETED:
            return _rendered_from_work(work), PostPublicationStatus.COMPLETED, None
        if work.status is PostPublicationWorkStatus.RUNNING:
            return None, PostPublicationStatus.PENDING, None
        retry = work.work_id in retry_ids
        if work.status is PostPublicationWorkStatus.FAILED and not retry:
            return None, PostPublicationStatus.FAILED, _durable_error(work)
        if renderer is None:
            return None, PostPublicationStatus.PENDING, None
        request = json.loads(work.request_json)
        try:
            self.store.claim_post_publication_work(
                work.work_id,
                retry_failed=retry,
                authorization_reason=authorization_reason,
            )
            rendered = renderer.render(artifact, profile=request["renderer_profile"])
            if (
                rendered.accepted_artifact_id != artifact.artifact_id
                or rendered.accepted_artifact_sha256 != request["artifact_sha256"]
            ):
                raise ValueError("renderer output changed accepted-artifact binding")
            self.store.complete_post_publication_work(
                work.work_id,
                result_payload={
                    "schema_version": "cera.post_publication_render_result.v1",
                    "rendered_story": rendered,
                },
            )
            return rendered, PostPublicationStatus.COMPLETED, None
        except Exception as exc:
            self._record_failure(work.work_id, "render", exc)
            return None, PostPublicationStatus.FAILED, _ephemeral_error(exc)

    def _dispatch_consolidation(
        self,
        work: PostPublicationWork | None,
        published: StoredCommit,
        consolidator: DerivedConsolidatorPort | None,
        retry_ids: frozenset[TypedId],
        authorization_reason: str | None,
    ):
        if work is None:
            return None, None, None, PostPublicationStatus.NOT_REQUESTED, None
        if work.status is PostPublicationWorkStatus.COMPLETED:
            existing = self.store.find_consolidation_by_request_id(
                _consolidation_request_id(published.receipt.new_artifact_id)
            )
            if existing is None:
                return None, None, None, PostPublicationStatus.FAILED, (
                    "TransactionError: completed work has no consolidation receipt"
                )
            return None, None, existing, PostPublicationStatus.COMPLETED, None
        if work.status is PostPublicationWorkStatus.NO_CHANGE:
            return None, None, None, PostPublicationStatus.NO_CHANGE, None
        if work.status is PostPublicationWorkStatus.RUNNING:
            return None, None, None, PostPublicationStatus.PENDING, None
        retry = work.work_id in retry_ids
        if work.status is PostPublicationWorkStatus.FAILED and not retry:
            return None, None, None, PostPublicationStatus.FAILED, _durable_error(work)
        if consolidator is None:
            return None, None, None, PostPublicationStatus.PENDING, None
        request = None
        staging = None
        committed = None
        try:
            self.store.claim_post_publication_work(
                work.work_id,
                retry_failed=retry,
                authorization_reason=authorization_reason,
            )
            request_id = _consolidation_request_id(published.receipt.new_artifact_id)
            existing = self.store.find_consolidation_by_request_id(request_id)
            if existing is not None:
                committed = existing
            else:
                request = self._consolidation_request(work, published)
                execution = consolidator.consolidate(request)
                staging = ConsolidationValidator(
                    self.store, EvidenceService(self.store)
                ).stage(
                    request,
                    execution,
                    transaction_id=deterministic_id(
                        IdKind.TRANSACTION,
                        "cera.post_publication.consolidation.v1",
                        f"{request.request_id}|{execution.proposal.proposal_sha256}",
                    ),
                    idempotency_key=(
                        f"post-publication:{published.receipt.new_artifact_id.value}"
                    ),
                )
                if staging.bundle is not None:
                    committed = self.store.commit_consolidation(staging.bundle)
                else:
                    self.store.complete_post_publication_work(
                        work.work_id,
                        result_payload={
                            "schema_version": (
                                "cera.post_publication_consolidation_no_change.v1"
                            ),
                            "request_id": request.request_id,
                            "request_sha256": request.request_sha256,
                            "proposal_sha256": execution.proposal.proposal_sha256,
                            "lookup_receipts": request.lookup_receipts,
                            "consolidator_receipt": execution.receipt,
                            "validation_receipt": staging.validation_receipt,
                        },
                        no_change=True,
                    )
                    return (
                        request,
                        staging,
                        None,
                        PostPublicationStatus.NO_CHANGE,
                        None,
                    )
            assert committed is not None
            self.store.complete_post_publication_work(
                work.work_id,
                result_payload={
                    "schema_version": "cera.post_publication_consolidation_result.v1",
                    "request_id": request_id,
                    "commit_receipt": committed.receipt,
                },
            )
            return (
                request,
                staging,
                committed,
                PostPublicationStatus.COMPLETED,
                None,
            )
        except Exception as exc:
            self._record_failure(work.work_id, "consolidation", exc)
            return None, None, None, PostPublicationStatus.FAILED, _ephemeral_error(exc)

    def _dispatch_views(
        self,
        work: PostPublicationWork | None,
        retry_ids: frozenset[TypedId],
        authorization_reason: str | None,
    ) -> tuple[DerivedViewBuildReceipt | None, PostPublicationStatus, str | None]:
        if work is None:
            return None, PostPublicationStatus.NOT_REQUESTED, None
        if work.status is PostPublicationWorkStatus.COMPLETED:
            return _view_receipt_from_work(work), PostPublicationStatus.COMPLETED, None
        if work.status is PostPublicationWorkStatus.NO_CHANGE:
            return None, PostPublicationStatus.NO_CHANGE, None
        if work.status is PostPublicationWorkStatus.RUNNING:
            return None, PostPublicationStatus.PENDING, None
        dependency = self.store.get_post_publication_work(work.depends_on_work_id)
        if dependency.status not in {
            PostPublicationWorkStatus.COMPLETED,
            PostPublicationWorkStatus.NO_CHANGE,
        }:
            return None, PostPublicationStatus.PENDING, None
        retry = work.work_id in retry_ids
        if work.status is PostPublicationWorkStatus.FAILED and not retry:
            return None, PostPublicationStatus.FAILED, _durable_error(work)
        try:
            self.store.claim_post_publication_work(
                work.work_id,
                retry_failed=retry,
                authorization_reason=authorization_reason,
            )
            if dependency.status is PostPublicationWorkStatus.NO_CHANGE:
                self.store.complete_post_publication_work(
                    work.work_id,
                    result_payload={
                        "schema_version": "cera.post_publication_view_no_change.v1",
                        "reason": "consolidation produced no derived authority change",
                        "dependency_work_id": dependency.work_id,
                        "dependency_result_sha256": dependency.result_sha256,
                    },
                    no_change=True,
                )
                return None, PostPublicationStatus.NO_CHANGE, None
            receipt = self.store.rebuild_derived_views(work.branch_id)
            self.store.complete_post_publication_work(
                work.work_id,
                result_payload={
                    "schema_version": "cera.post_publication_view_result.v1",
                    "derived_view_receipt": receipt,
                },
            )
            return receipt, PostPublicationStatus.COMPLETED, None
        except Exception as exc:
            self._record_failure(work.work_id, "derived_views", exc)
            return None, PostPublicationStatus.FAILED, _ephemeral_error(exc)

    def _consolidation_request(
        self,
        work: PostPublicationWork,
        published: StoredCommit,
    ) -> DerivedConsolidationRequest:
        payload = json.loads(work.request_json)
        artifact = self.store.get_artifact(published.receipt.new_artifact_id)
        branch = self.store.get_branch(artifact.branch_id)
        request_id = _consolidation_request_id(artifact.artifact_id)
        service = EvidenceService(self.store)
        snapshot = service.open_snapshot(
            request_id=request_id,
            world_id=branch.world_id,
            branch_id=artifact.branch_id,
            access_scope=EvidenceAccessScope(
                requester_role=EvidenceRequesterRole.SYSTEM_REASONER,
                perspective_id=None,
                permitted_private_owner_ids=artifact.responding_npc_ids,
                allow_system_private=True,
                allowed_content_classes=("ordinary", "system"),
                allow_audit_history=False,
            ),
            world_mode=EvidenceWorldMode(payload["world_mode"]),
        )
        event_id = TypedId.parse(payload["direct_event_id"], IdKind.EVENT)
        event_evidence_id = EvidenceService._evidence_id(snapshot, event_id)
        fetched = service.fetch_evidence(
            snapshot,
            EvidenceFetchRequest(
                (event_evidence_id,),
                ("event", "source_coverage", "artifact_binding"),
            ),
        )
        return DerivedConsolidationRequest(
            schema_version=DerivedConsolidationRequest.SCHEMA_VERSION,
            request_id=request_id,
            trace_id=deterministic_id(
                IdKind.TRACE,
                "cera.post_publication.consolidation_trace.v1",
                str(artifact.artifact_id),
            ),
            protected_user_id=TypedId.parse(
                payload["protected_user_id"], IdKind.CHARACTER
            ),
            snapshot=snapshot,
            exact_evidence=fetched.exact_records,
            lookup_receipt_ids=(fetched.receipt.lookup_receipt_id,),
            eligible_record_types=(
                EvidenceRecordType.MEMORY,
                EvidenceRecordType.RELATIONSHIP,
                EvidenceRecordType.THREAD,
                EvidenceRecordType.DEVELOPMENT,
            ),
            maximum_records=8,
            hard_boundaries=(
                "No objective event creation; the exact accepted-turn event is the source.",
                "No Genesis rewrite or automatic diagnosis.",
                "No protected-user private-state authorship.",
                "Every derived record must cite the exact accepted-turn event.",
            ),
            lookup_receipts=(fetched.receipt,),
        )

    def _record_failure(self, work_id: TypedId, stage: str, exc: Exception) -> None:
        try:
            current = self.store.get_post_publication_work(work_id)
            if current.status is PostPublicationWorkStatus.RUNNING:
                self.store.fail_post_publication_work(
                    work_id,
                    stage=stage,
                    error_type=type(exc).__name__,
                )
        except Exception:
            # The original failure remains the caller-visible error. A journal
            # persistence failure must not masquerade as successful work.
            pass


def _work_requests(
    result: LiveShapedTurnResult,
    *,
    renderer_profile: str | None,
    consolidation_requested: bool,
) -> tuple[PostPublicationWorkRequest, ...]:
    artifact = result.accepted_artifact
    prepared = result.context.request.prepared_turn
    requests: list[PostPublicationWorkRequest] = []
    if renderer_profile is not None:
        requests.append(
            _work_request(
                kind=PostPublicationWorkKind.RENDER,
                branch_id=artifact.branch_id,
                artifact_id=artifact.artifact_id,
                payload={
                    "schema_version": "cera.post_publication_render_work.v1",
                    "renderer_profile": renderer_profile,
                    "artifact_sha256": result.receipt.accepted_artifact_sha256,
                },
            )
        )
    if consolidation_requested:
        event_id = deterministic_id(
            IdKind.EVENT,
            "cera.accepted_turn_event.v1",
            str(artifact.artifact_id),
        )
        consolidation = _work_request(
            kind=PostPublicationWorkKind.CONSOLIDATE,
            branch_id=artifact.branch_id,
            artifact_id=artifact.artifact_id,
            payload={
                "schema_version": "cera.post_publication_consolidation_work.v1",
                "protected_user_id": prepared.request.protected_user_id,
                "world_mode": prepared.evidence_snapshot.world_mode.value,
                "direct_event_id": event_id,
                "artifact_sha256": result.receipt.accepted_artifact_sha256,
            },
        )
        requests.append(consolidation)
        requests.append(
            _work_request(
                kind=PostPublicationWorkKind.DERIVED_VIEWS,
                branch_id=artifact.branch_id,
                artifact_id=artifact.artifact_id,
                payload={
                    "schema_version": "cera.post_publication_view_work.v1",
                    "artifact_sha256": result.receipt.accepted_artifact_sha256,
                    "consolidation_work_id": consolidation.work_id,
                },
                depends_on_work_id=consolidation.work_id,
            )
        )
    return tuple(requests)


def _work_request(
    *,
    kind: PostPublicationWorkKind,
    branch_id: TypedId,
    artifact_id: TypedId,
    payload: object,
    depends_on_work_id: TypedId | None = None,
) -> PostPublicationWorkRequest:
    request_json = canonical_json(payload)
    work_id = deterministic_id(
        IdKind.POST_PUBLICATION_WORK,
        "cera.post_publication_work.v1",
        f"{artifact_id}|{kind.value}|{text_sha256(request_json)}",
    )
    return PostPublicationWorkRequest(
        work_id=work_id,
        kind=kind,
        branch_id=branch_id,
        artifact_id=artifact_id,
        request_json=request_json,
        request_sha256=text_sha256(request_json),
        depends_on_work_id=depends_on_work_id,
    )


def _status_payload(
    status,
    payload,
    error,
    label: str,
    *,
    supports_no_change: bool = False,
) -> None:
    if status in {PostPublicationStatus.NOT_REQUESTED, PostPublicationStatus.PENDING}:
        if payload is not None or error is not None:
            raise ValueError(f"inactive {label} cannot carry output")
    elif status is PostPublicationStatus.COMPLETED:
        if payload is None or error is not None:
            raise ValueError(f"completed {label} state is inconsistent")
    elif status is PostPublicationStatus.NO_CHANGE and supports_no_change:
        if payload is not None or error is not None:
            raise ValueError(f"no-change {label} cannot carry output")
    elif status is PostPublicationStatus.FAILED:
        if payload is not None or error is None:
            raise ValueError(f"failed {label} state is inconsistent")
    else:
        raise ValueError(f"{label} has an unsupported status")


def _rendered_from_work(work: PostPublicationWork) -> RenderedStory:
    payload = json.loads(work.result_json)
    return from_mapping(RenderedStory, payload["rendered_story"])


def _view_receipt_from_work(work: PostPublicationWork) -> DerivedViewBuildReceipt:
    payload = json.loads(work.result_json)
    return from_mapping(DerivedViewBuildReceipt, payload["derived_view_receipt"])


def _durable_error(work: PostPublicationWork) -> str:
    payload = json.loads(work.error_json)
    return f"{payload['error_type']}: {payload['message']}"


def _ephemeral_error(exc: Exception) -> str:
    message = " ".join(str(exc).split())
    if len(message) > 500:
        message = message[:497] + "..."
    return f"{type(exc).__name__}: {message}"


def _consolidation_request_id(artifact_id: TypedId) -> TypedId:
    return deterministic_id(
        IdKind.REQUEST,
        "cera.post_publication.consolidation_request.v1",
        str(artifact_id),
    )
