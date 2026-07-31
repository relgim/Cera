from __future__ import annotations

from dataclasses import replace
import unittest

from cera.composer import (
    AdultComposerBinding,
    CharacterMoveRealization,
    CharacterRealization,
    ComposerCandidate,
    ComposerCoordinator,
    ComposerContinuityReference,
    ComposerExecutionFailure,
    ComposerSourcePacket,
    ComposerSourceUnit,
    ComposerSubmission,
    CompositionMode,
    FakeComposerFixture,
    FakeSceneComposerPort,
    ProtectedSourceEnvelope,
    PurePresentationRenderer,
    RealizationKind,
    RealizationManifest,
    RealizationSpan,
    RendererProfile,
    SceneComposerRequest,
    SemanticCategory,
    SemanticInference,
    SourceUnitCoverage,
)
from cera.config import Environment
from cera.contracts import (
    AcceptedStoryArtifact,
    BeatState,
    DecisionRoute,
    SourceUnitClassification,
)
from cera.evidence import EvidenceFetchRequest
from cera.errors import ConfigurationError, ContractValidationError, ErrorCode
from cera.ids import IdKind, TypedId
from cera.kernel import IntakeSourceUnit
from cera.reasoner import (
    FakeReasonerFixture,
    FakeSceneReasonerPort,
    FakeToolCall,
    FakeToolOperation,
    ReasonerSourceMode,
)
from cera.schema import from_mapping
from cera.serialization import text_sha256, to_primitive
import tests.test_reasoner as reasoner_test_support


ident = reasoner_test_support.ident


class ComposerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.support = reasoner_test_support.ReasonerTests("runTest")
        self.support.setUp()
        self.addCleanup(self.support.doCleanups)
        self.coordinator = ComposerCoordinator()
        self.alpha = self.support.alpha
        self.beta = self.support.beta
        self.protected_user = self.support.ted

    def test_continuity_reference_preserves_every_authority_record_kind(self) -> None:
        for kind in (
            IdKind.RECORD,
            IdKind.EVENT,
            IdKind.MEMORY,
            IdKind.RELATIONSHIP,
            IdKind.THREAD,
            IdKind.MATERIAL,
            IdKind.DEVELOPMENT,
        ):
            reference = ComposerContinuityReference(
                evidence_id=ident(IdKind.EVIDENCE, f"continuity-{kind.value}"),
                record_id=ident(kind, f"continuity-{kind.value}"),
                record_version=1,
            )
            self.assertIs(reference.record_id.kind, kind)

        with self.assertRaisesRegex(Exception, "authority-record ID"):
            ComposerContinuityReference(
                evidence_id=ident(IdKind.EVIDENCE, "continuity-invalid"),
                record_id=ident(IdKind.ARTIFACT, "continuity-invalid"),
                record_version=1,
            )

    def reasoned(
        self,
        suffix: str,
        *,
        responders: tuple[TypedId, ...] | None = None,
        source_texts: tuple[str, ...] | None = None,
        classifications: tuple[SourceUnitClassification, ...] | None = None,
        adult: bool = False,
    ):
        responders = responders or (self.alpha,)
        source_texts = source_texts or (f"Synthetic exact source {suffix}.",)
        classifications = classifications or tuple(
            SourceUnitClassification.MESSAGE for _ in source_texts
        )
        command = self.support.base.command(
            suffix,
            responders=responders,
            units=tuple(
                IntakeSourceUnit(classification, text)
                for classification, text in zip(classifications, source_texts, strict=True)
            ),
            preflight=(self.support.base.adult_authority() if adult else None),
        )
        prepared = self.support.kernel.prepare_turn(
            command, access_scope=self.support.base.access
        )
        record_suffixes = {
            self.alpha: "record-alpha-present",
            self.beta: "record-beta-private",
        }
        evidence_by_character = {
            character_id: self.support.evidence_id(
                prepared, record_suffixes[character_id]
            )
            for character_id in responders
        }
        decision = self.support.decision(
            suffix,
            responders=responders,
            evidence_by_character=evidence_by_character,
            route=(
                DecisionRoute.CONSENT_VALID_ADULT if adult else DecisionRoute.ORDINARY
            ),
        )
        outcome = self.support.ready_outcome(
            decision,
            record_by_evidence={
                evidence_id: (ident(IdKind.RECORD, record_suffixes[character_id]), 1)
                for character_id, evidence_id in evidence_by_character.items()
            },
        )
        reasoner_request = self.support.request(
            prepared,
            aware=responders,
            mode=(
                ReasonerSourceMode.ADULT_NON_GRAPHIC_LEDGER
                if adult
                else ReasonerSourceMode.ORDINARY_EXACT
            ),
        )
        reasoner_fixture = FakeReasonerFixture(
            f"composer-reasoner-{suffix}",
            (
                FakeToolCall(
                    FakeToolOperation.FETCH_EVIDENCE,
                    EvidenceFetchRequest(
                        tuple(evidence_by_character.values()),
                        ("claim", "provenance"),
                    ),
                ),
            ),
            outcome,
        )
        reasoner_result = self.support.coordinator.execute(
            reasoner_request,
            FakeSceneReasonerPort(reasoner_fixture),
            fixture=reasoner_fixture,
        )
        return prepared, reasoner_request, reasoner_result, source_texts

    def request(
        self,
        suffix: str,
        *,
        responders: tuple[TypedId, ...] | None = None,
        source_texts: tuple[str, ...] | None = None,
        classifications: tuple[SourceUnitClassification, ...] | None = None,
        adult: bool = False,
        coverage: bool = False,
    ) -> SceneComposerRequest:
        prepared, reasoner_request, result, exact_texts = self.reasoned(
            suffix,
            responders=responders,
            source_texts=source_texts,
            classifications=classifications,
            adult=adult,
        )
        units = tuple(
            ComposerSourceUnit(
                source_unit_id=prepared.request.source_units[index].source_unit_id,
                classification=prepared.request.source_units[index].classification,
                exact_text=text,
                protected_user_allowed_kinds=(RealizationKind.ACTION,),
                required_state=(
                    BeatState.COMPLETED
                    if prepared.request.source_units[index].classification
                    is SourceUnitClassification.EVENT
                    else BeatState.ATTEMPTED
                ),
                participant_ids=responders or (self.alpha,),
            )
            for index, text in enumerate(exact_texts)
        )
        if adult:
            packet = ComposerSourcePacket(
                mode=CompositionMode.PROTECTED_ADULT,
                source_sha256=prepared.request.source_sha256,
                ordinary_units=(),
                protected_envelope=ProtectedSourceEnvelope(
                    protected_source_id=prepared.request.raw_source_ref,
                    source_sha256=prepared.request.source_sha256,
                    units=units,
                ),
                reasoner_safe_ledger_sha256=reasoner_request.source_view.source_view_sha256,
            )
        else:
            packet = ComposerSourcePacket(
                mode=CompositionMode.ORDINARY,
                source_sha256=prepared.request.source_sha256,
                ordinary_units=units,
                protected_envelope=None,
                reasoner_safe_ledger_sha256=reasoner_request.source_view.source_view_sha256,
            )
        adult_binding = None
        if adult:
            adult_binding = AdultComposerBinding(
                adult_authority_id=ident(IdKind.ADULT_AUTHORITY, f"phase6-{suffix}"),
                dual_representation_receipt_id=ident(
                    IdKind.VALIDATION, f"phase6-dual-{suffix}"
                ),
                mechanics_receipt_id=ident(
                    IdKind.PROVIDER_RECEIPT, f"phase6-mechanics-{suffix}"
                ),
                authority_sha256="a" * 64,
                dual_representation_sha256="b" * 64,
                safe_source_view_sha256=packet.reasoner_safe_ledger_sha256,
                protected_envelope_sha256=packet.protected_envelope_sha256,
                adult_context_sha256="c" * 64,
                mechanics_proposal_sha256="d" * 64,
                mechanics_receipt_sha256="e" * 64,
                selected_craft_reference_ids=(),
            )
        return SceneComposerRequest(
            schema_version=SceneComposerRequest.SCHEMA_VERSION,
            prepared_turn=prepared,
            reasoner_outcome=result.outcome,
            reasoner_receipt=result.receipt,
            source_packet=packet,
            selected_npc_ids=result.outcome.decision.responding_npc_ids,
            scene_scope="Immediate synthetic scene continuation.",
            response_profile_version="synthetic-core-v1",
            continuity_references=(),
            creator_event_coverage_required=coverage,
            hard_boundaries=(
                "Do not author the protected user beyond exact source.",
                "Return a complete presentation-neutral Core reply.",
            ),
            adult_binding=adult_binding,
        )

    def submission(
        self,
        request: SceneComposerRequest,
        suffix: str,
        *,
        story_text: str | None = None,
        spans: tuple[RealizationSpan, ...] = (),
        semantic: tuple[SemanticInference, ...] = (),
        advisory: tuple[object, ...] = (),
    ) -> ComposerSubmission:
        decision = request.reasoner_outcome.decision
        assert decision is not None
        if story_text is None:
            if request.creator_event_coverage_required:
                phrases = tuple(
                    f"Event unit {index} is realized."
                    for index, _ in enumerate(request.source_packet.units)
                )
                story_text = " ".join((*phrases, "The selected responder answers."))
            else:
                story_text = "The selected responder gives a concise, grounded answer."
        candidate = ComposerCandidate(
            schema_version=ComposerCandidate.SCHEMA_VERSION,
            candidate_id=ident(IdKind.COMPOSER_CANDIDATE, f"candidate-{suffix}"),
            story_text=story_text,
            story_text_sha256=text_sha256(story_text),
            complete_core=True,
            is_outline=False,
            is_partial_draft=False,
            awaits_detailer=False,
            contains_internal_labels=False,
            contains_ui_markup=False,
            contains_provider_diagnostics=False,
        )
        coverage: list[SourceUnitCoverage] = []
        if request.creator_event_coverage_required:
            cursor = 0
            for index, unit in enumerate(request.source_packet.units):
                phrase = f"Event unit {index} is realized."
                start = story_text.index(phrase, cursor)
                end = start + len(phrase)
                coverage.append(
                    SourceUnitCoverage(
                        source_unit_id=unit.source_unit_id,
                        ordinal=index,
                        start=start,
                        end=end,
                        preserved_state=BeatState.COMPLETED,
                    )
                )
                cursor = end
        manifest = RealizationManifest(
            schema_version=RealizationManifest.SCHEMA_VERSION,
            manifest_id=ident(IdKind.REALIZATION_MANIFEST, f"manifest-{suffix}"),
            candidate_sha256=candidate.candidate_sha256,
            decision_sha256=request.decision_sha256,
            sequence_plan_sha256=request.sequence_plan_sha256,
            floor_owner_id=decision.floor_owner_id,
            move_realizations=tuple(
                CharacterMoveRealization(
                    character_id=move.character_id,
                    selected_intent_sha256=text_sha256(move.selected_intent),
                    action_direction_sha256=text_sha256(move.action_direction),
                )
                for move in decision.character_moves
            ),
            participant_realizations=tuple(
                CharacterRealization(character_id=value, speaks=True, visibly_acts=False)
                for value in decision.responding_npc_ids
            ),
            realized_beat_ids=tuple(
                value.beat_id for value in decision.current_segment.ordered_beats
            ),
            source_unit_coverage=tuple(coverage),
            character_spans=spans,
            semantic_inferences=semantic,
            terminal_state_preserved=True,
            stops_before_protected_user_choice=True,
            introduced_major_objective_or_participant=False,
        )
        return ComposerSubmission(candidate, manifest, advisory)

    def execute(self, request: SceneComposerRequest, submission: ComposerSubmission, suffix: str):
        fixture = FakeComposerFixture(f"composer-{suffix}", submission)
        port = FakeSceneComposerPort(fixture)
        return self.coordinator.execute(request, port, fixture=fixture), port, fixture

    def test_valid_candidate_is_accepted_in_memory_deterministically_without_writes(self) -> None:
        request = self.request("valid")
        submission = self.submission(request, "valid")
        before = self.support.table_state()
        first, port, fixture = self.execute(request, submission, "valid")
        second = self.coordinator.execute(request, port, fixture=fixture)
        self.assertEqual(first, second)
        self.assertIsNone(first.accepted_artifact)
        self.assertEqual(first.candidate.story_text, submission.candidate.story_text)
        self.assertEqual(first.validation_receipt.status, "accepted_in_memory")
        self.assertFalse(first.validation_receipt.semantic_quality_proven)
        self.assertFalse(first.validation_receipt.story_state_committed)
        self.assertEqual(first.composer_receipt.external_provider_calls, 0)
        self.assertEqual(port.invocation_count, 2)
        self.assertEqual(before, self.support.table_state())
        self.assertLess(len(submission.candidate.story_text.split()), 20)

    def test_multi_character_requires_exact_selected_cast(self) -> None:
        request = self.request("multi", responders=(self.alpha, self.beta))
        valid = self.submission(request, "multi")
        self.execute(request, valid, "multi")
        unselected = ident(IdKind.CHARACTER, "synthetic-unselected")
        bad_manifest = replace(
            valid.manifest,
            participant_realizations=(*valid.manifest.participant_realizations,
                CharacterRealization(unselected, True, False)),
        )
        with self.assertRaises(ComposerExecutionFailure):
            self.execute(request, replace(valid, manifest=bad_manifest), "multi-bad")

    def test_decision_intent_floor_and_beat_drift_are_rejected(self) -> None:
        request = self.request("drift")
        valid = self.submission(request, "drift")
        move = valid.manifest.move_realizations[0]
        cases = (
            replace(
                valid.manifest,
                move_realizations=(replace(move, selected_intent_sha256="a" * 64),),
            ),
            replace(valid.manifest, realized_beat_ids=()),
            replace(valid.manifest, floor_owner_id=self.beta),
        )
        for index, manifest in enumerate(cases):
            with self.subTest(index=index), self.assertRaises(ComposerExecutionFailure):
                self.execute(request, replace(valid, manifest=manifest), f"drift-{index}")

    def test_creator_event_requires_complete_ordered_coverage_from_first_beat(self) -> None:
        request = self.request(
            "coverage",
            source_texts=("Synthetic first event.", "Synthetic second event."),
            classifications=(SourceUnitClassification.EVENT, SourceUnitClassification.EVENT),
            coverage=True,
        )
        valid = self.submission(request, "coverage")
        self.execute(request, valid, "coverage")
        bad_manifests = (
            replace(valid.manifest, source_unit_coverage=valid.manifest.source_unit_coverage[1:]),
            replace(valid.manifest, source_unit_coverage=tuple(reversed(valid.manifest.source_unit_coverage))),
            replace(
                valid.manifest,
                source_unit_coverage=(
                    replace(valid.manifest.source_unit_coverage[0], start=1, end=25),
                    valid.manifest.source_unit_coverage[1],
                ),
            ),
            replace(
                valid.manifest,
                source_unit_coverage=(
                    replace(
                        valid.manifest.source_unit_coverage[0],
                        preserved_state=BeatState.ATTEMPTED,
                    ),
                    valid.manifest.source_unit_coverage[1],
                ),
            ),
        )
        for index, manifest in enumerate(bad_manifests):
            with self.subTest(index=index), self.assertRaises(ComposerExecutionFailure):
                self.execute(request, replace(valid, manifest=manifest), f"coverage-{index}")

    def test_protected_user_requires_exact_source_authority_and_never_private_state(self) -> None:
        request = self.request("protected-user")
        story = "A supplied action is restated. The selected responder answers."
        start = story.index("A supplied action")
        allowed = RealizationSpan(
            start=start,
            end=start + len("A supplied action is restated."),
            owner_id=self.protected_user,
            kind=RealizationKind.ACTION,
            source_unit_id=request.source_packet.units[0].source_unit_id,
            beat_id=None,
            realized_state=BeatState.ATTEMPTED,
        )
        valid = self.submission(request, "protected-user", story_text=story, spans=(allowed,))
        self.execute(request, valid, "protected-user")
        invalid_spans = (
            replace(allowed, source_unit_id=None),
            replace(allowed, kind=RealizationKind.DIALOGUE),
            replace(allowed, kind=RealizationKind.PRIVATE_STATE),
            replace(allowed, realized_state=BeatState.COMPLETED),
            replace(allowed, owner_id=ident(IdKind.CHARACTER, "not-selected")),
        )
        for index, span in enumerate(invalid_spans):
            bad = replace(valid, manifest=replace(valid.manifest, character_spans=(span,)))
            with self.subTest(index=index), self.assertRaises(ComposerExecutionFailure):
                self.execute(request, bad, f"protected-user-{index}")

    def test_belief_and_body_response_cannot_be_promoted(self) -> None:
        request = self.request("semantic")
        forbidden = (
            SemanticInference(SemanticCategory.BELIEF, SemanticCategory.OBJECTIVE_FACT),
            SemanticInference(SemanticCategory.SUSPICION, SemanticCategory.OBJECTIVE_FACT),
            SemanticInference(SemanticCategory.BODILY_RESPONSE, SemanticCategory.CONSENT),
            SemanticInference(SemanticCategory.PLEASURE, SemanticCategory.CONSENT),
        )
        for index, inference in enumerate(forbidden):
            submission = self.submission(request, f"semantic-{index}", semantic=(inference,))
            with self.subTest(index=index), self.assertRaises(ComposerExecutionFailure):
                self.execute(request, submission, f"semantic-{index}")

    def test_protected_route_binds_separate_safe_and_exact_hashes_without_receipt_leak(self) -> None:
        exact = "Synthetic restricted exact source token ZXQ-941."
        request = self.request("adult", source_texts=(exact,), adult=True)
        submission = self.submission(request, "adult")
        result, _, _ = self.execute(request, submission, "adult")
        receipt_text = str(to_primitive(result.composer_receipt))
        validation_text = str(to_primitive(result.validation_receipt))
        self.assertNotIn(exact, receipt_text)
        self.assertNotIn(exact, validation_text)
        self.assertEqual(
            result.composer_receipt.safe_ledger_sha256,
            request.source_packet.reasoner_safe_ledger_sha256,
        )
        self.assertEqual(
            result.composer_receipt.protected_source_envelope_sha256,
            request.source_packet.protected_envelope_sha256,
        )

    def test_story_text_rejects_internal_labels_ui_and_diagnostics(self) -> None:
        request = self.request("leaks")
        texts = (
            "Plan: expose an internal outline.",
            "<div>UI-colored story wrapper</div>",
            "Diagnostics: provider latency was low.",
        )
        for index, text in enumerate(texts):
            submission = self.submission(request, f"leak-{index}", story_text=text)
            with self.subTest(index=index), self.assertRaises(ComposerExecutionFailure):
                self.execute(request, submission, f"leak-{index}")

    def test_outline_partial_detailer_and_boundary_flags_are_rejected_without_retry(self) -> None:
        request = self.request("incomplete")
        valid = self.submission(request, "incomplete")
        candidates = (
            replace(valid.candidate, complete_core=False),
            replace(valid.candidate, is_outline=True),
            replace(valid.candidate, is_partial_draft=True),
            replace(valid.candidate, awaits_detailer=True),
        )
        for index, candidate in enumerate(candidates):
            # Rebind the manifest so the failure is the completeness rule, not hash drift.
            manifest = replace(valid.manifest, candidate_sha256=candidate.candidate_sha256)
            with self.subTest(kind=index), self.assertRaises(ComposerExecutionFailure):
                self.execute(
                    request,
                    replace(valid, candidate=candidate, manifest=manifest),
                    f"incomplete-{index}",
                )
        manifests = (
            replace(valid.manifest, terminal_state_preserved=False),
            replace(valid.manifest, stops_before_protected_user_choice=False),
            replace(valid.manifest, introduced_major_objective_or_participant=True),
        )
        for index, manifest in enumerate(manifests):
            with self.subTest(boundary=index), self.assertRaises(ComposerExecutionFailure):
                self.execute(
                    request,
                    replace(valid, manifest=manifest),
                    f"boundary-{index}",
                )

    def test_schema_rejects_unknown_candidate_fields(self) -> None:
        request = self.request("schema")
        payload = to_primitive(self.submission(request, "schema").candidate)
        payload["provider_notes"] = "not allowed"
        with self.assertRaises(ContractValidationError):
            from_mapping(ComposerCandidate, payload)

    def test_malformed_advisory_metadata_is_quarantined_not_promoted(self) -> None:
        request = self.request("advisory")
        submission = self.submission(
            request,
            "advisory",
            advisory=(
                {"kind": "thread", "summary": "Valid advisory candidate."},
                {"kind": "memory", "unexpected": "malformed"},
                "untyped advisory",
            ),
        )
        result, _, _ = self.execute(request, submission, "advisory")
        self.assertEqual(len(result.quarantined_advisory_metadata), 2)
        self.assertEqual(len(result.advisory_realization_metadata), 1)
        self.assertEqual(result.validation_receipt.quarantined_advisory_count, 2)
        self.assertFalse(result.validation_receipt.story_state_committed)

    def test_renderer_accepts_only_accepted_artifact_and_preserves_artifact_hash(self) -> None:
        request = self.request("render")
        submission = self.submission(request, "render")
        result, _, _ = self.execute(request, submission, "render")
        decision = request.reasoner_outcome.decision
        assert decision is not None
        artifact = AcceptedStoryArtifact(
            schema_version=AcceptedStoryArtifact.SCHEMA_VERSION,
            artifact_id=ident(IdKind.ARTIFACT, "render"),
            branch_id=request.prepared_turn.request.branch_id,
            generation_id=request.prepared_turn.request.generation_id,
            parent_artifact_id=None,
            source_id=request.prepared_turn.source_record.source_id,
            decision_id=decision.decision_id,
            accepted_prose=result.candidate.story_text,
            prose_sha256=result.candidate.story_text_sha256,
            responding_npc_ids=decision.responding_npc_ids,
            realized_beat_ids=result.manifest.realized_beat_ids,
            validation_receipt_id=result.validation_receipt.validation_receipt_id,
            transaction_id=ident(IdKind.TRANSACTION, "render"),
            status="accepted",
        )
        renderer = PurePresentationRenderer(
            (
                RendererProfile("plain", "1"),
                RendererProfile("wrapped", "1", prefix="[", suffix="]"),
            )
        )
        plain = renderer.render(artifact, profile="plain")
        wrapped = renderer.render(artifact, profile="wrapped")
        self.assertEqual(plain.accepted_artifact_sha256, wrapped.accepted_artifact_sha256)
        self.assertNotEqual(plain.rendered_sha256, wrapped.rendered_sha256)
        with self.assertRaises(TypeError):
            renderer.render(submission.candidate, profile="plain")

    def test_fake_is_prohibited_in_production_and_unavailable_has_no_retry(self) -> None:
        request = self.request("unavailable")
        submission = self.submission(request, "unavailable")
        fixture = FakeComposerFixture("composer-unavailable", submission)
        with self.assertRaises(ConfigurationError):
            FakeSceneComposerPort(fixture, environment=Environment.PRODUCTION)
        port = FakeSceneComposerPort(fixture, available=False)
        with self.assertRaises(ComposerExecutionFailure) as caught:
            self.coordinator.execute(request, port, fixture=fixture)
        self.assertEqual(caught.exception.envelope.error_code, ErrorCode.COMPOSER_UNAVAILABLE)
        self.assertEqual(port.invocation_count, 0)

    def test_request_rejects_source_text_tampering_and_fixture_identity_mismatch(self) -> None:
        request = self.request("binding")
        packet = replace(
            request.source_packet,
            ordinary_units=(
                replace(request.source_packet.units[0], exact_text="Tampered exact text."),
            ),
        )
        with self.assertRaises(ContractValidationError):
            replace(request, source_packet=packet)
        submission = self.submission(request, "binding")
        fixture = FakeComposerFixture("binding-a", submission)
        other = FakeComposerFixture("binding-b", submission)
        port = FakeSceneComposerPort(fixture)
        with self.assertRaises(ComposerExecutionFailure):
            self.coordinator.execute(request, port, fixture=other)
        self.assertEqual(port.invocation_count, 0)


if __name__ == "__main__":
    unittest.main()
