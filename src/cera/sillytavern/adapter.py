"""CERA-native SillyTavern adapter over the qualified ordinary runtime path."""

from __future__ import annotations

from dataclasses import dataclass, replace
import json
import re
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Lock, Thread
from typing import Callable, Protocol

from cera.composer import (
    ArtifactPublicationMode,
    ComposerContextAssembler,
    ComposerCoordinator,
    DeepSeekSceneComposerPort,
)
from cera.contracts import (
    BehavioralTurnControls,
    CharacterAutonomyMode,
    PromptHandlingMode,
    SceneDepthMode,
    CreatorRevisionDirective,
    CreatorRevisionMode,
)
from cera.evidence import EvidenceService
from cera.genesis.hanezawa_builder import CHARACTER_IDS
from cera.ids import IdKind, TypedId, deterministic_id
from cera.errors import TransactionError
from cera.creator_review import (
    CreatorReviewAction,
    CreatorReviewCoordinator,
    CreatorReviewState,
    export_creator_correction_report,
)
from cera.kernel import TurnKernel
from cera.providers import (
    CodexExecRunner,
    CodexSDKTransport,
    CodexStructuredOutputTransport,
    DeepSeekChatTransport,
    codex_cli_realization_verifier_candidate,
    codex_reasoner_candidate,
    deepseek_composer_candidate,
)
from cera.reasoner import CodexSceneReasonerPort, ReasonerCoordinator
from cera.reasoner_session import NativeStoredReasonerSessionRuntime
from cera.realization import (
    CodexSceneRealizationVerifierPort,
    SceneRealizationVerificationCoordinator,
)
from cera.runtime import (
    DevelopmentOrdinaryTurnPreparer,
    DevelopmentTurnSpec,
    HanezawaHumanTestWorld,
    LiveShapedTurnPipeline,
    LocalOrdinaryApplication,
    RequiredSeedRecord,
    TurnStageAuditJournal,
)
from cera.serialization import text_sha256
from cera.schema import from_mapping
from cera.serialization import canonical_json

from .models import SillyTavernChatRequest, SillyTavernTurnReply


_SESSION_RE = re.compile(r"(?m)^\[\[CERA_SESSION:([0-9a-f]{64})\]\]\s*$")
_DEPTH_RE = re.compile(
    r"(?m)^\[\[CERA_DEPTH:(OFF|SHORT|AUTO|MEDIUM|LONG|EPIC)\]\]\s*$"
)
_REGEN_RE = re.compile(
    r"(?m)^\[\[CERA_REGENERATE:([a-z][a-z0-9_-]{0,95})\]\]\s*$"
)
_CONTROL_RE = re.compile(
    r"(?m)^\[\[CERA_(?:SESSION:[0-9a-f]{64}|DEPTH:(?:OFF|SHORT|AUTO|MEDIUM|LONG|EPIC)|"
    r"REGENERATE:[a-z][a-z0-9_-]{0,95})\]\]\s*(?:\r?\n)?"
)
_ALL_CAST_PHRASES = re.compile(
    r"\b(?:everyone|the whole family|all (?:the )?girls|the sisters)\b",
    re.IGNORECASE,
)
_HANA_ALIASES = re.compile(r"\b(?:mom|mother|hana)\b", re.IGNORECASE)
_MAX_CONTINUITY_EVENTS = 12
_MAX_ACCEPTED_REPLIES = 4
_HOUSEHOLD_FACT_CUE = re.compile(
    r"\b(?:orientation|household (?:rules?|routines?|schedules?)|"
    r"house (?:rules?|manual|tour)|common areas?|shared (?:areas?|spaces?)|"
    r"meal schedules?|kitchen duty|laundry schedule|trash disposal|"
    r"show me (?:the )?(?:house|home))\b",
    re.IGNORECASE,
)
_DEFAULT_REASONER_SESSION_RUNTIME = object()


@dataclass(frozen=True, slots=True)
class ParsedCeraControl:
    session_key: str
    depth: str
    regeneration_key: str | None
    raw_message: str
    character_autonomy: str = "both"
    prompt_handling: str = "adjustment"
    reasoning_effort: str = "medium"


class SillyTavernTurnExecutor(Protocol):
    def execute(
        self,
        prepared,
        *,
        workspace_root: Path,
        reasoning_effort: str = "medium",
    ) -> SillyTavernTurnReply: ...


