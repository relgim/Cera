"""Deterministic validation and staging for deferred derived records."""

from __future__ import annotations

import json
import re
from typing import Protocol

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
from cera.evidence.models import (
    EvidenceDocument,
    EvidenceEpistemicClass,
    EvidenceWorldMode,
    ExactEvidence,
)
from cera.evidence.service import EvidenceService
from cera.ids import IdKind, TypedId, deterministic_id, require_kind
from cera.schema import from_mapping
from cera.storage.models import AuthorityRecord, BranchState
from cera.storage.models import ReceiptCategory, ReceiptRecord

from .models import (
    ConsolidationBundle,
    ConsolidationExecutionResult,
    ConsolidationStagingResult,
    ConsolidationValidationReceipt,
    DERIVED_RECORD_TYPES,
    DerivedConsolidationRequest,
)


_RECORD_ID_KIND = {
    EvidenceRecordType.MEMORY: IdKind.MEMORY,
    EvidenceRecordType.RELATIONSHIP: IdKind.RELATIONSHIP,
    EvidenceRecordType.THREAD: IdKind.THREAD,
    EvidenceRecordType.DEVELOPMENT: IdKind.DEVELOPMENT,
}
_MEMORY_EPISTEMIC = {
    EvidenceEpistemicClass.CONSCIOUS_BELIEF,
    EvidenceEpistemicClass.PRIVATE_FEELING,
    EvidenceEpistemicClass.PRIVATE_BELIEF,
    EvidenceEpistemicClass.ALLEGATION,
}
_SUBJECTIVE_KINDS = {"fear", "betrayal", "shame", "anger", "self_blame", "other"}
_RELATIONSHIP_DIMENSIONS = {
    "trust",
    "fear",
    "obligation",
    "resentment",
    "secrecy",
    "reliance",
    "respect",
    "affection",
    "other",
}
_RELATIONSHIP_DIRECTIONS = {"increase", "decrease", "mixed", "stable"}
_DEVELOPMENT_KINDS = {
    "trust_adaptation",
    "boundary_response",
    "coping_tactic",
    "belief_shift",
    "habit_shift",
    "relationship_tactic",
    "other",
}
_CLINICAL_PATTERN = re.compile(
    r"\b(ptsd|post[- ]traumatic stress disorder|clinical diagnosis|diagnosed)\b",
    re.IGNORECASE,
)


class ConsolidationAuthorityStorePort(Protocol):
    def get_branch(self, branch_id: TypedId) -> BranchState: ...

    def get_world_genesis_revision(self, world_id: TypedId) -> TypedId: ...

    def authority_records_at_head(
        self, branch_id: TypedId, head_artifact_id: TypedId | None
    ) -> tuple[AuthorityRecord, ...]: ...


