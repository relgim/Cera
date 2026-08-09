"""Provider-neutral coordinator for the lean Pi Scene route."""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from threading import RLock
from typing import Any, Protocol

from cera.errors import ContractValidationError, StateConflictError
from cera.provider_dispatch_guard import (
    assert_provider_dispatch_allowed,
    is_external_provider_boundary,
)
from cera.semantic_validation import (
    BoundSemanticValidationV1,
    SemanticValidationCustodyV1,
    SemanticValidationRequestV1,
    SemanticVerdict,
)
from cera.serialization import canonical_sha256, text_sha256, to_primitive

from .contracts import (
    LeanAcceptedTurnReceiptV1,
    LeanCandidateV1,
    LeanRecordingAttemptV1,
    LeanRunResultV1,
    RecordingStatus,
    SceneRoute,
    advisory_ted_warnings,
    canonical_authority,
    primary_item_keys,
)
from .http_contracts import LeanSceneRequestControlsV1
from .pi_adapter import PiSceneAdapter, PiSceneInvocationV1
from .review_store import (
    CreatorGuidanceV1,
    DecisionReplayV1,
    DurableReviewStateStore,
    LeanDecisionResultV1,
    LeanReviewRecordV1,
    LeanReviewState,
    LeanSceneTurnInputV1,
    decision_request_sha256,
)
from .semantic_bridge import build_semantic_validation_input
from .store import (
    LeanSceneStore,
    adult_full_record_from_mapping,
    adult_projection_from_mapping,
    ordinary_record_from_mapping,
)
from .writer_view import WriterViewInputV1, WriterViewMaterializer


@dataclass(frozen=True, slots=True)
class PlannerTurnInputV1:
    world_id: str
    branch_id: str
    scene_id: str
    exact_user_source: str
    current_state: Mapping[str, Any]
    characters: Mapping[str, Mapping[str, Any]]
    relationships: Mapping[str, Mapping[str, Any]]
    relevant_memories: Mapping[str, Mapping[str, Any]]
    accepted_records: tuple[Mapping[str, Any], ...]
    request_controls: LeanSceneRequestControlsV1 | None = None
    creator_guidance: CreatorGuidanceV1 | None = None


@dataclass(frozen=True, slots=True)
class PlannerTurnOutputV1:
    sequence: Mapping[str, Any]
    provider_operations: int
    decision_bundle: Mapping[str, Any] | None = None
    validation_evidence: tuple[Mapping[str, Any], ...] = ()

    def __post_init__(self) -> None:
        if not self.sequence:
            raise ContractValidationError("ordinary Planner returned no sequence")
        if type(self.provider_operations) is not int or self.provider_operations < 1:
            raise ContractValidationError("ordinary Planner operation count is invalid")
        if self.decision_bundle is not None:
            nested = self.decision_bundle.get("sequence")
            if not isinstance(nested, Mapping) or dict(nested) != dict(self.sequence):
                raise ContractValidationError(
                    "ordinary Planner decision bundle changed its exact sequence"
                )


class OrdinarySemanticValidatorPort(Protocol):
    def validate(
        self,
        request: SemanticValidationRequestV1,
        custody: SemanticValidationCustodyV1,
    ) -> BoundSemanticValidationV1: ...


class OrdinaryPlannerPort(Protocol):
    def plan(self, request: PlannerTurnInputV1) -> PlannerTurnOutputV1: ...


RecordingFaultInjector = Callable[[LeanAcceptedTurnReceiptV1, int], str | None]
PlannerResolver = Callable[[LeanSceneTurnInputV1], OrdinaryPlannerPort]


