from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
import urllib.error
from dataclasses import fields
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from cera.errors import ContractValidationError, ErrorCode
from cera.evaluation import EvaluationRole
from cera.providers import (
    CodexMcpRuntimeBinding,
    CodexOperationTelemetryV1,
    CodexSDKTransport,
    CodexToolTimingV1,
    CodexUsageStepV1,
    CodexWorkerResult,
    DeepSeekMessage,
    LiveProviderCallReceipt,
    LiveProviderRoute,
    ProviderAuthMode,
    ProviderFailureCallReceipt,
    ProviderFinishReason,
    ProviderName,
    ProviderOutputMode,
    ProviderPricing,
    ProviderResponseFailureKind,
    ProviderRetryableFailureCategory,
    ProviderTransportError,
    codex_realization_verifier_candidate,
    codex_reasoner_candidate,
    codex_transport_probe_output_schema,
    deepseek_composer_candidate,
)
from cera.providers.codex import (
    CODEX_MCP_OBSERVATION_POLICY_CODE_MODE_V1,
    CODEX_MCP_OBSERVATION_POLICY_STRICT_V1,
    _codex_transport_json,
    _SubprocessCodexRunner,
)
from cera.providers.codex_sdk_compat import (
    CODEX_SDK_COMPATIBILITY_ID,
    EXPECTED_ROUTE_NOTIFICATION_SHA256,
)
from cera.registry import build_schema_registry
from cera.runtime.failure import PrivacySafeReceiptPayload
from cera.serialization import canonical_json, canonical_sha256, text_sha256, to_primitive
from tests.provider_fakes import (
    OfflineDeepSeekChatTransport,
    OfflinePersistentNoMcpCodexRunner,
    OfflineStoredCodexThreadRunner,
    OfflineSubprocessCodexRunner,
)

ACTIVE_REASONER_PROMPT_VERSION = "cera.codex_scene_reasoner_prompt.v25"
ACTIVE_COMPOSER_PROMPT_VERSION = "cera.deepseek_scene_composer_prompt.v26"
ACTIVE_VERIFIER_PROMPT_VERSION = (
    "cera.codex_scene_realization_verifier_prompt.v8"
)


PRICING_SOURCE = "https://api-docs.deepseek.com/quick_start/pricing/"


def deepseek_pricing(model: str = "deepseek-v4-pro") -> ProviderPricing:
    if model == "deepseek-v4-flash":
        rates = (2_800, 140_000, 280_000)
    else:
        rates = (3_625, 435_000, 870_000)
    return ProviderPricing(
        cache_hit_input_microusd_per_million=rates[0],
        cache_miss_input_microusd_per_million=rates[1],
        output_microusd_per_million=rates[2],
        source_url=PRICING_SOURCE,
        effective_date="2026-07-28",
    )


def deepseek_route(model: str = "deepseek-v4-pro") -> LiveProviderRoute:
    return LiveProviderRoute(
        schema_version=LiveProviderRoute.SCHEMA_VERSION,
        route_id=f"deepseek_composer_{model}",
        role=EvaluationRole.SCENE_COMPOSER,
        provider=ProviderName.DEEPSEEK,
        adapter_id="cera.deepseek_chat_composer.v1",
        model_name=model,
        model_revision="deepseek-v4-2026-04-24",
        prompt_version="cera.composer.transport_probe.v1",
        transport_name="deepseek_chat_completions",
        transport_version="2026-04-24",
        auth_mode=ProviderAuthMode.ENVIRONMENT_API_KEY,
        endpoint="https://api.deepseek.com",
        credential_environment_variable="DEEPSEEK_API_KEY",
        reasoning_effort=None,
        timeout_seconds=60,
        maximum_request_bytes=65_536,
        maximum_output_tokens=2_048,
        maximum_cost_microusd=100_000,
        automatic_retry_count=0,
        fallback_enabled=False,
        production_enabled=False,
        pricing=deepseek_pricing(model),
    )


def codex_route() -> LiveProviderRoute:
    return LiveProviderRoute(
        schema_version=LiveProviderRoute.SCHEMA_VERSION,
        route_id="codex_reasoner_sol_medium",
        role=EvaluationRole.SCENE_REASONER,
        provider=ProviderName.OPENAI_CODEX,
        adapter_id="cera.codex_python_sdk_reasoner.v1",
        model_name="gpt-5.6-sol",
        model_revision="openai-resolver-2026-07-28",
        prompt_version="cera.reasoner.transport_probe.v1",
        transport_name="codex_python_sdk_app_server",
        transport_version="0.144.4",
        auth_mode=ProviderAuthMode.CHATGPT_SESSION,
        endpoint=None,
        credential_environment_variable=None,
        reasoning_effort="medium",
        timeout_seconds=120,
        maximum_request_bytes=131_072,
        maximum_output_tokens=4_096,
        maximum_cost_microusd=0,
        automatic_retry_count=0,
        fallback_enabled=False,
        production_enabled=False,
        pricing=None,
        transport_compatibility_id=CODEX_SDK_COMPATIBILITY_ID,
        transport_compatibility_source_sha256=(
            EXPECTED_ROUTE_NOTIFICATION_SHA256
        ),
    )


class FakeHTTPResponse:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _tb) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


class RecordingOpener:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.calls = []

    def __call__(self, request, *, timeout):
        self.calls.append((request, timeout))
        return FakeHTTPResponse(self.payload)


class StaticCodexRunner:
    external_provider_boundary = False

    def __init__(
        self,
        *,
        returned_model: str = "gpt-5.6-sol",
        output_tokens: int = 12,
        mcp_server_names: tuple[str, ...] = (),
        mcp_tool_names: tuple[str, ...] = (),
        mcp_failed_tool_call_count: int = 0,
        operation_telemetry: CodexOperationTelemetryV1 | None = None,
    ) -> None:
        self.returned_model = returned_model
        self.output_tokens = output_tokens
        self.mcp_server_names = mcp_server_names
        self.mcp_tool_names = mcp_tool_names
        self.mcp_failed_tool_call_count = mcp_failed_tool_call_count
        self.operation_telemetry = operation_telemetry
        self.calls = 0
        self.last_schema: dict | None = None

    def run(self, *, route, prompt, output_schema, workspace, mcp_binding):
        self.calls += 1
        self.last_schema = output_schema
        return CodexWorkerResult(
            output_text='{"probe":"ok"}',
            provider_request_id="turn_private_id",
            returned_model=self.returned_model,
            duration_ms=1200,
            input_tokens=100,
            cached_input_tokens=40,
            output_tokens=self.output_tokens,
            reasoning_output_tokens=8,
            transport_version="0.144.4",
            mcp_server_names=self.mcp_server_names,
            mcp_tool_names=self.mcp_tool_names,
            mcp_tool_call_count=len(self.mcp_tool_names),
            mcp_failed_tool_call_count=self.mcp_failed_tool_call_count,
            operation_telemetry=self.operation_telemetry,
            pre_registered_turn_count=1,
        )


def codex_mcp_binding(
    *,
    server_name: str = "cera_request_evidence",
    enabled_tools: tuple[str, ...] = ("cera_get_turn_snapshot",),
    minimum_tool_calls: int = 1,
    maximum_tool_calls: int = 12,
) -> CodexMcpRuntimeBinding:
    return CodexMcpRuntimeBinding(
        server_name=server_name,
        url="http://127.0.0.1:43123/mcp",
        bearer_token_environment_variable="CERA_REQUEST_EVIDENCE_TOKEN",
        bearer_token="qualification-secret",
        enabled_tools=enabled_tools,
        binding_sha256=canonical_sha256({"fixture": "request-bound-evidence"}),
        minimum_tool_calls=minimum_tool_calls,
        maximum_tool_calls=maximum_tool_calls,
    )


