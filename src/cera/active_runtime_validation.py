"""Cross-module drift checks for the canonical active runtime profile."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from cera.active_runtime import ACTIVE_RUNTIME_PROFILE, ActiveRuntimeProfile
from cera.errors import ContractValidationError


def validate_active_runtime_bindings(
    profile: ActiveRuntimeProfile = ACTIVE_RUNTIME_PROFILE,
) -> tuple[str, ...]:
    """Return every active-source mismatch without touching a provider."""

    from cera.composer.deepseek import (
        DEEPSEEK_COMPOSER_ADAPTER_VERSION,
        DEEPSEEK_COMPOSER_PACKET_VERSION,
        DEEPSEEK_COMPOSER_PROMPT_VERSION,
        DEEPSEEK_COMPOSER_THINKING_ENABLED,
        DeepSeekCompositionDraftV6,
    )
    from cera.composer.models import SceneComposerRequest
    from cera.providers import ProviderSchemaDialect
    from cera.providers.routes import (
        codex_cli_realization_verifier_candidate,
        codex_reasoner_candidate,
        deepseek_composer_candidate,
    )
    from cera.realization.codex import (
        CODEX_REALIZATION_VERIFIER_ADAPTER_VERSION,
        CODEX_REALIZATION_VERIFIER_DRAFT_VERSION,
        CODEX_REALIZATION_VERIFIER_PACKET_VERSION,
        CODEX_REALIZATION_VERIFIER_PROMPT_VERSION,
    )
    from cera.realization.models import SceneRealizationVerificationRequest
    from cera.reasoner.codex import (
        CODEX_REASONER_ADAPTER_VERSION,
        CODEX_REASONER_PACKET_VERSION,
        CODEX_REASONER_PROMPT_VERSION,
    )
    from cera.reasoner.drafts import CodexReasonerDraftV6
    from cera.reasoner.mcp_bridge import MCP_TOOL_CONTRACT_VERSION
    from cera.reasoner.models import SceneReasonerRequest
    from cera.reasoner_session.runtime import NativeStoredReasonerSessionRuntime

    mismatches: list[str] = []

    def expect(label: str, actual: object, expected: object) -> None:
        if actual != expected:
            mismatches.append(f"{label}: {actual!r} != {expected!r}")

    reasoner = profile.reasoner
    expect(
        "reasoner domain adapter",
        CODEX_REASONER_ADAPTER_VERSION,
        reasoner.domain_adapter_version,
    )
    expect("reasoner packet", CODEX_REASONER_PACKET_VERSION, reasoner.packet_version)
    expect("reasoner prompt", CODEX_REASONER_PROMPT_VERSION, reasoner.prompt_version)
    expect(
        "reasoner draft",
        CodexReasonerDraftV6.SCHEMA_VERSION,
        reasoner.output_schema_version,
    )
    expect(
        "reasoner request",
        SceneReasonerRequest.SCHEMA_VERSION,
        reasoner.request_schema_version,
    )
    expect(
        "reasoner schema dialect",
        ProviderSchemaDialect.OPENAI_STRUCTURED_OUTPUT_V1.value,
        reasoner.provider_schema_dialect,
    )
    expect("reasoner MCP", MCP_TOOL_CONTRACT_VERSION, reasoner.tool_contract_version)
    expect(
        "reasoner session mode",
        NativeStoredReasonerSessionRuntime.MODE,
        profile.reasoner_session_mode,
    )
    for effort in reasoner.reasoning_efforts:
        route = codex_reasoner_candidate(model=reasoner.model, effort=effort)
        _compare_route("reasoner", route, reasoner, effort, expect)

    composer = profile.composer
    expect(
        "composer domain adapter",
        DEEPSEEK_COMPOSER_ADAPTER_VERSION,
        composer.domain_adapter_version,
    )
    expect(
        "composer packet",
        DEEPSEEK_COMPOSER_PACKET_VERSION,
        composer.packet_version,
    )
    expect(
        "composer prompt",
        DEEPSEEK_COMPOSER_PROMPT_VERSION,
        composer.prompt_version,
    )
    expect(
        "composer thinking",
        DEEPSEEK_COMPOSER_THINKING_ENABLED,
        composer.thinking_enabled,
    )
    expect(
        "composer draft",
        DeepSeekCompositionDraftV6.SCHEMA_VERSION,
        composer.output_schema_version,
    )
    expect(
        "composer request",
        SceneComposerRequest.SCHEMA_VERSION,
        composer.request_schema_version,
    )
    expect(
        "composer schema dialect",
        ProviderSchemaDialect.DEEPSEEK_JSON_OBJECT_PROMPT_V1.value,
        composer.provider_schema_dialect,
    )
    route = deepseek_composer_candidate(model=composer.model)
    _compare_route("composer", route, composer, None, expect)

    verifier = profile.verifier
    expect(
        "verifier domain adapter",
        CODEX_REALIZATION_VERIFIER_ADAPTER_VERSION,
        verifier.domain_adapter_version,
    )
    expect(
        "verifier packet",
        CODEX_REALIZATION_VERIFIER_PACKET_VERSION,
        verifier.packet_version,
    )
    expect(
        "verifier prompt",
        CODEX_REALIZATION_VERIFIER_PROMPT_VERSION,
        verifier.prompt_version,
    )
    expect(
        "verifier draft",
        CODEX_REALIZATION_VERIFIER_DRAFT_VERSION,
        verifier.output_schema_version,
    )
    expect(
        "verifier request",
        SceneRealizationVerificationRequest.SCHEMA_VERSION,
        verifier.request_schema_version,
    )
    expect(
        "verifier schema dialect",
        ProviderSchemaDialect.OPENAI_STRUCTURED_OUTPUT_V1.value,
        verifier.provider_schema_dialect,
    )
    route = codex_cli_realization_verifier_candidate(
        model=verifier.model,
        effort=verifier.default_reasoning_effort or "medium",
    )
    _compare_route(
        "verifier",
        route,
        verifier,
        verifier.default_reasoning_effort,
        expect,
    )
    return tuple(mismatches)


def assert_active_runtime_bindings(
    profile: ActiveRuntimeProfile = ACTIVE_RUNTIME_PROFILE,
) -> None:
    mismatches = validate_active_runtime_bindings(profile)
    if mismatches:
        raise ContractValidationError(
            "active runtime identity drift: " + "; ".join(mismatches)
        )


def active_runtime_status() -> dict[str, Any]:
    assert_active_runtime_bindings()
    profile = ACTIVE_RUNTIME_PROFILE
    return {
        "schema_version": profile.schema_version,
        "profile_id": profile.profile_id,
        "profile_sha256": profile.profile_sha256,
        "decision_id": profile.decision_id,
        "owner_architecture_version": profile.owner_architecture_version,
        "reasoner_session_mode": profile.reasoner_session_mode,
        "valid": True,
        "reasoner": asdict(profile.reasoner),
        "composer": asdict(profile.composer),
        "verifier": asdict(profile.verifier),
    }


def _compare_route(label, route, identity, effort, expect) -> None:
    expect(f"{label} route provider", route.provider.value, identity.provider)
    expect(f"{label} route model", route.model_name, identity.model)
    expect(
        f"{label} route model revision",
        route.model_revision,
        identity.model_revision,
    )
    expect(f"{label} route adapter", route.adapter_id, identity.route_adapter_id)
    expect(f"{label} route prompt", route.prompt_version, identity.prompt_version)
    expect(f"{label} route transport", route.transport_name, identity.transport_name)
    expect(
        f"{label} route transport version",
        route.transport_version,
        identity.transport_version,
    )
    expect(f"{label} route effort", route.reasoning_effort, effort)
    expect(
        f"{label} route retries",
        route.automatic_retry_count,
        identity.automatic_retry_count,
    )
    expect(f"{label} route fallback", route.fallback_enabled, identity.fallback_enabled)
    expect(f"{label} route production", route.production_enabled, identity.production_enabled)
