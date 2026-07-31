"""Python coordinator for independent realization verification."""

from __future__ import annotations

from cera.composer import (
    ComposerContextSource,
    ComposerExecutionResult,
    RealizationKind,
    SceneComposerRequest,
)
from cera.creator_review import (
    CreatorReviewAssessment,
    CreatorReviewSeverity,
    PublicationEligibility,
    ReviewIssueOwner,
)
from cera.errors import ContractValidationError
from cera.ids import IdKind, deterministic_id
from cera.serialization import canonical_json, text_sha256

from .models import (
    ProtectedUserRealizationAuthority,
    ProtectedUserRealizationClaim,
    RealizationBoundaryCheck,
    RealizationViolationCode,
    RealizationVerificationStatus,
    SceneRealizationBeatExpectation,
    SceneDevelopmentAtomExpectation,
    SceneRealizationVerificationReceipt,
    SceneRealizationVerificationRequest,
    SceneRealizationVerificationResult,
    SceneRealizationVerifierPort,
)
from .review import RejectedCandidateReviewPort, build_rejected_candidate_review


class SceneRealizationVerificationFailure(Exception):
    def __init__(
        self,
        message: str,
        *,
        receipt=None,
        provider_call_receipt=None,
        safe_diagnostics: tuple[str, ...] = (),
        rejected_candidate_review=None,
        external_provider_calls: int | None = None,
        creator_review_assessment: CreatorReviewAssessment | None = None,
    ) -> None:
        self.receipt = receipt
        self.provider_call_receipt = provider_call_receipt
        self.safe_diagnostics = safe_diagnostics
        self.rejected_candidate_review = rejected_candidate_review
        self.rejected_candidate_review_handle = (
            (
                rejected_candidate_review.review_id,
                rejected_candidate_review.review_sha256,
            )
            if rejected_candidate_review is not None
            else None
        )
        receipt_calls = (
            provider_call_receipt.external_provider_calls
            if provider_call_receipt is not None
            else 0
        )
        self.external_provider_calls = (
            receipt_calls
            if external_provider_calls is None
            else external_provider_calls
        )
        self.creator_review_assessment = creator_review_assessment
        if (
            type(self.external_provider_calls) is not int
            or self.external_provider_calls not in {0, 1}
        ):
            raise ValueError("realization verifier call observation must be zero or one")
        if provider_call_receipt is not None and self.external_provider_calls != receipt_calls:
            raise ValueError("verifier receipt and call observation disagree")
        super().__init__(message)


def _authoritative_verifier_context(
    request: SceneComposerRequest,
) -> tuple[str, ...]:
    """Project the Composer's exact evidence into a bounded verifier-only view."""

    context = request.realization_context
    if context is None:
        return ()
    projected: list[str] = []
    for block in context.blocks:
        if block.source is ComposerContextSource.CREATOR_CRAFT:
            continue
        if block.source is ComposerContextSource.EXACT_EVIDENCE:
            assert block.exact_evidence is not None
            evidence_id = block.exact_evidence.evidence_id
            authoritative_text = block.exact_evidence.sections_json
            record_type = block.exact_evidence.metadata.record_type.value
        else:
            assert block.evidence_section_view is not None
            evidence_id = block.evidence_section_view.evidence_id
            authoritative_text = block.evidence_section_view.selected_text
            record_type = block.evidence_section_view.metadata.record_type.value
        projected.append(
            canonical_json(
                {
                    "context_id": str(block.context_id),
                    "evidence_id": str(evidence_id),
                    "kind": block.kind.value,
                    "record_type": record_type,
                    "applicable_character_ids": [
                        str(value) for value in block.applicable_character_ids
                    ],
                    "authoritative_text": authoritative_text,
                }
            )
        )
    return tuple(projected)


