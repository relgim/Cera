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
from cera.serialization import re_is_sha256, text_sha256

from .contracts import (
    AcceptedTurnPairV1,
    CreatedFieldLogEntryV1,
    EventRecordCandidateV1,
    EventItemRoleLedgerV1,
    FinalSequenceItemV1,
    FinalSequenceV1,
    ProtectedSemanticAdjudicationV1,
    ProtectedUserRealizationSpanV1,
    RichPlannerSequenceV1,
    SceneSummaryV1,
    StoryRealizationSegmentV1,
    ValidatorFinalizationPackageV1,
    ValidatorSemanticStatus,
    ValidatorTaskMode,
    WorldEditOperationKind,
    WorldEditOperationV1,
    json_value_type,
)
from .call_ledger import ContinuousProviderCallLedger
from .prompting import (
    CONTINUOUS_PLANNER_PROMPT_VERSION,
    CONTINUOUS_VALIDATOR_PROMPT_VERSION,
)


CONTINUOUS_PLANNER_ADAPTER_VERSION = "cera.continuous_planner_adapter.v7"
CONTINUOUS_VALIDATOR_ADAPTER_VERSION = "cera.continuous_validator_adapter.v8"
CONTINUOUS_DEEPSEEK_ADAPTER_VERSION = "cera.continuous_deepseek_adapter.v5"
CONTINUOUS_DEEPSEEK_PROMPT_VERSION = "cera.continuous_deepseek_prompt.v5"


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
class ContinuousDeepSeekDraftV1:
    SCHEMA_VERSION: ClassVar[str] = "cera.continuous_deepseek_draft.v5"

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


def rich_planner_sequence_json_schema() -> dict[str, Any]:
    schema = _schema_for(RichPlannerSequenceV1)
    schema["properties"]["provisional"] = {"type": "boolean", "const": True}
    schema["properties"]["accepted_turn_id"] = {"type": "null", "const": None}
    return schema


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

    def plan(self, prompt: str) -> ContinuousProviderResultV1:
        self._operation_index += 1
        route = self.transport.route
        # All local schema and MCP construction completes before the provider
        # ledger records a transport invocation.
        output_schema = rich_planner_sequence_json_schema()
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
        self._operation_index = 0

    def validate(
        self,
        prompt: str,
        *,
        accepted_pairs: tuple[AcceptedTurnPairV1, ...] = (),
    ) -> ContinuousProviderResultV1:
        self._operation_index += 1
        route = self.transport.route
        output_schema = continuous_validator_draft_json_schema()
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
            draft = from_mapping(ContinuousValidatorDraftV1, result.parsed_json or {})
            return ContinuousProviderResultV1(
                value=draft.compile(accepted_pairs=accepted_pairs),
                provider_receipt=result.receipt,
                operation_telemetry=result.operation_telemetry,
                tool_call_count=result.tool_call_count,
                failed_tool_call_count=result.failed_tool_call_count,
                world_tool_debug=world_tool_debug,
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


class DeepSeekContinuousComposerPort:
    def __init__(
        self,
        transport: DeepSeekChatTransport,
        *,
        call_ledger: ContinuousProviderCallLedger,
    ) -> None:
        if transport.route.model_name != "deepseek-v4-flash":
            raise ContractValidationError("continuous Composer requires DeepSeek V4 Flash")
        self.transport = transport
        self.call_ledger = call_ledger
        self._operation_index = 0

    def compose(self, prompt: str) -> ContinuousProviderResultV1:
        schema = continuous_deepseek_draft_json_schema()
        messages = (
            DeepSeekMessage(
                "system",
                "You are CERA's prose Composer. Realize the supplied Planner sequence as complete presentation-neutral story prose. Preserve every required causal beat and boundary. Return an exhaustive, ordered, gap-free story_segments ledger covering every story_text character. Every segment declares one closed roles ledger: action_owner_ids own actions; state_owner_ids own thoughts, emotions, bodily states, consent, and decisions; speaker_ids own dialogue; affected_ids, addressed_ids, observing_ids, and referenced_ids are non-owning. Do not invent, paraphrase, extend, or misattribute protected-user thought, dialogue, action, decision, emotion, consent, or movement. Any assertion owned by Ted must exactly equal one supplied ingress claim and cite that claim. An NPC action may affect or address Ted without inventing Ted's response. Also declare the matching exact protected_user_realizations occurrence. Return exactly one JSON object matching the supplied schema. Thinking is disabled.",
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
            value = from_mapping(ContinuousDeepSeekDraftV1, result.parsed_json or {})
            return ContinuousProviderResultV1(
                value=value,
                provider_receipt=result.receipt,
                operation_telemetry=None,
                tool_call_count=0,
                failed_tool_call_count=0,
                world_tool_debug=None,
            )

        return self.call_ledger.execute(
            owner="composer",
            operation=f"compose_{self._operation_index:04d}",
            route=route.route_id,
            model=route.model_name,
            effort=route.reasoning_effort,
            dispatch_with_invocation_marker=dispatch,
            finalize=finalize,
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
