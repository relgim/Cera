"""Typed Codex Scene Reasoner adapter over the request-bound evidence bridge."""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
import time
from typing import Protocol

from cera.active_runtime import ACTIVE_RUNTIME_PROFILE
from cera.adult_craft.json_schema import (
    PROVIDER_BINDING_PLACEHOLDER,
    adult_craft_need_provider_draft_json_schema,
)
from cera.contracts import (
    BeatState,
    DecisionRoute,
    SceneDecision,
    build_scene_development_contract,
)
from cera.errors import ContractValidationError, ErrorCode, IdentityError
from cera.ids import (
    AUTHORITY_RECORD_ID_KIND_ORDER,
    IdKind,
    TypedId,
    deterministic_id,
)
from cera.kernel import ProtectedUserProvenance, StateMutationTarget, TurnRoute
from cera.providers import (
    CodexMcpRuntimeBinding,
    CodexSDKTransport,
    ProviderCallResult,
    ProviderTransportError,
)
from cera.schema import from_mapping
from cera.serialization import canonical_json, domain_sha256, to_primitive

from .fake import (
    ReasonerEvidenceToolPort,
    SceneReasonerPort,
    SceneReasonerPortFailure,
)
from .drafts import (
    CodexReasonerDraftV2,
    CodexReasonerDraftV3,
    CodexReasonerDraftV4,
    CodexReasonerDraftV5,
    CodexReasonerDraftV6,
    codex_reasoner_draft_v2_json_schema,
    codex_reasoner_draft_v3_json_schema,
    codex_reasoner_draft_v4_json_schema,
    codex_reasoner_draft_v5_json_schema,
    codex_reasoner_draft_v6_json_schema,
    compile_reasoner_draft,
    safe_reasoner_contract_diagnostics,
    validate_reasoner_draft_payload_semantics,
)
from .compact_v7 import (
    CodexReasonerDraftV7Compact,
    build_compact_v7_reasoner_prompt,
    codex_reasoner_draft_v7_compact_json_schema,
    compile_compact_v7_to_v6,
)
from .mcp_bridge import (
    MAX_FETCH_CITATION_ALIASES,
    McpEvidenceBridgeError,
    McpEvidenceBridgeReceipt,
    RequestBoundMcpEvidenceBridge,
)
from .input_preparation import (
    SceneCastScopeV1,
    build_reasoner_reading_capsule,
    compact_evidence_view,
)
from .models import (
    InterventionReason,
    ParticipationRole,
    ReasonerAdapterRole,
    ReasonerOutcome,
    ReasonerOutcomeStatus,
    SceneReasonerAdapterCall,
    SceneReasonerRequest,
)


CODEX_REASONER_ADAPTER_VERSION = ACTIVE_RUNTIME_PROFILE.reasoner.domain_adapter_version
CODEX_REASONER_PACKET_VERSION = ACTIVE_RUNTIME_PROFILE.reasoner.packet_version
CODEX_REASONER_PROMPT_VERSION = ACTIVE_RUNTIME_PROFILE.reasoner.prompt_version
COMPACT_REASONER_PACKET_VERSION = "cera.codex_reasoner_packet.v15.compact_shadow"
COMPACT_REASONER_PROMPT_VERSION = "cera.codex_reasoner_prompt.v26.compact_shadow"


class ReasonerEvidenceBridgePort(Protocol):
    @property
    def runtime_binding(self) -> CodexMcpRuntimeBinding: ...

    def finalize(self, provider_result: ProviderCallResult) -> McpEvidenceBridgeReceipt: ...

    def __enter__(self) -> "ReasonerEvidenceBridgePort": ...

    def __exit__(self, exc_type, exc, traceback) -> None: ...


ReasonerEvidenceBridgeFactory = Callable[
    [ReasonerEvidenceToolPort, SceneReasonerRequest],
    ReasonerEvidenceBridgePort,
]


def _seed_citation_aliases(request: SceneReasonerRequest) -> dict[str, TypedId]:
    return {
        f"evidence:seed_{index:03d}": value.evidence_id
        for index, value in enumerate(
            request.seed_dossier.exact_seed_evidence, start=1
        )
    }


def _bridge_citation_aliases(
    bridge: ReasonerEvidenceBridgePort | None,
) -> dict[str, TypedId]:
    if bridge is None:
        return {}
    value = getattr(bridge, "citation_aliases", None)
    if not isinstance(value, dict):
        return {}
    return {
        alias: evidence_id
        for alias, evidence_id in value.items()
        if isinstance(alias, str) and isinstance(evidence_id, TypedId)
    }


def _resolve_provider_evidence_aliases(
    payload: dict[str, object],
    *,
    alias_map: dict[str, TypedId],
    authorized_evidence_ids: set[TypedId],
) -> dict[str, object]:
    """Resolve provider-local citation handles before authoritative decoding.

    Canonical IDs are accepted only as a compatibility seam for fake adapters
    and stored provider evidence created before v25. The live provider schema
    permits request-local aliases only, so a copied or invented UUID cannot
    reach the authoritative draft as a silently different record.
    """

    resolved = deepcopy(payload)
    for collection_name in (
        "participation",
        "character_moves",
        "event_blocks",
        "development_atoms",
        "responders",
        "beats",
    ):
        collection = resolved.get(collection_name)
        if not isinstance(collection, list):
            continue
        for item in collection:
            if not isinstance(item, dict):
                continue
            evidence_ids = item.get("evidence_ids")
            if not isinstance(evidence_ids, list):
                continue
            normalized: list[object] = []
            for value in evidence_ids:
                if isinstance(value, str) and value in alias_map:
                    normalized.append(str(alias_map[value]))
                    continue
                if isinstance(value, str):
                    try:
                        canonical = TypedId.parse(value, IdKind.EVIDENCE)
                    except IdentityError:
                        canonical = None
                    if canonical in authorized_evidence_ids:
                        normalized.append(value)
                        continue
                normalized.append(value)
            item["evidence_ids"] = normalized
    return resolved


