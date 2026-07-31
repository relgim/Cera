"""Run the creator-authorized five-case, non-committing live qualification.

This is deliberately not a production runner.  Every case uses an auto-deleting
real-Genesis SQLite sandbox, an empty Codex workspace, and the unpromoted live
provider candidates.  A case is attempted once: there is no retry, fallback,
publication, branch mutation, or derived-memory consolidation.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
from typing import Any

from cera.adult import (
    AdultContentFamily,
    AdultContentTrigger,
    AdultMechanicsCoordinator,
    AdultMechanicsProposal,
    AdultMechanicsRequest,
    AdultObservableCode,
    AdultRouteCoordinator,
    AdultRouteInput,
    AdultRouteSourceUnit,
    AdultScenarioKind,
    AdultUnitTreatment,
    ContentActivationAuthority,
    FakeAdultMechanicsFixture,
    FakeAdultMechanicsPort,
    ProviderCapabilityStatus,
    build_adult_composer_binding,
)
from cera.adult_craft.catalog import AdultCraftCatalog
from cera.adult_craft.selector import AdultCraftSelector
from cera.adult_craft.semantic import ScriptedFakeSemanticSpecificityPort
from cera.composer import (
    ArtifactPublicationMode,
    ComposerContextAssembler,
    ComposerCoordinator,
    ComposerSourcePacket,
    ComposerSourceUnit,
    CompositionMode,
    DeepSeekSceneComposerPort,
    ProtectedSourceEnvelope,
    RealizationKind,
)
from cera.contracts import BeatState, SourceUnitClassification
from cera.evaluation import RealGenesisSandbox
from cera.evidence import EvidenceFetchRequest, EvidenceService, EvidenceWorldMode
from cera.genesis.hanezawa_builder import CHARACTER_IDS
from cera.genesis.models import GenesisRecordType
from cera.ids import IdKind, TypedId
from cera.kernel import (
    AdultCapacityStatus,
    AdultConsentStatus,
    AdultFreedomToStop,
    AdultIdentityStatus,
    AdultPressureStatus,
    IntakeSourceUnit,
    ParticipantAdultAuthority,
    PreflightAuthority,
    RequestedContentClass,
    TurnIntakeCommand,
    TurnKernel,
)
from cera.providers import (
    CodexSDKTransport,
    DeepSeekChatTransport,
    codex_realization_verifier_candidate,
    codex_reasoner_candidate,
    deepseek_composer_candidate,
)
from cera.reasoner import (
    CodexSceneReasonerPort,
    ReasonerCoordinator,
    ReasonerSourceMode,
    ReasonerSourceUnit,
    ReasonerSourceView,
    SceneReasonerRequest,
    SeedDossierAssembler,
    SeedDossierAssemblyRequest,
)
from cera.realization import (
    CodexSceneRealizationVerifierPort,
    RejectedCandidateReviewArtifact,
    SceneRealizationVerificationCoordinator,
)
from cera.runtime import ComposerRequestPlan, LiveShapedTurnPipeline, TurnStageAuditJournal
from cera.serialization import canonical_json, domain_sha256, text_sha256, to_primitive


ROOT = Path(__file__).resolve().parents[1]
CATALOG_ROOT = ROOT / "adult" / "catalog" / "adult_craft_v1"
DEFAULT_OUTPUT = (
    ROOT
    / "evaluation"
    / "evidence"
    / "live_story_qualification_2026-07-28_v1"
)


@dataclass(frozen=True, slots=True)
class QualificationCase:
    case_id: str
    title: str
    responders: tuple[str, ...]
    source_text: str
    adult: bool = False
    adult_mode: str | None = None
    adult_families: tuple[AdultContentFamily, ...] = ()
    reasoner_scene_anchors: tuple[str, ...] = ()
    indirect_memory_id: str | None = None


CASES = (
    QualificationCase(
        case_id="01_hana_indirect_memory",
        title="Hana ordinary turn with indirect missed-calls memory",
        responders=("Hana",),
        source_text=(
            "After dinner, Ted asks Hana whether the years of missed calls still "
            "weigh on her. He waits for her answer."
        ),
        reasoner_scene_anchors=(
            "Hanezawa home after dinner.",
            "The missed-calls cue requires an evidence lookup if it is not in the seed dossier.",
        ),
        indirect_memory_id="H-M08",
    ),
    QualificationCase(
        case_id="02_mia_sakura_selected_cast",
        title="Mia and Sakura ordinary selected-cast turn",
        responders=("Mia", "Sakura"),
        source_text=(
            "Ted asks Mia and Sakura, who are alone with him in the shared room, "
            "how they feel about his first evening in the house."
        ),
        reasoner_scene_anchors=(
            "Only Mia, Sakura, and Ted are present.",
            "Yuuni and every other sister are absent and cannot appear.",
        ),
    ),
    QualificationCase(
        case_id="03_hana_adult_on",
        title="Hana consent-valid Adult ON direct realization",
        responders=("Hana",),
        source_text=(
            "Hana, age 38, has explicitly agreed to a private sexual moment with "
            "Ted and remains free to stop. Ted touches her clitoris as agreed; Hana "
            "answers from her own established personality. Stop before Ted makes "
            "another choice."
        ),
        adult=True,
        adult_mode="adult_on",
        adult_families=(AdultContentFamily.GENERAL_INTIMACY,),
        reasoner_scene_anchors=(
            "Python authorized Adult ON for the current consent-valid segment.",
            "Use direct anatomical vocabulary where the selected action requires it.",
            "Keep Hana's response character-owned; do not author Ted beyond the supplied source.",
        ),
    ),
    QualificationCase(
        case_id="04_hana_adult_ex_climax",
        title="Hana consent-valid Adult EX buildup and climax",
        responders=("Hana",),
        source_text=(
            "Hana, age 38, has explicitly consented to continued private sex with "
            "Ted and can stop at any moment. The agreed stimulation is approaching "
            "her climax. Continue through a causal buildup, Hana's voice and inner "
            "experience, physical reactions, an action-bound sound, climax, and a "
            "brief aftermath; do not invent Ted's next decision."
        ),
        adult=True,
        adult_mode="adult_ex",
        adult_families=(
            AdultContentFamily.GENERAL_INTIMACY,
            AdultContentFamily.BODY_RESPONSE_CONTINUITY,
            AdultContentFamily.FLUID_CONTINUITY,
        ),
        reasoner_scene_anchors=(
            "Python authorized Adult EX for the current consent-valid segment.",
            "Select current beats for buildup, vocal and inner response, causal physiology, action-bound sound, climax, and aftermath.",
            "Bodily-fluid reactions may be heightened but must remain caused by the selected beat rather than automatic.",
        ),
    ),
    QualificationCase(
        case_id="05_hana_adult_ex_toilet",
        title="Hana consent-valid Adult EX toilet and material continuity",
        responders=("Hana",),
        source_text=(
            "Hana, age 38, and Ted have explicitly agreed to a private toilet-themed "
            "sexual scene, and Hana remains free to stop. Hana deliberately relaxes "
            "and defecates in front of him as agreed. Continue Hana's character-owned "
            "reaction with anatomical and material continuity, causal pressure and "
            "muscle response, action-bound sound, buildup, release, and aftermath; "
            "do not invent Ted's next action."
        ),
        adult=True,
        adult_mode="adult_ex",
        adult_families=(
            AdultContentFamily.GENERAL_INTIMACY,
            AdultContentFamily.BODY_RESPONSE_CONTINUITY,
            AdultContentFamily.TOILET_CONTINUITY,
        ),
        reasoner_scene_anchors=(
            "Python authorized Adult EX and toilet continuity for this current consent-valid segment.",
            "Select current beats for buildup, causal physiology, direct anatomy, material continuity, action-bound sound, release, and aftermath.",
            "Keep the toilet outcome scoped to the supplied agreement and do not infer adjacent preferences or relationship meaning.",
        ),
    ),
)


def ident(kind: IdKind, suffix: str) -> TypedId:
    return TypedId(kind, f"qualification-{suffix}")


def relationship_record(sandbox: RealGenesisSandbox, name: str):
    character_id = CHARACTER_IDS[name]
    ted = sandbox.protected_user_id()
    return next(
        record
        for record in sandbox.compiled.records
        if record.record_type is GenesisRecordType.RELATIONSHIP_EDGE
        and record.relationship_from_id == character_id
        and record.relationship_to_id == ted
    )


def adult_identity_record(sandbox: RealGenesisSandbox, name: str):
    character_id = CHARACTER_IDS[name]
    for record in sandbox.compiled.records:
        document = EvidenceService._from_genesis(record, sandbox.revision_id)
        sections = json.loads(document.sections_json)
        route_flags = sections.get("route_flags")
        if (
            character_id in document.subject_ids
            and isinstance(route_flags, dict)
            and route_flags.get("adult_eligibility") == "confirmed_identity_eligible"
        ):
            return record
    raise LookupError(f"adult identity record not found for {name}")


def build_command(
    sandbox: RealGenesisSandbox,
    case: QualificationCase,
    qualification_id: str,
    *,
    preflight: PreflightAuthority,
) -> TurnIntakeCommand:
    responders = tuple(CHARACTER_IDS[name] for name in case.responders)
    ted = sandbox.protected_user_id()
    return TurnIntakeCommand(
        world_id=sandbox.world_id,
        request_id=ident(IdKind.REQUEST, f"{qualification_id}-{case.case_id}"),
        session_id=ident(IdKind.SESSION, qualification_id),
        branch_id=sandbox.branch_id,
        expected_generation=0,
        expected_parent_artifact_id=None,
        genesis_revision_id=sandbox.revision_id,
        protected_user_id=ted,
        present_character_ids=(ted, *responders),
        requested_responding_npc_ids=responders,
        source_units=(
            IntakeSourceUnit(SourceUnitClassification.MESSAGE, case.source_text),
        ),
        requested_route_hints=(case.adult_mode,) if case.adult_mode else (),
        idempotency_key=f"{qualification_id}-{case.case_id}",
        preflight_authority=preflight,
        world_mode=EvidenceWorldMode.REAL,
    )


def exact_evidence(sandbox, prepared, records, sections):
    evidence_ids = tuple(
        sandbox.evidence_id(prepared.evidence_snapshot, record.record_id)
        for record in records
    )
    return sandbox.service.fetch_evidence(
        prepared.evidence_snapshot,
        EvidenceFetchRequest(evidence_ids, sections),
    ).exact_records


def ordinary_request(
    sandbox: RealGenesisSandbox,
    case: QualificationCase,
    qualification_id: str,
):
    kernel = TurnKernel(sandbox.service)
    prepared = kernel.prepare_turn(
        build_command(
            sandbox,
            case,
            qualification_id,
            preflight=PreflightAuthority(RequestedContentClass.ORDINARY),
        ),
        access_scope=sandbox.system_scope(),
    )
    relationship_records = tuple(
        relationship_record(sandbox, name) for name in case.responders
    )
    seeds = exact_evidence(
        sandbox,
        prepared,
        relationship_records,
        ("claim", "knowledge"),
    )
    prepared_unit = prepared.request.source_units[0]
    source_view = ReasonerSourceUnit(
        source_unit_id=prepared_unit.source_unit_id,
        classification=prepared_unit.classification,
        safe_text=case.source_text,
    )
    seed_assembly = SeedDossierAssembler(sandbox.service).assemble(
        SeedDossierAssemblyRequest(
            snapshot=prepared.evidence_snapshot,
            aware_character_ids=tuple(
                CHARACTER_IDS[name] for name in case.responders
            ),
            preexpanded_exact_evidence=seeds,
            evidence_obligations=(),
            scene_anchors=case.reasoner_scene_anchors,
            explicit_unknowns=("Ted's next unsupplied choice is unknown.",),
            prohibited_inferences=(
                "Do not author Ted's private state or an unsupplied action.",
                "Do not transfer one character's private evidence to another.",
            ),
        )
    )
    request = SceneReasonerRequest(
        schema_version=SceneReasonerRequest.SCHEMA_VERSION,
        prepared_turn=prepared,
        source_view=ReasonerSourceView(
            mode=ReasonerSourceMode.ORDINARY_EXACT,
            source_sha256=prepared.request.source_sha256,
            units=(source_view,),
            contains_exact_protected_adult_prose=False,
        ),
        seed_dossier=seed_assembly.dossier,
        hard_boundaries=(
            "Use only snapshot-authorized exact evidence for hard decisions.",
            "Do not author the protected user.",
            "Select only requested present responders.",
            *(
                (
                    "The missed-calls cue is a required indirect-memory lookup: search, fetch exact advertised sections, and cite the resulting owner-authorized memory in any decision.",
                )
                if case.indirect_memory_id
                else ()
            ),
        ),
    )
    composer_unit = ComposerSourceUnit(
        source_unit_id=prepared_unit.source_unit_id,
        classification=prepared_unit.classification,
        exact_text=case.source_text,
        protected_user_allowed_kinds=(RealizationKind.ACTION, RealizationKind.DIALOGUE),
        required_state=BeatState.ATTEMPTED,
        participant_ids=tuple(CHARACTER_IDS[name] for name in case.responders),
    )
    plan = ComposerRequestPlan(
        source_packet=ComposerSourcePacket(
            mode=CompositionMode.ORDINARY,
            source_sha256=prepared.request.source_sha256,
            ordinary_units=(composer_unit,),
            protected_envelope=None,
            reasoner_safe_ledger_sha256=request.source_view.source_view_sha256,
        ),
        scene_scope="Immediate Hanezawa household continuation for live qualification.",
        response_profile_version="cera-five-run-live-structural-v2",
        continuity_references=(),
        creator_event_coverage_required=False,
        hard_boundaries=(
            "Realize the validated current segment as complete presentation-neutral prose.",
            "Use only selected-character realization context.",
            "Stop before Ted's next unsupplied meaningful choice.",
        ),
        adult_binding=None,
        publication_mode=ArtifactPublicationMode.APPEND,
    )
    return kernel, request, plan, None, seed_assembly


def adult_request(
    sandbox: RealGenesisSandbox,
    case: QualificationCase,
    qualification_id: str,
):
    hana = CHARACTER_IDS["Hana"]
    identity = adult_identity_record(sandbox, "Hana")
    request_id = ident(IdKind.REQUEST, f"{qualification_id}-{case.case_id}")
    authority_snapshot = sandbox.service.open_snapshot(
        request_id=request_id,
        world_id=sandbox.world_id,
        branch_id=sandbox.branch_id,
        access_scope=sandbox.system_scope(),
        world_mode=EvidenceWorldMode.REAL,
    )
    identity_evidence_id = sandbox.evidence_id(authority_snapshot, identity.record_id)
    preflight = PreflightAuthority(
        requested_content_class=RequestedContentClass.ADULT,
        adult_participants=(
            ParticipantAdultAuthority(
                participant_id=hana,
                identity_status=AdultIdentityStatus.CONFIRMED_ADULT,
                consent_status=AdultConsentStatus.GRANTED,
                capacity_status=AdultCapacityStatus.CLEAR,
                pressure_status=AdultPressureStatus.NONE,
                freedom_to_stop=AdultFreedomToStop.PRESENT,
                evidence_ids=(identity_evidence_id,),
            ),
        ),
    )
    kernel = TurnKernel(sandbox.service)
    prepared = kernel.prepare_turn(
        build_command(sandbox, case, qualification_id, preflight=preflight),
        access_scope=sandbox.system_scope(),
    )
    unit = prepared.request.source_units[0]
    exact_unit = ComposerSourceUnit(
        source_unit_id=unit.source_unit_id,
        classification=unit.classification,
        exact_text=case.source_text,
        protected_user_allowed_kinds=(RealizationKind.ACTION, RealizationKind.DIALOGUE),
        required_state=BeatState.ATTEMPTED,
        participant_ids=(hana,),
    )
    envelope = ProtectedSourceEnvelope(
        protected_source_id=prepared.request.raw_source_ref,
        source_sha256=prepared.request.source_sha256,
        units=(exact_unit,),
    )
    triggers = tuple(
        AdultContentTrigger(
            family=family,
            authority=ContentActivationAuthority.CURRENT_EXPLICIT_SOURCE,
            source_unit_id=unit.source_unit_id,
            evidence_id=None,
        )
        for family in case.adult_families
    )
    preparation = AdultRouteCoordinator(sandbox.service).prepare(
        AdultRouteInput(
            prepared_turn=prepared,
            participant_ids=(hana,),
            scenario_kind=AdultScenarioKind.CONSENSUAL_ACTIVITY,
            scenario_source_unit_ids=(unit.source_unit_id,),
            source_units=(
                AdultRouteSourceUnit(
                    source_unit_id=unit.source_unit_id,
                    source_unit_sha256=unit.sha256,
                    ordinal=0,
                    progression_state=BeatState.ATTEMPTED,
                    participant_ids=(hana,),
                    observable_code=AdultObservableCode.SOURCE_AUTHORED_PROTECTED_EVENT,
                    private_state_owner_ids=(hana,),
                    consent_status=AdultConsentStatus.GRANTED,
                    capacity_status=AdultCapacityStatus.CLEAR,
                    pressure_status=AdultPressureStatus.NONE,
                    freedom_to_stop=AdultFreedomToStop.PRESENT,
                ),
            ),
            semantic_assertions=(),
            content_triggers=triggers,
            provider_capability=ProviderCapabilityStatus.NOT_EVALUATED,
            synthetic_fixture=True,
        ),
        envelope,
    )
    relationship = relationship_record(sandbox, "Hana")
    seeds = exact_evidence(
        sandbox,
        prepared,
        (relationship,),
        ("claim", "knowledge"),
    )
    seed_assembly = SeedDossierAssembler(sandbox.service).assemble(
        SeedDossierAssemblyRequest(
            snapshot=prepared.evidence_snapshot,
            aware_character_ids=(hana,),
            preexpanded_exact_evidence=seeds,
            evidence_obligations=(),
            scene_anchors=(
                "A private room in the Hanezawa home.",
                *case.reasoner_scene_anchors,
            ),
            explicit_unknowns=("Ted's next unsupplied choice is unknown.",),
            prohibited_inferences=(
                "Do not infer consent from bodily response.",
                "Do not infer adjacent preferences or relationship meaning.",
                "Do not author Ted beyond the protected source envelope.",
            ),
        )
    )
    request = SceneReasonerRequest(
        schema_version=SceneReasonerRequest.SCHEMA_VERSION,
        prepared_turn=prepared,
        source_view=preparation.reasoner_source_view,
        seed_dossier=seed_assembly.dossier,
        hard_boundaries=(
            "Use the non-graphic causal ledger for reasoning.",
            "Return an adult craft need matching the authorized mode, families, current beats, channels, and requested axes.",
            "All selected participants are confirmed adults and current consent is valid.",
            "Blocked non-consensual generation remains excluded.",
        ),
    )
    return kernel, request, None, preparation, seed_assembly


def write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


class QualificationRejectedCandidateReviewStore:
    """Explicit creator-local review sink, separate from safe runtime receipts."""

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
            raise RuntimeError("rejected-candidate review authority boundary changed")
        self.root.mkdir(parents=True, exist_ok=True)
        target = self.root / f"{artifact.review_sha256}.json"
        if target.exists():
            raise FileExistsError(
                f"refusing to overwrite rejected-candidate review evidence: {target}"
            )
        write_json(
            target,
            {
                "schema_version": "cera.qualification_rejected_candidate_review_export.v1",
                "review_sha256": artifact.review_sha256,
                "access_scope": artifact.access_scope,
                "retention_policy": artifact.retention_policy,
                "runtime_receipt_forbidden": True,
                "story_state_committed": False,
                "authoritative_store_writes": 0,
                "artifact": to_primitive(artifact),
            },
        )


def run_case(
    case: QualificationCase,
    output_dir: Path,
    qualification_id: str,
) -> dict[str, Any]:
    started = datetime.now(timezone.utc)
    with RealGenesisSandbox.create(ROOT) as sandbox, tempfile.TemporaryDirectory(
        prefix=f"cera-verifier-{case.case_id}-"
    ) as verifier_workspace:
        realization_verifier = CodexSceneRealizationVerifierPort(
            CodexSDKTransport(
                codex_realization_verifier_candidate(
                    model="gpt-5.6-sol",
                    effort="medium",
                ),
                workspace=Path(verifier_workspace),
            )
        )
        reasoner_coordinator = ReasonerCoordinator(
            sandbox.service,
            TurnKernel(sandbox.service),
        )
        pipeline = LiveShapedTurnPipeline(
            reasoner_coordinator,
            ComposerContextAssembler(sandbox.service),
            ComposerCoordinator(),
            adult_craft_selector=AdultCraftSelector(
                AdultCraftCatalog.load(CATALOG_ROOT),
                maximum_craft_bytes=32_768,
            ),
            realization_verification_coordinator=(
                SceneRealizationVerificationCoordinator(
                    QualificationRejectedCandidateReviewStore(
                        output_dir / "rejected_candidate_reviews"
                    )
                )
            ),
            realization_verifier_port=realization_verifier,
            stage_audit_journal=TurnStageAuditJournal(sandbox.store),
        )
        if case.adult:
            _, request, _, preparation, seed_assembly = adult_request(
                sandbox,
                case,
                qualification_id,
            )
        else:
            _, request, plan, preparation, seed_assembly = ordinary_request(
                sandbox,
                case,
                qualification_id,
            )

        with tempfile.TemporaryDirectory(prefix=f"cera-codex-{case.case_id}-") as workspace:
            reasoner_port = CodexSceneReasonerPort(
                CodexSDKTransport(
                    codex_reasoner_candidate(model="gpt-5.6-sol", effort="medium"),
                    workspace=Path(workspace),
                ),
                evidence_tools_enabled=case.indirect_memory_id is not None,
            )
            reasoned = reasoner_coordinator.execute(request, reasoner_port)

        if case.indirect_memory_id:
            expected_memory = sandbox.record_with_payload_value(
                "memory_id", case.indirect_memory_id
            )
            cited = {value.record_id for value in reasoned.outcome.hard_citations}
            if expected_memory.record_id not in cited:
                raise RuntimeError(
                    "live Reasoner did not retrieve and cite the required indirect memory"
                )

        if case.adult:
            # Route preparation is frozen, so keep the PreparedTurn in a local
            # side binding rather than modifying the authoritative dataclass.
            prepared_turn = request.prepared_turn
            decision = reasoned.outcome.decision
            assert decision is not None
            source_unit = preparation.ledger.units[0]
            mechanics_request = AdultMechanicsRequest(
                schema_version=AdultMechanicsRequest.SCHEMA_VERSION,
                prepared_turn=prepared_turn,
                adult_authority_id=preparation.authority.adult_authority_id,
                authority_sha256=preparation.authority.authority_sha256,
                ledger_sha256=preparation.ledger.ledger_sha256,
                context_sha256=preparation.context.context_sha256,
                reasoner_outcome=reasoned.outcome,
                reasoner_receipt=reasoned.receipt,
                unit_ids=(source_unit.source_unit_id,),
                hard_boundaries=(
                    "Mechanics only; preserve source outcomes.",
                    "Do not replace Codex psychology or sequence ownership.",
                ),
            )
            mechanics_proposal = AdultMechanicsProposal(
                schema_version=AdultMechanicsProposal.SCHEMA_VERSION,
                adult_plan_id=ident(IdKind.ADULT_PLAN, case.case_id),
                authority_sha256=preparation.authority.authority_sha256,
                ledger_sha256=preparation.ledger.ledger_sha256,
                context_sha256=preparation.context.context_sha256,
                decision_sha256=domain_sha256("cera.scene_decision.v1", decision),
                sequence_plan_sha256=domain_sha256(
                    "cera.sequence_plan.v1",
                    (decision.current_segment, decision.future_segments),
                ),
                unit_treatments=(
                    AdultUnitTreatment(
                        source_unit_id=source_unit.source_unit_id,
                        progression_state=source_unit.progression_state,
                        participant_ids=source_unit.participant_ids,
                        craft_reference_ids=preparation.context.craft_reference_ids,
                        preserve_source_outcome=True,
                        no_adjacent_psychology_inference=True,
                    ),
                ),
                no_psychology_override=True,
                contains_story_prose=False,
                advisory_metadata=(),
            )
            mechanics_fixture = FakeAdultMechanicsFixture(
                f"qualification-{case.case_id}", mechanics_proposal
            )
            mechanics = AdultMechanicsCoordinator().execute(
                preparation,
                mechanics_request,
                FakeAdultMechanicsPort(mechanics_fixture),
                fixture=mechanics_fixture,
            )
            plan = ComposerRequestPlan(
                source_packet=preparation.composer_source_packet,
                scene_scope="Immediate consent-valid protected continuation for live qualification.",
                response_profile_version="cera-five-run-live-adult-structural-v2",
                continuity_references=(),
                creator_event_coverage_required=True,
                hard_boundaries=(
                    "Preserve the validated decision and current source outcome.",
                    "Realize only selected characters with the selected Adult ON/EX craft fragments.",
                    "Stop before Ted's next unsupplied meaningful choice.",
                ),
                adult_binding=build_adult_composer_binding(preparation, mechanics),
                publication_mode=ArtifactPublicationMode.APPEND,
            )

        composer_port = DeepSeekSceneComposerPort(
            DeepSeekChatTransport(deepseek_composer_candidate(model="deepseek-v4-pro"))
        )
        semantic_port = (
            ScriptedFakeSemanticSpecificityPort(f"qualification-{case.case_id}")
            if case.adult
            else None
        )
        result = pipeline.execute(
            request,
            None,
            plan,
            composer_port,
            precomputed_reasoner_result=reasoned,
            semantic_specificity_port=semantic_port,
        )

        selected = tuple(str(value) for value in result.context.request.selected_npc_ids)
        expected_selected = tuple(str(CHARACTER_IDS[name]) for name in case.responders)
        if not selected or not set(selected).issubset(set(expected_selected)):
            raise RuntimeError("selected realization context escaped requested responders")
        if case.case_id == "02_mia_sakura_selected_cast":
            yuuni = CHARACTER_IDS["Yuuni"]
            if any(
                yuuni in block.applicable_character_ids
                for block in result.context.request.realization_context.blocks
            ):
                raise RuntimeError("Yuuni leaked into the Mia/Sakura realization context")

        finished = datetime.now(timezone.utc)
        artifact = result.accepted_artifact
        payload = {
            "schema_version": "cera.live_story_qualification_case.v2",
            "qualification_id": qualification_id,
            "case_id": case.case_id,
            "title": case.title,
            "status": "passed",
            "started_at": started.isoformat(),
            "finished_at": finished.isoformat(),
            "duration_seconds": round((finished - started).total_seconds(), 3),
            "input": {
                "source_text": case.source_text,
                "source_sha256": text_sha256(case.source_text),
                "adult": case.adult,
                "adult_mode": case.adult_mode,
                "responders": list(case.responders),
                "present_character_ids": [
                    str(value) for value in request.prepared_turn.present_character_ids
                ],
            },
            "reasoner": {
                "outcome": to_primitive(result.reasoner.outcome),
                "receipt": to_primitive(result.reasoner.receipt),
                "provider_receipt": to_primitive(
                    result.reasoner.provider_call_receipt
                ),
                "mcp_bridge_receipt": to_primitive(
                    result.reasoner.mcp_bridge_receipt
                ),
                "seed_assembly_receipt": to_primitive(seed_assembly.receipt),
                "seed_lookup_receipts": to_primitive(seed_assembly.lookup_receipts),
            },
            "context": {
                "selected_npc_ids": list(selected),
                "block_character_ids": sorted(
                    {
                        str(character_id)
                        for block in result.context.request.realization_context.blocks
                        for character_id in block.applicable_character_ids
                    }
                ),
                "receipt": to_primitive(result.context.receipt),
            },
            "composer": {
                "accepted_prose": artifact.accepted_prose,
                "prose_sha256": artifact.prose_sha256,
                "artifact": to_primitive(artifact),
                "manifest": to_primitive(result.composer.manifest),
                "provider_receipt": to_primitive(result.composer.provider_call_receipt),
                "validation_receipt": to_primitive(result.composer.validation_receipt),
            },
            "realization_verification": {
                "receipt": to_primitive(result.realization_verification.receipt),
                "provider_receipt": to_primitive(
                    result.realization_verification.provider_call_receipt
                ),
                "limitation": "live_semantic_verification_not_human_taste_proof",
            },
            "adult_craft": (
                to_primitive(result.adult_craft) if result.adult_craft is not None else None
            ),
            "live_shaped_receipt": to_primitive(result.receipt),
            "assertions": {
                "story_state_committed": result.receipt.story_state_committed,
                "story_authority_writes": result.receipt.story_authority_writes,
                "external_provider_calls": result.receipt.external_provider_calls,
                "selected_context_exact": selected == expected_selected,
                "yuuni_excluded_from_case_02": (
                    True if case.case_id == "02_mia_sakura_selected_cast" else None
                ),
                "semantic_verifier": (
                    "scripted_fake_non_proving" if case.adult else "not_applicable"
                ),
                "realization_verifier": "sol_medium_semantic_verifier",
            },
        }
        write_json(output_dir / f"{case.case_id}.json", payload)
        return {
            "case_id": case.case_id,
            "title": case.title,
            "status": "passed",
            "duration_seconds": payload["duration_seconds"],
            "accepted_prose_sha256": artifact.prose_sha256,
            "external_provider_calls": result.receipt.external_provider_calls,
            "codex_model": result.reasoner.provider_call_receipt.returned_model,
            "deepseek_model": result.composer.provider_call_receipt.returned_model,
            "verifier_model": (
                result.realization_verification.provider_call_receipt.returned_model
            ),
            "story_state_committed": False,
            "story_authority_writes": 0,
        }


def failure_record(
    case: QualificationCase,
    exc: Exception,
    qualification_id: str,
) -> dict[str, Any]:
    envelope = getattr(exc, "envelope", None)
    provider_receipt = getattr(exc, "provider_call_receipt", None)
    bridge_receipt = getattr(exc, "mcp_bridge_receipt", None)
    lookup_receipts = tuple(getattr(exc, "lookup_receipts", ()))
    failure_bundle = getattr(exc, "failure_evidence_bundle", None)
    retained_handles = tuple(getattr(exc, "retained_evidence_handles", ()))
    retained_payloads = tuple(
        getattr(exc, "privacy_safe_receipt_payloads", ())
    )
    rejected_review_handle = getattr(
        exc, "rejected_candidate_review_handle", None
    )
    return {
        "schema_version": "cera.live_story_qualification_case.v4",
        "qualification_id": qualification_id,
        "case_id": case.case_id,
        "title": case.title,
        "status": "failed",
        "error": (
            to_primitive(envelope)
            if envelope is not None
            else {"class": type(exc).__name__, "message": str(exc)}
        ),
        "source_sha256": text_sha256(case.source_text),
        "adult": case.adult,
        "adult_mode": case.adult_mode,
        "automatic_retry_count": 0,
        "fallback_enabled": False,
        "story_state_committed": False,
        "story_authority_writes": 0,
        "safe_failure_evidence": {
            "provider_receipt": to_primitive(provider_receipt),
            "mcp_bridge_receipt": to_primitive(bridge_receipt),
            "lookup_receipts": to_primitive(lookup_receipts),
            "stage_failure_bundle": to_primitive(failure_bundle),
            "all_prior_stage_receipt_handles": to_primitive(retained_handles),
            "all_prior_stage_receipt_payloads": to_primitive(retained_payloads),
            "rejected_candidate_review_handle": (
                {
                    "review_id": str(rejected_review_handle[0]),
                    "review_sha256": rejected_review_handle[1],
                }
                if rejected_review_handle is not None
                else None
            ),
            "retains_raw_provider_output": False,
            "retains_prompt": False,
            "retains_secret": False,
        },
    }


def existing_case_summary(payload: dict[str, Any]) -> dict[str, Any]:
    result = {
        "case_id": payload["case_id"],
        "title": payload["title"],
        "status": payload["status"],
        "story_state_committed": payload.get("story_state_committed", False),
        "story_authority_writes": payload.get("story_authority_writes", 0),
    }
    if payload["status"] == "passed":
        composer = payload["composer"]
        assertions = payload["assertions"]
        result.update(
            {
                "duration_seconds": payload["duration_seconds"],
                "accepted_prose_sha256": composer["prose_sha256"],
                "external_provider_calls": assertions["external_provider_calls"],
                "codex_model": payload["reasoner"]["provider_receipt"]["returned_model"],
                "deepseek_model": payload["composer"]["provider_receipt"]["returned_model"],
            }
        )
    else:
        error = payload["error"]
        result.update(
            {
                "error_class": error.get("class", error.get("error_code", "ErrorEnvelope")),
                "error_message": error.get("message", "qualification case failed"),
                "automatic_retry_count": 0,
                "fallback_enabled": False,
            }
        )
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--confirm-live", action="store_true")
    parser.add_argument("--list-cases", action="store_true")
    parser.add_argument("--resume-incomplete", action="store_true")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--qualification-id", default="five-run-live-v1")
    args = parser.parse_args()
    if args.list_cases:
        print(canonical_json([{"case_id": value.case_id, "title": value.title} for value in CASES]))
        return 0
    if not args.confirm_live:
        parser.error("live provider calls require --confirm-live")
    if len(CASES) != 5:
        raise RuntimeError("qualification matrix must contain exactly five cases")
    qualification_id = args.qualification_id.strip()
    if not qualification_id or len(qualification_id) > 80 or not all(
        value.isascii() and (value.isalnum() or value in "-_.")
        for value in qualification_id
    ):
        raise RuntimeError("qualification ID must be 1-80 safe ASCII characters")
    output_dir = args.output_dir.resolve()
    if output_dir.exists() and not args.resume_incomplete:
        raise RuntimeError(f"refusing to overwrite prior qualification evidence: {output_dir}")
    if args.resume_incomplete and not output_dir.is_dir():
        raise RuntimeError("resume requires an existing incomplete evidence directory")
    if (output_dir / "summary.json").exists():
        raise RuntimeError("qualification already has a terminal summary and cannot resume")
    output_dir.mkdir(parents=True, exist_ok=args.resume_incomplete)
    started = datetime.now(timezone.utc)
    summaries: list[dict[str, Any]] = []
    existing_ids: set[str] = set()
    for case in CASES:
        path = output_dir / f"{case.case_id}.json"
        if not path.exists():
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("case_id") != case.case_id or payload.get("status") not in {
            "passed",
            "failed",
        }:
            raise RuntimeError(f"existing case evidence is malformed: {path.name}")
        if payload.get("qualification_id", qualification_id) != qualification_id:
            raise RuntimeError(f"existing case evidence has a different qualification ID: {path.name}")
        existing_ids.add(case.case_id)
        summaries.append(existing_case_summary(payload))
    if existing_ids and not args.resume_incomplete:
        raise RuntimeError("existing case evidence requires --resume-incomplete")
    for index, case in enumerate(CASES, 1):
        if case.case_id in existing_ids:
            print(f"[{index}/5] {case.case_id}: preserved-existing", flush=True)
            continue
        print(f"[{index}/5] {case.case_id}: starting", flush=True)
        try:
            summary = run_case(case, output_dir, qualification_id)
        except Exception as exc:  # preserve one-shot failure and continue to next case
            failure = failure_record(case, exc, qualification_id)
            write_json(output_dir / f"{case.case_id}.json", failure)
            summary = {
                "case_id": case.case_id,
                "title": case.title,
                "status": "failed",
                "error_class": type(exc).__name__,
                "error_message": str(exc),
                "automatic_retry_count": 0,
                "fallback_enabled": False,
                "story_state_committed": False,
                "story_authority_writes": 0,
            }
        summaries.append(summary)
        print(f"[{index}/5] {case.case_id}: {summary['status']}", flush=True)
    finished = datetime.now(timezone.utc)
    report = {
        "schema_version": "cera.live_story_qualification_summary.v2",
        "qualification_id": qualification_id,
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
        "duration_seconds": round((finished - started).total_seconds(), 3),
        "attempted_cases": len(summaries),
        "passed_cases": sum(value["status"] == "passed" for value in summaries),
        "failed_cases": sum(value["status"] == "failed" for value in summaries),
        "automatic_retry_count": 0,
        "fallback_enabled": False,
        "resumed_after_runner_stdout_interruption": bool(existing_ids),
        "preserved_existing_case_ids": sorted(existing_ids),
        "production_enabled": False,
        "story_state_committed": False,
        "story_authority_writes": 0,
        "semantic_verifier_limitation": (
            "Adult semantic specificity used the production-prohibited scripted fake; "
            "its empty finding set is not proof of semantic quality."
        ),
        "realization_verifier_limitation": (
            "Scene realization uses one independent Sol-medium semantic verifier call; "
            "its result is not human taste proof or route promotion."
        ),
        "cases": summaries,
    }
    write_json(output_dir / "summary.json", report)
    print(
        f"completed: {report['passed_cases']} passed, {report['failed_cases']} failed; "
        f"evidence={output_dir}",
        flush=True,
    )
    return 0 if report["failed_cases"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
