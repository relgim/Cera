"""One-shot Sol-medium semantic scene-realization verifier adapter."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from cera.errors import ContractValidationError, ErrorCode, IdentityError
from cera.creator_review import (
    CreatorReviewAssessment,
    CreatorReviewSeverity,
    PublicationEligibility,
    ReviewIssueOwner,
)
from cera.evaluation import EvaluationRole
from cera.ids import IdKind, TypedId
from cera.providers import (
    CodexSDKTransport,
    ProviderName,
    ProviderTransportError,
)
from cera.schema import from_mapping
from cera.serialization import canonical_json, domain_sha256, to_primitive

from .models import (
    RealizationBoundaryCheck,
    RealizationVerifierAdapterRole,
    RealizationViolationCode,
    RealizationVerificationStatus,
    SceneRealizationVerificationDraft,
    SceneRealizationVerificationRequest,
    SceneRealizationVerifierCall,
    SceneRealizationViolationFinding,
)
from .orchestrator import SceneRealizationVerificationFailure


CODEX_REALIZATION_VERIFIER_ADAPTER_VERSION = (
    "cera.codex_scene_realization_verifier.v8"
)
CODEX_REALIZATION_VERIFIER_PACKET_VERSION = (
    "cera.codex_scene_realization_verifier_packet.v8"
)
CODEX_REALIZATION_VERIFIER_PROMPT_VERSION = (
    "cera.codex_scene_realization_verifier_prompt.v8"
)
CODEX_REALIZATION_VERIFIER_DRAFT_VERSION = (
    "cera.codex_scene_realization_verifier_draft.v3"
)

_MODEL_REPORTABLE_CODES = tuple(
    value
    for value in RealizationViolationCode
    if value is not RealizationViolationCode.SEMANTIC_VERIFIER_NOT_CONFIGURED
)


@dataclass(frozen=True, slots=True)
class CodexRealizationViolationAnchorDraft:
    code: RealizationViolationCode
    quote: str
    occurrence: int

    def __post_init__(self) -> None:
        if self.code not in _MODEL_REPORTABLE_CODES:
            raise ContractValidationError(
                "verifier finding uses a Python-owned failure code"
            )
        if not self.quote.strip() or len(self.quote) > 1_024:
            raise ContractValidationError(
                "verifier finding quote must be 1..1024 non-whitespace characters"
            )
        if type(self.occurrence) is not int or self.occurrence != 0:
            raise ContractValidationError(
                "verifier finding occurrence is a zero compatibility sentinel"
            )


@dataclass(frozen=True, slots=True)
class CodexSceneRealizationVerifierDraftV1:
    SCHEMA_VERSION: ClassVar[str] = CODEX_REALIZATION_VERIFIER_DRAFT_VERSION

    schema_version: str
    status: RealizationVerificationStatus
    verified_beat_ids: tuple[TypedId, ...]
    verified_participant_ids: tuple[TypedId, ...]
    verified_boundary_checks: tuple[RealizationBoundaryCheck, ...]
    verified_development_atom_ids: tuple[TypedId, ...]
    violation_codes: tuple[RealizationViolationCode, ...]
    violation_findings: tuple[CodexRealizationViolationAnchorDraft, ...]
    review_severity: CreatorReviewSeverity
    issue_owner: ReviewIssueOwner
    reason_codes: tuple[str, ...]
    creator_reason: str

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("Codex verifier draft version changed")
        for value in self.verified_beat_ids:
            if value.kind is not IdKind.BEAT:
                raise IdentityError("verified beat has the wrong ID kind")
        for value in self.verified_participant_ids:
            if value.kind is not IdKind.CHARACTER:
                raise IdentityError("verified participant has the wrong ID kind")
        for value in self.verified_development_atom_ids:
            if value.kind is not IdKind.DEVELOPMENT:
                raise IdentityError("verified development atom has the wrong ID kind")
        _unique(self.verified_beat_ids, "verified beats")
        _unique(self.verified_participant_ids, "verified participants")
        _unique(self.verified_boundary_checks, "verified boundary checks")
        _unique(self.verified_development_atom_ids, "verified development atoms")
        _unique(self.violation_codes, "violation codes")
        _unique(self.reason_codes, "creator review reason codes")
        _unique(
            ((value.code, value.quote) for value in self.violation_findings),
            "violation findings",
        )
        finding_codes = {value.code for value in self.violation_findings}
        if not finding_codes.issubset(set(self.violation_codes)):
            raise ContractValidationError(
                "verifier finding code is absent from violation codes"
            )
        protected = RealizationViolationCode.PROTECTED_USER_UNSUPPLIED_REALIZATION
        if protected in self.violation_codes and protected not in finding_codes:
            raise ContractValidationError(
                "protected-user rejection requires an exact candidate quote"
            )
        if self.status is RealizationVerificationStatus.ACCEPTED:
            if self.violation_codes or self.violation_findings:
                raise ContractValidationError(
                    "accepted verifier draft cannot carry violations"
                )
        elif not self.violation_codes:
            raise ContractValidationError(
                "rejected or inconclusive verifier draft requires a safe code"
            )
        if (
            self.review_severity is CreatorReviewSeverity.ERROR
            or not self.creator_reason.strip()
            or len(self.creator_reason) > 1_000
            or any(not value.strip() or len(value) > 96 for value in self.reason_codes)
        ):
            raise ContractValidationError("creator-facing verifier review is invalid")
        if self.review_severity is CreatorReviewSeverity.GOOD:
            if (
                self.status is not RealizationVerificationStatus.ACCEPTED
                or self.issue_owner is not ReviewIssueOwner.NONE
                or self.reason_codes
            ):
                raise ContractValidationError("good review fields are inconsistent")
        elif self.issue_owner is ReviewIssueOwner.NONE or not self.reason_codes:
            raise ContractValidationError(
                "review concern requires an owner and safe reason code"
            )
        if (
            self.status is not RealizationVerificationStatus.ACCEPTED
            and self.review_severity is not CreatorReviewSeverity.CRITICAL
        ):
            raise ContractValidationError(
                "nonaccepted semantic verification must be a critical review"
            )

    @property
    def draft_sha256(self) -> str:
        return domain_sha256(self.SCHEMA_VERSION, self)


class CodexVerifierDraftSemanticError(ContractValidationError):
    def __init__(self, diagnostics: tuple[str, ...]) -> None:
        self.diagnostics = diagnostics
        super().__init__("Codex verifier draft failed semantic validation")


class CodexSceneRealizationVerifierPort:
    """Use one isolated Sol-medium call to inspect, never realize, prose."""

    adapter_version = CODEX_REALIZATION_VERIFIER_ADAPTER_VERSION
    qualification_eligible = True

    def __init__(self, transport: CodexSDKTransport) -> None:
        route = transport.route
        if (
            route.role is not EvaluationRole.SCENE_REALIZATION_VERIFIER
            or route.provider is not ProviderName.OPENAI_CODEX
            or route.model_name != "gpt-5.6-sol"
            or route.reasoning_effort != "medium"
        ):
            raise ContractValidationError(
                "live realization verifier requires the Sol-medium verifier route"
            )
        self.transport = transport

    def verify(
        self,
        request: SceneRealizationVerificationRequest,
    ) -> SceneRealizationVerifierCall:
        packet = build_codex_realization_verifier_packet(request)
        prompt = build_codex_realization_verifier_prompt(packet)
        try:
            provider_result = self.transport.invoke(
                prompt,
                output_schema=codex_realization_verifier_draft_json_schema(),
                mcp_binding=None,
            )
        except ProviderTransportError as exc:
            raise SceneRealizationVerificationFailure(
                "Sol realization verifier dispatch failed",
                safe_diagnostics=(
                    f"provider:{exc.code.value}",
                    *exc.safe_diagnostics,
                ),
                external_provider_calls=exc.external_provider_calls_observed,
            ) from exc

        try:
            payload = provider_result.parsed_json
            if payload is None:
                raise ContractValidationError(
                    "Sol realization verifier omitted its JSON object"
                )
            payload = _upgrade_legacy_verifier_payload(payload)
            validate_codex_realization_verifier_payload_semantics(payload)
            provider_draft = from_mapping(
                CodexSceneRealizationVerifierDraftV1,
                payload,
            )
            findings = tuple(
                _compile_violation_finding(request.story_text, value)
                for value in provider_draft.violation_findings
            )
            draft = SceneRealizationVerificationDraft(
                status=provider_draft.status,
                verified_beat_ids=provider_draft.verified_beat_ids,
                verified_participant_ids=provider_draft.verified_participant_ids,
                violation_codes=tuple(
                    value.value for value in provider_draft.violation_codes
                ),
                verified_boundary_checks=provider_draft.verified_boundary_checks,
                violation_findings=findings,
                verified_development_atom_ids=(
                    provider_draft.verified_development_atom_ids
                ),
            )
            review_assessment = CreatorReviewAssessment(
                schema_version=CreatorReviewAssessment.SCHEMA_VERSION,
                severity=provider_draft.review_severity,
                publication_eligibility=(
                    PublicationEligibility.ACCEPT_ALLOWED
                    if provider_draft.status is RealizationVerificationStatus.ACCEPTED
                    else PublicationEligibility.ACCEPT_BLOCKED
                ),
                issue_owner=provider_draft.issue_owner,
                reason_codes=provider_draft.reason_codes,
                creator_reason=provider_draft.creator_reason,
                verifier_status=provider_draft.status.value,
            )
        except (ContractValidationError, IdentityError) as exc:
            diagnostics = getattr(exc, "diagnostics", ())
            raise SceneRealizationVerificationFailure(
                "Sol realization verifier output failed authoritative decoding",
                provider_call_receipt=provider_result.receipt,
                safe_diagnostics=tuple(diagnostics),
            ) from exc

        return SceneRealizationVerifierCall(
            draft=draft,
            adapter_role=RealizationVerifierAdapterRole.CODEX,
            adapter_version=self.adapter_version,
            adapter_evidence_id=self.transport.route.route_id,
            adapter_evidence_sha256=self.transport.route.route_sha256,
            qualification_eligible=True,
            external_provider_calls=1,
            provider_call_receipt=provider_result.receipt,
            creator_review_assessment=review_assessment,
        )


def build_codex_realization_verifier_packet(
    request: SceneRealizationVerificationRequest,
) -> dict[str, object]:
    """Build the bounded transient semantic evidence packet."""

    return {
        "schema_version": CODEX_REALIZATION_VERIFIER_PACKET_VERSION,
        "prompt_version": CODEX_REALIZATION_VERIFIER_PROMPT_VERSION,
        "task": "semantic_verification_only_no_prose_generation",
        "candidate_story_text": request.story_text,
        "expected_beats": [to_primitive(value) for value in request.expected_beats],
        "selected_participant_ids": [
            str(value) for value in request.selected_participant_ids
        ],
        "protected_user": {
            "character_id": str(request.protected_user_id),
            "authorities": [
                {
                    "source_unit_id": str(value.source_unit_id),
                    "exact_text": value.exact_text,
                    "allowed_kinds": [item.value for item in value.allowed_kinds],
                    "exact_claims": [
                        {
                            "kind": claim.kind.value,
                            "exact_text": claim.exact_text,
                        }
                        for claim in value.claims
                    ],
                }
                for value in request.protected_user_authorities
            ],
        },
        "required_boundary_checks": [
            value.value for value in request.required_boundary_checks
        ],
        "composer_claimed_anchors_advisory_only": [
            to_primitive(value) for value in request.realization_anchors
        ],
        "hard_boundaries": list(request.hard_boundaries),
        "established_scene_context": list(
            request.established_scene_context
        ),
        "authoritative_evidence_context": list(
            request.authoritative_evidence_context
        ),
        "development_expectations": [
            to_primitive(value) for value in request.development_expectations
        ],
        "authority_policy": {
            "composer_metadata_is_not_semantic_proof": True,
            "protected_user_actions_require_exact_source_entailment": True,
            "protected_user_thoughts_feelings_and_dialogue_cannot_be_invented": True,
            "inspect_only_do_not_generate_or_revise_prose": True,
            "unknown_or_ambiguous_means_inconclusive": True,
            "npc_reaction_to_supplied_source_is_not_protected_user_authorship": True,
            "quality_severity_is_separate_from_publication_eligibility": True,
            "development_atoms_are_provisional_until_verified_and_accepted": True,
            "concrete_world_claims_require_authoritative_evidence_review": True,
        },
    }


def build_codex_realization_verifier_prompt(packet: dict[str, object]) -> str:
    return """\
