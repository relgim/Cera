"""One-shot DeepSeek Chat Completions transport for live qualification."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
import time
from typing import Any, Callable
import urllib.error
import urllib.request

from cera.errors import ContractValidationError, ErrorCode
from cera.evaluation import EvaluationRole
from cera.ids import IdKind, deterministic_id
from cera.serialization import canonical_json, text_sha256

from .models import (
    LiveProviderCallReceipt,
    LiveProviderRoute,
    ModelIdentitySource,
    ProviderCallResult,
    ProviderName,
    ProviderOutputMode,
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

    def invoke(
        self,
        messages: tuple[DeepSeekMessage, ...],
        *,
        output_mode: ProviderOutputMode = ProviderOutputMode.TEXT,
        thinking_enabled: bool = False,
    ) -> ProviderCallResult:
        if not messages:
            raise ContractValidationError("DeepSeek invocation requires messages")
        if output_mode is ProviderOutputMode.JSON_SCHEMA:
            raise ContractValidationError("DeepSeek Chat transport does not claim strict JSON Schema")
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
        started = time.perf_counter()
        try:
            with self._opener(request, timeout=self.route.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as exc:
            raise ProviderTransportError(
                _unavailable_code(self.route.role),
                f"DeepSeek transport failed ({type(exc).__name__})",
            ) from None
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProviderTransportError(
                _contract_code(self.route.role),
                f"DeepSeek response was not valid JSON ({type(exc).__name__})",
            ) from None
        duration_ms = max(0, round((time.perf_counter() - started) * 1000))
        try:
            returned_model = payload["model"]
            provider_request_id = payload["id"]
            choice = payload["choices"][0]
            output_text = choice["message"]["content"]
            finish_reason = choice["finish_reason"]
            usage = payload.get("usage", {})
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderTransportError(
                _contract_code(self.route.role),
                "DeepSeek response omitted required fields",
            ) from None
        if returned_model != self.route.model_name:
            raise ProviderTransportError(
                _contract_code(self.route.role),
                "DeepSeek returned an unexpected model identity",
            )
        if finish_reason != "stop" or not isinstance(output_text, str) or not output_text.strip():
            raise ProviderTransportError(
                _contract_code(self.route.role),
                "DeepSeek did not return one complete candidate",
            )
        parsed_json = None
        if output_mode is ProviderOutputMode.JSON_OBJECT:
            try:
                parsed = json.loads(output_text)
            except json.JSONDecodeError:
                raise ProviderTransportError(
                    _contract_code(self.route.role),
                    "DeepSeek JSON-mode output was malformed",
                ) from None
            if not isinstance(parsed, dict):
                raise ProviderTransportError(
                    _contract_code(self.route.role),
                    "DeepSeek JSON-mode output was not an object",
                )
            parsed_json = parsed

        input_tokens = _non_negative_int(
            usage.get("prompt_tokens", 0), "prompt_tokens", self.route.role
        )
        output_tokens = _non_negative_int(
            usage.get("completion_tokens", 0), "completion_tokens", self.route.role
        )
        cached_tokens = _non_negative_int(
            usage.get("prompt_cache_hit_tokens", 0),
            "prompt_cache_hit_tokens",
            self.route.role,
        )
        cache_miss_tokens = _non_negative_int(
            usage.get("prompt_cache_miss_tokens", input_tokens - min(cached_tokens, input_tokens)),
            "prompt_cache_miss_tokens",
            self.route.role,
        )
        if cached_tokens + cache_miss_tokens != input_tokens:
            cached_tokens = min(cached_tokens, input_tokens)
            cache_miss_tokens = input_tokens - cached_tokens
        pricing = self.route.pricing
        assert pricing is not None
        cost_microusd = pricing.estimate_cost_microusd(
            cache_hit_input_tokens=cached_tokens,
            cache_miss_input_tokens=cache_miss_tokens,
            output_tokens=output_tokens,
        )
        if cost_microusd > self.route.maximum_cost_microusd:
            raise ProviderTransportError(
                ErrorCode.PROVIDER_BUDGET_EXCEEDED,
                "DeepSeek call exceeded the configured cost ceiling",
            )
        output_sha256 = text_sha256(output_text)
        fingerprint = payload.get("system_fingerprint")
        receipt_key = f"{self.route.route_sha256}|{request_sha256}|{output_sha256}|{provider_request_id}"
        receipt = LiveProviderCallReceipt(
            schema_version=LiveProviderCallReceipt.SCHEMA_VERSION,
            provider_receipt_id=deterministic_id(
                IdKind.PROVIDER_RECEIPT,
                "cera.live_provider.deepseek.receipt.v2",
                receipt_key,
            ),
            route_sha256=self.route.route_sha256,
            provider=self.route.provider,
            role=self.route.role,
            requested_model=self.route.model_name,
            returned_model=returned_model,
            model_revision=self.route.model_revision,
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
            reasoning_output_tokens=0,
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
        return ProviderCallResult(output_text, parsed_json, receipt)


def _non_negative_int(value: Any, field_name: str, role: EvaluationRole) -> int:
    if type(value) is not int or value < 0:
        raise ProviderTransportError(
            _contract_code(role),
            f"DeepSeek usage field {field_name} is invalid",
        )
    return value


def _unavailable_code(role: EvaluationRole) -> ErrorCode:
    return (
        ErrorCode.ADULT_PLANNER_UNAVAILABLE
        if role is EvaluationRole.ADULT_MECHANICS
        else ErrorCode.COMPOSER_UNAVAILABLE
    )


def _contract_code(role: EvaluationRole) -> ErrorCode:
    return (
        ErrorCode.ADULT_MECHANICS_CONTRACT_INVALID
        if role is EvaluationRole.ADULT_MECHANICS
        else ErrorCode.COMPOSER_CONTRACT_INVALID
    )
