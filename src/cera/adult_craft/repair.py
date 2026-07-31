"""One-attempt provider-free beat-scoped repair mechanics.

The fake port is declarative: it returns a test fixture and contains no prose,
scenario, or content-generation policy.  The coordinator locks all non-target
text and mechanically rebases structural spans for full Composer revalidation.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Protocol

from cera.composer.models import (
    ComposerCandidate,
    ComposerSubmission,
    RealizationKind,
    RealizationManifest,
    RealizationSpan,
    SceneComposerRequest,
    SourceUnitCoverage,
)
from cera.contracts import BeatState
from cera.errors import ContractValidationError
from cera.ids import IdKind, TypedId, deterministic_id
from cera.serialization import text_sha256

from .models import (
    BeatScopedRepairReceipt,
    BeatScopedRepairRequest,
    SemanticSpecificityReceipt,
    SemanticSpecificityResult,
    SpecificityContract,
    SpecificityValidationReceipt,
)
from .specificity import SpecificityValidator


@dataclass(frozen=True, slots=True)
class ReplacementSpan:
    start: int
    end: int
    owner_id: TypedId
    kind: RealizationKind
    source_unit_id: TypedId | None
    beat_id: TypedId
    realized_state: BeatState | None

    def __post_init__(self) -> None:
        if self.start < 0 or self.end <= self.start:
            raise ContractValidationError("replacement span is invalid")
        if self.owner_id.kind is not IdKind.CHARACTER:
            raise ContractValidationError("replacement span owner must be a character")
        if self.source_unit_id is not None and self.source_unit_id.kind is not IdKind.SOURCE_UNIT:
            raise ContractValidationError("replacement span source must be a source unit")
        if self.beat_id.kind is not IdKind.BEAT:
            raise ContractValidationError("replacement span beat must be a beat ID")


@dataclass(frozen=True, slots=True)
class BeatScopedRepairProposal:
    replacement_text: str
    replacement_spans: tuple[ReplacementSpan, ...]

    def __post_init__(self) -> None:
        if not self.replacement_text.strip() or not self.replacement_spans:
            raise ContractValidationError("beat repair proposal requires text and spans")
        for span in self.replacement_spans:
            if span.end > len(self.replacement_text):
                raise ContractValidationError("replacement span exceeds replacement text")


class BeatScopedRepairPort(Protocol):
    def repair(self, request: BeatScopedRepairRequest) -> BeatScopedRepairProposal: ...


class FakeBeatScopedRepairPort:
    production_prohibited = True

    def __init__(self, proposal: BeatScopedRepairProposal) -> None:
        self.proposal = proposal
        self.call_count = 0

    def repair(self, request: BeatScopedRepairRequest) -> BeatScopedRepairProposal:
        self.call_count += 1
        if self.call_count > 1:
            raise ContractValidationError("fake beat repair cannot be called recursively")
        return self.proposal


@dataclass(frozen=True, slots=True)
class BeatScopedRepairResult:
    request: BeatScopedRepairRequest
    submission: ComposerSubmission
    specificity_receipt: SpecificityValidationReceipt
    receipt: BeatScopedRepairReceipt


class BeatScopedRepairCoordinator:
    def __init__(self, validator: SpecificityValidator | None = None) -> None:
        self.validator = validator or SpecificityValidator()

    def build_request(
        self,
        composer_request: SceneComposerRequest,
        submission: ComposerSubmission,
        specificity_contract: SpecificityContract,
        specificity_receipt: SpecificityValidationReceipt,
        semantic_result: SemanticSpecificityResult,
        semantic_receipt: SemanticSpecificityReceipt,
        target_beat_id: TypedId,
    ) -> BeatScopedRepairRequest:
        repairable = set(specificity_receipt.repairable_beat_ids) | set(
            semantic_result.repairable_beat_ids
        )
        if specificity_receipt.status != "repair_required" and semantic_result.status != "repair_required":
            raise ContractValidationError("beat repair requires a failed specificity verifier")
        if target_beat_id not in repairable:
            raise ContractValidationError("target beat is not repairable")
        start, end = _target_range(submission.manifest, target_beat_id)
        original = submission.candidate.story_text[start:end]
        key = (
            f"{composer_request.request_sha256}|{submission.candidate.candidate_sha256}|"
            f"{submission.manifest.manifest_sha256}|{semantic_receipt.receipt_sha256}|"
            f"{target_beat_id}|{start}|{end}"
        )
        return BeatScopedRepairRequest(
            schema_version=BeatScopedRepairRequest.SCHEMA_VERSION,
            repair_request_id=deterministic_id(
                IdKind.BEAT_REPAIR,
                "cera.beat_scoped_repair.request.v2",
                key,
            ),
            request_id=composer_request.prepared_turn.request.request_id,
            target_beat_id=target_beat_id,
            composer_request_sha256=composer_request.request_sha256,
            original_candidate_sha256=submission.candidate.candidate_sha256,
            original_manifest_sha256=submission.manifest.manifest_sha256,
            specificity_contract_sha256=specificity_contract.contract_sha256,
            specificity_validation_receipt_sha256=specificity_receipt.receipt_sha256,
            semantic_specificity_receipt_sha256=semantic_receipt.receipt_sha256,
            original_span_start=start,
            original_span_end=end,
            original_span_sha256=text_sha256(original),
            attempt=1,
            maximum_attempts=1,
            non_target_content_locked=True,
            full_reply_replacement_forbidden=True,
            story_state_committed=False,
        )

    def execute(
        self,
        request: BeatScopedRepairRequest,
        composer_request: SceneComposerRequest,
        original: ComposerSubmission,
        specificity_contract: SpecificityContract,
        semantic_receipt: SemanticSpecificityReceipt,
        port: BeatScopedRepairPort,
    ) -> BeatScopedRepairResult:
        _validate_request_bindings(
            request,
            composer_request,
            original,
            specificity_contract,
            semantic_receipt,
        )
        proposal = port.repair(request)
        submission = _splice(request, composer_request, original, proposal)
        validation = self.validator.validate(
            specificity_contract,
            submission.candidate,
            submission.manifest,
        )
        accepted = validation.status == "accepted"
        key = f"{request.request_sha256}|{submission.candidate.candidate_sha256}|{validation.receipt_sha256}"
        receipt = BeatScopedRepairReceipt(
            schema_version=BeatScopedRepairReceipt.SCHEMA_VERSION,
            repair_receipt_id=deterministic_id(
                IdKind.BEAT_REPAIR,
                "cera.beat_scoped_repair.receipt.v1",
                key,
            ),
            repair_request_sha256=request.request_sha256,
            original_candidate_sha256=original.candidate.candidate_sha256,
            repaired_candidate_sha256=submission.candidate.candidate_sha256,
            repaired_manifest_sha256=submission.manifest.manifest_sha256,
            specificity_validation_receipt_sha256=validation.receipt_sha256,
            status="accepted_for_full_revalidation" if accepted else "rejected",
            attempt_count=1,
            recursive_retry_permitted=False,
            external_provider_calls=0,
            story_state_committed=False,
        )
        return BeatScopedRepairResult(request, submission, validation, receipt)


def _validate_request_bindings(
    request, composer_request, original, contract, semantic_receipt
) -> None:
    if request.composer_request_sha256 != composer_request.request_sha256:
        raise ContractValidationError("beat repair request changed Composer request")
    if request.original_candidate_sha256 != original.candidate.candidate_sha256:
        raise ContractValidationError("beat repair request changed original candidate")
    if request.original_manifest_sha256 != original.manifest.manifest_sha256:
        raise ContractValidationError("beat repair request changed original manifest")
    if request.specificity_contract_sha256 != contract.contract_sha256:
        raise ContractValidationError("beat repair request changed specificity contract")
    if request.semantic_specificity_receipt_sha256 != semantic_receipt.receipt_sha256:
        raise ContractValidationError("beat repair request changed semantic verifier receipt")
    original_text = original.candidate.story_text[
        request.original_span_start : request.original_span_end
    ]
    if text_sha256(original_text) != request.original_span_sha256:
        raise ContractValidationError("beat repair original span hash mismatch")


def _target_range(manifest: RealizationManifest, beat_id: TypedId) -> tuple[int, int]:
    spans = [value for value in manifest.character_spans if value.beat_id == beat_id]
    if not spans:
        raise ContractValidationError("target beat has no realization span")
    start = min(value.start for value in spans)
    end = max(value.end for value in spans)
    for value in manifest.character_spans:
        if value.beat_id != beat_id and value.start < end and value.end > start:
            raise ContractValidationError("target beat overlaps a non-target realization")
    return start, end


def _splice(request, composer_request, original, proposal) -> ComposerSubmission:
    start, end = request.original_span_start, request.original_span_end
    old_text = original.candidate.story_text
    new_text = old_text[:start] + proposal.replacement_text + old_text[end:]
    delta = len(proposal.replacement_text) - (end - start)
    target = request.target_beat_id
    selected = set(composer_request.selected_npc_ids)
    replacement_spans: list[RealizationSpan] = []
    for value in proposal.replacement_spans:
        if value.beat_id != target or value.owner_id not in selected:
            raise ContractValidationError("repair proposal escaped target beat or selected cast")
        replacement_spans.append(
            RealizationSpan(
                start=start + value.start,
                end=start + value.end,
                owner_id=value.owner_id,
                kind=value.kind,
                source_unit_id=value.source_unit_id,
                beat_id=value.beat_id,
                realized_state=value.realized_state,
            )
        )
    kept_spans: list[RealizationSpan] = []
    for value in original.manifest.character_spans:
        if value.beat_id == target:
            continue
        if value.end <= start:
            kept_spans.append(value)
        elif value.start >= end:
            kept_spans.append(replace(value, start=value.start + delta, end=value.end + delta))
        else:
            raise ContractValidationError("repair would alter a non-target realization span")
    all_spans = tuple(sorted((*kept_spans, *replacement_spans), key=lambda value: (value.start, value.end)))
    coverage = tuple(_rebase_coverage(value, start, end, delta) for value in original.manifest.source_unit_coverage)
    if any(
        value.beat_id == target
        for value in original.manifest.adult_specificity_coverage
    ):
        raise ContractValidationError(
            "active adult specificity coverage requires a typed replacement mapping"
        )
    candidate = ComposerCandidate(
        schema_version=ComposerCandidate.SCHEMA_VERSION,
        candidate_id=deterministic_id(
            IdKind.COMPOSER_CANDIDATE,
            "cera.beat_scoped_repair.candidate.v1",
            f"{request.request_sha256}|{text_sha256(new_text)}",
        ),
        story_text=new_text,
        story_text_sha256=text_sha256(new_text),
        complete_core=original.candidate.complete_core,
        is_outline=original.candidate.is_outline,
        is_partial_draft=original.candidate.is_partial_draft,
        awaits_detailer=original.candidate.awaits_detailer,
        contains_internal_labels=original.candidate.contains_internal_labels,
        contains_ui_markup=original.candidate.contains_ui_markup,
        contains_provider_diagnostics=original.candidate.contains_provider_diagnostics,
    )
    manifest = RealizationManifest(
        schema_version=RealizationManifest.SCHEMA_VERSION,
        manifest_id=deterministic_id(
            IdKind.REALIZATION_MANIFEST,
            "cera.beat_scoped_repair.manifest.v1",
            f"{request.request_sha256}|{candidate.candidate_sha256}",
        ),
        candidate_sha256=candidate.candidate_sha256,
        decision_sha256=original.manifest.decision_sha256,
        sequence_plan_sha256=original.manifest.sequence_plan_sha256,
        floor_owner_id=original.manifest.floor_owner_id,
        move_realizations=original.manifest.move_realizations,
        participant_realizations=original.manifest.participant_realizations,
        realized_beat_ids=original.manifest.realized_beat_ids,
        source_unit_coverage=coverage,
        character_spans=all_spans,
        semantic_inferences=original.manifest.semantic_inferences,
        terminal_state_preserved=original.manifest.terminal_state_preserved,
        stops_before_protected_user_choice=original.manifest.stops_before_protected_user_choice,
        introduced_major_objective_or_participant=(
            original.manifest.introduced_major_objective_or_participant
        ),
        adult_specificity_coverage=original.manifest.adult_specificity_coverage,
    )
    return ComposerSubmission(candidate, manifest, original.advisory_realization_metadata)


def _rebase_coverage(value: SourceUnitCoverage, start: int, end: int, delta: int):
    rebased = tuple(
        _rebase_range(item, start, end, delta) for item in value.ranges
    )
    return replace(
        value,
        start=rebased[0].start,
        end=rebased[0].end,
        additional_ranges=rebased[1:],
    )


def _rebase_range(value, start: int, end: int, delta: int):
    from cera.composer.models import StoryTextRange

    if value.end <= start:
        return value
    if value.start >= end:
        return StoryTextRange(value.start + delta, value.end + delta)
    if value.start <= start and value.end >= end:
        return StoryTextRange(value.start, value.end + delta)
    raise ContractValidationError("beat repair crosses a source-coverage boundary")
