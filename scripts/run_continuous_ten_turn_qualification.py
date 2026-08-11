"""Run one fresh, committed, disposable ten-turn live CERA qualification.

The route is noncanonical and auto-deleted.  Every turn begins at raw ingress,
uses the active v2 application request, invokes Sol-medium Reasoner, DeepSeek
V4 Flash Composer, and Sol-medium realization verification exactly once, then
commits atomically.  There is no retry or fallback inside this runner.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
from typing import Any

from cera.composer import (
    ArtifactPublicationMode,
    ComposerContextAssembler,
    ComposerCoordinator,
    DeepSeekSceneComposerPort,
)
from cera.composer.deepseek import (
    DEEPSEEK_COMPOSER_PROMPT_VERSION,
    DeepSeekCompositionDraftV6,
)
from cera.evaluation import RealGenesisSandbox
from cera.evidence import EvidenceService
from cera.genesis.hanezawa_builder import CHARACTER_IDS
from cera.genesis.models import GenesisRecordType
from cera.ids import IdKind, TypedId, deterministic_id
from cera.kernel import TurnKernel
from cera.providers import (
    ACTIVE_CODEX_CLI_VERIFIER_SUMMARY_SHA256,
    CodexCliVerifierQualificationEvidence,
    CodexExecRunner,
    CodexSDKTransport,
    CodexStructuredOutputTransport,
    CodexTransportRunner,
    DeepSeekChatTransport,
    PersistentCodexCompletionRegistrationQualificationEvidence,
    PersistentCodexQualificationEvidence,
    PersistentCodexTreeCleanupQualificationEvidence,
    PersistentNoMcpCodexRunner,
    ProviderSchemaDialect,
    RelationalBoundaryQualificationEvidence,
    codex_cli_realization_verifier_candidate,
    codex_realization_verifier_candidate,
    codex_reasoner_candidate,
    deepseek_composer_candidate,
    load_codex_cli_verifier_qualification,
    load_persistent_codex_completion_registration_qualification,
    load_persistent_codex_qualification,
    load_persistent_codex_tree_cleanup_qualification,
    load_relational_boundary_qualification,
    project_provider_output_schema,
)
from cera.reasoner import CodexSceneReasonerPort, ReasonerCoordinator
from cera.realization import (
    CodexSceneRealizationVerifierPort,
    RejectedCandidateReviewArtifact,
    SceneRealizationVerificationCoordinator,
    codex_realization_verifier_draft_json_schema,
)
from cera.runtime import (
    DevelopmentOrdinaryTurnPreparer,
    DevelopmentTurnSpec,
    LocalOrdinaryApplication,
    LiveShapedTurnPipeline,
    RequiredSeedRecord,
    TurnStageAuditJournal,
)
from cera.serialization import canonical_json, text_sha256, to_primitive
from cera.storage import SQLiteAuthorityStore
from cera.providers.codex_sdk_compat import (
    CODEX_SDK_COMPATIBILITY_ID,
    CODEX_SDK_COMPATIBILITY_SOURCE_SHA256,
    SUPPORTED_SDK_VERSION,
)

from audit_continuous_live_call_budget import (
    DEFAULT_DEEPSEEK_LIMIT,
    DEFAULT_SOL_LIMIT,
    audit as audit_live_call_budget,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = (
    ROOT
    / "evaluation"
    / "evidence"
    / "continuous_ten_turn_qualification_2026-07-30_v21_behavioral"
)
CODEX_CLI_VERIFIER_PROBE_SUMMARY = (
    ROOT
    / "evaluation"
    / "evidence"
    / "codex_cli_verifier_probe_2026-07-29_v1"
    / "summary.json"
)
PERSISTENT_PROBE_SUMMARY = (
    ROOT
    / "evaluation"
    / "evidence"
    / "persistent_codex_transport_probe_2026-07-29_v1"
    / "summary.json"
)
PERSISTENT_PROBE_SUMMARY_SHA256 = (
    "f92a08e5cda04c701c74b3d1ddd98fe1abc0da733959f56545c0eb43cd07f5f3"
)
PERSISTENT_TREE_CLEANUP_PROBE_SUMMARY = (
    ROOT
    / "evaluation"
    / "evidence"
    / "persistent_codex_transport_probe_2026-07-29_v3"
    / "summary.json"
)
PERSISTENT_TREE_CLEANUP_PROBE_SUMMARY_SHA256 = (
    "d477cade6580dcc3d5df9e445047f57a8c9b74e6f24bb21b2a109afb0d4124d8"
)
PERSISTENT_COMPLETION_REGISTRATION_PROBE_SUMMARY = (
    ROOT
    / "evaluation"
    / "evidence"
    / "persistent_codex_transport_probe_2026-07-29_v5"
    / "summary.json"
)
PERSISTENT_COMPLETION_REGISTRATION_PROBE_SUMMARY_SHA256 = (
    "1e5f7f09f0ed6b29211fe40c8ac998e6344103b84fe1ef0f4bd0b496c40c56f1"
)
RELATIONAL_BOUNDARY_PROBE_SUMMARY = (
    ROOT
    / "evaluation"
    / "evidence"
    / "deepseek_relational_boundary_probe_2026-07-29_v4"
    / "summary.json"
)
RELATIONAL_BOUNDARY_PROBE_SUMMARY_SHA256 = (
    "d18e1397eafe1e845e2722100268a7fa0cc7b514be30d710a02d76637981940c"
)


def _sdk_compatibility_metadata() -> dict[str, str]:
    return {
        "compatibility_id": CODEX_SDK_COMPATIBILITY_ID,
        "sdk_version": SUPPORTED_SDK_VERSION,
        "route_notification_source_sha256": (
            CODEX_SDK_COMPATIBILITY_SOURCE_SHA256
        ),
        "activation_contract": (
            "provider_receipt_requires_successful_worker_installation"
        ),
    }


@dataclass(frozen=True, slots=True)
class RouteTurn:
    index: int
    key: str
    title: str
    present: tuple[str, ...]
    eligible: tuple[str, ...]
    message: str
    anchors: tuple[str, ...]
    prior_event_turns: tuple[int, ...] = ()
    required_memory_id: str | None = None
    regeneration: bool = False
    require_all_eligible: bool = False


ROUTE = (
    RouteTurn(
        1,
        "sakura-doorway-shoe-and-entry-boundary",
        "Sakura defines the immediate shoe and entry boundary",
        ("Sakura",),
        ("Sakura",),
        (
            "Ted arrives at the Hanezawa doorway with one suitcase, knocks, and "
            "remains outside when Sakura answers. He says, \"I'm Ted. Before I "
            "step in, please tell me where guests leave their shoes and whether "
            "you want me to keep the suitcase out here until someone chooses a "
            "place for it.\" He does not move past her and waits."
        ),
        (
            "This begins from the exact V1.2 doorway state.",
            "Only Sakura and Ted directly hear this first exchange.",
            "Sakura owns formal first contact and the immediate entry instruction; no absent resident may answer from unheard dialogue.",
        ),
    ),
    RouteTurn(
        2,
        "tomi-post-run-shared-space-boundary",
        "Tomi defines an ordinary post-run shared-space boundary",
        ("Tomi",),
        ("Tomi",),
        (
            "One week after moving in, Ted finds Tomi stretching on the back "
            "steps after a morning run. He stays clear of her reach and says, "
            "\"When you come back from training, would you rather I leave this "
            "space quiet, give a brief greeting, or simply ask each time? I "
            "don't want being housemates to turn into me monitoring you.\" He waits."
        ),
        (
            "Only Ted and Tomi are part of this exchange.",
            "Tomi owns her training routine, bodily privacy, and whether any interaction is welcome.",
            "A week of ordinary contact establishes no intimacy, coaching role, medical authority, or romance.",
        ),
        (1,),
    ),
    RouteTurn(
        3,
        "tomi-indirect-running-joy-memory",
        "Tomi answers a paraphrased indirect cue to T-M03",
        ("Tomi",),
        ("Tomi",),
        (
            "Later, while Ted and Tomi are alone near the shoe rack, he says, "
            "\"Before times, rankings, and other people expecting results, did "
            "you ever have a moment when moving fast simply felt like it belonged "
            "to you? You do not have to give me the private history behind it.\" "
            "He waits."
        ),
        (
            "Ted's wording is a paraphrased indirect memory cue, not an exact Genesis title.",
            "Investigate through bounded evidence tools and fetch exact advertised sections before relying on a relevant owner-private memory.",
            "Tomi owns whether she discloses any personal history; the question alone proves nothing about her past.",
        ),
        (2,),
        required_memory_id="T-M03",
    ),
    RouteTurn(
        4,
        "tomi-yuuni-cooldown-music-boundaries",
        "Tomi and Yuuni keep training choice separate from a music offer",
        ("Tomi", "Yuuni"),
        ("Tomi", "Yuuni"),
        (
            "With Tomi and Yuuni together, Ted asks Tomi whether music during "
            "cooldown helps, distracts, or depends on the day. He then asks Yuuni "
            "only whether she would be willing to offer one public playlist if "
            "Tomi asks for it. He requests Tomi's answer first and Yuuni's second."
        ),
        (
            "Only Ted, Tomi, and Yuuni are present.",
            "Tomi owns her training environment; Yuuni owns whether she offers music but cannot volunteer Tomi's preference.",
            "The source explicitly requests one bounded response from every eligible NPC in the stated order.",
        ),
        (2, 3),
        require_all_eligible=True,
    ),
    RouteTurn(
        5,
        "tomi-bounded-stopwatch-offer",
        "Tomi receives a bounded practical stopwatch offer",
        ("Tomi",),
        ("Tomi",),
        (
            "The next morning, Ted speaks with Tomi in the common room and says, "
            "\"If you ever want someone to hold a stopwatch for one easy recovery "
            "loop, I can do that once and follow your instructions. I am not "
            "offering coaching, deciding whether you should train, or assuming "
            "the offer stays open. Would that ever be useful, or would you rather "
            "I stay out of training entirely?\" He waits."
        ),
        (
            "Ted and Tomi have known one another for only one week.",
            "The offer is practical, singular, and revocable; it creates no authority, obligation, intimacy, or outcome before Tomi answers.",
            "Tomi may show a beat-local embodied or emotional reaction supported by the cue, but the reaction must not predetermine her choice.",
        ),
        (4,),
    ),
    RouteTurn(
        6,
        "tomi-aoi-training-data-and-hallway-boundaries",
        "Tomi and Aoi separate training data from household logistics",
        ("Tomi", "Aoi"),
        ("Tomi", "Aoi"),
        (
            "After a process restart, Ted speaks with Tomi and Aoi. He says he "
            "cannot judge Tomi's training and will not report her earlier answer "
            "for her. He asks Tomi what timing data, if any, she would actually "
            "want from a helper. He then asks Aoi only whether the shared hallway "
            "or back-door timing needs neutral coordination. He requests Tomi's "
            "answer first and Aoi's second."
        ),
        (
            "This follows the accepted practical-offer exchange after a process restart.",
            "Tomi owns her answer, training data, and disclosure; Aoi owns only neutral shared-space logistics.",
            "The source explicitly requests one bounded response from every eligible NPC in the stated order.",
        ),
        (5,),
        require_all_eligible=True,
    ),
    RouteTurn(
        7,
        "tomi-mia-yuuni-frustration-support-round",
        "Tomi, Mia, and Yuuni each own a separate response to frustration",
        ("Tomi", "Mia", "Yuuni"),
        ("Tomi", "Mia", "Yuuni"),
        (
            "After Tomi returns visibly frustrated from missing a personal target, "
            "Ted speaks while Tomi, Mia, and Yuuni are present. He says he will "
            "not label the frustration as injury or failure. He asks Tomi whether "
            "she wants company, space, or one practical action. He asks Mia only "
            "what food or water she would offer if Tomi requests it, and Yuuni "
            "only what music she would offer if Tomi requests it. He asks Tomi, "
            "then Mia, then Yuuni."
        ),
        (
            "Ted, Tomi, Mia, and Yuuni are together for this exchange.",
            "The source explicitly requests one bounded response from every eligible NPC in the stated order.",
            "Visible frustration is not proof of injury, private thought, or a request; neither sister may answer for Tomi or turn care into pressure.",
        ),
        (5, 6),
        require_all_eligible=True,
    ),
    RouteTurn(
        8,
        "tomi-current-support-choice",
        "Tomi chooses whether any previously named support is wanted now",
        ("Tomi",),
        ("Tomi",),
        (
            "Later, Ted speaks with Tomi alone. He says, \"I do not assume any "
            "earlier offer is still active. Of the options people named—company, "
            "space, water or food, music, or a practical task—which, if any, do "
            "you want today? None is a complete answer.\" He waits."
        ),
        (
            "This follows the accepted three-person support round.",
            "Only Ted and Tomi are present; Tomi's current choice does not permanently authorize future support.",
            "The listed options are offers, not evidence that Tomi wants or needs any of them.",
        ),
        (7,),
    ),
    RouteTurn(
        9,
        "regenerate-tomi-low-demand-support",
        "Regenerate the support beat with less decision work imposed on Tomi",
        ("Tomi",),
        ("Tomi",),
        (
            "Instead of asking Tomi to choose among a list, Ted later speaks with "
            "her alone and says, \"I realized that offering five options can still "
            "make you manage everyone else's concern. I will step back. You do "
            "not need to answer or reassure me; if you want one specific thing, "
            "you can name it in your own time.\" He leaves the conversational "
            "floor to Tomi without moving closer."
        ),
        (
            "This regenerates the same post-frustration beat from the prior accepted parent.",
            "The replaced reply is not evidence for this alternative realization.",
            "Only Tomi is present; Tomi may answer, decline to answer, redirect, or set a different boundary.",
        ),
        (7,),
        regeneration=True,
    ),
    RouteTurn(
        10,
        "tomi-hana-training-time-household-boundary",
        "Tomi and Hana keep private support separate from household routine",
        ("Tomi", "Hana"),
        ("Tomi", "Hana"),
        (
            "After another process restart, Ted speaks with Tomi and Hana. He "
            "says, \"I will not repeat Tomi's private answer or turn one difficult "
            "training day into a family report.\" He asks Tomi whether she wants "
            "any general training-time household boundary stated. He then asks "
            "Hana only whether a neutral shared-space rule should apply equally "
            "to anyone returning from exercise. He requests Tomi's answer first "
            "and Hana's second."
        ),
        (
            "This follows the current regenerated support branch after restart.",
            "Tomi owns her private answer, training state, and disclosure; Hana owns only an equal household routine.",
            "The source explicitly requests one bounded response from every eligible NPC in the stated order.",
        ),
        (7, 9),
        require_all_eligible=True,
    ),
)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


class LocalRejectedCandidateStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def retain(self, artifact: RejectedCandidateReviewArtifact) -> None:
        if (
            not artifact.qualification_only
            or artifact.access_scope != "creator_local_qualification_review"
            or not artifact.runtime_receipt_forbidden
            or artifact.story_state_committed
            or artifact.authoritative_store_writes != 0
        ):
            raise RuntimeError("rejected candidate escaped qualification authority")
        target = self.root / f"{artifact.review_sha256}.json"
        if target.exists():
            raise FileExistsError("refusing to overwrite rejected candidate evidence")
        write_json(target, to_primitive(artifact))


def relationship_ids(sandbox: RealGenesisSandbox) -> dict[TypedId, TypedId]:
    return {
        character_id: next(
            record.record_id
            for record in sandbox.compiled.records
            if record.record_type is GenesisRecordType.RELATIONSHIP_EDGE
            and record.relationship_from_id == character_id
            and record.relationship_to_id == sandbox.protected_user_id()
        )
        for character_id in CHARACTER_IDS.values()
    }


def event_record_id(artifact_id: TypedId) -> TypedId:
    return deterministic_id(
        IdKind.EVENT,
        "cera.accepted_turn_event.v1",
        str(artifact_id),
    )


def build_application(
    *,
    store: SQLiteAuthorityStore,
    service: EvidenceService,
    evidence_tools_enabled: bool,
    persistent_reasoner_runner: CodexTransportRunner,
    verifier_runner: CodexTransportRunner,
    workspace_root: Path,
    output_dir: Path,
) -> LocalOrdinaryApplication:
    reasoner_port = CodexSceneReasonerPort(
        CodexSDKTransport(
            codex_reasoner_candidate(model="gpt-5.6-sol", effort="medium"),
            workspace=workspace_root / "reasoner",
            runner=(
                None if evidence_tools_enabled else persistent_reasoner_runner
            ),
        ),
        evidence_tools_enabled=evidence_tools_enabled,
    )
    verifier_port = CodexSceneRealizationVerifierPort(
        CodexStructuredOutputTransport(
            codex_cli_realization_verifier_candidate(
                model="gpt-5.6-sol",
                effort="medium",
            ),
            workspace=workspace_root / "verifier",
            runner=verifier_runner,
        )
    )
    pipeline = LiveShapedTurnPipeline(
        ReasonerCoordinator(service, TurnKernel(service)),
        ComposerContextAssembler(service),
        ComposerCoordinator(),
        realization_verification_coordinator=SceneRealizationVerificationCoordinator(
            LocalRejectedCandidateStore(output_dir / "rejected_candidate_reviews")
        ),
        realization_verifier_port=verifier_port,
        stage_audit_journal=TurnStageAuditJournal(store),
    )
    composer_port = DeepSeekSceneComposerPort(
        DeepSeekChatTransport(
            # This qualification is for the creator-selected implementation
            # route. Historical Pro evidence remains a prerequisite only; it
            # must not silently select the live Composer.
            deepseek_composer_candidate(model="deepseek-v4-flash")
        )
    )
    return LocalOrdinaryApplication(
        store=store,
        pipeline=pipeline,
        reasoner_port=reasoner_port,
        composer_port=composer_port,
    )


def persistent_session_state(
    reasoner_runner: PersistentNoMcpCodexRunner,
    verifier_runner: CodexExecRunner,
) -> dict[str, dict[str, int]]:
    return {
        "scene_reasoner": {
            "process_launch_count": reasoner_runner.process_launch_count,
            "request_submission_count": reasoner_runner.request_submission_count,
        },
        "scene_realization_verifier": {
            "process_launch_count": verifier_runner.process_launch_count,
            "request_submission_count": verifier_runner.request_submission_count,
        },
    }


def safe_failure(exc: Exception, store, request_id) -> dict[str, Any]:
    envelope = getattr(exc, "envelope", None)
    stage_journal = (
        to_primitive(store.turn_stage_entries(request_id))
        if store is not None and request_id is not None
        else []
    )
    stored_bundles = (
        to_primitive(store.turn_failure_evidence_bundles(request_id))
        if store is not None and request_id is not None
        else []
    )
    return {
        "error": (
            to_primitive(envelope)
            if envelope is not None
            else {"class": type(exc).__name__, "message": str(exc)}
        ),
        "failure_evidence_bundle": to_primitive(
            getattr(exc, "failure_evidence_bundle", None)
        ),
        "retained_evidence_handles": to_primitive(
            getattr(exc, "retained_evidence_handles", ())
        ),
        "privacy_safe_receipt_payloads": to_primitive(
            getattr(exc, "privacy_safe_receipt_payloads", ())
        ),
        "provider_call_receipt": to_primitive(
            getattr(exc, "provider_call_receipt", None)
        ),
        "mcp_bridge_receipt": to_primitive(
            getattr(exc, "mcp_bridge_receipt", None)
        ),
        "safe_diagnostics": list(
            getattr(exc, "safe_diagnostics", ())
        ),
        "stage_journal": stage_journal,
        "stored_failure_bundles": stored_bundles,
        "automatic_retry_count": 0,
        "fallback_enabled": False,
        "story_state_committed": False,
        "retains_prompt": False,
        "retains_secret": False,
    }


def record_terminal_failure(
    *,
    output_dir: Path,
    summary: dict[str, Any],
    qualification_id: str,
    route: RouteTurn,
    exc: Exception,
    store,
    request_id,
    failure_stage: str,
) -> None:
    failure = {
        "schema_version": "cera.continuous_qualification_failure.v1",
        "qualification_id": qualification_id,
        "index": route.index,
        "key": route.key,
        "title": route.title,
        "status": "failed",
        "failure_stage": failure_stage,
        "raw_message_sha256": text_sha256(route.message),
        "safe_failure": safe_failure(exc, store, request_id),
    }
    write_json(
        output_dir / "turns" / f"{route.index:02d}_{route.key}_failure.json",
        failure,
    )
    summary["status"] = "failed"
    summary["failed_turn"] = route.index
    summary["failure_stage"] = failure_stage
    summary["finished_at"] = datetime.now(timezone.utc).isoformat()
    summary["story_database_retained"] = False
    write_json(output_dir / "summary.json", summary)
    print(
        "CERA_CONTINUOUS_RESULT="
        + canonical_json(
            {
                "status": "failed",
                "failed_turn": route.index,
                "failure_stage": failure_stage,
                "error_class": type(exc).__name__,
                "message": str(exc),
            }
        ),
        flush=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--confirm-live", action="store_true")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--qualification-id",
        default="continuous-ten-turn-v21-behavioral",
    )
    parser.add_argument("--sol-call-limit", type=int, default=DEFAULT_SOL_LIMIT)
    parser.add_argument(
        "--deepseek-call-limit", type=int, default=DEFAULT_DEEPSEEK_LIMIT
    )
    args = parser.parse_args()
    if not args.confirm_live:
        parser.error("live provider calls require --confirm-live")
    if len(ROUTE) != 10:
        raise RuntimeError("continuous qualification route must contain ten turns")
    budget = audit_live_call_budget(
        sol_limit=args.sol_call_limit,
        deepseek_limit=args.deepseek_call_limit,
    )
    if not budget["may_begin_next_ten_turn_route"]:
        remaining = budget["remaining"]
        raise RuntimeError(
            "live call authority is insufficient for one complete route: "
            f"remaining Sol={remaining['sol']}, DeepSeek={remaining['deepseek']}; "
            "required Sol=20, DeepSeek=10"
        )
    persistent_qualification: PersistentCodexQualificationEvidence = (
        load_persistent_codex_qualification(
            PERSISTENT_PROBE_SUMMARY.resolve(),
            expected_summary_sha256=PERSISTENT_PROBE_SUMMARY_SHA256,
            expected_model="gpt-5.6-sol",
        )
    )
    tree_cleanup_qualification: PersistentCodexTreeCleanupQualificationEvidence = (
        load_persistent_codex_tree_cleanup_qualification(
            PERSISTENT_TREE_CLEANUP_PROBE_SUMMARY.resolve(),
            expected_summary_sha256=PERSISTENT_TREE_CLEANUP_PROBE_SUMMARY_SHA256,
            expected_model="gpt-5.6-sol",
        )
    )
    completion_registration_payload = json.loads(
        PERSISTENT_COMPLETION_REGISTRATION_PROBE_SUMMARY.read_text(
            encoding="utf-8"
        )
    )
    completion_registration_qualification: (
        PersistentCodexCompletionRegistrationQualificationEvidence
    ) = load_persistent_codex_completion_registration_qualification(
        PERSISTENT_COMPLETION_REGISTRATION_PROBE_SUMMARY.resolve(),
        expected_summary_sha256=(
            PERSISTENT_COMPLETION_REGISTRATION_PROBE_SUMMARY_SHA256
        ),
        expected_model="gpt-5.6-sol",
        expected_route_sha256=completion_registration_payload["route_sha256"],
    )
    cli_verifier_payload = json.loads(
        CODEX_CLI_VERIFIER_PROBE_SUMMARY.read_text(encoding="utf-8")
    )
    cli_verifier_qualification: CodexCliVerifierQualificationEvidence = (
        load_codex_cli_verifier_qualification(
            CODEX_CLI_VERIFIER_PROBE_SUMMARY.resolve(),
            expected_summary_sha256=(
                ACTIVE_CODEX_CLI_VERIFIER_SUMMARY_SHA256
            ),
            expected_route_sha256=cli_verifier_payload["route_sha256"],
            expected_model="gpt-5.6-sol",
            expected_provider_schema_sha256=(
                cli_verifier_payload["provider_schema_sha256"]
            ),
        )
    )
    relational_boundary_qualification: RelationalBoundaryQualificationEvidence = (
        load_relational_boundary_qualification(
            RELATIONAL_BOUNDARY_PROBE_SUMMARY.resolve(),
            expected_summary_sha256=(
                RELATIONAL_BOUNDARY_PROBE_SUMMARY_SHA256
            ),
            expected_composer_prompt_version=(
                "cera.deepseek_scene_composer_prompt.v12"
            ),
            expected_composer_dto_version=(
                "cera.deepseek_composition_draft.v5"
            ),
            expected_composer_model="deepseek-v4-pro",
            expected_verifier_model="gpt-5.6-sol",
            expected_probe_schema=(
                "cera.deepseek_relational_boundary_probe.v4"
            ),
            expected_qualification_id=(
                "deepseek-relational-protected-user-boundary-v4"
            ),
        )
    )
    output_dir = args.output_dir.resolve()
    if output_dir.exists():
        raise RuntimeError(f"refusing to overwrite qualification evidence: {output_dir}")
    output_dir.mkdir(parents=True)
    started = datetime.now(timezone.utc)
    summary: dict[str, Any] = {
        "schema_version": "cera.continuous_ten_turn_qualification.v1",
        "qualification_id": args.qualification_id,
        "status": "running",
        "started_at": started.isoformat(),
        "route_turn_count": 10,
        "attempts_per_stage": 1,
        "retry_enabled": False,
        "fallback_enabled": False,
        "genesis_revision": "v1_2",
        "live_route_models": {
            "scene_reasoner": "gpt-5.6-sol",
            "scene_composer": "deepseek-v4-flash",
            "scene_realization_verifier": "gpt-5.6-sol",
        },
        "persistent_codex_transport": to_primitive(persistent_qualification),
        "persistent_codex_tree_cleanup": to_primitive(
            tree_cleanup_qualification
        ),
        "persistent_codex_completion_registration": to_primitive(
            completion_registration_qualification
        ),
        "codex_cli_verifier_qualification": to_primitive(
            cli_verifier_qualification
        ),
        "relational_protected_user_boundary": to_primitive(
            relational_boundary_qualification
        ),
        "codex_sdk_compatibility": _sdk_compatibility_metadata(),
        "reasoner_transport_policy": (
            "persistent_no_mcp_except_request_bound_mcp_one_shot"
        ),
        "verifier_transport_policy": "codex_cli_exec_one_shot",
        "turns": [],
    }
    write_json(output_dir / "summary.json", summary)

    event_ids: dict[int, TypedId] = {}
    main_visible: tuple[TypedId, ...] = ()
    fork_id: TypedId | None = None
    fork_visible: tuple[TypedId, ...] = ()
    store: SQLiteAuthorityStore | None = None
    service: EvidenceService | None = None
    verifier_runner = CodexExecRunner()
    with (
        RealGenesisSandbox.create(ROOT, revision="v1_2") as sandbox,
        tempfile.TemporaryDirectory(prefix="cera-continuous-ten-turn-") as work,
        PersistentNoMcpCodexRunner() as persistent_reasoner_runner,
    ):
        store = sandbox.store
        service = sandbox.service
        relationships = relationship_ids(sandbox)
        required_memory_records = {
            memory_id: sandbox.record_with_payload_value("memory_id", memory_id)
            for memory_id in {
                route.required_memory_id
                for route in ROUTE
                if route.required_memory_id is not None
            }
        }
        for route in ROUTE:
            # Explicit restart probes rebuild all store/service/runtime objects
            # from the same disposable SQLite file before continuing.
            if route.index in {6, 10}:
                store = SQLiteAuthorityStore(sandbox.database_path)
                service = EvidenceService(store)
                if store.integrity_check() != ("ok",) or store.foreign_key_check():
                    raise RuntimeError("restart integrity probe failed")
            if route.index == 7:
                fork_id = TypedId(IdKind.BRANCH, "qualification-fork-after-turn-6")
                store.fork_branch(sandbox.branch_id, fork_id)
                fork_visible = store.visible_artifact_ids(fork_id)
                if len(fork_visible) != 6:
                    raise RuntimeError("fork did not preserve the first six artifacts")

            extra = tuple(
                RequiredSeedRecord(
                    record_id=event_ids[index],
                    sections=("event", "source_coverage", "artifact_binding"),
                    reason=f"Authorize accepted branch continuity from turn {index}.",
                )
                for index in route.prior_event_turns
            )
            spec = DevelopmentTurnSpec(
                schema_version=DevelopmentTurnSpec.SCHEMA_VERSION,
                turn_key=f"{args.qualification_id}-{route.index:02d}-{route.key}",
                raw_message=route.message,
                session_id=TypedId(IdKind.SESSION, args.qualification_id),
                branch_id=sandbox.branch_id,
                present_character_ids=(
                    sandbox.protected_user_id(),
                    *(CHARACTER_IDS[name] for name in route.present),
                ),
                eligible_responder_ids=tuple(
                    CHARACTER_IDS[name] for name in route.eligible
                ),
                scene_anchors=route.anchors,
                additional_seed_records=extra,
                publication_mode=(
                    ArtifactPublicationMode.REGENERATE
                    if route.regeneration
                    else ArtifactPublicationMode.APPEND
                ),
                response_profile_version="cera-continuous-live-ordinary-v3",
            )
            preparer = DevelopmentOrdinaryTurnPreparer(
                store=store,
                evidence_service=service,
                world_id=sandbox.world_id,
                genesis_revision_id=sandbox.revision_id,
                protected_user_id=sandbox.protected_user_id(),
                access_scope=sandbox.system_scope(),
                relationship_record_ids=relationships,
            )
            branch = store.get_branch(spec.branch_id)
            request_id = deterministic_id(
                IdKind.REQUEST,
                "cera.development.raw_turn.v1",
                f"{sandbox.world_id}|{spec.branch_id}|{branch.generation}|{spec.turn_key}",
            )
            try:
                prepared = preparer.prepare(spec)
            except Exception as exc:
                summary["persistent_transport_sessions"] = persistent_session_state(
                    persistent_reasoner_runner,
                    verifier_runner,
                )
                record_terminal_failure(
                    output_dir=output_dir,
                    summary=summary,
                    qualification_id=args.qualification_id,
                    route=route,
                    exc=exc,
                    store=store,
                    request_id=request_id,
                    failure_stage="development_turn_preparation",
                )
                return 1
            if prepared.reasoner_request.prepared_turn.request.request_id != request_id:
                raise RuntimeError("development preparer changed the predicted request identity")
            turn_started = datetime.now(timezone.utc)
            app_workspace = Path(work) / f"turn-{route.index:02d}"
            (app_workspace / "reasoner").mkdir(parents=True)
            (app_workspace / "verifier").mkdir(parents=True)
            application = build_application(
                store=store,
                service=service,
                evidence_tools_enabled=route.required_memory_id is not None,
                persistent_reasoner_runner=persistent_reasoner_runner,
                verifier_runner=verifier_runner,
                workspace_root=app_workspace,
                output_dir=output_dir,
            )
            try:
                result = application.execute(prepared.application_request)
                shaped = result.live_shaped_result
                if shaped is None:
                    raise RuntimeError("fresh qualification turn returned replay evidence")
                if (
                    shaped.reasoner.provider_call_receipt.requested_model
                    != "gpt-5.6-sol"
                    or shaped.composer.provider_call_receipt.requested_model
                    != "deepseek-v4-flash"
                    or shaped.realization_verification.provider_call_receipt.requested_model
                    != "gpt-5.6-sol"
                ):
                    raise RuntimeError(
                        "fresh qualification used a model outside its pinned live route"
                    )
                if result.receipt.adapter_call_count != 3:
                    raise RuntimeError("fresh turn did not make exactly three provider calls")
                if result.operational_report.failed_count != 0:
                    raise RuntimeError("post-publication work unexpectedly failed")
                selected = shaped.reasoner.outcome.decision.responding_npc_ids
                eligible = set(spec.eligible_responder_ids)
                if not selected or not set(selected).issubset(eligible):
                    raise RuntimeError("Reasoner selected an ineligible participant")
                context_characters = {
                    character_id
                    for block in shaped.context.request.realization_context.blocks
                    for character_id in block.applicable_character_ids
                }
                if not set(selected).issubset(context_characters):
                    raise RuntimeError("selected participant lacks Composer context")
                absent = set(CHARACTER_IDS.values()) - set(spec.present_character_ids)
                if context_characters.intersection(absent):
                    raise RuntimeError("absent character leaked into Composer context")
                if route.require_all_eligible and set(selected) != eligible:
                    raise RuntimeError(
                        "explicit all-eligible response request was not fully selected"
                    )
                if route.required_memory_id is not None:
                    cited_records = {
                        value.record_id for value in shaped.reasoner.outcome.hard_citations
                    }
                    required_memory = required_memory_records[route.required_memory_id]
                    if required_memory.record_id not in cited_records:
                        raise RuntimeError(
                            "required indirect memory was not retrieved and cited"
                        )
                    if shaped.reasoner.mcp_bridge_receipt is None:
                        raise RuntimeError("indirect memory turn lacks MCP bridge evidence")
                if route.regeneration and shaped.reasoner.mcp_bridge_receipt is not None:
                    raise RuntimeError("regeneration exposed the replaced head through MCP")
                stored_ingress = store.get_turn_receipt_record(
                    prepared.ingress_evidence.receipt_id
                )
                if stored_ingress.payload_sha256 != text_sha256(
                    canonical_json(prepared.ingress_evidence)
                ):
                    raise RuntimeError("durable ingress evidence hash mismatch")
                event_ids[route.index] = event_record_id(
                    result.accepted_artifact.artifact_id
                )
                main_visible = store.visible_artifact_ids(sandbox.branch_id)
                if fork_id is not None and store.visible_artifact_ids(fork_id) != fork_visible:
                    raise RuntimeError("main-branch continuation leaked into frozen fork")
                turn_finished = datetime.now(timezone.utc)
                payload = {
                    "schema_version": "cera.continuous_qualification_turn.v1",
                    "qualification_id": args.qualification_id,
                    "index": route.index,
                    "key": route.key,
                    "title": route.title,
                    "status": "passed",
                    "duration_seconds": round(
                        (turn_finished - turn_started).total_seconds(), 3
                    ),
                    "raw_message": route.message,
                    "raw_message_sha256": text_sha256(route.message),
                    "application_request_sha256": prepared.application_request.request_sha256,
                    "ingress_evidence": to_primitive(prepared.ingress_evidence),
                    "reasoner": {
                        "outcome": to_primitive(shaped.reasoner.outcome),
                        "receipt": to_primitive(shaped.reasoner.receipt),
                        "provider_receipt": to_primitive(
                            shaped.reasoner.provider_call_receipt
                        ),
                        "mcp_bridge_receipt": to_primitive(
                            shaped.reasoner.mcp_bridge_receipt
                        ),
                    },
                    "context": {
                        "selected_npc_ids": [str(value) for value in selected],
                        "context_character_ids": sorted(
                            str(value) for value in context_characters
                        ),
                        "receipt": to_primitive(shaped.context.receipt),
                    },
                    "composer": {
                        "accepted_prose": result.accepted_artifact.accepted_prose,
                        "prose_sha256": result.accepted_artifact.prose_sha256,
                        "provider_receipt": to_primitive(
                            shaped.composer.provider_call_receipt
                        ),
                        "manifest": to_primitive(shaped.composer.manifest),
                    },
                    "verifier": {
                        "receipt": to_primitive(
                            shaped.realization_verification.receipt
                        ),
                        "provider_receipt": to_primitive(
                            shaped.realization_verification.provider_call_receipt
                        ),
                    },
                    "publication": {
                        "artifact": to_primitive(result.accepted_artifact),
                        "commit_receipt": to_primitive(result.commit_receipt),
                        "application_receipt": to_primitive(result.receipt),
                        "event_record_id": str(event_ids[route.index]),
                        "visible_artifact_count": len(main_visible),
                    },
                    "assertions": {
                        "exactly_one_call_per_provider_stage": True,
                        "story_state_committed": True,
                        "ingress_evidence_committed": True,
                        "selected_context_only": True,
                        "branch_fork_unchanged": fork_id is not None,
                        "restart_before_turn": route.index in {6, 10},
                        "regeneration": route.regeneration,
                        "required_memory_id": route.required_memory_id,
                        "required_memory_retrieved": route.required_memory_id is not None,
                        "all_eligible_required": route.require_all_eligible,
                        "all_eligible_selected": (
                            set(selected) == eligible
                            if route.require_all_eligible
                            else None
                        ),
                        "reasoner_transport_mode": (
                            "one_shot_request_bound_mcp"
                            if route.required_memory_id is not None
                            else "persistent_no_mcp"
                        ),
                        "verifier_transport_mode": "codex_cli_exec_one_shot",
                    },
                }
                write_json(output_dir / "turns" / f"{route.index:02d}_{route.key}.json", payload)
                summary["turns"].append(
                    {
                        "index": route.index,
                        "key": route.key,
                        "status": "passed",
                        "duration_seconds": payload["duration_seconds"],
                        "artifact_id": str(result.accepted_artifact.artifact_id),
                        "prose_sha256": result.accepted_artifact.prose_sha256,
                        "selected_npc_ids": payload["context"]["selected_npc_ids"],
                        "provider_calls": result.receipt.adapter_call_count,
                    }
                )
                summary["persistent_transport_sessions"] = persistent_session_state(
                    persistent_reasoner_runner,
                    verifier_runner,
                )
                write_json(output_dir / "summary.json", summary)
                print(
                    "CERA_CONTINUOUS_TURN="
                    + canonical_json(
                        {
                            "index": route.index,
                            "key": route.key,
                            "status": "passed",
                            "duration_seconds": payload["duration_seconds"],
                            "generation": result.commit_receipt.generation_after,
                            "visible_artifacts": len(main_visible),
                        }
                    ),
                    flush=True,
                )
            except Exception as exc:
                summary["persistent_transport_sessions"] = persistent_session_state(
                    persistent_reasoner_runner,
                    verifier_runner,
                )
                record_terminal_failure(
                    output_dir=output_dir,
                    summary=summary,
                    qualification_id=args.qualification_id,
                    route=route,
                    exc=exc,
                    store=store,
                    request_id=request_id,
                    failure_stage="application_execution_or_acceptance",
                )
                return 1

        if store.integrity_check() != ("ok",) or store.foreign_key_check():
            raise RuntimeError("terminal SQLite integrity validation failed")
        if store.get_branch(sandbox.branch_id).generation != 10:
            raise RuntimeError("continuous route did not commit ten generations")
        if store.table_count("artifacts") != 10:
            raise RuntimeError("continuous route did not preserve ten immutable artifacts")
        if len(main_visible) != 9:
            raise RuntimeError("regeneration did not leave nine visible main artifacts")
        if fork_id is None or len(fork_visible) != 6:
            raise RuntimeError("branch isolation probe was not completed")
        sessions = persistent_session_state(
            persistent_reasoner_runner,
            verifier_runner,
        )
        expected_reasoner_submissions = sum(
            route.required_memory_id is None for route in ROUTE
        )
        requests_per_epoch = (
            PersistentNoMcpCodexRunner.QUALIFIED_MAX_REQUESTS_PER_PROCESS
        )
        expected_reasoner_launches = (
            expected_reasoner_submissions + requests_per_epoch - 1
        ) // requests_per_epoch
        if sessions != {
            "scene_reasoner": {
                "process_launch_count": expected_reasoner_launches,
                "request_submission_count": expected_reasoner_submissions,
            },
            "scene_realization_verifier": {
                "process_launch_count": len(ROUTE),
                "request_submission_count": len(ROUTE),
            },
        }:
            raise RuntimeError(
                "continuous route did not preserve qualified reasoner epochs and one-shot verifier calls"
            )
        summary.update(
            {
                "status": "passed",
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "successful_turns": 10,
                "consecutive_successes": 10,
                "total_provider_calls": 30,
                "sol_calls": 20,
                "deepseek_calls": 10,
                "committed_generations": 10,
                "immutable_artifacts": 10,
                "visible_main_artifacts": 9,
                "frozen_fork_artifacts": 6,
                "adult_route_reached": False,
                "adult_route_note": (
                    "The authorized route remained ordinary; Adult ON/EX was not forced."
                ),
                "integrity_check": "ok",
                "foreign_key_findings": 0,
                "story_database_retained": False,
                "qualification_story_canonical": False,
                "persistent_transport_sessions": sessions,
            }
        )
        write_json(output_dir / "summary.json", summary)
        print(
            "CERA_CONTINUOUS_RESULT="
            + canonical_json(
                {
                    "status": "passed",
                    "successful_turns": 10,
                    "consecutive_successes": 10,
                    "total_provider_calls": 30,
                    "committed_generations": 10,
                    "visible_main_artifacts": 9,
                    "frozen_fork_artifacts": 6,
                    "integrity_check": "ok",
                }
            ),
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
