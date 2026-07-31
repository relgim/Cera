"""Python-owned bounded realization-card assembly for the Scene Composer."""

from __future__ import annotations

from dataclasses import replace
import json
import re

from cera.adult_craft.models import CharacterCardSectionNeed
from cera.contracts import EffectiveCharacterProjection, EvidenceRecordType
from cera.errors import ContractValidationError, ErrorCode, EvidenceServiceError
from cera.evidence import (
    EvidenceFetchRequest,
    EvidenceQueryPlan,
    EvidenceService,
    ExactEvidence,
)
from cera.ids import IdKind, TypedId, deterministic_id
from cera.reasoner import ReasonerExecutionResult, ReasonerOutcomeStatus
from cera.serialization import text_sha256

from .models import (
    ComposerContextAssemblyReceipt,
    ComposerContextAssemblyResult,
    ComposerContextBlock,
    ComposerContextKind,
    ComposerContextSource,
    ComposerContinuityReference,
    ComposerEvidenceSectionView,
    ComposerRealizationContext,
    SceneComposerRequest,
)


CONTEXT_SELECTION_POLICY_VERSION = "cera.composer_context_selection.v3"
_REQUIRED_CHARACTER_PROJECTION_TAGS = (
    "core_premise",
    "fidelity_invariants",
    "initial_stance_toward_ted",
    "speech_system",
)


class ComposerContextAssemblyError(Exception):
    def __init__(self, code: ErrorCode, message: str) -> None:
        self.code = code
        super().__init__(message)


