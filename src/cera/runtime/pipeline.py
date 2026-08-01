"""Provider-free live-shaped Reasoner-to-Composer orchestration."""

from __future__ import annotations

from dataclasses import replace
import re
from typing import Callable

from cera.adult_craft.repair import (
    BeatScopedRepairCoordinator,
    BeatScopedRepairPort,
)
from cera.adult_craft.selector import (
    AdultCraftSelectionResult,
    AdultCraftSelector,
    bind_adult_craft,
)
from cera.adult_craft.specificity import SpecificityValidator
from cera.adult_craft.semantic import (
    SemanticSpecificityCoordinator,
    SemanticSpecificityPort,
)
from cera.adult_craft.models import SemanticVerificationStage
from cera.composer import (
    ArtifactPublicationMode,
    ComposerContextAssembler,
    ComposerContextAssemblyError,
    ComposerCoordinator,
    ComposerExecutionFailure,
    FakeComposerFixture,
    FakeSceneComposerPort,
    ComposerSubmission,
    CompositionMode,
    RealizationKind,
    SceneComposerPort,
    SceneComposerRequest,
)
from cera.contracts import AcceptedStoryArtifact, ErrorEnvelope
from cera.errors import ContractValidationError, ErrorCode, RetryMode
from cera.ids import IdKind, deterministic_id
from cera.reasoner import (
    FakeReasonerFixture,
    ReasonerCoordinator,
    ReasonerExecutionFailure,
    ReasonerOutcomeStatus,
    ReasonerExecutionResult,
    ProtectedUserClaimKind,
    SceneReasonerPort,
    SceneReasonerRequest,
)
from cera.realization import (
    SceneRealizationVerificationCoordinator,
    SceneRealizationVerificationFailure,
    SceneRealizationVerifierPort,
)
from cera.serialization import domain_sha256, text_sha256

from .audit import TurnStageAuditJournal
from .failure import FailureEvidenceHandle, PrivacySafeReceiptPayload
from .models import (
    ComposerRequestPlan,
    FinalStoryAcceptanceReceipt,
    LiveShapedTurnReceipt,
    LiveShapedTurnResult,
    ProvisionalTurnCandidate,
)
from .models import AdultCraftTurnEvidence


class LiveShapedTurnFailure(Exception):
    def __init__(self, envelope: ErrorEnvelope) -> None:
        self.envelope = envelope
        self.failure_evidence_bundle = None
        self.retained_evidence_handles = ()
        self.privacy_safe_receipt_payloads = ()
        self.rejected_candidate_review_handle = None
        self.creator_review_assessment = None
        self.realization_verification_receipt = None
        super().__init__(envelope.message)


