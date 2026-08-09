from __future__ import annotations

import io
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch

from cera.continuous.call_ledger import ContinuousProviderCallLedger
from cera.continuous.provider import (
    CodexContinuousCompactValidatorPort,
    CodexContinuousPlannerPort,
    CodexContinuousReaderPort,
    CodexContinuousValidatorPort,
    DeepSeekContinuousComposerPort,
    continuous_planner_route,
    continuous_validator_route,
)
from cera.continuous.sessions import ContinuousSessionCoordinator
from cera.errors import StateConflictError
from cera.pi_scene.cognition_planner import RetainedCognitionPlannerAdapter
from cera.pi_scene.pi_adapter import _run_process
from cera.pi_scene.runtime import LeanPiSceneCoordinator
from cera.provider_dispatch_guard import (
    PROVIDER_DISPATCH_DISABLED_ENV,
    assert_provider_dispatch_allowed,
    is_external_provider_boundary,
)
from cera.providers.codex import CodexSDKTransport
from cera.providers.codex_exec import CodexExecRunner
from cera.providers.deepseek import DeepSeekChatTransport, DeepSeekMessage
from cera.providers.models import ProviderOutputMode
from cera.providers.routes import (
    codex_cli_realization_verifier_candidate,
    codex_reasoner_candidate,
    deepseek_composer_candidate,
)
from cera.reasoner_session.codex_stored import OpenAICodexStoredThreadBackend
from cera.reasoner_session.runtime import NativeStoredReasonerSessionRuntime
from cera.semantic_validation.session import FreshLunaValidatorSession
from cera.sequence_first.runtime import SequenceFirstCoordinator
from cera.sequence_first.sessions import FreshCandidateValidatorSession


class _ExternalTransport:
    external_provider_boundary = True

    def __init__(self, route: object) -> None:
        self.route = route
        self.calls = 0

    def invoke(self, *args: object, **kwargs: object) -> object:
        self.calls += 1
        raise AssertionError("external transport was reached")


class _ExternalFactory:
    external_provider_boundary = True

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self) -> object:
        self.calls += 1
        raise AssertionError("external transport factory was reached")


class _ExternalSession:
    external_provider_boundary = True

    def __init__(self) -> None:
        self.calls = 0

    def plan(self, context: object) -> object:
        self.calls += 1
        raise AssertionError("external session was reached")


class _CountingEnvironment(dict[str, str]):
    def __init__(self) -> None:
        super().__init__({"DEEPSEEK_API_KEY": "must-not-be-read"})
        self.lookups = 0

    def get(self, key: str, default: str | None = None) -> str | None:
        self.lookups += 1
        return super().get(key, default)


class ProviderBoundaryClassifierTests(unittest.TestCase):
    def test_boundary_markers_are_literal_and_missing_fails_closed(self) -> None:
        class Offline:
            external_provider_boundary = False

        class LegacyCallable:
            def external_provider_boundary_active(self) -> bool:
                return False

        self.assertFalse(is_external_provider_boundary(Offline()))
        self.assertTrue(is_external_provider_boundary(object()))
        self.assertTrue(is_external_provider_boundary(LegacyCallable()))
        for malformed in (None, 0, "false", object()):
            owner = SimpleNamespace(external_provider_boundary=malformed)
            with self.subTest(marker=repr(malformed)), self.assertRaises(StateConflictError):
                is_external_provider_boundary(owner)

    def test_assert_rejects_falsey_non_boolean_classifications(self) -> None:
        for malformed in (None, 0, ""):
            with self.subTest(marker=repr(malformed)), self.assertRaises(StateConflictError):
                assert_provider_dispatch_allowed(
                    "tests.malformed_boundary",
                    external_provider_boundary=malformed,  # type: ignore[arg-type]
                )


