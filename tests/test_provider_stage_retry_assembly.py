from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from cera.continuous.call_ledger import ContinuousProviderCallLedger
from cera.continuous.provider import ContinuousProviderResultV1
from cera.errors import ErrorCode
from cera.evaluation import EvaluationRole
from cera.ids import IdKind, TypedId
from cera.pi_scene._world_workspace_files import lean_scene_branch_root
from cera.pi_scene.adult_operation_store import ProtectedAdultOperationStore
from cera.pi_scene.contracts import SceneRoute
from cera.pi_scene.full_model_controller import RejectedAdultTurnV1
from cera.pi_scene.http import PiSceneHttpAdapter
from cera.pi_scene.http_contracts import LeanSceneRequestControlsV2
from cera.pi_scene.operation_ledger import PiProviderOperationLedger
from cera.pi_scene.pi_adapter import PiSceneAdapter
from cera.pi_scene.provider_stage_retry import ProviderStage, ProviderStageAttemptPhase
from cera.pi_scene.provider_stage_retry_adapters import (
    ProviderStageBoundaryKind,
    ProviderStageLedgerSnapshotV1,
    ProviderStageReceiptMetricsV1,
)
from cera.pi_scene.provider_stage_retry_assembly import (
    OrdinaryProviderStageAttemptOwnerFactoryV1,
    OrdinaryStageProviderPortsV1,
    ProviderStageOwnerLifecycleClass,
    _LateBoundOrdinaryHttpContinuationV1,
    _ProtectedTerminalCompletionStoreV1,
    build_provider_stage_retry_production_assembly,
)
from cera.pi_scene.provider_stage_retry_executor import (
    ProviderStageSemanticDisposition,
    ProviderStageSuccessfulResultV1,
)
from cera.pi_scene.provider_stage_retry_http import ProviderStageRetryHttpNotFoundError
from cera.pi_scene.provider_stage_retry_ordinary import (
    OrdinaryStageRetryRequestContextV1,
)
from cera.pi_scene.provider_stage_retry_packets import ProviderStageFrozenPacketV1
from cera.pi_scene.provider_stage_retry_runtime import backend_action_from_envelope
from cera.pi_scene.provider_stage_retry_scope import (
    ProviderStageRetryOccurrenceScopeV1,
)
from cera.pi_scene.request_binding import build_request_binding
from cera.pi_scene.review_store import LeanSceneTurnInputV1
from cera.pi_scene.runtime import (
    PlannerTurnInputV1,
    PlannerTurnOutputV1,
    ProviderStageRetryPendingError,
)
from cera.pi_scene.store import LeanSceneStore
from cera.provider_dispatch_guard import PROVIDER_DISPATCH_DISABLED_ENV
from cera.providers.models import (
    LiveProviderCallReceipt,
    ModelIdentitySource,
    ProviderName,
    ProviderRetryableFailureCategory,
    ProviderTransportError,
)
from cera.serialization import (
    bytes_sha256,
    canonical_bytes,
    canonical_sha256,
    text_sha256,
    to_primitive,
)
from scripts.run_pi_scene_lean_server import (
    FullModelProviderComponents,
    _PiScenePlannerRegistry,
    build_live_runtime,
)
from tests import test_pi_scene_full_model_controller as full_model_support
from tests.test_adult_pipeline_pi_integration import (
    PROTECTED_PROSE,
    _filter_wire,
    _scene_wire,
)
from tests.test_adult_turn_preparation import _turn
from tests.test_pi_scene_adult_orchestration import _RejectingTransport
from tests.test_pi_scene_adult_stage_retry_integration import _Script, _ScriptedPiRunner
from tests.test_pi_scene_full_model_adult_operation_custody import (
    FullModelAdultOperationCustodyTests,
)


class _NoLifecycle:
    external_provider_boundary = False


class _NoLunaBackend:
    external_provider_boundary = False


class _NoReaderBackend:
    external_provider_boundary = False


class _NoDispatchSemanticValidator:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    def validate(self, *_args, **_kwargs):
        self.calls.append("luna_validate")
        raise AssertionError("assembly construction dispatched Luna")


class _NoDispatchReaderValidator:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    def validate(self, *_args, **_kwargs):
        self.calls.append("reader_validate")
        raise AssertionError("assembly construction dispatched Reader")


def _planner_receipt(
    label: str,
    *,
    input_tokens: int,
) -> LiveProviderCallReceipt:
    return LiveProviderCallReceipt(
        schema_version=LiveProviderCallReceipt.SCHEMA_VERSION,
        provider_receipt_id=TypedId(IdKind.PROVIDER_RECEIPT, f"assembly-{label}"),
        route_sha256=text_sha256("assembly-planner-route"),
        provider=ProviderName.OPENAI_CODEX,
        role=EvaluationRole.SCENE_REASONER,
        requested_model="gpt-5.6-sol",
        returned_model="gpt-5.6-sol",
        model_revision="provider-unverified",
        model_identity_source=ModelIdentitySource.EXPLICIT_REQUEST,
        model_identity_verified=False,
        request_sha256=text_sha256(f"assembly-request:{label}"),
        output_sha256=text_sha256(f"assembly-output:{label}"),
        provider_request_id_sha256=text_sha256(f"provider-request:{label}"),
        system_fingerprint_sha256=None,
        duration_ms=10 + input_tokens,
        input_tokens=input_tokens,
        cached_input_tokens=input_tokens // 2,
        output_tokens=20 + input_tokens,
        reasoning_output_tokens=5 + input_tokens,
        cost_microusd=0,
        cost_is_estimate=False,
        quota_metered=True,
        external_provider_calls=1,
        automatic_retry_count=0,
        story_authority_writes=0,
        retains_raw_source=False,
        retains_story_prose=False,
        retains_private_evidence=False,
        retains_prompt=False,
        retains_secret=False,
    )


