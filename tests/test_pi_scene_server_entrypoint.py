from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from cera.adult_pipeline.contracts import AdultNextRoute
from cera.pi_scene.contracts import SceneRoute
from scripts import run_pi_scene_lean_server as launcher


class PiSceneServerEntrypointTests(unittest.TestCase):
    def test_accepted_logic_route_uses_exact_turn_branch(self) -> None:
        store = Mock()
        store.current_logic_route.return_value = SimpleNamespace(
            current_logic_route=AdultNextRoute.ADULT,
        )
        turn = SimpleNamespace(world_id="world:one", branch_id="branch:one")

        route = launcher.accepted_logic_route(store, turn)  # type: ignore[arg-type]

        self.assertIs(route, SceneRoute.ADULT)
        store.current_logic_route.assert_called_once_with(
            world_id="world:one",
            branch_id="branch:one",
        )

    def test_serve_exposes_full_controller_auto_route_and_debug_location(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "runtime"
            debug_root = root / "debug" / "readable"
            controller = object()
            store = Mock()
            store.current_logic_route.return_value = SimpleNamespace(
                current_logic_route=AdultNextRoute.ORDINARY,
            )
            runtime = SimpleNamespace(
                coordinator=object(),
                store=store,
                readable_debug=SimpleNamespace(
                    root=debug_root,
                    PROTECTED_DIRECTORY="PROTECTED_ADULT",
                ),
                world_resolver=None,
                full_model_controller=controller,
                close=Mock(),
            )
            adapter = object()
            server = SimpleNamespace(
                serve_forever=Mock(),
                server_close=Mock(),
            )
            output = io.StringIO()
            with (
                patch.dict(
                    "os.environ",
                    {"CERA_PI_SCENE_TOKEN": "x" * 32},
                    clear=False,
                ),
                patch.object(launcher, "build_live_runtime", return_value=runtime),
                patch.object(
                    launcher,
                    "PiSceneHttpAdapter",
                    return_value=adapter,
                ) as adapter_type,
                patch.object(
                    launcher,
                    "build_pi_scene_server",
                    return_value=server,
                ) as server_builder,
                patch("sys.stdout", output),
            ):
                launcher.serve(
                    root,
                    port=5101,
                    session_id="compatibility-only",
                    sol_ceiling=1,
                    deepseek_ceiling=1,
                    deepseek_per_invocation_ceiling=1,
                )

            kwargs = adapter_type.call_args.kwargs
            self.assertIs(kwargs["full_model_controller"], controller)
            self.assertIs(
                kwargs["logic_route_resolver"](
                    SimpleNamespace(world_id="world:one", branch_id="branch:one")
                ),
                SceneRoute.ORDINARY,
            )
            server_builder.assert_called_once()
            self.assertIs(server_builder.call_args.args[0], adapter)
            server.serve_forever.assert_called_once_with()
            server.server_close.assert_called_once_with()
            runtime.close.assert_called_once_with()
            status = json.loads(output.getvalue())
            self.assertEqual(status["model"], "cera-alpha")
            self.assertEqual(status["endpoint"], "http://127.0.0.1:5101/v1")
            self.assertEqual(status["readable_debug_directory"], str(debug_root))
            self.assertEqual(
                status["protected_adult_debug_directory"],
                str(debug_root / "PROTECTED_ADULT"),
            )

    def test_checked_in_profile_uses_the_automatic_full_model(self) -> None:
        profile = json.loads(
            (
                Path(__file__).parents[1]
                / "integrations"
                / "sillytavern"
                / "pi_scene_lean_v1_profile.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(profile["endpoint"], "http://127.0.0.1:5101/v1")
        self.assertEqual(profile["models"]["automatic"], "cera-alpha")
        self.assertEqual(profile["route_owner"], "python_accepted_branch_state")
        self.assertEqual(
            profile["adult_craft_mode"],
            "retrieval_breadth_only_not_route_selection",
        )
        self.assertFalse(profile["creator_review_required"])
        self.assertTrue(profile["creator_review_available_on_reject"])
        self.assertNotIn("custom_include_body_yaml", profile)
        self.assertEqual(
            profile["request_controls"]["session_id"],
            "dynamic_per_sillytavern_chat",
        )
        self.assertEqual(
            profile["protected_adult_debug_directory"],
            "<runtime-root>\\debug\\readable\\PROTECTED_ADULT",
        )


if __name__ == "__main__":
    unittest.main()