class SceneRealizationVerificationCoordinator:
    def __init__(
        self,
        rejected_candidate_review_port: RejectedCandidateReviewPort | None = None,
    ) -> None:
        self.rejected_candidate_review_port = rejected_candidate_review_port

    def build_request(
        self,
        request: SceneComposerRequest,
        composed: ComposerExecutionResult,
    ) -> SceneRealizationVerificationRequest:
        decision = request.reasoner_outcome.decision
        assert decision is not None
        claims_by_unit = {}
        for claim in request.reasoner_outcome.protected_user_source_claims:
            claims_by_unit.setdefault(claim.source_unit_id, []).append(claim)
        claim_kind = {
            "action": RealizationKind.ACTION,
            "dialogue": RealizationKind.DIALOGUE,
        }
        protected_authorities = []
        for unit in request.source_packet.units:
            compiled_claims = tuple(claims_by_unit.get(unit.source_unit_id, ()))
            if compiled_claims:
                exact_claims = tuple(
                    ProtectedUserRealizationClaim(
                        kind=claim_kind[value.kind.value],
                        exact_text=unit.exact_text[value.start : value.end],
                    )
                    for value in compiled_claims
                )
            elif unit.protected_user_allowed_kinds:
                # Adult preflight supplies exact protected-user source units but
                # does not route through the ordinary Reasoner quote compiler.
                exact_claims = tuple(
                    ProtectedUserRealizationClaim(kind=value, exact_text=unit.exact_text)
                    for value in unit.protected_user_allowed_kinds
                )
            else:
                # Ordinary user text remains useful as non-authorizing context:
                # it lets the verifier distinguish an NPC reacting to an already
                # visible message from the Composer inventing a new user action.
                exact_claims = ()
            protected_authorities.append(
                ProtectedUserRealizationAuthority(
                    source_unit_id=unit.source_unit_id,
                    exact_text=unit.exact_text,
                    allowed_kinds=tuple(
                        dict.fromkeys(value.kind for value in exact_claims)
                    ),
                    claims=exact_claims,
                )
            )
        behavioral_plan = request.reasoner_outcome.behavioral_scene_plan
        development_expectations = ()
        if behavioral_plan is not None and behavioral_plan.development_atoms:
            block_to_beat = {
                block.block_id: beat.beat_id
                for block, beat in zip(
                    behavioral_plan.event_blocks,
                    decision.current_segment.ordered_beats,
                    strict=True,
                )
            }
            development_expectations = tuple(
                SceneDevelopmentAtomExpectation(
                    atom_id=value.atom_id,
                    owner_character_id=value.owner_character_id,
                    kind=value.kind,
                    strength_before=value.strength_before,
                    strength_after=value.strength_after,
                    summary=value.summary,
                    source_beat_ids=tuple(
                        block_to_beat[block_id]
                        for block_id in value.source_scene_block_ids
                    ),
                    inference_limit=value.inference_limit,
                )
                for value in behavioral_plan.development_atoms
            )
        return SceneRealizationVerificationRequest(
            schema_version=SceneRealizationVerificationRequest.SCHEMA_VERSION,
            request_id=request.prepared_turn.request.request_id,
            branch_id=request.prepared_turn.request.branch_id,
            generation_id=request.prepared_turn.request.generation_id,
            candidate_sha256=composed.candidate.candidate_sha256,
            story_text=composed.candidate.story_text,
            expected_beats=tuple(
                SceneRealizationBeatExpectation(
                    beat_id=value.beat_id,
                    actor_id=value.actor_id,
                    neutral_event=value.neutral_event,
                    required_state=value.state,
                )
                for value in decision.current_segment.ordered_beats
            ),
            selected_participant_ids=request.selected_npc_ids,
            protected_user_id=request.prepared_turn.request.protected_user_id,
            protected_user_authorities=tuple(protected_authorities),
            required_boundary_checks=(
                RealizationBoundaryCheck.PROTECTED_USER_NO_UNSUPPLIED_REALIZATION,
            ),
            realization_anchors=composed.manifest.character_spans,
            hard_boundaries=request.hard_boundaries,
            established_scene_context=request.established_scene_context,
            authoritative_evidence_context=(
                _authoritative_verifier_context(request)
            ),
            development_expectations=development_expectations,
        )

    def execute(
        self,
        request: SceneRealizationVerificationRequest,
        port: SceneRealizationVerifierPort,
    ) -> SceneRealizationVerificationResult:
        try:
            call = port.verify(request)
        except SceneRealizationVerificationFailure:
            raise
        except Exception as exc:
            raise SceneRealizationVerificationFailure(
                "scene realization verifier adapter failed",
                safe_diagnostics=(f"adapter:{type(exc).__name__}",),
            ) from exc
        if (
            call.adapter_version != port.adapter_version
            or call.qualification_eligible != port.qualification_eligible
        ):
            raise SceneRealizationVerificationFailure(
                "scene realization verifier adapter identity changed",
                provider_call_receipt=call.provider_call_receipt,
            )
        draft = call.draft
        for finding in draft.violation_findings:
            if finding.end > len(request.story_text) or text_sha256(
                request.story_text[finding.start : finding.end]
            ) != finding.text_sha256:
                raise SceneRealizationVerificationFailure(
                    "verifier returned an invalid anchored violation finding",
                    provider_call_receipt=call.provider_call_receipt,
                )
            if finding.code not in draft.violation_codes:
                raise SceneRealizationVerificationFailure(
                    "verifier finding code is absent from safe violation codes",
                    provider_call_receipt=call.provider_call_receipt,
                )
        protected_code = (
            RealizationViolationCode.PROTECTED_USER_UNSUPPLIED_REALIZATION.value
        )
        if protected_code in draft.violation_codes and not any(
            value.code == protected_code for value in draft.violation_findings
        ):
            raise SceneRealizationVerificationFailure(
                "protected-user semantic rejection requires an anchored finding",
                provider_call_receipt=call.provider_call_receipt,
            )
        if draft.status is RealizationVerificationStatus.ACCEPTED:
            if draft.verified_beat_ids != request.expected_beat_ids:
                raise SceneRealizationVerificationFailure(
                    "verifier acceptance omitted, added, or reordered a required beat",
                    provider_call_receipt=call.provider_call_receipt,
                )
            if set(draft.verified_participant_ids) != set(
                request.selected_participant_ids
            ):
                raise SceneRealizationVerificationFailure(
                    "verifier acceptance omitted or added a selected participant",
                    provider_call_receipt=call.provider_call_receipt,
                )
            if set(draft.verified_boundary_checks) != set(
                request.required_boundary_checks
            ):
                raise SceneRealizationVerificationFailure(
                    "verifier acceptance omitted or added a required semantic boundary",
                    provider_call_receipt=call.provider_call_receipt,
                )
            expected_atom_ids = {
                value.atom_id for value in request.development_expectations
            }
            if not set(draft.verified_development_atom_ids).issubset(
                expected_atom_ids
            ):
                raise SceneRealizationVerificationFailure(
                    "verifier accepted an unknown development atom",
                    provider_call_receipt=call.provider_call_receipt,
                )
        verification_id = deterministic_id(
            IdKind.REALIZATION_VERIFICATION,
            "cera.scene_realization_verification_receipt.v5",
            f"{request.request_sha256}|{draft.status.value}",
        )
        provider_receipt = call.provider_call_receipt
        receipt = SceneRealizationVerificationReceipt(
            schema_version=SceneRealizationVerificationReceipt.SCHEMA_VERSION,
            verification_id=verification_id,
            verification_request_sha256=request.request_sha256,
            candidate_sha256=request.candidate_sha256,
            story_text_sha256=request.story_text_sha256,
            status=draft.status,
            verified_beat_ids=draft.verified_beat_ids,
            verified_participant_ids=draft.verified_participant_ids,
            violation_codes=draft.violation_codes,
            verified_boundary_checks=draft.verified_boundary_checks,
            violation_finding_sha256s=tuple(
                value.finding_sha256 for value in draft.violation_findings
            ),
            verifier_adapter_version=call.adapter_version,
            verifier_adapter_evidence_id=call.adapter_evidence_id,
            verifier_adapter_evidence_sha256=call.adapter_evidence_sha256,
            qualification_eligible=call.qualification_eligible,
            provider_receipt_id=(
                provider_receipt.provider_receipt_id
                if provider_receipt is not None
                else None
            ),
            provider_receipt_sha256=(
                provider_receipt.receipt_sha256
                if provider_receipt is not None
                else None
            ),
            external_provider_calls=call.external_provider_calls,
            authoritative_store_writes=0,
            retains_story_prose=False,
            verified_development_atom_ids=draft.verified_development_atom_ids,
        )
        assessment = call.creator_review_assessment or _default_review_assessment(draft)
        if draft.status is not RealizationVerificationStatus.ACCEPTED:
            review = None
            if self.rejected_candidate_review_port is not None:
                try:
                    review = build_rejected_candidate_review(request, draft)
                    self.rejected_candidate_review_port.retain(review)
                except Exception as exc:
                    raise SceneRealizationVerificationFailure(
                        "rejected-candidate qualification review retention failed",
                        receipt=receipt,
                        provider_call_receipt=provider_receipt,
                        safe_diagnostics=(f"review_store:{type(exc).__name__}",),
                    ) from exc
            raise SceneRealizationVerificationFailure(
                "scene realization was not independently verified: "
                + ",".join(draft.violation_codes),
                receipt=receipt,
                provider_call_receipt=provider_receipt,
                rejected_candidate_review=review,
                creator_review_assessment=assessment,
            )
        return SceneRealizationVerificationResult(
            receipt,
            provider_receipt,
            assessment,
        )


