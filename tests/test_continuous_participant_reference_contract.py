from __future__ import annotations

import unittest

from cera.continuous.contracts import (
    CharacterRoleLedgerV1,
    DiagnosticGroundingStatus,
    DiagnosticProtectedSemanticAdjudicationV1,
    DiagnosticStorySegmentV1,
    DiagnosticViolationClassification,
    ProtectedSemanticRelationKind,
    ProtectedUserAllowanceMode,
    ProtectedUserAllowanceV1,
    RichPlannerSequenceV1,
    RichSequenceBeatV1,
    StoryRealizationKind,
    StoryRealizationSegmentV1,
)
from cera.continuous.evidence import RequestEvidenceBindingRegistry
from cera.continuous.prompting import (
    CONTINUOUS_PLANNER_PROMPT_VERSION,
    CONTINUOUS_VALIDATOR_PROMPT_VERSION,
    PLANNER_STABLE_INSTRUCTIONS,
    VALIDATOR_STABLE_INSTRUCTIONS,
    compile_compact_writer_brief,
)
from cera.continuous.runtime import _planner_reference_only_character_ids
from cera.serialization import text_sha256


HANA = "character:hana_hanezawa"
SAKURA = "character:sakura_hanezawa"
ENNE = "character:enne_hanezawa"
TED = "character:ted"


def _beat(*, roles: CharacterRoleLedgerV1) -> RichSequenceBeatV1:
    return RichSequenceBeatV1(
        beat_key="include_offscreen_reference",
        roles=roles,
        evidence_grounded_perception=(
            "Hana recognizes that the current question can mention Enne without bringing her onstage."
        ),
        immediate_goal="Answer naturally while preserving the current two-person conversational floor.",
        relevant_character_pressures=(
            "Keep the answer useful without inventing participation by an absent family member.",
        ),
        competing_obligation_or_constraint=(
            "Enne may be mentioned, but she is not present or acting in this scene."
        ),
        selected_tactic="Let Hana make one bounded verbal reference and retain the active cast.",
        causal_explanation=(
            "A reference answers the question while avoiding unsupported action, knowledge, or presence."
        ),
        observable_action_or_dialogue_direction=(
            "Hana briefly refers to Enne while continuing the exchange with Ted."
        ),
        private_state_guidance=(
            "Keep Hana's private reasoning limited to her established conversational goal."
        ),
        physical_material_continuity=(
            "Hana and Sakura remain the only active NPCs; Enne receives no location or action."
        ),
        resulting_state=(
            "The reference is complete and the next meaningful choice remains with Ted."
        ),
        deepseek_realization_space=(
            "Choose natural phrasing for Hana's bounded reference without staging Enne or Ted.",
        ),
        protected_user_allowance=ProtectedUserAllowanceV1(
            mode=ProtectedUserAllowanceMode.NONE,
            source_binding_keys=(),
            explanation="Do not invent Ted's response, movement, or state.",
        ),
        source_evidence_bindings=("evidence_hana_active",),
    )


def _sequence(
    *,
    selected: tuple[str, ...] = (HANA, SAKURA),
    omitted: tuple[str, ...] = (ENNE,),
    roles: CharacterRoleLedgerV1 | None = None,
) -> RichPlannerSequenceV1:
    return RichPlannerSequenceV1(
        schema_version=RichPlannerSequenceV1.SCHEMA_VERSION,
        sequence_id="sequence:participant_reference",
        world_id="world:test",
        branch_id="branch:main",
        accepted_turn_id=None,
        scene_id="scene:conversation",
        selected_character_ids=selected,
        omitted_character_ids=omitted,
        beats=(
            _beat(
                roles=roles
                or CharacterRoleLedgerV1(
                    speaker_ids=(HANA,),
                    referenced_ids=(ENNE,),
                )
            ),
        ),
        final_stop_state="Stop after Hana's bounded answer, before inventing Ted's response.",
        unresolved_threads=("Ted may decide how to continue the conversation.",),
        provisional=True,
    )


def _registry() -> RequestEvidenceBindingRegistry:
    return RequestEvidenceBindingRegistry(
        world_id="world:test",
        branch_id="branch:main",
        turn_id="turn:test",
    )


class PlannerParticipantReferenceTests(unittest.TestCase):
    def test_selected_cast_contains_active_npcs_only(self) -> None:
        sequence = _sequence(selected=(HANA, SAKURA, TED))
        with self.assertRaisesRegex(
            PermissionError, "cannot contain the protected user"
        ):
            _planner_reference_only_character_ids(sequence)

    def test_omitted_character_is_authorized_only_as_reference(self) -> None:
        sequence = _sequence()
        self.assertEqual(
            _planner_reference_only_character_ids(sequence),
            (ENNE,),
        )

        brief = compile_compact_writer_brief(
            current_user_source="Continue the scene.",
            planner_sequence=sequence,
        )
        self.assertEqual(brief.active_cast, (HANA, SAKURA))
        self.assertNotIn(TED, brief.active_cast)
        self.assertNotIn(ENNE, brief.active_cast)
        self.assertIn("reference to another character", brief.hard_boundaries[1])

    def test_omitted_character_cannot_receive_an_active_role(self) -> None:
        sequence = _sequence(
            roles=CharacterRoleLedgerV1(
                speaker_ids=(HANA,),
                addressed_ids=(ENNE,),
            )
        )
        with self.assertRaisesRegex(
            PermissionError, "may appear only as a reference"
        ):
            _planner_reference_only_character_ids(sequence)

    def test_nonselected_character_must_be_in_omitted_cast(self) -> None:
        sequence = _sequence(omitted=())
        with self.assertRaisesRegex(
            PermissionError, "outside selected and omitted cast"
        ):
            _planner_reference_only_character_ids(sequence)

    def test_prompt_identities_and_closed_role_guidance_advance_together(self) -> None:
        self.assertEqual(
            CONTINUOUS_PLANNER_PROMPT_VERSION,
            "cera.continuous_planner_prompt.v17",
        )
        self.assertEqual(
            CONTINUOUS_VALIDATOR_PROMPT_VERSION,
            "cera.continuous_validator_prompt.v38",
        )
        for required in (
            "active NPC participants only",
            "must never contain character:ted",
            "may appear only in referenced_ids",
        ):
            self.assertIn(required, PLANNER_STABLE_INSTRUCTIONS)
        for required in (
            "Planner-authorized omitted character",
            "only in referenced_ids",
            "reference-only allowance",
            "Do not reject an exact authorized offscreen reference",
        ):
            self.assertIn(required, VALIDATOR_STABLE_INSTRUCTIONS)


