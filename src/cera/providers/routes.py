"""Current unpromoted Phase 11 live-route candidates."""

from __future__ import annotations

from cera.active_runtime import ACTIVE_RUNTIME_PROFILE
from cera.evaluation import EvaluationRole

from .codex_exec_contract import (
    CODEX_CLI_EXEC_COMPATIBILITY_ID,
    CODEX_CLI_EXEC_CONTRACT_SHA256,
    CODEX_CLI_EXEC_TRANSPORT_NAME,
    CODEX_CLI_EXEC_VERSION,
)
from .codex_sdk_compat import (
    CODEX_SDK_COMPATIBILITY_ID,
    CODEX_SDK_COMPATIBILITY_SOURCE_SHA256,
)
from .models import (
    LiveProviderRoute,
    ProviderAuthMode,
    ProviderName,
    ProviderPricing,
)

DEEPSEEK_PRICING_SOURCE = "https://api-docs.deepseek.com/quick_start/pricing/"
DEFAULT_DEEPSEEK_COMPOSER_MODEL = ACTIVE_RUNTIME_PROFILE.composer.model


def codex_reasoner_candidate(
    *,
    model: str = ACTIVE_RUNTIME_PROFILE.reasoner.model,
    effort: str = (ACTIVE_RUNTIME_PROFILE.reasoner.default_reasoning_effort or "medium"),
) -> LiveProviderRoute:
    return LiveProviderRoute(
        schema_version=LiveProviderRoute.SCHEMA_VERSION,
        route_id=f"codex_reasoner_{model}_{effort}",
        role=EvaluationRole.SCENE_REASONER,
        provider=ProviderName.OPENAI_CODEX,
        adapter_id=ACTIVE_RUNTIME_PROFILE.reasoner.route_adapter_id,
        model_name=model,
        model_revision=ACTIVE_RUNTIME_PROFILE.reasoner.model_revision,
        prompt_version=ACTIVE_RUNTIME_PROFILE.reasoner.prompt_version,
        transport_name=ACTIVE_RUNTIME_PROFILE.reasoner.transport_name,
        transport_version=ACTIVE_RUNTIME_PROFILE.reasoner.transport_version,
        auth_mode=ProviderAuthMode.CHATGPT_SESSION,
        endpoint=None,
        credential_environment_variable=None,
        reasoning_effort=effort,
        timeout_seconds=240,
        maximum_request_bytes=262_144,
        maximum_output_tokens=16_384,
        maximum_cost_microusd=0,
        automatic_retry_count=0,
        fallback_enabled=False,
        production_enabled=False,
        pricing=None,
        transport_compatibility_id=CODEX_SDK_COMPATIBILITY_ID,
        transport_compatibility_source_sha256=(CODEX_SDK_COMPATIBILITY_SOURCE_SHA256),
    )


def codex_realization_verifier_candidate(
    *,
    model: str = ACTIVE_RUNTIME_PROFILE.verifier.model,
    effort: str = (ACTIVE_RUNTIME_PROFILE.verifier.default_reasoning_effort or "medium"),
) -> LiveProviderRoute:
    """Return the unpromoted one-shot semantic-verifier route."""

    return LiveProviderRoute(
        schema_version=LiveProviderRoute.SCHEMA_VERSION,
        route_id=f"codex_realization_verifier_{model}_{effort}",
        role=EvaluationRole.SCENE_REALIZATION_VERIFIER,
        provider=ProviderName.OPENAI_CODEX,
        adapter_id=ACTIVE_RUNTIME_PROFILE.verifier.domain_adapter_version,
        model_name=model,
        model_revision=ACTIVE_RUNTIME_PROFILE.verifier.model_revision,
        prompt_version=ACTIVE_RUNTIME_PROFILE.verifier.prompt_version,
        transport_name="codex_python_sdk_app_server",
        transport_version="0.144.4",
        auth_mode=ProviderAuthMode.CHATGPT_SESSION,
        endpoint=None,
        credential_environment_variable=None,
        reasoning_effort=effort,
        timeout_seconds=180,
        maximum_request_bytes=262_144,
        maximum_output_tokens=8_192,
        maximum_cost_microusd=0,
        automatic_retry_count=0,
        fallback_enabled=False,
        production_enabled=False,
        pricing=None,
        transport_compatibility_id=CODEX_SDK_COMPATIBILITY_ID,
        transport_compatibility_source_sha256=(CODEX_SDK_COMPATIBILITY_SOURCE_SHA256),
    )


