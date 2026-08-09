from __future__ import annotations

import unittest

from cera.errors import StateConflictError
from cera.pi_scene.pi_adapter import (
    _ParsedPiStream,
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


if __name__ == "__main__":
    unittest.main()
