"""Process-wide fail-closed guard for external provider dispatch.

Provider-free test runners opt in by setting
``CERA_PROVIDER_DISPATCH_DISABLED=1``.  Provider adapters must call
``assert_provider_dispatch_allowed`` before creating provider state or
recording a ledger event.  Explicitly injected offline fakes are not external
provider boundaries and may continue to exercise the surrounding contracts.
"""

from __future__ import annotations

import os

from cera.errors import StateConflictError


PROVIDER_DISPATCH_DISABLED_ENV = "CERA_PROVIDER_DISPATCH_DISABLED"
_DISABLED_VALUE = "1"


def provider_dispatch_disabled() -> bool:
    """Return whether this process forbids external provider dispatch.

    An unexpected value fails closed.  This prevents a misspelled attempt to
    enable the guard from silently restoring provider access.
    """

    value = os.environ.get(PROVIDER_DISPATCH_DISABLED_ENV)
    if value is None:
        return False
    if value == _DISABLED_VALUE:
        return True
    raise StateConflictError(
        f"{PROVIDER_DISPATCH_DISABLED_ENV} must be unset or exactly {_DISABLED_VALUE}"
    )


def assert_provider_dispatch_allowed(
    boundary: str,
    *,
    external_provider_boundary: bool = True,
) -> None:
    """Fail before an external dispatch boundary when the guard is active."""

    if not isinstance(boundary, str) or not boundary.strip():
        raise StateConflictError("provider dispatch boundary identity is empty")
    if external_provider_boundary and provider_dispatch_disabled():
        raise StateConflictError(
            "external provider dispatch is disabled by "
            f"{PROVIDER_DISPATCH_DISABLED_ENV} at {boundary}"
        )
