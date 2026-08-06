"""Loopback review adapter for the sequence-first Stage 6 virtual model."""

from __future__ import annotations

from dataclasses import dataclass, replace
import json
from pathlib import Path
from threading import RLock
from typing import Callable, Mapping

from cera.creator_review import CreatorReviewAction
from cera.errors import ContractValidationError, StateConflictError
from cera.ids import IdKind, TypedId, deterministic_id
from cera.serialization import canonical_json, canonical_sha256, text_sha256, to_primitive

from .models import (
    CERA_SEQUENCE_FIRST_STAGE6_MODEL,
    SillyTavernChatRequest,
    SillyTavernTurnReply,
)
from .server import CeraSillyTavernServerConfig, build_server
from .sequence_first_stage6 import (
    ExplicitSceneInitializationV1,
    SequenceFirstPreparedStage6TurnV1,
    SequenceFirstStage6Bridge,
    SequenceFirstStage6TurnCustodyV1,
)
from cera.sequence_first.runtime import SequenceFirstRunResultV1
from cera.sequence_first.contracts import apply_presence_changes


SEQUENCE_FIRST_STAGE6_PROFILE = "cera.sequence_first.stage6.v1"
SEQUENCE_FIRST_STAGE6_PROFILE_RELATIVE_PATH = (
    "integrations/sillytavern/sequence_first_stage6_profile.json"
)


def validate_sequence_first_stage6_profile(
    profile: Mapping[str, object],
) -> dict[str, object]:
    """Validate the non-authorizing, shadow-only Stage 6 route identity."""

    data = dict(profile)
    expected = {
        "profile_id": SEQUENCE_FIRST_STAGE6_PROFILE,
        "production": False,
        "endpoint": "http://127.0.0.1:5116/v1",
        "model": CERA_SEQUENCE_FIRST_STAGE6_MODEL,
        "stream": False,
        "route": "sequence_first_stage6",
        "provider_mode": "authority_bound_provider",
        "provider_activation_required": True,
        "external_provider_calls_authorized_by_profile": 0,
        "planner": {
            "model": "gpt-5.6-sol",
            "reasoning_effort": "medium",
            "fast_mode": False,
            "session": "persistent_per_branch",
        },
        "writer": {
            "model": "deepseek-v4-flash",
            "thinking": False,
            "maximum_fresh_attempts": 3,
            "merge_attempts": False,
        },
        "validator": {
            "model": "gpt-5.6-sol",
            "reasoning_effort": "medium",
            "fast_mode": False,
            "session": "fresh_per_candidate",
        },
        "reader": {
            "model": "gpt-5.6-sol",
            "reasoning_effort": "medium",
            "fast_mode": False,
            "session": "fresh_per_candidate",
        },
        "creator_review_required": True,
        "automatic_accept": False,
        "automatic_retry": False,
        "fallback": False,
        "database_policy": "isolated_copied_sequence_first_world",
        "loopback_only": True,
    }
    if data != expected:
        raise ContractValidationError("sequence-first Stage 6 profile changed")
    return data


def sequence_first_stage6_profile_path(project_root: Path) -> Path:
    return (project_root / SEQUENCE_FIRST_STAGE6_PROFILE_RELATIVE_PATH).resolve()


def sequence_first_stage6_server_config() -> CeraSillyTavernServerConfig:
    """Explicit loopback binding; the shared development default stays unchanged."""

    return CeraSillyTavernServerConfig(
        host="127.0.0.1",
        port=5116,
        model=CERA_SEQUENCE_FIRST_STAGE6_MODEL,
        service="cera-sequence-first-stage6-shadow",
    )


def build_sequence_first_stage6_server(
    adapter: "SequenceFirstStage6HttpAdapter",
):
    return build_server(adapter, sequence_first_stage6_server_config())


@dataclass(frozen=True, slots=True)
class SequenceFirstReviewRecordV1:
    review_id: TypedId
    prepared: SequenceFirstPreparedStage6TurnV1
    result: SequenceFirstRunResultV1
    generation: int
    provider_calls: int
    provider_evidence_json: str
    state: str = "review_ready"
    accepted_turn_id: str | None = None
    active_head_after_sha256: str | None = None

    @property
    def review_binding_sha256(self) -> str:
        candidate = self.result.candidate
        return canonical_sha256(
            {
                "review_id": str(self.review_id),
                "candidate_sha256": None if candidate is None else candidate.candidate_sha256,
                "request_id": self.prepared.request.custody.request_id,
                "generation": self.generation,
                "provider_calls": self.provider_calls,
                "provider_evidence_json_sha256": text_sha256(self.provider_evidence_json),
            }
        )


