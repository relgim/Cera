from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
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
from cera.composer import (
    CharacterMoveRealization,
    CharacterRealization,
    ComposerCandidate,
    ComposerCoordinator,
    ComposerSourcePacket,
    ComposerSourceUnit,
    ComposerSubmission,
    CompositionMode,
    FakeComposerFixture,
    FakeSceneComposerPort,
    ProtectedSourceEnvelope,
    RealizationKind,
    RealizationManifest,
    SceneComposerRequest,
    SourceUnitCoverage,
)
from cera.contracts import (
    BeatState,
    CharacterMove,
    CurrentSegment,
    DecisionRoute,
    FutureSegment,
    SceneDecision,
    SequenceBeat,
    SourceUnitClassification,
)
from cera.evaluation import RealGenesisSandbox
from cera.evidence import (
    EvidenceFetchRequest,
    EvidenceSearchRequest,
    EvidenceService,
    EvidenceWorldMode,
)
from cera.errors import ErrorCode, EvidenceServiceError
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
    TurnKernelFailure,
)
from cera.reasoner import (
    FakeReasonerFixture,
    FakeSceneReasonerPort,
    FakeToolCall,
    FakeToolOperation,
    InterventionReason,
    ParticipationRole,
    ParticipationSelection,
    ReasonerCoordinator,
    ReasonerEvidenceCitation,
    ReasonerExecutionFailure,
    ReasonerOutcome,
    ReasonerOutcomeStatus,
    ReasonerSeedDossier,
    ReasonerSourceMode,
    ReasonerSourceUnit,
    ReasonerSourceView,
    SceneReasonerRequest,
)
from cera.serialization import domain_sha256, text_sha256


ROOT = Path(__file__).resolve().parents[1]


def ident(kind: IdKind, suffix: str) -> TypedId:
    return TypedId(kind, f"real-{suffix}")


class RealGenesisRetrievalCalibrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sandbox = RealGenesisSandbox.create(ROOT)
        self.addCleanup(self.sandbox.close)

    def test_creator_package_installs_only_inside_auto_deleting_sandbox(self) -> None:
        self.assertTrue(self.sandbox.database_path.is_file())
        self.assertTrue(
            self.sandbox.database_path.is_relative_to(Path(self.sandbox.temporary.name))
        )
        self.assertFalse(self.sandbox.installed.exact_replay)
        replay = self.sandbox.repository.compile_and_install(
            ROOT / "genesis" / "packages" / "hanezawa_core_v1_1",
            self.sandbox.authorization,
            transaction_id=TypedId(IdKind.TRANSACTION, "offline-genesis-install"),
            idempotency_key="offline-genesis-install",
        )
        self.assertTrue(replay.exact_replay)
        self.assertEqual(replay.receipt, self.sandbox.installed.receipt)
        self.assertEqual(
            len(self.sandbox.store.active_genesis_records(self.sandbox.revision_id)),
            503,
        )

    def test_every_advertised_real_genesis_section_is_expandable(self) -> None:
        for record in self.sandbox.store.active_genesis_records(
            self.sandbox.revision_id
        ):
            with self.subTest(record_id=str(record.record_id)):
                document = EvidenceService._from_genesis(
                    record, self.sandbox.revision_id
                )
                sections = json.loads(document.sections_json)
                self.assertTrue(set(record.expandable_sections).issubset(sections))

    def test_indirect_memory_cue_searches_then_expands_exact_owner_memory(self) -> None:
        hana = CHARACTER_IDS["Hana"]
        snapshot = self.sandbox.open_snapshot(
            "hana-missed-calls", scope=self.sandbox.character_scope("Hana")
        )
        search = self.sandbox.service.search_evidence(
            snapshot,
            EvidenceSearchRequest(
                terms=("missed", "calls"), entity_ids=(hana,), limit=5
            ),
        )
        expected = self.sandbox.record_with_payload_value("memory_id", "H-M08")
        self.assertEqual(
            tuple(item.metadata.record_id for item in search.references),
            (expected.record_id,),
        )
        exact = self.sandbox.service.fetch_evidence(
            snapshot,
            EvidenceFetchRequest(
                (search.references[0].evidence_id,),
                (
                    "remembered_content",
                    "learned_meaning_or_belief_pressure",
                    "possible_retrieval_cues",
                ),
            ),
        )
        sections = json.loads(exact.exact_records[0].sections_json)
        self.assertIn("maintaining faith", sections["remembered_content"])
        self.assertIn("Missed calls", sections["possible_retrieval_cues"])
        self.assertLess(exact.receipt.cumulative_snapshot_bytes, 65_536)

    def test_affair_and_surveillance_knowledge_do_not_leak_by_character_view(self) -> None:
        en07 = self.sandbox.record_with_payload_value("event_id", "EN07")
        en08 = self.sandbox.record_with_payload_value("event_id", "EN08")
        st06 = self.sandbox.record_with_payload_value("event_id", "ST06")
        expectations = {
            "Hana": {en07.record_id: False, en08.record_id: False, st06.record_id: True},
            "Mia": {en07.record_id: False, en08.record_id: False, st06.record_id: False},
            "Sakura": {en07.record_id: False, en08.record_id: True, st06.record_id: True},
            "Enne": {en07.record_id: True, en08.record_id: True, st06.record_id: True},
        }
        for name, expected_records in expectations.items():
            snapshot = self.sandbox.open_snapshot(
                f"privacy-{name.casefold()}",
                scope=self.sandbox.character_scope(name),
            )
            for record_id, allowed in expected_records.items():
                evidence_id = self.sandbox.evidence_id(snapshot, record_id)
                with self.subTest(name=name, record_id=str(record_id)):
                    if allowed:
                        exact = self.sandbox.service.fetch_evidence(
                            snapshot,
                            EvidenceFetchRequest((evidence_id,), ("claim", "knowledge")),
                        )
                        self.assertEqual(exact.exact_records[0].metadata.record_id, record_id)
                    else:
                        with self.assertRaises(EvidenceServiceError) as denied:
                            self.sandbox.service.fetch_evidence(
                                snapshot,
                                EvidenceFetchRequest((evidence_id,), ("claim",)),
                            )
                        self.assertIs(
                            denied.exception.code, ErrorCode.EVIDENCE_ACCESS_DENIED
                        )

    def test_directional_ted_relationship_expands_without_whole_profile(self) -> None:
        hana = CHARACTER_IDS["Hana"]
        snapshot = self.sandbox.open_snapshot(
            "hana-ted-edge", scope=self.sandbox.character_scope("Hana")
        )
        search = self.sandbox.service.search_evidence(
            snapshot,
            EvidenceSearchRequest(
                entity_ids=(hana,), tags=("relationship", "ted"), limit=5
            ),
        )
        self.assertEqual(len(search.references), 1)
        exact = self.sandbox.service.fetch_evidence(
            snapshot,
            EvidenceFetchRequest(
                (search.references[0].evidence_id,),
                ("known", "emotional_stance", "boundary", "risk"),
            ),
        )
        sections = json.loads(exact.exact_records[0].sections_json)
        self.assertEqual(
            set(sections), {"known", "emotional_stance", "boundary", "risk"}
        )
        self.assertNotIn("source_text", sections)

    def test_real_followup_budget_is_deterministic_and_fail_closed(self) -> None:
        snapshot = self.sandbox.open_snapshot("budget")
        for terms in (
            ("missed", "calls"),
            ("fruit",),
            ("surveillance",),
            ("paying", "resident"),
        ):
            batch = self.sandbox.service.search_evidence(
                snapshot, EvidenceSearchRequest(terms=terms, limit=5)
            )
            self.assertLessEqual(batch.receipt.cumulative_snapshot_bytes, 65_536)
        with self.assertRaises(EvidenceServiceError) as limited:
            self.sandbox.service.search_evidence(
                snapshot, EvidenceSearchRequest(terms=("fifth",), limit=1)
            )
        self.assertIs(limited.exception.code, ErrorCode.EVIDENCE_LIMIT_EXCEEDED)


class RealGenesisReasonerComposerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sandbox = RealGenesisSandbox.create(ROOT)
        self.addCleanup(self.sandbox.close)
        self.kernel = TurnKernel(self.sandbox.service)
        self.reasoner = ReasonerCoordinator(self.sandbox.service, self.kernel)
        self.composer = ComposerCoordinator()
        self.ted = self.sandbox.protected_user_id()

    def command(
        self,
        suffix: str,
        *,
        responders: tuple[TypedId, ...],
        source_text: str,
        preflight: PreflightAuthority | None = None,
        expected_generation: int = 0,
        expected_parent_artifact_id: TypedId | None = None,
    ) -> TurnIntakeCommand:
        present = (self.ted, *tuple(CHARACTER_IDS.values()))
        return TurnIntakeCommand(
            world_id=self.sandbox.world_id,
            request_id=ident(IdKind.REQUEST, suffix),
            session_id=self.sandbox.session_id(),
            branch_id=self.sandbox.branch_id,
            expected_generation=expected_generation,
            expected_parent_artifact_id=expected_parent_artifact_id,
            genesis_revision_id=self.sandbox.revision_id,
            protected_user_id=self.ted,
            present_character_ids=present,
            requested_responding_npc_ids=responders,
            source_units=(
                IntakeSourceUnit(SourceUnitClassification.MESSAGE, source_text),
            ),
            requested_route_hints=(),
            idempotency_key=f"offline-{suffix}",
            preflight_authority=preflight
            or PreflightAuthority(RequestedContentClass.ORDINARY),
            world_mode=EvidenceWorldMode.REAL,
        )

    def relationship_record(self, from_name: str):
        from_id = CHARACTER_IDS[from_name]
        return next(
            record
            for record in self.sandbox.compiled.records
            if record.record_type is GenesisRecordType.RELATIONSHIP_EDGE
            and record.relationship_from_id == from_id
            and record.relationship_to_id == self.ted
        )

    def adult_identity_record(self, name: str):
        character_id = CHARACTER_IDS[name]
        for record in self.sandbox.compiled.records:
            document = EvidenceService._from_genesis(record, self.sandbox.revision_id)
            sections = json.loads(document.sections_json)
            if (
                character_id in document.subject_ids
                and sections["route_flags"].get("adult_eligibility")
                == "confirmed_identity_eligible"
            ):
                return record
        raise LookupError(f"adult identity record not found for {name}")

    def reasoner_request_and_result(
        self,
        suffix: str,
        responders: tuple[TypedId, ...],
        source_text: str,
        evidence_records,
        *,
        use_exact_seed: bool = False,
        fetch_sections: tuple[str, ...] = ("claim", "knowledge"),
        expected_generation: int = 0,
        expected_parent_artifact_id: TypedId | None = None,
    ):
        prepared = self.kernel.prepare_turn(
            self.command(
                suffix,
                responders=responders,
                source_text=source_text,
                expected_generation=expected_generation,
                expected_parent_artifact_id=expected_parent_artifact_id,
            ),
            access_scope=self.sandbox.system_scope(),
        )
        evidence_ids = tuple(
            self.sandbox.evidence_id(prepared.evidence_snapshot, record.record_id)
            for record in evidence_records
        )
        exact_seed_evidence = ()
        scripted_tool_calls = (
            FakeToolCall(
                FakeToolOperation.FETCH_EVIDENCE,
                EvidenceFetchRequest(evidence_ids, fetch_sections),
            ),
        )
        if use_exact_seed:
            exact_seed_evidence = self.sandbox.service.fetch_evidence(
                prepared.evidence_snapshot,
                EvidenceFetchRequest(evidence_ids, fetch_sections),
            ).exact_records
            scripted_tool_calls = ()
        moves = tuple(
            CharacterMove(
                character_id=character_id,
                perception="The resident addressed the family in the shared room.",
                selected_intent=f"Respond according to {character_id.value}'s established stance.",
                action_direction="Give a bounded character-owned response.",
                evidence_ids=(evidence_ids[index],),
                knowledge_constraints=("Do not transfer another character's private state.",),
            )
            for index, character_id in enumerate(responders)
        )
        beats = tuple(
            SequenceBeat(
                beat_id=ident(IdKind.BEAT, f"{suffix}-{index}"),
                actor_id=character_id,
                state=BeatState.ATTEMPTED,
                neutral_event="The character responds from her current relationship stance.",
                evidence_ids=(evidence_ids[index],),
            )
            for index, character_id in enumerate(responders)
        )
        decision = SceneDecision(
            schema_version=SceneDecision.SCHEMA_VERSION,
            decision_id=ident(IdKind.DECISION, suffix),
            route=DecisionRoute.ORDINARY,
            scene_intent="Continue the household arrival without authoring Ted's next choice.",
            responding_npc_ids=responders,
            floor_owner_id=responders[0],
            character_moves=moves,
            current_segment=CurrentSegment(
                segment_id=ident(IdKind.SEGMENT, f"{suffix}-current"),
                ordered_beats=beats,
                stop_before="Ted's next unsupplied meaningful choice.",
            ),
            future_segments=(
                FutureSegment(
                    segment_id=ident(IdKind.SEGMENT, f"{suffix}-future"),
                    status="conditional_plan_only",
                    activation_conditions=("Ted supplies another choice.",),
                    invalidation_conditions=("The branch or cast changes.",),
                    possible_consequences=("The household response may continue.",),
                    open_user_choice="Ted decides what to do next.",
                ),
            ),
            writer_must_preserve=("Preserve the selected floor and stop boundary.",),
            uncertainties=(),
            prohibited_inferences=("Do not invent Ted's private state.",),
            advisory_state_candidates=(),
        )
        outcome = ReasonerOutcome(
            schema_version=ReasonerOutcome.SCHEMA_VERSION,
            status=ReasonerOutcomeStatus.DECISION_READY,
            decision=decision,
            participation=tuple(
                ParticipationSelection(
                    character_id=character_id,
                    role=(
                        ParticipationRole.LEAD
                        if index == 0
                        else ParticipationRole.SECONDARY
                    ),
                    intervention_reason=(
                        InterventionReason.FLOOR_OWNER
                        if index == 0
                        else InterventionReason.DIRECT_STAKE
                    ),
                    evidence_ids=(evidence_ids[index],),
                )
                for index, character_id in enumerate(responders)
            ),
            hard_citations=tuple(
                ReasonerEvidenceCitation(
                    evidence_id=evidence_ids[index],
                    record_id=record.record_id,
                    record_version=record.record_version,
                )
                for index, record in enumerate(evidence_records)
            ),
            insufficiencies=(),
            blocker_code=None,
            advisory_state_deltas=(),
            protected_user_boundary_acknowledged=True,
        )
        source_view = ReasonerSourceView(
            mode=ReasonerSourceMode.ORDINARY_EXACT,
            source_sha256=prepared.request.source_sha256,
            units=(
                ReasonerSourceUnit(
                    source_unit_id=prepared.request.source_units[0].source_unit_id,
                    classification=SourceUnitClassification.MESSAGE,
                    safe_text=source_text,
                ),
            ),
            contains_exact_protected_adult_prose=False,
        )
        request = SceneReasonerRequest(
            schema_version=SceneReasonerRequest.SCHEMA_VERSION,
            prepared_turn=prepared,
            source_view=source_view,
            seed_dossier=ReasonerSeedDossier(
                snapshot_token=prepared.evidence_snapshot.snapshot_token,
                aware_character_ids=responders,
                exact_seed_evidence=exact_seed_evidence,
                scene_anchors=("Hanezawa House shared room.",),
                explicit_unknowns=("Ted's next choice is unknown.",),
                prohibited_inferences=("Do not author Ted's private state.",),
            ),
            hard_boundaries=(
                "Use only snapshot-authorized evidence.",
                "Do not author the protected user.",
            ),
        )
        fixture = FakeReasonerFixture(
            f"real-{suffix}",
            scripted_tool_calls,
            outcome,
        )
        result = self.reasoner.execute(
            request, FakeSceneReasonerPort(fixture), fixture=fixture
        )
        return prepared, request, result

    def compose(self, suffix, prepared, reasoner_request, reasoner_result, source_text):
        source_unit = ComposerSourceUnit(
            source_unit_id=prepared.request.source_units[0].source_unit_id,
            classification=SourceUnitClassification.MESSAGE,
            exact_text=source_text,
            protected_user_allowed_kinds=(RealizationKind.ACTION,),
            required_state=BeatState.ATTEMPTED,
            participant_ids=reasoner_result.outcome.decision.responding_npc_ids,
        )
        request = SceneComposerRequest(
            schema_version=SceneComposerRequest.SCHEMA_VERSION,
            prepared_turn=prepared,
            reasoner_outcome=reasoner_result.outcome,
            reasoner_receipt=reasoner_result.receipt,
            source_packet=ComposerSourcePacket(
                mode=CompositionMode.ORDINARY,
                source_sha256=prepared.request.source_sha256,
                ordinary_units=(source_unit,),
                protected_envelope=None,
                reasoner_safe_ledger_sha256=reasoner_request.source_view.source_view_sha256,
            ),
            selected_npc_ids=reasoner_result.outcome.decision.responding_npc_ids,
            scene_scope="Immediate provider-free Hanezawa household continuation.",
            response_profile_version="offline-real-genesis-v1",
            continuity_references=(),
            creator_event_coverage_required=False,
            hard_boundaries=(
                "Preserve the validated decisions.",
                "Stop before Ted's next unsupplied choice.",
            ),
            adult_binding=None,
        )
        decision = reasoner_result.outcome.decision
        story = "Hana welcomes Ted while Mia waits for her turn to speak."
        candidate = ComposerCandidate(
            schema_version=ComposerCandidate.SCHEMA_VERSION,
            candidate_id=ident(IdKind.COMPOSER_CANDIDATE, suffix),
            story_text=story,
            story_text_sha256=text_sha256(story),
            complete_core=True,
            is_outline=False,
            is_partial_draft=False,
            awaits_detailer=False,
            contains_internal_labels=False,
            contains_ui_markup=False,
            contains_provider_diagnostics=False,
        )
        manifest = RealizationManifest(
            schema_version=RealizationManifest.SCHEMA_VERSION,
            manifest_id=ident(IdKind.REALIZATION_MANIFEST, suffix),
            candidate_sha256=candidate.candidate_sha256,
            decision_sha256=request.decision_sha256,
            sequence_plan_sha256=request.sequence_plan_sha256,
            floor_owner_id=decision.floor_owner_id,
            move_realizations=tuple(
                CharacterMoveRealization(
                    move.character_id,
                    text_sha256(move.selected_intent),
                    text_sha256(move.action_direction),
                )
                for move in decision.character_moves
            ),
            participant_realizations=tuple(
                CharacterRealization(character_id, True, False)
                for character_id in decision.responding_npc_ids
            ),
            realized_beat_ids=tuple(
                beat.beat_id for beat in decision.current_segment.ordered_beats
            ),
            source_unit_coverage=(),
            character_spans=(),
            semantic_inferences=(),
            terminal_state_preserved=True,
            stops_before_protected_user_choice=True,
            introduced_major_objective_or_participant=False,
        )
        submission = ComposerSubmission(candidate, manifest, ())
        fixture = FakeComposerFixture(f"real-{suffix}", submission)
        result = self.composer.execute(
            request, FakeSceneComposerPort(fixture), fixture=fixture
        )
        return request, result

    def test_real_multi_character_ordinary_turn_reaches_in_memory_acceptance(self) -> None:
        responders = (CHARACTER_IDS["Hana"], CHARACTER_IDS["Mia"])
        source = "Ted enters the shared room and greets Hana and Mia."
        prepared, reasoner_request, reasoner_result = self.reasoner_request_and_result(
            "ordinary-multi",
            responders,
            source,
            tuple(self.relationship_record(name) for name in ("Hana", "Mia")),
        )
        _, composer_result = self.compose(
            "ordinary-multi",
            prepared,
            reasoner_request,
            reasoner_result,
            source,
        )
        self.assertEqual(reasoner_result.receipt.external_provider_calls, 0)
        self.assertEqual(composer_result.composer_receipt.external_provider_calls, 0)
        self.assertEqual(composer_result.validation_receipt.status, "accepted_in_memory")
        self.assertFalse(composer_result.validation_receipt.story_state_committed)
        self.assertEqual(self.sandbox.store.table_count("artifacts"), 0)

    def test_exact_seed_dossier_carries_content_without_reasoner_tool_call(self) -> None:
        hana = CHARACTER_IDS["Hana"]
        _, request, result = self.reasoner_request_and_result(
            "exact-seed",
            (hana,),
            "Ted asks Hana how she feels about his arrival.",
            (self.relationship_record("Hana"),),
            use_exact_seed=True,
        )
        self.assertEqual(result.receipt.tool_call_count, 0)
        self.assertEqual(result.receipt.evidence_lookup_receipt_ids, ())
        self.assertEqual(len(request.seed_dossier.exact_seed_evidence), 1)
        sections = json.loads(
            request.seed_dossier.exact_seed_evidence[0].sections_json
        )
        self.assertEqual(set(sections), {"claim", "knowledge"})
        self.assertIn("Maternal hospitality", sections["claim"])

    def test_real_hana_identity_completes_provider_free_adult_contract_route(self) -> None:
        suffix = "adult-hana"
        hana = CHARACTER_IDS["Hana"]
        identity_record = self.adult_identity_record("Hana")
        relationship_record = self.relationship_record("Hana")
        snapshot = self.sandbox.service.open_snapshot(
            request_id=ident(IdKind.REQUEST, suffix),
            world_id=self.sandbox.world_id,
            branch_id=self.sandbox.branch_id,
            access_scope=self.sandbox.system_scope(),
            world_mode=EvidenceWorldMode.REAL,
        )
        identity_evidence_id = self.sandbox.evidence_id(
            snapshot, identity_record.record_id
        )
        relationship_evidence_id = self.sandbox.evidence_id(
            snapshot, relationship_record.record_id
        )
        source_text = "Ted and Hana continue a mutually agreed intimate moment."
        command = self.command(
            suffix,
            responders=(hana,),
            source_text=source_text,
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
        prepared = self.kernel.prepare_turn(
            command, access_scope=self.sandbox.system_scope()
        )
        self.assertEqual(prepared.route.value, "consent_valid_adult")
        self.assertEqual(prepared.provider_calls_made, 0)

        prepared_unit = prepared.request.source_units[0]
        route_unit = AdultRouteSourceUnit(
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
        )
        route_input = AdultRouteInput(
            prepared_turn=prepared,
            participant_ids=(hana,),
            scenario_kind=AdultScenarioKind.CONSENSUAL_ACTIVITY,
            scenario_source_unit_ids=(prepared_unit.source_unit_id,),
            source_units=(route_unit,),
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
        envelope = ProtectedSourceEnvelope(
            protected_source_id=prepared.request.raw_source_ref,
            source_sha256=prepared.request.source_sha256,
            units=(
                ComposerSourceUnit(
                    source_unit_id=prepared_unit.source_unit_id,
                    classification=SourceUnitClassification.MESSAGE,
                    exact_text=source_text,
                    protected_user_allowed_kinds=(RealizationKind.ACTION,),
                    required_state=BeatState.ATTEMPTED,
                    participant_ids=(hana,),
                ),
            ),
        )
        preparation = AdultRouteCoordinator(self.sandbox.service).prepare(
            route_input, envelope
        )

        move = CharacterMove(
            character_id=hana,
            perception="Hana recognizes the mutually agreed private moment.",
            selected_intent="Continue only within the explicitly established boundary.",
            action_direction="Respond in character without deciding Ted's next choice.",
            evidence_ids=(relationship_evidence_id,),
            knowledge_constraints=("Hana may use only her authorized perspective.",),
        )
        beat = SequenceBeat(
            beat_id=ident(IdKind.BEAT, f"{suffix}-current"),
            actor_id=hana,
            state=BeatState.ATTEMPTED,
            neutral_event="Hana gives a bounded, character-owned response.",
            evidence_ids=(relationship_evidence_id,),
        )
        decision = SceneDecision(
            schema_version=SceneDecision.SCHEMA_VERSION,
            decision_id=ident(IdKind.DECISION, suffix),
            route=DecisionRoute.CONSENT_VALID_ADULT,
            scene_intent="Continue the consent-valid scene within the established boundary.",
            responding_npc_ids=(hana,),
            floor_owner_id=hana,
            character_moves=(move,),
            current_segment=CurrentSegment(
                segment_id=ident(IdKind.SEGMENT, f"{suffix}-current"),
                ordered_beats=(beat,),
                stop_before="Ted's next unsupplied meaningful choice.",
            ),
            future_segments=(
                FutureSegment(
                    segment_id=ident(IdKind.SEGMENT, f"{suffix}-future"),
                    status="conditional_plan_only",
                    activation_conditions=("Ted supplies another consent-valid choice.",),
                    invalidation_conditions=("Consent or capacity changes.",),
                    possible_consequences=("The private interaction may continue.",),
                    open_user_choice="Ted decides what to do next.",
                ),
            ),
            writer_must_preserve=("Preserve consent and the protected-user boundary.",),
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
                    character_id=hana,
                    role=ParticipationRole.LEAD,
                    intervention_reason=InterventionReason.FLOOR_OWNER,
                    evidence_ids=(relationship_evidence_id,),
                ),
            ),
            hard_citations=(
                ReasonerEvidenceCitation(
                    evidence_id=relationship_evidence_id,
                    record_id=relationship_record.record_id,
                    record_version=relationship_record.record_version,
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
                snapshot_token=prepared.evidence_snapshot.snapshot_token,
                aware_character_ids=(hana,),
                exact_seed_evidence=(),
                scene_anchors=("A private room in the Hanezawa home.",),
                explicit_unknowns=("Ted's next choice is unknown.",),
                prohibited_inferences=("Do not infer consent from bodily response.",),
            ),
            hard_boundaries=(
                "Use the non-graphic causal ledger.",
                "Do not author the protected user.",
            ),
        )
        reasoner_fixture = FakeReasonerFixture(
            f"real-{suffix}",
            (
                FakeToolCall(
                    FakeToolOperation.FETCH_EVIDENCE,
                    EvidenceFetchRequest(
                        (relationship_evidence_id,), ("claim", "knowledge")
                    ),
                ),
            ),
            outcome,
        )
        reasoner_result = self.reasoner.execute(
            reasoner_request,
            FakeSceneReasonerPort(reasoner_fixture),
            fixture=reasoner_fixture,
        )

        mechanics_request = AdultMechanicsRequest(
            schema_version=AdultMechanicsRequest.SCHEMA_VERSION,
            prepared_turn=prepared,
            adult_authority_id=preparation.authority.adult_authority_id,
            authority_sha256=preparation.authority.authority_sha256,
            ledger_sha256=preparation.ledger.ledger_sha256,
            context_sha256=preparation.context.context_sha256,
            reasoner_outcome=reasoner_result.outcome,
            reasoner_receipt=reasoner_result.receipt,
            unit_ids=(prepared_unit.source_unit_id,),
            hard_boundaries=(
                "Return mechanics only, never story prose.",
                "Do not replace Scene Reasoner psychology.",
            ),
        )
        mechanics_proposal = AdultMechanicsProposal(
            schema_version=AdultMechanicsProposal.SCHEMA_VERSION,
            adult_plan_id=ident(IdKind.ADULT_PLAN, suffix),
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
                    source_unit_id=prepared_unit.source_unit_id,
                    progression_state=BeatState.ATTEMPTED,
                    participant_ids=(hana,),
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
            f"real-{suffix}", mechanics_proposal
        )
        mechanics_result = AdultMechanicsCoordinator().execute(
            preparation,
            mechanics_request,
            FakeAdultMechanicsPort(mechanics_fixture),
            fixture=mechanics_fixture,
        )

        composer_request = SceneComposerRequest(
            schema_version=SceneComposerRequest.SCHEMA_VERSION,
            prepared_turn=prepared,
            reasoner_outcome=reasoner_result.outcome,
            reasoner_receipt=reasoner_result.receipt,
            source_packet=preparation.composer_source_packet,
            selected_npc_ids=(hana,),
            scene_scope="Immediate provider-free consent-valid continuation.",
            response_profile_version="offline-real-adult-contract-v1",
            continuity_references=(),
            creator_event_coverage_required=True,
            hard_boundaries=(
                "Preserve the validated decision.",
                "Stop before Ted's next unsupplied choice.",
            ),
            adult_binding=build_adult_composer_binding(
                preparation, mechanics_result
            ),
        )
        story = "The protected source unit is represented; Hana gives her bounded response."
        candidate = ComposerCandidate(
            schema_version=ComposerCandidate.SCHEMA_VERSION,
            candidate_id=ident(IdKind.COMPOSER_CANDIDATE, suffix),
            story_text=story,
            story_text_sha256=text_sha256(story),
            complete_core=True,
            is_outline=False,
            is_partial_draft=False,
            awaits_detailer=False,
            contains_internal_labels=False,
            contains_ui_markup=False,
            contains_provider_diagnostics=False,
        )
        covered = "The protected source unit is represented"
        manifest = RealizationManifest(
            schema_version=RealizationManifest.SCHEMA_VERSION,
            manifest_id=ident(IdKind.REALIZATION_MANIFEST, suffix),
            candidate_sha256=candidate.candidate_sha256,
            decision_sha256=composer_request.decision_sha256,
            sequence_plan_sha256=composer_request.sequence_plan_sha256,
            floor_owner_id=hana,
            move_realizations=(
                CharacterMoveRealization(
                    hana,
                    text_sha256(move.selected_intent),
                    text_sha256(move.action_direction),
                ),
            ),
            participant_realizations=(CharacterRealization(hana, True, False),),
            realized_beat_ids=(beat.beat_id,),
            source_unit_coverage=(
                SourceUnitCoverage(
                    source_unit_id=prepared_unit.source_unit_id,
                    ordinal=0,
                    start=0,
                    end=len(covered),
                    preserved_state=BeatState.ATTEMPTED,
                ),
            ),
            character_spans=(),
            semantic_inferences=(),
            terminal_state_preserved=True,
            stops_before_protected_user_choice=True,
            introduced_major_objective_or_participant=False,
        )
        composer_fixture = FakeComposerFixture(
            f"real-{suffix}", ComposerSubmission(candidate, manifest, ())
        )
        composer_result = self.composer.execute(
            composer_request,
            FakeSceneComposerPort(composer_fixture),
            fixture=composer_fixture,
        )
        self.assertEqual(preparation.receipt.external_provider_calls, 0)
        self.assertEqual(reasoner_result.receipt.external_provider_calls, 0)
        self.assertEqual(mechanics_result.receipt.external_provider_calls, 0)
        self.assertEqual(composer_result.composer_receipt.external_provider_calls, 0)
        self.assertEqual(composer_result.validation_receipt.status, "accepted_in_memory")
        self.assertFalse(composer_result.validation_receipt.story_state_committed)
        self.assertEqual(self.sandbox.store.table_count("artifacts"), 0)

    def test_real_limited_knowledge_cannot_drive_hana_decision(self) -> None:
        hana = CHARACTER_IDS["Hana"]
        enne_event = self.sandbox.record_with_payload_value("event_id", "EN07")
        with self.assertRaises(ReasonerExecutionFailure) as denied:
            self.reasoner_request_and_result(
                "knowledge-transfer",
                (hana,),
                "Ted asks Hana whether she knows what Enne discovered.",
                (enne_event,),
            )
        self.assertIs(
            denied.exception.envelope.error_code,
            ErrorCode.REASONER_CONTRACT_INVALID,
        )
        self.assertIn("transferred", denied.exception.envelope.message)

    def test_real_blocker_stops_before_fake_reasoner_or_composer(self) -> None:
        before = tuple(
            self.sandbox.store.table_count(table)
            for table in ("sources", "artifacts", "authority_records")
        )
        command = replace(
            self.command(
                "blocked",
                responders=(CHARACTER_IDS["Hana"],),
                source_text="A neutral setup reaches a blocked crossing.",
            ),
            source_units=(
                IntakeSourceUnit(SourceUnitClassification.CONTEXT, "Neutral setup."),
                IntakeSourceUnit(SourceUnitClassification.EVENT, "Blocked crossing."),
            ),
            preflight_authority=PreflightAuthority(
                requested_content_class=RequestedContentClass.ADULT,
                blocked_nonconsensual_crossing_established=True,
                blocker_boundary_unit_index=1,
                facts_established_before_blocker=("Neutral setup occurred.",),
            ),
        )
        with self.assertRaises(TurnKernelFailure) as blocked:
            self.kernel.prepare_turn(
                command, access_scope=self.sandbox.system_scope()
            )
        self.assertIs(
            blocked.exception.envelope.error_code,
            ErrorCode.BLOCKED_NONCONSENSUAL_EVENT,
        )
        self.assertFalse(blocked.exception.envelope.story_state_committed)
        self.assertEqual(
            before,
            tuple(
                self.sandbox.store.table_count(table)
                for table in ("sources", "artifacts", "authority_records")
            ),
        )


if __name__ == "__main__":
    unittest.main()
