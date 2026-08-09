from __future__ import annotations

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from cera.continuous.call_ledger import ContinuousProviderCallLedger
from cera.errors import StateConflictError
from cera.pi_scene.contracts import SceneRoute
from cera.pi_scene.operation_ledger import PiProviderOperationLedger
from cera.pi_scene.pi_adapter import PiSceneAdapter, PiSceneInvocationV1
from cera.pi_scene.writer_view import MaterializedWriterViewV1
from cera.provider_dispatch_guard import (
    PROVIDER_DISPATCH_DISABLED_ENV,
    assert_provider_dispatch_allowed,
    provider_dispatch_disabled,
)
from cera.reasoner_session.codex_stored import OpenAICodexStoredThreadBackend
from cera.sequence_first.contracts import ProviderReferenceScopeV1
from cera.sequence_first.prompting import PLANNER_BASE_INSTRUCTIONS, PLANNER_PROFILE
from cera.sequence_first.provider import SequenceFirstPlannerCodexBackend


class ProviderDispatchGuardTests(unittest.TestCase):
    def test_unset_guard_preserves_normal_behavior(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(PROVIDER_DISPATCH_DISABLED_ENV, None)
            self.assertFalse(provider_dispatch_disabled())
            self.assertIsNone(assert_provider_dispatch_allowed("test.normal-runtime"))

    def test_malformed_guard_value_fails_closed(self) -> None:
        with patch.dict(
            os.environ,
            {PROVIDER_DISPATCH_DISABLED_ENV: "true"},
            clear=False,
        ):
            with self.assertRaisesRegex(
                StateConflictError,
                "must be unset or exactly 1",
            ):
                provider_dispatch_disabled()

    def test_sequence_first_codex_is_blocked_before_thread_or_ledger(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            lifecycle = object.__new__(OpenAICodexStoredThreadBackend)
            ledger = ContinuousProviderCallLedger(
                root / "provider_calls.jsonl",
                maximum_calls=1,
            )
            backend = SequenceFirstPlannerCodexBackend(
                lifecycle=lifecycle,
                workspace=root / "planner-workspace",
                call_ledger=ledger,
            )
            with (
                patch.dict(
                    os.environ,
                    {PROVIDER_DISPATCH_DISABLED_ENV: "1"},
                    clear=False,
                ),
                patch.object(
                    OpenAICodexStoredThreadBackend,
                    "start_stored_thread",
                ) as start_thread,
                patch(
                    "cera.sequence_first.provider.CodexSDKTransport",
                ) as transport,
            ):
                with self.assertRaisesRegex(
                    StateConflictError,
                    "external provider dispatch is disabled",
                ):
                    backend.start_stored_thread(
                        base_instructions=PLANNER_BASE_INSTRUCTIONS,
                        profile=PLANNER_PROFILE,
                    )
                with self.assertRaisesRegex(
                    StateConflictError,
                    "external provider dispatch is disabled",
                ):
                    backend.run_planner_turn(
                        thread_id="provider-thread",
                        prompt="Plan this turn.",
                        reference_scope=ProviderReferenceScopeV1(
                            known_character_ids=(),
                            evidence_keys=(),
                            protected_source_claim_keys=(),
                            approved_target_keys=(),
                        ),
                    )

            start_thread.assert_not_called()
            transport.assert_not_called()
            self.assertEqual(backend._operation_index, 0)
            self.assertEqual(ledger.events, ())
            self.assertFalse(ledger.path.exists())
            self.assertFalse((root / "planner-workspace").exists())

    def test_pi_is_blocked_before_process_or_operation_ledger(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            executable = root / "pi.cmd"
            extension = root / "cera-scene-view.ts"
            executable.write_text("@echo off\n", encoding="utf-8")
            extension.write_text("export {};\n", encoding="utf-8")
            ledger = PiProviderOperationLedger(
                (root / "provider_operations.jsonl").resolve(),
                maximum_operations=6,
            )
            adapter = PiSceneAdapter(
                pi_executable=executable,
                extension_path=extension,
                pi_version="test",
                operation_ledger=ledger,
            )
            process_calls: list[object] = []

            def process_runner(*args, **kwargs):
                process_calls.append((args, kwargs))
                raise AssertionError("provider process runner was reached")

            # Keep the adapter's production-boundary classification while
            # replacing the runner with an observation-only test callback.
            adapter._process_runner = process_runner
            request = PiSceneInvocationV1(
                route=SceneRoute.ORDINARY,
                purpose="writer",
                view=MaterializedWriterViewV1(
                    root=root / "missing-view",
                    manifest_path=root / "missing-view" / "MANIFEST.json",
                    manifest_sha256="0" * 64,
                    file_count=0,
                    purpose="writer",
                ),
                prompt="Write the scene.",
                candidate_id="candidate-guarded",
                session_dir=root / "session",
            )
            with patch.dict(
                os.environ,
                {PROVIDER_DISPATCH_DISABLED_ENV: "1"},
                clear=False,
            ):
                with self.assertRaisesRegex(
                    StateConflictError,
                    "external provider dispatch is disabled",
                ):
                    adapter.invoke(request)

            self.assertEqual(process_calls, [])
            self.assertEqual(ledger.events, ())
            self.assertFalse(ledger.path.exists())
            self.assertFalse(request.session_dir.exists())


if __name__ == "__main__":
    unittest.main()
