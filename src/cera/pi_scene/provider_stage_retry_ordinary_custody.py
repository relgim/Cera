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

from .provider_stage_retry_packets import ImmutableRetrievalSnapshotIdentityV1
from .provider_stage_retry_scope import ProviderStageRetryOccurrenceScopeV1
from .request_binding import PiSceneRequestBindingV1, binding_from_payload
from .review_store import LeanSceneTurnInputV1

_STABLE_REDACTION_DISPOSITIONS = frozenset(
    {
        "completed",
        "attempts_exhausted",
        "recording_repair_required",
        "recovery_required",
        "operator_abandoned",
    }
)


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


class ProtectedOrdinaryStageRetryCustodyStoreV1:
    """Atomic trusted-local custody with cross-process Windows file locking."""

    SCHEMA_VERSION: ClassVar[str] = "cera.ordinary_stage_retry_protected_request.v1"
    TOMBSTONE_SCHEMA_VERSION: ClassVar[str] = (
        "cera.ordinary_stage_retry_protected_request_tombstone.v1"
    )
    RECORDER_SCHEMA_VERSION: ClassVar[str] = "cera.ordinary_stage_retry_recorder_continuation.v1"
    CHAIN_SCHEMA_VERSION: ClassVar[str] = "cera.ordinary_stage_retry_chain_context.v1"
    OCCURRENCE_SCHEMA_VERSION: ClassVar[str] = "cera.ordinary_stage_retry_occurrence_binding.v1"
    RESPONSE_SCHEMA_VERSION: ClassVar[str] = "cera.ordinary_stage_retry_terminal_response.v1"
    LATEST_CHAIN_SCHEMA_VERSION: ClassVar[str] = "cera.ordinary_stage_retry_latest_chain.v1"

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.requests_root = self.root / "requests"
        self.chains_root = self.root / "chains"
        self.occurrences_root = self.root / "occurrences"
        self.latest_chains_root = self.root / "latest_chains"
        self.responses_root = self.root / "responses"
        self.recorder_root = self.root / "recorder"
        self.locks_root = self.root / "locks"
        for path in (
            self.root,
            self.requests_root,
            self.chains_root,
            self.occurrences_root,
            self.latest_chains_root,
            self.responses_root,
            self.recorder_root,
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
    ) -> ProtectedOrdinaryChainRequestIdentityV1:
        """Advance a request cursor once, never roll it back during cache replay."""

        if type(scope) is not ProviderStageRetryOccurrenceScopeV1:
            raise ContractValidationError("ordinary latest-chain scope changed")
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

    def _request_path(self, request_id: str) -> Path:
        _require_request_id(request_id)
        return self.requests_root / f"{request_id.removeprefix('request-')}.json"

    def _recorder_path(self, chain_id: str) -> Path:
        _require_chain_id(chain_id)
        return self.recorder_root / f"{chain_id.removeprefix('stage-retry-')}.json"

    def _chain_path(self, chain_id: str) -> Path:
        _require_chain_id(chain_id)
        return self.chains_root / f"{chain_id.removeprefix('stage-retry-')}.json"

    def _response_path(self, request_id: str) -> Path:
        _require_request_id(request_id)
        return self.responses_root / f"{request_id.removeprefix('request-')}.json"

    def _occurrence_path(self, occurrence_key: str) -> Path:
        _require_sha256(occurrence_key, "occurrence key")
        return self.occurrences_root / f"{occurrence_key}.json"

    def _latest_chain_path(self, request_id: str) -> Path:
        _require_request_id(request_id)
        return self.latest_chains_root / f"{request_id.removeprefix('request-')}.json"

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
            ):
                raise StateConflictError("ordinary latest-chain entry changed")
            seen.add(identity.chain_id)
            normalized.append(dict(value))
        stage_counts: dict[str, int] = {}
        for index, value in enumerate(normalized):
            if index:
                self._require_pipeline_successor(normalized[index - 1], value)
            expected_ordinal = stage_counts.get(value["stage"], 0) + 1
            if value["stage_ordinal"] != expected_ordinal:
                raise StateConflictError("ordinary latest-chain stage ordinal changed")
            stage_counts[value["stage"]] = expected_ordinal
        return normalized

    @staticmethod
    def _require_pipeline_successor(
        prior: Mapping[str, Any],
        successor: Mapping[str, Any],
    ) -> None:
        allowed = {
            "planner": {"writer"},
            "writer": {"semantic_validator", "recorder"},
            "semantic_validator": {"writer", "recorder"},
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
    "ProtectedOrdinaryStageRetryCustodyStoreV1",
    "ProtectedOrdinaryTerminalResponseReceiptV1",
    "ProtectedRecorderContinuationV1",
]
