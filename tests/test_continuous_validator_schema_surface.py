from __future__ import annotations

from copy import deepcopy
import unittest

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from cera.continuous.contracts import (
    CharacterRoleLedgerV1,
    FinalFieldName,
    FinalFieldScopeV1,
    FinalInformationVisibility,
    FinalSequenceItemV1,
    FinalSequenceV1,
    ProtectedSemanticAdjudicationV1,
    ProtectedSemanticRelationKind,
    StoryRealizationKind,
    StoryRealizationSegmentV1,
    ValidatorSemanticStatus,
    ValidatorTaskMode,
)
from cera.continuous.provider import (
    CONTINUOUS_VALIDATOR_ADAPTER_VERSION,
    ContinuousSceneWriterDraftV1,
    ContinuousSemanticValidatorDraftV1,
    ContinuousSemanticValidatorDraftV2,
    ProviderEventRecordDraftV1,
    continuous_scene_writer_draft_json_schema,
    continuous_semantic_validator_draft_json_schema,
    continuous_validator_draft_json_schema,
    continuous_validator_route,
)
from cera.creator_review.models import (
    CreatorReviewAssessment,
    CreatorReviewSeverity,
    PublicationEligibility,
    ReviewIssueOwner,
)
from cera.errors import ContractValidationError
from cera.providers import (
    ProviderSchemaDialect,
    project_provider_output_schema,
    validate_provider_output_schema,
)
from cera.schema import from_mapping
from cera.serialization import canonical_sha256, text_sha256, to_primitive


EXPECTED_FINAL_FIELD_NAMES = (
    "realized_event",
    "valid_deepseek_additions",
    "knowledge_changes",
    "material_changes",
    "resulting_state",
)


