from __future__ import annotations

import json
import unittest
from dataclasses import dataclass, field, replace
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from cera.cognition import (
    CharacterAutonomyMode,
    CognitionCitationClass,
    CognitionDynamicEvidenceV1,
    CognitionPlanV1,
    CognitionProviderCompletedTurnV1,
    CognitionTurnContextV1,
    LogicRoute,
    PersistentCognitionPlannerSession,
    cognition_provider_turn_handoff,
    cognition_static_citation_scope,
)
from cera.cognition.prompting import (
    COGNITION_PLANNER_BASE_INSTRUCTIONS,
    COGNITION_PLANNER_PROFILE,
    cognition_turn_prompt,
)
from cera.cognition.provider import (
    COGNITION_PLANNER_ADAPTER,
    COGNITION_PLANNER_PROMPT,
    CodexCognitionPlannerBackend,
    _take_cognition_dynamic_evidence,
    cognition_planner_route,
)
from cera.cognition.provider_schema import cognition_plan_json_schema
from cera.continuous.call_ledger import (
    ContinuousProviderCallLedger,
    ProviderCallState,
)
from cera.errors import ContractValidationError, ErrorCode, StateConflictError
from cera.pi_scene.cognition_planner import RetainedCognitionPlannerAdapter
from cera.pi_scene.http_contracts import LeanSceneRequestControlsV1
from cera.pi_scene.runtime import PlannerTurnInputV1
from cera.providers import ProviderSchemaDialect, project_provider_output_schema
from cera.providers.models import (
    ProviderRetryableFailureCategory,
    ProviderTransportError,
)
from cera.sequence_first.contracts import (
    EvidenceRecordV1,
    ProviderReferenceScopeV1,
    Visibility,
)
from cera.serialization import canonical_json, text_sha256, to_primitive

from .test_cognition_citation_handoff import _dynamic, _plan_citing, _plan_item_citing
from .test_cognition_contracts import _plan, _turn


def _context() -> CognitionTurnContextV1:
    return CognitionTurnContextV1(
        turn=_turn(),
        autonomy_mode=CharacterAutonomyMode.BOTH,
        logic_route=LogicRoute.ORDINARY,
        available_provisional_record_ids=("provisional:door_claim",),
    )


@dataclass
class _FakeBackend:
    external_provider_boundary = False

    result: CognitionPlanV1
    thread_id: str = "thread:cognition"
    resumable: bool = True
    dynamic_evidence: tuple[CognitionDynamicEvidenceV1, ...] = ()
    failure_on_call: int | None = None
    last_available_evidence_refs: tuple[str, ...] = ()
    last_provider_turn_handoff: object | None = None
    last_provider_result: object | None = None
    starts: list[tuple[str, str]] = field(default_factory=list)
    calls: list[tuple[str, str, CognitionTurnContextV1]] = field(default_factory=list)
    clear_calls: int = 0

    def start_stored_thread(self, *, base_instructions: str, profile: str) -> str:
        self.starts.append((base_instructions, profile))
        return self.thread_id

    def run_cognition_turn(
        self,
        *,
        thread_id: str,
        prompt: str,
        context: CognitionTurnContextV1,
        reference_scope: ProviderReferenceScopeV1,
    ) -> CognitionProviderCompletedTurnV1:
        del reference_scope
        self.calls.append((thread_id, prompt, context))
        if self.failure_on_call == len(self.calls):
            raise StateConflictError("injected cognition backend failure")
        return CognitionProviderCompletedTurnV1(
            plan=self.result,
            handoff=cognition_provider_turn_handoff(
                plan=self.result,
                turn=context.turn,
                provider_thread_sha256=text_sha256(thread_id),
                dynamic_evidence=self.dynamic_evidence,
            ),
        )

    def is_resumable(self, thread_id: str) -> bool:
        return self.resumable and thread_id == self.thread_id

    def clear_cognition_transient_state(self) -> None:
        self.clear_calls += 1
        self.last_available_evidence_refs = ()
        self.last_provider_turn_handoff = None
        self.last_provider_result = None


class _InvalidCompletedCognitionTransport:
    def __init__(
        self,
        _route,
        *,
        workspace: Path,
        runner: object,
    ) -> None:
        del workspace, runner

    def invoke(
        self,
        _prompt: str,
        *,
        output_schema: dict[str, object],
        mcp_binding: object,
        on_worker_started,
        on_worker_preflight,
        on_transport_invoke,
    ) -> object:
        del output_schema, mcp_binding
        on_worker_started()
        on_worker_preflight()
        on_transport_invoke()
        payload = to_primitive(_plan())
        perceived = payload["decision_records"][0]["observer_frame"]["directly_perceived"]
        perceived.append(dict(perceived[0]))
        return SimpleNamespace(
            output_text=json.dumps(payload, sort_keys=True),
            parsed_json=payload,
            receipt={"provider": "offline-planner"},
            operation_telemetry=None,
            tool_call_count=0,
            failed_tool_call_count=0,
            tool_names=(),
            tool_server_names=(),
        )


