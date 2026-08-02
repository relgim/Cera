"""Durable creator-review boundary for arbitrary Continuous V3 test turns.

The adapter owns HTTP/session/review state only.  Planner, Composer, Validator,
scene-summary, persistence, and accepted-context synchronization remain owned by
the shared Continuous V3 coordinator supplied through the callbacks below.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
import json
import os
from pathlib import Path
from threading import Lock
from typing import Callable, Mapping

from cera.creator_review import CreatorReviewAction
from cera.creator_review.models import (
    CreatorReviewAssessment,
    CreatorReviewSeverity,
    PublicationEligibility,
    ReviewIssueOwner,
)
from cera.errors import ContractValidationError, StateConflictError
from cera.ids import IdKind, TypedId, deterministic_id
from cera.schema import from_mapping
from cera.serialization import (
    canonical_bytes,
    canonical_sha256,
    re_is_sha256,
    text_sha256,
    to_primitive,
)

from .models import (
    CERA_CONTINUOUS_V3_MANUAL_MODEL,
    SillyTavernChatRequest,
    SillyTavernTurnReply,
)


CONTINUOUS_V3_MANUAL_PROFILE_ID = "cera.continuous_v3.manual.v1"
CONTINUOUS_V3_MANUAL_PORT = 5114
CONTINUOUS_V3_MANUAL_SERVICE = "cera-sillytavern-continuous-v3-manual"


class ContinuousManualReviewState(StrEnum):
    REVIEW_READY = "review_ready"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class PreparedContinuousManualTurn:
    SCHEMA_VERSION = "cera.prepared_continuous_manual_turn.v1"

    schema_version: str
    turn_number: int
    turn_id: str
    scene_number: int
    scene_id: str
    scene_change: bool
    active_character_ids: tuple[str, ...]
    accepted_generation_after: int
    candidate_text: str
    candidate_sha256: str
    candidate_text_sha256: str
    sequence_beats: tuple[str, ...]
    sequence_plan_sha256: str
    validator_package_id: str
    validator_package_sha256: str
    validator_semantic_status: str
    assessment_schema_version: str
    assessment_severity: CreatorReviewSeverity
    assessment_publication_eligibility: PublicationEligibility
    assessment_issue_owner: ReviewIssueOwner
    assessment_reason_codes: tuple[str, ...]
    assessment_creator_reason: str
    assessment_verifier_status: str
    assessment_receipt_sha256: str
    provider_calls: int
    accept_allowed: bool
    raw_source_sha256: str
    conversation_sha256: str
    ingress_receipt_id: str
    ingress_receipt_sha256: str
    authority_context_sha256: str
    profile_id: str
    execution_identity_sha256: str
    process_instance_id: str

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("continuous manual prepared schema changed")
        if (
            self.turn_number < 1
            or self.scene_number < 1
            or self.accepted_generation_after < 1
            or type(self.scene_change) is not bool
        ):
            raise ContractValidationError("continuous manual turn identity is invalid")
        required = (
            self.turn_id,
            self.scene_id,
            self.candidate_text,
            self.candidate_sha256,
            self.candidate_text_sha256,
            self.sequence_plan_sha256,
            self.validator_package_id,
            self.validator_package_sha256,
            self.validator_semantic_status,
            self.assessment_schema_version,
            self.assessment_creator_reason,
            self.assessment_verifier_status,
            self.assessment_receipt_sha256,
            self.ingress_receipt_id,
            self.profile_id,
            self.process_instance_id,
        )
        if any(not isinstance(value, str) or not value.strip() for value in required):
            raise ContractValidationError("continuous manual candidate is incomplete")
        if not self.sequence_beats or any(not value.strip() for value in self.sequence_beats):
            raise ContractValidationError("continuous manual sequence plan is incomplete")
        if (
            not self.active_character_ids
            or len(set(self.active_character_ids)) != len(self.active_character_ids)
        ):
            raise ContractValidationError("continuous manual active cast is invalid")
        for character_id in self.active_character_ids:
            TypedId.parse(character_id, IdKind.CHARACTER)
        for value in (
            self.candidate_sha256,
            self.candidate_text_sha256,
            self.sequence_plan_sha256,
            self.validator_package_sha256,
            self.assessment_receipt_sha256,
            self.raw_source_sha256,
            self.conversation_sha256,
            self.ingress_receipt_sha256,
            self.authority_context_sha256,
            self.execution_identity_sha256,
        ):
            if not re_is_sha256(value):
                raise ContractValidationError("continuous manual review hash is invalid")
        if text_sha256(self.candidate_text) != self.candidate_text_sha256:
            raise ContractValidationError("continuous manual candidate prose hash changed")
        assessment = CreatorReviewAssessment(
            schema_version=self.assessment_schema_version,
            severity=self.assessment_severity,
            publication_eligibility=self.assessment_publication_eligibility,
            issue_owner=self.assessment_issue_owner,
            reason_codes=self.assessment_reason_codes,
            creator_reason=self.assessment_creator_reason,
            verifier_status=self.assessment_verifier_status,
        )
        if assessment.assessment_sha256 != self.assessment_receipt_sha256:
            raise ContractValidationError("continuous manual assessment receipt changed")
        expected_calls = 4 if self.scene_change else 3
        if self.provider_calls != expected_calls or type(self.accept_allowed) is not bool:
            raise ContractValidationError("continuous manual stage schedule changed")
        expected_accept = (
            self.validator_semantic_status == "accepted"
            and self.assessment_severity is CreatorReviewSeverity.GOOD
            and self.assessment_publication_eligibility
            is PublicationEligibility.ACCEPT_ALLOWED
        )
        if self.accept_allowed is not expected_accept:
            raise ContractValidationError(
                "continuous manual acceptance disagrees with Validator evidence"
            )

    @property
    def review_binding_sha256(self) -> str:
        return canonical_sha256(
            {
                "schema_version": self.schema_version,
                "turn_number": self.turn_number,
                "turn_id": self.turn_id,
                "scene_number": self.scene_number,
                "scene_id": self.scene_id,
                "scene_change": self.scene_change,
                "active_character_ids": self.active_character_ids,
                "accepted_generation_after": self.accepted_generation_after,
                "candidate_sha256": self.candidate_sha256,
                "candidate_text_sha256": self.candidate_text_sha256,
                "sequence_plan_sha256": self.sequence_plan_sha256,
                "validator_package_id": self.validator_package_id,
                "validator_package_sha256": self.validator_package_sha256,
                "validator_semantic_status": self.validator_semantic_status,
                "assessment_receipt_sha256": self.assessment_receipt_sha256,
                "raw_source_sha256": self.raw_source_sha256,
                "conversation_sha256": self.conversation_sha256,
                "ingress_receipt_id": self.ingress_receipt_id,
                "ingress_receipt_sha256": self.ingress_receipt_sha256,
                "authority_context_sha256": self.authority_context_sha256,
                "profile_id": self.profile_id,
                "execution_identity_sha256": self.execution_identity_sha256,
                "process_instance_id": self.process_instance_id,
                "accept_allowed": self.accept_allowed,
            }
        )


@dataclass(frozen=True, slots=True)
class AcceptedContinuousManualTurn:
    turn_number: int
    turn_id: str
    generation: int
    artifact_id: str
    promotion_receipt_sha256: str
    review_binding_sha256: str

    def __post_init__(self) -> None:
        if self.turn_number < 1 or self.generation < 1 or not self.turn_id.strip():
            raise ContractValidationError("continuous manual accepted identity is invalid")
        if not self.artifact_id.strip():
            raise ContractValidationError("continuous manual accepted artifact is empty")
        for value in (self.promotion_receipt_sha256, self.review_binding_sha256):
            if not re_is_sha256(value):
                raise ContractValidationError("continuous manual accepted hash is invalid")


@dataclass(frozen=True, slots=True)
class RejectedContinuousManualTurn:
    turn_number: int
    turn_id: str
    discard_receipt_sha256: str
    review_binding_sha256: str
    recovered_after_restart: bool

    def __post_init__(self) -> None:
        if self.turn_number < 1 or not self.turn_id.strip():
            raise ContractValidationError("continuous manual rejected identity is invalid")
        if type(self.recovered_after_restart) is not bool:
            raise ContractValidationError("continuous manual recovery flag is invalid")
        for value in (self.discard_receipt_sha256, self.review_binding_sha256):
            if not re_is_sha256(value):
                raise ContractValidationError("continuous manual rejected hash is invalid")


@dataclass(frozen=True, slots=True)
class ContinuousManualReviewRecord:
    SCHEMA_VERSION = "cera.continuous_manual_review_record.v1"

    schema_version: str
    review_id: TypedId
    session_id: str
    prepared: PreparedContinuousManualTurn
    state: ContinuousManualReviewState
    creator_action: CreatorReviewAction | None = None
    accepted: AcceptedContinuousManualTurn | None = None
    rejected: RejectedContinuousManualTurn | None = None
    feedback_sha256: str | None = None

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION or not self.session_id.strip():
            raise ContractValidationError("continuous manual review record is invalid")
        expected_review_id = deterministic_id(
            IdKind.REVIEW_PACKET,
            "cera.sillytavern.continuous_v3_manual_review.v1",
            (
                f"{self.session_id}\x1f{self.prepared.turn_id}\x1f"
                f"{self.prepared.review_binding_sha256}"
            ),
        )
        if self.review_id != expected_review_id:
            raise ContractValidationError("continuous manual review identity changed")
        if self.feedback_sha256 is not None and not re_is_sha256(self.feedback_sha256):
            raise ContractValidationError("continuous manual feedback hash is invalid")
        terminal = self.state is not ContinuousManualReviewState.REVIEW_READY
        if terminal != (self.creator_action is not None):
            raise ContractValidationError("continuous manual terminal action is inconsistent")
        if self.state is ContinuousManualReviewState.ACCEPTED:
            if (
                self.creator_action is not CreatorReviewAction.ACCEPT
                or self.accepted is None
                or self.rejected is not None
                or self.feedback_sha256 is not None
                or self.accepted.turn_number != self.prepared.turn_number
                or self.accepted.turn_id != self.prepared.turn_id
                or self.accepted.generation
                != self.prepared.accepted_generation_after
                or self.accepted.review_binding_sha256
                != self.prepared.review_binding_sha256
            ):
                raise ContractValidationError("continuous manual accepted record changed")
        elif self.state is ContinuousManualReviewState.REJECTED:
            if (
                self.creator_action is not CreatorReviewAction.DECLINE
                or self.rejected is None
                or self.accepted is not None
                or self.rejected.turn_number != self.prepared.turn_number
                or self.rejected.turn_id != self.prepared.turn_id
                or self.rejected.review_binding_sha256
                != self.prepared.review_binding_sha256
            ):
                raise ContractValidationError("continuous manual rejected record changed")
        elif (
            self.creator_action is not None
            or self.accepted is not None
            or self.rejected is not None
            or self.feedback_sha256 is not None
        ):
            raise ContractValidationError("continuous manual ready review is already terminal")


class ContinuousManualStateStore:
    """Self-hashed state plus immutable, hash-bound review records."""

    SCHEMA_VERSION = "cera.continuous_manual_state.v3"

    def __init__(
        self,
        root: Path,
        *,
        identity: Mapping[str, object],
    ) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.reviews_root = self.root / "reviews"
        self.reviews_root.mkdir(exist_ok=True)
        self.path = self.root / "MANUAL_STATE.json"
        self.identity = dict(identity)
        if self.path.exists():
            state = self.load()
            if state["identity"] != self.identity:
                raise StateConflictError("continuous manual state identity changed")
        else:
            self._write(
                {
                    "schema_version": self.SCHEMA_VERSION,
                    "identity": self.identity,
                    "revision": 0,
                    "next_turn_number": 1,
                    "accepted_turn_ids": [],
                    "scene_number": 1,
                    "current_scene_accepted_turn_ids": [],
                    "current_scene_character_ids": [],
                    "current_review_id": None,
                    "decision_intent": None,
                    "inflight_attempt": None,
                    "attempts": [],
                }
            )

    def load(self) -> dict[str, object]:
        value = json.loads(self.path.read_text(encoding="utf-8"))
        supplied = value.pop("state_sha256", None)
        expected_fields = {
            "schema_version",
            "identity",
            "revision",
            "next_turn_number",
            "accepted_turn_ids",
            "scene_number",
            "current_scene_accepted_turn_ids",
            "current_scene_character_ids",
            "current_review_id",
            "decision_intent",
            "inflight_attempt",
            "attempts",
        }
        accepted_turn_ids = value.get("accepted_turn_ids")
        current_scene_turn_ids = value.get("current_scene_accepted_turn_ids")
        current_scene_character_ids = value.get("current_scene_character_ids")
        attempts = value.get("attempts")
        inflight = value.get("inflight_attempt")
        if (
            set(value) != expected_fields
            or value.get("schema_version") != self.SCHEMA_VERSION
            or supplied != canonical_sha256(value)
            or not isinstance(value.get("identity"), dict)
            or type(value.get("revision")) is not int
            or int(value["revision"]) < 0
            or type(value.get("next_turn_number")) is not int
            or int(value["next_turn_number"]) < 1
            or type(value.get("scene_number")) is not int
            or int(value["scene_number"]) < 1
            or not isinstance(accepted_turn_ids, list)
            or not isinstance(current_scene_turn_ids, list)
            or not isinstance(current_scene_character_ids, list)
            or not isinstance(attempts, list)
            or any(not isinstance(item, str) for item in accepted_turn_ids)
            or any(not isinstance(item, str) for item in current_scene_turn_ids)
            or any(
                not isinstance(item, str)
                for item in current_scene_character_ids
            )
            or any(not isinstance(item, dict) for item in attempts)
            or inflight is not None
            and not isinstance(inflight, dict)
            or value.get("current_review_id") is not None
            and not isinstance(value.get("current_review_id"), str)
            or value.get("decision_intent") is not None
            and not isinstance(value.get("decision_intent"), dict)
            or len(set(accepted_turn_ids)) != len(accepted_turn_ids)
            or len(set(current_scene_turn_ids)) != len(current_scene_turn_ids)
            or len(set(current_scene_character_ids))
            != len(current_scene_character_ids)
            or not set(current_scene_turn_ids).issubset(accepted_turn_ids)
            or bool(current_scene_turn_ids) != bool(current_scene_character_ids)
            or int(value["next_turn_number"])
            != len(attempts) + (1 if inflight is not None else 0) + 1
            or value.get("decision_intent") is not None
            and value.get("current_review_id") is None
        ):
            raise StateConflictError("continuous manual state hash changed")
        value["state_sha256"] = supplied
        return value

    @property
    def accepted_turn_ids(self) -> tuple[str, ...]:
        return tuple(str(value) for value in self.load()["accepted_turn_ids"])

    @property
    def current_review_id(self) -> TypedId | None:
        value = self.load()["current_review_id"]
        return None if value is None else TypedId.parse(str(value), IdKind.REVIEW_PACKET)

    @property
    def decision_intent(self) -> dict[str, object] | None:
        value = self.load()["decision_intent"]
        return None if value is None else dict(value)

    def recover_interrupted_attempt(self) -> dict[str, object] | None:
        state = self.load()
        inflight = state["inflight_attempt"]
        if inflight is None:
            return None
        attempt = dict(inflight)
        attempt["status"] = "failed_process_interruption"
        state["attempts"].append(attempt)
        state["inflight_attempt"] = None
        state["revision"] = int(state["revision"]) + 1
        self._write_without_hash(state)
        return attempt

    def reserve_attempt(
        self,
        *,
        raw_source_sha256: str,
        conversation_sha256: str,
        scene_change: bool,
        process_instance_id: str,
    ) -> tuple[int, int, tuple[str, ...], tuple[str, ...]]:
        state = self.load()
        if (
            state["current_review_id"] is not None
            or state["decision_intent"] is not None
            or state["inflight_attempt"] is not None
        ):
            raise StateConflictError("continuous manual review or attempt is unresolved")
        current_scene = tuple(str(value) for value in state["current_scene_accepted_turn_ids"])
        current_cast = tuple(str(value) for value in state["current_scene_character_ids"])
        if scene_change and not current_scene:
            raise StateConflictError(
                "continuous manual Scene Change requires an accepted current scene"
            )
        turn_number = int(state["next_turn_number"])
        target_scene = int(state["scene_number"]) + (1 if scene_change else 0)
        state["next_turn_number"] = turn_number + 1
        state["inflight_attempt"] = {
            "turn_number": turn_number,
            "raw_source_sha256": raw_source_sha256,
            "conversation_sha256": conversation_sha256,
            "scene_change": scene_change,
            "target_scene_number": target_scene,
            "process_instance_id": process_instance_id,
            "status": "reserved_before_transport",
        }
        state["revision"] = int(state["revision"]) + 1
        self._write_without_hash(state)
        return turn_number, target_scene, current_scene, current_cast

    def fail_attempt(self, turn_number: int, *, error_type: str) -> None:
        state = self.load()
        inflight = state["inflight_attempt"]
        if not isinstance(inflight, dict) or inflight.get("turn_number") != turn_number:
            raise StateConflictError("continuous manual failed attempt identity changed")
        attempt = dict(inflight)
        attempt.update({"status": "failed", "error_type": error_type})
        state["attempts"].append(attempt)
        state["inflight_attempt"] = None
        state["revision"] = int(state["revision"]) + 1
        self._write_without_hash(state)

    def record_review(self, record: ContinuousManualReviewRecord) -> None:
        state = self.load()
        inflight = state["inflight_attempt"]
        if (
            not isinstance(inflight, dict)
            or inflight.get("turn_number") != record.prepared.turn_number
            or state["current_review_id"] is not None
        ):
            raise StateConflictError("continuous manual prepared attempt identity changed")
        self._write_review(record, immutable=True)
        attempt = dict(inflight)
        attempt.update(
            {
                "status": "review_ready",
                "review_id": str(record.review_id),
                "review_binding_sha256": record.prepared.review_binding_sha256,
            }
        )
        state["attempts"].append(attempt)
        state["inflight_attempt"] = None
        state["current_review_id"] = str(record.review_id)
        state["revision"] = int(state["revision"]) + 1
        self._write_without_hash(state)

    def load_review(self, review_id: TypedId) -> ContinuousManualReviewRecord:
        path = self._review_path(review_id)
        if not path.is_file() or path.is_symlink():
            raise StateConflictError("unknown continuous manual review")
        wrapper = json.loads(path.read_text(encoding="utf-8"))
        payload = wrapper.get("record")
        if wrapper.get("record_sha256") != canonical_sha256(payload):
            raise StateConflictError("continuous manual review bytes changed")
        record = from_mapping(ContinuousManualReviewRecord, payload)
        if record.review_id != review_id:
            raise StateConflictError("continuous manual review identity changed")
        return record

    def begin_decision(
        self,
        record: ContinuousManualReviewRecord,
        action: CreatorReviewAction,
        *,
        process_instance_id: str,
    ) -> None:
        state = self.load()
        if (
            record.state is not ContinuousManualReviewState.REVIEW_READY
            or state["current_review_id"] != str(record.review_id)
            or state["decision_intent"] is not None
            or action not in {CreatorReviewAction.ACCEPT, CreatorReviewAction.DECLINE}
            or not process_instance_id.strip()
        ):
            raise StateConflictError("continuous manual decision intent conflicts")
        state["decision_intent"] = {
            "review_id": str(record.review_id),
            "review_binding_sha256": record.prepared.review_binding_sha256,
            "action": action.value,
            "process_instance_id": process_instance_id,
        }
        state["revision"] = int(state["revision"]) + 1
        self._write_without_hash(state)

    def reconcile_pending_decision(
        self,
        recover: Callable[
            [PreparedContinuousManualTurn, CreatorReviewAction],
            AcceptedContinuousManualTurn | RejectedContinuousManualTurn | None,
        ],
    ) -> ContinuousManualReviewRecord | None:
        state = self.load()
        review_value = state["current_review_id"]
        intent = state["decision_intent"]
        if review_value is None:
            if intent is not None:
                raise StateConflictError(
                    "continuous manual decision intent lacks its review"
                )
            return None
        review_id = TypedId.parse(str(review_value), IdKind.REVIEW_PACKET)
        record = self.load_review(review_id)
        if intent is None:
            if record.state is not ContinuousManualReviewState.REVIEW_READY:
                raise StateConflictError(
                    "continuous manual terminal review lacks its decision intent"
                )
            return record
        if not isinstance(intent, dict):
            raise StateConflictError("continuous manual decision intent is invalid")
        try:
            action = CreatorReviewAction(str(intent.get("action", "")))
        except ValueError as exc:
            raise StateConflictError(
                "continuous manual decision intent action changed"
            ) from exc
        if (
            intent.get("review_id") != str(review_id)
            or intent.get("review_binding_sha256")
            != record.prepared.review_binding_sha256
            or action not in {CreatorReviewAction.ACCEPT, CreatorReviewAction.DECLINE}
        ):
            raise StateConflictError("continuous manual decision intent changed")
        if record.state is not ContinuousManualReviewState.REVIEW_READY:
            if record.creator_action is not action:
                raise StateConflictError(
                    "continuous manual terminal decision action changed"
                )
            self.terminalize_review(record)
            return record
        result = recover(record.prepared, action)
        if result is None:
            raise StateConflictError(
                "continuous manual decision outcome cannot be proven after restart"
            )
        if action is CreatorReviewAction.ACCEPT:
            if (
                not isinstance(result, AcceptedContinuousManualTurn)
                or result.turn_number != record.prepared.turn_number
                or result.turn_id != record.prepared.turn_id
                or result.generation != record.prepared.accepted_generation_after
                or result.review_binding_sha256
                != record.prepared.review_binding_sha256
            ):
                raise StateConflictError(
                    "continuous manual recovered acceptance changed its binding"
                )
            terminal = replace(
                record,
                state=ContinuousManualReviewState.ACCEPTED,
                creator_action=action,
                accepted=result,
            )
        else:
            if (
                not isinstance(result, RejectedContinuousManualTurn)
                or result.turn_number != record.prepared.turn_number
                or result.turn_id != record.prepared.turn_id
                or result.review_binding_sha256
                != record.prepared.review_binding_sha256
                or not result.recovered_after_restart
            ):
                raise StateConflictError(
                    "continuous manual recovered rejection changed its binding"
                )
            terminal = replace(
                record,
                state=ContinuousManualReviewState.REJECTED,
                creator_action=action,
                rejected=result,
            )
        self.terminalize_review(terminal)
        return terminal

    def terminalize_review(self, record: ContinuousManualReviewRecord) -> None:
        state = self.load()
        intent = state["decision_intent"]
        if (
            state["current_review_id"] != str(record.review_id)
            or not isinstance(intent, dict)
            or record.creator_action is None
            or intent.get("review_id") != str(record.review_id)
            or intent.get("action") != record.creator_action.value
            or intent.get("review_binding_sha256")
            != record.prepared.review_binding_sha256
        ):
            raise StateConflictError("continuous manual terminal review is stale")
        if record.state is ContinuousManualReviewState.ACCEPTED:
            if (
                record.prepared.turn_id in state["accepted_turn_ids"]
                or record.prepared.accepted_generation_after
                != len(state["accepted_turn_ids"]) + 1
            ):
                raise StateConflictError(
                    "continuous manual accepted sequence changed during terminalization"
                )
        self._write_review(record, immutable=False)
        if record.state is ContinuousManualReviewState.ACCEPTED:
            state["accepted_turn_ids"].append(record.prepared.turn_id)
            if record.prepared.scene_change:
                state["scene_number"] = record.prepared.scene_number
                state["current_scene_accepted_turn_ids"] = []
            state["current_scene_accepted_turn_ids"].append(record.prepared.turn_id)
            state["current_scene_character_ids"] = list(
                record.prepared.active_character_ids
            )
        state["current_review_id"] = None
        state["decision_intent"] = None
        state["revision"] = int(state["revision"]) + 1
        self._write_without_hash(state)

    def _write_review(
        self, record: ContinuousManualReviewRecord, *, immutable: bool
    ) -> None:
        path = self._review_path(record.review_id)
        payload = to_primitive(record)
        wrapper = {"record": payload, "record_sha256": canonical_sha256(payload)}
        encoded = canonical_bytes(wrapper) + b"\n"
        if path.exists() and immutable:
            if path.read_bytes() != encoded:
                raise StateConflictError("continuous manual review identity was reused")
            return
        temporary = path.with_suffix(".tmp")
        with temporary.open("wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)

    def _review_path(self, review_id: TypedId) -> Path:
        return self.reviews_root / f"{text_sha256(str(review_id))}.json"

    def _write_without_hash(self, state: dict[str, object]) -> None:
        state.pop("state_sha256", None)
        self._write(state)

    def _write(self, state: dict[str, object]) -> None:
        payload = dict(state)
        payload["state_sha256"] = canonical_sha256(state)
        temporary = self.path.with_suffix(".tmp")
        with temporary.open("wb") as stream:
            stream.write(canonical_bytes(payload) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, self.path)


class ContinuousSillyTavernManualAdapter:
    """Loopback-only manual route over one shared Continuous V3 coordinator."""

    def __init__(
        self,
        *,
        session_id: str,
        profile_id: str,
        execution_identity_sha256: str,
        process_instance_id: str,
        state_store: ContinuousManualStateStore,
        prepare_turn: Callable[
            [
                SillyTavernChatRequest,
                int,
                int,
                int,
                tuple[str, ...],
                tuple[str, ...],
                str,
            ],
            PreparedContinuousManualTurn,
        ],
        accept_turn: Callable[
            [PreparedContinuousManualTurn], AcceptedContinuousManualTurn
        ],
        reject_turn: Callable[
            [PreparedContinuousManualTurn, bool], RejectedContinuousManualTurn
        ],
        recover_decision: Callable[
            [PreparedContinuousManualTurn, CreatorReviewAction],
            AcceptedContinuousManualTurn | RejectedContinuousManualTurn | None,
        ]
        | None = None,
        route_identity: Mapping[str, object],
    ) -> None:
        if not session_id.strip() or profile_id != CONTINUOUS_V3_MANUAL_PROFILE_ID:
            raise ContractValidationError("continuous manual route identity is invalid")
        if not re_is_sha256(execution_identity_sha256) or not process_instance_id.strip():
            raise ContractValidationError("continuous manual execution identity is invalid")
        self.session_id = session_id
        self.profile_id = profile_id
        self.execution_identity_sha256 = execution_identity_sha256
        self.process_instance_id = process_instance_id
        self.state_store = state_store
        self._prepare_turn = prepare_turn
        self._accept_turn = accept_turn
        self._reject_turn = reject_turn
        self._recover_decision = recover_decision
        self._route_identity = dict(route_identity)
        self._lock = Lock()
        self.state_store.recover_interrupted_attempt()
        if self.state_store.decision_intent is not None:
            if self._recover_decision is None:
                raise StateConflictError(
                    "continuous manual decision recovery is unavailable"
                )
            self.state_store.reconcile_pending_decision(self._recover_decision)

    @property
    def virtual_model(self) -> str:
        return CERA_CONTINUOUS_V3_MANUAL_MODEL

    @property
    def reasoner_session_status(self) -> dict[str, object]:
        state = self.state_store.load()
        return {
            "mode": "continuous_v3_manual",
            "active": True,
            "profile_id": self.profile_id,
            "execution_identity_sha256": self.execution_identity_sha256,
            "process_instance_sha256": text_sha256(self.process_instance_id),
            "state_revision": state["revision"],
            "accepted_turn_count": len(state["accepted_turn_ids"]),
            "unresolved_review_id": state["current_review_id"],
            "decision_pending": state["decision_intent"] is not None,
            **self._route_identity,
        }

    def complete(self, request: SillyTavernChatRequest) -> SillyTavernTurnReply:
        with self._lock:
            self._validate_request(request)
            if self.state_store.current_review_id is not None:
                raise StateConflictError("continuous manual creator review is unresolved")
            raw_hash = text_sha256(request.latest_user_content)
            (
                turn_number,
                scene_number,
                current_scene_turn_ids,
                current_scene_character_ids,
            ) = (
                self.state_store.reserve_attempt(
                    raw_source_sha256=raw_hash,
                    conversation_sha256=request.conversation_sha256,
                    scene_change=request.cera_scene_change,
                    process_instance_id=self.process_instance_id,
                )
            )
            accepted_generation = len(self.state_store.accepted_turn_ids) + 1
            try:
                prepared = self._prepare_turn(
                    request,
                    turn_number,
                    accepted_generation,
                    scene_number,
                    current_scene_turn_ids,
                    current_scene_character_ids,
                    self.process_instance_id,
                )
                self._validate_prepared(
                    request,
                    prepared,
                    turn_number=turn_number,
                    accepted_generation=accepted_generation,
                    scene_number=scene_number,
                )
            except BaseException as exc:
                self.state_store.fail_attempt(turn_number, error_type=type(exc).__name__)
                raise
            review_id = deterministic_id(
                IdKind.REVIEW_PACKET,
                "cera.sillytavern.continuous_v3_manual_review.v1",
                (
                    f"{self.session_id}\x1f{prepared.turn_id}\x1f"
                    f"{prepared.review_binding_sha256}"
                ),
            )
            record = ContinuousManualReviewRecord(
                schema_version=ContinuousManualReviewRecord.SCHEMA_VERSION,
                review_id=review_id,
                session_id=self.session_id,
                prepared=prepared,
                state=ContinuousManualReviewState.REVIEW_READY,
            )
            self.state_store.record_review(record)
            return SillyTavernTurnReply(
                prose=prepared.candidate_text,
                request_id=prepared.turn_id,
                artifact_id=None,
                generation=prepared.accepted_generation_after,
                provider_calls=prepared.provider_calls,
                exact_replay=False,
                provisional_review_id=str(review_id),
                candidate_id=prepared.candidate_sha256,
                review_status=record.state.value,
                route_kind="continuous_v3_manual",
            )

    def get_review(self, review_id: TypedId) -> ContinuousManualReviewRecord:
        record = self.state_store.load_review(review_id)
        if (
            record.session_id != self.session_id
            or record.prepared.profile_id != self.profile_id
            or record.prepared.execution_identity_sha256
            != self.execution_identity_sha256
        ):
            raise StateConflictError("continuous manual review route identity changed")
        return record

    def review_action(
        self,
        review_id: TypedId,
        action: CreatorReviewAction,
        *,
        feedback: str | None = None,
    ) -> ContinuousManualReviewRecord:
        with self._lock:
            record = self.get_review(review_id)
            if record.state is not ContinuousManualReviewState.REVIEW_READY:
                raise StateConflictError("continuous manual review is already terminal")
            if self.state_store.current_review_id != review_id:
                raise StateConflictError("continuous manual review is stale")
            if action not in {CreatorReviewAction.ACCEPT, CreatorReviewAction.DECLINE}:
                raise StateConflictError(
                    "continuous manual route permits only strict Accept or Decline"
                )
            if action is CreatorReviewAction.ACCEPT:
                if feedback is not None:
                    raise StateConflictError("continuous manual strict Accept rejects feedback")
                if record.prepared.process_instance_id != self.process_instance_id:
                    raise StateConflictError(
                        "continuous manual Accept cannot resume an uncommitted prior process"
                    )
                if not record.prepared.accept_allowed:
                    raise StateConflictError("continuous manual Accept is not eligible")
            self.state_store.begin_decision(
                record,
                action,
                process_instance_id=self.process_instance_id,
            )
            if action is CreatorReviewAction.ACCEPT:
                accepted = self._accept_turn(record.prepared)
                if (
                    accepted.turn_number != record.prepared.turn_number
                    or accepted.turn_id != record.prepared.turn_id
                    or accepted.generation != record.prepared.accepted_generation_after
                    or accepted.review_binding_sha256
                    != record.prepared.review_binding_sha256
                ):
                    raise StateConflictError("continuous manual Accept changed its binding")
                terminal = replace(
                    record,
                    state=ContinuousManualReviewState.ACCEPTED,
                    creator_action=action,
                    accepted=accepted,
                )
            elif action is CreatorReviewAction.DECLINE:
                recovered = record.prepared.process_instance_id != self.process_instance_id
                rejected = self._reject_turn(record.prepared, recovered)
                if (
                    rejected.turn_number != record.prepared.turn_number
                    or rejected.turn_id != record.prepared.turn_id
                    or rejected.review_binding_sha256
                    != record.prepared.review_binding_sha256
                    or rejected.recovered_after_restart is not recovered
                ):
                    raise StateConflictError("continuous manual rejection changed its binding")
                terminal = replace(
                    record,
                    state=ContinuousManualReviewState.REJECTED,
                    creator_action=action,
                    rejected=rejected,
                    feedback_sha256=(
                        text_sha256(feedback) if feedback is not None else None
                    ),
                )
            self.state_store.terminalize_review(terminal)
            return terminal

    def review_payload(self, record: ContinuousManualReviewRecord) -> dict[str, object]:
        same_process = record.prepared.process_instance_id == self.process_instance_id
        decision_pending = self.state_store.decision_intent is not None
        return {
            "schema_version": "cera.sillytavern_continuous_v3_manual_review.v1",
            "review_id": str(record.review_id),
            "state": record.state.value,
            "provisional": record.state is ContinuousManualReviewState.REVIEW_READY,
            "candidate_text": record.prepared.candidate_text,
            "candidate_sha256": record.prepared.candidate_sha256,
            "candidate_text_sha256": record.prepared.candidate_text_sha256,
            "sequence_plan": list(record.prepared.sequence_beats),
            "sequence_plan_sha256": record.prepared.sequence_plan_sha256,
            "speaker_marks": [],
            "assessment": {
                "schema_version": record.prepared.assessment_schema_version,
                "severity": record.prepared.assessment_severity.value,
                "publication_eligibility": (
                    record.prepared.assessment_publication_eligibility.value
                ),
                "issue_owner": record.prepared.assessment_issue_owner.value,
                "reason_codes": list(record.prepared.assessment_reason_codes),
                "creator_reason": record.prepared.assessment_creator_reason,
                "verifier_status": record.prepared.assessment_verifier_status,
                "assessment_receipt_sha256": (
                    record.prepared.assessment_receipt_sha256
                ),
            },
            "accept_enabled": (
                record.state is ContinuousManualReviewState.REVIEW_READY
                and record.prepared.accept_allowed
                and same_process
                and not decision_pending
            ),
            "decline_enabled": (
                record.state is ContinuousManualReviewState.REVIEW_READY
                and not decision_pending
            ),
            "decision_pending": decision_pending,
            "recovered_after_restart": not same_process,
            "validator_semantic_status": record.prepared.validator_semantic_status,
            "validator_package_id": record.prepared.validator_package_id,
            "validator_package_sha256": record.prepared.validator_package_sha256,
            "prepared_package_id": record.prepared.validator_package_id,
            "review_binding_sha256": record.prepared.review_binding_sha256,
            "creator_action": (
                record.creator_action.value if record.creator_action is not None else None
            ),
            "feedback_required": False,
            "scene_id": record.prepared.scene_id,
            "scene_change": record.prepared.scene_change,
            "active_character_ids": list(record.prepared.active_character_ids),
            "raw_source_sha256": record.prepared.raw_source_sha256,
            "ingress_receipt_sha256": record.prepared.ingress_receipt_sha256,
            "execution_identity_sha256": record.prepared.execution_identity_sha256,
        }

    def review_decision_payload(
        self,
        review_id: TypedId,
        action: CreatorReviewAction,
        record: ContinuousManualReviewRecord,
    ) -> dict[str, object]:
        if record.review_id != review_id or record.creator_action is not action:
            raise StateConflictError("continuous manual decision result changed")
        if action is CreatorReviewAction.ACCEPT:
            if record.accepted is None:
                raise StateConflictError("continuous manual acceptance result disappeared")
            return {
                "status": "accepted",
                "creator_action": action.value,
                "review_id": str(review_id),
                "artifact_id": record.accepted.artifact_id,
                "generation": record.accepted.generation,
                "provider_calls": 0,
                "automatic_retries": 0,
                "promotion_receipt_sha256": (
                    record.accepted.promotion_receipt_sha256
                ),
                "review_binding_sha256": record.accepted.review_binding_sha256,
            }
        if action is CreatorReviewAction.DECLINE and record.rejected is not None:
            return {
                "status": "rejected",
                "creator_action": action.value,
                "review_id": str(review_id),
                "provider_calls": 0,
                "automatic_retries": 0,
                "discard_receipt_sha256": record.rejected.discard_receipt_sha256,
                "review_binding_sha256": record.rejected.review_binding_sha256,
                "recovered_after_restart": (
                    record.rejected.recovered_after_restart
                ),
            }
        raise StateConflictError("continuous manual decision action changed")

    def _validate_request(self, request: SillyTavernChatRequest) -> None:
        if request.model != CERA_CONTINUOUS_V3_MANUAL_MODEL:
            raise StateConflictError("continuous manual adapter rejects model substitution")
        if request.cera_profile_id != self.profile_id:
            raise StateConflictError("continuous manual profile identity changed")
        if request.cera_session_id != self.session_id:
            raise StateConflictError("continuous manual session identity changed")
        unsupported = (
            request.cera_scene_depth,
            request.cera_regeneration_key,
            request.cera_character_autonomy,
            request.cera_prompt_handling,
            request.cera_reasoning_effort,
        )
        if any(value is not None for value in unsupported):
            raise StateConflictError("continuous manual request contains unsupported controls")

    def _validate_prepared(
        self,
        request: SillyTavernChatRequest,
        prepared: PreparedContinuousManualTurn,
        *,
        turn_number: int,
        accepted_generation: int,
        scene_number: int,
    ) -> None:
        expected_scene_id = f"scene-{scene_number:03d}"
        if (
            prepared.turn_number != turn_number
            or prepared.turn_id != f"turn-{turn_number:03d}"
            or prepared.scene_number != scene_number
            or prepared.scene_id != expected_scene_id
            or prepared.scene_change is not request.cera_scene_change
            or prepared.accepted_generation_after != accepted_generation
            or prepared.raw_source_sha256
            != text_sha256(request.latest_user_content)
            or prepared.conversation_sha256 != request.conversation_sha256
            or prepared.profile_id != self.profile_id
            or prepared.execution_identity_sha256 != self.execution_identity_sha256
            or prepared.process_instance_id != self.process_instance_id
        ):
            raise StateConflictError("continuous manual prepared route identity changed")