class LeanPiSceneCoordinator:
    """Generate, review, minimally accept, then record independently."""

    def __init__(
        self,
        *,
        store: LeanSceneStore,
        planner: OrdinaryPlannerPort,
        writer_views: WriterViewMaterializer,
        pi: PiSceneAdapter,
        session_root: Path,
        recording_fault_injector: RecordingFaultInjector | None = None,
        planner_resolver: PlannerResolver | None = None,
        semantic_validator: OrdinarySemanticValidatorPort | None = None,
    ) -> None:
        self.store = store
        self.planner = planner
        self.writer_views = writer_views
        self.pi = pi
        self.session_root = session_root.resolve()
        self.session_root.mkdir(parents=True, exist_ok=True)
        self._recording_fault_injector = recording_fault_injector
        self._planner_resolver = planner_resolver
        self.semantic_validator = semantic_validator
        self._lock = RLock()
        self._review_state_store = DurableReviewStateStore(
            root=self.session_root,
            scene_store=self.store,
        )
        recovered = self._review_state_store.load()
        self._reviews = dict(recovered.reviews)
        self._unresolved_by_branch = dict(recovered.unresolved_by_branch)
        self._decisions = dict(recovered.decisions)
        self._candidate_counter = recovered.candidate_counter

    def start_ordinary(self, turn: LeanSceneTurnInputV1) -> LeanReviewRecordV1:
        with self._lock:
            self._require_no_unresolved(turn.world_id, turn.branch_id)
            planned = self._plan_ordinary(turn, creator_guidance=None)
            review = self._prepare_review(
                turn,
                route=SceneRoute.ORDINARY,
                primary_authority=(planned.decision_bundle or planned.sequence),
                planner_provider_operations=planned.provider_operations,
            )
            review = self._validate_ordinary_review(
                review,
                validation_evidence=planned.validation_evidence,
            )
            self._register_review(review)
            if (
                review.semantic_validation is not None
                and review.semantic_validation.verdict.verdict is SemanticVerdict.PASS
            ):
                return self.accept(
                    review.review_id,
                    acceptance_action="automatic_accept",
                ).review
            if (
                review.semantic_validation is not None
                and review.semantic_validation.verdict.automatic_repair_eligible
            ):
                return self._repair_rejected_ordinary(review)
            return review

    def _plan_ordinary(
        self,
        turn: LeanSceneTurnInputV1,
        *,
        creator_guidance: CreatorGuidanceV1 | None,
    ) -> PlannerTurnOutputV1:
        accepted_records = self.store.recent_ordinary_context_payloads(
            world_id=turn.world_id,
            branch_id=turn.branch_id,
        )
        return self._planner_for(turn).plan(
            PlannerTurnInputV1(
                world_id=turn.world_id,
                branch_id=turn.branch_id,
                scene_id=turn.scene_id,
                exact_user_source=turn.exact_user_source,
                current_state=_controlled_current_state(
                    turn,
                    creator_guidance=creator_guidance,
                ),
                characters=turn.characters,
                relationships=turn.relationships,
                relevant_memories=turn.relevant_memories,
                accepted_records=accepted_records,
                request_controls=turn.request_controls,
                creator_guidance=creator_guidance,
            )
        )

    def _validate_ordinary_review(
        self,
        review: LeanReviewRecordV1,
        *,
        validation_evidence: Sequence[Mapping[str, Any]],
    ) -> LeanReviewRecordV1:
        if review.candidate.primary_authority_kind != "codex_cognition_plan":
            return review
        if self.semantic_validator is None:
            raise StateConflictError("cognition candidate requires the semantic Validator")
        validation_request, validation_custody = build_semantic_validation_input(
            candidate=review.candidate,
            current_state=review.turn_input.current_state,
            validation_evidence=validation_evidence,
        )
        return replace(
            review,
            semantic_validation=self.semantic_validator.validate(
                validation_request,
                validation_custody,
            ),
        )

    def _repair_rejected_ordinary(
        self,
        review: LeanReviewRecordV1,
    ) -> LeanReviewRecordV1:
        validation = review.semantic_validation
        if validation is None or not validation.verdict.automatic_repair_eligible:
            raise StateConflictError("automatic repair requires one eligible rejection")
        authority = json.loads(review.candidate.primary_authority_json)
        if not isinstance(authority, Mapping):
            raise StateConflictError("automatic repair lost the cognition authority")
        successor = self._prepare_review(
            review.turn_input,
            route=SceneRoute.ORDINARY,
            primary_authority=authority,
            planner_provider_operations=0,
            repaired_from_candidate_id=review.candidate.candidate_id,
            repair_validation=validation,
        )
        successor = self._validate_ordinary_review(
            successor,
            validation_evidence=tuple(
                to_primitive(value) for value in validation.request.selected_evidence
            ),
        )
        terminal = replace(review, state=LeanReviewState.REPAIRED)
        request_sha256 = decision_request_sha256(
            action="automatic_repair",
            feedback=validation.binding_sha256,
        )
        decision = DecisionReplayV1(
            action="automatic_repair",
            request_sha256=request_sha256,
            result=LeanDecisionResultV1(review=terminal, successor=successor),
        )
        self._commit_review_transition(
            review,
            terminal=terminal,
            successor=successor,
            decision=decision,
        )
        if (
            successor.semantic_validation is not None
            and successor.semantic_validation.verdict.verdict is SemanticVerdict.PASS
        ):
            accepted = self.accept(
                successor.review_id,
                acceptance_action="automatic_accept",
            ).review
            self._decisions[review.review_id] = replace(
                decision,
                result=replace(decision.result, successor=accepted),
            )
            try:
                self._persist_review_state()
            except Exception:
                # The successor Accept is already immutable story truth.  A
                # trailing predecessor-replay snapshot is recoverable
                # bookkeeping and must not turn a successful turn into a
                # false uncommitted transport error.
                pass
            return accepted
        return successor

    def start_adult(self, turn: LeanSceneTurnInputV1) -> LeanReviewRecordV1:
        with self._lock:
            self._require_no_unresolved(turn.world_id, turn.branch_id)
            if turn.adult_handoff is None or not turn.adult_handoff:
                raise ContractValidationError("adult route requires an authorized handoff")
            review = self._prepare_review(
                turn,
                route=SceneRoute.ADULT,
                primary_authority=turn.adult_handoff,
                planner_provider_operations=0,
            )
            self._register_review(review)
            return review

    def get_review(self, review_id: str) -> LeanReviewRecordV1:
        with self._lock:
            if review_id in self._reviews:
                return self._reviews[review_id]
            replay = self._decisions.get(review_id)
            if replay is not None:
                return replay.result.review
            raise StateConflictError("unknown Pi Scene review")

    def unresolved_review(
        self,
        *,
        world_id: str,
        branch_id: str,
    ) -> LeanReviewRecordV1 | None:
        """Return the current provisional review without performing model work."""

        with self._lock:
            review_id = self._unresolved_by_branch.get((world_id, branch_id))
            return None if review_id is None else self._reviews[review_id]

    def accept_unresolved_for_new_turn(
        self,
        *,
        world_id: str,
        branch_id: str,
    ) -> LeanDecisionResultV1 | None:
        """Commit the prior published candidate before a later chat message.

        Published review-ready candidates have already passed every hard
        structural contract. Current Ted flags are explicitly advisory; a
        future typed blocker must prevent publication or be handled here as a
        Decline before it can become automatic story state.
        """

        with self._lock:
            review_id = self._unresolved_by_branch.get((world_id, branch_id))
            if review_id is None:
                return None
            review = self._reviews[review_id]
            if (
                review.semantic_validation is not None
                and review.semantic_validation.verdict.verdict is SemanticVerdict.REJECT
            ):
                return self.decline(review_id, allow_replay=True)
            return self.accept(review_id, allow_replay=True)

    def accept(
        self,
        review_id: str,
        *,
        allow_replay: bool = False,
        acceptance_action: str = "accept",
    ) -> LeanDecisionResultV1:
        with self._lock:
            if type(allow_replay) is not bool:
                raise ContractValidationError("Accept replay flag must be boolean")
            if acceptance_action not in {
                "accept",
                "automatic_accept",
                "provisional_accept",
            }:
                raise ContractValidationError("Accept action is invalid")
            decision_action = (
                "accept_provisional"
                if acceptance_action == "provisional_accept"
                else "accept"
            )
            request_sha256 = decision_request_sha256(action=decision_action)
            if allow_replay:
                replay = self._replay_decision(
                    review_id,
                    action=decision_action,
                    request_sha256=request_sha256,
                )
                if replay is not None:
                    return replay
            review = self._current_review(review_id)
            accepted = self.store.accept(
                review.candidate,
                semantic_validation=review.semantic_validation,
                acceptance_action=acceptance_action,
            )
            # The review becomes terminal immediately after the immutable
            # receipt. Any later session or Recorder exception therefore
            # cannot authorize a second Accept.
            terminal = replace(
                review,
                state=LeanReviewState.ACCEPTED,
                accepted_receipt=accepted,
            )
            self._reviews[review_id] = terminal
            self._unresolved_by_branch.pop(
                (review.candidate.world_id, review.candidate.branch_id), None
            )
            warnings: list[str] = []
            try:
                self._persist_review_state()
            except Exception:
                # Story truth is already committed. A stale provisional snapshot
                # is reconciled against the accepted head on restart, so this is
                # an operational warning rather than a false failed-Accept result.
                warnings.append("review_state_cleanup_pending")
            try:
                recording = self._record_after_accept(terminal)
            except Exception:
                recording = None
                warnings.append("recording_state_pending")
            terminal = replace(terminal, recording_attempt=recording)
            self._reviews[review_id] = terminal
            result = LeanDecisionResultV1(
                review=terminal,
                operational_warnings=tuple(warnings),
            )
            self._decisions[review_id] = DecisionReplayV1(
                action=decision_action,
                request_sha256=request_sha256,
                result=result,
            )
            try:
                # Retain accepted review custody only while its derived
                # recording still needs repair. A complete recording retires
                # the durable UI review on this same atomic snapshot.
                self._persist_review_state()
            except Exception:
                if "review_state_cleanup_pending" not in warnings:
                    warnings.append("review_state_cleanup_pending")
                result = replace(result, operational_warnings=tuple(warnings))
                self._decisions[review_id] = replace(
                    self._decisions[review_id],
                    result=result,
                )
            return result

    def decline(
        self,
        review_id: str,
        *,
        allow_replay: bool = False,
    ) -> LeanDecisionResultV1:
        with self._lock:
            if type(allow_replay) is not bool:
                raise ContractValidationError("Decline replay flag must be boolean")
            request_sha256 = decision_request_sha256(action="decline")
            if allow_replay:
                replay = self._replay_decision(
                    review_id,
                    action="decline",
                    request_sha256=request_sha256,
                )
                if replay is not None:
                    return replay
            review = self._current_review(review_id)
            terminal = replace(review, state=LeanReviewState.DECLINED)
            result = LeanDecisionResultV1(review=terminal)
            self._commit_review_transition(
                review,
                terminal=terminal,
                successor=None,
                decision=DecisionReplayV1(
                    action="decline",
                    request_sha256=request_sha256,
                    result=result,
                ),
            )
            return result

    def regenerate(
        self,
        review_id: str,
        *,
        feedback: str | None = None,
        force_rehydrate: bool = False,
        turn_input: LeanSceneTurnInputV1 | None = None,
        allow_replay: bool = False,
    ) -> LeanDecisionResultV1:
        with self._lock:
            if feedback is not None:
                if not isinstance(feedback, str):
                    raise ContractValidationError("Regenerate feedback must be text")
                if not feedback.strip():
                    raise ContractValidationError("Regenerate feedback is empty")
            if type(force_rehydrate) is not bool:
                raise ContractValidationError("Regenerate rehydrate flag must be boolean")
            if type(allow_replay) is not bool:
                raise ContractValidationError("Regenerate replay flag must be boolean")
            request_sha256 = decision_request_sha256(
                action="regenerate",
                feedback=feedback,
                force_rehydrate=force_rehydrate,
                turn_input=turn_input,
            )
            if allow_replay:
                replay = self._replay_decision(
                    review_id,
                    action="regenerate",
                    request_sha256=request_sha256,
                )
                if replay is not None:
                    return replay
            review = self._current_review(review_id)
            replacement_turn = review.turn_input
            if turn_input is not None:
                self._validate_regeneration_turn(review, turn_input)
                replacement_turn = turn_input
            guidance = (
                None
                if feedback is None
                else CreatorGuidanceV1.create(action="regenerate", text=feedback)
            )
            if review.candidate.route is SceneRoute.ORDINARY:
                planned = self._plan_ordinary(
                    replacement_turn,
                    creator_guidance=guidance,
                )
                successor = self._prepare_review(
                    replacement_turn,
                    route=SceneRoute.ORDINARY,
                    primary_authority=(planned.decision_bundle or planned.sequence),
                    planner_provider_operations=planned.provider_operations,
                    regenerated_from_candidate_id=review.candidate.candidate_id,
                    creator_guidance=guidance,
                    force_rehydrate=force_rehydrate,
                )
                successor = self._validate_ordinary_review(
                    successor,
                    validation_evidence=planned.validation_evidence,
                )
            else:
                authority = json.loads(review.candidate.primary_authority_json)
                if not isinstance(authority, Mapping):
                    raise StateConflictError("adult Regenerate lost its route authority")
                successor = self._prepare_review(
                    replacement_turn,
                    route=SceneRoute.ADULT,
                    primary_authority=authority,
                    planner_provider_operations=0,
                    regenerated_from_candidate_id=review.candidate.candidate_id,
                    creator_guidance=guidance,
                    force_rehydrate=force_rehydrate,
                )
            terminal = replace(review, state=LeanReviewState.REGENERATED)
            result = LeanDecisionResultV1(review=terminal, successor=successor)
            decision = DecisionReplayV1(
                action="regenerate",
                request_sha256=request_sha256,
                result=result,
            )
            self._commit_review_transition(
                review,
                terminal=terminal,
                successor=successor,
                decision=decision,
            )
            if (
                successor.semantic_validation is not None
                and successor.semantic_validation.verdict.verdict is SemanticVerdict.PASS
            ):
                accepted = self.accept(
                    successor.review_id,
                    acceptance_action="automatic_accept",
                ).review
                result = replace(result, successor=accepted)
                self._decisions[review.review_id] = replace(decision, result=result)
                try:
                    self._persist_review_state()
                except Exception:
                    result = replace(
                        result,
                        operational_warnings=("review_state_cleanup_pending",),
                    )
            return result

    def replan(
        self,
        review_id: str,
        *,
        feedback: str | None = None,
        allow_replay: bool = False,
    ) -> LeanDecisionResultV1:
        with self._lock:
            if feedback is not None and not isinstance(feedback, str):
                raise ContractValidationError("Replan feedback must be text")
            if type(allow_replay) is not bool:
                raise ContractValidationError("Replan replay flag must be boolean")
            normalized_feedback = "" if feedback is None else feedback
            request_sha256 = decision_request_sha256(
                action="replan",
                feedback=normalized_feedback,
            )
            if allow_replay:
                replay = self._replay_decision(
                    review_id,
                    action="replan",
                    request_sha256=request_sha256,
                )
                if replay is not None:
                    return replay
            review = self._current_review(review_id)
            if review.candidate.route is not SceneRoute.ORDINARY:
                raise ContractValidationError("adult route does not use a Codex replan")
            guidance = CreatorGuidanceV1.create(
                action="replan",
                text=normalized_feedback,
            )
            planned = self._plan_ordinary(
                review.turn_input,
                creator_guidance=guidance,
            )
            successor = self._prepare_review(
                review.turn_input,
                route=SceneRoute.ORDINARY,
                primary_authority=(planned.decision_bundle or planned.sequence),
                planner_provider_operations=planned.provider_operations,
                replanned_from_candidate_id=review.candidate.candidate_id,
                creator_guidance=guidance,
            )
            successor = self._validate_ordinary_review(
                successor,
                validation_evidence=planned.validation_evidence,
            )
            terminal = replace(review, state=LeanReviewState.REPLANNED)
            result = LeanDecisionResultV1(review=terminal, successor=successor)
            decision = DecisionReplayV1(
                action="replan",
                request_sha256=request_sha256,
                result=result,
            )
            self._commit_review_transition(
                review,
                terminal=terminal,
                successor=successor,
                decision=decision,
            )
            if (
                successor.semantic_validation is not None
                and successor.semantic_validation.verdict.verdict is SemanticVerdict.PASS
            ):
                accepted = self.accept(
                    successor.review_id,
                    acceptance_action="automatic_accept",
                ).review
                result = replace(result, successor=accepted)
                self._decisions[review.review_id] = replace(decision, result=result)
                try:
                    self._persist_review_state()
                except Exception:
                    result = replace(
                        result,
                        operational_warnings=("review_state_cleanup_pending",),
                    )
            return result

    def repair_recording(self, review_id: str) -> LeanDecisionResultV1:
        with self._lock:
            review = self.get_review(review_id)
            if review.state != LeanReviewState.ACCEPTED or review.accepted_receipt is None:
                raise StateConflictError("recording repair requires an accepted turn")
            status = self.store.recording_status(review.accepted_receipt)
            if status is RecordingStatus.COMPLETE:
                raise StateConflictError("accepted turn recording is already complete")
            recording = self._record_after_accept(review)
            terminal = replace(review, recording_attempt=recording)
            self._reviews[review_id] = terminal
            accepted_decision = self._decisions.get(review_id)
            if accepted_decision is None:
                decision_action = (
                    "accept_provisional"
                    if review.accepted_receipt.creator_action == "provisional_accept"
                    else "accept"
                )
                accepted_decision = DecisionReplayV1(
                    action=decision_action,
                    request_sha256=decision_request_sha256(action=decision_action),
                    result=LeanDecisionResultV1(review=terminal),
                )
            elif accepted_decision.action not in {"accept", "accept_provisional"}:
                raise StateConflictError(
                    "recording repair review has a conflicting decision receipt"
                )
            else:
                accepted_decision = replace(
                    accepted_decision,
                    result=replace(accepted_decision.result, review=terminal),
                )
            self._decisions[review_id] = accepted_decision
            warnings: tuple[str, ...] = ()
            try:
                self._persist_review_state()
            except Exception:
                warnings = ("review_state_cleanup_pending",)
            return LeanDecisionResultV1(
                review=terminal,
                operational_warnings=warnings,
            )

    def repair_latest_recording(
        self,
        turn: LeanSceneTurnInputV1,
    ) -> LeanRecordingAttemptV1:
        """Resume only post-Accept recording from authoritative branch state."""

        with self._lock:
            head = self.store.load_head(world_id=turn.world_id, branch_id=turn.branch_id)
            accepted = head.receipt
            if accepted is None or head.recording_status is RecordingStatus.COMPLETE:
                raise StateConflictError("latest accepted turn does not require recording repair")
            if accepted.exact_user_source != turn.exact_user_source:
                raise StateConflictError("recording repair turn source changed")
            prior = self.store.load_recording_attempt(accepted)
            return self._record_accepted(accepted, turn, prior)

    def _record_after_accept(self, review: LeanReviewRecordV1) -> LeanRecordingAttemptV1:
        """Convert any post-Accept Recorder failure into repairable custody.

        Acceptance has already committed before this method runs. A transport,
        view, session, or parsing failure must therefore be reported as
        ``pending_repair`` instead of escaping as though Accept itself failed.
        The durable Pi operation ledger remains the provider-accounting source.
        """

        assert_provider_dispatch_allowed(
            "pi_scene.record_after_accept",
            external_provider_boundary=is_external_provider_boundary(self.pi),
        )
        accepted = review.accepted_receipt
        if accepted is None:
            raise StateConflictError("Recorder recovery requires an accepted turn")
        operations_before = _pi_operation_count(self.pi)
        attempt_number = (
            1 if review.recording_attempt is None else review.recording_attempt.attempt_number + 1
        )
        try:
            self.store.promote_pi_session(
                accepted,
                session_id=review.pi_session_id,
                session_path=review.pi_session_dir,
            )
            return self._record(review)
        except Exception as exc:
            operations_after = _pi_operation_count(self.pi)
            request_sha256 = canonical_sha256(
                {
                    "schema_version": "cera.pi_scene.recorder_recovery_binding.v1",
                    "accepted_receipt_sha256": accepted.receipt_sha256,
                    "attempt_number": attempt_number,
                    "route": accepted.route.value,
                    "purpose": "recorder",
                }
            )
            return self.store.mark_recording_failure(
                accepted,
                recorder_request_sha256=request_sha256,
                recorder_output_sha256=None,
                provider_operations=max(0, operations_after - operations_before),
                failure_code=f"recorder_runtime:{type(exc).__name__}",
            )

    def _prepare_review(
        self,
        turn: LeanSceneTurnInputV1,
        *,
        route: SceneRoute,
        primary_authority: Mapping[str, Any],
        planner_provider_operations: int,
        regenerated_from_candidate_id: str | None = None,
        replanned_from_candidate_id: str | None = None,
        repaired_from_candidate_id: str | None = None,
        creator_guidance: CreatorGuidanceV1 | None = None,
        force_rehydrate: bool = False,
        repair_validation: BoundSemanticValidationV1 | None = None,
    ) -> LeanReviewRecordV1:
        head = self.store.load_head(world_id=turn.world_id, branch_id=turn.branch_id)
        candidate_counter = self._reserve_candidate_counter()
        authority_json, authority_sha = canonical_authority(primary_authority)
        turn_context_sha256 = canonical_sha256(turn)
        identity = canonical_sha256(
            {
                "world_id": turn.world_id,
                "branch_id": turn.branch_id,
                "scene_id": turn.scene_id,
                "generation": head.generation + 1,
                "source_sha256": text_sha256(turn.exact_user_source),
                "primary_authority_sha256": authority_sha,
                "route": route.value,
                "turn_context_sha256": turn_context_sha256,
                "creator_guidance_sha256": (
                    None if creator_guidance is None else canonical_sha256(creator_guidance)
                ),
                "candidate_counter": candidate_counter,
            }
        )
        candidate_id = f"candidate-{identity[:28]}"
        turn_id = f"turn-{head.generation + 1:04d}-{text_sha256(turn.exact_user_source)[:12]}"
        if route is SceneRoute.ADULT:
            accepted_records = self.store.recent_adult_context_payloads(
                world_id=turn.world_id,
                branch_id=turn.branch_id,
            )
        else:
            accepted_records = self.store.recent_ordinary_context_payloads(
                world_id=turn.world_id,
                branch_id=turn.branch_id,
            )
        writer_records = _writer_context_records(accepted_records)
        view = self.writer_views.materialize(
            WriterViewInputV1(
                world_id=turn.world_id,
                branch_id=turn.branch_id,
                scene_id=turn.scene_id,
                turn_id=turn_id,
                candidate_id=candidate_id,
                route=route,
                user_prompt=turn.exact_user_source,
                primary_authority=primary_authority,
                current_state=_controlled_current_state(
                    turn,
                    creator_guidance=creator_guidance,
                ),
                characters=turn.characters,
                relationships=turn.relationships,
                recent_prose=turn.recent_prose,
                relevant_memories=turn.relevant_memories,
                voice_examples=turn.voice_examples,
                craft_index=turn.craft_index,
                accepted_records=writer_records,
                purpose="writer",
            )
        )
        prompt = _writer_prompt(repair_validation)
        accepted_session = self.store.load_accepted_pi_session(
            world_id=turn.world_id,
            branch_id=turn.branch_id,
        )
        if (
            accepted_session is not None
            and not force_rehydrate
            and (head.receipt is None or head.receipt.route is not route)
        ):
            # Pi forks retain prior role/system history. Cross-route soft
            # continuity is rehydrated from accepted Python state instead.
            accepted_session = None
        # A missing/stale accepted session is the Pi adapter's natural fresh
        # rehydration path. Keep ``force_rehydrate`` false when no parent
        # exists because an explicit reset is valid only with a parent proof.
        pi_result = self.pi.invoke(
            PiSceneInvocationV1(
                route=route,
                purpose="writer",
                view=view,
                prompt=prompt,
                candidate_id=candidate_id,
                session_dir=self._session_dir(turn.world_id, turn.branch_id),
                accepted_parent_session=accepted_session,
                # Clean Pi forks proved unable to preserve the exact Writer
                # envelope reliably.  Accepted Python state therefore
                # rehydrates every candidate in a fresh session.  The parent
                # remains attached for custody/telemetry, but is never forked.
                force_rehydrate=(force_rehydrate or accepted_session is not None),
            )
        )
        candidate = LeanCandidateV1(
            schema_version=LeanCandidateV1.SCHEMA_VERSION,
            request_id=f"request-{text_sha256(identity + ':request')[:28]}",
            candidate_id=candidate_id,
            turn_id=turn_id,
            world_id=turn.world_id,
            branch_id=turn.branch_id,
            scene_id=turn.scene_id,
            generation=head.generation + 1,
            parent_accepted_turn_id=head.accepted_turn_id,
            accepted_head_before_sha256=head.accepted_head_sha256,
            exact_user_source=turn.exact_user_source,
            exact_user_source_sha256=text_sha256(turn.exact_user_source),
            route=route,
            primary_authority_kind=_primary_authority_kind(
                route=route,
                primary_authority=primary_authority,
            ),
            primary_authority_json=authority_json,
            primary_authority_sha256=authority_sha,
            writer_view_manifest_sha256=view.manifest_sha256,
            story_text=pi_result.output_text,
            story_text_sha256=text_sha256(pi_result.output_text),
            writer_receipt=pi_result.writer_receipt,
            warnings=advisory_ted_warnings(pi_result.output_text),
        )
        result = LeanRunResultV1(
            schema_version=LeanRunResultV1.SCHEMA_VERSION,
            candidate=candidate,
            planner_provider_operations=planner_provider_operations,
            writer_provider_operations=pi_result.writer_receipt.provider_operations,
            regenerated_from_candidate_id=regenerated_from_candidate_id,
            replanned_from_candidate_id=replanned_from_candidate_id,
            repaired_from_candidate_id=repaired_from_candidate_id,
        )
        review_id = f"review-{candidate.candidate_sha256[:28]}"
        review = LeanReviewRecordV1(
            review_id=review_id,
            turn_input=turn,
            result=result,
            pi_session_id=pi_result.session_id,
            pi_session_dir=pi_result.session_dir,
            created_unix_seconds=int(time.time()),
            creator_guidance=creator_guidance,
        )
        return review

    def _planner_for(self, turn: LeanSceneTurnInputV1) -> OrdinaryPlannerPort:
        if self._planner_resolver is None or turn.request_controls is None:
            return self.planner
        planner = self._planner_resolver(turn)
        if not callable(getattr(planner, "plan", None)):
            raise ContractValidationError("Pi Scene Planner resolver returned an invalid port")
        return planner

    def _reserve_candidate_counter(self) -> int:
        self._candidate_counter += 1
        try:
            self._persist_review_state()
        except Exception:
            self._candidate_counter -= 1
            raise
        return self._candidate_counter

    def _register_review(self, review: LeanReviewRecordV1) -> None:
        key = (review.candidate.world_id, review.candidate.branch_id)
        if review.review_id in self._reviews or key in self._unresolved_by_branch:
            raise StateConflictError("Pi Scene review identity is already occupied")
        prior_reviews = dict(self._reviews)
        prior_unresolved = dict(self._unresolved_by_branch)
        self._reviews[review.review_id] = review
        self._unresolved_by_branch[key] = review.review_id
        try:
            self._persist_review_state()
        except Exception:
            self._reviews = prior_reviews
            self._unresolved_by_branch = prior_unresolved
            raise

    def _commit_review_transition(
        self,
        prior: LeanReviewRecordV1,
        *,
        terminal: LeanReviewRecordV1,
        successor: LeanReviewRecordV1 | None,
        decision: DecisionReplayV1,
    ) -> None:
        key = (prior.candidate.world_id, prior.candidate.branch_id)
        if self._unresolved_by_branch.get(key) != prior.review_id:
            raise StateConflictError("Pi Scene review transition lost branch ownership")
        if successor is not None and (
            successor.state != LeanReviewState.REVIEW_READY
            or (successor.candidate.world_id, successor.candidate.branch_id) != key
            or successor.review_id in self._reviews
        ):
            raise StateConflictError("Pi Scene successor review identity is invalid")
        if prior.review_id in self._decisions:
            raise StateConflictError("Pi Scene review already has a decision receipt")

        prior_reviews = dict(self._reviews)
        prior_unresolved = dict(self._unresolved_by_branch)
        prior_decisions = dict(self._decisions)
        self._reviews[prior.review_id] = terminal
        if successor is None:
            self._unresolved_by_branch.pop(key, None)
        else:
            self._reviews[successor.review_id] = successor
            self._unresolved_by_branch[key] = successor.review_id
        self._decisions[prior.review_id] = decision
        try:
            self._persist_review_state()
        except Exception:
            self._reviews = prior_reviews
            self._unresolved_by_branch = prior_unresolved
            self._decisions = prior_decisions
            raise

    def _replay_decision(
        self,
        review_id: str,
        *,
        action: str,
        request_sha256: str,
    ) -> LeanDecisionResultV1 | None:
        prior = self._decisions.get(review_id)
        if prior is None:
            return None
        if prior.action != action:
            raise StateConflictError(f"Pi Scene review was already finalized by {prior.action}")
        if prior.request_sha256 != request_sha256:
            raise StateConflictError("Pi Scene decision replay request changed")
        return prior.result

    @staticmethod
    def _validate_regeneration_turn(
        review: LeanReviewRecordV1,
        replacement: LeanSceneTurnInputV1,
    ) -> None:
        prior = replace(review.turn_input, request_controls=None)
        incoming = replace(replacement, request_controls=None)
        if incoming != prior:
            raise StateConflictError(
                "Pi Scene regeneration changed the bound story turn; "
                "use Replan for semantic changes"
            )
        controls = replacement.request_controls
        if controls is None or controls.regeneration_key is None:
            raise ContractValidationError("Pi Scene regeneration requires its typed key")

    def _persist_review_state(self) -> None:
        self._review_state_store.persist(
            candidate_counter=self._candidate_counter,
            reviews=self._reviews,
            decisions=self._decisions,
        )

    def _record(self, review: LeanReviewRecordV1) -> LeanRecordingAttemptV1:
        accepted = review.accepted_receipt
        if accepted is None:
            raise StateConflictError("Recorder cannot run before phase-one acceptance")
        return self._record_accepted(accepted, review.turn_input, review.recording_attempt)

    def _record_accepted(
        self,
        accepted: LeanAcceptedTurnReceiptV1,
        turn_input: LeanSceneTurnInputV1,
        prior_attempt: LeanRecordingAttemptV1 | None,
    ) -> LeanRecordingAttemptV1:
        attempt_number = 1 if prior_attempt is None else prior_attempt.attempt_number + 1
        primary_authority = json.loads(accepted.primary_authority_json)
        if not isinstance(primary_authority, dict):
            raise ContractValidationError("Recorder primary authority is invalid")
        view = self.writer_views.materialize(
            WriterViewInputV1(
                world_id=accepted.world_id,
                branch_id=accepted.branch_id,
                scene_id=accepted.scene_id,
                turn_id=accepted.accepted_turn_id,
                candidate_id=f"record-{accepted.accepted_turn_id}-{attempt_number}",
                route=accepted.route,
                user_prompt=accepted.exact_user_source,
                primary_authority=primary_authority,
                current_state={
                    "recording_phase": "post_accept",
                    "accepted_turn_id": accepted.accepted_turn_id,
                    "authority": "exact_accepted_prose_and_primary_authority",
                },
                characters={},
                relationships={},
                recent_prose=(accepted.exact_accepted_prose,),
                relevant_memories={},
                voice_examples={},
                craft_index={},
                accepted_records=(),
                purpose="recorder",
            )
        )
        primary_path = (
            "PRIMARY_SEQUENCE.json"
            if accepted.route is SceneRoute.ORDINARY
            else "ADULT_HANDOFF.json"
        )
        prompt = (
            "Call the context tool directly once. Record the exact accepted "
            f"recent_prose/0001.txt prose against {primary_path}. "
            "Return only the route-specific semantic record fields; Python binds custody."
        )
        if prior_attempt is not None and prior_attempt.status is RecordingStatus.PENDING_REPAIR:
            prompt += (
                " This is an explicit recording repair after typed failure "
                f"{prior_attempt.failure_code}; return a fresh complete record."
            )
        fault = (
            None
            if self._recording_fault_injector is None
            else self._recording_fault_injector(accepted, attempt_number)
        )
        if fault is not None:
            return self.store.mark_recording_failure(
                accepted,
                recorder_request_sha256=canonical_sha256(
                    {
                        "schema_version": "cera.pi_scene.injected_recorder_failure.v1",
                        "accepted_receipt_sha256": accepted.receipt_sha256,
                        "attempt_number": attempt_number,
                    }
                ),
                recorder_output_sha256=None,
                provider_operations=0,
                failure_code=fault,
            )
        invocation = self.pi.invoke(
            PiSceneInvocationV1(
                route=accepted.route,
                purpose="recorder",
                view=view,
                prompt=prompt,
                candidate_id=f"recorder-{accepted.accepted_turn_id}-{attempt_number}",
                session_dir=self._session_dir(accepted.world_id, accepted.branch_id),
                # Recorder work is role-local and fresh. Only Writer sessions
                # participate in accepted soft lineage.
                accepted_parent_session=None,
            )
        )
        try:
            payload = _parse_recorder_payload(invocation.output_text)
            if accepted.route is SceneRoute.ORDINARY:
                return _attach_ordinary_payload(
                    store=self.store,
                    accepted=accepted,
                    payload=payload,
                    recorder_request_sha256=invocation.writer_receipt.request_sha256,
                    recorder_output_sha256=invocation.writer_receipt.output_sha256,
                    provider_operations=invocation.writer_receipt.provider_operations,
                )
            _require_exact_keys(
                payload,
                {"full_record", "codex_projection"},
                "adult Recorder envelope",
            )
            full_payload = payload["full_record"]
            projection_payload = payload["codex_projection"]
            if not isinstance(full_payload, dict) or not isinstance(projection_payload, dict):
                raise ContractValidationError("adult Recorder dual payload is invalid")
            _require_exact_keys(
                full_payload,
                {"decision_path", "events"},
                "adult full record",
            )
            _require_exact_keys(
                projection_payload,
                {"decision_path_summary", "items", "resulting_public_state", "unresolved_threads"},
                "adult Codex projection",
            )
            bound_full_payload = dict(full_payload)
            bound_full_payload.update(
                {
                    "schema_version": "cera.pi_scene.adult_full_record.v1",
                    "adult_handoff_sha256": accepted.primary_authority_sha256,
                    "resulting_public_state": projection_payload["resulting_public_state"],
                    "unresolved_threads": projection_payload["unresolved_threads"],
                }
            )
            full = adult_full_record_from_mapping(bound_full_payload)
            full_sha = canonical_sha256(full)
            bound_projection_payload = dict(projection_payload)
            bound_projection_payload.update(
                {
                    "schema_version": "cera.pi_scene.adult_codex_projection.v1",
                    "adult_full_record_sha256": full_sha,
                }
            )
            projection = adult_projection_from_mapping(bound_projection_payload)
            return self.store.attach_adult_records(
                accepted,
                full,
                projection,
                recorder_request_sha256=invocation.writer_receipt.request_sha256,
                recorder_output_sha256=invocation.writer_receipt.output_sha256,
                provider_operations=invocation.writer_receipt.provider_operations,
            )
        except (
            KeyError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
            ContractValidationError,
        ) as exc:
            return self.store.mark_recording_failure(
                accepted,
                recorder_request_sha256=invocation.writer_receipt.request_sha256,
                recorder_output_sha256=invocation.writer_receipt.output_sha256,
                provider_operations=invocation.writer_receipt.provider_operations,
                failure_code=_recorder_contract_failure(exc),
            )

    def _current_review(self, review_id: str) -> LeanReviewRecordV1:
        review = self.get_review(review_id)
        key = (review.candidate.world_id, review.candidate.branch_id)
        if review.state != LeanReviewState.REVIEW_READY:
            raise StateConflictError("Pi Scene review is already terminal")
        if self._unresolved_by_branch.get(key) != review_id:
            raise StateConflictError("Pi Scene review is not the current branch review")
        return review

    def _require_no_unresolved(self, world_id: str, branch_id: str) -> None:
        if (world_id, branch_id) in self._unresolved_by_branch:
            raise StateConflictError("Pi Scene creator review is unresolved")

    def _session_dir(self, world_id: str, branch_id: str) -> Path:
        path = (
            self.session_root
            / f"world-{text_sha256(world_id)[:16]}"
            / f"branch-{text_sha256(branch_id)[:16]}"
        ).resolve()
        if not path.is_relative_to(self.session_root):
            raise ContractValidationError("Pi session directory escaped its root")
        path.mkdir(parents=True, exist_ok=True)
        return path


