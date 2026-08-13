from __future__ import annotations

import json
import os
import tempfile
import unittest
from dataclasses import dataclass, replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from cera.adult_pipeline.contracts import (
    AdultFilterVerdict,
    AdultNextRoute,
    AdultRouteStateSnapshotV1,
)
from cera.adult_pipeline.pi_roles import AdultRoleViewContextV1
from cera.adult_pipeline.pipeline import AdultPipelineInputV1
from cera.errors import ErrorCode
from cera.pi_scene.adult_operation_store import ProtectedAdultOperationStore
from cera.pi_scene.adult_orchestration import PreparedAdultRouteOperationV1
from cera.pi_scene.adult_stage_retry_integration import (
    AdultPiStageAttemptOwnerFactoryV1,
    AdultStageRetryContinuationServiceV1,
    AdultStageRetryCoordinatorV1,
    AdultStageRetryExecutionInputV1,
    AdultStageRetryPacketFactoryV1,
    adult_stage_retry_runtime_adapters,
)
from cera.pi_scene.operation_ledger import PiProviderOperationLedger
from cera.pi_scene.pi_adapter import PiSceneAdapter
from cera.pi_scene.provider_stage_retry import (
    ProviderStage,
    ProviderStageFailureClass,
    ProviderStageRetryChainV1,
    ProviderStageRetryPhase,
)
from cera.pi_scene.provider_stage_retry_blob import TrustedLocalProtectedStageBlobStore
from cera.pi_scene.provider_stage_retry_executor import (
    ProviderStageAmbiguityResolutionV1,
    ProviderStageDispatchAmbiguousV1,
)
from cera.pi_scene.provider_stage_retry_packets import ProviderStageConfigurationV1
from cera.pi_scene.provider_stage_retry_runtime import (
    ProviderStageRetryRuntimeServiceV1,
    ProviderStageRuntimeAdapterV1,
    backend_action_from_envelope,
)
from cera.provider_dispatch_guard import assert_provider_dispatch_allowed
from cera.providers.models import (
    ProviderRetryableFailureCategory,
    ProviderTransportError,
)
from cera.serialization import canonical_sha256, text_sha256
from cera.storage.sqlite_store import SQLiteAuthorityStore
from tests.test_adult_pipeline_pi_integration import _filter_wire, _scene_wire
from tests.test_adult_pipeline_runtime import scene_request


@dataclass(frozen=True, slots=True)
class _Script:
    role: str
    outcome: str
    raw_json: str | None = None
    input_tokens: int = 100
    cached_input_tokens: int = 60
    output_tokens: int = 20
    reasoning_tokens: int = 7


class _ScriptedPiRunner:
    def __init__(self, scripts: list[_Script]) -> None:
        self.scripts = scripts
        self.calls: list[str] = []

    def __call__(
        self,
        command: object,
        cwd: Path,
        environment: object,
        timeout_seconds: int,
        on_line: object,
    ) -> SimpleNamespace:
        del command, cwd, environment, timeout_seconds
        if not self.scripts:
            raise AssertionError("unexpected Pi provider dispatch")
        script = self.scripts.pop(0)
        self.calls.append(script.role)
        callback = on_line
        assert callable(callback)
        session_id = f"session-{script.role}-{len(self.calls)}"
        if script.outcome == "process_start":
            raise OSError("scripted process start failure")
        if script.outcome == "process_exit":
            line = json.dumps({"type": "turn_start"})
            callback(line)
            return SimpleNamespace(returncode=7, stdout=line, stderr="scripted")
        if script.outcome == "stream_incomplete":
            line = json.dumps({"type": "turn_start"})
            callback(line)
            return SimpleNamespace(returncode=0, stdout=line, stderr="")
        if script.outcome == "output_invalid":
            line = json.dumps({"type": "turn_start"})
            callback(line)
            return SimpleNamespace(returncode=0, stdout=f"{line}\nnot-json", stderr="")
        if script.outcome in {"unavailable", "ambiguous", "configuration"}:
            if script.outcome == "configuration":
                raise ProviderTransportError(
                    ErrorCode.PROVIDER_CONFIG_INVALID,
                    "scripted typed configuration failure",
                    external_provider_calls_observed=0,
                )
            callback(json.dumps({"type": "turn_start"}))
            if script.outcome == "unavailable":
                raise ProviderTransportError(
                    ErrorCode.PROVIDER_TRANSPORT_FAILED,
                    "scripted closed provider failure",
                    external_provider_calls_observed=1,
                    retryable_failure_category=(
                        ProviderRetryableFailureCategory.PROVIDER_UNAVAILABLE
                    ),
                )
            raise RuntimeError("scripted unresolved dispatch")
        assert script.outcome in {"success", "completion_incomplete"}
        assert script.raw_json is not None
        events = (
            {"type": "session", "id": session_id},
            {"type": "turn_start"},
            {
                "type": "tool_execution_start",
                "toolName": "context",
                "toolCallId": "tool-1",
            },
            {
                "type": "tool_execution_end",
                "toolName": "context",
                "toolCallId": "tool-1",
                "isError": False,
            },
            {
                "type": "message_end",
                "message": {
                    "role": "assistant",
                    "usage": {
                        "input": script.input_tokens,
                        "cacheRead": script.cached_input_tokens,
                        "output": script.output_tokens,
                        "reasoning": script.reasoning_tokens,
                    },
                    "stopReason": (
                        "length" if script.outcome == "completion_incomplete" else "stop"
                    ),
                    "content": [{"type": "text", "text": script.raw_json}],
                },
            },
        )
        lines = tuple(json.dumps(event) for event in events)
        for line in lines:
            callback(line)
        return SimpleNamespace(returncode=0, stdout="\n".join(lines), stderr="")


