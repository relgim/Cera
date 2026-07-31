"""Canonical identity for the active local CERA runtime.

This module is deliberately data-only. Provider routes, adapters, session
compatibility, health output, tests, and current-status documents bind to this
single profile instead of repeating version strings independently.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from cera.serialization import canonical_sha256


@dataclass(frozen=True, slots=True)
class RuntimeRoleIdentity:
    role: str
    provider: str
    model: str
    model_revision: str
    route_adapter_id: str
    domain_adapter_version: str
    packet_version: str
    prompt_version: str
    output_schema_version: str
    request_schema_version: str | None
    transport_name: str
    transport_version: str
    provider_schema_dialect: str
    reasoning_efforts: tuple[str, ...]
    default_reasoning_effort: str | None
    thinking_enabled: bool | None
    tool_contract_version: str | None
    automatic_retry_count: int = 0
    fallback_enabled: bool = False
    production_enabled: bool = False


@dataclass(frozen=True, slots=True)
class ActiveRuntimeProfile:
    schema_version: str
    profile_id: str
    decision_id: str
    owner_architecture_version: str
    reasoner_session_mode: str
    reasoner: RuntimeRoleIdentity
    composer: RuntimeRoleIdentity
    verifier: RuntimeRoleIdentity

    def to_mapping(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def profile_sha256(self) -> str:
        return canonical_sha256(self.to_mapping())


ACTIVE_RUNTIME_PROFILE = ActiveRuntimeProfile(
    schema_version="cera.active_runtime_profile.v1",
    profile_id="cera.active_runtime.d180.v1",
    decision_id="D-180",
    owner_architecture_version="cera.owner_architecture.v2",
    reasoner_session_mode="branch_bound_native_stored_v1",
    reasoner=RuntimeRoleIdentity(
        role="scene_reasoner",
        provider="openai_codex",
        model="gpt-5.6-sol",
        model_revision="openai-resolver-2026-07-28",
        route_adapter_id="cera.codex_scene_reasoner.v25",
        domain_adapter_version="cera.codex_scene_reasoner.v25",
        packet_version="cera.codex_scene_reasoner_packet.v14",
        prompt_version="cera.codex_scene_reasoner_prompt.v25",
        output_schema_version="cera.codex_reasoner_draft.v6",
        request_schema_version="cera.scene_reasoner_request.v4",
        transport_name="codex_python_sdk_app_server",
        transport_version="0.144.4",
        provider_schema_dialect="openai_structured_output_2026_07_v1",
        reasoning_efforts=("medium", "high", "xhigh"),
        default_reasoning_effort="medium",
        thinking_enabled=None,
        tool_contract_version="cera.reasoner_evidence_mcp.v7",
    ),
    composer=RuntimeRoleIdentity(
        role="scene_composer",
        provider="deepseek",
        model="deepseek-v4-flash",
        model_revision="deepseek-v4-2026-04-24",
        route_adapter_id="cera.deepseek_scene_composer.v29",
        domain_adapter_version="cera.deepseek_scene_composer.v29",
        packet_version="cera.deepseek_scene_composer_packet.v15",
        prompt_version="cera.deepseek_scene_composer_prompt.v26",
        output_schema_version="cera.deepseek_composition_draft.v6",
        request_schema_version="cera.scene_composer_request.v6",
        transport_name="deepseek_chat_completions",
        transport_version="2026-04-24",
        provider_schema_dialect="deepseek_json_object_prompt_2026_07_v1",
        reasoning_efforts=(),
        default_reasoning_effort=None,
        thinking_enabled=False,
        tool_contract_version=None,
    ),
    verifier=RuntimeRoleIdentity(
        role="scene_realization_verifier",
        provider="openai_codex",
        model="gpt-5.6-sol",
        model_revision="openai-resolver-2026-07-28",
        route_adapter_id="cera.codex_cli_scene_realization_verifier.v1",
        domain_adapter_version="cera.codex_scene_realization_verifier.v8",
        packet_version="cera.codex_scene_realization_verifier_packet.v8",
        prompt_version="cera.codex_scene_realization_verifier_prompt.v8",
        output_schema_version="cera.codex_scene_realization_verifier_draft.v3",
        request_schema_version="cera.scene_realization_verification_request.v7",
        transport_name="codex_cli_exec",
        transport_version="0.144.4",
        provider_schema_dialect="openai_structured_output_2026_07_v1",
        reasoning_efforts=("medium",),
        default_reasoning_effort="medium",
        thinking_enabled=None,
        tool_contract_version=None,
    ),
)
