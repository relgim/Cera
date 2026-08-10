from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from cera.cognition import (
    CharacterAutonomyMode,
    CognitionPlanV1,
    CognitionTurnContextV1,
)
from cera.cognition.provider import (
    CodexCognitionPlannerBackend,
    cognition_planner_route,
)
from cera.continuous.call_ledger import ContinuousProviderCallLedger
from cera.continuous.operation_evidence import ProviderOperationEvidenceStoreV1
from cera.pi_scene.context import initial_hanezawa_doorway_seed
from cera.pi_scene.contracts import SceneRoute
from cera.pi_scene.full_model_runtime import (
    BranchBoundCognitionPlannerBackend,
    cognition_thread_compatibility_sha256,
)
from cera.pi_scene.http_contracts import LeanSceneRequestControlsV1
from cera.pi_scene.review_store import LeanReviewState, LeanSceneTurnInputV1
from cera.pi_scene.world_workspace import BranchBoundWorldMcpFactory
from cera.providers.models import LiveProviderRoute
from cera.semantic_validation import (
    LUNA_VALIDATOR_BASE_INSTRUCTIONS,
    CodexLunaSemanticValidatorBackend,
    SemanticValidationVerdictV1,
    SemanticVerdict,
)
from cera.serialization import canonical_sha256, text_sha256, to_primitive
from scripts.run_pi_scene_lean_server import (
    FullModelProviderComponents,
    build_live_runtime,
    build_session_context_provider,
)
from tests.test_cognition_contracts import _plan
from tests.test_pi_scene_lean_v1 import FakePi


@dataclass
class _OfflineLifecycle:
    external_provider_boundary = False


@dataclass
class _OfflineStoredLunaLifecycle:
    external_provider_boundary = False
    base_instructions: str = LUNA_VALIDATOR_BASE_INSTRUCTIONS
    starts: list[str] = field(default_factory=list)
    archives: list[str] = field(default_factory=list)

    def start_stored_thread(self) -> str:
        thread_id = f"thread:luna-real-offline-{len(self.starts) + 1}"
        self.starts.append(thread_id)
        return thread_id

    def archive_stored_thread(self, thread_id: str) -> None:
        self.archives.append(thread_id)

    def stored_thread_is_selectable(self, thread_id: str) -> bool:
        return thread_id not in self.archives


class _OfflineLunaTransport:
    def __init__(self, _route: object, *, workspace: Path, runner: object) -> None:
        del workspace, runner

    def invoke(
        self,
        _prompt: str,
        *,
        output_schema: dict[str, object],
        mcp_binding: object,
        on_worker_started,
        on_worker_preflight,
        on_transport_invoke,
    ) -> object:
        del output_schema, mcp_binding
        on_worker_started()
        on_worker_preflight()
        on_transport_invoke()
        verdict = SemanticValidationVerdictV1(
            schema_version=SemanticValidationVerdictV1.SCHEMA_VERSION,
            verdict=SemanticVerdict.PASS,
            conflict=None,
        )
        payload = {"result": to_primitive(verdict)}
        return SimpleNamespace(
            output_text=json.dumps(payload, sort_keys=True),
            parsed_json=payload,
            receipt={"provider": "offline-luna"},
            operation_telemetry=None,
            tool_call_count=0,
            failed_tool_call_count=0,
            tool_names=(),
            tool_server_names=(),
        )


@dataclass
class _FakeCognitionBackend:
    world_mcp_factory: BranchBoundWorldMcpFactory
    plan_value: CognitionPlanV1 = field(default_factory=_plan)
    external_provider_boundary: bool = False
    route: LiveProviderRoute = field(default_factory=cognition_planner_route)
    starts: list[tuple[str, str]] = field(default_factory=list)
    calls: list[CognitionTurnContextV1] = field(default_factory=list)

    def start_stored_thread(self, *, base_instructions: str, profile: str) -> str:
        self.starts.append((base_instructions, profile))
        workspace = self.world_mcp_factory.workspace
        return f"thread:{workspace.world_id}:{workspace.branch_id}"

    def run_cognition_turn(
        self,
        *,
        thread_id: str,
        prompt: str,
        context: CognitionTurnContextV1,
        reference_scope: object,
    ) -> CognitionPlanV1:
        del thread_id, prompt, reference_scope
        self.calls.append(context)
        return self.plan_value

    def is_resumable(self, _thread_id: str) -> bool:
        return True


