from __future__ import annotations

import unittest
from concurrent.futures import ThreadPoolExecutor
from itertools import count
from pathlib import Path
from tempfile import TemporaryDirectory

from cera.errors import ContractValidationError, StateConflictError
from cera.generated.provider_stage_retry_contracts_v1 import (
    ProviderStageRetryContractError,
    validate_provider_stage_retry_action_v1,
)
from cera.pi_scene.provider_stage_retry import (
    ProviderStage,
    ProviderStageBlockReason,
    ProviderStageFailureClass,
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
    ProviderStagePretransportFailureV1,
    ProviderStagePretransportNonRetryableFailureV1,
    ProviderStageRetryExecutorV1,
    ProviderStageSemanticDisposition,
    ProviderStageSuccessfulResultV1,
)
from cera.pi_scene.provider_stage_retry_packets import (
    ProviderStageConfigurationV1,
    ProviderStageFrozenPacketV1,
    freeze_writer_stage_packet,
)
from cera.pi_scene.provider_stage_retry_scope import (
    ProviderStageRetryOccurrenceScopeV1,
)
from cera.serialization import text_sha256
from cera.storage.provider_stage_retry_store import SQLiteProviderStageRetryStore
from cera.storage.sqlite_store import SQLiteAuthorityStore


def _sha(label: str) -> str:
    return text_sha256(label)


def _metrics(
    ledger_after: str,
    *,
    observed: int = 1,
    conservative: int = 1,
) -> ProviderStageAttemptMetricsV1:
    return ProviderStageAttemptMetricsV1(
        ledger_prefix_after_sha256=_sha(ledger_after),
        provider_operations_observed=observed,
        provider_operations_conservative=conservative,
        duration_ms=17,
    )


def _success(
    label: str,
    ledger_after: str,
    *,
    semantic_disposition: ProviderStageSemanticDisposition = (
        ProviderStageSemanticDisposition.NOT_APPLICABLE
    ),
) -> ProviderStageSuccessfulResultV1:
    return ProviderStageSuccessfulResultV1(
        exact_result=f"result:{label}".encode(),
        result_evidence_sha256=_sha(f"result-evidence:{label}"),
        metrics=_metrics(ledger_after),
        semantic_disposition=semantic_disposition,
    )


def _failure(label: str, ledger_after: str) -> ProviderStageClosedFailureV1:
    return ProviderStageClosedFailureV1(
        failure_class=ProviderStageFailureClass.PROVIDER_UNAVAILABLE,
        failure_evidence_sha256=_sha(f"failure-evidence:{label}"),
        metrics=_metrics(ledger_after),
    )


class _PreparedDispatch:
    def __init__(self, label: str, outcome: ProviderStageAttemptOutcomeV1) -> None:
        self._dispatch_evidence_sha256 = _sha(f"dispatch:{label}")
        self.outcome = outcome
        self.invoke_calls = 0

    @property
    def dispatch_evidence_sha256(self) -> str:
        return self._dispatch_evidence_sha256

    def invoke(self) -> ProviderStageAttemptOutcomeV1:
        self.invoke_calls += 1
        return self.outcome


class _Owner:
    def __init__(
        self,
        label: str,
        ledger_before: str,
        preparation: (
            _PreparedDispatch
            | ProviderStagePretransportFailureV1
            | ProviderStagePretransportNonRetryableFailureV1
        ),
        *,
        expected_input: bytes,
    ) -> None:
        self.label = label
        self._session_scope_sha256 = _sha(f"session:{label}")
        self._ledger_prefix_before_sha256 = _sha(ledger_before)
        self.preparation = preparation
        self.expected_input = expected_input
        self.prepare_calls = 0
        self.retire_calls = 0

    @property
    def session_scope_sha256(self) -> str:
        return self._session_scope_sha256

    @property
    def ledger_prefix_before_sha256(self) -> str:
        return self._ledger_prefix_before_sha256

    def prepare(
        self,
        *,
        chain_id: str,
        attempt_number: int,
        exact_input: bytes,
    ) -> (
        _PreparedDispatch
        | ProviderStagePretransportFailureV1
        | ProviderStagePretransportNonRetryableFailureV1
    ):
        del chain_id, attempt_number
        self.prepare_calls += 1
        if exact_input != self.expected_input:
            raise AssertionError("executor did not replay exact frozen input")
        return self.preparation

    def retire(
        self,
        *,
        chain_id: str,
        attempt_number: int,
        failure_class: ProviderStageFailureClass,
    ) -> str:
        self.retire_calls += 1
        return _sha(f"retired:{self.label}:{chain_id}:{attempt_number}:{failure_class.value}")


class ProviderStageRetryExecutorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        root = Path(self.temporary.name)
        authority = SQLiteAuthorityStore(root / "authority.sqlite3")
        blobs = TrustedLocalProtectedStageBlobStore(root / "protected")
        self.store = SQLiteProviderStageRetryStore(authority, blobs)
        self.executor = ProviderStageRetryExecutorV1(self.store)
        configuration = ProviderStageConfigurationV1.create(
            stage=ProviderStage.WRITER,
            model_id="deepseek-v4-flash",
            reasoning_mode="non_thinking",
            routing={"route": "ordinary"},
            content_policy_route="ordinary",
            stage_configuration={"maximum_output_tokens": 4096},
        )
        self.packet = freeze_writer_stage_packet(
            sequence_plan={"beats": ["one"]},
            realization_context={"cast": ["npc-1"]},
            configuration=configuration,
        )
        self.scope = self._scope(self.packet)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def _scope(packet: ProviderStageFrozenPacketV1) -> ProviderStageRetryOccurrenceScopeV1:
        return ProviderStageRetryOccurrenceScopeV1.create(
            world_id="world-1",
            branch_id="branch-1",
            request_id="request-1",
            generation_id="generation-1",
            stage=ProviderStage.WRITER,
            stage_ordinal=1,
            accepted_state_sha256=_sha("accepted-head"),
            exact_input=packet.exact_bytes,
            authority_binding={"branch_state_version": 4},
        )

    def _owner(
        self,
        label: str,
        ledger_before: str,
        outcome: ProviderStageAttemptOutcomeV1,
    ) -> tuple[_Owner, _PreparedDispatch]:
        dispatch = _PreparedDispatch(label, outcome)
        owner = _Owner(
            label,
            ledger_before,
            dispatch,
            expected_input=self.packet.exact_bytes,
        )
        return owner, dispatch

    def test_initial_success_and_exact_request_replay_do_not_redispatch(self) -> None:
        owner, dispatch = self._owner("attempt-1", "ledger-0", _success("one", "ledger-1"))
        chain = self.executor.execute_initial(
            scope=self.scope,
            packet=self.packet,
            owner=owner,
        )

        self.assertIs(chain.phase, ProviderStageRetryPhase.RESULT_FROZEN)
        self.assertEqual(dispatch.invoke_calls, 1)
        self.assertEqual(self.executor.load_exact_result(chain.chain_id), b"result:one")

        replay_owner, replay_dispatch = self._owner(
            "replay-must-not-run",
            "ledger-1",
            _success("wrong", "ledger-2"),
        )
        replay = self.executor.execute_initial(
            scope=self.scope,
            packet=self.packet,
            owner=replay_owner,
        )
        self.assertEqual(replay, chain)
        self.assertEqual(replay_owner.prepare_calls, 0)
        self.assertEqual(replay_dispatch.invoke_calls, 0)
        self.assertEqual(
            self.executor.status_envelope(self.scope)["status"]["state"],
            "in_progress",
        )
        succeeded = self.executor.bind_downstream_once(
            chain_id=chain.chain_id,
            downstream_intent_sha256=_sha("downstream-intent"),
            downstream_evidence_sha256=_sha("downstream-evidence"),
        )
        self.assertIs(succeeded.phase, ProviderStageRetryPhase.SUCCEEDED)
        self.assertEqual(
            self.executor.status_envelope(self.scope)["status"]["state"],
            "succeeded",
        )

    def test_pretransport_failure_consumes_attempt_but_zero_provider_operations(self) -> None:
        pretransport = ProviderStagePretransportFailureV1(
            failure_class=ProviderStageFailureClass.PROVIDER_UNAVAILABLE,
            failure_evidence_sha256=_sha("pretransport-failure"),
            ledger_prefix_after_sha256=_sha("ledger-1"),
            duration_ms=3,
        )
        owner = _Owner(
            "attempt-1",
            "ledger-0",
            pretransport,
            expected_input=self.packet.exact_bytes,
        )

        chain = self.executor.execute_initial(
            scope=self.scope,
            packet=self.packet,
            owner=owner,
        )
        envelope = self.executor.status_envelope(self.scope)

        self.assertIs(chain.phase, ProviderStageRetryPhase.OWNER_RETIRED)
        self.assertEqual(chain.attempts_total, 1)
        self.assertEqual(chain.provider_operations_observed_total, 0)
        self.assertEqual(chain.provider_operations_conservative_total, 0)
        self.assertEqual(envelope["status"]["state"], "eligible")
        self.assertEqual(envelope["status"]["retry_actions_accepted"], 0)
        self.assertEqual(envelope["actions"][0]["action_kind"], "provider_retry")

    def test_pretransport_rejects_post_transport_failure_classes(self) -> None:
        for failure_class in (
            ProviderStageFailureClass.PROVIDER_STREAM_INCOMPLETE,
            ProviderStageFailureClass.PROVIDER_COMPLETION_INCOMPLETE,
            ProviderStageFailureClass.PROVIDER_OUTPUT_INVALID,
        ):
            with self.subTest(failure_class=failure_class):
                with self.assertRaises(ContractValidationError):
                    ProviderStagePretransportFailureV1(
                        failure_class=failure_class,
                        failure_evidence_sha256=_sha("invalid-pretransport"),
                        ledger_prefix_after_sha256=_sha("ledger-not-contacted"),
                        duration_ms=1,
                    )

    def test_pretransport_non_retryable_failure_requires_explicit_recovery(self) -> None:
        pretransport = ProviderStagePretransportNonRetryableFailureV1(
            failure_class=ProviderStageFailureClass.PROVIDER_FAILURE_NOT_RETRYABLE,
            failure_evidence_sha256=_sha("pretransport-non-retryable"),
            ledger_prefix_after_sha256=_sha("ledger-not-contacted"),
            duration_ms=2,
        )
        owner = _Owner(
            "pretransport-non-retryable",
            "ledger-0",
            pretransport,
            expected_input=self.packet.exact_bytes,
        )

        terminal = self.executor.execute_initial(
            scope=self.scope,
            packet=self.packet,
            owner=owner,
        )
        envelope = self.executor.status_envelope(self.scope)

        self.assertIs(terminal.phase, ProviderStageRetryPhase.RECOVERY_REQUIRED)
        self.assertEqual(terminal.attempts_total, 1)
        self.assertEqual(terminal.provider_operations_observed_total, 0)
        self.assertEqual(terminal.provider_operations_conservative_total, 0)
        self.assertEqual(owner.prepare_calls, 1)
        self.assertEqual(owner.retire_calls, 1)
        self.assertEqual(envelope["status"]["state"], "recovery_required")
        self.assertEqual(
            envelope["status"]["failure_category"],
            "provider_failure_not_retryable",
        )
        self.assertEqual(envelope["actions"][0]["action_kind"], "explicit_recovery")
        self.assertFalse(envelope["actions"][0]["provider_dispatch_authorized"])
        with self.assertRaises(StateConflictError):
            self.executor.execute_manual_retry(action=envelope["actions"][0], owner=owner)

        replay_owner, replay_dispatch = self._owner(
            "pretransport-must-not-run",
            "ledger-0",
            _success("must-not-run", "ledger-1"),
        )
        replay = ProviderStageRetryExecutorV1(self.store).execute_initial(
            scope=self.scope,
            packet=self.packet,
            owner=replay_owner,
        )
        self.assertEqual(replay, terminal)
        self.assertEqual(replay_owner.prepare_calls, 0)
        self.assertEqual(replay_dispatch.invoke_calls, 0)

    def test_known_non_retryable_failure_requires_recovery_and_replays_after_restart(self) -> None:
        non_retryable = ProviderStageNonRetryableFailureV1(
            failure_class=ProviderStageFailureClass.PROVIDER_FAILURE_NOT_RETRYABLE,
            failure_evidence_sha256=_sha("known-non-retryable"),
            metrics=_metrics("ledger-closed-zero", observed=0, conservative=0),
        )
        owner, dispatch = self._owner("non-retryable", "ledger-0", non_retryable)
        terminal = self.executor.execute_initial(
            scope=self.scope,
            packet=self.packet,
            owner=owner,
        )

        self.assertIs(terminal.phase, ProviderStageRetryPhase.RECOVERY_REQUIRED)
        self.assertEqual(dispatch.invoke_calls, 1)
        self.assertEqual(owner.retire_calls, 1)
        self.assertEqual(terminal.attempts_total, 1)
        self.assertEqual(terminal.provider_operations_observed_total, 0)
        self.assertEqual(terminal.provider_operations_conservative_total, 0)
        envelope = self.executor.status_envelope(self.scope)
        self.assertEqual(envelope["status"]["state"], "recovery_required")
        self.assertEqual(
            envelope["status"]["failure_category"],
            "provider_failure_not_retryable",
        )
        self.assertEqual(envelope["actions"][0]["action_kind"], "explicit_recovery")
        self.assertFalse(envelope["actions"][0]["provider_dispatch_authorized"])
        self.assertFalse(envelope["actions"][0]["consumes_retry_action"])

        restarted = ProviderStageRetryExecutorV1(self.store)
        replay_owner, replay_dispatch = self._owner(
            "must-not-redispatch",
            "ledger-0",
            _success("must-not-run", "ledger-1"),
        )
        replay = restarted.execute_initial(
            scope=self.scope,
            packet=self.packet,
            owner=replay_owner,
        )
        self.assertEqual(replay, terminal)
        self.assertEqual(replay_owner.prepare_calls, 0)
        self.assertEqual(replay_dispatch.invoke_calls, 0)
        with self.assertRaises(StateConflictError):
            restarted.execute_manual_retry(
                action=envelope["actions"][0],
                owner=replay_owner,
            )

    def test_pre_attempt_authority_conflict_projects_zero_attempt_recovery(self) -> None:
        chain = self.store.begin(self.scope.identity, self.packet.exact_bytes)
        recovery = self.store.block_ambiguous(
            chain.chain_id,
            reason=ProviderStageBlockReason.AUTHORITY_CHANGED,
            evidence_sha256=_sha("pre-attempt-authority-conflict"),
        )
        self.assertIs(recovery.phase, ProviderStageRetryPhase.RECOVERY_REQUIRED)
        envelope = self.executor.status_envelope(self.scope)
        self.assertEqual(envelope["status"]["state"], "recovery_required")
        self.assertEqual(envelope["status"]["stage_attempts_total"], 0)
        self.assertEqual(envelope["status"]["retry_actions_accepted"], 0)
        self.assertEqual(envelope["status"]["failure_category"], "authority_changed")
        self.assertEqual(envelope["actions"][0]["action_kind"], "explicit_recovery")

    def test_manual_retry_is_idempotent_and_backend_forbids_attempt_four(self) -> None:
        owner1, _ = self._owner("attempt-1", "ledger-0", _failure("one", "ledger-1"))
        first = self.executor.execute_initial(
            scope=self.scope,
            packet=self.packet,
            owner=owner1,
        )
        self.assertIs(first.phase, ProviderStageRetryPhase.OWNER_RETIRED)
        action1 = self.executor.status_envelope(self.scope)["actions"][0]

        owner2, dispatch2 = self._owner(
            "attempt-2",
            "ledger-1",
            _failure("two", "ledger-2"),
        )
        second = self.executor.execute_manual_retry(action=action1, owner=owner2)
        self.assertIs(second.phase, ProviderStageRetryPhase.OWNER_RETIRED)
        self.assertEqual(dispatch2.invoke_calls, 1)

        different_owner, different_dispatch = self._owner(
            "duplicate-fresh-owner",
            "untrusted-ledger",
            _success("must-not-run", "untrusted-after"),
        )
        replay = self.executor.execute_manual_retry(action=action1, owner=different_owner)
        self.assertEqual(replay, second)
        self.assertEqual(dispatch2.invoke_calls, 1)
        self.assertEqual(different_owner.prepare_calls, 0)
        self.assertEqual(different_dispatch.invoke_calls, 0)
        action2 = self.executor.status_envelope(self.scope)["actions"][0]

        owner3, dispatch3 = self._owner(
            "attempt-3",
            "ledger-2",
            _failure("three", "ledger-3"),
        )
        exhausted = self.executor.execute_manual_retry(action=action2, owner=owner3)
        self.assertIs(exhausted.phase, ProviderStageRetryPhase.EXHAUSTED)
        self.assertEqual(exhausted.attempts_total, 3)
        self.assertEqual(self.store.retry_actions_accepted(exhausted.chain_id), 2)
        self.assertEqual(dispatch3.invoke_calls, 1)

        action2_replay = self.executor.execute_manual_retry(action=action2, owner=owner3)
        self.assertEqual(action2_replay, exhausted)
        self.assertEqual(dispatch3.invoke_calls, 1)
        envelope = self.executor.status_envelope(self.scope)
        self.assertEqual(envelope["status"]["state"], "attempts_exhausted")
        self.assertEqual(envelope["actions"][0]["action_kind"], "explicit_recovery")

        forged_fourth = dict(action2)
        forged_fourth["retry_action_ordinal"] = 3
        with self.assertRaises(ProviderStageRetryContractError):
            validate_provider_stage_retry_action_v1(forged_fourth)

    def test_late_result_is_rejected_after_owner_fence(self) -> None:
        owner, _ = self._owner("attempt-1", "ledger-0", _failure("one", "ledger-1"))
        fenced = self.executor.execute_initial(
            scope=self.scope,
            packet=self.packet,
            owner=owner,
        )

        with self.assertRaises(StateConflictError):
            self.executor.record_late_result(
                chain_id=fenced.chain_id,
                attempt_number=1,
                result=_success("late", "ledger-late"),
            )
        self.assertIs(
            self.store.read(fenced.chain_id).phase,
            ProviderStageRetryPhase.OWNER_RETIRED,
        )

    def test_ambiguity_reconciles_provider_free_without_another_dispatch(self) -> None:
        ambiguous = ProviderStageDispatchAmbiguousV1(
            failure_evidence_sha256=_sha("ambiguous"),
            metrics=_metrics("ledger-ambiguous", observed=0, conservative=1),
        )
        owner, dispatch = self._owner("attempt-1", "ledger-0", ambiguous)
        blocked = self.executor.execute_initial(
            scope=self.scope,
            packet=self.packet,
            owner=owner,
        )
        self.assertIs(blocked.phase, ProviderStageRetryPhase.BLOCKED_AMBIGUOUS)
        envelope = self.executor.status_envelope(self.scope)
        check_status = envelope["actions"][0]
        self.assertEqual(check_status["action_kind"], "check_status")

        forged_check = dict(check_status)
        forged_check["action_id"] = "stage-action-" + ("9" * 64)
        with self.assertRaises(StateConflictError):
            self.executor.reconcile_ambiguous(
                action=forged_check,
                resolution=None,
            )

        unchanged = self.executor.reconcile_ambiguous(
            action=check_status,
            resolution=None,
        )
        self.assertEqual(unchanged, blocked)
        resolution = ProviderStageAmbiguityResolutionV1(
            resolution_evidence_sha256=_sha("provider-free-resolution"),
            disposition=_success("recovered", "ledger-recovered"),
        )
        recovered = self.executor.reconcile_ambiguous(
            action=check_status,
            resolution=resolution,
        )

        self.assertIs(recovered.phase, ProviderStageRetryPhase.RESULT_FROZEN)
        self.assertEqual(dispatch.invoke_calls, 1)
        self.assertEqual(self.executor.load_exact_result(recovered.chain_id), b"result:recovered")
        self.assertEqual(
            self.executor.status_envelope(self.scope)["status"]["state"],
            "in_progress",
        )
        self.executor.bind_downstream_once(
            chain_id=recovered.chain_id,
            downstream_intent_sha256=_sha("recovered-intent"),
            downstream_evidence_sha256=_sha("recovered-evidence"),
        )
        self.assertEqual(
            self.executor.status_envelope(self.scope)["status"]["state"],
            "succeeded",
        )

    def test_semantic_rejection_is_a_successful_provider_result_not_retry(self) -> None:
        result = _success(
            "semantic-reject",
            "ledger-1",
            semantic_disposition=ProviderStageSemanticDisposition.REJECTED,
        )
        owner, dispatch = self._owner("attempt-1", "ledger-0", result)
        chain = self.executor.execute_initial(
            scope=self.scope,
            packet=self.packet,
            owner=owner,
        )
        envelope = self.executor.status_envelope(self.scope)

        self.assertIs(chain.phase, ProviderStageRetryPhase.RESULT_FROZEN)
        self.assertEqual(dispatch.invoke_calls, 1)
        self.assertEqual(envelope["status"]["state"], "in_progress")
        self.assertEqual(envelope["status"]["available_actions"], [])
        self.assertEqual(envelope["status"]["retry_actions_accepted"], 0)
        self.executor.bind_downstream_once(
            chain_id=chain.chain_id,
            downstream_intent_sha256=_sha("semantic-intent"),
            downstream_evidence_sha256=_sha("semantic-evidence"),
        )
        self.assertEqual(
            self.executor.status_envelope(self.scope)["status"]["state"],
            "succeeded",
        )

    def test_status_remains_in_progress_until_downstream_receipt_is_finalized(self) -> None:
        owner, _ = self._owner("attempt-1", "ledger-0", _success("one", "ledger-1"))
        chain = self.executor.execute_initial(
            scope=self.scope,
            packet=self.packet,
            owner=owner,
        )
        self.assertEqual(
            self.executor.status_envelope(self.scope)["status"]["state"],
            "in_progress",
        )

        chain = self.store.freeze_downstream_intent(
            chain.chain_id,
            downstream_intent_sha256=_sha("intent"),
        )
        self.assertIs(chain.phase, ProviderStageRetryPhase.DOWNSTREAM_INTENT_FROZEN)
        self.assertEqual(
            self.executor.status_envelope(self.scope)["status"]["state"],
            "in_progress",
        )
        chain = self.store.bind_downstream(
            chain.chain_id,
            downstream_evidence_sha256=_sha("effect-receipt"),
        )
        self.assertIs(chain.phase, ProviderStageRetryPhase.DOWNSTREAM_BOUND)
        self.assertEqual(
            self.executor.status_envelope(self.scope)["status"]["state"],
            "in_progress",
        )
        self.store.mark_succeeded(chain.chain_id)
        self.assertEqual(
            self.executor.status_envelope(self.scope)["status"]["state"],
            "succeeded",
        )

    def test_explicit_resume_runs_only_exact_persisted_prepared_owner(self) -> None:
        owner, dispatch = self._owner("attempt-1", "ledger-0", _success("one", "ledger-1"))
        chain = self.store.begin(self.scope.identity, self.packet.exact_bytes)
        prepared = self.store.prepare_attempt(
            chain.chain_id,
            session_scope_sha256=owner.session_scope_sha256,
            ledger_prefix_before_sha256=owner.ledger_prefix_before_sha256,
        )
        self.assertIs(prepared.phase, ProviderStageRetryPhase.ATTEMPT_PREPARED)

        wrong_owner, _ = self._owner(
            "wrong-owner",
            "ledger-0",
            _success("wrong", "ledger-1"),
        )
        with self.assertRaises(StateConflictError):
            self.executor.resume_prepared_attempt(
                chain_id=prepared.chain_id,
                owner=wrong_owner,
            )
        resumed = self.executor.resume_prepared_attempt(
            chain_id=prepared.chain_id,
            owner=owner,
        )
        self.assertIs(resumed.phase, ProviderStageRetryPhase.RESULT_FROZEN)
        self.assertEqual(dispatch.invoke_calls, 1)

    def test_two_executors_claim_one_manual_dispatch(self) -> None:
        owner1, _ = self._owner("attempt-1", "ledger-0", _failure("one", "ledger-1"))
        self.executor.execute_initial(
            scope=self.scope,
            packet=self.packet,
            owner=owner1,
        )
        action = self.executor.status_envelope(self.scope)["actions"][0]
        owner2, dispatch2 = self._owner(
            "attempt-2",
            "ledger-1",
            _success("concurrent", "ledger-2"),
        )
        nonce_counter = count(1)
        first = ProviderStageRetryExecutorV1(
            self.store,
            dispatch_nonce_factory=lambda: f"nonce-{next(nonce_counter)}",
        )
        second = ProviderStageRetryExecutorV1(
            self.store,
            dispatch_nonce_factory=lambda: f"nonce-{next(nonce_counter)}",
        )

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = (
                pool.submit(first.execute_manual_retry, action=action, owner=owner2),
                pool.submit(second.execute_manual_retry, action=action, owner=owner2),
            )
            for future in futures:
                future.result()

        durable = self.store.read(self.scope.identity.chain_id)
        self.assertIs(durable.phase, ProviderStageRetryPhase.RESULT_FROZEN)
        self.assertEqual(dispatch2.invoke_calls, 1)
        self.assertEqual(self.store.retry_actions_accepted(durable.chain_id), 1)

    def test_two_initial_requests_with_same_persisted_owner_invoke_once(self) -> None:
        owner, dispatch = self._owner(
            "attempt-1",
            "ledger-0",
            _success("concurrent-initial", "ledger-1"),
        )
        nonce_counter = count(1)
        first = ProviderStageRetryExecutorV1(
            self.store,
            dispatch_nonce_factory=lambda: f"initial-nonce-{next(nonce_counter)}",
        )
        second = ProviderStageRetryExecutorV1(
            self.store,
            dispatch_nonce_factory=lambda: f"initial-nonce-{next(nonce_counter)}",
        )

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = (
                pool.submit(
                    first.execute_initial,
                    scope=self.scope,
                    packet=self.packet,
                    owner=owner,
                ),
                pool.submit(
                    second.execute_initial,
                    scope=self.scope,
                    packet=self.packet,
                    owner=owner,
                ),
            )
            for future in futures:
                future.result()

        durable = self.store.read(self.scope.identity.chain_id)
        self.assertIs(durable.phase, ProviderStageRetryPhase.RESULT_FROZEN)
        self.assertEqual(dispatch.invoke_calls, 1)
        self.assertEqual(durable.attempts_total, 1)

    def test_concurrent_manual_retry_with_distinct_owners_dispatches_only_winner(self) -> None:
        owner1, _ = self._owner("attempt-1", "ledger-0", _failure("one", "ledger-1"))
        self.executor.execute_initial(
            scope=self.scope,
            packet=self.packet,
            owner=owner1,
        )
        action = self.executor.status_envelope(self.scope)["actions"][0]
        contender_a, dispatch_a = self._owner(
            "attempt-2-a",
            "ledger-1",
            _success("winner-a", "ledger-2"),
        )
        contender_b, dispatch_b = self._owner(
            "attempt-2-b",
            "ledger-1",
            _success("winner-b", "ledger-2"),
        )
        first = ProviderStageRetryExecutorV1(self.store)
        second = ProviderStageRetryExecutorV1(self.store)

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = (
                pool.submit(first.execute_manual_retry, action=action, owner=contender_a),
                pool.submit(second.execute_manual_retry, action=action, owner=contender_b),
            )
            for future in futures:
                future.result()

        durable = self.store.read(self.scope.identity.chain_id)
        persisted = durable.attempts[1]
        self.assertIs(durable.phase, ProviderStageRetryPhase.RESULT_FROZEN)
        self.assertEqual(dispatch_a.invoke_calls + dispatch_b.invoke_calls, 1)
        if persisted.session_scope_sha256 == contender_a.session_scope_sha256:
            self.assertEqual(dispatch_a.invoke_calls, 1)
            self.assertEqual(dispatch_b.invoke_calls, 0)
        else:
            self.assertEqual(persisted.session_scope_sha256, contender_b.session_scope_sha256)
            self.assertEqual(dispatch_a.invoke_calls, 0)
            self.assertEqual(dispatch_b.invoke_calls, 1)
        self.assertEqual(self.store.retry_actions_accepted(durable.chain_id), 1)


if __name__ == "__main__":
    unittest.main()
