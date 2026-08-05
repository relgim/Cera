from __future__ import annotations

from dataclasses import replace
import unittest

from jsonschema import Draft202012Validator

from cera.continuous.contracts import (
    CharacterRoleLedgerV1,
    DiagnosticGroundingStatus,
    DiagnosticViolationClassification,
    PresentationRealizationClass,
    PresentationRealizationSegmentV1,
    ProtectedSemanticAdjudicationV1,
    ProtectedSemanticRelationKind,
    StoryRealizationKind,
    StoryRealizationSegmentV1,
)
from cera.continuous.evidence import RequestEvidenceBindingRegistry
from cera.continuous.provider import (
    CONTINUOUS_VALIDATOR_ADAPTER_VERSION,
    ContinuousSemanticValidatorDraftV13,
    ProviderDiagnosticProtectedSemanticAdjudicationDraftV1,
    ProviderDiagnosticStorySegmentDraftV1,
    ProviderProtectedSemanticAdjudicationDraftV1,
    _compile_provider_protected_adjudications,
    _validate_diagnostic_adjudication_against_segment,
    continuous_semantic_validator_draft_json_schema,
)
from cera.errors import ContractValidationError
from cera.providers import ProviderSchemaDialect, project_provider_output_schema
from cera.serialization import text_sha256
from tests.test_continuous_hash_custody import STORY, _canonical_v4
from tests.test_continuous_planner_validator import beat, sequence


HANA = "character:hana_hanezawa"
TED = "character:ted"
FROZEN_FLASH_CANDIDATE = (
    "Hana pauses, the quiet of the kitchen settling around them. She glances "
    "down at her hands, then back up with a soft, reflective smile. \"It's been "
    "a quiet evening, a good one. I had some time to think, and the dinner came "
    "together nicely. How has your day been?\""
)
NEUTRAL_SPAN = " the quiet of the kitchen settling around them."


def _neutral_story_segment(
    *,
    roles: CharacterRoleLedgerV1 | None = None,
) -> StoryRealizationSegmentV1:
    return StoryRealizationSegmentV1(
        schema_version=StoryRealizationSegmentV1.SCHEMA_VERSION,
        segment_key="neutral_kitchen_atmosphere",
        kind=StoryRealizationKind.NARRATION,
        output_start=12,
        output_end=59,
        exact_text=NEUTRAL_SPAN,
        roles=roles
        or CharacterRoleLedgerV1(referenced_ids=(HANA, TED)),
        protected_user_source_claim_keys=(),
    )


def _neutral_presentation(
    segment: StoryRealizationSegmentV1,
    *,
    presentation_class: PresentationRealizationClass = (
        PresentationRealizationClass.NONPERSISTENT_ATMOSPHERE
    ),
) -> PresentationRealizationSegmentV1:
    return PresentationRealizationSegmentV1(
        schema_version=PresentationRealizationSegmentV1.SCHEMA_VERSION,
        segment_key=segment.segment_key,
        presentation_class=presentation_class,
        kind=segment.kind,
        output_start=segment.output_start,
        output_end=segment.output_end,
        exact_text=segment.exact_text,
        exact_text_sha256=text_sha256(segment.exact_text),
        roles=segment.roles,
    )


def _neutral_provider_adjudication(
    segment: StoryRealizationSegmentV1,
) -> ProviderProtectedSemanticAdjudicationDraftV1:
    return ProviderProtectedSemanticAdjudicationDraftV1(
        schema_version=ProviderProtectedSemanticAdjudicationDraftV1.SCHEMA_VERSION,
        adjudication_key="neutral_kitchen_atmosphere_adjudication",
        segment_key=segment.segment_key,
        output_start=segment.output_start,
        output_end=segment.output_end,
        protected_user_id=TED,
        relation=ProtectedSemanticRelationKind.NEUTRAL_PRESENTATION_REFERENCE,
        npc_assertion_owner_ids=(),
        protected_user_source_claim_keys=(),
    )


