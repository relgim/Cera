from __future__ import annotations

from dataclasses import replace
from contextlib import closing
import sqlite3
import unittest

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
    CharacterSectionsRequest,
    ContinuityRequest,
    EvidenceAccessScope,
    EvidenceDocument,
    EvidenceEpistemicClass,
    EvidenceFetchRequest,
    EvidenceLimits,
    EvidenceRequesterRole,
    EvidenceSearchRequest,
    EvidenceService,
    EvidenceWorldMode,
)
from cera.errors import ErrorCode, EvidenceServiceError
from cera.ids import IdKind, TypedId
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


class EvidenceServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = genesis_test_support.GenesisRepositoryTests("runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        stored, authorization, _ = self.fixture.install_root()
        self.store = self.fixture.store
        self.database_path = self.fixture.database_path
        self.world_id = self.fixture.world_id
        self.branch_id = self.fixture.branch_id
        self.alpha = self.fixture.alpha
        self.beta = self.fixture.beta
        self.revision_id = stored.receipt.revision_id
        self.authorization_id = authorization.authorization_id
        self.store.rebuild_evidence_search_index()

    def scope(
        self,
        character: TypedId | None = None,
        *,
        audit: bool = False,
        all_private: bool = False,
    ) -> EvidenceAccessScope:
        if character is not None:
            return EvidenceAccessScope(
                requester_role=EvidenceRequesterRole.CHARACTER,
                perspective_id=character,
                permitted_private_owner_ids=(character,),
            )
        return EvidenceAccessScope(
            requester_role=EvidenceRequesterRole.SYSTEM_REASONER,
            perspective_id=None,
            permitted_private_owner_ids=(self.alpha, self.beta) if all_private else (),
            allow_system_private=True,
            allow_audit_history=audit,
        )

    def snapshot(
        self,
        scope: EvidenceAccessScope | None = None,
        *,
        branch_id: TypedId | None = None,
        world_id: TypedId | None = None,
        service: EvidenceService | None = None,
        request_id: TypedId | None = None,
    ):
        return (service or EvidenceService(self.store)).open_snapshot(
            request_id=request_id or ident(IdKind.REQUEST, "evidence-snapshot"),
            world_id=world_id or self.world_id,
            branch_id=branch_id or self.branch_id,
            access_scope=scope or self.scope(),
            world_mode=EvidenceWorldMode.SYNTHETIC_FIXTURE,
        )

    def document(
        self,
        suffix: str,
        *,
        branch_id: TypedId,
        generation: int,
        claim: str | None = None,
        owner: TypedId | None = None,
        visibility: Visibility = Visibility.PUBLIC,
        knowledge_owners: tuple[TypedId, ...] = (),
        supersedes: tuple[TypedId, ...] = (),
        linked: tuple[TypedId, ...] = (),
        tags: tuple[str, ...] = ("thread",),
        record_type: EvidenceRecordType = EvidenceRecordType.EVENT_FACT,
        epistemic: EvidenceEpistemicClass = EvidenceEpistemicClass.OBJECTIVE_FACT,
    ) -> EvidenceDocument:
        record_id = ident(IdKind.RECORD, f"dynamic-{suffix}")
        text = claim or f"Synthetic dynamic event {suffix}."
        return EvidenceDocument(
            schema_version=EvidenceDocument.SCHEMA_VERSION,
            record_id=record_id,
            record_version=1,
            record_type=record_type,
            epistemic_class=epistemic,
            truth_status=TruthStatus.NONCANONICAL,
            title=f"Synthetic {suffix}",
            abstract=f"Noncanonical {record_type.value} reference for {suffix}",
            claim=text,
            authority=EvidenceAuthority.SYNTHETIC_FIXTURE,
            subject_ids=((owner,) if owner is not None else (self.alpha,)),
            owner_id=owner,
            knowledge_owner_ids=knowledge_owners,
            visibility=visibility,
            knowledge_route=KnowledgeRoute.DIRECT,
            certainty=(
                Certainty.BELIEVED
                if epistemic in {
                    EvidenceEpistemicClass.PRIVATE_BELIEF,
                    EvidenceEpistemicClass.CONSCIOUS_BELIEF,
                }
                else Certainty.SUSPECTED
                if epistemic is EvidenceEpistemicClass.ALLEGATION
                else Certainty.ESTABLISHED
            ),
            content_class="ordinary",
            genesis_revision_id=self.revision_id,
            branch_origin_id=branch_id,
            valid_from_generation=generation,
            valid_to_generation=None,
            source_refs=(ident(IdKind.SOURCE, f"dynamic-source-{suffix}"),),
            supersedes=supersedes,
            tags=tags,
            expandable_sections=("claim", "state"),
            linked_record_ids=linked,
            sections_json=canonical_json(
                {"claim": text, "state": {"suffix": suffix, "status": "synthetic"}}
            ),
        )

    def commit_document(
        self,
        document: EvidenceDocument,
        *,
        suffix: str,
        branch_id: TypedId | None = None,
    ) -> TurnCommitBundle:
        branch = branch_id or document.branch_origin_id
        assert branch is not None
        state = self.store.get_branch(branch)
        source = SourceRecord.from_payload(
            source_id=document.source_refs[0],
            request_id=ident(IdKind.REQUEST, f"dynamic-request-{suffix}"),
            branch_id=branch,
            payload={"synthetic": suffix},
        )
        transaction_id = ident(IdKind.TRANSACTION, f"dynamic-transaction-{suffix}")
        artifact_id = ident(IdKind.ARTIFACT, f"dynamic-artifact-{suffix}")
        prose = f"Synthetic accepted prose {suffix}."
        artifact = AcceptedStoryArtifact(
            schema_version=AcceptedStoryArtifact.SCHEMA_VERSION,
            artifact_id=artifact_id,
            branch_id=branch,
            generation_id=ident(IdKind.GENERATION, f"dynamic-generation-{suffix}"),
            parent_artifact_id=state.head_artifact_id,
            source_id=source.source_id,
            decision_id=ident(IdKind.DECISION, f"dynamic-decision-{suffix}"),
            accepted_prose=prose,
            prose_sha256=text_sha256(prose),
            responding_npc_ids=(self.alpha,),
            realized_beat_ids=(ident(IdKind.BEAT, f"dynamic-beat-{suffix}"),),
            validation_receipt_id=ident(IdKind.VALIDATION, f"dynamic-validation-{suffix}"),
            transaction_id=transaction_id,
            status="accepted",
        )
        authority = AuthorityRecord.from_payload(
            record_id=document.record_id,
            branch_id=branch,
            artifact_id=artifact_id,
            record_type=document.record_type.value,
            payload=document,
            supersedes=document.supersedes,
        )
        bundle = TurnCommitBundle(
            transaction_id=transaction_id,
            idempotency_key=f"dynamic-{suffix}",
            mode=CommitMode.APPEND,
            branch_id=branch,
            expected_generation=state.generation,
            expected_head_artifact_id=state.head_artifact_id,
            source=source,
            artifact=artifact,
            authority_records=(authority,),
            validation_receipt_ids=(artifact.validation_receipt_id,),
        )
        self.store.commit_turn(bundle)
        self.store.rebuild_evidence_search_index()
        return bundle

    def test_perspective_filters_search_and_exact_fetch_rechecks_privacy(self) -> None:
        service = EvidenceService(self.store)
        alpha_snapshot = self.snapshot(self.scope(self.alpha), service=service)
        beta_snapshot = self.snapshot(self.scope(self.beta), service=service)
        alpha = service.search_evidence(
            alpha_snapshot, EvidenceSearchRequest(terms=("privately",), limit=10)
        )
        beta = service.search_evidence(
            beta_snapshot, EvidenceSearchRequest(terms=("privately",), limit=10)
        )
        alpha_ids = {item.metadata.record_id for item in alpha.references}
        beta_ids = {item.metadata.record_id for item in beta.references}
        self.assertIn(ident(IdKind.RECORD, "record-alpha-private"), alpha_ids)
        self.assertNotIn(ident(IdKind.RECORD, "record-beta-private"), alpha_ids)
        self.assertIn(ident(IdKind.RECORD, "record-beta-private"), beta_ids)
        private_ref = next(
            item for item in alpha.references if item.metadata.record_id.value == "record-alpha-private"
        )
        with self.assertRaises(EvidenceServiceError) as denied:
            service.fetch_evidence(
                beta_snapshot,
                EvidenceFetchRequest((private_ref.evidence_id,), ("claim",)),
            )
        self.assertIs(denied.exception.code, ErrorCode.EVIDENCE_ACCESS_DENIED)

    def test_search_reference_is_compact_but_exact_fetch_returns_authorized_text(self) -> None:
        service = EvidenceService(self.store)
        snapshot = self.snapshot(self.scope(self.alpha), service=service)
        batch = service.search_evidence(
            snapshot, EvidenceSearchRequest(terms=("uneasy",), limit=5)
        )
        self.assertEqual(len(batch.references), 1)
        reference = batch.references[0]
        self.assertNotIn("privately feels uneasy", reference.abstract)
        exact = service.fetch_evidence(
            snapshot,
            EvidenceFetchRequest((reference.evidence_id,), ("claim", "provenance")),
        )
        self.assertIn("privately feels uneasy", exact.exact_records[0].sections_json)
        self.assertEqual(exact.receipt.authoritative_store_writes, 0)

    def test_linked_continuity_rechecks_authorization(self) -> None:
        private = self.document(
            "beta-linked-private",
            branch_id=self.branch_id,
            generation=1,
            owner=self.beta,
            visibility=Visibility.OWNER_PRIVATE,
            knowledge_owners=(self.beta,),
            epistemic=EvidenceEpistemicClass.PRIVATE_BELIEF,
        )
        self.commit_document(private, suffix="beta-linked-private")
        public = self.document(
            "public-root",
            branch_id=self.branch_id,
            generation=2,
            linked=(private.record_id,),
        )
        self.commit_document(public, suffix="public-root")
        service = EvidenceService(self.store)
        snapshot = self.snapshot(self.scope(self.alpha), service=service)
        hit = service.search_evidence(
            snapshot, EvidenceSearchRequest(terms=("public-root",), limit=1)
        ).references[0]
        with self.assertRaises(EvidenceServiceError) as denied:
            service.get_continuity(
                snapshot,
                ContinuityRequest((hit.evidence_id,), ("state",), maximum_depth=2),
            )
        self.assertIs(denied.exception.code, ErrorCode.EVIDENCE_ACCESS_DENIED)

    def test_snapshot_rejects_after_branch_advance(self) -> None:
        service = EvidenceService(self.store)
        snapshot = self.snapshot(self.scope(self.alpha), service=service)
        document = self.document(
            "advance",
            branch_id=self.branch_id,
            generation=1,
        )
        self.commit_document(document, suffix="advance")
        with self.assertRaises(EvidenceServiceError) as stale:
            service.search_evidence(
                snapshot, EvidenceSearchRequest(terms=("alpha",), limit=1)
            )
        self.assertIs(stale.exception.code, ErrorCode.EVIDENCE_SNAPSHOT_STALE)

    def test_fork_inherits_only_fork_lineage_and_child_supersession_isolated(self) -> None:
        old = self.document(
            "old-thread",
            branch_id=self.branch_id,
            generation=1,
            claim="Synthetic old thread state.",
        )
        root_commit = self.commit_document(old, suffix="old-thread")
        child = ident(IdKind.BRANCH, "synthetic-child")
        sibling = ident(IdKind.BRANCH, "synthetic-sibling")
        self.store.fork_branch(self.branch_id, child)
        self.store.fork_branch(self.branch_id, sibling)
        new = self.document(
            "new-thread",
            branch_id=child,
            generation=1,
            claim="Synthetic child-only corrected thread state.",
            supersedes=(old.record_id,),
        )
        self.commit_document(new, suffix="new-thread", branch_id=child)
        later_parent = self.document(
            "later-parent",
            branch_id=self.branch_id,
            generation=2,
            claim="Synthetic parent event after the fork.",
        )
        self.commit_document(later_parent, suffix="later-parent")
        service = EvidenceService(self.store)
        system = self.scope(audit=True, all_private=True)

        def ids(branch_id: TypedId) -> set[TypedId]:
            snap = self.snapshot(system, branch_id=branch_id, service=service)
            return {
                item.metadata.record_id
                for item in service.search_evidence(
                    snap, EvidenceSearchRequest(tags=("thread",), limit=20)
                ).references
            }

        child_ids = ids(child)
        sibling_ids = ids(sibling)
        main_ids = ids(self.branch_id)
        self.assertIn(new.record_id, child_ids)
        self.assertNotIn(old.record_id, child_ids)
        self.assertIn(old.record_id, sibling_ids)
        self.assertNotIn(new.record_id, sibling_ids)
        self.assertNotIn(later_parent.record_id, sibling_ids)
        self.assertIn(later_parent.record_id, main_ids)
        self.assertEqual(root_commit.artifact.artifact_id, self.store.get_branch(sibling).head_artifact_id)

    def test_superseded_record_is_audit_fetchable_only(self) -> None:
        old = self.document("audit-old", branch_id=self.branch_id, generation=1)
        self.commit_document(old, suffix="audit-old")
        new = self.document(
            "audit-new",
            branch_id=self.branch_id,
            generation=2,
            supersedes=(old.record_id,),
        )
        self.commit_document(new, suffix="audit-new")
        service = EvidenceService(self.store)
        snapshot = self.snapshot(self.scope(audit=True, all_private=True), service=service)
        old_evidence_id = service._evidence_id(snapshot, old.record_id)
        fetched = service.fetch_evidence(
            snapshot,
            EvidenceFetchRequest(
                (old_evidence_id,), ("claim",), include_superseded_audit=True
            ),
        )
        self.assertEqual(
            fetched.exact_records[0].metadata.supersession_status.value,
            "superseded",
        )
        ordinary = self.snapshot(self.scope(all_private=True), service=service)
        with self.assertRaises(EvidenceServiceError):
            service.fetch_evidence(
                ordinary,
                EvidenceFetchRequest(
                    (old_evidence_id,), ("claim",), include_superseded_audit=True
                ),
            )

    def test_epistemic_classes_and_explicit_unknown_are_not_flattened(self) -> None:
        allegation = self.document(
            "alpha-allegation",
            branch_id=self.branch_id,
            generation=1,
            claim="Alpha reports a synthetic allegation without establishing it.",
            owner=self.alpha,
            visibility=Visibility.OWNER_PRIVATE,
            knowledge_owners=(self.alpha,),
            epistemic=EvidenceEpistemicClass.ALLEGATION,
            tags=("allegation",),
        )
        self.commit_document(allegation, suffix="alpha-allegation")
        service = EvidenceService(self.store)
        snapshot = self.snapshot(self.scope(all_private=True), service=service)
        private = service.search_evidence(
            snapshot, EvidenceSearchRequest(terms=("privately",), limit=10)
        )
        classes = {item.metadata.epistemic_class for item in private.references}
        self.assertIn(EvidenceEpistemicClass.PRIVATE_FEELING, classes)
        self.assertIn(EvidenceEpistemicClass.PRIVATE_BELIEF, classes)
        allegation_hit = service.search_evidence(
            snapshot, EvidenceSearchRequest(tags=("allegation",), limit=1)
        ).references[0]
        self.assertIs(
            allegation_hit.metadata.epistemic_class,
            EvidenceEpistemicClass.ALLEGATION,
        )
        self.assertIs(allegation_hit.metadata.certainty, Certainty.SUSPECTED)
        unknown = service.search_evidence(
            snapshot, EvidenceSearchRequest(terms=("objective", "unknown"), limit=2)
        ).references[0]
        self.assertIs(
            unknown.metadata.epistemic_class,
            EvidenceEpistemicClass.UNRESOLVED_QUESTION,
        )
        self.assertIs(unknown.metadata.certainty, Certainty.UNKNOWN)
        self.assertIs(unknown.metadata.truth_status, TruthStatus.NONCANONICAL)

    def test_genesis_revision_or_world_binding_tamper_rejects(self) -> None:
        service = EvidenceService(self.store)
        snapshot = self.snapshot(service=service)
        tampered = replace(
            snapshot,
            genesis_revision_id=ident(IdKind.GENESIS_REVISION, "wrong-revision"),
        )
        with self.assertRaises(EvidenceServiceError) as stale:
            service.search_evidence(
                tampered, EvidenceSearchRequest(terms=("alpha",), limit=1)
            )
        self.assertIs(stale.exception.code, ErrorCode.EVIDENCE_SNAPSHOT_STALE)

    def test_cross_world_evidence_reference_is_rejected(self) -> None:
        second_world = ident(IdKind.WORLD, "synthetic-world-two")
        second_branch = ident(IdKind.BRANCH, "synthetic-branch-two")
        self.store.create_world(second_world)
        self.store.create_root_branch(second_world, second_branch)
        self.store.bind_world_to_genesis(
            second_world, self.revision_id, self.authorization_id
        )
        service = EvidenceService(self.store)
        first = self.snapshot(self.scope(self.alpha), service=service)
        first_ref = service.search_evidence(
            first, EvidenceSearchRequest(terms=("adult",), limit=1)
        ).references[0]
        second = self.snapshot(
            self.scope(self.alpha),
            world_id=second_world,
            branch_id=second_branch,
            service=service,
        )
        with self.assertRaises(EvidenceServiceError) as denied:
            service.fetch_evidence(
                second,
                EvidenceFetchRequest((first_ref.evidence_id,), ("claim",)),
            )
        self.assertIs(denied.exception.code, ErrorCode.EVIDENCE_ACCESS_DENIED)

    def test_result_byte_count_and_followup_search_limits_are_deterministic(self) -> None:
        service = EvidenceService(
            self.store,
            limits=EvidenceLimits(
                maximum_query_characters=20,
                maximum_search_results=5,
                maximum_fetched_records=5,
                maximum_traversal_depth=2,
                maximum_response_bytes=200,
                maximum_followup_searches=1,
                maximum_snapshot_evidence_bytes=200,
            ),
        )
        snapshot = self.snapshot(self.scope(all_private=True), service=service)
        first = service.search_evidence(
            snapshot, EvidenceSearchRequest(terms=("alpha",), limit=5)
        )
        self.assertTrue(first.receipt.truncated)
        with self.assertRaises(EvidenceServiceError) as limited:
            service.search_evidence(
                snapshot, EvidenceSearchRequest(terms=("beta",), limit=1)
            )
        self.assertIs(limited.exception.code, ErrorCode.EVIDENCE_LIMIT_EXCEEDED)

    def test_index_rebuild_is_equivalent_and_corruption_never_leaks(self) -> None:
        service = EvidenceService(self.store)
        snapshot = self.snapshot(self.scope(self.alpha), service=service)
        before = service.search_evidence(
            snapshot, EvidenceSearchRequest(terms=("uneasy",), limit=5)
        ).references
        self.store.rebuild_evidence_search_index()
        restarted = EvidenceService(
            SQLiteAuthorityStore(self.database_path, allow_synthetic_genesis=True)
        )
        restarted_snapshot = restarted.open_snapshot(
            request_id=ident(IdKind.REQUEST, "evidence-snapshot"),
            world_id=self.world_id,
            branch_id=self.branch_id,
            access_scope=self.scope(self.alpha),
            world_mode=EvidenceWorldMode.SYNTHETIC_FIXTURE,
        )
        after = restarted.search_evidence(
            restarted_snapshot, EvidenceSearchRequest(terms=("uneasy",), limit=5)
        ).references
        self.assertEqual(before, after)
        with closing(sqlite3.connect(self.database_path)) as connection:
            connection.execute(
                "INSERT INTO evidence_search_fts("
                "record_key, source_kind, record_id, record_sha256, searchable_text"
                ") VALUES (?, ?, ?, ?, ?)",
                ("authority|record:fake", "authority", "record:fake", "0" * 64, "uneasy"),
            )
            connection.commit()
        with self.assertRaises(EvidenceServiceError) as corrupt:
            restarted.search_evidence(
                restarted_snapshot, EvidenceSearchRequest(terms=("uneasy",), limit=5)
            )
        self.assertIs(corrupt.exception.code, ErrorCode.EVIDENCE_INDEX_INVALID)

    def test_synthetic_world_is_rejected_by_real_mode(self) -> None:
        with self.assertRaises(EvidenceServiceError) as denied:
            EvidenceService(self.store).open_snapshot(
                request_id=ident(IdKind.REQUEST, "real-mode-rejected"),
                world_id=self.world_id,
                branch_id=self.branch_id,
                access_scope=self.scope(),
                world_mode=EvidenceWorldMode.REAL,
            )
        self.assertIs(denied.exception.code, ErrorCode.EVIDENCE_ACCESS_DENIED)

    def test_restart_reproduces_snapshot_and_query_results(self) -> None:
        service = EvidenceService(self.store)
        snapshot = self.snapshot(self.scope(self.alpha), service=service)
        first = service.search_evidence(
            snapshot, EvidenceSearchRequest(terms=("alpha",), limit=5)
        )
        restarted_store = SQLiteAuthorityStore(
            self.database_path, allow_synthetic_genesis=True
        )
        restarted = EvidenceService(restarted_store)
        second_snapshot = restarted.open_snapshot(
            request_id=ident(IdKind.REQUEST, "evidence-snapshot"),
            world_id=self.world_id,
            branch_id=self.branch_id,
            access_scope=self.scope(self.alpha),
            world_mode=EvidenceWorldMode.SYNTHETIC_FIXTURE,
        )
        second = restarted.search_evidence(
            second_snapshot, EvidenceSearchRequest(terms=("alpha",), limit=5)
        )
        self.assertEqual(snapshot, second_snapshot)
        self.assertEqual(first.references, second.references)

    def test_unchanged_branch_uses_distinct_budget_scope_for_distinct_requests(self) -> None:
        service = EvidenceService(
            self.store,
            limits=EvidenceLimits(maximum_followup_searches=1),
        )
        first = self.snapshot(
            self.scope(self.alpha),
            service=service,
            request_id=ident(IdKind.REQUEST, "evidence-turn-one"),
        )
        second = self.snapshot(
            self.scope(self.alpha),
            service=service,
            request_id=ident(IdKind.REQUEST, "evidence-turn-two"),
        )
        self.assertNotEqual(first.snapshot_token, second.snapshot_token)
        service.search_evidence(first, EvidenceSearchRequest(terms=("alpha",), limit=1))
        service.search_evidence(second, EvidenceSearchRequest(terms=("alpha",), limit=1))

    def test_unavailable_store_fails_explicitly_without_fallback(self) -> None:
        class Unavailable:
            allow_synthetic_genesis = True

            def get_branch(self, branch_id):
                raise OSError("simulated store outage")

        with self.assertRaises(EvidenceServiceError) as unavailable:
            EvidenceService(Unavailable()).open_snapshot(
                request_id=ident(IdKind.REQUEST, "unavailable"),
                world_id=self.world_id,
                branch_id=self.branch_id,
                access_scope=self.scope(),
                world_mode=EvidenceWorldMode.SYNTHETIC_FIXTURE,
            )
        self.assertIs(
            unavailable.exception.code,
            ErrorCode.EVIDENCE_SERVICE_UNAVAILABLE,
        )

    def test_search_fetch_character_and_continuity_do_not_write_authority(self) -> None:
        def authority_state() -> tuple:
            with closing(sqlite3.connect(self.database_path)) as connection:
                return tuple(
                    connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                    for table in (
                        "sources",
                        "generations",
                        "artifacts",
                        "authority_records",
                        "transaction_journal",
                        "genesis_revisions",
                        "genesis_records",
                        "world_genesis_bindings",
                    )
                )

        before = authority_state()
        service = EvidenceService(self.store)
        snapshot = self.snapshot(self.scope(self.alpha), service=service)
        search = service.search_evidence(
            snapshot, EvidenceSearchRequest(terms=("adult",), limit=1)
        )
        service.fetch_evidence(
            snapshot,
            EvidenceFetchRequest((search.references[0].evidence_id,), ("claim",)),
        )
        service.get_character_sections(
            snapshot,
            CharacterSectionsRequest(self.alpha, ("claim",), limit=2),
        )
        service.get_continuity(
            snapshot,
            ContinuityRequest((search.references[0].evidence_id,), ("claim",), 1),
        )
        self.assertEqual(before, authority_state())


if __name__ == "__main__":
    unittest.main()
