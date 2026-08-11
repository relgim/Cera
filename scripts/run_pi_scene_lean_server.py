"""Launch or smoke-test the non-production Queue 0072 Pi Scene route."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import socket
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence
from contextlib import ExitStack
from dataclasses import dataclass, replace
from http.cookiejar import CookieJar
from pathlib import Path
from threading import RLock, Thread
from typing import Any, cast
from urllib.request import HTTPCookieProcessor, Request, build_opener, urlopen

from cera.cognition.prompting import COGNITION_PLANNER_BASE_INSTRUCTIONS
from cera.continuous.call_ledger import ContinuousProviderCallLedger
from cera.continuous.operation_evidence import ProviderOperationEvidenceStoreV1
from cera.errors import ContractValidationError, StateConflictError
from cera.pi_scene.adult_operation_store import (
    ProtectedAdultOperationController,
    ProtectedAdultOperationStore,
)
from cera.pi_scene.cognition_planner import RetainedCognitionPlannerAdapter
from cera.pi_scene.context import (
    AcceptedBranchContextProvider,
    PiSceneContextSeedV1,
    initial_hana_seed,
    initial_hanezawa_doorway_seed,
)
from cera.pi_scene.contracts import (
    LeanAcceptedTurnReceiptV1,
    RecordingStatus,
    SceneRoute,
)
from cera.pi_scene.full_model_adult_runtime import FullModelAdultRuntimeFactory
from cera.pi_scene.full_model_controller import FullModelSceneController
from cera.pi_scene.full_model_runtime import (
    BranchBoundCognitionPlannerBackend,
    CognitionSessionFactory,
    PathBudgetedCognitionDebugLog,
    RetrievalDirectedCognitionPlanner,
    default_cognition_session_factory,
)
from cera.pi_scene.http import (
    PiSceneHttpAdapter,
    PiSceneServerConfigV1,
    build_pi_scene_server,
)
from cera.pi_scene.http_contracts import (
    PI_SCENE_ADULT_MODEL,
    PI_SCENE_AUTO_MODEL,
    PI_SCENE_ORDINARY_MODEL,
    PI_SCENE_PROFILE,
    LeanSceneRequestControlsV1,
)
from cera.pi_scene.operation_ledger import PiProviderOperationLedger
from cera.pi_scene.pi_adapter import PiSceneAdapter
from cera.pi_scene.planner_state import PlannerThreadStateStore
from cera.pi_scene.provider_stage_retry_assembly import (
    OrdinaryStageProviderPortsV1,
    ProviderStageRetryProductionAssemblyV1,
    build_provider_stage_retry_production_assembly,
)
from cera.pi_scene.readable_debug import ReadablePiSceneDebugLog
from cera.pi_scene.request_journal import PiSceneRequestBindingV1
from cera.pi_scene.review_store import LeanSceneTurnInputV1
from cera.pi_scene.runtime import (
    LeanPiSceneCoordinator,
    OrdinaryProviderStageRetryPort,
    repair_latest_ordinary_recording_from_output,
)
from cera.pi_scene.sillytavern_isolation import (
    isolated_sillytavern_command,
    stage_isolated_sillytavern,
    verify_isolated_sillytavern,
)
from cera.pi_scene.store import LeanSceneStore
from cera.pi_scene.transport_retry import PiSceneProviderLedgerSnapshotV1
from cera.pi_scene.world_runtime import (
    PiSceneChatWorldResolver,
    new_chat_semantic_scope,
)
from cera.pi_scene.world_workspace import PiSceneWorldWorkspaceManager
from cera.pi_scene.writer_view import WriterViewMaterializer
from cera.provider_dispatch_guard import (
    assert_provider_dispatch_allowed,
    is_external_provider_boundary,
)
from cera.providers.codex_runtime_policy import (
    codex_app_server_config_overrides,
    codex_app_server_environment,
    validate_codex_app_server_configuration,
    validate_codex_mcp_server_status,
)
from cera.reader_validation import (
    SOL_READER_BASE_INSTRUCTIONS,
    CodexSolReaderBackend,
    FreshSolReaderFactory,
)
from cera.reasoner_session.codex_stored import OpenAICodexStoredThreadBackend
from cera.semantic_validation import (
    LUNA_VALIDATOR_BASE_INSTRUCTIONS,
    CodexLunaSemanticValidatorBackend,
    FreshLunaValidatorFactory,
)
from cera.sequence_first.prompting import PLANNER_BASE_INSTRUCTIONS, PLANNER_PROFILE
from cera.sequence_first.provider import SequenceFirstPlannerCodexBackend
from cera.sequence_first.sessions import PersistentPlannerSession
from cera.serialization import canonical_bytes, canonical_sha256, re_is_sha256, text_sha256

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PI = Path(r"C:\Users\Ted\AppData\Roaming\npm\pi.cmd")
DEFAULT_EXTENSION = ROOT / "integrations" / "pi" / "cera-scene-view.ts"
DEFAULT_SILLYTAVERN = Path(r"E:\AIChatBot\SillyTavern")


@dataclass(slots=True)
class LivePiSceneRuntime:
    stack: ExitStack
    coordinator: LeanPiSceneCoordinator
    store: LeanSceneStore
    sol_ledger: ContinuousProviderCallLedger
    deepseek_ledger: PiProviderOperationLedger
    readable_debug: ReadablePiSceneDebugLog
    world_resolver: PiSceneChatWorldResolver
    full_model_controller: FullModelSceneController
    provider_stage_retry: ProviderStageRetryProductionAssemblyV1 | None
    transport_retry_reinitializer: Callable[
        [PiSceneRequestBindingV1, LeanSceneTurnInputV1, str, str],
        None,
    ]
    transport_provider_ledger_snapshot: Callable[[], PiSceneProviderLedgerSnapshotV1]
    transport_retry_active_thread_snapshot: Callable[
        [PiSceneRequestBindingV1, LeanSceneTurnInputV1, str],
        str | None,
    ]
    transport_retry_fresh_thread_initializer: Callable[
        [PiSceneRequestBindingV1, LeanSceneTurnInputV1, str],
        str,
    ]
    transport_completed_planner_abandoner: Callable[
        [PiSceneRequestBindingV1, LeanSceneTurnInputV1, str, str],
        None,
    ]

    def close(self) -> None:
        self.stack.close()


PlannerSessionFactory = Callable[
    [str, str, SequenceFirstPlannerCodexBackend],
    PersistentPlannerSession,
]
PlannerBackendFactory = Callable[
    [str, str, LeanSceneTurnInputV1],
    Any,
]
CognitionBackendFactory = Callable[
    [str, str, LeanSceneTurnInputV1, Any],
    Any,
]


@dataclass(frozen=True, slots=True)
class FullModelProviderComponents:
    """Explicit provider seams used only by provider-free launcher tests.

    Production construction leaves this unset and creates the concrete Codex,
    Luna, and Pi adapters below.  Offline fakes must opt out of the external
    dispatch classification explicitly.
    """

    planner_lifecycle: Any
    luna_backend: Any
    reader_backend: Any
    pi: Any
    external_provider_boundary: bool


def planner_thread_compatibility_sha256() -> str:
    """Bind retained threads to the exact stable Planner contract."""

    return canonical_sha256(
        {
            "schema_version": "cera.pi_scene.planner_thread_compatibility.v1",
            "profile": PLANNER_PROFILE,
            "base_instructions_sha256": text_sha256(PLANNER_BASE_INSTRUCTIONS),
            "provider": "openai",
            "model": "gpt-5.6-sol",
            "adapter": "cera.pi_scene.retained_codex_planner.v1",
        }
    )


def default_planner_session_factory(runtime_root: Path) -> PlannerSessionFactory:
    state_store = PlannerThreadStateStore(runtime_root / "planner_threads")
    return state_store.session_factory(
        compatibility_sha256=planner_thread_compatibility_sha256(),
    )


class _PiScenePlannerRegistry:
    """Keep one compatible retained Planner session per SillyTavern chat."""

    def __init__(
        self,
        builder: Callable[
            [str, str, LeanSceneTurnInputV1],
            Any,
        ],
    ) -> None:
        self._builder = builder
        self._active: dict[
            str,
            tuple[str, str, str, Any],
        ] = {}
        self._lock = RLock()

    def resolve(self, turn: LeanSceneTurnInputV1) -> Any:
        controls = turn.request_controls
        if controls is None:
            raise ContractValidationError(
                "dynamic Pi Scene Planner resolution requires typed request controls"
            )
        session_id = controls.session_id
        effort = controls.reasoning_effort
        with self._lock:
            active = self._active.get(session_id)
            if active is not None and (active[1], active[2]) != (
                turn.world_id,
                turn.branch_id,
            ):
                raise StateConflictError("Pi Scene session resolved to a different world or branch")
            if active is not None and active[0] == effort:
                return active[3]
            planner = self._builder(session_id, effort, turn)
            self._active[session_id] = (
                effort,
                turn.world_id,
                turn.branch_id,
                planner,
            )
            return planner

    def reset_after_transport_failure(
        self,
        turn: LeanSceneTurnInputV1,
        expected_thread_sha256: str,
    ) -> None:
        """Archive exactly one interrupted branch-bound Planner thread."""

        planner = self.resolve(turn)
        reset = getattr(planner, "reset_provider_thread_after_transport_failure", None)
        if not callable(reset):
            raise StateConflictError(
                "Pi Scene Planner cannot archive its interrupted provider thread"
            )
        reset(expected_thread_sha256)

    def active_thread_sha256(self, turn: LeanSceneTurnInputV1) -> str | None:
        """Read the exact branch Planner thread hash without provider work."""

        planner = self.resolve(turn)
        read = getattr(planner, "active_provider_thread_sha256", None)
        if not callable(read):
            raise StateConflictError("Pi Scene Planner cannot expose retained-thread custody")
        value = read()
        if value is not None and (not isinstance(value, str) or not re_is_sha256(value)):
            raise StateConflictError("Pi Scene active Planner thread hash is invalid")
        return value

    def prepare_fresh_thread(self, turn: LeanSceneTurnInputV1) -> str:
        """Create one empty fresh Planner thread without a provider operation."""

        planner = self.resolve(turn)
        prepare = getattr(planner, "prepare_fresh_provider_thread", None)
        if not callable(prepare):
            raise StateConflictError("Pi Scene Planner cannot prepare a fresh retained thread")
        value = prepare()
        if not isinstance(value, str) or not re_is_sha256(value):
            raise StateConflictError("Pi Scene fresh Planner thread hash is invalid")
        return value

    def abandon_completed_uncommitted(
        self,
        turn: LeanSceneTurnInputV1,
        expected_thread_sha256: str,
    ) -> None:
        """Retire one completed Planner thread without provider work."""

        planner = self.resolve(turn)
        abandon = getattr(planner, "abandon_completed_uncommitted_thread", None)
        if not callable(abandon):
            raise StateConflictError(
                "Pi Scene Planner cannot retire its completed uncommitted thread"
            )
        abandon(expected_thread_sha256)


class _ResolverOnlyOrdinaryPlanner:
    """Fail closed when live input omits the chat-scoped Planner controls."""

    external_provider_boundary = False

    def plan(self, _request: Any) -> Any:
        raise ContractValidationError(
            "full-model ordinary planning requires a chat-scoped Planner resolver"
        )


def accepted_logic_route(
    store: LeanSceneStore,
    turn: LeanSceneTurnInputV1,
) -> SceneRoute:
    """Resolve the one automatic model from verified accepted branch state.

    The SillyTavern request never selects the adult owner.  Its bootstrap turn
    is rebuilt after this lookup when accepted state says the adult route is
    active, before any provider can be dispatched.
    """

    route_state = store.current_logic_route(
        world_id=turn.world_id,
        branch_id=turn.branch_id,
    )
    try:
        return SceneRoute(route_state.current_logic_route.value)
    except (AttributeError, ValueError) as exc:
        raise StateConflictError("accepted branch returned an unsupported logic route") from exc


def _initialize_live_runtime_roots(runtime_root: Path) -> tuple[Path, Path, Path]:
    root = runtime_root.resolve()
    root.mkdir(parents=True, exist_ok=False)
    lifecycle_root = root / "codex_lifecycle"
    lifecycle_root.mkdir()
    operation_root = root / "codex_operations"
    operation_root.mkdir()
    return root, lifecycle_root, operation_root


def build_live_runtime(
    runtime_root: Path,
    *,
    sol_ceiling: int,
    deepseek_ceiling: int,
    deepseek_per_invocation_ceiling: int = 6,
    seed_runtime_root: Path | None = None,
    inject_generation_two_recorder_failure: bool = False,
    planner_session_factory: CognitionSessionFactory | None = None,
    planner_backend_factory: PlannerBackendFactory | None = None,
    cognition_backend_factory: CognitionBackendFactory | None = None,
    provider_components: FullModelProviderComponents | None = None,
) -> LivePiSceneRuntime:
    if planner_backend_factory is not None and cognition_backend_factory is not None:
        raise ContractValidationError("only one custom cognition backend factory may be supplied")
    assert_provider_dispatch_allowed(
        "scripts.pi_scene_lean.live_runtime",
        external_provider_boundary=(
            True
            if provider_components is None
            else any(
                is_external_provider_boundary(value)
                for value in (
                    provider_components,
                    provider_components.planner_lifecycle,
                    provider_components.luna_backend,
                    provider_components.reader_backend,
                    provider_components.pi,
                )
            )
        ),
    )
    runtime_root, lifecycle_root, operation_root = _initialize_live_runtime_roots(runtime_root)
    if seed_runtime_root is not None:
        _seed_live_runtime_state(runtime_root, seed_runtime_root)
    stack = ExitStack()
    try:
        sol_ledger = ContinuousProviderCallLedger(
            (runtime_root / "SOL_PROVIDER_CALLS.jsonl").resolve(),
            maximum_calls=sol_ceiling,
        )
        # Parse any resumed ledger before constructing provider backends.
        _ = sol_ledger.dispatched_call_count
        deepseek_ledger = PiProviderOperationLedger(
            (runtime_root / "DEEPSEEK_PROVIDER_OPERATIONS.jsonl").resolve(),
            maximum_operations=deepseek_ceiling,
            maximum_operations_per_invocation=deepseek_per_invocation_ceiling,
        )
        _ = deepseek_ledger.operation_count
        readable_debug_setting = os.environ.get("CERA_PI_SCENE_READABLE_DEBUG", "1").strip().lower()
        readable_debug = ReadablePiSceneDebugLog(
            runtime_root / "debug" / "readable",
            enabled=readable_debug_setting not in {"0", "false", "off", "no"},
            max_entries=int(os.environ.get("CERA_PI_SCENE_READABLE_DEBUG_MAX", "200")),
        )
        cognition_readable_debug = PathBudgetedCognitionDebugLog(readable_debug)
        if provider_components is None:
            from openai_codex import Codex, CodexConfig

            planner_lifecycle_root = lifecycle_root / "planner"
            luna_lifecycle_root = lifecycle_root / "luna"
            reader_lifecycle_root = lifecycle_root / "reader"
            planner_lifecycle_root.mkdir()
            luna_lifecycle_root.mkdir()
            reader_lifecycle_root.mkdir()
            app_server_overrides = codex_app_server_config_overrides(lifecycle_root)
            codex = stack.enter_context(
                Codex(
                    CodexConfig(
                        config_overrides=app_server_overrides,
                        cwd=str(lifecycle_root),
                        env=codex_app_server_environment(),
                    )
                )
            )
            validate_codex_app_server_configuration(
                codex,
                cwd=lifecycle_root,
            )
            validate_codex_mcp_server_status(codex)
            account = codex.account()
            if account.account is None:
                raise StateConflictError("ChatGPT Codex account is unavailable")
            planner_lifecycle = OpenAICodexStoredThreadBackend(
                codex=codex,
                model="gpt-5.6-sol",
                cwd=str(planner_lifecycle_root),
                base_instructions=COGNITION_PLANNER_BASE_INSTRUCTIONS,
                service_name="cera_pi_scene_cognition_planner",
                service_tier="priority",
            )
            luna_lifecycle = OpenAICodexStoredThreadBackend(
                codex=codex,
                model="gpt-5.6-luna",
                cwd=str(luna_lifecycle_root),
                base_instructions=LUNA_VALIDATOR_BASE_INSTRUCTIONS,
                service_name="cera_pi_scene_semantic_validator",
                service_tier="priority",
            )
            reader_lifecycle = OpenAICodexStoredThreadBackend(
                codex=codex,
                model="gpt-5.6-sol",
                cwd=str(reader_lifecycle_root),
                base_instructions=SOL_READER_BASE_INSTRUCTIONS,
                service_name="cera_pi_scene_reader_validator",
                service_tier="priority",
            )
            luna_evidence = ProviderOperationEvidenceStoreV1(
                (runtime_root / "debug" / "semantic_validator").resolve(),
                stage="pi_scene_luna_semantic_validator",
            )
            luna_backend = CodexLunaSemanticValidatorBackend(
                lifecycle=luna_lifecycle,
                workspace=operation_root / "semantic_validator",
                call_ledger=sol_ledger,
                operation_evidence=luna_evidence,
            )
            reader_evidence = ProviderOperationEvidenceStoreV1(
                (runtime_root / "debug" / "reader_validator").resolve(),
                stage="pi_scene_sol_reader_validator",
            )
            reader_backend = CodexSolReaderBackend(
                lifecycle=reader_lifecycle,
                workspace=operation_root / "reader_validator",
                call_ledger=sol_ledger,
                operation_evidence=reader_evidence,
            )
            pi = PiSceneAdapter(
                pi_executable=DEFAULT_PI,
                extension_path=DEFAULT_EXTENSION,
                pi_version="0.84.1",
                operation_ledger=deepseek_ledger,
                readable_debug=readable_debug,
            )
        else:
            planner_lifecycle = provider_components.planner_lifecycle
            luna_backend = provider_components.luna_backend
            reader_backend = provider_components.reader_backend
            pi = provider_components.pi

        semantic_validator = FreshLunaValidatorFactory(luna_backend)
        reader_validator = FreshSolReaderFactory(reader_backend)
        selected_session_factory = (
            default_cognition_session_factory(runtime_root)
            if planner_session_factory is None
            else planner_session_factory
        )
        accepted_world_root = runtime_root / "accepted_world"
        store = LeanSceneStore(accepted_world_root)
        workspace_manager = PiSceneWorldWorkspaceManager(
            accepted_world_root,
            (ROOT / "genesis" / "packages").resolve(),
        )
        world_resolver = PiSceneChatWorldResolver(
            manager=workspace_manager,
            store=store,
            scope_root=runtime_root / "chat_scopes",
            base_seed=initial_hanezawa_doorway_seed(),
        )
        planner_backends: dict[tuple[str, str], Any] = {}

        def build_planner(
            session_id: str,
            effort: str,
            turn: LeanSceneTurnInputV1,
        ) -> RetrievalDirectedCognitionPlanner:
            session_digest = text_sha256(session_id)
            evidence = ProviderOperationEvidenceStoreV1(
                (runtime_root / "debug" / "planner" / session_digest[:24] / effort).resolve(),
                stage="pi_scene_full_model_cognition_planner",
            )
            controls = turn.request_controls
            if controls is None:
                raise ContractValidationError(
                    "branch-bound cognition Planner requires typed request controls"
                )
            scope = world_resolver.resolve(controls)
            if (scope.world_id, scope.branch_id) != (
                turn.world_id,
                turn.branch_id,
            ):
                raise StateConflictError(
                    "cognition Planner world workspace differs from turn scope"
                )
            world_mcp_factory = workspace_manager.mcp_factory(scope.workspace)
            backend_workspace = operation_root / "sessions" / session_digest[:24] / effort
            if cognition_backend_factory is not None:
                backend = cognition_backend_factory(
                    session_id,
                    effort,
                    turn,
                    world_mcp_factory,
                )
            elif planner_backend_factory is None:
                backend = BranchBoundCognitionPlannerBackend(
                    world_mcp_factory=world_mcp_factory,
                    lifecycle=planner_lifecycle,
                    workspace=backend_workspace,
                    call_ledger=sol_ledger,
                    operation_evidence=evidence,
                )
            else:
                backend = planner_backend_factory(session_id, effort, turn)
            if backend.route.reasoning_effort != effort:
                backend.route = replace(
                    backend.route,
                    route_id=(f"cera_pi_scene_cognition_{backend.route.model_name}_{effort}_v3"),
                    reasoning_effort=effort,
                )
            planner_backends[(session_id, effort)] = backend
            session = selected_session_factory(session_id, effort, backend)
            return RetrievalDirectedCognitionPlanner(
                RetainedCognitionPlannerAdapter(
                    session,
                    operation_evidence=evidence,
                    readable_debug=cognition_readable_debug,
                )
            )

        planner_registry = _PiScenePlannerRegistry(
            lambda session_id, effort, turn: build_planner(
                session_id,
                effort,
                turn,
            )
        )

        def reinitialize_transport_owner(
            binding: PiSceneRequestBindingV1,
            turn: LeanSceneTurnInputV1,
            logic_owner: str,
            expected_thread_sha256: str,
        ) -> None:
            controls = turn.request_controls
            if controls is None or (
                controls.session_id != binding.session_id
                or turn.world_id != binding.world_id
                or turn.branch_id != binding.branch_id
                or canonical_sha256(controls) != binding.controls_sha256
            ):
                raise StateConflictError(
                    "Pi Scene transport retry changed session or branch custody"
                )
            if logic_owner != "planner":
                raise StateConflictError("Pi Scene transport retry is Planner-only")
            planner_registry.reset_after_transport_failure(
                turn,
                expected_thread_sha256,
            )

        def transport_provider_ledger_snapshot() -> PiSceneProviderLedgerSnapshotV1:
            return PiSceneProviderLedgerSnapshotV1(
                schema_version=PiSceneProviderLedgerSnapshotV1.SCHEMA_VERSION,
                dispatched_call_count=sol_ledger.dispatched_call_count,
                events=tuple(sol_ledger.events),
            )

        def transport_retry_active_thread_snapshot(
            binding: PiSceneRequestBindingV1,
            turn: LeanSceneTurnInputV1,
            logic_owner: str,
        ) -> str | None:
            controls = turn.request_controls
            if controls is None or (
                controls.session_id != binding.session_id
                or turn.world_id != binding.world_id
                or turn.branch_id != binding.branch_id
                or canonical_sha256(controls) != binding.controls_sha256
            ):
                raise StateConflictError(
                    "Pi Scene transport retry changed session or branch custody"
                )
            if logic_owner != "planner":
                raise StateConflictError("Pi Scene transport retry is Planner-only")
            return planner_registry.active_thread_sha256(turn)

        def transport_retry_fresh_thread_initializer(
            binding: PiSceneRequestBindingV1,
            turn: LeanSceneTurnInputV1,
            logic_owner: str,
        ) -> str:
            controls = turn.request_controls
            if controls is None or (
                controls.session_id != binding.session_id
                or turn.world_id != binding.world_id
                or turn.branch_id != binding.branch_id
                or canonical_sha256(controls) != binding.controls_sha256
            ):
                raise StateConflictError(
                    "Pi Scene transport retry changed session or branch custody"
                )
            if logic_owner != "planner":
                raise StateConflictError("Pi Scene transport retry is Planner-only")
            return planner_registry.prepare_fresh_thread(turn)

        def transport_completed_planner_abandoner(
            binding: PiSceneRequestBindingV1,
            turn: LeanSceneTurnInputV1,
            logic_owner: str,
            expected_thread_sha256: str,
        ) -> None:
            controls = turn.request_controls
            if controls is None or (
                controls.session_id != binding.session_id
                or turn.world_id != binding.world_id
                or turn.branch_id != binding.branch_id
                or canonical_sha256(controls) != binding.controls_sha256
            ):
                raise StateConflictError(
                    "Pi Scene completed Planner changed session or branch custody"
                )
            if logic_owner != "planner":
                raise StateConflictError("Pi Scene completed result owner is not Planner")
            planner_registry.abandon_completed_uncommitted(
                turn,
                expected_thread_sha256,
            )

        def fault(
            accepted: LeanAcceptedTurnReceiptV1,
            attempt_number: int,
        ) -> str | None:
            if (
                inject_generation_two_recorder_failure
                and accepted.generation == 2
                and attempt_number == 1
            ):
                return "injected_smoke_recorder_failure"
            return None

        def planner_provider_result(turn: LeanSceneTurnInputV1) -> Any:
            controls = turn.request_controls
            if controls is None:
                raise StateConflictError("Planner Retry receipt lost request controls")
            backend = planner_backends.get((controls.session_id, controls.reasoning_effort))
            result = None if backend is None else getattr(backend, "last_provider_result", None)
            if result is None:
                raise StateConflictError("Planner Retry receipt is unavailable")
            return result

        def luna_provider_result() -> Any:
            result = getattr(luna_backend, "last_provider_result", None)
            if result is None:
                raise StateConflictError("Luna Retry receipt is unavailable")
            return result

        def reader_provider_result() -> Any:
            result = getattr(reader_backend, "last_provider_result", None)
            if result is None:
                raise StateConflictError("Reader Retry receipt is unavailable")
            return result

        provider_stage_retry = (
            build_provider_stage_retry_production_assembly(
                runtime_root=runtime_root,
                scene_store=store,
                sol_ledger=sol_ledger,
                pi_adapter=pi,
                ordinary_ports=OrdinaryStageProviderPortsV1(
                    resolve_planner=planner_registry.resolve,
                    planner_active_thread_sha256=(planner_registry.active_thread_sha256),
                    retire_planner_thread=(planner_registry.reset_after_transport_failure),
                    planner_provider_result=planner_provider_result,
                    semantic_validator=semantic_validator,
                    luna_provider_result=luna_provider_result,
                    reader_validator=reader_validator,
                    reader_provider_result=reader_provider_result,
                ),
            )
            if type(pi) is PiSceneAdapter
            else None
        )

        coordinator = LeanPiSceneCoordinator(
            store=store,
            planner=_ResolverOnlyOrdinaryPlanner(),
            writer_views=WriterViewMaterializer(runtime_root / "writer_views"),
            pi=pi,
            session_root=runtime_root / "pi_sessions",
            recording_fault_injector=fault,
            planner_resolver=planner_registry.resolve,
            semantic_validator=semantic_validator,
            reader_validator=reader_validator,
            ordinary_stage_retry=(
                None
                if provider_stage_retry is None
                else cast(
                    OrdinaryProviderStageRetryPort,
                    provider_stage_retry.ordinary,
                )
            ),
        )
        adult_runtime = FullModelAdultRuntimeFactory(
            store=store,
            pi_adapter=pi,
            catalog_root=ROOT / "adult" / "catalog" / "adult_craft_v1",
            protected_runtime_root=runtime_root / "protected_adult",
        )

        def adult_orchestrator(
            turn: LeanSceneTurnInputV1,
            request_id: str,
            candidate_id: str,
        ) -> Any:
            wrapped = adult_runtime.orchestrator(turn, request_id, candidate_id)
            return (
                wrapped
                if provider_stage_retry is None
                else provider_stage_retry.wrap_adult_orchestrator(wrapped, turn)
            )

        def adult_regeneration_executor(
            prepared: Any,
            frozen_turn: LeanSceneTurnInputV1,
        ) -> Any:
            if provider_stage_retry is None:
                return adult_runtime.regeneration_executor(prepared, frozen_turn)
            return provider_stage_retry.execute_adult_regeneration(
                prepared=prepared,
                frozen_turn=frozen_turn,
            )

        full_model_controller = FullModelSceneController(
            ordinary=coordinator,
            store=store,
            adult_orchestrator_factory=adult_orchestrator,
            adult_context_provider=adult_runtime.execution_context,
            adult_regeneration_executor=adult_regeneration_executor,
            adult_operation_controller_factory=lambda: ProtectedAdultOperationController(
                ProtectedAdultOperationStore(runtime_root / "protected_adult")
            ),
        )
        if provider_stage_retry is not None:
            provider_stage_retry.bind_full_model_controller(full_model_controller)
        return LivePiSceneRuntime(
            stack=stack,
            coordinator=coordinator,
            store=store,
            sol_ledger=sol_ledger,
            deepseek_ledger=deepseek_ledger,
            readable_debug=readable_debug,
            world_resolver=world_resolver,
            full_model_controller=full_model_controller,
            provider_stage_retry=provider_stage_retry,
            transport_retry_reinitializer=reinitialize_transport_owner,
            transport_provider_ledger_snapshot=transport_provider_ledger_snapshot,
            transport_retry_active_thread_snapshot=(transport_retry_active_thread_snapshot),
            transport_retry_fresh_thread_initializer=(transport_retry_fresh_thread_initializer),
            transport_completed_planner_abandoner=transport_completed_planner_abandoner,
        )
    except BaseException:
        stack.close()
        raise


def _seed_live_runtime_state(runtime_root: Path, seed_runtime_root: Path) -> None:
    if seed_runtime_root.is_symlink():
        raise StateConflictError("live seed root must not be a symlink")
    source_root = seed_runtime_root.resolve()
    target_root = runtime_root.resolve()
    if source_root == target_root or target_root.is_relative_to(source_root):
        raise ContractValidationError("live seed root overlaps its target")
    for name in ("accepted_world", "pi_sessions"):
        source = source_root / name
        target = target_root / name
        if source.is_symlink() or not source.is_dir() or target.exists():
            raise StateConflictError(f"live seed {name} is unavailable or occupied")
        if any(path.is_symlink() for path in source.rglob("*")):
            raise StateConflictError(f"live seed {name} contains a symlink")
        shutil.copytree(source, target)
    protected_adult_source = source_root / "protected_adult"
    if protected_adult_source.exists():
        protected_adult_target = target_root / "protected_adult"
        if (
            protected_adult_source.is_symlink()
            or not protected_adult_source.is_dir()
            or protected_adult_target.exists()
        ):
            raise StateConflictError("live seed protected_adult is unavailable or occupied")
        if any(path.is_symlink() for path in protected_adult_source.rglob("*")):
            raise StateConflictError("live seed protected_adult contains a symlink")
        shutil.copytree(protected_adult_source, protected_adult_target)
    planner_source = source_root / "planner_threads"
    if planner_source.exists():
        planner_target = target_root / "planner_threads"
        if planner_source.is_symlink() or not planner_source.is_dir() or planner_target.exists():
            raise StateConflictError("live seed planner_threads is unavailable or occupied")
        if any(path.is_symlink() for path in planner_source.rglob("*")):
            raise StateConflictError("live seed planner_threads contains a symlink")
        shutil.copytree(planner_source, planner_target)
    for name in (
        "SOL_PROVIDER_CALLS.jsonl",
        "DEEPSEEK_PROVIDER_OPERATIONS.jsonl",
    ):
        source = source_root / name
        target = target_root / name
        if not source.exists():
            continue
        if source.is_symlink() or not source.is_file() or target.exists():
            raise StateConflictError(f"live seed {name} is unavailable or occupied")
        shutil.copy2(source, target)


def pi_scene_session_scope(session_id: str) -> tuple[str, str, str]:
    """Resolve one stable SillyTavern chat to isolated story identities."""

    return new_chat_semantic_scope(session_id)


def build_session_context_provider(
    store: LeanSceneStore,
    seed: PiSceneContextSeedV1,
    *,
    workspace_resolver: PiSceneChatWorldResolver | None = None,
) -> Callable[
    [
        SceneRoute,
        str,
        Sequence[Mapping[str, str]],
        LeanSceneRequestControlsV1,
    ],
    LeanSceneTurnInputV1,
]:
    """Build accepted context from the exact branch assigned to one chat."""

    def provide(
        route: SceneRoute,
        source: str,
        messages: Sequence[Mapping[str, str]],
        controls: LeanSceneRequestControlsV1,
    ) -> LeanSceneTurnInputV1:
        if workspace_resolver is not None:
            return workspace_resolver.resolve_turn(
                route=route,
                source=source,
                messages=messages,
                controls=controls,
            ).turn
        world_id, branch_id, scene_id = pi_scene_session_scope(controls.session_id)
        scoped_seed = replace(
            seed,
            world_id=world_id,
            branch_id=branch_id,
            scene_id=scene_id,
        )
        turn = AcceptedBranchContextProvider(store, scoped_seed)(route, source, messages)
        return replace(turn, request_controls=controls)

    return provide


def _available_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _start_isolated_sillytavern(
    target_root: Path,
    *,
    port: int,
) -> subprocess.Popen[bytes]:
    manifest = verify_isolated_sillytavern(target_root)
    if manifest.get("user_data_copied") is not False:
        raise StateConflictError("isolated SillyTavern contains user data")
    node = shutil.which("node")
    if node is None:
        raise StateConflictError("Node.js is unavailable for isolated SillyTavern")
    command = isolated_sillytavern_command(
        target_root,
        node_executable=Path(node),
        port=port,
    )
    logs = target_root / "cera-smoke-logs"
    logs.mkdir()
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    stdout = (logs / "sillytavern.stdout.log").open("wb")
    stderr = (logs / "sillytavern.stderr.log").open("wb")
    try:
        process = subprocess.Popen(
            command,
            cwd=str(target_root),
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            shell=False,
            creationflags=creationflags,
        )
    finally:
        stdout.close()
        stderr.close()
    deadline = time.monotonic() + 90
    url = f"http://127.0.0.1:{port}/"
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise StateConflictError(
                f"isolated SillyTavern exited during startup ({process.returncode})"
            )
        try:
            with urlopen(url, timeout=2) as response:
                if 200 <= response.status < 500:
                    return process
        except Exception:
            time.sleep(0.2)
    process.terminate()
    process.wait(timeout=10)
    raise StateConflictError("isolated SillyTavern did not become reachable")


def _stop_process(process: subprocess.Popen[bytes] | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _json_request(
    url: str,
    *,
    token: str,
    payload: dict[str, Any] | None = None,
    origin: str = "http://127.0.0.1:8000",
) -> dict[str, Any]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Origin": origin,
    }
    request = Request(
        url,
        data=data,
        headers=headers,
        method="GET" if payload is None else "POST",
    )
    with urlopen(request, timeout=900) as response:
        content_type = response.headers.get("Content-Type", "")
        if not content_type.startswith("application/json"):
            raise StateConflictError("Pi Scene HTTP response is not JSON")
        value = json.loads(response.read().decode("utf-8"))
        if not isinstance(value, dict):
            raise StateConflictError("Pi Scene HTTP response is not an object")
        return value


class _IsolatedSillyTavernClient:
    """Use the isolated server's real Custom chat-completion proxy."""

    def __init__(self, origin: str) -> None:
        self.origin = origin.rstrip("/")
        self._opener = build_opener(HTTPCookieProcessor(CookieJar()))
        request = Request(self.origin + "/csrf-token", method="GET")
        with self._opener.open(request, timeout=30) as response:
            value = json.loads(response.read().decode("utf-8"))
        token = value.get("token") if isinstance(value, dict) else None
        if not isinstance(token, str) or not token:
            raise StateConflictError("isolated SillyTavern did not issue a CSRF token")
        self._csrf_token = token
        self.completed_requests = 0

    def complete(
        self,
        *,
        cera_base: str,
        authorization_token: str,
        model: str,
        source: str,
        session_id: str,
    ) -> dict[str, Any]:
        assert_provider_dispatch_allowed("scripts.pi_scene_lean.sillytavern_completion")
        payload = {
            "chat_completion_source": "custom",
            "custom_url": cera_base.rstrip("/") + "/v1",
            "custom_include_headers": (f"Authorization: Bearer {authorization_token}"),
            "custom_include_body": (
                f"cera_session_id: {session_id}\ncera_profile_id: {PI_SCENE_PROFILE}"
            ),
            "model": model,
            "messages": [{"role": "user", "content": source}],
            "stream": False,
            "temperature": 0.7,
            "max_tokens": 4096,
        }
        request = Request(
            self.origin + "/api/backends/chat-completions/generate",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "X-CSRF-Token": self._csrf_token,
            },
            method="POST",
        )
        with self._opener.open(request, timeout=900) as response:
            content_type = response.headers.get("Content-Type", "")
            if not content_type.startswith("application/json"):
                raise StateConflictError("isolated SillyTavern response is not JSON")
            value = json.loads(response.read().decode("utf-8"))
        if not isinstance(value, dict):
            raise StateConflictError("isolated SillyTavern response is not an object")
        if value.get("error"):
            raise StateConflictError("isolated SillyTavern reported an upstream error")
        self.completed_requests += 1
        return value


