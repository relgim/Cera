from __future__ import annotations

from pathlib import Path
from dataclasses import dataclass, replace
import json
import re
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from cera.continuous.prompting import PLANNER_STABLE_INSTRUCTIONS
from scripts.run_continuous_planner_validator_job4 import (
    BRANCH_ID,
    JobHarness,
    validate_job4_identity,
    StablePrefixTransport,
    WORLD_ID,
    build_report,
    compatibility,
    source_character_summary,
    seed_world,
    validate_declared_unittest_ids,
)
from cera.continuous.call_ledger import (
    ContinuousProviderCallLedger,
    ProviderCallState,
)
from cera.continuous.contracts import CharacterRoleLedgerV1
from cera.continuous.provider import (
    ContinuousDeepSeekDraftV1,
    ContinuousValidatorDraftV1,
    ProviderEventRecordDraftV1,
    ProviderSceneSummaryDraftV1,
    _transport_stored_thread_sha256,
    continuous_deepseek_route,
    continuous_planner_route,
    continuous_validator_route,
)
from cera.continuous.sessions import (
    ContinuousSessionCoordinator,
    ContinuousSessionRole,
    InMemoryContinuousStoredSessionPort,
)
from cera.continuous.world import ContinuousWorldStore
from cera.providers import (
    CodexMcpRuntimeBinding,
    CodexSDKTransport,
    DeepSeekChatTransport,
    DeepSeekMessage,
    ProviderOutputMode,
    ProviderTransportError,
)
from cera.providers.codex import CodexWorkerResult
from cera.providers.codex import _SubprocessCodexRunner
from cera.serialization import canonical_sha256, text_sha256, to_primitive
from tests.test_continuous_world import (
    composer_draft,
    package,
    rich_sequence,
    scene_summary_package,
)


ROOT = Path(__file__).resolve().parents[1]


class _Transport:
    route = SimpleNamespace()

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def invoke(self, prompt: str, **kwargs):
        self.prompts.append(prompt)
        return SimpleNamespace(prompt=prompt)


@dataclass(frozen=True, slots=True)
class _ScriptedReceipt:
    requested_model: str
    external_provider_calls: int = 0


@dataclass(frozen=True, slots=True)
class _ScriptedTelemetry:
    provider_thread_id_sha256: str
    model: str
    reasoning_effort: str
    fast_mode_enabled: bool = False


class _ScriptedCodexTransport:
    def __init__(self, route, thread_id: str, produce) -> None:
        self.route = route
        self.runner = SimpleNamespace(provider_thread_id=thread_id)
        self._thread_id = thread_id
        self._produce = produce

    def invoke(
        self,
        prompt: str,
        *,
        on_worker_started=None,
        on_worker_preflight=None,
        on_transport_invoke=None,
        **_kwargs,
    ):
        for callback in (
            on_worker_started,
            on_worker_preflight,
            on_transport_invoke,
        ):
            if callback is not None:
                callback()
        value = self._produce(prompt)
        return SimpleNamespace(
            parsed_json=to_primitive(value),
            receipt=_ScriptedReceipt(
                requested_model=self.route.model_name,
            ),
            operation_telemetry=_ScriptedTelemetry(
                provider_thread_id_sha256=text_sha256(self._thread_id),
                model=self.route.model_name,
                reasoning_effort=self.route.reasoning_effort,
            ),
            tool_call_count=0,
            failed_tool_call_count=0,
            tool_names=(),
            tool_server_names=(),
        )


class _ScriptedDeepSeekTransport:
    def __init__(self, produce) -> None:
        self.route = continuous_deepseek_route()
        self._produce = produce

    def invoke(self, messages, *, on_transport_invoke=None, **_kwargs):
        if on_transport_invoke is not None:
            on_transport_invoke()
        prompt = messages[-1].content
        return SimpleNamespace(
            parsed_json=to_primitive(self._produce(prompt)),
            receipt=_ScriptedReceipt(
                requested_model=self.route.model_name,
            ),
            tool_names=(),
            tool_server_names=(),
        )


