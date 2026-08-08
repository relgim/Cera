"""Filesystem custody for lean accepted turns and post-Accept records.

The accepted-turn directory is published atomically.  Its receipt never
changes.  Recorder attempts and their small status head are separate, so a
Recorder or reporting failure cannot erase or recommit visible canon.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import shutil
from threading import RLock
from typing import Any, Mapping
from uuid import uuid4

from cera.errors import ContractValidationError, StateConflictError
from cera.schema import from_mapping
from cera.serialization import canonical_json, canonical_sha256, text_sha256, to_primitive

from .contracts import (
    AdultCodexProjectionV1,
    AdultFullRecordV1,
    AdultProjectionItemV1,
    AdultRecordEventV1,
    LeanAcceptedTurnReceiptV1,
    LeanCandidateV1,
    LeanRecordingAttemptV1,
    OrdinarySceneRecordV1,
    PiWriterReceiptV1,
    RecordingStatus,
    SceneRoute,
    TedWarningV1,
    validate_adult_records,
    validate_ordinary_record,
)


@dataclass(frozen=True, slots=True)
class LeanAcceptedHeadV1:
    world_id: str
    branch_id: str
    generation: int
    accepted_turn_id: str | None
    accepted_head_sha256: str | None
    receipt: LeanAcceptedTurnReceiptV1 | None
    recording_status: RecordingStatus | None


@dataclass(frozen=True, slots=True)
class AcceptedPiSessionV1:
    accepted_turn_id: str
    session_id: str
    session_path: str
    session_id_sha256: str

    def __post_init__(self) -> None:
        if not self.accepted_turn_id.strip() or not self.session_id.strip():
            raise ContractValidationError("accepted Pi session identity is incomplete")
        if text_sha256(self.session_id) != self.session_id_sha256:
            raise ContractValidationError("accepted Pi session binding changed")


class LeanSceneStore:
    """Branch-scoped append-first store with restart reconciliation."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()

    def _branch_root(self, world_id: str, branch_id: str) -> Path:
        if not world_id.strip() or not branch_id.strip():
            raise ContractValidationError("world and branch identities are required")
        world_key = f"world-{text_sha256(world_id)[:24]}"
        branch_key = f"branch-{text_sha256(branch_id)[:24]}"
        root = (self.root / world_key / branch_key).resolve()
        if not root.is_relative_to(self.root):
            raise ContractValidationError("branch store escaped its configured root")
        root.mkdir(parents=True, exist_ok=True)
        identity = {"world_id": world_id, "branch_id": branch_id}
        identity_path = root / "BRANCH_IDENTITY.json"
        if identity_path.exists():
            if _read_json(identity_path) != identity:
                raise StateConflictError("branch hash collision changed identity")
        else:
            _atomic_write_json(identity_path, identity)
        (root / "accepted").mkdir(exist_ok=True)
        (root / "sessions").mkdir(exist_ok=True)
        return root

    def load_head(self, *, world_id: str, branch_id: str) -> LeanAcceptedHeadV1:
        with self._lock:
            branch_root = self._branch_root(world_id, branch_id)
            receipts = self._load_receipts(branch_root)
            parent: str | None = None
            parent_head_sha256: str | None = None
            for index, receipt in enumerate(receipts, start=1):
                if receipt.generation != index:
                    raise StateConflictError("accepted turn generations are not contiguous")
                if receipt.parent_accepted_turn_id != parent:
                    raise StateConflictError("accepted turn parent chain is inconsistent")
                if receipt.parent_accepted_head_sha256 != parent_head_sha256:
                    raise StateConflictError("accepted receipt hash chain is inconsistent")
                parent = receipt.accepted_turn_id
                parent_head_sha256 = receipt.receipt_sha256
            if not receipts:
                return LeanAcceptedHeadV1(
                    world_id=world_id,
                    branch_id=branch_id,
                    generation=0,
                    accepted_turn_id=None,
                    accepted_head_sha256=None,
                    receipt=None,
                    recording_status=None,
                )
            receipt = receipts[-1]
            return LeanAcceptedHeadV1(
                world_id=world_id,
                branch_id=branch_id,
                generation=receipt.generation,
                accepted_turn_id=receipt.accepted_turn_id,
                accepted_head_sha256=receipt.receipt_sha256,
                receipt=receipt,
                recording_status=self.recording_status(receipt),
            )

    def accept(self, candidate: LeanCandidateV1) -> LeanAcceptedTurnReceiptV1:
        """Publish phase one exactly once; identical recovery is read-only."""

        with self._lock:
            branch_root = self._branch_root(candidate.world_id, candidate.branch_id)
            existing = self._receipt_by_turn_id(branch_root, candidate.turn_id)
            if existing is not None:
                if existing.candidate_sha256 != candidate.candidate_sha256:
                    raise StateConflictError("accepted turn identity was reused by another candidate")
                return existing

            head = self.load_head(world_id=candidate.world_id, branch_id=candidate.branch_id)
            if candidate.generation != head.generation + 1:
                raise StateConflictError("candidate generation differs from accepted branch")
            if candidate.parent_accepted_turn_id != head.accepted_turn_id:
                raise StateConflictError("candidate parent differs from accepted branch")
            if candidate.accepted_head_before_sha256 != head.accepted_head_sha256:
                raise StateConflictError("candidate accepted-head binding is stale")

            receipt = LeanAcceptedTurnReceiptV1(
                schema_version=LeanAcceptedTurnReceiptV1.SCHEMA_VERSION,
                accepted_turn_id=candidate.turn_id,
                parent_accepted_turn_id=candidate.parent_accepted_turn_id,
                parent_accepted_head_sha256=candidate.accepted_head_before_sha256,
                world_id=candidate.world_id,
                branch_id=candidate.branch_id,
                scene_id=candidate.scene_id,
                generation=candidate.generation,
                route=candidate.route,
                exact_user_source=candidate.exact_user_source,
                exact_user_source_sha256=candidate.exact_user_source_sha256,
                exact_accepted_prose=candidate.story_text,
                exact_accepted_prose_sha256=candidate.story_text_sha256,
                primary_authority_kind=candidate.primary_authority_kind,
                primary_authority_json=candidate.primary_authority_json,
                primary_authority_sha256=candidate.primary_authority_sha256,
                writer_view_manifest_sha256=candidate.writer_view_manifest_sha256,
                writer_receipt=candidate.writer_receipt,
                creator_action="accept",
                warnings=candidate.warnings,
                initial_recording_status=RecordingStatus.PROJECTION_PENDING,
                candidate_sha256=candidate.candidate_sha256,
            )
            accepted_root = branch_root / "accepted"
            final_dir = accepted_root / _turn_directory_name(receipt)
            stage = branch_root / f".accept-{uuid4().hex}"
            stage.mkdir(parents=False, exist_ok=False)
            try:
                _write_new_json(stage / "ACCEPTED_RECEIPT.json", to_primitive(receipt))
                _write_new_json(
                    stage / "RECORDING_HEAD.json",
                    _recording_head_payload(
                        accepted_turn_id=receipt.accepted_turn_id,
                        status=RecordingStatus.PROJECTION_PENDING,
                        attempt_number=0,
                        attempt_sha256=None,
                    ),
                )
                os.replace(stage, final_dir)
            except Exception:
                if stage.exists():
                    shutil.rmtree(stage)
                raise
            self._write_branch_cache(branch_root, receipt)
            return receipt

    def recording_status(self, accepted: LeanAcceptedTurnReceiptV1) -> RecordingStatus:
        branch_root = self._branch_root(accepted.world_id, accepted.branch_id)
        turn_dir = branch_root / "accepted" / _turn_directory_name(accepted)
        payload = _read_json(turn_dir / "RECORDING_HEAD.json")
        if payload.get("accepted_turn_id") != accepted.accepted_turn_id:
            raise StateConflictError("recording head changed accepted turn identity")
        try:
            return RecordingStatus(str(payload["status"]))
        except (KeyError, ValueError) as exc:
            raise StateConflictError("recording head status is invalid") from exc

    def load_recording_attempt(
        self,
        accepted: LeanAcceptedTurnReceiptV1,
    ) -> LeanRecordingAttemptV1:
        """Load and hash-verify the current immutable recording attempt."""

        with self._lock:
            turn_dir = self._accepted_turn_dir(accepted)
            head = _read_json(turn_dir / "RECORDING_HEAD.json")
            if head.get("accepted_turn_id") != accepted.accepted_turn_id:
                raise StateConflictError("recording head changed accepted turn identity")
            number = head.get("attempt_number")
            if type(number) is not int or number < 1:
                raise StateConflictError("recording head attempt number is invalid")
            attempt = _recording_attempt_from_mapping(
                _read_json(turn_dir / f"RECORDING_ATTEMPT_{number:04d}.json")
            )
            if canonical_sha256(attempt) != head.get("attempt_sha256"):
                raise StateConflictError("recording attempt differs from its head")
            if attempt.status.value != head.get("status"):
                raise StateConflictError("recording attempt status differs from its head")
            return attempt

    def mark_recording_failure(
        self,
        accepted: LeanAcceptedTurnReceiptV1,
        *,
        recorder_request_sha256: str,
        provider_operations: int,
        failure_code: str,
        recorder_output_sha256: str | None = None,
    ) -> LeanRecordingAttemptV1:
        with self._lock:
            turn_dir = self._accepted_turn_dir(accepted)
            attempt_number = self._next_recording_attempt(turn_dir)
            attempt = LeanRecordingAttemptV1(
                schema_version=LeanRecordingAttemptV1.SCHEMA_VERSION,
                accepted_turn_id=accepted.accepted_turn_id,
                attempt_number=attempt_number,
                status=RecordingStatus.PENDING_REPAIR,
                recorder_request_sha256=recorder_request_sha256,
                recorder_output_sha256=recorder_output_sha256,
                provider_operations=provider_operations,
                failure_code=failure_code,
            )
            self._publish_recording_attempt(turn_dir, attempt)
            return attempt

    def attach_ordinary_record(
        self,
        accepted: LeanAcceptedTurnReceiptV1,
        record: OrdinarySceneRecordV1,
        *,
        recorder_request_sha256: str,
        recorder_output_sha256: str,
        provider_operations: int,
    ) -> LeanRecordingAttemptV1:
        with self._lock:
            validate_ordinary_record(record, accepted=accepted)
            turn_dir = self._accepted_turn_dir(accepted)
            record_sha256 = canonical_sha256(record)
            existing = turn_dir / "ORDINARY_RECORD.json"
            if existing.exists():
                if canonical_sha256(_read_json(existing)) != record_sha256:
                    raise StateConflictError("ordinary record changed after attachment")
                return self._load_complete_attempt(turn_dir)
            _write_new_json(existing, to_primitive(record))
            attempt = LeanRecordingAttemptV1(
                schema_version=LeanRecordingAttemptV1.SCHEMA_VERSION,
                accepted_turn_id=accepted.accepted_turn_id,
                attempt_number=self._next_recording_attempt(turn_dir),
                status=RecordingStatus.COMPLETE,
                recorder_request_sha256=recorder_request_sha256,
                recorder_output_sha256=recorder_output_sha256,
                provider_operations=provider_operations,
                ordinary_record_sha256=record_sha256,
            )
            self._publish_recording_attempt(turn_dir, attempt)
            return attempt

    def attach_adult_records(
        self,
        accepted: LeanAcceptedTurnReceiptV1,
        full: AdultFullRecordV1,
        projection: AdultCodexProjectionV1,
        *,
        recorder_request_sha256: str,
        recorder_output_sha256: str,
        provider_operations: int,
    ) -> LeanRecordingAttemptV1:
        with self._lock:
            validate_adult_records(full, projection, accepted=accepted)
            turn_dir = self._accepted_turn_dir(accepted)
            full_sha = canonical_sha256(full)
            projection_sha = canonical_sha256(projection)
            full_path = turn_dir / "ADULT_FULL_RECORD.json"
            projection_path = turn_dir / "ADULT_CODEX_PROJECTION.json"
            if full_path.exists() or projection_path.exists():
                if not (full_path.exists() and projection_path.exists()):
                    raise StateConflictError("adult dual record was only partly attached")
                if (
                    canonical_sha256(_read_json(full_path)) != full_sha
                    or canonical_sha256(_read_json(projection_path)) != projection_sha
                ):
                    raise StateConflictError("adult dual record changed after attachment")
                return self._load_complete_attempt(turn_dir)
            _write_new_json(full_path, to_primitive(full))
            _write_new_json(projection_path, to_primitive(projection))
            attempt = LeanRecordingAttemptV1(
                schema_version=LeanRecordingAttemptV1.SCHEMA_VERSION,
                accepted_turn_id=accepted.accepted_turn_id,
                attempt_number=self._next_recording_attempt(turn_dir),
                status=RecordingStatus.COMPLETE,
                recorder_request_sha256=recorder_request_sha256,
                recorder_output_sha256=recorder_output_sha256,
                provider_operations=provider_operations,
                adult_full_record_sha256=full_sha,
                adult_projection_sha256=projection_sha,
            )
            self._publish_recording_attempt(turn_dir, attempt)
            return attempt

    def promote_pi_session(
        self,
        accepted: LeanAcceptedTurnReceiptV1,
        *,
        session_id: str,
        session_path: Path,
    ) -> AcceptedPiSessionV1:
        """Promote only the session that produced an accepted candidate."""

        with self._lock:
            if text_sha256(session_id) != accepted.writer_receipt.session_id_sha256:
                raise StateConflictError("accepted Pi session differs from Writer receipt")
            payload = AcceptedPiSessionV1(
                accepted_turn_id=accepted.accepted_turn_id,
                session_id=session_id,
                session_path=str(session_path.resolve()),
                session_id_sha256=text_sha256(session_id),
            )
            branch_root = self._branch_root(accepted.world_id, accepted.branch_id)
            path = branch_root / "sessions" / "ACCEPTED_SESSION.json"
            _atomic_write_json(path, to_primitive(payload))
            return payload

    def load_accepted_pi_session(
        self,
        *,
        world_id: str,
        branch_id: str,
    ) -> AcceptedPiSessionV1 | None:
        branch_root = self._branch_root(world_id, branch_id)
        path = branch_root / "sessions" / "ACCEPTED_SESSION.json"
        if not path.exists():
            return None
        payload = _read_json(path)
        session = AcceptedPiSessionV1(
            accepted_turn_id=str(payload["accepted_turn_id"]),
            session_id=str(payload["session_id"]),
            session_path=str(payload["session_path"]),
            session_id_sha256=str(payload["session_id_sha256"]),
        )
        head = self.load_head(world_id=world_id, branch_id=branch_id)
        if session.accepted_turn_id != head.accepted_turn_id:
            raise StateConflictError("accepted Pi session does not match branch head")
        return session

    def recent_accepted_payloads(
        self,
        *,
        world_id: str,
        branch_id: str,
        limit: int = 6,
        adult_full: bool = False,
    ) -> tuple[dict[str, Any], ...]:
        if type(limit) is not int or not 1 <= limit <= 20:
            raise ContractValidationError("accepted context limit is invalid")
        branch_root = self._branch_root(world_id, branch_id)
        receipts = self._load_receipts(branch_root)[-limit:]
        output: list[dict[str, Any]] = []
        for receipt in receipts:
            turn_dir = branch_root / "accepted" / _turn_directory_name(receipt)
            item: dict[str, Any] = {"receipt": to_primitive(receipt)}
            ordinary = turn_dir / "ORDINARY_RECORD.json"
            projection = turn_dir / "ADULT_CODEX_PROJECTION.json"
            full = turn_dir / "ADULT_FULL_RECORD.json"
            if ordinary.exists():
                item["ordinary_record"] = _read_json(ordinary)
            if projection.exists():
                item["adult_projection"] = _read_json(projection)
            if adult_full and full.exists():
                item["adult_full_record"] = _read_json(full)
            output.append(item)
        return tuple(output)

    def _load_receipts(self, branch_root: Path) -> list[LeanAcceptedTurnReceiptV1]:
        receipts: list[LeanAcceptedTurnReceiptV1] = []
        for path in sorted((branch_root / "accepted").glob("*/ACCEPTED_RECEIPT.json")):
            receipts.append(_accepted_receipt_from_mapping(_read_json(path)))
        receipts.sort(key=lambda value: value.generation)
        if len({value.generation for value in receipts}) != len(receipts):
            raise StateConflictError("accepted branch contains duplicate generations")
        return receipts

    def _receipt_by_turn_id(
        self,
        branch_root: Path,
        turn_id: str,
    ) -> LeanAcceptedTurnReceiptV1 | None:
        matches = [value for value in self._load_receipts(branch_root) if value.accepted_turn_id == turn_id]
        if len(matches) > 1:
            raise StateConflictError("accepted turn identity occurs more than once")
        return matches[0] if matches else None

    def _accepted_turn_dir(self, accepted: LeanAcceptedTurnReceiptV1) -> Path:
        branch_root = self._branch_root(accepted.world_id, accepted.branch_id)
        turn_dir = branch_root / "accepted" / _turn_directory_name(accepted)
        if not turn_dir.is_dir():
            raise StateConflictError("accepted turn directory is missing")
        stored = _accepted_receipt_from_mapping(_read_json(turn_dir / "ACCEPTED_RECEIPT.json"))
        if stored.receipt_sha256 != accepted.receipt_sha256:
            raise StateConflictError("accepted turn receipt differs from stored bytes")
        return turn_dir

    @staticmethod
    def _next_recording_attempt(turn_dir: Path) -> int:
        numbers = []
        for path in turn_dir.glob("RECORDING_ATTEMPT_*.json"):
            match = re.fullmatch(r"RECORDING_ATTEMPT_(\d{4})\.json", path.name)
            if match:
                numbers.append(int(match.group(1)))
        return (max(numbers) if numbers else 0) + 1

    @staticmethod
    def _publish_recording_attempt(
        turn_dir: Path,
        attempt: LeanRecordingAttemptV1,
    ) -> None:
        attempt_path = turn_dir / f"RECORDING_ATTEMPT_{attempt.attempt_number:04d}.json"
        _write_new_json(attempt_path, to_primitive(attempt))
        _atomic_write_json(
            turn_dir / "RECORDING_HEAD.json",
            _recording_head_payload(
                accepted_turn_id=attempt.accepted_turn_id,
                status=attempt.status,
                attempt_number=attempt.attempt_number,
                attempt_sha256=canonical_sha256(attempt),
            ),
        )

    @staticmethod
    def _load_complete_attempt(turn_dir: Path) -> LeanRecordingAttemptV1:
        head = _read_json(turn_dir / "RECORDING_HEAD.json")
        if head.get("status") != RecordingStatus.COMPLETE.value:
            raise StateConflictError("attached record does not have complete status")
        number = int(head["attempt_number"])
        return _recording_attempt_from_mapping(
            _read_json(turn_dir / f"RECORDING_ATTEMPT_{number:04d}.json")
        )

    @staticmethod
    def _write_branch_cache(branch_root: Path, receipt: LeanAcceptedTurnReceiptV1) -> None:
        _atomic_write_json(
            branch_root / "BRANCH_HEAD_CACHE.json",
            {
                "schema_version": "cera.pi_scene.branch_head_cache.v1",
                "generation": receipt.generation,
                "accepted_turn_id": receipt.accepted_turn_id,
                "accepted_head_sha256": receipt.receipt_sha256,
            },
        )


