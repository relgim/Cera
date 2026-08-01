"""Provider DTOs and one-shot adapters for the shadow continuous route."""

from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass, replace
from enum import Enum
import json
from types import UnionType
from typing import Any, ClassVar, Union, get_args, get_origin, get_type_hints

from cera.creator_review.models import (
    CreatorReviewAssessment,
    CreatorReviewSeverity,
    PublicationEligibility,
    ReviewIssueOwner,
)
from cera.errors import ContractValidationError
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
from cera.serialization import re_is_sha256

from .contracts import (
    AcceptedTurnPairV1,
    CreatedFieldLogEntryV1,
    EventRecordCandidateV1,
    FinalSequenceItemV1,
    FinalSequenceV1,
    RichPlannerSequenceV1,
    SceneSummaryV1,
    ValidatorFinalizationPackageV1,
    ValidatorSemanticStatus,
    ValidatorTaskMode,
    WorldEditOperationKind,
    WorldEditOperationV1,
    json_value_type,
)
from .prompting import (
    CONTINUOUS_PLANNER_PROMPT_VERSION,
    CONTINUOUS_VALIDATOR_PROMPT_VERSION,
)


CONTINUOUS_PLANNER_ADAPTER_VERSION = "cera.continuous_planner_adapter.v1"
CONTINUOUS_VALIDATOR_ADAPTER_VERSION = "cera.continuous_validator_adapter.v1"
CONTINUOUS_DEEPSEEK_ADAPTER_VERSION = "cera.continuous_deepseek_adapter.v1"
CONTINUOUS_DEEPSEEK_PROMPT_VERSION = "cera.continuous_deepseek_prompt.v1"


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


@dataclass(frozen=True, slots=True)
class ProviderCreatedFieldLogEntryV1:
    target_file: str
    field_path: str
    value_type: str
    value_json: str
    reason: str
    source_final_sequence_item: str


@dataclass(frozen=True, slots=True)
class ProviderSceneSummaryDraftV1:
    summary_id: str
    completed_scene_id: str
    accepted_turn_ids: tuple[str, ...]
    shortest_complete_summary: str
    ending_state: str
    transition_context: str


@dataclass(frozen=True, slots=True)
class ContinuousValidatorDraftV1:
    SCHEMA_VERSION: ClassVar[str] = "cera.continuous_validator_draft.v1"

    schema_version: str
    package_id: str
    world_id: str
    branch_id: str
    task_mode: ValidatorTaskMode
    semantic_status: ValidatorSemanticStatus
    complete_final_sequence: FinalSequenceV1 | None
    creator_review: CreatorReviewAssessment | None
    world_edit_operations: tuple[ProviderWorldEditOperationV1, ...]
    created_field_log: tuple[ProviderCreatedFieldLogEntryV1, ...]
    event_record: EventRecordCandidateV1 | None
    optional_scene_summary: ProviderSceneSummaryDraftV1 | None

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("continuous Validator provider schema changed")
        if len(self.world_edit_operations) > ValidatorFinalizationPackageV1.MAX_EDIT_OPERATIONS:
            raise ContractValidationError("continuous Validator edit ceiling exceeded")

    def compile(
        self, *, accepted_pairs: tuple[AcceptedTurnPairV1, ...] = ()
    ) -> ValidatorFinalizationPackageV1:
        operations = []
        for value in self.world_edit_operations:
            try:
                decoded = json.loads(value.value_json)
            except json.JSONDecodeError as exc:
                raise ContractValidationError("Validator edit value_json is malformed") from exc
            operations.append(
                WorldEditOperationV1(
                    operation_key=value.operation_key,
                    target_file=value.target_file,
                    expected_file_revision=value.expected_file_revision,
                    operation=value.operation,
                    field_path=value.field_path,
                    value=decoded,
                    reason=value.reason,
                    source_final_sequence_item=value.source_final_sequence_item,
                )
            )
        created = []
        for value in self.created_field_log:
            try:
                decoded = json.loads(value.value_json)
            except json.JSONDecodeError as exc:
                raise ContractValidationError("Validator created-field value_json is malformed") from exc
            if json_value_type(decoded) != value.value_type:
                raise ContractValidationError("Validator created-field value type changed")
            created.append(
                CreatedFieldLogEntryV1(
                    target_file=value.target_file,
                    field_path=value.field_path,
                    value_type=value.value_type,
                    value=decoded,
                    reason=value.reason,
                    source_final_sequence_item=value.source_final_sequence_item,
                )
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
            event_record=self.event_record,
            optional_scene_summary=scene_summary,
        )


