from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from cera.continuous.contracts import (
    ACTIVE_VALIDATOR_WRITER_HARD_CLASSES,
    CharacterRoleLedgerV1,
    FinalFieldName,
    FinalFieldScopeV1,
    FinalInformationVisibility,
    FinalSequenceItemV1,
    FinalSequenceV1,
    ProtectedSemanticAdjudicationV1,
    ProtectedSemanticRelationKind,
    ProhibitedWriterDetailClass,
    StoryRealizationKind,
    StoryRealizationSegmentV1,
    ValidatorSemanticStatus,
    ValidatorTaskMode,
)
from cera.continuous.provider import (
    CONTINUOUS_VALIDATOR_ADAPTER_VERSION,
    CodexContinuousValidatorPort,
    ContinuousSceneWriterDraftV1,
    ContinuousSemanticValidatorDraftV1,
    ContinuousSemanticValidatorDraftV2,
    ContinuousSemanticValidatorDraftV3,
    ContinuousSemanticValidatorDraftV4,
    ContinuousSemanticValidatorDraftV5,
    ContinuousSemanticValidatorDraftV6,
    ContinuousSemanticValidatorDraftV7,
    ContinuousSemanticValidatorDraftV8,
    ContinuousSemanticValidatorDraftV9,
    ContinuousSemanticValidatorDraftV10,
    ContinuousSemanticValidatorDraftV12,
    ProviderEventRecordDraftV1,
    ProviderEventRecordDraftV2,
    ProviderFinalSequenceDraftV2,
    ProviderRejectedViolationDraftV1,
    _python_derived_event_record,
    _schema_for,
    continuous_scene_writer_draft_json_schema,
    continuous_semantic_validator_draft_json_schema,
    historical_continuous_semantic_validator_draft_v11_json_schema,
    continuous_validator_draft_json_schema,
    continuous_validator_route,
)
from cera.continuous.call_ledger import ContinuousProviderCallLedger
from cera.continuous.qualification_evidence import (
    QualificationRawProviderJsonCapture,
    QualificationRawProviderJsonCaptureRegistry,
)
from cera.creator_review.models import (
    CreatorReviewAssessment,
    CreatorReviewSeverity,
    PublicationEligibility,
    ReviewIssueOwner,
)
from cera.errors import ContractValidationError, StateConflictError
from cera.providers import (
    ProviderSchemaDialect,
    project_provider_output_schema,
    validate_provider_output_schema,
)
from cera.schema import from_mapping
from cera.serialization import bytes_sha256, canonical_sha256, text_sha256, to_primitive


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


