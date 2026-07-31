"""Fail-closed, read-only, snapshot-bound evidence service."""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Protocol

from cera.contracts import EvidenceAuthority, EvidenceRecordType, SupersessionStatus, Visibility
from cera.errors import ErrorCode, EvidenceServiceError
from cera.genesis.models import GenesisPackageClass, GenesisRecord, GenesisRecordType
from cera.ids import IdKind, TypedId, deterministic_id
from cera.schema import from_mapping
from cera.serialization import canonical_json, domain_sha256
from cera.storage.models import AuthorityRecord, BranchState

from .models import (
    CharacterSectionsRequest,
    ContinuityRequest,
    EvidenceAccessScope,
    EvidenceBatch,
    EvidenceDocument,
    EvidenceEpistemicClass,
    EvidenceFetchRequest,
    EvidenceLimits,
    EvidenceLookupReceipt,
    EvidenceMetadata,
    EvidenceQueryPlan,
    EvidenceReference,
    EvidenceSearchRequest,
    EvidenceSnapshot,
    EvidenceWorldMode,
    ExactEvidence,
)


VISIBILITY_POLICY_VERSION = "cera.visibility_policy.v1"


class EvidenceAuthorityStorePort(Protocol):
    allow_synthetic_genesis: bool

    def get_branch(self, branch_id: TypedId) -> BranchState: ...

    def get_world_genesis_revision(self, world_id: TypedId) -> TypedId: ...

    def genesis_package_class(self, revision_id: TypedId) -> GenesisPackageClass: ...

    def active_genesis_records(self, revision_id: TypedId) -> tuple[GenesisRecord, ...]: ...

    def genesis_record_history(self, revision_id: TypedId) -> tuple[GenesisRecord, ...]: ...

    def authority_records_at_head(
        self, branch_id: TypedId, head_artifact_id: TypedId | None
    ) -> tuple[AuthorityRecord, ...]: ...

    def get_artifact_parent_id(self, artifact_id: TypedId) -> TypedId | None: ...

    def evidence_index_candidates(self, terms: tuple[str, ...]) -> tuple[str, ...]: ...


@dataclass(frozen=True, slots=True)
class _Candidate:
    document: EvidenceDocument
    supersession_status: SupersessionStatus
    index_key: str


