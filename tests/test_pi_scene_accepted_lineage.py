from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from cera.cognition.contracts import ProvisionalDependencyV1, ProvisionalTruthValue
from cera.cognition.validation import validate_cognition_plan
from cera.errors import StateConflictError
from cera.pi_scene.review_store import LeanReviewState
from cera.pi_scene.runtime import LeanPiSceneCoordinator
from cera.pi_scene.store import LeanSceneStore
from cera.pi_scene.world_workspace import (
    ForkChatWorkspaceRequestV1,
    NewChatWorkspaceRequestV1,
    PiSceneWorldWorkspaceManager,
)
from cera.pi_scene.writer_view import WriterViewMaterializer
from cera.semantic_validation import SemanticVerdict
from cera.serialization import canonical_sha256

from . import test_pi_scene_lean_v1 as lean_support
from . import test_pi_scene_world_workspace as workspace_support
from .test_cognition_contracts import _context, _plan, _turn
from .test_pi_scene_semantic_runtime import (
    _CognitionPlanner,
    _runtime,
    _SemanticValidator,
)


def _runtime_pair(root: Path):
    harness = lean_support.PiSceneLeanTests(
        methodName="test_ordinary_regenerate_reuses_logic_and_accept_is_exactly_once"
    )
    coordinator, _, _, store = harness.make_runtime(root)
    original = coordinator.start_ordinary(lean_support.turn())
    replacement = coordinator.regenerate(original.review_id)
    assert replacement.successor is not None
    accepted = coordinator.accept(replacement.successor.review_id).review
    assert accepted.accepted_receipt is not None
    return coordinator, store, original, accepted.accepted_receipt


def _object_bytes(store: LeanSceneStore, receipt) -> dict[str, bytes]:
    root = store._accepted_turn_dir(receipt)
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


