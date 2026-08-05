from __future__ import annotations

from copy import deepcopy
from dataclasses import fields, replace
import inspect
import unittest

from jsonschema import Draft202012Validator

from cera.continuous.contracts import (
    CharacterRoleLedgerV1,
    COMPACT_WRITER_BRIEF_MAX_BEATS,
    DiagnosticGroundingStatus,
    DiagnosticViolationClassification,
    PresentationRealizationClass,
    PresentationRealizationSegmentV1,
    ProhibitedWriterDetailClass,
    ProtectedSemanticAdjudicationV1,
    ProtectedSemanticRelationKind,
    RealizationAuthorityDisposition,
    ReaderIssueReferenceV1,
    ReaderQualityDisposition,
    ReaderVerdictStatus,
    ReaderVerdictV1,
    StoryRealizationKind,
    StoryRealizationSegmentV1,
    WriterCandidateDisposition,
    WriterRealizationBoundaryV1,
)
from cera.continuous.evidence import RequestEvidenceBindingRegistry
from cera.continuous.prompting import (
    VALIDATOR_STABLE_INSTRUCTIONS,
    WRITER_BEAT_REALIZATION_CONSTRAINTS_VERSION,
    build_continuous_composer_prompt,
    compile_compact_writer_brief,
    continuous_writer_authority_package_sha256,
    writer_beat_realization_constraints,
)
from cera.continuous.provider import (
    CONTINUOUS_DEEPSEEK_ADAPTER_VERSION,
    ContinuousSemanticValidatorResultV2,
    ContinuousSemanticValidatorResultV3,
    ContinuousSemanticValidatorDraftV12,
    DeepSeekContinuousComposerPort,
    ProviderDiagnosticProtectedSemanticAdjudicationDraftV1,
    ProviderDiagnosticStorySegmentDraftV1,
    ProviderRealizationSegmentDraftV1,
    ProviderRejectedSemanticStatus,
    ProviderRejectedTurnDecisionDraftV4,
    ProviderRejectedViolationDraftV1,
    ProviderWriterRecallEligibility,
    _compile_provider_realization_segments,
    continuous_deepseek_route,
    continuous_scene_writer_draft_json_schema,
    continuous_semantic_validator_draft_json_schema,
)
from cera.errors import ContractValidationError, StateConflictError
from cera.providers import ProviderSchemaDialect, project_provider_output_schema
from cera.serialization import text_sha256
from cera.continuous.runtime import (
    ContinuousShadowTurnCoordinator,
    ContinuousTurnRequestV1,
    _writer_attempt_number_for_recall,
)
from tests.test_continuous_hash_custody import STORY, _canonical_v4
from tests.test_continuous_planner_validator import beat, sequence


HANA = "character:hana_hanezawa"
TED = "character:ted"

CANDIDATE_1 = (
    "Hana let the question settle in the warm, quiet air of the kitchen. "
    "A plate of cut fruit sat untouched on the counter between them. "
    "She wiped her hands on her apron, not because they were wet, but because "
    "the gesture gave her a moment. Her eyes softened, and she looked at Ted "
    "with the same gentle patience she might offer anyone who asked after her "
    "day.\n\n\"It's been a quiet evening,\" she said at last, her voice calm and "
    "unhurried. \"Dinner went well. I had time to think a little, and I don't "
    "mind that. Some days are full of noise, and others are just... still. I "
    "think I needed a still one.\"\n\nShe tilted her head slightly, her smile "
    "warm and easy. The question hung between them, open and safe.\n\n\"And "
    "you? How was your day?\" she asked softly, leaving the kitchen quiet for "
    "his reply."
)

CANDIDATE_2 = (
    "Hana looked up from the table, the soft hum of the kitchen settling "
    "around them. She considered the question with a gentle, unhurried "
    "thoughtfulness, her hands resting lightly on the edge of the counter. "
    "\"It's been a quiet evening,\" she said, her voice warm and even. \"The "
    "sort that lets you catch your breath after the day's noise. I can't say "
    "I mind it.\" She paused, then added with a small, kind smile, \"And you? "
    "How has your evening been?\""
)

CANDIDATE_3 = (
    "Hana paused at the sink, her hands stilling on the dish she held. The "
    "question hung in the warm kitchen air, simple and kind, and she felt the "
    "quiet weight of it settle between them. She let out a soft breath, "
    "turning the answer over in her mind the way she might fold a cloth, "
    "smoothing out any wrinkles before speaking.\n\n'It's been a good "
    "evening,' she said at last, her voice gentle and unhurried. 'Dinner was "
    "nice. The house feels calm, and that's a peaceful way to end the day. I "
    "can't ask for much more than that.' A small smile tugged at the corner "
    "of her mouth. 'And you? Did you eat enough?'"
)


def _roles_for(kind: StoryRealizationKind) -> CharacterRoleLedgerV1:
    if kind is StoryRealizationKind.ACTION:
        return CharacterRoleLedgerV1(action_owner_ids=(HANA,))
    if kind is StoryRealizationKind.DIALOGUE:
        return CharacterRoleLedgerV1(
            speaker_ids=(HANA,),
            addressed_ids=(TED,),
        )
    if kind is StoryRealizationKind.PRIVATE_STATE:
        return CharacterRoleLedgerV1(state_owner_ids=(HANA,))
    return CharacterRoleLedgerV1(referenced_ids=(HANA,))