class EvidenceService:
    """One service instance tracks bounded tool usage, never story authority."""

    def __init__(
        self,
        store: EvidenceAuthorityStorePort,
        *,
        limits: EvidenceLimits | None = None,
        visibility_policy_version: str = VISIBILITY_POLICY_VERSION,
    ) -> None:
        self.store = store
        self.limits = limits or EvidenceLimits()
        if not visibility_policy_version.strip():
            raise ValueError("visibility_policy_version must be non-empty")
        self.visibility_policy_version = visibility_policy_version
        self._usage: dict[str, dict[str, int]] = {}

    def open_snapshot(
        self,
        *,
        request_id: TypedId,
        world_id: TypedId,
        branch_id: TypedId,
        access_scope: EvidenceAccessScope,
        world_mode: EvidenceWorldMode,
    ) -> EvidenceSnapshot:
        try:
            branch = self.store.get_branch(branch_id)
            if branch.world_id != world_id:
                raise EvidenceServiceError(
                    ErrorCode.EVIDENCE_ACCESS_DENIED,
                    "branch does not belong to requested evidence world",
                )
            revision_id = self.store.get_world_genesis_revision(world_id)
            package_class = self.store.genesis_package_class(revision_id)
        except EvidenceServiceError:
            raise
        except Exception as exc:
            raise EvidenceServiceError(
                ErrorCode.EVIDENCE_SERVICE_UNAVAILABLE,
                "required evidence snapshot could not be established",
            ) from exc
        expected_class = (
            GenesisPackageClass.CREATOR_CANON
            if world_mode is EvidenceWorldMode.REAL
            else GenesisPackageClass.SYNTHETIC_FIXTURE
        )
        if package_class is not expected_class:
            raise EvidenceServiceError(
                ErrorCode.EVIDENCE_ACCESS_DENIED,
                "world mode does not match its bound Genesis authority",
            )
        if world_mode is EvidenceWorldMode.REAL and world_id.value.casefold().startswith(
            ("synthetic", "fixture")
        ):
            raise EvidenceServiceError(
                ErrorCode.EVIDENCE_ACCESS_DENIED,
                "synthetic world identifiers are rejected by real-world mode",
            )
        if world_mode is EvidenceWorldMode.SYNTHETIC_FIXTURE and not getattr(
            self.store, "allow_synthetic_genesis", False
        ):
            raise EvidenceServiceError(
                ErrorCode.EVIDENCE_ACCESS_DENIED,
                "synthetic evidence mode is disabled for this store",
            )
        binding = _snapshot_binding(
            request_id=request_id,
            world_id=world_id,
            branch=branch,
            revision_id=revision_id,
            access_scope=access_scope,
            policy_version=self.visibility_policy_version,
            world_mode=world_mode,
        )
        snapshot = EvidenceSnapshot(
            schema_version=EvidenceSnapshot.SCHEMA_VERSION,
            snapshot_token=deterministic_id(
                IdKind.SNAPSHOT,
                "cera.evidence.snapshot.v2",
                domain_sha256("cera.evidence.snapshot.binding.v2", binding),
            ),
            request_id=request_id,
            world_id=world_id,
            branch_id=branch_id,
            generation=branch.generation,
            branch_head_artifact_id=branch.head_artifact_id,
            genesis_revision_id=revision_id,
            access_scope=access_scope,
            visibility_policy_version=self.visibility_policy_version,
            world_mode=world_mode,
            authority_revision=branch.authority_revision,
        )
        self._usage.setdefault(str(snapshot.snapshot_token), {"bytes": 0, "searches": 0, "ops": 0})
        return snapshot

    def validate_snapshot(self, snapshot: EvidenceSnapshot) -> None:
        """Revalidate a snapshot without retrieval or authority writes."""

        self._validate_snapshot(snapshot)

    def search_evidence(
        self, snapshot: EvidenceSnapshot, request: EvidenceSearchRequest
    ) -> EvidenceBatch:
        self._validate_snapshot(snapshot)
        usage = self._usage_for(snapshot)
        if sum(len(value) for value in request.terms) > self.limits.maximum_query_characters:
            self._limit("evidence query exceeds maximum length")
        if request.limit > self.limits.maximum_search_results:
            self._limit("evidence search result limit exceeds route budget")
        if usage["searches"] >= self.limits.maximum_followup_searches:
            self._limit("evidence follow-up search budget exhausted")
        usage["searches"] += 1
        try:
            index_keys = set(self.store.evidence_index_candidates(request.terms))
            candidates = self._authorized_candidates(snapshot, include_history=False)
        except EvidenceServiceError:
            raise
        except Exception as exc:
            raise EvidenceServiceError(
                ErrorCode.EVIDENCE_SERVICE_UNAVAILABLE,
                "evidence search infrastructure is unavailable",
            ) from exc
        required_entities = set(request.entity_ids)
        required_tags = {value.casefold() for value in request.tags}
        required_types = set(request.record_types)
        matches: list[tuple[int, _Candidate]] = []
        normalized_terms = tuple(value.casefold() for value in request.terms)
        for candidate in candidates:
            document = candidate.document
            if candidate.index_key not in index_keys:
                continue
            if required_entities and not required_entities.intersection(document.subject_ids):
                continue
            tags = {value.casefold() for value in document.tags}
            if required_tags and not required_tags.issubset(tags):
                continue
            if required_types and document.record_type not in required_types:
                continue
            searchable = _searchable(document)
            score = sum(searchable.count(value) for value in normalized_terms)
            score += 2 * len(required_tags.intersection(tags))
            score += 2 * len(required_entities.intersection(document.subject_ids))
            matches.append((score, candidate))
        matches.sort(key=lambda value: (-value[0], str(value[1].document.record_id)))
        references = tuple(
            self._reference(snapshot, candidate, f"bounded search rank {rank}")
            for rank, (_, candidate) in enumerate(matches[: request.limit], start=1)
        )
        bounded, truncated = self._bound_items(snapshot, references)
        receipt = self._receipt(
            snapshot,
            operation="search_evidence",
            items=bounded,
            truncated=truncated,
            reason="response or snapshot byte budget" if truncated else None,
        )
        return EvidenceBatch(snapshot.snapshot_token, bounded, (), receipt)

    def search_query_plan(
        self, snapshot: EvidenceSnapshot, plan: EvidenceQueryPlan
    ) -> EvidenceBatch:
        """Execute bounded paraphrase expansion as one metered search."""

        self._validate_snapshot(snapshot)
        usage = self._usage_for(snapshot)
        term_sets = (plan.primary_terms, *plan.alternate_term_sets)
        if len(term_sets) > plan.maximum_variants:
            self._limit("query expansion exceeds its declared variant bound")
        if (
            sum(len(value) for group in term_sets for value in group)
            > self.limits.maximum_query_characters
        ):
            self._limit("expanded evidence query exceeds maximum length")
        if plan.limit > self.limits.maximum_search_results:
            self._limit("evidence query-plan result limit exceeds route budget")
        if usage["searches"] >= self.limits.maximum_followup_searches:
            self._limit("evidence follow-up search budget exhausted")
        usage["searches"] += 1
        try:
            candidates = self._authorized_candidates(snapshot, include_history=False)
            indexed_by_variant = tuple(
                set(self.store.evidence_index_candidates(terms))
                for terms in term_sets
            )
        except EvidenceServiceError:
            raise
        except Exception as exc:
            raise EvidenceServiceError(
                ErrorCode.EVIDENCE_SERVICE_UNAVAILABLE,
                "evidence query-plan infrastructure is unavailable",
            ) from exc
        required_entities = set(plan.entity_ids)
        required_tags = {value.casefold() for value in plan.tags}
        required_types = set(plan.record_types)
        best: dict[TypedId, tuple[int, int, _Candidate]] = {}
        for variant_index, (terms, index_keys) in enumerate(
            zip(term_sets, indexed_by_variant, strict=True)
        ):
            normalized_terms = tuple(_normalized_term(value) for value in terms)
            for candidate in candidates:
                document = candidate.document
                if candidate.index_key not in index_keys:
                    continue
                if required_entities and not required_entities.intersection(
                    document.subject_ids
                ):
                    continue
                tags = {value.casefold() for value in document.tags}
                if required_tags and not required_tags.issubset(tags):
                    continue
                if required_types and document.record_type not in required_types:
                    continue
                searchable = _searchable(document)
                score = sum(searchable.count(value) for value in normalized_terms)
                score += 2 * len(required_tags.intersection(tags))
                score += 2 * len(required_entities.intersection(document.subject_ids))
                current = best.get(document.record_id)
                ranked = (score, -variant_index, candidate)
                if current is None or ranked[:2] > current[:2]:
                    best[document.record_id] = ranked
        matches = sorted(
            best.values(),
            key=lambda value: (
                -value[0],
                -value[1],
                str(value[2].document.record_id),
            ),
        )
        references = tuple(
            self._reference(
                snapshot,
                candidate,
                f"bounded query-plan rank {rank}",
            )
            for rank, (_, _, candidate) in enumerate(
                matches[: plan.limit],
                start=1,
            )
        )
        bounded, truncated = self._bound_items(snapshot, references)
        receipt = self._receipt(
            snapshot,
            operation="search_query_plan",
            items=bounded,
            truncated=truncated,
            reason="response or snapshot byte budget" if truncated else None,
        )
        return EvidenceBatch(snapshot.snapshot_token, bounded, (), receipt)

    def reauthorize_exact_evidence(
        self,
        snapshot: EvidenceSnapshot,
        supplied: ExactEvidence,
    ) -> ExactEvidence:
        """Reauthorize a pre-expanded seed against the immutable snapshot.

        This performs no lookup metering and no authority write. Any mismatch
        means the dossier is stale, over-broad, or not authorized.
        """

        self._validate_snapshot(snapshot)
        candidates = self._authorized_candidates(snapshot, include_history=False)
        by_id = {
            self._evidence_id(snapshot, value.document.record_id): value
            for value in candidates
        }
        candidate = by_id.get(supplied.evidence_id)
        if candidate is None:
            self._deny("seed evidence is absent or unauthorized for this snapshot")
        assert candidate is not None
        document = candidate.document
        if (
            supplied.metadata.record_id != document.record_id
            or supplied.metadata.record_version != document.record_version
            or supplied.metadata.record_type is not document.record_type
            or supplied.metadata.genesis_revision_id != snapshot.genesis_revision_id
            or supplied.metadata.owner_id != document.owner_id
            or supplied.metadata.knowledge_owner_ids != document.knowledge_owner_ids
            or supplied.metadata.visibility is not document.visibility
            or supplied.metadata.content_class != document.content_class
            or supplied.metadata.supersession_status
            is not SupersessionStatus.CURRENT
            or supplied.subject_ids != document.subject_ids
        ):
            self._deny("seed evidence metadata no longer matches authority")
        supplied_sections = json.loads(supplied.sections_json)
        if not isinstance(supplied_sections, dict) or not set(
            supplied_sections
        ).issubset(document.expandable_sections):
            self._deny("seed evidence contains unavailable sections")
        authoritative_sections = json.loads(document.sections_json)
        selected = {
            name: authoritative_sections[name]
            for name in supplied_sections
        }
        canonical = canonical_json(selected)
        if canonical != supplied.sections_json:
            self._deny("seed evidence content no longer matches authority")
        return ExactEvidence(
            evidence_id=supplied.evidence_id,
            subject_ids=document.subject_ids,
            sections_json=canonical,
            metadata=self._metadata(
                candidate,
                "immutable-snapshot seed reauthorization",
            ),
        )

    def fetch_evidence(
        self, snapshot: EvidenceSnapshot, request: EvidenceFetchRequest
    ) -> EvidenceBatch:
        self._validate_snapshot(snapshot)
        if len(request.evidence_ids) > self.limits.maximum_fetched_records:
            self._limit("exact evidence fetch exceeds record budget")
        if request.include_superseded_audit and not snapshot.access_scope.allow_audit_history:
            self._deny("snapshot does not authorize superseded audit history")
        candidates = self._authorized_candidates(
            snapshot,
            include_history=request.include_superseded_audit,
        )
        by_id = {self._evidence_id(snapshot, value.document.record_id): value for value in candidates}
        exact: list[ExactEvidence] = []
        for evidence_id in request.evidence_ids:
            candidate = by_id.get(evidence_id)
            if candidate is None:
                self._deny("evidence reference is absent or unauthorized for this snapshot")
            assert candidate is not None
            if (
                candidate.supersession_status is SupersessionStatus.SUPERSEDED
                and not request.include_superseded_audit
            ):
                self._deny("superseded evidence requires an authorized audit fetch")
            document = candidate.document
            if not set(request.sections).issubset(document.expandable_sections):
                self._deny("requested section is unavailable or unauthorized")
            section_map = json.loads(document.sections_json)
            selected = {name: section_map[name] for name in request.sections}
            exact.append(
                ExactEvidence(
                    evidence_id=evidence_id,
                    subject_ids=document.subject_ids,
                    sections_json=canonical_json(selected),
                    metadata=self._metadata(
                        candidate,
                        "authorized exact evidence fetch",
                    ),
                )
            )
        bounded, truncated = self._bound_items(snapshot, tuple(exact))
        receipt = self._receipt(
            snapshot,
            operation="fetch_evidence",
            items=bounded,
            truncated=truncated,
            reason="response or snapshot byte budget" if truncated else None,
        )
        return EvidenceBatch(snapshot.snapshot_token, (), bounded, receipt)

    def get_character_sections(
        self, snapshot: EvidenceSnapshot, request: CharacterSectionsRequest
    ) -> EvidenceBatch:
        self._validate_snapshot(snapshot)
        if request.limit > self.limits.maximum_fetched_records:
            self._limit("character section result limit exceeds fetch budget")
        candidates = [
            value
            for value in self._authorized_candidates(snapshot, include_history=False)
            if request.character_id in value.document.subject_ids
            and set(request.sections).issubset(value.document.expandable_sections)
        ]
        candidates.sort(key=lambda value: str(value.document.record_id))
        ids = tuple(
            self._evidence_id(snapshot, value.document.record_id)
            for value in candidates[: request.limit]
        )
        if not ids:
            receipt = self._receipt(
                snapshot,
                operation="get_character_sections",
                items=(),
                truncated=False,
                reason=None,
            )
            return EvidenceBatch(snapshot.snapshot_token, (), (), receipt)
        result = self.fetch_evidence(
            snapshot,
            EvidenceFetchRequest(evidence_ids=ids, sections=request.sections),
        )
        receipt = self._receipt(
            snapshot,
            operation="get_character_sections",
            items=result.exact_records,
            truncated=result.receipt.truncated,
            reason=result.receipt.truncation_reason,
            count_bytes=False,
        )
        return EvidenceBatch(snapshot.snapshot_token, (), result.exact_records, receipt)

    def get_continuity(
        self, snapshot: EvidenceSnapshot, request: ContinuityRequest
    ) -> EvidenceBatch:
        self._validate_snapshot(snapshot)
        if request.maximum_depth > self.limits.maximum_traversal_depth:
            self._limit("continuity traversal exceeds depth budget")
        candidates = self._authorized_candidates(snapshot, include_history=False)
        by_evidence = {
            self._evidence_id(snapshot, value.document.record_id): value for value in candidates
        }
        by_record = {value.document.record_id: value for value in candidates}
        frontier: list[tuple[TypedId, int]] = [
            (evidence_id, 0) for evidence_id in request.starting_evidence_ids
        ]
        visited: set[TypedId] = set()
        ordered: list[_Candidate] = []
        while frontier:
            evidence_id, depth = frontier.pop(0)
            if evidence_id in visited:
                continue
            visited.add(evidence_id)
            candidate = by_evidence.get(evidence_id)
            if candidate is None:
                self._deny("continuity root or linked evidence is unauthorized")
            assert candidate is not None
            if not set(request.sections).issubset(candidate.document.expandable_sections):
                self._deny("continuity section is unavailable or unauthorized")
            ordered.append(candidate)
            if len(ordered) > self.limits.maximum_fetched_records:
                self._limit("continuity traversal exceeds fetched-record budget")
            if depth >= request.maximum_depth:
                continue
            for record_id in candidate.document.linked_record_ids:
                linked = by_record.get(record_id)
                if linked is None:
                    self._deny("linked continuity evidence is unauthorized")
                frontier.append((self._evidence_id(snapshot, record_id), depth + 1))
        exact = tuple(
            ExactEvidence(
                evidence_id=self._evidence_id(snapshot, value.document.record_id),
                subject_ids=value.document.subject_ids,
                sections_json=canonical_json(
                    {
                        name: json.loads(value.document.sections_json)[name]
                        for name in request.sections
                    }
                ),
                metadata=self._metadata(value, "authorized continuity traversal"),
            )
            for value in ordered
        )
        bounded, truncated = self._bound_items(snapshot, exact)
        receipt = self._receipt(
            snapshot,
            operation="get_continuity",
            items=bounded,
            truncated=truncated,
            reason="response or snapshot byte budget" if truncated else None,
        )
        return EvidenceBatch(snapshot.snapshot_token, (), bounded, receipt)

    def _validate_snapshot(self, snapshot: EvidenceSnapshot) -> None:
        if snapshot.visibility_policy_version != self.visibility_policy_version:
            self._stale("visibility policy version changed")
        try:
            branch = self.store.get_branch(snapshot.branch_id)
            if branch.world_id != snapshot.world_id:
                self._stale("snapshot world and branch no longer agree")
            if (
                branch.generation != snapshot.generation
                or branch.head_artifact_id != snapshot.branch_head_artifact_id
                or branch.authority_revision != snapshot.authority_revision
            ):
                self._stale(
                    "branch head or generation changed after snapshot creation, "
                    "or derived authority changed"
                )
            if self.store.get_world_genesis_revision(snapshot.world_id) != snapshot.genesis_revision_id:
                self._stale("Genesis revision binding does not match snapshot")
        except EvidenceServiceError:
            raise
        except Exception as exc:
            raise EvidenceServiceError(
                ErrorCode.EVIDENCE_SERVICE_UNAVAILABLE,
                "snapshot authority could not be revalidated",
            ) from exc
        binding = _snapshot_binding(
            request_id=snapshot.request_id,
            world_id=snapshot.world_id,
            branch=branch,
            revision_id=snapshot.genesis_revision_id,
            access_scope=snapshot.access_scope,
            policy_version=snapshot.visibility_policy_version,
            world_mode=snapshot.world_mode,
        )
        expected = deterministic_id(
            IdKind.SNAPSHOT,
            "cera.evidence.snapshot.v2",
            domain_sha256("cera.evidence.snapshot.binding.v2", binding),
        )
        if expected != snapshot.snapshot_token:
            self._stale("snapshot token does not match its immutable binding")

    def _authorized_candidates(
        self, snapshot: EvidenceSnapshot, *, include_history: bool
    ) -> tuple[_Candidate, ...]:
        genesis_records = (
            self.store.genesis_record_history(snapshot.genesis_revision_id)
            if include_history
            else self.store.active_genesis_records(snapshot.genesis_revision_id)
        )
        genesis_key_map = _genesis_key_map(genesis_records)
        genesis_documents = tuple(
            self._from_genesis(
                record,
                snapshot.genesis_revision_id,
                genesis_key_map,
            )
            for record in genesis_records
        )
        authority_documents: list[EvidenceDocument] = []
        for record in self.store.authority_records_at_head(
            snapshot.branch_id, snapshot.branch_head_artifact_id
        ):
            try:
                payload = json.loads(record.payload_json)
                if payload.get("schema_version") != EvidenceDocument.SCHEMA_VERSION:
                    continue
                document = from_mapping(EvidenceDocument, payload)
            except Exception as exc:
                raise EvidenceServiceError(
                    ErrorCode.EVIDENCE_SERVICE_UNAVAILABLE,
                    "an authoritative evidence record is malformed",
                ) from exc
            if (
                document.record_id != record.record_id
                or document.branch_origin_id != record.branch_id
                or document.genesis_revision_id != snapshot.genesis_revision_id
                or document.supersedes != record.supersedes
            ):
                raise EvidenceServiceError(
                    ErrorCode.EVIDENCE_SERVICE_UNAVAILABLE,
                    "authority record envelope does not match evidence payload",
                )
            authority_documents.append(document)
        superseded = {
            record_id
            for document in authority_documents
            for record_id in document.supersedes
        }
        if include_history:
            superseded.update(
                record_id
                for document in genesis_documents
                for record_id in document.supersedes
            )
        candidates: list[_Candidate] = []
        for document in (*genesis_documents, *authority_documents):
            status = (
                SupersessionStatus.SUPERSEDED
                if document.record_id in superseded
                else SupersessionStatus.CURRENT
            )
            if status is SupersessionStatus.SUPERSEDED and not include_history:
                continue
            if not self._authorized(document, snapshot):
                continue
            prefix = "genesis" if document.branch_origin_id is None else "authority"
            candidates.append(
                _Candidate(document, status, f"{prefix}|{document.record_id}")
            )
        candidates.sort(key=lambda value: value.index_key)
        return tuple(candidates)

    @staticmethod
    def _authorized(document: EvidenceDocument, snapshot: EvidenceSnapshot) -> bool:
        scope = snapshot.access_scope
        if document.content_class not in scope.allowed_content_classes:
            return False
        # Branch generation counters restart at a fork. Inherited records are
        # already bounded by the immutable artifact lineage, so their origin-
        # branch generation must not be compared to the child's local counter.
        if document.branch_origin_id in {None, snapshot.branch_id}:
            if document.valid_from_generation > snapshot.generation:
                return False
            if (
                document.valid_to_generation is not None
                and snapshot.generation > document.valid_to_generation
            ):
                return False
        if snapshot.world_mode is EvidenceWorldMode.REAL:
            if document.authority is EvidenceAuthority.SYNTHETIC_FIXTURE:
                return False
        elif document.authority is not EvidenceAuthority.SYNTHETIC_FIXTURE:
            return False
        if document.visibility is Visibility.OWNER_PRIVATE:
            if document.owner_id not in set(scope.permitted_private_owner_ids):
                return False
        elif document.visibility is Visibility.SYSTEM_PRIVATE:
            if not scope.allow_system_private:
                return False
        if scope.perspective_id is not None and document.knowledge_owner_ids:
            if scope.perspective_id not in document.knowledge_owner_ids:
                return False
        return True

    @staticmethod
    def _from_genesis(
        record: GenesisRecord,
        revision_id: TypedId,
        external_key_map: dict[str, TypedId] | None = None,
    ) -> EvidenceDocument:
        payload = json.loads(record.payload_json)
        if external_key_map is None:
            external_key_map = {
                value: record.record_id
                for value in _genesis_external_keys(payload)
            }
        sections = {
            "claim": record.claim,
            "knowledge": {
                "owner_id": str(record.owner_id) if record.owner_id else None,
                "knowledge_owner_ids": tuple(str(value) for value in record.knowledge_owner_ids),
                "visibility": record.visibility.value,
                "knowledge_route": record.knowledge_route.value,
                "certainty": record.certainty.value,
            },
            "payload": payload,
            "provenance": {
                "authority": record.authority.value,
                "record_version": record.record_version,
                "source_refs": tuple(str(value) for value in record.source_refs),
            },
            "relationship": {
                "from_id": str(record.relationship_from_id)
                if record.relationship_from_id
                else None,
                "to_id": str(record.relationship_to_id)
                if record.relationship_to_id
                else None,
            },
            "route_flags": {
                "adult_eligibility": record.adult_eligibility.value,
                "story_start_presence": record.story_start_presence.value,
            },
        }
        sections.update(
            _expandable_payload_sections(payload, record.expandable_sections)
        )
        return EvidenceDocument(
            schema_version=EvidenceDocument.SCHEMA_VERSION,
            record_id=record.record_id,
            record_version=record.record_version,
            record_type=_evidence_type_for_genesis(record.record_type),
            epistemic_class=EvidenceEpistemicClass(record.epistemic_layer.value),
            truth_status=record.truth_status,
            title=_privacy_safe_title(record, payload),
            abstract=_privacy_safe_abstract(record, payload),
            claim=record.claim,
            authority=record.authority,
            subject_ids=record.subject_ids,
            owner_id=record.owner_id,
            knowledge_owner_ids=record.knowledge_owner_ids,
            visibility=record.visibility,
            knowledge_route=record.knowledge_route,
            certainty=record.certainty,
            content_class=record.content_class,
            genesis_revision_id=revision_id,
            branch_origin_id=None,
            valid_from_generation=0,
            valid_to_generation=None,
            source_refs=record.source_refs,
            supersedes=record.supersedes,
            tags=tuple(
                dict.fromkeys(
                    (
                        *record.tags,
                        f"genesis_subtype:{record.record_type.value}",
                        *_genesis_alias_tags(payload),
                    )
                )
            ),
            expandable_sections=tuple(
                sorted(
                    set(record.expandable_sections)
                    | {
                        "claim",
                        "knowledge",
                        "provenance",
                        "relationship",
                        "route_flags",
                    }
                )
            ),
            linked_record_ids=_linked_genesis_records(
                payload,
                external_key_map,
                own_record_id=record.record_id,
            ),
            sections_json=canonical_json(sections),
        )
    def _reference(
        self, snapshot: EvidenceSnapshot, candidate: _Candidate, reason: str
    ) -> EvidenceReference:
        document = candidate.document
        return EvidenceReference(
            evidence_id=self._evidence_id(snapshot, document.record_id),
            title=document.title,
            abstract=document.abstract,
            subject_ids=document.subject_ids,
            tags=document.tags,
            expandable_sections=document.expandable_sections,
            metadata=self._metadata(candidate, reason),
        )

    @staticmethod
    def _metadata(candidate: _Candidate, reason: str) -> EvidenceMetadata:
        document = candidate.document
        return EvidenceMetadata(
            record_id=document.record_id,
            record_version=document.record_version,
            record_type=document.record_type,
            epistemic_class=document.epistemic_class,
            truth_status=document.truth_status,
            authority=document.authority,
            owner_id=document.owner_id,
            knowledge_owner_ids=document.knowledge_owner_ids,
            visibility=document.visibility,
            content_class=document.content_class,
            genesis_revision_id=document.genesis_revision_id,
            source_refs=document.source_refs,
            valid_from_generation=document.valid_from_generation,
            valid_to_generation=document.valid_to_generation,
            branch_origin_id=document.branch_origin_id,
            supersession_status=candidate.supersession_status,
            certainty=document.certainty,
            knowledge_route=document.knowledge_route,
            retrieval_reason=reason,
            tags=document.tags,
        )

    @staticmethod
    def _evidence_id(snapshot: EvidenceSnapshot, record_id: TypedId) -> TypedId:
        return deterministic_id(
            IdKind.EVIDENCE,
            "cera.evidence.record.v1",
            f"{snapshot.world_id}|{snapshot.genesis_revision_id}|{record_id}",
        )

    def _usage_for(self, snapshot: EvidenceSnapshot) -> dict[str, int]:
        return self._usage.setdefault(
            str(snapshot.snapshot_token), {"bytes": 0, "searches": 0, "ops": 0}
        )

    def _bound_items(self, snapshot: EvidenceSnapshot, items: tuple) -> tuple[tuple, bool]:
        usage = self._usage_for(snapshot)
        accepted: list[object] = []
        truncated = False
        for item in items:
            candidate = tuple((*accepted, item))
            size = len(canonical_json(candidate).encode("utf-8"))
            if (
                size > self.limits.maximum_response_bytes
                or usage["bytes"] + size
                > self.limits.maximum_snapshot_evidence_bytes
            ):
                truncated = True
                break
            accepted.append(item)
        return tuple(accepted), truncated

    def _receipt(
        self,
        snapshot: EvidenceSnapshot,
        *,
        operation: str,
        items: tuple,
        truncated: bool,
        reason: str | None,
        count_bytes: bool = True,
    ) -> EvidenceLookupReceipt:
        usage = self._usage_for(snapshot)
        returned_bytes = len(canonical_json(items).encode("utf-8"))
        if count_bytes:
            usage["bytes"] += returned_bytes
        usage["ops"] += 1
        return EvidenceLookupReceipt(
            schema_version=EvidenceLookupReceipt.SCHEMA_VERSION,
            lookup_receipt_id=deterministic_id(
                IdKind.LOOKUP_RECEIPT,
                "cera.evidence.lookup.v1",
                f"{snapshot.snapshot_token}|{operation}|{usage['ops']}|{domain_sha256('cera.evidence.response.v1', items)}",
            ),
            snapshot_token=snapshot.snapshot_token,
            operation=operation,
            returned_count=len(items),
            returned_bytes=returned_bytes,
            cumulative_snapshot_bytes=usage["bytes"],
            followup_search_count=usage["searches"],
            truncated=truncated,
            truncation_reason=reason,
            authoritative_store_writes=0,
        )

    @staticmethod
    def _deny(message: str) -> None:
        raise EvidenceServiceError(ErrorCode.EVIDENCE_ACCESS_DENIED, message)

    @staticmethod
    def _limit(message: str) -> None:
        raise EvidenceServiceError(ErrorCode.EVIDENCE_LIMIT_EXCEEDED, message)

    @staticmethod
    def _stale(message: str) -> None:
        raise EvidenceServiceError(ErrorCode.EVIDENCE_SNAPSHOT_STALE, message)


