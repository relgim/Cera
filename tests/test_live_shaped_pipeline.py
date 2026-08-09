from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import urllib.error
import unittest

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
    ComposerContextBlock,
    ComposerContextKind,
    ComposerContextSource,
    ComposerCoordinator,
    ComposerExecutionFailure,
    ComposerSourcePacket,
    ComposerSourceUnit,
    CompositionMode,
    DeepSeekSceneComposerPort,
    RealizationKind,
)
from cera.contracts import (
    BehavioralScenePlan,
    CausalRunwayContract,
    BeatState,
    CharacterMove,
    CurrentSegment,
    DecisionRoute,
    DevelopmentAtomKind,
    DevelopmentAtomProposal,
    DevelopmentAtomStrength,
    FutureSegment,
    InteractionTopology,
    InteriorityLevel,
    NaturalStopReason,
    PromptTone,
    ProtectedUserRealizationAllowance,
    SceneEventBlock,
    SceneFunction,
    SceneRunwayClass,
    SceneDecision,
    SequenceBeat,
    SourceClaimAuthority,
    SourceClaimDecision,
    SourceClaimKind,
    SourceClaimLedger,
    SourceUnitClassification,
    WriterScaffold,
)
from cera.evidence import EvidenceFetchRequest, EvidenceWorldMode
from cera.genesis.hanezawa_builder import CHARACTER_IDS
from cera.ids import IdKind
from cera.errors import (
    ContractValidationError,
    ErrorCode,
    EvidenceServiceError,
    StateConflictError,
    TransactionError,
)
from cera.kernel import (
    AdultCapacityStatus,
    AdultConsentStatus,
    AdultFreedomToStop,
    AdultIdentityStatus,
    AdultPressureStatus,
    ParticipantAdultAuthority,
    PreflightAuthority,
    RequestedContentClass,
)
from cera.providers import (
    CodexSDKTransport,
    DeepSeekChatTransport,
    ProviderFailureCallReceipt,
    codex_reasoner_candidate,
    deepseek_composer_candidate,
)
from cera.reasoner import (
    CodexSceneReasonerPort,
    McpEvidenceToolName,
    InterventionReason,
    ParticipationRole,
    ParticipationSelection,
    ReasonerEvidenceCitation,
    ReasonerExecutionFailure,
    ReasonerOutcome,
    ReasonerOutcomeStatus,
    ReasonerSeedDossier,
    SceneReasonerRequest,
    SceneReasonerPortFailure,
)
from cera.realization import EchoAcceptingSceneRealizationVerifierPort
from cera.runtime import (
    ComposerRequestPlan,
    LiveShapedTurnFailure,
    LiveShapedTurnPipeline,
    OrdinaryTurnCommitBuilder,
    OrdinaryTurnCommitCoordinator,
    TurnStageAuditJournal,
)
from cera.registry import build_schema_registry
from cera.serialization import canonical_json, domain_sha256, text_sha256, to_primitive
from cera.storage import SQLiteAuthorityStore
import tests.test_codex_scene_reasoner as codex_support
import tests.test_real_genesis_integration as real_support
from tests.provider_fakes import OfflineDeepSeekChatTransport


class FakeHTTPResponse:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _tb) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