class CodexSceneReasonerPort(SceneReasonerPort):
    """One Codex call, one request bridge, one typed advisory outcome."""

    def __init__(
        self,
        transport: CodexSDKTransport,
        *,
        bridge_factory: ReasonerEvidenceBridgeFactory | None = None,
        maximum_tool_calls: int = 12,
        evidence_tools_enabled: bool = True,
        provider_root_thread_id_sha256: str | None = None,
        accepted_parent_checkpoint_id_sha256: str | None = None,
        candidate_checkpoint_id_sha256: str | None = None,
        compact_input: bool = False,
        scene_cast_scope: SceneCastScopeV1 | None = None,
        draft_schema_version: str = CodexReasonerDraftV6.SCHEMA_VERSION,
    ) -> None:
        if type(maximum_tool_calls) is not int or not 1 <= maximum_tool_calls <= 32:
            raise ContractValidationError("reasoner MCP maximum tool calls must be 1..32")
        self.transport = transport
        self.maximum_tool_calls = maximum_tool_calls
        self.evidence_tools_enabled = evidence_tools_enabled
        self.provider_root_thread_id_sha256 = provider_root_thread_id_sha256
        self.accepted_parent_checkpoint_id_sha256 = (
            accepted_parent_checkpoint_id_sha256
        )
        self.candidate_checkpoint_id_sha256 = candidate_checkpoint_id_sha256
        self.compact_input = compact_input
        self.scene_cast_scope = scene_cast_scope
        if draft_schema_version not in {
            CodexReasonerDraftV6.SCHEMA_VERSION,
            CodexReasonerDraftV7Compact.SCHEMA_VERSION,
        }:
            raise ContractValidationError("unsupported Reasoner draft schema")
        self.draft_schema_version = draft_schema_version
        self._bridge_factory = bridge_factory or self._default_bridge

    def _default_bridge(
        self,
        tools: ReasonerEvidenceToolPort,
        request: SceneReasonerRequest,
    ) -> ReasonerEvidenceBridgePort:
        return RequestBoundMcpEvidenceBridge(
            tools,
            reasoner_request_sha256=request.request_sha256,
            snapshot=request.prepared_turn.evidence_snapshot,
            minimum_tool_calls=0,
            maximum_tool_calls=self.maximum_tool_calls,
        )

    def reason(
        self,
        request: SceneReasonerRequest,
        tools: ReasonerEvidenceToolPort,
    ) -> SceneReasonerAdapterCall:
        packet = build_codex_reasoner_packet(
            request,
            evidence_tools_available=self.evidence_tools_enabled,
            compact_input=self.compact_input,
            scene_cast_scope=self.scene_cast_scope,
            draft_schema_version=self.draft_schema_version,
        )
        prompt = build_codex_reasoner_prompt(
            packet,
            draft_schema_version=self.draft_schema_version,
        )
        seed_aliases = _seed_citation_aliases(request)
        provider_aliases = tuple(seed_aliases)
        if self.evidence_tools_enabled:
            provider_aliases += tuple(
                f"evidence:fetch_{index:03d}"
                for index in range(1, MAX_FETCH_CITATION_ALIASES + 1)
            )
        allowed_aliases = tuple(
            TypedId.parse(value, IdKind.EVIDENCE) for value in provider_aliases
        )
        output_schema = (
            codex_reasoner_draft_v7_compact_json_schema(
                allowed_evidence_ids=allowed_aliases,
                adult_route=(
                    request.prepared_turn.route is TurnRoute.CONSENT_VALID_ADULT
                ),
            )
            if self.draft_schema_version
            == CodexReasonerDraftV7Compact.SCHEMA_VERSION
            else codex_reasoner_draft_v6_json_schema(
                allowed_evidence_ids=allowed_aliases
            )
        )
        bridge = None
        packet_ready_us = time.time_ns() // 1_000
        try:
            if self.evidence_tools_enabled:
                bridge = self._bridge_factory(tools, request)
                with bridge:
                    provider_result = self.transport.invoke(
                        prompt,
                        output_schema=output_schema,
                        mcp_binding=bridge.runtime_binding,
                    )
                    bridge_receipt = bridge.finalize(provider_result)
            else:
                provider_result = self.transport.invoke(
                    prompt,
                    output_schema=output_schema,
                    mcp_binding=None,
                )
                bridge_receipt = None
        except ProviderTransportError as exc:
            raise _provider_failure(exc, bridge=bridge) from exc
        except McpEvidenceBridgeError as exc:
            raise SceneReasonerPortFailure(
                exc.code,
                str(exc),
                "reasoner_evidence",
            ) from exc

        validation_started_us = time.time_ns() // 1_000
        try:
            if provider_result.parsed_json is None:
                raise ContractValidationError("Codex reasoner omitted its JSON object")
            authorized = tuple(
                {
                    value.evidence_id: value
                    for value in (
                        *request.seed_dossier.exact_seed_evidence,
                        *tuple(getattr(tools, "exact_records", {}).values()),
                    )
                }.values()
            )
            alias_map = {
                **seed_aliases,
                **_bridge_citation_aliases(bridge),
            }
            resolved_payload = _resolve_provider_evidence_aliases(
                provider_result.parsed_json,
                alias_map=alias_map,
                authorized_evidence_ids={value.evidence_id for value in authorized},
            )
            draft_version = resolved_payload.get("schema_version")
            if draft_version != CodexReasonerDraftV7Compact.SCHEMA_VERSION:
                validate_reasoner_draft_payload_semantics(resolved_payload)
            draft_type = (
                CodexReasonerDraftV7Compact
                if draft_version == CodexReasonerDraftV7Compact.SCHEMA_VERSION
                else CodexReasonerDraftV6
                if draft_version == CodexReasonerDraftV6.SCHEMA_VERSION
                else CodexReasonerDraftV5
                if draft_version == CodexReasonerDraftV5.SCHEMA_VERSION
                else CodexReasonerDraftV4
                if draft_version == CodexReasonerDraftV4.SCHEMA_VERSION
                else CodexReasonerDraftV3
                if draft_version == CodexReasonerDraftV3.SCHEMA_VERSION
                else CodexReasonerDraftV2
            )
            draft = from_mapping(draft_type, resolved_payload)
            if isinstance(draft, CodexReasonerDraftV7Compact):
                draft = compile_compact_v7_to_v6(draft)
            outcome = compile_reasoner_draft(
                request,
                draft,
                authorized_exact_evidence=authorized,
            )
        except (ContractValidationError, IdentityError) as exc:
            diagnostics = safe_reasoner_contract_diagnostics(exc)
            failure = SceneReasonerPortFailure(
                ErrorCode.REASONER_CONTRACT_INVALID,
                f"Codex reasoner output failed typed decoding: {exc}",
                "reasoner_validation",
                safe_diagnostics=diagnostics,
            )
            failure.provider_call_receipt = provider_result.receipt
            failure.mcp_bridge_receipt = bridge_receipt
            if provider_result.operation_telemetry is not None:
                failure.operation_telemetry = (
                    provider_result.operation_telemetry.bind_reasoner_context(
                        packet_ready_unix_us=packet_ready_us,
                        provider_root_thread_id_sha256=(
                            self.provider_root_thread_id_sha256
                        ),
                        accepted_parent_checkpoint_id_sha256=(
                            self.accepted_parent_checkpoint_id_sha256
                        ),
                        candidate_checkpoint_id_sha256=(
                            self.candidate_checkpoint_id_sha256
                        ),
                    ).bind_validation_phase(
                        validation_start_unix_us=validation_started_us,
                        validation_completion_unix_us=time.time_ns() // 1_000,
                    )
                )
            raise failure from exc

        validation_completed_us = time.time_ns() // 1_000
        operation_telemetry = provider_result.operation_telemetry
        if operation_telemetry is not None:
            operation_telemetry = operation_telemetry.bind_reasoner_context(
                packet_ready_unix_us=packet_ready_us,
                provider_root_thread_id_sha256=(
                    self.provider_root_thread_id_sha256
                ),
                accepted_parent_checkpoint_id_sha256=(
                    self.accepted_parent_checkpoint_id_sha256
                ),
                candidate_checkpoint_id_sha256=(
                    self.candidate_checkpoint_id_sha256
                ),
            ).bind_validation_phase(
                validation_start_unix_us=validation_started_us,
                validation_completion_unix_us=validation_completed_us,
            )

        provider_receipt = provider_result.receipt
        return SceneReasonerAdapterCall(
            outcome=outcome,
            adapter_role=ReasonerAdapterRole.CODEX,
            adapter_version=CODEX_REASONER_ADAPTER_VERSION,
            adapter_evidence_id=self.transport.route.route_id,
            adapter_evidence_sha256=self.transport.route.route_sha256,
            provider_receipt_id=provider_receipt.provider_receipt_id,
            provider_receipt_sha256=provider_receipt.receipt_sha256,
            bridge_receipt_id=(
                bridge_receipt.bridge_receipt_id if bridge_receipt is not None else None
            ),
            bridge_receipt_sha256=(
                bridge_receipt.receipt_sha256 if bridge_receipt is not None else None
            ),
            external_provider_calls=provider_receipt.external_provider_calls,
            provider_call_receipt=provider_receipt,
            mcp_bridge_receipt=bridge_receipt,
            operation_telemetry=operation_telemetry,
        )


