"""CERA-native SillyTavern adapter over the qualified ordinary runtime path."""

from __future__ import annotations

from dataclasses import dataclass
import re
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Lock
from typing import Protocol

from cera.composer import (
    ArtifactPublicationMode,
    ComposerContextAssembler,
    ComposerCoordinator,
    DeepSeekSceneComposerPort,
)
from cera.contracts import SceneDepthMode
from cera.evidence import EvidenceService
from cera.genesis.hanezawa_builder import CHARACTER_IDS
from cera.ids import IdKind, TypedId, deterministic_id
from cera.errors import TransactionError
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

from .models import SillyTavernChatRequest, SillyTavernTurnReply


_SESSION_RE = re.compile(r"(?m)^\[\[CERA_SESSION:([0-9a-f]{64})\]\]\s*$")
_DEPTH_RE = re.compile(r"(?m)^\[\[CERA_DEPTH:(OFF|AUTO|LONG|EPIC)\]\]\s*$")
_REGEN_RE = re.compile(
    r"(?m)^\[\[CERA_REGENERATE:([a-z][a-z0-9_-]{0,95})\]\]\s*$"
)
_CONTROL_RE = re.compile(
    r"(?m)^\[\[CERA_(?:SESSION:[0-9a-f]{64}|DEPTH:(?:OFF|AUTO|LONG|EPIC)|"
    r"REGENERATE:[a-z][a-z0-9_-]{0,95})\]\]\s*(?:\r?\n)?"
)
_ALL_CAST_PHRASES = re.compile(
    r"\b(?:everyone|the whole family|all (?:the )?girls|the sisters)\b",
    re.IGNORECASE,
)
_HANA_ALIASES = re.compile(r"\b(?:mom|mother|hana)\b", re.IGNORECASE)
_MAX_CONTINUITY_EVENTS = 12
_MAX_ACCEPTED_REPLIES = 4


@dataclass(frozen=True, slots=True)
class ParsedCeraControl:
    session_key: str
    depth: str
    regeneration_key: str | None
    raw_message: str


class SillyTavernTurnExecutor(Protocol):
    def execute(
        self,
        prepared,
        *,
        workspace_root: Path,
    ) -> SillyTavernTurnReply: ...


