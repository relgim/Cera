"""Durable provisional-review and creator-decision custody for Pi Scene."""

from __future__ import annotations

from dataclasses import dataclass, replace
import json
import os
from pathlib import Path
import re
from typing import Any, ClassVar, Mapping, Sequence
from uuid import uuid4

from cera.errors import ContractValidationError, StateConflictError
from cera.schema import from_mapping
from cera.serialization import canonical_bytes, canonical_sha256, text_sha256, to_primitive

from .contracts import (
    LeanAcceptedTurnReceiptV1,
    LeanCandidateV1,
    LeanRecordingAttemptV1,
    LeanRunResultV1,
    RecordingStatus,
)
from .http_contracts import LeanSceneRequestControlsV1
from .store import LeanSceneStore


@dataclass(frozen=True, slots=True)
class CreatorGuidanceV1:
    """Noncanonical creator control, never an exact-source story claim."""

    SCHEMA_VERSION: ClassVar[str] = "cera.pi_scene.creator_guidance.v1"

    schema_version: str
    action: str
    text: str
    text_sha256: str

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("Pi Scene creator-guidance schema changed")
        if self.action not in {"regenerate", "replan"}:
            raise ContractValidationError("Pi Scene creator-guidance action is invalid")
        if not isinstance(self.text, str):
            raise ContractValidationError("Pi Scene creator guidance must be text")
        if len(self.text) > 4_000:
            raise ContractValidationError("Pi Scene creator guidance is too large")
        if text_sha256(self.text) != self.text_sha256:
            raise ContractValidationError("Pi Scene creator-guidance binding changed")

    @classmethod
    def create(cls, *, action: str, text: str) -> "CreatorGuidanceV1":
        return cls(
            schema_version=cls.SCHEMA_VERSION,
            action=action,
            text=text,
            text_sha256=text_sha256(text),
        )


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
    request_controls: LeanSceneRequestControlsV1 | None = None

    def __post_init__(self) -> None:
        for field_name in ("world_id", "branch_id", "scene_id", "exact_user_source"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ContractValidationError(f"turn input {field_name} is empty")
        if not self.current_state:
            raise ContractValidationError("turn input requires current accepted state")
        if self.request_controls is not None and not isinstance(
            self.request_controls,
            LeanSceneRequestControlsV1,
        ):
            raise ContractValidationError("turn input request controls are invalid")


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
    created_unix_seconds: int
    state: str = LeanReviewState.REVIEW_READY
    accepted_receipt: LeanAcceptedTurnReceiptV1 | None = None
    recording_attempt: LeanRecordingAttemptV1 | None = None
    creator_guidance: CreatorGuidanceV1 | None = None

    def __post_init__(self) -> None:
        if type(self.created_unix_seconds) is not int or self.created_unix_seconds < 0:
            raise ContractValidationError("Pi Scene review creation time is invalid")

    @property
    def candidate(self) -> LeanCandidateV1:
        return self.result.candidate


@dataclass(frozen=True, slots=True)
class LeanDecisionResultV1:
    review: LeanReviewRecordV1
    successor: LeanReviewRecordV1 | None = None
    operational_warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DecisionReplayV1:
    action: str
    request_sha256: str
    result: LeanDecisionResultV1


@dataclass(frozen=True, slots=True)
class ReviewStateSnapshotV1:
    candidate_counter: int
    reviews: Mapping[str, LeanReviewRecordV1]
    unresolved_by_branch: Mapping[tuple[str, str], str]
    decisions: Mapping[str, DecisionReplayV1]


class DurableReviewStateStore:
    """Hash-bind and atomically recover review state under one runtime root."""

    def __init__(self, *, root: Path, scene_store: LeanSceneStore) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.scene_store = scene_store
        self.path = self.root / "REVIEW_STATE.json"

    def persist(
        self,
        *,
        candidate_counter: int,
        reviews: Mapping[str, LeanReviewRecordV1],
        decisions: Mapping[str, DecisionReplayV1],
    ) -> None:
        review_values = [
            _review_state_payload(review, session_root=self.root)
            for review in sorted(reviews.values(), key=lambda value: value.review_id)
            if self._requires_persistence(review)
        ]
        decision_values = [
            _decision_state_payload(
                review_id,
                decision,
                session_root=self.root,
            )
            for review_id, decision in sorted(decisions.items())
        ]
        body = {
            "schema_version": "cera.pi_scene.review_state.v4",
            "candidate_counter": candidate_counter,
            "reviews": review_values,
            "decisions": decision_values,
        }
        payload = dict(body)
        payload["state_sha256"] = canonical_sha256(body)
        _atomic_write_json(self.path, payload)

    def load(self) -> ReviewStateSnapshotV1:
        if not self.path.exists():
            return ReviewStateSnapshotV1(0, {}, {}, {})
        payload = self._read_payload()
        counter = payload["candidate_counter"]
        values = payload["reviews"]
        decision_values = payload.get("decisions", [])
        if (
            type(counter) is not int
            or counter < 0
            or not isinstance(values, list)
            or not isinstance(decision_values, list)
        ):
            raise StateConflictError("Pi Scene review state counters are invalid")

        reviews: dict[str, LeanReviewRecordV1] = {}
        unresolved: dict[tuple[str, str], str] = {}
        decisions: dict[str, DecisionReplayV1] = {}
        stale = False
        for value in values:
            review = _review_from_state_payload(value, session_root=self.root)
            candidate = review.candidate
            if review.review_id in reviews:
                raise StateConflictError("Pi Scene review state contains duplicate identity")
            if review.state == LeanReviewState.REVIEW_READY:
                head = self.scene_store.load_head(
                    world_id=candidate.world_id,
                    branch_id=candidate.branch_id,
                )
                if (
                    candidate.generation == head.generation + 1
                    and candidate.parent_accepted_turn_id == head.accepted_turn_id
                    and candidate.accepted_head_before_sha256 == head.accepted_head_sha256
                ):
                    key = (candidate.world_id, candidate.branch_id)
                    if key in unresolved:
                        raise StateConflictError(
                            "Pi Scene review state contains duplicate ownership"
                        )
                    reviews[review.review_id] = review
                    unresolved[key] = review.review_id
                    continue

                accepted = head.receipt
                if accepted is not None and _accepted_matches_candidate(
                    accepted,
                    candidate,
                ):
                    # Accept committed after the last provisional snapshot.
                    # Reconstruct the exact terminal decision from immutable
                    # branch custody so a lost response remains replayable.
                    recovered = replace(
                        review,
                        state=LeanReviewState.ACCEPTED,
                        accepted_receipt=accepted,
                        recording_attempt=self.scene_store.load_recording_attempt(
                            accepted
                        ),
                    )
                    decisions[review.review_id] = DecisionReplayV1(
                        action="accept",
                        request_sha256=decision_request_sha256(action="accept"),
                        result=LeanDecisionResultV1(review=recovered),
                    )
                    if (
                        self.scene_store.recording_status(accepted)
                        is not RecordingStatus.COMPLETE
                    ):
                        reviews[review.review_id] = recovered
                    stale = True
                    continue

                stale = True
                continue

            accepted = review.accepted_receipt
            if review.state != LeanReviewState.ACCEPTED or accepted is None:
                raise StateConflictError("Pi Scene durable review state is invalid")
            status = self.scene_store.recording_status(accepted)
            if status is RecordingStatus.COMPLETE:
                stale = True
                continue
            reviews[review.review_id] = replace(
                review,
                recording_attempt=self.scene_store.load_recording_attempt(accepted),
            )

        for value in decision_values:
            review_id, decision = _decision_from_state_payload(
                value,
                session_root=self.root,
            )
            if review_id in decisions:
                raise StateConflictError(
                    "Pi Scene review state contains duplicate decision receipts"
                )
            if decision.action == "accept":
                accepted = decision.result.review.accepted_receipt
                if accepted is None:
                    raise StateConflictError("Accept decision omitted its receipt")
                authoritative_attempt = self.scene_store.load_recording_attempt(accepted)
                decision = replace(
                    decision,
                    result=replace(
                        decision.result,
                        review=replace(
                            decision.result.review,
                            recording_attempt=authoritative_attempt,
                        ),
                    ),
                )
            decisions[review_id] = decision

        for review_id in decisions:
            prior = reviews.get(review_id)
            if prior is None or prior.state == LeanReviewState.ACCEPTED:
                continue
            reviews.pop(review_id)
            unresolved.pop(
                (prior.candidate.world_id, prior.candidate.branch_id),
                None,
            )
            stale = True

        snapshot = ReviewStateSnapshotV1(
            candidate_counter=counter,
            reviews=reviews,
            unresolved_by_branch=unresolved,
            decisions=decisions,
        )
        if stale:
            self.persist(
                candidate_counter=counter,
                reviews=reviews,
                decisions=decisions,
            )
        return snapshot

    def _read_payload(self) -> dict[str, Any]:
        if self.path.is_symlink() or not self.path.is_file():
            raise StateConflictError("Pi Scene review state path is unsafe")
        raw = self.path.read_bytes()
        if not raw or len(raw) > 16_000_000:
            raise StateConflictError("Pi Scene review state size is invalid")
        try:
            payload = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise StateConflictError("Pi Scene review state is invalid JSON") from exc
        legacy_fields = {
            "schema_version",
            "candidate_counter",
            "reviews",
            "state_sha256",
        }
        current_fields = legacy_fields | {"decisions"}
        payload_fields = set(payload) if isinstance(payload, dict) else set()
        if not isinstance(payload, dict) or payload_fields not in (
            legacy_fields,
            current_fields,
        ):
            raise StateConflictError("Pi Scene review state fields changed")
        body = {key: payload[key] for key in payload if key != "state_sha256"}
        schema_version = payload["schema_version"]
        if schema_version not in {
            "cera.pi_scene.review_state.v1",
            "cera.pi_scene.review_state.v2",
            "cera.pi_scene.review_state.v3",
            "cera.pi_scene.review_state.v4",
        } or payload["state_sha256"] != canonical_sha256(body):
            raise StateConflictError("Pi Scene review state binding changed")
        if (schema_version in {
            "cera.pi_scene.review_state.v3",
            "cera.pi_scene.review_state.v4",
        }) != (
            "decisions" in payload
        ):
            raise StateConflictError("Pi Scene review state schema fields changed")
        return payload

    def _requires_persistence(self, review: LeanReviewRecordV1) -> bool:
        if review.state == LeanReviewState.REVIEW_READY:
            return True
        if review.state != LeanReviewState.ACCEPTED:
            return False
        accepted = review.accepted_receipt
        if accepted is None:
            raise StateConflictError("accepted Pi Scene review omitted its receipt")
        return self.scene_store.recording_status(accepted) is not RecordingStatus.COMPLETE


def decision_request_sha256(
    *,
    action: str,
    feedback: str | None = None,
    force_rehydrate: bool = False,
    turn_input: LeanSceneTurnInputV1 | None = None,
) -> str:
    if action not in {"accept", "decline", "regenerate", "replan"}:
        raise ContractValidationError("Pi Scene decision action is invalid")
    return canonical_sha256(
        {
            "schema_version": "cera.pi_scene.decision_request.v1",
            "action": action,
            "feedback": feedback,
            "force_rehydrate": force_rehydrate,
            "turn_input_sha256": (
                None if turn_input is None else canonical_sha256(turn_input)
            ),
        }
    )


def _accepted_matches_candidate(
    accepted: LeanAcceptedTurnReceiptV1,
    candidate: LeanCandidateV1,
) -> bool:
    return (
        accepted.accepted_turn_id == candidate.turn_id
        and accepted.parent_accepted_turn_id == candidate.parent_accepted_turn_id
        and accepted.parent_accepted_head_sha256
        == candidate.accepted_head_before_sha256
        and accepted.world_id == candidate.world_id
        and accepted.branch_id == candidate.branch_id
        and accepted.scene_id == candidate.scene_id
        and accepted.generation == candidate.generation
        and accepted.route is candidate.route
        and accepted.exact_user_source == candidate.exact_user_source
        and accepted.exact_user_source_sha256 == candidate.exact_user_source_sha256
        and accepted.exact_accepted_prose == candidate.story_text
        and accepted.exact_accepted_prose_sha256 == candidate.story_text_sha256
        and accepted.primary_authority_kind == candidate.primary_authority_kind
        and accepted.primary_authority_json == candidate.primary_authority_json
        and accepted.primary_authority_sha256 == candidate.primary_authority_sha256
        and accepted.writer_view_manifest_sha256
        == candidate.writer_view_manifest_sha256
        and accepted.writer_receipt == candidate.writer_receipt
        and accepted.warnings == candidate.warnings
        and accepted.candidate_sha256 == candidate.candidate_sha256
    )


def _review_state_payload(
    review: LeanReviewRecordV1,
    *,
    session_root: Path,
) -> dict[str, Any]:
    session_dir = review.pi_session_dir.resolve()
    if not session_dir.is_relative_to(session_root):
        raise StateConflictError("Pi Scene review session escaped its durable root")
    return {
        "review_id": review.review_id,
        "turn_input": to_primitive(review.turn_input),
        "result": to_primitive(review.result),
        "pi_session_id": review.pi_session_id,
        "pi_session_relative_path": session_dir.relative_to(session_root).as_posix(),
        "created_unix_seconds": review.created_unix_seconds,
        "state": review.state,
        "accepted_receipt": (
            None
            if review.accepted_receipt is None
            else to_primitive(review.accepted_receipt)
        ),
        "recording_attempt": (
            None
            if review.recording_attempt is None
            else to_primitive(review.recording_attempt)
        ),
        "creator_guidance": (
            None if review.creator_guidance is None else to_primitive(review.creator_guidance)
        ),
    }


def _review_from_state_payload(
    value: Any,
    *,
    session_root: Path,
) -> LeanReviewRecordV1:
    legacy_fields = {
        "review_id",
        "turn_input",
        "result",
        "pi_session_id",
        "pi_session_relative_path",
        "creator_guidance",
    }
    transitional_fields = legacy_fields | {
        "state",
        "accepted_receipt",
        "recording_attempt",
    }
    current_fields = transitional_fields | {"created_unix_seconds"}
    value_fields = set(value) if isinstance(value, Mapping) else set()
    if not isinstance(value, Mapping) or value_fields not in (
        legacy_fields,
        transitional_fields,
        current_fields,
    ):
        raise StateConflictError("Pi Scene persisted review fields changed")
    turn_value = value["turn_input"]
    result_value = value["result"]
    if not isinstance(turn_value, Mapping) or not isinstance(result_value, Mapping):
        raise StateConflictError("Pi Scene persisted review payload is invalid")
    turn = from_mapping(LeanSceneTurnInputV1, turn_value)
    result = from_mapping(LeanRunResultV1, result_value)
    guidance_value = value["creator_guidance"]
    guidance = (
        None
        if guidance_value is None
        else from_mapping(CreatorGuidanceV1, guidance_value)
    )
    is_legacy = value_fields == legacy_fields
    state = LeanReviewState.REVIEW_READY if is_legacy else value["state"]
    if state not in {
        LeanReviewState.REVIEW_READY,
        LeanReviewState.ACCEPTED,
        LeanReviewState.DECLINED,
        LeanReviewState.REGENERATED,
        LeanReviewState.REPLANNED,
    }:
        raise StateConflictError("Pi Scene persisted review state is invalid")
    accepted_value = None if is_legacy else value["accepted_receipt"]
    attempt_value = None if is_legacy else value["recording_attempt"]
    if accepted_value is not None and not isinstance(accepted_value, Mapping):
        raise StateConflictError("Pi Scene persisted accepted receipt is invalid")
    if attempt_value is not None and not isinstance(attempt_value, Mapping):
        raise StateConflictError("Pi Scene persisted recording attempt is invalid")
    accepted = (
        None
        if accepted_value is None
        else from_mapping(LeanAcceptedTurnReceiptV1, accepted_value)
    )
    attempt = (
        None
        if attempt_value is None
        else from_mapping(LeanRecordingAttemptV1, attempt_value)
    )
    review_id = value["review_id"]
    session_id = value["pi_session_id"]
    relative = value["pi_session_relative_path"]
    created_unix_seconds = value.get("created_unix_seconds", 0)
    if (
        not isinstance(review_id, str)
        or not isinstance(session_id, str)
        or not session_id.strip()
        or not isinstance(relative, str)
        or not relative
        or type(created_unix_seconds) is not int
        or created_unix_seconds < 0
    ):
        raise StateConflictError("Pi Scene persisted review identity is invalid")
    relative_path = Path(relative)
    if relative_path.is_absolute() or ".." in relative_path.parts:
        raise StateConflictError("Pi Scene persisted session path is unsafe")
    session_dir = (session_root / relative_path).resolve()
    if not session_dir.is_relative_to(session_root):
        raise StateConflictError("Pi Scene persisted session path escaped its root")
    candidate = result.candidate
    if (
        review_id != f"review-{candidate.candidate_sha256[:28]}"
        or candidate.world_id != turn.world_id
        or candidate.branch_id != turn.branch_id
        or candidate.scene_id != turn.scene_id
        or candidate.exact_user_source != turn.exact_user_source
        or candidate.writer_receipt.session_id_sha256 != text_sha256(session_id)
    ):
        raise StateConflictError("Pi Scene persisted review binding changed")
    if state != LeanReviewState.ACCEPTED:
        if accepted is not None or attempt is not None:
            raise StateConflictError("non-accepted Pi Scene review contains accepted state")
    elif accepted is None or (
        accepted.candidate_sha256 != candidate.candidate_sha256
        or accepted.world_id != candidate.world_id
        or accepted.branch_id != candidate.branch_id
        or accepted.scene_id != candidate.scene_id
        or accepted.generation != candidate.generation
        or accepted.exact_user_source != candidate.exact_user_source
        or accepted.exact_accepted_prose != candidate.story_text
        or accepted.primary_authority_sha256 != candidate.primary_authority_sha256
        or accepted.writer_receipt != candidate.writer_receipt
        or (attempt is not None and attempt.accepted_turn_id != accepted.accepted_turn_id)
    ):
        raise StateConflictError("accepted Pi Scene review binding changed")
    return LeanReviewRecordV1(
        review_id=review_id,
        turn_input=turn,
        result=result,
        pi_session_id=session_id,
        pi_session_dir=session_dir,
        created_unix_seconds=created_unix_seconds,
        state=state,
        accepted_receipt=accepted,
        recording_attempt=attempt,
        creator_guidance=guidance,
    )


def _decision_state_payload(
    review_id: str,
    decision: DecisionReplayV1,
    *,
    session_root: Path,
) -> dict[str, Any]:
    result = decision.result
    body = {
        "schema_version": "cera.pi_scene.decision_receipt.v1",
        "review_id": review_id,
        "action": decision.action,
        "request_sha256": decision.request_sha256,
        "review": _review_state_payload(result.review, session_root=session_root),
        "successor": (
            None
            if result.successor is None
            else _review_state_payload(result.successor, session_root=session_root)
        ),
        "operational_warnings": list(result.operational_warnings),
    }
    payload = dict(body)
    payload["decision_sha256"] = canonical_sha256(body)
    return payload


def _decision_from_state_payload(
    value: Any,
    *,
    session_root: Path,
) -> tuple[str, DecisionReplayV1]:
    expected = {
        "schema_version",
        "review_id",
        "action",
        "request_sha256",
        "review",
        "successor",
        "operational_warnings",
        "decision_sha256",
    }
    if not isinstance(value, Mapping) or set(value) != expected:
        raise StateConflictError("Pi Scene decision receipt fields changed")
    body = {key: value[key] for key in value if key != "decision_sha256"}
    if (
        value["schema_version"] != "cera.pi_scene.decision_receipt.v1"
        or value["decision_sha256"] != canonical_sha256(body)
    ):
        raise StateConflictError("Pi Scene decision receipt binding changed")
    review_id = value["review_id"]
    action = value["action"]
    request_sha256 = value["request_sha256"]
    warnings = value["operational_warnings"]
    if (
        not isinstance(review_id, str)
        or action not in {"accept", "decline", "regenerate", "replan"}
        or not isinstance(request_sha256, str)
        or not re.fullmatch(r"[0-9a-f]{64}", request_sha256)
        or not isinstance(warnings, list)
        or any(not isinstance(item, str) or not item for item in warnings)
    ):
        raise StateConflictError("Pi Scene decision receipt values are invalid")
    review = _review_from_state_payload(value["review"], session_root=session_root)
    successor_value = value["successor"]
    successor = (
        None
        if successor_value is None
        else _review_from_state_payload(successor_value, session_root=session_root)
    )
    expected_state = {
        "accept": LeanReviewState.ACCEPTED,
        "decline": LeanReviewState.DECLINED,
        "regenerate": LeanReviewState.REGENERATED,
        "replan": LeanReviewState.REPLANNED,
    }[action]
    if review.review_id != review_id or review.state != expected_state:
        raise StateConflictError("Pi Scene decision receipt review changed")
    if action in {"regenerate", "replan"}:
        if successor is None or successor.state != LeanReviewState.REVIEW_READY:
            raise StateConflictError("Pi Scene decision successor is missing")
        if (
            successor.review_id == review_id
            or successor.candidate.world_id != review.candidate.world_id
            or successor.candidate.branch_id != review.candidate.branch_id
            or successor.candidate.generation != review.candidate.generation
        ):
            raise StateConflictError("Pi Scene decision successor binding changed")
        predecessor_id = review.candidate.candidate_id
        if action == "regenerate" and (
            successor.result.regenerated_from_candidate_id != predecessor_id
            or successor.result.replanned_from_candidate_id is not None
        ):
            raise StateConflictError("Pi Scene regeneration receipt changed")
        if action == "replan" and (
            successor.result.replanned_from_candidate_id != predecessor_id
            or successor.result.regenerated_from_candidate_id is not None
        ):
            raise StateConflictError("Pi Scene replan receipt changed")
    elif successor is not None:
        raise StateConflictError("Pi Scene decision has an unexpected successor")
    return review_id, DecisionReplayV1(
        action=action,
        request_sha256=request_sha256,
        result=LeanDecisionResultV1(
            review=review,
            successor=successor,
            operational_warnings=tuple(warnings),
        ),
    )


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    data = canonical_bytes(payload) + b"\n"
    try:
        with temporary.open("xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()