def _filter_reject_wire() -> str:
    return json.dumps(
        {
            "verdict": "reject",
            "conflict": {
                "conflict_class": "logic_contradiction",
                "concise_explanation": "The staged decision conflicts with current logic.",
                "decision_key": "decision_one",
                "exact_quote": None,
            },
        },
        sort_keys=True,
        separators=(",", ":"),
    )


class _StepClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        self.value += 0.125
        return self.value


class _UnusedRegistration:
    def create_initial_owner(self, **kwargs: object) -> object:
        del kwargs
        raise AssertionError("unused provider-stage owner was selected")

    def create_retry_owner(self, **kwargs: object) -> object:
        del kwargs
        raise AssertionError("unused provider-stage owner was selected")

    def reconstruct_owner(self, **kwargs: object) -> object:
        del kwargs
        raise AssertionError("unused provider-stage owner was selected")

    def check_status(
        self,
        *,
        chain: ProviderStageRetryChainV1,
    ) -> ProviderStageAmbiguityResolutionV1 | None:
        del chain
        return None

    def recover_interrupted_dispatch(
        self,
        *,
        chain: ProviderStageRetryChainV1,
    ) -> ProviderStageDispatchAmbiguousV1:
        del chain
        raise AssertionError("unused provider-stage reconciler was selected")

    def downstream_intent_sha256(self, **kwargs: object) -> str:
        del kwargs
        raise AssertionError("unused provider-stage binder was selected")

    def bind_once(self, **kwargs: object) -> str:
        del kwargs
        raise AssertionError("unused provider-stage binder was selected")


class AdultStageRetryIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.provider_dispatch_guard = patch(
            "cera.adult_pipeline.pi_roles.assert_provider_dispatch_allowed",
            autospec=True,
        )
        self.provider_dispatch_guard.start()
        self.addCleanup(self.provider_dispatch_guard.stop)
        self.retry_owner_dispatch_guard = patch(
            "cera.pi_scene.adult_stage_retry_integration.assert_provider_dispatch_allowed",
            autospec=True,
        )
        self.retry_owner_dispatch_guard.start()
        self.addCleanup(self.retry_owner_dispatch_guard.stop)
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.database_path = self.root / "authority.sqlite3"
        self.protected_root = self.root / "protected"
        executable = self.root / "pi.cmd"
        extension = self.root / "extension.js"
        executable.write_text("offline fake", encoding="utf-8")
        extension.write_text("offline fake", encoding="utf-8")
        self.ledger = PiProviderOperationLedger(
            self.protected_root / "PI_PROVIDER_LEDGER.jsonl",
            maximum_operations=100,
            maximum_operations_per_invocation=6,
        )
        self.executable = executable
        self.extension = extension
        self.context = AdultRoleViewContextV1(
            world_id="world:adult-retry",
            branch_id="branch:adult-retry",
            scene_id="scene:adult-retry",
            turn_id="turn:adult-retry",
            candidate_id="candidate:adult-retry",
            current_state={"location": "private-room"},
            characters={"character:hana": {"name": "Hana", "age": 38}},
        )
        request = scene_request()
        self.prepared = AdultPipelineInputV1(
            request_id="request:adult-retry",
            candidate_id=self.context.candidate_id,
            world_id=self.context.world_id,
            branch_id=self.context.branch_id,
            accepted_head_sha256=text_sha256("accepted-head-before-adult-retry"),
            scene_request=request,
        )
        self.source = AdultStageRetryExecutionInputV1(
            generation_id="generation:adult-retry:1",
            prepared=self.prepared,
            role_context=self.context,
        )
        self.prepared_operation = PreparedAdultRouteOperationV1.create(
            request_id=self.prepared.request_id,
            candidate_id=self.prepared.candidate_id,
            route_state=AdultRouteStateSnapshotV1(
                schema_version=AdultRouteStateSnapshotV1.SCHEMA_VERSION,
                world_id=self.prepared.world_id,
                branch_id=self.prepared.branch_id,
                accepted_head_sha256=self.prepared.accepted_head_sha256,
                current_logic_route=AdultNextRoute.ORDINARY,
                source_promotion_sha256=text_sha256("source-promotion-before-adult-retry"),
            ),
            scene_request=self.prepared.scene_request,
        )
        self.packets = AdultStageRetryPacketFactoryV1(
            scene_configuration=self._configuration(ProviderStage.ADULT_SCENE),
            filter_configuration=self._configuration(ProviderStage.ADULT_FILTER),
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _configuration(self, stage: ProviderStage) -> ProviderStageConfigurationV1:
        return ProviderStageConfigurationV1.create(
            stage=stage,
            model_id="deepseek-v4-flash",
            reasoning_mode="off",
            routing={"role": stage.value},
            content_policy_route="protected_adult",
            stage_configuration={"automatic_retry": False},
        )

    def _adapter(self, runner: _ScriptedPiRunner) -> PiSceneAdapter:
        return PiSceneAdapter(
            pi_executable=self.executable,
            extension_path=self.extension,
            pi_version="0.50.2",
            operation_ledger=self.ledger,
            process_runner=runner,  # type: ignore[arg-type]
        )

    def test_budget_precheck_counts_unresolved_durable_reservation(self) -> None:
        ledger = PiProviderOperationLedger(
            self.protected_root / "BUDGET_PRECHECK_LEDGER.jsonl",
            maximum_operations=6,
            maximum_operations_per_invocation=6,
        )
        ledger.begin(
            candidate_id="candidate:adult-budget-reservation",
            purpose=ProviderStage.ADULT_SCENE.value,
            route="adult",
            request_sha256=text_sha256("adult-budget-reservation"),
        )
        adapter = PiSceneAdapter(
            pi_executable=self.executable,
            extension_path=self.extension,
            pi_version="0.50.2",
            operation_ledger=ledger,
        )
        owner_factory = AdultPiStageAttemptOwnerFactoryV1(
            stage=ProviderStage.ADULT_SCENE,
            pi_adapter=adapter,
            protected_runtime_root=self.protected_root / "budget-precheck",
        )

        with self.assertRaises(ProviderTransportError) as captured:
            owner_factory._assert_budget_available()

        self.assertEqual(captured.exception.code, ErrorCode.PROVIDER_BUDGET_EXCEEDED)
        self.assertEqual(captured.exception.external_provider_calls_observed, 0)

    def _service(
        self,
        adapter: PiSceneAdapter,
    ) -> ProviderStageRetryRuntimeServiceV1:
        adult = adult_stage_retry_runtime_adapters(
            pi_adapter=adapter,
            protected_runtime_root=self.protected_root / "adult-runtime",
        )
        unused = _UnusedRegistration()
        registrations: list[ProviderStageRuntimeAdapterV1] = []
        adult_by_stage = {registration.stage: registration for registration in adult}
        for stage in ProviderStage:
            registration = adult_by_stage.get(stage)
            registrations.append(
                registration
                if registration is not None
                else ProviderStageRuntimeAdapterV1(
                    stage=stage,
                    owner_factory=unused,  # type: ignore[arg-type]
                    ambiguity_reconciler=unused,
                    downstream_binder=unused,
                )
            )
        return ProviderStageRetryRuntimeServiceV1(
            authority_store=SQLiteAuthorityStore(self.database_path),
            protected_blob_store=TrustedLocalProtectedStageBlobStore(
                self.protected_root / "stage-blobs"
            ),
            registrations=tuple(registrations),
        )

    def _continuation(
        self,
        adapter: PiSceneAdapter,
    ) -> tuple[
        ProviderStageRetryRuntimeServiceV1,
        AdultStageRetryContinuationServiceV1,
    ]:
        service = self._service(adapter)
        return service, AdultStageRetryContinuationServiceV1(
            runtime=service,
            packets=self.packets,
            pi_adapter=adapter,
            operation_store=ProtectedAdultOperationStore(self.protected_root / "adult-operations"),
            protected_mapping_root=self.protected_root / "adult-continuations",
        )

    def test_scene_freezes_before_filter_with_exact_ledger_metrics(self) -> None:
        runner = _ScriptedPiRunner(
            [
                _Script(
                    "adult_scene",
                    "success",
                    _scene_wire(),
                    input_tokens=101,
                    cached_input_tokens=61,
                    output_tokens=21,
                    reasoning_tokens=8,
                ),
                _Script(
                    "adult_filter",
                    "success",
                    _filter_wire(),
                    input_tokens=202,
                    cached_input_tokens=122,
                    output_tokens=42,
                    reasoning_tokens=16,
                ),
            ]
        )
        service = self._service(self._adapter(runner))
        coordinator = AdultStageRetryCoordinatorV1(runtime=service, packets=self.packets)

        with patch(
            "cera.adult_pipeline.pi_roles.time.perf_counter",
            new=_StepClock(),
        ):
            progress = coordinator.execute_or_continue(self.source)

        self.assertTrue(progress.completed)
        self.assertEqual(runner.calls, ["adult_scene", "adult_filter"])
        self.assertNotEqual(progress.scene_chain_id, progress.filter_chain_id)
        scene_chain = service.read_chain(progress.scene_chain_id)
        assert progress.filter_chain_id is not None
        filter_chain = service.read_chain(progress.filter_chain_id)
        self.assertEqual(scene_chain.phase, ProviderStageRetryPhase.SUCCEEDED)
        self.assertEqual(filter_chain.phase, ProviderStageRetryPhase.SUCCEEDED)
        self.assertEqual(scene_chain.attempts[0].input_tokens, 101)
        self.assertEqual(scene_chain.attempts[0].cached_input_tokens, 61)
        self.assertEqual(scene_chain.attempts[0].duration_ms, 125)
        self.assertEqual(filter_chain.attempts[0].input_tokens, 202)
        self.assertEqual(filter_chain.attempts[0].reasoning_tokens, 16)
        self.assertEqual(filter_chain.attempts[0].duration_ms, 125)
        self.assertEqual(scene_chain.attempts[0].provider_operations_observed, 1)
        self.assertEqual(filter_chain.attempts[0].provider_operations_observed, 1)

        sqlite_bytes = self.database_path.read_bytes()
        wal = Path(str(self.database_path) + "-wal")
        if wal.is_file():
            sqlite_bytes += wal.read_bytes()
        self.assertNotIn(
            self.prepared.scene_request.exact_current_source.encode("utf-8"),
            sqlite_bytes,
        )

    def test_manual_filter_retry_resumes_without_scene_redispatch_after_restart(self) -> None:
        runner = _ScriptedPiRunner(
            [
                _Script("adult_scene", "success", _scene_wire()),
                _Script("adult_filter", "unavailable"),
                _Script("adult_filter", "success", _filter_wire()),
            ]
        )
        adapter = self._adapter(runner)
        first_service = self._service(adapter)
        first = AdultStageRetryCoordinatorV1(
            runtime=first_service,
            packets=self.packets,
        )

        pending = first.execute_or_continue(self.source)
        self.assertFalse(pending.completed)
        self.assertIs(pending.pending_stage, ProviderStage.ADULT_FILTER)
        self.assertEqual(runner.calls, ["adult_scene", "adult_filter"])
        replay = first.execute_or_continue(self.source)
        self.assertEqual(replay.pending_chain_id, pending.pending_chain_id)
        self.assertEqual(runner.calls, ["adult_scene", "adult_filter"])

        restarted_service = self._service(adapter)
        assert pending.pending_chain_id is not None
        envelope = restarted_service.canonical_status(chain_id=pending.pending_chain_id)
        action = backend_action_from_envelope(envelope)
        restarted_service.execute_manual_retry(action)
        restarted = AdultStageRetryCoordinatorV1(
            runtime=restarted_service,
            packets=self.packets,
        )
        completed = restarted.execute_or_continue(self.source)

        self.assertTrue(completed.completed)
        self.assertEqual(
            runner.calls,
            ["adult_scene", "adult_filter", "adult_filter"],
        )
        filter_chain = restarted_service.read_chain(pending.pending_chain_id)
        self.assertEqual(filter_chain.attempts_total, 2)
        self.assertEqual(filter_chain.phase, ProviderStageRetryPhase.SUCCEEDED)
        self.assertEqual(
            self.prepared.accepted_head_sha256,
            self.source.prepared.accepted_head_sha256,
        )

    def test_ambiguity_is_get_only_and_never_dispatches_again(self) -> None:
        runner = _ScriptedPiRunner([_Script("adult_scene", "ambiguous")])
        service = self._service(self._adapter(runner))
        coordinator = AdultStageRetryCoordinatorV1(runtime=service, packets=self.packets)

        pending = coordinator.execute_or_continue(self.source)
        self.assertIs(pending.pending_stage, ProviderStage.ADULT_SCENE)
        self.assertEqual(runner.calls, ["adult_scene"])
        replay = coordinator.execute_or_continue(self.source)
        self.assertEqual(replay.pending_chain_id, pending.pending_chain_id)
        self.assertEqual(runner.calls, ["adult_scene"])

        assert pending.pending_chain_id is not None
        envelope = service.canonical_status(chain_id=pending.pending_chain_id)
        action = backend_action_from_envelope(envelope)
        self.assertEqual(action["action_kind"], "check_status")
        service.check_status(action)
        self.assertEqual(runner.calls, ["adult_scene"])
        self.assertEqual(
            service.read_chain(pending.pending_chain_id).phase,
            ProviderStageRetryPhase.BLOCKED_AMBIGUOUS,
        )

    def test_filter_attempt_exhaustion_stops_at_three_without_scene_replay(self) -> None:
        runner = _ScriptedPiRunner(
            [
                _Script("adult_scene", "success", _scene_wire()),
                _Script("adult_filter", "unavailable"),
                _Script("adult_filter", "unavailable"),
                _Script("adult_filter", "unavailable"),
            ]
        )
        service = self._service(self._adapter(runner))
        coordinator = AdultStageRetryCoordinatorV1(runtime=service, packets=self.packets)
        pending = coordinator.execute_or_continue(self.source)
        assert pending.pending_chain_id is not None

        for expected_ordinal in (1, 2):
            envelope = service.canonical_status(chain_id=pending.pending_chain_id)
            action = backend_action_from_envelope(envelope)
            self.assertEqual(action["retry_action_ordinal"], expected_ordinal)
            service.execute_manual_retry(action)

        exhausted = service.read_chain(pending.pending_chain_id)
        self.assertEqual(exhausted.attempts_total, 3)
        self.assertEqual(exhausted.phase, ProviderStageRetryPhase.EXHAUSTED)
        status = service.canonical_status(chain_id=pending.pending_chain_id)
        self.assertEqual(status["actions"], [])
        replay = coordinator.execute_or_continue(self.source)
        self.assertEqual(replay.pending_chain_id, pending.pending_chain_id)
        self.assertEqual(
            runner.calls,
            ["adult_scene", "adult_filter", "adult_filter", "adult_filter"],
        )

    def test_filter_semantic_reject_is_success_without_provider_retry(self) -> None:
        runner = _ScriptedPiRunner(
            [
                _Script("adult_scene", "success", _scene_wire()),
                _Script("adult_filter", "success", _filter_reject_wire()),
            ]
        )
        service = self._service(self._adapter(runner))
        coordinator = AdultStageRetryCoordinatorV1(runtime=service, packets=self.packets)

        completed = coordinator.execute_or_continue(self.source)

        self.assertTrue(completed.completed)
        assert completed.execution is not None
        self.assertIs(
            completed.execution.result.filtered.invocation.decision.verdict,
            AdultFilterVerdict.REJECT,
        )
        self.assertEqual(runner.calls, ["adult_scene", "adult_filter"])
        assert completed.filter_chain_id is not None
        filter_chain = service.read_chain(completed.filter_chain_id)
        self.assertEqual(filter_chain.attempts_total, 1)
        self.assertEqual(filter_chain.phase, ProviderStageRetryPhase.SUCCEEDED)

    def test_typed_pi_boundary_failures_use_ledger_not_error_messages(self) -> None:
        cases = (
            ("process_start", ProviderStageFailureClass.PROVIDER_PROCESS_FAILED, 0),
            ("process_exit", ProviderStageFailureClass.PROVIDER_PROCESS_FAILED, 1),
            ("stream_incomplete", ProviderStageFailureClass.PROVIDER_STREAM_INCOMPLETE, 1),
            ("output_invalid", ProviderStageFailureClass.PROVIDER_OUTPUT_INVALID, 1),
        )
        for outcome, failure_class, operations in cases:
            with self.subTest(outcome=outcome):
                runner = _ScriptedPiRunner(
                    [
                        _Script(
                            "adult_scene",
                            outcome,
                        )
                    ]
                )
                service = self._service(self._adapter(runner))
                source = replace(
                    self.source,
                    generation_id=f"generation:adult-retry:{outcome}",
                )
                coordinator = AdultStageRetryCoordinatorV1(
                    runtime=service,
                    packets=self.packets,
                )

                with patch(
                    "cera.adult_pipeline.pi_roles.time.perf_counter",
                    new=_StepClock(),
                ):
                    pending = coordinator.execute_or_continue(source)

                self.assertFalse(pending.completed)
                self.assertIs(pending.pending_stage, ProviderStage.ADULT_SCENE)
                chain = service.read_chain(pending.scene_chain_id)
                self.assertEqual(chain.phase, ProviderStageRetryPhase.OWNER_RETIRED)
                self.assertIs(chain.attempts[0].failure_class, failure_class)
                self.assertEqual(
                    chain.attempts[0].provider_operations_observed,
                    operations,
                )
                self.assertEqual(chain.attempts[0].duration_ms, 125)
                self.assertEqual(runner.calls, ["adult_scene"])

    def test_output_limit_truncation_requires_recovery_without_retry(self) -> None:
        runner = _ScriptedPiRunner([_Script("adult_scene", "completion_incomplete", _scene_wire())])
        service = self._service(self._adapter(runner))
        coordinator = AdultStageRetryCoordinatorV1(runtime=service, packets=self.packets)

        with patch(
            "cera.adult_pipeline.pi_roles.time.perf_counter",
            new=_StepClock(),
        ):
            pending = coordinator.execute_or_continue(self.source)

        self.assertFalse(pending.completed)
        chain = service.read_chain(pending.scene_chain_id)
        self.assertEqual(chain.phase, ProviderStageRetryPhase.RECOVERY_REQUIRED)
        self.assertIs(
            chain.attempts[0].failure_class,
            ProviderStageFailureClass.OUTPUT_LIMIT_TRUNCATED,
        )
        self.assertEqual(chain.attempts[0].provider_operations_observed, 1)
        self.assertEqual(chain.attempts[0].provider_operations_conservative, 1)
        self.assertEqual(chain.attempts[0].duration_ms, 125)
        self.assertEqual(service.canonical_status(chain_id=chain.identity.chain_id)["actions"], [])
        self.assertEqual(runner.calls, ["adult_scene"])

    def test_dispatch_guard_fails_before_reservation_and_provider_call(self) -> None:
        runner = _ScriptedPiRunner([_Script("adult_scene", "success", _scene_wire())])
        service = self._service(self._adapter(runner))
        coordinator = AdultStageRetryCoordinatorV1(runtime=service, packets=self.packets)

        with (
            patch.dict(os.environ, {"CERA_PROVIDER_DISPATCH_DISABLED": "1"}),
            patch(
                "cera.pi_scene.adult_stage_retry_integration.assert_provider_dispatch_allowed",
                new=assert_provider_dispatch_allowed,
            ),
        ):
            pending = coordinator.execute_or_continue(self.source)

        self.assertFalse(pending.completed)
        chain = service.read_chain(pending.scene_chain_id)
        self.assertEqual(chain.phase, ProviderStageRetryPhase.RECOVERY_REQUIRED)
        self.assertIs(
            chain.attempts[0].failure_class,
            ProviderStageFailureClass.PROVIDER_FAILURE_NOT_RETRYABLE,
        )
        self.assertEqual(chain.attempts[0].provider_operations_observed, 0)
        self.assertEqual(chain.attempts[0].provider_operations_conservative, 0)
        self.assertEqual(service.canonical_status(chain_id=chain.identity.chain_id)["actions"], [])
        self.assertEqual(runner.calls, [])

    def test_interrupted_success_recovers_from_disposition_without_redispatch(self) -> None:
        runner = _ScriptedPiRunner([_Script("adult_scene", "success", _scene_wire())])
        adapter = self._adapter(runner)
        service = self._service(adapter)
        packet = self.packets.scene_packet(self.source)
        scope = self.packets.scope(self.source, packet)

        with (
            patch(
                "cera.adult_pipeline.pi_roles.time.perf_counter",
                new=_StepClock(),
            ),
            patch.object(
                service._executor,  # noqa: SLF001 - deliberate crash-window seam
                "_freeze_result",
                side_effect=RuntimeError("scripted executor checkpoint crash"),
            ),
            self.assertRaises(RuntimeError),
        ):
            service.start_initial(scope=scope, packet=packet)

        self.assertEqual(
            service.read_chain(scope.identity.chain_id).phase,
            ProviderStageRetryPhase.DISPATCH_STARTED,
        )
        restarted = self._service(adapter)
        recovered = restarted.recover_incomplete(scope.identity.chain_id)
        self.assertEqual(recovered.phase, ProviderStageRetryPhase.RESULT_FROZEN)
        self.assertEqual(runner.calls, ["adult_scene"])
        self.assertEqual(recovered.attempts[0].duration_ms, 125)
        self.assertEqual(recovered.attempts[0].input_tokens, 100)

    def test_interrupted_retryable_failure_recovers_without_redispatch(self) -> None:
        runner = _ScriptedPiRunner([_Script("adult_scene", "unavailable")])
        adapter = self._adapter(runner)
        service = self._service(adapter)
        packet = self.packets.scene_packet(self.source)
        scope = self.packets.scope(self.source, packet)

        with (
            patch.object(
                service._executor,  # noqa: SLF001 - deliberate crash-window seam
                "_close_failure",
                side_effect=RuntimeError("scripted executor checkpoint crash"),
            ),
            self.assertRaises(RuntimeError),
        ):
            service.start_initial(scope=scope, packet=packet)

        restarted = self._service(adapter)
        recovered = restarted.recover_incomplete(scope.identity.chain_id)
        self.assertEqual(recovered.phase, ProviderStageRetryPhase.OWNER_RETIRED)
        self.assertEqual(runner.calls, ["adult_scene"])
        self.assertEqual(recovered.attempts[0].provider_operations_observed, 1)

    def test_interrupted_non_retry_failure_recovers_without_redispatch(self) -> None:
        runner = _ScriptedPiRunner([_Script("adult_scene", "configuration")])
        adapter = self._adapter(runner)
        service = self._service(adapter)
        packet = self.packets.scene_packet(self.source)
        scope = self.packets.scope(self.source, packet)

        with (
            patch.object(
                service._executor,  # noqa: SLF001 - deliberate crash-window seam
                "_close_non_retryable_failure",
                side_effect=RuntimeError("scripted executor checkpoint crash"),
            ),
            self.assertRaises(RuntimeError),
        ):
            service.start_initial(scope=scope, packet=packet)

        restarted = self._service(adapter)
        recovered = restarted.recover_incomplete(scope.identity.chain_id)
        self.assertEqual(recovered.phase, ProviderStageRetryPhase.RECOVERY_REQUIRED)
        self.assertEqual(runner.calls, ["adult_scene"])
        self.assertEqual(recovered.attempts[0].provider_operations_observed, 0)

    def test_continuation_restart_recovers_source_and_binds_terminal_operation(self) -> None:
        runner = _ScriptedPiRunner(
            [
                _Script("adult_scene", "success", _scene_wire()),
                _Script("adult_filter", "success", _filter_wire()),
            ]
        )
        adapter = self._adapter(runner)
        service, continuation = self._continuation(adapter)
        scene_packet = self.packets.scene_packet(self.source)
        scene_chain_id = self.packets.scope(self.source, scene_packet).identity.chain_id

        with (
            patch.object(
                service._executor,  # noqa: SLF001 - deliberate crash-window seam
                "_freeze_result",
                side_effect=RuntimeError("scripted executor checkpoint crash"),
            ),
            self.assertRaises(RuntimeError),
        ):
            continuation.execute_or_continue(
                prepared=self.prepared_operation,
                source=self.source,
            )

        restarted_service, restarted = self._continuation(adapter)
        restarted.status(scene_chain_id)
        self.assertEqual(runner.calls, ["adult_scene"])
        self.assertEqual(
            restarted_service.read_chain(scene_chain_id).phase,
            ProviderStageRetryPhase.RESULT_FROZEN,
        )
        self.assertEqual(
            canonical_sha256(restarted.source_for_chain(scene_chain_id)),
            canonical_sha256(self.source),
        )

        completed = restarted.resume_succeeded_chain(scene_chain_id)

        self.assertTrue(completed.completed)
        self.assertEqual(runner.calls, ["adult_scene", "adult_filter"])
        assert completed.filter_chain_id is not None
        self.assertEqual(
            restarted.latest_chain_for_chain(scene_chain_id),
            completed.filter_chain_id,
        )
        terminal_by_chain = restarted.load_terminal_for_chain(scene_chain_id)
        terminal_by_request = restarted.load_terminal_for_request(
            request_id=self.prepared.request_id,
            candidate_id=self.prepared.candidate_id,
        )
        self.assertEqual(terminal_by_chain, completed.execution)
        self.assertEqual(terminal_by_request, completed.execution)
        mapping_bytes = b"".join(
            path.read_bytes()
            for path in (self.protected_root / "adult-continuations").rglob("*.json")
        )
        self.assertNotIn(
            self.prepared.scene_request.exact_current_source.encode("utf-8"),
            mapping_bytes,
        )

    def test_filter_successor_cursor_is_bound_before_interrupted_dispatch_closes(self) -> None:
        runner = _ScriptedPiRunner(
            [
                _Script("adult_scene", "success", _scene_wire()),
                _Script("adult_filter", "unavailable"),
            ]
        )
        adapter = self._adapter(runner)
        service, continuation = self._continuation(adapter)
        scene_packet = self.packets.scene_packet(self.source)
        scene_chain_id = self.packets.scope(self.source, scene_packet).identity.chain_id

        with (
            patch.object(
                service._executor,  # noqa: SLF001 - deliberate crash-window seam
                "_close_failure",
                side_effect=RuntimeError("scripted executor checkpoint crash"),
            ),
            self.assertRaises(RuntimeError),
        ):
            continuation.execute_or_continue(
                prepared=self.prepared_operation,
                source=self.source,
            )

        filter_chain_id = continuation.latest_chain_for_chain(scene_chain_id)
        self.assertNotEqual(filter_chain_id, scene_chain_id)
        self.assertEqual(runner.calls, ["adult_scene", "adult_filter"])
        restarted_service, restarted = self._continuation(adapter)
        restarted.status(filter_chain_id)
        self.assertEqual(runner.calls, ["adult_scene", "adult_filter"])
        self.assertEqual(
            restarted_service.read_chain(filter_chain_id).phase,
            ProviderStageRetryPhase.OWNER_RETIRED,
        )
        self.assertEqual(
            canonical_sha256(restarted.source_for_chain(filter_chain_id)),
            canonical_sha256(self.source),
        )

    def test_continuation_duplicate_retry_action_replays_terminal_without_dispatch(self) -> None:
        runner = _ScriptedPiRunner(
            [
                _Script("adult_scene", "success", _scene_wire()),
                _Script("adult_filter", "unavailable"),
                _Script("adult_filter", "success", _filter_wire()),
            ]
        )
        adapter = self._adapter(runner)
        _, continuation = self._continuation(adapter)
        pending = continuation.execute_or_continue(
            prepared=self.prepared_operation,
            source=self.source,
        )
        assert pending.pending_chain_id is not None
        action = backend_action_from_envelope(continuation.status(pending.pending_chain_id))

        completed = continuation.execute_action(action)
        replayed = continuation.execute_action(action)

        self.assertTrue(completed.completed)
        self.assertEqual(replayed.execution, completed.execution)
        self.assertEqual(
            runner.calls,
            ["adult_scene", "adult_filter", "adult_filter"],
        )
        self.assertEqual(
            continuation.latest_chain_for_chain(completed.scene_chain_id),
            completed.filter_chain_id,
        )
        self.assertEqual(
            continuation.load_terminal_for_chain(pending.pending_chain_id),
            completed.execution,
        )


if __name__ == "__main__":
    unittest.main()