class ComposerContextAssembler:
    """Select cited facts plus one exact voice card per responding NPC."""

    def __init__(self, evidence_service: EvidenceService) -> None:
        self.evidence_service = evidence_service

    def assemble(
        self,
        base_request: SceneComposerRequest,
        reasoner_result: ReasonerExecutionResult,
        *,
        craft_blocks: tuple[ComposerContextBlock, ...] = (),
        realization_card_sections: tuple[CharacterCardSectionNeed, ...] = (),
        maximum_bytes: int = 65_536,
    ) -> ComposerContextAssemblyResult:
        if base_request.realization_context is not None:
            raise ComposerContextAssemblyError(
                ErrorCode.COMPOSER_CONTRACT_INVALID,
                "Composer request already contains realization context",
            )
        if reasoner_result.outcome.status is not ReasonerOutcomeStatus.DECISION_READY:
            raise ComposerContextAssemblyError(
                ErrorCode.COMPOSER_CONTRACT_INVALID,
                "Composer context requires a decision-ready Reasoner result",
            )
        if (
            reasoner_result.outcome.outcome_sha256
            != base_request.reasoner_outcome.outcome_sha256
            or reasoner_result.receipt != base_request.reasoner_receipt
        ):
            raise ComposerContextAssemblyError(
                ErrorCode.COMPOSER_CONTRACT_INVALID,
                "Composer context Reasoner result does not match the request",
            )
        if any(value.source is not ComposerContextSource.CREATOR_CRAFT for value in craft_blocks):
            raise ComposerContextAssemblyError(
                ErrorCode.COMPOSER_CONTRACT_INVALID,
                "caller-supplied Composer context must be labeled creator craft",
            )
        card_need_by_character = {
            value.character_id: value for value in realization_card_sections
        }
        if len(card_need_by_character) != len(realization_card_sections):
            raise ComposerContextAssemblyError(
                ErrorCode.COMPOSER_CONTRACT_INVALID,
                "realization-card section requests contain duplicate characters",
            )
        if not set(card_need_by_character).issubset(base_request.selected_npc_ids):
            raise ComposerContextAssemblyError(
                ErrorCode.COMPOSER_CONTRACT_INVALID,
                "realization-card section request names an unselected character",
            )

        snapshot = base_request.prepared_turn.evidence_snapshot
        service = EvidenceService(
            self.evidence_service.store,
            limits=self.evidence_service.limits,
            visibility_policy_version=self.evidence_service.visibility_policy_version,
        )
        exact_by_id = {
            value.evidence_id: value
            for value in reasoner_result.authorized_exact_evidence
        }
        usage = _evidence_use_by_character(base_request)
        blocks: list[ComposerContextBlock] = []
        for citation in base_request.reasoner_outcome.hard_citations:
            exact = exact_by_id.get(citation.evidence_id)
            applicable = tuple(sorted(usage.get(citation.evidence_id, set())))
            if not applicable:
                continue
            if exact is None:
                raise ComposerContextAssemblyError(
                    ErrorCode.COMPOSER_CONTRACT_INVALID,
                    "Reasoner-cited Composer evidence was not retained exactly",
                )
            blocks.append(
                _exact_block(
                    exact,
                    kind=_context_kind(exact),
                    applicable=applicable,
                )
            )

        lookup_receipt_ids: list[TypedId] = []
        lookup_receipts = []
        returned_bytes = 0
        continuity = list(base_request.continuity_references)
        projection_records: dict[TypedId, dict[str, ExactEvidence]] = {}
        projection_required = (
            base_request.reasoner_outcome.behavioral_scene_plan is not None
        )
        try:
            for character_id in base_request.selected_npc_ids:
                required_tags = (
                    _REQUIRED_CHARACTER_PROJECTION_TAGS
                    if projection_required
                    else ("speech_system",)
                )
                search = service.search_query_plan(
                    snapshot,
                    EvidenceQueryPlan(
                        schema_version=EvidenceQueryPlan.SCHEMA_VERSION,
                        primary_terms=(required_tags[0],),
                        alternate_term_sets=tuple(
                            (tag,) for tag in required_tags[1:]
                        ),
                        entity_ids=(character_id,),
                        tags=(),
                        record_types=(EvidenceRecordType.GENESIS_FACT,),
                        limit=8,
                        maximum_variants=len(required_tags),
                    ),
                )
                lookup_receipt_ids.append(search.receipt.lookup_receipt_id)
                lookup_receipts.append(search.receipt)
                returned_bytes += search.receipt.returned_bytes
                selected_by_tag = {}
                for tag in required_tags:
                    eligible = tuple(
                        value
                        for value in search.references
                        if character_id in value.subject_ids
                        and tag in {item.casefold() for item in value.tags}
                        and "source_text" in value.expandable_sections
                    )
                    if len(eligible) != 1:
                        raise ComposerContextAssemblyError(
                            ErrorCode.EVIDENCE_INSUFFICIENT,
                            f"selected character lacks one unambiguous {tag} record",
                        )
                    selected_by_tag[tag] = eligible[0]
                fetched = service.fetch_evidence(
                    snapshot,
                    EvidenceFetchRequest(
                        tuple(
                            selected_by_tag[tag].evidence_id
                            for tag in required_tags
                        ),
                        ("source_text",),
                    ),
                )
                lookup_receipt_ids.append(fetched.receipt.lookup_receipt_id)
                lookup_receipts.append(fetched.receipt)
                returned_bytes += fetched.receipt.returned_bytes
                if len(fetched.exact_records) != len(required_tags):
                    raise ComposerContextAssemblyError(
                        ErrorCode.EVIDENCE_INSUFFICIENT,
                        "effective character projection did not expand exactly",
                    )
                exact_by_evidence = {
                    value.evidence_id: value for value in fetched.exact_records
                }
                exact_by_tag = {
                    tag: exact_by_evidence[selected_by_tag[tag].evidence_id]
                    for tag in required_tags
                }
                projection_records[character_id] = exact_by_tag
                voice = exact_by_tag["speech_system"]
                card_need = card_need_by_character.get(character_id)
                voice_block = (
                    _selected_voice_block(voice, character_id, card_need)
                    if card_need is not None
                    else _exact_block(
                        voice,
                        kind=ComposerContextKind.CHARACTER_VOICE,
                        applicable=(character_id,),
                    )
                )
                existing_index = next(
                    (
                        index
                        for index, value in enumerate(blocks)
                        if (
                            value.context_id == voice.evidence_id
                            or (
                                value.evidence_section_view is not None
                                and value.evidence_section_view.evidence_id == voice.evidence_id
                            )
                        )
                    ),
                    None,
                )
                if existing_index is None:
                    blocks.append(voice_block)
                else:
                    blocks[existing_index] = voice_block
                for tag in (
                    (
                        "core_premise",
                        "fidelity_invariants",
                        "initial_stance_toward_ted",
                    )
                    if projection_required
                    else ()
                ):
                    exact = exact_by_tag[tag]
                    kind = (
                        ComposerContextKind.RELATIONSHIP
                        if tag == "initial_stance_toward_ted"
                        else ComposerContextKind.CHARACTER_IDENTITY
                    )
                    profile_block = _exact_block(
                        exact,
                        kind=kind,
                        applicable=(character_id,),
                    )
                    existing_index = next(
                        (
                            index
                            for index, value in enumerate(blocks)
                            if value.context_id == exact.evidence_id
                        ),
                        None,
                    )
                    if existing_index is None:
                        blocks.append(profile_block)
                    else:
                        blocks[existing_index] = profile_block
                for exact in exact_by_tag.values():
                    continuity.append(
                        ComposerContinuityReference(
                            evidence_id=exact.evidence_id,
                            record_id=exact.metadata.record_id,
                            record_version=exact.metadata.record_version,
                        )
                    )
        except EvidenceServiceError as exc:
            raise ComposerContextAssemblyError(exc.code, str(exc)) from exc

        all_blocks = tuple((*blocks, *craft_blocks))
        projections = (
            tuple(
                _effective_projection(
                    base_request,
                    character_id,
                    projection_records[character_id],
                    exact_by_id,
                )
                for character_id in base_request.selected_npc_ids
            )
            if projection_required
            else ()
        )
        context = ComposerRealizationContext(
            selection_policy_version=CONTEXT_SELECTION_POLICY_VERSION,
            blocks=all_blocks,
            explicit_exclusions=(
                "No inactive-character identity or private memory.",
                "No uncited event, relationship, material, or development fact.",
                "Creator craft teaches technique only and never establishes scene truth.",
                "Genesis expression examples are non-executable style evidence and must not be copied or treated as historical dialogue.",
            ),
            maximum_bytes=maximum_bytes,
            effective_character_projections=projections,
        )
        try:
            assembled = replace(
                base_request,
                continuity_references=_unique_continuity(tuple(continuity)),
                realization_context=context,
            )
        except ContractValidationError as exc:
            raise ComposerContextAssemblyError(
                ErrorCode.COMPOSER_CONTRACT_INVALID,
                f"assembled Composer context failed validation: {exc}",
            ) from exc

        evidence_ids = tuple(
            (
                value.context_id
                if value.source is ComposerContextSource.EXACT_EVIDENCE
                else value.evidence_section_view.evidence_id
            )
            for value in all_blocks
            if value.source
            in {
                ComposerContextSource.EXACT_EVIDENCE,
                ComposerContextSource.EVIDENCE_SECTION_VIEW,
            }
        )
        craft_ids = tuple(
            value.context_id
            for value in all_blocks
            if value.source is ComposerContextSource.CREATOR_CRAFT
        )
        key = f"{base_request.request_sha256}|{assembled.request_sha256}|{context.context_sha256}"
        receipt = ComposerContextAssemblyReceipt(
            schema_version=ComposerContextAssemblyReceipt.SCHEMA_VERSION,
            assembly_receipt_id=deterministic_id(
                IdKind.VALIDATION,
                "cera.composer_context_assembly.v1",
                key,
            ),
            snapshot_token=snapshot.snapshot_token,
            base_request_sha256=base_request.request_sha256,
            assembled_request_sha256=assembled.request_sha256,
            realization_context_sha256=context.context_sha256,
            selected_evidence_ids=evidence_ids,
            selected_craft_reference_ids=craft_ids,
            lookup_receipt_ids=tuple(lookup_receipt_ids),
            lookup_count=len(lookup_receipt_ids),
            cumulative_returned_bytes=returned_bytes,
            external_provider_calls=0,
            story_authority_writes=0,
        )
        return ComposerContextAssemblyResult(assembled, receipt, tuple(lookup_receipts))