class ValidatorParticipantReferenceTests(unittest.TestCase):
    def test_semantic_ledger_accepts_only_the_authorized_reference_role(self) -> None:
        story = "Hana mentioned Enne."
        segment = StoryRealizationSegmentV1(
            schema_version=StoryRealizationSegmentV1.SCHEMA_VERSION,
            segment_key="hana_mentions_enne",
            kind=StoryRealizationKind.DIALOGUE,
            output_start=0,
            output_end=len(story),
            exact_text=story,
            roles=CharacterRoleLedgerV1(
                speaker_ids=(HANA,),
                referenced_ids=(ENNE,),
            ),
            protected_user_source_claim_keys=(),
        )
        _registry().validate_validator_semantics(
            story_text=story,
            story_segments=(segment,),
            allowed_character_ids=(HANA, SAKURA),
            reference_only_character_ids=(ENNE,),
        )

        activated = StoryRealizationSegmentV1(
            schema_version=StoryRealizationSegmentV1.SCHEMA_VERSION,
            segment_key="enne_speaks",
            kind=StoryRealizationKind.DIALOGUE,
            output_start=0,
            output_end=len(story),
            exact_text=story,
            roles=CharacterRoleLedgerV1(speaker_ids=(ENNE,)),
            protected_user_source_claim_keys=(),
        )
        with self.assertRaisesRegex(
            PermissionError, "activated a reference-only character"
        ):
            _registry().validate_validator_semantics(
                story_text=story,
                story_segments=(activated,),
                allowed_character_ids=(HANA, SAKURA),
                reference_only_character_ids=(ENNE,),
            )

    def test_rejected_diagnostics_preserve_offscreen_reference_evidence(self) -> None:
        story = "Hana mentioned Enne."
        roles = CharacterRoleLedgerV1(
            speaker_ids=(HANA,),
            referenced_ids=(ENNE,),
        )
        segment = DiagnosticStorySegmentV1(
            schema_version=DiagnosticStorySegmentV1.SCHEMA_VERSION,
            segment_key="hana_mentions_enne",
            kind=StoryRealizationKind.DIALOGUE,
            output_start=0,
            output_end=len(story),
            exact_text=story,
            exact_text_sha256=text_sha256(story),
            roles=roles,
            grounding_status=DiagnosticGroundingStatus.GROUNDED,
            protected_user_source_claim_keys=(),
        )
        adjudication = DiagnosticProtectedSemanticAdjudicationV1(
            schema_version=(
                DiagnosticProtectedSemanticAdjudicationV1.SCHEMA_VERSION
            ),
            adjudication_key="hana_mentions_enne_adjudication",
            segment_key=segment.segment_key,
            output_start=0,
            output_end=len(story),
            exact_text_sha256=text_sha256(story),
            protected_user_id=TED,
            relation=ProtectedSemanticRelationKind.NONE,
            grounding_status=DiagnosticGroundingStatus.GROUNDED,
            violation_classification=DiagnosticViolationClassification.NONE,
            npc_assertion_owner_ids=(),
            protected_user_source_claim_keys=(),
        )
        _registry().validate_validator_diagnostics(
            story_text=story,
            diagnostic_story_segments=(segment,),
            diagnostic_protected_semantic_adjudications=(adjudication,),
            allowed_character_ids=(HANA, SAKURA),
            reference_only_character_ids=(ENNE,),
        )

        activated_roles = CharacterRoleLedgerV1(speaker_ids=(ENNE,))
        activated_segment = DiagnosticStorySegmentV1(
            schema_version=DiagnosticStorySegmentV1.SCHEMA_VERSION,
            segment_key=segment.segment_key,
            kind=StoryRealizationKind.DIALOGUE,
            output_start=0,
            output_end=len(story),
            exact_text=story,
            exact_text_sha256=text_sha256(story),
            roles=activated_roles,
            grounding_status=DiagnosticGroundingStatus.GROUNDED,
            protected_user_source_claim_keys=(),
        )
        with self.assertRaisesRegex(
            PermissionError, "activated a reference-only character"
        ):
            _registry().validate_validator_diagnostics(
                story_text=story,
                diagnostic_story_segments=(activated_segment,),
                diagnostic_protected_semantic_adjudications=(adjudication,),
                allowed_character_ids=(HANA, SAKURA),
                reference_only_character_ids=(ENNE,),
            )


if __name__ == "__main__":
    unittest.main()