def codex_operation_telemetry(
    observations: tuple[tuple[str, str], ...],
    statuses: tuple[str, ...],
    *,
    sequences: tuple[int, ...] | None = None,
) -> CodexOperationTelemetryV1:
    if sequences is None:
        sequences = tuple(range(1, len(observations) + 1))
    step = CodexUsageStepV1(
        sequence=1,
        observed_at_unix_us=20,
        input_tokens=100,
        cached_input_tokens=40,
        uncached_input_tokens=60,
        output_tokens=12,
        reasoning_tokens=8,
        thread_total_input_tokens=100,
        thread_total_cached_input_tokens=40,
        thread_total_output_tokens=12,
        thread_total_reasoning_tokens=8,
    )
    return CodexOperationTelemetryV1(
        schema_version=CodexOperationTelemetryV1.SCHEMA_VERSION,
        request_sha256=None,
        provider_operation_id_sha256=text_sha256("operation"),
        provider_thread_id_sha256=text_sha256("thread"),
        provider_root_thread_id_sha256=None,
        accepted_parent_checkpoint_id_sha256=None,
        candidate_checkpoint_id_sha256=None,
        model="gpt-5.6-sol",
        reasoning_effort="medium",
        fast_mode_enabled=False,
        packet_ready_unix_us=None,
        request_start_unix_us=10,
        first_reasoning_item_unix_us=12,
        first_structured_output_item_unix_us=18,
        final_evidence_result_unix_us=16,
        provider_completion_unix_us=25,
        python_parse_start_unix_us=None,
        python_parse_completion_unix_us=None,
        python_validation_start_unix_us=None,
        python_validation_completion_unix_us=None,
        usage_steps=(step,),
        cumulative_input_tokens=100,
        cumulative_cached_input_tokens=40,
        cumulative_uncached_input_tokens=60,
        cumulative_output_tokens=12,
        cumulative_reasoning_tokens=8,
        tool_timings=tuple(
            CodexToolTimingV1(
                sequence=sequence,
                server_name=server_name,
                tool_name=tool_name,
                started_at_unix_us=None,
                completed_at_unix_us=None,
                status=status,
            )
            for sequence, (server_name, tool_name), status in zip(
                sequences,
                observations,
                statuses,
                strict=True,
            )
        ),
        tool_call_count=len(observations),
        provider_attempt_count=1,
        finish_status="completed",
        transport_error=None,
        unsupported_fields=(
            "request_sha256",
            "packet_ready_unix_us",
            "provider_root_thread_id_sha256",
            "accepted_parent_checkpoint_id_sha256",
            "candidate_checkpoint_id_sha256",
            "python_parse_start_unix_us",
            "python_parse_completion_unix_us",
            "python_validation_start_unix_us",
            "python_validation_completion_unix_us",
        ),
        retains_prompt=False,
        retains_output=False,
        retains_reasoning=False,
        retains_tool_arguments=False,
    )


