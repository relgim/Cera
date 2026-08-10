"""One-shot DeepSeek Chat Completions transport for live qualification."""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from cera.errors import ContractValidationError, ErrorCode
from cera.evaluation import EvaluationRole
from cera.ids import IdKind, deterministic_id
from cera.provider_dispatch_guard import (
    assert_provider_dispatch_allowed,
    is_external_provider_boundary,
)
from cera.serialization import canonical_json, text_sha256

from .models import (
    LiveProviderCallReceipt,
    LiveProviderRoute,
    ModelIdentitySource,
    ProviderCallResult,
    ProviderFailureCallReceipt,
    ProviderFinishReason,
    ProviderName,
    ProviderOutputMode,
    ProviderResponseFailureKind,
    ProviderRetryableFailureCategory,
    ProviderTransportError,
)


@dataclass(frozen=True, slots=True)
class DeepSeekMessage:
    role: str
    content: str

    def __post_init__(self) -> None:
        if self.role not in {"system", "user", "assistant"}:
            raise ContractValidationError("DeepSeek message role is invalid")
        if not self.content.strip():
            raise ContractValidationError("DeepSeek message content must be non-empty")


class DeepSeekChatTransport:
    external_provider_boundary = True

    def __init__(
        self,
        route: LiveProviderRoute,
        *,
        opener: Callable[..., Any] = urllib.request.urlopen,
        environment: dict[str, str] | None = None,
    ) -> None:
        if route.provider is not ProviderName.DEEPSEEK:
            raise ContractValidationError("DeepSeek transport requires a DeepSeek route")
        self.route = route
        self._opener = opener
        self._environment = os.environ if environment is None else environment

    def external_provider_boundary_active(self) -> bool:
        """Return whether this transport owns the production HTTP boundary."""

        return is_external_provider_boundary(self)

    def invoke(
        self,
        messages: tuple[DeepSeekMessage, ...],
        *,
        output_mode: ProviderOutputMode = ProviderOutputMode.TEXT,
        thinking_enabled: bool = False,
        on_transport_invoke: Callable[[], None] | None = None,
    ) -> ProviderCallResult:
        if not messages:
            raise ContractValidationError("DeepSeek invocation requires messages")
        if output_mode is ProviderOutputMode.JSON_SCHEMA:
            raise ContractValidationError("DeepSeek Chat transport does not claim strict JSON Schema")
        assert_provider_dispatch_allowed(
            "providers.deepseek.chat_completions",
            external_provider_boundary=self.external_provider_boundary_active(),
        )
        key_name = self.route.credential_environment_variable
        assert key_name is not None
        api_key = self._environment.get(key_name)
        if not api_key:
            raise ProviderTransportError(
                _unavailable_code(self.route.role),
                "DeepSeek credential is unavailable",
            )
        body: dict[str, Any] = {
            "model": self.route.model_name,
            "messages": [
                {"role": message.role, "content": message.content} for message in messages
            ],
            "thinking": {"type": "enabled" if thinking_enabled else "disabled"},
            "max_tokens": self.route.maximum_output_tokens,
            "stream": False,
        }
        if output_mode is ProviderOutputMode.JSON_OBJECT:
            body["response_format"] = {"type": "json_object"}
        request_text = canonical_json(body)
        if len(request_text.encode("utf-8")) > self.route.maximum_request_bytes:
            raise ProviderTransportError(
                ErrorCode.PROVIDER_BUDGET_EXCEEDED,
                "DeepSeek request exceeds the configured byte budget",
            )
        request_sha256 = text_sha256(request_text)
        request = urllib.request.Request(
            f"{self.route.endpoint}/chat/completions",
            data=request_text.encode("utf-8"),
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        if on_transport_invoke is not None:
            on_transport_invoke()
        started = time.perf_counter()
        try:
            with self._opener(request, timeout=self.route.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ProviderTransportError(
                _unavailable_code(self.route.role),
                f"DeepSeek transport failed ({type(exc).__name__})",
                safe_diagnostics=("DEEPSEEK_TRANSPORT_RESPONSE_UNAVAILABLE",),
                external_provider_calls_observed=1,
                retryable_failure_category=(
                    _deepseek_retryable_failure_category(exc)
                ),
            ) from None
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProviderTransportError(
                _contract_code(self.route.role),
                f"DeepSeek response was not valid JSON ({type(exc).__name__})",
                safe_diagnostics=("DEEPSEEK_RESPONSE_INVALID_OUTER_JSON",),
                external_provider_calls_observed=1,
                retryable_failure_category=(
                    ProviderRetryableFailureCategory.PROVIDER_OUTPUT_INVALID
                ),
            ) from None
        duration_ms = max(0, round((time.perf_counter() - started) * 1000))
        try:
            returned_model = payload["model"]
            provider_request_id = payload["id"]
            choice = payload["choices"][0]
            output_text = choice["message"]["content"]
            finish_reason = choice["finish_reason"]
            usage = payload.get("usage", {})
        except (KeyError, IndexError, TypeError):
            raise ProviderTransportError(
                _contract_code(self.route.role),
                "DeepSeek response omitted required fields",
                safe_diagnostics=("DEEPSEEK_RESPONSE_MISSING_REQUIRED_FIELDS",),
                external_provider_calls_observed=1,
                retryable_failure_category=(
                    ProviderRetryableFailureCategory.PROVIDER_OUTPUT_INVALID
                ),
            ) from None
        if returned_model != self.route.model_name:
            raise ProviderTransportError(
                _contract_code(self.route.role),
                "DeepSeek returned an unexpected model identity",
                safe_diagnostics=("DEEPSEEK_RESPONSE_MODEL_IDENTITY_MISMATCH",),
                external_provider_calls_observed=1,
            )
        receipt = _build_call_receipt(
            route=self.route,
            request_sha256=request_sha256,
            returned_model=returned_model,
            provider_request_id=provider_request_id,
            fingerprint=payload.get("system_fingerprint"),
            duration_ms=duration_ms,
            usage=usage,
            output_text=output_text if isinstance(output_text, str) else "",
        )
        try:
            normalized_finish_reason = ProviderFinishReason(finish_reason)
        except (TypeError, ValueError):
            raise ProviderTransportError(
                _contract_code(self.route.role),
                "DeepSeek returned an unknown finish reason",
                safe_diagnostics=("DEEPSEEK_FINISH_REASON_UNKNOWN",),
                external_provider_calls_observed=1,
                provider_call_receipt=receipt,
                retryable_failure_category=(
                    ProviderRetryableFailureCategory.PROVIDER_OUTPUT_INVALID
                ),
            ) from None
        if receipt.cost_microusd > self.route.maximum_cost_microusd:
            failure_receipt = _failure_receipt(
                receipt,
                normalized_finish_reason,
                ProviderResponseFailureKind.COST_CEILING_EXCEEDED,
            )
            raise ProviderTransportError(
                ErrorCode.PROVIDER_BUDGET_EXCEEDED,
                "DeepSeek call exceeded the configured cost ceiling",
                safe_diagnostics=("DEEPSEEK_COST_CEILING_EXCEEDED",),
                external_provider_calls_observed=1,
                provider_call_receipt=failure_receipt,
            )
        if normalized_finish_reason is not ProviderFinishReason.STOP:
            failure_receipt = _failure_receipt(
                receipt,
                normalized_finish_reason,
                ProviderResponseFailureKind.NON_STOP_FINISH,
            )
            raise ProviderTransportError(
                _contract_code(self.route.role),
                "DeepSeek did not return one complete candidate",
                safe_diagnostics=(
                    f"DEEPSEEK_FINISH_REASON_{normalized_finish_reason.value.upper()}",
                ),
                external_provider_calls_observed=1,
                provider_call_receipt=failure_receipt,
                retryable_failure_category=(
                    ProviderRetryableFailureCategory.PROVIDER_COMPLETION_INCOMPLETE
                    if normalized_finish_reason
                    is ProviderFinishReason.INSUFFICIENT_SYSTEM_RESOURCE
                    else None
                ),
            )
        if not isinstance(output_text, str) or not output_text.strip():
            failure_receipt = _failure_receipt(
                receipt,
                normalized_finish_reason,
                ProviderResponseFailureKind.EMPTY_CONTENT,
            )
            raise ProviderTransportError(
                _contract_code(self.route.role),
                "DeepSeek returned empty candidate content",
                safe_diagnostics=("DEEPSEEK_CONTENT_EMPTY",),
                external_provider_calls_observed=1,
                provider_call_receipt=failure_receipt,
                retryable_failure_category=(
                    ProviderRetryableFailureCategory.PROVIDER_COMPLETION_INCOMPLETE
                ),
            )

        parsed_json = None
        if output_mode is ProviderOutputMode.JSON_OBJECT:
            try:
                parsed = json.loads(output_text)
            except json.JSONDecodeError:
                failure_receipt = _failure_receipt(
                    receipt,
                    normalized_finish_reason,
                    ProviderResponseFailureKind.MALFORMED_JSON,
                )
                raise ProviderTransportError(
                    _contract_code(self.route.role),
                    "DeepSeek JSON-mode output was malformed",
                    safe_diagnostics=("DEEPSEEK_CONTENT_MALFORMED_JSON",),
                    external_provider_calls_observed=1,
                    provider_call_receipt=failure_receipt,
                    retryable_failure_category=(
                        ProviderRetryableFailureCategory.PROVIDER_OUTPUT_INVALID
                    ),
                ) from None
            if not isinstance(parsed, dict):
                failure_receipt = _failure_receipt(
                    receipt,
                    normalized_finish_reason,
                    ProviderResponseFailureKind.NON_OBJECT_JSON,
                )
                raise ProviderTransportError(
                    _contract_code(self.route.role),
                    "DeepSeek JSON-mode output was not an object",
                    safe_diagnostics=("DEEPSEEK_CONTENT_NON_OBJECT_JSON",),
                    external_provider_calls_observed=1,
                    provider_call_receipt=failure_receipt,
                    retryable_failure_category=(
                        ProviderRetryableFailureCategory.PROVIDER_OUTPUT_INVALID
                    ),
                )
            parsed_json = parsed
        return ProviderCallResult(output_text, parsed_json, receipt)


def _build_call_receipt(
    *,
    route: LiveProviderRoute,
    request_sha256: str,
    returned_model: str,
    provider_request_id: object,
    fingerprint: object,
    duration_ms: int,
    usage: object,
    output_text: str,
) -> LiveProviderCallReceipt:
    if not isinstance(usage, dict):
        raise ProviderTransportError(
            _contract_code(route.role),
            "DeepSeek usage payload is invalid",
            safe_diagnostics=("DEEPSEEK_USAGE_INVALID_OBJECT",),
            external_provider_calls_observed=1,
            retryable_failure_category=(
                ProviderRetryableFailureCategory.PROVIDER_OUTPUT_INVALID
            ),
        )
    input_tokens = _non_negative_int(
        usage.get("prompt_tokens", 0),
        "prompt_tokens",
        route.role,
        external_provider_calls_observed=1,
    )
    output_tokens = _non_negative_int(
        usage.get("completion_tokens", 0),
        "completion_tokens",
        route.role,
        external_provider_calls_observed=1,
    )
    cached_tokens = _non_negative_int(
        usage.get("prompt_cache_hit_tokens", 0),
        "prompt_cache_hit_tokens",
        route.role,
        external_provider_calls_observed=1,
    )
    cache_miss_tokens = _non_negative_int(
        usage.get(
            "prompt_cache_miss_tokens",
            input_tokens - min(cached_tokens, input_tokens),
        ),
        "prompt_cache_miss_tokens",
        route.role,
        external_provider_calls_observed=1,
    )
    if cached_tokens + cache_miss_tokens != input_tokens:
        cached_tokens = min(cached_tokens, input_tokens)
        cache_miss_tokens = input_tokens - cached_tokens
    pricing = route.pricing
    assert pricing is not None
    cost_microusd = pricing.estimate_cost_microusd(
        cache_hit_input_tokens=cached_tokens,
        cache_miss_input_tokens=cache_miss_tokens,
        output_tokens=output_tokens,
    )
    output_sha256 = text_sha256(output_text)
    completion_details = usage.get("completion_tokens_details", {})
    if not isinstance(completion_details, dict):
        completion_details = {}
    reasoning_output_tokens = usage.get(
        "reasoning_tokens",
        completion_details.get("reasoning_tokens", 0),
    )
    if type(reasoning_output_tokens) is not int or reasoning_output_tokens < 0:
        reasoning_output_tokens = 0
    receipt_key = (
        f"{route.route_sha256}|{request_sha256}|{output_sha256}|"
        f"{provider_request_id}"
    )
    return LiveProviderCallReceipt(
        schema_version=LiveProviderCallReceipt.SCHEMA_VERSION,
        provider_receipt_id=deterministic_id(
            IdKind.PROVIDER_RECEIPT,
            "cera.live_provider.deepseek.receipt.v2",
            receipt_key,
        ),
        route_sha256=route.route_sha256,
        provider=route.provider,
        role=route.role,
        requested_model=route.model_name,
        returned_model=returned_model,
        model_revision=route.model_revision,
        model_identity_source=ModelIdentitySource.PROVIDER_RESPONSE,
        model_identity_verified=True,
        request_sha256=request_sha256,
        output_sha256=output_sha256,
        provider_request_id_sha256=text_sha256(str(provider_request_id)),
        system_fingerprint_sha256=(
            text_sha256(str(fingerprint)) if fingerprint is not None else None
        ),
        duration_ms=duration_ms,
        input_tokens=input_tokens,
        cached_input_tokens=cached_tokens,
        output_tokens=output_tokens,
        reasoning_output_tokens=reasoning_output_tokens,
        cost_microusd=cost_microusd,
        cost_is_estimate=True,
        quota_metered=False,
        external_provider_calls=1,
        automatic_retry_count=0,
        story_authority_writes=0,
        retains_raw_source=False,
        retains_story_prose=False,
        retains_private_evidence=False,
        retains_prompt=False,
        retains_secret=False,
    )


def _failure_receipt(
    call_receipt: LiveProviderCallReceipt,
    finish_reason: ProviderFinishReason,
    failure_kind: ProviderResponseFailureKind,
) -> ProviderFailureCallReceipt:
    return ProviderFailureCallReceipt(
        schema_version=ProviderFailureCallReceipt.SCHEMA_VERSION,
        provider_receipt_id=deterministic_id(
            IdKind.PROVIDER_RECEIPT,
            "cera.provider_failure_call_receipt.v1",
            (
                f"{call_receipt.provider_receipt_id}|{finish_reason.value}|"
                f"{failure_kind.value}"
            ),
        ),
        call_receipt=call_receipt,
        finish_reason=finish_reason,
        failure_kind=failure_kind,
        completion_accepted=False,
    )


def _non_negative_int(
    value: Any,
    field_name: str,
    role: EvaluationRole,
    *,
    external_provider_calls_observed: int = 0,
) -> int:
    if type(value) is not int or value < 0:
        raise ProviderTransportError(
            _contract_code(role),
            f"DeepSeek usage field {field_name} is invalid",
            safe_diagnostics=(f"DEEPSEEK_USAGE_{field_name.upper()}_INVALID",),
            external_provider_calls_observed=external_provider_calls_observed,
            retryable_failure_category=(
                ProviderRetryableFailureCategory.PROVIDER_OUTPUT_INVALID
            ),
        )
    return value


def _unavailable_code(role: EvaluationRole) -> ErrorCode:
    return (
        ErrorCode.ADULT_PLANNER_UNAVAILABLE
        if role is EvaluationRole.ADULT_MECHANICS
        else ErrorCode.COMPOSER_UNAVAILABLE
    )


def _deepseek_retryable_failure_category(
    exc: BaseException,
) -> ProviderRetryableFailureCategory | None:
    """Classify only typed DeepSeek transport evidence; never parse messages."""

    if isinstance(exc, urllib.error.HTTPError):
        status = exc.code
        if status == 408:
            return ProviderRetryableFailureCategory.TRANSPORT_TIMEOUT
        if status == 429 or 500 <= status <= 599:
            return ProviderRetryableFailureCategory.PROVIDER_UNAVAILABLE
        return None
    if isinstance(exc, TimeoutError):
        return ProviderRetryableFailureCategory.TRANSPORT_TIMEOUT
    if isinstance(exc, urllib.error.URLError):
        if isinstance(exc.reason, TimeoutError):
            return ProviderRetryableFailureCategory.TRANSPORT_TIMEOUT
        if isinstance(exc.reason, ConnectionError):
            return ProviderRetryableFailureCategory.PROVIDER_UNAVAILABLE
        return None
    if isinstance(exc, ConnectionError):
        return ProviderRetryableFailureCategory.PROVIDER_UNAVAILABLE
    return None


def _contract_code(role: EvaluationRole) -> ErrorCode:
    return (
        ErrorCode.ADULT_MECHANICS_CONTRACT_INVALID
        if role is EvaluationRole.ADULT_MECHANICS
        else ErrorCode.COMPOSER_CONTRACT_INVALID
    )
