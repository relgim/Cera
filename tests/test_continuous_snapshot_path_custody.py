from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
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
    CONTINUOUS_ACCEPTED_SNAPSHOT_PATH_POLICY_V2_SHA256,
    ContinuousAcceptedSnapshotPathPlanV1,
    ContinuousReconstructionAcceptedTurnV1,
    ContinuousSessionCoordinator,
    ContinuousSessionReconstructionBundleV1,
    ContinuousSessionRole,
    ContinuousSessionSnapshotReceiptV1,
    ContinuousSessionSnapshotReceiptV2,
    ContinuousSessionSnapshotReceiptV3,
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
from tests.test_continuous_corrections import (
    _AcceptingReaderStage,
    _QueueStage,
    _seed_character,
)
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


def _create_directory_alias(link: Path, target: Path) -> dict[str, object]:
    """Create an exercised directory alias and return capability evidence."""

    if os.name == "nt":
        result = subprocess.run(
            ["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(target)],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise AssertionError(
                "Windows junction capability unavailable: "
                + json.dumps(
                    {
                        "returncode": result.returncode,
                        "stdout": result.stdout.strip(),
                        "stderr": result.stderr.strip(),
                    },
                    sort_keys=True,
                )
            )
        return {
            "mechanism": "windows_junction_mklink_j",
            "returncode": result.returncode,
            "isjunction": os.path.isjunction(link),
            "reparse_tag": int(os.lstat(link).st_reparse_tag),
        }
    os.symlink(target, link, target_is_directory=True)
    return {
        "mechanism": "posix_directory_symlink",
        "islink": link.is_symlink(),
    }


def _remove_directory_alias(path: Path) -> None:
    if os.name == "nt" and os.path.isjunction(path):
        path.rmdir()
    elif path.is_symlink():
        path.unlink()


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

            self.assertIsInstance(receipt, ContinuousSessionSnapshotReceiptV3)
            self.assertTrue(
                receipt.immutable_relative_path.startswith(
                    "PLANNER_SESSION/ACCEPTED/v2/"
                )
            )
            self.assertEqual(
                receipt.path_plan.path_policy_sha256,
                CONTINUOUS_ACCEPTED_SNAPSHOT_PATH_POLICY_V2_SHA256,
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
            self.assertEqual(
                build_schema_registry().decode(to_primitive(receipt)), receipt
            )

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
                reader=_AcceptingReaderStage(),
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

    def test_windows_junction_and_directory_aliases_fail_component_custody(
        self,
    ) -> None:
        for target_location in ("inside_root", "outside_root"):
            with self.subTest(target_location=target_location), TemporaryDirectory() as directory:
                base = Path(directory)
                root = base / "branch"
                root.mkdir()
                target = (
                    root / "alias-target"
                    if target_location == "inside_root"
                    else base / "outside-target"
                )
                target.mkdir()
                link = root / "PLANNER_SESSION"
                capability = _create_directory_alias(link, target)
                try:
                    if os.name == "nt":
                        self.assertEqual(
                            capability,
                            {
                                "mechanism": "windows_junction_mklink_j",
                                "returncode": 0,
                                "isjunction": True,
                                "reparse_tag": 0xA0000003,
                            },
                        )
                    store = ContinuousSessionSnapshotStore(root)
                    with self.assertRaisesRegex(
                        StateConflictError, "no-follow|reparse|identity"
                    ):
                        store.preflight_acceptance_paths(
                            accepted_turn_id="turn-junction",
                            snapshot_sha256="a" * 64,
                        )
                finally:
                    _remove_directory_alias(link)

    def test_final_and_temporary_file_symlinks_fail_closed(self) -> None:
        for leaf_kind in ("immutable_final", "immutable_temporary"):
            with self.subTest(leaf_kind=leaf_kind), TemporaryDirectory() as directory:
                root = Path(directory) / "branch"
                root.mkdir()
                _port, coordinator, envelope, injection = _accepted_session()
                store = ContinuousSessionSnapshotStore(root)
                snapshot = coordinator.snapshot()
                plan = store.preflight_acceptance_paths(
                    accepted_turn_id=envelope.accepted_turn_id,
                    snapshot_sha256=snapshot.snapshot_sha256,
                )
                outside = Path(directory) / f"outside-{leaf_kind}.json"
                outside.write_text("outside\n", encoding="utf-8")
                relative = (
                    plan.immutable_relative_path
                    if leaf_kind == "immutable_final"
                    else plan.immutable_temporary_relative_path
                )
                alias = root / relative
                try:
                    os.symlink(outside, alias, target_is_directory=False)
                except OSError as exc:
                    self.skipTest(
                        "file-symlink capability receipt: "
                        + json.dumps(
                            {
                                "platform": os.name,
                                "error_type": type(exc).__name__,
                                "errno": exc.errno,
                                "winerror": getattr(exc, "winerror", None),
                            },
                            sort_keys=True,
                        )
                    )
                try:
                    with self.assertRaisesRegex(
                        StateConflictError, "no-follow|reparse|identity"
                    ):
                        store.save_for_acceptance(
                            snapshot,
                            accepted_turn_id=envelope.accepted_turn_id,
                            accepted_envelope_sha256=envelope.envelope_sha256,
                            injection_receipt=injection,
                        )
                    self.assertEqual(outside.read_text(encoding="utf-8"), "outside\n")
                finally:
                    if alias.is_symlink():
                        alias.unlink()

    def test_in_root_and_outside_root_hardlink_aliases_fail_closed(self) -> None:
        for target_location in ("inside_root", "outside_root"):
            with self.subTest(target_location=target_location), TemporaryDirectory() as directory:
                base = Path(directory)
                root = base / "branch"
                root.mkdir()
                _port, coordinator, envelope, injection = _accepted_session()
                store = ContinuousSessionSnapshotStore(root)
                snapshot = coordinator.snapshot()
                plan = store.preflight_acceptance_paths(
                    accepted_turn_id=envelope.accepted_turn_id,
                    snapshot_sha256=snapshot.snapshot_sha256,
                )
                source = (
                    root / "hardlink-source.json"
                    if target_location == "inside_root"
                    else base / "outside-hardlink-source.json"
                )
                source.write_text("aliased\n", encoding="utf-8")
                alias = root / plan.immutable_relative_path
                os.link(source, alias)
                with self.assertRaisesRegex(
                    StateConflictError, "hard-link|identity"
                ):
                    store.save_for_acceptance(
                        snapshot,
                        accepted_turn_id=envelope.accepted_turn_id,
                        accepted_envelope_sha256=envelope.envelope_sha256,
                        injection_receipt=injection,
                    )
                self.assertEqual(source.read_text(encoding="utf-8"), "aliased\n")

    def test_alias_insertion_after_preflight_fails_before_temporary_creation(
        self,
    ) -> None:
        with TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "branch"
            root.mkdir()
            outside = base / "outside-race"
            outside.mkdir()
            _port, coordinator, envelope, injection = _accepted_session()
            alias_path = root / "PLANNER_SESSION" / "ACCEPTED" / "v2"
            inserted = False

            def failpoint(stage: str) -> None:
                nonlocal inserted
                if stage == "after_acceptance_path_preflight" and not inserted:
                    alias_path.rmdir()
                    _create_directory_alias(alias_path, outside)
                    inserted = True

            store = ContinuousSessionSnapshotStore(root, failpoint=failpoint)
            try:
                with self.assertRaisesRegex(
                    StateConflictError, "no-follow|reparse|identity"
                ):
                    store.save_for_acceptance(
                        coordinator.snapshot(),
                        accepted_turn_id=envelope.accepted_turn_id,
                        accepted_envelope_sha256=envelope.envelope_sha256,
                        injection_receipt=injection,
                    )
                self.assertTrue(inserted)
                self.assertEqual(tuple(outside.iterdir()), ())
            finally:
                if inserted:
                    _remove_directory_alias(alias_path)

    def test_parent_replacement_attempt_during_publication_fails_closed(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory) / "branch"
            root.mkdir()
            _port, coordinator, envelope, injection = _accepted_session()
            parent = root / "PLANNER_SESSION" / "ACCEPTED" / "v2"
            backup = parent.with_name("v2-replaced")
            attempted = False
            replaced = False

            def failpoint(stage: str) -> None:
                nonlocal attempted, replaced
                if stage != "before_immutable_snapshot_replace" or attempted:
                    return
                attempted = True
                try:
                    os.replace(parent, backup)
                except OSError as exc:
                    raise StateConflictError(
                        "adversarial parent replacement was blocked by custody lock"
                    ) from exc
                replaced = True
                parent.mkdir()

            store = ContinuousSessionSnapshotStore(root, failpoint=failpoint)
            with self.assertRaisesRegex(
                StateConflictError, "parent|custody|identity|replacement"
            ):
                store.save_for_acceptance(
                    coordinator.snapshot(),
                    accepted_turn_id=envelope.accepted_turn_id,
                    accepted_envelope_sha256=envelope.envelope_sha256,
                    injection_receipt=injection,
                )
            self.assertTrue(attempted)
            self.assertFalse(
                (root / "PLANNER_SESSION" / "SESSION_SNAPSHOT.json").exists()
            )
            if replaced:
                self.assertTrue(backup.is_dir())

    def test_occupied_final_and_temporary_paths_fail_before_publication(self) -> None:
        for occupied in ("current_temp", "immutable_temp", "immutable_directory"):
            with self.subTest(occupied=occupied), TemporaryDirectory() as directory:
                root = Path(directory) / "branch"
                root.mkdir()
                _port, coordinator, envelope, injection = _accepted_session()
                store = ContinuousSessionSnapshotStore(root)
                snapshot = coordinator.snapshot()
                plan = store.preflight_acceptance_paths(
                    accepted_turn_id=envelope.accepted_turn_id,
                    snapshot_sha256=snapshot.snapshot_sha256,
                )
                relative = {
                    "current_temp": plan.current_temporary_relative_path,
                    "immutable_temp": plan.immutable_temporary_relative_path,
                    "immutable_directory": plan.immutable_relative_path,
                }[occupied]
                occupied_path = root / relative
                if occupied == "immutable_directory":
                    occupied_path.mkdir()
                else:
                    occupied_path.write_text("occupied\n", encoding="utf-8")
                with self.assertRaisesRegex(
                    StateConflictError, "occupied|kind|no-follow"
                ):
                    store.save_for_acceptance(
                        snapshot,
                        accepted_turn_id=envelope.accepted_turn_id,
                        accepted_envelope_sha256=envelope.envelope_sha256,
                        injection_receipt=injection,
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
            self.assertIn(ContinuousSessionSnapshotReceiptV3.SCHEMA_VERSION, versions)
            self.assertIn("cera.continuous_accepted_snapshot_path_plan.v1", versions)
            self.assertIn("cera.continuous_accepted_snapshot_path_plan.v2", versions)

    def test_historical_v2_receipt_remains_decodable_under_no_follow_load(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            _port, coordinator, envelope, injection = _accepted_session()
            snapshot = coordinator.snapshot()
            snapshot_envelope = {
                "schema_version": "cera.continuous_session_snapshot_envelope.v1",
                "snapshot": to_primitive(snapshot),
                "snapshot_sha256": snapshot.snapshot_sha256,
            }
            encoded_snapshot = canonical_bytes(snapshot_envelope) + b"\n"
            immutable_relative = (
                "PLANNER_SESSION/ACCEPTED/v2/"
                + text_sha256(envelope.accepted_turn_id)[:16]
                + "-"
                + snapshot.snapshot_sha256[:24]
                + ".json"
            )
            immutable_temporary_relative = immutable_relative + ".tmp"
            current_relative = "PLANNER_SESSION/SESSION_SNAPSHOT.json"
            current_temporary_relative = current_relative + ".tmp"
            paths = tuple(
                root / value
                for value in (
                    current_relative,
                    current_temporary_relative,
                    immutable_relative,
                    immutable_temporary_relative,
                )
            )
            plan_payload = {
                "schema_version": ContinuousAcceptedSnapshotPathPlanV1.SCHEMA_VERSION,
                "path_policy_sha256": CONTINUOUS_ACCEPTED_SNAPSHOT_PATH_POLICY_SHA256,
                "maximum_resolved_path_characters": (
                    CONTINUOUS_ACCEPTED_SNAPSHOT_MAX_RESOLVED_CHARS
                ),
                "current_relative_path": current_relative,
                "current_temporary_relative_path": current_temporary_relative,
                "immutable_relative_path": immutable_relative,
                "immutable_temporary_relative_path": immutable_temporary_relative,
                "current_resolved_path_characters": len(str(paths[0])),
                "current_temporary_resolved_path_characters": len(str(paths[1])),
                "immutable_resolved_path_characters": len(str(paths[2])),
                "immutable_temporary_resolved_path_characters": len(str(paths[3])),
            }
            path_plan = ContinuousAcceptedSnapshotPathPlanV1(
                **plan_payload,
                plan_sha256=canonical_sha256(plan_payload),
            )
            historical_envelope = {
                "schema_version": "cera.continuous_session_snapshot_envelope.v2",
                "path_policy_sha256": CONTINUOUS_ACCEPTED_SNAPSHOT_PATH_POLICY_SHA256,
                "accepted_turn_id": envelope.accepted_turn_id,
                "accepted_turn_id_sha256": text_sha256(envelope.accepted_turn_id),
                "accepted_envelope_sha256": envelope.envelope_sha256,
                "provider_thread_sha256": snapshot.handle.provider_thread_id_sha256,
                "injection_receipt": to_primitive(injection),
                "encoded_snapshot_file_sha256": bytes_sha256(encoded_snapshot),
                "snapshot_envelope": snapshot_envelope,
            }
            encoded = canonical_bytes(historical_envelope) + b"\n"
            immutable_path = root / immutable_relative
            immutable_path.parent.mkdir(parents=True)
            immutable_path.write_bytes(encoded)
            receipt_payload = {
                "schema_version": ContinuousSessionSnapshotReceiptV2.SCHEMA_VERSION,
                "world_id": snapshot.compatibility.world_id,
                "branch_id": snapshot.compatibility.branch_id,
                "role": snapshot.compatibility.role,
                "accepted_turn_id": envelope.accepted_turn_id,
                "accepted_turn_id_sha256": text_sha256(envelope.accepted_turn_id),
                "accepted_envelope_sha256": envelope.envelope_sha256,
                "provider_thread_sha256": snapshot.handle.provider_thread_id_sha256,
                "snapshot_sha256": snapshot.snapshot_sha256,
                "injection_receipt": injection,
                "injection_operation_receipt_sha256": (
                    injection.operation_receipt_sha256
                ),
                "encoded_snapshot_file_sha256": bytes_sha256(encoded_snapshot),
                "immutable_relative_path": immutable_relative,
                "immutable_file_sha256": bytes_sha256(encoded),
                "current_relative_path": current_relative,
                "path_plan": path_plan,
            }
            receipt = ContinuousSessionSnapshotReceiptV2(
                **receipt_payload,
                receipt_sha256=canonical_sha256(
                    {
                        **receipt_payload,
                        "role": snapshot.compatibility.role.value,
                        "injection_receipt": to_primitive(injection),
                        "path_plan": to_primitive(path_plan),
                    }
                ),
            )
            self.assertEqual(
                ContinuousSessionSnapshotStore(root).load_immutable(receipt),
                snapshot,
            )


if __name__ == "__main__":
    unittest.main()
