from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
import unittest
from unittest import mock

from cera.continuous.evidence import (
    RequestEvidenceBindingRegistry,
    StableAcceptedContextReferenceStore,
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
from cera.continuous.prompting import (
    PLANNER_STABLE_INSTRUCTIONS,
    build_continuous_composer_prompt,
    build_planner_turn_prompt,
    planner_base_instruction_usage,
)
from cera.continuous.sessions import (
    ContinuousBranchForkReceiptV1,
    ContinuousReconstructionAcceptedTurnV1,
    ContinuousSessionCoordinator,
    ContinuousSessionReconstructionBundleV1,
    ContinuousSessionRole,
    InMemoryContinuousStoredSessionPort,
    PlannerContextMode,
)
from cera.errors import ContractValidationError, StateConflictError
from cera.serialization import canonical_sha256, text_sha256
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
            parent = ContinuousSessionCoordinator(
                session_compatibility(ContinuousSessionRole.PLANNER),
                port,
            )
            validator_session = ContinuousSessionCoordinator(
                session_compatibility(ContinuousSessionRole.VALIDATOR),
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
                    current_authority_packet={
                        "protected_user_id": "character:ted"
                    },
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

            parent_root = world.branch_root("world-test", "main")
            child_root = world.initialize("world-test", "child")
            pair = world.accepted_turn_pairs(
                "world-test", "main", ("turn-001",)
            )[0]
            world.write_accepted_pair("world-test", "child", pair)
            source_receipt_path = (
                parent_root
                / "CANDIDATES"
                / "turn-001"
                / "PROMOTION_RECEIPT.json"
            )
            target_receipt_path = (
                child_root
                / "CANDIDATES"
                / "turn-001"
                / "PROMOTION_RECEIPT.json"
            )
            target_receipt_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_receipt_path, target_receipt_path)

            target = replace(
                session_compatibility(ContinuousSessionRole.PLANNER),
                branch_id="child",
                world_directory_identity_sha256=text_sha256("world-test/child"),
            )
            custody = branch_receipt(parent, "child")
            forked = parent_runtime.fork_planner_session_for_branch(
                target_compatibility=target,
                branch_receipt=custody,
            )
            child = forked.coordinator
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
                    world_directory_identity_sha256=text_sha256(
                        "world-test/child"
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
                current_authority_packet={
                    "protected_user_id": "character:ted"
                },
                ingress_receipt_id="receipt-not-used-by-private-binding-test",
                ingress_receipt_sha256=text_sha256("receipt"),
            )
            registry = RequestEvidenceBindingRegistry(
                world_id="world-test",
                branch_id="child",
                turn_id="turn-002",
            )
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

            def seed_child_checkpoint(branch_id: str) -> Path:
                seeded = world.initialize("world-test", branch_id)
                world.write_accepted_pair("world-test", branch_id, pair)
                target_receipt = (
                    seeded
                    / "CANDIDATES"
                    / "turn-001"
                    / "PROMOTION_RECEIPT.json"
                )
                target_receipt.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source_receipt_path, target_receipt)
                return seeded

            persistence_root = seed_child_checkpoint("child-persistence-failure")
            persistence_target = replace(
                target,
                branch_id="child-persistence-failure",
                world_directory_identity_sha256=text_sha256(
                    "world-test/child-persistence-failure"
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
                        branch_receipt=branch_receipt(
                            parent, "child-persistence-failure"
                        ),
                    )
            self.assertFalse(
                (
                    persistence_root
                    / "PLANNER_SESSION"
                    / "SESSION_SNAPSHOT.json"
                ).exists()
            )
            self.assertIs(parent_runtime.planner_session, parent)

            injection_root = seed_child_checkpoint("child-injection-failure")
            injection_target = replace(
                target,
                branch_id="child-injection-failure",
                world_directory_identity_sha256=text_sha256(
                    "world-test/child-injection-failure"
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
                        branch_receipt=branch_receipt(
                            parent, "child-injection-failure"
                        ),
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
                    **changes,
                }
                forged = ContinuousBranchForkReceiptV1(
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
