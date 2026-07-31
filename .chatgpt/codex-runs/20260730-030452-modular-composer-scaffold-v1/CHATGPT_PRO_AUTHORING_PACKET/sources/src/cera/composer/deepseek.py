"""Typed one-shot DeepSeek Scene Composer adapter.

DeepSeek realizes prose from a Python-validated decision and bounded context.  It
does not decide cast, floor, character intent, authority, or durable state.
Provider declarations are decoded strictly and converted into Python-owned
candidate IDs, hashes, offsets, and receipts before the coordinator validates
the result.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import ClassVar

from cera.contracts import BeatState, build_scene_development_contract
from cera.errors import ContractValidationError, ErrorCode, IdentityError
from cera.ids import IdKind, TypedId, deterministic_id, require_kind
from cera.providers import (
    DeepSeekChatTransport,
    DeepSeekMessage,
    ProviderSchemaDialect,
    ProviderOutputMode,
    ProviderTransportError,
    project_provider_output_schema,
)
from cera.schema import from_mapping, require_schema
from cera.serialization import canonical_json, text_sha256, to_primitive

from .fake import SceneComposerPort, SceneComposerPortFailure
from .models import (
    AdultSpecificityCoverage,
    CharacterMoveRealization,
    CharacterRealization,
    ComposerAdapterRole,
    ComposerCandidate,
    ComposerSubmission,
    RealizationKind,
    RealizationManifest,
    RealizationSpan,
    SceneComposerAdapterCall,
    SceneComposerRequest,
    SemanticCategory,
    SemanticInference,
    SourceUnitCoverage,
    StoryTextRange,
)
from .obligations import (
    ComposerOutputObligations,
    build_composer_output_obligations,
    evaluate_draft_obligations,
    typed_decoding_diagnostic,
)


DEEPSEEK_COMPOSER_ADAPTER_VERSION = "cera.deepseek_scene_composer.v19"
DEEPSEEK_COMPOSER_PACKET_VERSION = "cera.deepseek_scene_composer_packet.v12"
DEEPSEEK_COMPOSER_RESPONSE_VERSION = "cera.deepseek_composition_draft.v2"
DEEPSEEK_COMPOSER_PROMPT_VERSION = "cera.deepseek_scene_composer_prompt.v18"
CHARACTER_EXPRESSION_CONTRACT_VERSION = (
    "cera.character_specific_speech_realization.v1_2"
)
CHARACTER_EXPRESSION_SOURCE_SHA256 = (
    "e61c8b9a287e6be1223e4a2a2aebcbd8d5da5e5433bee8de781d833149cc11cf"
)
DEEPSEEK_BINDING_PLACEHOLDER = "python_computed_from_decision"


@dataclass(frozen=True, slots=True)
class QuoteAnchor:
    """Provider quote plus a compatibility sentinel; Python owns occurrence."""

    quote: str
    occurrence: int

    def __post_init__(self) -> None:
        if not isinstance(self.quote, str) or not self.quote.strip():
            raise ContractValidationError("Composer quote anchor must be non-empty")
        if type(self.occurrence) is not int or self.occurrence != 0:
            raise ContractValidationError(
                "Composer quote occurrence is a zero compatibility sentinel"
            )


@dataclass(frozen=True, slots=True)
class SourceCoverageDraft:
    source_unit_id: TypedId
    ordinal: int
    anchor: QuoteAnchor
    preserved_state: BeatState

    def __post_init__(self) -> None:
        require_kind(self.source_unit_id, IdKind.SOURCE_UNIT, "source_unit_id")
        if type(self.ordinal) is not int or self.ordinal < 0:
            raise ContractValidationError("source coverage ordinal must be non-negative")


@dataclass(frozen=True, slots=True)
class CharacterSpanDraft:
    owner_id: TypedId
    kind: RealizationKind
    anchor: QuoteAnchor
    source_unit_id: TypedId | None
    beat_id: TypedId | None
    realized_state: BeatState | None

    def __post_init__(self) -> None:
        require_kind(self.owner_id, IdKind.CHARACTER, "owner_id")
        if self.source_unit_id is not None:
            require_kind(self.source_unit_id, IdKind.SOURCE_UNIT, "source_unit_id")
        if self.beat_id is not None:
            require_kind(self.beat_id, IdKind.BEAT, "beat_id")


@dataclass(frozen=True, slots=True)
class ComposerAdvisoryNote:
    kind: str
    summary: str

    def __post_init__(self) -> None:
        if not self.kind.strip() or not self.summary.strip():
            raise ContractValidationError("Composer advisory note must be non-empty")


@dataclass(frozen=True, slots=True)
class DeepSeekComposerResponse:
    SCHEMA_VERSION: ClassVar[str] = DEEPSEEK_COMPOSER_RESPONSE_VERSION

    schema_version: str
    story_text: str
    complete_core: bool
    is_outline: bool
    is_partial_draft: bool
    awaits_detailer: bool
    contains_internal_labels: bool
    contains_ui_markup: bool
    contains_provider_diagnostics: bool
    floor_owner_id: TypedId
    move_realizations: tuple[CharacterMoveRealization, ...]
    participant_realizations: tuple[CharacterRealization, ...]
    realized_beat_ids: tuple[TypedId, ...]
    source_unit_coverage: tuple[SourceCoverageDraft, ...]
    character_spans: tuple[CharacterSpanDraft, ...]
    semantic_inferences: tuple[SemanticInference, ...]
    terminal_state_preserved: bool
    stops_before_protected_user_choice: bool
    introduced_major_objective_or_participant: bool
    advisory_notes: tuple[ComposerAdvisoryNote, ...]

    def __post_init__(self) -> None:
        require_schema(self.schema_version, self.SCHEMA_VERSION, type(self).__name__)
        if not isinstance(self.story_text, str) or not self.story_text.strip():
            raise ContractValidationError("DeepSeek story text must be non-empty")
        require_kind(self.floor_owner_id, IdKind.CHARACTER, "floor_owner_id")
        for beat_id in self.realized_beat_ids:
            require_kind(beat_id, IdKind.BEAT, "realized_beat_ids")


@dataclass(frozen=True, slots=True)
class SourceCoverageAnchorV2:
    source_unit_id: TypedId
    anchor: QuoteAnchor

    def __post_init__(self) -> None:
        require_kind(self.source_unit_id, IdKind.SOURCE_UNIT, "source_unit_id")


@dataclass(frozen=True, slots=True)
class RealizationAnchorV2:
    owner_id: TypedId
    kind: RealizationKind
    anchor: QuoteAnchor
    source_unit_id: TypedId | None
    beat_id: TypedId | None

    def __post_init__(self) -> None:
        require_kind(self.owner_id, IdKind.CHARACTER, "owner_id")
        if self.source_unit_id is not None:
            require_kind(self.source_unit_id, IdKind.SOURCE_UNIT, "source_unit_id")
        if self.beat_id is not None:
            require_kind(self.beat_id, IdKind.BEAT, "beat_id")
        if self.source_unit_id is None and self.beat_id is None:
            raise ContractValidationError(
                "realization anchor requires source or decision-beat authority"
            )


@dataclass(frozen=True, slots=True)
class DeepSeekCompositionDraftV2:
    """Minimal creative DTO; never an authoritative realization manifest."""

    SCHEMA_VERSION: ClassVar[str] = DEEPSEEK_COMPOSER_RESPONSE_VERSION

    schema_version: str
    story_text: str
    source_coverage_anchors: tuple[SourceCoverageAnchorV2, ...]
    realization_anchors: tuple[RealizationAnchorV2, ...]
    terminal_boundary_anchor: QuoteAnchor

    def __post_init__(self) -> None:
        require_schema(self.schema_version, self.SCHEMA_VERSION, type(self).__name__)
        if not self.story_text.strip():
            raise ContractValidationError("DeepSeek story text must be non-empty")
        _unique(
            (value.source_unit_id for value in self.source_coverage_anchors),
            "source coverage anchors",
        )


@dataclass(frozen=True, slots=True)
class StorySegmentDraftV3:
    """One provider-authored prose segment with a stable local label."""

    segment_key: str
    text: str

    def __post_init__(self) -> None:
        if re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", self.segment_key) is None:
            raise ContractValidationError("story segment key is invalid")
        if not isinstance(self.text, str) or not self.text.strip():
            raise ContractValidationError("story segment text must be non-empty")


@dataclass(frozen=True, slots=True)
class SourceCoverageSegmentRefV3:
    source_unit_id: TypedId
    segment_key: str

    def __post_init__(self) -> None:
        require_kind(self.source_unit_id, IdKind.SOURCE_UNIT, "source_unit_id")
        if not self.segment_key.strip():
            raise ContractValidationError("source coverage segment key is required")


@dataclass(frozen=True, slots=True)
class RealizationSegmentRefV3:
    owner_id: TypedId
    kind: RealizationKind
    segment_key: str
    source_unit_id: TypedId | None
    beat_id: TypedId | None

    def __post_init__(self) -> None:
        require_kind(self.owner_id, IdKind.CHARACTER, "owner_id")
        if not self.segment_key.strip():
            raise ContractValidationError("realization segment key is required")
        if self.source_unit_id is not None:
            require_kind(self.source_unit_id, IdKind.SOURCE_UNIT, "source_unit_id")
        if self.beat_id is not None:
            require_kind(self.beat_id, IdKind.BEAT, "beat_id")
        if self.source_unit_id is None and self.beat_id is None:
            raise ContractValidationError(
                "realization segment reference requires source or beat authority"
            )


@dataclass(frozen=True, slots=True)
class DeepSeekCompositionDraftV3:
    """Active creative DTO; Python concatenates and anchors labeled segments."""

    SCHEMA_VERSION: ClassVar[str] = "cera.deepseek_composition_draft.v3"

    schema_version: str
    story_segments: tuple[StorySegmentDraftV3, ...]
    source_coverage_segments: tuple[SourceCoverageSegmentRefV3, ...]
    realization_segments: tuple[RealizationSegmentRefV3, ...]
    terminal_segment_key: str

    def __post_init__(self) -> None:
        require_schema(self.schema_version, self.SCHEMA_VERSION, type(self).__name__)
        if not self.story_segments:
            raise ContractValidationError("DeepSeek story segments must be non-empty")
        _unique(
            (value.segment_key for value in self.story_segments),
            "story segment keys",
        )
        _unique(
            (value.source_unit_id for value in self.source_coverage_segments),
            "source coverage segment references",
        )
        keys = {value.segment_key for value in self.story_segments}
        referenced = {
            *(value.segment_key for value in self.source_coverage_segments),
            *(value.segment_key for value in self.realization_segments),
        }
        if not referenced.issubset(keys):
            raise ContractValidationError("Composer references an unknown story segment")
        if self.terminal_segment_key != self.story_segments[-1].segment_key:
            raise ContractValidationError(
                "terminal segment key must name the final ordered segment"
            )
        _unique(
            (
                f"{value.owner_id}|{value.kind.value}|{value.segment_key}|"
                f"{value.source_unit_id}|{value.beat_id}"
                for value in self.realization_segments
            ),
            "realization segment references",
        )


@dataclass(frozen=True, slots=True)
class SourceCoverageSegmentsV4:
    """One Python-owned source obligation mapped to ordered prose segments."""

    source_unit_id: TypedId
    segment_keys: tuple[str, ...]

    def __post_init__(self) -> None:
        require_kind(self.source_unit_id, IdKind.SOURCE_UNIT, "source_unit_id")
        if not self.segment_keys:
            raise ContractValidationError(
                "source coverage requires at least one segment key"
            )
        _unique(self.segment_keys, "source coverage segment keys")


@dataclass(frozen=True, slots=True)
class SpecificityCoverageSegmentsV4:
    """Provider references an advertised Python-owned craft obligation."""

    obligation_key: str
    segment_keys: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.obligation_key.strip() or not self.segment_keys:
            raise ContractValidationError(
                "specificity coverage requires an obligation and segment keys"
            )
        _unique(self.segment_keys, "specificity coverage segment keys")


@dataclass(frozen=True, slots=True)
class DeepSeekCompositionDraftV4:
    """Active creative DTO with Python-owned multi-segment obligations."""

    SCHEMA_VERSION: ClassVar[str] = "cera.deepseek_composition_draft.v4"

    schema_version: str
    story_segments: tuple[StorySegmentDraftV3, ...]
    source_coverage: tuple[SourceCoverageSegmentsV4, ...]
    realization_segments: tuple[RealizationSegmentRefV3, ...]
    specificity_coverage: tuple[SpecificityCoverageSegmentsV4, ...]
    terminal_segment_key: str

    def __post_init__(self) -> None:
        require_schema(self.schema_version, self.SCHEMA_VERSION, type(self).__name__)
        if not self.story_segments:
            raise ContractValidationError("DeepSeek story segments must be non-empty")
        _unique(
            (value.segment_key for value in self.story_segments),
            "story segment keys",
        )
        _unique(
            (value.source_unit_id for value in self.source_coverage),
            "source coverage obligations",
        )
        _unique(
            (value.obligation_key for value in self.specificity_coverage),
            "specificity coverage obligations",
        )
        keys = {value.segment_key for value in self.story_segments}
        referenced = {
            *(
                key
                for value in self.source_coverage
                for key in value.segment_keys
            ),
            *(value.segment_key for value in self.realization_segments),
            *(
                key
                for value in self.specificity_coverage
                for key in value.segment_keys
            ),
        }
        if not referenced.issubset(keys):
            raise ContractValidationError("Composer references an unknown story segment")
        if self.terminal_segment_key != self.story_segments[-1].segment_key:
            raise ContractValidationError(
                "terminal segment key must name the final ordered segment"
            )
        _unique(
            (
                f"{value.owner_id}|{value.kind.value}|{value.segment_key}|"
                f"{value.source_unit_id}|{value.beat_id}"
                for value in self.realization_segments
            ),
            "realization segment references",
        )


@dataclass(frozen=True, slots=True)
class RealizationSegmentRefV5:
    """One prose segment bound to exactly one source or decision authority."""

    owner_id: TypedId
    kind: RealizationKind
    segment_key: str
    authority_id: TypedId

    def __post_init__(self) -> None:
        require_kind(self.owner_id, IdKind.CHARACTER, "owner_id")
        if not self.segment_key.strip():
            raise ContractValidationError("realization segment key is required")
        if self.authority_id.kind not in {IdKind.SOURCE_UNIT, IdKind.BEAT}:
            raise ContractValidationError(
                "realization authority must be one source unit or decision beat"
            )


@dataclass(frozen=True, slots=True)
class DeepSeekCompositionDraftV5:
    """Active DTO without nullable cross-field realization authority."""

    SCHEMA_VERSION: ClassVar[str] = "cera.deepseek_composition_draft.v5"

    schema_version: str
    story_segments: tuple[StorySegmentDraftV3, ...]
    source_coverage: tuple[SourceCoverageSegmentsV4, ...]
    realization_segments: tuple[RealizationSegmentRefV5, ...]
    specificity_coverage: tuple[SpecificityCoverageSegmentsV4, ...]
    terminal_segment_key: str

    def __post_init__(self) -> None:
        require_schema(self.schema_version, self.SCHEMA_VERSION, type(self).__name__)
        if not self.story_segments:
            raise ContractValidationError("DeepSeek story segments must be non-empty")
        _unique((value.segment_key for value in self.story_segments), "story segment keys")
        _unique(
            (value.source_unit_id for value in self.source_coverage),
            "source coverage obligations",
        )
        _unique(
            (value.obligation_key for value in self.specificity_coverage),
            "specificity coverage obligations",
        )
        keys = {value.segment_key for value in self.story_segments}
        referenced = {
            *(key for value in self.source_coverage for key in value.segment_keys),
            *(value.segment_key for value in self.realization_segments),
            *(key for value in self.specificity_coverage for key in value.segment_keys),
        }
        if not referenced.issubset(keys):
            raise ContractValidationError("Composer references an unknown story segment")
        if self.terminal_segment_key != self.story_segments[-1].segment_key:
            raise ContractValidationError(
                "terminal segment key must name the final ordered segment"
            )
        _unique(
            (
                f"{value.owner_id}|{value.kind.value}|{value.segment_key}|"
                f"{value.authority_id}"
                for value in self.realization_segments
            ),
            "realization segment references",
        )


@dataclass(frozen=True, slots=True)
class RealizationSegmentRefV6:
    """Creative segment reference; Python derives its owner from authority_id."""

    kind: RealizationKind
    segment_key: str
    authority_id: TypedId

    def __post_init__(self) -> None:
        if not self.segment_key.strip():
            raise ContractValidationError("realization segment key is required")
        if self.authority_id.kind not in {IdKind.SOURCE_UNIT, IdKind.BEAT}:
            raise ContractValidationError(
                "realization authority must be one source unit or decision beat"
            )


@dataclass(frozen=True, slots=True)
class DeepSeekCompositionDraftV6:
    """Active creative DTO with Python-owned realization character identity."""

    SCHEMA_VERSION: ClassVar[str] = "cera.deepseek_composition_draft.v6"

    schema_version: str
    story_segments: tuple[StorySegmentDraftV3, ...]
    source_coverage: tuple[SourceCoverageSegmentsV4, ...]
    realization_segments: tuple[RealizationSegmentRefV6, ...]
    specificity_coverage: tuple[SpecificityCoverageSegmentsV4, ...]
    terminal_segment_key: str

    def __post_init__(self) -> None:
        require_schema(self.schema_version, self.SCHEMA_VERSION, type(self).__name__)
        if not self.story_segments:
            raise ContractValidationError("DeepSeek story segments must be non-empty")
        _unique((value.segment_key for value in self.story_segments), "story segment keys")
        _unique(
            (value.source_unit_id for value in self.source_coverage),
            "source coverage obligations",
        )
        _unique(
            (value.obligation_key for value in self.specificity_coverage),
            "specificity coverage obligations",
        )
        keys = {value.segment_key for value in self.story_segments}
        referenced = {
            *(key for value in self.source_coverage for key in value.segment_keys),
            *(value.segment_key for value in self.realization_segments),
            *(key for value in self.specificity_coverage for key in value.segment_keys),
        }
        if not referenced.issubset(keys):
            raise ContractValidationError("Composer references an unknown story segment")
        if self.terminal_segment_key != self.story_segments[-1].segment_key:
            raise ContractValidationError(
                "terminal segment key must name the final ordered segment"
            )
        # Exact duplicate realization references are non-semantic provider
        # bookkeeping. Python removes them deterministically before domain
        # validation; differing kinds, segments, or authorities remain distinct.


class DeepSeekSceneComposerPort(SceneComposerPort):
    """One DeepSeek call producing a non-authoritative prose candidate."""

    def __init__(self, transport: DeepSeekChatTransport) -> None:
        self.transport = transport

    def compose(self, request: SceneComposerRequest) -> SceneComposerAdapterCall:
        self._validate_context(request)
        obligations = build_composer_output_obligations(request)
        packet = build_deepseek_composer_packet(
            request,
            obligations=obligations,
        )
        messages = build_deepseek_composer_messages(packet)
        try:
            provider_result = self.transport.invoke(
                messages,
                output_mode=ProviderOutputMode.JSON_OBJECT,
                thinking_enabled=False,
            )
        except ProviderTransportError as exc:
            raise SceneComposerPortFailure(
                exc.code,
                str(exc),
                "composer_dispatch",
            ) from exc

        try:
            if provider_result.parsed_json is None:
                raise ContractValidationError("DeepSeek Composer omitted its JSON object")
            response_version = provider_result.parsed_json.get("schema_version")
            if response_version != DeepSeekCompositionDraftV6.SCHEMA_VERSION:
                raise ContractValidationError(
                    "active DeepSeek Composer response schema is not v6"
                )
            response = from_mapping(
                DeepSeekCompositionDraftV6,
                provider_result.parsed_json,
            )
            compiled_response = _compile_v6_python_owners(response, obligations)
            diagnostics = evaluate_draft_obligations(
                compiled_response,
                obligations,
            )
            if diagnostics:
                raise SceneComposerPortFailure(
                    ErrorCode.COMPOSER_CONTRACT_INVALID,
                    (
                        "DeepSeek Composer output failed request-local "
                        "atomic obligations"
                    ),
                    "composer_validation",
                    safe_diagnostics=tuple(
                        value.value for value in diagnostics
                    ),
                )
            submission = _build_submission_v5(request, compiled_response)
        except SceneComposerPortFailure as exc:
            exc.provider_call_receipt = provider_result.receipt
            raise
        except (ContractValidationError, IdentityError) as exc:
            failure = SceneComposerPortFailure(
                ErrorCode.COMPOSER_CONTRACT_INVALID,
                "DeepSeek Composer output failed typed decoding",
                "composer_validation",
                safe_diagnostics=(typed_decoding_diagnostic(exc).value,),
            )
            failure.provider_call_receipt = provider_result.receipt
            raise failure from exc

        receipt = provider_result.receipt
        return SceneComposerAdapterCall(
            submission=submission,
            adapter_role=ComposerAdapterRole.DEEPSEEK,
            adapter_version=DEEPSEEK_COMPOSER_ADAPTER_VERSION,
            adapter_evidence_id=self.transport.route.route_id,
            adapter_evidence_sha256=self.transport.route.route_sha256,
            provider_receipt_id=receipt.provider_receipt_id,
            provider_receipt_sha256=receipt.receipt_sha256,
            external_provider_calls=receipt.external_provider_calls,
            validated_obligation_sha256=obligations.obligation_sha256,
            provider_call_receipt=receipt,
        )

    @staticmethod
    def _validate_context(request: SceneComposerRequest) -> None:
        context = request.realization_context
        if context is None:
            raise SceneComposerPortFailure(
                ErrorCode.COMPOSER_CONTRACT_INVALID,
                "DeepSeek Composer requires a bounded realization context",
                "composer_context",
            )
        covered: set[TypedId] = set()
        for block in context.blocks:
            if block.kind.value in {
                "character_identity",
                "character_voice",
                "character_expression",
            }:
                covered.update(block.applicable_character_ids)
        missing = set(request.selected_npc_ids) - covered
        if missing:
            raise SceneComposerPortFailure(
                ErrorCode.COMPOSER_CONTRACT_INVALID,
                "DeepSeek Composer lacks identity or voice context for a selected NPC",
                "composer_context",
            )


def build_deepseek_composer_packet(
    request: SceneComposerRequest,
    *,
    obligations: ComposerOutputObligations | None = None,
) -> dict[str, object]:
    if request.realization_context is None:
        raise ContractValidationError("DeepSeek packet requires realization context")
    obligations = obligations or build_composer_output_obligations(request)
    provider_schema = project_provider_output_schema(
        deepseek_composition_draft_v6_json_schema(),
        ProviderSchemaDialect.DEEPSEEK_JSON_OBJECT_PROMPT_V1,
    ).provider_schema
    bind_provider_schema_to_output_obligations(
        provider_schema,
        obligations,
    )
    return {
        "schema_version": DEEPSEEK_COMPOSER_PACKET_VERSION,
        "prompt_version": DEEPSEEK_COMPOSER_PROMPT_VERSION,
        "composition_dto": _minimal_composition_dto(request),
        "authority_policy": {
            "python_route_and_validation_are_authoritative": True,
            "reasoner_decision_is_binding_for_realization": True,
            "composer_output_is_creative_until_python_acceptance": True,
            "durable_state_writes_forbidden": True,
            "character_expression_contract": {
                "version": CHARACTER_EXPRESSION_CONTRACT_VERSION,
                "source_sha256": CHARACTER_EXPRESSION_SOURCE_SHA256,
                "examples_are_non_executable_and_noncopyable": True,
            },
        },
        "segment_policy": {
            "provider_supplies_ordered_labeled_prose_segments": True,
            "references_use_segment_keys_not_copied_quotes": True,
            "python_concatenates_text_and_derives_offsets_and_hashes": True,
            "segment_separator": "double_newline",
        },
        "output_obligations": obligations.to_provider_dict(),
        "output_schema": provider_schema,
    }


def bind_provider_schema_to_output_obligations(
    schema: dict[str, object],
    obligations: ComposerOutputObligations,
) -> None:
    """Narrow the prompt-visible schema to the current request's exact sets."""

    properties = schema["properties"]
    assert isinstance(properties, dict)
    source_schema = properties["source_coverage"]
    specificity_schema = properties["specificity_coverage"]
    realization_schema = properties["realization_segments"]
    assert isinstance(source_schema, dict)
    assert isinstance(specificity_schema, dict)
    assert isinstance(realization_schema, dict)

    source_ids = list(obligations.source_coverage.values)
    specificity_keys = list(obligations.specificity_coverage.values)
    _bind_exact_array_length(source_schema, len(source_ids))
    _bind_exact_array_length(specificity_schema, len(specificity_keys))

    source_items = source_schema.get("items")
    specificity_items = specificity_schema.get("items")
    realization_items = realization_schema.get("items")
    if source_ids and isinstance(source_items, dict):
        source_items["properties"]["source_unit_id"]["enum"] = source_ids
    if specificity_keys and isinstance(specificity_items, dict):
        specificity_items["properties"]["obligation_key"][
            "enum"
        ] = specificity_keys
    if isinstance(realization_items, dict):
        realization_properties = realization_items["properties"]
        realization_properties["authority_id"]["enum"] = [
            str(value.authority_id)
            for value in obligations.allowed_realization_associations
        ]

