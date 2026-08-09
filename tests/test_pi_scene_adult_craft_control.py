from __future__ import annotations

import unittest
from pathlib import Path

from cera.errors import ContractValidationError
from cera.pi_scene.contracts import SceneRoute
from cera.pi_scene.http_contracts import (
    PI_SCENE_ADULT_MODEL,
    PI_SCENE_ORDINARY_MODEL,
    PI_SCENE_PROFILE,
    LeanSceneRequestControlsV1,
    LeanSceneRequestControlsV2,
    parse_chat_request,
)
from cera.pi_scene.review_store import LeanSceneTurnInputV1
from cera.schema import from_mapping
from cera.serialization import to_primitive


def _payload(*, model: str, mode: str | None = None) -> dict[str, object]:
    value: dict[str, object] = {
        "model": model,
        "messages": [{"role": "user", "content": "Continue."}],
        "stream": False,
        "cera_profile_id": PI_SCENE_PROFILE,
        "cera_session_id": "chat-adult-craft",
    }
    if mode is not None:
        value["cera_adult_craft_mode"] = mode
    return value


def _turn_payload(controls: LeanSceneRequestControlsV1) -> dict[str, object]:
    return {
        "world_id": "world-1",
        "branch_id": "branch-1",
        "scene_id": "scene-1",
        "exact_user_source": "Continue.",
        "current_state": {"accepted_head": "genesis"},
        "characters": {},
        "relationships": {},
        "recent_prose": [],
        "relevant_memories": {},
        "voice_examples": {},
        "craft_index": {},
        "adult_handoff": None,
        "request_controls": to_primitive(controls),
    }


class AdultCraftControlTests(unittest.TestCase):
    def test_new_requests_use_v2_and_default_to_off(self) -> None:
        request = parse_chat_request(
            _payload(model=PI_SCENE_ORDINARY_MODEL),
            expected_session_id="chat-adult-craft",
        )

        self.assertIsInstance(request.controls, LeanSceneRequestControlsV2)
        self.assertEqual(request.controls.adult_craft_mode, "off")
        self.assertEqual(request.route, SceneRoute.ORDINARY)

    def test_mode_is_normalized_and_only_changes_craft_guidance(self) -> None:
        routes = set()
        for mode in ("off", "ON", "Ex"):
            request = parse_chat_request(
                _payload(model=PI_SCENE_ORDINARY_MODEL, mode=mode),
                expected_session_id="chat-adult-craft",
            )
            routes.add(request.route)
            visible = request.controls.model_visible
            self.assertEqual(visible["adult_craft_mode"], mode.casefold())
            self.assertEqual(
                visible["adult_craft_scope"],
                "adult_scene_craft_retrieval_only",
            )
            self.assertEqual(visible["adult_craft_route_effect"], "none")
            self.assertNotIn("route", visible)

        self.assertEqual(routes, {SceneRoute.ORDINARY})

    def test_adult_model_route_does_not_depend_on_craft_mode(self) -> None:
        request = parse_chat_request(
            _payload(model=PI_SCENE_ADULT_MODEL, mode="off"),
            expected_session_id="chat-adult-craft",
        )

        self.assertEqual(request.route, SceneRoute.ADULT)
        self.assertEqual(request.controls.adult_craft_mode, "off")

    def test_invalid_mode_fails_closed(self) -> None:
        with self.assertRaisesRegex(ContractValidationError, "adult-craft"):
            parse_chat_request(
                _payload(model=PI_SCENE_ORDINARY_MODEL, mode="expanded"),
                expected_session_id="chat-adult-craft",
            )

    def test_persisted_v1_and_v2_controls_both_decode_exactly(self) -> None:
        v1 = LeanSceneRequestControlsV1(
            schema_version=LeanSceneRequestControlsV1.SCHEMA_VERSION,
            session_id="chat-legacy",
        )
        v2 = LeanSceneRequestControlsV2(
            schema_version=LeanSceneRequestControlsV2.SCHEMA_VERSION,
            session_id="chat-current",
            adult_craft_mode="ex",
        )

        legacy_turn = from_mapping(LeanSceneTurnInputV1, _turn_payload(v1))
        current_turn = from_mapping(LeanSceneTurnInputV1, _turn_payload(v2))

        self.assertIs(type(legacy_turn.request_controls), LeanSceneRequestControlsV1)
        self.assertIs(type(current_turn.request_controls), LeanSceneRequestControlsV2)
        assert isinstance(current_turn.request_controls, LeanSceneRequestControlsV2)
        self.assertEqual(current_turn.request_controls.adult_craft_mode, "ex")

    def test_repository_extension_exposes_off_on_ex_control(self) -> None:
        extension = (
            Path(__file__).parents[1]
            / "integrations"
            / "sillytavern"
            / "creator-review-extension"
            / "index.js"
        ).read_text(encoding="utf-8")

        self.assertIn("adult_craft_mode: 'off'", extension)
        self.assertIn("['off', 'Off'], ['on', 'On'], ['ex', 'Ex']", extension)
        self.assertIn("#cera_adult_craft_control", extension)


if __name__ == "__main__":
    unittest.main()
