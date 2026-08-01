"""Provider-neutral orchestration for the shadow continuous route."""

from __future__ import annotations

from dataclasses import dataclass, replace
import json
from pathlib import Path
import time
from typing import Any, Protocol

from cera.creator_review.models import CreatorReviewAction
from cera.errors import ContractValidationError, StateConflictError
from cera.serialization import canonical_sha256, text_sha256, to_primitive

from .contracts import (
    AcceptedFinalSequenceEnvelopeV1,
    AcceptedTurnPairV1,
    CharacterSummaryEnvelopeV1,
    RichPlannerSequenceV1,
    ValidatorFinalizationPackageV1,
    ValidatorTaskMode,
)
from .prompting import (
    build_continuous_composer_prompt,
    build_planner_turn_prompt,
    build_validator_prompt,
)
from .sessions import (
    ContinuousSessionCoordinator,
    ContinuousSessionRole,
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
    scene_id: str
    turn_id: str
    user_message: str
    current_authority_packet: dict[str, Any]
    character_summaries: tuple[CharacterSummaryEnvelopeV1, ...] = ()
    cera_scene_change: bool = False

    def __post_init__(self) -> None:
        for field in ("world_id", "branch_id", "scene_id", "turn_id", "user_message"):
            value = getattr(self, field)
            if not isinstance(value, str) or not value.strip():
                raise ContractValidationError(f"continuous turn {field} is required")
        if type(self.cera_scene_change) is not bool:
            raise ContractValidationError("continuous scene change flag must be boolean")


@dataclass(frozen=True, slots=True)
class ContinuousTurnCandidateV1:
    request: ContinuousTurnRequestV1
    candidate_view: CandidateWorldViewV1
    planner_sequence: RichPlannerSequenceV1
    deepseek_story_text: str
    validator_package: ValidatorFinalizationPackageV1
    planner_prompt_sha256: str
    validator_prompt_sha256: str
    debug_root: Path
    provider_calls: int

    @property
    def candidate_sha256(self) -> str:
        return canonical_sha256(
            {
                "turn_id": self.request.turn_id,
                "planner": self.planner_sequence.sequence_sha256,
                "story": text_sha256(self.deepseek_story_text),
                "validator": self.validator_package.package_sha256,
            }
        )


@dataclass(frozen=True, slots=True)
class ContinuousSceneChangeCandidateV1:
    scene_change_envelope: SceneChangeEnvelopeV1
    scene_summary_package: ValidatorFinalizationPackageV1
    turn_candidate: ContinuousTurnCandidateV1
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
        self._candidates: dict[str, ContinuousTurnCandidateV1] = {}

    def restore_pending_accepted_context(self) -> tuple[str, ...]:
        """Report incomplete physical-thread synchronization after restart.

        V1 never retries an ambiguous history injection automatically.  The
        caller must stop and preserve the pending turn identities for governed
        diagnosis.
        """

        restored = []
        for turn_id in self.planner_session.unsynchronized_accepted_turn_ids:
            envelope = self.world.accepted_final_envelope(
                self.planner_session.compatibility.world_id,
                self.planner_session.compatibility.branch_id,
                turn_id,
            )
            expected = next(
                value.payload_sha256
                for value in self.planner_session.snapshot().context_events
                if value.event_type == "accepted_final_sequence"
                and value.turn_or_scene_id == turn_id
            )
            if envelope.envelope_sha256 != expected:
                raise StateConflictError("restored accepted-final envelope hash changed")
            restored.append(turn_id)
        return tuple(restored)

    def prepare(self, request: ContinuousTurnRequestV1) -> ContinuousTurnCandidateV1:
        if request.cera_scene_change:
            raise StateConflictError(
                "scene-change prompt must first pass the explicit SceneChangeCoordinator"
            )
        return self._prepare(request, scene_change_envelope=None, prior_provider_calls=0)

    def prepare_scene_change(
        self,
        request: ContinuousTurnRequestV1,
        *,
        completed_scene_id: str,
        accepted_turn_ids: tuple[str, ...],
    ) -> ContinuousSceneChangeCandidateV1:
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
        turn_candidate = self._prepare(
            replace(request, cera_scene_change=False),
            scene_change_envelope=to_primitive(envelope),
            prior_provider_calls=summary_calls,
        )
        debug = ContinuousDebugRecorder(
            self.world.branch_root(request.world_id, request.branch_id),
            request.scene_id,
            request.turn_id,
        )
        debug.write_json("scene_change_request.json", {"prompt": prompt})
        debug.write_json("scene_change_output.json", to_primitive(package))
        debug.write_json("scene_change_tools.json", _provider_debug(result))
        debug.write_json("scene_change_timing.json", {"validator_ns": elapsed})
        return ContinuousSceneChangeCandidateV1(
            scene_change_envelope=envelope,
            scene_summary_package=package,
            turn_candidate=turn_candidate,
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
        if request.turn_id in self._candidates:
            raise StateConflictError("continuous turn candidate already exists")
        if self.planner_session.unsynchronized_accepted_turn_ids:
            raise StateConflictError(
                "accepted Planner context is not synchronized; continuation is blocked"
            )
        branch_root = self.world.initialize(request.world_id, request.branch_id)
        debug = ContinuousDebugRecorder(branch_root, request.scene_id, request.turn_id)
        debug.initialize()
        candidate_view = self.world.create_candidate(
            request.world_id, request.branch_id, request.turn_id
        )
        planner_prompt, planner_usage = build_planner_turn_prompt(
            current_packet={
                **request.current_authority_packet,
                "world_id": request.world_id,
                "branch_id": request.branch_id,
                "scene_id": request.scene_id,
                "turn_id": request.turn_id,
                "current_user_message": request.user_message,
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
        if (
            planner_sequence.world_id != request.world_id
            or planner_sequence.branch_id != request.branch_id
            or planner_sequence.scene_id != request.scene_id
        ):
            raise StateConflictError("Planner changed turn scope")
        self.planner_session.record_planner_provisional(
            request.turn_id, planner_sequence.sequence_sha256
        )
        debug.write_json("planner_output.json", to_primitive(planner_sequence))
        debug.write_json("planner_tools.json", _provider_debug(planner_result))
        composer_prompt, composer_usage = build_continuous_composer_prompt(
            current_user_source=request.user_message,
            planner_sequence=planner_sequence,
            character_summaries=request.character_summaries,
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
        debug.write_json("deepseek_output.json", {"story_text": story_text})
        validator_prompt, validator_usage = build_validator_prompt(
            task_mode=ValidatorTaskMode.FINALIZE_TURN,
            current_user_source=request.user_message,
            planner_sequence=planner_sequence,
            deepseek_realization=story_text,
            accepted_turn_id=request.turn_id,
            world_file_manifest=self.world.active_manifest(
                request.world_id, request.branch_id
            ),
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
        ):
            raise StateConflictError("Validator changed turn scope")
        self.validator_session.record_validator_candidate(
            request.turn_id, package.package_sha256
        )
        try:
            self.world.record_candidate_package(
                request.world_id, request.branch_id, candidate_view, package
            )
        except BaseException as exc:
            debug.record_failure("candidate_package", exc)
            raise
        before_files = _snapshot_files(candidate_view.root / "ACTIVE_VIEW")
        provider_calls = prior_provider_calls + sum(
            int(getattr(value.provider_receipt, "external_provider_calls", 0))
            for value in (planner_result, composer_result, validator_result)
        )
        debug_payloads = {
            "planner_prompt_components.json": [to_primitive(value) for value in planner_usage],
            "planner_output.json": to_primitive(planner_sequence),
            "planner_tools.json": _provider_debug(planner_result),
            "deepseek_request.json": {"prompt": composer_prompt},
            "deepseek_output.json": {"story_text": story_text},
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
                "composer_result": {"story_text": story_text},
                "validator_result": to_primitive(package),
            },
        }
        debug.write_text("planner_raw_prompt.txt", planner_prompt)
        for name, payload in debug_payloads.items():
            debug.write_json(name, payload)
        candidate = ContinuousTurnCandidateV1(
            request=request,
            candidate_view=candidate_view,
            planner_sequence=planner_sequence,
            deepseek_story_text=story_text,
            validator_package=package,
            planner_prompt_sha256=text_sha256(planner_prompt),
            validator_prompt_sha256=text_sha256(validator_prompt),
            debug_root=debug.root,
            provider_calls=provider_calls,
        )
        self._candidates[request.turn_id] = candidate
        return candidate

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
            self.planner_session.append_accepted_final_sequence(envelope)
            self.planner_session.synchronize_accepted_final_sequence(envelope)
        else:
            self.validator_session.record_validator_candidate(
                turn_id, package.package_sha256, rejected=True
            )
        return receipt


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
