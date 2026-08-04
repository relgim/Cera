from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace
import unittest

from cera.continuous.contracts import (
    CharacterRoleLedgerV1,
    FinalFieldName,
    FinalFieldScopeV1,
    FinalInformationVisibility,
    IngressSourceUnitKind,
    IngressSourceUnitV1,
    PresentationRealizationClass,
    RealizationAuthorityDisposition,
    StoryRealizationKind,
    StoryRealizationSegmentV1,
)
from cera.continuous.evidence import RequestEvidenceBindingRegistry
from cera.continuous.prompting import VALIDATOR_STABLE_INSTRUCTIONS
from cera.continuous.provider import (
    ProviderProtectedSemanticAdjudicationDraftV2,
    ProviderProtectedSemanticRelationKindV2,
    ProviderRealizationSegmentDraftV1,
    _compile_provider_protected_adjudications_v2,
)
from cera.errors import ContractValidationError, StateConflictError
from cera.serialization import bytes_sha256


TED = "character:ted"
HANA = "character:hana_hanezawa"
SOURCE = "Ted is waiting at the threshold."
STORY = "Ted stood at the threshold."
SOURCE_UNIT_KEY = "source_instruction_0001"
ROOT = Path(__file__).resolve().parents[1]
FROZEN_WRITER_OUTPUT = (
    ROOT
    / ".chatgpt"
    / "pf"
    / "q0053_s4_final3"
    / "live_worlds"
    / "protected_user_temptation"
    / "runtime-v3-stage4-02"
    / "qualification-main"
    / "DEBUG"
    / "scene-001"
    / "turn-001"
    / "deepseek_output.json"
)


def _source_unit() -> IngressSourceUnitV1:
    return IngressSourceUnitV1(
        schema_version=IngressSourceUnitV1.SCHEMA_VERSION,
        source_unit_key=SOURCE_UNIT_KEY,
        kind=IngressSourceUnitKind.INSTRUCTION,
        source_start=0,
        source_end=len(SOURCE),
        exact_text=SOURCE,
        actor_id=None,
        speaker_id=None,
        classification_basis="explicit_ingress_instruction",
    )


def _source_grounded_provider_pair():
    realization = ProviderRealizationSegmentDraftV1(
        schema_version=ProviderRealizationSegmentDraftV1.SCHEMA_VERSION,
        segment_key="ted_threshold_public_state",
        authority_disposition=RealizationAuthorityDisposition.PRESENTATION_ONLY,
        presentation_class=(
            PresentationRealizationClass.NONPERSISTENT_SPATIAL_PHRASING
        ),
        kind=StoryRealizationKind.NARRATION,
        output_start=0,
        output_end=len(STORY),
        roles=CharacterRoleLedgerV1(referenced_ids=(TED,)),
        protected_user_source_claim_keys=(),
    )
    semantic, presentation = realization.compile(writer_story_text=STORY)
    assert presentation is not None
    adjudication = ProviderProtectedSemanticAdjudicationDraftV2(
        schema_version=(
            ProviderProtectedSemanticAdjudicationDraftV2.SCHEMA_VERSION
        ),
        adjudication_key="ted_threshold_public_state_adjudication",
        segment_key=semantic.segment_key,
        output_start=0,
        output_end=len(STORY),
        protected_user_id=TED,
        relation=(
            ProviderProtectedSemanticRelationKindV2.SOURCE_GROUNDED_PUBLIC_STATE
        ),
        npc_assertion_owner_ids=(),
        protected_user_source_claim_keys=(),
        protected_user_source_unit_keys=(SOURCE_UNIT_KEY,),
    )
    compiled, receipts = _compile_provider_protected_adjudications_v2(
        writer_story_text=STORY,
        story_segments=(semantic,),
        adjudications=(adjudication,),
        presentation_segments=(presentation,),
    )
    return semantic, presentation, compiled[0], receipts[0]