def _controlled_current_state(
    turn: LeanSceneTurnInputV1,
    *,
    creator_guidance: CreatorGuidanceV1 | None,
) -> dict[str, Any]:
    """Project UI controls as noncanonical constraints, never source evidence."""

    state = dict(turn.current_state)
    raw_boundaries = state.get("hard_boundaries", ())
    if isinstance(raw_boundaries, (str, bytes)) or not isinstance(raw_boundaries, (list, tuple)):
        raise ContractValidationError("turn hard_boundaries must be an ordered list")
    if any(not isinstance(value, str) or not value.strip() for value in raw_boundaries):
        raise ContractValidationError("turn hard_boundaries contains invalid text")
    state["hard_boundaries"] = list(raw_boundaries)

    controls = turn.request_controls
    if controls is not None:
        state["request_controls"] = controls.model_visible

    if creator_guidance is not None:
        state["creator_control_guidance"] = to_primitive(creator_guidance)
    return state


def _primary_authority_kind(
    *,
    route: SceneRoute,
    primary_authority: Mapping[str, Any],
) -> str:
    if route is SceneRoute.ADULT:
        return "adult_handoff"
    if {
        "sequence",
        "decision_records",
        "decision_item_links",
    }.issubset(primary_authority):
        return "codex_cognition_plan"
    return "codex_sequence"