@dataclass
class _FakeLunaBackend:
    external_provider_boundary: bool = False
    starts: list[str] = field(default_factory=list)
    calls: list[str] = field(default_factory=list)
    archives: list[str] = field(default_factory=list)

    def start_fresh_thread(self, **_kwargs: str) -> str:
        thread_id = f"thread:luna-{len(self.starts) + 1}"
        self.starts.append(thread_id)
        return thread_id

    def run_validator_once(
        self,
        *,
        thread_id: str,
        request: object,
    ) -> SemanticValidationVerdictV1:
        del request
        self.calls.append(thread_id)
        return SemanticValidationVerdictV1(
            schema_version=SemanticValidationVerdictV1.SCHEMA_VERSION,
            verdict=SemanticVerdict.PASS,
            conflict=None,
        )

    def archive(self, thread_id: str) -> None:
        self.archives.append(thread_id)

    def is_resumable(self, _thread_id: str) -> bool:
        return False


class _Bridge:
    def __init__(self) -> None:
        self.aborts = 0

    def abort(self) -> None:
        self.aborts += 1


class _WorldMcpFactory:
    def __init__(self) -> None:
        self.bridges: list[_Bridge] = []

    def bridge(self, **_kwargs) -> _Bridge:
        bridge = _Bridge()
        self.bridges.append(bridge)
        return bridge


def _controls(session_id: str, *, effort: str = "medium") -> LeanSceneRequestControlsV1:
    return LeanSceneRequestControlsV1(
        schema_version=LeanSceneRequestControlsV1.SCHEMA_VERSION,
        session_id=session_id,
        character_autonomy="both",
        reasoning_effort=effort,
    )


