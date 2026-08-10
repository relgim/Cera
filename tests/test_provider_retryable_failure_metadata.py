from __future__ import annotations

import io
import json
import subprocess
import unittest
import urllib.error
from email.message import Message
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from cera.errors import ContractValidationError, ErrorCode
from cera.pi_scene.pi_adapter import (
    _pi_process_exit_error,
    _ProcessResult,
    _run_process,
)
from cera.providers import (
    CodexSDKTransport,
    CodexWorkerResult,
    DeepSeekMessage,
    ProviderFinishReason,
    ProviderOutputMode,
    ProviderRetryableFailureCategory,
    ProviderTransportError,
    codex_cli_realization_verifier_candidate,
    codex_reasoner_candidate,
    deepseek_composer_candidate,
)
from tests.provider_fakes import (
    OfflineCodexExecRunner,
    OfflineDeepSeekChatTransport,
    OfflineSubprocessCodexRunner,
)


class _HTTPResponse:
    def __init__(self, body: bytes) -> None:
        self.body = body

    def __enter__(self) -> _HTTPResponse:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return self.body


class _RaisingOpener:
    def __init__(self, failure: BaseException) -> None:
        self.failure = failure

    def __call__(self, *_args: object, **_kwargs: object) -> object:
        raise self.failure


class _CodexProcess:
    def __init__(
        self,
        *,
        returncode: int = 0,
        stdout: str = "",
        stderr: str = "",
        failure: BaseException | None = None,
    ) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.failure = failure

    def communicate(self, *_args: object, **_kwargs: object) -> tuple[str, str]:
        if self.failure is not None:
            raise self.failure
        return self.stdout, self.stderr


class _PiProcess:
    def __init__(
        self,
        *,
        stdout: str = "",
        stderr: str = "",
        timeout_once: bool = False,
    ) -> None:
        self.stdout = io.StringIO(stdout)
        self.stderr = io.StringIO(stderr)
        self.returncode = 0
        self.timeout_once = timeout_once
        self.wait_count = 0
        self.killed = False

    def wait(self, timeout: int | None = None) -> int:
        self.wait_count += 1
        if self.timeout_once and self.wait_count == 1:
            raise subprocess.TimeoutExpired("pi", float(timeout or 0))
        return self.returncode

    def kill(self) -> None:
        self.killed = True


class _CodexResultRunner:
    external_provider_boundary = False

    def __init__(self, *, output_text: str | None = None, fail: bool = False) -> None:
        self.output_text = output_text
        self.fail = fail

    def run(
        self,
        *,
        route: object,
        prompt: str,
        output_schema: dict[str, object],
        workspace: Path,
        mcp_binding: object,
        on_worker_started: object = None,
        on_worker_preflight: object = None,
        on_provider_submit: object = None,
    ) -> CodexWorkerResult:
        del prompt, output_schema, workspace, mcp_binding
        if callable(on_worker_started):
            on_worker_started()
        if callable(on_worker_preflight):
            on_worker_preflight()
        if callable(on_provider_submit):
            on_provider_submit()
        if self.fail:
            raise RuntimeError("generic submitted worker failure")
        assert self.output_text is not None
        return CodexWorkerResult(
            output_text=self.output_text,
            provider_request_id="provider-request",
            returned_model=route.model_name,  # type: ignore[attr-defined]
            duration_ms=1,
            input_tokens=1,
            cached_input_tokens=0,
            output_tokens=1,
            reasoning_output_tokens=0,
            transport_version=route.transport_version,  # type: ignore[attr-defined]
            pre_registered_turn_count=1,
        )