class NeutralProtectedReferenceTests(unittest.TestCase):
    def test_frozen_flash_span_is_exact(self) -> None:
        self.assertEqual(FROZEN_FLASH_CANDIDATE[12:59], NEUTRAL_SPAN)

    def test_active_schema_versions_and_projects_closed_relation(self) -> None:
        schema = continuous_semantic_validator_draft_json_schema()
        Draft202012Validator.check_schema(schema)
        projected = project_provider_output_schema(
            schema,
            ProviderSchemaDialect.OPENAI_STRUCTURED_OUTPUT_V1,
        ).provider_schema
        Draft202012Validator.check_schema(projected)
        self.assertEqual(
            schema["properties"]["schema_version"]["const"],
            ContinuousSemanticValidatorDraftV13.SCHEMA_VERSION,
        )
        self.assertEqual(
            ContinuousSemanticValidatorDraftV13.SCHEMA_VERSION,
            "cera.continuous_semantic_validator_draft.v13",
        )
        self.assertEqual(
            CONTINUOUS_VALIDATOR_ADAPTER_VERSION,
            "cera.continuous_validator_adapter.v28",
        )
        self.assertIn(
            ProtectedSemanticRelationKind.NEUTRAL_PRESENTATION_REFERENCE.value,
            str(schema),
        )
        self.assertIn(
            ProtectedSemanticRelationKind.NEUTRAL_PRESENTATION_REFERENCE.value,
            str(projected),
        )

    def test_provider_accepts_only_nonpersistent_neutral_narration(self) -> None:
        segment = _neutral_story_segment()
        presentation = _neutral_presentation(segment)
        adjudications = _compile_provider_protected_adjudications(
            writer_story_text=FROZEN_FLASH_CANDIDATE,
            story_segments=(segment,),
            adjudications=(_neutral_provider_adjudication(segment),),
            presentation_segments=(presentation,),
        )
        self.assertEqual(
            adjudications[0].relation,
            ProtectedSemanticRelationKind.NEUTRAL_PRESENTATION_REFERENCE,
        )

        invalid_presentations = (
            (),
            (
                _neutral_presentation(
                    segment,
                    presentation_class=PresentationRealizationClass.CADENCE,
                ),
            ),
        )
        for invalid in invalid_presentations:
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(
                    ContractValidationError,
                    "nonpersistent presentation-only narration",
                ):
                    _compile_provider_protected_adjudications(
                        writer_story_text=FROZEN_FLASH_CANDIDATE,
                        story_segments=(segment,),
                        adjudications=(_neutral_provider_adjudication(segment),),
                        presentation_segments=invalid,
                    )

        missing_ted = _neutral_story_segment(
            roles=CharacterRoleLedgerV1(referenced_ids=(HANA,))
        )
        with self.assertRaisesRegex(
            ContractValidationError,
            "nonpersistent presentation-only narration",
        ):
            _compile_provider_protected_adjudications(
                writer_story_text=FROZEN_FLASH_CANDIDATE,
                story_segments=(missing_ted,),
                adjudications=(_neutral_provider_adjudication(missing_ted),),
                presentation_segments=(_neutral_presentation(missing_ted),),
            )

        action_segment = replace(
            segment,
            kind=StoryRealizationKind.ACTION,
            roles=CharacterRoleLedgerV1(
                action_owner_ids=(HANA,),
                referenced_ids=(TED,),
            ),
        )
        with self.assertRaisesRegex(
            ContractValidationError,
            "nonpersistent presentation-only narration",
        ):
            _compile_provider_protected_adjudications(
                writer_story_text=FROZEN_FLASH_CANDIDATE,
                story_segments=(action_segment,),
                adjudications=(_neutral_provider_adjudication(action_segment),),
                presentation_segments=(_neutral_presentation(action_segment),),
            )

    def test_existing_npc_owned_reference_still_requires_an_owner(self) -> None:
        segment = _neutral_story_segment()
        draft = replace(
            _neutral_provider_adjudication(segment),
            relation=ProtectedSemanticRelationKind.REFERENCED_ONLY_BY_NPC,
        )
        with self.assertRaisesRegex(
            ContractValidationError,
            "requires an exact NPC predicate owner",
        ):
            draft.compile(
                writer_story_text=FROZEN_FLASH_CANDIDATE,
                story_segment=segment,
            )

    def test_diagnostic_path_represents_same_neutral_reference_without_authority(self) -> None:
        roles = CharacterRoleLedgerV1(referenced_ids=(HANA, TED))
        segment = ProviderDiagnosticStorySegmentDraftV1(
            schema_version=ProviderDiagnosticStorySegmentDraftV1.SCHEMA_VERSION,
            segment_key="neutral_kitchen_atmosphere",
            kind=StoryRealizationKind.NARRATION,
            output_start=12,
            output_end=59,
            roles=roles,
            grounding_status=DiagnosticGroundingStatus.GROUNDED,
            protected_user_source_claim_keys=(),
        ).compile(writer_story_text=FROZEN_FLASH_CANDIDATE)
        adjudication = ProviderDiagnosticProtectedSemanticAdjudicationDraftV1(
            schema_version=(
                ProviderDiagnosticProtectedSemanticAdjudicationDraftV1.SCHEMA_VERSION
            ),
            adjudication_key="neutral_kitchen_atmosphere_adjudication",
            segment_key=segment.segment_key,
            output_start=segment.output_start,
            output_end=segment.output_end,
            protected_user_id=TED,
            relation=ProtectedSemanticRelationKind.NEUTRAL_PRESENTATION_REFERENCE,
            grounding_status=DiagnosticGroundingStatus.GROUNDED,
            violation_classification=DiagnosticViolationClassification.NONE,
            npc_assertion_owner_ids=(),
            protected_user_source_claim_keys=(),
        ).compile(
            writer_story_text=FROZEN_FLASH_CANDIDATE,
            story_segment=segment,
        )
        _validate_diagnostic_adjudication_against_segment(
            segment=segment,
            adjudication=adjudication,
        )

        with self.assertRaisesRegex(
            ContractValidationError,
            "closed narration roles",
        ):
            _validate_diagnostic_adjudication_against_segment(
                segment=replace(
                    segment,
                    roles=CharacterRoleLedgerV1(observing_ids=(TED,)),
                ),
                adjudication=adjudication,
            )

    def test_python_retains_neutral_reference_only_as_presentation(self) -> None:
        neutral = "The quiet of the kitchen settled around them. "
        story = neutral + STORY
        presentation = PresentationRealizationSegmentV1(
            schema_version=PresentationRealizationSegmentV1.SCHEMA_VERSION,
            segment_key="neutral_shared_atmosphere",
            presentation_class=PresentationRealizationClass.NONPERSISTENT_ATMOSPHERE,
            kind=StoryRealizationKind.NARRATION,
            output_start=0,
            output_end=len(neutral),
            exact_text=neutral,
            exact_text_sha256=text_sha256(neutral),
            roles=CharacterRoleLedgerV1(referenced_ids=(HANA, TED)),
        )
        material = StoryRealizationSegmentV1(
            schema_version=StoryRealizationSegmentV1.SCHEMA_VERSION,
            segment_key="segment_entire_story",
            kind=StoryRealizationKind.ACTION,
            output_start=len(neutral),
            output_end=len(story),
            exact_text=STORY,
            roles=CharacterRoleLedgerV1(
                action_owner_ids=(HANA,),
                addressed_ids=(TED,),
            ),
        )
        base_package = _canonical_v4().compile().finalization_package
        self.assertIsNotNone(base_package)
        material_adjudication = replace(
            base_package.protected_semantic_adjudications[0],
            output_start=len(neutral),
            output_end=len(story),
        )
        neutral_adjudication = ProtectedSemanticAdjudicationV1(
            schema_version=ProtectedSemanticAdjudicationV1.SCHEMA_VERSION,
            adjudication_key="neutral_shared_atmosphere_adjudication",
            segment_key=presentation.segment_key,
            output_start=0,
            output_end=len(neutral),
            exact_text_sha256=text_sha256(neutral),
            protected_user_id=TED,
            relation=ProtectedSemanticRelationKind.NEUTRAL_PRESENTATION_REFERENCE,
            npc_assertion_owner_ids=(),
            protected_user_source_claim_keys=(),
        )
        package = replace(
            base_package,
            protected_semantic_adjudications=(material_adjudication,),
        )
        registry = RequestEvidenceBindingRegistry(
            world_id="world:neutral_reference",
            branch_id="branch:main",
            turn_id="turn:neutral_reference",
        )
        registry.validate_validator_realization_boundary(
            story_text=story,
            story_segments=(material,),
            presentation_segments=(presentation,),
            package=package,
            presentation_adjudications=(neutral_adjudication,),
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

if __name__ == "__main__":
    unittest.main()
