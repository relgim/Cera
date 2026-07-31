"""Provider-neutral Scene Composer, acceptance, and rendering contracts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, ClassVar

from cera.adult_craft.models import RealizationChannel, SpecificityContract
from cera.contracts import (
    AcceptedStoryArtifact,
    BeatState,
    SceneDepthMode,
    EffectiveCharacterProjection,
    SourceUnitClassification,
    Visibility,
    CreatorRevisionDirective,
)
from cera.evidence import EvidenceLookupReceipt, EvidenceMetadata, ExactEvidence
from cera.errors import ContractValidationError
from cera.ids import IdKind, TypedId, require_authority_record_kind, require_kind
from cera.kernel import PreparedTurn
from cera.reasoner import ReasonerOutcome, SceneReasonerReceipt
from cera.schema import require_schema
from cera.serialization import canonical_json, domain_sha256, re_is_sha256, text_sha256

if TYPE_CHECKING:
    from cera.providers import LiveProviderCallReceipt


class CompositionMode(StrEnum):
    ORDINARY = "ordinary"
    PROTECTED_ADULT = "protected_adult"


class ArtifactPublicationMode(StrEnum):
    APPEND = "append"
    REGENERATE = "regenerate"


class ComposerAdapterRole(StrEnum):
    SCRIPTED_FAKE = "fake_scene_composer"
    DEEPSEEK = "deepseek_scene_composer"


class ComposerContextSource(StrEnum):
    EXACT_EVIDENCE = "exact_evidence"
    EVIDENCE_SECTION_VIEW = "evidence_section_view"
    CREATOR_CRAFT = "creator_craft"


class ComposerContextKind(StrEnum):
    CHARACTER_IDENTITY = "character_identity"
    CHARACTER_VOICE = "character_voice"
    CHARACTER_EXPRESSION = "character_expression"
    CURRENT_STATE = "current_state"
    RELATIONSHIP = "relationship"
    CONTINUITY = "continuity"
    SCENE = "scene"
    MATERIAL = "material"
    CRAFT_REFERENCE = "craft_reference"


class RealizationKind(StrEnum):
    DIALOGUE = "dialogue"
    VOCALIZATION = "vocalization"
    SOUND_EFFECT = "sound_effect"
    ACTION = "action"
    PRIVATE_STATE = "private_state"
    EMOTION = "emotion"
    MOTIVE = "motive"
    CONSENT = "consent"
    REFUSAL = "refusal"
    REACTION = "reaction"
    DEPARTURE = "departure"
    DESTINATION = "destination"
    COMMITMENT = "commitment"


class SemanticCategory(StrEnum):
    BODILY_RESPONSE = "bodily_response"
    VOCALIZATION = "vocalization"
    FREEZE = "freeze"
    SILENCE = "silence"
    COMPLIANCE = "compliance"
    FAILURE_TO_RESIST = "failure_to_resist"
    DESIRE = "desire"
    ATTRACTION = "attraction"
    PLEASURE = "pleasure"
    CONSENT = "consent"
    CAPACITY = "capacity"
    PRESSURE = "pressure"
    INTENTION = "intention"
    ACTION = "action"
    RELATIONSHIP_MEANING = "relationship_meaning"
    BELIEF = "belief"
    ALLEGATION = "allegation"
    SUSPICION = "suspicion"
    UNCERTAINTY = "uncertainty"
    OBJECTIVE_FACT = "objective_fact"
    CERTAINTY = "certainty"


@dataclass(frozen=True, slots=True)
class ComposerSourceUnit:
    source_unit_id: TypedId
    classification: SourceUnitClassification
    exact_text: str
    protected_user_allowed_kinds: tuple[RealizationKind, ...]
    required_state: BeatState | None
    participant_ids: tuple[TypedId, ...]

    def __post_init__(self) -> None:
        require_kind(self.source_unit_id, IdKind.SOURCE_UNIT, "source_unit_id")
        _non_empty(self.exact_text, "exact_text")
        _unique((value.value for value in self.protected_user_allowed_kinds), "allowed kinds")
        for participant_id in self.participant_ids:
            require_kind(participant_id, IdKind.CHARACTER, "participant_ids")
        _unique((str(value) for value in self.participant_ids), "source participants")


@dataclass(frozen=True, slots=True)
class AdultComposerBinding:
    adult_authority_id: TypedId
    dual_representation_receipt_id: TypedId
    mechanics_receipt_id: TypedId
    authority_sha256: str
    dual_representation_sha256: str
    safe_source_view_sha256: str
    protected_envelope_sha256: str
    adult_context_sha256: str
    mechanics_proposal_sha256: str
    mechanics_receipt_sha256: str
    selected_craft_reference_ids: tuple[TypedId, ...]
    craft_selection_id: TypedId | None = None
    craft_selection_sha256: str | None = None
    specificity_contract_id: TypedId | None = None
    specificity_contract_sha256: str | None = None

    def __post_init__(self) -> None:
        require_kind(self.adult_authority_id, IdKind.ADULT_AUTHORITY, "adult_authority_id")
        require_kind(
            self.dual_representation_receipt_id,
            IdKind.VALIDATION,
            "dual_representation_receipt_id",
        )
        require_kind(
            self.mechanics_receipt_id,
            IdKind.PROVIDER_RECEIPT,
            "mechanics_receipt_id",
        )
        for value in (
            self.authority_sha256,
            self.dual_representation_sha256,
            self.safe_source_view_sha256,
            self.protected_envelope_sha256,
            self.adult_context_sha256,
            self.mechanics_proposal_sha256,
            self.mechanics_receipt_sha256,
        ):
            _sha256(value, "adult Composer binding hash")
        for reference_id in self.selected_craft_reference_ids:
            require_kind(reference_id, IdKind.CRAFT_REFERENCE, "craft reference IDs")
        _unique(
            (str(value) for value in self.selected_craft_reference_ids),
            "craft reference IDs",
        )
        extended = (
            self.craft_selection_id,
            self.craft_selection_sha256,
            self.specificity_contract_id,
            self.specificity_contract_sha256,
        )
        if any(value is not None for value in extended):
            if any(value is None for value in extended):
                raise ContractValidationError("adult craft binding fields must be all present")
            assert self.craft_selection_id is not None
            assert self.specificity_contract_id is not None
            assert self.craft_selection_sha256 is not None
            assert self.specificity_contract_sha256 is not None
            require_kind(self.craft_selection_id, IdKind.CRAFT_SELECTION, "craft_selection_id")
            require_kind(
                self.specificity_contract_id,
                IdKind.SPECIFICITY_CONTRACT,
                "specificity_contract_id",
            )
            _sha256(self.craft_selection_sha256, "craft_selection_sha256")
            _sha256(self.specificity_contract_sha256, "specificity_contract_sha256")


@dataclass(frozen=True, slots=True)
class ProtectedSourceEnvelope:
    protected_source_id: TypedId
    source_sha256: str
    units: tuple[ComposerSourceUnit, ...]

    def __post_init__(self) -> None:
        require_kind(
            self.protected_source_id,
            IdKind.PROTECTED_SOURCE,
            "protected_source_id",
        )
        _sha256(self.source_sha256, "source_sha256")
        if not self.units:
            raise ContractValidationError("protected source envelope requires units")
        _unique((str(value.source_unit_id) for value in self.units), "source units")

    @property
    def envelope_sha256(self) -> str:
        return domain_sha256("cera.protected_source_envelope.v1", self)


@dataclass(frozen=True, slots=True)
class ComposerSourcePacket:
    mode: CompositionMode
    source_sha256: str
    ordinary_units: tuple[ComposerSourceUnit, ...]
    protected_envelope: ProtectedSourceEnvelope | None
    reasoner_safe_ledger_sha256: str

    def __post_init__(self) -> None:
        _sha256(self.source_sha256, "source_sha256")
        _sha256(self.reasoner_safe_ledger_sha256, "reasoner_safe_ledger_sha256")
        if self.mode is CompositionMode.ORDINARY:
            if not self.ordinary_units or self.protected_envelope is not None:
                raise ContractValidationError(
                    "ordinary composition requires ordinary units and no protected envelope"
                )
        else:
            if self.ordinary_units or self.protected_envelope is None:
                raise ContractValidationError(
                    "protected composition requires only the restricted source envelope"
                )
            if self.protected_envelope.source_sha256 != self.source_sha256:
                raise ContractValidationError("protected envelope source hash mismatch")
        _unique((str(value.source_unit_id) for value in self.units), "composer source units")

    @property
    def units(self) -> tuple[ComposerSourceUnit, ...]:
        if self.protected_envelope is not None:
            return self.protected_envelope.units
        return self.ordinary_units

    @property
    def protected_envelope_sha256(self) -> str | None:
        if self.protected_envelope is None:
            return None
        return self.protected_envelope.envelope_sha256


@dataclass(frozen=True, slots=True)
class ComposerContinuityReference:
    evidence_id: TypedId
    record_id: TypedId
    record_version: int

    def __post_init__(self) -> None:
        require_kind(self.evidence_id, IdKind.EVIDENCE, "evidence_id")
        require_authority_record_kind(self.record_id, "record_id")
        if self.record_version < 1:
            raise ContractValidationError("continuity record version must be positive")


@dataclass(frozen=True, slots=True)
class ComposerEvidenceSectionView:
    selection_id: TypedId
    evidence_id: TypedId
    subject_ids: tuple[TypedId, ...]
    metadata: EvidenceMetadata
    source_sections_sha256: str
    selected_heading_paths: tuple[str, ...]
    selected_text: str
    selected_text_sha256: str
    selection_policy_version: str

    def __post_init__(self) -> None:
        require_kind(self.selection_id, IdKind.REALIZATION_SECTION, "selection_id")
        require_kind(self.evidence_id, IdKind.EVIDENCE, "evidence_id")
        if not self.subject_ids or not self.selected_heading_paths:
            raise ContractValidationError("realization section view requires subjects and headings")
        _unique((str(value) for value in self.subject_ids), "section-view subjects")
        _unique(self.selected_heading_paths, "selected heading paths")
        _sha256(self.source_sections_sha256, "source_sections_sha256")
        _non_empty(self.selected_text, "selected_text")
        _sha256(self.selected_text_sha256, "selected_text_sha256")
        if text_sha256(self.selected_text) != self.selected_text_sha256:
            raise ContractValidationError("selected realization text hash mismatch")
        _non_empty(self.selection_policy_version, "selection_policy_version")


@dataclass(frozen=True, slots=True)
class ComposerContextBlock:
    context_id: TypedId
    source: ComposerContextSource
    kind: ComposerContextKind
    applicable_character_ids: tuple[TypedId, ...]
    exact_evidence: ExactEvidence | None
    craft_reference_id: TypedId | None
    craft_text: str | None
    content_class: str
    evidence_section_view: ComposerEvidenceSectionView | None = None

    def __post_init__(self) -> None:
        for character_id in self.applicable_character_ids:
            require_kind(
                character_id,
                IdKind.CHARACTER,
                "applicable_character_ids",
            )
        _unique(
            (str(value) for value in self.applicable_character_ids),
            "context applicable characters",
        )
        if self.content_class not in {
            "ordinary",
            "protected_non_graphic",
            "system",
            "protected_adult",
        }:
            raise ContractValidationError("unsupported Composer context content class")
        if self.source is ComposerContextSource.EXACT_EVIDENCE:
            require_kind(self.context_id, IdKind.EVIDENCE, "context_id")
            if (
                self.exact_evidence is None
                or self.exact_evidence.evidence_id != self.context_id
                or self.craft_reference_id is not None
                or self.craft_text is not None
                or self.evidence_section_view is not None
                or self.kind is ComposerContextKind.CRAFT_REFERENCE
            ):
                raise ContractValidationError("exact Composer context is malformed")
            if self.content_class != self.exact_evidence.metadata.content_class:
                raise ContractValidationError("Composer context content class changed evidence")
        elif self.source is ComposerContextSource.EVIDENCE_SECTION_VIEW:
            require_kind(self.context_id, IdKind.REALIZATION_SECTION, "context_id")
            if (
                self.evidence_section_view is None
                or self.evidence_section_view.selection_id != self.context_id
                or self.exact_evidence is not None
                or self.craft_reference_id is not None
                or self.craft_text is not None
                or self.kind is ComposerContextKind.CRAFT_REFERENCE
            ):
                raise ContractValidationError("evidence section-view context is malformed")
            if self.content_class != self.evidence_section_view.metadata.content_class:
                raise ContractValidationError("section-view content class changed evidence")
        else:
            require_kind(self.context_id, IdKind.CRAFT_REFERENCE, "context_id")
            if (
                self.kind is not ComposerContextKind.CRAFT_REFERENCE
                or self.exact_evidence is not None
                or self.evidence_section_view is not None
                or self.craft_reference_id != self.context_id
                or self.craft_text is None
                or not self.craft_text.strip()
            ):
                raise ContractValidationError("craft Composer context is malformed")


@dataclass(frozen=True, slots=True)
class ComposerRealizationContext:
    selection_policy_version: str
    blocks: tuple[ComposerContextBlock, ...]
    explicit_exclusions: tuple[str, ...]
    maximum_bytes: int = 65_536
    effective_character_projections: tuple[EffectiveCharacterProjection, ...] = ()

    def __post_init__(self) -> None:
        _non_empty(self.selection_policy_version, "selection_policy_version")
        if not self.blocks:
            raise ContractValidationError("Composer realization context cannot be empty")
        _unique((str(value.context_id) for value in self.blocks), "Composer context IDs")
        if type(self.maximum_bytes) is not int or not 1 <= self.maximum_bytes <= 262_144:
            raise ContractValidationError("Composer context byte limit is invalid")
        if len(canonical_json(self.blocks).encode("utf-8")) > self.maximum_bytes:
            raise ContractValidationError("Composer realization context exceeds its byte budget")
        _unique(self.explicit_exclusions, "Composer context exclusions")
        _unique(
            (str(value.character_id) for value in self.effective_character_projections),
            "effective character projections",
        )
        if self.effective_character_projections:
            snapshot_tokens = {
                value.snapshot_token for value in self.effective_character_projections
            }
            if len(snapshot_tokens) != 1:
                raise ContractValidationError(
                    "effective character projections must share one snapshot"
                )

    @property
    def context_sha256(self) -> str:
        return domain_sha256("cera.composer_realization_context.v1", self)


@dataclass(frozen=True, slots=True)
class ComposerContextAssemblyReceipt:
    SCHEMA_VERSION: ClassVar[str] = "cera.composer_context_assembly_receipt.v1"

    schema_version: str
    assembly_receipt_id: TypedId
    snapshot_token: TypedId
    base_request_sha256: str
    assembled_request_sha256: str
    realization_context_sha256: str
    selected_evidence_ids: tuple[TypedId, ...]
    selected_craft_reference_ids: tuple[TypedId, ...]
    lookup_receipt_ids: tuple[TypedId, ...]
    lookup_count: int
    cumulative_returned_bytes: int
    external_provider_calls: int
    story_authority_writes: int

    def __post_init__(self) -> None:
        require_schema(self.schema_version, self.SCHEMA_VERSION, type(self).__name__)
        require_kind(
            self.assembly_receipt_id,
            IdKind.VALIDATION,
            "assembly_receipt_id",
        )
        require_kind(self.snapshot_token, IdKind.SNAPSHOT, "snapshot_token")
        for value in (
            self.base_request_sha256,
            self.assembled_request_sha256,
            self.realization_context_sha256,
        ):
            _sha256(value, "Composer context receipt hash")
        for evidence_id in self.selected_evidence_ids:
            require_kind(evidence_id, IdKind.EVIDENCE, "selected_evidence_ids")
        for reference_id in self.selected_craft_reference_ids:
            require_kind(
                reference_id,
                IdKind.CRAFT_REFERENCE,
                "selected_craft_reference_ids",
            )
        for receipt_id in self.lookup_receipt_ids:
            require_kind(receipt_id, IdKind.LOOKUP_RECEIPT, "lookup_receipt_ids")
        _unique(
            (str(value) for value in self.selected_evidence_ids),
            "selected evidence IDs",
        )
        _unique(
            (str(value) for value in self.selected_craft_reference_ids),
            "selected craft IDs",
        )
        _unique(
            (str(value) for value in self.lookup_receipt_ids),
            "context lookup receipt IDs",
        )
        if self.lookup_count != len(self.lookup_receipt_ids):
            raise ContractValidationError("context lookup count does not match receipts")
        if min(
            self.lookup_count,
            self.cumulative_returned_bytes,
            self.external_provider_calls,
            self.story_authority_writes,
        ) < 0:
            raise ContractValidationError("context receipt counters cannot be negative")
        if self.external_provider_calls != 0 or self.story_authority_writes != 0:
            raise ContractValidationError("Composer context assembly cannot call providers or write")


@dataclass(frozen=True, slots=True)
class ComposerContextAssemblyResult:
    request: "SceneComposerRequest"
    receipt: ComposerContextAssemblyReceipt
    lookup_receipts: tuple[EvidenceLookupReceipt, ...] = ()

    def __post_init__(self) -> None:
        if self.request.request_sha256 != self.receipt.assembled_request_sha256:
            raise ContractValidationError("assembled Composer request does not match receipt")
        assert self.request.realization_context is not None
        if (
            self.request.realization_context.context_sha256
            != self.receipt.realization_context_sha256
        ):
            raise ContractValidationError("assembled Composer context does not match receipt")
        if tuple(
            value.lookup_receipt_id for value in self.lookup_receipts
        ) != self.receipt.lookup_receipt_ids:
            raise ContractValidationError(
                "retained Composer lookup receipts do not match receipt handles"
            )


@dataclass(frozen=True, slots=True)
class SceneComposerRequest:
    SCHEMA_VERSION: ClassVar[str] = "cera.scene_composer_request.v6"

    schema_version: str
    prepared_turn: PreparedTurn
    reasoner_outcome: ReasonerOutcome
    reasoner_receipt: SceneReasonerReceipt
    source_packet: ComposerSourcePacket
    selected_npc_ids: tuple[TypedId, ...]
    scene_scope: str
    response_profile_version: str
    continuity_references: tuple[ComposerContinuityReference, ...]
    creator_event_coverage_required: bool
    hard_boundaries: tuple[str, ...]
    established_scene_context: tuple[str, ...] = ()
    adult_binding: AdultComposerBinding | None = None
    realization_context: ComposerRealizationContext | None = None
    publication_mode: ArtifactPublicationMode = ArtifactPublicationMode.APPEND
    publication_parent_artifact_id: TypedId | None = None
    replaces_artifact_id: TypedId | None = None
    specificity_contract: SpecificityContract | None = None
    scene_depth_mode: SceneDepthMode = SceneDepthMode.AUTO
    creator_revision: CreatorRevisionDirective | None = None

    def __post_init__(self) -> None:
        # Importing here avoids a runtime cycle through the public kernel package.
        from cera.kernel import TurnRoute
        from cera.reasoner import ReasonerOutcomeStatus

        require_schema(self.schema_version, self.SCHEMA_VERSION, type(self).__name__)
        if not isinstance(self.prepared_turn, PreparedTurn):
            raise ContractValidationError("composer request requires a PreparedTurn")
        if self.reasoner_outcome.status is not ReasonerOutcomeStatus.DECISION_READY:
            raise ContractValidationError("composer requires a decision-ready reasoner outcome")
        decision = self.reasoner_outcome.decision
        assert decision is not None
        if self.reasoner_receipt.outcome_sha256 != self.reasoner_outcome.outcome_sha256:
            raise ContractValidationError("reasoner receipt does not bind supplied outcome")
        if self.reasoner_receipt.snapshot_token != self.prepared_turn.evidence_snapshot.snapshot_token:
            raise ContractValidationError("composer snapshot does not match reasoner receipt")
        if self.reasoner_receipt.source_sha256 != self.source_packet.source_sha256:
            raise ContractValidationError("composer source does not match reasoner receipt")
        if (
            self.reasoner_receipt.source_view_sha256
            != self.source_packet.reasoner_safe_ledger_sha256
        ):
            raise ContractValidationError("composer safe ledger does not match reasoner receipt")
        if self.prepared_turn.request.source_sha256 != self.source_packet.source_sha256:
            raise ContractValidationError("composer source does not match prepared turn")
        if self.selected_npc_ids != decision.responding_npc_ids:
            raise ContractValidationError("composer selected cast must equal validated decision cast")
        _unique((str(value) for value in self.selected_npc_ids), "selected NPCs")
        for character_id in self.selected_npc_ids:
            require_kind(character_id, IdKind.CHARACTER, "selected_npc_ids")
        source_units = tuple(value.source_unit_id for value in self.source_packet.units)
        prepared_units = tuple(value.source_unit_id for value in self.prepared_turn.request.source_units)
        if source_units != prepared_units:
            raise ContractValidationError("composer source-unit order differs from prepared turn")
        for supplied, prepared in zip(
            self.source_packet.units, self.prepared_turn.request.source_units, strict=True
        ):
            if supplied.classification is not prepared.classification:
                raise ContractValidationError("composer source classification changed")
            if text_sha256(supplied.exact_text) != prepared.sha256:
                raise ContractValidationError("composer exact source text failed source-unit hash")
        expected_mode = (
            CompositionMode.PROTECTED_ADULT
            if self.prepared_turn.route is TurnRoute.CONSENT_VALID_ADULT
            else CompositionMode.ORDINARY
        )
        if self.source_packet.mode is not expected_mode:
            raise ContractValidationError("composer mode changed Python route")
        if expected_mode is CompositionMode.PROTECTED_ADULT:
            # The ledger hash is a separate binding; exact protected text is not part
            # of reasoner output or general provider receipts.
            if not self.source_packet.protected_envelope_sha256:
                raise ContractValidationError("adult composer lacks restricted source envelope")
            if self.adult_binding is None:
                raise ContractValidationError("adult composer lacks validated adult-route binding")
            if (
                self.adult_binding.safe_source_view_sha256
                != self.source_packet.reasoner_safe_ledger_sha256
                or self.adult_binding.protected_envelope_sha256
                != self.source_packet.protected_envelope_sha256
            ):
                raise ContractValidationError("adult Composer binding does not match dual representations")
            if self.specificity_contract is not None:
                contract = self.specificity_contract
                if (
                    self.adult_binding.specificity_contract_id
                    != contract.specificity_contract_id
                    or self.adult_binding.specificity_contract_sha256
                    != contract.contract_sha256
                    or contract.request_id != self.prepared_turn.request.request_id
                    or contract.decision_id != decision.decision_id
                ):
                    raise ContractValidationError("adult specificity contract is not bound to this request")
        elif self.adult_binding is not None:
            raise ContractValidationError("ordinary Composer request cannot carry adult context")
        elif self.specificity_contract is not None:
            raise ContractValidationError("ordinary Composer request cannot carry adult specificity")
        if not self.hard_boundaries:
            raise ContractValidationError("composer request requires hard boundaries")
        if any(not value.strip() for value in self.established_scene_context):
            raise ContractValidationError(
                "composer established scene context cannot contain blanks"
            )
        _unique(
            self.established_scene_context,
            "composer established scene context",
        )
        if self.publication_mode is ArtifactPublicationMode.APPEND:
            if (
                self.publication_parent_artifact_id is not None
                or self.replaces_artifact_id is not None
            ):
                raise ContractValidationError(
                    "append Composer request cannot carry regeneration topology"
                )
        else:
            if self.replaces_artifact_id is None:
                raise ContractValidationError(
                    "regeneration Composer request requires a replacement target"
                )
            require_kind(
                self.replaces_artifact_id,
                IdKind.ARTIFACT,
                "replaces_artifact_id",
            )
            if self.publication_parent_artifact_id is not None:
                require_kind(
                    self.publication_parent_artifact_id,
                    IdKind.ARTIFACT,
                    "publication_parent_artifact_id",
                )
            if self.replaces_artifact_id != self.prepared_turn.request.parent_artifact_id:
                raise ContractValidationError(
                    "regeneration must replace the prepared branch head"
                )
        _non_empty(self.scene_scope, "scene_scope")
        _non_empty(self.response_profile_version, "response_profile_version")
        _unique(
            (str(value.evidence_id) for value in self.continuity_references),
            "continuity references",
        )
        if self.realization_context is not None:
            self._validate_realization_context()

    def _validate_realization_context(self) -> None:
        assert self.realization_context is not None
        selected = set(self.selected_npc_ids)
        protected_user_id = self.prepared_turn.request.protected_user_id
        authorized_evidence_ids = {
            *(value.evidence_id for value in self.reasoner_outcome.hard_citations),
            *(value.evidence_id for value in self.continuity_references),
        }
        craft_ids: set[TypedId] = set()
        for block in self.realization_context.blocks:
            applicable = set(block.applicable_character_ids)
            if not applicable.issubset(selected):
                raise ContractValidationError(
                    "Composer context applies to an unselected character"
                )
            if block.source is ComposerContextSource.CREATOR_CRAFT:
                assert block.craft_reference_id is not None
                craft_ids.add(block.craft_reference_id)
                continue
            if block.source is ComposerContextSource.EVIDENCE_SECTION_VIEW:
                assert block.evidence_section_view is not None
                evidence_id = block.evidence_section_view.evidence_id
                evidence_metadata = block.evidence_section_view.metadata
                evidence_subject_ids = block.evidence_section_view.subject_ids
            else:
                assert block.exact_evidence is not None
                evidence_id = block.exact_evidence.evidence_id
                evidence_metadata = block.exact_evidence.metadata
                evidence_subject_ids = block.exact_evidence.subject_ids
            if evidence_id not in authorized_evidence_ids:
                raise ContractValidationError(
                    "Composer context evidence was not reasoner-cited or continuity-selected"
                )
            if (
                evidence_metadata.genesis_revision_id
                != self.prepared_turn.evidence_snapshot.genesis_revision_id
            ):
                raise ContractValidationError("Composer context Genesis revision mismatch")
            if evidence_metadata.visibility is Visibility.OWNER_PRIVATE:
                if (
                    evidence_metadata.owner_id is None
                    or evidence_metadata.owner_id not in selected
                    or applicable != {evidence_metadata.owner_id}
                ):
                    raise ContractValidationError(
                        "owner-private Composer context escaped its selected owner"
                    )
            if evidence_metadata.knowledge_owner_ids and not applicable.issubset(
                set(evidence_metadata.knowledge_owner_ids)
            ):
                raise ContractValidationError(
                    "Composer context applies evidence beyond its knowledge owners"
                )
            if (
                evidence_metadata.visibility is Visibility.SYSTEM_PRIVATE
                and not applicable
                and protected_user_id not in evidence_subject_ids
            ):
                raise ContractValidationError(
                    "system-private Composer context lacks an authorized active subject"
                )
        if self.source_packet.mode is CompositionMode.ORDINARY:
            if any(
                value.content_class == "protected_adult"
                for value in self.realization_context.blocks
            ):
                raise ContractValidationError(
                    "ordinary Composer context cannot contain protected-adult craft"
                )
        else:
            assert self.adult_binding is not None
            if craft_ids != set(self.adult_binding.selected_craft_reference_ids):
                raise ContractValidationError(
                    "adult Composer craft context differs from selected references"
                )

    @property
    def decision_sha256(self) -> str:
        assert self.reasoner_outcome.decision is not None
        return domain_sha256("cera.scene_decision.v1", self.reasoner_outcome.decision)

    @property
    def sequence_plan_sha256(self) -> str:
        assert self.reasoner_outcome.decision is not None
        decision = self.reasoner_outcome.decision
        return domain_sha256(
            "cera.sequence_plan.v1",
            (decision.current_segment, decision.future_segments),
        )

    @property
    def request_sha256(self) -> str:
        return domain_sha256(self.SCHEMA_VERSION, self)


@dataclass(frozen=True, slots=True)
class ComposerCandidate:
    SCHEMA_VERSION: ClassVar[str] = "cera.composer_candidate.v1"

    schema_version: str
    candidate_id: TypedId
    story_text: str
    story_text_sha256: str
    complete_core: bool
    is_outline: bool
    is_partial_draft: bool
    awaits_detailer: bool
    contains_internal_labels: bool
    contains_ui_markup: bool
    contains_provider_diagnostics: bool

    def __post_init__(self) -> None:
        require_schema(self.schema_version, self.SCHEMA_VERSION, type(self).__name__)
        require_kind(self.candidate_id, IdKind.COMPOSER_CANDIDATE, "candidate_id")
        _non_empty(self.story_text, "story_text")
        _sha256(self.story_text_sha256, "story_text_sha256")
        if text_sha256(self.story_text) != self.story_text_sha256:
            raise ContractValidationError("candidate story hash mismatch")

    @property
    def candidate_sha256(self) -> str:
        return domain_sha256("cera.composer_candidate.v1", self)


@dataclass(frozen=True, slots=True)
class CharacterMoveRealization:
    character_id: TypedId
    selected_intent_sha256: str
    action_direction_sha256: str

    def __post_init__(self) -> None:
        require_kind(self.character_id, IdKind.CHARACTER, "character_id")
        _sha256(self.selected_intent_sha256, "selected_intent_sha256")
        _sha256(self.action_direction_sha256, "action_direction_sha256")


@dataclass(frozen=True, slots=True)
class CharacterRealization:
    character_id: TypedId
    speaks: bool
    visibly_acts: bool

    def __post_init__(self) -> None:
        require_kind(self.character_id, IdKind.CHARACTER, "character_id")
        if not (self.speaks or self.visibly_acts):
            raise ContractValidationError("realized participant must speak or visibly act")


@dataclass(frozen=True, slots=True)
class RealizationSpan:
    start: int
    end: int
    owner_id: TypedId
    kind: RealizationKind
    source_unit_id: TypedId | None
    beat_id: TypedId | None
    realized_state: BeatState | None

    def __post_init__(self) -> None:
        require_kind(self.owner_id, IdKind.CHARACTER, "owner_id")
        if self.start < 0 or self.end <= self.start:
            raise ContractValidationError("realization span is invalid")
        if self.source_unit_id is not None:
            require_kind(self.source_unit_id, IdKind.SOURCE_UNIT, "source_unit_id")
        if self.beat_id is not None:
            require_kind(self.beat_id, IdKind.BEAT, "beat_id")


@dataclass(frozen=True, slots=True)
class StoryTextRange:
    """Python-derived range into one immutable Composer candidate."""

    start: int
    end: int

    def __post_init__(self) -> None:
        if type(self.start) is not int or type(self.end) is not int:
            raise ContractValidationError("story text range offsets must be integers")
        if self.start < 0 or self.end <= self.start:
            raise ContractValidationError("story text range is invalid")


@dataclass(frozen=True, slots=True)
class SourceUnitCoverage:
    source_unit_id: TypedId
    ordinal: int
    start: int
    end: int
    preserved_state: BeatState
    additional_ranges: tuple[StoryTextRange, ...] = ()

    def __post_init__(self) -> None:
        require_kind(self.source_unit_id, IdKind.SOURCE_UNIT, "source_unit_id")
        if self.ordinal < 0 or self.start < 0 or self.end <= self.start:
            raise ContractValidationError("source coverage span is invalid")
        previous_end = self.end
        for value in self.additional_ranges:
            if value.start < previous_end:
                raise ContractValidationError(
                    "source coverage ranges must be ordered and non-overlapping"
                )
            previous_end = value.end

    @property
    def ranges(self) -> tuple[StoryTextRange, ...]:
        return (StoryTextRange(self.start, self.end), *self.additional_ranges)


@dataclass(frozen=True, slots=True)
class AdultSpecificityCoverage:
    """Python-bound coverage for one beat-local adult craft obligation.

    The provider supplies only an obligation key and ordered segment keys.
    Python resolves the key back to the authoritative contract fields and
    derives every text range.
    """

    obligation_key: str
    beat_id: TypedId
    channel: RealizationChannel
    character_id: TypedId | None
    ranges: tuple[StoryTextRange, ...]

    def __post_init__(self) -> None:
        if not self.obligation_key.strip():
            raise ContractValidationError("adult specificity obligation key is required")
        require_kind(self.beat_id, IdKind.BEAT, "beat_id")
        if self.character_id is not None:
            require_kind(self.character_id, IdKind.CHARACTER, "character_id")
        if self.channel in {
            RealizationChannel.DIALOGUE,
            RealizationChannel.INNER_VOICE,
        }:
            if self.character_id is None:
                raise ContractValidationError(
                    "character-owned adult specificity coverage requires an owner"
                )
        elif self.character_id is not None:
            raise ContractValidationError(
                "scene adult specificity coverage cannot name a character owner"
            )
        if not self.ranges:
            raise ContractValidationError(
                "adult specificity coverage requires at least one text range"
            )
        previous_end = -1
        for value in self.ranges:
            if value.start < previous_end:
                raise ContractValidationError(
                    "adult specificity ranges must be ordered and non-overlapping"
                )
            previous_end = value.end


@dataclass(frozen=True, slots=True)
class SemanticInference:
    source: SemanticCategory
    target: SemanticCategory


@dataclass(frozen=True, slots=True)
class RealizationManifest:
    SCHEMA_VERSION: ClassVar[str] = "cera.realization_manifest.v2"

    schema_version: str
    manifest_id: TypedId
    candidate_sha256: str
    decision_sha256: str
    sequence_plan_sha256: str
    floor_owner_id: TypedId
    move_realizations: tuple[CharacterMoveRealization, ...]
    participant_realizations: tuple[CharacterRealization, ...]
    realized_beat_ids: tuple[TypedId, ...]
    source_unit_coverage: tuple[SourceUnitCoverage, ...]
    character_spans: tuple[RealizationSpan, ...]
    semantic_inferences: tuple[SemanticInference, ...]
    terminal_state_preserved: bool
    stops_before_protected_user_choice: bool
    introduced_major_objective_or_participant: bool
    adult_specificity_coverage: tuple[AdultSpecificityCoverage, ...] = ()

    def __post_init__(self) -> None:
        require_schema(self.schema_version, self.SCHEMA_VERSION, type(self).__name__)
        require_kind(self.manifest_id, IdKind.REALIZATION_MANIFEST, "manifest_id")
        require_kind(self.floor_owner_id, IdKind.CHARACTER, "floor_owner_id")
        for value in (
            self.candidate_sha256,
            self.decision_sha256,
            self.sequence_plan_sha256,
        ):
            _sha256(value, "manifest hash")
        _unique((str(value.character_id) for value in self.move_realizations), "move realizations")
        _unique(
            (str(value.character_id) for value in self.participant_realizations),
            "participant realizations",
        )
        _unique((str(value) for value in self.realized_beat_ids), "realized beat IDs")
        _unique(
            (str(value.source_unit_id) for value in self.source_unit_coverage),
            "source-unit coverage",
        )
        _unique(
            (value.obligation_key for value in self.adult_specificity_coverage),
            "adult specificity obligations",
        )
        for beat_id in self.realized_beat_ids:
            require_kind(beat_id, IdKind.BEAT, "realized_beat_ids")

    @property
    def manifest_sha256(self) -> str:
        return domain_sha256("cera.realization_manifest.v2", self)


@dataclass(frozen=True, slots=True)
class ComposerSubmission:
    candidate: ComposerCandidate
    manifest: RealizationManifest
    advisory_realization_metadata: tuple[object, ...]


@dataclass(frozen=True, slots=True)
class SceneComposerAdapterCall:
    submission: ComposerSubmission
    adapter_role: ComposerAdapterRole
    adapter_version: str
    adapter_evidence_id: str
    adapter_evidence_sha256: str
    provider_receipt_id: TypedId
    provider_receipt_sha256: str
    external_provider_calls: int
    validated_obligation_sha256: str | None = None
    provider_call_receipt: LiveProviderCallReceipt | None = None

    def __post_init__(self) -> None:
        _non_empty(self.adapter_version, "adapter_version")
        _non_empty(self.adapter_evidence_id, "adapter_evidence_id")
        require_kind(self.provider_receipt_id, IdKind.PROVIDER_RECEIPT, "provider_receipt_id")
        _sha256(self.adapter_evidence_sha256, "adapter_evidence_sha256")
        _sha256(self.provider_receipt_sha256, "provider_receipt_sha256")
        if self.adapter_role is ComposerAdapterRole.SCRIPTED_FAKE:
            if (
                self.external_provider_calls != 0
                or self.provider_call_receipt is not None
                or self.validated_obligation_sha256 is not None
            ):
                raise ContractValidationError("fake Composer cannot report a live receipt")
        else:
            if self.external_provider_calls != 1 or self.provider_call_receipt is None:
                raise ContractValidationError("DeepSeek Composer requires one provider receipt")
            if self.validated_obligation_sha256 is None:
                raise ContractValidationError(
                    "DeepSeek Composer must bind its validated output obligations"
                )
            _sha256(
                self.validated_obligation_sha256,
                "validated_obligation_sha256",
            )
            if (
                self.provider_call_receipt.provider_receipt_id
                != self.provider_receipt_id
                or self.provider_call_receipt.receipt_sha256
                != self.provider_receipt_sha256
            ):
                raise ContractValidationError("Composer provider receipt handle mismatch")


@dataclass(frozen=True, slots=True)
class SceneComposerReceipt:
    SCHEMA_VERSION: ClassVar[str] = "cera.scene_composer_receipt.v2"

    schema_version: str
    provider_receipt_id: TypedId
    adapter_role: ComposerAdapterRole
    adapter_version: str
    adapter_evidence_id: str
    adapter_evidence_sha256: str
    provider_receipt_sha256: str
    composer_request_sha256: str
    source_sha256: str
    safe_ledger_sha256: str
    protected_source_envelope_sha256: str | None
    decision_sha256: str
    sequence_plan_sha256: str
    candidate_sha256: str
    manifest_sha256: str
    outcome: str
    external_provider_calls: int

    def __post_init__(self) -> None:
        require_schema(self.schema_version, self.SCHEMA_VERSION, type(self).__name__)
        require_kind(self.provider_receipt_id, IdKind.PROVIDER_RECEIPT, "provider_receipt_id")
        _non_empty(self.adapter_version, "adapter_version")
        _non_empty(self.adapter_evidence_id, "adapter_evidence_id")
        for value in (
            self.adapter_evidence_sha256,
            self.provider_receipt_sha256,
            self.composer_request_sha256,
            self.source_sha256,
            self.safe_ledger_sha256,
            self.decision_sha256,
            self.sequence_plan_sha256,
            self.candidate_sha256,
            self.manifest_sha256,
        ):
            _sha256(value, "composer receipt hash")
        if self.protected_source_envelope_sha256 is not None:
            _sha256(
                self.protected_source_envelope_sha256,
                "protected_source_envelope_sha256",
            )
        if self.outcome != "candidate_returned":
            raise ContractValidationError("Composer receipt outcome is invalid")
        if self.adapter_role is ComposerAdapterRole.SCRIPTED_FAKE:
            if self.external_provider_calls != 0:
                raise ContractValidationError("fake Composer receipt cannot claim live calls")
        elif self.external_provider_calls != 1:
            raise ContractValidationError("DeepSeek Composer receipt requires one live call")


@dataclass(frozen=True, slots=True)
class ComposerValidationReceipt:
    SCHEMA_VERSION: ClassVar[str] = "cera.composer_validation_receipt.v1"

    schema_version: str
    validation_receipt_id: TypedId
    composer_request_sha256: str
    candidate_sha256: str
    manifest_sha256: str
    validation_kind: str
    status: str
    quarantined_advisory_count: int
    quarantined_advisory_sha256: tuple[str, ...]
    semantic_quality_proven: bool
    story_state_committed: bool

    def __post_init__(self) -> None:
        require_schema(self.schema_version, self.SCHEMA_VERSION, type(self).__name__)
        require_kind(
            self.validation_receipt_id,
            IdKind.VALIDATION,
            "validation_receipt_id",
        )
        for value in (
            self.composer_request_sha256,
            self.candidate_sha256,
            self.manifest_sha256,
        ):
            _sha256(value, "validation receipt hash")
        for value in self.quarantined_advisory_sha256:
            _sha256(value, "quarantined advisory hash")
        if self.validation_kind != "structural_candidate_validation":
            raise ContractValidationError("Phase 6 cannot claim semantic validation")
        if self.status != "accepted_in_memory":
            raise ContractValidationError("invalid Phase 6 validation status")
        if self.quarantined_advisory_count != len(self.quarantined_advisory_sha256):
            raise ContractValidationError("quarantine count does not match hashes")
        if self.semantic_quality_proven or self.story_state_committed:
            raise ContractValidationError("Phase 6 validation cannot prove quality or commit")


@dataclass(frozen=True, slots=True)
class ComposerExecutionResult:
    candidate: ComposerCandidate
    manifest: RealizationManifest
    composer_receipt: SceneComposerReceipt
    validation_receipt: ComposerValidationReceipt
    advisory_realization_metadata: tuple[object, ...]
    quarantined_advisory_metadata: tuple[object, ...]
    provider_call_receipt: LiveProviderCallReceipt | None
    # Compatibility-only for the separately governed aftermath application.
    # The normal live-shaped pipeline creates its AcceptedStoryArtifact only
    # after independent realization and adult-specific validation.
    accepted_artifact: AcceptedStoryArtifact | None = None

    def __post_init__(self) -> None:
        if self.composer_receipt.adapter_role is ComposerAdapterRole.SCRIPTED_FAKE:
            if self.provider_call_receipt is not None:
                raise ContractValidationError("fake Composer execution retained live receipt")
        else:
            if self.provider_call_receipt is None:
                raise ContractValidationError("DeepSeek Composer execution lacks provider receipt")
            if (
                self.provider_call_receipt.provider_receipt_id
                != self.composer_receipt.provider_receipt_id
                or self.provider_call_receipt.receipt_sha256
                != self.composer_receipt.provider_receipt_sha256
            ):
                raise ContractValidationError("Composer execution receipt mismatch")


@dataclass(frozen=True, slots=True)
class RenderedStory:
    SCHEMA_VERSION: ClassVar[str] = "cera.rendered_story.v1"

    schema_version: str
    rendered_artifact_id: TypedId
    accepted_artifact_id: TypedId
    accepted_artifact_sha256: str
    renderer_profile: str
    renderer_version: str
    rendered_text: str
    rendered_sha256: str

    def __post_init__(self) -> None:
        require_schema(self.schema_version, self.SCHEMA_VERSION, type(self).__name__)
        require_kind(
            self.rendered_artifact_id,
            IdKind.RENDERED_ARTIFACT,
            "rendered_artifact_id",
        )
        require_kind(self.accepted_artifact_id, IdKind.ARTIFACT, "accepted_artifact_id")
        _sha256(self.accepted_artifact_sha256, "accepted_artifact_sha256")
        _non_empty(self.renderer_profile, "renderer_profile")
        _non_empty(self.renderer_version, "renderer_version")
        _non_empty(self.rendered_text, "rendered_text")
        _sha256(self.rendered_sha256, "rendered_sha256")
        if text_sha256(self.rendered_text) != self.rendered_sha256:
            raise ContractValidationError("rendered story hash mismatch")


def _sha256(value: str, field_name: str) -> None:
    if not re_is_sha256(value):
        raise ContractValidationError(f"{field_name} must be SHA-256")


def _non_empty(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ContractValidationError(f"{field_name} must be non-empty")


def _unique(values, field_name: str) -> None:
    items = list(values)
    if len(items) != len(set(items)):
        raise ContractValidationError(f"{field_name} must not contain duplicates")
