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
from cera.schema import from_mapping

from .contracts import (
    AcceptedFinalSequenceEnvelopeV1,
    AcceptedTurnPairV1,
    CharacterSummaryEnvelopeV1,
    EventRecordCandidateV1,
    RichPlannerSequenceV1,
    ValidatorFinalizationPackageV1,
    ValidatorTaskMode,
)
from .ingress import ContinuousIngressAuthorityPort
from .evidence import (
    AcceptedSessionProjectionV1,
    RequestEvidenceBindingRegistry,
    bind_character_summary_envelopes,
    project_final_sequence_facts,
)
from .prompting import (
    build_continuous_composer_prompt,
    build_planner_turn_prompt,
    build_validator_prompt,
)
from .record_policy import PERSISTENCE_POLICY_SHA256
from .sessions import (
    ContinuousSessionCoordinator,
    ContinuousSessionRole,
    ContinuousSessionSnapshotReceiptV1,
    ContinuousSessionSnapshotStore,
    assert_separate_role_sessions,
)
from .world import (
    CandidateWorldViewV1,
    ContinuousDebugRecorder,
    ContinuousWorldStore,
    SceneChangeCoordinator,
    SceneChangeEnvelopeV1,
    WorldPromotionReceiptV1,
)


class PlannerPort(Protocol):
    def plan(self, prompt: str): ...


class ComposerPort(Protocol):
    def compose(self, prompt: str): ...


