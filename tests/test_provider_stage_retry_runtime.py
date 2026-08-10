from __future__ import annotations

import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from cera.errors import ContractValidationError, StateConflictError
from cera.generated.provider_stage_retry_contracts_v1 import (
    ProviderStageRetryContractError,
)
from cera.pi_scene.provider_stage_retry import (
    ProviderStage,
    ProviderStageAttemptV1,
    ProviderStageFailureClass,
    ProviderStageRetryChainV1,
    ProviderStageRetryPhase,
)
from cera.pi_scene.provider_stage_retry_blob import TrustedLocalProtectedStageBlobStore
from cera.pi_scene.provider_stage_retry_executor import (
    ProviderStageAmbiguityResolutionV1,
    ProviderStageAttemptMetricsV1,
    ProviderStageAttemptOutcomeV1,
    ProviderStageClosedFailureV1,
    ProviderStageDispatchAmbiguousV1,
    ProviderStageNonRetryableFailureV1,
    ProviderStageSemanticDisposition,
    ProviderStageSuccessfulResultV1,
)
from cera.pi_scene.provider_stage_retry_packets import ProviderStageFrozenPacketV1
from cera.pi_scene.provider_stage_retry_runtime import (
    ProviderStageRetryRuntimeServiceV1,
    ProviderStageRuntimeAdapterRegistryV1,
    ProviderStageRuntimeAdapterV1,
    backend_action_from_envelope,
)
from cera.pi_scene.provider_stage_retry_scope import (
    ProviderStageRetryOccurrenceScopeV1,
)
from cera.serialization import bytes_sha256, canonical_bytes, text_sha256
from cera.storage.sqlite_store import SQLiteAuthorityStore


def _sha(label: str) -> str:
    return text_sha256(label)


def _metrics(label: str) -> ProviderStageAttemptMetricsV1:
    return ProviderStageAttemptMetricsV1(
        ledger_prefix_after_sha256=_sha(f"ledger-after:{label}"),
        provider_operations_observed=1,
        provider_operations_conservative=1,
        duration_ms=13,
    )


def _success(label: str) -> ProviderStageSuccessfulResultV1:
    return ProviderStageSuccessfulResultV1(
        exact_result=f"exact-result:{label}".encode(),
        result_evidence_sha256=_sha(f"result-evidence:{label}"),
        metrics=_metrics(label),
        semantic_disposition=ProviderStageSemanticDisposition.NOT_APPLICABLE,
    )


def _retryable_failure(label: str) -> ProviderStageClosedFailureV1:
    return ProviderStageClosedFailureV1(
        failure_class=ProviderStageFailureClass.PROVIDER_UNAVAILABLE,
        failure_evidence_sha256=_sha(f"failure-evidence:{label}"),
        metrics=_metrics(label),
    )


def _ambiguous(label: str) -> ProviderStageDispatchAmbiguousV1:
    return ProviderStageDispatchAmbiguousV1(
        failure_evidence_sha256=_sha(f"ambiguity-evidence:{label}"),
        metrics=_metrics(label),
    )


def _non_retryable_failure(label: str) -> ProviderStageNonRetryableFailureV1:
    return ProviderStageNonRetryableFailureV1(
        failure_class=ProviderStageFailureClass.AUTHENTICATION_FAILED,
        failure_evidence_sha256=_sha(f"non-retryable-evidence:{label}"),
        metrics=_metrics(label),
    )


def _packet(
    stage: ProviderStage,
    *,
    private_input: str | None = None,
) -> ProviderStageFrozenPacketV1:
    packet_kind = f"test_{stage.value}"
    exact_bytes = canonical_bytes(
        {
            "schema_version": ProviderStageFrozenPacketV1.SCHEMA_VERSION,
            "stage": stage.value,
            "packet_kind": packet_kind,
            "semantic_input": {"private": private_input or f"input-for-{stage.value}"},
        }
    )
    return ProviderStageFrozenPacketV1(
        schema_version=ProviderStageFrozenPacketV1.SCHEMA_VERSION,
        stage=stage,
        packet_kind=packet_kind,
        exact_bytes=exact_bytes,
        stage_input_sha256=bytes_sha256(exact_bytes),
    )


