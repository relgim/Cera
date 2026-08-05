from __future__ import annotations

from copy import deepcopy
from dataclasses import fields, replace
import unittest

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from cera.continuous.contracts import (
    CharacterRoleLedgerV1,
    DiagnosticGroundingStatus,
    DiagnosticProtectedSemanticAdjudicationV1,
    DiagnosticStorySegmentV1,
    DiagnosticViolationClassification,
    IngressSourceUnitKind,
    IngressSourceUnitV1,
    ProhibitedWriterDetailClass,
    ProtectedSemanticRelationKind,
    StoryRealizationKind,
    StoryRealizationSegmentV1,
    ValidatorSemanticStatus,
)
from cera.continuous.evidence import RequestEvidenceBindingRegistry
from cera.continuous.provider import (
    ContinuousSemanticValidatorDraftV13,
    ProviderAcceptedTurnDecisionDraftV6,
    ProviderConcernTurnDecisionDraftV6,
    ProviderDiagnosticProtectedSemanticAdjudicationDraftV1,
    ProviderDiagnosticStorySegmentDraftV1,
    ProviderRejectedSemanticStatus,
    ProviderRejectedTurnDecisionDraftV4,
    ProviderRejectedViolationDraftV1,
    ProviderWriterRecallEligibility,
    continuous_semantic_validator_draft_json_schema,
)
from cera.errors import ContractValidationError
from cera.providers import (
    ProviderSchemaDialect,
    project_provider_output_schema,
    validate_provider_output_schema,
)
from cera.schema import from_mapping
from cera.serialization import text_sha256, to_primitive
from tests.test_continuous_hash_custody import _canonical_v4


STORY = "Hana smiled as Ted stepped inside."
HANA_TEXT = "Hana smiled as "
TED_TEXT = "Ted stepped inside."


def _diagnostic_decision(
    status: ProviderRejectedSemanticStatus = ProviderRejectedSemanticStatus.REJECTED,
) -> ProviderRejectedTurnDecisionDraftV4:
    recall_eligible = status is ProviderRejectedSemanticStatus.REJECTED
    return ProviderRejectedTurnDecisionDraftV4(
        schema_version=ProviderRejectedTurnDecisionDraftV4.SCHEMA_VERSION,
        semantic_status=status,
        primary_reason_code="protected_user_invention",
        additional_reason_codes=("protected_user_stopping_boundary_violation",),
        diagnostic_story_segments=(
            ProviderDiagnosticStorySegmentDraftV1(
                schema_version=ProviderDiagnosticStorySegmentDraftV1.SCHEMA_VERSION,
                segment_key="hana_smile",
                kind=StoryRealizationKind.ACTION,
                output_start=0,
                output_end=len(HANA_TEXT),
                roles=CharacterRoleLedgerV1(
                    action_owner_ids=("character:hana_hanezawa",),
                ),
                grounding_status=DiagnosticGroundingStatus.GROUNDED,
                protected_user_source_claim_keys=(),
            ),
            ProviderDiagnosticStorySegmentDraftV1(
                schema_version=ProviderDiagnosticStorySegmentDraftV1.SCHEMA_VERSION,
                segment_key="ted_steps_inside",
                kind=StoryRealizationKind.ACTION,
                output_start=len(HANA_TEXT),
                output_end=len(STORY),
                roles=CharacterRoleLedgerV1(
                    action_owner_ids=("character:ted",),
                ),
                grounding_status=(
                    DiagnosticGroundingStatus.UNGROUNDED_PROTECTED_USER_ASSERTION
                ),
                protected_user_source_claim_keys=(),
            ),
        ),
        diagnostic_protected_semantic_adjudications=(
            ProviderDiagnosticProtectedSemanticAdjudicationDraftV1(
                schema_version=(
                    ProviderDiagnosticProtectedSemanticAdjudicationDraftV1.SCHEMA_VERSION
                ),
                adjudication_key="hana_smile_adjudication",
                segment_key="hana_smile",
                output_start=0,
                output_end=len(HANA_TEXT),
                protected_user_id="character:ted",
                relation=ProtectedSemanticRelationKind.NONE,
                grounding_status=DiagnosticGroundingStatus.GROUNDED,
                violation_classification=DiagnosticViolationClassification.NONE,
                npc_assertion_owner_ids=(),
                protected_user_source_claim_keys=(),
            ),
            ProviderDiagnosticProtectedSemanticAdjudicationDraftV1(
                schema_version=(
                    ProviderDiagnosticProtectedSemanticAdjudicationDraftV1.SCHEMA_VERSION
                ),
                adjudication_key="ted_steps_inside_adjudication",
                segment_key="ted_steps_inside",
                output_start=len(HANA_TEXT),
                output_end=len(STORY),
                protected_user_id="character:ted",
                relation=ProtectedSemanticRelationKind.PROTECTED_ASSERTION,
                grounding_status=(
                    DiagnosticGroundingStatus.UNGROUNDED_PROTECTED_USER_ASSERTION
                ),
                violation_classification=(
                    DiagnosticViolationClassification.UNGROUNDED_PROTECTED_USER_ASSERTION
                ),
                npc_assertion_owner_ids=(),
                protected_user_source_claim_keys=(),
            ),
        ),
        writer_recall_eligibility=(
            ProviderWriterRecallEligibility.ELIGIBLE
            if recall_eligible
            else ProviderWriterRecallEligibility.INELIGIBLE
        ),
        writer_recall_violations=(
            (
                ProviderRejectedViolationDraftV1(
                    schema_version=(
                        ProviderRejectedViolationDraftV1.SCHEMA_VERSION
                    ),
                    segment_key="ted_steps_inside",
                    prohibited_detail_classes=(
                        ProhibitedWriterDetailClass.PROTECTED_USER_BEHAVIOR,
                        ProhibitedWriterDetailClass.STOPPING_BOUNDARY_VIOLATION,
                    ),
                ),
            )
            if recall_eligible
            else ()
        ),
    )


