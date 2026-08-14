from __future__ import annotations

import json
import tempfile
import unittest
from copy import deepcopy
from dataclasses import dataclass, replace
from pathlib import Path
from threading import Barrier, Event, Thread
from time import monotonic
from types import SimpleNamespace
from unittest.mock import patch

from cera.errors import ContractValidationError, StateConflictError
from cera.pi_scene.http import PiSceneHttpAdapter
from cera.pi_scene.http_contracts import (
    PI_SCENE_ORDINARY_MODEL,
    PI_SCENE_PROFILE,
    LeanSceneRequestControlsV3,
)
from cera.pi_scene.ordinary_http import ordinary_review_payload
from cera.pi_scene.ordinary_rejection_policy import (
    tolerated_ordinary_rejection_reasons,
)
from cera.pi_scene.review_lifecycle import (
    OrdinaryReviewPhase,
    OrdinaryValidationInputBindingV1,
    OrdinaryValidationOwner,
)
from cera.pi_scene.review_store import LeanReviewState
from cera.pi_scene.runtime import (
    LeanPiSceneCoordinator,
    OrdinaryWriterCandidateOccurrenceV1,
    ProviderStageRetryPendingError,
    _writer_prompt,
)
from cera.pi_scene.store import LeanSceneStore
from cera.pi_scene.writer_view import WriterViewMaterializer
from cera.reader_validation import (
    BoundReaderValidationV1,
    ReaderIssueV1,
    ReaderStatus,
    ReaderVerdictV1,
    RetryFeedbackScope,
)
from cera.semantic_validation import (
    BoundSemanticValidationV1,
    SemanticConflictClass,
    SemanticConflictV1,
    SemanticValidationVerdictV1,
    SemanticVerdict,
)
from cera.serialization import canonical_sha256, text_sha256, to_primitive
from tests.test_pi_scene_lean_v1 import FakePi, turn
from tests.test_pi_scene_semantic_runtime import _CognitionPlanner

_GENERIC_WRITER_PROMPT = (
    "Call context exactly once, use its complete confined Writer view, "
    "then produce the complete scene now without another tool call."
)
_REPAIR_WHOLE_SCENE_AUDIT = (
    "The cited conflict is the repair target, not a replacement for any other authority "
    "in the unchanged Writer view. Produce a fresh complete scene from the unchanged Writer "
    "view; do not quote, patch, or continue the rejected prose. Before returning, silently "
    "re-audit the entire fresh scene against every ordered surface_realization_item: each "
    "required action must actually occur rather than be promised, intended, summarized, or "
    "deferred; every required communication must preserve its authorized speaker, addressee, "
    "and channel; preserve every cast or capability restriction and every continuously held "
    "boundary without a temporary breach; and make the required final beat the output's final "
    "sentence or paragraph, with no aftermath, waiting, ambience, summary, restatement, or "
    "other content after it."
)


class _UnusedReader:
    def validate(self, _request: object, _custody: object) -> object:
        raise AssertionError("generic lifecycle bypassed its prepared Reader lane")


@dataclass(frozen=True)
class _PreparedLane:
    lane: str
    request: object
    custody: object
    scope: object
    session_id: str
    workspace: str


class _LifecycleRetryPort:
    """Provider-free lifecycle double exercising the coordinator's real futures."""

    def __init__(
        self,
        planner: _CognitionPlanner,
        pi: FakePi,
        *,
        luna: SemanticVerdict = SemanticVerdict.PASS,
        reader: ReaderStatus = ReaderStatus.ACCEPTED,
    ) -> None:
        self.planner = planner
        self.pi = pi
        self.luna_verdict = luna
        self.reader_status = reader
        self.prepare_order: list[str] = []
        self.dispatch_order: list[str] = []
        self.prepared: dict[str, _PreparedLane] = {}
        self.prepared_by_chain: dict[str, _PreparedLane] = {}
        self.results: dict[str, object] = {}
        self.results_frozen = Event()
        self.validation_barrier = Barrier(2)
        self.entered = {"luna": Event(), "reader": Event()}
        self.release_reader = Event()
        self.release_reader.set()
        self.fail_lane: str | set[str] | None = None
        self.writer_occurrences = 0
        self.next_provider_operations = {"luna": 1, "reader": 1}
        self.provider_operations_by_chain: dict[str, int] = {}

    def run_planner(self, request: object, **_kwargs: object) -> object:
        return self.planner.plan(request)

    def reserve_writer_candidate_occurrence(
        self,
        *,
        world_id: str,
        branch_id: str,
        generation: int,
    ) -> OrdinaryWriterCandidateOccurrenceV1:
        self.writer_occurrences += 1
        return OrdinaryWriterCandidateOccurrenceV1(
            stage_ordinal=self.writer_occurrences,
            occurrence_sha256=canonical_sha256(
                {
                    "world_id": world_id,
                    "branch_id": branch_id,
                    "generation": generation,
                    "ordinal": self.writer_occurrences,
                }
            ),
        )

    def run_writer(self, invocation: object, **_kwargs: object) -> object:
        return self.pi.invoke(invocation)

    def run_recorder(self, invocation: object, **_kwargs: object) -> object:
        return self.pi.invoke(invocation)

    def bind_recorder_pending(self, **_kwargs: object) -> None:
        return None

    @staticmethod
    def _chain(lane: str, request: object, custody: object) -> str:
        return "stage-retry-" + canonical_sha256(
            {"lane": lane, "request": request, "custody": custody}
        )

    def _prepare(self, lane: str, request: object, custody: object) -> _PreparedLane:
        self.prepare_order.append(lane)
        chain_id = self._chain(lane, request, custody)
        prepared = _PreparedLane(
            lane=lane,
            request=request,
            custody=custody,
            scope=SimpleNamespace(identity=SimpleNamespace(chain_id=chain_id)),
            session_id=f"session:{lane}:fresh",
            workspace=f"workspace/{lane}",
        )
        self.prepared[lane] = prepared
        self.prepared_by_chain[chain_id] = prepared
        self.provider_operations_by_chain[chain_id] = self.next_provider_operations[lane]
        return prepared

    def status(self, chain_id: str) -> dict[str, object]:
        prepared = self.prepared_by_chain.get(chain_id)
        if prepared is None:
            raise StateConflictError("test validation chain is unavailable")
        succeeded = chain_id in self.results
        operations = self.provider_operations_by_chain[chain_id] if succeeded else 0
        stage_attempts = max(1, operations)
        retry_actions = stage_attempts - 1
        digest = canonical_sha256({"chain_id": chain_id, "lane": prepared.lane})
        return {
            "schema_version": "cera.provider_stage_retry_status_envelope.v1",
            "status": {
                "schema_version": "cera.provider_stage_retry_status.v1",
                "chain_id": chain_id,
                "provider": "codex",
                "model_family": "luna" if prepared.lane == "luna" else "sol",
                "stage": ("semantic_validator" if prepared.lane == "luna" else "reader"),
                "state": "succeeded" if succeeded else "in_progress",
                "maximum_attempts": 3,
                "stage_attempts_total": stage_attempts,
                "retry_actions_accepted": retry_actions,
                "provider_operations_observed_total": operations,
                "provider_operations_conservative_total": operations,
                "story_state_committed": False,
                "branch_preserved_at_last_accepted_head": True,
                "failure_category": None,
                "available_actions": [],
                "technical_details": {
                    "schema_version": "cera.provider_stage_retry_technical_details.v1",
                    "request_occurrence_sha256": digest,
                    "request_sha256": canonical_sha256(prepared.request),
                    "stage_input_sha256": canonical_sha256(
                        {"request": prepared.request, "custody": prepared.custody}
                    ),
                    "accepted_state_sha256": canonical_sha256(
                        prepared.custody.accepted_head_sha256
                    ),
                    "chain_sha256": canonical_sha256(
                        {"chain_id": chain_id, "succeeded": succeeded, "operations": operations}
                    ),
                },
            },
            "actions": [],
        }

    def prepare_semantic_validator(
        self,
        request: object,
        custody: object,
        **_kwargs: object,
    ) -> _PreparedLane:
        return self._prepare("luna", request, custody)

    def prepare_reader(
        self,
        request: object,
        custody: object,
        **_kwargs: object,
    ) -> _PreparedLane:
        return self._prepare("reader", request, custody)

    def _enter(self, prepared: _PreparedLane) -> None:
        if self.prepare_order[-2:] != ["luna", "reader"]:
            raise AssertionError("a validator dispatched before both inputs were prepared")
        self.dispatch_order.append(prepared.lane)
        self.validation_barrier.wait(timeout=5)
        self.entered[prepared.lane].set()
        if prepared.lane == "reader":
            if not self.release_reader.wait(timeout=5):
                raise AssertionError("test did not release the Reader lane")
        if self.fail_lane == prepared.lane or (
            isinstance(self.fail_lane, set) and prepared.lane in self.fail_lane
        ):
            raise RuntimeError("injected validation runtime failure")

    def dispatch_prepared_semantic_validator(
        self,
        prepared: _PreparedLane,
    ) -> BoundSemanticValidationV1:
        self._enter(prepared)
        conflict = (
            None
            if self.luna_verdict is SemanticVerdict.PASS
            else SemanticConflictV1(
                conflict_class=SemanticConflictClass.OMITTED_DECISION,
                concise_explanation="The candidate omitted one planned decision.",
                exact_quote=None,
                decision_key="sakura_door_response",
            )
        )
        result = BoundSemanticValidationV1(
            request=prepared.request,
            custody=prepared.custody,
            verdict=SemanticValidationVerdictV1(
                schema_version=SemanticValidationVerdictV1.SCHEMA_VERSION,
                verdict=self.luna_verdict,
                conflict=conflict,
            ),
        )
        self.results[prepared.scope.identity.chain_id] = result
        if len(self.results) == 2:
            self.results_frozen.set()
        return result

    def dispatch_prepared_reader(
        self,
        prepared: _PreparedLane,
    ) -> BoundReaderValidationV1:
        self._enter(prepared)
        issues = (
            ()
            if self.reader_status is ReaderStatus.ACCEPTED
            else (
                ReaderIssueV1(
                    issue_code="severe_quality_failure",
                    concise_explanation="The candidate has a severe continuity failure.",
                ),
            )
        )
        result = BoundReaderValidationV1(
            request=prepared.request,
            custody=prepared.custody,
            verdict=ReaderVerdictV1(status=self.reader_status, issues=issues),
        )
        self.results[prepared.scope.identity.chain_id] = result
        if len(self.results) == 2:
            self.results_frozen.set()
        return result

    def run_semantic_validator(self, request: object, custody: object, **_kwargs: object) -> object:
        return self.dispatch_prepared_semantic_validator(self._prepare("luna", request, custody))

    def reconcile_semantic_validator(self, chain_id: str) -> object:
        return self.results.get(chain_id, self.status(chain_id))

    def reconcile_reader(self, chain_id: str) -> object:
        return self.results.get(chain_id, self.status(chain_id))


