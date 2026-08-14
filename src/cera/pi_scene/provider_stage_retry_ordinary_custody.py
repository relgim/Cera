"""Protected crash-safe request custody for ordinary stage continuation.

Raw normalized HTTP payloads and Planner retrieval material live only below
the trusted-local protected root.  SQLite and public Retry DTOs retain hashes
and identifiers only.  A request is redacted only after an explicitly named
stable disposition.
"""

from __future__ import annotations

import json
import msvcrt
import os
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Any, ClassVar
from uuid import uuid4

from cera.errors import ContractValidationError, StateConflictError
from cera.schema import from_mapping
from cera.serialization import (
    canonical_bytes,
    canonical_sha256,
    domain_sha256,
    re_is_sha256,
    to_primitive,
)

from .contracts import SceneRoute
from .provider_stage_retry import ProviderStage
from .provider_stage_retry_packets import ImmutableRetrievalSnapshotIdentityV1
from .provider_stage_retry_scope import ProviderStageRetryOccurrenceScopeV1
from .request_binding import PiSceneRequestBindingV1, binding_from_payload
from .review_store import LeanReviewRecordV1, LeanSceneTurnInputV1

_STABLE_REDACTION_DISPOSITIONS = frozenset(
    {
        "completed",
        "attempts_exhausted",
        "recording_repair_required",
        "recovery_required",
        "operator_abandoned",
    }
)
_ORDINARY_STAGE_VALUES = (
    ProviderStage.PLANNER.value,
    ProviderStage.WRITER.value,
    ProviderStage.SEMANTIC_VALIDATOR.value,
    ProviderStage.READER.value,
    ProviderStage.RECORDER.value,
)
_LEGACY_ORDINARY_STAGE_VALUES = (
    ProviderStage.PLANNER.value,
    ProviderStage.WRITER.value,
    ProviderStage.SEMANTIC_VALIDATOR.value,
    ProviderStage.RECORDER.value,
)


def _ordinary_stage_retry_identity_hashes(
    *,
    world_id: str,
    branch_id: str,
    request_id: str,
    generation_id: str,
    stage: str,
    stage_ordinal: int,
) -> tuple[str, str, str]:
    """Rebuild the canonical scope hashes needed to verify custody indexes.

    These domains intentionally mirror ``ProviderStageRetryOccurrenceScopeV1``
    and ``ProviderStageRetryIdentityV1.logical_key_sha256``.  The protected
    occurrence index does not retain the full scope, so readback must derive
    the same identities from its pending request custody.
    """

    request_sha256 = domain_sha256(
        "cera.provider_stage_retry_request_identity.v1",
        {
            "world_id": world_id,
            "branch_id": branch_id,
            "request_id": request_id,
            "generation_id": generation_id,
        },
    )
    request_occurrence_sha256 = domain_sha256(
        "cera.provider_stage_retry_occurrence.v1",
        {
            "world_id": world_id,
            "branch_id": branch_id,
            "request_id": request_id,
            "generation_id": generation_id,
            "stage": stage,
            "stage_ordinal": stage_ordinal,
        },
    )
    chain_id = "stage-retry-" + domain_sha256(
        "cera.provider_stage_retry_logical_key.v1",
        {
            "stage": stage,
            "request_occurrence_sha256": request_occurrence_sha256,
        },
    )
    return request_sha256, request_occurrence_sha256, chain_id