class _ScriptedCompletedCognitionTransport:
    payload: dict[str, object] = {}

    def __init__(
        self,
        _route,
        *,
        workspace: Path,
        runner: object,
    ) -> None:
        del workspace, runner

    def invoke(
        self,
        _prompt: str,
        *,
        output_schema: dict[str, object],
        mcp_binding: object,
        on_worker_started,
        on_worker_preflight,
        on_transport_invoke,
    ) -> object:
        del output_schema, mcp_binding
        on_worker_started()
        on_worker_preflight()
        on_transport_invoke()
        return SimpleNamespace(
            output_text=json.dumps(self.payload, sort_keys=True),
            parsed_json=self.payload,
            receipt={"provider": "offline-planner"},
            operation_telemetry=None,
            tool_call_count=1,
            failed_tool_call_count=0,
            tool_names=("get_exact_record",),
            tool_server_names=("cera_continuous_world",),
        )


class _TypedWorldBridge:
    def __init__(self, additional: object) -> None:
        self.runtime_binding = object()
        self.additional = additional
        self.finalized = False

    def finalize(self, _result: object) -> dict[str, object]:
        self.finalized = True
        return {
            "binding_sha256": "a" * 64,
            "tool_call_count": 1,
            "evidence_bindings": [],
        }

    def take_additional_finalization(self) -> object:
        if not self.finalized:
            raise AssertionError("bridge result was taken before finalization")
        value = self.additional
        self.additional = None
        return value


