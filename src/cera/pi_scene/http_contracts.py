"""Typed local HTTP request contracts for the lean Pi Scene route."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, ClassVar

from cera.errors import ContractValidationError

from .contracts import SceneRoute

PI_SCENE_ORDINARY_MODEL = "cera-pi-scene-ordinary"
PI_SCENE_ADULT_MODEL = "cera-pi-scene-adult"
PI_SCENE_PROFILE = "cera.pi_scene.lean.v1"

_SUPPORTED_CONTROLS = frozenset(
    {
        "cera_session_id",
        "cera_profile_id",
        "cera_scene_depth",
        "cera_regeneration_key",
        "cera_character_autonomy",
        "cera_adult_craft_mode",
        "cera_prompt_handling",
        "cera_reasoning_effort",
        "cera_scene_change",
    }
)


@dataclass(frozen=True, slots=True)
class LeanSceneRequestControlsV1:
    """Typed SillyTavern controls kept separate from exact story source."""

    SCHEMA_VERSION: ClassVar[str] = "cera.pi_scene.request_controls.v1"

    schema_version: str
    session_id: str
    scene_depth: str = "auto"
    regeneration_key: str | None = None
    character_autonomy: str = "both"
    prompt_handling: str = "adjustment"
    reasoning_effort: str = "medium"
    scene_change: bool = False

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("Pi Scene request-control schema changed")
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,95}", self.session_id):
            raise ContractValidationError("Pi Scene session identity is invalid")
        if self.scene_depth not in {"off", "short", "auto", "medium", "long", "epic"}:
            raise ContractValidationError("Pi Scene scene-depth control is invalid")
        if self.regeneration_key is not None and not re.fullmatch(
            r"[a-z][a-z0-9_-]{0,95}", self.regeneration_key
        ):
            raise ContractValidationError("Pi Scene regeneration key is invalid")
        if self.character_autonomy not in {"off", "mind", "body", "both"}:
            raise ContractValidationError("Pi Scene character-autonomy control is invalid")
        if self.prompt_handling not in {"adjustment", "modification"}:
            raise ContractValidationError("Pi Scene prompt-handling control is invalid")
        if self.reasoning_effort not in {"medium", "high", "xhigh"}:
            raise ContractValidationError("Pi Scene reasoning-effort control is invalid")
        if type(self.scene_change) is not bool:
            raise ContractValidationError("Pi Scene scene-change control must be boolean")

    @property
    def model_visible(self) -> dict[str, Any]:
        """Return only non-secret semantic controls for confined model context."""

        return _base_model_visible_controls(self)


@dataclass(frozen=True, slots=True)
class LeanSceneRequestControlsV2(LeanSceneRequestControlsV1):
    """Current controls with Adult craft retrieval breadth.

    ``adult_craft_mode`` is a presentation/craft control.  It cannot choose the
    scene route or the turn's logic owner; those remain separate Python-owned
    decisions.
    """

    SCHEMA_VERSION: ClassVar[str] = "cera.pi_scene.request_controls.v2"

    adult_craft_mode: str = "off"

    def __post_init__(self) -> None:
        LeanSceneRequestControlsV1.__post_init__(self)
        if self.adult_craft_mode not in {"off", "on", "ex"}:
            raise ContractValidationError("Pi Scene adult-craft control is invalid")

    @property
    def model_visible(self) -> dict[str, Any]:
        """Expose breadth as craft guidance without granting route authority."""

        value = _base_model_visible_controls(self)
        value.update(
            {
                "schema_version": "cera.pi_scene.model_visible_controls.v2",
                "adult_craft_mode": self.adult_craft_mode,
                "adult_craft_scope": "adult_scene_craft_retrieval_only",
                "adult_craft_route_effect": "none",
            }
        )
        return value


def _base_model_visible_controls(
    controls: LeanSceneRequestControlsV1,
) -> dict[str, Any]:
    autonomy_meaning = {
        "off": "User direction is prioritized.",
        "mind": "Character decision logic is prioritized.",
        "body": "Bodily impulse and reaction are prioritized.",
        "both": "Character decision logic and bodily impulse or reaction are prioritized.",
    }[controls.character_autonomy]
    return {
        "schema_version": "cera.pi_scene.model_visible_controls.v1",
        "scene_depth": controls.scene_depth,
        "character_autonomy": controls.character_autonomy,
        "character_autonomy_meaning": autonomy_meaning,
        "opposing_pressure_rule": (
            "Strong opposing pressure can overcome a prioritized mind or body tendency."
        ),
        "prompt_handling": controls.prompt_handling,
        "scene_change": controls.scene_change,
    }


@dataclass(frozen=True, slots=True)
class PiSceneChatRequestV1:
    route: SceneRoute
    messages: tuple[Mapping[str, str], ...]
    exact_user_source: str
    controls: LeanSceneRequestControlsV2


def parse_chat_request(
    payload: Mapping[str, Any],
    *,
    expected_session_id: str | None,
) -> PiSceneChatRequestV1:
    """Validate the strict CERA extension fields and relevant OpenAI envelope."""

    if not isinstance(payload, Mapping):
        raise ContractValidationError("chat completion body must be an object")
    unsupported = sorted(
        str(key)
        for key in payload
        if str(key).startswith("cera_") and key not in _SUPPORTED_CONTROLS
    )
    if unsupported:
        raise ContractValidationError(
            "unsupported Pi Scene controls: " + ", ".join(unsupported)
        )
    model = str(payload.get("model", ""))
    if model == PI_SCENE_ORDINARY_MODEL:
        route = SceneRoute.ORDINARY
    elif model == PI_SCENE_ADULT_MODEL:
        route = SceneRoute.ADULT
    else:
        raise ContractValidationError("Pi Scene rejects model substitution")
    if payload.get("stream", False) is not False:
        raise ContractValidationError("Pi Scene requires non-streaming requests")
    if payload.get("cera_profile_id") != PI_SCENE_PROFILE:
        raise ContractValidationError("Pi Scene rejects profile substitution")
    session_id = payload.get("cera_session_id")
    if not isinstance(session_id, str):
        raise ContractValidationError("Pi Scene requires a session identity")
    if expected_session_id is not None and session_id != expected_session_id:
        raise ContractValidationError("Pi Scene rejects session substitution")
    controls = LeanSceneRequestControlsV2(
        schema_version=LeanSceneRequestControlsV2.SCHEMA_VERSION,
        session_id=session_id,
        scene_depth=_normalized_control(payload, "cera_scene_depth", "auto"),
        regeneration_key=_optional_text_control(payload, "cera_regeneration_key"),
        character_autonomy=_normalized_control(
            payload,
            "cera_character_autonomy",
            "both",
        ),
        adult_craft_mode=_normalized_control(
            payload,
            "cera_adult_craft_mode",
            "off",
        ),
        prompt_handling=_normalized_control(
            payload,
            "cera_prompt_handling",
            "adjustment",
        ),
        reasoning_effort=_normalized_control(
            payload,
            "cera_reasoning_effort",
            "medium",
        ),
        scene_change=_boolean_control(payload, "cera_scene_change", False),
    )
    raw_messages = payload.get("messages")
    if not isinstance(raw_messages, list) or not raw_messages:
        raise ContractValidationError("Pi Scene requires chat messages")
    messages: list[Mapping[str, str]] = []
    for value in raw_messages:
        if not isinstance(value, Mapping):
            raise ContractValidationError("Pi Scene chat message is invalid")
        role = value.get("role")
        content = value.get("content")
        if role not in {"system", "user", "assistant"} or not isinstance(content, str):
            raise ContractValidationError("Pi Scene accepts text chat messages only")
        messages.append({"role": str(role), "content": content})
    sources = [value["content"] for value in messages if value["role"] == "user"]
    if not sources:
        raise ContractValidationError("Pi Scene request has no user source")
    return PiSceneChatRequestV1(
        route=route,
        messages=tuple(messages),
        exact_user_source=sources[-1],
        controls=controls,
    )


def _optional_text_control(payload: Mapping[str, Any], field_name: str) -> str | None:
    value = payload.get(field_name)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ContractValidationError(f"{field_name} must be text")
    return value


def _normalized_control(
    payload: Mapping[str, Any],
    field_name: str,
    default: str,
) -> str:
    value = payload.get(field_name)
    if value is None:
        return default
    if not isinstance(value, str):
        raise ContractValidationError(f"{field_name} must be text")
    return value.casefold()


def _boolean_control(
    payload: Mapping[str, Any],
    field_name: str,
    default: bool,
) -> bool:
    value = payload.get(field_name, default)
    if type(value) is not bool:
        raise ContractValidationError(f"{field_name} must be boolean")
    return value
