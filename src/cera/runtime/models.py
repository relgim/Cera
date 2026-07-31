"""Provider-neutral live-shaped turn orchestration contracts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from cera.adult_craft.models import (
    AdultCraftSelectionReceipt,
    BeatScopedRepairReceipt,
    SemanticSpecificityReceipt,
    SemanticSpecificityResult,
    SpecificityValidationReceipt,
)
from cera.composer import (
    AdultComposerBinding,
    ArtifactPublicationMode,
    ComposerContextAssemblyResult,
    ComposerContextBlock,
    ComposerContinuityReference,
    ComposerExecutionResult,
    ComposerSourcePacket,
)
from cera.contracts import (
    AcceptedStoryArtifact,
    CreatorRevisionDirective,
    SceneDepthMode,
)
from cera.evidence import EvidenceLookupReceipt
from cera.errors import ContractValidationError
from cera.ids import IdKind, TypedId, deterministic_id, require_kind
from cera.ingress import IntentInterpretationReceipt
from cera.reasoner import ReasonerExecutionResult
from cera.reasoner import SeedDossierReceipt
from cera.realization import SceneRealizationVerificationResult
from cera.schema import require_schema
from cera.serialization import domain_sha256, re_is_sha256


@dataclass(frozen=True, slots=True)
class IngressPublicationEvidence:
    """Safe raw-ingress and seed evidence retained with an accepted turn.

    Exact source text and exact seed sections remain outside this receipt.  The
    aggregate preserves the interpretation and immutable-snapshot seed proof,
    while its lookup receipts preserve how obligation-driven exact evidence was
    authorized.  A validation-kind aggregate ID is used because seed receipt
    IDs are a domain identity rather than a transactional receipt category.
    """

    SCHEMA_VERSION: ClassVar[str] = "cera.ingress_publication_evidence.v1"

    schema_version: str
    receipt_id: TypedId
    request_id: TypedId
    snapshot_token: TypedId
    source_sha256: str
    reasoner_request_sha256: str
    interpretation_receipt: IntentInterpretationReceipt
    seed_receipt: SeedDossierReceipt
    seed_lookup_receipts: tuple[EvidenceLookupReceipt, ...]
    authoritative_store_writes: int

    def __post_init__(self) -> None:
        require_schema(self.schema_version, self.SCHEMA_VERSION, type(self).__name__)
        require_kind(self.receipt_id, IdKind.VALIDATION, "receipt_id")
        require_kind(self.request_id, IdKind.REQUEST, "request_id")
        require_kind(self.snapshot_token, IdKind.SNAPSHOT, "snapshot_token")
        if not re_is_sha256(self.source_sha256) or not re_is_sha256(
            self.reasoner_request_sha256
        ):
            raise ContractValidationError(
                "ingress publication evidence requires a Reasoner request hash"
            )
        if self.seed_receipt.snapshot_token != self.snapshot_token:
            raise ContractValidationError(
                "ingress publication evidence changed the seed snapshot"
            )
        lookup_ids = tuple(
            value.lookup_receipt_id for value in self.seed_lookup_receipts
        )
        if lookup_ids != self.seed_receipt.lookup_receipt_ids:
            raise ContractValidationError(
                "ingress publication evidence lookup payloads do not match the seed receipt"
            )
        if any(
            value.snapshot_token != self.snapshot_token
            for value in self.seed_lookup_receipts
        ):
            raise ContractValidationError(
                "ingress publication evidence contains a foreign snapshot receipt"
            )
        if (
            self.authoritative_store_writes != 0
            or self.interpretation_receipt.authoritative_store_writes != 0
            or self.seed_receipt.authoritative_store_writes != 0
            or any(
                value.authoritative_store_writes != 0
                for value in self.seed_lookup_receipts
            )
        ):
            raise ContractValidationError(
                "ingress publication evidence cannot claim authority writes"
            )
        expected = deterministic_id(
            IdKind.VALIDATION,
            "cera.ingress_publication_evidence.v1",
            self._binding_sha256(),
        )
        if self.receipt_id != expected:
            raise ContractValidationError(
                "ingress publication evidence receipt ID is not content-bound"
            )

    @classmethod
    def create(
        cls,
        *,
        request_id: TypedId,
        source_sha256: str,
        reasoner_request_sha256: str,
        interpretation_receipt: IntentInterpretationReceipt,
        seed_receipt: SeedDossierReceipt,
        seed_lookup_receipts: tuple[EvidenceLookupReceipt, ...],
    ) -> "IngressPublicationEvidence":
        core = {
            "request_id": request_id,
            "snapshot_token": seed_receipt.snapshot_token,
            "source_sha256": source_sha256,
            "reasoner_request_sha256": reasoner_request_sha256,
            "interpretation_receipt": interpretation_receipt,
            "seed_receipt": seed_receipt,
            "seed_lookup_receipts": seed_lookup_receipts,
            "authoritative_store_writes": 0,
        }
        binding = domain_sha256(
            "cera.ingress_publication_evidence.binding.v1", core
        )
        return cls(
            schema_version=cls.SCHEMA_VERSION,
            receipt_id=deterministic_id(
                IdKind.VALIDATION,
                "cera.ingress_publication_evidence.v1",
                binding,
            ),
            request_id=request_id,
            snapshot_token=seed_receipt.snapshot_token,
            source_sha256=source_sha256,
            reasoner_request_sha256=reasoner_request_sha256,
            interpretation_receipt=interpretation_receipt,
            seed_receipt=seed_receipt,
            seed_lookup_receipts=seed_lookup_receipts,
            authoritative_store_writes=0,
        )

    def _binding_sha256(self) -> str:
        return domain_sha256(
            "cera.ingress_publication_evidence.binding.v1",
            {
                "request_id": self.request_id,
                "snapshot_token": self.snapshot_token,
                "source_sha256": self.source_sha256,
                "reasoner_request_sha256": self.reasoner_request_sha256,
                "interpretation_receipt": self.interpretation_receipt,
                "seed_receipt": self.seed_receipt,
                "seed_lookup_receipts": self.seed_lookup_receipts,
                "authoritative_store_writes": self.authoritative_store_writes,
            },
        )

    @property
    def receipt_sha256(self) -> str:
        return domain_sha256(self.SCHEMA_VERSION, self)


@dataclass(frozen=True, slots=True)
class ComposerRequestPlan:
    source_packet: ComposerSourcePacket
    scene_scope: str
    response_profile_version: str
    continuity_references: tuple[ComposerContinuityReference, ...]
    creator_event_coverage_required: bool
    hard_boundaries: tuple[str, ...]
    adult_binding: AdultComposerBinding | None
    established_scene_context: tuple[str, ...] = ()
    craft_blocks: tuple[ComposerContextBlock, ...] = ()
    publication_mode: ArtifactPublicationMode = ArtifactPublicationMode.APPEND
    publication_parent_artifact_id: TypedId | None = None
    replaces_artifact_id: TypedId | None = None
    scene_depth_mode: SceneDepthMode = SceneDepthMode.AUTO
    creator_revision: CreatorRevisionDirective | None = None

    def __post_init__(self) -> None:
        if not self.scene_scope.strip() or not self.response_profile_version.strip():
            raise ContractValidationError("Composer request plan text must be non-empty")
        if not self.hard_boundaries:
            raise ContractValidationError("Composer request plan requires hard boundaries")
        if any(not value.strip() for value in self.established_scene_context):
            raise ContractValidationError(
                "Composer established scene context cannot contain blanks"
            )
        if len(set(self.established_scene_context)) != len(
            self.established_scene_context
        ):
            raise ContractValidationError(
                "Composer established scene context contains duplicates"
            )
        if self.publication_mode is ArtifactPublicationMode.APPEND:
            if (
                self.publication_parent_artifact_id is not None
                or self.replaces_artifact_id is not None
            ):
                raise ContractValidationError(
                    "append Composer plan cannot carry regeneration topology"
                )
        else:
            if self.replaces_artifact_id is None:
                raise ContractValidationError(
                    "regeneration Composer plan requires a replacement target"
                )
            require_kind(self.replaces_artifact_id, IdKind.ARTIFACT, "replaces_artifact_id")
            if self.publication_parent_artifact_id is not None:
                require_kind(
                    self.publication_parent_artifact_id,
                    IdKind.ARTIFACT,
                    "publication_parent_artifact_id",
                )


@dataclass(frozen=True, slots=True)
class LiveShapedTurnReceipt:
    SCHEMA_VERSION: ClassVar[str] = "cera.live_shaped_turn_receipt.v5"

    schema_version: str
    receipt_id: TypedId
    request_id: TypedId
    branch_id: TypedId
    generation_id: TypedId
    snapshot_token: TypedId
    reasoner_provider_receipt_id: TypedId
    reasoner_receipt_sha256: str
    context_assembly_receipt_id: TypedId
    context_assembly_receipt_sha256: str
    composer_provider_receipt_id: TypedId
    composer_receipt_sha256: str
    realization_verification_id: TypedId
    realization_verification_receipt_sha256: str
    final_acceptance_receipt_id: TypedId
    final_acceptance_receipt_sha256: str
    accepted_artifact_id: TypedId
    accepted_artifact_sha256: str
    evidence_lookup_receipt_ids: tuple[TypedId, ...]
    external_provider_calls: int
    story_authority_writes: int
    story_state_committed: bool
    adult_craft_selection_id: TypedId | None = None
    adult_craft_selection_sha256: str | None = None
    specificity_validation_receipt_id: TypedId | None = None
    specificity_validation_receipt_sha256: str | None = None
    semantic_specificity_receipt_id: TypedId | None = None
    semantic_specificity_receipt_sha256: str | None = None
    beat_repair_receipt_id: TypedId | None = None
    beat_repair_receipt_sha256: str | None = None

    def __post_init__(self) -> None:
        require_schema(self.schema_version, self.SCHEMA_VERSION, type(self).__name__)
        require_kind(self.receipt_id, IdKind.VALIDATION, "receipt_id")
        require_kind(self.request_id, IdKind.REQUEST, "request_id")
        require_kind(self.branch_id, IdKind.BRANCH, "branch_id")
        require_kind(self.generation_id, IdKind.GENERATION, "generation_id")
        require_kind(self.snapshot_token, IdKind.SNAPSHOT, "snapshot_token")
        require_kind(
            self.reasoner_provider_receipt_id,
            IdKind.PROVIDER_RECEIPT,
            "reasoner_provider_receipt_id",
        )
        require_kind(
            self.context_assembly_receipt_id,
            IdKind.VALIDATION,
            "context_assembly_receipt_id",
        )
        require_kind(
            self.composer_provider_receipt_id,
            IdKind.PROVIDER_RECEIPT,
            "composer_provider_receipt_id",
        )
        require_kind(
            self.realization_verification_id,
            IdKind.REALIZATION_VERIFICATION,
            "realization_verification_id",
        )
        require_kind(
            self.final_acceptance_receipt_id,
            IdKind.VALIDATION,
            "final_acceptance_receipt_id",
        )
        require_kind(self.accepted_artifact_id, IdKind.ARTIFACT, "accepted_artifact_id")
        for value in (
            self.reasoner_receipt_sha256,
            self.context_assembly_receipt_sha256,
            self.composer_receipt_sha256,
            self.realization_verification_receipt_sha256,
            self.final_acceptance_receipt_sha256,
            self.accepted_artifact_sha256,
        ):
            if not re_is_sha256(value):
                raise ContractValidationError("live-shaped turn receipt hash is invalid")
        for lookup_id in self.evidence_lookup_receipt_ids:
            require_kind(lookup_id, IdKind.LOOKUP_RECEIPT, "evidence_lookup_receipt_ids")
        if len(self.evidence_lookup_receipt_ids) != len(
            set(self.evidence_lookup_receipt_ids)
        ):
            raise ContractValidationError("live-shaped lookup receipt IDs must be unique")
        if self.external_provider_calls < 0:
            raise ContractValidationError("live-shaped provider call count cannot be negative")
        if self.story_authority_writes != 0 or self.story_state_committed:
            raise ContractValidationError("live-shaped turn receipt cannot claim a story commit")
        adult_fields = (
            self.adult_craft_selection_id,
            self.adult_craft_selection_sha256,
            self.specificity_validation_receipt_id,
            self.specificity_validation_receipt_sha256,
            self.semantic_specificity_receipt_id,
            self.semantic_specificity_receipt_sha256,
        )
        if any(value is not None for value in adult_fields):
            if any(value is None for value in adult_fields):
                raise ContractValidationError("adult live-shaped receipt fields must be paired")
            assert self.adult_craft_selection_id is not None
            assert self.specificity_validation_receipt_id is not None
            assert self.semantic_specificity_receipt_id is not None
            assert self.adult_craft_selection_sha256 is not None
            assert self.specificity_validation_receipt_sha256 is not None
            assert self.semantic_specificity_receipt_sha256 is not None
            require_kind(self.adult_craft_selection_id, IdKind.CRAFT_SELECTION, "adult_craft_selection_id")
            require_kind(
                self.specificity_validation_receipt_id,
                IdKind.SPECIFICITY_VALIDATION,
                "specificity_validation_receipt_id",
            )
            require_kind(
                self.semantic_specificity_receipt_id,
                IdKind.SEMANTIC_SPECIFICITY,
                "semantic_specificity_receipt_id",
            )
            for value in (
                self.adult_craft_selection_sha256,
                self.specificity_validation_receipt_sha256,
                self.semantic_specificity_receipt_sha256,
            ):
                if not re_is_sha256(value):
                    raise ContractValidationError("adult live-shaped receipt hash is invalid")
        repair_fields = (self.beat_repair_receipt_id, self.beat_repair_receipt_sha256)
        if any(value is not None for value in repair_fields):
            if any(value is None for value in repair_fields) or self.adult_craft_selection_id is None:
                raise ContractValidationError("beat-repair receipt requires complete adult evidence")
            assert self.beat_repair_receipt_id is not None
            assert self.beat_repair_receipt_sha256 is not None
            require_kind(self.beat_repair_receipt_id, IdKind.BEAT_REPAIR, "beat_repair_receipt_id")
            if not re_is_sha256(self.beat_repair_receipt_sha256):
                raise ContractValidationError("beat repair receipt hash is invalid")


@dataclass(frozen=True, slots=True)
class AdultCraftTurnEvidence:
    selection_receipt: AdultCraftSelectionReceipt
    initial_specificity_receipt: SpecificityValidationReceipt
    final_specificity_receipt: SpecificityValidationReceipt
    initial_semantic_result: SemanticSpecificityResult
    initial_semantic_receipt: SemanticSpecificityReceipt
    final_semantic_result: SemanticSpecificityResult
    final_semantic_receipt: SemanticSpecificityReceipt
    beat_repair_receipt: BeatScopedRepairReceipt | None

    def __post_init__(self) -> None:
        if self.final_specificity_receipt.status != "accepted":
            raise ContractValidationError("adult turn evidence requires accepted final specificity")
        if self.final_semantic_result.status != "accepted":
            raise ContractValidationError("adult turn evidence requires accepted final semantics")
        if (
            self.initial_semantic_receipt.result_id != self.initial_semantic_result.result_id
            or self.final_semantic_receipt.result_id != self.final_semantic_result.result_id
        ):
            raise ContractValidationError("adult semantic receipts do not bind their results")
        if self.beat_repair_receipt is None:
            if self.initial_specificity_receipt != self.final_specificity_receipt:
                raise ContractValidationError("adult specificity changed without a repair receipt")
            if self.initial_semantic_receipt != self.final_semantic_receipt:
                raise ContractValidationError("adult semantics changed without a repair receipt")
            if self.initial_semantic_result != self.final_semantic_result:
                raise ContractValidationError("adult semantic result changed without a repair receipt")
        elif self.beat_repair_receipt.status != "accepted_for_full_revalidation":
            raise ContractValidationError("adult turn evidence retained a rejected repair")


@dataclass(frozen=True, slots=True)
class FinalStoryAcceptanceReceipt:
    """Python-owned proof that every pre-publication gate accepted one prose candidate."""

    SCHEMA_VERSION: ClassVar[str] = "cera.final_story_acceptance_receipt.v1"

    schema_version: str
    receipt_id: TypedId
    request_id: TypedId
    candidate_sha256: str
    manifest_sha256: str
    composer_validation_receipt_id: TypedId
    realization_verification_id: TypedId
    adult_specificity_receipt_id: TypedId | None
    adult_semantic_receipt_id: TypedId | None
    status: str
    story_state_committed: bool

    def __post_init__(self) -> None:
        require_schema(self.schema_version, self.SCHEMA_VERSION, type(self).__name__)
        require_kind(self.receipt_id, IdKind.VALIDATION, "receipt_id")
        require_kind(self.request_id, IdKind.REQUEST, "request_id")
        require_kind(
            self.composer_validation_receipt_id,
            IdKind.VALIDATION,
            "composer_validation_receipt_id",
        )
        require_kind(
            self.realization_verification_id,
            IdKind.REALIZATION_VERIFICATION,
            "realization_verification_id",
        )
        if self.adult_specificity_receipt_id is not None:
            require_kind(
                self.adult_specificity_receipt_id,
                IdKind.SPECIFICITY_VALIDATION,
                "adult_specificity_receipt_id",
            )
        if self.adult_semantic_receipt_id is not None:
            require_kind(
                self.adult_semantic_receipt_id,
                IdKind.SEMANTIC_SPECIFICITY,
                "adult_semantic_receipt_id",
            )
        if not re_is_sha256(self.candidate_sha256) or not re_is_sha256(
            self.manifest_sha256
        ):
            raise ContractValidationError("final acceptance hashes are invalid")
        if self.status != "accepted_after_all_validation" or self.story_state_committed:
            raise ContractValidationError(
                "final acceptance receipt has invalid pre-publication state"
            )

    @property
    def receipt_sha256(self) -> str:
        return domain_sha256("cera.final_story_acceptance_receipt.v1", self)


@dataclass(frozen=True, slots=True)
class ProvisionalTurnCandidate:
    """Fully decoded Composer candidate exposed before semantic verification."""

    reasoner: ReasonerExecutionResult
    context: ComposerContextAssemblyResult
    composer: ComposerExecutionResult

    def __post_init__(self) -> None:
        if (
            self.context.request.reasoner_outcome != self.reasoner.outcome
            or self.context.request.reasoner_receipt != self.reasoner.receipt
        ):
            raise ContractValidationError(
                "provisional candidate changed its accepted Reasoner binding"
            )
        if (
            self.composer.composer_receipt.composer_request_sha256
            != self.context.request.request_sha256
        ):
            raise ContractValidationError(
                "provisional candidate changed its Composer request binding"
            )
        if self.composer.validation_receipt.story_state_committed:
            raise ContractValidationError(
                "provisional candidate cannot claim committed story state"
            )

    @property
    def provider_calls(self) -> int:
        return (
            self.reasoner.receipt.external_provider_calls
            + self.composer.composer_receipt.external_provider_calls
        )


@dataclass(frozen=True, slots=True)
class LiveShapedTurnResult:
    reasoner: ReasonerExecutionResult
    context: ComposerContextAssemblyResult
    composer: ComposerExecutionResult
    realization_verification: SceneRealizationVerificationResult
    final_acceptance: FinalStoryAcceptanceReceipt
    accepted_artifact: AcceptedStoryArtifact
    receipt: LiveShapedTurnReceipt
    adult_craft: AdultCraftTurnEvidence | None = None

    def __post_init__(self) -> None:
        if self.reasoner.receipt.provider_receipt_id != self.receipt.reasoner_provider_receipt_id:
            raise ContractValidationError("live-shaped result reasoner receipt mismatch")
        if (
            self.context.receipt.assembly_receipt_id
            != self.receipt.context_assembly_receipt_id
        ):
            raise ContractValidationError("live-shaped result context receipt mismatch")
        if self.composer.composer_receipt.provider_receipt_id != self.receipt.composer_provider_receipt_id:
            raise ContractValidationError("live-shaped result Composer receipt mismatch")
        if (
            not self.realization_verification.accepted
            or self.realization_verification.receipt.verification_id
            != self.receipt.realization_verification_id
        ):
            raise ContractValidationError(
                "live-shaped result lacks accepted realization verification"
            )
        if self.accepted_artifact.artifact_id != self.receipt.accepted_artifact_id:
            raise ContractValidationError("live-shaped result artifact mismatch")
        if self.accepted_artifact.validation_receipt_id != self.final_acceptance.receipt_id:
            raise ContractValidationError(
                "accepted artifact was not created from final acceptance evidence"
            )
        if (
            self.receipt.final_acceptance_receipt_id
            != self.final_acceptance.receipt_id
            or self.receipt.final_acceptance_receipt_sha256
            != self.final_acceptance.receipt_sha256
        ):
            raise ContractValidationError(
                "live-shaped receipt does not bind final acceptance evidence"
            )
        if (
            domain_sha256("cera.scene_reasoner_receipt.v2", self.reasoner.receipt)
            != self.receipt.reasoner_receipt_sha256
            or domain_sha256(
                "cera.composer_context_assembly_receipt.v1", self.context.receipt
            )
            != self.receipt.context_assembly_receipt_sha256
            or domain_sha256(
                "cera.scene_composer_receipt.v2", self.composer.composer_receipt
            )
            != self.receipt.composer_receipt_sha256
            or self.realization_verification.receipt.receipt_sha256
            != self.receipt.realization_verification_receipt_sha256
            or domain_sha256(
                "cera.accepted_story_artifact.v1", self.accepted_artifact
            )
            != self.receipt.accepted_artifact_sha256
        ):
            raise ContractValidationError("live-shaped result hash binding mismatch")
        expected_calls = (
            self.reasoner.receipt.external_provider_calls
            + self.composer.composer_receipt.external_provider_calls
            + self.realization_verification.receipt.external_provider_calls
        )
        if self.receipt.external_provider_calls != expected_calls:
            raise ContractValidationError("live-shaped result provider-call count mismatch")
        if self.adult_craft is None:
            if self.receipt.adult_craft_selection_id is not None:
                raise ContractValidationError("live-shaped receipt has orphan adult craft evidence")
        else:
            adult = self.adult_craft
            if (
                self.receipt.adult_craft_selection_id
                != adult.selection_receipt.selection_id
                or self.receipt.adult_craft_selection_sha256
                != adult.selection_receipt.selection_sha256
                or self.receipt.specificity_validation_receipt_id
                != adult.final_specificity_receipt.validation_receipt_id
                or self.receipt.specificity_validation_receipt_sha256
                != adult.final_specificity_receipt.receipt_sha256
                or self.receipt.semantic_specificity_receipt_id
                != adult.final_semantic_receipt.receipt_id
                or self.receipt.semantic_specificity_receipt_sha256
            != adult.final_semantic_receipt.receipt_sha256
            ):
                raise ContractValidationError("live-shaped adult evidence does not match receipt")
            if adult.beat_repair_receipt is None:
                if self.receipt.beat_repair_receipt_id is not None:
                    raise ContractValidationError("live-shaped receipt has orphan beat repair")
            elif (
                self.receipt.beat_repair_receipt_id
                != adult.beat_repair_receipt.repair_receipt_id
                or self.receipt.beat_repair_receipt_sha256
                != adult.beat_repair_receipt.receipt_sha256
            ):
                raise ContractValidationError("live-shaped beat repair does not match receipt")