class LiveShapedTurnPipeline:
    """Run typed adapters through Python validation without committing story state."""

    def __init__(
        self,
        reasoner_coordinator: ReasonerCoordinator,
        context_assembler: ComposerContextAssembler,
        composer_coordinator: ComposerCoordinator,
        adult_craft_selector: AdultCraftSelector | None = None,
        specificity_validator: SpecificityValidator | None = None,
        semantic_specificity_coordinator: SemanticSpecificityCoordinator | None = None,
        beat_repair_coordinator: BeatScopedRepairCoordinator | None = None,
        realization_verification_coordinator: (
            SceneRealizationVerificationCoordinator | None
        ) = None,
        realization_verifier_port: SceneRealizationVerifierPort | None = None,
        stage_audit_journal: TurnStageAuditJournal | None = None,
    ) -> None:
        self.reasoner_coordinator = reasoner_coordinator
        self.context_assembler = context_assembler
        self.composer_coordinator = composer_coordinator
        self.adult_craft_selector = adult_craft_selector
        self.specificity_validator = specificity_validator or SpecificityValidator()
        self.semantic_specificity_coordinator = (
            semantic_specificity_coordinator or SemanticSpecificityCoordinator()
        )
        self.beat_repair_coordinator = beat_repair_coordinator or BeatScopedRepairCoordinator(
            self.specificity_validator
        )
        self.realization_verification_coordinator = (
            realization_verification_coordinator
            or SceneRealizationVerificationCoordinator()
        )
        self.realization_verifier_port = realization_verifier_port
        self.stage_audit_journal = stage_audit_journal

    def execute(
        self,
        reasoner_request: SceneReasonerRequest,
        reasoner_port: SceneReasonerPort | None,
        composer_plan: ComposerRequestPlan,
        composer_port: SceneComposerPort,
        *,
        reasoner_fixture: FakeReasonerFixture | None = None,
        composer_fixture: FakeComposerFixture | None = None,
        semantic_specificity_port: SemanticSpecificityPort | None = None,
        beat_repair_port: BeatScopedRepairPort | None = None,
        realization_verifier_port: SceneRealizationVerifierPort | None = None,
        precomputed_reasoner_result: ReasonerExecutionResult | None = None,
        provisional_candidate_callback: (
            Callable[[ProvisionalTurnCandidate], None] | None
        ) = None,
    ) -> LiveShapedTurnResult:
        if precomputed_reasoner_result is None:
            if reasoner_port is None:
                raise self._failure(
                    reasoner_request,
                    ErrorCode.REASONER_UNAVAILABLE,
                    "Reasoner port is required when no validated result is supplied",
                )
            self._audit_started(
                reasoner_request,
                "scene_reasoner",
                reasoner_request.request_sha256,
                0,
            )
            try:
                reasoned = self.reasoner_coordinator.execute(
                    reasoner_request,
                    reasoner_port,
                    fixture=reasoner_fixture,
                )
            except ReasonerExecutionFailure as exc:
                handles = _reasoner_failure_handles(exc)
                payloads = _reasoner_failure_payloads(exc)
                bundle = self._audit_failed(
                    reasoner_request,
                    "scene_reasoner",
                    exc.envelope.error_code,
                    "scene reasoner failed before typed acceptance",
                    input_sha256=reasoner_request.request_sha256,
                    output_sha256=(
                        exc.provider_call_receipt.output_sha256
                        if exc.provider_call_receipt is not None
                        else None
                    ),
                    handles=handles,
                    payloads=payloads,
                    safe_diagnostic_codes=_reasoner_failure_diagnostic_codes(exc),
                    calls=exc.external_provider_calls_observed,
                )
                exc.retained_evidence_handles = handles
                exc.privacy_safe_receipt_payloads = payloads
                exc.failure_evidence_bundle = bundle
                raise
            self._audit_completed(
                reasoner_request,
                "scene_reasoner",
                reasoner_request.request_sha256,
                reasoned.outcome.outcome_sha256,
                reasoned.receipt.external_provider_calls,
            )
        else:
            reasoned = precomputed_reasoner_result
            if (
                reasoned.receipt.reasoner_request_sha256
                != reasoner_request.request_sha256
                or reasoned.receipt.snapshot_token
                != reasoner_request.prepared_turn.evidence_snapshot.snapshot_token
            ):
                raise self._failure(
                    reasoner_request,
                    ErrorCode.REASONER_CONTRACT_INVALID,
                    "precomputed Reasoner result is not bound to this request/snapshot",
                )
        if reasoned.outcome.status is not ReasonerOutcomeStatus.DECISION_READY:
            code = reasoned.outcome.blocker_code or ErrorCode.EVIDENCE_INSUFFICIENT
            stage = "reasoner_decision_gate"
            reasoner_handles, reasoner_payloads = _reasoner_success_evidence(reasoned)
            self._audit_started(
                reasoner_request,
                stage,
                reasoned.outcome.outcome_sha256,
                reasoned.receipt.external_provider_calls,
            )
            failure = self._failure(
                reasoner_request,
                code,
                "Reasoner did not authorize composition",
                stage=stage,
            )
            failure.retained_evidence_handles = reasoner_handles
            failure.privacy_safe_receipt_payloads = reasoner_payloads
            failure.failure_evidence_bundle = self._audit_failed(
                reasoner_request,
                stage,
                code,
                "Reasoner returned a non-decision outcome",
                input_sha256=reasoned.outcome.outcome_sha256,
                output_sha256=None,
                handles=reasoner_handles,
                payloads=reasoner_payloads,
                safe_diagnostic_codes=(
                    f"REASONER_STATUS_{reasoned.outcome.status.value.upper()}",
                ),
                calls=reasoned.receipt.external_provider_calls,
            )
            raise failure
        decision = reasoned.outcome.decision
        assert decision is not None
        adult_selection: AdultCraftSelectionResult | None = None
        adult_binding = composer_plan.adult_binding
        craft_blocks = composer_plan.craft_blocks
        card_sections = ()
        specificity_contract = None
        if reasoned.outcome.adult_craft_need is not None:
            reasoner_handles, reasoner_payloads = _reasoner_success_evidence(reasoned)
            self._audit_started(
                reasoner_request,
                "adult_craft_selection",
                reasoned.outcome.outcome_sha256,
                reasoned.receipt.external_provider_calls,
            )
            if self.adult_craft_selector is None or adult_binding is None:
                failure = self._failure(
                    reasoner_request,
                    ErrorCode.ADULT_CONTEXT_INVALID,
                    "adult craft need requires the repository-local catalog selector and adult binding",
                    stage="adult_craft_selection",
                )
                failure.retained_evidence_handles = reasoner_handles
                failure.privacy_safe_receipt_payloads = reasoner_payloads
                failure.failure_evidence_bundle = self._audit_failed(
                    reasoner_request,
                    "adult_craft_selection",
                    ErrorCode.ADULT_CONTEXT_INVALID,
                    "adult craft selection prerequisites were unavailable",
                    input_sha256=reasoned.outcome.outcome_sha256,
                    output_sha256=None,
                    handles=reasoner_handles,
                    payloads=reasoner_payloads,
                    calls=reasoned.receipt.external_provider_calls,
                )
                raise failure
            if craft_blocks:
                failure = self._failure(
                    reasoner_request,
                    ErrorCode.ADULT_CONTEXT_INVALID,
                    "catalog-selected adult craft cannot be mixed with caller-supplied craft blocks",
                    stage="adult_craft_selection",
                )
                failure.retained_evidence_handles = reasoner_handles
                failure.privacy_safe_receipt_payloads = reasoner_payloads
                failure.failure_evidence_bundle = self._audit_failed(
                    reasoner_request,
                    "adult_craft_selection",
                    ErrorCode.ADULT_CONTEXT_INVALID,
                    "adult craft selection received conflicting inputs",
                    input_sha256=reasoned.outcome.outcome_sha256,
                    output_sha256=None,
                    handles=reasoner_handles,
                    payloads=reasoner_payloads,
                    calls=reasoned.receipt.external_provider_calls,
                )
                raise failure
            try:
                adult_selection = self.adult_craft_selector.select(
                    reasoned.outcome.adult_craft_need,
                    decision,
                    request_id=reasoner_request.prepared_turn.request.request_id,
                )
                adult_binding = bind_adult_craft(adult_binding, adult_selection)
                craft_blocks = adult_selection.craft_blocks
                card_sections = reasoned.outcome.adult_craft_need.character_card_sections
                specificity_contract = adult_selection.specificity_contract
                self._audit_completed(
                    reasoner_request,
                    "adult_craft_selection",
                    reasoned.outcome.outcome_sha256,
                    adult_selection.receipt.selection_sha256,
                    reasoned.receipt.external_provider_calls,
                )
            except Exception as exc:
                failure = self._failure(
                    reasoner_request,
                    ErrorCode.ADULT_CRAFT_SELECTION_INCOMPLETE,
                    f"adult craft selection failed: {exc}",
                    stage="adult_craft_selection",
                )
                failure.retained_evidence_handles = reasoner_handles
                failure.privacy_safe_receipt_payloads = reasoner_payloads
                failure.failure_evidence_bundle = self._audit_failed(
                    reasoner_request,
                    "adult_craft_selection",
                    ErrorCode.ADULT_CRAFT_SELECTION_INCOMPLETE,
                    "adult craft selection failed",
                    input_sha256=reasoned.outcome.outcome_sha256,
                    output_sha256=None,
                    handles=reasoner_handles,
                    payloads=reasoner_payloads,
                    calls=reasoned.receipt.external_provider_calls,
                )
                raise failure from exc
        try:
            self._audit_started(
                reasoner_request,
                "composer_context_assembly",
                reasoned.outcome.outcome_sha256,
                reasoned.receipt.external_provider_calls,
            )
            if composer_plan.publication_mode is ArtifactPublicationMode.REGENERATE:
                target = reasoner_request.prepared_turn.request.parent_artifact_id
                if target is None or composer_plan.replaces_artifact_id != target:
                    raise ComposerContextAssemblyError(
                        ErrorCode.COMPOSER_CONTRACT_INVALID,
                        "regeneration target must equal the prepared branch head",
                    )
                actual_parent = (
                    self.context_assembler.evidence_service.store.get_artifact_parent_id(
                        target
                    )
                )
                if actual_parent != composer_plan.publication_parent_artifact_id:
                    raise ComposerContextAssemblyError(
                        ErrorCode.STATE_CONFLICT,
                        "regeneration parent does not match immutable target lineage",
                    )
            bound_source_packet = _bind_protected_user_source_authority(
                composer_plan.source_packet,
                reasoned.outcome,
            )
            base_composer_request = SceneComposerRequest(
                schema_version=SceneComposerRequest.SCHEMA_VERSION,
                prepared_turn=reasoner_request.prepared_turn,
                reasoner_outcome=reasoned.outcome,
                reasoner_receipt=reasoned.receipt,
                source_packet=bound_source_packet,
                selected_npc_ids=decision.responding_npc_ids,
                scene_scope=composer_plan.scene_scope,
                response_profile_version=composer_plan.response_profile_version,
                continuity_references=composer_plan.continuity_references,
                creator_event_coverage_required=(
                    composer_plan.creator_event_coverage_required
                ),
                hard_boundaries=composer_plan.hard_boundaries,
                established_scene_context=(
                    composer_plan.established_scene_context
                ),
                adult_binding=adult_binding,
                realization_context=None,
                publication_mode=composer_plan.publication_mode,
                publication_parent_artifact_id=(
                    composer_plan.publication_parent_artifact_id
                ),
                replaces_artifact_id=composer_plan.replaces_artifact_id,
                specificity_contract=specificity_contract,
                scene_depth_mode=composer_plan.scene_depth_mode,
                creator_revision=composer_plan.creator_revision,
            )
            context = self.context_assembler.assemble(
                base_composer_request,
                reasoned,
                craft_blocks=craft_blocks,
                realization_card_sections=card_sections,
            )
            self._audit_completed(
                reasoner_request,
                "composer_context_assembly",
                reasoned.outcome.outcome_sha256,
                context.request.request_sha256,
                reasoned.receipt.external_provider_calls,
            )
        except ComposerContextAssemblyError as exc:
            prior_handles, prior_payloads = _reasoner_success_evidence(reasoned)
            bundle = self._audit_failed(
                reasoner_request,
                "composer_context_assembly",
                exc.code,
                "composer context assembly failed",
                input_sha256=reasoned.outcome.outcome_sha256,
                output_sha256=None,
                handles=prior_handles,
                payloads=prior_payloads,
                calls=reasoned.receipt.external_provider_calls,
            )
            failure = self._failure(
                reasoner_request,
                exc.code,
                str(exc),
                stage="composer_context",
            )
            failure.retained_evidence_handles = prior_handles
            failure.privacy_safe_receipt_payloads = prior_payloads
            failure.failure_evidence_bundle = bundle
            raise failure from exc
        except Exception as exc:
            prior_handles, prior_payloads = _reasoner_success_evidence(reasoned)
            bundle = self._audit_failed(
                reasoner_request,
                "composer_context_assembly",
                ErrorCode.COMPOSER_CONTRACT_INVALID,
                "composer context assembly failed",
                input_sha256=reasoned.outcome.outcome_sha256,
                output_sha256=None,
                handles=prior_handles,
                payloads=prior_payloads,
                calls=reasoned.receipt.external_provider_calls,
            )
            failure = self._failure(
                reasoner_request,
                ErrorCode.COMPOSER_CONTRACT_INVALID,
                f"Composer request assembly failed: {exc}",
                stage="composer_context",
            )
            failure.retained_evidence_handles = prior_handles
            failure.privacy_safe_receipt_payloads = prior_payloads
            failure.failure_evidence_bundle = bundle
            raise failure from exc

        self._audit_started(
            reasoner_request,
            "scene_composer",
            context.request.request_sha256,
            reasoned.receipt.external_provider_calls,
        )
        try:
            composed = self.composer_coordinator.execute(
                context.request,
                composer_port,
                fixture=composer_fixture,
            )
        except ComposerExecutionFailure as exc:
            handles = _composer_failure_handles(exc, context, reasoned)
            payloads = _composer_failure_payloads(exc, context, reasoned)
            bundle = self._audit_failed(
                reasoner_request,
                "scene_composer",
                exc.envelope.error_code,
                "scene composer failed before typed acceptance",
                input_sha256=context.request.request_sha256,
                output_sha256=(
                    exc.provider_call_receipt.output_sha256
                    if exc.provider_call_receipt is not None
                    else None
                ),
                handles=handles,
                payloads=payloads,
                safe_diagnostic_codes=exc.safe_diagnostics,
                calls=(
                    reasoned.receipt.external_provider_calls
                    + exc.external_provider_calls_observed
                ),
            )
            exc.retained_evidence_handles = handles
            exc.privacy_safe_receipt_payloads = payloads
            exc.failure_evidence_bundle = bundle
            raise
        self._audit_completed(
            reasoner_request,
            "scene_composer",
            context.request.request_sha256,
            composed.candidate.candidate_sha256,
            (
                reasoned.receipt.external_provider_calls
                + composed.composer_receipt.external_provider_calls
            ),
        )
        adult_evidence = None
        if adult_selection is not None:
            if semantic_specificity_port is None:
                raise self._post_composer_terminal_failure(
                    reasoner_request,
                    reasoned,
                    context,
                    composed,
                    ErrorCode.ADULT_SEMANTIC_SPECIFICITY_FAILED,
                    "adult route requires one provider-neutral semantic specificity verification",
                    "adult_semantic_specificity",
                    adult_selection=adult_selection,
                )
            try:
                initial_specificity = self.specificity_validator.validate(
                    adult_selection.specificity_contract,
                    composed.candidate,
                    composed.manifest,
                )
                semantic_request = self.semantic_specificity_coordinator.build_request(
                    context.request,
                    reasoned.outcome.adult_craft_need,
                    adult_selection.specificity_contract,
                    composed.candidate,
                    composed.manifest,
                    stage=SemanticVerificationStage.INITIAL,
                )
                initial_semantic = self.semantic_specificity_coordinator.execute(
                    semantic_request,
                    semantic_specificity_port,
                )
            except Exception as exc:
                raise self._post_composer_terminal_failure(
                    reasoner_request,
                    reasoned,
                    context,
                    composed,
                    ErrorCode.ADULT_SEMANTIC_SPECIFICITY_FAILED,
                    "initial adult specificity verification failed",
                    "adult_semantic_specificity",
                    adult_selection=adult_selection,
                ) from exc
            initial_adult_receipts = (
                (
                    "adult_specificity_validation_receipt",
                    initial_specificity.validation_receipt_id,
                    initial_specificity.receipt_sha256,
                    initial_specificity,
                ),
                (
                    "adult_semantic_specificity_receipt",
                    initial_semantic.receipt.receipt_id,
                    initial_semantic.receipt.receipt_sha256,
                    initial_semantic.receipt,
                ),
            )
            final_specificity = initial_specificity
            final_semantic = initial_semantic
            repair_receipt = None
            failed_beats = tuple(
                dict.fromkeys(
                    (
                        *initial_specificity.repairable_beat_ids,
                        *initial_semantic.result.repairable_beat_ids,
                    )
                )
            )
            if failed_beats:
                if beat_repair_port is None or len(failed_beats) != 1:
                    raise self._post_composer_terminal_failure(
                        reasoner_request,
                        reasoned,
                        context,
                        composed,
                        ErrorCode.ADULT_SPECIFICITY_FAILED,
                        "adult lexical/semantic specificity failed and one bounded beat repair was unavailable",
                        "adult_specificity",
                        adult_selection=adult_selection,
                        additional_receipts=initial_adult_receipts,
                    )
                original_submission = ComposerSubmission(
                    composed.candidate,
                    composed.manifest,
                    composed.advisory_realization_metadata,
                )
                try:
                    repair_request = self.beat_repair_coordinator.build_request(
                        context.request,
                        original_submission,
                        adult_selection.specificity_contract,
                        initial_specificity,
                        initial_semantic.result,
                        initial_semantic.receipt,
                        failed_beats[0],
                    )
                    repaired = self.beat_repair_coordinator.execute(
                        repair_request,
                        context.request,
                        original_submission,
                        adult_selection.specificity_contract,
                        initial_semantic.receipt,
                        beat_repair_port,
                    )
                except Exception as exc:
                    raise self._post_composer_terminal_failure(
                        reasoner_request,
                        reasoned,
                        context,
                        composed,
                        ErrorCode.ADULT_BEAT_REPAIR_FAILED,
                        "bounded beat repair failed",
                        "adult_beat_repair",
                        adult_selection=adult_selection,
                        additional_receipts=initial_adult_receipts,
                    ) from exc
                if repaired.receipt.status != "accepted_for_full_revalidation":
                    raise self._post_composer_terminal_failure(
                        reasoner_request,
                        reasoned,
                        context,
                        composed,
                        ErrorCode.ADULT_BEAT_REPAIR_FAILED,
                        "bounded beat repair did not satisfy specificity",
                        "adult_beat_repair",
                        adult_selection=adult_selection,
                        additional_receipts=(
                            *initial_adult_receipts,
                            (
                                "adult_beat_repair_receipt",
                                repaired.receipt.repair_receipt_id,
                                repaired.receipt.receipt_sha256,
                                repaired.receipt,
                            ),
                        ),
                    )
                try:
                    post_semantic_request = self.semantic_specificity_coordinator.build_request(
                        context.request,
                        reasoned.outcome.adult_craft_need,
                        adult_selection.specificity_contract,
                        repaired.submission.candidate,
                        repaired.submission.manifest,
                        stage=SemanticVerificationStage.POST_REPAIR,
                        prior_semantic_receipt_sha256=initial_semantic.receipt.receipt_sha256,
                    )
                    final_semantic = self.semantic_specificity_coordinator.execute(
                        post_semantic_request,
                        semantic_specificity_port,
                    )
                except Exception as exc:
                    raise self._post_composer_terminal_failure(
                        reasoner_request,
                        reasoned,
                        context,
                        composed,
                        ErrorCode.ADULT_SEMANTIC_SPECIFICITY_FAILED,
                        "post-repair adult semantic verification failed",
                        "adult_semantic_specificity",
                        adult_selection=adult_selection,
                        additional_receipts=(
                            *initial_adult_receipts,
                            (
                                "adult_beat_repair_receipt",
                                repaired.receipt.repair_receipt_id,
                                repaired.receipt.receipt_sha256,
                                repaired.receipt,
                            ),
                        ),
                    ) from exc
                if final_semantic.result.status != "accepted":
                    raise self._post_composer_terminal_failure(
                        reasoner_request,
                        reasoned,
                        context,
                        composed,
                        ErrorCode.ADULT_SEMANTIC_SPECIFICITY_FAILED,
                        "semantic specificity failed after the only permitted repair",
                        "adult_semantic_specificity",
                        adult_selection=adult_selection,
                        additional_receipts=(
                            *initial_adult_receipts,
                            (
                                "adult_beat_repair_receipt",
                                repaired.receipt.repair_receipt_id,
                                repaired.receipt.receipt_sha256,
                                repaired.receipt,
                            ),
                            (
                                "adult_semantic_specificity_final_receipt",
                                final_semantic.receipt.receipt_id,
                                final_semantic.receipt.receipt_sha256,
                                final_semantic.receipt,
                            ),
                        ),
                    )
                repair_fixture = FakeComposerFixture(
                    f"adult-beat-repair:{repaired.receipt.repair_receipt_id.value}",
                    repaired.submission,
                )
                composed = self.composer_coordinator.execute(
                    context.request,
                    FakeSceneComposerPort(repair_fixture),
                    fixture=repair_fixture,
                )
                final_specificity = repaired.specificity_receipt
                repair_receipt = repaired.receipt
            adult_evidence = AdultCraftTurnEvidence(
                adult_selection.receipt,
                initial_specificity,
                final_specificity,
                initial_semantic.result,
                initial_semantic.receipt,
                final_semantic.result,
                final_semantic.receipt,
                repair_receipt,
            )
        if provisional_candidate_callback is not None:
            if adult_evidence is not None:
                raise self._post_composer_terminal_failure(
                    reasoner_request,
                    reasoned,
                    context,
                    composed,
                    ErrorCode.CREATOR_REVIEW_FAILED,
                    "post-display creator review is prohibited for Adult routes",
                    "creator_review_provisional_display",
                    adult_selection=adult_selection,
                )
            try:
                provisional_candidate_callback(
                    ProvisionalTurnCandidate(reasoned, context, composed)
                )
            except Exception as exc:
                raise self._post_composer_terminal_failure(
                    reasoner_request,
                    reasoned,
                    context,
                    composed,
                    ErrorCode.CREATOR_REVIEW_FAILED,
                    "provisional candidate could not be recorded for creator review",
                    "creator_review_provisional_display",
                ) from exc
        active_verifier = realization_verifier_port or self.realization_verifier_port
        self._audit_started(
            reasoner_request,
            "scene_realization_verification",
            composed.candidate.candidate_sha256,
            (
                reasoned.receipt.external_provider_calls
                + composed.composer_receipt.external_provider_calls
            ),
        )
        if active_verifier is None:
            handles = _post_composer_failure_handles(
                reasoned,
                context,
                composed,
                adult_evidence,
            )
            payloads = _post_composer_failure_payloads(
                reasoned,
                context,
                composed,
                adult_evidence,
            )
            bundle = self._audit_failed(
                reasoner_request,
                "scene_realization_verification",
                ErrorCode.VERIFIER_FAILED,
                "scene realization verifier was unavailable",
                input_sha256=composed.candidate.candidate_sha256,
                output_sha256=None,
                handles=handles,
                payloads=payloads,
                calls=(
                    reasoned.receipt.external_provider_calls
                    + composed.composer_receipt.external_provider_calls
                ),
            )
            failure = self._failure(
                reasoner_request,
                ErrorCode.VERIFIER_FAILED,
                "independent scene-realization verifier is unavailable",
                stage="scene_realization_verification",
            )
            failure.retained_evidence_handles = handles
            failure.privacy_safe_receipt_payloads = payloads
            failure.failure_evidence_bundle = bundle
            raise failure
        try:
            verification_request = (
                self.realization_verification_coordinator.build_request(
                    context.request,
                    composed,
                )
            )
            realization_verification = (
                self.realization_verification_coordinator.execute(
                    verification_request,
                    active_verifier,
                )
            )
            self._audit_completed(
                reasoner_request,
                "scene_realization_verification",
                composed.candidate.candidate_sha256,
                realization_verification.receipt.receipt_sha256,
                (
                    reasoned.receipt.external_provider_calls
                    + composed.composer_receipt.external_provider_calls
                    + realization_verification.receipt.external_provider_calls
                ),
            )
        except SceneRealizationVerificationFailure as exc:
            handles = _post_composer_failure_handles(
                reasoned,
                context,
                composed,
                adult_evidence,
                realization_verification_receipt=exc.receipt,
                realization_verifier_provider_receipt=(
                    exc.provider_call_receipt
                ),
            )
            if exc.rejected_candidate_review_handle is not None:
                review_id, review_sha256 = exc.rejected_candidate_review_handle
                handles = (
                    *handles,
                    FailureEvidenceHandle(
                        "rejected_candidate_review_artifact",
                        review_id,
                        review_sha256,
                    ),
                )
            payloads = _post_composer_failure_payloads(
                reasoned,
                context,
                composed,
                adult_evidence,
                realization_verification_receipt=exc.receipt,
                realization_verifier_provider_receipt=(
                    exc.provider_call_receipt
                ),
            )
            bundle = self._audit_failed(
                reasoner_request,
                "scene_realization_verification",
                ErrorCode.VERIFIER_FAILED,
                "scene realization verification rejected or was inconclusive",
                input_sha256=composed.candidate.candidate_sha256,
                output_sha256=None,
                handles=handles,
                payloads=payloads,
                calls=(
                    reasoned.receipt.external_provider_calls
                    + composed.composer_receipt.external_provider_calls
                    + exc.external_provider_calls
                ),
            )
            failure = self._failure(
                reasoner_request,
                ErrorCode.VERIFIER_FAILED,
                str(exc),
                stage="scene_realization_verification",
                details=tuple(exc.safe_diagnostics),
            )
            failure.retained_evidence_handles = handles
            failure.privacy_safe_receipt_payloads = payloads
            failure.failure_evidence_bundle = bundle
            failure.rejected_candidate_review_handle = getattr(
                exc, "rejected_candidate_review_handle", None
            )
            failure.creator_review_assessment = getattr(
                exc, "creator_review_assessment", None
            )
            failure.realization_verification_receipt = exc.receipt
            raise failure from exc
        acceptance_key = "|".join(
            (
                context.request.request_sha256,
                composed.candidate.candidate_sha256,
                composed.manifest.manifest_sha256,
                str(composed.validation_receipt.validation_receipt_id),
                str(realization_verification.receipt.verification_id),
                str(
                    adult_evidence.final_specificity_receipt.validation_receipt_id
                    if adult_evidence is not None
                    else "-"
                ),
                str(
                    adult_evidence.final_semantic_receipt.receipt_id
                    if adult_evidence is not None
                    else "-"
                ),
            )
        )
        final_acceptance = FinalStoryAcceptanceReceipt(
            schema_version=FinalStoryAcceptanceReceipt.SCHEMA_VERSION,
            receipt_id=deterministic_id(
                IdKind.VALIDATION,
                "cera.final_story_acceptance_receipt.v1",
                acceptance_key,
            ),
            request_id=reasoner_request.prepared_turn.request.request_id,
            candidate_sha256=composed.candidate.candidate_sha256,
            manifest_sha256=composed.manifest.manifest_sha256,
            composer_validation_receipt_id=(
                composed.validation_receipt.validation_receipt_id
            ),
            realization_verification_id=(
                realization_verification.receipt.verification_id
            ),
            adult_specificity_receipt_id=(
                adult_evidence.final_specificity_receipt.validation_receipt_id
                if adult_evidence is not None
                else None
            ),
            adult_semantic_receipt_id=(
                adult_evidence.final_semantic_receipt.receipt_id
                if adult_evidence is not None
                else None
            ),
            status="accepted_after_all_validation",
            story_state_committed=False,
        )
        decision = reasoned.outcome.decision
        assert decision is not None
        artifact_key = f"{acceptance_key}|{final_acceptance.receipt_sha256}"
        accepted_artifact = AcceptedStoryArtifact(
            schema_version=AcceptedStoryArtifact.SCHEMA_VERSION,
            artifact_id=deterministic_id(
                IdKind.ARTIFACT,
                "cera.accepted_story_artifact.after_all_validation.v1",
                artifact_key,
            ),
            branch_id=reasoner_request.prepared_turn.request.branch_id,
            generation_id=reasoner_request.prepared_turn.request.generation_id,
            parent_artifact_id=(
                reasoner_request.prepared_turn.request.parent_artifact_id
                if context.request.publication_mode is ArtifactPublicationMode.APPEND
                else context.request.publication_parent_artifact_id
            ),
            source_id=reasoner_request.prepared_turn.source_record.source_id,
            decision_id=decision.decision_id,
            accepted_prose=composed.candidate.story_text,
            prose_sha256=text_sha256(composed.candidate.story_text),
            responding_npc_ids=decision.responding_npc_ids,
            realized_beat_ids=composed.manifest.realized_beat_ids,
            validation_receipt_id=final_acceptance.receipt_id,
            transaction_id=deterministic_id(
                IdKind.TRANSACTION,
                "cera.accepted_story_artifact.reserved_transaction.v2",
                artifact_key,
            ),
            status="accepted",
        )
        lookup_ids = tuple(
            dict.fromkeys(
                (
                    *reasoned.receipt.evidence_lookup_receipt_ids,
                    *context.receipt.lookup_receipt_ids,
                )
            )
        )
        receipt_key = (
            f"{reasoner_request.request_sha256}|{reasoned.receipt.provider_receipt_id}|"
            f"{context.receipt.assembly_receipt_id}|"
            f"{composed.composer_receipt.provider_receipt_id}|"
            f"{realization_verification.receipt.verification_id}|"
            f"{final_acceptance.receipt_id}|"
            f"{accepted_artifact.artifact_id}"
        )
        receipt = LiveShapedTurnReceipt(
            schema_version=LiveShapedTurnReceipt.SCHEMA_VERSION,
            receipt_id=deterministic_id(
                IdKind.VALIDATION,
                "cera.live_shaped_turn_receipt.v5",
                receipt_key,
            ),
            request_id=reasoner_request.prepared_turn.request.request_id,
            branch_id=reasoner_request.prepared_turn.request.branch_id,
            generation_id=reasoner_request.prepared_turn.request.generation_id,
            snapshot_token=reasoner_request.prepared_turn.evidence_snapshot.snapshot_token,
            reasoner_provider_receipt_id=reasoned.receipt.provider_receipt_id,
            reasoner_receipt_sha256=domain_sha256(
                "cera.scene_reasoner_receipt.v2", reasoned.receipt
            ),
            context_assembly_receipt_id=context.receipt.assembly_receipt_id,
            context_assembly_receipt_sha256=domain_sha256(
                "cera.composer_context_assembly_receipt.v1", context.receipt
            ),
            composer_provider_receipt_id=composed.composer_receipt.provider_receipt_id,
            composer_receipt_sha256=domain_sha256(
                "cera.scene_composer_receipt.v2", composed.composer_receipt
            ),
            realization_verification_id=(
                realization_verification.receipt.verification_id
            ),
            realization_verification_receipt_sha256=(
                realization_verification.receipt.receipt_sha256
            ),
            final_acceptance_receipt_id=final_acceptance.receipt_id,
            final_acceptance_receipt_sha256=final_acceptance.receipt_sha256,
            accepted_artifact_id=accepted_artifact.artifact_id,
            accepted_artifact_sha256=domain_sha256(
                "cera.accepted_story_artifact.v1", accepted_artifact
            ),
            evidence_lookup_receipt_ids=lookup_ids,
            external_provider_calls=(
                reasoned.receipt.external_provider_calls
                + composed.composer_receipt.external_provider_calls
                + realization_verification.receipt.external_provider_calls
            ),
            story_authority_writes=0,
            story_state_committed=False,
            adult_craft_selection_id=(
                adult_selection.receipt.selection_id if adult_selection is not None else None
            ),
            adult_craft_selection_sha256=(
                adult_selection.receipt.selection_sha256 if adult_selection is not None else None
            ),
            specificity_validation_receipt_id=(
                adult_evidence.final_specificity_receipt.validation_receipt_id
                if adult_evidence is not None
                else None
            ),
            specificity_validation_receipt_sha256=(
                adult_evidence.final_specificity_receipt.receipt_sha256
                if adult_evidence is not None
                else None
            ),
            semantic_specificity_receipt_id=(
                adult_evidence.final_semantic_receipt.receipt_id
                if adult_evidence is not None
                else None
            ),
            semantic_specificity_receipt_sha256=(
                adult_evidence.final_semantic_receipt.receipt_sha256
                if adult_evidence is not None
                else None
            ),
            beat_repair_receipt_id=(
                adult_evidence.beat_repair_receipt.repair_receipt_id
                if adult_evidence is not None and adult_evidence.beat_repair_receipt is not None
                else None
            ),
            beat_repair_receipt_sha256=(
                adult_evidence.beat_repair_receipt.receipt_sha256
                if adult_evidence is not None and adult_evidence.beat_repair_receipt is not None
                else None
            ),
        )
        return LiveShapedTurnResult(
            reasoner=reasoned,
            context=context,
            composer=composed,
            realization_verification=realization_verification,
            final_acceptance=final_acceptance,
            accepted_artifact=accepted_artifact,
            receipt=receipt,
            adult_craft=adult_evidence,
        )

    def _post_composer_terminal_failure(
        self,
        request: SceneReasonerRequest,
        reasoned,
        context,
        composed,
        code: ErrorCode,
        message: str,
        stage: str,
        *,
        adult_selection=None,
        adult_evidence=None,
        additional_receipts=(),
    ) -> LiveShapedTurnFailure:
        handles = _post_composer_failure_handles(
            reasoned,
            context,
            composed,
            adult_evidence,
            adult_selection=adult_selection,
        )
        payloads = _post_composer_failure_payloads(
            reasoned,
            context,
            composed,
            adult_evidence,
            adult_selection=adult_selection,
        )
        if additional_receipts:
            handles = _dedupe_handles(
                (
                    *handles,
                    *(
                        FailureEvidenceHandle(kind, evidence_id, evidence_sha256)
                        for kind, evidence_id, evidence_sha256, _ in additional_receipts
                    ),
                )
            )
            payloads = _dedupe_payloads(
                (
                    *payloads,
                    *(
                        _safe_payload(kind, evidence_id, evidence_sha256, receipt)
                        for kind, evidence_id, evidence_sha256, receipt in additional_receipts
                    ),
                )
            )
        calls = (
            reasoned.receipt.external_provider_calls
            + composed.composer_receipt.external_provider_calls
        )
        bundle = self._audit_failed(
            request,
            stage,
            code,
            message,
            input_sha256=composed.candidate.candidate_sha256,
            output_sha256=None,
            handles=handles,
            payloads=payloads,
            calls=calls,
        )
        failure = self._failure(request, code, message, stage=stage)
        failure.retained_evidence_handles = handles
        failure.privacy_safe_receipt_payloads = payloads
        failure.failure_evidence_bundle = bundle
        return failure

    def _audit_started(
        self,
        request: SceneReasonerRequest,
        stage: str,
        input_sha256: str | None,
        calls: int,
    ) -> None:
        if self.stage_audit_journal is not None:
            self.stage_audit_journal.started(
                request.prepared_turn,
                stage,
                input_sha256=input_sha256,
                external_provider_calls_observed=calls,
            )

    def _audit_completed(
        self,
        request: SceneReasonerRequest,
        stage: str,
        input_sha256: str | None,
        output_sha256: str,
        calls: int,
    ) -> None:
        if self.stage_audit_journal is not None:
            self.stage_audit_journal.completed(
                request.prepared_turn,
                stage,
                input_sha256=input_sha256,
                output_sha256=output_sha256,
                external_provider_calls_observed=calls,
            )

    def _audit_failed(
        self,
        request: SceneReasonerRequest,
        stage: str,
        code: ErrorCode,
        message: str,
        *,
        input_sha256: str | None,
        output_sha256: str | None,
        handles: tuple[FailureEvidenceHandle, ...],
        payloads: tuple[PrivacySafeReceiptPayload, ...] = (),
        safe_diagnostic_codes: tuple[str, ...] = (),
        calls: int,
    ):
        if self.stage_audit_journal is not None:
            return self.stage_audit_journal.failed(
                request.prepared_turn,
                stage,
                code,
                message,
                input_sha256=input_sha256,
                output_sha256=output_sha256,
                retained_evidence=handles,
                retained_receipt_payloads=payloads,
                safe_diagnostic_codes=safe_diagnostic_codes,
                external_provider_calls_observed=calls,
            )
        return None

    @staticmethod
    def _failure(
        request: SceneReasonerRequest,
        code: ErrorCode,
        message: str,
        *,
        stage: str = "turn_pipeline",
        details: tuple[str, ...] = (),
    ) -> LiveShapedTurnFailure:
        prepared = request.prepared_turn
        return LiveShapedTurnFailure(
            ErrorEnvelope(
                schema_version=ErrorEnvelope.SCHEMA_VERSION,
                error_code=code,
                message=message,
                trace_id=deterministic_id(
                    IdKind.TRACE,
                    "cera.live_shaped_turn.failure.v1",
                    f"{prepared.request.request_id}|{stage}|{code.value}",
                ),
                request_id=prepared.request.request_id,
                branch_id=prepared.request.branch_id,
                generation_id=prepared.request.generation_id,
                stage=stage,
                story_state_committed=False,
                retry_mode=RetryMode.MANUAL_AFTER_REVIEW,
                details=details,
            )
        )


