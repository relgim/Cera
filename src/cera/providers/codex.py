"""Killable, one-shot Codex Python SDK transport for qualification calls."""

from __future__ import annotations

import inspect
import json
import os
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from types import TracebackType
from typing import Any, Protocol
from urllib.parse import urlparse

from cera.errors import ContractValidationError, ErrorCode
from cera.ids import IdKind, deterministic_id
from cera.provider_dispatch_guard import (
    assert_provider_dispatch_allowed,
    is_external_provider_boundary,
)
from cera.schema import from_mapping
from cera.serialization import canonical_json, text_sha256

from .codex_exec_contract import (
    CODEX_CLI_EXEC_COMPATIBILITY_ID,
    CODEX_CLI_EXEC_CONTRACT_SHA256,
)
from .codex_observability import CodexOperationTelemetryV1
from .codex_runtime_policy import (
    COMPLETED_RESULT_ITEM_TYPE_EVIDENCE,
    validate_codex_prompt_markers,
)
from .codex_sdk_compat import (
    CODEX_SDK_COMPATIBILITY_ID,
    CODEX_SDK_COMPATIBILITY_SOURCE_SHA256,
)
from .models import (
    LiveProviderCallReceipt,
    LiveProviderRoute,
    ModelIdentitySource,
    ProviderCallResult,
    ProviderName,
    ProviderOutputMode,
    ProviderRetryableFailureCategory,
    ProviderTransportError,
)
from .schema_dialects import (
    ProviderSchemaDialect,
    project_provider_output_schema,
)


def _runner_external_provider_boundary(runner: object) -> bool:
    return is_external_provider_boundary(runner)


CODEX_MCP_OBSERVATION_POLICY_STRICT_V1 = "cera.codex_mcp_observation.strict.v1"


@dataclass(frozen=True, slots=True)
class _CodexMcpObservationProjection:
    """Request-bound CERA observations projected from raw Codex telemetry."""

    server_names: tuple[str, ...]
    tool_names: tuple[str, ...]
    tool_call_count: int
    failed_tool_call_count: int


def decode_completed_codex_output[T](
    result: ProviderCallResult,
    *,
    safe_diagnostic: str,
    decoder: Callable[[], T],
) -> T:
    """Decode one completed Codex result at a closed DTO boundary.

    The transport has already proven one provider operation and constructed
    its content-free receipt before this helper is reachable.  Only declared
    contract invalidity from the role-specific decoder is Retry-eligible;
    custody, state, and unexpected implementation failures remain outside this
    conversion and therefore fail closed at their existing boundaries.
    """

    if not isinstance(safe_diagnostic, str) or not safe_diagnostic.strip():
        raise ContractValidationError("completed Codex output diagnostic must be non-empty")
    try:
        return decoder()
    except ContractValidationError:
        failure = ProviderTransportError(
            ErrorCode.REASONER_CONTRACT_INVALID,
            "Codex structured output failed closed DTO validation",
            safe_diagnostics=(safe_diagnostic,),
            external_provider_calls_observed=1,
            provider_call_receipt=result.receipt,
            retryable_failure_category=(ProviderRetryableFailureCategory.PROVIDER_OUTPUT_INVALID),
        )
        failure.mcp_server_names = result.tool_server_names
        failure.mcp_tool_names = result.tool_names
        failure.mcp_tool_call_count = result.tool_call_count
        failure.mcp_failed_tool_call_count = result.failed_tool_call_count
        failure.operation_telemetry = result.operation_telemetry
        raise failure from None


@dataclass(frozen=True, slots=True)
class CodexMcpRuntimeBinding:
    """Ephemeral MCP capability supplied to one Codex worker invocation.

    The bearer token is deliberately excluded from repr and from provider
    receipts. ``binding_sha256`` binds the public request/snapshot/tool policy;
    it must not include the token or the ephemeral loopback port.
    """

    server_name: str
    url: str
    bearer_token_environment_variable: str
    bearer_token: str = field(repr=False)
    enabled_tools: tuple[str, ...] = ()
    binding_sha256: str = ""
    startup_timeout_seconds: int = 10
    tool_timeout_seconds: int = 10
    minimum_tool_calls: int = 0
    maximum_tool_calls: int = 12
    failed_tool_call_provider_request_classifier: (
        Callable[[tuple[str, ...], tuple[str, ...], int, int], bool] | None
    ) = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        parsed = urlparse(self.url)
        if (
            parsed.scheme != "http"
            or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
            or not parsed.port
            or parsed.path != "/mcp"
        ):
            raise ContractValidationError("Codex MCP binding must use loopback HTTP /mcp")
        if not self.server_name.strip() or not self.bearer_token.strip():
            raise ContractValidationError("Codex MCP binding requires server identity and token")
        if self.bearer_token_environment_variable != "CERA_REQUEST_EVIDENCE_TOKEN":
            raise ContractValidationError("Codex MCP token environment variable is not approved")
        if not self.enabled_tools or len(self.enabled_tools) != len(set(self.enabled_tools)):
            raise ContractValidationError("Codex MCP binding requires unique enabled tools")
        if not all(value.strip() for value in self.enabled_tools):
            raise ContractValidationError("Codex MCP tool names must be non-empty")
        if len(self.binding_sha256) != 64 or any(
            value not in "0123456789abcdef" for value in self.binding_sha256
        ):
            raise ContractValidationError("Codex MCP binding hash must be SHA-256")
        if not 1 <= self.startup_timeout_seconds <= 60:
            raise ContractValidationError("Codex MCP startup timeout must be 1..60 seconds")
        if not 1 <= self.tool_timeout_seconds <= 60:
            raise ContractValidationError("Codex MCP tool timeout must be 1..60 seconds")
        if not 0 <= self.minimum_tool_calls <= 32:
            raise ContractValidationError("Codex MCP minimum tool calls must be 0..32")
        if not 1 <= self.maximum_tool_calls <= 32:
            raise ContractValidationError("Codex MCP maximum tool calls must be 1..32")
        if self.minimum_tool_calls > self.maximum_tool_calls:
            raise ContractValidationError("Codex MCP minimum calls exceed maximum calls")
        if self.failed_tool_call_provider_request_classifier is not None and not callable(
            self.failed_tool_call_provider_request_classifier
        ):
            raise ContractValidationError("Codex MCP failure classifier is invalid")

    @property
    def public_descriptor(self) -> dict[str, object]:
        return {
            "server_name": self.server_name,
            "enabled_tools": self.enabled_tools,
            "binding_sha256": self.binding_sha256,
            "required": True,
            "startup_timeout_seconds": self.startup_timeout_seconds,
            "tool_timeout_seconds": self.tool_timeout_seconds,
            "minimum_tool_calls": self.minimum_tool_calls,
            "maximum_tool_calls": self.maximum_tool_calls,
        }