def _field_name_schemas(value: object, path: str = "$") -> list[tuple[str, dict]]:
    found: list[tuple[str, dict]] = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if key == "field_name":
                found.append((child_path, child))
            found.extend(_field_name_schemas(child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(_field_name_schemas(child, f"{path}[{index}]"))
    return found


def _active_draft() -> ContinuousSemanticValidatorDraftV2:
    story = "Hana set down the teacup."
    roles = CharacterRoleLedgerV1(
        action_owner_ids=("character:hana_hanezawa",),
    )
    scopes = tuple(
        FinalFieldScopeV1(
            field_name=name,
            visibility=FinalInformationVisibility.PUBLIC,
            knowledge_owner_id=None,
            story_segment_keys=("segment_entire_story",),
            roles=roles,
        )
        for name in EXPECTED_FINAL_FIELD_NAMES
    )
    sequence = FinalSequenceV1(
        schema_version=FinalSequenceV1.SCHEMA_VERSION,
        sequence_id="sequence:validator_schema_surface",
        accepted_turn_id="turn:validator_schema_surface",
        items=(
            FinalSequenceItemV1(
                item_key="validator_schema_surface",
                planner_beat_keys=("planner_beat",),
                story_segment_keys=("segment_entire_story",),
                realized_event="Hana set down the teacup.",
                valid_deepseek_additions=("The motion remained measured.",),
                omitted_or_contradicted_details=(),
                private_state_owner_ids=(),
                knowledge_changes=("The visible action is public.",),
                material_changes=("The teacup is now on the table.",),
                resulting_state="Hana awaits the next supplied choice.",
                roles=roles,
                field_scopes=scopes,
            ),
        ),
        final_stop_state="Hana awaits the next supplied choice.",
    )
    segment = StoryRealizationSegmentV1(
        schema_version=StoryRealizationSegmentV1.SCHEMA_VERSION,
        segment_key="segment_entire_story",
        kind=StoryRealizationKind.ACTION,
        output_start=0,
        output_end=len(story),
        exact_text=story,
        roles=roles,
    )
    return ContinuousSemanticValidatorDraftV2(
        schema_version=ContinuousSemanticValidatorDraftV2.SCHEMA_VERSION,
        package_id="package:validator_schema_surface",
        world_id="world:validator_schema_surface",
        branch_id="branch:main",
        task_mode=ValidatorTaskMode.FINALIZE_TURN,
        semantic_status=ValidatorSemanticStatus.ACCEPTED,
        reason_codes=(),
        story_segments=(segment,),
        complete_final_sequence=sequence,
        creator_review=CreatorReviewAssessment(
            schema_version=CreatorReviewAssessment.SCHEMA_VERSION,
            severity=CreatorReviewSeverity.GOOD,
            publication_eligibility=PublicationEligibility.ACCEPT_ALLOWED,
            issue_owner=ReviewIssueOwner.NONE,
            reason_codes=(),
            creator_reason="The fixture preserves the closed Validator field contract.",
            verifier_status="accepted",
        ),
        protected_semantic_adjudications=(
            ProtectedSemanticAdjudicationV1(
                schema_version=ProtectedSemanticAdjudicationV1.SCHEMA_VERSION,
                adjudication_key="adjudication_entire_story",
                segment_key="segment_entire_story",
                output_start=0,
                output_end=len(story),
                exact_text_sha256=text_sha256(story),
                protected_user_id="character:ted",
                relation=ProtectedSemanticRelationKind.NONE,
                npc_assertion_owner_ids=(),
                protected_user_source_claim_keys=(),
            ),
        ),
        event_record=ProviderEventRecordDraftV1(
            event_id="event:validator_schema_surface",
            accepted_turn_id="turn:validator_schema_surface",
            scene_id="scene:validator_schema_surface",
            summary="Hana set down the teacup.",
            final_sequence_item_keys=("validator_schema_surface",),
            protected_user_source_claim_keys=(),
        ),
        optional_scene_summary=None,
    )


class ContinuousValidatorSchemaSurfaceTests(unittest.TestCase):
    def test_provider_neutral_schema_has_the_exact_enum_at_every_path(self) -> None:
        schemas = _field_name_schemas(continuous_semantic_validator_draft_json_schema())
        self.assertEqual(len(schemas), 1)
        for path, schema in schemas:
            with self.subTest(path=path):
                self.assertEqual(schema, {"type": "string", "enum": list(EXPECTED_FINAL_FIELD_NAMES)})

    def test_openai_projection_retains_the_exact_enum_at_every_path(self) -> None:
        projection = project_provider_output_schema(
            continuous_semantic_validator_draft_json_schema(),
            ProviderSchemaDialect.OPENAI_STRUCTURED_OUTPUT_V1,
        )
        schemas = _field_name_schemas(projection.provider_schema)
        self.assertEqual(len(schemas), 1)
        self.assertTrue(projection.strict_provider_enforced)
        for path, schema in schemas:
            with self.subTest(path=path):
                self.assertEqual(schema, {"type": "string", "enum": list(EXPECTED_FINAL_FIELD_NAMES)})

    def test_schema_enum_and_python_vocabulary_are_identical(self) -> None:
        self.assertEqual(tuple(value.value for value in FinalFieldName), EXPECTED_FINAL_FIELD_NAMES)
        schema = continuous_semantic_validator_draft_json_schema()
        self.assertEqual(tuple(_field_name_schemas(schema)[0][1]["enum"]), EXPECTED_FINAL_FIELD_NAMES)

    def test_all_five_values_decode_and_compile(self) -> None:
        payload = to_primitive(_active_draft())
        decoded = from_mapping(ContinuousSemanticValidatorDraftV2, payload)
        scopes = decoded.complete_final_sequence.items[0].field_scopes
        self.assertEqual(tuple(value.field_name.value for value in scopes), EXPECTED_FINAL_FIELD_NAMES)
        self.assertTrue(all(isinstance(value.field_name, FinalFieldName) for value in scopes))
        compiled = decoded.compile()
        self.assertEqual(
            tuple(value.field_name.value for value in compiled.finalization_package.complete_final_sequence.items[0].field_scopes),
            EXPECTED_FINAL_FIELD_NAMES,
        )

    def test_arbitrary_values_fail_local_schema_and_python_injection(self) -> None:
        schema = continuous_semantic_validator_draft_json_schema()
        for arbitrary in ("teacup_position", "tea_question", "future_state"):
            with self.subTest(arbitrary=arbitrary):
                payload = deepcopy(to_primitive(_active_draft()))
                payload["complete_final_sequence"]["items"][0]["field_scopes"][0]["field_name"] = arbitrary
                with self.assertRaises(ValidationError):
                    Draft202012Validator(schema).validate(payload)
                with self.assertRaises(ContractValidationError):
                    from_mapping(ContinuousSemanticValidatorDraftV2, payload)
                with self.assertRaises(ContractValidationError):
                    FinalFieldScopeV1(
                        field_name=arbitrary,
                        visibility=FinalInformationVisibility.PUBLIC,
                        knowledge_owner_id=None,
                        story_segment_keys=("segment_entire_story",),
                        roles=CharacterRoleLedgerV1(
                            action_owner_ids=("character:hana_hanezawa",),
                        ),
                    )

    def test_active_schema_passes_openai_preflight_and_identity_is_advanced(self) -> None:
        projection = project_provider_output_schema(
            continuous_semantic_validator_draft_json_schema(),
            ProviderSchemaDialect.OPENAI_STRUCTURED_OUTPUT_V1,
        )
        validate_provider_output_schema(
            projection.provider_schema,
            ProviderSchemaDialect.OPENAI_STRUCTURED_OUTPUT_V1,
        )
        self.assertEqual(
            ContinuousSemanticValidatorDraftV2.SCHEMA_VERSION,
            "cera.continuous_semantic_validator_draft.v2",
        )
        self.assertEqual(
            CONTINUOUS_VALIDATOR_ADAPTER_VERSION,
            "cera.continuous_validator_adapter.v10",
        )
        self.assertEqual(
            continuous_validator_route(model="gpt-5.6-sol", effort="medium").adapter_id,
            CONTINUOUS_VALIDATOR_ADAPTER_VERSION,
        )

    def test_historical_validator_readers_remain_available(self) -> None:
        active_payload = to_primitive(_active_draft())
        historical_payload = deepcopy(active_payload)
        historical_payload["schema_version"] = ContinuousSemanticValidatorDraftV1.SCHEMA_VERSION
        historical = from_mapping(ContinuousSemanticValidatorDraftV1, historical_payload)
        self.assertEqual(
            historical.schema_version,
            "cera.continuous_semantic_validator_draft.v1",
        )
        self.assertIsNotNone(historical.compile().finalization_package)
        self.assertIn("complete_final_sequence", continuous_validator_draft_json_schema()["properties"])

    def test_writer_schema_and_boundary_are_unchanged(self) -> None:
        schema = continuous_scene_writer_draft_json_schema()
        self.assertEqual(tuple(schema["properties"]), ("schema_version", "story_text"))
        self.assertEqual(
            schema["properties"]["schema_version"]["const"],
            ContinuousSceneWriterDraftV1.SCHEMA_VERSION,
        )
        self.assertEqual(
            canonical_sha256(schema),
            "dec9386e5a4b0fd3c9c9f48cd4826a14daf17009970c2be0d5bfa336ba8a717d",
        )


if __name__ == "__main__":
    unittest.main()