def _wire(
    status: ProviderRejectedSemanticStatus = ProviderRejectedSemanticStatus.REJECTED,
) -> ContinuousSemanticValidatorDraftV13:
    return ContinuousSemanticValidatorDraftV13(
        schema_version=ContinuousSemanticValidatorDraftV13.SCHEMA_VERSION,
        package_id="candidate:rejected_diagnostic_fixture",
        world_id="world:rejected_diagnostic_fixture",
        branch_id="branch:main",
        decision=_diagnostic_decision(status),
    )


class RejectedDiagnosticContractTests(unittest.TestCase):
    def test_whitespace_only_diagnostic_span_is_rejected_without_python_repair(self) -> None:
        span = ProviderDiagnosticStorySegmentDraftV1(
            schema_version=ProviderDiagnosticStorySegmentDraftV1.SCHEMA_VERSION,
            segment_key="paragraph_break",
            kind=StoryRealizationKind.NARRATION,
            output_start=0,
            output_end=2,
            roles=CharacterRoleLedgerV1(),
            grounding_status=DiagnosticGroundingStatus.GROUNDED,
            protected_user_source_claim_keys=(),
        )

        with self.assertRaisesRegex(
            ContractValidationError,
            "diagnostic_story_segment.exact_text must be non-empty and bounded",
        ):
            span.compile(writer_story_text="\n\nHana spoke.")

        attached = replace(span, output_end=len("\n\nHana spoke."))
        compiled = attached.compile(writer_story_text="\n\nHana spoke.")
        self.assertEqual(compiled.exact_text, "\n\nHana spoke.")

    def test_exact_seeded_rejection_decodes_and_compiles_truthfully(self) -> None:
        payload = to_primitive(_wire())
        schema = continuous_semantic_validator_draft_json_schema()
        Draft202012Validator(schema).validate(payload)
        decoded = from_mapping(ContinuousSemanticValidatorDraftV13, payload)
        result = decoded.compile(writer_story_text=STORY)

        self.assertIs(result.semantic_status, ValidatorSemanticStatus.REJECTED)
        self.assertIsNone(result.finalization_package)
        self.assertFalse(result.story_segments)
        self.assertFalse(result.protected_semantic_adjudications)
        self.assertEqual(
            tuple(value.exact_text for value in result.diagnostic_story_segments),
            (HANA_TEXT, TED_TEXT),
        )
        ted = result.diagnostic_story_segments[1]
        self.assertEqual(ted.protected_user_source_claim_keys, ())
        self.assertIs(
            ted.grounding_status,
            DiagnosticGroundingStatus.UNGROUNDED_PROTECTED_USER_ASSERTION,
        )
        self.assertIs(
            result.diagnostic_protected_semantic_adjudications[1].violation_classification,
            DiagnosticViolationClassification.UNGROUNDED_PROTECTED_USER_ASSERTION,
        )
        self.assertIs(
            result.writer_recall_eligibility,
            ProviderWriterRecallEligibility.ELIGIBLE,
        )
        self.assertEqual(
            result.writer_recall_offending_spans[0].exact_text,
            TED_TEXT,
        )

    def test_duplicate_primary_reason_is_normalized_only_at_semantic_projection(self) -> None:
        payload = to_primitive(_wire())
        payload["decision"]["additional_reason_codes"] = [
            "protected_user_stopping_boundary_violation",
            "protected_user_invention",
        ]
        Draft202012Validator(
            continuous_semantic_validator_draft_json_schema()
        ).validate(payload)

        decoded = from_mapping(ContinuousSemanticValidatorDraftV13, payload)
        self.assertEqual(
            decoded.decision.additional_reason_codes,
            (
                "protected_user_stopping_boundary_violation",
                "protected_user_invention",
            ),
        )
        result = decoded.compile(writer_story_text=STORY)
        self.assertEqual(
            result.reason_codes,
            (
                "protected_user_invention",
                "protected_user_stopping_boundary_violation",
            ),
        )

        directive = result.build_writer_recall_directive(
            rejected_candidate_id="candidate:duplicate_reason_fixture",
            rejected_story_text=STORY,
            frozen_authority_package_sha256="a" * 64,
            source_attempt_number=1,
        )
        self.assertEqual(directive.reason_codes, result.reason_codes)
        self.assertFalse(directive.authoritative)
        self.assertFalse(directive.attempts_may_merge)

    def test_rejected_inconclusive_and_error_round_trip_neutral_and_openai(self) -> None:
        neutral = continuous_semantic_validator_draft_json_schema()
        projected = project_provider_output_schema(
            neutral,
            ProviderSchemaDialect.OPENAI_STRUCTURED_OUTPUT_V1,
        ).provider_schema
        validate_provider_output_schema(
            projected,
            ProviderSchemaDialect.OPENAI_STRUCTURED_OUTPUT_V1,
        )
        for status in ProviderRejectedSemanticStatus:
            with self.subTest(status=status.value):
                payload = to_primitive(_wire(status))
                Draft202012Validator(neutral).validate(payload)
                Draft202012Validator(projected).validate(payload)
                result = from_mapping(
                    ContinuousSemanticValidatorDraftV13, payload
                ).compile(writer_story_text=STORY)
                self.assertIs(
                    result.semantic_status,
                    ValidatorSemanticStatus(status.value),
                )
                self.assertIsNone(result.finalization_package)

    def test_diagnostic_wire_omits_text_and_hash_and_python_derives_both(self) -> None:
        payload = to_primitive(_wire())
        for segment in payload["decision"]["diagnostic_story_segments"]:
            self.assertNotIn("exact_text", segment)
            self.assertNotIn("exact_text_sha256", segment)
        for adjudication in payload["decision"][
            "diagnostic_protected_semantic_adjudications"
        ]:
            self.assertNotIn("exact_text", adjudication)
            self.assertNotIn("exact_text_sha256", adjudication)
        result = _wire().compile(writer_story_text=STORY)
        self.assertEqual(
            result.diagnostic_story_segments[1].exact_text_sha256,
            text_sha256(TED_TEXT),
        )
        self.assertEqual(
            result.diagnostic_protected_semantic_adjudications[
                1
            ].exact_text_sha256,
            text_sha256(TED_TEXT),
        )

    def test_canonical_segment_still_rejects_unclaimed_ted_assertion(self) -> None:
        with self.assertRaisesRegex(
            ContractValidationError,
            "requires exact supplied claim keys",
        ):
            StoryRealizationSegmentV1(
                schema_version=StoryRealizationSegmentV1.SCHEMA_VERSION,
                segment_key="ted_steps_inside",
                kind=StoryRealizationKind.ACTION,
                output_start=0,
                output_end=len(TED_TEXT),
                exact_text=TED_TEXT,
                roles=CharacterRoleLedgerV1(
                    action_owner_ids=("character:ted",),
                ),
                protected_user_source_claim_keys=(),
            )

    def test_ungrounded_status_is_exclusive_to_zero_claim_ted_assertions(self) -> None:
        ted = _diagnostic_decision().diagnostic_story_segments[1]
        with self.assertRaisesRegex(
            ContractValidationError,
            "Ted assertion with zero claims",
        ):
            replace(
                ted,
                roles=CharacterRoleLedgerV1(
                    action_owner_ids=("character:hana_hanezawa",),
                ),
            ).compile(writer_story_text=STORY)
        with self.assertRaisesRegex(
            ContractValidationError,
            "Ted assertion with zero claims",
        ):
            replace(
                ted,
                protected_user_source_claim_keys=("invented_claim",),
            ).compile(writer_story_text=STORY)
        with self.assertRaisesRegex(
            ContractValidationError,
            "one exact supplied claim",
        ):
            replace(
                ted,
                grounding_status=DiagnosticGroundingStatus.GROUNDED,
            ).compile(writer_story_text=STORY)

    def test_gaps_offsets_and_adjudication_conflicts_fail_closed(self) -> None:
        decision = _diagnostic_decision()
        with self.assertRaisesRegex(ContractValidationError, "not gap-free"):
            replace(
                decision,
                diagnostic_story_segments=(
                    decision.diagnostic_story_segments[0],
                    replace(
                        decision.diagnostic_story_segments[1],
                        output_start=len(HANA_TEXT) + 1,
                    ),
                ),
            ).compile(writer_story_text=STORY)
        with self.assertRaisesRegex(ContractValidationError, "exact diagnostic"):
            replace(
                decision,
                diagnostic_protected_semantic_adjudications=(
                    decision.diagnostic_protected_semantic_adjudications[0],
                    replace(
                        decision.diagnostic_protected_semantic_adjudications[1],
                        output_start=len(HANA_TEXT) - 1,
                    ),
                ),
            ).compile(writer_story_text=STORY)
        tampered = deepcopy(to_primitive(_wire()))
        tampered["decision"]["diagnostic_story_segments"][1]["exact_text"] = TED_TEXT
        with self.assertRaises(ValidationError):
            Draft202012Validator(
                continuous_semantic_validator_draft_json_schema()
            ).validate(tampered)
        with self.assertRaises(ContractValidationError):
            from_mapping(ContinuousSemanticValidatorDraftV13, tampered)

    def test_accepted_and_concern_schema_branches_expose_no_diagnostics(self) -> None:
        schema = continuous_semantic_validator_draft_json_schema()
        branches = schema["properties"]["decision"]["anyOf"]
        canonical = [
            branch
            for branch in branches
            if branch.get("properties", {}).get("schema_version", {}).get("const")
            in {
                ProviderAcceptedTurnDecisionDraftV6.SCHEMA_VERSION,
                ProviderConcernTurnDecisionDraftV6.SCHEMA_VERSION,
            }
        ]
        self.assertEqual(len(canonical), 2)
        for branch in canonical:
            properties = branch["properties"]
            self.assertIn("realization_segments", properties)
            self.assertNotIn("diagnostic_story_segments", properties)
            self.assertNotIn(
                "diagnostic_protected_semantic_adjudications", properties
            )
        rejected = [
            branch
            for branch in branches
            if branch.get("properties", {}).get("schema_version", {}).get("const")
            == ProviderRejectedTurnDecisionDraftV4.SCHEMA_VERSION
        ]
        self.assertEqual(len(rejected), 1)
        self.assertNotIn("realization_segments", rejected[0]["properties"])
        self.assertNotIn(
            "protected_semantic_adjudications", rejected[0]["properties"]
        )

    def test_diagnostic_evidence_cannot_enter_finalization_or_result_authority(self) -> None:
        result = _wire().compile(writer_story_text=STORY)
        canonical_package = _canonical_v4().compile().finalization_package
        self.assertIsNotNone(canonical_package)
        with self.assertRaisesRegex(
            ContractValidationError,
            "cannot enter finalization",
        ):
            replace(result, finalization_package=canonical_package)
        with self.assertRaisesRegex(
            ContractValidationError,
            "canonical protected adjudications",
        ):
            replace(
                canonical_package,
                protected_semantic_adjudications=(
                    result.diagnostic_protected_semantic_adjudications[0],
                ),
            )
        with self.assertRaisesRegex(
            ContractValidationError,
            "canonical segment types",
        ):
            replace(
                result,
                story_segments=(result.diagnostic_story_segments[0],),
                diagnostic_story_segments=(),
                diagnostic_protected_semantic_adjudications=(),
            )

    def test_diagnostic_registry_validation_is_non_mutating(self) -> None:
        result = _wire().compile(writer_story_text=STORY)
        registry = RequestEvidenceBindingRegistry(
            world_id="world:diagnostic",
            branch_id="branch:main",
            turn_id="turn:diagnostic",
        )
        registry.validate_validator_diagnostics(
            story_text=STORY,
            diagnostic_story_segments=result.diagnostic_story_segments,
            diagnostic_protected_semantic_adjudications=(
                result.diagnostic_protected_semantic_adjudications
            ),
            allowed_character_ids=("character:hana_hanezawa",),
        )
        self.assertEqual(registry._story_segments, {})

    def test_rejected_diagnostic_may_cite_source_without_gaining_authority(self) -> None:
        source = "Ted remains silent and still."
        registry = RequestEvidenceBindingRegistry(
            world_id="world:diagnostic-citation",
            branch_id="branch:main",
            turn_id="turn:diagnostic-citation",
        )
        registry.allocate_current_source(
            source_identity="source:diagnostic-citation",
            source_text=source,
            protected_user_allowance_scope="exact_source_only",
            source_units=(
                IngressSourceUnitV1(
                    schema_version=IngressSourceUnitV1.SCHEMA_VERSION,
                    source_unit_key="source_state_0001",
                    kind=IngressSourceUnitKind.STATE,
                    source_start=0,
                    source_end=len(source),
                    exact_text=source,
                    actor_id="character:ted",
                    speaker_id=None,
                    classification_basis="explicit_ingress_actor",
                ),
            ),
        )
        claim_key = next(iter(registry._protected_user_claims))
        rejected_text = "the one who had not yet spoken."
        segment = DiagnosticStorySegmentV1(
            schema_version=DiagnosticStorySegmentV1.SCHEMA_VERSION,
            segment_key="ted_silence_paraphrase",
            kind=StoryRealizationKind.PRIVATE_STATE,
            output_start=0,
            output_end=len(rejected_text),
            exact_text=rejected_text,
            exact_text_sha256=text_sha256(rejected_text),
            roles=CharacterRoleLedgerV1(
                state_owner_ids=("character:ted",),
            ),
            grounding_status=DiagnosticGroundingStatus.GROUNDED,
            protected_user_source_claim_keys=(claim_key,),
        )
        adjudication = DiagnosticProtectedSemanticAdjudicationV1(
            schema_version=DiagnosticProtectedSemanticAdjudicationV1.SCHEMA_VERSION,
            adjudication_key="ted_silence_paraphrase_adjudication",
            segment_key=segment.segment_key,
            output_start=0,
            output_end=len(rejected_text),
            exact_text_sha256=text_sha256(rejected_text),
            protected_user_id="character:ted",
            relation=ProtectedSemanticRelationKind.PROTECTED_ASSERTION,
            grounding_status=DiagnosticGroundingStatus.GROUNDED,
            violation_classification=DiagnosticViolationClassification.NONE,
            npc_assertion_owner_ids=(),
            protected_user_source_claim_keys=(claim_key,),
        )

        registry.validate_validator_diagnostics(
            story_text=rejected_text,
            diagnostic_story_segments=(segment,),
            diagnostic_protected_semantic_adjudications=(adjudication,),
            allowed_character_ids=(),
        )
        self.assertEqual(registry._story_segments, {})

        unknown = "claim_source_00000000000000000000"
        with self.assertRaisesRegex(PermissionError, "supplied ingress citation"):
            registry.validate_validator_diagnostics(
                story_text=rejected_text,
                diagnostic_story_segments=(
                    replace(
                        segment,
                        protected_user_source_claim_keys=(unknown,),
                    ),
                ),
                diagnostic_protected_semantic_adjudications=(
                    replace(
                        adjudication,
                        protected_user_source_claim_keys=(unknown,),
                    ),
                ),
                allowed_character_ids=(),
            )

        dialogue = replace(
            segment,
            kind=StoryRealizationKind.DIALOGUE,
            roles=CharacterRoleLedgerV1(speaker_ids=("character:ted",)),
        )
        with self.assertRaisesRegex(PermissionError, "changed claim semantics"):
            registry.validate_validator_diagnostics(
                story_text=rejected_text,
                diagnostic_story_segments=(dialogue,),
                diagnostic_protected_semantic_adjudications=(adjudication,),
                allowed_character_ids=(),
            )

    def test_diagnostic_types_have_no_conversion_to_canonical_authority(self) -> None:
        self.assertNotIn(
            "finalization_package",
            {value.name for value in fields(ProviderRejectedTurnDecisionDraftV4)},
        )
        self.assertNotIn(
            "complete_final_sequence",
            {value.name for value in fields(ProviderRejectedTurnDecisionDraftV4)},
        )
        self.assertFalse(hasattr(DiagnosticStorySegmentV1, "to_canonical"))
        self.assertFalse(
            hasattr(DiagnosticProtectedSemanticAdjudicationV1, "to_canonical")
        )


if __name__ == "__main__":
    unittest.main()
