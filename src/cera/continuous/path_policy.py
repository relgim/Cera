"""Repository-owned Windows legacy-path budget checks.

The product does not depend on the host ``LongPathsEnabled`` registry value.
Callers preflight every final and same-directory temporary path that can be
reached from a creator-accepted transaction before publishing authoritative
bytes.
"""

from __future__ import annotations

from pathlib import Path

from cera.errors import StateConflictError


CONTINUOUS_WINDOWS_LEGACY_PATH_MAX_CHARACTERS = 248


def preflight_windows_legacy_paths(
    *paths: Path,
    label: str,
) -> tuple[int, ...]:
    """Return resolved path lengths or fail before a legacy Windows write."""

    if not paths:
        raise ValueError("at least one path is required for path preflight")
    resolved = tuple(path.resolve() for path in paths)
    lengths = tuple(len(str(path)) for path in resolved)
    if any(
        length > CONTINUOUS_WINDOWS_LEGACY_PATH_MAX_CHARACTERS
        for length in lengths
    ):
        raise StateConflictError(
            f"{label} path exceeds the Windows legacy path budget"
        )
    return lengths