def _bind_protected_user_source_authority(source_packet, outcome):
    """Narrow ordinary protected-user realization to exact Reasoner anchors."""

    if source_packet.mode is not CompositionMode.ORDINARY:
        return source_packet
    claims_by_unit = {}
    for claim in outcome.protected_user_source_claims:
        claims_by_unit.setdefault(claim.source_unit_id, []).append(claim)
    kind_map = {
        ProtectedUserClaimKind.ACTION: RealizationKind.ACTION,
        ProtectedUserClaimKind.DIALOGUE: RealizationKind.DIALOGUE,
    }
    units = []
    for unit in source_packet.ordinary_units:
        claims = tuple(claims_by_unit.get(unit.source_unit_id, ()))
        for claim in claims:
            if claim.end > len(unit.exact_text):
                raise ContractValidationError(
                    "protected-user source claim exceeds exact Composer source"
                )
            exact = unit.exact_text[claim.start : claim.end]
            if text_sha256(exact) != claim.exact_text_sha256:
                raise ContractValidationError(
                    "protected-user source claim changed between Reasoner and Composer"
                )
        allowed = tuple(dict.fromkeys(kind_map[value.kind] for value in claims))
        units.append(replace(unit, protected_user_allowed_kinds=allowed))
    unknown = set(claims_by_unit) - {value.source_unit_id for value in units}
    if unknown:
        raise ContractValidationError(
            "protected-user source claim is absent from Composer source"
        )
    return replace(source_packet, ordinary_units=tuple(units))


