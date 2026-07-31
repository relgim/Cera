"""Provider-neutral semantic-specificity verification for adult craft.

The request exposes only locked beat-local prose and semantic bindings.  The
privacy-safe receipt retains hashes and counters, never candidate text.  The
scripted fake is declarative and deliberately contains no prose-analysis logic.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from cera.composer.models import (
    ComposerCandidate,
    RealizationKind,
    RealizationManifest,
    SceneComposerRequest,
)
from cera.errors import ContractValidationError
from cera.ids import IdKind, TypedId, deterministic_id
from cera.serialization import domain_sha256, text_sha256

from .models import (
    AdultCraftConcept,
    AdultCraftNeed,
    RealizationChannel,
    SemanticActionBinding,
    SemanticBeatSpan,
    SemanticRequiredTransition,
    SemanticSpecificityAdapterRole,
    SemanticSpecificityFinding,
    SemanticSpecificityReceipt,
    SemanticSpecificityRequest,
    SemanticSpecificityResult,
    SemanticVerificationStage,
    SpecificityContract,
)


_MATERIAL_CONCEPTS = {
    AdultCraftConcept.BODILY_FLUIDS,
    AdultCraftConcept.FECES,
    AdultCraftConcept.LUBRICATION,
    AdultCraftConcept.MATERIAL_CONTINUITY,
    AdultCraftConcept.SALIVA,
    AdultCraftConcept.SEMEN,
    AdultCraftConcept.SQUIRTING,
    AdultCraftConcept.TEARS,
    AdultCraftConcept.URINE,
}

_KIND_CHANNELS = {
    RealizationKind.ACTION: (RealizationChannel.NARRATION, RealizationChannel.PHYSIOLOGY),
    RealizationKind.REACTION: (RealizationChannel.NARRATION, RealizationChannel.PHYSIOLOGY),
    RealizationKind.EMOTION: (RealizationChannel.NARRATION, RealizationChannel.INNER_VOICE),
    RealizationKind.DIALOGUE: (RealizationChannel.DIALOGUE,),
    RealizationKind.VOCALIZATION: (RealizationChannel.DIALOGUE,),
    RealizationKind.PRIVATE_STATE: (RealizationChannel.INNER_VOICE,),
    RealizationKind.MOTIVE: (RealizationChannel.INNER_VOICE,),
    RealizationKind.SOUND_EFFECT: (RealizationChannel.SOUND_EFFECT,),
}


@dataclass(frozen=True, slots=True)
class SemanticSpecificityAdapterResponse:
    findings: tuple[SemanticSpecificityFinding, ...]
    adapter_role: SemanticSpecificityAdapterRole
    adapter_version: str
    adapter_evidence_id: str
    adapter_evidence_sha256: str
    external_provider_calls: int


class SemanticSpecificityPort(Protocol):
    def verify(
        self, request: SemanticSpecificityRequest
    ) -> SemanticSpecificityAdapterResponse: ...


class ScriptedFakeSemanticSpecificityPort:
    """Return stage-specific findings without implementing semantic judgment."""

    production_prohibited = True

    def __init__(
        self,
        fixture_id: str,
        *,
        initial_findings: tuple[SemanticSpecificityFinding, ...] = (),
        post_repair_findings: tuple[SemanticSpecificityFinding, ...] = (),
    ) -> None:
        if not fixture_id.strip():
            raise ContractValidationError("semantic fake fixture ID must be non-empty")
        self.fixture_id = fixture_id
        self._findings = {
            SemanticVerificationStage.INITIAL: initial_findings,
            SemanticVerificationStage.POST_REPAIR: post_repair_findings,
        }
        self._seen_request_ids: set[TypedId] = set()
        self.call_count = 0

    def verify(
        self, request: SemanticSpecificityRequest
    ) -> SemanticSpecificityAdapterResponse:
        if request.semantic_request_id in self._seen_request_ids:
            raise ContractValidationError("semantic verifier cannot retry a request")
        self._seen_request_ids.add(request.semantic_request_id)
        self.call_count += 1
        findings = self._findings[request.stage]
        evidence_key = domain_sha256(
            "cera.scripted_fake_semantic_specificity_fixture.v1",
            (self.fixture_id, request.stage, findings),
        )
        return SemanticSpecificityAdapterResponse(
            findings=findings,
            adapter_role=SemanticSpecificityAdapterRole.SCRIPTED_FAKE,
            adapter_version="cera.scripted_fake_semantic_specificity.v1",
            adapter_evidence_id=f"{self.fixture_id}:{request.stage.value}",
            adapter_evidence_sha256=evidence_key,
            external_provider_calls=0,
        )


@dataclass(frozen=True, slots=True)
class SemanticSpecificityExecutionResult:
    request: SemanticSpecificityRequest
    result: SemanticSpecificityResult
    receipt: SemanticSpecificityReceipt


class SemanticSpecificityCoordinator:
    def build_request(
        self,
        composer_request: SceneComposerRequest,
        craft_need: AdultCraftNeed,
        contract: SpecificityContract,
        candidate: ComposerCandidate,
        manifest: RealizationManifest,
        *,
        stage: SemanticVerificationStage,
        prior_semantic_receipt_sha256: str | None = None,
    ) -> SemanticSpecificityRequest:
        decision = composer_request.reasoner_outcome.decision
        if decision is None:
            raise ContractValidationError("semantic verifier requires a decision")
        if craft_need.decision_id != decision.decision_id:
            raise ContractValidationError("semantic verifier craft need changed decision")
        if contract.decision_id != decision.decision_id:
            raise ContractValidationError("semantic verifier contract changed decision")
        if contract.request_id != composer_request.prepared_turn.request.request_id:
            raise ContractValidationError("semantic verifier contract changed story request")
        if candidate.candidate_sha256 != manifest.candidate_sha256:
            raise ContractValidationError("semantic verifier candidate/manifest mismatch")

        ordered_need_ids = tuple(value.beat_id for value in craft_need.beat_needs)
        decision_ids = tuple(value.beat_id for value in decision.current_segment.ordered_beats)
        if not set(ordered_need_ids).issubset(set(decision_ids)):
            raise ContractValidationError("semantic verifier craft beat left current segment")
        ordered_need_ids = tuple(value for value in decision_ids if value in set(ordered_need_ids))

        spans: list[SemanticBeatSpan] = []
        bindings: list[SemanticActionBinding] = []
        needs_by_id = {value.beat_id: value for value in craft_need.beat_needs}
        for beat_id in ordered_need_ids:
            start, end, channels = _beat_range(manifest, beat_id)
            exact_text = candidate.story_text[start:end]
            need = needs_by_id[beat_id]
            spans.append(
                SemanticBeatSpan(
                    beat_id=beat_id,
                    start=start,
                    end=end,
                    exact_text=exact_text,
                    exact_text_sha256=text_sha256(exact_text),
                    channels=channels,
                )
            )
            bindings.append(
                SemanticActionBinding(
                    beat_id=beat_id,
                    actor_id=need.actor_id,
                    action_family=need.action_family,
                    object_concepts=need.object_concepts,
                    required_material_outcomes=tuple(
                        value for value in need.object_concepts if value in _MATERIAL_CONCEPTS
                    ),
                )
            )
        transitions = tuple(
            SemanticRequiredTransition(previous, current)
            for previous, current in zip(ordered_need_ids, ordered_need_ids[1:])
        )
        key = (
            f"{composer_request.request_sha256}|{contract.contract_sha256}|"
            f"{candidate.candidate_sha256}|{manifest.manifest_sha256}|{stage.value}|"
            f"{prior_semantic_receipt_sha256 or '-'}"
        )
        return SemanticSpecificityRequest(
            schema_version=SemanticSpecificityRequest.SCHEMA_VERSION,
            semantic_request_id=deterministic_id(
                IdKind.SEMANTIC_SPECIFICITY,
                "cera.semantic_specificity_request.v1",
                key,
            ),
            story_request_id=composer_request.prepared_turn.request.request_id,
            decision_id=decision.decision_id,
            specificity_contract_id=contract.specificity_contract_id,
            specificity_contract_sha256=contract.contract_sha256,
            candidate_sha256=candidate.candidate_sha256,
            manifest_sha256=manifest.manifest_sha256,
            stage=stage,
            selected_character_ids=composer_request.selected_npc_ids,
            beat_spans=tuple(spans),
            action_bindings=tuple(bindings),
            required_transitions=transitions,
            prior_semantic_receipt_sha256=prior_semantic_receipt_sha256,
            coverage_declarations_advisory_only=True,
            authority_additions_forbidden=True,
            story_state_committed=False,
        )

    def execute(
        self,
        request: SemanticSpecificityRequest,
        port: SemanticSpecificityPort,
    ) -> SemanticSpecificityExecutionResult:
        response = port.verify(request)
        valid_beats = {value.beat_id for value in request.beat_spans}
        if any(value.beat_id not in valid_beats for value in response.findings):
            raise ContractValidationError("semantic verifier finding escaped requested beats")
        keys = tuple((value.beat_id, value.code) for value in response.findings)
        if len(keys) != len(set(keys)):
            raise ContractValidationError("semantic verifier returned duplicate findings")
        if response.adapter_role is SemanticSpecificityAdapterRole.SCRIPTED_FAKE:
            if response.external_provider_calls != 0:
                raise ContractValidationError("fake semantic verifier reported a provider call")
        result_key = request.request_sha256 + "|" + "|".join(
            f"{value.beat_id}:{value.code.value}" for value in response.findings
        )
        repairable = (
            tuple(dict.fromkeys(value.beat_id for value in response.findings))
            if request.stage is SemanticVerificationStage.INITIAL
            else ()
        )
        result = SemanticSpecificityResult(
            schema_version=SemanticSpecificityResult.SCHEMA_VERSION,
            result_id=deterministic_id(
                IdKind.SEMANTIC_SPECIFICITY,
                "cera.semantic_specificity_result.v1",
                result_key,
            ),
            semantic_request_id=request.semantic_request_id,
            semantic_request_sha256=request.request_sha256,
            stage=request.stage,
            status=(
                "accepted"
                if not response.findings
                else "repair_required"
                if request.stage is SemanticVerificationStage.INITIAL
                else "rejected"
            ),
            findings=response.findings,
            repairable_beat_ids=repairable,
            authority_changed=False,
            coverage_declarations_treated_as_proof=False,
        )
        receipt_key = (
            f"{request.request_sha256}|{result.result_sha256}|"
            f"{response.adapter_evidence_sha256}"
        )
        receipt = SemanticSpecificityReceipt(
            schema_version=SemanticSpecificityReceipt.SCHEMA_VERSION,
            receipt_id=deterministic_id(
                IdKind.SEMANTIC_SPECIFICITY,
                "cera.semantic_specificity_receipt.v1",
                receipt_key,
            ),
            semantic_request_id=request.semantic_request_id,
            semantic_request_sha256=request.request_sha256,
            result_id=result.result_id,
            result_sha256=result.result_sha256,
            adapter_role=response.adapter_role,
            adapter_version=response.adapter_version,
            adapter_evidence_id=response.adapter_evidence_id,
            adapter_evidence_sha256=response.adapter_evidence_sha256,
            invocation_count=1,
            external_provider_calls=response.external_provider_calls,
            retry_count=0,
            fallback_used=False,
            exact_beat_text_retained=False,
            raw_candidate_retained=False,
            story_state_committed=False,
        )
        return SemanticSpecificityExecutionResult(request, result, receipt)


def _beat_range(
    manifest: RealizationManifest, beat_id: TypedId
) -> tuple[int, int, tuple[RealizationChannel, ...]]:
    realized = [value for value in manifest.character_spans if value.beat_id == beat_id]
    if not realized:
        raise ContractValidationError("semantic verification requires a beat-local span")
    explicit = [
        value
        for value in manifest.adult_specificity_coverage
        if value.beat_id == beat_id
    ]
    explicit_ranges = tuple(
        text_range for value in explicit for text_range in value.ranges
    )
    all_starts = [
        *(value.start for value in realized),
        *(value.start for value in explicit_ranges),
    ]
    all_ends = [
        *(value.end for value in realized),
        *(value.end for value in explicit_ranges),
    ]
    start = min(all_starts)
    end = max(all_ends)
    for value in manifest.character_spans:
        if value.beat_id != beat_id and value.start < end and value.end > start:
            raise ContractValidationError("semantic beat overlaps a non-target realization")
    channels: list[RealizationChannel] = []
    if explicit:
        for value in explicit:
            if value.channel not in channels:
                channels.append(value.channel)
    else:
        # Historical manifests used a lossy kind-to-channel projection. Active
        # v4 Composer responses carry exact Python-bound adult obligations.
        for value in realized:
            for channel in _KIND_CHANNELS[value.kind]:
                if channel not in channels:
                    channels.append(channel)
    return start, end, tuple(channels)
