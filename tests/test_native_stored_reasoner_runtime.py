from __future__ import annotations

import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from cera.errors import ErrorCode
from cera.providers import ProviderTransportError
from cera.providers.codex_runtime_policy import (
    CODEX_RUNTIME_TOOL_SURFACE_POLICY_ID,
)
from cera.reasoner.mcp_bridge import MCP_TOOL_CONTRACT_VERSION
from cera.reasoner_session import (
    CheckpointStatus,
    InMemoryReasonerSessionPort,
    NativeStoredReasonerSessionRuntime,
    SessionStatus,
)
from cera.reasoner_session.runtime import _StablePrefixStoredTransport
from cera.runtime import HanezawaHumanTestWorld
from scripts.run_codex_branch_session_ab import prepare_message

ROOT = Path(__file__).resolve().parents[1]


class NativeStoredReasonerRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory(prefix="cera-native-stored-runtime-test-")
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
        checkpoint = self.world.store.get_reasoner_checkpoint(binding.checkpoint_id)
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
        first_checkpoint = self.world.store.get_reasoner_checkpoint(first.checkpoint_id)
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
        second_checkpoint = self.world.store.get_reasoner_checkpoint(second.checkpoint_id)
        second_ledger = self.world.store.get_reasoner_session(second_checkpoint.session_id)
        self.assertNotEqual(second_checkpoint.session_id, first_session_id)
        self.assertEqual(second_ledger.rotated_from_session_id, first_session_id)
        self.assertEqual(second_ledger.compatibility.reasoning_effort, "xhigh")
        self.assertEqual(self.world.store.get_branch(self.world.branch_id).generation, 0)

    def test_pre_tool_surface_policy_handle_is_invalidated_before_resume(
        self,
    ) -> None:
        first_application = self.prepared("compatibility probe one", "legacy-policy")
        current = self.runtime._compatibility(
            first_application.reasoner_request,
            "medium",
        )
        legacy = replace(
            current,
            tool_contract_version=MCP_TOOL_CONTRACT_VERSION,
        )
        self.assertIn(
            CODEX_RUNTIME_TOOL_SURFACE_POLICY_ID,
            current.tool_contract_version,
        )
        self.assertNotEqual(current.compatibility_sha256, legacy.compatibility_sha256)

        with patch.object(self.runtime, "_compatibility", return_value=legacy):
            first = self.runtime.begin_candidate(
                first_application,
                effort="medium",
                workspace=self.workspace("legacy-policy-workspace"),
            )
        first_checkpoint = self.world.store.get_reasoner_checkpoint(first.checkpoint_id)
        first_session_id = first_checkpoint.session_id
        self.runtime.invalidate_checkpoint(
            first.checkpoint_id,
            reason="provider_free_tool_surface_policy_migration",
        )

        second = self.runtime.begin_candidate(
            self.prepared("compatibility probe two", "current-policy"),
            effort="medium",
            workspace=self.workspace("current-policy-workspace"),
        )
        second_checkpoint = self.world.store.get_reasoner_checkpoint(second.checkpoint_id)
        second_ledger = self.world.store.get_reasoner_session(second_checkpoint.session_id)
        retired = self.world.store.get_reasoner_session(first_session_id)
        self.assertNotEqual(second_checkpoint.session_id, first_session_id)
        self.assertEqual(second_ledger.rotated_from_session_id, first_session_id)
        self.assertEqual(retired.status, SessionStatus.INVALIDATED)
        self.assertEqual(retired.status_reason, "session_compatibility_changed")
        self.assertEqual(self.port.provider_calls, 0)

    def test_stored_prompt_boundary_failure_is_typed_and_diagnostic(self) -> None:
        class NeverInvokedTransport:
            route = object()

            def invoke(self, *_args, **_kwargs):
                raise AssertionError("malformed stored prompt reached provider transport")

        transport = _StablePrefixStoredTransport(NeverInvokedTransport())
        with self.assertRaises(ProviderTransportError) as caught:
            transport.invoke("malformed prompt without authority marker")

        self.assertEqual(caught.exception.code, ErrorCode.REASONER_CONTRACT_INVALID)
        self.assertEqual(
            caught.exception.safe_diagnostics,
            ("stored_transport:prompt_split_invalid",),
        )
        self.assertEqual(caught.exception.external_provider_calls_observed, 0)


if __name__ == "__main__":
    unittest.main()