class _RetainedPlannerFailureThenSuccess:
    """Provider-free Planner double with a real Sol ledger and thread fence."""

    def __init__(self, ledger: ContinuousProviderCallLedger) -> None:
        self.ledger = ledger
        self.thread_sha256: str | None = text_sha256("assembly-planner-thread-a")
        self.physical_threads: list[str] = []
        self.fresh_thread_starts: list[str] = []
        self.retired_threads: list[str] = []
        self.receipts: list[LiveProviderCallReceipt] = []
        self.last_provider_result: ContinuousProviderResultV1 | None = None

    def plan(self, _request: PlannerTurnInputV1) -> PlannerTurnOutputV1:
        attempt_number = len(self.physical_threads) + 1
        if self.thread_sha256 is None:
            self.thread_sha256 = text_sha256("assembly-planner-thread-b")
            self.fresh_thread_starts.append(self.thread_sha256)
        thread_sha256 = self.thread_sha256
        receipt = _planner_receipt(
            f"attempt-{attempt_number}",
            input_tokens=100 + attempt_number,
        )
        self.physical_threads.append(thread_sha256)
        self.receipts.append(receipt)

        def dispatch(mark_transport_invoked) -> ContinuousProviderResultV1:
            mark_transport_invoked()
            if attempt_number == 1:
                raise ProviderTransportError(
                    ErrorCode.PROVIDER_TRANSPORT_FAILED,
                    "provider-free injected Planner timeout",
                    external_provider_calls_observed=1,
                    provider_call_receipt=receipt,
                    retryable_failure_category=(ProviderRetryableFailureCategory.TRANSPORT_TIMEOUT),
                )
            result = ContinuousProviderResultV1(
                value=PlannerTurnOutputV1(
                    sequence={
                        "items": [{"item_key": "planner_retry_succeeded"}],
                        "resulting_public_state": "The conversational floor remains open.",
                    },
                    provider_operations=1,
                ),
                provider_receipt=receipt,
                operation_telemetry=None,
                tool_call_count=0,
                failed_tool_call_count=0,
                physical_session_sha256=thread_sha256,
            )
            self.last_provider_result = result
            return result

        return self.ledger.execute(
            owner="planner",
            operation=f"assembly_retry_attempt_{attempt_number}",
            route="codex_app",
            model="gpt-5.6-sol",
            effort="medium",
            dispatch_with_invocation_marker=dispatch,
            finalize=lambda result: result.value,
            receipt_of=lambda result: result.provider_receipt,
            stored_thread_sha256=thread_sha256,
        )

    def active_provider_thread_sha256(self) -> str | None:
        return self.thread_sha256

    def reset_provider_thread_after_transport_failure(
        self,
        expected_thread_sha256: str,
    ) -> None:
        if expected_thread_sha256 != self.thread_sha256:
            raise AssertionError("Planner retirement changed physical owner")
        self.retired_threads.append(expected_thread_sha256)
        self.thread_sha256 = None


class _OrdinaryCompletionCustody:
    def __init__(self) -> None:
        self.request_id = "request:provisional"
        self.context_payload = {"context": "provisional"}
        self.context_sha256 = canonical_sha256(self.context_payload)
        self.bound: list[tuple[str, object]] = []
        self.redactions: list[tuple[str, str, str]] = []

    def review_action_for_chain(self, _chain_id: str) -> None:
        return None

    def chain_request_identity(self, _chain_id: str) -> object:
        return SimpleNamespace(
            request_id=self.request_id,
            context_sha256=self.context_sha256,
        )

    def pending_request_for_review(self, review_id: str) -> tuple[object, object, object]:
        if review_id != "review:provisional":
            raise AssertionError("unexpected provisional review")
        return (
            {},
            SimpleNamespace(
                binding=SimpleNamespace(request_id=self.request_id),
                context_payload=lambda: self.context_payload,
            ),
            {},
        )

    def bind_terminal_response_for_chain(
        self,
        *,
        chain_id: str,
        response: object,
    ) -> object:
        self.bound.append((chain_id, response))
        return SimpleNamespace(response_sha256=canonical_sha256(response))

    def redact_pending_request(
        self,
        request_id: str,
        *,
        disposition: str,
        terminal_evidence_sha256: str,
    ) -> None:
        self.redactions.append((request_id, disposition, terminal_evidence_sha256))


