"""Separately governed qualification review for rejected prose candidates.

These artifacts are deliberately excluded from runtime receipts, telemetry,
story authority, and the general schema registry.  A caller must explicitly
install a review store for a qualification run.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Protocol

from cera.errors import ContractValidationError
from cera.ids import IdKind, TypedId, deterministic_id, require_kind
from cera.schema import require_schema
from cera.serialization import domain_sha256, re_is_sha256, text_sha256

from .models import (
    SceneRealizationVerificationDraft,
    SceneRealizationVerificationRequest,
)


@dataclass(frozen=True, slots=True)
class RejectedCandidateExcerpt:
    finding_code: str
    start: int
    end: int
    excerpt: str
    excerpt_sha256: str

    def __post_init__(self) -> None:
        if not self.finding_code.strip() or self.start < 0 or self.end <= self.start:
            raise ContractValidationError("rejected-candidate excerpt is invalid")
        if not self.excerpt or len(self.excerpt) > 512:
            raise ContractValidationError("review excerpt must contain at most 512 characters")
        if self.excerpt_sha256 != text_sha256(self.excerpt):
            raise ContractValidationError("review excerpt hash changed")


@dataclass(frozen=True, slots=True)
class RejectedCandidateReviewArtifact:
    """Creator-local, non-authoritative evidence for one rejected candidate."""

    SCHEMA_VERSION: ClassVar[str] = "cera.rejected_candidate_review_artifact.v1"

    schema_version: str
    review_id: TypedId
    request_id: TypedId
    branch_id: TypedId
    generation_id: TypedId
    candidate_sha256: str
    verification_request_sha256: str
    status: str
    violation_codes: tuple[str, ...]
    finding_sha256s: tuple[str, ...]
    excerpts: tuple[RejectedCandidateExcerpt, ...]
    access_scope: str
    retention_policy: str
    reviewer_status: str
    qualification_only: bool
    runtime_receipt_forbidden: bool
    story_state_committed: bool
    authoritative_store_writes: int
    retains_story_prose: bool

    def __post_init__(self) -> None:
        require_schema(self.schema_version, self.SCHEMA_VERSION, type(self).__name__)
        require_kind(self.review_id, IdKind.REVIEW_PACKET, "review_id")
        require_kind(self.request_id, IdKind.REQUEST, "request_id")
        require_kind(self.branch_id, IdKind.BRANCH, "branch_id")
        require_kind(self.generation_id, IdKind.GENERATION, "generation_id")
        for value in (self.candidate_sha256, self.verification_request_sha256):
            if not re_is_sha256(value):
                raise ContractValidationError("rejected-candidate review hash is invalid")
        if self.status not in {"rejected", "inconclusive"} or not self.violation_codes:
            raise ContractValidationError("review artifact requires a terminal rejection")
        if len(self.violation_codes) != len(set(self.violation_codes)):
            raise ContractValidationError("review violation codes must be unique")
        if len(self.finding_sha256s) != len(set(self.finding_sha256s)) or any(
            not re_is_sha256(value) for value in self.finding_sha256s
        ):
            raise ContractValidationError("review finding hashes are invalid")
        if self.access_scope != "creator_local_qualification_review":
            raise ContractValidationError("review access scope cannot be broadened")
        if self.retention_policy != "manual_delete_after_adjudication":
            raise ContractValidationError("review retention policy is invalid")
        if self.reviewer_status != "pending_creator_review":
            raise ContractValidationError("review artifact cannot self-adjudicate")
        if (
            not self.qualification_only
            or not self.runtime_receipt_forbidden
            or self.story_state_committed
            or self.authoritative_store_writes != 0
            or not self.retains_story_prose
        ):
            raise ContractValidationError("review artifact authority boundary changed")

    @property
    def review_sha256(self) -> str:
        return domain_sha256("cera.rejected_candidate_review_artifact.v1", self)


class RejectedCandidateReviewPort(Protocol):
    def retain(self, artifact: RejectedCandidateReviewArtifact) -> None: ...


class InMemoryRejectedCandidateReviewStore:
    """Provider-free qualification fixture; never a production story store."""

    def __init__(self) -> None:
        self._records: dict[TypedId, RejectedCandidateReviewArtifact] = {}

    def retain(self, artifact: RejectedCandidateReviewArtifact) -> None:
        existing = self._records.get(artifact.review_id)
        if existing is not None and existing != artifact:
            raise ContractValidationError("review identity belongs to different evidence")
        self._records[artifact.review_id] = artifact

    def get(self, review_id: TypedId) -> RejectedCandidateReviewArtifact:
        require_kind(review_id, IdKind.REVIEW_PACKET, "review_id")
        try:
            return self._records[review_id]
        except KeyError as exc:
            raise ContractValidationError("review artifact does not exist") from exc


def build_rejected_candidate_review(
    request: SceneRealizationVerificationRequest,
    draft: SceneRealizationVerificationDraft,
) -> RejectedCandidateReviewArtifact:
    excerpts = []
    for finding in draft.violation_findings:
        context_start = max(0, finding.start - 160)
        context_end = min(len(request.story_text), finding.end + 160)
        if context_end - context_start > 512:
            context_end = context_start + 512
        excerpt = request.story_text[context_start:context_end]
        excerpts.append(
            RejectedCandidateExcerpt(
                finding_code=finding.code,
                start=context_start,
                end=context_end,
                excerpt=excerpt,
                excerpt_sha256=text_sha256(excerpt),
            )
        )
    identity = (
        f"{request.request_id}|{request.branch_id}|{request.generation_id}|"
        f"{request.candidate_sha256}|{draft.status.value}|"
        + "|".join(value.finding_sha256 for value in draft.violation_findings)
    )
    return RejectedCandidateReviewArtifact(
        schema_version=RejectedCandidateReviewArtifact.SCHEMA_VERSION,
        review_id=deterministic_id(
            IdKind.REVIEW_PACKET,
            "cera.rejected_candidate_review_artifact.v1",
            identity,
        ),
        request_id=request.request_id,
        branch_id=request.branch_id,
        generation_id=request.generation_id,
        candidate_sha256=request.candidate_sha256,
        verification_request_sha256=request.request_sha256,
        status=draft.status.value,
        violation_codes=draft.violation_codes,
        finding_sha256s=tuple(
            value.finding_sha256 for value in draft.violation_findings
        ),
        excerpts=tuple(excerpts),
        access_scope="creator_local_qualification_review",
        retention_policy="manual_delete_after_adjudication",
        reviewer_status="pending_creator_review",
        qualification_only=True,
        runtime_receipt_forbidden=True,
        story_state_committed=False,
        authoritative_store_writes=0,
        retains_story_prose=True,
    )
