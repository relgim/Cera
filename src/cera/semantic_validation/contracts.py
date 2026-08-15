"""Provider-neutral contracts for ordinary candidate semantic validation.

The validator judges whether prose realizes an already-authorized cognition
plan.  It cannot replace the plan, rewrite prose, or author persistence state.
All branch and candidate custody is attached separately by Python.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar

from cera.cognition.contracts import CognitionPlanV1
from cera.errors import ContractValidationError
from cera.serialization import canonical_sha256, re_is_sha256, text_sha256

_IDENTITY = re.compile(r"[a-z][a-z0-9_.:-]{0,191}\Z")
_LOCAL_KEY = re.compile(r"[a-z][a-z0-9_]{0,95}\Z")


def _text(value: str, field: str, *, maximum: int) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ContractValidationError(f"{field} must be non-empty and bounded")


def _identity(value: str, field: str) -> None:
    if not isinstance(value, str) or _IDENTITY.fullmatch(value) is None:
        raise ContractValidationError(f"{field} must be a stable identity")


def _local_key(value: str, field: str) -> None:
    if not isinstance(value, str) or _LOCAL_KEY.fullmatch(value) is None:
        raise ContractValidationError(f"{field} must be a local key")


def _unique(values: tuple[str, ...], field: str) -> None:
    if len(values) != len(set(values)):
        raise ContractValidationError(f"{field} contains duplicates")


class SemanticVerdict(StrEnum):
    PASS = "pass"
    REJECT = "reject"


class SemanticConflictClass(StrEnum):
    OMITTED_DECISION = "omitted_decision"
    CONTRADICTED_DECISION = "contradicted_decision"
    LOCKED_FACT_CONFLICT = "locked_fact_conflict"
    UNAUTHORIZED_CONSEQUENCE = "unauthorized_consequence"
    KNOWLEDGE_VIOLATION = "knowledge_violation"
    PRESENCE_VIOLATION = "presence_violation"
    PROTECTED_USER_DIALOGUE = "protected_user_dialogue"
    PROTECTED_USER_PRIVATE_STATE = "protected_user_private_state"
    PROTECTED_BOUNDARY_CONFLICT = "protected_boundary_conflict"
    STOPPING_BOUNDARY = "stopping_boundary"
    SEVERE_INCOMPLETENESS = "severe_incompleteness"
    AUTHORITY_AMBIGUITY = "authority_ambiguity"
    CAPABILITY_RESTRICTION = "capability_restriction"


_AUTOMATIC_REPAIRABLE = frozenset(
    {
        SemanticConflictClass.OMITTED_DECISION,
        SemanticConflictClass.CONTRADICTED_DECISION,
        SemanticConflictClass.LOCKED_FACT_CONFLICT,
        SemanticConflictClass.UNAUTHORIZED_CONSEQUENCE,
        SemanticConflictClass.KNOWLEDGE_VIOLATION,
        SemanticConflictClass.PRESENCE_VIOLATION,
        SemanticConflictClass.PROTECTED_USER_DIALOGUE,
        SemanticConflictClass.PROTECTED_USER_PRIVATE_STATE,
        SemanticConflictClass.PROTECTED_BOUNDARY_CONFLICT,
        SemanticConflictClass.STOPPING_BOUNDARY,
        SemanticConflictClass.SEVERE_INCOMPLETENESS,
    }
)


@dataclass(frozen=True, slots=True)
class ValidationEvidenceV1:
    evidence_ref: str
    concise_authoritative_fact: str

    def __post_init__(self) -> None:
        _identity(self.evidence_ref, "validation_evidence.evidence_ref")
        _text(
            self.concise_authoritative_fact,
            "validation_evidence.concise_authoritative_fact",
            maximum=4_000,
        )


@dataclass(frozen=True, slots=True)
class SemanticReviewFlagV1:
    flag_code: str
    concise_explanation: str

    def __post_init__(self) -> None:
        _local_key(self.flag_code, "semantic_review_flag.flag_code")
        _text(
            self.concise_explanation,
            "semantic_review_flag.concise_explanation",
            maximum=1_000,
        )


@dataclass(frozen=True, slots=True)
class SemanticConflictV1:
    conflict_class: SemanticConflictClass
    concise_explanation: str
    exact_quote: str | None
    decision_key: str | None

    def __post_init__(self) -> None:
        _text(
            self.concise_explanation,
            "semantic_conflict.concise_explanation",
            maximum=2_000,
        )
        if self.exact_quote is not None:
            _text(self.exact_quote, "semantic_conflict.exact_quote", maximum=1_000)
        if self.decision_key is not None:
            _local_key(self.decision_key, "semantic_conflict.decision_key")
        if self.exact_quote is None and self.decision_key is None:
            raise ContractValidationError(
                "semantic conflict requires an exact quote or decision key"
            )


@dataclass(frozen=True, slots=True)
class SemanticValidationVerdictV1:
    """Closed provider wire.  It contains no candidate or branch custody."""

    SCHEMA_VERSION: ClassVar[str] = "cera.semantic_validation.verdict.v1"

    schema_version: str
    verdict: SemanticVerdict
    conflict: SemanticConflictV1 | None
    review_flags: tuple[SemanticReviewFlagV1, ...] = ()

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("semantic validation schema changed")
        if self.verdict is SemanticVerdict.PASS:
            if self.conflict is not None:
                raise ContractValidationError("passing verdict cannot contain a conflict")
        elif self.conflict is None:
            raise ContractValidationError("rejecting verdict requires one conflict")
        codes = tuple(flag.flag_code for flag in self.review_flags)
        _unique(codes, "semantic_validation.review_flags")

    @property
    def automatic_repair_eligible(self) -> bool:
        return (
            self.verdict is SemanticVerdict.REJECT
            and self.conflict is not None
            and self.conflict.conflict_class in _AUTOMATIC_REPAIRABLE
        )


@dataclass(frozen=True, slots=True)
class SemanticValidationRequestV1:
    """Candidate-specific semantic input visible to the Luna role."""

    SCHEMA_VERSION: ClassVar[str] = "cera.semantic_validation.request.v1"

    schema_version: str
    cognition_plan: CognitionPlanV1
    exact_current_source: str
    exact_candidate_prose: str
    current_public_state: str
    hard_boundaries: tuple[str, ...]
    selected_evidence: tuple[ValidationEvidenceV1, ...]

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("semantic validation request schema changed")
        _text(
            self.exact_current_source,
            "semantic_validation_request.exact_current_source",
            maximum=100_000,
        )
        _text(
            self.exact_candidate_prose,
            "semantic_validation_request.exact_candidate_prose",
            maximum=100_000,
        )
        _text(
            self.current_public_state,
            "semantic_validation_request.current_public_state",
            maximum=20_000,
        )
        _unique(self.hard_boundaries, "semantic_validation_request.hard_boundaries")
        for boundary in self.hard_boundaries:
            _text(boundary, "semantic_validation_request.hard_boundaries", maximum=2_000)
        refs = tuple(value.evidence_ref for value in self.selected_evidence)
        _unique(refs, "semantic_validation_request.selected_evidence")


@dataclass(frozen=True, slots=True)
class SemanticValidationCustodyV1:
    """Python-only binding; never requested from the validator."""

    SCHEMA_VERSION: ClassVar[str] = "cera.semantic_validation.custody.v1"

    request_id: str
    candidate_id: str
    world_id: str
    branch_id: str
    accepted_head_sha256: str | None
    cognition_plan_sha256: str
    candidate_prose_sha256: str
    validation_request_sha256: str

    def __post_init__(self) -> None:
        for field in ("request_id", "candidate_id", "world_id", "branch_id"):
            _identity(getattr(self, field), f"semantic_validation_custody.{field}")
        for field in (
            "accepted_head_sha256",
            "cognition_plan_sha256",
            "candidate_prose_sha256",
            "validation_request_sha256",
        ):
            value = getattr(self, field)
            if value is not None and not re_is_sha256(value):
                raise ContractValidationError(
                    f"semantic_validation_custody.{field} must be SHA-256"
                )


@dataclass(frozen=True, slots=True)
class BoundSemanticValidationV1:
    request: SemanticValidationRequestV1
    custody: SemanticValidationCustodyV1
    verdict: SemanticValidationVerdictV1

    def __post_init__(self) -> None:
        validate_semantic_validation_request_custody(self.request, self.custody)
        validate_semantic_validation_verdict_binding(self.request, self.verdict)

    @property
    def binding_sha256(self) -> str:
        return canonical_sha256(self)


def validate_semantic_validation_verdict_binding(
    request: SemanticValidationRequestV1,
    verdict: SemanticValidationVerdictV1,
) -> None:
    """Validate only provider-owned verdict references against the exact request."""

    if type(request) is not SemanticValidationRequestV1:
        raise ContractValidationError("validation request changed type")
    if type(verdict) is not SemanticValidationVerdictV1:
        raise ContractValidationError("validation verdict changed type")
    conflict = verdict.conflict
    if conflict is None:
        return
    if (
        conflict.exact_quote is not None
        and conflict.exact_quote not in request.exact_candidate_prose
    ):
        raise ContractValidationError("validation conflict quote is not exact")
    if conflict.decision_key is not None:
        decision_keys = {
            decision.decision_key for decision in request.cognition_plan.decision_records
        }
        if conflict.decision_key not in decision_keys:
            raise ContractValidationError("validation cites an unknown decision")


def validate_semantic_validation_request_custody(
    request: SemanticValidationRequestV1,
    custody: SemanticValidationCustodyV1,
) -> None:
    """Validate caller-owned custody before any provider lifecycle begins."""

    if type(request) is not SemanticValidationRequestV1:
        raise ContractValidationError("validation request changed type")
    if type(custody) is not SemanticValidationCustodyV1:
        raise ContractValidationError("validation custody changed type")
    if canonical_sha256(request.cognition_plan) != custody.cognition_plan_sha256:
        raise ContractValidationError("validation custody lost the cognition plan")
    if text_sha256(request.exact_candidate_prose) != custody.candidate_prose_sha256:
        raise ContractValidationError("validation custody lost the candidate prose")
    if canonical_sha256(request) != custody.validation_request_sha256:
        raise ContractValidationError("validation custody lost the exact request")
