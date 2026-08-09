"""Per-operation branch MCP binding for retained sequence-first Planners."""

from __future__ import annotations

from typing import Any

from cera.errors import StateConflictError
from cera.sequence_first.contracts import ProviderReferenceScopeV1, SequenceDraftV1
from cera.sequence_first.provider import SequenceFirstPlannerCodexBackend

from .world_workspace import BranchBoundWorldMcpFactory


class BranchBoundSequenceFirstPlannerBackend(SequenceFirstPlannerCodexBackend):
    """Refresh the one-use request MCP bridge for every retained-thread turn.

    Provider conversation continuity may persist across accepted turns, but a
    local MCP bridge is a request resource.  Reusing one bridge would either
    expose stale custody or fail on the second turn, so each operation receives
    a fresh bridge bound to the same exact semantic branch.
    """

    def __init__(
        self,
        *,
        world_mcp_factory: BranchBoundWorldMcpFactory,
        **kwargs: Any,
    ) -> None:
        super().__init__(world_bridge=None, **kwargs)
        self.world_mcp_factory = world_mcp_factory

    def run_planner_turn(
        self,
        *,
        thread_id: str,
        prompt: str,
        reference_scope: ProviderReferenceScopeV1,
    ) -> SequenceDraftV1:
        if self.world_bridge is not None:
            raise StateConflictError("Planner retained a prior request MCP bridge")
        bridge = self.world_mcp_factory.bridge()
        self.world_bridge = bridge
        try:
            return super().run_planner_turn(
                thread_id=thread_id,
                prompt=prompt,
                reference_scope=reference_scope,
            )
        except BaseException:
            bridge.abort()
            raise
        finally:
            self.world_bridge = None
