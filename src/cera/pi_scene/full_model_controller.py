"""One-message logic-owner routing for the CERA full-model runtime.

The controller chooses exactly one narrative logic owner.  It either binds one
retained Codex cognition result to the ordinary Writer/Luna path, transfers
that exact result at its declared adult boundary, or continues an already
accepted adult branch without invoking Codex.  Python owns only identity,
accepted-head checks, and atomic publication.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar, Protocol, cast

from cera.adult_pipeline.acceptance import AdultAcceptedTurnEnvelopeV1
from cera.adult_pipeline.contracts import (
    AdultContextFactV1,
    AdultNextRoute,
    AdultRouteStateSnapshotV1,
    BoundAdultPromotionV1,
)
from cera.cognition.contracts import CognitionPlanV1
from cera.errors import ContractValidationError, StateConflictError
from cera.schema import from_mapping
from cera.serialization import (
    canonical_json,
    canonical_sha256,
    domain_sha256,
    re_is_sha256,
    text_sha256,
)

from .adult_operation_contracts import (
    AdultOperationDecisionAction,
    AdultOperationDecisionV1,
    AdultOperationDispatchUncertainError,
)
from .adult_operation_store import (
    ProtectedAdultOperationController,
    ProtectedAdultOperationStore,
)
from .adult_orchestration import (
    AutomaticAdultRouteOrchestrator,
    PassedAdultRouteOperationV1,
    PreparedAdultRouteOperationV1,
    RejectedAdultRouteOperationV1,
)
from .contracts import SceneRoute
from .review_store import LeanReviewRecordV1, LeanSceneTurnInputV1
from .runtime import BoundPlannerTurnOutputV1, LeanPiSceneCoordinator
from .store import LeanSceneStore


@dataclass(frozen=True, slots=True)
class AdultExecutionContextV1:
    """Already-separated safe/protected context for the adult logic owner."""

    accepted_safe_projection: str
    protected_adult_continuity: str | None
    current_facts: tuple[AdultContextFactV1, ...]
    product_story_boundaries: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.accepted_safe_projection.strip():
            raise ContractValidationError("adult safe projection is empty")
        if self.protected_adult_continuity is not None:
            protected = self.protected_adult_continuity
            if not protected.strip():
                raise ContractValidationError("adult protected continuity is empty")
            safe_text = (
                self.accepted_safe_projection,
                *(value.authoritative_fact for value in self.current_facts),
                *self.product_story_boundaries,
            )
            if any(protected in value for value in safe_text):
                raise ContractValidationError(
                    "adult protected continuity escaped its dedicated field"
                )


class AdultExecutionContextProvider(Protocol):
    def __call__(
        self,
        turn: LeanSceneTurnInputV1,
        route_state: AdultRouteStateSnapshotV1,
    ) -> AdultExecutionContextV1: ...


class AdultOrchestratorFactory(Protocol):
    def __call__(
        self,
        turn: LeanSceneTurnInputV1,
        request_id: str,
        candidate_id: str,
    ) -> AutomaticAdultRouteOrchestrator: ...


type AdultOperationControllerFactory = Callable[[], ProtectedAdultOperationController]


@dataclass(frozen=True, slots=True)
class AcceptedAdultTurnV1:
    """Protected accepted result plus its atomic public receipt."""

    SCHEMA_VERSION: ClassVar[str] = "cera.pi_scene.accepted_adult_turn.v1"

    schema_version: str
    outcome: PassedAdultRouteOperationV1
    envelope: AdultAcceptedTurnEnvelopeV1
    promotion: BoundAdultPromotionV1
    planner_provider_operations: int

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("accepted adult turn schema changed")
        if self.outcome.promotion_envelope != self.envelope.promotion_bundle:
            raise ContractValidationError("accepted adult turn changed its promotion bundle")
        if self.promotion.bundle != self.envelope.promotion_bundle:
            raise ContractValidationError("accepted adult turn receipt changed its bundle")
        if self.promotion.receipt.accepted_turn_id != self.envelope.accepted_turn_id:
            raise ContractValidationError("accepted adult turn receipt changed its identity")
        _validate_adult_planner_operations(
            self.outcome,
            self.planner_provider_operations,
        )

    @property
    def exact_story_prose(self) -> str:
        return cast(str, self.envelope.promotion_bundle.exact_story_prose)

    @property
    def next_route(self) -> AdultNextRoute:
        return self.envelope.promotion_bundle.next_route


@dataclass(frozen=True, slots=True)
class RejectedAdultTurnV1:
    """Inspectable protected rejection; no accepted branch effect exists."""

    SCHEMA_VERSION: ClassVar[str] = "cera.pi_scene.rejected_adult_turn.v1"

    schema_version: str
    outcome: RejectedAdultRouteOperationV1
    planner_provider_operations: int

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("rejected adult turn schema changed")
        _validate_adult_planner_operations(
            self.outcome,
            self.planner_provider_operations,
        )

    @property
    def exact_story_prose(self) -> str:
        return cast(
            str,
            self.outcome.protected_execution.result.scene.invocation.output.exact_story_prose,
        )


type FullModelTurnOutcomeV1 = LeanReviewRecordV1 | AcceptedAdultTurnV1 | RejectedAdultTurnV1


class FullModelSceneController:
    """Route one complete user message through exactly one logic owner."""

    def __init__(
        self,
        *,
        ordinary: LeanPiSceneCoordinator,
        store: LeanSceneStore,
        adult_orchestrator_factory: AdultOrchestratorFactory,
        adult_context_provider: AdultExecutionContextProvider,
        adult_operation_controller_factory: AdultOperationControllerFactory | None = None,
    ) -> None:
        self.ordinary = ordinary
        self.store = store
        self.adult_orchestrator_factory = adult_orchestrator_factory
        self.adult_context_provider = adult_context_provider
        self.adult_operation_controller_factory = (
            adult_operation_controller_factory
            if adult_operation_controller_factory is not None
            else lambda: ProtectedAdultOperationController(
                ProtectedAdultOperationStore(self.store.root.parent / "protected_adult")
            )
        )

    def recover_committed_adult(
        self,
        *,
        request_id: str,
        turn: LeanSceneTurnInputV1,
    ) -> AcceptedAdultTurnV1 | None:
        """Terminalize a promotion committed before its operation decision.

        This narrow recovery path performs no planning, preparation, or provider
        dispatch.  It considers only the current accepted adult head, verifies
        that its exact protected envelope and operation belong to this request,
        then records the missing hash-only accept decision idempotently.
        """

        head = self.store.load_head(world_id=turn.world_id, branch_id=turn.branch_id)
        receipt = head.receipt
        if receipt is None or receipt.route is not SceneRoute.ADULT:
            return None
        envelope, promotion = self.store.load_promoted_adult_acceptance(
            world_id=turn.world_id,
            branch_id=turn.branch_id,
            accepted_turn_id=receipt.accepted_turn_id,
        )
        if envelope.request_id != request_id:
            return None
        if (
            envelope.world_id != turn.world_id
            or envelope.branch_id != turn.branch_id
            or envelope.scene_id != turn.scene_id
            or envelope.exact_current_source != turn.exact_user_source
        ):
            raise StateConflictError(
                "committed adult recovery changed exact request or branch custody"
            )

        operation_controller = self.adult_operation_controller_factory()
        record = operation_controller.store.lookup(
            request_id=request_id,
            candidate_id=envelope.candidate_id,
        )
        outcome = record.outcome
        if not isinstance(outcome, PassedAdultRouteOperationV1):
            raise StateConflictError(
                "committed adult promotion lacks its exact passed operation"
            )
        planner_provider_operations = _load_adult_planner_telemetry(
            operation_controller,
            record.prepared,
        )
        reconstructed = outcome.protected_execution.acceptance_envelope(
            accepted_turn_id=envelope.accepted_turn_id,
            parent_accepted_turn_id=envelope.parent_accepted_turn_id,
            scene_id=envelope.scene_id,
            generation=envelope.generation,
            creator_action=envelope.creator_action,
        )
        if reconstructed != envelope or outcome.promotion_envelope != promotion.bundle:
            raise StateConflictError(
                "committed adult promotion changed its protected operation binding"
            )
        operation_controller.store.mark_accepted(
            request_id=request_id,
            candidate_id=envelope.candidate_id,
            decision=_adult_accept_decision(
                operation_sha256=record.prepared.operation_sha256,
                promotion=promotion,
            ),
        )
        return AcceptedAdultTurnV1(
            schema_version=AcceptedAdultTurnV1.SCHEMA_VERSION,
            outcome=outcome,
            envelope=envelope,
            promotion=promotion,
            planner_provider_operations=planner_provider_operations,
        )

    def recover_completed_adult_operation(
        self,
        *,
        request_id: str,
        turn: LeanSceneTurnInputV1,
    ) -> AcceptedAdultTurnV1 | RejectedAdultTurnV1 | None:
        """Recover one durable adult execution before HTTP progress exists.

        A committed promotion is terminalized first.  Otherwise the original
        candidate is re-derived only from the still-current pre-turn route and
        request custody.  No Planner, context provider, orchestrator, Scene, or
        Filter callback is reachable from this method.
        """

        promoted = self.recover_committed_adult(request_id=request_id, turn=turn)
        if promoted is not None:
            return promoted

        route_state = self.store.current_logic_route(
            world_id=turn.world_id,
            branch_id=turn.branch_id,
        )
        candidate_id = _adult_candidate_id(
            request_id=request_id,
            turn=turn,
            route_state=route_state,
        )
        operation_controller = self.adult_operation_controller_factory()
        record = operation_controller.store.lookup_optional(
            request_id=request_id,
            candidate_id=candidate_id,
        )
        if record is None:
            return None
        _validate_recovered_adult_preparation(
            record.prepared,
            turn=turn,
            route_state=route_state,
        )
        planner_provider_operations = _load_adult_planner_telemetry(
            operation_controller,
            record.prepared,
        )
        outcome = record.outcome
        if outcome is None:
            raise AdultOperationDispatchUncertainError(
                request_id=request_id,
                candidate_id=candidate_id,
            )
        if isinstance(outcome, RejectedAdultRouteOperationV1):
            return RejectedAdultTurnV1(
                schema_version=RejectedAdultTurnV1.SCHEMA_VERSION,
                outcome=outcome,
                planner_provider_operations=planner_provider_operations,
            )
        if not isinstance(outcome, PassedAdultRouteOperationV1):
            raise StateConflictError("adult operation recovered an unsupported outcome")
        return self._promote_passed_operation(
            turn=turn,
            operation_controller=operation_controller,
            outcome=outcome,
            planner_provider_operations=planner_provider_operations,
        )

    def complete(
        self,
        *,
        request_id: str,
        turn: LeanSceneTurnInputV1,
    ) -> FullModelTurnOutcomeV1:
        route_state = self.store.current_logic_route(
            world_id=turn.world_id,
            branch_id=turn.branch_id,
        )
        bound_plan: BoundPlannerTurnOutputV1 | None = None
        cognition_plan: CognitionPlanV1 | None = None
        if route_state.current_logic_route is AdultNextRoute.ORDINARY:
            bound_plan = self.ordinary.plan_ordinary_logic(turn)
            cognition_plan = _decode_cognition_plan(bound_plan)
            if cognition_plan.route_transition is None:
                return self.ordinary.start_ordinary_from_plan(turn, bound_plan)
            if bound_plan.accepted_head_sha256 != route_state.accepted_head_sha256:
                raise StateConflictError("adult handoff changed its accepted-head custody")

        context = self.adult_context_provider(turn, route_state)
        candidate_id = _adult_candidate_id(
            request_id=request_id,
            turn=turn,
            route_state=route_state,
        )
        orchestrator = self.adult_orchestrator_factory(turn, request_id, candidate_id)
        operation_controller = self.adult_operation_controller_factory()
        begin = operation_controller.recover_or_prepare(
            request_id=request_id,
            candidate_id=candidate_id,
            prepare=lambda: orchestrator.prepare(
                request_id=request_id,
                candidate_id=candidate_id,
                turn_input=turn,
                accepted_safe_projection=context.accepted_safe_projection,
                protected_adult_continuity=context.protected_adult_continuity,
                current_facts=context.current_facts,
                product_story_boundaries=context.product_story_boundaries,
                cognition_handoff_factory=(
                    None if cognition_plan is None else lambda: cognition_plan
                ),
            ),
        )
        planner_provider_operations = (
            0 if bound_plan is None else bound_plan.output.provider_operations
        )
        _bind_adult_planner_telemetry(
            operation_controller,
            begin.record.prepared,
            planner_provider_operations,
        )
        resolution = operation_controller.execute_new(
            begin,
            execute=orchestrator.execute_prepared,
        )
        outcome = resolution.record.outcome
        if outcome is None:
            raise StateConflictError("adult protected execution resolution lost its outcome")
        if isinstance(outcome, RejectedAdultRouteOperationV1):
            return RejectedAdultTurnV1(
                schema_version=RejectedAdultTurnV1.SCHEMA_VERSION,
                outcome=outcome,
                planner_provider_operations=planner_provider_operations,
            )
        return self._promote_passed_operation(
            turn=turn,
            operation_controller=operation_controller,
            outcome=outcome,
            planner_provider_operations=planner_provider_operations,
        )

    def _promote_passed_operation(
        self,
        *,
        turn: LeanSceneTurnInputV1,
        operation_controller: ProtectedAdultOperationController,
        outcome: PassedAdultRouteOperationV1,
        planner_provider_operations: int,
    ) -> AcceptedAdultTurnV1:
        prepared = outcome.prepared
        head = self.store.load_head(world_id=turn.world_id, branch_id=turn.branch_id)
        if head.accepted_head_sha256 != prepared.route_state.accepted_head_sha256:
            raise StateConflictError("accepted head changed during adult execution")
        generation = head.generation + 1
        accepted_turn_id = f"turn-{generation:04d}-{text_sha256(turn.exact_user_source)[:12]}"
        envelope = outcome.protected_execution.acceptance_envelope(
            accepted_turn_id=accepted_turn_id,
            parent_accepted_turn_id=head.accepted_turn_id,
            scene_id=turn.scene_id,
            generation=generation,
        )
        promotion = self.store.promote_adult_acceptance_envelope(envelope)
        operation_controller.store.mark_accepted(
            request_id=prepared.request_id,
            candidate_id=prepared.candidate_id,
            decision=_adult_accept_decision(
                operation_sha256=prepared.operation_sha256,
                promotion=promotion,
            ),
        )
        return AcceptedAdultTurnV1(
            schema_version=AcceptedAdultTurnV1.SCHEMA_VERSION,
            outcome=outcome,
            envelope=envelope,
            promotion=promotion,
            planner_provider_operations=planner_provider_operations,
        )


def _decode_cognition_plan(bound: BoundPlannerTurnOutputV1) -> CognitionPlanV1:
    payload = bound.output.decision_bundle
    if payload is None:
        raise ContractValidationError("full-model route requires a cognition decision bundle")
    try:
        return cast(CognitionPlanV1, from_mapping(CognitionPlanV1, payload))
    except (ContractValidationError, TypeError, ValueError) as exc:
        raise ContractValidationError("full-model cognition bundle is invalid") from exc


def _adult_candidate_id(
    *,
    request_id: str,
    turn: LeanSceneTurnInputV1,
    route_state: AdultRouteStateSnapshotV1,
) -> str:
    if not request_id.strip():
        raise ContractValidationError("full-model request identity is empty")
    digest = domain_sha256(
        "cera.pi_scene.full_model_adult_candidate.v1",
        {
            "request_id": request_id,
            "world_id": turn.world_id,
            "branch_id": turn.branch_id,
            "scene_id": turn.scene_id,
            "source_sha256": text_sha256(turn.exact_user_source),
            "controls_sha256": canonical_sha256(turn.request_controls),
            "accepted_head_sha256": route_state.accepted_head_sha256,
            "route_state_sha256": canonical_sha256(route_state),
        },
    )
    if not re_is_sha256(digest):
        raise ContractValidationError("full-model adult candidate hash is invalid")
    return f"candidate:adult:{digest[:32]}"


def _adult_accept_decision(
    *,
    operation_sha256: str,
    promotion: BoundAdultPromotionV1,
) -> AdultOperationDecisionV1:
    accepted_binding_sha256 = canonical_sha256(promotion)
    decision_request_sha256 = domain_sha256(
        "cera.pi_scene.full_model_adult_accept_decision.v1",
        {
            "operation_sha256": operation_sha256,
            "action": AdultOperationDecisionAction.ACCEPT.value,
            "accepted_binding_sha256": accepted_binding_sha256,
        },
    )
    return AdultOperationDecisionV1(
        schema_version=AdultOperationDecisionV1.SCHEMA_VERSION,
        action=AdultOperationDecisionAction.ACCEPT,
        decision_id=f"adult_decision:{decision_request_sha256[:32]}",
        decision_request_sha256=decision_request_sha256,
        accepted_binding_sha256=accepted_binding_sha256,
    )


_PLANNER_TELEMETRY_SCHEMA = "cera.pi_scene.adult_planner_telemetry.v1"


def _validate_adult_planner_operations(
    outcome: PassedAdultRouteOperationV1 | RejectedAdultRouteOperationV1,
    provider_operations: int,
) -> None:
    if type(provider_operations) is not int or provider_operations < 0:
        raise ContractValidationError("adult Planner operation count is invalid")
    if outcome.prepared.bypassed_codex:
        if provider_operations != 0:
            raise ContractValidationError(
                "accepted adult continuation cannot report Codex Planner work"
            )
    elif provider_operations < 1:
        raise ContractValidationError("Codex adult handoff lost its Planner operation count")


def _planner_telemetry_path(
    operation_controller: ProtectedAdultOperationController,
    prepared: PreparedAdultRouteOperationV1,
) -> Path:
    store_root = cast(Path, operation_controller.store.root)
    root = (store_root / "PLANNER_TELEMETRY").resolve()
    if not root.is_relative_to(store_root):
        raise ContractValidationError("adult Planner telemetry escaped protected custody")
    root.mkdir(parents=True, exist_ok=True)
    return root / f"op-{prepared.operation_sha256}.json"


def _adult_planner_telemetry_payload(
    prepared: PreparedAdultRouteOperationV1,
    provider_operations: int,
) -> dict[str, object]:
    if type(provider_operations) is not int or provider_operations < 0:
        raise ContractValidationError("adult Planner operation count is invalid")
    if prepared.bypassed_codex != (provider_operations == 0):
        raise ContractValidationError("adult Planner telemetry changed logic-owner custody")
    body: dict[str, object] = {
        "schema_version": _PLANNER_TELEMETRY_SCHEMA,
        "request_id": prepared.request_id,
        "candidate_id": prepared.candidate_id,
        "operation_id": prepared.operation_id,
        "operation_sha256": prepared.operation_sha256,
        "provider_operations": provider_operations,
    }
    return {**body, "binding_sha256": canonical_sha256(body)}


def _bind_adult_planner_telemetry(
    operation_controller: ProtectedAdultOperationController,
    prepared: PreparedAdultRouteOperationV1,
    provider_operations: int,
) -> None:
    path = _planner_telemetry_path(operation_controller, prepared)
    payload = _adult_planner_telemetry_payload(prepared, provider_operations)
    rendered = canonical_json(payload) + "\n"
    try:
        with path.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(rendered)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError:
        if path.read_text(encoding="utf-8") != rendered:
            raise StateConflictError("adult Planner telemetry changed on replay") from None


def _load_adult_planner_telemetry(
    operation_controller: ProtectedAdultOperationController,
    prepared: PreparedAdultRouteOperationV1,
) -> int:
    path = _planner_telemetry_path(operation_controller, prepared)
    if not path.exists():
        if prepared.bypassed_codex:
            return 0
        raise StateConflictError(
            "Codex adult handoff lacks durable Planner operation telemetry"
        )
    try:
        text = path.read_text(encoding="utf-8")
        payload = json.loads(text)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StateConflictError("adult Planner telemetry is unreadable") from exc
    required = {
        "schema_version",
        "request_id",
        "candidate_id",
        "operation_id",
        "operation_sha256",
        "provider_operations",
        "binding_sha256",
    }
    if not isinstance(payload, dict) or set(payload) != required:
        raise StateConflictError("adult Planner telemetry shape changed")
    if text != canonical_json(payload) + "\n":
        raise StateConflictError("adult Planner telemetry is not canonical")
    body = {key: payload[key] for key in required if key != "binding_sha256"}
    if payload["binding_sha256"] != canonical_sha256(body):
        raise StateConflictError("adult Planner telemetry binding changed")
    expected = _adult_planner_telemetry_payload(
        prepared,
        payload["provider_operations"],
    )
    if payload != expected:
        raise StateConflictError("adult Planner telemetry operation custody changed")
    return cast(int, payload["provider_operations"])


def _validate_recovered_adult_preparation(
    prepared: PreparedAdultRouteOperationV1,
    *,
    turn: LeanSceneTurnInputV1,
    route_state: AdultRouteStateSnapshotV1,
) -> None:
    if prepared.route_state != route_state:
        raise StateConflictError("recovered adult operation changed accepted route custody")
    request = prepared.scene_request
    if (
        route_state.world_id != turn.world_id
        or route_state.branch_id != turn.branch_id
        or request.exact_current_source != turn.exact_user_source
    ):
        raise StateConflictError("recovered adult operation changed exact request custody")