def _reasoner_failure_handles(
    failure: ReasonerExecutionFailure,
) -> tuple[FailureEvidenceHandle, ...]:
    handles: list[FailureEvidenceHandle] = []
    if failure.provider_call_receipt is not None:
        handles.append(
            FailureEvidenceHandle(
                "provider_call_receipt",
                failure.provider_call_receipt.provider_receipt_id,
                failure.provider_call_receipt.receipt_sha256,
            )
        )
    if failure.mcp_bridge_receipt is not None:
        handles.append(
            FailureEvidenceHandle(
                "mcp_bridge_receipt",
                failure.mcp_bridge_receipt.bridge_receipt_id,
                failure.mcp_bridge_receipt.receipt_sha256,
            )
        )
    handles.extend(
        FailureEvidenceHandle(
            "evidence_lookup_receipt",
            value.lookup_receipt_id,
            domain_sha256("cera.evidence_lookup_receipt.v1", value),
        )
        for value in failure.lookup_receipts
    )
    return tuple(handles)


_VALUE_FREE_REASONER_DIAGNOSTIC = re.compile(r"^[A-Za-z0-9_.:\[\]-]{1,160}$")


def _reasoner_failure_diagnostic_codes(
    failure: ReasonerExecutionFailure,
) -> tuple[str, ...]:
    """Project value-free Reasoner details into the durable audit vocabulary.

    Transport and typed-decoder diagnostics deliberately use compact tokens
    such as ``worker_stage:thread_resume``.  Failure bundles use a stricter
    uppercase identifier grammar.  This projection preserves the useful stage
    without allowing exception text, story content, or provider output into
    the durable audit record.
    """

    projected: list[str] = []
    for detail in failure.envelope.details:
        if _VALUE_FREE_REASONER_DIAGNOSTIC.fullmatch(detail) is None:
            continue
        suffix = re.sub(r"[^A-Za-z0-9]+", "_", detail).strip("_").upper()
        code = f"REASONER_{suffix}"
        if not 3 <= len(code) <= 96 or code in projected:
            continue
        projected.append(code)
    return tuple(projected)