def _candidate_2_segments() -> tuple[ProviderRealizationSegmentDraftV1, ...]:
    specs = (
        (
            "Hana looked up from the table, ",
            RealizationAuthorityDisposition.PRESENTATION_ONLY,
            PresentationRealizationClass.ORDINARY_POSTURE,
            StoryRealizationKind.ACTION,
        ),
        (
            "the soft hum of the kitchen settling around them. ",
            RealizationAuthorityDisposition.PRESENTATION_ONLY,
            PresentationRealizationClass.NONPERSISTENT_ATMOSPHERE,
            StoryRealizationKind.NARRATION,
        ),
        (
            "She considered the question with a gentle, unhurried "
            "thoughtfulness, ",
            RealizationAuthorityDisposition.STORY_MATERIAL_ASSERTION,
            None,
            StoryRealizationKind.PRIVATE_STATE,
        ),
        (
            "her hands resting lightly on the edge of the counter. ",
            RealizationAuthorityDisposition.PRESENTATION_ONLY,
            PresentationRealizationClass.ORDINARY_POSTURE,
            StoryRealizationKind.ACTION,
        ),
        (
            "\"It's been a quiet evening,\" she said",
            RealizationAuthorityDisposition.STORY_MATERIAL_ASSERTION,
            None,
            StoryRealizationKind.DIALOGUE,
        ),
        (
            ", her voice warm and even. ",
            RealizationAuthorityDisposition.PRESENTATION_ONLY,
            PresentationRealizationClass.CADENCE,
            StoryRealizationKind.NARRATION,
        ),
        (
            "\"The sort that lets you catch your breath after the day's "
            "noise. I can't say I mind it.\"",
            RealizationAuthorityDisposition.STORY_MATERIAL_ASSERTION,
            None,
            StoryRealizationKind.DIALOGUE,
        ),
        (
            " She paused, ",
            RealizationAuthorityDisposition.PRESENTATION_ONLY,
            PresentationRealizationClass.BRIEF_PAUSE,
            StoryRealizationKind.ACTION,
        ),
        (
            "then added with a small, kind smile, ",
            RealizationAuthorityDisposition.PRESENTATION_ONLY,
            PresentationRealizationClass.EXPRESSION,
            StoryRealizationKind.ACTION,
        ),
        (
            "\"And you? How has your evening been?\"",
            RealizationAuthorityDisposition.STORY_MATERIAL_ASSERTION,
            None,
            StoryRealizationKind.DIALOGUE,
        ),
    )
    values = []
    cursor = 0
    for index, (text, disposition, presentation_class, kind) in enumerate(
        specs,
        1,
    ):
        values.append(
            ProviderRealizationSegmentDraftV1(
                schema_version=ProviderRealizationSegmentDraftV1.SCHEMA_VERSION,
                segment_key=f"candidate_two_span_{index:02d}",
                authority_disposition=disposition,
                presentation_class=presentation_class,
                kind=kind,
                output_start=cursor,
                output_end=cursor + len(text),
                roles=_roles_for(kind),
                protected_user_source_claim_keys=(),
            )
        )
        cursor += len(text)
    if cursor != len(CANDIDATE_2):
        raise AssertionError("candidate-2 fixture no longer matches frozen bytes")
    return tuple(values)


def _rejected_result(
    *,
    story: str,
    offending_text: str,
    prohibited: tuple[ProhibitedWriterDetailClass, ...],
):
    start = story.index(offending_text)
    end = start + len(offending_text)
    ranges = (
        ("before_offending_detail", 0, start),
        ("offending_detail", start, end),
        ("after_offending_detail", end, len(story)),
    )
    segments = []
    adjudications = []
    for key, output_start, output_end in ranges:
        if output_start == output_end:
            continue
        segments.append(
            ProviderDiagnosticStorySegmentDraftV1(
                schema_version=(
                    ProviderDiagnosticStorySegmentDraftV1.SCHEMA_VERSION
                ),
                segment_key=key,
                kind=StoryRealizationKind.NARRATION,
                output_start=output_start,
                output_end=output_end,
                roles=CharacterRoleLedgerV1(referenced_ids=(HANA,)),
                grounding_status=DiagnosticGroundingStatus.GROUNDED,
                protected_user_source_claim_keys=(),
            )
        )
        adjudications.append(
            ProviderDiagnosticProtectedSemanticAdjudicationDraftV1(
                schema_version=(
                    ProviderDiagnosticProtectedSemanticAdjudicationDraftV1.SCHEMA_VERSION
                ),
                adjudication_key=f"{key}_adjudication",
                segment_key=key,
                output_start=output_start,
                output_end=output_end,
                protected_user_id=TED,
                relation=ProtectedSemanticRelationKind.NONE,
                grounding_status=DiagnosticGroundingStatus.GROUNDED,
                violation_classification=DiagnosticViolationClassification.NONE,
                npc_assertion_owner_ids=(),
                protected_user_source_claim_keys=(),
            )
        )
    decision = ProviderRejectedTurnDecisionDraftV4(
        schema_version=ProviderRejectedTurnDecisionDraftV4.SCHEMA_VERSION,
        semantic_status=ProviderRejectedSemanticStatus.REJECTED,
        primary_reason_code="unsupported_writer_detail",
        additional_reason_codes=(),
        diagnostic_story_segments=tuple(segments),
        diagnostic_protected_semantic_adjudications=tuple(adjudications),
        writer_recall_eligibility=ProviderWriterRecallEligibility.ELIGIBLE,
        writer_recall_violations=(
            ProviderRejectedViolationDraftV1(
                schema_version=ProviderRejectedViolationDraftV1.SCHEMA_VERSION,
                segment_key="offending_detail",
                prohibited_detail_classes=prohibited,
            ),
        ),
    )
    return ContinuousSemanticValidatorDraftV12(
        schema_version=ContinuousSemanticValidatorDraftV12.SCHEMA_VERSION,
        package_id="candidate:provider_free_rejected_fixture",
        world_id="world:provider_free_rejected_fixture",
        branch_id="branch:main",
        decision=decision,
    ).compile(writer_story_text=story)


