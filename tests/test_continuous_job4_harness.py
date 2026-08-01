from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from cera.continuous.prompting import PLANNER_STABLE_INSTRUCTIONS
from scripts.run_continuous_planner_validator_job4 import (
    BRANCH_ID,
    JobHarness,
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
from cera.serialization import canonical_sha256, text_sha256


ROOT = Path(__file__).resolve().parents[1]


class _Transport:
    route = SimpleNamespace()

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def invoke(self, prompt: str, **kwargs):
        self.prompts.append(prompt)
        return SimpleNamespace(prompt=prompt)


class ContinuousJob4HarnessTests(unittest.TestCase):
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