def build_codex_reasoner_packet(
    request: SceneReasonerRequest,
    *,
    evidence_tools_available: bool = True,
    compact_input: bool = False,
    scene_cast_scope: SceneCastScopeV1 | None = None,
    draft_schema_version: str = CodexReasonerDraftV6.SCHEMA_VERSION,
) -> dict[str, object]:
    prepared = request.prepared_turn
    intake = prepared.request
    if compact_input:
        compact_evidence = tuple(
            compact_evidence_view(
                value,
                alias=f"evidence:seed_{index:03d}",
            )
            for index, value in enumerate(
                request.seed_dossier.exact_seed_evidence, start=1
            )
        )
        if scene_cast_scope is None:
            scene_cast_scope = SceneCastScopeV1(
                schema_version=SceneCastScopeV1.SCHEMA_VERSION,
                protected_user_id=intake.protected_user_id,
                world_known_character_ids=prepared.present_character_ids,
                physically_present_character_ids=prepared.present_character_ids,
                scene_reachable_character_ids=prepared.present_character_ids,
                currently_active_character_ids=prepared.present_character_ids,
                exact_source_responder_ids=(),
                eligible_responder_ids=prepared.eligible_responding_npc_ids,
                selected_responder_ids=(),
                derivation_reasons=("bounded prepared-turn scope",),
            )
        if (
            scene_cast_scope.protected_user_id != intake.protected_user_id
            or scene_cast_scope.physically_present_character_ids
            != prepared.present_character_ids
            or scene_cast_scope.eligible_responder_ids
            != prepared.eligible_responding_npc_ids
        ):
            raise ContractValidationError(
                "compact scene scope does not match authoritative prepared turn"
            )
        capsule = build_reasoner_reading_capsule(
            request,
            compact_evidence,
            stable_prompt_identity=(
                COMPACT_REASONER_PROMPT_VERSION
                if draft_schema_version
                == CodexReasonerDraftV7Compact.SCHEMA_VERSION
                else CODEX_REASONER_PROMPT_VERSION
            ),
        )
        seed_payload = {
            "aware_character_ids": [
                str(value) for value in request.seed_dossier.aware_character_ids
            ],
            "compact_exact_evidence": [
                to_primitive(value) for value in compact_evidence
            ],
            "scene_anchors": list(request.seed_dossier.scene_anchors),
            "explicit_unknowns": list(request.seed_dossier.explicit_unknowns),
            "prohibited_inferences": list(
                request.seed_dossier.prohibited_inferences
            ),
        }
        identity_payload = {
            "protected_user_id": str(intake.protected_user_id),
            "route": prepared.route.value,
            "cast_scope": to_primitive(scene_cast_scope),
        }
    else:
        seed_payload = {
            "aware_character_ids": [
                str(value) for value in request.seed_dossier.aware_character_ids
            ],
            "exact_seed_evidence": [
                {
                    **to_primitive(value),
                    "citation_alias": f"evidence:seed_{index:03d}",
                }
                for index, value in enumerate(
                    request.seed_dossier.exact_seed_evidence, start=1
                )
            ],
            "scene_anchors": list(request.seed_dossier.scene_anchors),
            "explicit_unknowns": list(request.seed_dossier.explicit_unknowns),
            "prohibited_inferences": list(
                request.seed_dossier.prohibited_inferences
            ),
        }
        identity_payload = {
            "protected_user_id": str(intake.protected_user_id),
            "route": prepared.route.value,
            "present_character_ids": [
                str(value) for value in prepared.present_character_ids
            ],
            "eligible_responder_ids": [
                str(value) for value in prepared.eligible_responding_npc_ids
            ],
        }
    return {
        "schema_version": (
            COMPACT_REASONER_PACKET_VERSION
            if compact_input
            else CODEX_REASONER_PACKET_VERSION
        ),
        "prompt_version": (
            COMPACT_REASONER_PROMPT_VERSION
            if draft_schema_version == CodexReasonerDraftV7Compact.SCHEMA_VERSION
            else CODEX_REASONER_PROMPT_VERSION
        ),
        "identity": identity_payload,
        "source_view": {
            "mode": request.source_view.mode.value,
            "units": [to_primitive(value) for value in request.source_view.units],
            "contains_exact_protected_adult_prose": (
                request.source_view.contains_exact_protected_adult_prose
            ),
        },
        "seed_dossier": seed_payload,
        **(
            {"reading_capsule": to_primitive(capsule)}
            if compact_input
            else {}
        ),
        "hard_boundaries": list(request.hard_boundaries),
        "scene_development_contract": build_scene_development_contract(
            request.scene_depth_mode
        ),
        "behavioral_controls": to_primitive(request.behavioral_controls),
        "creator_revision": (
            to_primitive(request.creator_revision)
            if request.creator_revision is not None
            else None
        ),
        "tool_policy": {
            "evidence_tools_available": evidence_tools_available,
            "search_is_reference_only": True,
            "hard_decisions_require_exact_fetch_or_exact_seed": True,
            "maximum_followup_search_operations": 4,
            "shared_search_budget_operations": [
                "cera_resolve_entities",
                "cera_search_evidence",
                "cera_search_query_plan",
            ],
            "exact_fetch_uses_followup_search_budget": False,
            "tool_results_publish_remaining_budget": True,
            "snapshot_refresh_forbidden": True,
            "filesystem_and_database_access_forbidden": True,
        },
        "output_policy": {
            "authority": "advisory_until_python_validation",
            "story_prose_forbidden": True,
            "canonical_ids_and_citations_python_owned": True,
            "provider_uses_local_claim_block_and_segment_keys": True,
            "provider_uses_request_local_evidence_citation_aliases": True,
            "protected_user_authorship_forbidden": True,
            "character_knowledge_transfer_forbidden": True,
            "unknowns_remain_unknown": True,
        },
    }