class ContinuousJob4HarnessTests(unittest.TestCase):
    def test_declared_unittest_ids_must_resolve_before_publication(self) -> None:
        valid = (
            "tests.test_continuous_planner_validator.ContinuousSessionTests."
            "test_restart_rejects_pre_v6_policy_compatibility",
        )
        self.assertEqual(validate_declared_unittest_ids(valid), {valid[0]: 1})
        with self.assertRaisesRegex(ValueError, "did not resolve"):
            validate_declared_unittest_ids(
                (
                    "tests.test_continuous_planner_validator."
                    "ContinuousSessionLifecycleTests."
                    "test_restart_rejects_pre_v6_policy_compatibility",
                )
            )

    def test_parameterized_canary_identity_rejects_historical_reuse(self) -> None:
        cycle_id = "cycle:new"
        task_id = "task:new"
        authorization = "a" * 64
        manifest = {
            "cycle_id": cycle_id,
            "job4": {
                "task_id": task_id,
                "authorization_record_sha256": authorization,
            },
        }
        validate_job4_identity(
            manifest,
            expected_cycle_id=cycle_id,
            expected_task_id=task_id,
            expected_authorization_sha256=authorization,
            maximum_provider_calls=10,
        )
        historical_cycle = "2026-08-01-continuous-planner-validator-v1-cycle-001"
        historical_task = "continuous-planner-validator-three-turn-scene-change-canary-v1"
        with self.assertRaisesRegex(ValueError, "historical failed"):
            validate_job4_identity(
                {
                    "cycle_id": historical_cycle,
                    "job4": {
                        "task_id": historical_task,
                        "authorization_record_sha256": authorization,
                    },
                },
                expected_cycle_id=historical_cycle,
                expected_task_id=historical_task,
                expected_authorization_sha256=authorization,
                maximum_provider_calls=10,
            )
        with self.assertRaisesRegex(ValueError, "ceiling"):
            validate_job4_identity(
                manifest,
                expected_cycle_id=cycle_id,
                expected_task_id=task_id,
                expected_authorization_sha256=authorization,
                maximum_provider_calls=11,
            )

    def test_stable_prefix_is_not_resent_in_the_stored_turn(self) -> None:
        inner = _Transport()
        transport = StablePrefixTransport(inner, PLANNER_STABLE_INSTRUCTIONS)
        result = transport.invoke(
            PLANNER_STABLE_INSTRUCTIONS + "\n\n[CURRENT AUTHORITATIVE TURN PACKET]\n{}"
        )
        self.assertEqual(result.prompt, "[CURRENT AUTHORITATIVE TURN PACKET]\n{}")
        self.assertNotIn(PLANNER_STABLE_INSTRUCTIONS, inner.prompts[0])

    def test_stable_prefix_recursively_exposes_stored_thread_identity(self) -> None:
        inner = SimpleNamespace(
            route=SimpleNamespace(),
            runner=SimpleNamespace(provider_thread_id="stored-planner-thread"),
        )
        wrapped = StablePrefixTransport(inner, PLANNER_STABLE_INSTRUCTIONS)
        self.assertEqual(
            _transport_stored_thread_sha256(wrapped),
            text_sha256("stored-planner-thread"),
        )

    def test_actual_codex_and_deepseek_transports_mark_submission_boundary(self) -> None:
        class Runner:
            def __init__(self, output: str) -> None:
                self.output = output

            def run(self, *, route, **_kwargs):
                return CodexWorkerResult(
                    output_text=self.output,
                    provider_request_id="fake-request",
                    returned_model=route.model_name,
                    duration_ms=1,
                    input_tokens=1,
                    cached_input_tokens=0,
                    output_tokens=1,
                    reasoning_output_tokens=0,
                    transport_version=route.transport_version,
                    pre_registered_turn_count=1,
                )

        schema = {
            "type": "object",
            "properties": {"ok": {"type": "boolean"}},
            "required": ["ok"],
            "additionalProperties": False,
        }
        for output in ("not-json", "[]"):
            with self.subTest(output=output), TemporaryDirectory() as directory:
                workspace = Path(directory).resolve()
                markers: list[str] = []
                transport = CodexSDKTransport(
                    continuous_planner_route(),
                    workspace=workspace,
                    runner=Runner(output),
                )
                with self.assertRaises(ProviderTransportError):
                    transport.invoke(
                        "test prompt",
                        output_schema=schema,
                        on_transport_invoke=lambda: markers.append("invoked"),
                    )
                self.assertEqual(markers, ["invoked"])

        with TemporaryDirectory() as directory:
            workspace = Path(directory).resolve()
            markers = []
            transport = CodexSDKTransport(
                continuous_planner_route(), workspace=workspace, runner=Runner('{"ok":true}')
            )
            binding = CodexMcpRuntimeBinding(
                server_name="cera_continuous_world_v1",
                url="http://127.0.0.1:43123/mcp",
                bearer_token_environment_variable="CERA_REQUEST_EVIDENCE_TOKEN",
                bearer_token="fake-secret",
                enabled_tools=("cera_world_read",),
                binding_sha256=canonical_sha256({"fake": "binding"}),
                minimum_tool_calls=1,
                maximum_tool_calls=1,
            )
            with self.assertRaises(ProviderTransportError):
                transport.invoke(
                    "test prompt",
                    output_schema=schema,
                    mcp_binding=binding,
                    on_transport_invoke=lambda: markers.append("invoked"),
                )
            self.assertEqual(markers, ["invoked"])

        markers = []

        def timed_out(*_args, **_kwargs):
            raise TimeoutError("fake timeout")

        deepseek = DeepSeekChatTransport(
            continuous_deepseek_route(),
            opener=timed_out,
            environment={"DEEPSEEK_API_KEY": "fake-key"},
        )
        with self.assertRaises(ProviderTransportError):
            deepseek.invoke(
                (DeepSeekMessage(role="user", content="test"),),
                output_mode=ProviderOutputMode.JSON_OBJECT,
                on_transport_invoke=lambda: markers.append("invoked"),
            )
        self.assertEqual(markers, ["invoked"])

    def test_actual_subprocess_sidecar_marks_post_submit_failure(self) -> None:
        markers: list[str] = []
        with TemporaryDirectory() as directory:
            workspace = Path(directory).resolve()
            with self.assertRaises(ProviderTransportError) as raised:
                _SubprocessCodexRunner(
                    worker_module="tests.fixtures.codex_progress_worker"
                ).run(
                    route=continuous_planner_route(),
                    prompt="provider-free fixture",
                    output_schema={
                        "type": "object",
                        "properties": {"ok": {"type": "boolean"}},
                        "required": ["ok"],
                        "additionalProperties": False,
                    },
                    workspace=workspace,
                    mcp_binding=None,
                    on_worker_started=lambda: markers.append("worker_started"),
                    on_worker_preflight=lambda: markers.append("preflight"),
                    on_provider_submit=lambda: markers.append("thread_run"),
                )
        self.assertEqual(raised.exception.external_provider_calls_observed, 1)
        self.assertIn("worker_stage:thread_run", raised.exception.safe_diagnostics)
        self.assertEqual(markers.count("worker_started"), 1)
        self.assertGreaterEqual(markers.count("preflight"), 1)
        self.assertGreaterEqual(markers.count("thread_run"), 1)

    def test_subprocess_sidecar_stage_matrix_and_stranded_accounting(self) -> None:
        schema = {
            "type": "object",
            "properties": {"ok": {"type": "boolean"}},
            "required": ["ok"],
            "additionalProperties": False,
        }

        def invoke(control: dict[str, str], *, timeout_seconds: int = 5):
            directory = TemporaryDirectory()
            self.addCleanup(directory.cleanup)
            workspace = Path(directory.name).resolve()
            route = replace(
                continuous_planner_route(), timeout_seconds=timeout_seconds
            )
            return _SubprocessCodexRunner(
                worker_module="tests.fixtures.codex_stage_matrix_worker",
                provider_thread_id="fixture-thread",
            ).run(
                route=route,
                prompt=json.dumps(control, sort_keys=True),
                output_schema=schema,
                workspace=workspace,
                mcp_binding=None,
            )

        for stage, expected_calls in (
            ("worker_launch", 0),
            ("sdk_import", 0),
            ("account_check", 0),
            ("thread_resume", 0),
            ("thread_run", 1),
            ("thread_read", 1),
        ):
            with self.subTest(stage=stage), self.assertRaises(
                ProviderTransportError
            ) as raised:
                invoke({"mode": "fail", "stage": stage})
            self.assertEqual(
                raised.exception.external_provider_calls_observed,
                expected_calls,
            )
            self.assertIn(
                f"worker_stage:{stage}", raised.exception.safe_diagnostics
            )

        success = invoke({"mode": "success"})
        self.assertEqual(success.output_text, '{"ok":true}')
        with self.assertRaises(ProviderTransportError) as malformed:
            invoke({"mode": "malformed"})
        self.assertEqual(malformed.exception.external_provider_calls_observed, 1)
        self.assertIn(
            "transport:invalid_worker_envelope",
            malformed.exception.safe_diagnostics,
        )
        for stage, expected_calls in (("sdk_import", 0), ("thread_run", 1)):
            with self.subTest(timeout_stage=stage), self.assertRaises(
                ProviderTransportError
            ) as timeout:
                invoke(
                    {"mode": "timeout", "stage": stage}, timeout_seconds=1
                )
            self.assertEqual(
                timeout.exception.external_provider_calls_observed,
                expected_calls,
            )
            self.assertIn("transport:timeout", timeout.exception.safe_diagnostics)

        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            workspace = root / "workspace"
            workspace.mkdir()
            ledger = ContinuousProviderCallLedger(root / "calls.jsonl")
            route = replace(continuous_planner_route(), timeout_seconds=1)
            runner = _SubprocessCodexRunner(
                worker_module="tests.fixtures.codex_stage_matrix_worker",
                provider_thread_id="fixture-thread",
            )
            with self.assertRaises(ProviderTransportError):
                ledger.execute(
                    owner="planner",
                    operation="stranded_thread_run",
                    route=route.route_id,
                    model=route.model_name,
                    effort=route.reasoning_effort,
                    dispatch_with_stage_markers=lambda markers: runner.run(
                        route=route,
                        prompt=json.dumps(
                            {"mode": "timeout", "stage": "thread_run"},
                            sort_keys=True,
                        ),
                        output_schema=schema,
                        workspace=workspace,
                        mcp_binding=None,
                        on_worker_started=markers.mark_worker_started,
                        on_worker_preflight=markers.mark_worker_preflight,
                        on_provider_submit=markers.mark_transport_invoked,
                    ),
                    finalize=lambda value: value,
                    stored_thread_sha256=text_sha256("fixture-thread"),
                )
            self.assertEqual(ledger.dispatched_call_count, 1)
            self.assertEqual(ledger.unresolved_prepared_call_ids, ())
            call_id = ledger.events[0]["call_id"]
            self.assertEqual(
                ledger.terminal_state(call_id), ProviderCallState.PROVIDER_FAILED
            )

    def test_exact_job_harness_summary_path_reaches_first_provider_boundary(self) -> None:
        class FirstProviderBoundary(RuntimeError):
            pass

        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            world = ContinuousWorldStore(root / "worlds")
            seed_world(world, ROOT)
            session_port = InMemoryContinuousStoredSessionPort()
            planner_session = ContinuousSessionCoordinator(
                compatibility(world, ContinuousSessionRole.PLANNER), session_port
            )
            validator_session = ContinuousSessionCoordinator(
                compatibility(world, ContinuousSessionRole.VALIDATOR), session_port
            )
            planner_handle = planner_session.ensure_session().provider_thread_id
            validator_handle = validator_session.ensure_session().provider_thread_id
            lifecycle = root / "lifecycle"
            lifecycle.mkdir()
            harness = JobHarness(
                source_root=ROOT,
                cycle=root / "cycle",
                world=world,
                planner_session=planner_session,
                validator_session=validator_session,
                planner_handle=planner_handle,
                validator_handle=validator_handle,
                lifecycle_root=lifecycle,
                call_ledger=ContinuousProviderCallLedger(root / "calls.jsonl"),
            )
            harness.provider_call = lambda *_args, **_kwargs: (_ for _ in ()).throw(
                FirstProviderBoundary("provider boundary reached")
            )
            summary = source_character_summary(
                ROOT, "sakura", world=world, world_file_revision=1
            )
            with self.assertRaisesRegex(FirstProviderBoundary, "boundary reached"):
                harness.run_turn(
                    turn_number=1,
                    scene_id="scene-arrival",
                    summaries=(summary,),
                )
            self.assertFalse(
                (world.branch_root(WORLD_ID, BRANCH_ID) / "ACTIVE" / "ACTIVE").exists()
            )

    def test_exact_job_harness_completes_all_ten_provider_free_stages(self) -> None:
        class ProviderFreeHarness(JobHarness):
            def __init__(self, **kwargs):
                super().__init__(**kwargs)
                self.planner_prompts: list[str] = []

            @staticmethod
            def _result(value):
                return SimpleNamespace(
                    value=value,
                    provider_receipt=None,
                    operation_telemetry=None,
                    tool_call_count=0,
                    failed_tool_call_count=0,
                    world_tool_debug=None,
                )

            def provider_call(self, label, owner, operation):
                result = operation()
                self.call_records.append(
                    {
                        "index": len(self.call_records) + 1,
                        "label": label,
                        "owner": owner,
                        "status": "provider_free_passed",
                    }
                )
                return result

            def codex_planner(self, prompt, turn_id):
                self.planner_prompts.append(prompt)
                bindings = tuple(
                    dict.fromkeys(
                        re.findall(r'"binding_key":"(binding_[a-z0-9_]+)"', prompt)
                    )
                )
                value = rich_sequence()
                value = replace(
                    value,
                    sequence_id=f"sequence:{turn_id.replace('-', '_')}",
                    world_id=WORLD_ID,
                    branch_id=BRANCH_ID,
                    scene_id=("scene-002" if turn_id == "turn-003" else "scene-001"),
                    selected_character_ids=(
                        ("character:mia_hanezawa",)
                        if turn_id == "turn-003"
                        else value.selected_character_ids
                    ),
                    beats=tuple(
                        replace(
                            beat,
                            roles=(
                                CharacterRoleLedgerV1(
                                    action_owner_ids=("character:mia_hanezawa",),
                                    addressed_ids=("character:ted",),
                                )
                                if turn_id == "turn-003"
                                else beat.roles
                            ),
                            source_evidence_bindings=bindings,
                        )
                        for beat in value.beats
                    ),
                )
                return self._result(value)

            def deepseek(self, _prompt):
                if self._active_turn_id == "turn-003":
                    draft = composer_draft("Mia answers cautiously.")
                    draft = SimpleNamespace(
                        story_text=draft.story_text,
                        protected_user_realizations=(),
                        story_segments=(
                            replace(
                                draft.story_segments[0],
                                roles=CharacterRoleLedgerV1(
                                    action_owner_ids=("character:mia_hanezawa",),
                                    addressed_ids=("character:ted",),
                                ),
                            ),
                        ),
                    )
                    return self._result(draft)
                return self._result(composer_draft("Sakura requests bounded proof."))

            def codex_validator(self, _prompt, turn_id, *, accepted_pairs=()):
                if accepted_pairs:
                    result = scene_summary_package(
                        accepted_pairs[0], new_prompt="unused"
                    )
                    result = replace(
                        result,
                        world_id=WORLD_ID,
                        branch_id=BRANCH_ID,
                        optional_scene_summary=replace(
                            result.optional_scene_summary,
                            completed_scene_id="scene-001",
                            accepted_turn_ids=tuple(
                                value.accepted_turn_id for value in accepted_pairs
                            ),
                            last_five_exact_pairs=tuple(accepted_pairs),
                        ),
                    )
                    return self._result(result)
                revision = int(turn_id.rsplit("-", 1)[1])
                result = package(turn_id=turn_id, revision=revision)
                if turn_id == "turn-003":
                    item = result.complete_final_sequence.items[0]
                    item = replace(
                        item,
                        realized_event="Mia answers cautiously in the later scene.",
                        private_state_owner_ids=("character:mia_hanezawa",),
                        roles=CharacterRoleLedgerV1(
                            action_owner_ids=("character:mia_hanezawa",),
                            addressed_ids=("character:ted",),
                        ),
                        field_scopes=tuple(
                            replace(
                                scope,
                                knowledge_owner_id=(
                                    "character:mia_hanezawa"
                                    if scope.knowledge_owner_id is not None
                                    else None
                                ),
                                roles=CharacterRoleLedgerV1(
                                    action_owner_ids=("character:mia_hanezawa",),
                                    addressed_ids=("character:ted",),
                                ),
                            )
                            for scope in item.field_scopes
                        ),
                    )
                    result = replace(
                        result,
                        complete_final_sequence=replace(
                            result.complete_final_sequence, items=(item,)
                        ),
                        event_record=replace(
                            result.event_record,
                            participant_ids=(
                                "character:mia_hanezawa",
                                "character:ted",
                            ),
                            item_role_ledgers=(
                                replace(
                                    result.event_record.item_role_ledgers[0],
                                    roles=item.roles,
                                ),
                            ),
                            summary="Mia answers cautiously in the later scene.",
                        ),
                    )
                result = replace(
                    result,
                    world_id=WORLD_ID,
                    branch_id=BRANCH_ID,
                    world_edit_operations=(),
                    created_field_log=(),
                    event_record=replace(
                        result.event_record,
                        scene_id=(
                            "scene-002" if turn_id == "turn-003" else "scene-001"
                        ),
                    ),
                )
                return self._result(result)

        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            world = ContinuousWorldStore(root / "worlds")
            seed_world(world, ROOT)
            session_port = InMemoryContinuousStoredSessionPort()
            planner_session = ContinuousSessionCoordinator(
                compatibility(world, ContinuousSessionRole.PLANNER), session_port
            )
            validator_session = ContinuousSessionCoordinator(
                compatibility(world, ContinuousSessionRole.VALIDATOR), session_port
            )
            lifecycle = root / "lifecycle"
            lifecycle.mkdir()
            harness = ProviderFreeHarness(
                source_root=ROOT,
                cycle=root / "cycle",
                world=world,
                planner_session=planner_session,
                validator_session=validator_session,
                planner_handle=planner_session.ensure_session().provider_thread_id,
                validator_handle=validator_session.ensure_session().provider_thread_id,
                lifecycle_root=lifecycle,
                call_ledger=ContinuousProviderCallLedger(root / "calls.jsonl"),
            )
            sakura = source_character_summary(
                ROOT, "sakura", world=world, world_file_revision=1
            )
            harness.run_turn(
                turn_number=1, scene_id="scene-001", summaries=(sakura,)
            )
            harness.run_turn(turn_number=2, scene_id="scene-001", summaries=())
            harness.summarize_scene()
            mia = source_character_summary(
                ROOT, "mia", world=world, world_file_revision=1
            )
            harness.run_turn(
                turn_number=3,
                scene_id="scene-002",
                summaries=(mia,),
                scene_change_context={"validated": True},
            )
            self.assertEqual(
                tuple(value["label"] for value in harness.call_records),
                (
                    "turn-1-planner",
                    "turn-1-deepseek",
                    "turn-1-validator",
                    "turn-2-planner",
                    "turn-2-deepseek",
                    "turn-2-validator",
                    "scene-1-validator-summary",
                    "turn-3-planner",
                    "turn-3-deepseek",
                    "turn-3-validator",
                ),
            )
            self.assertEqual(harness.provider_calls, 0)
            self.assertIn('"character_summary_bindings":[]', harness.planner_prompts[1])

    def test_complete_job_harness_uses_actual_ports_with_scripted_transports(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            world = ContinuousWorldStore(root / "worlds")
            seed_world(world, ROOT)
            session_port = InMemoryContinuousStoredSessionPort()
            planner_session = ContinuousSessionCoordinator(
                compatibility(world, ContinuousSessionRole.PLANNER), session_port
            )
            validator_session = ContinuousSessionCoordinator(
                compatibility(world, ContinuousSessionRole.VALIDATOR), session_port
            )
            holder: dict[str, JobHarness] = {}

            def planner_value(prompt: str):
                harness = holder["harness"]
                turn_id = harness._active_turn_id
                bindings = tuple(
                    dict.fromkeys(
                        re.findall(r'"binding_key":"(binding_[a-z0-9_]+)"', prompt)
                    )
                )
                value = rich_sequence()
                return replace(
                    value,
                    sequence_id=f"sequence:{turn_id.replace('-', '_')}",
                    world_id=WORLD_ID,
                    branch_id=BRANCH_ID,
                    scene_id=("scene-002" if turn_id == "turn-003" else "scene-001"),
                    selected_character_ids=(
                        ("character:mia_hanezawa",)
                        if turn_id == "turn-003"
                        else value.selected_character_ids
                    ),
                    beats=tuple(
                        replace(
                            beat,
                            roles=(
                                CharacterRoleLedgerV1(
                                    action_owner_ids=("character:mia_hanezawa",),
                                    addressed_ids=("character:ted",),
                                )
                                if turn_id == "turn-003"
                                else beat.roles
                            ),
                            source_evidence_bindings=bindings,
                        )
                        for beat in value.beats
                    ),
                )

            def composer_value(_prompt: str):
                turn_id = holder["harness"]._active_turn_id
                story = (
                    "Mia answers cautiously in the later scene."
                    if turn_id == "turn-003"
                    else "Sakura requests bounded proof."
                )
                draft = composer_draft(story)
                if turn_id == "turn-003":
                    segments = (
                        replace(
                            draft.story_segments[0],
                            roles=CharacterRoleLedgerV1(
                                action_owner_ids=("character:mia_hanezawa",),
                                addressed_ids=("character:ted",),
                            ),
                        ),
                    )
                else:
                    segments = draft.story_segments
                return ContinuousDeepSeekDraftV1(
                    schema_version=ContinuousDeepSeekDraftV1.SCHEMA_VERSION,
                    story_text=story,
                    protected_user_realizations=(),
                    story_segments=segments,
                )

            def validator_value(_prompt: str):
                harness = holder["harness"]
                if harness._active_validator_label == "scene-1-validator-summary":
                    summary = scene_summary_package(
                        harness.accepted_pairs[0], new_prompt="unused"
                    ).optional_scene_summary
                    summary = replace(
                        summary,
                        completed_scene_id="scene-001",
                        accepted_turn_ids=tuple(
                            value.accepted_turn_id for value in harness.accepted_pairs
                        ),
                        last_five_exact_pairs=tuple(harness.accepted_pairs),
                    )
                    return ContinuousValidatorDraftV1(
                        schema_version=ContinuousValidatorDraftV1.SCHEMA_VERSION,
                        package_id="package:scene_summary",
                        world_id=WORLD_ID,
                        branch_id=BRANCH_ID,
                        task_mode=scene_summary_package(
                            harness.accepted_pairs[0], new_prompt="unused"
                        ).task_mode,
                        semantic_status=scene_summary_package(
                            harness.accepted_pairs[0], new_prompt="unused"
                        ).semantic_status,
                        complete_final_sequence=None,
                        creator_review=None,
                        world_edit_operations=(),
                        created_field_log=(),
                        event_record=None,
                        optional_scene_summary=ProviderSceneSummaryDraftV1(
                            summary_id=summary.summary_id,
                            completed_scene_id=summary.completed_scene_id,
                            accepted_turn_ids=summary.accepted_turn_ids,
                            shortest_complete_summary=summary.shortest_complete_summary,
                            ending_state=summary.ending_state,
                            transition_context=summary.transition_context,
                        ),
                    )

                turn_id = harness._active_turn_id
                result = package(
                    turn_id=turn_id,
                    revision=int(turn_id.rsplit("-", 1)[1]),
                )
                if turn_id == "turn-003":
                    item = replace(
                        result.complete_final_sequence.items[0],
                        realized_event="Mia answers cautiously in the later scene.",
                        private_state_owner_ids=("character:mia_hanezawa",),
                        roles=CharacterRoleLedgerV1(
                            action_owner_ids=("character:mia_hanezawa",),
                            addressed_ids=("character:ted",),
                        ),
                        field_scopes=tuple(
                            replace(
                                scope,
                                knowledge_owner_id=(
                                    "character:mia_hanezawa"
                                    if scope.knowledge_owner_id is not None
                                    else None
                                ),
                                roles=CharacterRoleLedgerV1(
                                    action_owner_ids=("character:mia_hanezawa",),
                                    addressed_ids=("character:ted",),
                                ),
                            )
                            for scope in result.complete_final_sequence.items[0].field_scopes
                        ),
                    )
                    result = replace(
                        result,
                        complete_final_sequence=replace(
                            result.complete_final_sequence, items=(item,)
                        ),
                        event_record=replace(
                            result.event_record,
                            participant_ids=(
                                "character:mia_hanezawa",
                                "character:ted",
                            ),
                            item_role_ledgers=(
                                replace(
                                    result.event_record.item_role_ledgers[0],
                                    roles=item.roles,
                                ),
                            ),
                            summary=item.realized_event,
                        ),
                    )
                result = replace(
                    result,
                    world_id=WORLD_ID,
                    branch_id=BRANCH_ID,
                    world_edit_operations=(),
                    created_field_log=(),
                    event_record=replace(
                        result.event_record,
                        scene_id=(
                            "scene-002" if turn_id == "turn-003" else "scene-001"
                        ),
                    ),
                )
                event = result.event_record
                return ContinuousValidatorDraftV1(
                    schema_version=ContinuousValidatorDraftV1.SCHEMA_VERSION,
                    package_id=result.package_id,
                    world_id=result.world_id,
                    branch_id=result.branch_id,
                    task_mode=result.task_mode,
                    semantic_status=result.semantic_status,
                    complete_final_sequence=result.complete_final_sequence,
                    creator_review=result.creator_review,
                    world_edit_operations=(),
                    created_field_log=(),
                    event_record=ProviderEventRecordDraftV1(
                        event_id=event.event_id,
                        accepted_turn_id=event.accepted_turn_id,
                        scene_id=event.scene_id,
                        summary=event.summary,
                        final_sequence_item_keys=event.final_sequence_item_keys,
                        protected_user_source_claim_keys=(
                            event.protected_user_source_claim_keys
                        ),
                    ),
                    optional_scene_summary=None,
                )

            planner_handle = planner_session.ensure_session().provider_thread_id
            validator_handle = validator_session.ensure_session().provider_thread_id
            lifecycle_root = root / "lifecycle"
            lifecycle_root.mkdir()
            harness = JobHarness(
                source_root=ROOT,
                cycle=root / "cycle",
                world=world,
                planner_session=planner_session,
                validator_session=validator_session,
                planner_handle=planner_handle,
                validator_handle=validator_handle,
                lifecycle_root=lifecycle_root,
                call_ledger=ContinuousProviderCallLedger(
                    root / "calls.jsonl", maximum_calls=10
                ),
                planner_transport_factory=lambda _workspace, thread_id: _ScriptedCodexTransport(
                    continuous_planner_route(effort="medium"),
                    thread_id,
                    planner_value,
                ),
                validator_transport_factory=lambda _workspace, thread_id: _ScriptedCodexTransport(
                    continuous_validator_route(
                        model="gpt-5.6-terra", effort="high"
                    ),
                    thread_id,
                    validator_value,
                ),
                composer_transport_factory=lambda: _ScriptedDeepSeekTransport(
                    composer_value
                ),
                scripted_provider_free=True,
            )
            holder["harness"] = harness
            sakura = source_character_summary(
                ROOT, "sakura", world=world, world_file_revision=1
            )
            harness.run_turn(
                turn_number=1, scene_id="scene-001", summaries=(sakura,)
            )
            harness.run_turn(turn_number=2, scene_id="scene-001", summaries=())
            harness.summarize_scene()
            mia = source_character_summary(
                ROOT, "mia", world=world, world_file_revision=1
            )
            harness.run_turn(
                turn_number=3,
                scene_id="scene-002",
                summaries=(mia,),
                scene_change_context={"validated": True},
            )
            self.assertEqual(len(harness.call_records), 10)
            self.assertTrue(
                all(
                    value["status"] == "scripted_provider_free_passed"
                    for value in harness.call_records
                )
            )
            self.assertEqual(harness.provider_calls, 0)
            self.assertEqual(harness.scripted_transport_invocations, 10)
            self.assertEqual(harness.call_ledger.dispatched_call_count, 10)
            self.assertEqual(len(harness.poll_records), 20)
            self.assertEqual(
                planner_session.snapshot().accepted_turn_ids,
                ("turn-001", "turn-002", "turn-003"),
            )
            planner_handle_value = planner_session.ensure_session()
            validator_handle_value = validator_session.ensure_session()
            session_port.archive(
                planner_handle_value, "provider_free_job4_complete"
            )
            session_port.archive(
                validator_handle_value, "provider_free_job4_complete"
            )
            self.assertFalse(session_port.resume(planner_handle_value))
            self.assertFalse(session_port.resume(validator_handle_value))
            self.assertEqual(
                sum(operation == "archive" for operation, _ in session_port.operations),
                2,
            )

    def test_hanezawa_canary_summaries_use_real_genesis_sections(self) -> None:
        with TemporaryDirectory() as directory:
            world = ContinuousWorldStore(Path(directory).resolve() / "worlds")
            seed_world(world, ROOT)
            for character in ("sakura", "mia"):
                summary = source_character_summary(
                    ROOT, character, world=world, world_file_revision=1
                )
                self.assertTrue(summary.incomplete)
                self.assertTrue(summary.more_information_available)
                self.assertIn("Characters/", summary.source_path_or_record_id)
                self.assertGreater(len(summary.summary), 100)

    def test_report_contains_terminal_route_and_effect_accounting(self) -> None:
        report = build_report(
            {
                "status": "failed",
                "provider_calls": 2,
                "calls": [],
                "turns": [],
                "failure": {"stage": "turn-1-validator"},
                "story_database_writes": 0,
                "active_route_unchanged": True,
            }
        )
        self.assertIn("Provider calls observed:** 2 / 10", report)
        self.assertIn("deepseek-v4-flash", report)
        self.assertIn("turn-1-validator", report)


if __name__ == "__main__":
    unittest.main()
