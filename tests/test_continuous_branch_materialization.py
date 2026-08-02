from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from cera.continuous.evidence import (
    StableAcceptedContextReferenceStore,
    build_character_summary_envelope,
)
from cera.continuous.runtime import (
    ContinuousShadowTurnCoordinator,
    ContinuousTurnRequestV1,
)
from cera.continuous.sessions import (
    ContinuousSessionCoordinator,
    ContinuousReconstructionAcceptedTurnV1,
    ContinuousSessionReconstructionBundleV1,
    ContinuousSessionRole,
    InMemoryContinuousStoredSessionPort,
)
from cera.continuous.world import ContinuousWorldStore
from cera.creator_review.models import CreatorReviewAction
from cera.errors import StateConflictError
from cera.serialization import canonical_bytes, canonical_sha256, text_sha256

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


class ContinuousBranchMaterializationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.world = ContinuousWorldStore(self.root / "worlds")
        _seed_character(self.world)
        for relative_path, payload in (
            (
                "Relationships/Sakura_Ted.json",
                {
                    "schema_version": "cera.continuous_relationship.v1",
                    "relationship_id": "relationship:sakura_ted",
                    "participant_ids": [
                        "character:sakura_hanezawa",
                        "character:ted",
                    ],
                },
            ),
            (
                "Rules/House.json",
                {
                    "schema_version": "cera.continuous_rule.v1",
                    "rule_id": "rule:house",
                },
            ),
            (
                "Locations/Entry.json",
                {
                    "schema_version": "cera.continuous_location.v1",
                    "location_id": "location:entry",
                },
            ),
            (
                "Scenes/Arrival.json",
                {
                    "schema_version": "cera.continuous_scene.v1",
                    "scene_id": "scene-001",
                },
            ),
        ):
            self.world.seed_active_json(
                "world-test", "main", relative_path, payload
            )
        message = "Hello, my name is Ted."
        self.ingress = make_ingress_authority(
            self.root / "ingress", (message, "turn-001")
        )
        self.port = InMemoryContinuousStoredSessionPort()
        parent_compatibility = replace(
            session_compatibility(ContinuousSessionRole.PLANNER),
            world_directory_identity_sha256=(
                self.world.branch_directory_identity_sha256("world-test", "main")
            ),
        )
        self.parent = ContinuousSessionCoordinator(parent_compatibility, self.port)
        validator = ContinuousSessionCoordinator(
            replace(
                session_compatibility(ContinuousSessionRole.VALIDATOR),
                world_directory_identity_sha256=(
                    self.world.branch_directory_identity_sha256(
                        "world-test", "main"
                    )
                ),
            ),
            self.port,
        )
        self.runtime = ContinuousShadowTurnCoordinator(
            world=self.world,
            planner_session=self.parent,
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
            ingress_authority=self.ingress,
        )
        character_path = (
            self.world.branch_root("world-test", "main")
            / "ACTIVE"
            / "Characters"
            / "Sakura.json"
        )
        self.runtime.prepare(
            ContinuousTurnRequestV1(
                world_id="world-test",
                branch_id="main",
                scene_id="scene-001",
                turn_id="turn-001",
                user_message=message,
                **ingress_reference(self.ingress, message, "turn-001"),
                character_summaries=(
                    character_summary(
                        source_sha256=text_sha256(
                            character_path.read_text(encoding="utf-8")
                        )
                    ),
                ),
            )
        )
        self.runtime.apply_creator_action("turn-001", CreatorReviewAction.ACCEPT)
        current_summary = build_character_summary_envelope(
            branch_root=self.world.branch_root("world-test", "main"),
            source_path="ACTIVE/Characters/Sakura.json",
            character_id="character:sakura_hanezawa",
        )
        self.parent.record_character_summary_deliveries(
            (current_summary,),
            ("material_revision_change",),
            planner_prompt_sha256=text_sha256("current summary prompt"),
        )
        self.current_summary = current_summary

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _target(self, child_branch: str):
        return replace(
            self.parent.compatibility,
            branch_id=child_branch,
            world_directory_identity_sha256=(
                self.world.branch_directory_identity_sha256(
                    "world-test", child_branch
                )
            ),
        )

    def _materialize(self, child_branch: str):
        target = self._target(child_branch)
        materialization = self.runtime.materialize_planner_branch(
            target_compatibility=target
        )
        branch_receipt = self.parent.build_branch_fork_receipt(
            target,
            branch_materialization_receipt_sha256=(
                materialization.receipt_sha256
            ),
        )
        return target, materialization, branch_receipt

    def _fork_count(self) -> int:
        return sum(value[0] == "fork_branch" for value in self.port.operations)

    @staticmethod
    def _change_json(path: Path, **changes) -> None:
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload.update(changes)
        path.write_bytes(canonical_bytes(payload) + b"\n")

    def test_valid_materialization_binds_complete_snapshot_and_first_lean_fork(self) -> None:
        target, materialization, branch_receipt = self._materialize("child-valid")
        categories = {
            entry.record_category
            for entry in materialization.child_initial_active_manifest
        }
        self.assertTrue(
            {
                "characters",
                "relationships",
                "rules",
                "locations",
                "events",
                "scenes",
                "world_state",
                "world_index",
            }.issubset(categories)
        )
        self.assertEqual(
            materialization.parent_accepted_checkpoint_manifest,
            materialization.child_accepted_checkpoint_manifest,
        )
        self.assertNotEqual(
            materialization.parent_world_directory_identity_sha256,
            materialization.child_world_directory_identity_sha256,
        )
        forked = self.runtime.fork_planner_session_for_branch(
            target_compatibility=target,
            materialization_receipt=materialization,
            branch_receipt=branch_receipt,
        )
        self.assertEqual(self._fork_count(), 1)
        self.assertEqual(
            len(forked.coordinator.snapshot().character_summary_deliveries), 1
        )

    def test_over_budget_child_receipt_path_fails_before_materialization(self) -> None:
        child_branch = "c" + ("x" * 95)
        parent_root = self.world.branch_root("world-test", "main")
        child_root = self.world.branch_root("world-test", child_branch)
        parent_tree_before = self.world.tree_sha256(parent_root)
        with self.assertRaisesRegex(StateConflictError, "legacy path budget"):
            self.runtime.materialize_planner_branch(
                target_compatibility=self._target(child_branch)
            )
        self.assertFalse(child_root.exists())
        self.assertEqual(self.world.tree_sha256(parent_root), parent_tree_before)

    def test_empty_and_partial_children_are_not_materializable(self) -> None:
        self.world.initialize("world-test", "child-empty")
        with self.assertRaisesRegex(StateConflictError, "previously nonexistent"):
            self.runtime.materialize_planner_branch(
                target_compatibility=self._target("child-empty")
            )
        partial = self.world.initialize("world-test", "child-partial")
        (partial / "ACTIVE" / "Rules" / "partial.json").write_text(
            "{}\n", encoding="utf-8"
        )
        with self.assertRaisesRegex(StateConflictError, "previously nonexistent"):
            self.runtime.materialize_planner_branch(
                target_compatibility=self._target("child-partial")
            )
        self.assertEqual(self._fork_count(), 0)

    def test_every_active_category_and_index_tamper_fails_before_transport(self) -> None:
        relative_paths = (
            "Characters/Sakura.json",
            "Relationships/Sakura_Ted.json",
            "Rules/House.json",
            "Locations/Entry.json",
            next(
                value.relative_to(
                    self.world.branch_root("world-test", "main") / "ACTIVE"
                ).as_posix()
                for value in (
                    self.world.branch_root("world-test", "main")
                    / "ACTIVE"
                    / "Events"
                ).glob("*.json")
            ),
            "Scenes/Arrival.json",
            "WORLD_STATE.json",
            "WORLD_INDEX.jsonl",
        )
        for index, relative_path in enumerate(relative_paths, 1):
            with self.subTest(relative_path=relative_path):
                target, materialization, branch_receipt = self._materialize(
                    f"child-tamper-{index}"
                )
                path = (
                    self.world.branch_root("world-test", target.branch_id)
                    / "ACTIVE"
                    / relative_path
                )
                if path.suffix == ".json":
                    self._change_json(path, tampered=True)
                else:
                    path.write_text(
                        path.read_text(encoding="utf-8") + "{}\n",
                        encoding="utf-8",
                    )
                before = self._fork_count()
                with self.assertRaisesRegex(StateConflictError, "snapshot changed"):
                    self.runtime.fork_planner_session_for_branch(
                        target_compatibility=target,
                        materialization_receipt=materialization,
                        branch_receipt=branch_receipt,
                    )
                self.assertEqual(self._fork_count(), before)

    def test_wrong_directory_stale_parent_foreign_and_replayed_receipts_fail_closed(self) -> None:
        wrong = replace(
            self._target("child-wrong-directory"),
            world_directory_identity_sha256=text_sha256("foreign directory"),
        )
        with self.assertRaisesRegex(StateConflictError, "actual world directories"):
            self.runtime.materialize_planner_branch(target_compatibility=wrong)

        target, materialization, branch_receipt = self._materialize("child-stale")
        parent_rule = (
            self.world.branch_root("world-test", "main")
            / "ACTIVE"
            / "Rules"
            / "House.json"
        )
        original = parent_rule.read_bytes()
        self._change_json(parent_rule, stale_after_materialization=True)
        with self.assertRaisesRegex(StateConflictError, "snapshot changed"):
            self.runtime.fork_planner_session_for_branch(
                target_compatibility=target,
                materialization_receipt=materialization,
                branch_receipt=branch_receipt,
            )
        parent_rule.write_bytes(original)

        sibling_target, sibling_materialization, sibling_receipt = self._materialize(
            "child-sibling"
        )
        with self.assertRaisesRegex(StateConflictError, "branch custody"):
            self.runtime.fork_planner_session_for_branch(
                target_compatibility=sibling_target,
                materialization_receipt=materialization,
                branch_receipt=sibling_receipt,
            )
        with self.assertRaisesRegex(StateConflictError, "branch custody"):
            self.runtime.fork_planner_session_for_branch(
                target_compatibility=target,
                materialization_receipt=sibling_materialization,
                branch_receipt=branch_receipt,
            )
        self.assertEqual(self._fork_count(), 0)

    def test_scene_head_order_checkpoint_artifact_and_summary_tamper_fail_pretransport(self) -> None:
        cases = (
            ("scene", "WORLD_STATE.json", {"current_scene_id": "scene-tampered"}),
            ("head", "WORLD_STATE.json", {"accepted_turn_ids": []}),
            (
                "summary-content",
                "Characters/Sakura.json",
                {"reasoning_summary": "stale changed summary source"},
            ),
            ("revision", "Characters/Sakura.json", {"_cera_revision": 999}),
            (
                "owner",
                "Characters/Sakura.json",
                {"character_id": "character:foreign"},
            ),
        )
        for label, relative, changes in cases:
            with self.subTest(label=label):
                target, materialization, branch_receipt = self._materialize(
                    f"child-semantic-{label}"
                )
                path = (
                    self.world.branch_root("world-test", target.branch_id)
                    / "ACTIVE"
                    / relative
                )
                self._change_json(path, **changes)
                before = self._fork_count()
                with self.assertRaises(StateConflictError):
                    self.runtime.fork_planner_session_for_branch(
                        target_compatibility=target,
                        materialization_receipt=materialization,
                        branch_receipt=branch_receipt,
                    )
                self.assertEqual(self._fork_count(), before)

        target, materialization, branch_receipt = self._materialize(
            "child-summary-missing"
        )
        (
            self.world.branch_root("world-test", target.branch_id)
            / "ACTIVE"
            / "Characters"
            / "Sakura.json"
        ).unlink()
        with self.assertRaises(StateConflictError):
            self.runtime.fork_planner_session_for_branch(
                target_compatibility=target,
                materialization_receipt=materialization,
                branch_receipt=branch_receipt,
            )

        target, materialization, branch_receipt = self._materialize(
            "child-checkpoint-artifact"
        )
        checkpoint = (
            self.world.branch_root("world-test", target.branch_id)
            / "CANDIDATES"
            / "turn-001"
            / "PROMOTION_RECEIPT.json"
        )
        self._change_json(checkpoint, tampered=True)
        with self.assertRaisesRegex(StateConflictError, "snapshot changed"):
            self.runtime.fork_planner_session_for_branch(
                target_compatibility=target,
                materialization_receipt=materialization,
                branch_receipt=branch_receipt,
            )
        self.assertEqual(self._fork_count(), 0)

    def _reconstruction_bundle(self, child_branch: str):
        parent_root = self.world.branch_root("world-test", "main")
        source_receipt, source_references = StableAcceptedContextReferenceStore(
            parent_root
        ).load(
            "turn-001",
            provider_thread_sha256=(
                self.parent.ensure_session().provider_thread_id_sha256
            ),
        )
        envelope = self.world.accepted_final_envelope(
            "world-test", "main", "turn-001"
        )
        accepted = ContinuousReconstructionAcceptedTurnV1(
            envelope=envelope,
            synchronization_receipt_sha256=(
                source_receipt.synchronization_receipt_sha256
            ),
            stable_reference_descriptors=tuple(
                value.injection_descriptor() for value in source_references
            ),
        )
        ancestry = canonical_sha256(
            {
                "world_id": "world-test",
                "branch_id": child_branch,
                "accepted_tail": (
                    (
                        envelope.accepted_turn_id,
                        envelope.envelope_sha256,
                        accepted.synchronization_receipt_sha256,
                    ),
                ),
            }
        )
        return ContinuousSessionReconstructionBundleV1(
            schema_version=ContinuousSessionReconstructionBundleV1.SCHEMA_VERSION,
            world_id="world-test",
            branch_id=child_branch,
            accepted_tail=(accepted,),
            character_summaries=(self.current_summary,),
            accepted_ancestry_sha256=ancestry,
            reconstruction_reason="non_forkable_branch",
        )

    def test_cross_branch_reconstruction_requires_valid_materialization_before_create(self) -> None:
        invalid_target = self._target("child-reconstruct-invalid")
        invalid_bundle = self._reconstruction_bundle(invalid_target.branch_id)
        invalid_materialization = self.runtime.materialize_planner_branch(
            target_compatibility=invalid_target,
            required_character_summaries=invalid_bundle.character_summaries,
        )
        invalid_branch_receipt = self.parent.build_branch_fork_receipt(
            invalid_target,
            branch_materialization_receipt_sha256=(
                invalid_materialization.receipt_sha256
            ),
        )
        self._change_json(
            self.world.branch_root("world-test", invalid_target.branch_id)
            / "ACTIVE"
            / "Rules"
            / "House.json",
            changed_before_reconstruction=True,
        )
        creates_before = sum(value[0] == "create" for value in self.port.operations)
        with self.assertRaisesRegex(StateConflictError, "snapshot changed"):
            self.runtime.reconstruct_planner_session(
                bundle=invalid_bundle,
                expected_compatibility=invalid_target,
                branch_receipt=invalid_branch_receipt,
                materialization_receipt=invalid_materialization,
            )
        self.assertEqual(
            sum(value[0] == "create" for value in self.port.operations),
            creates_before,
        )

        valid_target = self._target("child-reconstruct-valid")
        valid_bundle = self._reconstruction_bundle(valid_target.branch_id)
        valid_materialization = self.runtime.materialize_planner_branch(
            target_compatibility=valid_target,
            required_character_summaries=valid_bundle.character_summaries,
        )
        valid_branch_receipt = self.parent.build_branch_fork_receipt(
            valid_target,
            branch_materialization_receipt_sha256=(
                valid_materialization.receipt_sha256
            ),
        )
        initialization = self.runtime.reconstruct_planner_session(
            bundle=valid_bundle,
            expected_compatibility=valid_target,
            branch_receipt=valid_branch_receipt,
            materialization_receipt=valid_materialization,
        )
        self.assertEqual(
            sum(value[0] == "create" for value in self.port.operations),
            creates_before + 1,
        )
        self.assertEqual(initialization.branch_id, valid_target.branch_id)


if __name__ == "__main__":
    unittest.main()
