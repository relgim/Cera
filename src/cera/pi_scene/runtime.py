"""Provider-neutral coordinator for the lean Pi Scene route."""

from __future__ import annotations

from dataclasses import dataclass, replace
import json
from pathlib import Path
from threading import RLock
from typing import Any, Callable, Mapping, Protocol, Sequence

from cera.errors import ContractValidationError, StateConflictError
from cera.serialization import canonical_sha256, text_sha256

from .contracts import (
    AdultCodexProjectionV1,
    AdultProjectionItemV1,
    LeanAcceptedTurnReceiptV1,
    LeanCandidateV1,
    LeanRecordingAttemptV1,
    LeanRunResultV1,
    RecordingStatus,
    SceneRoute,
    advisory_ted_warnings,
    canonical_authority,
)
from .pi_adapter import PiSceneAdapter, PiSceneInvocationResultV1, PiSceneInvocationV1
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


@dataclass(frozen=True, slots=True)
class PlannerTurnOutputV1:
    sequence: Mapping[str, Any]
    provider_operations: int

    def __post_init__(self) -> None:
        if not self.sequence:
            raise ContractValidationError("ordinary Planner returned no sequence")
        if type(self.provider_operations) is not int or self.provider_operations < 1:
            raise ContractValidationError("ordinary Planner operation count is invalid")


class OrdinaryPlannerPort(Protocol):
    def plan(self, request: PlannerTurnInputV1) -> PlannerTurnOutputV1: ...