def _schema_version_schemas(
    value: object, path: str = "$"
) -> list[tuple[str, dict]]:
    found: list[tuple[str, dict]] = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if key == "schema_version":
                found.append((child_path, child))
            found.extend(_schema_version_schemas(child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(_schema_version_schemas(child, f"{path}[{index}]"))
    return found


def _role_ledger_schemas(value: object, path: str = "$") -> list[tuple[str, dict]]:
    found: list[tuple[str, dict]] = []
    if isinstance(value, dict):
        properties = value.get("properties")
        if (
            isinstance(properties, dict)
            and properties.get("schema_version", {}).get("const")
            == CharacterRoleLedgerV1.SCHEMA_VERSION
        ):
            found.append((path, value))
        for key, child in value.items():
            found.extend(_role_ledger_schemas(child, f"{path}.{key}"))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(_role_ledger_schemas(child, f"{path}[{index}]"))
    return found


def _active_draft() -> ContinuousSemanticValidatorDraftV4:
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
    return ContinuousSemanticValidatorDraftV4(
        schema_version=ContinuousSemanticValidatorDraftV4.SCHEMA_VERSION,
        package_id="package:validator_schema_surface",
        world_id="world:validator_schema_surface",
        branch_id="branch:main",
        task_mode=ValidatorTaskMode.FINALIZE_TURN,
        semantic_status=ValidatorSemanticStatus.ACCEPTED,
        reason_codes=(),
        story_segments=(segment,),
        complete_final_sequence=ProviderFinalSequenceDraftV2.from_final_sequence(
            sequence
        ),
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


def _active_wire() -> ContinuousSemanticValidatorDraftV12:
    return ContinuousSemanticValidatorDraftV12.from_v4(_active_draft())


class ContinuousValidatorSchemaSurfaceTests(unittest.TestCase):
    def test_actorless_active_schema_supersedes_historical_blanket_rule(self) -> None:
        role_fields = (
            "action_owner_ids",
            "state_owner_ids",
            "speaker_ids",
            "affected_ids",
            "addressed_ids",
            "observing_ids",
            "referenced_ids",
        )
        active = _role_ledger_schemas(
            continuous_semantic_validator_draft_json_schema()
        )
        projected = _role_ledger_schemas(
            project_provider_output_schema(
                continuous_semantic_validator_draft_json_schema(),
                ProviderSchemaDialect.OPENAI_STRUCTURED_OUTPUT_V1,
            ).provider_schema
        )
        historical = _role_ledger_schemas(
            historical_continuous_semantic_validator_draft_v11_json_schema()
        )
        self.assertTrue(active)
        self.assertEqual(len(projected), len(active))
        self.assertEqual(len(historical), len(active))
        expected = [
            {
                "properties": {field_name: {"minItems": 1}},
                "required": [field_name],
            }
            for field_name in role_fields
        ]
        for schema_family in (active, projected):
            for path, role_schema in schema_family:
                with self.subTest(path=path):
                    self.assertNotIn("anyOf", role_schema)
        for path, role_schema in historical:
            with self.subTest(path=path):
                self.assertEqual(role_schema["anyOf"], expected)

    def test_empty_material_role_ledger_is_contextually_rejected_by_python(self) -> None:
        payload = deepcopy(to_primitive(_active_wire()))
        roles = payload["decision"]["realization_segments"][0]["roles"]
        for field_name in (
            "action_owner_ids",
            "state_owner_ids",
            "speaker_ids",
            "affected_ids",
            "addressed_ids",
            "observing_ids",
            "referenced_ids",
        ):
            roles[field_name] = []
        Draft202012Validator(
            continuous_semantic_validator_draft_json_schema()
        ).validate(payload)
        with self.assertRaises(ValidationError):
            Draft202012Validator(
                historical_continuous_semantic_validator_draft_v11_json_schema()
            ).validate(payload)
        with self.assertRaisesRegex(
            ContractValidationError, "empty role ledger is restricted"
        ):
            from_mapping(ContinuousSemanticValidatorDraftV12, payload)

    def test_active_validator_recall_vocabulary_is_precise_and_projected(self) -> None:
        expected = [value.value for value in ACTIVE_VALIDATOR_WRITER_HARD_CLASSES]
        for schema in (
            continuous_semantic_validator_draft_json_schema(),
            project_provider_output_schema(
                continuous_semantic_validator_draft_json_schema(),
                ProviderSchemaDialect.OPENAI_STRUCTURED_OUTPUT_V1,
            ).provider_schema,
        ):
            rejected = next(
                branch
                for branch in schema["properties"]["decision"]["anyOf"]
                if "writer_recall_violations" in branch.get("properties", {})
            )
            actual = rejected["properties"]["writer_recall_violations"]["items"][
                "properties"
            ]["prohibited_detail_classes"]["items"]["enum"]
            self.assertEqual(actual, expected)
            self.assertNotIn("planner_sequence_departure", actual)
            self.assertNotIn("severe_reader_quality_failure", actual)

        historical = _schema_for(ProviderRejectedViolationDraftV1)
        historical_values = historical["properties"]["prohibited_detail_classes"][
            "items"
        ]["enum"]
        self.assertIn(ProhibitedWriterDetailClass.PLANNER_SEQUENCE_DEPARTURE.value, historical_values)
        self.assertIn(ProhibitedWriterDetailClass.SEVERE_READER_QUALITY_FAILURE.value, historical_values)

    def test_provider_neutral_schema_has_the_exact_enum_at_every_path(self) -> None:
        schemas = _field_name_schemas(continuous_semantic_validator_draft_json_schema())
        self.assertEqual(len(schemas), 2)
        for path, schema in schemas:
            with self.subTest(path=path):
                self.assertEqual(schema, {"type": "string", "enum": list(EXPECTED_FINAL_FIELD_NAMES)})

    def test_openai_projection_retains_the_exact_enum_at_every_path(self) -> None:
        projection = project_provider_output_schema(
            continuous_semantic_validator_draft_json_schema(),
            ProviderSchemaDialect.OPENAI_STRUCTURED_OUTPUT_V1,
        )
        schemas = _field_name_schemas(projection.provider_schema)
        self.assertEqual(len(schemas), 2)
        self.assertTrue(projection.strict_provider_enforced)
        for path, schema in schemas:
            with self.subTest(path=path):
                self.assertEqual(schema, {"type": "string", "enum": list(EXPECTED_FINAL_FIELD_NAMES)})

    def test_schema_enum_and_python_vocabulary_are_identical(self) -> None:
        self.assertEqual(tuple(value.value for value in FinalFieldName), EXPECTED_FINAL_FIELD_NAMES)
        schema = continuous_semantic_validator_draft_json_schema()
        self.assertEqual(tuple(_field_name_schemas(schema)[0][1]["enum"]), EXPECTED_FINAL_FIELD_NAMES)

    def test_all_five_values_decode_and_compile(self) -> None:
        payload = to_primitive(_active_wire())
        decoded = from_mapping(ContinuousSemanticValidatorDraftV12, payload)
        scopes = decoded.decision.complete_final_sequence.items[0].field_scopes
        self.assertEqual(tuple(value.field_name.value for value in scopes), EXPECTED_FINAL_FIELD_NAMES)
        self.assertTrue(all(isinstance(value.field_name, FinalFieldName) for value in scopes))
        compiled = decoded.compile(writer_story_text="Hana set down the teacup.")
        self.assertEqual(
            tuple(value.field_name.value for value in compiled.finalization_package.complete_final_sequence.items[0].field_scopes),
            EXPECTED_FINAL_FIELD_NAMES,
        )

    def test_arbitrary_values_fail_local_schema_and_python_injection(self) -> None:
        schema = continuous_semantic_validator_draft_json_schema()
        for arbitrary in ("teacup_position", "tea_question", "future_state"):
            with self.subTest(arbitrary=arbitrary):
                payload = deepcopy(to_primitive(_active_wire()))
                payload["decision"]["complete_final_sequence"]["items"][0]["field_scopes"][0]["field_name"] = arbitrary
                with self.assertRaises(ValidationError):
                    Draft202012Validator(schema).validate(payload)
                with self.assertRaises(ContractValidationError):
                    from_mapping(ContinuousSemanticValidatorDraftV12, payload)
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
            ContinuousSemanticValidatorDraftV12.SCHEMA_VERSION,
            "cera.continuous_semantic_validator_draft.v12",
        )
        self.assertEqual(
            CONTINUOUS_VALIDATOR_ADAPTER_VERSION,
            "cera.continuous_validator_adapter.v27",
        )
        self.assertEqual(
            continuous_validator_route(model="gpt-5.6-sol", effort="medium").adapter_id,
            CONTINUOUS_VALIDATOR_ADAPTER_VERSION,
        )

    def test_historical_validator_readers_remain_available(self) -> None:
        active_payload = to_primitive(_active_draft())
        for historical_type in (
            ContinuousSemanticValidatorDraftV1,
            ContinuousSemanticValidatorDraftV2,
            ContinuousSemanticValidatorDraftV3,
        ):
            with self.subTest(schema_version=historical_type.SCHEMA_VERSION):
                historical_payload = deepcopy(active_payload)
                historical_payload["schema_version"] = historical_type.SCHEMA_VERSION
                final = historical_payload["complete_final_sequence"]
                final["schema_version"] = FinalSequenceV1.SCHEMA_VERSION
                if historical_type in {
                    ContinuousSemanticValidatorDraftV1,
                    ContinuousSemanticValidatorDraftV2,
                }:
                    final["final_stop_state"] = final["items"][-1]["resulting_state"]
                historical = from_mapping(historical_type, historical_payload)
                self.assertEqual(
                    historical.schema_version,
                    historical_type.SCHEMA_VERSION,
                )
                self.assertIsNotNone(historical.compile().finalization_package)
        self.assertIn("complete_final_sequence", continuous_validator_draft_json_schema()["properties"])

        historical_v5 = ContinuousSemanticValidatorDraftV5.from_v4(_active_draft())
        decoded_v5 = from_mapping(
            ContinuousSemanticValidatorDraftV5,
            to_primitive(historical_v5),
        )
        self.assertIsNotNone(decoded_v5.compile().finalization_package)
        historical_v6 = ContinuousSemanticValidatorDraftV6.from_v4(_active_draft())
        decoded_v6 = from_mapping(
            ContinuousSemanticValidatorDraftV6,
            to_primitive(historical_v6),
        )
        self.assertIsNotNone(decoded_v6.compile().finalization_package)
        historical_v7 = ContinuousSemanticValidatorDraftV7.from_v4(_active_draft())
        decoded_v7 = from_mapping(
            ContinuousSemanticValidatorDraftV7,
            to_primitive(historical_v7),
        )
        self.assertIsNotNone(
            decoded_v7.compile(
                writer_story_text="Hana set down the teacup."
            ).finalization_package
        )
        historical_v8 = ContinuousSemanticValidatorDraftV8.from_v4(_active_draft())
        decoded_v8 = from_mapping(
            ContinuousSemanticValidatorDraftV8,
            to_primitive(historical_v8),
        )
        self.assertIsNotNone(
            decoded_v8.compile(
                writer_story_text="Hana set down the teacup."
            ).finalization_package
        )
        historical_v9 = ContinuousSemanticValidatorDraftV9.from_v4(_active_draft())
        decoded_v9 = from_mapping(
            ContinuousSemanticValidatorDraftV9,
            to_primitive(historical_v9),
        )
        self.assertIsNotNone(
            decoded_v9.compile(
                writer_story_text="Hana set down the teacup."
            ).finalization_package
        )
        historical_v10 = ContinuousSemanticValidatorDraftV10.from_v4(_active_draft())
        decoded_v10 = from_mapping(
            ContinuousSemanticValidatorDraftV10,
            to_primitive(historical_v10),
        )
        self.assertIsNotNone(
            decoded_v10.compile(
                writer_story_text="Hana set down the teacup."
            ).finalization_package
        )

    def test_active_wire_omits_and_python_derives_final_stop_state(self) -> None:
        payload = to_primitive(_active_wire())
        provider_sequence = payload["decision"]["complete_final_sequence"]
        self.assertNotIn("final_stop_state", provider_sequence)
        self.assertNotIn("schema_version", provider_sequence)
        Draft202012Validator(continuous_semantic_validator_draft_json_schema()).validate(
            payload
        )
        decoded = from_mapping(ContinuousSemanticValidatorDraftV12, payload)
        final_sequence = decoded.compile(
            writer_story_text="Hana set down the teacup."
        ).finalization_package.complete_final_sequence
        self.assertEqual(
            final_sequence.schema_version,
            FinalSequenceV1.SCHEMA_VERSION,
        )
        self.assertEqual(
            final_sequence.final_stop_state,
            final_sequence.items[-1].resulting_state,
        )
        injected = deepcopy(payload)
        injected["decision"]["complete_final_sequence"]["schema_version"] = (
            FinalSequenceV1.SCHEMA_VERSION
        )
        with self.assertRaises(ValidationError):
            Draft202012Validator(
                continuous_semantic_validator_draft_json_schema()
            ).validate(injected)
        with self.assertRaises(ContractValidationError):
            from_mapping(ContinuousSemanticValidatorDraftV12, injected)

    def test_active_wire_omits_and_python_derives_event_summary(self) -> None:
        payload = to_primitive(_active_wire())
        event_payload = payload["decision"]["event_record"]
        self.assertNotIn("summary", event_payload)
        Draft202012Validator(
            continuous_semantic_validator_draft_json_schema()
        ).validate(payload)

        sequence = _active_draft().complete_final_sequence
        first = sequence.items[0]
        second = replace(
            first,
            item_key="validator_schema_surface_followup",
            realized_event="Hana asked whether Ted wanted tea.",
            resulting_state="Hana awaits Ted's answer.",
        )
        two_item_sequence = replace(sequence, items=(first, second))
        derived = _python_derived_event_record(
            ProviderEventRecordDraftV2(
                event_id="event:validator_schema_surface",
                accepted_turn_id="turn:validator_schema_surface",
                scene_id="scene:validator_schema_surface",
                final_sequence_item_keys=(first.item_key, second.item_key),
                protected_user_source_claim_keys=(),
            ),
            two_item_sequence,
        )
        self.assertEqual(
            derived.summary,
            "Hana set down the teacup. Hana asked whether Ted wanted tea.",
        )

        injected = deepcopy(payload)
        injected["decision"]["event_record"]["summary"] = (
            "Provider-authored paraphrases are not accepted."
        )
        with self.assertRaises(ValidationError):
            Draft202012Validator(
                continuous_semantic_validator_draft_json_schema()
            ).validate(injected)
        with self.assertRaises(ContractValidationError):
            from_mapping(ContinuousSemanticValidatorDraftV12, injected)

    def test_failed_v2_cross_field_shape_is_reproduced_provider_free(self) -> None:
        payload = to_primitive(_active_draft())
        payload["schema_version"] = ContinuousSemanticValidatorDraftV2.SCHEMA_VERSION
        payload["complete_final_sequence"]["schema_version"] = (
            FinalSequenceV1.SCHEMA_VERSION
        )
        payload["complete_final_sequence"]["final_stop_state"] = (
            "A schema-valid but unequal duplicate stop state."
        )
        Draft202012Validator(_schema_for(ContinuousSemanticValidatorDraftV2)).validate(
            payload
        )
        with self.assertRaisesRegex(
            ContractValidationError,
            "final stop state must be the exact last resulting state",
        ):
            from_mapping(ContinuousSemanticValidatorDraftV2, payload)

    def test_all_active_schema_versions_are_python_owned_or_absent(self) -> None:
        schema = continuous_semantic_validator_draft_json_schema()
        versions = _schema_version_schemas(schema)
        self.assertTrue(versions)
        for path, version_schema in versions:
            with self.subTest(path=path):
                self.assertEqual(set(version_schema), {"type", "const"})
                self.assertEqual(version_schema["type"], "string")
                self.assertIsInstance(version_schema["const"], str)
                self.assertTrue(version_schema["const"].startswith("cera."))
        self.assertEqual(
            dict(versions)["$.properties.schema_version"]["const"],
            ContinuousSemanticValidatorDraftV12.SCHEMA_VERSION,
        )
        decision_branches = schema["properties"]["decision"]["anyOf"]
        sequence_schemas = [
            branch["properties"]["complete_final_sequence"]
            for branch in decision_branches
            if "complete_final_sequence" in branch.get("properties", {})
        ]
        self.assertEqual(len(sequence_schemas), 2)
        for sequence_schema in sequence_schemas:
            self.assertNotIn("schema_version", sequence_schema["properties"])

    def test_accepted_branch_omits_all_diagnostic_fields(self) -> None:
        payload = to_primitive(_active_wire())
        decision = payload["decision"]
        review = decision["creator_review"]
        self.assertEqual(decision["decision_kind"], "accepted")
        self.assertEqual(review["review_kind"], "good")
        for forbidden in ("reason_codes", "issue_owner", "severity"):
            self.assertNotIn(forbidden, review)
        for forbidden in ("reason_codes", "semantic_status"):
            self.assertNotIn(forbidden, payload)
        compiled = from_mapping(
            ContinuousSemanticValidatorDraftV12, payload
        ).compile(
            writer_story_text="Hana set down the teacup."
        ).finalization_package
        self.assertEqual(compiled.creator_review.reason_codes, ())
        self.assertIs(compiled.creator_review.issue_owner, ReviewIssueOwner.NONE)

    def test_turn_branches_require_their_own_span_and_adjudication_family(self) -> None:
        schema = continuous_semantic_validator_draft_json_schema()
        canonical_branches = [
            branch
            for branch in schema["properties"]["decision"]["anyOf"]
            if "realization_segments" in branch.get("properties", {})
        ]
        self.assertEqual(len(canonical_branches), 2)
        for branch in canonical_branches:
            properties = branch["properties"]
            self.assertEqual(properties["realization_segments"]["minItems"], 1)
            self.assertEqual(
                properties["protected_semantic_adjudications"]["minItems"], 1
            )
            self.assertNotIn("diagnostic_story_segments", properties)
            self.assertNotIn(
                "diagnostic_protected_semantic_adjudications", properties
            )
        diagnostic_branches = [
            branch
            for branch in schema["properties"]["decision"]["anyOf"]
            if "diagnostic_story_segments" in branch.get("properties", {})
        ]
        self.assertEqual(len(diagnostic_branches), 1)
        diagnostic = diagnostic_branches[0]["properties"]
        self.assertEqual(diagnostic["diagnostic_story_segments"]["minItems"], 1)
        self.assertEqual(
            diagnostic["diagnostic_protected_semantic_adjudications"]["minItems"],
            1,
        )
        self.assertNotIn("realization_segments", diagnostic)
        self.assertNotIn("protected_semantic_adjudications", diagnostic)
        finalizing = [
            branch
            for branch in canonical_branches
            if "complete_final_sequence" in branch["properties"]
        ]
        self.assertEqual(len(finalizing), 2)
        for branch in finalizing:
            sequence = branch["properties"]["complete_final_sequence"]
            self.assertEqual(sequence["properties"]["items"]["minItems"], 1)

    def test_openai_projection_preserves_every_schema_version_const(self) -> None:
        neutral = _schema_version_schemas(
            continuous_semantic_validator_draft_json_schema()
        )
        projected = _schema_version_schemas(
            project_provider_output_schema(
                continuous_semantic_validator_draft_json_schema(),
                ProviderSchemaDialect.OPENAI_STRUCTURED_OUTPUT_V1,
            ).provider_schema
        )
        self.assertEqual(projected, neutral)

    def test_runtime_identity_constants_reach_the_provider_schema(self) -> None:
        payload = to_primitive(_active_wire())
        captured: dict[str, object] = {}

        class Transport:
            route = continuous_validator_route(model="gpt-5.6-sol", effort="medium")
            runner = SimpleNamespace(provider_thread_id="thread:identity-schema-test")

            def invoke(self, _prompt: str, **kwargs):
                captured["output_schema"] = deepcopy(kwargs["output_schema"])
                for name in (
                    "on_worker_started",
                    "on_worker_preflight",
                    "on_transport_invoke",
                ):
                    callback = kwargs.get(name)
                    if callback is not None:
                        callback()
                return SimpleNamespace(
                    parsed_json=deepcopy(payload),
                    receipt={"status": "completed"},
                    operation_telemetry={"status": "completed"},
                    tool_call_count=0,
                    failed_tool_call_count=0,
                    tool_names=(),
                    tool_server_names=(),
                )

        expected = {
            "package_id": payload["package_id"],
            "world_id": payload["world_id"],
            "branch_id": payload["branch_id"],
        }
        with TemporaryDirectory() as directory:
            port = CodexContinuousValidatorPort(
                Transport(),
                call_ledger=ContinuousProviderCallLedger(
                    Path(directory) / "CALL_LEDGER.jsonl"
                ),
            )
            port.validate(
                "Validate the benign fixture.",
                writer_story_text="Hana set down the teacup.",
                expected_package_id=expected["package_id"],
                expected_world_id=expected["world_id"],
                expected_branch_id=expected["branch_id"],
            )

        submitted = captured["output_schema"]
        for field_name, expected_value in expected.items():
            self.assertEqual(
                submitted["properties"][field_name]["const"], expected_value
            )
        projected = project_provider_output_schema(
            submitted,
            ProviderSchemaDialect.OPENAI_STRUCTURED_OUTPUT_V1,
        ).provider_schema
        for field_name, expected_value in expected.items():
            self.assertEqual(
                projected["properties"][field_name]["const"], expected_value
            )

    def test_runtime_identity_mismatch_fails_after_provider_capture(self) -> None:
        payload = to_primitive(_active_wire())
        payload["package_id"] = "package:changed_by_provider"

        class Transport:
            route = continuous_validator_route(model="gpt-5.6-sol", effort="medium")
            runner = SimpleNamespace(provider_thread_id="thread:identity-mismatch-test")

            def invoke(self, _prompt: str, **kwargs):
                for name in (
                    "on_worker_started",
                    "on_worker_preflight",
                    "on_transport_invoke",
                ):
                    callback = kwargs.get(name)
                    if callback is not None:
                        callback()
                return SimpleNamespace(
                    parsed_json=deepcopy(payload),
                    receipt={"status": "completed"},
                    operation_telemetry={"status": "completed"},
                    tool_call_count=0,
                    failed_tool_call_count=0,
                    tool_names=(),
                    tool_server_names=(),
                )

        with TemporaryDirectory() as directory:
            ledger = ContinuousProviderCallLedger(
                Path(directory) / "CALL_LEDGER.jsonl"
            )
            port = CodexContinuousValidatorPort(Transport(), call_ledger=ledger)
            with self.assertRaisesRegex(
                ContractValidationError, "provider identity changed"
            ):
                port.validate(
                    "Validate the benign fixture.",
                    writer_story_text="Hana set down the teacup.",
                    expected_package_id="package:validator_schema_surface",
                    expected_world_id="world:validator_schema_surface",
                    expected_branch_id="branch:main",
                )
            self.assertEqual(
                ledger.events[-1]["state"],
                "provider_completed_post_validation_failed",
            )

    def test_raw_provider_json_is_observed_before_closed_dto_decode(self) -> None:
        payload = to_primitive(_active_wire())
        payload["decision"]["complete_final_sequence"]["schema_version"] = "provider-authored"

        class Transport:
            route = continuous_validator_route(model="gpt-5.6-sol", effort="medium")
            runner = SimpleNamespace(provider_thread_id="thread:raw-capture-test")

            def invoke(self, _prompt: str, **kwargs):
                for name in (
                    "on_worker_started",
                    "on_worker_preflight",
                    "on_transport_invoke",
                ):
                    callback = kwargs.get(name)
                    if callback is not None:
                        callback()
                return SimpleNamespace(
                    parsed_json=deepcopy(payload),
                    receipt={"status": "completed"},
                    operation_telemetry={"status": "completed"},
                    tool_call_count=0,
                    failed_tool_call_count=0,
                    tool_names=(),
                    tool_server_names=(),
                )

        with TemporaryDirectory() as directory:
            evidence_root = Path(directory).resolve()
            artifact = evidence_root / "validator" / "RAW_PROVIDER_RESULT.json"
            capture = QualificationRawProviderJsonCapture(artifact)
            ledger = ContinuousProviderCallLedger(
                evidence_root / "CALL_LEDGER.jsonl"
            )
            port = CodexContinuousValidatorPort(
                Transport(),
                call_ledger=ledger,
                raw_result_observer=capture,
            )
            with self.assertRaises(ContractValidationError):
                port.validate(
                    "Validate the benign fixture.",
                    writer_story_text="Hana set down the teacup.",
                )
            self.assertEqual(
                ledger.events[-1]["state"],
                "provider_completed_post_validation_failed",
            )
            self.assertEqual(json.loads(artifact.read_text(encoding="utf-8")), payload)
            self.assertEqual(capture.artifact_sha256, bytes_sha256(artifact.read_bytes()))
            self.assertEqual(
                capture.evidence(evidence_root=evidence_root),
                {
                    "raw_provider_result_path": "validator/RAW_PROVIDER_RESULT.json",
                    "raw_provider_result_sha256": capture.artifact_sha256,
                },
            )

    def test_raw_provider_registry_owns_one_explicit_evidence_root(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            first_root = root / "first"
            second_root = root / "second"
            first = QualificationRawProviderJsonCaptureRegistry(first_root)
            second = QualificationRawProviderJsonCaptureRegistry(second_root)
            first.configure("attempt_001")
            second.configure("attempt_001")

            first_capture = first.allocate("validator")
            second_capture = second.allocate("validator")
            first_capture({"verdict": "first"})
            second_capture({"verdict": "second"})
            first.bind("validator", "session:first", first_capture)
            second.bind("validator", "session:second", second_capture)

            self.assertTrue(first_capture.artifact_path.is_relative_to(first_root))
            self.assertTrue(second_capture.artifact_path.is_relative_to(second_root))
            self.assertNotEqual(first_capture.artifact_path, second_capture.artifact_path)
            self.assertEqual(
                first.for_session("validator", "session:first"),
                first_capture.evidence(evidence_root=first_root),
            )
            self.assertEqual(first.inventory()[0]["role"], "validator")
            self.assertEqual(second.inventory()[0]["role"], "validator")

    def test_raw_provider_registry_is_closed_and_fail_closed(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            with self.assertRaises(ContractValidationError):
                QualificationRawProviderJsonCaptureRegistry(Path("relative"))

            registry = QualificationRawProviderJsonCaptureRegistry(root)
            with self.assertRaises(StateConflictError):
                registry.allocate("validator")
            with self.assertRaises(ContractValidationError):
                registry.configure("../escaped")
            registry.configure("attempt_001")
            with self.assertRaises(StateConflictError):
                registry.configure("attempt_002")
            with self.assertRaises(ContractValidationError):
                registry.allocate("planner")

            capture = registry.allocate("reader")
            capture({"verdict": "accepted"})
            registry.bind("reader", "session:reader", capture)
            with self.assertRaises(StateConflictError):
                registry.bind("reader", "session:reader", capture)
            with self.assertRaises(StateConflictError):
                capture({"verdict": "changed"})

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