def _bind_exact_array_length(schema: dict[str, object], count: int) -> None:
    schema["maxItems"] = count
    if count:
        schema["minItems"] = count
    else:
        schema.pop("minItems", None)


def _minimal_composition_dto(
    request: SceneComposerRequest,
) -> dict[str, object]:
    """Select only creative inputs; omit receipts and duplicated bookkeeping."""

    decision = request.reasoner_outcome.decision
    assert decision is not None
    context = request.realization_context
    assert context is not None
    return {
        "identity": {
            "protected_user_id": str(
                request.prepared_turn.request.protected_user_id
            ),
            "selected_npc_ids": [str(value) for value in request.selected_npc_ids],
        },
        "source": _minimal_source_packet(request),
        "decision": _minimal_decision(decision),
        "scene_scope": request.scene_scope,
        "response_profile_version": request.response_profile_version,
        "scene_development_contract": build_scene_development_contract(
            request.scene_depth_mode
        ),
        "creator_event_coverage_required": request.creator_event_coverage_required,
        "hard_boundaries": list(request.hard_boundaries),
        "specificity_contract": (
            _minimal_specificity_contract(request.specificity_contract)
            if request.specificity_contract is not None
            else None
        ),
        "realization_context": {
            "selection_policy_version": context.selection_policy_version,
            "blocks": [_minimal_context_block(value) for value in context.blocks],
            "explicit_exclusions": list(context.explicit_exclusions),
        },
    }


