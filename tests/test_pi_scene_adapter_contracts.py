from __future__ import annotations

import json
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from cera.errors import StateConflictError
from cera.pi_scene.contracts import SceneRoute
from cera.pi_scene.operation_ledger import PiProviderOperationLedger
from cera.pi_scene.pi_adapter import (
    ORDINARY_WRITER_SYSTEM_PROMPT,
    PiOutputLimitError,
    PiSceneAdapter,
    PiSceneInvocationV1,
    _parse_pi_json_stream,
    _ParsedPiStream,
    _ProcessResult,
    _validate_pi_completion,
)
from cera.pi_scene.store import AcceptedPiSessionV1
from cera.pi_scene.writer_view import MaterializedWriterViewV1
from cera.providers.models import (
    ProviderRetryableFailureCategory,
    ProviderTransportError,
)
from cera.serialization import canonical_sha256, text_sha256


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
    def test_default_writer_attempt_timeout_is_three_minutes(self) -> None:
        with TemporaryDirectory() as temporary:
            adapter, _request, _ledger, _view = _offline_invocation(
                Path(temporary),
                "",
            )
            self.assertEqual(adapter.timeout_seconds, 180)

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

    def test_rehydrated_writer_retains_parent_provenance_without_fork(self) -> None:
        stream = "\n".join(
            json.dumps(value)
            for value in (
                {"type": "session", "id": "session-1"},
                {"type": "turn_start"},
                {
                    "type": "message_end",
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "toolCall", "name": "context"}],
                        "usage": {"input": 80, "output": 1},
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
                {
                    "type": "message_end",
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": "Visible scene prose."}],
                        "usage": {"input": 100, "cacheRead": 80, "output": 20},
                        "stopReason": "stop",
                    },
                },
            )
        )
        with TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            adapter, base_request, _, view = _offline_invocation(root, stream)
            parent_id = "accepted-parent-session"
            parent = AcceptedPiSessionV1(
                accepted_turn_id="turn:accepted:parent",
                session_id=parent_id,
                session_path=str((root / "accepted-parent").resolve()),
                session_id_sha256=text_sha256(parent_id),
            )
            request = replace(
                base_request,
                accepted_parent_session=parent,
                force_rehydrate=True,
            )

            command = adapter.command_for(request)
            self.assertNotIn("--fork", command)
            self.assertEqual(command.count("--session-id"), 1)
            with patch("cera.pi_scene.pi_adapter.verify_writer_view", return_value=view):
                result = adapter.invoke(request)

            self.assertTrue(result.writer_receipt.rehydrated)
            self.assertEqual(
                result.writer_receipt.parent_session_id_sha256,
                parent.session_id_sha256,
            )
            self.assertEqual(
                result.writer_receipt.request_sha256,
                canonical_sha256(
                    {
                        "route": SceneRoute.ORDINARY.value,
                        "purpose": "writer",
                        "view_manifest_sha256": view.manifest_sha256,
                        "prompt": request.prompt,
                        "system_prompt": ORDINARY_WRITER_SYSTEM_PROMPT,
                        "model": adapter.model,
                        "thinking": "off",
                        "tools": ["context"],
                        "parent_session_id_sha256": parent.session_id_sha256,
                    }
                ),
            )

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


class PiProviderOperationLedgerTests(unittest.TestCase):
    def test_concurrent_reservations_cannot_exceed_global_operation_ceiling(self) -> None:
        with TemporaryDirectory() as temporary:
            ledger = PiProviderOperationLedger(
                (Path(temporary) / "provider_operations.jsonl").resolve(),
                maximum_operations=48,
                maximum_operations_per_invocation=2,
            )

            def reserve(index: int) -> str:
                return ledger.begin(
                    candidate_id=f"candidate-{index}",
                    purpose="writer",
                    route="ordinary",
                    request_sha256=text_sha256(f"request-{index}"),
                )

            successes: list[str] = []
            failures = 0
            with ThreadPoolExecutor(max_workers=25) as pool:
                futures = [pool.submit(reserve, index) for index in range(25)]
                for future in futures:
                    try:
                        successes.append(future.result())
                    except StateConflictError:
                        failures += 1

            self.assertEqual((len(successes), failures), (24, 1))
            self.assertEqual(len(set(successes)), 24)
            self.assertEqual(ledger.operation_count, 0)
            self.assertEqual(ledger.conservative_operation_count, 48)

    def test_unresolved_reservation_survives_restart_until_terminal_event(self) -> None:
        with TemporaryDirectory() as temporary:
            path = (Path(temporary) / "provider_operations.jsonl").resolve()
            ledger = PiProviderOperationLedger(
                path,
                maximum_operations=4,
                maximum_operations_per_invocation=2,
            )
            first = ledger.begin(
                candidate_id="candidate-first",
                purpose="writer",
                route="ordinary",
                request_sha256=text_sha256("request-first"),
            )
            self.assertEqual((ledger.operation_count, ledger.conservative_operation_count), (0, 2))

            resumed = PiProviderOperationLedger(
                path,
                maximum_operations=4,
                maximum_operations_per_invocation=2,
            )
            second = resumed.begin(
                candidate_id="candidate-second",
                purpose="writer",
                route="ordinary",
                request_sha256=text_sha256("request-second"),
            )
            with self.assertRaisesRegex(StateConflictError, "reservation would exceed"):
                resumed.begin(
                    candidate_id="candidate-blocked",
                    purpose="writer",
                    route="ordinary",
                    request_sha256=text_sha256("request-blocked"),
                )

            resumed.observe_line(second, json.dumps({"type": "turn_start"}))
            resumed.finish(second, status="failed", duration_ms=0)
            restarted = PiProviderOperationLedger(
                path,
                maximum_operations=4,
                maximum_operations_per_invocation=2,
            )
            self.assertEqual(
                (restarted.operation_count, restarted.conservative_operation_count), (1, 3)
            )
            with self.assertRaisesRegex(StateConflictError, "reservation would exceed"):
                restarted.begin(
                    candidate_id="candidate-still-blocked",
                    purpose="writer",
                    route="ordinary",
                    request_sha256=text_sha256("request-still-blocked"),
                )

            restarted.finish(first, status="failed", duration_ms=0)
            third = restarted.begin(
                candidate_id="candidate-third",
                purpose="writer",
                route="ordinary",
                request_sha256=text_sha256("request-third"),
            )
            self.assertTrue(third.startswith("piop-"))
            self.assertEqual(
                (restarted.operation_count, restarted.conservative_operation_count), (1, 3)
            )


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
