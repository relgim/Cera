"""Protected exact-action custody for Adult Regenerate stage continuation.

The generic provider-stage authority receives only content-free hashes.  The
normalized creator action and its durable review-decision response stay below
the protected runtime root.  Construction, lookup, response replay, and
finalization are provider-free; only the caller-owned controller invocation in
``resume_review_action`` may advance already-authorized application state.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Any, ClassVar, cast
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

from .adult_full_model_review import AdultBoundRejectedReviewV1
from .adult_operation_store import ProtectedAdultOperationStore
from .adult_orchestration import PreparedAdultRouteOperationV1
from .full_model_controller import (
    AcceptedAdultTurnV1,
    FullModelSceneController,
    RejectedAdultTurnV1,
)
from .provider_stage_retry import ProviderStage
from .provider_stage_retry_packets import ProviderStageFrozenPacketV1
from .provider_stage_retry_scope import ProviderStageRetryOccurrenceScopeV1
from .review_store import LeanSceneTurnInputV1

_ACTION_PREFIX = "adult-review-action-"
_CHAIN_PREFIX = "stage-retry-"
_ADULT_STAGES = frozenset({ProviderStage.ADULT_SCENE, ProviderStage.ADULT_FILTER})


@dataclass(frozen=True, slots=True)
class AdultProviderStageReviewActionIdentityV1:
    """Content-free identity for one exact Adult Regenerate action."""

    SCHEMA_VERSION: ClassVar[str] = "cera.adult_provider_stage_review_action_identity.v1"

    schema_version: str
    action_id: str
    review_id: str
    request_id: str
    candidate_id: str
    world_id: str
    branch_id: str
    review_sha256: str
    predecessor_operation_sha256: str
    normalized_action_sha256: str
    accepted_state_sha256: str
    action_scope_sha256: str

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("adult review action identity schema changed")
        _require_action_id(self.action_id)
        for value, label in (
            (self.review_id, "review ID"),
            (self.request_id, "request ID"),
            (self.candidate_id, "candidate ID"),
            (self.world_id, "world ID"),
            (self.branch_id, "branch ID"),
        ):
            _require_text(value, f"adult review action {label}")
        for value, label in (
            (self.review_sha256, "review"),
            (self.predecessor_operation_sha256, "predecessor operation"),
            (self.normalized_action_sha256, "normalized action"),
            (self.accepted_state_sha256, "accepted state"),
            (self.action_scope_sha256, "scope"),
        ):
            _require_sha256(value, f"adult review action {label}")
        expected_scope = _action_scope_sha256(
            review_id=self.review_id,
            request_id=self.request_id,
            candidate_id=self.candidate_id,
            world_id=self.world_id,
            branch_id=self.branch_id,
            review_sha256=self.review_sha256,
            predecessor_operation_sha256=self.predecessor_operation_sha256,
            normalized_action_sha256=self.normalized_action_sha256,
            accepted_state_sha256=self.accepted_state_sha256,
        )
        if self.action_scope_sha256 != expected_scope:
            raise ContractValidationError("adult review action scope changed")
        if self.action_id != _ACTION_PREFIX + expected_scope:
            raise ContractValidationError("adult review action ID changed")


@dataclass(frozen=True, slots=True)
class AdultProviderStageReviewActionChainIdentityV1:
    """Content-free action epoch bound before one Adult stage dispatch."""

    SCHEMA_VERSION: ClassVar[str] = "cera.adult_provider_stage_review_action_chain_identity.v1"

    schema_version: str
    chain_id: str
    stage: ProviderStage
    action_id: str
    review_id: str
    request_id: str
    normalized_action_sha256: str
    action_scope_sha256: str
    successor_operation_sha256: str
    request_sha256: str
    accepted_state_sha256: str
    authority_sha256: str
    stage_input_sha256: str
    chain_binding_sha256: str

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("adult review action chain schema changed")
        _require_chain_id(self.chain_id)
        if self.stage not in _ADULT_STAGES:
            raise ContractValidationError("adult review action chain changed stage")
        _require_action_id(self.action_id)
        for value, label in (
            (self.review_id, "review ID"),
            (self.request_id, "request ID"),
        ):
            _require_text(value, f"adult review action chain {label}")
        for value, label in (
            (self.normalized_action_sha256, "normalized action"),
            (self.action_scope_sha256, "action scope"),
            (self.successor_operation_sha256, "successor operation"),
            (self.request_sha256, "provider request"),
            (self.accepted_state_sha256, "accepted state"),
            (self.authority_sha256, "authority"),
            (self.stage_input_sha256, "stage input"),
            (self.chain_binding_sha256, "binding"),
        ):
            _require_sha256(value, f"adult review action chain {label}")
        if self.chain_binding_sha256 != _chain_binding_sha256(self):
            raise ContractValidationError("adult review action chain binding changed")


@dataclass(frozen=True, slots=True)
class AdultProviderStageReviewActionResponseReceiptV1:
    """Content-free receipt for one durable review-decision projection."""

    action_id: str
    response_sha256: str
    response_size_bytes: int

    def __post_init__(self) -> None:
        _require_action_id(self.action_id)
        _require_sha256(self.response_sha256, "adult review action response")
        if type(self.response_size_bytes) is not int or self.response_size_bytes < 2:
            raise ContractValidationError("adult review action response size is invalid")


@dataclass(frozen=True, slots=True)
class AdultProviderStageReviewActionResultV1:
    """Trusted continuation result used only for exact HTTP projection."""

    identity: AdultProviderStageReviewActionIdentityV1
    normalized_action: Mapping[str, Any]
    outcome: AcceptedAdultTurnV1 | RejectedAdultTurnV1

    def __post_init__(self) -> None:
        normalized = normalize_adult_review_action(self.normalized_action)
        if canonical_sha256(normalized) != self.identity.normalized_action_sha256:
            raise ContractValidationError("adult review action result changed action")
        if not isinstance(self.outcome, (AcceptedAdultTurnV1, RejectedAdultTurnV1)):
            raise ContractValidationError("adult review action result changed outcome")


@dataclass(frozen=True, slots=True)
class _AdultReviewActionSuccessorBindingV1:
    SCHEMA_VERSION: ClassVar[str] = "cera.adult_provider_stage_review_action_successor.v1"

    schema_version: str
    action_id: str
    successor_operation_sha256: str
    successor_candidate_id: str
    turn_sha256: str
    scene_request_sha256: str
    repair_request_sha256: str
    binding_sha256: str

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("adult review action successor schema changed")
        _require_action_id(self.action_id)
        _require_text(self.successor_candidate_id, "adult action successor candidate")
        for value, label in (
            (self.successor_operation_sha256, "successor operation"),
            (self.turn_sha256, "successor turn"),
            (self.scene_request_sha256, "successor Scene request"),
            (self.repair_request_sha256, "successor repair request"),
            (self.binding_sha256, "successor binding"),
        ):
            _require_sha256(value, f"adult review action {label}")
        if self.binding_sha256 != _successor_binding_sha256(self):
            raise ContractValidationError("adult review action successor binding changed")


@dataclass(slots=True)
class _BoundAdultReviewActionV1:
    identity: AdultProviderStageReviewActionIdentityV1
    normalized_action: dict[str, Any]
    retired: bool


class AdultProviderStageReviewActionRuntimeV1:
    """Protected restart-safe Adult Regenerate action and response custody."""

    ACTION_SCHEMA_VERSION = "cera.adult_provider_stage_review_action.v1"
    TOMBSTONE_SCHEMA_VERSION = "cera.adult_provider_stage_review_action_tombstone.v1"
    SUCCESSOR_SCHEMA_VERSION = _AdultReviewActionSuccessorBindingV1.SCHEMA_VERSION
    CHAIN_SCHEMA_VERSION = "cera.adult_provider_stage_review_action_chain.v1"
    RESPONSE_SCHEMA_VERSION = "cera.adult_provider_stage_review_action_response.v1"

    def __init__(
        self,
        *,
        root: Path,
        operation_store: ProtectedAdultOperationStore,
    ) -> None:
        if type(operation_store) is not ProtectedAdultOperationStore:
            raise ContractValidationError("adult review action operation store changed")
        resolved = root.resolve()
        if not resolved.is_absolute():
            raise ContractValidationError("adult review action root is not absolute")
        self.root = resolved
        self.operation_store = operation_store
        self.actions_root = resolved / "actions"
        self.successors_root = resolved / "successors"
        self.chains_root = resolved / "chains"
        self.responses_root = resolved / "responses"
        for path in (
            self.actions_root,
            self.successors_root,
            self.chains_root,
            self.responses_root,
        ):
            path.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._bound: ContextVar[_BoundAdultReviewActionV1 | None] = ContextVar(
            f"adult_provider_stage_review_action_{id(self)}",
            default=None,
        )

    @contextmanager
    def bind_review_action(
        self,
        *,
        review_id: str,
        normalized_action: Mapping[str, Any],
        controller: FullModelSceneController,
    ) -> Iterator[AdultProviderStageReviewActionIdentityV1]:
        """Freeze one exact normalized action before any provider work."""

        if type(controller) is not FullModelSceneController:
            raise ContractValidationError("adult review action controller changed")
        normalized = normalize_adult_review_action(normalized_action)
        bound = controller.get_adult_review(review_id)
        if type(bound) is not AdultBoundRejectedReviewV1:
            raise StateConflictError("adult review action lost rejected review custody")
        identity = _identity_from_bound_review(bound, normalized)
        if identity.review_id != review_id:
            raise StateConflictError("adult review action changed public review identity")
        exact = self._action_payload(identity, normalized)
        with self._lock:
            path = self._action_path(identity.action_id)
            retired = False
            if path.exists():
                prior = _read_canonical_object(path, "adult review action")
                if prior.get("schema_version") == self.TOMBSTONE_SCHEMA_VERSION:
                    prior_identity, terminal_sha256 = self._verify_tombstone(prior)
                    if prior_identity != identity:
                        raise StateConflictError("adult review action tombstone changed identity")
                    response = self.load_review_action_response_optional(identity.action_id)
                    if response is None or response[1].response_sha256 != terminal_sha256:
                        raise StateConflictError("adult review action tombstone lost response")
                    retired = True
                elif prior != exact:
                    raise StateConflictError("adult review action changed exact custody")
            else:
                _write_once(path, canonical_bytes(exact))
            token = self._bound.set(
                _BoundAdultReviewActionV1(
                    identity=identity,
                    normalized_action=normalized,
                    retired=retired,
                )
            )
        try:
            yield identity
        finally:
            self._bound.reset(token)

    def bind_successor_for_current_action(
        self,
        *,
        prepared: PreparedAdultRouteOperationV1,
        frozen_turn: LeanSceneTurnInputV1,
    ) -> AdultProviderStageReviewActionIdentityV1 | None:
        """Bind the exact repair successor before its first stage dispatch."""

        state = self._bound.get()
        if state is None:
            return None
        if state.retired:
            raise StateConflictError("retired adult review action cannot dispatch")
        identity = state.identity
        predecessor = self.operation_store.lookup(
            request_id=identity.request_id,
            candidate_id=identity.candidate_id,
        )
        repair = predecessor.repair
        successor = self.operation_store.lookup(
            request_id=prepared.request_id,
            candidate_id=prepared.candidate_id,
        )
        if (
            predecessor.prepared.operation_sha256 != identity.predecessor_operation_sha256
            or repair is None
            or repair.predecessor_operation_sha256 != identity.predecessor_operation_sha256
            or repair.successor_operation_sha256 != prepared.operation_sha256
            or successor.prepared != prepared
            or successor.predecessor_repair != repair
            or prepared.request_id != identity.request_id
            or prepared.route_state.world_id != identity.world_id
            or prepared.route_state.branch_id != identity.branch_id
            or _accepted_state_sha256(prepared) != identity.accepted_state_sha256
            or frozen_turn.world_id != identity.world_id
            or frozen_turn.branch_id != identity.branch_id
            or prepared.scene_request.exact_current_source != frozen_turn.exact_user_source
        ):
            raise StateConflictError("adult review action changed repair successor custody")
        body = {
            "schema_version": _AdultReviewActionSuccessorBindingV1.SCHEMA_VERSION,
            "action_id": identity.action_id,
            "successor_operation_sha256": prepared.operation_sha256,
            "successor_candidate_id": prepared.candidate_id,
            "turn_sha256": canonical_sha256(frozen_turn),
            "scene_request_sha256": canonical_sha256(prepared.scene_request),
            "repair_request_sha256": repair.repair_request_sha256,
        }
        binding = _AdultReviewActionSuccessorBindingV1(
            **body,
            binding_sha256=canonical_sha256(body),
        )
        with self._lock:
            _write_once(
                self._successor_path(identity.action_id),
                canonical_bytes(binding),
            )
            if self._load_successor(identity.action_id) != binding:
                raise StateConflictError("adult review action successor readback changed")
        return identity

    def bind_stage_dispatch(
        self,
        *,
        scope: ProviderStageRetryOccurrenceScopeV1,
        packet: ProviderStageFrozenPacketV1,
        source_chain_id: str | None = None,
    ) -> AdultProviderStageReviewActionChainIdentityV1 | None:
        """Bind an action epoch after generic custody and before provider dispatch."""

        if (
            type(scope) is not ProviderStageRetryOccurrenceScopeV1
            or type(packet) is not ProviderStageFrozenPacketV1
        ):
            raise ContractValidationError("adult review action dispatch contract changed")
        state = self._bound.get()
        current_identity = None if state is None else state.identity
        source_identity = (
            None if source_chain_id is None else self._action_identity_for_chain(source_chain_id)
        )
        if current_identity is None and source_identity is None:
            return None
        if (
            current_identity is not None
            and source_identity is not None
            and current_identity != source_identity
        ):
            raise StateConflictError("adult review action continuation changed epoch")
        identity = current_identity or source_identity
        assert identity is not None
        if state is not None and state.retired:
            raise StateConflictError("retired adult review action cannot dispatch")
        successor = self._load_successor(identity.action_id)
        if (
            scope.stage not in _ADULT_STAGES
            or packet.stage is not scope.stage
            or packet.stage_input_sha256 != scope.stage_input_sha256
            or scope.request_id != identity.request_id
            or scope.world_id != identity.world_id
            or scope.branch_id != identity.branch_id
            or scope.accepted_state_sha256 != identity.accepted_state_sha256
            or scope.generation_id != f"adult-operation:{successor.successor_operation_sha256}"
        ):
            raise StateConflictError("adult review action changed provider-stage scope")
        body = {
            "schema_version": AdultProviderStageReviewActionChainIdentityV1.SCHEMA_VERSION,
            "chain_id": scope.identity.chain_id,
            "stage": scope.stage,
            "action_id": identity.action_id,
            "review_id": identity.review_id,
            "request_id": identity.request_id,
            "normalized_action_sha256": identity.normalized_action_sha256,
            "action_scope_sha256": identity.action_scope_sha256,
            "successor_operation_sha256": successor.successor_operation_sha256,
            "request_sha256": scope.request_sha256,
            "accepted_state_sha256": scope.accepted_state_sha256,
            "authority_sha256": scope.authority_sha256,
            "stage_input_sha256": scope.stage_input_sha256,
        }
        chain_identity = AdultProviderStageReviewActionChainIdentityV1(
            schema_version=AdultProviderStageReviewActionChainIdentityV1.SCHEMA_VERSION,
            chain_id=scope.identity.chain_id,
            stage=scope.stage,
            action_id=identity.action_id,
            review_id=identity.review_id,
            request_id=identity.request_id,
            normalized_action_sha256=identity.normalized_action_sha256,
            action_scope_sha256=identity.action_scope_sha256,
            successor_operation_sha256=successor.successor_operation_sha256,
            request_sha256=scope.request_sha256,
            accepted_state_sha256=scope.accepted_state_sha256,
            authority_sha256=scope.authority_sha256,
            stage_input_sha256=scope.stage_input_sha256,
            chain_binding_sha256=domain_sha256(
                "cera.adult_provider_stage_review_action_chain_binding.v1",
                to_primitive(body),
            ),
        )
        payload = {
            "schema_version": self.CHAIN_SCHEMA_VERSION,
            "chain_identity": to_primitive(chain_identity),
            "artifact_sha256": canonical_sha256(chain_identity),
        }
        with self._lock:
            _write_once(self._chain_path(chain_identity.chain_id), canonical_bytes(payload))
            if self.review_action_for_chain(chain_identity.chain_id) != chain_identity:
                raise StateConflictError("adult review action chain readback changed")
        return chain_identity

    def review_action_for_chain(
        self,
        chain_id: str,
    ) -> AdultProviderStageReviewActionChainIdentityV1 | None:
        """Return a content-free action epoch, or None for a normal Adult turn."""

        _require_chain_id(chain_id)
        path = self._chain_path(chain_id)
        if not path.exists():
            return None
        payload = _read_canonical_object(path, "adult review action chain")
        if (
            set(payload) != {"schema_version", "chain_identity", "artifact_sha256"}
            or payload.get("schema_version") != self.CHAIN_SCHEMA_VERSION
            or not isinstance(payload.get("chain_identity"), dict)
            or canonical_sha256(payload["chain_identity"]) != payload.get("artifact_sha256")
        ):
            raise StateConflictError("adult review action chain artifact changed")
        try:
            identity = cast(
                AdultProviderStageReviewActionChainIdentityV1,
                from_mapping(
                    AdultProviderStageReviewActionChainIdentityV1,
                    payload["chain_identity"],
                ),
            )
        except (ContractValidationError, KeyError, TypeError) as exc:
            raise StateConflictError("adult review action chain identity changed") from exc
        if identity.chain_id != chain_id:
            raise StateConflictError("adult review action chain ID changed")
        action = self._action_identity_any(identity.action_id)
        successor = self._load_successor(identity.action_id)
        if (
            identity.action_id != action.action_id
            or identity.review_id != action.review_id
            or identity.request_id != action.request_id
            or identity.normalized_action_sha256 != action.normalized_action_sha256
            or identity.action_scope_sha256 != action.action_scope_sha256
            or identity.accepted_state_sha256 != action.accepted_state_sha256
            or identity.successor_operation_sha256 != successor.successor_operation_sha256
        ):
            raise StateConflictError("adult review action chain lost action custody")
        return identity

    def review_action_identity_for_chain(
        self,
        chain_id: str,
    ) -> AdultProviderStageReviewActionIdentityV1 | None:
        """Return the stable action epoch shared by Scene and Filter."""

        chain = self.review_action_for_chain(chain_id)
        return None if chain is None else self._action_identity_any(chain.action_id)

    def resume_review_action(
        self,
        *,
        chain_id: str,
        controller: FullModelSceneController,
    ) -> AdultProviderStageReviewActionResultV1:
        """Resume the exact creator action after its stage chain succeeds."""

        if type(controller) is not FullModelSceneController:
            raise ContractValidationError("adult review action controller changed")
        chain = self.review_action_for_chain(chain_id)
        if chain is None:
            raise StateConflictError("adult stage chain has no review action")
        identity, normalized = self._load_active_action(chain.action_id)
        successor = self._load_successor(chain.action_id)
        token = self._bound.set(
            _BoundAdultReviewActionV1(
                identity=identity,
                normalized_action=normalized,
                retired=False,
            )
        )
        try:
            outcome = controller.regenerate_adult_review(identity.review_id)
        finally:
            self._bound.reset(token)
        if (
            not isinstance(outcome, (AcceptedAdultTurnV1, RejectedAdultTurnV1))
            or outcome.outcome.prepared.operation_sha256 != successor.successor_operation_sha256
            or outcome.outcome.prepared.request_id != identity.request_id
        ):
            raise StateConflictError("adult review action resumed another successor")
        return AdultProviderStageReviewActionResultV1(
            identity=identity,
            normalized_action=normalized,
            outcome=outcome,
        )

    def bind_review_action_response(
        self,
        *,
        action_id: str,
        response: Mapping[str, Any],
    ) -> AdultProviderStageReviewActionResponseReceiptV1:
        """Durably bind one exact safe review-decision response."""

        identity = self._action_identity_any(action_id)
        projected = to_primitive(response)
        if (
            not isinstance(projected, dict)
            or projected.get("schema_version") != "cera.pi_scene.review_decision.v1"
            or projected.get("creator_action") != "regenerate"
        ):
            raise ContractValidationError("adult review action response is not Regenerate")
        exact = canonical_bytes(projected)
        receipt = AdultProviderStageReviewActionResponseReceiptV1(
            action_id=identity.action_id,
            response_sha256=canonical_sha256(projected),
            response_size_bytes=len(exact),
        )
        body = {
            "schema_version": self.RESPONSE_SCHEMA_VERSION,
            "receipt": to_primitive(receipt),
            "response": projected,
        }
        payload = {**body, "artifact_sha256": canonical_sha256(body)}
        with self._lock:
            _write_once(self._response_path(action_id), canonical_bytes(payload))
            loaded = self.load_review_action_response(action_id)
            if loaded != (projected, receipt):
                raise StateConflictError("adult review action response readback changed")
        return receipt

    def bind_review_action_response_for_chain(
        self,
        *,
        chain_id: str,
        response: Mapping[str, Any],
    ) -> AdultProviderStageReviewActionResponseReceiptV1:
        chain = self.review_action_for_chain(chain_id)
        if chain is None:
            raise StateConflictError("adult stage chain has no review action")
        return self.bind_review_action_response(
            action_id=chain.action_id,
            response=response,
        )

    def load_review_action_response(
        self,
        action_id: str,
    ) -> tuple[dict[str, Any], AdultProviderStageReviewActionResponseReceiptV1]:
        _require_action_id(action_id)
        payload = _read_canonical_object(
            self._response_path(action_id),
            "adult review action response",
        )
        body = {key: value for key, value in payload.items() if key != "artifact_sha256"}
        receipt_value = body.get("receipt")
        response = body.get("response")
        if (
            set(payload) != {"schema_version", "receipt", "response", "artifact_sha256"}
            or body.get("schema_version") != self.RESPONSE_SCHEMA_VERSION
            or not isinstance(receipt_value, dict)
            or not isinstance(response, dict)
            or canonical_sha256(body) != payload.get("artifact_sha256")
        ):
            raise StateConflictError("adult review action response artifact changed")
        try:
            receipt = cast(
                AdultProviderStageReviewActionResponseReceiptV1,
                from_mapping(
                    AdultProviderStageReviewActionResponseReceiptV1,
                    receipt_value,
                ),
            )
        except (ContractValidationError, KeyError, TypeError) as exc:
            raise StateConflictError("adult review action response receipt changed") from exc
        if (
            receipt.action_id != action_id
            or receipt.response_sha256 != canonical_sha256(response)
            or receipt.response_size_bytes != len(canonical_bytes(response))
        ):
            raise StateConflictError("adult review action response binding changed")
        self._action_identity_any(action_id)
        return dict(response), receipt

    def load_review_action_response_optional(
        self,
        action_id: str,
    ) -> tuple[dict[str, Any], AdultProviderStageReviewActionResponseReceiptV1] | None:
        _require_action_id(action_id)
        if not self._response_path(action_id).exists():
            return None
        return self.load_review_action_response(action_id)

    def load_review_action_response_for_chain(
        self,
        chain_id: str,
    ) -> tuple[dict[str, Any], AdultProviderStageReviewActionResponseReceiptV1]:
        chain = self.review_action_for_chain(chain_id)
        if chain is None:
            raise FileNotFoundError(self._chain_path(chain_id))
        return self.load_review_action_response(chain.action_id)

    def load_review_action_response_for_chain_optional(
        self,
        chain_id: str,
    ) -> tuple[dict[str, Any], AdultProviderStageReviewActionResponseReceiptV1] | None:
        chain = self.review_action_for_chain(chain_id)
        if chain is None:
            return None
        return self.load_review_action_response_optional(chain.action_id)

    def finalize_review_action(
        self,
        action_id: str,
    ) -> AdultProviderStageReviewActionIdentityV1:
        """Redact exact action bytes after its durable response is available."""

        _, response_receipt = self.load_review_action_response(action_id)
        with self._lock:
            path = self._action_path(action_id)
            payload = _read_canonical_object(path, "adult review action")
            if payload.get("schema_version") == self.TOMBSTONE_SCHEMA_VERSION:
                identity, terminal_sha256 = self._verify_tombstone(payload)
                if terminal_sha256 != response_receipt.response_sha256:
                    raise StateConflictError("adult review action retirement evidence changed")
                return identity
            identity, _ = self._load_active_action(action_id)
            successor = self._load_successor(action_id)
            body = {
                "schema_version": self.TOMBSTONE_SCHEMA_VERSION,
                "identity": to_primitive(identity),
                "successor_binding_sha256": successor.binding_sha256,
                "terminal_evidence_sha256": response_receipt.response_sha256,
            }
            tombstone = {**body, "artifact_sha256": canonical_sha256(body)}
            _atomic_replace(path, canonical_bytes(tombstone))
            verified, terminal_sha256 = self._verify_tombstone(
                _read_canonical_object(path, "adult review action tombstone")
            )
            if verified != identity or terminal_sha256 != response_receipt.response_sha256:
                raise StateConflictError("adult review action tombstone readback changed")
            return verified

    def _action_payload(
        self,
        identity: AdultProviderStageReviewActionIdentityV1,
        normalized: Mapping[str, Any],
    ) -> dict[str, Any]:
        body = {
            "schema_version": self.ACTION_SCHEMA_VERSION,
            "identity": to_primitive(identity),
            "normalized_action": to_primitive(normalized),
        }
        return {**body, "artifact_sha256": canonical_sha256(body)}

    def _load_active_action(
        self,
        action_id: str,
    ) -> tuple[AdultProviderStageReviewActionIdentityV1, dict[str, Any]]:
        _require_action_id(action_id)
        payload = _read_canonical_object(self._action_path(action_id), "adult review action")
        if payload.get("schema_version") == self.TOMBSTONE_SCHEMA_VERSION:
            self._verify_tombstone(payload)
            raise StateConflictError("adult review action is retired")
        body = {key: value for key, value in payload.items() if key != "artifact_sha256"}
        identity_value = body.get("identity")
        normalized_value = body.get("normalized_action")
        if (
            set(payload) != {"schema_version", "identity", "normalized_action", "artifact_sha256"}
            or body.get("schema_version") != self.ACTION_SCHEMA_VERSION
            or not isinstance(identity_value, dict)
            or not isinstance(normalized_value, dict)
            or canonical_sha256(body) != payload.get("artifact_sha256")
        ):
            raise StateConflictError("adult review action artifact changed")
        try:
            identity = cast(
                AdultProviderStageReviewActionIdentityV1,
                from_mapping(AdultProviderStageReviewActionIdentityV1, identity_value),
            )
            normalized = normalize_adult_review_action(normalized_value)
        except (ContractValidationError, KeyError, TypeError) as exc:
            raise StateConflictError("adult review action content changed") from exc
        if (
            identity.action_id != action_id
            or canonical_sha256(normalized) != identity.normalized_action_sha256
        ):
            raise StateConflictError("adult review action binding changed")
        return identity, normalized

    def _action_identity_any(
        self,
        action_id: str,
    ) -> AdultProviderStageReviewActionIdentityV1:
        payload = _read_canonical_object(self._action_path(action_id), "adult review action")
        if payload.get("schema_version") == self.TOMBSTONE_SCHEMA_VERSION:
            return self._verify_tombstone(payload)[0]
        return self._load_active_action(action_id)[0]

    def _verify_tombstone(
        self,
        payload: Mapping[str, Any],
    ) -> tuple[AdultProviderStageReviewActionIdentityV1, str]:
        body = {key: value for key, value in payload.items() if key != "artifact_sha256"}
        identity_value = body.get("identity")
        terminal = body.get("terminal_evidence_sha256")
        if (
            set(payload)
            != {
                "schema_version",
                "identity",
                "successor_binding_sha256",
                "terminal_evidence_sha256",
                "artifact_sha256",
            }
            or body.get("schema_version") != self.TOMBSTONE_SCHEMA_VERSION
            or not isinstance(identity_value, dict)
            or not isinstance(terminal, str)
            or canonical_sha256(body) != payload.get("artifact_sha256")
        ):
            raise StateConflictError("adult review action tombstone changed")
        try:
            identity = cast(
                AdultProviderStageReviewActionIdentityV1,
                from_mapping(AdultProviderStageReviewActionIdentityV1, identity_value),
            )
            _require_sha256(terminal, "adult review action terminal evidence")
            _require_sha256(
                cast(str, body.get("successor_binding_sha256")),
                "adult review action successor binding",
            )
        except (ContractValidationError, KeyError, TypeError) as exc:
            raise StateConflictError("adult review action tombstone content changed") from exc
        return identity, terminal

    def _load_successor(self, action_id: str) -> _AdultReviewActionSuccessorBindingV1:
        payload = _read_canonical_object(
            self._successor_path(action_id),
            "adult review action successor",
        )
        try:
            binding = cast(
                _AdultReviewActionSuccessorBindingV1,
                from_mapping(_AdultReviewActionSuccessorBindingV1, payload),
            )
        except (ContractValidationError, KeyError, TypeError) as exc:
            raise StateConflictError("adult review action successor changed") from exc
        if binding.action_id != action_id:
            raise StateConflictError("adult review action successor changed action")
        return binding

    def _action_identity_for_chain(
        self,
        chain_id: str,
    ) -> AdultProviderStageReviewActionIdentityV1 | None:
        return self.review_action_identity_for_chain(chain_id)

    def _action_path(self, action_id: str) -> Path:
        _require_action_id(action_id)
        return self.actions_root / f"{action_id.removeprefix(_ACTION_PREFIX)}.json"

    def _successor_path(self, action_id: str) -> Path:
        _require_action_id(action_id)
        return self.successors_root / f"{action_id.removeprefix(_ACTION_PREFIX)}.json"

    def _response_path(self, action_id: str) -> Path:
        _require_action_id(action_id)
        return self.responses_root / f"{action_id.removeprefix(_ACTION_PREFIX)}.json"

    def _chain_path(self, chain_id: str) -> Path:
        _require_chain_id(chain_id)
        return self.chains_root / f"{chain_id.removeprefix(_CHAIN_PREFIX)}.json"


def normalize_adult_review_action(value: Mapping[str, Any]) -> dict[str, Any]:
    """Return the one canonical Adult Regenerate semantic action."""

    if not isinstance(value, Mapping):
        raise ContractValidationError("adult review action must be an object")
    if set(value) - {"action", "feedback", "force_rehydrate"}:
        raise ContractValidationError("adult review action has unknown fields")
    action = value.get("action")
    feedback = value.get("feedback")
    force_rehydrate = value.get("force_rehydrate", False)
    if action != "regenerate":
        raise ContractValidationError("adult review action is not Regenerate")
    if feedback not in (None, ""):
        raise ContractValidationError("adult Regenerate does not accept feedback")
    if type(force_rehydrate) is not bool:
        raise ContractValidationError("adult review action force_rehydrate is invalid")
    if force_rehydrate:
        raise ContractValidationError("adult Regenerate always uses frozen rehydration")
    return {
        "action": "regenerate",
        "feedback": None,
        "force_rehydrate": False,
    }


def _identity_from_bound_review(
    bound: AdultBoundRejectedReviewV1,
    normalized: Mapping[str, Any],
) -> AdultProviderStageReviewActionIdentityV1:
    prepared = bound.outcome.prepared
    normalized_sha256 = canonical_sha256(normalized)
    accepted_state_sha256 = _accepted_state_sha256(prepared)
    scope_sha256 = _action_scope_sha256(
        review_id=bound.public_review_id,
        request_id=prepared.request_id,
        candidate_id=prepared.candidate_id,
        world_id=prepared.route_state.world_id,
        branch_id=prepared.route_state.branch_id,
        review_sha256=bound.review.review_sha256,
        predecessor_operation_sha256=prepared.operation_sha256,
        normalized_action_sha256=normalized_sha256,
        accepted_state_sha256=accepted_state_sha256,
    )
    return AdultProviderStageReviewActionIdentityV1(
        schema_version=AdultProviderStageReviewActionIdentityV1.SCHEMA_VERSION,
        action_id=_ACTION_PREFIX + scope_sha256,
        review_id=bound.public_review_id,
        request_id=prepared.request_id,
        candidate_id=prepared.candidate_id,
        world_id=prepared.route_state.world_id,
        branch_id=prepared.route_state.branch_id,
        review_sha256=bound.review.review_sha256,
        predecessor_operation_sha256=prepared.operation_sha256,
        normalized_action_sha256=normalized_sha256,
        accepted_state_sha256=accepted_state_sha256,
        action_scope_sha256=scope_sha256,
    )


def _accepted_state_sha256(prepared: PreparedAdultRouteOperationV1) -> str:
    return prepared.route_state.accepted_head_sha256 or domain_sha256(
        "cera.adult_provider_stage_unaccepted_root.v1",
        {
            "world_id": prepared.route_state.world_id,
            "branch_id": prepared.route_state.branch_id,
        },
    )


def _action_scope_sha256(
    *,
    review_id: str,
    request_id: str,
    candidate_id: str,
    world_id: str,
    branch_id: str,
    review_sha256: str,
    predecessor_operation_sha256: str,
    normalized_action_sha256: str,
    accepted_state_sha256: str,
) -> str:
    return domain_sha256(
        "cera.adult_provider_stage_review_action_scope.v1",
        {
            "review_id": review_id,
            "request_id": request_id,
            "candidate_id": candidate_id,
            "world_id": world_id,
            "branch_id": branch_id,
            "review_sha256": review_sha256,
            "predecessor_operation_sha256": predecessor_operation_sha256,
            "normalized_action_sha256": normalized_action_sha256,
            "accepted_state_sha256": accepted_state_sha256,
        },
    )


def _chain_binding_sha256(
    value: AdultProviderStageReviewActionChainIdentityV1,
) -> str:
    body = {key: item for key, item in to_primitive(value).items() if key != "chain_binding_sha256"}
    return domain_sha256(
        "cera.adult_provider_stage_review_action_chain_binding.v1",
        body,
    )


def _successor_binding_sha256(value: _AdultReviewActionSuccessorBindingV1) -> str:
    body = {key: item for key, item in to_primitive(value).items() if key != "binding_sha256"}
    return canonical_sha256(body)


def _read_canonical_object(path: Path, label: str) -> dict[str, Any]:
    try:
        exact = path.read_bytes()
        value: object = json.loads(exact.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StateConflictError(f"{label} is unavailable") from exc
    if not isinstance(value, dict) or canonical_bytes(value) != exact:
        raise StateConflictError(f"{label} is not canonical")
    return cast(dict[str, Any], value)


def _write_once(path: Path, exact: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != exact:
            raise StateConflictError("adult review action artifact changed")
        return
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(exact)
            stream.flush()
            os.fsync(stream.fileno())
        if path.exists():
            if path.read_bytes() != exact:
                raise StateConflictError("adult review action artifact changed")
            return
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _atomic_replace(path: Path, exact: bytes) -> None:
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(exact)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _require_text(value: str, label: str) -> None:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise ContractValidationError(f"{label} is invalid")


def _require_sha256(value: str, label: str) -> None:
    if not isinstance(value, str) or not re_is_sha256(value):
        raise ContractValidationError(f"{label} must be SHA-256")


def _require_action_id(action_id: str) -> None:
    if not isinstance(action_id, str) or not action_id.startswith(_ACTION_PREFIX):
        raise ContractValidationError("adult review action ID is invalid")
    _require_sha256(action_id.removeprefix(_ACTION_PREFIX), "adult review action ID")


def _require_chain_id(chain_id: str) -> None:
    if not isinstance(chain_id, str) or not chain_id.startswith(_CHAIN_PREFIX):
        raise ContractValidationError("adult review action chain ID is invalid")
    _require_sha256(chain_id.removeprefix(_CHAIN_PREFIX), "adult review action chain ID")


__all__ = [
    "AdultProviderStageReviewActionChainIdentityV1",
    "AdultProviderStageReviewActionIdentityV1",
    "AdultProviderStageReviewActionResponseReceiptV1",
    "AdultProviderStageReviewActionResultV1",
    "AdultProviderStageReviewActionRuntimeV1",
    "normalize_adult_review_action",
]
