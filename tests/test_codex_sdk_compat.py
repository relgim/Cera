from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import patch

from openai_codex._message_router import MessageRouter
from openai_codex.generated.v2_all import (
    Turn,
    TurnCompletedNotification,
    TurnStartResponse,
    TurnStatus,
)
from openai_codex.models import Notification

from cera.errors import ContractValidationError
from cera.providers.codex_sdk_compat import (
    CODEX_SDK_COMPATIBILITY_ID,
    install_early_turn_completion_buffer,
)


def completed_turn(turn_id: str) -> Turn:
    return Turn(
        id=turn_id,
        items=[],
        status=TurnStatus.completed,
    )


class SimulatedEarlyCompletionClient:
    def __init__(self) -> None:
        self._router = MessageRouter()

    def turn_start(self, thread_id, input_items, params=None):
        del input_items, params
        turn = completed_turn("turn:early")
        self._router.route_notification(
            Notification(
                method="turn/completed",
                payload=TurnCompletedNotification(
                    threadId=thread_id,
                    turn=turn,
                ),
            )
        )
        self._router.register_turn(turn.id)
        return TurnStartResponse(turn=turn)


class SimulatedPostResponseCompletionClient:
    def __init__(self) -> None:
        self._router = MessageRouter()

    def turn_start(self, thread_id, input_items, params=None):
        del thread_id, input_items, params
        return TurnStartResponse(turn=completed_turn("turn:post-response"))


class CodexSdkCompatibilityTests(unittest.TestCase):
    def test_early_completion_is_replayed_after_turn_start_registration(self) -> None:
        client = SimulatedEarlyCompletionClient()
        codex = SimpleNamespace(_client=client)
        state = install_early_turn_completion_buffer(codex)
        response = client.turn_start("thread:one", "prompt", params={})
        notification = client._router.next_turn_notification(response.turn.id)
        self.assertEqual(notification.method, "turn/completed")
        self.assertEqual(notification.payload.turn.id, response.turn.id)
        self.assertEqual(state.compatibility_id, CODEX_SDK_COMPATIBILITY_ID)
        self.assertEqual(state.buffered_early_completion_count, 1)
        self.assertEqual(state.pre_registered_turn_count, 1)

    def test_returned_turn_is_registered_before_post_response_completion(self) -> None:
        client = SimulatedPostResponseCompletionClient()
        codex = SimpleNamespace(_client=client)
        state = install_early_turn_completion_buffer(codex)
        response = client.turn_start("thread:one", "prompt", params={})

        turn = response.turn
        client._router.route_notification(
            Notification(
                method="turn/completed",
                payload=TurnCompletedNotification(
                    threadId="thread:one",
                    turn=turn,
                ),
            )
        )
        notification = client._router.next_turn_notification(turn.id)
        self.assertEqual(notification.method, "turn/completed")
        self.assertEqual(notification.payload.turn.id, turn.id)
        self.assertEqual(state.buffered_early_completion_count, 0)
        self.assertEqual(state.pre_registered_turn_count, 1)

    def test_unowned_early_completion_keeps_sdk_default_discard_behavior(self) -> None:
        client = SimulatedEarlyCompletionClient()
        codex = SimpleNamespace(_client=client)
        install_early_turn_completion_buffer(codex)
        turn = completed_turn("turn:unowned")
        client._router.route_notification(
            Notification(
                method="turn/completed",
                payload=TurnCompletedNotification(
                    threadId="thread:not-starting",
                    turn=turn,
                ),
            )
        )
        self.assertNotIn(
            turn.id,
            client._router._pending_turn_notifications,
        )

    def test_changed_sdk_router_source_fails_closed(self) -> None:
        client = SimulatedEarlyCompletionClient()
        codex = SimpleNamespace(_client=client)
        with patch(
            "cera.providers.codex_sdk_compat.inspect.getsource",
            return_value="changed",
        ), self.assertRaisesRegex(ContractValidationError, "does not match"):
            install_early_turn_completion_buffer(codex)


if __name__ == "__main__":
    unittest.main()
