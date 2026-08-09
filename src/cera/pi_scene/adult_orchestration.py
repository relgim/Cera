"""Automatic protected adult-route orchestration for the Pi Scene runtime.

This module owns only route-state dispatch and provider-result classification.
It deliberately does not own accepted storage, HTTP, world workspaces, or
adult preparation semantics.  A prepared operation is suitable for durable
protected custody before Scene/Filter dispatch; a persisted execution can be
classified again after restart without another model call.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import ClassVar, Protocol

from cera.adult_pipeline.acceptance import AdultIntegratedExecutionV1
from cera.adult_pipeline.contracts import (
    AdultContextFactV1,
    AdultEntryReason,
    AdultFilterConflictV1,
    AdultNextRoute,
    AdultPromotionBundleV1,
    AdultRouteStateSnapshotV1,
    AdultSceneRequestV1,
)
from cera.adult_pipeline.integration import AdultScenePreparationV1
from cera.adult_pipeline.ports import AdultRouteStatePort
from cera.adult_pipeline.preparation import AdultTurnPreparationBuilder
from cera.cognition.contracts import CognitionPlanV1
from cera.errors import ContractValidationError, StateConflictError
from cera.serialization import canonical_sha256, domain_sha256, re_is_sha256, text_sha256

from .review_store import LeanSceneTurnInputV1

_IDENTITY = re.compile(r"[a-z][a-z0-9_.:-]{0,191}\Z")
_OPERATION_DOMAIN = "cera.pi_scene.adult_orchestration.operation.v1"


class AdultPipelineExecutionPort(Protocol):
    """The already-implemented protected Scene/Filter integration surface."""

    def prepare_scene_request(self, source: AdultScenePreparationV1) -> AdultSceneRequestV1: ...

    def execute(
        self,
        *,
        request_id: str,
        candidate_id: str,
        world_id: str,
        branch_id: str,
        accepted_head_sha256: str | None,
        scene_request: AdultSceneRequestV1,
    ) -> AdultIntegratedExecutionV1: ...


type CognitionHandoffFactory = Callable[[], CognitionPlanV1]


@dataclass(frozen=True, slots=True)
class PreparedAdultRouteOperationV1:
    """Frozen protected input ready for one Scene then Filter execution.

    ``scene_request`` may contain accepted protected continuity and therefore
    belongs only in the protected runtime.  ``operation_id`` and
    ``operation_sha256`` are privacy-safe handles for journals and receipts.
    """

    SCHEMA_VERSION: ClassVar[str] = "cera.pi_scene.prepared_adult_route_operation.v1"
    PROTECTION_SCOPE: ClassVar[str] = "protected_runtime_only"

    schema_version: str
    operation_id: str
    operation_sha256: str
    request_id: str
    candidate_id: str
    route_state: AdultRouteStateSnapshotV1
    scene_request: AdultSceneRequestV1

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("prepared adult operation schema changed")
        for field_name in ("operation_id", "request_id", "candidate_id"):
            _identity(getattr(self, field_name), f"prepared_adult.{field_name}")
        if not re_is_sha256(self.operation_sha256):
            raise ContractValidationError("prepared adult operation hash is invalid")
        _validate_entry_binding(self.route_state, self.scene_request)
        expected_sha256 = _operation_sha256(
            request_id=self.request_id,
            candidate_id=self.candidate_id,
            route_state=self.route_state,
            scene_request=self.scene_request,
        )
        if self.operation_sha256 != expected_sha256:
            raise ContractValidationError("prepared adult operation binding changed")
        if self.operation_id != _operation_id(expected_sha256):
            raise ContractValidationError("prepared adult operation identity changed")

    @classmethod
    def create(
        cls,
        *,
        request_id: str,
        candidate_id: str,
        route_state: AdultRouteStateSnapshotV1,
        scene_request: AdultSceneRequestV1,
    ) -> PreparedAdultRouteOperationV1:
        operation_sha256 = _operation_sha256(
            request_id=request_id,
            candidate_id=candidate_id,
            route_state=route_state,
            scene_request=scene_request,
        )
        return cls(
            schema_version=cls.SCHEMA_VERSION,
            operation_id=_operation_id(operation_sha256),
            operation_sha256=operation_sha256,
            request_id=request_id,
            candidate_id=candidate_id,
            route_state=route_state,
            scene_request=scene_request,
        )

    @property
    def bypassed_codex(self) -> bool:
        return self.scene_request.entry_reason is AdultEntryReason.ACCEPTED_ADULT_CONTINUATION


@dataclass(frozen=True, slots=True)
class PassedAdultRouteOperationV1:
    """Protected passed result exposing the exact atomic promotion envelope."""

    SCHEMA_VERSION: ClassVar[str] = "cera.pi_scene.passed_adult_route_operation.v1"
    PROTECTION_SCOPE: ClassVar[str] = "protected_runtime_only"

    schema_version: str
    prepared: PreparedAdultRouteOperationV1
    protected_execution: AdultIntegratedExecutionV1
    promotion_envelope: AdultPromotionBundleV1
    execution_sha256: str

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("passed adult operation schema changed")
        if not re_is_sha256(self.execution_sha256):
            raise ContractValidationError("passed adult execution hash is invalid")
        if self.execution_sha256 != self.protected_execution.execution_sha256:
            raise ContractValidationError("passed adult execution binding changed")
        _validate_execution_binding(self.prepared, self.protected_execution)
        if not self.protected_execution.result.eligible_for_atomic_acceptance:
            raise ContractValidationError("passed adult operation contains a rejection")
        if self.promotion_envelope != self.protected_execution.result.promotion_bundle():
            raise ContractValidationError("passed adult promotion envelope changed")

    @property
    def outcome_sha256(self) -> str:
        return canonical_sha256(self)

    @property
    def protected_exact_story_prose_sha256(self) -> str:
        return self.promotion_envelope.exact_story_prose_sha256


@dataclass(frozen=True, slots=True)
class RejectedAdultRouteOperationV1:
    """Protected typed rejection with no promotable artifact surface."""

    SCHEMA_VERSION: ClassVar[str] = "cera.pi_scene.rejected_adult_route_operation.v1"
    PROTECTION_SCOPE: ClassVar[str] = "protected_runtime_only"

    schema_version: str
    prepared: PreparedAdultRouteOperationV1
    protected_execution: AdultIntegratedExecutionV1
    conflict: AdultFilterConflictV1
    execution_sha256: str

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("rejected adult operation schema changed")
        if not re_is_sha256(self.execution_sha256):
            raise ContractValidationError("rejected adult execution hash is invalid")
        if self.execution_sha256 != self.protected_execution.execution_sha256:
            raise ContractValidationError("rejected adult execution binding changed")
        _validate_execution_binding(self.prepared, self.protected_execution)
        result = self.protected_execution.result
        if result.eligible_for_atomic_acceptance:
            raise ContractValidationError("rejected adult operation contains a pass")
        if result.filtered.invocation.decision.conflict != self.conflict:
            raise ContractValidationError("rejected adult conflict binding changed")

    @property
    def outcome_sha256(self) -> str:
        return canonical_sha256(self)

    @property
    def protected_exact_story_prose_sha256(self) -> str:
        prose = self.protected_execution.result.scene.invocation.output.exact_story_prose
        return text_sha256(prose)


type AdultRouteOperationOutcomeV1 = PassedAdultRouteOperationV1 | RejectedAdultRouteOperationV1


class AutomaticAdultRouteOrchestrator:
    """Select one adult preparation path, then run Scene and Filter exactly once.

    Accepted branch route state is the sole selector.  Adult craft OFF/ON/EX is
    consumed only by ``AdultTurnPreparationBuilder`` after that selection and
    cannot select the logic owner.
    """

    def __init__(
        self,
        *,
        route_state: AdultRouteStatePort,
        preparation_builder: AdultTurnPreparationBuilder,
        pipeline: AdultPipelineExecutionPort,
    ) -> None:
        self.route_state = route_state
        self.preparation_builder = preparation_builder
        self.pipeline = pipeline

    def prepare(
        self,
        *,
        request_id: str,
        candidate_id: str,
        turn_input: LeanSceneTurnInputV1,
        accepted_safe_projection: str,
        protected_adult_continuity: str | None,
        current_facts: tuple[AdultContextFactV1, ...],
        product_story_boundaries: tuple[str, ...],
        cognition_handoff_factory: CognitionHandoffFactory | None,
    ) -> PreparedAdultRouteOperationV1:
        """Freeze one provider-ready operation, bypassing Codex on adult state."""

        snapshot = self.route_state.current_logic_route(
            world_id=turn_input.world_id,
            branch_id=turn_input.branch_id,
        )
        _validate_snapshot_identity(snapshot, turn_input)
        if snapshot.current_logic_route is AdultNextRoute.ADULT:
            if snapshot.accepted_head_sha256 is None or snapshot.source_promotion_sha256 is None:
                raise StateConflictError(
                    "accepted adult continuation lacks accepted promotion custody"
                )
            if protected_adult_continuity is None:
                raise StateConflictError("accepted adult continuation lacks protected continuity")
            # Deliberately do not touch the factory: the accepted adult route
            # has one DeepSeek logic owner for this complete message.
            preparation = self.preparation_builder.from_accepted_adult_continuation(
                turn_input=turn_input,
                accepted_safe_projection=accepted_safe_projection,
                protected_adult_continuity=protected_adult_continuity,
                current_facts=current_facts,
                product_story_boundaries=product_story_boundaries,
            )
        else:
            if cognition_handoff_factory is None:
                raise StateConflictError("ordinary route requires an exact Codex adult handoff")
            cognition_plan = cognition_handoff_factory()
            if not isinstance(cognition_plan, CognitionPlanV1):
                raise ContractValidationError("Codex adult handoff returned the wrong type")
            preparation = self.preparation_builder.from_cognition_handoff(
                turn_input=turn_input,
                cognition_plan=cognition_plan,
                accepted_safe_projection=accepted_safe_projection,
                protected_adult_continuity=protected_adult_continuity,
                current_facts=current_facts,
                product_story_boundaries=product_story_boundaries,
            )

        scene_request = self.pipeline.prepare_scene_request(preparation)
        if scene_request.exact_current_source != turn_input.exact_user_source:
            raise ContractValidationError("adult preparation changed the exact current source")
        _validate_entry_binding(snapshot, scene_request)
        return PreparedAdultRouteOperationV1.create(
            request_id=request_id,
            candidate_id=candidate_id,
            route_state=snapshot,
            scene_request=scene_request,
        )

    def execute_prepared(
        self,
        prepared: PreparedAdultRouteOperationV1,
    ) -> AdultRouteOperationOutcomeV1:
        """Dispatch Scene+Filter after proving the accepted route did not move."""

        current = self.route_state.current_logic_route(
            world_id=prepared.route_state.world_id,
            branch_id=prepared.route_state.branch_id,
        )
        if current != prepared.route_state:
            raise StateConflictError("accepted adult route changed after preparation")
        execution = self.pipeline.execute(
            request_id=prepared.request_id,
            candidate_id=prepared.candidate_id,
            world_id=prepared.route_state.world_id,
            branch_id=prepared.route_state.branch_id,
            accepted_head_sha256=prepared.route_state.accepted_head_sha256,
            scene_request=prepared.scene_request,
        )
        return classify_prepared_adult_execution(prepared, execution)

    def run(
        self,
        *,
        request_id: str,
        candidate_id: str,
        turn_input: LeanSceneTurnInputV1,
        accepted_safe_projection: str,
        protected_adult_continuity: str | None,
        current_facts: tuple[AdultContextFactV1, ...],
        product_story_boundaries: tuple[str, ...],
        cognition_handoff_factory: CognitionHandoffFactory | None,
    ) -> AdultRouteOperationOutcomeV1:
        """Convenience path; durable runtimes should journal the prepared phase."""

        prepared = self.prepare(
            request_id=request_id,
            candidate_id=candidate_id,
            turn_input=turn_input,
            accepted_safe_projection=accepted_safe_projection,
            protected_adult_continuity=protected_adult_continuity,
            current_facts=current_facts,
            product_story_boundaries=product_story_boundaries,
            cognition_handoff_factory=cognition_handoff_factory,
        )
        return self.execute_prepared(prepared)


def classify_prepared_adult_execution(
    prepared: PreparedAdultRouteOperationV1,
    protected_execution: AdultIntegratedExecutionV1,
) -> AdultRouteOperationOutcomeV1:
    """Pure restart/replay seam: classify durable bytes with zero dispatch."""

    _validate_execution_binding(prepared, protected_execution)
    result = protected_execution.result
    if result.eligible_for_atomic_acceptance:
        return PassedAdultRouteOperationV1(
            schema_version=PassedAdultRouteOperationV1.SCHEMA_VERSION,
            prepared=prepared,
            protected_execution=protected_execution,
            promotion_envelope=result.promotion_bundle(),
            execution_sha256=protected_execution.execution_sha256,
        )
    conflict = result.filtered.invocation.decision.conflict
    if conflict is None:
        raise ContractValidationError("adult Filter rejection lost its typed conflict")
    return RejectedAdultRouteOperationV1(
        schema_version=RejectedAdultRouteOperationV1.SCHEMA_VERSION,
        prepared=prepared,
        protected_execution=protected_execution,
        conflict=conflict,
        execution_sha256=protected_execution.execution_sha256,
    )


def _validate_snapshot_identity(
    snapshot: AdultRouteStateSnapshotV1,
    turn_input: LeanSceneTurnInputV1,
) -> None:
    if snapshot.world_id != turn_input.world_id or snapshot.branch_id != turn_input.branch_id:
        raise StateConflictError("adult route-state snapshot belongs to another branch")


def _validate_entry_binding(
    snapshot: AdultRouteStateSnapshotV1,
    scene_request: AdultSceneRequestV1,
) -> None:
    if snapshot.current_logic_route is AdultNextRoute.ADULT:
        if scene_request.entry_reason is not AdultEntryReason.ACCEPTED_ADULT_CONTINUATION:
            raise ContractValidationError("accepted adult route did not use continuation entry")
        if scene_request.adult_handoff is not None:
            raise ContractValidationError("accepted adult continuation unexpectedly called Codex")
    elif scene_request.entry_reason is not AdultEntryReason.CODEX_ADULT_HANDOFF:
        raise ContractValidationError("ordinary route did not bind an exact Codex handoff")


def _validate_execution_binding(
    prepared: PreparedAdultRouteOperationV1,
    execution: AdultIntegratedExecutionV1,
) -> None:
    result = execution.result
    custody = result.scene.custody
    expected = prepared.route_state
    if (
        custody.request_id != prepared.request_id
        or custody.candidate_id != prepared.candidate_id
        or custody.world_id != expected.world_id
        or custody.branch_id != expected.branch_id
        or custody.accepted_head_sha256 != expected.accepted_head_sha256
        or result.scene.request != prepared.scene_request
    ):
        raise StateConflictError("adult execution belongs to another prepared operation")


def _operation_sha256(
    *,
    request_id: str,
    candidate_id: str,
    route_state: AdultRouteStateSnapshotV1,
    scene_request: AdultSceneRequestV1,
) -> str:
    return domain_sha256(
        _OPERATION_DOMAIN,
        {
            "schema_version": PreparedAdultRouteOperationV1.SCHEMA_VERSION,
            "request_id": request_id,
            "candidate_id": candidate_id,
            "route_state": route_state,
            "scene_request": scene_request,
        },
    )


def _operation_id(operation_sha256: str) -> str:
    return f"adult_operation:{operation_sha256[:32]}"


def _identity(value: str, field_name: str) -> None:
    if type(value) is not str or _IDENTITY.fullmatch(value) is None:
        raise ContractValidationError(f"{field_name} must be a stable identity")
