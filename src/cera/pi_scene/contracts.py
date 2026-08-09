"""Versioned contracts for the lean Planner/Pi/Recorder route.

The model-visible result is intentionally small.  Python-owned identity,
branch, acceptance, and recording custody live in these envelopes and are
never requested from DeepSeek.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, ClassVar

from cera.errors import ContractValidationError
from cera.serialization import canonical_json, canonical_sha256, re_is_sha256, text_sha256


def _required(value: str, field_name: str) -> str:
    if type(value) is not str or not value.strip():
        raise ContractValidationError(f"{field_name} must be non-empty")
    return value


def _sha(value: str, field_name: str) -> str:
    if type(value) is not str or not re_is_sha256(value):
        raise ContractValidationError(f"{field_name} must be a SHA-256 value")
    return value


def _unique_nonempty(values: tuple[str, ...], field_name: str) -> tuple[str, ...]:
    if (
        type(values) is not tuple
        or any(type(value) is not str or not value.strip() for value in values)
        or len(values) != len(set(values))
    ):
        raise ContractValidationError(f"{field_name} must contain unique non-empty values")
    return values


class SceneRoute(StrEnum):
    ORDINARY = "ordinary"
    ADULT = "adult"


class RecordingStatus(StrEnum):
    PROJECTION_PENDING = "projection_pending"
    PENDING_REPAIR = "pending_repair"
    COMPLETE = "complete"


@dataclass(frozen=True, slots=True)
class TedWarningV1:
    SCHEMA_VERSION: ClassVar[str] = "cera.pi_scene.ted_warning.v1"

    schema_version: str
    warning_code: str
    excerpt: str

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("Ted warning schema changed")
        _required(self.warning_code, "warning_code")
        _required(self.excerpt, "warning excerpt")
        if len(self.excerpt) > 200:
            raise ContractValidationError("Ted warning excerpt is too large")


_TED_WARNING_PATTERNS = (
    (
        "possible_invented_ted_dialogue",
        re.compile(
            r"(?:"
            r"\bTed\s+(?:said|asked|replied|answered|whispered|murmured|"
            r"shouted|called|told)\b"
            r"|\bTed\b[\s\S]{0,320}?[\"“][^\"”\n]{1,240}[\"”]\s*"
            r"(?:he\s+)?(?:said|asked|replied|answered|whispered|murmured|"
            r"shouted|called)\b"
            r")",
            re.IGNORECASE,
        ),
    ),
    (
        "possible_invented_ted_private_state",
        re.compile(
            r"\bTed\s+(?:thought|felt|wondered|realized|wanted|hoped|feared|"
            r"decided|remembered|believed)\b",
            re.IGNORECASE,
        ),
    ),
)


def advisory_ted_warnings(story_text: str) -> tuple[TedWarningV1, ...]:
    """Return advisory-only flags; never authorize or block narrative meaning."""

    warnings: list[TedWarningV1] = []
    for code, pattern in _TED_WARNING_PATTERNS:
        match = pattern.search(story_text)
        if match is None:
            continue
        start = max(0, match.start() - 70)
        end = min(len(story_text), match.end() + 70)
        warnings.append(
            TedWarningV1(
                schema_version=TedWarningV1.SCHEMA_VERSION,
                warning_code=code,
                excerpt=story_text[start:end].strip()[:200],
            )
        )
    return tuple(warnings)


@dataclass(frozen=True, slots=True)
class PiWriterReceiptV1:
    """Privacy-safe receipt for every model invocation made by one Pi turn."""

    SCHEMA_VERSION: ClassVar[str] = "cera.pi_scene.writer_receipt.v1"

    schema_version: str
    route: SceneRoute
    provider: str
    model: str
    pi_version: str
    session_id_sha256: str
    parent_session_id_sha256: str | None
    request_sha256: str
    output_sha256: str
    provider_operations: int
    tool_call_count: int
    failed_tool_call_count: int
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    reasoning_tokens: int
    duration_ms: int
    finish_status: str
    rehydrated: bool

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("Pi Writer receipt schema changed")
        for field_name in ("provider", "model", "pi_version", "finish_status"):
            _required(getattr(self, field_name), field_name)
        for field_name in (
            "session_id_sha256",
            "request_sha256",
            "output_sha256",
        ):
            _sha(getattr(self, field_name), field_name)
        if self.parent_session_id_sha256 is not None:
            _sha(self.parent_session_id_sha256, "parent_session_id_sha256")
        for field_name in (
            "provider_operations",
            "tool_call_count",
            "failed_tool_call_count",
            "input_tokens",
            "cached_input_tokens",
            "output_tokens",
            "reasoning_tokens",
            "duration_ms",
        ):
            value = getattr(self, field_name)
            if type(value) is not int or value < 0:
                raise ContractValidationError(f"{field_name} must be a non-negative integer")
        if self.provider_operations < 1:
            raise ContractValidationError("Pi Writer receipt requires a provider operation")
        if self.cached_input_tokens > self.input_tokens:
            raise ContractValidationError("cached input exceeds total input")
        if type(self.rehydrated) is not bool:
            raise ContractValidationError("rehydrated must be boolean")


@dataclass(frozen=True, slots=True)
class LeanCandidateV1:
    """One provisional visible result; no Validator or Reader field is present."""

    SCHEMA_VERSION: ClassVar[str] = "cera.pi_scene.candidate.v1"

    schema_version: str
    request_id: str
    candidate_id: str
    turn_id: str
    world_id: str
    branch_id: str
    scene_id: str
    generation: int
    parent_accepted_turn_id: str | None
    accepted_head_before_sha256: str | None
    exact_user_source: str
    exact_user_source_sha256: str
    route: SceneRoute
    primary_authority_kind: str
    primary_authority_json: str
    primary_authority_sha256: str
    writer_view_manifest_sha256: str
    story_text: str
    story_text_sha256: str
    writer_receipt: PiWriterReceiptV1
    warnings: tuple[TedWarningV1, ...] = ()

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("lean candidate schema changed")
        for field_name in (
            "request_id",
            "candidate_id",
            "turn_id",
            "world_id",
            "branch_id",
            "scene_id",
            "exact_user_source",
            "primary_authority_kind",
            "primary_authority_json",
            "story_text",
        ):
            _required(getattr(self, field_name), field_name)
        if type(self.generation) is not int or self.generation < 1:
            raise ContractValidationError("candidate generation must be positive")
        if self.route is SceneRoute.ORDINARY and self.primary_authority_kind not in {
            "codex_sequence",
            "codex_cognition_plan",
        }:
            raise ContractValidationError("ordinary candidate requires Codex logic authority")
        if self.route is SceneRoute.ADULT and self.primary_authority_kind != "adult_handoff":
            raise ContractValidationError("adult candidate requires adult handoff authority")
        try:
            parsed_authority = json.loads(self.primary_authority_json)
        except json.JSONDecodeError as exc:
            raise ContractValidationError("primary authority is not valid JSON") from exc
        if not isinstance(parsed_authority, dict):
            raise ContractValidationError("primary authority must be a JSON object")
        if canonical_json(parsed_authority) != self.primary_authority_json:
            raise ContractValidationError("primary authority JSON is not canonical")
        if text_sha256(self.exact_user_source) != self.exact_user_source_sha256:
            raise ContractValidationError("candidate source binding changed")
        if text_sha256(self.primary_authority_json) != self.primary_authority_sha256:
            raise ContractValidationError("candidate primary authority binding changed")
        if text_sha256(self.story_text) != self.story_text_sha256:
            raise ContractValidationError("candidate prose binding changed")
        for field_name in (
            "exact_user_source_sha256",
            "primary_authority_sha256",
            "writer_view_manifest_sha256",
            "story_text_sha256",
        ):
            _sha(getattr(self, field_name), field_name)
        if self.accepted_head_before_sha256 is not None:
            _sha(self.accepted_head_before_sha256, "accepted_head_before_sha256")
        if self.writer_receipt.route is not self.route:
            raise ContractValidationError("Writer receipt route differs from candidate")
        if self.writer_receipt.output_sha256 != self.story_text_sha256:
            raise ContractValidationError("Writer receipt output differs from candidate prose")
        if len(self.warnings) != len({value.warning_code for value in self.warnings}):
            raise ContractValidationError("candidate contains duplicate warning codes")

    @property
    def candidate_sha256(self) -> str:
        return canonical_sha256(self)


@dataclass(frozen=True, slots=True)
class LeanRunResultV1:
    """A successful lean generation result without qualification-role fields."""

    SCHEMA_VERSION: ClassVar[str] = "cera.pi_scene.run_result.v1"

    schema_version: str
    candidate: LeanCandidateV1
    planner_provider_operations: int
    writer_provider_operations: int
    regenerated_from_candidate_id: str | None = None
    replanned_from_candidate_id: str | None = None
    repaired_from_candidate_id: str | None = None

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("lean run-result schema changed")
        if (
            type(self.planner_provider_operations) is not int
            or self.planner_provider_operations < 0
        ):
            raise ContractValidationError("Planner operation count is invalid")
        if type(self.writer_provider_operations) is not int or self.writer_provider_operations < 1:
            raise ContractValidationError("Writer operation count is invalid")
        if self.writer_provider_operations != self.candidate.writer_receipt.provider_operations:
            raise ContractValidationError("Writer operation count differs from its receipt")
        if self.candidate.route is SceneRoute.ADULT and self.planner_provider_operations != 0:
            raise ContractValidationError("initial adult route must not call the Codex Planner")
        lineage = (
            self.regenerated_from_candidate_id,
            self.replanned_from_candidate_id,
            self.repaired_from_candidate_id,
        )
        if sum(value is not None for value in lineage) > 1:
            raise ContractValidationError(
                "a result cannot be regenerated, replanned, and repaired together"
            )


@dataclass(frozen=True, slots=True)
class LeanAcceptedTurnReceiptV1:
    """Immutable phase-one authority written before optional Recorder work."""

    SCHEMA_VERSION: ClassVar[str] = "cera.pi_scene.accepted_turn_receipt.v1"

    schema_version: str
    accepted_turn_id: str
    parent_accepted_turn_id: str | None
    parent_accepted_head_sha256: str | None
    world_id: str
    branch_id: str
    scene_id: str
    generation: int
    route: SceneRoute
    exact_user_source: str
    exact_user_source_sha256: str
    exact_accepted_prose: str
    exact_accepted_prose_sha256: str
    primary_authority_kind: str
    primary_authority_json: str
    primary_authority_sha256: str
    writer_view_manifest_sha256: str
    writer_receipt: PiWriterReceiptV1
    creator_action: str
    warnings: tuple[TedWarningV1, ...]
    initial_recording_status: RecordingStatus
    candidate_sha256: str

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("accepted-turn receipt schema changed")
        if self.creator_action not in {
            "accept",
            "automatic_accept",
            "provisional_accept",
        }:
            raise ContractValidationError("accepted-turn receipt action is invalid")
        if self.initial_recording_status is not RecordingStatus.PROJECTION_PENDING:
            raise ContractValidationError("accepted turn must begin projection-pending")
        for field_name in (
            "accepted_turn_id",
            "world_id",
            "branch_id",
            "scene_id",
            "exact_user_source",
            "exact_accepted_prose",
            "primary_authority_kind",
            "primary_authority_json",
        ):
            _required(getattr(self, field_name), field_name)
        if type(self.generation) is not int or self.generation < 1:
            raise ContractValidationError("accepted generation must be positive")
        if (self.parent_accepted_turn_id is None) != (self.parent_accepted_head_sha256 is None):
            raise ContractValidationError("accepted parent identity and hash must agree")
        if self.parent_accepted_head_sha256 is not None:
            _sha(self.parent_accepted_head_sha256, "parent_accepted_head_sha256")
        if text_sha256(self.exact_user_source) != self.exact_user_source_sha256:
            raise ContractValidationError("accepted source binding changed")
        if text_sha256(self.exact_accepted_prose) != self.exact_accepted_prose_sha256:
            raise ContractValidationError("accepted prose binding changed")
        if text_sha256(self.primary_authority_json) != self.primary_authority_sha256:
            raise ContractValidationError("accepted primary authority binding changed")
        for field_name in (
            "exact_user_source_sha256",
            "exact_accepted_prose_sha256",
            "primary_authority_sha256",
            "writer_view_manifest_sha256",
            "candidate_sha256",
        ):
            _sha(getattr(self, field_name), field_name)
        if self.writer_receipt.route is not self.route:
            raise ContractValidationError("accepted Writer route changed")
        if self.writer_receipt.output_sha256 != self.exact_accepted_prose_sha256:
            raise ContractValidationError("accepted prose differs from Writer receipt")

    @property
    def receipt_sha256(self) -> str:
        return canonical_sha256(self)


@dataclass(frozen=True, slots=True)
class OrdinarySceneRecordV1:
    SCHEMA_VERSION: ClassVar[str] = "cera.pi_scene.ordinary_record.v1"

    schema_version: str
    primary_sequence_sha256: str
    realized_item_keys: tuple[str, ...]
    secondary_canon: tuple[str, ...]
    resulting_public_state: str
    relationship_changes: tuple[str, ...]
    knowledge_changes: tuple[str, ...]
    durable_changes: tuple[str, ...]
    unresolved_threads: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("ordinary record schema changed")
        _sha(self.primary_sequence_sha256, "primary_sequence_sha256")
        _unique_nonempty(self.realized_item_keys, "realized_item_keys")
        _required(self.resulting_public_state, "resulting_public_state")
        for field_name in (
            "secondary_canon",
            "relationship_changes",
            "knowledge_changes",
            "durable_changes",
            "unresolved_threads",
        ):
            _unique_nonempty(getattr(self, field_name), field_name)


@dataclass(frozen=True, slots=True)
class AdultRecordEventV1:
    event_key: str
    summary: str
    motive: str
    alternatives_considered: tuple[str, ...]
    consent_or_boundary_transition: str
    thoughts_and_feelings: tuple[str, ...]
    durable_effects: tuple[str, ...]
    knowledge_scope: tuple[str, ...]

    def __post_init__(self) -> None:
        for field_name in (
            "event_key",
            "summary",
            "motive",
            "consent_or_boundary_transition",
        ):
            _required(getattr(self, field_name), field_name)
        for field_name in (
            "alternatives_considered",
            "thoughts_and_feelings",
            "durable_effects",
            "knowledge_scope",
        ):
            _unique_nonempty(getattr(self, field_name), field_name)


@dataclass(frozen=True, slots=True)
class AdultFullRecordV1:
    SCHEMA_VERSION: ClassVar[str] = "cera.pi_scene.adult_full_record.v1"

    schema_version: str
    adult_handoff_sha256: str
    decision_path: tuple[str, ...]
    events: tuple[AdultRecordEventV1, ...]
    resulting_public_state: str
    unresolved_threads: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("adult full-record schema changed")
        _sha(self.adult_handoff_sha256, "adult_handoff_sha256")
        _unique_nonempty(self.decision_path, "decision_path")
        if not self.events:
            raise ContractValidationError("adult full record requires at least one event")
        _unique_nonempty(tuple(value.event_key for value in self.events), "adult event keys")
        _required(self.resulting_public_state, "resulting_public_state")
        _unique_nonempty(self.unresolved_threads, "unresolved_threads")


@dataclass(frozen=True, slots=True)
class AdultProjectionItemV1:
    event_key: str
    non_explicit_summary: str
    lasting_story_meaning: str

    def __post_init__(self) -> None:
        _required(self.event_key, "projection event_key")
        _required(self.non_explicit_summary, "non_explicit_summary")
        _required(self.lasting_story_meaning, "lasting_story_meaning")


@dataclass(frozen=True, slots=True)
class AdultProjectionPresenceChangeV1:
    """Non-explicit, Recorder-proposed presence change.

    The provider owns only the semantic change. Python supplies accepted-turn
    identity and custody when the projection is attached.
    """

    character_id: str
    direction: str
    effective_after_event_key: str

    def __post_init__(self) -> None:
        _required(self.character_id, "adult presence character_id")
        if not self.character_id.startswith("character:"):
            raise ContractValidationError("adult presence character_id is invalid")
        if self.direction not in {"enter", "leave"}:
            raise ContractValidationError("adult presence direction is invalid")
        _required(
            self.effective_after_event_key,
            "adult presence effective_after_event_key",
        )


@dataclass(frozen=True, slots=True)
class AdultProjectionDurableChangeV1:
    """Non-explicit durable effect with explicit knowledge ownership."""

    change_key: str
    kind: str
    subject_ids: tuple[str, ...]
    non_explicit_change: str
    target_key: str
    visibility: str
    knowledge_owner_id: str | None

    def __post_init__(self) -> None:
        for field_name in ("change_key", "non_explicit_change", "target_key"):
            _required(getattr(self, field_name), f"adult durable {field_name}")
        if self.kind not in {
            "material",
            "knowledge",
            "relationship",
            "character_development",
        }:
            raise ContractValidationError("adult durable kind is invalid")
        _unique_nonempty(self.subject_ids, "adult durable subject_ids")
        if self.visibility not in {"public", "character_private"}:
            raise ContractValidationError("adult durable visibility is invalid")
        if self.visibility == "character_private":
            _required(self.knowledge_owner_id or "", "adult durable knowledge_owner_id")
            if (
                self.knowledge_owner_id is None
                or not self.knowledge_owner_id.startswith("character:")
                or self.knowledge_owner_id not in self.subject_ids
            ):
                raise ContractValidationError(
                    "private adult durable effect requires a subject owner"
                )
        elif self.knowledge_owner_id is not None:
            raise ContractValidationError(
                "public adult durable effect cannot name a knowledge owner"
            )


@dataclass(frozen=True, slots=True)
class AdultCodexProjectionV1:
    SCHEMA_VERSION: ClassVar[str] = "cera.pi_scene.adult_codex_projection.v1"

    schema_version: str
    adult_full_record_sha256: str
    decision_path_summary: tuple[str, ...]
    items: tuple[AdultProjectionItemV1, ...]
    resulting_public_state: str
    unresolved_threads: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("adult projection schema changed")
        _sha(self.adult_full_record_sha256, "adult_full_record_sha256")
        _unique_nonempty(self.decision_path_summary, "decision_path_summary")
        if not self.items:
            raise ContractValidationError("adult projection requires at least one item")
        _unique_nonempty(tuple(value.event_key for value in self.items), "projection event keys")
        _required(self.resulting_public_state, "resulting_public_state")
        _unique_nonempty(self.unresolved_threads, "unresolved_threads")


@dataclass(frozen=True, slots=True)
class AdultCodexProjectionV2:
    """Non-explicit adult-to-Codex bridge with scoped state effects.

    ``adult_full_record_sha256`` is injected by Python. The remaining fields
    are the Recorder's non-explicit semantic proposal and contain no accepted
    receipt, turn, branch, path, or transaction custody.
    """

    SCHEMA_VERSION: ClassVar[str] = "cera.pi_scene.adult_codex_projection.v2"

    schema_version: str
    adult_full_record_sha256: str
    decision_path_summary: tuple[str, ...]
    items: tuple[AdultProjectionItemV1, ...]
    presence_changes: tuple[AdultProjectionPresenceChangeV1, ...]
    durable_effects: tuple[AdultProjectionDurableChangeV1, ...]
    resulting_public_state: str
    unresolved_threads: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("adult projection V2 schema changed")
        _sha(self.adult_full_record_sha256, "adult_full_record_sha256")
        _unique_nonempty(self.decision_path_summary, "decision_path_summary")
        if not self.items:
            raise ContractValidationError("adult projection requires at least one item")
        event_keys = tuple(value.event_key for value in self.items)
        _unique_nonempty(event_keys, "projection event keys")
        event_key_set = set(event_keys)
        for change in self.presence_changes:
            if change.effective_after_event_key not in event_key_set:
                raise ContractValidationError(
                    "adult presence change cites an unknown projection event"
                )
        if len({value.change_key for value in self.durable_effects}) != len(self.durable_effects):
            raise ContractValidationError("adult durable change keys contain duplicates")
        _required(self.resulting_public_state, "resulting_public_state")
        _unique_nonempty(self.unresolved_threads, "unresolved_threads")


@dataclass(frozen=True, slots=True)
class LeanRecordingAttemptV1:
    SCHEMA_VERSION: ClassVar[str] = "cera.pi_scene.recording_attempt.v1"

    schema_version: str
    accepted_turn_id: str
    attempt_number: int
    status: RecordingStatus
    recorder_request_sha256: str
    recorder_output_sha256: str | None
    provider_operations: int
    failure_code: str | None = None
    ordinary_record_sha256: str | None = None
    adult_full_record_sha256: str | None = None
    adult_projection_sha256: str | None = None

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("recording-attempt schema changed")
        _required(self.accepted_turn_id, "accepted_turn_id")
        if type(self.attempt_number) is not int or self.attempt_number < 0:
            raise ContractValidationError("recording attempt number must be non-negative")
        _sha(self.recorder_request_sha256, "recorder_request_sha256")
        if type(self.provider_operations) is not int or self.provider_operations < 0:
            raise ContractValidationError("Recorder provider operation count is invalid")
        for field_name in (
            "recorder_output_sha256",
            "ordinary_record_sha256",
            "adult_full_record_sha256",
            "adult_projection_sha256",
        ):
            value = getattr(self, field_name)
            if value is not None:
                _sha(value, field_name)
        if self.status is RecordingStatus.PROJECTION_PENDING:
            if self.attempt_number != 0:
                raise ContractValidationError(
                    "projection-pending recording state must be attempt zero"
                )
            if self.provider_operations != 0 or any(
                value is not None
                for value in (
                    self.recorder_output_sha256,
                    self.failure_code,
                    self.ordinary_record_sha256,
                    self.adult_full_record_sha256,
                    self.adult_projection_sha256,
                )
            ):
                raise ContractValidationError(
                    "projection-pending recording state has attempt metadata"
                )
        elif self.status is RecordingStatus.PENDING_REPAIR:
            if self.attempt_number < 1:
                raise ContractValidationError("failed recording attempt must be positive")
            _required(self.failure_code or "", "failure_code")
            if any(
                value is not None
                for value in (
                    self.ordinary_record_sha256,
                    self.adult_full_record_sha256,
                    self.adult_projection_sha256,
                )
            ):
                raise ContractValidationError("failed recording attempt cannot attach records")
        elif self.status is RecordingStatus.COMPLETE:
            if self.attempt_number < 1:
                raise ContractValidationError("complete recording attempt must be positive")
            if self.failure_code is not None or self.recorder_output_sha256 is None:
                raise ContractValidationError("complete recording attempt has failure metadata")
            ordinary = self.ordinary_record_sha256 is not None
            adult = (
                self.adult_full_record_sha256 is not None
                and self.adult_projection_sha256 is not None
            )
            if ordinary == adult:
                raise ContractValidationError(
                    "complete recording attempt must attach one route shape"
                )
        else:
            raise ContractValidationError("recording attempt status is invalid")


def primary_item_keys(primary_authority_json: str) -> tuple[str, ...]:
    """Extract exact Planner item keys without interpreting narrative meaning."""

    data = json.loads(primary_authority_json)
    values = data.get("items")
    if not isinstance(values, list):
        # Historical SequenceDraftV1 serializes the ordered sequence as `items`.
        # Reject rather than adding semantic aliases.
        raise ContractValidationError("Codex sequence does not expose ordered items")
    keys: list[str] = []
    for item in values:
        if not isinstance(item, Mapping) or not isinstance(item.get("item_key"), str):
            raise ContractValidationError("Codex sequence item key is missing")
        keys.append(item["item_key"])
    return _unique_nonempty(tuple(keys), "primary item keys")


def validate_ordinary_record(
    record: OrdinarySceneRecordV1,
    *,
    accepted: LeanAcceptedTurnReceiptV1,
) -> None:
    if accepted.route is not SceneRoute.ORDINARY:
        raise ContractValidationError("ordinary record cannot attach to adult turn")
    if record.primary_sequence_sha256 != accepted.primary_authority_sha256:
        raise ContractValidationError("Recorder rewrote the exact Codex sequence binding")
    if tuple(record.realized_item_keys) != primary_item_keys(accepted.primary_authority_json):
        raise ContractValidationError(
            "ordinary record does not preserve every Planner item in order"
        )


def validate_adult_records(
    full: AdultFullRecordV1,
    projection: AdultCodexProjectionV1 | AdultCodexProjectionV2,
    *,
    accepted: LeanAcceptedTurnReceiptV1,
) -> None:
    if accepted.route is not SceneRoute.ADULT:
        raise ContractValidationError("adult record cannot attach to ordinary turn")
    if full.adult_handoff_sha256 != accepted.primary_authority_sha256:
        raise ContractValidationError("adult record changed handoff authority")
    if projection.adult_full_record_sha256 != canonical_sha256(full):
        raise ContractValidationError("adult projection does not bind the full record")
    if tuple(value.event_key for value in projection.items) != tuple(
        value.event_key for value in full.events
    ):
        raise ContractValidationError(
            "adult projection does not preserve every full-record event in order"
        )


def canonical_authority(value: Mapping[str, Any]) -> tuple[str, str]:
    text = canonical_json(dict(value))
    return text, text_sha256(text)