def _reasoner_failure_payloads(
    failure: ReasonerExecutionFailure,
) -> tuple[PrivacySafeReceiptPayload, ...]:
    payloads: list[PrivacySafeReceiptPayload] = []
    if failure.provider_call_receipt is not None:
        payloads.append(
            _safe_payload(
                "provider_call_receipt",
                failure.provider_call_receipt.provider_receipt_id,
                failure.provider_call_receipt.receipt_sha256,
                failure.provider_call_receipt,
            )
        )
    if failure.mcp_bridge_receipt is not None:
        payloads.append(
            _safe_payload(
                "mcp_bridge_receipt",
                failure.mcp_bridge_receipt.bridge_receipt_id,
                failure.mcp_bridge_receipt.receipt_sha256,
                failure.mcp_bridge_receipt,
            )
        )
    payloads.extend(
        _safe_payload(
            "evidence_lookup_receipt",
            value.lookup_receipt_id,
            domain_sha256("cera.evidence_lookup_receipt.v1", value),
            value,
        )
        for value in failure.lookup_receipts
    )
    return tuple(payloads)


def _reasoner_success_evidence(reasoned):
    handles = [
        FailureEvidenceHandle(
            "scene_reasoner_receipt",
            reasoned.receipt.provider_receipt_id,
            domain_sha256("cera.scene_reasoner_receipt.v2", reasoned.receipt),
        )
    ]
    payloads = [
        _safe_payload(
            "scene_reasoner_receipt",
            reasoned.receipt.provider_receipt_id,
            handles[0].evidence_sha256,
            reasoned.receipt,
        )
    ]
    optional = []
    if reasoned.provider_call_receipt is not None:
        optional.append(
            (
                "reasoner_provider_call_receipt",
                reasoned.provider_call_receipt.provider_receipt_id,
                reasoned.provider_call_receipt.receipt_sha256,
                reasoned.provider_call_receipt,
            )
        )
    if reasoned.mcp_bridge_receipt is not None:
        optional.append(
            (
                "mcp_bridge_receipt",
                reasoned.mcp_bridge_receipt.bridge_receipt_id,
                reasoned.mcp_bridge_receipt.receipt_sha256,
                reasoned.mcp_bridge_receipt,
            )
        )
    optional.extend(
        (
            "evidence_lookup_receipt",
            value.lookup_receipt_id,
            domain_sha256("cera.evidence_lookup_receipt.v1", value),
            value,
        )
        for value in reasoned.evidence_lookup_receipts
    )
    for kind, evidence_id, evidence_sha256, receipt in optional:
        handles.append(FailureEvidenceHandle(kind, evidence_id, evidence_sha256))
        payloads.append(_safe_payload(kind, evidence_id, evidence_sha256, receipt))
    return _dedupe_handles(handles), _dedupe_payloads(payloads)


