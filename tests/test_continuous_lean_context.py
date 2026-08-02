from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest import mock

from cera.continuous.evidence import (
    EvidenceVisibility,
    RequestEvidenceBindingRegistry,
    StableAcceptedContextReferenceStore,
    ValidatorCitedAcceptedEvidenceV1,
    build_character_summary_envelope,
    build_stable_accepted_context_references,
    project_final_sequence_facts,
    rebind_stable_accepted_context_references_for_reconstruction,
    stable_reference_descriptors_for_reconstruction_target,
    validate_stable_accepted_context_reference_facts,
)
from cera.continuous.runtime import (
    ContinuousShadowTurnCoordinator,
    ContinuousTurnRequestV1,
)
from cera.continuous.packets import (
    ContinuousPlannerPacketKind,
    ContinuousPlannerTurnPacketV1,
    build_continuous_planner_turn_packet,
)
from cera.continuous.prompting import (
    PLANNER_STABLE_INSTRUCTIONS,
    build_continuous_composer_prompt,
    build_planner_turn_prompt,
    build_validator_prompt,
    planner_base_instruction_usage,
)
from cera.continuous.sessions import (
    ContinuousBranchForkReceiptV2,
    ContinuousReconstructionAcceptedTurnV1,
    ContinuousSessionCoordinator,
    ContinuousSessionInitializationKind,
    ContinuousSessionInitializationPacketV1,
    ContinuousSessionReconstructionBundleV1,
    ContinuousSessionRole,
    InMemoryContinuousStoredSessionPort,
    PlannerContextMode,
)
from cera.errors import ContractValidationError, StateConflictError
from cera.serialization import canonical_sha256, text_sha256, to_primitive
from cera.creator_review.models import CreatorReviewAction

from tests.test_continuous_planner_validator import (
    accepted_sequence,
    branch_receipt,
    compatibility,
    sequence,
)
from tests.test_continuous_corrections import _QueueStage, _seed_character
from tests.test_continuous_world import (
    character_summary,
    composer_draft,
    ingress_reference,
    make_ingress_authority,
    package,
    rich_sequence,
    session_compatibility,
)
from cera.continuous.world import ContinuousWorldStore
from cera.continuous.contracts import (
    AcceptedFinalSequenceEnvelopeV1,
    ValidatorTaskMode,
)


def accepted_envelope() -> AcceptedFinalSequenceEnvelopeV1:
    return AcceptedFinalSequenceEnvelopeV1(
        schema_version=AcceptedFinalSequenceEnvelopeV1.SCHEMA_VERSION,
        accepted_turn_id="turn:001",
        user_message="Hello.",
        complete_final_sequence=accepted_sequence(),
        acceptance_receipt_sha256=text_sha256("accepted-turn-001"),
    )


def first_planner_packet(
    *,
    turn_id: str = "turn:001",
    user_message: str = "Continue.",
) -> ContinuousPlannerTurnPacketV1:
    registry = RequestEvidenceBindingRegistry(
        world_id="world:hanezawa_test",
        branch_id="branch:main",
        turn_id=turn_id,
    )
    source = registry.allocate_current_source(
        source_identity=f"current_user_source:{turn_id}",
        source_text=user_message,
        protected_user_allowance_scope="exact supplied source",
        source_units=(),
    )
    mechanical = registry.allocate_mechanical_connective_allowance()
    return build_continuous_planner_turn_packet(
        world_id="world:hanezawa_test",
        branch_id="branch:main",
        session_id="session:one",
        request_id=f"request:{turn_id}",
        scene_id="scene:arrival",
        turn_id=turn_id,
        context_mode="lean_continuous",
        current_user_message=user_message,
        request_local_evidence_bindings=registry.prompt_manifest(),
        current_source_binding_key=source.binding_key,
        mechanical_connective_binding_key=mechanical.binding_key,
        protected_user_source_claims=(),
        ingress_source_units=(),
        ingress_custody={
            "receipt_id": f"ingress_receipt:{turn_id}",
            "receipt_sha256": text_sha256(f"receipt:{turn_id}"),
            "raw_source_sha256": text_sha256(user_message),
            "protected_user_id": "character:ted",
            "source_unit_keys": (),
        },
        character_summary_bindings=(),
        compact_accepted_head_receipt=None,
        stable_accepted_reference_keys=(),
        projection_assisted_trigger=None,
        projection_reference_keys=(),
        projection_facts=(),
        scene_change_envelope_sha256=None,
    )