class PacketDrivenDeepSeekOpener:
    """Build a valid declared response from the submitted typed packet."""

    def __init__(self) -> None:
        self.calls = []
        self.last_packet: dict | None = None

    def __call__(self, request, *, timeout):
        self.calls.append((request, timeout))
        body = json.loads(request.data.decode("utf-8"))
        user_message = body["messages"][1]["content"]
        packet = json.loads(user_message.split("\n", 1)[1])
        self.last_packet = packet
        composer_request = packet["composition_dto"]
        decision = composer_request["decision"]
        selected = decision["responding_npc_ids"]
        coverage_required = composer_request["creator_event_coverage_required"]
        prefix = "The supplied source event is preserved. " if coverage_required else ""
        if composer_request["source"]["protected_envelope"] is not None:
            realization = (
                "The selected responder answers within the agreed boundary; "
                "the narration names her vagina directly while preserving the "
                "planned action and conversational floor."
            )
        else:
            realization = (
                "The selected responders answer in their established voices "
                "and preserve the conversational floor."
            )
        story = prefix + realization
        coverage = []
        if coverage_required:
            for unit in (
                composer_request["source"]["protected_envelope"]["units"]
                if composer_request["source"]["protected_envelope"]
                else composer_request["source"]["ordinary_units"]
            ):
                coverage.append(
                    {
                        "source_unit_id": unit["source_unit_id"],
                        "segment_keys": ["core"],
                    }
                )
        realization_segments = []
        beats = decision["current_segment"]["ordered_beats"]
        for index, beat in enumerate(beats):
            realization_segments.append(
                {
                    "kind": "action",
                    "segment_key": "core",
                    "authority_id": beat["beat_id"],
                }
            )
        specificity = [
            {
                "obligation_key": value["obligation_key"],
                "segment_keys": ["core"],
            }
            for value in (
                composer_request.get("specificity_contract") or {}
            ).get("beat_requirements", [])
        ]
        output = {
            "schema_version": "cera.deepseek_composition_draft.v6",
            "story_segments": [{"segment_key": "core", "text": story}],
            "source_coverage": coverage,
            "realization_segments": realization_segments,
            "specificity_coverage": specificity,
            "terminal_segment_key": "core",
        }
        payload = {
            "id": f"private-deepseek-{len(self.calls)}",
            "model": "deepseek-v4-flash",
            "choices": [
                {
                    "message": {"content": canonical_json(output)},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 900,
                "completion_tokens": 180,
                "prompt_cache_hit_tokens": 0,
                "prompt_cache_miss_tokens": 900,
            },
        }
        return FakeHTTPResponse(payload)


class FailingTurnReceiptStore(SQLiteAuthorityStore):
    def _after_artifact_insert(self, connection, bundle) -> None:
        raise RuntimeError("simulated Phase 16 failure")


class LiveShapedPipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.real = real_support.RealGenesisReasonerComposerTests("runTest")
        self.real.setUp()
        self.addCleanup(self.real.doCleanups)
        self.temporary = tempfile.TemporaryDirectory(prefix="cera_live_shaped_")
        self.addCleanup(self.temporary.cleanup)
        self.pipeline = LiveShapedTurnPipeline(
            self.real.reasoner,
            ComposerContextAssembler(self.real.sandbox.service),
            ComposerCoordinator(),
            adult_craft_selector=AdultCraftSelector(
                AdultCraftCatalog.load(
                    Path(__file__).resolve().parents[1]
                    / "adult"
                    / "catalog"
                    / "adult_craft_v1"
                ),
                maximum_craft_bytes=20_000,
            ),
            realization_verifier_port=EchoAcceptingSceneRealizationVerifierPort(),
        )

    def ordinary_case(
        self,
        suffix: str,
        *,
        responders=None,
        records=None,
        source: str | None = None,
        fetch_sections=("claim", "knowledge"),
        include_search: bool = False,
        expected_generation: int = 0,
        expected_parent_artifact_id=None,
        publication_mode: ArtifactPublicationMode = ArtifactPublicationMode.APPEND,
        publication_parent_artifact_id=None,
        replaces_artifact_id=None,
    ):
        responders = responders or (CHARACTER_IDS["Hana"],)
        records = records or tuple(
            self.real.relationship_record(
                next(name for name, value in CHARACTER_IDS.items() if value == character_id)
            )
            for character_id in responders
        )
        source = source or "Ted addresses the selected family members in the shared room."
        prepared, reasoner_request, scripted_result = (
            self.real.reasoner_request_and_result(
                f"{suffix}-seed",
                responders,
                source,
                records,
                expected_generation=expected_generation,
                expected_parent_artifact_id=expected_parent_artifact_id,
            )
        )
        citations = scripted_result.outcome.hard_citations
        script = []
        if include_search:
            script.append(
                (
                    McpEvidenceToolName.SEARCH_EVIDENCE.value,
                    {"terms": ["missed", "calls"], "entity_ids": [str(responders[0])], "limit": 5},
                )
            )
        script.append(
            (
                McpEvidenceToolName.FETCH_EVIDENCE.value,
                {
                    "evidence_ids": [str(value.evidence_id) for value in citations],
                    "sections": list(fetch_sections),
                    "include_superseded_audit": False,
                },
            )
        )
        tool_names = tuple(value[0] for value in script)
        runner = codex_support.StaticReasonerRunner(
            scripted_result.outcome,
            tool_names=tool_names,
        )
        codex_transport = CodexSDKTransport(
            codex_reasoner_candidate(),
            workspace=Path(self.temporary.name),
            runner=runner,
        )
        bridge = codex_support.RecordingBridgeFactory(tuple(script))
        reasoner_port = CodexSceneReasonerPort(
            codex_transport,
            bridge_factory=bridge,
        )
        source_unit = ComposerSourceUnit(
            source_unit_id=prepared.request.source_units[0].source_unit_id,
            classification=SourceUnitClassification.MESSAGE,
            exact_text=source,
            protected_user_allowed_kinds=(RealizationKind.ACTION,),
            required_state=BeatState.ATTEMPTED,
            participant_ids=responders,
        )
        plan = ComposerRequestPlan(
            source_packet=ComposerSourcePacket(
                mode=CompositionMode.ORDINARY,
                source_sha256=prepared.request.source_sha256,
                ordinary_units=(source_unit,),
                protected_envelope=None,
                reasoner_safe_ledger_sha256=(
                    reasoner_request.source_view.source_view_sha256
                ),
            ),
            scene_scope="Immediate real-Genesis provider-free continuation.",
            response_profile_version="live-shaped-test-v1",
            continuity_references=(),
            creator_event_coverage_required=False,
            hard_boundaries=(
                "Preserve the validated decisions.",
                "Stop before Ted's next unsupplied choice.",
            ),
            adult_binding=None,
            publication_mode=publication_mode,
            publication_parent_artifact_id=publication_parent_artifact_id,
            replaces_artifact_id=replaces_artifact_id,
        )
        deepseek_opener = PacketDrivenDeepSeekOpener()
        composer_port = DeepSeekSceneComposerPort(
            OfflineDeepSeekChatTransport(
                deepseek_composer_candidate(),
                opener=deepseek_opener,
                environment={"DEEPSEEK_API_KEY": "dummy"},
                external_provider_boundary=False,
            )
        )
        return (
            reasoner_request,
            reasoner_port,
            plan,
            composer_port,
            runner,
            deepseek_opener,
        )

    def adult_case(self, suffix: str):
        hana = CHARACTER_IDS["Hana"]
        identity_record = self.real.adult_identity_record("Hana")
        relationship_record = self.real.relationship_record("Hana")
        snapshot = self.real.sandbox.service.open_snapshot(
            request_id=real_support.ident(IdKind.REQUEST, suffix),
            world_id=self.real.sandbox.world_id,
            branch_id=self.real.sandbox.branch_id,
            access_scope=self.real.sandbox.system_scope(),
            world_mode=real_support.EvidenceWorldMode.REAL,
        )
        identity_evidence_id = self.real.sandbox.evidence_id(
            snapshot, identity_record.record_id
        )
        relationship_evidence_id = self.real.sandbox.evidence_id(
            snapshot, relationship_record.record_id
        )
        exact_marker = "PROTECTED-COMPOSER-ONLY-741"
        command = self.real.command(
            suffix,
            responders=(hana,),
            source_text=exact_marker,
            preflight=PreflightAuthority(
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
            ),
        )
        prepared = self.real.kernel.prepare_turn(
            command,
            access_scope=self.real.sandbox.system_scope(),
        )
        prepared_unit = prepared.request.source_units[0]
        route_input = AdultRouteInput(
            prepared_turn=prepared,
            participant_ids=(hana,),
            scenario_kind=AdultScenarioKind.CONSENSUAL_ACTIVITY,
            scenario_source_unit_ids=(prepared_unit.source_unit_id,),
            source_units=(
                AdultRouteSourceUnit(
                    source_unit_id=prepared_unit.source_unit_id,
                    source_unit_sha256=prepared_unit.sha256,
                    ordinal=0,
                    progression_state=BeatState.ATTEMPTED,
                    participant_ids=(hana,),
                    observable_code=AdultObservableCode.SOURCE_AUTHORED_PROTECTED_EVENT,
                    private_state_owner_ids=(),
                    consent_status=AdultConsentStatus.GRANTED,
                    capacity_status=AdultCapacityStatus.CLEAR,
                    pressure_status=AdultPressureStatus.NONE,
                    freedom_to_stop=AdultFreedomToStop.PRESENT,
                ),
            ),
            semantic_assertions=(),
            content_triggers=(
                AdultContentTrigger(
                    family=AdultContentFamily.GENERAL_INTIMACY,
                    authority=ContentActivationAuthority.CURRENT_EXPLICIT_SOURCE,
                    source_unit_id=prepared_unit.source_unit_id,
                    evidence_id=None,
                ),
            ),
            provider_capability=ProviderCapabilityStatus.NOT_EVALUATED,
            synthetic_fixture=True,
        )
        envelope = real_support.ProtectedSourceEnvelope(
            protected_source_id=prepared.request.raw_source_ref,
            source_sha256=prepared.request.source_sha256,
            units=(
                ComposerSourceUnit(
                    source_unit_id=prepared_unit.source_unit_id,
                    classification=SourceUnitClassification.MESSAGE,
                    exact_text=exact_marker,
                    protected_user_allowed_kinds=(RealizationKind.ACTION,),
                    required_state=BeatState.ATTEMPTED,
                    participant_ids=(hana,),
                ),
            ),
        )
        preparation = AdultRouteCoordinator(self.real.sandbox.service).prepare(
            route_input, envelope
        )
        move = CharacterMove(
            character_id=hana,
            perception="Hana recognizes the mutually agreed private moment.",
            selected_intent="Continue only within the explicitly established boundary.",
            action_direction="Respond without deciding Ted's next choice.",
            evidence_ids=(relationship_evidence_id,),
            knowledge_constraints=("Use only Hana-authorized evidence.",),
        )
        beat = SequenceBeat(
            beat_id=real_support.ident(IdKind.BEAT, f"{suffix}-beat"),
            actor_id=hana,
            state=BeatState.ATTEMPTED,
            neutral_event="Hana gives a bounded character-owned response.",
            evidence_ids=(relationship_evidence_id,),
        )
        decision = SceneDecision(
            schema_version=SceneDecision.SCHEMA_VERSION,
            decision_id=real_support.ident(IdKind.DECISION, suffix),
            route=DecisionRoute.CONSENT_VALID_ADULT,
            scene_intent="Continue within the established consent boundary.",
            responding_npc_ids=(hana,),
            floor_owner_id=hana,
            character_moves=(move,),
            current_segment=CurrentSegment(
                real_support.ident(IdKind.SEGMENT, f"{suffix}-current"),
                (beat,),
                "Ted's next unsupplied meaningful choice.",
            ),
            future_segments=(
                FutureSegment(
                    real_support.ident(IdKind.SEGMENT, f"{suffix}-future"),
                    "conditional_plan_only",
                    ("Ted supplies another consent-valid choice.",),
                    ("Consent or capacity changes.",),
                    ("The private interaction may continue.",),
                    "Ted decides what to do next.",
                ),
            ),
            writer_must_preserve=("Preserve consent and the stop boundary.",),
            uncertainties=(),
            prohibited_inferences=("Do not infer consent from bodily response.",),
            advisory_state_candidates=(),
        )
        outcome = ReasonerOutcome(
            schema_version=ReasonerOutcome.SCHEMA_VERSION,
            status=ReasonerOutcomeStatus.DECISION_READY,
            decision=decision,
            participation=(
                ParticipationSelection(
                    hana,
                    ParticipationRole.LEAD,
                    InterventionReason.FLOOR_OWNER,
                    (relationship_evidence_id,),
                ),
            ),
            hard_citations=(
                ReasonerEvidenceCitation(
                    relationship_evidence_id,
                    relationship_record.record_id,
                    relationship_record.record_version,
                ),
            ),
            insufficiencies=(),
            blocker_code=None,
            advisory_state_deltas=(),
            protected_user_boundary_acknowledged=True,
        )
        reasoner_request = SceneReasonerRequest(
            schema_version=SceneReasonerRequest.SCHEMA_VERSION,
            prepared_turn=prepared,
            source_view=preparation.reasoner_source_view,
            seed_dossier=ReasonerSeedDossier(
                prepared.evidence_snapshot.snapshot_token,
                (hana,),
                (),
                ("A private room in the Hanezawa home.",),
                ("Ted's next choice is unknown.",),
                ("Do not infer consent from bodily response.",),
            ),
            hard_boundaries=(
                "Use the non-graphic causal ledger.",
                "Do not author the protected user.",
            ),
        )
        reasoner_fixture = real_support.FakeReasonerFixture(
            f"binding-{suffix}",
            (
                real_support.FakeToolCall(
                    real_support.FakeToolOperation.FETCH_EVIDENCE,
                    EvidenceFetchRequest(
                        (relationship_evidence_id,), ("claim", "knowledge")
                    ),
                ),
            ),
            outcome,
        )
        binding_reasoned = self.real.reasoner.execute(
            reasoner_request,
            real_support.FakeSceneReasonerPort(reasoner_fixture),
            fixture=reasoner_fixture,
        )
        mechanics_request = AdultMechanicsRequest(
            AdultMechanicsRequest.SCHEMA_VERSION,
            prepared,
            preparation.authority.adult_authority_id,
            preparation.authority.authority_sha256,
            preparation.ledger.ledger_sha256,
            preparation.context.context_sha256,
            binding_reasoned.outcome,
            binding_reasoned.receipt,
            (prepared_unit.source_unit_id,),
            ("Mechanics only.", "Do not replace psychology."),
        )
        proposal = AdultMechanicsProposal(
            AdultMechanicsProposal.SCHEMA_VERSION,
            real_support.ident(IdKind.ADULT_PLAN, suffix),
            preparation.authority.authority_sha256,
            preparation.ledger.ledger_sha256,
            preparation.context.context_sha256,
            domain_sha256("cera.scene_decision.v1", decision),
            domain_sha256(
                "cera.sequence_plan.v1",
                (decision.current_segment, decision.future_segments),
            ),
            (
                AdultUnitTreatment(
                    prepared_unit.source_unit_id,
                    BeatState.ATTEMPTED,
                    (hana,),
                    preparation.context.craft_reference_ids,
                    True,
                    True,
                ),
            ),
            True,
            False,
            (),
        )
        mechanics_fixture = FakeAdultMechanicsFixture(f"binding-{suffix}", proposal)
        mechanics = AdultMechanicsCoordinator().execute(
            preparation,
            mechanics_request,
            FakeAdultMechanicsPort(mechanics_fixture),
            fixture=mechanics_fixture,
        )
        script = (
            (
                McpEvidenceToolName.FETCH_EVIDENCE.value,
                {
                    "evidence_ids": [str(relationship_evidence_id)],
                    "sections": ["claim", "knowledge"],
                    "include_superseded_audit": False,
                },
            ),
        )
        runner = codex_support.StaticReasonerRunner(
            outcome,
            tool_names=(McpEvidenceToolName.FETCH_EVIDENCE.value,),
        )
        reasoner_port = CodexSceneReasonerPort(
            CodexSDKTransport(
                codex_reasoner_candidate(),
                workspace=Path(self.temporary.name),
                runner=runner,
            ),
            bridge_factory=codex_support.RecordingBridgeFactory(script),
        )
        plan = ComposerRequestPlan(
            source_packet=preparation.composer_source_packet,
            scene_scope="Immediate consent-valid protected continuation.",
            response_profile_version="live-shaped-adult-test-v1",
            continuity_references=(),
            creator_event_coverage_required=True,
            hard_boundaries=(
                "Preserve the validated decision.",
                "Stop before Ted's next unsupplied choice.",
            ),
            adult_binding=build_adult_composer_binding(preparation, mechanics),
            craft_blocks=(),
        )
        opener = PacketDrivenDeepSeekOpener()
        composer_port = DeepSeekSceneComposerPort(
            OfflineDeepSeekChatTransport(
                deepseek_composer_candidate(),
                opener=opener,
                environment={"DEEPSEEK_API_KEY": "dummy"},
                external_provider_boundary=False,
            )
        )
        return reasoner_request, reasoner_port, plan, composer_port, runner, opener, exact_marker

    def test_real_multi_character_live_shaped_path_is_bounded_and_uncommitted(self) -> None:
        args = self.ordinary_case(
            "multi",
            responders=(CHARACTER_IDS["Hana"], CHARACTER_IDS["Mia"]),
        )
        before = tuple(
            self.real.sandbox.store.table_count(value)
            for value in ("sources", "artifacts", "authority_records")
        )
        result = self.pipeline.execute(*args[:4])
        runner, opener = args[4:]
        self.assertEqual(runner.calls, 1)
        self.assertEqual(len(opener.calls), 1)
        self.assertEqual(result.receipt.external_provider_calls, 2)
        self.assertFalse(result.receipt.story_state_committed)
        self.assertEqual(result.receipt.story_authority_writes, 0)
        self.assertEqual(result.context.receipt.lookup_count, 4)
        voice_blocks = tuple(
            value
            for value in result.context.request.realization_context.blocks
            if value.kind is ComposerContextKind.CHARACTER_VOICE
        )
        self.assertEqual(len(voice_blocks), 2)
        self.assertEqual(
            {value.applicable_character_ids[0] for value in voice_blocks},
            {CHARACTER_IDS["Hana"], CHARACTER_IDS["Mia"]},
        )
        self.assertEqual(
            before,
            tuple(
                self.real.sandbox.store.table_count(value)
                for value in ("sources", "artifacts", "authority_records")
            ),
        )
        restarted = SQLiteAuthorityStore(self.real.sandbox.database_path)
        self.assertEqual(restarted.table_count("artifacts"), 0)

    def test_sakura_voice_rules_and_examples_reach_deepseek_as_structured_context(self) -> None:
        args = self.ordinary_case(
            "sakura-voice-packet",
            responders=(CHARACTER_IDS["Sakura"],),
        )
        self.pipeline.execute(*args[:4])
        packet = args[5].last_packet
        self.assertIsNotNone(packet)
        blocks = packet["composition_dto"]["realization_context"]["blocks"]
        voice = next(value for value in blocks if value["kind"] == "character_voice")
        self.assertEqual(
            voice["applicable_character_ids"],
            [str(CHARACTER_IDS["Sakura"])],
        )
        self.assertIsInstance(voice["sections"], dict)
        source_text = voice["sections"]["source_text"]
        self.assertIn("precise, formal, direct, concise", source_text)
        self.assertIn("Your explanation does not account for the timing", source_text)
        self.assertIn("Her speech remains administrative and reasoned", source_text)

    def test_active_behavioral_packet_contains_only_selected_character_profiles(self) -> None:
        args = self.ordinary_case(
            "selected-character-projection",
            responders=(CHARACTER_IDS["Sakura"],),
            source="Ted asks Sakura a question at the front door.",
        )
        reasoned = self.real.reasoner.execute(args[0], args[1])
        decision = reasoned.outcome.decision
        assert decision is not None
        source_unit = args[0].source_view.units[0]
        source_claim = SourceClaimDecision(
            claim_id=real_support.ident(IdKind.SOURCE_CLAIM, "selected-profile-source"),
            source_unit_id=source_unit.source_unit_id,
            kind=SourceClaimKind.CREATIVE_DIRECTION,
            authority=SourceClaimAuthority.SOURCE_AUTHORIZED,
            start=0,
            end=len(source_unit.safe_text),
            exact_text_sha256=text_sha256(source_unit.safe_text),
            normalized_meaning="Ted asks Sakura a question at the front door.",
            affected_character_ids=(CHARACTER_IDS["Sakura"],),
            rationale="The current source directly establishes this cue.",
        )
        evidence_id = reasoned.outcome.hard_citations[0].evidence_id
        block = SceneEventBlock(
            block_id=real_support.ident(IdKind.SCENE_BLOCK, "selected-profile-block"),
            actor_ids=(CHARACTER_IDS["Sakura"],),
            purpose="Sakura appraises and answers the doorstep question.",
            causal_basis="Her current stance and the direct question.",
            event_advances=("She chooses a bounded, character-specific answer.",),
            resulting_state="Sakura retains the floor after answering.",
            evidence_ids=(evidence_id,),
            source_claim_ids=(source_claim.claim_id,),
            protected_user_allowance=ProtectedUserRealizationAllowance(
                allowed_kinds=(),
                source_claim_ids=(),
                explanation="No new protected-user realization is needed.",
            ),
            writer_scaffold=WriterScaffold(
                scaffold_id=real_support.ident(
                    IdKind.WRITER_SCAFFOLD, "selected-profile-scaffold"
                ),
                viewpoint_character_ids=(CHARACTER_IDS["Sakura"],),
                motivation_and_subtext=("Preserve Sakura's guarded formality.",),
                voice_and_interiority=("Use her exact selected speech evidence.",),
                physical_and_material_continuity=("Keep the doorstep geometry.",),
                transition_obligations=("Move from appraisal to answer.",),
                open_realization_space=("DeepSeek owns phrasing, pacing, and gesture.",),
            ),
        )
        plan = BehavioralScenePlan(
            schema_version=BehavioralScenePlan.SCHEMA_VERSION,
            claim_ledger=SourceClaimLedger(
                schema_version=SourceClaimLedger.SCHEMA_VERSION,
                source_sha256=args[0].prepared_turn.request.source_sha256,
                controls_sha256=args[0].behavioral_controls.controls_sha256,
                claims=(source_claim,),
                unresolved_questions=(),
                prohibited_inferences=("Do not author Ted's response.",),
            ),
            event_blocks=(block,),
            runway=CausalRunwayContract(
                runway_class=SceneRunwayClass.DEVELOPED,
                development_obligations=("Complete the appraisal and answer.",),
                continue_beyond_prompt_endpoint=True,
                stop_reason=NaturalStopReason.CAUSAL_UNIT_COMPLETE,
                stop_condition="Stop after Sakura's meaningful afterbeat.",
            ),
            interaction_topology=InteractionTopology.SINGLE_NPC_FLOOR,
            scene_function=SceneFunction.ORDINARY_SOCIAL,
            tone=PromptTone.NEUTRAL,
            interiority_level=InteriorityLevel.LOW,
            selected_character_ids=(CHARACTER_IDS["Sakura"],),
            omitted_character_ids=tuple(
                value
                for value in CHARACTER_IDS.values()
                if value != CHARACTER_IDS["Sakura"]
            ),
        )
        outcome = replace(reasoned.outcome, behavioral_scene_plan=plan)
        receipt = replace(reasoned.receipt, outcome_sha256=outcome.outcome_sha256)
        reasoned = replace(reasoned, outcome=outcome, receipt=receipt)
        self.pipeline.execute(
            args[0],
            None,
            args[2],
            args[3],
            precomputed_reasoner_result=reasoned,
        )
        dto = args[5].last_packet["composition_dto"]
        projections = dto["realization_context"]["effective_character_projections"]
        self.assertEqual(
            [value["character_id"] for value in projections],
            [str(CHARACTER_IDS["Sakura"])],
        )
        serialized = canonical_json(dto)
        for name, character_id in CHARACTER_IDS.items():
            if name != "Sakura":
                self.assertNotIn(str(character_id), serialized)

    def test_only_sol_verified_atomic_development_commits_with_the_accepted_turn(self) -> None:
        args = self.ordinary_case(
            "atomic-development",
            responders=(CHARACTER_IDS["Enne"],),
            source="Ted asks Enne a careful question and waits for her answer.",
        )
        reasoned = self.real.reasoner.execute(args[0], args[1])
        plan = reasoned.outcome.behavioral_scene_plan
        if plan is None:
            source_unit = args[0].source_view.units[0]
            source_claim = SourceClaimDecision(
                claim_id=real_support.ident(
                    IdKind.SOURCE_CLAIM, "atomic-development-source"
                ),
                source_unit_id=source_unit.source_unit_id,
                kind=SourceClaimKind.CREATIVE_DIRECTION,
                authority=SourceClaimAuthority.SOURCE_AUTHORIZED,
                start=0,
                end=len(source_unit.safe_text),
                exact_text_sha256=text_sha256(source_unit.safe_text),
                normalized_meaning="Ted asks Enne a careful question and waits.",
                affected_character_ids=(CHARACTER_IDS["Enne"],),
                rationale="The current source directly establishes the cue.",
            )
            block = SceneEventBlock(
                block_id=real_support.ident(
                    IdKind.SCENE_BLOCK, "atomic-development-block"
                ),
                actor_ids=(CHARACTER_IDS["Enne"],),
                purpose="Enne appraises and answers the careful question.",
                causal_basis="Her current analytical stance and the direct cue.",
                event_advances=("She notices and contains one small response.",),
                resulting_state="Enne has answered without resolving Ted's next choice.",
                evidence_ids=(reasoned.outcome.hard_citations[0].evidence_id,),
                source_claim_ids=(source_claim.claim_id,),
                protected_user_allowance=ProtectedUserRealizationAllowance(
                    allowed_kinds=(),
                    source_claim_ids=(),
                    explanation="No new protected-user realization is needed.",
                ),
                writer_scaffold=WriterScaffold(
                    scaffold_id=real_support.ident(
                        IdKind.WRITER_SCAFFOLD, "atomic-development-scaffold"
                    ),
                    viewpoint_character_ids=(CHARACTER_IDS["Enne"],),
                    motivation_and_subtext=("Keep the response small and unexplained.",),
                    voice_and_interiority=("Preserve Enne's analytical reserve.",),
                    physical_and_material_continuity=(),
                    transition_obligations=("Move from appraisal to answer.",),
                    open_realization_space=("DeepSeek owns exact phrasing.",),
                ),
            )
            plan = BehavioralScenePlan(
                schema_version=BehavioralScenePlan.SCHEMA_VERSION,
                claim_ledger=SourceClaimLedger(
                    schema_version=SourceClaimLedger.SCHEMA_VERSION,
                    source_sha256=args[0].prepared_turn.request.source_sha256,
                    controls_sha256=args[0].behavioral_controls.controls_sha256,
                    claims=(source_claim,),
                    unresolved_questions=(),
                    prohibited_inferences=("Do not accelerate Enne's attachment.",),
                ),
                event_blocks=(block,),
                runway=CausalRunwayContract(
                    runway_class=SceneRunwayClass.DEVELOPED,
                    development_obligations=("Keep change atomic.",),
                    continue_beyond_prompt_endpoint=True,
                    stop_reason=NaturalStopReason.CAUSAL_UNIT_COMPLETE,
                    stop_condition="Stop after Enne's bounded answer.",
                ),
                interaction_topology=InteractionTopology.SINGLE_NPC_FLOOR,
                scene_function=SceneFunction.EMOTIONAL_VULNERABILITY,
                tone=PromptTone.WARM,
                interiority_level=InteriorityLevel.MEDIUM,
                selected_character_ids=(CHARACTER_IDS["Enne"],),
                omitted_character_ids=(),
            )
        atom = DevelopmentAtomProposal(
            atom_id=real_support.ident(IdKind.DEVELOPMENT, "enne-first-trace"),
            owner_character_id=CHARACTER_IDS["Enne"],
            kind=DevelopmentAtomKind.NOTICED_SIGNAL,
            strength_before=DevelopmentAtomStrength.ABSENT,
            strength_after=DevelopmentAtomStrength.TRACE,
            summary="Enne notices one small internal response without identifying it.",
            source_scene_block_ids=(plan.event_blocks[0].block_id,),
            evidence_record_ids=(reasoned.outcome.hard_citations[0].record_id,),
            predecessor_development_ids=(),
            inference_limit=(
                "This does not establish attraction, attachment, jealousy, or love."
            ),
        )
        updated_plan = replace(plan, development_atoms=(atom,))
        outcome = replace(reasoned.outcome, behavioral_scene_plan=updated_plan)
        receipt = replace(reasoned.receipt, outcome_sha256=outcome.outcome_sha256)
        reasoned = replace(reasoned, outcome=outcome, receipt=receipt)
        result = self.pipeline.execute(
            args[0],
            None,
            args[2],
            args[3],
            precomputed_reasoner_result=reasoned,
        )
        self.assertEqual(
            result.realization_verification.receipt.verified_development_atom_ids,
            (atom.atom_id,),
        )
        committed = OrdinaryTurnCommitCoordinator(
            self.real.sandbox.store
        ).commit(result)
        inserted = self.real.sandbox.store.authority_records_at_head(
            committed.receipt.branch_id,
            committed.receipt.new_artifact_id,
        )
        development = tuple(
            value for value in inserted if value.record_type == "development"
        )
        self.assertEqual(len(development), 1)
        payload = json.loads(development[0].payload_json)
        section = json.loads(payload["sections_json"])["development_atom"]
        self.assertEqual(section["strength_before"], "absent")
        self.assertEqual(section["strength_after"], "trace")
        self.assertIn("does not establish", section["inference_limit"])

    def test_validated_precomputed_reasoner_result_does_not_invoke_reasoner_twice(self) -> None:
        args = self.ordinary_case("precomputed-once")
        reasoned = self.real.reasoner.execute(args[0], args[1])
        result = self.pipeline.execute(
            args[0],
            None,
            args[2],
            args[3],
            precomputed_reasoner_result=reasoned,
        )
        runner, opener = args[4:]
        self.assertEqual(runner.calls, 1)
        self.assertEqual(len(opener.calls), 1)
        self.assertEqual(result.reasoner, reasoned)
        self.assertEqual(result.receipt.external_provider_calls, 2)
        self.assertFalse(result.receipt.story_state_committed)

    def test_indirect_owner_memory_reaches_hana_context_without_cross_character_leak(self) -> None:
        memory = self.real.sandbox.record_with_payload_value("memory_id", "H-M08")
        args = self.ordinary_case(
            "memory",
            records=(memory,),
            source="Ted asks Hana about the years of missed calls.",
            fetch_sections=(
                "remembered_content",
                "learned_meaning_or_belief_pressure",
                "possible_retrieval_cues",
            ),
            include_search=True,
        )
        result = self.pipeline.execute(*args[:4])
        current = tuple(
            value
            for value in result.context.request.realization_context.blocks
            if value.kind is ComposerContextKind.CURRENT_STATE
        )
        self.assertEqual(len(current), 1)
        self.assertEqual(current[0].applicable_character_ids, (CHARACTER_IDS["Hana"],))
        self.assertIn("maintaining faith", current[0].exact_evidence.sections_json)
        self.assertNotIn(CHARACTER_IDS["Mia"], current[0].applicable_character_ids)
        self.assertEqual(result.reasoner.receipt.tool_call_count, 2)

    def test_nondecision_stops_before_context_and_deepseek(self) -> None:
        args = list(self.ordinary_case("insufficient"))
        outcome = ReasonerOutcome(
            schema_version=ReasonerOutcome.SCHEMA_VERSION,
            status=ReasonerOutcomeStatus.INSUFFICIENT_EVIDENCE,
            decision=None,
            participation=(),
            hard_citations=(),
            insufficiencies=("Exact evidence is unavailable.",),
            blocker_code=None,
            advisory_state_deltas=(),
            protected_user_boundary_acknowledged=True,
        )
        runner = codex_support.StaticReasonerRunner(outcome)
        transport = CodexSDKTransport(
            codex_reasoner_candidate(),
            workspace=Path(self.temporary.name),
            runner=runner,
        )
        args[1] = CodexSceneReasonerPort(
            transport,
            bridge_factory=codex_support.RecordingBridgeFactory(),
        )
        self.pipeline.stage_audit_journal = TurnStageAuditJournal(
            self.real.sandbox.store
        )
        with self.assertRaises(LiveShapedTurnFailure) as caught:
            self.pipeline.execute(*args[:4])
        self.assertEqual(runner.calls, 1)
        self.assertEqual(len(args[5].calls), 0)
        self.assertEqual(self.real.sandbox.store.table_count("artifacts"), 0)
        bundle = caught.exception.failure_evidence_bundle
        self.assertIsNotNone(bundle)
        self.assertEqual(bundle.stage, "reasoner_decision_gate")
        self.assertEqual(bundle.external_provider_calls_observed, 1)
        self.assertIn(
            "REASONER_STATUS_INSUFFICIENT_EVIDENCE",
            bundle.safe_diagnostic_codes,
        )
        self.assertIn(
            "reasoner_provider_call_receipt",
            {value.evidence_kind for value in bundle.retained_evidence},
        )
        entries = self.real.sandbox.store.turn_stage_entries(
            args[0].prepared_turn.request.request_id
        )
        self.assertEqual(entries[-1].stage, "reasoner_decision_gate")
        self.assertEqual(entries[-1].status.value, "failed")

    def test_reasoner_transport_diagnostics_survive_failure_audit(self) -> None:
        args = self.ordinary_case("reasoner-worker-handoff")
        self.pipeline.stage_audit_journal = TurnStageAuditJournal(
            self.real.sandbox.store
        )

        class FailedStoredThreadPort:
            def reason(self, _request, _tools):
                raise SceneReasonerPortFailure(
                    ErrorCode.REASONER_UNAVAILABLE,
                    "stored Reasoner worker failed before provider dispatch",
                    safe_diagnostics=(
                        "transport:worker_failure",
                        "worker_stage:thread_resume",
                    ),
                )

        with self.assertRaises(ReasonerExecutionFailure) as caught:
            self.pipeline.execute(
                args[0],
                FailedStoredThreadPort(),
                args[2],
                args[3],
            )

        bundle = caught.exception.failure_evidence_bundle
        self.assertIsNotNone(bundle)
        self.assertEqual(
            bundle.safe_diagnostic_codes,
            (
                "REASONER_TRANSPORT_WORKER_FAILURE",
                "REASONER_WORKER_STAGE_THREAD_RESUME",
            ),
        )
        reloaded = self.real.sandbox.store.get_turn_failure_evidence(
            bundle.failure_bundle_id
        )
        self.assertEqual(
            reloaded.safe_diagnostic_codes,
            bundle.safe_diagnostic_codes,
        )
        self.assertEqual(bundle.external_provider_calls_observed, 0)
        self.assertEqual(self.real.sandbox.store.table_count("artifacts"), 0)

    def test_receipt_and_packet_do_not_retain_provider_secrets(self) -> None:
        args = self.ordinary_case("receipt")
        result = self.pipeline.execute(*args[:4])
        self.assertNotIn("dummy", repr(result))
        self.assertNotIn("offline-test-secret", repr(result))
        packet = args[5].last_packet
        self.assertIsNotNone(packet)
        self.assertTrue(packet["authority_policy"]["durable_state_writes_forbidden"])
        self.assertEqual(result.composer.validation_receipt.status, "accepted_in_memory")
        registry = build_schema_registry()
        self.assertEqual(
            registry.decode(to_primitive(result.context.receipt)),
            result.context.receipt,
        )
        self.assertEqual(registry.decode(to_primitive(result.receipt)), result.receipt)

    def test_consent_valid_adult_keeps_exact_source_composer_only(self) -> None:
        args = self.adult_case("adult-live-shaped")
        result = self.pipeline.execute(
            *args[:4],
            semantic_specificity_port=ScriptedFakeSemanticSpecificityPort(
                "adult-live-shaped"
            ),
        )
        runner, opener, marker = args[4:]
        self.assertEqual(runner.calls, 1)
        self.assertEqual(len(opener.calls), 1)
        self.assertNotIn(marker, runner.last_prompt)
        self.assertIn(marker, canonical_json(opener.last_packet))
        self.assertEqual(result.receipt.external_provider_calls, 2)
        self.assertIsNotNone(result.adult_craft)
        assert result.adult_craft is not None
        self.assertEqual(
            result.context.receipt.selected_craft_reference_ids,
            result.adult_craft.selection_receipt.selected_fragment_ids,
        )
        self.assertFalse(result.receipt.story_state_committed)
        self.assertEqual(self.real.sandbox.store.table_count("artifacts"), 0)

    def test_composer_outage_is_one_call_with_no_fallback_or_state_change(self) -> None:
        args = list(self.ordinary_case("composer-outage"))
        calls = 0

        def fail(_request, *, timeout):
            nonlocal calls
            calls += 1
            raise urllib.error.URLError("offline")

        args[3] = DeepSeekSceneComposerPort(
            OfflineDeepSeekChatTransport(
                deepseek_composer_candidate(),
                opener=fail,
                environment={"DEEPSEEK_API_KEY": "dummy"},
                external_provider_boundary=False,
            )
        )
        before = tuple(
            self.real.sandbox.store.table_count(value)
            for value in ("sources", "artifacts", "authority_records")
        )
        with self.assertRaises(ComposerExecutionFailure):
            self.pipeline.execute(*args[:4])
        self.assertEqual(args[4].calls, 1)
        self.assertEqual(calls, 1)
        self.assertEqual(
            before,
            tuple(
                self.real.sandbox.store.table_count(value)
                for value in ("sources", "artifacts", "authority_records")
            ),
        )

    def test_incomplete_composer_retains_safe_finish_and_usage_evidence(self) -> None:
        args = list(self.ordinary_case("composer-incomplete"))
        self.pipeline.stage_audit_journal = TurnStageAuditJournal(
            self.real.sandbox.store
        )

        def incomplete(_request, *, timeout):
            return FakeHTTPResponse(
                {
                    "id": "deepseek-incomplete-live-shaped",
                    "model": deepseek_composer_candidate().model_name,
                    "choices": [
                        {
                            "message": {"content": '{"partial":"not retained"}'},
                            "finish_reason": "length",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 901,
                        "completion_tokens": 8192,
                        "prompt_cache_hit_tokens": 400,
                        "prompt_cache_miss_tokens": 501,
                        "completion_tokens_details": {"reasoning_tokens": 7000},
                    },
                }
            )

        args[3] = DeepSeekSceneComposerPort(
            OfflineDeepSeekChatTransport(
                deepseek_composer_candidate(),
                opener=incomplete,
                environment={"DEEPSEEK_API_KEY": "dummy"},
                external_provider_boundary=False,
            )
        )
        with self.assertRaises(ComposerExecutionFailure) as caught:
            self.pipeline.execute(*args[:4])

        failure = caught.exception
        self.assertEqual(failure.external_provider_calls_observed, 1)
        self.assertEqual(
            failure.safe_diagnostics,
            ("DEEPSEEK_FINISH_REASON_LENGTH",),
        )
        self.assertIsInstance(
            failure.provider_call_receipt,
            ProviderFailureCallReceipt,
        )
        assert isinstance(failure.provider_call_receipt, ProviderFailureCallReceipt)
        self.assertEqual(failure.provider_call_receipt.finish_reason.value, "length")
        self.assertEqual(
            failure.provider_call_receipt.call_receipt.output_tokens,
            8192,
        )
        self.assertEqual(failure.failure_evidence_bundle.external_provider_calls_observed, 2)
        self.assertIn(
            "provider_call_receipt",
            {value.evidence_kind for value in failure.retained_evidence_handles},
        )
        safe_payload = next(
            value
            for value in failure.privacy_safe_receipt_payloads
            if value.receipt_schema_version
            == ProviderFailureCallReceipt.SCHEMA_VERSION
        )
        self.assertIn('"finish_reason":"length"', safe_payload.payload_json)
        self.assertIn('"output_tokens":8192', safe_payload.payload_json)
        self.assertNotIn("not retained", safe_payload.payload_json)
        self.assertEqual(self.real.sandbox.store.table_count("artifacts"), 0)

    def test_ordinary_result_commits_artifact_and_full_receipt_evidence_atomically(self) -> None:
        args = self.ordinary_case("phase16-commit")
        result = self.pipeline.execute(*args[:4])
        stored = OrdinaryTurnCommitCoordinator(self.real.sandbox.store).commit(result)
        replay = OrdinaryTurnCommitCoordinator(self.real.sandbox.store).commit(result)

        expected_receipt_ids = {
            *stored.receipt.validation_receipt_ids,
            *stored.receipt.lookup_receipt_ids,
            *stored.receipt.provider_receipt_ids,
        }
        self.assertFalse(stored.exact_replay)
        self.assertTrue(replay.exact_replay)
        self.assertEqual(replay.receipt, stored.receipt)
        self.assertEqual(len(stored.receipt.inserted_record_ids), 2)
        self.assertEqual(
            tuple(value.kind for value in stored.receipt.inserted_record_ids),
            (IdKind.EVENT, IdKind.MATERIAL),
        )
        self.assertEqual(self.real.sandbox.store.table_count("authority_records"), 2)
        self.assertEqual(
            self.real.sandbox.store.table_count("turn_receipt_records"),
            len(expected_receipt_ids),
        )
        self.assertEqual(
            self.real.sandbox.store.visible_artifact_ids(self.real.sandbox.branch_id),
            (result.accepted_artifact.artifact_id,),
        )
        restarted = SQLiteAuthorityStore(self.real.sandbox.database_path)
        for receipt_id in expected_receipt_ids:
            receipt_record = restarted.get_turn_receipt_record(receipt_id)
            self.assertNotIn("dummy", receipt_record.payload_json)
            self.assertNotIn("offline-test-secret", receipt_record.payload_json)
        service = real_support.EvidenceService(restarted)
        snapshot = service.open_snapshot(
            request_id=real_support.ident(IdKind.REQUEST, "phase16-event-read"),
            world_id=self.real.sandbox.world_id,
            branch_id=self.real.sandbox.branch_id,
            access_scope=self.real.sandbox.system_scope(),
            world_mode=EvidenceWorldMode.REAL,
        )
        event_evidence_id = self.real.sandbox.evidence_id(
            snapshot, stored.receipt.inserted_record_ids[0]
        )
        exact_event = service.fetch_evidence(
            snapshot,
            EvidenceFetchRequest(
                (event_evidence_id,),
                ("event", "source_coverage", "artifact_binding"),
            ),
        )
        self.assertIn("accepted_turn_realized", exact_event.exact_records[0].sections_json)
        character_snapshot = service.open_snapshot(
            request_id=real_support.ident(IdKind.REQUEST, "phase16-event-private"),
            world_id=self.real.sandbox.world_id,
            branch_id=self.real.sandbox.branch_id,
            access_scope=self.real.sandbox.character_scope("Hana"),
            world_mode=EvidenceWorldMode.REAL,
        )
        with self.assertRaises(EvidenceServiceError):
            service.fetch_evidence(
                character_snapshot,
                EvidenceFetchRequest(
                    (
                        self.real.sandbox.evidence_id(
                            character_snapshot, stored.receipt.inserted_record_ids[0]
                        ),
                    ),
                    ("event",),
                ),
            )
        self.assertEqual(restarted.integrity_check(), ("ok",))
        self.assertEqual(restarted.foreign_key_check(), ())

    def test_ordinary_publication_rejects_adult_result_without_writes(self) -> None:
        args = self.adult_case("phase16-adult-reject")
        result = self.pipeline.execute(
            *args[:4],
            semantic_specificity_port=ScriptedFakeSemanticSpecificityPort(
                "phase16-adult-reject"
            ),
        )
        with self.assertRaises(ContractValidationError):
            OrdinaryTurnCommitBuilder().build(result)
        self.assertEqual(self.real.sandbox.store.table_count("artifacts"), 0)
        self.assertEqual(self.real.sandbox.store.table_count("turn_receipt_records"), 0)

    def test_publication_detects_artifact_hash_tamper_before_prepare(self) -> None:
        args = self.ordinary_case("phase16-tamper")
        result = self.pipeline.execute(*args[:4])
        object.__setattr__(result.receipt, "accepted_artifact_sha256", "0" * 64)
        with self.assertRaises(ContractValidationError):
            OrdinaryTurnCommitBuilder().build(result)
        self.assertEqual(self.real.sandbox.store.table_count("transaction_journal"), 0)

    def test_in_transaction_failure_rolls_back_receipts_and_restart_marks_prepare(self) -> None:
        args = self.ordinary_case("phase16-rollback")
        result = self.pipeline.execute(*args[:4])
        bundle = OrdinaryTurnCommitBuilder().build(result)
        failing = FailingTurnReceiptStore(self.real.sandbox.database_path)
        index_rows_before = failing.evidence_index_row_count()
        with self.assertRaises(TransactionError):
            failing.commit_turn(bundle)
        for table in (
            "sources",
            "generations",
            "artifacts",
            "turn_receipt_records",
            "commit_receipts",
        ):
            self.assertEqual(failing.table_count(table), 0)
        self.assertEqual(failing.evidence_index_row_count(), index_rows_before)
        restarted = SQLiteAuthorityStore(self.real.sandbox.database_path)
        recovered = restarted.recover_prepared(reason="Phase 16 restart audit")
        self.assertEqual(recovered.rolled_back_transaction_ids, (bundle.transaction_id,))

    def test_stale_sibling_result_cannot_overwrite_committed_branch_head(self) -> None:
        first_args = self.ordinary_case("phase16-first")
        second_args = self.ordinary_case("phase16-stale")
        first = self.pipeline.execute(*first_args[:4])
        stale = self.pipeline.execute(*second_args[:4])
        OrdinaryTurnCommitCoordinator(self.real.sandbox.store).commit(first)
        with self.assertRaises(StateConflictError):
            OrdinaryTurnCommitCoordinator(self.real.sandbox.store).commit(stale)
        self.assertEqual(
            self.real.sandbox.store.visible_artifact_ids(self.real.sandbox.branch_id),
            (first.accepted_artifact.artifact_id,),
        )

    def test_fork_created_after_commit_retains_committed_ordinary_artifact(self) -> None:
        args = self.ordinary_case("phase16-fork")
        result = self.pipeline.execute(*args[:4])
        OrdinaryTurnCommitCoordinator(self.real.sandbox.store).commit(result)
        child = real_support.ident(IdKind.BRANCH, "phase16-child")
        self.real.sandbox.store.fork_branch(self.real.sandbox.branch_id, child)
        expected = (result.accepted_artifact.artifact_id,)
        self.assertEqual(
            self.real.sandbox.store.visible_artifact_ids(self.real.sandbox.branch_id),
            expected,
        )
        self.assertEqual(self.real.sandbox.store.visible_artifact_ids(child), expected)

    def test_regeneration_commits_sibling_while_prior_fork_keeps_old_artifact(self) -> None:
        first_args = self.ordinary_case("phase16-regeneration-first")
        first = self.pipeline.execute(*first_args[:4])
        OrdinaryTurnCommitCoordinator(self.real.sandbox.store).commit(first)
        child = real_support.ident(IdKind.BRANCH, "phase16-regeneration-child")
        self.real.sandbox.store.fork_branch(self.real.sandbox.branch_id, child)

        first_id = first.accepted_artifact.artifact_id
        replacement_args = self.ordinary_case(
            "phase16-regeneration-replacement",
            source="Ted asks for a different continuation of the same moment.",
            expected_generation=1,
            expected_parent_artifact_id=first_id,
            publication_mode=ArtifactPublicationMode.REGENERATE,
            publication_parent_artifact_id=None,
            replaces_artifact_id=first_id,
        )
        replacement = self.pipeline.execute(*replacement_args[:4])
        committed = OrdinaryTurnCommitCoordinator(self.real.sandbox.store).commit(
            replacement
        )
        self.assertEqual(committed.receipt.expected_previous_artifact_id, first_id)
        self.assertEqual(replacement.accepted_artifact.parent_artifact_id, None)
        self.assertEqual(
            self.real.sandbox.store.visible_artifact_ids(self.real.sandbox.branch_id),
            (replacement.accepted_artifact.artifact_id,),
        )
        self.assertEqual(
            self.real.sandbox.store.visible_artifact_ids(child),
            (first_id,),
        )


if __name__ == "__main__":
    unittest.main()
