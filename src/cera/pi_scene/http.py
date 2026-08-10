"""Authenticated loopback HTTP surface for the isolated Pi Scene profile."""

from __future__ import annotations

import hmac
import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import RLock
from typing import Any
from urllib.parse import unquote, urlparse
from uuid import uuid4

from cera.errors import ContractValidationError, ErrorCode, StateConflictError
from cera.serialization import canonical_sha256, text_sha256, to_primitive

from .contracts import SceneRoute
from .full_model_controller import (
    AcceptedAdultTurnV1,
    FullModelSceneController,
    RejectedAdultTurnV1,
)
from .full_model_http import (
    adult_completion_payload,
    adult_journal_progress,
    adult_provisional_blocked_payload,
    adult_review_payload,
    recover_adult_journal_response,
    recover_adult_journal_response_from_binding,
)
from .http_contracts import (
    PI_SCENE_ADULT_MODEL,
    PI_SCENE_AUTO_MODEL,
    PI_SCENE_ORDINARY_MODEL,
    PI_SCENE_PROFILE,
    LeanSceneRequestControlsV1,
    PiSceneChatRequestV1,
    parse_chat_request,
)
from .ordinary_http import (
    attach_debug_path,
    ordinary_completion_payload,
    ordinary_review_payload,
)
from .readable_debug import ReadablePiSceneDebugLog
from .request_journal import (
    PiSceneRequestBindingV1,
    PiSceneRequestJournal,
    PlannerResultUnavailableError,
    RequestJournalResolutionV1,
    RequestReplayPendingError,
    TransportRetryNotFoundError,
    build_request_binding,
)
from .review_store import (
    LeanDecisionResultV1,
    LeanReviewRecordV1,
    LeanSceneTurnInputV1,
)
from .runtime import LeanPiSceneCoordinator
from .transport_retry import (
    PiScenePlannerCompletionMarkerV1,
    PiSceneProviderLedgerSnapshotV1,
    PiSceneProviderTransportFailure,
    PiSceneTransportEffectSnapshotV1,
    PiSceneZeroEffectProofV1,
    TransportFailureReceiptV1,
    build_provider_failure_evidence,
    provider_failure_detail_sha256,
)
from .transport_retry_http import (
    PiSceneManualTransportRetryError,
    TransportCompletedPlannerAbandoner,
    TransportProviderLedgerSnapshot,
    TransportRetryActiveThreadSnapshot,
    TransportRetryFreshThreadInitializer,
    TransportRetryHttpController,
    TransportRetryHttpDependencies,
    TransportRetryReinitializer,
    transport_retry_action,
    transport_retry_id,
)

ContextProvider = Callable[[SceneRoute, str, Sequence[Mapping[str, str]]], LeanSceneTurnInputV1]
RequestContextProvider = Callable[
    [SceneRoute, str, Sequence[Mapping[str, str]], LeanSceneRequestControlsV1],
    LeanSceneTurnInputV1,
]
LogicRouteResolver = Callable[[LeanSceneTurnInputV1], SceneRoute]


class PiSceneCommittedStateError(RuntimeError):
    """Reporting/delivery failed after authoritative Accept already committed."""


@dataclass(frozen=True, slots=True)
class PiSceneServerConfigV1:
    host: str
    port: int
    authorization_token: str
    approved_origins: tuple[str, ...]
    service: str = "cera-pi-scene-isolated"

    def __post_init__(self) -> None:
        if self.host not in {"127.0.0.1", "localhost"}:
            raise ContractValidationError("Pi Scene HTTP server must remain loopback-only")
        if type(self.port) is not int or not 0 <= self.port <= 65535:
            raise ContractValidationError("Pi Scene HTTP port is invalid")
        if len(self.authorization_token) < 24:
            raise ContractValidationError("Pi Scene local authorization token is too short")
        if not self.approved_origins:
            raise ContractValidationError("Pi Scene requires an explicit local origin allowlist")
        for origin in self.approved_origins:
            if not re.fullmatch(r"https?://(?:127\.0\.0\.1|localhost):\d{1,5}", origin):
                raise ContractValidationError("Pi Scene approved origin is not loopback")
        if not self.service.strip():
            raise ContractValidationError("Pi Scene service identity is empty")