def _require_exact_keys(
    value: Mapping[str, Any],
    expected: set[str],
    label: str,
) -> None:
    if set(value) != expected:
        raise ContractValidationError(f"{label} fields changed")


def _string_array(value: Any, field_name: str) -> list[str]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise ContractValidationError(f"{field_name} must be an array of strings")
    if len(value) != len(set(value)):
        raise ContractValidationError(f"{field_name} contains duplicates")
    return value


def _secondary_canon_array(value: Any) -> list[str]:
    """Accept the Recorder's singular canonical note as one exact array item."""

    if isinstance(value, str):
        if not value.strip():
            raise ContractValidationError("secondary_canon is empty")
        return [value]
    return _string_array(value, "secondary_canon")


def _attach_ordinary_payload(
    *,
    store: LeanSceneStore,
    accepted: LeanAcceptedTurnReceiptV1,
    payload: Mapping[str, Any],
    recorder_request_sha256: str,
    recorder_output_sha256: str,
    provider_operations: int,
) -> LeanRecordingAttemptV1:
    _require_exact_keys(
        payload,
        {
            "secondary_canon",
            "resulting_public_state",
            "relationship_changes",
            "knowledge_changes",
            "durable_changes",
            "unresolved_threads",
        },
        "ordinary Recorder",
    )
    item_keys = primary_item_keys(accepted.primary_authority_json)
    resulting_public_state = payload["resulting_public_state"]
    if not isinstance(resulting_public_state, str) or not resulting_public_state.strip():
        raise ContractValidationError("resulting_public_state must be non-empty text")
    record = ordinary_record_from_mapping(
        {
            "schema_version": "cera.pi_scene.ordinary_record.v1",
            "primary_sequence_sha256": accepted.primary_authority_sha256,
            "realized_item_keys": list(item_keys),
            "secondary_canon": _secondary_canon_array(payload["secondary_canon"]),
            "resulting_public_state": resulting_public_state,
            "relationship_changes": _string_array(
                payload["relationship_changes"], "relationship_changes"
            ),
            "knowledge_changes": _string_array(payload["knowledge_changes"], "knowledge_changes"),
            "durable_changes": _string_array(payload["durable_changes"], "durable_changes"),
            "unresolved_threads": _string_array(
                payload["unresolved_threads"], "unresolved_threads"
            ),
        }
    )
    return store.attach_ordinary_record(
        accepted,
        record,
        recorder_request_sha256=recorder_request_sha256,
        recorder_output_sha256=recorder_output_sha256,
        provider_operations=provider_operations,
    )


