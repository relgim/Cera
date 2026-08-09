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
from typing import Any
from urllib.parse import unquote, urlparse
from uuid import uuid4

from cera.errors import ContractValidationError, StateConflictError
from cera.serialization import canonical_sha256, text_sha256, to_primitive

from .contracts import RecordingStatus, SceneRoute
from .http_contracts import (
    PI_SCENE_ADULT_MODEL,
    PI_SCENE_ORDINARY_MODEL,
    PI_SCENE_PROFILE,
    LeanSceneRequestControlsV1,
    PiSceneChatRequestV1,
    parse_chat_request,
)
from .readable_debug import ReadablePiSceneDebugLog
from .request_journal import (
    PiSceneRequestJournal,
    RequestReplayPendingError,
    build_request_binding,
)
from .review_store import (
    LeanDecisionResultV1,
    LeanReviewRecordV1,
    LeanReviewState,
    LeanSceneTurnInputV1,
)
from .runtime import LeanPiSceneCoordinator

ContextProvider = Callable[[SceneRoute, str, Sequence[Mapping[str, str]]], LeanSceneTurnInputV1]
RequestContextProvider = Callable[
    [SceneRoute, str, Sequence[Mapping[str, str]], LeanSceneRequestControlsV1],
    LeanSceneTurnInputV1,
]

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

    @property
    def status(self) -> dict[str, Any]:
        return {
            "mode": "pi_scene_full_model",
            "active": True,
            "profile_id": PI_SCENE_PROFILE,
            "models": [PI_SCENE_ORDINARY_MODEL, PI_SCENE_ADULT_MODEL],
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
        request = self._parse_chat_request(payload)
        turn = self._turn_for_request(request)
        binding = build_request_binding(
            payload=payload,
            session_id=request.controls.session_id,
            world_id=turn.world_id,
            branch_id=turn.branch_id,
            route=request.route,
            controls=request.controls,
        )
        request_journal = self._durable_request_journal()
        resolution = request_journal.begin(binding)
        if resolution.replayed:
            if resolution.terminal_response is None:
                raise StateConflictError("Pi Scene terminal replay omitted its response")
            return dict(resolution.terminal_response)
        if resolution.review_progress is not None:
            review = self._recover_journal_review(
                request=request,
                turn=turn,
                progress=resolution.review_progress,
            )
            response = self._completion_response_with_debug(review)
            request_journal.complete(binding, response)
            return response
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
                review = (
                    self.coordinator.start_ordinary(turn)
                    if request.route is SceneRoute.ORDINARY
                    else self.coordinator.start_adult(turn)
                )
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
            request_journal.complete(binding, response)
        except Exception as exc:
            if review.accepted_receipt is not None:
                raise PiSceneCommittedStateError(
                    "Pi Scene accepted story state but could not terminalize "
                    "its request replay journal"
                ) from exc
            raise
        return response

    def _completion_response_with_debug(
        self,
        review: LeanReviewRecordV1,
    ) -> dict[str, Any]:
        response = self._completion_payload(review)
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
                    response["cera"]["debug_log_path"] = str(debug_entry)
            except Exception:
                response["cera"]["operational_warnings"] = [
                    "readable_debug_write_failed"
                ]
        return response

    def get_review(self, review_id: str) -> dict[str, Any]:
        return self.review_payload(self.coordinator.get_review(review_id))

    def decide(self, review_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
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
            if (
                decision is not None
                and decision.review.accepted_receipt is not None
            ):
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
                result.setdefault("operational_warnings", []).append(
                    "readable_debug_write_failed"
                )
        return result

    def review_payload(self, review: LeanReviewRecordV1) -> dict[str, Any]:
        candidate = review.candidate
        status = (
            None
            if review.accepted_receipt is None
            else self.coordinator.store.recording_status(review.accepted_receipt).value
        )
        validation = review.semantic_validation
        validation_payload = (
            None
            if validation is None
            else {
                "binding_sha256": validation.binding_sha256,
                "verdict": validation.verdict.verdict.value,
                "automatic_repair_eligible": validation.verdict.automatic_repair_eligible,
                "conflict": (
                    None
                    if validation.verdict.conflict is None
                    else to_primitive(validation.verdict.conflict)
                ),
                "review_flags": [
                    to_primitive(value) for value in validation.verdict.review_flags
                ],
            }
        )
        rejected = validation is not None and validation.verdict.verdict.value == "reject"
        provisional_canon = (
            review.accepted_receipt is not None
            and review.accepted_receipt.creator_action == "provisional_accept"
        )
        return {
            "schema_version": "cera.pi_scene.review.v1",
            "review_id": review.review_id,
            "state": review.state,
            "provisional": review.state == LeanReviewState.REVIEW_READY,
            "route": candidate.route.value,
            "story_text": candidate.story_text,
            "candidate_id": candidate.candidate_id,
            "candidate_sha256": candidate.candidate_sha256,
            "primary_authority_kind": candidate.primary_authority_kind,
            "primary_authority_sha256": candidate.primary_authority_sha256,
            "warnings": [to_primitive(value) for value in candidate.warnings],
            "warnings_block_accept": False,
            "recording_status": status,
            "story_state_committed": review.accepted_receipt is not None,
            "canon_status": (
                "provisional"
                if provisional_canon
                else "accepted"
                if review.accepted_receipt is not None
                else "unaccepted"
            ),
            "semantic_validation": validation_payload,
            "request_controls": (
                None
                if review.turn_input.request_controls is None
                else to_primitive(review.turn_input.request_controls)
            ),
            "creator_guidance": (
                None
                if review.creator_guidance is None
                else to_primitive(review.creator_guidance)
            ),
            "accept_enabled": (
                review.state == LeanReviewState.REVIEW_READY and not rejected
            ),
            "provisional_accept_enabled": (
                review.state == LeanReviewState.REVIEW_READY and rejected
            ),
            "decline_enabled": review.state == LeanReviewState.REVIEW_READY,
            "regenerate_enabled": review.state == LeanReviewState.REVIEW_READY,
            "replan_enabled": (
                review.state == LeanReviewState.REVIEW_READY
                and candidate.route is SceneRoute.ORDINARY
            ),
            "repair_recording_enabled": status in {
                RecordingStatus.PROJECTION_PENDING.value,
                RecordingStatus.PENDING_REPAIR.value,
            },
            "provider_operations": {
                "planner": review.result.planner_provider_operations,
                "writer": review.result.writer_provider_operations,
                "recorder": (
                    0
                    if review.recording_attempt is None
                    else review.recording_attempt.provider_operations
                ),
            },
        }

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
                else self._completion_payload(decision.successor)
            ),
            "operational_warnings": list(decision.operational_warnings),
        }
        if decision.review.accepted_receipt is not None:
            body["accepted_receipt_sha256"] = (
                decision.review.accepted_receipt.receipt_sha256
            )
            body["accepted_turn_id"] = (
                decision.review.accepted_receipt.accepted_turn_id
            )
        return body

    @staticmethod
    def _completion_payload(review: LeanReviewRecordV1) -> dict[str, Any]:
        candidate = review.candidate
        committed = review.accepted_receipt is not None
        validation = review.semantic_validation
        rejected = validation is not None and validation.verdict.verdict.value == "reject"
        recording_status = (
            None
            if review.recording_attempt is None
            else review.recording_attempt.status.value
        )
        provisional_canon = (
            review.accepted_receipt is not None
            and review.accepted_receipt.creator_action == "provisional_accept"
        )
        review_status = (
            "accepted"
            if committed
            else "validation_rejected"
            if rejected
            else "review_ready"
        )
        return {
            "id": f"chatcmpl-cera-{candidate.candidate_sha256[:24]}",
            "object": "chat.completion",
            "created": review.created_unix_seconds,
            "model": (
                PI_SCENE_ORDINARY_MODEL
                if candidate.route is SceneRoute.ORDINARY
                else PI_SCENE_ADULT_MODEL
            ),
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": candidate.story_text},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            "cera": {
                "profile_id": PI_SCENE_PROFILE,
                "route_mode": candidate.route.value,
                "provisional": not committed,
                "status": review_status,
                "story_state_committed": committed,
                "canon_status": (
                    "provisional" if provisional_canon else "accepted" if committed else None
                ),
                "provisional_review_id": None if committed else review.review_id,
                "review_url": f"/v1/cera/reviews/{review.review_id}",
                "candidate_id": candidate.candidate_id,
                "generation": candidate.generation,
                "warnings": [to_primitive(value) for value in candidate.warnings],
                "warnings_block_accept": False,
                "accepted_turn_id": (
                    None
                    if review.accepted_receipt is None
                    else review.accepted_receipt.accepted_turn_id
                ),
                "accepted_receipt_sha256": (
                    None
                    if review.accepted_receipt is None
                    else review.accepted_receipt.receipt_sha256
                ),
                "recording_status": recording_status,
                "semantic_validation": (
                    None
                    if validation is None
                    else {
                        "binding_sha256": validation.binding_sha256,
                        "verdict": validation.verdict.verdict.value,
                        "automatic_repair_eligible": (
                            validation.verdict.automatic_repair_eligible
                        ),
                        "conflict": (
                            None
                            if validation.verdict.conflict is None
                            else to_primitive(validation.verdict.conflict)
                        ),
                        "review_flags": [
                            to_primitive(value)
                            for value in validation.verdict.review_flags
                        ],
                    }
                ),
                "request_controls": (
                    None
                    if review.turn_input.request_controls is None
                    else to_primitive(review.turn_input.request_controls)
                ),
                "creator_guidance": (
                    None
                    if review.creator_guidance is None
                    else to_primitive(review.creator_guidance)
                ),
                "operational_warnings": [],
                "provider_operations": {
                    "planner": review.result.planner_provider_operations,
                    "writer": review.result.writer_provider_operations,
                },
            },
        }

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
        self.request_journal = PiSceneRequestJournal(root / "http_request_journal")
        return self.request_journal

    @staticmethod
    def _journal_review_progress(review: LeanReviewRecordV1) -> dict[str, Any]:
        receipt = review.accepted_receipt
        recording_status = (
            None
            if review.recording_attempt is None
            else review.recording_attempt.status.value
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
            "controls_sha256": canonical_sha256(
                to_primitive(review.turn_input.request_controls)
            ),
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
            raise StateConflictError(
                "Pi Scene regeneration requires the current provisional review"
            )
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
) -> dict[str, Any]:
    if technical_detail is not None and not technical_detail.strip():
        technical_detail = None
    envelope: dict[str, Any] = {
        "schema_version": "cera.error.v1",
        "error_code": error_code,
        "message": message,
        "trace_id": trace_id or f"trace:{uuid4().hex}",
        "request_id": None,
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
        "provider_operation_submitted": False,
        "accepted_state_changed": story_state_committed,
        "next_action": next_action,
        "debug_log_path": debug_log_path,
    }
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
                            for model in (PI_SCENE_ORDINARY_MODEL, PI_SCENE_ADULT_MODEL)
                        ],
                    },
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
            technical_detail = f"{type(exc).__name__}: {exc}"
            if isinstance(exc, PiSceneCommittedStateError):
                status = HTTPStatus.INTERNAL_SERVER_ERROR
                code = "CERA_DELIVERY_AFTER_COMMIT_FAILED"
                message = "Accepted story state was retained, but response delivery failed."
                committed = True
                retry_mode = "manual_after_review"
                next_action = "check_current_review_before_retrying"
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
