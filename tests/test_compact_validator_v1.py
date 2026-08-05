from __future__ import annotations

from copy import deepcopy
import unittest

from jsonschema import Draft202012Validator

from cera.continuous.contracts import ValidatorSemanticStatus, ValidatorTaskMode
from cera.continuous.prompting import (
    COMPACT_VALIDATOR_STABLE_INSTRUCTIONS,
    CONTINUOUS_COMPACT_VALIDATOR_PROMPT_VERSION,
    build_validator_prompt,
)
from cera.continuous.evidence import RequestEvidenceBindingRegistry
from cera.continuous.provider import (
    CONTINUOUS_COMPACT_VALIDATOR_ADAPTER_VERSION,
    ContinuousCompactSemanticValidatorDraftV1,
    ProviderCompactRejectedTurnDecisionDraftV1,
    ProviderWriterRecallEligibility,
    continuous_compact_semantic_validator_draft_json_schema,
    continuous_compact_validator_route,
)
from cera.providers import ProviderSchemaDialect, project_provider_output_schema
from cera.schema import from_mapping
from cera.serialization import to_primitive
from tests.test_continuous_planner_validator import sequence as planner_sequence
from tests.test_continuous_rejected_diagnostics import (
    STORY as REJECTED_STORY,
    _diagnostic_decision,
)
from tests.test_continuous_validator_schema_surface import _active_wire


def compact_accepted_payload() -> tuple[dict, str]:
    story = "Hana set down the teacup."
    payload = deepcopy(to_primitive(_active_wire()))
    decision = payload["decision"]
    material = decision.pop("realization_segments")
    material[0]["output_start"] = len("Hana ")
    decision.update(
        {
            "schema_version": "cera.provider_compact_accepted_turn_decision.v1",
            "material_segments": material,
            "noncanonical_spans": [],
            "beat_coverage": [
                {
                    "schema_version": "cera.provider_compact_beat_coverage.v1",
                    "beat_key": "planner_beat",
                    "material_segment_keys": ["segment_entire_story"],
                }
            ],
        }
    )
    decision["protected_semantic_adjudications"][0]["output_start"] = len(
        "Hana "
    )
    payload["schema_version"] = (
        ContinuousCompactSemanticValidatorDraftV1.SCHEMA_VERSION
    )
    return payload, story