class ContinuousLeanContextTests(unittest.TestCase):
    def test_context_modes_are_closed_and_lean_prompt_has_no_stable_prefix(self) -> None:
        current = compatibility(ContinuousSessionRole.PLANNER)
        self.assertIs(
            current.default_context_mode,
            PlannerContextMode.LEAN_CONTINUOUS,
        )
        with self.assertRaisesRegex(
            ContractValidationError, "default context mode"
        ):
            replace(
                current,
                default_context_mode=PlannerContextMode.PROJECTION_ASSISTED,
            )

        packet = first_planner_packet()
        prompt, usage = build_planner_turn_prompt(current_packet=packet)
        self.assertNotIn(PLANNER_STABLE_INSTRUCTIONS, prompt)
        self.assertIn('"context_mode":"lean_continuous"', prompt)
        self.assertNotIn(
            "stable_instructions", tuple(value.component for value in usage)
        )
        base = planner_base_instruction_usage()
        self.assertEqual(base.byte_count, len(PLANNER_STABLE_INSTRUCTIONS.encode("utf-8")))
        with self.assertRaisesRegex(TypeError, "validated continuous Planner packet"):
            build_planner_turn_prompt(
                current_packet={"turn_id": "turn:002"},  # type: ignore[arg-type]
            )
        with self.assertRaises(TypeError):
            build_planner_turn_prompt(
                current_packet=packet,
                context_mode=PlannerContextMode.LEAN_CONTINUOUS,  # type: ignore[call-arg]
            )

    def test_mode_specific_planner_packets_are_closed_and_replay_free(self) -> None:
        facts = tuple(
            fact
            for item in accepted_sequence().items
            for fact in project_final_sequence_facts(item)
        )
        receipt, references = build_stable_accepted_context_references(
            world_id="world:hanezawa_test",
            branch_id="branch:main",
            scene_id="scene:arrival",
            planner_session_id="planner-session",
            provider_thread_sha256=text_sha256("planner-thread"),
            accepted_turn_ids=("turn:001",),
            accepted_turn_id="turn:001",
            accepted_envelope_sha256=accepted_envelope().envelope_sha256,
            accepted_pair_sha256=text_sha256("pair"),
            accepted_event_sha256=text_sha256("event"),
            acceptance_receipt_sha256=text_sha256("acceptance"),
            injection_receipt_sha256=text_sha256("injection"),
            session_snapshot_sha256=text_sha256("snapshot"),
            synchronization_receipt_sha256=text_sha256("synchronization"),
            facts=facts,
        )
        registry = RequestEvidenceBindingRegistry(
            world_id="world:hanezawa_test",
            branch_id="branch:main",
            turn_id="turn:002",
        )
        source = registry.allocate_current_source(
            source_identity="current_user_source:turn:002",
            source_text="Continue.",
            protected_user_allowance_scope="exact supplied source",
            source_units=(),
        )
        mechanical = registry.allocate_mechanical_connective_allowance()
        common = {
            "world_id": "world:hanezawa_test",
            "branch_id": "branch:main",
            "session_id": "session:one",
            "request_id": "request:one",
            "scene_id": "scene:arrival",
            "turn_id": "turn:002",
            "context_mode": "lean_continuous",
            "current_user_message": "Continue.",
            "request_local_evidence_bindings": registry.prompt_manifest(),
            "current_source_binding_key": source.binding_key,
            "mechanical_connective_binding_key": mechanical.binding_key,
            "protected_user_source_claims": (),
            "ingress_source_units": (),
            "ingress_custody": {
                "receipt_id": "ingress_receipt:one",
                "receipt_sha256": text_sha256("receipt"),
                "raw_source_sha256": text_sha256("Continue."),
                "protected_user_id": "character:ted",
                "source_unit_keys": (),
            },
            "character_summary_bindings": (),
        }
        first = build_continuous_planner_turn_packet(
            **common,
            compact_accepted_head_receipt=None,
            stable_accepted_reference_keys=(),
            projection_assisted_trigger=None,
            projection_reference_keys=(),
            projection_facts=(),
            scene_change_envelope_sha256=None,
        )
        for reference in references:
            registry.allocate_stable_accepted_context_reference(
                reference,
                current_provider_thread_sha256=receipt.provider_thread_sha256,
                current_accepted_ancestry_sha256=receipt.accepted_ancestry_sha256,
            )
        continuation_common = {
            **common,
            "request_local_evidence_bindings": registry.prompt_manifest(),
        }
        lean = build_continuous_planner_turn_packet(
            **continuation_common,
            compact_accepted_head_receipt=receipt,
            stable_accepted_reference_keys=receipt.stable_reference_keys,
            projection_assisted_trigger=None,
            projection_reference_keys=(),
            projection_facts=(),
            scene_change_envelope_sha256=None,
        )
        key = references[0].reference_key
        projection = build_continuous_planner_turn_packet(
            **{**continuation_common, "context_mode": "projection_assisted"},
            compact_accepted_head_receipt=receipt,
            stable_accepted_reference_keys=receipt.stable_reference_keys,
            projection_assisted_trigger=(
                "demonstrated_continuity_defect:missing_state"
            ),
            projection_reference_keys=(key,),
            projection_facts=(
                {
                    "reference_key": key,
                    "accepted_turn_id": references[0].accepted_turn_id,
                    "source_item_key": references[0].source_item_key,
                    "field_name": references[0].field_name,
                    "field_value": references[0].field_value,
                    "visibility": references[0].visibility.value,
                    "knowledge_owner_id": references[0].knowledge_owner_id,
                    "roles": to_primitive(references[0].roles),
                },
            ),
            scene_change_envelope_sha256=None,
        )
        scene_change = build_continuous_planner_turn_packet(
            **continuation_common,
            compact_accepted_head_receipt=receipt,
            stable_accepted_reference_keys=receipt.stable_reference_keys,
            projection_assisted_trigger=None,
            projection_reference_keys=(),
            projection_facts=(),
            scene_change_envelope_sha256=text_sha256("scene change"),
        )
        self.assertEqual(
            tuple(value.packet_kind for value in (first, lean, projection, scene_change)),
            (
                ContinuousPlannerPacketKind.FIRST_TURN_INITIALIZATION,
                ContinuousPlannerPacketKind.LEAN_CONTINUATION,
                ContinuousPlannerPacketKind.PROJECTION_ASSISTED,
                ContinuousPlannerPacketKind.SCENE_CHANGE,
            ),
        )
        for packet in (first, lean, projection, scene_change):
            payload = packet.to_payload()
            self.assertEqual(
                set(payload), packet.ALLOWED_FIELDS_BY_KIND[packet.packet_kind]
            )
            self.assertEqual(canonical_sha256(payload), packet.packet_sha256)
            self.assertEqual(
                len(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()),
                packet.packet_bytes,
            )
        assisted, _ = build_planner_turn_prompt(current_packet=projection)
        mode = json.loads(
            assisted.split("[PLANNER CONTEXT MODE]\n", 1)[1].split(
                "\n\n[CHARACTER CARD SUMMARIES", 1
            )[0]
        )
        self.assertEqual(mode["context_mode"], "projection_assisted")
        self.assertEqual(mode["projection_reference_keys"], [key])
        with self.assertRaisesRegex(ValueError, "supplied together"):
            build_planner_turn_prompt(current_packet=scene_change)
        lean_text = json.dumps(lean.to_payload(), sort_keys=True)
        for forbidden in (
            "previous_accepted_pairs",
            "prior_complete_sequences",
            "accepted_session_projections",
            "previous_prose",
            "full_history",
            "stable_instructions",
            "untyped_caller_data",
        ):
            self.assertNotIn(forbidden, lean_text)
        forbidden_inputs = (
            "previous_accepted_pairs",
            "prior_complete_sequences",
            "accepted_session_facts",
            "accepted_session_projections",
            "previous_prose",
            "full_history",
            "unchanged_character_summaries",
            "stable_instructions",
            "foreign_reference_payloads",
            "untyped_caller_data",
            "reconstruction_packet",
            "fork_packet",
            "scene_change_context",
            "projection_assistance_payload",
        )
        for forbidden in forbidden_inputs:
            with self.subTest(forbidden_top_level=forbidden):
                with self.assertRaises(TypeError):
                    build_continuous_planner_turn_packet(
                        **continuation_common,
                        compact_accepted_head_receipt=receipt,
                        stable_accepted_reference_keys=(
                            receipt.stable_reference_keys
                        ),
                        projection_assisted_trigger=None,
                        projection_reference_keys=(),
                        projection_facts=(),
                        scene_change_envelope_sha256=None,
                        **{forbidden: {"smuggled": True}},
                    )
        current_manifest = tuple(common["request_local_evidence_bindings"])
        for forbidden in forbidden_inputs:
            nested_manifest = (
                {
                    **current_manifest[0],
                    "source_identity": {forbidden: {"smuggled": True}},
                },
                *current_manifest[1:],
            )
            with self.subTest(forbidden_nested=forbidden):
                with self.assertRaisesRegex(
                    ContractValidationError, "contains nested caller data"
                ):
                    build_continuous_planner_turn_packet(
                        **{
                            **common,
                            "request_local_evidence_bindings": nested_manifest,
                        },
                        compact_accepted_head_receipt=None,
                        stable_accepted_reference_keys=(),
                        projection_assisted_trigger=None,
                        projection_reference_keys=(),
                        projection_facts=(),
                        scene_change_envelope_sha256=None,
                    )
        with self.assertRaisesRegex(
            ContractValidationError, "request evidence binding schema is open"
        ):
            build_continuous_planner_turn_packet(
                **{
                    **common,
                    "request_local_evidence_bindings": (
                        {
                            "caller_label": "harmless",
                            "nested": {
                                "previous_prose": "smuggled history",
                                "prior_complete_sequences": (),
                            },
                        },
                    ),
                },
                compact_accepted_head_receipt=receipt,
                stable_accepted_reference_keys=receipt.stable_reference_keys,
                projection_assisted_trigger=None,
                projection_reference_keys=(),
                projection_facts=(),
                scene_change_envelope_sha256=None,
            )
        with self.assertRaisesRegex(ContractValidationError, "lean packet contract"):
            build_continuous_planner_turn_packet(
                **{**continuation_common, "context_mode": "projection_assisted"},
                compact_accepted_head_receipt=receipt,
                stable_accepted_reference_keys=receipt.stable_reference_keys,
                projection_assisted_trigger=None,
                projection_reference_keys=(),
                projection_facts=(),
                scene_change_envelope_sha256=None,
            )
        with self.assertRaisesRegex(
            ContractValidationError, "projection-assisted packet contract"
        ):
            build_continuous_planner_turn_packet(
                **continuation_common,
                compact_accepted_head_receipt=receipt,
                stable_accepted_reference_keys=receipt.stable_reference_keys,
                projection_assisted_trigger="demonstrated defect",
                projection_reference_keys=("binding_accepted_ref_" + "f" * 20,),
                projection_facts=(
                    {
                        "reference_key": "binding_accepted_ref_" + "f" * 20,
                        "accepted_turn_id": "turn:001",
                        "source_item_key": "foreign",
                        "field_name": "resulting_state",
                        "field_value": "foreign",
                        "visibility": "public",
                        "knowledge_owner_id": None,
                        "roles": to_primitive(references[0].roles),
                    },
                ),
                scene_change_envelope_sha256=None,
            )
        smuggled_roles = {
            **to_primitive(references[0].roles),
            "action_owner_ids": {
                "full_history": "nested under an allowed roles label"
            },
        }
        with self.assertRaisesRegex(
            ContractValidationError, "projection roles contain caller data"
        ):
            build_continuous_planner_turn_packet(
                **{**continuation_common, "context_mode": "projection_assisted"},
                compact_accepted_head_receipt=receipt,
                stable_accepted_reference_keys=receipt.stable_reference_keys,
                projection_assisted_trigger="demonstrated defect",
                projection_reference_keys=(key,),
                projection_facts=(
                    {
                        "reference_key": key,
                        "accepted_turn_id": references[0].accepted_turn_id,
                        "source_item_key": references[0].source_item_key,
                        "field_name": references[0].field_name,
                        "field_value": references[0].field_value,
                        "visibility": references[0].visibility.value,
                        "knowledge_owner_id": references[0].knowledge_owner_id,
                        "roles": smuggled_roles,
                    },
                ),
                scene_change_envelope_sha256=None,
            )
        with self.assertRaises(TypeError):
            ContinuousTurnRequestV1(
                world_id="world:hanezawa_test",
                branch_id="branch:main",
                session_id="session:one",
                request_id="request:one",
                idempotency_key_sha256=text_sha256("idempotency"),
                scene_id="scene:arrival",
                turn_id="turn:002",
                user_message="Continue.",
                ingress_receipt_id="ingress_receipt:one",
                ingress_receipt_sha256=text_sha256("receipt"),
                current_authority_packet={"untyped_caller_data": True},
            )

    def test_character_delivery_is_thread_local_but_composer_context_is_not_suppressed(self) -> None:
        port = InMemoryContinuousStoredSessionPort()
        planner = ContinuousSessionCoordinator(
            compatibility(ContinuousSessionRole.PLANNER), port
        )
        planner.install_base_instructions(PLANNER_STABLE_INSTRUCTIONS)
        summary = character_summary()
        first, reasons = planner.select_character_summaries(
            (summary,), context_mode=PlannerContextMode.LEAN_CONTINUOUS
        )
        self.assertEqual(first, (summary,))
        receipts = planner.record_character_summary_deliveries(
            first,
            reasons,
            planner_prompt_sha256=text_sha256("turn-one-prompt"),
        )
        self.assertEqual(receipts[0].delivery_reason, "first_relevant_appearance")
        repeated, _ = planner.select_character_summaries(
            (summary,), context_mode=PlannerContextMode.LEAN_CONTINUOUS
        )
        self.assertEqual(repeated, ())

        composer_prompt, _ = build_continuous_composer_prompt(
            current_user_source="Continue.",
            ingress_source_units=(),
            planner_sequence=sequence(),
            character_summaries=(summary,),
        )
        self.assertIn(summary.summary, composer_prompt)

    def test_true_reconstruction_creates_a_new_thread_and_then_returns_to_lean(self) -> None:
        port = InMemoryContinuousStoredSessionPort()
        old = ContinuousSessionCoordinator(
            compatibility(ContinuousSessionRole.PLANNER), port
        )
        old.install_base_instructions(PLANNER_STABLE_INSTRUCTIONS)
        old_handle = old.ensure_session()
        self.assertIs(
            old.initialization_receipt.packet_kind,
            ContinuousSessionInitializationKind.FIRST_THREAD_INITIALIZATION,
        )
        first_initialization_packet = (
            ContinuousSessionInitializationPacketV1.from_receipt(
                old.initialization_receipt
            )
        )
        self.assertEqual(
            set(first_initialization_packet.to_payload()),
            first_initialization_packet.ALLOWED_FIELDS,
        )
        self.assertEqual(
            first_initialization_packet.to_payload()["packet_kind"],
            "first_thread_initialization",
        )
        old_snapshot = old.snapshot()
        port.archive(old_handle, "simulated-loss")
        with self.assertRaisesRegex(StateConflictError, "lost provider thread"):
            ContinuousSessionCoordinator.resume_compatible(
                old_snapshot,
                port,
                expected_compatibility=compatibility(
                    ContinuousSessionRole.PLANNER
                ),
                base_instructions=PLANNER_STABLE_INSTRUCTIONS,
            )

        envelope = accepted_envelope()
        descriptor = {
            "reference_key": "binding_accepted_ref_" + "1" * 20,
            "accepted_turn_id": envelope.accepted_turn_id,
            "source_item_key": "verify_arrival",
            "field_name": "resulting_state",
            "visibility": "public",
            "knowledge_owner_id": None,
            "roles": {},
        }
        accepted = ContinuousReconstructionAcceptedTurnV1(
            envelope=envelope,
            synchronization_receipt_sha256=text_sha256("synchronized"),
            stable_reference_descriptors=(descriptor,),
        )
        ancestry = canonical_sha256(
            {
                "world_id": "world:hanezawa_test",
                "branch_id": "branch:main",
                "accepted_tail": (
                    (
                        envelope.accepted_turn_id,
                        envelope.envelope_sha256,
                        accepted.synchronization_receipt_sha256,
                    ),
                ),
            }
        )
        summary = character_summary()
        bundle = ContinuousSessionReconstructionBundleV1(
            schema_version=ContinuousSessionReconstructionBundleV1.SCHEMA_VERSION,
            world_id="world:hanezawa_test",
            branch_id="branch:main",
            accepted_tail=(accepted,),
            character_summaries=(summary,),
            accepted_ancestry_sha256=ancestry,
            reconstruction_reason="lost_thread",
        )
        rebuilt = ContinuousSessionCoordinator.reconstruct_new_thread(
            port=port,
            expected_compatibility=compatibility(
                ContinuousSessionRole.PLANNER
            ),
            base_instructions=PLANNER_STABLE_INSTRUCTIONS,
            bundle=bundle,
            parent_provider_thread_sha256=old_handle.provider_thread_id_sha256,
        )
        self.assertNotEqual(
            old_handle.provider_thread_id,
            rebuilt.ensure_session().provider_thread_id,
        )
        self.assertIs(
            rebuilt.initialization_receipt.context_mode,
            PlannerContextMode.RECONSTRUCTION,
        )
        self.assertIs(
            rebuilt.initialization_receipt.packet_kind,
            ContinuousSessionInitializationKind.RECONSTRUCTION_INITIALIZATION,
        )
        self.assertEqual(
            ContinuousSessionInitializationPacketV1.from_receipt(
                rebuilt.initialization_receipt
            ).to_payload()["packet_kind"],
            "reconstruction_initialization",
        )
        self.assertGreater(rebuilt.initialization_receipt.reconstruction_bytes, 0)
        self.assertEqual(rebuilt.snapshot().accepted_turn_ids, ("turn:001",))
        repeated, _ = rebuilt.select_character_summaries(
            (summary,), context_mode=PlannerContextMode.LEAN_CONTINUOUS
        )
        self.assertEqual(repeated, ())

    def test_stable_reference_store_resolves_without_prompt_fact_payload(self) -> None:
        facts = tuple(
            fact
            for item in accepted_sequence().items
            for fact in project_final_sequence_facts(item)
        )
        receipt, references = build_stable_accepted_context_references(
            world_id="world:hanezawa_test",
            branch_id="branch:main",
            scene_id="scene:arrival",
            planner_session_id="session-one",
            provider_thread_sha256=text_sha256("thread-one"),
            accepted_turn_ids=("turn:001",),
            accepted_turn_id="turn:001",
            accepted_envelope_sha256=accepted_envelope().envelope_sha256,
            accepted_pair_sha256=text_sha256("pair"),
            accepted_event_sha256=text_sha256("event"),
            acceptance_receipt_sha256=text_sha256("acceptance"),
            injection_receipt_sha256=text_sha256("injection"),
            session_snapshot_sha256=text_sha256("snapshot"),
            synchronization_receipt_sha256=text_sha256("synchronization"),
            facts=facts,
        )
        with TemporaryDirectory() as directory:
            store = StableAcceptedContextReferenceStore(Path(directory))
            store.save(receipt=receipt, references=references)
            loaded_receipt, loaded = store.load(
                "turn:001",
                provider_thread_sha256=text_sha256("thread-one"),
            )
        self.assertEqual(loaded_receipt, receipt)
        self.assertEqual(loaded, references)
        validate_stable_accepted_context_reference_facts(
            envelope=accepted_envelope(),
            references=loaded,
        )
        tampered = replace(
            loaded[0],
            field_value="altered but structurally valid accepted context",
            field_value_sha256=text_sha256(
                "altered but structurally valid accepted context"
            ),
        )
        with self.assertRaisesRegex(StateConflictError, "facts changed"):
            validate_stable_accepted_context_reference_facts(
                envelope=accepted_envelope(),
                references=(tampered, *loaded[1:]),
            )
        compact_text = str(loaded_receipt)
        self.assertNotIn(references[0].field_value, compact_text)

        registry = RequestEvidenceBindingRegistry(
            world_id="world:hanezawa_test",
            branch_id="branch:main",
            turn_id="turn:002",
        )
        binding = registry.allocate_stable_accepted_context_reference(
            references[0],
            current_provider_thread_sha256=text_sha256("thread-one"),
            current_accepted_ancestry_sha256=receipt.accepted_ancestry_sha256,
        )
        self.assertEqual(binding.binding_key, references[0].reference_key)
        with self.assertRaisesRegex(StateConflictError, "stale, foreign"):
            RequestEvidenceBindingRegistry(
                world_id="world:hanezawa_test",
                branch_id="branch:sibling",
                turn_id="turn:002",
            ).allocate_stable_accepted_context_reference(
                references[0],
                current_provider_thread_sha256=text_sha256("thread-one"),
                current_accepted_ancestry_sha256=receipt.accepted_ancestry_sha256,
            )
        for provider_thread_sha256, accepted_ancestry_sha256 in (
            (text_sha256("wrong thread"), receipt.accepted_ancestry_sha256),
            (text_sha256("thread-one"), text_sha256("wrong ancestry")),
        ):
            with self.assertRaisesRegex(StateConflictError, "stale, foreign"):
                RequestEvidenceBindingRegistry(
                    world_id="world:hanezawa_test",
                    branch_id="branch:main",
                    turn_id="turn:002",
                ).allocate_stable_accepted_context_reference(
                    references[0],
                    current_provider_thread_sha256=provider_thread_sha256,
                    current_accepted_ancestry_sha256=accepted_ancestry_sha256,
                )

    def test_validator_closure_resolves_only_cited_exact_accepted_values(self) -> None:
        facts = tuple(
            fact
            for item in accepted_sequence().items
            for fact in project_final_sequence_facts(item)
        )
        receipt, references = build_stable_accepted_context_references(
            world_id="world:hanezawa_test",
            branch_id="branch:main",
            scene_id="scene:arrival",
            planner_session_id="planner-session",
            provider_thread_sha256=text_sha256("planner-thread"),
            accepted_turn_ids=("turn:001",),
            accepted_turn_id="turn:001",
            accepted_envelope_sha256=accepted_envelope().envelope_sha256,
            accepted_pair_sha256=text_sha256("pair"),
            accepted_event_sha256=text_sha256("event"),
            acceptance_receipt_sha256=text_sha256("acceptance"),
            injection_receipt_sha256=text_sha256("injection"),
            session_snapshot_sha256=text_sha256("snapshot"),
            synchronization_receipt_sha256=text_sha256("synchronization"),
            facts=facts,
        )
        registry = RequestEvidenceBindingRegistry(
            world_id="world:hanezawa_test",
            branch_id="branch:main",
            turn_id="turn:002",
        )
        for reference in references:
            registry.allocate_stable_accepted_context_reference(
                reference,
                current_provider_thread_sha256=text_sha256("planner-thread"),
                current_accepted_ancestry_sha256=(
                    receipt.accepted_ancestry_sha256
                ),
            )
        cited = next(
            value for value in references if value.field_name == "resulting_state"
        )
        uncited = next(
            value
            for value in references
            if value.field_name == "valid_deepseek_additions"
        )
        private_other = next(
            value for value in references if value.knowledge_owner_id is not None
        )
        compatible = replace(
            sequence(),
            accepted_turn_id="turn:002",
            beats=(
                replace(
                    sequence().beats[0],
                    evidence_grounded_perception=cited.field_value,
                    resulting_state=cited.field_value,
                    source_evidence_bindings=(cited.reference_key,),
                ),
            ),
        )
        closure = registry.validator_cited_accepted_evidence_closure(
            compatible
        )
        self.assertEqual(len(closure), 1)
        self.assertEqual(closure[0].binding_key, cited.reference_key)
        self.assertEqual(
            closure[0].accepted_reference.field_value,
            cited.field_value,
        )
        self.assertEqual(
            set(to_primitive(closure[0])),
            {
                "accepted_reference",
                "authority_classification",
                "binding_key",
                "binding_kind",
                "cited_by_beat_keys",
                "closure_sha256",
                "request_turn_id",
                "schema_version",
            },
        )
        with self.assertRaises(TypeError):
            ValidatorCitedAcceptedEvidenceV1(
                **{
                    **{
                        key: getattr(closure[0], key)
                        for key in to_primitive(closure[0])
                    },
                    "untyped_caller_data": "forbidden",
                }
            )
        serialized = json.dumps(
            to_primitive(closure),
            sort_keys=True,
            separators=(",", ":"),
        )
        self.assertNotIn(uncited.field_value, serialized)
        self.assertNotIn(private_other.field_value, serialized)
        validator_manifest = registry.validator_binding_manifest(compatible)
        stable_manifest_keys = tuple(
            value["binding_key"]
            for value in validator_manifest
            if value["stable_reference_only"]
        )
        self.assertEqual(stable_manifest_keys, (cited.reference_key,))

        incompatible = replace(
            compatible,
            beats=(
                replace(
                    compatible.beats[0],
                    evidence_grounded_perception=(
                        "The visitor is already inside and no threshold remains."
                    ),
                    resulting_state=(
                        "The visitor is already inside and no threshold remains."
                    ),
                ),
            ),
        )
        compatible_prompt, _ = build_validator_prompt(
            task_mode=ValidatorTaskMode.FINALIZE_TURN,
            current_user_source="Continue.",
            planner_sequence=compatible,
            deepseek_realization="Sakura waits at the threshold.",
            accepted_turn_id="turn:002",
            evidence_binding_manifest=validator_manifest,
            cited_accepted_evidence=tuple(
                to_primitive(value) for value in closure
            ),
        )
        incompatible_prompt, _ = build_validator_prompt(
            task_mode=ValidatorTaskMode.FINALIZE_TURN,
            current_user_source="Continue.",
            planner_sequence=incompatible,
            deepseek_realization="Sakura waits at the threshold.",
            accepted_turn_id="turn:002",
            evidence_binding_manifest=validator_manifest,
            cited_accepted_evidence=tuple(
                to_primitive(value) for value in closure
            ),
        )

        def provider_free_semantic_disposition(prompt: str) -> str:
            request = json.loads(prompt.split("[VALIDATOR REQUEST]\n", 1)[1])
            exact_values = tuple(
                value["accepted_reference"]["field_value"]
                for value in request["cited_accepted_evidence"]
            )
            resulting_state = request["planner_sequence"]["beats"][0][
                "resulting_state"
            ]
            if "outside awaiting verification" in exact_values[0] and (
                "already inside" in resulting_state
            ):
                return "concern"
            return "accepted"

        self.assertEqual(
            provider_free_semantic_disposition(compatible_prompt),
            "accepted",
        )
        self.assertEqual(
            provider_free_semantic_disposition(incompatible_prompt),
            "concern",
        )
        self.assertNotIn("accepted_session_projections", incompatible_prompt)

        other_owner_sequence = replace(
            compatible,
            selected_character_ids=("character:mia_hanezawa",),
            omitted_character_ids=("character:sakura_hanezawa",),
            beats=(
                replace(
                    compatible.beats[0],
                    roles=replace(
                        compatible.beats[0].roles,
                        action_owner_ids=("character:mia_hanezawa",),
                    ),
                    source_evidence_bindings=(private_other.reference_key,),
                ),
            ),
        )
        with self.assertRaisesRegex(PermissionError, "left its owner"):
            registry.validator_cited_accepted_evidence_closure(
                other_owner_sequence
            )

        binding = registry._bindings[cited.reference_key]
        registry._bindings[cited.reference_key] = replace(
            binding,
            visibility=EvidenceVisibility.CHARACTER_PRIVATE,
            knowledge_owner_id="character:sakura_hanezawa",
        )
        with self.assertRaisesRegex(StateConflictError, "binding changed"):
            registry.validator_cited_accepted_evidence_closure(compatible)
        registry._bindings[cited.reference_key] = binding

        stale_references = (
            replace(
                cited,
                field_value="stale accepted value",
                field_value_sha256=text_sha256("stale accepted value"),
            ),
            replace(cited, accepted_turn_id="turn:stale"),
            replace(cited, accepted_ancestry_sha256=text_sha256("stale ancestry")),
            replace(cited, accepted_envelope_sha256=text_sha256("stale envelope")),
            replace(cited, accepted_pair_sha256=text_sha256("stale pair")),
            replace(cited, accepted_event_sha256=text_sha256("stale event")),
            replace(cited, acceptance_receipt_sha256=text_sha256("stale acceptance")),
            replace(cited, injection_receipt_sha256=text_sha256("stale injection")),
            replace(cited, session_snapshot_sha256=text_sha256("stale snapshot")),
            replace(
                cited,
                synchronization_receipt_sha256=text_sha256(
                    "stale synchronization"
                ),
            ),
        )
        for stale_reference in stale_references:
            with self.subTest(stale_field=stale_reference.reference_sha256):
                registry._stable_accepted_context_references[
                    cited.reference_key
                ] = stale_reference
                with self.assertRaisesRegex(StateConflictError, "binding changed"):
                    registry.validator_cited_accepted_evidence_closure(
                        compatible
                    )
        registry._stable_accepted_context_references[cited.reference_key] = cited

    def test_successful_provider_fork_persists_child_keys_and_summary_custody(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            world = ContinuousWorldStore(root / "worlds")
            _seed_character(world)
            message = "Hello, my name is Ted."
            ingress = make_ingress_authority(
                root / "ingress",
                (message, "turn-001"),
            )
            port = InMemoryContinuousStoredSessionPort()
            parent_compatibility = replace(
                session_compatibility(ContinuousSessionRole.PLANNER),
                world_directory_identity_sha256=(
                    world.branch_directory_identity_sha256("world-test", "main")
                ),
            )
            parent = ContinuousSessionCoordinator(
                parent_compatibility,
                port,
            )
            validator_session = ContinuousSessionCoordinator(
                replace(
                    session_compatibility(ContinuousSessionRole.VALIDATOR),
                    world_directory_identity_sha256=(
                        world.branch_directory_identity_sha256("world-test", "main")
                    ),
                ),
                port,
            )
            parent_runtime = ContinuousShadowTurnCoordinator(
                world=world,
                planner_session=parent,
                validator_session=validator_session,
                planner=_QueueStage(rich_sequence()),
                composer=_QueueStage(composer_draft("Sakura requests proof.")),
                validator=_QueueStage(
                    package(
                        turn_id="turn-001",
                        revision=1,
                        story_text="Sakura requests proof.",
                    )
                ),
                ingress_authority=ingress,
            )
            character_path = (
                world.branch_root("world-test", "main")
                / "ACTIVE"
                / "Characters"
                / "Sakura.json"
            )
            candidate = parent_runtime.prepare(
                ContinuousTurnRequestV1(
                    world_id="world-test",
                    branch_id="main",
                    scene_id="scene-001",
                    turn_id="turn-001",
                    user_message=message,
                    **ingress_reference(ingress, message, "turn-001"),
                    character_summaries=(
                        character_summary(
                            source_sha256=text_sha256(
                                character_path.read_text(encoding="utf-8")
                            )
                        ),
                    ),
                )
            )
            self.assertEqual(candidate.provider_calls, 0)
            parent_runtime.apply_creator_action(
                "turn-001", CreatorReviewAction.ACCEPT
            )
            updated_summary = build_character_summary_envelope(
                branch_root=world.branch_root("world-test", "main"),
                source_path="ACTIVE/Characters/Sakura.json",
                character_id="character:sakura_hanezawa",
            )
            parent.record_character_summary_deliveries(
                (updated_summary,),
                ("material_revision_change",),
                planner_prompt_sha256=text_sha256("updated summary prompt"),
            )

            parent_root = world.branch_root("world-test", "main")
            pair = world.accepted_turn_pairs(
                "world-test", "main", ("turn-001",)
            )[0]

            target = replace(
                parent_compatibility,
                branch_id="child",
                world_directory_identity_sha256=(
                    world.branch_directory_identity_sha256("world-test", "child")
                ),
            )
            materialization = parent_runtime.materialize_planner_branch(
                target_compatibility=target
            )
            child_root = world.branch_root("world-test", "child")
            custody = branch_receipt(
                parent,
                "child",
                materialization_receipt_sha256=materialization.receipt_sha256,
            )
            forked = parent_runtime.fork_planner_session_for_branch(
                target_compatibility=target,
                materialization_receipt=materialization,
                branch_receipt=custody,
            )
            child = forked.coordinator
            self.assertIs(
                child.initialization_receipt.packet_kind,
                ContinuousSessionInitializationKind.ACCEPTED_CHECKPOINT_FORK_INITIALIZATION,
            )
            self.assertEqual(
                ContinuousSessionInitializationPacketV1.from_receipt(
                    child.initialization_receipt
                ).to_payload()["packet_kind"],
                "accepted_checkpoint_fork_initialization",
            )
            self.assertNotEqual(
                child.ensure_session().provider_thread_id_sha256,
                parent.ensure_session().provider_thread_id_sha256,
            )
            self.assertEqual(len(forked.stable_reference_paths), 1)
            self.assertTrue(forked.session_snapshot_path.is_file())
            transfer_events = tuple(
                value
                for value in child.snapshot().context_events
                if value.event_type == "branch_reference_rebinding"
            )
            self.assertEqual(len(transfer_events), 1)
            child_context = port.model_visible_context[
                child.ensure_session().provider_thread_id
            ]
            self.assertEqual(
                sum(
                    value.startswith(
                        "[BRANCH ACCEPTED REFERENCE REBINDING]"
                    )
                    for value in child_context
                ),
                1,
            )
            child_receipt, child_references = StableAcceptedContextReferenceStore(
                child_root
            ).load(
                "turn-001",
                provider_thread_sha256=(
                    child.ensure_session().provider_thread_id_sha256
                ),
            )
            parent_receipt, parent_references = StableAcceptedContextReferenceStore(
                parent_root
            ).load(
                "turn-001",
                provider_thread_sha256=(
                    parent.ensure_session().provider_thread_id_sha256
                ),
            )
            self.assertEqual(child_receipt.branch_id, "child")
            self.assertNotEqual(
                child_receipt.stable_reference_keys,
                parent_receipt.stable_reference_keys,
            )
            with self.assertRaisesRegex(StateConflictError, "already established"):
                child.establish_branch_reference_rebinding(
                    world.accepted_final_envelope(
                        "world-test", "child", "turn-001"
                    ),
                    branch_receipt=custody,
                    parent_reference_keys=parent_receipt.stable_reference_keys,
                    child_reference_descriptors=tuple(
                        value.injection_descriptor()
                        for value in child_references
                    ),
                )
            deliveries = child.snapshot().character_summary_deliveries
            self.assertEqual(len(deliveries), 1)
            self.assertEqual(
                deliveries[0].delivery_reason,
                "accepted_checkpoint_fork",
            )
            self.assertEqual(
                deliveries[0].provider_thread_sha256,
                child.ensure_session().provider_thread_id_sha256,
            )

            child_validator = ContinuousSessionCoordinator(
                replace(
                    session_compatibility(ContinuousSessionRole.VALIDATOR),
                    branch_id="child",
                    world_directory_identity_sha256=(
                        world.branch_directory_identity_sha256(
                            "world-test", "child"
                        )
                    ),
                ),
                port,
            )
            child_runtime = ContinuousShadowTurnCoordinator(
                world=world,
                planner_session=child,
                validator_session=child_validator,
                planner=_QueueStage(),
                composer=_QueueStage(),
                validator=_QueueStage(),
                ingress_authority=ingress,
            )
            next_request = ContinuousTurnRequestV1(
                world_id="world-test",
                branch_id="child",
                session_id="session-test",
                request_id="request-turn-002",
                idempotency_key_sha256=text_sha256("turn-002-idempotency"),
                scene_id="scene-001",
                turn_id="turn-002",
                user_message="Continue.",
                ingress_receipt_id="receipt-not-used-by-private-binding-test",
                ingress_receipt_sha256=text_sha256("receipt"),
            )
            registry = RequestEvidenceBindingRegistry(
                world_id="world-test",
                branch_id="child",
                turn_id="turn-002",
            )
            source = registry.allocate_current_source(
                source_identity="current_user_source:turn-002",
                source_text="Continue.",
                protected_user_allowance_scope="exact supplied source",
                source_units=(),
            )
            mechanical = registry.allocate_mechanical_connective_allowance()
            head, bindings, projections = (
                child_runtime._bind_stable_accepted_context(
                    request=next_request,
                    registry=registry,
                    branch_root=child_root,
                )
            )
            self.assertEqual(head, child_receipt)
            self.assertEqual(projections, ())
            self.assertEqual(
                tuple(value["binding_key"] for value in bindings),
                child_receipt.stable_reference_keys,
            )
            child_lean_packet = build_continuous_planner_turn_packet(
                world_id="world-test",
                branch_id="child",
                session_id="session-test",
                request_id="request-turn-002",
                scene_id="scene-001",
                turn_id="turn-002",
                context_mode="lean_continuous",
                current_user_message="Continue.",
                request_local_evidence_bindings=registry.prompt_manifest(),
                current_source_binding_key=source.binding_key,
                mechanical_connective_binding_key=mechanical.binding_key,
                protected_user_source_claims=(),
                ingress_source_units=(),
                ingress_custody={
                    "receipt_id": "ingress_receipt:child",
                    "receipt_sha256": text_sha256("child receipt"),
                    "raw_source_sha256": text_sha256("Continue."),
                    "protected_user_id": "character:ted",
                    "source_unit_keys": (),
                },
                character_summary_bindings=(),
                compact_accepted_head_receipt=head,
                stable_accepted_reference_keys=tuple(
                    value["binding_key"] for value in bindings
                ),
                projection_assisted_trigger=None,
                projection_reference_keys=(),
                projection_facts=(),
                scene_change_envelope_sha256=None,
            )
            self.assertIs(
                child_lean_packet.packet_kind,
                ContinuousPlannerPacketKind.LEAN_CONTINUATION,
            )
            with self.assertRaisesRegex(StateConflictError, "stale, foreign"):
                RequestEvidenceBindingRegistry(
                    world_id="world-test",
                    branch_id="child",
                    turn_id="turn-002",
                ).allocate_stable_accepted_context_reference(
                    parent_references[0],
                    current_provider_thread_sha256=(
                        child.ensure_session().provider_thread_id_sha256
                    ),
                    current_accepted_ancestry_sha256=(
                        child_receipt.accepted_ancestry_sha256
                    ),
                )
            with self.assertRaisesRegex(StateConflictError, "stale, foreign"):
                RequestEvidenceBindingRegistry(
                    world_id="world-test",
                    branch_id="child-sibling",
                    turn_id="turn-002",
                ).allocate_stable_accepted_context_reference(
                    child_references[0],
                    current_provider_thread_sha256=(
                        child.ensure_session().provider_thread_id_sha256
                    ),
                    current_accepted_ancestry_sha256=(
                        child_receipt.accepted_ancestry_sha256
                    ),
                )
            self.assertEqual(
                child_references[0].injection_receipt_sha256,
                forked.transfer_receipt.operation_receipt_sha256,
            )

            persistence_target = replace(
                target,
                branch_id="child-persistence-failure",
                world_directory_identity_sha256=(
                    world.branch_directory_identity_sha256(
                        "world-test", "child-persistence-failure"
                    )
                ),
            )
            persistence_materialization = parent_runtime.materialize_planner_branch(
                target_compatibility=persistence_target
            )
            persistence_root = world.branch_root(
                "world-test", "child-persistence-failure"
            )
            persistence_custody = branch_receipt(
                parent,
                "child-persistence-failure",
                materialization_receipt_sha256=(
                    persistence_materialization.receipt_sha256
                ),
            )
            with mock.patch.object(
                StableAcceptedContextReferenceStore,
                "save",
                side_effect=StateConflictError("simulated child persistence failure"),
            ):
                with self.assertRaisesRegex(
                    StateConflictError, "persistence failure"
                ):
                    parent_runtime.fork_planner_session_for_branch(
                        target_compatibility=persistence_target,
                        materialization_receipt=persistence_materialization,
                        branch_receipt=persistence_custody,
                    )
            self.assertFalse(
                (
                    persistence_root
                    / "PLANNER_SESSION"
                    / "SESSION_SNAPSHOT.json"
                ).exists()
            )
            self.assertIs(parent_runtime.planner_session, parent)

            injection_target = replace(
                target,
                branch_id="child-injection-failure",
                world_directory_identity_sha256=(
                    world.branch_directory_identity_sha256(
                        "world-test", "child-injection-failure"
                    )
                ),
            )
            injection_materialization = parent_runtime.materialize_planner_branch(
                target_compatibility=injection_target
            )
            injection_root = world.branch_root(
                "world-test", "child-injection-failure"
            )
            injection_custody = branch_receipt(
                parent,
                "child-injection-failure",
                materialization_receipt_sha256=(
                    injection_materialization.receipt_sha256
                ),
            )
            original_append = port.append_context

            def fail_transfer(handle, payload):
                if payload.startswith(
                    "[BRANCH ACCEPTED REFERENCE REBINDING]"
                ):
                    raise StateConflictError("simulated child injection failure")
                return original_append(handle, payload)

            with mock.patch.object(
                port,
                "append_context",
                side_effect=fail_transfer,
            ):
                with self.assertRaisesRegex(
                    StateConflictError, "injection failure"
                ):
                    parent_runtime.fork_planner_session_for_branch(
                        target_compatibility=injection_target,
                        materialization_receipt=injection_materialization,
                        branch_receipt=injection_custody,
                    )
            self.assertFalse(
                any(
                    (injection_root / "PLANNER_SESSION" / "ACCEPTED_REFERENCES").glob(
                        "*.json"
                    )
                )
            )
            self.assertFalse(
                (
                    injection_root
                    / "PLANNER_SESSION"
                    / "SESSION_SNAPSHOT.json"
                ).exists()
            )
            self.assertIs(parent_runtime.planner_session, parent)

    def test_provider_fork_rejects_foreign_stale_and_wrong_privacy_receipts(self) -> None:
        port = InMemoryContinuousStoredSessionPort()
        parent = ContinuousSessionCoordinator(
            compatibility(ContinuousSessionRole.PLANNER), port
        )
        parent.install_base_instructions(PLANNER_STABLE_INSTRUCTIONS)
        envelope = accepted_envelope()
        parent.append_accepted_final_sequence(envelope)
        parent.synchronize_accepted_final_sequence(envelope)
        valid = branch_receipt(parent, "branch:child")
        prior_forks = tuple(
            value for value in port.operations if value[0] == "fork_branch"
        )
        cases = {
            "privacy": {
                "privacy_boundary_sha256": text_sha256(
                    "caller supplied but structurally valid"
                )
            },
            "foreign_parent": {"parent_branch_id": "branch:foreign"},
            "sibling_target": {"child_branch_id": "branch:sibling"},
            "changed_head": {"accepted_checkpoint_turn_id": "turn:stale"},
            "sibling_ancestry": {
                "accepted_ancestry_sha256": text_sha256("sibling ancestry")
            },
            "incompatible_thread": {
                "parent_provider_thread_sha256": text_sha256("foreign thread")
            },
        }
        for label, changes in cases.items():
            with self.subTest(label=label):
                payload = {
                    "schema_version": valid.schema_version,
                    "world_id": valid.world_id,
                    "parent_branch_id": valid.parent_branch_id,
                    "child_branch_id": valid.child_branch_id,
                    "accepted_checkpoint_turn_id": (
                        valid.accepted_checkpoint_turn_id
                    ),
                    "accepted_ancestry_sha256": valid.accepted_ancestry_sha256,
                    "parent_provider_thread_sha256": (
                        valid.parent_provider_thread_sha256
                    ),
                    "privacy_boundary_sha256": valid.privacy_boundary_sha256,
                    "branch_materialization_receipt_sha256": (
                        valid.branch_materialization_receipt_sha256
                    ),
                    **changes,
                }
                forged = ContinuousBranchForkReceiptV2(
                    **payload,
                    receipt_sha256=canonical_sha256(payload),
                )
                with self.assertRaisesRegex(
                    StateConflictError, "accepted ancestry"
                ):
                    parent.fork_for_branch(
                        compatibility(
                            ContinuousSessionRole.PLANNER, "branch:child"
                        ),
                        branch_receipt=forged,
                    )
        self.assertEqual(
            tuple(value for value in port.operations if value[0] == "fork_branch"),
            prior_forks,
        )

    def test_nonforkable_branch_reconstructs_and_rekeys_parent_references(self) -> None:
        class NonForkablePort(InMemoryContinuousStoredSessionPort):
            def fork_branch(self, parent, compatibility):
                raise StateConflictError("provider fork unavailable")

        port = NonForkablePort()
        parent = ContinuousSessionCoordinator(
            compatibility(ContinuousSessionRole.PLANNER), port
        )
        parent.install_base_instructions(PLANNER_STABLE_INSTRUCTIONS)
        envelope = accepted_envelope()
        parent.append_accepted_final_sequence(envelope)
        parent.synchronize_accepted_final_sequence(envelope)
        custody = branch_receipt(parent, "branch:child")
        with self.assertRaisesRegex(StateConflictError, "fork unavailable"):
            parent.fork_for_branch(
                compatibility(ContinuousSessionRole.PLANNER, "branch:child"),
                branch_receipt=custody,
            )

        facts = tuple(
            fact
            for item in accepted_sequence().items
            for fact in project_final_sequence_facts(item)
        )
        source_receipt, source_references = build_stable_accepted_context_references(
            world_id="world:hanezawa_test",
            branch_id="branch:main",
            scene_id="scene:arrival",
            planner_session_id="parent-session",
            provider_thread_sha256=parent.ensure_session().provider_thread_id_sha256,
            accepted_turn_ids=("turn:001",),
            accepted_turn_id="turn:001",
            accepted_envelope_sha256=envelope.envelope_sha256,
            accepted_pair_sha256=text_sha256("pair"),
            accepted_event_sha256=text_sha256("event"),
            acceptance_receipt_sha256=text_sha256("acceptance"),
            injection_receipt_sha256=text_sha256("injection"),
            session_snapshot_sha256=text_sha256("parent-snapshot"),
            synchronization_receipt_sha256=text_sha256("parent-sync"),
            facts=facts,
        )
        descriptors = stable_reference_descriptors_for_reconstruction_target(
            source_references=source_references,
            target_world_id="world:hanezawa_test",
            target_branch_id="branch:child",
        )
        accepted = ContinuousReconstructionAcceptedTurnV1(
            envelope=envelope,
            synchronization_receipt_sha256=text_sha256("parent-sync"),
            stable_reference_descriptors=descriptors,
        )
        ancestry = canonical_sha256(
            {
                "world_id": "world:hanezawa_test",
                "branch_id": "branch:child",
                "accepted_tail": (
                    (
                        envelope.accepted_turn_id,
                        envelope.envelope_sha256,
                        accepted.synchronization_receipt_sha256,
                    ),
                ),
            }
        )
        bundle = ContinuousSessionReconstructionBundleV1(
            schema_version=ContinuousSessionReconstructionBundleV1.SCHEMA_VERSION,
            world_id="world:hanezawa_test",
            branch_id="branch:child",
            accepted_tail=(accepted,),
            character_summaries=(),
            accepted_ancestry_sha256=ancestry,
            reconstruction_reason="non_forkable_branch",
        )
        child = ContinuousSessionCoordinator.reconstruct_new_thread(
            port=port,
            expected_compatibility=compatibility(
                ContinuousSessionRole.PLANNER, "branch:child"
            ),
            base_instructions=PLANNER_STABLE_INSTRUCTIONS,
            bundle=bundle,
            parent_provider_thread_sha256=(
                parent.ensure_session().provider_thread_id_sha256
            ),
            branch_receipt_sha256=custody.receipt_sha256,
        )
        child_handle = child.ensure_session()
        rebound_receipt, rebound = (
            rebind_stable_accepted_context_references_for_reconstruction(
                source_receipt=source_receipt,
                source_references=source_references,
                planner_session_id=child_handle.provider_session_id,
                provider_thread_sha256=child_handle.provider_thread_id_sha256,
                accepted_turn_ids=("turn:001",),
                initialization_receipt_sha256=(
                    child.initialization_receipt.receipt_sha256
                ),
                session_snapshot_sha256=child.snapshot().snapshot_sha256,
                target_world_id="world:hanezawa_test",
                target_branch_id="branch:child",
            )
        )
        self.assertEqual(rebound_receipt.branch_id, "branch:child")
        self.assertNotEqual(
            rebound_receipt.stable_reference_keys,
            source_receipt.stable_reference_keys,
        )
        child_binding = RequestEvidenceBindingRegistry(
            world_id="world:hanezawa_test",
            branch_id="branch:child",
            turn_id="turn:002",
        ).allocate_stable_accepted_context_reference(
            rebound[0],
            current_provider_thread_sha256=child_handle.provider_thread_id_sha256,
            current_accepted_ancestry_sha256=(
                rebound_receipt.accepted_ancestry_sha256
            ),
        )
        self.assertEqual(child_binding.binding_key, rebound[0].reference_key)
        with self.assertRaisesRegex(StateConflictError, "stale, foreign"):
            RequestEvidenceBindingRegistry(
                world_id="world:hanezawa_test",
                branch_id="branch:main",
                turn_id="turn:002",
            ).allocate_stable_accepted_context_reference(
                rebound[0],
                current_provider_thread_sha256=child_handle.provider_thread_id_sha256,
                current_accepted_ancestry_sha256=(
                    rebound_receipt.accepted_ancestry_sha256
                ),
            )


if __name__ == "__main__":
    unittest.main()
