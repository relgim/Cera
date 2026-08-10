"""Typed, promotion-neutral contracts for bounded live-provider calls."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Any, ClassVar
from urllib.parse import urlparse

from cera.errors import ContractValidationError, ErrorCode
from cera.evaluation import EvaluationRole, RouteIdentity, RouteKind
from cera.ids import IdKind, TypedId, require_kind
from cera.schema import require_schema
from cera.serialization import domain_sha256, re_is_sha256, text_sha256

from .codex_observability import CodexOperationTelemetryV1


class ProviderName(StrEnum):
    OPENAI_CODEX = "openai_codex"
    DEEPSEEK = "deepseek"


class ProviderAuthMode(StrEnum):
    CHATGPT_SESSION = "chatgpt_session"
    ENVIRONMENT_API_KEY = "environment_api_key"


class ProviderOutputMode(StrEnum):
    TEXT = "text"
    JSON_OBJECT = "json_object"
    JSON_SCHEMA = "json_schema"


class ModelIdentitySource(StrEnum):
    EXPLICIT_REQUEST = "explicit_request"
    PROVIDER_RESPONSE = "provider_response"


class ProviderFinishReason(StrEnum):
    """Normalized completion reasons documented by the DeepSeek API."""

    STOP = "stop"
    LENGTH = "length"
    CONTENT_FILTER = "content_filter"
    TOOL_CALLS = "tool_calls"
    INSUFFICIENT_SYSTEM_RESOURCE = "insufficient_system_resource"


class ProviderResponseFailureKind(StrEnum):
    NON_STOP_FINISH = "non_stop_finish"
    EMPTY_CONTENT = "empty_content"
    MALFORMED_JSON = "malformed_json"
    NON_OBJECT_JSON = "non_object_json"
    COST_CEILING_EXCEEDED = "cost_ceiling_exceeded"


class ProviderRetryableFailureCategory(StrEnum):
    """Closed provider-boundary failures eligible for stage-local Retry.

    Absence of this metadata is intentional and means that the failure is not
    known to be Retry-eligible. ``provider_output_invalid`` is limited to a
    deterministically received, closed provider or private-transport envelope;
    it is never inferred from a generic worker exception. Dispatch ambiguity
    is an executor/ledger disposition and therefore is deliberately not
    representable here.
    """

    TRANSPORT_TIMEOUT = "transport_timeout"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    PROVIDER_PROCESS_FAILED = "provider_process_failed"
    PROVIDER_STREAM_INCOMPLETE = "provider_stream_incomplete"
    PROVIDER_COMPLETION_INCOMPLETE = "provider_completion_incomplete"
    PROVIDER_OUTPUT_INVALID = "provider_output_invalid"


_PROVIDER_ROLES = {
    ProviderName.OPENAI_CODEX: frozenset(
        {
            EvaluationRole.SCENE_REASONER,
            EvaluationRole.SCENE_REALIZATION_VERIFIER,
            EvaluationRole.DERIVED_CONSOLIDATOR,
        }
    ),
    ProviderName.DEEPSEEK: frozenset(
        {EvaluationRole.SCENE_COMPOSER, EvaluationRole.ADULT_MECHANICS}
    ),
}
_DEEPSEEK_MODELS = frozenset({"deepseek-v4-flash", "deepseek-v4-pro"})
_CODEX_MODELS = frozenset({"gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"})


@dataclass(frozen=True, slots=True)
class ProviderPricing:
    cache_hit_input_microusd_per_million: int
    cache_miss_input_microusd_per_million: int
    output_microusd_per_million: int
    source_url: str
    effective_date: str

    def __post_init__(self) -> None:
        values = (
            self.cache_hit_input_microusd_per_million,
            self.cache_miss_input_microusd_per_million,
            self.output_microusd_per_million,
        )
        if any(type(value) is not int or value < 0 for value in values):
            raise ContractValidationError("provider prices must be non-negative integers")
        parsed = urlparse(self.source_url)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ContractValidationError("provider pricing requires an HTTPS source URL")
        try:
            date.fromisoformat(self.effective_date)
        except ValueError:
            raise ContractValidationError("provider pricing requires an ISO date") from None

    def estimate_cost_microusd(
        self,
        *,
        cache_hit_input_tokens: int,
        cache_miss_input_tokens: int,
        output_tokens: int,
    ) -> int:
        counts = (cache_hit_input_tokens, cache_miss_input_tokens, output_tokens)
        if any(type(value) is not int or value < 0 for value in counts):
            raise ContractValidationError("provider token counts cannot be negative")
        numerator = (
            cache_hit_input_tokens * self.cache_hit_input_microusd_per_million
            + cache_miss_input_tokens * self.cache_miss_input_microusd_per_million
            + output_tokens * self.output_microusd_per_million
        )
        return (numerator + 999_999) // 1_000_000


@dataclass(frozen=True, slots=True)
class LiveProviderRoute:
    SCHEMA_VERSION: ClassVar[str] = "cera.live_provider_route.v2"

    schema_version: str
    route_id: str
    role: EvaluationRole
    provider: ProviderName
    adapter_id: str
    model_name: str
    model_revision: str
    prompt_version: str
    transport_name: str
    transport_version: str
    auth_mode: ProviderAuthMode
    endpoint: str | None
    credential_environment_variable: str | None
    reasoning_effort: str | None
    timeout_seconds: int
    maximum_request_bytes: int
    maximum_output_tokens: int
    maximum_cost_microusd: int
    automatic_retry_count: int
    fallback_enabled: bool
    production_enabled: bool
    pricing: ProviderPricing | None
    transport_compatibility_id: str | None = None
    transport_compatibility_source_sha256: str | None = None

    def __post_init__(self) -> None:
        require_schema(self.schema_version, self.SCHEMA_VERSION, type(self).__name__)
        for value, label in (
            (self.route_id, "route_id"),
            (self.adapter_id, "adapter_id"),
            (self.model_name, "model_name"),
            (self.model_revision, "model_revision"),
            (self.prompt_version, "prompt_version"),
            (self.transport_name, "transport_name"),
            (self.transport_version, "transport_version"),
        ):
            _non_empty(value, label)
        if self.role not in _PROVIDER_ROLES[self.provider]:
            raise ContractValidationError("provider is not assigned to this runtime role")
        if self.automatic_retry_count != 0 or self.fallback_enabled:
            raise ContractValidationError("CERA live routes prohibit automatic retry and fallback")
        if self.production_enabled:
            raise ContractValidationError("qualification routes cannot enable production")
        if not 1 <= self.timeout_seconds <= 600:
            raise ContractValidationError("provider timeout must be 1..600 seconds")
        if not 256 <= self.maximum_request_bytes <= 4_194_304:
            raise ContractValidationError("provider request budget is invalid")
        if not 1 <= self.maximum_output_tokens <= 131_072:
            raise ContractValidationError("provider output-token budget is invalid")
        if type(self.maximum_cost_microusd) is not int or self.maximum_cost_microusd < 0:
            raise ContractValidationError("provider cost budget cannot be negative")

        if self.provider is ProviderName.OPENAI_CODEX:
            if self.auth_mode is not ProviderAuthMode.CHATGPT_SESSION:
                raise ContractValidationError("Codex qualification must use ChatGPT session auth")
            if self.endpoint is not None or self.credential_environment_variable is not None:
                raise ContractValidationError("Codex session routes cannot embed endpoint credentials")
            if self.model_name not in _CODEX_MODELS:
                raise ContractValidationError("unqualified Codex model family")
            if self.reasoning_effort not in {"low", "medium", "high", "xhigh", "max"}:
                raise ContractValidationError("Codex route requires an explicit supported effort")
            if self.pricing is not None:
                raise ContractValidationError("ChatGPT-authenticated Codex is quota, not API priced")
            if not (
                isinstance(self.transport_compatibility_id, str)
                and self.transport_compatibility_id.strip()
                and isinstance(
                    self.transport_compatibility_source_sha256,
                    str,
                )
                and re_is_sha256(
                    self.transport_compatibility_source_sha256
                )
            ):
                raise ContractValidationError(
                    "Codex route requires pinned transport compatibility evidence"
                )
        else:
            if self.auth_mode is not ProviderAuthMode.ENVIRONMENT_API_KEY:
                raise ContractValidationError("DeepSeek qualification requires environment-key auth")
            if self.endpoint != "https://api.deepseek.com":
                raise ContractValidationError("DeepSeek qualification endpoint is not approved")
            if self.credential_environment_variable != "DEEPSEEK_API_KEY":
                raise ContractValidationError("DeepSeek credential source must be DEEPSEEK_API_KEY")
            if self.model_name not in _DEEPSEEK_MODELS:
                raise ContractValidationError("legacy or unknown DeepSeek model is prohibited")
            if self.reasoning_effort is not None:
                raise ContractValidationError("DeepSeek Composer route uses explicit thinking mode instead")
            if self.pricing is None:
                raise ContractValidationError("paid DeepSeek routes require a dated price schedule")
            if (
                self.transport_compatibility_id is not None
                or self.transport_compatibility_source_sha256 is not None
            ):
                raise ContractValidationError(
                    "DeepSeek route cannot claim Codex transport compatibility"
                )

    @property
    def route_sha256(self) -> str:
        return domain_sha256("cera.live_provider_route.v2", self)

    def evaluation_identity(self) -> RouteIdentity:
        return RouteIdentity(
            role=self.role,
            route_kind=RouteKind.LIVE_PROVIDER,
            adapter_id=self.adapter_id,
            model_name=self.model_name,
            model_revision=self.model_revision,
            prompt_version=self.prompt_version,
            transport_version=self.transport_version,
            configuration_sha256=self.route_sha256,
        )


@dataclass(frozen=True, slots=True)
class LiveProviderCallReceipt:
    SCHEMA_VERSION: ClassVar[str] = "cera.live_provider_call_receipt.v2"

    schema_version: str
    provider_receipt_id: TypedId
    route_sha256: str
    provider: ProviderName
    role: EvaluationRole
    requested_model: str
    returned_model: str
    model_revision: str
    model_identity_source: ModelIdentitySource
    model_identity_verified: bool
    request_sha256: str
    output_sha256: str
    provider_request_id_sha256: str | None
    system_fingerprint_sha256: str | None
    duration_ms: int
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    reasoning_output_tokens: int
    cost_microusd: int
    cost_is_estimate: bool
    quota_metered: bool
    external_provider_calls: int
    automatic_retry_count: int
    story_authority_writes: int
    retains_raw_source: bool
    retains_story_prose: bool
    retains_private_evidence: bool
    retains_prompt: bool
    retains_secret: bool

    def __post_init__(self) -> None:
        require_schema(self.schema_version, self.SCHEMA_VERSION, type(self).__name__)
        require_kind(self.provider_receipt_id, IdKind.PROVIDER_RECEIPT, "provider_receipt_id")
        for value in (self.route_sha256, self.request_sha256, self.output_sha256):
            _sha256(value, "provider receipt hash")
        for value in (self.provider_request_id_sha256, self.system_fingerprint_sha256):
            if value is not None:
                _sha256(value, "provider metadata hash")
        for value, label in (
            (self.requested_model, "requested_model"),
            (self.returned_model, "returned_model"),
            (self.model_revision, "model_revision"),
        ):
            _non_empty(value, label)
        counters = (
            self.duration_ms,
            self.input_tokens,
            self.cached_input_tokens,
            self.output_tokens,
            self.reasoning_output_tokens,
            self.cost_microusd,
            self.external_provider_calls,
            self.automatic_retry_count,
            self.story_authority_writes,
        )
        if any(type(value) is not int or value < 0 for value in counters):
            raise ContractValidationError("provider receipt counters cannot be negative")
        if self.cached_input_tokens > self.input_tokens:
            raise ContractValidationError("cached input exceeds total input")
        if self.role not in _PROVIDER_ROLES[self.provider]:
            raise ContractValidationError("provider receipt role is not assigned to provider")
        if self.requested_model != self.returned_model:
            raise ContractValidationError("provider receipt model identities do not match")
        if self.external_provider_calls != 1 or self.automatic_retry_count != 0:
            raise ContractValidationError("live receipt must represent exactly one unretried call")
        if self.story_authority_writes != 0:
            raise ContractValidationError("provider calls cannot write story authority")
        if any(
            (
                self.retains_raw_source,
                self.retains_story_prose,
                self.retains_private_evidence,
                self.retains_prompt,
                self.retains_secret,
            )
        ):
            raise ContractValidationError("provider receipt cannot retain protected content")
        if self.provider is ProviderName.OPENAI_CODEX:
            if not self.quota_metered or self.cost_microusd != 0 or self.cost_is_estimate:
                raise ContractValidationError("ChatGPT Codex receipts use quota, not API cost")
            if (
                self.model_identity_source is not ModelIdentitySource.EXPLICIT_REQUEST
                or self.model_identity_verified
            ):
                raise ContractValidationError(
                    "Codex app-server receipts must qualify model identity as request-bound"
                )
        else:
            if self.quota_metered or not self.cost_is_estimate:
                raise ContractValidationError("DeepSeek receipt requires estimated API cost")
            if (
                self.model_identity_source is not ModelIdentitySource.PROVIDER_RESPONSE
                or not self.model_identity_verified
            ):
                raise ContractValidationError("DeepSeek model identity must come from its response")

    @property
    def receipt_sha256(self) -> str:
        return domain_sha256("cera.live_provider_call_receipt.v2", self)

    # Read-only aliases for historical telemetry consumers. Constructor names
    # intentionally use "retains" so they cannot be confused with what the
    # provider request necessarily contained.
    @property
    def raw_source_included(self) -> bool:
        return self.retains_raw_source

    @property
    def story_prose_included(self) -> bool:
        return self.retains_story_prose

    @property
    def private_evidence_included(self) -> bool:
        return self.retains_private_evidence

    @property
    def prompt_included(self) -> bool:
        return self.retains_prompt

    @property
    def secret_included(self) -> bool:
        return self.retains_secret


@dataclass(frozen=True, slots=True)
class ProviderFailureCallReceipt:
    """Safe evidence for a completed provider response rejected by CERA.

    The nested call receipt preserves timing, usage, model identity, cost, and
    request/output hashes.  This wrapper adds only bounded provider metadata;
    it never retains response text, reasoning, prompts, or evidence.
    """

    SCHEMA_VERSION: ClassVar[str] = "cera.provider_failure_call_receipt.v1"

    schema_version: str
    provider_receipt_id: TypedId
    call_receipt: LiveProviderCallReceipt
    finish_reason: ProviderFinishReason
    failure_kind: ProviderResponseFailureKind
    completion_accepted: bool

    def __post_init__(self) -> None:
        require_schema(self.schema_version, self.SCHEMA_VERSION, type(self).__name__)
        require_kind(
            self.provider_receipt_id,
            IdKind.PROVIDER_RECEIPT,
            "provider_receipt_id",
        )
        if self.completion_accepted:
            raise ContractValidationError(
                "provider failure receipt cannot claim accepted completion"
            )
        if (
            self.failure_kind is ProviderResponseFailureKind.NON_STOP_FINISH
            and self.finish_reason is ProviderFinishReason.STOP
        ):
            raise ContractValidationError(
                "non-stop failure receipt requires a non-stop finish reason"
            )
        if (
            self.failure_kind
            in {
                ProviderResponseFailureKind.EMPTY_CONTENT,
                ProviderResponseFailureKind.MALFORMED_JSON,
                ProviderResponseFailureKind.NON_OBJECT_JSON,
            }
            and self.finish_reason is not ProviderFinishReason.STOP
        ):
            raise ContractValidationError(
                "content-shape failure receipt requires a stop finish reason"
            )

    @property
    def receipt_sha256(self) -> str:
        return domain_sha256(self.SCHEMA_VERSION, self)

    @property
    def output_sha256(self) -> str:
        return self.call_receipt.output_sha256

    @property
    def requested_model(self) -> str:
        return self.call_receipt.requested_model

    @property
    def returned_model(self) -> str:
        return self.call_receipt.returned_model

    @property
    def external_provider_calls(self) -> int:
        return self.call_receipt.external_provider_calls


@dataclass(frozen=True, slots=True)
class ProviderCallResult:
    output_text: str
    parsed_json: dict[str, Any] | None
    receipt: LiveProviderCallReceipt
    tool_server_names: tuple[str, ...] = ()
    tool_names: tuple[str, ...] = ()
    tool_call_count: int = 0
    failed_tool_call_count: int = 0
    operation_telemetry: CodexOperationTelemetryV1 | None = None

    def __post_init__(self) -> None:
        _non_empty(self.output_text, "provider output")
        if text_sha256(self.output_text) != self.receipt.output_sha256:
            raise ContractValidationError("provider result does not match receipt output hash")
        if min(self.tool_call_count, self.failed_tool_call_count) < 0:
            raise ContractValidationError("provider tool-call counters cannot be negative")
        if len(self.tool_server_names) != self.tool_call_count:
            raise ContractValidationError("provider tool servers do not match call count")
        if len(self.tool_names) != self.tool_call_count:
            raise ContractValidationError("provider tool names do not match call count")
        if self.failed_tool_call_count > self.tool_call_count:
            raise ContractValidationError("failed provider tool calls exceed total calls")
        if self.operation_telemetry is not None:
            if self.receipt.provider is not ProviderName.OPENAI_CODEX:
                raise ContractValidationError(
                    "Codex operation telemetry cannot attach to another provider"
                )
            if self.operation_telemetry.request_sha256 != self.receipt.request_sha256:
                raise ContractValidationError(
                    "Codex operation telemetry request does not match receipt"
                )
            totals = (
                self.operation_telemetry.cumulative_input_tokens,
                self.operation_telemetry.cumulative_cached_input_tokens,
                self.operation_telemetry.cumulative_output_tokens,
                self.operation_telemetry.cumulative_reasoning_tokens,
            )
            receipt_totals = (
                self.receipt.input_tokens,
                self.receipt.cached_input_tokens,
                self.receipt.output_tokens,
                self.receipt.reasoning_output_tokens,
            )
            if totals != receipt_totals:
                raise ContractValidationError(
                    "Codex operation telemetry totals do not match receipt"
                )


class ProviderTransportError(Exception):
    """Stable transport failure with no provider payload or secret retention."""

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        safe_diagnostics: tuple[str, ...] = (),
        external_provider_calls_observed: int = 0,
        provider_call_receipt: (
            LiveProviderCallReceipt | ProviderFailureCallReceipt | None
        ) = None,
        retryable_failure_category: ProviderRetryableFailureCategory | None = None,
    ) -> None:
        self.code = code
        self.provider_call_receipt = provider_call_receipt
        if retryable_failure_category is not None and type(
            retryable_failure_category
        ) is not ProviderRetryableFailureCategory:
            raise ContractValidationError(
                "transport failure Retry category must be an exact provider enum"
            )
        self.retryable_failure_category = retryable_failure_category
        if (
            type(external_provider_calls_observed) is not int
            or external_provider_calls_observed not in {0, 1}
        ):
            raise ContractValidationError(
                "transport failure provider-call observation must be zero or one"
            )
        if any(
            not isinstance(value, str) or not value.strip()
            for value in safe_diagnostics
        ):
            raise ContractValidationError(
                "transport failure diagnostics must be non-empty strings"
            )
        self.safe_diagnostics = tuple(safe_diagnostics)
        self.external_provider_calls_observed = external_provider_calls_observed
        if provider_call_receipt is not None and external_provider_calls_observed != 1:
            raise ContractValidationError(
                "transport failure receipt requires one observed provider call"
            )
        # Privacy-safe transport observations survive typed/tool failure. They
        # contain tool identities and counters only, never argument values or
        # provider output.
        self.mcp_server_names: tuple[str, ...] = ()
        self.mcp_tool_names: tuple[str, ...] = ()
        self.mcp_tool_call_count = 0
        self.mcp_failed_tool_call_count = 0
        self.operation_telemetry: CodexOperationTelemetryV1 | None = None
        super().__init__(message)


def _non_empty(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ContractValidationError(f"{field_name} must be non-empty")


def _sha256(value: str, field_name: str) -> None:
    if not re_is_sha256(value):
        raise ContractValidationError(f"{field_name} must be SHA-256")