You are an independent CERA semantic verifier. Inspect the supplied candidate;
do not continue, rewrite, repair, improve, or propose replacement story prose.

Decide only from the packet. Composer anchors are advisory claims, not proof.
For each expected beat, verify that its neutral event is visibly realized in
the candidate. For each selected participant, verify that the character is
meaningfully realized rather than merely named.

For each development_expectation, include its atom_id in
verified_development_atom_ids only when the candidate visibly supports that
exact small change and remains within its inference_limit. Do not infer an
emotion from physiology, an interpretation from emotion, attachment from
attraction, jealousy from disappointment, love from attachment, clinical
pregnancy from a home test, or any later strength level. Omit unsupported atoms;
omission is not by itself a hard semantic rejection. Return an empty array when
the packet has no development expectations or none is visibly established.

The protected user is special: exact_claims are the sole allowances for directly
narrating new protected-user behavior in this reply; surrounding exact_text is
context and grants no additional protected-user action or dialogue.
established_scene_context contains Python-authorized facts that were already
true before this reply, such as a completed doorbell ring, established location,
or accepted prior event. The candidate may faithfully refer back to those facts
or preserve their unchanged state. They do not authorize repeating, advancing,
or inventing a new protected-user action, dialogue, thought, feeling, or choice.
A selected NPC may perceive,
remember, interpret, or react to the already-visible supplied source. Generic
anaphora such as "his words," "the question," "seeing him," or "what he asked"
is not protected-user authorship when it only points back to exact_text and adds
no new behavior, private state, or choice. Physical presence, location, waiting,
or speech already explicit in exact_text may be referred to or faithfully
entailed as background continuity; those references do not advance the protected
user and are not violations. Reject only a newly added, story-progressing
protected-user behavior or private state beyond exact_text. A third-person
pronoun or an NPC's focalized perception is not itself a violation. The anchored
finding must contain the invented protected-user behavior, not merely the NPC's
reaction around it. For example, an NPC reacting to a supplied question is
permitted; a new unsupplied user entrance, touch, reply, decision, or feeling is
not. Every directly attributed action,
dialogue, thought,
feeling, choice, consent, movement, gaze, touch, or other consequential
realization must be semantically entailed by one supplied exact source unit and
must use an allowed realization kind. Ordinary grammatical connective tissue
is acceptable only when it adds no new protected-user choice or behavior.

