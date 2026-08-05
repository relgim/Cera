from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from tempfile import TemporaryDirectory
import unittest

from jsonschema import Draft202012Validator, ValidationError

from cera.continuous.contracts import (
    CompactProtectedUserImplication,
    ProhibitedWriterDetailClass,
    ValidatorSemanticStatus,
    ValidatorTaskMode,
)
from cera.continuous.evidence import RequestEvidenceBindingRegistry
from cera.continuous.call_ledger import ContinuousProviderCallLedger
from cera.continuous.prompting import (
    COMPACT_VALIDATOR_STABLE_INSTRUCTIONS_V2,
    COMPACT_VALIDATOR_STABLE_INSTRUCTIONS_V3,
    CONTINUOUS_COMPACT_VALIDATOR_PROMPT_VERSION_V2,
    CONTINUOUS_COMPACT_VALIDATOR_PROMPT_VERSION_V3,
    build_validator_prompt,
)
from cera.continuous.provider import (
    CONTINUOUS_COMPACT_VALIDATOR_ADAPTER_VERSION_V2,
    CONTINUOUS_COMPACT_VALIDATOR_ADAPTER_VERSION_V3,
    CodexContinuousCompactValidatorPortV2,
    ContinuousCompactSemanticValidatorDraftV2,
    ProviderCompactRejectedTurnDecisionDraftV1,
    ProviderCompactRejectedTurnDecisionDraftV2,
    continuous_compact_semantic_validator_draft_v2_json_schema,
    continuous_compact_validator_v2_route,
    continuous_compact_validator_v3_route,
)
from cera.providers import (
    ProviderSchemaDialect,
    project_provider_output_schema,
    validate_provider_output_schema,
)
from cera.schema import from_mapping
from tests.test_compact_validator_v1 import compact_accepted_payload
from tests.test_continuous_planner_validator import sequence as planner_sequence


def violation_payload(
    story: str,
    *,
    violation_key: str = "protected_user_violation",
    predicate_owner_ids: tuple[str, ...],
    implicated_character_ids: tuple[str, ...],
    implication: str,
    claim_keys: tuple[str, ...] = (),
) -> dict:
    return {
        "schema_version": ContinuousCompactSemanticValidatorDraftV2.SCHEMA_VERSION,
        "package_id": "package:compact_violation",
        "world_id": "world:compact_violation",
        "branch_id": "branch:main",
        "decision": {
            "schema_version": ProviderCompactRejectedTurnDecisionDraftV2.SCHEMA_VERSION,
            "semantic_status": "rejected",
            "violations": [
                {
                    "schema_version": "cera.provider_compact_violation_span.v1",
                    "violation_key": violation_key,
                    "output_start": 0,
                    "output_end": len(story),
                    "violation_class": "protected_user_behavior",
                    "predicate_owner_ids": list(predicate_owner_ids),
                    "implicated_character_ids": list(implicated_character_ids),
                    "protected_user_id": "character:ted",
                    "protected_user_implication": implication,
                    "protected_user_source_claim_keys": list(claim_keys),
                }
            ],
        },
    }