def run_live_smoke(
    runtime_root: Path,
    *,
    sillytavern_source: Path = DEFAULT_SILLYTAVERN,
    sol_ceiling: int = 12,
    deepseek_ceiling: int = 60,
    deepseek_per_invocation_ceiling: int = 6,
    resume_from_runtime_root: Path | None = None,
) -> dict[str, Any]:
    runtime = build_live_runtime(
        runtime_root,
        sol_ceiling=sol_ceiling,
        deepseek_ceiling=deepseek_ceiling,
        deepseek_per_invocation_ceiling=deepseek_per_invocation_ceiling,
        seed_runtime_root=resume_from_runtime_root,
        inject_generation_two_recorder_failure=(resume_from_runtime_root is None),
    )
    token = secrets.token_urlsafe(32)
    session_id = "cera-pi-scene-smoke"
    st_process = None
    try:
        isolated_st_root = runtime_root / "isolated_sillytavern"
        st_manifest = stage_isolated_sillytavern(sillytavern_source, isolated_st_root)
        st_port = _available_loopback_port()
        st_origin = f"http://127.0.0.1:{st_port}"
        st_process = _start_isolated_sillytavern(isolated_st_root, port=st_port)
        context = AcceptedBranchContextProvider(runtime.store, initial_hana_seed())
        provider_stage_retry = getattr(runtime, "provider_stage_retry", None)
        retry_http_kwargs = (
            {} if provider_stage_retry is None else provider_stage_retry.http_adapter_kwargs()
        )
        adapter = PiSceneHttpAdapter(
            coordinator=runtime.coordinator,
            session_id=session_id,
            context_provider=context,
            readable_debug=runtime.readable_debug,
            logic_route_resolver=lambda turn: accepted_logic_route(
                runtime.store,
                turn,
            ),
            full_model_controller=runtime.full_model_controller,
            transport_retry_reinitializer=runtime.transport_retry_reinitializer,
            transport_provider_ledger_snapshot=(runtime.transport_provider_ledger_snapshot),
            transport_retry_active_thread_snapshot=(runtime.transport_retry_active_thread_snapshot),
            transport_retry_fresh_thread_initializer=(
                runtime.transport_retry_fresh_thread_initializer
            ),
            transport_completed_planner_abandoner=(runtime.transport_completed_planner_abandoner),
            **retry_http_kwargs,
        )
        if provider_stage_retry is not None:
            provider_stage_retry.bind_http_adapter(adapter)
        server = build_pi_scene_server(
            adapter,
            PiSceneServerConfigV1(
                host="127.0.0.1",
                port=0,
                authorization_token=token,
                approved_origins=(st_origin,),
            ),
        )
        worker = Thread(target=server.serve_forever, daemon=True)
        worker.start()
        st_client = _IsolatedSillyTavernClient(st_origin)
    except BaseException:
        _stop_process(st_process)
        runtime.close()
        raise
    base = f"http://127.0.0.1:{server.server_address[1]}"
    evidence: list[dict[str, Any]] = []

    def create(model: str, source: str) -> dict[str, Any]:
        value = st_client.complete(
            cera_base=base,
            authorization_token=token,
            model=model,
            source=source,
            session_id=session_id,
        )
        text = value["choices"][0]["message"]["content"]
        if not isinstance(text, str) or len(text.strip()) < 80:
            raise StateConflictError("live Pi Writer returned an unusable scene")
        lowered = text.lower()
        if any(
            marker in lowered for marker in ("i can't help", "i cannot help", "unable to comply")
        ):
            raise StateConflictError("live Pi Writer returned a refusal")
        return value

    def decide(review_id: str, action: str, **extra: Any) -> dict[str, Any]:
        return _json_request(
            f"{base}/v1/cera/reviews/{review_id}/decision",
            token=token,
            payload={"action": action, **extra},
            origin=st_origin,
        )

    try:
        health = _json_request(base + "/health", token=token, origin=st_origin)
        if not health.get("loopback_only") or not health.get("authorization_required"):
            raise StateConflictError("loopback health boundary changed")

        if resume_from_runtime_root is None:
            ordinary_one = create(
                PI_SCENE_ORDINARY_MODEL,
                "Hana continues the conversation naturally and returns the next choice to Ted.",
            )
            ordinary_one_review = ordinary_one["cera"]["provisional_review_id"]
            accepted_one = decide(ordinary_one_review, "accept")
            if accepted_one["review"]["recording_status"] != RecordingStatus.COMPLETE.value:
                raise StateConflictError("first ordinary Recorder did not complete")
            evidence.append(_smoke_step("ordinary_accept_1", accepted_one))

            adult_one = create(
                PI_SCENE_ADULT_MODEL,
                "Both adults explicitly choose to become more intimate while Hana keeps the ability to pause.",
            )
            adult_one_review = adult_one["cera"]["provisional_review_id"]
            accepted_two = decide(adult_one_review, "accept")
            if accepted_two["review"]["recording_status"] != RecordingStatus.PENDING_REPAIR.value:
                raise StateConflictError("injected Recorder failure did not remain repairable")
            repaired_two = decide(adult_one_review, "repair_recording")
            if repaired_two["review"]["recording_status"] != RecordingStatus.COMPLETE.value:
                raise StateConflictError("Recorder repair did not complete")
            evidence.append(_smoke_step("adult_accept_1_repaired", repaired_two))
        else:
            seed_head = runtime.store.load_head(
                world_id="world-pi-scene-smoke", branch_id="branch-main"
            )
            if seed_head.generation != 2 or seed_head.receipt is None:
                raise StateConflictError("resume seed is not the accepted generation-two frontier")
            repair_turn = context(
                SceneRoute.ADULT,
                seed_head.receipt.exact_user_source,
                (),
            )
            repaired_attempt = runtime.coordinator.repair_latest_recording(repair_turn)
            if repaired_attempt.status is not RecordingStatus.COMPLETE:
                raise StateConflictError("resumed Recorder repair did not complete")
            evidence.append(
                {
                    "label": "adult_accept_1_repaired_after_restart",
                    "accepted_turn_id": seed_head.receipt.accepted_turn_id,
                    "accepted_receipt_sha256": seed_head.receipt.receipt_sha256,
                    "route": seed_head.receipt.route.value,
                    "recording_status": repaired_attempt.status.value,
                    "warnings": [],
                    "provider_operations": repaired_attempt.provider_operations,
                }
            )

        adult_two = create(
            PI_SCENE_ADULT_MODEL,
            "The adults continue one mutually chosen intimate beat and stop when Hana makes her next choice clear.",
        )
        adult_two_review = adult_two["cera"]["provisional_review_id"]
        accepted_three = decide(adult_two_review, "accept")
        if accepted_three["review"]["recording_status"] != RecordingStatus.COMPLETE.value:
            raise StateConflictError("second adult Recorder did not complete")
        evidence.append(_smoke_step("adult_accept_2", accepted_three))

        ordinary_two = create(
            PI_SCENE_ORDINARY_MODEL,
            "Return to an ordinary conversation as Hana reflects non-explicitly and leaves the floor to Ted.",
        )
        original_review_id = ordinary_two["cera"]["provisional_review_id"]
        original_review = _json_request(
            f"{base}/v1/cera/reviews/{original_review_id}",
            token=token,
            origin=st_origin,
        )
        regenerated = decide(
            original_review_id,
            "regenerate",
            force_rehydrate=True,
        )
        successor = regenerated.get("successor")
        if not isinstance(successor, dict):
            raise StateConflictError("Regenerate did not produce a successor")
        successor_review_id = successor["cera"]["provisional_review_id"]
        successor_review = _json_request(
            f"{base}/v1/cera/reviews/{successor_review_id}",
            token=token,
            origin=st_origin,
        )
        if (
            successor_review["primary_authority_sha256"]
            != original_review["primary_authority_sha256"]
        ):
            raise StateConflictError("Regenerate changed the exact Codex sequence")
        accepted_four = decide(successor_review_id, "accept")
        if accepted_four["review"]["recording_status"] != RecordingStatus.COMPLETE.value:
            raise StateConflictError("final ordinary Recorder did not complete")
        evidence.append(_smoke_step("ordinary_accept_2_rehydrated", accepted_four))

        head = runtime.store.load_head(
            world_id="world-pi-scene-smoke",
            branch_id="branch-main",
        )
        if head.generation != 4:
            raise StateConflictError("live smoke accepted generation differs from four")
        accepted_payloads = runtime.store.recent_accepted_payloads(
            world_id="world-pi-scene-smoke",
            branch_id="branch-main",
            limit=6,
            adult_full=True,
        )
        route_sequence = [value["receipt"]["route"] for value in accepted_payloads]
        if route_sequence != ["ordinary", "adult", "adult", "ordinary"]:
            raise StateConflictError("ordinary/adult transition sequence changed")
        final_writer_receipt = accepted_payloads[-1]["receipt"]["writer_receipt"]
        if final_writer_receipt["rehydrated"] is not True:
            raise StateConflictError("final accepted Pi session was not rehydrated")
        expected_sol_calls = 2 if resume_from_runtime_root is None else 1
        if runtime.sol_ledger.dispatched_call_count != expected_sol_calls:
            raise StateConflictError("live smoke Sol call count differs from its route")
        if st_process.poll() is not None:
            raise StateConflictError("isolated SillyTavern exited during the live smoke")
        expected_st_requests = 4 if resume_from_runtime_root is None else 2
        if st_client.completed_requests != expected_st_requests:
            raise StateConflictError("isolated SillyTavern request count differs from its route")

        result = {
            "schema_version": "cera.pi_scene.live_smoke_evidence.v1",
            "status": "passed",
            "accepted_turns": 4,
            "routes": route_sequence,
            "sol_calls": runtime.sol_ledger.dispatched_call_count,
            "deepseek_operations": runtime.deepseek_ledger.operation_count,
            "deepseek_cached_input_tokens": sum(
                int(value.get("cached_input_tokens", 0))
                for value in runtime.deepseek_ledger.events
                if value.get("event") == "provider_operation_completed"
            ),
            "regenerate_exact_sequence": True,
            "pi_reset_rehydrated": True,
            "recorder_failure_repaired": True,
            "route_transition": "ordinary-adult-adult-ordinary",
            "isolated_sillytavern_started": True,
            "isolated_sillytavern_routed_chat_completions": st_client.completed_requests,
            "resumed_from_accepted_generation": (None if resume_from_runtime_root is None else 2),
            "isolated_sillytavern_user_data_copied": st_manifest["user_data_copied"],
            "isolated_sillytavern_manifest_sha256": st_manifest["manifest_sha256"],
            "accepted_head_sha256": head.accepted_head_sha256,
            "steps": evidence,
        }
        result["evidence_sha256"] = canonical_sha256(result)
        (runtime_root / "SMOKE_EVIDENCE.json").write_bytes(canonical_bytes(result) + b"\n")
        return result
    finally:
        try:
            server.shutdown()
            server.server_close()
            worker.join(timeout=10)
        finally:
            _stop_process(st_process)
            runtime.close()


