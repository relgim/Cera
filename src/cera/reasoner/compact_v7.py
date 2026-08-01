"""Shadow-only compact Reasoner v7 draft and deterministic v6 compiler.

The model authors semantic choices once.  Python expands only mechanical
duplication required by the existing validated v6 domain/compiler path.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import ClassVar

from cera.contracts import (
    DecisionRoute,
    InteractionTopology,
    InteriorityLevel,
    NaturalStopReason,
    PromptTone,
    ProtectedUserAllowanceKind,
    SceneFunction,
    SceneRunwayClass,
)
from cera.errors import ContractValidationError, ErrorCode
from cera.ids import IdKind, TypedId, require_kind
from cera.schema import require_schema
from cera.serialization import canonical_json, domain_sha256

from .drafts import (
    AdultCraftNeedDraftV3,
    CodexReasonerDraftV6,
    DraftCausalRunwayV5,
    DraftCharacterMoveV2,
    DraftDevelopmentAtomV6,
    DraftFutureSegmentV2,
    DraftParticipationV2,
    DraftProtectedUserAllowanceV5,
    DraftSceneEventBlockV5,
    DraftSourceClaimV5,
    DraftWriterScaffoldV5,
    codex_reasoner_draft_v6_json_schema,
)
from .models import InterventionReason, ReasonerOutcomeStatus


def _nonempty(value: str, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ContractValidationError(f"{field} must be non-empty")


def _unique(values, field: str) -> None:
    items = tuple(values)
    if len(items) != len(set(items)):
        raise ContractValidationError(f"{field} contains duplicates")


def _text_tuple(values: tuple[str, ...], field: str, *, required: bool = False) -> None:
    if not isinstance(values, tuple) or any(
        not isinstance(value, str) or not value.strip() for value in values
    ):
        raise ContractValidationError(f"{field} must contain non-empty strings")
    if required and not values:
        raise ContractValidationError(f"{field} cannot be empty")
    _unique(values, field)


@dataclass(frozen=True, slots=True)
class CompactResponderV7:
    character_id: TypedId
    intervention_reason: InterventionReason | None
    perception_basis: str
    intent: str
    tactic: str
    evidence_ids: tuple[TypedId, ...]
    knowledge_limits: tuple[str, ...]

    def __post_init__(self) -> None:
        require_kind(self.character_id, IdKind.CHARACTER, "character_id")
        if self.intervention_reason is not None and not isinstance(
            self.intervention_reason, InterventionReason
        ):
            raise ContractValidationError("compact responder intervention is invalid")
        if self.intervention_reason is InterventionReason.FLOOR_OWNER:
            raise ContractValidationError("v7 floor_owner label is Python-owned")
        for value in (self.perception_basis, self.intent, self.tactic):
            _nonempty(value, "compact responder semantics")
        for evidence_id in self.evidence_ids:
            require_kind(evidence_id, IdKind.EVIDENCE, "evidence_ids")
        _unique(self.evidence_ids, "compact responder evidence")
        _text_tuple(self.knowledge_limits, "compact responder knowledge limits")


@dataclass(frozen=True, slots=True)
class CompactMaterialTransitionV7:
    material_id: TypedId
    state_before: str
    state_after: str

    def __post_init__(self) -> None:
        require_kind(self.material_id, IdKind.MATERIAL, "material_id")
        _nonempty(self.state_before, "material state_before")
        _nonempty(self.state_after, "material state_after")
        if self.state_before == self.state_after:
            raise ContractValidationError("material transition must change state")


@dataclass(frozen=True, slots=True)
class CompactBeatV7:
    beat_key: str
    actor_ids: tuple[TypedId, ...]
    goal: str
    causal_basis: str
    advances: tuple[str, ...]
    result: str
    evidence_ids: tuple[TypedId, ...]
    source_claim_keys: tuple[str, ...]
    protected_user_allowed_kinds: tuple[ProtectedUserAllowanceKind, ...]
    protected_user_source_claim_keys: tuple[str, ...]
    continuity: tuple[str, ...]
    material_transitions: tuple[CompactMaterialTransitionV7, ...]
    realization_space: tuple[str, ...]

    def __post_init__(self) -> None:
        _nonempty(self.beat_key, "beat_key")
        if not self.actor_ids or not self.advances or not self.realization_space:
            raise ContractValidationError("compact beat lacks required semantics")
        for value in (self.goal, self.causal_basis, self.result):
            _nonempty(value, "compact beat semantics")
        for actor_id in self.actor_ids:
            if actor_id.kind not in {IdKind.CHARACTER, IdKind.MATERIAL}:
                raise ContractValidationError("compact beat actor kind is invalid")
        for evidence_id in self.evidence_ids:
            require_kind(evidence_id, IdKind.EVIDENCE, "evidence_ids")
        for field, required in (
            ("advances", True),
            ("source_claim_keys", False),
            ("protected_user_source_claim_keys", False),
            ("continuity", False),
            ("realization_space", True),
        ):
            _text_tuple(getattr(self, field), f"compact beat {field}", required=required)
        for field in (
            "actor_ids",
            "evidence_ids",
            "protected_user_allowed_kinds",
            "material_transitions",
        ):
            _unique(getattr(self, field), f"compact beat {field}")


@dataclass(frozen=True, slots=True)
class CodexReasonerDraftV7Compact:
    SCHEMA_VERSION: ClassVar[str] = "cera.codex_reasoner_draft.v7.compact_shadow"

    schema_version: str
    status: ReasonerOutcomeStatus
    route: DecisionRoute | None
    scene_goal: str | None
    reason_code: str | None
    responders: tuple[CompactResponderV7, ...]
    floor_owner_id: TypedId | None
    source_claims: tuple[DraftSourceClaimV5, ...]
    beats: tuple[CompactBeatV7, ...]
    runway_class: SceneRunwayClass | None
    continue_beyond_prompt_endpoint: bool | None
    stop_reason: NaturalStopReason | None
    stop_condition: str | None
    interaction_topology: InteractionTopology | None
    scene_function: SceneFunction | None
    tone: PromptTone | None
    interiority_level: InteriorityLevel | None
    essential_continuity: tuple[str, ...]
    future_segments: tuple[DraftFutureSegmentV2, ...]
    uncertainties: tuple[str, ...]
    prohibited_inferences: tuple[str, ...]
    insufficiencies: tuple[str, ...]
    blocker_code: ErrorCode | None
    adult_craft_need: AdultCraftNeedDraftV3 | None
    development_atoms: tuple[DraftDevelopmentAtomV6, ...]
    protected_user_boundary_acknowledged: bool

    def __post_init__(self) -> None:
        require_schema(self.schema_version, self.SCHEMA_VERSION, type(self).__name__)
        if not self.protected_user_boundary_acknowledged:
            raise ContractValidationError("compact v7 must acknowledge Ted ownership")
        for field, values in (
            ("responders", (value.character_id for value in self.responders)),
            ("source_claims", (value.claim_key for value in self.source_claims)),
            ("beats", (value.beat_key for value in self.beats)),
            ("future_segments", (value.segment_key for value in self.future_segments)),
            ("essential_continuity", self.essential_continuity),
            ("uncertainties", self.uncertainties),
            ("prohibited_inferences", self.prohibited_inferences),
            ("insufficiencies", self.insufficiencies),
            ("development_atoms", (value.atom_key for value in self.development_atoms)),
        ):
            _unique(values, f"compact v7 {field}")
        for field in (
            "essential_continuity",
            "uncertainties",
            "prohibited_inferences",
            "insufficiencies",
        ):
            _text_tuple(getattr(self, field), f"compact v7 {field}")
        if self.status is ReasonerOutcomeStatus.DECISION_READY:
            required = (
                self.route,
                self.scene_goal,
                self.reason_code,
                self.floor_owner_id,
                self.runway_class,
                self.continue_beyond_prompt_endpoint,
                self.stop_reason,
                self.stop_condition,
                self.interaction_topology,
                self.scene_function,
                self.tone,
                self.interiority_level,
            )
            if any(value is None for value in required):
                raise ContractValidationError("ready compact v7 lacks selectors")
            if not self.responders or not self.source_claims or not self.beats:
                raise ContractValidationError("ready compact v7 lacks scene semantics")
            if self.insufficiencies or self.blocker_code is not None:
                raise ContractValidationError("ready compact v7 carries failure state")
            selected = {value.character_id for value in self.responders}
            if self.floor_owner_id not in selected:
                raise ContractValidationError("compact v7 floor owner is not selected")
            claim_keys = {value.claim_key for value in self.source_claims}
            beat_keys = {value.beat_key for value in self.beats}
            used_evidence = {
                evidence_id
                for responder in self.responders
                for evidence_id in responder.evidence_ids
            }
            for beat in self.beats:
                character_actors = {
                    value for value in beat.actor_ids if value.kind is IdKind.CHARACTER
                }
                if not character_actors or not character_actors.issubset(selected):
                    raise ContractValidationError("compact beat has unselected actor")
                if not set(beat.source_claim_keys).issubset(claim_keys):
                    raise ContractValidationError("compact beat cites unknown source claim")
                if not set(beat.protected_user_source_claim_keys).issubset(claim_keys):
                    raise ContractValidationError("compact Ted allowance cites unknown claim")
                used_evidence.update(beat.evidence_ids)
            if self.route is DecisionRoute.ORDINARY and self.adult_craft_need is not None:
                raise ContractValidationError("ordinary compact v7 carries adult craft")
            if (
                self.route is DecisionRoute.CONSENT_VALID_ADULT
                and self.adult_craft_need is None
            ):
                raise ContractValidationError("adult compact v7 lacks craft semantics")
            if self.adult_craft_need is not None and not {
                value.beat_key for value in self.adult_craft_need.beat_requirements
            }.issubset(beat_keys):
                raise ContractValidationError("adult compact v7 cites unknown beat")
            for atom in self.development_atoms:
                if not set(atom.source_block_keys).issubset(beat_keys):
                    raise ContractValidationError("development atom cites unknown beat")
                if not set(atom.evidence_ids).issubset(used_evidence):
                    raise ContractValidationError("development atom cites unused evidence")
        else:
            if self.status is ReasonerOutcomeStatus.INSUFFICIENT_EVIDENCE:
                if not self.insufficiencies or self.blocker_code is not None:
                    raise ContractValidationError("compact insufficiency state is invalid")
            elif (
                self.blocker_code is not ErrorCode.BLOCKED_NONCONSENSUAL_EVENT
                or self.insufficiencies
            ):
                raise ContractValidationError("compact blocked state is invalid")
            if any(
                (
                    self.route is not None,
                    self.scene_goal is not None,
                    self.reason_code is not None,
                    bool(self.responders),
                    self.floor_owner_id is not None,
                    bool(self.source_claims),
                    bool(self.beats),
                    self.runway_class is not None,
                    self.continue_beyond_prompt_endpoint is not None,
                    self.stop_reason is not None,
                    self.stop_condition is not None,
                    self.interaction_topology is not None,
                    self.scene_function is not None,
                    self.tone is not None,
                    self.interiority_level is not None,
                    bool(self.essential_continuity),
                    bool(self.future_segments),
                    self.adult_craft_need is not None,
                    bool(self.development_atoms),
                )
            ):
                raise ContractValidationError("non-ready compact v7 carries decision content")

    @property
    def draft_sha256(self) -> str:
        return domain_sha256(self.SCHEMA_VERSION, self)


def compile_compact_v7_to_v6(draft: CodexReasonerDraftV7Compact) -> CodexReasonerDraftV6:
    """Expand mechanical v6 fields without introducing a semantic choice."""

    if draft.status is not ReasonerOutcomeStatus.DECISION_READY:
        return CodexReasonerDraftV6(
            schema_version=CodexReasonerDraftV6.SCHEMA_VERSION,
            status=draft.status,
            route=None,
            scene_intent=None,
            responding_npc_ids=(),
            floor_owner_id=None,
            participation=(),
            character_moves=(),
            source_claims=(),
            event_blocks=(),
            causal_runway=None,
            interaction_topology=None,
            scene_function=None,
            tone=None,
            interiority_level=None,
            future_segments=(),
            writer_must_preserve=(),
            uncertainties=draft.uncertainties,
            prohibited_inferences=draft.prohibited_inferences,
            insufficiencies=draft.insufficiencies,
            blocker_code=draft.blocker_code,
            adult_craft_need=None,
            protected_user_boundary_acknowledged=True,
            development_atoms=(),
        )
    responders = {value.character_id: value for value in draft.responders}
    compiled_blocks = []
    for beat in draft.beats:
        actor_responders = tuple(
            responders[value]
            for value in beat.actor_ids
            if value.kind is IdKind.CHARACTER
        )
        material_lines = tuple(
            f"{value.material_id}: {value.state_before} -> {value.state_after}"
            for value in beat.material_transitions
        )
        allowance = DraftProtectedUserAllowanceV5(
            allowed_kinds=beat.protected_user_allowed_kinds,
            source_claim_keys=beat.protected_user_source_claim_keys,
            explanation=(
                "Only the cited exact source-owned Ted content may be realized."
                if beat.protected_user_allowed_kinds
                else "No new Ted action, dialogue, thought, feeling, or choice."
            ),
        )
        compiled_blocks.append(
            DraftSceneEventBlockV5(
                block_key=beat.beat_key,
                actor_ids=beat.actor_ids,
                purpose=beat.goal,
                causal_basis=beat.causal_basis,
                event_advances=beat.advances,
                resulting_state=beat.result,
                evidence_ids=beat.evidence_ids,
                source_claim_keys=beat.source_claim_keys,
                protected_user_allowance=allowance,
                writer_scaffold=DraftWriterScaffoldV5(
                    viewpoint_character_ids=tuple(
                        value.character_id for value in actor_responders
                    ),
                    motivation_and_subtext=tuple(
                        f"{value.intent}; tactic: {value.tactic}"
                        for value in actor_responders
                    ),
                    voice_and_interiority=(),
                    physical_and_material_continuity=(
                        *draft.essential_continuity,
                        *beat.continuity,
                        *material_lines,
                    ),
                    transition_obligations=beat.advances,
                    open_realization_space=beat.realization_space,
                ),
            )
        )
    assert draft.runway_class is not None
    assert draft.continue_beyond_prompt_endpoint is not None
    assert draft.stop_reason is not None
    assert draft.stop_condition is not None
    return CodexReasonerDraftV6(
        schema_version=CodexReasonerDraftV6.SCHEMA_VERSION,
        status=draft.status,
        route=draft.route,
        scene_intent=draft.scene_goal,
        responding_npc_ids=tuple(value.character_id for value in draft.responders),
        floor_owner_id=draft.floor_owner_id,
        participation=tuple(
            DraftParticipationV2(
                character_id=value.character_id,
                intervention_reason=(
                    None
                    if value.character_id == draft.floor_owner_id
                    else value.intervention_reason
                ),
                evidence_ids=value.evidence_ids,
            )
            for value in draft.responders
        ),
        character_moves=tuple(
            DraftCharacterMoveV2(
                character_id=value.character_id,
                perception=value.perception_basis,
                selected_intent=value.intent,
                action_direction=value.tactic,
                evidence_ids=value.evidence_ids,
                knowledge_constraints=value.knowledge_limits,
            )
            for value in draft.responders
        ),
        source_claims=draft.source_claims,
        event_blocks=tuple(compiled_blocks),
        causal_runway=DraftCausalRunwayV5(
            runway_class=draft.runway_class,
            development_obligations=(draft.reason_code or "complete_supported_unit",),
            continue_beyond_prompt_endpoint=draft.continue_beyond_prompt_endpoint,
            stop_reason=draft.stop_reason,
            stop_condition=draft.stop_condition,
        ),
        interaction_topology=draft.interaction_topology,
        scene_function=draft.scene_function,
        tone=draft.tone,
        interiority_level=draft.interiority_level,
        future_segments=draft.future_segments,
        writer_must_preserve=draft.essential_continuity,
        uncertainties=draft.uncertainties,
        prohibited_inferences=draft.prohibited_inferences,
        insufficiencies=(),
        blocker_code=None,
        adult_craft_need=draft.adult_craft_need,
        protected_user_boundary_acknowledged=True,
        development_atoms=draft.development_atoms,
    )


def normalized_reasoner_semantics(
    draft: CodexReasonerDraftV6 | CodexReasonerDraftV7Compact,
) -> dict[str, object]:
    """Compare v6 and v7 without Python-owned structural duplication."""

    value = (
        compile_compact_v7_to_v6(draft)
        if isinstance(draft, CodexReasonerDraftV7Compact)
        else draft
    )
    return {
        "status": value.status.value,
        "route": value.route.value if value.route is not None else None,
        "scene_goal": value.scene_intent,
        "responders": tuple(str(item) for item in value.responding_npc_ids),
        "floor_owner": str(value.floor_owner_id) if value.floor_owner_id else None,
        "participation": tuple(
            (
                str(item.character_id),
                (
                    item.intervention_reason.value
                    if item.intervention_reason is not None
                    else None
                ),
                tuple(str(evidence) for evidence in item.evidence_ids),
            )
            for item in value.participation
        ),
        "moves": tuple(
            (
                str(item.character_id),
                item.perception,
                item.selected_intent,
                item.action_direction,
                tuple(str(evidence) for evidence in item.evidence_ids),
                item.knowledge_constraints,
            )
            for item in value.character_moves
        ),
        "source_claims": tuple(
            (
                item.claim_key,
                str(item.source_unit_id),
                item.kind.value,
                item.authority.value,
                item.quote,
                item.normalized_meaning,
                tuple(str(character) for character in item.affected_character_ids),
                item.rationale,
            )
            for item in value.source_claims
        ),
        "beats": tuple(
            (
                item.block_key,
                tuple(str(actor) for actor in item.actor_ids),
                item.purpose,
                item.causal_basis,
                item.event_advances,
                item.resulting_state,
                tuple(str(evidence) for evidence in item.evidence_ids),
                item.source_claim_keys,
                item.protected_user_allowance.allowed_kinds,
                item.protected_user_allowance.source_claim_keys,
                item.protected_user_allowance.explanation,
                tuple(
                    str(character)
                    for character in item.writer_scaffold.viewpoint_character_ids
                ),
                item.writer_scaffold.motivation_and_subtext,
                item.writer_scaffold.voice_and_interiority,
                item.writer_scaffold.physical_and_material_continuity,
                item.writer_scaffold.transition_obligations,
                item.writer_scaffold.open_realization_space,
            )
            for item in value.event_blocks
        ),
        "stop": (
            (
                value.causal_runway.runway_class.value,
                value.causal_runway.development_obligations,
                value.causal_runway.continue_beyond_prompt_endpoint,
                value.causal_runway.stop_reason.value,
                value.causal_runway.stop_condition,
            )
            if value.causal_runway is not None
            else None
        ),
        "selectors": (
            value.interaction_topology.value
            if value.interaction_topology is not None
            else None,
            value.scene_function.value if value.scene_function is not None else None,
            value.tone.value if value.tone is not None else None,
            value.interiority_level.value
            if value.interiority_level is not None
            else None,
        ),
        "writer_must_preserve": value.writer_must_preserve,
        "uncertainties": value.uncertainties,
        "prohibited_inferences": value.prohibited_inferences,
        "insufficiencies": value.insufficiencies,
        "blocker_code": value.blocker_code.value if value.blocker_code else None,
        "future": value.future_segments,
        "adult": value.adult_craft_need,
        "development": value.development_atoms,
    }


def _closed(properties: dict[str, object]) -> dict[str, object]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
        "required": list(properties),
    }


def _array(items: dict[str, object], maximum: int, minimum: int = 0) -> dict[str, object]:
    return {"type": "array", "minItems": minimum, "maxItems": maximum, "items": items}


def _nullable(schema: dict[str, object]) -> dict[str, object]:
    return {"anyOf": [schema, {"type": "null"}]}


def codex_reasoner_draft_v7_compact_json_schema(
    *,
    allowed_evidence_ids: tuple[TypedId, ...] = (),
    adult_route: bool | None = None,
) -> dict[str, object]:
    """Closed OpenAI-compatible schema, additive and shadow-only."""

    v6 = codex_reasoner_draft_v6_json_schema(
        allowed_evidence_ids=allowed_evidence_ids
    )
    p = v6["properties"]
    evidence_item: dict[str, object] = (
        {"type": "string", "enum": [str(value) for value in allowed_evidence_ids]}
        if allowed_evidence_ids
        else {"type": "string", "pattern": r"^(?:evidence):[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"}
    )
    short = {"type": "string", "minLength": 1, "maxLength": 500}
    text_set = _array(short, 16)
    responder = _closed(
        {
            "character_id": deepcopy(p["responding_npc_ids"]["items"]),
            "intervention_reason": deepcopy(p["participation"]["items"]["properties"]["intervention_reason"]),
            "perception_basis": short,
            "intent": short,
            "tactic": short,
            "evidence_ids": _array(evidence_item, 16),
            "knowledge_limits": text_set,
        }
    )
    material_transition = _closed(
        {
            "material_id": {
                "type": "string",
                "pattern": r"^(?:material):[A-Za-z0-9][A-Za-z0-9._-]{0,127}$",
            },
            "state_before": short,
            "state_after": short,
        }
    )
    beat = _closed(
        {
            "beat_key": deepcopy(p["event_blocks"]["items"]["properties"]["block_key"]),
            "actor_ids": deepcopy(p["event_blocks"]["items"]["properties"]["actor_ids"]),
            "goal": short,
            "causal_basis": short,
            "advances": _array(short, 16, 1),
            "result": short,
            "evidence_ids": _array(evidence_item, 16),
            "source_claim_keys": deepcopy(p["event_blocks"]["items"]["properties"]["source_claim_keys"]),
            "protected_user_allowed_kinds": deepcopy(
                p["event_blocks"]["items"]["properties"]["protected_user_allowance"]["properties"]["allowed_kinds"]
            ),
            "protected_user_source_claim_keys": deepcopy(
                p["event_blocks"]["items"]["properties"]["protected_user_allowance"]["properties"]["source_claim_keys"]
            ),
            "continuity": text_set,
            "material_transitions": _array(material_transition, 12),
            "realization_space": _array(short, 16, 1),
        }
    )
    properties = {
        "schema_version": {"const": CodexReasonerDraftV7Compact.SCHEMA_VERSION},
        "status": deepcopy(p["status"]),
        "route": deepcopy(p["route"]),
        "scene_goal": _nullable(short),
        "reason_code": _nullable(short),
        "responders": _array(responder, 8),
        "floor_owner_id": deepcopy(p["floor_owner_id"]),
        "source_claims": deepcopy(p["source_claims"]),
        "beats": _array(beat, 24),
        "runway_class": _nullable({"type": "string", "enum": [value.value for value in SceneRunwayClass]}),
        "continue_beyond_prompt_endpoint": _nullable({"type": "boolean"}),
        "stop_reason": _nullable({"type": "string", "enum": [value.value for value in NaturalStopReason]}),
        "stop_condition": _nullable(short),
        "interaction_topology": deepcopy(p["interaction_topology"]),
        "scene_function": deepcopy(p["scene_function"]),
        "tone": deepcopy(p["tone"]),
        "interiority_level": deepcopy(p["interiority_level"]),
        "essential_continuity": text_set,
        "future_segments": deepcopy(p["future_segments"]),
        "uncertainties": deepcopy(p["uncertainties"]),
        "prohibited_inferences": deepcopy(p["prohibited_inferences"]),
        "insufficiencies": deepcopy(p["insufficiencies"]),
        "blocker_code": deepcopy(p["blocker_code"]),
        "adult_craft_need": (
            {"type": "null"}
            if adult_route is False
            else deepcopy(p["adult_craft_need"])
        ),
        "development_atoms": deepcopy(p["development_atoms"]),
        "protected_user_boundary_acknowledged": {"const": True},
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        **_closed(properties),
    }


def build_compact_v7_reasoner_prompt(packet: dict[str, object]) -> str:
    """Concise semantic contract; Python and the schema own mechanics."""

    return "\n".join(
        (
            "CERA Scene Reasoner compact v7 shadow invocation.",
            "Return exactly one CodexReasonerDraftV7Compact JSON object matching the supplied schema. Return operational semantics, never story prose.",
            "Python owns durable authority, canonical IDs, alias resolution, validation, publication, regeneration, and branch isolation. Provider conversation history is never evidence. The newest packet is complete and supersedes conflicting history.",
            "Choose only eligible scene-reachable NPC responders. Treat world-known, physically present, currently active, exact-source responders, eligible responders, and selected responders as different sets. Do not add an inactive character without a causally supported entrance; obey a current-only cast request.",
            "For each selected responder state the evidence-grounded perception basis, intent, tactic, knowledge limits, and evidence aliases once. Pick exactly one floor_owner_id. The floor owner uses null intervention_reason; every secondary requires a semantic intervention reason.",
            "Plan the complete supported causal continuation requested by scene_development_contract. Beats are ordered broad causal units: goal, cause, materially distinct advances, resulting state, continuity, typed material transitions, and creative realization space. Scene depth controls causal scope, not schema verbosity or a rigid beat quota.",
            "Classify every materially relevant exact source claim. Character autonomy, consent/capacity, identity, privacy, knowledge ownership, creator authority, and branch boundaries are binding. A user assertion about an NPC private state or involuntary body response is nonbinding when the packet says so.",
            "Never author Ted's new action, dialogue, thought, feeling, decision, consent, refusal, destination, or reciprocal response. protected_user_allowed_kinds may expose only exact cited source-owned content; otherwise leave both Ted allowance arrays empty. Stop before his next genuinely necessary unsupplied meaningful choice.",
            "Use only exact compact seed evidence for hard decisions. Its supported_content is exact fetched section content; authority, truth, knowledge owners, visibility, version, and hash remain binding. Search references are not evidence. If required exact support is absent, use only request-bound tools and fetch exact advertised sections. Never invent or transfer private knowledge.",
            "essential_continuity contains only sequence-wide accepted facts that must survive realization. material_transitions contain only supported before/after state changes. future_segments remain conditional and are never current events. development_atoms are optional one-step proposals and require current beat plus exact evidence support.",
            "Adult craft is null unless Python selected consent_valid_adult. On that route, describe non-graphic semantic craft for current beats only. Ordinary content must not activate adult routing.",
            "NON-READY RESET: insufficient_evidence or blocked must clear every decision field, responder, claim, beat, selector, continuity item, future segment, adult item, and development atom. Insufficient requires insufficiencies. Blocked requires only CERA_BLOCKED_NONCONSENSUAL_EVENT.",
            "Every semantic-set array must contain distinct values. Cite only evidence aliases actually used. Preserve all supported causal beats; compact means no repeated bookkeeping, not less logic.",
            "The complete authoritative packet follows as canonical JSON:",
            canonical_json(packet),
        )
    )
