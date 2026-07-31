"""Typed Codex Scene Reasoner adapter over the request-bound evidence bridge."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

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
from cera.ids import AUTHORITY_RECORD_ID_KIND_ORDER, IdKind, deterministic_id
from cera.kernel import ProtectedUserProvenance, StateMutationTarget
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
    ReasonerDraftSemanticError,
    codex_reasoner_draft_v2_json_schema,
    codex_reasoner_draft_v3_json_schema,
    codex_reasoner_draft_v4_json_schema,
    compile_reasoner_draft,
    validate_reasoner_draft_payload_semantics,
)
from .mcp_bridge import (
    McpEvidenceBridgeError,
    McpEvidenceBridgeReceipt,
    RequestBoundMcpEvidenceBridge,
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


CODEX_REASONER_ADAPTER_VERSION = "cera.codex_scene_reasoner.v13"
CODEX_REASONER_PACKET_VERSION = "cera.codex_scene_reasoner_packet.v8"
CODEX_REASONER_PROMPT_VERSION = "cera.codex_scene_reasoner_prompt.v13"


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


class CodexSceneReasonerPort(SceneReasonerPort):
    """One Codex call, one request bridge, one typed advisory outcome."""

    def __init__(
        self,
        transport: CodexSDKTransport,
        *,
        bridge_factory: ReasonerEvidenceBridgeFactory | None = None,
        maximum_tool_calls: int = 12,
        evidence_tools_enabled: bool = True,
    ) -> None:
        if type(maximum_tool_calls) is not int or not 1 <= maximum_tool_calls <= 32:
            raise ContractValidationError("reasoner MCP maximum tool calls must be 1..32")
        self.transport = transport
        self.maximum_tool_calls = maximum_tool_calls
        self.evidence_tools_enabled = evidence_tools_enabled
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
        )
        prompt = build_codex_reasoner_prompt(packet)
        output_schema = codex_reasoner_draft_v4_json_schema(
            allowed_evidence_ids=(
                None
                if self.evidence_tools_enabled
                else tuple(
                    value.evidence_id
                    for value in request.seed_dossier.exact_seed_evidence
                )
            )
        )
        bridge = None
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

        try:
            if provider_result.parsed_json is None:
                raise ContractValidationError("Codex reasoner omitted its JSON object")
            validate_reasoner_draft_payload_semantics(provider_result.parsed_json)
            draft_version = provider_result.parsed_json.get("schema_version")
            draft_type = (
                CodexReasonerDraftV4
                if draft_version == CodexReasonerDraftV4.SCHEMA_VERSION
                else CodexReasonerDraftV3
                if draft_version == CodexReasonerDraftV3.SCHEMA_VERSION
                else CodexReasonerDraftV2
            )
            draft = from_mapping(draft_type, provider_result.parsed_json)
            authorized = tuple(
                {
                    value.evidence_id: value
                    for value in (
                        *request.seed_dossier.exact_seed_evidence,
                        *tuple(getattr(tools, "exact_records", {}).values()),
                    )
                }.values()
            )
            outcome = compile_reasoner_draft(
                request,
                draft,
                authorized_exact_evidence=authorized,
            )
        except (ContractValidationError, IdentityError) as exc:
            diagnostics = (
                exc.diagnostics
                if isinstance(exc, ReasonerDraftSemanticError)
                else ()
            )
            failure = SceneReasonerPortFailure(
                ErrorCode.REASONER_CONTRACT_INVALID,
                f"Codex reasoner output failed typed decoding: {exc}",
                "reasoner_validation",
                safe_diagnostics=diagnostics,
            )
            failure.provider_call_receipt = provider_result.receipt
            failure.mcp_bridge_receipt = bridge_receipt
            raise failure from exc

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
        )


def build_codex_reasoner_packet(
    request: SceneReasonerRequest,
    *,
    evidence_tools_available: bool = True,
) -> dict[str, object]:
    prepared = request.prepared_turn
    intake = prepared.request
    return {
        "schema_version": CODEX_REASONER_PACKET_VERSION,
        "prompt_version": CODEX_REASONER_PROMPT_VERSION,
        "identity": {
            "protected_user_id": str(intake.protected_user_id),
            "route": prepared.route.value,
            "present_character_ids": [
                str(value) for value in prepared.present_character_ids
            ],
            "eligible_responder_ids": [
                str(value) for value in prepared.eligible_responding_npc_ids
            ],
        },
        "source_view": {
            "mode": request.source_view.mode.value,
            "units": [to_primitive(value) for value in request.source_view.units],
            "contains_exact_protected_adult_prose": (
                request.source_view.contains_exact_protected_adult_prose
            ),
        },
        "seed_dossier": {
            "aware_character_ids": [
                str(value) for value in request.seed_dossier.aware_character_ids
            ],
            "exact_seed_evidence": [
                to_primitive(value)
                for value in request.seed_dossier.exact_seed_evidence
            ],
            "scene_anchors": list(request.seed_dossier.scene_anchors),
            "explicit_unknowns": list(request.seed_dossier.explicit_unknowns),
            "prohibited_inferences": list(
                request.seed_dossier.prohibited_inferences
            ),
        },
        "hard_boundaries": list(request.hard_boundaries),
        "scene_development_contract": build_scene_development_contract(
            request.scene_depth_mode
        ),
        "tool_policy": {
            "evidence_tools_available": evidence_tools_available,
            "search_is_reference_only": True,
            "hard_decisions_require_exact_fetch_or_exact_seed": True,
            "snapshot_refresh_forbidden": True,
            "filesystem_and_database_access_forbidden": True,
        },
        "output_policy": {
            "authority": "advisory_until_python_validation",
            "story_prose_forbidden": True,
            "canonical_ids_and_citations_python_owned": True,
            "provider_uses_local_beat_and_segment_keys": True,
            "protected_user_authorship_forbidden": True,
            "unknowns_remain_unknown": True,
        },
    }


def build_codex_reasoner_prompt(packet: dict[str, object]) -> str:
    tools_available = bool(packet["tool_policy"]["evidence_tools_available"])
    tool_instruction = (
        "Use exact seed evidence when sufficient. If more evidence is needed, use only the request-bound CERA tools. Search results are references; fetch only exact expandable_sections advertised by the returned reference before using evidence in a hard decision. Never guess a section heading. The provider-facing cera_fetch_evidence tool expands one record per call: pass exactly evidence_id as one returned evidence ID and sections as an array of advertised section-name strings. For cera_search_evidence and cera_search_query_plan, record_types is optional and accepts only genesis_fact, source_fact, event_fact, memory, relationship, thread, material, or development. Use memory for private or recalled experience and relationship for directional relationship state. Python owns ambiguity handling and returns bounded candidates for exact follow-up; do not supply an ambiguity_policy field. Do not add fields."
        if tools_available
        else "No evidence tools are available for this call because Python marked the exact seed dossier sufficient. Use only that dossier; return insufficient_evidence instead of attempting or inventing a lookup."
    )
    return "\n".join(
        (
            "CERA typed Scene Reasoner invocation.",
            "Return exactly one CodexReasonerDraft v4 JSON object matching the supplied output schema.",
            "Interpret the current source, choose only eligible aware NPC responders, and produce operational causal beats rather than story prose.",
            "Own the causal sequence, not merely the first reaction. Plan the complete meaningful current reply from the supplied cue through supported NPC-controlled consequences, then stop at the earliest natural user-relevant handoff after that sequence. Do not extend to the latest event that could occur before the protected user is mechanically required. A short user cue does not imply a one-beat reply; causal significance, scene stakes, available NPC agency, and the typed scene_development_contract determine the justified scope.",
            "The scene_development_contract is a binding current-reply scope obligation, not optional permission and not a numeric beat quota. Follow its mode-specific instruction. Off requests compactness. Auto classifies atomic, developed ordinary, or domino/multi-scene scope. Long affirmatively requires the full supported current-scene causal chain. Epic affirmatively requires every materially distinct supported NPC-controlled consequence across available phases or mini-scenes, then stops at the contract's first natural protected-user handoff. A short source must not silently downgrade Long or Epic. Padding, repetition, invented events, and protected-user invention remain forbidden.",
            "Under auto, first classify the reply as an atomic exchange, developed ordinary interaction, or domino/multi-scene progression. An atomic exchange may need only one or two beats, a developed ordinary interaction commonly needs two to four, and a supported domino progression commonly needs four to six or more. These ranges are advisory. Appraisal or attention shift, character-specific choice or tactic, concrete dialogue or physical response, and immediate consequence or floor handoff are common functions, not a mandatory template.",
            "Each current beat must add a distinct realizable change controlled by its actor. Questions, gestures, or sentences that serve the same tactic without an intervening change of knowledge, emotion, action, relationship pressure, material state, or conversational floor belong in one beat. Do not disguise one summarized reaction as several synonymous beats or split one verification tactic into several differently worded requests. Conversely, do not compress perception, choice, response, consequence, and supported follow-on character agency into one vague beat when they are meaningfully separate. Give the Composer enough causal material to write a complete scene rather than forcing it to invent connective logic.",
            "For a substantive cue, plan beyond the first reaction: include the supported chain of NPC appraisal, choice, action or dialogue, immediate consequences, and further NPC-controlled developments until the earliest natural user-relevant handoff. For an atomic cue, stop compactly once its one meaningful tactic and handoff are complete. Depth targets are not permission to pad, repeat the source, or invent events.",
            "When the source supports several locations, phases, or mini-scenes without requiring a new protected-user action, include their supported progression in current_beats. When progression depends on an unsupplied protected-user choice, stop the current segment and place only conditional possibilities in future_segments.",
            "Never author a new action, dialogue, thought, feeling, or decision for the protected user.",
            "Do not encode relational or reciprocal wording in scene_intent, perception, selected_intent, action_direction, neutral_event, writer_must_preserve, prohibited_inferences, or future consequences when that wording silently presupposes an unsupplied protected-user action. Describe only the NPC-controlled half of an interaction unless an exact protected_user_source_claim authorizes the reciprocal half. An NPC may look toward, speak to, approach, or react to supplied words from the protected user; the decision must not turn that into a met gaze, returned smile, answered gesture, mutual touch, shared movement, or other reciprocal behavior without exact source authority.",
            "protected_user_source_claims is an allow-list, not a summary. For ordinary exact source only, include a claim solely when the final reply may reproduce or directly narrate a protected-user action or dialogue that is explicitly present. Copy a short exact uniquely occurring quote from the named source unit and label it action or dialogue. Use an empty array when the reply only needs NPC responses. Never infer a claim, quote an adult non-graphic ledger, or use a claim for thought, emotion, motive, consent, refusal, destination, or commitment.",
            "When a hard boundary requires complete creator-event source coverage, include enough exact claims to authorize every explicitly supplied protected-user action and dialogue kind that the Composer must directly narrate. This remains an allow-list for supplied source only; it never permits an addition.",
            tool_instruction,
            "Reference every evidence item used by participation, a character move, or a current beat by exact evidence_id only. Python derives record IDs, record versions, hard citations, and all canonical hashes.",
            "When the current moment materially concerns a selected speaker's body, dignity, refusal, family defense, objectification, betrayal, or trust fracture and the active Genesis advertises character_expression evidence, retrieve only that speaker's relevant embodied profile, rhetorical signature, or one response-mode record. Fetch exact advertised sections, use them to select meaning and tactic, and cite them on the character move or beat. Do not fetch all modes, all speakers, or examples for an ordinary unrelated scene.",
            "Expression examples are non-executable authoring evidence: never copy them, schedule their scenario, infer Ted's motive from them, or treat them as past dialogue. Refusal, consent, attraction, objectification, bodily response, and trust consequence remain separate semantics.",
            "Choose one floor_owner_id. Python derives the lead role and floor_owner label. For that participant set intervention_reason to null; every other participant must use a non-floor_owner semantic intervention reason.",
            "Respect branch, knowledge-owner, privacy, identity, consent/capacity, source-state, cast, floor, and stop boundaries. Do not infer missing facts.",
            "For decision_ready, use short stable local beat_key and segment_key values. Python derives canonical decision, segment, beat, craft-need, and requirement IDs. Keep each scene_intent and neutral_event concise, single-line, concrete, and operational; concision applies to each field, not to the completeness of the beat sequence. Use writer_must_preserve for sequence-wide pacing, character-voice, emotional-layer, and consequence obligations that the Composer must visibly realize. Future segments are conditional possibilities, never committed events.",
            "Status matrix: decision_ready requires route, scene_intent, one or more responding_npc_ids, floor_owner_id, matching participation and character_moves, one or more current_beats, and stop_before; it forbids insufficiencies and blocker_code. insufficient_evidence requires one or more insufficiencies and otherwise null/empty decision fields with blocker_code null. blocked requires only CERA_BLOCKED_NONCONSENSUAL_EVENT and null/empty decision fields with no insufficiencies.",
            "Every array that represents a semantic set must contain distinct items. Never repeat responder IDs, participant or move character IDs, evidence IDs within one owner, directive strings, adult families/subfamilies, adult beat keys, object concepts, axes, channel-owner pairs, required concepts, character-card owners, or section queries. Ordered beat and future-segment arrays still require unique local keys.",
            "For a consent-valid adult decision, provide beat-local AdultCraftNeedDraft v3 semantics. Each current adult beat uses its beat_key and contains its own axes and channel_requirements. Dialogue and inner_voice require character_id; narration, sound_effect, and physiology forbid character ownership. semantic_concepts describe meanings and craft techniques that must be realized; they never by themselves require an exact word. lexical_concepts must be a distinct subset of semantic_concepts and should contain only concepts for which explicit vocabulary is genuinely required in that exact channel. Use an empty lexical_concepts array for semantic-only structure, pacing, physiology, sound-shape, or continuity. Do not repeat global channel owners, write adult prose, or provide vocabulary examples. For ordinary, insufficient, or blocked outcomes, adult_craft_need must be null.",
            "Adult beat_requirements must be a non-empty subset of current_beats and must include every current beat requiring adult-specific craft. Do not invent a beat_key or describe a future beat as current.",
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

    return codex_reasoner_draft_v4_json_schema()


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
    return failure