class ProviderStageRetryAssemblyTests(unittest.TestCase):
    def test_validation_lane_never_freezes_a_stale_terminal_completion(self) -> None:
        ordinary = _OrdinaryCompletionCustody()
        chain_id = "stage-retry-" + "a" * 64
        current = {"schema_version": "cera.pi_scene.review.v2", "state": "validating"}

        class Adapter:
            def validation_lane_binding_for_chain(self, value: str) -> dict[str, str]:
                return {
                    "schema_version": "cera.pi_scene.review_validation_lanes.v1",
                    "review_id": "review-" + "b" * 28,
                    "lane": "reader",
                    "chain_id": value,
                    "luna_chain_id": "stage-retry-" + "c" * 64,
                    "reader_chain_id": value,
                }

            def review_payload_for_validation_chain(self, _value: str) -> dict[str, object]:
                return dict(current)

        adapter = Adapter()
        with tempfile.TemporaryDirectory() as temporary:
            completion_root = Path(temporary)
            continuation = _LateBoundOrdinaryHttpContinuationV1(
                ordinary=ordinary,  # type: ignore[arg-type]
                completions=_ProtectedTerminalCompletionStoreV1(completion_root),
            )
            continuation.bind_adapter(adapter)  # type: ignore[arg-type]

            self.assertIsNone(continuation.load_terminal_completion(chain_id))
            self.assertEqual(
                continuation.bind_terminal_completion(
                    chain_id=chain_id,
                    completion={"state": "obsolete_lane_snapshot"},
                ),
                current,
            )
            self.assertEqual(tuple(completion_root.iterdir()), ())
            current["state"] = "accepted"
            self.assertEqual(
                continuation.review_payload_for_validation_chain(chain_id)["state"],
                "accepted",
            )

    def test_provisional_retry_completion_retains_original_request_custody(self) -> None:
        ordinary = _OrdinaryCompletionCustody()
        with tempfile.TemporaryDirectory() as temporary:
            continuation = _LateBoundOrdinaryHttpContinuationV1(
                ordinary=ordinary,  # type: ignore[arg-type]
                completions=_ProtectedTerminalCompletionStoreV1(Path(temporary)),
            )
            provisional_chain = "stage-retry-" + "1" * 64
            provisional = {
                "cera": {
                    "provisional": True,
                    "provisional_review_id": "review:provisional",
                }
            }
            self.assertEqual(
                continuation.bind_terminal_completion(
                    chain_id=provisional_chain,
                    completion=provisional,
                ),
                provisional,
            )
            self.assertEqual(ordinary.redactions, [])

            accepted_chain = "stage-retry-" + "2" * 64
            accepted = {"cera": {"provisional": False}}
            continuation.bind_terminal_completion(
                chain_id=accepted_chain,
                completion=accepted,
            )
            self.assertEqual(len(ordinary.redactions), 1)
            self.assertEqual(
                ordinary.redactions[0][:2],
                (ordinary.request_id, "completed"),
            )

    def test_ordinary_owner_is_lazy_until_executor_dispatch(self) -> None:
        calls: list[str] = []
        ledger_events: list[str] = []

        def ledger_snapshot() -> ProviderStageLedgerSnapshotV1:
            return ProviderStageLedgerSnapshotV1(
                prefix_sha256=canonical_sha256(tuple(ledger_events)),
                provider_operations_total=len(ledger_events),
            )

        def invoke_provider(request: object, _chain: str, _attempt: int) -> object:
            calls.append("provider")
            ledger_events.append("provider_operation_started")
            return {"request": request, "result": "ok"}

        with tempfile.TemporaryDirectory() as temporary:
            exact_input = canonical_bytes(
                {
                    "schema_version": ProviderStageFrozenPacketV1.SCHEMA_VERSION,
                    "stage": ProviderStage.WRITER.value,
                    "packet_kind": "assembly_lazy_boundary_test",
                }
            )
            packet = ProviderStageFrozenPacketV1(
                schema_version=ProviderStageFrozenPacketV1.SCHEMA_VERSION,
                stage=ProviderStage.WRITER,
                packet_kind="assembly_lazy_boundary_test",
                exact_bytes=exact_input,
                stage_input_sha256=bytes_sha256(exact_input),
            )
            scope = ProviderStageRetryOccurrenceScopeV1.create(
                world_id="world:test",
                branch_id="branch:test",
                request_id="request:test",
                generation_id="generation:test",
                stage=ProviderStage.WRITER,
                stage_ordinal=1,
                accepted_state_sha256=canonical_sha256({"head": None}),
                exact_input=exact_input,
                authority_binding={"test": "lazy"},
            )
            factory = OrdinaryProviderStageAttemptOwnerFactoryV1(
                stage=ProviderStage.WRITER,
                lifecycle=ProviderStageOwnerLifecycleClass.ONE_SHOT_PROCESS,
                boundary_kind=ProviderStageBoundaryKind.PI_DEEPSEEK,
                maximum_provider_operations=1,
                protected_root=Path(temporary) / "owners",
                read_current_ledger=ledger_snapshot,
                read_ledger_prefix=lambda prefix: (
                    ledger_snapshot()
                    if ledger_snapshot().prefix_sha256 == prefix
                    else (_ for _ in ()).throw(AssertionError("unknown ledger prefix"))
                ),
                build_request=lambda value: calls.append("build") or value,
                invoke_provider=invoke_provider,
                serialize_result=lambda _result: canonical_bytes({"result": "ok"}),
                result_receipt_metrics=lambda _result: ProviderStageReceiptMetricsV1(
                    receipt_evidence_sha256=canonical_sha256({"receipt": "test"}),
                    provider_operations=1,
                    duration_ms=1,
                ),
                semantic_disposition=lambda _result: (
                    ProviderStageSemanticDisposition.NOT_APPLICABLE
                ),
                retire_failed_owner=lambda *_args: canonical_sha256({"retired": True}),
            )

            owner = factory.create_initial_owner(scope=scope, packet=packet)
            self.assertEqual(calls, [])
            prepared = owner.prepare(
                chain_id=scope.identity.chain_id,
                attempt_number=1,
                exact_input=exact_input,
            )
            self.assertTrue(callable(getattr(prepared, "invoke", None)))
            self.assertEqual(calls, ["build"])
            self.assertEqual(ledger_events, [])

            outcome = prepared.invoke()
            self.assertIsInstance(outcome, ProviderStageSuccessfulResultV1)
            self.assertEqual(calls, ["build", "provider"])
            self.assertEqual(ledger_events, ["provider_operation_started"])

    def test_construction_and_unknown_get_are_provider_free_and_exhaustive(self) -> None:
        lifecycle_calls: list[str] = []
        provider_calls: list[str] = []
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            pi_executable = root / "pi.cmd"
            extension = root / "scene.ts"
            pi_executable.write_text("@echo off\n", encoding="utf-8")
            extension.write_text("export {};\n", encoding="utf-8")
            pi_ledger = PiProviderOperationLedger(
                root / "pi.jsonl",
                maximum_operations=40,
                maximum_operations_per_invocation=6,
            )
            pi = PiSceneAdapter(
                pi_executable=pi_executable,
                extension_path=extension,
                pi_version="0.84.1",
                operation_ledger=pi_ledger,
                process_runner=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                    AssertionError("assembly construction started Pi")
                ),
            )
            sol = ContinuousProviderCallLedger(
                root / "sol.jsonl",
                maximum_calls=20,
            )
            assembly = build_provider_stage_retry_production_assembly(
                runtime_root=root,
                scene_store=LeanSceneStore(root / "accepted_world"),
                sol_ledger=sol,
                pi_adapter=pi,
                ordinary_ports=OrdinaryStageProviderPortsV1(
                    resolve_planner=lambda _turn: provider_calls.append("planner_resolve"),
                    planner_active_thread_sha256=lambda _turn: lifecycle_calls.append(
                        "planner_thread_read"
                    ),
                    retire_planner_thread=lambda _turn, _thread: lifecycle_calls.append(
                        "planner_retire"
                    ),
                    planner_provider_result=lambda _turn: (_ for _ in ()).throw(
                        AssertionError("assembly read a Planner provider result")
                    ),
                    semantic_validator=_NoDispatchSemanticValidator(provider_calls),
                    luna_provider_result=lambda: (_ for _ in ()).throw(
                        AssertionError("assembly read a Luna provider result")
                    ),
                    reader_validator=_NoDispatchReaderValidator(provider_calls),
                    reader_provider_result=lambda: (_ for _ in ()).throw(
                        AssertionError("assembly read a Reader provider result")
                    ),
                ),
            )

            self.assertEqual(lifecycle_calls, [])
            self.assertEqual(provider_calls, [])
            self.assertEqual(sol.dispatched_call_count, 0)
            self.assertEqual(pi_ledger.operation_count, 0)
            self.assertEqual(
                {value.stage for value in assembly.registrations},
                set(ProviderStage),
            )
            by_stage = {value.stage: value for value in assembly.registrations}
            self.assertIs(
                by_stage[ProviderStage.PLANNER].lifecycle,
                ProviderStageOwnerLifecycleClass.RETAINED_INITIAL_FRESH_MANUAL_RETRY,
            )
            self.assertIs(
                by_stage[ProviderStage.SEMANTIC_VALIDATOR].lifecycle,
                ProviderStageOwnerLifecycleClass.FRESH_SINGLE_USE,
            )
            self.assertIs(
                by_stage[ProviderStage.READER].lifecycle,
                ProviderStageOwnerLifecycleClass.FRESH_SINGLE_USE,
            )
            for stage in (
                ProviderStage.WRITER,
                ProviderStage.RECORDER,
                ProviderStage.ADULT_SCENE,
                ProviderStage.ADULT_FILTER,
            ):
                self.assertIs(
                    by_stage[stage].lifecycle,
                    ProviderStageOwnerLifecycleClass.ONE_SHOT_PROCESS,
                )
                self.assertEqual(by_stage[stage].maximum_provider_operations, 6)
            self.assertEqual(by_stage[ProviderStage.PLANNER].maximum_provider_operations, 1)
            self.assertEqual(
                by_stage[ProviderStage.SEMANTIC_VALIDATOR].maximum_provider_operations,
                1,
            )
            self.assertEqual(by_stage[ProviderStage.READER].maximum_provider_operations, 1)

            with self.assertRaises(ProviderStageRetryHttpNotFoundError):
                assembly.http.get("stage-retry-" + "0" * 64)
            self.assertEqual(lifecycle_calls, [])
            self.assertEqual(provider_calls, [])
            self.assertEqual(sol.dispatched_call_count, 0)
            self.assertEqual(pi_ledger.operation_count, 0)

    def test_adult_regenerate_retry_replays_exact_decision_after_restart(self) -> None:
        short_root = Path(r"D:\Cera\tmp")
        temporary_parent = short_root if short_root.is_dir() else None
        with tempfile.TemporaryDirectory(dir=temporary_parent) as temporary:
            root = Path(temporary).resolve()
            runtime_root = root / "runtime"
            scene_store = LeanSceneStore(root / "world")
            adult_operations = ProtectedAdultOperationStore(runtime_root / "protected_adult")
            controller, _, _ = FullModelAdultOperationCustodyTests()._controller(
                root=root,
                store=scene_store,
                custody=adult_operations,
                planner=full_model_support._HandoffPlanner(),
            )
            with (
                patch.dict(
                    os.environ,
                    {PROVIDER_DISPATCH_DISABLED_ENV: "1"},
                    clear=False,
                ),
                patch.object(
                    full_model_support,
                    "_FakeStructuredTransport",
                    _RejectingTransport,
                ),
            ):
                rejected = controller.complete(
                    request_id="request:adult-action-retry",
                    turn=_turn(),
                )
            self.assertIsInstance(rejected, RejectedAdultTurnV1)
            assert isinstance(rejected, RejectedAdultTurnV1)

            executable = root / "pi.cmd"
            extension = root / "extension.js"
            executable.write_text("offline scripted Pi", encoding="utf-8")
            extension.write_text("offline scripted Pi", encoding="utf-8")
            runner = _ScriptedPiRunner(
                [
                    _Script("adult_scene", "unavailable"),
                    _Script("adult_scene", "success", _scene_wire()),
                    _Script("adult_filter", "success", _filter_wire()),
                ]
            )
            pi_ledger = PiProviderOperationLedger(
                root / "pi.jsonl",
                maximum_operations=20,
                maximum_operations_per_invocation=6,
            )
            pi = PiSceneAdapter(
                pi_executable=executable,
                extension_path=extension,
                pi_version="0.84.1",
                operation_ledger=pi_ledger,
                process_runner=runner,  # type: ignore[arg-type]
            )
            sol = ContinuousProviderCallLedger(root / "sol.jsonl", maximum_calls=5)

            def ordinary_ports() -> OrdinaryStageProviderPortsV1:
                return OrdinaryStageProviderPortsV1(
                    resolve_planner=lambda _turn: (_ for _ in ()).throw(
                        AssertionError("adult action test invoked Planner")
                    ),
                    planner_active_thread_sha256=lambda _turn: None,
                    retire_planner_thread=lambda _turn, _thread: None,
                    planner_provider_result=lambda _turn: (_ for _ in ()).throw(
                        AssertionError("adult action test read a Planner result")
                    ),
                    semantic_validator=_NoDispatchSemanticValidator([]),
                    luna_provider_result=lambda: (_ for _ in ()).throw(
                        AssertionError("adult action test read a Luna result")
                    ),
                    reader_validator=_NoDispatchReaderValidator([]),
                    reader_provider_result=lambda: (_ for _ in ()).throw(
                        AssertionError("adult action test read a Reader result")
                    ),
                )

            def build_assembly():
                return build_provider_stage_retry_production_assembly(
                    runtime_root=runtime_root,
                    scene_store=scene_store,
                    sol_ledger=sol,
                    pi_adapter=pi,
                    ordinary_ports=ordinary_ports(),
                )

            assembly = build_assembly()
            controller.ordinary.ordinary_stage_retry = assembly.ordinary
            controller.adult_regeneration_executor = lambda prepared, turn: (
                assembly.execute_adult_regeneration(
                    prepared=prepared,
                    frozen_turn=turn,
                )
            )
            assembly.bind_full_model_controller(controller)

            def adapter_for(current) -> PiSceneHttpAdapter:
                adapter = PiSceneHttpAdapter(
                    coordinator=controller.ordinary,
                    session_id="adult-action-retry",
                    context_provider=lambda *_args, **_kwargs: None,
                    logic_route_resolver=lambda _turn: SceneRoute.ADULT,
                    full_model_controller=controller,
                    **current.http_adapter_kwargs(),
                )
                current.bind_http_adapter(adapter)
                return adapter

            adapter = adapter_for(assembly)
            predecessor = controller.get_adult_review(rejected.public_review_id).outcome.prepared
            assembly.http.begin_request(
                request_id=predecessor.request_id,
                world_id=predecessor.route_state.world_id,
                branch_id=predecessor.route_state.branch_id,
            )
            with (
                patch(
                    "cera.pi_scene.adult_stage_retry_integration.assert_provider_dispatch_allowed",
                    autospec=True,
                ),
                patch(
                    "cera.adult_pipeline.pi_roles.assert_provider_dispatch_allowed",
                    autospec=True,
                ),
            ):
                with self.assertRaises(ProviderStageRetryPendingError) as captured:
                    adapter.decide(
                        rejected.public_review_id,
                        {"action": "regenerate"},
                    )
                self.assertEqual(runner.calls, ["adult_scene"])
                action = backend_action_from_envelope(captured.exception.envelope)
                response = assembly.http.post(
                    chain_id=captured.exception.chain_id,
                    action_id=action["action_id"],
                    body=action,
                )

            self.assertEqual(
                runner.calls,
                ["adult_scene", "adult_scene", "adult_filter"],
            )
            self.assertEqual(
                response["schema_version"],
                "cera.pi_scene.review_decision.v1",
            )
            self.assertEqual(response["creator_action"], "regenerate")
            self.assertIsNotNone(response["successor"])
            scene_chain_id = captured.exception.chain_id
            filter_chain_id = assembly.adult.latest_chain_for_chain(scene_chain_id)
            self.assertNotEqual(scene_chain_id, filter_chain_id)
            scene_epoch = assembly.adult_stage_retry_actions.review_action_identity_for_chain(
                scene_chain_id
            )
            filter_epoch = assembly.adult_stage_retry_actions.review_action_identity_for_chain(
                filter_chain_id
            )
            self.assertIsNotNone(scene_epoch)
            self.assertEqual(scene_epoch, filter_epoch)
            self.assertEqual(assembly.http.get(scene_chain_id), response)
            self.assertEqual(
                runner.calls,
                ["adult_scene", "adult_scene", "adult_filter"],
            )

            restarted = build_assembly()
            controller.ordinary.ordinary_stage_retry = restarted.ordinary
            controller.adult_regeneration_executor = lambda prepared, turn: (
                restarted.execute_adult_regeneration(
                    prepared=prepared,
                    frozen_turn=turn,
                )
            )
            restarted.bind_full_model_controller(controller)
            adapter_for(restarted)
            self.assertEqual(restarted.http.get(scene_chain_id), response)
            self.assertEqual(
                runner.calls,
                ["adult_scene", "adult_scene", "adult_filter"],
            )

            action_response, response_receipt = (
                restarted.adult_stage_retry_actions.load_review_action_response_for_chain(
                    scene_chain_id
                )
            )
            self.assertEqual(action_response, response)
            exact_source = predecessor.scene_request.exact_current_source.encode("utf-8")
            protected_prose = PROTECTED_PROSE.encode("utf-8")
            normalized_action = canonical_bytes(
                {
                    "action": "regenerate",
                    "feedback": None,
                    "force_rehydrate": False,
                }
            )
            authority_path = runtime_root / "provider_stage_retry" / "authority.sqlite3"
            authority_bytes = authority_path.read_bytes()
            wal_path = Path(str(authority_path) + "-wal")
            if wal_path.is_file():
                authority_bytes += wal_path.read_bytes()
            for forbidden in (exact_source, protected_prose, normalized_action):
                self.assertNotIn(forbidden, authority_bytes)

            cursor_bytes = b"".join(
                path.read_bytes()
                for path in (runtime_root / "provider_stage_retry" / "protected" / "http").rglob(
                    "*.json"
                )
            )
            epoch_bytes = canonical_bytes(to_primitive(scene_epoch))
            status_bytes = canonical_bytes(captured.exception.envelope)
            receipt_bytes = canonical_bytes(to_primitive(response_receipt))
            for safe_bytes in (cursor_bytes, epoch_bytes, status_bytes, receipt_bytes):
                self.assertNotIn(exact_source, safe_bytes)
                self.assertNotIn(protected_prose, safe_bytes)
                self.assertNotIn(normalized_action, safe_bytes)

            action_files = tuple(
                (
                    runtime_root
                    / "provider_stage_retry"
                    / "protected"
                    / "adult_review_actions"
                    / "actions"
                ).glob("*.json")
            )
            self.assertEqual(len(action_files), 1)
            tombstone = action_files[0].read_bytes()
            self.assertNotIn(b'"normalized_action":', tombstone)
            self.assertNotIn(exact_source, tombstone)
            self.assertNotIn(protected_prose, tombstone)

    def test_real_registry_fences_failed_planner_before_manual_retry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            pi_executable = root / "pi.cmd"
            extension = root / "scene.ts"
            pi_executable.write_text("@echo off\n", encoding="utf-8")
            extension.write_text("export {};\n", encoding="utf-8")
            pi = PiSceneAdapter(
                pi_executable=pi_executable,
                extension_path=extension,
                pi_version="0.84.1",
                operation_ledger=PiProviderOperationLedger(
                    root / "pi.jsonl",
                    maximum_operations=20,
                    maximum_operations_per_invocation=5,
                ),
                process_runner=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                    AssertionError("Planner Retry test started Pi")
                ),
            )
            sol = ContinuousProviderCallLedger(root / "sol.jsonl", maximum_calls=4)
            planner = _RetainedPlannerFailureThenSuccess(sol)
            registry_builds: list[tuple[str, str]] = []

            def build_planner(
                session_id: str,
                effort: str,
                _turn: LeanSceneTurnInputV1,
            ) -> _RetainedPlannerFailureThenSuccess:
                registry_builds.append((session_id, effort))
                return planner

            registry = _PiScenePlannerRegistry(build_planner)
            assembly = build_provider_stage_retry_production_assembly(
                runtime_root=root,
                scene_store=LeanSceneStore(root / "accepted_world"),
                sol_ledger=sol,
                pi_adapter=pi,
                ordinary_ports=OrdinaryStageProviderPortsV1(
                    resolve_planner=registry.resolve,
                    planner_active_thread_sha256=registry.active_thread_sha256,
                    retire_planner_thread=registry.reset_after_transport_failure,
                    planner_provider_result=lambda _turn: (
                        planner.last_provider_result
                        if planner.last_provider_result is not None
                        else (_ for _ in ()).throw(
                            AssertionError("Planner success receipt is unavailable")
                        )
                    ),
                    semantic_validator=_NoDispatchSemanticValidator([]),
                    luna_provider_result=lambda: (_ for _ in ()).throw(
                        AssertionError("Planner test read a Luna receipt")
                    ),
                    reader_validator=_NoDispatchReaderValidator([]),
                    reader_provider_result=lambda: (_ for _ in ()).throw(
                        AssertionError("Planner test read a Reader receipt")
                    ),
                ),
            )
            controls = LeanSceneRequestControlsV2(
                schema_version=LeanSceneRequestControlsV2.SCHEMA_VERSION,
                session_id="assembly-planner-retry",
                reasoning_effort="medium",
            )
            payload = {
                "model": "cera-alpha",
                "messages": [{"role": "user", "content": "Continue the ordinary scene."}],
                "cera_profile_id": "cera.pi_scene.lean.v1",
                "cera_session_id": controls.session_id,
                "stream": False,
            }
            turn = LeanSceneTurnInputV1(
                world_id="world-assembly",
                branch_id="branch-main",
                scene_id="scene-assembly",
                exact_user_source="Continue the ordinary scene.",
                current_state={"public_scene_state": "The conversation remains open."},
                characters={},
                relationships={},
                recent_prose=(),
                relevant_memories={},
                voice_examples={},
                craft_index={},
                request_controls=controls,
            )
            binding = build_request_binding(
                payload=payload,
                session_id=controls.session_id,
                world_id=turn.world_id,
                branch_id=turn.branch_id,
                route=SceneRoute.ORDINARY,
                controls=controls,
            )
            branch_root = lean_scene_branch_root(
                root,
                turn.world_id,
                turn.branch_id,
            )
            (branch_root / "ACTIVE").mkdir(parents=True)
            (branch_root / "DERIVED").mkdir()
            context = OrdinaryStageRetryRequestContextV1(
                binding=binding,
                generation=1,
                turn_input=turn,
                planner_retrieval=assembly.retrieval.capture(binding),
            )
            request = PlannerTurnInputV1(
                world_id=turn.world_id,
                branch_id=turn.branch_id,
                scene_id=turn.scene_id,
                exact_user_source=turn.exact_user_source,
                current_state=turn.current_state,
                characters=turn.characters,
                relationships=turn.relationships,
                relevant_memories=turn.relevant_memories,
                accepted_records=(),
                request_controls=controls,
            )
            accepted_state_sha256 = canonical_sha256(
                {"schema_version": "assembly-accepted-state.v1", "head": None}
            )
            assembly.ordinary.freeze_pending_request(
                normalized_request=payload,
                context=context,
            )
            assembly.http.begin_request(
                request_id=binding.request_id,
                world_id=binding.world_id,
                branch_id=binding.branch_id,
            )

            with assembly.ordinary.bind_request(context):
                with self.assertRaises(ProviderStageRetryPendingError) as captured:
                    assembly.ordinary.run_planner(
                        request,
                        accepted_state_sha256=accepted_state_sha256,
                        authority_binding={"accepted_head_sha256": None},
                    )
            first_thread = text_sha256("assembly-planner-thread-a")
            second_thread = text_sha256("assembly-planner-thread-b")
            self.assertEqual(registry_builds, [(controls.session_id, "medium")])
            self.assertEqual(planner.physical_threads, [first_thread])
            self.assertEqual(planner.retired_threads, [first_thread])
            self.assertEqual(planner.fresh_thread_starts, [])
            self.assertIsNone(planner.thread_sha256)
            self.assertEqual(sol.dispatched_call_count, 1)
            assembly.http.capture_pending(captured.exception.envelope)
            read_only = assembly.http.get(captured.exception.chain_id)
            self.assertEqual(
                read_only["status"]["chain_id"],
                captured.exception.chain_id,
            )
            self.assertEqual(planner.physical_threads, [first_thread])
            self.assertEqual(planner.retired_threads, [first_thread])
            self.assertEqual(sol.dispatched_call_count, 1)

            action = backend_action_from_envelope(captured.exception.envelope)
            projection = assembly.ordinary.execute_action(action)
            self.assertTrue(projection.result_ready)
            self.assertEqual(planner.physical_threads, [first_thread, second_thread])
            self.assertEqual(planner.retired_threads, [first_thread])
            self.assertEqual(planner.fresh_thread_starts, [second_thread])
            self.assertEqual(registry_builds, [(controls.session_id, "medium")])
            self.assertEqual(sol.dispatched_call_count, 2)

            chain = assembly.runtime.read_chain(captured.exception.chain_id)
            self.assertEqual(len(chain.attempts), 2)
            first, second = chain.attempts
            self.assertIs(first.phase, ProviderStageAttemptPhase.OWNER_RETIRED)
            self.assertIs(second.phase, ProviderStageAttemptPhase.RESULT_FROZEN)
            self.assertNotEqual(first.session_scope_sha256, second.session_scope_sha256)
            self.assertEqual(
                first.ledger_prefix_after_sha256,
                second.ledger_prefix_before_sha256,
            )
            self.assertEqual(first.input_tokens, planner.receipts[0].input_tokens)
            self.assertEqual(second.input_tokens, planner.receipts[1].input_tokens)
            self.assertIsNotNone(first.owner_retirement_evidence_sha256)

            events = tuple(sol.events)
            call_ids = tuple(dict.fromkeys(event["call_id"] for event in events))
            self.assertEqual(len(call_ids), 2)
            first_events = tuple(event for event in events if event["call_id"] == call_ids[0])
            second_events = tuple(event for event in events if event["call_id"] == call_ids[1])
            self.assertEqual(
                sum(event["state"] == "transport_invoked" for event in first_events),
                1,
            )
            self.assertEqual(
                sum(event["state"] == "transport_invoked" for event in second_events),
                1,
            )
            self.assertEqual(
                {event["stored_thread_sha256"] for event in first_events},
                {first_thread},
            )
            self.assertEqual(
                {event["stored_thread_sha256"] for event in second_events},
                {second_thread},
            )
            self.assertEqual(
                first.ledger_prefix_after_sha256,
                canonical_sha256(first_events),
            )
            self.assertEqual(
                second.ledger_prefix_after_sha256,
                canonical_sha256(events),
            )

    def test_launcher_attaches_generic_runtime_without_touching_legacy_budget(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            pi_executable = root / "pi.cmd"
            extension = root / "scene.ts"
            pi_executable.write_text("@echo off\n", encoding="utf-8")
            extension.write_text("export {};\n", encoding="utf-8")
            ledger = PiProviderOperationLedger(
                root / "pi.jsonl",
                maximum_operations=40,
                maximum_operations_per_invocation=5,
            )
            pi = PiSceneAdapter(
                pi_executable=pi_executable,
                extension_path=extension,
                pi_version="0.84.1",
                operation_ledger=ledger,
                process_runner=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                    AssertionError("launcher construction started Pi")
                ),
            )
            pi.external_provider_boundary = False
            with patch.dict("os.environ", {"CERA_PROVIDER_DISPATCH_DISABLED": "1"}):
                runtime = build_live_runtime(
                    root / "runtime",
                    sol_ceiling=20,
                    deepseek_ceiling=40,
                    deepseek_per_invocation_ceiling=5,
                    provider_components=FullModelProviderComponents(
                        planner_lifecycle=_NoLifecycle(),
                        luna_backend=_NoLunaBackend(),
                        reader_backend=_NoReaderBackend(),
                        pi=pi,
                        external_provider_boundary=False,
                    ),
                )
            try:
                assembly = runtime.provider_stage_retry
                self.assertIsNotNone(assembly)
                assert assembly is not None
                self.assertIs(runtime.coordinator.ordinary_stage_retry, assembly.ordinary)
                self.assertEqual(runtime.sol_ledger.dispatched_call_count, 0)
                self.assertEqual(runtime.deepseek_ledger.operation_count, 0)
                adapter = PiSceneHttpAdapter(
                    coordinator=runtime.coordinator,
                    session_id="provider-stage-preflight",
                    context_provider=lambda *_args, **_kwargs: None,
                    logic_route_resolver=lambda _turn: SceneRoute.ORDINARY,
                    full_model_controller=runtime.full_model_controller,
                    transport_retry_reinitializer=runtime.transport_retry_reinitializer,
                    transport_provider_ledger_snapshot=(runtime.transport_provider_ledger_snapshot),
                    transport_retry_active_thread_snapshot=(
                        runtime.transport_retry_active_thread_snapshot
                    ),
                    transport_retry_fresh_thread_initializer=(
                        runtime.transport_retry_fresh_thread_initializer
                    ),
                    transport_completed_planner_abandoner=(
                        runtime.transport_completed_planner_abandoner
                    ),
                    **assembly.http_adapter_kwargs(),
                )
                assembly.bind_http_adapter(adapter)
                status = adapter.status
                self.assertTrue(status["active"])
                profile_path = (
                    Path(__file__).resolve().parents[1]
                    / "integrations"
                    / "sillytavern"
                    / "pi_scene_lean_v1_profile.json"
                )
                profile = json.loads(profile_path.read_text(encoding="utf-8"))
                self.assertTrue(status["reader_required"])
                self.assertFalse(status["adult_reader_required"])
                self.assertTrue(status["python_gate_required"])
                self.assertFalse(status["automatic_accept_after_semantic_pass"])
                self.assertTrue(status["automatic_accept_after_all_checks_pass"])
                self.assertEqual(status["review_mode_default"], "automatic")
                self.assertEqual(
                    status["ordinary_acceptance_checks"],
                    ["luna", "reader", "python"],
                )
                self.assertEqual(
                    status["review_mode_default"],
                    profile["ordinary_review_mode_default"],
                )
                self.assertEqual(
                    status["reader_required"],
                    profile["reader_required"],
                )
                self.assertEqual(
                    status["adult_reader_required"],
                    profile["adult_reader_required"],
                )
                self.assertEqual(
                    status["python_gate_required"],
                    profile["python_gate_required"],
                )
                self.assertEqual(
                    profile["automatic_accept"],
                    "all_route_required_checks_pass_only",
                )
                self.assertIs(adapter.provider_stage_retry_http, assembly.http)
                self.assertIs(adapter.ordinary_stage_retry_runtime, assembly.ordinary)
                self.assertEqual(runtime.sol_ledger.dispatched_call_count, 0)
                self.assertEqual(runtime.deepseek_ledger.operation_count, 0)
                # Legacy callbacks remain available only as compatibility
                # seams; HTTP bypass is selected by the generic coordinator.
                self.assertIsNotNone(runtime.transport_retry_reinitializer)
            finally:
                runtime.close()


if __name__ == "__main__":
    unittest.main()
