from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from cera.errors import StateConflictError
from cera.pi_scene.planner_state import (
    PlannerThreadStateStore,
    PlannerThreadStateV1,
)


COMPATIBILITY = "a" * 64


class _Backend:
    def __init__(self) -> None:
        self.started = 0
        self.turns = 0
        self.resumable = True

    def start_stored_thread(self, **_kwargs) -> str:
        self.started += 1
        return "thread-new"

    def run_planner_turn(self, **_kwargs):
        self.turns += 1
        return {"ok": True}

    def is_resumable(self, _thread_id: str) -> bool:
        return self.resumable


class PlannerThreadStateStoreTests(unittest.TestCase):
    def test_new_thread_is_persisted_and_reused_after_restart(self) -> None:
        with TemporaryDirectory() as directory:
            store = PlannerThreadStateStore(Path(directory))
            backend = _Backend()
            session = store.session_factory(compatibility_sha256=COMPATIBILITY)(
                "chat-one", "medium", backend
            )
            with (
                patch(
                    "cera.sequence_first.sessions.planner_turn_prompt",
                    return_value="prompt",
                ),
                patch(
                    "cera.sequence_first.sessions.ProviderReferenceScopeV1.from_turn",
                    return_value="scope",
                ),
            ):
                self.assertEqual(session.plan(object()), {"ok": True})  # type: ignore[arg-type]
            self.assertEqual(backend.started, 1)
            restored = store.session_factory(compatibility_sha256=COMPATIBILITY)(
                "chat-one", "medium", backend
            )
            self.assertEqual(restored.thread_id, "thread-new")

    def test_effort_and_chat_have_isolated_state(self) -> None:
        with TemporaryDirectory() as directory:
            store = PlannerThreadStateStore(Path(directory))
            store.persist(
                PlannerThreadStateV1("chat-one", "medium", COMPATIBILITY, "thread-a")
            )
            self.assertIsNone(
                store.load(
                    session_id="chat-two",
                    reasoning_effort="medium",
                    compatibility_sha256=COMPATIBILITY,
                )
            )
            self.assertIsNone(
                store.load(
                    session_id="chat-one",
                    reasoning_effort="high",
                    compatibility_sha256=COMPATIBILITY,
                )
            )

    def test_existing_binding_cannot_be_replaced(self) -> None:
        with TemporaryDirectory() as directory:
            store = PlannerThreadStateStore(Path(directory))
            store.persist(
                PlannerThreadStateV1("chat-one", "medium", COMPATIBILITY, "thread-a")
            )
            with self.assertRaises(StateConflictError):
                store.persist(
                    PlannerThreadStateV1("chat-one", "medium", COMPATIBILITY, "thread-b")
                )

    def test_unknown_field_and_hash_tampering_fail_closed(self) -> None:
        with TemporaryDirectory() as directory:
            store = PlannerThreadStateStore(Path(directory))
            state = PlannerThreadStateV1(
                "chat-one", "medium", COMPATIBILITY, "thread-a"
            )
            store.persist(state)
            path = next(Path(directory).rglob("PLANNER_THREAD_STATE.json"))
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["unexpected"] = True
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(StateConflictError):
                store.load(
                    session_id="chat-one",
                    reasoning_effort="medium",
                    compatibility_sha256=COMPATIBILITY,
                )

    def test_compatibility_mismatch_does_not_reuse_thread(self) -> None:
        with TemporaryDirectory() as directory:
            store = PlannerThreadStateStore(Path(directory))
            store.persist(
                PlannerThreadStateV1("chat-one", "medium", COMPATIBILITY, "thread-a")
            )
            with self.assertRaises(StateConflictError):
                store.load(
                    session_id="chat-one",
                    reasoning_effort="medium",
                    compatibility_sha256="b" * 64,
                )


if __name__ == "__main__":
    unittest.main()
