"""Non-substitutable identities for the two isolated manual routes."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from cera.errors import ContractValidationError

from .continuous_manual import (
    CONTINUOUS_V3_MANUAL_PORT,
    CONTINUOUS_V3_MANUAL_PROFILE_ID,
    CONTINUOUS_V3_MANUAL_SERVICE,
    CONTINUOUS_V3_PROVIDER_MANUAL_PORT,
    CONTINUOUS_V3_PROVIDER_MANUAL_PROFILE_ID,
    CONTINUOUS_V3_PROVIDER_MANUAL_SERVICE,
)
from .models import (
    CERA_CONTINUOUS_V3_MANUAL_MODEL,
    CERA_CONTINUOUS_V3_PROVIDER_MANUAL_MODEL,
)


@dataclass(frozen=True, slots=True)
class ContinuousManualRoute:
    key: str
    profile_id: str
    profile_relative_path: str
    model: str
    service: str
    port: int
    default_root_name: str
    default_session_id: str
    provider_capable: bool

    def __post_init__(self) -> None:
        if (
            self.key not in {"provider_free", "provider_backed"}
            or not self.profile_id.strip()
            or not self.profile_relative_path.strip()
            or not self.model.strip()
            or not self.service.strip()
            or not 1024 <= self.port <= 65535
            or not self.default_root_name.strip()
            or not self.default_session_id.strip()
            or type(self.provider_capable) is not bool
        ):
            raise ContractValidationError("continuous manual route is invalid")

    def profile_path(self, project_root: Path) -> Path:
        return (project_root / self.profile_relative_path).resolve()


PROVIDER_FREE_MANUAL_ROUTE = ContinuousManualRoute(
    key="provider_free",
    profile_id=CONTINUOUS_V3_MANUAL_PROFILE_ID,
    profile_relative_path="integrations/sillytavern/continuous_v3_manual_profile.json",
    model=CERA_CONTINUOUS_V3_MANUAL_MODEL,
    service=CONTINUOUS_V3_MANUAL_SERVICE,
    port=CONTINUOUS_V3_MANUAL_PORT,
    default_root_name="continuous_v3",
    default_session_id="cera-continuous-manual",
    provider_capable=False,
)

PROVIDER_BACKED_MANUAL_ROUTE = ContinuousManualRoute(
    key="provider_backed",
    profile_id=CONTINUOUS_V3_PROVIDER_MANUAL_PROFILE_ID,
    profile_relative_path=(
        "integrations/sillytavern/continuous_v3_provider_manual_profile.json"
    ),
    model=CERA_CONTINUOUS_V3_PROVIDER_MANUAL_MODEL,
    service=CONTINUOUS_V3_PROVIDER_MANUAL_SERVICE,
    port=CONTINUOUS_V3_PROVIDER_MANUAL_PORT,
    default_root_name="continuous_v3_provider_backed",
    default_session_id="cera-continuous-provider-manual",
    provider_capable=True,
)


def validate_manual_profile(
    profile: Mapping[str, Any], *, route: ContinuousManualRoute
) -> dict[str, Any]:
    data = dict(profile)
    common = {
        "profile_id": route.profile_id,
        "production": False,
        "endpoint": f"http://127.0.0.1:{route.port}/v1",
        "model": route.model,
        "stream": False,
        "creator_review_required": True,
        "automatic_accept": False,
        "automatic_retry": False,
        "fallback": False,
        "automatic_false_positive": False,
        "lan_binding_allowed": False,
    }
    if any(data.get(field) != expected for field, expected in common.items()):
        raise ContractValidationError("continuous manual profile identity changed")
    if route is PROVIDER_FREE_MANUAL_ROUTE:
        if (
            data.get("route") != "continuous_v3_manual"
            or data.get("provider_mode") != "scripted_provider_free"
            or data.get("external_provider_calls_authorized") != 0
            or any(field in data for field in ("planner", "composer", "validator"))
        ):
            raise ContractValidationError("provider-free manual profile was substituted")
    else:
        expected_fields = {
            "profile_id",
            "production",
            "endpoint",
            "model",
            "stream",
            "route",
            "provider_mode",
            "provider_activation_required",
            "external_provider_calls_authorized_by_profile",
            "planner",
            "composer",
            "validator",
            "creator_review_required",
            "automatic_accept",
            "automatic_retry",
            "fallback",
            "automatic_false_positive",
            "database_policy",
            "lan_binding_allowed",
            "sillytavern_custom_endpoint",
            "notes",
        }
        if (
            set(data) != expected_fields
            or data.get("route") != "continuous_v3_manual_provider_backed"
            or data.get("provider_mode") != "authority_bound_provider"
            or data.get("provider_activation_required") is not True
            or data.get("external_provider_calls_authorized_by_profile") != 0
            or data.get("planner")
            != {
                "model": "gpt-5.6-sol",
                "reasoning_effort": "medium",
                "fast_mode": False,
            }
            or data.get("composer")
            != {"model": "deepseek-v4-flash", "thinking": False}
            or data.get("validator")
            != {
                "model": "gpt-5.6-terra",
                "reasoning_effort": "high",
                "fast_mode": False,
            }
        ):
            raise ContractValidationError("provider-backed manual profile was substituted")
    return data