def _require_sha256(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not re_is_sha256(value):
        raise ContractValidationError(f"ordinary protected custody {field_name} is invalid")


def _require_request_id(request_id: str) -> None:
    if (
        not isinstance(request_id, str)
        or not request_id.startswith("request-")
        or not re_is_sha256(request_id.removeprefix("request-"))
    ):
        raise ContractValidationError("ordinary protected custody request ID is invalid")


def _require_chain_id(chain_id: str) -> None:
    if (
        not isinstance(chain_id, str)
        or not chain_id.startswith("stage-retry-")
        or not re_is_sha256(chain_id.removeprefix("stage-retry-"))
    ):
        raise ContractValidationError("ordinary protected custody chain ID is invalid")


def _require_stage_action_id(action_id: str) -> None:
    if (
        not isinstance(action_id, str)
        or not action_id.startswith("stage-action-")
        or not re_is_sha256(action_id.removeprefix("stage-action-"))
    ):
        raise ContractValidationError("ordinary protected custody stage action ID is invalid")


def _require_identifier(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise ContractValidationError(f"ordinary protected custody {field_name} is invalid")


def _branch_scope_sha256(world_id: str, branch_id: str) -> str:
    if (
        not isinstance(world_id, str)
        or not world_id.strip()
        or not isinstance(branch_id, str)
        or not branch_id.strip()
    ):
        raise ContractValidationError("ordinary protected custody branch scope is invalid")
    return domain_sha256(
        "cera.ordinary_stage_retry_branch_scope.v1",
        {"world_id": world_id, "branch_id": branch_id},
    )


def _normalize_stage_occurrence_counts(
    value: Mapping[str, int],
) -> dict[str, int]:
    if not isinstance(value, Mapping) or frozenset(value) not in {
        frozenset(_ORDINARY_STAGE_VALUES),
        frozenset(_LEGACY_ORDINARY_STAGE_VALUES),
    }:
        raise ContractValidationError(
            "ordinary protected custody stage occurrence counts changed shape"
        )
    normalized: dict[str, int] = {}
    for stage in _ORDINARY_STAGE_VALUES:
        count = value.get(stage, 0)
        if type(count) is not int or count < 0:
            raise ContractValidationError(
                "ordinary protected custody stage occurrence count is invalid"
            )
        normalized[stage] = count
    return normalized


@dataclass(frozen=True, slots=True)
class ProtectedOrdinaryPendingRequestV1:
    """Verified protected material needed for exact local pipeline replay."""

    binding: PiSceneRequestBindingV1
    normalized_request: Mapping[str, Any]
    generation: int
    turn_input: LeanSceneTurnInputV1
    retrieval_snapshot: ImmutableRetrievalSnapshotIdentityV1
    tool_result_bundle: object
    context_sha256: str

    def __post_init__(self) -> None:
        if type(self.binding) is not PiSceneRequestBindingV1:
            raise ContractValidationError("ordinary pending request binding changed")
        if not isinstance(self.normalized_request, Mapping):
            raise ContractValidationError("ordinary pending normalized request changed")
        request_bytes = canonical_bytes(self.normalized_request)
        if (
            len(request_bytes) != self.binding.normalized_request_size_bytes
            or canonical_sha256(self.normalized_request) != self.binding.normalized_request_sha256
        ):
            # canonical_sha256(object) equals SHA-256(canonical bytes).
            raise StateConflictError("ordinary pending normalized request changed binding")
        if type(self.generation) is not int or self.generation < 1:
            raise ContractValidationError("ordinary pending generation is invalid")
        if type(self.turn_input) is not LeanSceneTurnInputV1:
            raise ContractValidationError("ordinary pending turn input changed")
        if (self.turn_input.world_id, self.turn_input.branch_id) != (
            self.binding.world_id,
            self.binding.branch_id,
        ) or canonical_sha256(
            to_primitive(self.turn_input.request_controls)
        ) != self.binding.controls_sha256:
            raise StateConflictError("ordinary pending turn changed HTTP custody")
        if type(self.retrieval_snapshot) is not ImmutableRetrievalSnapshotIdentityV1:
            raise ContractValidationError("ordinary pending retrieval snapshot changed")
        _require_sha256(self.context_sha256, "context hash")
        if self.context_sha256 != canonical_sha256(self.context_payload()):
            raise StateConflictError("ordinary pending request context changed")

    def context_payload(self) -> dict[str, Any]:
        return {
            "schema_version": "cera.ordinary_stage_retry_request_context.v1",
            "request_binding_sha256": canonical_sha256(self.binding.to_payload()),
            "generation": self.generation,
            "turn_context": to_primitive(self.turn_input),
            "turn_context_sha256": canonical_sha256(self.turn_input),
            "retrieval_snapshot": self.retrieval_snapshot.to_payload(),
            "tool_result_bundle": to_primitive(self.tool_result_bundle),
        }


@dataclass(frozen=True, slots=True)
class ProtectedOrdinaryCustodyReceiptV1:
    """Content-free receipt safe for local control flow and tests."""

    request_id: str
    request_binding_sha256: str
    normalized_request_sha256: str
    context_sha256: str
    disposition: str
    branch_barrier_active: bool

    def __post_init__(self) -> None:
        _require_request_id(self.request_id)
        for value, name in (
            (self.request_binding_sha256, "request binding hash"),
            (self.normalized_request_sha256, "normalized request hash"),
            (self.context_sha256, "context hash"),
        ):
            _require_sha256(value, name)
        if self.disposition != "active" and self.disposition not in (
            _STABLE_REDACTION_DISPOSITIONS
        ):
            raise ContractValidationError("ordinary protected custody disposition is invalid")
        if type(self.branch_barrier_active) is not bool:
            raise ContractValidationError("ordinary protected custody barrier changed")


@dataclass(frozen=True, slots=True)
class ProtectedOrdinaryChainContextV1:
    chain_id: str
    request_id: str
    context_sha256: str

    def __post_init__(self) -> None:
        _require_chain_id(self.chain_id)
        _require_request_id(self.request_id)
        _require_sha256(self.context_sha256, "chain context hash")


@dataclass(frozen=True, slots=True)
class ProtectedOrdinaryTerminalResponseReceiptV1:
    request_id: str
    response_sha256: str
    response_size_bytes: int

    def __post_init__(self) -> None:
        _require_request_id(self.request_id)
        _require_sha256(self.response_sha256, "terminal response hash")
        if type(self.response_size_bytes) is not int or self.response_size_bytes < 2:
            raise ContractValidationError("ordinary terminal response size is invalid")


@dataclass(frozen=True, slots=True)
class ProtectedOrdinaryChainRequestIdentityV1:
    chain_id: str
    request_id: str
    request_sha256: str
    context_sha256: str

    def __post_init__(self) -> None:
        _require_chain_id(self.chain_id)
        _require_request_id(self.request_id)
        _require_sha256(self.request_sha256, "provider-stage request hash")
        _require_sha256(self.context_sha256, "chain request context hash")


@dataclass(frozen=True, slots=True)
class ProtectedOrdinaryReviewRequestIdentityV1:
    """Content-free immutable binding from one review to its original request."""

    review_id: str
    request_id: str
    world_id: str
    branch_id: str
    generation: int
    context_sha256: str
    turn_context_sha256: str
    candidate_id: str
    candidate_sha256: str
    stage_occurrence_counts_sha256: str

    def __post_init__(self) -> None:
        for value, field_name in (
            (self.review_id, "review ID"),
            (self.world_id, "review world ID"),
            (self.branch_id, "review branch ID"),
            (self.candidate_id, "review candidate ID"),
        ):
            _require_identifier(value, field_name)
        _require_request_id(self.request_id)
        if type(self.generation) is not int or self.generation < 1:
            raise ContractValidationError("ordinary protected custody review generation is invalid")
        for value, field_name in (
            (self.context_sha256, "review context hash"),
            (self.turn_context_sha256, "review turn hash"),
            (self.candidate_sha256, "review candidate hash"),
            (
                self.stage_occurrence_counts_sha256,
                "review stage occurrence counts hash",
            ),
        ):
            _require_sha256(value, field_name)


@dataclass(frozen=True, slots=True)
class ProtectedOrdinaryReviewActionIdentityV1:
    """Content-free identity for one exact creator action under Request A."""

    action_id: str
    review_id: str
    request_id: str
    world_id: str
    branch_id: str
    context_sha256: str
    normalized_action_sha256: str
    stage_occurrence_counts_sha256: str

    def __post_init__(self) -> None:
        for value, field_name in (
            (self.action_id, "review action ID"),
            (self.review_id, "review action review ID"),
            (self.world_id, "review action world ID"),
            (self.branch_id, "review action branch ID"),
        ):
            _require_identifier(value, field_name)
        if not self.action_id.startswith("review-action-") or not re_is_sha256(
            self.action_id.removeprefix("review-action-")
        ):
            raise ContractValidationError("ordinary protected custody review action ID is invalid")
        _require_request_id(self.request_id)
        for value, field_name in (
            (self.context_sha256, "review action context hash"),
            (self.normalized_action_sha256, "normalized review action hash"),
            (
                self.stage_occurrence_counts_sha256,
                "review action stage occurrence counts hash",
            ),
        ):
            _require_sha256(value, field_name)


@dataclass(frozen=True, slots=True)
class ProtectedOrdinaryReviewActionChainIdentityV1:
    """Action epoch plus the exact provider-stage request hash for one chain."""

    chain_id: str
    action_id: str
    review_id: str
    request_id: str
    request_sha256: str
    context_sha256: str
    normalized_action_sha256: str

    def __post_init__(self) -> None:
        _require_chain_id(self.chain_id)
        _require_identifier(self.action_id, "review action ID")
        if not self.action_id.startswith("review-action-") or not re_is_sha256(
            self.action_id.removeprefix("review-action-")
        ):
            raise ContractValidationError("ordinary protected custody review action ID is invalid")
        _require_identifier(self.review_id, "review action review ID")
        _require_request_id(self.request_id)
        for value, field_name in (
            (self.request_sha256, "review action provider-stage request hash"),
            (self.context_sha256, "review action chain context hash"),
            (self.normalized_action_sha256, "normalized review action hash"),
        ):
            _require_sha256(value, field_name)


@dataclass(frozen=True, slots=True)
class ProtectedOrdinaryReviewActionResponseReceiptV1:
    """Content-free receipt for one durable review-decision response."""

    action_id: str
    response_sha256: str
    response_size_bytes: int

    def __post_init__(self) -> None:
        _require_identifier(self.action_id, "review action ID")
        if not self.action_id.startswith("review-action-") or not re_is_sha256(
            self.action_id.removeprefix("review-action-")
        ):
            raise ContractValidationError("ordinary protected custody review action ID is invalid")
        _require_sha256(self.response_sha256, "review action response hash")
        if type(self.response_size_bytes) is not int or self.response_size_bytes < 2:
            raise ContractValidationError(
                "ordinary protected custody review action response size is invalid"
            )


@dataclass(frozen=True, slots=True)
class ProtectedRecorderContinuationV1:
    """Exact accepted-review target for Recorder-only continuation."""

    chain_id: str
    request_id: str
    review_id: str
    accepted_turn_id: str
    accepted_receipt_sha256: str

    def __post_init__(self) -> None:
        _require_chain_id(self.chain_id)
        _require_request_id(self.request_id)
        for value, field_name in (
            (self.review_id, "review ID"),
            (self.accepted_turn_id, "accepted turn ID"),
        ):
            if not isinstance(value, str) or not value.strip() or "\x00" in value:
                raise ContractValidationError(
                    f"ordinary Recorder continuation {field_name} is invalid"
                )
        _require_sha256(self.accepted_receipt_sha256, "accepted receipt hash")


@dataclass(frozen=True, slots=True)
class ProtectedRecorderRepairSuccessorV1:
    """One non-recursive Recorder repair occurrence bound to its exhausted parent."""

    parent_chain_id: str
    successor_chain_id: str
    request_id: str
    review_id: str
    accepted_turn_id: str
    accepted_receipt_sha256: str
    parent_chain_sha256: str
    repair_action_id: str
    repair_action_sha256: str
    successor_scope_sha256: str

    def __post_init__(self) -> None:
        _require_chain_id(self.parent_chain_id)
        _require_chain_id(self.successor_chain_id)
        if self.parent_chain_id == self.successor_chain_id:
            raise ContractValidationError("Recorder repair successor reused its parent chain")
        _require_request_id(self.request_id)
        _require_identifier(self.review_id, "Recorder repair review ID")
        _require_identifier(self.accepted_turn_id, "Recorder repair accepted turn ID")
        _require_stage_action_id(self.repair_action_id)
        for value, field_name in (
            (self.accepted_receipt_sha256, "Recorder repair accepted receipt"),
            (self.parent_chain_sha256, "Recorder repair parent chain"),
            (self.repair_action_sha256, "Recorder repair action"),
            (self.successor_scope_sha256, "Recorder repair successor scope"),
        ):
            _require_sha256(value, field_name)


class ProtectedOrdinaryStageRetryCustodyStoreV1:
    """Atomic trusted-local custody with cross-process Windows file locking."""

    SCHEMA_VERSION: ClassVar[str] = "cera.ordinary_stage_retry_protected_request.v1"
    TOMBSTONE_SCHEMA_VERSION: ClassVar[str] = (
        "cera.ordinary_stage_retry_protected_request_tombstone.v1"
    )
    RECORDER_SCHEMA_VERSION: ClassVar[str] = "cera.ordinary_stage_retry_recorder_continuation.v1"
    RECORDER_REPAIR_SCHEMA_VERSION: ClassVar[str] = (
        "cera.ordinary_stage_retry_recorder_repair_successor.v1"
    )
    CHAIN_SCHEMA_VERSION: ClassVar[str] = "cera.ordinary_stage_retry_chain_context.v1"
    OCCURRENCE_SCHEMA_VERSION: ClassVar[str] = "cera.ordinary_stage_retry_occurrence_binding.v1"
    RESPONSE_SCHEMA_VERSION: ClassVar[str] = "cera.ordinary_stage_retry_terminal_response.v1"
    LATEST_CHAIN_SCHEMA_VERSION: ClassVar[str] = "cera.ordinary_stage_retry_latest_chain.v1"
    REVIEW_SCHEMA_VERSION: ClassVar[str] = "cera.ordinary_stage_retry_review_request.v1"
    REVIEW_TOMBSTONE_SCHEMA_VERSION: ClassVar[str] = (
        "cera.ordinary_stage_retry_review_request_tombstone.v1"
    )
    REVIEW_ACTION_SCHEMA_VERSION: ClassVar[str] = "cera.ordinary_stage_retry_review_action.v1"
    REVIEW_ACTION_TOMBSTONE_SCHEMA_VERSION: ClassVar[str] = (
        "cera.ordinary_stage_retry_review_action_tombstone.v1"
    )
    REVIEW_ACTION_CHAIN_SCHEMA_VERSION: ClassVar[str] = (
        "cera.ordinary_stage_retry_review_action_chain.v1"
    )
    REVIEW_ACTION_RESPONSE_SCHEMA_VERSION: ClassVar[str] = (
        "cera.ordinary_stage_retry_review_action_response.v1"
    )

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.requests_root = self.root / "requests"
        self.chains_root = self.root / "chains"
        self.occurrences_root = self.root / "occurrences"
        self.latest_chains_root = self.root / "latest_chains"
        self.reviews_root = self.root / "reviews"
        self.review_actions_root = self.root / "review_actions"
        self.review_action_chains_root = self.root / "review_action_chains"
        self.review_action_responses_root = self.root / "review_action_responses"
        self.responses_root = self.root / "responses"
        self.recorder_root = self.root / "recorder"
        self.recorder_repairs_root = self.root / "recorder_repairs"
        self.locks_root = self.root / "locks"
        for path in (
            self.root,
            self.requests_root,
            self.chains_root,
            self.occurrences_root,
            self.latest_chains_root,
            self.reviews_root,
            self.review_actions_root,
            self.review_action_chains_root,
            self.review_action_responses_root,
            self.responses_root,
            self.recorder_root,
            self.recorder_repairs_root,
            self.locks_root,
        ):
            path.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()

    def freeze_request(
        self,
        *,
        normalized_request: Mapping[str, Any],
        binding: PiSceneRequestBindingV1,
        generation: int,
        turn_input: LeanSceneTurnInputV1,
        retrieval_snapshot: ImmutableRetrievalSnapshotIdentityV1,
        tool_result_bundle: object,
    ) -> ProtectedOrdinaryCustodyReceiptV1:
        """Freeze exact resume material before the first generic dispatch."""

        if type(binding) is not PiSceneRequestBindingV1:
            raise ContractValidationError("ordinary pending request binding changed")
        if type(retrieval_snapshot) is not ImmutableRetrievalSnapshotIdentityV1:
            raise ContractValidationError("ordinary pending retrieval snapshot changed")
        if type(generation) is not int or generation < 1:
            raise ContractValidationError("ordinary pending generation is invalid")
        if type(turn_input) is not LeanSceneTurnInputV1:
            raise ContractValidationError("ordinary pending turn input changed")
        if (turn_input.world_id, turn_input.branch_id) != (
            binding.world_id,
            binding.branch_id,
        ) or canonical_sha256(to_primitive(turn_input.request_controls)) != binding.controls_sha256:
            raise StateConflictError("ordinary pending turn changed HTTP custody")
        normalized = to_primitive(normalized_request)
        if not isinstance(normalized, dict):
            raise ContractValidationError("ordinary pending request must be an object")
        request_bytes = canonical_bytes(normalized)
        if (
            len(request_bytes) != binding.normalized_request_size_bytes
            or canonical_sha256(normalized) != binding.normalized_request_sha256
        ):
            raise StateConflictError("ordinary pending request differs from HTTP binding")
        context_payload = {
            "schema_version": "cera.ordinary_stage_retry_request_context.v1",
            "request_binding_sha256": canonical_sha256(binding.to_payload()),
            "generation": generation,
            "turn_context": to_primitive(turn_input),
            "turn_context_sha256": canonical_sha256(turn_input),
            "retrieval_snapshot": retrieval_snapshot.to_payload(),
            "tool_result_bundle": to_primitive(tool_result_bundle),
        }
        body = {
            "schema_version": self.SCHEMA_VERSION,
            "binding": binding.to_payload(),
            "normalized_request": normalized,
            "context": context_payload,
            "context_sha256": canonical_sha256(context_payload),
        }
        payload = {**body, "custody_sha256": canonical_sha256(body)}
        path = self._request_path(binding.request_id)
        branch_claim = self._branch_claim_identity(binding.world_id, binding.branch_id)
        with self._claim(branch_claim):
            barrier = self._branch_barrier_unlocked(
                world_id=binding.world_id,
                branch_id=binding.branch_id,
            )
            if barrier is not None and barrier.request_id != binding.request_id:
                raise StateConflictError(
                    "ordinary branch already has an unresolved request barrier"
                )
            with self._claim(binding.request_id):
                if path.exists():
                    current = self._read_json(path)
                    if current.get("schema_version") == self.TOMBSTONE_SCHEMA_VERSION:
                        raise StateConflictError("ordinary pending request was already redacted")
                    if current != payload:
                        raise StateConflictError("ordinary pending request custody changed")
                else:
                    self._atomic_write(path, payload)
                verified = self.load_request(binding.request_id)
        return ProtectedOrdinaryCustodyReceiptV1(
            request_id=binding.request_id,
            request_binding_sha256=canonical_sha256(binding.to_payload()),
            normalized_request_sha256=binding.normalized_request_sha256,
            context_sha256=verified.context_sha256,
            disposition="active",
            branch_barrier_active=True,
        )

    def load_request(self, request_id: str) -> ProtectedOrdinaryPendingRequestV1:
        _require_request_id(request_id)
        payload = self._read_json(self._request_path(request_id))
        if payload.get("schema_version") == self.TOMBSTONE_SCHEMA_VERSION:
            raise StateConflictError("ordinary pending request has been redacted")
        expected = {
            "schema_version",
            "binding",
            "normalized_request",
            "context",
            "context_sha256",
            "custody_sha256",
        }
        if set(payload) != expected or payload["schema_version"] != self.SCHEMA_VERSION:
            raise StateConflictError("ordinary pending request custody shape changed")
        unsigned = {key: value for key, value in payload.items() if key != "custody_sha256"}
        if canonical_sha256(unsigned) != payload["custody_sha256"]:
            raise StateConflictError("ordinary pending request custody hash changed")
        binding = binding_from_payload(payload["binding"])
        if binding.request_id != request_id:
            raise StateConflictError("ordinary pending request identity changed")
        context = payload["context"]
        if not isinstance(context, dict) or set(context) != {
            "schema_version",
            "request_binding_sha256",
            "generation",
            "turn_context",
            "turn_context_sha256",
            "retrieval_snapshot",
            "tool_result_bundle",
        }:
            raise StateConflictError("ordinary pending request context shape changed")
        retrieval = context["retrieval_snapshot"]
        if not isinstance(retrieval, dict):
            raise StateConflictError("ordinary pending retrieval snapshot changed")
        snapshot = from_mapping(ImmutableRetrievalSnapshotIdentityV1, retrieval)
        assert isinstance(snapshot, ImmutableRetrievalSnapshotIdentityV1)
        normalized_request = payload["normalized_request"]
        if not isinstance(normalized_request, dict):
            raise StateConflictError("ordinary pending normalized request changed shape")
        turn_payload = context["turn_context"]
        if (
            not isinstance(turn_payload, dict)
            or canonical_sha256(turn_payload) != context["turn_context_sha256"]
        ):
            raise StateConflictError("ordinary pending turn context changed")
        turn_input = from_mapping(LeanSceneTurnInputV1, turn_payload)
        assert isinstance(turn_input, LeanSceneTurnInputV1)
        return ProtectedOrdinaryPendingRequestV1(
            binding=binding,
            normalized_request=normalized_request,
            generation=context["generation"],
            turn_input=turn_input,
            retrieval_snapshot=snapshot,
            tool_result_bundle=context["tool_result_bundle"],
            context_sha256=payload["context_sha256"],
        )

    def load_for_scope(
        self,
        scope: ProviderStageRetryOccurrenceScopeV1,
    ) -> ProtectedOrdinaryPendingRequestV1:
        if type(scope) is not ProviderStageRetryOccurrenceScopeV1:
            raise ContractValidationError("ordinary pending scope changed")
        pending = self.load_request(scope.request_id)
        if (
            pending.binding.world_id != scope.world_id
            or pending.binding.branch_id != scope.branch_id
            or f"generation-{pending.generation:08d}" != scope.generation_id
        ):
            raise StateConflictError("ordinary pending request differs from durable scope")
        return pending

    def bind_chain(
        self,
        *,
        chain_id: str,
        request_id: str,
        context_sha256: str,
    ) -> ProtectedOrdinaryChainContextV1:
        """Bind a public chain identity to protected request context pre-dispatch."""

        _require_chain_id(chain_id)
        _require_request_id(request_id)
        _require_sha256(context_sha256, "chain context hash")
        pending = self.load_request(request_id)
        if pending.context_sha256 != context_sha256:
            raise StateConflictError("ordinary chain changed protected request context")
        body = {
            "schema_version": self.CHAIN_SCHEMA_VERSION,
            "chain_id": chain_id,
            "request_id": request_id,
            "context_sha256": context_sha256,
        }
        payload = {**body, "chain_context_sha256": canonical_sha256(body)}
        path = self._chain_path(chain_id)
        with self._claim(chain_id):
            if path.exists() and self._read_json(path) != payload:
                raise StateConflictError("ordinary chain-to-request context changed")
            if not path.exists():
                self._atomic_write(path, payload)
            return self.chain_context(chain_id)

    def bind_occurrence(
        self,
        *,
        scope: ProviderStageRetryOccurrenceScopeV1,
        context_sha256: str,
    ) -> None:
        """Freeze one request/generation/stage ordinal to one exact authority."""

        if type(scope) is not ProviderStageRetryOccurrenceScopeV1:
            raise ContractValidationError("ordinary occurrence scope changed")
        _require_sha256(context_sha256, "occurrence context hash")
        pending = self.load_for_scope(scope)
        if pending.context_sha256 != context_sha256:
            raise StateConflictError("ordinary occurrence changed protected context")
        identity = {
            "request_id": scope.request_id,
            "generation_id": scope.generation_id,
            "stage": scope.stage.value,
            "stage_ordinal": scope.stage_ordinal,
        }
        occurrence_key = domain_sha256(
            "cera.ordinary_stage_retry_occurrence_key.v1",
            identity,
        )
        body = {
            "schema_version": self.OCCURRENCE_SCHEMA_VERSION,
            "occurrence_key": occurrence_key,
            "identity": identity,
            "chain_id": scope.identity.chain_id,
            "scope_sha256": canonical_sha256(scope.to_payload()),
            "context_sha256": context_sha256,
        }
        payload = {**body, "occurrence_binding_sha256": canonical_sha256(body)}
        path = self._occurrence_path(occurrence_key)
        with self._claim(f"occurrence:{occurrence_key}"):
            if path.exists() and self._read_json(path) != payload:
                raise StateConflictError(
                    "ordinary stage occurrence changed accepted head, input, or authority"
                )
            if not path.exists():
                self._atomic_write(path, payload)

    def advance_latest_chain(
        self,
        *,
        scope: ProviderStageRetryOccurrenceScopeV1,
        context_sha256: str,
        semantic_action_boundary: bool = False,
        recording_repair_boundary: bool = False,
    ) -> ProtectedOrdinaryChainRequestIdentityV1:
        """Advance a request cursor once, never roll it back during cache replay."""

        if type(scope) is not ProviderStageRetryOccurrenceScopeV1:
            raise ContractValidationError("ordinary latest-chain scope changed")
        if type(semantic_action_boundary) is not bool:
            raise ContractValidationError("ordinary latest-chain semantic action boundary changed")
        if type(recording_repair_boundary) is not bool or (
            semantic_action_boundary and recording_repair_boundary
        ):
            raise ContractValidationError("ordinary latest-chain recording repair boundary changed")
        _require_sha256(context_sha256, "latest-chain context hash")
        pending = self.load_for_scope(scope)
        if pending.context_sha256 != context_sha256:
            raise StateConflictError("ordinary latest chain changed protected context")
        entry = {
            "chain_id": scope.identity.chain_id,
            "request_sha256": scope.request_sha256,
            "request_occurrence_sha256": scope.request_occurrence_sha256,
            "stage": scope.stage.value,
            "stage_ordinal": scope.stage_ordinal,
            "semantic_action_boundary": semantic_action_boundary,
            "recording_repair_boundary": recording_repair_boundary,
        }
        path = self._latest_chain_path(scope.request_id)
        with self._claim(f"latest-chain:{scope.request_id}"):
            entries: list[dict[str, Any]] = []
            if path.exists():
                payload = self._read_json(path)
                entries = self._verify_latest_chain_payload(
                    payload,
                    request_id=scope.request_id,
                    context_sha256=context_sha256,
                )
            matching = tuple(
                value for value in entries if value.get("chain_id") == scope.identity.chain_id
            )
            if matching:
                if len(matching) != 1 or matching[0] != entry:
                    raise StateConflictError("ordinary latest-chain entry changed")
            else:
                if entries:
                    self._require_pipeline_successor(entries[-1], entry)
                self._require_stage_ordinal(entries, entry)
                entries.append(entry)
                body = {
                    "schema_version": self.LATEST_CHAIN_SCHEMA_VERSION,
                    "request_id": scope.request_id,
                    "context_sha256": context_sha256,
                    "entries": entries,
                    "latest_chain_id": scope.identity.chain_id,
                }
                self._atomic_write(
                    path,
                    {**body, "latest_chain_sha256": canonical_sha256(body)},
                )
            latest = entries[-1]
            return ProtectedOrdinaryChainRequestIdentityV1(
                chain_id=latest["chain_id"],
                request_id=scope.request_id,
                request_sha256=latest["request_sha256"],
                context_sha256=context_sha256,
            )

    def latest_chain_for_chain(
        self,
        source_chain_id: str,
    ) -> ProtectedOrdinaryChainRequestIdentityV1:
        source = self.chain_context(source_chain_id)
        payload = self._read_json(self._latest_chain_path(source.request_id))
        entries = self._verify_latest_chain_payload(
            payload,
            request_id=source.request_id,
            context_sha256=source.context_sha256,
        )
        if not any(entry["chain_id"] == source_chain_id for entry in entries):
            raise StateConflictError("ordinary source chain is absent from request cursor")
        latest = entries[-1]
        return ProtectedOrdinaryChainRequestIdentityV1(
            chain_id=latest["chain_id"],
            request_id=source.request_id,
            request_sha256=latest["request_sha256"],
            context_sha256=source.context_sha256,
        )

    def recorder_chain_ids_for_request(self, request_id: str) -> tuple[str, ...]:
        """Return ordered Recorder occurrences from one authenticated request cursor."""

        _require_request_id(request_id)
        pending = self.load_request(request_id)
        path = self._latest_chain_path(request_id)
        if not path.exists():
            return ()
        entries = self._verify_latest_chain_payload(
            self._read_json(path),
            request_id=request_id,
            context_sha256=pending.context_sha256,
        )
        return tuple(
            entry["chain_id"] for entry in entries if entry["stage"] == ProviderStage.RECORDER.value
        )

    def chain_request_identity(
        self,
        chain_id: str,
    ) -> ProtectedOrdinaryChainRequestIdentityV1:
        chain = self.chain_context(chain_id)
        payload = self._read_json(self._latest_chain_path(chain.request_id))
        entries = self._verify_latest_chain_payload(
            payload,
            request_id=chain.request_id,
            context_sha256=chain.context_sha256,
        )
        matches = [entry for entry in entries if entry["chain_id"] == chain_id]
        if len(matches) != 1:
            raise StateConflictError("ordinary chain request identity is unavailable")
        return ProtectedOrdinaryChainRequestIdentityV1(
            chain_id=chain_id,
            request_id=chain.request_id,
            request_sha256=matches[0]["request_sha256"],
            context_sha256=chain.context_sha256,
        )

    def chain_context(self, chain_id: str) -> ProtectedOrdinaryChainContextV1:
        _require_chain_id(chain_id)
        payload = self._read_json(self._chain_path(chain_id))
        expected = {
            "schema_version",
            "chain_id",
            "request_id",
            "context_sha256",
            "chain_context_sha256",
        }
        if set(payload) != expected or payload["schema_version"] != (self.CHAIN_SCHEMA_VERSION):
            raise StateConflictError("ordinary chain context shape changed")
        unsigned = {key: value for key, value in payload.items() if key != "chain_context_sha256"}
        if canonical_sha256(unsigned) != payload["chain_context_sha256"]:
            raise StateConflictError("ordinary chain context hash changed")
        if payload["chain_id"] != chain_id:
            raise StateConflictError("ordinary chain context identity changed")
        return ProtectedOrdinaryChainContextV1(
            chain_id=payload["chain_id"],
            request_id=payload["request_id"],
            context_sha256=payload["context_sha256"],
        )

    def load_request_for_chain(self, chain_id: str) -> ProtectedOrdinaryPendingRequestV1:
        chain = self.chain_context(chain_id)
        pending = self.load_request(chain.request_id)
        if pending.context_sha256 != chain.context_sha256:
            raise StateConflictError("ordinary chain changed pending request context")
        return pending

    def bind_review_request(
        self,
        *,
        review: LeanReviewRecordV1,
        request_id: str,
        context_sha256: str,
        stage_occurrence_counts: Mapping[str, int],
    ) -> ProtectedOrdinaryReviewRequestIdentityV1:
        """Immutably bind a review to the exact still-pending request."""

        if type(review) is not LeanReviewRecordV1:
            raise ContractValidationError("ordinary protected review contract changed")
        _require_request_id(request_id)
        _require_sha256(context_sha256, "review context hash")
        pending = self.load_request(request_id)
        counts = _normalize_stage_occurrence_counts(stage_occurrence_counts)
        if pending.context_sha256 != context_sha256:
            raise StateConflictError("ordinary review changed protected request context")
        candidate = review.candidate
        turn = review.turn_input
        if (
            pending.binding.route is not SceneRoute.ORDINARY
            or candidate.route is not SceneRoute.ORDINARY
            or (candidate.world_id, candidate.branch_id)
            != (pending.binding.world_id, pending.binding.branch_id)
            or candidate.generation != pending.generation
            or (turn.world_id, turn.branch_id, turn.scene_id, turn.exact_user_source)
            != (
                candidate.world_id,
                candidate.branch_id,
                candidate.scene_id,
                candidate.exact_user_source,
            )
            or canonical_sha256(turn) != canonical_sha256(pending.turn_input)
        ):
            raise StateConflictError("ordinary review changed original request custody")
        if counts != self.stage_occurrence_counts(request_id):
            raise StateConflictError("ordinary review stage occurrence counts changed")
        counts_sha256 = canonical_sha256(counts)
        identity = ProtectedOrdinaryReviewRequestIdentityV1(
            review_id=review.review_id,
            request_id=request_id,
            world_id=candidate.world_id,
            branch_id=candidate.branch_id,
            generation=candidate.generation,
            context_sha256=context_sha256,
            turn_context_sha256=canonical_sha256(turn),
            candidate_id=candidate.candidate_id,
            candidate_sha256=candidate.candidate_sha256,
            stage_occurrence_counts_sha256=counts_sha256,
        )
        body = {
            "schema_version": self.REVIEW_SCHEMA_VERSION,
            **to_primitive(identity),
            "stage_occurrence_counts": counts,
        }
        payload = {**body, "review_request_sha256": canonical_sha256(body)}
        path = self._review_path(review.review_id)
        with self._claim(f"review:{review.review_id}"):
            if path.exists():
                prior = self._read_json(path)
                if prior.get("schema_version") == self.REVIEW_TOMBSTONE_SCHEMA_VERSION:
                    raise StateConflictError("ordinary review request mapping is retired")
                loaded, loaded_counts = self.load_review_request(review.review_id)
                if loaded != identity or loaded_counts != counts:
                    raise StateConflictError("ordinary review request mapping changed")
                return loaded
            else:
                self._atomic_write(path, payload)
            loaded, loaded_counts = self.load_review_request(review.review_id)
            if loaded != identity or loaded_counts != counts:
                raise StateConflictError("ordinary review request mapping readback changed")
            return loaded

    def load_review_request(
        self,
        review_id: str,
    ) -> tuple[ProtectedOrdinaryReviewRequestIdentityV1, dict[str, int]]:
        """Load an active review mapping; absence and retirement are distinct."""

        _require_identifier(review_id, "review ID")
        path = self._review_path(review_id)
        if not path.exists():
            raise FileNotFoundError(path)
        payload = self._read_json(path)
        if payload.get("schema_version") == self.REVIEW_TOMBSTONE_SCHEMA_VERSION:
            self._verify_review_tombstone(payload, review_id=review_id)
            raise StateConflictError("ordinary review request mapping is retired")
        expected = {
            "schema_version",
            "review_id",
            "request_id",
            "world_id",
            "branch_id",
            "generation",
            "context_sha256",
            "turn_context_sha256",
            "candidate_id",
            "candidate_sha256",
            "stage_occurrence_counts_sha256",
            "stage_occurrence_counts",
            "review_request_sha256",
        }
        if set(payload) != expected or payload.get("schema_version") != self.REVIEW_SCHEMA_VERSION:
            raise StateConflictError("ordinary review request mapping shape changed")
        unsigned = {key: value for key, value in payload.items() if key != "review_request_sha256"}
        if canonical_sha256(unsigned) != payload.get("review_request_sha256"):
            raise StateConflictError("ordinary review request mapping hash changed")
        counts_value = payload["stage_occurrence_counts"]
        if not isinstance(counts_value, dict):
            raise StateConflictError("ordinary review stage occurrence counts changed")
        if canonical_sha256(counts_value) != payload["stage_occurrence_counts_sha256"]:
            raise StateConflictError("ordinary review stage occurrence counts hash changed")
        try:
            counts = _normalize_stage_occurrence_counts(counts_value)
        except ContractValidationError as exc:
            raise StateConflictError("ordinary review stage occurrence counts changed") from exc
        identity = ProtectedOrdinaryReviewRequestIdentityV1(
            review_id=payload["review_id"],
            request_id=payload["request_id"],
            world_id=payload["world_id"],
            branch_id=payload["branch_id"],
            generation=payload["generation"],
            context_sha256=payload["context_sha256"],
            turn_context_sha256=payload["turn_context_sha256"],
            candidate_id=payload["candidate_id"],
            candidate_sha256=payload["candidate_sha256"],
            stage_occurrence_counts_sha256=canonical_sha256(counts),
        )
        if identity.review_id != review_id:
            raise StateConflictError("ordinary review request identity changed")
        return identity, counts

    def bind_review_action(
        self,
        *,
        review_id: str,
        normalized_action: Mapping[str, Any],
    ) -> tuple[
        ProtectedOrdinaryReviewActionIdentityV1,
        dict[str, Any],
        dict[str, int],
    ]:
        """Write one exact creator action before any of its provider stages."""

        review, counts = self.load_review_request(review_id)
        normalized = to_primitive(normalized_action)
        if not isinstance(normalized, dict) or not normalized:
            raise ContractValidationError(
                "ordinary protected review action must be a nonempty object"
            )
        # Reject values that cannot round-trip as one exact canonical action.
        canonical_bytes(normalized)
        normalized_sha256 = canonical_sha256(normalized)
        counts_sha256 = canonical_sha256(counts)
        action_id = "review-action-" + domain_sha256(
            "cera.ordinary_stage_retry_review_action_identity.v1",
            {
                "review_id": review.review_id,
                "request_id": review.request_id,
                "context_sha256": review.context_sha256,
                "normalized_action_sha256": normalized_sha256,
                "stage_occurrence_counts_sha256": counts_sha256,
            },
        )
        identity = ProtectedOrdinaryReviewActionIdentityV1(
            action_id=action_id,
            review_id=review.review_id,
            request_id=review.request_id,
            world_id=review.world_id,
            branch_id=review.branch_id,
            context_sha256=review.context_sha256,
            normalized_action_sha256=normalized_sha256,
            stage_occurrence_counts_sha256=counts_sha256,
        )
        body = {
            "schema_version": self.REVIEW_ACTION_SCHEMA_VERSION,
            **to_primitive(identity),
            "normalized_action": normalized,
            "stage_occurrence_counts": counts,
        }
        payload = {**body, "review_action_sha256": canonical_sha256(body)}
        path = self._review_action_path(action_id)
        with self._claim(f"review-action:{review_id}"):
            active = self._active_review_action_for_review(review_id)
            if active is not None:
                loaded = self.load_review_action(active.action_id)
                loaded_identity, loaded_action, loaded_counts = loaded
                if (
                    loaded_action != normalized
                    or loaded_counts != counts
                    or loaded_identity.review_id != identity.review_id
                    or loaded_identity.request_id != identity.request_id
                    or loaded_identity.world_id != identity.world_id
                    or loaded_identity.branch_id != identity.branch_id
                    or loaded_identity.context_sha256 != identity.context_sha256
                    or loaded_identity.normalized_action_sha256 != identity.normalized_action_sha256
                ):
                    raise StateConflictError(
                        "ordinary review already has a different protected action"
                    )
                return loaded
            if path.exists():
                prior = self._read_json(path)
                if prior.get("schema_version") == self.REVIEW_ACTION_TOMBSTONE_SCHEMA_VERSION:
                    self._verify_review_action_tombstone(prior, action_id=action_id)
                    raise StateConflictError("ordinary protected review action is retired")
                if prior != payload:
                    raise StateConflictError("ordinary protected review action changed")
            else:
                self._atomic_write(path, payload)
            loaded = self.load_review_action(action_id)
            if loaded != (identity, normalized, counts):
                raise StateConflictError("ordinary protected review action readback changed")
            return loaded

    def load_review_action(
        self,
        action_id: str,
    ) -> tuple[
        ProtectedOrdinaryReviewActionIdentityV1,
        dict[str, Any],
        dict[str, int],
    ]:
        """Load exact active action bytes and their original occurrence base."""

        path = self._review_action_path(action_id)
        if not path.exists():
            raise FileNotFoundError(path)
        payload = self._read_json(path)
        if payload.get("schema_version") == self.REVIEW_ACTION_TOMBSTONE_SCHEMA_VERSION:
            self._verify_review_action_tombstone(payload, action_id=action_id)
            raise StateConflictError("ordinary protected review action is retired")
        expected = {
            "schema_version",
            "action_id",
            "review_id",
            "request_id",
            "world_id",
            "branch_id",
            "context_sha256",
            "normalized_action_sha256",
            "stage_occurrence_counts_sha256",
            "normalized_action",
            "stage_occurrence_counts",
            "review_action_sha256",
        }
        unsigned = {key: value for key, value in payload.items() if key != "review_action_sha256"}
        if (
            set(payload) != expected
            or payload.get("schema_version") != self.REVIEW_ACTION_SCHEMA_VERSION
            or canonical_sha256(unsigned) != payload.get("review_action_sha256")
        ):
            raise StateConflictError("ordinary protected review action shape changed")
        action = payload["normalized_action"]
        counts_value = payload["stage_occurrence_counts"]
        if not isinstance(action, dict) or not isinstance(counts_value, dict):
            raise StateConflictError("ordinary protected review action content changed")
        if canonical_sha256(counts_value) != payload["stage_occurrence_counts_sha256"]:
            raise StateConflictError("ordinary protected review action binding changed")
        try:
            counts = _normalize_stage_occurrence_counts(counts_value)
            identity = ProtectedOrdinaryReviewActionIdentityV1(
                action_id=payload["action_id"],
                review_id=payload["review_id"],
                request_id=payload["request_id"],
                world_id=payload["world_id"],
                branch_id=payload["branch_id"],
                context_sha256=payload["context_sha256"],
                normalized_action_sha256=payload["normalized_action_sha256"],
                stage_occurrence_counts_sha256=canonical_sha256(counts),
            )
        except (ContractValidationError, KeyError, TypeError) as exc:
            raise StateConflictError("ordinary protected review action changed") from exc
        if (
            identity.action_id != action_id
            or canonical_sha256(action) != identity.normalized_action_sha256
            or canonical_sha256(counts) != identity.stage_occurrence_counts_sha256
        ):
            raise StateConflictError("ordinary protected review action binding changed")
        review, review_counts = self._review_request_identity_any(identity.review_id)
        if (
            identity.request_id != review.request_id
            or identity.world_id != review.world_id
            or identity.branch_id != review.branch_id
            or identity.context_sha256 != review.context_sha256
            or counts != review_counts
        ):
            raise StateConflictError("ordinary protected review action lost Request A")
        return identity, dict(action), counts

    def bind_review_action_chain(
        self,
        *,
        chain_id: str,
        action_id: str,
        request_id: str,
        request_sha256: str,
        context_sha256: str,
    ) -> ProtectedOrdinaryReviewActionIdentityV1:
        """Bind every action-produced stage chain before its provider dispatch."""

        _require_chain_id(chain_id)
        _require_request_id(request_id)
        _require_sha256(request_sha256, "review action provider-stage request hash")
        _require_sha256(context_sha256, "review action chain context hash")
        identity, _ = self._review_action_identity_any(action_id)
        if identity.request_id != request_id or identity.context_sha256 != context_sha256:
            raise StateConflictError("ordinary review action chain changed Request A")
        chain = self.chain_context(chain_id)
        if chain.request_id != request_id or chain.context_sha256 != context_sha256:
            raise StateConflictError("ordinary review action chain changed request context")
        body = {
            "schema_version": self.REVIEW_ACTION_CHAIN_SCHEMA_VERSION,
            "chain_id": chain_id,
            "request_sha256": request_sha256,
            **to_primitive(identity),
        }
        payload = {**body, "review_action_chain_sha256": canonical_sha256(body)}
        path = self._review_action_chain_path(chain_id)
        with self._claim(f"review-action-chain:{chain_id}"):
            if path.exists():
                if self._read_json(path) != payload:
                    raise StateConflictError("ordinary review action chain mapping changed")
            else:
                self._atomic_write(path, payload)
        loaded = self.review_action_for_chain(chain_id)
        if loaded != identity:
            raise StateConflictError("ordinary review action chain readback changed")
        return identity

    def review_action_for_chain(
        self,
        chain_id: str,
    ) -> ProtectedOrdinaryReviewActionIdentityV1 | None:
        """Return content-free action identity, or None for an initial chat chain."""

        chain_identity = self.review_action_chain_identity(chain_id)
        if chain_identity is None:
            return None
        return self._review_action_identity_from_chain(chain_identity)

    def review_action_chain_identity(
        self,
        chain_id: str,
    ) -> ProtectedOrdinaryReviewActionChainIdentityV1 | None:
        """Return the action epoch bound to one exact provider-stage request."""

        path = self._review_action_chain_path(chain_id)
        if not path.exists():
            return None
        payload = self._read_json(path)
        expected = {
            "schema_version",
            "chain_id",
            "request_sha256",
            "action_id",
            "review_id",
            "request_id",
            "world_id",
            "branch_id",
            "context_sha256",
            "normalized_action_sha256",
            "stage_occurrence_counts_sha256",
            "review_action_chain_sha256",
        }
        unsigned = {
            key: value for key, value in payload.items() if key != "review_action_chain_sha256"
        }
        if (
            set(payload) != expected
            or payload.get("schema_version") != self.REVIEW_ACTION_CHAIN_SCHEMA_VERSION
            or payload.get("chain_id") != chain_id
            or canonical_sha256(unsigned) != payload.get("review_action_chain_sha256")
        ):
            raise StateConflictError("ordinary review action chain mapping changed")
        try:
            chain_identity = ProtectedOrdinaryReviewActionChainIdentityV1(
                chain_id=payload["chain_id"],
                action_id=payload["action_id"],
                review_id=payload["review_id"],
                request_id=payload["request_id"],
                request_sha256=payload["request_sha256"],
                context_sha256=payload["context_sha256"],
                normalized_action_sha256=payload["normalized_action_sha256"],
            )
        except (ContractValidationError, KeyError, TypeError) as exc:
            raise StateConflictError("ordinary review action chain identity changed") from exc
        chain = self.chain_context(chain_id)
        if (
            chain.request_id != chain_identity.request_id
            or chain.context_sha256 != chain_identity.context_sha256
        ):
            raise StateConflictError("ordinary review action chain lost Request A")
        self._review_action_identity_from_chain(chain_identity)
        return chain_identity

    def _review_action_identity_from_chain(
        self,
        chain_identity: ProtectedOrdinaryReviewActionChainIdentityV1,
    ) -> ProtectedOrdinaryReviewActionIdentityV1:
        identity, _ = self._review_action_identity_any(chain_identity.action_id)
        if (
            identity.review_id != chain_identity.review_id
            or identity.request_id != chain_identity.request_id
            or identity.context_sha256 != chain_identity.context_sha256
            or identity.normalized_action_sha256 != chain_identity.normalized_action_sha256
        ):
            raise StateConflictError("ordinary review action chain changed action identity")
        return identity

    def bind_review_action_response(
        self,
        *,
        action_id: str,
        response: Mapping[str, Any],
    ) -> ProtectedOrdinaryReviewActionResponseReceiptV1:
        """Durably bind the exact safe review-decision projection once."""

        identity, _, _ = self.load_review_action(action_id)
        projected = to_primitive(response)
        if not isinstance(projected, dict) or not projected:
            raise ContractValidationError(
                "ordinary protected review action response must be an object"
            )
        exact = canonical_bytes(projected)
        receipt = ProtectedOrdinaryReviewActionResponseReceiptV1(
            action_id=identity.action_id,
            response_sha256=canonical_sha256(projected),
            response_size_bytes=len(exact),
        )
        body = {
            "schema_version": self.REVIEW_ACTION_RESPONSE_SCHEMA_VERSION,
            **to_primitive(receipt),
            "response": projected,
        }
        payload = {
            **body,
            "review_action_response_sha256": canonical_sha256(body),
        }
        path = self._review_action_response_path(action_id)
        with self._claim(f"review-action-response:{action_id}"):
            if path.exists():
                if self._read_json(path) != payload:
                    raise StateConflictError("ordinary protected review action response changed")
            else:
                self._atomic_write(path, payload)
        loaded, loaded_receipt = self.load_review_action_response(action_id)
        if loaded != projected or loaded_receipt != receipt:
            raise StateConflictError("ordinary protected review action response readback changed")
        return loaded_receipt

    def load_review_action_response(
        self,
        action_id: str,
    ) -> tuple[dict[str, Any], ProtectedOrdinaryReviewActionResponseReceiptV1]:
        path = self._review_action_response_path(action_id)
        payload = self._read_json(path)
        expected = {
            "schema_version",
            "action_id",
            "response_sha256",
            "response_size_bytes",
            "response",
            "review_action_response_sha256",
        }
        unsigned = {
            key: value for key, value in payload.items() if key != "review_action_response_sha256"
        }
        response = payload.get("response")
        if (
            set(payload) != expected
            or payload.get("schema_version") != self.REVIEW_ACTION_RESPONSE_SCHEMA_VERSION
            or canonical_sha256(unsigned) != payload.get("review_action_response_sha256")
            or not isinstance(response, dict)
        ):
            raise StateConflictError("ordinary protected review action response shape changed")
        try:
            receipt = ProtectedOrdinaryReviewActionResponseReceiptV1(
                action_id=payload["action_id"],
                response_sha256=payload["response_sha256"],
                response_size_bytes=payload["response_size_bytes"],
            )
        except (ContractValidationError, KeyError, TypeError) as exc:
            raise StateConflictError("ordinary protected review action response changed") from exc
        if (
            receipt.action_id != action_id
            or canonical_sha256(response) != receipt.response_sha256
            or len(canonical_bytes(response)) != receipt.response_size_bytes
        ):
            raise StateConflictError("ordinary protected review action response binding changed")
        return dict(response), receipt

    def load_review_action_response_optional(
        self,
        action_id: str,
    ) -> tuple[dict[str, Any], ProtectedOrdinaryReviewActionResponseReceiptV1] | None:
        path = self._review_action_response_path(action_id)
        if not path.exists():
            return None
        return self.load_review_action_response(action_id)

    def load_review_action_response_for_chain(
        self,
        chain_id: str,
    ) -> tuple[dict[str, Any], ProtectedOrdinaryReviewActionResponseReceiptV1]:
        identity = self.review_action_for_chain(chain_id)
        if identity is None:
            raise FileNotFoundError(self._review_action_chain_path(chain_id))
        return self.load_review_action_response(identity.action_id)

    def reconcile_finalized_review_action_response_for_review_optional(
        self,
        review_id: str,
    ) -> tuple[dict[str, Any], ProtectedOrdinaryReviewActionResponseReceiptV1] | None:
        """Return one exact finalized action response, or no action at all.

        Absence is the legacy/no-action fallback. Once an action identity exists,
        missing response or finalization evidence is a custody conflict rather
        than permission to reconstruct a response from mutable review state.
        """

        _require_identifier(review_id, "review ID")
        matches: list[tuple[ProtectedOrdinaryReviewActionIdentityV1, dict[str, Any]]] = []
        for path in self.review_actions_root.glob("*.json"):
            action_id = f"review-action-{path.stem}"
            payload = self._read_json(path)
            identity, _ = self._review_action_identity_any(action_id)
            if identity.review_id == review_id:
                matches.append((identity, payload))
        if not matches:
            return None
        if len(matches) != 1:
            raise StateConflictError("ordinary review has ambiguous protected actions")

        identity, action_payload = matches[0]
        if action_payload.get("schema_version") != self.REVIEW_ACTION_TOMBSTONE_SCHEMA_VERSION:
            pending_response = self.load_review_action_response_optional(identity.action_id)
            if pending_response is None:
                raise StateConflictError("ordinary protected review action response is unavailable")
            self.finalize_review_action(identity.action_id)
            action_payload = self._read_json(self._review_action_path(identity.action_id))

        finalized = self._verify_review_action_tombstone(
            action_payload,
            action_id=identity.action_id,
        )
        try:
            response, receipt = self.load_review_action_response(identity.action_id)
        except (FileNotFoundError, StateConflictError) as exc:
            raise StateConflictError(
                "ordinary protected review action response is unavailable"
            ) from exc
        review_payload = self._read_json(self._review_path(review_id))
        if review_payload.get("schema_version") != self.REVIEW_TOMBSTONE_SCHEMA_VERSION:
            raise StateConflictError("ordinary review action response is not finalized")
        review_identity, _ = self._verify_review_tombstone(
            review_payload,
            review_id=review_id,
        )
        if (
            finalized != identity
            or review_identity.review_id != identity.review_id
            or review_identity.request_id != identity.request_id
            or action_payload.get("terminal_evidence_sha256") != receipt.response_sha256
            or review_payload.get("terminal_evidence_sha256") != receipt.response_sha256
        ):
            raise StateConflictError(
                "ordinary protected review action response lost finalization custody"
            )
        return response, receipt

    def retire_review_action(
        self,
        action_id: str,
        *,
        terminal_evidence_sha256: str,
    ) -> ProtectedOrdinaryReviewActionIdentityV1:
        """Redact exact action bytes only after final decision evidence exists."""

        _require_sha256(terminal_evidence_sha256, "review action terminal evidence")
        path = self._review_action_path(action_id)
        with self._claim(f"review-action-id:{action_id}"):
            payload = self._read_json(path)
            if payload.get("schema_version") == self.REVIEW_ACTION_TOMBSTONE_SCHEMA_VERSION:
                identity = self._verify_review_action_tombstone(
                    payload,
                    action_id=action_id,
                )
                if payload["terminal_evidence_sha256"] != terminal_evidence_sha256:
                    raise StateConflictError(
                        "ordinary protected review action retirement evidence changed"
                    )
                return identity
            identity, _, counts = self.load_review_action(action_id)
            _, response_receipt = self.load_review_action_response(action_id)
            if response_receipt.response_sha256 != terminal_evidence_sha256:
                raise StateConflictError(
                    "ordinary protected review action terminal response changed"
                )
            body = {
                "schema_version": self.REVIEW_ACTION_TOMBSTONE_SCHEMA_VERSION,
                **to_primitive(identity),
                "stage_occurrence_counts": counts,
                "terminal_evidence_sha256": terminal_evidence_sha256,
            }
            tombstone = {
                **body,
                "review_action_tombstone_sha256": canonical_sha256(body),
            }
            self._atomic_write(path, tombstone)
            verified = self._verify_review_action_tombstone(
                self._read_json(path),
                action_id=action_id,
            )
            if verified != identity:
                raise StateConflictError(
                    "ordinary protected review action retirement changed identity"
                )
            return verified

    def finalize_review_action(
        self,
        action_id: str,
    ) -> tuple[
        ProtectedOrdinaryReviewActionIdentityV1,
        ProtectedOrdinaryCustodyReceiptV1,
    ]:
        """Provider-free crash reconciliation after a decision response is bound."""

        _, response_receipt = self.load_review_action_response(action_id)
        identity, _ = self._review_action_identity_any(action_id)
        request_receipt = self.retire_review_request(
            identity.review_id,
            terminal_evidence_sha256=response_receipt.response_sha256,
        )
        retired = self.retire_review_action(
            action_id,
            terminal_evidence_sha256=response_receipt.response_sha256,
        )
        if retired != identity or request_receipt.request_id != identity.request_id:
            raise StateConflictError(
                "ordinary protected review action finalization changed Request A"
            )
        return retired, request_receipt

    def stage_occurrence_counts(self, request_id: str) -> dict[str, int]:
        """Return durable per-stage occurrence maxima for one request."""

        pending = self.load_request(request_id)
        cursor_payload = self._read_json(self._latest_chain_path(request_id))
        cursor_entries = self._verify_latest_chain_payload(
            cursor_payload,
            request_id=request_id,
            context_sha256=pending.context_sha256,
        )
        expected_generation_id = f"generation-{pending.generation:08d}"
        occurrences: dict[tuple[str, int], dict[str, Any]] = {}
        occurrence_request_hashes: dict[tuple[str, int], tuple[str, str]] = {}
        for path in self.occurrences_root.glob("*.json"):
            occurrence = self._verify_occurrence_binding(path)
            identity = occurrence["identity"]
            if identity["request_id"] != request_id:
                # A shared custody root may contain several exact requests.
                # Verify foreign record/hash/key/stage/chain-context integrity
                # above, but do not mix its ordinal into this request's
                # immutable accounting. Request-specific derivation below is
                # possible only for the pending request being counted.
                continue
            request_sha256, request_occurrence_sha256, expected_chain_id = (
                _ordinary_stage_retry_identity_hashes(
                    world_id=pending.binding.world_id,
                    branch_id=pending.binding.branch_id,
                    request_id=request_id,
                    generation_id=identity["generation_id"],
                    stage=identity["stage"],
                    stage_ordinal=identity["stage_ordinal"],
                )
            )
            if (
                identity["generation_id"] != expected_generation_id
                or occurrence["context_sha256"] != pending.context_sha256
                or occurrence["chain_id"] != expected_chain_id
            ):
                raise StateConflictError(
                    "ordinary stage occurrence changed request generation, context, or identity"
                )
            key = (identity["stage"], identity["stage_ordinal"])
            if key in occurrences:
                raise StateConflictError("ordinary stage occurrence identity was duplicated")
            occurrences[key] = occurrence
            occurrence_request_hashes[key] = (
                request_sha256,
                request_occurrence_sha256,
            )

        # The latest-chain file remains the linear semantic cursor. Luna and
        # Reader review lanes are concurrent siblings and intentionally never
        # enter it, but every cursor entry must still be backed by the same
        # verified occurrence binding.
        for entry in cursor_entries:
            key = (entry["stage"], entry["stage_ordinal"])
            cursor_occurrence = occurrences.get(key)
            hashes = occurrence_request_hashes.get(key)
            if (
                cursor_occurrence is None
                or hashes is None
                or cursor_occurrence["chain_id"] != entry["chain_id"]
                or entry["request_sha256"] != hashes[0]
                or entry["request_occurrence_sha256"] != hashes[1]
            ):
                raise StateConflictError("ordinary latest chain lost its stage occurrence binding")

        counts = {stage: 0 for stage in _ORDINARY_STAGE_VALUES}
        for stage in _ORDINARY_STAGE_VALUES:
            ordinals = sorted(
                ordinal for (bound_stage, ordinal) in occurrences if bound_stage == stage
            )
            if ordinals and ordinals != list(range(1, ordinals[-1] + 1)):
                raise StateConflictError("ordinary stage occurrence ordinals are not contiguous")
            counts[stage] = 0 if not ordinals else ordinals[-1]
        return counts

    def _verify_occurrence_binding(self, path: Path) -> dict[str, Any]:
        """Verify one content-free stage-occurrence index record."""

        payload = self._read_json(path)
        expected = {
            "schema_version",
            "occurrence_key",
            "identity",
            "chain_id",
            "scope_sha256",
            "context_sha256",
            "occurrence_binding_sha256",
        }
        if set(payload) != expected or payload.get("schema_version") != (
            self.OCCURRENCE_SCHEMA_VERSION
        ):
            raise StateConflictError("ordinary stage occurrence binding shape changed")
        unsigned = {
            key: value for key, value in payload.items() if key != "occurrence_binding_sha256"
        }
        binding_sha256 = payload.get("occurrence_binding_sha256")
        if not isinstance(binding_sha256, str) or (
            not re_is_sha256(binding_sha256) or canonical_sha256(unsigned) != binding_sha256
        ):
            raise StateConflictError("ordinary stage occurrence binding hash changed")

        identity = payload.get("identity")
        if not isinstance(identity, dict) or set(identity) != {
            "request_id",
            "generation_id",
            "stage",
            "stage_ordinal",
        }:
            raise StateConflictError("ordinary stage occurrence identity changed")
        request_id = identity.get("request_id")
        generation_id = identity.get("generation_id")
        stage = identity.get("stage")
        stage_ordinal = identity.get("stage_ordinal")
        chain_id = payload.get("chain_id")
        scope_sha256 = payload.get("scope_sha256")
        context_sha256 = payload.get("context_sha256")
        if (
            not isinstance(request_id, str)
            or not isinstance(generation_id, str)
            or not isinstance(stage, str)
            or not isinstance(chain_id, str)
            or not isinstance(scope_sha256, str)
            or not isinstance(context_sha256, str)
        ):
            raise StateConflictError("ordinary stage occurrence identity changed")
        try:
            _require_request_id(request_id)
            _require_identifier(generation_id, "occurrence generation ID")
            _require_chain_id(chain_id)
            _require_sha256(scope_sha256, "occurrence scope hash")
            _require_sha256(context_sha256, "occurrence context hash")
        except ContractValidationError as exc:
            raise StateConflictError("ordinary stage occurrence identity changed") from exc
        if (
            stage not in _ORDINARY_STAGE_VALUES
            or type(stage_ordinal) is not int
            or stage_ordinal < 1
        ):
            raise StateConflictError("ordinary stage occurrence identity changed")

        occurrence_key = domain_sha256(
            "cera.ordinary_stage_retry_occurrence_key.v1",
            identity,
        )
        if payload.get("occurrence_key") != occurrence_key or path.name != f"{occurrence_key}.json":
            raise StateConflictError("ordinary stage occurrence key changed")
        chain = self.chain_context(chain_id)
        if chain.request_id != request_id or chain.context_sha256 != context_sha256:
            raise StateConflictError("ordinary stage occurrence changed its chain link")
        return payload

    def require_review_stage_occurrences(
        self,
        *,
        request_id: str,
        stage_occurrence_counts: Mapping[str, int],
    ) -> dict[str, int]:
        """Authorize rebinding only from an active immutable review snapshot."""

        _require_request_id(request_id)
        counts = _normalize_stage_occurrence_counts(stage_occurrence_counts)
        matched = False
        for path in self.reviews_root.glob("*.json"):
            payload = self._read_json(path)
            if payload.get("schema_version") == self.REVIEW_TOMBSTONE_SCHEMA_VERSION:
                self._verify_review_tombstone(payload)
                continue
            review_id = payload.get("review_id")
            if not isinstance(review_id, str):
                raise StateConflictError("ordinary review request mapping changed")
            identity, review_counts = self.load_review_request(review_id)
            if identity.request_id == request_id and review_counts == counts:
                matched = True
        if not matched:
            raise StateConflictError("ordinary prior stage occurrences lack active review custody")
        return counts

    def request_receipt(self, request_id: str) -> ProtectedOrdinaryCustodyReceiptV1:
        """Load active or terminal content-free request custody."""

        _require_request_id(request_id)
        payload = self._read_json(self._request_path(request_id))
        if payload.get("schema_version") == self.TOMBSTONE_SCHEMA_VERSION:
            self._verify_tombstone(payload)
            return self._receipt_from_tombstone(payload)
        pending = self.load_request(request_id)
        return ProtectedOrdinaryCustodyReceiptV1(
            request_id=request_id,
            request_binding_sha256=canonical_sha256(pending.binding.to_payload()),
            normalized_request_sha256=pending.binding.normalized_request_sha256,
            context_sha256=pending.context_sha256,
            disposition="active",
            branch_barrier_active=True,
        )

    def retire_review_request(
        self,
        review_id: str,
        *,
        terminal_evidence_sha256: str,
    ) -> ProtectedOrdinaryCustodyReceiptV1:
        """Retire one review and release raw request custody only when it is last."""

        _require_identifier(review_id, "review ID")
        _require_sha256(terminal_evidence_sha256, "review terminal evidence")
        path = self._review_path(review_id)
        with self._claim(f"review:{review_id}"):
            payload = self._read_json(path)
            if payload.get("schema_version") == self.REVIEW_TOMBSTONE_SCHEMA_VERSION:
                self._verify_review_tombstone(payload, review_id=review_id)
                if payload["terminal_evidence_sha256"] != terminal_evidence_sha256:
                    raise StateConflictError("ordinary review retirement evidence changed")
                request_id = payload["request_id"]
            else:
                identity, counts = self.load_review_request(review_id)
                body = {
                    "schema_version": self.REVIEW_TOMBSTONE_SCHEMA_VERSION,
                    **to_primitive(identity),
                    "stage_occurrence_counts": counts,
                    "terminal_evidence_sha256": terminal_evidence_sha256,
                }
                retired = {**body, "review_tombstone_sha256": canonical_sha256(body)}
                self._atomic_write(path, retired)
                request_id = identity.request_id

            if self._has_active_review_for_request(request_id):
                return self.request_receipt(request_id)
            receipt = self.request_receipt(request_id)
            if receipt.disposition == "completed":
                return receipt
            if receipt.disposition != "active":
                raise StateConflictError(
                    "ordinary review request has a non-completion terminal disposition"
                )
            return self.redact_request(
                request_id,
                disposition="completed",
                terminal_evidence_sha256=terminal_evidence_sha256,
            )

    def branch_barrier(
        self,
        *,
        world_id: str,
        branch_id: str,
    ) -> ProtectedOrdinaryCustodyReceiptV1 | None:
        """Return the sole active or operator-terminal request for a branch."""

        with self._claim(self._branch_claim_identity(world_id, branch_id)):
            return self._branch_barrier_unlocked(world_id=world_id, branch_id=branch_id)

    def _branch_barrier_unlocked(
        self,
        *,
        world_id: str,
        branch_id: str,
    ) -> ProtectedOrdinaryCustodyReceiptV1 | None:
        branch_sha256 = _branch_scope_sha256(world_id, branch_id)
        matches: list[ProtectedOrdinaryCustodyReceiptV1] = []
        for path in self.requests_root.glob("*.json"):
            payload = self._read_json(path)
            if payload.get("schema_version") == self.SCHEMA_VERSION:
                binding = binding_from_payload(payload.get("binding"))
                if (binding.world_id, binding.branch_id) != (world_id, branch_id):
                    continue
                pending = self.load_request(binding.request_id)
                matches.append(
                    ProtectedOrdinaryCustodyReceiptV1(
                        request_id=binding.request_id,
                        request_binding_sha256=canonical_sha256(binding.to_payload()),
                        normalized_request_sha256=binding.normalized_request_sha256,
                        context_sha256=pending.context_sha256,
                        disposition="active",
                        branch_barrier_active=True,
                    )
                )
            elif payload.get("schema_version") == self.TOMBSTONE_SCHEMA_VERSION:
                self._verify_tombstone(payload)
                if (
                    payload["branch_scope_sha256"] == branch_sha256
                    and payload["branch_barrier_active"]
                ):
                    matches.append(self._receipt_from_tombstone(payload))
        if len(matches) > 1:
            raise StateConflictError("ordinary branch has multiple pending request barriers")
        return None if not matches else matches[0]

    def release_branch_barrier(
        self,
        request_id: str,
        *,
        recovery_evidence_sha256: str,
    ) -> ProtectedOrdinaryCustodyReceiptV1:
        """Release only a previously stable operator-terminal branch barrier."""

        _require_sha256(recovery_evidence_sha256, "barrier recovery evidence")
        path = self._request_path(request_id)
        with self._claim(request_id):
            payload = self._read_json(path)
            self._verify_tombstone(payload)
            if payload["disposition"] == "completed":
                return self._receipt_from_tombstone(payload)
            prior_evidence = payload["barrier_release_evidence_sha256"]
            if not payload["branch_barrier_active"]:
                if prior_evidence != recovery_evidence_sha256:
                    raise StateConflictError("ordinary branch barrier recovery changed")
                return self._receipt_from_tombstone(payload)
            body = {key: value for key, value in payload.items() if key != "tombstone_sha256"}
            body["branch_barrier_active"] = False
            body["barrier_release_evidence_sha256"] = recovery_evidence_sha256
            released = {**body, "tombstone_sha256": canonical_sha256(body)}
            self._atomic_write(path, released)
            return self._receipt_from_tombstone(released)

    def bind_terminal_response(
        self,
        *,
        request_id: str,
        response: Mapping[str, Any],
    ) -> ProtectedOrdinaryTerminalResponseReceiptV1:
        """Persist exact terminal response before delivery or request redaction."""

        _require_request_id(request_id)
        self.load_request(request_id)
        normalized = to_primitive(response)
        if not isinstance(normalized, dict):
            raise ContractValidationError("ordinary terminal response must be an object")
        body = {
            "schema_version": self.RESPONSE_SCHEMA_VERSION,
            "request_id": request_id,
            "response": normalized,
            "response_sha256": canonical_sha256(normalized),
            "response_size_bytes": len(canonical_bytes(normalized)),
        }
        payload = {**body, "terminal_response_sha256": canonical_sha256(body)}
        path = self._response_path(request_id)
        with self._claim(f"response:{request_id}"):
            if path.exists() and self._read_json(path) != payload:
                raise StateConflictError("ordinary terminal response changed")
            if not path.exists():
                self._atomic_write(path, payload)
            response_value, receipt = self.load_terminal_response(request_id)
            if response_value != normalized:
                raise StateConflictError("ordinary terminal response readback changed")
            return receipt

    def load_terminal_response(
        self,
        request_id: str,
    ) -> tuple[dict[str, Any], ProtectedOrdinaryTerminalResponseReceiptV1]:
        _require_request_id(request_id)
        payload = self._read_json(self._response_path(request_id))
        expected = {
            "schema_version",
            "request_id",
            "response",
            "response_sha256",
            "response_size_bytes",
            "terminal_response_sha256",
        }
        if set(payload) != expected or payload["schema_version"] != (self.RESPONSE_SCHEMA_VERSION):
            raise StateConflictError("ordinary terminal response shape changed")
        unsigned = {
            key: value for key, value in payload.items() if key != "terminal_response_sha256"
        }
        response = payload["response"]
        if (
            not isinstance(response, dict)
            or canonical_sha256(unsigned) != payload["terminal_response_sha256"]
            or payload["request_id"] != request_id
            or canonical_sha256(response) != payload["response_sha256"]
            or len(canonical_bytes(response)) != payload["response_size_bytes"]
        ):
            raise StateConflictError("ordinary terminal response custody changed")
        return dict(response), ProtectedOrdinaryTerminalResponseReceiptV1(
            request_id=payload["request_id"],
            response_sha256=payload["response_sha256"],
            response_size_bytes=payload["response_size_bytes"],
        )

    def load_terminal_response_optional(
        self,
        request_id: str,
    ) -> tuple[dict[str, Any], ProtectedOrdinaryTerminalResponseReceiptV1] | None:
        """Return None only when no response exists; corrupt custody still conflicts."""

        _require_request_id(request_id)
        if not self._response_path(request_id).exists():
            return None
        return self.load_terminal_response(request_id)

    def load_terminal_response_for_chain(
        self,
        chain_id: str,
    ) -> tuple[dict[str, Any], ProtectedOrdinaryTerminalResponseReceiptV1]:
        """Recover a lost terminal POST response from only its public chain ID."""

        chain = self.chain_context(chain_id)
        return self.load_terminal_response(chain.request_id)

    def bind_terminal_response_for_chain(
        self,
        *,
        chain_id: str,
        response: Mapping[str, Any],
    ) -> ProtectedOrdinaryTerminalResponseReceiptV1:
        chain = self.chain_context(chain_id)
        return self.bind_terminal_response(
            request_id=chain.request_id,
            response=response,
        )

    def redact_request(
        self,
        request_id: str,
        *,
        disposition: str,
        terminal_evidence_sha256: str,
    ) -> ProtectedOrdinaryCustodyReceiptV1:
        if disposition not in _STABLE_REDACTION_DISPOSITIONS:
            raise StateConflictError("ordinary request disposition is not stably terminal")
        _require_sha256(terminal_evidence_sha256, "terminal evidence")
        path = self._request_path(request_id)
        with self._claim(request_id):
            payload = self._read_json(path)
            if payload.get("schema_version") == self.TOMBSTONE_SCHEMA_VERSION:
                self._verify_tombstone(payload)
                if (
                    payload.get("disposition") != disposition
                    or payload.get("terminal_evidence_sha256") != terminal_evidence_sha256
                ):
                    raise StateConflictError("ordinary request redaction disposition changed")
                return self._receipt_from_tombstone(payload)
            pending = self.load_request(request_id)
            tombstone_body = {
                "schema_version": self.TOMBSTONE_SCHEMA_VERSION,
                "request_id": request_id,
                "request_binding_sha256": canonical_sha256(pending.binding.to_payload()),
                "normalized_request_sha256": (pending.binding.normalized_request_sha256),
                "context_sha256": pending.context_sha256,
                "branch_scope_sha256": _branch_scope_sha256(
                    pending.binding.world_id,
                    pending.binding.branch_id,
                ),
                "disposition": disposition,
                "terminal_evidence_sha256": terminal_evidence_sha256,
                "branch_barrier_active": disposition != "completed",
                "barrier_release_evidence_sha256": None,
            }
            tombstone = {
                **tombstone_body,
                "tombstone_sha256": canonical_sha256(tombstone_body),
            }
            self._atomic_write(path, tombstone)
            return self._receipt_from_tombstone(tombstone)

    def bind_recorder_continuation(
        self,
        continuation: ProtectedRecorderContinuationV1,
    ) -> None:
        if type(continuation) is not ProtectedRecorderContinuationV1:
            raise ContractValidationError("ordinary Recorder continuation changed")
        chain = self.chain_context(continuation.chain_id)
        if chain.request_id != continuation.request_id:
            raise StateConflictError("ordinary Recorder continuation changed request custody")
        body = {
            "schema_version": self.RECORDER_SCHEMA_VERSION,
            "chain_id": continuation.chain_id,
            "request_id": continuation.request_id,
            "review_id": continuation.review_id,
            "accepted_turn_id": continuation.accepted_turn_id,
            "accepted_receipt_sha256": continuation.accepted_receipt_sha256,
        }
        payload = {**body, "continuation_sha256": canonical_sha256(body)}
        path = self._recorder_path(continuation.chain_id)
        with self._claim(continuation.chain_id):
            if path.exists() and self._read_json(path) != payload:
                raise StateConflictError("ordinary Recorder continuation changed")
            if not path.exists():
                self._atomic_write(path, payload)
            if self.load_recorder_continuation(continuation.chain_id) != continuation:
                raise StateConflictError("ordinary Recorder continuation readback changed")

    def load_recorder_continuation(
        self,
        chain_id: str,
    ) -> ProtectedRecorderContinuationV1:
        _require_chain_id(chain_id)
        payload = self._read_json(self._recorder_path(chain_id))
        expected = {
            "schema_version",
            "chain_id",
            "request_id",
            "review_id",
            "accepted_turn_id",
            "accepted_receipt_sha256",
            "continuation_sha256",
        }
        if set(payload) != expected or payload["schema_version"] != (self.RECORDER_SCHEMA_VERSION):
            raise StateConflictError("ordinary Recorder continuation shape changed")
        unsigned = {key: value for key, value in payload.items() if key != "continuation_sha256"}
        if canonical_sha256(unsigned) != payload["continuation_sha256"]:
            raise StateConflictError("ordinary Recorder continuation hash changed")
        if payload["chain_id"] != chain_id:
            raise StateConflictError("ordinary Recorder continuation identity changed")
        return ProtectedRecorderContinuationV1(
            chain_id=payload["chain_id"],
            request_id=payload["request_id"],
            review_id=payload["review_id"],
            accepted_turn_id=payload["accepted_turn_id"],
            accepted_receipt_sha256=payload["accepted_receipt_sha256"],
        )

    def find_recorder_continuation(
        self,
        chain_id: str,
    ) -> ProtectedRecorderContinuationV1 | None:
        _require_chain_id(chain_id)
        if not self._recorder_path(chain_id).exists():
            return None
        return self.load_recorder_continuation(chain_id)

    def recorder_continuation_for_review(
        self,
        *,
        review_id: str,
        accepted_turn_id: str,
        accepted_receipt_sha256: str,
    ) -> ProtectedRecorderContinuationV1 | None:
        """Reverse-resolve the sole initial Recorder chain for an accepted review."""

        _require_identifier(review_id, "review ID")
        _require_identifier(accepted_turn_id, "accepted turn ID")
        _require_sha256(accepted_receipt_sha256, "accepted receipt hash")
        matches: list[ProtectedRecorderContinuationV1] = []
        with self._claim("recorder-continuation-index"):
            for path in self.recorder_root.glob("*.json"):
                payload = self._read_json(path)
                chain_id = payload.get("chain_id")
                if not isinstance(chain_id, str):
                    raise StateConflictError("ordinary Recorder continuation index changed shape")
                continuation = self.load_recorder_continuation(chain_id)
                if continuation.review_id != review_id:
                    continue
                if (
                    continuation.accepted_turn_id != accepted_turn_id
                    or continuation.accepted_receipt_sha256 != accepted_receipt_sha256
                ):
                    raise StateConflictError(
                        "ordinary Recorder continuation changed accepted review"
                    )
                matches.append(continuation)
        if len(matches) > 1:
            raise StateConflictError("ordinary accepted review has multiple Recorder parent chains")
        return None if not matches else matches[0]

    def bind_recorder_repair_successor(
        self,
        authority: ProtectedRecorderRepairSuccessorV1,
    ) -> ProtectedRecorderRepairSuccessorV1:
        """Freeze the sole repair successor for one accepted Recorder parent."""

        if type(authority) is not ProtectedRecorderRepairSuccessorV1:
            raise ContractValidationError("ordinary Recorder repair authority changed")
        parent = self.load_recorder_continuation(authority.parent_chain_id)
        if (
            parent.request_id != authority.request_id
            or parent.review_id != authority.review_id
            or parent.accepted_turn_id != authority.accepted_turn_id
            or parent.accepted_receipt_sha256 != authority.accepted_receipt_sha256
        ):
            raise StateConflictError("ordinary Recorder repair changed accepted continuation")
        parent_context = self.chain_context(authority.parent_chain_id)
        successor_context = self.chain_context(authority.successor_chain_id)
        if (
            parent_context.request_id != authority.request_id
            or successor_context.request_id != authority.request_id
            or successor_context.context_sha256 != parent_context.context_sha256
        ):
            raise StateConflictError("ordinary Recorder repair changed request custody")
        projected = to_primitive(authority)
        if not isinstance(projected, dict):
            raise ContractValidationError("ordinary Recorder repair did not encode as an object")
        body = {
            "schema_version": self.RECORDER_REPAIR_SCHEMA_VERSION,
            **projected,
        }
        payload = {**body, "repair_binding_sha256": canonical_sha256(body)}
        path = self._recorder_repair_path(authority.parent_chain_id)
        with self._claim("recorder-repair-authority"):
            for candidate in self.recorder_repairs_root.glob("*.json"):
                bound = self._verify_recorder_repair_payload(self._read_json(candidate))
                if bound.successor_chain_id == authority.parent_chain_id:
                    raise StateConflictError("ordinary Recorder repair cannot recurse")
                if (
                    bound.parent_chain_id == authority.parent_chain_id
                    or bound.successor_chain_id == authority.successor_chain_id
                    or bound.review_id == authority.review_id
                    or bound.accepted_receipt_sha256 == authority.accepted_receipt_sha256
                ) and bound != authority:
                    raise StateConflictError("ordinary Recorder repair successor changed")
            if path.exists():
                if self._read_json(path) != payload:
                    raise StateConflictError("ordinary Recorder repair authority changed")
            else:
                self._atomic_write(path, payload)
        return self.load_recorder_repair_successor(authority.parent_chain_id)

    def load_recorder_repair_successor(
        self,
        parent_chain_id: str,
    ) -> ProtectedRecorderRepairSuccessorV1:
        _require_chain_id(parent_chain_id)
        authority = self._verify_recorder_repair_payload(
            self._read_json(self._recorder_repair_path(parent_chain_id))
        )
        if authority.parent_chain_id != parent_chain_id:
            raise StateConflictError("ordinary Recorder repair parent changed")
        return authority

    def find_recorder_repair_successor(
        self,
        parent_chain_id: str,
    ) -> ProtectedRecorderRepairSuccessorV1 | None:
        _require_chain_id(parent_chain_id)
        path = self._recorder_repair_path(parent_chain_id)
        if not path.exists():
            return None
        return self.load_recorder_repair_successor(parent_chain_id)

    def recorder_repair_parent_for_successor(
        self,
        successor_chain_id: str,
    ) -> ProtectedRecorderRepairSuccessorV1 | None:
        _require_chain_id(successor_chain_id)
        matched: list[ProtectedRecorderRepairSuccessorV1] = []
        with self._claim("recorder-repair-authority"):
            for candidate in self.recorder_repairs_root.glob("*.json"):
                authority = self._verify_recorder_repair_payload(self._read_json(candidate))
                if authority.successor_chain_id == successor_chain_id:
                    matched.append(authority)
        if len(matched) > 1:
            raise StateConflictError("ordinary Recorder repair successor is ambiguous")
        return None if not matched else matched[0]

    def _verify_recorder_repair_payload(
        self,
        payload: Mapping[str, Any],
    ) -> ProtectedRecorderRepairSuccessorV1:
        expected = {
            "schema_version",
            "parent_chain_id",
            "successor_chain_id",
            "request_id",
            "review_id",
            "accepted_turn_id",
            "accepted_receipt_sha256",
            "parent_chain_sha256",
            "repair_action_id",
            "repair_action_sha256",
            "successor_scope_sha256",
            "repair_binding_sha256",
        }
        unsigned = {key: value for key, value in payload.items() if key != "repair_binding_sha256"}
        if (
            set(payload) != expected
            or payload.get("schema_version") != self.RECORDER_REPAIR_SCHEMA_VERSION
            or canonical_sha256(unsigned) != payload.get("repair_binding_sha256")
        ):
            raise StateConflictError("ordinary Recorder repair authority shape changed")
        try:
            return ProtectedRecorderRepairSuccessorV1(
                parent_chain_id=payload["parent_chain_id"],
                successor_chain_id=payload["successor_chain_id"],
                request_id=payload["request_id"],
                review_id=payload["review_id"],
                accepted_turn_id=payload["accepted_turn_id"],
                accepted_receipt_sha256=payload["accepted_receipt_sha256"],
                parent_chain_sha256=payload["parent_chain_sha256"],
                repair_action_id=payload["repair_action_id"],
                repair_action_sha256=payload["repair_action_sha256"],
                successor_scope_sha256=payload["successor_scope_sha256"],
            )
        except (ContractValidationError, KeyError, TypeError) as exc:
            raise StateConflictError("ordinary Recorder repair authority changed") from exc

    def _request_path(self, request_id: str) -> Path:
        _require_request_id(request_id)
        return self.requests_root / f"{request_id.removeprefix('request-')}.json"

    def _recorder_path(self, chain_id: str) -> Path:
        _require_chain_id(chain_id)
        return self.recorder_root / f"{chain_id.removeprefix('stage-retry-')}.json"

    def _recorder_repair_path(self, parent_chain_id: str) -> Path:
        _require_chain_id(parent_chain_id)
        return self.recorder_repairs_root / (f"{parent_chain_id.removeprefix('stage-retry-')}.json")

    def _chain_path(self, chain_id: str) -> Path:
        _require_chain_id(chain_id)
        return self.chains_root / f"{chain_id.removeprefix('stage-retry-')}.json"

    def _response_path(self, request_id: str) -> Path:
        _require_request_id(request_id)
        return self.responses_root / f"{request_id.removeprefix('request-')}.json"

    def _review_path(self, review_id: str) -> Path:
        _require_identifier(review_id, "review ID")
        key = domain_sha256(
            "cera.ordinary_stage_retry_review_request_key.v1",
            {"review_id": review_id},
        )
        return self.reviews_root / f"{key}.json"

    def _review_action_path(self, action_id: str) -> Path:
        _require_identifier(action_id, "review action ID")
        if not action_id.startswith("review-action-") or not re_is_sha256(
            action_id.removeprefix("review-action-")
        ):
            raise ContractValidationError("ordinary protected custody review action ID is invalid")
        return self.review_actions_root / f"{action_id.removeprefix('review-action-')}.json"

    def _review_action_chain_path(self, chain_id: str) -> Path:
        _require_chain_id(chain_id)
        return self.review_action_chains_root / f"{chain_id.removeprefix('stage-retry-')}.json"

    def _review_action_response_path(self, action_id: str) -> Path:
        self._review_action_path(action_id)
        return self.review_action_responses_root / (
            f"{action_id.removeprefix('review-action-')}.json"
        )

    def _occurrence_path(self, occurrence_key: str) -> Path:
        _require_sha256(occurrence_key, "occurrence key")
        return self.occurrences_root / f"{occurrence_key}.json"

    def _latest_chain_path(self, request_id: str) -> Path:
        _require_request_id(request_id)
        return self.latest_chains_root / f"{request_id.removeprefix('request-')}.json"

    def _has_active_review_for_request(self, request_id: str) -> bool:
        _require_request_id(request_id)
        found = False
        for path in self.reviews_root.glob("*.json"):
            payload = self._read_json(path)
            if payload.get("schema_version") == self.REVIEW_TOMBSTONE_SCHEMA_VERSION:
                self._verify_review_tombstone(payload)
                continue
            review_id = payload.get("review_id")
            if not isinstance(review_id, str):
                raise StateConflictError("ordinary review request mapping changed")
            identity, _ = self.load_review_request(review_id)
            if identity.request_id == request_id:
                found = True
        return found

    def _active_review_action_for_review(
        self,
        review_id: str,
    ) -> ProtectedOrdinaryReviewActionIdentityV1 | None:
        _require_identifier(review_id, "review ID")
        matches: list[ProtectedOrdinaryReviewActionIdentityV1] = []
        for path in self.review_actions_root.glob("*.json"):
            payload = self._read_json(path)
            if payload.get("schema_version") == self.REVIEW_ACTION_TOMBSTONE_SCHEMA_VERSION:
                self._verify_review_action_tombstone(payload)
                continue
            action_id = payload.get("action_id")
            if not isinstance(action_id, str):
                raise StateConflictError("ordinary protected review action changed")
            identity, _, _ = self.load_review_action(action_id)
            if identity.review_id == review_id:
                matches.append(identity)
        if len(matches) > 1:
            raise StateConflictError("ordinary review has multiple protected actions")
        return None if not matches else matches[0]

    def _review_request_identity_any(
        self,
        review_id: str,
    ) -> tuple[ProtectedOrdinaryReviewRequestIdentityV1, dict[str, int]]:
        payload = self._read_json(self._review_path(review_id))
        if payload.get("schema_version") == self.REVIEW_TOMBSTONE_SCHEMA_VERSION:
            return self._verify_review_tombstone(payload, review_id=review_id)
        return self.load_review_request(review_id)

    def _review_action_identity_any(
        self,
        action_id: str,
    ) -> tuple[ProtectedOrdinaryReviewActionIdentityV1, dict[str, int]]:
        payload = self._read_json(self._review_action_path(action_id))
        if payload.get("schema_version") == self.REVIEW_ACTION_TOMBSTONE_SCHEMA_VERSION:
            identity = self._verify_review_action_tombstone(
                payload,
                action_id=action_id,
            )
            counts_value = payload.get("stage_occurrence_counts")
            if not isinstance(counts_value, dict):
                raise StateConflictError("ordinary protected review action tombstone changed")
            try:
                counts = _normalize_stage_occurrence_counts(counts_value)
            except ContractValidationError as exc:
                raise StateConflictError(
                    "ordinary protected review action tombstone changed"
                ) from exc
            return identity, counts
        identity, _, counts = self.load_review_action(action_id)
        return identity, counts

    def _verify_review_action_tombstone(
        self,
        payload: Mapping[str, Any],
        *,
        action_id: str | None = None,
    ) -> ProtectedOrdinaryReviewActionIdentityV1:
        expected = {
            "schema_version",
            "action_id",
            "review_id",
            "request_id",
            "world_id",
            "branch_id",
            "context_sha256",
            "normalized_action_sha256",
            "stage_occurrence_counts_sha256",
            "stage_occurrence_counts",
            "terminal_evidence_sha256",
            "review_action_tombstone_sha256",
        }
        unsigned = {
            key: value for key, value in payload.items() if key != "review_action_tombstone_sha256"
        }
        counts_value = payload.get("stage_occurrence_counts")
        if (
            set(payload) != expected
            or payload.get("schema_version") != self.REVIEW_ACTION_TOMBSTONE_SCHEMA_VERSION
            or canonical_sha256(unsigned) != payload.get("review_action_tombstone_sha256")
            or not isinstance(counts_value, dict)
        ):
            raise StateConflictError("ordinary protected review action tombstone changed")
        try:
            counts = _normalize_stage_occurrence_counts(counts_value)
            identity = ProtectedOrdinaryReviewActionIdentityV1(
                action_id=payload["action_id"],
                review_id=payload["review_id"],
                request_id=payload["request_id"],
                world_id=payload["world_id"],
                branch_id=payload["branch_id"],
                context_sha256=payload["context_sha256"],
                normalized_action_sha256=payload["normalized_action_sha256"],
                stage_occurrence_counts_sha256=canonical_sha256(counts),
            )
            _require_sha256(
                payload["terminal_evidence_sha256"],
                "review action terminal evidence",
            )
        except (ContractValidationError, KeyError, TypeError) as exc:
            raise StateConflictError("ordinary protected review action tombstone changed") from exc
        if canonical_sha256(counts_value) != payload["stage_occurrence_counts_sha256"] or (
            action_id is not None and identity.action_id != action_id
        ):
            raise StateConflictError("ordinary protected review action tombstone identity changed")
        return identity

    def _verify_review_tombstone(
        self,
        payload: Mapping[str, Any],
        *,
        review_id: str | None = None,
    ) -> tuple[ProtectedOrdinaryReviewRequestIdentityV1, dict[str, int]]:
        expected = {
            "schema_version",
            "review_id",
            "request_id",
            "world_id",
            "branch_id",
            "generation",
            "context_sha256",
            "turn_context_sha256",
            "candidate_id",
            "candidate_sha256",
            "stage_occurrence_counts_sha256",
            "stage_occurrence_counts",
            "terminal_evidence_sha256",
            "review_tombstone_sha256",
        }
        unsigned = {
            key: value for key, value in payload.items() if key != "review_tombstone_sha256"
        }
        counts_value = payload.get("stage_occurrence_counts")
        if (
            set(payload) != expected
            or payload.get("schema_version") != self.REVIEW_TOMBSTONE_SCHEMA_VERSION
            or canonical_sha256(unsigned) != payload.get("review_tombstone_sha256")
            or not isinstance(counts_value, dict)
        ):
            raise StateConflictError("ordinary review request tombstone changed")
        try:
            counts = _normalize_stage_occurrence_counts(counts_value)
            identity = ProtectedOrdinaryReviewRequestIdentityV1(
                review_id=payload["review_id"],
                request_id=payload["request_id"],
                world_id=payload["world_id"],
                branch_id=payload["branch_id"],
                generation=payload["generation"],
                context_sha256=payload["context_sha256"],
                turn_context_sha256=payload["turn_context_sha256"],
                candidate_id=payload["candidate_id"],
                candidate_sha256=payload["candidate_sha256"],
                stage_occurrence_counts_sha256=canonical_sha256(counts),
            )
            _require_sha256(payload["terminal_evidence_sha256"], "review terminal evidence")
        except (ContractValidationError, KeyError, TypeError) as exc:
            raise StateConflictError("ordinary review request tombstone changed") from exc
        if canonical_sha256(counts_value) != payload["stage_occurrence_counts_sha256"] or (
            review_id is not None and identity.review_id != review_id
        ):
            raise StateConflictError("ordinary review request tombstone identity changed")
        return identity, counts

    def _verify_latest_chain_payload(
        self,
        payload: Mapping[str, Any],
        *,
        request_id: str,
        context_sha256: str,
    ) -> list[dict[str, Any]]:
        expected = {
            "schema_version",
            "request_id",
            "context_sha256",
            "entries",
            "latest_chain_id",
            "latest_chain_sha256",
        }
        unsigned = {key: value for key, value in payload.items() if key != "latest_chain_sha256"}
        entries = payload.get("entries")
        if (
            set(payload) != expected
            or payload.get("schema_version") != self.LATEST_CHAIN_SCHEMA_VERSION
            or payload.get("request_id") != request_id
            or payload.get("context_sha256") != context_sha256
            or canonical_sha256(unsigned) != payload.get("latest_chain_sha256")
            or not isinstance(entries, list)
            or not entries
            or payload.get("latest_chain_id") != entries[-1].get("chain_id")
        ):
            raise StateConflictError("ordinary latest-chain cursor changed")
        required_entry = {
            "chain_id",
            "request_sha256",
            "request_occurrence_sha256",
            "stage",
            "stage_ordinal",
            "semantic_action_boundary",
            "recording_repair_boundary",
        }
        normalized: list[dict[str, Any]] = []
        seen: set[str] = set()
        for value in entries:
            if not isinstance(value, dict) or set(value) != required_entry:
                raise StateConflictError("ordinary latest-chain entry shape changed")
            identity = ProtectedOrdinaryChainRequestIdentityV1(
                chain_id=value["chain_id"],
                request_id=request_id,
                request_sha256=value["request_sha256"],
                context_sha256=context_sha256,
            )
            if (
                identity.chain_id in seen
                or not isinstance(value["request_occurrence_sha256"], str)
                or not re_is_sha256(value["request_occurrence_sha256"])
                or value["stage"]
                not in {
                    "planner",
                    "writer",
                    "semantic_validator",
                    "recorder",
                }
                or type(value["stage_ordinal"]) is not int
                or value["stage_ordinal"] < 1
                or type(value["semantic_action_boundary"]) is not bool
                or type(value["recording_repair_boundary"]) is not bool
                or (value["semantic_action_boundary"] and value["recording_repair_boundary"])
            ):
                raise StateConflictError("ordinary latest-chain entry changed")
            seen.add(identity.chain_id)
            normalized.append(dict(value))
        stage_counts: dict[str, int] = {}
        for index, value in enumerate(normalized):
            if index:
                self._require_pipeline_successor(
                    normalized[index - 1],
                    value,
                    semantic_action_boundary=value["semantic_action_boundary"],
                    recording_repair_boundary=value["recording_repair_boundary"],
                )
            elif value["semantic_action_boundary"] or value["recording_repair_boundary"]:
                raise StateConflictError(
                    "ordinary latest chain starts with a continuation boundary"
                )
            expected_ordinal = stage_counts.get(value["stage"], 0) + 1
            if value["stage_ordinal"] != expected_ordinal:
                raise StateConflictError("ordinary latest-chain stage ordinal changed")
            stage_counts[value["stage"]] = expected_ordinal
        return normalized

    @staticmethod
    def _require_pipeline_successor(
        prior: Mapping[str, Any],
        successor: Mapping[str, Any],
        *,
        semantic_action_boundary: bool | None = None,
        recording_repair_boundary: bool | None = None,
    ) -> None:
        boundary = (
            successor.get("semantic_action_boundary", False)
            if semantic_action_boundary is None
            else semantic_action_boundary
        )
        if type(boundary) is not bool:
            raise StateConflictError("ordinary latest chain semantic action boundary changed")
        repair_boundary = (
            successor.get("recording_repair_boundary", False)
            if recording_repair_boundary is None
            else recording_repair_boundary
        )
        if type(repair_boundary) is not bool or (boundary and repair_boundary):
            raise StateConflictError("ordinary latest chain recording repair boundary changed")
        if repair_boundary:
            if prior["stage"] != "recorder" or successor["stage"] != "recorder":
                raise StateConflictError("ordinary latest chain changed Recorder repair boundary")
            return
        if boundary:
            if prior["stage"] not in {
                "writer",
                "semantic_validator",
                "reader",
            } or successor["stage"] not in {
                "planner",
                "writer",
            }:
                raise StateConflictError("ordinary latest chain changed semantic action boundary")
            return
        allowed = {
            "planner": {"writer"},
            "writer": {"semantic_validator", "recorder"},
            "semantic_validator": {"writer", "recorder"},
            # Concurrent review validation lanes are persisted as siblings on
            # their review binding and never enter this linear stage cursor.
            "reader": set(),
            "recorder": set(),
        }
        if successor["stage"] not in allowed[prior["stage"]]:
            raise StateConflictError("ordinary latest chain changed pipeline order")
        if successor["stage"] == prior["stage"]:
            raise StateConflictError("ordinary latest chain reused a stage transition")

    @staticmethod
    def _require_stage_ordinal(
        entries: list[dict[str, Any]],
        successor: Mapping[str, Any],
    ) -> None:
        expected = (
            max(
                (
                    entry["stage_ordinal"]
                    for entry in entries
                    if entry["stage"] == successor["stage"]
                ),
                default=0,
            )
            + 1
        )
        if successor["stage_ordinal"] != expected:
            raise StateConflictError("ordinary latest chain changed stage ordinal")

    @staticmethod
    def _branch_claim_identity(world_id: str, branch_id: str) -> str:
        return f"branch:{_branch_scope_sha256(world_id, branch_id)}"

    @contextmanager
    def _claim(self, identity: str) -> Iterator[None]:
        key = canonical_sha256({"identity": identity})
        path = self.locks_root / f"{key}.lock"
        with self._lock:
            with path.open("a+b") as handle:
                handle.seek(0, os.SEEK_END)
                if handle.tell() == 0:
                    handle.write(b"\x00")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
                try:
                    yield
                finally:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        try:
            payload: object = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise StateConflictError("ordinary protected custody is unreadable") from exc
        if not isinstance(payload, dict):
            raise StateConflictError("ordinary protected custody is not an object")
        return payload

    @staticmethod
    def _atomic_write(path: Path, payload: Mapping[str, Any]) -> None:
        data = canonical_bytes(payload)
        temporary = path.parent / f".{path.name}.{uuid4().hex}.tmp"
        try:
            with temporary.open("xb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            if temporary.exists():
                temporary.unlink()

    @staticmethod
    def _receipt_from_tombstone(
        payload: Mapping[str, Any],
    ) -> ProtectedOrdinaryCustodyReceiptV1:
        return ProtectedOrdinaryCustodyReceiptV1(
            request_id=payload["request_id"],
            request_binding_sha256=payload["request_binding_sha256"],
            normalized_request_sha256=payload["normalized_request_sha256"],
            context_sha256=payload["context_sha256"],
            disposition=payload["disposition"],
            branch_barrier_active=payload["branch_barrier_active"],
        )

    @classmethod
    def _verify_tombstone(cls, payload: Mapping[str, Any]) -> None:
        expected = {
            "schema_version",
            "request_id",
            "request_binding_sha256",
            "normalized_request_sha256",
            "context_sha256",
            "branch_scope_sha256",
            "disposition",
            "terminal_evidence_sha256",
            "branch_barrier_active",
            "barrier_release_evidence_sha256",
            "tombstone_sha256",
        }
        if set(payload) != expected or payload.get("schema_version") != (
            cls.TOMBSTONE_SCHEMA_VERSION
        ):
            raise StateConflictError("ordinary protected tombstone shape changed")
        unsigned = {key: value for key, value in payload.items() if key != "tombstone_sha256"}
        if canonical_sha256(unsigned) != payload["tombstone_sha256"]:
            raise StateConflictError("ordinary protected tombstone hash changed")
        ProtectedOrdinaryCustodyReceiptV1(
            request_id=payload["request_id"],
            request_binding_sha256=payload["request_binding_sha256"],
            normalized_request_sha256=payload["normalized_request_sha256"],
            context_sha256=payload["context_sha256"],
            disposition=payload["disposition"],
            branch_barrier_active=payload["branch_barrier_active"],
        )
        _require_sha256(payload["branch_scope_sha256"], "branch scope hash")
        _require_sha256(payload["terminal_evidence_sha256"], "terminal evidence")
        release = payload["barrier_release_evidence_sha256"]
        if release is not None:
            _require_sha256(release, "barrier release evidence")


__all__ = [
    "ProtectedOrdinaryChainContextV1",
    "ProtectedOrdinaryChainRequestIdentityV1",
    "ProtectedOrdinaryCustodyReceiptV1",
    "ProtectedOrdinaryPendingRequestV1",
    "ProtectedOrdinaryReviewActionChainIdentityV1",
    "ProtectedOrdinaryReviewActionIdentityV1",
    "ProtectedOrdinaryReviewActionResponseReceiptV1",
    "ProtectedOrdinaryReviewRequestIdentityV1",
    "ProtectedOrdinaryStageRetryCustodyStoreV1",
    "ProtectedOrdinaryTerminalResponseReceiptV1",
    "ProtectedRecorderContinuationV1",
    "ProtectedRecorderRepairSuccessorV1",
]