class LiveSillyTavernTurnExecutor:
    """One-call-per-stage live executor; no retry and no fallback."""

    def __init__(
        self,
        world: HanezawaHumanTestWorld,
        *,
        rejected_candidate_review_port=None,
        reasoner_port_factory: Callable[[Path], object] | None = None,
        composer_port_factory: Callable[[], object] | None = None,
        verifier_port_factory: Callable[[Path], object] | None = None,
        reasoner_session_runtime=_DEFAULT_REASONER_SESSION_RUNTIME,
        stage_audit_enabled: bool = True,
        diagnostic_report_path: Path | None = None,
    ) -> None:
        self.world = world
        self.rejected_candidate_review_port = rejected_candidate_review_port
        self.verifier_runner = CodexExecRunner()
        self.reasoner_port_factory = reasoner_port_factory
        self.composer_port_factory = composer_port_factory
        self.verifier_port_factory = verifier_port_factory
        self.stage_audit_enabled = stage_audit_enabled
        self.diagnostic_report_path = diagnostic_report_path
        if reasoner_session_runtime is _DEFAULT_REASONER_SESSION_RUNTIME:
            self.reasoner_sessions = (
                None
                if reasoner_port_factory is not None
                else NativeStoredReasonerSessionRuntime(
                    world.store,
                    repository_root=Path(__file__).resolve().parents[3],
                )
            )
        else:
            self.reasoner_sessions = reasoner_session_runtime

    def _reasoner_port(self, workspace: Path, *, effort: str = "medium"):
        if self.reasoner_port_factory is not None:
            return self.reasoner_port_factory(workspace)
        return CodexSceneReasonerPort(
            CodexSDKTransport(
                codex_reasoner_candidate(model="gpt-5.6-sol", effort=effort),
                workspace=workspace,
            ),
            evidence_tools_enabled=True,
        )

    def _composer_port(self):
        if self.composer_port_factory is not None:
            return self.composer_port_factory()
        return DeepSeekSceneComposerPort(
            DeepSeekChatTransport(deepseek_composer_candidate())
        )

    def _verifier_port(self, workspace: Path):
        if self.verifier_port_factory is not None:
            return self.verifier_port_factory(workspace)
        return CodexSceneRealizationVerifierPort(
            CodexStructuredOutputTransport(
                codex_cli_realization_verifier_candidate(
                    model="gpt-5.6-sol",
                    effort="medium",
                ),
                workspace=workspace,
                runner=self.verifier_runner,
            )
        )

    def _pipeline(self, service: EvidenceService, *, audit: bool, verifier_port):
        return LiveShapedTurnPipeline(
            ReasonerCoordinator(service, TurnKernel(service)),
            ComposerContextAssembler(service),
            ComposerCoordinator(),
            realization_verification_coordinator=(
                SceneRealizationVerificationCoordinator(
                    self.rejected_candidate_review_port
                )
            ),
            realization_verifier_port=verifier_port,
            stage_audit_journal=(
                TurnStageAuditJournal(self.world.store)
                if audit and self.stage_audit_enabled
                else None
            ),
        )

    def execute(
        self,
        prepared,
        *,
        workspace_root: Path,
        reasoning_effort: str = "medium",
    ) -> SillyTavernTurnReply:
        ready = Event()
        shared: dict[str, object] = {}
        coordinator = CreatorReviewCoordinator(self.world.store)
        session_binding = None
        if self.reasoner_sessions is not None:
            session_binding = self.reasoner_sessions.begin_candidate(
                prepared.application_request,
                effort=reasoning_effort,
                workspace=workspace_root / "reasoner",
            )

        def run() -> None:
            try:
                with TemporaryDirectory(prefix="cera-review-worker-") as temporary:
                    worker_root = Path(temporary)
                    (worker_root / "reasoner").mkdir()
                    (worker_root / "verifier").mkdir()
                    store = self.world.store
                    service = EvidenceService(store)
                    reasoner_port = (
                        self._reasoner_port(
                            worker_root / "reasoner",
                            effort=reasoning_effort,
                        )
                        if self.reasoner_port_factory is not None
                        else session_binding.reasoner_port
                        if session_binding is not None
                        else self._reasoner_port(
                            worker_root / "reasoner",
                            effort=reasoning_effort,
                        )
                    )
                    verifier_port = self._verifier_port(worker_root / "verifier")
                    pipeline = self._pipeline(
                        service,
                        audit=True,
                        verifier_port=verifier_port,
                    )
                    composer_port = self._composer_port()

                    def display(candidate) -> None:
                        record = coordinator.record_provisional(
                            prepared.application_request,
                            candidate,
                        )
                        if session_binding is not None:
                            self.reasoner_sessions.bind_review(
                                session_binding.checkpoint_id,
                                record.review_id,
                            )
                        shared["record"] = record
                        shared["provider_calls_at_display"] = candidate.provider_calls
                        ready.set()

                    result = pipeline.execute(
                        prepared.application_request.reasoner_request,
                        reasoner_port,
                        prepared.application_request.composer_plan,
                        composer_port,
                        provisional_candidate_callback=display,
                    )
                    record = shared.get("record")
                    if record is None:
                        raise RuntimeError(
                            "CERA pipeline completed without provisional display evidence"
                        )
                    coordinator.prepare_publication(
                        record.review_id,
                        prepared.application_request,
                        result,
                    )
            except Exception as exc:
                record = shared.get("record")
                if record is None:
                    if session_binding is not None:
                        self.reasoner_sessions.invalidate_checkpoint(
                            session_binding.checkpoint_id,
                            reason="pipeline_failed_before_provisional_display",
                        )
                    shared["failure"] = exc
                    ready.set()
                    return
                assessment = getattr(exc, "creator_review_assessment", None)
                try:
                    if assessment is not None:
                        coordinator.record_blocked_review(record.review_id, assessment)
                    else:
                        coordinator.record_error(
                            record.review_id,
                            "sol_verification_unavailable",
                        )
                except Exception as review_exc:
                    shared["background_failure"] = review_exc

        Thread(
            target=run,
            name="cera-post-display-review",
            daemon=True,
        ).start()
        if not ready.wait(timeout=900):
            raise TimeoutError("CERA candidate display timed out")
        if "failure" in shared:
            raise shared["failure"]
        record = shared["record"]
        assert record.candidate_text is not None
        return SillyTavernTurnReply(
            prose=record.candidate_text,
            request_id=str(record.request_id),
            artifact_id=None,
            generation=record.expected_generation + 1,
            provider_calls=int(shared["provider_calls_at_display"]),
            exact_replay=False,
            provisional_review_id=str(record.review_id),
            candidate_id=str(
                deterministic_id(
                    IdKind.COMPOSER_CANDIDATE,
                    "cera.sillytavern.provisional.v1",
                    record.candidate_sha256,
                )
            ),
            review_status=record.state.value,
        )

    @property
    def reasoner_session_status(self) -> dict[str, object]:
        if self.reasoner_sessions is None:
            return {
                "mode": "test_factory",
                "active": False,
            }
        return self.reasoner_sessions.status

    def close(self) -> None:
        if self.reasoner_sessions is not None:
            self.reasoner_sessions.close()

    def get_review(self, review_id: TypedId):
        return self.world.store.get_creator_review(review_id)

    def get_accept_timing(self, review_id: TypedId):
        return self.world.store.get_creator_accept_timing(review_id)

    def review_action(
        self,
        review_id: TypedId,
        action: CreatorReviewAction,
        *,
        feedback: str | None = None,
    ):
        coordinator = CreatorReviewCoordinator(self.world.store)
        if action in {
            CreatorReviewAction.ACCEPT,
            CreatorReviewAction.FALSE_POSITIVE,
        }:
            published = coordinator.accept(review_id, action=action)
            if self.reasoner_sessions is not None:
                self.reasoner_sessions.accept_review(review_id, published)
            return published
        if action is CreatorReviewAction.DECLINE:
            declined = coordinator.decline(review_id)
            if self.reasoner_sessions is not None:
                self.reasoner_sessions.reject_review(review_id)
            return declined
        current = coordinator.store.get_creator_review(review_id)
        if current.state is CreatorReviewState.AWAITING_FEEDBACK:
            if current.creator_action is not action:
                raise ValueError(
                    "CERA review is already awaiting feedback for another action"
                )
            record = current
        else:
            record = coordinator.request_feedback(review_id, action)
        if feedback is None:
            return record
        record = coordinator.record_feedback(review_id, feedback)
        if self.diagnostic_report_path is not None:
            export_creator_correction_report(
                self.world.store,
                self.diagnostic_report_path,
            )
        return self._execute_revision(record, coordinator)

    def _execute_revision(self, record, coordinator: CreatorReviewCoordinator):
        from cera.runtime import (
            IngressPublicationEvidence,
            OrdinaryApplicationRequest,
            OrdinaryApplicationRequestV2,
            ProvisionalTurnCandidate,
        )

        if (
            record.application_request_json is None
            or record.provisional_candidate_json is None
            or record.assessment is None
            or record.creator_feedback is None
            or record.creator_action is None
        ):
            raise ValueError("creator revision lacks its bound provisional evidence")
        app_payload = json.loads(record.application_request_json)
        app_type = (
            OrdinaryApplicationRequestV2
            if app_payload.get("schema_version")
            == OrdinaryApplicationRequestV2.SCHEMA_VERSION
            else OrdinaryApplicationRequest
        )
        application_request = from_mapping(app_type, app_payload)
        prior = from_mapping(
            ProvisionalTurnCandidate,
            json.loads(record.provisional_candidate_json),
        )
        mode = {
            CreatorReviewAction.DEEPSEEK_REWRITE: CreatorRevisionMode.COMPOSER_REWRITE,
            CreatorReviewAction.CODEX_REPLAN: CreatorRevisionMode.REASONER_REPLAN,
            CreatorReviewAction.CORRECTION_ADJUSTMENT: (
                CreatorRevisionMode.CORRECTION_ADJUSTMENT
            ),
        }[record.creator_action]
        directive = CreatorRevisionDirective(
            schema_version=CreatorRevisionDirective.SCHEMA_VERSION,
            mode=mode,
            original_sequence_plan_sha256=record.sequence_plan_sha256,
            original_sequence_beats=record.sequence_beats,
            rejected_candidate_sha256=record.candidate_sha256,
            rejected_candidate_text=record.candidate_text or "",
            assessment_sha256=record.assessment.assessment_sha256,
            reason_codes=record.assessment.reason_codes,
            creator_feedback=record.creator_feedback,
            creator_feedback_sha256=record.creator_feedback_sha256 or "",
        )
        reasoner_request = application_request.reasoner_request
        if mode is not CreatorRevisionMode.COMPOSER_REWRITE:
            reasoner_request = replace(
                reasoner_request,
                creator_revision=directive,
            )
        composer_plan = replace(
            application_request.composer_plan,
            creator_revision=directive,
        )
        if isinstance(application_request, OrdinaryApplicationRequestV2):
            prior_ingress = application_request.ingress_evidence
            ingress = IngressPublicationEvidence.create(
                request_id=prior_ingress.request_id,
                source_sha256=prior_ingress.source_sha256,
                reasoner_request_sha256=reasoner_request.request_sha256,
                interpretation_receipt=prior_ingress.interpretation_receipt,
                seed_receipt=prior_ingress.seed_receipt,
                seed_lookup_receipts=prior_ingress.seed_lookup_receipts,
            )
            revised_request = replace(
                application_request,
                reasoner_request=reasoner_request,
                composer_plan=composer_plan,
                ingress_evidence=ingress,
            )
        else:
            revised_request = replace(
                application_request,
                reasoner_request=reasoner_request,
                composer_plan=composer_plan,
            )

        new_record = None
        replacement_session_binding = None
        effort = "medium"
        if self.reasoner_sessions is not None:
            checkpoint = self.world.store.reasoner_checkpoint_for_review(
                record.review_id
            )
            if checkpoint is None:
                raise ValueError("creator revision lost its Reasoner checkpoint")
            ledger = self.world.store.get_reasoner_session(checkpoint.session_id)
            effort = ledger.compatibility.reasoning_effort
            if mode is not CreatorRevisionMode.COMPOSER_REWRITE:
                coordinator.supersede_for_revision(record.review_id)
                self.reasoner_sessions.reject_review(record.review_id)
        with TemporaryDirectory(prefix="cera-creator-revision-") as temporary:
            worker_root = Path(temporary)
            (worker_root / "reasoner").mkdir()
            (worker_root / "verifier").mkdir()
            service = EvidenceService(self.world.store)
            if (
                self.reasoner_sessions is not None
                and mode is not CreatorRevisionMode.COMPOSER_REWRITE
            ):
                replacement_session_binding = self.reasoner_sessions.begin_candidate(
                    revised_request,
                    effort=effort,
                    workspace=worker_root / "reasoner",
                )
            reasoner_port = (
                self._reasoner_port(worker_root / "reasoner", effort=effort)
                if self.reasoner_port_factory is not None
                else replacement_session_binding.reasoner_port
                if replacement_session_binding is not None
                else self._reasoner_port(worker_root / "reasoner", effort=effort)
            )
            verifier_port = self._verifier_port(worker_root / "verifier")
            pipeline = self._pipeline(
                service,
                audit=False,
                verifier_port=verifier_port,
            )
            composer_port = self._composer_port()

            def display(candidate) -> None:
                nonlocal new_record
                new_record = coordinator.record_provisional(revised_request, candidate)
                if self.reasoner_sessions is None:
                    coordinator.supersede_for_revision(record.review_id)
                elif mode is CreatorRevisionMode.COMPOSER_REWRITE:
                    coordinator.supersede_for_revision(record.review_id)
                    self.reasoner_sessions.rebind_review(
                        prior_review_id=record.review_id,
                        replacement_review_id=new_record.review_id,
                    )
                else:
                    assert replacement_session_binding is not None
                    self.reasoner_sessions.bind_review(
                        replacement_session_binding.checkpoint_id,
                        new_record.review_id,
                    )

            try:
                result = pipeline.execute(
                    revised_request.reasoner_request,
                    (
                        None
                        if mode is CreatorRevisionMode.COMPOSER_REWRITE
                        else reasoner_port
                    ),
                    revised_request.composer_plan,
                    composer_port,
                    provisional_candidate_callback=display,
                    precomputed_reasoner_result=(
                        prior.reasoner
                        if mode is CreatorRevisionMode.COMPOSER_REWRITE
                        else None
                    ),
                )
                if new_record is None:
                    raise RuntimeError("creator revision produced no provisional candidate")
                return coordinator.prepare_publication(
                    new_record.review_id,
                    revised_request,
                    result,
                )
            except Exception as exc:
                if new_record is None:
                    if replacement_session_binding is not None:
                        self.reasoner_sessions.invalidate_checkpoint(
                            replacement_session_binding.checkpoint_id,
                            reason="creator_revision_failed_before_display",
                        )
                    raise
                assessment = getattr(exc, "creator_review_assessment", None)
                if assessment is not None:
                    return coordinator.record_blocked_review(
                        new_record.review_id,
                        assessment,
                    )
                return coordinator.record_error(
                    new_record.review_id,
                    "creator_revision_verification_unavailable",
                )


