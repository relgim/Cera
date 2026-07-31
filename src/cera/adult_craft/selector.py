"""Deterministic post-Reasoner adult craft selection and prompt blocks."""

from __future__ import annotations

from dataclasses import dataclass, replace

from cera.composer.models import (
    ComposerContextBlock,
    ComposerContextKind,
    ComposerContextSource,
    AdultComposerBinding,
)
from cera.contracts import DecisionRoute, SceneDecision
from cera.errors import ContractValidationError
from cera.ids import IdKind, TypedId, deterministic_id
from cera.serialization import domain_sha256

from .catalog import AdultCraftCatalog
from .models import (
    AdultCraftAxis,
    AdultCraftFamily,
    AdultCraftFragment,
    AdultCraftMode,
    AdultCraftNeed,
    AdultCraftNeedV2,
    AdultCraftNeedV3,
    AdultCraftSelectionReceipt,
    RealizationChannel,
    SpecificityBeatRequirement,
    SpecificityContract,
    SpecificityRegister,
    SpecificityTermRequirement,
)


ADULT_CRAFT_SELECTION_POLICY_VERSION = "cera.adult_craft_selection.v2"


class AdultCraftSelectionError(ContractValidationError):
    pass


@dataclass(frozen=True, slots=True)
class AdultCraftSelectionResult:
    fragments: tuple[AdultCraftFragment, ...]
    craft_blocks: tuple[ComposerContextBlock, ...]
    specificity_contract: SpecificityContract
    receipt: AdultCraftSelectionReceipt


class AdultCraftSelector:
    """Coverage/budget selector; no fixed fragment count and no raw-message regex."""

    def __init__(
        self,
        catalog: AdultCraftCatalog,
        *,
        maximum_craft_bytes: int = 32_768,
    ) -> None:
        if not 1_024 <= maximum_craft_bytes <= 131_072:
            raise ContractValidationError("adult craft byte budget must be 1024..131072")
        self.catalog = catalog
        self.maximum_craft_bytes = maximum_craft_bytes

    def select(
        self,
        need: AdultCraftNeed | AdultCraftNeedV2 | AdultCraftNeedV3,
        decision: SceneDecision,
        *,
        request_id: TypedId,
    ) -> AdultCraftSelectionResult:
        if decision.route is not DecisionRoute.CONSENT_VALID_ADULT:
            raise AdultCraftSelectionError("adult craft requires a consent-valid adult decision")
        if request_id != need.request_id or decision.decision_id != need.decision_id:
            raise AdultCraftSelectionError("adult craft need does not bind this request/decision")
        sequence_sha256 = domain_sha256(
            "cera.sequence_plan.v1",
            (decision.current_segment, decision.future_segments),
        )
        if need.sequence_plan_sha256 != sequence_sha256:
            raise AdultCraftSelectionError("adult craft need changed the SequencePlan")
        decision_beats = {value.beat_id for value in decision.current_segment.ordered_beats}
        craft_beats = {value.beat_id for value in need.beat_needs}
        if not craft_beats or not craft_beats.issubset(decision_beats):
            raise AdultCraftSelectionError(
                "adult craft need must cite a non-empty subset of current beats"
            )
        selected_characters = set(decision.responding_npc_ids)
        if any(
            value.character_id not in selected_characters
            for value in need.character_card_sections
        ):
            raise AdultCraftSelectionError("adult card request names an unselected character")

        requirements = _requirements(need)
        candidates = tuple(
            fragment
            for fragment in self.catalog.fragments
            if need.mode in fragment.modes
            and (
                AdultCraftFamily.GENERAL in fragment.families
                or set(fragment.families).intersection(need.families)
            )
        )
        chosen: list[AdultCraftFragment] = []
        uncovered = set(requirements)
        used_bytes = 0
        while uncovered:
            ranked = []
            for fragment in candidates:
                if fragment in chosen:
                    continue
                coverage = uncovered.intersection(_fragment_requirements(fragment))
                size = len(fragment.craft_text.encode("utf-8"))
                if coverage and used_bytes + size <= self.maximum_craft_bytes:
                    ranked.append((-len(coverage), size, str(fragment.fragment_id), fragment, coverage))
            if not ranked:
                break
            _, size, _, fragment, coverage = min(ranked)
            chosen.append(fragment)
            used_bytes += size
            uncovered.difference_update(coverage)
        if uncovered:
            raise AdultCraftSelectionError(
                "adult craft catalog cannot cover: " + ", ".join(sorted(uncovered))
            )

        covered = tuple(sorted(requirements))
        key = (
            f"{self.catalog.manifest.manifest_sha256}|{need.need_sha256}|"
            + "|".join(str(value.fragment_id) for value in chosen)
        )
        receipt = AdultCraftSelectionReceipt(
            schema_version=AdultCraftSelectionReceipt.SCHEMA_VERSION,
            selection_id=deterministic_id(
                IdKind.CRAFT_SELECTION,
                "cera.adult_craft_selection.v2",
                key,
            ),
            catalog_id=self.catalog.manifest.catalog_id,
            catalog_manifest_sha256=self.catalog.manifest.manifest_sha256,
            craft_need_id=need.craft_need_id,
            craft_need_sha256=need.need_sha256,
            selected_fragment_ids=tuple(value.fragment_id for value in chosen),
            selected_fragment_sha256=tuple(value.fragment_sha256 for value in chosen),
            selected_modes=(need.mode,),
            covered_requirements=covered,
            uncovered_requirements=(),
            total_craft_bytes=used_bytes,
            maximum_craft_bytes=self.maximum_craft_bytes,
            selection_policy_version=ADULT_CRAFT_SELECTION_POLICY_VERSION,
            story_state_committed=False,
            external_provider_calls=0,
        )
        contract = _specificity_contract(need, decision, tuple(chosen), receipt)
        blocks = tuple(_craft_block(value, decision.responding_npc_ids) for value in chosen)
        return AdultCraftSelectionResult(tuple(chosen), blocks, contract, receipt)


