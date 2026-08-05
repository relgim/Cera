"""Provider-neutral orchestration for the shadow continuous route."""

from __future__ import annotations

from dataclasses import dataclass, replace
import json
from pathlib import Path
import time
from typing import Any, Callable, Protocol

from cera.creator_review.models import CreatorReviewAction
from cera.errors import ContractValidationError, StateConflictError
from cera.serialization import canonical_sha256, re_is_sha256, text_sha256, to_primitive

from .contracts import (
    AcceptedFinalSequenceEnvelopeV1,
    AcceptedTurnPairV1,
    CharacterSummaryEnvelopeV1,
    ReaderVerdictStatus,
    ReaderVerdictV1,
    RichPlannerSequenceV1,
    PresentationRealizationSegmentV1,
    StoryRealizationSegmentV1,
    ValidatorFinalizationPackageV1,
    ValidatorSemanticStatus,
    ValidatorTaskMode,
    WriterMechanicalEnvelopeV1,
    WriterRecallDirectiveV1,
    WriterRealizationBoundaryV1,
)
from .ingress import ContinuousIngressAuthorityPort
from .evidence import (
    CompactAcceptedHeadReceiptV1,
    RequestEvidenceBindingRegistry,
    StableAcceptedContextReferenceStore,
    bind_character_summary_envelopes,
    build_character_summary_envelope,
    build_stable_accepted_context_references,
    project_final_sequence_facts,
    provider_fork_reference_synchronization_sha256,
    rebind_stable_accepted_context_references_for_reconstruction,
    reconstruction_reference_synchronization_sha256,
    stable_reference_descriptors_for_reconstruction_target,
    validate_stable_accepted_context_reference_facts,
    stable_reference_descriptors_for_facts,
    validate_character_summary_envelope,
)
from .prompting import (
    build_continuous_composer_prompt,
    build_planner_turn_prompt,
    build_reader_prompt,
    build_validator_prompt,
    continuous_writer_authority_package_sha256,
    planner_base_instruction_usage,
    prompt_text_usage,
    PLANNER_STABLE_INSTRUCTIONS,
)
from .packets import (
    LeanContinuationAuthorityV1,
    LeanSceneChangeContextV1,
    build_accepted_lean_continuation_authority,
    build_continuous_planner_turn_packet,
)
from .record_policy import PERSISTENCE_POLICY_SHA256
from .sessions import (
    ContinuousBranchForkReceiptV2,
    ContinuousBranchReferenceTransferReceiptV1,
    ContinuousSessionCoordinator,
    ContinuousSessionCompatibilityV1,
    ContinuousSessionInitializationKind,
    ContinuousSessionInitializationPacketV1,
    ContinuousSessionInitializationReceiptV1,
    ContinuousSessionReconstructionBundleV1,
    ContinuousSessionRole,
    ContinuousSessionSnapshotStore,
    ContinuousThreadArchiveEvidenceV1,
    PlannerContextMode,
    assert_separate_role_sessions,
    continuous_branch_privacy_boundary_sha256,
)
from .thread_lineage import (
    ContinuousThreadLineageLedger,
    ContinuousThreadLineageReceiptV1,
)
from .world import (
    CandidateWorldViewV1,
    ContinuousBranchMaterializationReceiptV1,
    ContinuousBranchMaterializationReceiptV2,
    ContinuousDebugRecorder,
    ContinuousInheritedSummarySourceV1,
    ContinuousWorldStore,
    SceneChangeCoordinator,
    SceneChangeEnvelopeV1,
    WorldPromotionReceiptV1,
)


class PlannerPort(Protocol):
    def plan(
        self,
        prompt: str,
        *,
        protected_user_id: str | None = None,
        protected_user_source_claim_keys: tuple[str, ...] = (),
    ): ...


class ComposerPort(Protocol):
    def compose(self, prompt: str): ...


class ValidatorPort(Protocol):
    def validate(
        self,
        prompt: str,
        *,
        writer_story_text: str | None,
        **kwargs,
    ): ...


class ReaderPort(Protocol):
    def review(self, prompt: str, *, writer_story_text: str): ...


def _writer_attempt_number_for_recall(
    writer_recall_directive: WriterRecallDirectiveV1 | None,
    *,
    frozen_authority_package_sha256: str,
) -> int:
    """Validate one internal recall handoff before the fresh Writer dispatch."""

    if writer_recall_directive is None:
        return 1
    if not isinstance(writer_recall_directive, WriterRecallDirectiveV1):
        raise ContractValidationError("Writer recall input uses the wrong contract")
    if (
        writer_recall_directive.frozen_authority_package_sha256
        != frozen_authority_package_sha256
    ):
        raise StateConflictError("Writer recall changed the frozen authority package")
    return writer_recall_directive.next_attempt_number


def _planner_reference_only_character_ids(
    planner_sequence: RichPlannerSequenceV1,
) -> tuple[str, ...]:
    """Validate current participant semantics and return authorized references."""

    selected = set(planner_sequence.selected_character_ids)
    omitted = set(planner_sequence.omitted_character_ids)
    if "character:ted" in selected:
        raise PermissionError(
            "Planner selected cast cannot contain the protected user"
        )
    authorized_references: set[str] = set()
    non_reference_fields = (
        "action_owner_ids",
        "state_owner_ids",
        "speaker_ids",
        "affected_ids",
        "addressed_ids",
        "observing_ids",
    )
    for beat in planner_sequence.beats:
        for character_id in beat.roles.involved_ids:
            if character_id == "character:ted" or character_id in selected:
                continue
            if character_id not in omitted:
                raise PermissionError(
                    "Planner beat introduced a character outside selected and omitted cast"
                )
            if character_id not in beat.roles.referenced_ids or any(
                character_id in getattr(beat.roles, field)
                for field in non_reference_fields
            ):
                raise PermissionError(
                    "Planner omitted character may appear only as a reference"
                )
            authorized_references.add(character_id)
    return tuple(
        character_id
        for character_id in planner_sequence.omitted_character_ids
        if character_id in authorized_references
    )


def _validator_package_id(
    *,
    task_mode: ValidatorTaskMode,
    world_id: str,
    branch_id: str,
    accepted_turn_id: str | None,
    accepted_scene_turn_ids: tuple[str, ...],
    candidate_id: str | None,
) -> str:
    return "package:" + canonical_sha256(
        {
            "contract": "cera.validator_package_identity.v1",
            "task_mode": task_mode.value,
            "world_id": world_id,
            "branch_id": branch_id,
            "accepted_turn_id": accepted_turn_id,
            "accepted_scene_turn_ids": accepted_scene_turn_ids,
            "candidate_id": candidate_id,
        }
    )


@dataclass(frozen=True, slots=True)
class ContinuousTurnRequestV1:
    world_id: str
    branch_id: str
    session_id: str
    request_id: str
    idempotency_key_sha256: str
    scene_id: str
    turn_id: str
    user_message: str
    ingress_receipt_id: str
    ingress_receipt_sha256: str
    character_summaries: tuple[CharacterSummaryEnvelopeV1, ...] = ()
    cera_scene_change: bool = False
    context_mode: PlannerContextMode = PlannerContextMode.LEAN_CONTINUOUS
    projection_assisted_trigger: str | None = None
    projection_reference_keys: tuple[str, ...] = ()
    planner_requested_character_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for field in (
            "world_id",
            "branch_id",
            "session_id",
            "request_id",
            "scene_id",
            "turn_id",
            "user_message",
        ):
            value = getattr(self, field)
            if not isinstance(value, str) or not value.strip():
                raise ContractValidationError(f"continuous turn {field} is required")
        if type(self.cera_scene_change) is not bool:
            raise ContractValidationError("continuous scene change flag must be boolean")
        if self.context_mode is PlannerContextMode.RECONSTRUCTION:
            raise ContractValidationError(
                "reconstruction is a session initialization, not a turn request"
            )
        if self.context_mode is PlannerContextMode.PROJECTION_ASSISTED:
            if (
                not isinstance(self.projection_assisted_trigger, str)
                or not self.projection_assisted_trigger.strip()
                or not self.projection_reference_keys
            ):
                raise ContractValidationError(
                    "projection-assisted turn lacks its demonstrated trigger and keys"
                )
        elif self.projection_assisted_trigger is not None or self.projection_reference_keys:
            raise ContractValidationError(
                "non-projection turn carries projection-assisted state"
            )
        if len(self.projection_reference_keys) != len(
            set(self.projection_reference_keys)
        ):
            raise ContractValidationError("projection-assisted keys are duplicated")
        if len(self.planner_requested_character_ids) != len(
            set(self.planner_requested_character_ids)
        ):
            raise ContractValidationError("Planner-requested character IDs are duplicated")
        if not re_is_sha256(self.idempotency_key_sha256):
            raise ContractValidationError(
                "continuous turn idempotency identity is invalid"
            )
        if not self.ingress_receipt_id.strip() or not re_is_sha256(
            self.ingress_receipt_sha256
        ):
            raise ContractValidationError(
                "continuous turn requires a trusted ingress receipt reference"
            )


@dataclass(frozen=True, slots=True)
class ContinuousTurnCandidateV1:
    request: ContinuousTurnRequestV1
    candidate_view: CandidateWorldViewV1
    planner_sequence: RichPlannerSequenceV1
    deepseek_story_text: str
    writer_mechanical_envelope: WriterMechanicalEnvelopeV1
    validator_story_segments: tuple[StoryRealizationSegmentV1, ...]
    validator_package: ValidatorFinalizationPackageV1
    reader_verdict: ReaderVerdictV1
    planner_prompt_sha256: str
    composer_prompt_sha256: str
    validator_prompt_sha256: str
    reader_prompt_sha256: str
    debug_root: Path
    provider_calls: int
    evidence_registry_sha256: str
    protected_user_claim_ledger_sha256: str
    protected_user_realization_ledger_sha256: str
    story_segment_ledger_sha256: str
    protected_semantic_adjudication_ledger_sha256: str
    accepted_session_projection_ledger_sha256: str
    validator_cited_accepted_evidence_sha256: str
    reader_session_sha256: str
    planner_authority_packet_schema_version: str
    planner_authority_packet_kind: str
    planner_authority_packet_sha256: str
    planner_authority_packet_bytes: int
    session_initialization_packet_kind: str
    session_initialization_packet_sha256: str
    session_initialization_packet_bytes: int
    context_mode: PlannerContextMode = PlannerContextMode.LEAN_CONTINUOUS
    compact_accepted_head_receipt_sha256: str | None = None
    character_summary_delivery_receipt_sha256s: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if (
            not self.validator_story_segments
            or any(
                not isinstance(value, StoryRealizationSegmentV1)
                for value in self.validator_story_segments
            )
        ):
            raise ContractValidationError(
                "review-ready candidate requires canonical Validator story segments"
            )
        if (
            not isinstance(self.validator_package, ValidatorFinalizationPackageV1)
            or self.validator_package.task_mode
            is not ValidatorTaskMode.FINALIZE_TURN
            or self.validator_package.semantic_status
            not in {
                ValidatorSemanticStatus.ACCEPTED,
                ValidatorSemanticStatus.CONCERN,
            }
        ):
            raise ContractValidationError(
                "review-ready candidate requires an accepted canonical finalization"
            )

    @property
    def authority_context_sha256(self) -> str:
        return canonical_sha256(
            {
                "evidence_registry_sha256": self.evidence_registry_sha256,
                "protected_user_claim_ledger_sha256": self.protected_user_claim_ledger_sha256,
                "protected_user_realization_ledger_sha256": self.protected_user_realization_ledger_sha256,
                "story_segment_ledger_sha256": self.story_segment_ledger_sha256,
                "protected_semantic_adjudication_ledger_sha256": (
                    self.protected_semantic_adjudication_ledger_sha256
                ),
                "accepted_session_projection_ledger_sha256": self.accepted_session_projection_ledger_sha256,
                "validator_cited_accepted_evidence_sha256": (
                    self.validator_cited_accepted_evidence_sha256
                ),
                "writer_mechanical_envelope": self.writer_mechanical_envelope,
                "reader_verdict": self.reader_verdict,
                "reader_session_sha256": self.reader_session_sha256,
                "planner_authority_packet_schema_version": (
                    self.planner_authority_packet_schema_version
                ),
                "planner_authority_packet_kind": self.planner_authority_packet_kind,
                "planner_authority_packet_sha256": (
                    self.planner_authority_packet_sha256
                ),
                "planner_authority_packet_bytes": self.planner_authority_packet_bytes,
                "session_initialization_packet_kind": (
                    self.session_initialization_packet_kind
                ),
                "session_initialization_packet_sha256": (
                    self.session_initialization_packet_sha256
                ),
                "session_initialization_packet_bytes": (
                    self.session_initialization_packet_bytes
                ),
                "context_mode": self.context_mode.value,
                "compact_accepted_head_receipt_sha256": self.compact_accepted_head_receipt_sha256,
                "character_summary_delivery_receipt_sha256s": self.character_summary_delivery_receipt_sha256s,
                "ingress_receipt_sha256": self.request.ingress_receipt_sha256,
                "persistence_policy_sha256": PERSISTENCE_POLICY_SHA256,
                "planner_prompt_sha256": self.planner_prompt_sha256,
                "composer_prompt_sha256": self.composer_prompt_sha256,
                "validator_prompt_sha256": self.validator_prompt_sha256,
                "reader_prompt_sha256": self.reader_prompt_sha256,
                "planner_schema_version": self.planner_sequence.schema_version,
                "validator_schema_version": self.validator_package.schema_version,
                "reader_schema_version": self.reader_verdict.schema_version,
            }
        )

    @property
    def candidate_sha256(self) -> str:
        return canonical_sha256(
            {
                "turn_id": self.request.turn_id,
                "planner": self.planner_sequence.sequence_sha256,
                "story": text_sha256(self.deepseek_story_text),
                "validator": self.validator_package.package_sha256,
                "reader": canonical_sha256(self.reader_verdict),
                "authority_context_sha256": self.authority_context_sha256,
            }
        )