class CeraSillyTavernAdapter:
    """Serializes local UI turns through one branch-safe development world."""

    def __init__(
        self,
        world: HanezawaHumanTestWorld,
        executor: SillyTavernTurnExecutor,
    ) -> None:
        self.world = world
        self.executor = executor
        self._turn_lock = Lock()

    @property
    def reasoner_session_status(self) -> dict[str, object]:
        return getattr(
            self.executor,
            "reasoner_session_status",
            {"mode": "unavailable", "active": False},
        )

    def complete(self, request: SillyTavernChatRequest) -> SillyTavernTurnReply:
        controls = parse_cera_controls(
            request.latest_user_content,
            session_key=request.cera_session_id,
            depth=request.cera_scene_depth,
            regeneration_key=request.cera_regeneration_key,
            character_autonomy=request.cera_character_autonomy,
            prompt_handling=request.cera_prompt_handling,
            reasoning_effort=request.cera_reasoning_effort,
        )
        with self._turn_lock:
            return self._complete_locked(request, controls)

    def _complete_locked(
        self,
        request: SillyTavernChatRequest,
        controls: ParsedCeraControl,
    ) -> SillyTavernTurnReply:
        branch_id = resolve_session_branch(request, controls, self.world)
        unresolved = self.world.store.unresolved_creator_review_for_branch(branch_id)
        if unresolved is not None:
            raise ValueError(
                "CERA creator review is unresolved; accept, revise, or decline it before continuing"
            )
        branch = self.world.store.get_branch(branch_id)
        present, eligible = select_candidate_cast(
            controls.raw_message,
            self.world,
            branch_id=branch_id,
        )
        publication_mode = (
            ArtifactPublicationMode.REGENERATE
            if controls.regeneration_key is not None
            else ArtifactPublicationMode.APPEND
        )
        continuity = continuity_seeds(
            self.world,
            branch_id=branch_id,
            exclude_artifact_id=(
                branch.head_artifact_id
                if publication_mode is ArtifactPublicationMode.REGENERATE
                else None
            ),
        )
        contextual = contextual_genesis_seeds(
            controls.raw_message,
            self.world,
        )
        verifier_continuity = verifier_continuity_context(
            self.world,
            branch_id=branch_id,
            exclude_artifact_id=(
                branch.head_artifact_id
                if publication_mode is ArtifactPublicationMode.REGENERATE
                else None
            ),
        )
        fingerprint = text_sha256(
            f"{controls.session_key}\x1f{request.conversation_sha256}\x1f"
            f"{controls.regeneration_key or ''}"
        )
        turn_key = f"st-{controls.session_key[:12]}-{fingerprint[:48]}"
        if (
            publication_mode is ArtifactPublicationMode.REGENERATE
            and branch.head_artifact_id is None
        ):
            raise ValueError("cannot regenerate before CERA has accepted a turn")
        depth_anchor = {
            "off": (
                "Minimal sequence depth: use one compact consequential beat and "
                "an optional distinct handoff beat. Do not develop a wider sequence."
            ),
            "short": (
                "Short sequence depth: realize one concise complete causal unit, "
                "including the immediate character consequence and a natural "
                "afterbeat. Include only the lead-in needed for coherence. Do not "
                "collapse the result into summary or pad it."
            ),
            "auto": (
                "Adaptive CERA sequence depth: default to developed ordinary scope, "
                "and expand to domino or multi-scene from causal density rather than "
                "prompt length or a numeric beat quota. Use atomic scope only when "
                "further NPC-controlled progress would be unsupported or repetitive. Develop "
                "the lead-in and move beyond the first complete answer or reaction into "
                "a materially different supported follow-through, consequence, rational "
                "interruption or entrance, material-state change, or meaningful afterbeat. "
                "Descriptive elaboration of one unchanged action does not count. Do not stop merely "
                "because Ted could respond sooner; stop when further consequential "
                "progress truly requires his unsupplied choice. Never pad or invent him."
            ),
            "medium": (
                "Medium sequence depth is a developed-scene request: establish the "
                "useful lead-in, carry the central interaction through materially "
                "distinct NPC-controlled consequences, and include a meaningful "
                "afterbeat. Use several causal blocks when the scene supports them, "
                "without repetition, padding, or invented Ted choices."
            ),
            "long": (
                "Full sequence depth is an affirmative scope request: develop the "
                "complete supported current-scene causal chain and meaningful "
                "NPC-controlled mini-scene progression before Ted's next genuinely "
                "necessary unsupplied choice. A short cue does not reduce this scope. "
                "Do not pad, repeat, or invent Ted."
            ),
            "epic": (
                "Epic sequence depth is an affirmative scope request: cover every "
                "materially distinct supported NPC-controlled consequence across "
                "available current-scene phases or mini-scenes, then stop at the first "
                "natural handoff where further progression would require, assume, or "
                "pre-empt Ted's choice. A short cue does not reduce this scope. Never "
                "pad, repeat, manufacture events, or invent Ted."
            ),
        }[controls.depth]
        initial_scenario_anchor = (
            self.world.initial_scenario_projection(
                _initial_scenario_aware_cast(
                    controls.raw_message,
                    self.world,
                    branch_id=branch_id,
                )
            )
            if branch.generation == 0 and branch.head_artifact_id is None
            else None
        )
        spec = DevelopmentTurnSpec(
            schema_version=DevelopmentTurnSpec.SCHEMA_VERSION,
            turn_key=turn_key,
            raw_message=controls.raw_message,
            session_id=TypedId(
                IdKind.SESSION,
                f"st-{controls.session_key}",
            ),
            branch_id=branch_id,
            present_character_ids=present,
            eligible_responder_ids=eligible,
            scene_anchors=(
                "This is the CERA V1.2 human-test world.",
                (
                "Python exposed the scene-reachable Hanezawa household cast as a "
                "candidate pool, not as a claim that everyone is physically present. "
                "Runtime Codex must select only the responders justified by the cue, "
                "location, continuity, and character logic. An unmentioned entrant "
                "requires a causal entrance or transition in the plan."
                ),
                *((initial_scenario_anchor,) if initial_scenario_anchor else ()),
                (
                    "For regeneration, the existing head is the rejected candidate "
                    "being replaced, not continuity authority for the sibling reply."
                    if publication_mode is ArtifactPublicationMode.REGENERATE
                    else "This is an append turn on the current branch."
                ),
                depth_anchor,
            ),
            established_scene_context=(
                *((initial_scenario_anchor,) if initial_scenario_anchor else ()),
                *verifier_continuity,
            ),
            additional_seed_records=(*continuity, *contextual),
            publication_mode=publication_mode,
            response_profile_version=f"cera-sillytavern-{controls.depth}-v6",
            scene_depth_mode=SceneDepthMode(controls.depth),
            behavioral_controls=BehavioralTurnControls(
                schema_version=BehavioralTurnControls.SCHEMA_VERSION,
                character_autonomy_mode=CharacterAutonomyMode(
                    controls.character_autonomy
                ),
                prompt_handling_mode=PromptHandlingMode(controls.prompt_handling),
                selective_interiority_enabled=True,
                provisional_display_required=True,
                creator_acceptance_required=True,
            ),
        )
        preparer = DevelopmentOrdinaryTurnPreparer(
            store=self.world.store,
            evidence_service=self.world.service,
            world_id=self.world.world_id,
            genesis_revision_id=self.world.revision_id,
            protected_user_id=self.world.protected_user_id,
            access_scope=self.world.system_scope,
            relationship_record_ids=self.world.relationship_record_ids,
            character_baseline_record_ids=(
                self.world.character_baseline_record_ids
            ),
        )
        prepared = preparer.prepare(spec)
        with TemporaryDirectory(prefix="cera-sillytavern-turn-") as temporary:
            workspace = Path(temporary)
            (workspace / "reasoner").mkdir()
            (workspace / "verifier").mkdir()
            return self.executor.execute(
                prepared,
                workspace_root=workspace,
                reasoning_effort=controls.reasoning_effort,
            )

    def get_review(self, review_id: TypedId):
        getter = getattr(self.executor, "get_review", None)
        if getter is None:
            raise ValueError("CERA review controls are unavailable for this executor")
        return getter(review_id)

    def get_accept_timing(self, review_id: TypedId):
        getter = getattr(self.executor, "get_accept_timing", None)
        if getter is None:
            return None
        return getter(review_id)

    def review_action(
        self,
        review_id: TypedId,
        action: CreatorReviewAction,
        *,
        feedback: str | None = None,
    ):
        handler = getattr(self.executor, "review_action", None)
        if handler is None:
            raise ValueError("CERA review controls are unavailable for this executor")
        return handler(review_id, action, feedback=feedback)