def _composer_failure_handles(
    failure: ComposerExecutionFailure,
    context,
    reasoned,
) -> tuple[FailureEvidenceHandle, ...]:
    handles = [
        FailureEvidenceHandle(
            "scene_reasoner_receipt",
            reasoned.receipt.provider_receipt_id,
            domain_sha256("cera.scene_reasoner_receipt.v2", reasoned.receipt),
        ),
        FailureEvidenceHandle(
            "composer_context_assembly_receipt",
            context.receipt.assembly_receipt_id,
            domain_sha256(
                "cera.composer_context_assembly_receipt.v1",
                context.receipt,
            ),
        )
    ]
    if reasoned.provider_call_receipt is not None:
        handles.append(
            FailureEvidenceHandle(
                "reasoner_provider_call_receipt",
                reasoned.provider_call_receipt.provider_receipt_id,
                reasoned.provider_call_receipt.receipt_sha256,
            )
        )
    if reasoned.mcp_bridge_receipt is not None:
        handles.append(
            FailureEvidenceHandle(
                "mcp_bridge_receipt",
                reasoned.mcp_bridge_receipt.bridge_receipt_id,
                reasoned.mcp_bridge_receipt.receipt_sha256,
            )
        )
    if failure.provider_call_receipt is not None:
        handles.append(
            FailureEvidenceHandle(
                "provider_call_receipt",
                failure.provider_call_receipt.provider_receipt_id,
                failure.provider_call_receipt.receipt_sha256,
            )
        )
    handles.extend(
        FailureEvidenceHandle(
            "evidence_lookup_receipt",
            value.lookup_receipt_id,
            domain_sha256("cera.evidence_lookup_receipt.v1", value),
        )
        for value in (
            *reasoned.evidence_lookup_receipts,
            *context.lookup_receipts,
        )
    )
    return tuple(
        {
            (value.evidence_kind, str(value.evidence_id)): value
            for value in handles
        }.values()
    )


