"""Final provider-free ordinary-turn application facade."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import ClassVar

from cera.composer import (
    CompositionMode,
    FakeComposerFixture,
    PresentationRendererPort,
    SceneComposerPort,
)
from cera.config import Environment
from cera.consolidation import DerivedConsolidatorPort
from cera.contracts import AcceptedStoryArtifact, CommitReceipt, ErrorEnvelope
from cera.errors import (
    ConfigurationError,
    ContractValidationError,
    ErrorCode,
    RetryMode,
    StateConflictError,
    TransactionError,
)
from cera.ids import IdKind, TypedId, deterministic_id, require_kind
from cera.kernel import TurnRoute
from cera.reasoner import FakeReasonerFixture, SceneReasonerPort, SceneReasonerRequest
from cera.serialization import canonical_json, domain_sha256, re_is_sha256, text_sha256
from cera.storage import PostPublicationWorkKind, SQLiteAuthorityStore

from .models import (
    ComposerRequestPlan,
    IngressPublicationEvidence,
    LiveShapedTurnResult,
)
from .operations import (
    PostPublicationOperationsService,
    PostPublicationStatusReport,
)
from .pipeline import LiveShapedTurnPipeline
from .post_publication import PostPublicationCoordinator


@dataclass(frozen=True, slots=True)
class OrdinaryApplicationRequest:
    SCHEMA_VERSION: ClassVar[str] = "cera.ordinary_application_request.v1"

    schema_version: str
    reasoner_request: SceneReasonerRequest
    composer_plan: ComposerRequestPlan
    renderer_profile: str | None
    consolidation_requested: bool

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("ordinary application request schema is invalid")
        _validate_application_shape(
            self.reasoner_request,
            self.composer_plan,
            self.renderer_profile,
            self.consolidation_requested,
        )

    @property
    def request_sha256(self) -> str:
        return domain_sha256("cera.ordinary_application_request.v1", self)


@dataclass(frozen=True, slots=True)
class OrdinaryApplicationRequestV2:
    """Active ordinary application request with durable ingress evidence."""

    SCHEMA_VERSION: ClassVar[str] = "cera.ordinary_application_request.v2"

    schema_version: str
    reasoner_request: SceneReasonerRequest
    composer_plan: ComposerRequestPlan
    renderer_profile: str | None
    consolidation_requested: bool
    ingress_evidence: IngressPublicationEvidence

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError(
                "ordinary application v2 request schema is invalid"
            )
        _validate_application_shape(
            self.reasoner_request,
            self.composer_plan,
            self.renderer_profile,
            self.consolidation_requested,
        )
        prepared = self.reasoner_request.prepared_turn
        if (
            self.ingress_evidence.request_id != prepared.request.request_id
            or self.ingress_evidence.snapshot_token
            != prepared.evidence_snapshot.snapshot_token
            or self.ingress_evidence.source_sha256
            != prepared.request.source_sha256
            or self.ingress_evidence.reasoner_request_sha256
            != self.reasoner_request.request_sha256
            or self.ingress_evidence.seed_receipt.seed_receipt_id
            != self.reasoner_request.seed_dossier.seed_receipt_id
            or self.ingress_evidence.seed_receipt.exact_evidence_ids
            != tuple(
                value.evidence_id
                for value in self.reasoner_request.seed_dossier.exact_seed_evidence
            )
        ):
            raise ContractValidationError(
                "ordinary application v2 ingress evidence does not bind the prepared turn"
            )

    @property
    def request_sha256(self) -> str:
        return domain_sha256(self.SCHEMA_VERSION, self)


def _validate_application_shape(
    reasoner_request: SceneReasonerRequest,
    composer_plan: ComposerRequestPlan,
    renderer_profile: str | None,
    consolidation_requested: bool,
) -> None:
    prepared = reasoner_request.prepared_turn
    if prepared.route is not TurnRoute.ORDINARY:
        raise ContractValidationError(
            "ordinary application facade accepts only the ordinary route"
        )
    if (
        composer_plan.source_packet.mode is not CompositionMode.ORDINARY
        or composer_plan.adult_binding is not None
    ):
        raise ContractValidationError(
            "ordinary application facade rejects adult and blocked composition"
        )
    if renderer_profile is not None and not renderer_profile.strip():
        raise ContractValidationError("renderer profile must be non-empty")
    if type(consolidation_requested) is not bool:
        raise ContractValidationError("consolidation_requested must be boolean")


@dataclass(frozen=True, slots=True)
class OrdinaryApplicationReceipt:
    SCHEMA_VERSION: ClassVar[str] = "cera.ordinary_application_receipt.v1"

    schema_version: str
    receipt_id: TypedId
    application_request_sha256: str
    request_id: TypedId
    branch_id: TypedId
    generation_id: TypedId
    accepted_artifact_id: TypedId
    accepted_artifact_sha256: str
    commit_id: TypedId
    transaction_sha256: str
    operational_report_id: TypedId
    operational_report_sha256: str
    adapter_call_count: int
    exact_replay: bool
    downstream_failure_count: int
    story_state_committed: bool
    adult_route_activated: bool

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("ordinary application receipt schema is invalid")
        require_kind(self.receipt_id, IdKind.VALIDATION, "receipt_id")
        require_kind(self.request_id, IdKind.REQUEST, "request_id")
        require_kind(self.branch_id, IdKind.BRANCH, "branch_id")
        require_kind(self.generation_id, IdKind.GENERATION, "generation_id")
        require_kind(self.accepted_artifact_id, IdKind.ARTIFACT, "accepted_artifact_id")
        require_kind(self.commit_id, IdKind.COMMIT, "commit_id")
        require_kind(self.operational_report_id, IdKind.VALIDATION, "operational_report_id")
        for value in (
            self.application_request_sha256,
            self.accepted_artifact_sha256,
            self.transaction_sha256,
            self.operational_report_sha256,
        ):
            if not re_is_sha256(value):
                raise ContractValidationError("ordinary application receipt hash is invalid")
        if self.adapter_call_count < 0 or self.downstream_failure_count < 0:
            raise ContractValidationError("ordinary application counters cannot be negative")
        if not self.story_state_committed:
            raise ContractValidationError("successful application receipt requires a commit")
        if self.adult_route_activated:
            raise ContractValidationError("ordinary application cannot activate adult routing")
        if self.exact_replay and self.adapter_call_count != 0:
            raise ContractValidationError("exact application replay cannot call adapters")


@dataclass(frozen=True, slots=True)
class OrdinaryApplicationResult:
    accepted_artifact: AcceptedStoryArtifact
    commit_receipt: CommitReceipt
    operational_report: PostPublicationStatusReport
    receipt: OrdinaryApplicationReceipt
    live_shaped_result: LiveShapedTurnResult | None = None

    def __post_init__(self) -> None:
        if (
            self.accepted_artifact.artifact_id != self.commit_receipt.new_artifact_id
            or self.accepted_artifact.artifact_id != self.operational_report.artifact_id
            or self.accepted_artifact.artifact_id != self.receipt.accepted_artifact_id
        ):
            raise ContractValidationError("ordinary application artifact binding mismatch")
        artifact_sha256 = domain_sha256(
            "cera.accepted_story_artifact.v1", self.accepted_artifact
        )
        if (
            artifact_sha256 != self.commit_receipt.new_artifact_sha256
            or artifact_sha256 != self.receipt.accepted_artifact_sha256
        ):
            raise ContractValidationError("ordinary application artifact hash mismatch")
        if (
            self.commit_receipt.commit_id != self.receipt.commit_id
            or self.commit_receipt.transaction_sha256 != self.receipt.transaction_sha256
            or self.operational_report.report_id != self.receipt.operational_report_id
            or self.operational_report.report_sha256
            != self.receipt.operational_report_sha256
        ):
            raise ContractValidationError("ordinary application receipt binding mismatch")
        if self.operational_report.failed_count != self.receipt.downstream_failure_count:
            raise ContractValidationError("ordinary application failure count mismatch")
        if self.receipt.exact_replay != (self.live_shaped_result is None):
            raise ContractValidationError(
                "ordinary application replay state does not match live-shaped evidence"
            )
        if (
            self.live_shaped_result is not None
            and self.live_shaped_result.accepted_artifact != self.accepted_artifact
        ):
            raise ContractValidationError(
                "ordinary application live-shaped artifact binding mismatch"
            )


class OrdinaryApplicationFailure(Exception):
    def __init__(self, envelope: ErrorEnvelope, cause: Exception | None = None) -> None:
        self.envelope = envelope
        self.failure_evidence_bundle = getattr(cause, "failure_evidence_bundle", None)
        self.retained_evidence_handles = tuple(
            getattr(cause, "retained_evidence_handles", ())
        )
        self.privacy_safe_receipt_payloads = tuple(
            getattr(cause, "privacy_safe_receipt_payloads", ())
        )
        self.rejected_candidate_review_handle = getattr(
            cause, "rejected_candidate_review_handle", None
        )
        self.provider_call_receipt = getattr(cause, "provider_call_receipt", None)
        self.mcp_bridge_receipt = getattr(cause, "mcp_bridge_receipt", None)
        self.lookup_receipts = tuple(getattr(cause, "lookup_receipts", ()))
        super().__init__(envelope.message)


class ProviderFreeOrdinaryApplication:
    """One ordinary-turn entry point, explicitly prohibited in production."""

    def __init__(
        self,
        *,
        store: SQLiteAuthorityStore,
        pipeline: LiveShapedTurnPipeline,
        reasoner_port: SceneReasonerPort,
        composer_port: SceneComposerPort,
        renderer: PresentationRendererPort | None = None,
        consolidator: DerivedConsolidatorPort | None = None,
        environment: Environment = Environment.TEST,
    ) -> None:
        if environment is Environment.PRODUCTION:
            raise ConfigurationError(
                "ProviderFreeOrdinaryApplication is prohibited in production"
            )
        self.store = store
        self.pipeline = pipeline
        self.reasoner_port = reasoner_port
        self.composer_port = composer_port
        self.renderer = renderer
        self.consolidator = consolidator
        self.post_publication = PostPublicationCoordinator(store)
        self.operations = PostPublicationOperationsService(store)

    def execute(
        self,
        request: OrdinaryApplicationRequest | OrdinaryApplicationRequestV2,
        *,
        reasoner_fixture: FakeReasonerFixture | None = None,
        composer_fixture: FakeComposerFixture | None = None,
    ) -> OrdinaryApplicationResult:
        if not isinstance(
            request, (OrdinaryApplicationRequest, OrdinaryApplicationRequestV2)
        ):
            raise TypeError("application facade requires OrdinaryApplicationRequest")
        prepared = request.reasoner_request.prepared_turn
        turn = prepared.request
        try:
            existing = self.store.find_committed_turn(
                request_id=turn.request_id,
                branch_id=turn.branch_id,
                idempotency_key=turn.idempotency_key,
                source_sha256=turn.source_sha256,
            )
        except TransactionError as exc:
            raise self._failure(
                request,
                ErrorCode.STATE_CONFLICT,
                "The idempotency key belongs to another committed turn identity.",
                "application_replay",
            ) from exc
        if existing is not None:
            self._assert_replay_work_shape(request, existing.receipt.new_artifact_id)
            self._assert_replay_ingress_evidence(request)
            artifact = self.store.get_artifact(existing.receipt.new_artifact_id)
            report = self.operations.inspect_artifact(artifact.artifact_id)
            return self._result(
                request,
                artifact,
                existing.receipt,
                report,
                adapter_call_count=0,
                exact_replay=True,
                live_shaped_result=None,
            )

        if request.renderer_profile is not None and self.renderer is None:
            raise self._failure(
                request,
                ErrorCode.RENDER_FAILED,
                "The requested presentation renderer is unavailable.",
                "application_preflight",
            )
        if request.consolidation_requested and self.consolidator is None:
            raise self._failure(
                request,
                ErrorCode.CONSOLIDATOR_UNAVAILABLE,
                "The requested derived consolidator is unavailable.",
                "application_preflight",
            )

        try:
            shaped = self.pipeline.execute(
                request.reasoner_request,
                self.reasoner_port,
                request.composer_plan,
                self.composer_port,
                reasoner_fixture=reasoner_fixture,
                composer_fixture=composer_fixture,
            )
        except Exception as exc:
            envelope = getattr(exc, "envelope", None)
            if isinstance(envelope, ErrorEnvelope):
                raise OrdinaryApplicationFailure(envelope, exc) from exc
            raise self._failure(
                request,
                ErrorCode.VERIFIER_FAILED,
                "The provider-free ordinary pipeline failed before publication.",
                "application_pipeline",
            ) from exc
        try:
            published = self.post_publication.execute(
                shaped,
                renderer=(self.renderer if request.renderer_profile is not None else None),
                renderer_profile=request.renderer_profile,
                consolidator=(
                    self.consolidator if request.consolidation_requested else None
                ),
                ingress_evidence=(
                    request.ingress_evidence
                    if isinstance(request, OrdinaryApplicationRequestV2)
                    else None
                ),
            )
        except (StateConflictError, TransactionError) as exc:
            code = (
                ErrorCode.STATE_CONFLICT
                if isinstance(exc, StateConflictError)
                else ErrorCode.TRANSACTION_ROLLED_BACK
            )
            raise self._failure(
                request,
                code,
                "The ordinary story transaction did not commit.",
                "application_publication",
            ) from exc
        artifact = self.store.get_artifact(
            published.publication.receipt.new_artifact_id
        )
        report = self.operations.inspect_artifact(artifact.artifact_id)
        return self._result(
            request,
            artifact,
            published.publication.receipt,
            report,
            adapter_call_count=shaped.receipt.external_provider_calls,
            exact_replay=False,
            live_shaped_result=shaped,
        )

    def _assert_replay_work_shape(
        self,
        request: OrdinaryApplicationRequest | OrdinaryApplicationRequestV2,
        artifact_id: TypedId,
    ) -> None:
        work = self.store.post_publication_work_for_artifact(artifact_id)
        kinds = {value.kind for value in work}
        expected = set()
        if request.renderer_profile is not None:
            expected.add(PostPublicationWorkKind.RENDER)
        if request.consolidation_requested:
            expected.update(
                {
                    PostPublicationWorkKind.CONSOLIDATE,
                    PostPublicationWorkKind.DERIVED_VIEWS,
                }
            )
        if kinds != expected:
            raise self._failure(
                request,
                ErrorCode.STATE_CONFLICT,
                "The committed turn has a different post-publication work plan.",
                "application_replay",
            )
        if request.renderer_profile is not None:
            render = next(value for value in work if value.kind is PostPublicationWorkKind.RENDER)
            if json.loads(render.request_json)["renderer_profile"] != request.renderer_profile:
                raise self._failure(
                    request,
                    ErrorCode.STATE_CONFLICT,
                    "The committed turn has a different renderer profile.",
                    "application_replay",
                )

    def _assert_replay_ingress_evidence(
        self,
        request: OrdinaryApplicationRequest | OrdinaryApplicationRequestV2,
    ) -> None:
        if not isinstance(request, OrdinaryApplicationRequestV2):
            return
        stored = self.store.get_turn_receipt_record(
            request.ingress_evidence.receipt_id
        )
        if (
            stored.schema_version != request.ingress_evidence.schema_version
            or stored.payload_sha256
            != text_sha256(canonical_json(request.ingress_evidence))
        ):
            raise self._failure(
                request,
                ErrorCode.STATE_CONFLICT,
                "The committed turn has different ingress publication evidence.",
                "application_replay",
            )

    @staticmethod
    def _result(
        request: OrdinaryApplicationRequest | OrdinaryApplicationRequestV2,
        artifact: AcceptedStoryArtifact,
        commit: CommitReceipt,
        report: PostPublicationStatusReport,
        *,
        adapter_call_count: int,
        exact_replay: bool,
        live_shaped_result: LiveShapedTurnResult | None,
    ) -> OrdinaryApplicationResult:
        key = (
            f"{request.request_sha256}|{commit.commit_id}|{report.report_sha256}|"
            f"{adapter_call_count}|{exact_replay}"
        )
        receipt = OrdinaryApplicationReceipt(
            schema_version=OrdinaryApplicationReceipt.SCHEMA_VERSION,
            receipt_id=deterministic_id(
                IdKind.VALIDATION,
                "cera.ordinary_application_receipt.v1",
                key,
            ),
            application_request_sha256=request.request_sha256,
            request_id=request.reasoner_request.prepared_turn.request.request_id,
            branch_id=artifact.branch_id,
            generation_id=artifact.generation_id,
            accepted_artifact_id=artifact.artifact_id,
            accepted_artifact_sha256=domain_sha256(
                "cera.accepted_story_artifact.v1", artifact
            ),
            commit_id=commit.commit_id,
            transaction_sha256=commit.transaction_sha256,
            operational_report_id=report.report_id,
            operational_report_sha256=report.report_sha256,
            adapter_call_count=adapter_call_count,
            exact_replay=exact_replay,
            downstream_failure_count=report.failed_count,
            story_state_committed=True,
            adult_route_activated=False,
        )
        return OrdinaryApplicationResult(
            artifact,
            commit,
            report,
            receipt,
            live_shaped_result,
        )

    @staticmethod
    def _failure(
        request: OrdinaryApplicationRequest | OrdinaryApplicationRequestV2,
        code: ErrorCode,
        message: str,
        stage: str,
    ) -> OrdinaryApplicationFailure:
        turn = request.reasoner_request.prepared_turn.request
        return OrdinaryApplicationFailure(
            ErrorEnvelope(
                schema_version=ErrorEnvelope.SCHEMA_VERSION,
                error_code=code,
                message=message,
                trace_id=deterministic_id(
                    IdKind.TRACE,
                    "cera.ordinary_application.failure.v1",
                    f"{turn.request_id}|{stage}|{code.value}",
                ),
                request_id=turn.request_id,
                branch_id=turn.branch_id,
                generation_id=turn.generation_id,
                stage=stage,
                story_state_committed=False,
                retry_mode=RetryMode.MANUAL_AFTER_REVIEW,
                details=(),
            )
        )


class LocalOrdinaryApplication(ProviderFreeOrdinaryApplication):
    """Provider-neutral non-production facade for qualified local adapters.

    The historical class name remains available for provider-free fixtures.
    This name accurately identifies the same fail-closed boundary when its
    injected ports make separately authorized live qualification calls.
    """