class ValidatorSimplificationSourceGroundedStateTests(unittest.TestCase):
    def test_optional_incidental_prop_offer_is_soft_and_nonpersistent(self) -> None:
        encoded = FROZEN_WRITER_OUTPUT.read_bytes()
        story = json.loads(encoded.decode("utf-8"))["story_text"]
        start = story.index("There", story.index("She offered"))
        end = story.index(" she said", start)
        tea_offer = story[start:end]
        _, presentation = ProviderRealizationSegmentDraftV1(
            schema_version=ProviderRealizationSegmentDraftV1.SCHEMA_VERSION,
            segment_key="hana_optional_tea_offer",
            authority_disposition=RealizationAuthorityDisposition.PRESENTATION_ONLY,
            presentation_class=PresentationRealizationClass.INCIDENTAL_PROP,
            kind=StoryRealizationKind.DIALOGUE,
            output_start=start,
            output_end=end,
            roles=CharacterRoleLedgerV1(
                speaker_ids=(HANA,), addressed_ids=(TED,)
            ),
            protected_user_source_claim_keys=(),
        ).compile(writer_story_text=story)
        self.assertEqual(
            presentation.presentation_class,
            PresentationRealizationClass.INCIDENTAL_PROP,
        )
        for required in (
            "offering an incidental household item as optional low-stakes invitation color",
            "does not prepare, transfer, acquire, consume, inventory, causally require, or retain",
            "Do not classify such an optional mention as new_continuity_object",
            "Actual preparation, transfer, acquisition, consumption, inventory change",
        ):
            self.assertIn(required, VALIDATOR_STABLE_INSTRUCTIONS)

    def test_exact_frozen_candidate_exercises_both_corrected_classes(self) -> None:
        encoded = FROZEN_WRITER_OUTPUT.read_bytes()
        self.assertEqual(
            bytes_sha256(encoded),
            "ad0299c1dcf9e51bbbc470547a1deddb3018850674e98152f66b425d8f82b793",
        )
        story = json.loads(encoded.decode("utf-8"))["story_text"]
        threshold = "Ted stood there, framed by the threshold,"
        threshold_start = story.index(threshold)
        threshold_draft = ProviderRealizationSegmentDraftV1(
            schema_version=ProviderRealizationSegmentDraftV1.SCHEMA_VERSION,
            segment_key="frozen_ted_threshold_public_state",
            authority_disposition=RealizationAuthorityDisposition.PRESENTATION_ONLY,
            presentation_class=(
                PresentationRealizationClass.NONPERSISTENT_SPATIAL_PHRASING
            ),
            kind=StoryRealizationKind.NARRATION,
            output_start=threshold_start,
            output_end=threshold_start + len(threshold),
            roles=CharacterRoleLedgerV1(referenced_ids=(TED,)),
            protected_user_source_claim_keys=(),
        )
        semantic, presentation = threshold_draft.compile(
            writer_story_text=story
        )
        adjudications, receipts = _compile_provider_protected_adjudications_v2(
            writer_story_text=story,
            story_segments=(semantic,),
            adjudications=(
                ProviderProtectedSemanticAdjudicationDraftV2(
                    schema_version=(
                        ProviderProtectedSemanticAdjudicationDraftV2.SCHEMA_VERSION
                    ),
                    adjudication_key="frozen_ted_threshold_adjudication",
                    segment_key=semantic.segment_key,
                    output_start=semantic.output_start,
                    output_end=semantic.output_end,
                    protected_user_id=TED,
                    relation=(
                        ProviderProtectedSemanticRelationKindV2.SOURCE_GROUNDED_PUBLIC_STATE
                    ),
                    npc_assertion_owner_ids=(),
                    protected_user_source_claim_keys=(),
                    protected_user_source_unit_keys=(SOURCE_UNIT_KEY,),
                ),
            ),
            presentation_segments=(presentation,),
        )
        self.assertEqual(len(adjudications), 1)
        self.assertEqual(receipts[0].source_unit_key, SOURCE_UNIT_KEY)

        ambience = "The afternoon light fell across the floor between them, untroubled by the silence."
        ambience_start = story.index(ambience)
        _, actorless = ProviderRealizationSegmentDraftV1(
            schema_version=ProviderRealizationSegmentDraftV1.SCHEMA_VERSION,
            segment_key="frozen_actorless_afternoon_light",
            authority_disposition=RealizationAuthorityDisposition.PRESENTATION_ONLY,
            presentation_class=(
                PresentationRealizationClass.NONPERSISTENT_ATMOSPHERE
            ),
            kind=StoryRealizationKind.NARRATION,
            output_start=ambience_start,
            output_end=ambience_start + len(ambience),
            roles=CharacterRoleLedgerV1(),
            protected_user_source_claim_keys=(),
        ).compile(writer_story_text=story)
        self.assertTrue(actorless.roles.is_empty)

    def test_actorless_ambience_is_presentation_only_with_no_fabricated_role(self) -> None:
        text = "The kitchen was quiet."
        empty = CharacterRoleLedgerV1()
        self.assertTrue(empty.is_empty)
        draft = ProviderRealizationSegmentDraftV1(
            schema_version=ProviderRealizationSegmentDraftV1.SCHEMA_VERSION,
            segment_key="actorless_kitchen_ambience",
            authority_disposition=RealizationAuthorityDisposition.PRESENTATION_ONLY,
            presentation_class=(
                PresentationRealizationClass.NONPERSISTENT_ATMOSPHERE
            ),
            kind=StoryRealizationKind.NARRATION,
            output_start=0,
            output_end=len(text),
            roles=empty,
            protected_user_source_claim_keys=(),
        )
        semantic, presentation = draft.compile(writer_story_text=text)
        self.assertTrue(semantic.roles.is_empty)
        self.assertIsNotNone(presentation)
        self.assertTrue(presentation.roles.is_empty)

    def test_empty_roles_cannot_become_material_or_final_fields(self) -> None:
        text = "The question hung in the air."
        with self.assertRaisesRegex(
            ContractValidationError, "empty role ledger is restricted"
        ):
            ProviderRealizationSegmentDraftV1(
                schema_version=ProviderRealizationSegmentDraftV1.SCHEMA_VERSION,
                segment_key="actorless_material",
                authority_disposition=(
                    RealizationAuthorityDisposition.STORY_MATERIAL_ASSERTION
                ),
                presentation_class=None,
                kind=StoryRealizationKind.NARRATION,
                output_start=0,
                output_end=len(text),
                roles=CharacterRoleLedgerV1(),
                protected_user_source_claim_keys=(),
            )
        with self.assertRaisesRegex(
            ContractValidationError, "final field scope requires character-owned"
        ):
            FinalFieldScopeV1(
                field_name=FinalFieldName.REALIZED_EVENT,
                visibility=FinalInformationVisibility.PUBLIC,
                knowledge_owner_id=None,
                story_segment_keys=("actorless_material",),
                roles=CharacterRoleLedgerV1(),
            )

    def test_threshold_restatement_cites_and_verifies_current_source_unit(self) -> None:
        semantic, presentation, adjudication, receipt = (
            _source_grounded_provider_pair()
        )
        registry = RequestEvidenceBindingRegistry(
            world_id="world:source_grounded",
            branch_id="branch:main",
            turn_id="turn:001",
        )
        registry.allocate_current_source(
            source_identity="current_user_source:turn:001",
            source_text=SOURCE,
            protected_user_allowance_scope="exact current source only",
            source_units=(_source_unit(),),
        )
        registry.validate_validator_semantics(
            story_text=STORY,
            story_segments=(semantic,),
            allowed_character_ids=(HANA,),
        )
        registry._validate_protected_semantics(
            SimpleNamespace(
                protected_semantic_adjudications=(adjudication,)
            ),
            presentation_segments=(presentation,),
            source_grounded_public_state_receipts=(receipt,),
        )
        with self.assertRaisesRegex(
            StateConflictError, "exact current-source presentation custody"
        ):
            registry._validate_protected_semantics(
                SimpleNamespace(
                    protected_semantic_adjudications=(adjudication,)
                ),
                presentation_segments=(presentation,),
                source_grounded_public_state_receipts=(
                    replace(receipt, source_unit_key="source_instruction_missing"),
                ),
            )

    def test_source_grounded_route_cannot_claim_material_action_or_private_state(self) -> None:
        for text, kind, roles in (
            (
                "Ted stepped inside.",
                StoryRealizationKind.ACTION,
                CharacterRoleLedgerV1(action_owner_ids=(TED,)),
            ),
            (
                "Ted hesitated.",
                StoryRealizationKind.PRIVATE_STATE,
                CharacterRoleLedgerV1(state_owner_ids=(TED,)),
            ),
            (
                "Ted smiled back.",
                StoryRealizationKind.ACTION,
                CharacterRoleLedgerV1(action_owner_ids=(TED,)),
            ),
        ):
            with self.subTest(text=text):
                with self.assertRaisesRegex(
                    ContractValidationError, "exact supplied claim"
                ):
                    StoryRealizationSegmentV1(
                        schema_version=StoryRealizationSegmentV1.SCHEMA_VERSION,
                        segment_key="prohibited_ted_addition",
                        kind=kind,
                        output_start=0,
                        output_end=len(text),
                        exact_text=text,
                        roles=roles,
                        protected_user_source_claim_keys=(),
                    )

    def test_exact_ted_dialogue_still_requires_an_exact_claim(self) -> None:
        text = 'Ted said, "I will come in."'
        with self.assertRaisesRegex(
            ContractValidationError, "exact supplied claim"
        ):
            StoryRealizationSegmentV1(
                schema_version=StoryRealizationSegmentV1.SCHEMA_VERSION,
                segment_key="unsupplied_ted_dialogue",
                kind=StoryRealizationKind.DIALOGUE,
                output_start=0,
                output_end=len(text),
                exact_text=text,
                roles=CharacterRoleLedgerV1(speaker_ids=(TED,)),
                protected_user_source_claim_keys=(),
            )

    def test_prompt_assigns_semantic_equivalence_to_codex_and_names_adversaries(self) -> None:
        for required in (
            "Codex independently judges",
            "Ted stood at the threshold",
            "Ted stepped inside",
            "Ted hesitated",
            "Ted smiled back",
            "Exact Ted dialogue remains protected_assertion",
            "never create an event, memory, persistence, or accepted protected-user authority",
        ):
            self.assertIn(required, VALIDATOR_STABLE_INSTRUCTIONS)


if __name__ == "__main__":
    unittest.main()