class PiSceneAcceptedLineageTests(unittest.TestCase):
    def test_replacement_switches_selected_head_and_preserves_old_object(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _, store, original, replaced = _runtime_pair(root)
            before = _object_bytes(store, replaced)
            base = store.regeneration_base(replaced)
            self.assertEqual(base.selected_prefix_receipt_sha256s, ())
            self.assertEqual(store.regeneration_prefix_payloads(base), ())
            self.assertIsNotNone(
                store.load_accepted_pi_session(
                    world_id=replaced.world_id,
                    branch_id=replaced.branch_id,
                )
            )

            selected = store.accept_replacement(
                original.candidate,
                replaced_receipt=replaced,
            )

            self.assertEqual(selected.generation, replaced.generation)
            self.assertNotEqual(selected.receipt_sha256, replaced.receipt_sha256)
            self.assertEqual(_object_bytes(store, replaced), before)
            self.assertEqual(
                store.load_head(world_id=replaced.world_id, branch_id=replaced.branch_id)
                .accepted_head_sha256,
                selected.receipt_sha256,
            )
            self.assertEqual(
                store.load_accepted_turn_by_receipt_sha256(
                    world_id=replaced.world_id,
                    branch_id=replaced.branch_id,
                    receipt_sha256=replaced.receipt_sha256,
                ),
                replaced,
            )
            # Both siblings share a turn ID. The replaced sibling's soft Pi
            # session must nevertheless be excluded after the selector switch.
            self.assertIsNone(
                store.load_accepted_pi_session(
                    world_id=replaced.world_id,
                    branch_id=replaced.branch_id,
                )
            )
            promoted = store.promote_pi_session(
                selected,
                session_id="pi-session-0001",
                session_path=root / "sessions" / "replacement",
            )
            assert promoted is not None
            self.assertEqual(
                promoted.accepted_receipt_sha256,
                selected.receipt_sha256,
            )
            context = store.recent_accepted_payloads(
                world_id=replaced.world_id,
                branch_id=replaced.branch_id,
                allow_pending=True,
            )
            self.assertEqual(len(context), 1)
            self.assertEqual(
                context[0]["receipt"]["candidate_sha256"],
                original.candidate.candidate_sha256,
            )

            restarted = LeanSceneStore(root / "world")
            self.assertEqual(
                restarted.load_head(
                    world_id=replaced.world_id,
                    branch_id=replaced.branch_id,
                ).accepted_head_sha256,
                selected.receipt_sha256,
            )
            self.assertEqual(_object_bytes(restarted, replaced), before)

    def test_historical_unbound_session_remains_readable_until_lineage_switch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _, store, _, accepted = _runtime_pair(root)
            promoted = store.load_accepted_pi_session(
                world_id=accepted.world_id,
                branch_id=accepted.branch_id,
            )
            assert promoted is not None
            branch_root = store._branch_root(accepted.world_id, accepted.branch_id)
            session_path = branch_root / "sessions" / "ACCEPTED_SESSION.json"
            session = json.loads(session_path.read_text(encoding="utf-8"))
            session.pop("accepted_receipt_sha256")
            session_path.write_text(json.dumps(session), encoding="utf-8")
            (branch_root / "ACTIVE_LINEAGE.json").unlink()

            historical = LeanSceneStore(root / "world").load_accepted_pi_session(
                world_id=accepted.world_id,
                branch_id=accepted.branch_id,
            )
            assert historical is not None
            self.assertIsNone(historical.accepted_receipt_sha256)

    def test_fork_rebinds_only_selected_prefix_and_archives_inactive_sibling(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime_root = root / "worlds"
            manager = PiSceneWorldWorkspaceManager(
                runtime_root,
                workspace_support.GENESIS_ROOT,
            )
            manager.create_new_chat(
                NewChatWorkspaceRequestV1(
                    chat_id="chat-parent",
                    world_id="world-test",
                    branch_id="branch-main",
                    settings={"autonomy": "both"},
                )
            )
            store = LeanSceneStore(runtime_root)
            coordinator = LeanPiSceneCoordinator(
                store=store,
                planner=lean_support.FakePlanner(),
                writer_views=WriterViewMaterializer(root / "views"),
                pi=lean_support.FakePi(),
                session_root=root / "sessions",
            )
            original = coordinator.start_ordinary(lean_support.turn())
            regenerated = coordinator.regenerate(original.review_id)
            assert regenerated.successor is not None
            replaced_review = coordinator.accept(regenerated.successor.review_id).review
            assert replaced_review.accepted_receipt is not None
            replaced = replaced_review.accepted_receipt
            selected = store.accept_replacement(
                original.candidate,
                replaced_receipt=replaced,
            )

            child = manager.fork_chat(
                ForkChatWorkspaceRequestV1(
                    parent_chat_id="chat-parent",
                    world_id="world-test",
                    parent_branch_id="branch-main",
                    child_chat_id="chat-child",
                    child_branch_id="branch-child",
                )
            )
            child_store = LeanSceneStore(runtime_root)
            child_head = child_store.load_head(
                world_id="world-test",
                branch_id="branch-child",
            )
            self.assertEqual(child_head.generation, selected.generation)
            assert child_head.receipt is not None
            self.assertEqual(
                child_head.receipt.candidate_sha256,
                original.candidate.candidate_sha256,
            )
            self.assertEqual(
                len(
                    child_store.recent_accepted_payloads(
                        world_id="world-test",
                        branch_id="branch-child",
                        allow_pending=True,
                    )
                ),
                1,
            )
            archived = child.branch_root / "FORK_SOURCE_INACTIVE_ACCEPTED"
            self.assertEqual(
                len(tuple(archived.glob("*/ACCEPTED_RECEIPT.json"))),
                1,
            )
            self.assertEqual(
                store.load_head(
                    world_id="world-test",
                    branch_id="branch-main",
                ).accepted_head_sha256,
                selected.receipt_sha256,
            )

    def test_fork_rebinds_cognition_validation_custody(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime_root = root / "worlds"
            manager = PiSceneWorldWorkspaceManager(
                runtime_root,
                workspace_support.GENESIS_ROOT,
            )
            manager.create_new_chat(
                NewChatWorkspaceRequestV1(
                    chat_id="chat-parent",
                    world_id="world-test",
                    branch_id="branch-main",
                    settings={"autonomy": "both"},
                )
            )
            validator = _SemanticValidator(SemanticVerdict.PASS)
            store = LeanSceneStore(runtime_root)
            coordinator = LeanPiSceneCoordinator(
                store=store,
                planner=_CognitionPlanner(),
                writer_views=WriterViewMaterializer(root / "views"),
                pi=lean_support.FakePi(),
                session_root=root / "sessions",
                semantic_validator=validator,
            )
            accepted = coordinator.start_ordinary(lean_support.turn())
            self.assertEqual(accepted.state, LeanReviewState.ACCEPTED)
            assert accepted.accepted_receipt is not None

            manager.fork_chat(
                ForkChatWorkspaceRequestV1(
                    parent_chat_id="chat-parent",
                    world_id="world-test",
                    parent_branch_id="branch-main",
                    child_chat_id="chat-child",
                    child_branch_id="branch-child",
                )
            )
            child_store = LeanSceneStore(runtime_root)
            child_head = child_store.load_head(
                world_id="world-test",
                branch_id="branch-child",
            )
            self.assertEqual(child_head.generation, 1)
            self.assertIsNotNone(child_head.receipt)
            self.assertEqual(
                child_store.recent_accepted_payloads(
                    world_id="world-test",
                    branch_id="branch-child",
                    allow_pending=True,
                )[0]["recording_status"],
                "complete",
            )

    def test_fork_rebinds_completed_record_manifest_to_child_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime_root = root / "worlds"
            manager = PiSceneWorldWorkspaceManager(
                runtime_root,
                workspace_support.GENESIS_ROOT,
            )
            manager.create_new_chat(
                NewChatWorkspaceRequestV1(
                    chat_id="chat-parent",
                    world_id="world-test",
                    branch_id="branch-main",
                    settings={"autonomy": "both"},
                )
            )
            coordinator = LeanPiSceneCoordinator(
                store=LeanSceneStore(runtime_root),
                planner=lean_support.FakePlanner(),
                writer_views=WriterViewMaterializer(root / "views"),
                pi=lean_support.FakePi(),
                session_root=root / "sessions",
            )
            review = coordinator.start_ordinary(lean_support.turn())
            accepted = coordinator.accept(review.review_id).review
            assert accepted.accepted_receipt is not None
            store = LeanSceneStore(runtime_root)
            self.assertEqual(
                store.recording_status(accepted.accepted_receipt).value,
                "complete",
            )

            manager.fork_chat(
                ForkChatWorkspaceRequestV1(
                    parent_chat_id="chat-parent",
                    world_id="world-test",
                    parent_branch_id="branch-main",
                    child_chat_id="chat-child",
                    child_branch_id="branch-child",
                )
            )
            child_payload = LeanSceneStore(runtime_root).recent_ordinary_context_payloads(
                world_id="world-test",
                branch_id="branch-child",
            )[0]
            self.assertEqual(child_payload["recording_status"], "complete")

    def test_pre_switch_failure_leaves_old_lineage_and_retry_selects_orphan(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _, store, original, replaced = _runtime_pair(root)
            select = store._select_receipt

            def fail_before_switch(*_args, **_kwargs):
                raise OSError("injected selector failure")

            store._select_receipt = fail_before_switch
            with self.assertRaisesRegex(OSError, "selector failure"):
                store.accept_replacement(
                    original.candidate,
                    replaced_receipt=replaced,
                )
            self.assertEqual(
                LeanSceneStore(root / "world")
                .load_head(world_id=replaced.world_id, branch_id=replaced.branch_id)
                .accepted_head_sha256,
                replaced.receipt_sha256,
            )

            store._select_receipt = select
            selected = store.accept_replacement(
                original.candidate,
                replaced_receipt=replaced,
            )
            self.assertNotEqual(selected.receipt_sha256, replaced.receipt_sha256)

    def test_normal_accept_appends_to_the_selected_replacement_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            coordinator, store, original, replaced = _runtime_pair(root)
            selected = store.accept_replacement(
                original.candidate,
                replaced_receipt=replaced,
            )
            next_review = coordinator.start_ordinary(
                lean_support.turn(source="Continue from the selected alternative.")
            )
            accepted_next = coordinator.accept(next_review.review_id).review
            assert accepted_next.accepted_receipt is not None
            self.assertEqual(
                accepted_next.accepted_receipt.parent_accepted_head_sha256,
                selected.receipt_sha256,
            )
            payloads = store.recent_accepted_payloads(
                world_id=replaced.world_id,
                branch_id=replaced.branch_id,
                allow_pending=True,
            )
            self.assertEqual(len(payloads), 2)
            self.assertNotIn(
                replaced.candidate_sha256,
                {value["receipt"]["candidate_sha256"] for value in payloads},
            )

    def test_stale_cache_after_atomic_switch_recovers_and_tamper_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _, store, original, replaced = _runtime_pair(root)
            write_cache = store._write_branch_cache

            def fail_cache(*_args, **_kwargs):
                raise OSError("injected cache failure")

            store._write_branch_cache = fail_cache
            selected = store.accept_replacement(
                original.candidate,
                replaced_receipt=replaced,
            )
            store._write_branch_cache = write_cache
            restarted = LeanSceneStore(root / "world")
            self.assertEqual(
                restarted.load_head(
                    world_id=replaced.world_id,
                    branch_id=replaced.branch_id,
                ).accepted_head_sha256,
                selected.receipt_sha256,
            )

            branch_root = restarted._branch_root(replaced.world_id, replaced.branch_id)
            manifest_path = branch_root / "ACTIVE_LINEAGE.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["selected_receipt_sha256"] = "0" * 64
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(StateConflictError, "manifest integrity"):
                LeanSceneStore(root / "world").load_head(
                    world_id=replaced.world_id,
                    branch_id=replaced.branch_id,
                )

    def test_provisional_v3_is_neutral_and_historical_v2_v1_remain_valid(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            coordinator, store, _ = _runtime(
                root,
                _SemanticValidator(SemanticVerdict.REJECT),
            )
            review = coordinator.start_ordinary(lean_support.turn())
            self.assertEqual(review.state, LeanReviewState.REVIEW_READY)
            accepted = coordinator.accept(
                review.review_id,
                acceptance_action="provisional_accept",
            ).review
            assert accepted.accepted_receipt is not None
            artifact_path = (
                store._accepted_turn_dir(accepted.accepted_receipt)
                / "PROVISIONAL_CANON.json"
            )
            before = artifact_path.read_bytes()
            artifact = json.loads(before)
            self.assertEqual(
                artifact["schema_version"],
                "cera.pi_scene.provisional_canon.v3",
            )
            self.assertIsNone(artifact["reader_validation_sha256"])
            self.assertNotIn("working_assumption", artifact)
            provisional_id = artifact["provisional_canon_id"]

            for truth in (ProvisionalTruthValue.TRUE, ProvisionalTruthValue.FALSE):
                dependency = ProvisionalDependencyV1(
                    provisional_record_id=provisional_id,
                    assumed_value=truth,
                    concise_dependency=f"This scene treats the record as {truth.value}.",
                )
                plan = replace(
                    _plan(),
                    provisional_dependencies=(dependency,),
                )
                validate_cognition_plan(
                    plan,
                    turn=_turn(),
                    context=_context(provisional=(provisional_id,)),
                )
                self.assertEqual(artifact_path.read_bytes(), before)

            historical_v2_body = {
                key: value
                for key, value in artifact.items()
                if key not in {"artifact_sha256", "reader_validation_sha256"}
            }
            historical_v2_body["schema_version"] = "cera.pi_scene.provisional_canon.v2"
            artifact_path.write_text(
                json.dumps(
                    {
                        **historical_v2_body,
                        "artifact_sha256": canonical_sha256(historical_v2_body),
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(
                LeanSceneStore(root / "world")
                .load_head(
                    world_id=accepted.accepted_receipt.world_id,
                    branch_id=accepted.accepted_receipt.branch_id,
                )
                .accepted_head_sha256,
                accepted.accepted_receipt.receipt_sha256,
            )

            historical_v1_body = {
                key: value
                for key, value in historical_v2_body.items()
                if key != "resolution_policy"
            }
            historical_v1_body["schema_version"] = "cera.pi_scene.provisional_canon.v1"
            historical_v1_body["working_assumption"] = (
                "accepted_candidate_events_are_provisionally_true"
            )
            artifact_path.write_text(
                json.dumps(
                    {
                        **historical_v1_body,
                        "artifact_sha256": canonical_sha256(historical_v1_body),
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(
                LeanSceneStore(root / "world")
                .load_head(
                    world_id=accepted.accepted_receipt.world_id,
                    branch_id=accepted.accepted_receipt.branch_id,
                )
                .accepted_head_sha256,
                accepted.accepted_receipt.receipt_sha256,
            )


if __name__ == "__main__":
    unittest.main()
