"""Codex Luna adapter for the provider-neutral semantic Validator port."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import cast

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
from cera.providers.codex import CodexSDKTransport, StoredCodexThreadRunner
from cera.providers.models import LiveProviderRoute, ProviderCallResult
from cera.providers.routes import codex_realization_verifier_candidate
from cera.reasoner_session.codex_stored import OpenAICodexStoredThreadBackend
from cera.schema import from_mapping
from cera.serialization import text_sha256

from .contracts import SemanticValidationRequestV1, SemanticValidationVerdictV1
from .prompting import (
    LUNA_VALIDATOR_BASE_INSTRUCTIONS,
    LUNA_VALIDATOR_PROFILE,
    build_luna_validation_prompt,
)
from .schema import semantic_verdict_json_schema

LUNA_VALIDATOR_ADAPTER = "cera.semantic_validation.luna_adapter.v1"
LUNA_VALIDATOR_PROMPT = "cera.semantic_validation.luna_prompt.v1"


def luna_validator_route() -> LiveProviderRoute:
    return replace(
        codex_realization_verifier_candidate(
            model="gpt-5.6-luna",
            effort="xhigh",
        ),
        route_id="cera_semantic_validator_luna_xhigh_v1",
        adapter_id=LUNA_VALIDATOR_ADAPTER,
        prompt_version=LUNA_VALIDATOR_PROMPT,
        maximum_output_tokens=4_096,
        automatic_retry_count=0,
        fallback_enabled=False,
        production_enabled=False,
    )


class CodexLunaSemanticValidatorBackend:
    """One fresh, archived Luna thread for every ordinary candidate."""

    def __init__(
        self,
        *,
        lifecycle: OpenAICodexStoredThreadBackend,
        workspace: Path,
        call_ledger: ContinuousProviderCallLedger,
        operation_evidence: ProviderOperationEvidenceStoreV1 | None = None,
    ) -> None:
        self.lifecycle = lifecycle
        self.workspace = workspace
        self.call_ledger = call_ledger
        self.operation_evidence = operation_evidence
        self.route = luna_validator_route()
        self._operation_index = 0
        self.last_provider_result: ContinuousProviderResultV1 | None = None

    def start_fresh_thread(self, *, base_instructions: str, profile: str) -> str:
        assert_provider_dispatch_allowed(
            "semantic_validation.luna.thread_start",
            external_provider_boundary=is_external_provider_boundary(self.lifecycle),
        )
        if (
            profile != LUNA_VALIDATOR_PROFILE
            or base_instructions != LUNA_VALIDATOR_BASE_INSTRUCTIONS
        ):
            raise ContractValidationError("Luna Validator profile changed")
        if base_instructions not in self.lifecycle.base_instructions:
            raise ContractValidationError("Luna lifecycle lacks its stable base instructions")
        return self.lifecycle.start_stored_thread()

    def run_validator_once(
        self,
        *,
        thread_id: str,
        request: SemanticValidationRequestV1,
    ) -> SemanticValidationVerdictV1:
        assert_provider_dispatch_allowed(
            "semantic_validation.luna.turn",
            external_provider_boundary=is_external_provider_boundary(self.lifecycle),
        )
        self._operation_index += 1
        operation_workspace = (
            self.workspace / f"luna_validation_operation_{self._operation_index:04d}"
        )
        try:
            operation_workspace.mkdir(parents=True)
        except FileExistsError as exc:
            raise ContractValidationError("Luna validation workspace already exists") from exc
        transport = CodexSDKTransport(
            self.route,
            workspace=operation_workspace,
            runner=StoredCodexThreadRunner(thread_id),
        )
        prompt = build_luna_validation_prompt(request)
        output_schema = semantic_verdict_json_schema(
            decision_keys=tuple(
                decision.decision_key for decision in request.cognition_plan.decision_records
            )
        )
        stored_thread_sha256 = text_sha256(thread_id)

        def dispatch(markers: ProviderCallStageMarkersV1) -> ProviderCallResult:
            return transport.invoke(
                prompt,
                output_schema=output_schema,
                mcp_binding=None,
                on_worker_started=markers.mark_worker_started,
                on_worker_preflight=markers.mark_worker_preflight,
                on_transport_invoke=markers.mark_transport_invoked,
            )

        def finalize(result: ProviderCallResult) -> ContinuousProviderResultV1:
            payload = result.parsed_json or {}
            if set(payload) != {"result"} or not isinstance(payload.get("result"), dict):
                raise ContractValidationError("Luna result changed its closed provider envelope")
            verdict = from_mapping(
                SemanticValidationVerdictV1,
                payload["result"],
            )
            return ContinuousProviderResultV1(
                value=verdict,
                provider_receipt=result.receipt,
                operation_telemetry=result.operation_telemetry,
                tool_call_count=result.tool_call_count,
                failed_tool_call_count=result.failed_tool_call_count,
                world_tool_debug=None,
                physical_session_sha256=stored_thread_sha256,
            )

        provider_result = self.call_ledger.execute(
            owner="validator",
            operation=f"luna_validate_{self._operation_index:04d}",
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
                    schema_version=SemanticValidationVerdictV1.SCHEMA_VERSION,
                    operation_workspace=str(operation_workspace),
                    role="validator",
                    archival_policy="fresh_per_candidate_then_archive",
                )
            ),
        )
        self.last_provider_result = provider_result
        return cast(SemanticValidationVerdictV1, provider_result.value)

    def archive(self, thread_id: str) -> None:
        self.lifecycle.archive_stored_thread(thread_id)

    def is_resumable(self, thread_id: str) -> bool:
        resumable = self.lifecycle.stored_thread_is_selectable(thread_id)
        if self.operation_evidence is not None:
            identity_sha256 = text_sha256(thread_id)
            dispatched = self.operation_evidence.has_provider_call_for_thread(
                role="validator",
                thread_identity_sha256=identity_sha256,
            )
            self.operation_evidence.record_archival(
                role="validator",
                archived=True,
                resumable=resumable,
                disposition=(
                    "archived_after_candidate"
                    if dispatched
                    else "archived_before_provider_dispatch"
                ),
                thread_identity_sha256=identity_sha256,
            )
        return resumable
