from __future__ import annotations

import unittest
from unittest.mock import patch

from cera.errors import ContractValidationError, StateConflictError
from cera.sequence_first.sessions import PersistentPlannerSession


class _Backend:
    def __init__(self) -> None:
        self.started = 0
        self.resumable: set[str] = set()
        self.run_threads: list[str] = []

    def start_stored_thread(self, *, base_instructions: str, profile: str) -> str:
        self.started += 1
        thread_id = f"thread-{self.started}"
        self.resumable.add(thread_id)
        return thread_id

    def run_planner_turn(self, *, thread_id, prompt, reference_scope):
        self.run_threads.append(thread_id)
        return "planned"

    def is_resumable(self, thread_id: str) -> bool:
        return thread_id in self.resumable


class _SemanticInput:
    pass


def _plan(session: PersistentPlannerSession):
    with (
        patch("cera.sequence_first.sessions.planner_turn_prompt", return_value="prompt"),
        patch(
            "cera.sequence_first.sessions.ProviderReferenceScopeV1.from_turn",
            return_value="scope",
        ),
    ):
        return session.plan(_SemanticInput())


class PersistentPlannerSessionRecoveryTests(unittest.TestCase):
    def test_new_thread_is_persisted_before_first_turn_dispatch(self) -> None:
        backend = _Backend()
        saved: list[str] = []
        session = PersistentPlannerSession(backend, persist_thread_id=saved.append)
        result = _plan(session)
        self.assertEqual(result, "planned")
        self.assertEqual(saved, ["thread-1"])
        self.assertEqual(backend.run_threads, ["thread-1"])

    def test_restored_thread_is_reused_without_starting_another(self) -> None:
        backend = _Backend()
        backend.resumable.add("thread-existing")
        session = PersistentPlannerSession(
            backend,
            restored_thread_id="thread-existing",
        )
        _plan(session)
        self.assertEqual(backend.started, 0)
        self.assertEqual(backend.run_threads, ["thread-existing"])

    def test_nonresumable_restored_thread_fails_without_fallback(self) -> None:
        backend = _Backend()
        session = PersistentPlannerSession(
            backend,
            restored_thread_id="thread-lost",
        )
        with self.assertRaisesRegex(StateConflictError, "not resumable"):
            _plan(session)
        self.assertEqual(backend.started, 0)
        self.assertEqual(backend.run_threads, [])

    def test_empty_restored_thread_is_rejected(self) -> None:
        with self.assertRaisesRegex(ContractValidationError, "identity is empty"):
            PersistentPlannerSession(_Backend(), restored_thread_id=" ")


if __name__ == "__main__":
    unittest.main()
