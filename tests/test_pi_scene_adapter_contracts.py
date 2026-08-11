from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from cera.errors import StateConflictError
from cera.pi_scene.contracts import SceneRoute
from cera.pi_scene.operation_ledger import PiProviderOperationLedger
from cera.pi_scene.pi_adapter import (
    PiOutputLimitError,
    PiSceneAdapter,
    PiSceneInvocationV1,
    _parse_pi_json_stream,
    _ParsedPiStream,
    _ProcessResult,
    _validate_pi_completion,
)
from cera.pi_scene.writer_view import MaterializedWriterViewV1
from cera.providers.models import (
    ProviderRetryableFailureCategory,
    ProviderTransportError,
)


def _parsed(
    *,
    tool_calls: int = 1,
    failed_tool_calls: int = 0,
    finish_status: str = "stop",
) -> _ParsedPiStream:
    return _ParsedPiStream(
        session_id="session-1",
        output_text="Visible scene prose.",
        provider_operations=2,
        tool_call_count=tool_calls,
        failed_tool_call_count=failed_tool_calls,
        input_tokens=100,
        cached_input_tokens=80,
        output_tokens=20,
        reasoning_tokens=0,
        finish_status=finish_status,
        event_count=6,
    )


class PiSceneCompletionContractTests(unittest.TestCase):
    def test_exactly_one_successful_context_call_and_stop_passes(self) -> None:
        _validate_pi_completion(_parsed())

    def test_zero_context_calls_fail_closed(self) -> None:
        with self.assertRaises(ProviderTransportError) as captured:
            _validate_pi_completion(_parsed(tool_calls=0))
        self.assertIs(
            captured.exception.retryable_failure_category,
            ProviderRetryableFailureCategory.PROVIDER_OUTPUT_INVALID,
        )

    def test_second_context_call_fails_closed(self) -> None:
        with self.assertRaises(ProviderTransportError) as captured:
            _validate_pi_completion(_parsed(tool_calls=2))
        self.assertIs(
            captured.exception.retryable_failure_category,
            ProviderRetryableFailureCategory.PROVIDER_OUTPUT_INVALID,
        )

    def test_failed_context_call_fails_closed(self) -> None:
        with self.assertRaises(ProviderTransportError) as captured:
            _validate_pi_completion(_parsed(failed_tool_calls=1))
        self.assertIs(
            captured.exception.retryable_failure_category,
            ProviderRetryableFailureCategory.PROVIDER_OUTPUT_INVALID,
        )

    def test_length_termination_cannot_be_published_as_stop(self) -> None:
        with self.assertRaises(ProviderTransportError) as captured:
            _validate_pi_completion(_parsed(finish_status="length"))
        self.assertIsInstance(captured.exception, PiOutputLimitError)
        self.assertIsNone(captured.exception.retryable_failure_category)

    def test_non_stop_completion_is_distinct_from_invalid_output(self) -> None:
        with self.assertRaises(ProviderTransportError) as captured:
            _validate_pi_completion(_parsed(finish_status="toolUse"))
        self.assertIs(
            captured.exception.retryable_failure_category,
            ProviderRetryableFailureCategory.PROVIDER_COMPLETION_INCOMPLETE,
        )

    def test_context_start_without_matching_end_fails_closed(self) -> None:
        parsed = _parse_pi_json_stream(
            _stream({"type": "tool_execution_start", "toolName": "context"})
        )
        with self.assertRaises(ProviderTransportError) as captured:
            _validate_pi_completion(parsed)
        self.assertIs(
            captured.exception.retryable_failure_category,
            ProviderRetryableFailureCategory.PROVIDER_OUTPUT_INVALID,
        )

    def test_non_context_tool_cannot_satisfy_context_contract(self) -> None:
        parsed = _parse_pi_json_stream(
            _stream(
                {"type": "tool_execution_start", "toolName": "read"},
                {
                    "type": "tool_execution_end",
                    "toolName": "read",
                    "isError": False,
                },
            )
        )
        with self.assertRaises(ProviderTransportError) as captured:
            _validate_pi_completion(parsed)
        self.assertIs(
            captured.exception.retryable_failure_category,
            ProviderRetryableFailureCategory.PROVIDER_OUTPUT_INVALID,
        )

    def test_matching_context_start_and_end_passes(self) -> None:
        parsed = _parse_pi_json_stream(
            _stream(
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
            )
        )
        _validate_pi_completion(parsed)

    def test_closed_two_operation_completion_failure_persists_exact_metrics(self) -> None:
        self._assert_closed_invocation_failure(
            _two_operation_stream(second_context_call=False),
            ProviderRetryableFailureCategory.PROVIDER_COMPLETION_INCOMPLETE,
        )

    def test_root_g_two_failed_context_operations_close_as_output_invalid(self) -> None:
        self._assert_closed_invocation_failure(
            _two_operation_stream(
                second_context_call=True,
                failed_context_calls=True,
            ),
            ProviderRetryableFailureCategory.PROVIDER_OUTPUT_INVALID,
        )

    def test_protocol_failure_is_not_typed_until_operation_accounting_closes(self) -> None:
        stream = _incomplete_operation_stream()
        with TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            adapter, request, ledger, view = _offline_invocation(root, stream)
            with (
                patch("cera.pi_scene.pi_adapter.verify_writer_view", return_value=view),
                patch(
                    "cera.pi_scene.pi_adapter.time.perf_counter",
                    side_effect=(10.0, 10.125),
                ),
                self.assertRaises(StateConflictError),
            ):
                adapter.invoke(request)

            invocation_id = str(ledger.events[0]["invocation_id"])
            metrics = ledger.invocation_metrics(invocation_id)
            self.assertEqual(metrics.provider_operations_started, 2)
            self.assertEqual(metrics.provider_operations_completed, 1)
            self.assertEqual(metrics.duration_ms, 125)
            self.assertIsNone(metrics.failure_category)

    def _assert_closed_invocation_failure(
        self,
        stream: str,
        category: ProviderRetryableFailureCategory,
    ) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            adapter, request, ledger, view = _offline_invocation(root, stream)

            with (
                patch("cera.pi_scene.pi_adapter.verify_writer_view", return_value=view),
                patch(
                    "cera.pi_scene.pi_adapter.time.perf_counter",
                    side_effect=(10.0, 10.125),
                ),
                self.assertRaises(ProviderTransportError) as captured,
            ):
                adapter.invoke(request)

            self.assertIs(captured.exception.retryable_failure_category, category)
            invocation_id = str(ledger.events[0]["invocation_id"])
            metrics = ledger.invocation_metrics(invocation_id)
            self.assertEqual(metrics.status, "failed")
            self.assertEqual(metrics.provider_operations_started, 2)
            self.assertEqual(metrics.provider_operations_completed, 2)
            self.assertEqual(metrics.duration_ms, 125)
            self.assertEqual(metrics.failure_category, category.value)


