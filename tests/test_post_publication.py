from __future__ import annotations

import unittest

from cera.composer import PurePresentationRenderer, RendererProfile
from cera.consolidation import (
    ConsolidationProposal,
    FakeConsolidationFixture,
    FakeDerivedConsolidatorPort,
)
from cera.contracts import (
    Certainty,
    EvidenceAuthority,
    EvidenceRecordType,
    KnowledgeRoute,
    TruthStatus,
    Visibility,
)
from cera.evidence import (
    EvidenceDocument,
    EvidenceEpistemicClass,
    EvidenceSearchRequest,
    EvidenceService,
    EvidenceWorldMode,
)
from cera.genesis.hanezawa_builder import CHARACTER_IDS
from cera.ids import IdKind, deterministic_id
from cera.runtime import (
    OrdinaryTurnCommitBuilder,
    PostPublicationCoordinator,
    PostPublicationStatus,
)
from cera.runtime.post_publication import _work_requests
from cera.serialization import canonical_json
from cera.storage import (
    PostPublicationWorkKind,
    PostPublicationWorkStatus,
    SQLiteAuthorityStore,
)
import tests.test_live_shaped_pipeline as live_support
import tests.test_real_genesis_integration as real_support


class FailingRenderer:
    def render(self, artifact, *, profile):
        raise RuntimeError("simulated renderer outage")


class CrashAfterPublicationCoordinator(PostPublicationCoordinator):
    def _dispatch(self, *args, **kwargs):
        raise RuntimeError("simulated process exit after story commit")


class PostPublicationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.live = live_support.LiveShapedPipelineTests("runTest")
        self.live.setUp()
        self.addCleanup(self.live.doCleanups)

    def result(self, suffix: str):
        args = self.live.ordinary_case(suffix)
        return self.live.pipeline.execute(*args[:4])

    @staticmethod
    def no_change_port(suffix: str):
        proposal = ConsolidationProposal(
            schema_version=ConsolidationProposal.SCHEMA_VERSION,
            decision_id=real_support.ident(IdKind.DECISION, f"{suffix}-no-change"),
            records=(),
            source_evidence_ids=(),
            uncertainties=(),
            no_change_reason="No durable derived update is justified.",
            genesis_rewrite_proposed=False,
            clinical_diagnosis_proposed=False,
            accepted_story_change_proposed=False,
        )
        fixture = FakeConsolidationFixture(f"{suffix}-no-change", proposal)
        return FakeDerivedConsolidatorPort(fixture)

    def memory_port(self, result, suffix: str):
        artifact = result.accepted_artifact
        prepared = result.context.request.prepared_turn
        hana = CHARACTER_IDS["Hana"]
        event_id = deterministic_id(
            IdKind.EVENT,
            "cera.accepted_turn_event.v1",
            str(artifact.artifact_id),
        )
        event_evidence_id = deterministic_id(
            IdKind.EVIDENCE,
            "cera.evidence.record.v1",
            f"{prepared.request.world_id}|{prepared.request.genesis_revision_id}|{event_id}",
        )
        memory_id = real_support.ident(IdKind.MEMORY, f"{suffix}-hana-memory")
        memory = EvidenceDocument(
            schema_version=EvidenceDocument.SCHEMA_VERSION,
            record_id=memory_id,
            record_version=1,
            record_type=EvidenceRecordType.MEMORY,
            epistemic_class=EvidenceEpistemicClass.PRIVATE_FEELING,
            truth_status=TruthStatus.CHARACTER_OWNED,
            title="Hana remembers the accepted exchange",
            abstract="Hana privately retains the meaning of the accepted turn.",
            claim="Hana remembers the exchange and remains uncertain about its meaning.",
            authority=EvidenceAuthority.VALIDATED_DERIVED,
            subject_ids=(hana,),
            owner_id=hana,
            knowledge_owner_ids=(hana,),
            visibility=Visibility.OWNER_PRIVATE,
            knowledge_route=KnowledgeRoute.DIRECT,
            certainty=Certainty.BELIEVED,
            content_class="ordinary",
            genesis_revision_id=prepared.request.genesis_revision_id,
            branch_origin_id=prepared.request.branch_id,
            valid_from_generation=prepared.evidence_snapshot.generation + 1,
            valid_to_generation=None,
            source_refs=(event_id,),
            supersedes=(),
            tags=("accepted_turn", "hana", "exchange"),
            expandable_sections=("memory",),
            linked_record_ids=(),
            sections_json=canonical_json(
                {
                    "memory": {
                        "memory_key": f"accepted-turn-{suffix}",
                        "objective_event_refs": [str(event_id)],
                        "perceived_facts": [
                            "Hana directly participated in the accepted exchange."
                        ],
                        "subjective_interpretations": [
                            {
                                "kind": "other",
                                "statement": "Hana is still deciding what the exchange means.",
                                "certainty": "believed",
                            }
                        ],
                        "unknowns": ["Its longer-term effect remains unknown."],
                        "retrieval_tags": ["accepted_turn", "hana", "exchange"],
                        "trigger_cues": ["a similar exchange"],
                        "clinical_diagnosis": "not_assessed",
                        "genesis_effect": "none",
                        "supersession_reason": None,
                    }
                }
            ),
        )
        proposal = ConsolidationProposal(
            schema_version=ConsolidationProposal.SCHEMA_VERSION,
            decision_id=real_support.ident(IdKind.DECISION, f"{suffix}-memory"),
            records=(memory,),
            source_evidence_ids=(event_evidence_id,),
            uncertainties=("Long-term meaning remains unknown.",),
            no_change_reason=None,
            genesis_rewrite_proposed=False,
            clinical_diagnosis_proposed=False,
            accepted_story_change_proposed=False,
        )
        fixture = FakeConsolidationFixture(f"{suffix}-memory", proposal)
        return FakeDerivedConsolidatorPort(fixture), memory

    def test_publication_render_and_no_change_consolidation_are_separate(self) -> None:
        result = self.result("phase17-no-change")
        renderer = PurePresentationRenderer(
            (RendererProfile("plain-test", "v1", prefix="<story>", suffix="</story>"),)
        )
        consolidator = self.no_change_port("phase17")
        outcome = PostPublicationCoordinator(self.live.real.sandbox.store).execute(
            result,
            renderer=renderer,
            renderer_profile="plain-test",
            consolidator=consolidator,
        )
        self.assertEqual(outcome.render_status, PostPublicationStatus.COMPLETED)
        self.assertEqual(
            outcome.consolidation_status, PostPublicationStatus.NO_CHANGE
        )
        self.assertEqual(outcome.derived_view_status, PostPublicationStatus.NO_CHANGE)
        self.assertEqual(consolidator.invocation_count, 1)
        self.assertEqual(self.live.real.sandbox.store.table_count("artifacts"), 1)
        self.assertEqual(self.live.real.sandbox.store.table_count("authority_records"), 2)
        self.assertEqual(
            outcome.rendered_story.accepted_artifact_sha256,
            outcome.publication.receipt.new_artifact_sha256,
        )

    def test_event_drives_private_memory_commit_receipts_views_and_replay(self) -> None:
        result = self.result("phase17-memory")
        consolidator, memory = self.memory_port(result, "phase17")
        renderer = PurePresentationRenderer((RendererProfile("plain", "v1"),))
        coordinator = PostPublicationCoordinator(self.live.real.sandbox.store)
        first = coordinator.execute(
            result,
            renderer=renderer,
            renderer_profile="plain",
            consolidator=consolidator,
        )
        self.assertEqual(first.consolidation_status, PostPublicationStatus.COMPLETED)
        self.assertEqual(first.derived_view_status, PostPublicationStatus.COMPLETED)
        self.assertFalse(first.consolidation_commit.exact_replay)
        self.assertEqual(self.live.real.sandbox.store.get_branch(
            self.live.real.sandbox.branch_id
        ).generation, 1)
        self.assertEqual(self.live.real.sandbox.store.get_branch(
            self.live.real.sandbox.branch_id
        ).authority_revision, 1)
        self.assertEqual(self.live.real.sandbox.store.table_count("artifacts"), 1)
        self.assertEqual(self.live.real.sandbox.store.table_count("authority_records"), 3)
        self.assertEqual(
            self.live.real.sandbox.store.table_count("consolidation_receipt_records"),
            3,
        )
        visible = self.live.real.sandbox.store.visible_record_ids(
            self.live.real.sandbox.branch_id
        )
        self.assertIn(memory.record_id, visible)
        evidence = EvidenceService(self.live.real.sandbox.store)
        hana_snapshot = evidence.open_snapshot(
            request_id=real_support.ident(IdKind.REQUEST, "phase17-hana-retrieval"),
            world_id=self.live.real.sandbox.world_id,
            branch_id=self.live.real.sandbox.branch_id,
            access_scope=self.live.real.sandbox.character_scope("Hana"),
            world_mode=EvidenceWorldMode.REAL,
        )
        mia_snapshot = evidence.open_snapshot(
            request_id=real_support.ident(IdKind.REQUEST, "phase17-mia-retrieval"),
            world_id=self.live.real.sandbox.world_id,
            branch_id=self.live.real.sandbox.branch_id,
            access_scope=self.live.real.sandbox.character_scope("Mia"),
            world_mode=EvidenceWorldMode.REAL,
        )
        hana_records = {
            value.metadata.record_id
            for value in evidence.search_evidence(
                hana_snapshot,
                EvidenceSearchRequest(tags=("exchange",), limit=10),
            ).references
        }
        mia_records = {
            value.metadata.record_id
            for value in evidence.search_evidence(
                mia_snapshot,
                EvidenceSearchRequest(tags=("exchange",), limit=10),
            ).references
        }
        self.assertIn(memory.record_id, hana_records)
        self.assertNotIn(memory.record_id, mia_records)

        second = coordinator.execute(
            result,
            renderer=renderer,
            renderer_profile="plain",
            consolidator=consolidator,
        )
        self.assertTrue(second.publication.exact_replay)
        self.assertTrue(second.consolidation_commit.exact_replay)
        self.assertEqual(consolidator.invocation_count, 1)
        self.assertEqual(self.live.real.sandbox.store.table_count("artifacts"), 1)
        self.assertEqual(self.live.real.sandbox.store.table_count("authority_records"), 3)

        restarted = SQLiteAuthorityStore(self.live.real.sandbox.database_path)
        self.assertEqual(restarted.get_branch(self.live.real.sandbox.branch_id).generation, 1)
        self.assertEqual(
            restarted.get_branch(self.live.real.sandbox.branch_id).authority_revision, 1
        )

    def test_render_and_consolidator_failures_do_not_roll_back_publication(self) -> None:
        result = self.result("phase17-failures")
        unavailable = self.no_change_port("phase17-unavailable")
        unavailable.available = False
        outcome = PostPublicationCoordinator(self.live.real.sandbox.store).execute(
            result,
            renderer=FailingRenderer(),
            renderer_profile="broken",
            consolidator=unavailable,
        )
        self.assertEqual(outcome.render_status, PostPublicationStatus.FAILED)
        self.assertEqual(outcome.consolidation_status, PostPublicationStatus.FAILED)
        self.assertEqual(outcome.derived_view_status, PostPublicationStatus.PENDING)
        branch = self.live.real.sandbox.store.get_branch(self.live.real.sandbox.branch_id)
        self.assertEqual(branch.head_artifact_id, result.accepted_artifact.artifact_id)
        self.assertEqual(branch.generation, 1)
        self.assertEqual(branch.authority_revision, 0)
        self.assertEqual(self.live.real.sandbox.store.table_count("artifacts"), 1)
        self.assertEqual(self.live.real.sandbox.store.table_count("authority_records"), 2)

    def test_atomic_outbox_survives_restart_before_dispatch(self) -> None:
        result = self.result("phase18-crash-before-dispatch")
        renderer = PurePresentationRenderer((RendererProfile("plain", "v1"),))
        consolidator = self.no_change_port("phase18-crash")
        with self.assertRaisesRegex(RuntimeError, "process exit"):
            CrashAfterPublicationCoordinator(
                self.live.real.sandbox.store
            ).execute(
                result,
                renderer=renderer,
                renderer_profile="plain",
                consolidator=consolidator,
            )

        artifact_id = result.accepted_artifact.artifact_id
        pending = self.live.real.sandbox.store.post_publication_work_for_artifact(
            artifact_id
        )
        self.assertEqual(len(pending), 3)
        self.assertTrue(
            all(value.status is PostPublicationWorkStatus.PENDING for value in pending)
        )
        restarted = SQLiteAuthorityStore(self.live.real.sandbox.database_path)
        resumed = PostPublicationCoordinator(restarted).resume(
            artifact_id,
            renderer=renderer,
            consolidator=consolidator,
        )
        self.assertEqual(resumed.render_status, PostPublicationStatus.COMPLETED)
        self.assertEqual(resumed.consolidation_status, PostPublicationStatus.NO_CHANGE)
        self.assertEqual(resumed.derived_view_status, PostPublicationStatus.NO_CHANGE)
        self.assertEqual(consolidator.invocation_count, 1)
        self.assertTrue(
            all(
                value.status
                in {
                    PostPublicationWorkStatus.COMPLETED,
                    PostPublicationWorkStatus.NO_CHANGE,
                }
                for value in resumed.work_items
            )
        )
        replayed = PostPublicationCoordinator(restarted).resume(
            artifact_id,
            renderer=renderer,
            consolidator=consolidator,
        )
        self.assertEqual(replayed.consolidation_status, PostPublicationStatus.NO_CHANGE)
        self.assertEqual(replayed.derived_view_status, PostPublicationStatus.NO_CHANGE)
        self.assertEqual(consolidator.invocation_count, 1)
        self.assertEqual(restarted.table_count("post_publication_attempts"), 3)

    def test_story_rollback_also_rolls_back_scheduled_work(self) -> None:
        result = self.result("phase18-atomic-rollback")
        requests = _work_requests(
            result,
            renderer_profile="plain",
            consolidation_requested=True,
        )
        bundle = OrdinaryTurnCommitBuilder().build(
            result,
            post_publication_work_requests=requests,
        )
        failing = live_support.FailingTurnReceiptStore(
            self.live.real.sandbox.database_path
        )
        with self.assertRaisesRegex(Exception, "simulated Phase 16 failure"):
            failing.commit_turn(bundle)
        restarted = SQLiteAuthorityStore(self.live.real.sandbox.database_path)
        self.assertEqual(restarted.table_count("artifacts"), 0)
        self.assertEqual(restarted.table_count("authority_records"), 0)
        self.assertEqual(restarted.table_count("post_publication_work"), 0)

    def test_failed_work_never_retries_without_explicit_work_ids_and_reason(self) -> None:
        result = self.result("phase18-explicit-retry")
        unavailable = self.no_change_port("phase18-unavailable")
        unavailable.available = False
        first = PostPublicationCoordinator(self.live.real.sandbox.store).execute(
            result,
            renderer=FailingRenderer(),
            renderer_profile="plain",
            consolidator=unavailable,
        )
        failed_ids = tuple(
            value.work_id
            for value in first.work_items
            if value.status is PostPublicationWorkStatus.FAILED
        )
        self.assertEqual(len(failed_ids), 2)
        artifact_id = result.accepted_artifact.artifact_id

        healthy_renderer = PurePresentationRenderer((RendererProfile("plain", "v1"),))
        healthy_consolidator = self.no_change_port("phase18-retry")
        restarted = SQLiteAuthorityStore(self.live.real.sandbox.database_path)
        held = PostPublicationCoordinator(restarted).resume(
            artifact_id,
            renderer=healthy_renderer,
            consolidator=healthy_consolidator,
        )
        self.assertEqual(held.render_status, PostPublicationStatus.FAILED)
        self.assertEqual(held.consolidation_status, PostPublicationStatus.FAILED)
        self.assertEqual(healthy_consolidator.invocation_count, 0)
        self.assertEqual(restarted.table_count("post_publication_attempts"), 2)

        retried = PostPublicationCoordinator(restarted).resume(
            artifact_id,
            renderer=healthy_renderer,
            consolidator=healthy_consolidator,
            retry_failed_work_ids=failed_ids,
            authorization_reason="operator reviewed both simulated outages",
        )
        self.assertEqual(retried.render_status, PostPublicationStatus.COMPLETED)
        self.assertEqual(retried.consolidation_status, PostPublicationStatus.NO_CHANGE)
        self.assertEqual(retried.derived_view_status, PostPublicationStatus.NO_CHANGE)
        self.assertEqual(healthy_consolidator.invocation_count, 1)
        self.assertEqual(restarted.table_count("post_publication_attempts"), 5)
        attempts = {value.kind: value.attempt_count for value in retried.work_items}
        self.assertEqual(attempts[PostPublicationWorkKind.RENDER], 2)
        self.assertEqual(attempts[PostPublicationWorkKind.CONSOLIDATE], 2)
        self.assertEqual(attempts[PostPublicationWorkKind.DERIVED_VIEWS], 1)

    def test_running_work_is_failed_closed_on_explicit_restart_recovery(self) -> None:
        result = self.result("phase18-interrupted")
        renderer = PurePresentationRenderer((RendererProfile("plain", "v1"),))
        consolidator = self.no_change_port("phase18-interrupted")
        with self.assertRaises(RuntimeError):
            CrashAfterPublicationCoordinator(self.live.real.sandbox.store).execute(
                result,
                renderer=renderer,
                renderer_profile="plain",
                consolidator=consolidator,
            )
        artifact_id = result.accepted_artifact.artifact_id
        render_work = next(
            value
            for value in self.live.real.sandbox.store.post_publication_work_for_artifact(
                artifact_id
            )
            if value.kind is PostPublicationWorkKind.RENDER
        )
        self.live.real.sandbox.store.claim_post_publication_work(render_work.work_id)

        restarted = SQLiteAuthorityStore(self.live.real.sandbox.database_path)
        recovered = restarted.recover_interrupted_post_publication_work()
        self.assertEqual(recovered, (render_work.work_id,))
        self.assertEqual(
            restarted.get_post_publication_work(render_work.work_id).status,
            PostPublicationWorkStatus.FAILED,
        )
        held = PostPublicationCoordinator(restarted).resume(
            artifact_id,
            renderer=renderer,
        )
        self.assertEqual(held.render_status, PostPublicationStatus.FAILED)
        retried = PostPublicationCoordinator(restarted).resume(
            artifact_id,
            renderer=renderer,
            retry_failed_work_ids=(render_work.work_id,),
            authorization_reason="operator reconciled interrupted pure render",
        )
        self.assertEqual(retried.render_status, PostPublicationStatus.COMPLETED)


if __name__ == "__main__":
    unittest.main()
