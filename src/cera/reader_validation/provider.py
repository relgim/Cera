"""Fresh Sol-medium provider adapter for ordinary Reader validation."""

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
from cera.providers.codex import (
    CodexSDKTransport,
    StoredCodexThreadRunner,
    decode_completed_codex_output,
)
from cera.providers.models import LiveProviderRoute, ProviderCallResult
from cera.providers.routes import codex_realization_verifier_candidate
from cera.reasoner_session.codex_stored import OpenAICodexStoredThreadBackend
from cera.schema import from_mapping
from cera.serialization import canonical_sha256, text_sha256

from .contracts import ReaderValidationRequestV1, ReaderVerdictV1
from .prompting import (
    SOL_READER_BASE_INSTRUCTIONS,
    SOL_READER_PROFILE,
    build_reader_validation_prompt,
)
from .schema import reader_verdict_json_schema

SOL_READER_ADAPTER = "cera.reader_validation.sol_adapter.v2"
SOL_READER_PROMPT = "cera.reader_validation.sol_prompt.v1"
SOL_READER_HARD_TIMEOUT_SECONDS = 600


def sol_reader_route() -> LiveProviderRoute:
    return replace(
        codex_realization_verifier_candidate(
            model="gpt-5.6-sol",
            effort="medium",
        ),
        route_id="cera_reader_validation_sol_medium_v2",
        adapter_id=SOL_READER_ADAPTER,
        prompt_version=SOL_READER_PROMPT,
        timeout_seconds=SOL_READER_HARD_TIMEOUT_SECONDS,
        maximum_output_tokens=4_096,
        automatic_retry_count=0,
        fallback_enabled=False,
        production_enabled=False,
    )


class CodexSolReaderBackend:
    """One fresh, archived Sol-medium thread for every stage attempt."""

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
        self.route = sol_reader_route()
        self._operation_index = 0
        self.last_provider_result: ContinuousProviderResultV1 | None = None

    @property
    def external_provider_boundary(self) -> bool:
        return is_external_provider_boundary(self.lifecycle)

    def start_fresh_thread(self, *, base_instructions: str, profile: str) -> str:
        assert_provider_dispatch_allowed(
            "reader_validation.sol.thread_start",
            external_provider_boundary=self.external_provider_boundary,
        )
        if profile != SOL_READER_PROFILE or base_instructions != SOL_READER_BASE_INSTRUCTIONS:
            raise ContractValidationError("Sol Reader profile changed")
        if base_instructions not in self.lifecycle.base_instructions:
            raise ContractValidationError("Sol Reader lifecycle lacks its stable instructions")
        return self.lifecycle.start_stored_thread()

    def run_reader_once(
        self,
        *,
        thread_id: str,
        request: ReaderValidationRequestV1,
    ) -> ReaderVerdictV1:
        assert_provider_dispatch_allowed(
            "reader_validation.sol.turn",
            external_provider_boundary=self.external_provider_boundary,
        )
        if self.operation_evidence is not None:
            self.operation_evidence.begin_turn(f"reader-validation:{canonical_sha256(request)}")
        self._operation_index += 1
        operation_workspace = self.workspace / f"reader_operation_{self._operation_index:04d}"
        try:
            operation_workspace.mkdir(parents=True)
        except FileExistsError as exc:
            raise ContractValidationError("Sol Reader workspace already exists") from exc
        transport = CodexSDKTransport(
            self.route,
            workspace=operation_workspace,
            runner=StoredCodexThreadRunner(
                thread_id,
                base_instructions=SOL_READER_BASE_INSTRUCTIONS,
            ),
        )
        prompt = build_reader_validation_prompt(request)
        output_schema = reader_verdict_json_schema(
            plan_item_keys=request.current_plan_item_keys,
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
            def decode_verdict() -> ReaderVerdictV1:
                payload = result.parsed_json or {}
                if set(payload) != {"verdict"} or not isinstance(payload.get("verdict"), dict):
                    raise ContractValidationError("Sol Reader result changed its closed envelope")
                return cast(
                    ReaderVerdictV1,
                    from_mapping(ReaderVerdictV1, payload["verdict"]),
                )

            verdict = decode_completed_codex_output(
                result,
                safe_diagnostic="provider_output:reader_verdict_contract_invalid",
                decoder=decode_verdict,
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
            owner="reader",
            operation=f"sol_read_{self._operation_index:04d}",
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
                    schema_version=ReaderVerdictV1.SCHEMA_VERSION,
                    operation_workspace=str(operation_workspace),
                    role="reader",
                    archival_policy="fresh_per_candidate_attempt_then_archive",
                )
            ),
        )
        self.last_provider_result = provider_result
        return cast(ReaderVerdictV1, provider_result.value)

    def archive(self, thread_id: str) -> None:
        self.lifecycle.archive_stored_thread(thread_id)

    def is_resumable(self, thread_id: str) -> bool:
        resumable = self.lifecycle.stored_thread_is_selectable(thread_id)
        if self.operation_evidence is not None:
            identity_sha256 = text_sha256(thread_id)
            dispatched = self.operation_evidence.has_provider_call_for_thread(
                role="reader",
                thread_identity_sha256=identity_sha256,
            )
            self.operation_evidence.record_archival(
                role="reader",
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


__all__ = [
    "CodexSolReaderBackend",
    "SOL_READER_ADAPTER",
    "SOL_READER_HARD_TIMEOUT_SECONDS",
    "SOL_READER_PROMPT",
    "sol_reader_route",
]
