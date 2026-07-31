from __future__ import annotations

from pathlib import Path
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
from cera.evaluation import RealGenesisSandbox
from cera.evidence import (
    EvidenceDocument,
    EvidenceEpistemicClass,
    EvidenceSearchRequest,
    EvidenceService,
    EvidenceWorldMode,
)
from cera.genesis.hanezawa_builder import CHARACTER_IDS
from cera.ids import IdKind, TypedId
from cera.serialization import canonical_json, text_sha256
from cera.storage import (
    AuthorityRecord,
    CommitMode,
    SourceRecord,
    SQLiteAuthorityStore,
    TurnCommitBundle,
)


ROOT = Path(__file__).resolve().parents[1]


def ident(kind: IdKind, suffix: str) -> TypedId:
    return TypedId(kind, f"real-transaction-{suffix}")


class RealGenesisTransactionIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sandbox = RealGenesisSandbox.create(ROOT)
        self.addCleanup(self.sandbox.close)
        self.hana = CHARACTER_IDS["Hana"]

    def bundle(
        self,
        suffix: str,
        *,
        branch_id: TypedId,
        expected_generation: int,
        expected_head: TypedId | None,
        parent: TypedId | None,
        mode: CommitMode = CommitMode.APPEND,
        replaces: TypedId | None = None,
        supersedes: tuple[TypedId, ...] = (),
    ) -> TurnCommitBundle:
        source = SourceRecord.from_payload(
            source_id=ident(IdKind.SOURCE, suffix),
            request_id=ident(IdKind.REQUEST, suffix),
            branch_id=branch_id,
            payload={"offline_real_genesis_calibration": suffix},
        )
        transaction_id = ident(IdKind.TRANSACTION, suffix)
        artifact_id = ident(IdKind.ARTIFACT, suffix)
        prose = f"Provider-free accepted calibration prose for {suffix}."
        artifact = AcceptedStoryArtifact(
            schema_version=AcceptedStoryArtifact.SCHEMA_VERSION,
            artifact_id=artifact_id,
            branch_id=branch_id,
            generation_id=ident(IdKind.GENERATION, suffix),
            parent_artifact_id=parent,
            source_id=source.source_id,
            decision_id=ident(IdKind.DECISION, suffix),
            accepted_prose=prose,
            prose_sha256=text_sha256(prose),
            responding_npc_ids=(self.hana,),
            realized_beat_ids=(ident(IdKind.BEAT, suffix),),
            validation_receipt_id=ident(IdKind.VALIDATION, suffix),
            transaction_id=transaction_id,
            status="accepted",
        )
        memory_id = ident(IdKind.MEMORY, suffix)
        claim = f"Hana retains branch-local calibration memory {suffix}."
        memory = EvidenceDocument(
            schema_version=EvidenceDocument.SCHEMA_VERSION,
            record_id=memory_id,
            record_version=1,
            record_type=EvidenceRecordType.MEMORY,
            epistemic_class=EvidenceEpistemicClass.VALIDATED_DERIVED,
            truth_status=TruthStatus.DERIVED,
            title=f"Calibration memory {suffix}",
            abstract=f"Branch-local Hana memory for {suffix}",
            claim=claim,
            authority=EvidenceAuthority.VALIDATED_DERIVED,
            subject_ids=(self.hana,),
            owner_id=self.hana,
            knowledge_owner_ids=(self.hana,),
            visibility=Visibility.OWNER_PRIVATE,
            knowledge_route=KnowledgeRoute.DIRECT,
            certainty=Certainty.BELIEVED,
            content_class="ordinary",
            genesis_revision_id=self.sandbox.revision_id,
            branch_origin_id=branch_id,
            valid_from_generation=expected_generation + 1,
            valid_to_generation=None,
            source_refs=(source.source_id,),
            supersedes=supersedes,
            tags=("offline_calibration", "memory", suffix),
            expandable_sections=("claim", "state"),
            linked_record_ids=(),
            sections_json=canonical_json(
                {"claim": claim, "state": {"calibration": suffix}}
            ),
        )
        authority = AuthorityRecord.from_payload(
            record_id=memory.record_id,
            branch_id=branch_id,
            artifact_id=artifact_id,
            record_type=memory.record_type.value,
            payload=memory,
            supersedes=supersedes,
        )
        return TurnCommitBundle(
            transaction_id=transaction_id,
            idempotency_key=f"offline-real-{suffix}",
            mode=mode,
            branch_id=branch_id,
            expected_generation=expected_generation,
            expected_head_artifact_id=expected_head,
            source=source,
            artifact=artifact,
            authority_records=(authority,),
            validation_receipt_ids=(artifact.validation_receipt_id,),
            lookup_receipt_ids=(ident(IdKind.LOOKUP_RECEIPT, suffix),),
            provider_receipt_ids=(),
            replaces_artifact_id=replaces,
        )

    def visible_calibration_memories(
        self, store: SQLiteAuthorityStore, branch_id: TypedId, request_suffix: str
    ) -> set[TypedId]:
        service = EvidenceService(store)
        snapshot = service.open_snapshot(
            request_id=ident(IdKind.REQUEST, request_suffix),
            world_id=self.sandbox.world_id,
            branch_id=branch_id,
            access_scope=self.sandbox.character_scope("Hana"),
            world_mode=EvidenceWorldMode.REAL,
        )
        return {
            item.metadata.record_id
            for item in service.search_evidence(
                snapshot,
                EvidenceSearchRequest(tags=("offline_calibration",), limit=20),
            ).references
        }

    def test_real_genesis_commit_replay_regeneration_fork_and_restart(self) -> None:
        main = self.sandbox.branch_id
        first = self.bundle(
            "first",
            branch_id=main,
            expected_generation=0,
            expected_head=None,
            parent=None,
        )
        committed = self.sandbox.store.commit_turn(first)
        replay = self.sandbox.store.commit_turn(first)
        self.assertFalse(committed.exact_replay)
        self.assertTrue(replay.exact_replay)
        self.assertEqual(committed.receipt, replay.receipt)

        second = self.bundle(
            "second",
            branch_id=main,
            expected_generation=1,
            expected_head=first.artifact.artifact_id,
            parent=first.artifact.artifact_id,
        )
        self.sandbox.store.commit_turn(second)
        child = ident(IdKind.BRANCH, "child")
        self.sandbox.store.fork_branch(main, child)

        replacement = self.bundle(
            "replacement",
            branch_id=main,
            expected_generation=2,
            expected_head=second.artifact.artifact_id,
            parent=first.artifact.artifact_id,
            mode=CommitMode.REGENERATE,
            replaces=second.artifact.artifact_id,
            supersedes=(second.authority_records[0].record_id,),
        )
        self.sandbox.store.commit_turn(replacement)
        self.sandbox.store.rebuild_evidence_search_index()

        restarted = SQLiteAuthorityStore(self.sandbox.database_path)
        restarted.rebuild_evidence_search_index()
        self.assertEqual(restarted.get_world_genesis_revision(self.sandbox.world_id), self.sandbox.revision_id)
        self.assertEqual(
            restarted.visible_artifact_ids(main),
            (first.artifact.artifact_id, replacement.artifact.artifact_id),
        )
        self.assertEqual(
            restarted.visible_artifact_ids(child),
            (first.artifact.artifact_id, second.artifact.artifact_id),
        )
        self.assertEqual(
            self.visible_calibration_memories(restarted, main, "restart-main"),
            {
                first.authority_records[0].record_id,
                replacement.authority_records[0].record_id,
            },
        )
        self.assertEqual(
            self.visible_calibration_memories(restarted, child, "restart-child"),
            {
                first.authority_records[0].record_id,
                second.authority_records[0].record_id,
            },
        )
        self.assertEqual(restarted.integrity_check(), ("ok",))
        self.assertEqual(restarted.foreign_key_check(), ())


if __name__ == "__main__":
    unittest.main()