def _composer_failure_payloads(failure, context, reasoned):
    handles, payloads = _reasoner_success_evidence(reasoned)
    records = [
        (
            "composer_context_assembly_receipt",
            context.receipt.assembly_receipt_id,
            domain_sha256(
                "cera.composer_context_assembly_receipt.v1", context.receipt
            ),
            context.receipt,
        ),
        *(
            [
                (
                    "provider_call_receipt",
                    failure.provider_call_receipt.provider_receipt_id,
                    failure.provider_call_receipt.receipt_sha256,
                    failure.provider_call_receipt,
                )
            ]
            if failure.provider_call_receipt is not None
            else []
        ),
        *(
            (
                "evidence_lookup_receipt",
                value.lookup_receipt_id,
                domain_sha256("cera.evidence_lookup_receipt.v1", value),
                value,
            )
            for value in context.lookup_receipts
        ),
    ]
    handle_list = list(handles)
    payload_list = list(payloads)
    for kind, evidence_id, evidence_sha256, receipt in records:
        handle_list.append(FailureEvidenceHandle(kind, evidence_id, evidence_sha256))
        payload_list.append(_safe_payload(kind, evidence_id, evidence_sha256, receipt))
    return _dedupe_payloads(payload_list)


def _post_composer_failure_handles(
    reasoned,
    context,
    composed,
    adult_evidence,
    adult_selection=None,
    realization_verification_receipt=None,
    realization_verifier_provider_receipt=None,
) -> tuple[FailureEvidenceHandle, ...]:
    """Retain only safe receipts from every successfully crossed boundary."""

    handles = [
        FailureEvidenceHandle(
            "scene_reasoner_receipt",
            reasoned.receipt.provider_receipt_id,
            domain_sha256("cera.scene_reasoner_receipt.v2", reasoned.receipt),
        ),
        FailureEvidenceHandle(
            "composer_context_assembly_receipt",
            context.receipt.assembly_receipt_id,
            domain_sha256(
                "cera.composer_context_assembly_receipt.v1",
                context.receipt,
            ),
        ),
        FailureEvidenceHandle(
            "scene_composer_receipt",
            composed.composer_receipt.provider_receipt_id,
            domain_sha256(
                "cera.scene_composer_receipt.v2",
                composed.composer_receipt,
            ),
        ),
        FailureEvidenceHandle(
            "composer_validation_receipt",
            composed.validation_receipt.validation_receipt_id,
            domain_sha256(
                "cera.composer_validation_receipt.v1",
                composed.validation_receipt,
            ),
        ),
    ]
    if reasoned.provider_call_receipt is not None:
        handles.append(
            FailureEvidenceHandle(
                "reasoner_provider_call_receipt",
                reasoned.provider_call_receipt.provider_receipt_id,
                reasoned.provider_call_receipt.receipt_sha256,
            )
        )
    if reasoned.mcp_bridge_receipt is not None:
        handles.append(
            FailureEvidenceHandle(
                "mcp_bridge_receipt",
                reasoned.mcp_bridge_receipt.bridge_receipt_id,
                reasoned.mcp_bridge_receipt.receipt_sha256,
            )
        )
    if composed.provider_call_receipt is not None:
        handles.append(
            FailureEvidenceHandle(
                "composer_provider_call_receipt",
                composed.provider_call_receipt.provider_receipt_id,
                composed.provider_call_receipt.receipt_sha256,
            )
        )
    handles.extend(
        FailureEvidenceHandle(
            "evidence_lookup_receipt",
            value.lookup_receipt_id,
            domain_sha256("cera.evidence_lookup_receipt.v1", value),
        )
        for value in (
            *reasoned.evidence_lookup_receipts,
            *context.lookup_receipts,
        )
    )
    if adult_evidence is not None:
        handles.extend(
            (
                FailureEvidenceHandle(
                    "adult_craft_selection_receipt",
                    adult_evidence.selection_receipt.selection_id,
                    adult_evidence.selection_receipt.selection_sha256,
                ),
                FailureEvidenceHandle(
                    "adult_specificity_validation_receipt",
                    adult_evidence.final_specificity_receipt.validation_receipt_id,
                    adult_evidence.final_specificity_receipt.receipt_sha256,
                ),
                FailureEvidenceHandle(
                    "adult_semantic_specificity_receipt",
                    adult_evidence.final_semantic_receipt.receipt_id,
                    adult_evidence.final_semantic_receipt.receipt_sha256,
                ),
            )
        )
        if adult_evidence.beat_repair_receipt is not None:
            handles.append(
                FailureEvidenceHandle(
                    "adult_beat_repair_receipt",
                    adult_evidence.beat_repair_receipt.repair_receipt_id,
                    adult_evidence.beat_repair_receipt.receipt_sha256,
                )
            )
    elif adult_selection is not None:
        handles.append(
            FailureEvidenceHandle(
                "adult_craft_selection_receipt",
                adult_selection.receipt.selection_id,
                adult_selection.receipt.selection_sha256,
            )
        )
    if realization_verification_receipt is not None:
        handles.append(
            FailureEvidenceHandle(
                "scene_realization_verification_receipt",
                realization_verification_receipt.verification_id,
                realization_verification_receipt.receipt_sha256,
            )
        )
    if realization_verifier_provider_receipt is not None:
        handles.append(
            FailureEvidenceHandle(
                "realization_verifier_provider_call_receipt",
                realization_verifier_provider_receipt.provider_receipt_id,
                realization_verifier_provider_receipt.receipt_sha256,
            )
        )
    return tuple(
        {
            (value.evidence_kind, str(value.evidence_id)): value
            for value in handles
        }.values()
    )