class ConsolidationValidator:
    def __init__(
        self,
        store: ConsolidationAuthorityStorePort,
        evidence_service: EvidenceService,
    ) -> None:
        self.store = store
        self.evidence_service = evidence_service

    def stage(
        self,
        request: DerivedConsolidationRequest,
        execution: ConsolidationExecutionResult,
        *,
        transaction_id: TypedId,
        idempotency_key: str,
    ) -> ConsolidationStagingResult:
        require_kind(transaction_id, IdKind.TRANSACTION, "transaction_id")
        self.evidence_service.validate_snapshot(request.snapshot)
        self._validate_execution_binding(request, execution)
        proposal = execution.proposal
        receipt_id = deterministic_id(
            IdKind.VALIDATION,
            "cera.consolidation.validation.v1",
            f"{request.request_sha256}|{proposal.proposal_sha256}",
        )
        if not proposal.records:
            receipt = ConsolidationValidationReceipt(
                schema_version=ConsolidationValidationReceipt.SCHEMA_VERSION,
                validation_receipt_id=receipt_id,
                request_sha256=request.request_sha256,
                proposal_sha256=proposal.proposal_sha256,
                snapshot_token=request.snapshot.snapshot_token,
                validated_record_ids=(),
                superseded_record_ids=(),
                status="validated_no_change",
                authority_store_writes=0,
            )
            return ConsolidationStagingResult(receipt, None)
        if len(proposal.records) > request.maximum_records:
            raise ContractValidationError("consolidation proposal exceeds record limit")
        eligible = set(request.eligible_record_types)
        if any(document.record_type not in eligible for document in proposal.records):
            raise ContractValidationError("proposal contains an ineligible derived record type")
        visible = self._visible_documents(request)
        self._validate_exact_evidence(request, visible)
        active = _active_documents(visible)
        exact_by_evidence_id = {value.evidence_id: value for value in request.exact_evidence}
        selected_exact = tuple(
            exact_by_evidence_id[value] for value in proposal.source_evidence_ids
        )
        selected_record_ids = {value.metadata.record_id for value in selected_exact}
        allowed_source_refs = {request.snapshot.branch_head_artifact_id}
        for evidence in selected_exact:
            allowed_source_refs.add(evidence.metadata.record_id)
            allowed_source_refs.update(evidence.metadata.source_refs)
        superseded_by_batch: set[TypedId] = set()
        for document in proposal.records:
            self._validate_document(
                request=request,
                document=document,
                active=active,
                selected_exact=selected_exact,
                selected_record_ids=selected_record_ids,
                allowed_source_refs=allowed_source_refs,
                superseded_by_batch=superseded_by_batch,
            )
            superseded_by_batch.update(document.supersedes)
        receipt = ConsolidationValidationReceipt(
            schema_version=ConsolidationValidationReceipt.SCHEMA_VERSION,
            validation_receipt_id=receipt_id,
            request_sha256=request.request_sha256,
            proposal_sha256=proposal.proposal_sha256,
            snapshot_token=request.snapshot.snapshot_token,
            validated_record_ids=tuple(value.record_id for value in proposal.records),
            superseded_record_ids=tuple(
                record_id for value in proposal.records for record_id in value.supersedes
            ),
            status="validated_pending_commit",
            authority_store_writes=0,
        )
        snapshot = request.snapshot
        assert snapshot.branch_head_artifact_id is not None
        bundle = ConsolidationBundle(
            schema_version=ConsolidationBundle.SCHEMA_VERSION,
            transaction_id=transaction_id,
            idempotency_key=idempotency_key,
            request_id=request.request_id,
            branch_id=snapshot.branch_id,
            expected_generation=snapshot.generation,
            expected_head_artifact_id=snapshot.branch_head_artifact_id,
            expected_authority_revision=snapshot.authority_revision,
            genesis_revision_id=snapshot.genesis_revision_id,
            snapshot_token=snapshot.snapshot_token,
            snapshot_binding_sha256=snapshot.binding_sha256,
            request_sha256=request.request_sha256,
            proposal_sha256=proposal.proposal_sha256,
            documents=proposal.records,
            validation_receipt_id=receipt.validation_receipt_id,
            reasoner_receipt_id=execution.receipt.provider_receipt_id,
            lookup_receipt_ids=request.lookup_receipt_ids,
            receipt_records=(
                *(
                    ReceiptRecord.from_payload(
                        receipt_id=value.lookup_receipt_id,
                        category=ReceiptCategory.LOOKUP,
                        schema_version=value.schema_version,
                        payload=value,
                    )
                    for value in request.lookup_receipts
                ),
                ReceiptRecord.from_payload(
                    receipt_id=execution.receipt.provider_receipt_id,
                    category=ReceiptCategory.PROVIDER,
                    schema_version=execution.receipt.schema_version,
                    payload=execution.receipt,
                ),
                ReceiptRecord.from_payload(
                    receipt_id=receipt.validation_receipt_id,
                    category=ReceiptCategory.VALIDATION,
                    schema_version=receipt.schema_version,
                    payload=receipt,
                ),
            )
            if request.lookup_receipts
            else (),
        )
        return ConsolidationStagingResult(receipt, bundle)

    @staticmethod
    def _validate_execution_binding(
        request: DerivedConsolidationRequest,
        execution: ConsolidationExecutionResult,
    ) -> None:
        receipt = execution.receipt
        proposal = execution.proposal
        if (
            receipt.request_sha256 != request.request_sha256
            or receipt.snapshot_token != request.snapshot.snapshot_token
            or receipt.snapshot_binding_sha256 != request.snapshot.binding_sha256
            or receipt.proposal_sha256 != proposal.proposal_sha256
            or receipt.source_evidence_ids != proposal.source_evidence_ids
            or receipt.lookup_receipt_ids != request.lookup_receipt_ids
        ):
            raise ContractValidationError("consolidation reasoner receipt binding mismatch")
        request_evidence_ids = {value.evidence_id for value in request.exact_evidence}
        if not set(proposal.source_evidence_ids).issubset(request_evidence_ids):
            raise ContractValidationError("proposal cites evidence outside the exact dossier")

    def _visible_documents(
        self, request: DerivedConsolidationRequest
    ) -> tuple[EvidenceDocument, ...]:
        snapshot = request.snapshot
        branch = self.store.get_branch(snapshot.branch_id)
        if (
            branch.world_id != snapshot.world_id
            or branch.head_artifact_id != snapshot.branch_head_artifact_id
            or branch.generation != snapshot.generation
            or branch.authority_revision != snapshot.authority_revision
        ):
            raise ContractValidationError("consolidation branch snapshot is stale")
        if self.store.get_world_genesis_revision(snapshot.world_id) != snapshot.genesis_revision_id:
            raise ContractValidationError("consolidation Genesis binding mismatch")
        documents: list[EvidenceDocument] = []
        for record in self.store.authority_records_at_head(
            snapshot.branch_id, snapshot.branch_head_artifact_id
        ):
            payload = json.loads(record.payload_json)
            if payload.get("schema_version") != EvidenceDocument.SCHEMA_VERSION:
                continue
            document = from_mapping(EvidenceDocument, payload)
            if document.record_id != record.record_id:
                raise ContractValidationError("authority record payload identity mismatch")
            documents.append(document)
        return tuple(documents)

    @staticmethod
    def _validate_exact_evidence(
        request: DerivedConsolidationRequest,
        visible: tuple[EvidenceDocument, ...],
    ) -> None:
        by_record_id = {value.record_id: value for value in visible}
        superseded_ids = {
            record_id for value in visible for record_id in value.supersedes
        }
        for exact in request.exact_evidence:
            metadata = exact.metadata
            document = by_record_id.get(metadata.record_id)
            if document is None:
                raise ContractValidationError(
                    "exact consolidation evidence is not current branch authority"
                )
            expected_evidence_id = deterministic_id(
                IdKind.EVIDENCE,
                "cera.evidence.record.v1",
                f"{request.snapshot.world_id}|{request.snapshot.genesis_revision_id}|"
                f"{document.record_id}",
            )
            if exact.evidence_id != expected_evidence_id:
                raise ContractValidationError("exact consolidation evidence ID is invalid")
            expected_status = (
                SupersessionStatus.SUPERSEDED
                if document.record_id in superseded_ids
                else SupersessionStatus.CURRENT
            )
            expected_metadata = (
                document.record_id,
                document.record_version,
                document.record_type,
                document.epistemic_class,
                document.truth_status,
                document.authority,
                document.owner_id,
                document.knowledge_owner_ids,
                document.visibility,
                document.content_class,
                document.genesis_revision_id,
                document.source_refs,
                document.valid_from_generation,
                document.valid_to_generation,
                document.branch_origin_id,
                expected_status,
                document.certainty,
                document.knowledge_route,
            )
            actual_metadata = (
                metadata.record_id,
                metadata.record_version,
                metadata.record_type,
                metadata.epistemic_class,
                metadata.truth_status,
                metadata.authority,
                metadata.owner_id,
                metadata.knowledge_owner_ids,
                metadata.visibility,
                metadata.content_class,
                metadata.genesis_revision_id,
                metadata.source_refs,
                metadata.valid_from_generation,
                metadata.valid_to_generation,
                metadata.branch_origin_id,
                metadata.supersession_status,
                metadata.certainty,
                metadata.knowledge_route,
            )
            if actual_metadata != expected_metadata or exact.subject_ids != document.subject_ids:
                raise ContractValidationError(
                    "exact consolidation evidence metadata does not match authority"
                )
            exact_sections = json.loads(exact.sections_json)
            authoritative_sections = json.loads(document.sections_json)
            if not exact_sections or any(
                key not in document.expandable_sections
                or key not in authoritative_sections
                or value != authoritative_sections[key]
                for key, value in exact_sections.items()
            ):
                raise ContractValidationError(
                    "exact consolidation evidence sections do not match authority"
                )

    def _validate_document(
        self,
        *,
        request: DerivedConsolidationRequest,
        document: EvidenceDocument,
        active: dict[TypedId, EvidenceDocument],
        selected_exact: tuple[ExactEvidence, ...],
        selected_record_ids: set[TypedId],
        allowed_source_refs: set[TypedId | None],
        superseded_by_batch: set[TypedId],
    ) -> None:
        snapshot = request.snapshot
        expected_kind = _RECORD_ID_KIND.get(document.record_type)
        if expected_kind is None or document.record_id.kind is not expected_kind:
            raise ContractValidationError("derived record ID kind does not match record type")
        if document.record_type not in DERIVED_RECORD_TYPES:
            raise ContractValidationError("consolidation cannot create direct facts")
        if document.branch_origin_id != snapshot.branch_id:
            raise ContractValidationError("derived record branch mismatch")
        if document.genesis_revision_id != snapshot.genesis_revision_id:
            raise ContractValidationError("derived record Genesis mismatch")
        if (
            document.valid_from_generation != snapshot.generation
            or document.valid_to_generation is not None
        ):
            raise ContractValidationError("derived record validity must start now and remain open")
        if not document.source_refs or not set(document.source_refs).issubset(allowed_source_refs):
            raise ContractValidationError("derived record has unexpanded or unauthorized source refs")
        if not set(document.linked_record_ids).issubset(selected_record_ids):
            raise ContractValidationError("linked records must be expanded exact evidence")
        if request.protected_user_id in document.knowledge_owner_ids:
            raise ContractValidationError("consolidation cannot author protected-user knowledge")
        if document.owner_id == request.protected_user_id:
            raise ContractValidationError("consolidation cannot author protected-user private state")
        expected_authority = (
            EvidenceAuthority.VALIDATED_DERIVED
            if snapshot.world_mode is EvidenceWorldMode.REAL
            else EvidenceAuthority.SYNTHETIC_FIXTURE
        )
        expected_truth = (
            None if snapshot.world_mode is EvidenceWorldMode.REAL else TruthStatus.NONCANONICAL
        )
        if document.authority is not expected_authority:
            raise ContractValidationError("derived record authority does not match world mode")
        if expected_truth is not None and document.truth_status is not expected_truth:
            raise ContractValidationError("synthetic derived record must remain noncanonical")
        if _CLINICAL_PATTERN.search(_document_text(document)):
            raise ContractValidationError("consolidation cannot create a clinical diagnosis")
        event_ids = {
            value.metadata.record_id
            for value in selected_exact
            if value.metadata.record_type is EvidenceRecordType.EVENT_FACT
            and value.metadata.supersession_status is SupersessionStatus.CURRENT
        }
        used_event_ids = event_ids.intersection(
            set(document.source_refs) | set(document.linked_record_ids)
        )
        sections = _sections(document)
        if document.record_type is EvidenceRecordType.MEMORY:
            declared_event_ids = self._validate_memory(request, document, sections)
            if not declared_event_ids.issubset(event_ids):
                raise ContractValidationError(
                    "memory event references must be expanded current evidence"
                )
            used_event_ids.update(declared_event_ids)
            actor_id = document.owner_id
        elif document.record_type is EvidenceRecordType.RELATIONSHIP:
            actor_id = self._validate_relationship(request, document, sections)
        elif document.record_type is EvidenceRecordType.THREAD:
            actor_id = self._validate_thread(request, document, sections)
        else:
            declared_event_ids = self._validate_development(request, document, sections)
            if not declared_event_ids.issubset(event_ids):
                raise ContractValidationError(
                    "development event references must be expanded current evidence"
                )
            used_event_ids.update(declared_event_ids)
            actor_id = document.owner_id
        if not used_event_ids:
            raise ContractValidationError("every derived record requires expanded event evidence")
        if actor_id is not None and not any(
            value.metadata.record_id in used_event_ids
            and _actor_may_use(actor_id, value)
            for value in selected_exact
        ):
            raise ContractValidationError("derived record actor does not own its event evidence")
        self._validate_supersession(
            document=document,
            sections=sections,
            active=active,
            selected_record_ids=selected_record_ids,
            superseded_by_batch=superseded_by_batch,
        )

    @staticmethod
    def _validate_memory(
        request: DerivedConsolidationRequest,
        document: EvidenceDocument,
        sections: dict[str, object],
    ) -> set[TypedId]:
        payload = _single_section(document, sections, "memory")
        _exact_keys(
            payload,
            {
                "memory_key",
                "objective_event_refs",
                "perceived_facts",
                "subjective_interpretations",
                "unknowns",
                "retrieval_tags",
                "trigger_cues",
                "clinical_diagnosis",
                "genesis_effect",
                "supersession_reason",
            },
            "memory",
        )
        if document.owner_id is None or document.visibility is not Visibility.OWNER_PRIVATE:
            raise ContractValidationError("character memory must be owner-private")
        if document.owner_id not in document.knowledge_owner_ids:
            raise ContractValidationError("memory owner must own its knowledge")
        if request.snapshot.world_mode is EvidenceWorldMode.REAL:
            if (
                document.epistemic_class not in _MEMORY_EPISTEMIC
                or document.truth_status is not TruthStatus.CHARACTER_OWNED
            ):
                raise ContractValidationError("memory must remain character-owned interpretation")
        if document.certainty is Certainty.ESTABLISHED:
            raise ContractValidationError("memory cannot establish objective truth")
        if document.knowledge_route not in {
            KnowledgeRoute.DIRECT,
            KnowledgeRoute.REPORTED,
            KnowledgeRoute.INFERRED,
        }:
            raise ContractValidationError("memory knowledge route is invalid")
        _required_text(payload.get("memory_key"), "memory_key")
        events = _typed_id_list(payload.get("objective_event_refs"), IdKind.EVENT, "events")
        _text_list(payload.get("perceived_facts"), "perceived_facts", require_non_empty=True)
        _text_list(payload.get("unknowns"), "unknowns")
        tags = _text_list(payload.get("retrieval_tags"), "retrieval_tags", require_non_empty=True)
        _text_list(payload.get("trigger_cues"), "trigger_cues")
        if not set(tags).issubset(set(document.tags)):
            raise ContractValidationError("memory retrieval tags must be indexed")
        interpretations = payload.get("subjective_interpretations")
        if not isinstance(interpretations, list):
            raise ContractValidationError("subjective_interpretations must be an array")
        for item in interpretations:
            if not isinstance(item, dict):
                raise ContractValidationError("subjective interpretation must be an object")
            _exact_keys(item, {"kind", "statement", "certainty"}, "interpretation")
            if item["kind"] not in _SUBJECTIVE_KINDS:
                raise ContractValidationError("unsupported subjective interpretation kind")
            _required_text(item["statement"], "interpretation statement")
            if item["certainty"] not in {"believed", "suspected", "feared"}:
                raise ContractValidationError("subjective certainty cannot be objective")
        _no_genesis_or_diagnosis(payload)
        return set(events)

    @staticmethod
    def _validate_relationship(
        request: DerivedConsolidationRequest,
        document: EvidenceDocument,
        sections: dict[str, object],
    ) -> TypedId:
        payload = _single_section(document, sections, "relationship")
        _exact_keys(
            payload,
            {
                "from_id",
                "to_id",
                "observations",
                "universal_score",
                "genesis_effect",
                "supersession_reason",
            },
            "relationship",
        )
        from_id = TypedId.parse(payload.get("from_id"), IdKind.CHARACTER)
        to_id = TypedId.parse(payload.get("to_id"), IdKind.CHARACTER)
        if from_id == request.protected_user_id:
            raise ContractValidationError("cannot author the protected user's relationship state")
        if document.owner_id != from_id or tuple(document.subject_ids) != (from_id, to_id):
            raise ContractValidationError("relationship direction does not match evidence envelope")
        if from_id not in document.knowledge_owner_ids:
            raise ContractValidationError("relationship owner must own its observations")
        if request.snapshot.world_mode is EvidenceWorldMode.REAL and (
            document.epistemic_class is not EvidenceEpistemicClass.VALIDATED_DERIVED
            or document.truth_status is not TruthStatus.DERIVED
        ):
            raise ContractValidationError("relationship evidence must be validated derived truth")
        observations = payload.get("observations")
        if not isinstance(observations, list) or not observations:
            raise ContractValidationError("relationship observations must be non-empty")
        for item in observations:
            if not isinstance(item, dict):
                raise ContractValidationError("relationship observation must be an object")
            _exact_keys(item, {"dimension", "direction", "statement"}, "observation")
            if item["dimension"] not in _RELATIONSHIP_DIMENSIONS:
                raise ContractValidationError("unsupported relationship dimension")
            if item["direction"] not in _RELATIONSHIP_DIRECTIONS:
                raise ContractValidationError("unsupported relationship direction")
            _required_text(item["statement"], "relationship statement")
        if payload.get("universal_score") is not None:
            raise ContractValidationError("relationship evidence cannot collapse to one score")
        _no_genesis_or_diagnosis(payload, diagnosis_required=False)
        return from_id

    @staticmethod
    def _validate_thread(
        request: DerivedConsolidationRequest,
        document: EvidenceDocument,
        sections: dict[str, object],
    ) -> TypedId | None:
        payload = _single_section(document, sections, "thread")
        _exact_keys(
            payload,
            {
                "thread_key",
                "status",
                "question",
                "resolution",
                "participant_ids",
                "genesis_effect",
                "supersession_reason",
            },
            "thread",
        )
        _required_text(payload.get("thread_key"), "thread_key")
        _required_text(payload.get("question"), "thread question")
        if payload.get("status") not in {"open", "resolved", "dormant"}:
            raise ContractValidationError("thread status is invalid")
        if (payload.get("status") == "resolved") != isinstance(payload.get("resolution"), str):
            raise ContractValidationError("thread resolution must match resolved status")
        if isinstance(payload.get("resolution"), str):
            _required_text(payload["resolution"], "thread resolution")
        participants = _typed_id_list(
            payload.get("participant_ids"), IdKind.CHARACTER, "thread participants"
        )
        if set(participants) != set(document.subject_ids):
            raise ContractValidationError("thread participants do not match subjects")
        if request.snapshot.world_mode is EvidenceWorldMode.REAL and (
            document.epistemic_class is not EvidenceEpistemicClass.VALIDATED_DERIVED
            or document.truth_status is not TruthStatus.DERIVED
        ):
            raise ContractValidationError("thread must be validated derived truth")
        _no_genesis_or_diagnosis(payload, diagnosis_required=False)
        return document.owner_id

    @staticmethod
    def _validate_development(
        request: DerivedConsolidationRequest,
        document: EvidenceDocument,
        sections: dict[str, object],
    ) -> set[TypedId]:
        payload = _single_section(document, sections, "development")
        _exact_keys(
            payload,
            {
                "owner_id",
                "kind",
                "tendency",
                "strength",
                "scope",
                "first_evidence_refs",
                "last_evidence_refs",
                "contradiction_evidence_refs",
                "clinical_diagnosis",
                "genesis_effect",
                "supersession_reason",
            },
            "development",
        )
        owner_id = TypedId.parse(payload.get("owner_id"), IdKind.CHARACTER)
        if owner_id == request.protected_user_id or document.owner_id != owner_id:
            raise ContractValidationError("development owner is invalid")
        if document.visibility is not Visibility.OWNER_PRIVATE:
            raise ContractValidationError("character development must be owner-private")
        if owner_id not in document.knowledge_owner_ids:
            raise ContractValidationError("development owner must own its evidence")
        if payload.get("kind") not in _DEVELOPMENT_KINDS:
            raise ContractValidationError("unsupported development kind")
        _required_text(payload.get("tendency"), "development tendency")
        if payload.get("strength") not in {"emerging", "supported", "strong"}:
            raise ContractValidationError("development strength is invalid")
        scope = payload.get("scope")
        _required_text(scope, "development scope")
        if str(scope).casefold() in {"global", "identity", "permanent"}:
            raise ContractValidationError("development cannot become an identity rewrite")
        first = _typed_id_list(
            payload.get("first_evidence_refs"), IdKind.EVENT, "first evidence refs"
        )
        last = _typed_id_list(
            payload.get("last_evidence_refs"), IdKind.EVENT, "last evidence refs"
        )
        contradictions = _typed_id_list(
            payload.get("contradiction_evidence_refs"),
            IdKind.EVENT,
            "contradiction evidence refs",
            allow_empty=True,
        )
        if payload.get("strength") == "strong" and len(set(first + last)) < 2:
            raise ContractValidationError("strong development requires repeated event evidence")
        if request.snapshot.world_mode is EvidenceWorldMode.REAL and (
            document.epistemic_class is not EvidenceEpistemicClass.VALIDATED_DERIVED
            or document.truth_status is not TruthStatus.DERIVED
        ):
            raise ContractValidationError("development must be validated derived truth")
        _no_genesis_or_diagnosis(payload)
        return set(first + last + contradictions)

    @staticmethod
    def _validate_supersession(
        *,
        document: EvidenceDocument,
        sections: dict[str, object],
        active: dict[TypedId, EvidenceDocument],
        selected_record_ids: set[TypedId],
        superseded_by_batch: set[TypedId],
    ) -> None:
        section = sections[document.record_type.value]
        assert isinstance(section, dict)
        reason = section.get("supersession_reason")
        if not document.supersedes:
            if document.record_version != 1 or reason is not None:
                raise ContractValidationError("new derived record must begin at version one")
            return
        if len(document.supersedes) != 1:
            raise ContractValidationError("derived supersession must name one predecessor")
        target_id = document.supersedes[0]
        if target_id in superseded_by_batch:
            raise ContractValidationError("two proposal records supersede the same predecessor")
        if target_id not in selected_record_ids:
            raise ContractValidationError("supersession predecessor must be expanded evidence")
        target = active.get(target_id)
        if target is None:
            raise ContractValidationError("supersession target is absent or already superseded")
        if target.record_type is not document.record_type:
            raise ContractValidationError("supersession cannot change record type")
        if document.record_version != target.record_version + 1:
            raise ContractValidationError("supersession must advance record version by one")
        if target.owner_id != document.owner_id or target.subject_ids != document.subject_ids:
            raise ContractValidationError("supersession cannot change owner or subjects")
        _required_text(reason, "supersession_reason")
        target_sections = json.loads(target.sections_json)
        target_payload = target_sections.get(document.record_type.value, target_sections)
        if not isinstance(target_payload, dict):
            raise ContractValidationError("supersession target has unusable sections")
        identity_fields = {
            EvidenceRecordType.MEMORY: ("memory_key",),
            EvidenceRecordType.RELATIONSHIP: ("from_id", "to_id"),
            EvidenceRecordType.THREAD: ("thread_key",),
            EvidenceRecordType.DEVELOPMENT: ("owner_id", "kind", "scope"),
        }[document.record_type]
        for field in identity_fields:
            if field in target_payload and target_payload[field] != section.get(field):
                raise ContractValidationError("supersession changed derived-record identity")