def _minimal_source_packet(request: SceneComposerRequest) -> dict[str, object]:
    source = request.source_packet
    return {
        "mode": source.mode.value,
        "ordinary_units": (
            [_minimal_source_unit(value) for value in source.ordinary_units]
            if source.protected_envelope is None
            else []
        ),
        "protected_envelope": (
            {
                "units": [
                    _minimal_source_unit(value)
                    for value in source.protected_envelope.units
                ]
            }
            if source.protected_envelope is not None
            else None
        ),
    }


def _minimal_source_unit(value) -> dict[str, object]:
    return {
        "source_unit_id": str(value.source_unit_id),
        "classification": value.classification.value,
        "exact_text": value.exact_text,
        "protected_user_allowed_kinds": [
            item.value for item in value.protected_user_allowed_kinds
        ],
        "required_state": (
            value.required_state.value
            if value.required_state is not None
            else None
        ),
        "participant_ids": [str(item) for item in value.participant_ids],
    }


def _minimal_decision(decision) -> dict[str, object]:
    return {
        "route": decision.route.value,
        "scene_intent": decision.scene_intent,
        "responding_npc_ids": [
            str(value) for value in decision.responding_npc_ids
        ],
        "floor_owner_id": (
            str(decision.floor_owner_id)
            if decision.floor_owner_id is not None
            else None
        ),
        "character_moves": [
            {
                "character_id": str(value.character_id),
                "perception": value.perception,
                "selected_intent": value.selected_intent,
                "action_direction": value.action_direction,
                "knowledge_constraints": list(value.knowledge_constraints),
            }
            for value in decision.character_moves
        ],
        "current_segment": {
            "ordered_beats": [
                {
                    "beat_id": str(value.beat_id),
                    "actor_id": str(value.actor_id),
                    "state": value.state.value,
                    "neutral_event": value.neutral_event,
                }
                for value in decision.current_segment.ordered_beats
            ],
            "stop_before": decision.current_segment.stop_before,
        },
        "future_segments": [
            {
                "activation_conditions": list(value.activation_conditions),
                "invalidation_conditions": list(value.invalidation_conditions),
                "possible_consequences": list(value.possible_consequences),
                "open_user_choice": value.open_user_choice,
            }
            for value in decision.future_segments
        ],
        "writer_must_preserve": list(decision.writer_must_preserve),
        "uncertainties": list(decision.uncertainties),
        "prohibited_inferences": list(decision.prohibited_inferences),
    }


def _minimal_specificity_contract(contract) -> dict[str, object]:
    return {
        "beat_requirements": [
            {
                "obligation_key": value.obligation_key,
                "beat_id": str(value.beat_id),
                "channel": value.channel.value,
                "character_id": (
                    str(value.character_id)
                    if value.character_id is not None
                    else None
                ),
                "minimum_register": value.minimum_register.value,
                "terms": [
                    {
                        "concept": item.concept.value,
                        "alternatives": list(item.alternatives),
                        "minimum_matches": item.minimum_matches,
                    }
                    for item in value.terms
                ],
            }
            for value in contract.beat_requirements
        ],
        "climax": to_primitive(contract.climax),
        "aftermath": to_primitive(contract.aftermath),
        "conditional_layers_not_quotas": contract.conditional_layers_not_quotas,
        "bodily_response_not_consent": contract.bodily_response_not_consent,
        "blocked_nonconsensual_generation_excluded": (
            contract.blocked_nonconsensual_generation_excluded
        ),
    }


