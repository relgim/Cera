"""Provider-free orchestration for accepted-checkpoint session trees."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

from cera.errors import StateConflictError
from cera.creator_review.models import CorrectionDiagnosticKind
from cera.ids import IdKind, TypedId, deterministic_id, new_id
from cera.serialization import canonical_sha256, text_sha256

from .models import (
    AcceptedTurnReceipt,
    CheckpointStatus,
    ConstraintBinding,
    ConstraintScope,
    ConstraintStatus,
    ContextAuthorityDelta,
    CreatorConstraintRecord,
    ProviderThreadCustodyEvent,
    ProviderThreadCustodyEventKind,
    ReasonerSessionCheckpoint,
    ReasonerSessionCompatibility,
    ReasonerSessionLedger,
    RejectedCandidateReceipt,
    SessionReconstructionBundle,
    SessionStatus,
    SessionTurnMode,
)
from .ports import ReasonerSessionPort


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


class BranchBoundReasonerSessionCoordinator:
    """Owns provider context state while Python remains story authority."""

    def __init__(self, store, port: ReasonerSessionPort) -> None:
        self.store = store
        self.port = port

    def create_session(
        self,
        compatibility: ReasonerSessionCompatibility,
        reconstruction: SessionReconstructionBundle,
        *,
        rotated_from_session_id: TypedId | None = None,
        provider_handle=None,
    ) -> ReasonerSessionLedger:
        self._validate_reconstruction(compatibility, reconstruction)
        handle = provider_handle or self.port.create_session(
            compatibility, reconstruction
        )
        session_id = new_id(IdKind.SESSION)
        now = _utc_now()
        checkpoint_id = deterministic_id(
            IdKind.CHECKPOINT,
            "cera.reasoner_session_root.v1",
            f"{session_id}|{reconstruction.bundle_sha256}",
        )
        root = ReasonerSessionCheckpoint(
            schema_version=ReasonerSessionCheckpoint.SCHEMA_VERSION,
            checkpoint_id=checkpoint_id,
            session_id=session_id,
            parent_checkpoint_id=None,
            branch_id=compatibility.branch_id,
            request_id=None,
            review_id=None,
            turn_mode=None,
            replaces_artifact_id=None,
            provider_handle=handle,
            status=CheckpointStatus.ACCEPTED,
            accepted_head_artifact_id=reconstruction.accepted_head_artifact_id,
            generation=reconstruction.generation,
            authority_revision=reconstruction.authority_revision,
            context_manifest_sha256=reconstruction.bundle_sha256,
            accepted_receipt_id=None,
            rejection_receipt_id=None,
            status_reason=None,
            created_at=now,
            updated_at=now,
        )
        ledger = ReasonerSessionLedger(
            schema_version=ReasonerSessionLedger.SCHEMA_VERSION,
            session_id=session_id,
            compatibility=compatibility,
            status=SessionStatus.ACTIVE,
            accepted_checkpoint_id=checkpoint_id,
            provider_root_handle=handle,
            accumulated_turns=0,
            rotated_from_session_id=rotated_from_session_id,
            status_reason=None,
            created_at=now,
            updated_at=now,
        )
        try:
            self.store.create_reasoner_session(
                ledger,
                root,
                self._custody_event(
                    root,
                    ProviderThreadCustodyEventKind.ALLOCATED,
                    "stored_session_root_allocated",
                ),
            )
        except Exception:
            self.port.archive_lineage(handle, "local_session_creation_failed")
            raise
        return ledger

    def ensure_session(
        self,
        compatibility: ReasonerSessionCompatibility,
        reconstruction: SessionReconstructionBundle,
    ) -> ReasonerSessionLedger:
        active = self.store.active_reasoner_session_for_branch_role(
            compatibility.branch_id,
            role=compatibility.role.value,
        )
        if active is None:
            return self.create_session(compatibility, reconstruction)
        if (
            active.compatibility.compatibility_sha256
            != compatibility.compatibility_sha256
        ):
            return self._replace_session(
                active,
                compatibility,
                reconstruction,
                terminal_status=SessionStatus.INVALIDATED,
                reason="session_compatibility_changed",
            )
        checkpoint = self.store.get_reasoner_checkpoint(
            active.accepted_checkpoint_id
        )
        resumed = self.port.try_resume(checkpoint.provider_handle)
        if resumed is not None:
            self.store.put_provider_thread_custody_event(
                self._custody_event(
                    checkpoint,
                    ProviderThreadCustodyEventKind.RESUMED,
                    "accepted_provider_checkpoint_resumed",
                )
            )
            return active
        self.store.put_provider_thread_custody_event(
            self._custody_event(
                checkpoint,
                ProviderThreadCustodyEventKind.MISSING,
                "accepted_provider_checkpoint_missing",
            )
        )
        return self.rotate_session(
            active.session_id,
            reconstruction,
            reason="provider_handle_unavailable_after_restart",
        )

    def begin_candidate(
        self,
        delta: ContextAuthorityDelta,
    ) -> ReasonerSessionCheckpoint:
        ledger = self.store.get_reasoner_session(delta.session_id)
        if ledger.status is not SessionStatus.ACTIVE:
            raise StateConflictError("reasoner session is not active")
        parent = self.store.get_reasoner_checkpoint(ledger.accepted_checkpoint_id)
        if delta.turn_mode is SessionTurnMode.APPEND:
            provider_parent = parent
            if delta.parent_checkpoint_id != parent.checkpoint_id:
                raise StateConflictError("append context changed accepted parent")
        else:
            expected_parent_id = parent.parent_checkpoint_id or parent.checkpoint_id
            if (
                delta.replaces_artifact_id != parent.accepted_head_artifact_id
                or delta.parent_checkpoint_id != expected_parent_id
            ):
                raise StateConflictError("regeneration context changed sibling base")
            provider_parent = (
                parent
                if parent.parent_checkpoint_id is None
                else self.store.get_reasoner_checkpoint(parent.parent_checkpoint_id)
            )
        if (
            provider_parent.status is not CheckpointStatus.ACCEPTED
            or delta.world_id != ledger.compatibility.world_id
            or delta.branch_id != ledger.compatibility.branch_id
            or delta.generation != parent.generation
            or delta.accepted_head_artifact_id != parent.accepted_head_artifact_id
            or delta.authority_revision != parent.authority_revision
        ):
            raise StateConflictError("context delta does not match accepted checkpoint")
        self._assert_current_constraints(ledger, delta)
        self._assert_materialized_evidence_is_accepted_ancestry(ledger, delta)
        handle = self.port.fork_candidate(provider_parent.provider_handle, delta)
        now = _utc_now()
        checkpoint = ReasonerSessionCheckpoint(
            schema_version=ReasonerSessionCheckpoint.SCHEMA_VERSION,
            checkpoint_id=deterministic_id(
                IdKind.CHECKPOINT,
                "cera.reasoner_candidate_checkpoint.v1",
                f"{ledger.session_id}|{delta.request_id}|{delta.delta_sha256}",
            ),
            session_id=ledger.session_id,
            parent_checkpoint_id=provider_parent.checkpoint_id,
            branch_id=delta.branch_id,
            request_id=delta.request_id,
            review_id=None,
            turn_mode=delta.turn_mode,
            replaces_artifact_id=delta.replaces_artifact_id,
            provider_handle=handle,
            status=CheckpointStatus.CANDIDATE,
            accepted_head_artifact_id=parent.accepted_head_artifact_id,
            generation=parent.generation,
            authority_revision=parent.authority_revision,
            context_manifest_sha256=delta.delta_sha256,
            accepted_receipt_id=None,
            rejection_receipt_id=None,
            status_reason=None,
            created_at=now,
            updated_at=now,
        )
        try:
            self.store.create_candidate_checkpoint(
                checkpoint,
                expected_accepted_checkpoint_id=parent.checkpoint_id,
                custody_event=self._custody_event(
                    checkpoint,
                    ProviderThreadCustodyEventKind.ALLOCATED,
                    "stored_candidate_fork_allocated",
                ),
            )
        except Exception:
            self.port.discard_failed_candidate(
                handle, "candidate_checkpoint_creation_failed"
            )
            raise
        return checkpoint

    def bind_creator_review(
        self, checkpoint_id: TypedId, review_id: TypedId
    ) -> ReasonerSessionCheckpoint:
        checkpoint = self.store.get_reasoner_checkpoint(checkpoint_id)
        review = self.store.get_creator_review(review_id)
        if (
            checkpoint.status is not CheckpointStatus.CANDIDATE
            or checkpoint.review_id is not None
            or checkpoint.branch_id != review.branch_id
            or checkpoint.request_id != review.request_id
        ):
            raise StateConflictError("creator review does not bind the candidate")
        updated = replace(
            checkpoint,
            review_id=review_id,
            updated_at=_utc_now(),
        )
        self.store.bind_candidate_review(updated)
        return updated

    def rebind_creator_review(
        self,
        checkpoint_id: TypedId,
        *,
        prior_review_id: TypedId,
        replacement_review_id: TypedId,
    ) -> ReasonerSessionCheckpoint:
        """Move one unchanged Reasoner candidate to a Composer-only revision."""

        checkpoint = self.store.get_reasoner_checkpoint(checkpoint_id)
        prior = self.store.get_creator_review(prior_review_id)
        replacement = self.store.get_creator_review(replacement_review_id)
        if (
            checkpoint.status is not CheckpointStatus.CANDIDATE
            or checkpoint.review_id != prior_review_id
            or prior.state.value != "rejected"
            or replacement.state.value not in {
                "provisional_visible",
                "verifying_and_preparing",
                "review_ready",
                "error",
            }
            or checkpoint.branch_id != replacement.branch_id
            or checkpoint.request_id != replacement.request_id
            or prior.branch_id != replacement.branch_id
            or prior.request_id != replacement.request_id
        ):
            raise StateConflictError("replacement review does not bind the candidate")
        updated = replace(
            checkpoint,
            review_id=replacement_review_id,
            updated_at=_utc_now(),
        )
        self.store.bind_candidate_review(
            updated,
            expected_review_id=prior_review_id,
        )
        return updated

    def accept_candidate(
        self,
        checkpoint_id: TypedId,
        *,
        artifact_id: TypedId,
        accepted_prose_sha256: str,
        accepted_event_state_sha256: str,
        commit_sha256: str,
    ) -> AcceptedTurnReceipt:
        checkpoint = self.store.get_reasoner_checkpoint(checkpoint_id)
        if (
            checkpoint.status is not CheckpointStatus.CANDIDATE
            or checkpoint.review_id is None
            or checkpoint.request_id is None
        ):
            raise StateConflictError("candidate is not bound for creator acceptance")
        review = self.store.get_creator_review(checkpoint.review_id)
        if review.state.value != "accepted" or review.prepared_package is None:
            raise StateConflictError("creator review has not accepted this candidate")
        if review.prepared_package.accepted_artifact_id != artifact_id:
            raise StateConflictError("accepted artifact changed after creator review")
        branch = self.store.get_branch(checkpoint.branch_id)
        if (
            branch.head_artifact_id != artifact_id
            or branch.generation != checkpoint.generation + 1
        ):
            raise StateConflictError("accepted branch commit is not complete")
        now = _utc_now()
        receipt_id = deterministic_id(
            IdKind.SESSION_RECEIPT,
            "cera.accepted_turn_session_receipt.v1",
            f"{checkpoint.session_id}|{checkpoint.checkpoint_id}|{artifact_id}|{commit_sha256}",
        )
        receipt = AcceptedTurnReceipt(
            schema_version=AcceptedTurnReceipt.SCHEMA_VERSION,
            receipt_id=receipt_id,
            session_id=checkpoint.session_id,
            checkpoint_id=checkpoint.checkpoint_id,
            review_id=checkpoint.review_id,
            request_id=checkpoint.request_id,
            branch_id=checkpoint.branch_id,
            artifact_id=artifact_id,
            replaces_artifact_id=checkpoint.replaces_artifact_id,
            generation_before=checkpoint.generation,
            generation_after=branch.generation,
            authority_revision_after=branch.authority_revision,
            accepted_prose_sha256=accepted_prose_sha256,
            accepted_event_state_sha256=accepted_event_state_sha256,
            commit_sha256=commit_sha256,
            provider_calls=0,
            automatic_retries=0,
            created_at=now,
        )
        updated_handle = self.port.inject_accepted_receipt(
            checkpoint.provider_handle, receipt
        )
        accepted = replace(
            checkpoint,
            provider_handle=updated_handle,
            status=CheckpointStatus.ACCEPTED,
            accepted_head_artifact_id=artifact_id,
            generation=branch.generation,
            authority_revision=branch.authority_revision,
            context_manifest_sha256=canonical_sha256(
                {
                    "candidate_manifest": checkpoint.context_manifest_sha256,
                    "accepted_receipt": receipt.receipt_sha256,
                }
            ),
            accepted_receipt_id=receipt_id,
            updated_at=now,
        )
        ledger = self.store.get_reasoner_session(checkpoint.session_id)
        promoted = replace(
            ledger,
            accepted_checkpoint_id=checkpoint.checkpoint_id,
            accumulated_turns=ledger.accumulated_turns + 1,
            updated_at=now,
        )
        try:
            self.store.accept_candidate_checkpoint(
                promoted,
                accepted,
                receipt,
                self._custody_event(
                    accepted,
                    ProviderThreadCustodyEventKind.ACCEPTED,
                    "creator_accepted_python_committed_candidate",
                ),
            )
        except Exception:
            self.port.discard_failed_candidate(
                updated_handle, "local_candidate_promotion_failed"
            )
            raise
        return receipt

    def reject_candidate(
        self,
        checkpoint_id: TypedId,
        *,
        scope: ConstraintScope = ConstraintScope.BRANCH,
        supersedes: tuple[TypedId, ...] = (),
    ) -> RejectedCandidateReceipt:
        checkpoint = self.store.get_reasoner_checkpoint(checkpoint_id)
        if (
            checkpoint.status is not CheckpointStatus.CANDIDATE
            or checkpoint.review_id is None
            or checkpoint.request_id is None
            or checkpoint.parent_checkpoint_id is None
        ):
            raise StateConflictError("candidate is not bound for rejection")
        review = self.store.get_creator_review(checkpoint.review_id)
        if review.state.value != "rejected":
            raise StateConflictError("creator review has not rejected this candidate")
        constraints = self._constraints_for_review(review, scope=scope)
        if supersedes and not constraints:
            raise StateConflictError("bare decline cannot supersede creator constraints")
        now = _utc_now()
        reason_codes = (
            review.assessment.reason_codes
            if review.assessment is not None and review.assessment.reason_codes
            else ("creator_declined",)
        )
        receipt_id = deterministic_id(
            IdKind.REJECTION,
            "cera.rejected_candidate_session_receipt.v1",
            f"{checkpoint.session_id}|{checkpoint.checkpoint_id}|{review.candidate_sha256}",
        )
        receipt = RejectedCandidateReceipt(
            schema_version=RejectedCandidateReceipt.SCHEMA_VERSION,
            receipt_id=receipt_id,
            session_id=checkpoint.session_id,
            checkpoint_id=checkpoint.checkpoint_id,
            parent_accepted_checkpoint_id=checkpoint.parent_checkpoint_id,
            review_id=checkpoint.review_id,
            request_id=checkpoint.request_id,
            branch_id=checkpoint.branch_id,
            candidate_sha256=review.candidate_sha256,
            replaces_artifact_id=checkpoint.replaces_artifact_id,
            reason_codes=reason_codes,
            constraint_ids=tuple(value.constraint_id for value in constraints),
            raw_candidate_retained=False,
            created_at=now,
        )
        self.port.mark_rejected(checkpoint.provider_handle, receipt)
        rejected = replace(
            checkpoint,
            status=CheckpointStatus.REJECTED,
            rejection_receipt_id=receipt_id,
            status_reason="creator_rejected_noncanonical_candidate",
            updated_at=now,
        )
        self.store.reject_candidate_checkpoint(
            rejected,
            receipt,
            constraints,
            supersedes=supersedes,
            custody_event=self._custody_event(
                rejected,
                ProviderThreadCustodyEventKind.REJECTED_ARCHIVED,
                "creator_rejected_noncanonical_candidate",
            ),
        )
        return receipt

    def invalidate_candidate(self, checkpoint_id: TypedId, reason: str) -> None:
        checkpoint = self.store.get_reasoner_checkpoint(checkpoint_id)
        if checkpoint.status is not CheckpointStatus.CANDIDATE:
            raise StateConflictError("only an open candidate can be invalidated")
        self.port.discard_failed_candidate(checkpoint.provider_handle, reason)
        invalidated = replace(
            checkpoint,
            status=CheckpointStatus.INVALIDATED,
            status_reason=reason,
            updated_at=_utc_now(),
        )
        self.store.invalidate_candidate_checkpoint(
            invalidated,
            self._custody_event(
                invalidated,
                ProviderThreadCustodyEventKind.FAILED_ARCHIVED,
                reason,
            ),
        )

    def rotate_session(
        self,
        session_id: TypedId,
        reconstruction: SessionReconstructionBundle,
        *,
        reason: str,
    ) -> ReasonerSessionLedger:
        current = self.store.get_reasoner_session(session_id)
        if current.status is not SessionStatus.ACTIVE:
            raise StateConflictError("only an active session can rotate")
        candidates = tuple(
            value
            for value in self.store.reasoner_checkpoints(session_id)
            if value.status is CheckpointStatus.CANDIDATE
        )
        if candidates:
            raise StateConflictError("open candidate must resolve before session rotation")
        return self._replace_session(
            current,
            current.compatibility,
            reconstruction,
            terminal_status=SessionStatus.ROTATED,
            reason=reason,
        )

    def _replace_session(
        self,
        current: ReasonerSessionLedger,
        compatibility: ReasonerSessionCompatibility,
        reconstruction: SessionReconstructionBundle,
        *,
        terminal_status: SessionStatus,
        reason: str,
    ) -> ReasonerSessionLedger:
        if current.status is not SessionStatus.ACTIVE:
            raise StateConflictError("only an active session can be replaced")
        candidates = tuple(
            value
            for value in self.store.reasoner_checkpoints(current.session_id)
            if value.status is CheckpointStatus.CANDIDATE
        )
        if candidates:
            raise StateConflictError("open candidate must resolve before session replacement")
        self._validate_reconstruction(compatibility, reconstruction)
        handle = self.port.create_session(compatibility, reconstruction)
        now = _utc_now()
        new_session_id = new_id(IdKind.SESSION)
        root_id = deterministic_id(
            IdKind.CHECKPOINT,
            "cera.reasoner_session_root.v1",
            f"{new_session_id}|{reconstruction.bundle_sha256}",
        )
        root = ReasonerSessionCheckpoint(
            schema_version=ReasonerSessionCheckpoint.SCHEMA_VERSION,
            checkpoint_id=root_id,
            session_id=new_session_id,
            parent_checkpoint_id=None,
            branch_id=compatibility.branch_id,
            request_id=None,
            review_id=None,
            turn_mode=None,
            replaces_artifact_id=None,
            provider_handle=handle,
            status=CheckpointStatus.ACCEPTED,
            accepted_head_artifact_id=reconstruction.accepted_head_artifact_id,
            generation=reconstruction.generation,
            authority_revision=reconstruction.authority_revision,
            context_manifest_sha256=reconstruction.bundle_sha256,
            accepted_receipt_id=None,
            rejection_receipt_id=None,
            status_reason=None,
            created_at=now,
            updated_at=now,
        )
        terminal = replace(
            current,
            status=terminal_status,
            status_reason=reason,
            updated_at=now,
        )
        new_ledger = ReasonerSessionLedger(
            schema_version=ReasonerSessionLedger.SCHEMA_VERSION,
            session_id=new_session_id,
            compatibility=compatibility,
            status=SessionStatus.ACTIVE,
            accepted_checkpoint_id=root_id,
            provider_root_handle=handle,
            accumulated_turns=0,
            rotated_from_session_id=current.session_id,
            status_reason=None,
            created_at=now,
            updated_at=now,
        )
        try:
            self.store.rotate_reasoner_session(
                terminal,
                new_ledger,
                root,
                self._custody_event(
                    root,
                    ProviderThreadCustodyEventKind.ALLOCATED,
                    "stored_session_reconstructed",
                ),
            )
        except Exception:
            self.port.archive_lineage(handle, "local_session_rotation_failed")
            raise
        prior_handle = self.store.get_reasoner_checkpoint(
            current.accepted_checkpoint_id
        ).provider_handle
        if self.port.try_resume(prior_handle) is not None:
            self.port.archive_lineage(prior_handle, reason)
            prior_checkpoint = self.store.get_reasoner_checkpoint(
                current.accepted_checkpoint_id
            )
            self.store.put_provider_thread_custody_event(
                self._custody_event(
                    prior_checkpoint,
                    ProviderThreadCustodyEventKind.ARCHIVED,
                    reason,
                )
            )
        return new_ledger

    def fork_branch_session(
        self,
        parent_session_id: TypedId,
        compatibility: ReasonerSessionCompatibility,
        reconstruction: SessionReconstructionBundle,
    ) -> ReasonerSessionLedger:
        parent = self.store.get_reasoner_session(parent_session_id)
        parent_checkpoint = self.store.get_reasoner_checkpoint(
            parent.accepted_checkpoint_id
        )
        child_branch = self.store.get_branch(compatibility.branch_id)
        if (
            parent.status is not SessionStatus.ACTIVE
            or child_branch.parent_branch_id != parent.compatibility.branch_id
            or child_branch.fork_artifact_id != parent_checkpoint.accepted_head_artifact_id
            or reconstruction.accepted_head_artifact_id != child_branch.head_artifact_id
        ):
            raise StateConflictError("provider branch fork does not match CERA branch fork")
        self._validate_reconstruction(compatibility, reconstruction)
        handle = self.port.fork_branch(
            parent_checkpoint.provider_handle,
            compatibility,
            reconstruction,
        )
        return self.create_session(
            compatibility,
            reconstruction,
            provider_handle=handle,
        )

    def _validate_reconstruction(
        self,
        compatibility: ReasonerSessionCompatibility,
        reconstruction: SessionReconstructionBundle,
    ) -> None:
        branch = self.store.get_branch(compatibility.branch_id)
        if (
            reconstruction.compatibility_sha256
            != compatibility.compatibility_sha256
            or reconstruction.world_id != compatibility.world_id
            or reconstruction.branch_id != compatibility.branch_id
            or branch.world_id != compatibility.world_id
            or branch.head_artifact_id != reconstruction.accepted_head_artifact_id
            or branch.generation != reconstruction.generation
            or branch.authority_revision != reconstruction.authority_revision
        ):
            raise StateConflictError(
                "session reconstruction does not match current Python branch authority"
            )
        active = self.store.active_creator_constraints(
            compatibility.world_id, compatibility.branch_id
        )
        expected = tuple(
            ConstraintBinding(value.constraint_id, value.record_sha256)
            for value in active
        )
        if reconstruction.active_constraints != expected:
            raise StateConflictError(
                "session reconstruction omitted or changed active creator constraints"
            )

    def _assert_current_constraints(
        self,
        ledger: ReasonerSessionLedger,
        delta: ContextAuthorityDelta,
    ) -> None:
        current = self.store.active_creator_constraints(
            ledger.compatibility.world_id,
            ledger.compatibility.branch_id,
        )
        expected = tuple(
            ConstraintBinding(value.constraint_id, value.record_sha256)
            for value in current
        )
        if delta.active_constraints != expected:
            raise StateConflictError(
                "context delta omitted or changed active creator constraints"
            )

    def _assert_materialized_evidence_is_accepted_ancestry(
        self,
        ledger: ReasonerSessionLedger,
        delta: ContextAuthorityDelta,
    ) -> None:
        checkpoints = {
            value.checkpoint_id: value
            for value in self.store.reasoner_checkpoints(ledger.session_id)
        }
        ancestor_ids: set[TypedId] = set()
        current_id: TypedId | None = delta.parent_checkpoint_id
        while current_id is not None:
            current = checkpoints[current_id]
            if current.status is not CheckpointStatus.ACCEPTED:
                raise StateConflictError("accepted lineage contains non-accepted context")
            ancestor_ids.add(current_id)
            current_id = current.parent_checkpoint_id
        for evidence in delta.current_evidence:
            if (
                evidence.materialized_checkpoint_id is not None
                and evidence.materialized_checkpoint_id not in ancestor_ids
            ):
                raise StateConflictError(
                    "materialized evidence does not come from accepted ancestry"
                )

    def _constraints_for_review(
        self,
        review,
        *,
        scope: ConstraintScope,
    ) -> tuple[CreatorConstraintRecord, ...]:
        diagnostics = tuple(
            value
            for value in self.store.creator_correction_diagnostics(
                branch_id=review.branch_id
            )
            if value.review_id == review.review_id
        )
        # A bare Decline has no diagnostic and creates no inferred lesson.
        if not diagnostics:
            return ()
        now = _utc_now()
        records: list[CreatorConstraintRecord] = []
        for diagnostic in diagnostics:
            if diagnostic.diagnostic_kind is CorrectionDiagnosticKind.FALSE_POSITIVE:
                # A false-positive label is evaluator feedback, not a negative
                # story/character constraint for later Reasoner turns.
                continue
            instruction = diagnostic.creator_feedback
            records.append(
                CreatorConstraintRecord(
                    schema_version=CreatorConstraintRecord.SCHEMA_VERSION,
                    constraint_id=deterministic_id(
                        IdKind.CREATOR_CONSTRAINT,
                        "cera.creator_constraint_record.v1",
                        (
                            f"{diagnostic.diagnostic_id}|{scope.value}|"
                            f"{diagnostic.creator_feedback_sha256}"
                        ),
                    ),
                    scope=scope,
                    world_id=review.world_id,
                    branch_id=(review.branch_id if scope is ConstraintScope.BRANCH else None),
                    source_review_id=review.review_id,
                    source_diagnostic_id=diagnostic.diagnostic_id,
                    source_candidate_sha256=review.candidate_sha256,
                    owner=diagnostic.likely_owner.value,
                    creator_feedback=diagnostic.creator_feedback,
                    creator_feedback_sha256=diagnostic.creator_feedback_sha256,
                    normalized_instruction=instruction,
                    normalized_instruction_sha256=text_sha256(instruction),
                    status=ConstraintStatus.ACTIVE,
                    superseded_by_constraint_id=None,
                    story_authority=False,
                    character_knowledge=False,
                    created_at=now,
                    updated_at=now,
                )
            )
        return tuple(records)

    def _custody_event(
        self,
        checkpoint: ReasonerSessionCheckpoint,
        event_kind: ProviderThreadCustodyEventKind,
        reason_code: str,
    ) -> ProviderThreadCustodyEvent:
        descriptor = self.port.describe_thread(checkpoint.provider_handle)
        now = _utc_now()
        return ProviderThreadCustodyEvent(
            schema_version=ProviderThreadCustodyEvent.SCHEMA_VERSION,
            event_id=deterministic_id(
                IdKind.SESSION_RECEIPT,
                "cera.provider_thread_custody_event.v1",
                (
                    f"{checkpoint.session_id}|{checkpoint.checkpoint_id}|"
                    f"{event_kind.value}|{canonical_sha256(descriptor)}|"
                    f"{reason_code}|{now}"
                ),
            ),
            session_id=checkpoint.session_id,
            checkpoint_id=checkpoint.checkpoint_id,
            event_kind=event_kind,
            descriptor=descriptor,
            reason_code=reason_code,
            created_at=now,
        )
