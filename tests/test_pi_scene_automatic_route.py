from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from cera.pi_scene.contracts import SceneRoute
from cera.pi_scene.http import PiSceneHttpAdapter
from cera.pi_scene.http_contracts import (
    PI_SCENE_ADULT_MODEL,
    PI_SCENE_AUTO_MODEL,
    PI_SCENE_PROFILE,
    parse_chat_request,
)
from cera.semantic_validation import SemanticVerdict

from .test_pi_scene_lean_v1 import turn
from .test_pi_scene_semantic_runtime import _runtime, _SemanticValidator


def _payload() -> dict[str, object]:
    return {
        "model": PI_SCENE_AUTO_MODEL,
        "messages": [{"role": "user", "content": "Continue the scene."}],
        "stream": False,
        "cera_profile_id": PI_SCENE_PROFILE,
        "cera_session_id": "chat-auto",
    }


class PiSceneAutomaticRouteTests(unittest.TestCase):
    def test_auto_model_is_typed_separately_from_explicit_route_models(self) -> None:
        request = parse_chat_request(_payload(), expected_session_id=None)
        self.assertTrue(request.automatic_route)
        self.assertEqual(request.route, SceneRoute.ORDINARY)

    def test_durable_route_resolution_rebuilds_context_before_dispatch(self) -> None:
        short_root = Path(r"D:\Cera\tmp")
        short_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=short_root) as temporary:
            coordinator, _, planner = _runtime(
                Path(temporary),
                _SemanticValidator(SemanticVerdict.PASS),
            )
            context_routes: list[SceneRoute] = []

            def context(route, *_args):
                context_routes.append(route)
                return turn(adult=route is SceneRoute.ADULT)

            adapter = PiSceneHttpAdapter(
                coordinator=coordinator,
                session_id="chat-auto",
                context_provider=context,
                logic_route_resolver=lambda _turn: SceneRoute.ADULT,
            )
            response = adapter.complete(_payload())

            # The second pair is the under-claim revalidation immediately
            # before provider dispatch.
            self.assertEqual(
                context_routes,
                [
                    SceneRoute.ORDINARY,
                    SceneRoute.ADULT,
                    SceneRoute.ORDINARY,
                    SceneRoute.ADULT,
                ],
            )
            self.assertEqual(response["model"], PI_SCENE_ADULT_MODEL)
            self.assertEqual(response["cera"]["route_mode"], "adult")
            self.assertEqual(planner.calls, 0)


if __name__ == "__main__":
    unittest.main()