class PiSceneHttpAdapter:
    def __init__(
        self,
        *,
        coordinator: LeanPiSceneCoordinator,
        session_id: str | None = None,
        context_provider: ContextProvider | None = None,
        request_context_provider: RequestContextProvider | None = None,
        readable_debug: ReadablePiSceneDebugLog | None = None,
        request_journal: PiSceneRequestJournal | None = None,
        logic_route_resolver: LogicRouteResolver | None = None,
        full_model_controller: FullModelSceneController | None = None,
        transport_retry_reinitializer: TransportRetryReinitializer | None = None,
        transport_provider_ledger_snapshot: TransportProviderLedgerSnapshot | None = None,
        transport_retry_active_thread_snapshot: (TransportRetryActiveThreadSnapshot | None) = None,
        transport_retry_fresh_thread_initializer: (
            TransportRetryFreshThreadInitializer | None
        ) = None,
        transport_completed_planner_abandoner: (TransportCompletedPlannerAbandoner | None) = None,
    ) -> None:
        legacy = request_context_provider is None
        if legacy:
            if (
                session_id is None
                or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,95}", session_id)
                or context_provider is None
            ):
                raise ContractValidationError("Pi Scene legacy HTTP session binding is invalid")
        elif session_id is not None or context_provider is not None:
            raise ContractValidationError(
                "Pi Scene dynamic session binding cannot include a static session"
            )
        self.coordinator = coordinator
        self.session_id = session_id
        self.context_provider = context_provider
        self.request_context_provider = request_context_provider
        self.readable_debug = readable_debug
        self.request_journal = request_journal
        self.logic_route_resolver = logic_route_resolver
        self.full_model_controller = full_model_controller
        self.transport_retry_reinitializer = transport_retry_reinitializer
        self.transport_provider_ledger_snapshot = transport_provider_ledger_snapshot
        self.transport_retry_active_thread_snapshot = transport_retry_active_thread_snapshot
        self.transport_retry_fresh_thread_initializer = transport_retry_fresh_thread_initializer
        self.transport_completed_planner_abandoner = transport_completed_planner_abandoner
        # One adapter owns the snapshot -> Planner call -> terminal-ledger span.
        # This binds global Sol ledger appends to one exact request and also
        # prevents two manual POSTs from dispatching the same retry.
        self._provider_request_lock = RLock()
        if self.full_model_controller is not None and self.logic_route_resolver is None:
            raise ContractValidationError(
                "full-model HTTP requires accepted logic-route resolution"
            )

    @property
    def status(self) -> dict[str, Any]:
        return {
            "mode": "pi_scene_full_model",
            "active": True,
            "profile_id": PI_SCENE_PROFILE,
            "models": [
                PI_SCENE_AUTO_MODEL,
                PI_SCENE_ORDINARY_MODEL,
                PI_SCENE_ADULT_MODEL,
            ],
            "production_model": PI_SCENE_AUTO_MODEL,
            "explicit_route_models": "compatibility_and_test_only",
            "creator_review_required": False,
            "creator_review_available_on_reject": True,
            "validator_required": True,
            "reader_required": False,
            "ted_restrictions": "warn_only",
            "writer_session": "accepted_lineage_or_fresh_rehydration",
            "python_accepted_state_authoritative": True,
            "automatic_accept_after_semantic_pass": True,
            "automatic_retry": False,
            "maximum_critical_complete_repairs": 1,
            "fallback": False,
            "session_scope": (
                "static_legacy" if self.request_context_provider is None else "per_chat_branch"
            ),
        }

    def complete(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        with self._provider_request_lock:
            request, turn, binding = self._prepare_bound_request(payload)
            journal = self._durable_request_journal()
            with journal.provider_dispatch_claim():
                dispatch_intent = journal.active_transport_dispatch_for_scope(
                    session_id=binding.session_id,
                    world_id=binding.world_id,
                    branch_id=binding.branch_id,
                )
                if dispatch_intent is not None and dispatch_intent.request_id != binding.request_id:
                    raise RequestReplayPendingError(
                        "Pi Scene has an interrupted manual provider dispatch",
                        request_id=dispatch_intent.request_id,
                    )
                current = self._prepare_bound_request(payload)
                if current != (request, turn, binding):
                    raise StateConflictError(
                        "Pi Scene request context changed before provider dispatch"
                    )
                active_retry = journal.actionable_transport_failure_for_scope(
                    session_id=binding.session_id,
                    world_id=binding.world_id,
                    branch_id=binding.branch_id,
                )
                if active_retry is not None and active_retry.request_id != binding.request_id:
                    raise RequestReplayPendingError(
                        "Pi Scene branch has an unresolved manual transport Retry",
                        request_id=active_retry.request_id,
                    )
                return self._run_bound_request(
                    payload=payload,
                    request=request,
                    turn=turn,
                    binding=binding,
                    resolution=None,
                )

    def retry_transport(self, retry_id: str) -> dict[str, Any]:
        """Run one explicit, durable provider retry with no changed request bytes."""

        with self._provider_request_lock:
            return self._retry_transport_locked(retry_id)

    def _retry_transport_locked(self, retry_id: str) -> dict[str, Any]:
        """Run one Retry while holding exclusive provider-ledger attribution."""

        request_journal = self._durable_request_journal()
        with request_journal.transport_dispatch_claim(retry_id):
            with request_journal.provider_dispatch_claim():
                return self._transport_retry_controller(request_journal).retry_claimed(retry_id)

    def transport_retry_status(self, retry_id: str) -> dict[str, Any]:
        """Read/reconcile one authenticated Retry without provider dispatch."""

        return self._transport_retry_controller().status(retry_id)

    def _transport_retry_controller(
        self,
        journal: PiSceneRequestJournal | None = None,
    ) -> TransportRetryHttpController:
        return TransportRetryHttpController(
            TransportRetryHttpDependencies(
                journal=self._durable_request_journal() if journal is None else journal,
                prepare_bound_request=self._prepare_bound_request,
                run_bound_request=self._run_bound_request,
                complete_bound_request=self._complete_bound_request,
                effect_snapshot=self._transport_effect_snapshot,
                provider_ledger_snapshot=self._provider_ledger_snapshot,
                reinitializer=self.transport_retry_reinitializer,
                active_thread_snapshot=self.transport_retry_active_thread_snapshot,
                fresh_thread_initializer=(self.transport_retry_fresh_thread_initializer),
                completed_planner_abandoner=self.transport_completed_planner_abandoner,
                recover_completed_planner_progress=(self._recover_completed_planner_progress),
                recover_progressed_request=self._recover_progressed_request,
            )
        )

    def _provider_ledger_snapshot(self) -> PiSceneProviderLedgerSnapshotV1:
        snapshot = self._optional_provider_ledger_snapshot()
        if snapshot is None:
            raise StateConflictError("Pi Scene Planner retry lacks Sol ledger custody")
        return snapshot

    def _optional_provider_ledger_snapshot(
        self,
    ) -> PiSceneProviderLedgerSnapshotV1 | None:
        provider = self.transport_provider_ledger_snapshot
        if provider is None:
            return None
        value = provider()
        if not isinstance(value, PiSceneProviderLedgerSnapshotV1):
            raise StateConflictError("Pi Scene Sol ledger snapshot changed shape")
        return value

    def _prepare_bound_request(
        self,
        payload: Mapping[str, Any],
    ) -> tuple[PiSceneChatRequestV1, LeanSceneTurnInputV1, PiSceneRequestBindingV1]:
        request = self._parse_chat_request(payload)
        turn = self._turn_for_request(request)
        if request.automatic_route and self.logic_route_resolver is not None:
            resolved_route = self.logic_route_resolver(turn)
            if type(resolved_route) is not SceneRoute:
                raise StateConflictError("Pi Scene logic-route resolver returned invalid state")
            if resolved_route is not request.route:
                request = replace(request, route=resolved_route)
                rebuilt = self._turn_for_request(request)
                if (rebuilt.world_id, rebuilt.branch_id) != (
                    turn.world_id,
                    turn.branch_id,
                ):
                    raise StateConflictError(
                        "Pi Scene automatic route changed world or branch scope"
                    )
                turn = rebuilt
        binding = build_request_binding(
            payload=payload,
            session_id=request.controls.session_id,
            world_id=turn.world_id,
            branch_id=turn.branch_id,
            route=request.route,
            controls=request.controls,
        )
        return request, turn, binding

    def _run_bound_request(
        self,
        *,
        payload: Mapping[str, Any],
        request: PiSceneChatRequestV1,
        turn: LeanSceneTurnInputV1,
        binding: PiSceneRequestBindingV1,
        resolution: RequestJournalResolutionV1 | None,
        provider_ledger_before: PiSceneProviderLedgerSnapshotV1 | None = None,
    ) -> dict[str, Any]:
        before = self._transport_effect_snapshot(turn)
        ledger_before = (
            self._optional_provider_ledger_snapshot()
            if provider_ledger_before is None
            else provider_ledger_before
        )
        try:
            return self._complete_bound_request(
                payload=payload,
                request=request,
                turn=turn,
                binding=binding,
                resolution=resolution,
            )
        except PiSceneProviderTransportFailure as exc:
            if (
                exc.logic_owner != "planner"
                or exc.failure.code
                not in {
                    ErrorCode.REASONER_UNAVAILABLE,
                    ErrorCode.COMPOSER_UNAVAILABLE,
                    ErrorCode.ADULT_PLANNER_UNAVAILABLE,
                }
                or ledger_before is None
            ):
                journal = self._durable_request_journal()
                staged = journal.staged_transport_dispatch(binding)
                if staged is not None and staged["phase"] == "planner_completed_pending_progress":
                    try:
                        recovered = self._transport_retry_controller(
                            journal
                        ).recover_staged_planner_failure(
                            binding=binding,
                            payload=payload,
                            request=request,
                            turn=turn,
                        )
                    except PlannerResultUnavailableError:
                        raise exc from None
                    if isinstance(recovered, dict):
                        return recovered
                journal.discard_staged_transport_dispatch(binding)
                raise
            effect_before = exc.effect_snapshot_before or before
            after = self._transport_effect_snapshot(turn)
            if effect_before != after:
                self._durable_request_journal().discard_staged_transport_dispatch(binding)
                raise
            ledger_after = self._provider_ledger_snapshot()
            submitted = exc.failure.external_provider_calls_observed == 1
            provider_evidence = build_provider_failure_evidence(
                before=ledger_before,
                after=ledger_after,
                provider_operation_submitted=submitted,
                provider_failure_detail_sha256=provider_failure_detail_sha256(exc.failure),
            )
            proof = PiSceneZeroEffectProofV1(
                schema_version=PiSceneZeroEffectProofV1.SCHEMA_VERSION,
                request_id=binding.request_id,
                logic_owner=exc.logic_owner,
                resolved_route=request.route.value,
                turn_context_sha256=canonical_sha256(turn),
                before_snapshot=effect_before,
                after_snapshot=after,
                provider_failure_evidence=provider_evidence,
                candidate_effect_absent=True,
                review_effect_absent=True,
                accepted_effect_absent=True,
                recording_effect_absent=True,
                branch_head_effect_absent=True,
            )
            receipt = self._durable_request_journal().record_transport_failure(
                binding,
                normalized_request=payload,
                logic_owner=exc.logic_owner,
                provider_error_code=exc.failure.code.value,
                provider_operations_observed=int(submitted),
                effect_proof=proof,
                provider_ledger_before=ledger_before,
            )
            raise PiSceneManualTransportRetryError(receipt) from exc
        except Exception as original:
            journal = self._durable_request_journal()
            staged = journal.staged_transport_dispatch(binding)
            if staged is None or staged["phase"] != "planner_completed_pending_progress":
                journal.discard_staged_transport_dispatch(binding)
                raise
            try:
                recovered = self._transport_retry_controller(
                    journal
                ).recover_staged_planner_failure(
                    binding=binding,
                    payload=payload,
                    request=request,
                    turn=turn,
                )
            except PlannerResultUnavailableError:
                raise original from None
            if isinstance(recovered, dict):
                return recovered
            journal.discard_staged_transport_dispatch(binding)
            raise

    def _complete_bound_request(
        self,
        *,
        payload: Mapping[str, Any],
        request: PiSceneChatRequestV1,
        turn: LeanSceneTurnInputV1,
        binding: PiSceneRequestBindingV1,
        resolution: RequestJournalResolutionV1 | None,
    ) -> dict[str, Any]:
        request_journal = self._durable_request_journal()
        if resolution is None:
            try:
                resolution = request_journal.begin(binding)
            except RequestReplayPendingError:
                staged_recovery = self._transport_retry_controller(
                    request_journal
                ).recover_staged_planner_failure(
                    binding=binding,
                    payload=payload,
                    request=request,
                    turn=turn,
                )
                if isinstance(staged_recovery, TransportFailureReceiptV1):
                    raise PiSceneManualTransportRetryError(staged_recovery) from None
                if isinstance(staged_recovery, RequestJournalResolutionV1):
                    resolution = staged_recovery
                elif isinstance(staged_recovery, dict):
                    return staged_recovery
                else:
                    failed = request_journal.active_transport_failure(binding)
                    if failed is not None:
                        raise PiSceneManualTransportRetryError(failed) from None
                    if self.full_model_controller is None:
                        raise
                    recovered = self.full_model_controller.recover_completed_adult_operation(
                        request_id=binding.request_id,
                        turn=turn,
                    )
                    if recovered is None:
                        raise
                    progress = adult_journal_progress(
                        recovered,
                        controls_sha256=binding.controls_sha256,
                    )
                    try:
                        request_journal.bind_progress(binding, progress)
                        response = self._adult_completion_response_with_debug(recovered)
                        response = request_journal.complete(binding, response)
                    except Exception as exc:
                        raise PiSceneCommittedStateError(
                            "CERA recovered accepted adult story state but could not "
                            "terminalize its durable response"
                        ) from exc
                    return response
        if resolution.replayed:
            if resolution.terminal_response is None:
                raise StateConflictError("Pi Scene terminal replay omitted its response")
            return dict(resolution.terminal_response)
        if resolution.review_progress is not None:
            if resolution.review_progress.get("schema_version") == (
                "cera.pi_scene.http_adult_progress.v1"
            ):
                controller = self.full_model_controller
                if controller is None:
                    raise StateConflictError("adult request replay lacks its full-model controller")
                response = recover_adult_journal_response(
                    controller=controller,
                    request=request,
                    turn=turn,
                    binding_request_id=binding.request_id,
                    progress=resolution.review_progress,
                )
                response = request_journal.complete(binding, response)
                return response
            review = self._recover_journal_review(
                request=request,
                turn=turn,
                progress=resolution.review_progress,
            )
            response = self._completion_response_with_debug(review)
            response = request_journal.complete(binding, response)
            return response
        if (
            request.controls.regeneration_key is not None
            and self.full_model_controller is not None
            and request.automatic_route
            and request.route is SceneRoute.ADULT
        ):
            adult_regeneration_outcome = self.full_model_controller.regenerate_accepted_adult(
                action_request_id=binding.request_id,
                world_id=turn.world_id,
                branch_id=turn.branch_id,
            )
            return self._finish_adult_outcome(
                request_journal=request_journal,
                binding=binding,
                outcome=adult_regeneration_outcome,
            )
        if request.controls.regeneration_key is not None:
            review = self._regenerate_from_chat_request(request, turn)
        else:
            current = self.coordinator.unresolved_review(
                world_id=turn.world_id,
                branch_id=turn.branch_id,
            )
            if current is not None and (
                current.turn_input.exact_user_source == turn.exact_user_source
                and current.turn_input.request_controls == turn.request_controls
            ):
                # Safe transport replay of the same still-provisional request.
                review = current
            else:
                if current is not None:
                    self.coordinator.accept_unresolved_for_new_turn(
                        world_id=turn.world_id,
                        branch_id=turn.branch_id,
                    )
                provider_effect_before = self._transport_effect_snapshot(turn)

                def stage_planner_transport() -> None:
                    provider_ledger_at_dispatch = self._optional_provider_ledger_snapshot()
                    if provider_ledger_at_dispatch is not None:
                        thread_snapshot = self.transport_retry_active_thread_snapshot
                        planner_thread_sha256 = (
                            None
                            if thread_snapshot is None
                            else thread_snapshot(binding, turn, "planner")
                        )
                        request_journal.stage_transport_dispatch(
                            binding,
                            normalized_request=payload,
                            logic_owner="planner",
                            resolved_route=request.route.value,
                            turn_context_sha256=canonical_sha256(turn),
                            effect_before=provider_effect_before,
                            provider_ledger_before=provider_ledger_at_dispatch,
                            planner_thread_sha256=planner_thread_sha256,
                        )

                def close_planner_transport() -> None:
                    self._transport_retry_controller(request_journal).close_staged_planner_dispatch(
                        binding=binding,
                        turn=turn,
                    )

                with self.coordinator.planner_transport_custody(
                    on_start=stage_planner_transport,
                    on_success=close_planner_transport,
                ):
                    try:
                        if self.full_model_controller is not None and request.automatic_route:
                            full_model_outcome = self.full_model_controller.complete(
                                request_id=binding.request_id,
                                turn=turn,
                            )
                            if isinstance(
                                full_model_outcome,
                                (AcceptedAdultTurnV1, RejectedAdultTurnV1),
                            ):
                                return self._finish_adult_outcome(
                                    request_journal=request_journal,
                                    binding=binding,
                                    outcome=full_model_outcome,
                                )
                            review = full_model_outcome
                        else:
                            review = (
                                self.coordinator.start_ordinary(turn)
                                if request.route is SceneRoute.ORDINARY
                                else self.coordinator.start_adult(turn)
                            )
                    except PiSceneProviderTransportFailure as exc:
                        exc.effect_snapshot_before = provider_effect_before
                        raise
        try:
            request_journal.bind_review(binding, self._journal_review_progress(review))
        except Exception as exc:
            if review.accepted_receipt is not None:
                raise PiSceneCommittedStateError(
                    "Pi Scene accepted story state but could not bind its durable review"
                ) from exc
            raise
        response = self._completion_response_with_debug(review)
        try:
            response = request_journal.complete(binding, response)
        except Exception as exc:
            if review.accepted_receipt is not None:
                raise PiSceneCommittedStateError(
                    "Pi Scene accepted story state but could not terminalize "
                    "its request replay journal"
                ) from exc
            raise
        return response

    def _recover_completed_planner_progress(
        self,
        binding: PiSceneRequestBindingV1,
        request: PiSceneChatRequestV1,
        turn: LeanSceneTurnInputV1,
        completion: PiScenePlannerCompletionMarkerV1,
    ) -> dict[str, Any] | None:
        """Bind an exact durable post-Plan result without another provider call."""

        journal = self._durable_request_journal()
        current = self.coordinator.durable_result_for_request(
            world_id=binding.world_id,
            branch_id=binding.branch_id,
            turn_context_sha256=completion.turn_context_sha256,
            exact_user_source=request.exact_user_source,
            request_controls=request.controls,
        )
        if current is not None:
            journal.bind_review(binding, self._journal_review_progress(current))
            return journal.complete(binding, self._completion_response_with_debug(current))
        controller = self.full_model_controller
        if controller is None:
            return None
        recovered = controller.recover_completed_adult_operation(
            request_id=binding.request_id,
            turn=turn,
        )
        if recovered is None:
            return None
        progress = adult_journal_progress(
            recovered,
            controls_sha256=binding.controls_sha256,
        )
        journal.bind_progress(binding, progress)
        return journal.complete(
            binding,
            self._adult_completion_response_with_debug(recovered),
        )

    def _recover_progressed_request(
        self,
        binding: PiSceneRequestBindingV1,
        progress: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Terminalize exact durable progress after raw Retry bytes were redacted."""

        journal = self._durable_request_journal()
        if progress.get("schema_version") == "cera.pi_scene.http_adult_progress.v1":
            controller = self.full_model_controller
            if controller is None:
                raise StateConflictError("adult progressed Retry lacks its controller")
            response = recover_adult_journal_response_from_binding(
                controller=controller,
                binding=binding,
                progress=progress,
            )
            return journal.complete(binding, response)
        review_id = progress.get("review_id")
        if not isinstance(review_id, str):
            raise StateConflictError("ordinary progressed Retry lost its review identity")
        review = self.coordinator.get_review(review_id)
        if (
            self._journal_review_progress(review) != dict(progress)
            or review.candidate.world_id != binding.world_id
            or review.candidate.branch_id != binding.branch_id
            or canonical_sha256(to_primitive(review.turn_input.request_controls))
            != binding.controls_sha256
        ):
            raise StateConflictError("ordinary progressed Retry changed durable custody")
        return journal.complete(binding, self._completion_response_with_debug(review))

    def _finish_adult_outcome(
        self,
        *,
        request_journal: PiSceneRequestJournal,
        binding: PiSceneRequestBindingV1,
        outcome: AcceptedAdultTurnV1 | RejectedAdultTurnV1,
    ) -> dict[str, Any]:
        progress = adult_journal_progress(
            outcome,
            controls_sha256=binding.controls_sha256,
        )
        try:
            request_journal.bind_progress(binding, progress)
        except Exception as exc:
            if isinstance(outcome, AcceptedAdultTurnV1):
                raise PiSceneCommittedStateError(
                    "CERA accepted the adult story state but could not bind its "
                    "durable request progress"
                ) from exc
            raise
        response = self._adult_completion_response_with_debug(outcome)
        try:
            response = request_journal.complete(binding, response)
        except Exception as exc:
            if isinstance(outcome, AcceptedAdultTurnV1):
                raise PiSceneCommittedStateError(
                    "CERA accepted the adult story state but could not terminalize "
                    "its request replay journal"
                ) from exc
            raise
        return response

    def _completion_response_with_debug(
        self,
        review: LeanReviewRecordV1,
    ) -> dict[str, Any]:
        response = self._completion_payload(
            review,
            provider_attempts=self.coordinator.provider_operation_attempts(review),
        )
        if self.readable_debug is not None:
            try:
                debug_entry = self.readable_debug.write(
                    stage="creator-review-ready",
                    identity=review.review_id,
                    protected=review.candidate.route is SceneRoute.ADULT,
                    sections={
                        "Exact user input": review.turn_input.exact_user_source,
                        "Review state": self.review_payload(review),
                        "Visible provisional prose": review.candidate.story_text,
                    },
                )
                if debug_entry is not None:
                    attach_debug_path(response, str(debug_entry))
            except Exception:
                response["cera"]["operational_warnings"] = ["readable_debug_write_failed"]
        return response

    def _adult_completion_response_with_debug(
        self,
        outcome: AcceptedAdultTurnV1 | RejectedAdultTurnV1,
    ) -> dict[str, Any]:
        response = adult_completion_payload(outcome)
        if self.readable_debug is not None:
            try:
                debug_entry = self.readable_debug.write(
                    stage="adult-scene-filter-result",
                    identity=outcome.outcome.prepared.operation_id,
                    protected=True,
                    sections={
                        "Exact user input": (
                            outcome.outcome.prepared.scene_request.exact_current_source
                        ),
                        "Protected adult result": outcome,
                        "Visible story prose": outcome.exact_story_prose,
                    },
                )
                if debug_entry is not None:
                    attach_debug_path(response, str(debug_entry))
            except Exception:
                response["cera"]["operational_warnings"] = ["readable_debug_write_failed"]
        return response

    def get_review(self, review_id: str) -> dict[str, Any]:
        adult = (
            self.full_model_controller is not None
            and self.full_model_controller.has_adult_review(review_id)
        )
        ordinary = self._ordinary_review_optional(review_id)
        if adult and ordinary is not None:
            raise StateConflictError("CERA public review identity is ambiguous")
        if adult:
            assert self.full_model_controller is not None
            return adult_review_payload(self.full_model_controller.get_adult_review(review_id))
        if ordinary is None:
            raise StateConflictError("unknown Pi Scene review")
        return self.review_payload(ordinary)

    def decide(self, review_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        session_id, world_id, branch_id = self._review_dispatch_scope(review_id)
        with self._provider_request_lock:
            journal = self._durable_request_journal()
            with journal.provider_dispatch_claim():
                dispatch_intent = journal.active_transport_dispatch_for_scope(
                    session_id=session_id,
                    world_id=world_id,
                    branch_id=branch_id,
                )
                if dispatch_intent is not None:
                    raise RequestReplayPendingError(
                        "Pi Scene has an interrupted manual provider dispatch",
                        request_id=dispatch_intent.request_id,
                    )
                return self._decide_locked(review_id, payload)

    def _review_dispatch_scope(
        self,
        review_id: str,
    ) -> tuple[str | None, str, str]:
        adult = (
            self.full_model_controller is not None
            and self.full_model_controller.has_adult_review(review_id)
        )
        ordinary = self._ordinary_review_optional(review_id)
        if adult and ordinary is not None:
            raise StateConflictError("CERA public review identity is ambiguous")
        if ordinary is not None:
            controls = ordinary.turn_input.request_controls
            return (
                None if controls is None else controls.session_id,
                ordinary.turn_input.world_id,
                ordinary.turn_input.branch_id,
            )
        if adult:
            assert self.full_model_controller is not None
            bound = self.full_model_controller.get_adult_review(review_id)
            route_state = bound.outcome.prepared.route_state
            return None, route_state.world_id, route_state.branch_id
        raise StateConflictError("unknown Pi Scene review")

    def _decide_locked(
        self,
        review_id: str,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        adult = (
            self.full_model_controller is not None
            and self.full_model_controller.has_adult_review(review_id)
        )
        ordinary = self._ordinary_review_optional(review_id)
        if adult and ordinary is not None:
            raise StateConflictError("CERA public review identity is ambiguous")
        if adult:
            return self._decide_adult_review(review_id, payload)
        action = str(payload.get("action", ""))
        feedback = payload.get("feedback")
        if feedback is not None and not isinstance(feedback, str):
            raise ContractValidationError("creator feedback must be text")
        force_rehydrate = payload.get("force_rehydrate", False)
        if type(force_rehydrate) is not bool:
            raise ContractValidationError("force_rehydrate must be boolean")
        decision: LeanDecisionResultV1 | None = None
        try:
            if action == "accept":
                decision = self.coordinator.accept(review_id, allow_replay=True)
            elif action == "accept_provisional":
                decision = self.coordinator.accept(
                    review_id,
                    allow_replay=True,
                    acceptance_action="provisional_accept",
                )
            elif action == "decline":
                decision = self.coordinator.decline(review_id, allow_replay=True)
            elif action == "regenerate":
                decision = self.coordinator.regenerate(
                    review_id,
                    feedback=None if feedback == "" else feedback,
                    force_rehydrate=force_rehydrate,
                    allow_replay=True,
                )
            elif action == "replan":
                decision = self.coordinator.replan(
                    review_id,
                    feedback=feedback,
                    allow_replay=True,
                )
            elif action == "repair_recording":
                decision = self.coordinator.repair_recording(review_id)
            else:
                raise ContractValidationError("unknown Pi Scene creator action")
            result = self.decision_payload(action, decision)
        except Exception as exc:
            if decision is not None and decision.review.accepted_receipt is not None:
                raise PiSceneCommittedStateError(
                    "Pi Scene accepted story state but could not render its decision response"
                ) from exc
            raise
        if self.readable_debug is not None:
            try:
                debug_entry = self.readable_debug.write(
                    stage="creator-decision",
                    identity=review_id,
                    protected=decision.review.candidate.route is SceneRoute.ADULT,
                    sections={
                        "Creator action": action,
                        "Creator feedback": feedback,
                        "Persisted review state": result,
                    },
                )
                if debug_entry is not None:
                    result["debug_log_path"] = str(debug_entry)
            except Exception:
                result.setdefault("operational_warnings", []).append("readable_debug_write_failed")
        return result

    def _decide_adult_review(
        self,
        review_id: str,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        controller = self.full_model_controller
        if controller is None:
            raise StateConflictError("adult creator review controller is unavailable")
        action = str(payload.get("action", ""))
        feedback = payload.get("feedback")
        if feedback is not None and not isinstance(feedback, str):
            raise ContractValidationError("creator feedback must be text")
        if feedback not in (None, ""):
            raise ContractValidationError("adult Regenerate uses no rejected-candidate feedback")
        force_rehydrate = payload.get("force_rehydrate", False)
        if type(force_rehydrate) is not bool:
            raise ContractValidationError("force_rehydrate must be boolean")
        if force_rehydrate:
            raise ContractValidationError("adult Regenerate always uses fresh frozen rehydration")
        if action == "decline":
            review = adult_review_payload(controller.decline_adult_review(review_id))
            return {
                "schema_version": "cera.pi_scene.review_decision.v1",
                "status": "review_transitioned",
                "creator_action": "decline",
                "story_state_committed": False,
                "retry_mode": "not_applicable",
                "review": review,
                "successor": None,
                "operational_warnings": [],
            }
        if action == "regenerate":
            outcome = controller.regenerate_adult_review(review_id)
            successor = self._adult_completion_response_with_debug(outcome)
            committed = isinstance(outcome, AcceptedAdultTurnV1)
            result: dict[str, Any] = {
                "schema_version": "cera.pi_scene.review_decision.v1",
                "status": "story_committed" if committed else "review_transitioned",
                "creator_action": "regenerate",
                "story_state_committed": committed,
                "retry_mode": "not_applicable",
                "review": adult_review_payload(controller.get_adult_review(review_id)),
                "successor": successor,
                "operational_warnings": [],
            }
            if isinstance(outcome, AcceptedAdultTurnV1):
                result.update(
                    {
                        "accepted_turn_id": outcome.envelope.accepted_turn_id,
                        "accepted_receipt_sha256": (
                            outcome.promotion.receipt.accepted_head_after_sha256
                        ),
                    }
                )
            return result
        if action == "accept_provisional":
            blocked = controller.provisional_adult_review(review_id)
            return adult_provisional_blocked_payload(
                public_review_id=review_id,
                blocked=blocked,
            )
        if action == "accept":
            raise ContractValidationError(
                "rejected adult candidate cannot be accepted without reprojection"
            )
        raise ContractValidationError("unknown adult creator-review action")

    def _ordinary_review_optional(self, review_id: str) -> LeanReviewRecordV1 | None:
        try:
            return self.coordinator.get_review(review_id)
        except StateConflictError as exc:
            if exc.args == ("unknown Pi Scene review",):
                return None
            raise

    def review_payload(self, review: LeanReviewRecordV1) -> dict[str, Any]:
        status = (
            None
            if review.accepted_receipt is None
            else self.coordinator.store.recording_status(review.accepted_receipt).value
        )
        return ordinary_review_payload(review, recording_status=status)

    @staticmethod
    def _completion_payload(
        review: LeanReviewRecordV1,
        *,
        provider_attempts: Sequence[LeanReviewRecordV1] | None = None,
    ) -> dict[str, Any]:
        """Compatibility wrapper over the pure ordinary HTTP projection."""

        return ordinary_completion_payload(
            review,
            provider_attempts=provider_attempts,
        )

    def decision_payload(
        self,
        action: str,
        decision: LeanDecisionResultV1,
    ) -> dict[str, Any]:
        body = {
            "schema_version": "cera.pi_scene.review_decision.v1",
            "status": (
                "story_committed"
                if decision.review.accepted_receipt is not None
                else "review_transitioned"
            ),
            "creator_action": action,
            "story_state_committed": decision.review.accepted_receipt is not None,
            "retry_mode": "not_applicable",
            "review": self.review_payload(decision.review),
            "successor": (
                None
                if decision.successor is None
                else self._completion_payload(
                    decision.successor,
                    provider_attempts=self.coordinator.provider_operation_attempts(
                        decision.successor
                    ),
                )
            ),
            "operational_warnings": list(decision.operational_warnings),
        }
        if decision.review.accepted_receipt is not None:
            body["accepted_receipt_sha256"] = decision.review.accepted_receipt.receipt_sha256
            body["accepted_turn_id"] = decision.review.accepted_receipt.accepted_turn_id
        return body

    def _parse_chat_request(
        self,
        payload: Mapping[str, Any],
    ) -> PiSceneChatRequestV1:
        return parse_chat_request(
            payload,
            expected_session_id=self.session_id,
        )

    def _durable_request_journal(self) -> PiSceneRequestJournal:
        if self.request_journal is not None:
            return self.request_journal
        store = getattr(self.coordinator, "store", None)
        root = getattr(store, "root", None)
        if not isinstance(root, Path):
            raise StateConflictError("Pi Scene durable request-journal root is unavailable")
        self.request_journal = PiSceneRequestJournal(
            root / "http_request_journal",
            protected_retry_root=root.parent / "protected_transport_retry",
        )
        return self.request_journal

    def _transport_effect_snapshot(
        self,
        turn: LeanSceneTurnInputV1,
    ) -> PiSceneTransportEffectSnapshotV1:
        head = self.coordinator.store.load_head(
            world_id=turn.world_id,
            branch_id=turn.branch_id,
        )
        review = self.coordinator.unresolved_review(
            world_id=turn.world_id,
            branch_id=turn.branch_id,
        )
        recording_attempt = (
            None
            if head.receipt is None or head.receipt.route is SceneRoute.ADULT
            else self.coordinator.store.load_recording_attempt(head.receipt)
        )
        return PiSceneTransportEffectSnapshotV1(
            schema_version=PiSceneTransportEffectSnapshotV1.SCHEMA_VERSION,
            world_id=turn.world_id,
            branch_id=turn.branch_id,
            accepted_turn_id=head.accepted_turn_id,
            accepted_receipt_sha256=head.accepted_head_sha256,
            accepted_head_sha256=canonical_sha256(to_primitive(head)),
            unresolved_review_sha256=(
                None
                if review is None
                else canonical_sha256(
                    {
                        "review_id": review.review_id,
                        "state": review.state,
                        "candidate_sha256": review.candidate.candidate_sha256,
                        "accepted_receipt_sha256": (
                            None
                            if review.accepted_receipt is None
                            else review.accepted_receipt.receipt_sha256
                        ),
                        "recording_attempt_sha256": (
                            None
                            if review.recording_attempt is None
                            else canonical_sha256(to_primitive(review.recording_attempt))
                        ),
                    }
                )
            ),
            recording_attempt_sha256=(
                None
                if recording_attempt is None
                else canonical_sha256(to_primitive(recording_attempt))
            ),
        )

    @staticmethod
    def _journal_review_progress(review: LeanReviewRecordV1) -> dict[str, Any]:
        receipt = review.accepted_receipt
        recording_status = (
            None if review.recording_attempt is None else review.recording_attempt.status.value
        )
        return {
            "schema_version": "cera.pi_scene.http_review_progress.v1",
            "review_id": review.review_id,
            "candidate_id": review.candidate.candidate_id,
            "candidate_sha256": review.candidate.candidate_sha256,
            "world_id": review.candidate.world_id,
            "branch_id": review.candidate.branch_id,
            "route": review.candidate.route.value,
            "exact_user_source_sha256": text_sha256(review.turn_input.exact_user_source),
            "controls_sha256": canonical_sha256(to_primitive(review.turn_input.request_controls)),
            "review_state": review.state,
            "accepted_turn_id": None if receipt is None else receipt.accepted_turn_id,
            "accepted_receipt_sha256": None if receipt is None else receipt.receipt_sha256,
            "recording_status": recording_status,
        }

    def _recover_journal_review(
        self,
        *,
        request: PiSceneChatRequestV1,
        turn: LeanSceneTurnInputV1,
        progress: Mapping[str, Any],
    ) -> LeanReviewRecordV1:
        review_id = progress.get("review_id")
        if not isinstance(review_id, str):
            raise StateConflictError("Pi Scene replay review identity is invalid")
        review = self.coordinator.get_review(review_id)
        if (
            review.turn_input.exact_user_source != request.exact_user_source
            or review.turn_input.request_controls != request.controls
            or review.candidate.world_id != turn.world_id
            or review.candidate.branch_id != turn.branch_id
            or review.candidate.route is not request.route
            or self._journal_review_progress(review) != dict(progress)
        ):
            raise StateConflictError(
                "Pi Scene durable review no longer matches pending request custody"
            )
        return review

    def _turn_for_request(self, request: PiSceneChatRequestV1) -> LeanSceneTurnInputV1:
        if self.request_context_provider is None:
            if self.context_provider is None:
                raise StateConflictError("Pi Scene context provider is unavailable")
            turn = self.context_provider(
                request.route,
                request.exact_user_source,
                request.messages,
            )
        else:
            turn = self.request_context_provider(
                request.route,
                request.exact_user_source,
                request.messages,
                request.controls,
            )
        if turn.request_controls is not None and turn.request_controls != request.controls:
            raise StateConflictError("Pi Scene context provider changed request controls")
        return replace(turn, request_controls=request.controls)

    def _regenerate_from_chat_request(
        self,
        request: PiSceneChatRequestV1,
        turn: LeanSceneTurnInputV1,
    ) -> LeanReviewRecordV1:
        current = self.coordinator.unresolved_review(
            world_id=turn.world_id,
            branch_id=turn.branch_id,
        )
        if current is None:
            return self.coordinator.regenerate_accepted(turn)
        if current.candidate.route is not request.route:
            raise StateConflictError("Pi Scene regeneration cannot change route")
        prior_controls = current.turn_input.request_controls
        if (
            prior_controls == request.controls
            and current.turn_input.exact_user_source == request.exact_user_source
        ):
            return current
        if (
            prior_controls is not None
            and prior_controls.regeneration_key == request.controls.regeneration_key
        ):
            raise StateConflictError(
                "Pi Scene regeneration key was reused with different request controls"
            )
        decision = self.coordinator.regenerate(
            current.review_id,
            turn_input=turn,
        )
        if decision.successor is None:
            raise StateConflictError("Pi Scene regeneration omitted its successor")
        return decision.successor


def _typed_error_payload(
    *,
    error_code: str,
    message: str,
    story_state_committed: bool = False,
    retry_mode: str = "not_applicable",
    technical_detail: str | None = None,
    next_action: str = "check_configuration",
    debug_log_path: str | None = None,
    trace_id: str | None = None,
    request_id: str | None = None,
    provider_operation_submitted: bool = False,
    retry_transport_enabled: bool = False,
    transport_retry: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if technical_detail is not None and not technical_detail.strip():
        technical_detail = None
    envelope: dict[str, Any] = {
        "schema_version": "cera.error.v1",
        "error_code": error_code,
        "message": message,
        "trace_id": trace_id or f"trace:{uuid4().hex}",
        "request_id": request_id,
        "branch_id": None,
        "generation_id": None,
        "stage": "pi_scene_http",
        "story_state_committed": story_state_committed,
        "retry_mode": retry_mode,
        # Exact exception text can contain local paths, provider fragments, or
        # protected story material.  It belongs only in the local readable
        # debug entry named below, never in the HTTP response.
        "details": (
            []
            if technical_detail is None
            else ["Technical detail is available in the local debug log."]
        ),
        "fallback_used": False,
        "provider_operation_submitted": provider_operation_submitted,
        "accepted_state_changed": story_state_committed,
        "next_action": next_action,
        "debug_log_path": debug_log_path,
        "retry_transport_enabled": retry_transport_enabled,
    }
    if transport_retry is not None:
        envelope["transport_retry"] = dict(transport_retry)
    return {
        "status": "error",
        "story_state_committed": story_state_committed,
        "error": envelope,
    }


def build_pi_scene_server(
    adapter: PiSceneHttpAdapter,
    config: PiSceneServerConfigV1,
) -> ThreadingHTTPServer:
    token_bytes = config.authorization_token.encode("utf-8")
    approved_origins = frozenset(config.approved_origins)

    class Handler(BaseHTTPRequestHandler):
        server_version = "CERA-Pi-Scene/1"

        def do_OPTIONS(self) -> None:
            if not self._origin_allowed():
                self.send_response(HTTPStatus.FORBIDDEN.value)
                self.end_headers()
                return
            self.send_response(HTTPStatus.NO_CONTENT.value)
            self._cors_headers()
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
            self.send_header("Access-Control-Max-Age", "600")
            self.end_headers()

        def do_GET(self) -> None:
            if not self._authorized():
                self._json(
                    HTTPStatus.UNAUTHORIZED,
                    _typed_error_payload(
                        error_code="CERA_HTTP_UNAUTHORIZED",
                        message="Pi Scene local authorization is required.",
                    ),
                )
                return
            if not self._origin_allowed(optional=True):
                self._json(
                    HTTPStatus.FORBIDDEN,
                    _typed_error_payload(
                        error_code="CERA_HTTP_ORIGIN_FORBIDDEN",
                        message="Pi Scene rejected the request origin.",
                    ),
                )
                return
            path = urlparse(self.path).path
            if path in {"/health", "/v1/health"}:
                self._json(
                    HTTPStatus.OK,
                    {
                        "status": "ok",
                        "service": config.service,
                        "production": False,
                        "loopback_only": True,
                        "authorization_required": True,
                        "authorization_token_sha256": text_sha256(config.authorization_token),
                        "runtime": adapter.status,
                    },
                )
                return
            if path == "/v1/models":
                self._json(
                    HTTPStatus.OK,
                    {
                        "object": "list",
                        "data": [
                            {"id": model, "object": "model", "owned_by": "cera-local-isolated"}
                            for model in (
                                PI_SCENE_AUTO_MODEL,
                                PI_SCENE_ORDINARY_MODEL,
                                PI_SCENE_ADULT_MODEL,
                            )
                        ],
                    },
                )
                return
            retry_id = transport_retry_id(path)
            if retry_id is not None:
                self._guarded(lambda: adapter.transport_retry_status(retry_id))
                return
            if path.startswith("/v1/cera/transport-retries/"):
                self._error(
                    TransportRetryNotFoundError("Pi Scene transport retry identity is unavailable")
                )
                return
            review_id = _review_id(path)
            if review_id is not None:
                self._guarded(lambda: adapter.get_review(review_id))
                return
            self._json(
                HTTPStatus.NOT_FOUND,
                _typed_error_payload(
                    error_code="CERA_HTTP_NOT_FOUND",
                    message="The Pi Scene endpoint was not found.",
                ),
            )

        def do_POST(self) -> None:
            if not self._authorized():
                self._json(
                    HTTPStatus.UNAUTHORIZED,
                    _typed_error_payload(
                        error_code="CERA_HTTP_UNAUTHORIZED",
                        message="Pi Scene local authorization is required.",
                    ),
                )
                return
            if not self._origin_allowed(optional=True):
                self._json(
                    HTTPStatus.FORBIDDEN,
                    _typed_error_payload(
                        error_code="CERA_HTTP_ORIGIN_FORBIDDEN",
                        message="Pi Scene rejected the request origin.",
                    ),
                )
                return
            content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
            if content_type != "application/json":
                self._json(
                    HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                    _typed_error_payload(
                        error_code="CERA_INTAKE_INVALID",
                        message="Pi Scene requires an application/json request body.",
                    ),
                )
                return
            path = urlparse(self.path).path
            try:
                payload = self._read_json_body()
            except Exception as exc:
                self._error(exc)
                return
            if path == "/v1/chat/completions":
                self._guarded(lambda: adapter.complete(payload))
                return
            retry_id = transport_retry_id(path)
            if retry_id is not None:
                if payload != {}:
                    self._json(
                        HTTPStatus.UNPROCESSABLE_ENTITY,
                        _typed_error_payload(
                            error_code="CERA_INTAKE_INVALID",
                            message="Pi Scene transport Retry accepts only an empty object.",
                        ),
                    )
                    return
                self._guarded(lambda: adapter.retry_transport(retry_id))
                return
            if path.startswith("/v1/cera/transport-retries/"):
                self._error(
                    TransportRetryNotFoundError("Pi Scene transport retry identity is unavailable")
                )
                return
            review_id = _review_id(path, suffix="/decision")
            if review_id is not None:
                self._guarded(lambda: adapter.decide(review_id, payload))
                return
            self._json(
                HTTPStatus.NOT_FOUND,
                _typed_error_payload(
                    error_code="CERA_HTTP_NOT_FOUND",
                    message="The Pi Scene endpoint was not found.",
                ),
            )

        def _guarded(self, operation: Callable[[], Mapping[str, Any]]) -> None:
            try:
                self._json(HTTPStatus.OK, operation())
            except Exception as exc:
                self._error(exc)

        def _error(self, exc: Exception) -> None:
            technical_detail: str | None = f"{type(exc).__name__}: {exc}"
            transport_receipt = None
            error_request_id: str | None = None
            if isinstance(exc, PiSceneManualTransportRetryError):
                receipt = exc.receipt
                status = HTTPStatus.INTERNAL_SERVER_ERROR
                code = ErrorCode.PROVIDER_TRANSPORT_FAILED.value
                message = "A provider transport failed with no candidate or story-state effect."
                committed = False
                retry_mode = "manual_transport"
                next_action = "use_transport_retry"
                transport_receipt = receipt
                technical_detail = None
            elif isinstance(exc, PiSceneCommittedStateError):
                status = HTTPStatus.INTERNAL_SERVER_ERROR
                code = "CERA_DELIVERY_AFTER_COMMIT_FAILED"
                message = "Accepted story state was retained, but response delivery failed."
                committed = True
                retry_mode = "manual_after_review"
                next_action = "check_current_review_before_retrying"
            elif isinstance(exc, PlannerResultUnavailableError):
                status = HTTPStatus.CONFLICT
                code = ErrorCode.PLANNER_RESULT_UNAVAILABLE.value
                message = (
                    "The Planner operation completed, but its result was lost before "
                    "durable story progress. The exact request will not be redispatched."
                )
                committed = False
                retry_mode = "not_applicable"
                next_action = (
                    "start_a_new_chat_or_repair_planner_thread_custody"
                    if exc.disposition.branch_dispatch_blocked
                    else "send_a_different_prompt_or_start_a_new_chat"
                )
                error_request_id = exc.request_id
                technical_detail = None
            elif isinstance(exc, RequestReplayPendingError):
                status = HTTPStatus.CONFLICT
                code = "CERA_REQUEST_REPLAY_PENDING"
                message = (
                    "An identical request has non-terminal durable custody; "
                    "provider redispatch was blocked."
                )
                committed = False
                retry_mode = "manual_after_review"
                next_action = "recover_the_exact_pending_request_before_redispatch"
            elif isinstance(exc, TransportRetryNotFoundError):
                status = HTTPStatus.NOT_FOUND
                code = "CERA_TRANSPORT_RETRY_NOT_FOUND"
                message = "The transport Retry identity is unavailable."
                committed = False
                retry_mode = "not_applicable"
                next_action = "check_transport_retry_identity"
                technical_detail = None
            elif isinstance(exc, StateConflictError):
                status = HTTPStatus.CONFLICT
                code = "CERA_STATE_CONFLICT"
                message = "The request conflicts with the current Pi Scene review or branch state."
                committed = False
                retry_mode = "manual_after_review"
                next_action = "check_current_review_or_branch_state"
            elif isinstance(exc, ContractValidationError):
                status = HTTPStatus.UNPROCESSABLE_ENTITY
                code = "CERA_INTAKE_INVALID"
                message = "The Pi Scene request failed contract validation."
                committed = False
                retry_mode = "not_applicable"
                next_action = "correct_the_reported_request_field"
            elif isinstance(exc, (json.JSONDecodeError, UnicodeDecodeError, ValueError)):
                status = HTTPStatus.BAD_REQUEST
                code = "CERA_INTAKE_INVALID"
                message = "The Pi Scene request body is invalid."
                committed = False
                retry_mode = "not_applicable"
                next_action = "send_valid_json_without_changing_story_state"
            else:
                status = HTTPStatus.INTERNAL_SERVER_ERROR
                code = "CERA_INTERNAL_ERROR"
                message = "The Pi Scene request failed internally."
                committed = False
                retry_mode = "manual_after_review"
                next_action = "open_the_debug_log_and_report_the_trace_id"
            trace_id = f"trace:{uuid4().hex}"
            debug_log_path = None
            if adapter.readable_debug is not None:
                try:
                    debug_entry = adapter.readable_debug.write(
                        stage="http-error",
                        identity=trace_id,
                        sections={
                            "Stable error code": code,
                            "User-facing message": message,
                            "Exact local backend error": technical_detail,
                            "Accepted state changed": committed,
                            "Next action": next_action,
                        },
                    )
                    debug_log_path = None if debug_entry is None else str(debug_entry)
                except Exception:
                    debug_log_path = None
            self._json(
                status,
                _typed_error_payload(
                    error_code=code,
                    message=message,
                    story_state_committed=committed,
                    retry_mode=retry_mode,
                    technical_detail=technical_detail,
                    next_action=next_action,
                    debug_log_path=debug_log_path,
                    trace_id=trace_id,
                    request_id=(
                        error_request_id
                        if transport_receipt is None
                        else transport_receipt.request_id
                    ),
                    provider_operation_submitted=(
                        isinstance(exc, PlannerResultUnavailableError)
                        if transport_receipt is None
                        else transport_receipt.provider_operations_observed == 1
                    ),
                    retry_transport_enabled=transport_receipt is not None,
                    transport_retry=(
                        None
                        if transport_receipt is None
                        else {
                            **transport_retry_action(transport_receipt),
                        }
                    ),
                ),
            )

        def _authorized(self) -> bool:
            value = self.headers.get("Authorization", "")
            prefix = "Bearer "
            if not value.startswith(prefix):
                return False
            return hmac.compare_digest(value[len(prefix) :].encode("utf-8"), token_bytes)

        def _origin_allowed(self, *, optional: bool = False) -> bool:
            origin = self.headers.get("Origin")
            return optional and origin is None or origin in approved_origins

        def _cors_headers(self) -> None:
            origin = self.headers.get("Origin")
            if origin in approved_origins:
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Vary", "Origin")

        def _read_json_body(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 2_000_000:
                raise ContractValidationError("request body length is invalid")
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ContractValidationError("request body must be a JSON object")
            return payload

        def _json(self, status: HTTPStatus, payload: Mapping[str, Any]) -> None:
            body = (json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n").encode(
                "utf-8"
            )
            self.send_response(status.value)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self._cors_headers()
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: Any) -> None:
            del format, args

    return ThreadingHTTPServer((config.host, config.port), Handler)


def _review_id(path: str, *, suffix: str = "") -> str | None:
    prefix = "/v1/cera/reviews/"
    if not path.startswith(prefix) or not path.endswith(suffix):
        return None
    value = path[len(prefix) :]
    if suffix:
        value = value[: -len(suffix)]
    value = unquote(value)
    if not re.fullmatch(r"review-[a-f0-9]{28}", value):
        return None
    return value
