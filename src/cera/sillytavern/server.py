"""Small loopback-only OpenAI-compatible HTTP service for SillyTavern."""

from __future__ import annotations

from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import re
import sys
import time
from typing import Any
from urllib.parse import unquote, urlparse

from cera.creator_review import CreatorReviewAction
from cera.ids import IdKind, TypedId
from cera.serialization import text_sha256, to_primitive

from .adapter import CeraSillyTavernAdapter
from .models import CERA_VIRTUAL_MODEL, SillyTavernChatRequest


@dataclass(frozen=True, slots=True)
class CeraSillyTavernServerConfig:
    host: str = "127.0.0.1"
    port: int = 5101

    def __post_init__(self) -> None:
        if self.host not in {"127.0.0.1", "localhost"}:
            raise ValueError("CERA development server must remain loopback-only")
        if not (0 <= self.port <= 65535):
            raise ValueError("CERA development server port is invalid")


def build_server(
    adapter: CeraSillyTavernAdapter,
    config: CeraSillyTavernServerConfig = CeraSillyTavernServerConfig(),
) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        server_version = "CERA-SillyTavern/1"

        def do_OPTIONS(self) -> None:
            if self._allowed_origin() is None:
                self.send_response(HTTPStatus.FORBIDDEN.value)
                self.end_headers()
                return
            self.send_response(HTTPStatus.NO_CONTENT.value)
            self._cors_headers()
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Access-Control-Max-Age", "600")
            self.end_headers()

        def do_GET(self) -> None:
            if self.path in {"/health", "/v1/health"}:
                self._json(
                    HTTPStatus.OK,
                    {
                        "status": "ok",
                        "service": "cera-sillytavern-development",
                        "model": CERA_VIRTUAL_MODEL,
                        "production": False,
                        "reasoner_session": adapter.reasoner_session_status,
                    },
                )
                return
            if self.path == "/v1/models":
                self._json(
                    HTTPStatus.OK,
                    {
                        "object": "list",
                        "data": [
                            {
                                "id": CERA_VIRTUAL_MODEL,
                                "object": "model",
                                "owned_by": "cera-local-development",
                            }
                        ],
                    },
                )
                return
            review_id = _review_id_from_path(self.path)
            if review_id is not None:
                try:
                    record = adapter.get_review(review_id)
                    self._json(HTTPStatus.OK, _review_payload(record))
                except Exception as exc:
                    self._error(
                        HTTPStatus.BAD_REQUEST,
                        "cera_review_failed",
                        str(exc) or "CERA review lookup failed.",
                        stage="creator_review_lookup",
                    )
                return
            self._error(HTTPStatus.NOT_FOUND, "not_found", "Unknown CERA endpoint.")

        def do_POST(self) -> None:
            review_id = _review_id_from_path(self.path, suffix="/decision")
            if review_id is not None:
                try:
                    payload = self._read_json_body(max_bytes=100_000)
                    action = CreatorReviewAction(str(payload.get("action", "")))
                    feedback = payload.get("feedback")
                    if feedback is not None and not isinstance(feedback, str):
                        raise ValueError("creator feedback must be text")
                    result = adapter.review_action(
                        review_id,
                        action,
                        feedback=feedback,
                    )
                    if action in {
                        CreatorReviewAction.ACCEPT,
                        CreatorReviewAction.FALSE_POSITIVE,
                    }:
                        artifact_id = result.publication.receipt.new_artifact_id
                        timing_getter = getattr(adapter, "get_accept_timing", None)
                        timing = (
                            timing_getter(review_id)
                            if timing_getter is not None
                            else None
                        )
                        body = {
                            "status": "accepted",
                            "creator_action": action.value,
                            "review_id": str(review_id),
                            "artifact_id": str(artifact_id),
                            "generation": result.publication.receipt.generation_after,
                            "provider_calls": 0,
                            "automatic_retries": 0,
                            "accept_to_commit_microseconds": (
                                timing.accept_to_commit_microseconds
                                if timing is not None
                                else None
                            ),
                        }
                    else:
                        body = _review_payload(result)
                    self._json(HTTPStatus.OK, body)
                except Exception as exc:
                    self._error(
                        HTTPStatus.BAD_REQUEST,
                        "cera_review_action_failed",
                        str(exc) or "CERA review action failed.",
                        stage="creator_review_action",
                    )
                return
            if self.path != "/v1/chat/completions":
                self._error(
                    HTTPStatus.NOT_FOUND,
                    "not_found",
                    "Unknown CERA endpoint.",
                )
                return
            try:
                payload = self._read_json_body(max_bytes=2_000_000)
                request = SillyTavernChatRequest.from_mapping(payload)
                reply = adapter.complete(request)
                completion_id = (
                    "chatcmpl-cera-"
                    + text_sha256(
                        f"{reply.request_id}|{reply.artifact_id}|{reply.generation}"
                    )[:24]
                )
                self._json(
                    HTTPStatus.OK,
                    {
                        "id": completion_id,
                        "object": "chat.completion",
                        "created": int(time.time()),
                        "model": CERA_VIRTUAL_MODEL,
                        "choices": [
                            {
                                "index": 0,
                                "message": {
                                    "role": "assistant",
                                    "content": reply.prose,
                                },
                                "finish_reason": "stop",
                            }
                        ],
                        "usage": {
                            "prompt_tokens": 0,
                            "completion_tokens": 0,
                            "total_tokens": 0,
                        },
                        "cera": {
                            "request_id": reply.request_id,
                            "artifact_id": reply.artifact_id,
                            "generation": reply.generation,
                            "provider_calls": reply.provider_calls,
                            "exact_replay": reply.exact_replay,
                            "provisional": reply.provisional_review_id is not None,
                            "provisional_review_id": reply.provisional_review_id,
                            "candidate_id": reply.candidate_id,
                            "review_status": reply.review_status,
                            "review_url": (
                                f"/v1/cera/reviews/{reply.provisional_review_id}"
                                if reply.provisional_review_id is not None
                                else None
                            ),
                        },
                    },
                )
            except Exception as exc:
                envelope = getattr(exc, "envelope", None)
                stage = getattr(envelope, "stage", "sillytavern_adapter")
                code = getattr(
                    getattr(envelope, "error_code", None),
                    "value",
                    "cera_turn_failed",
                )
                message = (
                    getattr(envelope, "message", None)
                    or str(exc)
                    or "CERA turn failed."
                )
                self._error(
                    HTTPStatus.BAD_REQUEST,
                    code,
                    message,
                    stage=stage,
                )

        def log_message(self, format: str, *args: Any) -> None:
            return

        def _error(
            self,
            status: HTTPStatus,
            code: str,
            message: str,
            *,
            stage: str = "http",
        ) -> None:
            print(
                json.dumps(
                    {
                        "event": "cera_sillytavern_error",
                        "http_status": status.value,
                        "code": code,
                        "stage": stage,
                        "message": message,
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                file=sys.stderr,
                flush=True,
            )
            self._json(
                status,
                {
                    "error": {
                        "message": message,
                        "type": "cera_error",
                        "code": code,
                        "stage": stage,
                        "retryable": False,
                        "fallback_used": False,
                    }
                },
            )

        def _json(self, status: HTTPStatus, payload: Any) -> None:
            body = (
                json.dumps(
                    payload,
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                + "\n"
            ).encode("utf-8")
            self.send_response(status.value)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self._cors_headers()
            self.end_headers()
            self.wfile.write(body)

        def _allowed_origin(self) -> str | None:
            origin = self.headers.get("Origin")
            if origin is None:
                return None
            return (
                origin
                if re.fullmatch(r"https?://(?:127\.0\.0\.1|localhost):\d{1,5}", origin)
                else None
            )

        def _cors_headers(self) -> None:
            origin = self._allowed_origin()
            if origin is not None:
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Vary", "Origin")

        def _read_json_body(self, *, max_bytes: int) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > max_bytes:
                raise ValueError("request body length is invalid")
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError("request body must be a JSON object")
            return payload

    return ThreadingHTTPServer((config.host, config.port), Handler)


def _review_id_from_path(
    path: str,
    *,
    suffix: str = "",
) -> TypedId | None:
    route = urlparse(path).path
    prefix = "/v1/cera/reviews/"
    if not route.startswith(prefix) or not route.endswith(suffix):
        return None
    raw = route[len(prefix) :]
    if suffix:
        raw = raw[: -len(suffix)]
    if not raw or "/" in raw:
        return None
    return TypedId.parse(unquote(raw), IdKind.REVIEW_PACKET)


def _review_payload(record) -> dict[str, Any]:
    assessment = (
        to_primitive(record.assessment) if record.assessment is not None else None
    )
    package = record.prepared_package
    return {
        "schema_version": "cera.sillytavern_creator_review_response.v1",
        "review_id": str(record.review_id),
        "state": record.state.value,
        "provisional": record.state.value not in {"accepted", "rejected"},
        "candidate_text": record.candidate_text,
        "candidate_sha256": record.candidate_sha256,
        "candidate_text_sha256": record.candidate_text_sha256,
        "sequence_plan": list(record.sequence_beats),
        "sequence_plan_sha256": record.sequence_plan_sha256,
        "speaker_marks": _review_speaker_marks(record),
        "assessment": assessment,
        "accept_enabled": (
            assessment is not None
            and assessment["publication_eligibility"] == "accept_allowed"
            and package is not None
            and record.state.value == "review_ready"
        ),
        "prepared_package_id": (
            str(package.package_id) if package is not None else None
        ),
        "creator_action": (
            record.creator_action.value if record.creator_action is not None else None
        ),
        "feedback_required": record.state.value == "awaiting_feedback",
    }


_VISIBLE_QUOTED_SPEECH = re.compile(r'"[^"\r\n]+"|“[^”\r\n]+”')
_CERA_SPEAKERS = frozenset({"hana", "sakura", "mia", "enne", "tomi", "aoi", "yuuni"})


def _review_speaker_marks(record) -> list[dict[str, str]]:
    """Return presentation-only speaker hints from validated dialogue spans.

    Canonical prose remains presentation-neutral.  The browser receives only
    exact quotations already visible in the candidate and a bounded cast key;
    it never receives hidden character state through this view.
    """

    candidate = record.candidate_text
    raw_result = getattr(record, "live_result_json", None)
    if not candidate or not raw_result:
        return []
    try:
        result = json.loads(raw_result)
        spans = result["composer"]["manifest"]["character_spans"]
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return []
    if not isinstance(spans, list):
        return []
    marked: list[tuple[int, dict[str, str]]] = []
    for span in spans:
        if not isinstance(span, dict) or span.get("kind") != "dialogue":
            continue
        owner = str(span.get("owner_id", ""))
        match = re.fullmatch(r"character:([a-z]+)_hanezawa", owner)
        if match is None or match.group(1) not in _CERA_SPEAKERS:
            continue
        start = span.get("start")
        end = span.get("end")
        if (
            not isinstance(start, int)
            or isinstance(start, bool)
            or not isinstance(end, int)
            or isinstance(end, bool)
            or start < 0
            or end <= start
            or end > len(candidate)
        ):
            continue
        for quotation in _VISIBLE_QUOTED_SPEECH.finditer(candidate[start:end]):
            marked.append(
                (
                    start + quotation.start(),
                    {
                        "speaker": match.group(1),
                        "kind": "dialogue",
                        "quote": quotation.group(0),
                    },
                )
            )
    marked.sort(key=lambda value: value[0])
    return [value for _, value in marked]
