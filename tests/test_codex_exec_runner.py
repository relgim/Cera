from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from cera.errors import ContractValidationError, ErrorCode
from cera.providers import (
    CodexExecRunner,
    CodexStructuredOutputTransport,
    ProviderTransportError,
    codex_cli_realization_verifier_candidate,
    codex_transport_probe_output_schema,
)
from cera.providers.codex import CodexWorkerResult
from cera.providers.codex_exec import (
    _codex_exec_environment,
    _parse_codex_exec_jsonl,
    _resolve_and_validate_codex_binary,
)
from cera.providers.codex_exec_contract import (
    CODEX_CLI_EXEC_COMPATIBILITY_ID,
    CODEX_CLI_EXEC_CONTRACT_SHA256,
    CODEX_CLI_EXEC_VERSION,
)
from tests.provider_fakes import OfflineCodexExecRunner


def _success_jsonl(*, item_type: str = "agent_message") -> str:
    events = (
        {"type": "thread.started", "thread_id": "private-thread"},
        {"type": "turn.started"},
        {
            "type": "item.completed",
            "item": {
                "id": "item-1",
                "type": item_type,
                "text": '{"probe":"ok"}',
            },
        },
        {
            "type": "turn.completed",
            "usage": {
                "input_tokens": 100,
                "cached_input_tokens": 40,
                "output_tokens": 12,
                "reasoning_output_tokens": 7,
            },
        },
    )
    return "\n".join(json.dumps(event) for event in events) + "\n"


class _ExecProcess:
    pid = 12345

    def __init__(
        self,
        command,
        *,
        stdin,
        stdout,
        stderr,
        text,
        encoding,
        cwd,
        env,
        **kwargs,
    ) -> None:
        self.command = tuple(command)
        self.cwd = Path(cwd)
        self.environment = dict(env)
        self.kwargs = kwargs
        self.returncode = 0
        self.prompt: str | None = None
        output_index = self.command.index("--output-last-message") + 1
        self.output_path = Path(self.command[output_index])
        schema_index = self.command.index("--output-schema") + 1
        self.schema = json.loads(Path(self.command[schema_index]).read_text("utf-8"))

    def communicate(self, *, input, timeout):
        self.prompt = input
        self.timeout = timeout
        self.output_path.write_text('{"probe":"ok"}', encoding="utf-8")
        return _success_jsonl(), ""