def repair_latest_ordinary_recording_from_output(
    *,
    store: LeanSceneStore,
    turn: LeanSceneTurnInputV1,
    output_text: str,
) -> LeanRecordingAttemptV1:
    """Explicitly revalidate one already-charged Recorder output after restart."""

    head = store.load_head(world_id=turn.world_id, branch_id=turn.branch_id)
    accepted = head.receipt
    if (
        accepted is None
        or accepted.route is not SceneRoute.ORDINARY
        or head.recording_status is RecordingStatus.COMPLETE
    ):
        raise StateConflictError("latest ordinary recording does not require repair")
    if accepted.exact_user_source != turn.exact_user_source:
        raise StateConflictError("recording repair turn source changed")
    prior = store.load_recording_attempt(accepted)
    output_sha256 = text_sha256(output_text.strip())
    if prior.recorder_output_sha256 != output_sha256:
        raise StateConflictError("Recorder repair output differs from the failed attempt")
    payload = _parse_recorder_payload(output_text)
    return _attach_ordinary_payload(
        store=store,
        accepted=accepted,
        payload=payload,
        recorder_request_sha256=prior.recorder_request_sha256,
        recorder_output_sha256=output_sha256,
        provider_operations=0,
    )


def _pi_operation_count(pi: object) -> int:
    ledger = getattr(pi, "operation_ledger", None)
    value = getattr(ledger, "operation_count", 0)
    return value if type(value) is int and value >= 0 else 0


