"""Codex adapter for the provider-neutral cognition planner port."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any, cast

from cera.continuous.call_ledger import (
    ContinuousProviderCallLedger,
    ProviderCallStageMarkersV1,
)
from cera.continuous.operation_evidence import ProviderOperationEvidenceStoreV1
from cera.continuous.provider import ContinuousProviderResultV1
from cera.errors import ContractValidationError
from cera.provider_dispatch_guard import (
    assert_provider_dispatch_allowed,
    is_external_provider_boundary,
)
from cera.providers.codex import (
    CodexSDKTransport,
    StoredCodexThreadRunner,
    decode_completed_codex_output,
)
from cera.providers.models import LiveProviderRoute, ProviderCallResult
from cera.providers.routes import codex_reasoner_candidate
from cera.reasoner_session.codex_stored import OpenAICodexStoredThreadBackend
from cera.schema import from_mapping
from cera.sequence_first.contracts import ProviderReferenceScopeV1
from cera.serialization import text_sha256

from .contracts import CognitionPlanV1, CognitionTurnContextV1
from .prompting import COGNITION_PLANNER_BASE_INSTRUCTIONS, COGNITION_PLANNER_PROFILE
from .provider_schema import cognition_plan_json_schema
from .validation import CognitionValidationContextV1, validate_cognition_plan

COGNITION_PLANNER_ADAPTER = "cera.cognition.codex_planner_adapter.v4"
COGNITION_PLANNER_PROMPT = "cera.cognition.codex_planner_prompt.v5"
# This is a transport-loss boundary, not the three-minute interaction target.
# Retained-turn latency remains observable in provider telemetry.
COGNITION_PLANNER_HARD_TIMEOUT_SECONDS = 600


def cognition_planner_route() -> LiveProviderRoute:
    return replace(
        codex_reasoner_candidate(model="gpt-5.6-sol", effort="medium"),
        route_id="cera_cognition_planner_sol_medium_v5",
        adapter_id=COGNITION_PLANNER_ADAPTER,
        prompt_version=COGNITION_PLANNER_PROMPT,
        timeout_seconds=COGNITION_PLANNER_HARD_TIMEOUT_SECONDS,
        maximum_output_tokens=12_288,
        automatic_retry_count=0,
        fallback_enabled=False,
        production_enabled=False,
    )


class CodexCognitionPlannerBackend:
    """One retained Codex thread with request-bound evidence tools."""

    def __init__(
        self,
        *,
        lifecycle: OpenAICodexStoredThreadBackend,
        workspace: Path,
        call_ledger: ContinuousProviderCallLedger,
        world_bridge: Any = None,
        operation_evidence: ProviderOperationEvidenceStoreV1 | None = None,
    ) -> None:
        self.lifecycle = lifecycle
        self.workspace = workspace
        self.call_ledger = call_ledger
        self.world_bridge = world_bridge
        self.operation_evidence = operation_evidence
        self.route = cognition_planner_route()
        self._operation_index = 0
        self.last_provider_result: ContinuousProviderResultV1 | None = None
        self.last_available_evidence_refs: tuple[str, ...] = ()

    def start_stored_thread(self, *, base_instructions: str, profile: str) -> str:
        assert_provider_dispatch_allowed(
            "cognition.planner.thread_start",
            external_provider_boundary=is_external_provider_boundary(self.lifecycle),
        )
        if (
            profile != COGNITION_PLANNER_PROFILE
            or base_instructions != COGNITION_PLANNER_BASE_INSTRUCTIONS
        ):
            raise ContractValidationError("cognition Planner profile changed")
        if base_instructions not in self.lifecycle.base_instructions:
            raise ContractValidationError(
                "stored cognition backend lacks its stable base instructions"
            )
        return self.lifecycle.start_stored_thread()

    def run_cognition_turn(
        self,
        *,
        thread_id: str,
        prompt: str,
        context: CognitionTurnContextV1,
        reference_scope: ProviderReferenceScopeV1,
    ) -> CognitionPlanV1:
        assert_provider_dispatch_allowed(
            "cognition.planner.turn",
            external_provider_boundary=is_external_provider_boundary(self.lifecycle),
        )
        self.last_available_evidence_refs = reference_scope.evidence_keys
        self._operation_index += 1
        operation_workspace = self.workspace / (
            f"cognition_planner_operation_{self._operation_index:04d}"
        )
        try:
            operation_workspace.mkdir(parents=True)
        except FileExistsError as exc:
            raise ContractValidationError(
                "cognition provider operation workspace already exists"
            ) from exc

        transport = CodexSDKTransport(
            self.route,
            workspace=operation_workspace,
            runner=StoredCodexThreadRunner(
                thread_id,
                base_instructions=COGNITION_PLANNER_BASE_INSTRUCTIONS,
            ),
        )
        stored_thread_sha256 = text_sha256(thread_id)
        mcp_binding = self.world_bridge.runtime_binding if self.world_bridge is not None else None
        output_schema = cognition_plan_json_schema(
            context=context,
            reference_scope=reference_scope,
        )

        def dispatch(markers: ProviderCallStageMarkersV1) -> ProviderCallResult:
            return transport.invoke(
                prompt,
                output_schema=output_schema,
                mcp_binding=mcp_binding,
                on_worker_started=markers.mark_worker_started,
                on_worker_preflight=markers.mark_worker_preflight,
                on_transport_invoke=markers.mark_transport_invoked,
            )

        def finalize(result: ProviderCallResult) -> ContinuousProviderResultV1:
            world_tool_debug = (
                self.world_bridge.finalize(result) if self.world_bridge is not None else None
            )
            dynamic_evidence_refs = _bound_world_evidence_refs(world_tool_debug)
            available_evidence_refs = tuple(
                dict.fromkeys((*reference_scope.evidence_keys, *dynamic_evidence_refs))
            )

            def decode_plan() -> CognitionPlanV1:
                plan = cast(
                    CognitionPlanV1,
                    from_mapping(CognitionPlanV1, result.parsed_json or {}),
                )
                validate_cognition_plan(
                    plan,
                    turn=context.turn,
                    context=CognitionValidationContextV1(
                        autonomy_mode=context.autonomy_mode,
                        logic_route=context.logic_route,
                        available_evidence_refs=available_evidence_refs,
                        available_provisional_record_ids=(context.available_provisional_record_ids),
                    ),
                )
                return plan

            plan = decode_completed_codex_output(
                result,
                safe_diagnostic="provider_output:cognition_plan_contract_invalid",
                decoder=decode_plan,
            )
            self.last_available_evidence_refs = available_evidence_refs
            return ContinuousProviderResultV1(
                value=plan,
                provider_receipt=result.receipt,
                operation_telemetry=result.operation_telemetry,
                tool_call_count=result.tool_call_count,
                failed_tool_call_count=result.failed_tool_call_count,
                world_tool_debug=world_tool_debug,
                physical_session_sha256=stored_thread_sha256,
            )

        provider_result = self.call_ledger.execute(
            owner="planner",
            operation=f"cognition_plan_{self._operation_index:04d}",
            route=self.route.route_id,
            model=self.route.model_name,
            effort=self.route.reasoning_effort,
            dispatch_with_stage_markers=dispatch,
            finalize=finalize,
            stored_thread_sha256=stored_thread_sha256,
            operation_evidence=(
                None
                if self.operation_evidence is None
                else self.operation_evidence.request(
                    request_bytes=prompt.encode("utf-8"),
                    structured_output_schema=output_schema,
                    prompt_version=self.route.prompt_version,
                    schema_version=CognitionPlanV1.SCHEMA_VERSION,
                    operation_workspace=str(operation_workspace),
                    role="planner",
                    archival_policy="persistent_per_accepted_branch",
                )
            ),
        )
        self.last_provider_result = provider_result
        if self.operation_evidence is not None:
            self.operation_evidence.record_archival(
                role="planner",
                archived=False,
                resumable=self.lifecycle.resume_stored_thread(thread_id),
                disposition="retained_for_accepted_branch",
            )
        return cast(CognitionPlanV1, provider_result.value)

    def is_resumable(self, thread_id: str) -> bool:
        return self.lifecycle.resume_stored_thread(thread_id)


def _bound_world_evidence_refs(value: object) -> tuple[str, ...]:
    """Extract only Python-allocated bindings from this exact bridge result."""

    if value is None:
        return ()
    if not isinstance(value, dict):
        raise ContractValidationError("cognition world-tool receipt changed shape")
    raw = value.get("evidence_bindings", ())
    if not isinstance(raw, list | tuple):
        raise ContractValidationError("cognition evidence bindings changed shape")
    output: list[str] = []
    for binding in raw:
        if not isinstance(binding, dict) or binding.get("kind") != "world_record":
            raise ContractValidationError("cognition evidence binding is invalid")
        key = binding.get("binding_key")
        if not isinstance(key, str) or not key.startswith("binding_record_"):
            raise ContractValidationError("cognition evidence binding key is invalid")
        output.append(key)
    if len(output) != len(set(output)):
        raise ContractValidationError("cognition evidence bindings contain duplicates")
    return tuple(output)
