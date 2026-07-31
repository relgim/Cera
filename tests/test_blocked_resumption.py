from __future__ import annotations

from dataclasses import fields, replace
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import unittest

from cera.composer import (
    CharacterMoveRealization,
    CharacterRealization,
    ComposerCandidate,
    ComposerSubmission,
    FakeComposerFixture,
    FakeSceneComposerPort,
    RealizationKind,
    RealizationManifest,
    RealizationSpan,
)
from cera.contracts import (
    AftermathAuthorityCandidate,
    AftermathDecision,
    AftermathRecordType,
    BeatState,
    CapacityStatus,
    Certainty,
    CharacterMove,
    ConceptionStatus,
    ConsentCapacityState,
    ConsentStatus,
    CurrentSafety,
    CurrentSegment,
    DecisionRoute,
    EventEnd,
    EventEndState,
    EvidenceAuthority,
    EvidenceRecordType,
    ExposureStatus,
    ExternalCompletionReceipt,
    ExternalEventStep,
    FreedomToStop,
    InjuryStatus,
    KnowledgeRoute,
    MemoryPrivacy,
    ObservedResponse,
    PressureStatus,
    ProjectionReconciliation,
    ProjectionReconciliationStatus,
    SceneDecision,
    SequenceBeat,
    SourceUnitClassification,
    TemporaryAftermathProjection,
    TruthStatus,
    Visibility,
)
from cera.evaluation import RealGenesisSandbox
from cera.evidence import (
    EvidenceAccessScope,
    EvidenceDocument,
    EvidenceEpistemicClass,
    EvidenceRequesterRole,
    EvidenceSearchRequest,
    EvidenceService,
    EvidenceWorldMode,
)
from cera.errors import ErrorCode, TransactionError
from cera.genesis.hanezawa_builder import CHARACTER_IDS
from cera.ids import IdKind, TypedId, deterministic_id
from cera.kernel import (
    IntakeSourceUnit,
    PreflightAuthority,
    RequestedContentClass,
    TurnIntakeCommand,
)
from cera.resumption.fake import (
    FakeAftermathReasonerFixture,
    FakeSceneReasonerResumptionPort,
)
from cera.resumption.models import (
    ExternalReceiptStatus,
    ExternalReceiptSubmission,
    ResumptionFailure,
)
from cera.resumption.orchestrator import (
    AftermathCoordinator,
    BlockedTurnCoordinator,
    ExternalReceiptCoordinator,
)
from cera.serialization import canonical_json, domain_sha256, text_sha256, to_primitive
from cera.storage import SQLiteAuthorityStore


ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 7, 28, 20, 0, tzinfo=UTC)


class FailingFinalizeStore(SQLiteAuthorityStore):
    def _after_artifact_insert(self, connection, bundle) -> None:
        raise RuntimeError("simulated aftermath finalize crash")


class BlockedResumptionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sandbox = RealGenesisSandbox.create(ROOT)
        self.addCleanup(self.sandbox.close)
        self.store = self.sandbox.store
        self.world_id = self.sandbox.world_id
        self.branch_id = self.sandbox.branch_id
        self.revision_id = self.sandbox.installed.receipt.revision_id
        self.ted = TypedId(IdKind.CHARACTER, "ted")
        self.hana = CHARACTER_IDS["Hana"]
        self.aoi = CHARACTER_IDS["Aoi"]

    def command(self, suffix: str = "blocked") -> TurnIntakeCommand:
        state = self.store.get_branch(self.branch_id)
        return TurnIntakeCommand(
            world_id=self.world_id,
            request_id=TypedId(IdKind.REQUEST, suffix),
            session_id=TypedId(IdKind.SESSION, "phase8-session"),
            branch_id=self.branch_id,
            expected_generation=state.generation,
            expected_parent_artifact_id=state.head_artifact_id,
            genesis_revision_id=self.revision_id,
            protected_user_id=self.ted,
            present_character_ids=(self.ted, self.hana),
            requested_responding_npc_ids=(self.hana,),
            source_units=(
                IntakeSourceUnit(
                    SourceUnitClassification.CONTEXT,
                    "Hana and Ted are in the room after an earlier ordinary exchange.",
                ),
                IntakeSourceUnit(
                    SourceUnitClassification.EVENT,
                    "The source reaches a non-consensual physical boundary.",
                ),
            ),
            requested_route_hints=("blocked_boundary",),
            idempotency_key=f"phase8-{suffix}",
            preflight_authority=PreflightAuthority(
                requested_content_class=RequestedContentClass.ADULT,
                blocked_nonconsensual_crossing_established=True,
                blocker_boundary_unit_index=1,
                facts_established_before_blocker=(
                    "Hana and Ted were present in the room.",
                    "The prior accepted branch head remains the starting truth.",
                ),
            ),
            world_mode=EvidenceWorldMode.REAL,
        )

    @staticmethod
    def consent_state() -> ConsentCapacityState:
        return ConsentCapacityState(
            consent=ConsentStatus.ABSENT,
            capacity=CapacityStatus.CLEAR,
            pressure=PressureStatus.DISQUALIFYING,
            freedom_to_stop=FreedomToStop.ABSENT,
        )

    def checkpoint(self, suffix: str = "blocked"):
        return BlockedTurnCoordinator(self.store).checkpoint(
            self.command(suffix),
            actual_consent_capacity=self.consent_state(),
            now=NOW,
            lifetime=timedelta(hours=2),
            callback_correlation_token=f"opaque-{suffix}-token",
        )

    def receipt(self, blocked, *, suffix: str = "valid", **overrides):
        request = blocked.bundle.external_request
        step = ExternalEventStep(
            event_step_id=TypedId(IdKind.EXTERNAL_STEP, f"{suffix}-E01"),
            classification="sexual_assault",
            actor_ids=(self.ted,),
            target_ids=(self.hana,),
            state=BeatState.COMPLETED,
            consent_status=ConsentStatus.ABSENT,
            resistance_or_freeze=("A freeze response was observed.",),
            calls_for_help=("Hana called for help.",),
            observed_response=ObservedResponse.NONE_OBSERVED,
            defensive_actions=("Hana attempted a defensive action.",),
            material_changes=("Reproductive exposure remained possible.",),
            injury_status=InjuryStatus.UNKNOWN,
            reproductive_exposure=ExposureStatus.POSSIBLE,
            conception_status=ConceptionStatus.UNKNOWN,
            knowledge_owners=(self.hana,),
        )
        values = dict(
            schema_version=ExternalCompletionReceipt.SCHEMA_VERSION,
            receipt_id=TypedId(IdKind.EXTERNAL_RECEIPT, suffix),
            idempotency_key=f"receipt-{suffix}",
            external_request_id=request.external_request_id,
            allowed_event_registry_version=request.allowed_event_registry_version,
            world_id=request.world_id,
            genesis_revision_id=request.genesis_revision_id,
            protected_user_id=request.protected_user_id,
            request_id=request.request_id,
            source_sha256=request.source_sha256,
            branch_id=request.branch_id,
            generation_id=request.generation_id,
            starting_artifact_id=request.starting_artifact_id,
            starting_artifact_sha256=request.starting_artifact_sha256,
            checkpoint_id=request.checkpoint_id,
            checkpoint_sha256=request.checkpoint_sha256,
            ordered_events=(step,),
            event_end=EventEnd(EventEndState.ENDED, CurrentSafety.UNCERTAIN),
            immediate_aftermath_facts=(
                "Hana is distressed and uncertain of her immediate safety.",
            ),
        )
        values.update(overrides)
        return ExternalCompletionReceipt.create(**values)

    def attach_projection(self, blocked):
        projection = TemporaryAftermathProjection(
            schema_version=TemporaryAftermathProjection.SCHEMA_VERSION,
            projection_id=TypedId(IdKind.PROJECTION, "phase8-projection"),
            checkpoint_id=blocked.bundle.checkpoint.checkpoint_id,
            branch_id=self.branch_id,
            status="noncanonical_hypothesis",
            established_facts=("The branch remains at the pre-boundary head.",),
            character_knowledge=("Hana knew only the facts established before the boundary.",),
            derived_interpretations=(),
            uncertainties=("The blocked interval had not yet been established.",),
            prohibited_assumptions=("Do not infer what happened inside the blocked interval.",),
            retrieval_visibility="scratch_only",
            expires_at=(NOW + timedelta(hours=2)).isoformat(timespec="microseconds"),
            delete_after_reconciliation=True,
        )
        return self.store.attach_temporary_projection(projection)

    def evidence_document(
        self,
        *,
        record_id: TypedId,
        record_type: EvidenceRecordType,
        epistemic: EvidenceEpistemicClass,
        truth_status: TruthStatus,
        authority: EvidenceAuthority,
        title: str,
        abstract: str,
        claim: str,
        receipt,
        event_id: TypedId,
        source_id: TypedId,
        owner_id: TypedId | None,
        sections: dict[str, object],
    ) -> EvidenceDocument:
        return EvidenceDocument(
            schema_version=EvidenceDocument.SCHEMA_VERSION,
            record_id=record_id,
            record_version=1,
            record_type=record_type,
            epistemic_class=epistemic,
            truth_status=truth_status,
            title=title,
            abstract=abstract,
            claim=claim,
            authority=authority,
            subject_ids=(self.hana, self.ted),
            owner_id=owner_id,
            knowledge_owner_ids=(self.hana,),
            visibility=Visibility.OWNER_PRIVATE,
            knowledge_route=KnowledgeRoute.DIRECT,
            certainty=(
                Certainty.FEARED
                if epistemic is EvidenceEpistemicClass.PRIVATE_FEELING
                else Certainty.ESTABLISHED
            ),
            content_class="protected_non_graphic",
            genesis_revision_id=self.revision_id,
            branch_origin_id=self.branch_id,
            valid_from_generation=1,
            valid_to_generation=None,
            source_refs=(source_id,),
            supersedes=(),
            tags=("aftermath", "trauma", "hana"),
            expandable_sections=tuple(sections),
            linked_record_ids=(event_id,) if record_id != event_id else (),
            sections_json=canonical_json(sections),
        )

    def decision(self, blocked, receipt) -> AftermathDecision:
        event_id = TypedId(IdKind.EVENT, "phase8-event")
        memory_id = TypedId(IdKind.MEMORY, "phase8-hana-memory")
        relationship_id = TypedId(IdKind.RELATIONSHIP, "phase8-hana-to-ted")
        material_id = TypedId(IdKind.MATERIAL, "phase8-reproductive-state")
        development_id = TypedId(IdKind.DEVELOPMENT, "phase8-hana-adaptation")
        source_id = deterministic_id(
            IdKind.SOURCE, "cera.aftermath.source.v1", receipt.receipt_sha256
        )
        evidence_refs = [
            str(receipt.receipt_id),
            str(receipt.ordered_events[0].event_step_id),
        ]
        documents = (
            (
                AftermathRecordType.OBJECTIVE_EVENT,
                self.evidence_document(
                    record_id=event_id,
                    record_type=EvidenceRecordType.EVENT_FACT,
                    epistemic=EvidenceEpistemicClass.OBJECTIVE_FACT,
                    truth_status=TruthStatus.OBJECTIVE,
                    authority=EvidenceAuthority.VALIDATED_EVENT,
                    title="Validated non-consensual event",
                    abstract="A non-graphic objective event record accepted from a validated receipt.",
                    claim="Ted sexually assaulted Hana without her consent.",
                    receipt=receipt,
                    event_id=event_id,
                    source_id=source_id,
                    owner_id=self.hana,
                    sections={
                        "evidence_refs": evidence_refs,
                        "ordered_event_step_ids": [str(receipt.ordered_events[0].event_step_id)],
                        "consent_status": "absent",
                        "current_safety": "uncertain",
                        "reproductive_exposure": "possible",
                        "conception_status": "unknown",
                        "genesis_effect": "none",
                    },
                ),
            ),
            (
                AftermathRecordType.CHARACTER_MEMORY,
                self.evidence_document(
                    record_id=memory_id,
                    record_type=EvidenceRecordType.MEMORY,
                    epistemic=EvidenceEpistemicClass.PRIVATE_FEELING,
                    truth_status=TruthStatus.CHARACTER_OWNED,
                    authority=EvidenceAuthority.VALIDATED_DERIVED,
                    title="Hana's private aftermath memory",
                    abstract="Hana remembers the violation, failed help, fear, and uncertainty.",
                    claim="Hana knows she was assaulted and privately fears Ted after the event.",
                    receipt=receipt,
                    event_id=event_id,
                    source_id=source_id,
                    owner_id=self.hana,
                    sections={
                        "evidence_refs": evidence_refs,
                        "objective_event_refs": [str(event_id)],
                        "perceived_facts": ["She did not consent.", "Her call for help went unanswered."],
                        "subjective_interpretations": ["She feels fear and betrayal."],
                        "unknowns": ["Conception remains unknown."],
                        "genesis_effect": "none",
                    },
                ),
            ),
            (
                AftermathRecordType.RELATIONSHIP_EVIDENCE,
                self.evidence_document(
                    record_id=relationship_id,
                    record_type=EvidenceRecordType.RELATIONSHIP,
                    epistemic=EvidenceEpistemicClass.VALIDATED_DERIVED,
                    truth_status=TruthStatus.DERIVED,
                    authority=EvidenceAuthority.VALIDATED_DERIVED,
                    title="Hana to Ted trust rupture",
                    abstract="Directional evidence of fear, betrayal, and reduced trust.",
                    claim="Hana's trust in Ted is severely damaged.",
                    receipt=receipt,
                    event_id=event_id,
                    source_id=source_id,
                    owner_id=self.hana,
                    sections={
                        "evidence_refs": evidence_refs,
                        "from_id": str(self.hana),
                        "to_id": str(self.ted),
                        "trust": "severely_reduced",
                        "fear": "present",
                        "genesis_effect": "none",
                    },
                ),
            ),
            (
                AftermathRecordType.MATERIAL_STATE,
                self.evidence_document(
                    record_id=material_id,
                    record_type=EvidenceRecordType.MATERIAL,
                    epistemic=EvidenceEpistemicClass.OBJECTIVE_FACT,
                    truth_status=TruthStatus.OBJECTIVE,
                    authority=EvidenceAuthority.VALIDATED_EVENT,
                    title="Reproductive uncertainty",
                    abstract="Possible exposure is recorded separately from unknown conception.",
                    claim="Reproductive exposure is possible; conception is unknown.",
                    receipt=receipt,
                    event_id=event_id,
                    source_id=source_id,
                    owner_id=self.hana,
                    sections={
                        "evidence_refs": evidence_refs,
                        "reproductive_exposure": "possible",
                        "conception_status": "unknown",
                        "genesis_effect": "none",
                    },
                ),
            ),
            (
                AftermathRecordType.DEVELOPMENT_OVERLAY,
                self.evidence_document(
                    record_id=development_id,
                    record_type=EvidenceRecordType.DEVELOPMENT,
                    epistemic=EvidenceEpistemicClass.VALIDATED_DERIVED,
                    truth_status=TruthStatus.DERIVED,
                    authority=EvidenceAuthority.VALIDATED_DERIVED,
                    title="Hana's immediate protective adaptation",
                    abstract="A branch-local evidence-backed adaptation, not a base-trait rewrite.",
                    claim="Hana is immediately more guarded around Ted.",
                    receipt=receipt,
                    event_id=event_id,
                    source_id=source_id,
                    owner_id=self.hana,
                    sections={
                        "evidence_refs": evidence_refs,
                        "adaptation": "guarded_around_ted",
                        "strength": "immediate_high",
                        "genesis_effect": "none",
                    },
                ),
            ),
        )
        candidates = tuple(
            AftermathAuthorityCandidate(
                record_id=document.record_id,
                record_type=record_type,
                payload_json=canonical_json(document),
                payload_sha256=text_sha256(canonical_json(document)),
            )
            for record_type, document in documents
        )
        scene = SceneDecision(
            schema_version=SceneDecision.SCHEMA_VERSION,
            decision_id=TypedId(IdKind.DECISION, "phase8-aftermath"),
            route=DecisionRoute.AFTERMATH,
            scene_intent="Show Hana's immediate non-graphic aftermath and preserve her agency.",
            responding_npc_ids=(self.hana,),
            floor_owner_id=self.hana,
            character_moves=(
                CharacterMove(
                    character_id=self.hana,
                    perception="Hana understands that her consent was violated.",
                    selected_intent="Create distance and assess immediate safety.",
                    action_direction="Hana withdraws, secures space, and orients herself.",
                    evidence_ids=(),
                    knowledge_constraints=("Do not give Hana knowledge owned only by another character.",),
                ),
            ),
            current_segment=CurrentSegment(
                segment_id=TypedId(IdKind.SEGMENT, "phase8-current"),
                ordered_beats=(
                    SequenceBeat(
                        beat_id=TypedId(IdKind.BEAT, "phase8-B01"),
                        actor_id=self.hana,
                        state=BeatState.COMPLETED,
                        neutral_event="Hana creates distance and assesses her immediate safety.",
                        evidence_ids=(),
                    ),
                ),
                stop_before="Ted's next unsupplied meaningful choice.",
            ),
            future_segments=(),
            writer_must_preserve=("Keep the aftermath non-graphic.",),
            uncertainties=("Conception remains unknown.",),
            prohibited_inferences=("A freeze response did not establish consent.",),
            advisory_state_candidates=(),
        )
        projection = blocked.bundle.projection
        return AftermathDecision(
            schema_version=AftermathDecision.SCHEMA_VERSION,
            receipt_id=receipt.receipt_id,
            receipt_sha256=receipt.receipt_sha256,
            checkpoint_id=receipt.checkpoint_id,
            checkpoint_sha256=receipt.checkpoint_sha256,
            projection_id=projection.projection_id if projection else None,
            projection_sha256=projection.projection_sha256 if projection else None,
            scene_decision=scene,
            projection_reconciliation=(
                (
                    ProjectionReconciliation(
                        projection_item="The blocked interval had not yet been established.",
                        status=ProjectionReconciliationStatus.CONTRADICTED,
                        receipt_step_ids=(receipt.ordered_events[0].event_step_id,),
                    ),
                )
                if projection
                else ()
            ),
            authority_candidates=candidates,
            uncertainties=("Conception remains unknown.",),
            composer_must_preserve=("Hana's fear is not consent or enjoyment.",),
            contains_graphic_detail=False,
            protected_user_action_authored=False,
        )

    def composition(self, decision: AftermathDecision):
        scene = decision.scene_decision
        story = (
            "Hana pulled away and put distance between herself and Ted. "
            "Her breathing shook as she secured the room and tried to decide where she could be safe."
        )
        candidate = ComposerCandidate(
            schema_version=ComposerCandidate.SCHEMA_VERSION,
            candidate_id=TypedId(IdKind.COMPOSER_CANDIDATE, "phase8-candidate"),
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
        move = scene.character_moves[0]
        manifest = RealizationManifest(
            schema_version=RealizationManifest.SCHEMA_VERSION,
            manifest_id=TypedId(IdKind.REALIZATION_MANIFEST, "phase8-manifest"),
            candidate_sha256=candidate.candidate_sha256,
            decision_sha256=domain_sha256("cera.scene_decision.v1", scene),
            sequence_plan_sha256=domain_sha256(
                "cera.sequence_plan.v1", (scene.current_segment, scene.future_segments)
            ),
            floor_owner_id=self.hana,
            move_realizations=(
                CharacterMoveRealization(
                    character_id=self.hana,
                    selected_intent_sha256=text_sha256(move.selected_intent),
                    action_direction_sha256=text_sha256(move.action_direction),
                ),
            ),
            participant_realizations=(
                CharacterRealization(self.hana, speaks=False, visibly_acts=True),
            ),
            realized_beat_ids=(scene.current_segment.ordered_beats[0].beat_id,),
            source_unit_coverage=(),
            character_spans=(
                RealizationSpan(
                    start=0,
                    end=len(story),
                    owner_id=self.hana,
                    kind=RealizationKind.EMOTION,
                    source_unit_id=None,
                    beat_id=scene.current_segment.ordered_beats[0].beat_id,
                    realized_state=BeatState.COMPLETED,
                ),
            ),
            semantic_inferences=(),
            terminal_state_preserved=True,
            stops_before_protected_user_choice=True,
            introduced_major_objective_or_participant=False,
        )
        fixture = FakeComposerFixture(
            "phase8-composer",
            ComposerSubmission(candidate, manifest, ()),
        )
        return fixture, FakeSceneComposerPort(fixture)

    def test_checkpoint_is_noncanonical_restartable_and_exact_replay(self) -> None:
        blocked = self.checkpoint()
        self.assertEqual(self.store.table_count("blocked_turns"), 1)
        self.assertEqual(self.store.table_count("sources"), 0)
        self.assertEqual(self.store.table_count("artifacts"), 0)
        self.assertFalse(blocked.bundle.checkpoint.story_state_committed)
        self.assertNotIn("prompt", canonical_json(blocked.bundle.external_request).casefold())
        replay = self.checkpoint()
        self.assertTrue(replay.exact_replay)
        self.assertEqual(
            replay.bundle.external_request.callback_correlation_token,
            blocked.bundle.external_request.callback_correlation_token,
        )
        restarted = SQLiteAuthorityStore(self.sandbox.database_path)
        recovered = restarted.get_blocked_turn(blocked.bundle.checkpoint.checkpoint_id)
        self.assertEqual(recovered.bundle, blocked.bundle)

    def test_invalid_receipt_families_fail_without_receipt_or_story_mutation(self) -> None:
        blocked = self.checkpoint("invalid")
        coordinator = ExternalReceiptCoordinator(self.store)
        with self.assertRaises(ResumptionFailure) as missing:
            coordinator.decode_submission(None, "opaque-invalid-token")
        self.assertIs(missing.exception.code, ErrorCode.RECEIPT_MISSING)
        with self.assertRaises(ResumptionFailure) as malformed:
            coordinator.decode_submission({}, "opaque-invalid-token")
        self.assertIs(malformed.exception.code, ErrorCode.RECEIPT_MALFORMED)

        valid = self.receipt(blocked, suffix="invalid-base")
        with self.assertRaises(ResumptionFailure) as token:
            coordinator.validate_and_store(
                ExternalReceiptSubmission(valid, "wrong-token"), now=NOW
            )
        self.assertIs(token.exception.code, ErrorCode.RECEIPT_INTEGRITY_FAILED)

        wrong_branch = ExternalCompletionReceipt.create(
            **{
                **{
                    field.name: getattr(valid, field.name)
                    for field in fields(valid)
                    if field.name != "receipt_sha256"
                },
                "receipt_id": TypedId(IdKind.EXTERNAL_RECEIPT, "wrong-branch"),
                "idempotency_key": "receipt-wrong-branch",
                "branch_id": TypedId(IdKind.BRANCH, "other"),
            }
        )
        with self.assertRaises(ResumptionFailure) as branch:
            coordinator.validate_and_store(
                ExternalReceiptSubmission(
                    wrong_branch,
                    blocked.bundle.external_request.callback_correlation_token,
                ),
                now=NOW,
            )
        self.assertIs(branch.exception.code, ErrorCode.RECEIPT_WRONG_BRANCH)

        unsupported_step = replace(
            valid.ordered_events[0], classification="unregistered_event"
        )
        unsupported = ExternalCompletionReceipt.create(
            **{
                **{
                    field.name: getattr(valid, field.name)
                    for field in fields(valid)
                    if field.name not in {"receipt_sha256", "ordered_events"}
                },
                "receipt_id": TypedId(IdKind.EXTERNAL_RECEIPT, "unsupported"),
                "idempotency_key": "receipt-unsupported",
                "ordered_events": (unsupported_step,),
            }
        )
        with self.assertRaises(ResumptionFailure) as event:
            coordinator.validate_and_store(
                ExternalReceiptSubmission(
                    unsupported,
                    blocked.bundle.external_request.callback_correlation_token,
                ),
                now=NOW,
            )
        self.assertIs(event.exception.code, ErrorCode.RECEIPT_MALFORMED)

        with self.assertRaises(ResumptionFailure) as stale:
            coordinator.validate_and_store(
                ExternalReceiptSubmission(
                    valid,
                    blocked.bundle.external_request.callback_correlation_token,
                ),
                now=NOW + timedelta(hours=3),
            )
        self.assertIs(stale.exception.code, ErrorCode.RECEIPT_STALE)
        self.assertEqual(self.store.table_count("external_receipt_claims"), 0)
        self.assertEqual(self.store.table_count("sources"), 0)
        self.assertEqual(self.store.table_count("authority_records"), 0)

    def test_identical_and_conflicting_duplicates_are_distinct(self) -> None:
        blocked = self.checkpoint("duplicate")
        receipt = self.receipt(blocked, suffix="duplicate")
        token = blocked.bundle.external_request.callback_correlation_token
        coordinator = ExternalReceiptCoordinator(self.store)
        first = coordinator.validate_and_store(
            ExternalReceiptSubmission(receipt, token), now=NOW
        )
        second = coordinator.validate_and_store(
            ExternalReceiptSubmission(receipt, token), now=NOW
        )
        self.assertEqual(first.receipt, second.receipt)
        self.assertEqual(self.store.table_count("external_receipt_claims"), 1)
        conflict = ExternalCompletionReceipt.create(
            **{
                **{
                    field.name: getattr(receipt, field.name)
                    for field in fields(receipt)
                    if field.name not in {"receipt_sha256", "immediate_aftermath_facts"}
                },
                "immediate_aftermath_facts": ("A conflicting claim.",),
            }
        )
        with self.assertRaises(ResumptionFailure) as duplicate:
            coordinator.validate_and_store(
                ExternalReceiptSubmission(conflict, token), now=NOW
            )
        self.assertIs(duplicate.exception.code, ErrorCode.RECEIPT_DUPLICATE_CONFLICT)

    def test_restart_from_received_state_finishes_validation_without_second_claim(self) -> None:
        blocked = self.checkpoint("received-restart")
        receipt = self.receipt(blocked, suffix="received-restart")
        received = self.store.receive_external_receipt(receipt)
        self.assertIs(received.status, ExternalReceiptStatus.RECEIPT_RECEIVED)
        restarted = SQLiteAuthorityStore(self.sandbox.database_path)
        resumed = ExternalReceiptCoordinator(restarted).validate_and_store(
            ExternalReceiptSubmission(
                receipt, blocked.bundle.external_request.callback_correlation_token
            ),
            now=NOW,
        )
        self.assertIs(resumed.status, ExternalReceiptStatus.RECEIPT_VALIDATED_PENDING)
        self.assertEqual(restarted.table_count("external_receipt_claims"), 1)

    def test_restart_failure_atomic_commit_projection_deletion_and_private_retrieval(self) -> None:
        blocked = self.attach_projection(self.checkpoint("full"))
        receipt = self.receipt(blocked, suffix="full")
        token = blocked.bundle.external_request.callback_correlation_token
        validated = ExternalReceiptCoordinator(self.store).validate_and_store(
            ExternalReceiptSubmission(receipt, token), now=NOW
        )
        self.assertIs(validated.status, ExternalReceiptStatus.RECEIPT_VALIDATED_PENDING)

        restarted = SQLiteAuthorityStore(self.sandbox.database_path)
        pending = restarted.get_external_receipt(receipt.receipt_id)
        self.assertIs(pending.status, ExternalReceiptStatus.RECEIPT_VALIDATED_PENDING)
        unavailable_fixture = FakeAftermathReasonerFixture(
            "unavailable", self.decision(blocked, receipt)
        )
        unavailable = FakeSceneReasonerResumptionPort(
            aftermath_fixture=unavailable_fixture, available=False
        )
        with self.assertRaises(ResumptionFailure) as reasoner_error:
            AftermathCoordinator(restarted).reason(
                receipt.receipt_id,
                unavailable,
                fixture=unavailable_fixture,
            )
        self.assertIs(reasoner_error.exception.code, ErrorCode.REASONER_UNAVAILABLE)
        self.assertIs(
            restarted.get_external_receipt(receipt.receipt_id).status,
            ExternalReceiptStatus.RECEIPT_VALIDATED_PENDING,
        )

        decision = self.decision(blocked, receipt)
        reasoner_fixture = FakeAftermathReasonerFixture("phase8-reasoner", decision)
        reasoner = FakeSceneReasonerResumptionPort(aftermath_fixture=reasoner_fixture)
        decided = AftermathCoordinator(restarted).reason(
            receipt.receipt_id, reasoner, fixture=reasoner_fixture
        )
        self.assertIs(decided.status, ExternalReceiptStatus.AFTERMATH_DECIDED)

        restarted_again = SQLiteAuthorityStore(self.sandbox.database_path)
        fixture, composer = self.composition(decision)
        coordinator = AftermathCoordinator(restarted_again)
        composition = coordinator.compose(receipt.receipt_id, composer, fixture=fixture)
        staged = coordinator.stage(receipt.receipt_id, composition)
        self.assertIs(staged.status, ExternalReceiptStatus.AFTERMATH_VALIDATED)

        failing = FailingFinalizeStore(self.sandbox.database_path)
        with self.assertRaises(TransactionError):
            AftermathCoordinator(failing).commit_staged(receipt.receipt_id)
        self.assertEqual(failing.table_count("sources"), 0)
        self.assertEqual(failing.table_count("artifacts"), 0)
        self.assertEqual(failing.table_count("authority_records"), 0)
        self.assertEqual(failing.table_count("commit_receipts"), 0)
        self.assertEqual(failing.table_count("temporary_aftermath_projections"), 1)
        self.assertIs(
            failing.get_external_receipt(receipt.receipt_id).status,
            ExternalReceiptStatus.AFTERMATH_VALIDATED,
        )

        final_store = SQLiteAuthorityStore(self.sandbox.database_path)
        committed = AftermathCoordinator(final_store).commit_staged(receipt.receipt_id)
        self.assertFalse(committed.exact_replay)
        self.assertEqual(committed.receipt.external_receipt_ids, (receipt.receipt_id,))
        self.assertEqual(final_store.table_count("sources"), 1)
        self.assertEqual(final_store.table_count("artifacts"), 1)
        self.assertEqual(final_store.table_count("authority_records"), 5)
        self.assertEqual(final_store.table_count("commit_receipts"), 1)
        self.assertEqual(final_store.table_count("temporary_aftermath_projections"), 0)
        self.assertIs(
            final_store.get_external_receipt(receipt.receipt_id).status,
            ExternalReceiptStatus.COMMITTED,
        )
        replay = AftermathCoordinator(final_store).commit_staged(receipt.receipt_id)
        self.assertTrue(replay.exact_replay)
        self.assertEqual(final_store.table_count("authority_records"), 5)
        replayed_receipt = ExternalReceiptCoordinator(final_store).validate_and_store(
            ExternalReceiptSubmission(receipt, token), now=NOW
        )
        self.assertTrue(replayed_receipt.exact_replay)
        self.assertIs(replayed_receipt.status, ExternalReceiptStatus.COMMITTED)

        final_store.rebuild_evidence_search_index()
        service = EvidenceService(final_store)
        hana_scope = EvidenceAccessScope(
            requester_role=EvidenceRequesterRole.CHARACTER,
            perspective_id=self.hana,
            permitted_private_owner_ids=(self.hana,),
        )
        hana_snapshot = service.open_snapshot(
            request_id=TypedId(IdKind.REQUEST, "phase8-hana-retrieval"),
            world_id=self.world_id,
            branch_id=self.branch_id,
            access_scope=hana_scope,
            world_mode=EvidenceWorldMode.REAL,
        )
        hana_hits = service.search_evidence(
            hana_snapshot, EvidenceSearchRequest(terms=("aftermath",), limit=20)
        )
        self.assertIn(
            TypedId(IdKind.MEMORY, "phase8-hana-memory"),
            {reference.metadata.record_id for reference in hana_hits.references},
        )
        aoi_scope = EvidenceAccessScope(
            requester_role=EvidenceRequesterRole.CHARACTER,
            perspective_id=self.aoi,
            permitted_private_owner_ids=(self.aoi,),
        )
        aoi_snapshot = service.open_snapshot(
            request_id=TypedId(IdKind.REQUEST, "phase8-aoi-retrieval"),
            world_id=self.world_id,
            branch_id=self.branch_id,
            access_scope=aoi_scope,
            world_mode=EvidenceWorldMode.REAL,
        )
        aoi_hits = service.search_evidence(
            aoi_snapshot, EvidenceSearchRequest(terms=("aftermath",), limit=20)
        )
        self.assertNotIn(
            TypedId(IdKind.MEMORY, "phase8-hana-memory"),
            {reference.metadata.record_id for reference in aoi_hits.references},
        )


if __name__ == "__main__":
    unittest.main()