class CompactValidatorV1Tests(unittest.TestCase):
    def test_sparse_accepted_contract_leaves_unlisted_text_noncanonical(self) -> None:
        payload, story = compact_accepted_payload()
        schema = continuous_compact_semantic_validator_draft_json_schema()
        Draft202012Validator(schema).validate(payload)
        decoded = from_mapping(ContinuousCompactSemanticValidatorDraftV1, payload)
        result = decoded.compile(
            writer_story_text=story,
            expected_planner_beat_keys=("planner_beat",),
        )

        self.assertIs(result.semantic_status, ValidatorSemanticStatus.ACCEPTED)
        self.assertEqual(result.story_segments[0].exact_text, "set down the teacup.")
        self.assertEqual(result.story_segments[0].output_start, len("Hana "))
        self.assertFalse(result.presentation_realization_segments)
        self.assertEqual(
            result.finalization_package.complete_final_sequence.items[0].story_segment_keys,
            ("segment_entire_story",),
        )
        registry = RequestEvidenceBindingRegistry(
            world_id=decoded.world_id,
            branch_id=decoded.branch_id,
            turn_id="turn:validator_schema_surface",
        )
        with self.assertRaisesRegex(PermissionError, "not gap-free"):
            registry.validate_validator_realization_boundary(
                story_text=story,
                story_segments=result.story_segments,
                presentation_segments=result.presentation_realization_segments,
                package=result.finalization_package,
                presentation_adjudications=(
                    result.presentation_protected_semantic_adjudications
                ),
                allowed_character_ids=("character:hana_hanezawa", "character:ted"),
            )
        registry = RequestEvidenceBindingRegistry(
            world_id=decoded.world_id,
            branch_id=decoded.branch_id,
            turn_id="turn:validator_schema_surface",
        )
        registry.validate_validator_realization_boundary(
            story_text=story,
            story_segments=result.story_segments,
            presentation_segments=result.presentation_realization_segments,
            package=result.finalization_package,
            presentation_adjudications=(
                result.presentation_protected_semantic_adjudications
            ),
            allowed_character_ids=("character:hana_hanezawa", "character:ted"),
            require_gap_free=False,
        )

    def test_sparse_rejection_needs_only_the_exact_offending_span(self) -> None:
        historical = _diagnostic_decision()
        payload = {
            "schema_version": ContinuousCompactSemanticValidatorDraftV1.SCHEMA_VERSION,
            "package_id": "package:compact_rejection",
            "world_id": "world:compact_rejection",
            "branch_id": "branch:main",
            "decision": {
                "schema_version": ProviderCompactRejectedTurnDecisionDraftV1.SCHEMA_VERSION,
                "semantic_status": historical.semantic_status.value,
                "primary_reason_code": historical.primary_reason_code,
                "additional_reason_codes": list(historical.additional_reason_codes),
                "offending_spans": [
                    to_primitive(historical.diagnostic_story_segments[1])
                ],
                "protected_semantic_adjudications": [
                    to_primitive(
                        historical.diagnostic_protected_semantic_adjudications[1]
                    )
                ],
                "writer_recall_eligibility": historical.writer_recall_eligibility.value,
                "writer_recall_violations": [
                    to_primitive(historical.writer_recall_violations[0])
                ],
            },
        }
        Draft202012Validator(
            continuous_compact_semantic_validator_draft_json_schema()
        ).validate(payload)
        result = from_mapping(
            ContinuousCompactSemanticValidatorDraftV1, payload
        ).compile(
            writer_story_text=REJECTED_STORY,
            expected_planner_beat_keys=("unused_on_reject",),
        )

        self.assertIs(result.semantic_status, ValidatorSemanticStatus.REJECTED)
        self.assertEqual(len(result.diagnostic_story_segments), 1)
        self.assertEqual(
            result.diagnostic_story_segments[0].exact_text,
            "Ted stepped inside.",
        )
        self.assertIs(
            result.writer_recall_eligibility,
            ProviderWriterRecallEligibility.ELIGIBLE,
        )
        registry = RequestEvidenceBindingRegistry(
            world_id="world:compact_rejection",
            branch_id="branch:main",
            turn_id="turn:compact_rejection",
        )
        registry.validate_validator_diagnostics(
            story_text=REJECTED_STORY,
            diagnostic_story_segments=result.diagnostic_story_segments,
            diagnostic_protected_semantic_adjudications=(
                result.diagnostic_protected_semantic_adjudications
            ),
            allowed_character_ids=("character:hana_hanezawa", "character:ted"),
            require_gap_free=False,
        )

    def test_source_grounded_public_state_is_sparse_and_noncanonical(self) -> None:
        story = "Hana smiled. She met Ted's silence with patience."
        payload, _ = compact_accepted_payload()
        decision = payload["decision"]
        material = decision["material_segments"][0]
        material.update(
            {
                "output_start": 0,
                "output_end": len("Hana smiled."),
                "segment_key": "hana_smile",
            }
        )
        silence_start = story.index("Ted's silence")
        decision["noncanonical_spans"] = [
            {
                "schema_version": "cera.provider_realization_segment.v1",
                "segment_key": "ted_silence_reference",
                "authority_disposition": "presentation_only",
                "presentation_class": "nonpersistent_spatial_phrasing",
                "kind": "narration",
                "output_start": silence_start,
                "output_end": silence_start + len("Ted's silence"),
                "roles": {
                    "schema_version": "cera.character_role_ledger.v1",
                    "action_owner_ids": [],
                    "state_owner_ids": [],
                    "speaker_ids": [],
                    "affected_ids": [],
                    "addressed_ids": [],
                    "observing_ids": [],
                    "referenced_ids": ["character:ted"],
                },
                "protected_user_source_claim_keys": [],
            }
        ]
        decision["beat_coverage"][0]["material_segment_keys"] = ["hana_smile"]
        item = decision["complete_final_sequence"]["items"][0]
        for scope in item["field_scopes"]:
            scope["story_segment_keys"] = ["hana_smile"]
        item["realized_event"] = "Hana smiled."
        item["resulting_state"] = "Hana has offered a visible smile."
        decision["protected_semantic_adjudications"] = [
            {
                "schema_version": "cera.provider_protected_semantic_adjudication.v2",
                "adjudication_key": "hana_smile_adjudication",
                "segment_key": "hana_smile",
                "output_start": 0,
                "output_end": len("Hana smiled."),
                "protected_user_id": "character:ted",
                "relation": "none",
                "npc_assertion_owner_ids": [],
                "protected_user_source_claim_keys": [],
                "protected_user_source_unit_keys": [],
            },
            {
                "schema_version": "cera.provider_protected_semantic_adjudication.v2",
                "adjudication_key": "ted_silence_source_adjudication",
                "segment_key": "ted_silence_reference",
                "output_start": silence_start,
                "output_end": silence_start + len("Ted's silence"),
                "protected_user_id": "character:ted",
                "relation": "source_grounded_public_state",
                "npc_assertion_owner_ids": [],
                "protected_user_source_claim_keys": [],
                "protected_user_source_unit_keys": ["source_state_0001"],
            },
        ]
        result = from_mapping(
            ContinuousCompactSemanticValidatorDraftV1, payload
        ).compile(
            writer_story_text=story,
            expected_planner_beat_keys=("planner_beat",),
        )

        self.assertEqual(len(result.story_segments), 1)
        self.assertEqual(len(result.presentation_realization_segments), 1)
        self.assertEqual(
            result.source_grounded_public_state_receipts[0].source_unit_key,
            "source_state_0001",
        )
        final_keys = {
            key
            for item in result.finalization_package.complete_final_sequence.items
            for key in item.story_segment_keys
        }
        self.assertNotIn("ted_silence_reference", final_keys)

    def test_schema_is_closed_sparse_and_survives_openai_projection(self) -> None:
        schema = continuous_compact_semantic_validator_draft_json_schema()
        self.assertFalse(schema["additionalProperties"])
        branches = schema["properties"]["decision"]["anyOf"]
        accepted = [
            branch
            for branch in branches
            if "material_segments" in branch["properties"]
        ]
        rejected = [
            branch
            for branch in branches
            if "offending_spans" in branch["properties"]
        ]
        self.assertEqual(len(accepted), 2)
        self.assertEqual(len(rejected), 1)
        self.assertTrue(
            all("realization_segments" not in branch["properties"] for branch in branches)
        )
        self.assertTrue(
            all(
                "diagnostic_story_segments" not in branch["properties"]
                for branch in branches
            )
        )
        projected = project_provider_output_schema(
            schema,
            ProviderSchemaDialect.OPENAI_STRUCTURED_OUTPUT_V1,
        ).provider_schema
        self.assertEqual(
            projected["properties"]["schema_version"]["const"],
            ContinuousCompactSemanticValidatorDraftV1.SCHEMA_VERSION,
        )

    def test_compact_prompt_and_route_are_opt_in(self) -> None:
        plan = planner_sequence()
        prompt, _ = build_validator_prompt(
            task_mode=ValidatorTaskMode.FINALIZE_TURN,
            package_id="package:compact_prompt",
            world_id=plan.world_id,
            branch_id=plan.branch_id,
            candidate_id="candidate:compact_prompt",
            current_user_source="Continue the scene",
            planner_sequence=plan,
            writer_story_text="Mia answered.",
            writer_mechanical_envelope={
                "candidate_id": "candidate:compact_prompt",
                "story_text_sha256": "0" * 64,
                "codepoint_count": 13,
            },
            accepted_turn_id="turn:compact_prompt",
            contract_profile="compact_v1",
        )
        self.assertIn("[COMPACT VALIDATOR REQUEST]", prompt)
        self.assertTrue(prompt.startswith(COMPACT_VALIDATOR_STABLE_INSTRUCTIONS))
        route = continuous_compact_validator_route(
            model="gpt-5.6-sol", effort="medium"
        )
        self.assertEqual(route.adapter_id, CONTINUOUS_COMPACT_VALIDATOR_ADAPTER_VERSION)
        self.assertEqual(route.prompt_version, CONTINUOUS_COMPACT_VALIDATOR_PROMPT_VERSION)
        self.assertEqual(route.maximum_output_tokens, 16_384)


if __name__ == "__main__":
    unittest.main()
