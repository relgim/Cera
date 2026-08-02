"""Test-only Continuous V3 bridge for the real SillyTavern HTTP contract.

This module deliberately owns no provider or story logic.  It serializes the
frozen three-turn fixture onto a supplied Continuous V3 campaign harness and
keeps creator acceptance as a separate HTTP decision.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from threading import Lock
from typing import Callable, Mapping

from cera.creator_review import CreatorReviewAction
from cera.errors import ContractValidationError, StateConflictError
from cera.ids import IdKind, TypedId, deterministic_id

from .models import (
    CERA_CONTINUOUS_V3_TEST_MODEL,
    SillyTavernChatRequest,
    SillyTavernTurnReply,
)


CONTINUOUS_V3_TEST_FIXTURE = (
    "Hello, my name is Ted. Is this the Hanezawa residence?",
    "I'm the tenant who was supposed to arrive today.",
    'Several days later, Ted is in the kitchen with Mia and asks, "Is Sakura always that cautious with visitors?"',
)


class ContinuousTestReviewState(str, Enum):
    REVIEW_READY = "review_ready"
    ACCEPTED = "accepted"


@dataclass(frozen=True, slots=True)
class PreparedContinuousTestTurn:
    turn_number: int
    turn_id: str
    candidate_text: str
    candidate_sha256: str
    sequence_beats: tuple[str, ...]
    validator_package_sha256: str
    provider_calls: int
    accept_allowed: bool

    def __post_init__(self) -> None:
        if self.turn_number not in {1, 2, 3}:
            raise ContractValidationError("continuous test turn number is invalid")
        if not all(
            isinstance(value, str) and value.strip()
            for value in (
                self.turn_id,
                self.candidate_text,
                self.candidate_sha256,
                self.validator_package_sha256,
            )
        ):
            raise ContractValidationError("continuous test candidate is incomplete")
        expected_calls = 4 if self.turn_number == 3 else 3
        if self.provider_calls != expected_calls or type(self.accept_allowed) is not bool:
            raise ContractValidationError("continuous test candidate call/accept contract changed")


@dataclass(frozen=True, slots=True)
class AcceptedContinuousTestTurn:
    turn_number: int
    artifact_id: str
    generation: int
    promotion_receipt_sha256: str


@dataclass(frozen=True, slots=True)
class ContinuousTestReviewRecord:
    review_id: TypedId
    session_id: str
    prepared: PreparedContinuousTestTurn
    state: ContinuousTestReviewState
    creator_action: CreatorReviewAction | None = None
    accepted: AcceptedContinuousTestTurn | None = None


class ContinuousSillyTavernTestAdapter:
    """Fail-closed HTTP adapter for one disposable three-turn V3 run."""

    def __init__(
        self,
        *,
        session_id: str,
        prepare_turn: Callable[[int], PreparedContinuousTestTurn],
        accept_turn: Callable[[int], AcceptedContinuousTestTurn],
        route_identity: Mapping[str, object],
    ) -> None:
        if not session_id.strip():
            raise ContractValidationError("continuous test session identity is empty")
        self.session_id = session_id
        self._prepare_turn = prepare_turn
        self._accept_turn = accept_turn
        self._route_identity = dict(route_identity)
        self._reviews: dict[TypedId, ContinuousTestReviewRecord] = {}
        self._accepted_turns = 0
        self._lock = Lock()

    @property
    def reasoner_session_status(self) -> dict[str, object]:
        return {
            "mode": "continuous_v3_test",
            "active": True,
            **self._route_identity,
        }

    def complete(self, request: SillyTavernChatRequest) -> SillyTavernTurnReply:
        with self._lock:
            if request.model != CERA_CONTINUOUS_V3_TEST_MODEL:
                raise StateConflictError(
                    "continuous V3 test adapter rejects the legacy model"
                )
            if request.cera_session_id != self.session_id:
                raise StateConflictError("continuous V3 test session changed")
            if any(
                record.state is ContinuousTestReviewState.REVIEW_READY
                for record in self._reviews.values()
            ):
                raise StateConflictError(
                    "continuous V3 creator review is unresolved"
                )
            turn_number = self._accepted_turns + 1
            if turn_number > len(CONTINUOUS_V3_TEST_FIXTURE):
                raise StateConflictError("continuous V3 test run is complete")
            expected = CONTINUOUS_V3_TEST_FIXTURE[turn_number - 1]
            if request.latest_user_content != expected:
                raise StateConflictError("continuous V3 test fixture changed")
            if request.cera_scene_change != (turn_number == 3):
                raise StateConflictError("continuous V3 scene-change flag changed")
            prepared = self._prepare_turn(turn_number)
            if not prepared.accept_allowed:
                raise StateConflictError(
                    "continuous V3 candidate did not qualify for strict acceptance"
                )
            review_id = deterministic_id(
                IdKind.REVIEW_PACKET,
                "cera.sillytavern.continuous_v3_test_review.v1",
                (
                    f"{self.session_id}\x1f{prepared.turn_id}\x1f"
                    f"{prepared.candidate_sha256}"
                ),
            )
            if review_id in self._reviews:
                raise StateConflictError("continuous V3 review identity was reused")
            record = ContinuousTestReviewRecord(
                review_id=review_id,
                session_id=self.session_id,
                prepared=prepared,
                state=ContinuousTestReviewState.REVIEW_READY,
            )
            self._reviews[review_id] = record
            return SillyTavernTurnReply(
                prose=prepared.candidate_text,
                request_id=prepared.turn_id,
                artifact_id=None,
                generation=turn_number,
                provider_calls=prepared.provider_calls,
                exact_replay=False,
                provisional_review_id=str(review_id),
                candidate_id=prepared.candidate_sha256,
                review_status=record.state.value,
                route_kind="continuous_v3_test",
            )

    def get_review(self, review_id: TypedId) -> ContinuousTestReviewRecord:
        try:
            return self._reviews[review_id]
        except KeyError as exc:
            raise StateConflictError("unknown continuous V3 review") from exc

    def review_action(
        self,
        review_id: TypedId,
        action: CreatorReviewAction,
        *,
        feedback: str | None = None,
    ) -> ContinuousTestReviewRecord:
        with self._lock:
            record = self.get_review(review_id)
            if record.state is not ContinuousTestReviewState.REVIEW_READY:
                raise StateConflictError("continuous V3 review is already terminal")
            if action is not CreatorReviewAction.ACCEPT or feedback is not None:
                raise StateConflictError(
                    "continuous V3 qualification permits strict Accept only"
                )
            if not record.prepared.accept_allowed:
                raise StateConflictError("continuous V3 Accept is not eligible")
            accepted = self._accept_turn(record.prepared.turn_number)
            if accepted.turn_number != record.prepared.turn_number:
                raise StateConflictError("continuous V3 acceptance turn changed")
            terminal = replace(
                record,
                state=ContinuousTestReviewState.ACCEPTED,
                creator_action=CreatorReviewAction.ACCEPT,
                accepted=accepted,
            )
            self._reviews[review_id] = terminal
            self._accepted_turns += 1
            return terminal

    def review_payload(self, record: ContinuousTestReviewRecord) -> dict[str, object]:
        return {
            "schema_version": "cera.sillytavern_continuous_v3_review.v1",
            "review_id": str(record.review_id),
            "state": record.state.value,
            "provisional": record.state is ContinuousTestReviewState.REVIEW_READY,
            "candidate_text": record.prepared.candidate_text,
            "candidate_sha256": record.prepared.candidate_sha256,
            "candidate_text_sha256": record.prepared.candidate_sha256,
            "sequence_plan": list(record.prepared.sequence_beats),
            "sequence_plan_sha256": record.prepared.validator_package_sha256,
            "speaker_marks": [],
            "assessment": {
                "severity": "good",
                "publication_eligibility": "accept_allowed",
            },
            "accept_enabled": (
                record.state is ContinuousTestReviewState.REVIEW_READY
                and record.prepared.accept_allowed
            ),
            "prepared_package_id": record.prepared.validator_package_sha256,
            "creator_action": (
                record.creator_action.value if record.creator_action is not None else None
            ),
            "feedback_required": False,
        }

    def review_decision_payload(
        self,
        review_id: TypedId,
        action: CreatorReviewAction,
        record: ContinuousTestReviewRecord,
    ) -> dict[str, object]:
        if (
            action is not CreatorReviewAction.ACCEPT
            or record.accepted is None
            or record.review_id != review_id
        ):
            raise StateConflictError("continuous V3 decision result changed")
        return {
            "status": "accepted",
            "creator_action": action.value,
            "review_id": str(review_id),
            "artifact_id": record.accepted.artifact_id,
            "generation": record.accepted.generation,
            "provider_calls": 0,
            "automatic_retries": 0,
            "promotion_receipt_sha256": record.accepted.promotion_receipt_sha256,
        }
