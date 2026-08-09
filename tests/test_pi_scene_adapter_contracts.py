from __future__ import annotations

import json
import unittest

from cera.errors import StateConflictError
from cera.pi_scene.pi_adapter import (
    _ParsedPiStream,
    _parse_pi_json_stream,
    _validate_pi_completion,
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
        with self.assertRaisesRegex(StateConflictError, "exactly once"):
            _validate_pi_completion(_parsed(tool_calls=0))

    def test_second_context_call_fails_closed(self) -> None:
        with self.assertRaisesRegex(StateConflictError, "exactly once"):
            _validate_pi_completion(_parsed(tool_calls=2))

    def test_failed_context_call_fails_closed(self) -> None:
        with self.assertRaisesRegex(StateConflictError, "context loading failed"):
            _validate_pi_completion(_parsed(failed_tool_calls=1))

    def test_length_termination_cannot_be_published_as_stop(self) -> None:
        with self.assertRaisesRegex(StateConflictError, "normal terminal stop"):
            _validate_pi_completion(_parsed(finish_status="length"))

    def test_context_start_without_matching_end_fails_closed(self) -> None:
        parsed = _parse_pi_json_stream(
            _stream({"type": "tool_execution_start", "toolName": "context"})
        )
        with self.assertRaisesRegex(StateConflictError, "matched successful"):
            _validate_pi_completion(parsed)

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
        with self.assertRaisesRegex(StateConflictError, "matched successful"):
            _validate_pi_completion(parsed)

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


if __name__ == "__main__":
    unittest.main()
