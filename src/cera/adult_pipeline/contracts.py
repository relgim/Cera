"""Provider-neutral contracts for the protected adult Scene/Filter route.

The Adult Scene role is the only narrative-logic owner for one candidate.  A
separate Adult Filter may validate and derive records, but it cannot replace
the decision path, prose, or next-route decision.  Python-owned world,
candidate, branch, and hash custody is kept out of both provider wire shapes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar

from cera.errors import ContractValidationError, StateConflictError
from cera.serialization import canonical_json, canonical_sha256, re_is_sha256, text_sha256

_IDENTITY = re.compile(r"[a-z][a-z0-9_.:-]{0,191}\Z")
_LOCAL_KEY = re.compile(r"[a-z][a-z0-9_]{0,95}\Z")


def _text(value: str, field: str, *, maximum: int = 100_000) -> None:
    if type(value) is not str or not value.strip() or len(value) > maximum:
        raise ContractValidationError(f"{field} must be non-empty and bounded")


def _identity(value: str, field: str) -> None:
    if type(value) is not str or _IDENTITY.fullmatch(value) is None:
        raise ContractValidationError(f"{field} must be a stable identity")


def _key(value: str, field: str) -> None:
    if type(value) is not str or _LOCAL_KEY.fullmatch(value) is None:
        raise ContractValidationError(f"{field} must be a local key")


def _sha(value: str, field: str) -> None:
    if type(value) is not str or not re_is_sha256(value):
        raise ContractValidationError(f"{field} must be SHA-256")


def _unique(values: tuple[str, ...], field: str) -> None:
    if type(values) is not tuple or len(values) != len(set(values)):
        raise ContractValidationError(f"{field} contains duplicates")


def _bounded_unique_text(values: tuple[str, ...], field: str, *, maximum: int = 2_000) -> None:
    _unique(values, field)
    for value in values:
        _text(value, field, maximum=maximum)


class AdultNextRoute(StrEnum):
    ADULT = "adult"
    ORDINARY = "ordinary"


class AdultEntryReason(StrEnum):
    CODEX_ADULT_HANDOFF = "codex_adult_handoff"
    ACCEPTED_ADULT_CONTINUATION = "accepted_adult_continuation"
    DIRECT_ADULT_ROUTE = "direct_adult_route"


class AdultCraftMode(StrEnum):
    OFF = "off"
    ON = "on"
    EX = "ex"


_ON_CRAFT_AXES = frozenset({"direct_vocabulary", "clarity"})
_EX_CRAFT_AXES = frozenset(
    {
        *_ON_CRAFT_AXES,
        "buildup",
        "physiology",
        "continuity",
        "sound",
        "climax",
        "aftermath",
    }
)


class AdultFilterVerdict(StrEnum):
    PASS = "pass"
    REJECT = "reject"


class AdultFilterConflictClass(StrEnum):
    LOGIC_NOT_REALIZED = "logic_not_realized"
    LOGIC_CONTRADICTION = "logic_contradiction"
    CURRENT_DATA_CONFLICT = "current_data_conflict"
    KNOWLEDGE_OR_PRIVACY_CONFLICT = "knowledge_or_privacy_conflict"
    UNSUPPORTED_DURABLE_EFFECT = "unsupported_durable_effect"
    ROUTE_TRANSITION_CONFLICT = "route_transition_conflict"
    SEVERE_INCOMPLETENESS = "severe_incompleteness"
    AUTHORITY_AMBIGUITY = "authority_ambiguity"


class AdultProviderRole(StrEnum):
    SCENE = "adult_scene"
    FILTER = "adult_filter"


class AdultSessionScope(StrEnum):
    ACCEPTED_BRANCH = "accepted_branch"
    CANDIDATE = "candidate"


@dataclass(frozen=True, slots=True)
class AdultContextFactV1:
    """Role-authorized current data visible to both adult roles."""

    evidence_ref: str
    subject_id: str
    authoritative_fact: str
    visibility: str

    def __post_init__(self) -> None:
        _identity(self.evidence_ref, "adult_context_fact.evidence_ref")
        _identity(self.subject_id, "adult_context_fact.subject_id")
        _text(
            self.authoritative_fact,
            "adult_context_fact.authoritative_fact",
            maximum=4_000,
        )
        if self.visibility not in {"public", "adult_role_private"}:
            raise ContractValidationError("adult context visibility is invalid")


@dataclass(frozen=True, slots=True)
class AdultCraftExcerptV1:
    craft_ref: str
    concept_keys: tuple[str, ...]
    excerpt: str

    def __post_init__(self) -> None:
        _identity(self.craft_ref, "adult_craft.craft_ref")
        _unique(self.concept_keys, "adult_craft.concept_keys")
        if not self.concept_keys:
            raise ContractValidationError("adult craft excerpt requires a concept key")
        for key in self.concept_keys:
            _key(key, "adult_craft.concept_key")
        _text(self.excerpt, "adult_craft.excerpt", maximum=8_000)


@dataclass(frozen=True, slots=True)
class AdultCraftQueryV1:
    SCHEMA_VERSION: ClassVar[str] = "cera.adult_pipeline.craft_query.v1"

    schema_version: str
    mode: AdultCraftMode
    concept_keys: tuple[str, ...]
    keyword_keys: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("adult craft query schema changed")
        for field, values in (
            ("concept_keys", self.concept_keys),
            ("keyword_keys", self.keyword_keys),
        ):
            _unique(values, f"adult_craft_query.{field}")
            if len(values) > 16:
                raise ContractValidationError(f"adult_craft_query.{field} exceeds its budget")
            for value in values:
                _key(value, f"adult_craft_query.{field}")
        if self.mode is AdultCraftMode.OFF and (self.concept_keys or self.keyword_keys):
            raise ContractValidationError("adult craft OFF query must not retrieve concepts")


@dataclass(frozen=True, slots=True)
class AdultCraftSelectionV1:
    """Frozen bounded retrieval result; never the complete craft catalog."""

    SCHEMA_VERSION: ClassVar[str] = "cera.adult_pipeline.craft_selection.v1"

    schema_version: str
    mode: AdultCraftMode
    query_sha256: str
    covered_axes: tuple[str, ...]
    excerpts: tuple[AdultCraftExcerptV1, ...]

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("adult craft selection schema changed")
        _sha(self.query_sha256, "adult_craft_selection.query_sha256")
        _unique(self.covered_axes, "adult_craft_selection.covered_axes")
        for value in self.covered_axes:
            _key(value, "adult_craft_selection.covered_axis")
        if len(self.excerpts) > 12:
            raise ContractValidationError("adult craft selection exceeds excerpt budget")
        _unique(
            tuple(value.craft_ref for value in self.excerpts),
            "adult_craft_selection.excerpts",
        )
        axes = set(self.covered_axes)
        if self.mode is AdultCraftMode.OFF:
            if self.covered_axes or self.excerpts:
                raise ContractValidationError("adult craft OFF selection must be empty")
        elif self.mode is AdultCraftMode.ON:
            if not self.excerpts or not _ON_CRAFT_AXES.issubset(axes):
                raise ContractValidationError(
                    "adult craft ON requires direct-vocabulary and clarity coverage"
                )
        elif not self.excerpts or not _EX_CRAFT_AXES.issubset(axes):
            raise ContractValidationError("adult craft EX lacks extended craft-axis coverage")


@dataclass(frozen=True, slots=True)
class AdultSceneRequestV1:
    """Provider-visible input with no Python transaction or path custody."""

    SCHEMA_VERSION: ClassVar[str] = "cera.adult_pipeline.scene_request.v1"

    schema_version: str
    entry_reason: AdultEntryReason
    adult_handoff: str | None
    exact_current_source: str
    accepted_safe_continuity: str
    accepted_protected_continuity: str | None
    autonomy_mode: str
    depth_mode: str
    current_context: tuple[AdultContextFactV1, ...]
    retrieved_craft: AdultCraftSelectionV1
    hard_boundaries: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("adult Scene request schema changed")
        if self.entry_reason is AdultEntryReason.CODEX_ADULT_HANDOFF:
            _text(self.adult_handoff or "", "adult_scene.adult_handoff", maximum=20_000)
        elif self.adult_handoff is not None:
            raise ContractValidationError("only a Codex handoff entry may carry a handoff")
        _text(self.exact_current_source, "adult_scene.exact_current_source")
        _text(
            self.accepted_safe_continuity,
            "adult_scene.accepted_safe_continuity",
            maximum=40_000,
        )
        if self.entry_reason is AdultEntryReason.ACCEPTED_ADULT_CONTINUATION:
            _text(
                self.accepted_protected_continuity or "",
                "adult_scene.accepted_protected_continuity",
            )
        elif self.accepted_protected_continuity is not None:
            _text(
                self.accepted_protected_continuity,
                "adult_scene.accepted_protected_continuity",
            )
        if self.autonomy_mode not in {"off", "mind", "body", "both"}:
            raise ContractValidationError("adult Scene autonomy mode is invalid")
        _text(self.depth_mode, "adult_scene.depth_mode", maximum=100)
        context_refs = tuple(value.evidence_ref for value in self.current_context)
        _unique(context_refs, "adult_scene.current_context")
        _bounded_unique_text(self.hard_boundaries, "adult_scene.hard_boundaries")


@dataclass(frozen=True, slots=True)
class AdultDecisionStepV1:
    decision_key: str
    character_id: str
    concise_decision: str
    evidence_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        _key(self.decision_key, "adult_decision.decision_key")
        _identity(self.character_id, "adult_decision.character_id")
        _text(self.concise_decision, "adult_decision.concise_decision", maximum=4_000)
        _unique(self.evidence_refs, "adult_decision.evidence_refs")
        for value in self.evidence_refs:
            _identity(value, "adult_decision.evidence_ref")


@dataclass(frozen=True, slots=True)
class AdultSceneOutputV1:
    """Complete logic and prose authored by the sole adult logic owner."""

    SCHEMA_VERSION: ClassVar[str] = "cera.adult_pipeline.scene_output.v1"

    schema_version: str
    logic_owner: str
    decision_path: tuple[AdultDecisionStepV1, ...]
    exact_story_prose: str
    resulting_state: str
    unresolved_threads: tuple[str, ...]
    next_route: AdultNextRoute
    next_route_reason: str

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("adult Scene output schema changed")
        if self.logic_owner != "deepseek_adult_scene":
            raise ContractValidationError("adult candidate has more than one logic owner")
        if not self.decision_path:
            raise ContractValidationError("adult Scene output requires a decision path")
        _unique(
            tuple(value.decision_key for value in self.decision_path),
            "adult_scene.decision_path",
        )
        _text(self.exact_story_prose, "adult_scene.exact_story_prose")
        _text(self.resulting_state, "adult_scene.resulting_state", maximum=10_000)
        _bounded_unique_text(self.unresolved_threads, "adult_scene.unresolved_threads")
        _text(self.next_route_reason, "adult_scene.next_route_reason", maximum=2_000)


@dataclass(frozen=True, slots=True)
class AdultProviderReceiptV1:
    """Privacy-safe invocation receipt produced at a provider boundary."""

    SCHEMA_VERSION: ClassVar[str] = "cera.adult_pipeline.provider_receipt.v1"

    schema_version: str
    role: AdultProviderRole
    session_scope: AdultSessionScope
    provider: str
    model: str
    session_id_sha256: str
    request_sha256: str
    output_sha256: str
    provider_operations: int
    finish_status: str
    session_terminalized: bool

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("adult provider receipt schema changed")
        _text(self.provider, "adult_receipt.provider", maximum=200)
        _text(self.model, "adult_receipt.model", maximum=200)
        _text(self.finish_status, "adult_receipt.finish_status", maximum=100)
        for field in ("session_id_sha256", "request_sha256", "output_sha256"):
            _sha(getattr(self, field), f"adult_receipt.{field}")
        if type(self.provider_operations) is not int or self.provider_operations != 1:
            raise ContractValidationError("adult role requires exactly one provider operation")
        if type(self.session_terminalized) is not bool:
            raise ContractValidationError("adult receipt terminalization flag is invalid")
        if self.role is AdultProviderRole.SCENE:
            if self.session_scope is not AdultSessionScope.ACCEPTED_BRANCH:
                raise ContractValidationError("adult Scene must use accepted-branch session scope")
        else:
            if self.session_scope is not AdultSessionScope.CANDIDATE:
                raise ContractValidationError("adult Filter must use candidate session scope")
            if not self.session_terminalized:
                raise ContractValidationError("adult Filter session must be terminalized")


@dataclass(frozen=True, slots=True)
class AdultSceneCustodyV1:
    """Python-only candidate binding; never requested from Adult Scene."""

    SCHEMA_VERSION: ClassVar[str] = "cera.adult_pipeline.scene_custody.v1"

    schema_version: str
    request_id: str
    candidate_id: str
    world_id: str
    branch_id: str
    accepted_head_sha256: str | None
    exact_source_sha256: str
    scene_request_sha256: str

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("adult Scene custody schema changed")
        for field in ("request_id", "candidate_id", "world_id", "branch_id"):
            _identity(getattr(self, field), f"adult_scene_custody.{field}")
        if self.accepted_head_sha256 is not None:
            _sha(self.accepted_head_sha256, "adult_scene_custody.accepted_head_sha256")
        _sha(self.exact_source_sha256, "adult_scene_custody.exact_source_sha256")
        _sha(self.scene_request_sha256, "adult_scene_custody.scene_request_sha256")


@dataclass(frozen=True, slots=True)
class AdultSceneInvocationV1:
    output: AdultSceneOutputV1
    receipt: AdultProviderReceiptV1

    def __post_init__(self) -> None:
        if self.receipt.role is not AdultProviderRole.SCENE:
            raise ContractValidationError("adult Scene returned a non-Scene receipt")
        if canonical_sha256(self.output) != self.receipt.output_sha256:
            raise ContractValidationError("adult Scene receipt lost exact output custody")


@dataclass(frozen=True, slots=True)
class BoundAdultSceneCandidateV1:
    request: AdultSceneRequestV1
    custody: AdultSceneCustodyV1
    invocation: AdultSceneInvocationV1

    def __post_init__(self) -> None:
        if text_sha256(self.request.exact_current_source) != self.custody.exact_source_sha256:
            raise ContractValidationError("adult Scene custody lost exact source")
        request_sha256 = canonical_sha256(self.request)
        if request_sha256 != self.custody.scene_request_sha256:
            raise ContractValidationError("adult Scene custody lost exact request")
        if request_sha256 != self.invocation.receipt.request_sha256:
            raise ContractValidationError("adult Scene receipt cites another request")
        allowed_refs = {value.evidence_ref for value in self.request.current_context}
        for step in self.invocation.output.decision_path:
            if not set(step.evidence_refs).issubset(allowed_refs):
                raise ContractValidationError("adult decision cites unavailable current data")

    @property
    def candidate_sha256(self) -> str:
        return canonical_sha256(self)


@dataclass(frozen=True, slots=True)
class AdultFilterRequestV1:
    """Candidate-isolated Filter input, still free of Python custody fields."""

    SCHEMA_VERSION: ClassVar[str] = "cera.adult_pipeline.filter_request.v1"

    schema_version: str
    scene_request: AdultSceneRequestV1
    scene_output: AdultSceneOutputV1

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("adult Filter request schema changed")


@dataclass(frozen=True, slots=True)
class AdultCurrentDataUseV1:
    evidence_ref: str
    decision_key: str
    concise_use: str

    def __post_init__(self) -> None:
        _identity(self.evidence_ref, "adult_data_use.evidence_ref")
        _key(self.decision_key, "adult_data_use.decision_key")
        _text(self.concise_use, "adult_data_use.concise_use", maximum=2_000)


@dataclass(frozen=True, slots=True)
class AdultProtectedEventV1:
    event_key: str
    protected_summary: str
    character_ids: tuple[str, ...]
    durable_effects: tuple[str, ...]
    knowledge_owner_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        _key(self.event_key, "adult_protected_event.event_key")
        _text(self.protected_summary, "adult_protected_event.protected_summary", maximum=8_000)
        _unique(self.character_ids, "adult_protected_event.character_ids")
        if not self.character_ids:
            raise ContractValidationError("adult protected event requires a character")
        for value in (*self.character_ids, *self.knowledge_owner_ids):
            _identity(value, "adult_protected_event.character_id")
        _unique(self.knowledge_owner_ids, "adult_protected_event.knowledge_owner_ids")
        if not set(self.knowledge_owner_ids).issubset(self.character_ids):
            raise ContractValidationError("adult event knowledge owner is not a participant")
        _bounded_unique_text(self.durable_effects, "adult_protected_event.durable_effects")


@dataclass(frozen=True, slots=True)
class AdultProtectedFullRecordV1:
    """Protected pre-accept record; never exposed through the Codex projection."""

    SCHEMA_VERSION: ClassVar[str] = "cera.adult_pipeline.protected_full_record.v1"

    schema_version: str
    scene_output_sha256: str
    exact_story_prose: str
    exact_story_prose_sha256: str
    decision_path: tuple[AdultDecisionStepV1, ...]
    events: tuple[AdultProtectedEventV1, ...]
    current_data_uses: tuple[AdultCurrentDataUseV1, ...]
    resulting_protected_state: str
    unresolved_threads: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("adult protected full-record schema changed")
        _sha(self.scene_output_sha256, "adult_full.scene_output_sha256")
        _text(self.exact_story_prose, "adult_full.exact_story_prose")
        if text_sha256(self.exact_story_prose) != self.exact_story_prose_sha256:
            raise ContractValidationError("adult full record prose hash changed")
        _unique(
            tuple(value.decision_key for value in self.decision_path),
            "adult_full.decision_path",
        )
        if not self.events:
            raise ContractValidationError("adult full record requires events")
        _unique(tuple(value.event_key for value in self.events), "adult_full.events")
        _text(
            self.resulting_protected_state,
            "adult_full.resulting_protected_state",
            maximum=20_000,
        )
        _bounded_unique_text(self.unresolved_threads, "adult_full.unresolved_threads")


@dataclass(frozen=True, slots=True)
class AdultProjectionEventV1:
    event_key: str
    non_explicit_summary: str
    lasting_story_meaning: str

    def __post_init__(self) -> None:
        _key(self.event_key, "adult_projection.event_key")
        _text(
            self.non_explicit_summary,
            "adult_projection.non_explicit_summary",
            maximum=2_000,
        )
        _text(
            self.lasting_story_meaning,
            "adult_projection.lasting_story_meaning",
            maximum=2_000,
        )


@dataclass(frozen=True, slots=True)
class AdultProjectionEffectV1:
    effect_key: str
    effect_kind: str
    source_event_key: str
    subject_ids: tuple[str, ...]
    non_explicit_effect: str
    target_key: str
    visibility: str
    knowledge_owner_id: str | None

    def __post_init__(self) -> None:
        _key(self.effect_key, "adult_projection_effect.effect_key")
        if self.effect_kind not in {
            "material",
            "knowledge",
            "relationship",
            "character_development",
        }:
            raise ContractValidationError("adult projection effect kind is invalid")
        _key(self.source_event_key, "adult_projection_effect.source_event_key")
        _unique(self.subject_ids, "adult_projection_effect.subject_ids")
        if not self.subject_ids:
            raise ContractValidationError("adult projection effect requires a subject")
        for value in self.subject_ids:
            _identity(value, "adult_projection_effect.subject_id")
        _text(
            self.non_explicit_effect,
            "adult_projection_effect.non_explicit_effect",
            maximum=2_000,
        )
        _identity(self.target_key, "adult_projection_effect.target_key")
        if self.visibility not in {"public", "character_private"}:
            raise ContractValidationError("adult projection effect visibility is invalid")
        if self.visibility == "character_private":
            if self.knowledge_owner_id not in self.subject_ids:
                raise ContractValidationError("private projection effect lacks its owner")
        elif self.knowledge_owner_id is not None:
            raise ContractValidationError("public projection effect cannot name a private owner")


@dataclass(frozen=True, slots=True)
class AdultProjectionPresenceChangeV1:
    character_id: str
    direction: str
    effective_after_event_key: str

    def __post_init__(self) -> None:
        _identity(self.character_id, "adult_projection_presence.character_id")
        if self.direction not in {"enter", "leave"}:
            raise ContractValidationError("adult projection presence direction is invalid")
        _key(
            self.effective_after_event_key,
            "adult_projection_presence.effective_after_event_key",
        )


@dataclass(frozen=True, slots=True)
class AdultCodexProjectionV2:
    """Non-explicit view that is the only adult record visible to ordinary Codex."""

    SCHEMA_VERSION: ClassVar[str] = "cera.adult_pipeline.codex_projection.v2"

    schema_version: str
    protected_full_record_sha256: str
    events: tuple[AdultProjectionEventV1, ...]
    presence_changes: tuple[AdultProjectionPresenceChangeV1, ...]
    durable_effects: tuple[AdultProjectionEffectV1, ...]
    resulting_public_state: str
    unresolved_threads: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("adult Codex projection schema changed")
        _sha(self.protected_full_record_sha256, "adult_projection.full_record_sha256")
        if not self.events:
            raise ContractValidationError("adult Codex projection requires events")
        _unique(tuple(value.event_key for value in self.events), "adult_projection.events")
        event_keys = {value.event_key for value in self.events}
        for change in self.presence_changes:
            if change.effective_after_event_key not in event_keys:
                raise ContractValidationError(
                    "adult projection presence change cites an unknown event"
                )
        _unique(
            tuple(value.effect_key for value in self.durable_effects),
            "adult_projection.durable_effects",
        )
        for effect in self.durable_effects:
            if effect.source_event_key not in event_keys:
                raise ContractValidationError(
                    "adult projection durable effect cites an unknown event"
                )
        _text(
            self.resulting_public_state,
            "adult_projection.resulting_public_state",
            maximum=20_000,
        )
        _bounded_unique_text(self.unresolved_threads, "adult_projection.unresolved_threads")


@dataclass(frozen=True, slots=True)
class AdultRouteTransitionV1:
    SCHEMA_VERSION: ClassVar[str] = "cera.adult_pipeline.route_transition.v1"

    schema_version: str
    current_route: AdultNextRoute
    next_route: AdultNextRoute
    return_to_codex: bool
    concise_reason: str

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("adult route-transition schema changed")
        if self.current_route is not AdultNextRoute.ADULT:
            raise ContractValidationError("adult pipeline transition must start on adult route")
        if type(self.return_to_codex) is not bool:
            raise ContractValidationError("adult return_to_codex must be boolean")
        if self.return_to_codex != (self.next_route is AdultNextRoute.ORDINARY):
            raise ContractValidationError("return_to_codex disagrees with next-route state")
        _text(self.concise_reason, "adult_route_transition.concise_reason", maximum=2_000)


@dataclass(frozen=True, slots=True)
class AdultFilterPassV1:
    protected_full_record: AdultProtectedFullRecordV1
    codex_projection: AdultCodexProjectionV2
    route_transition: AdultRouteTransitionV1


@dataclass(frozen=True, slots=True)
class AdultFilterConflictV1:
    conflict_class: AdultFilterConflictClass
    concise_explanation: str
    decision_key: str | None = None
    exact_quote: str | None = None

    def __post_init__(self) -> None:
        _text(self.concise_explanation, "adult_filter_conflict.explanation", maximum=2_000)
        if self.decision_key is not None:
            _key(self.decision_key, "adult_filter_conflict.decision_key")
        if self.exact_quote is not None:
            _text(self.exact_quote, "adult_filter_conflict.exact_quote", maximum=1_000)
        if self.decision_key is None and self.exact_quote is None:
            raise ContractValidationError("adult Filter conflict requires an exact anchor")


@dataclass(frozen=True, slots=True)
class AdultFilterDecisionV1:
    """Closed Filter output: exactly one pass bundle or one rejection."""

    SCHEMA_VERSION: ClassVar[str] = "cera.adult_pipeline.filter_decision.v1"

    schema_version: str
    verdict: AdultFilterVerdict
    passed: AdultFilterPassV1 | None
    conflict: AdultFilterConflictV1 | None

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("adult Filter decision schema changed")
        if self.verdict is AdultFilterVerdict.PASS:
            if self.passed is None or self.conflict is not None:
                raise ContractValidationError("passing adult Filter requires only staged records")
        elif self.conflict is None or self.passed is not None:
            raise ContractValidationError("rejecting adult Filter requires only a conflict")


@dataclass(frozen=True, slots=True)
class AdultFilterCustodyV1:
    """Python-only Filter binding; never requested from the Filter model."""

    SCHEMA_VERSION: ClassVar[str] = "cera.adult_pipeline.filter_custody.v1"

    schema_version: str
    request_id: str
    candidate_id: str
    world_id: str
    branch_id: str
    accepted_head_sha256: str | None
    scene_candidate_sha256: str
    filter_request_sha256: str

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("adult Filter custody schema changed")
        for field in ("request_id", "candidate_id", "world_id", "branch_id"):
            _identity(getattr(self, field), f"adult_filter_custody.{field}")
        if self.accepted_head_sha256 is not None:
            _sha(self.accepted_head_sha256, "adult_filter_custody.accepted_head_sha256")
        _sha(self.scene_candidate_sha256, "adult_filter_custody.scene_candidate_sha256")
        _sha(self.filter_request_sha256, "adult_filter_custody.filter_request_sha256")


@dataclass(frozen=True, slots=True)
class AdultFilterInvocationV1:
    decision: AdultFilterDecisionV1
    receipt: AdultProviderReceiptV1

    def __post_init__(self) -> None:
        if self.receipt.role is not AdultProviderRole.FILTER:
            raise ContractValidationError("adult Filter returned a non-Filter receipt")
        if canonical_sha256(self.decision) != self.receipt.output_sha256:
            raise ContractValidationError("adult Filter receipt lost exact output custody")


@dataclass(frozen=True, slots=True)
class BoundAdultFilterResultV1:
    request: AdultFilterRequestV1
    custody: AdultFilterCustodyV1
    invocation: AdultFilterInvocationV1

    def __post_init__(self) -> None:
        if canonical_sha256(self.request) != self.custody.filter_request_sha256:
            raise ContractValidationError("adult Filter custody lost exact request")
        if self.invocation.receipt.request_sha256 != self.custody.filter_request_sha256:
            raise ContractValidationError("adult Filter receipt cites another request")
        decision = self.invocation.decision
        if decision.verdict is AdultFilterVerdict.REJECT:
            conflict = decision.conflict
            if conflict is None:
                raise ContractValidationError("adult Filter rejection lost its conflict")
            if (
                conflict.exact_quote is not None
                and conflict.exact_quote not in self.request.scene_output.exact_story_prose
            ):
                raise ContractValidationError("adult Filter conflict quote is not exact")
            if conflict.decision_key is not None and conflict.decision_key not in {
                value.decision_key for value in self.request.scene_output.decision_path
            }:
                raise ContractValidationError("adult Filter conflict cites an unknown decision")
            return
        passed = decision.passed
        if passed is None:
            raise ContractValidationError("adult Filter pass lost its staged bundle")
        scene = self.request.scene_output
        full = passed.protected_full_record
        projection = passed.codex_projection
        if full.scene_output_sha256 != canonical_sha256(scene):
            raise ContractValidationError("adult full record cites another Scene output")
        if full.exact_story_prose != scene.exact_story_prose:
            raise ContractValidationError("adult Filter rewrote the exact Scene prose")
        if full.decision_path != scene.decision_path:
            raise ContractValidationError("adult Filter rewrote the adult decision path")
        decision_keys = tuple(value.decision_key for value in scene.decision_path)
        if tuple(value.event_key for value in full.events) != decision_keys:
            raise ContractValidationError("adult full record changed decision/event ordering")
        context_refs = {value.evidence_ref for value in self.request.scene_request.current_context}
        for use in full.current_data_uses:
            if use.evidence_ref not in context_refs or use.decision_key not in decision_keys:
                raise ContractValidationError("adult full record cites unavailable current data")
        use_pairs = tuple(
            (value.decision_key, value.evidence_ref) for value in full.current_data_uses
        )
        if len(use_pairs) != len(set(use_pairs)):
            raise ContractValidationError("adult full record duplicates a current-data use")
        expected_use_pairs = {
            (step.decision_key, evidence_ref)
            for step in scene.decision_path
            for evidence_ref in step.evidence_refs
        }
        if set(use_pairs) != expected_use_pairs:
            raise ContractValidationError(
                "adult full record did not validate every decision current-data reference"
            )
        if projection.protected_full_record_sha256 != canonical_sha256(full):
            raise ContractValidationError("adult projection cites another protected record")
        if tuple(value.event_key for value in projection.events) != decision_keys:
            raise ContractValidationError("adult projection changed decision/event ordering")
        if passed.route_transition.next_route is not scene.next_route:
            raise ContractValidationError("adult Filter changed the next-route decision")
        if passed.route_transition.concise_reason != scene.next_route_reason:
            raise ContractValidationError("adult Filter changed the route-transition reason")
        projection_text = canonical_json(projection)
        if scene.exact_story_prose in projection_text:
            raise ContractValidationError("adult projection contains exact protected prose")
        private_facts = (
            value.authoritative_fact
            for value in self.request.scene_request.current_context
            if value.visibility == "adult_role_private"
        )
        if any(value in projection_text for value in private_facts):
            raise ContractValidationError("adult projection exposes adult-role private context")
        protected_continuity = self.request.scene_request.accepted_protected_continuity
        if protected_continuity is not None and protected_continuity in projection_text:
            raise ContractValidationError("adult projection exposes protected adult continuity")

    @property
    def binding_sha256(self) -> str:
        return canonical_sha256(self)


@dataclass(frozen=True, slots=True)
class AdultPromotionBundleV1:
    """Complete pre-accept transaction input; there is no adult Recorder stage."""

    SCHEMA_VERSION: ClassVar[str] = "cera.adult_pipeline.promotion_bundle.v1"

    schema_version: str
    request_id: str
    candidate_id: str
    world_id: str
    branch_id: str
    accepted_head_before_sha256: str | None
    scene_candidate_sha256: str
    filter_binding_sha256: str
    exact_story_prose: str
    exact_story_prose_sha256: str
    protected_full_record: AdultProtectedFullRecordV1
    codex_projection: AdultCodexProjectionV2
    route_transition: AdultRouteTransitionV1

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("adult promotion bundle schema changed")
        for field in ("request_id", "candidate_id", "world_id", "branch_id"):
            _identity(getattr(self, field), f"adult_promotion.{field}")
        if self.accepted_head_before_sha256 is not None:
            _sha(self.accepted_head_before_sha256, "adult_promotion.accepted_head")
        for field in (
            "scene_candidate_sha256",
            "filter_binding_sha256",
            "exact_story_prose_sha256",
        ):
            _sha(getattr(self, field), f"adult_promotion.{field}")
        if text_sha256(self.exact_story_prose) != self.exact_story_prose_sha256:
            raise ContractValidationError("adult promotion prose hash changed")
        if self.protected_full_record.exact_story_prose != self.exact_story_prose:
            raise ContractValidationError("adult promotion prose differs from protected record")
        if self.codex_projection.protected_full_record_sha256 != canonical_sha256(
            self.protected_full_record
        ):
            raise ContractValidationError("adult promotion projection binding changed")

    @property
    def logic_owner(self) -> str:
        return "deepseek_adult_scene"

    @property
    def next_route(self) -> AdultNextRoute:
        return self.route_transition.next_route

    @property
    def return_to_codex(self) -> bool:
        return self.route_transition.return_to_codex


@dataclass(frozen=True, slots=True)
class AdultRouteStateSnapshotV1:
    """Restart-safe accepted route state returned by the branch store."""

    SCHEMA_VERSION: ClassVar[str] = "cera.adult_pipeline.route_state_snapshot.v1"

    schema_version: str
    world_id: str
    branch_id: str
    accepted_head_sha256: str | None
    current_logic_route: AdultNextRoute
    source_promotion_sha256: str | None

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("adult route-state snapshot schema changed")
        _identity(self.world_id, "adult_route_state.world_id")
        _identity(self.branch_id, "adult_route_state.branch_id")
        if (self.accepted_head_sha256 is None) != (self.source_promotion_sha256 is None):
            raise ContractValidationError(
                "adult route state head and source promotion must both be present or absent"
            )
        if self.accepted_head_sha256 is not None:
            _sha(self.accepted_head_sha256, "adult_route_state.accepted_head_sha256")
        if self.source_promotion_sha256 is not None:
            _sha(self.source_promotion_sha256, "adult_route_state.source_promotion_sha256")


@dataclass(frozen=True, slots=True)
class AdultAcceptedPromotionReceiptV1:
    """Store receipt proving artifacts and route state promoted together."""

    SCHEMA_VERSION: ClassVar[str] = "cera.adult_pipeline.accepted_promotion_receipt.v1"

    schema_version: str
    accepted_turn_id: str
    world_id: str
    branch_id: str
    accepted_head_before_sha256: str | None
    accepted_head_after_sha256: str
    promotion_bundle_sha256: str
    current_logic_route: AdultNextRoute
    return_to_codex: bool

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("adult accepted-promotion receipt schema changed")
        for field in ("accepted_turn_id", "world_id", "branch_id"):
            _identity(getattr(self, field), f"adult_acceptance.{field}")
        if self.accepted_head_before_sha256 is not None:
            _sha(
                self.accepted_head_before_sha256,
                "adult_acceptance.accepted_head_before_sha256",
            )
        _sha(self.accepted_head_after_sha256, "adult_acceptance.accepted_head_after_sha256")
        _sha(self.promotion_bundle_sha256, "adult_acceptance.promotion_bundle_sha256")
        if type(self.return_to_codex) is not bool:
            raise ContractValidationError("adult acceptance return_to_codex is invalid")
        if self.return_to_codex != (self.current_logic_route is AdultNextRoute.ORDINARY):
            raise ContractValidationError("adult acceptance route and return_to_codex disagree")


@dataclass(frozen=True, slots=True)
class BoundAdultPromotionV1:
    bundle: AdultPromotionBundleV1
    receipt: AdultAcceptedPromotionReceiptV1

    def __post_init__(self) -> None:
        if self.receipt.promotion_bundle_sha256 != canonical_sha256(self.bundle):
            raise ContractValidationError("adult acceptance receipt cites another bundle")
        for field in ("world_id", "branch_id"):
            if getattr(self.receipt, field) != getattr(self.bundle, field):
                raise ContractValidationError(f"adult acceptance {field} changed")
        if self.receipt.accepted_head_before_sha256 != self.bundle.accepted_head_before_sha256:
            raise ContractValidationError("adult acceptance parent head changed")
        if self.receipt.current_logic_route is not self.bundle.next_route:
            raise ContractValidationError("adult acceptance persisted the wrong next route")
        if self.receipt.return_to_codex != self.bundle.return_to_codex:
            raise ContractValidationError("adult acceptance return_to_codex changed")


@dataclass(frozen=True, slots=True)
class AdultPipelineResultV1:
    scene: BoundAdultSceneCandidateV1
    filtered: BoundAdultFilterResultV1

    def __post_init__(self) -> None:
        scene_custody = self.scene.custody
        filter_custody = self.filtered.custody
        for field in ("request_id", "candidate_id", "world_id", "branch_id"):
            if getattr(scene_custody, field) != getattr(filter_custody, field):
                raise ContractValidationError(f"adult pipeline {field} changed between roles")
        if scene_custody.accepted_head_sha256 != filter_custody.accepted_head_sha256:
            raise ContractValidationError("adult pipeline accepted head changed between roles")
        if self.scene.candidate_sha256 != filter_custody.scene_candidate_sha256:
            raise ContractValidationError("adult Filter was bound to another candidate")
        if self.scene.invocation.receipt.session_id_sha256 == (
            self.filtered.invocation.receipt.session_id_sha256
        ):
            raise ContractValidationError("adult Scene and Filter sessions are not isolated")

    @property
    def eligible_for_atomic_acceptance(self) -> bool:
        return self.filtered.invocation.decision.verdict is AdultFilterVerdict.PASS

    @property
    def requires_post_accept_recorder(self) -> bool:
        return False

    @property
    def logic_owner(self) -> str:
        return self.scene.invocation.output.logic_owner

    @property
    def next_route(self) -> AdultNextRoute | None:
        passed = self.filtered.invocation.decision.passed
        return None if passed is None else passed.route_transition.next_route

    @property
    def return_to_codex(self) -> bool:
        passed = self.filtered.invocation.decision.passed
        return False if passed is None else passed.route_transition.return_to_codex

    def codex_readable_projection(self) -> AdultCodexProjectionV2:
        if not self.eligible_for_atomic_acceptance:
            raise StateConflictError("rejected adult candidate has no Codex projection")
        passed = self.filtered.invocation.decision.passed
        if passed is None:
            raise StateConflictError("adult Filter pass lost staged artifacts")
        return passed.codex_projection

    def promotion_bundle(self) -> AdultPromotionBundleV1:
        if not self.eligible_for_atomic_acceptance:
            raise StateConflictError("rejected adult candidate has no promotable artifacts")
        passed = self.filtered.invocation.decision.passed
        if passed is None:
            raise StateConflictError("adult Filter pass lost staged artifacts")
        custody = self.scene.custody
        return AdultPromotionBundleV1(
            schema_version=AdultPromotionBundleV1.SCHEMA_VERSION,
            request_id=custody.request_id,
            candidate_id=custody.candidate_id,
            world_id=custody.world_id,
            branch_id=custody.branch_id,
            accepted_head_before_sha256=custody.accepted_head_sha256,
            scene_candidate_sha256=self.scene.candidate_sha256,
            filter_binding_sha256=self.filtered.binding_sha256,
            exact_story_prose=self.scene.invocation.output.exact_story_prose,
            exact_story_prose_sha256=text_sha256(self.scene.invocation.output.exact_story_prose),
            protected_full_record=passed.protected_full_record,
            codex_projection=passed.codex_projection,
            route_transition=passed.route_transition,
        )