def _writer_prompt(
    repair_validation: BoundSemanticValidationV1 | None,
) -> str:
    base = (
        "Call context exactly once, use its complete confined Writer view, "
        "then produce the complete scene now without another tool call."
    )
    if repair_validation is None:
        return base
    verdict = repair_validation.verdict
    conflict = verdict.conflict
    if (
        verdict.verdict is not SemanticVerdict.REJECT
        or not verdict.automatic_repair_eligible
        or conflict is None
    ):
        raise ContractValidationError(
            "Writer repair requires one eligible semantic rejection"
        )
    target = (
        f"decision {conflict.decision_key}"
        if conflict.decision_key is not None
        else f"the exact candidate phrase {json.dumps(conflict.exact_quote)}"
    )
    return (
        f"{base} This is the only complete repair attempt. The prior candidate "
        f"was rejected for {conflict.conflict_class.value} at {target}: "
        f"{conflict.concise_explanation} Produce a fresh complete scene from "
        "the unchanged Writer view; do not quote, patch, or continue the rejected prose."
    )


def _writer_context_records(
    accepted_records: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], ...]:
    """Project lean accepted continuity without replaying old turn authority."""

    output: list[dict[str, Any]] = []
    for record in accepted_records:
        receipt = record.get("receipt")
        if not isinstance(receipt, Mapping):
            raise StateConflictError("accepted Writer context omitted its receipt")
        route = receipt.get("route")
        prose = receipt.get("exact_accepted_prose")
        if prose is None:
            # The ordinary route receives only the non-explicit projection of
            # an accepted adult turn. Its receipt hash remains available for
            # chain custody, but exact adult prose must not cross to Codex.
            if route != SceneRoute.ADULT.value or "adult_projection" not in record:
                raise StateConflictError("accepted prose link changed")
        elif not isinstance(prose, str) or receipt.get(
            "exact_accepted_prose_sha256"
        ) != text_sha256(prose):
            raise StateConflictError("accepted prose link changed")
        projected_receipt = {
            key: receipt[key]
            for key in (
                "schema_version",
                "accepted_turn_id",
                "generation",
                "route",
                "exact_user_source_sha256",
                "exact_accepted_prose_sha256",
                "primary_authority_sha256",
            )
            if key in receipt
        }
        item: dict[str, Any] = {"receipt": projected_receipt}
        for key in ("ordinary_record", "adult_projection", "adult_full_record"):
            value = record.get(key)
            if value is not None:
                item[key] = value
        output.append(item)
    return tuple(output)


def _recorder_contract_failure(exc: BaseException) -> str:
    """Preserve bounded structural feedback without recording provider prose."""

    detail = " ".join(str(exc).split())
    prefix = f"recorder_contract:{type(exc).__name__}"
    return prefix if not detail else f"{prefix}:{detail[:240]}"


def _parse_recorder_payload(output_text: str) -> dict[str, Any]:
    """Decode raw JSON or one exact JSON fence without semantic repair."""

    value = output_text.strip()
    fence = "```json\n"
    if value.count("```") == 2 and fence in value:
        start = value.index(fence) + len(fence)
        end = value.index("```", start)
        value = value[start:end].strip()
    payload = json.loads(value)
    if not isinstance(payload, dict):
        raise ContractValidationError("Recorder result is not a JSON object")
    return payload