class ConcreteProviderBoundaryTests(unittest.TestCase):
    def test_deepseek_blocks_before_credentials_callback_or_http(self) -> None:
        environment = _CountingEnvironment()
        opener = Mock(side_effect=AssertionError("HTTP opener was reached"))
        callback = Mock()
        transport = DeepSeekChatTransport(
            deepseek_composer_candidate(model="deepseek-v4-flash"),
            opener=opener,
            environment=environment,
        )
        with (
            patch.dict(
                os.environ,
                {PROVIDER_DISPATCH_DISABLED_ENV: "1"},
                clear=False,
            ),
            self.assertRaises(StateConflictError),
        ):
            transport.invoke(
                (DeepSeekMessage("user", "provider-free"),),
                output_mode=ProviderOutputMode.JSON_OBJECT,
                thinking_enabled=False,
                on_transport_invoke=callback,
            )
        self.assertEqual(environment.lookups, 0)
        opener.assert_not_called()
        callback.assert_not_called()

    def test_codex_sdk_and_exec_block_before_runner_or_workspace_mutation(self) -> None:
        runner = _ExternalTransport(codex_reasoner_candidate())
        with TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            sdk_workspace = root / "sdk-workspace"
            sdk_workspace.mkdir()
            transport = CodexSDKTransport(
                codex_reasoner_candidate(),
                workspace=sdk_workspace,
                runner=runner,  # type: ignore[arg-type]
            )
            callback = Mock()
            with (
                patch.dict(
                    os.environ,
                    {PROVIDER_DISPATCH_DISABLED_ENV: "1"},
                    clear=False,
                ),
                self.assertRaises(StateConflictError),
            ):
                transport.invoke(
                    "provider-free",
                    output_schema={"type": "object"},
                    on_transport_invoke=callback,
                )
            self.assertEqual(runner.calls, 0)
            callback.assert_not_called()
            self.assertEqual(tuple(sdk_workspace.iterdir()), ())

            exec_workspace = root / "exec-workspace"
            exec_workspace.mkdir()
            exec_runner = CodexExecRunner()
            with (
                patch.dict(
                    os.environ,
                    {PROVIDER_DISPATCH_DISABLED_ENV: "1"},
                    clear=False,
                ),
                patch(
                    "cera.providers.codex_exec._resolve_and_validate_codex_binary"
                ) as resolve_binary,
                self.assertRaises(StateConflictError),
            ):
                exec_runner.run(
                    route=codex_cli_realization_verifier_candidate(),
                    prompt="provider-free",
                    output_schema={"type": "object"},
                    workspace=exec_workspace,
                    mcp_binding=None,
                )
            resolve_binary.assert_not_called()
            self.assertEqual(exec_runner.process_launch_count, 0)
            self.assertEqual(exec_runner.request_submission_count, 0)
            self.assertEqual(tuple(exec_workspace.iterdir()), ())

    def test_stored_backend_blocks_constructor_and_every_sdk_lifecycle_call(self) -> None:
        codex = Mock()
        with (
            patch.dict(
                os.environ,
                {PROVIDER_DISPATCH_DISABLED_ENV: "1"},
                clear=False,
            ),
            patch("cera.reasoner_session.codex_stored.uuid4") as uuid4,
            patch(
                "cera.reasoner_session.codex_stored.version",
                return_value="0.144.4",
            ) as package_version,
            self.assertRaises(StateConflictError),
        ):
            OpenAICodexStoredThreadBackend(
                codex=codex,
                model="gpt-5.6-sol",
                cwd="C:/provider-free",
                base_instructions="stable",
            )
        uuid4.assert_not_called()
        package_version.assert_called_once_with("openai-codex")
        codex.assert_not_called()

        backend = object.__new__(OpenAICodexStoredThreadBackend)
        backend.codex = codex
        calls = (
            backend.start_stored_thread,
            lambda: backend.fork_stored_thread("thread-parent"),
            lambda: backend.resume_stored_thread("thread-one"),
            lambda: backend.append_model_visible_context("thread-one", "accepted delta"),
            lambda: backend.archive_stored_thread("thread-one"),
            lambda: backend.stored_thread_is_selectable("thread-one"),
            lambda: backend._materialize_thread("thread-one", role="planner"),
        )
        with patch.dict(
            os.environ,
            {PROVIDER_DISPATCH_DISABLED_ENV: "1"},
            clear=False,
        ):
            for index, call in enumerate(calls):
                with self.subTest(call_index=index), self.assertRaises(StateConflictError):
                    call()
        self.assertEqual(codex.mock_calls, [])

    def test_worker_entrypoints_block_before_stdin_or_sdk_import(self) -> None:
        from cera.providers import (
            codex_session_worker,
            codex_stored_turn_worker,
            codex_worker,
        )

        for worker in (codex_worker, codex_stored_turn_worker, codex_session_worker):
            stream = Mock(spec=io.TextIOBase)
            with (
                self.subTest(worker=worker.__name__),
                patch.dict(
                    os.environ,
                    {PROVIDER_DISPATCH_DISABLED_ENV: "1"},
                    clear=False,
                ),
                patch.object(worker.sys, "stdin", stream),
                self.assertRaises(StateConflictError),
            ):
                worker.main()
            stream.read.assert_not_called()
            stream.readline.assert_not_called()

    def test_pi_process_boundary_blocks_before_command_or_popen(self) -> None:
        with (
            patch.dict(
                os.environ,
                {PROVIDER_DISPATCH_DISABLED_ENV: "1"},
                clear=False,
            ),
            patch("cera.pi_scene.pi_adapter._native_command") as native_command,
            patch("cera.pi_scene.pi_adapter.subprocess.Popen") as popen,
            self.assertRaises(StateConflictError),
        ):
            _run_process(
                ("pi", "--print"),
                Path.cwd(),
                {},
                1,
                lambda line: None,
            )
        native_command.assert_not_called()
        popen.assert_not_called()

    def test_native_stored_runtime_blocks_before_sdk_context(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            with (
                patch.dict(
                    os.environ,
                    {PROVIDER_DISPATCH_DISABLED_ENV: "1"},
                    clear=False,
                ),
                self.assertRaises(StateConflictError),
            ):
                NativeStoredReasonerSessionRuntime(
                    object(),
                    repository_root=root,
                )


class ContinuousBoundaryTests(unittest.TestCase):
    def test_every_continuous_port_blocks_before_factory_index_or_ledger(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            cases: list[tuple[object, object, _ExternalTransport | None]] = []

            planner_transport = _ExternalTransport(continuous_planner_route())
            planner_ledger = ContinuousProviderCallLedger(root / "planner.jsonl")
            planner = CodexContinuousPlannerPort(
                planner_transport,  # type: ignore[arg-type]
                call_ledger=planner_ledger,
            )
            cases.append((planner, lambda: planner.plan("plan"), planner_transport))

            validator_transport = _ExternalTransport(
                continuous_validator_route(model="gpt-5.6-sol", effort="medium")
            )
            validator_ledger = ContinuousProviderCallLedger(root / "validator.jsonl")
            validator = CodexContinuousValidatorPort(
                validator_transport,  # type: ignore[arg-type]
                call_ledger=validator_ledger,
            )
            cases.append(
                (
                    validator,
                    lambda: validator.validate("validate", writer_story_text=None),
                    validator_transport,
                )
            )

            compact_transport = _ExternalTransport(
                continuous_validator_route(model="gpt-5.6-sol", effort="medium")
            )
            compact_ledger = ContinuousProviderCallLedger(root / "compact.jsonl")
            compact = CodexContinuousCompactValidatorPort(
                compact_transport,  # type: ignore[arg-type]
                call_ledger=compact_ledger,
            )
            cases.append(
                (
                    compact,
                    lambda: compact.validate("compact", writer_story_text=None),
                    compact_transport,
                )
            )

            composer_transport = _ExternalTransport(
                deepseek_composer_candidate(model="deepseek-v4-flash")
            )
            composer_ledger = ContinuousProviderCallLedger(root / "composer.jsonl")
            composer = DeepSeekContinuousComposerPort(
                composer_transport,  # type: ignore[arg-type]
                call_ledger=composer_ledger,
            )
            cases.append((composer, lambda: composer.compose("compose"), composer_transport))

            factory = _ExternalFactory()
            reader_ledger = ContinuousProviderCallLedger(root / "reader.jsonl")
            reader = CodexContinuousReaderPort(
                factory,  # type: ignore[arg-type]
                call_ledger=reader_ledger,
            )
            cases.append(
                (
                    reader,
                    lambda: reader.review("review", writer_story_text="story"),
                    None,
                )
            )

            with patch.dict(
                os.environ,
                {PROVIDER_DISPATCH_DISABLED_ENV: "1"},
                clear=False,
            ):
                for port, invoke, transport in cases:
                    with (
                        self.subTest(port=type(port).__name__),
                        self.assertRaises(StateConflictError),
                    ):
                        invoke()
                    self.assertEqual(port._operation_index, 0)
                    self.assertEqual(port.call_ledger.events, ())
                    self.assertFalse(port.call_ledger.path.exists())
                    if transport is not None:
                        self.assertEqual(transport.calls, 0)
            self.assertEqual(factory.calls, 0)


class HighLevelSessionBoundaryTests(unittest.TestCase):
    def test_cognition_adapter_blocks_before_evidence_and_session(self) -> None:
        session = _ExternalSession()
        evidence = Mock()
        adapter = RetainedCognitionPlannerAdapter(
            session,  # type: ignore[arg-type]
            operation_evidence=evidence,
        )
        request = SimpleNamespace(request_controls=object())
        with (
            patch.dict(
                os.environ,
                {PROVIDER_DISPATCH_DISABLED_ENV: "1"},
                clear=False,
            ),
            self.assertRaises(StateConflictError),
        ):
            adapter.plan(request)  # type: ignore[arg-type]
        self.assertEqual(adapter._turn_index, 0)
        self.assertEqual(session.calls, 0)
        evidence.begin_turn.assert_not_called()

    def test_luna_session_blocks_before_consuming_single_use_state(self) -> None:
        backend = Mock()
        backend.external_provider_boundary = True
        session = FreshLunaValidatorSession(backend, "luna-thread")
        with (
            patch.dict(
                os.environ,
                {PROVIDER_DISPATCH_DISABLED_ENV: "1"},
                clear=False,
            ),
            self.assertRaises(StateConflictError),
        ):
            session.validate(None, None)  # type: ignore[arg-type]
        self.assertFalse(session._used)
        backend.run_validator_once.assert_not_called()

    def test_sequence_validator_blocks_before_consuming_single_use_state(self) -> None:
        backend = Mock()
        backend.external_provider_boundary = True
        session = FreshCandidateValidatorSession(backend, "validator-thread")
        with (
            patch.dict(
                os.environ,
                {PROVIDER_DISPATCH_DISABLED_ENV: "1"},
                clear=False,
            ),
            self.assertRaises(StateConflictError),
        ):
            session.validate(None)  # type: ignore[arg-type]
        self.assertFalse(session._used)
        backend.run_validator_once.assert_not_called()

    def test_continuous_archive_blocks_before_terminal_state_or_port_call(self) -> None:
        coordinator = object.__new__(ContinuousSessionCoordinator)
        coordinator._terminally_archived = False
        coordinator.handle = SimpleNamespace(provider_thread_id_sha256="0" * 64)
        coordinator.port = Mock(external_provider_boundary=True)
        coordinator._thread_lineage = Mock()
        with (
            patch.dict(
                os.environ,
                {PROVIDER_DISPATCH_DISABLED_ENV: "1"},
                clear=False,
            ),
            self.assertRaises(StateConflictError),
        ):
            coordinator.archive_and_verify_terminal("provider-free")
        self.assertFalse(coordinator._terminally_archived)
        coordinator.port.archive.assert_not_called()
        coordinator._thread_lineage.record_archive.assert_not_called()

    def test_sequence_coordinator_blocks_before_operation_evidence(self) -> None:
        coordinator = object.__new__(SequenceFirstCoordinator)
        coordinator._planner = Mock(external_provider_boundary=True)
        coordinator._writer = Mock(external_provider_boundary=False)
        coordinator._validator_factory = Mock(external_provider_boundary=False)
        coordinator._reader = Mock(external_provider_boundary=False)
        coordinator._operation_evidence = Mock()
        request = SimpleNamespace(semantic_input=object())
        with (
            patch.dict(
                os.environ,
                {PROVIDER_DISPATCH_DISABLED_ENV: "1"},
                clear=False,
            ),
            self.assertRaises(StateConflictError),
        ):
            coordinator.generate(request)  # type: ignore[arg-type]
        coordinator._operation_evidence.begin_turn.assert_not_called()
        coordinator._planner.plan.assert_not_called()

    def test_pi_recording_blocks_before_session_promotion(self) -> None:
        coordinator = object.__new__(LeanPiSceneCoordinator)
        coordinator.pi = Mock(external_provider_boundary=True)
        coordinator.store = Mock()
        with (
            patch.dict(
                os.environ,
                {PROVIDER_DISPATCH_DISABLED_ENV: "1"},
                clear=False,
            ),
            self.assertRaises(StateConflictError),
        ):
            coordinator._record_after_accept(None)  # type: ignore[arg-type]
        coordinator.store.promote_pi_session.assert_not_called()

    def test_live_pi_builder_blocks_before_runtime_root_creation(self) -> None:
        from scripts.run_pi_scene_lean_server import build_live_runtime

        with TemporaryDirectory() as temporary:
            runtime_root = Path(temporary).resolve() / "runtime"
            with (
                patch.dict(
                    os.environ,
                    {PROVIDER_DISPATCH_DISABLED_ENV: "1"},
                    clear=False,
                ),
                self.assertRaises(StateConflictError),
            ):
                build_live_runtime(
                    runtime_root,
                    sol_ceiling=1,
                    deepseek_ceiling=1,
                )
            self.assertFalse(runtime_root.exists())

    def test_live_session_runners_block_before_sdk_context_or_counters(self) -> None:
        from scripts.run_branch_bound_reasoner_live_five import ForkingSessionRunner
        from scripts.run_codex_branch_session_ab import InProcessSessionRunner

        with TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            runners = (
                InProcessSessionRunner(reuse_thread=True, workspace=root),
                ForkingSessionRunner(workspace=root, stable_instructions="stable"),
            )
            with patch.dict(
                os.environ,
                {PROVIDER_DISPATCH_DISABLED_ENV: "1"},
                clear=False,
            ):
                for runner in runners:
                    with (
                        self.subTest(runner=type(runner).__name__),
                        self.assertRaises(StateConflictError),
                    ):
                        runner.__enter__()
                    self.assertIsNone(runner._codex_context)
                    self.assertIsNone(runner.codex)


if __name__ == "__main__":
    unittest.main()