@dataclass(frozen=True, slots=True)
class CodexWorkerResult:
    output_text: str
    provider_request_id: str
    returned_model: str
    duration_ms: int
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    reasoning_output_tokens: int
    transport_version: str
    mcp_server_names: tuple[str, ...] = ()
    mcp_tool_names: tuple[str, ...] = ()
    mcp_tool_call_count: int = 0
    mcp_failed_tool_call_count: int = 0
    unsupported_item_types: tuple[str, ...] = ()
    transport_compatibility_id: str = CODEX_SDK_COMPATIBILITY_ID
    transport_compatibility_source_sha256: str = CODEX_SDK_COMPATIBILITY_SOURCE_SHA256
    transport_compatibility_activated: bool = True
    buffered_early_completion_count: int = 0
    pre_registered_turn_count: int = 0
    operation_telemetry: CodexOperationTelemetryV1 | None = None
    last_step_output_tokens: int | None = None

    def __post_init__(self) -> None:
        counters = (
            self.duration_ms,
            self.input_tokens,
            self.cached_input_tokens,
            self.output_tokens,
            self.reasoning_output_tokens,
            self.mcp_tool_call_count,
            self.mcp_failed_tool_call_count,
            self.buffered_early_completion_count,
            self.pre_registered_turn_count,
        )
        if any(type(value) is not int or value < 0 for value in counters):
            raise ContractValidationError("Codex worker counters cannot be negative")
        if self.last_step_output_tokens is not None and (
            type(self.last_step_output_tokens) is not int
            or self.last_step_output_tokens < 0
            or self.last_step_output_tokens > self.output_tokens
        ):
            raise ContractValidationError("Codex last-step output usage is invalid")
        if len(self.mcp_server_names) != self.mcp_tool_call_count:
            raise ContractValidationError("Codex MCP server observations do not match call count")
        if len(self.mcp_tool_names) != self.mcp_tool_call_count:
            raise ContractValidationError("Codex MCP tool observations do not match call count")
        if self.mcp_failed_tool_call_count > self.mcp_tool_call_count:
            raise ContractValidationError("Codex failed MCP calls exceed total calls")
        if (
            type(self.unsupported_item_types) is not tuple
            or len(self.unsupported_item_types) > len(COMPLETED_RESULT_ITEM_TYPE_EVIDENCE)
            or self.unsupported_item_types != tuple(sorted(set(self.unsupported_item_types)))
            or any(
                value not in COMPLETED_RESULT_ITEM_TYPE_EVIDENCE
                for value in self.unsupported_item_types
            )
        ):
            raise ContractValidationError("Codex unsupported item-type evidence is invalid")
        if self.operation_telemetry is not None:
            telemetry = self.operation_telemetry
            if telemetry.tool_call_count != self.mcp_tool_call_count:
                raise ContractValidationError(
                    "Codex worker telemetry tool count does not match result"
                )
            if (
                telemetry.cumulative_input_tokens != self.input_tokens
                or telemetry.cumulative_cached_input_tokens != self.cached_input_tokens
                or telemetry.cumulative_output_tokens != self.output_tokens
                or telemetry.cumulative_reasoning_tokens != self.reasoning_output_tokens
            ):
                raise ContractValidationError("Codex worker telemetry totals do not match result")
        compatibility = {
            CODEX_SDK_COMPATIBILITY_ID: (
                CODEX_SDK_COMPATIBILITY_SOURCE_SHA256,
                True,
            ),
            CODEX_CLI_EXEC_COMPATIBILITY_ID: (
                CODEX_CLI_EXEC_CONTRACT_SHA256,
                False,
            ),
        }.get(self.transport_compatibility_id)
        if (
            compatibility is None
            or self.transport_compatibility_source_sha256 != compatibility[0]
            or self.transport_compatibility_activated is not True
            or (compatibility[1] and self.pre_registered_turn_count < 1)
            or (
                not compatibility[1]
                and (
                    self.pre_registered_turn_count != 0 or self.buffered_early_completion_count != 0
                )
            )
        ):
            raise ContractValidationError(
                "Codex worker compatibility activation evidence is invalid"
            )


class CodexTransportRunner(Protocol):
    def run(
        self,
        *,
        route: LiveProviderRoute,
        prompt: str,
        output_schema: dict[str, Any],
        workspace: Path,
        mcp_binding: CodexMcpRuntimeBinding | None,
        on_worker_started: Callable[[], None] | None = None,
        on_worker_preflight: Callable[[], None] | None = None,
        on_provider_submit: Callable[[], None] | None = None,
    ) -> CodexWorkerResult: ...