class _ActiveRecorderReviewPort:
    """Review-level double proving an active chain blocks sibling repair."""

    def __init__(self) -> None:
        self.resolutions = 0
        self.bind_attempts = 0
        chain_id = "stage-retry-" + text_sha256("active-recorder-review")
        chain_sha256 = text_sha256(f"{chain_id}:1:0:eligible")
        action = {
            "schema_version": "cera.provider_stage_retry_action.v1",
            "action_id": "stage-action-" + text_sha256(f"{chain_id}:1"),
            "chain_id": chain_id,
            "action_family": "provider_stage_control",
            "action_kind": "provider_retry",
            "automatic": False,
            "provider_dispatch_authorized": True,
            "consumes_retry_action": True,
            "retry_action_ordinal": 1,
            "whole_request_replay_authorized": False,
            "provider_substitution_authorized": False,
            "expected_chain_sha256": chain_sha256,
        }
        self.envelope = {
            "schema_version": "cera.provider_stage_retry_status_envelope.v1",
            "status": {
                "schema_version": "cera.provider_stage_retry_status.v1",
                "chain_id": chain_id,
                "provider": "deepseek",
                "model_family": "deepseek_v4",
                "stage": "recorder",
                "state": "eligible",
                "maximum_attempts": 3,
                "stage_attempts_total": 1,
                "retry_actions_accepted": 0,
                "provider_operations_observed_total": 2,
                "provider_operations_conservative_total": 2,
                "story_state_committed": True,
                "branch_preserved_at_last_accepted_head": True,
                "failure_category": "provider_output_invalid",
                "available_actions": ["provider_retry"],
                "technical_details": {
                    "schema_version": "cera.provider_stage_retry_technical_details.v1",
                    "request_occurrence_sha256": "1" * 64,
                    "request_sha256": "2" * 64,
                    "stage_input_sha256": "3" * 64,
                    "accepted_state_sha256": "4" * 64,
                    "chain_sha256": chain_sha256,
                },
            },
            "actions": [action],
        }

    def recorder_review_resolution(self, **_kwargs: object) -> object:
        self.resolutions += 1
        return SimpleNamespace(kind="chain_active", envelope=self.envelope)

    def bind_review_action(self, **_kwargs: object) -> object:
        self.bind_attempts += 1
        raise AssertionError("active Recorder chain minted a sibling action")


class _HttpReviewCustody:
    """Minimal protected HTTP shell; provider stages remain on the real coordinator port."""

    def __init__(self, request_id: str) -> None:
        self.request_id = request_id

    def branch_barrier(self, **_kwargs: object) -> None:
        return None

    def load_terminal_response_optional(self, _request_id: str) -> None:
        return None

    def bind_review_request(self, review: object) -> object:
        return SimpleNamespace(
            request_id=self.request_id,
            review_id=review.review_id,
        )

    def bind_terminal_response(self, **kwargs: object) -> object:
        response = kwargs["response"]
        return SimpleNamespace(response_sha256=canonical_sha256(response))

    def recorder_review_resolution(self, **_kwargs: object) -> object:
        return SimpleNamespace(kind="initial_start")


class _ExactTerminalResponseCustody:
    def __init__(self, review_id: str, response: dict[str, object]) -> None:
        self.review_id = review_id
        self.response = response

    def reconcile_finalized_review_action_response_for_review_optional(
        self,
        review_id: str,
    ) -> tuple[dict[str, object], object] | None:
        if review_id != self.review_id:
            return None
        return deepcopy(self.response), SimpleNamespace(
            response_sha256=canonical_sha256(self.response)
        )


def _controls(mode: str = "automatic") -> LeanSceneRequestControlsV3:
    return LeanSceneRequestControlsV3(
        schema_version=LeanSceneRequestControlsV3.SCHEMA_VERSION,
        session_id="lifecycle-test",
        review_mode=mode,
    )


def _turn(*, mode: str = "automatic", source: str = "Continue the scene."):
    value = turn(source=source)
    return replace(
        value,
        characters={
            **value.characters,
            "character:sakura_hanezawa": {
                "name": "Sakura",
                "role": "resident",
                "voice": "Direct, grounded, and concise.",
            },
        },
        request_controls=_controls(mode),
    )


def _keys(value: object) -> set[str]:
    if isinstance(value, dict):
        return {str(key) for key in value} | {
            nested for item in value.values() for nested in _keys(item)
        }
    if isinstance(value, (list, tuple)):
        return {nested for item in value for nested in _keys(item)}
    return set()


