from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from jsonschema import Draft202012Validator

from cera.errors import ContractValidationError, ErrorCode
from cera.ids import IdKind, TypedId
from cera.providers import (
    CodexWorkerResult,
    CodexSDKTransport,
    ProviderSchemaDialect,
    ProviderTransportError,
    active_provider_schema_inventory,
    codex_transport_probe_output_schema,
    project_provider_output_schema,
)
from cera.reasoner.drafts import (
    CodexReasonerDraftV4,
    codex_reasoner_draft_v4_json_schema,
)
from cera.schema import from_mapping
from cera.serialization import canonical_json
from tests.test_provider_qualification import StaticCodexRunner, codex_route
import tests.test_live_shaped_pipeline as live_support


class ProviderSchemaCompatibilityTests(unittest.TestCase):
    def test_no_tool_reasoner_schema_closes_evidence_ids_to_exact_seed(self) -> None:
        allowed = TypedId(IdKind.EVIDENCE, "authorized-seed")
        schema = codex_reasoner_draft_v4_json_schema(
            allowed_evidence_ids=(allowed,)
        )
        for collection in ("participation", "character_moves", "current_beats"):
            items = (
                schema["properties"][collection]["items"]["properties"]
                ["evidence_ids"]["items"]
            )
            self.assertFalse(tuple(Draft202012Validator(items).iter_errors(str(allowed))))
            self.assertTrue(
                tuple(
                    Draft202012Validator(items).iter_errors(
                        "evidence:invented-not-authorized"
                    )
                )
            )

    def test_active_schema_inventory_is_complete_and_dialect_clean(self) -> None:
        inventory = active_provider_schema_inventory()
        self.assertEqual(
            tuple(value.name for value in inventory),
            (
                "codex_reasoner_draft_v6",
                "codex_realization_verifier_draft_v3",
                "codex_transport_probe",
                "codex_mcp_bridge_probe",
                "codex_query_plan_probe",
                "deepseek_composition_draft_v6",
            ),
        )
        for entry in inventory:
            with self.subTest(entry=entry.name):
                projection = entry.projection
                self.assertEqual(len(projection.authoritative_schema_sha256), 64)
                self.assertEqual(len(projection.provider_schema_sha256), 64)
                self.assertNotIn("$schema", projection.provider_schema)
                self.assertNotIn("oneOf", canonical_json(projection.provider_schema))
                self.assertEqual(
                    projection.strict_provider_enforced,
                    entry.dialect
                    is ProviderSchemaDialect.OPENAI_STRUCTURED_OUTPUT_V1,
                )

        codex = inventory[0].projection
        self.assertEqual(codex.transformed_one_of_count, 1)
        self.assertIn("anyOf", canonical_json(codex.provider_schema))

    def test_openai_projection_preserves_channel_owner_discrimination(self) -> None:
        source = codex_reasoner_draft_v4_json_schema()
        projected = project_provider_output_schema(
            source,
            ProviderSchemaDialect.OPENAI_STRUCTURED_OUTPUT_V1,
        ).provider_schema
        source_channel = (
            source["properties"]["adult_craft_need"]["anyOf"][0]
            ["properties"]["beat_requirements"]["items"]
            ["properties"]["channel_requirements"]["items"]
        )
        projected_channel = (
            projected["properties"]["adult_craft_need"]["anyOf"][0]
            ["properties"]["beat_requirements"]["items"]
            ["properties"]["channel_requirements"]["items"]
        )
        character_id = "character:hana"
        cases = (
            (
                {
                    "channel": "dialogue",
                    "character_id": character_id,
                    "minimum_register": "direct",
                    "semantic_concepts": ["anatomy"],
                    "lexical_concepts": [],
                },
                True,
            ),
            (
                {
                    "channel": "physiology",
                    "minimum_register": "direct",
                    "semantic_concepts": ["anatomy"],
                    "lexical_concepts": [],
                },
                True,
            ),
            (
                {
                    "channel": "dialogue",
                    "minimum_register": "direct",
                    "semantic_concepts": ["anatomy"],
                    "lexical_concepts": [],
                },
                False,
            ),
            (
                {
                    "channel": "physiology",
                    "character_id": character_id,
                    "minimum_register": "direct",
                    "semantic_concepts": ["anatomy"],
                    "lexical_concepts": [],
                },
                False,
            ),
        )
        for payload, expected in cases:
            with self.subTest(payload=payload):
                source_valid = not tuple(
                    Draft202012Validator(source_channel).iter_errors(payload)
                )
                projected_valid = not tuple(
                    Draft202012Validator(projected_channel).iter_errors(payload)
                )
                self.assertEqual(source_valid, expected)
                self.assertEqual(projected_valid, expected)

    def test_projected_schema_cannot_bypass_authoritative_typed_decoder(self) -> None:
        support = live_support.LiveShapedPipelineTests("runTest")
        support.setUp()
        self.addCleanup(support.doCleanups)
        args = support.adult_case("provider-projection-domain")
        payload = json.loads(args[4].output_text)
        projected = project_provider_output_schema(
            codex_reasoner_draft_v4_json_schema(),
            ProviderSchemaDialect.OPENAI_STRUCTURED_OUTPUT_V1,
        ).provider_schema
        payload["schema_version"] = CodexReasonerDraftV4.SCHEMA_VERSION
        payload["protected_user_source_claims"] = []
        for beat in payload["adult_craft_need"]["beat_requirements"]:
            for channel in beat["channel_requirements"]:
                channel["semantic_concepts"] = channel.pop("required_concepts")
                channel["lexical_concepts"] = []
        Draft202012Validator(projected).validate(payload)
        from_mapping(CodexReasonerDraftV4, payload)

        invalid = json.loads(json.dumps(payload))
        invalid["adult_craft_need"]["beat_requirements"][0][
            "channel_requirements"
        ][0]["character_id"] = "character:hana"
        self.assertTrue(tuple(Draft202012Validator(projected).iter_errors(invalid)))
        with self.assertRaises(ContractValidationError):
            from_mapping(CodexReasonerDraftV4, invalid)

    def test_transport_projects_before_dispatch_and_rejects_unknown_dialect_features(self) -> None:
        source = codex_reasoner_draft_v4_json_schema()
        with tempfile.TemporaryDirectory() as temporary:
            runner = StaticCodexRunner()
            transport = CodexSDKTransport(
                codex_route(), workspace=Path(temporary), runner=runner
            )
            transport.invoke("Return the probe object.", output_schema=source)
            self.assertEqual(runner.calls, 1)
            self.assertNotIn("oneOf", canonical_json(runner.last_schema))

        invalid_schema = codex_transport_probe_output_schema()
        invalid_schema["allOf"] = [{"type": "object"}]
        with tempfile.TemporaryDirectory() as temporary:
            runner = StaticCodexRunner()
            transport = CodexSDKTransport(
                codex_route(), workspace=Path(temporary), runner=runner
            )
            with self.assertRaisesRegex(ContractValidationError, "unsupported"):
                transport.invoke("Probe.", output_schema=invalid_schema)
            self.assertEqual(runner.calls, 0)

    def test_optional_object_fields_fail_provider_dialect_preflight(self) -> None:
        schema = {
            "type": "object",
            "properties": {
                "required_value": {"type": "string"},
                "silently_optional": {"type": "string"},
            },
            "required": ["required_value"],
            "additionalProperties": False,
        }
        with self.assertRaisesRegex(ContractValidationError, "require every property"):
            project_provider_output_schema(
                schema,
                ProviderSchemaDialect.OPENAI_STRUCTURED_OUTPUT_V1,
            )

    def test_malformed_provider_output_still_fails_after_projection(self) -> None:
        class MalformedCodexRunner(StaticCodexRunner):
            def run(self, *, route, prompt, output_schema, workspace, mcp_binding):
                self.calls += 1
                self.last_schema = output_schema
                return CodexWorkerResult(
                    output_text="not-json",
                    provider_request_id="private-malformed-output-id",
                    returned_model=route.model_name,
                    duration_ms=1,
                    input_tokens=1,
                    cached_input_tokens=0,
                    output_tokens=1,
                    reasoning_output_tokens=0,
                    transport_version=route.transport_version,
                    pre_registered_turn_count=1,
                )

        with tempfile.TemporaryDirectory() as temporary:
            runner = MalformedCodexRunner()
            transport = CodexSDKTransport(
                codex_route(), workspace=Path(temporary), runner=runner
            )
            with self.assertRaises(ProviderTransportError) as failure:
                transport.invoke(
                    "Return a reasoner draft.",
                    output_schema=codex_reasoner_draft_v4_json_schema(),
                )
            self.assertEqual(failure.exception.code, ErrorCode.REASONER_CONTRACT_INVALID)
            self.assertEqual(runner.calls, 1)
            self.assertNotIn("oneOf", canonical_json(runner.last_schema))


if __name__ == "__main__":
    unittest.main()