class CodexSDKTransport:
    __slots__ = ("route", "workspace", "runner")

    def __init__(
        self,
        route: LiveProviderRoute,
        *,
        workspace: Path,
        runner: CodexTransportRunner | None = None,
    ) -> None:
        if route.provider is not ProviderName.OPENAI_CODEX:
            raise ContractValidationError("Codex transport requires an OpenAI Codex route")
        if not workspace.is_absolute() or not workspace.is_dir():
            raise ContractValidationError("Codex qualification workspace must already exist")
        if any(workspace.iterdir()):
            raise ContractValidationError("Codex qualification workspace must be empty")
        self.route = route
        self.workspace = workspace
        self.runner = runner or _SubprocessCodexRunner()

    def external_provider_boundary_active(self) -> bool:
        """Return whether the bound runner owns a concrete provider process."""

        return _runner_external_provider_boundary(self.runner)

    @property
    def external_provider_boundary(self) -> bool:
        return self.external_provider_boundary_active()

    def invoke(
        self,
        prompt: str,
        *,
        output_schema: dict[str, Any],
        output_mode: ProviderOutputMode = ProviderOutputMode.JSON_SCHEMA,
        mcp_binding: CodexMcpRuntimeBinding | None = None,
        on_worker_started: Callable[[], None] | None = None,
        on_worker_preflight: Callable[[], None] | None = None,
        on_transport_invoke: Callable[[], None] | None = None,
    ) -> ProviderCallResult:
        if output_mode is not ProviderOutputMode.JSON_SCHEMA:
            raise ContractValidationError("Codex reasoner transport requires JSON Schema output")
        validate_codex_prompt_markers(prompt)
        if not isinstance(output_schema, dict) or not output_schema:
            raise ContractValidationError("Codex invocation requires an output schema")
        assert_provider_dispatch_allowed(
            "providers.codex.sdk_transport",
            external_provider_boundary=self.external_provider_boundary_active(),
        )
        projection = project_provider_output_schema(
            output_schema,
            ProviderSchemaDialect.OPENAI_STRUCTURED_OUTPUT_V1,
        )
        provider_schema = projection.provider_schema
        request_payload = {
            "prompt": prompt,
            "output_schema": provider_schema,
            "mcp_binding": (mcp_binding.public_descriptor if mcp_binding is not None else None),
        }
        request_text = canonical_json(request_payload)
        if len(request_text.encode("utf-8")) > self.route.maximum_request_bytes:
            raise ProviderTransportError(
                ErrorCode.PROVIDER_BUDGET_EXCEEDED,
                "Codex request exceeds the configured byte budget",
            )
        request_sha256 = text_sha256(request_text)
        try:
            parameters = inspect.signature(self.runner.run).parameters
            # Only an explicit callback parameter proves that a runner owns
            # the stage contract.  Generic **kwargs wrappers often forward to
            # legacy runners and must not be treated as stage-aware.
            supports_stages = "on_provider_submit" in parameters
            if supports_stages:
                result = self.runner.run(
                    route=self.route,
                    prompt=prompt,
                    output_schema=provider_schema,
                    workspace=self.workspace,
                    mcp_binding=mcp_binding,
                    on_worker_started=on_worker_started,
                    on_worker_preflight=on_worker_preflight,
                    on_provider_submit=on_transport_invoke,
                )
            else:
                result = self.runner.run(
                    route=self.route,
                    prompt=prompt,
                    output_schema=provider_schema,
                    workspace=self.workspace,
                    mcp_binding=mcp_binding,
                )
            # Older provider-free fakes lack stage callbacks.  A successful
            # result proves one completed provider operation; failures carry
            # external_provider_calls_observed and are reconciled by the ledger.
            if on_transport_invoke is not None:
                on_transport_invoke()
        except ProviderTransportError:
            raise
        except Exception as exc:
            raise ProviderTransportError(
                ErrorCode.REASONER_UNAVAILABLE,
                f"Codex transport failed ({type(exc).__name__})",
            ) from None

        def completed_failure(
            code: ErrorCode,
            message: str,
            *,
            safe_diagnostics: tuple[str, ...] = (),
        ) -> ProviderTransportError:
            completed_transport_failure = ProviderTransportError(
                code,
                message,
                safe_diagnostics=safe_diagnostics,
                external_provider_calls_observed=1,
            )
            if result.operation_telemetry is not None:
                completed_transport_failure.operation_telemetry = (
                    result.operation_telemetry.bind_request(request_sha256).with_transport_error(
                        code.value
                    )
                )
            return completed_transport_failure

        if result.returned_model != self.route.model_name:
            raise completed_failure(
                ErrorCode.REASONER_CONTRACT_INVALID,
                "Codex returned an unexpected model identity",
                safe_diagnostics=("transport:model_identity_mismatch",),
            )
        if result.transport_version != self.route.transport_version:
            raise completed_failure(
                ErrorCode.REASONER_CONTRACT_INVALID,
                "Codex worker runtime version did not match the qualified route",
                safe_diagnostics=("transport:runtime_version_mismatch",),
            )
        if (
            self.route.transport_compatibility_id != result.transport_compatibility_id
            or self.route.transport_compatibility_source_sha256
            != result.transport_compatibility_source_sha256
            or result.transport_compatibility_activated is not True
        ):
            raise completed_failure(
                ErrorCode.REASONER_CONTRACT_INVALID,
                "Codex worker compatibility activation did not match the route",
                safe_diagnostics=("transport:compatibility_activation_mismatch",),
            )
        budget_output_tokens = (
            result.last_step_output_tokens
            if result.last_step_output_tokens is not None
            else result.output_tokens
        )
        if budget_output_tokens > self.route.maximum_output_tokens:
            raise completed_failure(
                ErrorCode.PROVIDER_BUDGET_EXCEEDED,
                "Codex output exceeded the configured token budget",
                safe_diagnostics=("transport:output_token_budget_exceeded",),
            )
        output_sha256 = text_sha256(result.output_text)
        receipt_key = (
            f"{self.route.route_sha256}|{request_sha256}|{output_sha256}|"
            f"{result.provider_request_id}"
        )
        receipt = LiveProviderCallReceipt(
            schema_version=LiveProviderCallReceipt.SCHEMA_VERSION,
            provider_receipt_id=deterministic_id(
                IdKind.PROVIDER_RECEIPT,
                "cera.live_provider.codex.receipt.v2",
                receipt_key,
            ),
            route_sha256=self.route.route_sha256,
            provider=self.route.provider,
            role=self.route.role,
            requested_model=self.route.model_name,
            returned_model=result.returned_model,
            model_revision=self.route.model_revision,
            model_identity_source=ModelIdentitySource.EXPLICIT_REQUEST,
            model_identity_verified=False,
            request_sha256=request_sha256,
            output_sha256=output_sha256,
            provider_request_id_sha256=text_sha256(result.provider_request_id),
            system_fingerprint_sha256=None,
            duration_ms=result.duration_ms,
            input_tokens=result.input_tokens,
            cached_input_tokens=result.cached_input_tokens,
            output_tokens=result.output_tokens,
            reasoning_output_tokens=result.reasoning_output_tokens,
            cost_microusd=0,
            cost_is_estimate=False,
            quota_metered=True,
            external_provider_calls=1,
            automatic_retry_count=0,
            story_authority_writes=0,
            retains_raw_source=False,
            retains_story_prose=False,
            retains_private_evidence=False,
            retains_prompt=False,
            retains_secret=False,
        )
        try:
            self._validate_completed_item_types(result)
            mcp_observations = self._validate_mcp_observations(result, mcp_binding)
        except ProviderTransportError as failure:
            failure.external_provider_calls_observed = 1
            failure.provider_call_receipt = receipt
            failure.mcp_server_names = result.mcp_server_names
            failure.mcp_tool_names = result.mcp_tool_names
            failure.mcp_tool_call_count = result.mcp_tool_call_count
            failure.mcp_failed_tool_call_count = result.mcp_failed_tool_call_count
            if result.operation_telemetry is not None:
                failure.operation_telemetry = result.operation_telemetry.bind_request(
                    request_sha256
                ).with_transport_error(failure.code.value)
            raise
        parse_started_us = time.time_ns() // 1_000
        try:
            parsed = json.loads(result.output_text)
            if not isinstance(parsed, dict):
                raise ProviderTransportError(
                    ErrorCode.REASONER_CONTRACT_INVALID,
                    "Codex schema output was not an object",
                    external_provider_calls_observed=1,
                    provider_call_receipt=receipt,
                    retryable_failure_category=(
                        ProviderRetryableFailureCategory.PROVIDER_OUTPUT_INVALID
                    ),
                )
        except json.JSONDecodeError:
            parse_failure = ProviderTransportError(
                ErrorCode.REASONER_CONTRACT_INVALID,
                "Codex schema output was malformed",
                external_provider_calls_observed=1,
                provider_call_receipt=receipt,
                retryable_failure_category=(
                    ProviderRetryableFailureCategory.PROVIDER_OUTPUT_INVALID
                ),
            )
            if result.operation_telemetry is not None:
                parse_failure.operation_telemetry = (
                    result.operation_telemetry.bind_request(request_sha256)
                    .bind_parse_phase(
                        parse_start_unix_us=parse_started_us,
                        parse_completion_unix_us=time.time_ns() // 1_000,
                    )
                    .with_transport_error(parse_failure.code.value)
                )
            raise parse_failure from None
        except ProviderTransportError as failure:
            failure.external_provider_calls_observed = 1
            failure.provider_call_receipt = receipt
            failure.mcp_server_names = result.mcp_server_names
            failure.mcp_tool_names = result.mcp_tool_names
            failure.mcp_tool_call_count = result.mcp_tool_call_count
            failure.mcp_failed_tool_call_count = result.mcp_failed_tool_call_count
            if result.operation_telemetry is not None:
                failure.operation_telemetry = (
                    result.operation_telemetry.bind_request(request_sha256)
                    .bind_parse_phase(
                        parse_start_unix_us=parse_started_us,
                        parse_completion_unix_us=time.time_ns() // 1_000,
                    )
                    .with_transport_error(failure.code.value)
                )
            raise
        parse_completed_us = time.time_ns() // 1_000
        operation_telemetry = (
            result.operation_telemetry.bind_request(request_sha256).bind_parse_phase(
                parse_start_unix_us=parse_started_us,
                parse_completion_unix_us=parse_completed_us,
            )
            if result.operation_telemetry is not None
            else None
        )
        return ProviderCallResult(
            result.output_text,
            parsed,
            receipt,
            tool_server_names=mcp_observations.server_names,
            tool_names=mcp_observations.tool_names,
            tool_call_count=mcp_observations.tool_call_count,
            failed_tool_call_count=mcp_observations.failed_tool_call_count,
            operation_telemetry=operation_telemetry,
        )

    @staticmethod
    def _validate_completed_item_types(result: CodexWorkerResult) -> None:
        if result.unsupported_item_types:
            raise ProviderTransportError(
                ErrorCode.REASONER_CONTRACT_INVALID,
                "Codex used a completed item outside the bounded CERA transport",
                safe_diagnostics=("transport:unsupported_completed_item_type",),
            )

    def _validate_mcp_observations(
        self,
        result: CodexWorkerResult,
        binding: CodexMcpRuntimeBinding | None,
    ) -> _CodexMcpObservationProjection:
        if binding is None:
            if result.mcp_tool_call_count:
                raise ProviderTransportError(
                    ErrorCode.REASONER_CONTRACT_INVALID,
                    "Codex used an MCP tool without a request-bound bridge",
                )
            return _CodexMcpObservationProjection((), (), 0, 0)
        return self._validate_strict_mcp_observations(result, binding)

    @staticmethod
    def _validate_strict_mcp_observations(
        result: CodexWorkerResult,
        binding: CodexMcpRuntimeBinding,
    ) -> _CodexMcpObservationProjection:
        if result.mcp_tool_call_count < binding.minimum_tool_calls:
            raise ProviderTransportError(
                ErrorCode.REASONER_CONTRACT_INVALID,
                "Codex omitted a required MCP evidence lookup",
                safe_diagnostics=("mcp:required_evidence_lookup_omitted",),
                retryable_failure_category=(
                    ProviderRetryableFailureCategory.PROVIDER_OUTPUT_INVALID
                ),
            )
        if result.mcp_tool_call_count > binding.maximum_tool_calls:
            raise ProviderTransportError(
                ErrorCode.EVIDENCE_LIMIT_EXCEEDED,
                "Codex exceeded the request-bound MCP tool-call budget",
            )
        if any(value != binding.server_name for value in result.mcp_server_names):
            raise ProviderTransportError(
                ErrorCode.REASONER_CONTRACT_INVALID,
                "Codex used an MCP server outside the request-bound bridge",
                safe_diagnostics=("mcp:outside_request_binding",),
                retryable_failure_category=(
                    ProviderRetryableFailureCategory.PROVIDER_OUTPUT_INVALID
                ),
            )
        allowed = set(binding.enabled_tools)
        if any(value not in allowed for value in result.mcp_tool_names):
            raise ProviderTransportError(
                ErrorCode.REASONER_CONTRACT_INVALID,
                "Codex used an MCP tool outside the request allow-list",
                safe_diagnostics=("mcp:outside_request_binding",),
                retryable_failure_category=(
                    ProviderRetryableFailureCategory.PROVIDER_OUTPUT_INVALID
                ),
            )
        projection = _CodexMcpObservationProjection(
            result.mcp_server_names,
            result.mcp_tool_names,
            result.mcp_tool_call_count,
            result.mcp_failed_tool_call_count,
        )
        CodexSDKTransport._raise_for_failed_mcp_observations(projection, binding)
        return projection

    @staticmethod
    def _raise_for_failed_mcp_observations(
        projection: _CodexMcpObservationProjection,
        binding: CodexMcpRuntimeBinding,
    ) -> None:
        if not projection.failed_tool_call_count:
            return
        provider_request_failure = False
        classifier = binding.failed_tool_call_provider_request_classifier
        if classifier is not None:
            try:
                provider_request_failure = (
                    classifier(
                        projection.server_names,
                        projection.tool_names,
                        projection.tool_call_count,
                        projection.failed_tool_call_count,
                    )
                    is True
                )
            except Exception:
                provider_request_failure = False
        if provider_request_failure:
            raise ProviderTransportError(
                ErrorCode.REASONER_CONTRACT_INVALID,
                "Codex MCP request violated the bounded evidence protocol",
                safe_diagnostics=("mcp:provider_request_invalid",),
                retryable_failure_category=(
                    ProviderRetryableFailureCategory.PROVIDER_OUTPUT_INVALID
                ),
            )
        raise ProviderTransportError(
            ErrorCode.EVIDENCE_SERVICE_UNAVAILABLE,
            "Codex MCP evidence lookup failed",
        )


