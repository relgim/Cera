from __future__ import annotations

import json
import os
import subprocess
import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import cera.pi_scene.provider_stage_retry_store as retry_store_module
from cera.errors import ContractValidationError, StateConflictError
from cera.pi_scene.provider_stage_retry import (
    MAXIMUM_SAFE_INTEGER,
    ProviderFamily,
    ProviderModelFamily,
    ProviderStage,
    ProviderStageAttemptPhase,
    ProviderStageBlockReason,
    ProviderStageCheckpointKind,
    ProviderStageFailureClass,
    ProviderStageProtectedCheckpointV1,
    ProviderStageRecoveryAction,
    ProviderStageRetryBlockedV1,
    ProviderStageRetryChainV1,
    ProviderStageRetryExhaustedV1,
    ProviderStageRetryIdentityV1,
    ProviderStageRetryPhase,
    ProviderStageRetryRecoveryV1,
    provider_stage_chain_from_payload,
    provider_stage_retry_identity_from_payload,
)
from cera.pi_scene.provider_stage_retry_controller import (
    ProviderStageRetryControllerV1,
)
from cera.pi_scene.provider_stage_retry_store import ProviderStageRetryStoreV1
from cera.serialization import bytes_sha256, canonical_sha256, text_sha256

_INPUT_SENTINEL = b"PROTECTED INPUT PROSE C:\\private\\story.txt"
_RESULT_SENTINEL = b"PROTECTED PROVIDER RESULT SHOULD NEVER PROJECT"


def _sha(label: str) -> str:
    return text_sha256(label)


