"""Python-owned structural candidate acceptance and pure rendering."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Protocol

from cera.contracts import AcceptedStoryArtifact, ErrorEnvelope, SourceUnitClassification
from cera.errors import ContractValidationError, ErrorCode, RetryMode
from cera.ids import IdKind, TypedId, deterministic_id
from cera.serialization import canonical_sha256, domain_sha256, text_sha256

from .fake import (
    FakeComposerFixture,
    SceneComposerPort,
    SceneComposerPortFailure,
    SceneComposerUnavailable,
)
from .obligations import build_composer_output_obligations
from .models import (
    ArtifactPublicationMode,
    ComposerExecutionResult,
    ComposerSubmission,
    SceneComposerAdapterCall,
    ComposerValidationReceipt,
    RealizationKind,
    RenderedStory,
    SceneComposerReceipt,
    SceneComposerRequest,
    SemanticCategory,
)


_FORBIDDEN_INFERENCES = frozenset(
    {
        *(
            (source, target)
            for source in (
                SemanticCategory.BODILY_RESPONSE,
                SemanticCategory.VOCALIZATION,
                SemanticCategory.FREEZE,
                SemanticCategory.SILENCE,
                SemanticCategory.COMPLIANCE,
                SemanticCategory.FAILURE_TO_RESIST,
            )
            for target in (
                SemanticCategory.DESIRE,
                SemanticCategory.ATTRACTION,
                SemanticCategory.PLEASURE,
                SemanticCategory.CONSENT,
            )
        ),
        (SemanticCategory.DESIRE, SemanticCategory.CONSENT),
        (SemanticCategory.PLEASURE, SemanticCategory.CONSENT),
        (SemanticCategory.ATTRACTION, SemanticCategory.CONSENT),
        (SemanticCategory.BELIEF, SemanticCategory.OBJECTIVE_FACT),
        (SemanticCategory.ALLEGATION, SemanticCategory.OBJECTIVE_FACT),
        (SemanticCategory.SUSPICION, SemanticCategory.OBJECTIVE_FACT),
        (SemanticCategory.UNCERTAINTY, SemanticCategory.CERTAINTY),
        (SemanticCategory.UNCERTAINTY, SemanticCategory.OBJECTIVE_FACT),
    }
)
_INTERNAL_LABEL = re.compile(
    r"(?im)^\s*(?:\[?(?:plan|reasoning|diagnostics?|manifest|beat\s*id)\]?\s*:|#+\s*(?:plan|reasoning|diagnostics?|manifest)\b)"
)
_HTML = re.compile(r"<\/?[A-Za-z][^>]*>")


class ComposerExecutionFailure(Exception):
    def __init__(
        self,
        envelope: ErrorEnvelope,
        *,
        provider_call_receipt=None,
        safe_diagnostics: tuple[str, ...] = (),
        external_provider_calls_observed: int = 0,
    ) -> None:
        if (
            type(external_provider_calls_observed) is not int
            or external_provider_calls_observed not in {0, 1}
        ):
            raise ContractValidationError(
                "composer failure provider-call observation must be zero or one"
            )
        if any(
            not isinstance(value, str) or not value.strip()
            for value in safe_diagnostics
        ):
            raise ContractValidationError(
                "composer failure diagnostics must be non-empty strings"
            )
        if (
            provider_call_receipt is not None
            and external_provider_calls_observed != 1
        ):
            raise ContractValidationError(
                "composer failure receipt requires one observed provider call"
            )
        self.envelope = envelope
        self.provider_call_receipt = provider_call_receipt
        self.safe_diagnostics = tuple(safe_diagnostics)
        self.external_provider_calls_observed = external_provider_calls_observed
        self.failure_evidence_bundle = None
        self.retained_evidence_handles = ()
        super().__init__(envelope.message)


class ComposerCoordinator:
    def execute(
        self,
        request: SceneComposerRequest,
        port: SceneComposerPort,
        *,
        fixture: FakeComposerFixture | None = None,
    ) -> ComposerExecutionResult:
        port_fixture = getattr(port, "fixture", None)
        if (port_fixture is None) != (fixture is None) or (
            fixture is not None and port_fixture != fixture
        ):
            raise self._failure(request, "fake adapter fixture does not match receipt fixture")
        try:
            adapter_call = port.compose(request)
            if not isinstance(adapter_call, SceneComposerAdapterCall):
                raise TypeError("Composer port omitted adapter-call evidence")
            submission = adapter_call.submission
            expected_obligation_sha256 = (
                build_composer_output_obligations(
                    request
                ).obligation_sha256
            )
            provider_atomic_obligations_validated = (
                adapter_call.adapter_role.value
                == "deepseek_scene_composer"
                and adapter_call.validated_obligation_sha256
                == expected_obligation_sha256
            )
            if (
                adapter_call.adapter_role.value
                == "deepseek_scene_composer"
                and not provider_atomic_obligations_validated
            ):
                raise self._failure(
                    request,
                    "Composer adapter obligation binding is invalid",
                    code=ErrorCode.INTERNAL_CONTRACT_INVARIANT_FAILED,
                    details=("COMPOSER_OBLIGATION_BINDING_DIVERGENCE",),
                )
            self._validate_submission(
                request,
                submission,
                require_explicit_specificity_coverage=(
                    adapter_call.adapter_role.value == "deepseek_scene_composer"
                ),
                provider_atomic_obligations_validated=(
                    provider_atomic_obligations_validated
                ),
            )
        except SceneComposerUnavailable as exc:
            raise self._failure(
                request,
                str(exc),
                code=ErrorCode.COMPOSER_UNAVAILABLE,
                stage="composer_dispatch",
            ) from exc
        except SceneComposerPortFailure as exc:
            failure = self._failure(
                request,
                str(exc),
                code=exc.code,
                stage=exc.stage,
                details=exc.safe_diagnostics,
            )
            failure.provider_call_receipt = exc.provider_call_receipt
            failure.safe_diagnostics = exc.safe_diagnostics
            failure.external_provider_calls_observed = (
                exc.external_provider_calls_observed
            )
            raise failure from exc
        except ComposerExecutionFailure as exc:
            if "adapter_call" in locals():
                exc.provider_call_receipt = adapter_call.provider_call_receipt
                exc.external_provider_calls_observed = (
                    adapter_call.external_provider_calls
                )
            raise
        except Exception as exc:
            raise self._failure(request, f"composer result failed validation: {exc}") from exc

        candidate = submission.candidate
        manifest = submission.manifest
        composer_receipt = SceneComposerReceipt(
            schema_version=SceneComposerReceipt.SCHEMA_VERSION,
            provider_receipt_id=adapter_call.provider_receipt_id,
            adapter_role=adapter_call.adapter_role,
            adapter_version=adapter_call.adapter_version,
            adapter_evidence_id=adapter_call.adapter_evidence_id,
            adapter_evidence_sha256=adapter_call.adapter_evidence_sha256,
            provider_receipt_sha256=adapter_call.provider_receipt_sha256,
            composer_request_sha256=request.request_sha256,
            source_sha256=request.source_packet.source_sha256,
            safe_ledger_sha256=request.source_packet.reasoner_safe_ledger_sha256,
            protected_source_envelope_sha256=request.source_packet.protected_envelope_sha256,
            decision_sha256=request.decision_sha256,
            sequence_plan_sha256=request.sequence_plan_sha256,
            candidate_sha256=candidate.candidate_sha256,
            manifest_sha256=manifest.manifest_sha256,
            outcome="candidate_returned",
            external_provider_calls=adapter_call.external_provider_calls,
        )
        quarantined = tuple(
            value
            for value in submission.advisory_realization_metadata
            if not self._valid_advisory(value)
        )
        retained_advisory = tuple(
            value
            for value in submission.advisory_realization_metadata
            if self._valid_advisory(value)
        )
        quarantine_hashes = tuple(canonical_sha256(value) for value in quarantined)
        validation_id = deterministic_id(
            IdKind.VALIDATION,
            "cera.composer.structural_validation.v1",
            f"{request.request_sha256}|{candidate.candidate_sha256}|{manifest.manifest_sha256}",
        )
        validation_receipt = ComposerValidationReceipt(
            schema_version=ComposerValidationReceipt.SCHEMA_VERSION,
            validation_receipt_id=validation_id,
            composer_request_sha256=request.request_sha256,
            candidate_sha256=candidate.candidate_sha256,
            manifest_sha256=manifest.manifest_sha256,
            validation_kind="structural_candidate_validation",
            status="accepted_in_memory",
            quarantined_advisory_count=len(quarantined),
            quarantined_advisory_sha256=quarantine_hashes,
            semantic_quality_proven=False,
            story_state_committed=False,
        )
        return ComposerExecutionResult(
            candidate=candidate,
            manifest=manifest,
            composer_receipt=composer_receipt,
            validation_receipt=validation_receipt,
            advisory_realization_metadata=retained_advisory,
            quarantined_advisory_metadata=quarantined,
            provider_call_receipt=adapter_call.provider_call_receipt,
        )

    def _validate_submission(
        self,
        request: SceneComposerRequest,
        submission: ComposerSubmission,
        *,
        require_explicit_specificity_coverage: bool = False,
        provider_atomic_obligations_validated: bool = False,
    ) -> None:
        candidate = submission.candidate
        manifest = submission.manifest
        decision = request.reasoner_outcome.decision
        assert decision is not None
        if candidate.is_outline or candidate.is_partial_draft:
            self._invalid(request, "composer did not return a complete Core reply")
        if not candidate.complete_core:
            if provider_atomic_obligations_validated:
                raise self._failure(
                    request,
                    (
                        "Composer complete_core diverged from passing atomic "
                        "obligations"
                    ),
                    code=ErrorCode.INTERNAL_CONTRACT_INVARIANT_FAILED,
                    details=("COMPLETE_CORE_ATOMIC_DIVERGENCE",),
                )
            self._invalid(
                request,
                "composer did not return a complete Core reply",
            )
        if candidate.awaits_detailer:
            self._invalid(request, "candidate depends on a prohibited automatic Detailer")
        if (
            candidate.contains_internal_labels
            or candidate.contains_ui_markup
            or candidate.contains_provider_diagnostics
            or _INTERNAL_LABEL.search(candidate.story_text)
            or _HTML.search(candidate.story_text)
        ):
            self._invalid(request, "candidate leaked non-story presentation or diagnostics")
        if manifest.candidate_sha256 != candidate.candidate_sha256:
            self._invalid(request, "manifest does not bind raw candidate")
        if manifest.decision_sha256 != request.decision_sha256:
            self._invalid(request, "candidate manifest drifted from validated decision")
        if manifest.sequence_plan_sha256 != request.sequence_plan_sha256:
            self._invalid(request, "candidate manifest drifted from SequencePlan")
        if manifest.floor_owner_id != decision.floor_owner_id:
            self._invalid(request, "candidate changed the conversational floor")
        selected = set(request.selected_npc_ids)
        realized = {value.character_id for value in manifest.participant_realizations}
        if realized != selected:
            self._invalid(request, "candidate omitted or added a responding participant")
        move_map = {value.character_id: value for value in manifest.move_realizations}
        if set(move_map) != selected:
            self._invalid(request, "candidate omitted or added a character move")
        for move in decision.character_moves:
            realized_move = move_map[move.character_id]
            if (
                realized_move.selected_intent_sha256 != text_sha256(move.selected_intent)
                or realized_move.action_direction_sha256 != text_sha256(move.action_direction)
            ):
                self._invalid(request, "candidate reversed or replaced reasoner intent")
        expected_beats = tuple(value.beat_id for value in decision.current_segment.ordered_beats)
        if manifest.realized_beat_ids != expected_beats:
            self._invalid(request, "candidate omitted, duplicated, or reordered a required beat")
        if not manifest.terminal_state_preserved:
            self._invalid(request, "candidate did not preserve the supplied terminal state")
        if not manifest.stops_before_protected_user_choice:
            self._invalid(request, "candidate crossed the protected-user choice boundary")
        if manifest.introduced_major_objective_or_participant:
            self._invalid(request, "candidate introduced an unauthorized major addition")
        self._validate_spans(request, submission)
        self._validate_source_coverage(request, submission)
        self._validate_adult_specificity_coverage(
            request,
            submission,
            required=require_explicit_specificity_coverage,
        )
        for relation in manifest.semantic_inferences:
            if (relation.source, relation.target) in _FORBIDDEN_INFERENCES:
                self._invalid(request, "candidate manifest asserted a forbidden semantic inference")

    def _validate_spans(
        self, request: SceneComposerRequest, submission: ComposerSubmission
    ) -> None:
        candidate = submission.candidate
        manifest = submission.manifest
        selected = set(request.selected_npc_ids)
        protected_user = request.prepared_turn.request.protected_user_id
        source_by_id = {value.source_unit_id: value for value in request.source_packet.units}
        expected_beats = {
            value.beat_id for value in request.reasoner_outcome.decision.current_segment.ordered_beats
        }
        for span in manifest.character_spans:
            if span.end > len(candidate.story_text) or not candidate.story_text[span.start:span.end].strip():
                self._invalid(request, "candidate realization span is outside non-empty prose")
            if span.owner_id == protected_user:
                if span.kind in {
                    RealizationKind.PRIVATE_STATE,
                    RealizationKind.EMOTION,
                    RealizationKind.MOTIVE,
                    RealizationKind.CONSENT,
                    RealizationKind.REFUSAL,
                    RealizationKind.REACTION,
                    RealizationKind.DEPARTURE,
                    RealizationKind.DESTINATION,
                    RealizationKind.COMMITMENT,
                }:
                    self._invalid(request, "candidate authored protected-user ownership")
                if span.source_unit_id is None or span.source_unit_id not in source_by_id:
                    self._invalid(request, "protected-user realization lacks exact source authority")
                if span.kind not in source_by_id[span.source_unit_id].protected_user_allowed_kinds:
                    self._invalid(request, "protected-user realization exceeds source authority")
                if span.realized_state != source_by_id[span.source_unit_id].required_state:
                    self._invalid(request, "protected-user realization changed source progression state")
            elif span.owner_id not in selected:
                self._invalid(request, "candidate gave dialogue or action to unselected character")
            if span.beat_id is not None and span.beat_id not in expected_beats:
                self._invalid(request, "candidate span cites an unknown decision beat")

    def _validate_source_coverage(
        self, request: SceneComposerRequest, submission: ComposerSubmission
    ) -> None:
        if not request.creator_event_coverage_required:
            return
        coverage = submission.manifest.source_unit_coverage
        expected_units = tuple(value.source_unit_id for value in request.source_packet.units)
        actual_units = tuple(value.source_unit_id for value in coverage)
        if actual_units != expected_units:
            self._invalid(request, "creator event source units are incomplete or reordered")
        if tuple(value.ordinal for value in coverage) != tuple(range(len(coverage))):
            self._invalid(request, "creator event coverage ordinals are invalid")
        previous_end = -1
        for item in coverage:
            for text_range in item.ranges:
                if (
                    text_range.end > len(submission.candidate.story_text)
                    or text_range.start < previous_end
                ):
                    self._invalid(
                        request,
                        "creator event coverage spans are overlapping or reordered",
                    )
                if not submission.candidate.story_text[
                    text_range.start : text_range.end
                ].strip():
                    self._invalid(request, "creator event coverage points to empty prose")
                previous_end = text_range.end
            expected_state = next(
                value.required_state
                for value in request.source_packet.units
                if value.source_unit_id == item.source_unit_id
            )
            if expected_state is None or item.preserved_state is not expected_state:
                self._invalid(request, "creator event coverage changed event state")
        if coverage and submission.candidate.story_text[: coverage[0].start].strip():
            self._invalid(request, "creator event candidate starts after its earliest supplied beat")

    def _validate_adult_specificity_coverage(
        self,
        request: SceneComposerRequest,
        submission: ComposerSubmission,
        *,
        required: bool,
    ) -> None:
        contract = request.specificity_contract
        coverage = submission.manifest.adult_specificity_coverage
        if contract is None:
            if coverage:
                self._invalid(request, "ordinary candidate carried adult specificity coverage")
            return
        if not coverage and not required:
            return
        expected = tuple(
            (
                value.obligation_key,
                value.beat_id,
                value.channel,
                value.character_id,
            )
            for value in contract.beat_requirements
        )
        actual = tuple(
            (
                value.obligation_key,
                value.beat_id,
                value.channel,
                value.character_id,
            )
            for value in coverage
        )
        if actual != expected:
            self._invalid(
                request,
                "adult specificity coverage changed, omitted, or reordered an obligation",
            )
        for item in coverage:
            for text_range in item.ranges:
                if (
                    text_range.end > len(submission.candidate.story_text)
                    or not submission.candidate.story_text[
                        text_range.start : text_range.end
                    ].strip()
                ):
                    self._invalid(
                        request,
                        "adult specificity coverage points outside non-empty prose",
                    )

    @staticmethod
    def _valid_advisory(value: object) -> bool:
        if not isinstance(value, dict) or set(value) != {"kind", "summary"}:
            return False
        return all(isinstance(item, str) and item.strip() for item in value.values())

    def _invalid(self, request: SceneComposerRequest, message: str) -> None:
        raise self._failure(request, message)

    @staticmethod
    def _failure(
        request: SceneComposerRequest,
        message: str,
        *,
        code: ErrorCode = ErrorCode.COMPOSER_CONTRACT_INVALID,
        stage: str = "composer_validation",
        details: tuple[str, ...] = (),
    ) -> ComposerExecutionFailure:
        prepared = request.prepared_turn
        return ComposerExecutionFailure(
            ErrorEnvelope(
                schema_version=ErrorEnvelope.SCHEMA_VERSION,
                error_code=code,
                message=message,
                trace_id=deterministic_id(
                    IdKind.TRACE,
                    "cera.composer.failure.v1",
                    f"{prepared.request.request_id}|{stage}|{code.value}",
                ),
                request_id=prepared.request.request_id,
                branch_id=prepared.request.branch_id,
                generation_id=prepared.request.generation_id,
                stage=stage,
                story_state_committed=False,
                retry_mode=RetryMode.MANUAL_AFTER_REVIEW,
                details=details,
            ),
            safe_diagnostics=details,
        )


class PresentationRendererPort(Protocol):
    def render(self, artifact: AcceptedStoryArtifact, *, profile: str) -> RenderedStory: ...


@dataclass(frozen=True, slots=True)
class RendererProfile:
    name: str
    version: str
    prefix: str = ""
    suffix: str = ""


class PurePresentationRenderer:
    """Pure projection only: no repair, provider call, or authority-store access."""

    def __init__(self, profiles: tuple[RendererProfile, ...]) -> None:
        self._profiles = {value.name: value for value in profiles}
        if len(self._profiles) != len(profiles):
            raise ValueError("renderer profiles must have unique names")

    def render(self, artifact: AcceptedStoryArtifact, *, profile: str) -> RenderedStory:
        if not isinstance(artifact, AcceptedStoryArtifact):
            raise TypeError("renderer accepts only AcceptedStoryArtifact")
        selected = self._profiles.get(profile)
        if selected is None:
            raise ValueError("unknown renderer profile")
        accepted_hash = domain_sha256("cera.accepted_story_artifact.v1", artifact)
        rendered_text = f"{selected.prefix}{artifact.accepted_prose}{selected.suffix}"
        return RenderedStory(
            schema_version=RenderedStory.SCHEMA_VERSION,
            rendered_artifact_id=deterministic_id(
                IdKind.RENDERED_ARTIFACT,
                "cera.rendered_story.v1",
                f"{accepted_hash}|{selected.name}|{selected.version}",
            ),
            accepted_artifact_id=artifact.artifact_id,
            accepted_artifact_sha256=accepted_hash,
            renderer_profile=selected.name,
            renderer_version=selected.version,
            rendered_text=rendered_text,
            rendered_sha256=text_sha256(rendered_text),
        )
