"""Pinned compatibility guard for OpenAI Codex SDK turn-start races.

OpenAI Codex Python SDK 0.144.4 registers the per-turn notification queue only
after the ``turn/start`` response arrives.  Its router buffers most early turn
events, but explicitly discards an early ``turn/completed`` notification.  A
fast provider turn can therefore complete successfully while ``Thread.run``
waits forever for the discarded terminal event.

CERA installs this process-local shim before starting any turn.  It buffers an
early completion only while that exact thread's ``turn_start`` request is in
flight, registers the returned turn queue before the wrapped call yields
control, and injects an explicit empty experimental environment list into every
turn.  The environment projection prevents fresh, resumed, and forked threads
from acquiring shell/file tools through inherited thread state.  No provider
request is repeated or replaced.
"""

from __future__ import annotations

import inspect
import threading
from collections import deque
from dataclasses import dataclass, field
from importlib.metadata import version
from typing import Any

from cera.errors import ContractValidationError
from cera.provider_dispatch_guard import (
    assert_provider_dispatch_allowed,
    is_external_provider_boundary,
)
from cera.providers.codex_runtime_policy import (
    CODEX_RUNTIME_TOOL_SURFACE_POLICY_ID,
    require_qualified_codex_model,
)
from cera.serialization import text_sha256

LEGACY_CODEX_SDK_COMPATIBILITY_ID = "cera.codex_sdk_early_completion_buffer.v1"
PREVIOUS_CODEX_SDK_COMPATIBILITY_ID = "cera.codex_sdk_completion_registration.v2"
CODEX_SDK_COMPATIBILITY_ID = (
    "cera.codex_sdk_completion_registration_no_environments.v3+"
    + CODEX_RUNTIME_TOOL_SURFACE_POLICY_ID
)
SUPPORTED_SDK_VERSION = "0.144.4"
EXPECTED_ROUTE_NOTIFICATION_SHA256 = (
    "8fd316aa949d03812e935b0928e3767d0faa1d79701f599d97e66c0e46c679d1"
)
CODEX_SDK_COMPATIBILITY_SOURCE_SHA256 = text_sha256(
    EXPECTED_ROUTE_NOTIFICATION_SHA256 + "+" + CODEX_RUNTIME_TOOL_SURFACE_POLICY_ID
)


@dataclass(slots=True)
class CodexSdkCompatibilityState:
    compatibility_id: str = CODEX_SDK_COMPATIBILITY_ID
    source_sha256: str = CODEX_SDK_COMPATIBILITY_SOURCE_SHA256
    buffered_early_completion_count: int = 0
    pre_registered_turn_count: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def record_buffered_completion(self) -> None:
        with self._lock:
            self.buffered_early_completion_count += 1

    def record_pre_registered_turn(self) -> None:
        with self._lock:
            self.pre_registered_turn_count += 1


def install_early_turn_completion_buffer(
    codex: Any,
) -> CodexSdkCompatibilityState:
    """Install the version/hash-pinned completion-buffering shim."""

    external_provider_boundary = is_external_provider_boundary(codex)
    assert_provider_dispatch_allowed(
        "providers.codex.sdk_compatibility_install",
        external_provider_boundary=external_provider_boundary,
    )
    if version("openai-codex") != SUPPORTED_SDK_VERSION:
        raise ContractValidationError(
            "Codex SDK compatibility shim does not support this SDK version"
        )
    try:
        client = codex._client
        router = client._router
        router_class = type(router)
        original_source = inspect.getsource(router_class.route_notification)
    except (AttributeError, OSError, TypeError):
        raise ContractValidationError("Codex SDK compatibility internals are unavailable") from None
    if text_sha256(original_source) != EXPECTED_ROUTE_NOTIFICATION_SHA256:
        raise ContractValidationError(
            "Codex SDK notification router source does not match the pinned shim"
        )
    required_router_fields = {
        "_lock",
        "_turn_notifications",
        "_pending_turn_notifications",
        "_notification_turn_id",
        "_notification_thread_id",
    }
    if any(not hasattr(router, field_name) for field_name in required_router_fields):
        raise ContractValidationError("Codex SDK notification router contract is incompatible")

    state = CodexSdkCompatibilityState()
    in_flight_thread_ids: set[str] = set()
    in_flight_lock = threading.Lock()
    original_turn_start = client.turn_start
    original_route_notification = router.route_notification

    def no_environment_turn_params(params: Any) -> dict[str, Any]:
        if params is None:
            projected: dict[str, Any] = {}
        elif isinstance(params, dict):
            projected = dict(params)
        else:
            model_dump = getattr(params, "model_dump", None)
            if not callable(model_dump):
                raise ContractValidationError("Codex SDK turn parameters are incompatible")
            dumped = model_dump(
                by_alias=True,
                exclude_none=True,
                mode="json",
            )
            if not isinstance(dumped, dict):
                raise ContractValidationError("Codex SDK turn parameters are incompatible")
            projected = dict(dumped)
        existing = projected.get("environments")
        if "environments" in projected and existing != []:
            raise ContractValidationError("Codex SDK turn attempted to retain an environment")
        projected["environments"] = []
        if "model" in projected:
            require_qualified_codex_model(projected["model"])
        return projected

    def guarded_turn_start(
        thread_id: str,
        input_items: Any,
        params: Any = None,
    ) -> Any:
        assert_provider_dispatch_allowed(
            "providers.codex.sdk_turn_start",
            external_provider_boundary=external_provider_boundary,
        )
        with in_flight_lock:
            if thread_id in in_flight_thread_ids:
                raise ContractValidationError(
                    "Codex SDK received overlapping turn starts for one thread"
                )
            in_flight_thread_ids.add(thread_id)
        try:
            response = original_turn_start(
                thread_id,
                input_items,
                params=no_environment_turn_params(params),
            )
            turn = getattr(response, "turn", None)
            turn_id = getattr(turn, "id", None)
            if not isinstance(turn_id, str) or not turn_id:
                raise ContractValidationError("Codex SDK turn start omitted a turn identity")
            # ``TurnHandle.stream`` registers this queue later, but the SDK
            # discards a terminal notification received after turn/start
            # returns and before that later registration.  Registration is
            # idempotent, so doing it here closes both completion races.
            router.register_turn(turn_id)
            state.record_pre_registered_turn()
            return response
        finally:
            with in_flight_lock:
                in_flight_thread_ids.discard(thread_id)

    def guarded_route_notification(notification: Any) -> None:
        if notification.method == "turn/completed":
            turn_id = router._notification_turn_id(notification)
            thread_id = router._notification_thread_id(notification)
            with in_flight_lock:
                start_is_in_flight = (
                    isinstance(thread_id, str) and thread_id in in_flight_thread_ids
                )
            if start_is_in_flight and isinstance(turn_id, str):
                with router._lock:
                    turn_queue = router._turn_notifications.get(turn_id)
                    if turn_queue is None:
                        router._pending_turn_notifications.setdefault(
                            turn_id,
                            deque(),
                        ).append(notification)
                        state.record_buffered_completion()
                        return
        original_route_notification(notification)

    client.turn_start = guarded_turn_start
    router.route_notification = guarded_route_notification
    return state