# Provider-neutral name for callers that use either qualified Codex runner.
# The legacy SDK name remains available so historical evidence scripts retain
# their exact imports and behavior.
CodexStructuredOutputTransport = CodexSDKTransport


def _codex_transport_json(value: Any) -> str:
    """Encode the private worker protocol as deterministic ASCII-only JSON."""

    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


class _SubprocessCodexRunner:
    external_provider_boundary = True

    def __init__(
        self,
        *,
        service_tier: str | None = None,
        worker_module: str = "cera.providers.codex_worker",
        provider_thread_id: str | None = None,
        base_instructions: str | None = None,
    ) -> None:
        if service_tier is not None and service_tier != "priority":
            raise ContractValidationError(
                "Codex subprocess service tier must be priority or omitted"
            )
        self.service_tier = service_tier
        if not isinstance(worker_module, str) or not worker_module.strip():
            raise ContractValidationError("Codex worker module is required")
        if provider_thread_id is not None and (
            not isinstance(provider_thread_id, str) or not provider_thread_id.strip()
        ):
            raise ContractValidationError("stored Codex thread ID is invalid")
        if provider_thread_id is not None and (
            not isinstance(base_instructions, str) or not base_instructions.strip()
        ):
            raise ContractValidationError("stored Codex runner requires base instructions")
        if provider_thread_id is None and base_instructions is not None:
            raise ContractValidationError(
                "one-shot Codex runner cannot retain stored base instructions"
            )
        self.worker_module = worker_module
        self.provider_thread_id = provider_thread_id
        self.base_instructions = base_instructions

    def run(
        self,
        *,
        route: LiveProviderRoute,
        prompt: str,
        output_schema: dict[str, Any],
        workspace: Path,
        mcp_binding: CodexMcpRuntimeBinding | None,
        on_worker_started: Callable[[], None] | None = None,
        on_worker_preflight: Callable[[], None] | None = None,
        on_provider_submit: Callable[[], None] | None = None,
    ) -> CodexWorkerResult:
        assert_provider_dispatch_allowed(
            "providers.codex.subprocess_runner",
            external_provider_boundary=is_external_provider_boundary(self),
        )
        progress_path = _initialize_codex_worker_progress(workspace)
        request_payload = {
            "model": route.model_name,
            "effort": route.reasoning_effort,
            "service_tier": self.service_tier,
            "role": route.role.value,
            "prompt": prompt,
            "output_schema": output_schema,
            "workspace": str(workspace),
            "progress_path": str(progress_path),
            "transport_version": route.transport_version,
            "mcp_binding": (
                {
                    **mcp_binding.public_descriptor,
                    "url": mcp_binding.url,
                    "bearer_token_environment_variable": (
                        mcp_binding.bearer_token_environment_variable
                    ),
                    "bearer_token": mcp_binding.bearer_token,
                }
                if mcp_binding is not None
                else None
            ),
        }
        if self.provider_thread_id is not None:
            request_payload["provider_thread_id"] = self.provider_thread_id
            request_payload["base_instructions"] = self.base_instructions
        payload = _codex_transport_json(request_payload)
        started = time.perf_counter()
        popen_kwargs: dict[str, Any] = {}
        if sys.platform == "win32":
            popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            popen_kwargs["start_new_session"] = True
        try:
            process = subprocess.Popen(
                [sys.executable, "-m", self.worker_module],
                stdin=subprocess.PIPE,
                text=True,
                # The private worker protocol is deliberately ASCII-only JSON.
                # Provider prose is escaped by the worker so Windows console code
                # pages cannot corrupt framing between requests.
                encoding="ascii",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                **popen_kwargs,
            )
        except OSError as exc:
            raise ProviderTransportError(
                ErrorCode.REASONER_UNAVAILABLE,
                "Codex qualification worker process could not start",
                safe_diagnostics=(f"transport:process_start:{type(exc).__name__}",),
                external_provider_calls_observed=0,
                retryable_failure_category=(
                    ProviderRetryableFailureCategory.PROVIDER_PROCESS_FAILED
                ),
            ) from None
        if on_worker_started is not None:
            on_worker_started()
        observer_stop, observer = _start_codex_stage_observer(
            progress_path,
            on_worker_preflight=on_worker_preflight,
            on_provider_submit=on_provider_submit,
        )
        try:
            stdout, stderr = process.communicate(
                payload,
                timeout=route.timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            _terminate_codex_worker_tree(process)
            stage = _read_codex_worker_stage(progress_path)
            raise ProviderTransportError(
                ErrorCode.REASONER_UNAVAILABLE,
                "Codex qualification call timed out",
                safe_diagnostics=(
                    "transport:timeout",
                    f"worker_stage:{stage}",
                ),
                external_provider_calls_observed=int(_codex_stage_observed_provider_call(stage)),
                retryable_failure_category=(ProviderRetryableFailureCategory.TRANSPORT_TIMEOUT),
            ) from None
        finally:
            observer_stop.set()
            observer.join(timeout=1)
            _emit_codex_stage_markers(
                _read_codex_worker_stage(progress_path),
                on_worker_preflight=on_worker_preflight,
                on_provider_submit=on_provider_submit,
            )
        if process.returncode != 0:
            stage = _read_codex_worker_stage(progress_path)
            diagnostic = stderr.strip()
            if not diagnostic.startswith("codex qualification worker failed:"):
                diagnostic = "codex qualification worker failed: unknown"
            diagnostic = diagnostic.removeprefix("codex qualification worker failed:").strip()
            raise ProviderTransportError(
                ErrorCode.REASONER_UNAVAILABLE,
                f"Codex qualification worker failed ({diagnostic})",
                safe_diagnostics=(
                    "transport:worker_failure",
                    f"worker_stage:{stage}",
                ),
                external_provider_calls_observed=int(_codex_stage_observed_provider_call(stage)),
            )
        try:
            output = json.loads(stdout)
            output["mcp_server_names"] = tuple(output.get("mcp_server_names", ()))
            output["mcp_tool_names"] = tuple(output.get("mcp_tool_names", ()))
            output["unsupported_item_types"] = tuple(output.get("unsupported_item_types", ()))
            if output.get("operation_telemetry") is not None:
                output["operation_telemetry"] = from_mapping(
                    CodexOperationTelemetryV1,
                    output["operation_telemetry"],
                )
            worker = CodexWorkerResult(**output)
        except (json.JSONDecodeError, TypeError, KeyError, ContractValidationError):
            stage = _read_codex_worker_stage(progress_path)
            raise ProviderTransportError(
                ErrorCode.REASONER_CONTRACT_INVALID,
                "Codex qualification worker returned an invalid envelope",
                safe_diagnostics=(
                    "transport:invalid_worker_envelope",
                    f"worker_stage:{stage}",
                ),
                external_provider_calls_observed=int(_codex_stage_observed_provider_call(stage)),
                retryable_failure_category=(
                    ProviderRetryableFailureCategory.PROVIDER_OUTPUT_INVALID
                ),
            ) from None
        elapsed_ms = max(0, round((time.perf_counter() - started) * 1000))
        if worker.duration_ms > elapsed_ms + 1000:
            stage = _read_codex_worker_stage(progress_path)
            raise ProviderTransportError(
                ErrorCode.REASONER_CONTRACT_INVALID,
                "Codex worker duration was inconsistent",
                safe_diagnostics=(
                    "transport:worker_duration_inconsistent",
                    f"worker_stage:{stage}",
                ),
                external_provider_calls_observed=int(_codex_stage_observed_provider_call(stage)),
            )
        return worker


class StoredCodexThreadRunner(_SubprocessCodexRunner):
    """Run one Reasoner turn on an already allocated stored checkpoint thread."""

    def __init__(
        self,
        provider_thread_id: str,
        *,
        base_instructions: str,
        service_tier: str | None = None,
    ) -> None:
        super().__init__(
            service_tier=service_tier,
            worker_module="cera.providers.codex_stored_turn_worker",
            provider_thread_id=provider_thread_id,
            base_instructions=base_instructions,
        )


class PersistentNoMcpCodexRunner:
    """Supervise bounded no-MCP app-server process epochs for one role identity.

    Calls are serialized.  Every call creates a new ephemeral Codex thread in
    a different isolated workspace.  The governing live evidence qualified two
    requests per process, so a successful two-request epoch is closed before
    the next request is dispatched.  Process rotation is pre-dispatch lifecycle
    management, not a retry or fallback.  A timeout kills the complete worker
    tree and fails that request; this class never resubmits it.
    """

    PROTOCOL_VERSION = "cera.codex_persistent_no_mcp.v1"
    QUALIFIED_MAX_REQUESTS_PER_PROCESS = 2
    external_provider_boundary = True

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._process: subprocess.Popen[str] | None = None
        self._route_identity: tuple[str, str, str | None, str] | None = None
        self._closed = False
        self._process_launch_count = 0
        self._request_submission_count = 0
        self._current_process_submission_count = 0

    def __enter__(self) -> PersistentNoMcpCodexRunner:
        return self

    def __exit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc: BaseException | None,
        _tb: TracebackType | None,
    ) -> None:
        self.close()

    @property
    def process_launch_count(self) -> int:
        with self._lock:
            return self._process_launch_count

    @property
    def request_submission_count(self) -> int:
        with self._lock:
            return self._request_submission_count

    def run(
        self,
        *,
        route: LiveProviderRoute,
        prompt: str,
        output_schema: dict[str, Any],
        workspace: Path,
        mcp_binding: CodexMcpRuntimeBinding | None,
        on_worker_started: Callable[[], None] | None = None,
        on_worker_preflight: Callable[[], None] | None = None,
        on_provider_submit: Callable[[], None] | None = None,
    ) -> CodexWorkerResult:
        assert_provider_dispatch_allowed(
            "providers.codex.persistent_runner",
            external_provider_boundary=is_external_provider_boundary(self),
        )
        if mcp_binding is not None:
            raise ProviderTransportError(
                ErrorCode.REASONER_CONTRACT_INVALID,
                "persistent Codex runner cannot receive request-bound MCP authority",
            )
        identity = (
            route.role.value,
            route.model_name,
            route.reasoning_effort,
            route.transport_version,
        )
        with self._lock:
            if self._closed:
                raise ProviderTransportError(
                    ErrorCode.REASONER_UNAVAILABLE,
                    "persistent Codex runner is closed",
                )
            if self._route_identity is None:
                self._route_identity = identity
            elif self._route_identity != identity:
                raise ProviderTransportError(
                    ErrorCode.REASONER_CONTRACT_INVALID,
                    "persistent Codex runner role/model/effort identity changed",
                )
            progress_path = _initialize_codex_worker_progress(workspace)
            payload = _codex_transport_json(
                {
                    "protocol_version": self.PROTOCOL_VERSION,
                    "model": route.model_name,
                    "effort": route.reasoning_effort,
                    "role": route.role.value,
                    "prompt": prompt,
                    "output_schema": output_schema,
                    "workspace": str(workspace),
                    "progress_path": str(progress_path),
                    "transport_version": route.transport_version,
                    "mcp_binding": None,
                }
            )
            started = time.perf_counter()
            process = self._ensure_process()
            if on_worker_started is not None:
                on_worker_started()
            try:
                assert process.stdin is not None
                process.stdin.write(payload + "\n")
                process.stdin.flush()
                self._request_submission_count += 1
                self._current_process_submission_count += 1
            except (BrokenPipeError, OSError):
                self._terminate_and_forget(process)
                raise ProviderTransportError(
                    ErrorCode.REASONER_UNAVAILABLE,
                    "persistent Codex worker pipe closed before dispatch",
                    safe_diagnostics=(
                        "transport:worker_pipe_closed",
                        f"worker_stage:{_read_codex_worker_stage(progress_path)}",
                    ),
                    retryable_failure_category=(
                        ProviderRetryableFailureCategory.PROVIDER_PROCESS_FAILED
                    ),
                ) from None
            observer_stop, observer = _start_codex_stage_observer(
                progress_path,
                on_worker_preflight=on_worker_preflight,
                on_provider_submit=on_provider_submit,
            )
            try:
                line = self._readline(process, route.timeout_seconds)
            except (UnicodeError, OSError):
                stage = _read_codex_worker_stage(progress_path)
                self._terminate_and_forget(process)
                raise ProviderTransportError(
                    ErrorCode.REASONER_CONTRACT_INVALID,
                    "persistent Codex worker violated the ASCII transport protocol",
                    safe_diagnostics=(
                        "transport:worker_pipe_decode_failed",
                        f"worker_stage:{stage}",
                        "transport_mode:persistent_no_mcp",
                    ),
                    external_provider_calls_observed=int(
                        _codex_stage_observed_provider_call(stage)
                    ),
                    retryable_failure_category=(
                        ProviderRetryableFailureCategory.PROVIDER_STREAM_INCOMPLETE
                    ),
                ) from None
            finally:
                observer_stop.set()
                observer.join(timeout=1)
                _emit_codex_stage_markers(
                    _read_codex_worker_stage(progress_path),
                    on_worker_preflight=on_worker_preflight,
                    on_provider_submit=on_provider_submit,
                )
            if line is None:
                stage = _read_codex_worker_stage(progress_path)
                _terminate_codex_worker_tree(process)
                self._forget_process(process)
                raise ProviderTransportError(
                    ErrorCode.REASONER_UNAVAILABLE,
                    "persistent Codex qualification call timed out",
                    safe_diagnostics=(
                        "transport:timeout",
                        f"worker_stage:{stage}",
                        "transport_mode:persistent_no_mcp",
                    ),
                    external_provider_calls_observed=int(
                        _codex_stage_observed_provider_call(stage)
                    ),
                    retryable_failure_category=(ProviderRetryableFailureCategory.TRANSPORT_TIMEOUT),
                ) from None
            try:
                envelope = json.loads(line)
            except json.JSONDecodeError:
                stage = _read_codex_worker_stage(progress_path)
                self._terminate_and_forget(process)
                raise ProviderTransportError(
                    ErrorCode.REASONER_CONTRACT_INVALID,
                    "persistent Codex worker returned an invalid envelope",
                    safe_diagnostics=(
                        "transport:invalid_worker_envelope",
                        f"worker_stage:{stage}",
                        "transport_mode:persistent_no_mcp",
                    ),
                    external_provider_calls_observed=int(
                        _codex_stage_observed_provider_call(stage)
                    ),
                    retryable_failure_category=(
                        ProviderRetryableFailureCategory.PROVIDER_OUTPUT_INVALID
                        if line
                        else ProviderRetryableFailureCategory.PROVIDER_STREAM_INCOMPLETE
                    ),
                ) from None
            if not isinstance(envelope, dict) or envelope.get("ok") is not True:
                stage = _read_codex_worker_stage(progress_path)
                self._terminate_and_forget(process)
                raise ProviderTransportError(
                    ErrorCode.REASONER_UNAVAILABLE,
                    "persistent Codex qualification worker failed",
                    safe_diagnostics=(
                        "transport:worker_failure",
                        f"worker_stage:{stage}",
                        "transport_mode:persistent_no_mcp",
                    ),
                    external_provider_calls_observed=int(
                        _codex_stage_observed_provider_call(stage)
                    ),
                ) from None
            try:
                output = envelope["result"]
                if not isinstance(output, dict):
                    raise TypeError
                output["mcp_server_names"] = tuple(output.get("mcp_server_names", ()))
                output["mcp_tool_names"] = tuple(output.get("mcp_tool_names", ()))
                output["unsupported_item_types"] = tuple(output.get("unsupported_item_types", ()))
                if output.get("operation_telemetry") is not None:
                    output["operation_telemetry"] = from_mapping(
                        CodexOperationTelemetryV1,
                        output["operation_telemetry"],
                    )
                worker = CodexWorkerResult(**output)
            except (TypeError, KeyError, ContractValidationError):
                stage = _read_codex_worker_stage(progress_path)
                self._terminate_and_forget(process)
                raise ProviderTransportError(
                    ErrorCode.REASONER_CONTRACT_INVALID,
                    "persistent Codex worker returned an invalid result",
                    safe_diagnostics=(
                        "transport:invalid_worker_result",
                        f"worker_stage:{stage}",
                        "transport_mode:persistent_no_mcp",
                    ),
                    external_provider_calls_observed=int(
                        _codex_stage_observed_provider_call(stage)
                    ),
                    retryable_failure_category=(
                        ProviderRetryableFailureCategory.PROVIDER_OUTPUT_INVALID
                    ),
                ) from None
            elapsed_ms = max(0, round((time.perf_counter() - started) * 1000))
            if worker.duration_ms > elapsed_ms + 1000:
                stage = _read_codex_worker_stage(progress_path)
                self._terminate_and_forget(process)
                raise ProviderTransportError(
                    ErrorCode.REASONER_CONTRACT_INVALID,
                    "persistent Codex worker duration was inconsistent",
                    safe_diagnostics=(
                        "transport:worker_duration_inconsistent",
                        f"worker_stage:{stage}",
                        "transport_mode:persistent_no_mcp",
                    ),
                    external_provider_calls_observed=int(
                        _codex_stage_observed_provider_call(stage)
                    ),
                )
        return worker

    def close(self) -> None:
        with self._lock:
            self._closed = True
            process = self._process
            self._process = None
            if process is None:
                return
            # Closing stdin proves only that the Python worker exited; on
            # Windows its app-server child can survive the parent.  Always
            # terminate the complete process tree at a completed request
            # boundary so no provider process outlives the runner.
            _terminate_codex_worker_tree(process)
            self._current_process_submission_count = 0

    def _ensure_process(self) -> subprocess.Popen[str]:
        assert_provider_dispatch_allowed(
            "providers.codex.persistent_process_start",
            external_provider_boundary=is_external_provider_boundary(self),
        )
        if self._process is not None and self._process.poll() is None:
            if self._current_process_submission_count < self.QUALIFIED_MAX_REQUESTS_PER_PROCESS:
                return self._process
            self._close_process_epoch(self._process)
        if self._process is not None:
            self._process = None
            self._closed = True
            raise ProviderTransportError(
                ErrorCode.REASONER_UNAVAILABLE,
                "persistent Codex worker exited between requests",
                retryable_failure_category=(
                    ProviderRetryableFailureCategory.PROVIDER_PROCESS_FAILED
                ),
            )
        popen_kwargs: dict[str, Any] = {}
        if sys.platform == "win32":
            popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            popen_kwargs["start_new_session"] = True
        try:
            self._process = subprocess.Popen(
                [sys.executable, "-m", "cera.providers.codex_session_worker"],
                stdin=subprocess.PIPE,
                text=True,
                encoding="ascii",
                stdout=subprocess.PIPE,
                # The persistent worker and its app-server children can emit
                # diagnostics for the lifetime of the process.  No reader consumed
                # this pipe, so enough output could block the child indefinitely.
                # Diagnostics are deliberately discarded because they may contain
                # request material; the privacy-safe progress sidecar is the
                # supported failure channel.
                stderr=subprocess.DEVNULL,
                bufsize=1,
                **popen_kwargs,
            )
        except OSError as exc:
            raise ProviderTransportError(
                ErrorCode.REASONER_UNAVAILABLE,
                "persistent Codex worker process could not start",
                safe_diagnostics=(f"transport:process_start:{type(exc).__name__}",),
                external_provider_calls_observed=0,
                retryable_failure_category=(
                    ProviderRetryableFailureCategory.PROVIDER_PROCESS_FAILED
                ),
            ) from None
        self._process_launch_count += 1
        self._current_process_submission_count = 0
        return self._process

    def _close_process_epoch(self, process: subprocess.Popen[str]) -> None:
        """Terminate a successful epoch's full tree before a later dispatch."""

        terminated = _terminate_codex_worker_tree(process)
        if self._process is process:
            self._process = None
        self._current_process_submission_count = 0
        if not terminated:
            self._closed = True
            raise ProviderTransportError(
                ErrorCode.REASONER_UNAVAILABLE,
                "persistent Codex process epoch cleanup failed",
                safe_diagnostics=(
                    "transport:process_epoch_cleanup_failed",
                    "transport_mode:persistent_no_mcp",
                ),
                external_provider_calls_observed=0,
                retryable_failure_category=(
                    ProviderRetryableFailureCategory.PROVIDER_PROCESS_FAILED
                ),
            )

    @staticmethod
    def _readline(process: subprocess.Popen[str], timeout_seconds: int) -> str | None:
        stdout = process.stdout
        assert stdout is not None
        result: list[str] = []
        errors: list[BaseException] = []

        def read() -> None:
            try:
                result.append(stdout.readline())
            except BaseException as exc:  # propagated on the requesting thread
                errors.append(exc)

        reader = threading.Thread(target=read, daemon=True)
        reader.start()
        reader.join(timeout_seconds)
        if reader.is_alive():
            return None
        if errors:
            raise errors[0]
        return result[0] if result else ""

    def _terminate_and_forget(self, process: subprocess.Popen[str]) -> None:
        _terminate_codex_worker_tree(process)
        self._forget_process(process)

    def _forget_process(self, process: subprocess.Popen[str]) -> None:
        if self._process is process:
            self._process = None
        self._current_process_submission_count = 0
        self._closed = True