def build_codex_reasoner_prompt(
    packet: dict[str, object],
    *,
    draft_schema_version: str = CodexReasonerDraftV6.SCHEMA_VERSION,
) -> str:
    if draft_schema_version == CodexReasonerDraftV7Compact.SCHEMA_VERSION:
        return build_compact_v7_reasoner_prompt(packet)
    if draft_schema_version != CodexReasonerDraftV6.SCHEMA_VERSION:
        raise ContractValidationError("unsupported Reasoner prompt schema")
    tools_available = bool(packet["tool_policy"]["evidence_tools_available"])
    tool_instruction = (
        "Use exact seed evidence when sufficient. If more evidence is needed, use only the request-bound CERA tools. Start with cera_get_turn_snapshot and read cera_tool_budget after every tool result. cera_resolve_entities, cera_search_evidence, and cera_search_query_plan share exactly four search operations for the entire turn; resolving an entity is not free. Never call one of those tools when remaining_followup_searches is zero, never repeat a failed tool call, and never spend another search merely to restate a typed entity ID already present in the request. Prefer one cera_search_query_plan for indirect or paraphrased cues. Terms within one term set are all required while alternate term sets are OR variants, so use one or two discriminative concepts per set instead of copying the complete user sentence. Search results are references; fetch only exact expandable_sections advertised by the returned reference before using evidence in a hard decision. Once exact fetched evidence is sufficient, stop searching and complete the draft; if the bounded search cannot establish the needed fact, return insufficient_evidence. Never guess a section heading. The provider-facing cera_fetch_evidence tool expands one record per call: pass exactly evidence_id as one returned evidence ID and sections as an array of advertised section-name strings. For cera_search_evidence and cera_search_query_plan, record_types is optional and accepts only genesis_fact, source_fact, event_fact, memory, relationship, thread, material, or development. Use memory for private or recalled experience and relationship for directional relationship state. Python owns ambiguity handling and returns bounded candidates for exact follow-up; do not supply an ambiguity_policy field. Do not add fields."
        if tools_available
        else "No evidence tools are available for this call because Python marked the exact seed dossier sufficient. Use only that dossier; return insufficient_evidence instead of attempting or inventing a lookup."
    )
    return "\n".join(
        (
            "CERA typed Scene Reasoner invocation.",
            "Return exactly one CodexReasonerDraft v6 JSON object matching the supplied output schema.",
            "Interpret the current source, choose only eligible scene-reachable NPC responders, and produce broad operational SceneEventBlocks plus WriterScaffolds rather than story prose. Candidate eligibility does not assert physical presence: an unmentioned entrant needs a causally justified entrance or transition, while an unneeded household member must remain omitted.",
            "First classify every materially relevant exact source span in source_claims. Copy one short uniquely occurring quote, label its semantic kind, and assign only the authority permitted by behavioral_controls. source_authorized means the supplied claim may control that channel; nonbinding_input means it remains visible as a request but cannot determine the character; modification_proposal is allowed only in Modification mode; requires_creator_confirmation is provisional; hard_rejected is unavailable to planning and realization.",
            "Character autonomy is binding. With Mind or Both, user assertions about an NPC's thought, feeling, desire, interpretation, or other private mental state are nonbinding. With Body or Both, user assertions about an NPC's involuntary sensation, arousal, discharge, reflex, or other involuntary body state are nonbinding. Observable NPC actions, spoken words, positions, and material outcomes remain separately classifiable and are not erased merely because autonomy is enabled.",
            "Adjustment mode preserves supplied objective actions, dialogue meaning, order, and outcome while allowing causal framing and transitions. Modification mode may propose a minor wording, ordering, or reversible connective change, but never changes identity, consent, privacy, knowledge, branch facts, or another hard boundary. Never disguise a modification as an inference.",
            "creator_revision is out-of-world creator control, never story source, Ted dialogue, character knowledge, or canon. When present, inspect the bound prior sequence, rejected candidate, Sol reason codes, and creator feedback. For reasoner_replan, replace the complete current plan while preserving valid source/evidence boundaries. For correction_adjustment, make the smallest sufficient logic or framing change requested, but still return a complete plan. Do not merely patch one beat and do not repeat rejected logic that the feedback identifies.",
            "Own the causal sequence, not merely the first reaction. Plan the complete meaningful current reply from the supplied cue through supported NPC-controlled consequences and a useful afterbeat, then stop at a meaningful user-relevant boundary after that sequence. Do not stop simply because Ted could respond earlier, and do not extend to the latest event that could occur before he is mechanically required. A short user cue does not imply a one-beat reply or one-block reply; causal significance, scene stakes, available NPC agency, and the typed scene_development_contract determine the justified scope.",
            "The scene_development_contract is a binding current-reply scope obligation, not optional permission and not a numeric beat quota. Follow its mode-specific instruction. Off is a legacy minimal mode. Short requests one concise complete causal unit. Auto classifies atomic, developed ordinary, domino, or multi-scene scope. Medium affirmatively requests a substantively developed current scene with useful lead-in, distinct consequences, and afterbeat. Long requires the full supported current-scene causal chain. Epic requires every materially distinct supported NPC-controlled consequence across available phases or mini-scenes, then stops at the contract's first natural protected-user handoff. A short source must not silently downgrade Medium, Long, or Epic. Padding, repetition, invented events, and protected-user invention remain forbidden.",
            "Under Auto, default to developed ordinary scope and expand to domino or multi-scene scope from causal significance, stakes, available character agency, and supported continuation. Use atomic scope only when any further NPC-controlled progress would be unsupported or repetitive. A developed ordinary plan must move beyond the floor owner's first complete answer or reaction into a materially different supported follow-through, such as a changed tactic, immediate consequence, rational interruption or entrance, material-state change, or meaningful aftereffect. This may stay with one NPC or add a scene-reachable participant when causally justified. Do not use a fixed beat count or literal doubling formula. Detailed wording of one unchanged action is not additional scene development.",
            "Each event_block must represent one broad causal unit with a purpose, causal basis, one or more materially distinct event advances, and a resulting state. Its WriterScaffold teaches motivation and subtext, voice and selective interiority, physical/material continuity, transition obligations, and what creative space DeepSeek must retain. Do not prewrite exact prose, gestures, pacing, imagery, or dialogue unless exact source authority requires supplied wording.",
            "Select interaction_topology, scene_function, tone, and interiority_level from their exact enums as semantic realization guidance. Interiority is automatic and selective: use none or low when private thought adds no value, medium for useful character processing, and high only when sustained interior processing is central. These selectors never override evidence, character autonomy, or event authority.",
            "For a substantive cue, plan beyond the first reaction through supported NPC appraisal, tactic, action or dialogue, immediate consequence, interruption, follow-through, or another rational participant's intervention. A question or confession is not automatically the stopping point. Set continue_beyond_prompt_endpoint true when supported consequences remain; stop only when a causal unit or scene transition is complete, a genuinely meaningful unsupplied user decision is required, a hard boundary is reached, or uncertainty is intentionally preserved.",
            "When the source supports several locations, phases, or mini-scenes without requiring a new protected-user action, include them as ordered event_blocks. Progression that depends on an unsupplied protected-user choice belongs only in future_segments.",
            "Never author a new action, dialogue, thought, feeling, or decision for the protected user.",
            "Do not encode relational or reciprocal wording in scene_intent, perception, selected_intent, action_direction, event blocks, writer scaffolds, writer_must_preserve, prohibited_inferences, or future consequences when it silently presupposes an unsupplied protected-user action. Describe only the NPC-controlled half unless an exact source-authorized protected-user claim permits the supplied reciprocal half. An NPC may look toward, speak to, approach, or react to supplied words; the plan must not turn that into a met gaze, returned smile, answered gesture, mutual touch, shared movement, or another reciprocal behavior without exact source authority.",
            "Every event_block has a precise protected_user_allowance. Exact supplied action/dialogue allowances must cite source_claim_keys that classify those exact claims as source_authorized. Maintaining an established position or minimal nonbranching connective motion may be allowed only when it creates no dialogue, thought, feeling, motive, consent, refusal, strategy, destination, commitment, or consequential new choice for Ted.",
            tool_instruction,
            "Reference every evidence item used by participation, a character move, an event block, or a development atom through its request-local citation_alias. In every evidence_ids field, copy only citation_alias values: seed aliases come from seed_dossier.exact_seed_evidence and fetched aliases come from successful exact-fetch results in this same invocation. Never copy or retype the canonical evidence UUID into output, never reuse an alias from an earlier stored turn, and never place a record_id, development_id, character_id, source_unit_id, branch/artifact identity, source-claim key, search reference, or self-invented identifier in an evidence_ids field. Python resolves current aliases to exact evidence IDs and derives record IDs, record versions, hard citations, claim spans, block/scaffold IDs, and all canonical hashes.",
            "Evidence visibility to the system-scoped Reasoner never transfers knowledge between characters. Bind every participation entry, character_move, event_block, and development atom to the actor or owner who is permitted to know and use each cited record. owner_private evidence may support only its exact owner. Evidence with knowledge_owner_ids may support only a listed knower. system_private evidence may support only a subject of that record. Public evidence may support any causally relevant actor. For a multi-character block, split the block or cite only evidence every affected actor may use; never use one NPC's private relationship, feeling, plan, memory, or suspicion as another NPC's reason to enter, speak, act, or change. Being in the candidate cast, seed dossier, or same household does not grant knowledge.",
            "For every selected responder, ground the character move in an exact directional Ted relationship plus stable character evidence such as core premise or fidelity invariants, and retrieve any scene-relevant state, memory, or development record needed for the choice. Compact seed claims are candidate-selection aids, not substitutes for exact expandable source_text when their summary is insufficient. Cite only evidence actually used. This requirement applies by behavior class, not by a memorized test phrase.",
            "When the planned scene depends on concrete existing world information—such as household rules, routines, schedules, layout, chores, possessions, procedures, history, or prior agreements—character traits and generic scene logic are insufficient. Use exact seed evidence or search and fetch the authoritative factual record before planning those specifics, then cite it on every move or event block that communicates or relies on them. If exact evidence does not establish a detail, keep the plan at the supported level or mark the fact unknown; never ask the Composer to fill factual substance from convention. An information-transfer or orientation block must identify its authorized factual topics in causal_basis, event_advances, and the WriterScaffold rather than merely ordering the Composer to be detailed.",
            "When the current moment materially concerns a selected speaker's body, dignity, refusal, family defense, objectification, betrayal, or trust fracture and the active Genesis advertises character_expression evidence, retrieve only that speaker's relevant embodied profile, rhetorical signature, or one response-mode record. Fetch exact advertised sections, use them to select meaning and tactic, and cite them on the character move or beat. Do not fetch all modes, all speakers, or examples for an ordinary unrelated scene.",
            "Expression examples are non-executable authoring evidence: never copy them, schedule their scenario, infer Ted's motive from them, or treat them as past dialogue. Refusal, consent, attraction, objectification, bodily response, and trust consequence remain separate semantics.",
            "development_atoms are optional, provisional, one-step state proposals, never story events or durable authority. Add an atom only when a current event block can visibly support a small owner-specific change worth later retrieval. Separate involuntary body signals, immediate affect, noticed signals, interpretation hypotheses, micro-development, relationship observations, and reproductive evidence instead of chaining one into another. Every atom cites its current source_block_keys and exact evidence already used by the plan. An entirely new tendency advances absent to trace with no predecessor. Any later advance must cite one or more exactly retrieved predecessor development record IDs and move exactly one adjacent level: trace to emerging, emerging to supported, or supported to established. If that exact predecessor development record was not retrieved for this turn, do not propose a later-strength atom; return no atom for that tendency. Never jump, infer love or jealousy from attraction or contact, convert a home test into clinical confirmation, or turn a momentary response into identity. Use inference_limit to state what the atom does not establish. Return an empty array when no durable development is supported.",
            "Choose one floor_owner_id. Python derives the lead role and floor_owner label. For that participant set intervention_reason to null; every other participant must use a non-floor_owner semantic intervention reason.",
            "Respect branch, knowledge-owner, privacy, identity, consent/capacity, source-state, cast, floor, and stop boundaries. Do not infer missing facts.",
            "For decision_ready, use short stable local claim_key, block_key, and segment_key values. Python derives every canonical identity and span. Use writer_must_preserve only for sequence-wide obligations not already owned by a block scaffold. Future segments are conditional possibilities, never committed events.",
            "Status matrix: decision_ready requires route, scene_intent, one or more responding_npc_ids, floor_owner_id, matching participation and character_moves, one or more source_claims, one or more event_blocks, causal_runway, interaction_topology, scene_function, tone, and interiority_level; it forbids insufficiencies and blocker_code. insufficient_evidence requires one or more insufficiencies and otherwise null/empty decision fields with blocker_code null. blocked requires only CERA_BLOCKED_NONCONSENSUAL_EVENT and null/empty decision fields with no insufficiencies.",
            "NON-READY RESET IS MANDATORY: if the final status is insufficient_evidence or blocked, discard all tentative decision work before emitting JSON. Set route, scene_intent, floor_owner_id, causal_runway, interaction_topology, scene_function, tone, interiority_level, and adult_craft_need to null; set responding_npc_ids, participation, character_moves, source_claims, event_blocks, development_atoms, and future_segments to empty arrays. Never preserve source classification or a partial plan beside a non-ready status. Python will reject the complete response if even one forbidden decision field survives.",
            "Every array that represents a semantic set must contain distinct items. Never repeat responder IDs, participant or move character IDs, evidence IDs within one owner, directive strings, adult families/subfamilies, adult beat keys, object concepts, axes, channel-owner pairs, required concepts, character-card owners, or section queries. Ordered beat and future-segment arrays still require unique local keys.",
            "For a consent-valid adult decision, provide block-local AdultCraftNeedDraft v3 semantics. Each current adult block uses its block_key in the existing beat_key field and contains its own axes and channel_requirements. Dialogue and inner_voice require character_id; narration, sound_effect, and physiology forbid character ownership. semantic_concepts describe meanings and craft techniques that must be realized; lexical_concepts are only the distinct subset genuinely requiring explicit vocabulary. For ordinary, insufficient, or blocked outcomes, adult_craft_need must be null.",
            "Adult beat_requirements must be a non-empty subset of current event_blocks and include every current block needing adult-specific craft. Do not invent a key or describe a future block as current.",
            "Treat active_content_families in the non-graphic ledger as Python-authorized craft-routing evidence: toilet_continuity maps to toilet_scat, infidelity_drama maps to infidelity_drama, and general_intimacy/roleplay/body_response_continuity/fluid_continuity normally map to general unless a validated current beat clearly requires a narrower schema family. Never invent a narrower family from an unselected future possibility.",
            "character_card_sections is optional. Use an empty array unless an exact speech-system heading was present in fetched evidence; never invent or paraphrase a section heading.",
            "If authorized evidence is insufficient, return insufficient_evidence without a decision. If the supplied contract requires a blocker, return blocked without decision content.",
            "The complete authoritative packet follows as canonical JSON:",
            canonical_json(packet),
        )
    )


