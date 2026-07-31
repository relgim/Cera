"""Versioned provider-schema projection and compatibility validation.

CERA's Python/domain schemas remain authoritative. Provider schemas are
transport projections: they may use only the dialect accepted by that
provider, and every returned object still passes the authoritative typed
decoder and cross-field validators.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from cera.errors import ContractValidationError
from cera.serialization import canonical_sha256


class ProviderSchemaDialect(StrEnum):
    OPENAI_STRUCTURED_OUTPUT_V1 = "openai_structured_output_2026_07_v1"
    DEEPSEEK_JSON_OBJECT_PROMPT_V1 = "deepseek_json_object_prompt_2026_07_v1"


@dataclass(frozen=True, slots=True)
class ProviderSchemaProjection:
    dialect: ProviderSchemaDialect
    authoritative_schema_sha256: str
    provider_schema_sha256: str
    provider_schema: dict[str, Any]
    transformed_one_of_count: int
    strict_provider_enforced: bool


@dataclass(frozen=True, slots=True)
class ProviderSchemaInventoryEntry:
    name: str
    role: str
    dialect: ProviderSchemaDialect
    projection: ProviderSchemaProjection


_OPENAI_UNSUPPORTED = frozenset(
    {
        "oneOf",
        "allOf",
        "not",
        "dependentRequired",
        "dependentSchemas",
        "if",
        "then",
        "else",
        "uniqueItems",
        "patternProperties",
    }
)
_OPENAI_SCHEMA_KEYS = frozenset(
    {
        "type",
        "properties",
        "required",
        "additionalProperties",
        "items",
        "enum",
        "const",
        "anyOf",
        "pattern",
        "format",
        "minLength",
        "maxLength",
        "multipleOf",
        "maximum",
        "exclusiveMaximum",
        "minimum",
        "exclusiveMinimum",
        "minItems",
        "maxItems",
        "description",
        "$defs",
        "$ref",
    }
)
_SUPPORTED_TYPES = frozenset(
    {"string", "number", "boolean", "integer", "object", "array", "null"}
)


def project_provider_output_schema(
    authoritative_schema: dict[str, Any],
    dialect: ProviderSchemaDialect,
) -> ProviderSchemaProjection:
    if not isinstance(authoritative_schema, dict) or not authoritative_schema:
        raise ContractValidationError("provider schema projection requires an object")
    source = deepcopy(authoritative_schema)
    projected, transformations = _project_node(source, dialect)
    if not isinstance(projected, dict):
        raise ContractValidationError("provider schema projection lost its root object")
    projected = _add_primitive_types(projected)
    validate_provider_output_schema(projected, dialect)
    return ProviderSchemaProjection(
        dialect=dialect,
        authoritative_schema_sha256=canonical_sha256(authoritative_schema),
        provider_schema_sha256=canonical_sha256(projected),
        provider_schema=projected,
        transformed_one_of_count=transformations,
        strict_provider_enforced=(
            dialect is ProviderSchemaDialect.OPENAI_STRUCTURED_OUTPUT_V1
        ),
    )


def validate_provider_output_schema(
    schema: dict[str, Any],
    dialect: ProviderSchemaDialect,
) -> None:
    if dialect is ProviderSchemaDialect.OPENAI_STRUCTURED_OUTPUT_V1:
        _OpenAISchemaAudit().validate(schema)
        return
    if dialect is ProviderSchemaDialect.DEEPSEEK_JSON_OBJECT_PROMPT_V1:
        _validate_deepseek_prompt_schema(schema)
        return
    raise ContractValidationError(f"unsupported provider schema dialect: {dialect}")


def codex_transport_probe_output_schema() -> dict[str, Any]:
    return _closed(
        {
            "status": {"type": "string", "enum": ["ok"]},
            "tools_used": {"type": "integer", "enum": [0]},
        }
    )


def codex_mcp_probe_output_schema() -> dict[str, Any]:
    return _closed(
        {
            "status": {"type": "string", "enum": ["ok"]},
            "snapshot_token": {
                "type": "string",
                "pattern": "^snapshot:[A-Za-z0-9][A-Za-z0-9._-]{0,127}$",
            },
            "tool_calls": {"type": "integer", "enum": [1]},
        }
    )


def codex_query_plan_probe_output_schema() -> dict[str, Any]:
    """Closed non-story canary for reference search followed by exact fetch."""

    return _closed(
        {
            "status": {"type": "string", "enum": ["ok"]},
            "evidence_id": {
                "type": "string",
                "pattern": "^evidence:[A-Za-z0-9][A-Za-z0-9._-]{0,127}$",
            },
            "tool_calls": {"type": "integer", "enum": [2]},
            "exact_fetch_completed": {"type": "boolean", "const": True},
        }
    )


def active_provider_schema_inventory() -> tuple[ProviderSchemaInventoryEntry, ...]:
    """Return every active CERA schema crossing a provider boundary."""

    # Lazy imports avoid coupling provider transports back into role adapters.
    from cera.composer.deepseek import deepseek_composition_draft_v6_json_schema
    from cera.realization.codex import codex_realization_verifier_draft_json_schema
    from cera.reasoner.drafts import codex_reasoner_draft_v6_json_schema

    sources = (
        (
            "codex_reasoner_draft_v6",
            "scene_reasoner",
            ProviderSchemaDialect.OPENAI_STRUCTURED_OUTPUT_V1,
            codex_reasoner_draft_v6_json_schema(),
        ),
        (
            "codex_realization_verifier_draft_v3",
            "scene_realization_verifier",
            ProviderSchemaDialect.OPENAI_STRUCTURED_OUTPUT_V1,
            codex_realization_verifier_draft_json_schema(),
        ),
        (
            "codex_transport_probe",
            "transport_probe",
            ProviderSchemaDialect.OPENAI_STRUCTURED_OUTPUT_V1,
            codex_transport_probe_output_schema(),
        ),
        (
            "codex_mcp_bridge_probe",
            "evidence_bridge_probe",
            ProviderSchemaDialect.OPENAI_STRUCTURED_OUTPUT_V1,
            codex_mcp_probe_output_schema(),
        ),
        (
            "codex_query_plan_probe",
            "evidence_query_plan_probe",
            ProviderSchemaDialect.OPENAI_STRUCTURED_OUTPUT_V1,
            codex_query_plan_probe_output_schema(),
        ),
        (
            "deepseek_composition_draft_v6",
            "scene_composer",
            ProviderSchemaDialect.DEEPSEEK_JSON_OBJECT_PROMPT_V1,
            deepseek_composition_draft_v6_json_schema(),
        ),
    )
    return tuple(
        ProviderSchemaInventoryEntry(
            name=name,
            role=role,
            dialect=dialect,
            projection=project_provider_output_schema(schema, dialect),
        )
        for name, role, dialect, schema in sources
    )


def _project_node(
    value: Any,
    dialect: ProviderSchemaDialect,
) -> tuple[Any, int]:
    if isinstance(value, list):
        projected = []
        total = 0
        for item in value:
            child, count = _project_node(item, dialect)
            projected.append(child)
            total += count
        return projected, total
    if not isinstance(value, dict):
        return value, 0
    projected: dict[str, Any] = {}
    total = 0
    for key, item in value.items():
        if key == "$schema":
            continue
        target = key
        if key == "oneOf":
            if dialect is ProviderSchemaDialect.OPENAI_STRUCTURED_OUTPUT_V1:
                if "anyOf" in value:
                    raise ContractValidationError(
                        "provider schema node cannot combine oneOf and anyOf"
                    )
                target = "anyOf"
                total += 1
            else:
                target = "anyOf"
                total += 1
        child, count = _project_node(item, dialect)
        projected[target] = child
        total += count
    return projected, total


def _add_primitive_types(value: Any) -> Any:
    if isinstance(value, list):
        return [_add_primitive_types(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {key: _add_primitive_types(item) for key, item in value.items()}
    if "type" not in result:
        sample = None
        if "const" in result:
            sample = result["const"]
        elif isinstance(result.get("enum"), list) and result["enum"]:
            sample = result["enum"][0]
        if isinstance(sample, bool):
            result["type"] = "boolean"
        elif isinstance(sample, str):
            result["type"] = "string"
        elif isinstance(sample, int):
            result["type"] = "integer"
        elif sample is None and ("const" in result or "enum" in result):
            result["type"] = "null"
    return result


class _OpenAISchemaAudit:
    def __init__(self) -> None:
        self.object_property_count = 0
        self.enum_value_count = 0
        self.schema_string_size = 0

    def validate(self, schema: dict[str, Any]) -> None:
        if schema.get("type") != "object" or "anyOf" in schema:
            raise ContractValidationError(
                "OpenAI Structured Outputs requires a non-anyOf object root"
            )
        self._walk(schema, path="$", object_depth=0)
        if self.object_property_count > 5_000:
            raise ContractValidationError(
                "OpenAI provider schema exceeds 5000 object properties"
            )
        if self.enum_value_count > 1_000:
            raise ContractValidationError(
                "OpenAI provider schema exceeds 1000 enum values"
            )
        if self.schema_string_size > 120_000:
            raise ContractValidationError(
                "OpenAI provider schema exceeds the 120000-character name/enum budget"
            )

    def _walk(self, node: Any, *, path: str, object_depth: int) -> None:
        if not isinstance(node, dict):
            raise ContractValidationError(f"provider schema node is not an object at {path}")
        forbidden = _OPENAI_UNSUPPORTED.intersection(node)
        if forbidden:
            names = ", ".join(sorted(forbidden))
            raise ContractValidationError(
                f"OpenAI provider schema uses unsupported keyword(s) {names} at {path}"
            )
        unknown = set(node).difference(_OPENAI_SCHEMA_KEYS)
        if unknown:
            names = ", ".join(sorted(unknown))
            raise ContractValidationError(
                f"OpenAI provider schema uses unknown keyword(s) {names} at {path}"
            )
        declared_type = node.get("type")
        types = (
            tuple(declared_type)
            if isinstance(declared_type, list)
            else ((declared_type,) if declared_type is not None else ())
        )
        if any(value not in _SUPPORTED_TYPES for value in types):
            raise ContractValidationError(f"unsupported provider schema type at {path}")
        if isinstance(declared_type, list) and (
            len(types) != len(set(types)) or "null" not in types
        ):
            raise ContractValidationError(
                f"provider type arrays must be unique nullable unions at {path}"
            )
        if "enum" in node:
            values = node["enum"]
            if not isinstance(values, list) or not values:
                raise ContractValidationError(f"provider enum is empty at {path}")
            self.enum_value_count += len(values)
            string_values = [value for value in values if isinstance(value, str)]
            self.schema_string_size += sum(len(value) for value in string_values)
            if len(values) > 250 and sum(len(value) for value in string_values) > 15_000:
                raise ContractValidationError(
                    f"provider enum exceeds its string budget at {path}"
                )
        if "const" in node and isinstance(node["const"], str):
            self.schema_string_size += len(node["const"])
        if "anyOf" in node:
            branches = node["anyOf"]
            if not isinstance(branches, list) or not branches:
                raise ContractValidationError(f"provider anyOf is empty at {path}")
            for index, branch in enumerate(branches):
                self._walk(
                    branch,
                    path=f"{path}.anyOf[{index}]",
                    object_depth=object_depth,
                )
        if "object" in types:
            next_depth = object_depth + 1
            if next_depth > 10:
                raise ContractValidationError(
                    f"provider object nesting exceeds ten levels at {path}"
                )
            properties = node.get("properties")
            required = node.get("required")
            if not isinstance(properties, dict):
                raise ContractValidationError(
                    f"provider object lacks properties at {path}"
                )
            if node.get("additionalProperties") is not False:
                raise ContractValidationError(
                    f"provider object must set additionalProperties false at {path}"
                )
            if required != list(properties):
                raise ContractValidationError(
                    f"provider object must require every property in schema order at {path}"
                )
            self.object_property_count += len(properties)
            self.schema_string_size += sum(len(name) for name in properties)
            for name, child in properties.items():
                self._walk(
                    child,
                    path=f"{path}.properties.{name}",
                    object_depth=next_depth,
                )
        if "array" in types:
            items = node.get("items")
            if not isinstance(items, dict):
                raise ContractValidationError(f"provider array lacks items at {path}")
            self._walk(items, path=f"{path}.items", object_depth=object_depth)
        definitions = node.get("$defs")
        if definitions is not None:
            if not isinstance(definitions, dict):
                raise ContractValidationError(f"provider definitions are invalid at {path}")
            self.schema_string_size += sum(len(name) for name in definitions)
            for name, child in definitions.items():
                self._walk(
                    child,
                    path=f"{path}.$defs.{name}",
                    object_depth=object_depth,
                )


def _validate_deepseek_prompt_schema(schema: dict[str, Any]) -> None:
    if schema.get("type") != "object" or schema.get("additionalProperties") is not False:
        raise ContractValidationError(
            "DeepSeek JSON-object prompt schema requires a closed object root"
        )
    properties = schema.get("properties")
    if not isinstance(properties, dict) or schema.get("required") != list(properties):
        raise ContractValidationError(
            "DeepSeek prompt schema must explicitly require every root field"
        )
    forbidden = _find_keywords(schema, {"oneOf", "allOf", "if", "then", "else"})
    if forbidden:
        raise ContractValidationError(
            "DeepSeek prompt schema contains non-portable composition: "
            + ", ".join(forbidden)
        )


def _find_keywords(value: Any, forbidden: set[str], path: str = "$") -> list[str]:
    findings: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if key in forbidden:
                findings.append(child_path)
            findings.extend(_find_keywords(child, forbidden, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            findings.extend(_find_keywords(child, forbidden, f"{path}[{index}]"))
    return findings


def _closed(properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }
