from __future__ import annotations

import json
import sqlite3
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from cera.errors import ContractValidationError, StateConflictError
from cera.pi_scene.provider_stage_retry import (
    ProviderFamily,
    ProviderModelFamily,
    ProviderStage,
    ProviderStageBlockReason,
    ProviderStageFailureClass,
    ProviderStageRecoveryAction,
    ProviderStageRecoveryRequiredV1,
    ProviderStageRetryIdentityV1,
    ProviderStageRetryPhase,
)
from cera.pi_scene.provider_stage_retry_blob import (
    TrustedLocalProtectedStageBlobStore,
)
from cera.pi_scene.provider_stage_retry_controller import (
    ProviderStageRetryControllerV1,
)
from cera.pi_scene.provider_stage_retry_scope import ProviderStageRetryOccurrenceScopeV1
from cera.serialization import bytes_sha256, canonical_json, text_sha256
from cera.storage.migrations import MIGRATIONS
from cera.storage.provider_stage_retry_store import SQLiteProviderStageRetryStore
from cera.storage.sqlite_store import SQLiteAuthorityStore

_ADULT_INPUT_SENTINEL = b"ADULT PRIVATE INPUT MUST STAY OUT OF SQLITE AND WAL"
_ADULT_RESULT_SENTINEL = b"ADULT PRIVATE RESULT MUST STAY OUT OF SAFE STATUS"


def _sha(label: str) -> str:
    return text_sha256(label)


def _identity(
    exact_input: bytes,
    *,
    stage: ProviderStage = ProviderStage.WRITER,
    occurrence: str = "occurrence-1",
    authority: str = "authority-1",
) -> ProviderStageRetryIdentityV1:
    owners = {
        ProviderStage.PLANNER: (ProviderFamily.CODEX, ProviderModelFamily.SOL),
        ProviderStage.SEMANTIC_VALIDATOR: (
            ProviderFamily.CODEX,
            ProviderModelFamily.LUNA,
        ),
        ProviderStage.READER: (ProviderFamily.CODEX, ProviderModelFamily.SOL),
        ProviderStage.WRITER: (
            ProviderFamily.DEEPSEEK,
            ProviderModelFamily.DEEPSEEK_V4,
        ),
        ProviderStage.RECORDER: (
            ProviderFamily.DEEPSEEK,
            ProviderModelFamily.DEEPSEEK_V4,
        ),
        ProviderStage.ADULT_SCENE: (
            ProviderFamily.DEEPSEEK,
            ProviderModelFamily.DEEPSEEK_V4,
        ),
        ProviderStage.ADULT_FILTER: (
            ProviderFamily.DEEPSEEK,
            ProviderModelFamily.DEEPSEEK_V4,
        ),
    }
    provider, model_family = owners[stage]
    return ProviderStageRetryIdentityV1(
        schema_version=ProviderStageRetryIdentityV1.SCHEMA_VERSION,
        provider=provider,
        model_family=model_family,
        stage=stage,
        request_occurrence_sha256=_sha(occurrence),
        request_sha256=_sha("request-content"),
        stage_input_sha256=bytes_sha256(exact_input),
        authority_sha256=_sha(authority),
        story_state_committed=stage is ProviderStage.RECORDER,
    )


class _CrashAfterResultBlobStore(SQLiteProviderStageRetryStore):
    def __init__(
        self,
        authority_store: SQLiteAuthorityStore,
        blob_store: TrustedLocalProtectedStageBlobStore,
    ) -> None:
        super().__init__(authority_store, blob_store)
        self.crash_once = True

    def _after_result_blob_frozen(self, receipt: object) -> None:
        del receipt
        if self.crash_once:
            self.crash_once = False
            raise RuntimeError("simulated crash after protected result freeze")


class SQLiteProviderStageRetryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.database_path = self.root / "authority.sqlite3"
        self.blob_root = self.root / "protected-stage-blobs"
        self.authority = SQLiteAuthorityStore(self.database_path)
        self.blobs = TrustedLocalProtectedStageBlobStore(self.blob_root)
        self.store = SQLiteProviderStageRetryStore(self.authority, self.blobs)
        self.controller = ProviderStageRetryControllerV1(store=self.store)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_unreleased_migration_19_hash_is_pinned(self) -> None:
        self.assertEqual(
            text_sha256(MIGRATIONS[19]),
            "3425b562c8e29b370c68440f2153c2e4df5ffc5447f76f915d6edb32f15658b2",
        )

    def test_reader_migration_20_hash_is_pinned(self) -> None:
        self.assertEqual(
            text_sha256(MIGRATIONS[20]),
            "5d75f36797e650c46d0592387e7a5679ac1cbdf351bbcac9a787c60850ff91cc",
        )

    def _prepared(
        self,
        *,
        stage: ProviderStage = ProviderStage.WRITER,
        exact_input: bytes = b"protected stage input",
        occurrence: str = "occurrence-1",
    ) -> tuple[ProviderStageRetryIdentityV1, str]:
        identity = _identity(
            exact_input,
            stage=stage,
            occurrence=occurrence,
        )
        chain = self.controller.begin(identity, exact_input)
        chain = self.controller.prepare_attempt(
            chain.chain_id,
            session_scope_sha256=_sha(f"{occurrence}:session:1"),
            ledger_prefix_before_sha256=_sha(f"{occurrence}:ledger:0"),
        )
        self.assertIs(chain.phase, ProviderStageRetryPhase.ATTEMPT_PREPARED)
        return identity, chain.chain_id

    def _close_pretransport_attempt(
        self,
        chain_id: str,
        attempt_number: int,
        *,
        occurrence: str = "occurrence-1",
    ) -> None:
        chain = self.controller.mark_pretransport_failed(
            chain_id,
            attempt_number=attempt_number,
            failure_class=ProviderStageFailureClass.PROVIDER_UNAVAILABLE,
            failure_evidence_sha256=_sha(f"{occurrence}:failure:{attempt_number}"),
            ledger_prefix_after_sha256=_sha(f"{occurrence}:ledger:{attempt_number}"),
            duration_ms=attempt_number,
        )
        self.assertEqual(chain.attempts[-1].provider_operations_observed, 0)
        self.assertEqual(chain.attempts[-1].provider_operations_conservative, 0)
        chain = self.controller.mark_owner_retired(
            chain_id,
            attempt_number=attempt_number,
            retirement_evidence_sha256=_sha(f"{occurrence}:retired:{attempt_number}"),
        )
        if attempt_number < 3:
            self.assertIs(chain.phase, ProviderStageRetryPhase.OWNER_RETIRED)

    def _accept_retry(
        self,
        chain_id: str,
        attempt_number: int,
        *,
        occurrence: str = "occurrence-1",
    ) -> None:
        chain = self.controller.accept_retry(
            chain_id,
            retry_action_sha256=_sha(f"{occurrence}:retry:{attempt_number}"),
            session_scope_sha256=_sha(f"{occurrence}:session:{attempt_number}"),
            ledger_prefix_before_sha256=_sha(f"{occurrence}:ledger:{attempt_number - 1}"),
        )
        self.assertEqual(chain.attempts[-1].attempt_number, attempt_number)
        self.assertIs(chain.phase, ProviderStageRetryPhase.ATTEMPT_PREPARED)

    def test_migration_restart_exact_replay_and_occurrence_authority(self) -> None:
        with closing(sqlite3.connect(self.database_path)) as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 20)
            tables = {
                row[0]
                for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            }
        self.assertTrue(
            {
                "provider_stage_retry_chains",
                "provider_stage_retry_actions",
                "provider_stage_retry_checkpoints",
                "provider_stage_retry_attempts",
                "provider_stage_retry_events",
                "provider_stage_retry_occurrence_scopes",
            }.issubset(tables)
        )

        exact_input = b"same repeatable request content"
        identity, chain_id = self._prepared(exact_input=exact_input)
        restarted = SQLiteProviderStageRetryStore(
            SQLiteAuthorityStore(self.database_path),
            TrustedLocalProtectedStageBlobStore(self.blob_root),
        )
        self.assertEqual(restarted.read(chain_id), self.store.read(chain_id))
        self.assertEqual(restarted.load_input(chain_id), exact_input)
        self.assertEqual(restarted.begin(identity, exact_input).chain_id, chain_id)

        changed_input = exact_input + b" changed"
        with self.assertRaises(StateConflictError):
            restarted.begin(
                _identity(changed_input, occurrence="occurrence-1"),
                changed_input,
            )
        with self.assertRaises(StateConflictError):
            restarted.begin(
                _identity(
                    exact_input,
                    occurrence="occurrence-1",
                    authority="authority-2",
                ),
                exact_input,
            )
        distinct = restarted.begin(
            _identity(exact_input, occurrence="occurrence-2"),
            exact_input,
        )
        self.assertNotEqual(distinct.chain_id, chain_id)

    def test_reader_chain_is_accepted_by_fresh_schema(self) -> None:
        exact_input = b"frozen Reader validation packet"
        identity = _identity(
            exact_input,
            stage=ProviderStage.READER,
            occurrence="reader-occurrence",
        )

        chain = self.controller.begin(identity, exact_input)

        self.assertIs(chain.identity.stage, ProviderStage.READER)
        self.assertIs(chain.identity.provider, ProviderFamily.CODEX)
        self.assertIs(chain.identity.model_family, ProviderModelFamily.SOL)

    def test_populated_migration_19_upgrades_to_reader_without_losing_custody(self) -> None:
        legacy_path = self.root / "legacy-v19.sqlite3"
        with closing(sqlite3.connect(legacy_path)) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA journal_mode = WAL")
            for version in range(1, 20):
                connection.executescript("BEGIN IMMEDIATE;\n" + MIGRATIONS[version])
                connection.execute(
                    "INSERT INTO schema_migrations(version, applied_at, migration_sha256) "
                    "VALUES (?, ?, ?)",
                    (version, "2026-08-10T00:00:00+00:00", text_sha256(MIGRATIONS[version])),
                )
                connection.execute(f"PRAGMA user_version = {version}")
                connection.commit()

        legacy_authority = object.__new__(SQLiteAuthorityStore)
        legacy_authority.database_path = legacy_path.resolve()
        legacy_authority.busy_timeout_ms = 5_000
        legacy_authority.allow_synthetic_genesis = False
        legacy_blobs = TrustedLocalProtectedStageBlobStore(self.root / "legacy-blobs")
        legacy_store = SQLiteProviderStageRetryStore(legacy_authority, legacy_blobs)
        legacy_controller = ProviderStageRetryControllerV1(store=legacy_store)
        exact_input = b"populated v19 provider-stage custody"
        legacy_identity = _identity(exact_input, occurrence="legacy-v19")
        legacy_chain = legacy_controller.begin(legacy_identity, exact_input)
        legacy_controller.prepare_attempt(
            legacy_chain.chain_id,
            session_scope_sha256=_sha("legacy-session"),
            ledger_prefix_before_sha256=_sha("legacy-ledger"),
        )
        with closing(sqlite3.connect(legacy_path)) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            scope = canonical_json(
                {"schema_version": "migration-test.scope.v1", "chain_id": legacy_chain.chain_id}
            )
            connection.execute(
                "INSERT INTO provider_stage_retry_occurrence_scopes("
                "chain_id, scope_json, scope_sha256, created_at) VALUES (?, ?, ?, ?)",
                (
                    legacy_chain.chain_id,
                    scope,
                    text_sha256(scope),
                    "2026-08-10T00:00:00+00:00",
                ),
            )
            connection.commit()
            tables = (
                "provider_stage_retry_chains",
                "provider_stage_retry_occurrence_scopes",
                "provider_stage_retry_checkpoints",
                "provider_stage_retry_attempts",
                "provider_stage_retry_events",
            )
            before = {
                table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in tables
            }
            self.assertTrue(all(count > 0 for count in before.values()))

        upgraded_authority = SQLiteAuthorityStore(legacy_path)
        upgraded_store = SQLiteProviderStageRetryStore(upgraded_authority, legacy_blobs)
        self.assertEqual(upgraded_store.read(legacy_chain.chain_id).chain_id, legacy_chain.chain_id)
        self.assertEqual(upgraded_store.load_input(legacy_chain.chain_id), exact_input)
        with closing(sqlite3.connect(legacy_path)) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 20)
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
            after = {
                table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in tables
            }
            self.assertEqual(after, before)
            indexes = {
                row[0]
                for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'index'")
            }
            triggers = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'trigger'"
                )
            }
        self.assertIn("idx_provider_stage_retry_phase", indexes)
        self.assertTrue(
            {
                "provider_stage_retry_chain_identity_immutable",
                "provider_stage_retry_chain_terminal_immutable",
                "provider_stage_retry_chains_no_delete",
            }.issubset(triggers)
        )
        reader_input = b"Reader after populated v19 upgrade"
        upgraded_reader = ProviderStageRetryControllerV1(store=upgraded_store).begin(
            _identity(
                reader_input,
                stage=ProviderStage.READER,
                occurrence="reader-after-upgrade",
            ),
            reader_input,
        )
        self.assertIs(upgraded_reader.identity.stage, ProviderStage.READER)

    def test_manual_retry_is_idempotent_bounded_and_never_mints_attempt_four(self) -> None:
        _, chain_id = self._prepared()
        self._close_pretransport_attempt(chain_id, 1)
        self.assertIs(
            self.controller.recover(chain_id).action,
            ProviderStageRecoveryAction.AWAIT_MANUAL_RETRY,
        )
        with self.assertRaises(StateConflictError):
            self.controller.prepare_attempt(
                chain_id,
                session_scope_sha256=_sha("unapproved-session"),
                ledger_prefix_before_sha256=_sha("occurrence-1:ledger:1"),
            )

        barrier = threading.Barrier(2)

        def duplicate_click() -> str:
            independent = SQLiteProviderStageRetryStore(
                SQLiteAuthorityStore(self.database_path),
                TrustedLocalProtectedStageBlobStore(self.blob_root),
            )
            barrier.wait(timeout=5)
            return independent.accept_retry(
                chain_id,
                retry_action_sha256=_sha("occurrence-1:retry:2"),
                session_scope_sha256=_sha("occurrence-1:session:2"),
                ledger_prefix_before_sha256=_sha("occurrence-1:ledger:1"),
            ).chain_sha256

        with ThreadPoolExecutor(max_workers=2) as executor:
            hashes = tuple(executor.map(lambda _: duplicate_click(), range(2)))
        self.assertEqual(hashes[0], hashes[1])
        self.assertEqual(self.controller.retry_actions_accepted(chain_id), 1)
        with self.assertRaises(StateConflictError):
            self.controller.accept_retry(
                chain_id,
                retry_action_sha256=_sha("occurrence-1:retry:2"),
                session_scope_sha256=_sha("changed-session"),
                ledger_prefix_before_sha256=_sha("occurrence-1:ledger:1"),
            )

        self._close_pretransport_attempt(chain_id, 2)
        self._accept_retry(chain_id, 3)
        self._close_pretransport_attempt(chain_id, 3)
        exhausted = self.controller.status(chain_id)
        self.assertIs(exhausted.phase, ProviderStageRetryPhase.EXHAUSTED)
        self.assertEqual(exhausted.attempts_total, 3)
        self.assertEqual(self.controller.retry_actions_accepted(chain_id), 2)
        terminal = self.controller.terminal(chain_id)
        self.assertIsNotNone(terminal)
        assert terminal is not None
        self.assertEqual(terminal.attempts_total, 3)
        with self.assertRaises(StateConflictError):
            self.controller.accept_retry(
                chain_id,
                retry_action_sha256=_sha("attempt-four-action"),
                session_scope_sha256=_sha("attempt-four-session"),
                ledger_prefix_before_sha256=_sha("occurrence-1:ledger:3"),
            )
        with closing(sqlite3.connect(self.database_path)) as connection:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "INSERT INTO provider_stage_retry_attempts("
                    "chain_id, attempt_number, phase, session_scope_sha256, "
                    "ledger_prefix_before_sha256, attempt_json, attempt_sha256, "
                    "created_at, updated_at) VALUES (?, 4, 'prepared', ?, ?, '{}', ?, ?, ?)",
                    (
                        chain_id,
                        _sha("attempt-four-session"),
                        _sha("occurrence-1:ledger:3"),
                        _sha("attempt-four"),
                        "now",
                        "now",
                    ),
                )

    def test_dispatch_reservation_ambiguity_blocks_and_resolves_without_dispatch(self) -> None:
        _, chain_id = self._prepared()
        chain = self.controller.mark_dispatch_started(
            chain_id,
            attempt_number=1,
            dispatch_evidence_sha256=_sha("dispatch-1"),
            maximum_provider_operations=3,
        )
        self.assertEqual(chain.attempts[-1].provider_operations_observed, 0)
        self.assertEqual(chain.attempts[-1].provider_operations_conservative, 3)
        with self.assertRaisesRegex(StateConflictError, "reservation changed"):
            self.controller.mark_dispatch_started(
                chain_id,
                attempt_number=1,
                dispatch_evidence_sha256=_sha("dispatch-1"),
                maximum_provider_operations=2,
            )
        with closing(sqlite3.connect(self.database_path)) as connection:
            reserved = connection.execute(
                "SELECT invocation_reserved FROM provider_stage_retry_attempts "
                "WHERE chain_id = ? AND attempt_number = 1",
                (chain_id,),
            ).fetchone()[0]
        self.assertEqual(reserved, 1)
        chain = self.controller.mark_attempt_failed(
            chain_id,
            attempt_number=1,
            failure_class=ProviderStageFailureClass.DISPATCH_AMBIGUOUS,
            failure_evidence_sha256=_sha("ambiguous-1"),
            ledger_prefix_after_sha256=_sha("occurrence-1:ledger:1"),
            provider_operations_observed=0,
            provider_operations_conservative=3,
            duration_ms=7,
        )
        chain = self.controller.mark_owner_retired(
            chain_id,
            attempt_number=1,
            retirement_evidence_sha256=_sha("retired-1"),
        )
        self.assertIs(chain.phase, ProviderStageRetryPhase.BLOCKED_AMBIGUOUS)
        self.assertIsNotNone(self.controller.terminal(chain_id))
        with self.assertRaises(StateConflictError):
            self.controller.accept_retry(
                chain_id,
                retry_action_sha256=_sha("unsafe-retry"),
                session_scope_sha256=_sha("unsafe-session"),
                ledger_prefix_before_sha256=_sha("occurrence-1:ledger:1"),
            )
        resolved = self.controller.resolve_blocked_failure(
            chain_id,
            resolution_evidence_sha256=_sha("resolution-1"),
            failure_class=ProviderStageFailureClass.PROVIDER_UNAVAILABLE,
            failure_evidence_sha256=_sha("closed-failure-1"),
            ledger_prefix_after_sha256=_sha("occurrence-1:ledger:1"),
            provider_operations_observed=0,
            provider_operations_conservative=0,
            duration_ms=7,
        )
        self.assertIs(resolved.phase, ProviderStageRetryPhase.OWNER_RETIRED)
        self.assertIsNone(self.controller.terminal(chain_id))
        self._accept_retry(chain_id, 2)

    def test_known_non_retryable_terminal_is_fenced_replayed_and_never_retryable(self) -> None:
        _, chain_id = self._prepared(occurrence="non-retryable")
        self.controller.mark_dispatch_started(
            chain_id,
            attempt_number=1,
            dispatch_evidence_sha256=_sha("non-retryable:dispatch"),
            maximum_provider_operations=1,
        )
        failed = self.controller.mark_non_retryable_failed(
            chain_id,
            attempt_number=1,
            failure_class=ProviderStageFailureClass.PROVIDER_FAILURE_NOT_RETRYABLE,
            failure_evidence_sha256=_sha("non-retryable:failure"),
            ledger_prefix_after_sha256=_sha("non-retryable:ledger:closed"),
            provider_operations_observed=0,
            provider_operations_conservative=0,
            duration_ms=4,
        )
        self.assertIs(failed.phase, ProviderStageRetryPhase.AWAITING_OWNER_RETIREMENT)
        terminal_chain = self.controller.mark_owner_retired(
            chain_id,
            attempt_number=1,
            retirement_evidence_sha256=_sha("non-retryable:retired"),
        )
        self.assertIs(terminal_chain.phase, ProviderStageRetryPhase.RECOVERY_REQUIRED)
        self.assertEqual(terminal_chain.provider_operations_observed_total, 0)
        self.assertEqual(terminal_chain.provider_operations_conservative_total, 0)
        terminal = self.controller.terminal(chain_id)
        self.assertIsInstance(terminal, ProviderStageRecoveryRequiredV1)
        assert isinstance(terminal, ProviderStageRecoveryRequiredV1)
        self.assertEqual(
            terminal.final_failure_class,
            ProviderStageFailureClass.PROVIDER_FAILURE_NOT_RETRYABLE,
        )
        self.assertIsNone(terminal.block_reason)

        restarted = ProviderStageRetryControllerV1(
            store=SQLiteProviderStageRetryStore(
                SQLiteAuthorityStore(self.database_path),
                TrustedLocalProtectedStageBlobStore(self.blob_root),
            )
        )
        self.assertEqual(restarted.status(chain_id), terminal_chain)
        self.assertEqual(restarted.terminal(chain_id), terminal)
        self.assertIs(
            restarted.recover(chain_id).action,
            ProviderStageRecoveryAction.REPORT_RECOVERY_REQUIRED,
        )
        with self.assertRaises(StateConflictError):
            restarted.accept_retry(
                chain_id,
                retry_action_sha256=_sha("non-retryable:unsafe-retry"),
                session_scope_sha256=_sha("non-retryable:unsafe-session"),
                ledger_prefix_before_sha256=_sha("non-retryable:ledger:closed"),
            )

    def test_non_dispatch_conflict_requires_recovery_not_ambiguity(self) -> None:
        _, chain_id = self._prepared(occurrence="authority-conflict")
        recovery = self.controller.block_ambiguous(
            chain_id,
            reason=ProviderStageBlockReason.AUTHORITY_CHANGED,
            evidence_sha256=_sha("authority-conflict:evidence"),
        )
        self.assertIs(recovery.phase, ProviderStageRetryPhase.RECOVERY_REQUIRED)
        terminal = self.controller.terminal(chain_id)
        self.assertIsInstance(terminal, ProviderStageRecoveryRequiredV1)
        assert isinstance(terminal, ProviderStageRecoveryRequiredV1)
        self.assertEqual(terminal.block_reason, ProviderStageBlockReason.AUTHORITY_CHANGED)
        self.assertIsNone(terminal.final_failure_class)

    def test_recorder_non_retryable_terminal_preserves_accepted_story(self) -> None:
        accepted_prose = b"accepted story preserved across non-Retry Recorder failure"
        _, chain_id = self._prepared(
            stage=ProviderStage.RECORDER,
            exact_input=accepted_prose,
            occurrence="recorder-non-retryable",
        )
        self.controller.mark_dispatch_started(
            chain_id,
            attempt_number=1,
            dispatch_evidence_sha256=_sha("recorder-non-retryable:dispatch"),
            maximum_provider_operations=1,
        )
        self.controller.mark_non_retryable_failed(
            chain_id,
            attempt_number=1,
            failure_class=ProviderStageFailureClass.AUTHENTICATION_FAILED,
            failure_evidence_sha256=_sha("recorder-non-retryable:auth"),
            ledger_prefix_after_sha256=_sha("recorder-non-retryable:ledger"),
            provider_operations_observed=0,
            provider_operations_conservative=0,
            duration_ms=2,
        )
        chain = self.controller.mark_owner_retired(
            chain_id,
            attempt_number=1,
            retirement_evidence_sha256=_sha("recorder-non-retryable:retired"),
        )
        self.assertIs(chain.phase, ProviderStageRetryPhase.RECOVERY_REQUIRED)
        self.assertTrue(chain.identity.story_state_committed)
        self.assertEqual(self.controller.load_protected_input(chain_id), accepted_prose)
        terminal = self.controller.terminal(chain_id)
        self.assertIsInstance(terminal, ProviderStageRecoveryRequiredV1)
        assert isinstance(terminal, ProviderStageRecoveryRequiredV1)
        self.assertTrue(terminal.story_state_committed)

    def test_blocked_dispatch_can_resolve_to_exact_frozen_result(self) -> None:
        _, chain_id = self._prepared(occurrence="result-resolution")
        self.controller.mark_dispatch_started(
            chain_id,
            attempt_number=1,
            dispatch_evidence_sha256=_sha("result-resolution:dispatch"),
            maximum_provider_operations=1,
        )
        self.controller.mark_attempt_failed(
            chain_id,
            attempt_number=1,
            failure_class=ProviderStageFailureClass.DISPATCH_AMBIGUOUS,
            failure_evidence_sha256=_sha("result-resolution:ambiguous"),
            ledger_prefix_after_sha256=_sha("result-resolution:ledger:1"),
            provider_operations_observed=0,
            provider_operations_conservative=1,
            duration_ms=13,
        )
        self.controller.mark_owner_retired(
            chain_id,
            attempt_number=1,
            retirement_evidence_sha256=_sha("result-resolution:retired"),
        )
        exact_result = b"recovered exact provider result"
        result = self.controller.resolve_blocked_result(
            chain_id,
            resolution_evidence_sha256=_sha("result-resolution:proof"),
            exact_result=exact_result,
            result_evidence_sha256=_sha("result-resolution:result"),
            ledger_prefix_after_sha256=_sha("result-resolution:ledger:1-closed"),
            provider_operations_observed=1,
            provider_operations_conservative=1,
            duration_ms=13,
            input_tokens=10,
            cached_input_tokens=4,
            output_tokens=3,
            reasoning_tokens=2,
        )
        self.assertIs(result.phase, ProviderStageRetryPhase.RESULT_FROZEN)
        self.assertEqual(self.controller.load_protected_result(chain_id), exact_result)

    def test_recorder_exhaustion_projects_repair_and_preserves_accepted_input(self) -> None:
        accepted_prose = b"accepted story prose already committed"
        _, chain_id = self._prepared(
            stage=ProviderStage.RECORDER,
            exact_input=accepted_prose,
            occurrence="recorder",
        )
        for attempt_number in range(1, 4):
            self._close_pretransport_attempt(
                chain_id,
                attempt_number,
                occurrence="recorder",
            )
            if attempt_number < 3:
                self._accept_retry(
                    chain_id,
                    attempt_number + 1,
                    occurrence="recorder",
                )
        chain = self.controller.status(chain_id)
        self.assertIs(
            chain.phase,
            ProviderStageRetryPhase.RECORDING_REPAIR_REQUIRED,
        )
        self.assertIsNone(self.controller.terminal(chain_id))
        self.assertEqual(self.controller.load_protected_input(chain_id), accepted_prose)
        recovery = self.controller.recover(chain_id)
        self.assertIs(
            recovery.action,
            ProviderStageRecoveryAction.REPORT_RECORDING_REPAIR_REQUIRED,
        )

    def test_third_recorder_ambiguity_requires_closed_resolution_before_repair(self) -> None:
        _, chain_id = self._prepared(
            stage=ProviderStage.RECORDER,
            occurrence="recorder-ambiguous",
        )
        for attempt_number in (1, 2):
            self._close_pretransport_attempt(
                chain_id,
                attempt_number,
                occurrence="recorder-ambiguous",
            )
            self._accept_retry(
                chain_id,
                attempt_number + 1,
                occurrence="recorder-ambiguous",
            )
        self.controller.mark_dispatch_started(
            chain_id,
            attempt_number=3,
            dispatch_evidence_sha256=_sha("recorder-ambiguous:dispatch:3"),
            maximum_provider_operations=1,
        )
        self.controller.mark_attempt_failed(
            chain_id,
            attempt_number=3,
            failure_class=ProviderStageFailureClass.DISPATCH_AMBIGUOUS,
            failure_evidence_sha256=_sha("recorder-ambiguous:failure:3"),
            ledger_prefix_after_sha256=_sha("recorder-ambiguous:ledger:3"),
            provider_operations_observed=0,
            provider_operations_conservative=1,
            duration_ms=21,
        )
        blocked = self.controller.mark_owner_retired(
            chain_id,
            attempt_number=3,
            retirement_evidence_sha256=_sha("recorder-ambiguous:retired:3"),
        )
        self.assertIs(blocked.phase, ProviderStageRetryPhase.BLOCKED_AMBIGUOUS)
        repair = self.controller.resolve_blocked_recording_repair(
            chain_id,
            resolution_evidence_sha256=_sha("recorder-ambiguous:resolution:3"),
            failure_class=ProviderStageFailureClass.PROVIDER_UNAVAILABLE,
            failure_evidence_sha256=_sha("recorder-ambiguous:closed:3"),
            ledger_prefix_after_sha256=_sha("recorder-ambiguous:ledger:3"),
            provider_operations_observed=0,
            provider_operations_conservative=0,
            duration_ms=21,
        )
        self.assertIs(
            repair.phase,
            ProviderStageRetryPhase.RECORDING_REPAIR_REQUIRED,
        )

    def test_result_blob_crash_is_restart_idempotent_before_downstream(self) -> None:
        _, chain_id = self._prepared(occurrence="result-crash")
        self.controller.mark_dispatch_started(
            chain_id,
            attempt_number=1,
            dispatch_evidence_sha256=_sha("result-crash:dispatch"),
            maximum_provider_operations=1,
        )
        crashing = _CrashAfterResultBlobStore(self.authority, self.blobs)
        exact_result = b"protected exact result after crash"
        with self.assertRaisesRegex(RuntimeError, "simulated crash"):
            crashing.freeze_result(
                chain_id,
                attempt_number=1,
                exact_result=exact_result,
                result_evidence_sha256=_sha("result-crash:result"),
                ledger_prefix_after_sha256=_sha("result-crash:ledger:1"),
                provider_operations_observed=1,
                provider_operations_conservative=1,
                duration_ms=17,
            )
        self.assertIs(
            self.controller.status(chain_id).phase,
            ProviderStageRetryPhase.DISPATCH_STARTED,
        )
        restarted = SQLiteProviderStageRetryStore(
            SQLiteAuthorityStore(self.database_path),
            TrustedLocalProtectedStageBlobStore(self.blob_root),
        )
        result = restarted.freeze_result(
            chain_id,
            attempt_number=1,
            exact_result=exact_result,
            result_evidence_sha256=_sha("result-crash:result"),
            ledger_prefix_after_sha256=_sha("result-crash:ledger:1"),
            provider_operations_observed=1,
            provider_operations_conservative=1,
            duration_ms=17,
        )
        self.assertIs(result.phase, ProviderStageRetryPhase.RESULT_FROZEN)
        self.assertEqual(restarted.load_result(chain_id), exact_result)
        intent = _sha("result-crash:downstream-intent")
        receipt = _sha("result-crash:downstream-receipt")
        self.assertEqual(
            restarted.freeze_downstream_intent(
                chain_id,
                downstream_intent_sha256=intent,
            ),
            restarted.freeze_downstream_intent(
                chain_id,
                downstream_intent_sha256=intent,
            ),
        )
        restarted.bind_downstream(chain_id, downstream_evidence_sha256=receipt)
        succeeded = restarted.mark_succeeded(chain_id)
        self.assertEqual(restarted.mark_succeeded(chain_id), succeeded)

    def test_adult_exact_bytes_never_enter_sqlite_wal_or_safe_dto(self) -> None:
        _, chain_id = self._prepared(
            stage=ProviderStage.ADULT_SCENE,
            exact_input=_ADULT_INPUT_SENTINEL,
            occurrence="adult-scene",
        )
        self.controller.mark_dispatch_started(
            chain_id,
            attempt_number=1,
            dispatch_evidence_sha256=_sha("adult-scene:dispatch"),
            maximum_provider_operations=1,
        )
        chain = self.controller.freeze_result(
            chain_id,
            attempt_number=1,
            exact_result=_ADULT_RESULT_SENTINEL,
            result_evidence_sha256=_sha("adult-scene:result"),
            ledger_prefix_after_sha256=_sha("adult-scene:ledger:1"),
            provider_operations_observed=1,
            provider_operations_conservative=1,
            duration_ms=31,
            input_tokens=20,
            cached_input_tokens=10,
            output_tokens=8,
            reasoning_tokens=4,
        )
        safe_json = canonical_json(chain.to_payload()).encode("utf-8")
        self.assertNotIn(_ADULT_INPUT_SENTINEL, safe_json)
        self.assertNotIn(_ADULT_RESULT_SENTINEL, safe_json)
        self.assertNotIn(str(self.blob_root).encode("utf-8"), safe_json)
        for suffix in ("", "-wal", "-shm"):
            path = Path(f"{self.database_path}{suffix}")
            if path.exists():
                data = path.read_bytes()
                self.assertNotIn(_ADULT_INPUT_SENTINEL, data)
                self.assertNotIn(_ADULT_RESULT_SENTINEL, data)
        self.assertEqual(self.controller.load_protected_input(chain_id), _ADULT_INPUT_SENTINEL)
        self.assertEqual(
            self.controller.load_protected_result(chain_id),
            _ADULT_RESULT_SENTINEL,
        )

    def test_scope_binding_drift_conflicts_without_resetting_attempt_budget(self) -> None:
        exact_input = b"stable occurrence input"

        def scope(
            *,
            generation_id: str = "generation-1",
            accepted_state: str = "accepted-1",
            input_bytes: bytes = exact_input,
        ) -> ProviderStageRetryOccurrenceScopeV1:
            return ProviderStageRetryOccurrenceScopeV1.create(
                world_id="world-1",
                branch_id="branch-1",
                request_id="request-1",
                generation_id=generation_id,
                stage=ProviderStage.WRITER,
                stage_ordinal=1,
                accepted_state_sha256=_sha(accepted_state),
                exact_input=input_bytes,
                authority_binding={"branch_state_version": accepted_state},
            )

        baseline = scope()
        self.store.begin(baseline.identity, exact_input)
        changed_head = scope(accepted_state="accepted-2")
        changed_input = scope(input_bytes=b"drifted occurrence input")
        self.assertEqual(changed_head.identity.chain_id, baseline.identity.chain_id)
        self.assertEqual(changed_input.identity.chain_id, baseline.identity.chain_id)
        with self.assertRaises(StateConflictError):
            self.store.begin(changed_head.identity, exact_input)
        with self.assertRaises(StateConflictError):
            self.store.begin(changed_input.identity, b"drifted occurrence input")

        next_generation = scope(generation_id="generation-2")
        self.assertNotEqual(next_generation.identity.chain_id, baseline.identity.chain_id)
        self.store.begin(next_generation.identity, exact_input)

    def test_pretransport_failure_rejects_ambiguous_class_without_mutation(self) -> None:
        _, chain_id = self._prepared(occurrence="pretransport")
        with self.assertRaises(ContractValidationError):
            self.controller.mark_pretransport_failed(
                chain_id,
                attempt_number=1,
                failure_class=ProviderStageFailureClass.DISPATCH_AMBIGUOUS,
                failure_evidence_sha256=_sha("pretransport:failure"),
                ledger_prefix_after_sha256=_sha("pretransport:ledger:1"),
                duration_ms=1,
            )
        state = self.controller.status(chain_id)
        self.assertIs(state.phase, ProviderStageRetryPhase.ATTEMPT_PREPARED)
        with closing(sqlite3.connect(self.database_path)) as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM provider_stage_retry_events WHERE chain_id = ?",
                (chain_id,),
            ).fetchone()[0]
        self.assertEqual(count, 2)
        self.assertEqual(
            json.loads(canonical_json(state.to_payload()))["attempts"][0][
                "provider_operations_conservative"
            ],
            0,
        )


if __name__ == "__main__":
    unittest.main()