def _post_composer_failure_payloads(
    reasoned,
    context,
    composed,
    adult_evidence,
    adult_selection=None,
    realization_verification_receipt=None,
    realization_verifier_provider_receipt=None,
) -> tuple[PrivacySafeReceiptPayload, ...]:
    _, prior = _reasoner_success_evidence(reasoned)
    records = [
        (
            "composer_context_assembly_receipt",
            context.receipt.assembly_receipt_id,
            domain_sha256(
                "cera.composer_context_assembly_receipt.v1", context.receipt
            ),
            context.receipt,
        ),
        (
            "scene_composer_receipt",
            composed.composer_receipt.provider_receipt_id,
            domain_sha256("cera.scene_composer_receipt.v2", composed.composer_receipt),
            composed.composer_receipt,
        ),
        (
            "composer_validation_receipt",
            composed.validation_receipt.validation_receipt_id,
            domain_sha256(
                "cera.composer_validation_receipt.v1", composed.validation_receipt
            ),
            composed.validation_receipt,
        ),
    ]
    if composed.provider_call_receipt is not None:
        records.append(
            (
                "composer_provider_call_receipt",
                composed.provider_call_receipt.provider_receipt_id,
                composed.provider_call_receipt.receipt_sha256,
                composed.provider_call_receipt,
            )
        )
    records.extend(
        (
            "evidence_lookup_receipt",
            value.lookup_receipt_id,
            domain_sha256("cera.evidence_lookup_receipt.v1", value),
            value,
        )
        for value in context.lookup_receipts
    )
    if adult_evidence is not None:
        records.extend(
            (
                (
                    "adult_craft_selection_receipt",
                    adult_evidence.selection_receipt.selection_id,
                    adult_evidence.selection_receipt.selection_sha256,
                    adult_evidence.selection_receipt,
                ),
                (
                    "adult_specificity_validation_receipt",
                    adult_evidence.final_specificity_receipt.validation_receipt_id,
                    adult_evidence.final_specificity_receipt.receipt_sha256,
                    adult_evidence.final_specificity_receipt,
                ),
                (
                    "adult_semantic_specificity_receipt",
                    adult_evidence.final_semantic_receipt.receipt_id,
                    adult_evidence.final_semantic_receipt.receipt_sha256,
                    adult_evidence.final_semantic_receipt,
                ),
            )
        )
        if adult_evidence.beat_repair_receipt is not None:
            records.append(
                (
                    "adult_beat_repair_receipt",
                    adult_evidence.beat_repair_receipt.repair_receipt_id,
                    adult_evidence.beat_repair_receipt.receipt_sha256,
                    adult_evidence.beat_repair_receipt,
                )
            )
    elif adult_selection is not None:
        records.append(
            (
                "adult_craft_selection_receipt",
                adult_selection.receipt.selection_id,
                adult_selection.receipt.selection_sha256,
                adult_selection.receipt,
            )
        )
    if realization_verification_receipt is not None:
        records.append(
            (
                "scene_realization_verification_receipt",
                realization_verification_receipt.verification_id,
                realization_verification_receipt.receipt_sha256,
                realization_verification_receipt,
            )
        )
    if realization_verifier_provider_receipt is not None:
        records.append(
            (
                "realization_verifier_provider_call_receipt",
                realization_verifier_provider_receipt.provider_receipt_id,
                realization_verifier_provider_receipt.receipt_sha256,
                realization_verifier_provider_receipt,
            )
        )
    payloads = list(prior)
    payloads.extend(
        _safe_payload(kind, evidence_id, evidence_sha256, receipt)
        for kind, evidence_id, evidence_sha256, receipt in records
    )
    return _dedupe_payloads(payloads)


def _safe_payload(kind, evidence_id, evidence_sha256, receipt):
    return PrivacySafeReceiptPayload.from_receipt(
        kind,
        evidence_id,
        evidence_sha256,
        receipt,
    )


def _dedupe_handles(values):
    return tuple(
        {
            (value.evidence_kind, str(value.evidence_id)): value
            for value in values
        }.values()
    )


def _dedupe_payloads(values):
    return tuple(
        {
            (value.evidence_kind, str(value.evidence_id)): value
            for value in values
        }.values()
    )