def _turn_directory_name(receipt: LeanAcceptedTurnReceiptV1) -> str:
    return f"{receipt.generation:08d}-{text_sha256(receipt.accepted_turn_id)[:20]}"


def _recording_head_payload(
    *,
    accepted_turn_id: str,
    status: RecordingStatus,
    attempt_number: int,
    attempt_sha256: str | None,
) -> dict[str, Any]:
    return {
        "schema_version": "cera.pi_scene.recording_head.v1",
        "accepted_turn_id": accepted_turn_id,
        "status": status.value,
        "attempt_number": attempt_number,
        "attempt_sha256": attempt_sha256,
    }


def _atomic_write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    temporary.write_text(canonical_json(dict(value)), encoding="utf-8")
    os.replace(temporary, path)


def _write_new_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = canonical_json(dict(value))
    try:
        with path.open("x", encoding="utf-8", newline="") as handle:
            handle.write(text)
    except FileExistsError:
        if path.read_text(encoding="utf-8") != text:
            raise StateConflictError(f"immutable artifact changed: {path.name}") from None


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StateConflictError(f"stored JSON is unreadable: {path.name}") from exc
    if not isinstance(value, dict):
        raise StateConflictError(f"stored JSON is not an object: {path.name}")
    return value


def _writer_receipt_from_mapping(value: Mapping[str, Any]) -> PiWriterReceiptV1:
    return PiWriterReceiptV1(
        schema_version=str(value["schema_version"]),
        route=SceneRoute(str(value["route"])),
        provider=str(value["provider"]),
        model=str(value["model"]),
        pi_version=str(value["pi_version"]),
        session_id_sha256=str(value["session_id_sha256"]),
        parent_session_id_sha256=(
            None
            if value.get("parent_session_id_sha256") is None
            else str(value["parent_session_id_sha256"])
        ),
        request_sha256=str(value["request_sha256"]),
        output_sha256=str(value["output_sha256"]),
        provider_operations=int(value["provider_operations"]),
        tool_call_count=int(value["tool_call_count"]),
        failed_tool_call_count=int(value["failed_tool_call_count"]),
        input_tokens=int(value["input_tokens"]),
        cached_input_tokens=int(value["cached_input_tokens"]),
        output_tokens=int(value["output_tokens"]),
        reasoning_tokens=int(value["reasoning_tokens"]),
        duration_ms=int(value["duration_ms"]),
        finish_status=str(value["finish_status"]),
        rehydrated=bool(value["rehydrated"]),
    )