@dataclass(frozen=True, slots=True)
class ContinuousSceneChangeCandidateV1:
    scene_change_envelope: SceneChangeEnvelopeV1
    scene_summary_package: ValidatorFinalizationPackageV1
    turn_candidate: ContinuousTurnCandidateV1
    summary_provider_calls: int


@dataclass(frozen=True, slots=True)
class ContinuousSceneSummaryCandidateV1:
    scene_change_envelope: SceneChangeEnvelopeV1
    scene_summary_package: ValidatorFinalizationPackageV1
    summary_provider_calls: int


@dataclass(frozen=True, slots=True)
class ContinuousForkedPlannerSessionV2:
    """Completed child Planner custody returned by the integrated fork entrypoint."""

    coordinator: ContinuousSessionCoordinator
    transfer_receipt: ContinuousBranchReferenceTransferReceiptV1
    stable_reference_paths: tuple[Path, ...]
    session_snapshot_path: Path


class ContinuousShadowTurnCoordinator:
    """Execute shadow candidates; creator action remains the only promotion gate."""

    def __init__(
        self,
        *,
        world: ContinuousWorldStore,
        planner_session: ContinuousSessionCoordinator,
        validator_session: ContinuousSessionCoordinator,
        planner: PlannerPort,
        composer: ComposerPort,
        validator: ValidatorPort,
        reader: ReaderPort,
        ingress_authority: ContinuousIngressAuthorityPort,
        validator_contract_profile: str = "exhaustive_v13",
        acceptance_sync_failpoint: Callable[[str], None] | None = None,
        thread_lifecycle_failpoint: Callable[[str], None] | None = None,
        thread_lineage_ledger: ContinuousThreadLineageLedger | None = None,
    ) -> None:
        assert_separate_role_sessions(planner_session, validator_session)
        if planner_session.compatibility.role is not ContinuousSessionRole.PLANNER:
            raise StateConflictError("continuous Planner session role changed")
        if validator_session.compatibility.role is not ContinuousSessionRole.VALIDATOR:
            raise StateConflictError("continuous Validator session role changed")
        self.world = world
        self.planner_session = planner_session
        self.validator_session = validator_session
        self.planner = planner
        self.composer = composer
        self.validator = validator
        self.reader = reader
        self.ingress_authority = ingress_authority
        if validator_contract_profile not in {"exhaustive_v13", "compact_v1"}:
            raise StateConflictError("continuous Validator contract profile changed")
        if (
            validator_contract_profile == "compact_v1"
            and not bool(getattr(validator, "uses_compact_turn_contract", False))
        ):
            raise StateConflictError(
                "compact Validator prompt requires the compact provider adapter"
            )
        self.validator_contract_profile = validator_contract_profile
        self._acceptance_sync_failpoint = acceptance_sync_failpoint
        self._thread_lifecycle_failpoint = thread_lifecycle_failpoint
        inherited_lineages = tuple(
            value
            for value in (
                self.planner_session._thread_lineage,
                self.validator_session._thread_lineage,
            )
            if value is not None
        )
        if (
            inherited_lineages
            and any(value is not inherited_lineages[0] for value in inherited_lineages)
        ):
            raise StateConflictError("continuous role sessions changed lineage owner")
        self.thread_lineage = (
            thread_lineage_ledger
            or (inherited_lineages[0] if inherited_lineages else None)
            or ContinuousThreadLineageLedger()
        )
        self.last_reconstruction_prior_archive: (
            ContinuousThreadArchiveEvidenceV1 | None
        ) = None
        self.planner_session.attach_thread_lineage(
            self.thread_lineage,
            purpose="primary_planner",
        )
        self.validator_session.attach_thread_lineage(
            self.thread_lineage,
            purpose="primary_validator",
        )
        self._candidates: dict[str, ContinuousTurnCandidateV1] = {}
        self._reader_session_sha256s: set[str] = set()
        if not self.planner_session.base_instructions:
            self.planner_session.install_base_instructions(
                PLANNER_STABLE_INSTRUCTIONS
            )
        elif PLANNER_STABLE_INSTRUCTIONS not in (
            self.planner_session.base_instructions
        ):
            raise StateConflictError(
                "continuous Planner base omits stable D-200 instructions"
            )

    def materialize_planner_branch(
        self,
        *,
        target_compatibility: ContinuousSessionCompatibilityV1,
        required_character_summaries: tuple[CharacterSummaryEnvelopeV1, ...] = (),
    ) -> ContinuousBranchMaterializationReceiptV1 | ContinuousBranchMaterializationReceiptV2:
        """Create the complete child ACTIVE snapshot before any fork transport."""

        parent = self.planner_session
        self._assert_branch_compatibility(target_compatibility)
        parent_handle = parent.ensure_session()
        parent_snapshot = parent.snapshot()
        accepted_turn_ids = parent_snapshot.accepted_turn_ids
        if not accepted_turn_ids:
            raise StateConflictError("branch materialization lacks an accepted checkpoint")
        accepted_envelopes = {
            event.turn_or_scene_id: event.payload_sha256
            for event in parent_snapshot.context_events
            if event.event_type == "accepted_final_sequence"
        }
        accepted_ancestry_sha256 = canonical_sha256(
            {
                "accepted_turn_ids": accepted_turn_ids,
                "accepted_envelopes": tuple(
                    (turn_id, accepted_envelopes[turn_id])
                    for turn_id in accepted_turn_ids
                ),
            }
        )
        inherited_summary_sources = self._merge_summary_sources(
            self._current_inherited_summary_sources(
                parent_snapshot,
                branch_id=parent.compatibility.branch_id,
            ),
            self._summary_sources_for_envelopes(
                required_character_summaries,
                branch_id=parent.compatibility.branch_id,
            ),
        )
        return self.world.materialize_branch_from_checkpoint(
            world_id=parent.compatibility.world_id,
            parent_branch_id=parent.compatibility.branch_id,
            child_branch_id=target_compatibility.branch_id,
            accepted_checkpoint_turn_id=accepted_turn_ids[-1],
            ordered_accepted_turn_ids=accepted_turn_ids,
            accepted_ancestry_sha256=accepted_ancestry_sha256,
            parent_provider_thread_sha256=parent_handle.provider_thread_id_sha256,
            parent_world_directory_identity_sha256=(
                parent.compatibility.world_directory_identity_sha256
            ),
            child_world_directory_identity_sha256=(
                target_compatibility.world_directory_identity_sha256
            ),
            authority_policy_version=parent.compatibility.authority_policy_version,
            privacy_policy_version=parent.compatibility.privacy_policy_version,
            protected_user_policy_version=(
                parent.compatibility.protected_user_policy_version
            ),
            session_policy_version=parent.compatibility.session_policy_version,
            persistence_policy_sha256=parent.compatibility.persistence_policy_sha256,
            inherited_summary_sources=inherited_summary_sources,
        )

    def fork_planner_session_for_branch(
        self,
        *,
        target_compatibility: ContinuousSessionCompatibilityV1,
        materialization_receipt: ContinuousBranchMaterializationReceiptV1
        | ContinuousBranchMaterializationReceiptV2,
        branch_receipt: ContinuousBranchForkReceiptV2,
    ) -> ContinuousForkedPlannerSessionV2:
        """Fork one accepted Planner checkpoint and close child reference custody."""

        parent = self.planner_session
        self._assert_branch_compatibility(target_compatibility)
        parent_handle = parent.ensure_session()
        parent_snapshot = parent.snapshot()
        accepted_turn_ids = parent_snapshot.accepted_turn_ids
        if not accepted_turn_ids:
            raise StateConflictError("provider fork lacks an accepted checkpoint")
        expected_summaries = self._current_inherited_summary_sources(
            parent_snapshot,
            branch_id=parent.compatibility.branch_id,
        )
        if (
            branch_receipt.branch_materialization_receipt_sha256
            != materialization_receipt.receipt_sha256
            or materialization_receipt.world_id != parent.compatibility.world_id
            or materialization_receipt.parent_branch_id
            != parent.compatibility.branch_id
            or materialization_receipt.child_branch_id
            != target_compatibility.branch_id
            or materialization_receipt.accepted_checkpoint_turn_id
            != accepted_turn_ids[-1]
            or materialization_receipt.accepted_ancestry_sha256
            != branch_receipt.accepted_ancestry_sha256
            or materialization_receipt.parent_provider_thread_sha256
            != parent_handle.provider_thread_id_sha256
            or materialization_receipt.inherited_summary_sources
            != expected_summaries
            or materialization_receipt.authority_policy_version
            != parent.compatibility.authority_policy_version
            or materialization_receipt.privacy_policy_version
            != parent.compatibility.privacy_policy_version
            or materialization_receipt.protected_user_policy_version
            != parent.compatibility.protected_user_policy_version
            or materialization_receipt.session_policy_version
            != parent.compatibility.session_policy_version
            or materialization_receipt.persistence_policy_sha256
            != parent.compatibility.persistence_policy_sha256
        ):
            raise StateConflictError(
                "provider fork materialization receipt changed branch custody"
            )
        self.world.validate_branch_materialization(materialization_receipt)
        parent_root = self.world.branch_root(
            parent.compatibility.world_id,
            parent.compatibility.branch_id,
        )
        child_root = self.world.branch_root(
            target_compatibility.world_id,
            target_compatibility.branch_id,
        )
        if not child_root.is_dir():
            raise StateConflictError(
                "provider fork requires an existing child story branch"
            )
        source_store = StableAcceptedContextReferenceStore(parent_root)
        source_sets = []
        child_envelopes = []
        for accepted_turn_id in accepted_turn_ids:
            source_receipt, source_references = source_store.load(
                accepted_turn_id,
                provider_thread_sha256=parent_handle.provider_thread_id_sha256,
            )
            parent_envelope = self.world.accepted_final_envelope(
                parent.compatibility.world_id,
                parent.compatibility.branch_id,
                accepted_turn_id,
            )
            child_envelope = self.world.accepted_final_envelope(
                target_compatibility.world_id,
                target_compatibility.branch_id,
                accepted_turn_id,
            )
            if child_envelope != parent_envelope:
                raise StateConflictError(
                    "provider fork child changed accepted checkpoint bytes"
                )
            validate_stable_accepted_context_reference_facts(
                envelope=parent_envelope,
                references=source_references,
            )
            if (
                source_receipt.world_id != parent.compatibility.world_id
                or source_receipt.branch_id != parent.compatibility.branch_id
                or source_receipt.accepted_envelope_sha256
                != parent_envelope.envelope_sha256
            ):
                raise StateConflictError(
                    "provider fork source reference custody changed"
                )
            source_sets.append((source_receipt, source_references))
            child_envelopes.append(child_envelope)
        self.world.validate_branch_materialization(materialization_receipt)
        child = parent.fork_for_branch(
            target_compatibility,
            branch_receipt=branch_receipt,
            inherited_summary_envelope_sha256s=tuple(
                value.envelope_sha256 for value in expected_summaries
            ),
            lifecycle_failpoint=self._thread_lifecycle_failpoint,
        )
        try:
            return self._complete_forked_planner_setup(
                child=child,
                parent=parent,
                parent_handle=parent_handle,
                parent_root=parent_root,
                child_root=child_root,
                accepted_turn_ids=accepted_turn_ids,
                child_envelopes=tuple(child_envelopes),
                source_sets=tuple(source_sets),
                target_compatibility=target_compatibility,
                branch_receipt=branch_receipt,
                materialization_receipt=materialization_receipt,
            )
        except BaseException as exc:
            if not child._terminally_archived:
                self.thread_lineage.record_abandoned(
                    child.ensure_session().provider_thread_id_sha256,
                    reason_sha256=text_sha256("fork_post_creation_setup_failed"),
                )
                evidence = child.archive_and_verify_terminal(
                    "fork_post_creation_setup_failed"
                )
                if not evidence.verified:
                    raise StateConflictError(
                        "fork setup failed and child archival was not verified"
                    ) from exc
            raise

    def _complete_forked_planner_setup(
        self,
        *,
        child: ContinuousSessionCoordinator,
        parent: ContinuousSessionCoordinator,
        parent_handle,
        parent_root: Path,
        child_root: Path,
        accepted_turn_ids: tuple[str, ...],
        child_envelopes: tuple[AcceptedFinalSequenceEnvelopeV1, ...],
        source_sets: tuple[tuple[Any, Any], ...],
        target_compatibility: ContinuousSessionCompatibilityV1,
        branch_receipt: ContinuousBranchForkReceiptV2,
        materialization_receipt: ContinuousBranchMaterializationReceiptV1
        | ContinuousBranchMaterializationReceiptV2,
    ) -> ContinuousForkedPlannerSessionV2:
        self.world.validate_branch_materialization(materialization_receipt)
        self._thread_failpoint(
            "after_child_branch_validation_before_descriptor_append"
        )
        child_descriptors = tuple(
            descriptor
            for _source_receipt, source_references in source_sets
            for descriptor in stable_reference_descriptors_for_reconstruction_target(
                source_references=source_references,
                target_world_id=target_compatibility.world_id,
                target_branch_id=target_compatibility.branch_id,
            )
        )
        parent_reference_keys = tuple(
            reference.reference_key
            for _source_receipt, source_references in source_sets
            for reference in source_references
        )
        transfer = child.establish_branch_reference_rebinding(
            child_envelopes[-1],
            branch_receipt=branch_receipt,
            parent_reference_keys=parent_reference_keys,
            child_reference_descriptors=child_descriptors,
        )
        self._thread_failpoint("after_child_descriptor_append")
        child_handle = child.ensure_session()
        child_snapshot = child.snapshot()
        stable_paths = []
        child_store = StableAcceptedContextReferenceStore(child_root)
        for reference_index, (source_receipt, source_references) in enumerate(
            source_sets, 1
        ):
            rebound_receipt, rebound_references = (
                rebind_stable_accepted_context_references_for_reconstruction(
                    source_receipt=source_receipt,
                    source_references=source_references,
                    planner_session_id=child_handle.provider_session_id,
                    provider_thread_sha256=child_handle.provider_thread_id_sha256,
                    accepted_turn_ids=accepted_turn_ids,
                    initialization_receipt_sha256=(
                        transfer.operation_receipt_sha256
                    ),
                    session_snapshot_sha256=child_snapshot.snapshot_sha256,
                    target_world_id=target_compatibility.world_id,
                    target_branch_id=target_compatibility.branch_id,
                    synchronization_kind="accepted_checkpoint_fork",
                )
            )
            stable_paths.append(
                child_store.save(
                    receipt=rebound_receipt,
                    references=rebound_references,
                )
            )
            self._thread_failpoint(
                f"after_child_accepted_reference_save:{reference_index}"
            )
            self._thread_failpoint("after_child_accepted_reference_save")
        self._thread_failpoint("before_child_snapshot_persistence")
        snapshot_path = ContinuousSessionSnapshotStore(child_root).save(
            child_snapshot
        )
        self._thread_failpoint("after_child_snapshot_persistence")
        return ContinuousForkedPlannerSessionV2(
            coordinator=child,
            transfer_receipt=transfer,
            stable_reference_paths=tuple(stable_paths),
            session_snapshot_path=snapshot_path,
        )

    def archive_auxiliary_planner_session(
        self,
        forked: ContinuousForkedPlannerSessionV2,
        *,
        reason: str,
    ) -> ContinuousThreadArchiveEvidenceV1:
        child = forked.coordinator
        if child is self.planner_session:
            raise StateConflictError("authorized active Planner is not auxiliary")
        self.thread_lineage.record_abandoned(
            child.ensure_session().provider_thread_id_sha256,
            reason_sha256=text_sha256(reason),
        )
        evidence = child.archive_and_verify_terminal(reason)
        if not evidence.verified:
            raise StateConflictError("auxiliary Planner archival was not verified")
        return evidence

    def adopt_forked_planner_session(
        self,
        forked: ContinuousForkedPlannerSessionV2,
        *,
        reason: str,
    ) -> ContinuousThreadArchiveEvidenceV1:
        """Transfer route ownership to one verified child and retire its parent."""

        prior = self.planner_session
        child = forked.coordinator
        if child is prior:
            raise StateConflictError("fork adoption did not change Planner thread")
        if child._thread_lineage is not self.thread_lineage:
            raise StateConflictError("fork adoption changed thread-lineage custody")
        if not isinstance(reason, str) or not reason.strip():
            raise ContractValidationError("fork adoption reason is required")
        prior_handle = prior.ensure_session()
        child_handle = child.ensure_session()
        initialization = child.initialization_receipt
        if (
            initialization.packet_kind
            is not ContinuousSessionInitializationKind.ACCEPTED_CHECKPOINT_FORK_INITIALIZATION
            or initialization.parent_provider_thread_sha256
            != prior_handle.provider_thread_id_sha256
            or child_handle.provider_thread_id_sha256
            == prior_handle.provider_thread_id_sha256
        ):
            raise StateConflictError("fork adoption changed parent-child custody")
        try:
            prior_archive = prior.archive_and_verify_terminal(
                "planner_fork_superseded_prior"
            )
            if not prior_archive.verified:
                raise StateConflictError(
                    "fork adoption prior Planner archival was not verified"
                )
            self.thread_lineage.record_adoption(
                child_handle.provider_thread_id_sha256,
                reason_sha256=text_sha256(reason),
                superseded_thread_sha256=prior_handle.provider_thread_id_sha256,
            )
            self._thread_failpoint("after_child_adoption_before_return")
            self.planner_session = child
            assert_separate_role_sessions(child, self.validator_session)
            return prior_archive
        except BaseException as exc:
            if not child._terminally_archived:
                self.thread_lineage.record_abandoned(
                    child_handle.provider_thread_id_sha256,
                    reason_sha256=text_sha256("fork_adoption_failed"),
                )
                child_archive = child.archive_and_verify_terminal(
                    "fork_adoption_failed"
                )
                if not child_archive.verified:
                    raise StateConflictError(
                        "fork adoption failed and child archival was not verified"
                    ) from exc
            raise

    def finalize_thread_lineage(
        self, *, authorized_active: bool
    ) -> ContinuousThreadLineageReceiptV1:
        active = {}
        if authorized_active:
            active = {
                "planner": self.planner_session.ensure_session().provider_thread_id_sha256,
                "validator": self.validator_session.ensure_session().provider_thread_id_sha256,
            }
        return self.thread_lineage.freeze(authorized_active_threads=active)

    def _thread_failpoint(self, stage: str) -> None:
        if self._thread_lifecycle_failpoint is not None:
            self._thread_lifecycle_failpoint(stage)

    def reconstruct_planner_session(
        self,
        *,
        bundle: ContinuousSessionReconstructionBundleV1,
        expected_compatibility: ContinuousSessionCompatibilityV1 | None = None,
        branch_receipt: ContinuousBranchForkReceiptV2 | None = None,
        materialization_receipt: ContinuousBranchMaterializationReceiptV1
        | ContinuousBranchMaterializationReceiptV2
        | None = None,
    ) -> ContinuousSessionInitializationReceiptV1:
        """Replace a lost/non-forkable Planner thread with bounded authority."""

        prior = self.planner_session
        target_compatibility = expected_compatibility or prior.compatibility
        if target_compatibility.role is not ContinuousSessionRole.PLANNER:
            raise StateConflictError("reconstruction target is not a Planner session")
        prior_handle = prior.handle
        if prior_handle is None:
            prior_handle = prior.ensure_session()
        branch_changed = (
            target_compatibility.branch_id != prior.compatibility.branch_id
        )
        if branch_changed and bundle.reconstruction_reason != "non_forkable_branch":
            raise StateConflictError(
                "cross-branch reconstruction is not classified as non-forkable"
            )
        if not branch_changed and bundle.reconstruction_reason == "non_forkable_branch":
            raise StateConflictError(
                "non-forkable reconstruction did not create a child branch"
            )
        if target_compatibility.world_id != prior.compatibility.world_id:
            raise StateConflictError("reconstruction changed world identity")
        if not branch_changed and target_compatibility != prior.compatibility:
            raise StateConflictError(
                "same-branch reconstruction changed Planner compatibility"
            )
        if branch_changed:
            self._assert_branch_compatibility(target_compatibility)
            if branch_receipt is None:
                raise StateConflictError(
                    "non-forkable branch reconstruction lacks its Python receipt"
                )
            prior_snapshot = prior.snapshot()
            accepted_turn_ids = prior_snapshot.accepted_turn_ids
            accepted_envelopes = {
                event.turn_or_scene_id: event.payload_sha256
                for event in prior_snapshot.context_events
                if event.event_type == "accepted_final_sequence"
            }
            expected_ancestry = canonical_sha256(
                {
                    "accepted_turn_ids": accepted_turn_ids,
                    "accepted_envelopes": tuple(
                        (turn_id, accepted_envelopes[turn_id])
                        for turn_id in accepted_turn_ids
                    ),
                }
            )
            expected_privacy_boundary = continuous_branch_privacy_boundary_sha256(
                parent_compatibility=prior.compatibility,
                child_compatibility=target_compatibility,
                accepted_checkpoint_turn_id=accepted_turn_ids[-1],
                accepted_ancestry_sha256=expected_ancestry,
                parent_provider_thread_sha256=(
                    prior_handle.provider_thread_id_sha256
                ),
            )
            if materialization_receipt is None:
                raise StateConflictError(
                    "non-forkable branch reconstruction lacks materialization custody"
                )
            if (
                not accepted_turn_ids
                or branch_receipt.world_id != target_compatibility.world_id
                or branch_receipt.parent_branch_id
                != prior.compatibility.branch_id
                or branch_receipt.child_branch_id
                != target_compatibility.branch_id
                or branch_receipt.accepted_checkpoint_turn_id
                != accepted_turn_ids[-1]
                or branch_receipt.accepted_ancestry_sha256
                != expected_ancestry
                or branch_receipt.parent_provider_thread_sha256
                != prior_handle.provider_thread_id_sha256
                or branch_receipt.privacy_boundary_sha256
                != expected_privacy_boundary
                or branch_receipt.branch_materialization_receipt_sha256
                != materialization_receipt.receipt_sha256
                or materialization_receipt.world_id
                != target_compatibility.world_id
                or materialization_receipt.parent_branch_id
                != prior.compatibility.branch_id
                or materialization_receipt.child_branch_id
                != target_compatibility.branch_id
                or materialization_receipt.accepted_checkpoint_turn_id
                != accepted_turn_ids[-1]
                or materialization_receipt.accepted_ancestry_sha256
                != expected_ancestry
                or materialization_receipt.parent_provider_thread_sha256
                != prior_handle.provider_thread_id_sha256
                or materialization_receipt.authority_policy_version
                != prior.compatibility.authority_policy_version
                or materialization_receipt.privacy_policy_version
                != prior.compatibility.privacy_policy_version
                or materialization_receipt.protected_user_policy_version
                != prior.compatibility.protected_user_policy_version
                or materialization_receipt.session_policy_version
                != prior.compatibility.session_policy_version
                or materialization_receipt.persistence_policy_sha256
                != prior.compatibility.persistence_policy_sha256
                or materialization_receipt.inherited_summary_sources
                != self._merge_summary_sources(
                    self._current_inherited_summary_sources(
                        prior_snapshot,
                        branch_id=prior.compatibility.branch_id,
                    ),
                    self._summary_sources_for_envelopes(
                        bundle.character_summaries,
                        branch_id=prior.compatibility.branch_id,
                    ),
                )
            ):
                raise StateConflictError(
                    "non-forkable branch reconstruction receipt changed ancestry"
                )
            self.world.validate_branch_materialization(materialization_receipt)
        elif branch_receipt is not None or materialization_receipt is not None:
            raise StateConflictError(
                "same-branch reconstruction cannot carry a branch receipt"
            )
        branch_root = self.world.branch_root(
            prior.compatibility.world_id,
            prior.compatibility.branch_id,
        )
        source_store = StableAcceptedContextReferenceStore(branch_root)
        source_sets = []
        for accepted in bundle.accepted_tail:
            source_receipt, source_references = source_store.load(
                accepted.envelope.accepted_turn_id,
                provider_thread_sha256=prior_handle.provider_thread_id_sha256,
            )
            if (
                source_receipt.accepted_envelope_sha256
                != accepted.envelope.envelope_sha256
                or tuple(
                    value.reference_key
                    for value in source_references
                )
                != tuple(
                    str(value["reference_key"])
                    for value in accepted.stable_reference_descriptors
                )
            ):
                raise StateConflictError(
                    "reconstruction bundle changed accepted-reference custody"
                )
            source_sets.append((source_receipt, source_references))
        target_tail = tuple(
            replace(
                accepted,
                stable_reference_descriptors=(
                    stable_reference_descriptors_for_reconstruction_target(
                        source_references=source_references,
                        target_world_id=target_compatibility.world_id,
                        target_branch_id=target_compatibility.branch_id,
                    )
                ),
            )
            for accepted, (_source_receipt, source_references) in zip(
                bundle.accepted_tail,
                source_sets,
                strict=True,
            )
        )
        target_bundle = replace(bundle, accepted_tail=target_tail)
        target_branch_root = self.world.branch_root(
            target_compatibility.world_id,
            target_compatibility.branch_id,
        )
        for summary in target_bundle.character_summaries:
            validate_character_summary_envelope(
                branch_root=target_branch_root,
                envelope=summary,
            )
        if branch_changed:
            assert materialization_receipt is not None
            self.world.validate_branch_materialization(materialization_receipt)
        rebuilt = ContinuousSessionCoordinator.reconstruct_new_thread(
            port=prior.port,
            expected_compatibility=target_compatibility,
            base_instructions=prior.base_instructions,
            bundle=target_bundle,
            parent_provider_thread_sha256=(
                prior_handle.provider_thread_id_sha256
            ),
            branch_receipt_sha256=(
                branch_receipt.receipt_sha256
                if branch_receipt is not None
                else None
            ),
            thread_lineage=self.thread_lineage,
            lifecycle_failpoint=self._thread_lifecycle_failpoint,
        )
        try:
            initialization = rebuilt.initialization_receipt
            if initialization is None:
                raise StateConflictError("reconstruction omitted initialization custody")
            rebuilt_snapshot = rebuilt.snapshot()
            rebuilt_handle = rebuilt.ensure_session()
            accepted_turn_ids = rebuilt_snapshot.accepted_turn_ids
            for reference_index, (source_receipt, source_references) in enumerate(
                source_sets, 1
            ):
                rebound_receipt, rebound_references = (
                    rebind_stable_accepted_context_references_for_reconstruction(
                        source_receipt=source_receipt,
                        source_references=source_references,
                        planner_session_id=rebuilt_handle.provider_session_id,
                        provider_thread_sha256=(
                            rebuilt_handle.provider_thread_id_sha256
                        ),
                        accepted_turn_ids=accepted_turn_ids,
                        initialization_receipt_sha256=(
                            initialization.receipt_sha256
                        ),
                        session_snapshot_sha256=rebuilt_snapshot.snapshot_sha256,
                        target_world_id=target_compatibility.world_id,
                        target_branch_id=target_compatibility.branch_id,
                    )
                )
                StableAcceptedContextReferenceStore(
                    self.world.branch_root(
                        target_compatibility.world_id,
                        target_compatibility.branch_id,
                    )
                ).save(
                    receipt=rebound_receipt,
                    references=rebound_references,
                )
                self._thread_failpoint(
                    "after_reconstruction_accepted_reference_save:"
                    f"{reference_index}"
                )
                self._thread_failpoint(
                    "after_reconstruction_accepted_reference_save"
                )
            prior_archive = prior.archive_and_verify_terminal(
                "planner_reconstruction_superseded_prior"
            )
            self.last_reconstruction_prior_archive = prior_archive
            if not prior_archive.verified:
                raise StateConflictError(
                    "reconstruction prior Planner archival was not verified"
                )
            self.thread_lineage.record_adoption(
                rebuilt_handle.provider_thread_id_sha256,
                reason_sha256=text_sha256("planner_reconstruction_adopted"),
                superseded_thread_sha256=prior_handle.provider_thread_id_sha256,
            )
            self._thread_failpoint(
                "after_reconstruction_adoption_before_return"
            )
            self.planner_session = rebuilt
            assert_separate_role_sessions(rebuilt, self.validator_session)
            return initialization
        except BaseException as exc:
            if not rebuilt._terminally_archived:
                self.thread_lineage.record_abandoned(
                    rebuilt.ensure_session().provider_thread_id_sha256,
                    reason_sha256=text_sha256("reconstruction_post_create_failed"),
                )
                evidence = rebuilt.archive_and_verify_terminal(
                    "reconstruction_post_create_failed"
                )
                if not evidence.verified:
                    raise StateConflictError(
                        "reconstruction failed and new Planner archival was not verified"
                    ) from exc
            raise

    def _assert_branch_compatibility(
        self, target: ContinuousSessionCompatibilityV1
    ) -> None:
        parent = self.planner_session.compatibility
        if (
            target.role is not ContinuousSessionRole.PLANNER
            or target.world_id != parent.world_id
            or target.branch_id == parent.branch_id
        ):
            raise StateConflictError("provider-fork target scope is invalid")
        comparable_fields = (
            "role",
            "world_id",
            "provider",
            "model",
            "reasoning_effort",
            "prompt_version",
            "output_schema_version",
            "authority_policy_version",
            "privacy_policy_version",
            "protected_user_policy_version",
            "session_policy_version",
            "ingress_classifier_registry_sha256",
            "persistence_policy_sha256",
            "default_context_mode",
            "allowed_context_modes",
        )
        if any(getattr(target, field) != getattr(parent, field) for field in comparable_fields):
            raise StateConflictError(
                "provider fork changed Planner provider or policy compatibility"
            )
        if (
            parent.world_directory_identity_sha256
            != self.world.branch_directory_identity_sha256(
                parent.world_id, parent.branch_id
            )
            or target.world_directory_identity_sha256
            != self.world.branch_directory_identity_sha256(
                target.world_id, target.branch_id
            )
        ):
            raise StateConflictError(
                "provider fork compatibility does not bind actual world directories"
            )

    def _current_inherited_summary_sources(
        self,
        snapshot,
        *,
        branch_id: str,
    ) -> tuple[ContinuousInheritedSummarySourceV1, ...]:
        branch_root = self.world.branch_root(snapshot.compatibility.world_id, branch_id)
        current = []
        for receipt in snapshot.character_summary_deliveries:
            try:
                envelope = build_character_summary_envelope(
                    branch_root=branch_root,
                    source_path=receipt.source_path_or_record_id,
                    character_id=receipt.character_id,
                )
            except (ContractValidationError, StateConflictError):
                continue
            if (
                envelope.source_revision != receipt.source_revision
                or envelope.source_sha256 != receipt.source_sha256
                or envelope.envelope_sha256 != receipt.envelope_sha256
            ):
                continue
            current.append(
                ContinuousInheritedSummarySourceV1(
                    character_id=receipt.character_id,
                    source_path_or_record_id=receipt.source_path_or_record_id,
                    source_revision=receipt.source_revision,
                    source_sha256=receipt.source_sha256,
                    source_authority_classification=(
                        envelope.source_authority_classification
                    ),
                    envelope_sha256=receipt.envelope_sha256,
                )
            )
        return tuple(
            sorted(
                current,
                key=lambda value: (
                    value.character_id,
                    value.source_path_or_record_id,
                ),
            )
        )

    def _summary_sources_for_envelopes(
        self,
        summaries: tuple[CharacterSummaryEnvelopeV1, ...],
        *,
        branch_id: str,
    ) -> tuple[ContinuousInheritedSummarySourceV1, ...]:
        branch_root = self.world.branch_root(
            self.planner_session.compatibility.world_id,
            branch_id,
        )
        result = []
        for summary in summaries:
            validate_character_summary_envelope(
                branch_root=branch_root,
                envelope=summary,
            )
            result.append(
                ContinuousInheritedSummarySourceV1(
                    character_id=summary.character_id,
                    source_path_or_record_id=summary.source_path_or_record_id,
                    source_revision=summary.source_revision,
                    source_sha256=summary.source_sha256,
                    source_authority_classification=(
                        summary.source_authority_classification
                    ),
                    envelope_sha256=summary.envelope_sha256,
                )
            )
        return tuple(
            sorted(
                result,
                key=lambda value: (
                    value.character_id,
                    value.source_path_or_record_id,
                ),
            )
        )

    @staticmethod
    def _merge_summary_sources(
        *groups: tuple[ContinuousInheritedSummarySourceV1, ...],
    ) -> tuple[ContinuousInheritedSummarySourceV1, ...]:
        merged: dict[tuple[str, str], ContinuousInheritedSummarySourceV1] = {}
        for source in (value for group in groups for value in group):
            key = (source.character_id, source.source_path_or_record_id)
            prior = merged.get(key)
            if prior is not None and prior != source:
                raise StateConflictError(
                    "branch materialization summary sources conflict"
                )
            merged[key] = source
        return tuple(merged[key] for key in sorted(merged))

    def restore_pending_accepted_context(self) -> tuple[str, ...]:
        """Report incomplete physical-thread synchronization after restart.

        V1 never retries an ambiguous history injection automatically.  The
        caller must stop and preserve the pending turn identities for governed
        diagnosis.
        """

        world_pending = self.world.pending_acceptance_synchronization(
            self.planner_session.compatibility.world_id,
            self.planner_session.compatibility.branch_id,
        )
        restored = []
        for turn_id in tuple(dict.fromkeys(
            world_pending + self.planner_session.unsynchronized_accepted_turn_ids
        )):
            envelope = self.world.accepted_final_envelope(
                self.planner_session.compatibility.world_id,
                self.planner_session.compatibility.branch_id,
                turn_id,
            )
            expected = next(
                (
                    value.payload_sha256
                    for value in self.planner_session.snapshot().context_events
                    if value.event_type == "accepted_final_sequence"
                    and value.turn_or_scene_id == turn_id
                ),
                None,
            )
            if expected is not None and envelope.envelope_sha256 != expected:
                raise StateConflictError("restored accepted-final envelope hash changed")
            restored.append(turn_id)
        return tuple(restored)

    def prepare(
        self,
        request: ContinuousTurnRequestV1,
        *,
        writer_recall_directive: WriterRecallDirectiveV1 | None = None,
    ) -> ContinuousTurnCandidateV1:
        if request.cera_scene_change:
            raise StateConflictError(
                "scene-change prompt must first pass the explicit SceneChangeCoordinator"
            )
        return self._prepare(
            request,
            scene_change_envelope=None,
            prior_provider_calls=0,
            writer_recall_directive=writer_recall_directive,
        )

    def prepare_after_validated_scene_change(
        self,
        request: ContinuousTurnRequestV1,
        *,
        scene_change_envelope: SceneChangeEnvelopeV1,
        summary_provider_calls: int,
        writer_recall_directive: WriterRecallDirectiveV1 | None = None,
    ) -> ContinuousTurnCandidateV1:
        """Continue after this coordinator's explicit summary phase completed."""

        if not request.cera_scene_change:
            raise StateConflictError("validated scene continuation requires the creator flag")
        if (
            scene_change_envelope.first_user_message_of_new_scene
            != request.user_message
            or type(summary_provider_calls) is not int
            or summary_provider_calls < 0
        ):
            raise StateConflictError("validated scene-change envelope scope changed")
        return self._prepare(
            replace(request, cera_scene_change=False),
            scene_change_envelope=scene_change_envelope.lean_planner_context(),
            prior_provider_calls=summary_provider_calls,
            writer_recall_directive=writer_recall_directive,
        )

    def prepare_scene_change(
        self,
        request: ContinuousTurnRequestV1,
        *,
        completed_scene_id: str,
        accepted_turn_ids: tuple[str, ...],
    ) -> ContinuousSceneChangeCandidateV1:
        summary = self.prepare_scene_change_summary(
            request,
            completed_scene_id=completed_scene_id,
            accepted_turn_ids=accepted_turn_ids,
        )
        turn_candidate = self.prepare_after_validated_scene_change(
            request,
            scene_change_envelope=summary.scene_change_envelope,
            summary_provider_calls=summary.summary_provider_calls,
        )
        return ContinuousSceneChangeCandidateV1(
            scene_change_envelope=summary.scene_change_envelope,
            scene_summary_package=summary.scene_summary_package,
            turn_candidate=turn_candidate,
            summary_provider_calls=summary.summary_provider_calls,
        )

    def prepare_scene_change_summary(
        self,
        request: ContinuousTurnRequestV1,
        *,
        completed_scene_id: str,
        accepted_turn_ids: tuple[str, ...],
    ) -> ContinuousSceneSummaryCandidateV1:
        if not request.cera_scene_change:
            raise StateConflictError("scene-change processing requires the creator flag")
        branch_root = self.world.initialize(request.world_id, request.branch_id)
        scene_debug = ContinuousDebugRecorder(
            branch_root, request.scene_id, request.turn_id
        )
        scene_debug.initialize()
        pairs = self.world.accepted_turn_pairs(
            request.world_id, request.branch_id, accepted_turn_ids
        )
        package_id = _validator_package_id(
            task_mode=ValidatorTaskMode.SCENE_SUMMARY,
            world_id=request.world_id,
            branch_id=request.branch_id,
            accepted_turn_id=None,
            accepted_scene_turn_ids=accepted_turn_ids,
            candidate_id=None,
        )
        prompt, _usage = build_validator_prompt(
            task_mode=ValidatorTaskMode.SCENE_SUMMARY,
            package_id=package_id,
            world_id=request.world_id,
            branch_id=request.branch_id,
            candidate_id=None,
            current_user_source=None,
            planner_sequence=None,
            writer_story_text=None,
            writer_mechanical_envelope=None,
            accepted_turn_id=None,
            accepted_scene_turn_ids=accepted_turn_ids,
            accepted_pairs=tuple(to_primitive(value) for value in pairs),
            world_file_manifest=self.world.active_manifest(
                request.world_id, request.branch_id
            ),
        )
        scene_debug.write_json("scene_change_request.json", {"prompt": prompt})
        started = time.perf_counter_ns()
        try:
            result = self.validator.validate(
                prompt,
                writer_story_text=None,
                accepted_pairs=pairs,
                expected_package_id=package_id,
                expected_world_id=request.world_id,
                expected_branch_id=request.branch_id,
            )
        except BaseException as exc:
            scene_debug.record_failure("scene_summary_validator", exc)
            raise
        elapsed = time.perf_counter_ns() - started
        semantic_result = result.value
        package = getattr(semantic_result, "finalization_package", None)
        if (
            getattr(semantic_result, "semantic_status", None)
            is not ValidatorSemanticStatus.ACCEPTED
            or package is None
            or tuple(
                getattr(semantic_result, "story_segments", ())
            )
        ):
            raise StateConflictError(
                "scene-summary Validator returned the wrong V3 result contract"
            )
        scene_debug.write_json("scene_change_output.json", to_primitive(package))
        scene_debug.write_json("scene_change_tools.json", _provider_debug(result))
        scene_debug.write_json("scene_change_timing.json", {"validator_ns": elapsed})
        if (
            package.task_mode is not ValidatorTaskMode.SCENE_SUMMARY
            or package.optional_scene_summary is None
            or package.world_id != request.world_id
            or package.branch_id != request.branch_id
        ):
            raise StateConflictError("Validator returned an invalid Scene Summary package")
        envelope = SceneChangeCoordinator(self.world).process(
            world_id=request.world_id,
            branch_id=request.branch_id,
            completed_scene_id=completed_scene_id,
            first_new_scene_prompt=request.user_message,
            accepted_turn_ids=accepted_turn_ids,
            summarize=lambda _pairs: package.optional_scene_summary,
        )
        self.validator_session.record_scene_summary(
            completed_scene_id, package.package_sha256
        )
        self.planner_session.record_scene_change(
            completed_scene_id, canonical_sha256(to_primitive(envelope))
        )
        summary_calls = int(
            getattr(getattr(result, "provider_receipt", None), "external_provider_calls", 0)
        )
        return ContinuousSceneSummaryCandidateV1(
            scene_change_envelope=envelope,
            scene_summary_package=package,
            summary_provider_calls=summary_calls,
        )

    def _prepare(
        self,
        request: ContinuousTurnRequestV1,
        *,
        scene_change_envelope: LeanSceneChangeContextV1 | None,
        prior_provider_calls: int,
        writer_recall_directive: WriterRecallDirectiveV1 | None,
    ) -> ContinuousTurnCandidateV1:
        if (
            request.world_id != self.planner_session.compatibility.world_id
            or request.branch_id != self.planner_session.compatibility.branch_id
            or request.world_id != self.validator_session.compatibility.world_id
            or request.branch_id != self.validator_session.compatibility.branch_id
        ):
            raise StateConflictError("continuous turn changed session world or branch")
        if request.context_mode not in self.planner_session.compatibility.allowed_context_modes:
            raise StateConflictError("continuous Planner context mode is not allowed")
        if request.context_mode is PlannerContextMode.RECONSTRUCTION:
            raise StateConflictError(
                "continuous reconstruction cannot occur inside an ordinary attempt"
            )
        ingress_receipt = self.ingress_authority.resolve(
            receipt_id=request.ingress_receipt_id,
            receipt_sha256=request.ingress_receipt_sha256,
        )
        if (
            ingress_receipt.world_id != request.world_id
            or ingress_receipt.branch_id != request.branch_id
            or ingress_receipt.session_id != request.session_id
            or ingress_receipt.request_id != request.request_id
            or ingress_receipt.turn_id != request.turn_id
            or ingress_receipt.idempotency_key_sha256
            != request.idempotency_key_sha256
            or ingress_receipt.raw_source_sha256 != text_sha256(request.user_message)
            or ingress_receipt.protected_user_id != "character:ted"
        ):
            raise StateConflictError(
                "continuous ingress receipt changed request, branch, source, or protected user"
            )
        source_units = ingress_receipt.source_units
        if request.turn_id in self._candidates:
            raise StateConflictError("continuous turn candidate already exists")
        if self.planner_session.unsynchronized_accepted_turn_ids:
            raise StateConflictError(
                "accepted Planner context is not synchronized; continuation is blocked"
            )
        if self.world.pending_acceptance_synchronization(
            request.world_id, request.branch_id
        ):
            raise StateConflictError(
                "accepted Planner context is not synchronized; continuation is blocked"
            )
        branch_root = self.world.initialize(request.world_id, request.branch_id)
        debug = ContinuousDebugRecorder(branch_root, request.scene_id, request.turn_id)
        debug.initialize()
        candidate_view = self.world.create_candidate(
            request.world_id, request.branch_id, request.turn_id
        )
        evidence_registry = RequestEvidenceBindingRegistry(
            world_id=request.world_id,
            branch_id=request.branch_id,
            turn_id=request.turn_id,
        )
        current_source_binding = evidence_registry.allocate_current_source(
            source_identity=f"current_user_source:{request.turn_id}",
            source_text=request.user_message,
            protected_user_allowance_scope="exact supplied source plus minimal nonbranching connective",
            source_units=source_units,
        )
        mechanical_binding = evidence_registry.allocate_mechanical_connective_allowance()
        (
            compact_accepted_head,
            stable_reference_bindings,
            projection_assisted_payloads,
            lean_continuation_authority,
        ) = self._bind_stable_accepted_context(
            request=request,
            registry=evidence_registry,
            branch_root=branch_root,
        )
        planner_summaries, summary_delivery_reasons = (
            self.planner_session.select_character_summaries(
                request.character_summaries,
                context_mode=request.context_mode,
                scene_change=scene_change_envelope is not None,
                explicit_need_character_ids=request.planner_requested_character_ids,
            )
        )
        summary_bindings = bind_character_summary_envelopes(
            registry=evidence_registry,
            branch_root=branch_root,
            summaries=planner_summaries,
        )
        compact_ingress_custody = {
            "receipt_id": ingress_receipt.receipt_id,
            "receipt_sha256": ingress_receipt.receipt_sha256,
            "raw_source_sha256": ingress_receipt.raw_source_sha256,
            "protected_user_id": ingress_receipt.protected_user_id,
            "source_unit_keys": tuple(
                value.source_unit_key for value in ingress_receipt.source_units
            ),
        }
        stable_reference_keys = tuple(
            value["binding_key"] for value in stable_reference_bindings
        )
        protected_user_claim_manifest = (
            evidence_registry.protected_user_claim_manifest()
        )
        planner_authority_packet = build_continuous_planner_turn_packet(
            world_id=request.world_id,
            branch_id=request.branch_id,
            session_id=request.session_id,
            request_id=request.request_id,
            scene_id=request.scene_id,
            turn_id=request.turn_id,
            context_mode=request.context_mode.value,
            current_user_message=request.user_message,
            request_local_evidence_bindings=evidence_registry.prompt_manifest(),
            current_source_binding_key=current_source_binding.binding_key,
            mechanical_connective_binding_key=mechanical_binding.binding_key,
            protected_user_source_claims=protected_user_claim_manifest,
            ingress_source_units=tuple(
                to_primitive(value) for value in source_units
            ),
            ingress_custody=compact_ingress_custody,
            character_summary_bindings=tuple(summary_bindings),
            compact_accepted_head_receipt=compact_accepted_head,
            stable_accepted_reference_keys=stable_reference_keys,
            lean_continuation_authority=lean_continuation_authority,
            projection_assisted_trigger=request.projection_assisted_trigger,
            projection_reference_keys=request.projection_reference_keys,
            projection_facts=projection_assisted_payloads,
            scene_change_envelope_sha256=(
                scene_change_envelope.context_sha256
                if scene_change_envelope is not None
                else None
            ),
        )
        planner_authority_packet_payload = planner_authority_packet.to_payload()
        debug.write_json(
            "planner_authority_packet.json",
            planner_authority_packet_payload,
        )
        initialization_packet = ContinuousSessionInitializationPacketV1.from_receipt(
            self.planner_session.initialization_receipt
        )
        planner_prompt, planner_usage = build_planner_turn_prompt(
            current_packet=planner_authority_packet,
            character_summaries=planner_summaries,
            scene_change_context=scene_change_envelope,
        )
        debug.write_text("planner_raw_prompt.txt", planner_prompt)
        debug.write_json(
            "planner_prompt_components.json",
            [to_primitive(value) for value in planner_usage],
        )
        started = time.perf_counter_ns()
        try:
            planner_result = self.planner.plan(
                planner_prompt,
                protected_user_id=ingress_receipt.protected_user_id,
                protected_user_source_claim_keys=tuple(
                    value["claim_key"]
                    for value in protected_user_claim_manifest
                ),
            )
        except BaseException as exc:
            debug.record_failure("planner", exc)
            raise
        planner_elapsed = time.perf_counter_ns() - started
        planner_sequence = planner_result.value
        evidence_registry.import_provider_debug(
            getattr(planner_result, "world_tool_debug", None)
        )
        if (
            planner_sequence.world_id != request.world_id
            or planner_sequence.branch_id != request.branch_id
            or planner_sequence.scene_id != request.scene_id
        ):
            raise StateConflictError("Planner changed turn scope")
        reference_only_character_ids = _planner_reference_only_character_ids(
            planner_sequence
        )
        evidence_registry.validate_sequence(
            planner_sequence,
            branch_root=branch_root,
        )
        validator_binding_manifest = (
            evidence_registry.validator_binding_manifest(planner_sequence)
        )
        validator_cited_accepted_evidence = (
            evidence_registry.validator_cited_accepted_evidence_closure(
                planner_sequence
            )
        )
        validator_cited_accepted_evidence_payload = tuple(
            to_primitive(value)
            for value in validator_cited_accepted_evidence
        )
        self.planner_session.record_planner_provisional(
            request.turn_id, planner_sequence.sequence_sha256
        )
        summary_delivery_receipts = (
            self.planner_session.record_character_summary_deliveries(
                planner_summaries,
                summary_delivery_reasons,
                planner_prompt_sha256=text_sha256(planner_prompt),
            )
        )
        debug.write_json("planner_output.json", to_primitive(planner_sequence))
        debug.write_json("planner_tools.json", _provider_debug(planner_result))
        for summary in request.character_summaries:
            validate_character_summary_envelope(
                branch_root=branch_root,
                envelope=summary,
            )
        realization_boundary = WriterRealizationBoundaryV1.default()
        frozen_writer_authority_sha256 = (
            continuous_writer_authority_package_sha256(
                current_user_source=request.user_message,
                ingress_source_units=tuple(
                    to_primitive(value) for value in source_units
                ),
                planner_sequence=planner_sequence,
                character_summaries=request.character_summaries,
                protected_user_claim_manifest=protected_user_claim_manifest,
                accepted_session_projections=(),
                realization_boundary=realization_boundary,
            )
        )
        attempt_provider_calls = 0
        while True:
            writer_attempt_number = _writer_attempt_number_for_recall(
                writer_recall_directive,
                frozen_authority_package_sha256=frozen_writer_authority_sha256,
            )
            attempt_debug = debug.for_writer_attempt(writer_attempt_number)
            if attempt_debug is not debug:
                attempt_debug.write_json(
                    "planner_authority_packet.json",
                    planner_authority_packet_payload,
                )
                attempt_debug.write_json(
                    "planner_prompt_components.json",
                    [to_primitive(value) for value in planner_usage],
                )
                attempt_debug.write_text("planner_raw_prompt.txt", planner_prompt)
                attempt_debug.write_json(
                    "planner_output.json",
                    to_primitive(planner_sequence),
                )
                attempt_debug.write_json(
                    "planner_tools.json",
                    _provider_debug(planner_result),
                )
            if writer_recall_directive is not None:
                attempt_debug.write_json(
                    "writer_recall_input.json",
                    to_primitive(writer_recall_directive),
                )
            composer_prompt, composer_usage = build_continuous_composer_prompt(
                current_user_source=request.user_message,
                ingress_source_units=tuple(
                    to_primitive(value) for value in source_units
                ),
                planner_sequence=planner_sequence,
                character_summaries=request.character_summaries,
                protected_user_claim_manifest=protected_user_claim_manifest,
                accepted_session_projections=(),
                realization_boundary=realization_boundary,
                writer_recall_directive=writer_recall_directive,
            )
            attempt_debug.write_json(
                "deepseek_request.json",
                {"prompt": composer_prompt},
            )
            started = time.perf_counter_ns()
            try:
                composer_result = self.composer.compose(composer_prompt)
            except BaseException as exc:
                attempt_debug.record_failure("composer", exc)
                raise
            composer_elapsed = time.perf_counter_ns() - started
            attempt_provider_calls += int(
                getattr(composer_result.provider_receipt, "external_provider_calls", 0)
            )
            story_text = composer_result.value.story_text
            candidate_id = "candidate:" + canonical_sha256(
                {
                    "world_id": request.world_id,
                    "branch_id": request.branch_id,
                    "turn_id": request.turn_id,
                    "request_id": request.request_id,
                    "idempotency_key_sha256": request.idempotency_key_sha256,
                    "ingress_receipt_sha256": request.ingress_receipt_sha256,
                    "planner_sequence_sha256": planner_sequence.sequence_sha256,
                    "writer_story_text_sha256": text_sha256(story_text),
                }
            )
            writer_envelope = WriterMechanicalEnvelopeV1.from_story_text(
                candidate_id=candidate_id,
                story_text=story_text,
            )
            writer_envelope.validate_story_text(story_text)
            composer_payload = {
                "schema_version": getattr(composer_result.value, "schema_version", None),
                "story_text": story_text,
            }
            attempt_debug.write_json("deepseek_output.json", composer_payload)
            attempt_debug.write_json(
                "writer_mechanical_envelope.json",
                to_primitive(writer_envelope),
            )
            package_id = _validator_package_id(
                task_mode=ValidatorTaskMode.FINALIZE_TURN,
                world_id=request.world_id,
                branch_id=request.branch_id,
                accepted_turn_id=request.turn_id,
                accepted_scene_turn_ids=(),
                candidate_id=candidate_id,
            )
            validator_prompt, validator_usage = build_validator_prompt(
                task_mode=ValidatorTaskMode.FINALIZE_TURN,
                package_id=package_id,
                world_id=request.world_id,
                branch_id=request.branch_id,
                candidate_id=candidate_id,
                current_user_source=request.user_message,
                planner_sequence=planner_sequence,
                writer_story_text=story_text,
                writer_mechanical_envelope=to_primitive(writer_envelope),
                accepted_turn_id=request.turn_id,
                world_file_manifest=self.world.active_manifest(
                    request.world_id, request.branch_id
                ),
                evidence_binding_manifest=validator_binding_manifest,
                protected_user_claim_manifest=protected_user_claim_manifest,
                ingress_source_units=tuple(
                    to_primitive(value) for value in source_units
                ),
                cited_accepted_evidence=(
                    validator_cited_accepted_evidence_payload
                ),
                realization_boundary=realization_boundary,
                contract_profile=self.validator_contract_profile,
            )
            attempt_debug.write_json(
                "validator_request.json",
                {"prompt": validator_prompt},
            )
            started = time.perf_counter_ns()
            try:
                validator_result = self.validator.validate(
                    validator_prompt,
                    writer_story_text=story_text,
                    expected_package_id=package_id,
                    expected_world_id=request.world_id,
                    expected_branch_id=request.branch_id,
                )
            except BaseException as exc:
                attempt_debug.record_failure("validator", exc)
                raise
            validator_elapsed = time.perf_counter_ns() - started
            attempt_provider_calls += int(
                getattr(validator_result.provider_receipt, "external_provider_calls", 0)
            )
            semantic_result = validator_result.value
            package = getattr(semantic_result, "finalization_package", None)
            story_segments = tuple(
                getattr(semantic_result, "story_segments", ())
            )
            diagnostic_story_segments = tuple(
                getattr(semantic_result, "diagnostic_story_segments", ())
            )
            diagnostic_adjudications = tuple(
                getattr(
                    semantic_result,
                    "diagnostic_protected_semantic_adjudications",
                    (),
                )
            )
            presentation_segments = tuple(
                getattr(
                    semantic_result,
                    "presentation_realization_segments",
                    (),
                )
            )
            presentation_adjudications = tuple(
                getattr(
                    semantic_result,
                    "presentation_protected_semantic_adjudications",
                    (),
                )
            )
            source_grounded_public_state_receipts = tuple(
                getattr(
                    semantic_result,
                    "source_grounded_public_state_receipts",
                    (),
                )
            )
            attempt_debug.write_json(
                "validator_output.json",
                to_primitive(semantic_result),
            )
            attempt_debug.write_json(
                "validator_tools.json",
                _provider_debug(validator_result),
            )
            semantic_status = getattr(semantic_result, "semantic_status", None)
            if semantic_status not in {
                ValidatorSemanticStatus.ACCEPTED,
                ValidatorSemanticStatus.CONCERN,
            } or package is None:
                if package is not None or story_segments or getattr(
                    semantic_result, "protected_semantic_adjudications", ()
                ):
                    raise StateConflictError(
                        "rejected Validator result carried canonical finalization authority"
                    )
                evidence_registry.validate_validator_diagnostics(
                    story_text=story_text,
                    diagnostic_story_segments=diagnostic_story_segments,
                    diagnostic_protected_semantic_adjudications=(
                        diagnostic_adjudications
                    ),
                    allowed_character_ids=planner_sequence.selected_character_ids,
                    reference_only_character_ids=reference_only_character_ids,
                    require_gap_free=(
                        self.validator_contract_profile != "compact_v1"
                    ),
                )
                reason_codes = tuple(
                    getattr(semantic_result, "reason_codes", ())
                )
                recall_builder = getattr(
                    semantic_result,
                    "build_writer_recall_directive",
                    None,
                )
                if callable(recall_builder) and getattr(
                    semantic_result,
                    "writer_recall_offending_spans",
                    (),
                ) and writer_attempt_number < 3:
                    recall_directive = recall_builder(
                        rejected_candidate_id=candidate_id,
                        rejected_story_text=story_text,
                        frozen_authority_package_sha256=(
                            frozen_writer_authority_sha256
                        ),
                        source_attempt_number=writer_attempt_number,
                    )
                    attempt_debug.write_json(
                        "writer_recall_directive.json",
                        to_primitive(recall_directive),
                    )
                    error = PermissionError(
                        "Semantic Validator rejected immutable Writer attempt "
                        f"{writer_attempt_number}; bounded recall remains eligible"
                    )
                    attempt_debug.record_failure(
                        "validator_semantic_verdict",
                        error,
                    )
                    writer_recall_directive = recall_directive
                    continue
                error = PermissionError(
                    "Semantic Validator rejected or could not resolve the immutable "
                    f"Writer candidate: {','.join(reason_codes) or 'missing_typed_reason'}"
                )
                attempt_debug.record_failure("validator_semantic_verdict", error)
                raise error
            if diagnostic_story_segments or diagnostic_adjudications:
                raise StateConflictError(
                    "accepted Validator result carried rejected diagnostic evidence"
                )
            if any(
                not isinstance(value, PresentationRealizationSegmentV1)
                for value in presentation_segments
            ):
                raise StateConflictError(
                    "Validator returned the wrong presentation realization contract"
                )
            protected_realizations = (
                evidence_registry.validate_validator_realization_boundary(
                    story_text=story_text,
                    story_segments=story_segments,
                    presentation_segments=presentation_segments,
                    package=package,
                    presentation_adjudications=presentation_adjudications,
                    allowed_character_ids=planner_sequence.selected_character_ids,
                    reference_only_character_ids=reference_only_character_ids,
                    source_grounded_public_state_receipts=(
                        source_grounded_public_state_receipts
                    ),
                    require_gap_free=(
                        self.validator_contract_profile != "compact_v1"
                    ),
                )
            )
            attempt_debug.write_json(
                "presentation_realization_segments.json",
                to_primitive(presentation_segments),
            )
            attempt_debug.write_json(
                "source_grounded_public_state_receipts.json",
                to_primitive(source_grounded_public_state_receipts),
            )
            if (
                package.world_id != request.world_id
                or package.branch_id != request.branch_id
                or package.complete_final_sequence is None
                or package.complete_final_sequence.accepted_turn_id != request.turn_id
                or package.event_record is None
                or package.event_record.scene_id != request.scene_id
            ):
                raise StateConflictError("Validator changed turn scope")
            evidence_registry.validate_traceability(
                planner_sequence,
                package,
                branch_root=branch_root,
            )
            reader_prompt, reader_usage = build_reader_prompt(
                world_id=request.world_id,
                branch_id=request.branch_id,
                turn_id=request.turn_id,
                candidate_id=candidate_id,
                writer_story_text=story_text,
                writer_mechanical_envelope=to_primitive(writer_envelope),
                planner_sequence=planner_sequence,
                bounded_accepted_context=validator_cited_accepted_evidence_payload,
                hard_constraints=(
                    "Do not invent protected-user action, dialogue, state, or choice.",
                    "Do not introduce inactive characters as scene participants.",
                    "Preserve the Planner stopping boundary.",
                    "Treat presentation-only realization as visible but noncanonical.",
                ),
            )
            attempt_debug.write_json(
                "reader_request.json",
                {"prompt": reader_prompt},
            )
            started = time.perf_counter_ns()
            try:
                reader_result = self.reader.review(
                    reader_prompt,
                    writer_story_text=story_text,
                )
            except BaseException as exc:
                attempt_debug.record_failure("reader", exc)
                raise
            reader_elapsed = time.perf_counter_ns() - started
            attempt_provider_calls += int(
                getattr(reader_result.provider_receipt, "external_provider_calls", 0)
            )
            reader_verdict = reader_result.value
            if not isinstance(reader_verdict, ReaderVerdictV1):
                raise StateConflictError("Reader returned the wrong result contract")
            reader_verdict.validate_story_text(story_text)
            if (
                reader_verdict.world_id != request.world_id
                or reader_verdict.branch_id != request.branch_id
                or reader_verdict.turn_id != request.turn_id
                or reader_verdict.candidate_id != candidate_id
            ):
                raise StateConflictError("Reader changed candidate scope")
            reader_session_sha256 = getattr(
                reader_result,
                "physical_session_sha256",
                None,
            )
            planner_session_sha256 = (
                self.planner_session.ensure_session().provider_thread_id_sha256
            )
            validator_session_sha256 = (
                self.validator_session.ensure_session().provider_thread_id_sha256
            )
            if (
                not isinstance(reader_session_sha256, str)
                or not re_is_sha256(reader_session_sha256)
                or reader_session_sha256
                in {
                    planner_session_sha256,
                    validator_session_sha256,
                    *self._reader_session_sha256s,
                }
            ):
                raise StateConflictError(
                    "Planner, Validator, and candidate Reader require distinct physical sessions"
                )
            self._reader_session_sha256s.add(reader_session_sha256)
            attempt_debug.write_json(
                "reader_output.json",
                to_primitive(reader_verdict),
            )
            attempt_debug.write_json(
                "reader_tools.json",
                _provider_debug(reader_result),
            )
            if reader_verdict.verdict is not ReaderVerdictStatus.ACCEPTED:
                if (
                    reader_verdict.verdict is ReaderVerdictStatus.REJECTED
                    and writer_attempt_number < 3
                ):
                    reader_recall_directive = (
                        reader_verdict.build_writer_recall_directive(
                            writer_story_text=story_text,
                            frozen_authority_package_sha256=(
                                frozen_writer_authority_sha256
                            ),
                            source_attempt_number=writer_attempt_number,
                        )
                    )
                    attempt_debug.write_json(
                        "writer_recall_directive.json",
                        {
                            "source": "severe_reader_quality_failure",
                            "directive": to_primitive(reader_recall_directive),
                        },
                    )
                    error = PermissionError(
                        "Reader rejected immutable Writer attempt "
                        f"{writer_attempt_number}; bounded recall remains eligible"
                    )
                    attempt_debug.record_failure("reader_verdict", error)
                    writer_recall_directive = reader_recall_directive
                    continue
                error = PermissionError(
                    "Reader rejected or could not resolve the immutable candidate"
                )
                attempt_debug.record_failure("reader_verdict", error)
                raise error
            debug = attempt_debug
            break
        self.validator_session.record_validator_candidate(
            request.turn_id, package.package_sha256
        )
        before_files = _snapshot_files(candidate_view.root / "ACTIVE_VIEW")
        provider_calls = (
            prior_provider_calls
            + int(
                getattr(planner_result.provider_receipt, "external_provider_calls", 0)
            )
            + attempt_provider_calls
        )
        candidate = ContinuousTurnCandidateV1(
            request=request,
            candidate_view=candidate_view,
            planner_sequence=planner_sequence,
            deepseek_story_text=story_text,
            writer_mechanical_envelope=writer_envelope,
            validator_story_segments=story_segments,
            validator_package=package,
            reader_verdict=reader_verdict,
            planner_prompt_sha256=text_sha256(planner_prompt),
            composer_prompt_sha256=text_sha256(composer_prompt),
            validator_prompt_sha256=text_sha256(validator_prompt),
            reader_prompt_sha256=text_sha256(reader_prompt),
            debug_root=attempt_debug.root,
            provider_calls=provider_calls,
            evidence_registry_sha256=evidence_registry.registry_sha256,
            protected_user_claim_ledger_sha256=canonical_sha256(
                protected_user_claim_manifest
            ),
            protected_user_realization_ledger_sha256=canonical_sha256(
                to_primitive(protected_realizations)
            ),
            story_segment_ledger_sha256=canonical_sha256(
                to_primitive(story_segments)
            ),
            protected_semantic_adjudication_ledger_sha256=canonical_sha256(
                to_primitive(package.protected_semantic_adjudications)
            ),
            accepted_session_projection_ledger_sha256=canonical_sha256(
                {
                    "compact_accepted_head_receipt_sha256": (
                        compact_accepted_head.receipt_sha256
                        if compact_accepted_head is not None
                        else None
                    ),
                    "stable_reference_binding_keys": tuple(
                        value["binding_key"] for value in stable_reference_bindings
                    ),
                    "projection_assisted_reference_keys": (
                        request.projection_reference_keys
                    ),
                }
            ),
            validator_cited_accepted_evidence_sha256=canonical_sha256(
                validator_cited_accepted_evidence_payload
            ),
            reader_session_sha256=reader_session_sha256,
            planner_authority_packet_schema_version=(
                planner_authority_packet.schema_version
            ),
            planner_authority_packet_kind=(
                planner_authority_packet.packet_kind.value
            ),
            planner_authority_packet_sha256=(
                planner_authority_packet.packet_sha256
            ),
            planner_authority_packet_bytes=planner_authority_packet.packet_bytes,
            session_initialization_packet_kind=(
                initialization_packet.packet_kind.value
            ),
            session_initialization_packet_sha256=(
                initialization_packet.packet_sha256
            ),
            session_initialization_packet_bytes=(
                initialization_packet.packet_bytes
            ),
            context_mode=request.context_mode,
            compact_accepted_head_receipt_sha256=(
                compact_accepted_head.receipt_sha256
                if compact_accepted_head is not None
                else None
            ),
            character_summary_delivery_receipt_sha256s=tuple(
                value.receipt_sha256 for value in summary_delivery_receipts
            ),
        )
        try:
            self.world.record_candidate_package(
                request.world_id,
                request.branch_id,
                candidate_view,
                package,
                candidate_sha256=candidate.candidate_sha256,
                authority_context_sha256=candidate.authority_context_sha256,
            )
        except BaseException as exc:
            debug.record_failure("candidate_package", exc)
            raise
        debug_payloads = {
            "planner_authority_packet.json": planner_authority_packet_payload,
            "planner_prompt_components.json": [to_primitive(value) for value in planner_usage],
            "planner_output.json": to_primitive(planner_sequence),
            "planner_tools.json": _provider_debug(planner_result),
            "deepseek_request.json": {"prompt": composer_prompt},
            "deepseek_output.json": composer_payload,
            "writer_mechanical_envelope.json": to_primitive(writer_envelope),
            "validator_request.json": {"prompt": validator_prompt},
            "validator_cited_accepted_evidence.json": (
                validator_cited_accepted_evidence_payload
            ),
            "validator_output.json": to_primitive(semantic_result),
            "presentation_realization_segments.json": to_primitive(
                presentation_segments
            ),
            "validator_tools.json": _provider_debug(validator_result),
            "reader_request.json": {"prompt": reader_prompt},
            "reader_output.json": to_primitive(reader_verdict),
            "reader_tools.json": _provider_debug(reader_result),
            "candidate_before.json": before_files,
            "candidate_after.json": before_files,
            "exact_diff.json": [],
            "edit_package.json": to_primitive(package.world_edit_operations),
            "new_field_log.json": to_primitive(package.created_field_log),
            "creator_action.json": {"state": "pending"},
            "promotion_or_discard_receipt.json": {"state": "pending"},
            "provider_routes.json": {
                "planner": _provider_debug(planner_result),
                "composer": _provider_debug(composer_result),
                "validator": _provider_debug(validator_result),
                "reader": _provider_debug(reader_result),
            },
            "usage.json": {
                "planner_context_mode": request.context_mode.value,
                "planner_authority_packet": {
                    "schema_version": planner_authority_packet.schema_version,
                    "packet_kind": planner_authority_packet.packet_kind.value,
                    "sha256": planner_authority_packet.packet_sha256,
                    "byte_count": planner_authority_packet.packet_bytes,
                },
                "planner_base_stable_instructions": to_primitive(
                    planner_base_instruction_usage()
                ),
                "actual_submitted_prompts": {
                    "planner": to_primitive(
                        prompt_text_usage(
                            "actual_submitted_planner_prompt",
                            planner_prompt,
                        )
                    ),
                    "composer": to_primitive(
                        prompt_text_usage(
                            "actual_submitted_composer_prompt",
                            composer_prompt,
                        )
                    ),
                    "validator": to_primitive(
                        prompt_text_usage(
                            "actual_submitted_validator_prompt",
                            validator_prompt,
                        )
                    ),
                    "reader": to_primitive(
                        prompt_text_usage(
                            "actual_submitted_reader_prompt",
                            reader_prompt,
                        )
                    ),
                },
                "validator_pre_adapter_prompt": to_primitive(
                    prompt_text_usage(
                        "validator_pre_adapter_prompt",
                        validator_prompt,
                    )
                ),
                "planner_prompt_components": [to_primitive(value) for value in planner_usage],
                "composer_prompt_components": [to_primitive(value) for value in composer_usage],
                "validator_prompt_components": [to_primitive(value) for value in validator_usage],
                "reader_prompt_components": [to_primitive(value) for value in reader_usage],
                "compact_accepted_head_receipt_sha256": (
                    compact_accepted_head.receipt_sha256
                    if compact_accepted_head is not None
                    else None
                ),
                "projection_assisted_bytes": len(
                    json.dumps(
                        projection_assisted_payloads,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                ),
                "session_initialization_packet": {
                    "payload": initialization_packet.to_payload(),
                    "sha256": initialization_packet.packet_sha256,
                    "byte_count": initialization_packet.packet_bytes,
                    "estimated_tokens": (
                        initialization_packet.packet_bytes + 3
                    )
                    // 4,
                },
                "character_summary_delivery_receipts": tuple(
                    to_primitive(value) for value in summary_delivery_receipts
                ),
                "provider_operation_telemetry": {
                    "planner": to_primitive(
                        getattr(planner_result, "operation_telemetry", None)
                    ),
                    "composer": to_primitive(
                        getattr(composer_result, "operation_telemetry", None)
                    ),
                    "validator": to_primitive(
                        getattr(validator_result, "operation_telemetry", None)
                    ),
                    "reader": to_primitive(
                        getattr(reader_result, "operation_telemetry", None)
                    ),
                },
                "world_tool_activity": {
                    "planner": getattr(planner_result, "world_tool_debug", None),
                    "validator": getattr(validator_result, "world_tool_debug", None),
                },
                "rich_sequence": {
                    "beat_count": len(planner_sequence.beats),
                    "validation_outcome": "passed",
                },
            },
            "stage_timings.json": {
                "planner_ns": planner_elapsed,
                "composer_ns": composer_elapsed,
                "validator_ns": validator_elapsed,
                "reader_ns": reader_elapsed,
            },
            "errors.json": [],
            "replay_input.json": {
                "request": to_primitive(request),
                "planner_authority_packet": planner_authority_packet_payload,
                "planner_authority_packet_sha256": (
                    planner_authority_packet.packet_sha256
                ),
                "session_initialization_packet": (
                    initialization_packet.to_payload()
                ),
                "session_initialization_packet_sha256": (
                    initialization_packet.packet_sha256
                ),
                "planner_result": to_primitive(planner_sequence),
                "composer_result": composer_payload,
                "writer_mechanical_envelope": to_primitive(writer_envelope),
                "evidence_registry_sha256": evidence_registry.registry_sha256,
                "protected_user_claim_manifest": protected_user_claim_manifest,
                "ingress_source_units": tuple(
                    to_primitive(value) for value in source_units
                ),
                "ingress_receipt": to_primitive(ingress_receipt),
                "story_segments": to_primitive(story_segments),
                "reader_result": to_primitive(reader_verdict),
                "authority_context_sha256": candidate.authority_context_sha256,
                "candidate_sha256": candidate.candidate_sha256,
                "validator_result": to_primitive(package),
                "validator_cited_accepted_evidence": (
                    validator_cited_accepted_evidence_payload
                ),
            },
        }
        debug.write_text("planner_raw_prompt.txt", planner_prompt)
        for name, payload in debug_payloads.items():
            debug.write_json(name, payload)
        self._candidates[request.turn_id] = candidate
        return candidate

    def _bind_stable_accepted_context(
        self,
        *,
        request: ContinuousTurnRequestV1,
        registry: RequestEvidenceBindingRegistry,
        branch_root: Path,
    ) -> tuple[
        CompactAcceptedHeadReceiptV1 | None,
        tuple[dict[str, Any], ...],
        tuple[dict[str, Any], ...],
        LeanContinuationAuthorityV1 | None,
    ]:
        """Resolve stable keys and compact, value-bounded continuation authority."""

        snapshot = self.planner_session.snapshot()
        accepted_ids = snapshot.accepted_turn_ids
        if not accepted_ids:
            if request.context_mode is PlannerContextMode.PROJECTION_ASSISTED:
                raise StateConflictError(
                    "projection-assisted mode has no accepted context"
                )
            return None, (), (), None
        accepted_turn_id = accepted_ids[-1]
        initialization = snapshot.initialization_receipt
        reconstructed_head = (
            initialization is not None
            and initialization.context_mode is PlannerContextMode.RECONSTRUCTION
            and accepted_turn_id in initialization.accepted_tail_turn_ids
        )
        forked_head = (
            initialization is not None
            and initialization.context_mode is PlannerContextMode.LEAN_CONTINUOUS
            and initialization.branch_receipt_sha256 is not None
            and accepted_turn_id in initialization.accepted_tail_turn_ids
        )
        journal: dict[str, Any] | None = None
        if not reconstructed_head and not forked_head:
            journal = self.world.acceptance_synchronization_record(
                request.world_id, request.branch_id, accepted_turn_id
            )
            if journal.get("model_injection_state") != "synchronized":
                raise StateConflictError(
                    "stable accepted context is not synchronized"
                )
        handle = self.planner_session.ensure_session()
        reference_store = StableAcceptedContextReferenceStore(branch_root)
        receipt, references = reference_store.load(
            accepted_turn_id,
            provider_thread_sha256=handle.provider_thread_id_sha256,
        )
        accepted_envelope = self.world.accepted_final_envelope(
            request.world_id, request.branch_id, accepted_turn_id
        )
        expected_ancestry = canonical_sha256(
            {
                "world_id": request.world_id,
                "branch_id": request.branch_id,
                "accepted_turn_ids": accepted_ids,
                "accepted_head_envelope_sha256": receipt.accepted_envelope_sha256,
            }
        )
        fork_transfer_sha256 = None
        if forked_head:
            transfer_events = tuple(
                value.payload_sha256
                for value in snapshot.context_events
                if value.event_type == "branch_reference_rebinding"
                and value.turn_or_scene_id == accepted_turn_id
            )
            if len(transfer_events) != 1:
                raise StateConflictError(
                    "forked accepted context lacks one reference transfer"
                )
            fork_transfer_sha256 = transfer_events[0]
        expected_injection_receipt = (
            initialization.receipt_sha256
            if reconstructed_head
            else (
                fork_transfer_sha256
                if forked_head
                else journal.get("injection_operation_receipt_sha256")  # type: ignore[union-attr]
            )
        )
        expected_snapshot_sha256 = (
            snapshot.snapshot_sha256
            if reconstructed_head or forked_head
            else journal.get("planner_session_snapshot_sha256")  # type: ignore[union-attr]
        )
        expected_synchronization_receipt = (
            reconstruction_reference_synchronization_sha256(
                initialization_receipt_sha256=initialization.receipt_sha256,
                accepted_turn_id=accepted_turn_id,
                accepted_envelope_sha256=receipt.accepted_envelope_sha256,
            )
            if reconstructed_head and initialization is not None
            else (
                provider_fork_reference_synchronization_sha256(
                    transfer_receipt_sha256=str(expected_injection_receipt),
                    accepted_turn_id=accepted_turn_id,
                    accepted_envelope_sha256=receipt.accepted_envelope_sha256,
                )
                if forked_head
                else journal.get("synchronization_receipt_sha256")  # type: ignore[union-attr]
            )
        )
        checks = {
            "world": receipt.world_id == request.world_id,
            "branch": receipt.branch_id == request.branch_id,
            "accepted_turn": receipt.accepted_turn_id == accepted_turn_id,
            "provider_thread": (
                receipt.provider_thread_sha256
                == handle.provider_thread_id_sha256
            ),
            "provider_session": (
                receipt.planner_session_id_sha256
                == text_sha256(handle.provider_session_id)
            ),
            "ancestry": receipt.accepted_ancestry_sha256 == expected_ancestry,
            "envelope": (
                receipt.accepted_envelope_sha256
                == accepted_envelope.envelope_sha256
            ),
            "pair": (
                reconstructed_head
                or forked_head
                or receipt.accepted_pair_sha256
                == journal.get("accepted_pair_sha256")  # type: ignore[union-attr]
            ),
            "event": (
                reconstructed_head
                or forked_head
                or receipt.accepted_event_sha256
                == journal.get("accepted_event_sha256")  # type: ignore[union-attr]
            ),
            "acceptance": (
                receipt.acceptance_receipt_sha256
                == accepted_envelope.acceptance_receipt_sha256
            ),
            "injection": (
                receipt.injection_receipt_sha256
                == expected_injection_receipt
            ),
            "snapshot": (
                receipt.session_snapshot_sha256
                == expected_snapshot_sha256
            ),
            "synchronization": (
                receipt.synchronization_receipt_sha256
                == expected_synchronization_receipt
            ),
        }
        if not all(checks.values()):
            failed = ",".join(key for key, passed in checks.items() if not passed)
            raise StateConflictError(
                "stable accepted-context receipt is stale, foreign, or unsynchronized: "
                + failed
            )
        if not reconstructed_head and not forked_head:
            reference_path = reference_store.path_for(
                accepted_turn_id,
                handle.provider_thread_id_sha256,
            )
            if (
                journal.get("stable_reference_state") != "persisted"  # type: ignore[union-attr]
                or journal.get("compact_accepted_head_receipt_sha256")  # type: ignore[union-attr]
                != receipt.receipt_sha256
                or journal.get("stable_reference_set_sha256")  # type: ignore[union-attr]
                != text_sha256(reference_path.read_text(encoding="utf-8"))
            ):
                raise StateConflictError(
                    "stable accepted-reference artifact custody changed"
                )
        validate_stable_accepted_context_reference_facts(
            envelope=accepted_envelope,
            references=references,
        )
        reference_map = {value.reference_key: value for value in references}
        selected = set(request.projection_reference_keys)
        if selected - set(reference_map):
            raise StateConflictError(
                "projection-assisted mode requested an unknown stable key"
            )
        bindings: list[dict[str, Any]] = []
        projection_payloads: list[dict[str, Any]] = []
        for reference in references:
            binding = registry.allocate_stable_accepted_context_reference(
                reference,
                current_provider_thread_sha256=handle.provider_thread_id_sha256,
                current_accepted_ancestry_sha256=expected_ancestry,
            )
            bindings.append(
                {
                    "binding_key": binding.binding_key,
                    "reference_sha256": reference.reference_sha256,
                    "visibility": reference.visibility.value,
                    "knowledge_owner_id": reference.knowledge_owner_id,
                }
            )
            if reference.reference_key in selected:
                projection_payloads.append(
                    {
                        "reference_key": reference.reference_key,
                        "accepted_turn_id": reference.accepted_turn_id,
                        "source_item_key": reference.source_item_key,
                        "field_name": reference.field_name,
                        "field_value": reference.field_value,
                        "visibility": reference.visibility.value,
                        "knowledge_owner_id": reference.knowledge_owner_id,
                        "roles": to_primitive(reference.roles),
                    }
                )
        if request.context_mode is PlannerContextMode.LEAN_CONTINUOUS and (
            projection_payloads or selected
        ):
            raise StateConflictError(
                "lean_continuous silently acquired projection payload"
            )
        if request.context_mode is PlannerContextMode.PROJECTION_ASSISTED and (
            len(projection_payloads) != len(selected)
        ):
            raise StateConflictError(
                "projection-assisted selection is incomplete"
            )
        lean_authority = build_accepted_lean_continuation_authority(
            receipt=receipt,
            references=references,
            final_sequence=accepted_envelope.complete_final_sequence,
        )
        return (
            receipt,
            tuple(bindings),
            tuple(projection_payloads),
            lean_authority,
        )

    def apply_creator_action(
        self,
        turn_id: str,
        action: CreatorReviewAction,
    ) -> WorldPromotionReceiptV1:
        candidate = self._candidates[turn_id]
        package = candidate.validator_package
        acceptance_snapshot_store: ContinuousSessionSnapshotStore | None = None
        pair = None
        if package.complete_final_sequence is not None:
            pair = AcceptedTurnPairV1(
                accepted_turn_id=turn_id,
                user_message=candidate.request.user_message,
                complete_final_sequence=package.complete_final_sequence,
            )
        if action in {CreatorReviewAction.ACCEPT, CreatorReviewAction.FALSE_POSITIVE}:
            acceptance_snapshot_store = ContinuousSessionSnapshotStore(
                self.world.branch_root(
                    candidate.request.world_id, candidate.request.branch_id
                ),
                failpoint=self._acceptance_failpoint,
            )
            acceptance_snapshot_store.preflight_acceptance_capacity(
                accepted_turn_id=turn_id
            )
        self.planner_session.ensure_session()
        promotion_started = time.perf_counter_ns()
        try:
            receipt = self.world.apply_creator_action(
                world_id=candidate.request.world_id,
                branch_id=candidate.request.branch_id,
                turn_id=turn_id,
                action=action,
                package=package,
                candidate_sha256=candidate.candidate_sha256,
                authority_context_sha256=candidate.authority_context_sha256,
                accepted_pair=pair if action in {CreatorReviewAction.ACCEPT, CreatorReviewAction.FALSE_POSITIVE} else None,
            )
        except BaseException as exc:
            ContinuousDebugRecorder(
                self.world.branch_root(
                    candidate.request.world_id, candidate.request.branch_id
                ),
                candidate.request.scene_id,
                turn_id,
            ).record_failure("creator_promotion", exc)
            raise
        promotion_elapsed = time.perf_counter_ns() - promotion_started
        debug = ContinuousDebugRecorder(
            self.world.branch_root(candidate.request.world_id, candidate.request.branch_id),
            candidate.request.scene_id,
            turn_id,
        )
        debug.write_json("creator_action.json", {"action": action.value})
        debug.write_json("promotion_or_discard_receipt.json", to_primitive(receipt))
        before = json.loads(
            (debug.root / "candidate_before.json").read_text(encoding="utf-8")
        )
        after = _snapshot_files(candidate.candidate_view.root / "ACTIVE_VIEW")
        debug.write_json("candidate_after.json", after)
        debug.write_json(
            "exact_diff.json",
            [
                {
                    "path": path,
                    "before_sha256": before.get(path),
                    "after_sha256": after.get(path),
                }
                for path in sorted(set(before).union(after))
                if before.get(path) != after.get(path)
            ],
        )
        timings_path = debug.root / "stage_timings.json"
        timings = json.loads(timings_path.read_text(encoding="utf-8"))
        if not isinstance(timings, dict) or "state" in timings:
            timings = {}
        timings["python_creator_promotion_ns"] = promotion_elapsed
        debug.write_json("stage_timings.json", timings)
        if receipt.accepted:
            assert package.complete_final_sequence is not None
            envelope = AcceptedFinalSequenceEnvelopeV1(
                schema_version=AcceptedFinalSequenceEnvelopeV1.SCHEMA_VERSION,
                accepted_turn_id=turn_id,
                user_message=candidate.request.user_message,
                complete_final_sequence=package.complete_final_sequence,
                acceptance_receipt_sha256=receipt.receipt_sha256,
            )
            accepted_facts = tuple(
                fact
                for item in package.complete_final_sequence.items
                for fact in project_final_sequence_facts(item)
            )
            stable_reference_descriptors = stable_reference_descriptors_for_facts(
                world_id=candidate.request.world_id,
                branch_id=candidate.request.branch_id,
                accepted_turn_id=turn_id,
                facts=accepted_facts,
            )
            handle = self.planner_session.ensure_session()
            self.planner_session.append_accepted_final_sequence(envelope)
            self.world.mark_acceptance_planner_ledger_appended(
                candidate.request.world_id,
                candidate.request.branch_id,
                turn_id,
                envelope.envelope_sha256,
                handle.provider_thread_id_sha256,
            )
            self._acceptance_failpoint("after_in_memory_ledger_append")
            injection = self.planner_session.synchronize_accepted_final_sequence_with_receipt(
                envelope,
                stable_reference_descriptors=stable_reference_descriptors,
            )
            if injection is None:
                raise StateConflictError("accepted final sequence was not injected")
            self._acceptance_failpoint("after_provider_injection_returned")
            self.world.mark_acceptance_injection_returned(
                candidate.request.world_id,
                candidate.request.branch_id,
                turn_id,
                envelope_sha256=envelope.envelope_sha256,
                provider_thread_sha256=handle.provider_thread_id_sha256,
                injection_operation_receipt_sha256=injection.operation_receipt_sha256,
            )
            self._acceptance_failpoint("after_world_injection_update")
            if acceptance_snapshot_store is None:
                raise StateConflictError("accepted action lacked snapshot path preflight")
            snapshot = self.planner_session.snapshot()
            snapshot_receipt = acceptance_snapshot_store.save_for_acceptance(
                snapshot,
                accepted_turn_id=turn_id,
                accepted_envelope_sha256=envelope.envelope_sha256,
                injection_receipt=injection,
            )
            self.world.mark_acceptance_session_snapshot_persisted(
                candidate.request.world_id,
                candidate.request.branch_id,
                turn_id,
                snapshot_receipt=snapshot_receipt,
                injection_receipt=injection,
            )
            self._acceptance_failpoint("after_snapshot_persisted")
            pending_synchronization = self.world.acceptance_synchronization_record(
                candidate.request.world_id,
                candidate.request.branch_id,
                turn_id,
            )
            synchronization_receipt_sha256 = (
                self.world.expected_acceptance_synchronization_receipt_sha256(
                    candidate.request.world_id,
                    candidate.request.branch_id,
                    turn_id,
                    envelope.envelope_sha256,
                )
            )
            compact_receipt, stable_references = (
                build_stable_accepted_context_references(
                    world_id=candidate.request.world_id,
                    branch_id=candidate.request.branch_id,
                    scene_id=candidate.request.scene_id,
                    planner_session_id=handle.provider_session_id,
                    provider_thread_sha256=handle.provider_thread_id_sha256,
                    accepted_turn_ids=self.planner_session.snapshot().accepted_turn_ids,
                    accepted_turn_id=turn_id,
                    accepted_envelope_sha256=envelope.envelope_sha256,
                    accepted_pair_sha256=str(
                        pending_synchronization["accepted_pair_sha256"]
                    ),
                    accepted_event_sha256=str(
                        pending_synchronization["accepted_event_sha256"]
                    ),
                    acceptance_receipt_sha256=receipt.receipt_sha256,
                    injection_receipt_sha256=injection.operation_receipt_sha256,
                    session_snapshot_sha256=str(
                        pending_synchronization[
                            "planner_session_snapshot_sha256"
                        ]
                    ),
                    synchronization_receipt_sha256=synchronization_receipt_sha256,
                    facts=accepted_facts,
                )
            )
            stable_reference_path = StableAcceptedContextReferenceStore(
                self.world.branch_root(
                    candidate.request.world_id, candidate.request.branch_id
                )
            ).save(receipt=compact_receipt, references=stable_references)
            self.world.mark_acceptance_stable_references_persisted(
                candidate.request.world_id,
                candidate.request.branch_id,
                turn_id,
                reference_path=stable_reference_path,
                compact_head_receipt_sha256=compact_receipt.receipt_sha256,
            )
            self._acceptance_failpoint("after_stable_references_persisted")
            self.world.mark_acceptance_model_synchronized(
                candidate.request.world_id,
                candidate.request.branch_id,
                turn_id,
                envelope.envelope_sha256,
            )
            synchronized = self.world.acceptance_synchronization_record(
                candidate.request.world_id,
                candidate.request.branch_id,
                turn_id,
            )
            if (
                synchronized.get("synchronization_receipt_sha256")
                != synchronization_receipt_sha256
            ):
                raise StateConflictError(
                    "acceptance synchronization receipt changed after reference custody"
                )
            rendered_injection = envelope.render_for_planner(
                stable_reference_descriptors=stable_reference_descriptors
            )
            usage_path = debug.root / "usage.json"
            usage_payload = json.loads(usage_path.read_text(encoding="utf-8"))
            usage_payload["accepted_context_injection"] = {
                "context_mode": candidate.context_mode.value,
                "accepted_turn_id": turn_id,
                "injected_context_sha256": text_sha256(rendered_injection),
                "injected_context_bytes": len(rendered_injection.encode("utf-8")),
                "injected_context_estimated_tokens": (
                    len(rendered_injection.encode("utf-8")) + 3
                )
                // 4,
                "injection_receipt_sha256": injection.operation_receipt_sha256,
                "compact_accepted_head_receipt_sha256": compact_receipt.receipt_sha256,
                "stable_reference_keys": compact_receipt.stable_reference_keys,
            }
            debug.write_json("usage.json", usage_payload)
        else:
            self.validator_session.record_validator_candidate(
                turn_id, package.package_sha256, rejected=True
            )
        return receipt

    def _acceptance_failpoint(self, stage: str) -> None:
        if self._acceptance_sync_failpoint is not None:
            self._acceptance_sync_failpoint(stage)


def _provider_debug(result: Any) -> dict[str, Any]:
    receipt = getattr(result, "provider_receipt", None)
    telemetry = getattr(result, "operation_telemetry", None)
    return {
        "provider_receipt": to_primitive(receipt) if receipt is not None else None,
        "operation_telemetry": to_primitive(telemetry) if telemetry is not None else None,
        "tool_call_count": int(getattr(result, "tool_call_count", 0)),
        "failed_tool_call_count": int(getattr(result, "failed_tool_call_count", 0)),
        "world_tool_debug": getattr(result, "world_tool_debug", None),
    }


def _snapshot_files(root: Path) -> dict[str, str]:
    return {
        value.relative_to(root).as_posix(): text_sha256(value.read_text(encoding="utf-8"))
        for value in sorted(path for path in root.rglob("*") if path.is_file())
    }
