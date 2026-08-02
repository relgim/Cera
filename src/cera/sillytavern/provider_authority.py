"""Explicit provider activation receipts for non-production CERA routes.

A route profile describes construction but never grants dispatch authority.
Only a cycle/task/authorization-bound receipt can activate external provider
calls.  Provider-free fake ports use no activation receipt and remain
structurally unable to dispatch network requests.
"""

from __future__ import annotations

from typing import Any, Mapping

from cera.errors import ContractValidationError, StateConflictError
from cera.serialization import canonical_sha256, re_is_sha256


CONTINUOUS_PROVIDER_ACTIVATION_SCHEMA = "cera.continuous_provider_activation.v1"

CONTINUOUS_PROVIDER_MODELS = {
    "planner": {"model": "gpt-5.6-sol", "effort": "medium", "fast": False},
    "composer": {"model": "deepseek-v4-flash", "thinking": False},
    "validator": {"model": "gpt-5.6-terra", "effort": "high", "fast": False},
}


def validate_provider_activation(
    value: Mapping[str, Any],
    *,
    expected_cycle_id: str,
    expected_cycle_sequence: int,
    expected_job4_task_id: str,
    expected_job4_authorization_sha256: str,
    expected_route_profile_id: str,
    maximum_codex_family_calls: int,
    maximum_deepseek_calls: int,
) -> dict[str, Any]:
    """Return exact activation bytes or fail before provider construction."""

    if not isinstance(value, Mapping):
        raise ContractValidationError("provider activation must be an object")
    data = dict(value)
    expected_fields = {
        "schema_version",
        "cycle_id",
        "cycle_sequence",
        "job4_task_id",
        "job4_authorization_sha256",
        "route_profile_id",
        "provider_models",
        "maximum_codex_family_calls",
        "maximum_deepseek_calls",
        "activation_nonce",
        "authority_source_sha256",
        "activation_sha256",
    }
    if set(data) != expected_fields:
        raise ContractValidationError("provider activation fields changed")
    if (
        data.get("schema_version") != CONTINUOUS_PROVIDER_ACTIVATION_SCHEMA
        or data.get("cycle_id") != expected_cycle_id
        or data.get("cycle_sequence") != expected_cycle_sequence
        or data.get("job4_task_id") != expected_job4_task_id
        or data.get("job4_authorization_sha256")
        != expected_job4_authorization_sha256
        or data.get("route_profile_id") != expected_route_profile_id
        or data.get("provider_models") != CONTINUOUS_PROVIDER_MODELS
    ):
        raise StateConflictError("provider activation authority changed")
    codex_calls = data.get("maximum_codex_family_calls")
    deepseek_calls = data.get("maximum_deepseek_calls")
    if (
        type(codex_calls) is not int
        or type(deepseek_calls) is not int
        or not 1 <= codex_calls <= maximum_codex_family_calls
        or not 1 <= deepseek_calls <= maximum_deepseek_calls
    ):
        raise StateConflictError("provider activation exceeds remaining call authority")
    if (
        not isinstance(data.get("activation_nonce"), str)
        or not data["activation_nonce"].strip()
        or not re_is_sha256(data.get("authority_source_sha256"))
    ):
        raise ContractValidationError("provider activation source is invalid")
    activation_sha256 = data.get("activation_sha256")
    unsigned = dict(data)
    unsigned.pop("activation_sha256", None)
    if (
        not re_is_sha256(activation_sha256)
        or activation_sha256 != canonical_sha256(unsigned)
    ):
        raise StateConflictError("provider activation receipt changed")
    return data
