"""Bind Pi Scene turn state to the full-model cognition planner."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from cera.cognition import (
    CharacterAutonomyMode,
    CognitionPlanV1,
    CognitionTurnContextV1,
    CognitionValidationContextV1,
    LogicRoute,
    validate_cognition_plan,
)
from cera.continuous.operation_evidence import ProviderOperationEvidenceStoreV1
from cera.errors import ContractValidationError
from cera.serialization import text_sha256, to_primitive

from .codex_planner import build_sequence_semantic_input
from .readable_debug import ReadablePiSceneDebugLog
from .runtime import PlannerTurnInputV1


class CognitionPlannerSessionPort(Protocol):
    def plan(self, context: CognitionTurnContextV1) -> CognitionPlanV1: ...


@dataclass(frozen=True, slots=True)
class CognitionPlannerTurnOutputV1:
    sequence: Mapping[str, Any]
    decision_bundle: Mapping[str, Any]
    provider_operations: int

    def __post_init__(self) -> None:
        if not self.sequence or not self.decision_bundle:
            raise ContractValidationError("cognition Planner returned an empty bundle")
        if type(self.provider_operations) is not int or self.provider_operations < 1:
            raise ContractValidationError("cognition Planner operation count is invalid")


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

    def plan(self, request: PlannerTurnInputV1) -> CognitionPlannerTurnOutputV1:
        controls = request.request_controls
        if controls is None:
            raise ContractValidationError("full-model cognition requires typed request controls")
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
        validate_cognition_plan(
            plan,
            turn=semantic_input,
            context=CognitionValidationContextV1(
                autonomy_mode=context.autonomy_mode,
                logic_route=context.logic_route,
                available_evidence_refs=(
                    semantic_input.current_source_key,
                    *(value.evidence_key for value in semantic_input.evidence_records),
                ),
                available_provisional_record_ids=(context.available_provisional_record_ids),
            ),
        )
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
        return CognitionPlannerTurnOutputV1(
            sequence=primitive["sequence"],
            decision_bundle=primitive,
            provider_operations=1,
        )


def _provisional_ids(state: Mapping[str, Any]) -> tuple[str, ...]:
    raw = state.get("provisional_canon_lineage", ())
    if isinstance(raw, (str, bytes)) or not isinstance(raw, (list, tuple)):
        raise ContractValidationError("provisional_canon_lineage must be an ordered list")
    output: list[str] = []
    for value in raw:
        if not isinstance(value, Mapping):
            raise ContractValidationError("provisional canon entry is not an object")
        record_id = value.get("provisional_record_id")
        if not isinstance(record_id, str) or not record_id.strip():
            raise ContractValidationError("provisional canon entry has no identity")
        output.append(record_id)
    if len(output) != len(set(output)):
        raise ContractValidationError("provisional canon identities contain duplicates")
    return tuple(output)
