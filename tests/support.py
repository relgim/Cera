from __future__ import annotations

from cera.ids import IdKind, TypedId


def tid(kind: IdKind, value: str | None = None) -> TypedId:
    return TypedId(kind, value or f"test-{kind.value}")


HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64