class ProviderQualificationTests(unittest.TestCase):
    def test_codex_worker_protocol_escapes_non_ascii_without_semantic_loss(self) -> None:
        payload = {"prompt": "Sakura—doorway", "nested": {"voice": "café"}}
        encoded = _codex_transport_json(payload)
        self.assertTrue(encoded.isascii())
        self.assertEqual(json.loads(encoded), payload)

    def test_codex_worker_requires_returned_turn_pre_registration_evidence(
        self,
    ) -> None:
        with self.assertRaisesRegex(
            ContractValidationError,
            "compatibility activation evidence",
        ):
            CodexWorkerResult(
                output_text='{"probe":"ok"}',
                provider_request_id="private-turn",
                returned_model="gpt-5.6-sol",
                duration_ms=1,
                input_tokens=1,
                cached_input_tokens=0,
                output_tokens=1,
                reasoning_output_tokens=0,
                transport_version="0.144.4",
            )

    def test_codex_worker_failure_after_dispatch_counts_the_unreceipted_call(self) -> None:
        class FailedProcess:
            pid = 43211
            returncode = 1

            def communicate(self, payload, *, timeout):
                request = json.loads(payload)
                Path(request["progress_path"]).write_text(
                    json.dumps(
                        {
                            "schema_version": "cera.codex_worker_progress.v1",
                            "stage": "thread_read",
                        }
                    ),
                    encoding="utf-8",
                )
                return "", "codex qualification worker failed: test"

        with tempfile.TemporaryDirectory() as directory, patch(
            "cera.providers.codex.subprocess.Popen",
            return_value=FailedProcess(),
        ):
            with self.assertRaises(ProviderTransportError) as caught:
                OfflineSubprocessCodexRunner().run(
                    route=codex_reasoner_candidate(),
                    prompt="Return a bounded test object.",
                    output_schema=codex_transport_probe_output_schema(),
                    workspace=Path(directory),
                    mcp_binding=None,
                )
        self.assertEqual(caught.exception.external_provider_calls_observed, 1)
        self.assertEqual(
            caught.exception.safe_diagnostics,
            ("transport:worker_failure", "worker_stage:thread_read"),
        )

    def test_codex_subprocess_forwards_only_the_supported_fast_tier(self) -> None:
        captured = {}

        class FailedBeforeDispatchProcess:
            pid = 43212
            returncode = 1

            def communicate(self, payload, *, timeout):
                request = json.loads(payload)
                captured.update(request)
                return "", "codex qualification worker failed: test"

        with tempfile.TemporaryDirectory() as directory, patch(
            "cera.providers.codex.subprocess.Popen",
            return_value=FailedBeforeDispatchProcess(),
        ):
            with self.assertRaises(ProviderTransportError):
                OfflineSubprocessCodexRunner(service_tier="priority").run(
                    route=codex_reasoner_candidate(
                        model="gpt-5.6-luna",
                        effort="max",
                    ),
                    prompt="Return a bounded test object.",
                    output_schema=codex_transport_probe_output_schema(),
                    workspace=Path(directory),
                    mcp_binding=None,
                )
        self.assertEqual(captured["service_tier"], "priority")
        with self.assertRaises(ContractValidationError):
            _SubprocessCodexRunner(service_tier="unsupported")

    def test_stored_codex_runner_binds_exact_thread_and_worker_module(self) -> None:
        captured = {}

        class FailedBeforeDispatchProcess:
            pid = 43213
            returncode = 1

            def __init__(self, command) -> None:
                captured["command"] = tuple(command)

            def communicate(self, payload, *, timeout):
                captured["payload"] = json.loads(payload)
                return "", "codex qualification worker failed: test"

        def create_process(command, **kwargs):
            return FailedBeforeDispatchProcess(command)

        with tempfile.TemporaryDirectory() as directory, patch(
            "cera.providers.codex.subprocess.Popen",
            side_effect=create_process,
        ):
            with self.assertRaises(ProviderTransportError) as caught:
                OfflineStoredCodexThreadRunner("stored-thread-51").run(
                    route=codex_reasoner_candidate(),
                    prompt="Return a bounded test object.",
                    output_schema=codex_transport_probe_output_schema(),
                    workspace=Path(directory),
                    mcp_binding=None,
                )
        self.assertEqual(caught.exception.external_provider_calls_observed, 0)
        self.assertEqual(
            captured["command"][-1], "cera.providers.codex_stored_turn_worker"
        )
        self.assertEqual(
            captured["payload"]["provider_thread_id"], "stored-thread-51"
        )

    def test_persistent_codex_runner_reuses_process_but_not_request_workspace(self) -> None:
        worker_result = {
            "output_text": '{"probe":"ok"}',
            "provider_request_id": "private-turn",
            "returned_model": "gpt-5.6-sol",
            "duration_ms": 0,
            "input_tokens": 10,
            "cached_input_tokens": 0,
            "output_tokens": 3,
            "reasoning_output_tokens": 1,
            "transport_version": "0.144.4",
            "mcp_server_names": [],
            "mcp_tool_names": [],
            "mcp_tool_call_count": 0,
            "mcp_failed_tool_call_count": 0,
            "pre_registered_turn_count": 1,
        }
        lines = [
            json.dumps({"ok": True, "result": worker_result}) + "\n",
            json.dumps({"ok": True, "result": worker_result}) + "\n",
        ]

        class InputPipe:
            def __init__(self) -> None:
                self.values: list[str] = []

            def write(self, value: str) -> None:
                self.values.append(value)

            def flush(self) -> None:
                return None

            def close(self) -> None:
                return None

        class OutputPipe:
            def readline(self) -> str:
                return lines.pop(0)

        class Process:
            pid = 12345
            returncode = None

            def __init__(self) -> None:
                self.stdin = InputPipe()
                self.stdout = OutputPipe()
                self.stderr = OutputPipe()

            def poll(self):
                return None

            def wait(self, *, timeout):
                return 0

        process = Process()
        runner = OfflinePersistentNoMcpCodexRunner()
        schema = codex_transport_probe_output_schema()
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second, tempfile.TemporaryDirectory() as third, patch(
            "cera.providers.codex.subprocess.Popen", return_value=process
        ) as popen, patch(
            "cera.providers.codex._terminate_codex_worker_tree",
            return_value=True,
        ):
            first_result = runner.run(
                route=codex_reasoner_candidate(),
                prompt="first request",
                output_schema=schema,
                workspace=Path(first),
                mcp_binding=None,
            )
            second_result = runner.run(
                route=codex_reasoner_candidate(),
                prompt="second request",
                output_schema=schema,
                workspace=Path(second),
                mcp_binding=None,
            )
            with self.assertRaises(ProviderTransportError) as cross_role:
                runner.run(
                    route=codex_realization_verifier_candidate(),
                    prompt="cross-role request",
                    output_schema=schema,
                    workspace=Path(third),
                    mcp_binding=None,
                )
            self.assertFalse(
                (Path(third) / ".cera_codex_worker_progress.json").exists()
            )
            runner.close()
        self.assertEqual(popen.call_count, 1)
        self.assertEqual(runner.process_launch_count, 1)
        self.assertEqual(runner.request_submission_count, 2)
        self.assertEqual(
            cross_role.exception.code,
            ErrorCode.REASONER_CONTRACT_INVALID,
        )
        self.assertEqual(first_result.output_text, '{"probe":"ok"}')
        self.assertEqual(second_result.output_text, '{"probe":"ok"}')
        requests = [json.loads(value) for value in process.stdin.values]
        self.assertEqual(len(requests), 2)
        self.assertNotEqual(requests[0]["workspace"], requests[1]["workspace"])
        self.assertEqual(
            {value["role"] for value in requests},
            {"scene_reasoner"},
        )
        self.assertTrue(all(value["mcp_binding"] is None for value in requests))

    def test_persistent_codex_runner_rotates_before_third_dispatch(self) -> None:
        worker_result = {
            "output_text": '{"probe":"ok"}',
            "provider_request_id": "private-turn",
            "returned_model": "gpt-5.6-sol",
            "duration_ms": 0,
            "input_tokens": 10,
            "cached_input_tokens": 0,
            "output_tokens": 3,
            "reasoning_output_tokens": 1,
            "transport_version": "0.144.4",
            "mcp_server_names": [],
            "mcp_tool_names": [],
            "mcp_tool_call_count": 0,
            "mcp_failed_tool_call_count": 0,
            "pre_registered_turn_count": 1,
        }

        class InputPipe:
            def __init__(self) -> None:
                self.values: list[str] = []
                self.closed = False

            def write(self, value: str) -> None:
                self.values.append(value)

            def flush(self) -> None:
                return None

            def close(self) -> None:
                self.closed = True

        class OutputPipe:
            def __init__(self, count: int) -> None:
                self.lines = [
                    json.dumps({"ok": True, "result": worker_result}) + "\n"
                    for _ in range(count)
                ]

            def readline(self) -> str:
                return self.lines.pop(0)

        class Process:
            returncode = None

            def __init__(self, pid: int, count: int) -> None:
                self.pid = pid
                self.stdin = InputPipe()
                self.stdout = OutputPipe(count)

            def poll(self):
                return None

            def wait(self, *, timeout):
                return 0

        processes = (Process(12345, 2), Process(12346, 1))
        runner = OfflinePersistentNoMcpCodexRunner()
        schema = codex_transport_probe_output_schema()
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second, tempfile.TemporaryDirectory() as third, patch(
            "cera.providers.codex.subprocess.Popen",
            side_effect=processes,
        ) as popen, patch(
            "cera.providers.codex._terminate_codex_worker_tree",
            return_value=True,
        ) as terminate:
            for prompt, directory in (
                ("first", first),
                ("second", second),
                ("third", third),
            ):
                runner.run(
                    route=codex_reasoner_candidate(),
                    prompt=prompt,
                    output_schema=schema,
                    workspace=Path(directory),
                    mcp_binding=None,
                )
            runner.close()
        self.assertEqual(popen.call_count, 2)
        self.assertEqual(runner.process_launch_count, 2)
        self.assertEqual(runner.request_submission_count, 3)
        self.assertEqual(len(processes[0].stdin.values), 2)
        self.assertEqual(len(processes[1].stdin.values), 1)
        self.assertEqual(
            [call.args[0] for call in terminate.call_args_list],
            [processes[0], processes[1]],
        )
        self.assertTrue(
            all(call.kwargs["stderr"] is subprocess.DEVNULL for call in popen.mock_calls)
        )

    def test_persistent_codex_runner_reports_non_ascii_pipe_corruption(self) -> None:
        class InputPipe:
            def write(self, _value: str) -> None:
                return None

            def flush(self) -> None:
                return None

        class OutputPipe:
            def readline(self) -> str:
                raise UnicodeDecodeError("ascii", b"\x97", 0, 1, "non-ASCII byte")

        class Process:
            pid = 12348
            returncode = None
            stdin = InputPipe()
            stdout = OutputPipe()

            def poll(self):
                return None

        runner = OfflinePersistentNoMcpCodexRunner()
        with tempfile.TemporaryDirectory() as directory, patch(
            "cera.providers.codex.subprocess.Popen", return_value=Process()
        ), patch(
            "cera.providers.codex._terminate_codex_worker_tree", return_value=True
        ):
            with self.assertRaises(ProviderTransportError) as caught:
                runner.run(
                    route=codex_reasoner_candidate(),
                    prompt="request",
                    output_schema=codex_transport_probe_output_schema(),
                    workspace=Path(directory),
                    mcp_binding=None,
                )
        self.assertEqual(caught.exception.code, ErrorCode.REASONER_CONTRACT_INVALID)
        self.assertEqual(
            caught.exception.safe_diagnostics,
            (
                "transport:worker_pipe_decode_failed",
                "worker_stage:worker_launch",
                "transport_mode:persistent_no_mcp",
            ),
        )
    def test_persistent_codex_runner_fails_before_third_dispatch_if_tree_cleanup_fails(
        self,
    ) -> None:
        worker_result = {
            "output_text": '{"probe":"ok"}',
            "provider_request_id": "private-turn",
            "returned_model": "gpt-5.6-sol",
            "duration_ms": 0,
            "input_tokens": 10,
            "cached_input_tokens": 0,
            "output_tokens": 3,
            "reasoning_output_tokens": 1,
            "transport_version": "0.144.4",
            "mcp_server_names": [],
            "mcp_tool_names": [],
            "mcp_tool_call_count": 0,
            "mcp_failed_tool_call_count": 0,
            "pre_registered_turn_count": 1,
        }
        lines = [
            json.dumps({"ok": True, "result": worker_result}) + "\n",
            json.dumps({"ok": True, "result": worker_result}) + "\n",
        ]

        class InputPipe:
            def write(self, _value: str) -> None:
                return None

            def flush(self) -> None:
                return None

        class OutputPipe:
            def readline(self) -> str:
                return lines.pop(0)

        class Process:
            pid = 12347
            stdin = InputPipe()
            stdout = OutputPipe()

            def poll(self):
                return None

        process = Process()
        runner = OfflinePersistentNoMcpCodexRunner()
        schema = codex_transport_probe_output_schema()
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second, tempfile.TemporaryDirectory() as third, patch(
            "cera.providers.codex.subprocess.Popen",
            return_value=process,
        ) as popen, patch(
            "cera.providers.codex._terminate_codex_worker_tree",
            return_value=False,
        ):
            for prompt, directory in (("first", first), ("second", second)):
                runner.run(
                    route=codex_reasoner_candidate(),
                    prompt=prompt,
                    output_schema=schema,
                    workspace=Path(directory),
                    mcp_binding=None,
                )
            with self.assertRaises(ProviderTransportError) as caught:
                runner.run(
                    route=codex_reasoner_candidate(),
                    prompt="third",
                    output_schema=schema,
                    workspace=Path(third),
                    mcp_binding=None,
                )
        self.assertEqual(popen.call_count, 1)
        self.assertEqual(runner.request_submission_count, 2)
        self.assertEqual(caught.exception.external_provider_calls_observed, 0)
        self.assertEqual(
            caught.exception.safe_diagnostics,
            (
                "transport:process_epoch_cleanup_failed",
                "transport_mode:persistent_no_mcp",
            ),
        )

    def test_persistent_codex_runner_rejects_request_bound_mcp(self) -> None:
        runner = OfflinePersistentNoMcpCodexRunner()
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ProviderTransportError):
                runner.run(
                    route=codex_reasoner_candidate(),
                    prompt="request",
                    output_schema=codex_transport_probe_output_schema(),
                    workspace=Path(directory),
                    mcp_binding=codex_mcp_binding(),
                )

    def test_codex_timeout_terminates_worker_tree_without_retry(self) -> None:
        class TimedOutProcess:
            pid = 43210
            returncode = None

            def communicate(self, payload, *, timeout):
                request = json.loads(payload)
                Path(request["progress_path"]).write_text(
                    json.dumps(
                        {
                            "schema_version": "cera.codex_worker_progress.v1",
                            "stage": "thread_run",
                        }
                    ),
                    encoding="utf-8",
                )
                raise subprocess.TimeoutExpired("codex-worker", timeout)

        process = TimedOutProcess()
        with tempfile.TemporaryDirectory() as directory, patch(
            "cera.providers.codex.subprocess.Popen",
            return_value=process,
        ) as popen, patch(
            "cera.providers.codex._terminate_codex_worker_tree"
        ) as terminate:
            with self.assertRaises(ProviderTransportError) as caught:
                OfflineSubprocessCodexRunner().run(
                    route=codex_reasoner_candidate(),
                    prompt="Return a bounded test object.",
                    output_schema=codex_transport_probe_output_schema(),
                    workspace=Path(directory),
                    mcp_binding=None,
                )
        self.assertEqual(caught.exception.code, ErrorCode.REASONER_UNAVAILABLE)
        self.assertEqual(
            caught.exception.safe_diagnostics,
            ("transport:timeout", "worker_stage:thread_run"),
        )
        self.assertEqual(caught.exception.external_provider_calls_observed, 1)
        self.assertEqual(popen.call_count, 1)
        terminate.assert_called_once_with(process)

    def test_routes_are_live_evaluation_identities_without_promotion(self) -> None:
        route = codex_route()
        identity = route.evaluation_identity()
        self.assertEqual(identity.model_name, "gpt-5.6-sol")
        self.assertEqual(identity.configuration_sha256, route.route_sha256)
        reasoner = codex_reasoner_candidate()
        self.assertEqual(reasoner.model_name, "gpt-5.6-sol")
        self.assertEqual(reasoner.prompt_version, ACTIVE_REASONER_PROMPT_VERSION)
        verifier = codex_realization_verifier_candidate()
        self.assertEqual(verifier.model_name, "gpt-5.6-sol")
        self.assertEqual(verifier.role, EvaluationRole.SCENE_REALIZATION_VERIFIER)
        self.assertEqual(verifier.prompt_version, ACTIVE_VERIFIER_PROMPT_VERSION)
        composer = deepseek_composer_candidate()
        self.assertEqual(composer.model_name, "deepseek-v4-flash")
        self.assertEqual(composer.prompt_version, ACTIVE_COMPOSER_PROMPT_VERSION)
        self.assertEqual(
            deepseek_composer_candidate(model="deepseek-v4-pro").model_name,
            "deepseek-v4-pro",
        )

    def test_routes_reject_retry_fallback_production_and_legacy_model(self) -> None:
        route = deepseek_route()
        payload = {field.name: getattr(route, field.name) for field in fields(route)}
        with self.assertRaises(ContractValidationError):
            LiveProviderRoute(**{**payload, "automatic_retry_count": 1})
        with self.assertRaises(ContractValidationError):
            LiveProviderRoute(**{**payload, "fallback_enabled": True})
        with self.assertRaises(ContractValidationError):
            LiveProviderRoute(**{**payload, "production_enabled": True})
        with self.assertRaises(ContractValidationError):
            LiveProviderRoute(**{**payload, "model_name": "deepseek-chat"})

    def test_deepseek_pricing_is_rounded_up_and_dated(self) -> None:
        price = deepseek_pricing()
        self.assertEqual(
            price.estimate_cost_microusd(
                cache_hit_input_tokens=0,
                cache_miss_input_tokens=27,
                output_tokens=6,
            ),
            17,
        )

    def test_deepseek_one_shot_success_records_safe_receipt(self) -> None:
        opener = RecordingOpener(
            {
                "id": "provider_private_id",
                "model": "deepseek-v4-pro",
                "system_fingerprint": "private_fingerprint",
                "choices": [
                    {
                        "message": {"content": '{"story":"candidate"}'},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 27,
                    "completion_tokens": 6,
                    "completion_tokens_details": {"reasoning_tokens": 2},
                    "prompt_cache_hit_tokens": 0,
                    "prompt_cache_miss_tokens": 27,
                },
            }
        )
        transport = OfflineDeepSeekChatTransport(
            deepseek_route(),
            opener=opener,
            environment={"DEEPSEEK_API_KEY": "dummy"},
            external_provider_boundary=False,
        )
        result = transport.invoke(
            (
                DeepSeekMessage("system", "Return JSON."),
                DeepSeekMessage("user", "Compose the candidate."),
            ),
            output_mode=ProviderOutputMode.JSON_OBJECT,
        )
        self.assertEqual(result.parsed_json, {"story": "candidate"})
        self.assertEqual(len(opener.calls), 1)
        request, timeout = opener.calls[0]
        self.assertEqual(timeout, 60)
        self.assertEqual(request.get_header("Authorization"), "Bearer dummy")
        self.assertNotIn("dummy", json.dumps(result.receipt, default=str))
        self.assertEqual(result.receipt.external_provider_calls, 1)
        self.assertEqual(result.receipt.cost_microusd, 17)
        self.assertEqual(result.receipt.reasoning_output_tokens, 2)
        self.assertFalse(result.receipt.prompt_included)
        self.assertFalse(result.receipt.story_prose_included)

    def test_deepseek_non_stop_response_preserves_safe_finish_and_usage(self) -> None:
        partial = '{"story":"private partial candidate'
        opener = RecordingOpener(
            {
                "id": "provider_private_incomplete_id",
                "model": "deepseek-v4-pro",
                "system_fingerprint": "private_fingerprint",
                "choices": [
                    {
                        "message": {"content": partial},
                        "finish_reason": "length",
                    }
                ],
                "usage": {
                    "prompt_tokens": 27,
                    "completion_tokens": 2048,
                    "completion_tokens_details": {"reasoning_tokens": 1900},
                    "prompt_cache_hit_tokens": 7,
                    "prompt_cache_miss_tokens": 20,
                },
            }
        )
        transport = OfflineDeepSeekChatTransport(
            deepseek_route(),
            opener=opener,
            environment={"DEEPSEEK_API_KEY": "dummy"},
            external_provider_boundary=False,
        )
        with self.assertRaises(ProviderTransportError) as caught:
            transport.invoke(
                (DeepSeekMessage("user", "Compose the candidate."),),
                output_mode=ProviderOutputMode.JSON_OBJECT,
            )
        failure = caught.exception
        self.assertEqual(failure.external_provider_calls_observed, 1)
        self.assertEqual(
            failure.safe_diagnostics,
            ("DEEPSEEK_FINISH_REASON_LENGTH",),
        )
        receipt = failure.provider_call_receipt
        self.assertIsInstance(receipt, ProviderFailureCallReceipt)
        self.assertIs(receipt.finish_reason, ProviderFinishReason.LENGTH)
        self.assertIs(
            receipt.failure_kind,
            ProviderResponseFailureKind.NON_STOP_FINISH,
        )
        self.assertEqual(receipt.call_receipt.input_tokens, 27)
        self.assertEqual(receipt.call_receipt.cached_input_tokens, 7)
        self.assertEqual(receipt.call_receipt.output_tokens, 2048)
        self.assertEqual(receipt.call_receipt.reasoning_output_tokens, 1900)
        payload = to_primitive(receipt)
        serialized = json.dumps(payload)
        self.assertNotIn(partial, serialized)
        self.assertEqual(
            build_schema_registry().decode(payload),
            receipt,
        )
        safe = PrivacySafeReceiptPayload.from_receipt(
            "provider_failure_call_receipt",
            receipt.provider_receipt_id,
            receipt.receipt_sha256,
            receipt,
        )
        self.assertEqual(
            safe.receipt_schema_version,
            ProviderFailureCallReceipt.SCHEMA_VERSION,
        )
        self.assertNotIn(partial, safe.payload_json)

    def test_deepseek_all_supported_non_stop_reasons_fail_closed(self) -> None:
        for reason in (
            ProviderFinishReason.LENGTH,
            ProviderFinishReason.CONTENT_FILTER,
            ProviderFinishReason.TOOL_CALLS,
            ProviderFinishReason.INSUFFICIENT_SYSTEM_RESOURCE,
        ):
            with self.subTest(reason=reason.value):
                opener = RecordingOpener(
                    {
                        "id": f"provider_{reason.value}",
                        "model": "deepseek-v4-pro",
                        "choices": [
                            {
                                "message": {"content": "private partial"},
                                "finish_reason": reason.value,
                            }
                        ],
                        "usage": {
                            "prompt_tokens": 12,
                            "completion_tokens": 34,
                            "prompt_cache_hit_tokens": 2,
                            "prompt_cache_miss_tokens": 10,
                        },
                    }
                )
                transport = OfflineDeepSeekChatTransport(
                    deepseek_route(),
                    opener=opener,
                    environment={"DEEPSEEK_API_KEY": "dummy"},
                    external_provider_boundary=False,
                )
                with self.assertRaises(ProviderTransportError) as caught:
                    transport.invoke((DeepSeekMessage("user", "Probe."),))
                failure = caught.exception
                self.assertIsInstance(
                    failure.provider_call_receipt,
                    ProviderFailureCallReceipt,
                )
                self.assertIs(
                    failure.provider_call_receipt.finish_reason,
                    reason,
                )
                self.assertEqual(failure.external_provider_calls_observed, 1)
                self.assertNotIn(
                    "private partial",
                    canonical_json(to_primitive(failure.provider_call_receipt)),
                )

    def test_deepseek_missing_key_and_model_drift_fail_without_retry(self) -> None:
        missing = OfflineDeepSeekChatTransport(
            deepseek_route(),
            opener=RecordingOpener({}),
            environment={},
            external_provider_boundary=False,
        )
        with self.assertRaises(ProviderTransportError) as caught:
            missing.invoke((DeepSeekMessage("user", "Probe."),))
        self.assertEqual(caught.exception.code, ErrorCode.COMPOSER_UNAVAILABLE)

        opener = RecordingOpener(
            {
                "id": "id",
                "model": "deepseek-v4-flash",
                "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                "usage": {},
            }
        )
        drift = OfflineDeepSeekChatTransport(
            deepseek_route(),
            opener=opener,
            environment={"DEEPSEEK_API_KEY": "dummy"},
            external_provider_boundary=False,
        )
        with self.assertRaises(ProviderTransportError) as caught:
            drift.invoke((DeepSeekMessage("user", "Probe."),))
        self.assertEqual(caught.exception.code, ErrorCode.COMPOSER_CONTRACT_INVALID)
        self.assertEqual(len(opener.calls), 1)

    def test_deepseek_network_failure_is_stable_and_unretried(self) -> None:
        calls = 0

        def fail(_request, *, timeout):
            nonlocal calls
            calls += 1
            raise urllib.error.URLError("offline")

        transport = OfflineDeepSeekChatTransport(
            deepseek_route(),
            opener=fail,
            environment={"DEEPSEEK_API_KEY": "dummy"},
            external_provider_boundary=False,
        )
        with self.assertRaises(ProviderTransportError) as caught:
            transport.invoke((DeepSeekMessage("user", "Probe."),))
        self.assertEqual(caught.exception.code, ErrorCode.COMPOSER_UNAVAILABLE)
        self.assertEqual(calls, 1)

    def test_codex_schema_call_records_quota_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runner = StaticCodexRunner()
            transport = CodexSDKTransport(
                codex_route(), workspace=Path(temporary), runner=runner
            )
            result = transport.invoke(
                "Return the probe object.",
                output_schema=codex_transport_probe_output_schema(),
            )
        self.assertEqual(result.parsed_json, {"probe": "ok"})
        self.assertEqual(runner.calls, 1)
        self.assertTrue(result.receipt.quota_metered)
        self.assertEqual(result.receipt.cost_microusd, 0)
        self.assertEqual(result.receipt.reasoning_output_tokens, 8)
        self.assertNotIn("turn_private_id", json.dumps(result.receipt, default=str))

    def test_codex_model_drift_and_runner_failure_are_unretried(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runner = StaticCodexRunner(returned_model="gpt-5.6-terra")
            transport = CodexSDKTransport(
                codex_route(), workspace=Path(temporary), runner=runner
            )
            with self.assertRaises(ProviderTransportError) as caught:
                transport.invoke(
                    "Probe.", output_schema=codex_transport_probe_output_schema()
                )
        self.assertEqual(caught.exception.code, ErrorCode.REASONER_CONTRACT_INVALID)
        self.assertEqual(caught.exception.external_provider_calls_observed, 1)
        self.assertEqual(runner.calls, 1)

        class FailingRunner:
            external_provider_boundary = False

            def __init__(self) -> None:
                self.calls = 0

            def run(self, **_kwargs):
                self.calls += 1
                raise RuntimeError("private failure")

        with tempfile.TemporaryDirectory() as temporary:
            failing = FailingRunner()
            transport = CodexSDKTransport(
                codex_route(), workspace=Path(temporary), runner=failing
            )
            with self.assertRaises(ProviderTransportError) as caught:
                transport.invoke(
                    "Probe.", output_schema=codex_transport_probe_output_schema()
                )
        self.assertEqual(caught.exception.code, ErrorCode.REASONER_UNAVAILABLE)
        self.assertEqual(failing.calls, 1)

    def test_codex_worker_compatibility_evidence_must_match_route(self) -> None:
        class MismatchedCompatibilityRunner(StaticCodexRunner):
            def run(self, **kwargs):
                worker = super().run(**kwargs)
                payload = {
                    field.name: getattr(worker, field.name)
                    for field in fields(CodexWorkerResult)
                }
                payload["transport_compatibility_id"] = (
                    "cera.codex_sdk_early_completion_buffer.unqualified"
                )
                return SimpleNamespace(**payload)

        with tempfile.TemporaryDirectory() as temporary:
            runner = MismatchedCompatibilityRunner()
            transport = CodexSDKTransport(
                codex_route(), workspace=Path(temporary), runner=runner
            )
            with self.assertRaises(ProviderTransportError) as caught:
                transport.invoke(
                    "Probe.", output_schema=codex_transport_probe_output_schema()
                )
        self.assertEqual(
            caught.exception.code,
            ErrorCode.REASONER_CONTRACT_INVALID,
        )
        self.assertEqual(
            caught.exception.safe_diagnostics,
            ("transport:compatibility_activation_mismatch",),
        )
        self.assertEqual(caught.exception.external_provider_calls_observed, 1)
        self.assertEqual(runner.calls, 1)

    def test_codex_workspace_must_be_isolated(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)
            (path / "unexpected.txt").write_text("not allowed", encoding="utf-8")
            with self.assertRaises(ContractValidationError):
                CodexSDKTransport(codex_route(), workspace=path, runner=StaticCodexRunner())

    def test_codex_mcp_observation_policy_is_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(
                ContractValidationError,
                "MCP observation policy is unqualified",
            ):
                CodexSDKTransport(
                    codex_route(),
                    workspace=Path(temporary),
                    runner=StaticCodexRunner(),
                    mcp_observation_policy="cera.codex_mcp_observation.unknown.v1",
                )

    def test_codex_mcp_observation_policy_rejects_post_construction_tamper(
        self,
    ) -> None:
        observations = (
            ("node_repl", "js"),
            ("cera_request_evidence", "cera_get_turn_snapshot"),
        )

        def transport_at(path: Path) -> CodexSDKTransport:
            return CodexSDKTransport(
                codex_route(),
                workspace=path,
                runner=StaticCodexRunner(
                    mcp_server_names=tuple(value[0] for value in observations),
                    mcp_tool_names=tuple(value[1] for value in observations),
                    operation_telemetry=codex_operation_telemetry(
                        observations,
                        ("completed", "completed"),
                    ),
                ),
            )

        with tempfile.TemporaryDirectory() as temporary:
            transport = transport_at(Path(temporary))
            self.assertEqual(
                transport.mcp_observation_policy,
                CODEX_MCP_OBSERVATION_POLICY_STRICT_V1,
            )
            with self.assertRaises(AttributeError):
                transport.mcp_observation_policy = "arbitrary"  # type: ignore[misc]
            with self.assertRaises(ProviderTransportError) as still_strict:
                transport.invoke(
                    "Probe.",
                    output_schema=codex_transport_probe_output_schema(),
                    mcp_binding=codex_mcp_binding(maximum_tool_calls=2),
                )
        self.assertEqual(still_strict.exception.code, ErrorCode.REASONER_CONTRACT_INVALID)
        self.assertIsNone(still_strict.exception.retryable_failure_category)
        self.assertIsNotNone(still_strict.exception.provider_call_receipt)

        with tempfile.TemporaryDirectory() as temporary:
            transport = transport_at(Path(temporary))
            transport._mcp_observation_policy = "arbitrary"
            with self.assertRaises(ProviderTransportError) as private_tamper:
                transport.invoke(
                    "Probe.",
                    output_schema=codex_transport_probe_output_schema(),
                    mcp_binding=codex_mcp_binding(maximum_tool_calls=2),
                )
        failure = private_tamper.exception
        self.assertEqual(failure.code, ErrorCode.REASONER_CONTRACT_INVALID)
        self.assertEqual(failure.safe_diagnostics, ("mcp:observation_policy_invalid",))
        self.assertIsNone(failure.retryable_failure_category)
        self.assertEqual(failure.external_provider_calls_observed, 1)
        self.assertIsNotNone(failure.provider_call_receipt)
        self.assertEqual(failure.mcp_tool_call_count, 2)
        self.assertEqual(failure.mcp_failed_tool_call_count, 0)

    def test_codex_mcp_binding_is_loopback_allow_listed_and_secret_safe(self) -> None:
        binding = codex_mcp_binding()
        self.assertNotIn("qualification-secret", repr(binding))
        self.assertNotIn("qualification-secret", json.dumps(binding.public_descriptor))
        self.assertNotIn("url", binding.public_descriptor)
        with self.assertRaises(ContractValidationError):
            CodexMcpRuntimeBinding(
                server_name=binding.server_name,
                url="https://example.com/mcp",
                bearer_token_environment_variable=binding.bearer_token_environment_variable,
                bearer_token="secret",
                enabled_tools=binding.enabled_tools,
                binding_sha256=binding.binding_sha256,
            )
        with self.assertRaises(ContractValidationError):
            codex_mcp_binding(minimum_tool_calls=2, maximum_tool_calls=1)
        with self.assertRaises(ContractValidationError):
            CodexMcpRuntimeBinding(
                server_name=binding.server_name,
                url=binding.url,
                bearer_token_environment_variable="UNAPPROVED_TOKEN",
                bearer_token="secret",
                enabled_tools=binding.enabled_tools,
                binding_sha256=binding.binding_sha256,
            )

    def test_codex_mcp_observations_are_required_and_allow_listed(self) -> None:
        cases = (
            (
                StaticCodexRunner(),
                codex_mcp_binding(),
                ErrorCode.REASONER_CONTRACT_INVALID,
            ),
            (
                StaticCodexRunner(
                    mcp_server_names=("other_server",),
                    mcp_tool_names=("cera_get_turn_snapshot",),
                ),
                codex_mcp_binding(),
                ErrorCode.REASONER_CONTRACT_INVALID,
            ),
            (
                StaticCodexRunner(
                    mcp_server_names=("cera_request_evidence",),
                    mcp_tool_names=("unapproved_tool",),
                ),
                codex_mcp_binding(),
                ErrorCode.REASONER_CONTRACT_INVALID,
            ),
            (
                StaticCodexRunner(
                    mcp_server_names=("cera_request_evidence",),
                    mcp_tool_names=("cera_get_turn_snapshot",),
                    mcp_failed_tool_call_count=1,
                ),
                codex_mcp_binding(),
                ErrorCode.EVIDENCE_SERVICE_UNAVAILABLE,
            ),
            (
                StaticCodexRunner(
                    mcp_server_names=(
                        "cera_request_evidence",
                        "cera_request_evidence",
                    ),
                    mcp_tool_names=(
                        "cera_get_turn_snapshot",
                        "cera_get_turn_snapshot",
                    ),
                ),
                codex_mcp_binding(minimum_tool_calls=0, maximum_tool_calls=1),
                ErrorCode.EVIDENCE_LIMIT_EXCEEDED,
            ),
        )
        for runner, binding, expected in cases:
            with self.subTest(expected=expected, runner=runner):
                with tempfile.TemporaryDirectory() as temporary:
                    transport = CodexSDKTransport(
                        codex_route(), workspace=Path(temporary), runner=runner
                    )
                    with self.assertRaises(ProviderTransportError) as caught:
                        transport.invoke(
                            "Probe.",
                            output_schema=codex_transport_probe_output_schema(),
                            mcp_binding=binding,
                        )
                self.assertEqual(caught.exception.code, expected)
                self.assertEqual(runner.calls, 1)
                if expected is ErrorCode.REASONER_CONTRACT_INVALID:
                    self.assertIsNone(caught.exception.retryable_failure_category)
                    self.assertIsNotNone(caught.exception.provider_call_receipt)
                if expected is ErrorCode.EVIDENCE_SERVICE_UNAVAILABLE:
                    self.assertEqual(
                        caught.exception.mcp_tool_names,
                        ("cera_get_turn_snapshot",),
                    )
                    self.assertEqual(caught.exception.mcp_tool_call_count, 1)
                    self.assertEqual(caught.exception.mcp_failed_tool_call_count, 1)

        unbound_runner = StaticCodexRunner(
            mcp_server_names=("node_repl",),
            mcp_tool_names=("js",),
        )
        with tempfile.TemporaryDirectory() as temporary:
            transport = CodexSDKTransport(
                codex_route(), workspace=Path(temporary), runner=unbound_runner
            )
            with self.assertRaises(ProviderTransportError) as caught:
                transport.invoke(
                    "Probe.", output_schema=codex_transport_probe_output_schema()
                )
        self.assertEqual(caught.exception.code, ErrorCode.REASONER_CONTRACT_INVALID)
        self.assertIsNone(caught.exception.retryable_failure_category)

    def test_codex_code_mode_auxiliary_calls_project_to_cera_observations(self) -> None:
        observations = (
            ("node_repl", "js"),
            ("node_repl", "js"),
            ("cera_continuous_world", "get_turn_context"),
            ("cera_continuous_world", "search_evidence"),
            ("cera_continuous_world", "search_evidence"),
        )
        telemetry = codex_operation_telemetry(
            observations,
            ("completed",) * len(observations),
        )
        runner = StaticCodexRunner(
            mcp_server_names=tuple(value[0] for value in observations),
            mcp_tool_names=tuple(value[1] for value in observations),
            operation_telemetry=telemetry,
        )
        binding = codex_mcp_binding(
            server_name="cera_continuous_world",
            enabled_tools=("get_turn_context", "search_evidence"),
            minimum_tool_calls=3,
            maximum_tool_calls=5,
        )
        with tempfile.TemporaryDirectory() as temporary:
            result = CodexSDKTransport(
                codex_route(),
                workspace=Path(temporary),
                runner=runner,
                mcp_observation_policy=CODEX_MCP_OBSERVATION_POLICY_CODE_MODE_V1,
            ).invoke(
                "Probe.",
                output_schema=codex_transport_probe_output_schema(),
                mcp_binding=binding,
            )

        self.assertEqual(
            result.tool_server_names,
            ("cera_continuous_world",) * 3,
        )
        self.assertEqual(
            result.tool_names,
            ("get_turn_context", "search_evidence", "search_evidence"),
        )
        self.assertEqual(result.tool_call_count, 3)
        self.assertEqual(result.failed_tool_call_count, 0)
        self.assertIsNotNone(result.operation_telemetry)
        self.assertEqual(result.operation_telemetry.tool_call_count, 5)
        self.assertEqual(
            tuple(
                (value.server_name, value.tool_name)
                for value in result.operation_telemetry.tool_timings
            ),
            observations,
        )
        self.assertEqual(result.receipt.external_provider_calls, 1)
        self.assertFalse(result.receipt.retains_raw_source)
        self.assertFalse(result.receipt.retains_private_evidence)

    def test_codex_code_mode_auxiliary_failure_is_retryable_and_not_overwritten(
        self,
    ) -> None:
        observations = (
            ("node_repl", "js"),
            ("node_repl", "js"),
            ("cera_request_evidence", "cera_get_turn_snapshot"),
        )
        runner = StaticCodexRunner(
            mcp_server_names=tuple(value[0] for value in observations),
            mcp_tool_names=tuple(value[1] for value in observations),
            mcp_failed_tool_call_count=1,
            operation_telemetry=codex_operation_telemetry(
                observations,
                ("failed", "completed", "completed"),
            ),
        )
        with tempfile.TemporaryDirectory() as temporary:
            transport = CodexSDKTransport(
                codex_route(),
                workspace=Path(temporary),
                runner=runner,
                mcp_observation_policy=CODEX_MCP_OBSERVATION_POLICY_CODE_MODE_V1,
            )
            with self.assertRaises(ProviderTransportError) as caught:
                transport.invoke(
                    "Probe.",
                    output_schema=codex_transport_probe_output_schema(),
                    mcp_binding=codex_mcp_binding(maximum_tool_calls=3),
                )

        failure = caught.exception
        self.assertEqual(failure.code, ErrorCode.REASONER_CONTRACT_INVALID)
        self.assertEqual(
            failure.retryable_failure_category,
            ProviderRetryableFailureCategory.PROVIDER_OUTPUT_INVALID,
        )
        self.assertEqual(failure.safe_diagnostics, ("mcp:auxiliary_execution_failed",))
        self.assertEqual(failure.external_provider_calls_observed, 1)
        self.assertIsNotNone(failure.provider_call_receipt)
        self.assertEqual(failure.mcp_tool_call_count, 3)
        self.assertEqual(failure.mcp_failed_tool_call_count, 1)
        self.assertIsNotNone(failure.operation_telemetry)
        self.assertEqual(failure.operation_telemetry.tool_call_count, 3)

    def test_codex_code_mode_auxiliary_requires_aligned_terminal_telemetry(
        self,
    ) -> None:
        observations = (
            ("node_repl", "js"),
            ("cera_request_evidence", "cera_get_turn_snapshot"),
        )
        aligned_statuses = ("completed", "completed")
        mismatches = (
            ("missing", None, 0),
            (
                "sequence",
                codex_operation_telemetry(
                    observations,
                    aligned_statuses,
                    sequences=(2, 2),
                ),
                0,
            ),
            (
                "identity",
                codex_operation_telemetry(
                    (
                        ("node_repl", "js"),
                        ("cera_request_evidence", "other_tool"),
                    ),
                    aligned_statuses,
                ),
                0,
            ),
            (
                "status",
                codex_operation_telemetry(
                    observations,
                    ("completed", "inProgress"),
                ),
                0,
            ),
            (
                "failed_count",
                codex_operation_telemetry(
                    observations,
                    ("failed", "completed"),
                ),
                0,
            ),
        )
        for label, telemetry, failed_count in mismatches:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temporary:
                runner = StaticCodexRunner(
                    mcp_server_names=tuple(value[0] for value in observations),
                    mcp_tool_names=tuple(value[1] for value in observations),
                    mcp_failed_tool_call_count=failed_count,
                    operation_telemetry=telemetry,
                )
                transport = CodexSDKTransport(
                    codex_route(),
                    workspace=Path(temporary),
                    runner=runner,
                    mcp_observation_policy=(
                        CODEX_MCP_OBSERVATION_POLICY_CODE_MODE_V1
                    ),
                )
                with self.assertRaises(ProviderTransportError) as caught:
                    transport.invoke(
                        "Probe.",
                        output_schema=codex_transport_probe_output_schema(),
                        mcp_binding=codex_mcp_binding(maximum_tool_calls=2),
                    )

            failure = caught.exception
            self.assertEqual(failure.code, ErrorCode.REASONER_CONTRACT_INVALID)
            self.assertEqual(
                failure.retryable_failure_category,
                ProviderRetryableFailureCategory.PROVIDER_OUTPUT_INVALID,
            )
            self.assertEqual(
                failure.safe_diagnostics,
                ("mcp:auxiliary_observation_invalid",),
            )
            self.assertEqual(failure.external_provider_calls_observed, 1)
            self.assertIsNotNone(failure.provider_call_receipt)
            self.assertEqual(failure.mcp_tool_call_count, 2)

    def test_codex_code_mode_auxiliary_does_not_satisfy_or_expand_call_budget(
        self,
    ) -> None:
        auxiliary_only = (("node_repl", "js"),)
        runner = StaticCodexRunner(
            mcp_server_names=("node_repl",),
            mcp_tool_names=("js",),
            operation_telemetry=codex_operation_telemetry(
                auxiliary_only,
                ("completed",),
            ),
        )
        with tempfile.TemporaryDirectory() as temporary:
            transport = CodexSDKTransport(
                codex_route(),
                workspace=Path(temporary),
                runner=runner,
                mcp_observation_policy=CODEX_MCP_OBSERVATION_POLICY_CODE_MODE_V1,
            )
            with self.assertRaises(ProviderTransportError) as omitted:
                transport.invoke(
                    "Probe.",
                    output_schema=codex_transport_probe_output_schema(),
                    mcp_binding=codex_mcp_binding(maximum_tool_calls=1),
                )
        self.assertEqual(omitted.exception.code, ErrorCode.REASONER_CONTRACT_INVALID)
        self.assertEqual(
            omitted.exception.retryable_failure_category,
            ProviderRetryableFailureCategory.PROVIDER_OUTPUT_INVALID,
        )
        self.assertEqual(
            omitted.exception.safe_diagnostics,
            ("mcp:required_evidence_lookup_omitted",),
        )

        observations = (
            ("node_repl", "js"),
            ("cera_request_evidence", "cera_get_turn_snapshot"),
        )
        over_budget = StaticCodexRunner(
            mcp_server_names=tuple(value[0] for value in observations),
            mcp_tool_names=tuple(value[1] for value in observations),
            operation_telemetry=codex_operation_telemetry(
                observations,
                ("completed", "completed"),
            ),
        )
        with tempfile.TemporaryDirectory() as temporary:
            transport = CodexSDKTransport(
                codex_route(),
                workspace=Path(temporary),
                runner=over_budget,
                mcp_observation_policy=CODEX_MCP_OBSERVATION_POLICY_CODE_MODE_V1,
            )
            with self.assertRaises(ProviderTransportError) as exceeded:
                transport.invoke(
                    "Probe.",
                    output_schema=codex_transport_probe_output_schema(),
                    mcp_binding=codex_mcp_binding(
                        minimum_tool_calls=0,
                        maximum_tool_calls=1,
                    ),
                )
        self.assertEqual(exceeded.exception.code, ErrorCode.EVIDENCE_LIMIT_EXCEEDED)
        self.assertIsNone(exceeded.exception.retryable_failure_category)

    def test_codex_mcp_provider_request_failure_is_narrow_and_retryable(self) -> None:
        observations: list[
            tuple[tuple[str, ...], tuple[str, ...], int, int]
        ] = []

        def classified(
            server_names: tuple[str, ...],
            tool_names: tuple[str, ...],
            tool_call_count: int,
            failed_tool_call_count: int,
        ) -> bool:
            observations.append(
                (server_names, tool_names, tool_call_count, failed_tool_call_count)
            )
            return True

        classified_observations = (
            ("node_repl", "js"),
            ("cera_request_evidence", "cera_get_turn_snapshot"),
        )
        runner = StaticCodexRunner(
            mcp_server_names=tuple(value[0] for value in classified_observations),
            mcp_tool_names=tuple(value[1] for value in classified_observations),
            mcp_failed_tool_call_count=1,
            operation_telemetry=codex_operation_telemetry(
                classified_observations,
                ("completed", "failed"),
            ),
        )
        base_binding = codex_mcp_binding()

        def binding_with_classifier(classifier) -> CodexMcpRuntimeBinding:
            return CodexMcpRuntimeBinding(
                server_name=base_binding.server_name,
                url=base_binding.url,
                bearer_token_environment_variable=(
                    base_binding.bearer_token_environment_variable
                ),
                bearer_token="qualification-secret",
                enabled_tools=base_binding.enabled_tools,
                binding_sha256=base_binding.binding_sha256,
                startup_timeout_seconds=base_binding.startup_timeout_seconds,
                tool_timeout_seconds=base_binding.tool_timeout_seconds,
                minimum_tool_calls=base_binding.minimum_tool_calls,
                maximum_tool_calls=base_binding.maximum_tool_calls,
                failed_tool_call_provider_request_classifier=classifier,
            )

        binding = binding_with_classifier(classified)
        self.assertNotIn("classifier", repr(binding))
        self.assertNotIn("classifier", json.dumps(binding.public_descriptor))
        binding_without_classifier = codex_mcp_binding()
        self.assertEqual(binding, binding_without_classifier)
        self.assertEqual(hash(binding), hash(binding_without_classifier))
        with tempfile.TemporaryDirectory() as temporary:
            transport = CodexSDKTransport(
                codex_route(),
                workspace=Path(temporary),
                runner=runner,
                mcp_observation_policy=CODEX_MCP_OBSERVATION_POLICY_CODE_MODE_V1,
            )
            with self.assertRaises(ProviderTransportError) as caught:
                transport.invoke(
                    "Probe.",
                    output_schema=codex_transport_probe_output_schema(),
                    mcp_binding=binding,
                )

        failure = caught.exception
        self.assertEqual(failure.code, ErrorCode.REASONER_CONTRACT_INVALID)
        self.assertEqual(
            failure.retryable_failure_category,
            ProviderRetryableFailureCategory.PROVIDER_OUTPUT_INVALID,
        )
        self.assertEqual(failure.safe_diagnostics, ("mcp:provider_request_invalid",))
        self.assertEqual(failure.external_provider_calls_observed, 1)
        self.assertIsNotNone(failure.provider_call_receipt)
        self.assertEqual(failure.mcp_tool_call_count, 2)
        self.assertEqual(failure.mcp_failed_tool_call_count, 1)
        self.assertEqual(
            observations,
            [
                (
                    ("cera_request_evidence",),
                    ("cera_get_turn_snapshot",),
                    1,
                    1,
                )
            ],
        )

        for invalid_runner in (
            StaticCodexRunner(
                mcp_server_names=("other_server",),
                mcp_tool_names=("cera_get_turn_snapshot",),
                mcp_failed_tool_call_count=1,
            ),
            StaticCodexRunner(
                mcp_server_names=("cera_request_evidence",),
                mcp_tool_names=("unapproved_tool",),
                mcp_failed_tool_call_count=1,
            ),
        ):
            with self.subTest(invalid_runner=invalid_runner):
                with tempfile.TemporaryDirectory() as temporary:
                    transport = CodexSDKTransport(
                        codex_route(),
                        workspace=Path(temporary),
                        runner=invalid_runner,
                        mcp_observation_policy=(
                            CODEX_MCP_OBSERVATION_POLICY_CODE_MODE_V1
                        ),
                    )
                    with self.assertRaises(ProviderTransportError) as invalid:
                        transport.invoke(
                            "Probe.",
                            output_schema=codex_transport_probe_output_schema(),
                            mcp_binding=binding,
                        )
                self.assertEqual(
                    invalid.exception.code, ErrorCode.REASONER_CONTRACT_INVALID
                )
                self.assertEqual(
                    invalid.exception.retryable_failure_category,
                    ProviderRetryableFailureCategory.PROVIDER_OUTPUT_INVALID,
                )
                self.assertEqual(len(observations), 1)

        def classifier_error(
            _server_names: tuple[str, ...],
            _tool_names: tuple[str, ...],
            _tool_call_count: int,
            _failed_tool_call_count: int,
        ) -> bool:
            raise RuntimeError("private classifier failure")

        for classifier in (
            lambda _servers, _tools, _total, _failed: False,
            lambda _servers, _tools, _total, _failed: 1,
            classifier_error,
        ):
            with self.subTest(classifier=classifier):
                with tempfile.TemporaryDirectory() as temporary:
                    transport = CodexSDKTransport(
                        codex_route(),
                        workspace=Path(temporary),
                        runner=runner,
                        mcp_observation_policy=(
                            CODEX_MCP_OBSERVATION_POLICY_CODE_MODE_V1
                        ),
                    )
                    with self.assertRaises(ProviderTransportError) as fallback:
                        transport.invoke(
                            "Probe.",
                            output_schema=codex_transport_probe_output_schema(),
                            mcp_binding=binding_with_classifier(classifier),
                        )
                self.assertEqual(
                    fallback.exception.code, ErrorCode.EVIDENCE_SERVICE_UNAVAILABLE
                )
                self.assertIsNone(fallback.exception.retryable_failure_category)
                self.assertNotIn("private classifier failure", str(fallback.exception))

    def test_codex_enforces_output_token_ceiling(self) -> None:
        runner = StaticCodexRunner(output_tokens=codex_route().maximum_output_tokens + 1)
        with tempfile.TemporaryDirectory() as temporary:
            transport = CodexSDKTransport(
                codex_route(), workspace=Path(temporary), runner=runner
            )
            with self.assertRaises(ProviderTransportError) as caught:
                transport.invoke(
                    "Probe.", output_schema=codex_transport_probe_output_schema()
                )
        self.assertEqual(caught.exception.code, ErrorCode.PROVIDER_BUDGET_EXCEEDED)
        self.assertEqual(caught.exception.external_provider_calls_observed, 1)
        self.assertEqual(runner.calls, 1)

    def test_provider_schemas_are_registered(self) -> None:
        versions = build_schema_registry().versions
        self.assertIn(LiveProviderRoute.SCHEMA_VERSION, versions)
        self.assertIn(LiveProviderCallReceipt.SCHEMA_VERSION, versions)
        self.assertIn(ProviderFailureCallReceipt.SCHEMA_VERSION, versions)


if __name__ == "__main__":
    unittest.main()