@dataclass(frozen=True, slots=True)
class LeanSceneTurnInputV1:
    world_id: str
    branch_id: str
    scene_id: str
    exact_user_source: str
    current_state: Mapping[str, Any]
    characters: Mapping[str, Mapping[str, Any]]
    relationships: Mapping[str, Mapping[str, Any]]
    recent_prose: Sequence[Mapping[str, Any] | str]
    relevant_memories: Mapping[str, Mapping[str, Any]]
    voice_examples: Mapping[str, Mapping[str, Any] | str]
    craft_index: Mapping[str, Any]
    adult_handoff: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        for field_name in ("world_id", "branch_id", "scene_id", "exact_user_source"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ContractValidationError(f"turn input {field_name} is empty")
        if not self.current_state:
            raise ContractValidationError("turn input requires current accepted state")


class LeanReviewState(str):
    REVIEW_READY = "review_ready"
    ACCEPTED = "accepted"
    DECLINED = "declined"
    REGENERATED = "regenerated"
    REPLANNED = "replanned"


@dataclass(frozen=True, slots=True)
class LeanReviewRecordV1:
    review_id: str
    turn_input: LeanSceneTurnInputV1
    result: LeanRunResultV1
    pi_session_id: str
    pi_session_dir: Path
    state: str = LeanReviewState.REVIEW_READY
    accepted_receipt: LeanAcceptedTurnReceiptV1 | None = None
    recording_attempt: LeanRecordingAttemptV1 | None = None

    @property
    def candidate(self) -> LeanCandidateV1:
        return self.result.candidate


@dataclass(frozen=True, slots=True)
class LeanDecisionResultV1:
    review: LeanReviewRecordV1
    successor: LeanReviewRecordV1 | None = None


RecordingFaultInjector = Callable[[LeanAcceptedTurnReceiptV1, int], str | None]


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
    ) -> None:
        self.store = store
        self.planner = planner
        self.writer_views = writer_views
        self.pi = pi
        self.session_root = session_root.resolve()
        self.session_root.mkdir(parents=True, exist_ok=True)
        self._recording_fault_injector = recording_fault_injector
        self._reviews: dict[str, LeanReviewRecordV1] = {}
        self._unresolved_by_branch: dict[tuple[str, str], str] = {}
        self._candidate_counter = 0
        self._lock = RLock()

    def start_ordinary(self, turn: LeanSceneTurnInputV1) -> LeanReviewRecordV1:
        with self._lock:
            self._require_no_unresolved(turn.world_id, turn.branch_id)
            accepted_records = self.store.recent_accepted_payloads(
                world_id=turn.world_id,
                branch_id=turn.branch_id,
                adult_full=False,
            )
            planned = self.planner.plan(
                PlannerTurnInputV1(
                    world_id=turn.world_id,
                    branch_id=turn.branch_id,
                    scene_id=turn.scene_id,
                    exact_user_source=turn.exact_user_source,
                    current_state=turn.current_state,
                    characters=turn.characters,
                    relationships=turn.relationships,
                    relevant_memories=turn.relevant_memories,
                    accepted_records=accepted_records,
                )
            )
            return self._generate(
                turn,
                route=SceneRoute.ORDINARY,
                primary_authority=planned.sequence,
                planner_provider_operations=planned.provider_operations,
            )

    def start_adult(self, turn: LeanSceneTurnInputV1) -> LeanReviewRecordV1:
        with self._lock:
            self._require_no_unresolved(turn.world_id, turn.branch_id)
            if turn.adult_handoff is None or not turn.adult_handoff:
                raise ContractValidationError("adult route requires an authorized handoff")
            return self._generate(
                turn,
                route=SceneRoute.ADULT,
                primary_authority=turn.adult_handoff,
                planner_provider_operations=0,
            )

    def get_review(self, review_id: str) -> LeanReviewRecordV1:
        try:
            return self._reviews[review_id]
        except KeyError as exc:
            raise StateConflictError("unknown Pi Scene review") from exc

    def accept(self, review_id: str) -> LeanDecisionResultV1:
        with self._lock:
            review = self._current_review(review_id)
            accepted = self.store.accept(review.candidate)
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
            recording = self._record_after_accept(terminal)
            terminal = replace(terminal, recording_attempt=recording)
            self._reviews[review_id] = terminal
            return LeanDecisionResultV1(review=terminal)

    def decline(self, review_id: str) -> LeanDecisionResultV1:
        with self._lock:
            review = self._current_review(review_id)
            terminal = replace(review, state=LeanReviewState.DECLINED)
            self._reviews[review_id] = terminal
            self._unresolved_by_branch.pop(
                (review.candidate.world_id, review.candidate.branch_id), None
            )
            return LeanDecisionResultV1(review=terminal)

    def regenerate(
        self,
        review_id: str,
        *,
        feedback: str | None = None,
        force_rehydrate: bool = False,
    ) -> LeanDecisionResultV1:
        with self._lock:
            review = self._current_review(review_id)
            if feedback is not None and not feedback.strip():
                raise ContractValidationError("Regenerate feedback is empty")
            terminal = replace(review, state=LeanReviewState.REGENERATED)
            self._reviews[review_id] = terminal
            self._unresolved_by_branch.pop(
                (review.candidate.world_id, review.candidate.branch_id), None
            )
            authority = json.loads(review.candidate.primary_authority_json)
            successor = self._generate(
                review.turn_input,
                route=review.candidate.route,
                primary_authority=authority,
                planner_provider_operations=0,
                regenerated_from_candidate_id=review.candidate.candidate_id,
                feedback=feedback,
                force_rehydrate=force_rehydrate,
            )
            if (
                successor.candidate.primary_authority_json
                != review.candidate.primary_authority_json
                or successor.candidate.primary_authority_sha256
                != review.candidate.primary_authority_sha256
            ):
                raise StateConflictError("Regenerate changed the exact primary authority")
            return LeanDecisionResultV1(review=terminal, successor=successor)

    def replan(
        self,
        review_id: str,
        *,
        feedback: str | None = None,
    ) -> LeanDecisionResultV1:
        with self._lock:
            review = self._current_review(review_id)
            if review.candidate.route is not SceneRoute.ORDINARY:
                raise ContractValidationError("adult route does not use a Codex replan")
            if feedback is not None and not feedback.strip():
                raise ContractValidationError("Replan feedback is empty")
            terminal = replace(review, state=LeanReviewState.REPLANNED)
            self._reviews[review_id] = terminal
            self._unresolved_by_branch.pop(
                (review.candidate.world_id, review.candidate.branch_id), None
            )
            accepted_records = self.store.recent_accepted_payloads(
                world_id=review.turn_input.world_id,
                branch_id=review.turn_input.branch_id,
                adult_full=False,
            )
            source = review.turn_input.exact_user_source
            if feedback:
                source = f"{source}\n\nCreator replan guidance: {feedback}"
            planned = self.planner.plan(
                PlannerTurnInputV1(
                    world_id=review.turn_input.world_id,
                    branch_id=review.turn_input.branch_id,
                    scene_id=review.turn_input.scene_id,
                    exact_user_source=source,
                    current_state=review.turn_input.current_state,
                    characters=review.turn_input.characters,
                    relationships=review.turn_input.relationships,
                    relevant_memories=review.turn_input.relevant_memories,
                    accepted_records=accepted_records,
                )
            )
            successor = self._generate(
                review.turn_input,
                route=SceneRoute.ORDINARY,
                primary_authority=planned.sequence,
                planner_provider_operations=planned.provider_operations,
                replanned_from_candidate_id=review.candidate.candidate_id,
                feedback=feedback,
            )
            return LeanDecisionResultV1(review=terminal, successor=successor)

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
            return LeanDecisionResultV1(review=terminal)

    def _record_after_accept(self, review: LeanReviewRecordV1) -> LeanRecordingAttemptV1:
        """Convert any post-Accept Recorder failure into repairable custody.

        Acceptance has already committed before this method runs. A transport,
        view, session, or parsing failure must therefore be reported as
        ``pending_repair`` instead of escaping as though Accept itself failed.
        The durable Pi operation ledger remains the provider-accounting source.
        """

        accepted = review.accepted_receipt
        if accepted is None:
            raise StateConflictError("Recorder recovery requires an accepted turn")
        operations_before = _pi_operation_count(self.pi)
        attempt_number = (
            1
            if review.recording_attempt is None
            else review.recording_attempt.attempt_number + 1
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

    def _generate(
        self,
        turn: LeanSceneTurnInputV1,
        *,
        route: SceneRoute,
        primary_authority: Mapping[str, Any],
        planner_provider_operations: int,
        regenerated_from_candidate_id: str | None = None,
        replanned_from_candidate_id: str | None = None,
        feedback: str | None = None,
        force_rehydrate: bool = False,
    ) -> LeanReviewRecordV1:
        head = self.store.load_head(world_id=turn.world_id, branch_id=turn.branch_id)
        self._candidate_counter += 1
        authority_json, authority_sha = canonical_authority(primary_authority)
        identity = canonical_sha256(
            {
                "world_id": turn.world_id,
                "branch_id": turn.branch_id,
                "scene_id": turn.scene_id,
                "generation": head.generation + 1,
                "source_sha256": text_sha256(turn.exact_user_source),
                "primary_authority_sha256": authority_sha,
                "route": route.value,
                "candidate_counter": self._candidate_counter,
            }
        )
        candidate_id = f"candidate-{identity[:28]}"
        turn_id = f"turn-{head.generation + 1:04d}-{text_sha256(turn.exact_user_source)[:12]}"
        accepted_records = self.store.recent_accepted_payloads(
            world_id=turn.world_id,
            branch_id=turn.branch_id,
            adult_full=route is SceneRoute.ADULT,
        )
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
                current_state=turn.current_state,
                characters=turn.characters,
                relationships=turn.relationships,
                recent_prose=turn.recent_prose,
                relevant_memories=turn.relevant_memories,
                voice_examples=turn.voice_examples,
                craft_index=turn.craft_index,
                accepted_records=accepted_records,
            )
        )
        prompt = (
            "Use the confined Writer view to produce the complete scene now. "
            "Read the manifest and exact primary authority before writing."
        )
        if feedback:
            prompt += (
                " Produce a fresh complete replacement while considering this "
                f"noncanonical creator guidance: {feedback}"
            )
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
        pi_result = self.pi.invoke(
            PiSceneInvocationV1(
                route=route,
                purpose="writer",
                view=view,
                prompt=prompt,
                candidate_id=candidate_id,
                session_dir=self._session_dir(turn.world_id, turn.branch_id),
                accepted_parent_session=accepted_session,
                force_rehydrate=force_rehydrate,
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
            primary_authority_kind=(
                "codex_sequence" if route is SceneRoute.ORDINARY else "adult_handoff"
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
        )
        review_id = f"review-{candidate.candidate_sha256[:28]}"
        review = LeanReviewRecordV1(
            review_id=review_id,
            turn_input=turn,
            result=result,
            pi_session_id=pi_result.session_id,
            pi_session_dir=pi_result.session_dir,
        )
        self._reviews[review_id] = review
        self._unresolved_by_branch[(turn.world_id, turn.branch_id)] = review_id
        return review

    def _record(self, review: LeanReviewRecordV1) -> LeanRecordingAttemptV1:
        accepted = review.accepted_receipt
        if accepted is None:
            raise StateConflictError("Recorder cannot run before phase-one acceptance")
        attempt_number = (
            1
            if review.recording_attempt is None
            else review.recording_attempt.attempt_number + 1
        )
        accepted_records = self.store.recent_accepted_payloads(
            world_id=accepted.world_id,
            branch_id=accepted.branch_id,
            adult_full=accepted.route is SceneRoute.ADULT,
        )
        current_records = tuple(
            value
            for value in accepted_records
            if value.get("receipt", {}).get("accepted_turn_id")
            == accepted.accepted_turn_id
        )
        if len(current_records) != 1:
            raise StateConflictError("Recorder view did not resolve one accepted turn")
        view = self.writer_views.materialize(
            WriterViewInputV1(
                world_id=accepted.world_id,
                branch_id=accepted.branch_id,
                scene_id=accepted.scene_id,
                turn_id=accepted.accepted_turn_id,
                candidate_id=f"record-{accepted.accepted_turn_id}-{attempt_number}",
                route=accepted.route,
                user_prompt=accepted.exact_user_source,
                primary_authority=json.loads(accepted.primary_authority_json),
                current_state=review.turn_input.current_state,
                characters=review.turn_input.characters,
                relationships=review.turn_input.relationships,
                recent_prose=(accepted.exact_accepted_prose,),
                relevant_memories=review.turn_input.relevant_memories,
                voice_examples=review.turn_input.voice_examples,
                craft_index=review.turn_input.craft_index,
                accepted_records=current_records,
            )
        )
        prompt = (
            "Record the sole accepted_records/0001.json turn using the exact "
            "recent_prose/0001.txt prose. "
            "Return only the route-specific semantic record fields; Python binds custody."
        )
        if review.recording_attempt is not None:
            prompt += (
                " This is an explicit recording repair after typed failure "
                f"{review.recording_attempt.failure_code}; return a fresh complete record."
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
        fault = (
            None
            if self._recording_fault_injector is None
            else self._recording_fault_injector(accepted, attempt_number)
        )
        if fault is not None:
            return self.store.mark_recording_failure(
                accepted,
                recorder_request_sha256=invocation.writer_receipt.request_sha256,
                recorder_output_sha256=invocation.writer_receipt.output_sha256,
                provider_operations=invocation.writer_receipt.provider_operations,
                failure_code=fault,
            )
        try:
            payload = _parse_recorder_payload(invocation.output_text)
            if accepted.route is SceneRoute.ORDINARY:
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
                primary = json.loads(accepted.primary_authority_json)
                if not isinstance(primary, dict):
                    raise ContractValidationError("ordinary primary authority is invalid")
                items = primary.get("items")
                if not isinstance(items, list):
                    raise ContractValidationError("ordinary primary authority shape changed")
                resulting_public_state = payload["resulting_public_state"]
                if not isinstance(resulting_public_state, str) or not resulting_public_state.strip():
                    raise ContractValidationError("resulting_public_state must be non-empty text")
                record = ordinary_record_from_mapping(
                    {
                        "schema_version": "cera.pi_scene.ordinary_record.v1",
                        "primary_sequence_sha256": accepted.primary_authority_sha256,
                        "realized_item_keys": [value["item_key"] for value in items],
                        "secondary_canon": _string_array(
                            payload["secondary_canon"], "secondary_canon"
                        ),
                        "resulting_public_state": resulting_public_state,
                        "relationship_changes": _string_array(
                            payload["relationship_changes"], "relationship_changes"
                        ),
                        "knowledge_changes": _string_array(
                            payload["knowledge_changes"], "knowledge_changes"
                        ),
                        "durable_changes": _string_array(
                            payload["durable_changes"], "durable_changes"
                        ),
                        "unresolved_threads": _string_array(
                            payload["unresolved_threads"], "unresolved_threads"
                        ),
                    }
                )
                return self.store.attach_ordinary_record(
                    accepted,
                    record,
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
                {"decision_path", "events", "resulting_public_state", "unresolved_threads"},
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
        except (KeyError, TypeError, ValueError, json.JSONDecodeError, ContractValidationError) as exc:
            return self.store.mark_recording_failure(
                accepted,
                recorder_request_sha256=invocation.writer_receipt.request_sha256,
                recorder_output_sha256=invocation.writer_receipt.output_sha256,
                provider_operations=invocation.writer_receipt.provider_operations,
                failure_code=f"recorder_contract:{type(exc).__name__}",
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


def _pi_operation_count(pi: object) -> int:
    ledger = getattr(pi, "operation_ledger", None)
    value = getattr(ledger, "operation_count", 0)
    return value if type(value) is int and value >= 0 else 0


def _parse_recorder_payload(output_text: str) -> dict[str, Any]:
    """Decode raw JSON or one exact JSON fence without semantic repair."""

    value = output_text.strip()
    if value.startswith("```json\n") and value.endswith("\n```"):
        value = value[len("```json\n") : -len("\n```")].strip()
    payload = json.loads(value)
    if not isinstance(payload, dict):
        raise ContractValidationError("Recorder result is not a JSON object")
    return payload