def _default_review_assessment(
    draft,
) -> CreatorReviewAssessment:
    if draft.status is RealizationVerificationStatus.ACCEPTED:
        return CreatorReviewAssessment(
            schema_version=CreatorReviewAssessment.SCHEMA_VERSION,
            severity=CreatorReviewSeverity.GOOD,
            publication_eligibility=PublicationEligibility.ACCEPT_ALLOWED,
            issue_owner=ReviewIssueOwner.NONE,
            reason_codes=(),
            creator_reason=(
                "Good - the accepted sequence and required boundaries were realized."
            ),
            verifier_status=draft.status.value,
        )
    owner = (
        ReviewIssueOwner.HARD_BOUNDARY
        if draft.status is RealizationVerificationStatus.REJECTED
        else ReviewIssueOwner.VERIFIER
    )
    return CreatorReviewAssessment(
        schema_version=CreatorReviewAssessment.SCHEMA_VERSION,
        severity=CreatorReviewSeverity.CRITICAL,
        publication_eligibility=PublicationEligibility.ACCEPT_BLOCKED,
        issue_owner=owner,
        reason_codes=tuple(draft.violation_codes),
        creator_reason=(
            "Critical - semantic verification did not authorize publication."
        ),
        verifier_status=draft.status.value,
    )
