"""Genesis installation and privacy-scoped evidence retrieval."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Protocol

from cera.contracts import (
    EvidenceHit,
    EvidenceRecordType,
    SupersessionStatus,
    Visibility,
)
from cera.errors import ContractValidationError
from cera.evidence import EvidenceAccessScope, EvidenceRequesterRole
from cera.ids import IdKind, TypedId, deterministic_id, require_kind
from cera.serialization import canonical_json

from .compiler import GenesisCompiler
from .models import (
    CreatorAuthorization,
    GenesisInstallBundle,
    GenesisRecord,
    GenesisRecordType,
    StoredGenesisRevision,
    SyntheticFixtureAuthorization,
)


class GenesisRevisionStorePort(Protocol):
    def install_genesis_revision(
        self, bundle: GenesisInstallBundle
    ) -> StoredGenesisRevision: ...

    def install_synthetic_genesis_fixture(
        self, bundle: GenesisInstallBundle
    ) -> StoredGenesisRevision: ...

    def active_genesis_records(
        self, revision_id: TypedId
    ) -> tuple[GenesisRecord, ...]: ...

    def genesis_record_history(
        self, revision_id: TypedId
    ) -> tuple[GenesisRecord, ...]: ...

    def get_world_genesis_revision(self, world_id: TypedId) -> TypedId: ...


@dataclass(frozen=True, slots=True)
class EvidenceQuery:
    world_id: TypedId
    branch_id: TypedId
    generation: int
    snapshot_token: TypedId
    genesis_revision_id: TypedId
    access_scope: EvidenceAccessScope
    terms: tuple[str, ...] = ()
    entity_ids: tuple[TypedId, ...] = ()
    tags: tuple[str, ...] = ()
    record_types: tuple[GenesisRecordType, ...] = ()
    limit: int = 8

    def __post_init__(self) -> None:
        require_kind(self.world_id, IdKind.WORLD, "world_id")
        require_kind(self.branch_id, IdKind.BRANCH, "branch_id")
        require_kind(self.snapshot_token, IdKind.SNAPSHOT, "snapshot_token")
        require_kind(
            self.genesis_revision_id, IdKind.GENESIS_REVISION, "genesis_revision_id"
        )
        if self.generation < 0:
            raise ContractValidationError("generation cannot be negative")
        if not 1 <= self.limit <= 20:
            raise ContractValidationError("evidence limit must be between 1 and 20")
        if not self.terms and not self.entity_ids and not self.tags and not self.record_types:
            raise ContractValidationError("evidence query requires a bounded selector")
        for term in self.terms:
            if not term.strip():
                raise ContractValidationError("query terms must be non-empty")
        for entity_id in self.entity_ids:
            if entity_id.kind not in {
                IdKind.CHARACTER,
                IdKind.WORLD,
                IdKind.RELATIONSHIP,
                IdKind.MATERIAL,
            }:
                raise ContractValidationError("query has unsupported entity kind")
        _unique(self.terms, "terms")
        _unique((str(entity_id) for entity_id in self.entity_ids), "entity IDs")
        _unique(self.tags, "tags")
        _unique((item.value for item in self.record_types), "record types")


@dataclass(frozen=True, slots=True)
class ExpandedEvidence:
    evidence_id: TypedId
    record_id: TypedId
    genesis_revision_id: TypedId
    requested_sections: tuple[str, ...]
    sections_json: str


@dataclass(frozen=True, slots=True)
class EvidenceCatalogEntry:
    evidence_id: TypedId
    record_id: TypedId
    title: str
    abstract: str
    entity_ids: tuple[TypedId, ...]
    tags: tuple[str, ...]
    owner_id: TypedId | None
    genesis_revision_id: TypedId
    expandable_sections: tuple[str, ...]


class GenesisRepository:
    def __init__(
        self,
        store: GenesisRevisionStorePort,
        compiler: GenesisCompiler | None = None,
    ) -> None:
        self.store = store
        self.compiler = compiler or GenesisCompiler()

    def compile_and_install(
        self,
        package_root,
        authorization: CreatorAuthorization | None,
        *,
        transaction_id: TypedId,
        idempotency_key: str,
    ) -> StoredGenesisRevision:
        compiled = self.compiler.compile(package_root, authorization)
        bundle = GenesisInstallBundle(
            transaction_id=transaction_id,
            idempotency_key=idempotency_key,
            compiled=compiled,
            authorization=authorization,
        )
        return self.store.install_genesis_revision(bundle)

    def compile_and_install_synthetic_fixture(
        self,
        package_root,
        authorization: SyntheticFixtureAuthorization,
        *,
        transaction_id: TypedId,
        idempotency_key: str,
    ) -> StoredGenesisRevision:
        compiled = self.compiler.compile(package_root, authorization)
        bundle = GenesisInstallBundle(
            transaction_id=transaction_id,
            idempotency_key=idempotency_key,
            compiled=compiled,
            authorization=authorization,
        )
        return self.store.install_synthetic_genesis_fixture(bundle)

    def search_evidence(self, query: EvidenceQuery) -> tuple[EvidenceHit, ...]:
        bound_revision = self.store.get_world_genesis_revision(query.world_id)
        if bound_revision != query.genesis_revision_id:
            raise ContractValidationError(
                "evidence query Genesis revision does not match immutable world binding"
            )
        records = self.store.active_genesis_records(query.genesis_revision_id)
        matches: list[tuple[int, GenesisRecord]] = []
        normalized_terms = tuple(term.casefold() for term in query.terms)
        required_tags = {tag.casefold() for tag in query.tags}
        required_entities = set(query.entity_ids)
        required_types = set(query.record_types)
        for record in records:
            if not self._visible(record, query.access_scope):
                continue
            if required_entities and not required_entities.intersection(record.subject_ids):
                continue
            if required_tags and not required_tags.issubset(
                {tag.casefold() for tag in record.tags}
            ):
                continue
            if required_types and record.record_type not in required_types:
                continue
            searchable = " ".join(
                (
                    record.claim,
                    " ".join(record.tags),
                    " ".join(str(subject_id) for subject_id in record.subject_ids),
                )
            ).casefold()
            if normalized_terms and not all(term in searchable for term in normalized_terms):
                continue
            score = sum(searchable.count(term) for term in normalized_terms)
            score += 2 * len(required_tags.intersection({tag.casefold() for tag in record.tags}))
            score += 2 * len(required_entities.intersection(record.subject_ids))
            matches.append((score, record))
        matches.sort(key=lambda item: (-item[0], str(item[1].record_id)))
        return tuple(
            self._to_hit(record, query, rank + 1)
            for rank, (_, record) in enumerate(matches[: query.limit])
        )

    def expand_evidence(
        self,
        *,
        genesis_revision_id: TypedId,
        evidence_ids: tuple[TypedId, ...],
        requested_sections: tuple[str, ...],
        access_scope: EvidenceAccessScope,
    ) -> tuple[ExpandedEvidence, ...]:
        if not evidence_ids:
            raise ContractValidationError("evidence expansion requires IDs")
        if not requested_sections:
            raise ContractValidationError("evidence expansion requires sections")
        for evidence_id in evidence_ids:
            require_kind(evidence_id, IdKind.EVIDENCE, "evidence_ids")
        _unique((str(evidence_id) for evidence_id in evidence_ids), "evidence IDs")
        _unique(requested_sections, "requested sections")
        records = self.store.active_genesis_records(genesis_revision_id)
        by_evidence = {
            self._evidence_id(genesis_revision_id, record.record_id): record
            for record in records
        }
        expanded = []
        for evidence_id in evidence_ids:
            record = by_evidence.get(evidence_id)
            if record is None or not self._visible(record, access_scope):
                raise ContractValidationError("evidence is absent or outside privacy scope")
            allowed = set(record.expandable_sections) | {"claim", "provenance"}
            if not set(requested_sections).issubset(allowed):
                raise ContractValidationError("requested evidence section is not expandable")
            sections: dict[str, object] = {}
            if "claim" in requested_sections:
                sections["claim"] = record.claim
            if "provenance" in requested_sections:
                sections["provenance"] = {
                    "authority": record.authority.value,
                    "source_refs": tuple(str(source_id) for source_id in record.source_refs),
                    "record_version": record.record_version,
                }
            if "payload" in requested_sections:
                sections["payload"] = json.loads(record.payload_json)
            if "knowledge" in requested_sections:
                sections["knowledge"] = {
                    "owner_id": str(record.owner_id) if record.owner_id else None,
                    "knowledge_owner_ids": tuple(
                        str(owner_id) for owner_id in record.knowledge_owner_ids
                    ),
                    "visibility": record.visibility.value,
                    "knowledge_route": record.knowledge_route.value,
                    "certainty": record.certainty.value,
                }
            expanded.append(
                ExpandedEvidence(
                    evidence_id=evidence_id,
                    record_id=record.record_id,
                    genesis_revision_id=genesis_revision_id,
                    requested_sections=requested_sections,
                    sections_json=canonical_json(sections),
                )
            )
        return tuple(expanded)

    def evidence_catalog(
        self,
        genesis_revision_id: TypedId,
        access_scope: EvidenceAccessScope,
    ) -> tuple[EvidenceCatalogEntry, ...]:
        records = self.store.active_genesis_records(genesis_revision_id)
        return tuple(
            EvidenceCatalogEntry(
                evidence_id=self._evidence_id(genesis_revision_id, record.record_id),
                record_id=record.record_id,
                title=record.record_type.value.replace("_", " ").title(),
                abstract=record.claim,
                entity_ids=record.subject_ids,
                tags=record.tags,
                owner_id=record.owner_id,
                genesis_revision_id=genesis_revision_id,
                expandable_sections=record.expandable_sections,
            )
            for record in records
            if self._visible(record, access_scope)
        )

    def render_markdown_view(self, genesis_revision_id: TypedId) -> str:
        records = self.store.active_genesis_records(genesis_revision_id)
        lines = [
            "# Generated Genesis View",
            "",
            "> Derived, regenerable view. This file is not authority and cannot be imported.",
            "",
            f"Revision: `{genesis_revision_id}`",
            "",
        ]
        for record in records:
            lines.extend(
                (
                    f"## {record.record_type.value}: {record.record_id}",
                    "",
                    record.claim,
                    "",
                    f"- Layer: `{record.epistemic_layer.value}`",
                    f"- Truth: `{record.truth_status.value}`",
                    f"- Visibility: `{record.visibility.value}`",
                    f"- Sources: {', '.join(f'`{source}`' for source in record.source_refs)}",
                    "",
                )
            )
        return "\n".join(lines)

    def render_catalog_json(
        self,
        genesis_revision_id: TypedId,
        access_scope: EvidenceAccessScope,
    ) -> str:
        return canonical_json(self.evidence_catalog(genesis_revision_id, access_scope))

    @staticmethod
    def _visible(record: GenesisRecord, scope: EvidenceAccessScope) -> bool:
        if record.visibility in {Visibility.PUBLIC, Visibility.SHARED}:
            return True
        if record.visibility is Visibility.OWNER_PRIVATE:
            return record.owner_id in set(scope.permitted_private_owner_ids)
        return scope.allow_system_private

    @staticmethod
    def _evidence_id(revision_id: TypedId, record_id: TypedId) -> TypedId:
        return deterministic_id(
            IdKind.EVIDENCE,
            "cera.genesis.record",
            f"{revision_id}|{record_id}",
        )

    def _to_hit(
        self, record: GenesisRecord, query: EvidenceQuery, rank: int
    ) -> EvidenceHit:
        return EvidenceHit(
            schema_version=EvidenceHit.SCHEMA_VERSION,
            evidence_id=self._evidence_id(
                query.genesis_revision_id, record.record_id
            ),
            record_id=record.record_id,
            record_version=record.record_version,
            record_type=EvidenceRecordType.GENESIS_FACT,
            truth_status=record.truth_status,
            claim=record.claim,
            authority=record.authority,
            world_id=query.world_id,
            branch_origin_id=None,
            generation=query.generation,
            snapshot_token=query.snapshot_token,
            perspective_id=query.access_scope.perspective_id,
            owner_id=record.owner_id,
            knowledge_owner_id=record.owner_id,
            owner_scope=query.access_scope.requester_role.value,
            visibility=record.visibility,
            knowledge_route=record.knowledge_route,
            certainty=record.certainty,
            content_class=record.content_class,
            genesis_revision_id=query.genesis_revision_id,
            valid_from=record.valid_from,
            valid_to=record.valid_to,
            branch_scope=(query.branch_id,),
            source_refs=record.source_refs,
            supersession_status=SupersessionStatus.CURRENT,
            tags=record.tags,
            expandable_sections=record.expandable_sections,
            retrieval_reason=f"bounded Genesis match rank {rank}",
        )


def _unique(values, label: str) -> None:
    items = list(values)
    if len(items) != len(set(items)):
        raise ContractValidationError(f"{label} must not contain duplicates")