def _minimal_context_block(block) -> dict[str, object]:
    payload: dict[str, object] = {
        "kind": block.kind.value,
        "applicable_character_ids": [
            str(value) for value in block.applicable_character_ids
        ],
        "content_class": block.content_class,
    }
    if block.evidence_section_view is not None:
        payload["selected_heading_paths"] = list(
            block.evidence_section_view.selected_heading_paths
        )
        payload["selected_text"] = block.evidence_section_view.selected_text
    elif block.exact_evidence is not None:
        payload["subject_ids"] = [
            str(value) for value in block.exact_evidence.subject_ids
        ]
        payload["sections"] = json.loads(block.exact_evidence.sections_json)
    elif block.craft_text is not None:
        payload["craft_text"] = block.craft_text
    return payload


def build_deepseek_composer_messages(
    packet: dict[str, object],
) -> tuple[DeepSeekMessage, ...]:
    system = "\n".join(
        (
            "You are CERA's Scene Composer. Return exactly one JSON object matching output_schema.",
            "Write the complete presentation-neutral Core reply, never an outline, partial draft, Detailer handoff, diagnostic, bookkeeping marker, or HTML.",
            "The validated decision owns cast, floor, psychology, intent, action direction, required beats, boundaries, and conditional future possibilities. Realize it; do not replace it.",
            "The typed scene_development_contract binds realization scope as well as planning scope. Do not compress a valid Long or Epic multi-beat decision into summary prose. Every materially distinct planned beat must remain identifiable and causally meaningful, although it need not receive a separate paragraph. Stop at the contract's natural protected-user handoff; do not pad merely to sound long.",
            "realization_context is selected internal CERA evidence, even when it was not visible in the SillyTavern character-card initialization. A character_voice block contains creator-authored speech rules and may include illustrative dialogue. Apply its diction, syntax, rhythm, reasoning style, emotional leakage, and situation-specific variation to the named character. Illustrative lines are voice references, not scripts, past dialogue, or events to copy. Character identity, relationship, current-state, expression, and continuity blocks constrain psychology and facts; do not flatten an evidence-rich character into a generic role.",
            "Realize the ordered current beats as a causal scene sequence, not as a compressed summary. Make every transition legible: what the NPC notices or feels, what choice or tactic follows, how it becomes concrete in action or dialogue, and what immediate pressure or floor state results, to the extent authorized by that beat and the supplied evidence.",
            "A short source cue does not require a short reply when the validated decision contains a fuller supported sequence, but causal significance and stakes still govern realized length. Give each causally distinct beat enough prose to matter. Keep an atomic exchange compact; develop a consequential ordinary or domino sequence across all of its supplied changes instead of ending after the first reaction.",
            "A beat is a causal planning unit, not a paragraph quota. Paragraphing and rhythm must feel natural and must not expose a fixed appraisal-to-tactic-to-dialogue-to-handoff scaffold. Multiple beats may share one story segment when every beat remains independently identifiable in realization_segments; use separate segments only when they improve prose coherence or keep an evidence reference meaningfully local.",
            "Do not turn sequence depth into padding. Avoid generic atmosphere, synonymous restatement, repeated hesitation, decorative motion, stacked interpretive metaphors, or rhetorical explanation that repeats the same stance without advancing character meaning, action, consequence, or the conversational floor. When adjacent beats are closely coupled, realize them in one natural passage while retaining every required realization reference. Prefer direct physical and conversational detail. Use concrete micro-actions, character-specific interiority when authorized, distinctive dialogue, and physical or environmental continuity to realize the supplied logic rather than inventing new logic.",
            "Authorized current appraisal, motive, or manner does not authorize invented biography or world facts. Interiority may elaborate present inference supported by the decision and evidence, but must not invent formative history, remembered incidents, repeated past patterns, generalized lessons, household roles, institutional labels, established routines, prior frequency, or practiced familiarity unless that premise is explicitly supplied. Reversible present-moment texture is allowed; claims about what a character or household usually, repeatedly, or historically does are not mere texture.",
            "Do not invent action, dialogue, thought, emotion, consent, refusal, destination, commitment, or choice for the protected user beyond exact source authority.",
            "When creator_event_coverage_required is false, protected-user source is causal context for the NPC reply, not a prose checklist. Begin at the selected NPCs' current reaction and do not restage the protected user's earlier setup as a new action. An NPC may notice or refer to the already-established result, but must not extend it with a new protected-user movement, message, reply, expression, intention, or off-page activity.",
            "Protected-user invention includes relational or reciprocal wording that silently presupposes the user's half of an interaction. Unless exact source authority supplies that half, write only the selected NPC's one-way action or perception: use forms like 'she looks at him' rather than 'she meets his gaze,' and 'she reacts to the supplied words' rather than a shared look, returned smile, answered gesture, mutual touch, or other reciprocal construction. Do not imply that the protected user looks back, smiles back, enters, moves, touches, responds, or chooses merely to make an NPC sentence flow.",
            "For protected_user_id realization_segments, use only a kind listed in that exact source unit's protected_user_allowed_kinds and bind source_unit_id. Label supplied spoken words as dialogue and supplied physical motion as action; never relabel them as vocalization, reaction, commitment, motive, emotion, or another broader interpretation.",
            "Use only the supplied realization-context blocks for character identity, voice, relationships, continuity, and craft. Creator craft examples teach technique only and never establish scene facts.",
            "When character_expression evidence is supplied, preserve the already-selected intent and boundary while making the speaker's syntax, moral reasoning, emotional strategy, and naturally relevant comparison domain specific to her. Generate fresh wording; never quote or lightly paraphrase a Genesis example.",
            "Respectful attraction is not automatically objectification. If the selected meaning is refusal, it remains refusal despite attraction, bodily response, love, prior intimacy, fantasy, possessiveness, or latent M potential. Render partial trust fracture differently from complete trust destruction.",
            "Use character_expression evidence only for its applicable selected speaker. Do not add dialogue for an unselected, absent, or unaware character, and do not turn Yuuni's adult youngest-sister identity into child framing.",
            "When an adult SpecificityContract is present, satisfy each obligation inside its exact beat and adult channel; wording elsewhere does not satisfy it. Narration, each character's dialogue, each character's inner voice, sound effects, and physiology are separate adult craft channels.",
            "Climax and aftermath each have separate authority_state and current_segment_commitment. Allowed alone never creates an outcome. Vocal, inner, sound, fluid, and body-response layers are conditional tools, never quotas or evidence of consent.",
            "Future segments remain conditional and must not be narrated as completed. Stop before the protected user's next open choice.",
            "Write the reply as ordered story_segments. Each segment_key is a short unique local label and each text is final story prose. Keep segments semantically local enough that one reference identifies meaningful evidence. Python strips outer whitespace, joins segments with a double newline, and derives all offsets and hashes. Never copy quote anchors or calculate offsets, hashes, canonical IDs, participant booleans, move manifests, or semantic inference labels.",
            "Every realization_segments entry must contain exactly one authority_id. Do not return owner_id: Python derives the character owner deterministically from that authority. For a selected NPC, authority_id must be an exact current beat ID from the decision, and the prose must realize that beat's advertised actor. For protected_user_id, authority_id must be an exact supplied source_unit_id and the realization kind must be allowed by that unit. Every required current decision beat needs at least one realization_segments entry naming that exact beat authority_id. Every selected NPC needs at least one of that NPC's beat authorities realized. The kind field is only a general story function from its advertised enum; never put adult channel names such as physiology or narration in kind.",
            "When creator_event_coverage_required is false, source_coverage must be empty. When it is true, include every supplied source unit as final story prose, then provide one source_coverage entry for each source unit in packet order. Every returned entry's segment_keys array must contain at least one existing story segment key; an empty segment_keys array is always invalid. An entry may name multiple ordered unique segment_keys when one source obligation is realized across separated prose.",
            "When a SpecificityContract is absent, specificity_coverage must be empty. When present, provide exactly one specificity_coverage entry for every advertised obligation_key. Every returned entry's segment_keys array must contain at least one existing story segment key; an empty segment_keys array is always invalid. Map it to the ordered unique segment_keys where that adult channel is actually realized. Adult channel coverage never changes or substitutes the general realization kind.",
            "Follow output_obligations literally. Realize every required beat authority_id and use only allowed authority_ids; the advertised authority-owner associations tell you whose prose each authority must represent, but Python owns and derives the owner field. For source_coverage and specificity_coverage, mode must_be_empty requires an empty output array; mode exact_sequence requires exactly the advertised values in the advertised order. For every returned coverage entry, each_returned_entry_requires_one_or_more_existing_segment_keys is mandatory: never emit segment_keys: []. These are one canonical Python-owned obligation object, not creative choices.",
            "References identify evidence locations but do not prove semantic realization; Python and independent verifiers decide that.",
            "terminal_segment_key must name the final ordered story segment, whose prose stops before the protected user's next open choice.",
            "Bodily response, vocalization, freezing, silence, compliance, or failure to resist never establish desire, pleasure, or consent.",
        )
    )
    user = "The complete authoritative Composer packet follows as canonical JSON:\n" + canonical_json(packet)
    return (DeepSeekMessage("system", system), DeepSeekMessage("user", user))


def _compile_v6_python_owners(
    response: DeepSeekCompositionDraftV6,
    obligations: ComposerOutputObligations,
) -> DeepSeekCompositionDraftV5:
    """Attach authoritative owners without trusting redundant model metadata."""

    owner_by_authority: dict[TypedId, TypedId] = {}
    for association in obligations.allowed_realization_associations:
        existing = owner_by_authority.setdefault(
            association.authority_id,
            association.owner_id,
        )
        if existing != association.owner_id:
            raise ContractValidationError(
                "one Composer authority cannot belong to multiple characters"
            )
    compiled = []
    seen: set[tuple[RealizationKind, str, TypedId]] = set()
    for value in response.realization_segments:
        key = (value.kind, value.segment_key, value.authority_id)
        if key in seen:
            continue
        seen.add(key)
        owner_id = owner_by_authority.get(value.authority_id)
        if owner_id is None:
            raise ContractValidationError(
                "Composer realization references an unadvertised authority"
            )
        compiled.append(
            RealizationSegmentRefV5(
                owner_id=owner_id,
                kind=value.kind,
                segment_key=value.segment_key,
                authority_id=value.authority_id,
            )
        )
    return DeepSeekCompositionDraftV5(
        schema_version=DeepSeekCompositionDraftV5.SCHEMA_VERSION,
        story_segments=response.story_segments,
        source_coverage=response.source_coverage,
        realization_segments=tuple(compiled),
        specificity_coverage=response.specificity_coverage,
        terminal_segment_key=response.terminal_segment_key,
    )


