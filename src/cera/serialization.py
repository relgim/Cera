"""Deterministic JSON and SHA-256 helpers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import fields, is_dataclass
from enum import Enum
import hashlib
import json
import math
from typing import Any

from .errors import CanonicalizationError
from .ids import TypedId


def to_primitive(value: Any, *, path: str = "$") -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise CanonicalizationError(f"{path} contains a non-finite number")
        return value
    if isinstance(value, TypedId):
        return str(value)
    if isinstance(value, Enum):
        return to_primitive(value.value, path=path)
    if is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: to_primitive(getattr(value, field.name), path=f"{path}.{field.name}")
            for field in fields(value)
        }
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise CanonicalizationError(f"{path} contains a non-string object key")
            result[key] = to_primitive(item, path=f"{path}.{key}")
        return result
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [to_primitive(item, path=f"{path}[]") for item in value]
    raise CanonicalizationError(f"{path} contains unsupported type {type(value).__name__}")


def canonical_json(value: Any) -> str:
    return json.dumps(
        to_primitive(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def canonical_bytes(value: Any) -> bytes:
    return canonical_json(value).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def domain_sha256(domain: str, value: Any) -> str:
    if not isinstance(domain, str) or not domain.strip() or "\x00" in domain:
        raise CanonicalizationError("hash domain must be a non-empty string without NUL")
    payload = domain.encode("utf-8") + b"\x00" + canonical_bytes(value)
    return hashlib.sha256(payload).hexdigest()


def text_sha256(value: str) -> str:
    if not isinstance(value, str):
        raise CanonicalizationError("text hash input must be a string")
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def bytes_sha256(value: bytes) -> str:
    if not isinstance(value, bytes):
        raise CanonicalizationError("bytes hash input must be bytes")
    return hashlib.sha256(value).hexdigest()


def verify_sha256(value: Any, expected: str) -> bool:
    if not isinstance(expected, str) or not re_is_sha256(expected):
        return False
    return canonical_sha256(value) == expected


def verify_domain_sha256(domain: str, value: Any, expected: str) -> bool:
    if not isinstance(expected, str) or not re_is_sha256(expected):
        return False
    return domain_sha256(domain, value) == expected


def re_is_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)