def codex_cli_realization_verifier_candidate(
    *,
    model: str = ACTIVE_RUNTIME_PROFILE.verifier.model,
    effort: str = (ACTIVE_RUNTIME_PROFILE.verifier.default_reasoning_effort or "medium"),
) -> LiveProviderRoute:
    """Return the unpromoted one-shot CLI semantic-verifier route."""

    return LiveProviderRoute(
        schema_version=LiveProviderRoute.SCHEMA_VERSION,
        route_id=f"codex_cli_realization_verifier_{model}_{effort}",
        role=EvaluationRole.SCENE_REALIZATION_VERIFIER,
        provider=ProviderName.OPENAI_CODEX,
        adapter_id=ACTIVE_RUNTIME_PROFILE.verifier.route_adapter_id,
        model_name=model,
        model_revision=ACTIVE_RUNTIME_PROFILE.verifier.model_revision,
        prompt_version=ACTIVE_RUNTIME_PROFILE.verifier.prompt_version,
        transport_name=CODEX_CLI_EXEC_TRANSPORT_NAME,
        transport_version=CODEX_CLI_EXEC_VERSION,
        auth_mode=ProviderAuthMode.CHATGPT_SESSION,
        endpoint=None,
        credential_environment_variable=None,
        reasoning_effort=effort,
        timeout_seconds=180,
        maximum_request_bytes=262_144,
        maximum_output_tokens=8_192,
        maximum_cost_microusd=0,
        automatic_retry_count=0,
        fallback_enabled=False,
        production_enabled=False,
        pricing=None,
        transport_compatibility_id=CODEX_CLI_EXEC_COMPATIBILITY_ID,
        transport_compatibility_source_sha256=(CODEX_CLI_EXEC_CONTRACT_SHA256),
    )


def deepseek_composer_candidate(
    *, model: str = DEFAULT_DEEPSEEK_COMPOSER_MODEL
) -> LiveProviderRoute:
    rates = {
        "deepseek-v4-flash": (2_800, 140_000, 280_000, 100_000),
        "deepseek-v4-pro": (3_625, 435_000, 870_000, 250_000),
    }
    try:
        cache_hit, cache_miss, output, ceiling = rates[model]
    except KeyError:
        cache_hit = cache_miss = output = ceiling = 0
    return LiveProviderRoute(
        schema_version=LiveProviderRoute.SCHEMA_VERSION,
        route_id=f"deepseek_composer_{model}",
        role=EvaluationRole.SCENE_COMPOSER,
        provider=ProviderName.DEEPSEEK,
        adapter_id=ACTIVE_RUNTIME_PROFILE.composer.route_adapter_id,
        model_name=model,
        model_revision=ACTIVE_RUNTIME_PROFILE.composer.model_revision,
        prompt_version=ACTIVE_RUNTIME_PROFILE.composer.prompt_version,
        transport_name=ACTIVE_RUNTIME_PROFILE.composer.transport_name,
        transport_version=ACTIVE_RUNTIME_PROFILE.composer.transport_version,
        auth_mode=ProviderAuthMode.ENVIRONMENT_API_KEY,
        endpoint="https://api.deepseek.com",
        credential_environment_variable="DEEPSEEK_API_KEY",
        reasoning_effort=None,
        timeout_seconds=180,
        maximum_request_bytes=524_288,
        maximum_output_tokens=32_768,
        maximum_cost_microusd=ceiling,
        automatic_retry_count=0,
        fallback_enabled=False,
        production_enabled=False,
        pricing=ProviderPricing(
            cache_hit_input_microusd_per_million=cache_hit,
            cache_miss_input_microusd_per_million=cache_miss,
            output_microusd_per_million=output,
            source_url=DEEPSEEK_PRICING_SOURCE,
            effective_date="2026-07-28",
        ),
    )
