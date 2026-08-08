"""Authenticated loopback HTTP surface for the isolated Pi Scene profile."""

from __future__ import annotations

from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import json
import re
import time
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import unquote, urlparse

from cera.errors import ContractValidationError, StateConflictError
from cera.serialization import text_sha256, to_primitive

from .contracts import RecordingStatus, SceneRoute
from .runtime import (
    LeanDecisionResultV1,
    LeanPiSceneCoordinator,
    LeanReviewRecordV1,
    LeanReviewState,
    LeanSceneTurnInputV1,
)
from .readable_debug import ReadablePiSceneDebugLog


PI_SCENE_ORDINARY_MODEL = "cera-pi-scene-ordinary"
PI_SCENE_ADULT_MODEL = "cera-pi-scene-adult"
PI_SCENE_PROFILE = "cera.pi_scene.lean.v1"


ContextProvider = Callable[[SceneRoute, str, Sequence[Mapping[str, str]]], LeanSceneTurnInputV1]


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
        session_id: str,
        context_provider: ContextProvider,
        readable_debug: ReadablePiSceneDebugLog | None = None,
    ) -> None:
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,95}", session_id):
            raise ContractValidationError("Pi Scene HTTP session identity is invalid")
        self.coordinator = coordinator
        self.session_id = session_id
        self.context_provider = context_provider
        self.readable_debug = readable_debug

    @property
    def status(self) -> dict[str, Any]:
        return {
            "mode": "pi_scene_lean",
            "active": True,
            "profile_id": PI_SCENE_PROFILE,
            "models": [PI_SCENE_ORDINARY_MODEL, PI_SCENE_ADULT_MODEL],
            "creator_review_required": True,
            "validator_required": False,
            "reader_required": False,
            "ted_restrictions": "warn_only",
            "writer_session": "accepted_lineage_or_fresh_rehydration",
            "python_accepted_state_authoritative": True,
            "automatic_retry": False,
            "fallback": False,
        }

    def complete(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        route, messages, source = self._parse_chat_request(payload)
        turn = self.context_provider(route, source, messages)
        review = (
            self.coordinator.start_ordinary(turn)
            if route is SceneRoute.ORDINARY
            else self.coordinator.start_adult(turn)
        )
        payload = self._completion_payload(review)
        if self.readable_debug is not None:
            self.readable_debug.write(
                stage="creator-review-ready",
                identity=review.review_id,
                sections={
                    "Exact user input": review.turn_input.exact_user_source,
                    "Review state": self.review_payload(review),
                    "Visible provisional prose": review.candidate.story_text,
                },
            )
        return payload

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
        if action == "accept":
            decision = self.coordinator.accept(review_id)
        elif action == "decline":
            decision = self.coordinator.decline(review_id)
        elif action == "regenerate":
            decision = self.coordinator.regenerate(
                review_id,
                feedback=feedback,
                force_rehydrate=force_rehydrate,
            )
        elif action == "replan":
            decision = self.coordinator.replan(review_id, feedback=feedback)
        elif action == "repair_recording":
            decision = self.coordinator.repair_recording(review_id)
        else:
            raise ContractValidationError("unknown Pi Scene creator action")
        result = self.decision_payload(action, decision)
        if self.readable_debug is not None:
            self.readable_debug.write(
                stage="creator-decision",
                identity=review_id,
                sections={
                    "Creator action": action,
                    "Creator feedback": feedback,
                    "Persisted review state": result,
                },
            )
        return result

    def review_payload(self, review: LeanReviewRecordV1) -> dict[str, Any]:
        candidate = review.candidate
        status = (
            None
            if review.accepted_receipt is None
            else self.coordinator.store.recording_status(review.accepted_receipt).value
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
            "accept_enabled": review.state == LeanReviewState.REVIEW_READY,
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
            "creator_action": action,
            "review": self.review_payload(decision.review),
            "successor": (
                None
                if decision.successor is None
                else self._completion_payload(decision.successor)
            ),
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
        return {
            "id": f"chatcmpl-cera-{candidate.candidate_sha256[:24]}",
            "object": "chat.completion",
            "created": int(time.time()),
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
                "provisional": True,
                "provisional_review_id": review.review_id,
                "review_url": f"/v1/cera/reviews/{review.review_id}",
                "candidate_id": candidate.candidate_id,
                "generation": candidate.generation,
                "warnings": [to_primitive(value) for value in candidate.warnings],
                "warnings_block_accept": False,
                "recording_status": None,
                "provider_operations": {
                    "planner": review.result.planner_provider_operations,
                    "writer": review.result.writer_provider_operations,
                },
            },
        }

    def _parse_chat_request(
        self,
        payload: Mapping[str, Any],
    ) -> tuple[SceneRoute, tuple[Mapping[str, str], ...], str]:
        if not isinstance(payload, Mapping):
            raise ContractValidationError("chat completion body must be an object")
        model = str(payload.get("model", ""))
        if model == PI_SCENE_ORDINARY_MODEL:
            route = SceneRoute.ORDINARY
        elif model == PI_SCENE_ADULT_MODEL:
            route = SceneRoute.ADULT
        else:
            raise ContractValidationError("Pi Scene rejects model substitution")
        if payload.get("stream", False) is not False:
            raise ContractValidationError("Pi Scene requires non-streaming requests")
        if payload.get("cera_profile_id") != PI_SCENE_PROFILE:
            raise ContractValidationError("Pi Scene rejects profile substitution")
        if payload.get("cera_session_id") != self.session_id:
            raise ContractValidationError("Pi Scene rejects session substitution")
        raw_messages = payload.get("messages")
        if not isinstance(raw_messages, list) or not raw_messages:
            raise ContractValidationError("Pi Scene requires chat messages")
        messages: list[Mapping[str, str]] = []
        for value in raw_messages:
            if not isinstance(value, Mapping):
                raise ContractValidationError("Pi Scene chat message is invalid")
            role = value.get("role")
            content = value.get("content")
            if role not in {"system", "user", "assistant"} or not isinstance(content, str):
                raise ContractValidationError("Pi Scene accepts text chat messages only")
            messages.append({"role": str(role), "content": content})
        sources = [value["content"] for value in messages if value["role"] == "user"]
        if not sources:
            raise ContractValidationError("Pi Scene request has no user source")
        return route, tuple(messages), sources[-1]


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
                self._json(HTTPStatus.UNAUTHORIZED, {"error": {"code": "unauthorized"}})
                return
            if not self._origin_allowed(optional=True):
                self._json(HTTPStatus.FORBIDDEN, {"error": {"code": "origin_forbidden"}})
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
            self._json(HTTPStatus.NOT_FOUND, {"error": {"code": "not_found"}})

        def do_POST(self) -> None:
            if not self._authorized():
                self._json(HTTPStatus.UNAUTHORIZED, {"error": {"code": "unauthorized"}})
                return
            if not self._origin_allowed(optional=True):
                self._json(HTTPStatus.FORBIDDEN, {"error": {"code": "origin_forbidden"}})
                return
            content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
            if content_type != "application/json":
                self._json(
                    HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                    {"error": {"code": "json_content_type_required"}},
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
            self._json(HTTPStatus.NOT_FOUND, {"error": {"code": "not_found"}})

        def _guarded(self, operation: Callable[[], Mapping[str, Any]]) -> None:
            try:
                self._json(HTTPStatus.OK, operation())
            except Exception as exc:
                self._error(exc)

        def _error(self, exc: Exception) -> None:
            self._json(
                HTTPStatus.BAD_REQUEST,
                {
                    "error": {
                        "type": "cera_error",
                        "code": type(exc).__name__,
                        "message": str(exc) or "Pi Scene request failed",
                        "retryable": False,
                        "fallback_used": False,
                    }
                },
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
