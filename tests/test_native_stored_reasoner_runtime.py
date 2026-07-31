from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from cera.reasoner_session import (
    CheckpointStatus,
    InMemoryReasonerSessionPort,
    NativeStoredReasonerSessionRuntime,
)
from cera.runtime import HanezawaHumanTestWorld
from scripts.run_codex_branch_session_ab import prepare_message


ROOT = Path(__file__).resolve().parents[1]


class NativeStoredReasonerRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory(
            prefix="cera-native-stored-runtime-test-"
        )
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.world = HanezawaHumanTestWorld.initialize(
            ROOT,
            self.root / "world.sqlite3",
        )
        self.port = InMemoryReasonerSessionPort()
        self.runtime = NativeStoredReasonerSessionRuntime(
            self.world.store,
            repository_root=ROOT,
            session_port=self.port,
        )
        self.addCleanup(self.runtime.close)

    def prepared(self, message: str, suffix: str):
        return prepare_message(
            self.world,
            message=message,
            session_id=f"native-runtime-{suffix}",
        ).application_request

    def workspace(self, suffix: str) -> Path:
        value = self.root / suffix
        value.mkdir()
        return value

    def test_candidate_forks_from_accepted_branch_session(self) -> None:
        application = self.prepared(
            "Hello, my name is Ted. Is this the Hanezawa household?",
            "medium",
        )
        binding = self.runtime.begin_candidate(
            application,
            effort="medium",
            workspace=self.workspace("medium-workspace"),
        )
        checkpoint = self.world.store.get_reasoner_checkpoint(
            binding.checkpoint_id
        )
        ledger = self.world.store.get_reasoner_session(checkpoint.session_id)
        self.assertEqual(checkpoint.status, CheckpointStatus.CANDIDATE)
        self.assertEqual(ledger.compatibility.reasoning_effort, "medium")
        self.assertEqual(binding.effort, "medium")
        self.assertEqual(self.port.provider_calls, 0)

    def test_effort_change_rotates_only_after_candidate_is_resolved(self) -> None:
        first = self.runtime.begin_candidate(
            self.prepared("Hello, Hanezawa residence?", "first"),
            effort="medium",
            workspace=self.workspace("first-workspace"),
        )
        first_checkpoint = self.world.store.get_reasoner_checkpoint(
            first.checkpoint_id
        )
        first_session_id = first_checkpoint.session_id
        self.runtime.invalidate_checkpoint(
            first.checkpoint_id,
            reason="provider_free_effort_rotation_test",
        )

        second = self.runtime.begin_candidate(
            self.prepared("Sakura waits at the door.", "second"),
            effort="xhigh",
            workspace=self.workspace("second-workspace"),
        )
        second_checkpoint = self.world.store.get_reasoner_checkpoint(
            second.checkpoint_id
        )
        second_ledger = self.world.store.get_reasoner_session(
            second_checkpoint.session_id
        )
        self.assertNotEqual(second_checkpoint.session_id, first_session_id)
        self.assertEqual(second_ledger.rotated_from_session_id, first_session_id)
        self.assertEqual(second_ledger.compatibility.reasoning_effort, "xhigh")
        self.assertEqual(self.world.store.get_branch(self.world.branch_id).generation, 0)


if __name__ == "__main__":
    unittest.main()
