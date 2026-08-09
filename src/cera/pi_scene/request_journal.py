"""Durable pre-dispatch custody for Pi Scene chat-completion requests.

The journal is deliberately transport-only.  It binds canonical request bytes
to the Python-owned session, world, branch, route, and control envelope without
interpreting story meaning.  A terminal entry can be replayed byte-for-byte at
the JSON value level; an incomplete entry always fails closed so a process
restart cannot silently submit the same provider operation twice.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Any, ClassVar
from uuid import uuid4

from cera.errors import ContractValidationError, StateConflictError
from cera.serialization import (
    bytes_sha256,
    canonical_bytes,
    canonical_sha256,
    domain_sha256,
    re_is_sha256,
    text_sha256,
    to_primitive,
)

from .contracts import SceneRoute
from .http_contracts import LeanSceneRequestControlsV1, LeanSceneRequestControlsV2


class RequestReplayPendingError(StateConflictError):
    """An identical request has non-terminal custody and cannot be dispatched."""

    def __init__(self, message: str, *, request_id: str) -> None:
        super().__init__(message)
        self.request_id = request_id


@dataclass(frozen=True, slots=True)
class PiSceneRequestBindingV1:
    """Exact deterministic identity of one submitted HTTP request."""

    SCHEMA_VERSION: ClassVar[str] = "cera.pi_scene.http_request_binding.v1"

    schema_version: str
    request_id: str
    normalized_request_sha256: str
    normalized_request_size_bytes: int
    session_id: str
    world_id: str
    branch_id: str
    route: SceneRoute
    route_intent: str
    controls: Mapping[str, Any]
    controls_sha256: str

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("Pi Scene request-binding schema changed")
        if not re.fullmatch(r"request-[a-f0-9]{64}", self.request_id):
            raise ContractValidationError("Pi Scene request identity is invalid")
        if not re_is_sha256(self.normalized_request_sha256):
            raise ContractValidationError("Pi Scene normalized request hash is invalid")
        if (
            type(self.normalized_request_size_bytes) is not int
            or self.normalized_request_size_bytes < 2
        ):
            raise ContractValidationError("Pi Scene normalized request size is invalid")
        for field_name in ("session_id", "world_id", "branch_id"):
            value = getattr(self, field_name)
            if type(value) is not str or not value.strip():
                raise ContractValidationError(f"Pi Scene request {field_name} is empty")
        if type(self.route) is not SceneRoute:
            raise ContractValidationError("Pi Scene request route is invalid")
        if self.route_intent not in {"automatic", "explicit"}:
            raise ContractValidationError("Pi Scene request route intent is invalid")
        if not isinstance(self.controls, Mapping):
            raise ContractValidationError("Pi Scene request controls are invalid")
        if not re_is_sha256(self.controls_sha256):
            raise ContractValidationError("Pi Scene request controls hash is invalid")
        if canonical_sha256(self.controls) != self.controls_sha256:
            raise ContractValidationError("Pi Scene request controls binding changed")
        expected = _request_id(self._identity_payload())
        if self.request_id != expected:
            raise ContractValidationError("Pi Scene request identity binding changed")

    def _identity_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "normalized_request_sha256": self.normalized_request_sha256,
            "normalized_request_size_bytes": self.normalized_request_size_bytes,
            "session_id": self.session_id,
            "world_id": self.world_id,
            "branch_id": self.branch_id,
            "route": self.route.value,
            "route_intent": self.route_intent,
            "controls": dict(self.controls),
            "controls_sha256": self.controls_sha256,
        }

    def to_payload(self) -> dict[str, Any]:
        return {"request_id": self.request_id, **self._identity_payload()}


@dataclass(frozen=True, slots=True)
class RequestJournalResolutionV1:
    request_id: str
    replayed: bool
    terminal_response: Mapping[str, Any] | None
    review_progress: Mapping[str, Any] | None


def build_request_binding(
    *,
    payload: Mapping[str, Any],
    session_id: str,
    world_id: str,
    branch_id: str,
    route: SceneRoute,
    controls: LeanSceneRequestControlsV1 | LeanSceneRequestControlsV2,
) -> PiSceneRequestBindingV1:
    """Build an identity from exact canonical request bytes and custody fields."""

    request_bytes = canonical_bytes(payload)
    controls_payload = to_primitive(controls)
    identity = {
        "schema_version": PiSceneRequestBindingV1.SCHEMA_VERSION,
        "normalized_request_sha256": bytes_sha256(request_bytes),
        "normalized_request_size_bytes": len(request_bytes),
        "session_id": session_id,
        "world_id": world_id,
        "branch_id": branch_id,
        "route": route.value,
        "route_intent": _route_intent(payload),
        "controls": controls_payload,
        "controls_sha256": canonical_sha256(controls_payload),
    }
    return PiSceneRequestBindingV1(
        schema_version=PiSceneRequestBindingV1.SCHEMA_VERSION,
        request_id=_request_id(identity),
        normalized_request_sha256=identity["normalized_request_sha256"],
        normalized_request_size_bytes=identity["normalized_request_size_bytes"],
        session_id=session_id,
        world_id=world_id,
        branch_id=branch_id,
        route=route,
        route_intent=identity["route_intent"],
        controls=controls_payload,
        controls_sha256=identity["controls_sha256"],
    )


class PiSceneRequestJournal:
    """Atomic, restart-safe request replay journal under one runtime root."""

    SCHEMA_VERSION = "cera.pi_scene.http_request_journal.v1"

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()

    def begin(self, binding: PiSceneRequestBindingV1) -> RequestJournalResolutionV1:
        """Create durable pending custody or return the exact terminal response."""

        path = self._entry_path(binding)
        with self._lock:
            if path.exists():
                return self._resolve_existing(path, binding)
            with self._claim(path):
                if path.exists():
                    return self._resolve_existing(path, binding)
                _atomic_write_json(path, self._pending_entry(binding))
                # Read back the exact durable bytes before any provider is allowed.
                self._read_entry(path, expected=binding)
                return RequestJournalResolutionV1(
                    request_id=binding.request_id,
                    replayed=False,
                    terminal_response=None,
                    review_progress=None,
                )

    def bind_progress(
        self,
        binding: PiSceneRequestBindingV1,
        review_progress: Mapping[str, Any],
    ) -> None:
        """Persist exact durable operation custody before response rendering."""

        progress = to_primitive(review_progress)
        if not isinstance(progress, dict):
            raise ContractValidationError("Pi Scene review progress must be an object")
        _validate_review_progress(progress, binding)
        path = self._entry_path(binding)
        with self._lock, self._claim(path):
            if not path.exists():
                raise StateConflictError("Pi Scene review progress lacks pending custody")
            current = self._read_entry(path, expected=binding)
            if current["status"] in {"progressed", "terminal"}:
                if current["review_progress"] != progress:
                    raise StateConflictError("Pi Scene request review progress changed")
                return
            body = {
                "schema_version": self.SCHEMA_VERSION,
                "binding": binding.to_payload(),
                "status": "progressed",
                "pre_dispatch_journal_sha256": self._pending_entry(binding)["journal_sha256"],
                "review_progress": progress,
                "terminal_response": None,
                "terminal_response_sha256": None,
            }
            progressed = {**body, "journal_sha256": canonical_sha256(body)}
            _atomic_write_json(path, progressed)
            self._read_entry(path, expected=binding)

    def bind_review(
        self,
        binding: PiSceneRequestBindingV1,
        review_progress: Mapping[str, Any],
    ) -> None:
        """Historical ordinary-review spelling retained for compatibility."""

        self.bind_progress(binding, review_progress)

    def complete(
        self,
        binding: PiSceneRequestBindingV1,
        response: Mapping[str, Any],
    ) -> None:
        """Atomically attach one immutable terminal response to pending custody."""

        response_payload = to_primitive(response)
        if not isinstance(response_payload, dict):
            raise ContractValidationError("Pi Scene terminal response must be an object")
        path = self._entry_path(binding)
        with self._lock, self._claim(path):
            if not path.exists():
                raise StateConflictError("Pi Scene request terminalization lacks pending custody")
            current = self._read_entry(path, expected=binding)
            if current["status"] == "terminal":
                if current["terminal_response"] != response_payload:
                    raise StateConflictError("Pi Scene terminal request response changed")
                return
            if current["status"] != "progressed":
                raise StateConflictError("Pi Scene terminal response lacks durable review progress")
            body = {
                "schema_version": self.SCHEMA_VERSION,
                "binding": binding.to_payload(),
                "status": "terminal",
                "pre_dispatch_journal_sha256": self._pending_entry(binding)["journal_sha256"],
                "review_progress": current["review_progress"],
                "terminal_response": response_payload,
                "terminal_response_sha256": canonical_sha256(response_payload),
            }
            terminal = {**body, "journal_sha256": canonical_sha256(body)}
            _atomic_write_json(path, terminal)
            self._read_entry(path, expected=binding)

    def inspect(self, binding: PiSceneRequestBindingV1) -> Mapping[str, Any]:
        """Return a validated copy for diagnostics and provider-free tests."""

        path = self._entry_path(binding)
        with self._lock:
            if not path.exists():
                raise StateConflictError("Pi Scene request journal entry does not exist")
            return dict(self._read_entry(path, expected=binding))

    def entry_path(self, binding: PiSceneRequestBindingV1) -> Path:
        """Expose the deterministic local path for operational evidence only."""

        return self._entry_path(binding)

    def _pending_entry(self, binding: PiSceneRequestBindingV1) -> dict[str, Any]:
        body = {
            "schema_version": self.SCHEMA_VERSION,
            "binding": binding.to_payload(),
            "status": "pending",
            "pre_dispatch_journal_sha256": None,
            "review_progress": None,
            "terminal_response": None,
            "terminal_response_sha256": None,
        }
        return {**body, "journal_sha256": canonical_sha256(body)}

    def _resolve_existing(
        self,
        path: Path,
        binding: PiSceneRequestBindingV1,
    ) -> RequestJournalResolutionV1:
        entry = self._read_entry(path, expected=binding)
        if entry["status"] == "terminal":
            return RequestJournalResolutionV1(
                request_id=binding.request_id,
                replayed=True,
                terminal_response=dict(entry["terminal_response"]),
                review_progress=dict(entry["review_progress"]),
            )
        if entry["status"] == "progressed":
            return RequestJournalResolutionV1(
                request_id=binding.request_id,
                replayed=False,
                terminal_response=None,
                review_progress=dict(entry["review_progress"]),
            )
        raise RequestReplayPendingError(
            "Pi Scene identical request has pending pre-dispatch custody; "
            "provider redispatch is blocked",
            request_id=binding.request_id,
        )

    def _entry_path(self, binding: PiSceneRequestBindingV1) -> Path:
        protected = (
            "PROTECTED_ADULT"
            if binding.route is SceneRoute.ADULT
            else "PROTECTED_AUTO"
            if binding.route_intent == "automatic"
            else "ORDINARY"
        )
        path = (
            self.root
            / protected
            / f"s-{text_sha256(binding.session_id)[:16]}"
            / f"w-{text_sha256(binding.world_id)[:16]}"
            / f"b-{text_sha256(binding.branch_id)[:16]}"
            / f"{binding.request_id}.json"
        ).resolve()
        if not path.is_relative_to(self.root):
            raise ContractValidationError("Pi Scene request journal escaped its root")
        return path

    def _read_entry(
        self,
        path: Path,
        *,
        expected: PiSceneRequestBindingV1,
    ) -> dict[str, Any]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise StateConflictError("Pi Scene request journal is unreadable") from exc
        required = {
            "schema_version",
            "binding",
            "status",
            "pre_dispatch_journal_sha256",
            "review_progress",
            "terminal_response",
            "terminal_response_sha256",
            "journal_sha256",
        }
        if not isinstance(payload, dict) or set(payload) != required:
            raise StateConflictError("Pi Scene request journal shape changed")
        if payload["schema_version"] != self.SCHEMA_VERSION:
            raise StateConflictError("Pi Scene request journal schema changed")
        body = {key: payload[key] for key in required if key != "journal_sha256"}
        if payload["journal_sha256"] != canonical_sha256(body):
            raise StateConflictError("Pi Scene request journal integrity changed")
        recovered = _binding_from_payload(payload["binding"])
        if recovered != expected:
            raise StateConflictError("Pi Scene request journal binding changed")
        status = payload["status"]
        pre_dispatch_sha = payload["pre_dispatch_journal_sha256"]
        review_progress = payload["review_progress"]
        response = payload["terminal_response"]
        response_sha = payload["terminal_response_sha256"]
        if status == "pending":
            if (
                pre_dispatch_sha is not None
                or review_progress is not None
                or response is not None
                or response_sha is not None
            ):
                raise StateConflictError("Pi Scene pending journal contains a response")
        elif status == "progressed":
            expected_pending_sha = self._pending_entry(expected)["journal_sha256"]
            if pre_dispatch_sha != expected_pending_sha:
                raise StateConflictError("Pi Scene pre-dispatch custody binding changed")
            _validate_review_progress(review_progress, expected)
            if response is not None or response_sha is not None:
                raise StateConflictError("Pi Scene progressed journal contains a response")
        elif status == "terminal":
            expected_pending_sha = self._pending_entry(expected)["journal_sha256"]
            if pre_dispatch_sha != expected_pending_sha:
                raise StateConflictError("Pi Scene pre-dispatch custody binding changed")
            _validate_review_progress(review_progress, expected)
            if not isinstance(response, dict) or not re_is_sha256(response_sha or ""):
                raise StateConflictError("Pi Scene terminal journal response is invalid")
            if canonical_sha256(response) != response_sha:
                raise StateConflictError("Pi Scene terminal response binding changed")
        else:
            raise StateConflictError("Pi Scene request journal status changed")
        return payload

    class _Claim:
        def __init__(self, path: Path, request_id: str) -> None:
            self.path = path
            self.request_id = request_id
            self.acquired = False

        def __enter__(self) -> None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            try:
                descriptor = os.open(
                    self.path,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                    0o600,
                )
            except FileExistsError as exc:
                raise RequestReplayPendingError(
                    "Pi Scene request journal claim already exists; redispatch is blocked",
                    request_id=self.request_id,
                ) from exc
            try:
                os.write(descriptor, b"pending\n")
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            self.acquired = True

        def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
            del exc_type, exc, traceback
            if self.acquired:
                try:
                    self.path.unlink()
                except FileNotFoundError:
                    pass

    def _claim(self, entry_path: Path) -> PiSceneRequestJournal._Claim:
        return self._Claim(entry_path.with_suffix(".claim"), entry_path.stem)


def _request_id(identity: Mapping[str, Any]) -> str:
    return "request-" + domain_sha256("cera.pi_scene.http_request.v1", identity)


def _binding_from_payload(payload: Any) -> PiSceneRequestBindingV1:
    required = {
        "schema_version",
        "request_id",
        "normalized_request_sha256",
        "normalized_request_size_bytes",
        "session_id",
        "world_id",
        "branch_id",
        "route",
        "route_intent",
        "controls",
        "controls_sha256",
    }
    if not isinstance(payload, dict) or set(payload) != required:
        raise StateConflictError("Pi Scene request binding shape changed")
    try:
        route = SceneRoute(payload["route"])
        return PiSceneRequestBindingV1(
            schema_version=payload["schema_version"],
            request_id=payload["request_id"],
            normalized_request_sha256=payload["normalized_request_sha256"],
            normalized_request_size_bytes=payload["normalized_request_size_bytes"],
            session_id=payload["session_id"],
            world_id=payload["world_id"],
            branch_id=payload["branch_id"],
            route=route,
            route_intent=payload["route_intent"],
            controls=payload["controls"],
            controls_sha256=payload["controls_sha256"],
        )
    except (ContractValidationError, TypeError, ValueError) as exc:
        raise StateConflictError("Pi Scene request binding is invalid") from exc


def _route_intent(payload: Mapping[str, Any]) -> str:
    """Bind whether Python resolved the product route or a test model selected it."""

    return "automatic" if payload.get("model") == "cera-alpha" else "explicit"


def _validate_review_progress(
    progress: Any,
    binding: PiSceneRequestBindingV1,
) -> None:
    if isinstance(progress, dict) and progress.get("schema_version") == (
        "cera.pi_scene.http_adult_progress.v1"
    ):
        _validate_adult_progress(progress, binding)
        return
    required = {
        "schema_version",
        "review_id",
        "candidate_id",
        "candidate_sha256",
        "world_id",
        "branch_id",
        "route",
        "exact_user_source_sha256",
        "controls_sha256",
        "review_state",
        "accepted_turn_id",
        "accepted_receipt_sha256",
        "recording_status",
    }
    if not isinstance(progress, dict) or set(progress) != required:
        raise StateConflictError("Pi Scene request review-progress shape changed")
    if progress["schema_version"] != "cera.pi_scene.http_review_progress.v1":
        raise StateConflictError("Pi Scene request review-progress schema changed")
    if not re.fullmatch(r"review-[a-f0-9]{28}", progress["review_id"] or ""):
        raise StateConflictError("Pi Scene request review identity is invalid")
    if not isinstance(progress["candidate_id"], str) or not progress["candidate_id"]:
        raise StateConflictError("Pi Scene request candidate identity is invalid")
    for field_name in (
        "candidate_sha256",
        "exact_user_source_sha256",
        "controls_sha256",
    ):
        if not re_is_sha256(progress[field_name] or ""):
            raise StateConflictError(f"Pi Scene request {field_name} is invalid")
    if (
        progress["world_id"] != binding.world_id
        or progress["branch_id"] != binding.branch_id
        or progress["route"] != binding.route.value
        or progress["controls_sha256"] != binding.controls_sha256
    ):
        raise StateConflictError("Pi Scene request review-progress custody changed")
    accepted_turn = progress["accepted_turn_id"]
    accepted_sha = progress["accepted_receipt_sha256"]
    if (accepted_turn is None) != (accepted_sha is None):
        raise StateConflictError("Pi Scene accepted review-progress binding is partial")
    if accepted_turn is not None and (
        not isinstance(accepted_turn, str) or not re_is_sha256(accepted_sha or "")
    ):
        raise StateConflictError("Pi Scene accepted review-progress binding is invalid")
    if progress["recording_status"] is not None and not isinstance(
        progress["recording_status"], str
    ):
        raise StateConflictError("Pi Scene review-progress recording status is invalid")


def _validate_adult_progress(
    progress: dict[str, Any],
    binding: PiSceneRequestBindingV1,
) -> None:
    required = {
        "schema_version",
        "request_id",
        "candidate_id",
        "operation_sha256",
        "world_id",
        "branch_id",
        "actual_route",
        "exact_user_source_sha256",
        "controls_sha256",
        "outcome_status",
        "accepted_turn_id",
        "accepted_receipt_sha256",
        "promotion_bundle_sha256",
        "protected_rejected_outcome",
        "protected_rejected_outcome_sha256",
    }
    if set(progress) != required:
        raise StateConflictError("Pi Scene adult request-progress shape changed")
    if progress["request_id"] != binding.request_id:
        raise StateConflictError("Pi Scene adult request identity changed")
    if (
        progress["world_id"] != binding.world_id
        or progress["branch_id"] != binding.branch_id
        or progress["actual_route"] != SceneRoute.ADULT.value
        or progress["controls_sha256"] != binding.controls_sha256
    ):
        raise StateConflictError("Pi Scene adult request-progress custody changed")
    if binding.route is not SceneRoute.ADULT and binding.route_intent != "automatic":
        raise StateConflictError("Pi Scene adult result lacks protected route custody")
    if not isinstance(progress["candidate_id"], str) or not progress["candidate_id"]:
        raise StateConflictError("Pi Scene adult candidate identity is invalid")
    for field_name in (
        "operation_sha256",
        "exact_user_source_sha256",
        "controls_sha256",
    ):
        if not re_is_sha256(progress[field_name] or ""):
            raise StateConflictError(f"Pi Scene adult {field_name} is invalid")
    status = progress["outcome_status"]
    accepted_values = (
        progress["accepted_turn_id"],
        progress["accepted_receipt_sha256"],
        progress["promotion_bundle_sha256"],
    )
    rejected = progress["protected_rejected_outcome"]
    rejected_sha = progress["protected_rejected_outcome_sha256"]
    if status == "accepted":
        if (
            not isinstance(accepted_values[0], str)
            or not re_is_sha256(accepted_values[1] or "")
            or not re_is_sha256(accepted_values[2] or "")
            or rejected is not None
            or rejected_sha is not None
        ):
            raise StateConflictError("Pi Scene accepted adult progress is invalid")
    elif status == "filter_rejected":
        if (
            any(value is not None for value in accepted_values)
            or not isinstance(rejected, dict)
            or not re_is_sha256(rejected_sha or "")
            or canonical_sha256(rejected) != rejected_sha
        ):
            raise StateConflictError("Pi Scene rejected adult progress is invalid")
    else:
        raise StateConflictError("Pi Scene adult outcome status changed")


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
