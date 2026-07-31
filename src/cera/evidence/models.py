"""Snapshot-bound evidence contracts for runtime reasoning tools."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import json
from typing import ClassVar

from cera.contracts import (
    Certainty,
    EvidenceAuthority,
    EvidenceRecordType,
    KnowledgeRoute,
    SupersessionStatus,
    TruthStatus,
    Visibility,
)
from cera.errors import ContractValidationError
from cera.ids import AUTHORITY_RECORD_ID_KINDS, IdKind, TypedId, require_kind
from cera.schema import require_schema
from cera.serialization import canonical_json, domain_sha256


class EvidenceRequesterRole(StrEnum):
    CHARACTER = "character"
    SYSTEM_REASONER = "system_reasoner"


class EvidenceWorldMode(StrEnum):
    REAL = "real"
    SYNTHETIC_FIXTURE = "synthetic_fixture"


class EvidenceEpistemicClass(StrEnum):
    OBJECTIVE_FACT = "objective_fact"
    CONSCIOUS_BELIEF = "conscious_belief"
    PRIVATE_FEELING = "private_feeling"
    PRIVATE_BELIEF = "private_belief"
    ALLEGATION = "allegation"
    UNRESOLVED_QUESTION = "unresolved_question"
    CREATOR_PREFERENCE = "creator_preference"
    VALIDATED_DERIVED = "validated_derived"


@dataclass(frozen=True, slots=True)
class EvidenceAccessScope:
    requester_role: EvidenceRequesterRole
    perspective_id: TypedId | None
    permitted_private_owner_ids: tuple[TypedId, ...] = ()
    allow_system_private: bool = False
    allowed_content_classes: tuple[str, ...] = (
        "ordinary",
        "protected_non_graphic",
        "system",
    )
    allow_audit_history: bool = False

    def __post_init__(self) -> None:
        if self.perspective_id is not None:
            require_kind(self.perspective_id, IdKind.CHARACTER, "perspective_id")
        for owner_id in self.permitted_private_owner_ids:
            require_kind(owner_id, IdKind.CHARACTER, "permitted_private_owner_ids")
        _unique(
            (str(owner_id) for owner_id in self.permitted_private_owner_ids),
            "private owner scope",
        )
        _unique(self.allowed_content_classes, "allowed content classes")
        if not self.allowed_content_classes:
            raise ContractValidationError("allowed content classes cannot be empty")
        if any(
            value not in {"ordinary", "protected_non_graphic", "system"}
            for value in self.allowed_content_classes
        ):
            raise ContractValidationError("unsupported evidence content class")
        if self.requester_role is EvidenceRequesterRole.CHARACTER:
            if self.perspective_id is None:
                raise ContractValidationError("character access requires perspective_id")
            if self.allow_system_private or self.allow_audit_history:
                raise ContractValidationError(
                    "character access cannot include system-private or audit history"
                )
            if any(
                owner_id != self.perspective_id
                for owner_id in self.permitted_private_owner_ids
            ):
                raise ContractValidationError(
                    "character access cannot read another character's private state"
                )


@dataclass(frozen=True, slots=True)
class EvidenceLimits:
    maximum_query_characters: int = 512
    maximum_search_results: int = 20
    maximum_fetched_records: int = 8
    maximum_traversal_depth: int = 3
    maximum_response_bytes: int = 32_768
    maximum_followup_searches: int = 4
    maximum_snapshot_evidence_bytes: int = 65_536

    def __post_init__(self) -> None:
        values = (
            self.maximum_query_characters,
            self.maximum_search_results,
            self.maximum_fetched_records,
            self.maximum_traversal_depth,
            self.maximum_response_bytes,
            self.maximum_followup_searches,
            self.maximum_snapshot_evidence_bytes,
        )
        if any(type(value) is not int or value < 1 for value in values):
            raise ContractValidationError("evidence limits must be positive integers")
        if self.maximum_search_results > 100:
            raise ContractValidationError("maximum_search_results cannot exceed 100")
        if self.maximum_fetched_records > 50:
            raise ContractValidationError("maximum_fetched_records cannot exceed 50")


@dataclass(frozen=True, slots=True)
class EvidenceSnapshot:
    SCHEMA_VERSION: ClassVar[str] = "cera.evidence_snapshot.v2"

    schema_version: str
    snapshot_token: TypedId
    request_id: TypedId
    world_id: TypedId
    branch_id: TypedId
    generation: int
    branch_head_artifact_id: TypedId | None
    genesis_revision_id: TypedId
    access_scope: EvidenceAccessScope
    visibility_policy_version: str
    world_mode: EvidenceWorldMode
    authority_revision: int = 0

    def __post_init__(self) -> None:
        require_schema(self.schema_version, self.SCHEMA_VERSION, type(self).__name__)
        require_kind(self.snapshot_token, IdKind.SNAPSHOT, "snapshot_token")
        require_kind(self.request_id, IdKind.REQUEST, "request_id")
        require_kind(self.world_id, IdKind.WORLD, "world_id")
        require_kind(self.branch_id, IdKind.BRANCH, "branch_id")
        if self.generation < 0:
            raise ContractValidationError("snapshot generation cannot be negative")
        if self.branch_head_artifact_id is not None:
            require_kind(
                self.branch_head_artifact_id,
                IdKind.ARTIFACT,
                "branch_head_artifact_id",
            )
        require_kind(
            self.genesis_revision_id,
            IdKind.GENESIS_REVISION,
            "genesis_revision_id",
        )
        _non_empty(self.visibility_policy_version, "visibility_policy_version")
        if type(self.authority_revision) is not int or self.authority_revision < 0:
            raise ContractValidationError("snapshot authority revision cannot be negative")

    @property
    def binding_sha256(self) -> str:
        payload = {
            "request_id": str(self.request_id),
            "world_id": str(self.world_id),
            "branch_id": str(self.branch_id),
            "generation": self.generation,
            "branch_head_artifact_id": (
                str(self.branch_head_artifact_id)
                if self.branch_head_artifact_id is not None
                else None
            ),
            "genesis_revision_id": str(self.genesis_revision_id),
            "authority_revision": self.authority_revision,
            "access_scope": self.access_scope,
            "visibility_policy_version": self.visibility_policy_version,
            "world_mode": self.world_mode.value,
        }
        return domain_sha256("cera.evidence_snapshot.binding.v1", payload)


@dataclass(frozen=True, slots=True)
class EvidenceSearchRequest:
    terms: tuple[str, ...] = ()
    entity_ids: tuple[TypedId, ...] = ()
    tags: tuple[str, ...] = ()
    record_types: tuple[EvidenceRecordType, ...] = ()
    limit: int = 8

    def __post_init__(self) -> None:
        if not self.terms and not self.entity_ids and not self.tags and not self.record_types:
            raise ContractValidationError("evidence search requires a bounded selector")
        if type(self.limit) is not int or self.limit < 1:
            raise ContractValidationError("evidence search limit must be positive")
        for term in self.terms:
            _non_empty(term, "search term")
        for entity_id in self.entity_ids:
            if entity_id.kind not in {
                IdKind.CHARACTER,
                IdKind.WORLD,
                IdKind.RELATIONSHIP,
                IdKind.MATERIAL,
            }:
                raise ContractValidationError("unsupported evidence entity kind")
        _unique(self.terms, "search terms")
        _unique((str(value) for value in self.entity_ids), "search entities")
        _unique(self.tags, "search tags")
        _unique((value.value for value in self.record_types), "record types")


class EvidenceAmbiguityPolicy(StrEnum):
    RETURN_BOUNDED_CANDIDATES = "return_bounded_candidates"
    REQUIRE_UNAMBIGUOUS = "require_unambiguous"


@dataclass(frozen=True, slots=True)
class EvidenceQueryPlan:
    """Bounded provider-neutral query expansion owned by the retrieval caller.

    Each term set is an alternative semantic formulation, not another
    unbounded provider call. The evidence service executes the complete plan
    as one metered search operation and deduplicates authoritative records.
    """

    SCHEMA_VERSION: ClassVar[str] = "cera.evidence_query_plan.v1"

    schema_version: str
    primary_terms: tuple[str, ...]
    alternate_term_sets: tuple[tuple[str, ...], ...]
    entity_ids: tuple[TypedId, ...]
    tags: tuple[str, ...]
    record_types: tuple[EvidenceRecordType, ...]
    limit: int
    maximum_variants: int = 4
    ambiguity_policy: EvidenceAmbiguityPolicy = (
        EvidenceAmbiguityPolicy.RETURN_BOUNDED_CANDIDATES
    )

    def __post_init__(self) -> None:
        require_schema(self.schema_version, self.SCHEMA_VERSION, type(self).__name__)
        if not self.primary_terms:
            raise ContractValidationError("query plan requires primary terms")
        if not 1 <= self.maximum_variants <= 8:
            raise ContractValidationError("query-plan variant bound must be 1..8")
        if len(self.alternate_term_sets) + 1 > self.maximum_variants:
            raise ContractValidationError("query plan exceeds its declared variant bound")
        if not 1 <= self.limit <= 100:
            raise ContractValidationError("query-plan result limit must be 1..100")
        all_sets = (self.primary_terms, *self.alternate_term_sets)
        for term_set in all_sets:
            if not term_set:
                raise ContractValidationError("query-plan term sets cannot be empty")
            for term in term_set:
                _non_empty(term, "query-plan term")
            _unique(term_set, "query-plan terms")
        normalized_sets = tuple(
            tuple(" ".join(value.split()).casefold() for value in term_set)
            for term_set in all_sets
        )
        _unique(normalized_sets, "query-plan term sets")
        _unique((str(value) for value in self.entity_ids), "query-plan entities")
        _unique(self.tags, "query-plan tags")
        _unique((value.value for value in self.record_types), "query-plan record types")


class EvidenceObligationKind(StrEnum):
    REQUIRED_RECORD = "required_record"
    REQUIRED_SUBJECT = "required_subject"
    REQUIRED_RECORD_TYPE = "required_record_type"
    QUERY_PLAN = "query_plan"


@dataclass(frozen=True, slots=True)
class EvidenceObligation:
    """Deterministic seed requirement that must be satisfied or preserved."""

    SCHEMA_VERSION: ClassVar[str] = "cera.evidence_obligation.v1"

    schema_version: str
    obligation_id: TypedId
    kind: EvidenceObligationKind
    record_id: TypedId | None
    subject_id: TypedId | None
    record_type: EvidenceRecordType | None
    required_sections: tuple[str, ...]
    query_plan: EvidenceQueryPlan | None
    reason: str

    def __post_init__(self) -> None:
        require_schema(self.schema_version, self.SCHEMA_VERSION, type(self).__name__)
        require_kind(self.obligation_id, IdKind.OBLIGATION, "obligation_id")
        _non_empty(self.reason, "obligation reason")
        if not self.required_sections:
            raise ContractValidationError(
                "evidence obligation requires exact expansion sections"
            )
        for section in self.required_sections:
            _non_empty(section, "required section")
        _unique(self.required_sections, "required sections")
        selectors = (
            self.record_id is not None,
            self.subject_id is not None,
            self.record_type is not None,
            self.query_plan is not None,
        )
        expected_index = {
            EvidenceObligationKind.REQUIRED_RECORD: 0,
            EvidenceObligationKind.REQUIRED_SUBJECT: 1,
            EvidenceObligationKind.REQUIRED_RECORD_TYPE: 2,
            EvidenceObligationKind.QUERY_PLAN: 3,
        }[self.kind]
        if sum(selectors) != 1 or not selectors[expected_index]:
            raise ContractValidationError(
                "evidence obligation requires exactly its kind-specific selector"
            )
        if (
            self.record_id is not None
            and self.record_id.kind not in AUTHORITY_RECORD_ID_KINDS
        ):
            raise ContractValidationError("unsupported obligation record ID")


@dataclass(frozen=True, slots=True)
class EvidenceFetchRequest:
    evidence_ids: tuple[TypedId, ...]
    sections: tuple[str, ...]
    include_superseded_audit: bool = False

    def __post_init__(self) -> None:
        if not self.evidence_ids or not self.sections:
            raise ContractValidationError("exact fetch requires evidence IDs and sections")
        for evidence_id in self.evidence_ids:
            require_kind(evidence_id, IdKind.EVIDENCE, "evidence_ids")
        for section in self.sections:
            _non_empty(section, "section")
        _unique((str(value) for value in self.evidence_ids), "evidence IDs")
        _unique(self.sections, "sections")


@dataclass(frozen=True, slots=True)
class CharacterSectionsRequest:
    character_id: TypedId
    sections: tuple[str, ...]
    limit: int = 8

    def __post_init__(self) -> None:
        require_kind(self.character_id, IdKind.CHARACTER, "character_id")
        if not self.sections:
            raise ContractValidationError("character lookup requires sections")
        if type(self.limit) is not int or self.limit < 1:
            raise ContractValidationError("character lookup limit must be positive")
        _unique(self.sections, "character sections")


@dataclass(frozen=True, slots=True)
class ContinuityRequest:
    starting_evidence_ids: tuple[TypedId, ...]
    sections: tuple[str, ...]
    maximum_depth: int = 2

    def __post_init__(self) -> None:
        if not self.starting_evidence_ids or not self.sections:
            raise ContractValidationError("continuity lookup requires roots and sections")
        for evidence_id in self.starting_evidence_ids:
            require_kind(evidence_id, IdKind.EVIDENCE, "starting_evidence_ids")
        if type(self.maximum_depth) is not int or self.maximum_depth < 1:
            raise ContractValidationError("continuity depth must be positive")
        _unique((str(value) for value in self.starting_evidence_ids), "continuity roots")
        _unique(self.sections, "continuity sections")


@dataclass(frozen=True, slots=True)
class EvidenceDocument:
    """Normalized authoritative record; never sourced from a search index."""

    SCHEMA_VERSION: ClassVar[str] = "cera.evidence_document.v1"

    schema_version: str
    record_id: TypedId
    record_version: int
    record_type: EvidenceRecordType
    epistemic_class: EvidenceEpistemicClass
    truth_status: TruthStatus
    title: str
    abstract: str
    claim: str
    authority: EvidenceAuthority
    subject_ids: tuple[TypedId, ...]
    owner_id: TypedId | None
    knowledge_owner_ids: tuple[TypedId, ...]
    visibility: Visibility
    knowledge_route: KnowledgeRoute
    certainty: Certainty
    content_class: str
    genesis_revision_id: TypedId
    branch_origin_id: TypedId | None
    valid_from_generation: int
    valid_to_generation: int | None
    source_refs: tuple[TypedId, ...]
    supersedes: tuple[TypedId, ...]
    tags: tuple[str, ...]
    expandable_sections: tuple[str, ...]
    linked_record_ids: tuple[TypedId, ...]
    sections_json: str

    def __post_init__(self) -> None:
        require_schema(self.schema_version, self.SCHEMA_VERSION, type(self).__name__)
        if self.record_id.kind not in AUTHORITY_RECORD_ID_KINDS:
            raise ContractValidationError("unsupported evidence record ID kind")
        if self.record_version < 1:
            raise ContractValidationError("evidence record version must be positive")
        _non_empty(self.title, "title")
        _non_empty(self.abstract, "abstract")
        _non_empty(self.claim, "claim")
        if not self.subject_ids:
            raise ContractValidationError("evidence document requires subjects")
        if self.owner_id is not None:
            require_kind(self.owner_id, IdKind.CHARACTER, "owner_id")
        for owner_id in self.knowledge_owner_ids:
            require_kind(owner_id, IdKind.CHARACTER, "knowledge_owner_ids")
        if self.content_class not in {"ordinary", "protected_non_graphic", "system"}:
            raise ContractValidationError("unsupported evidence content class")
        require_kind(
            self.genesis_revision_id,
            IdKind.GENESIS_REVISION,
            "genesis_revision_id",
        )
        if self.branch_origin_id is not None:
            require_kind(self.branch_origin_id, IdKind.BRANCH, "branch_origin_id")
        if self.valid_from_generation < 0:
            raise ContractValidationError("valid_from_generation cannot be negative")
        if (
            self.valid_to_generation is not None
            and self.valid_to_generation < self.valid_from_generation
        ):
            raise ContractValidationError("invalid evidence validity interval")
        for linked in (*self.supersedes, *self.linked_record_ids):
            if linked.kind not in AUTHORITY_RECORD_ID_KINDS:
                raise ContractValidationError("unsupported linked evidence ID kind")
        for source_id in self.source_refs:
            if source_id.kind not in {
                IdKind.SOURCE,
                IdKind.ARTIFACT,
                IdKind.EVENT,
                IdKind.RECORD,
            }:
                raise ContractValidationError("unsupported evidence source reference")
        _unique((str(value) for value in self.subject_ids), "subjects")
        _unique((str(value) for value in self.knowledge_owner_ids), "knowledge owners")
        _unique((str(value) for value in self.source_refs), "source refs")
        _unique((str(value) for value in self.supersedes), "supersedes")
        _unique((str(value) for value in self.linked_record_ids), "linked records")
        _unique(self.tags, "tags")
        _unique(self.expandable_sections, "expandable sections")
        try:
            decoded = json.loads(self.sections_json)
        except json.JSONDecodeError as exc:
            raise ContractValidationError("sections_json must be JSON") from exc
        if not isinstance(decoded, dict) or canonical_json(decoded) != self.sections_json:
            raise ContractValidationError("sections_json must be a canonical JSON object")
        if not set(self.expandable_sections).issubset(decoded):
            raise ContractValidationError("expandable sections are absent from sections_json")
        if self.authority is EvidenceAuthority.SYNTHETIC_FIXTURE:
            if self.truth_status is not TruthStatus.NONCANONICAL:
                raise ContractValidationError("synthetic evidence must remain noncanonical")
        elif self.truth_status is TruthStatus.NONCANONICAL:
            raise ContractValidationError("non-synthetic evidence cannot be noncanonical")
        self._validate_epistemic_class()

    def _validate_epistemic_class(self) -> None:
        synthetic = self.authority is EvidenceAuthority.SYNTHETIC_FIXTURE
        character_owned = {
            EvidenceEpistemicClass.CONSCIOUS_BELIEF,
            EvidenceEpistemicClass.PRIVATE_FEELING,
            EvidenceEpistemicClass.PRIVATE_BELIEF,
            EvidenceEpistemicClass.ALLEGATION,
        }
        if self.epistemic_class is EvidenceEpistemicClass.OBJECTIVE_FACT:
            if not synthetic and self.truth_status is not TruthStatus.OBJECTIVE:
                raise ContractValidationError("objective evidence requires objective truth")
            if self.certainty is not Certainty.ESTABLISHED:
                raise ContractValidationError("objective evidence requires established certainty")
        elif self.epistemic_class in character_owned:
            if self.owner_id is None:
                raise ContractValidationError("belief, feeling, or allegation requires an owner")
            if self.certainty is Certainty.ESTABLISHED:
                raise ContractValidationError(
                    "belief, feeling, or allegation cannot establish objective truth"
                )
            if not synthetic and self.truth_status is not TruthStatus.CHARACTER_OWNED:
                raise ContractValidationError("character-owned evidence has wrong truth status")
        elif self.epistemic_class is EvidenceEpistemicClass.UNRESOLVED_QUESTION:
            if self.certainty is not Certainty.UNKNOWN:
                raise ContractValidationError("unresolved evidence requires unknown certainty")
            if not synthetic and self.truth_status is not TruthStatus.UNKNOWN:
                raise ContractValidationError("unresolved evidence requires unknown truth")
        elif self.epistemic_class is EvidenceEpistemicClass.CREATOR_PREFERENCE:
            if self.visibility is not Visibility.SYSTEM_PRIVATE:
                raise ContractValidationError("creator preference must be system-private")
            if not synthetic and self.truth_status is not TruthStatus.OBJECTIVE:
                raise ContractValidationError("creator preference has wrong truth status")
        elif (
            self.epistemic_class is EvidenceEpistemicClass.VALIDATED_DERIVED
            and not synthetic
            and self.truth_status is not TruthStatus.DERIVED
        ):
            raise ContractValidationError("derived evidence requires derived truth status")

    @property
    def record_sha256(self) -> str:
        return domain_sha256("cera.evidence_document.v1", self)


@dataclass(frozen=True, slots=True)
class EvidenceMetadata:
    record_id: TypedId
    record_version: int
    record_type: EvidenceRecordType
    epistemic_class: EvidenceEpistemicClass
    truth_status: TruthStatus
    authority: EvidenceAuthority
    owner_id: TypedId | None
    knowledge_owner_ids: tuple[TypedId, ...]
    visibility: Visibility
    content_class: str
    genesis_revision_id: TypedId
    source_refs: tuple[TypedId, ...]
    valid_from_generation: int
    valid_to_generation: int | None
    branch_origin_id: TypedId | None
    supersession_status: SupersessionStatus
    certainty: Certainty
    knowledge_route: KnowledgeRoute
    retrieval_reason: str
    tags: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class EvidenceReference:
    evidence_id: TypedId
    title: str
    abstract: str
    subject_ids: tuple[TypedId, ...]
    tags: tuple[str, ...]
    expandable_sections: tuple[str, ...]
    metadata: EvidenceMetadata


@dataclass(frozen=True, slots=True)
class ExactEvidence:
    evidence_id: TypedId
    subject_ids: tuple[TypedId, ...]
    sections_json: str
    metadata: EvidenceMetadata


@dataclass(frozen=True, slots=True)
class EvidenceLookupReceipt:
    SCHEMA_VERSION: ClassVar[str] = "cera.evidence_lookup_receipt.v1"

    schema_version: str
    lookup_receipt_id: TypedId
    snapshot_token: TypedId
    operation: str
    returned_count: int
    returned_bytes: int
    cumulative_snapshot_bytes: int
    followup_search_count: int
    truncated: bool
    truncation_reason: str | None
    authoritative_store_writes: int

    def __post_init__(self) -> None:
        require_schema(self.schema_version, self.SCHEMA_VERSION, type(self).__name__)
        require_kind(
            self.lookup_receipt_id,
            IdKind.LOOKUP_RECEIPT,
            "lookup_receipt_id",
        )
        require_kind(self.snapshot_token, IdKind.SNAPSHOT, "snapshot_token")
        _non_empty(self.operation, "operation")
        if min(
            self.returned_count,
            self.returned_bytes,
            self.cumulative_snapshot_bytes,
            self.followup_search_count,
            self.authoritative_store_writes,
        ) < 0:
            raise ContractValidationError("lookup receipt counts cannot be negative")
        if self.truncated != (self.truncation_reason is not None):
            raise ContractValidationError("truncation reason must match truncation status")
        if self.authoritative_store_writes != 0:
            raise ContractValidationError("evidence lookup cannot report authority writes")


@dataclass(frozen=True, slots=True)
class EvidenceBatch:
    snapshot_token: TypedId
    references: tuple[EvidenceReference, ...]
    exact_records: tuple[ExactEvidence, ...]
    receipt: EvidenceLookupReceipt


def _non_empty(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ContractValidationError(f"{field_name} must be non-empty")


def _unique(values, field_name: str) -> None:
    items = list(values)
    if len(items) != len(set(items)):
        raise ContractValidationError(f"{field_name} must not contain duplicates")
