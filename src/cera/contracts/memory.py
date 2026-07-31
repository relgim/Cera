"""Character-owned memory contracts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar

from cera.ids import IdKind, TypedId
from cera.schema import require_schema

from ._validation import kind, non_empty, sha256, unique_ids, unique_text
from .reasoning import Certainty, KnowledgeRoute


class MemoryPrivacy(StrEnum):
    OWNER_PRIVATE = "owner_private"
    SHARED = "shared"
    PUBLIC = "public"


class SubjectiveKind(StrEnum):
    FEAR = "fear"
    BETRAYAL = "betrayal"
    SHAME = "shame"
    ANGER = "anger"
    SELF_BLAME = "self_blame"
    OTHER = "other"


@dataclass(frozen=True, slots=True)
class SubjectiveInterpretation:
    kind: SubjectiveKind
    statement: str
    certainty: Certainty

    def __post_init__(self) -> None:
        non_empty(self.statement, "statement")
        if self.certainty is Certainty.ESTABLISHED:
            raise ValueError("subjective interpretation cannot be objective established fact")


@dataclass(frozen=True, slots=True)
class CharacterMemoryRecord:
    SCHEMA_VERSION: ClassVar[str] = "cera.character_memory.v1"

    schema_version: str
    memory_id: TypedId
    owner_id: TypedId
    branch_id: TypedId
    privacy: MemoryPrivacy
    title: str
    abstract: str
    objective_event_refs: tuple[TypedId, ...]
    perceived_facts: tuple[str, ...]
    subjective_interpretations: tuple[SubjectiveInterpretation, ...]
    unknowns: tuple[str, ...]
    knowledge_route: KnowledgeRoute
    retrieval_tags: tuple[str, ...]
    trigger_cues: tuple[str, ...]
    development_history_refs: tuple[TypedId, ...]
    supersedes: tuple[TypedId, ...]
    genesis_effect: str

    def __post_init__(self) -> None:
        require_schema(self.schema_version, self.SCHEMA_VERSION, type(self).__name__)
        kind(self.memory_id, IdKind.MEMORY, "memory_id")
        kind(self.owner_id, IdKind.CHARACTER, "owner_id")
        kind(self.branch_id, IdKind.BRANCH, "branch_id")
        non_empty(self.title, "title")
        non_empty(self.abstract, "abstract")
        for event_id in self.objective_event_refs:
            kind(event_id, IdKind.EVENT, "objective_event_refs")
        for development_id in self.development_history_refs:
            kind(development_id, IdKind.DEVELOPMENT, "development_history_refs")
        for memory_id in self.supersedes:
            kind(memory_id, IdKind.MEMORY, "supersedes")
        unique_ids(self.objective_event_refs, "objective_event_refs")
        unique_ids(self.development_history_refs, "development_history_refs")
        unique_ids(self.supersedes, "supersedes")
        unique_text(self.retrieval_tags, "retrieval_tags")
        unique_text(self.trigger_cues, "trigger_cues")
        if self.genesis_effect != "none":
            raise ValueError("runtime memory cannot modify Genesis")


@dataclass(frozen=True, slots=True)
class CommitReceipt:
    SCHEMA_VERSION: ClassVar[str] = "cera.commit_receipt.v2"

    schema_version: str
    commit_id: TypedId
    transaction_id: TypedId
    branch_id: TypedId
    source_id: TypedId
    source_sha256: str
    expected_previous_artifact_id: TypedId | None
    new_artifact_id: TypedId
    new_artifact_sha256: str
    generation_before: int
    generation_after: int
    inserted_record_ids: tuple[TypedId, ...]
    superseded_record_ids: tuple[TypedId, ...]
    validation_receipt_ids: tuple[TypedId, ...]
    lookup_receipt_ids: tuple[TypedId, ...]
    provider_receipt_ids: tuple[TypedId, ...]
    external_receipt_ids: tuple[TypedId, ...]
    transaction_sha256: str
    outcome: str

    def __post_init__(self) -> None:
        require_schema(self.schema_version, self.SCHEMA_VERSION, type(self).__name__)
        kind(self.commit_id, IdKind.COMMIT, "commit_id")
        kind(self.transaction_id, IdKind.TRANSACTION, "transaction_id")
        kind(self.branch_id, IdKind.BRANCH, "branch_id")
        kind(self.source_id, IdKind.SOURCE, "source_id")
        sha256(self.source_sha256, "source_sha256")
        if self.expected_previous_artifact_id is not None:
            kind(
                self.expected_previous_artifact_id,
                IdKind.ARTIFACT,
                "expected_previous_artifact_id",
            )
        kind(self.new_artifact_id, IdKind.ARTIFACT, "new_artifact_id")
        sha256(self.new_artifact_sha256, "new_artifact_sha256")
        if self.generation_before < 0 or self.generation_after != self.generation_before + 1:
            raise ValueError("commit receipt generation transition must advance by one")
        unique_ids(self.inserted_record_ids, "inserted_record_ids")
        unique_ids(self.superseded_record_ids, "superseded_record_ids")
        for receipt_id in self.validation_receipt_ids:
            if receipt_id.kind not in (
                IdKind.VALIDATION,
                IdKind.REALIZATION_VERIFICATION,
            ):
                raise ValueError(
                    "validation_receipt_ids requires validation or "
                    "realization_verification"
                )
        for receipt_id in self.lookup_receipt_ids:
            kind(receipt_id, IdKind.LOOKUP_RECEIPT, "lookup_receipt_ids")
        for receipt_id in self.provider_receipt_ids:
            kind(receipt_id, IdKind.PROVIDER_RECEIPT, "provider_receipt_ids")
        for receipt_id in self.external_receipt_ids:
            kind(receipt_id, IdKind.EXTERNAL_RECEIPT, "external_receipt_ids")
        unique_ids(self.validation_receipt_ids, "validation_receipt_ids")
        unique_ids(self.lookup_receipt_ids, "lookup_receipt_ids")
        unique_ids(self.provider_receipt_ids, "provider_receipt_ids")
        unique_ids(self.external_receipt_ids, "external_receipt_ids")
        sha256(self.transaction_sha256, "transaction_sha256")
        if self.outcome != "committed":
            raise ValueError("commit receipt outcome must be committed")
