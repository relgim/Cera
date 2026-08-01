"""Provider-facing scene scope, compact evidence, and discardable capsules.

These projections are advisory input views.  The authoritative request and
full exact evidence remain in Python for alias resolution and validation.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import ClassVar

from cera.errors import ContractValidationError
from cera.evidence import ExactEvidence
from cera.ids import IdKind, TypedId, require_kind
from cera.schema import require_schema
from cera.serialization import domain_sha256

from .models import SceneReasonerRequest


_CURRENT_ONLY_CUE = re.compile(
    r"\b(?:only\s+the\s+current\s+characters|current\s+characters\s+only|"
    r"those\s+(?:already\s+)?(?:here|present)|continue\s+(?:the\s+)?scene)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class SceneCastScopeV1:
    SCHEMA_VERSION: ClassVar[str] = "cera.scene_cast_scope.v1"

    schema_version: str
    protected_user_id: TypedId
    world_known_character_ids: tuple[TypedId, ...]
    physically_present_character_ids: tuple[TypedId, ...]
    scene_reachable_character_ids: tuple[TypedId, ...]
    currently_active_character_ids: tuple[TypedId, ...]
    exact_source_responder_ids: tuple[TypedId, ...]
    eligible_responder_ids: tuple[TypedId, ...]
    selected_responder_ids: tuple[TypedId, ...]
    derivation_reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        require_schema(self.schema_version, self.SCHEMA_VERSION, type(self).__name__)
        require_kind(self.protected_user_id, IdKind.CHARACTER, "protected_user_id")
        for field in (
            "world_known_character_ids",
            "physically_present_character_ids",
            "scene_reachable_character_ids",
            "currently_active_character_ids",
            "exact_source_responder_ids",
            "eligible_responder_ids",
            "selected_responder_ids",
        ):
            values = getattr(self, field)
            if len(values) != len(set(values)):
                raise ContractValidationError(f"{field} contains duplicates")
            for value in values:
                require_kind(value, IdKind.CHARACTER, field)
        world = set(self.world_known_character_ids)
        present = set(self.physically_present_character_ids)
        reachable = set(self.scene_reachable_character_ids)
        active = set(self.currently_active_character_ids)
        exact = set(self.exact_source_responder_ids)
        eligible = set(self.eligible_responder_ids)
        selected = set(self.selected_responder_ids)
        if self.protected_user_id not in world or self.protected_user_id not in present:
            raise ContractValidationError("scene cast scope must include protected user")
        if not present.issubset(world) or not reachable.issubset(world):
            raise ContractValidationError("scene cast scope exceeds known world cast")
        if not active.issubset(present):
            raise ContractValidationError("active cast must be physically present")
        npc_world = world - {self.protected_user_id}
        if not exact.issubset(npc_world) or not eligible.issubset(reachable):
            raise ContractValidationError("scene responder scope is invalid")
        if not selected.issubset(eligible):
            raise ContractValidationError("selected responders must be eligible")
        if not self.derivation_reasons or any(
            not value.strip() for value in self.derivation_reasons
        ):
            raise ContractValidationError("scene scope derivation reasons are required")


def derive_scene_cast_scope(
    *,
    raw_message: str,
    protected_user_id: TypedId,
    world_known_npc_ids: tuple[TypedId, ...],
    accepted_active_npc_ids: tuple[TypedId, ...],
    explicitly_named_npc_ids: tuple[TypedId, ...] = (),
    all_cast_requested: bool = False,
) -> SceneCastScopeV1:
    """Derive current scope from source plus the exact accepted branch head."""

    if not raw_message.strip():
        raise ContractValidationError("scene scope requires source text")
    world_npcs = tuple(dict.fromkeys(world_known_npc_ids))
    world_set = set(world_npcs)
    accepted = tuple(
        value for value in dict.fromkeys(accepted_active_npc_ids) if value in world_set
    )
    explicit = tuple(
        value for value in dict.fromkeys(explicitly_named_npc_ids) if value in world_set
    )
    if all_cast_requested:
        active_npcs = world_npcs
        eligible = world_npcs
        reasons = ("source_explicitly_requests_world_cast",)
    elif _CURRENT_ONLY_CUE.search(raw_message) is not None and accepted:
        active_npcs = accepted
        eligible = accepted
        reasons = (
            "current-only continuation cue",
            "active cast inherited from exact accepted branch head",
        )
    elif explicit:
        active_npcs = tuple(dict.fromkeys((*accepted, *explicit)))
        eligible = active_npcs
        reasons = (
            "explicit source character reference",
            "accepted active cast retained for continuity",
        )
    elif accepted:
        active_npcs = accepted
        eligible = world_npcs
        reasons = (
            "active cast inherited from exact accepted branch head",
            "unconstrained source leaves scene-reachable entrants eligible",
        )
    else:
        active_npcs = ()
        eligible = world_npcs
        reasons = (
            "no accepted branch head exists",
            "scene-reachable cast remains eligible for initial selection",
        )
    present = (protected_user_id, *active_npcs)
    return SceneCastScopeV1(
        schema_version=SceneCastScopeV1.SCHEMA_VERSION,
        protected_user_id=protected_user_id,
        world_known_character_ids=(protected_user_id, *world_npcs),
        physically_present_character_ids=present,
        scene_reachable_character_ids=(protected_user_id, *world_npcs),
        currently_active_character_ids=present,
        exact_source_responder_ids=explicit,
        eligible_responder_ids=eligible,
        selected_responder_ids=(),
        derivation_reasons=reasons,
    )


@dataclass(frozen=True, slots=True)
class CompactEvidenceViewV1:
    alias: str
    record_type: str
    subject_ids: tuple[TypedId, ...]
    owner_id: TypedId | None
    supported_content: object
    authority: str
    truth_status: str
    epistemic_class: str
    knowledge_owner_ids: tuple[TypedId, ...]
    visibility: str
    record_version: int
    exact_evidence_sha256: str
    relevance_reason: str

    def __post_init__(self) -> None:
        if not self.alias.startswith("evidence:seed_"):
            raise ContractValidationError("compact evidence alias is invalid")
        if not self.record_type.strip() or not self.relevance_reason.strip():
            raise ContractValidationError("compact evidence identity is incomplete")
        if self.record_version < 1:
            raise ContractValidationError("compact evidence version must be positive")
        if len(self.exact_evidence_sha256) != 64:
            raise ContractValidationError("compact evidence hash is invalid")


def compact_evidence_view(
    evidence: ExactEvidence,
    *,
    alias: str,
) -> CompactEvidenceViewV1:
    """Remove provenance bulk while preserving all fetched section content."""

    try:
        content = json.loads(evidence.sections_json)
    except json.JSONDecodeError:
        content = evidence.sections_json
    metadata = evidence.metadata
    return CompactEvidenceViewV1(
        alias=alias,
        record_type=metadata.record_type.value,
        subject_ids=evidence.subject_ids,
        owner_id=metadata.owner_id,
        supported_content=content,
        authority=metadata.authority.value,
        truth_status=metadata.truth_status.value,
        epistemic_class=metadata.epistemic_class.value,
        knowledge_owner_ids=metadata.knowledge_owner_ids,
        visibility=metadata.visibility.value,
        record_version=metadata.record_version,
        exact_evidence_sha256=domain_sha256("cera.compact_exact_evidence.v1", evidence),
        relevance_reason=metadata.retrieval_reason,
    )


@dataclass(frozen=True, slots=True)
class ReasonerReadingCapsuleV1:
    """Discardable local reconstruction aid; never durable story authority."""

    SCHEMA_VERSION: ClassVar[str] = "cera.reasoner_reading_capsule.v1"

    schema_version: str
    branch_id: TypedId
    generation: int
    active_character_ids: tuple[TypedId, ...]
    eligible_responder_ids: tuple[TypedId, ...]
    prior_floor_owner_id: TypedId | None
    accepted_continuity_aliases: tuple[str, ...]
    position_and_material_aliases: tuple[str, ...]
    unresolved_thread_aliases: tuple[str, ...]
    relationship_aliases: tuple[str, ...]
    protected_user_restrictions: tuple[str, ...]
    stable_prompt_identity: str
    durable_authority: bool
    invalidation_policy: str

    def __post_init__(self) -> None:
        require_schema(self.schema_version, self.SCHEMA_VERSION, type(self).__name__)
        require_kind(self.branch_id, IdKind.BRANCH, "branch_id")
        if self.generation < 0:
            raise ContractValidationError("capsule generation cannot be negative")
        if self.prior_floor_owner_id is not None:
            require_kind(self.prior_floor_owner_id, IdKind.CHARACTER, "prior_floor_owner_id")
        if self.durable_authority:
            raise ContractValidationError("reading capsule cannot be durable authority")
        if not self.stable_prompt_identity.strip() or not self.invalidation_policy.strip():
            raise ContractValidationError("reading capsule lifecycle is incomplete")


def build_reasoner_reading_capsule(
    request: SceneReasonerRequest,
    compact_evidence: tuple[CompactEvidenceViewV1, ...],
    *,
    stable_prompt_identity: str,
    prior_floor_owner_id: TypedId | None = None,
) -> ReasonerReadingCapsuleV1:
    by_type: dict[str, list[str]] = {}
    for value in compact_evidence:
        by_type.setdefault(value.record_type, []).append(value.alias)
    prepared = request.prepared_turn
    return ReasonerReadingCapsuleV1(
        schema_version=ReasonerReadingCapsuleV1.SCHEMA_VERSION,
        branch_id=prepared.request.branch_id,
        generation=prepared.evidence_snapshot.generation,
        active_character_ids=prepared.present_character_ids,
        eligible_responder_ids=prepared.eligible_responding_npc_ids,
        prior_floor_owner_id=prior_floor_owner_id,
        accepted_continuity_aliases=tuple(
            by_type.get("event_fact", ()) + by_type.get("material", ())
        ),
        position_and_material_aliases=tuple(by_type.get("material", ())),
        unresolved_thread_aliases=tuple(by_type.get("thread", ())),
        relationship_aliases=tuple(by_type.get("relationship", ())),
        protected_user_restrictions=request.hard_boundaries,
        stable_prompt_identity=stable_prompt_identity,
        durable_authority=False,
        invalidation_policy=(
            "Discard on rejection, regeneration, branch fork, stale snapshot, "
            "provider loss, or process shutdown; rebuild after acceptance."
        ),
    )