def reasoner_outcome_json_schema() -> dict[str, object]:
    """Compatibility name for the active provider-facing reasoner draft schema.

    Providers no longer author :class:`ReasonerOutcome`; Python compiles that
    authoritative record from the minimal semantic draft.
    """

    return codex_reasoner_draft_v6_json_schema()


def legacy_reasoner_outcome_json_schema() -> dict[str, object]:
    """Historical v1 provider schema retained for fixture migration only."""

    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "schema_version": {"const": ReasonerOutcome.SCHEMA_VERSION},
            "status": {
                "type": "string",
                "enum": [value.value for value in ReasonerOutcomeStatus],
            },
            "decision": {
                "anyOf": [{"$ref": "#/$defs/scene_decision"}, {"type": "null"}]
            },
            "participation": {
                "type": "array",
                "maxItems": 8,
                "items": {"$ref": "#/$defs/participation"},
            },
            "hard_citations": {
                "type": "array",
                "maxItems": 32,
                "items": {"$ref": "#/$defs/citation"},
            },
            "insufficiencies": {"$ref": "#/$defs/short_text_array"},
            "blocker_code": {
                "anyOf": [
                    {"const": ErrorCode.BLOCKED_NONCONSENSUAL_EVENT.value},
                    {"type": "null"},
                ]
            },
            "advisory_state_deltas": {
                "type": "array",
                "maxItems": 16,
                "items": {"$ref": "#/$defs/state_delta"},
            },
            "protected_user_boundary_acknowledged": {"const": True},
            "adult_craft_need": {
                "anyOf": [adult_craft_need_provider_draft_json_schema(), {"type": "null"}]
            },
        },
        "required": [
            "schema_version",
            "status",
            "decision",
            "participation",
            "hard_citations",
            "insufficiencies",
            "blocker_code",
            "advisory_state_deltas",
            "protected_user_boundary_acknowledged",
            "adult_craft_need",
        ],
        "$defs": _reasoner_schema_definitions(),
    }
    return _add_structured_output_types(schema)