def _evidence_use_by_character(
    request: SceneComposerRequest,
) -> dict[TypedId, set[TypedId]]:
    decision = request.reasoner_outcome.decision
    assert decision is not None
    used: dict[TypedId, set[TypedId]] = {}
    for move in decision.character_moves:
        for evidence_id in move.evidence_ids:
            used.setdefault(evidence_id, set()).add(move.character_id)
    for participation in request.reasoner_outcome.participation:
        for evidence_id in participation.evidence_ids:
            used.setdefault(evidence_id, set()).add(participation.character_id)
    for beat in decision.current_segment.ordered_beats:
        if beat.actor_id.kind is IdKind.CHARACTER:
            for evidence_id in beat.evidence_ids:
                used.setdefault(evidence_id, set()).add(beat.actor_id)
    return used


def _exact_block(
    exact: ExactEvidence,
    *,
    kind: ComposerContextKind,
    applicable: tuple[TypedId, ...],
) -> ComposerContextBlock:
    return ComposerContextBlock(
        context_id=exact.evidence_id,
        source=ComposerContextSource.EXACT_EVIDENCE,
        kind=kind,
        applicable_character_ids=applicable,
        exact_evidence=exact,
        craft_reference_id=None,
        craft_text=None,
        content_class=exact.metadata.content_class,
    )


