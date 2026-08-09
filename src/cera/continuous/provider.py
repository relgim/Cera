"""Provider DTOs and one-shot adapters for the shadow continuous route."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, fields, is_dataclass, replace
from enum import Enum
import json
import re
from types import UnionType
from typing import Any, Callable, ClassVar, Union, get_args, get_origin, get_type_hints

from cera.creator_review.models import (
    CreatorReviewAssessment,
    CreatorReviewSeverity,
    PublicationEligibility,
    ReviewIssueOwner,
)
from cera.errors import ContractValidationError
from cera.provider_dispatch_guard import (
    assert_provider_dispatch_allowed,
    is_external_provider_boundary,
)
from cera.providers import (
    CodexSDKTransport,
    DeepSeekChatTransport,
    DeepSeekMessage,
    ProviderOutputMode,
    codex_realization_verifier_candidate,
    codex_reasoner_candidate,
    deepseek_composer_candidate,
)
from cera.schema import from_mapping
from cera.serialization import (
    canonical_bytes,
    canonical_sha256,
    re_is_sha256,
    text_sha256,
    to_primitive,
)

from .contracts import (
    ACTIVE_VALIDATOR_WRITER_HARD_CLASSES,
    AcceptedTurnPairV1,
    CharacterRoleLedgerV1,
    CompactProtectedUserImplication,
    CompactRejectedViolationReceiptV1,
    COMPACT_WRITER_BRIEF_MAX_BEATS,
    CreatedFieldLogEntryV1,
    DiagnosticGroundingStatus,
    DiagnosticProtectedSemanticAdjudicationV1,
    DiagnosticStorySegmentV1,
    DiagnosticViolationClassification,
    EventRecordCandidateV1,
    EventItemRoleLedgerV1,
    FinalFieldName,
    FinalFieldScopeV1,
    FinalInformationVisibility,
    FinalSequenceItemV1,
    FinalSequenceV1,
    LOCAL_KEY_JSON_PATTERN,
    ProtectedSemanticAdjudicationV1,
    ProtectedSemanticRelationKind,
    ProtectedUserAllowanceMode,
    ProtectedUserRealizationSpanV1,
    ProtectedUserSourceClaimKind,
    PresentationRealizationClass,
    PresentationRealizationSegmentV1,
    ProhibitedWriterDetailClass,
    RealizationAuthorityDisposition,
    ReaderIssueReferenceV1,
    ReaderVerdictStatus,
    ReaderVerdictV1,
    ReaderVerdictV2,
    RichPlannerSequenceV1,
    SceneSummaryV1,
    SourceGroundedPublicStateReceiptV1,
    StoryRealizationKind,
    StoryRealizationSegmentV1,
    ValidatorFinalizationPackageV1,
    ValidatorSemanticStatus,
    ValidatorTaskMode,
    WriterCandidateDisposition,
    WriterRecallDirectiveV1,
    WriterRecallOffendingSpanV1,
    WorldEditOperationKind,
    WorldEditOperationV1,
    json_value_type,
)
from .call_ledger import ContinuousProviderCallLedger
from .operation_evidence import ProviderOperationEvidenceStoreV1
from .record_policy import PERSISTENCE_POLICY_SHA256
from .prompting import (
    CONTINUOUS_COMPACT_VALIDATOR_PROMPT_VERSION,
    CONTINUOUS_COMPACT_VALIDATOR_PROMPT_VERSION_V2,
    CONTINUOUS_COMPACT_VALIDATOR_PROMPT_VERSION_V3,
    CONTINUOUS_COMPACT_VALIDATOR_PROMPT_VERSION_V4,
    CONTINUOUS_PLANNER_PROMPT_VERSION,
    CONTINUOUS_READER_PROMPT_VERSION,
    CONTINUOUS_VALIDATOR_PROMPT_VERSION,
)


CONTINUOUS_PLANNER_ADAPTER_VERSION = "cera.continuous_planner_adapter.v14"
CONTINUOUS_VALIDATOR_ADAPTER_VERSION = "cera.continuous_validator_adapter.v30"
CONTINUOUS_COMPACT_VALIDATOR_ADAPTER_VERSION = (
    "cera.continuous_compact_validator_adapter.v1"
)
CONTINUOUS_COMPACT_VALIDATOR_ADAPTER_VERSION_V2 = (
    "cera.continuous_compact_validator_adapter.v2"
)
CONTINUOUS_COMPACT_VALIDATOR_ADAPTER_VERSION_V3 = (
    "cera.continuous_compact_validator_adapter.v3"
)
CONTINUOUS_COMPACT_VALIDATOR_ADAPTER_VERSION_V4 = (
    "cera.continuous_compact_validator_adapter.v4"
)
CONTINUOUS_DEEPSEEK_ADAPTER_VERSION = "cera.continuous_deepseek_adapter.v10"
CONTINUOUS_DEEPSEEK_PROMPT_VERSION = "cera.scene_writer_prompt.v6"
CONTINUOUS_READER_ADAPTER_VERSION = "cera.continuous_reader_adapter.v4"


@dataclass(frozen=True, slots=True)
class ProviderWorldEditOperationV1:
    operation_key: str
    target_file: str
    expected_file_revision: int | None
    operation: WorldEditOperationKind
    field_path: str
    value_json: str
    reason: str
    source_final_sequence_item: str
    source_final_field_name: str
    protected_user_source_claim_keys: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ProviderCreatedFieldLogEntryV1:
    target_file: str
    field_path: str
    value_type: str
    value_json: str
    reason: str
    source_final_sequence_item: str
    source_final_field_name: str
    protected_user_source_claim_keys: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ProviderSceneSummaryDraftV1:
    summary_id: str
    completed_scene_id: str
    accepted_turn_ids: tuple[str, ...]
    shortest_complete_summary: str
    ending_state: str
    transition_context: str


@dataclass(frozen=True, slots=True)
class ProviderEventRecordDraftV1:
    """Provider-facing event meaning; Python derives role bookkeeping."""

    event_id: str
    accepted_turn_id: str
    scene_id: str
    summary: str
    final_sequence_item_keys: tuple[str, ...]
    protected_user_source_claim_keys: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ProviderEventRecordDraftV2:
    """Active event provenance only; Python derives event meaning text."""

    event_id: str
    accepted_turn_id: str
    scene_id: str
    final_sequence_item_keys: tuple[str, ...]
    protected_user_source_claim_keys: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ContinuousValidatorDraftV1:
    SCHEMA_VERSION: ClassVar[str] = "cera.continuous_validator_draft.v8"

    schema_version: str
    package_id: str
    world_id: str
    branch_id: str
    task_mode: ValidatorTaskMode
    semantic_status: ValidatorSemanticStatus
    complete_final_sequence: FinalSequenceV1 | None
    creator_review: CreatorReviewAssessment | None
    protected_semantic_adjudications: tuple[
        ProtectedSemanticAdjudicationV1, ...
    ]
    event_record: ProviderEventRecordDraftV1 | None
    optional_scene_summary: ProviderSceneSummaryDraftV1 | None

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("continuous Validator provider schema changed")

    def compile(
        self, *, accepted_pairs: tuple[AcceptedTurnPairV1, ...] = ()
    ) -> ValidatorFinalizationPackageV1:
        operations, created = _derive_persistence_operations(
            self.complete_final_sequence
        )
        scene_summary = None
        if self.optional_scene_summary is not None:
            draft = self.optional_scene_summary
            expected_ids = tuple(value.accepted_turn_id for value in accepted_pairs)
            if draft.accepted_turn_ids != expected_ids:
                raise ContractValidationError(
                    "Validator scene summary changed the Python accepted-turn allow-list"
                )
            scene_summary = SceneSummaryV1(
                schema_version=SceneSummaryV1.SCHEMA_VERSION,
                summary_id=draft.summary_id,
                completed_scene_id=draft.completed_scene_id,
                accepted_turn_ids=draft.accepted_turn_ids,
                shortest_complete_summary=draft.shortest_complete_summary,
                last_five_exact_pairs=accepted_pairs[-5:],
                ending_state=draft.ending_state,
                transition_context=draft.transition_context,
            )
        elif accepted_pairs:
            raise ContractValidationError(
                "Validator omitted the requested scene summary"
            )
        event_record = None
        if self.event_record is not None:
            if self.complete_final_sequence is None:
                raise ContractValidationError(
                    "Validator event cannot exist without a final sequence"
                )
            items = {
                value.item_key: value
                for value in self.complete_final_sequence.items
            }
            if tuple(self.event_record.final_sequence_item_keys) != tuple(items):
                raise ContractValidationError(
                    "Validator event changed final sequence item order"
                )
            item_roles = tuple(
                EventItemRoleLedgerV1(
                    schema_version=EventItemRoleLedgerV1.SCHEMA_VERSION,
                    final_sequence_item_key=key,
                    roles=items[key].roles,
                )
                for key in self.event_record.final_sequence_item_keys
            )
            participant_ids = tuple(
                dict.fromkeys(
                    identity
                    for value in item_roles
                    for identity in value.roles.involved_ids
                )
            )
            event_record = EventRecordCandidateV1(
                event_id=self.event_record.event_id,
                accepted_turn_id=self.event_record.accepted_turn_id,
                scene_id=self.event_record.scene_id,
                participant_ids=participant_ids,
                item_role_ledgers=item_roles,
                summary=self.event_record.summary,
                final_sequence_item_keys=self.event_record.final_sequence_item_keys,
                protected_user_source_claim_keys=(
                    self.event_record.protected_user_source_claim_keys
                ),
            )
        return ValidatorFinalizationPackageV1(
            schema_version=ValidatorFinalizationPackageV1.SCHEMA_VERSION,
            package_id=self.package_id,
            world_id=self.world_id,
            branch_id=self.branch_id,
            task_mode=self.task_mode,
            semantic_status=self.semantic_status,
            complete_final_sequence=self.complete_final_sequence,
            creator_review=self.creator_review,
            world_edit_operations=tuple(operations),
            created_field_log=tuple(created),
            event_record=event_record,
            optional_scene_summary=scene_summary,
            protected_semantic_adjudications=self.protected_semantic_adjudications,
        )


def _derive_persistence_operations(
    sequence: FinalSequenceV1 | None,
) -> tuple[tuple[WorldEditOperationV1, ...], tuple[CreatedFieldLogEntryV1, ...]]:
    """Compile model-selected destinations into exact Python-owned bookkeeping."""

    if sequence is None:
        return (), ()
    operations: list[WorldEditOperationV1] = []
    created: list[CreatedFieldLogEntryV1] = []
    for item in sequence.items:
        for scope in item.field_scopes:
            raw_values = getattr(item, scope.field_name)
            source_values = raw_values if isinstance(raw_values, tuple) else (raw_values,)
            for directive in scope.persistence_directives:
                if directive.source_value_index >= len(source_values):
                    raise ContractValidationError(
                        "persistence directive selected a missing final-field value"
                    )
                value = source_values[directive.source_value_index]
                reason = f"Persist accepted final field {scope.field_name}."
                operation = WorldEditOperationV1(
                    operation_key=directive.directive_key,
                    target_file=directive.target_file,
                    expected_file_revision=directive.expected_file_revision,
                    operation=directive.operation,
                    field_path=directive.field_path,
                    value=value,
                    reason=reason,
                    source_final_sequence_item=item.item_key,
                    source_final_field_name=scope.field_name,
                    protected_user_source_claim_keys=(
                        scope.protected_user_source_claim_keys
                    ),
                    persistence_directive_key=directive.directive_key,
                )
                operations.append(operation)
                if directive.operation is WorldEditOperationKind.ADD:
                    created.append(
                        CreatedFieldLogEntryV1(
                            target_file=directive.target_file,
                            field_path=directive.field_path,
                            value_type=json_value_type(value),
                            value=value,
                            reason=reason,
                            source_final_sequence_item=item.item_key,
                            source_final_field_name=scope.field_name,
                            protected_user_source_claim_keys=(
                                scope.protected_user_source_claim_keys
                            ),
                            persistence_directive_key=directive.directive_key,
                        )
                    )
    return tuple(operations), tuple(created)


@dataclass(frozen=True, slots=True)
class ContinuousSemanticValidatorResultV1:
    """Validator-owned semantics plus the existing Python-compiled package."""

    semantic_status: ValidatorSemanticStatus
    reason_codes: tuple[str, ...]
    story_segments: tuple[StoryRealizationSegmentV1, ...]
    protected_semantic_adjudications: tuple[
        ProtectedSemanticAdjudicationV1, ...
    ]
    finalization_package: ValidatorFinalizationPackageV1 | None

    def __post_init__(self) -> None:
        if (
            self.finalization_package is not None
            and self.finalization_package.task_mode is ValidatorTaskMode.FINALIZE_TURN
            and not self.story_segments
        ):
            raise ContractValidationError(
                "continuous Semantic Validator omitted exact story spans"
            )
        keys = tuple(value.segment_key for value in self.story_segments)
        if len(keys) != len(set(keys)):
            raise ContractValidationError(
                "continuous Semantic Validator duplicated story span keys"
            )
        if self.semantic_status is ValidatorSemanticStatus.ACCEPTED:
            if self.reason_codes or self.finalization_package is None:
                raise ContractValidationError(
                    "accepted Semantic Validator result is incomplete"
                )
        elif self.semantic_status is ValidatorSemanticStatus.CONCERN:
            if not self.reason_codes or self.finalization_package is None:
                raise ContractValidationError(
                    "concerning Semantic Validator result is incomplete"
                )
        elif not self.reason_codes or self.finalization_package is not None:
            raise ContractValidationError(
                "non-accepted Semantic Validator result must stop before finalization"
            )
        if (
            self.finalization_package is not None
            and self.finalization_package.semantic_status is not self.semantic_status
        ):
            raise ContractValidationError(
                "Semantic Validator result changed finalization status"
            )

    @classmethod
    def from_finalization_package(
        cls,
        *,
        finalization_package: ValidatorFinalizationPackageV1,
        story_segments: tuple[StoryRealizationSegmentV1, ...],
    ) -> "ContinuousSemanticValidatorResultV1":
        return cls(
            semantic_status=finalization_package.semantic_status,
            reason_codes=(
                finalization_package.creator_review.reason_codes
                if finalization_package.semantic_status
                is ValidatorSemanticStatus.CONCERN
                and finalization_package.creator_review is not None
                else ()
            ),
            story_segments=story_segments,
            protected_semantic_adjudications=(
                finalization_package.protected_semantic_adjudications
            ),
            finalization_package=finalization_package,
        )


@dataclass(frozen=True, slots=True)
class ContinuousSemanticValidatorResultV2:
    """Branch-separated semantic result for Runtime Model V3.

    Canonical story authority appears only on accepted/concern turn results.
    Rejected, inconclusive, and error results carry a separate diagnostic
    provenance family that cannot satisfy finalization contracts.
    """

    semantic_status: ValidatorSemanticStatus
    reason_codes: tuple[str, ...]
    story_segments: tuple[StoryRealizationSegmentV1, ...]
    protected_semantic_adjudications: tuple[
        ProtectedSemanticAdjudicationV1, ...
    ]
    diagnostic_story_segments: tuple[DiagnosticStorySegmentV1, ...]
    diagnostic_protected_semantic_adjudications: tuple[
        DiagnosticProtectedSemanticAdjudicationV1, ...
    ]
    finalization_package: ValidatorFinalizationPackageV1 | None

    def __post_init__(self) -> None:
        if any(
            not isinstance(value, StoryRealizationSegmentV1)
            for value in self.story_segments
        ) or any(
            not isinstance(value, ProtectedSemanticAdjudicationV1)
            for value in self.protected_semantic_adjudications
        ):
            raise ContractValidationError(
                "accepted Validator evidence requires canonical segment types"
            )
        if any(
            not isinstance(value, DiagnosticStorySegmentV1)
            for value in self.diagnostic_story_segments
        ) or any(
            not isinstance(value, DiagnosticProtectedSemanticAdjudicationV1)
            for value in self.diagnostic_protected_semantic_adjudications
        ):
            raise ContractValidationError(
                "rejected Validator evidence requires diagnostic segment types"
            )
        canonical_present = bool(
            self.story_segments or self.protected_semantic_adjudications
        )
        diagnostic_present = bool(
            self.diagnostic_story_segments
            or self.diagnostic_protected_semantic_adjudications
        )
        if canonical_present and diagnostic_present:
            raise ContractValidationError(
                "canonical and diagnostic Validator evidence cannot mix"
            )
        if len({value.segment_key for value in self.story_segments}) != len(
            self.story_segments
        ):
            raise ContractValidationError(
                "continuous Semantic Validator duplicated canonical story span keys"
            )
        diagnostic_keys = tuple(
            value.segment_key for value in self.diagnostic_story_segments
        )
        if len(diagnostic_keys) != len(set(diagnostic_keys)):
            raise ContractValidationError(
                "continuous Semantic Validator duplicated diagnostic story span keys"
            )
        diagnostic_adjudication_keys = tuple(
            value.segment_key
            for value in self.diagnostic_protected_semantic_adjudications
        )
        if (
            len(diagnostic_adjudication_keys)
            != len(set(diagnostic_adjudication_keys))
            or set(diagnostic_adjudication_keys) != set(diagnostic_keys)
        ):
            raise ContractValidationError(
                "diagnostic adjudications must cover every diagnostic story span exactly once"
            )
        if self.finalization_package is not None:
            if diagnostic_present:
                raise ContractValidationError(
                    "diagnostic Validator evidence cannot enter finalization"
                )
            if self.finalization_package.task_mode is ValidatorTaskMode.SCENE_SUMMARY:
                if (
                    self.semantic_status is not ValidatorSemanticStatus.ACCEPTED
                    or canonical_present
                    or self.reason_codes
                ):
                    raise ContractValidationError(
                        "scene-summary result carried turn evidence or diagnostics"
                    )
            elif (
                self.semantic_status
                not in {
                    ValidatorSemanticStatus.ACCEPTED,
                    ValidatorSemanticStatus.CONCERN,
                }
                or not self.story_segments
                or not self.protected_semantic_adjudications
            ):
                raise ContractValidationError(
                    "finalizing turn requires strict canonical Validator evidence"
                )
            if self.finalization_package.semantic_status is not self.semantic_status:
                raise ContractValidationError(
                    "Semantic Validator result changed finalization status"
                )
        else:
            if (
                self.semantic_status
                in {
                    ValidatorSemanticStatus.ACCEPTED,
                    ValidatorSemanticStatus.CONCERN,
                }
                or not self.reason_codes
                or canonical_present
                or not self.diagnostic_story_segments
                or not self.diagnostic_protected_semantic_adjudications
            ):
                raise ContractValidationError(
                    "non-finalizing Validator result requires diagnostic-only evidence"
                )

    @classmethod
    def from_v1(
        cls, value: ContinuousSemanticValidatorResultV1
    ) -> "ContinuousSemanticValidatorResultV2":
        return cls(
            semantic_status=value.semantic_status,
            reason_codes=value.reason_codes,
            story_segments=value.story_segments,
            protected_semantic_adjudications=(
                value.protected_semantic_adjudications
            ),
            diagnostic_story_segments=(),
            diagnostic_protected_semantic_adjudications=(),
            finalization_package=value.finalization_package,
        )


@dataclass(frozen=True, slots=True)
class ProviderFinalSequenceDraftV1:
    """Provider-owned final items; Python derives the redundant stop-state copy."""

    schema_version: str
    sequence_id: str
    accepted_turn_id: str
    items: tuple[FinalSequenceItemV1, ...]

    def __post_init__(self) -> None:
        self.compile()

    @classmethod
    def from_final_sequence(
        cls, value: FinalSequenceV1
    ) -> "ProviderFinalSequenceDraftV1":
        return cls(
            schema_version=value.schema_version,
            sequence_id=value.sequence_id,
            accepted_turn_id=value.accepted_turn_id,
            items=value.items,
        )

    def compile(self) -> FinalSequenceV1:
        if self.schema_version != FinalSequenceV1.SCHEMA_VERSION:
            raise ContractValidationError("provider final sequence schema changed")
        if not self.items:
            raise ContractValidationError("final sequence requires at least one item")
        return FinalSequenceV1(
            schema_version=self.schema_version,
            sequence_id=self.sequence_id,
            accepted_turn_id=self.accepted_turn_id,
            items=self.items,
            final_stop_state=self.items[-1].resulting_state,
        )


@dataclass(frozen=True, slots=True)
class ProviderFinalSequenceDraftV2:
    """Provider-owned final items with Python-owned schema identity and stop state."""

    sequence_id: str
    accepted_turn_id: str
    items: tuple[FinalSequenceItemV1, ...]

    def __post_init__(self) -> None:
        self.compile()

    @classmethod
    def from_final_sequence(
        cls, value: FinalSequenceV1
    ) -> "ProviderFinalSequenceDraftV2":
        return cls(
            sequence_id=value.sequence_id,
            accepted_turn_id=value.accepted_turn_id,
            items=value.items,
        )

    def compile(self) -> FinalSequenceV1:
        if not self.items:
            raise ContractValidationError("final sequence requires at least one item")
        return FinalSequenceV1(
            schema_version=FinalSequenceV1.SCHEMA_VERSION,
            sequence_id=self.sequence_id,
            accepted_turn_id=self.accepted_turn_id,
            items=self.items,
            final_stop_state=self.items[-1].resulting_state,
        )


_FINAL_ROLE_FIELDS = (
    "action_owner_ids",
    "state_owner_ids",
    "speaker_ids",
    "affected_ids",
    "addressed_ids",
    "observing_ids",
    "referenced_ids",
)


def _derived_role_ledger(
    segments: tuple[StoryRealizationSegmentV1, ...],
) -> CharacterRoleLedgerV1:
    """Derive one canonical role ledger without interpreting prose."""

    return CharacterRoleLedgerV1(
        **{
            field_name: tuple(
                dict.fromkeys(
                    identity
                    for segment in segments
                    for identity in getattr(segment.roles, field_name)
                )
            )
            for field_name in _FINAL_ROLE_FIELDS
        }
    )


def _derived_claim_keys(
    segments: tuple[StoryRealizationSegmentV1, ...],
) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            claim_key
            for segment in segments
            for claim_key in segment.protected_user_source_claim_keys
        )
    )


@dataclass(frozen=True, slots=True)
class ProviderFinalFieldScopeDraftV1:
    """Active field wire; exact segment citations own repeated bookkeeping."""

    field_name: FinalFieldName
    visibility: FinalInformationVisibility
    knowledge_owner_id: str | None
    story_segment_keys: tuple[str, ...]

    def __post_init__(self) -> None:
        try:
            object.__setattr__(self, "field_name", FinalFieldName(self.field_name))
        except (TypeError, ValueError) as exc:
            raise ContractValidationError("provider final field name is invalid") from exc
        try:
            object.__setattr__(
                self,
                "visibility",
                FinalInformationVisibility(self.visibility),
            )
        except (TypeError, ValueError) as exc:
            raise ContractValidationError(
                "provider final field visibility is invalid"
            ) from exc
        if not self.story_segment_keys:
            raise ContractValidationError(
                "provider final field requires story-material segment citations"
            )
        if len(set(self.story_segment_keys)) != len(self.story_segment_keys):
            raise ContractValidationError(
                "provider final field segment citations are duplicated"
            )
        if self.visibility is FinalInformationVisibility.PUBLIC:
            if self.knowledge_owner_id is not None:
                raise ContractValidationError(
                    "public provider final field cannot have a private owner"
                )
        elif self.knowledge_owner_id is None:
            raise ContractValidationError(
                "private provider final field requires one knowledge owner"
            )

    @classmethod
    def from_canonical(
        cls, value: FinalFieldScopeV1
    ) -> "ProviderFinalFieldScopeDraftV1":
        return cls(
            field_name=value.field_name,
            visibility=value.visibility,
            knowledge_owner_id=value.knowledge_owner_id,
            story_segment_keys=value.story_segment_keys,
        )


@dataclass(frozen=True, slots=True)
class ProviderFinalSequenceItemDraftV1:
    """Active final-item wire with Python-derived ownership and segment union."""

    item_key: str
    planner_beat_keys: tuple[str, ...]
    realized_event: str
    valid_deepseek_additions: tuple[str, ...]
    omitted_or_contradicted_details: tuple[str, ...]
    knowledge_changes: tuple[str, ...]
    material_changes: tuple[str, ...]
    resulting_state: str
    field_scopes: tuple[ProviderFinalFieldScopeDraftV1, ...]

    @classmethod
    def from_canonical(
        cls, value: FinalSequenceItemV1
    ) -> "ProviderFinalSequenceItemDraftV1":
        return cls(
            item_key=value.item_key,
            planner_beat_keys=value.planner_beat_keys,
            realized_event=value.realized_event,
            valid_deepseek_additions=value.valid_deepseek_additions,
            omitted_or_contradicted_details=value.omitted_or_contradicted_details,
            knowledge_changes=value.knowledge_changes,
            material_changes=value.material_changes,
            resulting_state=value.resulting_state,
            field_scopes=tuple(
                ProviderFinalFieldScopeDraftV1.from_canonical(scope)
                for scope in value.field_scopes
            ),
        )


@dataclass(frozen=True, slots=True)
class ProviderFinalSequenceDraftV3:
    """Single-authority finalization wire compiled from material segments."""

    sequence_id: str
    accepted_turn_id: str
    items: tuple[ProviderFinalSequenceItemDraftV1, ...]

    def __post_init__(self) -> None:
        if not self.items:
            raise ContractValidationError("final sequence requires at least one item")

    @classmethod
    def from_final_sequence(
        cls, value: FinalSequenceV1
    ) -> "ProviderFinalSequenceDraftV3":
        return cls(
            sequence_id=value.sequence_id,
            accepted_turn_id=value.accepted_turn_id,
            items=tuple(
                ProviderFinalSequenceItemDraftV1.from_canonical(item)
                for item in value.items
            ),
        )

    def compile(
        self,
        *,
        material_segments: tuple[StoryRealizationSegmentV1, ...],
    ) -> FinalSequenceV1:
        segment_by_key = {
            segment.segment_key: segment for segment in material_segments
        }
        canonical_items: list[FinalSequenceItemV1] = []
        for item in self.items:
            canonical_scopes: list[FinalFieldScopeV1] = []
            item_segments: list[StoryRealizationSegmentV1] = []
            for scope in item.field_scopes:
                cited: list[StoryRealizationSegmentV1] = []
                for segment_key in scope.story_segment_keys:
                    segment = segment_by_key.get(segment_key)
                    if segment is None:
                        raise ContractValidationError(
                            "final field cited an unknown or ineligible story-material segment"
                        )
                    cited.append(segment)
                    if segment not in item_segments:
                        item_segments.append(segment)
                cited_segments = tuple(cited)
                canonical_scopes.append(
                    FinalFieldScopeV1(
                        field_name=scope.field_name,
                        visibility=scope.visibility,
                        knowledge_owner_id=scope.knowledge_owner_id,
                        story_segment_keys=scope.story_segment_keys,
                        roles=_derived_role_ledger(cited_segments),
                        protected_user_source_claim_keys=(
                            _derived_claim_keys(cited_segments)
                        ),
                        persistence_directives=(),
                    )
                )
            item_segment_tuple = tuple(item_segments)
            private_owner_ids = tuple(
                dict.fromkeys(
                    scope.knowledge_owner_id
                    for scope in canonical_scopes
                    if scope.visibility
                    is FinalInformationVisibility.CHARACTER_PRIVATE
                    and scope.knowledge_owner_id is not None
                )
            )
            canonical_items.append(
                FinalSequenceItemV1(
                    item_key=item.item_key,
                    planner_beat_keys=item.planner_beat_keys,
                    story_segment_keys=tuple(
                        segment.segment_key for segment in item_segment_tuple
                    ),
                    realized_event=item.realized_event,
                    valid_deepseek_additions=item.valid_deepseek_additions,
                    omitted_or_contradicted_details=(
                        item.omitted_or_contradicted_details
                    ),
                    private_state_owner_ids=private_owner_ids,
                    knowledge_changes=item.knowledge_changes,
                    material_changes=item.material_changes,
                    resulting_state=item.resulting_state,
                    roles=_derived_role_ledger(item_segment_tuple),
                    protected_user_source_claim_keys=(
                        _derived_claim_keys(item_segment_tuple)
                    ),
                    field_scopes=tuple(canonical_scopes),
                )
            )
        return FinalSequenceV1(
            schema_version=FinalSequenceV1.SCHEMA_VERSION,
            sequence_id=self.sequence_id,
            accepted_turn_id=self.accepted_turn_id,
            items=tuple(canonical_items),
            final_stop_state=canonical_items[-1].resulting_state,
        )


@dataclass(frozen=True, slots=True)
class ContinuousSemanticValidatorDraftV1:
    """Active V3 Validator wire; the Writer never supplies these semantics."""

    SCHEMA_VERSION: ClassVar[str] = "cera.continuous_semantic_validator_draft.v1"

    schema_version: str
    package_id: str
    world_id: str
    branch_id: str
    task_mode: ValidatorTaskMode
    semantic_status: ValidatorSemanticStatus
    reason_codes: tuple[str, ...]
    story_segments: tuple[StoryRealizationSegmentV1, ...]
    complete_final_sequence: FinalSequenceV1 | None
    creator_review: CreatorReviewAssessment | None
    protected_semantic_adjudications: tuple[
        ProtectedSemanticAdjudicationV1, ...
    ]
    event_record: ProviderEventRecordDraftV1 | None
    optional_scene_summary: ProviderSceneSummaryDraftV1 | None

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError(
                "continuous Semantic Validator provider schema changed"
            )
        if self.task_mode is ValidatorTaskMode.FINALIZE_TURN and not self.story_segments:
            raise ContractValidationError(
                "turn Semantic Validator requires exact story spans"
            )
        if self.task_mode is ValidatorTaskMode.SCENE_SUMMARY and self.story_segments:
            raise ContractValidationError(
                "scene-summary Validator cannot classify Writer prose"
            )
        if self.task_mode is ValidatorTaskMode.SCENE_SUMMARY:
            if self.semantic_status is not ValidatorSemanticStatus.ACCEPTED:
                raise ContractValidationError(
                    "scene-summary Validator must use the accepted summary contract"
                )
        elif self.semantic_status is ValidatorSemanticStatus.ACCEPTED:
            if self.reason_codes:
                raise ContractValidationError(
                    "accepted Semantic Validator cannot carry rejection reasons"
                )
        elif self.semantic_status is ValidatorSemanticStatus.CONCERN:
            if not self.reason_codes:
                raise ContractValidationError(
                    "concerning Semantic Validator requires reason codes"
                )
            if (
                self.creator_review is None
                or self.creator_review.reason_codes != self.reason_codes
            ):
                raise ContractValidationError(
                    "concerning Semantic Validator reasons changed creator review"
                )
        else:
            if not self.reason_codes:
                raise ContractValidationError(
                    "non-accepted Semantic Validator requires reason codes"
                )
            if any(
                value is not None
                for value in (
                    self.complete_final_sequence,
                    self.creator_review,
                    self.event_record,
                    self.optional_scene_summary,
                )
            ):
                raise ContractValidationError(
                    "non-accepted Semantic Validator cannot propose finalization"
                )

    def compile(
        self, *, accepted_pairs: tuple[AcceptedTurnPairV1, ...] = ()
    ) -> ContinuousSemanticValidatorResultV1:
        return self._compile_with_sequence(
            self.complete_final_sequence,
            accepted_pairs=accepted_pairs,
        )

    def _compile_with_sequence(
        self,
        complete_final_sequence: FinalSequenceV1 | None,
        *,
        accepted_pairs: tuple[AcceptedTurnPairV1, ...] = (),
    ) -> ContinuousSemanticValidatorResultV1:
        if self.semantic_status not in {
            ValidatorSemanticStatus.ACCEPTED,
            ValidatorSemanticStatus.CONCERN,
        }:
            return ContinuousSemanticValidatorResultV1(
                semantic_status=self.semantic_status,
                reason_codes=self.reason_codes,
                story_segments=self.story_segments,
                protected_semantic_adjudications=(
                    self.protected_semantic_adjudications
                ),
                finalization_package=None,
            )
        legacy = ContinuousValidatorDraftV1(
            schema_version=ContinuousValidatorDraftV1.SCHEMA_VERSION,
            package_id=self.package_id,
            world_id=self.world_id,
            branch_id=self.branch_id,
            task_mode=self.task_mode,
            semantic_status=self.semantic_status,
            complete_final_sequence=complete_final_sequence,
            creator_review=self.creator_review,
            protected_semantic_adjudications=(
                self.protected_semantic_adjudications
            ),
            event_record=self.event_record,
            optional_scene_summary=self.optional_scene_summary,
        )
        return ContinuousSemanticValidatorResultV1.from_finalization_package(
            finalization_package=legacy.compile(accepted_pairs=accepted_pairs),
            story_segments=self.story_segments,
        )


@dataclass(frozen=True, slots=True)
class ContinuousSemanticValidatorDraftV2(ContinuousSemanticValidatorDraftV1):
    """Historical schema-closed Validator wire retained for failed V2 evidence."""

    SCHEMA_VERSION: ClassVar[str] = "cera.continuous_semantic_validator_draft.v2"


@dataclass(frozen=True, slots=True)
class ContinuousSemanticValidatorDraftV3(ContinuousSemanticValidatorDraftV2):
    """Historical wire with Python-derived stop state and provider-owned version."""

    SCHEMA_VERSION: ClassVar[str] = "cera.continuous_semantic_validator_draft.v3"

    complete_final_sequence: ProviderFinalSequenceDraftV1 | None

    def compile(
        self, *, accepted_pairs: tuple[AcceptedTurnPairV1, ...] = ()
    ) -> ContinuousSemanticValidatorResultV1:
        return self._compile_with_sequence(
            (
                self.complete_final_sequence.compile()
                if self.complete_final_sequence is not None
                else None
            ),
            accepted_pairs=accepted_pairs,
        )


@dataclass(frozen=True, slots=True)
class ContinuousSemanticValidatorDraftV4(ContinuousSemanticValidatorDraftV3):
    """Active wire with Python-owned nested identity and final stop state."""

    SCHEMA_VERSION: ClassVar[str] = "cera.continuous_semantic_validator_draft.v4"

    complete_final_sequence: ProviderFinalSequenceDraftV2 | None

    def compile(
        self, *, accepted_pairs: tuple[AcceptedTurnPairV1, ...] = ()
    ) -> ContinuousSemanticValidatorResultV1:
        return self._compile_with_sequence(
            (
                self.complete_final_sequence.compile()
                if self.complete_final_sequence is not None
                else None
            ),
            accepted_pairs=accepted_pairs,
        )


class ProviderGoodReviewKind(str, Enum):
    GOOD = "good"


class ProviderAcceptedDecisionKind(str, Enum):
    ACCEPTED = "accepted"


class ProviderConcernDecisionKind(str, Enum):
    CONCERN = "concern"


class ProviderSceneSummaryDecisionKind(str, Enum):
    SCENE_SUMMARY = "scene_summary"


class ProviderRejectedSemanticStatus(str, Enum):
    REJECTED = "rejected"
    INCONCLUSIVE = "inconclusive"
    ERROR = "error"


class ProviderConcernReviewDisposition(str, Enum):
    CONCERN_ACCEPT_ALLOWED = "concern_accept_allowed"
    CONCERN_ACCEPT_BLOCKED = "concern_accept_blocked"
    CRITICAL_ACCEPT_ALLOWED = "critical_accept_allowed"
    CRITICAL_ACCEPT_BLOCKED = "critical_accept_blocked"


class ProviderDiagnosticIssueOwner(str, Enum):
    USER_REQUEST = "user_request"
    RETRIEVAL = "retrieval"
    PYTHON = "python"
    REASONER = "reasoner"
    COMPOSER = "composer"
    VERIFIER = "verifier"
    PROMPT_MATERIAL = "prompt_material"
    ADULT_EXAMPLES = "adult_examples"
    MIXED = "mixed"
    HARD_BOUNDARY = "hard_boundary"


@dataclass(frozen=True, slots=True)
class ProviderGoodCreatorReviewDraftV1:
    """Good review wire; Python owns the absent diagnostic fields."""

    SCHEMA_VERSION: ClassVar[str] = "cera.provider_good_creator_review.v1"

    schema_version: str
    review_kind: ProviderGoodReviewKind
    publication_eligibility: PublicationEligibility
    creator_reason: str
    verifier_status: str

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("provider good-review schema changed")
        self.compile()

    @property
    def reason_codes(self) -> tuple[str, ...]:
        return ()

    def compile(self) -> CreatorReviewAssessment:
        return CreatorReviewAssessment(
            schema_version=CreatorReviewAssessment.SCHEMA_VERSION,
            severity=CreatorReviewSeverity.GOOD,
            publication_eligibility=self.publication_eligibility,
            issue_owner=ReviewIssueOwner.NONE,
            reason_codes=(),
            creator_reason=self.creator_reason,
            verifier_status=self.verifier_status,
        )

    @classmethod
    def from_assessment(
        cls, value: CreatorReviewAssessment
    ) -> "ProviderGoodCreatorReviewDraftV1":
        if value.severity is not CreatorReviewSeverity.GOOD:
            raise ContractValidationError("provider good-review source is not good")
        return cls(
            schema_version=cls.SCHEMA_VERSION,
            review_kind=ProviderGoodReviewKind.GOOD,
            publication_eligibility=value.publication_eligibility,
            creator_reason=value.creator_reason,
            verifier_status=value.verifier_status,
        )


@dataclass(frozen=True, slots=True)
class ProviderConcernCreatorReviewDraftV1:
    """Concern or critical review with only structurally valid combinations."""

    SCHEMA_VERSION: ClassVar[str] = "cera.provider_concern_creator_review.v1"

    schema_version: str
    disposition: ProviderConcernReviewDisposition
    issue_owner: ProviderDiagnosticIssueOwner
    primary_reason_code: str
    additional_reason_codes: tuple[str, ...]
    creator_reason: str
    verifier_status: str

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("provider concern-review schema changed")
        self.compile()

    @property
    def reason_codes(self) -> tuple[str, ...]:
        return (self.primary_reason_code, *self.additional_reason_codes)

    def compile(self) -> CreatorReviewAssessment:
        severity, eligibility = {
            ProviderConcernReviewDisposition.CONCERN_ACCEPT_ALLOWED: (
                CreatorReviewSeverity.CONCERN,
                PublicationEligibility.ACCEPT_ALLOWED,
            ),
            ProviderConcernReviewDisposition.CONCERN_ACCEPT_BLOCKED: (
                CreatorReviewSeverity.CONCERN,
                PublicationEligibility.ACCEPT_BLOCKED,
            ),
            ProviderConcernReviewDisposition.CRITICAL_ACCEPT_ALLOWED: (
                CreatorReviewSeverity.CRITICAL,
                PublicationEligibility.ACCEPT_ALLOWED,
            ),
            ProviderConcernReviewDisposition.CRITICAL_ACCEPT_BLOCKED: (
                CreatorReviewSeverity.CRITICAL,
                PublicationEligibility.ACCEPT_BLOCKED,
            ),
        }[self.disposition]
        return CreatorReviewAssessment(
            schema_version=CreatorReviewAssessment.SCHEMA_VERSION,
            severity=severity,
            publication_eligibility=eligibility,
            issue_owner=ReviewIssueOwner(self.issue_owner.value),
            reason_codes=self.reason_codes,
            creator_reason=self.creator_reason,
            verifier_status=self.verifier_status,
        )


@dataclass(frozen=True, slots=True)
class ProviderAcceptedTurnDecisionDraftV1:
    SCHEMA_VERSION: ClassVar[str] = "cera.provider_accepted_turn_decision.v1"

    schema_version: str
    decision_kind: ProviderAcceptedDecisionKind
    story_segments: tuple[StoryRealizationSegmentV1, ...]
    complete_final_sequence: ProviderFinalSequenceDraftV2
    creator_review: ProviderGoodCreatorReviewDraftV1
    protected_semantic_adjudications: tuple[
        ProtectedSemanticAdjudicationV1, ...
    ]
    event_record: ProviderEventRecordDraftV1

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("provider accepted-decision schema changed")


@dataclass(frozen=True, slots=True)
class ProviderConcernTurnDecisionDraftV1:
    SCHEMA_VERSION: ClassVar[str] = "cera.provider_concern_turn_decision.v1"

    schema_version: str
    decision_kind: ProviderConcernDecisionKind
    story_segments: tuple[StoryRealizationSegmentV1, ...]
    complete_final_sequence: ProviderFinalSequenceDraftV2
    creator_review: ProviderConcernCreatorReviewDraftV1
    protected_semantic_adjudications: tuple[
        ProtectedSemanticAdjudicationV1, ...
    ]
    event_record: ProviderEventRecordDraftV1

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("provider concern-decision schema changed")


@dataclass(frozen=True, slots=True)
class ProviderRejectedTurnDecisionDraftV1:
    SCHEMA_VERSION: ClassVar[str] = "cera.provider_rejected_turn_decision.v1"

    schema_version: str
    semantic_status: ProviderRejectedSemanticStatus
    primary_reason_code: str
    additional_reason_codes: tuple[str, ...]
    story_segments: tuple[StoryRealizationSegmentV1, ...]
    protected_semantic_adjudications: tuple[
        ProtectedSemanticAdjudicationV1, ...
    ]

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("provider rejected-decision schema changed")

    @property
    def reason_codes(self) -> tuple[str, ...]:
        return (self.primary_reason_code, *self.additional_reason_codes)


@dataclass(frozen=True, slots=True)
class ProviderProtectedSemanticAdjudicationDraftV1:
    """Model-owned adjudication choices with Python-owned exact hash custody."""

    SCHEMA_VERSION: ClassVar[str] = (
        "cera.provider_protected_semantic_adjudication.v1"
    )

    schema_version: str
    adjudication_key: str
    segment_key: str
    output_start: int
    output_end: int
    protected_user_id: str
    relation: ProtectedSemanticRelationKind
    npc_assertion_owner_ids: tuple[str, ...]
    protected_user_source_claim_keys: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError(
                "provider protected-semantic adjudication schema changed"
            )

    @classmethod
    def from_canonical(
        cls,
        value: ProtectedSemanticAdjudicationV1,
    ) -> "ProviderProtectedSemanticAdjudicationDraftV1":
        return cls(
            schema_version=cls.SCHEMA_VERSION,
            adjudication_key=value.adjudication_key,
            segment_key=value.segment_key,
            output_start=value.output_start,
            output_end=value.output_end,
            protected_user_id=value.protected_user_id,
            relation=value.relation,
            npc_assertion_owner_ids=value.npc_assertion_owner_ids,
            protected_user_source_claim_keys=(
                value.protected_user_source_claim_keys
            ),
        )

    def compile(
        self,
        *,
        writer_story_text: str,
        story_segment: StoryRealizationSegmentV1,
    ) -> ProtectedSemanticAdjudicationV1:
        if (
            not isinstance(writer_story_text, str)
            or not writer_story_text
            or "\x00" in writer_story_text
        ):
            raise ContractValidationError(
                "Validator hash custody requires immutable Writer text"
            )
        if story_segment.segment_key != self.segment_key:
            raise ContractValidationError(
                "protected adjudication cited an unknown Validator story segment"
            )
        if (
            story_segment.output_end > len(writer_story_text)
            or writer_story_text[
                story_segment.output_start : story_segment.output_end
            ]
            != story_segment.exact_text
        ):
            raise ContractValidationError(
                "Validator story segment changed immutable Writer bytes"
            )
        if (
            type(self.output_start) is not int
            or type(self.output_end) is not int
            or self.output_start < story_segment.output_start
            or self.output_end > story_segment.output_end
            or self.output_end <= self.output_start
        ):
            raise ContractValidationError(
                "protected adjudication span is empty, out of bounds, or crosses its segment"
            )
        return ProtectedSemanticAdjudicationV1(
            schema_version=ProtectedSemanticAdjudicationV1.SCHEMA_VERSION,
            adjudication_key=self.adjudication_key,
            segment_key=self.segment_key,
            output_start=self.output_start,
            output_end=self.output_end,
            exact_text_sha256=text_sha256(
                writer_story_text[self.output_start : self.output_end]
            ),
            protected_user_id=self.protected_user_id,
            relation=self.relation,
            npc_assertion_owner_ids=self.npc_assertion_owner_ids,
            protected_user_source_claim_keys=(
                self.protected_user_source_claim_keys
            ),
        )


class ProviderProtectedSemanticRelationKindV2(str, Enum):
    """Active provider relation with a source-grounded presentation route."""

    NONE = ProtectedSemanticRelationKind.NONE.value
    PROTECTED_ASSERTION = ProtectedSemanticRelationKind.PROTECTED_ASSERTION.value
    AFFECTED_BY_NPC = ProtectedSemanticRelationKind.AFFECTED_BY_NPC.value
    ADDRESSED_BY_NPC = ProtectedSemanticRelationKind.ADDRESSED_BY_NPC.value
    OBSERVED_BY_NPC = ProtectedSemanticRelationKind.OBSERVED_BY_NPC.value
    REFERENCED_ONLY_BY_NPC = ProtectedSemanticRelationKind.REFERENCED_ONLY_BY_NPC.value
    NEUTRAL_PRESENTATION_REFERENCE = (
        ProtectedSemanticRelationKind.NEUTRAL_PRESENTATION_REFERENCE.value
    )
    SOURCE_GROUNDED_PUBLIC_STATE = "source_grounded_public_state"


@dataclass(frozen=True, slots=True)
class ProviderProtectedSemanticAdjudicationDraftV2:
    """Active adjudication wire with exact current-source-unit citation."""

    SCHEMA_VERSION: ClassVar[str] = (
        "cera.provider_protected_semantic_adjudication.v2"
    )

    schema_version: str
    adjudication_key: str
    segment_key: str
    output_start: int
    output_end: int
    protected_user_id: str
    relation: ProviderProtectedSemanticRelationKindV2
    npc_assertion_owner_ids: tuple[str, ...]
    protected_user_source_claim_keys: tuple[str, ...]
    protected_user_source_unit_keys: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError(
                "provider protected-semantic adjudication V2 schema changed"
            )
        for value in self.protected_user_source_unit_keys:
            if not isinstance(value, str) or not value.strip():
                raise ContractValidationError(
                    "source-grounded public state has an invalid source-unit key"
                )
        if len(set(self.protected_user_source_unit_keys)) != len(
            self.protected_user_source_unit_keys
        ):
            raise ContractValidationError(
                "source-grounded public-state source-unit keys are duplicated"
            )
        source_grounded = (
            self.relation
            is ProviderProtectedSemanticRelationKindV2.SOURCE_GROUNDED_PUBLIC_STATE
        )
        if source_grounded:
            if (
                len(self.protected_user_source_unit_keys) != 1
                or self.npc_assertion_owner_ids
                or self.protected_user_source_claim_keys
            ):
                raise ContractValidationError(
                    "source-grounded public state requires one source unit and no claims or NPC owners"
                )
        elif self.protected_user_source_unit_keys:
            raise ContractValidationError(
                "only source-grounded public state may cite source units"
            )

    @classmethod
    def from_v1(
        cls,
        value: ProviderProtectedSemanticAdjudicationDraftV1,
    ) -> "ProviderProtectedSemanticAdjudicationDraftV2":
        return cls(
            schema_version=cls.SCHEMA_VERSION,
            adjudication_key=value.adjudication_key,
            segment_key=value.segment_key,
            output_start=value.output_start,
            output_end=value.output_end,
            protected_user_id=value.protected_user_id,
            relation=ProviderProtectedSemanticRelationKindV2(
                value.relation.value
            ),
            npc_assertion_owner_ids=value.npc_assertion_owner_ids,
            protected_user_source_claim_keys=(
                value.protected_user_source_claim_keys
            ),
            protected_user_source_unit_keys=(),
        )

    def compile(
        self,
        *,
        writer_story_text: str,
        story_segment: StoryRealizationSegmentV1,
    ) -> tuple[
        ProtectedSemanticAdjudicationV1,
        SourceGroundedPublicStateReceiptV1 | None,
    ]:
        if (
            not isinstance(writer_story_text, str)
            or not writer_story_text
            or "\x00" in writer_story_text
        ):
            raise ContractValidationError(
                "Validator hash custody requires immutable Writer text"
            )
        if story_segment.segment_key != self.segment_key:
            raise ContractValidationError(
                "protected adjudication cited an unknown Validator story segment"
            )
        if (
            story_segment.output_end > len(writer_story_text)
            or writer_story_text[
                story_segment.output_start : story_segment.output_end
            ]
            != story_segment.exact_text
        ):
            raise ContractValidationError(
                "Validator story segment changed immutable Writer bytes"
            )
        if (
            type(self.output_start) is not int
            or type(self.output_end) is not int
            or self.output_start < story_segment.output_start
            or self.output_end > story_segment.output_end
            or self.output_end <= self.output_start
        ):
            raise ContractValidationError(
                "protected adjudication span is empty, out of bounds, or crosses its segment"
            )
        source_grounded = (
            self.relation
            is ProviderProtectedSemanticRelationKindV2.SOURCE_GROUNDED_PUBLIC_STATE
        )
        canonical_relation = (
            ProtectedSemanticRelationKind.NEUTRAL_PRESENTATION_REFERENCE
            if source_grounded
            else ProtectedSemanticRelationKind(self.relation.value)
        )
        adjudication = ProtectedSemanticAdjudicationV1(
            schema_version=ProtectedSemanticAdjudicationV1.SCHEMA_VERSION,
            adjudication_key=self.adjudication_key,
            segment_key=self.segment_key,
            output_start=self.output_start,
            output_end=self.output_end,
            exact_text_sha256=text_sha256(
                writer_story_text[self.output_start : self.output_end]
            ),
            protected_user_id=self.protected_user_id,
            relation=canonical_relation,
            npc_assertion_owner_ids=self.npc_assertion_owner_ids,
            protected_user_source_claim_keys=(
                self.protected_user_source_claim_keys
            ),
        )
        if not source_grounded:
            return adjudication, None
        return adjudication, SourceGroundedPublicStateReceiptV1(
            schema_version=SourceGroundedPublicStateReceiptV1.SCHEMA_VERSION,
            adjudication_key=self.adjudication_key,
            segment_key=self.segment_key,
            output_start=self.output_start,
            output_end=self.output_end,
            exact_text_sha256=adjudication.exact_text_sha256,
            protected_user_id=self.protected_user_id,
            source_unit_key=self.protected_user_source_unit_keys[0],
        )


def _compile_provider_protected_adjudications(
    *,
    writer_story_text: str,
    story_segments: tuple[StoryRealizationSegmentV1, ...],
    adjudications: tuple[ProviderProtectedSemanticAdjudicationDraftV1, ...],
    presentation_segments: tuple[PresentationRealizationSegmentV1, ...] = (),
) -> tuple[ProtectedSemanticAdjudicationV1, ...]:
    segments_by_key = {value.segment_key: value for value in story_segments}
    if len(segments_by_key) != len(story_segments):
        raise ContractValidationError(
            "Validator story segment keys are duplicated before hash custody"
        )
    presentation_by_key = {
        value.segment_key: value for value in presentation_segments
    }
    compiled: list[ProtectedSemanticAdjudicationV1] = []
    for value in adjudications:
        segment = segments_by_key.get(value.segment_key)
        if segment is None:
            raise ContractValidationError(
                "protected adjudication cited an unknown Validator story segment"
            )
        adjudication = value.compile(
            writer_story_text=writer_story_text,
            story_segment=segment,
        )
        if (
            adjudication.relation
            is ProtectedSemanticRelationKind.NEUTRAL_PRESENTATION_REFERENCE
        ):
            presentation = presentation_by_key.get(segment.segment_key)
            ted_role_fields = tuple(
                field
                for field in (
                    "action_owner_ids",
                    "state_owner_ids",
                    "speaker_ids",
                    "affected_ids",
                    "addressed_ids",
                    "observing_ids",
                    "referenced_ids",
                )
                if adjudication.protected_user_id in getattr(segment.roles, field)
            )
            if (
                presentation is None
                or presentation.presentation_class
                is not PresentationRealizationClass.NONPERSISTENT_ATMOSPHERE
                or segment.kind is not StoryRealizationKind.NARRATION
                or ted_role_fields != ("referenced_ids",)
                or segment.roles.assertion_owner_ids
                or adjudication.npc_assertion_owner_ids
                or adjudication.protected_user_source_claim_keys
            ):
                raise ContractValidationError(
                    "neutral protected reference requires nonpersistent presentation-only narration"
                )
        compiled.append(adjudication)
    return tuple(compiled)


def _compile_provider_protected_adjudications_v2(
    *,
    writer_story_text: str,
    story_segments: tuple[StoryRealizationSegmentV1, ...],
    adjudications: tuple[ProviderProtectedSemanticAdjudicationDraftV2, ...],
    presentation_segments: tuple[PresentationRealizationSegmentV1, ...] = (),
) -> tuple[
    tuple[ProtectedSemanticAdjudicationV1, ...],
    tuple[SourceGroundedPublicStateReceiptV1, ...],
]:
    segments_by_key = {value.segment_key: value for value in story_segments}
    if len(segments_by_key) != len(story_segments):
        raise ContractValidationError(
            "Validator story segment keys are duplicated before hash custody"
        )
    presentation_by_key = {
        value.segment_key: value for value in presentation_segments
    }
    compiled: list[ProtectedSemanticAdjudicationV1] = []
    receipts: list[SourceGroundedPublicStateReceiptV1] = []
    for value in adjudications:
        segment = segments_by_key.get(value.segment_key)
        if segment is None:
            raise ContractValidationError(
                "protected adjudication cited an unknown Validator story segment"
            )
        adjudication, receipt = value.compile(
            writer_story_text=writer_story_text,
            story_segment=segment,
        )
        presentation = presentation_by_key.get(segment.segment_key)
        ted_role_fields = tuple(
            field
            for field in _CHARACTER_ROLE_LEDGER_FIELDS
            if adjudication.protected_user_id in getattr(segment.roles, field)
        )
        if receipt is not None:
            if (
                presentation is None
                or presentation.presentation_class
                is not PresentationRealizationClass.NONPERSISTENT_SPATIAL_PHRASING
                or segment.kind is not StoryRealizationKind.NARRATION
                or ted_role_fields != ("referenced_ids",)
                or segment.roles.assertion_owner_ids
                or adjudication.npc_assertion_owner_ids
                or adjudication.protected_user_source_claim_keys
            ):
                raise ContractValidationError(
                    "source-grounded public state requires cited presentation-only narration"
                )
            receipts.append(receipt)
        elif (
            adjudication.relation
            is ProtectedSemanticRelationKind.NEUTRAL_PRESENTATION_REFERENCE
        ):
            if (
                presentation is None
                or presentation.presentation_class
                is not PresentationRealizationClass.NONPERSISTENT_ATMOSPHERE
                or segment.kind is not StoryRealizationKind.NARRATION
                or ted_role_fields != ("referenced_ids",)
                or segment.roles.assertion_owner_ids
                or adjudication.npc_assertion_owner_ids
                or adjudication.protected_user_source_claim_keys
            ):
                raise ContractValidationError(
                    "neutral protected reference requires nonpersistent presentation-only narration"
                )
        compiled.append(adjudication)
    return tuple(compiled), tuple(receipts)


@dataclass(frozen=True, slots=True)
class ProviderAcceptedTurnDecisionDraftV2:
    SCHEMA_VERSION: ClassVar[str] = "cera.provider_accepted_turn_decision.v2"

    schema_version: str
    decision_kind: ProviderAcceptedDecisionKind
    story_segments: tuple[StoryRealizationSegmentV1, ...]
    complete_final_sequence: ProviderFinalSequenceDraftV2
    creator_review: ProviderGoodCreatorReviewDraftV1
    protected_semantic_adjudications: tuple[
        ProviderProtectedSemanticAdjudicationDraftV1, ...
    ]
    event_record: ProviderEventRecordDraftV1

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError(
                "provider accepted-decision V2 schema changed"
            )

    def compile(self, *, writer_story_text: str) -> ProviderAcceptedTurnDecisionDraftV1:
        return ProviderAcceptedTurnDecisionDraftV1(
            schema_version=ProviderAcceptedTurnDecisionDraftV1.SCHEMA_VERSION,
            decision_kind=self.decision_kind,
            story_segments=self.story_segments,
            complete_final_sequence=self.complete_final_sequence,
            creator_review=self.creator_review,
            protected_semantic_adjudications=(
                _compile_provider_protected_adjudications(
                    writer_story_text=writer_story_text,
                    story_segments=self.story_segments,
                    adjudications=self.protected_semantic_adjudications,
                )
            ),
            event_record=self.event_record,
        )


@dataclass(frozen=True, slots=True)
class ProviderConcernTurnDecisionDraftV2:
    SCHEMA_VERSION: ClassVar[str] = "cera.provider_concern_turn_decision.v2"

    schema_version: str
    decision_kind: ProviderConcernDecisionKind
    story_segments: tuple[StoryRealizationSegmentV1, ...]
    complete_final_sequence: ProviderFinalSequenceDraftV2
    creator_review: ProviderConcernCreatorReviewDraftV1
    protected_semantic_adjudications: tuple[
        ProviderProtectedSemanticAdjudicationDraftV1, ...
    ]
    event_record: ProviderEventRecordDraftV1

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError(
                "provider concern-decision V2 schema changed"
            )

    def compile(self, *, writer_story_text: str) -> ProviderConcernTurnDecisionDraftV1:
        return ProviderConcernTurnDecisionDraftV1(
            schema_version=ProviderConcernTurnDecisionDraftV1.SCHEMA_VERSION,
            decision_kind=self.decision_kind,
            story_segments=self.story_segments,
            complete_final_sequence=self.complete_final_sequence,
            creator_review=self.creator_review,
            protected_semantic_adjudications=(
                _compile_provider_protected_adjudications(
                    writer_story_text=writer_story_text,
                    story_segments=self.story_segments,
                    adjudications=self.protected_semantic_adjudications,
                )
            ),
            event_record=self.event_record,
        )


@dataclass(frozen=True, slots=True)
class ProviderRejectedTurnDecisionDraftV2:
    SCHEMA_VERSION: ClassVar[str] = "cera.provider_rejected_turn_decision.v2"

    schema_version: str
    semantic_status: ProviderRejectedSemanticStatus
    primary_reason_code: str
    additional_reason_codes: tuple[str, ...]
    story_segments: tuple[StoryRealizationSegmentV1, ...]
    protected_semantic_adjudications: tuple[
        ProviderProtectedSemanticAdjudicationDraftV1, ...
    ]

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError(
                "provider rejected-decision V2 schema changed"
            )

    def compile(self, *, writer_story_text: str) -> ProviderRejectedTurnDecisionDraftV1:
        return ProviderRejectedTurnDecisionDraftV1(
            schema_version=ProviderRejectedTurnDecisionDraftV1.SCHEMA_VERSION,
            semantic_status=self.semantic_status,
            primary_reason_code=self.primary_reason_code,
            additional_reason_codes=self.additional_reason_codes,
            story_segments=self.story_segments,
            protected_semantic_adjudications=(
                _compile_provider_protected_adjudications(
                    writer_story_text=writer_story_text,
                    story_segments=self.story_segments,
                    adjudications=self.protected_semantic_adjudications,
                )
            ),
        )


@dataclass(frozen=True, slots=True)
class ProviderDiagnosticStorySegmentDraftV1:
    """Hash- and text-free rejected-only semantic span choices."""

    SCHEMA_VERSION: ClassVar[str] = "cera.provider_diagnostic_story_segment.v1"

    schema_version: str
    segment_key: str
    kind: StoryRealizationKind
    output_start: int
    output_end: int
    roles: CharacterRoleLedgerV1
    grounding_status: DiagnosticGroundingStatus
    protected_user_source_claim_keys: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError(
                "provider diagnostic story-segment schema changed"
            )

    def compile(self, *, writer_story_text: str) -> DiagnosticStorySegmentV1:
        if (
            not isinstance(writer_story_text, str)
            or not writer_story_text
            or "\x00" in writer_story_text
        ):
            raise ContractValidationError(
                "diagnostic span custody requires immutable Writer text"
            )
        if (
            type(self.output_start) is not int
            or type(self.output_end) is not int
            or self.output_start < 0
            or self.output_end > len(writer_story_text)
            or self.output_end <= self.output_start
        ):
            raise ContractValidationError(
                "provider diagnostic story span is empty or out of bounds"
            )
        exact_text = writer_story_text[self.output_start : self.output_end]
        return DiagnosticStorySegmentV1(
            schema_version=DiagnosticStorySegmentV1.SCHEMA_VERSION,
            segment_key=self.segment_key,
            kind=self.kind,
            output_start=self.output_start,
            output_end=self.output_end,
            exact_text=exact_text,
            exact_text_sha256=text_sha256(exact_text),
            roles=self.roles,
            grounding_status=self.grounding_status,
            protected_user_source_claim_keys=(
                self.protected_user_source_claim_keys
            ),
        )


@dataclass(frozen=True, slots=True)
class ProviderDiagnosticProtectedSemanticAdjudicationDraftV1:
    """Hash-free violation classification for one diagnostic story span."""

    SCHEMA_VERSION: ClassVar[str] = (
        "cera.provider_diagnostic_protected_semantic_adjudication.v1"
    )

    schema_version: str
    adjudication_key: str
    segment_key: str
    output_start: int
    output_end: int
    protected_user_id: str
    relation: ProtectedSemanticRelationKind
    grounding_status: DiagnosticGroundingStatus
    violation_classification: DiagnosticViolationClassification
    npc_assertion_owner_ids: tuple[str, ...]
    protected_user_source_claim_keys: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError(
                "provider diagnostic protected-adjudication schema changed"
            )

    def compile(
        self,
        *,
        writer_story_text: str,
        story_segment: DiagnosticStorySegmentV1,
    ) -> DiagnosticProtectedSemanticAdjudicationV1:
        if (
            story_segment.segment_key != self.segment_key
            or self.output_start != story_segment.output_start
            or self.output_end != story_segment.output_end
        ):
            raise ContractValidationError(
                "diagnostic adjudication must bind one exact diagnostic story span"
            )
        if (
            story_segment.output_end > len(writer_story_text)
            or writer_story_text[
                story_segment.output_start : story_segment.output_end
            ]
            != story_segment.exact_text
        ):
            raise ContractValidationError(
                "diagnostic adjudication changed immutable Writer bytes"
            )
        return DiagnosticProtectedSemanticAdjudicationV1(
            schema_version=(
                DiagnosticProtectedSemanticAdjudicationV1.SCHEMA_VERSION
            ),
            adjudication_key=self.adjudication_key,
            segment_key=self.segment_key,
            output_start=self.output_start,
            output_end=self.output_end,
            exact_text_sha256=text_sha256(story_segment.exact_text),
            protected_user_id=self.protected_user_id,
            relation=self.relation,
            grounding_status=self.grounding_status,
            violation_classification=self.violation_classification,
            npc_assertion_owner_ids=self.npc_assertion_owner_ids,
            protected_user_source_claim_keys=(
                self.protected_user_source_claim_keys
            ),
        )


def _validate_diagnostic_adjudication_against_segment(
    *,
    segment: DiagnosticStorySegmentV1,
    adjudication: DiagnosticProtectedSemanticAdjudicationV1,
) -> None:
    if adjudication.grounding_status is not segment.grounding_status:
        raise ContractValidationError(
            "diagnostic segment and adjudication grounding disagree"
        )
    protected_user_id = adjudication.protected_user_id
    protected_assertion = protected_user_id in segment.roles.assertion_owner_ids
    npc_owners = tuple(
        value
        for value in segment.roles.assertion_owner_ids
        if value != protected_user_id
    )
    if adjudication.relation is ProtectedSemanticRelationKind.PROTECTED_ASSERTION:
        if (
            not protected_assertion
            or adjudication.npc_assertion_owner_ids
            or adjudication.protected_user_source_claim_keys
            != segment.protected_user_source_claim_keys
        ):
            raise ContractValidationError(
                "diagnostic protected assertion disagrees with story-span roles"
            )
        return
    if protected_assertion:
        raise ContractValidationError(
            "diagnostic Ted assertion must be classified as protected_assertion"
        )
    if adjudication.relation is ProtectedSemanticRelationKind.NONE:
        if protected_user_id in segment.roles.involved_ids:
            raise ContractValidationError(
                "diagnostic protected-user role was mislabeled as absent"
            )
        return
    if (
        adjudication.relation
        is ProtectedSemanticRelationKind.NEUTRAL_PRESENTATION_REFERENCE
    ):
        ted_role_fields = tuple(
            field
            for field in (
                "action_owner_ids",
                "state_owner_ids",
                "speaker_ids",
                "affected_ids",
                "addressed_ids",
                "observing_ids",
                "referenced_ids",
            )
            if protected_user_id in getattr(segment.roles, field)
        )
        if (
            segment.kind is not StoryRealizationKind.NARRATION
            or ted_role_fields != ("referenced_ids",)
            or segment.roles.assertion_owner_ids
            or adjudication.npc_assertion_owner_ids
            or adjudication.protected_user_source_claim_keys
        ):
            raise ContractValidationError(
                "diagnostic neutral protected reference changed its closed narration roles"
            )
        return
    relation_fields = {
        ProtectedSemanticRelationKind.AFFECTED_BY_NPC: "affected_ids",
        ProtectedSemanticRelationKind.ADDRESSED_BY_NPC: "addressed_ids",
        ProtectedSemanticRelationKind.OBSERVED_BY_NPC: "observing_ids",
        ProtectedSemanticRelationKind.REFERENCED_ONLY_BY_NPC: "referenced_ids",
    }
    role_field = relation_fields[adjudication.relation]
    ted_role_fields = tuple(
        field
        for field in relation_fields.values()
        if protected_user_id in getattr(segment.roles, field)
    )
    if (
        ted_role_fields != (role_field,)
        or set(adjudication.npc_assertion_owner_ids) != set(npc_owners)
        or not npc_owners
    ):
        raise ContractValidationError(
            "diagnostic non-owning relation lacks an exact NPC-owned predicate"
        )


@dataclass(frozen=True, slots=True)
class ProviderRejectedTurnDecisionDraftV3:
    """Active rejected branch with diagnostic-only semantic provenance."""

    SCHEMA_VERSION: ClassVar[str] = "cera.provider_rejected_turn_decision.v3"

    schema_version: str
    semantic_status: ProviderRejectedSemanticStatus
    primary_reason_code: str
    additional_reason_codes: tuple[str, ...]
    diagnostic_story_segments: tuple[ProviderDiagnosticStorySegmentDraftV1, ...]
    diagnostic_protected_semantic_adjudications: tuple[
        ProviderDiagnosticProtectedSemanticAdjudicationDraftV1, ...
    ]

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError(
                "provider rejected-decision V3 schema changed"
            )

    @property
    def reason_codes(self) -> tuple[str, ...]:
        return (self.primary_reason_code, *self.additional_reason_codes)

    def compile(
        self, *, writer_story_text: str
    ) -> ContinuousSemanticValidatorResultV2:
        segments = tuple(
            value.compile(writer_story_text=writer_story_text)
            for value in self.diagnostic_story_segments
        )
        if not segments:
            raise ContractValidationError(
                "rejected Validator decision omitted diagnostic story spans"
            )
        segment_map = {value.segment_key: value for value in segments}
        if len(segment_map) != len(segments):
            raise ContractValidationError(
                "rejected Validator decision duplicated diagnostic story span keys"
            )
        cursor = 0
        for segment in segments:
            if segment.output_start != cursor:
                raise ContractValidationError(
                    "diagnostic story spans are not gap-free and ordered"
                )
            cursor = segment.output_end
        if cursor != len(writer_story_text):
            raise ContractValidationError(
                "diagnostic story spans do not cover immutable Writer text"
            )
        adjudications: list[DiagnosticProtectedSemanticAdjudicationV1] = []
        for value in self.diagnostic_protected_semantic_adjudications:
            segment = segment_map.get(value.segment_key)
            if segment is None:
                raise ContractValidationError(
                    "diagnostic adjudication cited an unknown story span"
                )
            adjudication = value.compile(
                writer_story_text=writer_story_text,
                story_segment=segment,
            )
            _validate_diagnostic_adjudication_against_segment(
                segment=segment,
                adjudication=adjudication,
            )
            adjudications.append(adjudication)
        return ContinuousSemanticValidatorResultV2(
            semantic_status=ValidatorSemanticStatus(self.semantic_status.value),
            reason_codes=self.reason_codes,
            story_segments=(),
            protected_semantic_adjudications=(),
            diagnostic_story_segments=segments,
            diagnostic_protected_semantic_adjudications=tuple(adjudications),
            finalization_package=None,
        )


@dataclass(frozen=True, slots=True)
class ProviderSceneSummaryDecisionDraftV1:
    SCHEMA_VERSION: ClassVar[str] = "cera.provider_scene_summary_decision.v1"

    schema_version: str
    decision_kind: ProviderSceneSummaryDecisionKind
    scene_summary: ProviderSceneSummaryDraftV1

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("provider scene-summary decision schema changed")


ProviderSemanticDecisionDraftV1 = Union[
    ProviderAcceptedTurnDecisionDraftV1,
    ProviderConcernTurnDecisionDraftV1,
    ProviderRejectedTurnDecisionDraftV1,
    ProviderSceneSummaryDecisionDraftV1,
]


@dataclass(frozen=True, slots=True)
class ContinuousSemanticValidatorDraftV5:
    """Active wire with mutually exclusive, schema-valid semantic branches."""

    SCHEMA_VERSION: ClassVar[str] = "cera.continuous_semantic_validator_draft.v5"

    schema_version: str
    package_id: str
    world_id: str
    branch_id: str
    decision: ProviderSemanticDecisionDraftV1

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError(
                "continuous Semantic Validator provider schema changed"
            )

    @classmethod
    def from_v4(
        cls, value: ContinuousSemanticValidatorDraftV4
    ) -> "ContinuousSemanticValidatorDraftV5":
        common = {
            "schema_version": cls.SCHEMA_VERSION,
            "package_id": value.package_id,
            "world_id": value.world_id,
            "branch_id": value.branch_id,
        }
        if value.task_mode is ValidatorTaskMode.SCENE_SUMMARY:
            if value.optional_scene_summary is None:
                raise ContractValidationError("scene-summary source omitted its summary")
            decision: ProviderSemanticDecisionDraftV1 = (
                ProviderSceneSummaryDecisionDraftV1(
                    schema_version=ProviderSceneSummaryDecisionDraftV1.SCHEMA_VERSION,
                    decision_kind=ProviderSceneSummaryDecisionKind.SCENE_SUMMARY,
                    scene_summary=value.optional_scene_summary,
                )
            )
        elif value.semantic_status is ValidatorSemanticStatus.ACCEPTED:
            if (
                value.complete_final_sequence is None
                or value.creator_review is None
                or value.event_record is None
            ):
                raise ContractValidationError("accepted source is incomplete")
            decision = ProviderAcceptedTurnDecisionDraftV1(
                schema_version=ProviderAcceptedTurnDecisionDraftV1.SCHEMA_VERSION,
                decision_kind=ProviderAcceptedDecisionKind.ACCEPTED,
                story_segments=value.story_segments,
                complete_final_sequence=value.complete_final_sequence,
                creator_review=ProviderGoodCreatorReviewDraftV1.from_assessment(
                    value.creator_review
                ),
                protected_semantic_adjudications=(
                    value.protected_semantic_adjudications
                ),
                event_record=value.event_record,
            )
        else:
            raise ContractValidationError(
                "V4 conversion is implemented only for accepted and scene-summary fixtures"
            )
        return cls(**common, decision=decision)

    def compile(
        self, *, accepted_pairs: tuple[AcceptedTurnPairV1, ...] = ()
    ) -> ContinuousSemanticValidatorResultV1:
        decision = self.decision
        if isinstance(decision, ProviderAcceptedTurnDecisionDraftV1):
            draft = ContinuousSemanticValidatorDraftV4(
                schema_version=ContinuousSemanticValidatorDraftV4.SCHEMA_VERSION,
                package_id=self.package_id,
                world_id=self.world_id,
                branch_id=self.branch_id,
                task_mode=ValidatorTaskMode.FINALIZE_TURN,
                semantic_status=ValidatorSemanticStatus.ACCEPTED,
                reason_codes=(),
                story_segments=decision.story_segments,
                complete_final_sequence=decision.complete_final_sequence,
                creator_review=decision.creator_review.compile(),
                protected_semantic_adjudications=(
                    decision.protected_semantic_adjudications
                ),
                event_record=decision.event_record,
                optional_scene_summary=None,
            )
        elif isinstance(decision, ProviderConcernTurnDecisionDraftV1):
            review = decision.creator_review.compile()
            draft = ContinuousSemanticValidatorDraftV4(
                schema_version=ContinuousSemanticValidatorDraftV4.SCHEMA_VERSION,
                package_id=self.package_id,
                world_id=self.world_id,
                branch_id=self.branch_id,
                task_mode=ValidatorTaskMode.FINALIZE_TURN,
                semantic_status=ValidatorSemanticStatus.CONCERN,
                reason_codes=review.reason_codes,
                story_segments=decision.story_segments,
                complete_final_sequence=decision.complete_final_sequence,
                creator_review=review,
                protected_semantic_adjudications=(
                    decision.protected_semantic_adjudications
                ),
                event_record=decision.event_record,
                optional_scene_summary=None,
            )
        elif isinstance(decision, ProviderRejectedTurnDecisionDraftV1):
            draft = ContinuousSemanticValidatorDraftV4(
                schema_version=ContinuousSemanticValidatorDraftV4.SCHEMA_VERSION,
                package_id=self.package_id,
                world_id=self.world_id,
                branch_id=self.branch_id,
                task_mode=ValidatorTaskMode.FINALIZE_TURN,
                semantic_status=ValidatorSemanticStatus(decision.semantic_status.value),
                reason_codes=decision.reason_codes,
                story_segments=decision.story_segments,
                complete_final_sequence=None,
                creator_review=None,
                protected_semantic_adjudications=(
                    decision.protected_semantic_adjudications
                ),
                event_record=None,
                optional_scene_summary=None,
            )
        else:
            draft = ContinuousSemanticValidatorDraftV4(
                schema_version=ContinuousSemanticValidatorDraftV4.SCHEMA_VERSION,
                package_id=self.package_id,
                world_id=self.world_id,
                branch_id=self.branch_id,
                task_mode=ValidatorTaskMode.SCENE_SUMMARY,
                semantic_status=ValidatorSemanticStatus.ACCEPTED,
                reason_codes=(),
                story_segments=(),
                complete_final_sequence=None,
                creator_review=None,
                protected_semantic_adjudications=(),
                event_record=None,
                optional_scene_summary=decision.scene_summary,
            )
        return draft.compile(accepted_pairs=accepted_pairs)


@dataclass(frozen=True, slots=True)
class ContinuousSemanticValidatorDraftV6(ContinuousSemanticValidatorDraftV5):
    """Active wire with structurally required turn evidence collections."""

    SCHEMA_VERSION: ClassVar[str] = "cera.continuous_semantic_validator_draft.v6"


ProviderSemanticDecisionDraftV2 = Union[
    ProviderAcceptedTurnDecisionDraftV2,
    ProviderConcernTurnDecisionDraftV2,
    ProviderRejectedTurnDecisionDraftV2,
    ProviderSceneSummaryDecisionDraftV1,
]


@dataclass(frozen=True, slots=True)
class ContinuousSemanticValidatorDraftV7:
    """Active wire with Python-owned protected-adjudication hash custody."""

    SCHEMA_VERSION: ClassVar[str] = "cera.continuous_semantic_validator_draft.v7"

    schema_version: str
    package_id: str
    world_id: str
    branch_id: str
    decision: ProviderSemanticDecisionDraftV2

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError(
                "continuous Semantic Validator V7 provider schema changed"
            )

    @classmethod
    def from_v4(
        cls,
        value: ContinuousSemanticValidatorDraftV4,
    ) -> "ContinuousSemanticValidatorDraftV7":
        common = {
            "schema_version": cls.SCHEMA_VERSION,
            "package_id": value.package_id,
            "world_id": value.world_id,
            "branch_id": value.branch_id,
        }
        if value.task_mode is ValidatorTaskMode.SCENE_SUMMARY:
            if value.optional_scene_summary is None:
                raise ContractValidationError(
                    "scene-summary source omitted its summary"
                )
            decision: ProviderSemanticDecisionDraftV2 = (
                ProviderSceneSummaryDecisionDraftV1(
                    schema_version=(
                        ProviderSceneSummaryDecisionDraftV1.SCHEMA_VERSION
                    ),
                    decision_kind=ProviderSceneSummaryDecisionKind.SCENE_SUMMARY,
                    scene_summary=value.optional_scene_summary,
                )
            )
        elif value.semantic_status is ValidatorSemanticStatus.ACCEPTED:
            if (
                value.complete_final_sequence is None
                or value.creator_review is None
                or value.event_record is None
            ):
                raise ContractValidationError("accepted source is incomplete")
            decision = ProviderAcceptedTurnDecisionDraftV2(
                schema_version=ProviderAcceptedTurnDecisionDraftV2.SCHEMA_VERSION,
                decision_kind=ProviderAcceptedDecisionKind.ACCEPTED,
                story_segments=value.story_segments,
                complete_final_sequence=ProviderFinalSequenceDraftV2.from_final_sequence(
                    value.complete_final_sequence.compile()
                ),
                creator_review=ProviderGoodCreatorReviewDraftV1.from_assessment(
                    value.creator_review
                ),
                protected_semantic_adjudications=tuple(
                    ProviderProtectedSemanticAdjudicationDraftV1.from_canonical(item)
                    for item in value.protected_semantic_adjudications
                ),
                event_record=value.event_record,
            )
        else:
            raise ContractValidationError(
                "V4 conversion is implemented only for accepted and scene-summary fixtures"
            )
        return cls(**common, decision=decision)

    def compile(
        self,
        *,
        writer_story_text: str | None,
        accepted_pairs: tuple[AcceptedTurnPairV1, ...] = (),
    ) -> ContinuousSemanticValidatorResultV1:
        decision = self.decision
        if isinstance(decision, ProviderSceneSummaryDecisionDraftV1):
            if writer_story_text is not None:
                raise ContractValidationError(
                    "scene-summary Validator cannot receive Writer text"
                )
            historical_decision: ProviderSemanticDecisionDraftV1 = decision
        else:
            if not isinstance(writer_story_text, str) or not writer_story_text:
                raise ContractValidationError(
                    "turn Validator requires typed immutable Writer text"
                )
            historical_decision = decision.compile(
                writer_story_text=writer_story_text
            )
        historical = ContinuousSemanticValidatorDraftV5(
            schema_version=ContinuousSemanticValidatorDraftV5.SCHEMA_VERSION,
            package_id=self.package_id,
            world_id=self.world_id,
            branch_id=self.branch_id,
            decision=historical_decision,
        )
        return historical.compile(accepted_pairs=accepted_pairs)


ProviderSemanticDecisionDraftV3 = Union[
    ProviderAcceptedTurnDecisionDraftV2,
    ProviderConcernTurnDecisionDraftV2,
    ProviderRejectedTurnDecisionDraftV3,
    ProviderSceneSummaryDecisionDraftV1,
]


@dataclass(frozen=True, slots=True)
class ContinuousSemanticValidatorDraftV8:
    """Active branch-separated Validator wire with rejected-only diagnostics."""

    SCHEMA_VERSION: ClassVar[str] = "cera.continuous_semantic_validator_draft.v8"

    schema_version: str
    package_id: str
    world_id: str
    branch_id: str
    decision: ProviderSemanticDecisionDraftV3

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError(
                "continuous Semantic Validator V8 provider schema changed"
            )

    @classmethod
    def from_v4(
        cls,
        value: ContinuousSemanticValidatorDraftV4,
    ) -> "ContinuousSemanticValidatorDraftV8":
        historical = ContinuousSemanticValidatorDraftV7.from_v4(value)
        return cls(
            schema_version=cls.SCHEMA_VERSION,
            package_id=historical.package_id,
            world_id=historical.world_id,
            branch_id=historical.branch_id,
            decision=historical.decision,
        )

    def compile(
        self,
        *,
        writer_story_text: str | None,
        accepted_pairs: tuple[AcceptedTurnPairV1, ...] = (),
    ) -> ContinuousSemanticValidatorResultV2:
        if isinstance(self.decision, ProviderRejectedTurnDecisionDraftV3):
            if not isinstance(writer_story_text, str) or not writer_story_text:
                raise ContractValidationError(
                    "turn Validator requires typed immutable Writer text"
                )
            return self.decision.compile(writer_story_text=writer_story_text)
        historical = ContinuousSemanticValidatorDraftV7(
            schema_version=ContinuousSemanticValidatorDraftV7.SCHEMA_VERSION,
            package_id=self.package_id,
            world_id=self.world_id,
            branch_id=self.branch_id,
            decision=self.decision,
        ).compile(
            writer_story_text=writer_story_text,
            accepted_pairs=accepted_pairs,
        )
        return ContinuousSemanticValidatorResultV2.from_v1(historical)


class ProviderWriterRecallEligibility(str, Enum):
    NOT_APPLICABLE = "not_applicable"
    ELIGIBLE = "eligible"
    INELIGIBLE = "ineligible"


@dataclass(frozen=True, slots=True)
class ProviderRealizationSegmentDraftV1:
    """Validator classification over one exact Writer span.

    Python slices the immutable Writer bytes.  The provider classifies meaning
    and whether the span is transient presentation or a story/material fact.
    """

    SCHEMA_VERSION: ClassVar[str] = "cera.provider_realization_segment.v1"

    schema_version: str
    segment_key: str
    authority_disposition: RealizationAuthorityDisposition
    presentation_class: PresentationRealizationClass | None
    kind: StoryRealizationKind
    output_start: int
    output_end: int
    roles: CharacterRoleLedgerV1
    protected_user_source_claim_keys: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError(
                "provider realization-segment schema changed"
            )
        if (
            self.authority_disposition
            is RealizationAuthorityDisposition.PRESENTATION_ONLY
        ) != (self.presentation_class is not None):
            raise ContractValidationError(
                "realization disposition and presentation class disagree"
            )
        if self.roles.is_empty and not (
            self.authority_disposition
            is RealizationAuthorityDisposition.PRESENTATION_ONLY
            and self.presentation_class
            is PresentationRealizationClass.NONPERSISTENT_ATMOSPHERE
            and self.kind is StoryRealizationKind.NARRATION
        ):
            raise ContractValidationError(
                "an empty role ledger is restricted to actorless atmosphere narration"
            )

    def compile(
        self,
        *,
        writer_story_text: str,
    ) -> tuple[StoryRealizationSegmentV1, PresentationRealizationSegmentV1 | None]:
        if (
            not isinstance(writer_story_text, str)
            or not writer_story_text
            or "\x00" in writer_story_text
            or type(self.output_start) is not int
            or type(self.output_end) is not int
            or self.output_start < 0
            or self.output_end > len(writer_story_text)
            or self.output_end <= self.output_start
        ):
            raise ContractValidationError(
                "realization span is empty or outside immutable Writer text"
            )
        exact_text = writer_story_text[self.output_start : self.output_end]
        semantic_segment = StoryRealizationSegmentV1(
            schema_version=StoryRealizationSegmentV1.SCHEMA_VERSION,
            segment_key=self.segment_key,
            kind=self.kind,
            output_start=self.output_start,
            output_end=self.output_end,
            exact_text=exact_text,
            roles=self.roles,
            protected_user_source_claim_keys=(
                self.protected_user_source_claim_keys
            ),
        )
        if (
            self.authority_disposition
            is RealizationAuthorityDisposition.STORY_MATERIAL_ASSERTION
        ):
            if self.roles.is_empty:
                raise ContractValidationError(
                    "story-material realization requires character roles"
                )
            return semantic_segment, None
        if self.protected_user_source_claim_keys:
            raise ContractValidationError(
                "presentation-only realization cannot carry protected claims"
            )
        assert self.presentation_class is not None
        return semantic_segment, PresentationRealizationSegmentV1(
            schema_version=PresentationRealizationSegmentV1.SCHEMA_VERSION,
            segment_key=self.segment_key,
            presentation_class=self.presentation_class,
            kind=self.kind,
            output_start=self.output_start,
            output_end=self.output_end,
            exact_text=exact_text,
            exact_text_sha256=text_sha256(exact_text),
            roles=self.roles,
        )


def _compile_provider_realization_segments(
    *,
    writer_story_text: str,
    values: tuple[ProviderRealizationSegmentDraftV1, ...],
) -> tuple[
    tuple[StoryRealizationSegmentV1, ...],
    tuple[StoryRealizationSegmentV1, ...],
    tuple[PresentationRealizationSegmentV1, ...],
]:
    if not values:
        raise ContractValidationError(
            "Validator omitted the exhaustive realization-span ledger"
        )
    all_segments: list[StoryRealizationSegmentV1] = []
    material_segments: list[StoryRealizationSegmentV1] = []
    presentation_segments: list[PresentationRealizationSegmentV1] = []
    cursor = 0
    keys: set[str] = set()
    for value in values:
        semantic, presentation = value.compile(
            writer_story_text=writer_story_text
        )
        if semantic.segment_key in keys or semantic.output_start != cursor:
            raise ContractValidationError(
                "realization spans are duplicated, unordered, or not gap-free"
            )
        keys.add(semantic.segment_key)
        cursor = semantic.output_end
        all_segments.append(semantic)
        if presentation is None:
            material_segments.append(semantic)
        else:
            presentation_segments.append(presentation)
    if cursor != len(writer_story_text):
        raise ContractValidationError(
            "realization spans do not cover immutable Writer text"
        )
    if not material_segments:
        raise ContractValidationError(
            "accepted prose requires at least one story/material assertion"
        )
    return (
        tuple(all_segments),
        tuple(material_segments),
        tuple(presentation_segments),
    )


@dataclass(frozen=True, slots=True)
class ProviderRejectedViolationDraftV1:
    """Provider-selected offending span; Python owns exact text custody."""

    SCHEMA_VERSION: ClassVar[str] = "cera.provider_rejected_violation.v1"

    schema_version: str
    segment_key: str
    prohibited_detail_classes: tuple[ProhibitedWriterDetailClass, ...]

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError(
                "provider rejected-violation schema changed"
            )

    def compile(
        self,
        *,
        segment: DiagnosticStorySegmentV1,
    ) -> WriterRecallOffendingSpanV1:
        if segment.segment_key != self.segment_key:
            raise ContractValidationError(
                "Writer recall violation cited the wrong diagnostic span"
            )
        return WriterRecallOffendingSpanV1(
            schema_version=WriterRecallOffendingSpanV1.SCHEMA_VERSION,
            segment_key=segment.segment_key,
            output_start=segment.output_start,
            output_end=segment.output_end,
            exact_text=segment.exact_text,
            exact_text_sha256=segment.exact_text_sha256,
            prohibited_detail_classes=self.prohibited_detail_classes,
        )


@dataclass(frozen=True, slots=True)
class ProviderAcceptedTurnDecisionDraftV3:
    SCHEMA_VERSION: ClassVar[str] = "cera.provider_accepted_turn_decision.v3"

    schema_version: str
    decision_kind: ProviderAcceptedDecisionKind
    realization_segments: tuple[ProviderRealizationSegmentDraftV1, ...]
    complete_final_sequence: ProviderFinalSequenceDraftV2
    creator_review: ProviderGoodCreatorReviewDraftV1
    protected_semantic_adjudications: tuple[
        ProviderProtectedSemanticAdjudicationDraftV1, ...
    ]
    event_record: ProviderEventRecordDraftV1

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError(
                "provider accepted-decision V3 schema changed"
            )


@dataclass(frozen=True, slots=True)
class ProviderConcernTurnDecisionDraftV3:
    SCHEMA_VERSION: ClassVar[str] = "cera.provider_concern_turn_decision.v3"

    schema_version: str
    decision_kind: ProviderConcernDecisionKind
    realization_segments: tuple[ProviderRealizationSegmentDraftV1, ...]
    complete_final_sequence: ProviderFinalSequenceDraftV2
    creator_review: ProviderConcernCreatorReviewDraftV1
    protected_semantic_adjudications: tuple[
        ProviderProtectedSemanticAdjudicationDraftV1, ...
    ]
    event_record: ProviderEventRecordDraftV1

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError(
                "provider concern-decision V3 schema changed"
            )


@dataclass(frozen=True, slots=True)
class ProviderAcceptedTurnDecisionDraftV4:
    SCHEMA_VERSION: ClassVar[str] = "cera.provider_accepted_turn_decision.v4"

    schema_version: str
    decision_kind: ProviderAcceptedDecisionKind
    realization_segments: tuple[ProviderRealizationSegmentDraftV1, ...]
    complete_final_sequence: ProviderFinalSequenceDraftV2
    creator_review: ProviderGoodCreatorReviewDraftV1
    protected_semantic_adjudications: tuple[
        ProviderProtectedSemanticAdjudicationDraftV1, ...
    ]
    event_record: ProviderEventRecordDraftV2

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError(
                "provider accepted-decision V4 schema changed"
            )


@dataclass(frozen=True, slots=True)
class ProviderConcernTurnDecisionDraftV4:
    SCHEMA_VERSION: ClassVar[str] = "cera.provider_concern_turn_decision.v4"

    schema_version: str
    decision_kind: ProviderConcernDecisionKind
    realization_segments: tuple[ProviderRealizationSegmentDraftV1, ...]
    complete_final_sequence: ProviderFinalSequenceDraftV2
    creator_review: ProviderConcernCreatorReviewDraftV1
    protected_semantic_adjudications: tuple[
        ProviderProtectedSemanticAdjudicationDraftV1, ...
    ]
    event_record: ProviderEventRecordDraftV2

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError(
                "provider concern-decision V4 schema changed"
            )


@dataclass(frozen=True, slots=True)
class ProviderRejectedTurnDecisionDraftV4:
    SCHEMA_VERSION: ClassVar[str] = "cera.provider_rejected_turn_decision.v4"

    schema_version: str
    semantic_status: ProviderRejectedSemanticStatus
    primary_reason_code: str
    additional_reason_codes: tuple[str, ...]
    diagnostic_story_segments: tuple[ProviderDiagnosticStorySegmentDraftV1, ...]
    diagnostic_protected_semantic_adjudications: tuple[
        ProviderDiagnosticProtectedSemanticAdjudicationDraftV1, ...
    ]
    writer_recall_eligibility: ProviderWriterRecallEligibility
    writer_recall_violations: tuple[ProviderRejectedViolationDraftV1, ...]

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError(
                "provider rejected-decision V4 schema changed"
            )
        eligible = (
            self.writer_recall_eligibility
            is ProviderWriterRecallEligibility.ELIGIBLE
        )
        if eligible != bool(self.writer_recall_violations):
            raise ContractValidationError(
                "Writer recall eligibility and violations disagree"
            )
        if eligible and self.semantic_status is not ProviderRejectedSemanticStatus.REJECTED:
            raise ContractValidationError(
                "only a rejected Writer-attributable verdict may open recall"
            )

    @property
    def reason_codes(self) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(
                (self.primary_reason_code, *self.additional_reason_codes)
            )
        )

    def compile(
        self,
        *,
        writer_story_text: str,
    ) -> tuple[ContinuousSemanticValidatorResultV2, tuple[WriterRecallOffendingSpanV1, ...]]:
        historical = ProviderRejectedTurnDecisionDraftV3(
            schema_version=ProviderRejectedTurnDecisionDraftV3.SCHEMA_VERSION,
            semantic_status=self.semantic_status,
            primary_reason_code=self.primary_reason_code,
            additional_reason_codes=self.reason_codes[1:],
            diagnostic_story_segments=self.diagnostic_story_segments,
            diagnostic_protected_semantic_adjudications=(
                self.diagnostic_protected_semantic_adjudications
            ),
        ).compile(writer_story_text=writer_story_text)
        segment_map = {
            value.segment_key: value
            for value in historical.diagnostic_story_segments
        }
        if len({value.segment_key for value in self.writer_recall_violations}) != len(
            self.writer_recall_violations
        ):
            raise ContractValidationError(
                "Writer recall violations duplicated a diagnostic span"
            )
        offending: list[WriterRecallOffendingSpanV1] = []
        for value in self.writer_recall_violations:
            segment = segment_map.get(value.segment_key)
            if segment is None:
                raise ContractValidationError(
                    "Writer recall violation cited an unknown diagnostic span"
                )
            offending.append(value.compile(segment=segment))
        return historical, tuple(offending)


@dataclass(frozen=True, slots=True)
class ContinuousSemanticValidatorResultV3:
    """Active result with non-authoritative presentation and recall channels."""

    semantic_status: ValidatorSemanticStatus
    reason_codes: tuple[str, ...]
    story_segments: tuple[StoryRealizationSegmentV1, ...]
    protected_semantic_adjudications: tuple[
        ProtectedSemanticAdjudicationV1, ...
    ]
    presentation_realization_segments: tuple[
        PresentationRealizationSegmentV1, ...
    ]
    presentation_protected_semantic_adjudications: tuple[
        ProtectedSemanticAdjudicationV1, ...
    ]
    diagnostic_story_segments: tuple[DiagnosticStorySegmentV1, ...]
    diagnostic_protected_semantic_adjudications: tuple[
        DiagnosticProtectedSemanticAdjudicationV1, ...
    ]
    finalization_package: ValidatorFinalizationPackageV1 | None
    writer_recall_eligibility: ProviderWriterRecallEligibility
    writer_recall_offending_spans: tuple[WriterRecallOffendingSpanV1, ...]
    source_grounded_public_state_receipts: tuple[
        SourceGroundedPublicStateReceiptV1, ...
    ] = ()

    def __post_init__(self) -> None:
        ContinuousSemanticValidatorResultV2(
            semantic_status=self.semantic_status,
            reason_codes=self.reason_codes,
            story_segments=self.story_segments,
            protected_semantic_adjudications=(
                self.protected_semantic_adjudications
            ),
            diagnostic_story_segments=self.diagnostic_story_segments,
            diagnostic_protected_semantic_adjudications=(
                self.diagnostic_protected_semantic_adjudications
            ),
            finalization_package=self.finalization_package,
        )
        accepted = self.semantic_status in {
            ValidatorSemanticStatus.ACCEPTED,
            ValidatorSemanticStatus.CONCERN,
        }
        if accepted:
            if (
                self.writer_recall_eligibility
                is not ProviderWriterRecallEligibility.NOT_APPLICABLE
                or self.writer_recall_offending_spans
            ):
                raise ContractValidationError(
                    "accepted Validator result cannot carry Writer recall feedback"
                )
            presentation_keys = {
                value.segment_key
                for value in self.presentation_realization_segments
            }
            adjudication_keys = {
                value.segment_key
                for value in self.presentation_protected_semantic_adjudications
            }
            if presentation_keys != adjudication_keys:
                raise ContractValidationError(
                    "presentation spans require separate complete protected adjudication"
                )
            if presentation_keys & {
                value.segment_key for value in self.story_segments
            }:
                raise ContractValidationError(
                    "presentation and story authority segment keys cannot overlap"
                )
            receipt_keys = tuple(
                value.segment_key
                for value in self.source_grounded_public_state_receipts
            )
            if (
                len(receipt_keys) != len(set(receipt_keys))
                or not set(receipt_keys).issubset(presentation_keys)
            ):
                raise ContractValidationError(
                    "source-grounded public state must cite unique presentation spans"
                )
        else:
            if (
                self.presentation_realization_segments
                or self.presentation_protected_semantic_adjudications
                or self.source_grounded_public_state_receipts
                or self.writer_recall_eligibility
                is ProviderWriterRecallEligibility.NOT_APPLICABLE
            ):
                raise ContractValidationError(
                    "rejected Validator result mixed presentation or missing recall disposition"
                )
            eligible = (
                self.writer_recall_eligibility
                is ProviderWriterRecallEligibility.ELIGIBLE
            )
            if eligible != bool(self.writer_recall_offending_spans):
                raise ContractValidationError(
                    "compiled Writer recall eligibility changed"
                )

    @property
    def writer_candidate_disposition(self) -> WriterCandidateDisposition:
        accepted = self.semantic_status in {
            ValidatorSemanticStatus.ACCEPTED,
            ValidatorSemanticStatus.CONCERN,
        }
        if accepted:
            if self.presentation_realization_segments:
                return WriterCandidateDisposition.SOFT_NONCANONICAL_DRIFT
            return WriterCandidateDisposition.CLEAN
        if (
            self.writer_recall_eligibility
            is ProviderWriterRecallEligibility.ELIGIBLE
        ):
            return WriterCandidateDisposition.HARD_WRITER_VIOLATION
        return WriterCandidateDisposition.VALIDATION_UNRESOLVED

    @classmethod
    def from_v2(
        cls,
        value: ContinuousSemanticValidatorResultV2,
        *,
        presentation_realization_segments: tuple[
            PresentationRealizationSegmentV1, ...
        ] = (),
        presentation_protected_semantic_adjudications: tuple[
            ProtectedSemanticAdjudicationV1, ...
        ] = (),
        writer_recall_eligibility: ProviderWriterRecallEligibility | None = None,
        writer_recall_offending_spans: tuple[
            WriterRecallOffendingSpanV1, ...
        ] = (),
        source_grounded_public_state_receipts: tuple[
            SourceGroundedPublicStateReceiptV1, ...
        ] = (),
    ) -> "ContinuousSemanticValidatorResultV3":
        accepted = value.semantic_status in {
            ValidatorSemanticStatus.ACCEPTED,
            ValidatorSemanticStatus.CONCERN,
        }
        return cls(
            semantic_status=value.semantic_status,
            reason_codes=value.reason_codes,
            story_segments=value.story_segments,
            protected_semantic_adjudications=(
                value.protected_semantic_adjudications
            ),
            presentation_realization_segments=(
                presentation_realization_segments
            ),
            presentation_protected_semantic_adjudications=(
                presentation_protected_semantic_adjudications
            ),
            diagnostic_story_segments=value.diagnostic_story_segments,
            diagnostic_protected_semantic_adjudications=(
                value.diagnostic_protected_semantic_adjudications
            ),
            finalization_package=value.finalization_package,
            writer_recall_eligibility=(
                ProviderWriterRecallEligibility.NOT_APPLICABLE
                if accepted
                else writer_recall_eligibility
                or ProviderWriterRecallEligibility.INELIGIBLE
            ),
            writer_recall_offending_spans=writer_recall_offending_spans,
            source_grounded_public_state_receipts=(
                source_grounded_public_state_receipts
            ),
        )

    def build_writer_recall_directive(
        self,
        *,
        rejected_candidate_id: str,
        rejected_story_text: str,
        frozen_authority_package_sha256: str,
        source_attempt_number: int,
    ) -> WriterRecallDirectiveV1:
        if (
            self.writer_recall_eligibility
            is not ProviderWriterRecallEligibility.ELIGIBLE
        ):
            raise ContractValidationError(
                "Validator result does not authorize a Writer recall"
            )
        if any(
            rejected_story_text[value.output_start : value.output_end]
            != value.exact_text
            for value in self.writer_recall_offending_spans
        ):
            raise ContractValidationError(
                "Writer recall feedback changed rejected candidate bytes"
            )
        return WriterRecallDirectiveV1(
            schema_version=WriterRecallDirectiveV1.SCHEMA_VERSION,
            rejected_candidate_id=rejected_candidate_id,
            rejected_story_text_sha256=text_sha256(rejected_story_text),
            frozen_authority_package_sha256=frozen_authority_package_sha256,
            source_attempt_number=source_attempt_number,
            next_attempt_number=source_attempt_number + 1,
            reason_codes=self.reason_codes,
            offending_spans=self.writer_recall_offending_spans,
            authoritative=False,
            attempts_may_merge=False,
        )


@dataclass(frozen=True, slots=True)
class ContinuousCompactRejectedValidatorResultV1:
    """Rejected compact V2 result with no canonical or legacy diagnostic wire."""

    semantic_status: ValidatorSemanticStatus
    reason_codes: tuple[str, ...]
    story_segments: tuple[StoryRealizationSegmentV1, ...]
    protected_semantic_adjudications: tuple[
        ProtectedSemanticAdjudicationV1, ...
    ]
    presentation_realization_segments: tuple[
        PresentationRealizationSegmentV1, ...
    ]
    presentation_protected_semantic_adjudications: tuple[
        ProtectedSemanticAdjudicationV1, ...
    ]
    diagnostic_story_segments: tuple[DiagnosticStorySegmentV1, ...]
    diagnostic_protected_semantic_adjudications: tuple[
        DiagnosticProtectedSemanticAdjudicationV1, ...
    ]
    finalization_package: ValidatorFinalizationPackageV1 | None
    writer_recall_eligibility: ProviderWriterRecallEligibility
    writer_recall_offending_spans: tuple[WriterRecallOffendingSpanV1, ...]
    source_grounded_public_state_receipts: tuple[
        SourceGroundedPublicStateReceiptV1, ...
    ]
    rejected_violation_receipts: tuple[
        CompactRejectedViolationReceiptV1, ...
    ]

    def __post_init__(self) -> None:
        if (
            self.semantic_status is not ValidatorSemanticStatus.REJECTED
            or self.story_segments
            or self.protected_semantic_adjudications
            or self.presentation_realization_segments
            or self.presentation_protected_semantic_adjudications
            or self.diagnostic_story_segments
            or self.diagnostic_protected_semantic_adjudications
            or self.finalization_package is not None
            or self.source_grounded_public_state_receipts
            or not self.rejected_violation_receipts
        ):
            raise ContractValidationError(
                "compact rejected violation result mixed with story authority"
            )
        keys = tuple(
            value.violation_key for value in self.rejected_violation_receipts
        )
        if len(keys) != len(set(keys)):
            raise ContractValidationError(
                "compact rejected violation receipt keys are duplicated"
            )
        cursor = 0
        for index, value in enumerate(self.rejected_violation_receipts):
            if index and value.output_start < cursor:
                raise ContractValidationError(
                    "compact rejected violation receipts overlap or are unordered"
                )
            cursor = value.output_end
        derived_reasons = tuple(
            dict.fromkeys(
                value.violation_class.value
                for value in self.rejected_violation_receipts
            )
        )
        if self.reason_codes != derived_reasons:
            raise ContractValidationError(
                "compact rejected violation reasons were not Python-derived"
            )
        if (
            self.writer_recall_eligibility
            is not ProviderWriterRecallEligibility.ELIGIBLE
        ):
            raise ContractValidationError(
                "compact rejected Writer violations must open bounded recall"
            )
        recall_by_key = {
            value.segment_key: value
            for value in self.writer_recall_offending_spans
        }
        if set(recall_by_key) != set(keys):
            raise ContractValidationError(
                "compact rejected violation recall spans changed receipt coverage"
            )
        for receipt in self.rejected_violation_receipts:
            recall = recall_by_key[receipt.violation_key]
            if (
                recall.output_start != receipt.output_start
                or recall.output_end != receipt.output_end
                or recall.exact_text != receipt.exact_text
                or recall.exact_text_sha256 != receipt.exact_text_sha256
                or recall.prohibited_detail_classes
                != (receipt.violation_class,)
            ):
                raise ContractValidationError(
                    "compact rejected violation recall feedback changed receipt custody"
                )

    @property
    def writer_candidate_disposition(self) -> WriterCandidateDisposition:
        return WriterCandidateDisposition.HARD_WRITER_VIOLATION

    def build_writer_recall_directive(
        self,
        *,
        rejected_candidate_id: str,
        rejected_story_text: str,
        frozen_authority_package_sha256: str,
        source_attempt_number: int,
    ) -> WriterRecallDirectiveV1:
        if any(
            rejected_story_text[value.output_start : value.output_end]
            != value.exact_text
            for value in self.writer_recall_offending_spans
        ):
            raise ContractValidationError(
                "Writer recall feedback changed rejected candidate bytes"
            )
        return WriterRecallDirectiveV1(
            schema_version=WriterRecallDirectiveV1.SCHEMA_VERSION,
            rejected_candidate_id=rejected_candidate_id,
            rejected_story_text_sha256=text_sha256(rejected_story_text),
            frozen_authority_package_sha256=frozen_authority_package_sha256,
            source_attempt_number=source_attempt_number,
            next_attempt_number=source_attempt_number + 1,
            reason_codes=self.reason_codes,
            offending_spans=self.writer_recall_offending_spans,
            authoritative=False,
            attempts_may_merge=False,
        )


ProviderSemanticDecisionDraftV4 = Union[
    ProviderAcceptedTurnDecisionDraftV3,
    ProviderConcernTurnDecisionDraftV3,
    ProviderRejectedTurnDecisionDraftV4,
    ProviderSceneSummaryDecisionDraftV1,
]


@dataclass(frozen=True, slots=True)
class ContinuousSemanticValidatorDraftV9:
    """Active typed realization-boundary and feedback-aware Validator wire."""

    SCHEMA_VERSION: ClassVar[str] = "cera.continuous_semantic_validator_draft.v9"

    schema_version: str
    package_id: str
    world_id: str
    branch_id: str
    decision: ProviderSemanticDecisionDraftV4

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError(
                "continuous Semantic Validator V9 provider schema changed"
            )

    @classmethod
    def from_v4(
        cls,
        value: ContinuousSemanticValidatorDraftV4,
    ) -> "ContinuousSemanticValidatorDraftV9":
        historical = ContinuousSemanticValidatorDraftV8.from_v4(value)
        decision = historical.decision
        if isinstance(decision, ProviderAcceptedTurnDecisionDraftV2):
            active: ProviderSemanticDecisionDraftV4 = (
                ProviderAcceptedTurnDecisionDraftV3(
                    schema_version=(
                        ProviderAcceptedTurnDecisionDraftV3.SCHEMA_VERSION
                    ),
                    decision_kind=decision.decision_kind,
                    realization_segments=tuple(
                        ProviderRealizationSegmentDraftV1(
                            schema_version=(
                                ProviderRealizationSegmentDraftV1.SCHEMA_VERSION
                            ),
                            segment_key=segment.segment_key,
                            authority_disposition=(
                                RealizationAuthorityDisposition.STORY_MATERIAL_ASSERTION
                            ),
                            presentation_class=None,
                            kind=segment.kind,
                            output_start=segment.output_start,
                            output_end=segment.output_end,
                            roles=segment.roles,
                            protected_user_source_claim_keys=(
                                segment.protected_user_source_claim_keys
                            ),
                        )
                        for segment in decision.story_segments
                    ),
                    complete_final_sequence=decision.complete_final_sequence,
                    creator_review=decision.creator_review,
                    protected_semantic_adjudications=(
                        decision.protected_semantic_adjudications
                    ),
                    event_record=decision.event_record,
                )
            )
        else:
            active = decision
        return cls(
            schema_version=cls.SCHEMA_VERSION,
            package_id=historical.package_id,
            world_id=historical.world_id,
            branch_id=historical.branch_id,
            decision=active,
        )

    def compile(
        self,
        *,
        writer_story_text: str | None,
        accepted_pairs: tuple[AcceptedTurnPairV1, ...] = (),
    ) -> ContinuousSemanticValidatorResultV3:
        decision = self.decision
        if isinstance(decision, ProviderSceneSummaryDecisionDraftV1):
            historical = ContinuousSemanticValidatorDraftV8(
                schema_version=ContinuousSemanticValidatorDraftV8.SCHEMA_VERSION,
                package_id=self.package_id,
                world_id=self.world_id,
                branch_id=self.branch_id,
                decision=decision,
            ).compile(writer_story_text=None, accepted_pairs=accepted_pairs)
            return ContinuousSemanticValidatorResultV3.from_v2(historical)
        if not isinstance(writer_story_text, str) or not writer_story_text:
            raise ContractValidationError(
                "turn Validator requires typed immutable Writer text"
            )
        if isinstance(decision, ProviderRejectedTurnDecisionDraftV4):
            historical, offending = decision.compile(
                writer_story_text=writer_story_text
            )
            return ContinuousSemanticValidatorResultV3.from_v2(
                historical,
                writer_recall_eligibility=decision.writer_recall_eligibility,
                writer_recall_offending_spans=offending,
            )
        all_segments, material_segments, presentation_segments = (
            _compile_provider_realization_segments(
                writer_story_text=writer_story_text,
                values=decision.realization_segments,
            )
        )
        all_adjudications = _compile_provider_protected_adjudications(
            writer_story_text=writer_story_text,
            story_segments=all_segments,
            adjudications=decision.protected_semantic_adjudications,
            presentation_segments=presentation_segments,
        )
        if {value.segment_key for value in all_adjudications} != {
            value.segment_key for value in all_segments
        }:
            raise ContractValidationError(
                "Validator did not adjudicate every realization span"
            )
        material_keys = {value.segment_key for value in material_segments}
        material_adjudications = tuple(
            value
            for value in all_adjudications
            if value.segment_key in material_keys
        )
        presentation_adjudications = tuple(
            value
            for value in all_adjudications
            if value.segment_key not in material_keys
        )
        if isinstance(decision, ProviderAcceptedTurnDecisionDraftV3):
            historical_decision: ProviderSemanticDecisionDraftV1 = (
                ProviderAcceptedTurnDecisionDraftV1(
                    schema_version=(
                        ProviderAcceptedTurnDecisionDraftV1.SCHEMA_VERSION
                    ),
                    decision_kind=decision.decision_kind,
                    story_segments=material_segments,
                    complete_final_sequence=decision.complete_final_sequence,
                    creator_review=decision.creator_review,
                    protected_semantic_adjudications=material_adjudications,
                    event_record=decision.event_record,
                )
            )
        else:
            historical_decision = ProviderConcernTurnDecisionDraftV1(
                schema_version=ProviderConcernTurnDecisionDraftV1.SCHEMA_VERSION,
                decision_kind=decision.decision_kind,
                story_segments=material_segments,
                complete_final_sequence=decision.complete_final_sequence,
                creator_review=decision.creator_review,
                protected_semantic_adjudications=material_adjudications,
                event_record=decision.event_record,
            )
        historical_result = ContinuousSemanticValidatorResultV2.from_v1(
            ContinuousSemanticValidatorDraftV5(
                schema_version=ContinuousSemanticValidatorDraftV5.SCHEMA_VERSION,
                package_id=self.package_id,
                world_id=self.world_id,
                branch_id=self.branch_id,
                decision=historical_decision,
            ).compile(accepted_pairs=accepted_pairs)
        )
        return ContinuousSemanticValidatorResultV3.from_v2(
            historical_result,
            presentation_realization_segments=presentation_segments,
            presentation_protected_semantic_adjudications=(
                presentation_adjudications
            ),
        )


@dataclass(frozen=True, slots=True)
class ContinuousSemanticValidatorDraftV10(ContinuousSemanticValidatorDraftV9):
    """Active Validator wire with neutral presentation-reference semantics."""

    SCHEMA_VERSION: ClassVar[str] = "cera.continuous_semantic_validator_draft.v10"

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError(
                "continuous Semantic Validator V10 provider schema changed"
            )

    @classmethod
    def from_v4(
        cls,
        value: ContinuousSemanticValidatorDraftV4,
    ) -> "ContinuousSemanticValidatorDraftV10":
        historical = ContinuousSemanticValidatorDraftV9.from_v4(value)
        return cls(
            schema_version=cls.SCHEMA_VERSION,
            package_id=historical.package_id,
            world_id=historical.world_id,
            branch_id=historical.branch_id,
            decision=historical.decision,
        )


ProviderSemanticDecisionDraftV5 = Union[
    ProviderAcceptedTurnDecisionDraftV4,
    ProviderConcernTurnDecisionDraftV4,
    ProviderRejectedTurnDecisionDraftV4,
    ProviderSceneSummaryDecisionDraftV1,
]


def _python_derived_event_record(
    event: ProviderEventRecordDraftV2,
    sequence: ProviderFinalSequenceDraftV2,
) -> ProviderEventRecordDraftV1:
    return ProviderEventRecordDraftV1(
        event_id=event.event_id,
        accepted_turn_id=event.accepted_turn_id,
        scene_id=event.scene_id,
        summary=" ".join(value.realized_event for value in sequence.items),
        final_sequence_item_keys=event.final_sequence_item_keys,
        protected_user_source_claim_keys=event.protected_user_source_claim_keys,
    )


@dataclass(frozen=True, slots=True)
class ContinuousSemanticValidatorDraftV11:
    """Active Validator wire with Python-derived event summary text."""

    SCHEMA_VERSION: ClassVar[str] = "cera.continuous_semantic_validator_draft.v11"

    schema_version: str
    package_id: str
    world_id: str
    branch_id: str
    decision: ProviderSemanticDecisionDraftV5

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError(
                "continuous Semantic Validator V11 provider schema changed"
            )

    @classmethod
    def from_v4(
        cls,
        value: ContinuousSemanticValidatorDraftV4,
    ) -> "ContinuousSemanticValidatorDraftV11":
        historical = ContinuousSemanticValidatorDraftV10.from_v4(value)
        decision = historical.decision
        if isinstance(decision, ProviderAcceptedTurnDecisionDraftV3):
            active: ProviderSemanticDecisionDraftV5 = (
                ProviderAcceptedTurnDecisionDraftV4(
                    schema_version=ProviderAcceptedTurnDecisionDraftV4.SCHEMA_VERSION,
                    decision_kind=decision.decision_kind,
                    realization_segments=decision.realization_segments,
                    complete_final_sequence=decision.complete_final_sequence,
                    creator_review=decision.creator_review,
                    protected_semantic_adjudications=(
                        decision.protected_semantic_adjudications
                    ),
                    event_record=ProviderEventRecordDraftV2(
                        event_id=decision.event_record.event_id,
                        accepted_turn_id=decision.event_record.accepted_turn_id,
                        scene_id=decision.event_record.scene_id,
                        final_sequence_item_keys=(
                            decision.event_record.final_sequence_item_keys
                        ),
                        protected_user_source_claim_keys=(
                            decision.event_record.protected_user_source_claim_keys
                        ),
                    ),
                )
            )
        elif isinstance(decision, ProviderConcernTurnDecisionDraftV3):
            active = ProviderConcernTurnDecisionDraftV4(
                schema_version=ProviderConcernTurnDecisionDraftV4.SCHEMA_VERSION,
                decision_kind=decision.decision_kind,
                realization_segments=decision.realization_segments,
                complete_final_sequence=decision.complete_final_sequence,
                creator_review=decision.creator_review,
                protected_semantic_adjudications=(
                    decision.protected_semantic_adjudications
                ),
                event_record=ProviderEventRecordDraftV2(
                    event_id=decision.event_record.event_id,
                    accepted_turn_id=decision.event_record.accepted_turn_id,
                    scene_id=decision.event_record.scene_id,
                    final_sequence_item_keys=(
                        decision.event_record.final_sequence_item_keys
                    ),
                    protected_user_source_claim_keys=(
                        decision.event_record.protected_user_source_claim_keys
                    ),
                ),
            )
        else:
            active = decision
        return cls(
            schema_version=cls.SCHEMA_VERSION,
            package_id=historical.package_id,
            world_id=historical.world_id,
            branch_id=historical.branch_id,
            decision=active,
        )

    def compile(
        self,
        *,
        writer_story_text: str | None,
        accepted_pairs: tuple[AcceptedTurnPairV1, ...] = (),
    ) -> ContinuousSemanticValidatorResultV3:
        decision = self.decision
        if isinstance(decision, ProviderAcceptedTurnDecisionDraftV4):
            historical: ProviderSemanticDecisionDraftV4 = (
                ProviderAcceptedTurnDecisionDraftV3(
                    schema_version=ProviderAcceptedTurnDecisionDraftV3.SCHEMA_VERSION,
                    decision_kind=decision.decision_kind,
                    realization_segments=decision.realization_segments,
                    complete_final_sequence=decision.complete_final_sequence,
                    creator_review=decision.creator_review,
                    protected_semantic_adjudications=(
                        decision.protected_semantic_adjudications
                    ),
                    event_record=_python_derived_event_record(
                        decision.event_record,
                        decision.complete_final_sequence,
                    ),
                )
            )
        elif isinstance(decision, ProviderConcernTurnDecisionDraftV4):
            historical = ProviderConcernTurnDecisionDraftV3(
                schema_version=ProviderConcernTurnDecisionDraftV3.SCHEMA_VERSION,
                decision_kind=decision.decision_kind,
                realization_segments=decision.realization_segments,
                complete_final_sequence=decision.complete_final_sequence,
                creator_review=decision.creator_review,
                protected_semantic_adjudications=(
                    decision.protected_semantic_adjudications
                ),
                event_record=_python_derived_event_record(
                    decision.event_record,
                    decision.complete_final_sequence,
                ),
            )
        else:
            historical = decision
        return ContinuousSemanticValidatorDraftV10(
            schema_version=ContinuousSemanticValidatorDraftV10.SCHEMA_VERSION,
            package_id=self.package_id,
            world_id=self.world_id,
            branch_id=self.branch_id,
            decision=historical,
        ).compile(
            writer_story_text=writer_story_text,
            accepted_pairs=accepted_pairs,
        )


@dataclass(frozen=True, slots=True)
class ProviderAcceptedTurnDecisionDraftV5:
    SCHEMA_VERSION: ClassVar[str] = "cera.provider_accepted_turn_decision.v5"

    schema_version: str
    decision_kind: ProviderAcceptedDecisionKind
    realization_segments: tuple[ProviderRealizationSegmentDraftV1, ...]
    complete_final_sequence: ProviderFinalSequenceDraftV2
    creator_review: ProviderGoodCreatorReviewDraftV1
    protected_semantic_adjudications: tuple[
        ProviderProtectedSemanticAdjudicationDraftV2, ...
    ]
    event_record: ProviderEventRecordDraftV2

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError(
                "provider accepted-decision V5 schema changed"
            )


@dataclass(frozen=True, slots=True)
class ProviderConcernTurnDecisionDraftV5:
    SCHEMA_VERSION: ClassVar[str] = "cera.provider_concern_turn_decision.v5"

    schema_version: str
    decision_kind: ProviderConcernDecisionKind
    realization_segments: tuple[ProviderRealizationSegmentDraftV1, ...]
    complete_final_sequence: ProviderFinalSequenceDraftV2
    creator_review: ProviderConcernCreatorReviewDraftV1
    protected_semantic_adjudications: tuple[
        ProviderProtectedSemanticAdjudicationDraftV2, ...
    ]
    event_record: ProviderEventRecordDraftV2

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError(
                "provider concern-decision V5 schema changed"
            )


ProviderSemanticDecisionDraftV6 = Union[
    ProviderAcceptedTurnDecisionDraftV5,
    ProviderConcernTurnDecisionDraftV5,
    ProviderRejectedTurnDecisionDraftV4,
    ProviderSceneSummaryDecisionDraftV1,
]


@dataclass(frozen=True, slots=True)
class ContinuousSemanticValidatorDraftV12:
    """Active wire for actorless presentation and source-grounded public state."""

    SCHEMA_VERSION: ClassVar[str] = "cera.continuous_semantic_validator_draft.v12"

    schema_version: str
    package_id: str
    world_id: str
    branch_id: str
    decision: ProviderSemanticDecisionDraftV6

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError(
                "continuous Semantic Validator V12 provider schema changed"
            )

    @classmethod
    def from_v4(
        cls,
        value: ContinuousSemanticValidatorDraftV4,
    ) -> "ContinuousSemanticValidatorDraftV12":
        historical = ContinuousSemanticValidatorDraftV11.from_v4(value)
        decision = historical.decision
        if isinstance(decision, ProviderAcceptedTurnDecisionDraftV4):
            active: ProviderSemanticDecisionDraftV6 = (
                ProviderAcceptedTurnDecisionDraftV5(
                    schema_version=ProviderAcceptedTurnDecisionDraftV5.SCHEMA_VERSION,
                    decision_kind=decision.decision_kind,
                    realization_segments=decision.realization_segments,
                    complete_final_sequence=decision.complete_final_sequence,
                    creator_review=decision.creator_review,
                    protected_semantic_adjudications=tuple(
                        ProviderProtectedSemanticAdjudicationDraftV2.from_v1(item)
                        for item in decision.protected_semantic_adjudications
                    ),
                    event_record=decision.event_record,
                )
            )
        elif isinstance(decision, ProviderConcernTurnDecisionDraftV4):
            active = ProviderConcernTurnDecisionDraftV5(
                schema_version=ProviderConcernTurnDecisionDraftV5.SCHEMA_VERSION,
                decision_kind=decision.decision_kind,
                realization_segments=decision.realization_segments,
                complete_final_sequence=decision.complete_final_sequence,
                creator_review=decision.creator_review,
                protected_semantic_adjudications=tuple(
                    ProviderProtectedSemanticAdjudicationDraftV2.from_v1(item)
                    for item in decision.protected_semantic_adjudications
                ),
                event_record=decision.event_record,
            )
        else:
            active = decision
        return cls(
            schema_version=cls.SCHEMA_VERSION,
            package_id=historical.package_id,
            world_id=historical.world_id,
            branch_id=historical.branch_id,
            decision=active,
        )

    def compile(
        self,
        *,
        writer_story_text: str | None,
        accepted_pairs: tuple[AcceptedTurnPairV1, ...] = (),
    ) -> ContinuousSemanticValidatorResultV3:
        decision = self.decision
        if isinstance(
            decision,
            (ProviderSceneSummaryDecisionDraftV1, ProviderRejectedTurnDecisionDraftV4),
        ):
            return ContinuousSemanticValidatorDraftV11(
                schema_version=ContinuousSemanticValidatorDraftV11.SCHEMA_VERSION,
                package_id=self.package_id,
                world_id=self.world_id,
                branch_id=self.branch_id,
                decision=decision,
            ).compile(
                writer_story_text=writer_story_text,
                accepted_pairs=accepted_pairs,
            )
        if not isinstance(writer_story_text, str) or not writer_story_text:
            raise ContractValidationError(
                "turn Validator requires typed immutable Writer text"
            )
        all_segments, material_segments, presentation_segments = (
            _compile_provider_realization_segments(
                writer_story_text=writer_story_text,
                values=decision.realization_segments,
            )
        )
        all_adjudications, source_receipts = (
            _compile_provider_protected_adjudications_v2(
                writer_story_text=writer_story_text,
                story_segments=all_segments,
                adjudications=decision.protected_semantic_adjudications,
                presentation_segments=presentation_segments,
            )
        )
        if {value.segment_key for value in all_adjudications} != {
            value.segment_key for value in all_segments
        }:
            raise ContractValidationError(
                "Validator did not adjudicate every realization span"
            )
        material_keys = {value.segment_key for value in material_segments}
        material_adjudications = tuple(
            value
            for value in all_adjudications
            if value.segment_key in material_keys
        )
        presentation_adjudications = tuple(
            value
            for value in all_adjudications
            if value.segment_key not in material_keys
        )
        event_record = _python_derived_event_record(
            decision.event_record,
            decision.complete_final_sequence,
        )
        if isinstance(decision, ProviderAcceptedTurnDecisionDraftV5):
            historical_decision: ProviderSemanticDecisionDraftV1 = (
                ProviderAcceptedTurnDecisionDraftV1(
                    schema_version=ProviderAcceptedTurnDecisionDraftV1.SCHEMA_VERSION,
                    decision_kind=decision.decision_kind,
                    story_segments=material_segments,
                    complete_final_sequence=decision.complete_final_sequence,
                    creator_review=decision.creator_review,
                    protected_semantic_adjudications=material_adjudications,
                    event_record=event_record,
                )
            )
        else:
            historical_decision = ProviderConcernTurnDecisionDraftV1(
                schema_version=ProviderConcernTurnDecisionDraftV1.SCHEMA_VERSION,
                decision_kind=decision.decision_kind,
                story_segments=material_segments,
                complete_final_sequence=decision.complete_final_sequence,
                creator_review=decision.creator_review,
                protected_semantic_adjudications=material_adjudications,
                event_record=event_record,
            )
        historical_result = ContinuousSemanticValidatorResultV2.from_v1(
            ContinuousSemanticValidatorDraftV5(
                schema_version=ContinuousSemanticValidatorDraftV5.SCHEMA_VERSION,
                package_id=self.package_id,
                world_id=self.world_id,
                branch_id=self.branch_id,
                decision=historical_decision,
            ).compile(accepted_pairs=accepted_pairs)
        )
        return ContinuousSemanticValidatorResultV3.from_v2(
            historical_result,
            presentation_realization_segments=presentation_segments,
            presentation_protected_semantic_adjudications=(
                presentation_adjudications
            ),
            source_grounded_public_state_receipts=source_receipts,
        )


@dataclass(frozen=True, slots=True)
class ProviderAcceptedTurnDecisionDraftV6:
    SCHEMA_VERSION: ClassVar[str] = "cera.provider_accepted_turn_decision.v6"

    schema_version: str
    decision_kind: ProviderAcceptedDecisionKind
    realization_segments: tuple[ProviderRealizationSegmentDraftV1, ...]
    complete_final_sequence: ProviderFinalSequenceDraftV3
    creator_review: ProviderGoodCreatorReviewDraftV1
    protected_semantic_adjudications: tuple[
        ProviderProtectedSemanticAdjudicationDraftV2, ...
    ]
    event_record: ProviderEventRecordDraftV2

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError(
                "provider accepted-decision V6 schema changed"
            )


@dataclass(frozen=True, slots=True)
class ProviderConcernTurnDecisionDraftV6:
    SCHEMA_VERSION: ClassVar[str] = "cera.provider_concern_turn_decision.v6"

    schema_version: str
    decision_kind: ProviderConcernDecisionKind
    realization_segments: tuple[ProviderRealizationSegmentDraftV1, ...]
    complete_final_sequence: ProviderFinalSequenceDraftV3
    creator_review: ProviderConcernCreatorReviewDraftV1
    protected_semantic_adjudications: tuple[
        ProviderProtectedSemanticAdjudicationDraftV2, ...
    ]
    event_record: ProviderEventRecordDraftV2

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError(
                "provider concern-decision V6 schema changed"
            )


ProviderSemanticDecisionDraftV7 = Union[
    ProviderAcceptedTurnDecisionDraftV6,
    ProviderConcernTurnDecisionDraftV6,
    ProviderRejectedTurnDecisionDraftV4,
    ProviderSceneSummaryDecisionDraftV1,
]


@dataclass(frozen=True, slots=True)
class ContinuousSemanticValidatorDraftV13:
    """Active single-authority finalization wire."""

    SCHEMA_VERSION: ClassVar[str] = "cera.continuous_semantic_validator_draft.v13"

    schema_version: str
    package_id: str
    world_id: str
    branch_id: str
    decision: ProviderSemanticDecisionDraftV7

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError(
                "continuous Semantic Validator V13 provider schema changed"
            )

    @classmethod
    def from_v4(
        cls,
        value: ContinuousSemanticValidatorDraftV4,
    ) -> "ContinuousSemanticValidatorDraftV13":
        historical = ContinuousSemanticValidatorDraftV12.from_v4(value)
        decision = historical.decision
        if isinstance(decision, ProviderAcceptedTurnDecisionDraftV5):
            active: ProviderSemanticDecisionDraftV7 = (
                ProviderAcceptedTurnDecisionDraftV6(
                    schema_version=ProviderAcceptedTurnDecisionDraftV6.SCHEMA_VERSION,
                    decision_kind=decision.decision_kind,
                    realization_segments=decision.realization_segments,
                    complete_final_sequence=(
                        ProviderFinalSequenceDraftV3.from_final_sequence(
                            decision.complete_final_sequence.compile()
                        )
                    ),
                    creator_review=decision.creator_review,
                    protected_semantic_adjudications=(
                        decision.protected_semantic_adjudications
                    ),
                    event_record=decision.event_record,
                )
            )
        elif isinstance(decision, ProviderConcernTurnDecisionDraftV5):
            active = ProviderConcernTurnDecisionDraftV6(
                schema_version=ProviderConcernTurnDecisionDraftV6.SCHEMA_VERSION,
                decision_kind=decision.decision_kind,
                realization_segments=decision.realization_segments,
                complete_final_sequence=(
                    ProviderFinalSequenceDraftV3.from_final_sequence(
                        decision.complete_final_sequence.compile()
                    )
                ),
                creator_review=decision.creator_review,
                protected_semantic_adjudications=(
                    decision.protected_semantic_adjudications
                ),
                event_record=decision.event_record,
            )
        else:
            active = decision
        return cls(
            schema_version=cls.SCHEMA_VERSION,
            package_id=historical.package_id,
            world_id=historical.world_id,
            branch_id=historical.branch_id,
            decision=active,
        )

    def compile(
        self,
        *,
        writer_story_text: str | None,
        accepted_pairs: tuple[AcceptedTurnPairV1, ...] = (),
    ) -> ContinuousSemanticValidatorResultV3:
        decision = self.decision
        if isinstance(
            decision,
            (ProviderSceneSummaryDecisionDraftV1, ProviderRejectedTurnDecisionDraftV4),
        ):
            return ContinuousSemanticValidatorDraftV12(
                schema_version=ContinuousSemanticValidatorDraftV12.SCHEMA_VERSION,
                package_id=self.package_id,
                world_id=self.world_id,
                branch_id=self.branch_id,
                decision=decision,
            ).compile(
                writer_story_text=writer_story_text,
                accepted_pairs=accepted_pairs,
            )
        if not isinstance(writer_story_text, str) or not writer_story_text:
            raise ContractValidationError(
                "turn Validator requires typed immutable Writer text"
            )
        all_segments, material_segments, presentation_segments = (
            _compile_provider_realization_segments(
                writer_story_text=writer_story_text,
                values=decision.realization_segments,
            )
        )
        all_adjudications, source_receipts = (
            _compile_provider_protected_adjudications_v2(
                writer_story_text=writer_story_text,
                story_segments=all_segments,
                adjudications=decision.protected_semantic_adjudications,
                presentation_segments=presentation_segments,
            )
        )
        if {value.segment_key for value in all_adjudications} != {
            value.segment_key for value in all_segments
        }:
            raise ContractValidationError(
                "Validator did not adjudicate every realization span"
            )
        material_keys = {value.segment_key for value in material_segments}
        material_adjudications = tuple(
            value
            for value in all_adjudications
            if value.segment_key in material_keys
        )
        presentation_adjudications = tuple(
            value
            for value in all_adjudications
            if value.segment_key not in material_keys
        )
        canonical_sequence = decision.complete_final_sequence.compile(
            material_segments=material_segments
        )
        historical_sequence = ProviderFinalSequenceDraftV2.from_final_sequence(
            canonical_sequence
        )
        event_record = _python_derived_event_record(
            decision.event_record,
            historical_sequence,
        )
        if isinstance(decision, ProviderAcceptedTurnDecisionDraftV6):
            historical_decision: ProviderSemanticDecisionDraftV1 = (
                ProviderAcceptedTurnDecisionDraftV1(
                    schema_version=ProviderAcceptedTurnDecisionDraftV1.SCHEMA_VERSION,
                    decision_kind=decision.decision_kind,
                    story_segments=material_segments,
                    complete_final_sequence=historical_sequence,
                    creator_review=decision.creator_review,
                    protected_semantic_adjudications=material_adjudications,
                    event_record=event_record,
                )
            )
        else:
            historical_decision = ProviderConcernTurnDecisionDraftV1(
                schema_version=ProviderConcernTurnDecisionDraftV1.SCHEMA_VERSION,
                decision_kind=decision.decision_kind,
                story_segments=material_segments,
                complete_final_sequence=historical_sequence,
                creator_review=decision.creator_review,
                protected_semantic_adjudications=material_adjudications,
                event_record=event_record,
            )
        historical_result = ContinuousSemanticValidatorResultV2.from_v1(
            ContinuousSemanticValidatorDraftV5(
                schema_version=ContinuousSemanticValidatorDraftV5.SCHEMA_VERSION,
                package_id=self.package_id,
                world_id=self.world_id,
                branch_id=self.branch_id,
                decision=historical_decision,
            ).compile(accepted_pairs=accepted_pairs)
        )
        return ContinuousSemanticValidatorResultV3.from_v2(
            historical_result,
            presentation_realization_segments=presentation_segments,
            presentation_protected_semantic_adjudications=(
                presentation_adjudications
            ),
            source_grounded_public_state_receipts=source_receipts,
        )


@dataclass(frozen=True, slots=True)
class ProviderCompactBeatCoverageDraftV1:
    """One mandatory Planner beat bound to sparse material spans."""

    SCHEMA_VERSION: ClassVar[str] = "cera.provider_compact_beat_coverage.v1"

    schema_version: str
    beat_key: str
    material_segment_keys: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("compact beat-coverage schema changed")
        if not re.fullmatch(LOCAL_KEY_JSON_PATTERN, self.beat_key):
            raise ContractValidationError("compact beat key is invalid")
        if not self.material_segment_keys:
            raise ContractValidationError(
                "compact beat coverage requires material spans"
            )
        if len(set(self.material_segment_keys)) != len(
            self.material_segment_keys
        ):
            raise ContractValidationError(
                "compact beat coverage duplicated a material span"
            )
        for value in self.material_segment_keys:
            if not re.fullmatch(LOCAL_KEY_JSON_PATTERN, value):
                raise ContractValidationError(
                    "compact beat material-segment key is invalid"
                )


@dataclass(frozen=True, slots=True)
class ProviderCompactAcceptedTurnDecisionDraftV1:
    SCHEMA_VERSION: ClassVar[str] = (
        "cera.provider_compact_accepted_turn_decision.v1"
    )

    schema_version: str
    decision_kind: ProviderAcceptedDecisionKind
    material_segments: tuple[ProviderRealizationSegmentDraftV1, ...]
    noncanonical_spans: tuple[ProviderRealizationSegmentDraftV1, ...]
    beat_coverage: tuple[ProviderCompactBeatCoverageDraftV1, ...]
    complete_final_sequence: ProviderFinalSequenceDraftV3
    creator_review: ProviderGoodCreatorReviewDraftV1
    protected_semantic_adjudications: tuple[
        ProviderProtectedSemanticAdjudicationDraftV2, ...
    ]
    event_record: ProviderEventRecordDraftV2

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError(
                "compact accepted-decision schema changed"
            )


@dataclass(frozen=True, slots=True)
class ProviderCompactConcernTurnDecisionDraftV1:
    SCHEMA_VERSION: ClassVar[str] = (
        "cera.provider_compact_concern_turn_decision.v1"
    )

    schema_version: str
    decision_kind: ProviderConcernDecisionKind
    material_segments: tuple[ProviderRealizationSegmentDraftV1, ...]
    noncanonical_spans: tuple[ProviderRealizationSegmentDraftV1, ...]
    beat_coverage: tuple[ProviderCompactBeatCoverageDraftV1, ...]
    complete_final_sequence: ProviderFinalSequenceDraftV3
    creator_review: ProviderConcernCreatorReviewDraftV1
    protected_semantic_adjudications: tuple[
        ProviderProtectedSemanticAdjudicationDraftV2, ...
    ]
    event_record: ProviderEventRecordDraftV2

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError(
                "compact concern-decision schema changed"
            )


@dataclass(frozen=True, slots=True)
class ProviderCompactViolationSpanDraftV1:
    """One provider-authored semantic violation over Python-owned Writer bytes."""

    SCHEMA_VERSION: ClassVar[str] = "cera.provider_compact_violation_span.v1"

    schema_version: str
    violation_key: str
    output_start: int
    output_end: int
    violation_class: ProhibitedWriterDetailClass
    predicate_owner_ids: tuple[str, ...]
    implicated_character_ids: tuple[str, ...]
    protected_user_id: str | None
    protected_user_implication: CompactProtectedUserImplication
    protected_user_source_claim_keys: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError(
                "provider compact violation-span schema changed"
            )
        if not re.fullmatch(LOCAL_KEY_JSON_PATTERN, self.violation_key):
            raise ContractValidationError("compact violation key is invalid")

    def compile(
        self, *, writer_story_text: str
    ) -> CompactRejectedViolationReceiptV1:
        if (
            type(self.output_start) is not int
            or type(self.output_end) is not int
            or self.output_start < 0
            or self.output_end <= self.output_start
            or self.output_end > len(writer_story_text)
        ):
            raise ContractValidationError(
                "provider compact violation span is out of bounds"
            )
        exact_text = writer_story_text[self.output_start : self.output_end]
        return CompactRejectedViolationReceiptV1(
            schema_version=CompactRejectedViolationReceiptV1.SCHEMA_VERSION,
            violation_key=self.violation_key,
            output_start=self.output_start,
            output_end=self.output_end,
            exact_text=exact_text,
            exact_text_sha256=text_sha256(exact_text),
            violation_class=self.violation_class,
            predicate_owner_ids=self.predicate_owner_ids,
            implicated_character_ids=self.implicated_character_ids,
            protected_user_id=self.protected_user_id,
            protected_user_implication=self.protected_user_implication,
            protected_user_source_claim_keys=(
                self.protected_user_source_claim_keys
            ),
        )


@dataclass(frozen=True, slots=True)
class ProviderCompactRejectedTurnDecisionDraftV2:
    """Rejected-only V2 wire with one authoritative sparse receipt family."""

    SCHEMA_VERSION: ClassVar[str] = (
        "cera.provider_compact_rejected_turn_decision.v2"
    )

    schema_version: str
    semantic_status: ProviderRejectedSemanticStatus
    violations: tuple[ProviderCompactViolationSpanDraftV1, ...]

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError(
                "compact rejected-decision V2 schema changed"
            )
        if self.semantic_status is not ProviderRejectedSemanticStatus.REJECTED:
            raise ContractValidationError(
                "compact violation receipts are restricted to rejected results"
            )
        if not self.violations:
            raise ContractValidationError(
                "compact rejected decision omitted violation receipts"
            )

    def compile(
        self, *, writer_story_text: str
    ) -> ContinuousCompactRejectedValidatorResultV1:
        receipts = tuple(
            value.compile(writer_story_text=writer_story_text)
            for value in self.violations
        )
        keys = tuple(value.violation_key for value in receipts)
        if len(keys) != len(set(keys)):
            raise ContractValidationError(
                "compact rejected decision duplicated violation keys"
            )
        cursor = 0
        for index, value in enumerate(receipts):
            if index and value.output_start < cursor:
                raise ContractValidationError(
                    "compact rejected violation spans overlap or are unordered"
                )
            cursor = value.output_end
        reason_codes = tuple(
            dict.fromkeys(value.violation_class.value for value in receipts)
        )
        offending = tuple(
            WriterRecallOffendingSpanV1(
                schema_version=WriterRecallOffendingSpanV1.SCHEMA_VERSION,
                segment_key=value.violation_key,
                output_start=value.output_start,
                output_end=value.output_end,
                exact_text=value.exact_text,
                exact_text_sha256=value.exact_text_sha256,
                prohibited_detail_classes=(value.violation_class,),
            )
            for value in receipts
        )
        return ContinuousCompactRejectedValidatorResultV1(
            semantic_status=ValidatorSemanticStatus.REJECTED,
            reason_codes=reason_codes,
            story_segments=(),
            protected_semantic_adjudications=(),
            presentation_realization_segments=(),
            presentation_protected_semantic_adjudications=(),
            diagnostic_story_segments=(),
            diagnostic_protected_semantic_adjudications=(),
            finalization_package=None,
            writer_recall_eligibility=ProviderWriterRecallEligibility.ELIGIBLE,
            writer_recall_offending_spans=offending,
            source_grounded_public_state_receipts=(),
            rejected_violation_receipts=receipts,
        )


@dataclass(frozen=True, slots=True)
class ProviderCompactRejectedTurnDecisionDraftV1:
    """Sparse rejected branch containing only exact offending spans."""

    SCHEMA_VERSION: ClassVar[str] = (
        "cera.provider_compact_rejected_turn_decision.v1"
    )

    schema_version: str
    semantic_status: ProviderRejectedSemanticStatus
    primary_reason_code: str
    additional_reason_codes: tuple[str, ...]
    offending_spans: tuple[ProviderDiagnosticStorySegmentDraftV1, ...]
    protected_semantic_adjudications: tuple[
        ProviderDiagnosticProtectedSemanticAdjudicationDraftV1, ...
    ]
    writer_recall_eligibility: ProviderWriterRecallEligibility
    writer_recall_violations: tuple[ProviderRejectedViolationDraftV1, ...]

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError(
                "compact rejected-decision schema changed"
            )
        eligible = (
            self.writer_recall_eligibility
            is ProviderWriterRecallEligibility.ELIGIBLE
        )
        if eligible != bool(self.writer_recall_violations):
            raise ContractValidationError(
                "compact Writer recall eligibility and violations disagree"
            )
        if eligible and self.semantic_status is not ProviderRejectedSemanticStatus.REJECTED:
            raise ContractValidationError(
                "only a rejected Writer-attributable compact verdict may open recall"
            )

    @property
    def reason_codes(self) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys((self.primary_reason_code, *self.additional_reason_codes))
        )

    def compile(
        self, *, writer_story_text: str
    ) -> ContinuousSemanticValidatorResultV3:
        segments = tuple(
            value.compile(writer_story_text=writer_story_text)
            for value in self.offending_spans
        )
        if not segments:
            raise ContractValidationError(
                "compact rejected decision omitted offending spans"
            )
        segment_map = {value.segment_key: value for value in segments}
        if len(segment_map) != len(segments):
            raise ContractValidationError(
                "compact rejected decision duplicated offending-span keys"
            )
        prior_end = 0
        for index, segment in enumerate(segments):
            if index and segment.output_start < prior_end:
                raise ContractValidationError(
                    "compact offending spans are unordered or overlap"
                )
            prior_end = segment.output_end
        adjudications: list[DiagnosticProtectedSemanticAdjudicationV1] = []
        for value in self.protected_semantic_adjudications:
            segment = segment_map.get(value.segment_key)
            if segment is None:
                raise ContractValidationError(
                    "compact protected adjudication cited an unknown offending span"
                )
            adjudication = value.compile(
                writer_story_text=writer_story_text,
                story_segment=segment,
            )
            _validate_diagnostic_adjudication_against_segment(
                segment=segment,
                adjudication=adjudication,
            )
            adjudications.append(adjudication)
        if {value.segment_key for value in adjudications} != set(segment_map):
            raise ContractValidationError(
                "compact rejected decision must adjudicate every offending span"
            )
        violation_keys = tuple(
            value.segment_key for value in self.writer_recall_violations
        )
        if len(set(violation_keys)) != len(violation_keys):
            raise ContractValidationError(
                "compact Writer recall duplicated an offending span"
            )
        offending: list[WriterRecallOffendingSpanV1] = []
        for value in self.writer_recall_violations:
            segment = segment_map.get(value.segment_key)
            if segment is None:
                raise ContractValidationError(
                    "compact Writer recall cited an unknown offending span"
                )
            offending.append(value.compile(segment=segment))
        base = ContinuousSemanticValidatorResultV2(
            semantic_status=ValidatorSemanticStatus(self.semantic_status.value),
            reason_codes=self.reason_codes,
            story_segments=(),
            protected_semantic_adjudications=(),
            diagnostic_story_segments=segments,
            diagnostic_protected_semantic_adjudications=tuple(adjudications),
            finalization_package=None,
        )
        return ContinuousSemanticValidatorResultV3.from_v2(
            base,
            writer_recall_eligibility=self.writer_recall_eligibility,
            writer_recall_offending_spans=tuple(offending),
        )


ProviderCompactSemanticDecisionDraftV1 = Union[
    ProviderCompactAcceptedTurnDecisionDraftV1,
    ProviderCompactConcernTurnDecisionDraftV1,
    ProviderCompactRejectedTurnDecisionDraftV1,
]

ProviderCompactSemanticDecisionDraftV2 = Union[
    ProviderCompactAcceptedTurnDecisionDraftV1,
    ProviderCompactConcernTurnDecisionDraftV1,
    ProviderCompactRejectedTurnDecisionDraftV2,
    ProviderCompactRejectedTurnDecisionDraftV1,
]


def _compile_sparse_provider_realization_segments(
    *,
    writer_story_text: str,
    material_values: tuple[ProviderRealizationSegmentDraftV1, ...],
    noncanonical_values: tuple[ProviderRealizationSegmentDraftV1, ...],
) -> tuple[
    tuple[StoryRealizationSegmentV1, ...],
    tuple[StoryRealizationSegmentV1, ...],
    tuple[PresentationRealizationSegmentV1, ...],
]:
    """Compile only listed authority; gaps remain noncanonical by contract."""

    if not material_values:
        raise ContractValidationError(
            "compact accepted prose requires at least one material span"
        )
    all_segments: list[StoryRealizationSegmentV1] = []
    material_segments: list[StoryRealizationSegmentV1] = []
    presentation_segments: list[PresentationRealizationSegmentV1] = []
    for value in material_values:
        semantic, presentation = value.compile(writer_story_text=writer_story_text)
        if presentation is not None:
            raise ContractValidationError(
                "compact material span was classified as presentation"
            )
        all_segments.append(semantic)
        material_segments.append(semantic)
    for value in noncanonical_values:
        semantic, presentation = value.compile(writer_story_text=writer_story_text)
        if presentation is None:
            raise ContractValidationError(
                "compact noncanonical span was classified as material"
            )
        all_segments.append(semantic)
        presentation_segments.append(presentation)
    for values, label in (
        (material_segments, "material"),
        (presentation_segments, "noncanonical"),
    ):
        if values != sorted(
            values, key=lambda value: (value.output_start, value.output_end)
        ):
            raise ContractValidationError(
                f"compact {label} spans must be ordered"
            )
    ordered = sorted(all_segments, key=lambda value: (value.output_start, value.output_end))
    keys: set[str] = set()
    prior_end = 0
    for index, value in enumerate(ordered):
        if value.segment_key in keys:
            raise ContractValidationError("compact span keys are duplicated")
        if index and value.output_start < prior_end:
            raise ContractValidationError("compact spans overlap")
        keys.add(value.segment_key)
        prior_end = value.output_end
    return (
        tuple(ordered),
        tuple(material_segments),
        tuple(presentation_segments),
    )


@dataclass(frozen=True, slots=True)
class ContinuousCompactSemanticValidatorDraftV1:
    """Sparse turn-validation wire; historical Validator wires stay immutable."""

    SCHEMA_VERSION: ClassVar[str] = (
        "cera.continuous_compact_semantic_validator_draft.v1"
    )

    schema_version: str
    package_id: str
    world_id: str
    branch_id: str
    decision: ProviderCompactSemanticDecisionDraftV1

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError(
                "continuous compact Semantic Validator schema changed"
            )

    def compile(
        self,
        *,
        writer_story_text: str | None,
        expected_planner_beat_keys: tuple[str, ...],
        accepted_pairs: tuple[AcceptedTurnPairV1, ...] = (),
    ) -> ContinuousSemanticValidatorResultV3:
        decision = self.decision
        if isinstance(decision, ProviderCompactRejectedTurnDecisionDraftV1):
            if not isinstance(writer_story_text, str) or not writer_story_text:
                raise ContractValidationError(
                    "compact rejected Validator requires immutable Writer text"
                )
            return decision.compile(writer_story_text=writer_story_text)
        if not isinstance(writer_story_text, str) or not writer_story_text:
            raise ContractValidationError(
                "compact turn Validator requires immutable Writer text"
            )
        if not expected_planner_beat_keys or len(set(expected_planner_beat_keys)) != len(
            expected_planner_beat_keys
        ):
            raise ContractValidationError(
                "compact Validator requires exact unique Planner beat keys"
            )
        all_segments, material_segments, presentation_segments = (
            _compile_sparse_provider_realization_segments(
                writer_story_text=writer_story_text,
                material_values=decision.material_segments,
                noncanonical_values=decision.noncanonical_spans,
            )
        )
        material_keys = {value.segment_key for value in material_segments}
        coverage_keys = tuple(value.beat_key for value in decision.beat_coverage)
        if coverage_keys != expected_planner_beat_keys:
            raise ContractValidationError(
                "compact beat coverage changed Planner order or completeness"
            )
        cited_material_keys = {
            segment_key
            for coverage in decision.beat_coverage
            for segment_key in coverage.material_segment_keys
        }
        if cited_material_keys != material_keys:
            raise ContractValidationError(
                "compact beat coverage must cite every and only material span"
            )
        all_adjudications, source_receipts = (
            _compile_provider_protected_adjudications_v2(
                writer_story_text=writer_story_text,
                story_segments=all_segments,
                adjudications=decision.protected_semantic_adjudications,
                presentation_segments=presentation_segments,
            )
        )
        if {value.segment_key for value in all_adjudications} != {
            value.segment_key for value in all_segments
        }:
            raise ContractValidationError(
                "compact Validator must adjudicate every listed span"
            )
        material_adjudications = tuple(
            value
            for value in all_adjudications
            if value.segment_key in material_keys
        )
        presentation_adjudications = tuple(
            value
            for value in all_adjudications
            if value.segment_key not in material_keys
        )
        canonical_sequence = decision.complete_final_sequence.compile(
            material_segments=material_segments
        )
        if {
            beat_key
            for item in canonical_sequence.items
            for beat_key in item.planner_beat_keys
        } != set(expected_planner_beat_keys):
            raise ContractValidationError(
                "compact final sequence changed mandatory beat coverage"
            )
        historical_sequence = ProviderFinalSequenceDraftV2.from_final_sequence(
            canonical_sequence
        )
        event_record = _python_derived_event_record(
            decision.event_record,
            historical_sequence,
        )
        if isinstance(decision, ProviderCompactAcceptedTurnDecisionDraftV1):
            historical_decision: ProviderSemanticDecisionDraftV1 = (
                ProviderAcceptedTurnDecisionDraftV1(
                    schema_version=ProviderAcceptedTurnDecisionDraftV1.SCHEMA_VERSION,
                    decision_kind=decision.decision_kind,
                    story_segments=material_segments,
                    complete_final_sequence=historical_sequence,
                    creator_review=decision.creator_review,
                    protected_semantic_adjudications=material_adjudications,
                    event_record=event_record,
                )
            )
        else:
            historical_decision = ProviderConcernTurnDecisionDraftV1(
                schema_version=ProviderConcernTurnDecisionDraftV1.SCHEMA_VERSION,
                decision_kind=decision.decision_kind,
                story_segments=material_segments,
                complete_final_sequence=historical_sequence,
                creator_review=decision.creator_review,
                protected_semantic_adjudications=material_adjudications,
                event_record=event_record,
            )
        historical_result = ContinuousSemanticValidatorResultV2.from_v1(
            ContinuousSemanticValidatorDraftV5(
                schema_version=ContinuousSemanticValidatorDraftV5.SCHEMA_VERSION,
                package_id=self.package_id,
                world_id=self.world_id,
                branch_id=self.branch_id,
                decision=historical_decision,
            ).compile(accepted_pairs=accepted_pairs)
        )
        return ContinuousSemanticValidatorResultV3.from_v2(
            historical_result,
            presentation_realization_segments=presentation_segments,
            presentation_protected_semantic_adjudications=(
                presentation_adjudications
            ),
            source_grounded_public_state_receipts=source_receipts,
        )


@dataclass(frozen=True, slots=True)
class ContinuousCompactSemanticValidatorDraftV2:
    """V2 compact wire with a sparse rejected violation receipt branch."""

    SCHEMA_VERSION: ClassVar[str] = (
        "cera.continuous_compact_semantic_validator_draft.v2"
    )

    schema_version: str
    package_id: str
    world_id: str
    branch_id: str
    decision: ProviderCompactSemanticDecisionDraftV2

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError(
                "continuous compact Semantic Validator V2 schema changed"
            )
        if (
            isinstance(self.decision, ProviderCompactRejectedTurnDecisionDraftV1)
            and self.decision.semantic_status
            is ProviderRejectedSemanticStatus.REJECTED
        ):
            raise ContractValidationError(
                "compact V2 Writer rejection must use violation receipts"
            )

    def compile(
        self,
        *,
        writer_story_text: str | None,
        expected_planner_beat_keys: tuple[str, ...],
        accepted_pairs: tuple[AcceptedTurnPairV1, ...] = (),
    ) -> (
        ContinuousSemanticValidatorResultV3
        | ContinuousCompactRejectedValidatorResultV1
    ):
        if not isinstance(writer_story_text, str) or not writer_story_text:
            raise ContractValidationError(
                "compact V2 Validator requires immutable Writer text"
            )
        if isinstance(self.decision, ProviderCompactRejectedTurnDecisionDraftV2):
            return self.decision.compile(writer_story_text=writer_story_text)
        return ContinuousCompactSemanticValidatorDraftV1(
            schema_version=ContinuousCompactSemanticValidatorDraftV1.SCHEMA_VERSION,
            package_id=self.package_id,
            world_id=self.world_id,
            branch_id=self.branch_id,
            decision=self.decision,
        ).compile(
            writer_story_text=writer_story_text,
            expected_planner_beat_keys=expected_planner_beat_keys,
            accepted_pairs=accepted_pairs,
        )


@dataclass(frozen=True, slots=True)
class ContinuousSceneWriterDraftV1:
    """Active Writer wire: exact candidate prose and nothing semantic."""

    SCHEMA_VERSION: ClassVar[str] = "cera.scene_writer_draft.v1"

    schema_version: str
    story_text: str

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("continuous Writer schema changed")
        if (
            not isinstance(self.story_text, str)
            or not self.story_text.strip()
            or len(self.story_text) > 256_000
            or "\x00" in self.story_text
        ):
            raise ContractValidationError("continuous Writer story text is invalid")
        try:
            self.story_text.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise ContractValidationError(
                "continuous Writer story text is not valid UTF-8"
            ) from exc


@dataclass(frozen=True, slots=True)
class ProviderReaderIssueReferenceDraftV1:
    """Reader semantic issue span without provider-authored byte hashes."""

    SCHEMA_VERSION: ClassVar[str] = "cera.provider_reader_issue_reference.v1"

    schema_version: str
    issue_code: str
    output_start: int
    output_end: int
    explanation: str

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError(
                "provider Reader issue-reference schema changed"
            )

    def compile(self, *, writer_story_text: str) -> ReaderIssueReferenceV1:
        if (
            type(self.output_start) is not int
            or type(self.output_end) is not int
            or self.output_start < 0
            or self.output_end <= self.output_start
            or self.output_end > len(writer_story_text)
        ):
            raise ContractValidationError(
                "Reader issue span is empty or outside immutable Writer text"
            )
        return ReaderIssueReferenceV1(
            schema_version=ReaderIssueReferenceV1.SCHEMA_VERSION,
            issue_code=self.issue_code,
            output_start=self.output_start,
            output_end=self.output_end,
            exact_text_sha256=text_sha256(
                writer_story_text[self.output_start : self.output_end]
            ),
            explanation=self.explanation,
        )


@dataclass(frozen=True, slots=True)
class ProviderReaderVerdictDraftV1:
    """Historical flat Reader wire retained for immutable provider evidence."""

    SCHEMA_VERSION: ClassVar[str] = "cera.provider_reader_verdict.v1"

    schema_version: str
    verdict_id: str
    world_id: str
    branch_id: str
    turn_id: str
    candidate_id: str
    verdict: ReaderVerdictStatus
    reason_codes: tuple[str, ...]
    issues: tuple[ProviderReaderIssueReferenceDraftV1, ...]
    scene_completeness_score: int
    character_voice_score: int
    dialogue_pacing_score: int
    readability_score: int

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("provider Reader verdict schema changed")

    def compile(self, *, writer_story_text: str) -> ReaderVerdictV1:
        if (
            not isinstance(writer_story_text, str)
            or not writer_story_text
            or "\x00" in writer_story_text
        ):
            raise ContractValidationError(
                "Reader hash custody requires immutable Writer text"
            )
        verdict = ReaderVerdictV1(
            schema_version=ReaderVerdictV1.SCHEMA_VERSION,
            verdict_id=self.verdict_id,
            world_id=self.world_id,
            branch_id=self.branch_id,
            turn_id=self.turn_id,
            candidate_id=self.candidate_id,
            story_text_sha256=text_sha256(writer_story_text),
            verdict=self.verdict,
            reason_codes=self.reason_codes,
            issues=tuple(
                value.compile(writer_story_text=writer_story_text)
                for value in self.issues
            ),
            scene_completeness_score=self.scene_completeness_score,
            character_voice_score=self.character_voice_score,
            dialogue_pacing_score=self.dialogue_pacing_score,
            readability_score=self.readability_score,
        )
        verdict.validate_story_text(writer_story_text)
        return verdict


class ProviderAcceptedReaderVerdictStatus(str, Enum):
    ACCEPTED = "accepted"


class ProviderRejectedReaderVerdictStatus(str, Enum):
    REJECTED = "rejected"


class ProviderInconclusiveReaderVerdictStatus(str, Enum):
    INCONCLUSIVE = "inconclusive"


@dataclass(frozen=True, slots=True)
class ProviderAcceptedReaderDecisionDraftV1:
    """Accepted Reader branch; reasons and issues are structurally absent."""

    SCHEMA_VERSION: ClassVar[str] = "cera.provider_accepted_reader_decision.v1"

    schema_version: str
    verdict: ProviderAcceptedReaderVerdictStatus

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError(
                "provider accepted Reader decision schema changed"
            )


@dataclass(frozen=True, slots=True)
class ProviderRejectedReaderDecisionDraftV1:
    """Rejected Reader branch with exact severe-quality diagnostics."""

    SCHEMA_VERSION: ClassVar[str] = "cera.provider_rejected_reader_decision.v1"

    schema_version: str
    verdict: ProviderRejectedReaderVerdictStatus
    reason_codes: tuple[str, ...]
    issues: tuple[ProviderReaderIssueReferenceDraftV1, ...]

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError(
                "provider rejected Reader decision schema changed"
            )
        if not self.reason_codes or not self.issues:
            raise ContractValidationError(
                "rejected Reader decision requires reasons and issues"
            )


@dataclass(frozen=True, slots=True)
class ProviderInconclusiveReaderDecisionDraftV1:
    """Non-recallable Reader uncertainty branch without rejection issues."""

    SCHEMA_VERSION: ClassVar[str] = "cera.provider_inconclusive_reader_decision.v1"

    schema_version: str
    verdict: ProviderInconclusiveReaderVerdictStatus
    reason_codes: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError(
                "provider inconclusive Reader decision schema changed"
            )
        if not self.reason_codes:
            raise ContractValidationError(
                "inconclusive Reader decision requires reasons"
            )


ProviderReaderDecisionDraftV1 = Union[
    ProviderAcceptedReaderDecisionDraftV1,
    ProviderRejectedReaderDecisionDraftV1,
    ProviderInconclusiveReaderDecisionDraftV1,
]


@dataclass(frozen=True, slots=True)
class ProviderReaderVerdictDraftV2:
    """Active branch-exact Reader wire with Python-owned text hashes."""

    SCHEMA_VERSION: ClassVar[str] = "cera.provider_reader_verdict.v2"

    schema_version: str
    verdict_id: str
    world_id: str
    branch_id: str
    turn_id: str
    candidate_id: str
    decision: ProviderReaderDecisionDraftV1
    scene_completeness_score: int
    character_voice_score: int
    dialogue_pacing_score: int
    readability_score: int

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("provider Reader verdict V2 schema changed")

    def compile(self, *, writer_story_text: str) -> ReaderVerdictV2:
        if (
            not isinstance(writer_story_text, str)
            or not writer_story_text
            or "\x00" in writer_story_text
        ):
            raise ContractValidationError(
                "Reader hash custody requires immutable Writer text"
            )
        decision = self.decision
        if isinstance(decision, ProviderAcceptedReaderDecisionDraftV1):
            verdict_status = ReaderVerdictStatus.ACCEPTED
            reason_codes: tuple[str, ...] = ()
            issues: tuple[ReaderIssueReferenceV1, ...] = ()
        elif isinstance(decision, ProviderRejectedReaderDecisionDraftV1):
            verdict_status = ReaderVerdictStatus.REJECTED
            reason_codes = decision.reason_codes
            issues = tuple(
                value.compile(writer_story_text=writer_story_text)
                for value in decision.issues
            )
        elif isinstance(decision, ProviderInconclusiveReaderDecisionDraftV1):
            verdict_status = ReaderVerdictStatus.INCONCLUSIVE
            reason_codes = decision.reason_codes
            issues = ()
        else:  # pragma: no cover - closed by the dataclass decoder
            raise ContractValidationError("provider Reader decision branch is unknown")
        verdict = ReaderVerdictV2(
            schema_version=ReaderVerdictV2.SCHEMA_VERSION,
            verdict_id=self.verdict_id,
            world_id=self.world_id,
            branch_id=self.branch_id,
            turn_id=self.turn_id,
            candidate_id=self.candidate_id,
            story_text_sha256=text_sha256(writer_story_text),
            verdict=verdict_status,
            reason_codes=reason_codes,
            issues=issues,
            scene_completeness_score=self.scene_completeness_score,
            character_voice_score=self.character_voice_score,
            dialogue_pacing_score=self.dialogue_pacing_score,
            readability_score=self.readability_score,
        )
        verdict.validate_story_text(writer_story_text)
        return verdict


@dataclass(frozen=True, slots=True)
class ContinuousDeepSeekNonOwningRoleDraftV1:
    """Historical V7 non-owning relation retained for immutable evidence."""

    SCHEMA_VERSION: ClassVar[str] = "cera.continuous_deepseek_non_owning_role.v1"

    schema_version: str
    character_id: str
    relation: "ContinuousDeepSeekNonOwningRelationKind"

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError(
                "continuous DeepSeek non-owning role schema changed"
            )
        fields = {
            ContinuousDeepSeekNonOwningRelationKind.AFFECTED: "affected_ids",
            ContinuousDeepSeekNonOwningRelationKind.ADDRESSED: "addressed_ids",
            ContinuousDeepSeekNonOwningRelationKind.OBSERVING: "observing_ids",
            ContinuousDeepSeekNonOwningRelationKind.REFERENCED: "referenced_ids",
        }
        CharacterRoleLedgerV1(**{fields[self.relation]: (self.character_id,)})


class ContinuousDeepSeekNonOwningRelationKind(str, Enum):
    AFFECTED = "affected"
    ADDRESSED = "addressed"
    OBSERVING = "observing"
    REFERENCED = "referenced"


class ContinuousDeepSeekAssertionKind(str, Enum):
    ACTION_OWNED = "action_owned"
    DIALOGUE_OWNED = "dialogue_owned"
    PRIVATE_STATE_OWNED = "private_state_owned"
    CONSENT_OR_DECISION_OWNED = "consent_or_decision_owned"
    UNOWNED_NARRATION = "unowned_narration"


@dataclass(frozen=True, slots=True)
class ContinuousDeepSeekStorySegmentDraftV1:
    """Provider-owned prose with minimal advisory assertion ownership."""

    SCHEMA_VERSION: ClassVar[str] = "cera.continuous_deepseek_story_segment_draft.v2"

    schema_version: str
    segment_key: str
    assertion_kind: ContinuousDeepSeekAssertionKind
    text: str
    owner_ids: tuple[str, ...]
    non_owning_roles: tuple[ContinuousDeepSeekNonOwningRoleDraftV1, ...]
    protected_user_source_claim_keys: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("continuous DeepSeek segment schema changed")
        if not self.segment_key.strip() or len(self.segment_key) > 256:
            raise ContractValidationError("continuous DeepSeek segment key is invalid")
        if not self.text.strip() or self.text != self.text.strip() or len(self.text) > 64_000:
            raise ContractValidationError("continuous DeepSeek segment text is invalid")
        non_owner_ids = tuple(value.character_id for value in self.non_owning_roles)
        if len(self.owner_ids) != len(set(self.owner_ids)) or len(
            non_owner_ids
        ) != len(set(non_owner_ids)):
            raise ContractValidationError("continuous DeepSeek segment identities are duplicated")
        if set(self.owner_ids) & set(non_owner_ids):
            raise ContractValidationError(
                "continuous DeepSeek owner and non-owner identities overlap"
            )
        self.compiled_roles()

    def compiled_roles(self) -> CharacterRoleLedgerV1:
        non_owners: dict[str, tuple[str, ...]] = {
            "affected_ids": tuple(
                value.character_id
                for value in self.non_owning_roles
                if value.relation is ContinuousDeepSeekNonOwningRelationKind.AFFECTED
            ),
            "addressed_ids": tuple(
                value.character_id
                for value in self.non_owning_roles
                if value.relation is ContinuousDeepSeekNonOwningRelationKind.ADDRESSED
            ),
            "observing_ids": tuple(
                value.character_id
                for value in self.non_owning_roles
                if value.relation is ContinuousDeepSeekNonOwningRelationKind.OBSERVING
            ),
            "referenced_ids": tuple(
                value.character_id
                for value in self.non_owning_roles
                if value.relation is ContinuousDeepSeekNonOwningRelationKind.REFERENCED
            ),
        }
        if self.assertion_kind is ContinuousDeepSeekAssertionKind.ACTION_OWNED:
            if not self.owner_ids:
                raise ContractValidationError("continuous DeepSeek action lacks an owner")
            return CharacterRoleLedgerV1(
                action_owner_ids=self.owner_ids,
                **non_owners,
            )
        if self.assertion_kind is ContinuousDeepSeekAssertionKind.DIALOGUE_OWNED:
            if len(self.owner_ids) != 1:
                raise ContractValidationError(
                    "continuous DeepSeek dialogue requires one speaker"
                )
            return CharacterRoleLedgerV1(
                speaker_ids=self.owner_ids,
                **non_owners,
            )
        if self.assertion_kind in {
            ContinuousDeepSeekAssertionKind.PRIVATE_STATE_OWNED,
            ContinuousDeepSeekAssertionKind.CONSENT_OR_DECISION_OWNED,
        }:
            if not self.owner_ids:
                raise ContractValidationError("continuous DeepSeek state lacks an owner")
            return CharacterRoleLedgerV1(
                state_owner_ids=self.owner_ids,
                **non_owners,
            )
        if self.owner_ids or not self.non_owning_roles:
            raise ContractValidationError(
                "continuous DeepSeek narration requires only non-owning characters"
            )
        return CharacterRoleLedgerV1(**non_owners)

    def compiled_kind(self) -> StoryRealizationKind:
        return {
            ContinuousDeepSeekAssertionKind.ACTION_OWNED: StoryRealizationKind.ACTION,
            ContinuousDeepSeekAssertionKind.DIALOGUE_OWNED: StoryRealizationKind.DIALOGUE,
            ContinuousDeepSeekAssertionKind.PRIVATE_STATE_OWNED: (
                StoryRealizationKind.PRIVATE_STATE
            ),
            ContinuousDeepSeekAssertionKind.CONSENT_OR_DECISION_OWNED: (
                StoryRealizationKind.CONSENT_OR_DECISION
            ),
            ContinuousDeepSeekAssertionKind.UNOWNED_NARRATION: (
                StoryRealizationKind.NARRATION
            ),
        }[self.assertion_kind]


@dataclass(frozen=True, slots=True)
class ContinuousDeepSeekProtectedRealizationDraftV1:
    """Exact protected claim bound to one named prose segment."""

    SCHEMA_VERSION: ClassVar[str] = "cera.continuous_deepseek_protected_realization_draft.v1"

    schema_version: str
    claim_key: str
    kind: ProtectedUserSourceClaimKind
    exact_text: str
    segment_key: str

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError(
                "continuous DeepSeek protected realization schema changed"
            )
        if not self.segment_key.strip() or len(self.segment_key) > 256:
            raise ContractValidationError(
                "continuous DeepSeek protected realization segment key is invalid"
            )
        ProtectedUserRealizationSpanV1(
            schema_version=ProtectedUserRealizationSpanV1.SCHEMA_VERSION,
            claim_key=self.claim_key,
            kind=self.kind,
            output_start=0,
            output_end=len(self.exact_text),
            exact_text=self.exact_text,
        )


@dataclass(frozen=True, slots=True)
class ContinuousDeepSeekWireDraftV1:
    """Strict provider DTO compiled into Python-owned story offsets."""

    SCHEMA_VERSION: ClassVar[str] = "cera.continuous_deepseek_wire_draft.v7"

    schema_version: str
    story_segments: tuple[ContinuousDeepSeekStorySegmentDraftV1, ...]
    protected_user_realizations: tuple[ContinuousDeepSeekProtectedRealizationDraftV1, ...]

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("continuous DeepSeek wire draft schema changed")
        if not self.story_segments:
            raise ContractValidationError("continuous DeepSeek story segments are absent")
        segment_keys = tuple(value.segment_key for value in self.story_segments)
        if len(segment_keys) != len(set(segment_keys)):
            raise ContractValidationError("continuous DeepSeek story segments are duplicated")
        realization_keys = tuple(
            (value.claim_key, value.segment_key)
            for value in self.protected_user_realizations
        )
        if len(realization_keys) != len(set(realization_keys)):
            raise ContractValidationError(
                "continuous DeepSeek protected realizations are duplicated"
            )
        segment_map = {value.segment_key: value for value in self.story_segments}
        for realization in self.protected_user_realizations:
            segment = segment_map.get(realization.segment_key)
            if segment is None:
                raise ContractValidationError(
                    "continuous DeepSeek protected realization names an unknown segment"
                )
            if realization.claim_key not in segment.protected_user_source_claim_keys:
                raise ContractValidationError(
                    "continuous DeepSeek protected realization lacks segment claim binding"
                )

    def compile(self) -> "ContinuousDeepSeekDraftV1":
        story_parts: list[str] = []
        compiled_segments: list[StoryRealizationSegmentV1] = []
        segment_offsets: dict[str, int] = {}
        segment_map: dict[str, ContinuousDeepSeekStorySegmentDraftV1] = {}
        cursor = 0
        for segment in self.story_segments:
            if story_parts:
                story_parts.append("\n\n")
                cursor += 2
            segment_offsets[segment.segment_key] = cursor
            segment_map[segment.segment_key] = segment
            story_parts.append(segment.text)
            end = cursor + len(segment.text)
            compiled_segments.append(
                StoryRealizationSegmentV1(
                    schema_version=StoryRealizationSegmentV1.SCHEMA_VERSION,
                    segment_key=segment.segment_key,
                    kind=segment.compiled_kind(),
                    output_start=cursor,
                    output_end=end,
                    exact_text=segment.text,
                    roles=segment.compiled_roles(),
                    protected_user_source_claim_keys=(
                        segment.protected_user_source_claim_keys
                    ),
                )
            )
            cursor = end

        compiled_realizations: list[ProtectedUserRealizationSpanV1] = []
        for realization in self.protected_user_realizations:
            segment = segment_map[realization.segment_key]
            if segment.text.count(realization.exact_text) != 1:
                raise ContractValidationError(
                    "continuous DeepSeek protected realization text is absent or ambiguous"
                )
            start = segment_offsets[realization.segment_key] + segment.text.index(
                realization.exact_text
            )
            compiled_realizations.append(
                ProtectedUserRealizationSpanV1(
                    schema_version=ProtectedUserRealizationSpanV1.SCHEMA_VERSION,
                    claim_key=realization.claim_key,
                    kind=realization.kind,
                    output_start=start,
                    output_end=start + len(realization.exact_text),
                    exact_text=realization.exact_text,
                )
            )
        return ContinuousDeepSeekDraftV1(
            schema_version=ContinuousDeepSeekDraftV1.SCHEMA_VERSION,
            story_text="".join(story_parts),
            protected_user_realizations=tuple(compiled_realizations),
            story_segments=tuple(compiled_segments),
        )


@dataclass(frozen=True, slots=True)
class ContinuousDeepSeekDraftV1:
    """Python-compiled internal Composer result with authoritative exact offsets."""

    SCHEMA_VERSION: ClassVar[str] = "cera.continuous_deepseek_draft.v7"

    schema_version: str
    story_text: str
    protected_user_realizations: tuple[ProtectedUserRealizationSpanV1, ...]
    story_segments: tuple[StoryRealizationSegmentV1, ...]

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("continuous DeepSeek draft schema changed")
        if not self.story_text.strip() or len(self.story_text) > 256_000:
            raise ContractValidationError("continuous DeepSeek story text is invalid")
        spans = tuple(
            (value.output_start, value.output_end) for value in self.protected_user_realizations
        )
        if len(spans) != len(set(spans)):
            raise ContractValidationError(
                "continuous DeepSeek protected-user spans are duplicated"
            )
        if tuple(sorted(spans)) != spans:
            raise ContractValidationError(
                "continuous DeepSeek protected-user spans are out of order"
            )
        segment_spans = tuple(
            (value.output_start, value.output_end) for value in self.story_segments
        )
        if not segment_spans or tuple(sorted(segment_spans)) != segment_spans:
            raise ContractValidationError(
                "continuous DeepSeek story segments are absent or out of order"
            )
        if len(segment_spans) != len(set(segment_spans)):
            raise ContractValidationError(
                "continuous DeepSeek story segments are duplicated"
            )


def _schema_for(annotation: Any, *, field_name: str | None = None, owner: type | None = None) -> dict[str, Any]:
    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin in (Union, UnionType):
        return {"anyOf": [_schema_for(value) if value is not type(None) else {"type": "null"} for value in args]}
    if origin is tuple:
        item = args[0] if args else Any
        return {"type": "array", "items": _schema_for(item)}
    if isinstance(annotation, type) and issubclass(annotation, Enum):
        return {"type": "string", "enum": [value.value for value in annotation]}
    if isinstance(annotation, type) and is_dataclass(annotation):
        hints = get_type_hints(annotation)
        properties = {}
        required = []
        for value in fields(annotation):
            required.append(value.name)
            if value.name == "schema_version" and hasattr(annotation, "SCHEMA_VERSION"):
                properties[value.name] = {"type": "string", "const": annotation.SCHEMA_VERSION}
            else:
                properties[value.name] = _schema_for(
                    hints[value.name], field_name=value.name, owner=annotation
                )
        return {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        }
    if annotation is str:
        return {"type": "string"}
    if annotation is int:
        return {"type": "integer"}
    if annotation is float:
        return {"type": "number"}
    if annotation is bool:
        return {"type": "boolean"}
    raise ContractValidationError(f"unsupported continuous provider schema annotation {annotation!r}")


def _exclude_exact_string_pattern(value: str) -> str:
    """Return a portable anchored pattern matching every string except value."""

    if not isinstance(value, str) or not value:
        raise ContractValidationError(
            "provider schema exact-string exclusion requires a value"
        )
    length = len(value)
    alternatives = [f".{{0,{length - 1}}}", f".{{{length + 1},}}"]
    for index, character in enumerate(value):
        prefix = re.escape(value[:index])
        remaining = length - index - 1
        alternatives.append(
            f"{prefix}[^{re.escape(character)}].{{{remaining}}}"
        )
    return f"^({'|'.join(alternatives)})$"


def rich_planner_sequence_json_schema(
    *,
    protected_user_id: str | None = None,
    protected_user_source_claim_keys: tuple[str, ...] = (),
) -> dict[str, Any]:
    if protected_user_id is not None and (
        not isinstance(protected_user_id, str)
        or not protected_user_id.startswith("character:")
        or not protected_user_id.strip()
    ):
        raise ContractValidationError(
            "Planner provider schema protected-user identity is invalid"
        )
    if protected_user_source_claim_keys and protected_user_id is None:
        raise ContractValidationError(
            "Planner provider schema claims lack a protected-user identity"
        )
    if (
        not isinstance(protected_user_source_claim_keys, tuple)
        or len(protected_user_source_claim_keys)
        != len(set(protected_user_source_claim_keys))
        or any(
            not isinstance(value, str) or not value.strip()
            for value in protected_user_source_claim_keys
        )
    ):
        raise ContractValidationError(
            "Planner provider schema protected-user claims are invalid"
        )
    schema = _schema_for(RichPlannerSequenceV1)
    schema["properties"]["provisional"] = {"type": "boolean", "const": True}
    schema["properties"]["accepted_turn_id"] = {"type": "null", "const": None}
    schema["properties"]["selected_character_ids"]["minItems"] = 1
    schema["properties"]["beats"]["minItems"] = 1
    schema["properties"]["beats"]["maxItems"] = COMPACT_WRITER_BRIEF_MAX_BEATS
    beat = schema["properties"]["beats"]["items"]["properties"]
    beat["beat_key"]["pattern"] = LOCAL_KEY_JSON_PATTERN
    beat["source_evidence_bindings"]["items"]["pattern"] = LOCAL_KEY_JSON_PATTERN
    roles = beat["roles"]
    owner_fields = ("action_owner_ids", "state_owner_ids", "speaker_ids")
    if protected_user_id is not None and not protected_user_source_claim_keys:
        owner_pattern = _exclude_exact_string_pattern(protected_user_id)
        for field in owner_fields:
            roles["properties"][field]["items"]["pattern"] = owner_pattern
    owner_branches = []
    for field in owner_fields:
        branch = deepcopy(roles)
        branch["properties"][field]["minItems"] = 1
        owner_branches.append(branch)
    roles["anyOf"] = owner_branches
    allowance = beat["protected_user_allowance"]["properties"]
    allowance["source_binding_keys"]["items"]["pattern"] = LOCAL_KEY_JSON_PATTERN
    allowance["source_claim_keys"]["items"]["pattern"] = LOCAL_KEY_JSON_PATTERN
    allowance_schema = beat["protected_user_allowance"]
    allowance_base = deepcopy(allowance_schema)

    def allowance_branch(
        mode: ProtectedUserAllowanceMode,
        *,
        minimum_bindings: int,
        maximum_bindings: int | None,
        minimum_claims: int,
        maximum_claims: int,
    ) -> dict[str, Any]:
        branch = deepcopy(allowance_base)
        properties = branch["properties"]
        properties["mode"] = {"type": "string", "const": mode.value}
        bindings = properties["source_binding_keys"]
        if minimum_bindings:
            bindings["minItems"] = minimum_bindings
        if maximum_bindings is not None:
            bindings["maxItems"] = maximum_bindings
        claims = properties["source_claim_keys"]
        if minimum_claims:
            claims["minItems"] = minimum_claims
        claims["maxItems"] = maximum_claims
        return branch

    allowance_branches = [
        allowance_branch(
            ProtectedUserAllowanceMode.NONE,
            minimum_bindings=0,
            maximum_bindings=0,
            minimum_claims=0,
            maximum_claims=0,
        ),
        allowance_branch(
            ProtectedUserAllowanceMode.MINIMAL_NONBRANCHING_CONNECTIVE,
            minimum_bindings=1,
            maximum_bindings=None,
            minimum_claims=0,
            maximum_claims=0,
        ),
    ]
    if protected_user_source_claim_keys:
        exact = allowance_branch(
            ProtectedUserAllowanceMode.EXACT_SOURCE_ONLY,
            minimum_bindings=1,
            maximum_bindings=None,
            minimum_claims=1,
            maximum_claims=1,
        )
        exact["properties"]["source_claim_keys"]["items"]["enum"] = list(
            protected_user_source_claim_keys
        )
        allowance_branches.append(exact)
    allowance_schema["anyOf"] = allowance_branches
    return schema


def continuous_validator_draft_json_schema() -> dict[str, Any]:
    """Historical V8 Validator schema retained for frozen evidence."""

    return _schema_for(ContinuousValidatorDraftV1)


def continuous_deepseek_draft_json_schema() -> dict[str, Any]:
    """Historical V7 Composer schema retained for frozen evidence."""

    return _schema_for(ContinuousDeepSeekWireDraftV1)


def _constrain_active_python_hash_constants(schema: dict[str, Any]) -> None:
    """Constrain the one supplied policy hash in active Validator output."""

    properties = schema.get("properties")
    if isinstance(properties, dict):
        if "expected_prior_value_sha256" in properties:
            properties.pop("expected_prior_value_sha256")
            required = schema.get("required")
            if isinstance(required, list):
                schema["required"] = [
                    value
                    for value in required
                    if value != "expected_prior_value_sha256"
                ]
        if "persistence_policy_sha256" in properties:
            properties["persistence_policy_sha256"] = {
                "type": "string",
                "const": PERSISTENCE_POLICY_SHA256,
            }
        for value in properties.values():
            if isinstance(value, dict):
                _constrain_active_python_hash_constants(value)
    items = schema.get("items")
    if isinstance(items, dict):
        _constrain_active_python_hash_constants(items)
    for union_name in ("anyOf", "oneOf"):
        for value in schema.get(union_name, ()):
            if isinstance(value, dict):
                _constrain_active_python_hash_constants(value)


def _json_pointer_value(document: Any, pointer: str) -> Any:
    if not isinstance(pointer, str) or not pointer.startswith("/"):
        raise ContractValidationError(
            "persistence prior-value derivation requires a JSON pointer"
        )
    current = document
    for raw_token in pointer[1:].split("/"):
        token = raw_token.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict) and token in current:
            current = current[token]
        elif isinstance(current, list) and token.isdigit():
            index = int(token)
            if index >= len(current):
                raise ContractValidationError(
                    "persistence replace target does not exist"
                )
            current = current[index]
        else:
            raise ContractValidationError(
                "persistence replace target does not exist"
            )
    return current


def _inject_python_owned_persistence_hashes(
    payload: dict[str, Any],
    *,
    world_bridge: Any,
) -> dict[str, Any]:
    """Inject exact prior-value hashes omitted from the active provider wire."""

    value = deepcopy(payload)
    decision = value.get("decision")
    if not isinstance(decision, dict):
        return value
    sequence = decision.get("complete_final_sequence")
    if not isinstance(sequence, dict):
        return value
    for item in sequence.get("items", ()):
        if not isinstance(item, dict):
            continue
        for scope in item.get("field_scopes", ()):
            if not isinstance(scope, dict):
                continue
            for directive in scope.get("persistence_directives", ()):
                if not isinstance(directive, dict):
                    continue
                if "expected_prior_value_sha256" in directive:
                    raise ContractValidationError(
                        "provider authored Python-owned prior-value hash"
                    )
                operation = directive.get("operation")
                if operation == WorldEditOperationKind.ADD.value:
                    directive["expected_prior_value_sha256"] = None
                    continue
                if operation != WorldEditOperationKind.REPLACE.value:
                    raise ContractValidationError(
                        "persistence hash custody received an unsupported operation"
                    )
                dispatcher = getattr(world_bridge, "dispatcher", None)
                branch_root = getattr(dispatcher, "branch_root", None)
                if branch_root is None:
                    raise ContractValidationError(
                        "replace persistence hash derivation requires typed world authority"
                    )
                active_root = (branch_root / "ACTIVE").resolve()
                target_file = directive.get("target_file")
                if not isinstance(target_file, str):
                    raise ContractValidationError(
                        "persistence hash derivation requires an exact target file"
                    )
                target = (active_root / target_file).resolve()
                if not target.is_relative_to(active_root) or not target.is_file():
                    raise ContractValidationError(
                        "persistence hash derivation target is outside ACTIVE authority"
                    )
                document = json.loads(target.read_text(encoding="utf-8"))
                prior = _json_pointer_value(document, directive.get("field_path"))
                directive["expected_prior_value_sha256"] = canonical_sha256(prior)
    return value


_CHARACTER_ROLE_LEDGER_FIELDS = (
    "action_owner_ids",
    "state_owner_ids",
    "speaker_ids",
    "affected_ids",
    "addressed_ids",
    "observing_ids",
    "referenced_ids",
)


def _constrain_nonempty_character_role_ledgers(value: Any) -> None:
    if isinstance(value, dict):
        properties = value.get("properties")
        if (
            isinstance(properties, dict)
            and properties.get("schema_version", {}).get("const")
            == CharacterRoleLedgerV1.SCHEMA_VERSION
        ):
            value["anyOf"] = [
                {
                    "properties": {field_name: {"minItems": 1}},
                    "required": [field_name],
                }
                for field_name in _CHARACTER_ROLE_LEDGER_FIELDS
            ]
        for child in value.values():
            _constrain_nonempty_character_role_ledgers(child)
    elif isinstance(value, list):
        for child in value:
            _constrain_nonempty_character_role_ledgers(child)


def historical_continuous_semantic_validator_draft_v11_json_schema() -> dict[str, Any]:
    """Exact pre-Queue-0054 V11 projection retained for evidence replay."""

    schema = _schema_for(ContinuousSemanticValidatorDraftV11)
    for branch in schema["properties"]["decision"]["anyOf"]:
        properties = branch.get("properties", {})
        for collection in (
            "story_segments",
            "realization_segments",
            "protected_semantic_adjudications",
            "diagnostic_story_segments",
            "diagnostic_protected_semantic_adjudications",
        ):
            if collection in properties:
                properties[collection]["minItems"] = 1
        sequence = properties.get("complete_final_sequence")
        if sequence is not None:
            sequence["properties"]["items"]["minItems"] = 1
        review = properties.get("creator_review")
        if review is not None:
            review_properties = review["properties"]
            for field_name in ("creator_reason", "verifier_status"):
                review_properties[field_name]["minLength"] = 1
            if "primary_reason_code" in review_properties:
                review_properties["primary_reason_code"]["minLength"] = 1
        if "primary_reason_code" in properties:
            properties["primary_reason_code"]["minLength"] = 1
            properties["primary_reason_code"]["pattern"] = LOCAL_KEY_JSON_PATTERN
        additional = properties.get("additional_reason_codes")
        if additional is not None:
            additional["items"]["pattern"] = LOCAL_KEY_JSON_PATTERN
        violations = properties.get("writer_recall_violations")
        if violations is not None:
            violations["items"]["properties"]["prohibited_detail_classes"][
                "items"
            ]["enum"] = [
                value.value for value in ACTIVE_VALIDATOR_WRITER_HARD_CLASSES
            ]
    _constrain_active_python_hash_constants(schema)
    _constrain_nonempty_character_role_ledgers(schema)
    return schema


def historical_continuous_semantic_validator_draft_v12_json_schema() -> dict[str, Any]:
    """Exact pre-Queue-0055 V12 projection retained for evidence replay."""

    schema = _schema_for(ContinuousSemanticValidatorDraftV12)
    _constrain_active_validator_schema(schema)
    return schema


def _constrain_active_validator_schema(schema: dict[str, Any]) -> None:
    """Apply shared strict projection constraints to one active-style wire."""

    for branch in schema["properties"]["decision"]["anyOf"]:
        properties = branch.get("properties", {})
        for collection in (
            "story_segments",
            "realization_segments",
            "protected_semantic_adjudications",
            "diagnostic_story_segments",
            "diagnostic_protected_semantic_adjudications",
        ):
            if collection in properties:
                properties[collection]["minItems"] = 1
        sequence = properties.get("complete_final_sequence")
        if sequence is not None:
            sequence["properties"]["items"]["minItems"] = 1
        review = properties.get("creator_review")
        if review is not None:
            review_properties = review["properties"]
            for field_name in ("creator_reason", "verifier_status"):
                review_properties[field_name]["minLength"] = 1
            if "primary_reason_code" in review_properties:
                review_properties["primary_reason_code"]["minLength"] = 1
        if "primary_reason_code" in properties:
            properties["primary_reason_code"]["minLength"] = 1
            properties["primary_reason_code"]["pattern"] = LOCAL_KEY_JSON_PATTERN
        additional = properties.get("additional_reason_codes")
        if additional is not None:
            additional["items"]["pattern"] = LOCAL_KEY_JSON_PATTERN
        violations = properties.get("writer_recall_violations")
        if violations is not None:
            violations["items"]["properties"]["prohibited_detail_classes"][
                "items"
            ]["enum"] = [
                value.value for value in ACTIVE_VALIDATOR_WRITER_HARD_CLASSES
            ]
    _constrain_active_python_hash_constants(schema)


def continuous_semantic_validator_draft_json_schema() -> dict[str, Any]:
    """Active V13 single-authority finalization projection."""

    schema = _schema_for(ContinuousSemanticValidatorDraftV13)
    _constrain_active_validator_schema(schema)
    return schema


def continuous_compact_semantic_validator_draft_json_schema() -> dict[str, Any]:
    """Closed sparse Validator projection used only by an explicit adapter."""

    schema = _schema_for(ContinuousCompactSemanticValidatorDraftV1)
    for branch in schema["properties"]["decision"]["anyOf"]:
        properties = branch.get("properties", {})
        material = properties.get("material_segments")
        if material is not None:
            material["minItems"] = 1
        coverage = properties.get("beat_coverage")
        if coverage is not None:
            coverage["minItems"] = 1
            coverage["items"]["properties"]["material_segment_keys"][
                "minItems"
            ] = 1
        sequence = properties.get("complete_final_sequence")
        if sequence is not None:
            sequence["properties"]["items"]["minItems"] = 1
        diagnostics = properties.get("diagnostic_story_segments")
        if diagnostics is not None:
            diagnostics["minItems"] = 1
        offending = properties.get("offending_spans")
        if offending is not None:
            offending["minItems"] = 1
        additional = properties.get("additional_reason_codes")
        if additional is not None:
            additional["items"]["pattern"] = LOCAL_KEY_JSON_PATTERN
        violations = properties.get("writer_recall_violations")
        if violations is not None:
            violations["items"]["properties"]["prohibited_detail_classes"][
                "items"
            ]["enum"] = [
                value.value for value in ACTIVE_VALIDATOR_WRITER_HARD_CLASSES
            ]
    _constrain_active_python_hash_constants(schema)
    return schema


def continuous_compact_semantic_validator_draft_v2_json_schema() -> dict[str, Any]:
    """V2 compact projection with one sparse rejected violation receipt wire."""

    schema = _schema_for(ContinuousCompactSemanticValidatorDraftV2)
    for branch in schema["properties"]["decision"]["anyOf"]:
        properties = branch.get("properties", {})
        material = properties.get("material_segments")
        if material is not None:
            material["minItems"] = 1
        coverage = properties.get("beat_coverage")
        if coverage is not None:
            coverage["minItems"] = 1
            coverage["items"]["properties"]["material_segment_keys"][
                "minItems"
            ] = 1
        sequence = properties.get("complete_final_sequence")
        if sequence is not None:
            sequence["properties"]["items"]["minItems"] = 1
        violations = properties.get("violations")
        if violations is not None:
            violations["minItems"] = 1
            violations["items"]["properties"]["violation_class"]["enum"] = [
                value.value for value in ACTIVE_VALIDATOR_WRITER_HARD_CLASSES
            ]
        offending = properties.get("offending_spans")
        if offending is not None:
            # Historical V1 diagnostics remain decodable only for unresolved
            # compact outcomes.  A Writer-attributable rejection must use V2.
            properties["semantic_status"]["enum"] = ["inconclusive", "error"]
            offending["minItems"] = 1
        additional = properties.get("additional_reason_codes")
        if additional is not None:
            additional["items"]["pattern"] = LOCAL_KEY_JSON_PATTERN
        recall_violations = properties.get("writer_recall_violations")
        if recall_violations is not None:
            recall_violations["items"]["properties"][
                "prohibited_detail_classes"
            ]["items"]["enum"] = [
                value.value for value in ACTIVE_VALIDATOR_WRITER_HARD_CLASSES
            ]
    _constrain_active_python_hash_constants(schema)
    return schema


def continuous_scene_writer_draft_json_schema() -> dict[str, Any]:
    return _schema_for(ContinuousSceneWriterDraftV1)


def continuous_reader_verdict_json_schema() -> dict[str, Any]:
    schema = _schema_for(ProviderReaderVerdictDraftV2)
    for branch in schema["properties"]["decision"]["anyOf"]:
        properties = branch["properties"]
        reasons = properties.get("reason_codes")
        if reasons is not None:
            reasons["minItems"] = 1
            reasons["items"]["pattern"] = LOCAL_KEY_JSON_PATTERN
        issues = properties.get("issues")
        if issues is not None:
            issues["minItems"] = 1
            issues["items"]["properties"]["issue_code"][
                "pattern"
            ] = LOCAL_KEY_JSON_PATTERN
    return schema


def historical_provider_reader_verdict_json_schema() -> dict[str, Any]:
    """Flat provider Reader V1 schema retained for immutable evidence replay."""

    return _schema_for(ProviderReaderVerdictDraftV1)


def historical_reader_verdict_json_schema() -> dict[str, Any]:
    """Canonical V1 Reader schema retained for immutable historical evidence."""

    return _schema_for(ReaderVerdictV1)


@dataclass(frozen=True, slots=True)
class ContinuousProviderResultV1:
    value: Any
    provider_receipt: Any
    operation_telemetry: Any
    tool_call_count: int
    failed_tool_call_count: int
    world_tool_debug: Any = None
    physical_session_sha256: str | None = None


def _transport_external_provider_boundary(transport: object) -> bool:
    """Classify provider-shaped transports through the fail-closed marker."""

    return is_external_provider_boundary(transport)


class CodexContinuousPlannerPort:
    def __init__(
        self,
        transport: CodexSDKTransport,
        *,
        world_bridge: Any = None,
        call_ledger: ContinuousProviderCallLedger,
    ) -> None:
        if transport.route.model_name != "gpt-5.6-sol":
            raise ContractValidationError("continuous Planner requires Sol")
        self.transport = transport
        self.world_bridge = world_bridge
        self.call_ledger = call_ledger
        self._operation_index = 0

    def plan(
        self,
        prompt: str,
        *,
        protected_user_id: str | None = None,
        protected_user_source_claim_keys: tuple[str, ...] = (),
    ) -> ContinuousProviderResultV1:
        assert_provider_dispatch_allowed(
            "continuous.planner.plan",
            external_provider_boundary=_transport_external_provider_boundary(
                self.transport
            ),
        )
        self._operation_index += 1
        route = self.transport.route
        # All local schema and MCP construction completes before the provider
        # ledger records a transport invocation.
        output_schema = rich_planner_sequence_json_schema(
            protected_user_id=protected_user_id,
            protected_user_source_claim_keys=protected_user_source_claim_keys,
        )
        mcp_binding = (
            self.world_bridge.runtime_binding if self.world_bridge is not None else None
        )
        stored_thread_sha256 = _transport_stored_thread_sha256(self.transport)

        def dispatch(markers):
            return self.transport.invoke(
                prompt,
                output_schema=output_schema,
                mcp_binding=mcp_binding,
                on_worker_started=markers.mark_worker_started,
                on_worker_preflight=markers.mark_worker_preflight,
                on_transport_invoke=markers.mark_transport_invoked,
            )

        def finalize(result):
            world_tool_debug = (
                self.world_bridge.finalize(result) if self.world_bridge is not None else None
            )
            value = from_mapping(RichPlannerSequenceV1, result.parsed_json or {})
            if not value.provisional or value.accepted_turn_id is not None:
                raise ContractValidationError("Planner provider result must remain provisional")
            return ContinuousProviderResultV1(
                value=value,
                provider_receipt=result.receipt,
                operation_telemetry=result.operation_telemetry,
                tool_call_count=result.tool_call_count,
                failed_tool_call_count=result.failed_tool_call_count,
                world_tool_debug=world_tool_debug,
                physical_session_sha256=stored_thread_sha256,
            )

        return self.call_ledger.execute(
            owner="planner",
            operation=f"plan_{self._operation_index:04d}",
            route=route.route_id,
            model=route.model_name,
            effort=route.reasoning_effort,
            dispatch_with_stage_markers=dispatch,
            finalize=finalize,
            stored_thread_sha256=stored_thread_sha256,
        )


class CodexContinuousValidatorPort:
    def __init__(
        self,
        transport: CodexSDKTransport,
        *,
        world_bridge: Any = None,
        call_ledger: ContinuousProviderCallLedger,
        raw_result_observer: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        route = transport.route
        allowed = {
            ("gpt-5.6-sol", "medium"),
            ("gpt-5.6-terra", "high"),
        }
        if (route.model_name, route.reasoning_effort) not in allowed:
            raise ContractValidationError("continuous Validator route is unsupported")
        self.transport = transport
        self.world_bridge = world_bridge
        self.call_ledger = call_ledger
        self.raw_result_observer = raw_result_observer
        self._operation_index = 0

    def validate(
        self,
        prompt: str,
        *,
        writer_story_text: str | None,
        accepted_pairs: tuple[AcceptedTurnPairV1, ...] = (),
        expected_package_id: str | None = None,
        expected_world_id: str | None = None,
        expected_branch_id: str | None = None,
    ) -> ContinuousProviderResultV1:
        assert_provider_dispatch_allowed(
            "continuous.validator.validate",
            external_provider_boundary=_transport_external_provider_boundary(
                self.transport
            ),
        )
        self._operation_index += 1
        route = self.transport.route
        output_schema = continuous_semantic_validator_draft_json_schema()
        expected_identities = {
            "package_id": expected_package_id,
            "world_id": expected_world_id,
            "branch_id": expected_branch_id,
        }
        supplied_identities = tuple(
            value is not None for value in expected_identities.values()
        )
        if any(supplied_identities) and not all(supplied_identities):
            raise ContractValidationError(
                "continuous Validator expected identities are incomplete"
            )
        if all(supplied_identities):
            for field_name, expected_value in expected_identities.items():
                if not isinstance(expected_value, str) or not expected_value.strip():
                    raise ContractValidationError(
                        "continuous Validator expected identity is invalid"
                    )
                output_schema["properties"][field_name]["const"] = expected_value
        mcp_binding = (
            self.world_bridge.runtime_binding if self.world_bridge is not None else None
        )
        stored_thread_sha256 = _transport_stored_thread_sha256(self.transport)

        def dispatch(markers):
            return self.transport.invoke(
                prompt,
                output_schema=output_schema,
                mcp_binding=mcp_binding,
                on_worker_started=markers.mark_worker_started,
                on_worker_preflight=markers.mark_worker_preflight,
                on_transport_invoke=markers.mark_transport_invoked,
            )

        def finalize(result):
            raw_provider_json = result.parsed_json or {}
            if self.raw_result_observer is not None:
                self.raw_result_observer(deepcopy(raw_provider_json))
            if all(supplied_identities) and any(
                raw_provider_json.get(field_name) != expected_value
                for field_name, expected_value in expected_identities.items()
            ):
                raise ContractValidationError(
                    "continuous Validator provider identity changed"
                )
            world_tool_debug = (
                self.world_bridge.finalize(result) if self.world_bridge is not None else None
            )
            canonical_payload = _inject_python_owned_persistence_hashes(
                raw_provider_json,
                world_bridge=self.world_bridge,
            )
            schema_version = canonical_payload.get("schema_version")
            if schema_version == ContinuousSemanticValidatorDraftV8.SCHEMA_VERSION:
                historical_draft = from_mapping(
                    ContinuousSemanticValidatorDraftV8,
                    canonical_payload,
                )
                value = ContinuousSemanticValidatorResultV3.from_v2(
                    historical_draft.compile(
                        writer_story_text=writer_story_text,
                        accepted_pairs=accepted_pairs,
                    )
                )
            elif schema_version == ContinuousSemanticValidatorDraftV9.SCHEMA_VERSION:
                draft = from_mapping(
                    ContinuousSemanticValidatorDraftV9,
                    canonical_payload,
                )
                value = draft.compile(
                    writer_story_text=writer_story_text,
                    accepted_pairs=accepted_pairs,
                )
            elif schema_version == ContinuousSemanticValidatorDraftV10.SCHEMA_VERSION:
                draft = from_mapping(
                    ContinuousSemanticValidatorDraftV10,
                    canonical_payload,
                )
                value = draft.compile(
                    writer_story_text=writer_story_text,
                    accepted_pairs=accepted_pairs,
                )
            elif schema_version == ContinuousSemanticValidatorDraftV11.SCHEMA_VERSION:
                draft = from_mapping(
                    ContinuousSemanticValidatorDraftV11,
                    canonical_payload,
                )
                value = draft.compile(
                    writer_story_text=writer_story_text,
                    accepted_pairs=accepted_pairs,
                )
            elif schema_version == ContinuousSemanticValidatorDraftV12.SCHEMA_VERSION:
                draft = from_mapping(
                    ContinuousSemanticValidatorDraftV12,
                    canonical_payload,
                )
                value = draft.compile(
                    writer_story_text=writer_story_text,
                    accepted_pairs=accepted_pairs,
                )
            else:
                draft = from_mapping(
                    ContinuousSemanticValidatorDraftV13,
                    canonical_payload,
                )
                value = draft.compile(
                    writer_story_text=writer_story_text,
                    accepted_pairs=accepted_pairs,
                )
            return ContinuousProviderResultV1(
                value=value,
                provider_receipt=result.receipt,
                operation_telemetry=result.operation_telemetry,
                tool_call_count=result.tool_call_count,
                failed_tool_call_count=result.failed_tool_call_count,
                world_tool_debug=world_tool_debug,
                physical_session_sha256=stored_thread_sha256,
            )

        return self.call_ledger.execute(
            owner="validator",
            operation=f"validate_{self._operation_index:04d}",
            route=route.route_id,
            model=route.model_name,
            effort=route.reasoning_effort,
            dispatch_with_stage_markers=dispatch,
            finalize=finalize,
            stored_thread_sha256=stored_thread_sha256,
        )


class CodexContinuousCompactValidatorPort:
    """Opt-in sparse Validator adapter; the exhaustive adapter is unchanged."""

    uses_compact_turn_contract = True
    compact_contract_profile = "compact_v1"
    request_marker = "[COMPACT VALIDATOR REQUEST]"
    draft_type = ContinuousCompactSemanticValidatorDraftV1
    output_schema_builder = staticmethod(
        continuous_compact_semantic_validator_draft_json_schema
    )
    operation_prefix = "compact_validate"

    def __init__(
        self,
        transport: CodexSDKTransport,
        *,
        world_bridge: Any = None,
        call_ledger: ContinuousProviderCallLedger,
        raw_result_observer: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        route = transport.route
        if (route.model_name, route.reasoning_effort) != (
            "gpt-5.6-sol",
            "medium",
        ):
            raise ContractValidationError(
                "compact continuous Validator requires Sol medium"
            )
        self.transport = transport
        self.world_bridge = world_bridge
        self.call_ledger = call_ledger
        self.raw_result_observer = raw_result_observer
        self._operation_index = 0

    @staticmethod
    def _planner_beat_keys_from_prompt(prompt: str) -> tuple[str, ...]:
        marker = CodexContinuousCompactValidatorPort.request_marker
        if marker not in prompt:
            raise ContractValidationError(
                "compact Validator request marker is missing"
            )
        try:
            request = json.loads(prompt.split(marker, 1)[1].strip())
            beats = request["planner_sequence"]["beats"]
            keys = tuple(value["beat_key"] for value in beats)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ContractValidationError(
                "compact Validator Planner beat custody is invalid"
            ) from exc
        if not keys or any(
            not isinstance(value, str) or not value for value in keys
        ):
            raise ContractValidationError(
                "compact Validator Planner beat custody is empty"
            )
        return keys

    def validate(
        self,
        prompt: str,
        *,
        writer_story_text: str | None,
        accepted_pairs: tuple[AcceptedTurnPairV1, ...] = (),
        expected_package_id: str | None = None,
        expected_world_id: str | None = None,
        expected_branch_id: str | None = None,
        expected_planner_beat_keys: tuple[str, ...] | None = None,
    ) -> ContinuousProviderResultV1:
        assert_provider_dispatch_allowed(
            "continuous.compact_validator.validate",
            external_provider_boundary=_transport_external_provider_boundary(
                self.transport
            ),
        )
        self._operation_index += 1
        route = self.transport.route
        output_schema = self.output_schema_builder()
        expected_identities = {
            "package_id": expected_package_id,
            "world_id": expected_world_id,
            "branch_id": expected_branch_id,
        }
        supplied = tuple(value is not None for value in expected_identities.values())
        if any(supplied) and not all(supplied):
            raise ContractValidationError(
                "compact Validator expected identities are incomplete"
            )
        if all(supplied):
            for field_name, expected_value in expected_identities.items():
                if not isinstance(expected_value, str) or not expected_value.strip():
                    raise ContractValidationError(
                        "compact Validator expected identity is invalid"
                    )
                output_schema["properties"][field_name]["const"] = expected_value
        beat_keys = (
            expected_planner_beat_keys
            if expected_planner_beat_keys is not None
            else self._planner_beat_keys_from_prompt(prompt)
        )
        mcp_binding = (
            self.world_bridge.runtime_binding if self.world_bridge is not None else None
        )
        stored_thread_sha256 = _transport_stored_thread_sha256(self.transport)

        def dispatch(markers):
            return self.transport.invoke(
                prompt,
                output_schema=output_schema,
                mcp_binding=mcp_binding,
                on_worker_started=markers.mark_worker_started,
                on_worker_preflight=markers.mark_worker_preflight,
                on_transport_invoke=markers.mark_transport_invoked,
            )

        def finalize(result):
            raw_provider_json = result.parsed_json or {}
            if self.raw_result_observer is not None:
                self.raw_result_observer(deepcopy(raw_provider_json))
            if all(supplied) and any(
                raw_provider_json.get(field_name) != expected_value
                for field_name, expected_value in expected_identities.items()
            ):
                raise ContractValidationError(
                    "compact Validator provider identity changed"
                )
            world_tool_debug = (
                self.world_bridge.finalize(result) if self.world_bridge is not None else None
            )
            draft = from_mapping(
                self.draft_type,
                raw_provider_json,
            )
            value = draft.compile(
                writer_story_text=writer_story_text,
                expected_planner_beat_keys=beat_keys,
                accepted_pairs=accepted_pairs,
            )
            return ContinuousProviderResultV1(
                value=value,
                provider_receipt=result.receipt,
                operation_telemetry=result.operation_telemetry,
                tool_call_count=result.tool_call_count,
                failed_tool_call_count=result.failed_tool_call_count,
                world_tool_debug=world_tool_debug,
                physical_session_sha256=stored_thread_sha256,
            )

        return self.call_ledger.execute(
            owner="validator",
            operation=f"{self.operation_prefix}_{self._operation_index:04d}",
            route=route.route_id,
            model=route.model_name,
            effort=route.reasoning_effort,
            dispatch_with_stage_markers=dispatch,
            finalize=finalize,
            stored_thread_sha256=stored_thread_sha256,
        )


class CodexContinuousCompactValidatorPortV2(CodexContinuousCompactValidatorPort):
    """Opt-in V2 adapter with rejected-only violation receipts."""

    compact_contract_profile = "compact_v2"
    request_marker = "[COMPACT VALIDATOR V2 REQUEST]"
    draft_type = ContinuousCompactSemanticValidatorDraftV2
    output_schema_builder = staticmethod(
        continuous_compact_semantic_validator_draft_v2_json_schema
    )
    operation_prefix = "compact_v2_validate"

    @classmethod
    def _planner_beat_keys_from_prompt(cls, prompt: str) -> tuple[str, ...]:
        marker = cls.request_marker
        if marker not in prompt:
            raise ContractValidationError(
                "compact Validator V2 request marker is missing"
            )
        try:
            request = json.loads(prompt.split(marker, 1)[1].strip())
            beats = request["planner_sequence"]["beats"]
            keys = tuple(value["beat_key"] for value in beats)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ContractValidationError(
                "compact Validator V2 Planner beat custody is invalid"
            ) from exc
        if not keys or any(
            not isinstance(value, str) or not value for value in keys
        ):
            raise ContractValidationError(
                "compact Validator V2 Planner beat custody is empty"
            )
        return keys


class CodexContinuousCompactValidatorPortV3(CodexContinuousCompactValidatorPortV2):
    """Prompt-v3 adapter; the compact V2 schema and receipt DTO are unchanged."""

    compact_contract_profile = "compact_v3"
    request_marker = "[COMPACT VALIDATOR V3 REQUEST]"
    operation_prefix = "compact_v3_validate"


class CodexContinuousCompactValidatorPortV4(CodexContinuousCompactValidatorPortV3):
    """Prompt-v4 adapter; compact V2 schema and receipt DTO remain unchanged."""

    compact_contract_profile = "compact_v4"
    request_marker = "[COMPACT VALIDATOR V4 REQUEST]"
    operation_prefix = "compact_v4_validate"


class CodexContinuousReaderPort:
    """Candidate-specific Codex Reader with no rewrite or persistence channel."""

    def __init__(
        self,
        transport_factory: Callable[[], CodexSDKTransport],
        *,
        call_ledger: ContinuousProviderCallLedger,
        raw_result_observer: Callable[[dict[str, Any]], None] | None = None,
        external_provider_boundary: bool | None = None,
    ) -> None:
        self.transport_factory = transport_factory
        self.external_provider_boundary = (
            is_external_provider_boundary(transport_factory)
            if external_provider_boundary is None
            else external_provider_boundary
        )
        if type(self.external_provider_boundary) is not bool:
            raise ContractValidationError("Reader boundary marker must be boolean")
        self.call_ledger = call_ledger
        self.raw_result_observer = raw_result_observer
        self._operation_index = 0

    def review(
        self,
        prompt: str,
        *,
        writer_story_text: str,
    ) -> ContinuousProviderResultV1:
        assert_provider_dispatch_allowed(
            "continuous.reader.transport_factory",
            external_provider_boundary=self.external_provider_boundary,
        )
        transport = self.transport_factory()
        assert_provider_dispatch_allowed(
            "continuous.reader.review",
            external_provider_boundary=_transport_external_provider_boundary(transport),
        )
        self._operation_index += 1
        route = transport.route
        allowed = {
            ("gpt-5.6-sol", "medium"),
            ("gpt-5.6-terra", "high"),
        }
        if (route.model_name, route.reasoning_effort) not in allowed:
            raise ContractValidationError("continuous Reader route is unsupported")
        output_schema = continuous_reader_verdict_json_schema()
        stored_thread_sha256 = _transport_stored_thread_sha256(transport)

        def dispatch(markers):
            return transport.invoke(
                prompt,
                output_schema=output_schema,
                mcp_binding=None,
                on_worker_started=markers.mark_worker_started,
                on_worker_preflight=markers.mark_worker_preflight,
                on_transport_invoke=markers.mark_transport_invoked,
            )

        def finalize(result):
            raw_provider_json = result.parsed_json or {}
            if self.raw_result_observer is not None:
                self.raw_result_observer(deepcopy(raw_provider_json))
            draft = from_mapping(ProviderReaderVerdictDraftV2, raw_provider_json)
            value = draft.compile(writer_story_text=writer_story_text)
            return ContinuousProviderResultV1(
                value=value,
                provider_receipt=result.receipt,
                operation_telemetry=result.operation_telemetry,
                tool_call_count=result.tool_call_count,
                failed_tool_call_count=result.failed_tool_call_count,
                world_tool_debug=None,
                physical_session_sha256=stored_thread_sha256,
            )

        return self.call_ledger.execute(
            owner="reader",
            operation=f"review_{self._operation_index:04d}",
            route=route.route_id,
            model=route.model_name,
            effort=route.reasoning_effort,
            dispatch_with_stage_markers=dispatch,
            finalize=finalize,
            stored_thread_sha256=stored_thread_sha256,
        )


class DeepSeekContinuousComposerPort:
    def __init__(
        self,
        transport: DeepSeekChatTransport,
        *,
        call_ledger: ContinuousProviderCallLedger,
    ) -> None:
        if transport.route.model_name not in {
            "deepseek-v4-flash",
            "deepseek-v4-pro",
        }:
            raise ContractValidationError(
                "continuous Composer requires an authorized DeepSeek V4 route"
            )
        self.transport = transport
        self.call_ledger = call_ledger
        self._operation_index = 0

    @property
    def external_provider_boundary(self) -> bool:
        return _transport_external_provider_boundary(self.transport)

    def compose(
        self,
        prompt: str,
        *,
        system_prompt: str | None = None,
        operation_evidence: ProviderOperationEvidenceStoreV1 | None = None,
        operation_evidence_attempt: int = 1,
        operation_evidence_prompt_version: str | None = None,
        operation_evidence_schema_version: str | None = None,
    ) -> ContinuousProviderResultV1:
        assert_provider_dispatch_allowed(
            "continuous.composer.compose",
            external_provider_boundary=_transport_external_provider_boundary(
                self.transport
            ),
        )
        schema = continuous_scene_writer_draft_json_schema()
        messages = (
            DeepSeekMessage(
                "system",
                system_prompt or "You are CERA's stateless Scene Writer. Realize the validated Planner sequence as one fresh complete presentation-neutral story response. Preserve the required causal beats, character boundaries, exact creator-source constraints, and stopping point. Write natural prose with dialogue, compatible transient gesture and staging, pacing, atmosphere, imagery, rhythm, and only the allowed character interiority. Never invent continuity-relevant objects, tasks, events, relocation, material state, relationship or memory facts, or protected-user behavior. Any recall directive is non-authoritative feedback about a rejected candidate; do not continue, patch, merge, or treat it as story context. Return exactly one JSON object containing schema_version and story_text. Do not return analysis, semantic labels, roles, owners, claim keys, consent judgments, offsets, hashes, coverage, events, memory, persistence, or acceptance decisions. Do not certify or explain your own prose. Thinking is disabled.",
            ),
            DeepSeekMessage(
                "user",
                prompt
                + "\n\n[RESPONSE SCHEMA]\n"
                + json.dumps(schema, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            ),
        )
        self._operation_index += 1
        route = self.transport.route

        def dispatch(mark_transport_invoked):
            return self.transport.invoke(
                messages,
                output_mode=ProviderOutputMode.JSON_OBJECT,
                thinking_enabled=False,
                on_transport_invoke=mark_transport_invoked,
            )

        def finalize(result):
            value = from_mapping(
                ContinuousSceneWriterDraftV1,
                result.parsed_json or {},
            )
            return ContinuousProviderResultV1(
                value=value,
                provider_receipt=result.receipt,
                operation_telemetry=None,
                tool_call_count=0,
                failed_tool_call_count=0,
                world_tool_debug=None,
                physical_session_sha256=None,
            )

        return self.call_ledger.execute(
            owner="composer",
            operation=f"compose_{self._operation_index:04d}",
            route=route.route_id,
            model=route.model_name,
            effort=route.reasoning_effort,
            dispatch_with_invocation_marker=dispatch,
            finalize=finalize,
            operation_evidence=(
                None
                if operation_evidence is None
                else operation_evidence.request(
                    request_bytes=canonical_bytes(to_primitive(messages)),
                    structured_output_schema=schema,
                    prompt_version=(
                        operation_evidence_prompt_version
                        or route.prompt_version
                    ),
                    schema_version=operation_evidence_schema_version,
                    operation_workspace="not_applicable:stateless_https",
                    role="writer",
                    attempt=operation_evidence_attempt,
                    archival_policy="not_applicable_stateless",
                )
            ),
        )


def continuous_planner_route(*, effort: str = "medium"):
    return replace(
        codex_reasoner_candidate(model="gpt-5.6-sol", effort=effort),
        adapter_id=CONTINUOUS_PLANNER_ADAPTER_VERSION,
        prompt_version=CONTINUOUS_PLANNER_PROMPT_VERSION,
        maximum_output_tokens=32_768,
    )


def continuous_validator_route(*, model: str, effort: str):
    return replace(
        codex_realization_verifier_candidate(model=model, effort=effort),
        adapter_id=CONTINUOUS_VALIDATOR_ADAPTER_VERSION,
        prompt_version=CONTINUOUS_VALIDATOR_PROMPT_VERSION,
        timeout_seconds=480,
        maximum_output_tokens=32_768,
    )


def continuous_compact_validator_route(*, model: str, effort: str):
    return replace(
        codex_realization_verifier_candidate(model=model, effort=effort),
        adapter_id=CONTINUOUS_COMPACT_VALIDATOR_ADAPTER_VERSION,
        prompt_version=CONTINUOUS_COMPACT_VALIDATOR_PROMPT_VERSION,
        timeout_seconds=480,
        maximum_output_tokens=16_384,
    )


def continuous_compact_validator_v2_route(*, model: str, effort: str):
    return replace(
        codex_realization_verifier_candidate(model=model, effort=effort),
        adapter_id=CONTINUOUS_COMPACT_VALIDATOR_ADAPTER_VERSION_V2,
        prompt_version=CONTINUOUS_COMPACT_VALIDATOR_PROMPT_VERSION_V2,
        timeout_seconds=480,
        maximum_output_tokens=16_384,
    )


def continuous_compact_validator_v3_route(*, model: str, effort: str):
    return replace(
        codex_realization_verifier_candidate(model=model, effort=effort),
        adapter_id=CONTINUOUS_COMPACT_VALIDATOR_ADAPTER_VERSION_V3,
        prompt_version=CONTINUOUS_COMPACT_VALIDATOR_PROMPT_VERSION_V3,
        timeout_seconds=480,
        maximum_output_tokens=16_384,
    )


def continuous_compact_validator_v4_route(*, model: str, effort: str):
    return replace(
        codex_realization_verifier_candidate(model=model, effort=effort),
        adapter_id=CONTINUOUS_COMPACT_VALIDATOR_ADAPTER_VERSION_V4,
        prompt_version=CONTINUOUS_COMPACT_VALIDATOR_PROMPT_VERSION_V4,
        timeout_seconds=480,
        maximum_output_tokens=16_384,
    )


def continuous_deepseek_route(*, model: str = "deepseek-v4-flash"):
    if model not in {"deepseek-v4-flash", "deepseek-v4-pro"}:
        raise ContractValidationError("continuous DeepSeek route is unsupported")
    return replace(
        deepseek_composer_candidate(model=model),
        adapter_id=CONTINUOUS_DEEPSEEK_ADAPTER_VERSION,
        prompt_version=CONTINUOUS_DEEPSEEK_PROMPT_VERSION,
        maximum_output_tokens=32_768,
    )


def continuous_reader_route(*, model: str, effort: str):
    return replace(
        codex_realization_verifier_candidate(model=model, effort=effort),
        adapter_id=CONTINUOUS_READER_ADAPTER_VERSION,
        prompt_version=CONTINUOUS_READER_PROMPT_VERSION,
        maximum_output_tokens=16_384,
    )


def _transport_stored_thread_sha256(transport: CodexSDKTransport) -> str:
    current: Any = transport
    seen: set[int] = set()
    for _ in range(8):
        identity = id(current)
        if identity in seen:
            break
        seen.add(identity)
        runner = getattr(current, "runner", None)
        provider_thread_id = getattr(runner, "provider_thread_id", None)
        if isinstance(provider_thread_id, str) and provider_thread_id.strip():
            return text_sha256(provider_thread_id)
        current = getattr(current, "transport", None)
        if current is None:
            break
    raise ContractValidationError(
        "continuous Codex transport lacks a bound stored-thread identity"
    )
