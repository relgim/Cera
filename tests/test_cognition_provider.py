from __future__ import annotations

import json
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from cera.cognition import (
    CharacterAutonomyMode,
    CognitionPlanV1,
    CognitionTurnContextV1,
    LogicRoute,
    PersistentCognitionPlannerSession,
)
from cera.cognition.prompting import (
    COGNITION_PLANNER_BASE_INSTRUCTIONS,
    COGNITION_PLANNER_PROFILE,
)
from cera.cognition.provider import (
    COGNITION_PLANNER_ADAPTER,
    COGNITION_PLANNER_PROMPT,
    CodexCognitionPlannerBackend,
    cognition_planner_route,
)
from cera.cognition.provider_schema import cognition_plan_json_schema
from cera.continuous.call_ledger import (
    ContinuousProviderCallLedger,
    ProviderCallState,
)
from cera.errors import ErrorCode, StateConflictError
from cera.pi_scene.cognition_planner import RetainedCognitionPlannerAdapter
from cera.pi_scene.http_contracts import LeanSceneRequestControlsV1
from cera.pi_scene.runtime import PlannerTurnInputV1
from cera.providers import ProviderSchemaDialect, project_provider_output_schema
from cera.providers.models import (
    ProviderRetryableFailureCategory,
    ProviderTransportError,
)
from cera.sequence_first.contracts import ProviderReferenceScopeV1
from cera.serialization import to_primitive

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
    last_available_evidence_refs: tuple[str, ...] = ()
    starts: list[tuple[str, str]] = field(default_factory=list)
    calls: list[tuple[str, str, CognitionTurnContextV1]] = field(default_factory=list)

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
    ) -> CognitionPlanV1:
        del reference_scope
        self.calls.append((thread_id, prompt, context))
        return self.result

    def is_resumable(self, thread_id: str) -> bool:
        return self.resumable and thread_id == self.thread_id


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


class CognitionProviderContractTests(unittest.TestCase):
    def test_live_route_uses_bounded_hard_timeout_without_retry(self) -> None:
        route = cognition_planner_route()
        self.assertEqual(route.timeout_seconds, 600)
        self.assertEqual(route.automatic_retry_count, 0)
        self.assertFalse(route.fallback_enabled)
        self.assertEqual(COGNITION_PLANNER_PROFILE, "cera_full_model_cognition_planner_v6")
        self.assertEqual(
            COGNITION_PLANNER_ADAPTER,
            "cera.cognition.codex_planner_adapter.v4",
        )
        self.assertEqual(
            COGNITION_PLANNER_PROMPT,
            "cera.cognition.codex_planner_prompt.v5",
        )
        self.assertEqual(route.route_id, "cera_cognition_planner_sol_medium_v5")
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
        self.assertTrue(
            all(COGNITION_PLANNER_BASE_INSTRUCTIONS not in call[1] for call in backend.calls)
        )
        self.assertEqual(session.last_available_evidence_refs, ("source:current",))

    def test_dynamic_evidence_scope_is_append_only_for_one_session_call(self) -> None:
        dynamic_key = "binding_record_0123456789abcdefabcd"
        backend = _FakeBackend(
            _plan(),
            last_available_evidence_refs=("source:current", dynamic_key),
        )
        session = PersistentCognitionPlannerSession(backend)
        session.plan(_context())
        self.assertEqual(
            session.last_available_evidence_refs,
            ("source:current", dynamic_key),
        )

        missing_base = _FakeBackend(
            _plan(),
            last_available_evidence_refs=(dynamic_key,),
        )
        with self.assertRaisesRegex(StateConflictError, "not append-only"):
            PersistentCognitionPlannerSession(missing_base).plan(_context())

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


if __name__ == "__main__":
    unittest.main()
