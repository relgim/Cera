from __future__ import annotations

from dataclasses import replace
import unittest

from cera.contracts import (
    AcceptedStoryArtifact,
    BeatState,
    BlockedTurnCheckpoint,
    CapacityStatus,
    CharacterMemoryRecord,
    CharacterMove,
    ConceptionStatus,
    ConsentCapacityState,
    ConsentStatus,
    CurrentSafety,
    CurrentSegment,
    DecisionRoute,
    ErrorEnvelope,
    EventEnd,
    EventEndState,
    ExposureStatus,
    ExternalCompletionReceipt,
    ExternalEventRequest,
    ExternalEventStep,
    FreedomToStop,
    InjuryStatus,
    KnowledgeRoute,
    MemoryPrivacy,
    ObservedResponse,
    PressureStatus,
    RejectedTurnReceipt,
    SceneDecision,
    SequenceBeat,
    SubjectiveInterpretation,
    SubjectiveKind,
    TemporaryAftermathProjection,
    Certainty,
)
from cera.errors import ErrorCode, RetryMode
from cera.ids import IdKind
from cera.serialization import text_sha256

from tests.support import HASH_A, HASH_B, HASH_C, tid


class ContractTests(unittest.TestCase):
    def test_error_envelope_cannot_claim_commit(self) -> None:
        envelope = ErrorEnvelope(
            schema_version=ErrorEnvelope.SCHEMA_VERSION,
            error_code=ErrorCode.REASONER_UNAVAILABLE,
            message="Reasoner unavailable.",
            trace_id=tid(IdKind.TRACE),
            request_id=tid(IdKind.REQUEST),
            branch_id=tid(IdKind.BRANCH),
            generation_id=tid(IdKind.GENERATION),
            stage="reasoning",
            story_state_committed=False,
            retry_mode=RetryMode.MANUAL_AFTER_REVIEW,
        )
        with self.assertRaises(ValueError):
            replace(envelope, story_state_committed=True)

    def test_scene_decision_requires_exact_move_coverage(self) -> None:
        npc = tid(IdKind.CHARACTER, "hana")
        evidence = tid(IdKind.EVIDENCE)
        beat = SequenceBeat(
            beat_id=tid(IdKind.BEAT, "B01"),
            actor_id=npc,
            state=BeatState.COMPLETED,
            neutral_event="Alpha answers the synthetic question.",
            evidence_ids=(evidence,),
        )
        move = CharacterMove(
            character_id=npc,
            perception="She heard the question.",
            selected_intent="Answer honestly.",
            action_direction="Give a cautious answer.",
            evidence_ids=(evidence,),
            knowledge_constraints=(),
        )
        decision = SceneDecision(
            schema_version=SceneDecision.SCHEMA_VERSION,
            decision_id=tid(IdKind.DECISION),
            route=DecisionRoute.ORDINARY,
            scene_intent="Answer without exposing private knowledge.",
            responding_npc_ids=(npc,),
            floor_owner_id=npc,
            character_moves=(move,),
            current_segment=CurrentSegment(
                segment_id=tid(IdKind.SEGMENT),
                ordered_beats=(beat,),
                stop_before="Ted's next choice",
            ),
            future_segments=(),
            writer_must_preserve=(),
            uncertainties=(),
            prohibited_inferences=(),
            advisory_state_candidates=(),
        )
        self.assertEqual(decision.responding_npc_ids, (npc,))
        with self.assertRaises(ValueError):
            replace(decision, character_moves=())

    def test_accepted_artifact_hash_is_binding(self) -> None:
        prose = "A presentation-neutral reply."
        artifact = AcceptedStoryArtifact(
            schema_version=AcceptedStoryArtifact.SCHEMA_VERSION,
            artifact_id=tid(IdKind.ARTIFACT),
            branch_id=tid(IdKind.BRANCH),
            generation_id=tid(IdKind.GENERATION),
            parent_artifact_id=None,
            source_id=tid(IdKind.SOURCE),
            decision_id=tid(IdKind.DECISION),
            accepted_prose=prose,
            prose_sha256=text_sha256(prose),
            responding_npc_ids=(tid(IdKind.CHARACTER, "hana"),),
            realized_beat_ids=(tid(IdKind.BEAT, "B01"),),
            validation_receipt_id=tid(IdKind.VALIDATION),
            transaction_id=tid(IdKind.TRANSACTION),
            status="accepted",
        )
        with self.assertRaises(ValueError):
            replace(artifact, accepted_prose="Changed without rehashing.")
        self.assertIn("presentation-neutral", AcceptedStoryArtifact.__doc__ or "")

    def test_blocked_checkpoint_and_rejection_cannot_commit_or_call(self) -> None:
        checkpoint = BlockedTurnCheckpoint.create(
            schema_version=BlockedTurnCheckpoint.SCHEMA_VERSION,
            checkpoint_id=tid(IdKind.CHECKPOINT),
            world_id=tid(IdKind.WORLD),
            genesis_revision_id=tid(IdKind.GENESIS_REVISION),
            protected_user_id=tid(IdKind.CHARACTER, "ted"),
            request_id=tid(IdKind.REQUEST),
            source_sha256=HASH_A,
            branch_id=tid(IdKind.BRANCH),
            generation_id=tid(IdKind.GENERATION),
            starting_artifact_id=tid(IdKind.ARTIFACT),
            starting_artifact_sha256=HASH_B,
            classification="blocked_nonconsensual_event",
            boundary_source_unit_id=tid(IdKind.SOURCE_UNIT, "S02"),
            facts_established_through_boundary=("A prior permitted interaction existed.",),
            actual_consent_capacity=ConsentCapacityState(
                consent=ConsentStatus.ABSENT,
                capacity=CapacityStatus.CLEAR,
                pressure=PressureStatus.PRESENT,
                freedom_to_stop=FreedomToStop.LIMITED,
            ),
            story_state_committed=False,
        )
        rejection = RejectedTurnReceipt(
            schema_version=RejectedTurnReceipt.SCHEMA_VERSION,
            rejection_id=tid(IdKind.REJECTION),
            request_id=checkpoint.request_id,
            checkpoint_id=checkpoint.checkpoint_id,
            error_code=ErrorCode.BLOCKED_NONCONSENSUAL_EVENT,
            story_state_committed=False,
            provider_calls_after_boundary=0,
        )
        with self.assertRaises(ValueError):
            replace(rejection, provider_calls_after_boundary=1)

    def test_external_request_contains_no_generation_prompt(self) -> None:
        request = ExternalEventRequest(
            schema_version=ExternalEventRequest.SCHEMA_VERSION,
            external_request_id=tid(IdKind.EXTERNAL_REQUEST),
            world_id=tid(IdKind.WORLD),
            genesis_revision_id=tid(IdKind.GENESIS_REVISION),
            protected_user_id=tid(IdKind.CHARACTER, "ted"),
            request_id=tid(IdKind.REQUEST),
            source_sha256=HASH_A,
            branch_id=tid(IdKind.BRANCH),
            generation_id=tid(IdKind.GENERATION),
            starting_artifact_id=tid(IdKind.ARTIFACT),
            starting_artifact_sha256=HASH_B,
            checkpoint_id=tid(IdKind.CHECKPOINT),
            checkpoint_sha256=HASH_C,
            required_receipt_schema=ExternalCompletionReceipt.SCHEMA_VERSION,
            allowed_event_registry_version="cera.controlled_event_registry.v1",
            expires_at="2026-07-29T00:00:00Z",
            callback_correlation_token="opaque-token",
        )
        self.assertFalse(hasattr(request, "prompt"))

    def test_external_receipt_preserves_separate_consent_and_reproductive_fields(self) -> None:
        step = ExternalEventStep(
            event_step_id=tid(IdKind.EXTERNAL_STEP, "E01"),
            classification="controlled_event",
            actor_ids=(tid(IdKind.CHARACTER, "ted"),),
            target_ids=(tid(IdKind.CHARACTER, "hana"),),
            state=BeatState.COMPLETED,
            consent_status=ConsentStatus.ABSENT,
            resistance_or_freeze=("freeze_observed",),
            calls_for_help=("call_for_help",),
            observed_response=ObservedResponse.NONE_OBSERVED,
            defensive_actions=("defensive_action",),
            material_changes=("object_location_changed",),
            injury_status=InjuryStatus.UNKNOWN,
            reproductive_exposure=ExposureStatus.ESTABLISHED,
            conception_status=ConceptionStatus.UNKNOWN,
            knowledge_owners=(tid(IdKind.CHARACTER, "hana"),),
        )
        receipt = ExternalCompletionReceipt.create(
            schema_version=ExternalCompletionReceipt.SCHEMA_VERSION,
            receipt_id=tid(IdKind.EXTERNAL_RECEIPT),
            idempotency_key="external-1",
            external_request_id=tid(IdKind.EXTERNAL_REQUEST),
            allowed_event_registry_version="cera.controlled_event_registry.v1",
            world_id=tid(IdKind.WORLD),
            genesis_revision_id=tid(IdKind.GENESIS_REVISION),
            protected_user_id=tid(IdKind.CHARACTER, "ted"),
            request_id=tid(IdKind.REQUEST),
            source_sha256=HASH_A,
            branch_id=tid(IdKind.BRANCH),
            generation_id=tid(IdKind.GENERATION),
            starting_artifact_id=tid(IdKind.ARTIFACT),
            starting_artifact_sha256=HASH_B,
            checkpoint_id=tid(IdKind.CHECKPOINT),
            checkpoint_sha256=HASH_C,
            ordered_events=(step,),
            event_end=EventEnd(EventEndState.ENDED, CurrentSafety.UNCERTAIN),
            immediate_aftermath_facts=("The affected character is distressed.",),
        )
        self.assertIs(receipt.ordered_events[0].consent_status, ConsentStatus.ABSENT)
        self.assertIs(receipt.ordered_events[0].conception_status, ConceptionStatus.UNKNOWN)

    def test_memory_keeps_self_blame_subjective_and_genesis_unchanged(self) -> None:
        memory = CharacterMemoryRecord(
            schema_version=CharacterMemoryRecord.SCHEMA_VERSION,
            memory_id=tid(IdKind.MEMORY),
            owner_id=tid(IdKind.CHARACTER, "hana"),
            branch_id=tid(IdKind.BRANCH),
            privacy=MemoryPrivacy.OWNER_PRIVATE,
            title="Private memory",
            abstract="Non-graphic memory summary.",
            objective_event_refs=(tid(IdKind.EVENT),),
            perceived_facts=("She directly perceived the event.",),
            subjective_interpretations=(
                SubjectiveInterpretation(
                    SubjectiveKind.SELF_BLAME,
                    "She blames herself.",
                    Certainty.BELIEVED,
                ),
            ),
            unknowns=("Long-term effect remains unknown.",),
            knowledge_route=KnowledgeRoute.DIRECT,
            retrieval_tags=("trauma", "trust"),
            trigger_cues=(),
            development_history_refs=(),
            supersedes=(),
            genesis_effect="none",
        )
        self.assertEqual(memory.genesis_effect, "none")
        with self.assertRaises(ValueError):
            replace(memory, genesis_effect="rewrite")

    def test_projection_is_scratch_only(self) -> None:
        projection = TemporaryAftermathProjection(
            schema_version=TemporaryAftermathProjection.SCHEMA_VERSION,
            projection_id=tid(IdKind.PROJECTION),
            checkpoint_id=tid(IdKind.CHECKPOINT),
            branch_id=tid(IdKind.BRANCH),
            status="noncanonical_hypothesis",
            established_facts=(),
            character_knowledge=(),
            derived_interpretations=(),
            uncertainties=("Outcome unknown.",),
            prohibited_assumptions=("Do not assert the event completed.",),
            retrieval_visibility="scratch_only",
            expires_at="2026-07-29T00:00:00Z",
            delete_after_reconciliation=True,
        )
        with self.assertRaises(ValueError):
            replace(projection, retrieval_visibility="story_memory")


if __name__ == "__main__":
    unittest.main()
