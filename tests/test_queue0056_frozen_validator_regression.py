from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
import unittest

from cera.continuous.contracts import (
    CharacterRoleLedgerV1,
    IngressSourceUnitKind,
    IngressSourceUnitV1,
    PresentationRealizationClass,
    RealizationAuthorityDisposition,
    StoryRealizationKind,
    StoryRealizationSegmentV1,
    WriterRealizationBoundaryV1,
)
from cera.continuous.evidence import RequestEvidenceBindingRegistry
from cera.continuous.provider import (
    ProviderProtectedSemanticAdjudicationDraftV2,
    ProviderProtectedSemanticRelationKindV2,
    ProviderRealizationSegmentDraftV1,
    _compile_provider_protected_adjudications_v2,
)
from cera.errors import ContractValidationError
from cera.serialization import bytes_sha256


TED = "character:ted"
HANA = "character:hana_hanezawa"
MIA = "character:mia_hanezawa"
SOURCE_UNIT_KEY = "source_state_0001"
SOURCE_STATE = "Ted remains silent and still."

ROOT = Path(__file__).resolve().parents[1]
V5_DEBUG = (
    ROOT
    / ".chatgpt"
    / "pf"
    / "q55_s6_v5"
    / "runs"
    / "q55-s6-v5-run-001"
    / "live_world"
    / "q55-s6-v5-world-001"
    / "qualification-main"
    / "DEBUG"
    / "scene-001"
    / "turn-001"
)
V5_CANDIDATES = (
    (
        V5_DEBUG / "deepseek_output.json",
        "030c50f5e32fbde44d4b2487a32234576c7d102f0d4ed454175c5336b287230d",
    ),
    (
        V5_DEBUG / "WRITER_ATTEMPTS" / "attempt-002" / "deepseek_output.json",
        "228a0acc4235f408044adc41dc51ed89a0e6348d0b1bb4824e7b02d3b69beb03",
    ),
    (
        V5_DEBUG / "WRITER_ATTEMPTS" / "attempt-003" / "deepseek_output.json",
        "eb511e74df68914486388d8eda197ca98fa9e4e859671d85f99c529f8a4116b7",
    ),
)


def _story(index: int) -> str:
    path, expected_sha256 = V5_CANDIDATES[index]
    encoded = path.read_bytes()
    if bytes_sha256(encoded) != expected_sha256:
        raise AssertionError(f"frozen V5 candidate {index + 1} changed")
    return json.loads(encoded.decode("utf-8"))["story_text"]


