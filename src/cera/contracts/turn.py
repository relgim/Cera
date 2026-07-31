"""Turn intake and source-ledger contracts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar

from cera.ids import IdKind, TypedId
from cera.schema import require_schema

from ._validation import kind, non_empty, optional_kind, sha256, unique_ids


class SourceUnitClassification(StrEnum):
    MESSAGE = "message"
    INSTRUCTION = "instruction"
    EVENT = "event"
    CONSTRAINT = "constraint"
    CONTEXT = "context"


class SceneDepthMode(StrEnum):
    """Creator-selected scope obligation for the current visible reply."""

    OFF = "off"
    SHORT = "short"
    AUTO = "auto"
    MEDIUM = "medium"
    LONG = "long"
    EPIC = "epic"


class AdultRenderingMode(StrEnum):
    """Creator-selected prose rendering preference, independent of route authority."""

    OFF = "off"
    ON = "on"
    EX = "ex"


def build_scene_development_contract(mode: SceneDepthMode) -> dict[str, object]:
    """Return one provider-neutral, Python-owned scope contract."""

    policies = {
        SceneDepthMode.OFF: {
            "scope_obligation": "compact_consequential_exchange",
            "instruction": (
                "Use the smallest complete NPC-controlled response. Do not add a "
                "wider follow-on sequence unless required for immediate coherence."
            ),
        },
        SceneDepthMode.SHORT: {
            "scope_obligation": "short_complete_causal_reply",
            "instruction": (
                "Realize one concise but complete causal unit, including its immediate "
                "character consequence and a natural afterbeat. Preserve supported "
                "lead-in only when needed for coherence; do not collapse into summary."
            ),
        },
        SceneDepthMode.AUTO: {
            "scope_obligation": "adaptive_complete_causal_reply",
            "instruction": (
                "Default to a developed ordinary reply and expand to domino or "
                "multi-scene scope when the cue supports it. Use an atomic reply only "
                "when any further NPC-controlled progress would be unsupported or "
                "repetitive. A developed ordinary reply must continue beyond the lead "
                "NPC's first answer or reaction into at least one materially different "
                "supported follow-through: a changed tactic, immediate consequence, "
                "rational interruption or entrance, material-state change, or meaningful "
                "aftereffect. Descriptive elaboration of the same action does not satisfy "
                "that obligation. Cover every causally distinct supported NPC-controlled "
                "development needed for the selected reply shape."
            ),
        },
        SceneDepthMode.MEDIUM: {
            "scope_obligation": "developed_current_scene",
            "instruction": (
                "Realize a substantively developed current-scene sequence: establish "
                "the useful lead-in, carry the central interaction through materially "
                "distinct NPC-controlled consequences, and include a meaningful "
                "afterbeat. Expand with causal content rather than repetition, and use "
                "multiple blocks whenever the supported scene cannot be complete as a "
                "single exchange."
            ),
        },
        SceneDepthMode.LONG: {
            "scope_obligation": "full_supported_causal_chain",
            "instruction": (
                "Affirmatively cover the complete supported current-scene causal chain, "
                "including meaningful NPC-controlled follow-on and mini-scene progression, "
                "then stop at the first natural handoff where further progression would "
                "require, assume, or pre-empt the protected user's choice."
            ),
        },
        SceneDepthMode.EPIC: {
            "scope_obligation": "all_materially_distinct_supported_progression",
            "instruction": (
                "Cover every materially distinct, supported NPC-controlled consequence "
                "across available current-scene phases or mini-scenes, then stop at the "
                "first natural handoff where further progression would require, assume, "
                "or pre-empt the protected user's choice."
            ),
        },
    }
    return {
        "mode": mode.value,
        **policies[mode],
        "binding_for_current_reply": True,
        "short_source_is_not_a_length_limit": True,
        "numeric_beat_quota_forbidden": True,
        "padding_and_repetition_forbidden": True,
        "protected_user_invention_forbidden": True,
    }


@dataclass(frozen=True, slots=True)
class SourceUnit:
    source_unit_id: TypedId
    classification: SourceUnitClassification
    text_ref: TypedId
    sha256: str

    def __post_init__(self) -> None:
        kind(self.source_unit_id, IdKind.SOURCE_UNIT, "source_unit_id")
        kind(self.text_ref, IdKind.PROTECTED_SOURCE_SEGMENT, "text_ref")
        sha256(self.sha256, "sha256")


@dataclass(frozen=True, slots=True)
class TurnRequest:
    SCHEMA_VERSION: ClassVar[str] = "cera.turn_request.v1"

    schema_version: str
    world_id: TypedId
    request_id: TypedId
    session_id: TypedId
    branch_id: TypedId
    parent_artifact_id: TypedId | None
    generation_id: TypedId
    snapshot_token: TypedId
    genesis_revision_id: TypedId
    protected_user_id: TypedId
    raw_source_ref: TypedId
    source_sha256: str
    source_units: tuple[SourceUnit, ...]
    requested_route_hints: tuple[str, ...]
    idempotency_key: str

    def __post_init__(self) -> None:
        require_schema(self.schema_version, self.SCHEMA_VERSION, type(self).__name__)
        kind(self.world_id, IdKind.WORLD, "world_id")
        kind(self.request_id, IdKind.REQUEST, "request_id")
        kind(self.session_id, IdKind.SESSION, "session_id")
        kind(self.branch_id, IdKind.BRANCH, "branch_id")
        optional_kind(self.parent_artifact_id, IdKind.ARTIFACT, "parent_artifact_id")
        kind(self.generation_id, IdKind.GENERATION, "generation_id")
        kind(self.snapshot_token, IdKind.SNAPSHOT, "snapshot_token")
        kind(self.genesis_revision_id, IdKind.GENESIS_REVISION, "genesis_revision_id")
        kind(self.protected_user_id, IdKind.CHARACTER, "protected_user_id")
        kind(self.raw_source_ref, IdKind.PROTECTED_SOURCE, "raw_source_ref")
        sha256(self.source_sha256, "source_sha256")
        if not self.source_units:
            raise ValueError("source_units must not be empty")
        unique_ids((unit.source_unit_id for unit in self.source_units), "source_units")
        non_empty(self.idempotency_key, "idempotency_key")
