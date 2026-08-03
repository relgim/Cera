from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from cera.continuous.call_ledger import (
    ContinuousProviderCallLedger,
    ProviderCallState,
    completed_provider_failure_evidence,
)
from cera.continuous.contracts import (
    FrozenContinuousIngressFixtureV1,
    IngressSourceUnitKind,
    IngressSourceUnitV1,
    ProtectedUserAllowanceMode,
    RichPlannerSequenceV1,
)
from cera.continuous.evidence import (
    RequestEvidenceBindingRegistry,
    bind_character_summary_envelopes,
)
from cera.continuous.ingress import ContinuousIngressAuthorityStore
from cera.continuous.prompting import (
    CONTINUOUS_PLANNER_PROMPT_VERSION,
    PLANNER_STABLE_INSTRUCTIONS,
)
from cera.continuous.world import ContinuousWorldStore
from cera.errors import ContractValidationError
from cera.schema import from_mapping
from cera.serialization import text_sha256
from scripts import run_continuous_planner_validator_job4 as stable


FIXTURE_PATH = (
    Path(__file__).parent
    / "fixtures"
    / "continuous_lean_two_call_call1_recovered_v1.json"
)
RECOVERED_OUTPUT_SHA256 = (
    "26fd671219eba12851d02bb8cc26c12b07070204d1ab83807d40759b7a2fd6f8"
)
SOURCE_BINDING = "binding_source_fded888c3c89115141e6"
SOURCE_CLAIM = "claim_source_d862f69cdcc8668b65a5"
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
WORLD_ID = "hanezawa-two-call-concept-v1"
BRANCH_ID = "probe-main"
TURN_ID = "probe-turn-001"
CURRENT_SOURCE = "Hello, my name is Ted. Is this the Hanezawa household?"
CHARACTER_NAMES = ("hana", "sakura", "mia", "enne", "tomi", "aoi", "yuuni")


class ContinuousProtectedIngressRegressionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture_bytes = FIXTURE_PATH.read_bytes()
        self.assertEqual(text_sha256(self.fixture_bytes.decode("utf-8")), RECOVERED_OUTPUT_SHA256)
        self.recovered = json.loads(self.fixture_bytes)

    def _strict_registry(self, root: Path) -> tuple[RequestEvidenceBindingRegistry, Path]:
        world = ContinuousWorldStore(root / "worlds")
        stable.seed_world(
            world,
            REPOSITORY_ROOT,
            world_id=WORLD_ID,
            branch_id=BRANCH_ID,
        )
        source_unit = IngressSourceUnitV1(
            schema_version=IngressSourceUnitV1.SCHEMA_VERSION,
            source_unit_key="probe_source_unit_001",
            kind=IngressSourceUnitKind.DIALOGUE,
            source_start=0,
            source_end=len(CURRENT_SOURCE),
            exact_text=CURRENT_SOURCE,
            actor_id=None,
            speaker_id="character:ted",
            classification_basis="explicit_ingress_speaker",
        )
        fixture = FrozenContinuousIngressFixtureV1(
            schema_version=FrozenContinuousIngressFixtureV1.SCHEMA_VERSION,
            fixture_id="cera.fixture.two_call_concept.turn_1",
            fixture_schema_id="cera.fixture_registry.two_call_concept.v1",
            world_id=WORLD_ID,
            branch_id=BRANCH_ID,
            session_id="session:two-call-concept-v1",
            request_id="request:probe-turn-001",
            turn_id=TURN_ID,
            idempotency_key_sha256=text_sha256("two-call-concept-turn-001"),
            raw_source=CURRENT_SOURCE,
            protected_user_id="character:ted",
            source_units=(source_unit,),
        )
        authority = ContinuousIngressAuthorityStore(
            root / "ingress",
            fixture_registry=(fixture,),
        )
        ingress = authority.issue_frozen_fixture(
            fixture_id=fixture.fixture_id,
            idempotency_key="two-call-concept-turn-001",
        )
        registry = RequestEvidenceBindingRegistry(
            world_id=WORLD_ID,
            branch_id=BRANCH_ID,
            turn_id=TURN_ID,
        )
        current = registry.allocate_current_source(
            source_identity=f"current_user_source:{TURN_ID}",
            source_text=CURRENT_SOURCE,
            protected_user_allowance_scope=(
                "exact supplied source plus minimal nonbranching connective"
            ),
            source_units=ingress.source_units,
        )
        registry.allocate_mechanical_connective_allowance()
        summaries = tuple(
            stable.source_character_summary(
                REPOSITORY_ROOT,
                name,
                world=world,
                world_file_revision=stable.read_world_revision(
                    world,
                    name.capitalize(),
                    world_id=WORLD_ID,
                    branch_id=BRANCH_ID,
                ),
                world_id=WORLD_ID,
                branch_id=BRANCH_ID,
            )
            for name in CHARACTER_NAMES
        )
        bind_character_summary_envelopes(
            registry=registry,
            branch_root=world.branch_root(WORLD_ID, BRANCH_ID),
            summaries=summaries,
        )
        self.assertEqual(current.binding_key, SOURCE_BINDING)
        self.assertEqual(
            registry.protected_user_claim_manifest()[0]["claim_key"],
            SOURCE_CLAIM,
        )
        return registry, world.branch_root(WORLD_ID, BRANCH_ID)

    def test_exact_recovered_three_beat_output_remains_strictly_rejected(self) -> None:
        before = deepcopy(self.recovered)
        with self.assertRaisesRegex(
            ContractValidationError,
            "rich sequence beat requires pressure and realization space",
        ):
            from_mapping(RichPlannerSequenceV1, self.recovered)
        self.assertEqual(self.recovered, before)
        self.assertEqual(self.recovered["beats"][0]["beat_key"], "beat_001_ingress_dialogue")
        self.assertEqual(self.recovered["beats"][0]["relevant_character_pressures"], [])

    def test_expected_npc_first_structure_passes_without_weakening_contracts(self) -> None:
        corrected = deepcopy(self.recovered)
        corrected["beats"] = corrected["beats"][1:]
        sequence = from_mapping(RichPlannerSequenceV1, corrected)

        self.assertEqual(len(sequence.beats), 2)
        self.assertEqual(sequence.beats[0].beat_key, "beat_002_hana_appraisal")
        self.assertEqual(sequence.beats[1].beat_key, "beat_003_hana_response")
        self.assertNotIn(
            "character:ted",
            {
                owner
                for beat in sequence.beats
                for owner in beat.roles.assertion_owner_ids
            },
        )
        for beat in sequence.beats:
            self.assertEqual(
                beat.protected_user_allowance.mode,
                ProtectedUserAllowanceMode.EXACT_SOURCE_ONLY,
            )
            self.assertIn(SOURCE_BINDING, beat.source_evidence_bindings)
            self.assertEqual(
                beat.protected_user_allowance.source_binding_keys,
                (SOURCE_BINDING,),
            )
            self.assertEqual(
                beat.protected_user_allowance.source_claim_keys,
                (SOURCE_CLAIM,),
            )
            self.assertTrue(beat.relevant_character_pressures)
            self.assertTrue(beat.deepseek_realization_space)

        with TemporaryDirectory(prefix="cera-protected-ingress-registry-") as directory:
            registry, branch_root = self._strict_registry(Path(directory))
            registry.validate_sequence(sequence, branch_root=branch_root)

        self.assertEqual(
            CONTINUOUS_PLANNER_PROMPT_VERSION,
            "cera.continuous_planner_prompt.v15",
        )
        self.assertIn(
            "begin the rich sequence with the first NPC-controlled causal consequence",
            PLANNER_STABLE_INSTRUCTIONS,
        )
        self.assertIn(
            "Do not emit a standalone Ted-owned rich beat",
            PLANNER_STABLE_INSTRUCTIONS,
        )
        self.assertIn(
            "not permission to delete, normalize, or repair provider output",
            PLANNER_STABLE_INSTRUCTIONS,
        )

    def test_post_provider_validation_failure_retains_primary_call_evidence(self) -> None:
        receipt = {
            "output_sha256": text_sha256('{"invalid":true}'),
            "input_tokens": 120,
            "cached_input_tokens": 80,
            "output_tokens": 14,
            "reasoning_output_tokens": 9,
            "external_provider_calls": 1,
        }
        telemetry = {
            "cumulative_input_tokens": 120,
            "cumulative_cached_input_tokens": 80,
            "cumulative_uncached_input_tokens": 40,
            "cumulative_output_tokens": 14,
            "cumulative_reasoning_tokens": 9,
        }
        raw = SimpleNamespace(
            output_text='{"invalid":true}',
            receipt=receipt,
            operation_telemetry=telemetry,
            tool_names=(),
            tool_server_names=(),
        )

        with TemporaryDirectory(prefix="cera-post-provider-evidence-") as directory:
            ledger = ContinuousProviderCallLedger(
                Path(directory) / "PROVIDER_CALL_LEDGER.jsonl",
                maximum_calls=1,
            )
            with self.assertRaisesRegex(
                ContractValidationError,
                "strict semantic rejection",
            ) as raised:
                ledger.execute(
                    owner="planner",
                    operation="plan_0001",
                    route="codex_reasoner_gpt-5.6-sol_medium",
                    model="gpt-5.6-sol",
                    effort="medium",
                    stored_thread_sha256="7" * 64,
                    dispatch_with_stage_markers=lambda markers: (
                        markers.mark_worker_started(),
                        markers.mark_worker_preflight(),
                        markers.mark_transport_invoked(),
                        raw,
                    )[-1],
                    finalize=lambda _raw: (_ for _ in ()).throw(
                        ContractValidationError("strict semantic rejection")
                    ),
                )

            failure = raised.exception
            self.assertEqual(failure.external_provider_calls_observed, 1)
            self.assertIs(failure.provider_call_receipt, receipt)
            self.assertIs(failure.operation_telemetry, telemetry)
            self.assertIs(failure.completed_provider_result, raw)
            self.assertEqual(failure.provider_output_sha256, receipt["output_sha256"])
            self.assertEqual(
                failure.provider_output_bytes,
                len(raw.output_text.encode("utf-8")),
            )
            primary = completed_provider_failure_evidence(failure)
            self.assertEqual(primary["external_provider_calls_observed"], 1)
            self.assertEqual(primary["provider_receipt"], receipt)
            self.assertEqual(primary["operation_telemetry"], telemetry)
            self.assertEqual(primary["input_tokens"], 120)
            self.assertEqual(primary["cached_input_tokens"], 80)
            self.assertEqual(primary["uncached_input_tokens"], 40)
            self.assertEqual(primary["output_tokens"], 14)
            self.assertEqual(primary["reasoning_tokens"], 9)
            self.assertEqual(primary["provider_output_sha256"], receipt["output_sha256"])
            self.assertEqual(
                primary["provider_output_bytes"],
                len(raw.output_text.encode("utf-8")),
            )
            self.assertTrue(primary["completed_result_retained_in_memory"])
            self.assertFalse(primary["raw_output_in_primary_receipt"])
            self.assertEqual(ledger.dispatched_call_count, 1)
            self.assertEqual(
                tuple(event["state"] for event in ledger.events),
                (
                    ProviderCallState.PREPARED.value,
                    ProviderCallState.WORKER_STARTED.value,
                    ProviderCallState.WORKER_PREFLIGHT.value,
                    ProviderCallState.TRANSPORT_INVOKED.value,
                    ProviderCallState.PROVIDER_COMPLETED.value,
                    ProviderCallState.POST_VALIDATION_FAILED.value,
                ),
            )
            self.assertIsNotNone(ledger.events[-1]["provider_receipt_sha256"])
            self.assertIsNotNone(ledger.events[-1]["operation_telemetry_sha256"])


if __name__ == "__main__":
    unittest.main()
