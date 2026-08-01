from __future__ import annotations

from pathlib import Path
from dataclasses import replace
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
)
from cera.continuous.call_ledger import ContinuousProviderCallLedger
from cera.continuous.provider import continuous_deepseek_route, continuous_planner_route
from cera.continuous.provider import _transport_stored_thread_sha256
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
from cera.serialization import canonical_sha256, text_sha256
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


class ContinuousJob4HarnessTests(unittest.TestCase):
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
                            actor_ids=(
                                ("character:mia_hanezawa",)
                                if turn_id == "turn-003"
                                else beat.actor_ids
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
                                actor_ids=("character:mia_hanezawa",),
                                subject_ids=("character:mia_hanezawa",),
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
                        actor_ids=("character:mia_hanezawa",),
                        subject_ids=("character:mia_hanezawa",),
                        field_scopes=tuple(
                            replace(
                                scope,
                                knowledge_owner_id=(
                                    "character:mia_hanezawa"
                                    if scope.knowledge_owner_id is not None
                                    else None
                                ),
                                actor_ids=("character:mia_hanezawa",),
                                subject_ids=("character:mia_hanezawa",),
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
                            participant_ids=("character:mia_hanezawa",),
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
