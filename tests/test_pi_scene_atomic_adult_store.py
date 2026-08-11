from __future__ import annotations

import json
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from cera.adult_pipeline.contracts import AdultNextRoute
from cera.adult_pipeline.ports import promote_passed_adult_candidate
from cera.errors import ContractValidationError, StateConflictError
from cera.pi_scene._branch_state_payloads import accepted_event_from_payload
from cera.pi_scene.branch_state import BranchStateReducerV1, GenesisBranchStateV1
from cera.pi_scene.store import AcceptedPiSessionV1, LeanSceneStore
from cera.pi_scene.world_workspace import (
    ForkChatWorkspaceRequestV1,
    NewChatWorkspaceRequestV1,
    PiSceneWorldWorkspaceManager,
)
from cera.provider_dispatch_guard import PROVIDER_DISPATCH_DISABLED_ENV
from cera.serialization import canonical_json, canonical_sha256

from . import test_adult_pipeline_pi_integration as adult_support
from . import test_pi_scene_lean_v1 as lean_support
from . import test_pi_scene_world_workspace as workspace_support


class PiSceneAtomicAdultStoreTests(unittest.TestCase):
    def _execution_and_envelope(
        self,
        *,
        next_route: AdultNextRoute = AdultNextRoute.ORDINARY,
        accepted_head_sha256: str | None = None,
        parent_accepted_turn_id: str | None = None,
        generation: int = 1,
        identity_suffix: str = "atomic-adult",
        protected_prose: str = adult_support.PROTECTED_PROSE,
        exact_source: str | None = None,
        autonomy_mode: str | None = None,
        accepted_parent_session: AcceptedPiSessionV1 | None = None,
        accepted_turn_id: str | None = None,
    ):
        support = adult_support.AdultPiIntegrationTests(
            methodName="test_full_fake_route_creates_restart_safe_acceptance_custody"
        )
        support.setUp()
        self.addCleanup(support.tearDown)
        integration = support._integration()
        integration.scene_port.accepted_parent_session = accepted_parent_session
        request = support._request(integration)
        if exact_source is not None:
            request = replace(request, exact_current_source=exact_source)
        if autonomy_mode is not None:
            request = replace(request, autonomy_mode=autonomy_mode)
        scene_wire = json.loads(adult_support._scene_wire())
        scene_wire["exact_story_prose"] = protected_prose
        scene_wire["next_route"] = next_route.value
        scene_wire["next_route_reason"] = f"Continue with {next_route.value} logic."
        with (
            patch.object(
                adult_support,
                "_scene_wire",
                return_value=canonical_json(scene_wire),
            ),
            patch.dict(
                os.environ,
                {PROVIDER_DISPATCH_DISABLED_ENV: "1"},
                clear=False,
            ),
        ):
            execution = integration.execute(
                request_id=f"request:{identity_suffix}",
                candidate_id=f"candidate:{identity_suffix}",
                world_id=support.context.world_id,
                branch_id=support.context.branch_id,
                accepted_head_sha256=accepted_head_sha256,
                scene_request=request,
            )
        envelope = execution.acceptance_envelope(
            accepted_turn_id=accepted_turn_id or support.context.turn_id,
            parent_accepted_turn_id=parent_accepted_turn_id,
            scene_id=support.context.scene_id,
            generation=generation,
        )
        return support, execution, envelope

    def test_consecutive_adult_acceptance_retains_parent_rehydration_after_restart(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first_support, first_execution, first_envelope = self._execution_and_envelope(
                next_route=AdultNextRoute.ADULT,
                identity_suffix="rehydrate-parent",
                accepted_turn_id="turn:adult:rehydrate-parent",
            )
            store = LeanSceneStore(root / "world")
            first = promote_passed_adult_candidate(
                first_execution.result,
                store.adult_promotion_port(first_envelope),
            )
            parent_session = store.load_accepted_pi_session(
                world_id=first_support.context.world_id,
                branch_id=first_support.context.branch_id,
            )
            assert parent_session is not None

            _, second_execution, second_envelope = self._execution_and_envelope(
                next_route=AdultNextRoute.ADULT,
                accepted_head_sha256=first.receipt.accepted_head_after_sha256,
                parent_accepted_turn_id=first_envelope.accepted_turn_id,
                generation=2,
                identity_suffix="rehydrate-child",
                accepted_parent_session=parent_session,
                accepted_turn_id="turn:adult:rehydrate-child",
            )
            second = promote_passed_adult_candidate(
                second_execution.result,
                store.adult_promotion_port(second_envelope),
            )

            restarted = LeanSceneStore(root / "world")
            head = restarted.load_head(
                world_id=first_support.context.world_id,
                branch_id=first_support.context.branch_id,
            )
            assert head.receipt is not None
            self.assertEqual(
                head.accepted_head_sha256,
                second.receipt.accepted_head_after_sha256,
            )
            self.assertEqual(
                head.receipt.writer_receipt.parent_session_id_sha256,
                parent_session.session_id_sha256,
            )
            self.assertTrue(head.receipt.writer_receipt.rehydrated)
            recovered_envelope, _ = restarted.load_promoted_adult_acceptance(
                world_id=first_support.context.world_id,
                branch_id=first_support.context.branch_id,
                accepted_turn_id=second_envelope.accepted_turn_id,
            )
            self.assertEqual(recovered_envelope, second_envelope)
            self.assertEqual(
                recovered_envelope.scene_invocation.receipt.parent_session_id_sha256,
                parent_session.session_id_sha256,
            )
            self.assertTrue(recovered_envelope.scene_invocation.receipt.rehydrated)
            self.assertEqual(
                recovered_envelope.scene_session.parent_session_id_sha256,
                parent_session.session_id_sha256,
            )
            self.assertTrue(recovered_envelope.scene_session.rehydrated)

    def test_atomic_promotion_restart_replay_route_session_and_privacy(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            support, execution, envelope = self._execution_and_envelope(
                next_route=AdultNextRoute.ADULT
            )
            store = LeanSceneStore(root / "world")
            initial = store.current_logic_route(
                world_id=support.context.world_id,
                branch_id=support.context.branch_id,
            )
            self.assertEqual(initial.current_logic_route, AdultNextRoute.ORDINARY)

            accepted = promote_passed_adult_candidate(
                execution.result,
                store.adult_promotion_port(envelope),
            )
            head = store.load_head(
                world_id=support.context.world_id,
                branch_id=support.context.branch_id,
            )
            self.assertEqual(head.accepted_head_sha256, accepted.receipt.accepted_head_after_sha256)
            self.assertEqual(head.recording_status.value, "complete")
            self.assertEqual(
                store.current_logic_route(
                    world_id=support.context.world_id,
                    branch_id=support.context.branch_id,
                ).current_logic_route,
                AdultNextRoute.ADULT,
            )
            branch_root = store._branch_root(
                support.context.world_id,
                support.context.branch_id,
            )
            self.assertFalse(any(branch_root.rglob("RECORDING_ATTEMPT.json")))
            self.assertFalse(any(branch_root.rglob("ADULT_FULL_RECORD.json")))

            ordinary = store.recent_ordinary_context_payloads(
                world_id=support.context.world_id,
                branch_id=support.context.branch_id,
            )
            ordinary_wire = canonical_json(ordinary)
            self.assertNotIn(adult_support.PROTECTED_PROSE, ordinary_wire)
            self.assertIn("adult_projection", ordinary_wire)
            protected = store.recent_adult_context_payloads(
                world_id=support.context.world_id,
                branch_id=support.context.branch_id,
            )
            self.assertIn(adult_support.PROTECTED_PROSE, canonical_json(protected))

            session = store.load_accepted_pi_session(
                world_id=support.context.world_id,
                branch_id=support.context.branch_id,
            )
            assert session is not None
            self.assertEqual(session.session_id_sha256, envelope.scene_session.session_id_sha256)
            recovered_envelope, recovered = store.load_promoted_adult_acceptance(
                world_id=support.context.world_id,
                branch_id=support.context.branch_id,
                promotion_bundle_sha256=canonical_sha256(envelope.promotion_bundle),
            )
            self.assertEqual(recovered_envelope, envelope)
            self.assertEqual(recovered, accepted)
            request_envelope, request_recovered = (
                store.load_promoted_adult_acceptance(
                    world_id=support.context.world_id,
                    branch_id=support.context.branch_id,
                    request_id=envelope.request_id,
                )
            )
            self.assertEqual(request_envelope, envelope)
            self.assertEqual(request_recovered, accepted)
            with self.assertRaisesRegex(ContractValidationError, "exactly one"):
                store.load_promoted_adult_acceptance(
                    world_id=support.context.world_id,
                    branch_id=support.context.branch_id,
                    accepted_turn_id=envelope.accepted_turn_id,
                    request_id=envelope.request_id,
                )

            object_bytes = {
                path.relative_to(branch_root).as_posix(): path.read_bytes()
                for path in branch_root.rglob("*")
                if path.is_file() and "sessions/ACCEPTED_SESSION.json" not in path.as_posix()
            }
            restarted = LeanSceneStore(root / "world")
            replay = restarted.promote_adult_acceptance_envelope(envelope)
            self.assertEqual(replay, accepted)
            self.assertEqual(
                {
                    path.relative_to(branch_root).as_posix(): path.read_bytes()
                    for path in branch_root.rglob("*")
                    if path.is_file()
                    and "sessions/ACCEPTED_SESSION.json" not in path.as_posix()
                },
                object_bytes,
            )

    def test_pre_selector_crash_is_inactive_and_replay_finishes_once(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            support, _, envelope = self._execution_and_envelope()
            store = LeanSceneStore(root / "world")
            select = store._select_receipt

            def fail_selector(*_args, **_kwargs):
                raise OSError("injected adult selector failure")

            store._select_receipt = fail_selector
            with self.assertRaisesRegex(OSError, "selector failure"):
                store.promote_adult_acceptance_envelope(envelope)
            restarted = LeanSceneStore(root / "world")
            # The complete immutable object is safely recoverable through the
            # historical root-cache rule; replay below materializes the modern
            # selector and does not duplicate any object.
            self.assertEqual(
                restarted.load_head(
                    world_id=support.context.world_id,
                    branch_id=support.context.branch_id,
                ).generation,
                1,
            )

            store._select_receipt = select
            accepted = store.promote_adult_acceptance_envelope(envelope)
            self.assertEqual(
                store.load_head(
                    world_id=support.context.world_id,
                    branch_id=support.context.branch_id,
                ).accepted_head_sha256,
                accepted.receipt.accepted_head_after_sha256,
            )
            self.assertEqual(
                len(tuple((store._branch_root(
                    support.context.world_id,
                    support.context.branch_id,
                ) / "accepted").glob("*/ACCEPTED_RECEIPT.json"))),
                1,
            )

    def test_tamper_fails_closed_and_bare_bundle_cannot_promote(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            support, execution, envelope = self._execution_and_envelope()
            store = LeanSceneStore(root / "world")
            with self.assertRaisesRegex(ContractValidationError, "full acceptance envelope"):
                store.promote_adult_acceptance_envelope(execution.result.promotion_bundle())
            accepted = store.promote_adult_acceptance_envelope(envelope)
            receipt = store.load_accepted_turn_by_receipt_sha256(
                world_id=support.context.world_id,
                branch_id=support.context.branch_id,
                receipt_sha256=accepted.receipt.accepted_head_after_sha256,
            )
            turn_dir = store._accepted_turn_dir(receipt)
            projection_path = turn_dir / "ADULT_CODEX_PROJECTION.json"
            projection = json.loads(projection_path.read_text(encoding="utf-8"))
            projection["resulting_public_state"] = "Tampered state."
            projection_path.write_text(canonical_json(projection), encoding="utf-8")
            with self.assertRaisesRegex(StateConflictError, "safe projection changed"):
                LeanSceneStore(root / "world").load_head(
                    world_id=support.context.world_id,
                    branch_id=support.context.branch_id,
                )

    def test_fork_rebinds_atomic_custody_but_never_reuses_parent_session(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            support, _, envelope = self._execution_and_envelope(
                next_route=AdultNextRoute.ADULT
            )
            runtime_root = root / "world"
            manager = PiSceneWorldWorkspaceManager(
                runtime_root,
                workspace_support.GENESIS_ROOT,
            )
            manager.create_new_chat(
                NewChatWorkspaceRequestV1(
                    chat_id="chat:adult-parent",
                    world_id=support.context.world_id,
                    branch_id=support.context.branch_id,
                    settings={"autonomy": "both"},
                )
            )
            parent_store = LeanSceneStore(runtime_root)
            parent_store.promote_adult_acceptance_envelope(envelope)
            self.assertIsNotNone(
                parent_store.load_accepted_pi_session(
                    world_id=support.context.world_id,
                    branch_id=support.context.branch_id,
                )
            )

            manager.fork_chat(
                ForkChatWorkspaceRequestV1(
                    parent_chat_id="chat:adult-parent",
                    world_id=support.context.world_id,
                    parent_branch_id=support.context.branch_id,
                    child_chat_id="chat:adult-child",
                    child_branch_id="branch:adult-child",
                )
            )
            child_store = LeanSceneStore(runtime_root)
            child_head = child_store.load_head(
                world_id=support.context.world_id,
                branch_id="branch:adult-child",
            )
            self.assertEqual(child_head.generation, 1)
            self.assertEqual(
                child_store.current_logic_route(
                    world_id=support.context.world_id,
                    branch_id="branch:adult-child",
                ).current_logic_route,
                AdultNextRoute.ADULT,
            )
            self.assertIsNone(
                child_store.load_accepted_pi_session(
                    world_id=support.context.world_id,
                    branch_id="branch:adult-child",
                )
            )
            child_envelope, _ = child_store.load_promoted_adult_acceptance(
                world_id=support.context.world_id,
                branch_id="branch:adult-child",
                accepted_turn_id=envelope.accepted_turn_id,
            )
            self.assertEqual(child_envelope.branch_id, "branch:adult-child")
            self.assertEqual(
                child_envelope.promotion_bundle.exact_story_prose,
                envelope.promotion_bundle.exact_story_prose,
            )
            self.assertNotIn(
                adult_support.PROTECTED_PROSE,
                canonical_json(
                    child_store.recent_ordinary_context_payloads(
                        world_id=support.context.world_id,
                        branch_id="branch:adult-child",
                    )
                ),
            )

    def test_regeneration_prefix_context_has_strict_route_privacy(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            support, _, envelope = self._execution_and_envelope()
            store = LeanSceneStore(root / "world")
            store.promote_adult_acceptance_envelope(envelope)

            coordinator, _, _, _ = lean_support.PiSceneLeanTests(
                methodName="test_ordinary_regenerate_reuses_logic_and_accept_is_exactly_once"
            ).make_runtime(root)
            ordinary_turn = replace(
                lean_support.turn(source="Continue after the protected event."),
                world_id=support.context.world_id,
                branch_id=support.context.branch_id,
                scene_id=support.context.scene_id,
            )
            review = coordinator.start_ordinary(ordinary_turn)
            accepted_review = coordinator.accept(review.review_id).review
            assert accepted_review.accepted_receipt is not None
            base = store.regeneration_base(accepted_review.accepted_receipt)

            raw = canonical_json(store.regeneration_prefix_payloads(base))
            ordinary = canonical_json(
                store.regeneration_prefix_ordinary_context_payloads(base)
            )
            protected = canonical_json(
                store.regeneration_prefix_adult_context_payloads(base)
            )
            self.assertIn(adult_support.PROTECTED_PROSE, raw)
            self.assertNotIn(adult_support.PROTECTED_PROSE, ordinary)
            self.assertIn("adult_projection", ordinary)
            self.assertIn(adult_support.PROTECTED_PROSE, protected)

    def test_atomic_projection_reduces_and_restarts_without_protected_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            support, _, envelope = self._execution_and_envelope()
            store = LeanSceneStore(root / "world")
            store.promote_adult_acceptance_envelope(envelope)
            payloads = store.accepted_branch_payloads(
                world_id=support.context.world_id,
                branch_id=support.context.branch_id,
            )
            genesis = GenesisBranchStateV1.from_mappings(
                world_id=support.context.world_id,
                genesis_revision="genesis:atomic-adult:v1",
                scene_id=support.context.scene_id,
                accepted_present_character_ids=("character:hana",),
                resulting_public_state="Hana is present in the room.",
                characters={"character:hana": {"name": "Hana", "age": 38}},
                relationships={},
                memories={},
            )
            reducer = BranchStateReducerV1(genesis)
            checkpoint = reducer.reduce(
                branch_id=support.context.branch_id,
                payloads=payloads,
            )
            restarted = BranchStateReducerV1(genesis).reduce(
                branch_id=support.context.branch_id,
                payloads=payloads,
            )
            self.assertEqual(restarted.checkpoint_sha256, checkpoint.checkpoint_sha256)
            self.assertEqual(checkpoint.accepted_order, 1)
            self.assertEqual(
                checkpoint.resulting_public_state,
                envelope.promotion_bundle.codex_projection.resulting_public_state,
            )
            self.assertEqual(
                checkpoint.adult_public_continuity[0].non_explicit_summary,
                envelope.promotion_bundle.codex_projection.events[0].non_explicit_summary,
            )
            self.assertEqual(
                checkpoint.durable_changes[-1].visibility,
                "character_private",
            )
            self.assertEqual(
                checkpoint.durable_changes[-1].knowledge_owner_id,
                "character:hana",
            )
            checkpoint_wire = canonical_json(checkpoint.to_mapping())
            self.assertNotIn(adult_support.PROTECTED_PROSE, checkpoint_wire)
            self.assertNotIn("protected_summary", checkpoint_wire)

            tampered = json.loads(canonical_json(payloads[0]))
            tampered["adult_projection"][
                "exact_story_prose"
            ] = adult_support.PROTECTED_PROSE
            with self.assertRaisesRegex(ContractValidationError, "unknown fields"):
                accepted_event_from_payload(tampered)

    def test_adult_replacement_switches_one_immutable_sibling_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first_prose = "The first protected adult alternative."
            second_prose = "The independently regenerated adult alternative."
            support, _, first_envelope = self._execution_and_envelope(
                next_route=AdultNextRoute.ADULT,
                identity_suffix="adult-replaced",
                protected_prose=first_prose,
            )
            store = LeanSceneStore(root / "world")
            first = store.promote_adult_acceptance_envelope(first_envelope)
            first_receipt = store.load_accepted_turn_by_receipt_sha256(
                world_id=support.context.world_id,
                branch_id=support.context.branch_id,
                receipt_sha256=first.receipt.accepted_head_after_sha256,
            )
            first_dir = store._accepted_turn_dir(first_receipt)
            first_bytes = {
                path.relative_to(first_dir).as_posix(): path.read_bytes()
                for path in first_dir.rglob("*")
                if path.is_file()
            }
            first_session = store.load_accepted_pi_session(
                world_id=support.context.world_id,
                branch_id=support.context.branch_id,
            )
            assert first_session is not None
            base = store.regeneration_base(first_receipt)

            _, _, second_envelope = self._execution_and_envelope(
                next_route=AdultNextRoute.ORDINARY,
                identity_suffix="adult-replacement",
                protected_prose=second_prose,
            )
            second = store.promote_adult_replacement_envelope(
                second_envelope,
                base=base,
            )
            second_receipt = store.load_accepted_turn_by_receipt_sha256(
                world_id=support.context.world_id,
                branch_id=support.context.branch_id,
                receipt_sha256=second.receipt.accepted_head_after_sha256,
            )

            self.assertEqual(second_receipt.generation, first_receipt.generation)
            self.assertNotEqual(second_receipt.receipt_sha256, first_receipt.receipt_sha256)
            self.assertEqual(
                {
                    path.relative_to(first_dir).as_posix(): path.read_bytes()
                    for path in first_dir.rglob("*")
                    if path.is_file()
                },
                first_bytes,
            )
            lineage = store.load_active_lineage(
                world_id=support.context.world_id,
                branch_id=support.context.branch_id,
            )
            self.assertEqual(lineage.switch_kind, "replacement")
            self.assertEqual(lineage.selected_receipt_sha256, second_receipt.receipt_sha256)
            self.assertEqual(lineage.previous_receipt_sha256, first_receipt.receipt_sha256)
            self.assertEqual(
                store.current_logic_route(
                    world_id=support.context.world_id,
                    branch_id=support.context.branch_id,
                ).current_logic_route,
                AdultNextRoute.ORDINARY,
            )
            current_session = store.load_accepted_pi_session(
                world_id=support.context.world_id,
                branch_id=support.context.branch_id,
            )
            assert current_session is not None
            self.assertEqual(
                current_session.accepted_receipt_sha256,
                second_receipt.receipt_sha256,
            )
            self.assertNotEqual(
                current_session.accepted_receipt_sha256,
                first_session.accepted_receipt_sha256,
            )
            ordinary_wire = canonical_json(
                store.recent_ordinary_context_payloads(
                    world_id=support.context.world_id,
                    branch_id=support.context.branch_id,
                )
            )
            self.assertNotIn(first_prose, ordinary_wire)
            self.assertNotIn(second_prose, ordinary_wire)
            protected_wire = canonical_json(
                store.recent_adult_context_payloads(
                    world_id=support.context.world_id,
                    branch_id=support.context.branch_id,
                )
            )
            self.assertNotIn(first_prose, protected_wire)
            self.assertIn(second_prose, protected_wire)
            self.assertEqual(
                store.promote_adult_replacement_envelope(
                    second_envelope,
                    base=base,
                ),
                second,
            )
            self.assertEqual(len(tuple((first_dir.parent).glob("*/ACCEPTED_RECEIPT.json"))), 2)

    def test_adult_replacement_recovers_after_pre_selector_crash(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            support, _, first_envelope = self._execution_and_envelope(
                identity_suffix="adult-crash-old"
            )
            store = LeanSceneStore(root / "world")
            first = store.promote_adult_acceptance_envelope(first_envelope)
            first_receipt = store.load_accepted_turn_by_receipt_sha256(
                world_id=support.context.world_id,
                branch_id=support.context.branch_id,
                receipt_sha256=first.receipt.accepted_head_after_sha256,
            )
            base = store.regeneration_base(first_receipt)
            _, _, replacement = self._execution_and_envelope(
                identity_suffix="adult-crash-new",
                protected_prose="A complete orphan waiting for the selector.",
            )
            select = store._select_receipt

            def fail_selector(*_args, **_kwargs):
                raise OSError("injected adult replacement selector failure")

            store._select_receipt = fail_selector
            with self.assertRaisesRegex(OSError, "replacement selector failure"):
                store.promote_adult_replacement_envelope(replacement, base=base)
            self.assertEqual(
                LeanSceneStore(root / "world").load_head(
                    world_id=support.context.world_id,
                    branch_id=support.context.branch_id,
                ).accepted_head_sha256,
                first_receipt.receipt_sha256,
            )

            store._select_receipt = select
            restarted = LeanSceneStore(root / "world")
            promoted = restarted.promote_adult_replacement_envelope(
                replacement,
                base=base,
            )
            self.assertEqual(
                restarted.load_head(
                    world_id=support.context.world_id,
                    branch_id=support.context.branch_id,
                ).accepted_head_sha256,
                promoted.receipt.accepted_head_after_sha256,
            )
            self.assertEqual(
                restarted.promote_adult_replacement_envelope(
                    replacement,
                    base=base,
                ),
                promoted,
            )
            self.assertEqual(
                len(
                    tuple(
                        (
                            restarted._branch_root(
                                support.context.world_id,
                                support.context.branch_id,
                            )
                            / "accepted"
                        ).glob("*/ACCEPTED_RECEIPT.json")
                    )
                ),
                2,
            )

    def test_adult_replacement_rejects_source_and_settings_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            support, _, original = self._execution_and_envelope(
                identity_suffix="adult-custody-old"
            )
            store = LeanSceneStore(root / "world")
            first = store.promote_adult_acceptance_envelope(original)
            first_receipt = store.load_accepted_turn_by_receipt_sha256(
                world_id=support.context.world_id,
                branch_id=support.context.branch_id,
                receipt_sha256=first.receipt.accepted_head_after_sha256,
            )
            base = store.regeneration_base(first_receipt)

            _, _, changed_source = self._execution_and_envelope(
                identity_suffix="adult-custody-source",
                exact_source="A different user source must not enter Regenerate.",
            )
            with self.assertRaisesRegex(StateConflictError, "source custody"):
                store.promote_adult_replacement_envelope(
                    changed_source,
                    base=base,
                )

            _, _, changed_settings = self._execution_and_envelope(
                identity_suffix="adult-custody-settings",
                autonomy_mode="mind",
            )
            with self.assertRaisesRegex(StateConflictError, "settings custody"):
                store.promote_adult_replacement_envelope(
                    changed_settings,
                    base=base,
                )

    def test_fork_after_adult_replacement_copies_only_selected_sibling(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original_prose = "The adult sibling that becomes inactive."
            selected_prose = "The adult sibling selected before the fork."
            support, _, original = self._execution_and_envelope(
                next_route=AdultNextRoute.ADULT,
                identity_suffix="adult-fork-old",
                protected_prose=original_prose,
            )
            runtime_root = root / "world"
            manager = PiSceneWorldWorkspaceManager(
                runtime_root,
                workspace_support.GENESIS_ROOT,
            )
            manager.create_new_chat(
                NewChatWorkspaceRequestV1(
                    chat_id="chat:adult-replacement-parent",
                    world_id=support.context.world_id,
                    branch_id=support.context.branch_id,
                    settings={"autonomy": "both"},
                )
            )
            store = LeanSceneStore(runtime_root)
            first = store.promote_adult_acceptance_envelope(original)
            first_receipt = store.load_accepted_turn_by_receipt_sha256(
                world_id=support.context.world_id,
                branch_id=support.context.branch_id,
                receipt_sha256=first.receipt.accepted_head_after_sha256,
            )
            _, _, replacement = self._execution_and_envelope(
                next_route=AdultNextRoute.ORDINARY,
                identity_suffix="adult-fork-selected",
                protected_prose=selected_prose,
            )
            store.promote_adult_replacement_envelope(
                replacement,
                base=store.regeneration_base(first_receipt),
            )

            manager.fork_chat(
                ForkChatWorkspaceRequestV1(
                    parent_chat_id="chat:adult-replacement-parent",
                    world_id=support.context.world_id,
                    parent_branch_id=support.context.branch_id,
                    child_chat_id="chat:adult-replacement-child",
                    child_branch_id="branch:adult-replacement-child",
                )
            )
            child = LeanSceneStore(runtime_root)
            child_protected = canonical_json(
                child.recent_adult_context_payloads(
                    world_id=support.context.world_id,
                    branch_id="branch:adult-replacement-child",
                )
            )
            self.assertIn(selected_prose, child_protected)
            self.assertNotIn(original_prose, child_protected)
            child_ordinary = canonical_json(
                child.recent_ordinary_context_payloads(
                    world_id=support.context.world_id,
                    branch_id="branch:adult-replacement-child",
                )
            )
            self.assertNotIn(selected_prose, child_ordinary)
            self.assertEqual(
                child.current_logic_route(
                    world_id=support.context.world_id,
                    branch_id="branch:adult-replacement-child",
                ).current_logic_route,
                AdultNextRoute.ORDINARY,
            )
            self.assertIsNone(
                child.load_accepted_pi_session(
                    world_id=support.context.world_id,
                    branch_id="branch:adult-replacement-child",
                )
            )


if __name__ == "__main__":
    unittest.main()