class Queue0056FrozenValidatorRegressionTests(unittest.TestCase):
    def test_candidate_one_reciprocal_gaze_remains_a_hard_ted_assertion(self) -> None:
        story = _story(0)
        text = "Mia caught Ted's eye"
        self.assertIn(text, story)
        with self.assertRaisesRegex(ContractValidationError, "exact supplied claim"):
            StoryRealizationSegmentV1(
                schema_version=StoryRealizationSegmentV1.SCHEMA_VERSION,
                segment_key="v5_reciprocal_gaze",
                kind=StoryRealizationKind.ACTION,
                output_start=0,
                output_end=len(text),
                exact_text=text,
                roles=CharacterRoleLedgerV1(action_owner_ids=(MIA, TED)),
                protected_user_source_claim_keys=(),
            )

    def test_candidate_two_relational_color_is_soft_and_noncanonical(self) -> None:
        story = _story(1)
        marker = "the quiet understanding of two people who had had this conversation in other forms before"
        start = story.index(marker)
        semantic, presentation = ProviderRealizationSegmentDraftV1(
            schema_version=ProviderRealizationSegmentDraftV1.SCHEMA_VERSION,
            segment_key="v5_soft_relational_color",
            authority_disposition=RealizationAuthorityDisposition.PRESENTATION_ONLY,
            presentation_class=(
                PresentationRealizationClass.LOW_STAKES_CONVERSATIONAL_COLOR
            ),
            kind=StoryRealizationKind.NARRATION,
            output_start=start,
            output_end=start + len(marker),
            roles=CharacterRoleLedgerV1(referenced_ids=(HANA, MIA)),
            protected_user_source_claim_keys=(),
        ).compile(writer_story_text=story)

        self.assertEqual(semantic.exact_text, marker)
        self.assertIsNotNone(presentation)
        self.assertEqual(
            presentation.presentation_class,
            PresentationRealizationClass.LOW_STAKES_CONVERSATIONAL_COLOR,
        )

    def test_candidate_three_silence_is_source_grounded_public_state(self) -> None:
        story = _story(2)
        start = story.index("Ted", story.index("corners of her eyes"))
        end = story.index(" with a calm patience", start)
        exact_text = story[start:end]
        self.assertTrue(exact_text.startswith("Ted"))
        self.assertTrue(exact_text.endswith("s silence"))

        semantic, presentation = ProviderRealizationSegmentDraftV1(
            schema_version=ProviderRealizationSegmentDraftV1.SCHEMA_VERSION,
            segment_key="v5_ted_silence_public_state",
            authority_disposition=RealizationAuthorityDisposition.PRESENTATION_ONLY,
            presentation_class=(
                PresentationRealizationClass.NONPERSISTENT_SPATIAL_PHRASING
            ),
            kind=StoryRealizationKind.NARRATION,
            output_start=0,
            output_end=len(exact_text),
            roles=CharacterRoleLedgerV1(referenced_ids=(TED,)),
            protected_user_source_claim_keys=(),
        ).compile(writer_story_text=exact_text)
        self.assertIsNotNone(presentation)
        adjudication = ProviderProtectedSemanticAdjudicationDraftV2(
            schema_version=ProviderProtectedSemanticAdjudicationDraftV2.SCHEMA_VERSION,
            adjudication_key="v5_ted_silence_public_state_adjudication",
            segment_key=semantic.segment_key,
            output_start=0,
            output_end=len(exact_text),
            protected_user_id=TED,
            relation=ProviderProtectedSemanticRelationKindV2.SOURCE_GROUNDED_PUBLIC_STATE,
            npc_assertion_owner_ids=(),
            protected_user_source_claim_keys=(),
            protected_user_source_unit_keys=(SOURCE_UNIT_KEY,),
        )
        compiled, receipts = _compile_provider_protected_adjudications_v2(
            writer_story_text=exact_text,
            story_segments=(semantic,),
            adjudications=(adjudication,),
            presentation_segments=(presentation,),
        )

        source_unit = IngressSourceUnitV1(
            schema_version=IngressSourceUnitV1.SCHEMA_VERSION,
            source_unit_key=SOURCE_UNIT_KEY,
            kind=IngressSourceUnitKind.STATE,
            source_start=0,
            source_end=len(SOURCE_STATE),
            exact_text=SOURCE_STATE,
            actor_id=TED,
            speaker_id=None,
            classification_basis="explicit_ingress_actor",
        )
        registry = RequestEvidenceBindingRegistry(
            world_id="world:q0056_frozen",
            branch_id="branch:main",
            turn_id="turn:001",
        )
        registry.allocate_current_source(
            source_identity="current_user_source:turn:001",
            source_text=SOURCE_STATE,
            protected_user_allowance_scope="exact current source only",
            source_units=(source_unit,),
        )
        registry.validate_validator_semantics(
            story_text=exact_text,
            story_segments=(semantic,),
            allowed_character_ids=(HANA, MIA),
        )
        registry._validate_protected_semantics(
            SimpleNamespace(protected_semantic_adjudications=compiled),
            presentation_segments=(presentation,),
            source_grounded_public_state_receipts=receipts,
        )
        self.assertEqual(receipts[0].source_unit_key, SOURCE_UNIT_KEY)

    def test_new_ted_action_emotion_choice_response_and_reciprocal_gaze_fail(self) -> None:
        cases = (
            ("Ted stepped inside.", StoryRealizationKind.ACTION, CharacterRoleLedgerV1(action_owner_ids=(TED,))),
            ("Mia caught Ted's eye.", StoryRealizationKind.ACTION, CharacterRoleLedgerV1(action_owner_ids=(MIA, TED))),
            ("Ted felt relieved.", StoryRealizationKind.PRIVATE_STATE, CharacterRoleLedgerV1(state_owner_ids=(TED,))),
            ("Ted chose to stay.", StoryRealizationKind.CONSENT_OR_DECISION, CharacterRoleLedgerV1(state_owner_ids=(TED,))),
            ('Ted said, "All right."', StoryRealizationKind.DIALOGUE, CharacterRoleLedgerV1(speaker_ids=(TED,))),
            ("Ted nodded back.", StoryRealizationKind.ACTION, CharacterRoleLedgerV1(action_owner_ids=(TED,))),
        )
        for index, (text, kind, roles) in enumerate(cases, start=1):
            with self.subTest(text=text), self.assertRaisesRegex(
                ContractValidationError, "exact supplied claim"
            ):
                StoryRealizationSegmentV1(
                    schema_version=StoryRealizationSegmentV1.SCHEMA_VERSION,
                    segment_key=f"hard_ted_case_{index}",
                    kind=kind,
                    output_start=0,
                    output_end=len(text),
                    exact_text=text,
                    roles=roles,
                    protected_user_source_claim_keys=(),
                )

    def test_soft_and_source_grounded_presentation_has_no_durable_authority(self) -> None:
        boundary = WriterRealizationBoundaryV1.default()
        self.assertFalse(boundary.presentation_enters_final_sequence)
        self.assertFalse(boundary.presentation_enters_events_or_material_changes)
        self.assertFalse(boundary.presentation_enters_memory_relationship_or_summary)
        self.assertFalse(boundary.presentation_enters_accepted_context_or_persistence)
        self.assertFalse(boundary.presentation_enters_canon)


if __name__ == "__main__":
    unittest.main()