def _warnings_from_mapping(values: Any) -> tuple[TedWarningV1, ...]:
    if not isinstance(values, list):
        raise StateConflictError("stored warnings are invalid")
    return tuple(
        TedWarningV1(
            schema_version=str(value["schema_version"]),
            warning_code=str(value["warning_code"]),
            excerpt=str(value["excerpt"]),
        )
        for value in values
    )


def _accepted_receipt_from_mapping(value: Mapping[str, Any]) -> LeanAcceptedTurnReceiptV1:
    return LeanAcceptedTurnReceiptV1(
        schema_version=str(value["schema_version"]),
        accepted_turn_id=str(value["accepted_turn_id"]),
        parent_accepted_turn_id=(
            None
            if value.get("parent_accepted_turn_id") is None
            else str(value["parent_accepted_turn_id"])
        ),
        parent_accepted_head_sha256=(
            None
            if value.get("parent_accepted_head_sha256") is None
            else str(value["parent_accepted_head_sha256"])
        ),
        world_id=str(value["world_id"]),
        branch_id=str(value["branch_id"]),
        scene_id=str(value["scene_id"]),
        generation=int(value["generation"]),
        route=SceneRoute(str(value["route"])),
        exact_user_source=str(value["exact_user_source"]),
        exact_user_source_sha256=str(value["exact_user_source_sha256"]),
        exact_accepted_prose=str(value["exact_accepted_prose"]),
        exact_accepted_prose_sha256=str(value["exact_accepted_prose_sha256"]),
        primary_authority_kind=str(value["primary_authority_kind"]),
        primary_authority_json=str(value["primary_authority_json"]),
        primary_authority_sha256=str(value["primary_authority_sha256"]),
        writer_view_manifest_sha256=str(value["writer_view_manifest_sha256"]),
        writer_receipt=_writer_receipt_from_mapping(value["writer_receipt"]),
        creator_action=str(value["creator_action"]),
        warnings=_warnings_from_mapping(value["warnings"]),
        initial_recording_status=RecordingStatus(str(value["initial_recording_status"])),
        candidate_sha256=str(value["candidate_sha256"]),
    )