def parse_cera_controls(
    content: str,
    *,
    session_key: str | None = None,
    depth: str | None = None,
    regeneration_key: str | None = None,
    character_autonomy: str | None = None,
    prompt_handling: str | None = None,
    reasoning_effort: str | None = None,
) -> ParsedCeraControl:
    sessions = _SESSION_RE.findall(content)
    depths = _DEPTH_RE.findall(content)
    regenerations = _REGEN_RE.findall(content)
    if len(sessions) > 1:
        raise ValueError("CERA session marker is missing or ambiguous")
    if len(depths) > 1:
        raise ValueError("CERA depth marker is missing or ambiguous")
    if len(regenerations) > 1:
        raise ValueError("CERA regeneration marker is ambiguous")
    marker_session = sessions[0] if sessions else None
    marker_depth = depths[0].lower() if depths else None
    marker_regeneration = regenerations[0] if regenerations else None
    normalized_session = session_key.lower() if session_key is not None else None
    normalized_depth = depth.lower() if depth is not None else None
    if normalized_session is not None and marker_session not in {
        None,
        normalized_session,
    }:
        raise ValueError("CERA session metadata conflicts with its marker")
    if normalized_depth is not None and marker_depth not in {
        None,
        normalized_depth,
    }:
        raise ValueError("CERA depth metadata conflicts with its marker")
    if regeneration_key is not None and marker_regeneration not in {
        None,
        regeneration_key,
    }:
        raise ValueError("CERA regeneration metadata conflicts with its marker")
    resolved_session = normalized_session or marker_session
    resolved_depth = normalized_depth or marker_depth or "auto"
    resolved_regeneration = regeneration_key or marker_regeneration
    resolved_autonomy = (character_autonomy or "both").casefold()
    resolved_prompt_handling = (prompt_handling or "adjustment").casefold()
    resolved_reasoning_effort = (reasoning_effort or "medium").casefold()
    if resolved_autonomy not in {"off", "mind", "body", "both"}:
        raise ValueError("CERA character autonomy mode is invalid")
    if resolved_prompt_handling not in {"adjustment", "modification"}:
        raise ValueError("CERA prompt handling mode is invalid")
    if resolved_reasoning_effort not in {"medium", "high", "xhigh"}:
        raise ValueError("CERA Sol reasoning effort is invalid")
    if resolved_session is None:
        raise ValueError("CERA session identity is missing")
    raw_message = _CONTROL_RE.sub("", content).strip()
    if not raw_message:
        raise ValueError("CERA user message is empty after control parsing")
    return ParsedCeraControl(
        session_key=resolved_session,
        depth=resolved_depth,
        regeneration_key=resolved_regeneration,
        raw_message=raw_message,
        character_autonomy=resolved_autonomy,
        prompt_handling=resolved_prompt_handling,
        reasoning_effort=resolved_reasoning_effort,
    )


