"""Provider-neutral independent scene-realization verification contracts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar, Protocol

from cera.composer import RealizationKind, RealizationSpan
from cera.creator_review import CreatorReviewAssessment
from cera.contracts import (
    BeatState,
    DevelopmentAtomKind,
    DevelopmentAtomStrength,
)
from cera.errors import ContractValidationError
from cera.evaluation import EvaluationRole
from cera.ids import IdKind, TypedId, require_kind
from cera.providers import LiveProviderCallReceipt
from cera.schema import require_schema
from cera.serialization import domain_sha256, re_is_sha256, text_sha256


class RealizationVerificationStatus(StrEnum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    INCONCLUSIVE = "inconclusive"


class RealizationVerifierAdapterRole(StrEnum):
    SCRIPTED_FAKE = "scripted_fake"
    CODEX = "codex"


class RealizationBoundaryCheck(StrEnum):
    PROTECTED_USER_NO_UNSUPPLIED_REALIZATION = (
        "protected_user_no_unsupplied_realization"
    )


class RealizationViolationCode(StrEnum):
    PROTECTED_USER_UNSUPPLIED_REALIZATION = (
        "protected_user_unsupplied_realization"
    )
    REQUIRED_BEAT_NOT_REALIZED = "required_beat_not_realized"
    SELECTED_PARTICIPANT_NOT_REALIZED = "selected_participant_not_realized"
    REQUIRED_BOUNDARY_NOT_VERIFIABLE = "required_boundary_not_verifiable"
    VERIFIER_EVIDENCE_INSUFFICIENT = "verifier_evidence_insufficient"
    SEMANTIC_VERIFIER_NOT_CONFIGURED = "semantic_verifier_not_configured"


@dataclass(frozen=True, slots=True)
class SceneRealizationBeatExpectation:
    beat_id: TypedId
    actor_id: TypedId
    neutral_event: str
    required_state: BeatState

    def __post_init__(self) -> None:
        require_kind(self.beat_id, IdKind.BEAT, "beat_id")
        if self.actor_id.kind not in {IdKind.CHARACTER, IdKind.MATERIAL}:
            raise ContractValidationError(
                "realization beat actor must be a character or material"
            )
        if not self.neutral_event.strip():
            raise ContractValidationError(
                "realization beat expectation requires a neutral event"
            )


@dataclass(frozen=True, slots=True)
class SceneDevelopmentAtomExpectation:
    """One provisional state change that must be visibly supported to persist."""

    atom_id: TypedId
    owner_character_id: TypedId
    kind: DevelopmentAtomKind
    strength_before: DevelopmentAtomStrength
    strength_after: DevelopmentAtomStrength
    summary: str
    source_beat_ids: tuple[TypedId, ...]
    inference_limit: str

    def __post_init__(self) -> None:
        require_kind(self.atom_id, IdKind.DEVELOPMENT, "atom_id")
        require_kind(
            self.owner_character_id,
            IdKind.CHARACTER,
            "owner_character_id",
        )
        if not self.summary.strip() or not self.inference_limit.strip():
            raise ContractValidationError(
                "development expectation requires summary and inference limit"
            )
        if not self.source_beat_ids:
            raise ContractValidationError(
                "development expectation requires source beats"
            )
        for beat_id in self.source_beat_ids:
            require_kind(beat_id, IdKind.BEAT, "source_beat_ids")
        _unique(self.source_beat_ids, "development source beats")


@dataclass(frozen=True, slots=True)
class ProtectedUserRealizationClaim:
    """One exact user-authored semantic allowance within a source unit."""

    kind: RealizationKind
    exact_text: str

    def __post_init__(self) -> None:
        if self.kind not in {RealizationKind.ACTION, RealizationKind.DIALOGUE}:
            raise ContractValidationError(
                "protected-user claim kind must be action or dialogue"
            )
        if not self.exact_text.strip() or len(self.exact_text) > 500:
            raise ContractValidationError(
                "protected-user claim text must be 1..500 non-whitespace characters"
            )

    @property
    def exact_text_sha256(self) -> str:
        return text_sha256(self.exact_text)


@dataclass(frozen=True, slots=True)
class ProtectedUserRealizationAuthority:
    source_unit_id: TypedId
    exact_text: str
    allowed_kinds: tuple[RealizationKind, ...]
    claims: tuple[ProtectedUserRealizationClaim, ...]

    def __post_init__(self) -> None:
        require_kind(self.source_unit_id, IdKind.SOURCE_UNIT, "source_unit_id")
        if not self.exact_text:
            raise ContractValidationError(
                "protected-user realization authority requires exact source"
            )
        _unique(self.allowed_kinds, "protected-user allowed realization kinds")
        if not self.claims:
            if self.allowed_kinds:
                raise ContractValidationError(
                    "context-only protected-user source cannot allow realization kinds"
                )
            return
        _unique(
            ((value.kind.value, value.exact_text) for value in self.claims),
            "protected-user exact claims",
        )
        if tuple(dict.fromkeys(value.kind for value in self.claims)) != self.allowed_kinds:
            raise ContractValidationError(
                "protected-user allowed kinds must be derived from exact claims"
            )
        for claim in self.claims:
            if self.exact_text.count(claim.exact_text) != 1:
                raise ContractValidationError(
                    "protected-user exact claim must occur once in its source unit"
                )

    @property
    def exact_text_sha256(self) -> str:
        return text_sha256(self.exact_text)


@dataclass(frozen=True, slots=True)
class SceneRealizationViolationFinding:
    code: str
    start: int
    end: int
    text_sha256: str

    def __post_init__(self) -> None:
        if not self.code.strip() or self.start < 0 or self.end <= self.start:
            raise ContractValidationError("realization violation finding is invalid")
        if not re_is_sha256(self.text_sha256):
            raise ContractValidationError(
                "realization violation finding hash is invalid"
            )

    @property
    def finding_sha256(self) -> str:
        return domain_sha256("cera.scene_realization_violation_finding.v1", self)


@dataclass(frozen=True, slots=True)
class SceneRealizationVerificationRequest:
    SCHEMA_VERSION: ClassVar[str] = "cera.scene_realization_verification_request.v7"

    schema_version: str
    request_id: TypedId
    branch_id: TypedId
    generation_id: TypedId
    candidate_sha256: str
    story_text: str
    expected_beats: tuple[SceneRealizationBeatExpectation, ...]
    selected_participant_ids: tuple[TypedId, ...]
    protected_user_id: TypedId
    protected_user_authorities: tuple[ProtectedUserRealizationAuthority, ...]
    required_boundary_checks: tuple[RealizationBoundaryCheck, ...]
    realization_anchors: tuple[RealizationSpan, ...]
    hard_boundaries: tuple[str, ...]
    established_scene_context: tuple[str, ...] = ()
    authoritative_evidence_context: tuple[str, ...] = ()
    development_expectations: tuple[SceneDevelopmentAtomExpectation, ...] = ()

    def __post_init__(self) -> None:
        require_schema(self.schema_version, self.SCHEMA_VERSION, type(self).__name__)
        require_kind(self.request_id, IdKind.REQUEST, "request_id")
        require_kind(self.branch_id, IdKind.BRANCH, "branch_id")
        require_kind(self.generation_id, IdKind.GENERATION, "generation_id")
        require_kind(self.protected_user_id, IdKind.CHARACTER, "protected_user_id")
        if not re_is_sha256(self.candidate_sha256):
            raise ContractValidationError("verification candidate hash is invalid")
        if not self.story_text.strip() or not self.hard_boundaries:
            raise ContractValidationError(
                "verification request requires prose and hard boundaries"
            )
        if any(not value.strip() for value in self.established_scene_context):
            raise ContractValidationError(
                "verification established scene context cannot contain blanks"
            )
        _unique(
            self.established_scene_context,
            "verification established scene context",
        )
        if any(not value.strip() for value in self.authoritative_evidence_context):
            raise ContractValidationError(
                "verification authoritative evidence context cannot contain blanks"
            )
        _unique(
            self.authoritative_evidence_context,
            "verification authoritative evidence context",
        )
        if sum(
            len(value.encode("utf-8"))
            for value in self.authoritative_evidence_context
        ) > 131_072:
            raise ContractValidationError(
                "verification authoritative evidence context exceeds its limit"
            )
        for participant_id in self.selected_participant_ids:
            require_kind(
                participant_id,
                IdKind.CHARACTER,
                "selected_participant_ids",
            )
        if not self.expected_beats:
            raise ContractValidationError(
                "verification request requires expected beat semantics"
            )
        _unique(
            (value.beat_id for value in self.expected_beats),
            "expected beats",
        )
        _unique(self.selected_participant_ids, "selected participants")
        _unique(
            (value.source_unit_id for value in self.protected_user_authorities),
            "protected-user source authorities",
        )
        _unique(self.required_boundary_checks, "required realization boundary checks")
        _unique(
            (value.atom_id for value in self.development_expectations),
            "development expectations",
        )
        if not {
            value.owner_character_id for value in self.development_expectations
        }.issubset(set(self.selected_participant_ids)):
            raise ContractValidationError(
                "development expectation owner is not a selected participant"
            )
        expected_beat_ids = set(self.expected_beat_ids)
        if any(
            not set(value.source_beat_ids).issubset(expected_beat_ids)
            for value in self.development_expectations
        ):
            raise ContractValidationError(
                "development expectation cites a beat outside this request"
            )
        if (
            RealizationBoundaryCheck.PROTECTED_USER_NO_UNSUPPLIED_REALIZATION
            not in self.required_boundary_checks
        ):
            raise ContractValidationError(
                "realization request must require protected-user semantic verification"
            )

    @property
    def story_text_sha256(self) -> str:
        return text_sha256(self.story_text)

    @property
    def expected_beat_ids(self) -> tuple[TypedId, ...]:
        return tuple(value.beat_id for value in self.expected_beats)

    @property
    def request_sha256(self) -> str:
        safe = {
            "request_id": str(self.request_id),
            "branch_id": str(self.branch_id),
            "generation_id": str(self.generation_id),
            "candidate_sha256": self.candidate_sha256,
            "story_text_sha256": self.story_text_sha256,
            "expected_beats": self.expected_beats,
            "selected_participant_ids": tuple(
                str(value) for value in self.selected_participant_ids
            ),
            "protected_user_id": str(self.protected_user_id),
            "protected_user_authorities": tuple(
                {
                    "source_unit_id": str(value.source_unit_id),
                    "exact_text_sha256": value.exact_text_sha256,
                    "allowed_kinds": tuple(item.value for item in value.allowed_kinds),
                    "claims": tuple(
                        {
                            "kind": claim.kind.value,
                            "exact_text_sha256": claim.exact_text_sha256,
                        }
                        for claim in value.claims
                    ),
                }
                for value in self.protected_user_authorities
            ),
            "required_boundary_checks": self.required_boundary_checks,
            "realization_anchors": self.realization_anchors,
            "hard_boundaries": self.hard_boundaries,
            "established_scene_context": self.established_scene_context,
            "authoritative_evidence_context": tuple(
                text_sha256(value)
                for value in self.authoritative_evidence_context
            ),
            "development_expectations": self.development_expectations,
        }
        return domain_sha256(
            "cera.scene_realization_verification_request.v7",
            safe,
        )


@dataclass(frozen=True, slots=True)
class SceneRealizationVerificationDraft:
    status: RealizationVerificationStatus
    verified_beat_ids: tuple[TypedId, ...]
    verified_participant_ids: tuple[TypedId, ...]
    violation_codes: tuple[str, ...]
    verified_boundary_checks: tuple[RealizationBoundaryCheck, ...] = ()
    violation_findings: tuple[SceneRealizationViolationFinding, ...] = ()
    verified_development_atom_ids: tuple[TypedId, ...] = ()

    def __post_init__(self) -> None:
        for beat_id in self.verified_beat_ids:
            require_kind(beat_id, IdKind.BEAT, "verified_beat_ids")
        for participant_id in self.verified_participant_ids:
            require_kind(
                participant_id,
                IdKind.CHARACTER,
                "verified_participant_ids",
            )
        _unique(self.verified_beat_ids, "verified beats")
        _unique(self.verified_participant_ids, "verified participants")
        _unique(self.violation_codes, "realization violation codes")
        _unique(self.verified_boundary_checks, "verified realization boundary checks")
        _unique(
            (value.finding_sha256 for value in self.violation_findings),
            "realization violation findings",
        )
        for atom_id in self.verified_development_atom_ids:
            require_kind(
                atom_id,
                IdKind.DEVELOPMENT,
                "verified_development_atom_ids",
            )
        _unique(
            self.verified_development_atom_ids,
            "verified development atoms",
        )
        if self.status is RealizationVerificationStatus.ACCEPTED:
            if self.violation_codes or self.violation_findings:
                raise ContractValidationError(
                    "accepted realization verification cannot carry violations"
                )
        elif not self.violation_codes:
            raise ContractValidationError(
                "rejected or inconclusive verification requires a safe code"
            )


@dataclass(frozen=True, slots=True)
class SceneRealizationVerifierCall:
    """Transient verifier result plus privacy-safe provider evidence."""

    draft: SceneRealizationVerificationDraft
    adapter_role: RealizationVerifierAdapterRole
    adapter_version: str
    adapter_evidence_id: str
    adapter_evidence_sha256: str
    qualification_eligible: bool
    external_provider_calls: int
    provider_call_receipt: LiveProviderCallReceipt | None = None
    creator_review_assessment: CreatorReviewAssessment | None = None

    def __post_init__(self) -> None:
        if not self.adapter_version.strip() or not self.adapter_evidence_id.strip():
            raise ContractValidationError("realization verifier adapter identity is required")
        if not re_is_sha256(self.adapter_evidence_sha256):
            raise ContractValidationError(
                "realization verifier adapter evidence hash is invalid"
            )
        if self.adapter_role is RealizationVerifierAdapterRole.SCRIPTED_FAKE:
            if (
                self.qualification_eligible
                or self.external_provider_calls != 0
                or self.provider_call_receipt is not None
            ):
                raise ContractValidationError(
                    "scripted realization verifier cannot claim live qualification"
                )
        else:
            if (
                not self.qualification_eligible
                or self.external_provider_calls != 1
                or self.provider_call_receipt is None
            ):
                raise ContractValidationError(
                    "Codex realization verifier requires one qualified provider call"
                )
            if (
                self.provider_call_receipt.role
                is not EvaluationRole.SCENE_REALIZATION_VERIFIER
            ):
                raise ContractValidationError(
                    "realization verifier provider receipt has the wrong role"
                )
        if (
            self.creator_review_assessment is not None
            and self.creator_review_assessment.verifier_status != self.draft.status.value
        ):
            raise ContractValidationError(
                "creator review assessment changed verifier status"
            )


class SceneRealizationVerifierPort(Protocol):
    adapter_version: str
    qualification_eligible: bool

    def verify(
        self,
        request: SceneRealizationVerificationRequest,
    ) -> SceneRealizationVerifierCall: ...


@dataclass(frozen=True, slots=True)
class SceneRealizationVerificationReceipt:
    SCHEMA_VERSION: ClassVar[str] = "cera.scene_realization_verification_receipt.v5"

    schema_version: str
    verification_id: TypedId
    verification_request_sha256: str
    candidate_sha256: str
    story_text_sha256: str
    status: RealizationVerificationStatus
    verified_beat_ids: tuple[TypedId, ...]
    verified_participant_ids: tuple[TypedId, ...]
    violation_codes: tuple[str, ...]
    verified_boundary_checks: tuple[RealizationBoundaryCheck, ...]
    violation_finding_sha256s: tuple[str, ...]
    verifier_adapter_version: str
    verifier_adapter_evidence_id: str
    verifier_adapter_evidence_sha256: str
    qualification_eligible: bool
    provider_receipt_id: TypedId | None
    provider_receipt_sha256: str | None
    external_provider_calls: int
    authoritative_store_writes: int
    retains_story_prose: bool
    verified_development_atom_ids: tuple[TypedId, ...] = ()

    def __post_init__(self) -> None:
        require_schema(self.schema_version, self.SCHEMA_VERSION, type(self).__name__)
        require_kind(
            self.verification_id,
            IdKind.REALIZATION_VERIFICATION,
            "verification_id",
        )
        for value in (
            self.verification_request_sha256,
            self.candidate_sha256,
            self.story_text_sha256,
        ):
            if not re_is_sha256(value):
                raise ContractValidationError(
                    "realization receipt hashes are invalid"
                )
        if (
            not self.verifier_adapter_version.strip()
            or not self.verifier_adapter_evidence_id.strip()
            or not re_is_sha256(self.verifier_adapter_evidence_sha256)
        ):
            raise ContractValidationError("verifier adapter evidence is invalid")
        if (self.provider_receipt_id is None) != (
            self.provider_receipt_sha256 is None
        ):
            raise ContractValidationError(
                "verifier provider receipt ID/hash must be paired"
            )
        if self.provider_receipt_id is not None:
            require_kind(
                self.provider_receipt_id,
                IdKind.PROVIDER_RECEIPT,
                "provider_receipt_id",
            )
            assert self.provider_receipt_sha256 is not None
            if not re_is_sha256(self.provider_receipt_sha256):
                raise ContractValidationError(
                    "verifier provider receipt hash is invalid"
                )
        _unique(self.verified_boundary_checks, "receipt boundary checks")
        _unique(self.violation_finding_sha256s, "receipt violation findings")
        for atom_id in self.verified_development_atom_ids:
            require_kind(
                atom_id,
                IdKind.DEVELOPMENT,
                "verified_development_atom_ids",
            )
        _unique(
            self.verified_development_atom_ids,
            "verified development atoms",
        )
        if any(not re_is_sha256(value) for value in self.violation_finding_sha256s):
            raise ContractValidationError("receipt violation finding hash is invalid")
        if self.external_provider_calls not in {0, 1}:
            raise ContractValidationError(
                "realization verification permits at most one provider call"
            )
        if self.external_provider_calls == 0 and (
            self.qualification_eligible or self.provider_receipt_id is not None
        ):
            raise ContractValidationError(
                "provider-free verifier receipt cannot claim qualification"
            )
        if self.external_provider_calls == 1 and (
            not self.qualification_eligible or self.provider_receipt_id is None
        ):
            raise ContractValidationError(
                "live verifier receipt requires qualified provider evidence"
            )
        if self.authoritative_store_writes != 0 or self.retains_story_prose:
            raise ContractValidationError(
                "verification receipt cannot write authority or retain prose"
            )

    @property
    def receipt_sha256(self) -> str:
        return domain_sha256(
            "cera.scene_realization_verification_receipt.v5",
            self,
        )


@dataclass(frozen=True, slots=True)
class SceneRealizationVerificationResult:
    receipt: SceneRealizationVerificationReceipt
    provider_call_receipt: LiveProviderCallReceipt | None = None
    creator_review_assessment: CreatorReviewAssessment | None = None

    def __post_init__(self) -> None:
        if (self.receipt.provider_receipt_id is None) != (
            self.provider_call_receipt is None
        ):
            raise ContractValidationError(
                "realization verification provider receipt presence differs"
            )
        if self.provider_call_receipt is not None and (
            self.provider_call_receipt.provider_receipt_id
            != self.receipt.provider_receipt_id
            or self.provider_call_receipt.receipt_sha256
            != self.receipt.provider_receipt_sha256
        ):
            raise ContractValidationError(
                "realization verification provider receipt does not match"
            )
        if (
            self.creator_review_assessment is not None
            and self.creator_review_assessment.verifier_status
            != self.receipt.status.value
        ):
            raise ContractValidationError(
                "realization review assessment changed receipt status"
            )

    @property
    def accepted(self) -> bool:
        return self.receipt.status is RealizationVerificationStatus.ACCEPTED


def _unique(values, field_name: str) -> None:
    items = list(values)
    if len(items) != len(set(items)):
        raise ContractValidationError(f"{field_name} must not contain duplicates")