class CognitionProviderContractTests(unittest.TestCase):
    def test_cognition_finalizer_requires_typed_additional_result(self) -> None:
        for value in (None, ("not-typed",)):
            with self.subTest(value=value):
                bridge = SimpleNamespace(take_additional_finalization=lambda value=value: value)
                with self.assertRaisesRegex(
                    ContractValidationError,
                    "dynamic evidence changed shape",
                ):
                    _take_cognition_dynamic_evidence(bridge)

    def test_live_route_uses_bounded_hard_timeout_without_retry(self) -> None:
        route = cognition_planner_route()
        self.assertEqual(route.timeout_seconds, 180)
        self.assertEqual(route.automatic_retry_count, 0)
        self.assertFalse(route.fallback_enabled)
        self.assertEqual(COGNITION_PLANNER_PROFILE, "cera_full_model_cognition_planner_v10")
        self.assertEqual(
            COGNITION_PLANNER_ADAPTER,
            "cera.cognition.codex_planner_adapter.v8",
        )
        self.assertEqual(
            COGNITION_PLANNER_PROMPT,
            "cera.cognition.codex_planner_prompt.v9",
        )
        self.assertEqual(route.route_id, "cera_cognition_planner_sol_medium_v10")
        self.assertIn(
            "Call get_turn_context first with character_ids omitted",
            COGNITION_PLANNER_BASE_INSTRUCTIONS,
        )
        self.assertIn(
            "one explicit selection may name only one distinct authorized character",
            COGNITION_PLANNER_BASE_INSTRUCTIONS,
        )
        self.assertIn(
            "Every dossier returned by get_turn_context or get_character_context is complete",
            COGNITION_PLANNER_BASE_INSTRUCTIONS,
        )
        self.assertIn(
            "Never call get_character_context, get_relationship_context, or get_memory_context",
            COGNITION_PLANNER_BASE_INSTRUCTIONS,
        )
        self.assertIn(
            "only with an exact record_id returned by a prior successful search_evidence",
            COGNITION_PLANNER_BASE_INSTRUCTIONS,
        )
        self.assertIn(
            "same source_ref may support multiple rows",
            COGNITION_PLANNER_BASE_INSTRUCTIONS,
        )
        self.assertIn("every context_ref", COGNITION_PLANNER_BASE_INSTRUCTIONS)
        self.assertIn("eligible_after_exact_fetch", COGNITION_PLANNER_BASE_INSTRUCTIONS)
        self.assertIn("citable_static_evidence_refs", COGNITION_PLANNER_BASE_INSTRUCTIONS)
        self.assertIn("never count, truncate, or summarize", COGNITION_PLANNER_BASE_INSTRUCTIONS)
        self.assertIn(
            "include at least one NPC-owned action",
            COGNITION_PLANNER_BASE_INSTRUCTIONS,
        )
        self.assertIn(
            "Every action, dialogue_intent, private_state, perception, and remote_communication "
            "item must set owner_id",
            COGNITION_PLANNER_BASE_INSTRUCTIONS,
        )
        self.assertIn(
            "source-supplied, environmental, mechanical, anonymous, or off-cast occurrence",
            COGNITION_PLANNER_BASE_INSTRUCTIONS,
        )
        self.assertIn(
            "material_continuity or scene_transition instead",
            COGNITION_PLANNER_BASE_INSTRUCTIONS,
        )
        self.assertIn(
            "never use an owner-required kind with a null owner_id or invent an owner",
            COGNITION_PLANNER_BASE_INSTRUCTIONS,
        )
        self.assertIn(
            "Do not create a Ted-owned sequence item for compatible visible behavior",
            COGNITION_PLANNER_BASE_INSTRUCTIONS,
        )
        self.assertIn(
            "only when it copies an exact supplied-source contribution",
            COGNITION_PLANNER_BASE_INSTRUCTIONS,
        )
        self.assertIn(
            "instead of canceling the new scene solely because the prior accepted location differs",
            COGNITION_PLANNER_BASE_INSTRUCTIONS,
        )

    def test_completed_invalid_plan_is_typed_retryable_provider_output(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            ledger = ContinuousProviderCallLedger(root / "ledger.jsonl")
            backend = CodexCognitionPlannerBackend(
                lifecycle=SimpleNamespace(external_provider_boundary=False),
                workspace=root / "workspace",
                call_ledger=ledger,
            )
            context = _context()
            with (
                patch(
                    "cera.cognition.provider.CodexSDKTransport",
                    _InvalidCompletedCognitionTransport,
                ),
                self.assertRaises(ProviderTransportError) as caught,
            ):
                backend.run_cognition_turn(
                    thread_id="thread:cognition-invalid",
                    prompt="Return one cognition plan.",
                    context=context,
                    reference_scope=ProviderReferenceScopeV1.from_turn(context.turn),
                )

            failure = caught.exception
            self.assertEqual(failure.code, ErrorCode.REASONER_CONTRACT_INVALID)
            self.assertEqual(
                failure.retryable_failure_category,
                ProviderRetryableFailureCategory.PROVIDER_OUTPUT_INVALID,
            )
            self.assertEqual(failure.external_provider_calls_observed, 1)
            self.assertEqual(failure.provider_call_receipt, {"provider": "offline-planner"})
            self.assertEqual(
                failure.safe_diagnostics,
                ("provider_output:cognition_plan_contract_invalid",),
            )
            self.assertIsNone(failure.__cause__)
            self.assertNotIn("duplicates", str(failure))
            self.assertEqual(ledger.dispatched_call_count, 1)
            self.assertEqual(
                tuple(value["state"] for value in ledger.events[-2:]),
                (
                    ProviderCallState.PROVIDER_COMPLETED.value,
                    ProviderCallState.POST_VALIDATION_FAILED.value,
                ),
            )
            self.assertFalse(
                any(value["state"] == ProviderCallState.ACCEPTED.value for value in ledger.events)
            )
            self.assertIsNone(backend.last_provider_result)
            self.assertFalse(hasattr(backend, "last_available_evidence_refs"))
            self.assertFalse(hasattr(backend, "last_provider_turn_handoff"))

    def test_context_only_citation_fails_before_accepted_with_one_charge(self) -> None:
        dynamic = _dynamic(
            CognitionCitationClass.DYNAMIC_CONTEXT_ONLY_BINDING,
            fact=None,
            tool_name="search_evidence",
            call_index=1,
        )
        plan = _plan_citing(dynamic.evidence_ref)
        _ScriptedCompletedCognitionTransport.payload = to_primitive(plan)
        with TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            ledger = ContinuousProviderCallLedger(root / "ledger.jsonl")
            backend = CodexCognitionPlannerBackend(
                lifecycle=SimpleNamespace(external_provider_boundary=False),
                workspace=root / "workspace",
                call_ledger=ledger,
                world_bridge=_TypedWorldBridge((dynamic,)),
            )
            with (
                patch(
                    "cera.cognition.provider.CodexSDKTransport",
                    _ScriptedCompletedCognitionTransport,
                ),
                self.assertRaises(ProviderTransportError) as caught,
            ):
                backend.run_cognition_turn(
                    thread_id="thread:cognition-context-only",
                    prompt="Return one cognition plan.",
                    context=_context(),
                    reference_scope=ProviderReferenceScopeV1.from_turn(_context().turn),
                )
            dispatched_call_count = ledger.dispatched_call_count
            events = ledger.events

        self.assertEqual(
            caught.exception.retryable_failure_category,
            ProviderRetryableFailureCategory.PROVIDER_OUTPUT_INVALID,
        )
        self.assertEqual(dispatched_call_count, 1)
        self.assertEqual(
            tuple(value["state"] for value in events[-2:]),
            (
                ProviderCallState.PROVIDER_COMPLETED.value,
                ProviderCallState.POST_VALIDATION_FAILED.value,
            ),
        )
        self.assertFalse(hasattr(backend, "last_provider_turn_handoff"))

    def test_expired_restart_ref_fails_preaccepted_without_redispatch(self) -> None:
        current = _dynamic(
            CognitionCitationClass.DYNAMIC_EXACT_RECORD_VALIDATION_EVIDENCE,
            fact='{"record_id":"record:current"}',
        )
        expired_ref = "binding_record_expired00000000001"
        _ScriptedCompletedCognitionTransport.payload = to_primitive(_plan_citing(expired_ref))
        with TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            ledger = ContinuousProviderCallLedger(root / "ledger.jsonl")
            backend = CodexCognitionPlannerBackend(
                lifecycle=SimpleNamespace(external_provider_boundary=False),
                workspace=root / "workspace",
                call_ledger=ledger,
                world_bridge=_TypedWorldBridge((current,)),
            )
            with (
                patch(
                    "cera.cognition.provider.CodexSDKTransport",
                    _ScriptedCompletedCognitionTransport,
                ),
                self.assertRaises(ProviderTransportError) as caught,
            ):
                backend.run_cognition_turn(
                    thread_id="thread:cognition-restarted",
                    prompt="Return one cognition plan.",
                    context=_context(),
                    reference_scope=ProviderReferenceScopeV1.from_turn(_context().turn),
                )
            dispatched_call_count = ledger.dispatched_call_count
            events = ledger.events

        self.assertEqual(
            caught.exception.retryable_failure_category,
            ProviderRetryableFailureCategory.PROVIDER_OUTPUT_INVALID,
        )
        self.assertEqual(dispatched_call_count, 1)
        self.assertEqual(
            tuple(value["state"] for value in events[-2:]),
            (
                ProviderCallState.PROVIDER_COMPLETED.value,
                ProviderCallState.POST_VALIDATION_FAILED.value,
            ),
        )
        self.assertIsNone(backend.last_provider_result)

    def test_static_4001_citation_fails_before_accepted_with_one_charge(self) -> None:
        record = EvidenceRecordV1(
            evidence_key="evidence:static-unicode-4001",
            subject_id="character:sakura_hanezawa",
            visibility=Visibility.PUBLIC,
            exact_content="é" * 4_001,
        )
        turn = replace(_turn(), evidence_records=(record,))
        context = replace(_context(), turn=turn)
        _ScriptedCompletedCognitionTransport.payload = to_primitive(
            _plan_citing(record.evidence_key)
        )
        with TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            ledger = ContinuousProviderCallLedger(root / "ledger.jsonl")
            backend = CodexCognitionPlannerBackend(
                lifecycle=SimpleNamespace(external_provider_boundary=False),
                workspace=root / "workspace",
                call_ledger=ledger,
            )
            with (
                patch(
                    "cera.cognition.provider.CodexSDKTransport",
                    _ScriptedCompletedCognitionTransport,
                ),
                self.assertRaises(ProviderTransportError) as caught,
            ):
                backend.run_cognition_turn(
                    thread_id="thread:cognition-static-oversize",
                    prompt="Return one cognition plan.",
                    context=context,
                    reference_scope=ProviderReferenceScopeV1.from_turn(turn),
                )
            dispatched_call_count = ledger.dispatched_call_count
            events = ledger.events
        self.assertEqual(
            caught.exception.retryable_failure_category,
            ProviderRetryableFailureCategory.PROVIDER_OUTPUT_INVALID,
        )
        self.assertEqual(dispatched_call_count, 1)
        self.assertEqual(
            tuple(value["state"] for value in events[-2:]),
            (
                ProviderCallState.PROVIDER_COMPLETED.value,
                ProviderCallState.POST_VALIDATION_FAILED.value,
            ),
        )
        self.assertIsNone(backend.last_provider_result)

    def test_static_4001_item_evidence_fails_preaccepted_with_one_charge(self) -> None:
        record = EvidenceRecordV1(
            evidence_key="evidence:item-unicode-4001",
            subject_id="character:sakura_hanezawa",
            visibility=Visibility.PUBLIC,
            exact_content=chr(0xE9) * 4_001,
        )
        turn = replace(_turn(), evidence_records=(record,))
        context = replace(_context(), turn=turn)
        _ScriptedCompletedCognitionTransport.payload = to_primitive(
            _plan_item_citing(record.evidence_key)
        )
        with TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            ledger = ContinuousProviderCallLedger(root / "ledger.jsonl")
            backend = CodexCognitionPlannerBackend(
                lifecycle=SimpleNamespace(external_provider_boundary=False),
                workspace=root / "workspace",
                call_ledger=ledger,
            )
            with (
                patch(
                    "cera.cognition.provider.CodexSDKTransport",
                    _ScriptedCompletedCognitionTransport,
                ),
                self.assertRaises(ProviderTransportError) as caught,
            ):
                backend.run_cognition_turn(
                    thread_id="thread:cognition-item-oversize",
                    prompt="Return one cognition plan.",
                    context=context,
                    reference_scope=ProviderReferenceScopeV1.from_turn(turn),
                )
            events = ledger.events
            dispatched_call_count = ledger.dispatched_call_count

        self.assertEqual(
            caught.exception.retryable_failure_category,
            ProviderRetryableFailureCategory.PROVIDER_OUTPUT_INVALID,
        )
        self.assertEqual(dispatched_call_count, 1)
        self.assertEqual(
            tuple(value["state"] for value in events[-2:]),
            (
                ProviderCallState.PROVIDER_COMPLETED.value,
                ProviderCallState.POST_VALIDATION_FAILED.value,
            ),
        )
        self.assertFalse(
            any(value["state"] == ProviderCallState.ACCEPTED.value for value in events)
        )
        self.assertIsNone(backend.last_provider_result)

    def test_unselected_static_4001_is_accepted_without_fact_retention(self) -> None:
        oversized = "é" * 4_001
        record = EvidenceRecordV1(
            evidence_key="evidence:static-unicode-4001",
            subject_id="character:sakura_hanezawa",
            visibility=Visibility.PUBLIC,
            exact_content=oversized,
        )
        turn = replace(_turn(), evidence_records=(record,))
        context = replace(_context(), turn=turn)
        _ScriptedCompletedCognitionTransport.payload = to_primitive(_plan())
        with TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            ledger = ContinuousProviderCallLedger(root / "ledger.jsonl")
            backend = CodexCognitionPlannerBackend(
                lifecycle=SimpleNamespace(external_provider_boundary=False),
                workspace=root / "workspace",
                call_ledger=ledger,
            )
            with patch(
                "cera.cognition.provider.CodexSDKTransport",
                _ScriptedCompletedCognitionTransport,
            ):
                completed = backend.run_cognition_turn(
                    thread_id="thread:cognition-static-unselected-oversize",
                    prompt="Return one cognition plan.",
                    context=context,
                    reference_scope=ProviderReferenceScopeV1.from_turn(turn),
                )
            events = ledger.events
            dispatched_call_count = ledger.dispatched_call_count

        self.assertEqual(completed.handoff.selected_evidence, ())
        self.assertEqual(events[-1]["state"], ProviderCallState.ACCEPTED.value)
        self.assertEqual(dispatched_call_count, 1)
        self.assertIsNotNone(backend.last_provider_result)
        self.assertNotIn(oversized, repr(backend.last_provider_result))

    def test_valid_exact_citation_materializes_handoff_before_accepted(self) -> None:
        fact = '{"record_id":"record:test","value":"raw_fact_not_in_debug"}'
        dynamic = _dynamic(
            CognitionCitationClass.DYNAMIC_EXACT_RECORD_VALIDATION_EVIDENCE,
            owner_id="character:sakura_hanezawa",
            fact=fact,
        )
        plan = _plan_citing(dynamic.evidence_ref)
        _ScriptedCompletedCognitionTransport.payload = to_primitive(plan)
        with TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            ledger = ContinuousProviderCallLedger(root / "ledger.jsonl")
            backend = CodexCognitionPlannerBackend(
                lifecycle=SimpleNamespace(external_provider_boundary=False),
                workspace=root / "workspace",
                call_ledger=ledger,
                world_bridge=_TypedWorldBridge((dynamic,)),
            )
            with patch(
                "cera.cognition.provider.CodexSDKTransport",
                _ScriptedCompletedCognitionTransport,
            ):
                completed = backend.run_cognition_turn(
                    thread_id="thread:cognition-exact",
                    prompt="Return one cognition plan.",
                    context=_context(),
                    reference_scope=ProviderReferenceScopeV1.from_turn(_context().turn),
                )
            dispatched_call_count = ledger.dispatched_call_count
            events = ledger.events

        self.assertEqual(completed.plan, plan)
        self.assertEqual(dispatched_call_count, 1)
        self.assertEqual(
            events[-1]["state"],
            ProviderCallState.ACCEPTED.value,
        )
        handoff = completed.handoff
        self.assertEqual(handoff.selected_evidence[0].concise_authoritative_fact, fact)
        self.assertIsNotNone(backend.last_provider_result)
        assert backend.last_provider_result is not None
        self.assertNotIn(
            "raw_fact_not_in_debug",
            repr(backend.last_provider_result.world_tool_debug),
        )
        self.assertNotIn("raw_fact_not_in_debug", repr(backend.last_provider_result.value))
        backend.take_last_provider_result()
        self.assertIsNone(backend.last_provider_result)
        self.assertFalse(hasattr(backend, "last_available_evidence_refs"))
        self.assertFalse(hasattr(backend, "last_provider_turn_handoff"))

    def test_new_backend_restart_allocates_fresh_immutable_operation_workspace(self) -> None:
        _ScriptedCompletedCognitionTransport.payload = to_primitive(_plan())
        with TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            workspace = root / "workspace"
            ledger = ContinuousProviderCallLedger(root / "ledger.jsonl")
            with patch(
                "cera.cognition.provider.CodexSDKTransport",
                _ScriptedCompletedCognitionTransport,
            ):
                first = CodexCognitionPlannerBackend(
                    lifecycle=SimpleNamespace(external_provider_boundary=False),
                    workspace=workspace,
                    call_ledger=ledger,
                )
                first.run_cognition_turn(
                    thread_id="thread:cognition-restart",
                    prompt="Return one cognition plan.",
                    context=_context(),
                    reference_scope=ProviderReferenceScopeV1.from_turn(_context().turn),
                )
                first.take_last_provider_result()
                self.assertIsNone(first.last_provider_result)
                first_workspace = workspace / "cognition_planner_operation_0001"
                marker = first_workspace / "immutable-marker.txt"
                marker.write_text("original", encoding="utf-8")
                second = CodexCognitionPlannerBackend(
                    lifecycle=SimpleNamespace(external_provider_boundary=False),
                    workspace=workspace,
                    call_ledger=ledger,
                )
                self.assertIsNone(second.last_provider_result)
                self.assertFalse(hasattr(second, "last_available_evidence_refs"))
                self.assertFalse(hasattr(second, "last_provider_turn_handoff"))
                second.run_cognition_turn(
                    thread_id="thread:cognition-restart",
                    prompt="Return one cognition plan.",
                    context=_context(),
                    reference_scope=ProviderReferenceScopeV1.from_turn(_context().turn),
                )
            self.assertEqual(marker.read_text(encoding="utf-8"), "original")
            self.assertTrue((workspace / "cognition_planner_operation_0002").is_dir())
            self.assertEqual(ledger.dispatched_call_count, 2)

    def test_schema_closes_autonomy_and_provisional_scope(self) -> None:
        context = _context()
        schema = cognition_plan_json_schema(
            context=context,
            reference_scope=ProviderReferenceScopeV1.from_turn(context.turn),
        )
        properties = schema["properties"]
        decision = properties["decision_records"]["items"]["properties"]
        autonomy = decision["autonomy_application"]["properties"]
        self.assertIs(autonomy["mind_precedence_applied"]["const"], True)
        self.assertIs(autonomy["body_precedence_applied"]["const"], True)
        provisional = properties["provisional_dependencies"]["items"]["properties"]
        self.assertEqual(
            provisional["provisional_record_id"]["enum"],
            ["provisional:door_claim"],
        )
        rendered = repr(schema)
        for forbidden in ("world_id", "branch_id", "candidate_id", "transaction_id"):
            self.assertNotIn(forbidden, rendered)
        directly_perceived = decision["observer_frame"]["properties"]["directly_perceived"]
        self.assertIn(
            "same source_ref may support multiple rows",
            directly_perceived["description"],
        )
        self.assertNotIn("uniqueItems", directly_perceived)
        projected = project_provider_output_schema(
            schema,
            ProviderSchemaDialect.OPENAI_STRUCTURED_OUTPUT_V1,
        ).provider_schema
        projected_directly_perceived = projected["properties"]["decision_records"]["items"][
            "properties"
        ]["observer_frame"]["properties"]["directly_perceived"]
        self.assertEqual(
            projected_directly_perceived["description"],
            directly_perceived["description"],
        )
        self.assertNotIn("uniqueItems", repr(projected))

    def test_static_citation_scope_is_fact_free_in_prompt_and_all_four_schemas(
        self,
    ) -> None:
        records = (
            EvidenceRecordV1(
                evidence_key="evidence:z-context-8000",
                subject_id="character:sakura_hanezawa",
                visibility=Visibility.PUBLIC,
                exact_content="z" * 8_000,
            ),
            EvidenceRecordV1(
                evidence_key="evidence:b-citable-4000",
                subject_id="character:sakura_hanezawa",
                visibility=Visibility.PUBLIC,
                exact_content="é" * 4_000,
            ),
            EvidenceRecordV1(
                evidence_key="evidence:a-context-4001",
                subject_id="character:sakura_hanezawa",
                visibility=Visibility.PUBLIC,
                exact_content="a" * 4_001,
            ),
        )
        turn = replace(_turn(), evidence_records=records)
        context = replace(_context(), turn=turn)
        scope = cognition_static_citation_scope(turn)
        self.assertEqual(
            scope.citable_static_evidence_refs,
            ("evidence:b-citable-4000",),
        )
        self.assertEqual(
            scope.context_only_static_evidence_refs,
            ("evidence:a-context-4001", "evidence:z-context-8000"),
        )
        self.assertFalse(
            set(scope.citable_static_evidence_refs).intersection(
                scope.context_only_static_evidence_refs
            )
        )
        scope_json = canonical_json(scope.to_payload())
        self.assertNotIn("é" * 4_000, scope_json)
        self.assertNotIn("a" * 4_001, scope_json)
        self.assertNotIn("z" * 8_000, scope_json)

        prompt = cognition_turn_prompt(context)
        static_header = prompt.split("[CURRENT COGNITION TURN]", 1)[0]
        self.assertEqual(
            static_header,
            "[STATIC CITATION SCOPE]\n" + scope_json + "\n",
        )
        reference_scope = ProviderReferenceScopeV1.from_turn(turn)
        self.assertEqual(
            reference_scope.evidence_keys,
            (
                turn.current_source_key,
                "evidence:z-context-8000",
                "evidence:b-citable-4000",
                "evidence:a-context-4001",
            ),
        )
        schema = cognition_plan_json_schema(
            context=context,
            reference_scope=reference_scope,
        )
        item_evidence = schema["properties"]["sequence"]["properties"]["items"]["items"][
            "properties"
        ]["evidence_keys"]
        self.assertEqual(
            item_evidence["items"]["enum"],
            [turn.current_source_key, "evidence:b-citable-4000"],
        )
        self.assertIn(scope_json, item_evidence["description"])
        self.assertNotIn("evidence:a-context-4001", item_evidence["items"]["enum"])
        self.assertNotIn("evidence:z-context-8000", item_evidence["items"]["enum"])
        decision = schema["properties"]["decision_records"]["items"]["properties"]
        descriptions = (
            decision["causal_trigger_refs"]["description"],
            decision["observer_frame"]["properties"]["directly_perceived"]["items"]["properties"][
                "source_ref"
            ]["description"],
            decision["decisive_factor_refs"]["description"],
            decision["material_pressures"]["items"]["properties"]["evidence_refs"]["description"],
        )
        self.assertEqual(len(descriptions), 4)
        for description in descriptions:
            self.assertIn(scope_json, description)
            self.assertIn("citable_static_evidence_refs", description)
            self.assertIn("context_only_static_evidence_refs", description)
            self.assertIn("never count, truncate, or summarize", description)
        rendered_schema = repr(schema)
        self.assertNotIn("é" * 4_000, rendered_schema)
        self.assertNotIn("a" * 4_001, rendered_schema)
        self.assertNotIn("z" * 8_000, rendered_schema)

    def test_persistent_session_installs_stable_prompt_once(self) -> None:
        backend = _FakeBackend(_plan())
        persisted: list[str] = []
        session = PersistentCognitionPlannerSession(
            backend,
            persist_thread_id=persisted.append,
        )
        self.assertEqual(session.plan(_context()), _plan())
        self.assertEqual(session.plan(_context()), _plan())
        self.assertEqual(
            backend.starts,
            [(COGNITION_PLANNER_BASE_INSTRUCTIONS, COGNITION_PLANNER_PROFILE)],
        )
        self.assertEqual(persisted, ["thread:cognition"])
        self.assertEqual(len(backend.calls), 2)
        self.assertTrue(all("[CURRENT COGNITION TURN]" in call[1] for call in backend.calls))
        self.assertTrue(all("[STATIC CITATION SCOPE]" in call[1] for call in backend.calls))
        self.assertTrue(
            all(COGNITION_PLANNER_BASE_INSTRUCTIONS not in call[1] for call in backend.calls)
        )
        self.assertEqual(session.last_available_evidence_refs, ("source:current",))

    def test_dynamic_evidence_scope_is_append_only_for_one_session_call(self) -> None:
        dynamic = _dynamic(
            CognitionCitationClass.DYNAMIC_EXACT_RECORD_VALIDATION_EVIDENCE,
            owner_id="character:sakura_hanezawa",
            fact='{"record_id":"record:test"}',
        )
        backend = _FakeBackend(
            _plan_citing(dynamic.evidence_ref),
            dynamic_evidence=(dynamic,),
        )
        session = PersistentCognitionPlannerSession(backend)
        session.plan(_context())
        self.assertEqual(
            session.last_available_evidence_refs,
            ("source:current", dynamic.evidence_ref),
        )

    def test_transient_handoff_clears_on_next_request_and_backend_failure(self) -> None:
        backend = _FakeBackend(_plan(), failure_on_call=2)
        session = PersistentCognitionPlannerSession(backend)
        session.plan(_context())
        self.assertIsNotNone(session.last_provider_turn_handoff)
        backend.last_available_evidence_refs = ("binding_record_stale000000000001",)
        backend.last_provider_turn_handoff = object()
        backend.last_provider_result = object()
        changed = replace(
            _context(),
            turn=replace(
                _turn(),
                current_source_key="source:next",
            ),
        )
        with self.assertRaisesRegex(StateConflictError, "injected"):
            session.plan(changed)
        self.assertEqual(session.last_available_evidence_refs, ())
        self.assertIsNone(session.last_provider_turn_handoff)
        self.assertEqual(backend.last_available_evidence_refs, ())
        self.assertIsNone(backend.last_provider_turn_handoff)
        self.assertIsNone(backend.last_provider_result)

    def test_transient_handoff_clears_on_reset_prepare_and_abandon(self) -> None:
        for operation in ("reset", "prepare", "abandon"):
            with self.subTest(operation=operation):
                backend = _FakeBackend(_plan())
                interrupted: list[str] = []
                abandoned: list[str] = []
                session = PersistentCognitionPlannerSession(
                    backend,
                    archive_interrupted_thread=interrupted.append,
                    archive_completed_uncommitted_thread=abandoned.append,
                )
                session.plan(_context())
                backend.last_available_evidence_refs = ("binding_record_stale000000000001",)
                backend.last_provider_turn_handoff = object()
                backend.last_provider_result = object()
                expected = text_sha256("thread:cognition")
                if operation == "reset":
                    session.reset_after_transport_failure(expected)
                    self.assertEqual(interrupted, ["thread:cognition"])
                elif operation == "prepare":
                    self.assertEqual(session.prepare_fresh_thread(), expected)
                else:
                    session.abandon_completed_uncommitted(expected)
                    self.assertEqual(abandoned, ["thread:cognition"])
                self.assertEqual(session.last_available_evidence_refs, ())
                self.assertIsNone(session.last_provider_turn_handoff)
                self.assertEqual(backend.last_available_evidence_refs, ())
                self.assertIsNone(backend.last_provider_turn_handoff)
                self.assertIsNone(backend.last_provider_result)

    def test_pi_adapter_propagates_global_autonomy_into_cognition(self) -> None:
        backend = _FakeBackend(_plan())
        session = PersistentCognitionPlannerSession(backend)
        adapter = RetainedCognitionPlannerAdapter(session)
        result = adapter.plan(
            PlannerTurnInputV1(
                world_id="world:test",
                branch_id="branch:main",
                scene_id="scene:door",
                exact_user_source="Ted asks Sakura to open the door.",
                current_state={
                    "accepted_present_character_ids": [
                        "character:ted",
                        "character:sakura_hanezawa",
                    ],
                    "explicitly_authorized_remote_character_ids": [],
                    "public_scene_state": "Ted and Sakura are inside by the closed door.",
                    "unresolved_threads": [],
                    "hard_boundaries": [],
                    "approved_targets": [],
                    "durable_changes": [],
                    "provisional_canon_lineage": [
                        {
                            "provisional_canon_id": "provisional:test-1",
                            "status": "unresolved",
                        }
                    ],
                },
                characters={"character:sakura_hanezawa": {"name": "Sakura"}},
                relationships={},
                relevant_memories={},
                accepted_records=(),
                request_controls=LeanSceneRequestControlsV1(
                    schema_version=LeanSceneRequestControlsV1.SCHEMA_VERSION,
                    session_id="test-session",
                    character_autonomy="both",
                ),
            )
        )
        self.assertEqual(result.sequence, result.decision_bundle["sequence"])
        self.assertEqual(
            adapter.last_context.autonomy_mode,
            CharacterAutonomyMode.BOTH,
        )
        self.assertEqual(
            adapter.last_context.available_provisional_record_ids,
            ("provisional:test-1",),
        )
        self.assertEqual(result.validation_evidence, ())
        self.assertEqual(session.last_available_evidence_refs, ())
        self.assertIsNone(session.last_provider_turn_handoff)
        self.assertEqual(backend.last_available_evidence_refs, ())
        self.assertIsNone(backend.last_provider_turn_handoff)
        self.assertIsNone(backend.last_provider_result)


if __name__ == "__main__":
    unittest.main()