def select_candidate_cast(
    raw_message: str,
    world: HanezawaHumanTestWorld,
    *,
    branch_id: TypedId | None = None,
) -> tuple[tuple[TypedId, ...], tuple[TypedId, ...]]:
    del raw_message, branch_id
    # The Reasoner, not this lexical helper, owns actual participant selection.
    # All household members are scene-reachable candidates; selected Composer
    # context remains strictly limited to the Reasoner's validated cast.
    candidates = tuple(CHARACTER_IDS.values())
    return (
        (world.protected_user_id, *candidates),
        candidates,
    )


def _initial_scenario_aware_cast(
    raw_message: str,
    world: HanezawaHumanTestWorld,
    *,
    branch_id: TypedId,
) -> tuple[TypedId, ...]:
    """Privacy-filter scenario clauses without shrinking Reasoner reachability."""

    aware: set[TypedId] = set()
    if _ALL_CAST_PHRASES.search(raw_message):
        aware.update(CHARACTER_IDS.values())
    for name, character_id in CHARACTER_IDS.items():
        if re.search(rf"\b{re.escape(name)}\b", raw_message, re.IGNORECASE):
            aware.add(character_id)
    if _HANA_ALIASES.search(raw_message):
        aware.add(CHARACTER_IDS["Hana"])
    branch = world.store.get_branch(branch_id)
    if branch.head_artifact_id is None:
        aware.add(CHARACTER_IDS["Sakura"])
    else:
        aware.update(
            world.store.get_artifact(branch.head_artifact_id).responding_npc_ids
        )
    return tuple(
        character_id
        for character_id in CHARACTER_IDS.values()
        if character_id in aware
    )