def _expandable_payload_sections(
    payload: dict[str, object], advertised_sections: tuple[str, ...]
) -> dict[str, object]:
    """Project compiler-advertised Genesis sections into exact fetch sections.

    Human source fields are often grouped under ``fields`` while larger records
    use nested typed objects. The compiler publishes normalized section names;
    the evidence boundary resolves those names without exposing an arbitrary
    JSON-path API or requiring runtime consumers to parse the entire payload.
    """

    resolved: dict[str, object] = {}
    grouped_fields = payload.get("fields")
    for section in advertised_sections:
        if section in payload:
            resolved[section] = payload[section]
            continue
        if isinstance(grouped_fields, dict):
            matches = [
                value
                for key, value in grouped_fields.items()
                if _section_key(str(key)) == section
            ]
            if len(matches) == 1:
                resolved[section] = matches[0]
                continue
        matches = _find_nested_section_values(payload, section)
        if len(matches) == 1:
            resolved[section] = matches[0]
    return resolved


def _find_nested_section_values(value: object, section: str) -> list[object]:
    matches: list[object] = []
    if isinstance(value, dict):
        for key, nested in value.items():
            if key == "source":
                continue
            if _section_key(str(key)) == section:
                matches.append(nested)
            matches.extend(_find_nested_section_values(nested, section))
    elif isinstance(value, list):
        for nested in value:
            matches.extend(_find_nested_section_values(nested, section))
    return matches