def _bind_python_owned_adult_fields(
    request: SceneReasonerRequest,
    provider_payload: dict[str, object],
) -> dict[str, object]:
    """Replace provider placeholders with request-bound canonical values."""

    payload = dict(provider_payload)
    craft_need = payload.get("adult_craft_need")
    if craft_need is None:
        return payload
    if not isinstance(craft_need, dict):
        raise ContractValidationError("Codex adult craft need must be an object")
    for field_name in (
        "craft_need_id",
        "request_id",
        "decision_id",
        "sequence_plan_sha256",
    ):
        if craft_need.get(field_name) != PROVIDER_BINDING_PLACEHOLDER:
            raise ContractValidationError(
                f"Codex adult craft need changed Python-owned field {field_name}"
            )
    decision_payload = payload.get("decision")
    if not isinstance(decision_payload, dict):
        raise ContractValidationError("Codex adult craft need requires a SceneDecision")
    decision = from_mapping(SceneDecision, decision_payload)
    sequence_sha256 = domain_sha256(
        "cera.sequence_plan.v1",
        (decision.current_segment, decision.future_segments),
    )
    bound_need = dict(craft_need)
    bound_need.update(
        {
            "craft_need_id": str(
                deterministic_id(
                    IdKind.ADULT_CRAFT_NEED,
                    "cera.adult_craft_need.v1",
                    f"{request.prepared_turn.request.request_id}|{decision.decision_id}|{sequence_sha256}",
                )
            ),
            "request_id": str(request.prepared_turn.request.request_id),
            "decision_id": str(decision.decision_id),
            "sequence_plan_sha256": sequence_sha256,
        }
    )
    payload["adult_craft_need"] = bound_need
    return payload