class PiSceneFullModelLauncherTests(unittest.TestCase):
    def test_branch_cognition_backend_refreshes_mcp_per_retained_turn(self) -> None:
        factory = _WorldMcpFactory()
        context = SimpleNamespace(
            turn=SimpleNamespace(current_source_key="source:current")
        )
        reference_scope = SimpleNamespace(
            known_character_ids=("character:sakura_hanezawa",)
        )
        with tempfile.TemporaryDirectory() as temporary:
            backend = BranchBoundCognitionPlannerBackend(
                world_mcp_factory=factory,  # type: ignore[arg-type]
                lifecycle=object(),
                workspace=Path(temporary),
                call_ledger=object(),
            )
            with patch.object(
                CodexCognitionPlannerBackend,
                "run_cognition_turn",
                autospec=True,
            ) as delegated:
                delegated.side_effect = lambda current, **_kwargs: current.world_bridge
                first = backend.run_cognition_turn(
                    thread_id="thread:one",
                    prompt="one",
                    context=context,  # type: ignore[arg-type]
                    reference_scope=reference_scope,  # type: ignore[arg-type]
                )
                second = backend.run_cognition_turn(
                    thread_id="thread:one",
                    prompt="two",
                    context=context,  # type: ignore[arg-type]
                    reference_scope=reference_scope,  # type: ignore[arg-type]
                )
        self.assertIs(first, factory.bridges[0])
        self.assertIs(second, factory.bridges[1])
        self.assertIsNone(backend.world_bridge)
        self.assertEqual([bridge.aborts for bridge in factory.bridges], [0, 0])

        with patch.object(
            CodexCognitionPlannerBackend,
            "run_cognition_turn",
            side_effect=RuntimeError("provider failed"),
        ):
            with self.assertRaisesRegex(RuntimeError, "provider failed"):
                backend.run_cognition_turn(
                    thread_id="thread:one",
                    prompt="three",
                    context=context,  # type: ignore[arg-type]
                    reference_scope=reference_scope,  # type: ignore[arg-type]
                )
        self.assertEqual(factory.bridges[-1].aborts, 1)
        self.assertIsNone(backend.world_bridge)

    def test_actual_launcher_assembles_cognition_luna_and_auto_accepts_offline(self) -> None:
        backends: list[_FakeCognitionBackend] = []
        luna = _FakeLunaBackend()
        pi = FakePi()

        def build_backend(
            _session_id: str,
            _effort: str,
            turn: LeanSceneTurnInputV1,
            world_mcp_factory: BranchBoundWorldMcpFactory,
        ) -> _FakeCognitionBackend:
            self.assertEqual(
                (world_mcp_factory.workspace.world_id, world_mcp_factory.workspace.branch_id),
                (turn.world_id, turn.branch_id),
            )
            backend = _FakeCognitionBackend(world_mcp_factory=world_mcp_factory)
            backends.append(backend)
            return backend

        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.dict(
                "os.environ",
                {"CERA_PROVIDER_DISPATCH_DISABLED": "1"},
            ),
        ):
            root = Path(temporary) / "runtime"
            runtime = build_live_runtime(
                root,
                sol_ceiling=20,
                deepseek_ceiling=20,
                cognition_backend_factory=build_backend,
                provider_components=FullModelProviderComponents(
                    planner_lifecycle=_OfflineLifecycle(),
                    luna_backend=luna,
                    pi=pi,
                    external_provider_boundary=False,
                ),
            )
            self.addCleanup(runtime.close)
            context = build_session_context_provider(
                runtime.store,
                initial_hanezawa_doorway_seed(),
                workspace_resolver=runtime.world_resolver,
            )
            source = "Ted asks Sakura to open the door."

            first = context(
                SceneRoute.ORDINARY,
                source,
                ({"role": "user", "content": source},),
                _controls("chat-launcher-a"),
            )
            accepted_one = runtime.coordinator.start_ordinary(first)
            second = context(
                accepted_one.candidate.route,
                source,
                ({"role": "user", "content": source},),
                _controls("chat-launcher-a"),
            )
            accepted_two = runtime.coordinator.start_ordinary(second)
            separate = context(
                accepted_two.candidate.route,
                source,
                ({"role": "user", "content": source},),
                _controls("chat-launcher-b", effort="xhigh"),
            )
            accepted_three = runtime.coordinator.start_ordinary(separate)

            self.assertTrue(
                all(
                    review.state is LeanReviewState.ACCEPTED
                    for review in (accepted_one, accepted_two, accepted_three)
                )
            )
            self.assertEqual(len(backends), 2)
            self.assertEqual(len(backends[0].starts), 1)
            self.assertEqual(len(backends[0].calls), 2)
            self.assertEqual(len(backends[1].starts), 1)
            self.assertEqual(
                (backends[1].route.reasoning_effort, backends[1].route.timeout_seconds),
                ("xhigh", 600),
            )
            self.assertNotEqual(
                backends[0].world_mcp_factory.workspace.world_id,
                backends[1].world_mcp_factory.workspace.world_id,
            )
            self.assertTrue(
                all(
                    call.autonomy_mode is CharacterAutonomyMode.BOTH
                    for backend in backends
                    for call in backend.calls
                )
            )
            self.assertEqual(luna.calls, luna.starts)
            self.assertEqual(luna.archives, luna.starts)
            self.assertEqual(len(luna.calls), 3)
            self.assertEqual(runtime.sol_ledger.dispatched_call_count, 0)
            self.assertEqual(runtime.deepseek_ledger.operation_count, 0)
            self.assertEqual(
                runtime.store.load_head(
                    world_id=first.world_id,
                    branch_id=first.branch_id,
                ).generation,
                2,
            )
            self.assertEqual(
                runtime.store.load_head(
                    world_id=separate.world_id,
                    branch_id=separate.branch_id,
                ).generation,
                1,
            )
            self.assertEqual(len(cognition_thread_compatibility_sha256()), 64)

    def test_launcher_real_luna_binds_provider_evidence_and_archives_offline(self) -> None:
        backends: list[_FakeCognitionBackend] = []

        def build_backend(
            _session_id: str,
            _effort: str,
            _turn: LeanSceneTurnInputV1,
            world_mcp_factory: BranchBoundWorldMcpFactory,
        ) -> _FakeCognitionBackend:
            backend = _FakeCognitionBackend(world_mcp_factory=world_mcp_factory)
            backends.append(backend)
            return backend

        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.dict("os.environ", {"CERA_PROVIDER_DISPATCH_DISABLED": "1"}),
            patch(
                "cera.semantic_validation.provider.CodexSDKTransport",
                _OfflineLunaTransport,
            ),
        ):
            root = Path(temporary).resolve()
            evidence_root = root / "luna_evidence"
            evidence = ProviderOperationEvidenceStoreV1(
                evidence_root,
                stage="pi_scene_launcher_luna_test",
            )
            luna_ledger = ContinuousProviderCallLedger(root / "luna_calls.jsonl")
            luna_lifecycle = _OfflineStoredLunaLifecycle()
            luna = CodexLunaSemanticValidatorBackend(
                lifecycle=luna_lifecycle,  # type: ignore[arg-type]
                workspace=root / "luna_workspace",
                call_ledger=luna_ledger,
                operation_evidence=evidence,
            )
            luna.external_provider_boundary = False
            runtime = build_live_runtime(
                root / "runtime",
                sol_ceiling=20,
                deepseek_ceiling=20,
                cognition_backend_factory=build_backend,
                provider_components=FullModelProviderComponents(
                    planner_lifecycle=_OfflineLifecycle(),
                    luna_backend=luna,
                    pi=FakePi(),
                    external_provider_boundary=False,
                ),
            )
            self.addCleanup(runtime.close)
            context = build_session_context_provider(
                runtime.store,
                initial_hanezawa_doorway_seed(),
                workspace_resolver=runtime.world_resolver,
            )
            source = "Ted asks Sakura to open the door."
            turn = context(
                SceneRoute.ORDINARY,
                source,
                ({"role": "user", "content": source},),
                _controls("chat-launcher-real-luna"),
            )

            accepted = runtime.coordinator.start_ordinary(turn)

            self.assertIs(accepted.state, LeanReviewState.ACCEPTED)
            self.assertEqual(len(backends), 1)
            self.assertEqual(luna_ledger.dispatched_call_count, 1)
            self.assertEqual(luna_lifecycle.starts, luna_lifecycle.archives)
            self.assertEqual(len(luna_lifecycle.starts), 1)
            thread_id = luna_lifecycle.starts[0]
            self.assertFalse(luna_lifecycle.stored_thread_is_selectable(thread_id))
            self.assertFalse(luna.external_provider_boundary)
            self.assertFalse(luna_lifecycle.external_provider_boundary)

            snapshot = evidence.snapshot()
            self.assertEqual(len(snapshot["calls"]), 1)
            call = snapshot["calls"][0]
            self.assertTrue(call["terminal"])
            call_root = evidence_root / call["call_id"]
            pre_dispatch = json.loads(
                (call_root / "PRE_DISPATCH.json").read_text(encoding="utf-8")
            )
            archival = json.loads(
                (call_root / "ARCHIVAL.json").read_text(encoding="utf-8")
            )
            validation = accepted.semantic_validation
            self.assertIsNotNone(validation)
            assert validation is not None
            expected_turn = (
                "semantic-validation:"
                f"{canonical_sha256(validation.request)}"
            )
            self.assertEqual(pre_dispatch["turn"], expected_turn)
            self.assertRegex(
                pre_dispatch["turn"],
                r"\Asemantic-validation:[0-9a-f]{64}\Z",
            )
            self.assertNotIn(source, pre_dispatch["turn"])
            self.assertNotIn(accepted.candidate.story_text, pre_dispatch["turn"])
            self.assertEqual(
                pre_dispatch["thread_identity_sha256"],
                text_sha256(thread_id),
            )
            self.assertEqual(archival["call_id"], call["call_id"])
            self.assertEqual(
                archival["thread_identity_sha256"],
                pre_dispatch["thread_identity_sha256"],
            )
            self.assertTrue(archival["archived"])
            self.assertFalse(archival["resumable"])
            self.assertTrue(archival["provider_dispatched"])
            self.assertEqual(
                archival["disposition"],
                "archived_after_candidate",
            )


if __name__ == "__main__":
    unittest.main()