def continuity_seeds(
    world: HanezawaHumanTestWorld,
    *,
    branch_id: TypedId | None = None,
    exclude_artifact_id: TypedId | None = None,
) -> tuple[RequiredSeedRecord, ...]:
    artifact_ids = tuple(
        artifact_id
        for artifact_id in world.store.visible_artifact_ids(
            branch_id or world.branch_id
        )
        if artifact_id != exclude_artifact_id
    )
    events = tuple(
        RequiredSeedRecord(
            record_id=deterministic_id(
                IdKind.EVENT,
                "cera.accepted_turn_event.v1",
                str(artifact_id),
            ),
            sections=("event", "source_coverage", "artifact_binding"),
            reason="Authorize validated branch-local continuity from an accepted turn.",
        )
        for artifact_id in artifact_ids[-_MAX_CONTINUITY_EVENTS:]
    )
    replies = tuple(
        RequiredSeedRecord(
            record_id=deterministic_id(
                IdKind.MATERIAL,
                "cera.accepted_reply_material.v1",
                str(artifact_id),
            ),
            sections=("accepted_reply", "artifact_binding"),
            reason=(
                "Authorize exact presentation continuity from a recently "
                "accepted immutable reply. Do not reinterpret its creative "
                "wording as independent objective event truth."
            ),
        )
        for artifact_id in artifact_ids[-_MAX_ACCEPTED_REPLIES:]
    )
    return (*events, *replies)