class ProviderRetryableFailureMetadataTests(unittest.TestCase):
    def test_enum_is_closed_and_transport_rejects_raw_strings(self) -> None:
        self.assertEqual(
            {value.value for value in ProviderRetryableFailureCategory},
            {
                "transport_timeout",
                "provider_unavailable",
                "provider_process_failed",
                "provider_stream_incomplete",
                "provider_completion_incomplete",
                "provider_output_invalid",
            },
        )
        generic = ProviderTransportError(ErrorCode.REASONER_UNAVAILABLE, "generic")
        self.assertIsNone(generic.retryable_failure_category)
        with self.assertRaises(ContractValidationError):
            ProviderTransportError(
                ErrorCode.REASONER_UNAVAILABLE,
                "raw category",
                retryable_failure_category="transport_timeout",  # type: ignore[arg-type]
            )
        with self.assertRaises(ContractValidationError):
            ProviderTransportError(
                ErrorCode.REASONER_UNAVAILABLE,
                "ambiguity is not a provider category",
                retryable_failure_category="dispatch_ambiguous",  # type: ignore[arg-type]
            )

    def _deepseek_failure(
        self,
        opener: object,
        *,
        output_mode: ProviderOutputMode = ProviderOutputMode.TEXT,
    ) -> ProviderTransportError:
        transport = OfflineDeepSeekChatTransport(
            deepseek_composer_candidate(model="deepseek-v4-flash"),
            opener=opener,
            environment={"DEEPSEEK_API_KEY": "offline"},
        )
        with self.assertRaises(ProviderTransportError) as caught:
            transport.invoke(
                (DeepSeekMessage("user", "provider-free"),),
                output_mode=output_mode,
            )
        return caught.exception

    def test_deepseek_http_classification_uses_status_and_exception_types(self) -> None:
        cases = (
            (
                urllib.error.HTTPError("https://offline", 408, "", Message(), None),
                ProviderRetryableFailureCategory.TRANSPORT_TIMEOUT,
            ),
            (
                urllib.error.HTTPError("https://offline", 429, "", Message(), None),
                ProviderRetryableFailureCategory.PROVIDER_UNAVAILABLE,
            ),
            (
                urllib.error.HTTPError("https://offline", 503, "", Message(), None),
                ProviderRetryableFailureCategory.PROVIDER_UNAVAILABLE,
            ),
            (
                urllib.error.URLError(TimeoutError("typed timeout")),
                ProviderRetryableFailureCategory.TRANSPORT_TIMEOUT,
            ),
            (
                urllib.error.URLError(ConnectionRefusedError()),
                ProviderRetryableFailureCategory.PROVIDER_UNAVAILABLE,
            ),
            (
                urllib.error.URLError("timeout-looking text is not typed"),
                None,
            ),
            (OSError("503-looking generic failure"), None),
        )
        for failure, expected in cases:
            with self.subTest(failure=repr(failure)):
                caught = self._deepseek_failure(_RaisingOpener(failure))
                self.assertIs(caught.retryable_failure_category, expected)

        for status in (400, 401, 403, 413, 422):
            with self.subTest(nonretryable_status=status):
                caught = self._deepseek_failure(
                    _RaisingOpener(
                        urllib.error.HTTPError(
                            "https://offline",
                            status,
                            "timeout unavailable 503",
                            Message(),
                            None,
                        )
                    )
                )
                self.assertIsNone(caught.retryable_failure_category)

    def test_deepseek_completion_and_envelope_failures_are_narrow(self) -> None:
        def response(finish_reason: str, content: object = "partial") -> _HTTPResponse:
            payload = {
                "id": "offline-id",
                "model": "deepseek-v4-flash",
                "choices": [
                    {
                        "message": {"content": content},
                        "finish_reason": finish_reason,
                    }
                ],
                "usage": {
                    "prompt_tokens": 1,
                    "completion_tokens": 1,
                    "prompt_cache_hit_tokens": 0,
                    "prompt_cache_miss_tokens": 1,
                },
            }
            return _HTTPResponse(json.dumps(payload).encode("utf-8"))

        for reason in (
            ProviderFinishReason.LENGTH,
            ProviderFinishReason.CONTENT_FILTER,
            ProviderFinishReason.TOOL_CALLS,
        ):
            with self.subTest(nonretryable_finish=reason.value):
                caught = self._deepseek_failure(
                    lambda *_args, _reason=reason, **_kwargs: response(_reason.value)
                )
                self.assertIsNone(caught.retryable_failure_category)

        incomplete = self._deepseek_failure(
            lambda *_args, **_kwargs: response(
                ProviderFinishReason.INSUFFICIENT_SYSTEM_RESOURCE.value
            )
        )
        self.assertIs(
            incomplete.retryable_failure_category,
            ProviderRetryableFailureCategory.PROVIDER_COMPLETION_INCOMPLETE,
        )

        invalid_outer = self._deepseek_failure(lambda *_args, **_kwargs: _HTTPResponse(b"{"))
        self.assertIs(
            invalid_outer.retryable_failure_category,
            ProviderRetryableFailureCategory.PROVIDER_OUTPUT_INVALID,
        )
        malformed_content = self._deepseek_failure(
            lambda *_args, **_kwargs: response("stop", "{"),
            output_mode=ProviderOutputMode.JSON_OBJECT,
        )
        self.assertIs(
            malformed_content.retryable_failure_category,
            ProviderRetryableFailureCategory.PROVIDER_OUTPUT_INVALID,
        )
        empty_content = self._deepseek_failure(lambda *_args, **_kwargs: response("stop", ""))
        self.assertIs(
            empty_content.retryable_failure_category,
            ProviderRetryableFailureCategory.PROVIDER_COMPLETION_INCOMPLETE,
        )

    def test_codex_sdk_output_invalid_but_generic_submitted_failure_is_closed(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            invalid_transport = CodexSDKTransport(
                codex_reasoner_candidate(),
                workspace=root,
                runner=_CodexResultRunner(output_text="{"),
            )
            with self.assertRaises(ProviderTransportError) as invalid:
                invalid_transport.invoke(
                    "provider-free",
                    output_schema={
                        "type": "object",
                        "properties": {},
                        "required": [],
                        "additionalProperties": False,
                    },
                )
            self.assertIs(
                invalid.exception.retryable_failure_category,
                ProviderRetryableFailureCategory.PROVIDER_OUTPUT_INVALID,
            )

            generic_transport = CodexSDKTransport(
                codex_reasoner_candidate(),
                workspace=root,
                runner=_CodexResultRunner(fail=True),
            )
            with self.assertRaises(ProviderTransportError) as generic:
                generic_transport.invoke(
                    "provider-free",
                    output_schema={
                        "type": "object",
                        "properties": {},
                        "required": [],
                        "additionalProperties": False,
                    },
                )
            self.assertIsNone(generic.exception.retryable_failure_category)

    def _codex_exec_failure(
        self,
        process: _CodexProcess | BaseException,
    ) -> ProviderTransportError:
        with TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            runner = OfflineCodexExecRunner()
            popen_patch = (
                patch(
                    "cera.providers.codex_exec.subprocess.Popen",
                    side_effect=process,
                )
                if isinstance(process, BaseException)
                else patch(
                    "cera.providers.codex_exec.subprocess.Popen",
                    return_value=process,
                )
            )
            with (
                patch(
                    "cera.providers.codex_exec._resolve_and_validate_codex_binary",
                    return_value=Path("codex.exe"),
                ),
                popen_patch,
                patch("cera.providers.codex_exec._terminate_codex_worker_tree"),
                self.assertRaises(ProviderTransportError) as caught,
            ):
                runner.run(
                    route=codex_cli_realization_verifier_candidate(),
                    prompt="provider-free",
                    output_schema={"type": "object"},
                    workspace=workspace,
                    mcp_binding=None,
                )
            return caught.exception

    def test_codex_exec_has_typed_timeout_process_protocol_and_completion_failures(self) -> None:
        cases = (
            (
                OSError("explicit process start failure"),
                ProviderRetryableFailureCategory.PROVIDER_PROCESS_FAILED,
            ),
            (
                _CodexProcess(
                    failure=subprocess.TimeoutExpired("codex", 1),
                ),
                ProviderRetryableFailureCategory.TRANSPORT_TIMEOUT,
            ),
            (
                _CodexProcess(
                    returncode=17,
                    stderr="auth timeout unavailable 503 invalid request",
                ),
                None,
            ),
            (
                _CodexProcess(stdout="not-jsonl"),
                ProviderRetryableFailureCategory.PROVIDER_OUTPUT_INVALID,
            ),
            (
                _CodexProcess(
                    stdout="\n".join(
                        (
                            json.dumps({"type": "thread.started", "thread_id": "offline"}),
                            json.dumps({"type": "turn.started"}),
                            json.dumps(
                                {
                                    "type": "turn.failed",
                                    "error": "auth timeout unavailable 503 invalid request",
                                }
                            ),
                        )
                    )
                ),
                None,
            ),
            (
                _CodexProcess(
                    stdout="\n".join(
                        (
                            json.dumps({"type": "thread.started", "thread_id": "offline"}),
                            json.dumps({"type": "turn.started"}),
                        )
                    )
                ),
                ProviderRetryableFailureCategory.PROVIDER_COMPLETION_INCOMPLETE,
            ),
            (
                _CodexProcess(
                    stdout="\n".join(
                        (
                            json.dumps({"type": "thread.started", "thread_id": "offline"}),
                            json.dumps({"type": "turn.started"}),
                            json.dumps(
                                {
                                    "type": "turn.completed",
                                    "usage": {
                                        "input_tokens": 1,
                                        "cached_input_tokens": 0,
                                        "output_tokens": 1,
                                        "reasoning_output_tokens": 0,
                                    },
                                }
                            ),
                        )
                    )
                ),
                ProviderRetryableFailureCategory.PROVIDER_COMPLETION_INCOMPLETE,
            ),
        )
        for process, expected in cases:
            with self.subTest(expected=getattr(expected, "value", None)):
                caught = self._codex_exec_failure(process)
                self.assertIs(caught.retryable_failure_category, expected)

    def test_codex_worker_exit_is_closed_but_process_start_is_typed(self) -> None:
        with TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            runner = OfflineSubprocessCodexRunner()
            with (
                patch(
                    "cera.providers.codex.subprocess.Popen",
                    return_value=_CodexProcess(
                        returncode=23,
                        stderr=(
                            "codex qualification worker failed: auth timeout "
                            "unavailable 503 invalid request"
                        ),
                    ),
                ),
                self.assertRaises(ProviderTransportError) as worker_exit,
            ):
                runner.run(
                    route=codex_reasoner_candidate(),
                    prompt="provider-free",
                    output_schema={"type": "object"},
                    workspace=workspace,
                    mcp_binding=None,
                )
            self.assertIsNone(worker_exit.exception.retryable_failure_category)

        with TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            runner = OfflineSubprocessCodexRunner()
            with (
                patch(
                    "cera.providers.codex.subprocess.Popen",
                    side_effect=OSError("process start"),
                ),
                self.assertRaises(ProviderTransportError) as process_start,
            ):
                runner.run(
                    route=codex_reasoner_candidate(),
                    prompt="provider-free",
                    output_schema={"type": "object"},
                    workspace=workspace,
                    mcp_binding=None,
                )
            self.assertIs(
                process_start.exception.retryable_failure_category,
                ProviderRetryableFailureCategory.PROVIDER_PROCESS_FAILED,
            )

    def test_pi_process_boundaries_are_typed_without_message_parsing(self) -> None:
        exit_failure = _pi_process_exit_error(
            _ProcessResult(
                9,
                "",
                "auth timeout unavailable 503 invalid request",
            )
        )
        self.assertIsNone(exit_failure.retryable_failure_category)

        with (
            patch("cera.pi_scene.pi_adapter.assert_provider_dispatch_allowed"),
            patch(
                "cera.pi_scene.pi_adapter.subprocess.Popen",
                side_effect=OSError("start failed"),
            ),
            self.assertRaises(ProviderTransportError) as start,
        ):
            _run_process(("pi",), Path.cwd(), {}, 1, lambda _line: None)
        self.assertIs(
            start.exception.retryable_failure_category,
            ProviderRetryableFailureCategory.PROVIDER_PROCESS_FAILED,
        )

        timeout_process = _PiProcess(timeout_once=True)
        with (
            patch("cera.pi_scene.pi_adapter.assert_provider_dispatch_allowed"),
            patch(
                "cera.pi_scene.pi_adapter.subprocess.Popen",
                return_value=timeout_process,
            ),
            self.assertRaises(ProviderTransportError) as timeout,
        ):
            _run_process(("pi",), Path.cwd(), {}, 1, lambda _line: None)
        self.assertTrue(timeout_process.killed)
        self.assertIs(
            timeout.exception.retryable_failure_category,
            ProviderRetryableFailureCategory.TRANSPORT_TIMEOUT,
        )

        stream_process = _PiProcess(stdout="one event\n")
        with (
            patch("cera.pi_scene.pi_adapter.assert_provider_dispatch_allowed"),
            patch(
                "cera.pi_scene.pi_adapter.subprocess.Popen",
                return_value=stream_process,
            ),
            self.assertRaises(ProviderTransportError) as stream,
        ):
            _run_process(
                ("pi",),
                Path.cwd(),
                {},
                1,
                lambda _line: (_ for _ in ()).throw(RuntimeError("callback")),
            )
        self.assertIs(
            stream.exception.retryable_failure_category,
            ProviderRetryableFailureCategory.PROVIDER_STREAM_INCOMPLETE,
        )


if __name__ == "__main__":
    unittest.main()