def bind_adult_craft(
    binding: AdultComposerBinding,
    selection: AdultCraftSelectionResult,
) -> AdultComposerBinding:
    """Attach the post-decision catalog/contract hashes without changing mechanics authority."""

    return replace(
        binding,
        selected_craft_reference_ids=selection.receipt.selected_fragment_ids,
        craft_selection_id=selection.receipt.selection_id,
        craft_selection_sha256=selection.receipt.selection_sha256,
        specificity_contract_id=selection.specificity_contract.specificity_contract_id,
        specificity_contract_sha256=selection.specificity_contract.contract_sha256,
    )


def _requirements(
    need: AdultCraftNeed | AdultCraftNeedV2 | AdultCraftNeedV3,
) -> set[str]:
    requirements = {f"family:{value.value}" for value in need.families}
    requirements.update(f"axis:{value.value}" for value in need.axes)
    requirements.update(
        f"channel:{value.channel.value}" for value in need.channel_needs
    )
    requirements.update(
        f"concept:{concept.value}"
        for value in need.channel_needs
        for concept in _semantic_concepts(value)
    )
    requirements.update(
        f"lexicon:{concept.value}"
        for value in need.channel_needs
        for concept in _lexical_concepts(value)
    )
    requirements.update(
        f"concept:{concept.value}"
        for value in need.beat_needs
        for concept in value.object_concepts
    )
    return requirements


def _fragment_requirements(fragment: AdultCraftFragment) -> set[str]:
    result = {f"family:{value.value}" for value in fragment.families}
    result.update(f"axis:{value.value}" for value in fragment.axes)
    result.update(f"channel:{value.value}" for value in fragment.channels)
    result.update(f"concept:{value.value}" for value in fragment.coverage_concepts)
    result.update(f"concept:{value.concept.value}" for value in fragment.vocabulary_groups)
    result.update(f"lexicon:{value.concept.value}" for value in fragment.vocabulary_groups)
    return result