def accepted_noncanonical_payload(
    story: str,
    *,
    span_text: str,
    span_key: str,
    presentation_class: str,
    kind: str,
    roles: dict[str, list[str]],
    relation: str,
    npc_owner_ids: tuple[str, ...],
    source_unit_keys: tuple[str, ...] = (),
) -> dict:
    payload, _ = compact_accepted_payload()
    payload["schema_version"] = ContinuousCompactSemanticValidatorDraftV2.SCHEMA_VERSION
    decision = payload["decision"]
    material = decision["material_segments"][0]
    material.update(
        {
            "segment_key": "hana_smile",
            "output_start": 0,
            "output_end": len("Hana smiled."),
        }
    )
    span_start = story.index(span_text)
    decision["noncanonical_spans"] = [
        {
            "schema_version": "cera.provider_realization_segment.v1",
            "segment_key": span_key,
            "authority_disposition": "presentation_only",
            "presentation_class": presentation_class,
            "kind": kind,
            "output_start": span_start,
            "output_end": span_start + len(span_text),
            "roles": {
                "schema_version": "cera.character_role_ledger.v1",
                **roles,
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
            "adjudication_key": f"{span_key}_adjudication",
            "segment_key": span_key,
            "output_start": span_start,
            "output_end": span_start + len(span_text),
            "protected_user_id": "character:ted",
            "relation": relation,
            "npc_assertion_owner_ids": list(npc_owner_ids),
            "protected_user_source_claim_keys": [],
            "protected_user_source_unit_keys": list(source_unit_keys),
        },
    ]
    return payload


class CompactRejectedViolationReceiptV1Tests(unittest.TestCase):
    def compile(self, payload: dict, story: str):
        Draft202012Validator(
            continuous_compact_semantic_validator_draft_v2_json_schema()
        ).validate(payload)
        return from_mapping(
            ContinuousCompactSemanticValidatorDraftV2, payload
        ).compile(
            writer_story_text=story,
            expected_planner_beat_keys=(
                ("planner_beat",)
                if "beat_coverage" in payload["decision"]
                else ("unused_on_reject",)
            ),
        )

    def test_relational_reciprocal_gaze_compiles_without_false_ted_role(self) -> None:
        story = "Mia caught Ted's eye."
        result = self.compile(
            violation_payload(
                story,
                violation_key="reciprocal_gaze",
                predicate_owner_ids=("character:mia_hanezawa",),
                implicated_character_ids=("character:ted",),
                implication="relational_entailment",
            ),
            story,
        )

        self.assertIs(result.semantic_status, ValidatorSemanticStatus.REJECTED)
        self.assertFalse(result.story_segments)
        self.assertFalse(result.diagnostic_story_segments)
        self.assertIsNone(result.finalization_package)
        self.assertEqual(result.reason_codes, ("protected_user_behavior",))
        receipt = result.rejected_violation_receipts[0]
        self.assertEqual(receipt.exact_text, story)
        self.assertEqual(receipt.predicate_owner_ids, ("character:mia_hanezawa",))
        self.assertEqual(receipt.implicated_character_ids, ("character:ted",))
        self.assertIs(
            receipt.protected_user_implication,
            CompactProtectedUserImplication.RELATIONAL_ENTAILMENT,
        )
        registry = RequestEvidenceBindingRegistry(
            world_id="world:compact_violation",
            branch_id="branch:main",
            turn_id="turn:compact_violation",
        )
        registry.validate_compact_violation_receipts(
            story_text=story,
            violation_receipts=result.rejected_violation_receipts,
            allowed_character_ids=("character:mia_hanezawa",),
        )
        directive = result.build_writer_recall_directive(
            rejected_candidate_id="candidate:compact_violation",
            rejected_story_text=story,
            frozen_authority_package_sha256="0" * 64,
            source_attempt_number=1,
        )
        self.assertEqual(directive.reason_codes, ("protected_user_behavior",))
        self.assertEqual(directive.offending_spans[0].exact_text, story)
        self.assertEqual(
            directive.offending_spans[0].prohibited_detail_classes,
            (ProhibitedWriterDetailClass.PROTECTED_USER_BEHAVIOR,),
        )

    def test_direct_protected_user_assertion_compiles(self) -> None:
        story = "Ted stepped inside."
        result = self.compile(
            violation_payload(
                story,
                predicate_owner_ids=("character:ted",),
                implicated_character_ids=("character:ted",),
                implication="direct_assertion",
            ),
            story,
        )
        receipt = result.rejected_violation_receipts[0]
        self.assertIs(
            receipt.protected_user_implication,
            CompactProtectedUserImplication.DIRECT_ASSERTION,
        )
        self.assertEqual(receipt.predicate_owner_ids, ("character:ted",))

    def test_npc_reference_and_address_are_not_automatic_protected_violations(self) -> None:
        empty_roles = {
            "action_owner_ids": [],
            "state_owner_ids": [],
            "speaker_ids": [],
            "affected_ids": [],
            "addressed_ids": [],
            "observing_ids": [],
            "referenced_ids": [],
        }
        fixtures = (
            (
                "Hana smiled. Mia looked toward Ted.",
                "Mia looked toward Ted.",
                "mia_looked_toward_ted",
                {
                    **empty_roles,
                    "action_owner_ids": ["character:mia_hanezawa"],
                    "referenced_ids": ["character:ted"],
                },
                "referenced_only_by_npc",
            ),
            (
                "Hana smiled. Mia addressed Ted.",
                "Mia addressed Ted.",
                "mia_addressed_ted",
                {
                    **empty_roles,
                    "action_owner_ids": ["character:mia_hanezawa"],
                    "addressed_ids": ["character:ted"],
                },
                "addressed_by_npc",
            ),
        )
        for story, span, key, roles, relation in fixtures:
            with self.subTest(story=story):
                result = self.compile(
                    accepted_noncanonical_payload(
                        story,
                        span_text=span,
                        span_key=key,
                        presentation_class="micro_action",
                        kind="action",
                        roles=roles,
                        relation=relation,
                        npc_owner_ids=("character:mia_hanezawa",),
                    ),
                    story,
                )
                self.assertIs(
                    result.semantic_status, ValidatorSemanticStatus.ACCEPTED
                )
                self.assertFalse(getattr(result, "rejected_violation_receipts", ()))
                self.assertEqual(
                    result.presentation_realization_segments[0].exact_text,
                    span,
                )

    def test_source_grounded_ted_silence_remains_noncanonical(self) -> None:
        story = "Hana smiled. She met Ted's silence with patience."
        empty_roles = {
            "action_owner_ids": [],
            "state_owner_ids": [],
            "speaker_ids": [],
            "affected_ids": [],
            "addressed_ids": [],
            "observing_ids": [],
            "referenced_ids": ["character:ted"],
        }
        result = self.compile(
            accepted_noncanonical_payload(
                story,
                span_text="Ted's silence",
                span_key="ted_silence_reference",
                presentation_class="nonpersistent_spatial_phrasing",
                kind="narration",
                roles=empty_roles,
                relation="source_grounded_public_state",
                npc_owner_ids=(),
                source_unit_keys=("source_state_0001",),
            ),
            story,
        )
        self.assertIs(result.semantic_status, ValidatorSemanticStatus.ACCEPTED)
        self.assertFalse(getattr(result, "rejected_violation_receipts", ()))
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

    def test_unused_household_lamp_is_soft_but_causal_use_remains_hard(self) -> None:
        story = "Hana smiled. A soft lamp sat on the low side table near the door."
        empty_roles = {
            "action_owner_ids": [],
            "state_owner_ids": [],
            "speaker_ids": [],
            "affected_ids": [],
            "addressed_ids": [],
            "observing_ids": [],
            "referenced_ids": [],
        }
        accepted = self.compile(
            accepted_noncanonical_payload(
                story,
                span_text="A soft lamp sat on the low side table near the door.",
                span_key="ambient_lamp_and_table",
                presentation_class="nonpersistent_atmosphere",
                kind="narration",
                roles=empty_roles,
                relation="none",
                npc_owner_ids=(),
            ),
            story,
        )
        self.assertIs(accepted.semantic_status, ValidatorSemanticStatus.ACCEPTED)
        self.assertEqual(
            accepted.presentation_realization_segments[0].exact_text,
            "A soft lamp sat on the low side table near the door.",
        )

        causal_story = "Hana picked up the lamp and carried it upstairs."
        hard_payload = violation_payload(
            causal_story,
            violation_key="causal_lamp_relocation",
            predicate_owner_ids=("character:hana_hanezawa",),
            implicated_character_ids=("character:hana_hanezawa",),
            implication="none",
        )
        violation = hard_payload["decision"]["violations"][0]
        violation["violation_class"] = "material_or_scene_state_change"
        violation["protected_user_id"] = None
        hard = self.compile(hard_payload, causal_story)
        self.assertIs(hard.semantic_status, ValidatorSemanticStatus.REJECTED)
        self.assertEqual(
            hard.reason_codes, ("material_or_scene_state_change",)
        )

    def test_direct_relational_and_claim_combinations_fail_closed(self) -> None:
        story = "Mia caught Ted's eye."
        cases = []
        direct_without_ted_owner = violation_payload(
            story,
            predicate_owner_ids=("character:mia_hanezawa",),
            implicated_character_ids=("character:ted",),
            implication="direct_assertion",
        )
        cases.append(direct_without_ted_owner)
        relational_with_ted_owner = violation_payload(
            story,
            predicate_owner_ids=("character:ted",),
            implicated_character_ids=("character:ted",),
            implication="relational_entailment",
        )
        cases.append(relational_with_ted_owner)
        ungrounded_with_claim = violation_payload(
            story,
            predicate_owner_ids=("character:mia_hanezawa",),
            implicated_character_ids=("character:ted",),
            implication="relational_entailment",
            claim_keys=("claim_ted_0001",),
        )
        cases.append(ungrounded_with_claim)

        for payload in cases:
            with self.subTest(payload=payload):
                with self.assertRaisesRegex(Exception, "compact|protected"):
                    self.compile(payload, story)

    def test_overlap_duplicate_unknown_id_enum_and_bounds_fail_closed(self) -> None:
        story = "Mia caught Ted's eye. Ted stepped inside."
        base = violation_payload(
            story,
            violation_key="reciprocal_gaze",
            predicate_owner_ids=("character:mia_hanezawa",),
            implicated_character_ids=("character:ted",),
            implication="relational_entailment",
        )
        first = base["decision"]["violations"][0]
        first["output_end"] = story.index(" Ted")

        duplicate = deepcopy(base)
        duplicate["decision"]["violations"].append(deepcopy(first))
        with self.assertRaisesRegex(Exception, "duplicated violation keys"):
            self.compile(duplicate, story)

        overlap = deepcopy(base)
        second = deepcopy(first)
        second.update(
            {
                "violation_key": "direct_entry",
                "output_start": first["output_end"] - 1,
                "output_end": len(story),
                "predicate_owner_ids": ["character:ted"],
                "protected_user_implication": "direct_assertion",
            }
        )
        overlap["decision"]["violations"].append(second)
        with self.assertRaisesRegex(Exception, "overlap"):
            self.compile(overlap, story)

        out_of_bounds = deepcopy(base)
        out_of_bounds["decision"]["violations"][0]["output_end"] = len(story) + 1
        with self.assertRaisesRegex(Exception, "out of bounds"):
            self.compile(out_of_bounds, story)

        bad_enum = deepcopy(base)
        bad_enum["decision"]["violations"][0][
            "protected_user_implication"
        ] = "reciprocal_guess"
        with self.assertRaises(ValidationError):
            Draft202012Validator(
                continuous_compact_semantic_validator_draft_v2_json_schema()
            ).validate(bad_enum)

        unknown = self.compile(base, story)
        registry = RequestEvidenceBindingRegistry(
            world_id="world:compact_violation",
            branch_id="branch:main",
            turn_id="turn:compact_violation",
        )
        tampered = deepcopy(base)
        tampered["decision"]["violations"][0]["predicate_owner_ids"] = [
            "character:unknown"
        ]
        tampered_result = self.compile(tampered, story)
        with self.assertRaisesRegex(PermissionError, "inactive predicate owner"):
            registry.validate_compact_violation_receipts(
                story_text=story,
                violation_receipts=tampered_result.rejected_violation_receipts,
                allowed_character_ids=("character:mia_hanezawa",),
            )
        self.assertFalse(unknown.finalization_package)

    def test_v2_keeps_accepted_branch_and_historical_v1_schema_separate(self) -> None:
        payload, story = compact_accepted_payload()
        payload["schema_version"] = (
            ContinuousCompactSemanticValidatorDraftV2.SCHEMA_VERSION
        )
        result = self.compile(payload, story)
        self.assertIs(result.semantic_status, ValidatorSemanticStatus.ACCEPTED)
        self.assertFalse(getattr(result, "rejected_violation_receipts", ()))

        schema = continuous_compact_semantic_validator_draft_v2_json_schema()
        branches = schema["properties"]["decision"]["anyOf"]
        v1_rejected = next(
            value
            for value in branches
            if value["properties"]["schema_version"].get("const")
            == ProviderCompactRejectedTurnDecisionDraftV1.SCHEMA_VERSION
        )
        self.assertEqual(
            v1_rejected["properties"]["semantic_status"]["enum"],
            ["inconclusive", "error"],
        )
        projected = project_provider_output_schema(
            schema,
            ProviderSchemaDialect.OPENAI_STRUCTURED_OUTPUT_V1,
        ).provider_schema
        validate_provider_output_schema(
            projected, ProviderSchemaDialect.OPENAI_STRUCTURED_OUTPUT_V1
        )
        v2_rejected = next(
            value
            for value in projected["properties"]["decision"]["anyOf"]
            if "violations" in value["properties"]
        )
        violation = v2_rejected["properties"]["violations"]["items"]["properties"]
        self.assertEqual(
            set(violation["violation_class"]["enum"]),
            {value.value for value in ProhibitedWriterDetailClass}
            - {"planner_sequence_departure", "severe_reader_quality_failure"},
        )
        self.assertEqual(
            violation["protected_user_implication"]["enum"],
            ["none", "direct_assertion", "relational_entailment"],
        )

    def test_v2_prompt_and_route_are_separate_and_do_not_change_writer(self) -> None:
        plan = planner_sequence()
        prompt, _ = build_validator_prompt(
            task_mode=ValidatorTaskMode.FINALIZE_TURN,
            package_id="package:compact_v2_prompt",
            world_id=plan.world_id,
            branch_id=plan.branch_id,
            candidate_id="candidate:compact_v2_prompt",
            current_user_source="Continue the scene",
            planner_sequence=plan,
            writer_story_text="Mia answered.",
            writer_mechanical_envelope={
                "candidate_id": "candidate:compact_v2_prompt",
                "story_text_sha256": "0" * 64,
                "codepoint_count": 13,
            },
            accepted_turn_id="turn:compact_v2_prompt",
            contract_profile="compact_v2",
        )
        self.assertTrue(prompt.startswith(COMPACT_VALIDATOR_STABLE_INSTRUCTIONS_V2))
        self.assertIn("[COMPACT VALIDATOR V2 REQUEST]", prompt)
        self.assertIn("relational_entailment", prompt)
        self.assertIn("Mia looked toward Ted", prompt)
        route = continuous_compact_validator_v2_route(
            model="gpt-5.6-sol", effort="medium"
        )
        self.assertEqual(
            route.adapter_id, CONTINUOUS_COMPACT_VALIDATOR_ADAPTER_VERSION_V2
        )
        self.assertEqual(
            route.prompt_version, CONTINUOUS_COMPACT_VALIDATOR_PROMPT_VERSION_V2
        )

    def test_v3_prompt_clarifies_incidental_props_with_new_identity(self) -> None:
        plan = planner_sequence()
        prompt, _ = build_validator_prompt(
            task_mode=ValidatorTaskMode.FINALIZE_TURN,
            package_id="package:compact_v3_prompt",
            world_id=plan.world_id,
            branch_id=plan.branch_id,
            candidate_id="candidate:compact_v3_prompt",
            current_user_source="Continue the scene",
            planner_sequence=plan,
            writer_story_text="A soft lamp sat near the door.",
            writer_mechanical_envelope={
                "candidate_id": "candidate:compact_v3_prompt",
                "story_text_sha256": "0" * 64,
                "codepoint_count": 30,
            },
            accepted_turn_id="turn:compact_v3_prompt",
            contract_profile="compact_v3",
        )
        self.assertTrue(prompt.startswith(COMPACT_VALIDATOR_STABLE_INSTRUCTIONS_V3))
        self.assertIn("[COMPACT VALIDATOR V3 REQUEST]", prompt)
        self.assertIn("soft lamp, side table", prompt)
        self.assertIn("Causal use, acquisition, transfer", prompt)
        self.assertNotEqual(
            COMPACT_VALIDATOR_STABLE_INSTRUCTIONS_V3,
            COMPACT_VALIDATOR_STABLE_INSTRUCTIONS_V2,
        )
        route = continuous_compact_validator_v3_route(
            model="gpt-5.6-sol", effort="medium"
        )
        self.assertEqual(
            route.adapter_id, CONTINUOUS_COMPACT_VALIDATOR_ADAPTER_VERSION_V3
        )
        self.assertEqual(
            route.prompt_version, CONTINUOUS_COMPACT_VALIDATOR_PROMPT_VERSION_V3
        )

    def test_v2_port_submits_and_compiles_only_the_v2_schema(self) -> None:
        story = "Mia caught Ted's eye."
        payload = violation_payload(
            story,
            violation_key="reciprocal_gaze",
            predicate_owner_ids=("character:mia_hanezawa",),
            implicated_character_ids=("character:ted",),
            implication="relational_entailment",
        )
        captured: dict[str, object] = {}

        class Transport:
            route = continuous_compact_validator_v2_route(
                model="gpt-5.6-sol", effort="medium"
            )
            runner = SimpleNamespace(provider_thread_id="thread:compact-v2-test")

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

        with TemporaryDirectory() as directory:
            port = CodexContinuousCompactValidatorPortV2(
                Transport(),
                call_ledger=ContinuousProviderCallLedger(
                    Path(directory) / "CALL_LEDGER.jsonl"
                ),
            )
            provider_result = port.validate(
                "[COMPACT VALIDATOR V2 REQUEST]\n{}",
                writer_story_text=story,
                expected_package_id=payload["package_id"],
                expected_world_id=payload["world_id"],
                expected_branch_id=payload["branch_id"],
                expected_planner_beat_keys=("unused_on_reject",),
            )
        self.assertEqual(
            captured["output_schema"]["properties"]["schema_version"]["const"],
            ContinuousCompactSemanticValidatorDraftV2.SCHEMA_VERSION,
        )
        self.assertEqual(
            provider_result.value.rejected_violation_receipts[0].violation_key,
            "reciprocal_gaze",
        )


if __name__ == "__main__":
    unittest.main()
