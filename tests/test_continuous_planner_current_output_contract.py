from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import re
from tempfile import TemporaryDirectory
import unittest

from jsonschema import Draft202012Validator

from cera.continuous.contracts import (
    CharacterRoleLedgerV1,
    FinalInformationVisibility,
    IngressSourceUnitKind,
    IngressSourceUnitV1,
    LOCAL_KEY_JSON_PATTERN,
    RichPlannerSequenceV1,
)
from cera.continuous.evidence import (
    RequestEvidenceBindingRegistry,
    StableAcceptedContextReferenceV1,
)
from cera.continuous.prompting import PLANNER_STABLE_INSTRUCTIONS
from cera.continuous.provider import (
    CONTINUOUS_PLANNER_ADAPTER_VERSION,
    rich_planner_sequence_json_schema,
)
from cera.errors import ContractValidationError
from cera.providers import (
    ProviderSchemaDialect,
    project_provider_output_schema,
    validate_provider_output_schema,
)
from cera.schema import from_mapping
from cera.serialization import canonical_sha256, text_sha256


FIXTURE_PATH = (
    Path(__file__).parent
    / "fixtures"
    / "continuous_lean_two_call_call2_copied_accepted_id_v1.json"
)
FIXTURE_SHA256 = "82a5ce05cbd0cc3f21203f85e2df954e94560868ec8347a8aa731e7581567309"
CURRENT_SOURCE = (
    "Continue the same doorway exchange for one further NPC-controlled sequence. "
    "Ted remains outside and has not spoken, moved, entered, or supplied a private "
    "state. Do not introduce anyone outside the active cast established by the "
    "validated first plan."
)


class ContinuousPlannerCurrentOutputContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture_bytes = FIXTURE_PATH.read_bytes()
        self.assertEqual(
            text_sha256(self.fixture_bytes.decode("utf-8")),
            FIXTURE_SHA256,
        )
        self.payload = json.loads(self.fixture_bytes)

    def test_current_output_schema_requires_provisional_true_and_null_acceptance(self) -> None:
        schema = rich_planner_sequence_json_schema()
        self.assertEqual(
            schema["properties"]["provisional"],
            {"type": "boolean", "const": True},
        )
        self.assertEqual(
            schema["properties"]["accepted_turn_id"],
            {"type": "null", "const": None},
        )
        provider_schema = project_provider_output_schema(
            schema,
            ProviderSchemaDialect.OPENAI_STRUCTURED_OUTPUT_V1,
        ).provider_schema
        self.assertEqual(
            provider_schema["properties"]["provisional"],
            {"type": "boolean", "const": True},
        )
        self.assertEqual(
            provider_schema["properties"]["accepted_turn_id"],
            {"type": "null", "const": None},
        )
        self.assertEqual(len(canonical_sha256(schema)), 64)

    def test_provider_schema_and_python_dto_both_require_cast_and_beats(self) -> None:
        source_schema = rich_planner_sequence_json_schema()
        projected_schema = project_provider_output_schema(
            source_schema,
            ProviderSchemaDialect.OPENAI_STRUCTURED_OUTPUT_V1,
        ).provider_schema
        for schema in (source_schema, projected_schema):
            self.assertEqual(
                schema["properties"]["selected_character_ids"]["minItems"],
                1,
            )
            self.assertEqual(schema["properties"]["beats"]["minItems"], 1)
            for field in ("selected_character_ids", "beats"):
                invalid = deepcopy(self.payload)
                invalid["accepted_turn_id"] = None
                invalid[field] = []
                self.assertTrue(
                    tuple(Draft202012Validator(schema).iter_errors(invalid)),
                    field,
                )

        for field in ("selected_character_ids", "beats"):
            invalid = deepcopy(self.payload)
            invalid["accepted_turn_id"] = None
            invalid[field] = []
            with self.assertRaisesRegex(
                ContractValidationError,
                "rich Planner sequence requires cast and beats",
            ):
                from_mapping(RichPlannerSequenceV1, invalid)

    def test_provider_schema_and_python_dto_require_an_assertion_owner_per_beat(self) -> None:
        source_schema = rich_planner_sequence_json_schema()
        projected_schema = project_provider_output_schema(
            source_schema,
            ProviderSchemaDialect.OPENAI_STRUCTURED_OUTPUT_V1,
        ).provider_schema
        owner_fields = ("action_owner_ids", "state_owner_ids", "speaker_ids")

        for schema in (source_schema, projected_schema):
            role_schema = schema["properties"]["beats"]["items"]["properties"][
                "roles"
            ]
            self.assertEqual(len(role_schema["anyOf"]), len(owner_fields))
            for owner_field, branch in zip(owner_fields, role_schema["anyOf"]):
                self.assertEqual(branch["type"], "object")
                self.assertFalse(branch["additionalProperties"])
                self.assertEqual(
                    tuple(branch["required"]), tuple(role_schema["required"])
                )
                self.assertEqual(
                    set(branch["properties"]), set(role_schema["properties"])
                )
                self.assertEqual(
                    branch["properties"][owner_field]["minItems"], 1
                )
                for other_field in owner_fields:
                    if other_field != owner_field:
                        self.assertNotIn(
                            "minItems", branch["properties"][other_field]
                        )

            without_owner = deepcopy(self.payload)
            without_owner["accepted_turn_id"] = None
            for field in owner_fields:
                without_owner["beats"][0]["roles"][field] = []
            self.assertTrue(
                tuple(Draft202012Validator(schema).iter_errors(without_owner))
            )

            for field in owner_fields:
                with_owner = deepcopy(without_owner)
                with_owner["beats"][0]["roles"][field] = [
                    "character:hana_hanezawa"
                ]
                self.assertFalse(
                    tuple(Draft202012Validator(schema).iter_errors(with_owner)),
                    field,
                )

        with self.assertRaisesRegex(
            ContractValidationError,
            "rich sequence beat requires an action, state, or dialogue owner",
        ):
            from_mapping(RichPlannerSequenceV1, without_owner)

    def test_zero_claim_schema_excludes_only_protected_assertion_ownership(self) -> None:
        source_schema = rich_planner_sequence_json_schema(
            protected_user_id="character:ted",
            protected_user_source_claim_keys=(),
        )
        projected_schema = project_provider_output_schema(
            source_schema,
            ProviderSchemaDialect.OPENAI_STRUCTURED_OUTPUT_V1,
        ).provider_schema
        owner_fields = ("action_owner_ids", "state_owner_ids", "speaker_ids")

        for schema in (source_schema, projected_schema):
            role_schema = schema["properties"]["beats"]["items"]["properties"][
                "roles"
            ]
            for field in owner_fields:
                pattern = role_schema["properties"][field]["items"]["pattern"]
                self.assertIsNone(re.fullmatch(pattern, "character:ted"))
                self.assertIsNotNone(
                    re.fullmatch(pattern, "character:hana_hanezawa")
                )
                for branch in role_schema["anyOf"]:
                    self.assertEqual(
                        branch["properties"][field]["items"]["pattern"],
                        pattern,
                    )
            for field in (
                "affected_ids",
                "addressed_ids",
                "observing_ids",
                "referenced_ids",
            ):
                self.assertNotIn(
                    "pattern", role_schema["properties"][field]["items"]
                )
            self.assertNotIn(
                "pattern", schema["properties"]["selected_character_ids"]["items"]
            )

        ted_owned = deepcopy(self.payload)
        ted_owned["accepted_turn_id"] = None
        ted_owned["beats"][0]["roles"]["speaker_ids"] = ["character:ted"]
        ted_owned["beats"][0]["roles"]["addressed_ids"] = []
        self.assertTrue(
            tuple(Draft202012Validator(projected_schema).iter_errors(ted_owned))
        )

        ted_addressed = deepcopy(self.payload)
        ted_addressed["accepted_turn_id"] = None
        self.assertFalse(
            tuple(Draft202012Validator(projected_schema).iter_errors(ted_addressed))
        )

    def test_exact_claim_schema_retains_python_guarded_protected_owner_surface(self) -> None:
        schema = rich_planner_sequence_json_schema(
            protected_user_id="character:ted",
            protected_user_source_claim_keys=("claim_current_dialogue_0001",),
        )
        roles = schema["properties"]["beats"]["items"]["properties"]["roles"]
        for field in ("action_owner_ids", "state_owner_ids", "speaker_ids"):
            self.assertNotIn("pattern", roles["properties"][field]["items"])

    def test_zero_claim_schema_closes_allowance_mode_and_claim_cardinality(self) -> None:
        source = rich_planner_sequence_json_schema(
            protected_user_id="character:ted",
            protected_user_source_claim_keys=(),
        )
        projected = project_provider_output_schema(
            source,
            ProviderSchemaDialect.OPENAI_STRUCTURED_OUTPUT_V1,
        ).provider_schema
        for schema in (source, projected):
            allowance = schema["properties"]["beats"]["items"]["properties"][
                "protected_user_allowance"
            ]
            modes = {
                branch["properties"]["mode"]["const"]
                for branch in allowance["anyOf"]
            }
            self.assertEqual(
                modes,
                {"none", "minimal_nonbranching_connective"},
            )

            invalid_exact = deepcopy(self.payload)
            invalid_exact["accepted_turn_id"] = None
            invalid_exact["beats"][0]["protected_user_allowance"] = {
                "mode": "exact_source_only",
                "source_binding_keys": ["binding_source_current"],
                "source_claim_keys": [],
                "explanation": "Invalid exact mode without a typed claim.",
            }
            self.assertTrue(
                tuple(Draft202012Validator(schema).iter_errors(invalid_exact))
            )

            valid_none = deepcopy(self.payload)
            valid_none["accepted_turn_id"] = None
            self.assertFalse(
                tuple(Draft202012Validator(schema).iter_errors(valid_none))
            )

            valid_minimal = deepcopy(valid_none)
            valid_minimal["beats"][0]["protected_user_allowance"] = {
                "mode": "minimal_nonbranching_connective",
                "source_binding_keys": ["binding_mechanical_current"],
                "source_claim_keys": [],
                "explanation": "Only a Python-authorized connective.",
            }
            self.assertFalse(
                tuple(Draft202012Validator(schema).iter_errors(valid_minimal))
            )
        validate_provider_output_schema(
            projected,
            ProviderSchemaDialect.OPENAI_STRUCTURED_OUTPUT_V1,
        )

    def test_exact_claim_schema_accepts_only_one_supplied_claim(self) -> None:
        claim_keys = (
            "claim_current_dialogue_0001",
            "claim_current_action_0002",
        )
        source = rich_planner_sequence_json_schema(
            protected_user_id="character:ted",
            protected_user_source_claim_keys=claim_keys,
        )
        projected = project_provider_output_schema(
            source,
            ProviderSchemaDialect.OPENAI_STRUCTURED_OUTPUT_V1,
        ).provider_schema
        for schema in (source, projected):
            allowance = schema["properties"]["beats"]["items"]["properties"][
                "protected_user_allowance"
            ]
            exact = next(
                branch
                for branch in allowance["anyOf"]
                if branch["properties"]["mode"].get("const")
                == "exact_source_only"
            )
            claims = exact["properties"]["source_claim_keys"]
            self.assertEqual(claims["minItems"], 1)
            self.assertEqual(claims["maxItems"], 1)
            self.assertEqual(tuple(claims["items"]["enum"]), claim_keys)
            self.assertEqual(
                exact["properties"]["source_binding_keys"]["minItems"], 1
            )

            def candidate(keys: list[str]) -> dict[str, object]:
                payload = deepcopy(self.payload)
                payload["accepted_turn_id"] = None
                payload["beats"][0]["protected_user_allowance"] = {
                    "mode": "exact_source_only",
                    "source_binding_keys": ["binding_source_current"],
                    "source_claim_keys": keys,
                    "explanation": "Use one exact typed source claim.",
                }
                return payload

            self.assertFalse(
                tuple(
                    Draft202012Validator(schema).iter_errors(
                        candidate([claim_keys[0]])
                    )
                )
            )
            for invalid_keys in (
                [],
                ["claim_unknown"],
                [claim_keys[0], claim_keys[1]],
            ):
                self.assertTrue(
                    tuple(
                        Draft202012Validator(schema).iter_errors(
                            candidate(invalid_keys)
                        )
                    ),
                    invalid_keys,
                )
        validate_provider_output_schema(
            projected,
            ProviderSchemaDialect.OPENAI_STRUCTURED_OUTPUT_V1,
        )

    def test_planner_schema_and_projection_constrain_every_local_key(self) -> None:
        schema = rich_planner_sequence_json_schema()
        projected = project_provider_output_schema(
            schema,
            ProviderSchemaDialect.OPENAI_STRUCTURED_OUTPUT_V1,
        ).provider_schema
        for candidate in (schema, projected):
            beat = candidate["properties"]["beats"]["items"]["properties"]
            self.assertEqual(
                beat["beat_key"]["pattern"], LOCAL_KEY_JSON_PATTERN
            )
            self.assertEqual(
                beat["source_evidence_bindings"]["items"]["pattern"],
                LOCAL_KEY_JSON_PATTERN,
            )
            allowance = beat["protected_user_allowance"]["properties"]
            for field in ("source_binding_keys", "source_claim_keys"):
                self.assertEqual(
                    allowance[field]["items"]["pattern"],
                    LOCAL_KEY_JSON_PATTERN,
                )

    def test_planner_schema_change_has_a_new_adapter_identity(self) -> None:
        self.assertEqual(
            CONTINUOUS_PLANNER_ADAPTER_VERSION,
            "cera.continuous_planner_adapter.v14",
        )

    def test_planner_schema_uses_the_compact_writer_beat_maximum(self) -> None:
        self.assertEqual(
            rich_planner_sequence_json_schema()["properties"]["beats"]["maxItems"],
            8,
        )

    def test_exact_cycle25_copied_prior_id_is_rejected_but_null_passes_unchanged(self) -> None:
        original = deepcopy(self.payload)
        with self.assertRaisesRegex(
            ContractValidationError,
            "current Planner output accepted_turn_id must be null",
        ):
            from_mapping(RichPlannerSequenceV1, self.payload)
        self.assertEqual(self.payload, original)

        corrected = deepcopy(self.payload)
        corrected["accepted_turn_id"] = None
        sequence = from_mapping(RichPlannerSequenceV1, corrected)

        self.assertTrue(sequence.provisional)
        self.assertIsNone(sequence.accepted_turn_id)
        self.assertEqual(len(sequence.beats), 1)
        self.assertEqual(
            sequence.beats[0].beat_key,
            "beat_001_hana_clarifies_verification",
        )
        self.assertEqual(
            corrected["beats"],
            self.payload["beats"],
            "nulling the current-output acceptance field must be the only change",
        )

        registry = RequestEvidenceBindingRegistry(
            world_id=sequence.world_id,
            branch_id=sequence.branch_id,
            turn_id="probe-turn-002",
        )
        source = registry.allocate_current_source(
            source_identity="current_user_source:probe-turn-002",
            source_text=CURRENT_SOURCE,
            protected_user_allowance_scope=(
                "exact supplied source plus minimal nonbranching connective"
            ),
            source_units=(
                IngressSourceUnitV1(
                    schema_version=IngressSourceUnitV1.SCHEMA_VERSION,
                    source_unit_key="probe_source_unit_002",
                    kind=IngressSourceUnitKind.INSTRUCTION,
                    source_start=0,
                    source_end=len(CURRENT_SOURCE),
                    exact_text=CURRENT_SOURCE,
                    actor_id=None,
                    speaker_id=None,
                    classification_basis="explicit_ingress_instruction",
                ),
            ),
        )
        self.assertEqual(
            source.binding_key,
            "binding_source_ba28d9267fd579987f48",
        )
        prior_value = "Hana's doorway verification remains unresolved."
        reference = StableAcceptedContextReferenceV1(
            schema_version=StableAcceptedContextReferenceV1.SCHEMA_VERSION,
            reference_key="binding_accepted_ref_5c962904f5e8e214ac6b",
            world_id=sequence.world_id,
            branch_id=sequence.branch_id,
            planner_session_id_sha256="1" * 64,
            provider_thread_sha256="2" * 64,
            accepted_turn_id="probe-provisional-turn-001",
            scene_id=sequence.scene_id,
            accepted_ancestry_sha256="3" * 64,
            accepted_envelope_sha256="4" * 64,
            accepted_pair_sha256="5" * 64,
            accepted_event_sha256="6" * 64,
            acceptance_receipt_sha256="7" * 64,
            injection_receipt_sha256="8" * 64,
            session_snapshot_sha256="9" * 64,
            synchronization_receipt_sha256="a" * 64,
            source_item_key="beat_003_hana_response",
            field_name="resulting_state",
            field_value=prior_value,
            field_value_sha256=text_sha256(prior_value),
            visibility=FinalInformationVisibility.PUBLIC,
            knowledge_owner_id=None,
            roles=CharacterRoleLedgerV1(
                speaker_ids=("character:hana_hanezawa",),
                addressed_ids=("character:ted",),
            ),
            protected_user_source_claim_keys=(),
        )
        registry.allocate_stable_accepted_context_reference(
            reference,
            current_provider_thread_sha256=reference.provider_thread_sha256,
            current_accepted_ancestry_sha256=reference.accepted_ancestry_sha256,
        )
        with TemporaryDirectory(prefix="cera-cycle25-current-output-") as directory:
            registry.validate_sequence(sequence, branch_root=Path(directory))

    def test_prior_identity_semantics_are_explicit_and_noncopyable(self) -> None:
        self.assertIn("prior/reference identity", PLANNER_STABLE_INSTRUCTIONS)
        self.assertIn(
            "current output accepted_turn_id must be null",
            PLANNER_STABLE_INSTRUCTIONS,
        )

    def test_npc_and_private_beats_require_exact_owner_active_bindings(self) -> None:
        self.assertIn(
            "Every beat with an NPC assertion owner must cite at least one exact ACTIVE world-record binding for that NPC",
            PLANNER_STABLE_INSTRUCTIONS,
        )
        self.assertIn(
            "Every private-state beat must cite the exact character-private ACTIVE binding",
            PLANNER_STABLE_INSTRUCTIONS,
        )
        self.assertIn(
            "emit separate beats and cite each owner's ACTIVE binding on that owner's beat",
            PLANNER_STABLE_INSTRUCTIONS,
        )


if __name__ == "__main__":
    unittest.main()
