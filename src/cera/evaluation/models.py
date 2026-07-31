"""Provider-neutral contracts for Phase 10 evaluation and promotion evidence."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re
from typing import ClassVar

from cera.errors import ContractValidationError
from cera.ids import IdKind, TypedId, require_kind
from cera.schema import require_schema
from cera.serialization import domain_sha256, re_is_sha256, text_sha256


class EvaluationRole(StrEnum):
    TURN_KERNEL = "turn_kernel"
    EVIDENCE_RETRIEVAL = "evidence_retrieval"
    SCENE_REASONER = "scene_reasoner"
    ADULT_MECHANICS = "adult_mechanics"
    SCENE_COMPOSER = "scene_composer"
    SCENE_REALIZATION_VERIFIER = "scene_realization_verifier"
    DERIVED_CONSOLIDATOR = "derived_consolidator"
    END_TO_END = "end_to_end"


class EvaluationPartition(StrEnum):
    DEVELOPMENT = "development"
    CALIBRATION = "calibration"
    HOLDOUT = "holdout"


class RouteKind(StrEnum):
    DETERMINISTIC_LOCAL = "deterministic_local"
    SCRIPTED_FAKE = "scripted_fake"
    LIVE_PROVIDER = "live_provider"


class EvidenceClass(StrEnum):
    SYNTHETIC_CONTRACT = "synthetic_contract"
    DISPOSABLE_REAL_GENESIS = "disposable_real_genesis"
    LIVE_PROVIDER = "live_provider"


class FindingSeverity(StrEnum):
    HARD = "hard"
    MAJOR = "major"
    MINOR = "minor"


class FindingCategory(StrEnum):
    CONTRACT = "contract"
    AUTHORITY = "authority"
    PRIVACY = "privacy"
    IDENTITY = "identity"
    CONSENT_CAPACITY = "consent_capacity"
    CAST = "cast"
    BRANCH = "branch"
    PROTECTED_USER = "protected_user"
    STATE_PROSE_AGREEMENT = "state_prose_agreement"
    QUALITY = "quality"
    RELIABILITY = "reliability"
    LATENCY_COST = "latency_cost"


HARD_BOUNDARY_CATEGORIES = frozenset(
    {
        FindingCategory.AUTHORITY,
        FindingCategory.PRIVACY,
        FindingCategory.IDENTITY,
        FindingCategory.CONSENT_CAPACITY,
        FindingCategory.CAST,
        FindingCategory.BRANCH,
        FindingCategory.PROTECTED_USER,
    }
)


class AssessmentStatus(StrEnum):
    OFFLINE_EVIDENCE_ONLY = "offline_evidence_only"
    PENDING_EVIDENCE = "pending_evidence"
    NOT_ELIGIBLE = "not_eligible"
    ELIGIBLE_FOR_CREATOR_REVIEW = "eligible_for_creator_review"


class HumanPreference(StrEnum):
    A = "a"
    B = "b"
    TIE = "tie"
    REJECT_BOTH = "reject_both"


class DeploymentReadinessStatus(StrEnum):
    BLOCKED = "blocked"
    ELIGIBLE_FOR_CREATOR_DECISION = "eligible_for_creator_decision"


@dataclass(frozen=True, slots=True)
class EvaluationCriterion:
    criterion_id: str
    category: FindingCategory
    severity: FindingSeverity
    description: str

    def __post_init__(self) -> None:
        _non_empty(self.criterion_id, "criterion_id")
        _non_empty(self.description, "criterion description")
        if self.category in HARD_BOUNDARY_CATEGORIES and self.severity is not FindingSeverity.HARD:
            raise ContractValidationError("authority-boundary criteria must be hard failures")


@dataclass(frozen=True, slots=True)
class EvaluationCase:
    SCHEMA_VERSION: ClassVar[str] = "cera.evaluation_case.v1"

    schema_version: str
    case_id: TypedId
    suite_id: str
    suite_version: str
    role: EvaluationRole
    partition: EvaluationPartition
    input_sha256: str
    expected_contract_sha256: str
    risk_tags: tuple[str, ...]
    criteria: tuple[EvaluationCriterion, ...]
    provenance_sha256: tuple[str, ...]
    sealed: bool

    def __post_init__(self) -> None:
        require_schema(self.schema_version, self.SCHEMA_VERSION, type(self).__name__)
        require_kind(self.case_id, IdKind.EVALUATION_CASE, "case_id")
        _non_empty(self.suite_id, "suite_id")
        _non_empty(self.suite_version, "suite_version")
        _sha256(self.input_sha256, "input_sha256")
        _sha256(self.expected_contract_sha256, "expected_contract_sha256")
        if not self.risk_tags or not self.criteria or not self.provenance_sha256:
            raise ContractValidationError("evaluation case requires tags, criteria, and provenance")
        _unique(self.risk_tags, "risk tags")
        _unique((value.criterion_id for value in self.criteria), "criteria")
        _unique(self.provenance_sha256, "case provenance")
        for value in (*self.provenance_sha256,):
            _sha256(value, "provenance hash")
        if self.partition is EvaluationPartition.HOLDOUT and not self.sealed:
            raise ContractValidationError("holdout evaluation cases must be sealed")
        if self.partition is not EvaluationPartition.HOLDOUT and self.sealed:
            raise ContractValidationError("only holdout evaluation cases may be sealed")

    @property
    def case_sha256(self) -> str:
        return domain_sha256("cera.evaluation_case.v1", self)


@dataclass(frozen=True, slots=True)
class EvaluationSuiteManifest:
    SCHEMA_VERSION: ClassVar[str] = "cera.evaluation_suite_manifest.v1"

    schema_version: str
    suite_id: str
    suite_version: str
    cases: tuple[EvaluationCase, ...]
    excluded_craft_asset_sha256: tuple[str, ...]
    required_roles: tuple[EvaluationRole, ...]
    required_risk_tags: tuple[str, ...]
    minimum_holdout_cases: int
    provider_free_authoring: bool

    def __post_init__(self) -> None:
        require_schema(self.schema_version, self.SCHEMA_VERSION, type(self).__name__)
        _non_empty(self.suite_id, "suite_id")
        _non_empty(self.suite_version, "suite_version")
        if not self.cases or not self.required_roles or not self.required_risk_tags:
            raise ContractValidationError("evaluation suite is incomplete")
        if type(self.minimum_holdout_cases) is not int or self.minimum_holdout_cases < 1:
            raise ContractValidationError("minimum_holdout_cases must be positive")
        if not self.provider_free_authoring:
            raise ContractValidationError("Phase 10 foundation suite must be provider-free authored")
        _unique((str(value.case_id) for value in self.cases), "evaluation case IDs")
        _unique((value.case_sha256 for value in self.cases), "evaluation case hashes")
        _unique((value.input_sha256 for value in self.cases), "evaluation case inputs")
        _unique(self.excluded_craft_asset_sha256, "excluded craft assets")
        _unique((value.value for value in self.required_roles), "required roles")
        _unique(self.required_risk_tags, "required risk tags")
        for value in self.excluded_craft_asset_sha256:
            _sha256(value, "excluded craft asset hash")
        if any(
            value.suite_id != self.suite_id or value.suite_version != self.suite_version
            for value in self.cases
        ):
            raise ContractValidationError("evaluation case suite binding mismatch")
        if not set(self.required_roles).issubset({value.role for value in self.cases}):
            raise ContractValidationError("evaluation suite is missing a required role")
        all_tags = {tag for value in self.cases for tag in value.risk_tags}
        if not set(self.required_risk_tags).issubset(all_tags):
            raise ContractValidationError("evaluation suite is missing a required risk tag")
        holdout_count = sum(
            value.partition is EvaluationPartition.HOLDOUT for value in self.cases
        )
        if holdout_count < self.minimum_holdout_cases:
            raise ContractValidationError("evaluation suite has too few holdout cases")
        excluded = set(self.excluded_craft_asset_sha256)
        if any(
            value.input_sha256 in excluded
            or value.expected_contract_sha256 in excluded
            or bool(set(value.provenance_sha256).intersection(excluded))
            for value in self.cases
        ):
            raise ContractValidationError("evaluation data overlaps an excluded craft asset")

    @property
    def manifest_sha256(self) -> str:
        return domain_sha256("cera.evaluation_suite_manifest.v1", self)


@dataclass(frozen=True, slots=True)
class RouteIdentity:
    role: EvaluationRole
    route_kind: RouteKind
    adapter_id: str
    model_name: str | None
    model_revision: str | None
    prompt_version: str
    transport_version: str
    configuration_sha256: str

    def __post_init__(self) -> None:
        _non_empty(self.adapter_id, "adapter_id")
        _non_empty(self.prompt_version, "prompt_version")
        _non_empty(self.transport_version, "transport_version")
        _sha256(self.configuration_sha256, "configuration_sha256")
        if self.route_kind is RouteKind.LIVE_PROVIDER:
            _non_empty(self.model_name, "model_name")
            _non_empty(self.model_revision, "model_revision")
        elif self.model_name is not None or self.model_revision is not None:
            raise ContractValidationError("non-live route cannot claim a provider model")

    @property
    def route_sha256(self) -> str:
        return domain_sha256("cera.evaluation_route.v1", self)


@dataclass(frozen=True, slots=True)
class CriterionObservation:
    criterion_id: str
    passed: bool
    evidence_sha256: str
    note: str

    def __post_init__(self) -> None:
        _non_empty(self.criterion_id, "criterion_id")
        _sha256(self.evidence_sha256, "criterion evidence hash")
        _non_empty(self.note, "criterion note")


@dataclass(frozen=True, slots=True)
class EvaluationCaseResult:
    case_id: TypedId
    route_sha256: str
    output_sha256: str
    observations: tuple[CriterionObservation, ...]
    evidence_class: EvidenceClass
    external_provider_calls: int
    latency_ms: int
    input_tokens: int
    output_tokens: int
    cost_microusd: int
    authority_store_writes: int

    def __post_init__(self) -> None:
        require_kind(self.case_id, IdKind.EVALUATION_CASE, "case_id")
        _sha256(self.route_sha256, "route_sha256")
        _sha256(self.output_sha256, "output_sha256")
        if not self.observations:
            raise ContractValidationError("evaluation result requires observations")
        _unique((value.criterion_id for value in self.observations), "observations")
        counters = (
            self.external_provider_calls,
            self.latency_ms,
            self.input_tokens,
            self.output_tokens,
            self.cost_microusd,
            self.authority_store_writes,
        )
        if any(type(value) is not int or value < 0 for value in counters):
            raise ContractValidationError("evaluation result counters cannot be negative")
        if self.authority_store_writes != 0:
            raise ContractValidationError("evaluation cannot write story authority")


@dataclass(frozen=True, slots=True)
class EvaluationFinding:
    finding_id: TypedId
    case_id: TypedId
    criterion_id: str
    category: FindingCategory
    severity: FindingSeverity
    evidence_sha256: str
    note: str

    def __post_init__(self) -> None:
        require_kind(self.finding_id, IdKind.EVALUATION_FINDING, "finding_id")
        require_kind(self.case_id, IdKind.EVALUATION_CASE, "case_id")
        _non_empty(self.criterion_id, "criterion_id")
        _sha256(self.evidence_sha256, "finding evidence hash")
        _non_empty(self.note, "finding note")
        if self.category in HARD_BOUNDARY_CATEGORIES and self.severity is not FindingSeverity.HARD:
            raise ContractValidationError("authority-boundary findings must be hard failures")


@dataclass(frozen=True, slots=True)
class EvaluationRunReport:
    SCHEMA_VERSION: ClassVar[str] = "cera.evaluation_run_report.v1"

    schema_version: str
    run_id: TypedId
    manifest_sha256: str
    route_sha256: str
    role: EvaluationRole
    partition: EvaluationPartition
    case_ids: tuple[TypedId, ...]
    result_sha256: tuple[str, ...]
    findings: tuple[EvaluationFinding, ...]
    hard_failure_count: int
    passed_case_count: int
    external_provider_calls: int
    evidence_only: bool
    story_authority_writes: int
    report_sha256: str

    def __post_init__(self) -> None:
        require_schema(self.schema_version, self.SCHEMA_VERSION, type(self).__name__)
        require_kind(self.run_id, IdKind.EVALUATION_RUN, "run_id")
        for value in (self.manifest_sha256, self.route_sha256, self.report_sha256):
            _sha256(value, "evaluation run hash")
        for value in self.result_sha256:
            _sha256(value, "evaluation result hash")
        _unique((str(value) for value in self.case_ids), "run case IDs")
        if len(self.case_ids) != len(self.result_sha256):
            raise ContractValidationError("run cases and result hashes do not align")
        counters = (
            self.hard_failure_count,
            self.passed_case_count,
            self.external_provider_calls,
            self.story_authority_writes,
        )
        if any(type(value) is not int or value < 0 for value in counters):
            raise ContractValidationError("run counters cannot be negative")
        if self.story_authority_writes != 0:
            raise ContractValidationError("evaluation run cannot write story authority")
        if self.passed_case_count > len(self.case_ids):
            raise ContractValidationError("passed case count exceeds run case count")
        if self.hard_failure_count != sum(
            value.severity is FindingSeverity.HARD for value in self.findings
        ):
            raise ContractValidationError("hard failure count does not match findings")


@dataclass(frozen=True, slots=True)
class TelemetryEvent:
    SCHEMA_VERSION: ClassVar[str] = "cera.telemetry_event.v1"

    schema_version: str
    telemetry_event_id: TypedId
    trace_id: TypedId
    stage: str
    route_sha256: str
    outcome: str
    duration_ms: int
    provider_calls: int
    input_tokens: int
    output_tokens: int
    cost_microusd: int
    error_code: str | None
    raw_source_included: bool
    story_prose_included: bool
    private_evidence_included: bool
    prompt_included: bool
    secret_included: bool

    def __post_init__(self) -> None:
        require_schema(self.schema_version, self.SCHEMA_VERSION, type(self).__name__)
        require_kind(self.telemetry_event_id, IdKind.TELEMETRY_EVENT, "telemetry_event_id")
        require_kind(self.trace_id, IdKind.TRACE, "trace_id")
        _non_empty(self.stage, "telemetry stage")
        _non_empty(self.outcome, "telemetry outcome")
        _machine_token(self.stage, "telemetry stage")
        _machine_token(self.outcome, "telemetry outcome")
        _sha256(self.route_sha256, "telemetry route hash")
        counters = (
            self.duration_ms,
            self.provider_calls,
            self.input_tokens,
            self.output_tokens,
            self.cost_microusd,
        )
        if any(type(value) is not int or value < 0 for value in counters):
            raise ContractValidationError("telemetry counters cannot be negative")
        if self.error_code is not None:
            _non_empty(self.error_code, "telemetry error_code")
            if re.fullmatch(r"CERA_[A-Z0-9_]{1,96}", self.error_code) is None:
                raise ContractValidationError("telemetry error_code must be a stable CERA code")
        if any(
            (
                self.raw_source_included,
                self.story_prose_included,
                self.private_evidence_included,
                self.prompt_included,
                self.secret_included,
            )
        ):
            raise ContractValidationError("telemetry cannot contain protected content")

    @property
    def telemetry_sha256(self) -> str:
        return domain_sha256("cera.telemetry_event.v1", self)


@dataclass(frozen=True, slots=True)
class HumanReviewPacket:
    SCHEMA_VERSION: ClassVar[str] = "cera.human_review_packet.v1"

    schema_version: str
    packet_id: TypedId
    case_id: TypedId
    evaluation_context: str
    evaluation_context_sha256: str
    candidate_a_text: str
    candidate_a_sha256: str
    candidate_b_text: str
    candidate_b_sha256: str
    order_commitment_sha256: str
    provider_identity_exposed: bool
    private_evidence_included: bool

    def __post_init__(self) -> None:
        require_schema(self.schema_version, self.SCHEMA_VERSION, type(self).__name__)
        require_kind(self.packet_id, IdKind.REVIEW_PACKET, "packet_id")
        require_kind(self.case_id, IdKind.EVALUATION_CASE, "case_id")
        _non_empty(self.evaluation_context, "evaluation context")
        _non_empty(self.candidate_a_text, "candidate A")
        _non_empty(self.candidate_b_text, "candidate B")
        for text, expected in (
            (self.evaluation_context, self.evaluation_context_sha256),
            (self.candidate_a_text, self.candidate_a_sha256),
            (self.candidate_b_text, self.candidate_b_sha256),
        ):
            _sha256(expected, "human review text hash")
            if text_sha256(text) != expected:
                raise ContractValidationError("human review text hash mismatch")
        _sha256(self.order_commitment_sha256, "order commitment hash")
        if self.provider_identity_exposed or self.private_evidence_included:
            raise ContractValidationError("human review packet must remain blinded and safe")


@dataclass(frozen=True, slots=True)
class HumanReviewKey:
    packet_id: TypedId
    route_a_sha256: str
    route_b_sha256: str
    order_nonce_sha256: str

    def __post_init__(self) -> None:
        require_kind(self.packet_id, IdKind.REVIEW_PACKET, "packet_id")
        for value in (
            self.route_a_sha256,
            self.route_b_sha256,
            self.order_nonce_sha256,
        ):
            _sha256(value, "human review key hash")
        if self.route_a_sha256 == self.route_b_sha256:
            raise ContractValidationError("matched review requires distinct routes")


@dataclass(frozen=True, slots=True)
class HumanDimensionScore:
    dimension: str
    candidate_a: int
    candidate_b: int

    def __post_init__(self) -> None:
        _non_empty(self.dimension, "review dimension")
        if any(type(value) is not int or not 1 <= value <= 5 for value in (
            self.candidate_a,
            self.candidate_b,
        )):
            raise ContractValidationError("human review scores must be integers from 1 to 5")


@dataclass(frozen=True, slots=True)
class HumanReviewBallot:
    SCHEMA_VERSION: ClassVar[str] = "cera.human_review_ballot.v1"

    schema_version: str
    ballot_id: TypedId
    packet_id: TypedId
    reviewer_pseudonym_sha256: str
    preference: HumanPreference
    scores: tuple[HumanDimensionScore, ...]
    hard_defect_categories: tuple[FindingCategory, ...]
    provider_identity_seen: bool

    def __post_init__(self) -> None:
        require_schema(self.schema_version, self.SCHEMA_VERSION, type(self).__name__)
        require_kind(self.ballot_id, IdKind.REVIEW_BALLOT, "ballot_id")
        require_kind(self.packet_id, IdKind.REVIEW_PACKET, "packet_id")
        _sha256(self.reviewer_pseudonym_sha256, "reviewer pseudonym hash")
        if not self.scores:
            raise ContractValidationError("human review ballot requires dimension scores")
        _unique((value.dimension for value in self.scores), "review dimensions")
        _unique((value.value for value in self.hard_defect_categories), "hard defects")
        if any(value not in HARD_BOUNDARY_CATEGORIES for value in self.hard_defect_categories):
            raise ContractValidationError("ballot hard defect category is not a hard boundary")
        if self.provider_identity_seen:
            raise ContractValidationError("unblinded ballot cannot support matched review")

    @property
    def ballot_sha256(self) -> str:
        return domain_sha256("cera.human_review_ballot.v1", self)


@dataclass(frozen=True, slots=True)
class HumanReviewSummary:
    route_sha256: str
    matched_case_count: int
    ballot_count: int
    wins: int
    losses: int
    ties: int
    reject_both: int
    hard_defect_count: int
    blinded: bool
    summary_sha256: str

    def __post_init__(self) -> None:
        _sha256(self.route_sha256, "review summary route hash")
        _sha256(self.summary_sha256, "review summary hash")
        counters = (
            self.matched_case_count,
            self.ballot_count,
            self.wins,
            self.losses,
            self.ties,
            self.reject_both,
            self.hard_defect_count,
        )
        if any(type(value) is not int or value < 0 for value in counters):
            raise ContractValidationError("review summary counters cannot be negative")
        if self.wins + self.losses + self.ties + self.reject_both != self.ballot_count:
            raise ContractValidationError("review summary outcomes do not match ballots")
        if not self.blinded:
            raise ContractValidationError("unblinded review cannot support promotion")


@dataclass(frozen=True, slots=True)
class PromotionPolicy:
    SCHEMA_VERSION: ClassVar[str] = "cera.promotion_policy.v1"

    schema_version: str
    policy_id: str
    role: EvaluationRole
    minimum_case_count: int
    minimum_holdout_case_count: int
    minimum_human_case_count: int
    minimum_human_ballots: int
    minimum_human_win_rate_basis_points: int
    minimum_pass_rate_basis_points: int
    required_risk_tags: tuple[str, ...]
    maximum_hard_failures: int

    def __post_init__(self) -> None:
        require_schema(self.schema_version, self.SCHEMA_VERSION, type(self).__name__)
        _non_empty(self.policy_id, "policy_id")
        counters = (
            self.minimum_case_count,
            self.minimum_holdout_case_count,
            self.minimum_human_case_count,
            self.minimum_human_ballots,
        )
        if any(type(value) is not int or value < 1 for value in counters):
            raise ContractValidationError("promotion minimums must be positive")
        if not 1 <= self.minimum_pass_rate_basis_points <= 10_000:
            raise ContractValidationError("promotion pass rate must be 1..10000 basis points")
        if not 1 <= self.minimum_human_win_rate_basis_points <= 10_000:
            raise ContractValidationError(
                "human win rate must be 1..10000 basis points"
            )
        if not self.required_risk_tags:
            raise ContractValidationError("promotion policy requires risk tags")
        _unique(self.required_risk_tags, "promotion risk tags")
        if self.maximum_hard_failures != 0:
            raise ContractValidationError("hard authority defects cannot be averaged away")


@dataclass(frozen=True, slots=True)
class PromotionAssessment:
    SCHEMA_VERSION: ClassVar[str] = "cera.promotion_assessment.v1"

    schema_version: str
    assessment_id: TypedId
    policy_id: str
    route_sha256: str
    run_report_sha256: tuple[str, ...]
    human_review_summary_sha256: str | None
    status: AssessmentStatus
    blockers: tuple[str, ...]
    evaluated_case_count: int
    holdout_case_count: int
    hard_failure_count: int
    pass_rate_basis_points: int
    live_provider_qualification_proven: bool
    creator_approval_required: bool
    route_promoted: bool

    def __post_init__(self) -> None:
        require_schema(self.schema_version, self.SCHEMA_VERSION, type(self).__name__)
        require_kind(self.assessment_id, IdKind.PROMOTION_ASSESSMENT, "assessment_id")
        _non_empty(self.policy_id, "policy_id")
        _sha256(self.route_sha256, "assessment route hash")
        for value in self.run_report_sha256:
            _sha256(value, "run report hash")
        if self.human_review_summary_sha256 is not None:
            _sha256(self.human_review_summary_sha256, "human review summary hash")
        counters = (
            self.evaluated_case_count,
            self.holdout_case_count,
            self.hard_failure_count,
            self.pass_rate_basis_points,
        )
        if any(type(value) is not int or value < 0 for value in counters):
            raise ContractValidationError("promotion assessment counters cannot be negative")
        if self.pass_rate_basis_points > 10_000:
            raise ContractValidationError("assessment pass rate exceeds 100 percent")
        if not self.creator_approval_required or self.route_promoted:
            raise ContractValidationError("assessment cannot grant creator promotion")
        if self.status is AssessmentStatus.ELIGIBLE_FOR_CREATOR_REVIEW:
            if self.blockers or not self.live_provider_qualification_proven:
                raise ContractValidationError("eligible assessment cannot retain blockers")
        elif not self.blockers:
            raise ContractValidationError("non-eligible assessment must explain its blockers")

    @property
    def assessment_sha256(self) -> str:
        return domain_sha256("cera.promotion_assessment.v1", self)


@dataclass(frozen=True, slots=True)
class DeploymentReadinessInput:
    promotion_assessment_ids: tuple[TypedId, ...]
    required_roles: tuple[EvaluationRole, ...]
    qualified_roles: tuple[EvaluationRole, ...]
    matched_human_review_complete: bool
    telemetry_privacy_review_complete: bool
    production_world_authorized: bool
    sillytavern_integration_authorized: bool
    credential_storage_approved: bool
    creator_fresh_chat_accepted: bool
    deployment_authorized: bool

    def __post_init__(self) -> None:
        if not self.promotion_assessment_ids or not self.required_roles:
            raise ContractValidationError("deployment readiness requires assessments and roles")
        for value in self.promotion_assessment_ids:
            require_kind(value, IdKind.PROMOTION_ASSESSMENT, "promotion assessment IDs")
        _unique((str(value) for value in self.promotion_assessment_ids), "assessment IDs")
        _unique((value.value for value in self.required_roles), "required roles")
        _unique((value.value for value in self.qualified_roles), "qualified roles")
        if not set(self.qualified_roles).issubset(set(self.required_roles)):
            raise ContractValidationError("qualified deployment role is not required")


@dataclass(frozen=True, slots=True)
class DeploymentReadinessReport:
    SCHEMA_VERSION: ClassVar[str] = "cera.deployment_readiness_report.v1"

    schema_version: str
    assessment_id: TypedId
    status: DeploymentReadinessStatus
    blockers: tuple[str, ...]
    creator_decision_required: bool
    deployment_executed: bool

    def __post_init__(self) -> None:
        require_schema(self.schema_version, self.SCHEMA_VERSION, type(self).__name__)
        require_kind(self.assessment_id, IdKind.PROMOTION_ASSESSMENT, "assessment_id")
        if not self.creator_decision_required or self.deployment_executed:
            raise ContractValidationError("readiness report cannot deploy or replace creator approval")
        if self.status is DeploymentReadinessStatus.BLOCKED and not self.blockers:
            raise ContractValidationError("blocked readiness report requires blockers")
        if self.status is DeploymentReadinessStatus.ELIGIBLE_FOR_CREATOR_DECISION and self.blockers:
            raise ContractValidationError("eligible readiness report cannot retain blockers")


def _non_empty(value: str | None, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContractValidationError(f"{field_name} must be non-empty")
    return value


def _sha256(value: str, field_name: str) -> None:
    if not re_is_sha256(value):
        raise ContractValidationError(f"{field_name} must be SHA-256")


def _unique(values, field_name: str) -> None:
    items = list(values)
    if len(items) != len(set(items)):
        raise ContractValidationError(f"{field_name} must not contain duplicates")


def _machine_token(value: str, field_name: str) -> None:
    if re.fullmatch(r"[a-z][a-z0-9_]{0,63}", value) is None:
        raise ContractValidationError(f"{field_name} must be a bounded machine token")