class PiSceneProvisionalReviewLifecycleTests(unittest.TestCase):
    def _runtime(
        self,
        root: Path,
        *,
        luna: SemanticVerdict = SemanticVerdict.PASS,
        reader: ReaderStatus = ReaderStatus.ACCEPTED,
    ) -> tuple[LeanPiSceneCoordinator, LeanSceneStore, FakePi, _LifecycleRetryPort]:
        store = LeanSceneStore(root / "world")
        planner = _CognitionPlanner()
        pi = FakePi()
        retry = _LifecycleRetryPort(planner, pi, luna=luna, reader=reader)
        coordinator = LeanPiSceneCoordinator(
            store=store,
            planner=planner,
            writer_views=WriterViewMaterializer(root / "views"),
            pi=pi,  # type: ignore[arg-type]
            session_root=root / "sessions",
            reader_validator=_UnusedReader(),  # type: ignore[arg-type]
            ordinary_stage_retry=retry,  # type: ignore[arg-type]
        )
        return coordinator, store, pi, retry

    @staticmethod
    def _adapter(coordinator: LeanPiSceneCoordinator) -> PiSceneHttpAdapter:
        return PiSceneHttpAdapter(
            coordinator=coordinator,
            session_id="lifecycle-test",
            context_provider=lambda *_args, **_kwargs: _turn(),
        )

    @staticmethod
    def _recovered_lane_result(
        retry: _LifecycleRetryPort,
        lane: str,
    ) -> BoundSemanticValidationV1 | BoundReaderValidationV1:
        prepared = retry.prepared[lane]
        if lane == OrdinaryValidationOwner.LUNA.value:
            return BoundSemanticValidationV1(
                request=prepared.request,
                custody=prepared.custody,
                verdict=SemanticValidationVerdictV1(
                    schema_version=SemanticValidationVerdictV1.SCHEMA_VERSION,
                    verdict=SemanticVerdict.REJECT,
                    conflict=SemanticConflictV1(
                        conflict_class=SemanticConflictClass.OMITTED_DECISION,
                        concise_explanation="The candidate omitted one planned decision.",
                        exact_quote=None,
                        decision_key="sakura_door_response",
                    ),
                ),
            )
        return BoundReaderValidationV1(
            request=prepared.request,
            custody=prepared.custody,
            verdict=ReaderVerdictV1(status=ReaderStatus.ACCEPTED, issues=()),
        )

    def test_frozen_payload_returns_while_both_real_futures_are_joined(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            coordinator, store, pi, retry = self._runtime(Path(temporary))
            retry.release_reader.clear()
            frozen = coordinator.start_ordinary(_turn())
            payload = PiSceneHttpAdapter._completion_payload(frozen)
            self.assertEqual(
                payload["choices"][0]["message"]["content"], frozen.candidate.story_text
            )
            self.assertEqual(payload["cera"]["review_lifecycle"]["state"], "checks_pending")

            result: list[object] = []
            worker = Thread(
                target=lambda: result.append(
                    coordinator.begin_ordinary_validation(frozen.review_id)
                )
            )
            worker.start()
            self.assertTrue(retry.entered["luna"].wait(timeout=5))
            self.assertTrue(retry.entered["reader"].wait(timeout=5))
            self.assertEqual(retry.prepare_order, ["luna", "reader"])
            current = coordinator.get_review(frozen.review_id, reconcile=False)
            self.assertIs(current.review_phase, OrdinaryReviewPhase.VALIDATING)
            self.assertEqual(
                store.load_head(world_id="world-test", branch_id="branch-main").generation, 0
            )
            self.assertEqual(result, [])

            luna = retry.prepared["luna"]
            reader = retry.prepared["reader"]
            forbidden = {
                "semantic_validation",
                "reader_validation",
                "luna_verdict",
                "reader_verdict",
            }
            self.assertTrue(forbidden.isdisjoint(_keys(to_primitive(luna.request))))
            self.assertTrue(forbidden.isdisjoint(_keys(to_primitive(reader.request))))
            self.assertEqual(luna.custody.candidate_id, reader.custody.candidate_id)
            self.assertEqual(luna.custody.accepted_head_sha256, reader.custody.accepted_head_sha256)
            self.assertEqual(reader.custody.candidate_sha256, frozen.candidate.candidate_sha256)
            self.assertNotEqual(luna.scope.identity.chain_id, reader.scope.identity.chain_id)
            self.assertNotEqual(luna.session_id, reader.session_id)
            self.assertNotEqual(luna.workspace, reader.workspace)

            retry.release_reader.set()
            worker.join(timeout=5)
            self.assertFalse(worker.is_alive())
            accepted = result[0]
            self.assertEqual(accepted.state, "accepted")
            self.assertIsNotNone(accepted.python_qualification)
            self.assertEqual(retry.dispatch_order.count("luna"), 1)
            self.assertEqual(retry.dispatch_order.count("reader"), 1)
            self.assertEqual([call.purpose for call in pi.calls], ["writer", "recorder"])
            replay = coordinator.begin_ordinary_validation(frozen.review_id)
            self.assertEqual(replay, accepted)

    def test_known_luna_reject_stays_nonactionable_until_reader_joins(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            coordinator, _store, pi, retry = self._runtime(
                Path(temporary),
                luna=SemanticVerdict.REJECT,
            )
            retry.release_reader.clear()
            frozen = coordinator.start_ordinary(_turn())
            result: list[object] = []
            worker = Thread(
                target=lambda: result.append(
                    coordinator.begin_ordinary_validation(frozen.review_id)
                )
            )
            worker.start()
            self.assertTrue(retry.entered["luna"].wait(timeout=5))
            self.assertTrue(retry.entered["reader"].wait(timeout=5))
            deadline = monotonic() + 5
            while monotonic() < deadline:
                partial = coordinator.get_review(frozen.review_id, reconcile=False)
                if partial.semantic_validation is not None:
                    break
                Event().wait(0.01)
            else:
                self.fail("Luna rejection was not durably visible before Reader joined")
            public = ordinary_review_payload(
                partial,
                recording_status=None,
                provider_attempts=(partial,),
            )
            self.assertEqual(public["state"], "checks_pending")
            self.assertEqual(public["gate_status"], "reject")
            self.assertEqual(public["checks"]["luna"]["status"], "reject")
            self.assertTrue(public["checks"]["luna"]["failures"])
            self.assertEqual(public["checks"]["reader"]["status"], "pending")
            self.assertFalse(
                any(
                    public["actions"][name]
                    for name in (
                        "accept_enabled",
                        "regenerate_enabled",
                        "decline_enabled",
                        "replan_enabled",
                        "auditable_override_enabled",
                        "repair_recording_enabled",
                    )
                )
            )
            self.assertEqual(result, [])
            self.assertEqual([call.purpose for call in pi.calls], ["writer"])

            retry.release_reader.set()
            worker.join(timeout=5)
            self.assertFalse(worker.is_alive())
            accepted = result[0]
            self.assertIs(accepted.review_phase, OrdinaryReviewPhase.VALIDATION_REJECTED)
            self.assertEqual(accepted.state, LeanReviewState.ACCEPTED)
            joined = self._adapter(coordinator).review_payload(accepted)
            self.assertEqual(joined["state"], "accepted")
            self.assertEqual(joined["gate_status"], "reject")
            self.assertEqual(joined["acceptance"]["mode"], "standing_policy")
            self.assertEqual(joined["acceptance"]["canon_status"], "provisional")
            self.assertIsNotNone(joined["acceptance"]["standing_policy"])
            self.assertFalse(joined["actions"]["regenerate_enabled"])
            self.assertTrue(joined["checks"]["luna"]["failures"])
            self.assertEqual([call.purpose for call in pi.calls], ["writer", "recorder"])

    def test_standing_policy_exact_soft_allowlists_are_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            coordinator, _store, _pi, _retry = self._runtime(
                Path(temporary),
                luna=SemanticVerdict.REJECT,
            )
            frozen = coordinator.start_ordinary(_turn())
            accepted = coordinator.begin_ordinary_validation(frozen.review_id)
            semantic = accepted.semantic_validation
            reader = accepted.reader_validation
            self.assertIsNotNone(semantic)
            self.assertIsNotNone(reader)
            assert semantic is not None and reader is not None
            soft = {
                SemanticConflictClass.OMITTED_DECISION,
                SemanticConflictClass.PRESENCE_VIOLATION,
                SemanticConflictClass.STOPPING_BOUNDARY,
                SemanticConflictClass.CAPABILITY_RESTRICTION,
            }
            for conflict_class in SemanticConflictClass:
                with self.subTest(conflict_class=conflict_class.value):
                    bound = replace(
                        semantic,
                        verdict=SemanticValidationVerdictV1(
                            schema_version=SemanticValidationVerdictV1.SCHEMA_VERSION,
                            verdict=SemanticVerdict.REJECT,
                            conflict=SemanticConflictV1(
                                conflict_class=conflict_class,
                                concise_explanation="Typed policy classification fixture.",
                                exact_quote=None,
                                decision_key=semantic.verdict.conflict.decision_key,
                            ),
                        ),
                    )
                    reasons = tolerated_ordinary_rejection_reasons(bound, reader)
                    if conflict_class in soft:
                        self.assertEqual(reasons, (f"luna:{conflict_class.value}",))
                    else:
                        self.assertIsNone(reasons)

            for scope in RetryFeedbackScope:
                with self.subTest(reader_scope=scope.value):
                    issue = ReaderIssueV1(
                        issue_code="policy_fixture",
                        concise_explanation="Typed Reader policy fixture.",
                        exact_quote=(
                            reader.request.exact_candidate_prose[:12]
                            if scope is RetryFeedbackScope.EXACT_QUOTE
                            else None
                        ),
                        omitted_planner_item_key=(
                            reader.request.current_plan_item_keys[0]
                            if scope is RetryFeedbackScope.OMITTED_PLANNER_ITEM
                            else None
                        ),
                        feedback_scope=scope,
                    )
                    rejected_reader = replace(
                        reader,
                        verdict=ReaderVerdictV1(
                            status=ReaderStatus.REJECTED,
                            issues=(issue,),
                        ),
                    )
                    passing_semantic = replace(
                        semantic,
                        verdict=SemanticValidationVerdictV1(
                            schema_version=SemanticValidationVerdictV1.SCHEMA_VERSION,
                            verdict=SemanticVerdict.PASS,
                            conflict=None,
                        ),
                    )
                    reasons = tolerated_ordinary_rejection_reasons(
                        passing_semantic,
                        rejected_reader,
                    )
                    if scope is RetryFeedbackScope.WHOLE_CANDIDATE_QUALITY:
                        self.assertIsNone(reasons)
                    else:
                        self.assertEqual(reasons, (f"reader:{scope.value}",))

    def test_standing_policy_soft_rejection_does_not_wait_in_manual_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            coordinator, _store, pi, _retry = self._runtime(
                Path(temporary),
                luna=SemanticVerdict.REJECT,
            )
            frozen = coordinator.start_ordinary(_turn(mode="manual"))
            accepted = coordinator.begin_ordinary_validation(frozen.review_id)
            public = self._adapter(coordinator).review_payload(accepted)

            self.assertEqual(accepted.state, LeanReviewState.ACCEPTED)
            self.assertEqual(public["review_mode"], "manual")
            self.assertEqual(public["gate_status"], "reject")
            self.assertEqual(public["acceptance"]["mode"], "standing_policy")
            self.assertEqual(public["acceptance"]["canon_status"], "provisional")
            self.assertFalse(any(public["actions"].values()))
            self.assertEqual([call.purpose for call in pi.calls], ["writer", "recorder"])

    def test_python_privacy_gate_rebuilds_the_safe_reader_projection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            coordinator, _store, _pi, _retry = self._runtime(
                Path(temporary),
                reader=ReaderStatus.REJECTED,
            )
            frozen = coordinator.start_ordinary(_turn())
            review = coordinator.begin_ordinary_validation(frozen.review_id)
            reader = review.reader_validation
            binding = review.validation_input_binding
            self.assertIsNotNone(reader)
            self.assertIsNotNone(binding)
            assert reader is not None
            assert binding is not None

            tampered_request = replace(
                reader.request,
                current_public_state="PRIVATE_SENTINEL_MUST_NOT_REACH_READER",
            )
            tampered_custody = replace(
                reader.custody,
                validation_request_sha256=canonical_sha256(tampered_request),
            )
            tampered_reader = BoundReaderValidationV1(
                request=tampered_request,
                custody=tampered_custody,
                verdict=reader.verdict,
            )
            tampered_binding = OrdinaryValidationInputBindingV1.create(
                candidate_sha256=binding.candidate_sha256,
                semantic_request_sha256=binding.semantic_request_sha256,
                semantic_custody_sha256=binding.semantic_custody_sha256,
                reader_request_sha256=canonical_sha256(tampered_request),
                reader_custody_sha256=canonical_sha256(tampered_custody),
                luna_chain_id=binding.luna_chain_id,
                reader_chain_id=binding.reader_chain_id,
            )
            tampered = replace(
                review,
                review_phase=OrdinaryReviewPhase.VALIDATING,
                reader_validation=tampered_reader,
                validation_input_binding=tampered_binding,
                python_qualification=None,
            )
            with self.assertRaisesRegex(
                ContractValidationError,
                "safe accepted-context projection",
            ):
                coordinator._qualify_ordinary_review(tampered)

    def test_real_http_complete_returns_prose_before_background_checks_finish(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            coordinator, _store, pi, retry = self._runtime(Path(temporary))
            retry.release_reader.clear()
            payload = {
                "model": PI_SCENE_ORDINARY_MODEL,
                "messages": [{"role": "user", "content": "Continue the scene."}],
                "stream": False,
                "cera_profile_id": PI_SCENE_PROFILE,
                "cera_session_id": "lifecycle-test",
                "cera_review_mode": "automatic",
            }
            adapter = self._adapter(coordinator)
            _, _, binding = adapter._prepare_bound_request(payload)
            adapter.ordinary_stage_retry_runtime = _HttpReviewCustody(  # type: ignore[assignment]
                binding.request_id
            )

            response = adapter.complete(payload)
            review_id = response["cera"]["provisional_review_id"]
            self.assertIsInstance(review_id, str)
            frozen = coordinator.get_review(review_id, reconcile=False)
            self.assertEqual(
                response["choices"][0]["message"]["content"],
                frozen.candidate.story_text,
            )
            self.assertEqual(response["cera"]["review_lifecycle"]["state"], "checks_pending")
            self.assertTrue(retry.entered["luna"].wait(timeout=5))
            self.assertTrue(retry.entered["reader"].wait(timeout=5))
            pending = adapter.get_review(review_id)
            self.assertEqual(pending["state"], "checks_pending")
            self.assertIsNone(pending["acceptance"])
            retry.release_reader.set()

            deadline = monotonic() + 5
            while monotonic() < deadline:
                with adapter._background_review_lock:
                    if review_id not in adapter._background_review_ids:
                        break
                Event().wait(0.01)
            with adapter._background_review_lock:
                self.assertNotIn(review_id, adapter._background_review_ids)
            accepted = adapter.get_review(review_id)
            self.assertEqual(accepted["state"], "accepted")
            self.assertEqual(accepted["acceptance"]["mode"], "automatic")
            self.assertEqual([call.purpose for call in pi.calls], ["writer", "recorder"])

    def test_reader_reject_finishes_both_checks_and_requires_feedback_bound_override(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            coordinator, _store, pi, retry = self._runtime(
                Path(temporary), reader=ReaderStatus.REJECTED
            )
            frozen = coordinator.start_ordinary(_turn())
            rejected = coordinator.begin_ordinary_validation(frozen.review_id)
            self.assertIs(rejected.review_phase, OrdinaryReviewPhase.VALIDATION_REJECTED)
            self.assertIsNotNone(rejected.semantic_validation)
            self.assertIsNotNone(rejected.reader_validation)
            self.assertIsNotNone(rejected.python_qualification)
            self.assertEqual(set(retry.dispatch_order), {"luna", "reader"})
            self.assertEqual([call.purpose for call in pi.calls], ["writer"])

            adapter = self._adapter(coordinator)
            public = adapter.review_payload(rejected)
            self.assertEqual(public["gate_status"], "reject")
            self.assertTrue(public["actions"]["auditable_override_enabled"])
            self.assertEqual(public["story_text"], frozen.candidate.story_text)
            with self.assertRaisesRegex(ContractValidationError, "nonempty"):
                adapter.decide(
                    rejected.review_id,
                    {"action": "accept_provisional", "feedback": "   "},
                )

            feedback = "Creator accepts this exact rejected candidate for a bounded test."
            accepted = adapter.decide(
                rejected.review_id,
                {"action": "accept_provisional", "feedback": feedback},
            )
            self.assertEqual(accepted["review"]["acceptance"]["mode"], "auditable_override")
            self.assertEqual(accepted["review"]["gate_status"], "reject")
            self.assertEqual(accepted["review"]["acceptance"]["canon_status"], "provisional")
            self.assertNotIn(feedback, json.dumps(accepted, sort_keys=True))
            self.assertEqual(
                adapter.decide(
                    rejected.review_id,
                    {"action": "accept_provisional", "feedback": feedback},
                ),
                accepted,
            )
            with self.assertRaisesRegex(StateConflictError, "request changed"):
                adapter.decide(
                    rejected.review_id,
                    {"action": "accept_provisional", "feedback": feedback + " changed"},
                )

            terminal = adapter.terminal_decision_payload(rejected.review_id)
            link = terminal["review"]["terminal_decision"]
            detached = json.loads(json.dumps(terminal))
            detached["review"]["terminal_decision"] = None
            self.assertEqual(link["decision_sha256"], canonical_sha256(detached))
            self.assertEqual(
                link["url"],
                f"/v1/cera/reviews/{rejected.review_id}/terminal-decision",
            )
            self.assertEqual(adapter.terminal_decision_payload(rejected.review_id), terminal)

    def test_override_crash_recovers_exact_feedback_bound_decision_hash(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            coordinator, store, _pi, _retry = self._runtime(
                root,
                reader=ReaderStatus.REJECTED,
            )
            frozen = coordinator.start_ordinary(_turn())
            rejected = coordinator.begin_ordinary_validation(frozen.review_id)
            feedback = "Creator accepts this exact rejected candidate after review."
            with patch.object(
                coordinator,
                "_persist_review_state",
                side_effect=OSError("injected lost post-Accept snapshot"),
            ):
                accepted = coordinator.accept(
                    rejected.review_id,
                    acceptance_action="provisional_accept",
                    override_feedback=feedback,
                    dispatch_recorder=False,
                )
            replay = coordinator.terminal_decision_replay(rejected.review_id)
            self.assertIsNotNone(replay)
            assert replay is not None
            receipt = accepted.review.accepted_receipt
            self.assertIsNotNone(receipt)
            assert receipt is not None
            audit = store.load_acceptance_decision_audit(receipt)
            self.assertIsNotNone(audit)
            assert audit is not None
            self.assertEqual(audit.decision_request_sha256, replay.request_sha256)
            self.assertEqual(audit.override_feedback_sha256, text_sha256(feedback))
            self.assertNotIn(feedback, json.dumps(to_primitive(audit), sort_keys=True))
            feedback_bytes = feedback.encode("utf-8")
            for artifact in root.rglob("*"):
                if artifact.is_file():
                    self.assertNotIn(
                        feedback_bytes,
                        artifact.read_bytes(),
                        f"raw override feedback escaped into {artifact.relative_to(root)}",
                    )

            restarted, _store, restarted_pi, _retry = self._runtime(
                root,
                reader=ReaderStatus.REJECTED,
            )
            recovered = restarted.terminal_decision_replay(rejected.review_id)
            self.assertIsNotNone(recovered)
            assert recovered is not None
            self.assertEqual(recovered.action, "accept_provisional")
            self.assertEqual(recovered.request_sha256, replay.request_sha256)
            self.assertEqual(
                restarted.accept(
                    rejected.review_id,
                    allow_replay=True,
                    acceptance_action="provisional_accept",
                    override_feedback=feedback,
                    dispatch_recorder=False,
                ),
                recovered.result,
            )
            with self.assertRaisesRegex(StateConflictError, "request changed"):
                restarted.accept(
                    rejected.review_id,
                    allow_replay=True,
                    acceptance_action="provisional_accept",
                    override_feedback=feedback + " changed",
                    dispatch_recorder=False,
                )
            self.assertEqual(restarted_pi.calls, [])

    def test_override_selection_crash_rejects_changed_feedback_before_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            coordinator, store, _pi, _retry = self._runtime(
                root,
                reader=ReaderStatus.REJECTED,
            )
            frozen = coordinator.start_ordinary(_turn())
            rejected = coordinator.begin_ordinary_validation(frozen.review_id)
            feedback_a = "Creator accepts the exact rejected candidate after review."
            feedback_b = "Creator supplies a different override after the crash."

            with patch.object(
                store,
                "_select_receipt",
                side_effect=OSError("injected lost active-lineage selection"),
            ):
                with self.assertRaisesRegex(OSError, "active-lineage selection"):
                    coordinator.accept(
                        rejected.review_id,
                        acceptance_action="provisional_accept",
                        override_feedback=feedback_a,
                        dispatch_recorder=False,
                    )

            with self.assertRaisesRegex(
                StateConflictError,
                "accepted decision audit differs from replay",
            ):
                coordinator.accept(
                    rejected.review_id,
                    acceptance_action="provisional_accept",
                    override_feedback=feedback_b,
                    dispatch_recorder=False,
                )

            recovered = coordinator.accept(
                rejected.review_id,
                acceptance_action="provisional_accept",
                override_feedback=feedback_a,
                dispatch_recorder=False,
            )
            replay = coordinator.terminal_decision_replay(rejected.review_id)
            self.assertIsNotNone(replay)
            assert replay is not None
            receipt = recovered.review.accepted_receipt
            self.assertIsNotNone(receipt)
            assert receipt is not None
            audit = store.load_acceptance_decision_audit(receipt)
            self.assertIsNotNone(audit)
            assert audit is not None
            self.assertEqual(audit.override_feedback_sha256, text_sha256(feedback_a))
            self.assertEqual(audit.decision_request_sha256, replay.request_sha256)

    def test_manual_pass_authorizes_accept_decline_regenerate_but_blocks_new_turn(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            coordinator, _store, pi, _retry = self._runtime(Path(temporary))
            frozen = coordinator.start_ordinary(_turn(mode="manual"))
            review = coordinator.begin_ordinary_validation(frozen.review_id)
            self.assertIs(review.review_phase, OrdinaryReviewPhase.AWAITING_MANUAL_ACCEPT)
            public = self._adapter(coordinator).review_payload(review)
            self.assertEqual(public["gate_status"], "pass")
            self.assertEqual(
                {
                    key: public["actions"][key]
                    for key in (
                        "accept_enabled",
                        "regenerate_enabled",
                        "decline_enabled",
                        "replan_enabled",
                        "auditable_override_enabled",
                    )
                },
                {
                    "accept_enabled": True,
                    "regenerate_enabled": True,
                    "decline_enabled": True,
                    "replan_enabled": False,
                    "auditable_override_enabled": False,
                },
            )
            self.assertEqual([call.purpose for call in pi.calls], ["writer"])
            with self.assertRaisesRegex(StateConflictError, "unresolved|explicit lifecycle"):
                coordinator.start_ordinary(_turn(source="A new user turn must remain blocked."))
            accepted = coordinator.accept(review.review_id, acceptance_action="accept")
            self.assertEqual(accepted.review.state, "accepted")
            self.assertEqual([call.purpose for call in pi.calls], ["writer", "recorder"])

    def test_soft_omitted_decision_does_not_call_a_second_writer(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            coordinator, _store, pi, retry = self._runtime(
                Path(temporary),
                luna=SemanticVerdict.REJECT,
            )
            frozen = coordinator.start_ordinary(_turn(mode="manual"))
            accepted = coordinator.begin_ordinary_validation(frozen.review_id)
            validation = accepted.semantic_validation
            self.assertIsNotNone(validation)
            assert validation is not None

            self.assertEqual(accepted.state, LeanReviewState.ACCEPTED)
            public = self._adapter(coordinator).review_payload(accepted)
            self.assertEqual(public["acceptance"]["mode"], "standing_policy")
            self.assertFalse(public["actions"]["regenerate_enabled"])
            with self.assertRaisesRegex(StateConflictError, "already terminal"):
                coordinator.regenerate(accepted.review_id)
            self.assertEqual(retry.planner.calls, 1)
            self.assertEqual([call.purpose for call in pi.calls], ["writer", "recorder"])
            self.assertEqual(retry.writer_occurrences, 1)

    def test_manual_pass_and_reader_only_regenerate_keep_generic_writer_prompt(self) -> None:
        self.assertEqual(_writer_prompt(None), _GENERIC_WRITER_PROMPT)
        self.assertNotIn(_REPAIR_WHOLE_SCENE_AUDIT, _writer_prompt(None))
        cases = (
            ("manual_pass", ReaderStatus.ACCEPTED),
            ("reader_only_reject", ReaderStatus.REJECTED),
        )
        for label, reader_status in cases:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temporary:
                coordinator, _store, pi, retry = self._runtime(
                    Path(temporary),
                    luna=SemanticVerdict.PASS,
                    reader=reader_status,
                )
                frozen = coordinator.start_ordinary(_turn(mode="manual"))
                review = coordinator.begin_ordinary_validation(frozen.review_id)

                with patch.object(
                    coordinator,
                    "_prepare_review",
                    wraps=coordinator._prepare_review,
                ) as prepare:
                    regenerated = coordinator.regenerate(review.review_id)

                self.assertIsNotNone(regenerated.successor)
                self.assertIsNone(prepare.call_args.kwargs["repair_validation"])
                self.assertEqual(pi.calls[-1].prompt, _GENERIC_WRITER_PROMPT)
                self.assertNotIn(_REPAIR_WHOLE_SCENE_AUDIT, pi.calls[-1].prompt)
                self.assertEqual(retry.planner.calls, 1)
                self.assertEqual([call.purpose for call in pi.calls], ["writer", "writer"])
                self.assertEqual(retry.writer_occurrences, 2)

    def test_writer_repair_prompt_supports_quote_and_decision_anchors(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            coordinator, _store, pi, _retry = self._runtime(
                Path(temporary),
                luna=SemanticVerdict.REJECT,
            )
            pi.writer_outputs.append("ROOT_T_QUOTE_SENTINEL")
            frozen = coordinator.start_ordinary(_turn(mode="manual"))
            rejected = coordinator.begin_ordinary_validation(frozen.review_id)
            validation = rejected.semantic_validation
            self.assertIsNotNone(validation)
            assert validation is not None

            quote_validation = BoundSemanticValidationV1(
                request=validation.request,
                custody=validation.custody,
                verdict=SemanticValidationVerdictV1(
                    schema_version=SemanticValidationVerdictV1.SCHEMA_VERSION,
                    verdict=SemanticVerdict.REJECT,
                    conflict=SemanticConflictV1(
                        conflict_class=SemanticConflictClass.PROTECTED_USER_DIALOGUE,
                        concise_explanation=(
                            "The candidate indirectly attributed dialogue to Ted."
                        ),
                        exact_quote="ROOT_T_QUOTE_SENTINEL",
                        decision_key=None,
                    ),
                ),
            )
            quote_prompt = _writer_prompt(quote_validation)
            self.assertIn("rejected for protected_user_dialogue", quote_prompt)
            self.assertIn(
                'the exact candidate phrase "ROOT_T_QUOTE_SENTINEL"',
                quote_prompt,
            )
            self.assertIn(
                "The candidate indirectly attributed dialogue to Ted.",
                quote_prompt,
            )
            self.assertEqual(quote_prompt.count(_REPAIR_WHOLE_SCENE_AUDIT), 1)
            self.assertTrue(quote_prompt.endswith(_REPAIR_WHOLE_SCENE_AUDIT))

            decision_key = "sakura_verify_before_access"
            plan = validation.request.cognition_plan
            decision_plan = replace(
                plan,
                decision_records=tuple(
                    replace(record, decision_key=decision_key) for record in plan.decision_records
                ),
                decision_item_links=tuple(
                    replace(link, decision_key=decision_key) for link in plan.decision_item_links
                ),
            )
            decision_request = replace(
                validation.request,
                cognition_plan=decision_plan,
            )
            decision_custody = replace(
                validation.custody,
                cognition_plan_sha256=canonical_sha256(decision_plan),
                validation_request_sha256=canonical_sha256(decision_request),
            )
            decision_validation = BoundSemanticValidationV1(
                request=decision_request,
                custody=decision_custody,
                verdict=SemanticValidationVerdictV1(
                    schema_version=SemanticValidationVerdictV1.SCHEMA_VERSION,
                    verdict=SemanticVerdict.REJECT,
                    conflict=SemanticConflictV1(
                        conflict_class=SemanticConflictClass.CONTRADICTED_DECISION,
                        concise_explanation=(
                            "The candidate weakened the keep-door-closed decision."
                        ),
                        exact_quote=None,
                        decision_key=decision_key,
                    ),
                ),
            )
            decision_prompt = _writer_prompt(decision_validation)
            self.assertIn("rejected for contradicted_decision", decision_prompt)
            self.assertIn(f"decision {decision_key}", decision_prompt)
            self.assertIn(
                "The candidate weakened the keep-door-closed decision.",
                decision_prompt,
            )
            self.assertEqual(decision_prompt.count(_REPAIR_WHOLE_SCENE_AUDIT), 1)
            self.assertTrue(decision_prompt.endswith(_REPAIR_WHOLE_SCENE_AUDIT))

    def test_pending_and_technical_blocked_states_reject_all_semantic_actions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            coordinator, _store, _pi, retry = self._runtime(Path(temporary))
            pending = coordinator.start_ordinary(_turn())
            for action in (
                lambda: coordinator.decline(pending.review_id),
                lambda: coordinator.regenerate(pending.review_id),
                lambda: coordinator.replan(pending.review_id),
                lambda: coordinator.accept(pending.review_id, acceptance_action="accept"),
            ):
                with self.assertRaises(StateConflictError):
                    action()

            retry.fail_lane = "reader"
            blocked = coordinator.begin_ordinary_validation(pending.review_id)
            self.assertIs(blocked.review_phase, OrdinaryReviewPhase.VALIDATION_BLOCKED)
            public = ordinary_review_payload(
                blocked,
                recording_status=None,
                provider_attempts=(blocked,),
            )
            self.assertEqual(public["gate_status"], "blocked")
            self.assertFalse(
                any(
                    public["actions"][key]
                    for key in (
                        "accept_enabled",
                        "regenerate_enabled",
                        "decline_enabled",
                        "replan_enabled",
                        "auditable_override_enabled",
                    )
                )
            )
            for action in (
                lambda: coordinator.decline(blocked.review_id),
                lambda: coordinator.regenerate(blocked.review_id),
                lambda: coordinator.replan(blocked.review_id),
                lambda: coordinator.accept(
                    blocked.review_id,
                    acceptance_action="provisional_accept",
                    override_feedback="Not authorized while Python is blocked.",
                ),
            ):
                with self.assertRaises(StateConflictError):
                    action()

    def test_manual_lane_resume_atomically_clears_the_last_blocking_failure(self) -> None:
        for failed_lane in (
            OrdinaryValidationOwner.LUNA.value,
            OrdinaryValidationOwner.READER.value,
        ):
            with self.subTest(failed_lane=failed_lane), tempfile.TemporaryDirectory() as temporary:
                coordinator, _store, pi, retry = self._runtime(
                    Path(temporary),
                    luna=SemanticVerdict.REJECT,
                )
                retry.fail_lane = failed_lane
                frozen = coordinator.start_ordinary(_turn())
                blocked = coordinator.begin_ordinary_validation(frozen.review_id)
                self.assertIs(blocked.review_phase, OrdinaryReviewPhase.VALIDATION_BLOCKED)
                self.assertEqual(
                    tuple(failure.owner.value for failure in blocked.validation_failures),
                    (failed_lane,),
                )

                result = self._recovered_lane_result(retry, failed_lane)
                chain_id = retry.prepared[failed_lane].scope.identity.chain_id
                recovered = coordinator.resume_ordinary_validation_lane(
                    chain_id=chain_id,
                    lane=failed_lane,
                    result=result,
                )

                self.assertIs(
                    recovered.review_phase,
                    OrdinaryReviewPhase.VALIDATION_REJECTED,
                )
                self.assertEqual(recovered.state, LeanReviewState.ACCEPTED)
                self.assertEqual(recovered.validation_failures, ())
                self.assertIsNone(recovered.luna_provider_stage_retry_status)
                self.assertIsNone(recovered.reader_provider_stage_retry_status)
                self.assertIsNotNone(recovered.semantic_validation)
                self.assertIsNotNone(recovered.reader_validation)
                self.assertIsNotNone(recovered.python_qualification)
                self.assertEqual([call.purpose for call in pi.calls], ["writer"])
                self.assertEqual(
                    coordinator.resume_ordinary_validation_lane(
                        chain_id=chain_id,
                        lane=failed_lane,
                        result=result,
                    ),
                    recovered,
                )

    def test_restart_reconciliation_atomically_clears_the_last_lane_failure(self) -> None:
        for failed_lane in (
            OrdinaryValidationOwner.LUNA.value,
            OrdinaryValidationOwner.READER.value,
        ):
            with self.subTest(failed_lane=failed_lane), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                coordinator, _store, pi, retry = self._runtime(
                    root,
                    luna=SemanticVerdict.REJECT,
                )
                retry.fail_lane = failed_lane
                frozen = coordinator.start_ordinary(_turn())
                blocked = coordinator.begin_ordinary_validation(frozen.review_id)
                self.assertIs(blocked.review_phase, OrdinaryReviewPhase.VALIDATION_BLOCKED)

                result = self._recovered_lane_result(retry, failed_lane)
                chain_id = retry.prepared[failed_lane].scope.identity.chain_id
                retry.results[chain_id] = result
                dispatch_order = tuple(retry.dispatch_order)
                restarted, _store, restarted_pi, _unused_retry = self._runtime(
                    root,
                    luna=SemanticVerdict.REJECT,
                )
                restarted.ordinary_stage_retry = retry  # type: ignore[assignment]

                recovered = restarted.reconcile_ordinary_review(frozen.review_id)

                self.assertIs(
                    recovered.review_phase,
                    OrdinaryReviewPhase.VALIDATION_REJECTED,
                )
                self.assertEqual(recovered.state, LeanReviewState.ACCEPTED)
                self.assertEqual(recovered.validation_failures, ())
                self.assertIsNone(recovered.luna_provider_stage_retry_status)
                self.assertIsNone(recovered.reader_provider_stage_retry_status)
                self.assertIsNotNone(recovered.semantic_validation)
                self.assertIsNotNone(recovered.reader_validation)
                self.assertIsNotNone(recovered.python_qualification)
                self.assertEqual(tuple(retry.dispatch_order), dispatch_order)
                self.assertEqual(restarted_pi.calls, [])
                self.assertEqual([call.purpose for call in pi.calls], ["writer"])

    def test_lane_recovery_preserves_a_remaining_peer_failure(self) -> None:
        for recovery_path in ("resume", "restart"):
            for recovered_lane in (
                OrdinaryValidationOwner.LUNA.value,
                OrdinaryValidationOwner.READER.value,
            ):
                with (
                    self.subTest(
                        recovery_path=recovery_path,
                        recovered_lane=recovered_lane,
                    ),
                    tempfile.TemporaryDirectory() as temporary,
                ):
                    root = Path(temporary)
                    coordinator, _store, pi, retry = self._runtime(
                        root,
                        luna=SemanticVerdict.REJECT,
                    )
                    retry.fail_lane = {
                        OrdinaryValidationOwner.LUNA.value,
                        OrdinaryValidationOwner.READER.value,
                    }
                    frozen = coordinator.start_ordinary(_turn())
                    blocked = coordinator.begin_ordinary_validation(frozen.review_id)
                    self.assertEqual(
                        {failure.owner.value for failure in blocked.validation_failures},
                        {
                            OrdinaryValidationOwner.LUNA.value,
                            OrdinaryValidationOwner.READER.value,
                        },
                    )

                    result = self._recovered_lane_result(retry, recovered_lane)
                    chain_id = retry.prepared[recovered_lane].scope.identity.chain_id
                    dispatch_order = tuple(retry.dispatch_order)
                    restarted_pi = None
                    if recovery_path == "resume":
                        active = coordinator
                        recovered = active.resume_ordinary_validation_lane(
                            chain_id=chain_id,
                            lane=recovered_lane,
                            result=result,
                        )
                        replay = active.resume_ordinary_validation_lane(
                            chain_id=chain_id,
                            lane=recovered_lane,
                            result=result,
                        )
                    else:
                        retry.results[chain_id] = result
                        active, _store, restarted_pi, _unused_retry = self._runtime(
                            root,
                            luna=SemanticVerdict.REJECT,
                        )
                        active.ordinary_stage_retry = retry  # type: ignore[assignment]
                        recovered = active.reconcile_ordinary_review(frozen.review_id)
                        replay = active.reconcile_ordinary_review(frozen.review_id)

                    peer_lane = (
                        OrdinaryValidationOwner.READER.value
                        if recovered_lane == OrdinaryValidationOwner.LUNA.value
                        else OrdinaryValidationOwner.LUNA.value
                    )
                    self.assertEqual(replay, recovered)
                    self.assertIs(
                        recovered.review_phase,
                        OrdinaryReviewPhase.VALIDATION_BLOCKED,
                    )
                    self.assertEqual(
                        tuple(failure.owner.value for failure in recovered.validation_failures),
                        (peer_lane,),
                    )
                    self.assertIsNone(recovered.python_qualification)
                    if recovered_lane == OrdinaryValidationOwner.LUNA.value:
                        self.assertEqual(recovered.semantic_validation, result)
                        self.assertIsNone(recovered.reader_validation)
                        self.assertIsNone(recovered.luna_provider_stage_retry_status)
                    else:
                        self.assertEqual(recovered.reader_validation, result)
                        self.assertIsNone(recovered.semantic_validation)
                        self.assertIsNone(recovered.reader_provider_stage_retry_status)
                    self.assertEqual(tuple(retry.dispatch_order), dispatch_order)
                    self.assertEqual([call.purpose for call in pi.calls], ["writer"])
                    if restarted_pi is not None:
                        self.assertEqual(restarted_pi.calls, [])

    def test_v2_creator_guidance_is_hash_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            coordinator, _store, _pi, _retry = self._runtime(Path(temporary))
            frozen = coordinator.start_ordinary(_turn(mode="manual"))
            manual = coordinator.begin_ordinary_validation(frozen.review_id)
            feedback = "Use a quieter cadence without changing the planned event."
            decision = self._adapter(coordinator).decide(
                manual.review_id,
                {"action": "regenerate", "feedback": feedback},
            )
            encoded = json.dumps(decision, sort_keys=True)
            self.assertNotIn(feedback, encoded)
            guidance = decision["successor"]["cera"]["creator_guidance"]
            self.assertEqual(
                guidance,
                {
                    "schema_version": "cera.pi_scene.creator_guidance_projection.v1",
                    "action": "regenerate",
                    "text_sha256": text_sha256(feedback),
                },
            )

    def test_terminal_regenerate_replays_exact_response_while_successor_validates(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            coordinator, _store, _pi, retry = self._runtime(Path(temporary))
            original = coordinator.start_ordinary(_turn(mode="manual"))
            predecessor = coordinator.begin_ordinary_validation(original.review_id)
            adapter = self._adapter(coordinator)
            decision = adapter.decide(
                predecessor.review_id,
                {"action": "regenerate", "feedback": None},
            )
            successor_id = decision["successor"]["cera"]["provisional_review_id"]
            adapter.ordinary_stage_retry_runtime = (  # type: ignore[assignment]
                _ExactTerminalResponseCustody(
                    predecessor.review_id,
                    decision,
                )
            )
            retry.entered["luna"].clear()
            retry.entered["reader"].clear()
            retry.release_reader.clear()
            failures: list[BaseException] = []

            def validate_successor() -> None:
                try:
                    coordinator.begin_ordinary_validation(successor_id)
                except BaseException as exc:  # pragma: no cover - asserted below
                    failures.append(exc)

            worker = Thread(target=validate_successor)
            worker.start()
            self.assertTrue(retry.entered["luna"].wait(timeout=5))
            self.assertTrue(retry.entered["reader"].wait(timeout=5))
            try:
                replay = coordinator.terminal_decision_replay(predecessor.review_id)
                self.assertIsNotNone(replay)
                assert replay is not None
                self.assertNotEqual(
                    replay.result.successor,
                    coordinator.get_review(successor_id, reconcile=False),
                )
                with self.assertRaisesRegex(
                    StateConflictError,
                    "response review is not durable",
                ):
                    adapter.decision_payload(replay.action, replay.result)
                self.assertEqual(
                    adapter.terminal_decision_payload(predecessor.review_id),
                    decision,
                )
            finally:
                retry.release_reader.set()
                worker.join(timeout=5)
            self.assertFalse(worker.is_alive())
            self.assertEqual(failures, [])

    def test_regenerate_http_accounting_aligns_each_candidate_retry_chain(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            coordinator, _store, _pi, retry = self._runtime(Path(temporary))
            original = coordinator.start_ordinary(_turn(mode="manual"))
            predecessor = coordinator.begin_ordinary_validation(original.review_id)
            retry.next_provider_operations["reader"] = 2

            adapter = self._adapter(coordinator)
            decision = adapter.decide(
                predecessor.review_id,
                {
                    "action": "regenerate",
                    "feedback": "Keep the exact plan and revise only the prose cadence.",
                },
            )
            successor_payload = decision["successor"]
            self.assertIsInstance(successor_payload, dict)
            successor_id = successor_payload["cera"]["provisional_review_id"]
            successor = coordinator.begin_ordinary_validation(successor_id)
            public = adapter.review_payload(successor)

            self.assertEqual(
                [attempt["candidate_id"] for attempt in public["provider_attempts"]],
                [predecessor.candidate.candidate_id, successor.candidate.candidate_id],
            )
            self.assertEqual(
                [attempt["provider_operations"] for attempt in public["provider_attempts"]],
                [
                    {"planner": 1, "writer": 1, "validator": 1, "reader": 1},
                    {"planner": 0, "writer": 1, "validator": 1, "reader": 2},
                ],
            )
            self.assertEqual(
                public["provider_operations"],
                {
                    "planner": 1,
                    "writer": 2,
                    "validator": 2,
                    "reader": 3,
                    "recorder": 0,
                },
            )
            predecessor_binding = predecessor.validation_input_binding
            successor_binding = successor.validation_input_binding
            self.assertIsNotNone(predecessor_binding)
            self.assertIsNotNone(successor_binding)
            assert predecessor_binding is not None
            assert successor_binding is not None
            self.assertNotEqual(
                predecessor_binding.reader_chain_id,
                successor_binding.reader_chain_id,
            )
            self.assertEqual(
                retry.status(successor_binding.reader_chain_id)["status"][
                    "provider_operations_observed_total"
                ],
                2,
            )

    def test_direct_repair_post_cannot_bypass_an_active_recorder_chain(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            coordinator, _store, pi, _retry = self._runtime(Path(temporary))
            frozen = coordinator.start_ordinary(_turn(mode="manual"))
            awaiting = coordinator.begin_ordinary_validation(frozen.review_id)
            accepted = coordinator.accept(
                awaiting.review_id,
                acceptance_action="accept",
                dispatch_recorder=False,
            ).review
            adapter = self._adapter(coordinator)
            recorder = _ActiveRecorderReviewPort()
            adapter.ordinary_stage_retry_runtime = recorder  # type: ignore[assignment]

            public = adapter.review_payload(accepted)
            self.assertFalse(public["actions"]["repair_recording_enabled"])
            with self.assertRaises(ProviderStageRetryPendingError) as pending:
                adapter.get_review(accepted.review_id)
            self.assertEqual(pending.exception.envelope, recorder.envelope)
            with self.assertRaisesRegex(StateConflictError, "exact generic recovery"):
                adapter.decide(
                    accepted.review_id,
                    {"action": "repair_recording"},
                )
            self.assertGreaterEqual(recorder.resolutions, 2)
            self.assertEqual(recorder.bind_attempts, 0)
            self.assertEqual([call.purpose for call in pi.calls], ["writer"])

    def test_poll_during_active_worker_cannot_steal_provider_free_finalization(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            coordinator, _store, pi, retry = self._runtime(Path(temporary))
            frozen = coordinator.start_ordinary(_turn())
            adapter = self._adapter(coordinator)
            record_entered = Event()
            release_record = Event()
            original = coordinator._record_validation_result

            def held_record(*args: object, **kwargs: object) -> object:
                record_entered.set()
                if not release_record.wait(timeout=5):
                    raise AssertionError("test did not release durable verdict recording")
                return original(*args, **kwargs)

            with adapter._background_review_lock:
                adapter._background_review_ids.add(frozen.review_id)
            with patch.object(
                coordinator,
                "_record_validation_result",
                side_effect=held_record,
            ):
                worker = Thread(
                    target=adapter._background_review_validation,
                    args=(frozen.review_id,),
                )
                worker.start()
                self.assertTrue(retry.results_frozen.wait(timeout=5))
                self.assertTrue(record_entered.wait(timeout=5))
                polled = adapter.get_review(frozen.review_id)
                self.assertEqual(polled["state"], "checks_pending")
                self.assertIsNone(polled["acceptance"])
                self.assertEqual(polled["story_text"], frozen.candidate.story_text)
                self.assertEqual([call.purpose for call in pi.calls], ["writer"])
                release_record.set()
                worker.join(timeout=5)
                self.assertFalse(worker.is_alive())

            accepted = adapter.get_review(frozen.review_id)
            self.assertEqual(accepted["state"], "accepted")
            self.assertEqual(accepted["acceptance"]["mode"], "automatic")
            self.assertEqual([call.purpose for call in pi.calls], ["writer", "recorder"])

    def test_two_provider_free_qualified_finalizers_accept_exactly_once(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            coordinator, store, pi, _retry = self._runtime(root)
            frozen = coordinator.start_ordinary(_turn())
            with patch.object(
                coordinator,
                "accept",
                side_effect=RuntimeError("stop after durable qualification"),
            ):
                with self.assertRaisesRegex(RuntimeError, "durable qualification"):
                    coordinator.begin_ordinary_validation(frozen.review_id)
            qualified = coordinator.get_review(frozen.review_id, reconcile=False)
            self.assertIs(qualified.review_phase, OrdinaryReviewPhase.QUALIFIED)

            start = Barrier(3)
            results: list[object] = []
            errors: list[BaseException] = []

            def finalize() -> None:
                try:
                    start.wait(timeout=5)
                    results.append(coordinator.reconcile_ordinary_review(frozen.review_id))
                except BaseException as exc:  # pragma: no cover - asserted below
                    errors.append(exc)

            workers = [Thread(target=finalize) for _ in range(2)]
            for worker in workers:
                worker.start()
            start.wait(timeout=5)
            for worker in workers:
                worker.join(timeout=5)
                self.assertFalse(worker.is_alive())
            self.assertEqual(errors, [])
            self.assertEqual(len(results), 2)
            receipts = {
                result.accepted_receipt.receipt_sha256
                for result in results
                if result.accepted_receipt is not None
            }
            self.assertEqual(len(receipts), 1)
            head = store.load_head(world_id="world-test", branch_id="branch-main")
            self.assertEqual(head.generation, 1)
            self.assertEqual(head.accepted_head_sha256, next(iter(receipts)))
            replay = coordinator.terminal_decision_replay(frozen.review_id)
            self.assertIsNotNone(replay)
            assert replay is not None
            self.assertEqual(replay.action, "automatic_accept")
            self.assertEqual([call.purpose for call in pi.calls], ["writer"])

            restarted, _store, restarted_pi, _retry = self._runtime(root)
            recovered = restarted.reconcile_ordinary_review(frozen.review_id)
            self.assertEqual(
                recovered.accepted_receipt.receipt_sha256,
                next(iter(receipts)),
            )
            restarted_replay = restarted.terminal_decision_replay(frozen.review_id)
            self.assertIsNotNone(restarted_replay)
            assert restarted_replay is not None
            self.assertEqual(restarted_replay.action, "automatic_accept")
            self.assertEqual(restarted_replay.request_sha256, replay.request_sha256)
            self.assertEqual(restarted_pi.calls, [])


if __name__ == "__main__":
    unittest.main()
