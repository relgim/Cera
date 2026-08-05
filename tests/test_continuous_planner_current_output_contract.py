from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
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
from cera.providers import ProviderSchemaDialect, project_provider_output_schema
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
            "cera.continuous_planner_adapter.v9",
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