def _recording_attempt_from_mapping(value: Mapping[str, Any]) -> LeanRecordingAttemptV1:
    return LeanRecordingAttemptV1(
        schema_version=str(value["schema_version"]),
        accepted_turn_id=str(value["accepted_turn_id"]),
        attempt_number=int(value["attempt_number"]),
        status=RecordingStatus(str(value["status"])),
        recorder_request_sha256=str(value["recorder_request_sha256"]),
        recorder_output_sha256=(
            None
            if value.get("recorder_output_sha256") is None
            else str(value["recorder_output_sha256"])
        ),
        provider_operations=int(value["provider_operations"]),
        failure_code=(None if value.get("failure_code") is None else str(value["failure_code"])),
        ordinary_record_sha256=(
            None
            if value.get("ordinary_record_sha256") is None
            else str(value["ordinary_record_sha256"])
        ),
        adult_full_record_sha256=(
            None
            if value.get("adult_full_record_sha256") is None
            else str(value["adult_full_record_sha256"])
        ),
        adult_projection_sha256=(
            None
            if value.get("adult_projection_sha256") is None
            else str(value["adult_projection_sha256"])
        ),
    )


def ordinary_record_from_mapping(value: Mapping[str, Any]) -> OrdinarySceneRecordV1:
    return from_mapping(OrdinarySceneRecordV1, value)


def adult_full_record_from_mapping(value: Mapping[str, Any]) -> AdultFullRecordV1:
    return from_mapping(AdultFullRecordV1, value)


def adult_projection_from_mapping(value: Mapping[str, Any]) -> AdultCodexProjectionV1:
    return from_mapping(AdultCodexProjectionV1, value)
