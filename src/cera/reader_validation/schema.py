"""Closed Sol Reader output schema adapted from the qualified Reader."""

from __future__ import annotations

from typing import Any

from cera.sequence_first.provider import reader_verdict_json_schema as _qualified_schema


def reader_verdict_json_schema(*, plan_item_keys: tuple[str, ...]) -> dict[str, Any]:
    """Keep every omission anchor inside the current cognition plan."""

    return _qualified_schema(planner_item_keys=plan_item_keys)


__all__ = ["reader_verdict_json_schema"]