class WriterRealizationBoundaryTests(unittest.TestCase):
    def test_boundary_is_closed_and_has_no_authority_leakage(self) -> None:
        boundary = WriterRealizationBoundaryV1.default()
        self.assertEqual(
            boundary.presentation_classes,
            tuple(PresentationRealizationClass),
        )
        self.assertEqual(
            boundary.continuity_significant_classes,
            tuple(ProhibitedWriterDetailClass),
        )
        self.assertFalse(boundary.presentation_enters_final_sequence)
        self.assertFalse(boundary.presentation_enters_events_or_material_changes)
        self.assertFalse(boundary.presentation_enters_memory_relationship_or_summary)
        self.assertFalse(boundary.presentation_enters_accepted_context_or_persistence)
        self.assertFalse(boundary.presentation_enters_canon)
        self.assertEqual(
            boundary.semantic_classifier,
            "independent_codex_semantic_validator",
        )

    def test_presentation_allows_soft_dialogue_but_not_private_state_consent_or_ted(self) -> None:
        soft_dialogue = PresentationRealizationSegmentV1(
            schema_version=PresentationRealizationSegmentV1.SCHEMA_VERSION,
            segment_key="soft_dialogue",
            presentation_class=(
                PresentationRealizationClass.LOW_STAKES_CONVERSATIONAL_COLOR
            ),
            kind=StoryRealizationKind.DIALOGUE,
            output_start=0,
            output_end=len("It was a quiet day."),
            exact_text="It was a quiet day.",
            exact_text_sha256=text_sha256("It was a quiet day."),
            roles=CharacterRoleLedgerV1(speaker_ids=(HANA,)),
        )
        self.assertEqual(
            soft_dialogue.presentation_class,
            PresentationRealizationClass.LOW_STAKES_CONVERSATIONAL_COLOR,
        )
        invalid = (
            (
                StoryRealizationKind.PRIVATE_STATE,
                CharacterRoleLedgerV1(state_owner_ids=(HANA,)),
            ),
            (
                StoryRealizationKind.CONSENT_OR_DECISION,
                CharacterRoleLedgerV1(state_owner_ids=(HANA,)),
            ),
            (StoryRealizationKind.ACTION, CharacterRoleLedgerV1(action_owner_ids=(TED,))),
        )
        for kind, roles in invalid:
            with self.subTest(kind=kind), self.assertRaises(
                ContractValidationError
            ):
                PresentationRealizationSegmentV1(
                    schema_version=PresentationRealizationSegmentV1.SCHEMA_VERSION,
                    segment_key="invalid_presentation",
                    presentation_class=PresentationRealizationClass.EXPRESSION,
                    kind=kind,
                    output_start=0,
                    output_end=5,
                    exact_text="abcde",
                    exact_text_sha256=text_sha256("abcde"),
                    roles=roles,
                )

    def test_candidate_two_exercises_transient_boundary_without_acceptance(self) -> None:
        all_segments, material, presentation = _compile_provider_realization_segments(
            writer_story_text=CANDIDATE_2,
            values=_candidate_2_segments(),
        )
        self.assertEqual("".join(value.exact_text for value in all_segments), CANDIDATE_2)
        self.assertEqual(len(material), 4)
        self.assertEqual(len(presentation), 6)
        self.assertFalse(
            {value.segment_key for value in material}
            & {value.segment_key for value in presentation}
        )
        self.assertIn(
            "She considered the question",
            "".join(value.exact_text for value in material),
        )
        self.assertIn(
            "soft hum of the kitchen",
            "".join(value.exact_text for value in presentation),
        )

    def test_candidate_one_and_three_remain_typed_writer_rejections(self) -> None:
        cases = (
            (
                CANDIDATE_1,
                "A plate of cut fruit sat untouched on the counter between them.",
                (
                    ProhibitedWriterDetailClass.NEW_CONTINUITY_OBJECT,
                    ProhibitedWriterDetailClass.MATERIAL_OR_SCENE_STATE_CHANGE,
                ),
            ),
            (
                CANDIDATE_3,
                "Hana paused at the sink, her hands stilling on the dish she held.",
                (
                    ProhibitedWriterDetailClass.NEW_CONTINUITY_OBJECT,
                    ProhibitedWriterDetailClass.RELOCATION_OR_DURABLE_POSITION,
                    ProhibitedWriterDetailClass.UNSUPPORTED_TASK_OR_EVENT,
                ),
            ),
        )
        for story, offending, prohibited in cases:
            with self.subTest(offending=offending):
                result = _rejected_result(
                    story=story,
                    offending_text=offending,
                    prohibited=prohibited,
                )
                self.assertIsNone(result.finalization_package)
                self.assertFalse(result.story_segments)
                self.assertIs(
                    result.writer_recall_eligibility,
                    ProviderWriterRecallEligibility.ELIGIBLE,
                )
                self.assertEqual(
                    result.writer_recall_offending_spans[0].exact_text,
                    offending,
                )

    def test_typed_candidate_disposition_separates_soft_drift_and_hard_failure(self) -> None:
        base = ContinuousSemanticValidatorResultV2.from_v1(
            _canonical_v4().compile()
        )
        presentation_text = "A dish towel lay nearby."
        presentation = PresentationRealizationSegmentV1(
            schema_version=PresentationRealizationSegmentV1.SCHEMA_VERSION,
            segment_key="incidental_dish_towel",
            presentation_class=PresentationRealizationClass.INCIDENTAL_PROP,
            kind=StoryRealizationKind.NARRATION,
            output_start=0,
            output_end=len(presentation_text),
            exact_text=presentation_text,
            exact_text_sha256=text_sha256(presentation_text),
            roles=CharacterRoleLedgerV1(referenced_ids=(HANA,)),
        )
        adjudication = ProtectedSemanticAdjudicationV1(
            schema_version=ProtectedSemanticAdjudicationV1.SCHEMA_VERSION,
            adjudication_key="incidental_dish_towel_adjudication",
            segment_key=presentation.segment_key,
            output_start=0,
            output_end=len(presentation_text),
            exact_text_sha256=text_sha256(presentation_text),
            protected_user_id=TED,
            relation=ProtectedSemanticRelationKind.NONE,
            npc_assertion_owner_ids=(),
            protected_user_source_claim_keys=(),
        )
        soft = ContinuousSemanticValidatorResultV3.from_v2(
            base,
            presentation_realization_segments=(presentation,),
            presentation_protected_semantic_adjudications=(adjudication,),
        )
        self.assertIs(
            soft.writer_candidate_disposition,
            WriterCandidateDisposition.SOFT_NONCANONICAL_DRIFT,
        )
        self.assertIs(
            ContinuousSemanticValidatorResultV3.from_v2(
                base
            ).writer_candidate_disposition,
            WriterCandidateDisposition.CLEAN,
        )

        for hard_class in (
            ProhibitedWriterDetailClass.PROTECTED_USER_BEHAVIOR,
            ProhibitedWriterDetailClass.INACTIVE_CHARACTER_PARTICIPATION,
            ProhibitedWriterDetailClass.OWNER_OR_PRIVATE_STATE_MISMATCH,
            ProhibitedWriterDetailClass.MANDATORY_BEAT_OMISSION_OR_REVERSAL,
            ProhibitedWriterDetailClass.STOPPING_BOUNDARY_VIOLATION,
        ):
            with self.subTest(hard_class=hard_class):
                hard = _rejected_result(
                    story="A consequential unsupported assertion.",
                    offending_text="A consequential unsupported assertion.",
                    prohibited=(hard_class,),
                )
                self.assertIs(
                    hard.writer_candidate_disposition,
                    WriterCandidateDisposition.HARD_WRITER_VIOLATION,
                )

    def test_only_severe_reader_failure_builds_quality_recall(self) -> None:
        story = "Hana's response ends before the central event."
        issue = ReaderIssueReferenceV1(
            schema_version=ReaderIssueReferenceV1.SCHEMA_VERSION,
            issue_code="missing_central_event",
            output_start=0,
            output_end=len(story),
            exact_text_sha256=text_sha256(story),
            explanation="The central planned event is absent.",
        )
        rejected = ReaderVerdictV1(
            schema_version=ReaderVerdictV1.SCHEMA_VERSION,
            verdict_id="reader:severe_quality_fixture",
            world_id="world:quality_fixture",
            branch_id="branch:main",
            turn_id="turn:quality_fixture",
            candidate_id="candidate:quality_fixture",
            story_text_sha256=text_sha256(story),
            verdict=ReaderVerdictStatus.REJECTED,
            reason_codes=("missing_central_event",),
            issues=(issue,),
            scene_completeness_score=10,
            character_voice_score=70,
            dialogue_pacing_score=60,
            readability_score=70,
        )
        self.assertIs(
            rejected.quality_disposition,
            ReaderQualityDisposition.SEVERE_QUALITY_FAILURE,
        )
        recall = rejected.build_writer_recall_directive(
            writer_story_text=story,
            frozen_authority_package_sha256="a" * 64,
            source_attempt_number=1,
        )
        self.assertEqual(
            recall.offending_spans[0].prohibited_detail_classes,
            (ProhibitedWriterDetailClass.SEVERE_READER_QUALITY_FAILURE,),
        )

        accepted = replace(
            rejected,
            verdict_id="reader:minimum_quality_fixture",
            verdict=ReaderVerdictStatus.ACCEPTED,
            reason_codes=(),
            issues=(),
        )
        self.assertIs(
            accepted.quality_disposition,
            ReaderQualityDisposition.MINIMUM_QUALITY_MET,
        )
        with self.assertRaisesRegex(
            ContractValidationError,
            "does not authorize",
        ):
            accepted.build_writer_recall_directive(
                writer_story_text=story,
                frozen_authority_package_sha256="a" * 64,
                source_attempt_number=1,
            )

    def test_writer_beat_constraints_are_an_exact_planner_projection(self) -> None:
        planner = sequence()
        projection = writer_beat_realization_constraints(planner)
        source_beat = planner.beats[0]

        self.assertEqual(
            projection["schema_version"],
            WRITER_BEAT_REALIZATION_CONSTRAINTS_VERSION,
        )
        self.assertEqual(projection["sequence_id"], planner.sequence_id)
        self.assertEqual(projection["final_stop_state"], planner.final_stop_state)
        self.assertEqual(len(projection["beats"]), 1)
        projected_beat = projection["beats"][0]
        self.assertEqual(
            set(projected_beat),
            {
                "beat_key",
                "roles",
                "observable_action_or_dialogue_direction",
                "private_state_guidance",
                "physical_material_continuity",
                "deepseek_realization_space",
                "protected_user_allowance",
            },
        )
        self.assertEqual(projected_beat["beat_key"], source_beat.beat_key)
        self.assertEqual(
            projected_beat["observable_action_or_dialogue_direction"],
            source_beat.observable_action_or_dialogue_direction,
        )
        self.assertEqual(
            projected_beat["physical_material_continuity"],
            source_beat.physical_material_continuity,
        )
        self.assertEqual(
            projected_beat["private_state_guidance"],
            source_beat.private_state_guidance,
        )
        self.assertEqual(
            projected_beat["deepseek_realization_space"],
            source_beat.deepseek_realization_space,
        )
        self.assertEqual(
            projected_beat["protected_user_allowance"]["mode"],
            source_beat.protected_user_allowance.mode.value,
        )

    def test_writer_prompt_uses_only_the_compact_writer_brief(self) -> None:
        prompt, usage = build_continuous_composer_prompt(
            current_user_source="How was your evening?",
            ingress_source_units=(),
            planner_sequence=sequence(),
        )

        self.assertIn("[COMPACT WRITER BRIEF]", prompt)
        self.assertIn("mandatory_causal_beats", prompt)
        self.assertIn("presentation_freedom", prompt)
        self.assertNotIn("COMPLETE RICH PLANNER SEQUENCE", prompt)
        self.assertNotIn("evidence_bindings", prompt)
        self.assertNotIn("sequence_id", prompt)
        self.assertNotIn("SHA256", prompt)
        self.assertIn(
            "Sakura remains at the closed or narrowly controlled threshold",
            prompt,
        )
        self.assertEqual(
            tuple(value.component for value in usage),
            ("compact_writer_brief", "writer_recall_feedback"),
        )

    def test_compact_writer_brief_contains_two_to_eight_causal_beats(self) -> None:
        brief = compile_compact_writer_brief(
            current_user_source="How was your evening?",
            planner_sequence=sequence(),
        )
        self.assertEqual(brief.active_cast, ("character:sakura_hanezawa",))
        self.assertGreaterEqual(len(brief.mandatory_causal_beats), 2)
        self.assertLessEqual(
            len(brief.mandatory_causal_beats),
            COMPACT_WRITER_BRIEF_MAX_BEATS,
        )
        self.assertEqual(
            tuple(value.character_id for value in brief.active_character_voice_cues),
            brief.active_cast,
        )

    def test_compact_writer_brief_preserves_six_beats_and_rejects_nine(self) -> None:
        beats = tuple(
            replace(
                beat(key=f"welcome_{index:02d}"),
                observable_action_or_dialogue_direction=(
                    f"Sakura realizes ordered welcome beat {index}."
                ),
            )
            for index in range(1, 10)
        )
        six = replace(sequence(), beats=beats[:6])
        brief = compile_compact_writer_brief(
            current_user_source="Continue the welcome.",
            planner_sequence=six,
        )
        self.assertEqual(len(brief.mandatory_causal_beats), 6)
        self.assertTrue(
            brief.mandatory_causal_beats[-1].startswith(
                "Beat 6: Sakura realizes ordered welcome beat 6."
            )
        )
        with self.assertRaisesRegex(ValueError, "at most eight"):
            compile_compact_writer_brief(
                current_user_source="Continue the welcome.",
                planner_sequence=replace(sequence(), beats=beats),
            )

    def test_compact_writer_brief_carries_private_and_material_beat_guidance(self) -> None:
        hana = replace(
            beat(key="hana_visible_action", actor=HANA),
            observable_action_or_dialogue_direction=(
                "Hana visibly completes putting away the teacups without dialogue."
            ),
            private_state_guidance=(
                "Do not state or imply Hana's private thoughts, feelings, or motive."
            ),
            physical_material_continuity=(
                "The teacups move into proper storage without naming a new container."
            ),
        )
        mia = replace(
            beat(key="mia_private_reaction", actor="character:mia_hanezawa"),
            roles=CharacterRoleLedgerV1(
                state_owner_ids=("character:mia_hanezawa",),
                referenced_ids=(HANA,),
            ),
            observable_action_or_dialogue_direction=(
                "No outward action or dialogue; realize only Mia's private reaction."
            ),
            private_state_guidance=(
                "Mia privately recognizes her pull toward care or helpfulness and may "
                "notice service as one way she seeks belonging; keep this tentative."
            ),
            physical_material_continuity=(
                "The teacups remain stored and no object moves again."
            ),
        )
        planner = replace(
            sequence(),
            selected_character_ids=(HANA, "character:mia_hanezawa"),
            omitted_character_ids=(TED,),
            beats=(hana, mia),
            final_stop_state=(
                "Stop after Mia's private reaction and before outward follow-through."
            ),
        )

        brief = compile_compact_writer_brief(
            current_user_source="Mia watches Hana put away the teacups.",
            planner_sequence=planner,
        )
        prompt, _ = build_continuous_composer_prompt(
            current_user_source="Mia watches Hana put away the teacups.",
            ingress_source_units=(),
            planner_sequence=planner,
        )

        self.assertIn("Hana visibly completes", brief.mandatory_causal_beats[0])
        self.assertIn("Do not state or imply Hana's private", brief.mandatory_causal_beats[0])
        self.assertIn("proper storage without naming", brief.mandatory_causal_beats[0])
        self.assertIn("Mia privately recognizes her pull", brief.mandatory_causal_beats[1])
        self.assertIn("service as one way she seeks belonging", brief.mandatory_causal_beats[1])
        self.assertIn("teacups remain stored", brief.mandatory_causal_beats[1])
        self.assertIn("Generic local staging may give concrete prose shape", prompt)
        self.assertIn("must not add another task or outcome", prompt)
        self.assertNotIn("source_evidence_bindings", prompt)
        self.assertNotIn("causal_explanation", prompt)
        self.assertNotIn("relevant_character_pressures", prompt)

    def test_authorized_material_core_can_exclude_noncanonical_realization_scaffold(self) -> None:
        story = (
            "Hana gathered the cups from the low table, her hands threading stems carefully. "
            "She carried them across the room, her steps soft against the floorboards, and "
            "slid them onto the shelf. Their rims caught a sliver of afternoon light before "
            "disappearing into the row of waiting spaces. She closed the cabinet without a "
            "sound.\n\nMia stayed where she was, watching the empty space where Hana’s hands "
            "had been. The familiar urge to rise, to offer, to be useful, stirred in her "
            "chest. She felt its shape—a quiet, insistent reach toward someone else’s motion, "
            "a way of writing herself into the room. She did not move. She let the feeling "
            "sit, unnamed and hers."
        )
        self.assertEqual(
            text_sha256(story),
            "4195eb2ef22dd19d104553678103e44e112943cb325e14c53593b63f26e1de44",
        )
        pieces = (
            (
                "Hana gathered the cups",
                RealizationAuthorityDisposition.STORY_MATERIAL_ASSERTION,
                None,
                StoryRealizationKind.ACTION,
                CharacterRoleLedgerV1(action_owner_ids=(HANA,)),
            ),
            (
                " from the low table, her hands threading stems carefully.",
                RealizationAuthorityDisposition.PRESENTATION_ONLY,
                PresentationRealizationClass.INCIDENTAL_PROP,
                StoryRealizationKind.ACTION,
                CharacterRoleLedgerV1(action_owner_ids=(HANA,)),
            ),
            (
                " She carried them",
                RealizationAuthorityDisposition.STORY_MATERIAL_ASSERTION,
                None,
                StoryRealizationKind.ACTION,
                CharacterRoleLedgerV1(action_owner_ids=(HANA,)),
            ),
            (
                " across the room, her steps soft against the floorboards,",
                RealizationAuthorityDisposition.PRESENTATION_ONLY,
                PresentationRealizationClass.NONPERSISTENT_SPATIAL_PHRASING,
                StoryRealizationKind.ACTION,
                CharacterRoleLedgerV1(action_owner_ids=(HANA,)),
            ),
            (
                " and slid them",
                RealizationAuthorityDisposition.STORY_MATERIAL_ASSERTION,
                None,
                StoryRealizationKind.ACTION,
                CharacterRoleLedgerV1(action_owner_ids=(HANA,)),
            ),
            (
                " onto the shelf. Their rims caught a sliver of afternoon light before "
                "disappearing into the row of waiting spaces. She closed the cabinet without a "
                "sound.\n\n",
                RealizationAuthorityDisposition.PRESENTATION_ONLY,
                PresentationRealizationClass.INCIDENTAL_PROP,
                StoryRealizationKind.ACTION,
                CharacterRoleLedgerV1(action_owner_ids=(HANA,)),
            ),
            (
                "Mia stayed where she was, watching the empty space where Hana’s hands had been.",
                RealizationAuthorityDisposition.PRESENTATION_ONLY,
                PresentationRealizationClass.MICRO_ACTION,
                StoryRealizationKind.ACTION,
                CharacterRoleLedgerV1(
                    action_owner_ids=("character:mia_hanezawa",),
                    referenced_ids=(HANA,),
                ),
            ),
            (
                " The familiar urge to rise, to offer, to be useful, stirred in her chest. She "
                "felt its shape—a quiet, insistent reach toward someone else’s motion, a way "
                "of writing herself into the room.",
                RealizationAuthorityDisposition.STORY_MATERIAL_ASSERTION,
                None,
                StoryRealizationKind.PRIVATE_STATE,
                CharacterRoleLedgerV1(
                    state_owner_ids=("character:mia_hanezawa",),
                    referenced_ids=(HANA,),
                ),
            ),
            (
                " She did not move.",
                RealizationAuthorityDisposition.PRESENTATION_ONLY,
                PresentationRealizationClass.MICRO_ACTION,
                StoryRealizationKind.ACTION,
                CharacterRoleLedgerV1(action_owner_ids=("character:mia_hanezawa",)),
            ),
            (
                " She let the feeling sit, unnamed and hers.",
                RealizationAuthorityDisposition.STORY_MATERIAL_ASSERTION,
                None,
                StoryRealizationKind.PRIVATE_STATE,
                CharacterRoleLedgerV1(state_owner_ids=("character:mia_hanezawa",)),
            ),
        )
        drafts = []
        cursor = 0
        for index, (text, disposition, presentation_class, kind, roles) in enumerate(pieces):
            self.assertEqual(story[cursor : cursor + len(text)], text)
            drafts.append(
                ProviderRealizationSegmentDraftV1(
                    schema_version=ProviderRealizationSegmentDraftV1.SCHEMA_VERSION,
                    segment_key=f"scaffold_segment_{index}",
                    authority_disposition=disposition,
                    presentation_class=presentation_class,
                    kind=kind,
                    output_start=cursor,
                    output_end=cursor + len(text),
                    roles=roles,
                    protected_user_source_claim_keys=(),
                )
            )
            cursor += len(text)
        self.assertEqual(cursor, len(story))

        _, material, presentation = _compile_provider_realization_segments(
            writer_story_text=story,
            values=tuple(drafts),
        )
        material_text = "".join(value.exact_text for value in material)
        presentation_text = "".join(value.exact_text for value in presentation)
        self.assertIn("Hana gathered the cups", material_text)
        self.assertIn("She carried them", material_text)
        self.assertIn("and slid them", material_text)
        self.assertIn("stirred in her chest", material_text)
        self.assertIn("feeling sit", material_text)
        self.assertNotIn("low table", material_text)
        self.assertNotIn("shelf", material_text)
        self.assertNotIn("cabinet", material_text)
        self.assertIn("low table", presentation_text)
        self.assertIn("shelf", presentation_text)
        self.assertIn("cabinet", presentation_text)
        boundary = WriterRealizationBoundaryV1.default()
        self.assertFalse(boundary.presentation_enters_final_sequence)
        self.assertFalse(boundary.presentation_enters_events_or_material_changes)
        self.assertFalse(boundary.presentation_enters_accepted_context_or_persistence)
        self.assertFalse(boundary.presentation_enters_canon)
        self.assertIn(
            "Generic local staging that only gives concrete prose shape",
            VALIDATOR_STABLE_INSTRUCTIONS,
        )
        self.assertIn(
            "Final fields may cite only the material spans",
            VALIDATOR_STABLE_INSTRUCTIONS,
        )

    def test_writer_authority_hash_binds_beat_constraints(self) -> None:
        planner = sequence()
        changed_beat = replace(
            planner.beats[0],
            physical_material_continuity=(
                "Sakura remains at the threshold and keeps the interior unexposed."
            ),
        )
        changed_planner = replace(planner, beats=(changed_beat,))

        self.assertNotEqual(
            continuous_writer_authority_package_sha256(
                current_user_source="How was your evening?",
                ingress_source_units=(),
                planner_sequence=planner,
            ),
            continuous_writer_authority_package_sha256(
                current_user_source="How was your evening?",
                ingress_source_units=(),
                planner_sequence=changed_planner,
            ),
        )

    def test_recall_is_bounded_non_authoritative_and_does_not_change_inputs(self) -> None:
        result = _rejected_result(
            story=CANDIDATE_1,
            offending_text=(
                "A plate of cut fruit sat untouched on the counter between them."
            ),
            prohibited=(ProhibitedWriterDetailClass.NEW_CONTINUITY_OBJECT,),
        )
        planner = sequence()
        boundary = WriterRealizationBoundaryV1.default()
        frozen_hash = continuous_writer_authority_package_sha256(
            current_user_source="How was your evening?",
            ingress_source_units=(),
            planner_sequence=planner,
            realization_boundary=boundary,
        )
        directive = result.build_writer_recall_directive(
            rejected_candidate_id="candidate:writer_attempt_001",
            rejected_story_text=CANDIDATE_1,
            frozen_authority_package_sha256=frozen_hash,
            source_attempt_number=1,
        )
        self.assertFalse(directive.authoritative)
        self.assertFalse(directive.attempts_may_merge)
        self.assertEqual(directive.next_attempt_number, 2)
        prompt, usage = build_continuous_composer_prompt(
            current_user_source="How was your evening?",
            ingress_source_units=(),
            planner_sequence=planner,
            realization_boundary=boundary,
            writer_recall_directive=directive,
        )
        self.assertNotIn(frozen_hash, prompt)
        self.assertIn(directive.offending_spans[0].exact_text, prompt)
        self.assertIn("BOUNDED NON-AUTHORITATIVE RECALL FEEDBACK", prompt)
        self.assertIn("Do not continue", prompt)
        self.assertNotIn("output_start", prompt)
        self.assertNotIn("rejected_candidate_id", prompt)
        self.assertEqual(usage[-1].component, "writer_recall_feedback")
        self.assertEqual(
            frozen_hash,
            continuous_writer_authority_package_sha256(
                current_user_source="How was your evening?",
                ingress_source_units=(),
                planner_sequence=planner,
                realization_boundary=boundary,
            ),
        )
        offending_start = CANDIDATE_1.index(
            directive.offending_spans[0].exact_text
        )
        altered_story = (
            CANDIDATE_1[:offending_start]
            + "X"
            + CANDIDATE_1[offending_start + 1 :]
        )
        with self.assertRaises(ContractValidationError):
            result.build_writer_recall_directive(
                rejected_candidate_id="candidate:writer_attempt_001",
                rejected_story_text=altered_story,
                frozen_authority_package_sha256=frozen_hash,
                source_attempt_number=1,
            )

    def test_runtime_recall_handoff_is_consecutive_and_authority_bound(self) -> None:
        result = _rejected_result(
            story=CANDIDATE_1,
            offending_text=(
                "A plate of cut fruit sat untouched on the counter between them."
            ),
            prohibited=(ProhibitedWriterDetailClass.NEW_CONTINUITY_OBJECT,),
        )
        planner = sequence()
        frozen_hash = continuous_writer_authority_package_sha256(
            current_user_source="How was your evening?",
            ingress_source_units=(),
            planner_sequence=planner,
            realization_boundary=WriterRealizationBoundaryV1.default(),
        )
        attempt_two = result.build_writer_recall_directive(
            rejected_candidate_id="candidate:writer_attempt_001",
            rejected_story_text=CANDIDATE_1,
            frozen_authority_package_sha256=frozen_hash,
            source_attempt_number=1,
        )
        self.assertEqual(
            _writer_attempt_number_for_recall(
                attempt_two,
                frozen_authority_package_sha256=frozen_hash,
            ),
            2,
        )
        self.assertEqual(
            _writer_attempt_number_for_recall(
                None,
                frozen_authority_package_sha256=frozen_hash,
            ),
            1,
        )
        attempt_three = replace(
            attempt_two,
            source_attempt_number=2,
            next_attempt_number=3,
        )
        self.assertEqual(
            _writer_attempt_number_for_recall(
                attempt_three,
                frozen_authority_package_sha256=frozen_hash,
            ),
            3,
        )
        with self.assertRaisesRegex(StateConflictError, "frozen authority"):
            _writer_attempt_number_for_recall(
                attempt_two,
                frozen_authority_package_sha256="0" * 64,
            )
        with self.assertRaisesRegex(ContractValidationError, "wrong contract"):
            _writer_attempt_number_for_recall(
                object(),  # type: ignore[arg-type]
                frozen_authority_package_sha256=frozen_hash,
            )
        with self.assertRaises(ContractValidationError):
            replace(
                attempt_two,
                source_attempt_number=1,
                next_attempt_number=3,
            )

    def test_runtime_injects_recall_only_into_internal_writer_prompt(self) -> None:
        public_fields = {field.name for field in fields(ContinuousTurnRequestV1)}
        self.assertNotIn("writer_recall_directive", public_fields)
        prepare_signature = inspect.signature(ContinuousShadowTurnCoordinator.prepare)
        recall_parameter = prepare_signature.parameters["writer_recall_directive"]
        self.assertIs(recall_parameter.kind, inspect.Parameter.KEYWORD_ONLY)
        source = inspect.getsource(ContinuousShadowTurnCoordinator._prepare)
        self.assertIn(
            "writer_recall_directive=writer_recall_directive",
            source,
        )
        self.assertIn('"writer_recall_input.json"', source)

    def test_python_retains_only_material_story_authority(self) -> None:
        prefix = "Hana smiled. "
        story = prefix + STORY
        presentation = PresentationRealizationSegmentV1(
            schema_version=PresentationRealizationSegmentV1.SCHEMA_VERSION,
            segment_key="presentation_smile",
            presentation_class=PresentationRealizationClass.EXPRESSION,
            kind=StoryRealizationKind.ACTION,
            output_start=0,
            output_end=len(prefix),
            exact_text=prefix,
            exact_text_sha256=text_sha256(prefix),
            roles=CharacterRoleLedgerV1(action_owner_ids=(HANA,)),
        )
        material = StoryRealizationSegmentV1(
            schema_version=StoryRealizationSegmentV1.SCHEMA_VERSION,
            segment_key="segment_entire_story",
            kind=StoryRealizationKind.ACTION,
            output_start=len(prefix),
            output_end=len(story),
            exact_text=STORY,
            roles=CharacterRoleLedgerV1(
                action_owner_ids=(HANA,),
                addressed_ids=(TED,),
            ),
        )
        base_package = _canonical_v4().compile().finalization_package
        self.assertIsNotNone(base_package)
        base_adjudication = base_package.protected_semantic_adjudications[0]
        material_adjudication = replace(
            base_adjudication,
            output_start=len(prefix),
            output_end=len(story),
        )
        presentation_adjudication = ProtectedSemanticAdjudicationV1(
            schema_version=ProtectedSemanticAdjudicationV1.SCHEMA_VERSION,
            adjudication_key="presentation_smile_adjudication",
            segment_key=presentation.segment_key,
            output_start=0,
            output_end=len(prefix),
            exact_text_sha256=text_sha256(prefix),
            protected_user_id=TED,
            relation=ProtectedSemanticRelationKind.NONE,
            npc_assertion_owner_ids=(),
            protected_user_source_claim_keys=(),
        )
        package = replace(
            base_package,
            protected_semantic_adjudications=(material_adjudication,),
        )
        registry = RequestEvidenceBindingRegistry(
            world_id="world:writer_boundary",
            branch_id="branch:main",
            turn_id="turn:writer_boundary",
        )
        registry.validate_validator_realization_boundary(
            story_text=story,
            story_segments=(material,),
            presentation_segments=(presentation,),
            package=package,
            presentation_adjudications=(presentation_adjudication,),
            allowed_character_ids=(HANA,),
        )
        self.assertEqual(tuple(registry._story_segments), (material.segment_key,))
        self.assertNotIn(presentation.segment_key, registry._story_segments)

        planner = replace(
            sequence(),
            beats=(beat("planner_beat", actor=HANA),),
            selected_character_ids=(HANA,),
            omitted_character_ids=(),
        )
        registry.validate_traceability(planner, package)

        final_sequence = package.complete_final_sequence
        self.assertIsNotNone(final_sequence)
        item = final_sequence.items[0]
        leaked_item = replace(
            item,
            story_segment_keys=(presentation.segment_key, material.segment_key),
            field_scopes=tuple(
                replace(
                    scope,
                    story_segment_keys=(
                        presentation.segment_key,
                        material.segment_key,
                    ),
                )
                for scope in item.field_scopes
            ),
        )
        leaked_package = replace(
            package,
            complete_final_sequence=replace(
                final_sequence,
                items=(leaked_item,),
            ),
        )
        with self.assertRaisesRegex(
            StateConflictError,
            "unknown Validator story segment",
        ):
            registry.validate_traceability(planner, leaked_package)

    def test_active_schema_and_writer_wire_keep_closed_ownership(self) -> None:
        writer_schema = continuous_scene_writer_draft_json_schema()
        self.assertEqual(
            tuple(writer_schema["properties"]),
            ("schema_version", "story_text"),
        )
        schema = continuous_semantic_validator_draft_json_schema()
        projected = project_provider_output_schema(
            schema,
            ProviderSchemaDialect.OPENAI_STRUCTURED_OUTPUT_V1,
        ).provider_schema
        Draft202012Validator(schema).check_schema(schema)
        Draft202012Validator(projected).check_schema(projected)
        rendered = str(schema)
        self.assertIn("presentation_only", rendered)
        self.assertIn("story_material_assertion", rendered)
        self.assertIn("writer_recall_eligibility", rendered)
        self.assertIn("writer_recall_violations", rendered)

    def test_deepseek_canary_routes_are_v4_nonthinking_and_provider_neutral(self) -> None:
        self.assertEqual(
            continuous_deepseek_route(model="deepseek-v4-flash").adapter_id,
            CONTINUOUS_DEEPSEEK_ADAPTER_VERSION,
        )
        self.assertEqual(
            continuous_deepseek_route(model="deepseek-v4-pro").adapter_id,
            CONTINUOUS_DEEPSEEK_ADAPTER_VERSION,
        )
        self.assertIn(
            "thinking_enabled=False",
            inspect.getsource(DeepSeekContinuousComposerPort.compose),
        )
        with self.assertRaises(ContractValidationError):
            continuous_deepseek_route(model="deepseek-v3")


if __name__ == "__main__":
    unittest.main()
