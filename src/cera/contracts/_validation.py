"""Shared structural validators for CERA contracts."""

from __future__ import annotations

from collections.abc import Iterable

from cera.errors import ContractValidationError
from cera.ids import IdKind, TypedId, require_kind
from cera.serialization import re_is_sha256


def non_empty(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ContractValidationError(f"{field_name} must be non-empty")


def sha256(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not re_is_sha256(value):
        raise ContractValidationError(f"{field_name} must be lowercase SHA-256")


def kind(value: TypedId, expected: IdKind, field_name: str) -> None:
    try:
        require_kind(value, expected, field_name)
    except Exception as exc:
        raise ContractValidationError(str(exc)) from exc


def optional_kind(value: TypedId | None, expected: IdKind, field_name: str) -> None:
    if value is not None:
        kind(value, expected, field_name)


def unique_ids(values: Iterable[TypedId], field_name: str) -> None:
    rendered = [str(value) for value in values]
    if len(rendered) != len(set(rendered)):
        raise ContractValidationError(f"{field_name} must not contain duplicate IDs")


def unique_text(values: Iterable[str], field_name: str) -> None:
    items = list(values)
    if len(items) != len(set(items)):
        raise ContractValidationError(f"{field_name} must not contain duplicates")

