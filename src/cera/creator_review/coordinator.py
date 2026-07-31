"""Display-first creator review over CERA's existing atomic publication path."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
import json
import time

from cera.errors import StateConflictError, TransactionError
from cera.ids import IdKind, TypedId, deterministic_id
from cera.schema import from_mapping
from cera.serialization import canonical_json, domain_sha256, text_sha256

from .models import (
    CorrectionDiagnosticKind,
    CreatorCorrectionDiagnostic,
    CreatorAcceptTimingReceipt,
    CreatorReviewAction,
    CreatorReviewAssessment,
    CreatorReviewRecord,
    CreatorReviewSeverity,
    CreatorReviewState,
    PreparedPublicationPackage,
    PublicationEligibility,
    ReviewIssueOwner,
)


class CreatorReviewCoordinator:
    """Persist provisional prose and publish only after a creator decision."""

    def __init__(self, store, *, ttl: timedelta = timedelta(days=7)) -> None:
        if ttl <= timedelta(0):
            raise ValueError("creator review TTL must be positive")
        self.store = store
        self.ttl = ttl

    def record_provisional(self, application_request, candidate) -> CreatorReviewRecord:
        turn = application_request.reasoner_request.prepared_turn.request
        branch = self.store.get_branch(turn.branch_id)
        decision = candidate.reasoner.outcome.decision
        assert decision is not None
        sequence_beats = tuple(
            value.neutral_event for value in decision.current_segment.ordered_beats
        )
        sequence_plan_sha256 = domain_sha256(
            "cera.accepted_current_sequence_plan.v1",
            decision.current_segment,
        )
        review_id = deterministic_id(
            IdKind.REVIEW_PACKET,
            "cera.creator_review_record.v1",
            (
                f"{turn.request_id}|{candidate.composer.candidate.candidate_sha256}|"
                f"{application_request.composer_plan.creator_revision.directive_sha256 if application_request.composer_plan.creator_revision is not None else 'initial'}"
            ),
        )
        now = _utc_now()
        record = CreatorReviewRecord(
            schema_version=CreatorReviewRecord.SCHEMA_VERSION,
            review_id=review_id,
            world_id=branch.world_id,
            branch_id=turn.branch_id,
            request_id=turn.request_id,
            generation_id=turn.generation_id,
            expected_generation=branch.generation,
            expected_head_artifact_id=branch.head_artifact_id,
            source_sha256=turn.source_sha256,
            snapshot_token=(
                application_request.reasoner_request.prepared_turn.evidence_snapshot.snapshot_token
            ),
            sequence_plan_sha256=sequence_plan_sha256,
            sequence_beats=sequence_beats,
            candidate_sha256=candidate.composer.candidate.candidate_sha256,
            candidate_text_sha256=(
                candidate.composer.candidate.story_text_sha256
            ),
            candidate_text=candidate.composer.candidate.story_text,
            state=CreatorReviewState.PROVISIONAL_VISIBLE,
            assessment=None,
            prepared_package=None,
            application_request_json=canonical_json(application_request),
            provisional_candidate_json=canonical_json(candidate),
            live_result_json=None,
            creator_action=None,
            creator_feedback=None,
            creator_feedback_sha256=None,
            created_at=now,
            updated_at=now,
            resolved_at=None,
        )
        self.store.create_creator_review(record)
        verifying = replace(
            record,
            state=CreatorReviewState.VERIFYING_AND_PREPARING,
            updated_at=_utc_now(),
        )
        self.store.replace_creator_review(
            verifying,
            expected_states=(CreatorReviewState.PROVISIONAL_VISIBLE,),
        )
        return verifying

    def prepare_publication(
        self,
        review_id: TypedId,
        application_request,
        result,
    ) -> CreatorReviewRecord:
        current = self.store.get_creator_review(review_id)
        self._assert_result_binding(current, application_request, result)
        assessment = result.realization_verification.creator_review_assessment
        if assessment is None:
            assessment = _good_assessment()
        if assessment.publication_eligibility is not PublicationEligibility.ACCEPT_ALLOWED:
            raise StateConflictError(
                "a blocked verifier result cannot create a publication package"
            )
        now_dt = datetime.now(UTC)
        live_json = canonical_json(result)
        app_json = canonical_json(application_request)
        turn = application_request.reasoner_request.prepared_turn.request
        package = PreparedPublicationPackage(
            schema_version=PreparedPublicationPackage.SCHEMA_VERSION,
            package_id=deterministic_id(
                IdKind.PREPARED_PUBLICATION,
                "cera.prepared_publication_package.v1",
                (
                    f"{review_id}|{current.sequence_plan_sha256}|"
                    f"{current.candidate_sha256}"
                ),
            ),
            world_id=current.world_id,
            branch_id=current.branch_id,
            request_id=current.request_id,
            generation_id=current.generation_id,
            expected_generation=current.expected_generation,
            expected_head_artifact_id=current.expected_head_artifact_id,
            source_sha256=current.source_sha256,
            snapshot_token=current.snapshot_token,
            sequence_plan_sha256=current.sequence_plan_sha256,
            candidate_sha256=current.candidate_sha256,
            accepted_artifact_id=result.accepted_artifact.artifact_id,
            accepted_artifact_sha256=domain_sha256(
                "cera.accepted_story_artifact.v1", result.accepted_artifact
            ),
            verifier_receipt_sha256=(
                result.realization_verification.receipt.receipt_sha256
            ),
            application_request_sha256=application_request.request_sha256,
            live_result_sha256=domain_sha256(
                "cera.live_shaped_turn_result.v1", result
            ),
            idempotency_key_sha256=text_sha256(turn.idempotency_key),
            created_at=now_dt.isoformat(timespec="microseconds"),
            expires_at=(now_dt + self.ttl).isoformat(timespec="microseconds"),
            story_state_committed=False,
        )
        ready = replace(
            current,
            state=CreatorReviewState.REVIEW_READY,
            assessment=assessment,
            prepared_package=package,
            application_request_json=app_json,
            live_result_json=live_json,
            updated_at=_utc_now(),
        )
        self.store.replace_creator_review(
            ready,
            expected_states=(CreatorReviewState.VERIFYING_AND_PREPARING,),
        )
        return ready

    def record_blocked_review(
        self,
        review_id: TypedId,
        assessment: CreatorReviewAssessment,
    ) -> CreatorReviewRecord:
        if assessment.publication_eligibility is not PublicationEligibility.ACCEPT_BLOCKED:
            raise ValueError("blocked review requires blocked publication eligibility")
        current = self.store.get_creator_review(review_id)
        ready = replace(
            current,
            state=CreatorReviewState.REVIEW_READY,
            assessment=assessment,
            prepared_package=None,
            live_result_json=None,
            updated_at=_utc_now(),
        )
        self.store.replace_creator_review(
            ready,
            expected_states=(CreatorReviewState.VERIFYING_AND_PREPARING,),
        )
        return ready

    def record_error(self, review_id: TypedId, reason_code: str) -> CreatorReviewRecord:
        current = self.store.get_creator_review(review_id)
        assessment = CreatorReviewAssessment(
            schema_version=CreatorReviewAssessment.SCHEMA_VERSION,
            severity=CreatorReviewSeverity.ERROR,
            publication_eligibility=PublicationEligibility.ACCEPT_BLOCKED,
            issue_owner=ReviewIssueOwner.VERIFIER,
            reason_codes=(reason_code,),
            creator_reason="Error - Codex verification is unavailable; continuation is blocked.",
            verifier_status="unavailable",
        )
        failed = replace(
            current,
            state=CreatorReviewState.ERROR,
            assessment=assessment,
            prepared_package=None,
            live_result_json=None,
            updated_at=_utc_now(),
        )
        self.store.replace_creator_review(
            failed,
            expected_states=(
                CreatorReviewState.PROVISIONAL_VISIBLE,
                CreatorReviewState.VERIFYING_AND_PREPARING,
            ),
        )
        return failed

    def accept(
        self,
        review_id: TypedId,
        *,
        action: CreatorReviewAction = CreatorReviewAction.ACCEPT,
    ):
        from cera.runtime.application import OrdinaryApplicationRequestV2
        from cera.runtime.post_publication import PostPublicationCoordinator

        if action not in {
            CreatorReviewAction.ACCEPT,
            CreatorReviewAction.FALSE_POSITIVE,
        }:
            raise ValueError("creator acceptance action is invalid")

        timing_started_at = _utc_now()
        timing_started_ns = time.perf_counter_ns()
        current = self.store.get_creator_review(review_id)
        if current.state is CreatorReviewState.ACCEPTED:
            if current.creator_action is not action:
                raise StateConflictError(
                    "creator review was accepted with a different action"
                )
            package = current.prepared_package
            if package is None:
                raise TransactionError("accepted review lost its package audit binding")
            return PostPublicationCoordinator(self.store).resume(
                package.accepted_artifact_id
            )
        if current.state not in {
            CreatorReviewState.REVIEW_READY,
            CreatorReviewState.COMMITTING,
        }:
            raise StateConflictError("creator review is not eligible for acceptance")
        if (
            action is CreatorReviewAction.FALSE_POSITIVE
            and (
                current.assessment is None
                or current.assessment.severity
                not in {
                    CreatorReviewSeverity.CONCERN,
                    CreatorReviewSeverity.CRITICAL,
                }
            )
        ):
            raise StateConflictError(
                "false-positive acceptance requires a concern or critical assessment"
            )
        if (
            current.state is CreatorReviewState.COMMITTING
            and current.creator_action is not action
        ):
            raise StateConflictError(
                "creator review is committing another acceptance action"
            )
        self._assert_accept_payload(current)
        package = current.prepared_package
        assert package is not None
        if current.state is CreatorReviewState.COMMITTING:
            try:
                recovered = PostPublicationCoordinator(self.store).resume(
                    package.accepted_artifact_id
                )
            except TransactionError:
                # No atomic story commit exists yet. Replaying the bound local
                # commit below is safe because its idempotency key is immutable.
                pass
            else:
                self._record_accept_timing(
                    current,
                    started_at=timing_started_at,
                    started_ns=timing_started_ns,
                )
                if action is CreatorReviewAction.FALSE_POSITIVE:
                    self._record_false_positive(current)
                self._mark_accepted(current)
                return recovered
        if datetime.now(UTC) > datetime.fromisoformat(package.expires_at):
            raise StateConflictError("prepared publication package has expired")
        self._assert_branch_binding(current)
        if current.state is CreatorReviewState.REVIEW_READY:
            committing = replace(
                current,
                state=CreatorReviewState.COMMITTING,
                creator_action=action,
                updated_at=_utc_now(),
            )
            self.store.replace_creator_review(
                committing,
                expected_states=(CreatorReviewState.REVIEW_READY,),
            )
        else:
            committing = current
        try:
            application_request, result = self._decode_accept_payload(committing)
            published = PostPublicationCoordinator(self.store).execute(
                result,
                ingress_evidence=(
                    application_request.ingress_evidence
                    if isinstance(application_request, OrdinaryApplicationRequestV2)
                    else None
                ),
            )
            self._record_accept_timing(
                committing,
                started_at=timing_started_at,
                started_ns=timing_started_ns,
            )
            if action is CreatorReviewAction.FALSE_POSITIVE:
                self._record_false_positive(committing)
            self._mark_accepted(committing)
            return published
        except Exception:
            # COMMITTING is deliberately durable.  A crash can happen after the
            # atomic story commit but before this audit record is finalized.
            # A later Accept resumes the exact idempotent local commit and then
            # purges the provisional payload; it never calls a provider again.
            raise

    def _record_false_positive(self, record: CreatorReviewRecord) -> None:
        assessment = record.assessment
        if assessment is None:
            raise TransactionError("false-positive acceptance lost its assessment")
        feedback = (
            "Creator marked the Sol verifier assessment as a false positive and "
            "accepted the candidate unchanged. Original assessment: "
            + assessment.creator_reason
        )
        feedback_sha256 = text_sha256(feedback)
        diagnostic = CreatorCorrectionDiagnostic(
            schema_version=CreatorCorrectionDiagnostic.SCHEMA_VERSION,
            diagnostic_id=deterministic_id(
                IdKind.REVIEW_FINDING,
                "cera.creator_false_positive_diagnostic.v1",
                f"{record.review_id}|{assessment.assessment_sha256}",
            ),
            review_id=record.review_id,
            branch_id=record.branch_id,
            action=CreatorReviewAction.FALSE_POSITIVE,
            diagnostic_kind=CorrectionDiagnosticKind.FALSE_POSITIVE,
            likely_owner=ReviewIssueOwner.VERIFIER,
            assessment_sha256=assessment.assessment_sha256,
            reason_codes=assessment.reason_codes,
            creator_feedback=feedback,
            creator_feedback_sha256=feedback_sha256,
            status="pending_shared_review",
            created_at=record.created_at,
        )
        self.store.put_creator_correction_diagnostic(diagnostic)

    def _record_accept_timing(
        self,
        record: CreatorReviewRecord,
        *,
        started_at: str,
        started_ns: int,
    ) -> CreatorAcceptTimingReceipt:
        package = record.prepared_package
        if package is None:
            raise TransactionError("creator acceptance lost its prepared package")
        committed_at = _utc_now()
        elapsed_microseconds = max(
            0,
            (time.perf_counter_ns() - started_ns) // 1_000,
        )
        receipt = CreatorAcceptTimingReceipt(
            schema_version=CreatorAcceptTimingReceipt.SCHEMA_VERSION,
            receipt_id=deterministic_id(
                IdKind.TELEMETRY_EVENT,
                CreatorAcceptTimingReceipt.SCHEMA_VERSION,
                str(record.review_id),
            ),
            review_id=record.review_id,
            package_id=package.package_id,
            branch_id=record.branch_id,
            started_at=started_at,
            committed_at=committed_at,
            accept_to_commit_microseconds=elapsed_microseconds,
            provider_calls=0,
            automatic_retries=0,
        )
        self.store.put_creator_accept_timing(receipt)
        return receipt

    def request_feedback(
        self,
        review_id: TypedId,
        action: CreatorReviewAction,
    ) -> CreatorReviewRecord:
        if action not in {
            CreatorReviewAction.DEEPSEEK_REWRITE,
            CreatorReviewAction.CODEX_REPLAN,
            CreatorReviewAction.CORRECTION_ADJUSTMENT,
        }:
            raise ValueError("action does not enter creator feedback mode")
        current = self.store.get_creator_review(review_id)
        if current.state is not CreatorReviewState.REVIEW_READY:
            raise StateConflictError("creator review is not awaiting a decision")
        pending = replace(
            current,
            state=CreatorReviewState.AWAITING_FEEDBACK,
            creator_action=action,
            updated_at=_utc_now(),
        )
        self.store.replace_creator_review(
            pending,
            expected_states=(CreatorReviewState.REVIEW_READY,),
        )
        return pending

    def record_feedback(self, review_id: TypedId, feedback: str) -> CreatorReviewRecord:
        if not feedback.strip():
            raise ValueError("creator feedback cannot be empty")
        current = self.store.get_creator_review(review_id)
        if current.state is not CreatorReviewState.AWAITING_FEEDBACK:
            raise StateConflictError("creator review is not awaiting feedback")
        updated = replace(
            current,
            creator_feedback=feedback,
            creator_feedback_sha256=text_sha256(feedback),
            updated_at=_utc_now(),
        )
        self.store.replace_creator_review(
            updated,
            expected_states=(CreatorReviewState.AWAITING_FEEDBACK,),
        )
        assert updated.creator_action is not None
        assert updated.assessment is not None
        likely_owner = updated.assessment.issue_owner
        if likely_owner is ReviewIssueOwner.NONE:
            likely_owner = {
                CreatorReviewAction.DEEPSEEK_REWRITE: ReviewIssueOwner.COMPOSER,
                CreatorReviewAction.CODEX_REPLAN: ReviewIssueOwner.REASONER,
                CreatorReviewAction.CORRECTION_ADJUSTMENT: ReviewIssueOwner.MIXED,
            }[updated.creator_action]
        diagnostic_kind = {
            CreatorReviewAction.DEEPSEEK_REWRITE: (
                CorrectionDiagnosticKind.PROSE_REALIZATION
            ),
            CreatorReviewAction.CODEX_REPLAN: CorrectionDiagnosticKind.CAUSAL_LOGIC,
            CreatorReviewAction.CORRECTION_ADJUSTMENT: (
                CorrectionDiagnosticKind.CORRECTION_ADJUSTMENT
            ),
        }[updated.creator_action]
        diagnostic = CreatorCorrectionDiagnostic(
            schema_version=CreatorCorrectionDiagnostic.SCHEMA_VERSION,
            diagnostic_id=deterministic_id(
                IdKind.REVIEW_FINDING,
                "cera.creator_correction_diagnostic.v1",
                (
                    f"{review_id}|{updated.creator_action.value}|"
                    f"{updated.creator_feedback_sha256}"
                ),
            ),
            review_id=review_id,
            branch_id=updated.branch_id,
            action=updated.creator_action,
            diagnostic_kind=diagnostic_kind,
            likely_owner=likely_owner,
            assessment_sha256=updated.assessment.assessment_sha256,
            reason_codes=updated.assessment.reason_codes,
            creator_feedback=feedback,
            creator_feedback_sha256=updated.creator_feedback_sha256 or "",
            status="pending_shared_review",
            created_at=updated.created_at,
        )
        self.store.put_creator_correction_diagnostic(diagnostic)
        return updated

    def decline(self, review_id: TypedId) -> CreatorReviewRecord:
        current = self.store.get_creator_review(review_id)
        if current.state not in {
            CreatorReviewState.REVIEW_READY,
            CreatorReviewState.ERROR,
            CreatorReviewState.AWAITING_FEEDBACK,
        }:
            raise StateConflictError("creator review cannot be declined in this state")
        resolved = replace(
            current,
            state=CreatorReviewState.REJECTED,
            candidate_text=None,
            application_request_json=None,
            provisional_candidate_json=None,
            live_result_json=None,
            prepared_package=None,
            creator_action=CreatorReviewAction.DECLINE,
            creator_feedback=None,
            creator_feedback_sha256=None,
            updated_at=_utc_now(),
            resolved_at=_utc_now(),
        )
        self.store.replace_creator_review(
            resolved,
            expected_states=(current.state,),
        )
        return resolved

    def supersede_for_revision(self, review_id: TypedId) -> CreatorReviewRecord:
        current = self.store.get_creator_review(review_id)
        if (
            current.state is not CreatorReviewState.AWAITING_FEEDBACK
            or current.creator_action
            not in {
                CreatorReviewAction.DEEPSEEK_REWRITE,
                CreatorReviewAction.CODEX_REPLAN,
                CreatorReviewAction.CORRECTION_ADJUSTMENT,
            }
        ):
            raise StateConflictError(
                "creator review is not bound to an active manual revision"
            )
        resolved = replace(
            current,
            state=CreatorReviewState.REJECTED,
            candidate_text=None,
            application_request_json=None,
            provisional_candidate_json=None,
            live_result_json=None,
            prepared_package=None,
            creator_feedback=None,
            creator_feedback_sha256=None,
            updated_at=_utc_now(),
            resolved_at=_utc_now(),
        )
        self.store.replace_creator_review(
            resolved,
            expected_states=(CreatorReviewState.AWAITING_FEEDBACK,),
        )
        return resolved

    def _assert_branch_binding(self, record: CreatorReviewRecord) -> None:
        branch = self.store.get_branch(record.branch_id)
        if (
            branch.world_id != record.world_id
            or branch.generation != record.expected_generation
            or branch.head_artifact_id != record.expected_head_artifact_id
        ):
            raise StateConflictError(
                "prepared publication package is stale for the current branch head"
            )

    def _assert_result_binding(self, record, application_request, result) -> None:
        turn = application_request.reasoner_request.prepared_turn.request
        decision = result.reasoner.outcome.decision
        assert decision is not None
        if (
            turn.request_id != record.request_id
            or turn.branch_id != record.branch_id
            or turn.generation_id != record.generation_id
            or turn.source_sha256 != record.source_sha256
            or application_request.reasoner_request.prepared_turn.evidence_snapshot.snapshot_token
            != record.snapshot_token
            or domain_sha256(
                "cera.accepted_current_sequence_plan.v1",
                decision.current_segment,
            )
            != record.sequence_plan_sha256
            or result.composer.candidate.candidate_sha256 != record.candidate_sha256
        ):
            raise StateConflictError(
                "prepared result changed a creator-review authority binding"
            )
        self._assert_branch_binding(record)

    @staticmethod
    def _assert_accept_payload(record: CreatorReviewRecord) -> None:
        if (
            record.assessment is None
            or record.assessment.publication_eligibility
            is not PublicationEligibility.ACCEPT_ALLOWED
            or record.prepared_package is None
            or record.application_request_json is None
            or record.live_result_json is None
        ):
            raise StateConflictError("creator review is not eligible for acceptance")

    def _decode_accept_payload(self, record: CreatorReviewRecord):
        from cera.runtime.application import (
            OrdinaryApplicationRequest,
            OrdinaryApplicationRequestV2,
        )
        from cera.runtime.models import LiveShapedTurnResult

        self._assert_accept_payload(record)
        package = record.prepared_package
        assert package is not None
        assert record.application_request_json is not None
        assert record.live_result_json is not None
        app_payload = json.loads(record.application_request_json)
        app_type = (
            OrdinaryApplicationRequestV2
            if app_payload.get("schema_version")
            == OrdinaryApplicationRequestV2.SCHEMA_VERSION
            else OrdinaryApplicationRequest
        )
        application_request = from_mapping(app_type, app_payload)
        result = from_mapping(
            LiveShapedTurnResult,
            json.loads(record.live_result_json),
        )
        if (
            application_request.request_sha256
            != package.application_request_sha256
            or domain_sha256("cera.live_shaped_turn_result.v1", result)
            != package.live_result_sha256
            or result.accepted_artifact.artifact_id
            != package.accepted_artifact_id
            or domain_sha256(
                "cera.accepted_story_artifact.v1", result.accepted_artifact
            )
            != package.accepted_artifact_sha256
            or result.realization_verification.receipt.receipt_sha256
            != package.verifier_receipt_sha256
        ):
            raise StateConflictError(
                "prepared publication payload failed package hash validation"
            )
        self._assert_result_binding(record, application_request, result)
        return application_request, result

    def _mark_accepted(self, record: CreatorReviewRecord) -> CreatorReviewRecord:
        resolved = replace(
            record,
            state=CreatorReviewState.ACCEPTED,
            candidate_text=None,
            application_request_json=None,
            provisional_candidate_json=None,
            live_result_json=None,
            creator_feedback=None,
            creator_feedback_sha256=None,
            updated_at=_utc_now(),
            resolved_at=_utc_now(),
        )
        self.store.replace_creator_review(
            resolved,
            expected_states=(CreatorReviewState.COMMITTING,),
        )
        return resolved


def _good_assessment() -> CreatorReviewAssessment:
    return CreatorReviewAssessment(
        schema_version=CreatorReviewAssessment.SCHEMA_VERSION,
        severity=CreatorReviewSeverity.GOOD,
        publication_eligibility=PublicationEligibility.ACCEPT_ALLOWED,
        issue_owner=ReviewIssueOwner.NONE,
        reason_codes=(),
        creator_reason="Good - the accepted sequence and required boundaries were realized.",
        verifier_status="accepted",
    )


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")
