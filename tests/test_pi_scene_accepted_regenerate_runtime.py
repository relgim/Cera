from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from cera.pi_scene.http import PiSceneHttpAdapter
from cera.pi_scene.http_contracts import (
    PI_SCENE_AUTO_MODEL,
    PI_SCENE_PROFILE,
    LeanSceneRequestControlsV2,
)
from cera.pi_scene.review_store import LeanReviewState
from cera.semantic_validation import SemanticVerdict

from .test_pi_scene_lean_v1 import turn
from .test_pi_scene_semantic_runtime import _runtime, _SemanticValidator


def _controls(key: str) -> LeanSceneRequestControlsV2:
    return LeanSceneRequestControlsV2(
        schema_version=LeanSceneRequestControlsV2.SCHEMA_VERSION,
        session_id="accepted-regenerate",
        regeneration_key=key,
    )


def _payload(*, regeneration_key: str | None = None) -> dict[str, object]:
    value: dict[str, object] = {
        "model": PI_SCENE_AUTO_MODEL,
        "messages": [{"role": "user", "content": "Continue the scene."}],
        "stream": False,
        "cera_profile_id": PI_SCENE_PROFILE,
        "cera_session_id": "accepted-regenerate",
    }
    if regeneration_key is not None:
        value["cera_regeneration_key"] = regeneration_key
    return value


class PiSceneAcceptedRegenerateRuntimeTests(unittest.TestCase):
    def test_accepted_regenerate_selects_an_immutable_same_generation_sibling(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path(r"D:\Cera\tmp")) as temporary:
            root = Path(temporary)
            coordinator, store, planner = _runtime(
                root,
                _SemanticValidator(SemanticVerdict.PASS),
            )
            original = coordinator.start_ordinary(turn())
            assert original.accepted_receipt is not None
            old_receipt = original.accepted_receipt

            replacement = coordinator.regenerate_accepted(
                replace(turn(), request_controls=_controls("regen-one"))
            )
            assert replacement.accepted_receipt is not None

            self.assertEqual(replacement.state, LeanReviewState.ACCEPTED)
            self.assertEqual(replacement.candidate.generation, old_receipt.generation)
            self.assertNotEqual(
                replacement.accepted_receipt.receipt_sha256,
                old_receipt.receipt_sha256,
            )
            self.assertEqual(planner.calls, 1)
            self.assertEqual(
                replacement.candidate.primary_authority_sha256,
                original.candidate.primary_authority_sha256,
            )
            self.assertEqual(replacement.result.planner_provider_operations, 0)
            self.assertEqual(
                store.load_head(
                    world_id="world-test", branch_id="branch-main"
                ).accepted_head_sha256,
                replacement.accepted_receipt.receipt_sha256,
            )
            self.assertEqual(
                store.load_accepted_turn_by_receipt_sha256(
                    world_id="world-test",
                    branch_id="branch-main",
                    receipt_sha256=old_receipt.receipt_sha256,
                ),
                old_receipt,
            )
            self.assertEqual(
                len(store.accepted_branch_payloads(world_id="world-test", branch_id="branch-main")),
                1,
            )

    def test_http_accepted_regenerate_replays_after_restart_without_providers(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path(r"D:\Cera\tmp")) as temporary:
            root = Path(temporary)
            coordinator, _, planner = _runtime(
                root,
                _SemanticValidator(SemanticVerdict.PASS),
            )
            adapter = PiSceneHttpAdapter(
                coordinator=coordinator,
                session_id="accepted-regenerate",
                context_provider=lambda *_args: turn(),
            )
            original = adapter.complete(_payload())
            regenerated = adapter.complete(_payload(regeneration_key="regen-http"))

            self.assertTrue(original["cera"]["story_state_committed"])
            self.assertTrue(regenerated["cera"]["story_state_committed"])
            self.assertEqual(original["cera"]["generation"], 1)
            self.assertEqual(regenerated["cera"]["generation"], 1)
            self.assertNotEqual(
                original["cera"]["accepted_receipt_sha256"],
                regenerated["cera"]["accepted_receipt_sha256"],
            )
            self.assertEqual(planner.calls, 1)

            restarted, _, restarted_planner = _runtime(
                root,
                _SemanticValidator(SemanticVerdict.PASS),
            )
            replay = PiSceneHttpAdapter(
                coordinator=restarted,
                session_id="accepted-regenerate",
                context_provider=lambda *_args: turn(),
            ).complete(_payload(regeneration_key="regen-http"))

            self.assertEqual(replay, regenerated)
            self.assertEqual(restarted_planner.calls, 0)


if __name__ == "__main__":
    unittest.main()
