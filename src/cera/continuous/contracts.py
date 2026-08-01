"""Shadow contracts for continuous Planner and Validator sessions.

These contracts are intentionally provider-neutral and non-authoritative.  A
Planner sequence is causal guidance for the Composer.  A Validator package is
a candidate publication package until Python validates it and the creator
chooses an accepting action.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re
from typing import Any, ClassVar, Mapping

from cera.creator_review.models import (
    CreatorReviewAssessment,
    CreatorReviewAction,
)
from cera.errors import ContractValidationError
from cera.serialization import canonical_sha256, domain_sha256, re_is_sha256


_KEY = re.compile(r"[a-z][a-z0-9_]{0,95}\Z")
_IDENTITY = re.compile(r"[a-z][a-z0-9_.:-]{0,191}\Z")
_RELATIVE_PART = re.compile(r"[^\\/:*?\"<>|\x00-\x1f]+\Z")


def _text(value: str, field: str, *, maximum: int = 8_000) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ContractValidationError(f"{field} must be non-empty and bounded")


def _identity(value: str, field: str) -> None:
    if not isinstance(value, str) or _IDENTITY.fullmatch(value) is None:
        raise ContractValidationError(f"{field} is not a stable identity")


def _key(value: str, field: str) -> None:
    if not isinstance(value, str) or _KEY.fullmatch(value) is None:
        raise ContractValidationError(f"{field} is not a local key")


def _unique(values: tuple[Any, ...], field: str) -> None:
    rendered = tuple(str(value) for value in values)
    if len(rendered) != len(set(rendered)):
        raise ContractValidationError(f"{field} contains duplicates")


def _relative_path(value: str, field: str) -> None:
    _text(value, field, maximum=512)
    normalized = value.replace("\\", "/")
    parts = normalized.split("/")
    if (
        value.startswith(("/", "\\"))
        or re.match(r"^[A-Za-z]:", value)
        or any(part in {"", ".", ".."} for part in parts)
        or any(_RELATIVE_PART.fullmatch(part) is None for part in parts)
    ):
        raise ContractValidationError(f"{field} must remain under the world root")


class ProtectedUserAllowanceMode(StrEnum):
    NONE = "none"
    EXACT_SOURCE_ONLY = "exact_source_only"
    MINIMAL_NONBRANCHING_CONNECTIVE = "minimal_nonbranching_connective"


@dataclass(frozen=True, slots=True)
class ProtectedUserAllowanceV1:
    mode: ProtectedUserAllowanceMode
    source_binding_keys: tuple[str, ...]
    explanation: str

    def __post_init__(self) -> None:
        _text(self.explanation, "protected_user_allowance.explanation", maximum=1_000)
        for value in self.source_binding_keys:
            _key(value, "protected_user_allowance.source_binding_keys")
        _unique(self.source_binding_keys, "protected_user_allowance.source_binding_keys")
        if self.mode is ProtectedUserAllowanceMode.EXACT_SOURCE_ONLY and not self.source_binding_keys:
            raise ContractValidationError("exact protected-user allowance requires source bindings")
        if self.mode is ProtectedUserAllowanceMode.NONE and self.source_binding_keys:
            raise ContractValidationError("no protected-user allowance cannot carry source bindings")


@dataclass(frozen=True, slots=True)
class RichSequenceBeatV1:
    """One causal unit; not a line of prose or a fixed-size micro-action."""

    beat_key: str
    actor_ids: tuple[str, ...]
    evidence_grounded_perception: str
    immediate_goal: str
    relevant_character_pressures: tuple[str, ...]
    competing_obligation_or_constraint: str | None
    selected_tactic: str
    causal_explanation: str
    observable_action_or_dialogue_direction: str
    private_state_guidance: str
    physical_material_continuity: str
    resulting_state: str
    deepseek_realization_space: tuple[str, ...]
    protected_user_allowance: ProtectedUserAllowanceV1
    source_evidence_bindings: tuple[str, ...]

    def __post_init__(self) -> None:
        _key(self.beat_key, "beat_key")
        if not self.actor_ids:
            raise ContractValidationError("rich sequence beat requires an actor")
        for value in self.actor_ids:
            _identity(value, "actor_ids")
        _unique(self.actor_ids, "actor_ids")
        for field in (
            "evidence_grounded_perception",
            "immediate_goal",
            "selected_tactic",
            "causal_explanation",
            "observable_action_or_dialogue_direction",
            "private_state_guidance",
            "physical_material_continuity",
            "resulting_state",
        ):
            _text(getattr(self, field), field, maximum=4_000)
        if self.competing_obligation_or_constraint is not None:
            _text(
                self.competing_obligation_or_constraint,
                "competing_obligation_or_constraint",
                maximum=2_000,
            )
        if not self.relevant_character_pressures or not self.deepseek_realization_space:
            raise ContractValidationError(
                "rich sequence beat requires pressure and realization space"
            )
        for field in ("relevant_character_pressures", "deepseek_realization_space"):
            values = getattr(self, field)
            for value in values:
                _text(value, field, maximum=2_000)
            _unique(values, field)
        if not self.source_evidence_bindings:
            raise ContractValidationError("rich sequence beat requires source/evidence bindings")
        for value in self.source_evidence_bindings:
            _key(value, "source_evidence_bindings")
        _unique(self.source_evidence_bindings, "source_evidence_bindings")
        reasoning = (
            self.evidence_grounded_perception,
            self.immediate_goal,
            self.selected_tactic,
            self.causal_explanation,
            self.resulting_state,
        )
        normalized = {" ".join(value.casefold().split()) for value in reasoning}
        if len(normalized) < 4 or any(len(value.split()) < 3 for value in reasoning):
            raise ContractValidationError("rich sequence beat is structurally shallow")
        if "character:ted" in self.actor_ids:
            if self.protected_user_allowance.mode is ProtectedUserAllowanceMode.NONE:
                raise ContractValidationError(
                    "protected user cannot be an actor without an explicit allowance"
                )


@dataclass(frozen=True, slots=True)
class RichPlannerSequenceV1:
    SCHEMA_VERSION: ClassVar[str] = "cera.rich_planner_sequence.v1"

    schema_version: str
    sequence_id: str
    world_id: str
    branch_id: str
    accepted_turn_id: str | None
    scene_id: str
    selected_character_ids: tuple[str, ...]
    omitted_character_ids: tuple[str, ...]
    beats: tuple[RichSequenceBeatV1, ...]
    final_stop_state: str
    unresolved_threads: tuple[str, ...]
    provisional: bool

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("rich Planner sequence schema version changed")
        for field in ("sequence_id", "world_id", "branch_id", "scene_id"):
            _identity(getattr(self, field), field)
        if self.accepted_turn_id is not None:
            _identity(self.accepted_turn_id, "accepted_turn_id")
        if not self.selected_character_ids or not self.beats:
            raise ContractValidationError("rich Planner sequence requires cast and beats")
        for field in ("selected_character_ids", "omitted_character_ids"):
            values = getattr(self, field)
            for value in values:
                _identity(value, field)
            _unique(values, field)
        if set(self.selected_character_ids).intersection(self.omitted_character_ids):
            raise ContractValidationError("selected and omitted characters overlap")
        _unique(tuple(value.beat_key for value in self.beats), "beat keys")
        selected = set(self.selected_character_ids)
        for beat in self.beats:
            npc_actors = {value for value in beat.actor_ids if value != "character:ted"}
            if not npc_actors.issubset(selected):
                raise ContractValidationError("rich sequence beat names an unselected character")
        _text(self.final_stop_state, "final_stop_state", maximum=4_000)
        for value in self.unresolved_threads:
            _text(value, "unresolved_threads", maximum=2_000)
        _unique(self.unresolved_threads, "unresolved_threads")

    @property
    def sequence_sha256(self) -> str:
        return domain_sha256(self.SCHEMA_VERSION, self)


@dataclass(frozen=True, slots=True)
class CharacterSummaryEnvelopeV1:
    SCHEMA_VERSION: ClassVar[str] = "cera.character_summary_envelope.v1"

    schema_version: str
    character_id: str
    source_path_or_record_id: str
    source_revision: int
    latest_accepted_changes: tuple[str, ...]
    summary: str
    incomplete: bool = True
    more_information_available: bool = True

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("character summary schema version changed")
        _identity(self.character_id, "character_id")
        if self.source_path_or_record_id.startswith("record:"):
            _identity(self.source_path_or_record_id, "source_path_or_record_id")
        else:
            _relative_path(self.source_path_or_record_id, "source_path_or_record_id")
        if type(self.source_revision) is not int or self.source_revision < 1:
            raise ContractValidationError("character summary revision must be positive")
        for value in self.latest_accepted_changes:
            _text(value, "latest_accepted_changes", maximum=2_000)
        _unique(self.latest_accepted_changes, "latest_accepted_changes")
        _text(self.summary, "summary", maximum=16_000)
        if not self.incomplete or not self.more_information_available:
            raise ContractValidationError(
                "V1 character summary must disclose that it is incomplete and expandable"
            )

    @property
    def envelope_sha256(self) -> str:
        return domain_sha256(self.SCHEMA_VERSION, self)


@dataclass(frozen=True, slots=True)
class AcceptedFinalSequenceEnvelopeV1:
    SCHEMA_VERSION: ClassVar[str] = "cera.accepted_final_sequence_envelope.v1"

    schema_version: str
    accepted_turn_id: str
    user_message: str
    complete_final_sequence: FinalSequenceV1
    acceptance_receipt_sha256: str

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("accepted sequence envelope schema changed")
        _identity(self.accepted_turn_id, "accepted_turn_id")
        _text(self.user_message, "user_message", maximum=64_000)
        if self.complete_final_sequence.accepted_turn_id != self.accepted_turn_id:
            raise ContractValidationError("accepted sequence turn identity changed")
        if not re_is_sha256(self.acceptance_receipt_sha256):
            raise ContractValidationError("accepted sequence receipt hash is invalid")

    @property
    def envelope_sha256(self) -> str:
        return domain_sha256(self.SCHEMA_VERSION, self)

    def render_for_planner(self) -> str:
        from cera.serialization import to_primitive
        import json

        return (
            "[TURN ACCEPTED]\n"
            f"[USER MESSAGE {self.accepted_turn_id}]\n{self.user_message}\n"
            f"[COMPLETE FINAL SEQUENCE {self.accepted_turn_id}]\n"
            + json.dumps(
                to_primitive(self.complete_final_sequence),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        )


class ValidatorTaskMode(StrEnum):
    FINALIZE_TURN = "finalize_turn"
    SCENE_SUMMARY = "scene_summary"


class ValidatorSemanticStatus(StrEnum):
    ACCEPTED = "accepted"
    CONCERN = "concern"
    REJECTED = "rejected"
    ERROR = "error"


class WorldEditOperationKind(StrEnum):
    ADD = "add"
    REPLACE = "replace"
    REMOVE = "remove"
    APPEND_UNIQUE = "append_unique"
    INCREMENT = "increment"
    CREATE_FILE = "create_file"


@dataclass(frozen=True, slots=True)
class WorldEditOperationV1:
    operation_key: str
    target_file: str
    expected_file_revision: int | None
    operation: WorldEditOperationKind
    field_path: str
    value: Any
    reason: str
    source_final_sequence_item: str

    def __post_init__(self) -> None:
        _key(self.operation_key, "operation_key")
        _relative_path(self.target_file, "target_file")
        normalized_target = self.target_file.replace("\\", "/")
        if (
            normalized_target.split("/", 1)[0]
            not in {"Characters", "Relationships", "Rules", "Locations", "Events", "Scenes"}
            or not normalized_target.casefold().endswith(".json")
        ):
            raise ContractValidationError(
                "world edit target must be a JSON record in an approved ACTIVE category"
            )
        if self.expected_file_revision is not None and (
            type(self.expected_file_revision) is not int
            or self.expected_file_revision < 0
        ):
            raise ContractValidationError("expected file revision is invalid")
        if self.operation is WorldEditOperationKind.CREATE_FILE:
            if self.expected_file_revision is not None or self.field_path != "/":
                raise ContractValidationError("create_file requires absent-file precondition and root path")
            if not isinstance(self.value, (dict, list, str)):
                raise ContractValidationError("create_file value must be JSON/text content")
        else:
            if self.expected_file_revision is None:
                raise ContractValidationError("existing-file edit requires a revision precondition")
            if not self.field_path.startswith("/"):
                raise ContractValidationError("world edit field_path must be a JSON pointer")
            if self.field_path in {"/_cera_revision", "/schema_version"}:
                raise ContractValidationError("Validator cannot edit Python-owned file metadata")
        _text(self.reason, "operation.reason", maximum=2_000)
        _key(self.source_final_sequence_item, "source_final_sequence_item")


@dataclass(frozen=True, slots=True)
class CreatedFieldLogEntryV1:
    target_file: str
    field_path: str
    value_type: str
    value: Any
    reason: str
    source_final_sequence_item: str

    def __post_init__(self) -> None:
        _relative_path(self.target_file, "created_field.target_file")
        if not self.field_path.startswith("/"):
            raise ContractValidationError("created field path must be a JSON pointer")
        if self.value_type not in {"null", "boolean", "integer", "number", "string", "array", "object"}:
            raise ContractValidationError("created field value_type is invalid")
        _text(self.reason, "created_field.reason", maximum=2_000)
        _key(self.source_final_sequence_item, "created_field.source_final_sequence_item")


@dataclass(frozen=True, slots=True)
class FinalSequenceItemV1:
    item_key: str
    planner_beat_keys: tuple[str, ...]
    realized_event: str
    valid_deepseek_additions: tuple[str, ...]
    omitted_or_contradicted_details: tuple[str, ...]
    private_state_owner_ids: tuple[str, ...]
    knowledge_changes: tuple[str, ...]
    material_changes: tuple[str, ...]
    resulting_state: str

    def __post_init__(self) -> None:
        _key(self.item_key, "final_sequence.item_key")
        if not self.planner_beat_keys:
            raise ContractValidationError("final sequence item requires Planner bindings")
        for value in self.planner_beat_keys:
            _key(value, "final_sequence.planner_beat_keys")
        _unique(self.planner_beat_keys, "final_sequence.planner_beat_keys")
        for field in ("realized_event", "resulting_state"):
            _text(getattr(self, field), f"final_sequence.{field}", maximum=8_000)
        for field in (
            "valid_deepseek_additions",
            "omitted_or_contradicted_details",
            "knowledge_changes",
            "material_changes",
        ):
            values = getattr(self, field)
            for value in values:
                _text(value, f"final_sequence.{field}", maximum=4_000)
            _unique(values, f"final_sequence.{field}")
        for value in self.private_state_owner_ids:
            _identity(value, "final_sequence.private_state_owner_ids")
        _unique(self.private_state_owner_ids, "final_sequence.private_state_owner_ids")


@dataclass(frozen=True, slots=True)
class FinalSequenceV1:
    SCHEMA_VERSION: ClassVar[str] = "cera.complete_final_sequence.v1"

    schema_version: str
    sequence_id: str
    accepted_turn_id: str
    items: tuple[FinalSequenceItemV1, ...]
    final_stop_state: str

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("complete final sequence schema changed")
        _identity(self.sequence_id, "final_sequence.sequence_id")
        _identity(self.accepted_turn_id, "final_sequence.accepted_turn_id")
        if not self.items:
            raise ContractValidationError("complete final sequence cannot be empty")
        _unique(tuple(value.item_key for value in self.items), "final sequence items")
        _text(self.final_stop_state, "final_sequence.final_stop_state", maximum=4_000)

    @property
    def sequence_sha256(self) -> str:
        return domain_sha256(self.SCHEMA_VERSION, self)


@dataclass(frozen=True, slots=True)
class EventRecordCandidateV1:
    event_id: str
    accepted_turn_id: str
    scene_id: str
    participant_ids: tuple[str, ...]
    summary: str
    final_sequence_item_keys: tuple[str, ...]

    def __post_init__(self) -> None:
        for field in ("event_id", "accepted_turn_id", "scene_id"):
            _identity(getattr(self, field), field)
        for value in self.participant_ids:
            _identity(value, "participant_ids")
        _unique(self.participant_ids, "participant_ids")
        if not self.final_sequence_item_keys:
            raise ContractValidationError("event record requires final sequence items")
        for value in self.final_sequence_item_keys:
            _key(value, "final_sequence_item_keys")
        _unique(self.final_sequence_item_keys, "final_sequence_item_keys")
        _text(self.summary, "event.summary", maximum=16_000)


@dataclass(frozen=True, slots=True)
class AcceptedTurnPairV1:
    accepted_turn_id: str
    user_message: str
    complete_final_sequence: FinalSequenceV1

    def __post_init__(self) -> None:
        _identity(self.accepted_turn_id, "accepted_turn_id")
        _text(self.user_message, "user_message", maximum=64_000)
        if self.complete_final_sequence.accepted_turn_id != self.accepted_turn_id:
            raise ContractValidationError("accepted pair sequence changed turn identity")


@dataclass(frozen=True, slots=True)
class SceneSummaryV1:
    SCHEMA_VERSION: ClassVar[str] = "cera.scene_summary.v1"

    schema_version: str
    summary_id: str
    completed_scene_id: str
    accepted_turn_ids: tuple[str, ...]
    shortest_complete_summary: str
    last_five_exact_pairs: tuple[AcceptedTurnPairV1, ...]
    ending_state: str
    transition_context: str

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("scene summary schema changed")
        _identity(self.summary_id, "summary_id")
        _identity(self.completed_scene_id, "completed_scene_id")
        if not self.accepted_turn_ids:
            raise ContractValidationError("scene summary requires accepted turns")
        for value in self.accepted_turn_ids:
            _identity(value, "accepted_turn_ids")
        _unique(self.accepted_turn_ids, "accepted_turn_ids")
        expected_tail = self.accepted_turn_ids[-5:]
        actual_tail = tuple(value.accepted_turn_id for value in self.last_five_exact_pairs)
        if actual_tail != expected_tail:
            raise ContractValidationError("scene summary exact-pair tail is incomplete or out of order")
        for field in ("shortest_complete_summary", "ending_state", "transition_context"):
            _text(getattr(self, field), field, maximum=32_000)


@dataclass(frozen=True, slots=True)
class ValidatorFinalizationPackageV1:
    SCHEMA_VERSION: ClassVar[str] = "cera.validator_finalization_package.v1"
    MAX_EDIT_OPERATIONS: ClassVar[int] = 100

    schema_version: str
    package_id: str
    world_id: str
    branch_id: str
    task_mode: ValidatorTaskMode
    semantic_status: ValidatorSemanticStatus
    complete_final_sequence: FinalSequenceV1 | None
    creator_review: CreatorReviewAssessment | None
    world_edit_operations: tuple[WorldEditOperationV1, ...]
    created_field_log: tuple[CreatedFieldLogEntryV1, ...]
    event_record: EventRecordCandidateV1 | None
    optional_scene_summary: SceneSummaryV1 | None

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("Validator package schema changed")
        for field in ("package_id", "world_id", "branch_id"):
            _identity(getattr(self, field), field)
        if len(self.world_edit_operations) > self.MAX_EDIT_OPERATIONS:
            raise ContractValidationError("Validator edit operation ceiling exceeded")
        _unique(tuple(value.operation_key for value in self.world_edit_operations), "edit operation keys")
        if self.task_mode is ValidatorTaskMode.FINALIZE_TURN:
            if self.complete_final_sequence is None or self.creator_review is None or self.event_record is None:
                raise ContractValidationError("turn finalization package is incomplete")
            if self.optional_scene_summary is not None:
                raise ContractValidationError("turn finalization cannot create a scene summary")
        else:
            if self.optional_scene_summary is None:
                raise ContractValidationError("scene-summary task requires a summary")
            if any((self.complete_final_sequence, self.creator_review, self.event_record)):
                raise ContractValidationError("scene-summary task cannot finalize a turn")
            if self.world_edit_operations or self.created_field_log:
                raise ContractValidationError("scene-summary task cannot edit world state")
        created_pairs = {(value.target_file, value.field_path) for value in self.created_field_log}
        for operation in self.world_edit_operations:
            if operation.operation in {
                WorldEditOperationKind.ADD,
                WorldEditOperationKind.CREATE_FILE,
            } and (
                operation.target_file,
                operation.field_path,
            ) not in created_pairs:
                raise ContractValidationError("new semantic field is missing from created_field_log")
        for entry in self.created_field_log:
            matches = tuple(
                value
                for value in self.world_edit_operations
                if value.operation in {
                    WorldEditOperationKind.ADD,
                    WorldEditOperationKind.CREATE_FILE,
                }
                and value.target_file == entry.target_file
                and value.field_path == entry.field_path
            )
            if len(matches) != 1:
                raise ContractValidationError("created_field_log has no unique add operation")
            operation = matches[0]
            if operation.value != entry.value or json_value_type(entry.value) != entry.value_type:
                raise ContractValidationError("created_field_log changed the proposed value")
            if (
                operation.reason != entry.reason
                or operation.source_final_sequence_item
                != entry.source_final_sequence_item
            ):
                raise ContractValidationError("created_field_log changed edit provenance")
        if self.task_mode is ValidatorTaskMode.FINALIZE_TURN:
            assert self.complete_final_sequence is not None
            assert self.event_record is not None
            if self.event_record.accepted_turn_id != self.complete_final_sequence.accepted_turn_id:
                raise ContractValidationError("event record changed accepted-turn identity")
            item_keys = {value.item_key for value in self.complete_final_sequence.items}
            if set(self.event_record.final_sequence_item_keys) != item_keys:
                raise ContractValidationError("event record does not cover the complete final sequence")
            for operation in self.world_edit_operations:
                if operation.source_final_sequence_item not in item_keys:
                    raise ContractValidationError("world edit cites an unknown final-sequence item")

    @property
    def package_sha256(self) -> str:
        return domain_sha256(self.SCHEMA_VERSION, self)

    def permits_disposable_acceptance(self, action: CreatorReviewAction) -> bool:
        from cera.creator_review.models import (
            CreatorReviewSeverity,
            PublicationEligibility,
        )

        return (
            action in {CreatorReviewAction.ACCEPT, CreatorReviewAction.FALSE_POSITIVE}
            and self.task_mode is ValidatorTaskMode.FINALIZE_TURN
            and self.semantic_status is ValidatorSemanticStatus.ACCEPTED
            and self.creator_review is not None
            and self.creator_review.severity is CreatorReviewSeverity.GOOD
            and self.creator_review.publication_eligibility
            is PublicationEligibility.ACCEPT_ALLOWED
        )


@dataclass(frozen=True, slots=True)
class ValidatorRouteV1:
    planner_model: str
    planner_effort: str
    validator_model: str
    validator_effort: str
    fast_enabled: bool = False

    def __post_init__(self) -> None:
        for field in ("planner_model", "planner_effort", "validator_model", "validator_effort"):
            _text(getattr(self, field), field, maximum=96)
        if self.fast_enabled:
            raise ContractValidationError("continuous Validator Fast mode is prohibited")
        ceiling = {"gpt-5.6-terra": 1, "gpt-5.6-sol": 2}
        if ceiling.get(self.validator_model, 99) > ceiling["gpt-5.6-sol"]:
            raise ContractValidationError("Validator exceeds the Sol-medium quality ceiling")
        if self.validator_model == "gpt-5.6-sol" and self.validator_effort != "medium":
            raise ContractValidationError("Sol Validator is fixed to medium")


def validator_route_for(planner_model: str, planner_effort: str) -> ValidatorRouteV1:
    mapping = {
        ("gpt-5.6-sol", "xhigh"): ("gpt-5.6-sol", "medium"),
        ("gpt-5.6-sol", "medium"): ("gpt-5.6-terra", "high"),
    }
    try:
        model, effort = mapping[(planner_model, planner_effort)]
    except KeyError as exc:
        raise ContractValidationError(
            "unsupported continuous Planner-to-Validator route mapping"
        ) from exc
    return ValidatorRouteV1(
        planner_model=planner_model,
        planner_effort=planner_effort,
        validator_model=model,
        validator_effort=effort,
        fast_enabled=False,
    )


@dataclass(frozen=True, slots=True)
class PromptComponentUsageV1:
    component: str
    byte_count: int
    estimated_tokens: int

    def __post_init__(self) -> None:
        _key(self.component, "prompt component")
        if min(self.byte_count, self.estimated_tokens) < 0:
            raise ContractValidationError("prompt component usage cannot be negative")


def json_value_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    raise ContractValidationError("world edit value is not JSON-compatible")


def package_identity_payload(package: ValidatorFinalizationPackageV1) -> Mapping[str, Any]:
    return {
        "package_sha256": package.package_sha256,
        "world_id": package.world_id,
        "branch_id": package.branch_id,
        "task_mode": package.task_mode.value,
        "semantic_status": package.semantic_status.value,
        "operation_count": len(package.world_edit_operations),
        "payload_sha256": canonical_sha256(package),
    }
