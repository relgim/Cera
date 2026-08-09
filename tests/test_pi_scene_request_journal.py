"""Provider-free request-replay custody tests for the Pi Scene HTTP route."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cera.errors import StateConflictError
from cera.pi_scene.contracts import SceneRoute
from cera.pi_scene.http import PiSceneCommittedStateError, PiSceneHttpAdapter
from cera.pi_scene.http_contracts import (
    PI_SCENE_ORDINARY_MODEL,
    PI_SCENE_PROFILE,
    LeanSceneRequestControlsV2,
)
from cera.pi_scene.request_journal import (
    PiSceneRequestJournal,
    RequestReplayPendingError,
    build_request_binding,
)
from cera.semantic_validation import SemanticVerdict

from .test_pi_scene_lean_v1 import turn
from .test_pi_scene_semantic_runtime import _runtime, _SemanticValidator


def _controls(session_id: str) -> LeanSceneRequestControlsV2:
    return LeanSceneRequestControlsV2(
        schema_version=LeanSceneRequestControlsV2.SCHEMA_VERSION,
        session_id=session_id,
        scene_depth="auto",
        regeneration_key=None,
        character_autonomy="both",
        prompt_handling="adjustment",
        reasoning_effort="medium",
        scene_change=False,
        adult_craft_mode="off",
    )


def _payload(session_id: str) -> dict[str, object]:
    return {
        "model": PI_SCENE_ORDINARY_MODEL,
        "messages": [{"role": "user", "content": "Continue the scene."}],
        "stream": False,
        "cera_profile_id": PI_SCENE_PROFILE,
        "cera_session_id": session_id,
        "cera_character_autonomy": "BOTH",
        "cera_adult_craft_mode": "OFF",
    }


def _binding(
    *,
    session_id: str = "chat-alpha",
    world_id: str = "world-alpha",
    branch_id: str = "branch-alpha",
    route: SceneRoute = SceneRoute.ORDINARY,
):
    payload = _payload(session_id)
    if route is SceneRoute.ADULT:
        payload["model"] = "cera-pi-scene-adult"
    return build_request_binding(
        payload=payload,
        session_id=session_id,
        world_id=world_id,
        branch_id=branch_id,
        route=route,
        controls=_controls(session_id),
    )


def _progress(binding) -> dict[str, object]:
    return {
        "schema_version": "cera.pi_scene.http_review_progress.v1",
        "review_id": "review-" + "a" * 28,
        "candidate_id": "candidate-test",
        "candidate_sha256": "b" * 64,
        "world_id": binding.world_id,
        "branch_id": binding.branch_id,
        "route": binding.route.value,
        "exact_user_source_sha256": "c" * 64,
        "controls_sha256": binding.controls_sha256,
        "review_state": "accepted",
        "accepted_turn_id": None,
        "accepted_receipt_sha256": None,
        "recording_status": None,
    }


class PiSceneRequestJournalTests(unittest.TestCase):
    def test_terminal_response_replays_exactly_after_restart(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            binding = _binding()
            first = PiSceneRequestJournal(root)
            resolution = first.begin(binding)
            self.assertFalse(resolution.replayed)
            response = {
                "id": "chatcmpl-cera-test",
                "choices": [{"message": {"content": "Hana answers."}}],
                "cera": {"status": "accepted"},
            }
            first.bind_review(binding, _progress(binding))
            first.complete(binding, response)

            restarted = PiSceneRequestJournal(root)
            replay = restarted.begin(binding)
            self.assertTrue(replay.replayed)
            self.assertEqual(replay.terminal_response, response)
            self.assertEqual(restarted.inspect(binding)["status"], "terminal")

    def test_nonterminal_restart_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            binding = _binding()
            PiSceneRequestJournal(root).begin(binding)
            with self.assertRaises(RequestReplayPendingError) as raised:
                PiSceneRequestJournal(root).begin(binding)
            self.assertEqual(raised.exception.request_id, binding.request_id)

    def test_route_session_world_and_branch_are_identity_bound(self) -> None:
        request_ids = {
            _binding().request_id,
            _binding(route=SceneRoute.ADULT).request_id,
            _binding(session_id="chat-beta").request_id,
            _binding(world_id="world-beta").request_id,
            _binding(branch_id="branch-beta").request_id,
        }
        self.assertEqual(len(request_ids), 5)
        with tempfile.TemporaryDirectory() as temporary:
            journal = PiSceneRequestJournal(Path(temporary))
            ordinary = _binding()
            adult = _binding(route=SceneRoute.ADULT)
            self.assertIn("ORDINARY", journal.entry_path(ordinary).parts)
            self.assertIn("PROTECTED_ADULT", journal.entry_path(adult).parts)

    def test_canonical_request_bytes_ignore_object_key_order_only(self) -> None:
        controls = _controls("chat-alpha")
        left = _payload("chat-alpha")
        right = dict(reversed(tuple(left.items())))
        left_binding = build_request_binding(
            payload=left,
            session_id="chat-alpha",
            world_id="world-alpha",
            branch_id="branch-alpha",
            route=SceneRoute.ORDINARY,
            controls=controls,
        )
        right_binding = build_request_binding(
            payload=right,
            session_id="chat-alpha",
            world_id="world-alpha",
            branch_id="branch-alpha",
            route=SceneRoute.ORDINARY,
            controls=controls,
        )
        self.assertEqual(left_binding, right_binding)
        changed = dict(left)
        changed["messages"] = [{"role": "user", "content": "Continue differently."}]
        changed_binding = build_request_binding(
            payload=changed,
            session_id="chat-alpha",
            world_id="world-alpha",
            branch_id="branch-alpha",
            route=SceneRoute.ORDINARY,
            controls=controls,
        )
        self.assertNotEqual(left_binding.request_id, changed_binding.request_id)

    def test_automatic_and_explicit_route_intent_are_separately_bound(self) -> None:
        controls = _controls("chat-alpha")
        explicit_payload = _payload("chat-alpha")
        automatic_payload = {**explicit_payload, "model": "cera-alpha"}
        explicit = build_request_binding(
            payload=explicit_payload,
            session_id="chat-alpha",
            world_id="world-alpha",
            branch_id="branch-alpha",
            route=SceneRoute.ORDINARY,
            controls=controls,
        )
        automatic = build_request_binding(
            payload=automatic_payload,
            session_id="chat-alpha",
            world_id="world-alpha",
            branch_id="branch-alpha",
            route=SceneRoute.ORDINARY,
            controls=controls,
        )
        self.assertEqual(explicit.route_intent, "explicit")
        self.assertEqual(automatic.route_intent, "automatic")
        self.assertNotEqual(explicit.request_id, automatic.request_id)

    def test_tampered_pending_or_terminal_entry_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            journal = PiSceneRequestJournal(Path(temporary))
            binding = _binding()
            journal.begin(binding)
            path = journal.entry_path(binding)
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["status"] = "terminal"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(StateConflictError):
                PiSceneRequestJournal(Path(temporary)).begin(binding)

    def test_stale_claim_without_entry_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            journal = PiSceneRequestJournal(Path(temporary))
            binding = _binding()
            path = journal.entry_path(binding)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.with_suffix(".claim").write_text("pending\n", encoding="utf-8")
            with self.assertRaises(RequestReplayPendingError):
                journal.begin(binding)

    def test_failed_atomic_terminal_replace_preserves_pending_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            journal = PiSceneRequestJournal(Path(temporary))
            binding = _binding()
            journal.begin(binding)
            journal.bind_review(binding, _progress(binding))
            with patch(
                "cera.pi_scene.request_journal.os.replace",
                side_effect=OSError("injected replace failure"),
            ):
                with self.assertRaises(OSError):
                    journal.complete(binding, {"status": "accepted"})
            self.assertEqual(journal.inspect(binding)["status"], "progressed")


class PiSceneHttpRequestReplayIntegrationTests(unittest.TestCase):
    def test_identical_auto_accepted_request_replays_with_zero_new_calls(self) -> None:
        short_root = Path("D:/Cera/tmp")
        short_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=short_root) as temporary:
            root = Path(temporary)
            validator = _SemanticValidator(SemanticVerdict.PASS)
            coordinator, _, planner = _runtime(root, validator)
            adapter = PiSceneHttpAdapter(
                coordinator=coordinator,
                session_id="chat-alpha",
                context_provider=lambda *_args: turn(),
            )
            request = _payload("chat-alpha")
            first = adapter.complete(request)
            first_counts = (
                planner.calls,
                len(coordinator.pi.calls),
                validator.calls,
            )
            replay = adapter.complete(request)
            self.assertEqual(replay, first)
            self.assertEqual(
                (planner.calls, len(coordinator.pi.calls), validator.calls),
                first_counts,
            )

            restarted_validator = _SemanticValidator(SemanticVerdict.PASS)
            restarted, _, restarted_planner = _runtime(root, restarted_validator)
            restarted_adapter = PiSceneHttpAdapter(
                coordinator=restarted,
                session_id="chat-alpha",
                context_provider=lambda *_args: turn(),
            )
            after_restart = restarted_adapter.complete(request)
            self.assertEqual(after_restart, first)
            self.assertEqual(restarted_planner.calls, 0)
            self.assertEqual(len(restarted.pi.calls), 0)
            self.assertEqual(restarted_validator.calls, 0)

    def test_crash_after_auto_accept_recovers_bound_review_without_providers(self) -> None:
        class CrashBeforeTerminalJournal(PiSceneRequestJournal):
            def complete(self, binding, response) -> None:
                del binding, response
                raise OSError("injected crash before terminal response")

        short_root = Path("D:/Cera/tmp")
        short_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=short_root) as temporary:
            root = Path(temporary)
            validator = _SemanticValidator(SemanticVerdict.PASS)
            coordinator, store, planner = _runtime(root, validator)
            journal_root = store.root / "http_request_journal"
            adapter = PiSceneHttpAdapter(
                coordinator=coordinator,
                session_id="chat-alpha",
                context_provider=lambda *_args: turn(),
                request_journal=CrashBeforeTerminalJournal(journal_root),
            )
            request = _payload("chat-alpha")
            with self.assertRaises(PiSceneCommittedStateError):
                adapter.complete(request)
            self.assertEqual(planner.calls, 1)
            self.assertEqual(validator.calls, 1)

            restarted_validator = _SemanticValidator(SemanticVerdict.PASS)
            restarted, restarted_store, restarted_planner = _runtime(
                root,
                restarted_validator,
            )
            recovered = PiSceneHttpAdapter(
                coordinator=restarted,
                session_id="chat-alpha",
                context_provider=lambda *_args: turn(),
                request_journal=PiSceneRequestJournal(
                    restarted_store.root / "http_request_journal"
                ),
            ).complete(request)
            self.assertTrue(recovered["cera"]["story_state_committed"])
            self.assertEqual(restarted_planner.calls, 0)
            self.assertEqual(len(restarted.pi.calls), 0)
            self.assertEqual(restarted_validator.calls, 0)


if __name__ == "__main__":
    unittest.main()
