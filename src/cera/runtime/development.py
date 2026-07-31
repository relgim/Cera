"""Non-production raw-turn preparation for local CERA development routes.

This module owns no model judgment.  It converts an operator-authorized local
scene scope into the existing raw ingress, immutable-snapshot seed, Reasoner,
Composer, and publication contracts.  The same preparation is used by the
continuous live qualification and the later SillyTavern development adapter so
the UI cannot acquire a private shortcut around the tested runtime path.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import ClassVar, Mapping

from cera.composer import (
    ArtifactPublicationMode,
    ComposerContinuityReference,
    ComposerSourcePacket,
    ComposerSourceUnit,
    CompositionMode,
    RealizationKind,
)
from cera.contracts import (
    BeatState,
    BehavioralTurnControls,
    EvidenceRecordType,
    SceneDepthMode,
)
from cera.evidence import (
    EvidenceAccessScope,
    EvidenceObligation,
    EvidenceObligationKind,
    EvidenceWorldMode,
)
from cera.errors import ContractValidationError
from cera.ids import (
    AUTHORITY_RECORD_ID_KINDS,
    IdKind,
    TypedId,
    deterministic_id,
    require_kind,
)
from cera.ingress import (
    DeterministicRawTurnProjectionPort,
    IntentInterpretationDraft,
    RawTurnEnvelope,
    RawTurnIngressFacade,
)
from cera.kernel import PreflightAuthority, RequestedContentClass, TurnKernel
from cera.reasoner import SeedDossierAssembler
from cera.serialization import domain_sha256
from cera.storage import SQLiteAuthorityStore

from .application import OrdinaryApplicationRequestV2
from .models import ComposerRequestPlan, IngressPublicationEvidence


@dataclass(frozen=True, slots=True)
class RequiredSeedRecord:
    record_id: TypedId
    sections: tuple[str, ...]
    reason: str

    def __post_init__(self) -> None:
        if self.record_id.kind not in AUTHORITY_RECORD_ID_KINDS:
            raise ContractValidationError("development seed record kind is invalid")
        if not self.sections or any(not value.strip() for value in self.sections):
            raise ContractValidationError("development seed sections are required")
        if len(set(self.sections)) != len(self.sections):
            raise ContractValidationError("development seed sections must be unique")
        if not self.reason.strip():
            raise ContractValidationError("development seed reason is required")


@dataclass(frozen=True, slots=True)
class DevelopmentTurnSpec:
    SCHEMA_VERSION: ClassVar[str] = "cera.development_turn_spec.v4"

    schema_version: str
    turn_key: str
    raw_message: str
    session_id: TypedId
    branch_id: TypedId
    present_character_ids: tuple[TypedId, ...]
    eligible_responder_ids: tuple[TypedId, ...]
    scene_anchors: tuple[str, ...]
    established_scene_context: tuple[str, ...] = ()
    additional_seed_records: tuple[RequiredSeedRecord, ...] = ()
    publication_mode: ArtifactPublicationMode = ArtifactPublicationMode.APPEND
    response_profile_version: str = "cera-local-development-ordinary-v1"
    scene_depth_mode: SceneDepthMode = SceneDepthMode.AUTO
    behavioral_controls: BehavioralTurnControls = field(
        default_factory=BehavioralTurnControls.creator_default
    )

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("development turn spec schema is invalid")
        if not self.turn_key.strip() or not self.raw_message:
            raise ContractValidationError("development turn key and raw message are required")
        if len(self.turn_key) > 120:
            raise ContractValidationError("development turn key is too long")
        require_kind(self.session_id, IdKind.SESSION, "session_id")
        require_kind(self.branch_id, IdKind.BRANCH, "branch_id")
        if not self.present_character_ids or not self.eligible_responder_ids:
            raise ContractValidationError("development turn requires a bounded cast")
        if not set(self.eligible_responder_ids).issubset(self.present_character_ids):
            raise ContractValidationError(
                "development responders must be present characters"
            )
        if len(set(self.present_character_ids)) != len(self.present_character_ids):
            raise ContractValidationError("development present cast contains duplicates")
        if len(set(self.eligible_responder_ids)) != len(self.eligible_responder_ids):
            raise ContractValidationError("development responders contain duplicates")
        if not self.scene_anchors or any(not value.strip() for value in self.scene_anchors):
            raise ContractValidationError("development scene anchors are required")
        if any(not value.strip() for value in self.established_scene_context):
            raise ContractValidationError(
                "development established scene context cannot contain blanks"
            )
        if len(set(self.established_scene_context)) != len(
            self.established_scene_context
        ):
            raise ContractValidationError(
                "development established scene context contains duplicates"
            )
        if not self.response_profile_version.strip():
            raise ContractValidationError("development response profile is required")


@dataclass(frozen=True, slots=True)
class PreparedDevelopmentTurn:
    specification: DevelopmentTurnSpec
    application_request: OrdinaryApplicationRequestV2

    @property
    def reasoner_request(self):
        return self.application_request.reasoner_request

    @property
    def ingress_evidence(self) -> IngressPublicationEvidence:
        return self.application_request.ingress_evidence


class ContextualRawTurnProjectionPort:
    """Exact deterministic projection plus Python-authorized seed obligations."""

    adapter_version = "cera.contextual_raw_turn_projection.v1"
    external_provider_calls = 0

    def __init__(
        self,
        *,
        obligations: tuple[EvidenceObligation, ...],
        scene_anchors: tuple[str, ...],
        explicit_unknowns: tuple[str, ...],
        prohibited_inferences: tuple[str, ...],
    ) -> None:
        self.obligations = obligations
        self.scene_anchors = scene_anchors
        self.explicit_unknowns = explicit_unknowns
        self.prohibited_inferences = prohibited_inferences
        self.base = DeterministicRawTurnProjectionPort()

    def interpret(self, envelope: RawTurnEnvelope) -> IntentInterpretationDraft:
        projected = self.base.interpret(envelope)
        return replace(
            projected,
            evidence_obligations=self.obligations,
            scene_anchors=self.scene_anchors,
            explicit_unknowns=self.explicit_unknowns,
            prohibited_inferences=self.prohibited_inferences,
        )


class DevelopmentOrdinaryTurnPreparer:
    """Build the active v2 ordinary request from one exact raw user message."""

    def __init__(
        self,
        *,
        store: SQLiteAuthorityStore,
        evidence_service,
        world_id: TypedId,
        genesis_revision_id: TypedId,
        protected_user_id: TypedId,
        access_scope: EvidenceAccessScope,
        relationship_record_ids: Mapping[TypedId, TypedId],
        character_baseline_record_ids: Mapping[
            TypedId, tuple[TypedId, ...]
        ] | None = None,
    ) -> None:
        self.store = store
        self.evidence_service = evidence_service
        self.world_id = world_id
        self.genesis_revision_id = genesis_revision_id
        self.protected_user_id = protected_user_id
        self.access_scope = access_scope
        self.relationship_record_ids = dict(relationship_record_ids)
        self.character_baseline_record_ids = dict(
            character_baseline_record_ids or {}
        )

    def prepare(self, spec: DevelopmentTurnSpec) -> PreparedDevelopmentTurn:
        if self.protected_user_id not in spec.present_character_ids:
            raise ContractValidationError("development cast must contain protected user")
        branch = self.store.get_branch(spec.branch_id)
        if branch.world_id != self.world_id:
            raise ContractValidationError("development branch belongs to another world")
        required = list(spec.additional_seed_records)
        for character_id in spec.eligible_responder_ids:
            relationship_id = self.relationship_record_ids.get(character_id)
            if relationship_id is None:
                raise ContractValidationError(
                    "eligible responder lacks a directional protected-user relationship seed"
                )
            required.append(
                RequiredSeedRecord(
                    record_id=relationship_id,
                    sections=("claim", "knowledge"),
                    reason=(
                        "Authorize the selected character's directional starting "
                        "relationship to the protected user."
                    ),
                )
            )
            for baseline_id in self.character_baseline_record_ids.get(
                character_id, ()
            ):
                required.append(
                    RequiredSeedRecord(
                        record_id=baseline_id,
                        sections=("claim",),
                        reason=(
                            "Authorize the candidate character's compact core premise "
                            "before "
                            "runtime Codex selects participants and scene logic."
                        ),
                    )
                )
        required = list(_unique_required_records(tuple(required)))
        request_id = deterministic_id(
            IdKind.REQUEST,
            "cera.development.raw_turn.v1",
            f"{self.world_id}|{spec.branch_id}|{branch.generation}|{spec.turn_key}",
        )
        obligations = tuple(
            EvidenceObligation(
                schema_version=EvidenceObligation.SCHEMA_VERSION,
                obligation_id=deterministic_id(
                    IdKind.OBLIGATION,
                    "cera.development.seed_obligation.v1",
                    f"{request_id}|{value.record_id}|{'|'.join(value.sections)}",
                ),
                kind=EvidenceObligationKind.REQUIRED_RECORD,
                record_id=value.record_id,
                subject_id=None,
                record_type=None,
                required_sections=value.sections,
                query_plan=None,
                reason=value.reason,
            )
            for value in required
        )
        hard_boundaries = (
            "Use only snapshot-authorized exact evidence for hard decisions.",
            "Investigate through bounded evidence tools when the exact seed is insufficient.",
            "Select only currently present eligible NPC responders.",
            "The protected user's raw message is already visible in chat. Respond from the selected NPC perspective without restating or directly narrating the protected user's supplied action or dialogue.",
            "Keep protected_user_source_claims empty unless a selected NPC response truly requires a short exact supplied quote.",
            "Do not invent the protected user's private state or next unsupplied choice.",
            "Do not transfer owner-private knowledge between characters.",
            "Stop before the protected user's next unsupplied meaningful choice.",
        )
        interpreter = ContextualRawTurnProjectionPort(
            obligations=obligations,
            scene_anchors=spec.scene_anchors,
            explicit_unknowns=(
                "The protected user's next unsupplied meaningful choice is unknown.",
                "Unretrieved private state and future outcomes remain unknown.",
            ),
            prohibited_inferences=(
                "Do not author the protected user's unsupplied action, dialogue, or private state.",
                "Do not treat a search reference as exact evidence.",
                "Do not leak owner-private evidence to another character.",
                "Do not make conditional future segments into current facts.",
            ),
        )
        envelope = RawTurnEnvelope(
            schema_version=RawTurnEnvelope.SCHEMA_VERSION,
            world_id=self.world_id,
            request_id=request_id,
            session_id=spec.session_id,
            branch_id=spec.branch_id,
            expected_generation=branch.generation,
            expected_parent_artifact_id=branch.head_artifact_id,
            genesis_revision_id=self.genesis_revision_id,
            protected_user_id=self.protected_user_id,
            present_character_ids=spec.present_character_ids,
            eligible_responder_ids=spec.eligible_responder_ids,
            raw_message=spec.raw_message,
            idempotency_key=f"cera-development-{spec.turn_key}",
            world_mode=EvidenceWorldMode.REAL,
            access_scope=self.access_scope,
            preflight_authority=PreflightAuthority(RequestedContentClass.ORDINARY),
            hard_boundaries=hard_boundaries,
            scene_depth_mode=spec.scene_depth_mode,
            behavioral_controls=spec.behavioral_controls,
        )
        ingress = RawTurnIngressFacade(
            turn_kernel=TurnKernel(self.evidence_service),
            seed_assembler=SeedDossierAssembler(self.evidence_service),
            intent_interpreter=interpreter,
        ).prepare(envelope)
        if not ingress.ready_for_reasoner:
            raise ContractValidationError(
                "development seed dossier contains an unresolved evidence obligation"
            )
        prepared = ingress.reasoner_request.prepared_turn
        source_units = tuple(
            ComposerSourceUnit(
                source_unit_id=value.source_unit_id,
                classification=value.classification,
                exact_text=spec.raw_message,
                protected_user_allowed_kinds=(
                    RealizationKind.ACTION,
                    RealizationKind.DIALOGUE,
                ),
                required_state=BeatState.ATTEMPTED,
                participant_ids=spec.eligible_responder_ids,
            )
            for value in prepared.request.source_units
        )
        continuity = tuple(
            ComposerContinuityReference(
                evidence_id=value.evidence_id,
                record_id=value.metadata.record_id,
                record_version=value.metadata.record_version,
            )
            for value in ingress.seed_assembly.dossier.exact_seed_evidence
            if value.metadata.record_type is EvidenceRecordType.EVENT_FACT
        )
        if spec.publication_mode is ArtifactPublicationMode.REGENERATE:
            if branch.head_artifact_id is None:
                raise ContractValidationError("cannot regenerate an empty branch")
            publication_parent = self.store.get_artifact_parent_id(
                branch.head_artifact_id
            )
            replaces = branch.head_artifact_id
        else:
            publication_parent = None
            replaces = None
        plan = ComposerRequestPlan(
            source_packet=ComposerSourcePacket(
                mode=CompositionMode.ORDINARY,
                source_sha256=prepared.request.source_sha256,
                ordinary_units=source_units,
                protected_envelope=None,
                reasoner_safe_ledger_sha256=(
                    ingress.reasoner_request.source_view.source_view_sha256
                ),
            ),
            scene_scope="Immediate branch-local Hanezawa household continuation.",
            response_profile_version=spec.response_profile_version,
            continuity_references=continuity,
            creator_event_coverage_required=False,
            hard_boundaries=(
                "Realize the validated current segment as complete presentation-neutral prose.",
                "Use only selected-character realization context and cited exact evidence.",
                "The protected user's source is already visible; begin with the selected NPC response and do not restate or directly narrate the source-owned action or dialogue.",
                "Stop before the protected user's next unsupplied meaningful choice.",
            ),
            established_scene_context=spec.established_scene_context,
            adult_binding=None,
            publication_mode=spec.publication_mode,
            publication_parent_artifact_id=publication_parent,
            replaces_artifact_id=replaces,
            scene_depth_mode=spec.scene_depth_mode,
        )
        publication_evidence = IngressPublicationEvidence.create(
            request_id=request_id,
            source_sha256=prepared.request.source_sha256,
            reasoner_request_sha256=ingress.reasoner_request.request_sha256,
            interpretation_receipt=ingress.interpretation_receipt,
            seed_receipt=ingress.seed_assembly.receipt,
            seed_lookup_receipts=ingress.seed_assembly.lookup_receipts,
        )
        application_request = OrdinaryApplicationRequestV2(
            schema_version=OrdinaryApplicationRequestV2.SCHEMA_VERSION,
            reasoner_request=ingress.reasoner_request,
            composer_plan=plan,
            renderer_profile=None,
            consolidation_requested=False,
            ingress_evidence=publication_evidence,
        )
        return PreparedDevelopmentTurn(spec, application_request)


def _unique_required_records(
    values: tuple[RequiredSeedRecord, ...],
) -> tuple[RequiredSeedRecord, ...]:
    by_key: dict[tuple[TypedId, tuple[str, ...]], RequiredSeedRecord] = {}
    for value in values:
        key = (value.record_id, value.sections)
        existing = by_key.get(key)
        if existing is not None and existing.reason != value.reason:
            raise ContractValidationError(
                "one development seed selector has conflicting reasons"
            )
        by_key[key] = value
    return tuple(by_key.values())