authoritative_evidence_context is the exact, bounded factual and character
evidence that the Composer received. Audit every concrete candidate claim about
the world or established continuity against exact source claims,
established_scene_context, expected beats, and this evidence. This includes
spatial direction or adjacency, object location, possession, schedules, time
estimates, routines, procedures, rules, roles, history, frequency, prior
familiarity, meals, chores, trips, school, work, health, and access details.
A statement is not supported merely because it is conventional, plausible, or
a likely implementation of an authorized category. Do not infer an inverse,
converse, default, exception, or extra procedure from an authoritative rule.
For example, a rule protecting labelled or clearly reserved food does not by
itself establish that all unlabeled food is communal.

An unsupported new world fact is a creator-review quality problem, not
automatically protected-user authorship. If the otherwise semantic result is
accepted, use concern or critical severity, issue_owner composer, stable reason
code unsupported_world_fact, and identify the unsupported claim concisely in
creator_reason. Use critical for a continuity contradiction, invented durable
history or role, or a material rule or procedure that could alter canon. Do not
mark a review good while any unsupported concrete world claim remains.

Status rules:
- accepted: return every expected beat ID in packet order, every selected
  participant ID, every required boundary check, and no violation.
- rejected: use a safe violation code. A protected-user unsupplied realization
  must include an exact non-whitespace quote from candidate_story_text that
  uniquely identifies the violating passage.
