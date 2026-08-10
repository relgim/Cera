from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from cera.errors import ContractValidationError, StateConflictError
from cera.pi_scene.provider_stage_retry import (
    MAXIMUM_PROVIDER_STAGE_ATTEMPTS,
    ProviderFamily,
    ProviderModelFamily,
    ProviderStage,
    ProviderStageBlockReason,
    ProviderStageFailureClass,
    ProviderStageRecoveryAction,
    ProviderStageRetryBlockedV1,
    ProviderStageRetryExhaustedV1,
    ProviderStageRetryIdentityV1,
    ProviderStageRetryPhase,
)
from cera.pi_scene.provider_stage_retry_controller import (
    ProviderStageRetryControllerV1,
)
from cera.pi_scene.provider_stage_retry_store import ProviderStageRetryStoreV1
from cera.serialization import bytes_sha256, text_sha256

_INPUT_SENTINEL = b"PROTECTED INPUT PROSE C:\\private\\story.txt"
_RESULT_SENTINEL = b"PROTECTED PROVIDER RESULT SHOULD NEVER PROJECT"


def _sha(label: str) -> str:
    return text_sha256(label)


def _identity(
    *,
    stage: ProviderStage = ProviderStage.WRITER,
    exact_input: bytes = _INPUT_SENTINEL,
) -> ProviderStageRetryIdentityV1:
    owners = {
        ProviderStage.PLANNER: (ProviderFamily.CODEX, ProviderModelFamily.SOL),
        ProviderStage.SEMANTIC_VALIDATOR: (
            ProviderFamily.CODEX,
            ProviderModelFamily.LUNA,
        ),
        ProviderStage.WRITER: (ProviderFamily.DEEPSEEK, ProviderModelFamily.DEEPSEEK_V4),
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
        request_sha256=_sha(f"request:{stage.value}"),
        stage_input_sha256=bytes_sha256(exact_input),
        authority_sha256=_sha(f"authority:{stage.value}"),
        story_state_committed=stage is ProviderStage.RECORDER,
    )


class ProviderStageRetryContractTests(unittest.TestCase):
    def test_closed_provider_model_stage_and_failure_vocabularies(self) -> None:
        expected_stages = {
            "planner",
            "semantic_validator",
            "writer",
            "recorder",
            "adult_scene",
            "adult_filter",
        }
        self.assertEqual({value.value for value in ProviderStage}, expected_stages)
        self.assertEqual(
            tuple(value.value for value in ProviderStageFailureClass),
            (
                "transport_timeout",
                "provider_unavailable",
                "provider_process_failed",
                "provider_stream_incomplete",
                "provider_completion_incomplete",
                "provider_output_invalid",
                "dispatch_ambiguous",
            ),
        )
        for stage in ProviderStage:
            identity = _identity(stage=stage)
            self.assertTrue(identity.chain_id.startswith("stage-retry-"))
            self.assertEqual(identity.story_state_committed, stage is ProviderStage.RECORDER)
        with self.assertRaisesRegex(ContractValidationError, "owner mapping"):
            ProviderStageRetryIdentityV1(
                schema_version=ProviderStageRetryIdentityV1.SCHEMA_VERSION,
                provider=ProviderFamily.CODEX,
                model_family=ProviderModelFamily.SOL,
                stage=ProviderStage.WRITER,
                request_sha256=_sha("request"),
                stage_input_sha256=bytes_sha256(_INPUT_SENTINEL),
                authority_sha256=_sha("authority"),
                story_state_committed=False,
            )


class ProviderStageRetryStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = (Path(self.temporary.name) / "provider-stage-retries").resolve()
        self.identity = _identity()

    def _controller(self) -> ProviderStageRetryControllerV1:
        return ProviderStageRetryControllerV1(self.root)

    def _begin(self) -> tuple[ProviderStageRetryControllerV1, str]:
        controller = self._controller()
        chain = controller.begin(self.identity, _INPUT_SENTINEL)
        return controller, chain.chain_id

    @staticmethod
    def _prepare_and_dispatch(
        controller: ProviderStageRetryControllerV1,
        chain_id: str,
        attempt_number: int,
        ledger_before: str,
    ) -> None:
        controller.prepare_attempt(
            chain_id,
            session_scope_sha256=_sha(f"session:{attempt_number}"),
            ledger_prefix_before_sha256=ledger_before,
        )
        controller.mark_dispatch_started(
            chain_id,
            attempt_number=attempt_number,
            dispatch_evidence_sha256=_sha(f"dispatch:{attempt_number}"),
        )

    @staticmethod
    def _fail(
        controller: ProviderStageRetryControllerV1,
        chain_id: str,
        attempt_number: int,
        ledger_after: str,
        *,
        failure_class: ProviderStageFailureClass = ProviderStageFailureClass.TRANSPORT_TIMEOUT,
    ) -> None:
        controller.mark_attempt_failed(
            chain_id,
            attempt_number=attempt_number,
            failure_class=failure_class,
            failure_evidence_sha256=_sha(f"failure:{attempt_number}"),
            ledger_prefix_after_sha256=ledger_after,
            provider_operations_observed=attempt_number,
            provider_operations_conservative=attempt_number + 1,
            duration_ms=1_000 * attempt_number,
            input_tokens=100 * attempt_number,
            cached_input_tokens=25 * attempt_number,
            output_tokens=50 * attempt_number,
            reasoning_tokens=10 * attempt_number,
        )

    def test_partial_input_freeze_recovers_idempotently_without_safe_projection_leak(self) -> None:
        store = ProviderStageRetryStoreV1(self.root)
        checkpoint = store.freeze_input(self.identity, _INPUT_SENTINEL)
        self.assertEqual(checkpoint.content_sha256, self.identity.stage_input_sha256)

        controller = self._controller()
        first = controller.begin(self.identity, _INPUT_SENTINEL)
        replay = controller.begin(self.identity, _INPUT_SENTINEL)
        self.assertEqual(first, replay)
        self.assertEqual(first.phase, ProviderStageRetryPhase.INPUT_FROZEN)
        self.assertEqual(controller.load_protected_input(first.chain_id), _INPUT_SENTINEL)
        safe_json = json.dumps(first.to_payload(), sort_keys=True)
        self.assertNotIn(_INPUT_SENTINEL.decode("utf-8"), safe_json)
        self.assertNotIn(str(self.root), safe_json)

        with self.assertRaisesRegex(ContractValidationError, "input hash changed"):
            controller.begin(self.identity, b"different protected input")

    def test_attempt_restart_actions_fresh_owner_and_safe_metrics(self) -> None:
        controller, chain_id = self._begin()
        self.assertEqual(
            controller.recover(chain_id).action,
            ProviderStageRecoveryAction.PREPARE_ATTEMPT,
        )
        controller.prepare_attempt(
            chain_id,
            session_scope_sha256=_sha("session:1"),
            ledger_prefix_before_sha256=_sha("ledger:0"),
        )
        self.assertEqual(
            self._controller().recover(chain_id).action,
            ProviderStageRecoveryAction.DISPATCH_PREPARED_ATTEMPT,
        )
        controller.mark_dispatch_started(
            chain_id,
            attempt_number=1,
            dispatch_evidence_sha256=_sha("dispatch:1"),
        )
        self.assertEqual(
            self._controller().recover(chain_id).action,
            ProviderStageRecoveryAction.RESOLVE_AMBIGUOUS_DISPATCH,
        )
        self._fail(controller, chain_id, 1, _sha("ledger:1"))
        failed = controller.status(chain_id)
        attempt = failed.attempts[0]
        self.assertEqual(attempt.duration_ms, 1_000)
        self.assertEqual(
            (
                attempt.input_tokens,
                attempt.cached_input_tokens,
                attempt.output_tokens,
                attempt.reasoning_tokens,
            ),
            (100, 25, 50, 10),
        )
        self.assertEqual(
            self._controller().recover(chain_id).action,
            ProviderStageRecoveryAction.RETIRE_FAILED_OWNER,
        )
        self._fail(controller, chain_id, 1, _sha("ledger:1"))
        controller.mark_owner_retired(
            chain_id,
            attempt_number=1,
            retirement_evidence_sha256=_sha("retired:1"),
        )
        self.assertEqual(
            self._controller().recover(chain_id).action,
            ProviderStageRecoveryAction.PREPARE_ATTEMPT,
        )
        with self.assertRaisesRegex(ContractValidationError, "reused a session scope"):
            controller.prepare_attempt(
                chain_id,
                session_scope_sha256=_sha("session:1"),
                ledger_prefix_before_sha256=_sha("ledger:1"),
            )
        with self.assertRaisesRegex(StateConflictError, "ledger prefix changed"):
            controller.prepare_attempt(
                chain_id,
                session_scope_sha256=_sha("session:2"),
                ledger_prefix_before_sha256=_sha("wrong-ledger"),
            )
        second = controller.prepare_attempt(
            chain_id,
            session_scope_sha256=_sha("session:2"),
            ledger_prefix_before_sha256=_sha("ledger:1"),
        )
        self.assertEqual(second.attempts_total, 2)
        self.assertEqual(second.retries_consumed, 1)

    def test_result_first_checkpoint_recovers_without_provider_replay(self) -> None:
        controller, chain_id = self._begin()
        self._prepare_and_dispatch(controller, chain_id, 1, _sha("ledger:0"))
        controller.store.stage_result(
            chain_id,
            attempt_number=1,
            exact_result=_RESULT_SENTINEL,
            result_evidence_sha256=_sha("result:evidence"),
            ledger_prefix_after_sha256=_sha("ledger:1"),
            provider_operations_observed=2,
            provider_operations_conservative=2,
            duration_ms=2_345,
            input_tokens=400,
            cached_input_tokens=100,
            output_tokens=125,
            reasoning_tokens=None,
        )

        restarted = self._controller()
        recovery = restarted.recover(chain_id)
        self.assertEqual(recovery.phase, ProviderStageRetryPhase.RESULT_FROZEN)
        self.assertEqual(recovery.action, ProviderStageRecoveryAction.RESUME_DOWNSTREAM)
        chain = restarted.status(chain_id)
        self.assertEqual(chain.attempts_total, 1)
        self.assertEqual(chain.attempts[0].duration_ms, 2_345)
        self.assertEqual(chain.attempts[0].cached_input_tokens, 100)
        self.assertEqual(restarted.load_protected_result(chain_id), _RESULT_SENTINEL)
        safe_json = json.dumps(chain.to_payload(), sort_keys=True)
        self.assertNotIn(_RESULT_SENTINEL.decode("utf-8"), safe_json)

        restarted.bind_downstream(
            chain_id,
            downstream_evidence_sha256=_sha("downstream"),
        )
        self.assertEqual(
            self._controller().recover(chain_id).action,
            ProviderStageRecoveryAction.RECONCILE_DOWNSTREAM,
        )
        restarted.mark_succeeded(chain_id)
        self.assertEqual(
            self._controller().recover(chain_id).action,
            ProviderStageRecoveryAction.REPLAY_SUCCESS,
        )
        with self.assertRaisesRegex(StateConflictError, "prepare another attempt"):
            restarted.prepare_attempt(
                chain_id,
                session_scope_sha256=_sha("session:2"),
                ledger_prefix_before_sha256=_sha("ledger:1"),
            )

    def test_three_failures_exhaust_exact_shared_terminal_and_never_offer_fourth(self) -> None:
        controller, chain_id = self._begin()
        ledger_before = _sha("ledger:0")
        failure_classes = (
            ProviderStageFailureClass.PROVIDER_UNAVAILABLE,
            ProviderStageFailureClass.PROVIDER_STREAM_INCOMPLETE,
            ProviderStageFailureClass.PROVIDER_OUTPUT_INVALID,
        )
        for attempt_number, failure_class in enumerate(failure_classes, start=1):
            self._prepare_and_dispatch(
                controller,
                chain_id,
                attempt_number,
                ledger_before,
            )
            ledger_after = _sha(f"ledger:{attempt_number}")
            self._fail(
                controller,
                chain_id,
                attempt_number,
                ledger_after,
                failure_class=failure_class,
            )
            if attempt_number < MAXIMUM_PROVIDER_STAGE_ATTEMPTS:
                controller.mark_owner_retired(
                    chain_id,
                    attempt_number=attempt_number,
                    retirement_evidence_sha256=_sha(f"retired:{attempt_number}"),
                )
            ledger_before = ledger_after

        chain = controller.status(chain_id)
        self.assertEqual(chain.phase, ProviderStageRetryPhase.EXHAUSTED)
        self.assertEqual(chain.attempts_total, 3)
        self.assertEqual(chain.retries_consumed, 2)
        self.assertEqual(chain.attempts_remaining, 0)
        terminal = controller.terminal(chain_id)
        self.assertIsInstance(terminal, ProviderStageRetryExhaustedV1)
        assert isinstance(terminal, ProviderStageRetryExhaustedV1)
        self.assertEqual(
            set(terminal.to_payload()),
            {
                "schema_version",
                "severity",
                "provider",
                "model_family",
                "stage",
                "maximum_attempts",
                "attempts_total",
                "retries_consumed",
                "story_state_committed",
                "failed_stage_effect_committed",
                "provider_operations_observed_total",
                "provider_operations_conservative_total",
                "final_failure_class",
                "request_sha256",
                "stage_input_sha256",
                "attempt_chain_sha256",
                "terminal_evidence_sha256",
            },
        )
        self.assertEqual(terminal.schema_version, "cera.provider_stage_retry_exhausted.v1")
        self.assertEqual(terminal.maximum_attempts, 3)
        self.assertEqual(terminal.attempts_total, 3)
        self.assertEqual(terminal.retries_consumed, 2)
        self.assertEqual(terminal.provider_operations_observed_total, 6)
        self.assertEqual(terminal.provider_operations_conservative_total, 9)
        self.assertEqual(
            terminal.final_failure_class,
            ProviderStageFailureClass.PROVIDER_OUTPUT_INVALID,
        )
        self.assertFalse(terminal.story_state_committed)
        self.assertFalse(terminal.failed_stage_effect_committed)
        safe_terminal = json.dumps(terminal.to_payload(), sort_keys=True)
        self.assertNotIn(_INPUT_SENTINEL.decode("utf-8"), safe_terminal)
        self.assertNotIn(str(self.root), safe_terminal)
        with self.assertRaisesRegex(StateConflictError, "prepare another attempt"):
            controller.prepare_attempt(
                chain_id,
                session_scope_sha256=_sha("session:4"),
                ledger_prefix_before_sha256=_sha("ledger:3"),
            )

        replay = self._controller().terminal(chain_id)
        self.assertEqual(replay, terminal)
        terminal_path = (
            self.root
            / "CHAINS"
            / f"c-{chain_id.removeprefix('stage-retry-')[:32]}"
            / "TERMINAL.json"
        )
        terminal_path.unlink()
        self.assertEqual(self._controller().terminal(chain_id), terminal)
        self.assertEqual(
            self._controller().recover(chain_id).action,
            ProviderStageRecoveryAction.REPORT_EXHAUSTED,
        )

    def test_blocked_ambiguous_is_distinct_terminal_and_idempotent(self) -> None:
        controller, chain_id = self._begin()
        self._prepare_and_dispatch(controller, chain_id, 1, _sha("ledger:0"))
        with self.assertRaisesRegex(StateConflictError, "conservatively accounted"):
            controller.block_ambiguous(
                chain_id,
                reason=ProviderStageBlockReason.DISPATCH_CUSTODY_AMBIGUOUS,
                evidence_sha256=_sha("unaccounted-block"),
            )
        self._fail(
            controller,
            chain_id,
            1,
            _sha("ledger:1"),
            failure_class=ProviderStageFailureClass.DISPATCH_AMBIGUOUS,
        )
        blocked = controller.block_ambiguous(
            chain_id,
            reason=ProviderStageBlockReason.OWNER_RETIREMENT_UNPROVEN,
            evidence_sha256=_sha("block:evidence"),
        )
        self.assertEqual(blocked.phase, ProviderStageRetryPhase.BLOCKED_AMBIGUOUS)
        replay = controller.block_ambiguous(
            chain_id,
            reason=ProviderStageBlockReason.OWNER_RETIREMENT_UNPROVEN,
            evidence_sha256=_sha("block:evidence"),
        )
        self.assertEqual(replay, blocked)
        terminal = self._controller().terminal(chain_id)
        self.assertIsInstance(terminal, ProviderStageRetryBlockedV1)
        assert isinstance(terminal, ProviderStageRetryBlockedV1)
        self.assertEqual(terminal.attempts_total, 1)
        self.assertEqual(terminal.retries_consumed, 0)
        self.assertEqual(terminal.provider_operations_observed_total, 1)
        self.assertEqual(terminal.provider_operations_conservative_total, 2)
        self.assertEqual(
            self._controller().recover(chain_id).action,
            ProviderStageRecoveryAction.REPORT_BLOCKED_AMBIGUOUS,
        )
        with self.assertRaisesRegex(StateConflictError, "prepare another attempt"):
            controller.prepare_attempt(
                chain_id,
                session_scope_sha256=_sha("session:2"),
                ledger_prefix_before_sha256=_sha("ledger:0"),
            )

    def test_invalid_terminal_metrics_do_not_publish_result_or_failure_state(self) -> None:
        controller, chain_id = self._begin()
        self._prepare_and_dispatch(controller, chain_id, 1, _sha("ledger:0"))
        with self.assertRaisesRegex(ContractValidationError, "input tokens"):
            controller.freeze_result(
                chain_id,
                attempt_number=1,
                exact_result=_RESULT_SENTINEL,
                result_evidence_sha256=_sha("result:evidence"),
                ledger_prefix_after_sha256=_sha("ledger:1"),
                provider_operations_observed=1,
                provider_operations_conservative=1,
                duration_ms=100,
                input_tokens=-1,
            )
        self.assertEqual(
            controller.status(chain_id).phase,
            ProviderStageRetryPhase.DISPATCH_STARTED,
        )
        with self.assertRaisesRegex(ContractValidationError, "duration"):
            controller.mark_attempt_failed(
                chain_id,
                attempt_number=1,
                failure_class=ProviderStageFailureClass.PROVIDER_PROCESS_FAILED,
                failure_evidence_sha256=_sha("failure"),
                ledger_prefix_after_sha256=_sha("ledger:1"),
                provider_operations_observed=0,
                provider_operations_conservative=1,
                duration_ms=-1,
            )
        self.assertEqual(
            controller.status(chain_id).phase,
            ProviderStageRetryPhase.DISPATCH_STARTED,
        )

    def test_protected_input_state_and_result_tamper_fail_closed(self) -> None:
        controller, chain_id = self._begin()
        compact = f"c-{chain_id.removeprefix('stage-retry-')[:32]}"
        chain_root = self.root / "CHAINS" / compact
        input_path = chain_root / "INPUT.bin"
        original_input = input_path.read_bytes()
        input_path.write_bytes(b"tampered")
        with self.assertRaisesRegex(StateConflictError, "input bytes changed"):
            self._controller().status(chain_id)
        input_path.write_bytes(original_input)

        state_path = chain_root / "STATE.json"
        original_state = state_path.read_bytes()
        state_payload = json.loads(original_state)
        state_payload["state_sha256"] = _sha("tampered-state")
        state_path.write_text(json.dumps(state_payload), encoding="utf-8")
        with self.assertRaisesRegex(StateConflictError, "state hash changed"):
            self._controller().status(chain_id)
        state_path.write_bytes(original_state)

        self._prepare_and_dispatch(controller, chain_id, 1, _sha("ledger:0"))
        controller.freeze_result(
            chain_id,
            attempt_number=1,
            exact_result=_RESULT_SENTINEL,
            result_evidence_sha256=_sha("result:evidence"),
            ledger_prefix_after_sha256=_sha("ledger:1"),
            provider_operations_observed=1,
            provider_operations_conservative=1,
            duration_ms=100,
        )
        result_path = chain_root / "ATTEMPTS" / "01" / "RESULT.bin"
        result_path.write_bytes(b"tampered result")
        with self.assertRaisesRegex(StateConflictError, "result bytes changed"):
            self._controller().status(chain_id)


if __name__ == "__main__":
    unittest.main()
