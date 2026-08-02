from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest import mock

from cera.continuous.contracts import AcceptedFinalSequenceEnvelopeV1
from cera.continuous.runtime import (
    ContinuousShadowTurnCoordinator,
    ContinuousTurnRequestV1,
)
from cera.continuous.sessions import (
    CONTINUOUS_ACCEPTED_SNAPSHOT_MAX_RESOLVED_CHARS,
    CONTINUOUS_ACCEPTED_SNAPSHOT_PATH_POLICY_SHA256,
    ContinuousReconstructionAcceptedTurnV1,
    ContinuousSessionCoordinator,
    ContinuousSessionReconstructionBundleV1,
    ContinuousSessionRole,
    ContinuousSessionSnapshotReceiptV1,
    ContinuousSessionSnapshotReceiptV2,
    ContinuousSessionSnapshotStore,
    InMemoryContinuousStoredSessionPort,
)
from cera.errors import StateConflictError
from cera.creator_review.models import CreatorReviewAction
from cera.registry import build_schema_registry
from cera.serialization import (
    bytes_sha256,
    canonical_bytes,
    canonical_sha256,
    text_sha256,
    to_primitive,
)
from tests.test_continuous_planner_validator import branch_receipt
from tests.test_continuous_corrections import _QueueStage, _seed_character
from tests.test_continuous_world import (
    character_summary,
    composer_draft,
    final_sequence,
    ingress_reference,
    make_ingress_authority,
    package,
    rich_sequence,
    session_compatibility,
)
from cera.continuous.world import ContinuousWorldStore


CYCLE_22_BRANCH_ROOT_CHARACTERS = 133


def _root_with_minimum_characters(base: Path, minimum: int) -> Path:
    root = base.resolve()
    index = 0
    while len(str(root)) < minimum:
        root /= f"path-budget-segment-{index:02d}"
        index += 1
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def _accepted_session():
    port = InMemoryContinuousStoredSessionPort()
    coordinator = ContinuousSessionCoordinator(
        session_compatibility(ContinuousSessionRole.PLANNER), port
    )
    envelope = AcceptedFinalSequenceEnvelopeV1(
        schema_version=AcceptedFinalSequenceEnvelopeV1.SCHEMA_VERSION,
        accepted_turn_id="turn-001",
        user_message="Hello.",
        complete_final_sequence=final_sequence(),
        acceptance_receipt_sha256="8" * 64,
    )
    coordinator.record_planner_provisional(
        envelope.accepted_turn_id, rich_sequence().sequence_sha256
    )
    coordinator.append_accepted_final_sequence(envelope)
    injection = coordinator.synchronize_accepted_final_sequence_with_receipt(envelope)
    assert injection is not None
    return port, coordinator, envelope, injection