def _normalize_provider_structural_fields(
    provider_payload: dict[str, object],
) -> dict[str, object]:
    """Canonicalize fields whose value is wholly implied by provider choices.

    Codex still owns floor and participation selection.  Once it selects a
    lead whose identity equals the selected floor owner, the domain value for
    that lead's intervention reason is mechanically ``floor_owner``.  Python
    writes that redundant label so a semantically valid choice cannot fail due
    only to an inconsistent restatement.
    """

    payload = dict(provider_payload)
    decision = payload.get("decision")
    participation = payload.get("participation")
    if not isinstance(decision, dict) or not isinstance(participation, list):
        return payload
    floor_owner_id = decision.get("floor_owner_id")
    normalized: list[object] = []
    for item in participation:
        if not isinstance(item, dict):
            normalized.append(item)
            continue
        entry = dict(item)
        if entry.get("role") == "lead" and entry.get("character_id") == floor_owner_id:
            entry["intervention_reason"] = "floor_owner"
        normalized.append(entry)
    payload["participation"] = normalized
    return payload


def _reasoner_schema_definitions() -> dict[str, object]:
    short_text = {"type": "string", "minLength": 1, "maxLength": 500}
    text_array = {
        "type": "array",
        "maxItems": 32,
        "items": short_text,
    }
    return {
        "short_text_array": text_array,
        "participation": _closed_object(
            {
                "character_id": _id_schema(IdKind.CHARACTER),
                "role": {
                    "type": "string",
                    "enum": [value.value for value in ParticipationRole],
                },
                "intervention_reason": {
                    "type": "string",
                    "enum": [value.value for value in InterventionReason],
                },
                "evidence_ids": _id_array(IdKind.EVIDENCE, maximum=16),
            }
        ),
        "citation": _closed_object(
            {
                "evidence_id": _id_schema(IdKind.EVIDENCE),
                "record_id": _id_schema(*AUTHORITY_RECORD_ID_KIND_ORDER),
                "record_version": {"type": "integer", "minimum": 1},
            }
        ),
        "character_move": _closed_object(
            {
                "character_id": _id_schema(IdKind.CHARACTER),
                "perception": short_text,
                "selected_intent": short_text,
                "action_direction": short_text,
                "evidence_ids": _id_array(IdKind.EVIDENCE, maximum=16),
                "knowledge_constraints": text_array,
            }
        ),
        "sequence_beat": _closed_object(
            {
                "beat_id": _id_schema(IdKind.BEAT),
                "actor_id": _id_schema(IdKind.CHARACTER, IdKind.MATERIAL),
                "state": {
                    "type": "string",
                    "enum": [value.value for value in BeatState],
                },
                "neutral_event": short_text,
                "evidence_ids": _id_array(IdKind.EVIDENCE, maximum=16),
            }
        ),
        "current_segment": _closed_object(
            {
                "segment_id": _id_schema(IdKind.SEGMENT),
                "ordered_beats": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 32,
                    "items": {"$ref": "#/$defs/sequence_beat"},
                },
                "stop_before": short_text,
            }
        ),
        "future_segment": _closed_object(
            {
                "segment_id": _id_schema(IdKind.SEGMENT),
                "status": {"const": "conditional_plan_only"},
                "activation_conditions": text_array,
                "invalidation_conditions": text_array,
                "possible_consequences": text_array,
                "open_user_choice": short_text,
            }
        ),
        "scene_decision": _closed_object(
            {
                "schema_version": {"const": "cera.scene_decision.v1"},
                "decision_id": _id_schema(IdKind.DECISION),
                "route": {
                    "type": "string",
                    "enum": [
                        DecisionRoute.ORDINARY.value,
                        DecisionRoute.CONSENT_VALID_ADULT.value,
                    ],
                },
                "scene_intent": short_text,
                "responding_npc_ids": _id_array(
                    IdKind.CHARACTER, minimum=1, maximum=8
                ),
                "floor_owner_id": {
                    "anyOf": [_id_schema(IdKind.CHARACTER), {"type": "null"}]
                },
                "character_moves": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 8,
                    "items": {"$ref": "#/$defs/character_move"},
                },
                "current_segment": {"$ref": "#/$defs/current_segment"},
                "future_segments": {
                    "type": "array",
                    "maxItems": 8,
                    "items": {"$ref": "#/$defs/future_segment"},
                },
                "writer_must_preserve": text_array,
                "uncertainties": text_array,
                "prohibited_inferences": text_array,
                "advisory_state_candidates": text_array,
            }
        ),
        "state_delta": _closed_object(
            {
                "record_id": _id_schema(*AUTHORITY_RECORD_ID_KIND_ORDER),
                "branch_id": _id_schema(IdKind.BRANCH),
                "mutation_target": {
                    "type": "string",
                    "enum": [StateMutationTarget.BRANCH_OVERLAY.value],
                },
                "subject_ids": _generic_id_array(minimum=1, maximum=16),
                "evidence_ids": _id_array(
                    IdKind.EVIDENCE, minimum=1, maximum=16
                ),
                "protected_user_provenance": {
                    "type": "string",
                    "enum": [value.value for value in ProtectedUserProvenance],
                },
            }
        ),
    }