def _stream(*tool_events: dict[str, object]) -> str:
    events: tuple[dict[str, object], ...] = (
        {"type": "session", "id": "session-1"},
        *tool_events,
        {
            "type": "message_end",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "Visible scene prose."}],
                "usage": {"input": 100, "output": 20},
                "stopReason": "stop",
            },
        },
    )
    return "\n".join(json.dumps(value) for value in events)


def _two_operation_stream(
    *,
    second_context_call: bool,
    failed_context_calls: bool = False,
) -> str:
    first_completion = {
        "type": "message_end",
        "message": {
            "role": "assistant",
            "content": [{"type": "toolCall", "name": "context"}],
            "usage": {"input": 100, "cacheRead": 20, "output": 5},
            "stopReason": "toolUse",
        },
    }
    second_completion = {
        "type": "message_end",
        "message": {
            "role": "assistant",
            "content": [{"type": "toolCall", "name": "context"}],
            "usage": {"input": 120, "cacheRead": 80, "output": 20},
            "stopReason": "toolUse",
        },
    }
    events: list[dict[str, object]] = [
        {"type": "session", "id": "session-1"},
        {"type": "turn_start"},
        first_completion,
        {
            "type": "tool_execution_start",
            "toolName": "context",
            "toolCallId": "tool-1",
        },
        {
            "type": "tool_execution_end",
            "toolName": "context",
            "toolCallId": "tool-1",
            "isError": failed_context_calls,
        },
        {"type": "turn_start"},
        second_completion,
    ]
    if second_context_call:
        events.extend(
            (
                {
                    "type": "tool_execution_start",
                    "toolName": "context",
                    "toolCallId": "tool-2",
                },
                {
                    "type": "tool_execution_end",
                    "toolName": "context",
                    "toolCallId": "tool-2",
                    "isError": failed_context_calls,
                },
            )
        )
    return "\n".join(json.dumps(value) for value in events)


def _incomplete_operation_stream() -> str:
    return "\n".join(
        json.dumps(value)
        for value in (
            {"type": "session", "id": "session-1"},
            {"type": "turn_start"},
            {
                "type": "message_end",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "toolCall", "name": "context"}],
                    "usage": {"input": 100, "cacheRead": 20, "output": 5},
                    "stopReason": "toolUse",
                },
            },
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
            {"type": "turn_start"},
        )
    )


def _offline_invocation(
    root: Path,
    stream: str,
) -> tuple[
    PiSceneAdapter,
    PiSceneInvocationV1,
    PiProviderOperationLedger,
    MaterializedWriterViewV1,
]:
    executable = root / "pi.cmd"
    extension = root / "scene.ts"
    executable.write_text("offline", encoding="utf-8")
    extension.write_text("offline", encoding="utf-8")
    ledger = PiProviderOperationLedger(
        root / "provider_operations.jsonl",
        maximum_operations=20,
        maximum_operations_per_invocation=6,
    )

    def runner(_command, _cwd, _environment, _timeout, observe) -> _ProcessResult:
        for line in stream.splitlines():
            observe(line)
        return _ProcessResult(0, stream, "")

    adapter = PiSceneAdapter(
        pi_executable=executable,
        extension_path=extension,
        pi_version="0.84.1",
        operation_ledger=ledger,
        process_runner=runner,
    )
    adapter.external_provider_boundary = False
    view = MaterializedWriterViewV1(
        root=root / "view",
        manifest_path=root / "view" / "MANIFEST.json",
        manifest_sha256="a" * 64,
        file_count=1,
        purpose="writer",
    )
    request = PiSceneInvocationV1(
        route=SceneRoute.ORDINARY,
        purpose="writer",
        view=view,
        prompt="Write the scene.",
        candidate_id="candidate-closed-failure",
        session_dir=root / "session",
    )
    return adapter, request, ledger, view


if __name__ == "__main__":
    unittest.main()
