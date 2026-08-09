"""Filesystem custody for lean accepted turns and post-Accept records.

The accepted-turn directory is published atomically.  Its receipt never
changes.  Recorder attempts and their small status head are separate, so a
Recorder or reporting failure cannot erase or recommit visible canon.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Any, ClassVar
from uuid import uuid4

from cera.errors import ContractValidationError, StateConflictError
from cera.schema import from_mapping
from cera.semantic_validation import (
    BoundSemanticValidationV1,
    SemanticVerdict,
)
from cera.serialization import (
    canonical_json,
    canonical_sha256,
    re_is_sha256,
    text_sha256,
    to_primitive,
)

from .contracts import (
    AdultCodexProjectionV1,
    AdultCodexProjectionV2,
    AdultFullRecordV1,
    LeanAcceptedTurnReceiptV1,
    LeanCandidateV1,
    LeanRecordingAttemptV1,
    OrdinarySceneRecordV1,
    RecordingStatus,
    SceneRoute,
    validate_adult_records,
    validate_ordinary_record,
)


@dataclass(frozen=True, slots=True)
class _RecordingHeadV1:
    SCHEMA_VERSION: ClassVar[str] = "cera.pi_scene.recording_head.v1"

    schema_version: str
    accepted_turn_id: str
    status: RecordingStatus
    attempt_number: int
    attempt_sha256: str | None

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("recording-head schema changed")
        if type(self.accepted_turn_id) is not str or not self.accepted_turn_id.strip():
            raise ContractValidationError("recording-head accepted turn is invalid")
        if type(self.attempt_number) is not int or self.attempt_number < 0:
            raise ContractValidationError("recording-head attempt number is invalid")
        if type(self.status) is not RecordingStatus:
            raise ContractValidationError("recording-head status is invalid")
        if self.status is RecordingStatus.PROJECTION_PENDING:
            if self.attempt_number != 0 or self.attempt_sha256 is not None:
                raise ContractValidationError(
                    "projection-pending recording head must remain at attempt zero"
                )
        elif (
            self.attempt_number < 1
            or type(self.attempt_sha256) is not str
            or not re_is_sha256(self.attempt_sha256)
        ):
            raise ContractValidationError("recording-head attempt binding is invalid")


@dataclass(frozen=True, slots=True)
class _BranchHeadCacheV1:
    """Durable branch-head anchor used to detect coherent receipt rewrites."""

    SCHEMA_VERSION: ClassVar[str] = "cera.pi_scene.branch_head_cache.v1"

    schema_version: str
    generation: int
    accepted_turn_id: str | None
    accepted_head_sha256: str | None

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("branch-head cache schema changed")
        if type(self.generation) is not int or self.generation < 0:
            raise ContractValidationError("branch-head cache generation is invalid")
        if self.generation == 0:
            if self.accepted_turn_id is not None or self.accepted_head_sha256 is not None:
                raise ContractValidationError("root branch-head cache contains a turn")
        elif (
            type(self.accepted_turn_id) is not str
            or not self.accepted_turn_id.strip()
            or type(self.accepted_head_sha256) is not str
            or not re_is_sha256(self.accepted_head_sha256)
        ):
            raise ContractValidationError("branch-head cache binding is invalid")


@dataclass(frozen=True, slots=True)
class _RecordingBundleManifestV1:
    SCHEMA_VERSION: ClassVar[str] = "cera.pi_scene.recording_bundle_manifest.v1"

    schema_version: str
    accepted_turn_id: str
    attempt_number: int
    attempt_sha256: str
    route: SceneRoute
    ordinary_record_sha256: str | None
    adult_full_record_sha256: str | None
    adult_projection_sha256: str | None

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("recording-bundle schema changed")
        if type(self.accepted_turn_id) is not str or not self.accepted_turn_id.strip():
            raise ContractValidationError("recording-bundle accepted turn is invalid")
        if type(self.attempt_number) is not int or self.attempt_number < 1:
            raise ContractValidationError("recording-bundle attempt number is invalid")
        if type(self.route) is not SceneRoute:
            raise ContractValidationError("recording-bundle route is invalid")
        for field_name in (
            "attempt_sha256",
            "ordinary_record_sha256",
            "adult_full_record_sha256",
            "adult_projection_sha256",
        ):
            value = getattr(self, field_name)
            if value is not None and (type(value) is not str or not re_is_sha256(value)):
                raise ContractValidationError(f"recording-bundle {field_name} is invalid")
        if type(self.attempt_sha256) is not str:
            raise ContractValidationError("recording-bundle attempt_sha256 is invalid")
        ordinary = self.ordinary_record_sha256 is not None
        adult = (
            self.adult_full_record_sha256 is not None and self.adult_projection_sha256 is not None
        )
        if ordinary == adult:
            raise ContractValidationError("recording bundle must contain one route shape")
        if ordinary != (self.route is SceneRoute.ORDINARY):
            raise ContractValidationError("recording bundle route shape changed")


@dataclass(frozen=True, slots=True)
class _RecordingBundleManifestV2:
    """Python-owned binding between one record bundle and accepted prose."""

    SCHEMA_VERSION: ClassVar[str] = "cera.pi_scene.recording_bundle_manifest.v2"

    schema_version: str
    accepted_turn_id: str
    accepted_receipt_sha256: str
    exact_accepted_prose_sha256: str
    primary_authority_sha256: str
    attempt_number: int
    attempt_sha256: str
    route: SceneRoute
    ordinary_record_sha256: str | None
    adult_full_record_sha256: str | None
    adult_projection_sha256: str | None

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("recording-bundle V2 schema changed")
        if type(self.accepted_turn_id) is not str or not self.accepted_turn_id.strip():
            raise ContractValidationError("recording-bundle accepted turn is invalid")
        if type(self.attempt_number) is not int or self.attempt_number < 1:
            raise ContractValidationError("recording-bundle attempt number is invalid")
        if type(self.route) is not SceneRoute:
            raise ContractValidationError("recording-bundle route is invalid")
        for field_name in (
            "accepted_receipt_sha256",
            "exact_accepted_prose_sha256",
            "primary_authority_sha256",
            "attempt_sha256",
            "ordinary_record_sha256",
            "adult_full_record_sha256",
            "adult_projection_sha256",
        ):
            value = getattr(self, field_name)
            if value is not None and (type(value) is not str or not re_is_sha256(value)):
                raise ContractValidationError(f"recording-bundle {field_name} is invalid")
        ordinary = self.ordinary_record_sha256 is not None
        adult = (
            self.adult_full_record_sha256 is not None and self.adult_projection_sha256 is not None
        )
        if ordinary == adult:
            raise ContractValidationError("recording bundle must contain one route shape")
        if ordinary != (self.route is SceneRoute.ORDINARY):
            raise ContractValidationError("recording bundle route shape changed")


_RecordingBundleManifest = _RecordingBundleManifestV1 | _RecordingBundleManifestV2


@dataclass(frozen=True, slots=True)
class _CompleteRecordingBundle:
    attempt: LeanRecordingAttemptV1
    ordinary_record: OrdinarySceneRecordV1 | None = None
    adult_full_record: AdultFullRecordV1 | None = None
    adult_projection: AdultCodexProjectionV1 | AdultCodexProjectionV2 | None = None


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
        if (
            type(self.accepted_turn_id) is not str
            or type(self.session_id) is not str
            or type(self.session_path) is not str
            or type(self.session_id_sha256) is not str
            or not self.accepted_turn_id.strip()
            or not self.session_id.strip()
            or not self.session_path.strip()
        ):
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
        if (
            type(world_id) is not str
            or type(branch_id) is not str
            or not world_id.strip()
            or not branch_id.strip()
        ):
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
        cache_path = root / "BRANCH_HEAD_CACHE.json"
        if not cache_path.exists() and not any((root / "accepted").glob("*/ACCEPTED_RECEIPT.json")):
            _atomic_write_json(
                cache_path,
                to_primitive(
                    _BranchHeadCacheV1(
                        schema_version=_BranchHeadCacheV1.SCHEMA_VERSION,
                        generation=0,
                        accepted_turn_id=None,
                        accepted_head_sha256=None,
                    )
                ),
            )
        return root

    def load_head(self, *, world_id: str, branch_id: str) -> LeanAcceptedHeadV1:
        with self._lock:
            branch_root = self._branch_root(world_id, branch_id)
            receipts = self._load_receipts(branch_root)
            _validate_receipt_chain(
                receipts,
                world_id=world_id,
                branch_id=branch_id,
            )
            self._verify_or_advance_branch_cache(branch_root, receipts)
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

    def accept(
        self,
        candidate: LeanCandidateV1,
        *,
        semantic_validation: BoundSemanticValidationV1 | None = None,
    ) -> LeanAcceptedTurnReceiptV1:
        """Publish phase one exactly once; identical recovery is read-only."""

        with self._lock:
            branch_root = self._branch_root(candidate.world_id, candidate.branch_id)
            existing = self._receipt_by_turn_id(branch_root, candidate.turn_id)
            if existing is not None:
                if existing.candidate_sha256 != candidate.candidate_sha256:
                    raise StateConflictError(
                        "accepted turn identity was reused by another candidate"
                    )
                return existing

            head = self.load_head(world_id=candidate.world_id, branch_id=candidate.branch_id)
            if candidate.generation != head.generation + 1:
                raise StateConflictError("candidate generation differs from accepted branch")
            if candidate.parent_accepted_turn_id != head.accepted_turn_id:
                raise StateConflictError("candidate parent differs from accepted branch")
            if candidate.accepted_head_before_sha256 != head.accepted_head_sha256:
                raise StateConflictError("candidate accepted-head binding is stale")

            _validate_candidate_qualification(candidate, semantic_validation)
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
                if semantic_validation is not None:
                    _write_new_json(
                        stage / "SEMANTIC_VALIDATION.json",
                        _semantic_validation_artifact(
                            candidate=candidate,
                            validation=semantic_validation,
                        ),
                    )
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
        with self._lock:
            turn_dir = self._accepted_turn_dir(accepted)
            head = _read_recording_head(turn_dir, accepted=accepted)
            if head.status is not RecordingStatus.COMPLETE:
                recovered = _recover_atomic_recording_bundle(
                    turn_dir,
                    accepted=accepted,
                    head=head,
                )
                if recovered is not None:
                    return recovered.attempt.status
                orphan = _recover_orphan_recording_attempt(
                    turn_dir,
                    accepted=accepted,
                    head=head,
                )
                if orphan is not None:
                    return orphan.status
            if head.status is RecordingStatus.COMPLETE:
                _load_complete_recording_bundle(
                    turn_dir,
                    accepted=accepted,
                    head=head,
                )
            elif head.status is RecordingStatus.PENDING_REPAIR:
                _load_attempt_from_head(turn_dir, accepted=accepted, head=head)
            return head.status

    def load_recording_attempt(
        self,
        accepted: LeanAcceptedTurnReceiptV1,
    ) -> LeanRecordingAttemptV1:
        """Load and hash-verify the current immutable recording attempt."""

        with self._lock:
            turn_dir = self._accepted_turn_dir(accepted)
            head = _read_recording_head(turn_dir, accepted=accepted)
            if head.status is not RecordingStatus.COMPLETE:
                recovered = _recover_atomic_recording_bundle(
                    turn_dir,
                    accepted=accepted,
                    head=head,
                )
                if recovered is not None:
                    return recovered.attempt
                orphan = _recover_orphan_recording_attempt(
                    turn_dir,
                    accepted=accepted,
                    head=head,
                )
                if orphan is not None:
                    return orphan
            if head.status is RecordingStatus.PROJECTION_PENDING:
                return _phase_one_recording_state(accepted)
            if head.status is RecordingStatus.COMPLETE:
                return _load_complete_recording_bundle(
                    turn_dir,
                    accepted=accepted,
                    head=head,
                ).attempt
            return _load_attempt_from_head(turn_dir, accepted=accepted, head=head)

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
            head = _read_recording_head(turn_dir, accepted=accepted)
            recovered = _recover_atomic_recording_bundle(
                turn_dir,
                accepted=accepted,
                head=head,
            )
            if recovered is not None:
                return recovered.attempt
            orphan = _recover_orphan_recording_attempt(
                turn_dir,
                accepted=accepted,
                head=head,
            )
            if orphan is not None:
                if (
                    orphan.recorder_request_sha256 == recorder_request_sha256
                    and orphan.recorder_output_sha256 == recorder_output_sha256
                    and orphan.provider_operations == provider_operations
                    and orphan.failure_code == failure_code
                ):
                    return orphan
                head = _read_recording_head(turn_dir, accepted=accepted)
            if head.status is RecordingStatus.COMPLETE:
                raise StateConflictError("accepted turn recording is already complete")
            if head.status is RecordingStatus.PENDING_REPAIR:
                prior = _load_attempt_from_head(
                    turn_dir,
                    accepted=accepted,
                    head=head,
                )
                if (
                    prior.recorder_request_sha256 == recorder_request_sha256
                    and prior.recorder_output_sha256 == recorder_output_sha256
                    and prior.provider_operations == provider_operations
                    and prior.failure_code == failure_code
                ):
                    return prior
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
            head = _read_recording_head(turn_dir, accepted=accepted)
            recovered = _recover_atomic_recording_bundle(
                turn_dir,
                accepted=accepted,
                head=head,
            )
            if recovered is not None:
                _require_ordinary_bundle_hash(recovered, record_sha256)
                return recovered.attempt
            orphan = _recover_orphan_recording_attempt(
                turn_dir,
                accepted=accepted,
                head=head,
            )
            if orphan is not None:
                head = _read_recording_head(turn_dir, accepted=accepted)
            if head.status is RecordingStatus.COMPLETE:
                existing = _load_complete_recording_bundle(
                    turn_dir,
                    accepted=accepted,
                    head=head,
                )
                _require_ordinary_bundle_hash(existing, record_sha256)
                return existing.attempt
            if head.status is RecordingStatus.PENDING_REPAIR:
                _load_attempt_from_head(turn_dir, accepted=accepted, head=head)
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
            bundle = _CompleteRecordingBundle(
                attempt=attempt,
                ordinary_record=record,
            )
            _publish_atomic_recording_bundle(
                turn_dir,
                accepted=accepted,
                bundle=bundle,
            )
            _finalize_recording_bundle(
                turn_dir,
                accepted=accepted,
                bundle=bundle,
                prior_head=head,
            )
            return attempt

    def attach_adult_records(
        self,
        accepted: LeanAcceptedTurnReceiptV1,
        full: AdultFullRecordV1,
        projection: AdultCodexProjectionV1 | AdultCodexProjectionV2,
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
            head = _read_recording_head(turn_dir, accepted=accepted)
            recovered = _recover_atomic_recording_bundle(
                turn_dir,
                accepted=accepted,
                head=head,
            )
            if recovered is not None:
                _require_adult_bundle_hashes(recovered, full_sha, projection_sha)
                return recovered.attempt
            orphan = _recover_orphan_recording_attempt(
                turn_dir,
                accepted=accepted,
                head=head,
            )
            if orphan is not None:
                head = _read_recording_head(turn_dir, accepted=accepted)
            if head.status is RecordingStatus.COMPLETE:
                existing = _load_complete_recording_bundle(
                    turn_dir,
                    accepted=accepted,
                    head=head,
                )
                _require_adult_bundle_hashes(existing, full_sha, projection_sha)
                return existing.attempt
            if head.status is RecordingStatus.PENDING_REPAIR:
                _load_attempt_from_head(turn_dir, accepted=accepted, head=head)
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
            bundle = _CompleteRecordingBundle(
                attempt=attempt,
                adult_full_record=full,
                adult_projection=projection,
            )
            _publish_atomic_recording_bundle(
                turn_dir,
                accepted=accepted,
                bundle=bundle,
            )
            _finalize_recording_bundle(
                turn_dir,
                accepted=accepted,
                bundle=bundle,
                prior_head=head,
            )
            return attempt

    def promote_pi_session(
        self,
        accepted: LeanAcceptedTurnReceiptV1,
        *,
        session_id: str,
        session_path: Path,
    ) -> AcceptedPiSessionV1 | None:
        """Promote only the current head's accepted Writer session.

        Pi continuity is a soft performance cache.  Recording repair for an
        older accepted turn must therefore never move the cache behind the
        authoritative accepted head.
        """

        with self._lock:
            if text_sha256(session_id) != accepted.writer_receipt.session_id_sha256:
                raise StateConflictError("accepted Pi session differs from Writer receipt")
            self._accepted_turn_dir(accepted)
            head = self.load_head(
                world_id=accepted.world_id,
                branch_id=accepted.branch_id,
            )
            branch_root = self._branch_root(accepted.world_id, accepted.branch_id)
            path = branch_root / "sessions" / "ACCEPTED_SESSION.json"
            if head.accepted_turn_id != accepted.accepted_turn_id:
                return _load_current_pi_session(path, head=head)
            payload = AcceptedPiSessionV1(
                accepted_turn_id=accepted.accepted_turn_id,
                session_id=session_id,
                session_path=str(session_path.resolve()),
                session_id_sha256=text_sha256(session_id),
            )
            if path.exists():
                existing = _decode_stored(
                    AcceptedPiSessionV1,
                    _read_json(path),
                    "accepted Pi session",
                )
                if existing.accepted_turn_id == accepted.accepted_turn_id:
                    if existing.session_id_sha256 != payload.session_id_sha256:
                        raise StateConflictError("accepted Pi session changed for the current head")
                    return existing
            _atomic_write_json(path, to_primitive(payload))
            return payload

    def load_accepted_pi_session(
        self,
        *,
        world_id: str,
        branch_id: str,
    ) -> AcceptedPiSessionV1 | None:
        with self._lock:
            branch_root = self._branch_root(world_id, branch_id)
            path = branch_root / "sessions" / "ACCEPTED_SESSION.json"
            if not path.exists():
                return None
            head = self.load_head(world_id=world_id, branch_id=branch_id)
            return _load_current_pi_session(path, head=head)

    def recent_accepted_payloads(
        self,
        *,
        world_id: str,
        branch_id: str,
        limit: int = 6,
        adult_full: bool = False,
        allow_pending: bool = False,
    ) -> tuple[dict[str, Any], ...]:
        if type(limit) is not int or not 1 <= limit <= 20:
            raise ContractValidationError("accepted context limit is invalid")
        if type(adult_full) is not bool:
            raise ContractValidationError("accepted context adult_full flag is invalid")
        if type(allow_pending) is not bool:
            raise ContractValidationError("accepted context allow_pending flag is invalid")
        with self._lock:
            branch_root = self._branch_root(world_id, branch_id)
            receipts = self._load_receipts(branch_root)
            _validate_receipt_chain(
                receipts,
                world_id=world_id,
                branch_id=branch_id,
            )
            self._verify_or_advance_branch_cache(branch_root, receipts)
            return self._accepted_payloads(
                branch_root,
                receipts[-limit:],
                adult_full=adult_full,
                allow_pending=allow_pending,
            )

    def accepted_branch_payloads(
        self,
        *,
        world_id: str,
        branch_id: str,
    ) -> Sequence[Mapping[str, Any]]:
        """Return the whole verified branch in immutable receipt order.

        This reducer-facing view never exposes exact user source, exact accepted
        prose, protected adult authority, or the adult full record. A phase-one
        accepted receipt remains visible while its derived recording is
        pending, but no unfinished derived field is promoted into the payload.
        Consumers must keep the prior complete derived checkpoint.
        """

        with self._lock:
            branch_root = self._branch_root(world_id, branch_id)
            receipts = self._load_receipts(branch_root)
            _validate_receipt_chain(
                receipts,
                world_id=world_id,
                branch_id=branch_id,
            )
            self._verify_or_advance_branch_cache(branch_root, receipts)
            raw = self._accepted_payloads(
                branch_root,
                receipts,
                adult_full=False,
                allow_pending=True,
            )
            return tuple(_reducer_payload(value) for value in raw)

    def recent_ordinary_context_payloads(
        self,
        *,
        world_id: str,
        branch_id: str,
        limit: int = 6,
    ) -> tuple[dict[str, Any], ...]:
        """Return the ordinary route's exact bounded continuity view.

        Ordinary accepted prose remains available. Adult turns are represented
        only by their non-explicit projection; returning to Codex fails closed
        while that projection is pending.
        """

        raw = self._recent_context_payloads_with_all_pending(
            world_id=world_id,
            branch_id=branch_id,
            limit=limit,
            adult_full=False,
        )
        output: list[dict[str, Any]] = []
        for value in raw:
            receipt = _payload_receipt(value)
            if receipt.route is SceneRoute.ADULT:
                if "adult_projection" not in value:
                    raise StateConflictError(
                        "ordinary continuity requires the pending adult projection"
                    )
                output.append(_reducer_payload(value))
            else:
                output.append(value)
        return tuple(output)

    def recent_adult_context_payloads(
        self,
        *,
        world_id: str,
        branch_id: str,
        limit: int = 6,
    ) -> tuple[dict[str, Any], ...]:
        """Return protected recent continuity for the DeepSeek adult owner.

        This is the only context API that may return exact accepted adult prose
        or a protected adult full record. It must never be passed to Codex.
        """

        return self._recent_context_payloads_with_all_pending(
            world_id=world_id,
            branch_id=branch_id,
            limit=limit,
            adult_full=True,
        )

    def _recent_context_payloads_with_all_pending(
        self,
        *,
        world_id: str,
        branch_id: str,
        limit: int,
        adult_full: bool,
    ) -> tuple[dict[str, Any], ...]:
        """Keep the presentation tail plus every unresolved accepted turn.

        A pending Recorder attachment is branch authority even after more than
        ``limit`` later turns have been accepted.  Omitting it here could let a
        route owner proceed without knowing that derived custody is incomplete.
        """

        if type(limit) is not int or not 1 <= limit <= 20:
            raise ContractValidationError("accepted context limit is invalid")
        with self._lock:
            branch_root = self._branch_root(world_id, branch_id)
            receipts = self._load_receipts(branch_root)
            _validate_receipt_chain(
                receipts,
                world_id=world_id,
                branch_id=branch_id,
            )
            self._verify_or_advance_branch_cache(branch_root, receipts)
            all_payloads = self._accepted_payloads(
                branch_root,
                receipts,
                adult_full=adult_full,
                allow_pending=True,
            )
            tail_start = max(0, len(all_payloads) - limit)
            selected = [
                value
                for index, value in enumerate(all_payloads)
                if index >= tail_start
                or value.get("recording_status") != RecordingStatus.COMPLETE.value
            ]
            return tuple(selected)

    @staticmethod
    def _accepted_payloads(
        branch_root: Path,
        receipts: Sequence[LeanAcceptedTurnReceiptV1],
        *,
        adult_full: bool,
        allow_pending: bool,
    ) -> tuple[dict[str, Any], ...]:
        output: list[dict[str, Any]] = []
        for receipt in receipts:
            turn_dir = branch_root / "accepted" / _turn_directory_name(receipt)
            head = _read_recording_head(turn_dir, accepted=receipt)
            if head.status is not RecordingStatus.COMPLETE:
                recovered = _recover_atomic_recording_bundle(
                    turn_dir,
                    accepted=receipt,
                    head=head,
                )
                if recovered is None:
                    orphan = _recover_orphan_recording_attempt(
                        turn_dir,
                        accepted=receipt,
                        head=head,
                    )
                    if orphan is not None:
                        head = _read_recording_head(turn_dir, accepted=receipt)
                    if not allow_pending:
                        raise StateConflictError(
                            "accepted turn recording is incomplete and cannot enter context"
                        )
                    if head.status is RecordingStatus.PENDING_REPAIR:
                        _load_attempt_from_head(
                            turn_dir,
                            accepted=receipt,
                            head=head,
                        )
                    item = {
                        "receipt": to_primitive(receipt),
                        "recording_status": head.status.value,
                    }
                    output.append(item)
                    continue
                bundle = recovered
            else:
                bundle = _load_complete_recording_bundle(
                    turn_dir,
                    accepted=receipt,
                    head=head,
                )
            item: dict[str, Any] = {
                "receipt": to_primitive(receipt),
                "recording_status": RecordingStatus.COMPLETE.value,
            }
            if bundle.ordinary_record is not None:
                item["ordinary_record"] = to_primitive(bundle.ordinary_record)
            if bundle.adult_projection is not None:
                item["adult_projection"] = to_primitive(bundle.adult_projection)
            if adult_full and bundle.adult_full_record is not None:
                item["adult_full_record"] = to_primitive(bundle.adult_full_record)
            output.append(item)
        return tuple(output)

    def _load_receipts(self, branch_root: Path) -> list[LeanAcceptedTurnReceiptV1]:
        receipts: list[LeanAcceptedTurnReceiptV1] = []
        for path in sorted((branch_root / "accepted").glob("*/ACCEPTED_RECEIPT.json")):
            receipt = _accepted_receipt_from_mapping(_read_json(path))
            _verify_semantic_validation_artifact(path.parent, receipt=receipt)
            receipts.append(receipt)
        receipts.sort(key=lambda value: value.generation)
        if len({value.generation for value in receipts}) != len(receipts):
            raise StateConflictError("accepted branch contains duplicate generations")
        return receipts

    def _receipt_by_turn_id(
        self,
        branch_root: Path,
        turn_id: str,
    ) -> LeanAcceptedTurnReceiptV1 | None:
        receipts = self._load_receipts(branch_root)
        if receipts:
            _validate_receipt_chain(
                receipts,
                world_id=receipts[0].world_id,
                branch_id=receipts[0].branch_id,
            )
        self._verify_or_advance_branch_cache(branch_root, receipts)
        matches = [value for value in receipts if value.accepted_turn_id == turn_id]
        if len(matches) > 1:
            raise StateConflictError("accepted turn identity occurs more than once")
        return matches[0] if matches else None

    def _accepted_turn_dir(self, accepted: LeanAcceptedTurnReceiptV1) -> Path:
        branch_root = self._branch_root(accepted.world_id, accepted.branch_id)
        receipts = self._load_receipts(branch_root)
        _validate_receipt_chain(
            receipts,
            world_id=accepted.world_id,
            branch_id=accepted.branch_id,
        )
        self._verify_or_advance_branch_cache(branch_root, receipts)
        turn_dir = branch_root / "accepted" / _turn_directory_name(accepted)
        if not turn_dir.is_dir():
            raise StateConflictError("accepted turn directory is missing")
        stored = _accepted_receipt_from_mapping(_read_json(turn_dir / "ACCEPTED_RECEIPT.json"))
        if stored.receipt_sha256 != accepted.receipt_sha256:
            raise StateConflictError("accepted turn receipt differs from stored bytes")
        return turn_dir

    @staticmethod
    def _next_recording_attempt(turn_dir: Path) -> int:
        numbers: list[int] = []
        for path in turn_dir.glob("RECORDING_ATTEMPT_*.json"):
            match = re.fullmatch(r"RECORDING_ATTEMPT_(\d{4})\.json", path.name)
            if match:
                numbers.append(int(match.group(1)))
        for path in turn_dir.glob("RECORDING_BUNDLE_*"):
            match = re.fullmatch(r"RECORDING_BUNDLE_(\d{4})", path.name)
            if match and path.is_dir():
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
    def _write_branch_cache(branch_root: Path, receipt: LeanAcceptedTurnReceiptV1) -> None:
        _atomic_write_json(
            branch_root / "BRANCH_HEAD_CACHE.json",
            to_primitive(
                _BranchHeadCacheV1(
                    schema_version=_BranchHeadCacheV1.SCHEMA_VERSION,
                    generation=receipt.generation,
                    accepted_turn_id=receipt.accepted_turn_id,
                    accepted_head_sha256=receipt.receipt_sha256,
                )
            ),
        )

    @classmethod
    def _verify_or_advance_branch_cache(
        cls,
        branch_root: Path,
        receipts: Sequence[LeanAcceptedTurnReceiptV1],
    ) -> None:
        """Verify the closed anchor, advancing only from an exact ancestor.

        An exact ancestor is the only recoverable state: it represents a crash
        after atomic receipt publication and before the small cache update.
        Missing, forward, or same-generation-different-hash anchors fail closed.
        """

        path = branch_root / "BRANCH_HEAD_CACHE.json"
        if not path.exists():
            raise StateConflictError("accepted branch head cache is missing")
        cache = _decode_stored(
            _BranchHeadCacheV1,
            _read_json(path),
            "branch-head cache",
        )
        if not receipts:
            if cache.generation != 0:
                raise StateConflictError("empty accepted branch has a non-root head cache")
            return
        latest = receipts[-1]
        if (
            cache.generation == latest.generation
            and cache.accepted_turn_id == latest.accepted_turn_id
            and cache.accepted_head_sha256 == latest.receipt_sha256
        ):
            return
        anchor_matches = (cache.generation == 0 and len(receipts) == 1) or any(
            value.generation == cache.generation
            and value.accepted_turn_id == cache.accepted_turn_id
            and value.receipt_sha256 == cache.accepted_head_sha256
            for value in receipts
        )
        if not anchor_matches or latest.generation != cache.generation + 1:
            raise StateConflictError("accepted branch head cache conflicts with receipts")
        cls._write_branch_cache(branch_root, latest)


def _turn_directory_name(receipt: LeanAcceptedTurnReceiptV1) -> str:
    return f"{receipt.generation:08d}-{text_sha256(receipt.accepted_turn_id)[:20]}"


def _validate_candidate_qualification(
    candidate: LeanCandidateV1,
    validation: BoundSemanticValidationV1 | None,
) -> None:
    requires_validation = (
        candidate.route is SceneRoute.ORDINARY
        and candidate.primary_authority_kind == "codex_cognition_plan"
    )
    if not requires_validation:
        if validation is not None:
            raise ContractValidationError("semantic validation cannot qualify this candidate route")
        return
    if validation is None:
        raise ContractValidationError("cognition candidate requires a passing semantic validation")
    if validation.verdict.verdict is not SemanticVerdict.PASS:
        raise ContractValidationError("rejected semantic validation cannot authorize Accept")
    custody = validation.custody
    request = validation.request
    if (
        custody.candidate_id != candidate.candidate_id
        or custody.world_id != candidate.world_id
        or custody.branch_id != candidate.branch_id
        or custody.accepted_head_sha256 != candidate.accepted_head_before_sha256
        or request.exact_current_source != candidate.exact_user_source
        or request.exact_candidate_prose != candidate.story_text
        or canonical_json(request.cognition_plan) != candidate.primary_authority_json
    ):
        raise ContractValidationError(
            "semantic validation does not bind the exact cognition candidate"
        )


def _semantic_validation_artifact(
    *,
    candidate: LeanCandidateV1,
    validation: BoundSemanticValidationV1,
) -> dict[str, Any]:
    _validate_candidate_qualification(candidate, validation)
    body = {
        "schema_version": "cera.pi_scene.semantic_acceptance.v1",
        "candidate_sha256": candidate.candidate_sha256,
        "validation": to_primitive(validation),
    }
    return {**body, "artifact_sha256": canonical_sha256(body)}


def _verify_semantic_validation_artifact(
    turn_dir: Path,
    *,
    receipt: LeanAcceptedTurnReceiptV1,
) -> None:
    path = turn_dir / "SEMANTIC_VALIDATION.json"
    requires_validation = (
        receipt.route is SceneRoute.ORDINARY
        and receipt.primary_authority_kind == "codex_cognition_plan"
    )
    if not requires_validation:
        if path.exists():
            raise StateConflictError(
                "accepted turn has an unauthorized semantic-validation artifact"
            )
        return
    if not path.is_file() or path.is_symlink():
        raise StateConflictError("accepted cognition turn lacks semantic-validation custody")
    payload = _read_json(path)
    expected = {
        "schema_version",
        "candidate_sha256",
        "validation",
        "artifact_sha256",
    }
    if set(payload) != expected:
        raise StateConflictError("semantic-validation artifact fields changed")
    body = {key: payload[key] for key in payload if key != "artifact_sha256"}
    if (
        payload["schema_version"] != "cera.pi_scene.semantic_acceptance.v1"
        or payload["candidate_sha256"] != receipt.candidate_sha256
        or payload["artifact_sha256"] != canonical_sha256(body)
        or not isinstance(payload["validation"], Mapping)
    ):
        raise StateConflictError("semantic-validation artifact binding changed")
    validation = _decode_stored(
        BoundSemanticValidationV1,
        payload["validation"],
        "semantic validation",
    )
    request = validation.request
    custody = validation.custody
    if (
        validation.verdict.verdict is not SemanticVerdict.PASS
        or custody.world_id != receipt.world_id
        or custody.branch_id != receipt.branch_id
        or custody.accepted_head_sha256 != receipt.parent_accepted_head_sha256
        or request.exact_current_source != receipt.exact_user_source
        or request.exact_candidate_prose != receipt.exact_accepted_prose
        or canonical_json(request.cognition_plan) != receipt.primary_authority_json
    ):
        raise StateConflictError("stored semantic validation changed accepted authority")


def _load_current_pi_session(
    path: Path,
    *,
    head: LeanAcceptedHeadV1,
) -> AcceptedPiSessionV1 | None:
    """Return a current soft cache entry; silently ignore a valid stale one."""

    if not path.exists():
        return None
    session = _decode_stored(
        AcceptedPiSessionV1,
        _read_json(path),
        "accepted Pi session",
    )
    if session.accepted_turn_id != head.accepted_turn_id:
        return None
    return session


def _payload_receipt(value: Mapping[str, Any]) -> LeanAcceptedTurnReceiptV1:
    receipt = value.get("receipt")
    if not isinstance(receipt, Mapping):
        raise StateConflictError("accepted payload omitted its receipt")
    return _accepted_receipt_from_mapping(receipt)


def _reducer_payload(value: Mapping[str, Any]) -> dict[str, Any]:
    """Remove exact source/prose and protected adult authority from state input."""

    receipt = _payload_receipt(value)
    primitive = to_primitive(receipt)
    common_fields = (
        "schema_version",
        "accepted_turn_id",
        "parent_accepted_turn_id",
        "parent_accepted_head_sha256",
        "world_id",
        "branch_id",
        "scene_id",
        "generation",
        "route",
        "exact_user_source_sha256",
        "exact_accepted_prose_sha256",
        "primary_authority_kind",
        "primary_authority_sha256",
        "writer_view_manifest_sha256",
        "creator_action",
        "initial_recording_status",
        "candidate_sha256",
    )
    projected_receipt = {name: primitive[name] for name in common_fields}
    if receipt.route is SceneRoute.ORDINARY:
        projected_receipt["primary_authority_json"] = primitive["primary_authority_json"]
    output: dict[str, Any] = {
        "receipt": projected_receipt,
        "accepted_receipt_sha256": receipt.receipt_sha256,
        "recording_status": value.get("recording_status"),
    }
    for name in ("ordinary_record", "adult_projection"):
        if name in value:
            output[name] = value[name]
    return output


def _validate_receipt_chain(
    receipts: Sequence[LeanAcceptedTurnReceiptV1],
    *,
    world_id: str,
    branch_id: str,
) -> None:
    parent: str | None = None
    parent_head_sha256: str | None = None
    for index, receipt in enumerate(receipts, start=1):
        if receipt.world_id != world_id or receipt.branch_id != branch_id:
            raise StateConflictError("accepted receipt escaped its branch identity")
        if receipt.generation != index:
            raise StateConflictError("accepted turn generations are not contiguous")
        if receipt.parent_accepted_turn_id != parent:
            raise StateConflictError("accepted turn parent chain is inconsistent")
        if receipt.parent_accepted_head_sha256 != parent_head_sha256:
            raise StateConflictError("accepted receipt hash chain is inconsistent")
        parent = receipt.accepted_turn_id
        parent_head_sha256 = receipt.receipt_sha256


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


def _read_recording_head(
    turn_dir: Path,
    *,
    accepted: LeanAcceptedTurnReceiptV1,
) -> _RecordingHeadV1:
    head = _decode_stored(
        _RecordingHeadV1,
        _read_json(turn_dir / "RECORDING_HEAD.json"),
        "recording head",
    )
    if head.accepted_turn_id != accepted.accepted_turn_id:
        raise StateConflictError("recording head changed accepted turn identity")
    return head


def _phase_one_recording_state(
    accepted: LeanAcceptedTurnReceiptV1,
) -> LeanRecordingAttemptV1:
    """Represent the immutable phase-one Accept head without inventing an attempt file."""

    return LeanRecordingAttemptV1(
        schema_version=LeanRecordingAttemptV1.SCHEMA_VERSION,
        accepted_turn_id=accepted.accepted_turn_id,
        attempt_number=0,
        status=RecordingStatus.PROJECTION_PENDING,
        recorder_request_sha256=canonical_sha256(
            {
                "schema_version": "cera.pi_scene.phase_one_recording_state.v1",
                "accepted_receipt_sha256": accepted.receipt_sha256,
            }
        ),
        recorder_output_sha256=None,
        provider_operations=0,
    )


def _load_attempt_from_head(
    turn_dir: Path,
    *,
    accepted: LeanAcceptedTurnReceiptV1,
    head: _RecordingHeadV1,
) -> LeanRecordingAttemptV1:
    if head.attempt_number < 1 or head.attempt_sha256 is None:
        raise StateConflictError("recording head does not identify an immutable attempt")
    attempt = _recording_attempt_from_mapping(
        _read_json(turn_dir / f"RECORDING_ATTEMPT_{head.attempt_number:04d}.json")
    )
    if attempt.accepted_turn_id != accepted.accepted_turn_id:
        raise StateConflictError("recording attempt changed accepted turn identity")
    if attempt.attempt_number != head.attempt_number:
        raise StateConflictError("recording attempt number differs from its head")
    if canonical_sha256(attempt) != head.attempt_sha256:
        raise StateConflictError("recording attempt differs from its head")
    if attempt.status is not head.status:
        raise StateConflictError("recording attempt status differs from its head")
    return attempt


def _recover_orphan_recording_attempt(
    turn_dir: Path,
    *,
    accepted: LeanAcceptedTurnReceiptV1,
    head: _RecordingHeadV1,
) -> LeanRecordingAttemptV1 | None:
    """Advance a head across one fully-written failed attempt after a crash.

    Complete attempts are recovered through their atomic bundle instead.  A
    gap, multiple candidates, or a different status is ambiguous and therefore
    fails closed rather than inventing publication order.
    """

    if head.status is RecordingStatus.COMPLETE:
        return None
    if head.status is RecordingStatus.PENDING_REPAIR:
        _load_attempt_from_head(turn_dir, accepted=accepted, head=head)

    numbered: list[tuple[int, Path]] = []
    for path in turn_dir.glob("RECORDING_ATTEMPT_*.json"):
        match = re.fullmatch(r"RECORDING_ATTEMPT_(\d{4})\.json", path.name)
        if match is not None:
            numbered.append((int(match.group(1)), path))
    candidates = sorted((number, path) for number, path in numbered if number > head.attempt_number)
    if not candidates:
        return None
    if len(candidates) != 1 or candidates[0][0] != head.attempt_number + 1:
        raise StateConflictError("recording attempts have ambiguous orphan publication")

    number, path = candidates[0]
    attempt = _recording_attempt_from_mapping(_read_json(path))
    if attempt.accepted_turn_id != accepted.accepted_turn_id or attempt.attempt_number != number:
        raise StateConflictError("orphan recording attempt changed its identity")
    if attempt.status is not RecordingStatus.PENDING_REPAIR:
        raise StateConflictError("orphan complete recording attempt is missing its atomic bundle")
    _atomic_write_json(
        turn_dir / "RECORDING_HEAD.json",
        _recording_head_payload(
            accepted_turn_id=accepted.accepted_turn_id,
            status=attempt.status,
            attempt_number=attempt.attempt_number,
            attempt_sha256=canonical_sha256(attempt),
        ),
    )
    recovered_head = _read_recording_head(turn_dir, accepted=accepted)
    recovered = _load_attempt_from_head(
        turn_dir,
        accepted=accepted,
        head=recovered_head,
    )
    if recovered != attempt:
        raise StateConflictError("orphan recording attempt changed during recovery")
    return recovered


def _recording_bundle_path(turn_dir: Path, attempt_number: int) -> Path:
    return turn_dir / f"RECORDING_BUNDLE_{attempt_number:04d}"


def _bundle_manifest(
    bundle: _CompleteRecordingBundle,
    *,
    accepted: LeanAcceptedTurnReceiptV1,
) -> _RecordingBundleManifestV2:
    attempt = bundle.attempt
    if attempt.status is not RecordingStatus.COMPLETE:
        raise ContractValidationError("atomic recording bundle requires a complete attempt")
    route = SceneRoute.ORDINARY if bundle.ordinary_record is not None else SceneRoute.ADULT
    manifest = _RecordingBundleManifestV2(
        schema_version=_RecordingBundleManifestV2.SCHEMA_VERSION,
        accepted_turn_id=attempt.accepted_turn_id,
        accepted_receipt_sha256=accepted.receipt_sha256,
        exact_accepted_prose_sha256=accepted.exact_accepted_prose_sha256,
        primary_authority_sha256=accepted.primary_authority_sha256,
        attempt_number=attempt.attempt_number,
        attempt_sha256=canonical_sha256(attempt),
        route=route,
        ordinary_record_sha256=(
            None if bundle.ordinary_record is None else canonical_sha256(bundle.ordinary_record)
        ),
        adult_full_record_sha256=(
            None if bundle.adult_full_record is None else canonical_sha256(bundle.adult_full_record)
        ),
        adult_projection_sha256=(
            None if bundle.adult_projection is None else canonical_sha256(bundle.adult_projection)
        ),
    )
    if (
        manifest.ordinary_record_sha256 != attempt.ordinary_record_sha256
        or manifest.adult_full_record_sha256 != attempt.adult_full_record_sha256
        or manifest.adult_projection_sha256 != attempt.adult_projection_sha256
    ):
        raise ContractValidationError("recording bundle differs from its attempt hashes")
    return manifest


def _publish_atomic_recording_bundle(
    turn_dir: Path,
    *,
    accepted: LeanAcceptedTurnReceiptV1,
    bundle: _CompleteRecordingBundle,
) -> None:
    """Atomically publish one immutable route-shaped record bundle.

    ``RECORDING_HEAD.json`` remains the commit marker. A crash before its update
    leaves either no bundle or one complete directory that restart reconciliation
    can verify and finish without repeating Recorder work.
    """

    manifest = _bundle_manifest(bundle, accepted=accepted)
    if manifest.accepted_turn_id != accepted.accepted_turn_id:
        raise StateConflictError("recording bundle changed accepted turn identity")
    if manifest.route is not accepted.route:
        raise StateConflictError("recording bundle route differs from accepted turn")
    final = _recording_bundle_path(turn_dir, bundle.attempt.attempt_number)
    if final.exists():
        existing = _load_recording_bundle_directory(final, accepted=accepted)
        if existing != bundle:
            raise StateConflictError("immutable recording bundle changed")
        return
    stage = turn_dir / f".{final.name}.{uuid4().hex}.tmp"
    stage.mkdir(parents=False, exist_ok=False)
    try:
        _write_new_json(stage / "BUNDLE_MANIFEST.json", to_primitive(manifest))
        _write_new_json(
            stage / "RECORDING_ATTEMPT.json",
            to_primitive(bundle.attempt),
        )
        if bundle.ordinary_record is not None:
            _write_new_json(
                stage / "ORDINARY_RECORD.json",
                to_primitive(bundle.ordinary_record),
            )
        else:
            if bundle.adult_full_record is None or bundle.adult_projection is None:
                raise ContractValidationError("adult recording bundle is incomplete")
            _write_new_json(
                stage / "ADULT_FULL_RECORD.json",
                to_primitive(bundle.adult_full_record),
            )
            _write_new_json(
                stage / "ADULT_CODEX_PROJECTION.json",
                to_primitive(bundle.adult_projection),
            )
        os.replace(stage, final)
    except Exception:
        if stage.exists():
            shutil.rmtree(stage)
        if final.exists():
            existing = _load_recording_bundle_directory(final, accepted=accepted)
            if existing == bundle:
                return
        raise


def _recover_atomic_recording_bundle(
    turn_dir: Path,
    *,
    accepted: LeanAcceptedTurnReceiptV1,
    head: _RecordingHeadV1,
) -> _CompleteRecordingBundle | None:
    if head.status is RecordingStatus.COMPLETE:
        return None
    paths = sorted(
        path
        for path in turn_dir.glob("RECORDING_BUNDLE_*")
        if path.is_dir() and re.fullmatch(r"RECORDING_BUNDLE_\d{4}", path.name) is not None
    )
    if not paths:
        return None
    if len(paths) != 1:
        raise StateConflictError("accepted turn has ambiguous recording bundles")
    bundle = _load_recording_bundle_directory(paths[0], accepted=accepted)
    _finalize_recording_bundle(
        turn_dir,
        accepted=accepted,
        bundle=bundle,
        prior_head=head,
    )
    return bundle


def _finalize_recording_bundle(
    turn_dir: Path,
    *,
    accepted: LeanAcceptedTurnReceiptV1,
    bundle: _CompleteRecordingBundle,
    prior_head: _RecordingHeadV1,
) -> None:
    canonical = _load_recording_bundle_directory(
        _recording_bundle_path(turn_dir, bundle.attempt.attempt_number),
        accepted=accepted,
    )
    if canonical != bundle:
        raise StateConflictError("recording bundle changed before finalization")
    current = _read_recording_head(turn_dir, accepted=accepted)
    if current.status is RecordingStatus.COMPLETE:
        existing = _load_complete_recording_bundle(
            turn_dir,
            accepted=accepted,
            head=current,
        )
        if existing != bundle:
            raise StateConflictError("completed recording differs from staged bundle")
        return
    if current != prior_head:
        raise StateConflictError("recording head changed during bundle publication")
    _materialize_recording_bundle_compatibility(
        turn_dir,
        accepted=accepted,
        bundle=bundle,
        allow_repair=True,
    )
    _atomic_write_json(
        turn_dir / "RECORDING_HEAD.json",
        _recording_head_payload(
            accepted_turn_id=accepted.accepted_turn_id,
            status=RecordingStatus.COMPLETE,
            attempt_number=bundle.attempt.attempt_number,
            attempt_sha256=canonical_sha256(bundle.attempt),
        ),
    )
    completed_head = _read_recording_head(turn_dir, accepted=accepted)
    _load_complete_recording_bundle(
        turn_dir,
        accepted=accepted,
        head=completed_head,
    )


def _materialize_recording_bundle_compatibility(
    turn_dir: Path,
    *,
    accepted: LeanAcceptedTurnReceiptV1,
    bundle: _CompleteRecordingBundle,
    allow_repair: bool,
) -> None:
    expected: dict[str, Mapping[str, Any]] = {
        f"RECORDING_ATTEMPT_{bundle.attempt.attempt_number:04d}.json": to_primitive(bundle.attempt)
    }
    if bundle.ordinary_record is not None:
        if (turn_dir / "ADULT_FULL_RECORD.json").exists() or (
            turn_dir / "ADULT_CODEX_PROJECTION.json"
        ).exists():
            raise StateConflictError("ordinary turn contains adult recording artifacts")
        expected["ORDINARY_RECORD.json"] = to_primitive(bundle.ordinary_record)
    else:
        if bundle.adult_full_record is None or bundle.adult_projection is None:
            raise StateConflictError("adult recording bundle is incomplete")
        if (turn_dir / "ORDINARY_RECORD.json").exists():
            raise StateConflictError("adult turn contains an ordinary recording artifact")
        expected["ADULT_FULL_RECORD.json"] = to_primitive(bundle.adult_full_record)
        expected["ADULT_CODEX_PROJECTION.json"] = to_primitive(bundle.adult_projection)
    for name, payload in expected.items():
        path = turn_dir / name
        text = canonical_json(dict(payload))
        if not path.exists():
            if not allow_repair:
                raise StateConflictError(f"recording compatibility artifact is missing: {name}")
            _atomic_write_json(path, payload)
            continue
        try:
            existing = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            if not allow_repair:
                raise StateConflictError(
                    f"recording compatibility artifact is unreadable: {name}"
                ) from exc
            existing = ""
        if existing == text:
            continue
        if not allow_repair:
            raise StateConflictError(f"recording compatibility artifact changed: {name}")
        _atomic_write_json(path, payload)


def _load_complete_recording_bundle(
    turn_dir: Path,
    *,
    accepted: LeanAcceptedTurnReceiptV1,
    head: _RecordingHeadV1,
) -> _CompleteRecordingBundle:
    if head.status is not RecordingStatus.COMPLETE:
        raise StateConflictError("attached record does not have complete status")
    bundle_path = _recording_bundle_path(turn_dir, head.attempt_number)
    if bundle_path.exists():
        bundle = _load_recording_bundle_directory(bundle_path, accepted=accepted)
        if (
            bundle.attempt.attempt_number != head.attempt_number
            or canonical_sha256(bundle.attempt) != head.attempt_sha256
        ):
            raise StateConflictError("recording bundle differs from its head")
        _materialize_recording_bundle_compatibility(
            turn_dir,
            accepted=accepted,
            bundle=bundle,
            allow_repair=False,
        )
        return bundle
    return _load_historical_complete_recording_bundle(
        turn_dir,
        accepted=accepted,
        head=head,
    )


def _load_recording_bundle_directory(
    path: Path,
    *,
    accepted: LeanAcceptedTurnReceiptV1,
) -> _CompleteRecordingBundle:
    if not path.is_dir():
        raise StateConflictError("recording bundle directory is missing")
    names = {item.name for item in path.iterdir()}
    common = {"BUNDLE_MANIFEST.json", "RECORDING_ATTEMPT.json"}
    ordinary_names = common | {"ORDINARY_RECORD.json"}
    adult_names = common | {
        "ADULT_FULL_RECORD.json",
        "ADULT_CODEX_PROJECTION.json",
    }
    if names not in (ordinary_names, adult_names):
        raise StateConflictError("recording bundle file set changed")
    manifest_payload = _read_json(path / "BUNDLE_MANIFEST.json")
    manifest_version = manifest_payload.get("schema_version")
    if manifest_version == _RecordingBundleManifestV1.SCHEMA_VERSION:
        manifest: _RecordingBundleManifest = _decode_stored(
            _RecordingBundleManifestV1,
            manifest_payload,
            "recording bundle manifest",
        )
    elif manifest_version == _RecordingBundleManifestV2.SCHEMA_VERSION:
        manifest = _decode_stored(
            _RecordingBundleManifestV2,
            manifest_payload,
            "recording bundle manifest",
        )
    else:
        raise StateConflictError("recording bundle manifest version is unsupported")
    attempt = _recording_attempt_from_mapping(_read_json(path / "RECORDING_ATTEMPT.json"))
    if manifest.accepted_turn_id != accepted.accepted_turn_id:
        raise StateConflictError("recording bundle changed accepted turn identity")
    if manifest.route is not accepted.route:
        raise StateConflictError("recording bundle route differs from accepted turn")
    if isinstance(manifest, _RecordingBundleManifestV2) and (
        manifest.accepted_receipt_sha256 != accepted.receipt_sha256
        or manifest.exact_accepted_prose_sha256 != accepted.exact_accepted_prose_sha256
        or manifest.primary_authority_sha256 != accepted.primary_authority_sha256
    ):
        raise StateConflictError("recording bundle accepted custody changed")
    if (
        attempt.status is not RecordingStatus.COMPLETE
        or attempt.accepted_turn_id != accepted.accepted_turn_id
        or attempt.attempt_number != manifest.attempt_number
        or canonical_sha256(attempt) != manifest.attempt_sha256
        or attempt.ordinary_record_sha256 != manifest.ordinary_record_sha256
        or attempt.adult_full_record_sha256 != manifest.adult_full_record_sha256
        or attempt.adult_projection_sha256 != manifest.adult_projection_sha256
    ):
        raise StateConflictError("recording bundle attempt binding changed")
    if manifest.route is SceneRoute.ORDINARY:
        record = _decode_stored(
            OrdinarySceneRecordV1,
            _read_json(path / "ORDINARY_RECORD.json"),
            "ordinary scene record",
        )
        if canonical_sha256(record) != manifest.ordinary_record_sha256:
            raise StateConflictError("ordinary record differs from its bundle hash")
        _validate_stored_ordinary_record(record, accepted=accepted)
        return _CompleteRecordingBundle(attempt=attempt, ordinary_record=record)
    full = _decode_stored(
        AdultFullRecordV1,
        _read_json(path / "ADULT_FULL_RECORD.json"),
        "adult full record",
    )
    projection = _decode_adult_projection(
        _read_json(path / "ADULT_CODEX_PROJECTION.json"),
        label="adult Codex projection",
    )
    if canonical_sha256(full) != manifest.adult_full_record_sha256:
        raise StateConflictError("adult full record differs from its bundle hash")
    if canonical_sha256(projection) != manifest.adult_projection_sha256:
        raise StateConflictError("adult projection differs from its bundle hash")
    _validate_stored_adult_records(full, projection, accepted=accepted)
    return _CompleteRecordingBundle(
        attempt=attempt,
        adult_full_record=full,
        adult_projection=projection,
    )


def _load_historical_complete_recording_bundle(
    turn_dir: Path,
    *,
    accepted: LeanAcceptedTurnReceiptV1,
    head: _RecordingHeadV1,
) -> _CompleteRecordingBundle:
    """Decode pre-bundle V1 storage without rewriting its immutable bytes."""

    attempt = _load_attempt_from_head(turn_dir, accepted=accepted, head=head)
    if attempt.status is not RecordingStatus.COMPLETE:
        raise StateConflictError("historical recording attempt is not complete")
    if attempt.ordinary_record_sha256 is not None:
        record = _decode_stored(
            OrdinarySceneRecordV1,
            _read_json(turn_dir / "ORDINARY_RECORD.json"),
            "historical ordinary scene record",
        )
        if canonical_sha256(record) != attempt.ordinary_record_sha256:
            raise StateConflictError("historical ordinary record hash changed")
        _validate_stored_ordinary_record(record, accepted=accepted)
        return _CompleteRecordingBundle(attempt=attempt, ordinary_record=record)
    full = _decode_stored(
        AdultFullRecordV1,
        _read_json(turn_dir / "ADULT_FULL_RECORD.json"),
        "historical adult full record",
    )
    projection = _decode_adult_projection(
        _read_json(turn_dir / "ADULT_CODEX_PROJECTION.json"),
        label="historical adult Codex projection",
    )
    if canonical_sha256(full) != attempt.adult_full_record_sha256:
        raise StateConflictError("historical adult full-record hash changed")
    if canonical_sha256(projection) != attempt.adult_projection_sha256:
        raise StateConflictError("historical adult projection hash changed")
    _validate_stored_adult_records(full, projection, accepted=accepted)
    return _CompleteRecordingBundle(
        attempt=attempt,
        adult_full_record=full,
        adult_projection=projection,
    )


def _validate_stored_ordinary_record(
    record: OrdinarySceneRecordV1,
    *,
    accepted: LeanAcceptedTurnReceiptV1,
) -> None:
    try:
        validate_ordinary_record(record, accepted=accepted)
    except ContractValidationError as exc:
        raise StateConflictError(f"stored ordinary record is invalid: {exc}") from exc


def _validate_stored_adult_records(
    full: AdultFullRecordV1,
    projection: AdultCodexProjectionV1 | AdultCodexProjectionV2,
    *,
    accepted: LeanAcceptedTurnReceiptV1,
) -> None:
    try:
        validate_adult_records(full, projection, accepted=accepted)
    except ContractValidationError as exc:
        raise StateConflictError(f"stored adult records are invalid: {exc}") from exc


def _require_ordinary_bundle_hash(
    bundle: _CompleteRecordingBundle,
    expected_sha256: str,
) -> None:
    if (
        bundle.ordinary_record is None
        or canonical_sha256(bundle.ordinary_record) != expected_sha256
    ):
        raise StateConflictError("ordinary record changed after attachment")


def _require_adult_bundle_hashes(
    bundle: _CompleteRecordingBundle,
    expected_full_sha256: str,
    expected_projection_sha256: str,
) -> None:
    if (
        bundle.adult_full_record is None
        or bundle.adult_projection is None
        or canonical_sha256(bundle.adult_full_record) != expected_full_sha256
        or canonical_sha256(bundle.adult_projection) != expected_projection_sha256
    ):
        raise StateConflictError("adult dual record changed after attachment")


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


def _decode_stored[T](
    model_type: type[T],
    value: Mapping[str, Any],
    label: str,
) -> T:
    """Decode a closed durable DTO without primitive coercion."""

    try:
        return from_mapping(model_type, value)
    except (ContractValidationError, KeyError, TypeError, ValueError) as exc:
        raise StateConflictError(f"stored {label} is invalid: {exc}") from exc


def _accepted_receipt_from_mapping(value: Mapping[str, Any]) -> LeanAcceptedTurnReceiptV1:
    return _decode_stored(
        LeanAcceptedTurnReceiptV1,
        value,
        "accepted-turn receipt",
    )


def _recording_attempt_from_mapping(value: Mapping[str, Any]) -> LeanRecordingAttemptV1:
    return _decode_stored(
        LeanRecordingAttemptV1,
        value,
        "recording attempt",
    )


def ordinary_record_from_mapping(value: Mapping[str, Any]) -> OrdinarySceneRecordV1:
    return from_mapping(OrdinarySceneRecordV1, value)


def adult_full_record_from_mapping(value: Mapping[str, Any]) -> AdultFullRecordV1:
    return from_mapping(AdultFullRecordV1, value)


def adult_projection_from_mapping(
    value: Mapping[str, Any],
) -> AdultCodexProjectionV1 | AdultCodexProjectionV2:
    version = value.get("schema_version")
    if version == AdultCodexProjectionV1.SCHEMA_VERSION:
        return from_mapping(AdultCodexProjectionV1, value)
    if version == AdultCodexProjectionV2.SCHEMA_VERSION:
        return from_mapping(AdultCodexProjectionV2, value)
    raise ContractValidationError("adult Codex projection version is unsupported")


def _decode_adult_projection(
    value: Mapping[str, Any],
    *,
    label: str,
) -> AdultCodexProjectionV1 | AdultCodexProjectionV2:
    try:
        return adult_projection_from_mapping(value)
    except (ContractValidationError, KeyError, TypeError, ValueError) as exc:
        raise StateConflictError(f"stored {label} is invalid: {exc}") from exc