def _smoke_step(label: str, value: dict[str, Any]) -> dict[str, Any]:
    review = value["review"]
    return {
        "label": label,
        "accepted_turn_id": value.get("accepted_turn_id"),
        "accepted_receipt_sha256": value.get("accepted_receipt_sha256"),
        "route": review["route"],
        "recording_status": review["recording_status"],
        "warnings": [warning["warning_code"] for warning in review["warnings"]],
        "provider_operations": review["provider_operations"],
    }


def repair_existing_smoke(runtime_root: Path) -> dict[str, Any]:
    """Finish one explicit structural Recorder repair with zero provider work."""

    root = runtime_root.resolve()
    evidence_path = root / "SMOKE_EVIDENCE.json"
    if evidence_path.exists():
        raise StateConflictError("smoke evidence is already published")
    store = LeanSceneStore(root / "accepted_world")
    context = AcceptedBranchContextProvider(store, initial_hana_seed())
    head = store.load_head(world_id="world-pi-scene-smoke", branch_id="branch-main")
    if head.generation != 4 or head.receipt is None:
        raise StateConflictError("existing smoke is not at accepted generation four")
    if head.recording_status is not RecordingStatus.PENDING_REPAIR:
        raise StateConflictError("existing smoke does not require final recording repair")

    sessions = sorted((root / "pi_sessions").rglob("*.jsonl"))
    if not sessions:
        raise StateConflictError("existing smoke omitted Pi session evidence")
    output_text = _last_assistant_text(sessions[-1])
    turn = context(SceneRoute.ORDINARY, head.receipt.exact_user_source, ())
    repaired = repair_latest_ordinary_recording_from_output(
        store=store,
        turn=turn,
        output_text=output_text,
    )
    if repaired.status is not RecordingStatus.COMPLETE or repaired.provider_operations != 0:
        raise StateConflictError("provider-free Recorder repair did not complete")

    accepted = store.recent_accepted_payloads(
        world_id="world-pi-scene-smoke",
        branch_id="branch-main",
        limit=6,
        adult_full=True,
    )
    routes = [value["receipt"]["route"] for value in accepted]
    if routes != ["ordinary", "adult", "adult", "ordinary"]:
        raise StateConflictError("existing smoke route sequence changed")
    for value in accepted:
        route = value["receipt"]["route"]
        if route == "ordinary" and "ordinary_record" not in value:
            raise StateConflictError("existing smoke retained an incomplete ordinary record")
        if route == "adult" and not {
            "adult_full_record",
            "adult_projection",
        }.issubset(value):
            raise StateConflictError("existing smoke retained an incomplete adult record")
    if accepted[-1]["receipt"]["writer_receipt"]["rehydrated"] is not True:
        raise StateConflictError("existing smoke final Writer was not rehydrated")

    sol_events = _read_jsonl(root / "SOL_PROVIDER_CALLS.jsonl")
    deepseek_events = _read_jsonl(root / "DEEPSEEK_PROVIDER_OPERATIONS.jsonl")
    sol_calls = sum(value.get("state") == "transport_invoked" for value in sol_events)
    deepseek_operations = sum(
        value.get("event") == "provider_operation_started" for value in deepseek_events
    )
    if sol_calls != 1 or deepseek_operations != 12:
        raise StateConflictError("existing smoke provider accounting changed")
    if any(value.get("event") == "invocation_failed" for value in deepseek_events):
        raise StateConflictError("existing smoke contains a failed Pi invocation")
    st_manifest = verify_isolated_sillytavern(root / "isolated_sillytavern")
    if not (root / "isolated_sillytavern" / "cera-smoke-logs").is_dir():
        raise StateConflictError("existing smoke omitted SillyTavern launch evidence")

    current_head = store.load_head(world_id="world-pi-scene-smoke", branch_id="branch-main")
    result = {
        "schema_version": "cera.pi_scene.live_smoke_evidence.v1",
        "status": "passed",
        "accepted_turns": 4,
        "routes": routes,
        "sol_calls": sol_calls,
        "deepseek_operations": deepseek_operations,
        "deepseek_cached_input_tokens": sum(
            int(value.get("cached_input_tokens", 0))
            for value in deepseek_events
            if value.get("event") == "provider_operation_completed"
        ),
        "regenerate_exact_sequence": True,
        "pi_reset_rehydrated": True,
        "recorder_failure_repaired": True,
        "provider_free_final_recording_repair": True,
        "route_transition": "ordinary-adult-adult-ordinary",
        "isolated_sillytavern_started": True,
        "isolated_sillytavern_routed_chat_completions": 2,
        "resumed_from_accepted_generation": 2,
        "isolated_sillytavern_user_data_copied": st_manifest["user_data_copied"],
        "isolated_sillytavern_manifest_sha256": st_manifest["manifest_sha256"],
        "accepted_head_sha256": current_head.accepted_head_sha256,
        "steps": [
            {
                "accepted_turn_id": value["receipt"]["accepted_turn_id"],
                "route": value["receipt"]["route"],
                "recording_status": "complete",
            }
            for value in accepted
        ],
    }
    result["evidence_sha256"] = canonical_sha256(result)
    evidence_path.write_bytes(canonical_bytes(result) + b"\n")
    return result


