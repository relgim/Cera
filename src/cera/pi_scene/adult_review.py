"""Creator-review lifecycle for protected adult Filter rejections.

The ordinary runtime may expose :class:`AdultRejectedReviewSafeSummaryV1`, but
the exact candidate can be recovered only through an injected local-creator
authorizer.  A rejected Filter result is deliberately not promotable: it lacks
the protected record, non-explicit projection, and route transition required by
the atomic adult-acceptance contract.

Regenerate is one complete, independent Scene+Filter successor.  It reuses the
byte-identical frozen Scene request, supplies no feedback, and never represents
continuation, patching, or candidate merging.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar, Protocol

from cera.adult_pipeline.contracts import AdultFilterConflictClass, AdultFilterConflictV1
from cera.errors import ContractValidationError, StateConflictError
from cera.serialization import domain_sha256, re_is_sha256, text_sha256

from .adult_operation_contracts import (
    AdultOperationDecisionAction,
    AdultOperationDecisionV1,
    AdultOperationReviewState,
    AdultOperationSafeSummaryV1,
    ProtectedAdultOperationRecordV1,
)
from .adult_operation_store import (
    ProtectedAdultOperationController,
    ProtectedAdultOperationStore,
)
from .adult_orchestration import (
    AdultRouteOperationOutcomeV1,
    PreparedAdultRouteOperationV1,
    RejectedAdultRouteOperationV1,
)

_REVIEW_DOMAIN = "cera.pi_scene.adult_rejected_review.v1"
_DECISION_DOMAIN = "cera.pi_scene.adult_rejected_review.decision.v1"
_REGENERATE_DOMAIN = "cera.pi_scene.adult_rejected_review.regenerate.v1"

# These classes prove a material accepted-authority or continuity conflict.
# Quality-only misses remain creator-review items and never trigger automation.
_CRITICAL_CONFLICTS = frozenset(
    {
        AdultFilterConflictClass.LOGIC_CONTRADICTION,
        AdultFilterConflictClass.CURRENT_DATA_CONFLICT,
        AdultFilterConflictClass.KNOWLEDGE_OR_PRIVACY_CONFLICT,
        AdultFilterConflictClass.UNSUPPORTED_DURABLE_EFFECT,
        AdultFilterConflictClass.ROUTE_TRANSITION_CONFLICT,
    }
)


class AdultConflictAnchorKind(StrEnum):
    DECISION_KEY = "decision_key"
    EXACT_QUOTE = "exact_quote"
    BOTH = "both"


class AdultProvisionalAcceptanceDisposition(StrEnum):
    REPROJECTION_REQUIRED = "reprojection_required"


class AdultAutomaticRepairDisposition(StrEnum):
    AUTO_REPAIRED = "auto_repaired"
    CREATOR_REVIEW_REQUIRED = "creator_review_required"
    REPAIR_LIMIT_REACHED = "repair_limit_reached"


class LocalAdultCreatorViewAuthorizer(Protocol):
    """External local-auth boundary; no permissive default is provided."""

    def authorize_protected_candidate(
        self,
        *,
        review_id: str,
        request_id: str,
        candidate_id: str,
    ) -> bool: ...


@dataclass(frozen=True, slots=True)
class AdultRejectedReviewSafeSummaryV1:
    """Non-explicit rejection summary suitable for ordinary diagnostics."""

    SCHEMA_VERSION: ClassVar[str] = "cera.pi_scene.adult_rejected_review_summary.v1"

    schema_version: str
    review_id: str
    review_sha256: str
    operation: AdultOperationSafeSummaryV1
    conflict_class: AdultFilterConflictClass
    conflict_anchor: AdultConflictAnchorKind
    automatic_repair_eligible: bool
    provisional_acceptance: AdultProvisionalAcceptanceDisposition

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("adult rejected review schema changed")
        _sha(self.review_sha256, "adult rejected review")
        if self.review_id != _review_id(self.review_sha256):
            raise ContractValidationError("adult rejected review identity changed")
        if type(self.automatic_repair_eligible) is not bool:
            raise ContractValidationError("adult automatic-repair eligibility must be boolean")
        if self.automatic_repair_eligible != (self.conflict_class in _CRITICAL_CONFLICTS):
            raise ContractValidationError("adult automatic-repair classification changed")
        if self.provisional_acceptance is not (
            AdultProvisionalAcceptanceDisposition.REPROJECTION_REQUIRED
        ):
            raise ContractValidationError("rejected adult review became directly promotable")


@dataclass(frozen=True, slots=True)
class ProtectedAdultCandidateViewV1:
    """Exact rejected candidate returned only after local creator authorization."""

    PROTECTION_SCOPE: ClassVar[str] = "local_creator_protected_view_only"

    review_id: str
    request_id: str
    candidate_id: str
    exact_story_prose: str
    exact_story_prose_sha256: str
    conflict: AdultFilterConflictV1

    def __post_init__(self) -> None:
        if not self.exact_story_prose:
            raise ContractValidationError("protected adult candidate prose is empty")
        if text_sha256(self.exact_story_prose) != self.exact_story_prose_sha256:
            raise ContractValidationError("protected adult candidate prose binding changed")


@dataclass(frozen=True, slots=True)
class AdultRegenerateResolutionV1:
    """Safe result of one complete fresh Scene+Filter successor."""

    predecessor_review_id: str
    successor_operation: AdultOperationSafeSummaryV1
    successor_rejected_review: AdultRejectedReviewSafeSummaryV1 | None
    replayed: bool

    def __post_init__(self) -> None:
        if type(self.replayed) is not bool:
            raise ContractValidationError("adult Regenerate replay flag must be boolean")
        if (
            self.successor_rejected_review is not None
            and self.successor_rejected_review.operation != self.successor_operation
        ):
            raise ContractValidationError("adult Regenerate successor review changed")


@dataclass(frozen=True, slots=True)
class AdultAutomaticRepairResolutionV1:
    disposition: AdultAutomaticRepairDisposition
    review: AdultRejectedReviewSafeSummaryV1
    regenerate: AdultRegenerateResolutionV1 | None

    def __post_init__(self) -> None:
        repaired = self.disposition is AdultAutomaticRepairDisposition.AUTO_REPAIRED
        if repaired != (self.regenerate is not None):
            raise ContractValidationError("adult automatic-repair result is inconsistent")


@dataclass(frozen=True, slots=True)
class AdultProvisionalAcceptanceBlockedV1:
    """Typed fail-closed result until the exact candidate is safely reprojected."""

    SCHEMA_VERSION: ClassVar[str] = "cera.pi_scene.adult_provisional_acceptance_blocked.v1"
    REQUIRED_ARTIFACTS: ClassVar[tuple[str, ...]] = (
        "protected_full_record",
        "non_explicit_codex_projection",
        "route_transition",
    )

    schema_version: str
    review_id: str
    disposition: AdultProvisionalAcceptanceDisposition
    reason_code: str
    required_artifacts: tuple[str, ...]
    accepted_effect_created: bool

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("adult provisional block schema changed")
        if self.disposition is not AdultProvisionalAcceptanceDisposition.REPROJECTION_REQUIRED:
            raise ContractValidationError("adult rejection bypassed safe reprojection")
        if self.reason_code != "filter_rejection_has_no_promotable_projection":
            raise ContractValidationError("adult provisional block reason changed")
        if self.required_artifacts != self.REQUIRED_ARTIFACTS:
            raise ContractValidationError("adult provisional required artifacts changed")
        if self.accepted_effect_created is not False:
            raise ContractValidationError("blocked adult provisional acceptance created effects")


class AdultRejectedReviewController:
    """Restart-safe review actions over :class:`ProtectedAdultOperationStore`."""

    def __init__(
        self,
        store: ProtectedAdultOperationStore,
        *,
        local_creator_authorizer: LocalAdultCreatorViewAuthorizer | None = None,
    ) -> None:
        self.store = store
        self.operations = ProtectedAdultOperationController(store)
        self.local_creator_authorizer = local_creator_authorizer

    def safe_review(
        self,
        *,
        request_id: str,
        candidate_id: str,
    ) -> AdultRejectedReviewSafeSummaryV1:
        record, rejected = self._rejected_record(
            request_id=request_id,
            candidate_id=candidate_id,
        )
        operation = self.store.safe_summary(
            request_id=request_id,
            candidate_id=candidate_id,
        )
        review_sha256 = _review_sha256(record, rejected)
        return AdultRejectedReviewSafeSummaryV1(
            schema_version=AdultRejectedReviewSafeSummaryV1.SCHEMA_VERSION,
            review_id=_review_id(review_sha256),
            review_sha256=review_sha256,
            operation=operation,
            conflict_class=rejected.conflict.conflict_class,
            conflict_anchor=_anchor_kind(rejected.conflict),
            automatic_repair_eligible=(rejected.conflict.conflict_class in _CRITICAL_CONFLICTS),
            provisional_acceptance=(AdultProvisionalAcceptanceDisposition.REPROJECTION_REQUIRED),
        )

    def protected_candidate(
        self,
        *,
        review_id: str,
        request_id: str,
        candidate_id: str,
    ) -> ProtectedAdultCandidateViewV1:
        review = self._bound_review(
            review_id=review_id,
            request_id=request_id,
            candidate_id=candidate_id,
        )
        authorizer = self.local_creator_authorizer
        if authorizer is None or not authorizer.authorize_protected_candidate(
            review_id=review.review_id,
            request_id=request_id,
            candidate_id=candidate_id,
        ):
            raise StateConflictError("protected adult candidate requires local creator access")
        _, rejected = self._rejected_record(
            request_id=request_id,
            candidate_id=candidate_id,
        )
        prose = rejected.protected_execution.result.scene.invocation.output.exact_story_prose
        return ProtectedAdultCandidateViewV1(
            review_id=review.review_id,
            request_id=request_id,
            candidate_id=candidate_id,
            exact_story_prose=prose,
            exact_story_prose_sha256=rejected.protected_exact_story_prose_sha256,
            conflict=rejected.conflict,
        )

    def decline(
        self,
        *,
        review_id: str,
        request_id: str,
        candidate_id: str,
    ) -> AdultRejectedReviewSafeSummaryV1:
        review = self._bound_review(
            review_id=review_id,
            request_id=request_id,
            candidate_id=candidate_id,
        )
        request_sha256 = domain_sha256(
            _DECISION_DOMAIN,
            {"review_sha256": review.review_sha256, "action": "decline"},
        )
        decision = AdultOperationDecisionV1(
            schema_version=AdultOperationDecisionV1.SCHEMA_VERSION,
            action=AdultOperationDecisionAction.DECLINE,
            decision_id=f"decision:adult-decline:{request_sha256[:24]}",
            decision_request_sha256=request_sha256,
            accepted_binding_sha256=None,
        )
        self.store.mark_declined(
            request_id=request_id,
            candidate_id=candidate_id,
            decision=decision,
        )
        return self.safe_review(request_id=request_id, candidate_id=candidate_id)

    def regenerate(
        self,
        *,
        review_id: str,
        request_id: str,
        candidate_id: str,
        execute: Callable[[PreparedAdultRouteOperationV1], AdultRouteOperationOutcomeV1],
    ) -> AdultRegenerateResolutionV1:
        review = self._bound_review(
            review_id=review_id,
            request_id=request_id,
            candidate_id=candidate_id,
        )
        record, _ = self._rejected_record(
            request_id=request_id,
            candidate_id=candidate_id,
        )
        if record.state is AdultOperationReviewState.DECLINED:
            raise StateConflictError("declined adult review cannot be regenerated")
        successor_candidate_id = adult_regenerate_candidate_id(review.review_id)
        successor = PreparedAdultRouteOperationV1.create(
            request_id=record.prepared.request_id,
            candidate_id=successor_candidate_id,
            route_state=record.prepared.route_state,
            scene_request=record.prepared.scene_request,
        )
        repair_request_sha256 = domain_sha256(
            _REGENERATE_DOMAIN,
            {
                "review_sha256": review.review_sha256,
                "successor_candidate_id": successor_candidate_id,
                "feedback": None,
            },
        )
        begin = self.store.begin_repair(
            request_id=request_id,
            candidate_id=candidate_id,
            successor=successor,
            repair_request_sha256=repair_request_sha256,
        )
        resolution = self.operations.execute_new(begin, execute=execute)
        successor_summary = self.store.safe_summary(
            request_id=request_id,
            candidate_id=successor_candidate_id,
        )
        successor_review = (
            self.safe_review(
                request_id=request_id,
                candidate_id=successor_candidate_id,
            )
            if isinstance(resolution.record.outcome, RejectedAdultRouteOperationV1)
            else None
        )
        return AdultRegenerateResolutionV1(
            predecessor_review_id=review.review_id,
            successor_operation=successor_summary,
            successor_rejected_review=successor_review,
            replayed=resolution.replayed,
        )

    def automatic_repair_if_critical(
        self,
        *,
        review_id: str,
        request_id: str,
        candidate_id: str,
        execute: Callable[[PreparedAdultRouteOperationV1], AdultRouteOperationOutcomeV1],
    ) -> AdultAutomaticRepairResolutionV1:
        review = self._bound_review(
            review_id=review_id,
            request_id=request_id,
            candidate_id=candidate_id,
        )
        record, rejected = self._rejected_record(
            request_id=request_id,
            candidate_id=candidate_id,
        )
        if record.predecessor_repair is not None or record.repair is not None:
            return AdultAutomaticRepairResolutionV1(
                disposition=AdultAutomaticRepairDisposition.REPAIR_LIMIT_REACHED,
                review=review,
                regenerate=None,
            )
        if rejected.conflict.conflict_class not in _CRITICAL_CONFLICTS:
            return AdultAutomaticRepairResolutionV1(
                disposition=AdultAutomaticRepairDisposition.CREATOR_REVIEW_REQUIRED,
                review=review,
                regenerate=None,
            )
        regenerated = self.regenerate(
            review_id=review_id,
            request_id=request_id,
            candidate_id=candidate_id,
            execute=execute,
        )
        return AdultAutomaticRepairResolutionV1(
            disposition=AdultAutomaticRepairDisposition.AUTO_REPAIRED,
            review=review,
            regenerate=regenerated,
        )

    def provisional_acceptance_disposition(
        self,
        *,
        review_id: str,
        request_id: str,
        candidate_id: str,
    ) -> AdultProvisionalAcceptanceBlockedV1:
        review = self._bound_review(
            review_id=review_id,
            request_id=request_id,
            candidate_id=candidate_id,
        )
        return AdultProvisionalAcceptanceBlockedV1(
            schema_version=AdultProvisionalAcceptanceBlockedV1.SCHEMA_VERSION,
            review_id=review.review_id,
            disposition=AdultProvisionalAcceptanceDisposition.REPROJECTION_REQUIRED,
            reason_code="filter_rejection_has_no_promotable_projection",
            required_artifacts=AdultProvisionalAcceptanceBlockedV1.REQUIRED_ARTIFACTS,
            accepted_effect_created=False,
        )

    def _bound_review(
        self,
        *,
        review_id: str,
        request_id: str,
        candidate_id: str,
    ) -> AdultRejectedReviewSafeSummaryV1:
        review = self.safe_review(request_id=request_id, candidate_id=candidate_id)
        if review.review_id != review_id:
            raise StateConflictError("adult rejected review identity is invalid")
        return review

    def _rejected_record(
        self,
        *,
        request_id: str,
        candidate_id: str,
    ) -> tuple[ProtectedAdultOperationRecordV1, RejectedAdultRouteOperationV1]:
        record = self.store.lookup(request_id=request_id, candidate_id=candidate_id)
        if not isinstance(record.outcome, RejectedAdultRouteOperationV1):
            raise StateConflictError("adult creator review requires an executed rejection")
        return record, record.outcome


def adult_regenerate_candidate_id(review_id: str) -> str:
    """Stable fresh identity makes Regenerate replay idempotent across restart."""

    digest = domain_sha256(_REGENERATE_DOMAIN, {"review_id": review_id})
    return f"candidate:adult-regenerate:{digest[:24]}"


def _review_sha256(
    record: ProtectedAdultOperationRecordV1,
    rejected: RejectedAdultRouteOperationV1,
) -> str:
    return domain_sha256(
        _REVIEW_DOMAIN,
        {
            "request_id": record.prepared.request_id,
            "candidate_id": record.prepared.candidate_id,
            "operation_sha256": record.prepared.operation_sha256,
            "outcome_sha256": rejected.outcome_sha256,
            "protected_exact_story_prose_sha256": (rejected.protected_exact_story_prose_sha256),
        },
    )


def _review_id(review_sha256: str) -> str:
    return f"adult-review:{review_sha256}"


def _anchor_kind(conflict: AdultFilterConflictV1) -> AdultConflictAnchorKind:
    if conflict.decision_key is not None and conflict.exact_quote is not None:
        return AdultConflictAnchorKind.BOTH
    if conflict.decision_key is not None:
        return AdultConflictAnchorKind.DECISION_KEY
    return AdultConflictAnchorKind.EXACT_QUOTE


def _sha(value: str, field: str) -> None:
    if type(value) is not str or not re_is_sha256(value):
        raise ContractValidationError(f"{field} must be SHA-256")
