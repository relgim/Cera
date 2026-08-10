from __future__ import annotations

import sqlite3
import unittest
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from cera.contracts import AcceptedStoryArtifact
from cera.errors import StateConflictError, TransactionError
from cera.ids import IdKind, TypedId
from cera.serialization import text_sha256
from cera.storage import (
    AuthorityRecord,
    CommitMode,
    JournalStatus,
    SourceRecord,
    SQLiteAuthorityStore,
    TurnCommitBundle,
)


def ident(kind: IdKind, suffix: str) -> TypedId:
    return TypedId(kind, suffix)


class FailingFinalizeStore(SQLiteAuthorityStore):
    def _after_artifact_insert(self, connection, bundle) -> None:
        raise RuntimeError("simulated process failure")


class SQLiteStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.database_path = Path(self.temporary.name) / "authority.sqlite3"
        self.store = SQLiteAuthorityStore(self.database_path)
        self.world_id = ident(IdKind.WORLD, "world-main")
        self.branch_id = ident(IdKind.BRANCH, "branch-main")
        self.store.create_world(self.world_id)
        self.store.create_root_branch(self.world_id, self.branch_id)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def bundle(
        self,
        suffix: str,
        *,
        branch_id: TypedId | None = None,
        expected_generation: int = 0,
        expected_head: TypedId | None = None,
        parent: TypedId | None = None,
        mode: CommitMode = CommitMode.APPEND,
        replaces: TypedId | None = None,
        supersedes: tuple[TypedId, ...] = (),
    ) -> TurnCommitBundle:
        branch = branch_id or self.branch_id
        source = SourceRecord.from_payload(
            source_id=ident(IdKind.SOURCE, f"source-{suffix}"),
            request_id=ident(IdKind.REQUEST, f"request-{suffix}"),
            branch_id=branch,
            payload={"message": suffix},
        )
        transaction_id = ident(IdKind.TRANSACTION, f"transaction-{suffix}")
        artifact_id = ident(IdKind.ARTIFACT, f"artifact-{suffix}")
        prose = f"Accepted prose {suffix}."
        artifact = AcceptedStoryArtifact(
            schema_version=AcceptedStoryArtifact.SCHEMA_VERSION,
            artifact_id=artifact_id,
            branch_id=branch,
            generation_id=ident(IdKind.GENERATION, f"generation-{suffix}"),
            parent_artifact_id=parent,
            source_id=source.source_id,
            decision_id=ident(IdKind.DECISION, f"decision-{suffix}"),
            accepted_prose=prose,
            prose_sha256=text_sha256(prose),
            responding_npc_ids=(ident(IdKind.CHARACTER, "alpha"),),
            realized_beat_ids=(ident(IdKind.BEAT, f"beat-{suffix}"),),
            validation_receipt_id=ident(IdKind.VALIDATION, f"validation-{suffix}"),
            transaction_id=transaction_id,
            status="accepted",
        )
        record = AuthorityRecord.from_payload(
            record_id=ident(IdKind.RECORD, f"record-{suffix}"),
            branch_id=branch,
            artifact_id=artifact_id,
            record_type="test_event",
            payload={"fact": suffix},
            supersedes=supersedes,
        )
        return TurnCommitBundle(
            transaction_id=transaction_id,
            idempotency_key=f"turn-{suffix}",
            mode=mode,
            branch_id=branch,
            expected_generation=expected_generation,
            expected_head_artifact_id=expected_head,
            source=source,
            artifact=artifact,
            authority_records=(record,),
            validation_receipt_ids=(artifact.validation_receipt_id,),
            lookup_receipt_ids=(ident(IdKind.LOOKUP_RECEIPT, f"lookup-{suffix}"),),
            provider_receipt_ids=(ident(IdKind.PROVIDER_RECEIPT, f"provider-{suffix}"),),
            replaces_artifact_id=replaces,
        )

    def test_store_enforces_wal_foreign_keys_and_versioned_migration(self) -> None:
        with closing(sqlite3.connect(self.database_path)) as connection:
            self.assertEqual(connection.execute("PRAGMA journal_mode").fetchone()[0], "wal")
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 19)
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0],
                19,
            )
        self.assertEqual(self.store.integrity_check(), ("ok",))
        self.assertEqual(self.store.foreign_key_check(), ())

    def test_branches_for_world_is_scoped_and_stable(self) -> None:
        child = ident(IdKind.BRANCH, "branch-child")
        self.store.create_root_branch(self.world_id, child)
        other_world = ident(IdKind.WORLD, "world-other")
        other_branch = ident(IdKind.BRANCH, "branch-other")
        self.store.create_world(other_world)
        self.store.create_root_branch(other_world, other_branch)

        branches = self.store.branches_for_world(self.world_id)
        self.assertEqual(
            {value.branch_id for value in branches},
            {self.branch_id, child},
        )
        self.assertNotIn(other_branch, {value.branch_id for value in branches})

    def test_atomic_commit_and_exact_replay(self) -> None:
        bundle = self.bundle("a", parent=None)
        first = self.store.commit_turn(bundle)
        second = self.store.commit_turn(bundle)

        self.assertFalse(first.exact_replay)
        self.assertTrue(second.exact_replay)
        self.assertEqual(first.receipt, second.receipt)
        self.assertEqual(first.receipt.transaction_sha256, bundle.bundle_sha256)
        self.assertEqual(self.store.get_branch(self.branch_id).generation, 1)
        self.assertEqual(self.store.table_count("artifacts"), 1)
        self.assertEqual(self.store.table_count("commit_receipts"), 1)

    def test_same_idempotency_key_with_different_bundle_is_rejected(self) -> None:
        original = self.bundle("a", parent=None)
        self.store.prepare_commit(original)
        conflicting = self.bundle("b", parent=None)
        object.__setattr__(conflicting, "idempotency_key", original.idempotency_key)
        with self.assertRaisesRegex(TransactionError, "reused differently"):
            self.store.prepare_commit(conflicting)

    def test_optimistic_prepare_loser_rolls_back_without_story_mutation(self) -> None:
        first = self.bundle("a", parent=None)
        second = self.bundle("b", parent=None)
        self.store.prepare_commit(first)
        self.store.prepare_commit(second)
        self.store.finalize_commit(first)

        with self.assertRaisesRegex(StateConflictError, "generation changed"):
            self.store.finalize_commit(second)

        self.assertEqual(
            self.store.get_journal(second.transaction_id).status,
            JournalStatus.ROLLED_BACK,
        )
        self.assertEqual(self.store.table_count("artifacts"), 1)
        self.assertEqual(self.store.table_count("sources"), 1)

    def test_crash_inside_finalize_is_atomic_and_restart_recovery_is_explicit(self) -> None:
        failing_store = FailingFinalizeStore(self.database_path)
        bundle = self.bundle("crash", parent=None)
        failing_store.prepare_commit(bundle)

        with self.assertRaisesRegex(TransactionError, "simulated process failure"):
            failing_store.finalize_commit(bundle)

        self.assertEqual(self.store.table_count("sources"), 0)
        self.assertEqual(self.store.table_count("artifacts"), 0)
        self.assertEqual(
            self.store.get_journal(bundle.transaction_id).status,
            JournalStatus.PREPARED,
        )
        restarted = SQLiteAuthorityStore(self.database_path)
        report = restarted.recover_prepared(reason="test restart audit")
        self.assertEqual(report.rolled_back_transaction_ids, (bundle.transaction_id,))
        self.assertEqual(
            restarted.get_journal(bundle.transaction_id).status,
            JournalStatus.ROLLED_BACK,
        )
        with self.assertRaisesRegex(TransactionError, "cannot be retried"):
            restarted.finalize_commit(bundle)

    def test_regeneration_creates_sibling_and_preserves_fork_isolation(self) -> None:
        first = self.bundle("a", parent=None)
        self.store.commit_turn(first)
        second = self.bundle(
            "b",
            expected_generation=1,
            expected_head=first.artifact.artifact_id,
            parent=first.artifact.artifact_id,
        )
        self.store.commit_turn(second)

        child = ident(IdKind.BRANCH, "branch-child")
        self.store.fork_branch(self.branch_id, child)
        replacement = self.bundle(
            "c",
            expected_generation=2,
            expected_head=second.artifact.artifact_id,
            parent=first.artifact.artifact_id,
            mode=CommitMode.REGENERATE,
            replaces=second.artifact.artifact_id,
        )
        self.store.commit_turn(replacement)

        self.assertEqual(
            self.store.visible_artifact_ids(self.branch_id),
            (first.artifact.artifact_id, replacement.artifact.artifact_id),
        )
        self.assertEqual(
            self.store.visible_artifact_ids(child),
            (first.artifact.artifact_id, second.artifact.artifact_id),
        )
        self.assertEqual(
            self.store.visible_record_ids(self.branch_id),
            (
                first.authority_records[0].record_id,
                replacement.authority_records[0].record_id,
            ),
        )
        self.assertEqual(
            self.store.visible_record_ids(child),
            (
                first.authority_records[0].record_id,
                second.authority_records[0].record_id,
            ),
        )
        self.assertEqual(self.store.table_count("artifacts"), 3)

    def test_child_commit_does_not_move_parent_head(self) -> None:
        first = self.bundle("a", parent=None)
        self.store.commit_turn(first)
        child = ident(IdKind.BRANCH, "branch-child")
        self.store.fork_branch(self.branch_id, child)
        child_commit = self.bundle(
            "child",
            branch_id=child,
            expected_generation=0,
            expected_head=first.artifact.artifact_id,
            parent=first.artifact.artifact_id,
        )
        self.store.commit_turn(child_commit)

        self.assertEqual(
            self.store.get_branch(self.branch_id).head_artifact_id, first.artifact.artifact_id
        )
        self.assertEqual(self.store.get_branch(self.branch_id).generation, 1)
        self.assertEqual(
            self.store.get_branch(child).head_artifact_id, child_commit.artifact.artifact_id
        )
        self.assertEqual(self.store.get_branch(child).generation, 1)

    def test_append_only_triggers_and_backup(self) -> None:
        bundle = self.bundle("a", parent=None)
        self.store.commit_turn(bundle)
        with closing(sqlite3.connect(self.database_path)) as connection:
            with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
                connection.execute(
                    "UPDATE artifacts SET accepted_prose = 'changed' WHERE artifact_id = ?",
                    (str(bundle.artifact.artifact_id),),
                )
        backup_path = Path(self.temporary.name) / "backup.sqlite3"
        self.store.backup_to(backup_path)
        backup = SQLiteAuthorityStore(backup_path)
        self.assertEqual(backup.integrity_check(), ("ok",))
        self.assertEqual(backup.table_count("artifacts"), 1)


if __name__ == "__main__":
    unittest.main()