def _build_submission_v5(
    request: SceneComposerRequest,
    response: DeepSeekCompositionDraftV5,
) -> ComposerSubmission:
    """Translate the single-authority provider DTO into the domain manifest seam."""

    source_by_id = {
        value.source_unit_id: value for value in request.source_packet.units
    }

    def authoritative_kind(value: RealizationSegmentRefV5) -> RealizationKind:
        """Derive unambiguous protected-user bookkeeping from exact source."""

        if value.authority_id.kind is not IdKind.SOURCE_UNIT:
            return value.kind
        unit = source_by_id.get(value.authority_id)
        if unit is None or len(unit.protected_user_allowed_kinds) != 1:
            return value.kind
        return unit.protected_user_allowed_kinds[0]

    v4 = DeepSeekCompositionDraftV4(
        schema_version=DeepSeekCompositionDraftV4.SCHEMA_VERSION,
        story_segments=response.story_segments,
        source_coverage=response.source_coverage,
        realization_segments=tuple(
            RealizationSegmentRefV3(
                owner_id=value.owner_id,
                kind=authoritative_kind(value),
                segment_key=value.segment_key,
                source_unit_id=(
                    value.authority_id
                    if value.authority_id.kind is IdKind.SOURCE_UNIT
                    else None
                ),
                beat_id=(
                    value.authority_id
                    if value.authority_id.kind is IdKind.BEAT
                    else None
                ),
            )
            for value in response.realization_segments
        ),
        specificity_coverage=response.specificity_coverage,
        terminal_segment_key=response.terminal_segment_key,
    )
    return _build_submission_v4(request, v4)


def _build_submission_v4(
    request: SceneComposerRequest,
    response: DeepSeekCompositionDraftV4,
) -> ComposerSubmission:
    story_text, segment_spans = _concatenate_story_segments(response.story_segments)
    candidate = ComposerCandidate(
        schema_version=ComposerCandidate.SCHEMA_VERSION,
        candidate_id=deterministic_id(
            IdKind.COMPOSER_CANDIDATE,
            "cera.deepseek_composer.candidate.v3",
            f"{request.request_sha256}|{text_sha256(story_text)}",
        ),
        story_text=story_text,
        story_text_sha256=text_sha256(story_text),
        complete_core=_draft_has_structural_core_v4(request, response),
        is_outline=_looks_like_outline(story_text),
        is_partial_draft=_looks_like_partial_draft(story_text),
        awaits_detailer=_awaits_detailer(story_text),
        contains_internal_labels=_contains_internal_labels(story_text),
        contains_ui_markup=_contains_ui_markup(story_text),
        contains_provider_diagnostics=_contains_provider_diagnostics(story_text),
    )
    coverage = (
        tuple(
            _coverage_from_segments_v4(request, value, ordinal, segment_spans)
            for ordinal, value in enumerate(response.source_coverage)
        )
        if request.creator_event_coverage_required
        else ()
    )
    spans = tuple(
        _span_from_segment_v3(request, value, segment_spans)
        for value in response.realization_segments
    )
    specificity_coverage = _specificity_coverage_from_segments_v4(
        request,
        response.specificity_coverage,
        segment_spans,
    )
    decision = request.reasoner_outcome.decision
    assert decision is not None
    span_by_owner = {
        owner_id: tuple(value for value in spans if value.owner_id == owner_id)
        for owner_id in request.selected_npc_ids
    }
    participant_realizations = tuple(
        CharacterRealization(
            character_id=owner_id,
            speaks=any(
                value.kind in {RealizationKind.DIALOGUE, RealizationKind.VOCALIZATION}
                for value in span_by_owner[owner_id]
            ),
            visibly_acts=any(
                value.kind not in {RealizationKind.DIALOGUE, RealizationKind.VOCALIZATION}
                for value in span_by_owner[owner_id]
            )
            or not any(
                value.kind in {RealizationKind.DIALOGUE, RealizationKind.VOCALIZATION}
                for value in span_by_owner[owner_id]
            ),
        )
        for owner_id in request.selected_npc_ids
        if span_by_owner[owner_id]
    )
    anchored_beat_ids = {
        value.beat_id for value in spans if value.beat_id is not None
    }
    realized_beat_ids = tuple(
        value.beat_id
        for value in decision.current_segment.ordered_beats
        if value.beat_id in anchored_beat_ids
    )
    terminal_end = segment_spans[response.terminal_segment_key][1]
    manifest = RealizationManifest(
        schema_version=RealizationManifest.SCHEMA_VERSION,
        manifest_id=deterministic_id(
            IdKind.REALIZATION_MANIFEST,
            "cera.deepseek_composer.manifest.v3",
            f"{request.request_sha256}|{candidate.candidate_sha256}",
        ),
        candidate_sha256=candidate.candidate_sha256,
        decision_sha256=request.decision_sha256,
        sequence_plan_sha256=request.sequence_plan_sha256,
        floor_owner_id=decision.floor_owner_id,
        move_realizations=tuple(
            CharacterMoveRealization(
                character_id=value.character_id,
                selected_intent_sha256=text_sha256(value.selected_intent),
                action_direction_sha256=text_sha256(value.action_direction),
            )
            for value in decision.character_moves
        ),
        participant_realizations=participant_realizations,
        realized_beat_ids=realized_beat_ids,
        source_unit_coverage=coverage,
        character_spans=spans,
        semantic_inferences=(),
        terminal_state_preserved=(
            realized_beat_ids
            == tuple(value.beat_id for value in decision.current_segment.ordered_beats)
        ),
        stops_before_protected_user_choice=terminal_end == len(story_text),
        introduced_major_objective_or_participant=False,
        adult_specificity_coverage=specificity_coverage,
    )
    return ComposerSubmission(candidate, manifest, ())


def _build_submission_v3(
    request: SceneComposerRequest,
    response: DeepSeekCompositionDraftV3,
) -> ComposerSubmission:
    story_text, segment_spans = _concatenate_story_segments(response.story_segments)
    candidate_id = deterministic_id(
        IdKind.COMPOSER_CANDIDATE,
        "cera.deepseek_composer.candidate.v2",
        f"{request.request_sha256}|{text_sha256(story_text)}",
    )
    candidate = ComposerCandidate(
        schema_version=ComposerCandidate.SCHEMA_VERSION,
        candidate_id=candidate_id,
        story_text=story_text,
        story_text_sha256=text_sha256(story_text),
        complete_core=_draft_has_structural_core_v3(request, response),
        is_outline=_looks_like_outline(story_text),
        is_partial_draft=_looks_like_partial_draft(story_text),
        awaits_detailer=_awaits_detailer(story_text),
        contains_internal_labels=_contains_internal_labels(story_text),
        contains_ui_markup=_contains_ui_markup(story_text),
        contains_provider_diagnostics=_contains_provider_diagnostics(story_text),
    )
    coverage = (
        tuple(
            _coverage_from_segment_v3(request, value, ordinal, segment_spans)
            for ordinal, value in enumerate(response.source_coverage_segments)
        )
        if request.creator_event_coverage_required
        else ()
    )
    spans = tuple(
        _span_from_segment_v3(request, value, segment_spans)
        for value in response.realization_segments
    )
    decision = request.reasoner_outcome.decision
    assert decision is not None
    span_by_owner = {
        owner_id: tuple(value for value in spans if value.owner_id == owner_id)
        for owner_id in request.selected_npc_ids
    }
    participant_realizations = tuple(
        CharacterRealization(
            character_id=owner_id,
            speaks=any(
                value.kind in {RealizationKind.DIALOGUE, RealizationKind.VOCALIZATION}
                for value in span_by_owner[owner_id]
            ),
            visibly_acts=any(
                value.kind not in {RealizationKind.DIALOGUE, RealizationKind.VOCALIZATION}
                for value in span_by_owner[owner_id]
            )
            or not any(
                value.kind in {RealizationKind.DIALOGUE, RealizationKind.VOCALIZATION}
                for value in span_by_owner[owner_id]
            ),
        )
        for owner_id in request.selected_npc_ids
        if span_by_owner[owner_id]
    )
    anchored_beat_ids = {
        value.beat_id for value in spans if value.beat_id is not None
    }
    realized_beat_ids = tuple(
        value.beat_id
        for value in decision.current_segment.ordered_beats
        if value.beat_id in anchored_beat_ids
    )
    terminal_end = segment_spans[response.terminal_segment_key][1]
    manifest = RealizationManifest(
        schema_version=RealizationManifest.SCHEMA_VERSION,
        manifest_id=deterministic_id(
            IdKind.REALIZATION_MANIFEST,
            "cera.deepseek_composer.manifest.v2",
            f"{request.request_sha256}|{candidate.candidate_sha256}",
        ),
        candidate_sha256=candidate.candidate_sha256,
        decision_sha256=request.decision_sha256,
        sequence_plan_sha256=request.sequence_plan_sha256,
        floor_owner_id=decision.floor_owner_id,
        move_realizations=tuple(
            CharacterMoveRealization(
                character_id=value.character_id,
                selected_intent_sha256=text_sha256(value.selected_intent),
                action_direction_sha256=text_sha256(value.action_direction),
            )
            for value in decision.character_moves
        ),
        participant_realizations=participant_realizations,
        realized_beat_ids=realized_beat_ids,
        source_unit_coverage=coverage,
        character_spans=spans,
        semantic_inferences=(),
        terminal_state_preserved=(
            realized_beat_ids
            == tuple(value.beat_id for value in decision.current_segment.ordered_beats)
        ),
        stops_before_protected_user_choice=terminal_end == len(story_text),
        introduced_major_objective_or_participant=False,
    )
    return ComposerSubmission(candidate, manifest, ())


def _concatenate_story_segments(
    segments: tuple[StorySegmentDraftV3, ...],
) -> tuple[str, dict[str, tuple[int, int]]]:
    parts: list[str] = []
    spans: dict[str, tuple[int, int]] = {}
    cursor = 0
    for segment in segments:
        text = segment.text.strip()
        if parts:
            parts.append("\n\n")
            cursor += 2
        start = cursor
        parts.append(text)
        cursor += len(text)
        spans[segment.segment_key] = (start, cursor)
    return "".join(parts), spans


