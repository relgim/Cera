"""Unpromoted provider adapters for the sequence-first semantic route.

Construction and tests are provider-free.  A caller must still supply the
installed Codex/DeepSeek transports and an independently authorized call
ledger before any external operation can occur.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from cera.errors import ContractValidationError
from cera.schema import from_mapping
from cera.serialization import text_sha256
from cera.providers.codex import CodexSDKTransport, StoredCodexThreadRunner
from cera.providers.routes import (
    codex_reasoner_candidate,
    codex_realization_verifier_candidate,
)
from cera.reasoner_session.codex_stored import OpenAICodexStoredThreadBackend
from cera.continuous.call_ledger import ContinuousProviderCallLedger
from cera.continuous.provider import (
    ContinuousProviderResultV1,
    DeepSeekContinuousComposerPort,
)

from .contracts import (
    SequenceDraftV1,
    ReaderVerdictV1,
    SequenceFirstReaderInputV1,
    SequenceFirstValidatorInputV1,
    SequenceFirstWriterBriefV1,
    ValidatorDecisionV1,
    WriterResponseV1,
)
from .prompting import (
    PLANNER_BASE_INSTRUCTIONS,
    PLANNER_PROFILE,
    READER_BASE_INSTRUCTIONS,
    VALIDATOR_BASE_INSTRUCTIONS,
    VALIDATOR_PROFILE,
    reader_prompt,
    writer_prompt,
)


SEQUENCE_FIRST_PLANNER_ADAPTER = "cera.sequence_first.planner_adapter.v1"
SEQUENCE_FIRST_PLANNER_PROMPT = "cera.sequence_first.planner_prompt.v1"
SEQUENCE_FIRST_VALIDATOR_ADAPTER = "cera.sequence_first.validator_adapter.v1"
SEQUENCE_FIRST_VALIDATOR_PROMPT = "cera.sequence_first.validator_prompt.v1"
SEQUENCE_FIRST_READER_ADAPTER = "cera.sequence_first.reader_adapter.v1"
SEQUENCE_FIRST_READER_PROMPT = "cera.sequence_first.reader_prompt.v1"


def sequence_first_planner_route():
    return replace(
        codex_reasoner_candidate(model="gpt-5.6-sol", effort="medium"),
        route_id="cera_sequence_first_planner_sol_medium_v1",
        adapter_id=SEQUENCE_FIRST_PLANNER_ADAPTER,
        prompt_version=SEQUENCE_FIRST_PLANNER_PROMPT,
        maximum_output_tokens=8_192,
        automatic_retry_count=0,
        fallback_enabled=False,
        production_enabled=False,
    )


def sequence_first_validator_route(*, model: str, effort: str):
    return replace(
        codex_realization_verifier_candidate(model=model, effort=effort),
        route_id=f"cera_sequence_first_validator_{model}_{effort}_v1",
        adapter_id=SEQUENCE_FIRST_VALIDATOR_ADAPTER,
        prompt_version=SEQUENCE_FIRST_VALIDATOR_PROMPT,
        maximum_output_tokens=8_192,
        automatic_retry_count=0,
        fallback_enabled=False,
        production_enabled=False,
    )


def sequence_first_reader_route(*, model: str, effort: str):
    return replace(
        codex_realization_verifier_candidate(model=model, effort=effort),
        route_id=f"cera_sequence_first_reader_{model}_{effort}_v1",
        adapter_id=SEQUENCE_FIRST_READER_ADAPTER,
        prompt_version=SEQUENCE_FIRST_READER_PROMPT,
        maximum_output_tokens=4_096,
        automatic_retry_count=0,
        fallback_enabled=False,
        production_enabled=False,
    )


def _strict_object(properties: dict) -> dict:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def _nullable(schema: dict) -> dict:
    return {"anyOf": [schema, {"type": "null"}]}


def _string_array() -> dict:
    return {"type": "array", "items": {"type": "string"}}


def _sequence_item_schema() -> dict:
    return _strict_object(
        {
            "item_key": {"type": "string"},
            "kind": {
                "type": "string",
                "enum": [
                    "action",
                    "dialogue_intent",
                    "private_state",
                    "perception",
                    "material_continuity",
                    "knowledge_change",
                    "relationship_change",
                    "presence_change",
                    "remote_communication",
                    "scene_transition",
                    "stopping_boundary",
                ],
            },
            "concise_meaning": {"type": "string"},
            "owner_id": _nullable({"type": "string"}),
            "causal_parent_item_key": _nullable({"type": "string"}),
            "evidence_keys": _string_array(),
            "protected_user_claim_keys": _string_array(),
            "durable_change_keys": _string_array(),
            "planner_item_keys": _string_array(),
        }
    )


def _durable_change_schema() -> dict:
    return _strict_object(
        {
            "change_key": {"type": "string"},
            "kind": {
                "type": "string",
                "enum": [
                    "material",
                    "knowledge",
                    "relationship",
                    "presence",
                    "character_development",
                ],
            },
            "subject_ids": _string_array(),
            "concise_change": {"type": "string"},
            "target_key": {"type": "string"},
            "visibility": {
                "type": "string",
                "enum": ["public", "character_private"],
            },
            "knowledge_owner_id": _nullable({"type": "string"}),
        }
    )


def _presence_change_schema() -> dict:
    return _strict_object(
        {
            "character_id": {"type": "string"},
            "direction": {"type": "string", "enum": ["enter", "leave"]},
            "effective_after_item_key": {"type": "string"},
        }
    )


def sequence_draft_json_schema() -> dict:
    return _strict_object(
        {
            "items": {"type": "array", "items": _sequence_item_schema()},
            "durable_changes": {
                "type": "array",
                "items": _durable_change_schema(),
            },
            "presence_changes": {
                "type": "array",
                "items": _presence_change_schema(),
            },
            "resulting_public_state": {"type": "string"},
            "unresolved_threads": _string_array(),
            "stopping_boundary": {"type": "string"},
        }
    )


def validator_decision_json_schema() -> dict:
    review_flag = _strict_object(
        {
            "flag_code": {"type": "string"},
            "severity": {
                "type": "string",
                "enum": ["notice", "important", "creator_decision"],
            },
            "concise_explanation": {"type": "string"},
        }
    )
    conflict = _strict_object(
        {
            "conflict_class": {
                "type": "string",
                "enum": [
                    "required_item_omitted",
                    "causal_order_changed",
                    "accepted_state_contradiction",
                    "character_logic_contradiction",
                    "knowledge_or_privacy_breach",
                    "presence_contradiction",
                    "protected_user_invention",
                    "material_addition",
                    "stopping_boundary_crossed",
                    "capability_restriction",
                    "authority_ambiguity",
                ],
            },
            "concise_explanation": {"type": "string"},
            "exact_quote": _nullable({"type": "string"}),
            "omitted_planner_item_key": _nullable({"type": "string"}),
        }
    )
    return _strict_object(
        {
            "verdict": {"type": "string", "enum": ["accept", "reject"]},
            "realized_sequence": _nullable(sequence_draft_json_schema()),
            "review_flags": {"type": "array", "items": review_flag},
            "conflict": _nullable(conflict),
        }
    )


def reader_verdict_json_schema() -> dict:
    issue = _strict_object(
        {
            "issue_code": {"type": "string"},
            "concise_explanation": {"type": "string"},
            "exact_quote": _nullable({"type": "string"}),
        }
    )
    return _strict_object(
        {
            "status": {
                "type": "string",
                "enum": ["accepted", "rejected", "inconclusive"],
            },
            "issues": {"type": "array", "items": issue},
        }
    )


class SequenceFirstPlannerCodexBackend:
    """Actual stored-thread Planner adapter; stable instructions are not resent."""

    def __init__(
        self,
        *,
        lifecycle: OpenAICodexStoredThreadBackend,
        workspace: Path,
        call_ledger: ContinuousProviderCallLedger,
        world_bridge=None,
    ) -> None:
        self.lifecycle = lifecycle
        self.workspace = workspace
        self.call_ledger = call_ledger
        self.world_bridge = world_bridge
        self.route = sequence_first_planner_route()
        self._operation_index = 0
        self.last_provider_result: ContinuousProviderResultV1 | None = None

    def start_stored_thread(self, *, base_instructions: str, profile: str) -> str:
        if profile != PLANNER_PROFILE or base_instructions != PLANNER_BASE_INSTRUCTIONS:
            raise ContractValidationError("sequence-first Planner profile changed")
        if base_instructions not in self.lifecycle.base_instructions:
            raise ContractValidationError(
                "stored Planner backend lacks sequence-first base instructions"
            )
        return self.lifecycle.start_stored_thread()

    def run_planner_turn(self, *, thread_id: str, prompt: str) -> SequenceDraftV1:
        transport = CodexSDKTransport(
            self.route,
            workspace=self.workspace,
            runner=StoredCodexThreadRunner(thread_id),
        )
        self._operation_index += 1
        stored_thread_sha256 = text_sha256(thread_id)
        mcp_binding = (
            self.world_bridge.runtime_binding
            if self.world_bridge is not None
            else None
        )

        def dispatch(markers):
            return transport.invoke(
                prompt,
                output_schema=sequence_draft_json_schema(),
                mcp_binding=mcp_binding,
                on_worker_started=markers.mark_worker_started,
                on_worker_preflight=markers.mark_worker_preflight,
                on_transport_invoke=markers.mark_transport_invoked,
            )

        def finalize(result):
            world_tool_debug = (
                self.world_bridge.finalize(result)
                if self.world_bridge is not None
                else None
            )
            return ContinuousProviderResultV1(
                value=from_mapping(SequenceDraftV1, result.parsed_json or {}),
                provider_receipt=result.receipt,
                operation_telemetry=result.operation_telemetry,
                tool_call_count=result.tool_call_count,
                failed_tool_call_count=result.failed_tool_call_count,
                world_tool_debug=world_tool_debug,
                physical_session_sha256=stored_thread_sha256,
            )

        provider_result = self.call_ledger.execute(
            owner="planner",
            operation=f"sequence_first_plan_{self._operation_index:04d}",
            route=self.route.route_id,
            model=self.route.model_name,
            effort=self.route.reasoning_effort,
            dispatch_with_stage_markers=dispatch,
            finalize=finalize,
            stored_thread_sha256=stored_thread_sha256,
        )
        self.last_provider_result = provider_result
        return provider_result.value

    def is_resumable(self, thread_id: str) -> bool:
        return self.lifecycle.resume_stored_thread(thread_id)


class SequenceFirstValidatorCodexBackend:
    """Actual one-shot Validator adapter with no historical profile default."""

    def __init__(
        self,
        *,
        lifecycle: OpenAICodexStoredThreadBackend,
        workspace: Path,
        model: str,
        effort: str,
        call_ledger: ContinuousProviderCallLedger,
    ) -> None:
        self.lifecycle = lifecycle
        self.workspace = workspace
        self.call_ledger = call_ledger
        self.route = sequence_first_validator_route(model=model, effort=effort)
        self._operation_index = 0
        self.last_provider_result: ContinuousProviderResultV1 | None = None

    def start_fresh_thread(self, *, base_instructions: str, profile: str) -> str:
        if profile != VALIDATOR_PROFILE or base_instructions != VALIDATOR_BASE_INSTRUCTIONS:
            raise ContractValidationError("sequence-first Validator profile changed")
        if base_instructions not in self.lifecycle.base_instructions:
            raise ContractValidationError(
                "fresh Validator backend lacks compact base instructions"
            )
        return self.lifecycle.start_stored_thread()

    def run_validator_once(
        self,
        *,
        thread_id: str,
        prompt: str,
    ) -> ValidatorDecisionV1:
        transport = CodexSDKTransport(
            self.route,
            workspace=self.workspace,
            runner=StoredCodexThreadRunner(thread_id),
        )
        self._operation_index += 1
        stored_thread_sha256 = text_sha256(thread_id)

        def dispatch(markers):
            return transport.invoke(
                prompt,
                output_schema=validator_decision_json_schema(),
                mcp_binding=None,
                on_worker_started=markers.mark_worker_started,
                on_worker_preflight=markers.mark_worker_preflight,
                on_transport_invoke=markers.mark_transport_invoked,
            )

        def finalize(result):
            return ContinuousProviderResultV1(
                value=from_mapping(
                    ValidatorDecisionV1,
                    result.parsed_json or {},
                ),
                provider_receipt=result.receipt,
                operation_telemetry=result.operation_telemetry,
                tool_call_count=result.tool_call_count,
                failed_tool_call_count=result.failed_tool_call_count,
                world_tool_debug=None,
                physical_session_sha256=stored_thread_sha256,
            )

        provider_result = self.call_ledger.execute(
            owner="validator",
            operation=f"sequence_first_validate_{self._operation_index:04d}",
            route=self.route.route_id,
            model=self.route.model_name,
            effort=self.route.reasoning_effort,
            dispatch_with_stage_markers=dispatch,
            finalize=finalize,
            stored_thread_sha256=stored_thread_sha256,
        )
        self.last_provider_result = provider_result
        return provider_result.value

    def archive(self, thread_id: str) -> None:
        self.lifecycle.archive_stored_thread(thread_id)

    def is_resumable(self, thread_id: str) -> bool:
        return self.lifecycle.stored_thread_is_selectable(thread_id)


class SequenceFirstReaderCodexPort:
    """Fresh one-shot Reader with durable accounting and terminal isolation."""

    def __init__(
        self,
        *,
        lifecycle: OpenAICodexStoredThreadBackend,
        workspace: Path,
        model: str,
        effort: str,
        call_ledger: ContinuousProviderCallLedger,
    ) -> None:
        if READER_BASE_INSTRUCTIONS not in lifecycle.base_instructions:
            raise ContractValidationError(
                "fresh Reader backend lacks sequence-first base instructions"
            )
        self.lifecycle = lifecycle
        self.workspace = workspace
        self.call_ledger = call_ledger
        self.route = sequence_first_reader_route(model=model, effort=effort)
        self._operation_index = 0
        self.last_provider_result: ContinuousProviderResultV1 | None = None

    def read(self, request: SequenceFirstReaderInputV1) -> ReaderVerdictV1:
        thread_id = self.lifecycle.start_stored_thread()
        stored_thread_sha256 = text_sha256(thread_id)
        transport = CodexSDKTransport(
            self.route,
            workspace=self.workspace,
            runner=StoredCodexThreadRunner(thread_id),
        )
        self._operation_index += 1

        def dispatch(markers):
            return transport.invoke(
                reader_prompt(request),
                output_schema=reader_verdict_json_schema(),
                mcp_binding=None,
                on_worker_started=markers.mark_worker_started,
                on_worker_preflight=markers.mark_worker_preflight,
                on_transport_invoke=markers.mark_transport_invoked,
            )

        def finalize(result):
            return ContinuousProviderResultV1(
                value=from_mapping(ReaderVerdictV1, result.parsed_json or {}),
                provider_receipt=result.receipt,
                operation_telemetry=result.operation_telemetry,
                tool_call_count=result.tool_call_count,
                failed_tool_call_count=result.failed_tool_call_count,
                world_tool_debug=None,
                physical_session_sha256=stored_thread_sha256,
            )

        try:
            provider_result = self.call_ledger.execute(
                owner="reader",
                operation=f"sequence_first_read_{self._operation_index:04d}",
                route=self.route.route_id,
                model=self.route.model_name,
                effort=self.route.reasoning_effort,
                dispatch_with_stage_markers=dispatch,
                finalize=finalize,
                stored_thread_sha256=stored_thread_sha256,
            )
            self.last_provider_result = provider_result
            return provider_result.value
        finally:
            self.lifecycle.archive_stored_thread(thread_id)
            if self.lifecycle.stored_thread_is_selectable(thread_id):
                raise ContractValidationError(
                    "archived sequence-first Reader remains resumable"
                )


class SequenceFirstDeepSeekWriterPort:
    """Reuse the qualified two-field DeepSeek wire; add no semantic metadata."""

    def __init__(self, composer: DeepSeekContinuousComposerPort) -> None:
        self.composer = composer

    def write(
        self,
        brief: SequenceFirstWriterBriefV1,
        attempt_number: int,
    ) -> WriterResponseV1:
        if not 1 <= attempt_number <= 3:
            raise ContractValidationError("DeepSeek Writer attempt is outside its bound")
        result = self.composer.compose(writer_prompt(brief))
        draft = result.value
        return WriterResponseV1(
            schema_version=draft.schema_version,
            story_text=draft.story_text,
        )