def _specificity_contract(
    need: AdultCraftNeed,
    decision: SceneDecision,
    fragments: tuple[AdultCraftFragment, ...],
    receipt: AdultCraftSelectionReceipt,
) -> SpecificityContract:
    vocabulary: dict[object, list[str]] = {}
    for fragment in fragments:
        for group in fragment.vocabulary_groups:
            terms = vocabulary.setdefault(group.concept, [])
            for term in group.terms:
                if term.casefold() not in {value.casefold() for value in terms}:
                    terms.append(term)
    requirements: list[SpecificityBeatRequirement] = []
    for beat in need.beat_needs:
        # The authoritative need is beat-local.  Never round-trip through the
        # flattened ``need.channel_needs`` compatibility view: two beats may use
        # the same channel/owner with different concepts or registers.
        beat_channels = tuple(
            getattr(beat, "channel_requirements", ())
        )
        if not beat_channels:
            beat_channels = tuple(
                value
                for value in need.channel_needs
                if value.channel in beat.channels
            )
        for channel_need in beat_channels:
            semantic_concepts = _semantic_concepts(channel_need)
            lexical_concepts = _lexical_concepts(channel_need)
            terms: list[SpecificityTermRequirement] = []
            if lexical_concepts:
                for concept in lexical_concepts:
                    alternatives = tuple(vocabulary.get(concept, ()))
                    if not alternatives:
                        raise AdultCraftSelectionError(
                            f"no selected vocabulary covers lexical concept: {concept.value}"
                        )
                    terms.append(SpecificityTermRequirement(concept, alternatives, 1))
            elif not semantic_concepts:
                # An available-but-unselected layer is not a quota. Structural
                # participant/beat validation remains owned by the Composer.
                continue
            requirements.append(
                SpecificityBeatRequirement(
                    beat_id=beat.beat_id,
                    channel=channel_need.channel,
                    character_id=getattr(channel_need, "character_id", None),
                    minimum_register=channel_need.minimum_register,
                    terms=tuple(terms),
                )
            )
    key = f"{need.need_sha256}|{receipt.selection_sha256}|{decision.decision_id}"
    return SpecificityContract(
        schema_version=SpecificityContract.SCHEMA_VERSION,
        specificity_contract_id=deterministic_id(
            IdKind.SPECIFICITY_CONTRACT,
            "cera.specificity_contract.v1",
            key,
        ),
        request_id=need.request_id,
        decision_id=need.decision_id,
        craft_need_sha256=need.need_sha256,
        craft_selection_sha256=receipt.selection_sha256,
        beat_requirements=tuple(requirements),
        climax=need.climax,
        aftermath=need.aftermath,
        conditional_layers_not_quotas=True,
        bodily_response_not_consent=True,
        blocked_nonconsensual_generation_excluded=True,
    )


def _semantic_concepts(channel_need) -> tuple:
    if hasattr(channel_need, "semantic_concepts"):
        return tuple(channel_need.semantic_concepts)
    return tuple(channel_need.required_concepts)


def _lexical_concepts(channel_need) -> tuple:
    if hasattr(channel_need, "lexical_concepts"):
        return tuple(channel_need.lexical_concepts)
    if channel_need.minimum_register is SpecificityRegister.INDIRECT:
        return ()
    return tuple(channel_need.required_concepts)


def _craft_block(
    fragment: AdultCraftFragment,
    selected_characters: tuple[TypedId, ...],
) -> ComposerContextBlock:
    return ComposerContextBlock(
        context_id=fragment.fragment_id,
        source=ComposerContextSource.CREATOR_CRAFT,
        kind=ComposerContextKind.CRAFT_REFERENCE,
        applicable_character_ids=selected_characters,
        exact_evidence=None,
        craft_reference_id=fragment.fragment_id,
        craft_text=fragment.craft_text,
        content_class="protected_adult",
    )