class SequenceFirstStage6HttpAdapter:
    """Actual server adapter: parse, generate, review, then atomic accept/decline."""

    def __init__(
        self,
        *,
        bridge: SequenceFirstStage6Bridge,
        world_id: str,
        branch_id: str,
        session_id: str,
        provider_call_count: Callable[[], int] = lambda: 0,
        provider_evidence_snapshot: Callable[[], Mapping[str, object]] = lambda: {},
        initial_scene: ExplicitSceneInitializationV1 | None = None,
    ) -> None:
        self._bridge = bridge
        self._world_id = world_id
        self._branch_id = branch_id
        self._session_id = session_id
        self._provider_call_count = provider_call_count
        self._provider_evidence_snapshot = provider_evidence_snapshot
        self._initial_scene = initial_scene
        self._reviews: dict[TypedId, SequenceFirstReviewRecordV1] = {}
        self._unresolved_review_id: TypedId | None = None
        self._accepted_generation = bridge.accepted_generation(
            world_id=world_id,
            branch_id=branch_id,
        )
        self._lock = RLock()

    @property
    def virtual_model(self) -> str:
        return CERA_SEQUENCE_FIRST_STAGE6_MODEL

    @property
    def reasoner_session_status(self) -> dict[str, object]:
        """Privacy-safe status contract consumed by the shared `/health` route."""

        with self._lock:
            return {
                "mode": "sequence_first_stage6",
                "active": True,
                "profile_id": SEQUENCE_FIRST_STAGE6_PROFILE,
                "route": "sequence_first_stage6",
                "model": self.virtual_model,
                "planner_session": "persistent_per_accepted_branch",
                "validator_session": "fresh_per_candidate",
                "reader_session": "fresh_per_candidate",
                "accepted_generation": self._accepted_generation,
                "creator_review_unresolved": self._unresolved_review_id is not None,
                "automatic_retry": False,
                "fallback": False,
            }

    @property
    def active_runtime_status(self) -> dict[str, object]:
        return {
            "valid": True,
            "profile_id": SEQUENCE_FIRST_STAGE6_PROFILE,
            "route": "sequence_first_stage6",
            "model": self.virtual_model,
            "production": False,
            "provider_activation_required": True,
        }

    def complete(self, request: SillyTavernChatRequest) -> SillyTavernTurnReply:
        with self._lock:
            self._validate_request(request)
            if self._unresolved_review_id is not None:
                raise StateConflictError("sequence-first creator review is unresolved")
            generation = self._accepted_generation + 1
            source_hash = text_sha256(request.latest_user_content)
            identity = f"{self._session_id}:{generation}:{request.conversation_sha256}:{source_hash}"
            custody = SequenceFirstStage6TurnCustodyV1(
                request_id=f"request-{source_hash[:24]}",
                candidate_id=f"candidate-{text_sha256(identity)[:24]}",
                turn_id=f"turn-{generation:04d}-{source_hash[:12]}",
                transaction_id=f"transaction-{text_sha256(identity + ':tx')[:24]}",
                current_source_key="source:current",
            )
            before_calls = self._provider_call_count()
            prepared = self._bridge.prepare(
                raw_request={
                    "model": request.model,
                    "messages": [
                        {"role": value.role, "content": value.content}
                        for value in request.messages
                    ],
                    "stream": request.stream,
                    "cera_session_id": request.cera_session_id,
                    "cera_profile_id": request.cera_profile_id,
                    "cera_scene_change": request.cera_scene_change,
                },
                world_id=self._world_id,
                branch_id=self._branch_id,
                custody=custody,
                scene_initialization=(
                    self._initial_scene if self._accepted_generation == 0 else None
                ),
            )
            result = self._bridge.generate(prepared)
            provider_calls = self._provider_call_count() - before_calls
            if result.candidate is None:
                raise StateConflictError("sequence-first candidate failed qualification")
            review_id = deterministic_id(
                IdKind.REVIEW_PACKET,
                "cera.sequence_first.stage6.review.v1",
                result.candidate.candidate_sha256,
            )
            if review_id in self._reviews:
                raise StateConflictError("sequence-first review identity was reused")
            record = SequenceFirstReviewRecordV1(
                review_id=review_id,
                prepared=prepared,
                result=result,
                generation=generation,
                provider_calls=provider_calls,
                provider_evidence_json=canonical_json(
                    dict(self._provider_evidence_snapshot())
                ),
            )
            self._reviews[review_id] = record
            self._unresolved_review_id = review_id
            return SillyTavernTurnReply(
                prose=result.candidate.writer_response.story_text,
                request_id=custody.request_id,
                artifact_id=None,
                generation=generation,
                provider_calls=provider_calls,
                exact_replay=False,
                provisional_review_id=str(review_id),
                candidate_id=custody.candidate_id,
                review_status="review_ready",
                route_kind="sequence_first_stage6",
            )

    def get_review(self, review_id: TypedId) -> SequenceFirstReviewRecordV1:
        try:
            return self._reviews[review_id]
        except KeyError as exc:
            raise StateConflictError("unknown sequence-first review") from exc

    def review_action(
        self,
        review_id: TypedId,
        action: CreatorReviewAction,
        *,
        feedback: str | None = None,
    ) -> SequenceFirstReviewRecordV1:
        del feedback
        with self._lock:
            record = self.get_review(review_id)
            if record.state != "review_ready" or self._unresolved_review_id != review_id:
                raise StateConflictError("sequence-first review is not current")
            if action is CreatorReviewAction.ACCEPT:
                head = self._bridge.accept_and_reload(
                    record.prepared,
                    record.result,
                    creator_accepted=True,
                )
                terminal = replace(
                    record,
                    state="accepted",
                    accepted_turn_id=head.accepted_turn_id,
                    active_head_after_sha256=head.active_head_sha256,
                )
                self._accepted_generation = record.generation
            elif action is CreatorReviewAction.DECLINE:
                terminal = replace(record, state="rejected")
            else:
                raise ContractValidationError(
                    "sequence-first route permits only strict Accept or Decline"
                )
            self._reviews[review_id] = terminal
            self._unresolved_review_id = None
            return terminal

    @staticmethod
    def review_payload(record: SequenceFirstReviewRecordV1) -> dict[str, object]:
        candidate = record.result.candidate
        return {
            "schema_version": "cera.sequence_first.stage6_review.v1",
            "review_id": str(record.review_id),
            "status": record.state,
            "provisional": record.state == "review_ready",
            "story_text": None if candidate is None else candidate.writer_response.story_text,
            "candidate_sha256": None if candidate is None else candidate.candidate_sha256,
            "review_binding_sha256": record.review_binding_sha256,
            "accept_enabled": record.state == "review_ready" and candidate is not None,
            "decline_enabled": record.state == "review_ready",
            "provider_calls": record.provider_calls,
            "generation": record.generation,
            "operation_evidence": SequenceFirstStage6HttpAdapter._operation_evidence(
                record
            ),
        }

    @staticmethod
    def review_decision_payload(
        review_id: TypedId,
        action: CreatorReviewAction,
        record: SequenceFirstReviewRecordV1,
    ) -> dict[str, object]:
        if record.review_id != review_id:
            raise StateConflictError("sequence-first review decision changed identity")
        return {
            "schema_version": "cera.sequence_first.stage6_review_decision.v1",
            "review_id": str(review_id),
            "creator_action": action.value,
            "status": record.state,
            "accepted_turn_id": record.accepted_turn_id,
            "generation": record.generation,
            "provider_calls": record.provider_calls,
            "review_binding_sha256": record.review_binding_sha256,
            "operation_evidence": SequenceFirstStage6HttpAdapter._operation_evidence(
                record
            ),
        }

    @staticmethod
    def _operation_evidence(record: SequenceFirstReviewRecordV1) -> dict[str, object]:
        candidate = record.result.candidate
        return {
            "schema_version": "cera.sequence_first.stage6_operation_evidence.v1",
            "virtual_model": CERA_SEQUENCE_FIRST_STAGE6_MODEL,
            "profile": SEQUENCE_FIRST_STAGE6_PROFILE,
            "exact_source_sha256": record.prepared.request.custody.exact_source_sha256,
            "accepted_head_before_sha256": (
                record.prepared.request.custody.accepted_head_sha256
            ),
            "accepted_head_after_sha256": record.active_head_after_sha256,
            "intended_sequence_sha256": (
                None
                if candidate is None
                else candidate.intended_sequence.semantic.semantic_sha256
            ),
            "writer_prose_sha256": None if candidate is None else candidate.prose_sha256,
            "realized_sequence_sha256": (
                None
                if candidate is None
                else candidate.realized_sequence.semantic.semantic_sha256
            ),
            "candidate_sha256": None if candidate is None else candidate.candidate_sha256,
            "attempt_receipts": to_primitive(record.result.attempt_receipts),
            "provider_calls": record.provider_calls,
            "provider_evidence": json.loads(record.provider_evidence_json),
            "protected_source_claim_count": len(
                record.prepared.request.semantic_input.protected_source_claims
            ),
            "accepted_presence_before": list(
                record.prepared.request.semantic_input.accepted_present_character_ids
            ),
            "accepted_presence_after": (
                None
                if candidate is None
                else list(
                    apply_presence_changes(
                        candidate.accepted_present_character_ids,
                        candidate.realized_sequence.semantic,
                    )
                )
            ),
        }

    def _validate_request(self, request: SillyTavernChatRequest) -> None:
        if request.model != self.virtual_model:
            raise StateConflictError("sequence-first adapter rejects model substitution")
        if request.cera_profile_id != SEQUENCE_FIRST_STAGE6_PROFILE:
            raise StateConflictError("sequence-first adapter rejects profile substitution")
        if request.cera_session_id != self._session_id:
            raise StateConflictError("sequence-first adapter rejects session substitution")
        if any(
            value is not None
            for value in (
                request.cera_scene_depth,
                request.cera_regeneration_key,
                request.cera_character_autonomy,
                request.cera_prompt_handling,
                request.cera_reasoning_effort,
            )
        ):
            raise StateConflictError("sequence-first request contains unsupported controls")