- inconclusive: use verifier_evidence_insufficient or
  required_boundary_not_verifiable; do not guess.

Separately assess realization quality for creator review. review_severity is
good, concern, or critical. A concern or critical quality judgment does not by
itself change an otherwise accepted semantic status. Use critical for a major
character, sequence, continuity, proportionality, or handoff problem; Python
will separately decide publication eligibility. Evaluate omitted or distorted
beats, unsupported new causality, excessive/repetitive expansion, character
consistency against supplied evidence, and whether the intended handoff was
preserved. Use issue_owner to locate the likely source (reasoner, composer,
mixed, prompt_material, or hard_boundary). Use short stable reason_codes and
one concise creator_reason without hidden reasoning. A good review uses owner
none and no reason codes. A concern or critical review requires a non-none owner
and at least one reason code. Any rejected or inconclusive status must use
critical severity.

For every finding, occurrence must be literal 0. If a short quote repeats,
extend it with adjacent candidate text until it occurs exactly once. Do not
calculate offsets or hashes; Python owns them. Arrays represent semantic sets
and must not contain duplicates. Return only the schema-conforming JSON object.

PACKET:
""" + canonical_json(packet)


def validate_codex_realization_verifier_payload_semantics(
    payload: dict[str, object],
) -> None:
    """Reject cross-field/provider-set failures using value-free diagnostics."""

    diagnostics: list[str] = []
    status = payload.get("status")
    codes = payload.get("violation_codes")
    findings = payload.get("violation_findings")
    for field_name in (
        "verified_beat_ids",
        "verified_participant_ids",
        "verified_boundary_checks",
        "verified_development_atom_ids",
        "violation_codes",
    ):
        value = payload.get(field_name)
        if isinstance(value, list) and len(value) != len(
            {canonical_json(item) for item in value}
        ):
            diagnostics.append(f"{field_name}:duplicate_items")
    if isinstance(findings, list):
        finding_keys = [
            canonical_json(
                {
                    "code": value.get("code"),
                    "quote": value.get("quote"),
                }
            )
            for value in findings
            if isinstance(value, dict)
        ]
        if len(finding_keys) != len(set(finding_keys)):
            diagnostics.append("violation_findings:duplicate_items")
    if status == RealizationVerificationStatus.ACCEPTED.value:
        if codes:
            diagnostics.append("violation_codes:forbidden_for_accepted")
        if findings:
            diagnostics.append("violation_findings:forbidden_for_accepted")
    elif status in {
        RealizationVerificationStatus.REJECTED.value,
        RealizationVerificationStatus.INCONCLUSIVE.value,
    }:
        if not codes:
            diagnostics.append("violation_codes:required_for_nonaccepted")
    if isinstance(codes, list) and (
        RealizationViolationCode.SEMANTIC_VERIFIER_NOT_CONFIGURED.value in codes
    ):
        diagnostics.append("violation_codes:python_owned_code")
    protected = RealizationViolationCode.PROTECTED_USER_UNSUPPLIED_REALIZATION.value
    if isinstance(codes, list) and protected in codes:
        matching = (
            isinstance(findings, list)
            and any(
                isinstance(value, dict) and value.get("code") == protected
                for value in findings
            )
        )
        if not matching:
            diagnostics.append(
                "violation_findings:required_for_protected_user_violation"
            )
    if diagnostics:
        raise CodexVerifierDraftSemanticError(tuple(dict.fromkeys(diagnostics)))


def _upgrade_legacy_verifier_payload(
    payload: dict[str, object],
) -> dict[str, object]:
    """Decode immutable v1/v2 qualification fixtures through the active v3 DTO."""

    legacy_version = payload.get("schema_version")
    if legacy_version not in {
        "cera.codex_scene_realization_verifier_draft.v1",
        "cera.codex_scene_realization_verifier_draft.v2",
    }:
        return payload
    upgraded = dict(payload)
    if legacy_version.endswith(".v1"):
        status = upgraded.get("status")
        if status == RealizationVerificationStatus.ACCEPTED.value:
            upgraded.update(
                {
                    "review_severity": CreatorReviewSeverity.GOOD.value,
                    "issue_owner": ReviewIssueOwner.NONE.value,
                    "reason_codes": [],
                    "creator_reason": (
                        "Good - the accepted sequence and required boundaries were realized."
                    ),
                }
            )
        else:
            codes = upgraded.get("violation_codes")
            upgraded.update(
                {
                    "review_severity": CreatorReviewSeverity.CRITICAL.value,
                    "issue_owner": (
                        ReviewIssueOwner.HARD_BOUNDARY.value
                        if status == RealizationVerificationStatus.REJECTED.value
                        else ReviewIssueOwner.VERIFIER.value
                    ),
                    "reason_codes": list(codes) if isinstance(codes, list) else [],
                    "creator_reason": (
                        "Critical - semantic verification did not authorize publication."
                    ),
                }
            )
    upgraded["schema_version"] = CODEX_REALIZATION_VERIFIER_DRAFT_VERSION
    upgraded["verified_development_atom_ids"] = []
    return upgraded


def codex_realization_verifier_draft_json_schema() -> dict[str, object]:
    short_quote = {
        "type": "string",
        "minLength": 1,
        "maxLength": 1_024,
        "description": (
            "Exact non-whitespace quote that occurs once in candidate_story_text; "
            "extend repeated text until unique."
        ),
    }
    violation_code = {
        "type": "string",
        "enum": [value.value for value in _MODEL_REPORTABLE_CODES],
    }
    finding = _closed(
        {
            "code": violation_code,
            "quote": short_quote,
            "occurrence": {
                "type": "integer",
                "enum": [0],
                "description": "Literal zero sentinel; Python derives occurrence and offsets.",
            },
        }
    )
    return _closed(
        {
            "schema_version": {
                "type": "string",
                "const": CODEX_REALIZATION_VERIFIER_DRAFT_VERSION,
            },
            "status": {
                "type": "string",
                "enum": [value.value for value in RealizationVerificationStatus],
            },
            "verified_beat_ids": _id_array(
                IdKind.BEAT,
                maximum=32,
                description="Distinct verified beat IDs in packet order.",
            ),
            "verified_participant_ids": _id_array(
                IdKind.CHARACTER,
                maximum=8,
                description="Distinct selected participants visibly realized.",
            ),
            "verified_boundary_checks": {
                "type": "array",
                "maxItems": 8,
                "description": "Distinct required semantic boundaries actually checked.",
                "items": {
                    "type": "string",
                    "enum": [value.value for value in RealizationBoundaryCheck],
                },
            },
            "verified_development_atom_ids": _id_array(
                IdKind.DEVELOPMENT,
                maximum=24,
                description=(
                    "Distinct provisional development atoms visibly supported by the prose."
                ),
            ),
            "violation_codes": {
                "type": "array",
                "maxItems": 8,
                "description": "Distinct privacy-safe semantic violation codes.",
                "items": violation_code,
            },
            "violation_findings": {
                "type": "array",
                "maxItems": 16,
                "description": "Distinct exact candidate anchors for present-text violations.",
                "items": finding,
            },
            "review_severity": {
                "type": "string",
                "enum": [
                    CreatorReviewSeverity.GOOD.value,
                    CreatorReviewSeverity.CONCERN.value,
                    CreatorReviewSeverity.CRITICAL.value,
                ],
            },
            "issue_owner": {
                "type": "string",
                "enum": [value.value for value in ReviewIssueOwner],
            },
            "reason_codes": {
                "type": "array",
                "maxItems": 12,
                "items": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 96,
                    "pattern": "^[a-z][a-z0-9_]{0,95}$",
                },
            },
            "creator_reason": {
                "type": "string",
                "minLength": 1,
                "maxLength": 1_000,
            },
        }
    )


def _compile_violation_finding(
    story_text: str,
    draft: CodexRealizationViolationAnchorDraft,
) -> SceneRealizationViolationFinding:
    start = story_text.find(draft.quote)
    if start < 0:
        raise ContractValidationError(
            "verifier finding quote is absent from candidate prose"
        )
    if story_text.find(draft.quote, start + 1) >= 0:
        raise ContractValidationError(
            "verifier finding quote is ambiguous in candidate prose"
        )
    end = start + len(draft.quote)
    from cera.serialization import text_sha256

    return SceneRealizationViolationFinding(
        code=draft.code.value,
        start=start,
        end=end,
        text_sha256=text_sha256(story_text[start:end]),
    )


def _id_array(
    kind: IdKind,
    *,
    maximum: int,
    description: str,
) -> dict[str, object]:
    return {
        "type": "array",
        "maxItems": maximum,
        "description": description,
        "items": {
            "type": "string",
            "pattern": rf"^{kind.value}:[A-Za-z0-9][A-Za-z0-9._-]{{0,127}}$",
        },
    }


def _closed(properties: dict[str, object]) -> dict[str, object]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def _unique(values, field_name: str) -> None:
    items = list(values)
    if len(items) != len(set(items)):
        raise ContractValidationError(f"{field_name} must not contain duplicates")