class ValidatorPort(Protocol):
    def validate(self, prompt: str, **kwargs): ...


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
    current_authority_packet: dict[str, Any]
    ingress_receipt_id: str
    ingress_receipt_sha256: str
    character_summaries: tuple[CharacterSummaryEnvelopeV1, ...] = ()
    cera_scene_change: bool = False

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
    validator_package: ValidatorFinalizationPackageV1
    planner_prompt_sha256: str
    composer_prompt_sha256: str
    validator_prompt_sha256: str
    debug_root: Path
    provider_calls: int
    evidence_registry_sha256: str
    protected_user_claim_ledger_sha256: str
    protected_user_realization_ledger_sha256: str
    story_segment_ledger_sha256: str
    protected_semantic_adjudication_ledger_sha256: str
    accepted_session_projection_ledger_sha256: str

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
                "ingress_receipt_sha256": self.request.ingress_receipt_sha256,
                "persistence_policy_sha256": PERSISTENCE_POLICY_SHA256,
                "planner_prompt_sha256": self.planner_prompt_sha256,
                "composer_prompt_sha256": self.composer_prompt_sha256,
                "validator_prompt_sha256": self.validator_prompt_sha256,
                "planner_schema_version": self.planner_sequence.schema_version,
                "validator_schema_version": self.validator_package.schema_version,
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
        ingress_authority: ContinuousIngressAuthorityPort,
        acceptance_sync_failpoint: Callable[[str], None] | None = None,
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
        self.ingress_authority = ingress_authority
        self._acceptance_sync_failpoint = acceptance_sync_failpoint
        self._candidates: dict[str, ContinuousTurnCandidateV1] = {}

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

    def prepare(self, request: ContinuousTurnRequestV1) -> ContinuousTurnCandidateV1:
        if request.cera_scene_change:
            raise StateConflictError(
                "scene-change prompt must first pass the explicit SceneChangeCoordinator"
            )
        return self._prepare(request, scene_change_envelope=None, prior_provider_calls=0)

    def prepare_after_validated_scene_change(
        self,
        request: ContinuousTurnRequestV1,
        *,
        scene_change_envelope: SceneChangeEnvelopeV1,
        summary_provider_calls: int,
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
            scene_change_envelope=to_primitive(scene_change_envelope),
            prior_provider_calls=summary_provider_calls,
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
        prompt, _usage = build_validator_prompt(
            task_mode=ValidatorTaskMode.SCENE_SUMMARY,
            current_user_source=None,
            planner_sequence=None,
            deepseek_realization=None,
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
            result = self.validator.validate(prompt, accepted_pairs=pairs)
        except BaseException as exc:
            scene_debug.record_failure("scene_summary_validator", exc)
            raise
        elapsed = time.perf_counter_ns() - started
        package = result.value
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
        scene_change_envelope: dict[str, Any] | None,
        prior_provider_calls: int,
    ) -> ContinuousTurnCandidateV1:
        if (
            request.world_id != self.planner_session.compatibility.world_id
            or request.branch_id != self.planner_session.compatibility.branch_id
            or request.world_id != self.validator_session.compatibility.world_id
            or request.branch_id != self.validator_session.compatibility.branch_id
        ):
            raise StateConflictError("continuous turn changed session world or branch")
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
        accepted_session_bindings = self._bind_latest_accepted_session_evidence(
            request=request,
            registry=evidence_registry,
            branch_root=branch_root,
        )
        summary_bindings = bind_character_summary_envelopes(
            registry=evidence_registry,
            branch_root=branch_root,
            summaries=request.character_summaries,
        )
        planner_prompt, planner_usage = build_planner_turn_prompt(
            current_packet={
                **request.current_authority_packet,
                "world_id": request.world_id,
                "branch_id": request.branch_id,
                "scene_id": request.scene_id,
                "turn_id": request.turn_id,
                "current_user_message": request.user_message,
                "request_local_evidence_bindings": evidence_registry.prompt_manifest(),
                "current_source_binding_key": current_source_binding.binding_key,
                "mechanical_connective_binding_key": mechanical_binding.binding_key,
                "protected_user_source_claims": evidence_registry.protected_user_claim_manifest(),
                "ingress_source_units": tuple(
                    to_primitive(value) for value in source_units
                ),
                "ingress_receipt": to_primitive(ingress_receipt),
                "accepted_session_bindings": tuple(
                    {
                        "binding_key": value["binding_key"],
                        "projection_sha256": value["projection_sha256"],
                    }
                    for value in accepted_session_bindings
                ),
                "accepted_session_projections": accepted_session_bindings,
                "character_summary_bindings": tuple(summary_bindings),
            },
            accepted_envelopes=(),
            character_summaries=request.character_summaries,
            scene_change_envelope=scene_change_envelope,
        )
        debug.write_text("planner_raw_prompt.txt", planner_prompt)
        debug.write_json(
            "planner_prompt_components.json",
            [to_primitive(value) for value in planner_usage],
        )
        started = time.perf_counter_ns()
        try:
            planner_result = self.planner.plan(planner_prompt)
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
        evidence_registry.validate_sequence(
            planner_sequence,
            branch_root=branch_root,
        )
        self.planner_session.record_planner_provisional(
            request.turn_id, planner_sequence.sequence_sha256
        )
        debug.write_json("planner_output.json", to_primitive(planner_sequence))
        debug.write_json("planner_tools.json", _provider_debug(planner_result))
        composer_prompt, composer_usage = build_continuous_composer_prompt(
            current_user_source=request.user_message,
            ingress_source_units=tuple(
                to_primitive(value) for value in source_units
            ),
            planner_sequence=planner_sequence,
            character_summaries=request.character_summaries,
            protected_user_claim_manifest=evidence_registry.protected_user_claim_manifest(),
            accepted_session_projections=accepted_session_bindings,
        )
        debug.write_json("deepseek_request.json", {"prompt": composer_prompt})
        started = time.perf_counter_ns()
        try:
            composer_result = self.composer.compose(composer_prompt)
        except BaseException as exc:
            debug.record_failure("composer", exc)
            raise
        composer_elapsed = time.perf_counter_ns() - started
        story_text = composer_result.value.story_text
        protected_realizations = tuple(
            getattr(composer_result.value, "protected_user_realizations", ())
        )
        story_segments = tuple(
            getattr(composer_result.value, "story_segments", ())
        )
        evidence_registry.validate_composer_realization(
            story_text=story_text,
            realizations=protected_realizations,
            story_segments=story_segments,
        )
        composer_payload = {
            "schema_version": getattr(composer_result.value, "schema_version", None),
            "story_text": story_text,
            "protected_user_realizations": to_primitive(protected_realizations),
            "story_segments": to_primitive(story_segments),
        }
        debug.write_json("deepseek_output.json", composer_payload)
        validator_prompt, validator_usage = build_validator_prompt(
            task_mode=ValidatorTaskMode.FINALIZE_TURN,
            current_user_source=request.user_message,
            planner_sequence=planner_sequence,
            deepseek_realization=story_text,
            accepted_turn_id=request.turn_id,
            world_file_manifest=self.world.active_manifest(
                request.world_id, request.branch_id
            ),
            evidence_binding_manifest=evidence_registry.prompt_manifest(),
            protected_user_claim_manifest=evidence_registry.protected_user_claim_manifest(),
            deepseek_protected_user_realizations=tuple(
                to_primitive(value)
                for value in protected_realizations
            ),
            deepseek_story_segments=tuple(
                to_primitive(value) for value in story_segments
            ),
            ingress_source_units=tuple(
                to_primitive(value) for value in source_units
            ),
            accepted_session_projections=accepted_session_bindings,
        )
        debug.write_json("validator_request.json", {"prompt": validator_prompt})
        started = time.perf_counter_ns()
        try:
            validator_result = self.validator.validate(validator_prompt)
        except BaseException as exc:
            debug.record_failure("validator", exc)
            raise
        validator_elapsed = time.perf_counter_ns() - started
        package = validator_result.value
        debug.write_json("validator_output.json", to_primitive(package))
        debug.write_json("validator_tools.json", _provider_debug(validator_result))
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
        self.validator_session.record_validator_candidate(
            request.turn_id, package.package_sha256
        )
        before_files = _snapshot_files(candidate_view.root / "ACTIVE_VIEW")
        provider_calls = prior_provider_calls + sum(
            int(getattr(value.provider_receipt, "external_provider_calls", 0))
            for value in (planner_result, composer_result, validator_result)
        )
        candidate = ContinuousTurnCandidateV1(
            request=request,
            candidate_view=candidate_view,
            planner_sequence=planner_sequence,
            deepseek_story_text=story_text,
            validator_package=package,
            planner_prompt_sha256=text_sha256(planner_prompt),
            composer_prompt_sha256=text_sha256(composer_prompt),
            validator_prompt_sha256=text_sha256(validator_prompt),
            debug_root=debug.root,
            provider_calls=provider_calls,
            evidence_registry_sha256=evidence_registry.registry_sha256,
            protected_user_claim_ledger_sha256=canonical_sha256(
                evidence_registry.protected_user_claim_manifest()
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
                accepted_session_bindings
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
            "planner_prompt_components.json": [to_primitive(value) for value in planner_usage],
            "planner_output.json": to_primitive(planner_sequence),
            "planner_tools.json": _provider_debug(planner_result),
            "deepseek_request.json": {"prompt": composer_prompt},
            "deepseek_output.json": composer_payload,
            "validator_request.json": {"prompt": validator_prompt},
            "validator_output.json": to_primitive(package),
            "validator_tools.json": _provider_debug(validator_result),
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
            },
            "usage.json": {
                "planner_prompt_components": [to_primitive(value) for value in planner_usage],
                "composer_prompt_components": [to_primitive(value) for value in composer_usage],
                "validator_prompt_components": [to_primitive(value) for value in validator_usage],
            },
            "stage_timings.json": {
                "planner_ns": planner_elapsed,
                "composer_ns": composer_elapsed,
                "validator_ns": validator_elapsed,
            },
            "errors.json": [],
            "replay_input.json": {
                "request": to_primitive(request),
                "planner_result": to_primitive(planner_sequence),
                "composer_result": composer_payload,
                "evidence_registry_sha256": evidence_registry.registry_sha256,
                "protected_user_claim_manifest": evidence_registry.protected_user_claim_manifest(),
                "ingress_source_units": tuple(
                    to_primitive(value) for value in source_units
                ),
                "ingress_receipt": to_primitive(ingress_receipt),
                "story_segments": to_primitive(story_segments),
                "authority_context_sha256": candidate.authority_context_sha256,
                "candidate_sha256": candidate.candidate_sha256,
                "validator_result": to_primitive(package),
            },
        }
        debug.write_text("planner_raw_prompt.txt", planner_prompt)
        for name, payload in debug_payloads.items():
            debug.write_json(name, payload)
        self._candidates[request.turn_id] = candidate
        return candidate

    def _bind_latest_accepted_session_evidence(
        self,
        *,
        request: ContinuousTurnRequestV1,
        registry: RequestEvidenceBindingRegistry,
        branch_root: Path,
    ) -> tuple[dict[str, Any], ...]:
        accepted_ids = self.planner_session.snapshot().accepted_turn_ids
        if not accepted_ids:
            return ()
        accepted_turn_id = accepted_ids[-1]
        journal = self.world.acceptance_synchronization_record(
            request.world_id, request.branch_id, accepted_turn_id
        )
        if journal.get("model_injection_state") != "synchronized":
            raise StateConflictError("accepted session evidence is not synchronized")
        envelope = self.world.accepted_final_envelope(
            request.world_id, request.branch_id, accepted_turn_id
        )
        handle = self.planner_session.ensure_session()
        snapshot_store = ContinuousSessionSnapshotStore(branch_root)
        snapshot_receipt = from_mapping(
            ContinuousSessionSnapshotReceiptV1,
            journal.get("planner_session_snapshot_receipt"),
        )
        snapshot = snapshot_store.load_immutable(snapshot_receipt)
        if (
            snapshot.handle.provider_thread_id_sha256 != handle.provider_thread_id_sha256
            or accepted_turn_id not in snapshot.accepted_turn_ids
            or journal.get("accepted_final_envelope_sha256") != envelope.envelope_sha256
            or journal.get("planner_provider_thread_sha256")
            != handle.provider_thread_id_sha256
            or journal.get("planner_session_snapshot_sha256")
            != snapshot.snapshot_sha256
            or journal.get("planner_session_snapshot_relative_path")
            != snapshot_receipt.immutable_relative_path
            or journal.get("promotion_receipt_payload", {}).get("receipt_sha256")
            not in {None, envelope.acceptance_receipt_sha256}
        ):
            raise StateConflictError("accepted session evidence bindings changed")
        sync_receipt = journal.get("synchronization_receipt_sha256")
        if not isinstance(sync_receipt, str):
            raise StateConflictError("accepted session synchronization receipt is absent")
        event_path = branch_root / "ACTIVE" / str(journal["accepted_event_relative_path"])
        pair_path = branch_root / "ACTIVE" / str(journal["accepted_pair_relative_path"])
        active_root = (branch_root / "ACTIVE").resolve()
        for path, expected, label in (
            (event_path, journal.get("accepted_event_sha256"), "event"),
            (pair_path, journal.get("accepted_pair_sha256"), "pair"),
        ):
            resolved = path.resolve()
            if (
                active_root not in resolved.parents
                or not path.is_file()
                or path.is_symlink()
                or not isinstance(expected, str)
                or text_sha256(path.read_text(encoding="utf-8")) != expected
            ):
                raise StateConflictError(f"accepted session {label} bytes changed")
        pair = from_mapping(
            AcceptedTurnPairV1,
            json.loads(pair_path.read_text(encoding="utf-8")),
        )
        raw_event = json.loads(event_path.read_text(encoding="utf-8"))
        if not isinstance(raw_event, dict):
            raise StateConflictError("accepted session event is malformed")
        event = from_mapping(
            EventRecordCandidateV1,
            {key: value for key, value in raw_event.items() if key != "_cera_revision"},
        )
        if (
            pair.accepted_turn_id != accepted_turn_id
            or event.accepted_turn_id != accepted_turn_id
            or pair.complete_final_sequence.sequence_sha256
            != envelope.complete_final_sequence.sequence_sha256
            or set(event.final_sequence_item_keys)
            != {value.item_key for value in pair.complete_final_sequence.items}
        ):
            raise StateConflictError("accepted session pair and event disagree")
        if event.scene_id != request.scene_id:
            return ()
        facts = tuple(
            fact
            for item in pair.complete_final_sequence.items
            for fact in project_final_sequence_facts(item)
        )
        public_facts = tuple(
            fact
            for fact in facts
            if fact.knowledge_owner_id is None
        )
        owners = tuple(
            dict.fromkeys(
                fact.knowledge_owner_id
                for fact in facts
                if fact.knowledge_owner_id is not None
                and fact.knowledge_owner_id
                in set(fact.roles.involved_ids)
            )
        )
        projections = []
        for owner in (None, *owners):
            private_facts = tuple(
                fact for fact in facts if fact.knowledge_owner_id == owner
            )
            if owner is None:
                projection_facts = public_facts
            else:
                if not private_facts:
                    continue
                projection_facts = (*public_facts, *private_facts)
            if not projection_facts:
                continue
            projection_key = "projection_session_" + canonical_sha256(
                {
                    "world_id": request.world_id,
                    "branch_id": request.branch_id,
                    "request_turn_id": request.turn_id,
                    "accepted_turn_id": accepted_turn_id,
                    "scene_id": event.scene_id,
                    "knowledge_owner_id": owner,
                    "fact_keys": tuple(fact.fact_key for fact in projection_facts),
                }
            )[:20]
            projection = AcceptedSessionProjectionV1(
                schema_version=AcceptedSessionProjectionV1.SCHEMA_VERSION,
                projection_key=projection_key,
                world_id=request.world_id,
                branch_id=request.branch_id,
                request_turn_id=request.turn_id,
                accepted_turn_id=accepted_turn_id,
                scene_id=event.scene_id,
                knowledge_owner_id=owner,
                facts=tuple(projection_facts),
                accepted_pair_sha256=str(journal["accepted_pair_sha256"]),
                accepted_event_sha256=str(journal["accepted_event_sha256"]),
                accepted_envelope_sha256=envelope.envelope_sha256,
                acceptance_receipt_sha256=envelope.acceptance_receipt_sha256,
                provider_thread_sha256=handle.provider_thread_id_sha256,
                session_snapshot_sha256=snapshot.snapshot_sha256,
                synchronization_receipt_sha256=sync_receipt,
            )
            binding = registry.allocate_accepted_session_projection(projection)
            projections.append(
                {
                    "binding_key": binding.binding_key,
                    "projection_sha256": projection.projection_sha256,
                    "projection": to_primitive(projection),
                }
            )
        return tuple(projections)

    def apply_creator_action(
        self,
        turn_id: str,
        action: CreatorReviewAction,
    ) -> WorldPromotionReceiptV1:
        candidate = self._candidates[turn_id]
        package = candidate.validator_package
        pair = None
        if package.complete_final_sequence is not None:
            pair = AcceptedTurnPairV1(
                accepted_turn_id=turn_id,
                user_message=candidate.request.user_message,
                complete_final_sequence=package.complete_final_sequence,
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
                envelope
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
            snapshot_store = ContinuousSessionSnapshotStore(
                self.world.branch_root(
                    candidate.request.world_id, candidate.request.branch_id
                ),
                failpoint=self._acceptance_failpoint,
            )
            snapshot = self.planner_session.snapshot()
            snapshot_receipt = snapshot_store.save_for_acceptance(
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
            self.world.mark_acceptance_model_synchronized(
                candidate.request.world_id,
                candidate.request.branch_id,
                turn_id,
                envelope.envelope_sha256,
            )
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