def _effective_projection(
    request: SceneComposerRequest,
    character_id: TypedId,
    mandatory: dict[str, ExactEvidence],
    cited_by_id: dict[TypedId, ExactEvidence],
) -> EffectiveCharacterProjection:
    """Bind the selected character's stable base and current cited state.

    The projection contains IDs and semantic section names only.  Exact content
    remains in privacy-checked context blocks, preventing a second ungoverned
    character-card representation from drifting away from evidence.
    """

    snapshot = request.prepared_turn.evidence_snapshot
    cited = tuple(
        value
        for value in cited_by_id.values()
        if character_id in value.subject_ids
    )
    development = tuple(
        value.metadata.record_id
        for value in cited
        if value.metadata.record_type is EvidenceRecordType.DEVELOPMENT
    )
    current = [mandatory["initial_stance_toward_ted"].metadata.record_id]
    current.extend(
        value.metadata.record_id
        for value in cited
        if value.metadata.record_type
        in {
            EvidenceRecordType.MEMORY,
            EvidenceRecordType.RELATIONSHIP,
            EvidenceRecordType.GENESIS_FACT,
        }
        and value.metadata.record_id not in current
    )
    return EffectiveCharacterProjection(
        schema_version=EffectiveCharacterProjection.SCHEMA_VERSION,
        character_id=character_id,
        genesis_revision_id=snapshot.genesis_revision_id,
        branch_id=snapshot.branch_id,
        generation=snapshot.generation,
        snapshot_token=snapshot.snapshot_token,
        invariant_record_ids=(
            mandatory["core_premise"].metadata.record_id,
            mandatory["fidelity_invariants"].metadata.record_id,
        ),
        current_state_record_ids=tuple(current),
        voice_record_ids=(mandatory["speech_system"].metadata.record_id,),
        development_record_ids=tuple(dict.fromkeys(development)),
        selected_section_names=_REQUIRED_CHARACTER_PROJECTION_TAGS,
        explicit_unknowns=request.reasoner_outcome.decision.uncertainties,
        prohibited_inferences=request.reasoner_outcome.decision.prohibited_inferences,
    )


_VOICE_HEADING = re.compile(r"(?m)^\*\*(?P<label>[^*\n]+?)\*\*(?P<tail>[^\n]*)")