def _initialize_codex_worker_progress(workspace: Path) -> Path:
    progress_path = workspace / ".cera_codex_worker_progress.json"
    if progress_path.exists():
        raise ProviderTransportError(
            ErrorCode.REASONER_CONTRACT_INVALID,
            "Codex qualification progress sidecar already exists",
        )
    progress_path.write_text(
        canonical_json(
            {
                "schema_version": "cera.codex_worker_progress.v1",
                "stage": "worker_launch",
            }
        ),
        encoding="utf-8",
    )
    return progress_path


_CODEX_WORKER_PROGRESS_STAGES = frozenset(
    {
        "worker_launch",
        "request_decode",
        "sdk_import",
        "transport_version_check",
        "sdk_start",
        "sdk_compatibility_install",
        "account_check",
        "thread_start",
        "thread_resume",
        "thread_run",
        "thread_read",
        "model_provider_check",
        "cli_version_check",
        "result_check",
        "response_encode",
    }
)

_CODEX_PROVIDER_CALL_OBSERVED_STAGES = frozenset(
    {
        "thread_run",
        "thread_read",
        "model_provider_check",
        "cli_version_check",
        "result_check",
        "response_encode",
    }
)


def _codex_stage_observed_provider_call(stage: str) -> bool:
    return stage in _CODEX_PROVIDER_CALL_OBSERVED_STAGES


