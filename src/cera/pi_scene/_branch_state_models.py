"""Typed state/checkpoint models for :mod:`cera.pi_scene.branch_state`."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, ClassVar, Mapping, Protocol, Sequence

from cera.errors import ContractValidationError
from cera.schema import from_mapping
from cera.serialization import canonical_json, canonical_sha256, re_is_sha256, text_sha256


ROUTES = frozenset({"ordinary", "adult"})
RECORDING_STATUSES = frozenset({"projection_pending", "pending_repair", "complete"})
PRESENCE_DIRECTIONS = frozenset({"enter", "leave"})
DURABLE_KINDS = frozenset(
    {
        "material",
        "knowledge",
        "relationship",
        "character_development",
        "recorded_durable",
        "adult_public_continuity",
    }
)
VISIBILITIES = frozenset(
    {"public", "character_private", "branch_internal_unspecified"}
)
PROVISIONAL_CANON_STATUSES = frozenset(
    {"unresolved", "accepted_true", "accepted_false"}
)


class AcceptedBranchPayloadSource(Protocol):
    """Return every sanitized, verified accepted payload in branch order."""

    def accepted_branch_payloads(
        self,
        *,
        world_id: str,
        branch_id: str,
    ) -> Sequence[Mapping[str, Any]]: ...


class AcceptedBranchContextSource(AcceptedBranchPayloadSource, Protocol):
    """Add route-separated bounded prose views to the reducer stream."""

    def recent_ordinary_context_payloads(
        self,
        *,
        world_id: str,
        branch_id: str,
        limit: int = 6,
    ) -> Sequence[Mapping[str, Any]]: ...

    def recent_adult_context_payloads(
        self,
        *,
        world_id: str,
        branch_id: str,
        limit: int = 6,
    ) -> Sequence[Mapping[str, Any]]: ...


@dataclass(frozen=True, slots=True)
class BranchStateRecordV1:
    """Immutable canonical JSON value keyed inside a branch checkpoint."""

    record_id: str
    value_json: str
    value_sha256: str

    def __post_init__(self) -> None:
        required_text(self.record_id, "branch-state record_id")
        required_text(self.value_json, "branch-state value_json")
        try:
            value = json.loads(self.value_json)
        except json.JSONDecodeError as exc:
            raise ContractValidationError("branch-state record JSON is invalid") from exc
        if not isinstance(value, Mapping):
            raise ContractValidationError("branch-state record must contain an object")
        if canonical_json(value) != self.value_json:
            raise ContractValidationError("branch-state record JSON is not canonical")
        require_sha(self.value_sha256, "branch-state value_sha256")
        if text_sha256(self.value_json) != self.value_sha256:
            raise ContractValidationError("branch-state record hash changed")

    @classmethod
    def from_value(
        cls,
        record_id: str,
        value: Mapping[str, Any],
    ) -> BranchStateRecordV1:
        value_json = canonical_json(dict(value))
        return cls(record_id, value_json, text_sha256(value_json))

    def as_mapping(self) -> dict[str, Any]:
        value = json.loads(self.value_json)
        if not isinstance(value, dict):  # guarded by __post_init__
            raise AssertionError("canonical branch-state record stopped being an object")
        return value


@dataclass(frozen=True, slots=True)
class GenesisBranchStateV1:
    """Immutable branch-independent baseline for one Genesis revision."""

    SCHEMA_VERSION: ClassVar[str] = "cera.pi_scene.genesis_branch_state.v1"

    schema_version: str
    world_id: str
    genesis_revision: str
    scene_id: str
    accepted_present_character_ids: tuple[str, ...]
    resulting_public_state: str
    characters: tuple[BranchStateRecordV1, ...]
    relationships: tuple[BranchStateRecordV1, ...]
    memories: tuple[BranchStateRecordV1, ...]

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("Pi Scene Genesis state schema changed")
        for name in ("world_id", "genesis_revision", "scene_id", "resulting_public_state"):
            required_text(getattr(self, name), f"Genesis {name}")
        unique_character_ids(
            self.accepted_present_character_ids,
            "Genesis accepted_present_character_ids",
        )
        unique_records(self.characters, "Genesis characters")
        unique_records(self.relationships, "Genesis relationships")
        unique_records(self.memories, "Genesis memories")

    @classmethod
    def from_mappings(
        cls,
        *,
        world_id: str,
        genesis_revision: str,
        scene_id: str,
        accepted_present_character_ids: Sequence[str],
        resulting_public_state: str,
        characters: Mapping[str, Mapping[str, Any]],
        relationships: Mapping[str, Mapping[str, Any]],
        memories: Mapping[str, Mapping[str, Any]],
    ) -> GenesisBranchStateV1:
        return cls(
            schema_version=cls.SCHEMA_VERSION,
            world_id=world_id,
            genesis_revision=genesis_revision,
            scene_id=scene_id,
            accepted_present_character_ids=tuple(accepted_present_character_ids),
            resulting_public_state=resulting_public_state,
            characters=records_from_mapping(characters),
            relationships=records_from_mapping(relationships),
            memories=records_from_mapping(memories),
        )

    @property
    def genesis_sha256(self) -> str:
        return canonical_sha256(self)


@dataclass(frozen=True, slots=True)
class PresenceChangeV1:
    character_id: str
    direction: str
    effective_after_item_key: str

    def __post_init__(self) -> None:
        character_id(self.character_id, "presence character_id")
        if self.direction not in PRESENCE_DIRECTIONS:
            raise ContractValidationError("presence direction is invalid")
        required_text(self.effective_after_item_key, "presence effective_after_item_key")


@dataclass(frozen=True, slots=True)
class DurableBranchChangeV1:
    """One accepted, source-labeled durable continuity statement."""

    change_key: str
    kind: str
    subject_ids: tuple[str, ...]
    concise_change: str
    target_key: str
    visibility: str
    knowledge_owner_id: str | None
    accepted_turn_id: str
    source_kind: str

    def __post_init__(self) -> None:
        for name in (
            "change_key",
            "concise_change",
            "target_key",
            "accepted_turn_id",
            "source_kind",
        ):
            required_text(getattr(self, name), f"durable {name}")
        if self.kind not in DURABLE_KINDS:
            raise ContractValidationError("durable change kind is invalid")
        if self.visibility not in VISIBILITIES:
            raise ContractValidationError("durable change visibility is invalid")
        if len(self.subject_ids) != len(set(self.subject_ids)):
            raise ContractValidationError("durable change subjects contain duplicates")
        for subject_id in self.subject_ids:
            required_text(subject_id, "durable change subject")
        if self.visibility == "character_private":
            if self.knowledge_owner_id is None:
                raise ContractValidationError("private durable change requires an owner")
            character_id(self.knowledge_owner_id, "durable knowledge owner")
            if self.knowledge_owner_id not in self.subject_ids:
                raise ContractValidationError("private durable change owner must be a subject")
        elif self.knowledge_owner_id is not None:
            raise ContractValidationError("non-private durable change cannot name an owner")


@dataclass(frozen=True, slots=True)
class AdultPublicContinuityV1:
    """Non-explicit return-to-Codex projection for one accepted adult event."""

    accepted_turn_id: str
    event_key: str
    non_explicit_summary: str
    lasting_story_meaning: str

    def __post_init__(self) -> None:
        for name in (
            "accepted_turn_id",
            "event_key",
            "non_explicit_summary",
            "lasting_story_meaning",
        ):
            required_text(getattr(self, name), f"adult projection {name}")


@dataclass(frozen=True, slots=True)
class AcceptedLineageEntryV1:
    """Hash-only ancestry for canonical accepted prose."""

    world_id: str
    branch_id: str
    accepted_order: int
    accepted_turn_id: str
    parent_accepted_turn_id: str | None
    route: str
    receipt_sha256: str
    candidate_sha256: str

    def __post_init__(self) -> None:
        for name in ("world_id", "branch_id", "accepted_turn_id"):
            required_text(getattr(self, name), f"lineage {name}")
        if type(self.accepted_order) is not int or self.accepted_order < 1:
            raise ContractValidationError("lineage accepted order is invalid")
        if self.parent_accepted_turn_id is not None:
            required_text(self.parent_accepted_turn_id, "lineage parent turn")
        if self.route not in ROUTES:
            raise ContractValidationError("lineage route is invalid")
        require_sha(self.receipt_sha256, "lineage receipt_sha256")
        require_sha(self.candidate_sha256, "lineage candidate_sha256")


@dataclass(frozen=True, slots=True)
class ProvisionalCanonLineageEntryV1:
    """Explicit lower-confidence canon lineage, separate from accepted prose."""

    lineage_entry_id: str
    provisional_canon_id: str
    parent_lineage_entry_id: str | None
    status: str
    authority_id: str

    def __post_init__(self) -> None:
        for name in ("lineage_entry_id", "provisional_canon_id", "authority_id"):
            required_text(getattr(self, name), f"provisional canon {name}")
        if self.parent_lineage_entry_id is not None:
            required_text(
                self.parent_lineage_entry_id,
                "provisional canon parent_lineage_entry_id",
            )
        if self.status not in PROVISIONAL_CANON_STATUSES:
            raise ContractValidationError("provisional canon status is invalid")


@dataclass(frozen=True, slots=True)
class BranchLineageHopV1:
    parent_branch_id: str
    child_branch_id: str
    forked_at_accepted_order: int
    forked_at_accepted_turn_id: str
    parent_checkpoint_sha256: str

    def __post_init__(self) -> None:
        for name in ("parent_branch_id", "child_branch_id", "forked_at_accepted_turn_id"):
            required_text(getattr(self, name), f"branch lineage {name}")
        if self.parent_branch_id == self.child_branch_id:
            raise ContractValidationError("branch fork must create a distinct child")
        if type(self.forked_at_accepted_order) is not int or self.forked_at_accepted_order < 1:
            raise ContractValidationError("branch fork requires an accepted checkpoint")
        require_sha(self.parent_checkpoint_sha256, "parent checkpoint hash")


@dataclass(frozen=True, slots=True)
class AcceptedBranchEventV1:
    """Prose-free semantic event projected from one accepted store payload."""

    world_id: str
    branch_id: str
    scene_id: str
    accepted_order: int
    accepted_turn_id: str
    parent_accepted_turn_id: str | None
    parent_accepted_head_sha256: str | None
    receipt_sha256: str
    route: str
    candidate_sha256: str
    presence_changes: tuple[PresenceChangeV1, ...]
    durable_changes: tuple[DurableBranchChangeV1, ...]
    secondary_canon: tuple[str, ...]
    relationship_changes: tuple[str, ...]
    knowledge_changes: tuple[str, ...]
    resulting_public_state: str | None
    unresolved_threads: tuple[str, ...]
    adult_public_continuity: tuple[AdultPublicContinuityV1, ...]
    derived_state_complete: bool

    def __post_init__(self) -> None:
        for name in ("world_id", "branch_id", "scene_id", "accepted_turn_id"):
            required_text(getattr(self, name), f"accepted event {name}")
        if type(self.accepted_order) is not int or self.accepted_order < 1:
            raise ContractValidationError("accepted event order is invalid")
        if self.parent_accepted_turn_id is not None:
            required_text(self.parent_accepted_turn_id, "accepted event parent")
        if (self.parent_accepted_turn_id is None) != (
            self.parent_accepted_head_sha256 is None
        ):
            raise ContractValidationError("accepted event parent identity/hash disagree")
        if self.parent_accepted_head_sha256 is not None:
            require_sha(self.parent_accepted_head_sha256, "accepted event parent hash")
        require_sha(self.receipt_sha256, "accepted event receipt hash")
        require_sha(self.candidate_sha256, "accepted event candidate hash")
        if self.route not in ROUTES:
            raise ContractValidationError("accepted event route is invalid")
        unique_text(self.secondary_canon, "accepted secondary canon")
        unique_text(self.relationship_changes, "accepted relationship changes")
        unique_text(self.knowledge_changes, "accepted knowledge changes")
        unique_text(self.unresolved_threads, "accepted unresolved threads")
        if self.route == "ordinary" and self.adult_public_continuity:
            raise ContractValidationError("ordinary event contains adult projection")
        if self.route == "adult" and self.presence_changes:
            raise ContractValidationError("adult projection cannot authorize presence changes")
        if type(self.derived_state_complete) is not bool:
            raise ContractValidationError("accepted derived-state status is invalid")
        if self.derived_state_complete:
            required_text(self.resulting_public_state, "accepted resulting_public_state")
        elif any(
            (
                self.presence_changes,
                self.durable_changes,
                self.secondary_canon,
                self.relationship_changes,
                self.knowledge_changes,
                self.unresolved_threads,
                self.adult_public_continuity,
            )
        ) or self.resulting_public_state is not None:
            raise ContractValidationError("pending event cannot promote derived state")

    @property
    def accepted_lineage_entry(self) -> AcceptedLineageEntryV1:
        return AcceptedLineageEntryV1(
            world_id=self.world_id,
            branch_id=self.branch_id,
            accepted_order=self.accepted_order,
            accepted_turn_id=self.accepted_turn_id,
            parent_accepted_turn_id=self.parent_accepted_turn_id,
            route=self.route,
            receipt_sha256=self.receipt_sha256,
            candidate_sha256=self.candidate_sha256,
        )


@dataclass(frozen=True, slots=True)
class BranchStateCheckpointV1:
    """Regenerable full accepted state at one exact branch checkpoint."""

    SCHEMA_VERSION: ClassVar[str] = "cera.pi_scene.branch_state_checkpoint.v1"

    schema_version: str
    world_id: str
    branch_id: str
    genesis_revision: str
    genesis_sha256: str
    scene_id: str
    accepted_order: int
    last_accepted_turn_id: str | None
    last_accepted_receipt_sha256: str | None
    accepted_present_character_ids: tuple[str, ...]
    characters: tuple[BranchStateRecordV1, ...]
    relationships: tuple[BranchStateRecordV1, ...]
    memories: tuple[BranchStateRecordV1, ...]
    durable_changes: tuple[DurableBranchChangeV1, ...]
    resulting_public_state: str
    unresolved_threads: tuple[str, ...]
    adult_public_continuity: tuple[AdultPublicContinuityV1, ...]
    accepted_lineage: tuple[AcceptedLineageEntryV1, ...]
    provisional_canon_lineage: tuple[ProvisionalCanonLineageEntryV1, ...]
    pending_ordinary_recording_turn_ids: tuple[str, ...]
    pending_adult_projection_turn_ids: tuple[str, ...]
    branch_lineage: tuple[BranchLineageHopV1, ...]

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("Pi Scene branch checkpoint schema changed")
        for name in (
            "world_id",
            "branch_id",
            "genesis_revision",
            "scene_id",
            "resulting_public_state",
        ):
            required_text(getattr(self, name), f"checkpoint {name}")
        require_sha(self.genesis_sha256, "checkpoint Genesis hash")
        if type(self.accepted_order) is not int or self.accepted_order < 0:
            raise ContractValidationError("checkpoint accepted order is invalid")
        if (self.last_accepted_turn_id is None) != (
            self.last_accepted_receipt_sha256 is None
        ):
            raise ContractValidationError("checkpoint accepted head identity/hash disagree")
        if self.last_accepted_turn_id is not None:
            required_text(self.last_accepted_turn_id, "checkpoint accepted head")
            require_sha(self.last_accepted_receipt_sha256, "checkpoint head hash")
        if self.accepted_order == 0:
            if self.last_accepted_turn_id is not None or self.accepted_lineage:
                raise ContractValidationError("root checkpoint contains accepted ancestry")
        elif (
            not self.accepted_lineage
            or self.accepted_lineage[-1].accepted_turn_id != self.last_accepted_turn_id
            or self.accepted_lineage[-1].accepted_order != self.accepted_order
        ):
            raise ContractValidationError("checkpoint accepted lineage is incomplete")
        unique_character_ids(
            self.accepted_present_character_ids,
            "checkpoint accepted presence",
        )
        unique_records(self.characters, "checkpoint characters")
        unique_records(self.relationships, "checkpoint relationships")
        unique_records(self.memories, "checkpoint memories")
        unique_text(self.unresolved_threads, "checkpoint unresolved threads")
        unique_text(
            self.pending_ordinary_recording_turn_ids,
            "checkpoint pending ordinary recordings",
        )
        unique_text(
            self.pending_adult_projection_turn_ids,
            "checkpoint pending adult projections",
        )
        if set(self.pending_ordinary_recording_turn_ids) & set(
            self.pending_adult_projection_turn_ids
        ):
            raise ContractValidationError("checkpoint pending route sets overlap")
        self._validate_provisional_lineage()

    def _validate_provisional_lineage(self) -> None:
        entry_ids = tuple(value.lineage_entry_id for value in self.provisional_canon_lineage)
        if len(entry_ids) != len(set(entry_ids)):
            raise ContractValidationError("provisional-canon lineage IDs contain duplicates")
        seen: set[str] = set()
        for value in self.provisional_canon_lineage:
            if (
                value.parent_lineage_entry_id is not None
                and value.parent_lineage_entry_id not in seen
            ):
                raise ContractValidationError("provisional-canon parent must precede child")
            seen.add(value.lineage_entry_id)

    @property
    def checkpoint_sha256(self) -> str:
        return canonical_sha256(self)

    def character_mapping(self) -> dict[str, dict[str, Any]]:
        return records_to_mapping(self.characters)

    def relationship_mapping(self) -> dict[str, dict[str, Any]]:
        return records_to_mapping(self.relationships)

    def memory_mapping(self) -> dict[str, dict[str, Any]]:
        return records_to_mapping(self.memories)

    def to_mapping(self) -> dict[str, Any]:
        value = json.loads(canonical_json(self))
        if not isinstance(value, dict):
            raise AssertionError("checkpoint serialization stopped being an object")
        return value

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> BranchStateCheckpointV1:
        return from_mapping(cls, value)


def records_from_mapping(
    values: Mapping[str, Mapping[str, Any]],
) -> tuple[BranchStateRecordV1, ...]:
    output: list[BranchStateRecordV1] = []
    for record_id in sorted(values):
        value = values[record_id]
        if not isinstance(value, Mapping):
            raise ContractValidationError("branch-state mapping contains a non-object")
        output.append(BranchStateRecordV1.from_value(record_id, value))
    return tuple(output)


def records_to_mapping(
    values: Sequence[BranchStateRecordV1],
) -> dict[str, dict[str, Any]]:
    return {value.record_id: value.as_mapping() for value in values}


def unique_records(values: Sequence[BranchStateRecordV1], field_name: str) -> None:
    record_ids = tuple(value.record_id for value in values)
    if len(record_ids) != len(set(record_ids)):
        raise ContractValidationError(f"{field_name} contain duplicate record IDs")


def required_text(value: object, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ContractValidationError(f"{field_name} is invalid")


def character_id(value: object, field_name: str) -> None:
    if not isinstance(value, str) or not value.startswith("character:"):
        raise ContractValidationError(f"{field_name} is invalid")


def unique_character_ids(values: Sequence[str], field_name: str) -> None:
    if not values:
        raise ContractValidationError(f"{field_name} are empty")
    for value in values:
        character_id(value, field_name)
    if len(values) != len(set(values)):
        raise ContractValidationError(f"{field_name} contain duplicates")


def unique_text(values: Sequence[str], field_name: str) -> None:
    for value in values:
        required_text(value, field_name)
    if len(values) != len(set(values)):
        raise ContractValidationError(f"{field_name} contain duplicates")


def require_sha(value: object, field_name: str) -> None:
    if not isinstance(value, str) or not re_is_sha256(value):
        raise ContractValidationError(f"{field_name} is invalid")