def _scope(
    packet: ProviderStageFrozenPacketV1,
    *,
    occurrence: str = "runtime-test",
) -> ProviderStageRetryOccurrenceScopeV1:
    return ProviderStageRetryOccurrenceScopeV1.create(
        world_id="world-runtime-test",
        branch_id="branch-runtime-test",
        request_id=f"request-{occurrence}",
        generation_id=f"generation-{occurrence}",
        stage=packet.stage,
        stage_ordinal=1,
        accepted_state_sha256=_sha("accepted-state"),
        exact_input=packet.exact_bytes,
        authority_binding={"head": "accepted-head"},
    )


class _InvocationLedger:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.calls = 0

    def invoked(self) -> None:
        with self._lock:
            self.calls += 1


class _PreparedDispatch:
    def __init__(
        self,
        *,
        label: str,
        outcome: ProviderStageAttemptOutcomeV1,
        ledger: _InvocationLedger,
    ) -> None:
        self._dispatch_evidence_sha256 = _sha(f"dispatch:{label}")
        self._outcome = outcome
        self._ledger = ledger

    @property
    def dispatch_evidence_sha256(self) -> str:
        return self._dispatch_evidence_sha256

    def invoke(self) -> ProviderStageAttemptOutcomeV1:
        self._ledger.invoked()
        return self._outcome


class _Owner:
    def __init__(
        self,
        *,
        session_scope_sha256: str,
        ledger_prefix_before_sha256: str,
        expected_input: bytes,
        label: str,
        outcome: ProviderStageAttemptOutcomeV1,
        invocation_ledger: _InvocationLedger,
    ) -> None:
        self._session_scope_sha256 = session_scope_sha256
        self._ledger_prefix_before_sha256 = ledger_prefix_before_sha256
        self._expected_input = expected_input
        self._label = label
        self._outcome = outcome
        self._invocation_ledger = invocation_ledger

    @property
    def session_scope_sha256(self) -> str:
        return self._session_scope_sha256

    @property
    def ledger_prefix_before_sha256(self) -> str:
        return self._ledger_prefix_before_sha256

    @property
    def maximum_provider_operations(self) -> int:
        return 1

    def prepare(
        self,
        *,
        chain_id: str,
        attempt_number: int,
        exact_input: bytes,
    ) -> _PreparedDispatch:
        if exact_input != self._expected_input:
            raise AssertionError("runtime did not replay the exact frozen input")
        return _PreparedDispatch(
            label=f"{self._label}:{chain_id}:{attempt_number}",
            outcome=self._outcome,
            ledger=self._invocation_ledger,
        )

    def retire(
        self,
        *,
        chain_id: str,
        attempt_number: int,
        failure_class: ProviderStageFailureClass,
    ) -> str:
        return _sha(f"retire:{self._label}:{chain_id}:{attempt_number}:{failure_class.value}")


