"""Restart-safe creator review and Regenerate for full-model adult turns.

The public review index contains only stable identities and hashes.  The exact
pre-turn input used to construct the protected Writer view is retained in a
separate protected capsule before provider dispatch.  An accepted Regenerate
therefore uses the selected turn's exact pre-turn authority and never rebuilds
its proof target from the current (post-turn) branch state.

This module does not dispatch providers itself.  Callers inject one protected
Scene+Filter execution callback.  The operation store publishes the complete
prepared operation before that callback is reachable.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar, cast
from uuid import uuid4

from cera.adult_pipeline.acceptance import AdultAcceptedTurnEnvelopeV1
from cera.adult_pipeline.contracts import BoundAdultPromotionV1
from cera.errors import ContractValidationError, StateConflictError
from cera.schema import from_mapping
from cera.serialization import (
    canonical_json,
    canonical_sha256,
    domain_sha256,
    re_is_sha256,
    text_sha256,
    to_primitive,
)

from .adult_operation_contracts import (
    AdultOperationDecisionAction,
    AdultOperationDecisionV1,
    AdultOperationReviewState,
    AdultOperationSafeSummaryV1,
)
from .adult_operation_store import (
    ProtectedAdultOperationController,
    ProtectedAdultOperationStore,
)
from .adult_orchestration import (
    AdultRouteOperationOutcomeV1,
    PassedAdultRouteOperationV1,
    PreparedAdultRouteOperationV1,
    RejectedAdultRouteOperationV1,
)
from .adult_review import (
    AdultAutomaticRepairDisposition,
    AdultProvisionalAcceptanceBlockedV1,
    AdultRejectedReviewController,
    AdultRejectedReviewSafeSummaryV1,
)
from .contracts import SceneRoute
from .lineage import LeanAcceptedRegenerationBaseV1
from .review_store import LeanSceneTurnInputV1
from .store import LeanSceneStore

_CAPSULE_DOMAIN = "cera.pi_scene.adult_turn_capsule.v1"
_REVIEW_BINDING_DOMAIN = "cera.pi_scene.adult_review_binding.v1"
_ACCEPTED_REGENERATE_DOMAIN = "cera.pi_scene.accepted_adult_regenerate.v1"
_ACCEPT_DECISION_DOMAIN = "cera.pi_scene.full_model_adult_accept_decision.v1"
_REVIEW_ID = re.compile(r"adult-review:([a-f0-9]{64})\Z")
_PUBLIC_REVIEW_ID = re.compile(r"review-([a-f0-9]{28})\Z")

type ProtectedAdultExecutor = Callable[
    [PreparedAdultRouteOperationV1, LeanSceneTurnInputV1], AdultRouteOperationOutcomeV1
]


@dataclass(frozen=True, slots=True)
class ProtectedAdultTurnCapsuleV1:
    """Exact pre-dispatch input needed to recreate one protected Writer view."""

    SCHEMA_VERSION: ClassVar[str] = "cera.pi_scene.protected_adult_turn_capsule.v1"

    schema_version: str
    operation_sha256: str
    turn_input: LeanSceneTurnInputV1
    turn_input_sha256: str
    capsule_sha256: str

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("adult turn capsule schema changed")
        _sha(self.operation_sha256, "adult turn capsule operation")
        _sha(self.turn_input_sha256, "adult turn capsule input")
        _sha(self.capsule_sha256, "adult turn capsule")
        if canonical_sha256(self.turn_input) != self.turn_input_sha256:
            raise ContractValidationError("adult turn capsule input binding changed")
        if self.capsule_sha256 != _capsule_sha256(
            operation_sha256=self.operation_sha256,
            turn_input=self.turn_input,
        ):
            raise ContractValidationError("adult turn capsule binding changed")

    @classmethod
    def create(
        cls,
        *,
        operation_sha256: str,
        turn_input: LeanSceneTurnInputV1,
    ) -> ProtectedAdultTurnCapsuleV1:
        return cls(
            schema_version=cls.SCHEMA_VERSION,
            operation_sha256=operation_sha256,
            turn_input=turn_input,
            turn_input_sha256=canonical_sha256(turn_input),
            capsule_sha256=_capsule_sha256(
                operation_sha256=operation_sha256,
                turn_input=turn_input,
            ),
        )


@dataclass(frozen=True, slots=True)
class AdultReviewBindingV1:
    """Privacy-safe lookup binding for one identity-bound rejected review."""

    SCHEMA_VERSION: ClassVar[str] = "cera.pi_scene.adult_review_binding.v2"

    schema_version: str
    review_id: str
    public_review_id: str
    review_sha256: str
    request_id: str
    candidate_id: str
    operation_sha256: str
    world_id: str
    branch_id: str
    scene_id: str
    planner_provider_operations: int
    capsule_sha256: str
    replacement_base: LeanAcceptedRegenerationBaseV1 | None
    binding_sha256: str

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("adult review binding schema changed")
        match = _REVIEW_ID.fullmatch(self.review_id)
        if match is None or match.group(1) != self.review_sha256:
            raise ContractValidationError("adult review binding identity changed")
        if self.public_review_id != _public_review_id(self.review_sha256):
            raise ContractValidationError("adult public review identity changed")
        for value, label in (
            (self.review_sha256, "adult review binding review"),
            (self.operation_sha256, "adult review binding operation"),
            (self.capsule_sha256, "adult review binding capsule"),
            (self.binding_sha256, "adult review binding"),
        ):
            _sha(value, label)
        for value, label in (
            (self.request_id, "adult review request"),
            (self.candidate_id, "adult review candidate"),
            (self.world_id, "adult review world"),
            (self.branch_id, "adult review branch"),
            (self.scene_id, "adult review scene"),
        ):
            if type(value) is not str or not value.strip():
                raise ContractValidationError(f"{label} is empty")
        if (
            type(self.planner_provider_operations) is not int
            or self.planner_provider_operations < 0
        ):
            raise ContractValidationError("adult review Planner count is invalid")
        if self.replacement_base is not None and (
            self.replacement_base.world_id != self.world_id
            or self.replacement_base.branch_id != self.branch_id
        ):
            raise ContractValidationError("adult review replacement base changed branch")
        if self.binding_sha256 != _review_binding_sha256(self):
            raise ContractValidationError("adult review binding hash changed")

    @classmethod
    def create(
        cls,
        *,
        review: AdultRejectedReviewSafeSummaryV1,
        turn_input: LeanSceneTurnInputV1,
        capsule: ProtectedAdultTurnCapsuleV1,
        planner_provider_operations: int,
        replacement_base: LeanAcceptedRegenerationBaseV1 | None,
    ) -> AdultReviewBindingV1:
        binding_sha256 = domain_sha256(
            _REVIEW_BINDING_DOMAIN,
            {
                "schema_version": cls.SCHEMA_VERSION,
                "review_id": review.review_id,
                "public_review_id": _public_review_id(review.review_sha256),
                "review_sha256": review.review_sha256,
                "request_id": review.operation.request_id,
                "candidate_id": review.operation.candidate_id,
                "operation_sha256": review.operation.operation_sha256,
                "world_id": turn_input.world_id,
                "branch_id": turn_input.branch_id,
                "scene_id": turn_input.scene_id,
                "planner_provider_operations": planner_provider_operations,
                "capsule_sha256": capsule.capsule_sha256,
                "replacement_base": replacement_base,
            },
        )
        return cls(
            schema_version=cls.SCHEMA_VERSION,
            review_id=review.review_id,
            public_review_id=_public_review_id(review.review_sha256),
            review_sha256=review.review_sha256,
            request_id=review.operation.request_id,
            candidate_id=review.operation.candidate_id,
            operation_sha256=review.operation.operation_sha256,
            world_id=turn_input.world_id,
            branch_id=turn_input.branch_id,
            scene_id=turn_input.scene_id,
            planner_provider_operations=planner_provider_operations,
            capsule_sha256=capsule.capsule_sha256,
            replacement_base=replacement_base,
            binding_sha256=binding_sha256,
        )


@dataclass(frozen=True, slots=True)
class AcceptedAdultRegenerateBindingV1:
    """Durable action binding written before an accepted Regenerate dispatch."""

    SCHEMA_VERSION: ClassVar[str] = "cera.pi_scene.accepted_adult_regenerate_binding.v1"

    schema_version: str
    action_request_id: str
    candidate_id: str
    predecessor_request_id: str
    predecessor_candidate_id: str
    predecessor_operation_sha256: str
    predecessor_capsule_sha256: str
    replacement_base: LeanAcceptedRegenerationBaseV1
    binding_sha256: str

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("accepted adult Regenerate binding schema changed")
        if any(
            not value.strip()
            for value in (
                self.action_request_id,
                self.candidate_id,
                self.predecessor_request_id,
                self.predecessor_candidate_id,
            )
        ):
            raise ContractValidationError("accepted adult Regenerate identity is empty")
        for value, label in (
            (self.predecessor_operation_sha256, "adult Regenerate predecessor"),
            (self.predecessor_capsule_sha256, "adult Regenerate capsule"),
            (self.binding_sha256, "accepted adult Regenerate binding"),
        ):
            _sha(value, label)
        if self.binding_sha256 != _accepted_regenerate_binding_sha256(self):
            raise ContractValidationError("accepted adult Regenerate binding hash changed")

    @classmethod
    def create(
        cls,
        *,
        action_request_id: str,
        candidate_id: str,
        predecessor_request_id: str,
        predecessor_candidate_id: str,
        predecessor_operation_sha256: str,
        predecessor_capsule_sha256: str,
        replacement_base: LeanAcceptedRegenerationBaseV1,
    ) -> AcceptedAdultRegenerateBindingV1:
        binding_sha256 = domain_sha256(
            _ACCEPTED_REGENERATE_DOMAIN,
            {
                "schema_version": cls.SCHEMA_VERSION,
                "action_request_id": action_request_id,
                "candidate_id": candidate_id,
                "predecessor_request_id": predecessor_request_id,
                "predecessor_candidate_id": predecessor_candidate_id,
                "predecessor_operation_sha256": predecessor_operation_sha256,
                "predecessor_capsule_sha256": predecessor_capsule_sha256,
                "replacement_base": replacement_base,
            },
        )
        return cls(
            schema_version=cls.SCHEMA_VERSION,
            action_request_id=action_request_id,
            candidate_id=candidate_id,
            predecessor_request_id=predecessor_request_id,
            predecessor_candidate_id=predecessor_candidate_id,
            predecessor_operation_sha256=predecessor_operation_sha256,
            predecessor_capsule_sha256=predecessor_capsule_sha256,
            replacement_base=replacement_base,
            binding_sha256=binding_sha256,
        )


@dataclass(frozen=True, slots=True)
class AdultPromotedReviewOutcomeV1:
    """Protected accepted result; HTTP must project it before exposure."""

    envelope: AdultAcceptedTurnEnvelopeV1
    promotion: BoundAdultPromotionV1
    operation: AdultOperationSafeSummaryV1
    replaced_receipt_sha256: str | None
    replayed: bool

    def __post_init__(self) -> None:
        if self.promotion.bundle != self.envelope.promotion_bundle:
            raise ContractValidationError("adult reviewed promotion bundle changed")
        if self.operation.state is not AdultOperationReviewState.ACCEPTED:
            raise ContractValidationError("adult reviewed promotion lacks accepted custody")
        if type(self.replayed) is not bool:
            raise ContractValidationError("adult reviewed promotion replay flag is invalid")
        if self.replaced_receipt_sha256 is not None:
            _sha(self.replaced_receipt_sha256, "adult replaced receipt")


@dataclass(frozen=True, slots=True)
class AdultRejectedReviewActionV1:
    action: str
    predecessor_review_id: str
    operation: AdultOperationSafeSummaryV1
    review: AdultRejectedReviewSafeSummaryV1 | None
    accepted: AdultPromotedReviewOutcomeV1 | None
    replayed: bool

    def __post_init__(self) -> None:
        if self.action not in {"regenerate", "automatic_repair"}:
            raise ContractValidationError("adult review action is invalid")
        if (self.review is None) == (self.accepted is None):
            raise ContractValidationError("adult review action has no unique result")
        if self.review is not None and self.review.operation != self.operation:
            raise ContractValidationError("adult review action summary changed")
        if self.accepted is not None and self.accepted.operation != self.operation:
            raise ContractValidationError("adult review accepted summary changed")
        if type(self.replayed) is not bool:
            raise ContractValidationError("adult review action replay flag is invalid")


@dataclass(frozen=True, slots=True)
class AdultBoundRejectedReviewV1:
    """Protected controller view bound to one public creator-review identity."""

    outcome: RejectedAdultRouteOperationV1
    review: AdultRejectedReviewSafeSummaryV1
    public_review_id: str
    planner_provider_operations: int
    regenerate_available: bool

    def __post_init__(self) -> None:
        if self.public_review_id != _public_review_id(self.review.review_sha256):
            raise ContractValidationError("bound adult public review identity changed")
        if (
            self.review.operation.request_id != self.outcome.prepared.request_id
            or self.review.operation.candidate_id != self.outcome.prepared.candidate_id
            or self.review.operation.operation_sha256
            != self.outcome.prepared.operation_sha256
        ):
            raise ContractValidationError("bound adult review operation changed")
        if (
            type(self.planner_provider_operations) is not int
            or self.planner_provider_operations < 0
        ):
            raise ContractValidationError("bound adult Planner count is invalid")
        if type(self.regenerate_available) is not bool:
            raise ContractValidationError("bound adult Regenerate state is invalid")


@dataclass(frozen=True, slots=True)
class AdultAutomaticRepairReviewRequiredV1:
    disposition: AdultAutomaticRepairDisposition
    review: AdultRejectedReviewSafeSummaryV1

    def __post_init__(self) -> None:
        if self.disposition not in {
            AdultAutomaticRepairDisposition.CREATOR_REVIEW_REQUIRED,
            AdultAutomaticRepairDisposition.REPAIR_LIMIT_REACHED,
        }:
            raise ContractValidationError("adult automatic-repair disposition is invalid")


type AdultAutomaticRepairServiceResultV1 = (
    AdultRejectedReviewActionV1 | AdultAutomaticRepairReviewRequiredV1
)


class AdultFullModelReviewService:
    """Protected review lifecycle plus exact accepted-adult Regenerate."""

    def __init__(
        self,
        *,
        scene_store: LeanSceneStore,
        operation_store: ProtectedAdultOperationStore,
    ) -> None:
        self.scene_store = scene_store
        self.operation_store = operation_store
        self.operations = ProtectedAdultOperationController(operation_store)
        self.reviews = AdultRejectedReviewController(operation_store)
        self.root = (operation_store.root / "FULL_MODEL_REVIEW").resolve()
        if not self.root.is_relative_to(operation_store.root):
            raise ContractValidationError("adult review service escaped protected runtime")
        self.capsules_root = self.root / "CAPSULES"
        self.bindings_root = self.root / "SAFE_REVIEW_BINDINGS"
        # Keep protected action custody below Windows' legacy path ceiling even
        # when the isolated runtime itself has a moderately long root.
        self.accepted_actions_root = self.root / "ACCEPTED_REGEN"

    def bind_prepared_turn(
        self,
        *,
        prepared: PreparedAdultRouteOperationV1,
        turn_input: LeanSceneTurnInputV1,
    ) -> ProtectedAdultTurnCapsuleV1:
        """Persist exact pre-turn authority before the execution callback."""

        _validate_turn_preparation(turn_input, prepared)
        capsule = ProtectedAdultTurnCapsuleV1.create(
            operation_sha256=prepared.operation_sha256,
            turn_input=turn_input,
        )
        path = self._capsule_path(prepared.operation_sha256)
        self._write_or_verify(path, capsule, ProtectedAdultTurnCapsuleV1)
        return self._load_capsule(prepared.operation_sha256)

    def register_rejection(
        self,
        *,
        outcome: RejectedAdultRouteOperationV1,
        turn_input: LeanSceneTurnInputV1,
        planner_provider_operations: int,
        replacement_base: LeanAcceptedRegenerationBaseV1 | None = None,
    ) -> AdultRejectedReviewSafeSummaryV1:
        capsule = self.bind_prepared_turn(
            prepared=outcome.prepared,
            turn_input=turn_input,
        )
        review = self.reviews.safe_review(
            request_id=outcome.prepared.request_id,
            candidate_id=outcome.prepared.candidate_id,
        )
        binding = AdultReviewBindingV1.create(
            review=review,
            turn_input=turn_input,
            capsule=capsule,
            planner_provider_operations=planner_provider_operations,
            replacement_base=replacement_base,
        )
        self._write_or_verify(
            self._review_binding_path(review.review_id),
            binding,
            AdultReviewBindingV1,
        )
        self._write_or_verify(
            self._public_review_binding_path(binding.public_review_id),
            binding,
            AdultReviewBindingV1,
        )
        return self.get_review(binding.public_review_id)

    def public_review_id(self, review_id: str) -> str:
        """Project protected or public identity to the stable HTTP review ID."""

        return self._load_review_binding(review_id).public_review_id

    def has_public_review(self, review_id: str) -> bool:
        """Return whether an exact public identity has valid adult custody.

        Existing malformed or tampered custody remains an error rather than
        falling through to the unrelated ordinary review store.
        """

        if _PUBLIC_REVIEW_ID.fullmatch(review_id) is None:
            return False
        path = self._public_review_binding_path(review_id)
        if not path.exists():
            return False
        self._load_review_binding(review_id)
        return True

    def regenerate_available(self, review_id: str) -> bool:
        """Return whether one independent successor can still be dispatched."""

        binding = self._load_review_binding(review_id)
        record = self.operation_store.lookup(
            request_id=binding.request_id,
            candidate_id=binding.candidate_id,
        )
        return (
            record.state is AdultOperationReviewState.EXECUTED_REJECTED
            and record.repair is None
            and record.predecessor_repair is None
        )

    def bound_rejection(self, review_id: str) -> AdultBoundRejectedReviewV1:
        """Load exact rejected custody for the trusted full-model controller."""

        binding = self._load_review_binding(review_id)
        review = self.get_review(review_id)
        record = self.operation_store.lookup(
            request_id=binding.request_id,
            candidate_id=binding.candidate_id,
        )
        if not isinstance(record.outcome, RejectedAdultRouteOperationV1):
            raise StateConflictError("adult review no longer binds a rejected outcome")
        return AdultBoundRejectedReviewV1(
            outcome=record.outcome,
            review=review,
            public_review_id=binding.public_review_id,
            planner_provider_operations=binding.planner_provider_operations,
            regenerate_available=self.regenerate_available(review_id),
        )

    def get_review(self, review_id: str) -> AdultRejectedReviewSafeSummaryV1:
        binding = self._load_review_binding(review_id)
        review = self.reviews.safe_review(
            request_id=binding.request_id,
            candidate_id=binding.candidate_id,
        )
        if (
            review.review_id != binding.review_id
            or review.review_sha256 != binding.review_sha256
            or review.operation.operation_sha256 != binding.operation_sha256
        ):
            raise StateConflictError("adult review binding differs from protected custody")
        capsule = self._load_capsule(binding.operation_sha256)
        if capsule.capsule_sha256 != binding.capsule_sha256:
            raise StateConflictError("adult review turn capsule changed")
        return review

    def decline(self, review_id: str) -> AdultRejectedReviewSafeSummaryV1:
        binding = self._load_review_binding(review_id)
        result = self.reviews.decline(
            review_id=binding.review_id,
            request_id=binding.request_id,
            candidate_id=binding.candidate_id,
        )
        if result.review_id != binding.review_id:
            raise StateConflictError("adult Decline changed review identity")
        return result

    def provisional_acceptance(
        self,
        review_id: str,
    ) -> AdultProvisionalAcceptanceBlockedV1:
        binding = self._load_review_binding(review_id)
        return self.reviews.provisional_acceptance_disposition(
            review_id=binding.review_id,
            request_id=binding.request_id,
            candidate_id=binding.candidate_id,
        )

    def regenerate_rejected(
        self,
        review_id: str,
        *,
        execute: ProtectedAdultExecutor,
    ) -> AdultRejectedReviewActionV1:
        binding = self._load_review_binding(review_id)
        capsule = self._load_capsule(binding.operation_sha256)

        def execute_bound(
            prepared: PreparedAdultRouteOperationV1,
        ) -> AdultRouteOperationOutcomeV1:
            self.bind_prepared_turn(prepared=prepared, turn_input=capsule.turn_input)
            return execute(prepared, capsule.turn_input)

        resolution = self.reviews.regenerate(
            review_id=binding.review_id,
            request_id=binding.request_id,
            candidate_id=binding.candidate_id,
            execute=execute_bound,
        )
        successor = self.operation_store.lookup(
            request_id=resolution.successor_operation.request_id,
            candidate_id=resolution.successor_operation.candidate_id,
        )
        outcome = successor.outcome
        if outcome is None:
            raise StateConflictError("adult Regenerate lost its protected outcome")
        if isinstance(outcome, RejectedAdultRouteOperationV1):
            review = self.register_rejection(
                outcome=outcome,
                turn_input=capsule.turn_input,
                planner_provider_operations=0,
                replacement_base=binding.replacement_base,
            )
            return AdultRejectedReviewActionV1(
                action="regenerate",
                predecessor_review_id=binding.review_id,
                operation=review.operation,
                review=review,
                accepted=None,
                replayed=resolution.replayed,
            )
        accepted = self._promote_passed(
            outcome,
            turn_input=capsule.turn_input,
            replacement_base=binding.replacement_base,
            replayed=resolution.replayed,
        )
        return AdultRejectedReviewActionV1(
            action="regenerate",
            predecessor_review_id=binding.review_id,
            operation=accepted.operation,
            review=None,
            accepted=accepted,
            replayed=resolution.replayed,
        )

    def automatic_repair_if_critical(
        self,
        review_id: str,
        *,
        execute: ProtectedAdultExecutor,
    ) -> AdultAutomaticRepairServiceResultV1:
        binding = self._load_review_binding(review_id)
        current = self.get_review(review_id)
        record = self.operation_store.lookup(
            request_id=binding.request_id,
            candidate_id=binding.candidate_id,
        )
        if record.predecessor_repair is not None or record.repair is not None:
            return AdultAutomaticRepairReviewRequiredV1(
                disposition=AdultAutomaticRepairDisposition.REPAIR_LIMIT_REACHED,
                review=current,
            )
        if not current.automatic_repair_eligible:
            return AdultAutomaticRepairReviewRequiredV1(
                disposition=AdultAutomaticRepairDisposition.CREATOR_REVIEW_REQUIRED,
                review=current,
            )
        regenerated = self.regenerate_rejected(review_id, execute=execute)
        return AdultRejectedReviewActionV1(
            action="automatic_repair",
            predecessor_review_id=regenerated.predecessor_review_id,
            operation=regenerated.operation,
            review=regenerated.review,
            accepted=regenerated.accepted,
            replayed=regenerated.replayed,
        )

    def regenerate_accepted(
        self,
        *,
        action_request_id: str,
        world_id: str,
        branch_id: str,
        execute: ProtectedAdultExecutor,
    ) -> AdultPromotedReviewOutcomeV1 | AdultRejectedReviewSafeSummaryV1:
        """Replace the selected adult turn from its exact protected snapshot."""

        action_path = self._accepted_action_path(action_request_id)
        if action_path.exists():
            action = cast(
                AcceptedAdultRegenerateBindingV1,
                self._load(action_path, AcceptedAdultRegenerateBindingV1),
            )
            if (
                action.replacement_base.world_id != world_id
                or action.replacement_base.branch_id != branch_id
            ):
                raise StateConflictError("accepted adult Regenerate action changed branch")
            predecessor = self.operation_store.lookup(
                request_id=action.predecessor_request_id,
                candidate_id=action.predecessor_candidate_id,
            )
            if predecessor.prepared.operation_sha256 != action.predecessor_operation_sha256:
                raise StateConflictError("accepted adult Regenerate operation changed")
            capsule = self._load_capsule(predecessor.prepared.operation_sha256)
            if capsule.capsule_sha256 != action.predecessor_capsule_sha256:
                raise StateConflictError("accepted adult Regenerate capsule changed")
            base = action.replacement_base
            candidate_id = action.candidate_id
        else:
            head = self.scene_store.load_head(world_id=world_id, branch_id=branch_id)
            receipt = head.receipt
            if receipt is None or receipt.route is not SceneRoute.ADULT:
                raise StateConflictError("accepted adult Regenerate requires an adult head")
            original, _ = self.scene_store.load_promoted_adult_acceptance(
                world_id=world_id,
                branch_id=branch_id,
                accepted_turn_id=receipt.accepted_turn_id,
            )
            predecessor = self.operation_store.lookup(
                request_id=original.request_id,
                candidate_id=original.candidate_id,
            )
            if (
                not isinstance(predecessor.outcome, PassedAdultRouteOperationV1)
                or predecessor.state is not AdultOperationReviewState.ACCEPTED
            ):
                raise StateConflictError("selected adult turn lacks accepted operation custody")
            capsule = self._load_capsule(predecessor.prepared.operation_sha256)
            _validate_original_acceptance(
                original,
                predecessor.prepared,
                capsule.turn_input,
            )
            base = self.scene_store.regeneration_base(receipt)
            digest = domain_sha256(
                _ACCEPTED_REGENERATE_DOMAIN,
                {
                    "action_request_id": action_request_id,
                    "base_binding_sha256": base.binding_sha256,
                    "predecessor_operation_sha256": (predecessor.prepared.operation_sha256),
                },
            )
            candidate_id = f"candidate:adult-regenerate:{digest[:32]}"
            action = AcceptedAdultRegenerateBindingV1.create(
                action_request_id=action_request_id,
                candidate_id=candidate_id,
                predecessor_request_id=predecessor.prepared.request_id,
                predecessor_candidate_id=predecessor.prepared.candidate_id,
                predecessor_operation_sha256=predecessor.prepared.operation_sha256,
                predecessor_capsule_sha256=capsule.capsule_sha256,
                replacement_base=base,
            )
            self._write_or_verify(
                action_path,
                action,
                AcceptedAdultRegenerateBindingV1,
            )
        prepared = PreparedAdultRouteOperationV1.create(
            request_id=action_request_id,
            candidate_id=candidate_id,
            route_state=predecessor.prepared.route_state,
            scene_request=predecessor.prepared.scene_request,
        )
        begin = self.operations.recover_or_prepare(
            request_id=action_request_id,
            candidate_id=candidate_id,
            prepare=lambda: prepared,
        )
        self.bind_prepared_turn(prepared=prepared, turn_input=capsule.turn_input)
        resolution = self.operations.execute_new(
            begin,
            execute=lambda value: execute(value, capsule.turn_input),
        )
        outcome = resolution.record.outcome
        if outcome is None:
            raise StateConflictError("accepted adult Regenerate lost its outcome")
        if isinstance(outcome, RejectedAdultRouteOperationV1):
            return self.register_rejection(
                outcome=outcome,
                turn_input=capsule.turn_input,
                planner_provider_operations=0,
                replacement_base=base,
            )
        return self._promote_passed(
            outcome,
            turn_input=capsule.turn_input,
            replacement_base=base,
            replayed=resolution.replayed,
        )

    def _promote_passed(
        self,
        outcome: PassedAdultRouteOperationV1,
        *,
        turn_input: LeanSceneTurnInputV1,
        replacement_base: LeanAcceptedRegenerationBaseV1 | None,
        replayed: bool,
    ) -> AdultPromotedReviewOutcomeV1:
        existing: tuple[AdultAcceptedTurnEnvelopeV1, BoundAdultPromotionV1] | None
        try:
            existing = self.scene_store.load_promoted_adult_acceptance(
                world_id=turn_input.world_id,
                branch_id=turn_input.branch_id,
                request_id=outcome.prepared.request_id,
            )
        except StateConflictError as exc:
            if exc.args != ("adult promotion identity is not uniquely stored",):
                raise
            existing = None
        if existing is not None:
            envelope, promotion = existing
            reconstructed = outcome.protected_execution.acceptance_envelope(
                accepted_turn_id=envelope.accepted_turn_id,
                parent_accepted_turn_id=envelope.parent_accepted_turn_id,
                scene_id=envelope.scene_id,
                generation=envelope.generation,
                creator_action=envelope.creator_action,
            )
            if reconstructed != envelope or promotion.bundle != envelope.promotion_bundle:
                raise StateConflictError("stored adult reviewed promotion changed")
            if replacement_base is None:
                replaced_receipt_sha256 = None
            else:
                if (
                    envelope.accepted_turn_id != replacement_base.replaced_turn_id
                    or envelope.generation != replacement_base.generation
                    or envelope.parent_accepted_turn_id != replacement_base.parent_accepted_turn_id
                    or envelope.parent_accepted_head_sha256
                    != replacement_base.parent_accepted_head_sha256
                ):
                    raise StateConflictError("stored adult replacement changed its base")
                replaced_receipt_sha256 = replacement_base.replaced_receipt_sha256
            self.operation_store.mark_accepted(
                request_id=outcome.prepared.request_id,
                candidate_id=outcome.prepared.candidate_id,
                decision=_accept_decision(
                    operation_sha256=outcome.prepared.operation_sha256,
                    promotion=promotion,
                ),
            )
            summary = self.operation_store.safe_summary(
                request_id=outcome.prepared.request_id,
                candidate_id=outcome.prepared.candidate_id,
            )
            return AdultPromotedReviewOutcomeV1(
                envelope=envelope,
                promotion=promotion,
                operation=summary,
                replaced_receipt_sha256=replaced_receipt_sha256,
                replayed=True,
            )
        if replacement_base is None:
            head = self.scene_store.load_head(
                world_id=turn_input.world_id,
                branch_id=turn_input.branch_id,
            )
            if head.accepted_head_sha256 != outcome.prepared.route_state.accepted_head_sha256:
                raise StateConflictError("adult reviewed acceptance lost its pre-turn head")
            generation = head.generation + 1
            accepted_turn_id = (
                f"turn-{generation:04d}-{text_sha256(turn_input.exact_user_source)[:12]}"
            )
            parent_turn_id = head.accepted_turn_id
        else:
            generation = replacement_base.generation
            accepted_turn_id = replacement_base.replaced_turn_id
            parent_turn_id = replacement_base.parent_accepted_turn_id
        envelope = outcome.protected_execution.acceptance_envelope(
            accepted_turn_id=accepted_turn_id,
            parent_accepted_turn_id=parent_turn_id,
            scene_id=turn_input.scene_id,
            generation=generation,
        )
        if replacement_base is None:
            promotion = self.scene_store.promote_adult_acceptance_envelope(envelope)
            replaced_receipt_sha256 = None
        else:
            promotion = self.scene_store.promote_adult_replacement_envelope(
                envelope,
                base=replacement_base,
            )
            replaced_receipt_sha256 = replacement_base.replaced_receipt_sha256
        self.operation_store.mark_accepted(
            request_id=outcome.prepared.request_id,
            candidate_id=outcome.prepared.candidate_id,
            decision=_accept_decision(
                operation_sha256=outcome.prepared.operation_sha256,
                promotion=promotion,
            ),
        )
        summary = self.operation_store.safe_summary(
            request_id=outcome.prepared.request_id,
            candidate_id=outcome.prepared.candidate_id,
        )
        return AdultPromotedReviewOutcomeV1(
            envelope=envelope,
            promotion=promotion,
            operation=summary,
            replaced_receipt_sha256=replaced_receipt_sha256,
            replayed=replayed,
        )

    def _capsule_path(self, operation_sha256: str) -> Path:
        _sha(operation_sha256, "adult capsule path")
        return self.capsules_root / f"capsule-{operation_sha256}.json"

    def _review_binding_path(self, review_id: str) -> Path:
        match = _REVIEW_ID.fullmatch(review_id)
        if match is None:
            raise ContractValidationError("adult review identity is invalid")
        return self.bindings_root / f"review-{match.group(1)}.json"

    def _public_review_binding_path(self, review_id: str) -> Path:
        match = _PUBLIC_REVIEW_ID.fullmatch(review_id)
        if match is None:
            raise ContractValidationError("adult public review identity is invalid")
        return self.bindings_root / f"public-review-{match.group(1)}.json"

    def _accepted_action_path(self, action_request_id: str) -> Path:
        if type(action_request_id) is not str or not action_request_id.strip():
            raise ContractValidationError("accepted adult Regenerate request is empty")
        return self.accepted_actions_root / f"action-{text_sha256(action_request_id)}.json"

    def _load_capsule(self, operation_sha256: str) -> ProtectedAdultTurnCapsuleV1:
        return cast(
            ProtectedAdultTurnCapsuleV1,
            self._load(self._capsule_path(operation_sha256), ProtectedAdultTurnCapsuleV1),
        )

    def _load_review_binding(self, review_id: str) -> AdultReviewBindingV1:
        if _REVIEW_ID.fullmatch(review_id) is not None:
            path = self._review_binding_path(review_id)
        elif _PUBLIC_REVIEW_ID.fullmatch(review_id) is not None:
            path = self._public_review_binding_path(review_id)
        else:
            raise ContractValidationError("adult review identity is invalid")
        binding = cast(
            AdultReviewBindingV1,
            self._load(path, AdultReviewBindingV1),
        )
        if review_id not in {binding.review_id, binding.public_review_id}:
            raise StateConflictError("adult review lookup identity changed")
        return binding

    @staticmethod
    def _load(path: Path, model: type[object]) -> object:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise StateConflictError("adult review custody does not exist") from exc
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise StateConflictError("adult review custody is unreadable") from exc
        if not isinstance(value, Mapping):
            raise StateConflictError("adult review custody shape changed")
        try:
            return from_mapping(model, value)
        except (ContractValidationError, TypeError, ValueError) as exc:
            raise StateConflictError("adult review custody is invalid") from exc

    @classmethod
    def _write_or_verify(cls, path: Path, value: object, model: type[object]) -> None:
        if path.exists():
            if canonical_sha256(cls._load(path, model)) != canonical_sha256(value):
                raise StateConflictError("adult review custody changed on replay")
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.parent / f".{path.name}.{uuid4().hex}.tmp"
        payload = canonical_json(to_primitive(value)) + "\n"
        try:
            with temporary.open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if temporary.exists():
                temporary.unlink()
        if canonical_sha256(cls._load(path, model)) != canonical_sha256(value):
            raise StateConflictError("adult review custody read-back changed")


def _capsule_sha256(
    *,
    operation_sha256: str,
    turn_input: LeanSceneTurnInputV1,
) -> str:
    return domain_sha256(
        _CAPSULE_DOMAIN,
        {
            "operation_sha256": operation_sha256,
            "turn_input": turn_input,
        },
    )


def _review_binding_sha256(binding: AdultReviewBindingV1) -> str:
    return domain_sha256(
        _REVIEW_BINDING_DOMAIN,
        {
            "schema_version": binding.schema_version,
            "review_id": binding.review_id,
            "public_review_id": binding.public_review_id,
            "review_sha256": binding.review_sha256,
            "request_id": binding.request_id,
            "candidate_id": binding.candidate_id,
            "operation_sha256": binding.operation_sha256,
            "world_id": binding.world_id,
            "branch_id": binding.branch_id,
            "scene_id": binding.scene_id,
            "planner_provider_operations": binding.planner_provider_operations,
            "capsule_sha256": binding.capsule_sha256,
            "replacement_base": binding.replacement_base,
        },
    )


def _public_review_id(review_sha256: str) -> str:
    _sha(review_sha256, "adult public review source")
    return f"review-{review_sha256[:28]}"


def _accepted_regenerate_binding_sha256(
    binding: AcceptedAdultRegenerateBindingV1,
) -> str:
    return domain_sha256(
        _ACCEPTED_REGENERATE_DOMAIN,
        {
            "schema_version": binding.schema_version,
            "action_request_id": binding.action_request_id,
            "candidate_id": binding.candidate_id,
            "predecessor_request_id": binding.predecessor_request_id,
            "predecessor_candidate_id": binding.predecessor_candidate_id,
            "predecessor_operation_sha256": binding.predecessor_operation_sha256,
            "predecessor_capsule_sha256": binding.predecessor_capsule_sha256,
            "replacement_base": binding.replacement_base,
        },
    )


def _accept_decision(
    *,
    operation_sha256: str,
    promotion: BoundAdultPromotionV1,
) -> AdultOperationDecisionV1:
    accepted_binding_sha256 = canonical_sha256(promotion)
    request_sha256 = domain_sha256(
        _ACCEPT_DECISION_DOMAIN,
        {
            "operation_sha256": operation_sha256,
            "action": AdultOperationDecisionAction.ACCEPT.value,
            "accepted_binding_sha256": accepted_binding_sha256,
        },
    )
    return AdultOperationDecisionV1(
        schema_version=AdultOperationDecisionV1.SCHEMA_VERSION,
        action=AdultOperationDecisionAction.ACCEPT,
        decision_id=f"adult_decision:{request_sha256[:32]}",
        decision_request_sha256=request_sha256,
        accepted_binding_sha256=accepted_binding_sha256,
    )


def _validate_turn_preparation(
    turn: LeanSceneTurnInputV1,
    prepared: PreparedAdultRouteOperationV1,
) -> None:
    if (
        turn.world_id != prepared.route_state.world_id
        or turn.branch_id != prepared.route_state.branch_id
        or turn.exact_user_source != prepared.scene_request.exact_current_source
    ):
        raise StateConflictError("adult turn capsule differs from prepared authority")


def _validate_original_acceptance(
    envelope: AdultAcceptedTurnEnvelopeV1,
    prepared: PreparedAdultRouteOperationV1,
    turn: LeanSceneTurnInputV1,
) -> None:
    _validate_turn_preparation(turn, prepared)
    if (
        envelope.request_id != prepared.request_id
        or envelope.candidate_id != prepared.candidate_id
        or envelope.scene_id != turn.scene_id
        or envelope.exact_current_source != turn.exact_user_source
        or envelope.primary_handoff_json != canonical_json(prepared.scene_request)
        or envelope.parent_accepted_head_sha256 != prepared.route_state.accepted_head_sha256
    ):
        raise StateConflictError("accepted adult Regenerate custody changed")


def _sha(value: str, label: str) -> None:
    if type(value) is not str or not re_is_sha256(value):
        raise ContractValidationError(f"{label} must be SHA-256")
