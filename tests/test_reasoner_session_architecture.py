from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from cera.contracts import AcceptedStoryArtifact
from cera.creator_review import (
    CorrectionDiagnosticKind,
    CreatorCorrectionDiagnostic,
    CreatorReviewAction,
    CreatorReviewAssessment,
    CreatorReviewRecord,
    CreatorReviewSeverity,
    CreatorReviewState,
    PreparedPublicationPackage,
    PublicationEligibility,
    ReviewIssueOwner,
)
from cera.errors import ContractValidationError, StateConflictError
from cera.evaluation import EvaluationRole
from cera.ids import IdKind, TypedId
from cera.reasoner_session import (
    AcceptedEvidenceMaterialization,
    BranchBoundReasonerSessionCoordinator,
    CheckpointStatus,
    CompactionState,
    ConstraintBinding,
    ConstraintScope,
    ContextAuthorityDelta,
    ContextEvidenceBinding,
    EvidenceDelivery,
    InMemoryReasonerSessionPort,
    ProviderThreadCustodyEventKind,
    ReasonerSessionCompatibility,
    SessionReconstructionBundle,
    SessionRole,
    SessionStatus,
    SessionTurnMode,
    SessionUsageReceiptV2,
    compile_shadow_reasoner_session_prompt,
    reuse_accepted_evidence,
    project_session_usage_receipt,
)
from cera.providers.models import (
    LiveProviderCallReceipt,
    ModelIdentitySource,
    ProviderName,
)
from cera.serialization import canonical_sha256, domain_sha256, text_sha256
from cera.storage import (
    AuthorityRecord,
    CommitMode,
    SourceRecord,
    SQLiteAuthorityStore,
    TurnCommitBundle,
)


NOW = "2026-07-30T23:59:00.000000+00:00"
HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64
HASH_D = "d" * 64


def tid(kind: IdKind, value: str) -> TypedId:
    return TypedId(kind, value)


class BranchBoundReasonerSessionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.store = SQLiteAuthorityStore(
            Path(self.temporary.name) / "authority.sqlite3"
        )
        self.world_id = tid(IdKind.WORLD, "session-world")
        self.branch_id = tid(IdKind.BRANCH, "session-main")
        self.user_id = tid(IdKind.CHARACTER, "ted")
        self.store.create_world(self.world_id)
        self.store.create_root_branch(self.world_id, self.branch_id)
        self.port = InMemoryReasonerSessionPort()
        self.coordinator = BranchBoundReasonerSessionCoordinator(
            self.store, self.port
        )

    def compatibility(
        self,
        branch_id: TypedId | None = None,
        *,
        prompt_version: str = "reasoner-prompt-v24",
    ) -> ReasonerSessionCompatibility:
        return ReasonerSessionCompatibility(
            schema_version=ReasonerSessionCompatibility.SCHEMA_VERSION,
            world_id=self.world_id,
            branch_id=branch_id or self.branch_id,
            role=SessionRole.SCENE_REASONER,
            provider="openai-codex",
            model="gpt-5.6-sol",
            reasoning_effort="medium",
            service_tier="priority",
            transport_version="codex-app-server-0.144.4",
            adapter_version="cera-codex-reasoner-v24",
            prompt_version=prompt_version,
            provider_schema_sha256=HASH_A,
            base_instruction_sha256=HASH_B,
            tool_contract_version="cera-evidence-tools-v6",
            genesis_revision_id=tid(IdKind.GENESIS_REVISION, "hanezawa-v1-2"),
            authority_policy_version="cera-authority-v1",
            privacy_projection_version="cera-privacy-v1",
            protected_user_id=self.user_id,
            autonomy_profile_version="cera-autonomy-v2",
        )

    def reconstruction(
        self,
        compatibility: ReasonerSessionCompatibility,
        *,
        evidence: tuple[ContextEvidenceBinding, ...] = (),
    ) -> SessionReconstructionBundle:
        branch = self.store.get_branch(compatibility.branch_id)
        constraints = tuple(
            ConstraintBinding(value.constraint_id, value.record_sha256)
            for value in self.store.active_creator_constraints(
                compatibility.world_id, compatibility.branch_id
            )
        )
        return SessionReconstructionBundle(
            schema_version=SessionReconstructionBundle.SCHEMA_VERSION,
            bundle_id=tid(
                IdKind.CONTEXT_DELTA,
                f"reconstruct-{compatibility.prompt_version}-{branch.generation}",
            ),
            compatibility_sha256=compatibility.compatibility_sha256,
            world_id=compatibility.world_id,
            branch_id=compatibility.branch_id,
            accepted_head_artifact_id=branch.head_artifact_id,
            generation=branch.generation,
            authority_revision=branch.authority_revision,
            evidence=evidence,
            evidence_snapshot_token=tid(
                IdKind.SNAPSHOT,
                f"snapshot-{compatibility.branch_id.value}-{branch.generation}",
            ),
            active_constraints=constraints,
            accepted_receipt_ids=(),
            authoritative_context_sha256=canonical_sha256(
                {
                    "branch": str(compatibility.branch_id),
                    "generation": branch.generation,
                    "head": str(branch.head_artifact_id),
                }
            ),
            created_at=NOW,
        )

    def delta(
        self,
        session_id: TypedId,
        suffix: str,
        *,
        evidence: tuple[ContextEvidenceBinding, ...] = (),
        include_constraints: bool = True,
        turn_mode: SessionTurnMode = SessionTurnMode.APPEND,
        replaces_artifact_id: TypedId | None = None,
    ) -> ContextAuthorityDelta:
        ledger = self.store.get_reasoner_session(session_id)
        checkpoint = self.store.get_reasoner_checkpoint(
            ledger.accepted_checkpoint_id
        )
        constraints = ()
        if include_constraints:
            constraints = tuple(
                ConstraintBinding(value.constraint_id, value.record_sha256)
                for value in self.store.active_creator_constraints(
                    ledger.compatibility.world_id,
                    ledger.compatibility.branch_id,
                )
            )
        return ContextAuthorityDelta(
            schema_version=ContextAuthorityDelta.SCHEMA_VERSION,
            delta_id=tid(IdKind.CONTEXT_DELTA, f"delta-{suffix}"),
            session_id=session_id,
            parent_checkpoint_id=(
                checkpoint.checkpoint_id
                if turn_mode is SessionTurnMode.APPEND
                else checkpoint.parent_checkpoint_id
            ),
            world_id=ledger.compatibility.world_id,
            branch_id=ledger.compatibility.branch_id,
            request_id=tid(IdKind.REQUEST, f"request-{suffix}"),
            turn_mode=turn_mode,
            replaces_artifact_id=replaces_artifact_id,
            generation=checkpoint.generation,
            accepted_head_artifact_id=checkpoint.accepted_head_artifact_id,
            authority_revision=checkpoint.authority_revision,
            evidence_snapshot_token=tid(IdKind.SNAPSHOT, f"snapshot-{suffix}"),
            current_evidence=evidence,
            revoked_evidence_ids=(),
            active_constraints=constraints,
            source_sha256=HASH_C,
            turn_packet_sha256=HASH_D,
            created_at=NOW,
        )

    def create_session(self):
        compatibility = self.compatibility()
        return self.coordinator.create_session(
            compatibility, self.reconstruction(compatibility)
        )

    def create_review(
        self,
        checkpoint,
        *,
        suffix: str,
        state: CreatorReviewState,
        action: CreatorReviewAction,
        artifact_id: TypedId | None = None,
        feedback: str | None = None,
    ) -> CreatorReviewRecord:
        review_id = tid(IdKind.REVIEW_PACKET, f"review-{suffix}")
        assessment = CreatorReviewAssessment(
            schema_version=CreatorReviewAssessment.SCHEMA_VERSION,
            severity=CreatorReviewSeverity.CONCERN,
            publication_eligibility=PublicationEligibility.ACCEPT_ALLOWED,
            issue_owner=ReviewIssueOwner.REASONER,
            reason_codes=("creator_logic_adjustment",),
            creator_reason="The creator requested a causal adjustment.",
            verifier_status="reviewed",
        )
        package = None
        if artifact_id is not None:
            package = PreparedPublicationPackage(
                schema_version=PreparedPublicationPackage.SCHEMA_VERSION,
                package_id=tid(IdKind.PREPARED_PUBLICATION, f"package-{suffix}"),
                world_id=self.world_id,
                branch_id=checkpoint.branch_id,
                request_id=checkpoint.request_id,
                generation_id=tid(IdKind.GENERATION, f"generation-{suffix}"),
                expected_generation=checkpoint.generation,
                expected_head_artifact_id=checkpoint.accepted_head_artifact_id,
                source_sha256=HASH_C,
                snapshot_token=tid(IdKind.SNAPSHOT, f"review-snapshot-{suffix}"),
                sequence_plan_sha256=HASH_A,
                candidate_sha256=HASH_B,
                accepted_artifact_id=artifact_id,
                accepted_artifact_sha256=HASH_C,
                verifier_receipt_sha256=HASH_D,
                application_request_sha256=HASH_A,
                live_result_sha256=HASH_B,
                idempotency_key_sha256=HASH_C,
                created_at=NOW,
                expires_at="2030-01-01T00:00:00.000000+00:00",
                story_state_committed=False,
            )
        record = CreatorReviewRecord(
            schema_version=CreatorReviewRecord.SCHEMA_VERSION,
            review_id=review_id,
            world_id=self.world_id,
            branch_id=checkpoint.branch_id,
            request_id=checkpoint.request_id,
            generation_id=tid(IdKind.GENERATION, f"generation-{suffix}"),
            expected_generation=checkpoint.generation,
            expected_head_artifact_id=checkpoint.accepted_head_artifact_id,
            source_sha256=HASH_C,
            snapshot_token=tid(IdKind.SNAPSHOT, f"review-snapshot-{suffix}"),
            sequence_plan_sha256=HASH_A,
            sequence_beats=("One creator-reviewed beat.",),
            candidate_sha256=HASH_B,
            candidate_text_sha256=HASH_D,
            candidate_text=None,
            state=state,
            assessment=assessment,
            prepared_package=package,
            application_request_json=None,
            provisional_candidate_json=None,
            live_result_json=None,
            creator_action=action,
            creator_feedback=None,
            creator_feedback_sha256=None,
            created_at=NOW,
            updated_at=NOW,
            resolved_at=NOW,
        )
        self.store.create_creator_review(record)
        if feedback is not None:
            diagnostic = CreatorCorrectionDiagnostic(
                schema_version=CreatorCorrectionDiagnostic.SCHEMA_VERSION,
                diagnostic_id=tid(IdKind.REVIEW_FINDING, f"diagnostic-{suffix}"),
                review_id=review_id,
                branch_id=checkpoint.branch_id,
                action=action,
                diagnostic_kind=CorrectionDiagnosticKind.CAUSAL_LOGIC,
                likely_owner=ReviewIssueOwner.REASONER,
                assessment_sha256=assessment.assessment_sha256,
                reason_codes=assessment.reason_codes,
                creator_feedback=feedback,
                creator_feedback_sha256=text_sha256(feedback),
                status="pending_shared_review",
                created_at=NOW,
            )
            self.store.put_creator_correction_diagnostic(diagnostic)
        return record

    def commit_artifact(
        self,
        suffix: str,
        *,
        mode: CommitMode = CommitMode.APPEND,
        replaces_artifact_id: TypedId | None = None,
    ):
        branch = self.store.get_branch(self.branch_id)
        parent_artifact_id = branch.head_artifact_id
        if mode is CommitMode.REGENERATE:
            if replaces_artifact_id is None:
                raise AssertionError("regeneration fixture requires replaced artifact")
            parent_artifact_id = self.store.get_artifact_parent_id(
                replaces_artifact_id
            )
        source = SourceRecord.from_payload(
            source_id=tid(IdKind.SOURCE, f"source-{suffix}"),
            request_id=tid(IdKind.REQUEST, f"source-request-{suffix}"),
            branch_id=self.branch_id,
            payload={"message": suffix},
        )
        transaction_id = tid(IdKind.TRANSACTION, f"transaction-{suffix}")
        artifact_id = tid(IdKind.ARTIFACT, f"artifact-{suffix}")
        prose = f"Accepted story prose for {suffix}."
        artifact = AcceptedStoryArtifact(
            schema_version=AcceptedStoryArtifact.SCHEMA_VERSION,
            artifact_id=artifact_id,
            branch_id=self.branch_id,
            generation_id=tid(IdKind.GENERATION, f"artifact-generation-{suffix}"),
            parent_artifact_id=parent_artifact_id,
            source_id=source.source_id,
            decision_id=tid(IdKind.DECISION, f"decision-{suffix}"),
            accepted_prose=prose,
            prose_sha256=text_sha256(prose),
            responding_npc_ids=(tid(IdKind.CHARACTER, "sakura"),),
            realized_beat_ids=(tid(IdKind.BEAT, f"beat-{suffix}"),),
            validation_receipt_id=tid(IdKind.VALIDATION, f"validation-{suffix}"),
            transaction_id=transaction_id,
            status="accepted",
        )
        authority = AuthorityRecord.from_payload(
            record_id=tid(IdKind.EVENT, f"event-{suffix}"),
            branch_id=self.branch_id,
            artifact_id=artifact_id,
            record_type="objective_event",
            payload={"accepted": suffix},
        )
        bundle = TurnCommitBundle(
            transaction_id=transaction_id,
            idempotency_key=f"commit-{suffix}",
            mode=mode,
            branch_id=self.branch_id,
            expected_generation=branch.generation,
            expected_head_artifact_id=branch.head_artifact_id,
            source=source,
            artifact=artifact,
            authority_records=(authority,),
            validation_receipt_ids=(artifact.validation_receipt_id,),
            lookup_receipt_ids=(tid(IdKind.LOOKUP_RECEIPT, f"lookup-{suffix}"),),
            provider_receipt_ids=(tid(IdKind.PROVIDER_RECEIPT, f"provider-{suffix}"),),
            replaces_artifact_id=replaces_artifact_id,
        )
        return artifact, self.store.commit_turn(bundle)

    def test_candidate_forks_from_accepted_checkpoint_without_provider_call(self) -> None:
        ledger = self.create_session()
        candidate = self.coordinator.begin_candidate(
            self.delta(ledger.session_id, "first")
        )
        parent = self.store.get_reasoner_checkpoint(ledger.accepted_checkpoint_id)
        self.assertEqual(candidate.parent_checkpoint_id, parent.checkpoint_id)
        self.assertEqual(
            self.port.parent_thread_id(candidate.provider_handle),
            parent.provider_handle.provider_thread_id,
        )
        self.assertEqual(self.port.provider_calls, 0)
        self.assertEqual(
            tuple(
                value.event_kind
                for value in self.store.provider_thread_custody_events(
                    candidate.checkpoint_id
                )
            ),
            (ProviderThreadCustodyEventKind.ALLOCATED,),
        )

    def test_rejected_candidate_stays_off_accepted_lineage_and_feedback_becomes_constraint(self) -> None:
        ledger = self.create_session()
        candidate = self.coordinator.begin_candidate(
            self.delta(ledger.session_id, "rejected")
        )
        review = self.create_review(
            candidate,
            suffix="rejected",
            state=CreatorReviewState.REJECTED,
            action=CreatorReviewAction.CODEX_REPLAN,
            feedback="Do not make Sakura reveal a conclusion she has not yet verified.",
        )
        candidate = self.coordinator.bind_creator_review(
            candidate.checkpoint_id, review.review_id
        )
        receipt = self.coordinator.reject_candidate(candidate.checkpoint_id)
        current = self.store.get_reasoner_session(ledger.session_id)
        self.assertEqual(current.accepted_checkpoint_id, ledger.accepted_checkpoint_id)
        self.assertEqual(
            self.store.get_reasoner_checkpoint(candidate.checkpoint_id).status,
            CheckpointStatus.REJECTED,
        )
        constraints = self.store.active_creator_constraints(
            self.world_id, self.branch_id
        )
        self.assertEqual(receipt.constraint_ids, (constraints[0].constraint_id,))
        self.assertFalse(constraints[0].story_authority)
        self.assertFalse(constraints[0].character_knowledge)
        self.assertEqual(
            tuple(
                value.event_kind
                for value in self.store.provider_thread_custody_events(
                    candidate.checkpoint_id
                )
            ),
            (
                ProviderThreadCustodyEventKind.ALLOCATED,
                ProviderThreadCustodyEventKind.REJECTED_ARCHIVED,
            ),
        )
        self.assertEqual(
            constraints[0].normalized_instruction,
            "Do not make Sakura reveal a conclusion she has not yet verified.",
        )

        with self.assertRaisesRegex(StateConflictError, "constraints"):
            self.coordinator.begin_candidate(
                self.delta(ledger.session_id, "missing-constraint", include_constraints=False)
            )
        replacement = self.coordinator.begin_candidate(
            self.delta(ledger.session_id, "replacement")
        )
        accepted_parent = self.store.get_reasoner_checkpoint(
            ledger.accepted_checkpoint_id
        )
        self.assertEqual(
            self.port.parent_thread_id(replacement.provider_handle),
            accepted_parent.provider_handle.provider_thread_id,
        )
        self.assertNotEqual(
            self.port.parent_thread_id(replacement.provider_handle),
            candidate.provider_handle.provider_thread_id,
        )

    def test_bare_decline_creates_no_inferred_negative_constraint(self) -> None:
        ledger = self.create_session()
        candidate = self.coordinator.begin_candidate(
            self.delta(ledger.session_id, "decline")
        )
        review = self.create_review(
            candidate,
            suffix="decline",
            state=CreatorReviewState.REJECTED,
            action=CreatorReviewAction.DECLINE,
        )
        self.coordinator.bind_creator_review(candidate.checkpoint_id, review.review_id)
        receipt = self.coordinator.reject_candidate(candidate.checkpoint_id)
        self.assertEqual(receipt.constraint_ids, ())
        self.assertEqual(
            self.store.active_creator_constraints(self.world_id, self.branch_id), ()
        )

    def test_provider_leaf_archive_failure_keeps_candidate_open_and_unaccepted(self) -> None:
        ledger = self.create_session()
        candidate = self.coordinator.begin_candidate(
            self.delta(ledger.session_id, "delete-failure")
        )
        review = self.create_review(
            candidate,
            suffix="delete-failure",
            state=CreatorReviewState.REJECTED,
            action=CreatorReviewAction.DECLINE,
        )
        self.coordinator.bind_creator_review(candidate.checkpoint_id, review.review_id)
        with patch.object(
            self.port,
            "mark_rejected",
            side_effect=StateConflictError("thread/archive unavailable"),
        ), self.assertRaisesRegex(StateConflictError, "thread/archive"):
            self.coordinator.reject_candidate(candidate.checkpoint_id)
        self.assertEqual(
            self.store.get_reasoner_checkpoint(candidate.checkpoint_id).status,
            CheckpointStatus.CANDIDATE,
        )
        self.assertEqual(
            self.store.get_reasoner_session(ledger.session_id).accepted_checkpoint_id,
            ledger.accepted_checkpoint_id,
        )
        self.assertIsNone(
            self.store.get_rejected_candidate_session_receipt(
                candidate.checkpoint_id
            )
        )

    def test_branch_constraint_does_not_leak_to_sibling_branch(self) -> None:
        ledger = self.create_session()
        candidate = self.coordinator.begin_candidate(
            self.delta(ledger.session_id, "branch-constraint")
        )
        review = self.create_review(
            candidate,
            suffix="branch-constraint",
            state=CreatorReviewState.REJECTED,
            action=CreatorReviewAction.CODEX_REPLAN,
            feedback="Keep this correction on the current branch only.",
        )
        self.coordinator.bind_creator_review(candidate.checkpoint_id, review.review_id)
        self.coordinator.reject_candidate(candidate.checkpoint_id)

        child_id = tid(IdKind.BRANCH, "constraint-child")
        self.store.fork_branch(self.branch_id, child_id)
        self.assertEqual(
            self.store.active_creator_constraints(self.world_id, child_id), ()
        )
        child_compatibility = self.compatibility(child_id)
        child = self.coordinator.fork_branch_session(
            ledger.session_id,
            child_compatibility,
            self.reconstruction(child_compatibility),
        )
        self.assertEqual(child.compatibility.branch_id, child_id)

    def test_global_constraint_requires_explicit_scope_and_reaches_sibling(self) -> None:
        ledger = self.create_session()
        candidate = self.coordinator.begin_candidate(
            self.delta(ledger.session_id, "global-constraint")
        )
        review = self.create_review(
            candidate,
            suffix="global-constraint",
            state=CreatorReviewState.REJECTED,
            action=CreatorReviewAction.CODEX_REPLAN,
            feedback="Apply this explicit creator correction across branches.",
        )
        self.coordinator.bind_creator_review(candidate.checkpoint_id, review.review_id)
        self.coordinator.reject_candidate(
            candidate.checkpoint_id, scope=ConstraintScope.GLOBAL
        )
        child_id = tid(IdKind.BRANCH, "global-constraint-child")
        self.store.fork_branch(self.branch_id, child_id)
        child_constraints = self.store.active_creator_constraints(
            self.world_id, child_id
        )
        self.assertEqual(len(child_constraints), 1)
        self.assertEqual(child_constraints[0].scope, ConstraintScope.GLOBAL)
        self.assertIsNone(child_constraints[0].branch_id)

    def test_new_creator_feedback_can_supersede_prior_constraint(self) -> None:
        ledger = self.create_session()
        first = self.coordinator.begin_candidate(
            self.delta(ledger.session_id, "constraint-first")
        )
        first_review = self.create_review(
            first,
            suffix="constraint-first",
            state=CreatorReviewState.REJECTED,
            action=CreatorReviewAction.CODEX_REPLAN,
            feedback="First version of the creator correction.",
        )
        self.coordinator.bind_creator_review(first.checkpoint_id, first_review.review_id)
        self.coordinator.reject_candidate(first.checkpoint_id)
        first_constraint = self.store.active_creator_constraints(
            self.world_id, self.branch_id
        )[0]

        second = self.coordinator.begin_candidate(
            self.delta(ledger.session_id, "constraint-second")
        )
        second_review = self.create_review(
            second,
            suffix="constraint-second",
            state=CreatorReviewState.REJECTED,
            action=CreatorReviewAction.CODEX_REPLAN,
            feedback="Second version replaces the earlier creator correction.",
        )
        self.coordinator.bind_creator_review(second.checkpoint_id, second_review.review_id)
        self.coordinator.reject_candidate(
            second.checkpoint_id,
            supersedes=(first_constraint.constraint_id,),
        )
        active = self.store.active_creator_constraints(self.world_id, self.branch_id)
        self.assertEqual(len(active), 1)
        self.assertNotEqual(active[0].constraint_id, first_constraint.constraint_id)
        self.assertEqual(
            active[0].creator_feedback,
            "Second version replaces the earlier creator correction.",
        )

    def test_acceptance_promotes_only_after_python_commit_and_creator_acceptance(self) -> None:
        ledger = self.create_session()
        candidate = self.coordinator.begin_candidate(
            self.delta(ledger.session_id, "accept")
        )
        artifact, commit = self.commit_artifact("accept")
        review = self.create_review(
            candidate,
            suffix="accept",
            state=CreatorReviewState.ACCEPTED,
            action=CreatorReviewAction.ACCEPT,
            artifact_id=artifact.artifact_id,
        )
        self.coordinator.bind_creator_review(candidate.checkpoint_id, review.review_id)
        receipt = self.coordinator.accept_candidate(
            candidate.checkpoint_id,
            artifact_id=artifact.artifact_id,
            accepted_prose_sha256=artifact.prose_sha256,
            accepted_event_state_sha256=domain_sha256(
                "cera.test.accepted_event_state.v1", {"artifact": str(artifact.artifact_id)}
            ),
            commit_sha256=commit.receipt.transaction_sha256,
        )
        current = self.store.get_reasoner_session(ledger.session_id)
        promoted = self.store.get_reasoner_checkpoint(current.accepted_checkpoint_id)
        self.assertEqual(promoted.status, CheckpointStatus.ACCEPTED)
        self.assertEqual(promoted.accepted_head_artifact_id, artifact.artifact_id)
        self.assertEqual(promoted.accepted_receipt_id, receipt.receipt_id)
        self.assertEqual(current.accumulated_turns, 1)
        self.assertEqual(
            tuple(
                value.event_kind
                for value in self.store.provider_thread_custody_events(
                    promoted.checkpoint_id
                )
            ),
            (
                ProviderThreadCustodyEventKind.ALLOCATED,
                ProviderThreadCustodyEventKind.ACCEPTED,
            ),
        )
        self.assertIn(
            ("accepted_receipt", receipt.receipt_sha256),
            self.port.thread_items(promoted.provider_handle),
        )
        self.assertEqual(
            self.store.get_accepted_turn_session_receipt(promoted.checkpoint_id),
            receipt,
        )

    def test_acceptance_before_story_commit_fails_closed(self) -> None:
        ledger = self.create_session()
        candidate = self.coordinator.begin_candidate(
            self.delta(ledger.session_id, "early-accept")
        )
        artifact_id = tid(IdKind.ARTIFACT, "artifact-not-committed")
        review = self.create_review(
            candidate,
            suffix="early-accept",
            state=CreatorReviewState.ACCEPTED,
            action=CreatorReviewAction.ACCEPT,
            artifact_id=artifact_id,
        )
        self.coordinator.bind_creator_review(candidate.checkpoint_id, review.review_id)
        with self.assertRaisesRegex(StateConflictError, "commit"):
            self.coordinator.accept_candidate(
                candidate.checkpoint_id,
                artifact_id=artifact_id,
                accepted_prose_sha256=HASH_A,
                accepted_event_state_sha256=HASH_B,
                commit_sha256=HASH_C,
            )
        self.assertEqual(
            self.store.get_reasoner_session(ledger.session_id).accepted_checkpoint_id,
            ledger.accepted_checkpoint_id,
        )

    def test_regeneration_forks_from_replaced_artifact_parent(self) -> None:
        first_artifact, _first_commit = self.commit_artifact("regen-base")
        compatibility = self.compatibility()
        ledger = self.coordinator.create_session(
            compatibility, self.reconstruction(compatibility)
        )

        normal = self.coordinator.begin_candidate(
            self.delta(ledger.session_id, "regen-replaced")
        )
        replaced_artifact, replaced_commit = self.commit_artifact("regen-replaced")
        normal_review = self.create_review(
            normal,
            suffix="regen-replaced",
            state=CreatorReviewState.ACCEPTED,
            action=CreatorReviewAction.ACCEPT,
            artifact_id=replaced_artifact.artifact_id,
        )
        self.coordinator.bind_creator_review(normal.checkpoint_id, normal_review.review_id)
        self.coordinator.accept_candidate(
            normal.checkpoint_id,
            artifact_id=replaced_artifact.artifact_id,
            accepted_prose_sha256=replaced_artifact.prose_sha256,
            accepted_event_state_sha256=HASH_A,
            commit_sha256=replaced_commit.receipt.transaction_sha256,
        )

        current = self.store.get_reasoner_session(ledger.session_id)
        replaced_checkpoint = self.store.get_reasoner_checkpoint(
            current.accepted_checkpoint_id
        )
        self.assertEqual(
            replaced_checkpoint.accepted_head_artifact_id,
            replaced_artifact.artifact_id,
        )
        regeneration = self.coordinator.begin_candidate(
            self.delta(
                ledger.session_id,
                "regenerated",
                turn_mode=SessionTurnMode.REGENERATE,
                replaces_artifact_id=replaced_artifact.artifact_id,
            )
        )
        self.assertEqual(
            regeneration.parent_checkpoint_id,
            replaced_checkpoint.parent_checkpoint_id,
        )
        base_checkpoint = self.store.get_reasoner_checkpoint(
            regeneration.parent_checkpoint_id
        )
        self.assertEqual(
            base_checkpoint.accepted_head_artifact_id,
            first_artifact.artifact_id,
        )

        regenerated_artifact, regenerated_commit = self.commit_artifact(
            "regenerated",
            mode=CommitMode.REGENERATE,
            replaces_artifact_id=replaced_artifact.artifact_id,
        )
        regeneration_review = self.create_review(
            regeneration,
            suffix="regenerated",
            state=CreatorReviewState.ACCEPTED,
            action=CreatorReviewAction.ACCEPT,
            artifact_id=regenerated_artifact.artifact_id,
        )
        self.coordinator.bind_creator_review(
            regeneration.checkpoint_id, regeneration_review.review_id
        )
        receipt = self.coordinator.accept_candidate(
            regeneration.checkpoint_id,
            artifact_id=regenerated_artifact.artifact_id,
            accepted_prose_sha256=regenerated_artifact.prose_sha256,
            accepted_event_state_sha256=HASH_B,
            commit_sha256=regenerated_commit.receipt.transaction_sha256,
        )
        self.assertEqual(receipt.replaces_artifact_id, replaced_artifact.artifact_id)
        promoted = self.store.get_reasoner_checkpoint(
            self.store.get_reasoner_session(ledger.session_id).accepted_checkpoint_id
        )
        self.assertEqual(promoted.parent_checkpoint_id, base_checkpoint.checkpoint_id)
        self.assertNotEqual(promoted.parent_checkpoint_id, replaced_checkpoint.checkpoint_id)

    def test_failed_candidate_is_invalidated_without_losing_parent(self) -> None:
        ledger = self.create_session()
        candidate = self.coordinator.begin_candidate(
            self.delta(ledger.session_id, "failure")
        )
        self.coordinator.invalidate_candidate(
            candidate.checkpoint_id, "provider_schema_invalid"
        )
        self.assertEqual(
            self.store.get_reasoner_checkpoint(candidate.checkpoint_id).status,
            CheckpointStatus.INVALIDATED,
        )
        self.assertEqual(
            self.store.get_reasoner_session(ledger.session_id).accepted_checkpoint_id,
            ledger.accepted_checkpoint_id,
        )
        self.assertEqual(
            self.store.provider_thread_custody_events(candidate.checkpoint_id)[-1].event_kind,
            ProviderThreadCustodyEventKind.FAILED_ARCHIVED,
        )
        replacement = self.coordinator.begin_candidate(
            self.delta(ledger.session_id, "after-failure")
        )
        self.assertEqual(replacement.parent_checkpoint_id, ledger.accepted_checkpoint_id)

    def test_restart_decodes_durable_session_candidate_and_constraint_hashes(self) -> None:
        ledger = self.create_session()
        candidate = self.coordinator.begin_candidate(
            self.delta(ledger.session_id, "durable-rejection")
        )
        review = self.create_review(
            candidate,
            suffix="durable-rejection",
            state=CreatorReviewState.REJECTED,
            action=CreatorReviewAction.CODEX_REPLAN,
            feedback="Persist this explicit correction without retaining rejected prose.",
        )
        self.coordinator.bind_creator_review(candidate.checkpoint_id, review.review_id)
        receipt = self.coordinator.reject_candidate(candidate.checkpoint_id)

        reopened = SQLiteAuthorityStore(self.store.database_path)
        self.assertEqual(reopened.get_reasoner_session(ledger.session_id), ledger)
        self.assertEqual(
            reopened.get_rejected_candidate_session_receipt(candidate.checkpoint_id),
            receipt,
        )
        constraints = reopened.active_creator_constraints(self.world_id, self.branch_id)
        self.assertEqual(len(constraints), 1)
        self.assertFalse(constraints[0].story_authority)

    def test_restart_rotates_when_provider_handle_cannot_resume(self) -> None:
        ledger = self.create_session()
        accepted = self.store.get_reasoner_checkpoint(ledger.accepted_checkpoint_id)
        self.port.invalidate(accepted.provider_handle, "simulated_restart_loss")
        compatibility = ledger.compatibility
        replacement = self.coordinator.ensure_session(
            compatibility, self.reconstruction(compatibility)
        )
        self.assertNotEqual(replacement.session_id, ledger.session_id)
        self.assertEqual(
            self.store.get_reasoner_session(ledger.session_id).status,
            SessionStatus.ROTATED,
        )
        self.assertEqual(replacement.rotated_from_session_id, ledger.session_id)
        self.assertEqual(
            self.store.provider_thread_custody_events(
                accepted.checkpoint_id
            )[-1].event_kind,
            ProviderThreadCustodyEventKind.MISSING,
        )

    def test_restart_resumes_exact_accepted_checkpoint_and_records_custody(self) -> None:
        ledger = self.create_session()
        accepted = self.store.get_reasoner_checkpoint(ledger.accepted_checkpoint_id)
        resumed = self.coordinator.ensure_session(
            ledger.compatibility,
            self.reconstruction(ledger.compatibility),
        )
        self.assertEqual(resumed.session_id, ledger.session_id)
        self.assertEqual(
            self.store.provider_thread_custody_events(
                accepted.checkpoint_id
            )[-1].event_kind,
            ProviderThreadCustodyEventKind.RESUMED,
        )

    def test_prompt_change_invalidates_and_reconstructs_incompatible_session(self) -> None:
        original = self.create_session()
        changed = self.compatibility(prompt_version="reasoner-prompt-v25")
        replacement = self.coordinator.ensure_session(
            changed, self.reconstruction(changed)
        )
        self.assertNotEqual(replacement.session_id, original.session_id)
        self.assertEqual(
            self.store.get_reasoner_session(original.session_id).status,
            SessionStatus.INVALIDATED,
        )
        self.assertEqual(
            replacement.compatibility.prompt_version, "reasoner-prompt-v25"
        )
        original_checkpoint = self.store.get_reasoner_checkpoint(
            original.accepted_checkpoint_id
        )
        self.assertEqual(
            self.store.provider_thread_custody_events(
                original_checkpoint.checkpoint_id
            )[-1].event_kind,
            ProviderThreadCustodyEventKind.ARCHIVED,
        )

    def test_rotation_is_blocked_while_candidate_is_unresolved(self) -> None:
        ledger = self.create_session()
        self.coordinator.begin_candidate(self.delta(ledger.session_id, "open"))
        with self.assertRaisesRegex(StateConflictError, "open candidate"):
            self.coordinator.rotate_session(
                ledger.session_id,
                self.reconstruction(ledger.compatibility),
                reason="context_pressure",
            )

    def test_branch_fork_uses_parent_accepted_checkpoint_and_new_identity(self) -> None:
        artifact, _commit = self.commit_artifact("fork-base")
        parent_compatibility = self.compatibility()
        parent = self.coordinator.create_session(
            parent_compatibility, self.reconstruction(parent_compatibility)
        )
        child_id = tid(IdKind.BRANCH, "session-child")
        self.store.fork_branch(self.branch_id, child_id)
        child_compatibility = self.compatibility(child_id)
        child = self.coordinator.fork_branch_session(
            parent.session_id,
            child_compatibility,
            self.reconstruction(child_compatibility),
        )
        child_root = self.store.get_reasoner_checkpoint(child.accepted_checkpoint_id)
        parent_root = self.store.get_reasoner_checkpoint(parent.accepted_checkpoint_id)
        self.assertNotEqual(child.session_id, parent.session_id)
        self.assertEqual(child_root.accepted_head_artifact_id, artifact.artifact_id)
        self.assertEqual(
            self.port.parent_thread_id(child_root.provider_handle),
            parent_root.provider_handle.provider_thread_id,
        )

    def test_materialized_reference_from_rejected_checkpoint_is_forbidden(self) -> None:
        ledger = self.create_session()
        candidate = self.coordinator.begin_candidate(
            self.delta(ledger.session_id, "bad-reference-source")
        )
        review = self.create_review(
            candidate,
            suffix="bad-reference-source",
            state=CreatorReviewState.REJECTED,
            action=CreatorReviewAction.DECLINE,
        )
        self.coordinator.bind_creator_review(candidate.checkpoint_id, review.review_id)
        self.coordinator.reject_candidate(candidate.checkpoint_id)
        evidence = ContextEvidenceBinding(
            record_id=tid(IdKind.MEMORY, "memory-private"),
            version=1,
            payload_sha256=HASH_A,
            delivery=EvidenceDelivery.MATERIALIZED_REFERENCE,
            exact_section_ids=("memory.summary",),
            materialized_checkpoint_id=candidate.checkpoint_id,
        )
        with self.assertRaisesRegex(StateConflictError, "accepted ancestry"):
            self.coordinator.begin_candidate(
                self.delta(ledger.session_id, "bad-reference", evidence=(evidence,))
            )

    def test_evidence_is_reused_only_when_hash_version_sections_and_ancestry_match(self) -> None:
        accepted_checkpoint = tid(IdKind.CHECKPOINT, "accepted-materialization")
        rejected_checkpoint = tid(IdKind.CHECKPOINT, "rejected-materialization")
        current = ContextEvidenceBinding(
            record_id=tid(IdKind.MEMORY, "memory-reusable"),
            version=3,
            payload_sha256=HASH_A,
            delivery=EvidenceDelivery.EXACT_INLINE,
            exact_section_ids=("memory.summary", "memory.consequences"),
        )
        exact_prior = AcceptedEvidenceMaterialization(
            record_id=current.record_id,
            version=current.version,
            payload_sha256=current.payload_sha256,
            exact_section_ids=current.exact_section_ids,
            checkpoint_id=accepted_checkpoint,
        )
        reused = reuse_accepted_evidence(
            (current,),
            (exact_prior,),
            accepted_ancestor_ids=frozenset({accepted_checkpoint}),
        )
        self.assertEqual(reused[0].delivery, EvidenceDelivery.MATERIALIZED_REFERENCE)
        self.assertEqual(reused[0].materialized_checkpoint_id, accepted_checkpoint)

        mutations = (
            replace(exact_prior, version=4),
            replace(exact_prior, payload_sha256=HASH_B),
            replace(exact_prior, exact_section_ids=("memory.summary",)),
            replace(exact_prior, checkpoint_id=rejected_checkpoint),
        )
        for prior in mutations:
            with self.subTest(prior=prior):
                planned = reuse_accepted_evidence(
                    (current,),
                    (prior,),
                    accepted_ancestor_ids=frozenset({accepted_checkpoint}),
                )
                self.assertEqual(planned, (current,))

    def test_shadow_prompt_split_is_byte_equivalent_and_keeps_evidence_variable(self) -> None:
        packet = {
            "schema_version": "test.packet.v1",
            "tool_policy": {"evidence_tools_available": False},
            "seed_dossier": {
                "exact_seed_evidence": [
                    {"evidence_id": "memory:test", "source_text": "Exact private evidence."}
                ]
            },
            "source_view": {"units": [{"text": "Current variable user cue."}]},
        }
        provider_schema = {
            "type": "object",
            "properties": {"status": {"type": "string"}},
        }
        compilation = compile_shadow_reasoner_session_prompt(
            packet, provider_schema=provider_schema
        )
        self.assertNotIn("Exact private evidence.", compilation.stable_instructions)
        self.assertIn("Exact private evidence.", compilation.variable_turn_prompt)
        self.assertIn("Current variable user cue.", compilation.variable_turn_prompt)
        self.assertEqual(
            text_sha256(compilation.recombined_prompt),
            compilation.legacy_full_prompt_sha256,
        )
        changed = compile_shadow_reasoner_session_prompt(
            {
                **packet,
                "source_view": {"units": [{"text": "Different cue."}]},
            },
            provider_schema=provider_schema,
        )
        self.assertEqual(
            compilation.stable_instructions_sha256,
            changed.stable_instructions_sha256,
        )
        self.assertNotEqual(
            compilation.variable_turn_prompt_sha256,
            changed.variable_turn_prompt_sha256,
        )

    def test_context_delta_rejects_current_and_revoked_overlap(self) -> None:
        ledger = self.create_session()
        binding = ContextEvidenceBinding(
            record_id=tid(IdKind.MEMORY, "memory-overlap"),
            version=1,
            payload_sha256=HASH_A,
            delivery=EvidenceDelivery.EXACT_INLINE,
            exact_section_ids=("memory.summary",),
        )
        with self.assertRaisesRegex(ContractValidationError, "current and revoked"):
            replace(
                self.delta(ledger.session_id, "overlap", evidence=(binding,)),
                revoked_evidence_ids=(binding.record_id,),
            )

    def test_usage_receipt_preserves_unknowns_and_rejects_bad_cache_math(self) -> None:
        ledger = self.create_session()
        checkpoint = self.store.get_reasoner_checkpoint(ledger.accepted_checkpoint_id)
        receipt = SessionUsageReceiptV2(
            schema_version=SessionUsageReceiptV2.SCHEMA_VERSION,
            receipt_id=tid(IdKind.TELEMETRY_EVENT, "usage-good"),
            session_id=ledger.session_id,
            checkpoint_id=checkpoint.checkpoint_id,
            request_id=None,
            branch_id=self.branch_id,
            provider="openai-codex",
            model="gpt-5.6-sol",
            reasoning_effort="medium",
            compatibility_sha256=ledger.compatibility.compatibility_sha256,
            prompt_version=ledger.compatibility.prompt_version,
            provider_schema_sha256=HASH_A,
            base_instruction_sha256=HASH_B,
            provider_thread_id_sha256=text_sha256(
                checkpoint.provider_handle.provider_thread_id
            ),
            wall_microseconds=1_000,
            queue_microseconds=None,
            provider_microseconds=900,
            ttft_microseconds=None,
            input_tokens=100,
            cached_input_tokens=60,
            cache_write_tokens=None,
            uncached_input_tokens=40,
            output_tokens=20,
            reasoning_tokens=10,
            prompt_bytes=200,
            packet_bytes=100,
            schema_bytes=50,
            tool_definition_bytes=0,
            session_age_turns=0,
            accumulated_turns=0,
            compaction_state=CompactionState.UNKNOWN,
            unsupported_fields=("cache_write_tokens", "ttft_microseconds"),
            raw_prompt_retained=False,
            raw_output_retained=False,
            created_at=NOW,
        )
        self.store.put_session_usage_receipt(receipt)
        self.assertEqual(self.store.table_count("reasoner_session_usage_receipts"), 1)
        self.assertEqual(
            self.store.reasoner_session_usage_receipts(ledger.session_id),
            (receipt,),
        )
        with self.assertRaisesRegex(ContractValidationError, "inconsistent"):
            replace(
                receipt,
                receipt_id=tid(IdKind.TELEMETRY_EVENT, "usage-bad"),
                uncached_input_tokens=41,
            )

    def test_compatibility_hash_is_stable_and_changes_for_each_versioned_dimension(self) -> None:
        baseline = self.compatibility()
        self.assertEqual(
            baseline.compatibility_sha256,
            self.compatibility().compatibility_sha256,
        )
        mutations = (
            replace(baseline, model="gpt-5.6-sol-next"),
            replace(baseline, reasoning_effort="high"),
            replace(baseline, prompt_version="reasoner-prompt-v25"),
            replace(baseline, provider_schema_sha256=HASH_C),
            replace(baseline, base_instruction_sha256=HASH_D),
            replace(baseline, tool_contract_version="cera-evidence-tools-v7"),
            replace(baseline, privacy_projection_version="cera-privacy-v2"),
        )
        self.assertEqual(
            len({value.compatibility_sha256 for value in mutations}),
            len(mutations),
        )
        self.assertNotIn(
            baseline.compatibility_sha256,
            {value.compatibility_sha256 for value in mutations},
        )

    def test_usage_receipt_cannot_retain_prompt_or_output(self) -> None:
        ledger = self.create_session()
        checkpoint = self.store.get_reasoner_checkpoint(ledger.accepted_checkpoint_id)
        with self.assertRaisesRegex(ContractValidationError, "cannot retain content"):
            SessionUsageReceiptV2(
                schema_version=SessionUsageReceiptV2.SCHEMA_VERSION,
                receipt_id=tid(IdKind.TELEMETRY_EVENT, "usage-private"),
                session_id=ledger.session_id,
                checkpoint_id=checkpoint.checkpoint_id,
                request_id=None,
                branch_id=self.branch_id,
                provider="openai-codex",
                model="gpt-5.6-sol",
                reasoning_effort="medium",
                compatibility_sha256=ledger.compatibility.compatibility_sha256,
                prompt_version=ledger.compatibility.prompt_version,
                provider_schema_sha256=HASH_A,
                base_instruction_sha256=HASH_B,
                provider_thread_id_sha256=HASH_C,
                wall_microseconds=None,
                queue_microseconds=None,
                provider_microseconds=None,
                ttft_microseconds=None,
                input_tokens=None,
                cached_input_tokens=None,
                cache_write_tokens=None,
                uncached_input_tokens=None,
                output_tokens=None,
                reasoning_tokens=None,
                prompt_bytes=0,
                packet_bytes=0,
                schema_bytes=0,
                tool_definition_bytes=0,
                session_age_turns=0,
                accumulated_turns=0,
                compaction_state=CompactionState.UNKNOWN,
                unsupported_fields=(),
                raw_prompt_retained=True,
                raw_output_retained=False,
                created_at=NOW,
            )

    def test_existing_provider_receipt_projects_without_inventing_missing_metrics(self) -> None:
        ledger = self.create_session()
        checkpoint = self.coordinator.begin_candidate(
            self.delta(ledger.session_id, "usage-projection")
        )
        provider = LiveProviderCallReceipt(
            schema_version=LiveProviderCallReceipt.SCHEMA_VERSION,
            provider_receipt_id=tid(IdKind.PROVIDER_RECEIPT, "usage-provider"),
            route_sha256=HASH_A,
            provider=ProviderName.OPENAI_CODEX,
            role=EvaluationRole.SCENE_REASONER,
            requested_model="gpt-5.6-sol",
            returned_model="gpt-5.6-sol",
            model_revision="provider-unverified",
            model_identity_source=ModelIdentitySource.EXPLICIT_REQUEST,
            model_identity_verified=False,
            request_sha256=HASH_B,
            output_sha256=HASH_C,
            provider_request_id_sha256=HASH_D,
            system_fingerprint_sha256=None,
            duration_ms=12_345,
            input_tokens=44_308,
            cached_input_tokens=33_536,
            output_tokens=421,
            reasoning_output_tokens=41,
            cost_microusd=0,
            cost_is_estimate=False,
            quota_metered=True,
            external_provider_calls=1,
            automatic_retry_count=0,
            story_authority_writes=0,
            retains_raw_source=False,
            retains_story_prose=False,
            retains_private_evidence=False,
            retains_prompt=False,
            retains_secret=False,
        )
        usage = project_session_usage_receipt(
            ledger=ledger,
            checkpoint=checkpoint,
            provider_receipt=provider,
            prompt_bytes=38_109,
            packet_bytes=21_754,
            schema_bytes=18_546,
            tool_definition_bytes=0,
            wall_microseconds=17_384_000,
            created_at=NOW,
        )
        self.assertEqual(usage.uncached_input_tokens, 10_772)
        self.assertEqual(
            usage.unsupported_fields,
            ("queue_microseconds", "ttft_microseconds", "cache_write_tokens"),
        )
        self.assertFalse(usage.raw_prompt_retained)
        self.assertFalse(usage.raw_output_retained)


if __name__ == "__main__":
    unittest.main()