def _selected_voice_block(
    exact: ExactEvidence,
    character_id: TypedId,
    need: CharacterCardSectionNeed,
) -> ComposerContextBlock:
    try:
        decoded = json.loads(exact.sections_json)
        source_text = decoded["source_text"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise ComposerContextAssemblyError(
            ErrorCode.COMPOSER_CONTRACT_INVALID,
            "speech-system evidence cannot be section-selected",
        ) from exc
    if not isinstance(source_text, str) or not source_text.strip():
        raise ComposerContextAssemblyError(
            ErrorCode.COMPOSER_CONTRACT_INVALID,
            "speech-system source text is empty",
        )
    matches = list(_VOICE_HEADING.finditer(source_text))
    if not matches:
        raise ComposerContextAssemblyError(
            ErrorCode.COMPOSER_CONTRACT_INVALID,
            "speech-system card has no selectable sections",
        )
    sections: list[tuple[str, str]] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(source_text)
        label = match.group("label").strip().rstrip(":")
        sections.append((label, source_text[match.start() : end].strip()))
    selected_indexes: list[int] = []
    baseline = [index for index, (label, _) in enumerate(sections) if _normalized(label) == "baseline"]
    if len(baseline) != 1:
        raise ComposerContextAssemblyError(
            ErrorCode.COMPOSER_CONTRACT_INVALID,
            "speech-system card requires one baseline section",
        )
    selected_indexes.append(baseline[0])
    for query in need.section_queries:
        normalized = _normalized(query)
        candidates = [
            index
            for index, (label, _) in enumerate(sections)
            if normalized == _normalized(label)
            or normalized in _normalized(label)
            or _normalized(label) in normalized
        ]
        if len(candidates) != 1:
            raise ComposerContextAssemblyError(
                ErrorCode.COMPOSER_CONTRACT_INVALID,
                f"realization-card section query is missing or ambiguous: {query}",
            )
        if candidates[0] not in selected_indexes:
            selected_indexes.append(candidates[0])
    selected_indexes.sort()
    labels = tuple(sections[index][0] for index in selected_indexes)
    selected_text = "\n\n".join(sections[index][1] for index in selected_indexes)
    selection_key = f"{exact.evidence_id}|{'|'.join(labels)}|{text_sha256(selected_text)}"
    view = ComposerEvidenceSectionView(
        selection_id=deterministic_id(
            IdKind.REALIZATION_SECTION,
            "cera.composer_realization_section.v1",
            selection_key,
        ),
        evidence_id=exact.evidence_id,
        subject_ids=exact.subject_ids,
        metadata=exact.metadata,
        source_sections_sha256=text_sha256(exact.sections_json),
        selected_heading_paths=labels,
        selected_text=selected_text,
        selected_text_sha256=text_sha256(selected_text),
        selection_policy_version=CONTEXT_SELECTION_POLICY_VERSION,
    )
    return ComposerContextBlock(
        context_id=view.selection_id,
        source=ComposerContextSource.EVIDENCE_SECTION_VIEW,
        kind=ComposerContextKind.CHARACTER_VOICE,
        applicable_character_ids=(character_id,),
        exact_evidence=None,
        craft_reference_id=None,
        craft_text=None,
        content_class=exact.metadata.content_class,
        evidence_section_view=view,
    )


def _normalized(value: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", value.casefold()).split())


def _context_kind(exact: ExactEvidence) -> ComposerContextKind:
    record_type = exact.metadata.record_type
    tags = set(exact.metadata.tags)
    if "character_expression" in tags:
        return ComposerContextKind.CHARACTER_EXPRESSION
    if "speech_system" in tags:
        return ComposerContextKind.CHARACTER_VOICE
    if record_type is EvidenceRecordType.RELATIONSHIP or "relationship" in tags:
        return ComposerContextKind.RELATIONSHIP
    if record_type in {EvidenceRecordType.MEMORY, EvidenceRecordType.DEVELOPMENT} or (
        "memory" in tags or "genesis_memory" in tags or "development" in tags
    ):
        return ComposerContextKind.CURRENT_STATE
    if record_type is EvidenceRecordType.MATERIAL:
        return ComposerContextKind.MATERIAL
    if record_type in {EvidenceRecordType.EVENT_FACT, EvidenceRecordType.SOURCE_FACT}:
        return ComposerContextKind.CONTINUITY
    return ComposerContextKind.CHARACTER_IDENTITY


def _unique_continuity(
    values: tuple[ComposerContinuityReference, ...],
) -> tuple[ComposerContinuityReference, ...]:
    by_id: dict[TypedId, ComposerContinuityReference] = {}
    for value in values:
        existing = by_id.get(value.evidence_id)
        if existing is not None and existing != value:
            raise ComposerContextAssemblyError(
                ErrorCode.COMPOSER_CONTRACT_INVALID,
                "Composer continuity evidence identity is ambiguous",
            )
        by_id[value.evidence_id] = value
    return tuple(by_id.values())
