"""Strict dataclass decoding and versioned schema registration."""

from __future__ import annotations

from collections.abc import Mapping as MappingABC
from dataclasses import MISSING, fields, is_dataclass
from enum import Enum
from types import UnionType
from typing import Any, Literal, Mapping, Union, get_args, get_origin, get_type_hints
import sys

from .errors import ContractValidationError, IdentityError
from .ids import TypedId


def from_mapping(model_type: type[Any], payload: Mapping[str, Any]) -> Any:
    if not is_dataclass(model_type):
        raise ContractValidationError(f"{model_type!r} is not a dataclass schema")
    if not isinstance(payload, MappingABC):
        raise ContractValidationError(f"{model_type.__name__} payload must be an object")
    model_fields = {field.name: field for field in fields(model_type)}
    unknown = sorted(set(payload) - set(model_fields))
    if unknown:
        raise ContractValidationError(
            f"{model_type.__name__} contains unknown fields: {', '.join(unknown)}"
        )
    missing = [
        name
        for name, field in model_fields.items()
        if name not in payload
        and field.default is MISSING
        and field.default_factory is MISSING
    ]
    if missing:
        raise ContractValidationError(
            f"{model_type.__name__} is missing required fields: {', '.join(missing)}"
        )
    hints = _resolved_type_hints(model_type)
    decoded = {
        name: _decode(hints[name], payload[name], f"{model_type.__name__}.{name}")
        for name in payload
    }
    try:
        return model_type(**decoded)
    except ContractValidationError:
        raise
    except (TypeError, ValueError) as exc:
        raise ContractValidationError(f"invalid {model_type.__name__}: {exc}") from exc


def _decode(annotation: Any, value: Any, path: str) -> Any:
    if annotation is Any:
        return value
    if annotation is TypedId:
        try:
            return TypedId.parse(value)
        except (IdentityError, TypeError, ValueError) as exc:
            raise ContractValidationError(f"{path} is not a valid typed ID: {exc}") from exc
    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin in (Union, UnionType):
        if value is None and type(None) in args:
            return None
        failures: list[str] = []
        for option in args:
            if option is type(None):
                continue
            try:
                return _decode(option, value, path)
            except (ContractValidationError, TypeError, ValueError) as exc:
                failures.append(str(exc))
        raise ContractValidationError(f"{path} does not match its union: {failures}")
    if origin is Literal:
        if value not in args:
            raise ContractValidationError(f"{path} must be one of {args!r}")
        return value
    if origin in (tuple, list):
        if not isinstance(value, list):
            raise ContractValidationError(f"{path} must be an array")
        if origin is tuple and len(args) > 1 and args[1] is not Ellipsis:
            if len(value) != len(args):
                raise ContractValidationError(f"{path} has the wrong tuple length")
            items = [_decode(item_type, item, f"{path}[]") for item_type, item in zip(args, value)]
        else:
            item_type = args[0] if args else Any
            items = [_decode(item_type, item, f"{path}[]") for item in value]
        return tuple(items) if origin is tuple else items
    if origin in (dict, Mapping, MappingABC):
        if not isinstance(value, MappingABC):
            raise ContractValidationError(f"{path} must be an object")
        key_type, value_type = args or (Any, Any)
        return {
            _decode(key_type, key, f"{path}.key"): _decode(value_type, item, f"{path}.{key}")
            for key, item in value.items()
        }
    if isinstance(annotation, type) and issubclass(annotation, Enum):
        try:
            return annotation(value)
        except ValueError as exc:
            raise ContractValidationError(f"{path} has invalid enum value {value!r}") from exc
    if isinstance(annotation, type) and is_dataclass(annotation):
        return from_mapping(annotation, value)
    if annotation is bool:
        if type(value) is not bool:
            raise ContractValidationError(f"{path} must be boolean")
        return value
    if annotation is int:
        if type(value) is not int:
            raise ContractValidationError(f"{path} must be integer")
        return value
    if annotation is float:
        if type(value) not in (int, float):
            raise ContractValidationError(f"{path} must be numeric")
        return float(value)
    if annotation is str:
        if not isinstance(value, str):
            raise ContractValidationError(f"{path} must be string")
        return value
    return value


def _resolved_type_hints(model_type: type[Any]) -> dict[str, Any]:
    """Resolve intentionally deferred contract imports during durable decoding."""

    module = sys.modules.get(model_type.__module__)
    namespace = dict(vars(module)) if module is not None else {}
    annotations = getattr(model_type, "__annotations__", {})
    rendered = " ".join(str(value) for value in annotations.values())
    if "LiveProviderCallReceipt" in rendered:
        from cera.providers.models import LiveProviderCallReceipt

        namespace["LiveProviderCallReceipt"] = LiveProviderCallReceipt
    if "McpEvidenceBridgeReceipt" in rendered:
        from cera.reasoner.mcp_bridge import McpEvidenceBridgeReceipt

        namespace["McpEvidenceBridgeReceipt"] = McpEvidenceBridgeReceipt
    if "CodexOperationTelemetryV1" in rendered:
        from cera.providers.codex_observability import CodexOperationTelemetryV1

        namespace["CodexOperationTelemetryV1"] = CodexOperationTelemetryV1
    return get_type_hints(model_type, globalns=namespace, localns=namespace)


class SchemaRegistry:
    def __init__(self) -> None:
        self._models: dict[str, type[Any]] = {}

    def register(self, model_type: type[Any]) -> None:
        version = getattr(model_type, "SCHEMA_VERSION", None)
        if not isinstance(version, str) or not version:
            raise ContractValidationError(f"{model_type.__name__} has no SCHEMA_VERSION")
        if version in self._models:
            raise ContractValidationError(f"duplicate schema version: {version}")
        self._models[version] = model_type

    def decode(self, payload: Mapping[str, Any]) -> Any:
        version = payload.get("schema_version") if isinstance(payload, MappingABC) else None
        if not isinstance(version, str) or version not in self._models:
            raise ContractValidationError(f"unknown schema version: {version!r}")
        return from_mapping(self._models[version], payload)

    @property
    def versions(self) -> tuple[str, ...]:
        return tuple(sorted(self._models))


def require_schema(actual: str, expected: str, model_name: str) -> None:
    if actual != expected:
        raise ContractValidationError(
            f"{model_name}.schema_version must be {expected!r}, received {actual!r}"
        )