def _closed_object(properties: dict[str, object]) -> dict[str, object]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
        "required": list(properties),
    }


def _id_schema(*kinds: IdKind) -> dict[str, object]:
    alternatives = "|".join(value.value for value in kinds)
    return {
        "type": "string",
        "pattern": rf"^(?:{alternatives}):[A-Za-z0-9][A-Za-z0-9._-]{{0,127}}$",
    }


def _id_array(
    *kinds: IdKind,
    minimum: int = 0,
    maximum: int,
) -> dict[str, object]:
    return {
        "type": "array",
        "minItems": minimum,
        "maxItems": maximum,
        "items": _id_schema(*kinds),
    }


def _generic_id_array(*, minimum: int, maximum: int) -> dict[str, object]:
    return {
        "type": "array",
        "minItems": minimum,
        "maxItems": maximum,
        "items": {
            "type": "string",
            "pattern": "^[a-z][a-z0-9_]*:[A-Za-z0-9][A-Za-z0-9._-]{0,127}$",
        },
    }


def _add_structured_output_types(value):
    """Add JSON primitive types required by Codex structured outputs.

    Domain schemas often use a concise enum/const projection.  The provider's
    strict response-format dialect requires those nodes to state their JSON
    type explicitly.  This transformation changes no accepted values; Python
    typed decoding remains the final validator.
    """

    if isinstance(value, list):
        return [_add_structured_output_types(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {
        key: _add_structured_output_types(item)
        for key, item in value.items()
    }
    if "type" not in result:
        sample = None
        if "const" in result:
            sample = result["const"]
        elif isinstance(result.get("enum"), list) and result["enum"]:
            sample = result["enum"][0]
        if isinstance(sample, bool):
            result["type"] = "boolean"
        elif isinstance(sample, str):
            result["type"] = "string"
        elif isinstance(sample, int):
            result["type"] = "integer"
        elif sample is None and ("const" in result or "enum" in result):
            result["type"] = "null"
    return result


def _provider_failure(
    exc: ProviderTransportError,
    *,
    bridge: ReasonerEvidenceBridgePort | None = None,
) -> SceneReasonerPortFailure:
    evidence_codes = {
        ErrorCode.EVIDENCE_SERVICE_UNAVAILABLE,
        ErrorCode.EVIDENCE_SNAPSHOT_STALE,
        ErrorCode.EVIDENCE_ACCESS_DENIED,
        ErrorCode.EVIDENCE_LIMIT_EXCEEDED,
        ErrorCode.EVIDENCE_INDEX_INVALID,
        ErrorCode.EVIDENCE_BRIDGE_UNAVAILABLE,
        ErrorCode.EVIDENCE_BRIDGE_CONTRACT_INVALID,
    }
    stage = "reasoner_evidence" if exc.code in evidence_codes else "reasoner_dispatch"
    if exc.code is ErrorCode.REASONER_CONTRACT_INVALID:
        stage = "reasoner_validation"
    message = str(exc)
    dispatcher = getattr(bridge, "dispatcher", None)
    calls = tuple(getattr(dispatcher, "calls", ()))
    failed = tuple(value for value in calls if not value.success)
    safe_diagnostics: tuple[str, ...] = tuple(exc.safe_diagnostics)
    if failed:
        trace = ",".join(
            (
                f"{value.call_index}:{value.tool_name.value}:{value.error_code.value}:"
                f"{value.error_field_path or '-'}:{value.error_reason or '-'}"
            )
            for value in failed
            if value.error_code is not None
        )
        message = f"{message}; safe_failed_tool_trace={trace}"
        safe_diagnostics = (
            *safe_diagnostics,
            *tuple(
                (
                    f"mcp_call[{value.call_index}]."
                    f"{value.error_field_path or value.tool_name.value}:"
                    f"{value.error_reason or 'failed'}"
                )
                for value in failed
            ),
        )
    failure = SceneReasonerPortFailure(
        exc.code,
        message,
        stage,
        safe_diagnostics=safe_diagnostics,
        external_provider_calls_observed=exc.external_provider_calls_observed,
    )
    failure.provider_call_receipt = getattr(exc, "provider_call_receipt", None)
    failure.operation_telemetry = getattr(exc, "operation_telemetry", None)
    return failure
