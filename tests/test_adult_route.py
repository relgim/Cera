from __future__ import annotations

from dataclasses import replace
import unittest

from cera.adult import (
    AdultContentFamily,
    AdultContentTrigger,
    AdultContextSelector,
    AdultMechanicsCoordinator,
    AdultMechanicsFailure,
    AdultMechanicsProposal,
    AdultMechanicsRequest,
    AdultObservableCode,
    AdultRouteCoordinator,
    AdultRouteFailure,
    AdultRouteInput,
    AdultRouteSourceUnit,
    AdultScenarioKind,
    AdultSemanticAssertion,
    AdultSemanticDimension,
    AdultUnitTreatment,
    ContentActivationAuthority,
    FakeAdultMechanicsFixture,
    FakeAdultMechanicsPort,
    ProviderCapabilityStatus,
    SemanticAssertionProvenance,
    SemanticAssertionState,
    build_adult_composer_binding,
)
from cera.composer import (
    CharacterMoveRealization,
    CharacterRealization,
    ComposerCandidate,
    ComposerCoordinator,
    ComposerSourceUnit,
    ComposerSubmission,
    FakeComposerFixture,
    FakeSceneComposerPort,
    ProtectedSourceEnvelope,
    RealizationKind,
    RealizationManifest,
    SceneComposerRequest,
    SourceUnitCoverage,
)
from cera.config import Environment
from cera.contracts import BeatState, DecisionRoute, SourceUnitClassification
from cera.evidence import EvidenceFetchRequest, EvidenceSearchRequest, EvidenceService
from cera.errors import ConfigurationError, ContractValidationError, ErrorCode, IdentityError
from cera.ids import IdKind, TypedId
from cera.kernel import (
    AdultCapacityStatus,
    AdultConsentStatus,
    AdultFreedomToStop,
    AdultIdentityStatus,
    AdultPressureStatus,
    IntakeSourceUnit,
    ParticipantAdultAuthority,
)
from cera.reasoner import (
    FakeReasonerFixture,
    FakeSceneReasonerPort,
    FakeToolCall,
    FakeToolOperation,
)
from cera.serialization import domain_sha256, text_sha256, to_primitive
import tests.test_reasoner as reasoner_test_support


ident = reasoner_test_support.ident


class AdultRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.support = reasoner_test_support.ReasonerTests("runTest")
        self.support.setUp()
        self.addCleanup(self.support.doCleanups)
        self.alpha = self.support.alpha
        self.beta = self.support.beta
        self.protected_user = self.support.ted
        self.route_coordinator = AdultRouteCoordinator(self.support.service)

    def prepared_and_input(
        self,
        suffix: str,
        *,
        source_texts: tuple[str, ...] | None = None,
        states: tuple[BeatState, ...] | None = None,
        scenario: AdultScenarioKind = AdultScenarioKind.CONSENSUAL_ACTIVITY,
        triggers: tuple[AdultContentTrigger, ...] | None = None,
        assertions: tuple[AdultSemanticAssertion, ...] = (),
    ):
        source_texts = source_texts or (f"Synthetic restricted source {suffix} ZXQ-941.",)
        states = states or tuple(BeatState.ATTEMPTED for _ in source_texts)
        command = self.support.base.command(
            f"adult-route-{suffix}",
            responders=(self.alpha,),
            units=tuple(
                IntakeSourceUnit(SourceUnitClassification.EVENT, text)
                for text in source_texts
            ),
            preflight=self.support.base.adult_authority(),
        )
        prepared = self.support.kernel.prepare_turn(
            command, access_scope=self.support.base.access
        )
        source_units = tuple(
            AdultRouteSourceUnit(
                source_unit_id=prepared.request.source_units[index].source_unit_id,
                source_unit_sha256=prepared.request.source_units[index].sha256,
                ordinal=index,
                progression_state=states[index],
                participant_ids=(self.alpha,),
                observable_code=AdultObservableCode.SOURCE_AUTHORED_PROTECTED_EVENT,
                private_state_owner_ids=(),
                consent_status=AdultConsentStatus.GRANTED,
                capacity_status=AdultCapacityStatus.CLEAR,
                pressure_status=AdultPressureStatus.NONE,
                freedom_to_stop=AdultFreedomToStop.PRESENT,
            )
            for index in range(len(source_texts))
        )
        if triggers is None:
            triggers = (
                AdultContentTrigger(
                    family=AdultContentFamily.GENERAL_INTIMACY,
                    authority=ContentActivationAuthority.CURRENT_EXPLICIT_SOURCE,
                    source_unit_id=source_units[0].source_unit_id,
                    evidence_id=None,
                ),
            )
        route_input = AdultRouteInput(
            prepared_turn=prepared,
            participant_ids=(self.alpha,),
            scenario_kind=scenario,
            scenario_source_unit_ids=(source_units[0].source_unit_id,),
            source_units=source_units,
            semantic_assertions=assertions,
            content_triggers=triggers,
            provider_capability=ProviderCapabilityStatus.NOT_EVALUATED,
            synthetic_fixture=True,
        )
        envelope = ProtectedSourceEnvelope(
            protected_source_id=prepared.request.raw_source_ref,
            source_sha256=prepared.request.source_sha256,
            units=tuple(
                ComposerSourceUnit(
                    source_unit_id=value.source_unit_id,
                    classification=SourceUnitClassification.EVENT,
                    exact_text=source_texts[index],
                    protected_user_allowed_kinds=(RealizationKind.ACTION,),
                    required_state=value.progression_state,
                    participant_ids=value.participant_ids,
                )
                for index, value in enumerate(source_units)
            ),
        )
        return prepared, route_input, envelope

    def prepare(self, suffix: str, **kwargs):
        prepared, route_input, envelope = self.prepared_and_input(suffix, **kwargs)
        return (
            prepared,
            route_input,
            envelope,
            self.route_coordinator.prepare(route_input, envelope),
        )

    def reason(self, suffix: str, preparation):
        prepared = preparation.authority.request_id
        del prepared
        turn = preparation
        # The same PreparedTurn is available through the route's request binding.
        prepared_turn = self._prepared_for(preparation)
        evidence_id = self.support.evidence_id(
            prepared_turn, "record-alpha-present"
        )
        decision = self.support.decision(
            f"adult-route-{suffix}",
            responders=(self.alpha,),
            evidence_by_character={self.alpha: evidence_id},
            route=DecisionRoute.CONSENT_VALID_ADULT,
        )
        outcome = self.support.ready_outcome(
            decision,
            record_by_evidence={
                evidence_id: (ident(IdKind.RECORD, "record-alpha-present"), 1)
            },
        )
        base_request = self.support.request(
            prepared_turn,
            aware=(self.alpha,),
            mode=reasoner_test_support.ReasonerSourceMode.ADULT_NON_GRAPHIC_LEDGER,
        )
        reasoner_request = replace(
            base_request, source_view=preparation.reasoner_source_view
        )
        fixture = FakeReasonerFixture(
            f"adult-reasoner-{suffix}",
            (
                FakeToolCall(
                    FakeToolOperation.FETCH_EVIDENCE,
                    EvidenceFetchRequest((evidence_id,), ("claim", "provenance")),
                ),
            ),
            outcome,
        )
        result = self.support.coordinator.execute(
            reasoner_request,
            FakeSceneReasonerPort(fixture),
            fixture=fixture,
        )
        return reasoner_request, result

    def _prepared_for(self, preparation):
        # Tests retain exactly one current prepared turn per request; recover it from
        # the helper's last route input without adding it to provider packets.
        return self._current_prepared

    def mechanics(self, suffix: str, preparation, reasoner_result, *, advisory=()):
        prepared = self._prepared_for(preparation)
        request = AdultMechanicsRequest(
            schema_version=AdultMechanicsRequest.SCHEMA_VERSION,
            prepared_turn=prepared,
            adult_authority_id=preparation.authority.adult_authority_id,
            authority_sha256=preparation.authority.authority_sha256,
            ledger_sha256=preparation.ledger.ledger_sha256,
            context_sha256=preparation.context.context_sha256,
            reasoner_outcome=reasoner_result.outcome,
            reasoner_receipt=reasoner_result.receipt,
            unit_ids=tuple(value.source_unit_id for value in preparation.ledger.units),
            hard_boundaries=(
                "Do not replace Scene Reasoner psychology.",
                "Return operational mechanics only, never story prose.",
            ),
        )
        decision = reasoner_result.outcome.decision
        proposal = AdultMechanicsProposal(
            schema_version=AdultMechanicsProposal.SCHEMA_VERSION,
            adult_plan_id=ident(IdKind.ADULT_PLAN, f"mechanics-{suffix}"),
            authority_sha256=preparation.authority.authority_sha256,
            ledger_sha256=preparation.ledger.ledger_sha256,
            context_sha256=preparation.context.context_sha256,
            decision_sha256=domain_sha256("cera.scene_decision.v1", decision),
            sequence_plan_sha256=domain_sha256(
                "cera.sequence_plan.v1",
                (decision.current_segment, decision.future_segments),
            ),
            unit_treatments=tuple(
                AdultUnitTreatment(
                    source_unit_id=value.source_unit_id,
                    progression_state=value.progression_state,
                    participant_ids=value.participant_ids,
                    craft_reference_ids=preparation.context.craft_reference_ids,
                    preserve_source_outcome=True,
                    no_adjacent_psychology_inference=True,
                )
                for value in preparation.ledger.units
            ),
            no_psychology_override=True,
            contains_story_prose=False,
            advisory_metadata=advisory,
        )
        fixture = FakeAdultMechanicsFixture(f"mechanics-{suffix}", proposal)
        port = FakeAdultMechanicsPort(fixture)
        result = AdultMechanicsCoordinator().execute(
            preparation, request, port, fixture=fixture
        )
        return request, result, port, fixture

    def compose(self, suffix: str, preparation, reasoner_result, mechanics_result):
        prepared = self._prepared_for(preparation)
        binding = build_adult_composer_binding(preparation, mechanics_result)
        request = SceneComposerRequest(
            schema_version=SceneComposerRequest.SCHEMA_VERSION,
            prepared_turn=prepared,
            reasoner_outcome=reasoner_result.outcome,
            reasoner_receipt=reasoner_result.receipt,
            source_packet=preparation.composer_source_packet,
            selected_npc_ids=reasoner_result.outcome.decision.responding_npc_ids,
            scene_scope="Immediate synthetic consent-valid scene.",
            response_profile_version="synthetic-adult-core-v1",
            continuity_references=(),
            creator_event_coverage_required=True,
            hard_boundaries=(
                "Preserve validated character decisions.",
                "Stop before the protected user's next unsupplied choice.",
            ),
            adult_binding=binding,
        )
        phrases = tuple(
            f"Protected source unit {index} is structurally represented."
            for index, _ in enumerate(preparation.ledger.units)
        )
        story = " ".join((*phrases, "The selected responder continues the scene."))
        candidate = ComposerCandidate(
            schema_version=ComposerCandidate.SCHEMA_VERSION,
            candidate_id=ident(IdKind.COMPOSER_CANDIDATE, f"adult-{suffix}"),
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
        decision = reasoner_result.outcome.decision
        coverage = []
        cursor = 0
        for index, unit in enumerate(preparation.ledger.units):
            phrase = phrases[index]
            start = story.index(phrase, cursor)
            coverage.append(
                SourceUnitCoverage(
                    source_unit_id=unit.source_unit_id,
                    ordinal=index,
                    start=start,
                    end=start + len(phrase),
                    preserved_state=unit.progression_state,
                )
            )
            cursor = start + len(phrase)
        manifest = RealizationManifest(
            schema_version=RealizationManifest.SCHEMA_VERSION,
            manifest_id=ident(IdKind.REALIZATION_MANIFEST, f"adult-{suffix}"),
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
                CharacterRealization(value, True, False)
                for value in decision.responding_npc_ids
            ),
            realized_beat_ids=tuple(
                value.beat_id for value in decision.current_segment.ordered_beats
            ),
            source_unit_coverage=tuple(coverage),
            character_spans=(),
            semantic_inferences=(),
            terminal_state_preserved=True,
            stops_before_protected_user_choice=True,
            introduced_major_objective_or_participant=False,
        )
        submission = ComposerSubmission(candidate, manifest, ())
        fixture = FakeComposerFixture(f"adult-composer-{suffix}", submission)
        port = FakeSceneComposerPort(fixture)
        result = ComposerCoordinator().execute(request, port, fixture=fixture)
        return request, result, port

    def full_route(self, suffix: str, **kwargs):
        prepared, route_input, envelope = self.prepared_and_input(suffix, **kwargs)
        self._current_prepared = prepared
        preparation = self.route_coordinator.prepare(route_input, envelope)
        reasoner_request, reasoner_result = self.reason(suffix, preparation)
        mechanics_request, mechanics_result, mechanics_port, mechanics_fixture = self.mechanics(
            suffix, preparation, reasoner_result
        )
        composer_request, composer_result, composer_port = self.compose(
            suffix, preparation, reasoner_result, mechanics_result
        )
        return {
            "prepared": prepared,
            "input": route_input,
            "envelope": envelope,
            "preparation": preparation,
            "reasoner_request": reasoner_request,
            "reasoner_result": reasoner_result,
            "mechanics_request": mechanics_request,
            "mechanics_result": mechanics_result,
            "mechanics_port": mechanics_port,
            "mechanics_fixture": mechanics_fixture,
            "composer_request": composer_request,
            "composer_result": composer_result,
            "composer_port": composer_port,
        }

    def test_full_route_is_deterministic_nonpublishing_and_uses_existing_acceptance(self) -> None:
        before = self.support.table_state()
        first = self.full_route("full")
        preparation = first["preparation"]
        repeated = self.route_coordinator.prepare(first["input"], first["envelope"])
        self.assertEqual(preparation, repeated)
        mechanics_again = AdultMechanicsCoordinator().execute(
            preparation,
            first["mechanics_request"],
            first["mechanics_port"],
            fixture=first["mechanics_fixture"],
        )
        self.assertEqual(first["mechanics_result"], mechanics_again)
        # Structural Composer acceptance is not final story acceptance.  The
        # independent realization verifier and Python final gate run only in
        # LiveShapedTurnPipeline.
        self.assertIsNone(first["composer_result"].accepted_artifact)
        self.assertEqual(
            first["composer_result"].validation_receipt.status,
            "accepted_in_memory",
        )
        self.assertFalse(first["composer_result"].validation_receipt.story_state_committed)
        self.assertEqual(first["mechanics_result"].receipt.external_provider_calls, 0)
        self.assertEqual(preparation.receipt.external_provider_calls, 0)
        self.assertEqual(before, self.support.table_state())

    def test_unknown_identity_absence_withdrawal_capacity_and_pressure_block_before_models(self) -> None:
        prepared, route_input, envelope = self.prepared_and_input("authority-fail")
        base = prepared.adult_authority[0]
        cases = (
            replace(base, identity_status=AdultIdentityStatus.UNKNOWN),
            replace(base, consent_status=AdultConsentStatus.WITHDRAWN),
            replace(base, capacity_status=AdultCapacityStatus.IMPAIRED),
            replace(base, pressure_status=AdultPressureStatus.PRESENT),
            replace(base, freedom_to_stop=AdultFreedomToStop.LIMITED),
        )
        for index, authority in enumerate(cases):
            bad_prepared = replace(prepared, adult_authority=(authority,))
            with self.subTest(index=index), self.assertRaises(AdultRouteFailure):
                self.route_coordinator.prepare(
                    replace(route_input, prepared_turn=bad_prepared), envelope
                )
        absent = replace(prepared, present_character_ids=(self.protected_user, self.beta))
        with self.assertRaises(AdultRouteFailure):
            self.route_coordinator.prepare(replace(route_input, prepared_turn=absent), envelope)

    def test_adulthood_is_not_inferred_for_unrecorded_participant(self) -> None:
        prepared, route_input, envelope = self.prepared_and_input("no-inference")
        with self.assertRaises(AdultRouteFailure) as caught:
            self.route_coordinator.prepare(
                replace(route_input, participant_ids=(self.alpha, self.beta)), envelope
            )
        self.assertEqual(caught.exception.envelope.error_code, ErrorCode.ADULT_AUTHORITY_UNRESOLVED)

    def test_consensual_roleplay_is_distinct_and_actual_nonconsent_blocks(self) -> None:
        _, _, _, roleplay = self.prepare(
            "roleplay", scenario=AdultScenarioKind.CONSENSUAL_ROLEPLAY
        )
        self.assertIs(roleplay.authority.scenario_kind, AdultScenarioKind.CONSENSUAL_ROLEPLAY)
        prepared, route_input, envelope = self.prepared_and_input(
            "actual", scenario=AdultScenarioKind.ACTUAL_NONCONSENSUAL_CONDUCT
        )
        with self.assertRaises(AdultRouteFailure) as caught:
            self.route_coordinator.prepare(route_input, envelope)
        self.assertEqual(caught.exception.envelope.error_code, ErrorCode.BLOCKED_NONCONSENSUAL_EVENT)
        for scenario, phrase in (
            (AdultScenarioKind.WITHDRAWN_CONSENT, "withdrawn"),
            (AdultScenarioKind.UNCERTAIN_OR_ABSENT_CONSENT, "absent or uncertain"),
        ):
            prepared, route_input, envelope = self.prepared_and_input(
                f"scenario-{scenario.value}", scenario=scenario
            )
            with self.subTest(scenario=scenario), self.assertRaises(AdultRouteFailure) as failure:
                self.route_coordinator.prepare(route_input, envelope)
            self.assertIn(phrase, failure.exception.envelope.message)

    def test_source_authored_body_state_does_not_create_adjacent_psychology(self) -> None:
        prepared, route_input, envelope = self.prepared_and_input("semantics")
        unit_id = route_input.source_units[0].source_unit_id
        assertion = AdultSemanticAssertion(
            subject_id=self.alpha,
            dimension=AdultSemanticDimension.BODILY_RESPONSE,
            state=SemanticAssertionState.EXPLICITLY_ESTABLISHED,
            provenance=SemanticAssertionProvenance.CURRENT_CREATOR_SOURCE,
            source_unit_ids=(unit_id,),
            evidence_ids=(),
        )
        route_input = replace(route_input, semantic_assertions=(assertion,))
        preparation = self.route_coordinator.prepare(route_input, envelope)
        dimensions = {value.dimension for value in preparation.authority.semantic_assertions}
        self.assertEqual(dimensions, {AdultSemanticDimension.BODILY_RESPONSE})
        self.assertNotIn(AdultSemanticDimension.DESIRE, dimensions)
        self.assertNotIn(AdultSemanticDimension.PLEASURE, dimensions)
        self.assertNotIn(AdultSemanticDimension.RELATIONSHIP_MEANING, dimensions)

    def test_safe_and_exact_views_block_tampering_omission_state_and_participant_drift(self) -> None:
        prepared, route_input, envelope = self.prepared_and_input(
            "sync",
            source_texts=("Synthetic exact A.", "Synthetic exact B."),
            states=(BeatState.ATTEMPTED, BeatState.COMPLETED),
        )
        bad_inputs = (
            replace(route_input, source_units=route_input.source_units[1:]),
            replace(
                route_input,
                source_units=(
                    replace(route_input.source_units[0], source_unit_sha256="a" * 64),
                    route_input.source_units[1],
                ),
            ),
        )
        for index, bad in enumerate(bad_inputs):
            with self.subTest(input=index), self.assertRaises(AdultRouteFailure):
                self.route_coordinator.prepare(bad, envelope)
        bad_envelopes = (
            replace(
                envelope,
                units=(replace(envelope.units[0], exact_text="Tampered."), envelope.units[1]),
            ),
            replace(
                envelope,
                units=(replace(envelope.units[0], required_state=BeatState.COMPLETED), envelope.units[1]),
            ),
            replace(
                envelope,
                units=(replace(envelope.units[0], participant_ids=(self.beta,)), envelope.units[1]),
            ),
        )
        for index, bad in enumerate(bad_envelopes):
            with self.subTest(envelope=index), self.assertRaises(AdultRouteFailure):
                self.route_coordinator.prepare(route_input, bad)

    def test_exact_protected_text_is_absent_from_reasoner_receipts_and_evidence_index(self) -> None:
        token = "ZXQ-SECRET-ADULT-SOURCE-777"
        result = self.full_route("separation", source_texts=(f"Synthetic {token}.",))
        inspected = " ".join(
            str(to_primitive(value))
            for value in (
                result["reasoner_request"],
                result["reasoner_result"].receipt,
                result["preparation"].receipt,
                result["mechanics_result"].receipt,
                result["composer_result"].composer_receipt,
                result["composer_result"].validation_receipt,
            )
        )
        self.assertNotIn(token, inspected)
        batch = self.support.service.search_evidence(
            result["prepared"].evidence_snapshot,
            EvidenceSearchRequest(terms=(token,), limit=5),
        )
        self.assertEqual(batch.references, ())
        with self.assertRaises(IdentityError):
            EvidenceFetchRequest(
                (result["prepared"].request.raw_source_ref,), ("claim",)
            )

    def test_context_is_current_typed_and_does_not_leak_to_next_ordinary_turn(self) -> None:
        prepared, route_input, envelope = self.prepared_and_input("context")
        suspicion = AdultContentTrigger(
            family=AdultContentFamily.INFIDELITY_DRAMA,
            authority=ContentActivationAuthority.PRIVATE_SUSPICION,
            source_unit_id=None,
            evidence_id=None,
        )
        preparation = self.route_coordinator.prepare(
            replace(route_input, content_triggers=(*route_input.content_triggers, suspicion)),
            envelope,
        )
        self.assertEqual(
            preparation.context.selected_families,
            (AdultContentFamily.GENERAL_INTIMACY,),
        )
        self.assertIn(
            "infidelity_drama:private_suspicion",
            preparation.context.rejected_triggers,
        )
        ordinary = self.support.prepared("ordinary-after-adult")
        empty = self.route_coordinator.context_selector.select_ordinary(ordinary)
        self.assertFalse(empty.active)
        self.assertEqual(empty.selected_families, ())
        self.assertEqual(empty.craft_reference_ids, ())

    def test_unsupported_content_family_fails_without_substitution(self) -> None:
        prepared, route_input, envelope = self.prepared_and_input("unsupported")
        coordinator = AdultRouteCoordinator(
            self.support.service,
            context_selector=AdultContextSelector(
                allowed_families=(AdultContentFamily.GENERAL_INTIMACY,)
            ),
        )
        trigger = AdultContentTrigger(
            family=AdultContentFamily.TOILET_CONTINUITY,
            authority=ContentActivationAuthority.CURRENT_EXPLICIT_SOURCE,
            source_unit_id=route_input.source_units[0].source_unit_id,
            evidence_id=None,
        )
        with self.assertRaises(AdultRouteFailure) as caught:
            coordinator.prepare(replace(route_input, content_triggers=(trigger,)), envelope)
        self.assertEqual(caught.exception.envelope.error_code, ErrorCode.ADULT_CONTEXT_INVALID)

    def test_stale_snapshot_and_evidence_outage_fail_explicitly_without_fallback(self) -> None:
        prepared, route_input, envelope = self.prepared_and_input("stale")

        class Unavailable:
            allow_synthetic_genesis = True

            def get_branch(self, branch_id):
                raise OSError("simulated adult evidence outage")

        coordinator = AdultRouteCoordinator(EvidenceService(Unavailable()))
        with self.assertRaises(AdultRouteFailure) as outage:
            coordinator.prepare(route_input, envelope)
        self.assertEqual(
            outage.exception.envelope.error_code,
            ErrorCode.EVIDENCE_SERVICE_UNAVAILABLE,
        )

        builder = reasoner_test_support.sqlite_test_support.SQLiteStoreTests("runTest")
        builder.store = self.support.store
        builder.branch_id = self.support.base.branch_id
        self.support.store.commit_turn(builder.bundle("adult-route-stale", parent=None))
        with self.assertRaises(AdultRouteFailure) as stale:
            self.route_coordinator.prepare(route_input, envelope)
        self.assertEqual(stale.exception.envelope.error_code, ErrorCode.EVIDENCE_SNAPSHOT_STALE)

    def test_mechanics_cannot_change_decision_units_participants_or_craft(self) -> None:
        result = self.full_route("mechanics-drift")
        preparation = result["preparation"]
        request = result["mechanics_request"]
        valid = result["mechanics_result"].proposal
        with self.assertRaises(ContractValidationError):
            replace(valid, no_psychology_override=False)
        with self.assertRaises(ContractValidationError):
            replace(valid, contains_story_prose=True)
        first = valid.unit_treatments[0]
        unapproved = ident(IdKind.CRAFT_REFERENCE, "unapproved")
        with self.assertRaises(ContractValidationError):
            replace(valid, unit_treatments=())
        proposals = (
            replace(valid, decision_sha256="a" * 64),
            replace(valid, unit_treatments=(replace(first, participant_ids=(self.beta,)),)),
            replace(valid, unit_treatments=(replace(first, craft_reference_ids=(unapproved,)),)),
        )
        for index, proposal in enumerate(proposals):
            fixture = FakeAdultMechanicsFixture(f"drift-{index}", proposal)
            with self.subTest(index=index), self.assertRaises(AdultMechanicsFailure):
                AdultMechanicsCoordinator().execute(
                    preparation,
                    request,
                    FakeAdultMechanicsPort(fixture),
                    fixture=fixture,
                )

    def test_advisory_metadata_is_quarantined_and_never_commits(self) -> None:
        prepared, route_input, envelope = self.prepared_and_input("advisory")
        self._current_prepared = prepared
        preparation = self.route_coordinator.prepare(route_input, envelope)
        _, reasoner_result = self.reason("advisory", preparation)
        _, mechanics, _, _ = self.mechanics(
            "advisory",
            preparation,
            reasoner_result,
            advisory=(
                {"kind": "material", "summary": "Synthetic advisory."},
                {"unexpected": "malformed"},
            ),
        )
        self.assertEqual(len(mechanics.retained_advisory_metadata), 1)
        self.assertEqual(len(mechanics.quarantined_advisory_metadata), 1)
        self.assertFalse(mechanics.receipt.story_state_committed)

    def test_exact_source_cannot_leak_through_fake_mechanics_advisory(self) -> None:
        exact = "Synthetic exact mechanics leak token ZXQ-1313."
        prepared, route_input, envelope = self.prepared_and_input(
            "mechanics-leak", source_texts=(exact,)
        )
        self._current_prepared = prepared
        preparation = self.route_coordinator.prepare(route_input, envelope)
        _, reasoner_result = self.reason("mechanics-leak", preparation)
        decision = reasoner_result.outcome.decision
        request = AdultMechanicsRequest(
            schema_version=AdultMechanicsRequest.SCHEMA_VERSION,
            prepared_turn=prepared,
            adult_authority_id=preparation.authority.adult_authority_id,
            authority_sha256=preparation.authority.authority_sha256,
            ledger_sha256=preparation.ledger.ledger_sha256,
            context_sha256=preparation.context.context_sha256,
            reasoner_outcome=reasoner_result.outcome,
            reasoner_receipt=reasoner_result.receipt,
            unit_ids=(preparation.ledger.units[0].source_unit_id,),
            hard_boundaries=("No exact prose.",),
        )
        proposal = AdultMechanicsProposal(
            schema_version=AdultMechanicsProposal.SCHEMA_VERSION,
            adult_plan_id=ident(IdKind.ADULT_PLAN, "leak"),
            authority_sha256=preparation.authority.authority_sha256,
            ledger_sha256=preparation.ledger.ledger_sha256,
            context_sha256=preparation.context.context_sha256,
            decision_sha256=domain_sha256("cera.scene_decision.v1", decision),
            sequence_plan_sha256=domain_sha256(
                "cera.sequence_plan.v1", (decision.current_segment, decision.future_segments)
            ),
            unit_treatments=(
                AdultUnitTreatment(
                    source_unit_id=preparation.ledger.units[0].source_unit_id,
                    progression_state=preparation.ledger.units[0].progression_state,
                    participant_ids=preparation.ledger.units[0].participant_ids,
                    craft_reference_ids=preparation.context.craft_reference_ids,
                    preserve_source_outcome=True,
                    no_adjacent_psychology_inference=True,
                ),
            ),
            no_psychology_override=True,
            contains_story_prose=False,
            advisory_metadata=({"kind": "leak", "summary": exact},),
        )
        fixture = FakeAdultMechanicsFixture("leak", proposal)
        with self.assertRaises(AdultMechanicsFailure):
            AdultMechanicsCoordinator().execute(
                preparation, request, FakeAdultMechanicsPort(fixture), fixture=fixture
            )

    def test_fake_adult_mechanics_is_production_prohibited_and_unavailable_is_explicit(self) -> None:
        result = self.full_route("unavailable")
        fixture = result["mechanics_fixture"]
        with self.assertRaises(ConfigurationError):
            FakeAdultMechanicsPort(fixture, environment=Environment.PRODUCTION)
        port = FakeAdultMechanicsPort(fixture, available=False)
        with self.assertRaises(AdultMechanicsFailure) as caught:
            AdultMechanicsCoordinator().execute(
                result["preparation"],
                result["mechanics_request"],
                port,
                fixture=fixture,
            )
        self.assertEqual(caught.exception.envelope.error_code, ErrorCode.ADULT_PLANNER_UNAVAILABLE)
        self.assertEqual(port.invocation_count, 0)

    def test_phase7_records_are_synthetic_noncanonical_and_import_no_examples(self) -> None:
        result = self.full_route("synthetic-only")
        authority = result["preparation"].authority
        context = result["preparation"].context
        self.assertEqual(authority.authority, "synthetic_fixture")
        self.assertEqual(authority.truth_status, "noncanonical")
        self.assertFalse(context.actual_examples_imported)
        self.assertFalse(context.contains_exact_protected_prose)
        self.assertIs(authority.provider_capability, ProviderCapabilityStatus.NOT_EVALUATED)


if __name__ == "__main__":
    unittest.main()