def _last_assistant_text(path: Path) -> str:
    output = ""
    for event in _read_jsonl(path):
        if event.get("type") != "message":
            continue
        message = event.get("message")
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        content = message.get("content")
        if isinstance(content, str):
            output = content
        elif isinstance(content, list):
            output = "".join(
                str(block.get("text", ""))
                for block in content
                if isinstance(block, dict) and block.get("type") == "text"
            )
    if not output.strip():
        raise StateConflictError("Pi session omitted final assistant text")
    return output


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    values = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    if any(not isinstance(value, dict) for value in values):
        raise StateConflictError("JSONL evidence contains a non-object")
    return values


def serve(
    runtime_root: Path,
    *,
    port: int,
    session_id: str,
    sol_ceiling: int,
    deepseek_ceiling: int,
    deepseek_per_invocation_ceiling: int,
    resume_from_runtime_root: Path | None = None,
) -> None:
    # Retained only as a CLI compatibility argument. Request-level session IDs
    # now resolve distinct worlds/branches and are the authoritative scope.
    del session_id
    token = __import__("os").environ.get("CERA_PI_SCENE_TOKEN", "")
    if len(token) < 24:
        raise ContractValidationError("CERA_PI_SCENE_TOKEN must contain at least 24 characters")
    runtime = build_live_runtime(
        runtime_root,
        sol_ceiling=sol_ceiling,
        deepseek_ceiling=deepseek_ceiling,
        deepseek_per_invocation_ceiling=deepseek_per_invocation_ceiling,
        seed_runtime_root=resume_from_runtime_root,
    )
    provider_stage_retry = getattr(runtime, "provider_stage_retry", None)
    retry_http_kwargs = (
        {} if provider_stage_retry is None else provider_stage_retry.http_adapter_kwargs()
    )
    adapter = PiSceneHttpAdapter(
        coordinator=runtime.coordinator,
        request_context_provider=build_session_context_provider(
            runtime.store,
            initial_hanezawa_doorway_seed(),
            workspace_resolver=getattr(runtime, "world_resolver", None),
        ),
        readable_debug=runtime.readable_debug,
        logic_route_resolver=lambda turn: accepted_logic_route(
            runtime.store,
            turn,
        ),
        full_model_controller=getattr(runtime, "full_model_controller", None),
        transport_retry_reinitializer=getattr(
            runtime,
            "transport_retry_reinitializer",
            None,
        ),
        transport_provider_ledger_snapshot=getattr(
            runtime,
            "transport_provider_ledger_snapshot",
            None,
        ),
        transport_retry_active_thread_snapshot=getattr(
            runtime,
            "transport_retry_active_thread_snapshot",
            None,
        ),
        transport_retry_fresh_thread_initializer=getattr(
            runtime,
            "transport_retry_fresh_thread_initializer",
            None,
        ),
        transport_completed_planner_abandoner=getattr(
            runtime,
            "transport_completed_planner_abandoner",
            None,
        ),
        **retry_http_kwargs,
    )
    if provider_stage_retry is not None:
        provider_stage_retry.bind_http_adapter(adapter)
    readable_debug_root = getattr(
        runtime.readable_debug,
        "root",
        (runtime_root / "debug" / "readable").resolve(),
    )
    protected_debug_name = getattr(
        runtime.readable_debug,
        "PROTECTED_DIRECTORY",
        ReadablePiSceneDebugLog.PROTECTED_DIRECTORY,
    )
    print(
        json.dumps(
            {
                "status": "cera_pi_scene_ready",
                "endpoint": f"http://127.0.0.1:{port}/v1",
                "model": PI_SCENE_AUTO_MODEL,
                "profile_id": PI_SCENE_PROFILE,
                "readable_debug_directory": str(readable_debug_root),
                "readable_debug_latest": str(readable_debug_root / "LATEST.md"),
                "protected_adult_debug_directory": str(readable_debug_root / protected_debug_name),
            },
            sort_keys=True,
        ),
        flush=True,
    )
    server = build_pi_scene_server(
        adapter,
        PiSceneServerConfigV1(
            host="127.0.0.1",
            port=port,
            authorization_token=token,
            approved_origins=("http://127.0.0.1:8000",),
        ),
    )
    try:
        server.serve_forever()
    finally:
        server.server_close()
        runtime.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("live-smoke", "repair-existing-smoke", "serve"))
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--port", type=int, default=5101)
    parser.add_argument("--session-id", default="cera-pi-scene-test")
    parser.add_argument("--sillytavern-source", type=Path, default=DEFAULT_SILLYTAVERN)
    parser.add_argument("--sol-ceiling", type=int, default=12)
    parser.add_argument("--deepseek-ceiling", type=int, default=60)
    parser.add_argument("--deepseek-per-invocation-ceiling", type=int, default=6)
    parser.add_argument("--resume-from-runtime-root", type=Path)
    args = parser.parse_args()
    if args.mode == "live-smoke":
        print(
            json.dumps(
                run_live_smoke(
                    args.runtime_root,
                    sillytavern_source=args.sillytavern_source,
                    sol_ceiling=args.sol_ceiling,
                    deepseek_ceiling=args.deepseek_ceiling,
                    deepseek_per_invocation_ceiling=args.deepseek_per_invocation_ceiling,
                    resume_from_runtime_root=args.resume_from_runtime_root,
                ),
                indent=2,
                sort_keys=True,
            )
        )
    elif args.mode == "repair-existing-smoke":
        print(json.dumps(repair_existing_smoke(args.runtime_root), indent=2, sort_keys=True))
    else:
        serve(
            args.runtime_root,
            port=args.port,
            session_id=args.session_id,
            sol_ceiling=args.sol_ceiling,
            deepseek_ceiling=args.deepseek_ceiling,
            deepseek_per_invocation_ceiling=(args.deepseek_per_invocation_ceiling),
            resume_from_runtime_root=args.resume_from_runtime_root,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