def _coverage_from_segment_v3(
    request: SceneComposerRequest,
    draft: SourceCoverageSegmentRefV3,
    ordinal: int,
    segment_spans: dict[str, tuple[int, int]],
) -> SourceUnitCoverage:
    units = request.source_packet.units
    if ordinal >= len(units) or draft.source_unit_id != units[ordinal].source_unit_id:
        raise ContractValidationError(
            "source coverage segments must follow exact source-unit order"
        )
    required_state = units[ordinal].required_state
    if required_state is None:
        raise ContractValidationError(
            "creator source coverage requires a declared progression state"
        )
    start, end = segment_spans[draft.segment_key]
    return SourceUnitCoverage(
        source_unit_id=draft.source_unit_id,
        ordinal=ordinal,
        start=start,
        end=end,
        preserved_state=required_state,
    )


def _ordered_ranges(
    segment_keys: tuple[str, ...],
    segment_spans: dict[str, tuple[int, int]],
) -> tuple[StoryTextRange, ...]:
    ranges = tuple(StoryTextRange(*segment_spans[key]) for key in segment_keys)
    if tuple(sorted(ranges, key=lambda value: (value.start, value.end))) != ranges:
        raise ContractValidationError("coverage segment keys must follow story order")
    previous_end = -1
    for value in ranges:
        if value.start < previous_end:
            raise ContractValidationError("coverage segment ranges overlap")
        previous_end = value.end
    return ranges


def _coverage_from_segments_v4(
    request: SceneComposerRequest,
    draft: SourceCoverageSegmentsV4,
    ordinal: int,
    segment_spans: dict[str, tuple[int, int]],
) -> SourceUnitCoverage:
    units = request.source_packet.units
    if ordinal >= len(units) or draft.source_unit_id != units[ordinal].source_unit_id:
        raise ContractValidationError(
            "source coverage must follow exact source-unit order"
        )
    required_state = units[ordinal].required_state
    if required_state is None:
        raise ContractValidationError(
            "creator source coverage requires a declared progression state"
        )
    ranges = _ordered_ranges(draft.segment_keys, segment_spans)
    return SourceUnitCoverage(
        source_unit_id=draft.source_unit_id,
        ordinal=ordinal,
        start=ranges[0].start,
        end=ranges[0].end,
        preserved_state=required_state,
        additional_ranges=ranges[1:],
    )


def _specificity_coverage_from_segments_v4(
    request: SceneComposerRequest,
    drafts: tuple[SpecificityCoverageSegmentsV4, ...],
    segment_spans: dict[str, tuple[int, int]],
) -> tuple[AdultSpecificityCoverage, ...]:
    contract = request.specificity_contract
    if contract is None:
        if drafts:
            raise ContractValidationError(
                "ordinary Composer response supplied adult specificity coverage"
            )
        return ()
    by_key = {value.obligation_key: value for value in contract.beat_requirements}
    if tuple(value.obligation_key for value in drafts) != tuple(by_key):
        raise ContractValidationError(
            "specificity coverage must match contract obligation order exactly"
        )
    return tuple(
        AdultSpecificityCoverage(
            obligation_key=value.obligation_key,
            beat_id=by_key[value.obligation_key].beat_id,
            channel=by_key[value.obligation_key].channel,
            character_id=by_key[value.obligation_key].character_id,
            ranges=_ordered_ranges(value.segment_keys, segment_spans),
        )
        for value in drafts
    )


def _span_from_segment_v3(
    request: SceneComposerRequest,
    draft: RealizationSegmentRefV3,
    segment_spans: dict[str, tuple[int, int]],
) -> RealizationSpan:
    allowed_owners = {
        *request.selected_npc_ids,
        request.prepared_turn.request.protected_user_id,
    }
    if draft.owner_id not in allowed_owners:
        raise ContractValidationError(
            "realization segment names an unauthorized participant"
        )
    source_by_id = {
        value.source_unit_id: value for value in request.source_packet.units
    }
    decision = request.reasoner_outcome.decision
    assert decision is not None
    beat_by_id = {
        value.beat_id: value for value in decision.current_segment.ordered_beats
    }
    if draft.source_unit_id is not None and draft.source_unit_id not in source_by_id:
        raise ContractValidationError("realization segment names an unknown source unit")
    if draft.beat_id is not None and draft.beat_id not in beat_by_id:
        raise ContractValidationError("realization segment names an unknown current beat")
    realized_state = (
        source_by_id[draft.source_unit_id].required_state
        if draft.source_unit_id is not None
        else beat_by_id[draft.beat_id].state
    )
    start, end = segment_spans[draft.segment_key]
    return RealizationSpan(
        start=start,
        end=end,
        owner_id=draft.owner_id,
        kind=draft.kind,
        source_unit_id=draft.source_unit_id,
        beat_id=draft.beat_id,
        realized_state=realized_state,
    )


def _draft_has_structural_core_v3(
    request: SceneComposerRequest,
    response: DeepSeekCompositionDraftV3,
) -> bool:
    decision = request.reasoner_outcome.decision
    assert decision is not None
    realized_beats = {
        value.beat_id for value in response.realization_segments
        if value.beat_id is not None
    }
    realized_owners = {value.owner_id for value in response.realization_segments}
    expected_coverage = (
        tuple(value.source_unit_id for value in request.source_packet.units)
        if request.creator_event_coverage_required
        else ()
    )
    actual_coverage = tuple(
        value.source_unit_id for value in response.source_coverage_segments
    )
    return (
        realized_beats
        == {value.beat_id for value in decision.current_segment.ordered_beats}
        and set(request.selected_npc_ids).issubset(realized_owners)
        and actual_coverage == expected_coverage
        and response.terminal_segment_key == response.story_segments[-1].segment_key
    )


def _draft_has_structural_core_v4(
    request: SceneComposerRequest,
    response: DeepSeekCompositionDraftV4,
) -> bool:
    return not evaluate_draft_obligations(
        response,
        build_composer_output_obligations(request),
    )


def _build_submission(
    request: SceneComposerRequest,
    response: DeepSeekCompositionDraftV2,
) -> ComposerSubmission:
    candidate_id = deterministic_id(
        IdKind.COMPOSER_CANDIDATE,
        "cera.deepseek_composer.candidate.v1",
        f"{request.request_sha256}|{text_sha256(response.story_text)}",
    )
    candidate = ComposerCandidate(
        schema_version=ComposerCandidate.SCHEMA_VERSION,
        candidate_id=candidate_id,
        story_text=response.story_text,
        story_text_sha256=text_sha256(response.story_text),
        complete_core=_draft_has_structural_core(request, response),
        is_outline=_looks_like_outline(response.story_text),
        is_partial_draft=_looks_like_partial_draft(response.story_text),
        awaits_detailer=_awaits_detailer(response.story_text),
        contains_internal_labels=_contains_internal_labels(response.story_text),
        contains_ui_markup=_contains_ui_markup(response.story_text),
        contains_provider_diagnostics=_contains_provider_diagnostics(
            response.story_text
        ),
    )
    coverage = (
        tuple(
            _coverage_from_anchor_v2(request, response.story_text, value, ordinal)
            for ordinal, value in enumerate(response.source_coverage_anchors)
        )
        if request.creator_event_coverage_required
        else ()
    )
    spans = tuple(
        _span_from_anchor_v2(request, response.story_text, value)
        for value in response.realization_anchors
    )
    decision = request.reasoner_outcome.decision
    assert decision is not None
    span_by_owner = {
        owner_id: tuple(value for value in spans if value.owner_id == owner_id)
        for owner_id in request.selected_npc_ids
    }
    participant_realizations = tuple(
        CharacterRealization(
            character_id=owner_id,
            speaks=any(
                value.kind in {RealizationKind.DIALOGUE, RealizationKind.VOCALIZATION}
                for value in span_by_owner[owner_id]
            ),
            visibly_acts=(
                any(
                    value.kind
                    not in {RealizationKind.DIALOGUE, RealizationKind.VOCALIZATION}
                    for value in span_by_owner[owner_id]
                )
                or not any(
                    value.kind
                    in {RealizationKind.DIALOGUE, RealizationKind.VOCALIZATION}
                    for value in span_by_owner[owner_id]
                )
            ),
        )
        for owner_id in request.selected_npc_ids
        if span_by_owner[owner_id]
    )
    anchored_beat_ids = {
        value.beat_id for value in spans if value.beat_id is not None
    }
    realized_beat_ids = tuple(
        value.beat_id
        for value in decision.current_segment.ordered_beats
        if value.beat_id in anchored_beat_ids
    )
    _, terminal_end = _resolve_anchor(
        response.story_text,
        response.terminal_boundary_anchor,
    )
    trailing_end = len(response.story_text.rstrip())
    manifest_id = deterministic_id(
        IdKind.REALIZATION_MANIFEST,
        "cera.deepseek_composer.manifest.v1",
        f"{request.request_sha256}|{candidate.candidate_sha256}",
    )
    manifest = RealizationManifest(
        schema_version=RealizationManifest.SCHEMA_VERSION,
        manifest_id=manifest_id,
        candidate_sha256=candidate.candidate_sha256,
        decision_sha256=request.decision_sha256,
        sequence_plan_sha256=request.sequence_plan_sha256,
        floor_owner_id=decision.floor_owner_id,
        move_realizations=tuple(
            CharacterMoveRealization(
                character_id=value.character_id,
                selected_intent_sha256=text_sha256(value.selected_intent),
                action_direction_sha256=text_sha256(value.action_direction),
            )
            for value in decision.character_moves
        ),
        participant_realizations=participant_realizations,
        realized_beat_ids=realized_beat_ids,
        source_unit_coverage=coverage,
        character_spans=spans,
        semantic_inferences=(),
        terminal_state_preserved=(
            realized_beat_ids
            == tuple(
                value.beat_id for value in decision.current_segment.ordered_beats
            )
        ),
        stops_before_protected_user_choice=terminal_end == trailing_end,
        introduced_major_objective_or_participant=False,
    )
    return ComposerSubmission(candidate, manifest, ())


