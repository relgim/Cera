from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from cera.continuous.evidence import (
    RequestEvidenceBindingRegistry,
    StableAcceptedContextReferenceStore,
    build_stable_accepted_context_references,
    project_final_sequence_facts,
    rebind_stable_accepted_context_references_for_reconstruction,
    stable_reference_descriptors_for_reconstruction_target,
    validate_stable_accepted_context_reference_facts,
)
from cera.continuous.prompting import (
    PLANNER_STABLE_INSTRUCTIONS,
    build_continuous_composer_prompt,
    build_planner_turn_prompt,
    planner_base_instruction_usage,
)
from cera.continuous.sessions import (
    ContinuousReconstructionAcceptedTurnV1,
    ContinuousSessionCoordinator,
    ContinuousSessionReconstructionBundleV1,
    ContinuousSessionRole,
    InMemoryContinuousStoredSessionPort,
    PlannerContextMode,
)
from cera.errors import ContractValidationError, StateConflictError
from cera.serialization import canonical_sha256, text_sha256

from tests.test_continuous_planner_validator import (
    accepted_sequence,
    branch_receipt,
    compatibility,
    sequence,
)
from tests.test_continuous_world import character_summary
from cera.continuous.contracts import AcceptedFinalSequenceEnvelopeV1


def accepted_envelope() -> AcceptedFinalSequenceEnvelopeV1:
    return AcceptedFinalSequenceEnvelopeV1(
        schema_version=AcceptedFinalSequenceEnvelopeV1.SCHEMA_VERSION,
        accepted_turn_id="turn:001",
        user_message="Hello.",
        complete_final_sequence=accepted_sequence(),
        acceptance_receipt_sha256=text_sha256("accepted-turn-001"),
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

        prompt, usage = build_planner_turn_prompt(
            current_packet={"turn_id": "turn:001"},
            context_mode=PlannerContextMode.LEAN_CONTINUOUS,
        )
        self.assertNotIn(PLANNER_STABLE_INSTRUCTIONS, prompt)
        self.assertIn('"context_mode":"lean_continuous"', prompt)
        self.assertNotIn(
            "stable_instructions", tuple(value.component for value in usage)
        )
        base = planner_base_instruction_usage()
        self.assertEqual(base.byte_count, len(PLANNER_STABLE_INSTRUCTIONS.encode("utf-8")))
        with self.assertRaisesRegex(ValueError, "requires a demonstrated trigger"):
            build_planner_turn_prompt(
                current_packet={"turn_id": "turn:002"},
                context_mode=PlannerContextMode.PROJECTION_ASSISTED,
            )
        key = "binding_accepted_ref_" + "2" * 20
        assisted, _ = build_planner_turn_prompt(
            current_packet={
                "turn_id": "turn:002",
                "projection_assisted": {
                    "facts": ({"binding_key": key, "field_value": "exact"},),
                },
            },
            context_mode=PlannerContextMode.PROJECTION_ASSISTED,
            projection_assisted_trigger="demonstrated_continuity_defect:missing_state",
            projection_reference_keys=(key,),
        )
        mode = json.loads(
            assisted.split("[PLANNER CONTEXT MODE]\n", 1)[1].split(
                "\n\n[ACCEPTED FINAL SEQUENCE ENVELOPES", 1
            )[0]
        )
        self.assertEqual(mode["context_mode"], "projection_assisted")
        self.assertEqual(mode["projection_reference_keys"], [key])
        with self.assertRaisesRegex(
            ValueError, "lean_continuous cannot carry projection"
        ):
            build_planner_turn_prompt(
                current_packet={"turn_id": "turn:002"},
                context_mode=PlannerContextMode.LEAN_CONTINUOUS,
                projection_assisted_trigger="silent-fallback",
                projection_reference_keys=(key,),
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
