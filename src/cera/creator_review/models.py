"""Branch-bound provisional display and creator-publication contracts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar

from cera.errors import ContractValidationError
from cera.ids import IdKind, TypedId, require_kind
from cera.schema import require_schema
from cera.serialization import domain_sha256, re_is_sha256, text_sha256


class CreatorReviewState(StrEnum):
    PROVISIONAL_VISIBLE = "provisional_visible"
    VERIFYING_AND_PREPARING = "verifying_and_preparing"
    REVIEW_READY = "review_ready"
    AWAITING_FEEDBACK = "awaiting_feedback"
    COMMITTING = "committing"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    ERROR = "error"


class CreatorReviewSeverity(StrEnum):
    GOOD = "good"
    CONCERN = "concern"
    CRITICAL = "critical"
    ERROR = "error"


class PublicationEligibility(StrEnum):
    ACCEPT_ALLOWED = "accept_allowed"
    ACCEPT_BLOCKED = "accept_blocked"


class ReviewIssueOwner(StrEnum):
    NONE = "none"
    USER_REQUEST = "user_request"
    RETRIEVAL = "retrieval"
    PYTHON = "python"
    REASONER = "reasoner"
    COMPOSER = "composer"
    VERIFIER = "verifier"
    PROMPT_MATERIAL = "prompt_material"
    ADULT_EXAMPLES = "adult_examples"
    MIXED = "mixed"
    HARD_BOUNDARY = "hard_boundary"


class CreatorReviewAction(StrEnum):
    ACCEPT = "accept"
    FALSE_POSITIVE = "false_positive"
    DEEPSEEK_REWRITE = "deepseek_rewrite"
    CODEX_REPLAN = "codex_replan"
    CORRECTION_ADJUSTMENT = "correction_adjustment"
    DECLINE = "decline"
    CANCEL = "cancel"


class CorrectionDiagnosticKind(StrEnum):
    PROSE_REALIZATION = "prose_realization"
    CAUSAL_LOGIC = "causal_logic"
    CORRECTION_ADJUSTMENT = "correction_adjustment"
    FALSE_POSITIVE = "false_positive"


@dataclass(frozen=True, slots=True)
class CreatorReviewAssessment:
    """Concise, privacy-safe Sol review shown to the creator."""

    SCHEMA_VERSION: ClassVar[str] = "cera.creator_review_assessment.v1"

    schema_version: str
    severity: CreatorReviewSeverity
    publication_eligibility: PublicationEligibility
    issue_owner: ReviewIssueOwner
    reason_codes: tuple[str, ...]
    creator_reason: str
    verifier_status: str

    def __post_init__(self) -> None:
        require_schema(self.schema_version, self.SCHEMA_VERSION, type(self).__name__)
        if not self.creator_reason.strip() or len(self.creator_reason) > 1_000:
            raise ContractValidationError(
                "creator review reason must be 1..1000 non-whitespace characters"
            )
        if any(not value.strip() or len(value) > 96 for value in self.reason_codes):
            raise ContractValidationError("creator review reason code is invalid")
        if len(self.reason_codes) != len(set(self.reason_codes)):
            raise ContractValidationError("creator review reason codes must be unique")
        if self.severity is CreatorReviewSeverity.GOOD:
            if self.issue_owner is not ReviewIssueOwner.NONE or self.reason_codes:
                raise ContractValidationError(
                    "good creator review cannot carry an issue owner or reason code"
                )
        elif self.issue_owner is ReviewIssueOwner.NONE:
            raise ContractValidationError(
                "non-good creator review requires a diagnostic issue owner"
            )
        if self.severity is CreatorReviewSeverity.ERROR and (
            self.publication_eligibility is not PublicationEligibility.ACCEPT_BLOCKED
        ):
            raise ContractValidationError("review errors must block publication")

    @property
    def assessment_sha256(self) -> str:
        return domain_sha256(self.SCHEMA_VERSION, self)


@dataclass(frozen=True, slots=True)
class PreparedPublicationPackage:
    """Validated pre-commit bindings; never authority until creator acceptance."""

    SCHEMA_VERSION: ClassVar[str] = "cera.prepared_publication_package.v1"

    schema_version: str
    package_id: TypedId
    world_id: TypedId
    branch_id: TypedId
    request_id: TypedId
    generation_id: TypedId
    expected_generation: int
    expected_head_artifact_id: TypedId | None
    source_sha256: str
    snapshot_token: TypedId
    sequence_plan_sha256: str
    candidate_sha256: str
    accepted_artifact_id: TypedId
    accepted_artifact_sha256: str
    verifier_receipt_sha256: str
    application_request_sha256: str
    live_result_sha256: str
    idempotency_key_sha256: str
    created_at: str
    expires_at: str
    story_state_committed: bool

    def __post_init__(self) -> None:
        require_schema(self.schema_version, self.SCHEMA_VERSION, type(self).__name__)
        require_kind(self.package_id, IdKind.PREPARED_PUBLICATION, "package_id")
        require_kind(self.world_id, IdKind.WORLD, "world_id")
        require_kind(self.branch_id, IdKind.BRANCH, "branch_id")
        require_kind(self.request_id, IdKind.REQUEST, "request_id")
        require_kind(self.generation_id, IdKind.GENERATION, "generation_id")
        require_kind(self.snapshot_token, IdKind.SNAPSHOT, "snapshot_token")
        require_kind(
            self.accepted_artifact_id, IdKind.ARTIFACT, "accepted_artifact_id"
        )
        if self.expected_head_artifact_id is not None:
            require_kind(
                self.expected_head_artifact_id,
                IdKind.ARTIFACT,
                "expected_head_artifact_id",
            )
        if self.expected_generation < 0:
            raise ContractValidationError("prepared generation cannot be negative")
        for value in (
            self.source_sha256,
            self.sequence_plan_sha256,
            self.candidate_sha256,
            self.accepted_artifact_sha256,
            self.verifier_receipt_sha256,
            self.application_request_sha256,
            self.live_result_sha256,
            self.idempotency_key_sha256,
        ):
            if not re_is_sha256(value):
                raise ContractValidationError("prepared package hash is invalid")
        if not self.created_at.strip() or not self.expires_at.strip():
            raise ContractValidationError("prepared package timestamps are required")
        if self.story_state_committed:
            raise ContractValidationError(
                "prepared publication package cannot claim a story commit"
            )

    @property
    def package_sha256(self) -> str:
        return domain_sha256(self.SCHEMA_VERSION, self)


@dataclass(frozen=True, slots=True)
class CreatorReviewRecord:
    """Durable local state for one unresolved or audited creator decision."""

    SCHEMA_VERSION: ClassVar[str] = "cera.creator_review_record.v1"

    schema_version: str
    review_id: TypedId
    world_id: TypedId
    branch_id: TypedId
    request_id: TypedId
    generation_id: TypedId
    expected_generation: int
    expected_head_artifact_id: TypedId | None
    source_sha256: str
    snapshot_token: TypedId
    sequence_plan_sha256: str
    sequence_beats: tuple[str, ...]
    candidate_sha256: str
    candidate_text_sha256: str
    candidate_text: str | None
    state: CreatorReviewState
    assessment: CreatorReviewAssessment | None
    prepared_package: PreparedPublicationPackage | None
    application_request_json: str | None
    provisional_candidate_json: str | None
    live_result_json: str | None
    creator_action: CreatorReviewAction | None
    creator_feedback: str | None
    creator_feedback_sha256: str | None
    created_at: str
    updated_at: str
    resolved_at: str | None

    def __post_init__(self) -> None:
        require_schema(self.schema_version, self.SCHEMA_VERSION, type(self).__name__)
        require_kind(self.review_id, IdKind.REVIEW_PACKET, "review_id")
        require_kind(self.world_id, IdKind.WORLD, "world_id")
        require_kind(self.branch_id, IdKind.BRANCH, "branch_id")
        require_kind(self.request_id, IdKind.REQUEST, "request_id")
        require_kind(self.generation_id, IdKind.GENERATION, "generation_id")
        require_kind(self.snapshot_token, IdKind.SNAPSHOT, "snapshot_token")
        if self.expected_head_artifact_id is not None:
            require_kind(
                self.expected_head_artifact_id,
                IdKind.ARTIFACT,
                "expected_head_artifact_id",
            )
        if self.expected_generation < 0 or not self.sequence_beats:
            raise ContractValidationError("creator review generation/beats are invalid")
        if any(not value.strip() for value in self.sequence_beats):
            raise ContractValidationError("creator review sequence beat is empty")
        for value in (
            self.source_sha256,
            self.sequence_plan_sha256,
            self.candidate_sha256,
            self.candidate_text_sha256,
        ):
            if not re_is_sha256(value):
                raise ContractValidationError("creator review binding hash is invalid")
        if self.candidate_text is not None and (
            not self.candidate_text.strip()
            or text_sha256(self.candidate_text) != self.candidate_text_sha256
        ):
            raise ContractValidationError("creator review candidate binding is invalid")
        if (self.creator_feedback is None) != (
            self.creator_feedback_sha256 is None
        ):
            raise ContractValidationError("creator feedback text/hash must be paired")
        if self.creator_feedback is not None and (
            not self.creator_feedback.strip()
            or text_sha256(self.creator_feedback) != self.creator_feedback_sha256
        ):
            raise ContractValidationError("creator feedback binding is invalid")
        if not self.created_at.strip() or not self.updated_at.strip():
            raise ContractValidationError("creator review timestamps are required")
        if self.state is CreatorReviewState.REVIEW_READY:
            if self.assessment is None:
                raise ContractValidationError(
                    "review-ready state requires an assessment"
                )
            if self.assessment.publication_eligibility is PublicationEligibility.ACCEPT_ALLOWED:
                if (
                    self.prepared_package is None
                    or self.live_result_json is None
                    or self.application_request_json is None
                ):
                    raise ContractValidationError(
                        "eligible review requires prepared publication data"
                    )
            elif self.prepared_package is not None or self.live_result_json is not None:
                raise ContractValidationError(
                    "blocked review cannot carry a publishable result"
                )
        if self.state in {CreatorReviewState.PROVISIONAL_VISIBLE, CreatorReviewState.VERIFYING_AND_PREPARING}:
            if (
                self.candidate_text is None
                or self.provisional_candidate_json is None
                or self.creator_action is not None
            ):
                raise ContractValidationError(
                    "unresolved provisional state requires candidate text and no action"
                )
        if self.state in {
            CreatorReviewState.ACCEPTED,
            CreatorReviewState.REJECTED,
        }:
            if self.creator_action is None or self.resolved_at is None:
                raise ContractValidationError(
                    "resolved creator review requires action and resolution time"
                )
            if (
                self.candidate_text is not None
                or self.provisional_candidate_json is not None
                or self.live_result_json is not None
            ):
                raise ContractValidationError(
                    "resolved creator review must purge provisional prose/result payload"
                )
        if self.prepared_package is not None and (
            self.prepared_package.branch_id != self.branch_id
            or self.prepared_package.request_id != self.request_id
            or self.prepared_package.candidate_sha256 != self.candidate_sha256
            or self.prepared_package.sequence_plan_sha256
            != self.sequence_plan_sha256
        ):
            raise ContractValidationError("prepared package does not bind its review")

    @property
    def record_sha256(self) -> str:
        return domain_sha256(self.SCHEMA_VERSION, self)


@dataclass(frozen=True, slots=True)
class CreatorCorrectionDiagnostic:
    """Non-story record of creator feedback for later shared correction work."""

    SCHEMA_VERSION: ClassVar[str] = "cera.creator_correction_diagnostic.v1"

    schema_version: str
    diagnostic_id: TypedId
    review_id: TypedId
    branch_id: TypedId
    action: CreatorReviewAction
    diagnostic_kind: CorrectionDiagnosticKind
    likely_owner: ReviewIssueOwner
    assessment_sha256: str
    reason_codes: tuple[str, ...]
    creator_feedback: str
    creator_feedback_sha256: str
    status: str
    created_at: str

    def __post_init__(self) -> None:
        require_schema(self.schema_version, self.SCHEMA_VERSION, type(self).__name__)
        require_kind(self.diagnostic_id, IdKind.REVIEW_FINDING, "diagnostic_id")
        require_kind(self.review_id, IdKind.REVIEW_PACKET, "review_id")
        require_kind(self.branch_id, IdKind.BRANCH, "branch_id")
        if self.action not in {
            CreatorReviewAction.DEEPSEEK_REWRITE,
            CreatorReviewAction.CODEX_REPLAN,
            CreatorReviewAction.CORRECTION_ADJUSTMENT,
            CreatorReviewAction.FALSE_POSITIVE,
        }:
            raise ContractValidationError(
                "creator correction diagnostic requires a revision action"
            )
        if self.likely_owner is ReviewIssueOwner.NONE:
            raise ContractValidationError(
                "creator correction diagnostic requires a likely owner"
            )
        if (
            self.action is CreatorReviewAction.FALSE_POSITIVE
            and (
                self.diagnostic_kind is not CorrectionDiagnosticKind.FALSE_POSITIVE
                or self.likely_owner is not ReviewIssueOwner.VERIFIER
            )
        ):
            raise ContractValidationError(
                "false-positive diagnostics must identify the verifier"
            )
        if not re_is_sha256(self.assessment_sha256):
            raise ContractValidationError("diagnostic assessment hash is invalid")
        if len(self.reason_codes) != len(set(self.reason_codes)) or any(
            not value.strip() for value in self.reason_codes
        ):
            raise ContractValidationError("diagnostic reason codes are invalid")
        if (
            not self.creator_feedback.strip()
            or len(self.creator_feedback) > 8_000
            or text_sha256(self.creator_feedback) != self.creator_feedback_sha256
        ):
            raise ContractValidationError("diagnostic creator feedback is invalid")
        if self.status != "pending_shared_review" or not self.created_at.strip():
            raise ContractValidationError("diagnostic status or timestamp is invalid")

    @property
    def diagnostic_sha256(self) -> str:
        return domain_sha256(self.SCHEMA_VERSION, self)


@dataclass(frozen=True, slots=True)
class CreatorAcceptTimingReceipt:
    """Append-only, non-story timing evidence for one creator acceptance."""

    SCHEMA_VERSION: ClassVar[str] = "cera.creator_accept_timing_receipt.v1"

    schema_version: str
    receipt_id: TypedId
    review_id: TypedId
    package_id: TypedId
    branch_id: TypedId
    started_at: str
    committed_at: str
    accept_to_commit_microseconds: int
    provider_calls: int
    automatic_retries: int

    def __post_init__(self) -> None:
        require_schema(self.schema_version, self.SCHEMA_VERSION, type(self).__name__)
        require_kind(self.receipt_id, IdKind.TELEMETRY_EVENT, "receipt_id")
        require_kind(self.review_id, IdKind.REVIEW_PACKET, "review_id")
        require_kind(self.package_id, IdKind.PREPARED_PUBLICATION, "package_id")
        require_kind(self.branch_id, IdKind.BRANCH, "branch_id")
        if not self.started_at.strip() or not self.committed_at.strip():
            raise ContractValidationError("accept timing timestamps are required")
        if self.accept_to_commit_microseconds < 0:
            raise ContractValidationError("accept timing cannot be negative")
        if self.provider_calls != 0 or self.automatic_retries != 0:
            raise ContractValidationError(
                "creator acceptance cannot call providers or automatically retry"
            )

    @property
    def receipt_sha256(self) -> str:
        return domain_sha256(self.SCHEMA_VERSION, self)
