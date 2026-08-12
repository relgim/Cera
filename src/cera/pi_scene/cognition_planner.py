"""Bind Pi Scene turn state to the full-model cognition planner."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from cera.cognition import (
    CharacterAutonomyMode,
    CognitionPlanV1,
    CognitionProviderTurnHandoffV1,
    CognitionTurnContextV1,
    LogicRoute,
)
from cera.continuous.operation_evidence import ProviderOperationEvidenceStoreV1
from cera.errors import ContractValidationError
from cera.provider_dispatch_guard import (
    assert_provider_dispatch_allowed,
    is_external_provider_boundary,
)
from cera.serialization import text_sha256, to_primitive

from .codex_planner import build_sequence_semantic_input
from .cognition_evidence import selected_validation_evidence
from .readable_debug import ReadablePiSceneDebugLog
from .runtime import PlannerTurnInputV1, PlannerTurnOutputV1


class CognitionPlannerSessionPort(Protocol):
    @property
    def active_thread_sha256(self) -> str | None: ...

    def plan(self, context: CognitionTurnContextV1) -> CognitionPlanV1: ...

    def take_provider_turn_handoff(self) -> CognitionProviderTurnHandoffV1: ...

    def reset_after_transport_failure(self, expected_thread_sha256: str) -> None: ...

    def prepare_fresh_thread(self) -> str: ...

    def abandon_completed_uncommitted(self, expected_thread_sha256: str) -> None: ...


class RetainedCognitionPlannerAdapter:
    """One ordinary-turn logic owner with controls and auditable decisions."""

    def __init__(
        self,
        session: CognitionPlannerSessionPort,
        *,
        operation_evidence: ProviderOperationEvidenceStoreV1 | None = None,
        readable_debug: ReadablePiSceneDebugLog | None = None,
    ) -> None:
        self.session = session
        self.operation_evidence = operation_evidence
        self.readable_debug = readable_debug
        self.last_context: CognitionTurnContextV1 | None = None
        self._turn_index = 0

    def plan(self, request: PlannerTurnInputV1) -> PlannerTurnOutputV1:
        self.last_context = None
        controls = request.request_controls
        if controls is None:
            raise ContractValidationError("full-model cognition requires typed request controls")
        assert_provider_dispatch_allowed(
            "pi_scene.cognition_planner.plan",
            external_provider_boundary=is_external_provider_boundary(self.session),
        )
        if self.operation_evidence is not None:
            self._turn_index += 1
            self.operation_evidence.begin_turn(
                ":".join(
                    (
                        request.world_id,
                        request.branch_id,
                        f"cognition-{self._turn_index:04d}",
                        text_sha256(request.exact_user_source)[:12],
                    )
                )
            )
        else:
            self._turn_index += 1

        semantic_input = build_sequence_semantic_input(request)
        context = CognitionTurnContextV1(
            turn=semantic_input,
            autonomy_mode=CharacterAutonomyMode(controls.character_autonomy),
            logic_route=LogicRoute.ORDINARY,
            available_provisional_record_ids=_provisional_ids(request.current_state),
        )
        plan = self.session.plan(context)
        handoff = self.session.take_provider_turn_handoff()
        self.last_context = context
        if self.readable_debug is not None:
            self.readable_debug.write(
                stage="codex-cognition-planner",
                identity=(
                    f"{request.world_id}-{request.branch_id}-cognition-{self._turn_index:04d}"
                ),
                sections={
                    "Exact user input": request.exact_user_source,
                    "Python cognition context sent to Codex": context,
                    "Codex cognition plan": plan,
                },
            )
        primitive = to_primitive(plan)
        return PlannerTurnOutputV1(
            sequence=primitive["sequence"],
            decision_bundle=primitive,
            provider_operations=1,
            validation_evidence=tuple(
                to_primitive(value)
                for value in selected_validation_evidence(
                    plan=plan,
                    turn=semantic_input,
                    handoff=handoff,
                )
            ),
        )

    def reset_provider_thread_after_transport_failure(
        self,
        expected_thread_sha256: str,
    ) -> None:
        """Archive the interrupted retained thread without running a model."""

        self.session.reset_after_transport_failure(expected_thread_sha256)
        self.last_context = None

    def active_provider_thread_sha256(self) -> str | None:
        """Expose only the active retained-thread hash for retry reconciliation."""

        return self.session.active_thread_sha256

    def prepare_fresh_provider_thread(self) -> str:
        """Create one empty fresh retained thread without a provider turn."""

        return self.session.prepare_fresh_thread()

    def abandon_completed_uncommitted_thread(
        self,
        expected_thread_sha256: str,
    ) -> None:
        """Retire a completed retained thread whose plan was not persisted."""

        self.session.abandon_completed_uncommitted(expected_thread_sha256)
        self.last_context = None


def _provisional_ids(state: Mapping[str, Any]) -> tuple[str, ...]:
    raw = state.get("provisional_canon_lineage", ())
    if isinstance(raw, (str, bytes)) or not isinstance(raw, (list, tuple)):
        raise ContractValidationError("provisional_canon_lineage must be an ordered list")
    output: list[str] = []
    for value in raw:
        if not isinstance(value, Mapping):
            raise ContractValidationError("provisional canon entry is not an object")
        record_id = value.get("provisional_canon_id")
        if not isinstance(record_id, str) or not record_id.strip():
            raise ContractValidationError("provisional canon entry has no identity")
        output.append(record_id)
    if len(output) != len(set(output)):
        raise ContractValidationError("provisional canon identities contain duplicates")
    return tuple(output)
