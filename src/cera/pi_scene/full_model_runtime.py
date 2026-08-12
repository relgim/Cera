"""Provider-neutral launcher seams for the ordinary full-model route.

The retained cognition thread is branch-local soft continuity.  Its world MCP
bridge is deliberately not retained: every provider operation receives one
fresh request-bound bridge for the exact world/branch workspace.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import replace
from pathlib import Path
from secrets import token_hex
from typing import Any, Protocol

from cera.cognition import (
    CognitionDynamicEvidenceV1,
    CognitionPlanV1,
    CognitionProviderCompletedTurnV1,
    CognitionProviderTurnHandoffV1,
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
from cera.sequence_first.contracts import PROTECTED_USER_ID, ProviderReferenceScopeV1
from cera.serialization import canonical_sha256, text_sha256

from .planner_state import PlannerThreadStateStore, PlannerThreadStateV1
from .readable_debug import ReadablePiSceneDebugLog
from .retrieval_tools import RetrievalProviderRole
from .runtime import PlannerTurnInputV1, PlannerTurnOutputV1
from .world_workspace import BranchBoundWorldMcpFactory

CognitionSessionFactory = Callable[
    [str, str, Any],
    PersistentCognitionPlannerSession,
]

COGNITION_THREAD_COMPATIBILITY_SCHEMA = "cera.pi_scene.cognition_thread_compatibility.v6"
COGNITION_WORLD_TOOLS_COMPATIBILITY = "cera.branch_bound_named_retrieval_mcp.v4"


class CognitionPlannerAdapterPort(Protocol):
    def plan(self, request: PlannerTurnInputV1) -> PlannerTurnOutputV1: ...


def cognition_thread_compatibility_sha256() -> str:
    """Bind durable retained threads to the complete cognition contract."""

    route = cognition_planner_route()
    return canonical_sha256(
        {
            "schema_version": COGNITION_THREAD_COMPATIBILITY_SCHEMA,
            "profile": COGNITION_PLANNER_PROFILE,
            "base_instructions_sha256": text_sha256(COGNITION_PLANNER_BASE_INSTRUCTIONS),
            "cognition_plan_schema": CognitionPlanV1.SCHEMA_VERSION,
            "dynamic_evidence_schema": CognitionDynamicEvidenceV1.SCHEMA_VERSION,
            "provider_turn_handoff_schema": CognitionProviderTurnHandoffV1.SCHEMA_VERSION,
            "provider": route.provider,
            "model": route.model_name,
            "adapter": COGNITION_PLANNER_ADAPTER,
            "prompt": COGNITION_PLANNER_PROMPT,
            "world_tools": COGNITION_WORLD_TOOLS_COMPATIBILITY,
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
        prior_compatibility = state_store.active_compatibility_sha256(
            session_id=session_id,
            reasoning_effort=reasoning_effort,
        )
        if (
            prior_compatibility is not None
            and prior_compatibility != compatibility_sha256
        ):
            state_store.retire_incompatible_thread(
                session_id=session_id,
                reasoning_effort=reasoning_effort,
                prior_compatibility_sha256=prior_compatibility,
                new_compatibility_sha256=compatibility_sha256,
            )
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

        def archive_interrupted(thread_id: str) -> None:
            current = state_store.load(
                session_id=session_id,
                reasoning_effort=reasoning_effort,
                compatibility_sha256=compatibility_sha256,
            )
            if current is None or current.thread_id != thread_id:
                raise StateConflictError("cognition thread changed before transport retry archival")
            state_store.archive_interrupted_transport_thread(current)

        def verify_interrupted(thread_id_sha256: str) -> bool:
            return state_store.has_interrupted_thread_hash(
                session_id=session_id,
                reasoning_effort=reasoning_effort,
                compatibility_sha256=compatibility_sha256,
                thread_id_sha256=thread_id_sha256,
            )

        def archive_completed_uncommitted(thread_id: str) -> None:
            current = state_store.load(
                session_id=session_id,
                reasoning_effort=reasoning_effort,
                compatibility_sha256=compatibility_sha256,
            )
            if current is None or current.thread_id != thread_id:
                raise StateConflictError(
                    "cognition thread changed before completed-result retirement"
                )
            state_store.archive_completed_uncommitted_thread(current)

        def verify_completed_uncommitted(thread_id_sha256: str) -> bool:
            return state_store.has_completed_uncommitted_thread_hash(
                session_id=session_id,
                reasoning_effort=reasoning_effort,
                compatibility_sha256=compatibility_sha256,
                thread_id_sha256=thread_id_sha256,
            )

        return PersistentCognitionPlannerSession(
            backend,
            restored_thread_id=None if state is None else state.thread_id,
            persist_thread_id=persist,
            archive_interrupted_thread=archive_interrupted,
            verify_interrupted_thread=verify_interrupted,
            archive_completed_uncommitted_thread=archive_completed_uncommitted,
            verify_completed_uncommitted_thread=verify_completed_uncommitted,
        )

    return build


class BranchBoundCognitionPlannerBackend(CodexCognitionPlannerBackend):
    """Refresh the exact branch MCP bridge for every retained cognition turn."""

    def __init__(
        self,
        *,
        world_mcp_factory: BranchBoundWorldMcpFactory,
        retrieval_request_nonce_factory: Callable[[], str] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(world_bridge=None, **kwargs)
        self.world_mcp_factory = world_mcp_factory
        self._retrieval_request_index = 0
        self._retrieval_request_nonce_factory: Callable[[], str] = (
            (lambda: token_hex(16))
            if retrieval_request_nonce_factory is None
            else retrieval_request_nonce_factory
        )

    def run_cognition_turn(
        self,
        *,
        thread_id: str,
        prompt: str,
        context: CognitionTurnContextV1,
        reference_scope: ProviderReferenceScopeV1,
    ) -> CognitionProviderCompletedTurnV1:
        if self.world_bridge is not None:
            raise StateConflictError("cognition Planner retained a prior request MCP bridge")
        self._retrieval_request_index += 1
        nonce = self._retrieval_request_nonce_factory()
        if not isinstance(nonce, str) or not nonce.strip():
            raise StateConflictError("cognition retrieval request nonce is invalid")
        workspace = self.world_mcp_factory.workspace
        bridge = self.world_mcp_factory.bridge(
            request_id=(
                "cognition_request_"
                + canonical_sha256(
                    {
                        "schema_version": "cera.pi_scene.cognition_retrieval_request.v1",
                        "world_id": workspace.world_id,
                        "branch_id": workspace.branch_id,
                        "turn_semantic_sha256": canonical_sha256(context.turn),
                        "provider_thread_sha256": text_sha256(thread_id),
                        "operation_index": self._retrieval_request_index,
                        "nonce": nonce,
                    }
                )[:32]
            ),
            private_character_ids=tuple(
                value
                for value in reference_scope.known_character_ids
                if value != PROTECTED_USER_ID
            ),
            role=RetrievalProviderRole.COGNITION_PLANNER,
        )
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

    def reset_provider_thread_after_transport_failure(
        self,
        expected_thread_sha256: str,
    ) -> None:
        reset = getattr(
            self.planner,
            "reset_provider_thread_after_transport_failure",
            None,
        )
        if not callable(reset):
            raise StateConflictError(
                "cognition Planner cannot archive an interrupted provider thread"
            )
        reset(expected_thread_sha256)

    def active_provider_thread_sha256(self) -> str | None:
        """Expose the exact retained Planner thread hash for retry custody."""

        read = getattr(self.planner, "active_provider_thread_sha256", None)
        if not callable(read):
            raise StateConflictError("cognition Planner cannot expose retained-thread custody")
        value = read()
        if value is not None and not isinstance(value, str):
            raise StateConflictError("cognition Planner thread hash changed shape")
        return value

    def prepare_fresh_provider_thread(self) -> str:
        """Create one empty post-archive Planner thread without a model turn."""

        prepare = getattr(self.planner, "prepare_fresh_provider_thread", None)
        if not callable(prepare):
            raise StateConflictError("cognition Planner cannot prepare a fresh retained thread")
        value = prepare()
        if not isinstance(value, str):
            raise StateConflictError("cognition fresh Planner thread hash changed shape")
        return value

    def abandon_completed_uncommitted_thread(
        self,
        expected_thread_sha256: str,
    ) -> None:
        """Retire a completed Planner thread that never entered durable progress."""

        abandon = getattr(
            self.planner,
            "abandon_completed_uncommitted_thread",
            None,
        )
        if not callable(abandon):
            raise StateConflictError(
                "cognition Planner cannot retire a completed uncommitted thread"
            )
        abandon(expected_thread_sha256)


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
        protected: bool = False,
    ) -> Path | None:
        return self.delegate.write(
            stage=stage,
            identity=f"cognition-{text_sha256(identity)[:24]}",
            sections={"Exact runtime identity": identity, **sections},
            protected=protected,
        )