class ContinuousSnapshotPathCustodyTests(unittest.TestCase):
    def test_equal_or_longer_cycle22_root_save_load_restart_reconstruction_and_fork(
        self,
    ) -> None:
        with TemporaryDirectory() as directory:
            root = _root_with_minimum_characters(
                Path(directory), CYCLE_22_BRANCH_ROOT_CHARACTERS
            )
            self.assertGreaterEqual(len(str(root)), CYCLE_22_BRANCH_ROOT_CHARACTERS)
            port, coordinator, envelope, injection = _accepted_session()
            store = ContinuousSessionSnapshotStore(root)
            snapshot = coordinator.snapshot()
            receipt = store.save_for_acceptance(
                snapshot,
                accepted_turn_id=envelope.accepted_turn_id,
                accepted_envelope_sha256=envelope.envelope_sha256,
                injection_receipt=injection,
            )

            self.assertIsInstance(receipt, ContinuousSessionSnapshotReceiptV2)
            self.assertTrue(
                receipt.immutable_relative_path.startswith(
                    "PLANNER_SESSION/ACCEPTED/v2/"
                )
            )
            self.assertEqual(
                receipt.path_plan.path_policy_sha256,
                CONTINUOUS_ACCEPTED_SNAPSHOT_PATH_POLICY_SHA256,
            )
            self.assertLessEqual(
                max(
                    receipt.path_plan.current_resolved_path_characters,
                    receipt.path_plan.current_temporary_resolved_path_characters,
                    receipt.path_plan.immutable_resolved_path_characters,
                    receipt.path_plan.immutable_temporary_resolved_path_characters,
                ),
                CONTINUOUS_ACCEPTED_SNAPSHOT_MAX_RESOLVED_CHARS,
            )
            historical_locator = (
                root
                / "PLANNER_SESSION"
                / "ACCEPTED"
                / ("a" * 24)
                / (("b" * 64) + ".snapshot.json")
            )
            self.assertGreaterEqual(len(str(historical_locator)), 262)

            immutable_path = root / receipt.immutable_relative_path
            outer = json.loads(immutable_path.read_text(encoding="utf-8"))
            self.assertEqual(outer["accepted_turn_id"], envelope.accepted_turn_id)
            self.assertEqual(
                outer["provider_thread_sha256"],
                snapshot.handle.provider_thread_id_sha256,
            )
            self.assertEqual(outer["injection_receipt"], to_primitive(injection))
            self.assertEqual(
                outer["encoded_snapshot_file_sha256"],
                bytes_sha256(
                    canonical_bytes(outer["snapshot_envelope"]) + b"\n"
                ),
            )
            self.assertEqual(
                receipt.immutable_file_sha256,
                bytes_sha256(immutable_path.read_bytes()),
            )
            self.assertEqual(receipt.injection_receipt, injection)

            self.assertEqual(store.load_immutable(receipt), snapshot)
            restarted_snapshot = ContinuousSessionSnapshotStore(root).load(
                ContinuousSessionRole.PLANNER
            )
            restarted = ContinuousSessionCoordinator.resume_compatible(
                restarted_snapshot,
                port,
                expected_compatibility=snapshot.compatibility,
            )
            self.assertEqual(restarted.snapshot().accepted_turn_ids, ("turn-001",))

            child_branch = "accepted-checkpoint-child"
            custody = branch_receipt(restarted, child_branch)
            child_compatibility = replace(
                restarted.compatibility,
                branch_id=child_branch,
                world_directory_identity_sha256=text_sha256(
                    f"{restarted.compatibility.world_id}/{child_branch}"
                ),
            )
            forked = restarted.fork_for_branch(
                child_compatibility,
                branch_receipt=custody,
            )
            self.assertEqual(forked.snapshot().accepted_turn_ids, ("turn-001",))

            port.archive(restarted.ensure_session(), "long-root-reconstruction")
            reconstructed_turn = ContinuousReconstructionAcceptedTurnV1(
                envelope=envelope,
                synchronization_receipt_sha256=text_sha256("long-root-sync"),
                stable_reference_descriptors=(),
            )
            ancestry = canonical_sha256(
                {
                    "world_id": restarted.compatibility.world_id,
                    "branch_id": restarted.compatibility.branch_id,
                    "accepted_tail": (
                        (
                            envelope.accepted_turn_id,
                            envelope.envelope_sha256,
                            reconstructed_turn.synchronization_receipt_sha256,
                        ),
                    ),
                }
            )
            bundle = ContinuousSessionReconstructionBundleV1(
                schema_version=ContinuousSessionReconstructionBundleV1.SCHEMA_VERSION,
                world_id=restarted.compatibility.world_id,
                branch_id=restarted.compatibility.branch_id,
                accepted_tail=(reconstructed_turn,),
                character_summaries=(),
                accepted_ancestry_sha256=ancestry,
                reconstruction_reason="lost_thread",
            )
            rebuilt = ContinuousSessionCoordinator.reconstruct_new_thread(
                port=port,
                expected_compatibility=restarted.compatibility,
                base_instructions="",
                bundle=bundle,
                parent_provider_thread_sha256=(
                    restarted_snapshot.handle.provider_thread_id_sha256
                ),
            )
            self.assertEqual(rebuilt.snapshot().accepted_turn_ids, ("turn-001",))

    def test_compact_locator_collision_and_tamper_fail_closed(self) -> None:
        with TemporaryDirectory() as directory:
            root = _root_with_minimum_characters(
                Path(directory), CYCLE_22_BRANCH_ROOT_CHARACTERS
            )
            _port, coordinator, envelope, injection = _accepted_session()
            store = ContinuousSessionSnapshotStore(root)
            snapshot = coordinator.snapshot()
            receipt = store.save_for_acceptance(
                snapshot,
                accepted_turn_id=envelope.accepted_turn_id,
                accepted_envelope_sha256=envelope.envelope_sha256,
                injection_receipt=injection,
            )
            immutable_path = root / receipt.immutable_relative_path
            original = immutable_path.read_bytes()
            replayed = store.save_for_acceptance(
                snapshot,
                accepted_turn_id=envelope.accepted_turn_id,
                accepted_envelope_sha256=envelope.envelope_sha256,
                injection_receipt=injection,
            )
            self.assertEqual(replayed, receipt)
            self.assertEqual(immutable_path.read_bytes(), original)
            collision = json.loads(original.decode("utf-8"))
            collision["accepted_turn_id_sha256"] = "f" * 64
            immutable_path.write_bytes(canonical_bytes(collision) + b"\n")
            with self.assertRaisesRegex(StateConflictError, "locator collision"):
                store.save_for_acceptance(
                    snapshot,
                    accepted_turn_id=envelope.accepted_turn_id,
                    accepted_envelope_sha256=envelope.envelope_sha256,
                    injection_receipt=injection,
                )

            immutable_path.write_bytes(original + b" ")
            with self.assertRaisesRegex(StateConflictError, "bytes changed"):
                store.load_immutable(receipt)

    def test_path_budget_failure_precedes_any_acceptance_file_mutation(self) -> None:
        with TemporaryDirectory() as directory:
            root = _root_with_minimum_characters(Path(directory), 220)
            _port, coordinator, envelope, injection = _accepted_session()
            store = ContinuousSessionSnapshotStore(root)
            with self.assertRaisesRegex(StateConflictError, "legacy path budget"):
                store.save_for_acceptance(
                    coordinator.snapshot(),
                    accepted_turn_id=envelope.accepted_turn_id,
                    accepted_envelope_sha256=envelope.envelope_sha256,
                    injection_receipt=injection,
                )
            self.assertFalse((root / "PLANNER_SESSION").exists())

    def test_runtime_preflights_capacity_before_world_promotion(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            world = ContinuousWorldStore(root / "worlds")
            _seed_character(world)
            message = "Hello, my name is Ted."
            ingress = make_ingress_authority(
                root / "ingress", (message, "turn-001")
            )
            port = InMemoryContinuousStoredSessionPort()
            planner = ContinuousSessionCoordinator(
                session_compatibility(ContinuousSessionRole.PLANNER), port
            )
            validator = ContinuousSessionCoordinator(
                session_compatibility(ContinuousSessionRole.VALIDATOR), port
            )
            runtime = ContinuousShadowTurnCoordinator(
                world=world,
                planner_session=planner,
                validator_session=validator,
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
            runtime.prepare(
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
            state_path = (
                world.branch_root("world-test", "main")
                / "ACTIVE"
                / "WORLD_STATE.json"
            )
            state_before = state_path.read_bytes()
            with mock.patch.object(
                ContinuousSessionSnapshotStore,
                "preflight_acceptance_capacity",
                side_effect=StateConflictError("legacy path budget"),
            ), mock.patch.object(
                world,
                "apply_creator_action",
                wraps=world.apply_creator_action,
            ) as apply_creator_action:
                with self.assertRaisesRegex(StateConflictError, "legacy path budget"):
                    runtime.apply_creator_action(
                        "turn-001", CreatorReviewAction.ACCEPT
                    )
                apply_creator_action.assert_not_called()
            self.assertEqual(state_path.read_bytes(), state_before)
            self.assertFalse(
                (
                    world.branch_root("world-test", "main")
                    / ".acceptance-turn-001"
                ).exists()
            )

    def test_historical_v1_receipt_and_path_remain_decodable(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            _port, coordinator, envelope, injection = _accepted_session()
            snapshot = coordinator.snapshot()
            legacy_envelope = {
                "schema_version": "cera.continuous_session_snapshot_envelope.v1",
                "snapshot": to_primitive(snapshot),
                "snapshot_sha256": snapshot.snapshot_sha256,
            }
            encoded = canonical_bytes(legacy_envelope) + b"\n"
            immutable_relative = (
                "PLANNER_SESSION/ACCEPTED/"
                + text_sha256(envelope.accepted_turn_id)[:24]
                + "/"
                + snapshot.snapshot_sha256
                + ".snapshot.json"
            )
            immutable_path = root / immutable_relative
            immutable_path.parent.mkdir(parents=True, exist_ok=True)
            immutable_path.write_bytes(encoded)
            payload = {
                "schema_version": ContinuousSessionSnapshotReceiptV1.SCHEMA_VERSION,
                "world_id": snapshot.compatibility.world_id,
                "branch_id": snapshot.compatibility.branch_id,
                "role": snapshot.compatibility.role,
                "accepted_turn_id": envelope.accepted_turn_id,
                "accepted_envelope_sha256": envelope.envelope_sha256,
                "provider_thread_sha256": snapshot.handle.provider_thread_id_sha256,
                "snapshot_sha256": snapshot.snapshot_sha256,
                "injection_operation_receipt_sha256": (
                    injection.operation_receipt_sha256
                ),
                "immutable_relative_path": immutable_relative,
                "immutable_file_sha256": bytes_sha256(encoded),
                "current_relative_path": "PLANNER_SESSION/SESSION_SNAPSHOT.json",
            }
            receipt = ContinuousSessionSnapshotReceiptV1(
                **payload,
                receipt_sha256=canonical_sha256(
                    {**payload, "role": snapshot.compatibility.role.value}
                ),
            )
            self.assertEqual(
                ContinuousSessionSnapshotStore(root).load_immutable(receipt),
                snapshot,
            )
            versions = build_schema_registry().versions
            self.assertIn(ContinuousSessionSnapshotReceiptV1.SCHEMA_VERSION, versions)
            self.assertIn(ContinuousSessionSnapshotReceiptV2.SCHEMA_VERSION, versions)
            self.assertIn("cera.continuous_accepted_snapshot_path_plan.v1", versions)


if __name__ == "__main__":
    unittest.main()