def _active_documents(
    documents: tuple[EvidenceDocument, ...]
) -> dict[TypedId, EvidenceDocument]:
    superseded = {record_id for value in documents for record_id in value.supersedes}
    return {value.record_id: value for value in documents if value.record_id not in superseded}


def _sections(document: EvidenceDocument) -> dict[str, object]:
    decoded = json.loads(document.sections_json)
    if not isinstance(decoded, dict):
        raise ContractValidationError("derived sections must be an object")
    return decoded


def _single_section(
    document: EvidenceDocument, sections: dict[str, object], expected: str
) -> dict[str, object]:
    if set(sections) != {expected} or document.expandable_sections != (expected,):
        raise ContractValidationError("derived record must expose one typed section")
    payload = sections[expected]
    if not isinstance(payload, dict):
        raise ContractValidationError("derived typed section must be an object")
    return payload


def _exact_keys(payload: dict[str, object], expected: set[str], label: str) -> None:
    if set(payload) != expected:
        raise ContractValidationError(f"{label} section fields do not match its contract")


def _required_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContractValidationError(f"{field_name} must be non-empty")
    return value


def _text_list(
    value: object, field_name: str, *, require_non_empty: bool = False
) -> tuple[str, ...]:
    if not isinstance(value, list) or (require_non_empty and not value):
        raise ContractValidationError(f"{field_name} must be an array")
    result = tuple(_required_text(item, field_name) for item in value)
    if len(result) != len(set(result)):
        raise ContractValidationError(f"{field_name} must not contain duplicates")
    return result


