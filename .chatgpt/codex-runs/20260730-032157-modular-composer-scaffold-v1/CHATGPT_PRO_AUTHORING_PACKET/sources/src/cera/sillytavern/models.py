"""Strict, minimal OpenAI-compatible SillyTavern request contracts."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, ClassVar, Mapping

from cera.errors import ContractValidationError
from cera.serialization import text_sha256


CERA_VIRTUAL_MODEL = "cera-alpha"


@dataclass(frozen=True, slots=True)
class ChatMessage:
    role: str
    content: str

    def __post_init__(self) -> None:
        if self.role not in {"system", "user", "assistant"}:
            raise ContractValidationError("unsupported SillyTavern message role")
        if not self.content:
            raise ContractValidationError("SillyTavern message content is empty")


@dataclass(frozen=True, slots=True)
class SillyTavernChatRequest:
    SCHEMA_VERSION: ClassVar[str] = "cera.sillytavern_chat_request.v1"

    model: str
    messages: tuple[ChatMessage, ...]
    stream: bool
    cera_session_id: str | None = None
    cera_scene_depth: str | None = None
    cera_regeneration_key: str | None = None

    def __post_init__(self) -> None:
        if self.model != CERA_VIRTUAL_MODEL:
            raise ContractValidationError("unknown CERA virtual model")
        if self.stream:
            raise ContractValidationError(
                "CERA human-test adapter requires non-streaming requests"
            )
        if not self.messages or not any(
            message.role == "user" for message in self.messages
        ):
            raise ContractValidationError(
                "CERA SillyTavern request requires a user message"
            )
        if self.cera_session_id is not None and not re.fullmatch(
            r"[a-z0-9][a-z0-9_-]{0,95}", self.cera_session_id
        ):
            raise ContractValidationError("invalid CERA session identity")
        if self.cera_scene_depth is not None and self.cera_scene_depth not in {
            "off",
            "auto",
            "long",
            "epic",
        }:
            raise ContractValidationError("invalid CERA scene depth")
        if self.cera_regeneration_key is not None and not re.fullmatch(
            r"[a-z][a-z0-9_-]{0,95}", self.cera_regeneration_key
        ):
            raise ContractValidationError("invalid CERA regeneration key")

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "SillyTavernChatRequest":
        if not isinstance(value, Mapping):
            raise ContractValidationError("chat completion body must be an object")
        raw_messages = value.get("messages")
        if not isinstance(raw_messages, list):
            raise ContractValidationError("chat completion messages must be an array")
        messages = []
        for item in raw_messages:
            if not isinstance(item, Mapping):
                raise ContractValidationError("chat completion message must be an object")
            content = item.get("content")
            if isinstance(content, list):
                text_parts = [
                    part.get("text")
                    for part in content
                    if isinstance(part, Mapping)
                    and part.get("type") == "text"
                    and isinstance(part.get("text"), str)
                ]
                content = "\n".join(text_parts)
            if not isinstance(content, str):
                raise ContractValidationError(
                    "CERA accepts text-only SillyTavern messages"
                )
            messages.append(ChatMessage(role=str(item.get("role", "")), content=content))
        return cls(
            model=str(value.get("model", "")),
            messages=tuple(messages),
            stream=bool(value.get("stream", False)),
            cera_session_id=_optional_control(value, "cera_session_id"),
            cera_scene_depth=_optional_control(
                value,
                "cera_scene_depth",
                normalize=True,
            ),
            cera_regeneration_key=_optional_control(
                value,
                "cera_regeneration_key",
            ),
        )

    @property
    def latest_user_content(self) -> str:
        return next(
            message.content
            for message in reversed(self.messages)
            if message.role == "user"
        )

    @property
    def conversation_sha256(self) -> str:
        payload = "\n".join(
            f"{message.role}\x1f{message.content}" for message in self.messages
        )
        return text_sha256(payload)


@dataclass(frozen=True, slots=True)
class SillyTavernTurnReply:
    prose: str
    request_id: str
    artifact_id: str
    generation: int
    provider_calls: int
    exact_replay: bool

    def __post_init__(self) -> None:
        if not self.prose.strip():
            raise ContractValidationError("accepted SillyTavern prose is empty")
        if self.generation < 1:
            raise ContractValidationError("accepted SillyTavern generation is invalid")
        if self.provider_calls not in {0, 3}:
            raise ContractValidationError(
                "SillyTavern turn must be replayed or use three provider stages"
            )
        if self.exact_replay != (self.provider_calls == 0):
            raise ContractValidationError("SillyTavern replay call count is inconsistent")


def _optional_control(
    value: Mapping[str, Any],
    field_name: str,
    *,
    normalize: bool = False,
) -> str | None:
    raw = value.get(field_name)
    if raw is None:
        return None
    if not isinstance(raw, str) or not raw.strip():
        raise ContractValidationError(f"{field_name} must be a non-empty string")
    result = raw.strip()
    return result.casefold() if normalize else result.lower()