def _bind_python_owned_move_hashes(
    request: SceneComposerRequest,
    provider_payload: dict[str, object],
) -> dict[str, object]:
    """Bind redundant move hashes to the already validated decision text."""

    payload = dict(provider_payload)
    declared = payload.get("move_realizations")
    if not isinstance(declared, list):
        raise ContractValidationError("DeepSeek move realizations must be an array")
    decision = request.reasoner_outcome.decision
    if decision is None:
        raise ContractValidationError("DeepSeek move realizations require a decision")
    expected = {
        str(move.character_id): (
            text_sha256(move.selected_intent),
            text_sha256(move.action_direction),
        )
        for move in decision.character_moves
    }
    normalized: list[object] = []
    for item in declared:
        if not isinstance(item, dict):
            raise ContractValidationError("DeepSeek move realization must be an object")
        entry = dict(item)
        character_id = entry.get("character_id")
        hashes = expected.get(character_id)
        if hashes is None:
            raise ContractValidationError("DeepSeek move realization names an unselected character")
        for field_name, expected_hash in zip(
            ("selected_intent_sha256", "action_direction_sha256"),
            hashes,
            strict=True,
        ):
            supplied = entry.get(field_name)
            if supplied not in {DEEPSEEK_BINDING_PLACEHOLDER, expected_hash}:
                raise ContractValidationError(
                    f"DeepSeek changed Python-owned move binding {field_name}"
                )
            entry[field_name] = expected_hash
        normalized.append(entry)
    payload["move_realizations"] = normalized
    return payload


def _coverage_from_draft(story_text: str, draft: SourceCoverageDraft) -> SourceUnitCoverage:
    start, end = _resolve_anchor(story_text, draft.anchor)
    return SourceUnitCoverage(
        source_unit_id=draft.source_unit_id,
        ordinal=draft.ordinal,
        start=start,
        end=end,
        preserved_state=draft.preserved_state,
    )


def _span_from_draft(story_text: str, draft: CharacterSpanDraft) -> RealizationSpan:
    start, end = _resolve_anchor(story_text, draft.anchor)
    return RealizationSpan(
        start=start,
        end=end,
        owner_id=draft.owner_id,
        kind=draft.kind,
        source_unit_id=draft.source_unit_id,
        beat_id=draft.beat_id,
        realized_state=draft.realized_state,
    )


def _resolve_anchor(story_text: str, anchor: QuoteAnchor) -> tuple[int, int]:
    start = story_text.find(anchor.quote)
    if start < 0:
        raise ContractValidationError("Composer quote anchor does not occur in story text")
    if story_text.find(anchor.quote, start + 1) >= 0:
        raise ContractValidationError(
            "Composer quote anchor is ambiguous; provider must supply a unique quote"
        )
    return start, start + len(anchor.quote)


def _coverage_from_anchor_v2(
    request: SceneComposerRequest,
    story_text: str,
    draft: SourceCoverageAnchorV2,
    ordinal: int,
) -> SourceUnitCoverage:
    units = request.source_packet.units
    if ordinal >= len(units) or draft.source_unit_id != units[ordinal].source_unit_id:
        raise ContractValidationError(
            "source coverage anchors must follow exact source-unit order"
        )
    required_state = units[ordinal].required_state
    if required_state is None:
        raise ContractValidationError(
            "creator source coverage requires a declared progression state"
        )
    start, end = _resolve_anchor(story_text, draft.anchor)
    return SourceUnitCoverage(
        source_unit_id=draft.source_unit_id,
        ordinal=ordinal,
        start=start,
        end=end,
        preserved_state=required_state,
    )


def _span_from_anchor_v2(
    request: SceneComposerRequest,
    story_text: str,
    draft: RealizationAnchorV2,
) -> RealizationSpan:
    allowed_owners = {
        *request.selected_npc_ids,
        request.prepared_turn.request.protected_user_id,
    }
    if draft.owner_id not in allowed_owners:
        raise ContractValidationError(
            "realization anchor names an unauthorized participant"
        )
    source_by_id = {
        value.source_unit_id: value for value in request.source_packet.units
    }
    decision = request.reasoner_outcome.decision
    assert decision is not None
    beat_by_id = {
        value.beat_id: value for value in decision.current_segment.ordered_beats
    }
    if (
        draft.source_unit_id is not None
        and draft.source_unit_id not in source_by_id
    ):
        raise ContractValidationError(
            "realization anchor names an unknown source unit"
        )
    if draft.beat_id is not None and draft.beat_id not in beat_by_id:
        raise ContractValidationError(
            "realization anchor names an unknown current beat"
        )
    realized_state = None
    if draft.source_unit_id is not None:
        realized_state = source_by_id[draft.source_unit_id].required_state
    elif draft.beat_id is not None:
        realized_state = beat_by_id[draft.beat_id].state
    start, end = _resolve_anchor(story_text, draft.anchor)
    return RealizationSpan(
        start=start,
        end=end,
        owner_id=draft.owner_id,
        kind=draft.kind,
        source_unit_id=draft.source_unit_id,
        beat_id=draft.beat_id,
        realized_state=realized_state,
    )


def _draft_has_structural_core(
    request: SceneComposerRequest,
    response: DeepSeekCompositionDraftV2,
) -> bool:
    decision = request.reasoner_outcome.decision
    assert decision is not None
    anchored_beats = {
        value.beat_id
        for value in response.realization_anchors
        if value.beat_id is not None
    }
    anchored_owners = {value.owner_id for value in response.realization_anchors}
    expected_coverage = (
        tuple(value.source_unit_id for value in request.source_packet.units)
        if request.creator_event_coverage_required
        else ()
    )
    actual_coverage = tuple(
        value.source_unit_id for value in response.source_coverage_anchors
    )
    try:
        _, terminal_end = _resolve_anchor(
            response.story_text,
            response.terminal_boundary_anchor,
        )
    except ContractValidationError:
        return False
    return (
        anchored_beats
        == {
            value.beat_id
            for value in decision.current_segment.ordered_beats
        }
        and set(request.selected_npc_ids).issubset(anchored_owners)
        and actual_coverage == expected_coverage
        and terminal_end == len(response.story_text.rstrip())
    )


_OUTLINE_LINE = re.compile(r"(?im)^\s*(?:[-*]\s+|(?:beat|scene)\s+\d+\s*:)")
_PARTIAL_MARKER = re.compile(r"(?i)\b(?:to be continued|continue from here|draft)\b")
_DETAILER_MARKER = re.compile(r"(?i)\b(?:detailer|expand this|add details later)\b")
_INTERNAL_MARKER = re.compile(
    r"(?im)^\s*(?:\[?(?:plan|reasoning|diagnostics?|manifest|beat\s*id)\]?\s*:|#+\s*(?:plan|reasoning|diagnostics?|manifest)\b)"
)
_HTML_MARKER = re.compile(r"<\/?[A-Za-z][^>]*>")
_PROVIDER_MARKER = re.compile(
    r"(?i)\b(?:provider response|token count|model output|json schema)\b"
)


def _looks_like_outline(text: str) -> bool:
    return bool(_OUTLINE_LINE.search(text))


def _looks_like_partial_draft(text: str) -> bool:
    return bool(_PARTIAL_MARKER.search(text))


def _awaits_detailer(text: str) -> bool:
    return bool(_DETAILER_MARKER.search(text))


def _contains_internal_labels(text: str) -> bool:
    return bool(_INTERNAL_MARKER.search(text))


def _contains_ui_markup(text: str) -> bool:
    return bool(_HTML_MARKER.search(text))


def _contains_provider_diagnostics(text: str) -> bool:
    return bool(_PROVIDER_MARKER.search(text))


def deepseek_composition_draft_v2_json_schema() -> dict[str, object]:
    """Closed schema for creative prose plus resolvable evidence anchors."""

    nullable_source = {
        "anyOf": [_id_schema(IdKind.SOURCE_UNIT), {"type": "null"}]
    }
    nullable_beat = {
        "anyOf": [_id_schema(IdKind.BEAT), {"type": "null"}]
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "schema_version": {
                "type": "string",
                "const": DeepSeekCompositionDraftV2.SCHEMA_VERSION,
            },
            "story_text": {"type": "string", "minLength": 1},
            "source_coverage_anchors": _array_schema(
                _closed_object(
                    {
                        "source_unit_id": _id_schema(IdKind.SOURCE_UNIT),
                        "anchor": _anchor_schema(),
                    }
                ),
                32,
            ),
            "realization_anchors": {
                "type": "array",
                "minItems": 1,
                "maxItems": 128,
                "items": _closed_object(
                    {
                        "owner_id": _id_schema(IdKind.CHARACTER),
                        "kind": {
                            "type": "string",
                            "enum": [
                                value.value for value in RealizationKind
                            ],
                        },
                        "anchor": _anchor_schema(),
                        "source_unit_id": nullable_source,
                        "beat_id": nullable_beat,
                    }
                ),
            },
            "terminal_boundary_anchor": _anchor_schema(),
        },
        "required": [
            "schema_version",
            "story_text",
            "source_coverage_anchors",
            "realization_anchors",
            "terminal_boundary_anchor",
        ],
    }


def deepseek_composition_draft_v3_json_schema() -> dict[str, object]:
    """Closed provider schema for ordered, Python-anchored prose segments."""

    local_key = {
        "type": "string",
        "pattern": "^[A-Za-z][A-Za-z0-9_-]{0,63}$",
    }
    nullable_source = {
        "anyOf": [_id_schema(IdKind.SOURCE_UNIT), {"type": "null"}]
    }
    nullable_beat = {
        "anyOf": [_id_schema(IdKind.BEAT), {"type": "null"}]
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "schema_version": {
                "type": "string",
                "const": DeepSeekCompositionDraftV3.SCHEMA_VERSION,
            },
            "story_segments": {
                "type": "array",
                "minItems": 1,
                "maxItems": 128,
                "items": _closed_object(
                    {
                        "segment_key": local_key,
                        "text": {"type": "string", "minLength": 1},
                    }
                ),
            },
            "source_coverage_segments": _array_schema(
                _closed_object(
                    {
                        "source_unit_id": _id_schema(IdKind.SOURCE_UNIT),
                        "segment_key": local_key,
                    }
                ),
                32,
            ),
            "realization_segments": {
                "type": "array",
                "minItems": 1,
                "maxItems": 128,
                "items": _closed_object(
                    {
                        "owner_id": _id_schema(IdKind.CHARACTER),
                        "kind": {
                            "type": "string",
                            "enum": [value.value for value in RealizationKind],
                        },
                        "segment_key": local_key,
                        "source_unit_id": nullable_source,
                        "beat_id": nullable_beat,
                    }
                ),
            },
            "terminal_segment_key": local_key,
        },
        "required": [
            "schema_version",
            "story_segments",
            "source_coverage_segments",
            "realization_segments",
            "terminal_segment_key",
        ],
    }


