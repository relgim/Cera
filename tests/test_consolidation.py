from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import unittest

from cera.consolidation.fake import FakeConsolidationFixture, FakeDerivedConsolidatorPort
from cera.consolidation.models import (
    ConsolidationProposal,
    DerivedConsolidationRequest,
)
from cera.consolidation.validator import ConsolidationValidator
from cera.contracts import (
    AcceptedStoryArtifact,
    Certainty,
    EvidenceAuthority,
    EvidenceRecordType,
    KnowledgeRoute,
    TruthStatus,
    Visibility,
)
from cera.evidence import (
    EvidenceAccessScope,
    EvidenceDocument,
    EvidenceEpistemicClass,
    EvidenceFetchRequest,
    EvidenceRequesterRole,
    EvidenceSearchRequest,
    EvidenceService,
    EvidenceWorldMode,
)
from cera.errors import ContractValidationError, EvidenceServiceError, StateConflictError, TransactionError
from cera.ids import IdKind, TypedId
from cera.evaluation import RealGenesisSandbox
from cera.serialization import canonical_json, text_sha256
from cera.storage import (
    AuthorityRecord,
    CommitMode,
    SourceRecord,
    SQLiteAuthorityStore,
    TurnCommitBundle,
)
import tests.test_genesis_repository as genesis_test_support


ident = genesis_test_support.ident


class FailingConsolidationStore(SQLiteAuthorityStore):
    def _after_consolidation_records_insert(self, connection, bundle) -> None:
        raise RuntimeError("simulated consolidation failure")


class ConsolidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = genesis_test_support.GenesisRepositoryTests("runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        stored, _, _ = self.fixture.install_root()
        self.store = self.fixture.store
        self.world_id = self.fixture.world_id
        self.branch_id = self.fixture.branch_id
        self.alpha = self.fixture.alpha
        self.beta = self.fixture.beta
        self.ted = ident(IdKind.CHARACTER, "ted")
        self.revision_id = stored.receipt.revision_id
        self.event_document = self.commit_event("root", self.branch_id)

    def scope(self, *owners: TypedId) -> EvidenceAccessScope:
        return EvidenceAccessScope(
            requester_role=EvidenceRequesterRole.SYSTEM_REASONER,
            perspective_id=None,
            permitted_private_owner_ids=owners,
            allow_system_private=True,
            allow_audit_history=True,
        )

    def commit_event(
        self,
        suffix: str,
        branch_id: TypedId,
        *,
        owner_id: TypedId | None = None,
        knowledge_owner_ids: tuple[TypedId, ...] | None = None,
        subject_ids: tuple[TypedId, ...] | None = None,
        visibility: Visibility = Visibility.SHARED,
    ) -> EvidenceDocument:
        state = self.store.get_branch(branch_id)
        event_id = ident(IdKind.EVENT, f"phase9-event-{suffix}")
        source_id = ident(IdKind.SOURCE, f"phase9-source-{suffix}")
        event = EvidenceDocument(
            schema_version=EvidenceDocument.SCHEMA_VERSION,
            record_id=event_id,
            record_version=1,
            record_type=EvidenceRecordType.EVENT_FACT,
            epistemic_class=EvidenceEpistemicClass.OBJECTIVE_FACT,
            truth_status=TruthStatus.NONCANONICAL,
            title=f"Synthetic event {suffix}",
            abstract="A provider-free accepted event used to test deferred consolidation.",
            claim="Alpha and Beta experienced a trust-relevant household event.",
            authority=EvidenceAuthority.SYNTHETIC_FIXTURE,
            subject_ids=subject_ids or (self.alpha, self.beta),
            owner_id=owner_id,
            knowledge_owner_ids=(
                knowledge_owner_ids
                if knowledge_owner_ids is not None
                else (self.alpha, self.beta)
            ),
            visibility=visibility,
            knowledge_route=KnowledgeRoute.DIRECT,
            certainty=Certainty.ESTABLISHED,
            content_class="ordinary",
            genesis_revision_id=self.revision_id,
            branch_origin_id=branch_id,
            valid_from_generation=state.generation + 1,
            valid_to_generation=None,
            source_refs=(source_id,),
            supersedes=(),
            tags=("phase9", "trust", suffix),
            expandable_sections=("event",),
            linked_record_ids=(),
            sections_json=canonical_json(
                {
                    "event": {
                        "participants": [str(self.alpha), str(self.beta)],
                        "status": "completed",
                    }
                }
            ),
        )
        source = SourceRecord.from_payload(
            source_id=source_id,
            request_id=ident(IdKind.REQUEST, f"phase9-source-request-{suffix}"),
            branch_id=branch_id,
            payload={"synthetic_event": suffix},
        )
        transaction_id = ident(IdKind.TRANSACTION, f"phase9-story-{suffix}")
        artifact_id = ident(IdKind.ARTIFACT, f"phase9-artifact-{suffix}")
        prose = f"Synthetic accepted event prose {suffix}."
        artifact = AcceptedStoryArtifact(
            schema_version=AcceptedStoryArtifact.SCHEMA_VERSION,
            artifact_id=artifact_id,
            branch_id=branch_id,
            generation_id=ident(IdKind.GENERATION, f"phase9-generation-{suffix}"),
            parent_artifact_id=state.head_artifact_id,
            source_id=source_id,
            decision_id=ident(IdKind.DECISION, f"phase9-story-decision-{suffix}"),
            accepted_prose=prose,
            prose_sha256=text_sha256(prose),
            responding_npc_ids=(self.alpha,),
            realized_beat_ids=(ident(IdKind.BEAT, f"phase9-beat-{suffix}"),),
            validation_receipt_id=ident(IdKind.VALIDATION, f"phase9-story-validation-{suffix}"),
            transaction_id=transaction_id,
            status="accepted",
        )
        authority = AuthorityRecord.from_payload(
            record_id=event_id,
            branch_id=branch_id,
            artifact_id=artifact_id,
            record_type=event.record_type.value,
            payload=event,
        )
        self.store.commit_turn(
            TurnCommitBundle(
                transaction_id=transaction_id,
                idempotency_key=f"phase9-story-{suffix}",
                mode=CommitMode.APPEND,
                branch_id=branch_id,
                expected_generation=state.generation,
                expected_head_artifact_id=state.head_artifact_id,
                source=source,
                artifact=artifact,
                authority_records=(authority,),
                validation_receipt_ids=(artifact.validation_receipt_id,),
            )
        )
        self.store.rebuild_evidence_search_index()
        return event

    def request(
        self,
        suffix: str,
        *,
        branch_id: TypedId | None = None,
        extra_record_ids: tuple[TypedId, ...] = (),
    ) -> DerivedConsolidationRequest:
        branch = branch_id or self.branch_id
        request_id = ident(IdKind.REQUEST, f"phase9-consolidation-{suffix}")
        service = EvidenceService(self.store)
        snapshot = service.open_snapshot(
            request_id=request_id,
            world_id=self.world_id,
            branch_id=branch,
            access_scope=self.scope(self.alpha, self.beta),
            world_mode=EvidenceWorldMode.SYNTHETIC_FIXTURE,
        )
        search = service.search_evidence(
            snapshot,
            EvidenceSearchRequest(
                record_types=(EvidenceRecordType.EVENT_FACT,),
                tags=("phase9",),
                limit=8,
            ),
        )
        event_reference = next(
            value for value in search.references if value.metadata.record_id == self.event_document.record_id
        )
        event_fetch = service.fetch_evidence(
            snapshot,
            EvidenceFetchRequest((event_reference.evidence_id,), ("event",)),
        )
        exact = list(event_fetch.exact_records)
        receipt_ids = [search.receipt.lookup_receipt_id, event_fetch.receipt.lookup_receipt_id]
        for record_id in extra_record_ids:
            record_search = service.search_evidence(
                snapshot,
                EvidenceSearchRequest(entity_ids=(), tags=(), record_types=(
                    self._record_type(record_id),
                ), limit=20),
            )
            reference = next(
                value for value in record_search.references if value.metadata.record_id == record_id
            )
            section = reference.metadata.record_type.value
            record_fetch = service.fetch_evidence(
                snapshot,
                EvidenceFetchRequest(
                    (reference.evidence_id,),
                    (section,),
                    include_superseded_audit=True,
                ),
            )
            exact.extend(record_fetch.exact_records)
            receipt_ids.extend(
                (record_search.receipt.lookup_receipt_id, record_fetch.receipt.lookup_receipt_id)
            )
        return DerivedConsolidationRequest(
            schema_version=DerivedConsolidationRequest.SCHEMA_VERSION,
            request_id=request_id,
            trace_id=ident(IdKind.TRACE, f"phase9-trace-{suffix}"),
            protected_user_id=self.ted,
            snapshot=snapshot,
            exact_evidence=tuple(exact),
            lookup_receipt_ids=tuple(receipt_ids),
            eligible_record_types=(
                EvidenceRecordType.MEMORY,
                EvidenceRecordType.RELATIONSHIP,
                EvidenceRecordType.THREAD,
                EvidenceRecordType.DEVELOPMENT,
            ),
            maximum_records=8,
            hard_boundaries=(
                "No Genesis rewrite.",
                "No automatic diagnosis.",
                "No protected-user private-state authorship.",
                "Every record must cite expanded event evidence.",
            ),
        )

    @staticmethod
    def _record_type(record_id: TypedId) -> EvidenceRecordType:
        return {
            IdKind.MEMORY: EvidenceRecordType.MEMORY,
            IdKind.RELATIONSHIP: EvidenceRecordType.RELATIONSHIP,
            IdKind.THREAD: EvidenceRecordType.THREAD,
            IdKind.DEVELOPMENT: EvidenceRecordType.DEVELOPMENT,
        }[record_id.kind]

    def document(
        self,
        request: DerivedConsolidationRequest,
        record_type: EvidenceRecordType,
        suffix: str,
        *,
        owner: TypedId | None = None,
        subjects: tuple[TypedId, ...] | None = None,
        knowledge_owners: tuple[TypedId, ...] | None = None,
        visibility: Visibility | None = None,
        record_version: int = 1,
        supersedes: tuple[TypedId, ...] = (),
        section_overrides: dict[str, object] | None = None,
        source_refs: tuple[TypedId, ...] | None = None,
        claim: str | None = None,
    ) -> EvidenceDocument:
        event_id = self.event_document.record_id
        owner_id = owner if owner is not None else (
            None if record_type is EvidenceRecordType.THREAD else self.alpha
        )
        if record_type is EvidenceRecordType.MEMORY:
            section = {
                "memory_key": f"memory-{suffix}",
                "objective_event_refs": [str(event_id)],
                "perceived_facts": ["Alpha remembers the event directly."],
                "subjective_interpretations": [
                    {"kind": "fear", "statement": "Alpha feels wary.", "certainty": "feared"}
                ],
                "unknowns": ["Long-term meaning remains unsettled."],
                "retrieval_tags": ["phase9", "trust"],
                "trigger_cues": ["similar household tension"],
                "clinical_diagnosis": "not_assessed",
                "genesis_effect": "none",
                "supersession_reason": None,
            }
            epistemic = EvidenceEpistemicClass.PRIVATE_FEELING
            certainty = Certainty.FEARED
            default_subjects = (owner_id,)
            default_visibility = Visibility.OWNER_PRIVATE
            default_knowledge = (owner_id,)
        elif record_type is EvidenceRecordType.RELATIONSHIP:
            section = {
                "from_id": str(owner_id),
                "to_id": str(self.ted),
                "observations": [
                    {
                        "dimension": "trust",
                        "direction": "decrease",
                        "statement": "Alpha is more cautious around Ted.",
                    }
                ],
                "universal_score": None,
                "genesis_effect": "none",
                "supersession_reason": None,
            }
            epistemic = EvidenceEpistemicClass.VALIDATED_DERIVED
            certainty = Certainty.BELIEVED
            default_subjects = (owner_id, self.ted)
            default_visibility = Visibility.OWNER_PRIVATE
            default_knowledge = (owner_id,)
        elif record_type is EvidenceRecordType.THREAD:
            section = {
                "thread_key": f"thread-{suffix}",
                "status": "open",
                "question": "How will Alpha and Beta repair trust?",
                "resolution": None,
                "participant_ids": [str(self.alpha), str(self.beta)],
                "genesis_effect": "none",
                "supersession_reason": None,
            }
            epistemic = EvidenceEpistemicClass.VALIDATED_DERIVED
            certainty = Certainty.BELIEVED
            default_subjects = (self.alpha, self.beta)
            default_visibility = Visibility.PUBLIC
            default_knowledge = ()
        else:
            section = {
                "owner_id": str(owner_id),
                "kind": "trust_adaptation",
                "tendency": "Alpha checks context before relying on Ted.",
                "strength": "emerging",
                "scope": "around_ted_after_trust_events",
                "first_evidence_refs": [str(event_id)],
                "last_evidence_refs": [str(event_id)],
                "contradiction_evidence_refs": [],
                "clinical_diagnosis": "not_assessed",
                "genesis_effect": "none",
                "supersession_reason": None,
            }
            epistemic = EvidenceEpistemicClass.VALIDATED_DERIVED
            certainty = Certainty.BELIEVED
            default_subjects = (owner_id, self.ted)
            default_visibility = Visibility.OWNER_PRIVATE
            default_knowledge = (owner_id,)
        if section_overrides:
            section.update(section_overrides)
        record_id = ident(
            {
                EvidenceRecordType.MEMORY: IdKind.MEMORY,
                EvidenceRecordType.RELATIONSHIP: IdKind.RELATIONSHIP,
                EvidenceRecordType.THREAD: IdKind.THREAD,
                EvidenceRecordType.DEVELOPMENT: IdKind.DEVELOPMENT,
            }[record_type],
            f"phase9-{suffix}",
        )
        key = record_type.value
        return EvidenceDocument(
            schema_version=EvidenceDocument.SCHEMA_VERSION,
            record_id=record_id,
            record_version=record_version,
            record_type=record_type,
            epistemic_class=epistemic,
            truth_status=TruthStatus.NONCANONICAL,
            title=f"Synthetic {record_type.value} {suffix}",
            abstract=f"Provider-free {record_type.value} consolidation fixture.",
            claim=claim or f"Synthetic {record_type.value} observation for {suffix}.",
            authority=EvidenceAuthority.SYNTHETIC_FIXTURE,
            subject_ids=subjects or default_subjects,
            owner_id=owner_id,
            knowledge_owner_ids=(
                knowledge_owners if knowledge_owners is not None else default_knowledge
            ),
            visibility=visibility or default_visibility,
            knowledge_route=(
                KnowledgeRoute.NOT_APPLICABLE
                if record_type is EvidenceRecordType.THREAD and owner_id is None
                else KnowledgeRoute.INFERRED
            ),
            certainty=certainty,
            content_class="ordinary",
            genesis_revision_id=self.revision_id,
            branch_origin_id=request.snapshot.branch_id,
            valid_from_generation=request.snapshot.generation,
            valid_to_generation=None,
            source_refs=source_refs or (event_id,),
            supersedes=supersedes,
            tags=("phase9", "trust"),
            expandable_sections=(key,),
            linked_record_ids=(),
            sections_json=canonical_json({key: section}),
        )

    def stage(
        self,
        request: DerivedConsolidationRequest,
        records: tuple[EvidenceDocument, ...],
        suffix: str,
    ):
        proposal = ConsolidationProposal(
            schema_version=ConsolidationProposal.SCHEMA_VERSION,
            decision_id=ident(IdKind.DECISION, f"phase9-consolidation-decision-{suffix}"),
            records=records,
            source_evidence_ids=tuple(value.evidence_id for value in request.exact_evidence),
            uncertainties=(),
            no_change_reason=None if records else "No durable derived change is supported.",
            genesis_rewrite_proposed=False,
            clinical_diagnosis_proposed=False,
            accepted_story_change_proposed=False,
        )
        port = FakeDerivedConsolidatorPort(
            FakeConsolidationFixture(f"phase9-fixture-{suffix}", proposal)
        )
        execution = port.consolidate(request)
        staged = ConsolidationValidator(self.store, EvidenceService(self.store)).stage(
            request,
            execution,
            transaction_id=ident(IdKind.TRANSACTION, f"phase9-consolidation-tx-{suffix}"),
            idempotency_key=f"phase9-consolidation-{suffix}",
        )
        return staged, port

    def test_complete_commit_replay_views_privacy_and_snapshot_invalidation(self) -> None:
        request = self.request("complete")
        documents = tuple(
            self.document(request, record_type, record_type.value)
            for record_type in (
                EvidenceRecordType.MEMORY,
                EvidenceRecordType.RELATIONSHIP,
                EvidenceRecordType.THREAD,
                EvidenceRecordType.DEVELOPMENT,
            )
        )
        staged, port = self.stage(request, documents, "complete")
        assert staged.bundle is not None
        before = self.store.get_branch(self.branch_id)
        first = self.store.commit_consolidation(staged.bundle)
        replay = self.store.commit_consolidation(staged.bundle)
        after = self.store.get_branch(self.branch_id)

        self.assertEqual(port.invocation_count, 1)
        self.assertFalse(first.exact_replay)
        self.assertTrue(replay.exact_replay)
        self.assertEqual(first.receipt, replay.receipt)
        self.assertEqual(after.head_artifact_id, before.head_artifact_id)
        self.assertEqual(after.generation, before.generation)
        self.assertEqual(after.authority_revision, before.authority_revision + 1)
        self.assertEqual(first.receipt.story_artifact_writes, 0)
        self.assertEqual(first.receipt.genesis_writes, 0)
        self.assertEqual(self.store.table_count("consolidation_receipts"), 1)
        with self.assertRaises(EvidenceServiceError):
            EvidenceService(self.store).validate_snapshot(request.snapshot)

        views = self.store.rebuild_derived_views(self.branch_id)
        self.assertEqual(len(views.view_keys), 3)
        owner_key = f"owner_summary|{self.alpha}"
        owner_view = self.store.get_derived_view(
            self.branch_id, owner_key, perspective_id=self.alpha
        )
        self.assertIn("phase9-memory", owner_view.payload_json)
        with self.assertRaisesRegex(TransactionError, "owner-private"):
            self.store.get_derived_view(
                self.branch_id, owner_key, perspective_id=self.beta
            )
        public_view = self.store.get_derived_view(self.branch_id, "public_summary")
        self.assertIn("phase9-thread", public_view.payload_json)
        self.assertNotIn("phase9-memory", public_view.payload_json)
        with self.assertRaisesRegex(TransactionError, "system-private"):
            self.store.get_derived_view(self.branch_id, "system_index")

        character_service = EvidenceService(self.store)
        alpha_snapshot = character_service.open_snapshot(
            request_id=ident(IdKind.REQUEST, "phase9-alpha-retrieval"),
            world_id=self.world_id,
            branch_id=self.branch_id,
            access_scope=EvidenceAccessScope(
                requester_role=EvidenceRequesterRole.CHARACTER,
                perspective_id=self.alpha,
                permitted_private_owner_ids=(self.alpha,),
            ),
            world_mode=EvidenceWorldMode.SYNTHETIC_FIXTURE,
        )
        found = character_service.search_evidence(
            alpha_snapshot,
            EvidenceSearchRequest(record_types=(EvidenceRecordType.MEMORY,), limit=8),
        )
        self.assertEqual(
            {value.metadata.record_id for value in found.references},
            {documents[0].record_id},
        )

    def test_invalid_authority_diagnosis_genesis_protected_user_and_event_rules(self) -> None:
        request = self.request("invalid")
        cases = {
            "genesis": self.document(
                request,
                EvidenceRecordType.DEVELOPMENT,
                "bad-genesis",
                section_overrides={"genesis_effect": "rewrite"},
            ),
            "diagnosis": self.document(
                request,
                EvidenceRecordType.MEMORY,
                "bad-diagnosis",
                claim="Alpha is diagnosed with PTSD.",
            ),
            "protected": self.document(
                request,
                EvidenceRecordType.MEMORY,
                "bad-protected",
                owner=self.ted,
                subjects=(self.ted,),
                knowledge_owners=(self.ted,),
            ),
            "no-event": self.document(
                request,
                EvidenceRecordType.THREAD,
                "bad-event",
                source_refs=(request.snapshot.branch_head_artifact_id,),
            ),
            "unexpanded-memory-event": self.document(
                request,
                EvidenceRecordType.MEMORY,
                "bad-unexpanded-memory-event",
                section_overrides={
                    "objective_event_refs": [
                        str(self.event_document.record_id),
                        str(ident(IdKind.EVENT, "phase9-event-not-expanded")),
                    ]
                },
            ),
            "unexpanded-development-event": self.document(
                request,
                EvidenceRecordType.DEVELOPMENT,
                "bad-unexpanded-development-event",
                section_overrides={
                    "last_evidence_refs": [
                        str(self.event_document.record_id),
                        str(ident(IdKind.EVENT, "phase9-event-not-expanded")),
                    ]
                },
            ),
            "strong-once": self.document(
                request,
                EvidenceRecordType.DEVELOPMENT,
                "bad-strong",
                section_overrides={"strength": "strong"},
            ),
        }
        for name, document in cases.items():
            with self.subTest(name=name):
                with self.assertRaises(ContractValidationError):
                    self.stage(request, (document,), f"invalid-{name}")
        self.assertEqual(self.store.get_branch(self.branch_id).authority_revision, 0)
        self.assertEqual(self.store.table_count("consolidation_receipts"), 0)

    def test_private_event_cannot_drive_another_character_record(self) -> None:
        self.event_document = self.commit_event(
            "private-owner",
            self.branch_id,
            owner_id=self.alpha,
            knowledge_owner_ids=(self.alpha,),
            subject_ids=(self.alpha,),
            visibility=Visibility.OWNER_PRIVATE,
        )
        request = self.request("wrong-owner")
        beta_memory = self.document(
            request,
            EvidenceRecordType.MEMORY,
            "beta-private-leak",
            owner=self.beta,
            subjects=(self.beta,),
            knowledge_owners=(self.beta,),
        )
        with self.assertRaisesRegex(ContractValidationError, "does not own"):
            self.stage(request, (beta_memory,), "private-leak")

    def test_exact_dossier_tampering_is_rejected_against_authority(self) -> None:
        request = self.request("tampered-exact")
        exact = request.exact_evidence[0]
        cases = {
            "metadata": replace(
                exact,
                metadata=replace(exact.metadata, knowledge_owner_ids=(self.alpha,)),
            ),
            "sections": replace(
                exact,
                sections_json=canonical_json(
                    {"event": {"participants": [], "status": "completed"}}
                ),
            ),
        }
        for name, altered in cases.items():
            with self.subTest(name=name):
                altered_request = replace(request, exact_evidence=(altered,))
                memory = self.document(
                    altered_request,
                    EvidenceRecordType.MEMORY,
                    f"tampered-exact-{name}",
                )
                with self.assertRaisesRegex(ContractValidationError, "exact consolidation"):
                    self.stage(
                        altered_request,
                        (memory,),
                        f"tampered-exact-{name}",
                    )

    def test_supersession_requires_expansion_and_preserves_audit_history(self) -> None:
        first_request = self.request("supersession-first")
        old = self.document(first_request, EvidenceRecordType.MEMORY, "memory-chain")
        first_stage, _ = self.stage(first_request, (old,), "supersession-first")
        assert first_stage.bundle is not None
        self.store.commit_consolidation(first_stage.bundle)
        self.store.rebuild_derived_views(self.branch_id)

        second_request = self.request(
            "supersession-second", extra_record_ids=(old.record_id,)
        )
        old_evidence_id = next(
            value.evidence_id
            for value in second_request.exact_evidence
            if value.metadata.record_id == old.record_id
        )
        new = self.document(
            second_request,
            EvidenceRecordType.MEMORY,
            "memory-chain-v2",
            record_version=2,
            supersedes=(old.record_id,),
            section_overrides={
                "memory_key": "memory-memory-chain",
                "supersession_reason": "Later event-backed reflection corrected the interpretation.",
            },
        )
        second_stage, _ = self.stage(second_request, (new,), "supersession-second")
        assert second_stage.bundle is not None
        self.store.commit_consolidation(second_stage.bundle)
        self.store.rebuild_derived_views(self.branch_id)

        service = EvidenceService(self.store)
        snapshot = service.open_snapshot(
            request_id=ident(IdKind.REQUEST, "phase9-supersession-audit"),
            world_id=self.world_id,
            branch_id=self.branch_id,
            access_scope=self.scope(self.alpha),
            world_mode=EvidenceWorldMode.SYNTHETIC_FIXTURE,
        )
        current = service.search_evidence(
            snapshot,
            EvidenceSearchRequest(record_types=(EvidenceRecordType.MEMORY,), limit=8),
        )
        self.assertEqual(
            {value.metadata.record_id for value in current.references}, {new.record_id}
        )
        audited = service.fetch_evidence(
            snapshot,
            EvidenceFetchRequest(
                (old_evidence_id,), ("memory",), include_superseded_audit=True
            ),
        )
        self.assertEqual(
            audited.exact_records[0].metadata.supersession_status.value, "superseded"
        )

    def test_fork_same_head_consolidations_do_not_leak_parent_child_or_sibling(self) -> None:
        child = ident(IdKind.BRANCH, "phase9-child")
        sibling = ident(IdKind.BRANCH, "phase9-sibling")
        self.store.fork_branch(self.branch_id, child)
        self.store.fork_branch(self.branch_id, sibling)

        child_request = self.request("child", branch_id=child)
        child_memory = self.document(
            child_request, EvidenceRecordType.MEMORY, "child-memory"
        )
        child_stage, _ = self.stage(child_request, (child_memory,), "child")
        assert child_stage.bundle is not None
        self.store.commit_consolidation(child_stage.bundle)
        self.store.rebuild_evidence_search_index()

        parent_request = self.request("parent-after-fork")
        parent_thread = self.document(
            parent_request, EvidenceRecordType.THREAD, "parent-thread"
        )
        parent_stage, _ = self.stage(parent_request, (parent_thread,), "parent-after-fork")
        assert parent_stage.bundle is not None
        self.store.commit_consolidation(parent_stage.bundle)

        parent_ids = set(self.store.visible_record_ids(self.branch_id))
        child_ids = set(self.store.visible_record_ids(child))
        sibling_ids = set(self.store.visible_record_ids(sibling))
        self.assertIn(parent_thread.record_id, parent_ids)
        self.assertNotIn(child_memory.record_id, parent_ids)
        self.assertIn(child_memory.record_id, child_ids)
        self.assertNotIn(parent_thread.record_id, child_ids)
        self.assertNotIn(child_memory.record_id, sibling_ids)
        self.assertNotIn(parent_thread.record_id, sibling_ids)

    def test_atomic_failure_retains_prepared_bundle_and_restart_commits_once(self) -> None:
        request = self.request("restart")
        memory = self.document(request, EvidenceRecordType.MEMORY, "restart-memory")
        staged, _ = self.stage(request, (memory,), "restart")
        assert staged.bundle is not None
        before = self.store.get_branch(self.branch_id)
        failing = FailingConsolidationStore(
            self.fixture.database_path, allow_synthetic_genesis=True
        )
        failing.prepare_consolidation(staged.bundle)
        with self.assertRaisesRegex(TransactionError, "simulated consolidation failure"):
            failing.finalize_consolidation(staged.bundle)
        self.assertNotIn(memory.record_id, self.store.visible_record_ids(self.branch_id))
        self.assertEqual(
            self.store.get_branch(self.branch_id).authority_revision,
            before.authority_revision,
        )
        pending = self.store.pending_consolidations()
        self.assertEqual(pending, (staged.bundle,))

        restarted = SQLiteAuthorityStore(
            self.fixture.database_path, allow_synthetic_genesis=True
        )
        committed = restarted.finalize_consolidation(pending[0])
        replay = restarted.finalize_consolidation(pending[0])
        self.assertFalse(committed.exact_replay)
        self.assertTrue(replay.exact_replay)
        self.assertEqual(restarted.table_count("consolidation_receipts"), 1)
        self.assertEqual(restarted.table_count("artifacts"), 1)

    def test_competing_prepared_bundle_loses_on_authority_revision(self) -> None:
        request_a = self.request("race-a")
        request_b = self.request("race-b")
        memory_a = self.document(request_a, EvidenceRecordType.MEMORY, "race-a")
        memory_b = self.document(request_b, EvidenceRecordType.MEMORY, "race-b")
        staged_a, _ = self.stage(request_a, (memory_a,), "race-a")
        staged_b, _ = self.stage(request_b, (memory_b,), "race-b")
        assert staged_a.bundle is not None and staged_b.bundle is not None
        self.store.prepare_consolidation(staged_a.bundle)
        self.store.prepare_consolidation(staged_b.bundle)
        self.store.finalize_consolidation(staged_a.bundle)
        with self.assertRaisesRegex(StateConflictError, "stale"):
            self.store.finalize_consolidation(staged_b.bundle)
        self.assertNotIn(memory_b.record_id, self.store.visible_record_ids(self.branch_id))

    def test_no_change_result_is_validated_without_transaction(self) -> None:
        request = self.request("no-change")
        staged, port = self.stage(request, (), "no-change")
        self.assertIsNone(staged.bundle)
        self.assertEqual(staged.validation_receipt.status, "validated_no_change")
        self.assertEqual(port.invocation_count, 1)
        self.assertEqual(self.store.table_count("consolidation_journal"), 0)

    def test_real_genesis_disposable_world_accepts_validated_derived_memory(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        with RealGenesisSandbox.create(project_root) as sandbox:
            hana = sandbox.character_id("Hana")
            ted = sandbox.protected_user_id()
            branch = sandbox.store.get_branch(sandbox.branch_id)
            event_id = ident(IdKind.EVENT, "phase9-real-event")
            source_id = ident(IdKind.SOURCE, "phase9-real-source")
            event = EvidenceDocument(
                schema_version=EvidenceDocument.SCHEMA_VERSION,
                record_id=event_id,
                record_version=1,
                record_type=EvidenceRecordType.EVENT_FACT,
                epistemic_class=EvidenceEpistemicClass.OBJECTIVE_FACT,
                truth_status=TruthStatus.OBJECTIVE,
                title="Accepted Hana trust event",
                abstract="A non-graphic event used for real-Genesis Phase 9 calibration.",
                claim="Hana directly experienced a trust-relevant interaction.",
                authority=EvidenceAuthority.VALIDATED_EVENT,
                subject_ids=(hana, ted),
                owner_id=hana,
                knowledge_owner_ids=(hana,),
                visibility=Visibility.OWNER_PRIVATE,
                knowledge_route=KnowledgeRoute.DIRECT,
                certainty=Certainty.ESTABLISHED,
                content_class="ordinary",
                genesis_revision_id=sandbox.revision_id,
                branch_origin_id=sandbox.branch_id,
                valid_from_generation=1,
                valid_to_generation=None,
                source_refs=(source_id,),
                supersedes=(),
                tags=("phase9-real", "trust"),
                expandable_sections=("event",),
                linked_record_ids=(),
                sections_json=canonical_json({"event": {"status": "completed"}}),
            )
            source = SourceRecord.from_payload(
                source_id=source_id,
                request_id=ident(IdKind.REQUEST, "phase9-real-story-request"),
                branch_id=sandbox.branch_id,
                payload={"phase9_real": True},
            )
            transaction_id = ident(IdKind.TRANSACTION, "phase9-real-story")
            prose = "Hana quietly registered the change in trust."
            artifact = AcceptedStoryArtifact(
                schema_version=AcceptedStoryArtifact.SCHEMA_VERSION,
                artifact_id=ident(IdKind.ARTIFACT, "phase9-real-artifact"),
                branch_id=sandbox.branch_id,
                generation_id=ident(IdKind.GENERATION, "phase9-real-generation"),
                parent_artifact_id=branch.head_artifact_id,
                source_id=source_id,
                decision_id=ident(IdKind.DECISION, "phase9-real-story-decision"),
                accepted_prose=prose,
                prose_sha256=text_sha256(prose),
                responding_npc_ids=(hana,),
                realized_beat_ids=(ident(IdKind.BEAT, "phase9-real-beat"),),
                validation_receipt_id=ident(IdKind.VALIDATION, "phase9-real-story-validation"),
                transaction_id=transaction_id,
                status="accepted",
            )
            sandbox.store.commit_turn(
                TurnCommitBundle(
                    transaction_id=transaction_id,
                    idempotency_key="phase9-real-story",
                    mode=CommitMode.APPEND,
                    branch_id=sandbox.branch_id,
                    expected_generation=branch.generation,
                    expected_head_artifact_id=branch.head_artifact_id,
                    source=source,
                    artifact=artifact,
                    authority_records=(
                        AuthorityRecord.from_payload(
                            record_id=event_id,
                            branch_id=sandbox.branch_id,
                            artifact_id=artifact.artifact_id,
                            record_type=event.record_type.value,
                            payload=event,
                        ),
                    ),
                    validation_receipt_ids=(artifact.validation_receipt_id,),
                )
            )
            sandbox.store.rebuild_evidence_search_index()
            request_id = ident(IdKind.REQUEST, "phase9-real-consolidation")
            service = EvidenceService(sandbox.store)
            snapshot = service.open_snapshot(
                request_id=request_id,
                world_id=sandbox.world_id,
                branch_id=sandbox.branch_id,
                access_scope=sandbox.system_scope(),
                world_mode=EvidenceWorldMode.REAL,
            )
            search = service.search_evidence(
                snapshot,
                EvidenceSearchRequest(tags=("phase9-real",), limit=4),
            )
            event_reference = next(
                value for value in search.references if value.metadata.record_id == event_id
            )
            fetched = service.fetch_evidence(
                snapshot,
                EvidenceFetchRequest((event_reference.evidence_id,), ("event",)),
            )
            request = DerivedConsolidationRequest(
                schema_version=DerivedConsolidationRequest.SCHEMA_VERSION,
                request_id=request_id,
                trace_id=ident(IdKind.TRACE, "phase9-real-trace"),
                protected_user_id=ted,
                snapshot=snapshot,
                exact_evidence=fetched.exact_records,
                lookup_receipt_ids=(
                    search.receipt.lookup_receipt_id,
                    fetched.receipt.lookup_receipt_id,
                ),
                eligible_record_types=(EvidenceRecordType.MEMORY,),
                maximum_records=2,
                hard_boundaries=("No Genesis rewrite.", "No automatic diagnosis."),
            )
            memory = EvidenceDocument(
                schema_version=EvidenceDocument.SCHEMA_VERSION,
                record_id=ident(IdKind.MEMORY, "phase9-real-hana-memory"),
                record_version=1,
                record_type=EvidenceRecordType.MEMORY,
                epistemic_class=EvidenceEpistemicClass.PRIVATE_FEELING,
                truth_status=TruthStatus.CHARACTER_OWNED,
                title="Hana's trust-event memory",
                abstract="Hana privately remembers becoming more cautious.",
                claim="Hana privately feels wary after the accepted interaction.",
                authority=EvidenceAuthority.VALIDATED_DERIVED,
                subject_ids=(hana,),
                owner_id=hana,
                knowledge_owner_ids=(hana,),
                visibility=Visibility.OWNER_PRIVATE,
                knowledge_route=KnowledgeRoute.DIRECT,
                certainty=Certainty.FEARED,
                content_class="ordinary",
                genesis_revision_id=sandbox.revision_id,
                branch_origin_id=sandbox.branch_id,
                valid_from_generation=snapshot.generation,
                valid_to_generation=None,
                source_refs=(event_id,),
                supersedes=(),
                tags=("phase9-real", "trust"),
                expandable_sections=("memory",),
                linked_record_ids=(),
                sections_json=canonical_json(
                    {
                        "memory": {
                            "memory_key": "hana-trust-event",
                            "objective_event_refs": [str(event_id)],
                            "perceived_facts": ["Hana experienced the interaction directly."],
                            "subjective_interpretations": [
                                {
                                    "kind": "fear",
                                    "statement": "Hana feels more cautious.",
                                    "certainty": "feared",
                                }
                            ],
                            "unknowns": ["The lasting effect remains unknown."],
                            "retrieval_tags": ["phase9-real", "trust"],
                            "trigger_cues": ["similar trust tension"],
                            "clinical_diagnosis": "not_assessed",
                            "genesis_effect": "none",
                            "supersession_reason": None,
                        }
                    }
                ),
            )
            proposal = ConsolidationProposal(
                schema_version=ConsolidationProposal.SCHEMA_VERSION,
                decision_id=ident(IdKind.DECISION, "phase9-real-consolidation-decision"),
                records=(memory,),
                source_evidence_ids=(event_reference.evidence_id,),
                uncertainties=("Long-term development is not established.",),
                no_change_reason=None,
                genesis_rewrite_proposed=False,
                clinical_diagnosis_proposed=False,
                accepted_story_change_proposed=False,
            )
            port = FakeDerivedConsolidatorPort(
                FakeConsolidationFixture("phase9-real-fixture", proposal)
            )
            staged = ConsolidationValidator(sandbox.store, service).stage(
                request,
                port.consolidate(request),
                transaction_id=ident(IdKind.TRANSACTION, "phase9-real-consolidation-tx"),
                idempotency_key="phase9-real-consolidation",
            )
            assert staged.bundle is not None
            sandbox.store.commit_consolidation(staged.bundle)
            sandbox.store.rebuild_derived_views(sandbox.branch_id)
            character_service = EvidenceService(sandbox.store)
            character_snapshot = character_service.open_snapshot(
                request_id=ident(IdKind.REQUEST, "phase9-real-hana-retrieval"),
                world_id=sandbox.world_id,
                branch_id=sandbox.branch_id,
                access_scope=sandbox.character_scope("Hana"),
                world_mode=EvidenceWorldMode.REAL,
            )
            result = character_service.search_evidence(
                character_snapshot,
                EvidenceSearchRequest(record_types=(EvidenceRecordType.MEMORY,), limit=4),
            )
            self.assertIn(memory.record_id, {value.metadata.record_id for value in result.references})


if __name__ == "__main__":
    unittest.main()