def _typed_id_list(
    value: object,
    kind: IdKind,
    field_name: str,
    *,
    allow_empty: bool = False,
) -> tuple[TypedId, ...]:
    if not isinstance(value, list) or (not allow_empty and not value):
        raise ContractValidationError(f"{field_name} must be an array")
    result = tuple(TypedId.parse(item, kind) for item in value)
    if len(result) != len(set(result)):
        raise ContractValidationError(f"{field_name} must not contain duplicates")
    return result


def _no_genesis_or_diagnosis(
    payload: dict[str, object], *, diagnosis_required: bool = True
) -> None:
    if payload.get("genesis_effect") != "none":
        raise ContractValidationError("derived records cannot modify Genesis")
    if diagnosis_required and payload.get("clinical_diagnosis") != "not_assessed":
        raise ContractValidationError("derived record cannot create a clinical diagnosis")


def _document_text(document: EvidenceDocument) -> str:
    return " ".join(
        (
            document.title,
            document.abstract,
            document.claim,
            document.sections_json,
            " ".join(document.tags),
        )
    )


def _actor_may_use(actor_id: TypedId, evidence: ExactEvidence) -> bool:
    metadata = evidence.metadata
    if metadata.visibility is Visibility.OWNER_PRIVATE:
        return metadata.owner_id == actor_id
    if metadata.knowledge_owner_ids:
        return actor_id in metadata.knowledge_owner_ids
    if metadata.visibility is Visibility.SYSTEM_PRIVATE:
        return actor_id in evidence.subject_ids
    return True