def _section_key(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")
    return normalized[:100] or "section"


def _snapshot_binding(
    *,
    request_id: TypedId,
    world_id: TypedId,
    branch: BranchState,
    revision_id: TypedId,
    access_scope: EvidenceAccessScope,
    policy_version: str,
    world_mode: EvidenceWorldMode,
) -> dict[str, object]:
    return {
        "request_id": str(request_id),
        "world_id": str(world_id),
        "branch_id": str(branch.branch_id),
        "generation": branch.generation,
        "branch_head_artifact_id": (
            str(branch.head_artifact_id) if branch.head_artifact_id else None
        ),
        "genesis_revision_id": str(revision_id),
        "authority_revision": branch.authority_revision,
        "access_scope": access_scope,
        "visibility_policy_version": policy_version,
        "world_mode": world_mode.value,
    }


def _searchable(document: EvidenceDocument) -> str:
    return " ".join(
        (
            document.title,
            document.abstract,
            document.claim,
            document.sections_json,
            " ".join(document.tags),
            " ".join(str(value) for value in document.subject_ids),
            document.record_type.value,
        )
    ).casefold()


_GENESIS_EVIDENCE_TYPES = {
    GenesisRecordType.FORMATIVE_EVENT: EvidenceRecordType.EVENT_FACT,
    GenesisRecordType.MEMORY_SEED: EvidenceRecordType.MEMORY,
    GenesisRecordType.RELATIONSHIP_EDGE: EvidenceRecordType.RELATIONSHIP,
}


def _evidence_type_for_genesis(
    record_type: GenesisRecordType,
) -> EvidenceRecordType:
    return _GENESIS_EVIDENCE_TYPES.get(
        record_type,
        EvidenceRecordType.GENESIS_FACT,
    )


def _privacy_safe_title(
    record: GenesisRecord,
    payload: dict[str, object],
) -> str:
    title = payload.get("title")
    if isinstance(title, str) and title.strip():
        return " ".join(title.split())[:200]
    return record.record_type.value.replace("_", " ").title()


def _privacy_safe_abstract(
    record: GenesisRecord,
    payload: dict[str, object],
) -> str:
    title = _privacy_safe_title(record, payload)
    owner_scope = (
        f"owner-scoped to {record.owner_id}"
        if record.owner_id is not None
        else record.visibility.value.replace("_", " ")
    )
    return (
        f"{record.record_type.value.replace('_', ' ')} reference "
        f"{title!r}; {owner_scope}; exact content requires authorized expansion"
    )


def _genesis_external_keys(payload: dict[str, object]) -> tuple[str, ...]:
    keys: list[str] = []
    for name, value in payload.items():
        normalized_name = name.casefold().replace(" ", "_")
        if (
            normalized_name.endswith("_id")
            and normalized_name
            not in {"owner_id", "character_id", "from_id", "to_id"}
            and isinstance(value, str)
            and value.strip()
        ):
            keys.append(_normalize_external_key(value))
    return tuple(dict.fromkeys(keys))


def _genesis_key_map(records: tuple[GenesisRecord, ...]) -> dict[str, TypedId]:
    result: dict[str, TypedId] = {}
    duplicates: set[str] = set()
    for record in records:
        payload = json.loads(record.payload_json)
        for key in _genesis_external_keys(payload):
            if key in result and result[key] != record.record_id:
                duplicates.add(key)
            else:
                result[key] = record.record_id
    for key in duplicates:
        result.pop(key, None)
    return result


def _genesis_alias_tags(payload: dict[str, object]) -> tuple[str, ...]:
    return tuple(
        f"genesis_alias:{value.casefold()}"
        for value in _genesis_external_keys(payload)
    )


def _linked_genesis_records(
    payload: dict[str, object],
    external_key_map: dict[str, TypedId],
    *,
    own_record_id: TypedId,
) -> tuple[TypedId, ...]:
    discovered: list[TypedId] = []

    def visit(name: str, value: object) -> None:
        normalized_name = name.casefold().replace(" ", "_")
        link_field = any(
            marker in normalized_name
            for marker in (
                "source_event",
                "linked_record",
                "memory_ref",
                "event_ref",
                "relationship_ref",
                "supersed",
            )
        )
        if isinstance(value, dict):
            for child_name, child_value in value.items():
                visit(child_name, child_value)
        elif isinstance(value, (list, tuple)):
            for item in value:
                visit(name, item)
        elif link_field and isinstance(value, str):
            for token in re.findall(r"\b[A-Za-z]+(?:-[A-Za-z]+)?-?\d+\b", value):
                linked = external_key_map.get(_normalize_external_key(token))
                if linked is not None and linked != own_record_id:
                    discovered.append(linked)

    for field_name, field_value in payload.items():
        visit(field_name, field_value)
    return tuple(dict.fromkeys(discovered))


def _normalize_external_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


def _normalized_term(value: str) -> str:
    return " ".join(value.split()).casefold()
