"""Python-owned seed dossier assembly and immutable-snapshot receipts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar

from cera.errors import ContractValidationError, EvidenceServiceError
from cera.evidence import (
    EvidenceAmbiguityPolicy,
    EvidenceFetchRequest,
    EvidenceLookupReceipt,
    EvidenceObligation,
    EvidenceObligationKind,
    EvidenceSearchRequest,
    EvidenceService,
    EvidenceSnapshot,
    ExactEvidence,
)
from cera.ids import IdKind, TypedId, deterministic_id, require_kind
from cera.schema import require_schema
from cera.serialization import domain_sha256, re_is_sha256

from .models import ReasonerSeedDossier


class EvidenceObligationStatus(StrEnum):
    SATISFIED = "satisfied"
    UNSATISFIED = "unsatisfied"
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True, slots=True)
class EvidenceObligationResolution:
    obligation_id: TypedId
    status: EvidenceObligationStatus
    evidence_ids: tuple[TypedId, ...]
    reason: str

    def __post_init__(self) -> None:
        require_kind(self.obligation_id, IdKind.OBLIGATION, "obligation_id")
        for evidence_id in self.evidence_ids:
            require_kind(evidence_id, IdKind.EVIDENCE, "evidence_ids")
        if not self.reason.strip():
            raise ContractValidationError("obligation resolution reason is required")
        if self.status is EvidenceObligationStatus.SATISFIED:
            if not self.evidence_ids:
                raise ContractValidationError(
                    "satisfied evidence obligation requires exact evidence"
                )
        elif self.evidence_ids:
            raise ContractValidationError(
                "unresolved evidence obligation cannot imply accepted evidence"
            )


@dataclass(frozen=True, slots=True)
class SeedDossierReceipt:
    SCHEMA_VERSION: ClassVar[str] = "cera.seed_dossier_receipt.v1"

    schema_version: str
    seed_receipt_id: TypedId
    snapshot_token: TypedId
    snapshot_binding_sha256: str
    reauthorized_seed_hashes: tuple[str, ...]
    obligation_resolutions: tuple[EvidenceObligationResolution, ...]
    lookup_receipt_ids: tuple[TypedId, ...]
    exact_evidence_ids: tuple[TypedId, ...]
    authoritative_store_writes: int

    def __post_init__(self) -> None:
        require_schema(self.schema_version, self.SCHEMA_VERSION, type(self).__name__)
        require_kind(self.seed_receipt_id, IdKind.SEED_RECEIPT, "seed_receipt_id")
        require_kind(self.snapshot_token, IdKind.SNAPSHOT, "snapshot_token")
        if not re_is_sha256(self.snapshot_binding_sha256):
            raise ContractValidationError("seed snapshot binding must be SHA-256")
        if any(not re_is_sha256(value) for value in self.reauthorized_seed_hashes):
            raise ContractValidationError("seed evidence hashes must be SHA-256")
        for receipt_id in self.lookup_receipt_ids:
            require_kind(receipt_id, IdKind.LOOKUP_RECEIPT, "lookup_receipt_ids")
        for evidence_id in self.exact_evidence_ids:
            require_kind(evidence_id, IdKind.EVIDENCE, "exact_evidence_ids")
        _unique(self.lookup_receipt_ids, "seed lookup receipts")
        _unique(self.exact_evidence_ids, "seed exact evidence")
        if self.authoritative_store_writes != 0:
            raise ContractValidationError("seed assembly cannot write authority")

    @property
    def receipt_sha256(self) -> str:
        return domain_sha256("cera.seed_dossier_receipt.v1", self)


@dataclass(frozen=True, slots=True)
class SeedDossierAssemblyRequest:
    snapshot: EvidenceSnapshot
    aware_character_ids: tuple[TypedId, ...]
    preexpanded_exact_evidence: tuple[ExactEvidence, ...]
    evidence_obligations: tuple[EvidenceObligation, ...]
    scene_anchors: tuple[str, ...]
    explicit_unknowns: tuple[str, ...]
    prohibited_inferences: tuple[str, ...]

    def __post_init__(self) -> None:
        for character_id in self.aware_character_ids:
            require_kind(character_id, IdKind.CHARACTER, "aware_character_ids")
        _unique(self.aware_character_ids, "aware characters")
        _unique(
            (value.evidence_id for value in self.preexpanded_exact_evidence),
            "preexpanded evidence",
        )
        _unique(
            (value.obligation_id for value in self.evidence_obligations),
            "evidence obligations",
        )


@dataclass(frozen=True, slots=True)
class SeedDossierAssemblyResult:
    dossier: ReasonerSeedDossier
    receipt: SeedDossierReceipt
    lookup_receipts: tuple[EvidenceLookupReceipt, ...]

    @property
    def ready(self) -> bool:
        return all(
            value.status is EvidenceObligationStatus.SATISFIED
            for value in self.receipt.obligation_resolutions
        )


class SeedDossierAssembler:
    """Reauthorizes seeds and resolves bounded obligations before Codex."""

    def __init__(self, evidence_service: EvidenceService) -> None:
        self.evidence_service = evidence_service

    def assemble(
        self,
        request: SeedDossierAssemblyRequest,
    ) -> SeedDossierAssemblyResult:
        self.evidence_service.validate_snapshot(request.snapshot)
        exact_by_id: dict[TypedId, ExactEvidence] = {}
        reauthorized_hashes: list[str] = []
        for supplied in request.preexpanded_exact_evidence:
            accepted = self.evidence_service.reauthorize_exact_evidence(
                request.snapshot,
                supplied,
            )
            exact_by_id[accepted.evidence_id] = accepted
            reauthorized_hashes.append(
                domain_sha256("cera.seed_exact_evidence.v1", accepted)
            )

        resolutions: list[EvidenceObligationResolution] = []
        lookup_receipts: list[EvidenceLookupReceipt] = []
        for obligation in request.evidence_obligations:
            resolution, exact, receipts = self._resolve(
                request.snapshot,
                obligation,
            )
            resolutions.append(resolution)
            lookup_receipts.extend(receipts)
            for item in exact:
                exact_by_id[item.evidence_id] = item

        receipt_core = {
            "snapshot_token": str(request.snapshot.snapshot_token),
            "snapshot_binding_sha256": request.snapshot.binding_sha256,
            "reauthorized_seed_hashes": tuple(reauthorized_hashes),
            "obligation_resolutions": tuple(resolutions),
            "lookup_receipt_ids": tuple(
                value.lookup_receipt_id for value in lookup_receipts
            ),
            "exact_evidence_ids": tuple(str(value) for value in exact_by_id),
        }
        seed_receipt_id = deterministic_id(
            IdKind.SEED_RECEIPT,
            "cera.seed_dossier_receipt.v1",
            domain_sha256("cera.seed_dossier_receipt.binding.v1", receipt_core),
        )
        receipt = SeedDossierReceipt(
            schema_version=SeedDossierReceipt.SCHEMA_VERSION,
            seed_receipt_id=seed_receipt_id,
            snapshot_token=request.snapshot.snapshot_token,
            snapshot_binding_sha256=request.snapshot.binding_sha256,
            reauthorized_seed_hashes=tuple(reauthorized_hashes),
            obligation_resolutions=tuple(resolutions),
            lookup_receipt_ids=tuple(
                value.lookup_receipt_id for value in lookup_receipts
            ),
            exact_evidence_ids=tuple(exact_by_id),
            authoritative_store_writes=0,
        )
        dossier = ReasonerSeedDossier(
            snapshot_token=request.snapshot.snapshot_token,
            aware_character_ids=request.aware_character_ids,
            exact_seed_evidence=tuple(exact_by_id.values()),
            scene_anchors=request.scene_anchors,
            explicit_unknowns=request.explicit_unknowns,
            prohibited_inferences=request.prohibited_inferences,
            evidence_obligations=request.evidence_obligations,
            seed_receipt_id=receipt.seed_receipt_id,
        )
        return SeedDossierAssemblyResult(
            dossier=dossier,
            receipt=receipt,
            lookup_receipts=tuple(lookup_receipts),
        )

    def _resolve(
        self,
        snapshot: EvidenceSnapshot,
        obligation: EvidenceObligation,
    ) -> tuple[
        EvidenceObligationResolution,
        tuple[ExactEvidence, ...],
        tuple[EvidenceLookupReceipt, ...],
    ]:
        try:
            if obligation.kind is EvidenceObligationKind.REQUIRED_RECORD:
                assert obligation.record_id is not None
                evidence_id = deterministic_id(
                    IdKind.EVIDENCE,
                    "cera.evidence.record.v1",
                    f"{snapshot.world_id}|{snapshot.genesis_revision_id}|{obligation.record_id}",
                )
                references = (evidence_id,)
                receipts: list[EvidenceLookupReceipt] = []
            else:
                if obligation.kind is EvidenceObligationKind.QUERY_PLAN:
                    assert obligation.query_plan is not None
                    search = self.evidence_service.search_query_plan(
                        snapshot,
                        obligation.query_plan,
                    )
                else:
                    search = self.evidence_service.search_evidence(
                        snapshot,
                        EvidenceSearchRequest(
                            entity_ids=(
                                (obligation.subject_id,)
                                if obligation.subject_id is not None
                                else ()
                            ),
                            record_types=(
                                (obligation.record_type,)
                                if obligation.record_type is not None
                                else ()
                            ),
                            limit=self.evidence_service.limits.maximum_fetched_records,
                        ),
                    )
                receipts = [search.receipt]
                if (
                    obligation.query_plan is not None
                    and obligation.query_plan.ambiguity_policy
                    is EvidenceAmbiguityPolicy.REQUIRE_UNAMBIGUOUS
                    and len(search.references) != 1
                ):
                    return (
                        EvidenceObligationResolution(
                            obligation_id=obligation.obligation_id,
                            status=EvidenceObligationStatus.AMBIGUOUS,
                            evidence_ids=(),
                            reason="bounded query did not resolve to exactly one reference",
                        ),
                        (),
                        tuple(receipts),
                    )
                references = tuple(value.evidence_id for value in search.references)
            if not references:
                return (
                    EvidenceObligationResolution(
                        obligation_id=obligation.obligation_id,
                        status=EvidenceObligationStatus.UNSATISFIED,
                        evidence_ids=(),
                        reason="no authorized reference satisfied the obligation",
                    ),
                    (),
                    tuple(receipts),
                )
            fetched = self.evidence_service.fetch_evidence(
                snapshot,
                EvidenceFetchRequest(
                    evidence_ids=references,
                    sections=obligation.required_sections,
                ),
            )
            receipts.append(fetched.receipt)
            if len(fetched.exact_records) != len(references):
                return (
                    EvidenceObligationResolution(
                        obligation_id=obligation.obligation_id,
                        status=EvidenceObligationStatus.UNSATISFIED,
                        evidence_ids=(),
                        reason="exact fetch was truncated before obligation completion",
                    ),
                    (),
                    tuple(receipts),
                )
            return (
                EvidenceObligationResolution(
                    obligation_id=obligation.obligation_id,
                    status=EvidenceObligationStatus.SATISFIED,
                    evidence_ids=tuple(
                        value.evidence_id for value in fetched.exact_records
                    ),
                    reason="exact evidence authorized under immutable snapshot",
                ),
                fetched.exact_records,
                tuple(receipts),
            )
        except EvidenceServiceError as exc:
            return (
                EvidenceObligationResolution(
                    obligation_id=obligation.obligation_id,
                    status=EvidenceObligationStatus.UNSATISFIED,
                    evidence_ids=(),
                    reason=f"evidence service rejected obligation: {exc.code.value}",
                ),
                (),
                (),
            )


def _unique(values, field_name: str) -> None:
    items = list(values)
    if len(items) != len(set(items)):
        raise ContractValidationError(f"{field_name} must not contain duplicates")