def _emit_codex_stage_markers(
    stage: str,
    *,
    on_worker_preflight: Callable[[], None] | None,
    on_provider_submit: Callable[[], None] | None,
) -> None:
    if stage in _CODEX_WORKER_PROGRESS_STAGES and stage != "worker_launch":
        if on_worker_preflight is not None:
            on_worker_preflight()
    if _codex_stage_observed_provider_call(stage) and on_provider_submit is not None:
        on_provider_submit()


def _start_codex_stage_observer(
    progress_path: Path,
    *,
    on_worker_preflight: Callable[[], None] | None,
    on_provider_submit: Callable[[], None] | None,
) -> tuple[threading.Event, threading.Thread]:
    stop = threading.Event()

    def observe() -> None:
        prior = ""
        while not stop.wait(0.01):
            stage = _read_codex_worker_stage(progress_path)
            if stage == prior:
                continue
            prior = stage
            _emit_codex_stage_markers(
                stage,
                on_worker_preflight=on_worker_preflight,
                on_provider_submit=on_provider_submit,
            )

    thread = threading.Thread(target=observe, daemon=True)
    thread.start()
    return stop, thread


def _read_codex_worker_stage(progress_path: Path) -> str:
    """Read only the allow-listed stage name from a worker progress sidecar."""

    try:
        payload = json.loads(progress_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "worker_launch"
    if not isinstance(payload, dict) or set(payload) != {"schema_version", "stage"}:
        return "worker_launch"
    if payload.get("schema_version") != "cera.codex_worker_progress.v1":
        return "worker_launch"
    stage = payload.get("stage")
    return stage if stage in _CODEX_WORKER_PROGRESS_STAGES else "worker_launch"


def _terminate_codex_worker_tree(process: subprocess.Popen[Any]) -> bool:
    """Terminate the timed-out worker and every SDK/app-server child it spawned."""

    tree_termination_requested = False
    if sys.platform == "win32":
        completed = subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        tree_termination_requested = completed.returncode == 0
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
            tree_termination_requested = True
        except ProcessLookupError:
            tree_termination_requested = process.poll() is not None
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)
    return tree_termination_requested
