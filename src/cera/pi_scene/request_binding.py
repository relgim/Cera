"""Exact identity contract for one normalized Pi Scene HTTP request."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, ClassVar

from cera.errors import ContractValidationError, StateConflictError
from cera.serialization import (
    bytes_sha256,
    canonical_bytes,
    canonical_sha256,
    domain_sha256,
    re_is_sha256,
    to_primitive,
)

from .contracts import SceneRoute
from .http_contracts import (
    LeanSceneRequestControlsV1,
    LeanSceneRequestControlsV2,
    LeanSceneRequestControlsV3,
)


@dataclass(frozen=True, slots=True)
class PiSceneRequestBindingV1:
    """Exact deterministic identity of one submitted HTTP request."""

    SCHEMA_VERSION: ClassVar[str] = "cera.pi_scene.http_request_binding.v1"

    schema_version: str
    request_id: str
    normalized_request_sha256: str
    normalized_request_size_bytes: int
    session_id: str
    world_id: str
    branch_id: str
    route: SceneRoute
    route_intent: str
    controls: Mapping[str, Any]
    controls_sha256: str

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("Pi Scene request-binding schema changed")
        if not self.request_id.startswith("request-") or len(self.request_id) != 72:
            raise ContractValidationError("Pi Scene request identity is invalid")
        if not re_is_sha256(self.request_id.removeprefix("request-")):
            raise ContractValidationError("Pi Scene request identity is invalid")
        if not re_is_sha256(self.normalized_request_sha256):
            raise ContractValidationError("Pi Scene normalized request hash is invalid")
        if (
            type(self.normalized_request_size_bytes) is not int
            or self.normalized_request_size_bytes < 2
        ):
            raise ContractValidationError("Pi Scene normalized request size is invalid")
        for field_name in ("session_id", "world_id", "branch_id"):
            value = getattr(self, field_name)
            if type(value) is not str or not value.strip():
                raise ContractValidationError(f"Pi Scene request {field_name} is empty")
        if type(self.route) is not SceneRoute:
            raise ContractValidationError("Pi Scene request route is invalid")
        if self.route_intent not in {"automatic", "explicit"}:
            raise ContractValidationError("Pi Scene request route intent is invalid")
        if not isinstance(self.controls, Mapping):
            raise ContractValidationError("Pi Scene request controls are invalid")
        if not re_is_sha256(self.controls_sha256):
            raise ContractValidationError("Pi Scene request controls hash is invalid")
        if canonical_sha256(self.controls) != self.controls_sha256:
            raise ContractValidationError("Pi Scene request controls binding changed")
        if self.request_id != _request_id(self._identity_payload()):
            raise ContractValidationError("Pi Scene request identity binding changed")

    def _identity_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "normalized_request_sha256": self.normalized_request_sha256,
            "normalized_request_size_bytes": self.normalized_request_size_bytes,
            "session_id": self.session_id,
            "world_id": self.world_id,
            "branch_id": self.branch_id,
            "route": self.route.value,
            "route_intent": self.route_intent,
            "controls": dict(self.controls),
            "controls_sha256": self.controls_sha256,
        }

    def to_payload(self) -> dict[str, Any]:
        return {"request_id": self.request_id, **self._identity_payload()}


def build_request_binding(
    *,
    payload: Mapping[str, Any],
    session_id: str,
    world_id: str,
    branch_id: str,
    route: SceneRoute,
    controls: LeanSceneRequestControlsV1 | LeanSceneRequestControlsV2 | LeanSceneRequestControlsV3,
) -> PiSceneRequestBindingV1:
    """Build an identity from exact canonical request bytes and custody fields."""

    request_bytes = canonical_bytes(payload)
    controls_payload = to_primitive(controls)
    route_intent = _route_intent(payload)
    # Automatic requests bind the exact client bytes; accepted state resolves
    # the execution route separately and is bound in durable progress/proof.
    identity_route = SceneRoute.ORDINARY if route_intent == "automatic" else route
    identity = {
        "schema_version": PiSceneRequestBindingV1.SCHEMA_VERSION,
        "normalized_request_sha256": bytes_sha256(request_bytes),
        "normalized_request_size_bytes": len(request_bytes),
        "session_id": session_id,
        "world_id": world_id,
        "branch_id": branch_id,
        "route": identity_route.value,
        "route_intent": route_intent,
        "controls": controls_payload,
        "controls_sha256": canonical_sha256(controls_payload),
    }
    return PiSceneRequestBindingV1(
        schema_version=PiSceneRequestBindingV1.SCHEMA_VERSION,
        request_id=_request_id(identity),
        normalized_request_sha256=identity["normalized_request_sha256"],
        normalized_request_size_bytes=identity["normalized_request_size_bytes"],
        session_id=session_id,
        world_id=world_id,
        branch_id=branch_id,
        route=identity_route,
        route_intent=identity["route_intent"],
        controls=controls_payload,
        controls_sha256=identity["controls_sha256"],
    )


def binding_from_payload(payload: Any) -> PiSceneRequestBindingV1:
    required = {
        "schema_version",
        "request_id",
        "normalized_request_sha256",
        "normalized_request_size_bytes",
        "session_id",
        "world_id",
        "branch_id",
        "route",
        "route_intent",
        "controls",
        "controls_sha256",
    }
    if not isinstance(payload, dict) or set(payload) != required:
        raise StateConflictError("Pi Scene request binding shape changed")
    try:
        return PiSceneRequestBindingV1(
            schema_version=payload["schema_version"],
            request_id=payload["request_id"],
            normalized_request_sha256=payload["normalized_request_sha256"],
            normalized_request_size_bytes=payload["normalized_request_size_bytes"],
            session_id=payload["session_id"],
            world_id=payload["world_id"],
            branch_id=payload["branch_id"],
            route=SceneRoute(payload["route"]),
            route_intent=payload["route_intent"],
            controls=payload["controls"],
            controls_sha256=payload["controls_sha256"],
        )
    except (ContractValidationError, TypeError, ValueError) as exc:
        raise StateConflictError("Pi Scene request binding is invalid") from exc


def _request_id(identity: Mapping[str, Any]) -> str:
    return "request-" + domain_sha256("cera.pi_scene.http_request.v1", identity)


def _route_intent(payload: Mapping[str, Any]) -> str:
    return "automatic" if payload.get("model") == "cera-alpha" else "explicit"


# Private compatibility spelling retained for the existing journal decoder.
_binding_from_payload = binding_from_payload