class _OwnerFactory:
    def __init__(
        self,
        *,
        stage: ProviderStage,
        outcomes: dict[int, ProviderStageAttemptOutcomeV1],
        invocation_ledger: _InvocationLedger,
    ) -> None:
        self.stage = stage
        self.outcomes = outcomes
        self.invocation_ledger = invocation_ledger
        self._creation_lock = threading.Lock()
        self.owner_creation_calls = 0
        self.initial_creation_barrier: threading.Barrier | None = None

    def _owner_created(self) -> None:
        with self._creation_lock:
            self.owner_creation_calls += 1

    def _owner(
        self,
        *,
        attempt_number: int,
        exact_input: bytes,
        session_scope_sha256: str,
        ledger_prefix_before_sha256: str,
    ) -> _Owner:
        return _Owner(
            session_scope_sha256=session_scope_sha256,
            ledger_prefix_before_sha256=ledger_prefix_before_sha256,
            expected_input=exact_input,
            label=f"{self.stage.value}:{attempt_number}",
            outcome=self.outcomes[attempt_number],
            invocation_ledger=self.invocation_ledger,
        )

    def create_initial_owner(
        self,
        *,
        scope: ProviderStageRetryOccurrenceScopeV1,
        packet: ProviderStageFrozenPacketV1,
    ) -> _Owner:
        self._owner_created()
        if scope.stage is not self.stage or packet.stage is not self.stage:
            raise AssertionError("runtime selected the wrong initial factory")
        if self.initial_creation_barrier is not None:
            self.initial_creation_barrier.wait(timeout=5)
        return self._owner(
            attempt_number=1,
            exact_input=packet.exact_bytes,
            session_scope_sha256=_sha(f"{self.stage.value}:session:1"),
            ledger_prefix_before_sha256=_sha(f"{self.stage.value}:ledger:0"),
        )

    def create_retry_owner(
        self,
        *,
        chain: ProviderStageRetryChainV1,
        exact_input: bytes,
    ) -> _Owner:
        self._owner_created()
        attempt_number = len(chain.attempts) + 1
        ledger_before = chain.attempts[-1].ledger_prefix_after_sha256
        if ledger_before is None:
            raise AssertionError("Retry owner lacks the closed prior ledger")
        return self._owner(
            attempt_number=attempt_number,
            exact_input=exact_input,
            session_scope_sha256=_sha(f"{self.stage.value}:session:{attempt_number}"),
            ledger_prefix_before_sha256=ledger_before,
        )

    def reconstruct_owner(
        self,
        *,
        chain: ProviderStageRetryChainV1,
        attempt: ProviderStageAttemptV1,
        exact_input: bytes,
    ) -> _Owner:
        self._owner_created()
        del chain
        return self._owner(
            attempt_number=attempt.attempt_number,
            exact_input=exact_input,
            session_scope_sha256=attempt.session_scope_sha256,
            ledger_prefix_before_sha256=attempt.ledger_prefix_before_sha256,
        )


class _Reconciler:
    def __init__(self) -> None:
        self.calls = 0
        self.restart_calls = 0
        self.resolution: ProviderStageAmbiguityResolutionV1 | None = None
        self.restart_outcome: ProviderStageAttemptOutcomeV1 | None = None

    def check_status(
        self,
        *,
        chain: ProviderStageRetryChainV1,
    ) -> ProviderStageAmbiguityResolutionV1 | None:
        if chain.phase is not ProviderStageRetryPhase.BLOCKED_AMBIGUOUS:
            raise AssertionError("Check Status was routed outside ambiguity")
        self.calls += 1
        return self.resolution

    def recover_interrupted_dispatch(
        self,
        *,
        chain: ProviderStageRetryChainV1,
    ) -> ProviderStageAttemptOutcomeV1:
        if chain.phase is not ProviderStageRetryPhase.DISPATCH_STARTED:
            raise AssertionError("restart recovery was routed outside dispatch custody")
        self.restart_calls += 1
        if self.restart_outcome is not None:
            return self.restart_outcome
        attempt = chain.attempts[-1]
        return ProviderStageDispatchAmbiguousV1(
            failure_evidence_sha256=_sha(
                f"restart-ambiguity:{chain.chain_id}:{attempt.attempt_number}"
            ),
            metrics=ProviderStageAttemptMetricsV1(
                ledger_prefix_after_sha256=attempt.ledger_prefix_before_sha256,
                provider_operations_observed=0,
                provider_operations_conservative=(attempt.provider_operations_conservative),
                duration_ms=0,
            ),
        )


class _BinderEffectJournal:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.effects: dict[str, str] = {}


class _Binder:
    def __init__(self, effect_journal: _BinderEffectJournal | None = None) -> None:
        self._lock = threading.Lock()
        self.effect_journal = effect_journal or _BinderEffectJournal()
        self.calls = 0
        self.crash_after_effect_once = False

    @property
    def effects(self) -> dict[str, str]:
        return self.effect_journal.effects

    def downstream_intent_sha256(
        self,
        *,
        chain: ProviderStageRetryChainV1,
        exact_result: bytes,
    ) -> str:
        return _sha(f"intent:{chain.chain_id}:{bytes_sha256(exact_result)}")

    def bind_once(
        self,
        *,
        chain: ProviderStageRetryChainV1,
        exact_result: bytes,
        downstream_intent_sha256: str,
    ) -> str:
        del exact_result
        evidence = _sha(f"bound:{chain.chain_id}:{downstream_intent_sha256}")
        with self.effect_journal._lock:
            prior = self.effect_journal.effects.setdefault(
                downstream_intent_sha256,
                evidence,
            )
            if prior != evidence:
                raise AssertionError("binder changed idempotent evidence")
        with self._lock:
            self.calls += 1
            if self.crash_after_effect_once:
                self.crash_after_effect_once = False
                raise RuntimeError("simulated process stop after downstream effect")
        return evidence


class ProviderStageRetryRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.database_path = self.root / "authority.sqlite3"
        self.blob_root = self.root / "protected"
        self.packet = _packet(ProviderStage.WRITER)
        self.scope = _scope(self.packet)
        self.invocations = _InvocationLedger()
        self.writer_factory = _OwnerFactory(
            stage=ProviderStage.WRITER,
            outcomes={1: _success("initial"), 2: _success("retry-1")},
            invocation_ledger=self.invocations,
        )
        self.writer_reconciler = _Reconciler()
        self.writer_binder = _Binder()
        self.registrations = self._registrations()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _registrations(
        self,
        *,
        writer_binder: _Binder | None = None,
    ) -> tuple[ProviderStageRuntimeAdapterV1, ...]:
        registrations: list[ProviderStageRuntimeAdapterV1] = []
        for stage in ProviderStage:
            if stage is ProviderStage.WRITER:
                factory = self.writer_factory
                reconciler = self.writer_reconciler
                binder = self.writer_binder if writer_binder is None else writer_binder
            else:
                factory = _OwnerFactory(
                    stage=stage,
                    outcomes={1: _success(stage.value)},
                    invocation_ledger=_InvocationLedger(),
                )
                reconciler = _Reconciler()
                binder = _Binder()
            registrations.append(
                ProviderStageRuntimeAdapterV1(
                    stage=stage,
                    owner_factory=factory,
                    ambiguity_reconciler=reconciler,
                    downstream_binder=binder,
                )
            )
        return tuple(registrations)

    def _service(
        self,
        registrations: tuple[ProviderStageRuntimeAdapterV1, ...] | None = None,
    ) -> ProviderStageRetryRuntimeServiceV1:
        return ProviderStageRetryRuntimeServiceV1(
            authority_store=SQLiteAuthorityStore(self.database_path),
            protected_blob_store=TrustedLocalProtectedStageBlobStore(self.blob_root),
            registrations=self.registrations if registrations is None else registrations,
        )

    @staticmethod
    def _claim_interrupted_dispatch(
        service: ProviderStageRetryRuntimeServiceV1,
        *,
        scope: ProviderStageRetryOccurrenceScopeV1,
        packet: ProviderStageFrozenPacketV1,
    ) -> ProviderStageRetryChainV1:
        chain = service.store.begin(scope.identity, packet.exact_bytes)
        chain = service.store.prepare_attempt(
            chain.chain_id,
            session_scope_sha256=_sha(f"{scope.identity.chain_id}:session:1"),
            ledger_prefix_before_sha256=_sha(f"{scope.identity.chain_id}:ledger:0"),
        )
        chain = service.store.mark_dispatch_started(
            chain.chain_id,
            attempt_number=1,
            dispatch_evidence_sha256=_sha(f"{scope.identity.chain_id}:dispatch"),
            maximum_provider_operations=1,
        )
        service.remember_scope(scope)
        return chain

    def test_registry_requires_one_adapter_for_each_closed_stage(self) -> None:
        with self.assertRaisesRegex(ContractValidationError, "not exhaustive"):
            ProviderStageRuntimeAdapterRegistryV1(self.registrations[:-1])
        with self.assertRaisesRegex(ContractValidationError, "duplicated"):
            ProviderStageRuntimeAdapterRegistryV1((*self.registrations, self.registrations[0]))

    def test_success_is_finalized_once_under_concurrent_service_calls(self) -> None:
        service = self._service()
        chain = service.start_initial(scope=self.scope, packet=self.packet)

        self.assertEqual(chain.phase, ProviderStageRetryPhase.RESULT_FROZEN)
        self.assertEqual(self.invocations.calls, 1)
        before = service.canonical_status(chain_id=chain.chain_id, scope=self.scope)
        self.assertEqual(before["status"]["state"], "in_progress")
        self.assertEqual(before["actions"], [])

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = tuple(
                pool.map(lambda _: service.finalize_result_once(chain.chain_id), range(8))
            )

        self.assertEqual(set(results), {b"exact-result:initial"})
        self.assertEqual(self.writer_binder.calls, 1)
        self.assertEqual(
            service.read_chain(chain.chain_id).phase,
            ProviderStageRetryPhase.SUCCEEDED,
        )
        after = service.canonical_status(chain_id=chain.chain_id)
        self.assertEqual(after["status"]["state"], "succeeded")

    def test_cross_process_binder_replay_does_not_duplicate_downstream_effect(self) -> None:
        first = self._service()
        chain = first.start_initial(scope=self.scope, packet=self.packet)
        self.writer_binder.crash_after_effect_once = True

        with self.assertRaisesRegex(RuntimeError, "simulated process stop"):
            first.finalize_result_once(chain.chain_id)
        interrupted = first.read_chain(chain.chain_id)
        self.assertEqual(
            interrupted.phase,
            ProviderStageRetryPhase.DOWNSTREAM_INTENT_FROZEN,
        )
        self.assertEqual(len(self.writer_binder.effects), 1)

        restarted_binder = _Binder(self.writer_binder.effect_journal)
        restarted = self._service(
            self._registrations(writer_binder=restarted_binder),
        )
        exact_result = restarted.finalize_result_once(chain.chain_id)
        self.assertEqual(exact_result, b"exact-result:initial")
        self.assertEqual(len(self.writer_binder.effects), 1)
        self.assertEqual(self.writer_binder.calls, 1)
        self.assertEqual(restarted_binder.calls, 1)
        self.assertEqual(
            restarted.read_chain(chain.chain_id).phase,
            ProviderStageRetryPhase.SUCCEEDED,
        )

    def test_duplicate_initial_service_calls_invoke_provider_once(self) -> None:
        first = self._service()
        second = self._service()
        self.writer_factory.initial_creation_barrier = threading.Barrier(2)

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = (
                pool.submit(first.start_initial, scope=self.scope, packet=self.packet),
                pool.submit(second.start_initial, scope=self.scope, packet=self.packet),
            )
            tuple(future.result() for future in futures)

        chain_id = self.scope.identity.chain_id
        self.assertEqual(self.writer_factory.owner_creation_calls, 2)
        self.assertEqual(self.invocations.calls, 1)
        self.assertEqual(
            first.read_chain(chain_id).phase,
            ProviderStageRetryPhase.RESULT_FROZEN,
        )
        safe_authority_bytes = self.database_path.read_bytes()
        wal_path = Path(str(self.database_path) + "-wal")
        if wal_path.exists():
            safe_authority_bytes += wal_path.read_bytes()
        self.assertNotIn(b"input-for-writer", safe_authority_bytes)

    def test_identical_initial_request_repairs_crash_between_chain_and_scope(self) -> None:
        first = self._service()
        frozen = first.store.begin(self.scope.identity, self.packet.exact_bytes)
        self.assertEqual(frozen.phase, ProviderStageRetryPhase.INPUT_FROZEN)
        self.assertEqual(self.invocations.calls, 0)

        restarted = self._service()
        with self.assertRaisesRegex(StateConflictError, "must be reconstructed"):
            restarted.canonical_status(chain_id=frozen.chain_id)
        recovered = restarted.start_initial(scope=self.scope, packet=self.packet)

        self.assertEqual(recovered.phase, ProviderStageRetryPhase.RESULT_FROZEN)
        self.assertEqual(self.invocations.calls, 1)
        second_restart = self._service()
        self.assertEqual(second_restart.scope_for_chain(frozen.chain_id), self.scope)
        envelope = second_restart.canonical_status(chain_id=frozen.chain_id)
        self.assertEqual(envelope["status"]["state"], "in_progress")

    def test_same_stage_locator_rejects_input_and_head_drift_without_new_budget(self) -> None:
        service = self._service()
        original = service.start_initial(scope=self.scope, packet=self.packet)
        changed_packet = _packet(
            ProviderStage.WRITER,
            private_input="changed-private-input",
        )
        changed_input_scope = _scope(changed_packet)

        self.assertEqual(changed_input_scope.identity.chain_id, original.chain_id)
        with self.assertRaisesRegex(StateConflictError, "input changed"):
            service.start_initial(scope=changed_input_scope, packet=changed_packet)

        changed_head_scope = ProviderStageRetryOccurrenceScopeV1.create(
            world_id="world-runtime-test",
            branch_id="branch-runtime-test",
            request_id="request-runtime-test",
            generation_id="generation-runtime-test",
            stage=self.packet.stage,
            stage_ordinal=1,
            accepted_state_sha256=_sha("different-accepted-state"),
            exact_input=self.packet.exact_bytes,
            authority_binding={"head": "different-accepted-head"},
        )
        self.assertEqual(changed_head_scope.identity.chain_id, original.chain_id)
        with self.assertRaisesRegex(StateConflictError, "authority changed"):
            service.start_initial(scope=changed_head_scope, packet=self.packet)

        self.assertEqual(self.invocations.calls, 1)
        self.assertEqual(service.read_chain(original.chain_id).attempts_total, 1)

    def test_restart_status_is_provider_free_and_prepared_resume_invokes_once(self) -> None:
        first = self._service()
        chain = first.store.begin(self.scope.identity, self.packet.exact_bytes)
        chain = first.store.prepare_attempt(
            chain.chain_id,
            session_scope_sha256=_sha("writer:session:1"),
            ledger_prefix_before_sha256=_sha("writer:ledger:0"),
        )
        first.remember_scope(self.scope)
        self.assertEqual(chain.phase, ProviderStageRetryPhase.ATTEMPT_PREPARED)

        restarted_a = self._service()
        restarted_b = self._service()
        replay = restarted_a.start_initial(scope=self.scope, packet=self.packet)
        self.assertEqual(replay.phase, ProviderStageRetryPhase.ATTEMPT_PREPARED)
        self.assertEqual(self.invocations.calls, 0)
        envelope = restarted_a.canonical_status(chain_id=chain.chain_id)
        self.assertEqual(envelope["status"]["state"], "in_progress")
        self.assertEqual(self.invocations.calls, 0)
        second_envelope = restarted_b.canonical_status(chain_id=chain.chain_id)
        self.assertEqual(second_envelope, envelope)
        self.assertEqual(self.invocations.calls, 0)

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = (
                pool.submit(restarted_a.resume_prepared, chain.chain_id),
                pool.submit(restarted_b.resume_prepared, chain.chain_id),
            )
            tuple(future.result() for future in futures)

        self.assertEqual(self.invocations.calls, 1)
        self.assertEqual(
            restarted_a.read_chain(chain.chain_id).phase,
            ProviderStageRetryPhase.RESULT_FROZEN,
        )

    def test_interrupted_dispatch_recovers_each_closed_outcome_without_invoke(self) -> None:
        cases: tuple[
            tuple[str, ProviderStageAttemptOutcomeV1, ProviderStageRetryPhase],
            ...,
        ] = (
            ("success", _success("restart-success"), ProviderStageRetryPhase.RESULT_FROZEN),
            (
                "retryable",
                _retryable_failure("restart-retryable"),
                ProviderStageRetryPhase.OWNER_RETIRED,
            ),
            (
                "non-retry",
                _non_retryable_failure("restart-non-retry"),
                ProviderStageRetryPhase.RECOVERY_REQUIRED,
            ),
            (
                "unresolved",
                _ambiguous("restart-unresolved"),
                ProviderStageRetryPhase.BLOCKED_AMBIGUOUS,
            ),
        )
        service = self._service()
        for label, outcome, expected_phase in cases:
            with self.subTest(label=label):
                packet = _packet(ProviderStage.WRITER, private_input=f"restart-{label}")
                scope = _scope(packet, occurrence=f"restart-{label}")
                claimed = self._claim_interrupted_dispatch(
                    service,
                    scope=scope,
                    packet=packet,
                )
                self.assertIs(claimed.phase, ProviderStageRetryPhase.DISPATCH_STARTED)
                self.writer_reconciler.restart_outcome = outcome

                recovered = service.recover_incomplete(claimed.chain_id)

                self.assertIs(recovered.phase, expected_phase)
                self.assertEqual(self.invocations.calls, 0)
        self.writer_reconciler.restart_outcome = None

    def test_interrupted_dispatch_recovery_race_never_invokes_or_redispatches(self) -> None:
        first = self._service()
        second = self._service()
        packet = _packet(ProviderStage.WRITER, private_input="restart-race")
        scope = _scope(packet, occurrence="restart-race")
        claimed = self._claim_interrupted_dispatch(first, scope=scope, packet=packet)
        self.writer_reconciler.restart_outcome = _ambiguous("restart-race")

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = tuple(
                pool.map(
                    lambda service: service.recover_incomplete(claimed.chain_id),
                    (first, second),
                )
            )

        self.assertEqual(
            {result.phase for result in results},
            {ProviderStageRetryPhase.BLOCKED_AMBIGUOUS},
        )
        self.assertEqual(self.invocations.calls, 0)
        self.assertEqual(first.read_chain(claimed.chain_id).attempts_total, 1)

    def test_failed_owner_retirement_replays_after_crash_without_invoke(self) -> None:
        first = self._service()
        packet = _packet(ProviderStage.WRITER, private_input="retirement-crash")
        scope = _scope(packet, occurrence="retirement-crash")
        claimed = self._claim_interrupted_dispatch(first, scope=scope, packet=packet)
        awaiting = first.store.mark_attempt_failed(
            claimed.chain_id,
            attempt_number=1,
            failure_class=ProviderStageFailureClass.PROVIDER_UNAVAILABLE,
            failure_evidence_sha256=_sha("retirement-crash:failure"),
            ledger_prefix_after_sha256=_sha("retirement-crash:ledger-after"),
            provider_operations_observed=1,
            provider_operations_conservative=1,
            duration_ms=7,
        )
        self.assertIs(
            awaiting.phase,
            ProviderStageRetryPhase.AWAITING_OWNER_RETIREMENT,
        )

        with patch.object(
            first.store,
            "mark_owner_retired",
            side_effect=RuntimeError("simulated crash before SQLite retirement mark"),
        ):
            with self.assertRaisesRegex(RuntimeError, "simulated crash"):
                first.recover_incomplete(claimed.chain_id)
        self.assertIs(
            first.read_chain(claimed.chain_id).phase,
            ProviderStageRetryPhase.AWAITING_OWNER_RETIREMENT,
        )

        restarted = self._service()
        recovered = restarted.recover_incomplete(claimed.chain_id)
        self.assertIs(recovered.phase, ProviderStageRetryPhase.OWNER_RETIRED)
        self.assertEqual(self.invocations.calls, 0)

    def test_manual_retry_is_backend_issued_and_never_automatic(self) -> None:
        self.writer_factory.outcomes[1] = _retryable_failure("attempt-1")
        first = self._service()
        second = self._service()
        chain = first.start_initial(scope=self.scope, packet=self.packet)

        self.assertEqual(chain.phase, ProviderStageRetryPhase.OWNER_RETIRED)
        self.assertEqual(self.invocations.calls, 1)
        envelope = first.canonical_status(chain_id=chain.chain_id, scope=self.scope)
        action = backend_action_from_envelope(envelope)
        self.assertEqual(action["action_kind"], "provider_retry")
        first.canonical_status(chain_id=chain.chain_id)
        self.assertEqual(self.invocations.calls, 1)

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = (
                pool.submit(first.execute_manual_retry, action),
                pool.submit(second.execute_manual_retry, action),
            )
            tuple(future.result() for future in futures)

        self.assertEqual(self.invocations.calls, 2)
        self.assertEqual(
            first.read_chain(chain.chain_id).phase,
            ProviderStageRetryPhase.RESULT_FROZEN,
        )
        first.execute_manual_retry(action)
        self.assertEqual(self.invocations.calls, 2)

    def test_check_status_reconciles_without_another_provider_dispatch(self) -> None:
        self.writer_factory.outcomes[1] = _ambiguous("attempt-1")
        service = self._service()
        chain = service.start_initial(scope=self.scope, packet=self.packet)

        self.assertEqual(chain.phase, ProviderStageRetryPhase.BLOCKED_AMBIGUOUS)
        self.assertEqual(self.invocations.calls, 1)
        envelope = service.canonical_status(chain_id=chain.chain_id, scope=self.scope)
        action = backend_action_from_envelope(envelope)
        self.assertEqual(action["action_kind"], "check_status")

        still_blocked = service.check_status(action)
        self.assertEqual(still_blocked.phase, ProviderStageRetryPhase.BLOCKED_AMBIGUOUS)
        self.assertEqual(self.writer_reconciler.calls, 1)
        self.assertEqual(self.invocations.calls, 1)

        self.writer_reconciler.resolution = ProviderStageAmbiguityResolutionV1(
            resolution_evidence_sha256=_sha("resolution"),
            disposition=_success("reconciled"),
        )
        restarted = self._service()
        with ThreadPoolExecutor(max_workers=2) as pool:
            resolved = tuple(
                future.result()
                for future in (
                    pool.submit(service.check_status, action),
                    pool.submit(restarted.check_status, action),
                )
            )
        self.assertEqual(
            {chain.phase for chain in resolved},
            {ProviderStageRetryPhase.RESULT_FROZEN},
        )
        self.assertEqual(self.writer_reconciler.calls, 2)
        self.assertEqual(self.invocations.calls, 1)
        self.assertEqual(service.load_exact_result(chain.chain_id), b"exact-result:reconciled")

    def test_non_retryable_failure_is_read_only_recovery_without_retry(self) -> None:
        self.writer_factory.outcomes[1] = _non_retryable_failure("authentication")
        service = self._service()
        chain = service.start_initial(scope=self.scope, packet=self.packet)

        self.assertEqual(chain.phase, ProviderStageRetryPhase.RECOVERY_REQUIRED)
        self.assertEqual(self.invocations.calls, 1)
        envelope = service.canonical_status(chain_id=chain.chain_id, scope=self.scope)
        self.assertEqual(envelope["status"]["state"], "recovery_required")
        self.assertEqual(envelope["actions"], [])
        with self.assertRaisesRegex(StateConflictError, "no sole backend action"):
            backend_action_from_envelope(envelope)
        replay = service.start_initial(scope=self.scope, packet=self.packet)
        self.assertEqual(replay.phase, ProviderStageRetryPhase.RECOVERY_REQUIRED)
        self.assertEqual(self.invocations.calls, 1)

    def test_wrong_action_family_never_reaches_an_owner_factory(self) -> None:
        service = self._service()
        chain = service.start_initial(scope=self.scope, packet=self.packet)
        service.finalize_result_once(chain.chain_id)
        envelope = service.canonical_status(chain_id=chain.chain_id, scope=self.scope)
        with self.assertRaisesRegex(StateConflictError, "no sole backend action"):
            backend_action_from_envelope(envelope)
        with self.assertRaises(ProviderStageRetryContractError):
            service.check_status(
                {
                    "schema_version": "cera.provider_stage_retry_action.v1",
                    "action_id": "stage-action-" + "a" * 64,
                    "chain_id": chain.chain_id,
                    "action_family": "provider_stage_control",
                    "action_kind": "explicit_recovery",
                    "automatic": False,
                    "provider_dispatch_authorized": False,
                    "consumes_retry_action": False,
                    "retry_action_ordinal": None,
                    "whole_request_replay_authorized": False,
                    "provider_substitution_authorized": False,
                    "expected_chain_sha256": service.read_chain(chain.chain_id).chain_sha256,
                }
            )
        self.assertEqual(self.invocations.calls, 1)


if __name__ == "__main__":
    unittest.main()