def contextual_genesis_seeds(
    message: str,
    world: HanezawaHumanTestWorld,
) -> tuple[RequiredSeedRecord, ...]:
    """Deterministically prefetch factual records for recognized scene domains."""

    if _HOUSEHOLD_FACT_CUE.search(message) is None:
        return ()
    return tuple(
        RequiredSeedRecord(
            record_id=record_id,
            sections=("claim", "source_text"),
            reason=(
                "Authorize concrete public household rules before planning or "
                "realizing an orientation, shared-space, schedule, or chore scene."
            ),
        )
        for record_id in world.public_household_rule_record_ids
    )


def verifier_continuity_context(
    world: HanezawaHumanTestWorld,
    *,
    branch_id: TypedId | None = None,
    exclude_artifact_id: TypedId | None = None,
) -> tuple[str, ...]:
    """Expose bounded accepted prose as verifier-only continuity authority.

    The independent verifier cannot infer prior branch facts from receipt IDs.
    Exact accepted replies therefore accompany the request as presentation
    continuity, while the label prevents creative narration from being
    reclassified wholesale as objective event truth.
    """

    artifact_ids = tuple(
        artifact_id
        for artifact_id in world.store.visible_artifact_ids(
            branch_id or world.branch_id
        )
        if artifact_id != exclude_artifact_id
    )
    return tuple(
        (
            "Accepted prior reply (immutable presentation continuity; it may "
            "authorize unchanged location, objects, dialogue, and visible state, "
            "but not every interpretive phrase as objective fact):\n"
            + world.store.get_artifact(artifact_id).accepted_prose
        )
        for artifact_id in artifact_ids[-_MAX_ACCEPTED_REPLIES:]
    )