class LiveSillyTavernTurnExecutor:
    """One-call-per-stage live executor; no retry and no fallback."""

    def __init__(
        self,
        world: HanezawaHumanTestWorld,
        *,
        rejected_candidate_review_port=None,
    ) -> None:
        self.world = world
        self.rejected_candidate_review_port = rejected_candidate_review_port
        self.verifier_runner = CodexExecRunner()

    def execute(
        self,
        prepared,
        *,
        workspace_root: Path,
    ) -> SillyTavernTurnReply:
        store = self.world.store
        service = EvidenceService(store)
        reasoner_port = CodexSceneReasonerPort(
            CodexSDKTransport(
                codex_reasoner_candidate(model="gpt-5.6-sol", effort="medium"),
                workspace=workspace_root / "reasoner",
            ),
            evidence_tools_enabled=True,
        )
        verifier_port = CodexSceneRealizationVerifierPort(
            CodexStructuredOutputTransport(
                codex_cli_realization_verifier_candidate(
                    model="gpt-5.6-sol",
                    effort="medium",
                ),
                workspace=workspace_root / "verifier",
                runner=self.verifier_runner,
            )
        )
        pipeline = LiveShapedTurnPipeline(
            ReasonerCoordinator(service, TurnKernel(service)),
            ComposerContextAssembler(service),
            ComposerCoordinator(),
            realization_verification_coordinator=(
                SceneRealizationVerificationCoordinator(
                    self.rejected_candidate_review_port
                )
            ),
            realization_verifier_port=verifier_port,
            stage_audit_journal=TurnStageAuditJournal(store),
        )
        composer_port = DeepSeekSceneComposerPort(
            DeepSeekChatTransport(deepseek_composer_candidate())
        )
        application = LocalOrdinaryApplication(
            store=store,
            pipeline=pipeline,
            reasoner_port=reasoner_port,
            composer_port=composer_port,
        )
        result = application.execute(prepared.application_request)
        return SillyTavernTurnReply(
            prose=result.accepted_artifact.accepted_prose,
            request_id=str(
                prepared.reasoner_request.prepared_turn.request.request_id
            ),
            artifact_id=str(result.accepted_artifact.artifact_id),
            generation=result.commit_receipt.generation_after,
            provider_calls=result.receipt.adapter_call_count,
            exact_replay=result.receipt.exact_replay,
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

    def complete(self, request: SillyTavernChatRequest) -> SillyTavernTurnReply:
        controls = parse_cera_controls(
            request.latest_user_content,
            session_key=request.cera_session_id,
            depth=request.cera_scene_depth,
            regeneration_key=request.cera_regeneration_key,
        )
        with self._turn_lock:
            return self._complete_locked(request, controls)

    def _complete_locked(
        self,
        request: SillyTavernChatRequest,
        controls: ParsedCeraControl,
    ) -> SillyTavernTurnReply:
        branch_id = resolve_session_branch(request, controls, self.world)
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
            "auto": (
                "Adaptive CERA sequence depth: classify the reply as atomic, "
                "developed ordinary, or domino/multi-scene. An atomic exchange may "
                "use one or two beats; an ordinary interaction commonly uses two "
                "to four; a supported domino progression commonly uses four to six "
                "or more. These are advisory ranges, never quotas. Develop all "
                "supported NPC-controlled consequences, then stop at the earliest "
                "natural Ted handoff without padding or inventing his next action."
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
            self.world.initial_scenario_projection(eligible)
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
                    "Python exposed only the current-head and explicitly named "
                    "candidate cast; runtime Codex must select the actual present "
                    "responders justified by the cue and continuity."
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
            additional_seed_records=continuity,
            publication_mode=publication_mode,
            response_profile_version=f"cera-sillytavern-{controls.depth}-v4",
            scene_depth_mode=SceneDepthMode(controls.depth),
        )
        preparer = DevelopmentOrdinaryTurnPreparer(
            store=self.world.store,
            evidence_service=self.world.service,
            world_id=self.world.world_id,
            genesis_revision_id=self.world.revision_id,
            protected_user_id=self.world.protected_user_id,
            access_scope=self.world.system_scope,
            relationship_record_ids=self.world.relationship_record_ids,
        )
        prepared = preparer.prepare(spec)
        with TemporaryDirectory(prefix="cera-sillytavern-turn-") as temporary:
            workspace = Path(temporary)
            (workspace / "reasoner").mkdir()
            (workspace / "verifier").mkdir()
            return self.executor.execute(prepared, workspace_root=workspace)


def parse_cera_controls(
    content: str,
    *,
    session_key: str | None = None,
    depth: str | None = None,
    regeneration_key: str | None = None,
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
    )


def select_candidate_cast(
    raw_message: str,
    world: HanezawaHumanTestWorld,
    *,
    branch_id: TypedId | None = None,
) -> tuple[tuple[TypedId, ...], tuple[TypedId, ...]]:
    explicit: set[TypedId] = set()
    if _ALL_CAST_PHRASES.search(raw_message):
        explicit.update(CHARACTER_IDS.values())
    for name, character_id in CHARACTER_IDS.items():
        if re.search(rf"\b{re.escape(name)}\b", raw_message, re.IGNORECASE):
            explicit.add(character_id)
    if _HANA_ALIASES.search(raw_message):
        explicit.add(CHARACTER_IDS["Hana"])

    branch = world.store.get_branch(branch_id or world.branch_id)
    current: set[TypedId] = set()
    if branch.head_artifact_id is not None:
        current.update(
            world.store.get_artifact(branch.head_artifact_id).responding_npc_ids
        )
    else:
        current.add(CHARACTER_IDS["Sakura"])
    candidates = tuple(
        character_id
        for character_id in CHARACTER_IDS.values()
        if character_id in explicit or character_id in current
    )
    if not candidates:
        candidates = (CHARACTER_IDS["Sakura"],)
    return (
        (world.protected_user_id, *candidates),
        candidates,
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