def _identity(
    *,
    stage: ProviderStage = ProviderStage.WRITER,
    exact_input: bytes = _INPUT_SENTINEL,
    occurrence_label: str | None = None,
    request_label: str | None = None,
    authority_label: str | None = None,
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
        request_occurrence_sha256=_sha(occurrence_label or f"occurrence:{stage.value}"),
        request_sha256=_sha(request_label or f"request:{stage.value}"),
        stage_input_sha256=bytes_sha256(exact_input),
        authority_sha256=_sha(authority_label or f"authority:{stage.value}"),
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
                request_occurrence_sha256=_sha("occurrence"),
                request_sha256=_sha("request"),
                stage_input_sha256=bytes_sha256(_INPUT_SENTINEL),
                authority_sha256=_sha("authority"),
                story_state_committed=False,
            )

    def test_raw_strenum_values_are_rejected_but_payload_decoders_convert(self) -> None:
        identity = _identity(stage=ProviderStage.RECORDER)
        decoded_identity = provider_stage_retry_identity_from_payload(identity.to_payload())
        self.assertIs(decoded_identity.stage, ProviderStage.RECORDER)
        with self.assertRaisesRegex(ContractValidationError, "enums are not closed"):
            replace(
                identity,
                stage="recorder",  # type: ignore[arg-type]
                story_state_committed=False,
            )

        checkpoint = ProviderStageProtectedCheckpointV1.create(
            checkpoint_kind=ProviderStageCheckpointKind.INPUT,
            chain_id=identity.chain_id,
            attempt_number=None,
            content_sha256=identity.stage_input_sha256,
            size_bytes=len(_INPUT_SENTINEL),
            evidence_sha256=identity.authority_sha256,
        )
        with self.assertRaisesRegex(ContractValidationError, "kind is not closed"):
            replace(checkpoint, checkpoint_kind="input")  # type: ignore[arg-type]

        chain = ProviderStageRetryChainV1(
            schema_version=ProviderStageRetryChainV1.SCHEMA_VERSION,
            identity=identity,
            phase=ProviderStageRetryPhase.INPUT_FROZEN,
            attempts=(),
            result_checkpoint=None,
            downstream_intent_sha256=None,
            downstream_evidence_sha256=None,
            block_reason=None,
            block_evidence_sha256=None,
        )
        decoded_chain = provider_stage_chain_from_payload(chain.to_payload())
        self.assertIs(decoded_chain.phase, ProviderStageRetryPhase.INPUT_FROZEN)
        with self.assertRaisesRegex(ContractValidationError, "enums or contracts"):
            replace(chain, phase="input_frozen")  # type: ignore[arg-type]

        recovery = ProviderStageRetryRecoveryV1(
            schema_version=ProviderStageRetryRecoveryV1.SCHEMA_VERSION,
            chain_id=identity.chain_id,
            phase=ProviderStageRetryPhase.INPUT_FROZEN,
            action=ProviderStageRecoveryAction.PREPARE_ATTEMPT,
            attempt_number=1,
            attempts_remaining=3,
            chain_sha256=chain.chain_sha256,
        )
        with self.assertRaisesRegex(ContractValidationError, "enums are not closed"):
            replace(recovery, action="prepare_attempt")  # type: ignore[arg-type]


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

    def _chain_root(self, chain_id: str) -> Path:
        compact = f"c-{chain_id.removeprefix('stage-retry-')[:32]}"
        return self.root / "CHAINS" / compact

    def _advance_to_attempt_three_dispatch(
        self,
        controller: ProviderStageRetryControllerV1,
        chain_id: str,
    ) -> None:
        ledger = _sha("ledger:0")
        for attempt_number in (1, 2):
            self._prepare_and_dispatch(
                controller,
                chain_id,
                attempt_number,
                ledger,
            )
            ledger_after = _sha(f"ledger:{attempt_number}")
            self._fail(controller, chain_id, attempt_number, ledger_after)
            controller.mark_owner_retired(
                chain_id,
                attempt_number=attempt_number,
                retirement_evidence_sha256=_sha(f"retired:{attempt_number}"),
            )
            ledger = ledger_after
        self._prepare_and_dispatch(controller, chain_id, 3, ledger)

    def _make_junction(self, link: Path, target: Path) -> None:
        target.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            ["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            self.skipTest(f"Windows junction capability unavailable: {result.stderr}")

        def remove_junction() -> None:
            if os.path.isjunction(link):
                os.rmdir(link)

        self.addCleanup(remove_junction)

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
            provider_operations_conservative=attempt_number,
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

    def test_occurrence_identity_prevents_aliasing_and_same_occurrence_drift(self) -> None:
        first = _identity(occurrence_label="world:branch:request:1")
        second = _identity(occurrence_label="world:branch:request:2")
        controller = self._controller()
        first_chain = controller.begin(first, _INPUT_SENTINEL)
        second_chain = controller.begin(second, _INPUT_SENTINEL)
        self.assertNotEqual(first_chain.chain_id, second_chain.chain_id)

        changed_input = b"A DIFFERENT PROTECTED INPUT"
        drift = _identity(
            occurrence_label="world:branch:request:1",
            exact_input=changed_input,
        )
        self.assertEqual(first.chain_id, drift.chain_id)
        with self.assertRaisesRegex(StateConflictError, "input changed"):
            controller.begin(drift, changed_input)
        blocked = controller.status(first.chain_id)
        self.assertEqual(blocked.phase, ProviderStageRetryPhase.BLOCKED_AMBIGUOUS)
        self.assertIs(blocked.block_reason, ProviderStageBlockReason.INPUT_CHANGED)
        self.assertEqual(blocked.attempts_total, 0)

    def test_same_occurrence_authority_drift_blocks_original_chain(self) -> None:
        controller, chain_id = self._begin()
        drift = _identity(
            occurrence_label="occurrence:writer",
            request_label="changed request authority",
        )
        self.assertEqual(chain_id, drift.chain_id)
        with self.assertRaisesRegex(StateConflictError, "authority changed"):
            controller.begin(drift, _INPUT_SENTINEL)
        blocked = controller.status(chain_id)
        self.assertIs(blocked.block_reason, ProviderStageBlockReason.AUTHORITY_CHANGED)
        with self.assertRaisesRegex(StateConflictError, "prepare another attempt"):
            controller.prepare_attempt(
                chain_id,
                session_scope_sha256=_sha("session"),
                ledger_prefix_before_sha256=_sha("ledger"),
            )

    def test_missing_and_rolled_back_state_cache_cannot_reset_budget(self) -> None:
        controller, chain_id = self._begin()
        state_one = (self._chain_root(chain_id) / "STATES" / "000001.json").read_bytes()
        self._prepare_and_dispatch(controller, chain_id, 1, _sha("ledger:0"))
        self._fail(controller, chain_id, 1, _sha("ledger:1"))
        controller.mark_owner_retired(
            chain_id,
            attempt_number=1,
            retirement_evidence_sha256=_sha("retired:1"),
        )
        controller.prepare_attempt(
            chain_id,
            session_scope_sha256=_sha("session:2"),
            ledger_prefix_before_sha256=_sha("ledger:1"),
        )
        expected = controller.status(chain_id)
        projection = self._chain_root(chain_id) / "STATE.json"
        projection.unlink()
        recovered_missing = self._controller().status(chain_id)
        self.assertEqual(recovered_missing, expected)
        projection.write_bytes(state_one)
        recovered_rollback = self._controller().status(chain_id)
        self.assertEqual(recovered_rollback, expected)
        self.assertEqual(recovered_rollback.attempts_total, 2)

    def test_state_history_gap_and_old_chain_append_fail_closed(self) -> None:
        controller, chain_id = self._begin()
        self._prepare_and_dispatch(controller, chain_id, 1, _sha("ledger:0"))
        states = self._chain_root(chain_id) / "STATES"
        state_two = states / "000002.json"
        state_two_bytes = state_two.read_bytes()
        state_two.unlink()
        with self.assertRaisesRegex(StateConflictError, "sequence has a gap"):
            self._controller().status(chain_id)
        state_two.write_bytes(state_two_bytes)

        old = json.loads((states / "000001.json").read_text(encoding="utf-8"))
        previous = json.loads((states / "000003.json").read_text(encoding="utf-8"))
        old["state_sequence"] = 4
        old["previous_state_record_sha256"] = previous["state_record_sha256"]
        body = {key: value for key, value in old.items() if key != "state_record_sha256"}
        old["state_record_sha256"] = canonical_sha256(body)
        (states / "000004.json").write_text(
            json.dumps(old, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(StateConflictError, "successor is not monotonic"):
            self._controller().status(chain_id)

    def test_state_predecessor_mutation_fails_closed(self) -> None:
        controller, chain_id = self._begin()
        controller.prepare_attempt(
            chain_id,
            session_scope_sha256=_sha("session:1"),
            ledger_prefix_before_sha256=_sha("ledger:0"),
        )
        states = self._chain_root(chain_id) / "STATES"
        state_two = json.loads((states / "000002.json").read_text(encoding="utf-8"))
        state_two["previous_state_record_sha256"] = _sha("wrong predecessor")
        body = {key: value for key, value in state_two.items() if key != "state_record_sha256"}
        state_two["state_record_sha256"] = canonical_sha256(body)
        (states / "000002.json").write_text(
            json.dumps(state_two, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(StateConflictError, "predecessor changed"):
            self._controller().status(chain_id)

    def test_pending_state_publication_recovers_once(self) -> None:
        controller, chain_id = self._begin()
        controller.prepare_attempt(
            chain_id,
            session_scope_sha256=_sha("session:1"),
            ledger_prefix_before_sha256=_sha("ledger:0"),
        )
        original_replace = retry_store_module.safe_replace
        failed = False

        def fail_state_three_once(*args: object, **kwargs: object) -> object:
            nonlocal failed
            final = kwargs["final_relative_path"]
            if not failed and isinstance(final, str) and final.endswith("000003.json"):
                failed = True
                raise StateConflictError("injected publication crash")
            return original_replace(*args, **kwargs)  # type: ignore[arg-type]

        with patch.object(retry_store_module, "safe_replace", fail_state_three_once):
            with self.assertRaisesRegex(StateConflictError, "injected publication crash"):
                controller.mark_dispatch_started(
                    chain_id,
                    attempt_number=1,
                    dispatch_evidence_sha256=_sha("dispatch:1"),
                )
        self.assertEqual(
            self._controller().status(chain_id).phase,
            ProviderStageRetryPhase.ATTEMPT_PREPARED,
        )
        recovered = self._controller().mark_dispatch_started(
            chain_id,
            attempt_number=1,
            dispatch_evidence_sha256=_sha("dispatch:1"),
        )
        self.assertEqual(recovered.phase, ProviderStageRetryPhase.DISPATCH_STARTED)
        states = self._chain_root(chain_id) / "STATES"
        self.assertTrue((states / "000003.json").is_file())
        self.assertFalse((states / "000004.json").exists())

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
        self.assertEqual(
            recovery.action,
            ProviderStageRecoveryAction.FREEZE_DOWNSTREAM_INTENT,
        )
        chain = restarted.status(chain_id)
        self.assertEqual(chain.attempts_total, 1)
        self.assertEqual(chain.attempts[0].duration_ms, 2_345)
        self.assertEqual(chain.attempts[0].cached_input_tokens, 100)
        self.assertEqual(restarted.load_protected_result(chain_id), _RESULT_SENTINEL)
        safe_json = json.dumps(chain.to_payload(), sort_keys=True)
        self.assertNotIn(_RESULT_SENTINEL.decode("utf-8"), safe_json)

        restarted.freeze_downstream_intent(
            chain_id,
            downstream_intent_sha256=_sha("downstream:intent"),
        )
        self.assertEqual(
            self._controller().recover(chain_id).action,
            ProviderStageRecoveryAction.RECONCILE_DOWNSTREAM,
        )
        restarted.bind_downstream(
            chain_id,
            downstream_evidence_sha256=_sha("downstream"),
        )
        self.assertEqual(
            self._controller().recover(chain_id).action,
            ProviderStageRecoveryAction.FINALIZE_SUCCESS,
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

    def test_downstream_intent_and_completion_are_two_crash_boundaries(self) -> None:
        controller, chain_id = self._begin()
        self._prepare_and_dispatch(controller, chain_id, 1, _sha("ledger:0"))
        controller.freeze_result(
            chain_id,
            attempt_number=1,
            exact_result=_RESULT_SENTINEL,
            result_evidence_sha256=_sha("result"),
            ledger_prefix_after_sha256=_sha("ledger:1"),
            provider_operations_observed=1,
            provider_operations_conservative=1,
            duration_ms=50,
        )
        with self.assertRaisesRegex(StateConflictError, "completion is not durably bound"):
            controller.mark_succeeded(chain_id)
        intent = _sha("sink intent")
        controller.freeze_downstream_intent(
            chain_id,
            downstream_intent_sha256=intent,
        )
        self.assertEqual(
            self._controller().recover(chain_id).action,
            ProviderStageRecoveryAction.RECONCILE_DOWNSTREAM,
        )
        with self.assertRaisesRegex(StateConflictError, "effect or terminal boundary"):
            controller.block_ambiguous(
                chain_id,
                reason=ProviderStageBlockReason.RESULT_CHECKPOINT_CONFLICT,
                evidence_sha256=_sha("unsafe block"),
            )

        fake_sink: dict[str, str] = {}
        apply_count = 0
        if intent not in fake_sink:
            apply_count += 1
            fake_sink[intent] = _sha("sink completion receipt")
        restarted = self._controller()
        self.assertEqual(
            restarted.recover(chain_id).action,
            ProviderStageRecoveryAction.RECONCILE_DOWNSTREAM,
        )
        if intent not in fake_sink:
            apply_count += 1
        completion = fake_sink[intent]
        restarted.bind_downstream(
            chain_id,
            downstream_evidence_sha256=completion,
        )
        self.assertEqual(apply_count, 1)
        with self.assertRaisesRegex(StateConflictError, "effect or terminal boundary"):
            restarted.block_ambiguous(
                chain_id,
                reason=ProviderStageBlockReason.RESULT_CHECKPOINT_CONFLICT,
                evidence_sha256=_sha("post-effect false block"),
            )
        self.assertEqual(
            self._controller().recover(chain_id).action,
            ProviderStageRecoveryAction.FINALIZE_SUCCESS,
        )
        with self.assertRaisesRegex(StateConflictError, "completion changed"):
            restarted.bind_downstream(
                chain_id,
                downstream_evidence_sha256=_sha("mismatched receipt"),
            )
        restarted.mark_succeeded(chain_id)
        self.assertEqual(
            self._controller().recover(chain_id).action,
            ProviderStageRecoveryAction.REPLAY_SUCCESS,
        )

    def test_load_input_returns_same_validated_buffer(self) -> None:
        controller, chain_id = self._begin()
        store = controller.store
        original = store._require_input_locked
        calls = 0

        def mutate_after_second_validation(
            target_chain_id: str,
            checkpoint: ProviderStageProtectedCheckpointV1,
        ) -> bytes:
            nonlocal calls
            calls += 1
            exact = original(target_chain_id, checkpoint)
            if calls == 2:
                (self._chain_root(chain_id) / "INPUT.bin").write_bytes(b"poison")
            return exact

        with patch.object(store, "_require_input_locked", mutate_after_second_validation):
            loaded = controller.load_protected_input(chain_id)
        self.assertEqual(loaded, _INPUT_SENTINEL)

    def test_load_result_returns_same_validated_buffer(self) -> None:
        controller, chain_id = self._begin()
        self._prepare_and_dispatch(controller, chain_id, 1, _sha("ledger:0"))
        controller.freeze_result(
            chain_id,
            attempt_number=1,
            exact_result=_RESULT_SENTINEL,
            result_evidence_sha256=_sha("result"),
            ledger_prefix_after_sha256=_sha("ledger:1"),
            provider_operations_observed=1,
            provider_operations_conservative=1,
            duration_ms=50,
        )
        store = controller.store
        original = store._read_staged_result_locked
        calls = 0

        def mutate_after_second_validation(
            target_chain_id: str,
            attempt_number: int,
            *,
            required: bool,
        ) -> dict[str, object] | None:
            nonlocal calls
            result = original(
                target_chain_id,
                attempt_number,
                required=required,
            )
            calls += 1
            if calls == 2:
                (self._chain_root(chain_id) / "ATTEMPTS" / "01" / "RESULT.bin").write_bytes(
                    b"poison"
                )
            return result

        with patch.object(
            store,
            "_read_staged_result_locked",
            mutate_after_second_validation,
        ):
            loaded = controller.load_protected_result(chain_id)
        self.assertEqual(loaded, _RESULT_SENTINEL)

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
        self.assertEqual(terminal.provider_operations_conservative_total, 6)
        self.assertEqual(
            terminal.final_failure_class,
            ProviderStageFailureClass.PROVIDER_OUTPUT_INVALID,
        )
        self.assertFalse(terminal.story_state_committed)
        self.assertFalse(terminal.failed_stage_effect_committed)
        with self.assertRaisesRegex(ContractValidationError, "enums are not closed"):
            replace(
                terminal,
                final_failure_class="provider_output_invalid",  # type: ignore[arg-type]
            )
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

    def test_third_failure_is_nonterminal_until_owner_retired(self) -> None:
        controller, chain_id = self._begin()
        self._advance_to_attempt_three_dispatch(controller, chain_id)
        self._fail(controller, chain_id, 3, _sha("ledger:3"))
        awaiting = controller.status(chain_id)
        self.assertEqual(
            awaiting.phase,
            ProviderStageRetryPhase.AWAITING_OWNER_RETIREMENT,
        )
        self.assertIsNone(controller.terminal(chain_id))
        self.assertEqual(
            controller.recover(chain_id).action,
            ProviderStageRecoveryAction.RETIRE_FAILED_OWNER,
        )
        with self.assertRaisesRegex(StateConflictError, "prepare another attempt"):
            controller.prepare_attempt(
                chain_id,
                session_scope_sha256=_sha("session:4"),
                ledger_prefix_before_sha256=_sha("ledger:3"),
            )
        exhausted = controller.mark_owner_retired(
            chain_id,
            attempt_number=3,
            retirement_evidence_sha256=_sha("retired:3"),
        )
        self.assertEqual(exhausted.phase, ProviderStageRetryPhase.EXHAUSTED)
        self.assertIs(
            exhausted.attempts[-1].phase,
            ProviderStageAttemptPhase.OWNER_RETIRED,
        )

    def test_unproven_final_owner_retirement_blocks_instead_of_exhausting(self) -> None:
        controller, chain_id = self._begin()
        self._advance_to_attempt_three_dispatch(controller, chain_id)
        self._fail(controller, chain_id, 3, _sha("ledger:3"))
        blocked = controller.block_ambiguous(
            chain_id,
            reason=ProviderStageBlockReason.OWNER_RETIREMENT_UNPROVEN,
            evidence_sha256=_sha("retirement-unproven"),
        )
        self.assertEqual(blocked.phase, ProviderStageRetryPhase.BLOCKED_AMBIGUOUS)
        terminal = controller.terminal(chain_id)
        self.assertIsInstance(terminal, ProviderStageRetryBlockedV1)
        self.assertNotIsInstance(terminal, ProviderStageRetryExhaustedV1)

    def test_dispatch_ambiguity_never_opens_retry_runway(self) -> None:
        controller, chain_id = self._begin()
        self._prepare_and_dispatch(controller, chain_id, 1, _sha("ledger:0"))
        controller.mark_attempt_failed(
            chain_id,
            attempt_number=1,
            failure_class=ProviderStageFailureClass.DISPATCH_AMBIGUOUS,
            failure_evidence_sha256=_sha("ambiguous:1"),
            ledger_prefix_after_sha256=_sha("ledger:ambiguous"),
            provider_operations_observed=0,
            provider_operations_conservative=1,
            duration_ms=500,
        )
        self.assertEqual(
            controller.recover(chain_id).action,
            ProviderStageRecoveryAction.RETIRE_FAILED_OWNER,
        )
        blocked = controller.mark_owner_retired(
            chain_id,
            attempt_number=1,
            retirement_evidence_sha256=_sha("retired:1"),
        )
        self.assertEqual(blocked.phase, ProviderStageRetryPhase.BLOCKED_AMBIGUOUS)
        self.assertIs(
            blocked.block_reason,
            ProviderStageBlockReason.DISPATCH_CUSTODY_AMBIGUOUS,
        )
        with self.assertRaisesRegex(StateConflictError, "prepare another attempt"):
            controller.prepare_attempt(
                chain_id,
                session_scope_sha256=_sha("session:2"),
                ledger_prefix_before_sha256=_sha("ledger:ambiguous"),
            )

    def test_final_dispatch_ambiguity_exhausts_only_after_retirement(self) -> None:
        controller, chain_id = self._begin()
        self._advance_to_attempt_three_dispatch(controller, chain_id)
        controller.mark_attempt_failed(
            chain_id,
            attempt_number=3,
            failure_class=ProviderStageFailureClass.DISPATCH_AMBIGUOUS,
            failure_evidence_sha256=_sha("ambiguous:3"),
            ledger_prefix_after_sha256=_sha("ledger:3"),
            provider_operations_observed=0,
            provider_operations_conservative=1,
            duration_ms=500,
        )
        self.assertIsNone(controller.terminal(chain_id))
        controller.mark_owner_retired(
            chain_id,
            attempt_number=3,
            retirement_evidence_sha256=_sha("retired:3"),
        )
        terminal = controller.terminal(chain_id)
        self.assertIsInstance(terminal, ProviderStageRetryExhaustedV1)
        assert isinstance(terminal, ProviderStageRetryExhaustedV1)
        self.assertIs(
            terminal.final_failure_class,
            ProviderStageFailureClass.DISPATCH_AMBIGUOUS,
        )

    def test_durable_staged_result_wins_over_later_failure_report(self) -> None:
        controller, chain_id = self._begin()
        self._prepare_and_dispatch(controller, chain_id, 1, _sha("ledger:0"))
        controller.store.stage_result(
            chain_id,
            attempt_number=1,
            exact_result=_RESULT_SENTINEL,
            result_evidence_sha256=_sha("result"),
            ledger_prefix_after_sha256=_sha("ledger:1"),
            provider_operations_observed=1,
            provider_operations_conservative=1,
            duration_ms=100,
        )
        with self.assertRaisesRegex(StateConflictError, "no active dispatch"):
            controller.mark_attempt_failed(
                chain_id,
                attempt_number=1,
                failure_class=ProviderStageFailureClass.TRANSPORT_TIMEOUT,
                failure_evidence_sha256=_sha("late failure"),
                ledger_prefix_after_sha256=_sha("ledger:1"),
                provider_operations_observed=1,
                provider_operations_conservative=1,
                duration_ms=100,
            )
        self.assertEqual(
            controller.status(chain_id).phase,
            ProviderStageRetryPhase.RESULT_FROZEN,
        )

    def test_blocked_ambiguous_is_distinct_terminal_and_idempotent(self) -> None:
        controller, chain_id = self._begin()
        self._prepare_and_dispatch(controller, chain_id, 1, _sha("ledger:0"))
        with self.assertRaisesRegex(StateConflictError, "conservative telemetry"):
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
        before_raw_block = controller.status(chain_id)
        with self.assertRaisesRegex(ContractValidationError, "block reason is not closed"):
            controller.block_ambiguous(
                chain_id,
                reason="owner_retirement_unproven",  # type: ignore[arg-type]
                evidence_sha256=_sha("raw block"),
            )
        self.assertEqual(controller.status(chain_id), before_raw_block)
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
        self.assertEqual(terminal.provider_operations_conservative_total, 1)
        self.assertEqual(
            self._controller().recover(chain_id).action,
            ProviderStageRecoveryAction.REPORT_BLOCKED_AMBIGUOUS,
        )
        with self.assertRaisesRegex(ContractValidationError, "enums are not closed"):
            replace(
                terminal,
                block_reason="owner_retirement_unproven",  # type: ignore[arg-type]
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

    def test_terminal_accounting_is_semantically_bounded_before_artifacts(self) -> None:
        controller, chain_id = self._begin()
        self._prepare_and_dispatch(controller, chain_id, 1, _sha("ledger:0"))
        invalid_results = (
            {
                "provider_operations_observed": 0,
                "provider_operations_conservative": 0,
                "duration_ms": 1,
            },
            {
                "provider_operations_observed": 1,
                "provider_operations_conservative": 2,
                "duration_ms": 1,
            },
            {
                "provider_operations_observed": 1,
                "provider_operations_conservative": 1,
                "duration_ms": MAXIMUM_SAFE_INTEGER + 1,
            },
            {
                "provider_operations_observed": 1,
                "provider_operations_conservative": 1,
                "duration_ms": 1,
                "cached_input_tokens": 1,
            },
            {
                "provider_operations_observed": 1,
                "provider_operations_conservative": 1,
                "duration_ms": 1,
                "input_tokens": 1,
                "cached_input_tokens": 2,
            },
            {
                "provider_operations_observed": 1,
                "provider_operations_conservative": 1,
                "duration_ms": 1,
                "input_tokens": 1,
                "cached_input_tokens": "1",
            },
            {
                "provider_operations_observed": True,
                "provider_operations_conservative": 1,
                "duration_ms": 1,
            },
        )
        for metrics in invalid_results:
            with self.subTest(metrics=metrics):
                with self.assertRaises(ContractValidationError):
                    controller.freeze_result(
                        chain_id,
                        attempt_number=1,
                        exact_result=_RESULT_SENTINEL,
                        result_evidence_sha256=_sha("result"),
                        ledger_prefix_after_sha256=_sha("ledger:1"),
                        **metrics,  # type: ignore[arg-type]
                    )
                self.assertEqual(
                    controller.status(chain_id).phase,
                    ProviderStageRetryPhase.DISPATCH_STARTED,
                )

        invalid_failures = (
            {
                "failure_class": ProviderStageFailureClass.DISPATCH_AMBIGUOUS,
                "provider_operations_observed": 0,
                "provider_operations_conservative": 0,
                "duration_ms": 1,
            },
            {
                "failure_class": ProviderStageFailureClass.TRANSPORT_TIMEOUT,
                "provider_operations_observed": 0,
                "provider_operations_conservative": 0,
                "duration_ms": 1,
                "input_tokens": 1,
            },
            {
                "failure_class": ProviderStageFailureClass.TRANSPORT_TIMEOUT,
                "provider_operations_observed": 1,
                "provider_operations_conservative": 2,
                "duration_ms": 1,
            },
            {
                "failure_class": ProviderStageFailureClass.TRANSPORT_TIMEOUT,
                "provider_operations_observed": MAXIMUM_SAFE_INTEGER + 1,
                "provider_operations_conservative": MAXIMUM_SAFE_INTEGER + 1,
                "duration_ms": 1,
            },
            {
                "failure_class": ProviderStageFailureClass.TRANSPORT_TIMEOUT,
                "provider_operations_observed": 0,
                "provider_operations_conservative": 0,
                "duration_ms": -1,
            },
        )
        for metrics in invalid_failures:
            with self.subTest(metrics=metrics):
                with self.assertRaises(ContractValidationError):
                    controller.mark_attempt_failed(
                        chain_id,
                        attempt_number=1,
                        failure_evidence_sha256=_sha("failure"),
                        ledger_prefix_after_sha256=_sha("ledger:1"),
                        **metrics,  # type: ignore[arg-type]
                    )
        with self.assertRaisesRegex(ContractValidationError, "failure class is not closed"):
            controller.mark_attempt_failed(
                chain_id,
                attempt_number=1,
                failure_class="transport_timeout",  # type: ignore[arg-type]
                failure_evidence_sha256=_sha("failure"),
                ledger_prefix_after_sha256=_sha("ledger:1"),
                provider_operations_observed=1,
                provider_operations_conservative=1,
                duration_ms=1,
            )
        chain_root = self._chain_root(chain_id)
        self.assertFalse((chain_root / "ATTEMPTS" / "01" / "RESULT.bin").exists())
        self.assertFalse((chain_root / "ATTEMPTS" / "01" / "RESULT.json").exists())
        self.assertIsNone(controller.terminal(chain_id))

    def test_cumulative_operation_accounting_cannot_exceed_safe_integer(self) -> None:
        controller, chain_id = self._begin()
        half = MAXIMUM_SAFE_INTEGER // 2
        ledger = _sha("ledger:0")
        for attempt_number in (1, 2):
            self._prepare_and_dispatch(
                controller,
                chain_id,
                attempt_number,
                ledger,
            )
            ledger_after = _sha(f"ledger:{attempt_number}")
            controller.mark_attempt_failed(
                chain_id,
                attempt_number=attempt_number,
                failure_class=ProviderStageFailureClass.PROVIDER_UNAVAILABLE,
                failure_evidence_sha256=_sha(f"failure:{attempt_number}"),
                ledger_prefix_after_sha256=ledger_after,
                provider_operations_observed=half,
                provider_operations_conservative=half,
                duration_ms=1,
            )
            controller.mark_owner_retired(
                chain_id,
                attempt_number=attempt_number,
                retirement_evidence_sha256=_sha(f"retired:{attempt_number}"),
            )
            ledger = ledger_after
        self._prepare_and_dispatch(controller, chain_id, 3, ledger)
        with self.assertRaisesRegex(ContractValidationError, "cumulative observed"):
            controller.mark_attempt_failed(
                chain_id,
                attempt_number=3,
                failure_class=ProviderStageFailureClass.PROVIDER_UNAVAILABLE,
                failure_evidence_sha256=_sha("failure:3"),
                ledger_prefix_after_sha256=_sha("ledger:3"),
                provider_operations_observed=2,
                provider_operations_conservative=2,
                duration_ms=1,
            )
        self.assertEqual(
            controller.status(chain_id).phase,
            ProviderStageRetryPhase.DISPATCH_STARTED,
        )
        self.assertIsNone(controller.terminal(chain_id))

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
        with self.assertRaisesRegex(StateConflictError, "state record shape changed"):
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

    @unittest.skipUnless(os.name == "nt", "Windows junction custody regression")
    def test_claims_junction_never_creates_outside_lock(self) -> None:
        self._controller()
        claims = self.root / "CLAIMS"
        claims.rmdir()
        outside = Path(self.temporary.name) / "outside-claims"
        self._make_junction(claims, outside)
        with self.assertRaisesRegex(StateConflictError, "no-follow|reparse|identity"):
            self._controller().begin(self.identity, _INPUT_SENTINEL)
        self.assertEqual(list(outside.iterdir()), [])

    @unittest.skipUnless(os.name == "nt", "Windows junction custody regression")
    def test_chain_root_junction_never_creates_outside_artifacts(self) -> None:
        controller = self._controller()
        chain_root = self._chain_root(self.identity.chain_id)
        outside = Path(self.temporary.name) / "outside-chain"
        self._make_junction(chain_root, outside)
        with self.assertRaisesRegex(StateConflictError, "no-follow|reparse|identity"):
            controller.begin(self.identity, _INPUT_SENTINEL)
        self.assertEqual(list(outside.iterdir()), [])

    @unittest.skipUnless(os.name == "nt", "Windows junction custody regression")
    def test_index_junction_never_creates_outside_artifacts(self) -> None:
        controller = self._controller()
        index = self.root / "INDEX"
        index.rmdir()
        outside = Path(self.temporary.name) / "outside-index"
        self._make_junction(index, outside)
        with self.assertRaisesRegex(StateConflictError, "no-follow|reparse|identity"):
            controller.begin(self.identity, _INPUT_SENTINEL)
        self.assertEqual(list(outside.iterdir()), [])

    @unittest.skipUnless(os.name == "nt", "Windows junction custody regression")
    def test_attempts_junction_never_creates_outside_artifacts(self) -> None:
        controller, chain_id = self._begin()
        attempts = self._chain_root(chain_id) / "ATTEMPTS"
        attempts.rmdir()
        outside = Path(self.temporary.name) / "outside-attempts"
        self._make_junction(attempts, outside)
        with self.assertRaisesRegex(StateConflictError, "no-follow|reparse|identity"):
            controller.prepare_attempt(
                chain_id,
                session_scope_sha256=_sha("session:1"),
                ledger_prefix_before_sha256=_sha("ledger:0"),
            )
        self.assertEqual(list(outside.iterdir()), [])

    @unittest.skipUnless(os.name == "nt", "Windows junction custody regression")
    def test_state_log_junction_never_reads_or_writes_outside(self) -> None:
        _, chain_id = self._begin()
        states = self._chain_root(chain_id) / "STATES"
        (states / "000001.json").unlink()
        states.rmdir()
        outside = Path(self.temporary.name) / "outside-state-log"
        self._make_junction(states, outside)
        with self.assertRaisesRegex(StateConflictError, "no-follow|reparse|identity"):
            self._controller().status(chain_id)
        self.assertEqual(list(outside.iterdir()), [])

    @unittest.skipUnless(os.name == "nt", "Windows junction custody regression")
    def test_pending_leaf_junction_never_publishes_outside(self) -> None:
        controller = self._controller()
        chain_root = self._chain_root(self.identity.chain_id)
        chain_root.mkdir()
        pending = chain_root / (f".INPUT.bin.{bytes_sha256(_INPUT_SENTINEL)}.pending")
        outside = Path(self.temporary.name) / "outside-pending"
        self._make_junction(pending, outside)
        with self.assertRaisesRegex(StateConflictError, "no-follow|reparse|identity"):
            controller.begin(self.identity, _INPUT_SENTINEL)
        self.assertEqual(list(outside.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