def resolve_session_branch(
    request: SillyTavernChatRequest,
    controls: ParsedCeraControl,
    world: HanezawaHumanTestWorld,
) -> TypedId:
    """Bind one SillyTavern chat to an isolated CERA story branch.

    New chat identities receive deterministic root branches. The original
    human-test chat may continue on the historical ``branch:main`` seam when
    its transcript contains that branch's exact accepted head. This also
    recovers safely from a client session-key format change without merging
    unrelated chats.
    """

    store = world.store
    candidate = deterministic_id(
        IdKind.BRANCH,
        "cera.sillytavern_session_branch.v1",
        f"{world.world_id}|{controls.session_key}",
    )
    try:
        existing = store.get_branch(candidate)
    except TransactionError:
        existing = None
    if existing is not None:
        if existing.world_id != world.world_id or existing.status != "active":
            raise ValueError("CERA session branch is unavailable")
        return existing.branch_id

    assistant_messages = {
        message.content
        for message in request.messages
        if message.role == "assistant"
    }
    transcript_matches = []
    for branch in store.branches_for_world(world.world_id):
        if branch.status != "active" or branch.head_artifact_id is None:
            continue
        accepted = store.get_artifact(branch.head_artifact_id).accepted_prose
        if accepted in assistant_messages:
            transcript_matches.append(branch.branch_id)
    if len(transcript_matches) > 1:
        raise ValueError("CERA transcript matches multiple story branches")
    if transcript_matches:
        return transcript_matches[0]

    original = store.get_branch(world.branch_id)
    if original.head_artifact_id is None and original.status == "active":
        return original.branch_id

    try:
        return store.create_root_branch(world.world_id, candidate).branch_id
    except TransactionError:
        raced = store.get_branch(candidate)
        if raced.world_id != world.world_id or raced.status != "active":
            raise ValueError("CERA session branch is unavailable")
        return raced.branch_id
