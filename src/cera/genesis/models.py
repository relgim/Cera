"""Strict contracts for creator-authorized Genesis revisions."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import json
from typing import ClassVar

from cera.contracts import (
    Certainty,
    EvidenceAuthority,
    KnowledgeRoute,
    TruthStatus,
    Visibility,
)
from cera.errors import ContractValidationError
from cera.ids import IdKind, TypedId, require_kind
from cera.schema import require_schema
from cera.serialization import canonical_json, domain_sha256, re_is_sha256


class GenesisRecordType(StrEnum):
    IDENTITY = "identity"
    FAMILY = "family"
    WORLD = "world"
    CHARACTER_STATE = "character_state"
    CHARACTER_PROFILE = "character_profile"
    VOICE_PROFILE = "voice_profile"
    FORMATIVE_EVENT = "formative_event"
    MEMORY_SEED = "memory_seed"
    HOUSEHOLD_RULE = "household_rule"
    VISUAL_CANON = "visual_canon"
    SUPERSESSION_LEDGER = "supersession_ledger"
    RELATIONSHIP_EDGE = "relationship_edge"
    ADULT_ELIGIBILITY = "adult_eligibility"
    STORY_START_PLACEMENT = "story_start_placement"
    UNRESOLVED_QUESTION = "unresolved_question"
    CREATOR_PREFERENCE = "creator_preference"


class EpistemicLayer(StrEnum):
    OBJECTIVE_FACT = "objective_fact"
    CONSCIOUS_BELIEF = "conscious_belief"
    PRIVATE_FEELING = "private_feeling"
    PRIVATE_BELIEF = "private_belief"
    ALLEGATION = "allegation"
    UNRESOLVED_QUESTION = "unresolved_question"
    CREATOR_PREFERENCE = "creator_preference"


class AdultEligibility(StrEnum):
    NOT_APPLICABLE = "not_applicable"
    CONFIRMED_IDENTITY_ELIGIBLE = "confirmed_identity_eligible"
    NOT_ELIGIBLE = "not_eligible"
    UNKNOWN = "unknown"


class StoryStartPresence(StrEnum):
    NOT_APPLICABLE = "not_applicable"
    PRESENT = "present"
    ABSENT = "absent"
    UNKNOWN = "unknown"


class GenesisPackageClass(StrEnum):
    CREATOR_CANON = "creator_canon"
    SYNTHETIC_FIXTURE = "synthetic_fixture"


COMPILER_CONTRACT_VERSION = "cera.genesis_compiler.v1"


@dataclass(frozen=True, slots=True)
class GenesisUnresolvedFinding:
    finding_id: TypedId
    description: str
    source_refs: tuple[TypedId, ...]
    preserved_record_id: TypedId

    def __post_init__(self) -> None:
        require_kind(self.finding_id, IdKind.GENESIS_FINDING, "finding_id")
        require_kind(self.preserved_record_id, IdKind.RECORD, "preserved_record_id")
        _non_empty(self.description, "description")
        if not self.source_refs:
            raise ContractValidationError("unresolved finding requires source refs")
        for source_id in self.source_refs:
            require_kind(source_id, IdKind.SOURCE, "source_refs")
        _unique((str(item) for item in self.source_refs), "finding source refs")


@dataclass(frozen=True, slots=True)
class GenesisModuleRef:
    source_id: TypedId
    relative_path: str
    content_sha256: str

    def __post_init__(self) -> None:
        require_kind(self.source_id, IdKind.SOURCE, "source_id")
        _relative_json_path(self.relative_path)
        _sha256(self.content_sha256, "content_sha256")


@dataclass(frozen=True, slots=True)
class GenesisManifest:
    SCHEMA_VERSION: ClassVar[str] = "cera.genesis_manifest.v1"

    schema_version: str
    package_id: TypedId
    package_class: GenesisPackageClass
    revision_id: TypedId
    revision_number: int
    parent_revision_id: TypedId | None
    modules: tuple[GenesisModuleRef, ...]
    unresolved_findings: tuple[GenesisUnresolvedFinding, ...]
    compiler_contract_version: str
    world_scope: str
    revision_label: str

    def __post_init__(self) -> None:
        require_schema(self.schema_version, self.SCHEMA_VERSION, type(self).__name__)
        require_kind(self.package_id, IdKind.GENESIS_PACKAGE, "package_id")
        require_kind(self.revision_id, IdKind.GENESIS_REVISION, "revision_id")
        if self.parent_revision_id is not None:
            require_kind(
                self.parent_revision_id, IdKind.GENESIS_REVISION, "parent_revision_id"
            )
        if self.revision_number < 1:
            raise ContractValidationError("revision_number must be positive")
        if self.revision_number == 1 and self.parent_revision_id is not None:
            raise ContractValidationError("first Genesis revision cannot have a parent")
        if self.revision_number > 1 and self.parent_revision_id is None:
            raise ContractValidationError("later Genesis revision requires a parent")
        if not self.modules:
            raise ContractValidationError("Genesis manifest must declare at least one module")
        _non_empty(self.revision_label, "revision_label")
        _unique((str(module.source_id) for module in self.modules), "module source IDs")
        _unique((module.relative_path for module in self.modules), "module paths")
        _unique(
            (str(finding.finding_id) for finding in self.unresolved_findings),
            "unresolved finding IDs",
        )
        if self.compiler_contract_version != COMPILER_CONTRACT_VERSION:
            raise ContractValidationError("unsupported Genesis compiler contract")
        expected_world_scope = (
            "creator_worlds"
            if self.package_class is GenesisPackageClass.CREATOR_CANON
            else "synthetic_only"
        )
        if self.world_scope != expected_world_scope:
            raise ContractValidationError("Genesis world scope does not match package class")


@dataclass(frozen=True, slots=True)
class GenesisRecord:
    SCHEMA_VERSION: ClassVar[str] = "cera.genesis_record.v1"

    schema_version: str
    record_id: TypedId
    record_version: int
    record_type: GenesisRecordType
    epistemic_layer: EpistemicLayer
    truth_status: TruthStatus
    claim: str
    authority: EvidenceAuthority
    subject_ids: tuple[TypedId, ...]
    owner_id: TypedId | None
    knowledge_owner_ids: tuple[TypedId, ...]
    visibility: Visibility
    knowledge_route: KnowledgeRoute
    certainty: Certainty
    content_class: str
    adult_eligibility: AdultEligibility
    story_start_presence: StoryStartPresence
    relationship_from_id: TypedId | None
    relationship_to_id: TypedId | None
    source_refs: tuple[TypedId, ...]
    valid_from: str | None
    valid_to: str | None
    supersedes: tuple[TypedId, ...]
    tags: tuple[str, ...]
    expandable_sections: tuple[str, ...]
    payload_json: str

    def __post_init__(self) -> None:
        require_schema(self.schema_version, self.SCHEMA_VERSION, type(self).__name__)
        require_kind(self.record_id, IdKind.RECORD, "record_id")
        if self.record_version < 1:
            raise ContractValidationError("record_version must be positive")
        _non_empty(self.claim, "claim")
        if self.authority not in {
            EvidenceAuthority.CREATOR,
            EvidenceAuthority.SYNTHETIC_FIXTURE,
        }:
            raise ContractValidationError("unsupported Genesis record authority")
        if self.authority is EvidenceAuthority.SYNTHETIC_FIXTURE:
            if self.truth_status is not TruthStatus.NONCANONICAL:
                raise ContractValidationError(
                    "synthetic records require noncanonical truth status"
                )
        elif self.truth_status is TruthStatus.NONCANONICAL:
            raise ContractValidationError(
                "creator Genesis cannot carry noncanonical truth status"
            )
        if not self.subject_ids:
            raise ContractValidationError("Genesis record requires at least one subject")
        for subject_id in self.subject_ids:
            if subject_id.kind not in {
                IdKind.CHARACTER,
                IdKind.WORLD,
                IdKind.RELATIONSHIP,
                IdKind.MATERIAL,
            }:
                raise ContractValidationError("unsupported Genesis subject ID kind")
        if self.owner_id is not None:
            require_kind(self.owner_id, IdKind.CHARACTER, "owner_id")
        for owner_id in self.knowledge_owner_ids:
            require_kind(owner_id, IdKind.CHARACTER, "knowledge_owner_ids")
        for source_id in self.source_refs:
            require_kind(source_id, IdKind.SOURCE, "source_refs")
        for record_id in self.supersedes:
            require_kind(record_id, IdKind.RECORD, "supersedes")
        if self.record_id in self.supersedes:
            raise ContractValidationError("Genesis record cannot supersede itself")
        _unique((str(item) for item in self.subject_ids), "subject IDs")
        _unique((str(item) for item in self.knowledge_owner_ids), "knowledge owners")
        _unique((str(item) for item in self.source_refs), "source refs")
        _unique((str(item) for item in self.supersedes), "supersession refs")
        _unique(self.tags, "tags")
        _unique(self.expandable_sections, "expandable sections")
        if not self.source_refs:
            raise ContractValidationError("Genesis record requires source refs")
        if self.content_class not in {"ordinary", "protected_non_graphic", "system"}:
            raise ContractValidationError("unsupported Genesis content_class")
        _canonical_payload(self.payload_json)
        self._validate_epistemic_layer()
        self._validate_relationship()
        self._validate_route_flags()

    def _validate_epistemic_layer(self) -> None:
        synthetic = self.authority is EvidenceAuthority.SYNTHETIC_FIXTURE
        private_layers = {
            EpistemicLayer.CONSCIOUS_BELIEF,
            EpistemicLayer.PRIVATE_FEELING,
            EpistemicLayer.PRIVATE_BELIEF,
            EpistemicLayer.ALLEGATION,
        }
        if self.epistemic_layer is EpistemicLayer.OBJECTIVE_FACT:
            if not synthetic and self.truth_status is not TruthStatus.OBJECTIVE:
                raise ContractValidationError("objective fact requires objective truth status")
            if self.certainty is not Certainty.ESTABLISHED:
                raise ContractValidationError("objective fact requires established certainty")
        elif self.epistemic_layer in private_layers:
            if not synthetic and self.truth_status is not TruthStatus.CHARACTER_OWNED:
                raise ContractValidationError("character state requires character-owned truth")
            if self.owner_id is None:
                raise ContractValidationError("character-owned state requires owner_id")
            if self.visibility is not Visibility.OWNER_PRIVATE:
                raise ContractValidationError("character-owned state must be owner-private")
            if self.owner_id not in self.knowledge_owner_ids:
                raise ContractValidationError("owner must be a knowledge owner")
            if self.certainty is Certainty.ESTABLISHED:
                raise ContractValidationError(
                    "character-owned state cannot establish an objective fact"
                )
        elif self.epistemic_layer is EpistemicLayer.UNRESOLVED_QUESTION:
            if not synthetic and self.truth_status is not TruthStatus.UNKNOWN:
                raise ContractValidationError("unresolved question requires unknown truth")
            if self.certainty is not Certainty.UNKNOWN:
                raise ContractValidationError("unresolved question requires unknown certainty")
            if self.record_type not in {
                GenesisRecordType.UNRESOLVED_QUESTION,
                GenesisRecordType.ADULT_ELIGIBILITY,
                GenesisRecordType.STORY_START_PLACEMENT,
            }:
                raise ContractValidationError(
                    "unresolved layer requires an unresolved-capable record type"
                )
        elif self.epistemic_layer is EpistemicLayer.CREATOR_PREFERENCE:
            if self.record_type is not GenesisRecordType.CREATOR_PREFERENCE:
                raise ContractValidationError("creator preference layer requires matching type")
            if not synthetic and self.truth_status is not TruthStatus.OBJECTIVE:
                raise ContractValidationError("creator preference requires objective authority")
            if self.visibility is not Visibility.SYSTEM_PRIVATE:
                raise ContractValidationError("creator preferences are system-private")

    def _validate_relationship(self) -> None:
        endpoints = (self.relationship_from_id, self.relationship_to_id)
        if self.record_type is GenesisRecordType.RELATIONSHIP_EDGE:
            if any(endpoint is None for endpoint in endpoints):
                raise ContractValidationError("relationship edge requires both directions")
            assert self.relationship_from_id is not None
            assert self.relationship_to_id is not None
            require_kind(self.relationship_from_id, IdKind.CHARACTER, "relationship_from_id")
            require_kind(self.relationship_to_id, IdKind.CHARACTER, "relationship_to_id")
            if self.relationship_from_id == self.relationship_to_id:
                raise ContractValidationError("relationship edge must be directional")
        elif any(endpoint is not None for endpoint in endpoints):
            raise ContractValidationError("only relationship edges may carry endpoints")

    def _validate_route_flags(self) -> None:
        payload = json.loads(self.payload_json)
        if self.adult_eligibility is not AdultEligibility.NOT_APPLICABLE:
            if self.record_type is not GenesisRecordType.ADULT_ELIGIBILITY:
                raise ContractValidationError("adult eligibility requires matching record type")
            if self.adult_eligibility is AdultEligibility.UNKNOWN:
                if self.epistemic_layer is not EpistemicLayer.UNRESOLVED_QUESTION:
                    raise ContractValidationError(
                        "unknown adult eligibility requires unresolved epistemic state"
                    )
            elif self.epistemic_layer is not EpistemicLayer.OBJECTIVE_FACT:
                raise ContractValidationError("known adult eligibility must be objective")
            if self.adult_eligibility is AdultEligibility.CONFIRMED_IDENTITY_ELIGIBLE:
                age = payload.get("age") if isinstance(payload, dict) else None
                if type(age) is not int or age < 18:
                    raise ContractValidationError(
                        "confirmed adult identity eligibility requires creator-established age >= 18"
                    )
        if self.story_start_presence is not StoryStartPresence.NOT_APPLICABLE:
            if self.record_type is not GenesisRecordType.STORY_START_PLACEMENT:
                raise ContractValidationError("story-start presence requires matching record type")
            if self.story_start_presence is StoryStartPresence.UNKNOWN:
                if self.epistemic_layer is not EpistemicLayer.UNRESOLVED_QUESTION:
                    raise ContractValidationError(
                        "unknown story-start presence requires unresolved epistemic state"
                    )
            elif self.epistemic_layer is not EpistemicLayer.OBJECTIVE_FACT:
                raise ContractValidationError("known story-start presence must be objective")

    @property
    def record_sha256(self) -> str:
        return domain_sha256("cera.genesis_record.v1", self)


@dataclass(frozen=True, slots=True)
class GenesisModule:
    SCHEMA_VERSION: ClassVar[str] = "cera.genesis_module.v1"

    schema_version: str
    source_id: TypedId
    package_class: GenesisPackageClass
    title: str
    records: tuple[GenesisRecord, ...]

    def __post_init__(self) -> None:
        require_schema(self.schema_version, self.SCHEMA_VERSION, type(self).__name__)
        require_kind(self.source_id, IdKind.SOURCE, "source_id")
        _non_empty(self.title, "title")
        if not self.records:
            raise ContractValidationError("Genesis module must contain records")
        _unique((str(record.record_id) for record in self.records), "module record IDs")
        for record in self.records:
            if self.source_id not in record.source_refs:
                raise ContractValidationError(
                    "each record must cite the module source containing it"
                )
            expected_authority = (
                EvidenceAuthority.CREATOR
                if self.package_class is GenesisPackageClass.CREATOR_CANON
                else EvidenceAuthority.SYNTHETIC_FIXTURE
            )
            if record.authority is not expected_authority:
                raise ContractValidationError(
                    "record authority does not match module package class"
                )


@dataclass(frozen=True, slots=True)
class AuthorizedGenesisSource:
    source_id: TypedId
    content_sha256: str

    def __post_init__(self) -> None:
        require_kind(self.source_id, IdKind.SOURCE, "source_id")
        _sha256(self.content_sha256, "content_sha256")


@dataclass(frozen=True, slots=True)
class CreatorAuthorization:
    SCHEMA_VERSION: ClassVar[str] = "cera.creator_authorization.v1"

    schema_version: str
    authorization_id: TypedId
    package_id: TypedId
    revision_id: TypedId
    manifest_sha256: str
    authorized_sources: tuple[AuthorizedGenesisSource, ...]
    authorization_source_id: TypedId
    authorization_statement_sha256: str
    scope: str
    authorized_by: str

    def __post_init__(self) -> None:
        require_schema(self.schema_version, self.SCHEMA_VERSION, type(self).__name__)
        require_kind(self.authorization_id, IdKind.AUTHORIZATION, "authorization_id")
        require_kind(self.package_id, IdKind.GENESIS_PACKAGE, "package_id")
        require_kind(self.revision_id, IdKind.GENESIS_REVISION, "revision_id")
        require_kind(self.authorization_source_id, IdKind.SOURCE, "authorization_source_id")
        _sha256(self.manifest_sha256, "manifest_sha256")
        _sha256(self.authorization_statement_sha256, "authorization_statement_sha256")
        if self.scope != "install_genesis_revision":
            raise ContractValidationError("authorization scope is not Genesis installation")
        if self.authorized_by != "creator":
            raise ContractValidationError("Genesis installation requires creator authorization")
        if not self.authorized_sources:
            raise ContractValidationError("authorization requires exact source hashes")
        _unique(
            (str(source.source_id) for source in self.authorized_sources),
            "authorized source IDs",
        )


@dataclass(frozen=True, slots=True)
class SyntheticFixtureAuthorization:
    SCHEMA_VERSION: ClassVar[str] = "cera.synthetic_fixture_authorization.v1"

    schema_version: str
    authorization_id: TypedId
    package_id: TypedId
    revision_id: TypedId
    manifest_sha256: str
    authorized_sources: tuple[AuthorizedGenesisSource, ...]
    scope: str
    authorized_by: str

    def __post_init__(self) -> None:
        require_schema(self.schema_version, self.SCHEMA_VERSION, type(self).__name__)
        require_kind(self.authorization_id, IdKind.AUTHORIZATION, "authorization_id")
        require_kind(self.package_id, IdKind.GENESIS_PACKAGE, "package_id")
        require_kind(self.revision_id, IdKind.GENESIS_REVISION, "revision_id")
        _sha256(self.manifest_sha256, "manifest_sha256")
        if self.scope != "install_synthetic_genesis_fixture":
            raise ContractValidationError("synthetic authorization has wrong scope")
        if self.authorized_by != "synthetic_test_harness":
            raise ContractValidationError("synthetic authorization requires test harness")
        if not self.authorized_sources:
            raise ContractValidationError("synthetic authorization requires source hashes")
        _unique(
            (str(source.source_id) for source in self.authorized_sources),
            "synthetic source IDs",
        )


@dataclass(frozen=True, slots=True)
class CompiledGenesisRevision:
    manifest: GenesisManifest
    manifest_sha256: str
    modules: tuple[GenesisModule, ...]
    records: tuple[GenesisRecord, ...]
    bundle_sha256: str


@dataclass(frozen=True, slots=True)
class GenesisInstallBundle:
    transaction_id: TypedId
    idempotency_key: str
    compiled: CompiledGenesisRevision
    authorization: CreatorAuthorization | SyntheticFixtureAuthorization

    def __post_init__(self) -> None:
        require_kind(self.transaction_id, IdKind.TRANSACTION, "transaction_id")
        _non_empty(self.idempotency_key, "idempotency_key")
        if len(self.idempotency_key) > 200:
            raise ContractValidationError("idempotency_key must be at most 200 characters")
        if self.compiled.manifest.revision_id != self.authorization.revision_id:
            raise ContractValidationError("authorization revision does not match compiled revision")
        if self.compiled.manifest_sha256 != self.authorization.manifest_sha256:
            raise ContractValidationError("authorization manifest hash does not match")
        if self.compiled.manifest.package_id != self.authorization.package_id:
            raise ContractValidationError("authorization package ID does not match")
        if (
            self.compiled.manifest.package_class is GenesisPackageClass.CREATOR_CANON
            and not isinstance(self.authorization, CreatorAuthorization)
        ):
            raise ContractValidationError("creator package requires creator authorization")
        if (
            self.compiled.manifest.package_class is GenesisPackageClass.SYNTHETIC_FIXTURE
            and not isinstance(self.authorization, SyntheticFixtureAuthorization)
        ):
            raise ContractValidationError("synthetic package requires fixture authorization")

    @property
    def transaction_sha256(self) -> str:
        return domain_sha256("cera.genesis_install_bundle.v1", self)


@dataclass(frozen=True, slots=True)
class GenesisRevisionReceipt:
    SCHEMA_VERSION: ClassVar[str] = "cera.genesis_revision_receipt.v1"

    schema_version: str
    receipt_id: TypedId
    transaction_id: TypedId
    package_id: TypedId
    package_class: GenesisPackageClass
    revision_id: TypedId
    parent_revision_id: TypedId | None
    manifest_sha256: str
    bundle_sha256: str
    transaction_sha256: str
    authorization_id: TypedId
    installed_record_ids: tuple[TypedId, ...]
    outcome: str

    def __post_init__(self) -> None:
        require_schema(self.schema_version, self.SCHEMA_VERSION, type(self).__name__)
        require_kind(self.receipt_id, IdKind.GENESIS_RECEIPT, "receipt_id")
        require_kind(self.transaction_id, IdKind.TRANSACTION, "transaction_id")
        require_kind(self.package_id, IdKind.GENESIS_PACKAGE, "package_id")
        require_kind(self.revision_id, IdKind.GENESIS_REVISION, "revision_id")
        if self.parent_revision_id is not None:
            require_kind(
                self.parent_revision_id, IdKind.GENESIS_REVISION, "parent_revision_id"
            )
        require_kind(self.authorization_id, IdKind.AUTHORIZATION, "authorization_id")
        _sha256(self.manifest_sha256, "manifest_sha256")
        _sha256(self.bundle_sha256, "bundle_sha256")
        _sha256(self.transaction_sha256, "transaction_sha256")
        _unique((str(record_id) for record_id in self.installed_record_ids), "record IDs")
        if self.outcome != "committed":
            raise ContractValidationError("Genesis receipt outcome must be committed")


class GenesisJournalStatus(StrEnum):
    PREPARED = "prepared"
    COMMITTED = "committed"
    ROLLED_BACK = "rolled_back"


@dataclass(frozen=True, slots=True)
class GenesisJournalEntry:
    transaction_id: TypedId
    idempotency_key: str
    revision_id: TypedId
    transaction_sha256: str
    status: GenesisJournalStatus
    receipt_id: TypedId | None
    rollback_reason: str | None


@dataclass(frozen=True, slots=True)
class StoredGenesisRevision:
    receipt: GenesisRevisionReceipt
    exact_replay: bool


@dataclass(frozen=True, slots=True)
class GenesisRecoveryReport:
    rolled_back_transaction_ids: tuple[TypedId, ...]


@dataclass(frozen=True, slots=True)
class GenesisDryRunPlan:
    package_id: TypedId
    package_class: GenesisPackageClass
    revision_id: TypedId
    manifest_sha256: str
    bundle_sha256: str
    compiler_contract_version: str
    world_scope: str
    source_hashes: tuple[AuthorizedGenesisSource, ...]
    record_hashes: tuple[str, ...]
    unresolved_findings: tuple[GenesisUnresolvedFinding, ...]

    @property
    def plan_sha256(self) -> str:
        return domain_sha256("cera.genesis_dry_run_plan.v1", self)


def _non_empty(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ContractValidationError(f"{field_name} must be non-empty")


def _unique(values, field_name: str) -> None:
    items = list(values)
    if len(items) != len(set(items)):
        raise ContractValidationError(f"{field_name} must not contain duplicates")


def _sha256(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not re_is_sha256(value):
        raise ContractValidationError(f"{field_name} must be lowercase SHA-256")


def _relative_json_path(value: str) -> None:
    _non_empty(value, "relative_path")
    normalized = value.replace("\\", "/")
    parts = normalized.split("/")
    if value != normalized or normalized.startswith("/") or any(
        part in {"", ".", ".."} for part in parts
    ):
        raise ContractValidationError("relative_path must be normalized and package-local")
    if not normalized.endswith(".json") or normalized == "manifest.json":
        raise ContractValidationError("relative_path must identify a module JSON file")


def _canonical_payload(value: str) -> None:
    try:
        decoded = json.loads(value)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ContractValidationError("payload_json must be valid JSON") from exc
    if canonical_json(decoded) != value:
        raise ContractValidationError("payload_json must use CERA canonical JSON")