class CodexExecRunnerTests(unittest.TestCase):
    def test_one_shot_cli_route_is_pinned_unretried_and_unpromoted(self) -> None:
        route = codex_cli_realization_verifier_candidate()
        self.assertEqual(route.transport_name, "codex_cli_exec")
        self.assertEqual(route.transport_version, CODEX_CLI_EXEC_VERSION)
        self.assertEqual(
            route.transport_compatibility_id,
            CODEX_CLI_EXEC_COMPATIBILITY_ID,
        )
        self.assertEqual(
            route.transport_compatibility_source_sha256,
            CODEX_CLI_EXEC_CONTRACT_SHA256,
        )
        self.assertEqual(route.automatic_retry_count, 0)
        self.assertFalse(route.fallback_enabled)
        self.assertFalse(route.production_enabled)

    def test_runner_uses_stdin_and_safe_explicit_cli_contract(self) -> None:
        created: list[_ExecProcess] = []

        def create_process(command, **kwargs):
            process = _ExecProcess(command, **kwargs)
            created.append(process)
            return process

        with tempfile.TemporaryDirectory() as directory, patch(
            "cera.providers.codex_exec._resolve_and_validate_codex_binary",
            return_value=Path("C:/qualified/codex.exe"),
        ), patch(
            "cera.providers.codex_exec.subprocess.Popen",
            side_effect=create_process,
        ), patch.dict(
            os.environ,
            {
                "OPENAI_API_KEY": "must-not-leak",
                "CODEX_API_KEY": "must-not-leak",
                "DEEPSEEK_API_KEY": "must-not-leak",
                "CERA_REQUEST_EVIDENCE_TOKEN": "must-not-leak",
                "CERA_SAFE_TEST_VALUE": "retained",
            },
            clear=False,
        ):
            workspace = Path(directory)
            route = codex_cli_realization_verifier_candidate()
            transport = CodexStructuredOutputTransport(
                route,
                workspace=workspace,
                runner=OfflineCodexExecRunner(),
            )
            result = transport.invoke(
                "Verify only this synthetic candidate.",
                output_schema=codex_transport_probe_output_schema(),
            )
            workspace_empty = not any(workspace.iterdir())

        self.assertEqual(result.parsed_json, {"probe": "ok"})
        self.assertEqual(result.receipt.external_provider_calls, 1)
        self.assertEqual(len(created), 1)
        process = created[0]
        command = process.command
        self.assertEqual(command[1], "exec")
        for flag in (
            "--ephemeral",
            "--ignore-user-config",
            "--ignore-rules",
            "--skip-git-repo-check",
            "--json",
            "--sandbox",
            "--model",
            "--cd",
            "--output-schema",
            "--output-last-message",
        ):
            self.assertIn(flag, command)
        self.assertEqual(command[-1], "-")
        self.assertNotIn("Verify only this synthetic candidate.", command)
        self.assertEqual(
            process.prompt,
            "Verify only this synthetic candidate.\n",
        )
        self.assertEqual(process.schema, codex_transport_probe_output_schema())
        self.assertEqual(process.environment["CERA_SAFE_TEST_VALUE"], "retained")
        for secret in (
            "OPENAI_API_KEY",
            "CODEX_API_KEY",
            "DEEPSEEK_API_KEY",
            "CERA_REQUEST_EVIDENCE_TOKEN",
        ):
            self.assertNotIn(secret, process.environment)
        self.assertTrue(workspace_empty)

    def test_runner_rejects_mcp_and_unpinned_route_before_dispatch(self) -> None:
        runner = OfflineCodexExecRunner()
        route = codex_cli_realization_verifier_candidate()
        with tempfile.TemporaryDirectory() as directory, patch(
            "cera.providers.codex_exec.subprocess.Popen"
        ) as popen:
            with self.assertRaises(ProviderTransportError) as caught:
                runner.run(
                    route=route,
                    prompt="probe",
                    output_schema=codex_transport_probe_output_schema(),
                    workspace=Path(directory),
                    mcp_binding=object(),  # type: ignore[arg-type]
                )
            self.assertEqual(caught.exception.external_provider_calls_observed, 0)
            with self.assertRaises(ProviderTransportError) as caught:
                runner.run(
                    route=replace(route, transport_name="other"),
                    prompt="probe",
                    output_schema=codex_transport_probe_output_schema(),
                    workspace=Path(directory),
                    mcp_binding=None,
                )
            self.assertEqual(caught.exception.external_provider_calls_observed, 0)
        popen.assert_not_called()

    def test_timeout_kills_process_tree_once_without_retry(self) -> None:
        class TimedOutProcess(_ExecProcess):
            def communicate(self, *, input, timeout):
                raise subprocess.TimeoutExpired("codex exec", timeout)

        with tempfile.TemporaryDirectory() as directory, patch(
            "cera.providers.codex_exec._resolve_and_validate_codex_binary",
            return_value=Path("C:/qualified/codex.exe"),
        ), patch(
            "cera.providers.codex_exec.subprocess.Popen",
            side_effect=TimedOutProcess,
        ) as popen, patch(
            "cera.providers.codex_exec._terminate_codex_worker_tree"
        ) as terminate:
            with self.assertRaises(ProviderTransportError) as caught:
                OfflineCodexExecRunner().run(
                    route=codex_cli_realization_verifier_candidate(),
                    prompt="probe",
                    output_schema=codex_transport_probe_output_schema(),
                    workspace=Path(directory),
                    mcp_binding=None,
                )
        self.assertEqual(caught.exception.code, ErrorCode.REASONER_UNAVAILABLE)
        self.assertEqual(caught.exception.external_provider_calls_observed, 1)
        self.assertEqual(popen.call_count, 1)
        terminate.assert_called_once()

    def test_nonzero_exit_and_workspace_mutation_fail_closed(self) -> None:
        class FailedProcess(_ExecProcess):
            def communicate(self, *, input, timeout):
                self.returncode = 1
                return "", ""

        with tempfile.TemporaryDirectory() as directory, patch(
            "cera.providers.codex_exec._resolve_and_validate_codex_binary",
            return_value=Path("C:/qualified/codex.exe"),
        ), patch(
            "cera.providers.codex_exec.subprocess.Popen",
            side_effect=FailedProcess,
        ):
            with self.assertRaises(ProviderTransportError) as caught:
                OfflineCodexExecRunner().run(
                    route=codex_cli_realization_verifier_candidate(),
                    prompt="probe",
                    output_schema=codex_transport_probe_output_schema(),
                    workspace=Path(directory),
                    mcp_binding=None,
                )
        self.assertEqual(caught.exception.external_provider_calls_observed, 1)

        class MutatingProcess(_ExecProcess):
            def communicate(self, *, input, timeout):
                result = super().communicate(input=input, timeout=timeout)
                (self.cwd / "mutation.txt").write_text("forbidden", encoding="utf-8")
                return result

        with tempfile.TemporaryDirectory() as directory, patch(
            "cera.providers.codex_exec._resolve_and_validate_codex_binary",
            return_value=Path("C:/qualified/codex.exe"),
        ), patch(
            "cera.providers.codex_exec.subprocess.Popen",
            side_effect=MutatingProcess,
        ):
            with self.assertRaises(ProviderTransportError) as caught:
                OfflineCodexExecRunner().run(
                    route=codex_cli_realization_verifier_candidate(),
                    prompt="probe",
                    output_schema=codex_transport_probe_output_schema(),
                    workspace=Path(directory),
                    mcp_binding=None,
                )
        self.assertEqual(
            caught.exception.safe_diagnostics,
            ("transport:workspace_mutation", "transport_mode:cli_exec_one_shot"),
        )

    def test_jsonl_parser_accepts_only_one_tool_free_completed_turn(self) -> None:
        thread_id, usage = _parse_codex_exec_jsonl(_success_jsonl())
        self.assertEqual(thread_id, "private-thread")
        self.assertEqual(usage["reasoning_output_tokens"], 7)
        for invalid in (
            _success_jsonl(item_type="command_execution"),
            '{"type":"turn.started"}\n',
            _success_jsonl() + '{"type":"turn.completed","usage":{}}\n',
            '{"type":"error","message":"private"}\n',
            "not-json\n",
        ):
            with self.subTest(invalid=invalid[:40]):
                with self.assertRaises(ValueError):
                    _parse_codex_exec_jsonl(invalid)

    def test_cli_worker_evidence_cannot_claim_sdk_registration(self) -> None:
        with self.assertRaisesRegex(
            ContractValidationError,
            "compatibility activation evidence",
        ):
            CodexWorkerResult(
                output_text='{"probe":"ok"}',
                provider_request_id="private",
                returned_model="gpt-5.6-sol",
                duration_ms=1,
                input_tokens=1,
                cached_input_tokens=0,
                output_tokens=1,
                reasoning_output_tokens=0,
                transport_version=CODEX_CLI_EXEC_VERSION,
                transport_compatibility_id=CODEX_CLI_EXEC_COMPATIBILITY_ID,
                transport_compatibility_source_sha256=(
                    CODEX_CLI_EXEC_CONTRACT_SHA256
                ),
                pre_registered_turn_count=1,
            )

    def test_binary_and_environment_validation_are_fail_closed(self) -> None:
        completed = subprocess.CompletedProcess(
            args=(),
            returncode=0,
            stdout=f"codex-cli {CODEX_CLI_EXEC_VERSION}\n",
            stderr="",
        )
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / "codex.exe"
            binary.touch()
            with patch(
                "codex_cli_bin.bundled_codex_path",
                return_value=str(binary),
            ), patch(
                "cera.providers.codex_exec.subprocess.run",
                return_value=completed,
            ) as run:
                self.assertEqual(_resolve_and_validate_codex_binary(), binary.resolve())
            self.assertEqual(run.call_count, 1)

        environment = _codex_exec_environment()
        self.assertNotIn("OPENAI_API_KEY", environment)
        self.assertNotIn("CODEX_API_KEY", environment)


if __name__ == "__main__":
    unittest.main()