@dataclass(frozen=True, slots=True)
class ContinuousDeepSeekDraftV1:
    SCHEMA_VERSION: ClassVar[str] = "cera.continuous_deepseek_draft.v1"

    schema_version: str
    story_text: str

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("continuous DeepSeek draft schema changed")
        if not self.story_text.strip() or len(self.story_text) > 256_000:
            raise ContractValidationError("continuous DeepSeek story text is invalid")


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


def rich_planner_sequence_json_schema() -> dict[str, Any]:
    return _schema_for(RichPlannerSequenceV1)


def continuous_validator_draft_json_schema() -> dict[str, Any]:
    return _schema_for(ContinuousValidatorDraftV1)


def continuous_deepseek_draft_json_schema() -> dict[str, Any]:
    return _schema_for(ContinuousDeepSeekDraftV1)


@dataclass(frozen=True, slots=True)
class ContinuousProviderResultV1:
    value: Any
    provider_receipt: Any
    operation_telemetry: Any
    tool_call_count: int
    failed_tool_call_count: int
    world_tool_debug: Any = None


class CodexContinuousPlannerPort:
    def __init__(self, transport: CodexSDKTransport, *, world_bridge: Any = None) -> None:
        if transport.route.model_name != "gpt-5.6-sol":
            raise ContractValidationError("continuous Planner requires Sol")
        self.transport = transport
        self.world_bridge = world_bridge

    def plan(self, prompt: str) -> ContinuousProviderResultV1:
        result = self.transport.invoke(
            prompt,
            output_schema=rich_planner_sequence_json_schema(),
            mcp_binding=(
                self.world_bridge.runtime_binding if self.world_bridge is not None else None
            ),
        )
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
        )


class CodexContinuousValidatorPort:
    def __init__(self, transport: CodexSDKTransport, *, world_bridge: Any = None) -> None:
        route = transport.route
        allowed = {
            ("gpt-5.6-sol", "medium"),
            ("gpt-5.6-terra", "high"),
        }
        if (route.model_name, route.reasoning_effort) not in allowed:
            raise ContractValidationError("continuous Validator route is unsupported")
        self.transport = transport
        self.world_bridge = world_bridge

    def validate(
        self,
        prompt: str,
        *,
        accepted_pairs: tuple[AcceptedTurnPairV1, ...] = (),
    ) -> ContinuousProviderResultV1:
        result = self.transport.invoke(
            prompt,
            output_schema=continuous_validator_draft_json_schema(),
            mcp_binding=(
                self.world_bridge.runtime_binding if self.world_bridge is not None else None
            ),
        )
        world_tool_debug = (
            self.world_bridge.finalize(result) if self.world_bridge is not None else None
        )
        draft = from_mapping(ContinuousValidatorDraftV1, result.parsed_json or {})
        return ContinuousProviderResultV1(
            value=draft.compile(accepted_pairs=accepted_pairs),
            provider_receipt=result.receipt,
            operation_telemetry=result.operation_telemetry,
            tool_call_count=result.tool_call_count,
            failed_tool_call_count=result.failed_tool_call_count,
            world_tool_debug=world_tool_debug,
        )


class DeepSeekContinuousComposerPort:
    def __init__(self, transport: DeepSeekChatTransport) -> None:
        if transport.route.model_name != "deepseek-v4-flash":
            raise ContractValidationError("continuous Composer requires DeepSeek V4 Flash")
        self.transport = transport

    def compose(self, prompt: str) -> ContinuousProviderResultV1:
        schema = continuous_deepseek_draft_json_schema()
        messages = (
            DeepSeekMessage(
                "system",
                "You are CERA's prose Composer. Realize the supplied Planner sequence as complete presentation-neutral story prose. Preserve every required causal beat and boundary. Do not invent protected-user thought, dialogue, or consequential action. Return exactly one JSON object matching the supplied schema. Thinking is disabled.",
            ),
            DeepSeekMessage(
                "user",
                prompt
                + "\n\n[RESPONSE SCHEMA]\n"
                + json.dumps(schema, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            ),
        )
        result = self.transport.invoke(
            messages,
            output_mode=ProviderOutputMode.JSON_OBJECT,
            thinking_enabled=False,
        )
        value = from_mapping(ContinuousDeepSeekDraftV1, result.parsed_json or {})
        return ContinuousProviderResultV1(
            value=value,
            provider_receipt=result.receipt,
            operation_telemetry=None,
            tool_call_count=0,
            failed_tool_call_count=0,
            world_tool_debug=None,
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
        maximum_output_tokens=32_768,
    )


def continuous_deepseek_route():
    return replace(
        deepseek_composer_candidate(model="deepseek-v4-flash"),
        adapter_id=CONTINUOUS_DEEPSEEK_ADAPTER_VERSION,
        prompt_version=CONTINUOUS_DEEPSEEK_PROMPT_VERSION,
        maximum_output_tokens=32_768,
    )
