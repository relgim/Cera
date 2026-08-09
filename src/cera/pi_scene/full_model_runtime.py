"""Provider-neutral launcher seams for the ordinary full-model route.

The retained cognition thread is branch-local soft continuity.  Its world MCP
bridge is deliberately not retained: every provider operation receives one
fresh request-bound bridge for the exact world/branch workspace.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import replace
from typing import Any, Protocol

from cera.cognition import (
    CognitionPlanV1,
    CognitionTurnContextV1,
    PersistentCognitionPlannerSession,
)
from cera.cognition.prompting import (
    COGNITION_PLANNER_BASE_INSTRUCTIONS,
    COGNITION_PLANNER_PROFILE,
)
from cera.cognition.provider import (
    COGNITION_PLANNER_ADAPTER,
    COGNITION_PLANNER_PROMPT,
    CodexCognitionPlannerBackend,
    cognition_planner_route,
)
from cera.errors import StateConflictError
from cera.sequence_first.contracts import ProviderReferenceScopeV1
from cera.serialization import canonical_sha256, text_sha256

from .planner_state import PlannerThreadStateStore, PlannerThreadStateV1
from .readable_debug import ReadablePiSceneDebugLog
from .runtime import PlannerTurnInputV1, PlannerTurnOutputV1
from .world_workspace import BranchBoundWorldMcpFactory

CognitionSessionFactory = Callable[
    [str, str, Any],
    PersistentCognitionPlannerSession,
]


class CognitionPlannerAdapterPort(Protocol):
    def plan(self, request: PlannerTurnInputV1) -> PlannerTurnOutputV1: ...


def cognition_thread_compatibility_sha256() -> str:
    """Bind durable retained threads to the complete cognition contract."""

    route = cognition_planner_route()
    return canonical_sha256(
        {
            "schema_version": "cera.pi_scene.cognition_thread_compatibility.v1",
            "profile": COGNITION_PLANNER_PROFILE,
            "base_instructions_sha256": text_sha256(COGNITION_PLANNER_BASE_INSTRUCTIONS),
            "cognition_plan_schema": CognitionPlanV1.SCHEMA_VERSION,
            "provider": route.provider,
            "model": route.model_name,
            "adapter": COGNITION_PLANNER_ADAPTER,
            "prompt": COGNITION_PLANNER_PROMPT,
            "world_tools": "cera.branch_bound_world_mcp.private_scoped.v1",
        }
    )


def default_cognition_session_factory(
    runtime_root: Any,
) -> CognitionSessionFactory:
    """Restore or create one hash-bound cognition thread per chat and effort."""

    state_store = PlannerThreadStateStore(runtime_root / "planner_threads")
    compatibility_sha256 = cognition_thread_compatibility_sha256()

    def build(
        session_id: str,
        reasoning_effort: str,
        backend: Any,
    ) -> PersistentCognitionPlannerSession:
        state = state_store.load(
            session_id=session_id,
            reasoning_effort=reasoning_effort,
            compatibility_sha256=compatibility_sha256,
        )

        def persist(thread_id: str) -> None:
            state_store.persist(
                PlannerThreadStateV1(
                    session_id=session_id,
                    reasoning_effort=reasoning_effort,
                    compatibility_sha256=compatibility_sha256,
                    thread_id=thread_id,
                )
            )

        return PersistentCognitionPlannerSession(
            backend,
            restored_thread_id=None if state is None else state.thread_id,
            persist_thread_id=persist,
        )

    return build


class BranchBoundCognitionPlannerBackend(CodexCognitionPlannerBackend):
    """Refresh the exact branch MCP bridge for every retained cognition turn."""

    def __init__(
        self,
        *,
        world_mcp_factory: BranchBoundWorldMcpFactory,
        **kwargs: Any,
    ) -> None:
        super().__init__(world_bridge=None, **kwargs)
        self.world_mcp_factory = world_mcp_factory

    def run_cognition_turn(
        self,
        *,
        thread_id: str,
        prompt: str,
        context: CognitionTurnContextV1,
        reference_scope: ProviderReferenceScopeV1,
    ) -> CognitionPlanV1:
        if self.world_bridge is not None:
            raise StateConflictError("cognition Planner retained a prior request MCP bridge")
        bridge = self.world_mcp_factory.bridge()
        self.world_bridge = bridge
        try:
            return super().run_cognition_turn(
                thread_id=thread_id,
                prompt=prompt,
                context=context,
                reference_scope=reference_scope,
            )
        except BaseException:
            bridge.abort()
            raise
        finally:
            self.world_bridge = None


class RetrievalDirectedCognitionPlanner:
    """Keep the turn delta small while full evidence stays available by MCP.

    Genesis projections and dossiers can be hundreds of kilobytes per
    character.  They are authority in the branch workspace, not model-visible
    packet fields.  The current packet carries only the relevant identities;
    the retained cognition owner retrieves exact supporting records through
    its request-bound world tools.
    """

    def __init__(self, planner: CognitionPlannerAdapterPort) -> None:
        self.planner = planner

    def plan(self, request: PlannerTurnInputV1) -> PlannerTurnOutputV1:
        identities = {
            character_id: {
                "character_id": character_id,
                "authoritative_context": "branch_bound_world_mcp",
            }
            for character_id in request.characters
        }
        return self.planner.plan(
            replace(
                request,
                characters=identities,
                relationships={},
                relevant_memories={},
            )
        )


class PathBudgetedCognitionDebugLog(ReadablePiSceneDebugLog):
    """Keep full semantic identities in content, not Windows filenames."""

    def __init__(self, delegate: ReadablePiSceneDebugLog) -> None:
        self.delegate = delegate

    def write(
        self,
        *,
        stage: str,
        identity: str,
        sections: Mapping[str, Any],
    ) -> None:
        self.delegate.write(
            stage=stage,
            identity=f"cognition-{text_sha256(identity)[:24]}",
            sections={"Exact runtime identity": identity, **sections},
        )
