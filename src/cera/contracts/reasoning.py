"""Evidence, decision, and sequence-plan contracts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar

from cera.ids import IdKind, TypedId, require_authority_record_kind
from cera.schema import require_schema

from ._validation import kind, non_empty, optional_kind, unique_ids, unique_text


class EvidenceRecordType(StrEnum):
    GENESIS_FACT = "genesis_fact"
    SOURCE_FACT = "source_fact"
    EVENT_FACT = "event_fact"
    MEMORY = "memory"
    RELATIONSHIP = "relationship"
    THREAD = "thread"
    MATERIAL = "material"
    DEVELOPMENT = "development"


class EvidenceAuthority(StrEnum):
    CREATOR = "creator"
    SYNTHETIC_FIXTURE = "synthetic_fixture"
    ACCEPTED_SOURCE = "accepted_source"
    VALIDATED_EVENT = "validated_event"
    VALIDATED_DERIVED = "validated_derived"


class Visibility(StrEnum):
    PUBLIC = "public"
    SHARED = "shared"
    OWNER_PRIVATE = "owner_private"
    SYSTEM_PRIVATE = "system_private"


class KnowledgeRoute(StrEnum):
    DIRECT = "direct"
    REPORTED = "reported"
    INFERRED = "inferred"
    CREATOR_SEED = "creator_seed"
    NOT_APPLICABLE = "not_applicable"


class Certainty(StrEnum):
    ESTABLISHED = "established"
    BELIEVED = "believed"
    SUSPECTED = "suspected"
    FEARED = "feared"
    UNKNOWN = "unknown"


class SupersessionStatus(StrEnum):
    CURRENT = "current"
    SUPERSEDED = "superseded"
    DISPUTED = "disputed"


class TruthStatus(StrEnum):
    OBJECTIVE = "objective"
    CHARACTER_OWNED = "character_owned"
    DERIVED = "derived"
    UNKNOWN = "unknown"
    NONCANONICAL = "noncanonical"


@dataclass(frozen=True, slots=True)
class EvidenceHit:
    SCHEMA_VERSION: ClassVar[str] = "cera.evidence_hit.v1"

    schema_version: str
    evidence_id: TypedId
    record_id: TypedId
    record_version: int
    record_type: EvidenceRecordType
    truth_status: TruthStatus
    claim: str
    authority: EvidenceAuthority
    world_id: TypedId
    branch_origin_id: TypedId | None
    generation: int
    snapshot_token: TypedId
    perspective_id: TypedId | None
    owner_id: TypedId | None
    knowledge_owner_id: TypedId | None
    owner_scope: str
    visibility: Visibility
    knowledge_route: KnowledgeRoute
    certainty: Certainty
    content_class: str
    genesis_revision_id: TypedId
    valid_from: str | None
    valid_to: str | None
    branch_scope: tuple[TypedId, ...]
    source_refs: tuple[TypedId, ...]
    supersession_status: SupersessionStatus
    tags: tuple[str, ...]
    expandable_sections: tuple[str, ...]
    retrieval_reason: str

    def __post_init__(self) -> None:
        require_schema(self.schema_version, self.SCHEMA_VERSION, type(self).__name__)
        kind(self.evidence_id, IdKind.EVIDENCE, "evidence_id")
        require_authority_record_kind(self.record_id, "record_id")
        if self.record_version < 1:
            raise ValueError("record_version must be positive")
        kind(self.world_id, IdKind.WORLD, "world_id")
        optional_kind(self.branch_origin_id, IdKind.BRANCH, "branch_origin_id")
        if self.generation < 0:
            raise ValueError("generation must be non-negative")
        kind(self.snapshot_token, IdKind.SNAPSHOT, "snapshot_token")
        optional_kind(self.perspective_id, IdKind.CHARACTER, "perspective_id")
        optional_kind(self.owner_id, IdKind.CHARACTER, "owner_id")
        optional_kind(self.knowledge_owner_id, IdKind.CHARACTER, "knowledge_owner_id")
        non_empty(self.owner_scope, "owner_scope")
        non_empty(self.claim, "claim")
        non_empty(self.content_class, "content_class")
        kind(self.genesis_revision_id, IdKind.GENESIS_REVISION, "genesis_revision_id")
        for branch_id in self.branch_scope:
            kind(branch_id, IdKind.BRANCH, "branch_scope")
        unique_ids(self.branch_scope, "branch_scope")
        unique_ids(self.source_refs, "source_refs")
        unique_text(self.tags, "tags")
        non_empty(self.retrieval_reason, "retrieval_reason")


class DecisionRoute(StrEnum):
    ORDINARY = "ordinary"
    CONSENT_VALID_ADULT = "consent_valid_adult"
    AFTERMATH = "aftermath"


class BeatState(StrEnum):
    REQUESTED = "requested"
    ATTEMPTED = "attempted"
    ONGOING = "ongoing"
    PARTIAL = "partial"
    INTERRUPTED = "interrupted"
    COMPLETED = "completed"
    STOPPED = "stopped"


@dataclass(frozen=True, slots=True)
class CharacterMove:
    character_id: TypedId
    perception: str
    selected_intent: str
    action_direction: str
    evidence_ids: tuple[TypedId, ...]
    knowledge_constraints: tuple[str, ...]

    def __post_init__(self) -> None:
        kind(self.character_id, IdKind.CHARACTER, "character_id")
        non_empty(self.selected_intent, "selected_intent")
        non_empty(self.action_direction, "action_direction")
        for evidence_id in self.evidence_ids:
            kind(evidence_id, IdKind.EVIDENCE, "evidence_ids")
        unique_ids(self.evidence_ids, "evidence_ids")


@dataclass(frozen=True, slots=True)
class SequenceBeat:
    beat_id: TypedId
    actor_id: TypedId
    state: BeatState
    neutral_event: str
    evidence_ids: tuple[TypedId, ...]

    def __post_init__(self) -> None:
        kind(self.beat_id, IdKind.BEAT, "beat_id")
        if self.actor_id.kind not in (IdKind.CHARACTER, IdKind.MATERIAL):
            raise ValueError("actor_id must be a character or material ID")
        non_empty(self.neutral_event, "neutral_event")
        for evidence_id in self.evidence_ids:
            kind(evidence_id, IdKind.EVIDENCE, "evidence_ids")
        unique_ids(self.evidence_ids, "evidence_ids")


@dataclass(frozen=True, slots=True)
class CurrentSegment:
    segment_id: TypedId
    ordered_beats: tuple[SequenceBeat, ...]
    stop_before: str

    def __post_init__(self) -> None:
        kind(self.segment_id, IdKind.SEGMENT, "segment_id")
        if not self.ordered_beats:
            raise ValueError("ordered_beats must not be empty")
        unique_ids((beat.beat_id for beat in self.ordered_beats), "ordered_beats")
        non_empty(self.stop_before, "stop_before")


@dataclass(frozen=True, slots=True)
class FutureSegment:
    segment_id: TypedId
    status: str
    activation_conditions: tuple[str, ...]
    invalidation_conditions: tuple[str, ...]
    possible_consequences: tuple[str, ...]
    open_user_choice: str

    def __post_init__(self) -> None:
        kind(self.segment_id, IdKind.SEGMENT, "segment_id")
        if self.status != "conditional_plan_only":
            raise ValueError("future segment status must be conditional_plan_only")
        non_empty(self.open_user_choice, "open_user_choice")


@dataclass(frozen=True, slots=True)
class SceneDecision:
    """Non-mutating reasoning output; it cannot create durable truth."""
    SCHEMA_VERSION: ClassVar[str] = "cera.scene_decision.v1"

    schema_version: str
    decision_id: TypedId
    route: DecisionRoute
    scene_intent: str
    responding_npc_ids: tuple[TypedId, ...]
    floor_owner_id: TypedId | None
    character_moves: tuple[CharacterMove, ...]
    current_segment: CurrentSegment
    future_segments: tuple[FutureSegment, ...]
    writer_must_preserve: tuple[str, ...]
    uncertainties: tuple[str, ...]
    prohibited_inferences: tuple[str, ...]
    advisory_state_candidates: tuple[str, ...]

    def __post_init__(self) -> None:
        require_schema(self.schema_version, self.SCHEMA_VERSION, type(self).__name__)
        kind(self.decision_id, IdKind.DECISION, "decision_id")
        non_empty(self.scene_intent, "scene_intent")
        for npc_id in self.responding_npc_ids:
            kind(npc_id, IdKind.CHARACTER, "responding_npc_ids")
        unique_ids(self.responding_npc_ids, "responding_npc_ids")
        optional_kind(self.floor_owner_id, IdKind.CHARACTER, "floor_owner_id")
        move_ids = tuple(move.character_id for move in self.character_moves)
        if set(move_ids) != set(self.responding_npc_ids) or len(move_ids) != len(
            self.responding_npc_ids
        ):
            raise ValueError("character_moves must map one-to-one to responding_npc_ids")
        if self.floor_owner_id is not None and self.floor_owner_id not in self.responding_npc_ids:
            raise ValueError("floor_owner_id must be a responding NPC")
        unique_ids((segment.segment_id for segment in self.future_segments), "future_segments")
