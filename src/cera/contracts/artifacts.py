"""Validated story artifact contracts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from cera.ids import IdKind, TypedId
from cera.schema import require_schema
from cera.serialization import text_sha256

from ._validation import kind, non_empty, optional_kind, sha256, unique_ids


@dataclass(frozen=True, slots=True)
class AcceptedStoryArtifact:
    """Final validated presentation-neutral prose before UI rendering."""
    SCHEMA_VERSION: ClassVar[str] = "cera.accepted_story_artifact.v1"

    schema_version: str
    artifact_id: TypedId
    branch_id: TypedId
    generation_id: TypedId
    parent_artifact_id: TypedId | None
    source_id: TypedId
    decision_id: TypedId
    accepted_prose: str
    prose_sha256: str
    responding_npc_ids: tuple[TypedId, ...]
    realized_beat_ids: tuple[TypedId, ...]
    validation_receipt_id: TypedId
    transaction_id: TypedId
    status: str

    def __post_init__(self) -> None:
        require_schema(self.schema_version, self.SCHEMA_VERSION, type(self).__name__)
        kind(self.artifact_id, IdKind.ARTIFACT, "artifact_id")
        kind(self.branch_id, IdKind.BRANCH, "branch_id")
        kind(self.generation_id, IdKind.GENERATION, "generation_id")
        optional_kind(self.parent_artifact_id, IdKind.ARTIFACT, "parent_artifact_id")
        kind(self.source_id, IdKind.SOURCE, "source_id")
        kind(self.decision_id, IdKind.DECISION, "decision_id")
        kind(self.validation_receipt_id, IdKind.VALIDATION, "validation_receipt_id")
        kind(self.transaction_id, IdKind.TRANSACTION, "transaction_id")
        non_empty(self.accepted_prose, "accepted_prose")
        sha256(self.prose_sha256, "prose_sha256")
        if text_sha256(self.accepted_prose) != self.prose_sha256:
            raise ValueError("prose_sha256 does not match accepted_prose")
        if self.status != "accepted":
            raise ValueError("accepted artifact status must be accepted")
        for npc_id in self.responding_npc_ids:
            kind(npc_id, IdKind.CHARACTER, "responding_npc_ids")
        for beat_id in self.realized_beat_ids:
            kind(beat_id, IdKind.BEAT, "realized_beat_ids")
        unique_ids(self.responding_npc_ids, "responding_npc_ids")
        unique_ids(self.realized_beat_ids, "realized_beat_ids")