def deepseek_composition_draft_v4_json_schema() -> dict[str, object]:
    """Active schema: creative prose plus Python-owned obligation references."""

    local_key = {
        "type": "string",
        "pattern": "^[A-Za-z][A-Za-z0-9_-]{0,63}$",
    }
    obligation_key = {
        "type": "string",
        "pattern": "^spec_[0-9a-f]{24}$",
    }
    segment_keys = {
        "type": "array",
        "minItems": 1,
        "maxItems": 32,
        "items": local_key,
        "description": "Ordered distinct segment keys; do not repeat a key.",
    }
    nullable_source = {
        "anyOf": [_id_schema(IdKind.SOURCE_UNIT), {"type": "null"}]
    }
    nullable_beat = {
        "anyOf": [_id_schema(IdKind.BEAT), {"type": "null"}]
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "schema_version": {
                "type": "string",
                "const": DeepSeekCompositionDraftV4.SCHEMA_VERSION,
            },
            "story_segments": {
                "type": "array",
                "minItems": 1,
                "maxItems": 128,
                "items": _closed_object(
                    {
                        "segment_key": local_key,
                        "text": {"type": "string", "minLength": 1},
                    }
                ),
            },
            "source_coverage": _array_schema(
                _closed_object(
                    {
                        "source_unit_id": _id_schema(IdKind.SOURCE_UNIT),
                        "segment_keys": segment_keys,
                    }
                ),
                32,
            ),
            "realization_segments": {
                "type": "array",
                "minItems": 1,
                "maxItems": 128,
                "items": _closed_object(
                    {
                        "owner_id": _id_schema(IdKind.CHARACTER),
                        "kind": {
                            "type": "string",
                            "enum": [value.value for value in RealizationKind],
                            "description": (
                                "General story function only. Adult channels such as "
                                "physiology and narration belong in specificity_coverage."
                            ),
                        },
                        "segment_key": local_key,
                        "source_unit_id": nullable_source,
                        "beat_id": nullable_beat,
                    }
                ),
            },
            "specificity_coverage": _array_schema(
                _closed_object(
                    {
                        "obligation_key": obligation_key,
                        "segment_keys": segment_keys,
                    }
                ),
                128,
            ),
            "terminal_segment_key": local_key,
        },
        "required": [
            "schema_version",
            "story_segments",
            "source_coverage",
            "realization_segments",
            "specificity_coverage",
            "terminal_segment_key",
        ],
    }


def deepseek_composition_draft_v5_json_schema() -> dict[str, object]:
    """Active Composer schema with one required typed realization authority."""

    local_key = {
        "type": "string",
        "pattern": "^[A-Za-z][A-Za-z0-9_-]{0,63}$",
    }
    segment_keys = {
        "type": "array",
        "minItems": 1,
        "maxItems": 128,
        "items": local_key,
        "description": "Ordered distinct segment keys; do not repeat a key.",
    }
    obligation_key = {
        "type": "string",
        "pattern": "^[A-Za-z][A-Za-z0-9_.:-]{0,127}$",
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "schema_version": {
                "type": "string",
                "const": DeepSeekCompositionDraftV5.SCHEMA_VERSION,
            },
            "story_segments": {
                "type": "array",
                "minItems": 1,
                "maxItems": 128,
                "items": _closed_object(
                    {
                        "segment_key": local_key,
                        "text": {"type": "string", "minLength": 1},
                    }
                ),
            },
            "source_coverage": _array_schema(
                _closed_object(
                    {
                        "source_unit_id": _id_schema(IdKind.SOURCE_UNIT),
                        "segment_keys": segment_keys,
                    }
                ),
                32,
            ),
            "realization_segments": {
                "type": "array",
                "minItems": 1,
                "maxItems": 128,
                "items": _closed_object(
                    {
                        "owner_id": _id_schema(IdKind.CHARACTER),
                        "kind": {
                            "type": "string",
                            "enum": [value.value for value in RealizationKind],
                            "description": (
                                "General story function only; adult channels belong "
                                "in specificity_coverage."
                            ),
                        },
                        "segment_key": local_key,
                        "authority_id": _id_schema(
                            IdKind.SOURCE_UNIT,
                            IdKind.BEAT,
                        ),
                    }
                ),
            },
            "specificity_coverage": _array_schema(
                _closed_object(
                    {
                        "obligation_key": obligation_key,
                        "segment_keys": segment_keys,
                    }
                ),
                128,
            ),
            "terminal_segment_key": local_key,
        },
        "required": [
            "schema_version",
            "story_segments",
            "source_coverage",
            "realization_segments",
            "specificity_coverage",
            "terminal_segment_key",
        ],
    }


def deepseek_composition_draft_v6_json_schema() -> dict[str, object]:
    """Active Composer schema; realization owners are no longer model-authored."""

    schema = deepseek_composition_draft_v5_json_schema()
    schema["properties"]["schema_version"][
        "const"
    ] = DeepSeekCompositionDraftV6.SCHEMA_VERSION
    realization_item = schema["properties"]["realization_segments"]["items"]
    realization_item["properties"].pop("owner_id")
    realization_item["required"].remove("owner_id")
    return schema


def deepseek_composer_response_json_schema() -> dict[str, object]:
    """Compatibility name for the active minimal composition draft schema."""

    return deepseek_composition_draft_v6_json_schema()


def legacy_deepseek_composer_response_json_schema() -> dict[str, object]:
    """Historical provider-owned manifest schema retained for fixture migration."""

    bool_fields = (
        "complete_core",
        "is_outline",
        "is_partial_draft",
        "awaits_detailer",
        "contains_internal_labels",
        "contains_ui_markup",
        "contains_provider_diagnostics",
        "terminal_state_preserved",
        "stops_before_protected_user_choice",
        "introduced_major_objective_or_participant",
    )
    properties: dict[str, object] = {
        "schema_version": {"const": DEEPSEEK_COMPOSER_RESPONSE_VERSION},
        "story_text": {"type": "string", "minLength": 1},
        "floor_owner_id": _id_schema(IdKind.CHARACTER),
        "move_realizations": _array_schema(
            _closed_object(
                {
                    "character_id": _id_schema(IdKind.CHARACTER),
                    "selected_intent_sha256": {"const": DEEPSEEK_BINDING_PLACEHOLDER},
                    "action_direction_sha256": {"const": DEEPSEEK_BINDING_PLACEHOLDER},
                }
            ),
            8,
        ),
        "participant_realizations": _array_schema(
            _closed_object(
                {
                    "character_id": _id_schema(IdKind.CHARACTER),
                    "speaks": {"type": "boolean"},
                    "visibly_acts": {"type": "boolean"},
                }
            ),
            8,
        ),
        "realized_beat_ids": _array_schema(_id_schema(IdKind.BEAT), 32),
        "source_unit_coverage": _array_schema(_coverage_schema(), 32),
        "character_spans": _array_schema(_span_schema(), 128),
        "semantic_inferences": _array_schema(
            _closed_object(
                {
                    "source": {"enum": [value.value for value in SemanticCategory]},
                    "target": {"enum": [value.value for value in SemanticCategory]},
                }
            ),
            32,
        ),
        "advisory_notes": _array_schema(
            _closed_object(
                {
                    "kind": {"type": "string", "minLength": 1, "maxLength": 100},
                    "summary": {"type": "string", "minLength": 1, "maxLength": 500},
                }
            ),
            16,
        ),
    }
    properties.update({name: {"type": "boolean"} for name in bool_fields})
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
        "required": list(properties),
    }


def _anchor_schema() -> dict[str, object]:
    return _closed_object(
        {
            "quote": {"type": "string", "minLength": 1},
            "occurrence": {
                "type": "integer",
                "const": 0,
                "description": (
                    "Compatibility sentinel only. The quote must occur exactly once; "
                    "Python derives occurrence and offsets."
                ),
            },
        }
    )


def _coverage_schema() -> dict[str, object]:
    return _closed_object(
        {
            "source_unit_id": _id_schema(IdKind.SOURCE_UNIT),
            "ordinal": {"type": "integer", "minimum": 0},
            "anchor": _anchor_schema(),
            "preserved_state": {"enum": [value.value for value in BeatState]},
        }
    )


def _span_schema() -> dict[str, object]:
    nullable_source = {"anyOf": [_id_schema(IdKind.SOURCE_UNIT), {"type": "null"}]}
    nullable_beat = {"anyOf": [_id_schema(IdKind.BEAT), {"type": "null"}]}
    nullable_state = {
        "anyOf": [{"enum": [value.value for value in BeatState]}, {"type": "null"}]
    }
    return _closed_object(
        {
            "owner_id": _id_schema(IdKind.CHARACTER),
            "kind": {"enum": [value.value for value in RealizationKind]},
            "anchor": _anchor_schema(),
            "source_unit_id": nullable_source,
            "beat_id": nullable_beat,
            "realized_state": nullable_state,
        }
    )


def _closed_object(properties: dict[str, object]) -> dict[str, object]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
        "required": list(properties),
    }


def _array_schema(items: dict[str, object], maximum: int) -> dict[str, object]:
    return {"type": "array", "maxItems": maximum, "items": items}


def _id_schema(*kinds: IdKind) -> dict[str, object]:
    alternatives = "|".join(value.value for value in kinds)
    return {
        "type": "string",
        "pattern": rf"^(?:{alternatives}):[A-Za-z0-9][A-Za-z0-9._-]{{0,127}}$",
    }


def _sha_schema() -> dict[str, object]:
    return {"type": "string", "pattern": "^[0-9a-f]{64}$"}


def _unique(values, field_name: str) -> None:
    items = list(values)
    if len(items) != len(set(items)):
        raise ContractValidationError(f"{field_name} must not contain duplicates")
