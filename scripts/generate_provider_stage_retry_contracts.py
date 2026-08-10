"""Generate provider-stage Retry contracts from canonical JSON Schemas.

The JSON Schemas are the only hand-edited field/enum/fixture source.  This
generator emits Python and JavaScript validation projections, positive and
negative fixtures, and compact field documentation.  ``--check`` performs no
writes and fails when any generated artifact differs from the canonical input.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_ROOT = ROOT / "schemas" / "provider_stage_retry" / "v1"
PYTHON_TARGET = ROOT / "src" / "cera" / "generated" / "provider_stage_retry_contracts_v1.py"
PYTHON_INIT_TARGET = ROOT / "src" / "cera" / "generated" / "__init__.py"
JAVASCRIPT_TARGET = (
    ROOT / "integrations" / "sillytavern" / "generated" / "provider-stage-retry-contracts-v1.mjs"
)
POSITIVE_FIXTURE_TARGET = (
    ROOT / "tests" / "fixtures" / "generated" / "provider_stage_retry_v1_positive.json"
)
NEGATIVE_FIXTURE_TARGET = (
    ROOT / "tests" / "fixtures" / "generated" / "provider_stage_retry_v1_negative.json"
)
DOC_TARGET = ROOT / "docs" / "generated" / "PROVIDER_STAGE_RETRY_CONTRACTS_V1.md"
JSON_SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode()


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _load_schemas() -> list[tuple[Path, dict[str, Any], bytes]]:
    loaded: list[tuple[Path, dict[str, Any], bytes]] = []
    ids: set[str] = set()
    versions: set[str] = set()
    for path in sorted(SCHEMA_ROOT.glob("*.schema.json")):
        raw = path.read_bytes()
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError(f"{path} must contain a JSON object")
        if value.get("$schema") != JSON_SCHEMA_DIALECT:
            raise ValueError(f"{path} must use JSON Schema 2020-12")
        schema_id = value.get("$id")
        if not isinstance(schema_id, str) or schema_id in ids:
            raise ValueError(f"{path} has a missing or duplicate $id")
        ids.add(schema_id)
        version = value.get("x-cera-schema-version")
        if value.get("x-cera-support-schema") is True:
            if version is not None:
                raise ValueError(f"{path} support schema cannot publish a DTO version")
        elif not isinstance(version, str) or version in versions:
            raise ValueError(f"{path} has a missing or duplicate contract version")
        else:
            versions.add(version)
            if value.get("properties", {}).get("schema_version", {}).get("const") != version:
                raise ValueError(f"{path} schema_version const differs from its published version")
            if value.get("additionalProperties") is not False:
                raise ValueError(f"{path} published root must reject unknown fields")
            if not value.get("examples"):
                raise ValueError(f"{path} must provide generated positive fixtures")
            if not value.get("x-cera-negative-fixtures"):
                raise ValueError(f"{path} must provide generated negative fixtures")
        loaded.append((path, value, raw))
    if not loaded:
        raise ValueError(f"no schemas found under {SCHEMA_ROOT}")
    _verify_references(loaded)
    return loaded


def _verify_references(loaded: list[tuple[Path, dict[str, Any], bytes]]) -> None:
    schema_ids = {value["$id"] for _, value, _ in loaded}

    def visit(value: object, source: Path) -> None:
        if isinstance(value, dict):
            reference = value.get("$ref")
            if isinstance(reference, str):
                base = reference.partition("#")[0]
                if base and base not in schema_ids:
                    raise ValueError(f"{source} references unknown schema {base}")
            for nested in value.values():
                visit(nested, source)
        elif isinstance(value, list):
            for nested in value:
                visit(nested, source)

    for path, schema, _ in loaded:
        visit(schema, path)


def _runtime_schemas(
    loaded: list[tuple[Path, dict[str, Any], bytes]],
) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    by_id: dict[str, dict[str, Any]] = {}
    version_to_id: dict[str, str] = {}
    for _, source, _ in loaded:
        schema = copy.deepcopy(source)
        schema.pop("examples", None)
        schema.pop("x-cera-negative-fixtures", None)
        schema_id = schema["$id"]
        by_id[schema_id] = schema
        version = schema.get("x-cera-schema-version")
        if isinstance(version, str):
            version_to_id[version] = schema_id
    return by_id, version_to_id


def _resolve_source_ref(
    reference: str,
    base_id: str,
    schemas: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    schema_id, _, fragment = reference.partition("#")
    target = schemas[schema_id or base_id]
    if fragment:
        current: object = target
        for raw_part in fragment.removeprefix("/").split("/"):
            part = raw_part.replace("~1", "/").replace("~0", "~")
            if not isinstance(current, dict):
                raise ValueError(f"invalid schema pointer {reference}")
            current = current[part]
        if not isinstance(current, dict):
            raise ValueError(f"schema pointer is not an object: {reference}")
        return current
    return target


def _python_type(
    schema: dict[str, Any],
    base_id: str,
    schemas: dict[str, dict[str, Any]],
) -> str:
    reference = schema.get("$ref")
    if isinstance(reference, str):
        return _python_type(_resolve_source_ref(reference, base_id, schemas), base_id, schemas)
    if "const" in schema:
        constant = schema["const"]
        if isinstance(constant, bool):
            return f"Literal[{constant!r}]"
        if isinstance(constant, int):
            return f"Literal[{constant}]"
        if isinstance(constant, str):
            return f"Literal[{constant!r}]"
        if constant is None:
            return "None"
        if isinstance(constant, list):
            return "list[str]"
        if isinstance(constant, dict):
            return "dict[str, object]"
    alternatives = schema.get("anyOf")
    if isinstance(alternatives, list):
        parts: list[str] = []
        for alternative in alternatives:
            if isinstance(alternative, dict):
                part = _python_type(alternative, base_id, schemas)
                if part not in parts:
                    parts.append(part)
        return " | ".join(parts) if parts else "object"
    value_type = schema.get("type")
    enum = schema.get("enum")
    if isinstance(enum, list) and enum and all(isinstance(item, str | int | bool) for item in enum):
        return "Literal[" + ", ".join(repr(item) for item in enum) + "]"
    if value_type == "string":
        return "str"
    if value_type == "integer":
        return "int"
    if value_type == "boolean":
        return "bool"
    if value_type == "null":
        return "None"
    if value_type == "array":
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            return f"list[{_python_type(item_schema, base_id, schemas)}]"
        return "list[object]"
    if value_type == "object":
        return "dict[str, object]"
    return "object"


def _typed_dicts(
    loaded: list[tuple[Path, dict[str, Any], bytes]],
    runtime_schemas: dict[str, dict[str, Any]],
) -> str:
    blocks: list[str] = []
    for _, schema, _ in loaded:
        class_name = schema.get("x-cera-python-name")
        if not isinstance(class_name, str):
            continue
        properties = schema.get("properties")
        if not isinstance(properties, dict):
            raise ValueError(f"{schema['$id']} has no root properties")
        required = set(schema.get("required", []))
        if required != set(properties):
            raise ValueError(f"{schema['$id']} generated projection requires closed root fields")
        lines = [f"class {class_name}(TypedDict):"]
        for field_name, field_schema in properties.items():
            if not isinstance(field_schema, dict):
                raise ValueError(f"{schema['$id']} property {field_name} is not a schema")
            type_name = _python_type(field_schema, schema["$id"], runtime_schemas)
            lines.append(f"    {field_name}: {type_name}")
        blocks.append("\n".join(lines))
    return "\n\n\n".join(blocks)


PYTHON_RUNTIME = r'''
class ProviderStageRetryContractError(ContractValidationError):
    """A generated provider-stage Retry contract rejected a value."""


def _json_equal(left: object, right: object) -> bool:
    return json.dumps(left, sort_keys=True, separators=(",", ":")) == json.dumps(
        right, sort_keys=True, separators=(",", ":")
    )


def _resolve_pointer(value: object, fragment: str) -> object:
    current = value
    if not fragment:
        return current
    for raw_part in fragment.removeprefix("/").split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if not isinstance(current, dict) or part not in current:
            raise ProviderStageRetryContractError(f"unresolved schema pointer #{fragment}")
        current = current[part]
    return current


def _resolve_ref(reference: str, base_id: str) -> tuple[dict[str, object], str]:
    schema_id, _, fragment = reference.partition("#")
    resolved_id = schema_id or base_id
    target = SCHEMAS_BY_ID.get(resolved_id)
    if target is None:
        raise ProviderStageRetryContractError(f"unknown schema reference {resolved_id}")
    resolved = _resolve_pointer(target, fragment)
    if not isinstance(resolved, dict):
        raise ProviderStageRetryContractError(f"schema reference is not an object: {reference}")
    return resolved, resolved_id


def _is_json_type(value: object, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return type(value) is int
    if expected == "number":
        return (type(value) is int or isinstance(value, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "null":
        return value is None
    raise ProviderStageRetryContractError(f"unsupported schema type {expected}")


def _matches(value: object, schema: dict[str, object], path: str, base_id: str) -> bool:
    try:
        _validate(value, schema, path, base_id)
    except ProviderStageRetryContractError:
        return False
    return True


def _validate(value: object, schema: dict[str, object], path: str, base_id: str) -> None:
    reference = schema.get("$ref")
    if isinstance(reference, str):
        target, target_id = _resolve_ref(reference, base_id)
        _validate(value, target, path, target_id)

    all_of = schema.get("allOf")
    if isinstance(all_of, list):
        for item in all_of:
            if isinstance(item, dict):
                _validate(value, item, path, base_id)

    any_of = schema.get("anyOf")
    if isinstance(any_of, list) and not any(
        isinstance(item, dict) and _matches(value, item, path, base_id) for item in any_of
    ):
        raise ProviderStageRetryContractError(f"{path} matches no allowed contract branch")

    one_of = schema.get("oneOf")
    if isinstance(one_of, list):
        matches = sum(
            1
            for item in one_of
            if isinstance(item, dict) and _matches(value, item, path, base_id)
        )
        if matches != 1:
            raise ProviderStageRetryContractError(
                f"{path} must match exactly one contract branch; matched {matches}"
            )

    condition = schema.get("if")
    if isinstance(condition, dict):
        branch_name = "then" if _matches(value, condition, path, base_id) else "else"
        branch = schema.get(branch_name)
        if isinstance(branch, dict):
            _validate(value, branch, path, base_id)

    if "const" in schema and not _json_equal(value, schema["const"]):
        raise ProviderStageRetryContractError(f"{path} differs from its required constant")
    enum = schema.get("enum")
    if isinstance(enum, list) and not any(_json_equal(value, item) for item in enum):
        raise ProviderStageRetryContractError(f"{path} is outside its closed enumeration")

    expected_type = schema.get("type")
    if isinstance(expected_type, str) and not _is_json_type(value, expected_type):
        raise ProviderStageRetryContractError(f"{path} must be {expected_type}")

    if isinstance(value, str):
        minimum_length = schema.get("minLength")
        maximum_length = schema.get("maxLength")
        pattern = schema.get("pattern")
        if isinstance(minimum_length, int) and len(value) < minimum_length:
            raise ProviderStageRetryContractError(f"{path} is too short")
        if isinstance(maximum_length, int) and len(value) > maximum_length:
            raise ProviderStageRetryContractError(f"{path} is too long")
        if isinstance(pattern, str) and re.search(pattern, value) is None:
            raise ProviderStageRetryContractError(f"{path} does not match {pattern}")

    if (type(value) is int or isinstance(value, float)) and not isinstance(value, bool):
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        if isinstance(minimum, int | float) and value < minimum:
            raise ProviderStageRetryContractError(f"{path} is below its minimum")
        if isinstance(maximum, int | float) and value > maximum:
            raise ProviderStageRetryContractError(f"{path} exceeds its maximum")

    if isinstance(value, list):
        minimum_items = schema.get("minItems")
        maximum_items = schema.get("maxItems")
        if isinstance(minimum_items, int) and len(value) < minimum_items:
            raise ProviderStageRetryContractError(f"{path} has too few items")
        if isinstance(maximum_items, int) and len(value) > maximum_items:
            raise ProviderStageRetryContractError(f"{path} has too many items")
        if schema.get("uniqueItems") is True:
            encoded = [json.dumps(item, sort_keys=True, separators=(",", ":")) for item in value]
            if len(encoded) != len(set(encoded)):
                raise ProviderStageRetryContractError(f"{path} has duplicate items")
        prefix_items = schema.get("prefixItems")
        if isinstance(prefix_items, list):
            for index, item_schema in enumerate(prefix_items):
                if index < len(value) and isinstance(item_schema, dict):
                    _validate(value[index], item_schema, f"{path}[{index}]", base_id)
        items = schema.get("items")
        start = len(prefix_items) if isinstance(prefix_items, list) else 0
        if items is False and len(value) > start:
            raise ProviderStageRetryContractError(f"{path} has undeclared trailing items")
        if isinstance(items, dict):
            for index, item in enumerate(value[start:], start=start):
                _validate(item, items, f"{path}[{index}]", base_id)

    if isinstance(value, dict):
        required = schema.get("required")
        if isinstance(required, list):
            missing = [item for item in required if isinstance(item, str) and item not in value]
            if missing:
                raise ProviderStageRetryContractError(f"{path} lacks required fields {missing}")
        properties = schema.get("properties")
        declared = properties if isinstance(properties, dict) else {}
        for key, item in value.items():
            item_schema = declared.get(key)
            if isinstance(item_schema, dict):
                _validate(item, item_schema, f"{path}.{key}", base_id)
            elif schema.get("additionalProperties") is False:
                raise ProviderStageRetryContractError(f"{path}.{key} is not an allowed field")
            elif isinstance(schema.get("additionalProperties"), dict):
                additional = schema["additionalProperties"]
                assert isinstance(additional, dict)
                _validate(item, additional, f"{path}.{key}", base_id)

        invariants = schema.get("x-cera-invariants")
        if isinstance(invariants, list):
            for invariant in invariants:
                if not isinstance(invariant, dict):
                    raise ProviderStageRetryContractError(f"{path} has an invalid invariant")
                operation = invariant.get("op")
                message = invariant.get("message", "contract invariant failed")
                if operation == "field_gte_field":
                    left = invariant.get("left")
                    right = invariant.get("right")
                    if (
                        not isinstance(left, str)
                        or not isinstance(right, str)
                        or value.get(left) < value.get(right)  # type: ignore[operator]
                    ):
                        raise ProviderStageRetryContractError(f"{path}: {message}")
                elif operation == "allowed_field_pairs":
                    fields = invariant.get("fields")
                    allowed = invariant.get("allowed")
                    if (
                        not isinstance(fields, list)
                        or not isinstance(allowed, list)
                        or [value.get(field) for field in fields] not in allowed
                    ):
                        raise ProviderStageRetryContractError(f"{path}: {message}")
                else:
                    raise ProviderStageRetryContractError(
                        f"{path} uses unsupported invariant {operation}"
                    )


def validate_schema_version(schema_version: str, value: object) -> dict[str, object]:
    schema_id = SCHEMA_VERSION_TO_ID.get(schema_version)
    if schema_id is None:
        raise ProviderStageRetryContractError(f"unknown provider-stage schema {schema_version}")
    schema = SCHEMAS_BY_ID[schema_id]
    _validate(value, schema, "$", schema_id)
    if not isinstance(value, dict):
        raise ProviderStageRetryContractError("provider-stage contract must be an object")
    return deepcopy(value)
'''


def _render_python(
    loaded: list[tuple[Path, dict[str, Any], bytes]],
    runtime_schemas: dict[str, dict[str, Any]],
    version_to_id: dict[str, str],
) -> str:
    schemas_literal = json.dumps(runtime_schemas, indent=2, ensure_ascii=False, sort_keys=True)
    versions_literal = json.dumps(version_to_id, indent=2, ensure_ascii=False, sort_keys=True)
    typed_dicts = _typed_dicts(loaded, runtime_schemas)
    functions: list[str] = []
    exported = [
        "COMPATIBILITY_ADAPTERS",
        "ProviderStageRetryContractError",
        "SCHEMAS_BY_ID",
        "SCHEMA_VERSION_TO_ID",
        "validate_schema_version",
    ]
    compatibility_examples: list[dict[str, Any]] = []
    for _, schema, _ in loaded:
        class_name = schema.get("x-cera-python-name")
        version = schema.get("x-cera-schema-version")
        if not isinstance(class_name, str) or not isinstance(version, str):
            continue
        snake_name = _camel_to_snake(class_name)
        functions.append(
            f'''def validate_{snake_name}(value: object) -> {class_name}:
    """Validate and copy ``{version}``."""

    return cast({class_name}, validate_schema_version({version!r}, value))


def normalize_{snake_name}(value: object) -> {class_name} | None:
    """Return a validated copy, or ``None`` for an invalid boundary value."""

    try:
        return validate_{snake_name}(value)
    except ProviderStageRetryContractError:
        return None'''
        )
        exported.extend(
            [
                class_name,
                f"normalize_{snake_name}",
                f"validate_{snake_name}",
            ]
        )
        if version == "cera.provider_stage_retry_compatibility_adapter.v1":
            compatibility_examples.extend(copy.deepcopy(schema["examples"]))
    exported_literal = repr(sorted(exported))
    compatibility_literal = json.dumps(
        compatibility_examples,
        indent=2,
        ensure_ascii=False,
        sort_keys=True,
    )
    return f"""# Generated by scripts/generate_provider_stage_retry_contracts.py. DO NOT EDIT.
# ruff: noqa
# fmt: off
from __future__ import annotations

import json
import re
from copy import deepcopy
from typing import Final, Literal, TypedDict, cast

from cera.errors import ContractValidationError


{typed_dicts}


SCHEMAS_BY_ID: Final[dict[str, dict[str, object]]] = cast(
    dict[str, dict[str, object]], json.loads({schemas_literal!r})
)
SCHEMA_VERSION_TO_ID: Final[dict[str, str]] = cast(
    dict[str, str], json.loads({versions_literal!r})
)
COMPATIBILITY_ADAPTERS: Final[list[dict[str, object]]] = cast(
    list[dict[str, object]], json.loads({compatibility_literal!r})
)


{PYTHON_RUNTIME.strip()}


{("\n\n\n".join(functions))}


__all__ = {exported_literal}
# fmt: on
"""


JAVASCRIPT_RUNTIME = r"""
export class ProviderStageRetryContractError extends TypeError {}

function canonical(value) {
  if (Array.isArray(value)) return `[${value.map(canonical).join(',')}]`;
  if (plainObject(value)) {
    return `{${Object.keys(value).sort().map(key => `${JSON.stringify(key)}:${canonical(value[key])}`).join(',')}}`;
  }
  return JSON.stringify(value);
}

function jsonEqual(left, right) {
  return canonical(left) === canonical(right);
}

function plainObject(value) {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
}

function deepClone(value) {
  return typeof structuredClone === 'function'
    ? structuredClone(value)
    : JSON.parse(JSON.stringify(value));
}

function deepFreeze(value) {
  if ((plainObject(value) || Array.isArray(value)) && !Object.isFrozen(value)) {
    for (const item of Object.values(value)) deepFreeze(item);
    Object.freeze(value);
  }
  return value;
}

function resolvePointer(value, fragment) {
  let current = value;
  if (!fragment) return current;
  for (const rawPart of fragment.replace(/^\//, '').split('/')) {
    const part = rawPart.replaceAll('~1', '/').replaceAll('~0', '~');
    if (!plainObject(current) || !(part in current)) {
      throw new ProviderStageRetryContractError(`unresolved schema pointer #${fragment}`);
    }
    current = current[part];
  }
  return current;
}

function resolveRef(reference, baseId) {
  const split = reference.indexOf('#');
  const schemaId = split === -1 ? reference : reference.slice(0, split);
  const fragment = split === -1 ? '' : reference.slice(split + 1);
  const resolvedId = schemaId || baseId;
  const target = CONTRACT_SCHEMAS[resolvedId];
  if (!target) throw new ProviderStageRetryContractError(`unknown schema reference ${resolvedId}`);
  const resolved = resolvePointer(target, fragment);
  if (!plainObject(resolved)) {
    throw new ProviderStageRetryContractError(`schema reference is not an object: ${reference}`);
  }
  return [resolved, resolvedId];
}

function isJsonType(value, expected) {
  if (expected === 'object') return plainObject(value);
  if (expected === 'array') return Array.isArray(value);
  if (expected === 'string') return typeof value === 'string';
  if (expected === 'integer') return Number.isSafeInteger(value);
  if (expected === 'number') return typeof value === 'number' && Number.isFinite(value);
  if (expected === 'boolean') return typeof value === 'boolean';
  if (expected === 'null') return value === null;
  throw new ProviderStageRetryContractError(`unsupported schema type ${expected}`);
}

function matches(value, schema, path, baseId) {
  try {
    validate(value, schema, path, baseId);
    return true;
  } catch (error) {
    if (error instanceof ProviderStageRetryContractError) return false;
    throw error;
  }
}

function validate(value, schema, path, baseId) {
  if (typeof schema.$ref === 'string') {
    const [target, targetId] = resolveRef(schema.$ref, baseId);
    validate(value, target, path, targetId);
  }
  if (Array.isArray(schema.allOf)) {
    for (const item of schema.allOf) if (plainObject(item)) validate(value, item, path, baseId);
  }
  if (Array.isArray(schema.anyOf) && !schema.anyOf.some(item => plainObject(item) && matches(value, item, path, baseId))) {
    throw new ProviderStageRetryContractError(`${path} matches no allowed contract branch`);
  }
  if (Array.isArray(schema.oneOf)) {
    const count = schema.oneOf.filter(item => plainObject(item) && matches(value, item, path, baseId)).length;
    if (count !== 1) {
      throw new ProviderStageRetryContractError(`${path} must match exactly one contract branch; matched ${count}`);
    }
  }
  if (plainObject(schema.if)) {
    const branch = matches(value, schema.if, path, baseId) ? schema.then : schema.else;
    if (plainObject(branch)) validate(value, branch, path, baseId);
  }
  if ('const' in schema && !jsonEqual(value, schema.const)) {
    throw new ProviderStageRetryContractError(`${path} differs from its required constant`);
  }
  if (Array.isArray(schema.enum) && !schema.enum.some(item => jsonEqual(value, item))) {
    throw new ProviderStageRetryContractError(`${path} is outside its closed enumeration`);
  }
  if (typeof schema.type === 'string' && !isJsonType(value, schema.type)) {
    throw new ProviderStageRetryContractError(`${path} must be ${schema.type}`);
  }
  if (typeof value === 'string') {
    if (Number.isInteger(schema.minLength) && value.length < schema.minLength) {
      throw new ProviderStageRetryContractError(`${path} is too short`);
    }
    if (Number.isInteger(schema.maxLength) && value.length > schema.maxLength) {
      throw new ProviderStageRetryContractError(`${path} is too long`);
    }
    if (typeof schema.pattern === 'string' && !(new RegExp(schema.pattern)).test(value)) {
      throw new ProviderStageRetryContractError(`${path} does not match ${schema.pattern}`);
    }
  }
  if (typeof value === 'number' && Number.isFinite(value)) {
    if (typeof schema.minimum === 'number' && value < schema.minimum) {
      throw new ProviderStageRetryContractError(`${path} is below its minimum`);
    }
    if (typeof schema.maximum === 'number' && value > schema.maximum) {
      throw new ProviderStageRetryContractError(`${path} exceeds its maximum`);
    }
  }
  if (Array.isArray(value)) {
    if (Number.isInteger(schema.minItems) && value.length < schema.minItems) {
      throw new ProviderStageRetryContractError(`${path} has too few items`);
    }
    if (Number.isInteger(schema.maxItems) && value.length > schema.maxItems) {
      throw new ProviderStageRetryContractError(`${path} has too many items`);
    }
    if (schema.uniqueItems === true) {
      const encoded = value.map(canonical);
      if ((new Set(encoded)).size !== encoded.length) {
        throw new ProviderStageRetryContractError(`${path} has duplicate items`);
      }
    }
    const prefix = Array.isArray(schema.prefixItems) ? schema.prefixItems : [];
    prefix.forEach((item, index) => {
      if (index < value.length && plainObject(item)) validate(value[index], item, `${path}[${index}]`, baseId);
    });
    if (schema.items === false && value.length > prefix.length) {
      throw new ProviderStageRetryContractError(`${path} has undeclared trailing items`);
    }
    if (plainObject(schema.items)) {
      value.slice(prefix.length).forEach((item, offset) => validate(item, schema.items, `${path}[${offset + prefix.length}]`, baseId));
    }
  }
  if (plainObject(value)) {
    if (Array.isArray(schema.required)) {
      const missing = schema.required.filter(item => typeof item === 'string' && !(item in value));
      if (missing.length) throw new ProviderStageRetryContractError(`${path} lacks required fields ${missing.join(',')}`);
    }
    const declared = plainObject(schema.properties) ? schema.properties : {};
    for (const [key, item] of Object.entries(value)) {
      if (plainObject(declared[key])) validate(item, declared[key], `${path}.${key}`, baseId);
      else if (schema.additionalProperties === false) {
        throw new ProviderStageRetryContractError(`${path}.${key} is not an allowed field`);
      } else if (plainObject(schema.additionalProperties)) {
        validate(item, schema.additionalProperties, `${path}.${key}`, baseId);
      }
    }
    if (Array.isArray(schema['x-cera-invariants'])) {
      for (const invariant of schema['x-cera-invariants']) {
        if (!plainObject(invariant)) throw new ProviderStageRetryContractError(`${path} has an invalid invariant`);
        const message = invariant.message || 'contract invariant failed';
        if (invariant.op === 'field_gte_field') {
          if (typeof invariant.left !== 'string' || typeof invariant.right !== 'string' || value[invariant.left] < value[invariant.right]) {
            throw new ProviderStageRetryContractError(`${path}: ${message}`);
          }
        } else if (invariant.op === 'allowed_field_pairs') {
          const pair = Array.isArray(invariant.fields) ? invariant.fields.map(field => value[field]) : null;
          if (!pair || !Array.isArray(invariant.allowed) || !invariant.allowed.some(item => jsonEqual(item, pair))) {
            throw new ProviderStageRetryContractError(`${path}: ${message}`);
          }
        } else {
          throw new ProviderStageRetryContractError(`${path} uses unsupported invariant ${invariant.op}`);
        }
      }
    }
  }
}

export function validateSchemaVersion(schemaVersion, value) {
  const schemaId = SCHEMA_VERSION_TO_ID[schemaVersion];
  if (!schemaId) throw new ProviderStageRetryContractError(`unknown provider-stage schema ${schemaVersion}`);
  validate(value, CONTRACT_SCHEMAS[schemaId], '$', schemaId);
  if (!plainObject(value)) throw new ProviderStageRetryContractError('provider-stage contract must be an object');
  return deepClone(value);
}
"""


def _render_javascript(
    loaded: list[tuple[Path, dict[str, Any], bytes]],
    runtime_schemas: dict[str, dict[str, Any]],
    version_to_id: dict[str, str],
) -> str:
    schemas_literal = json.dumps(runtime_schemas, indent=2, ensure_ascii=False)
    versions_literal = json.dumps(version_to_id, indent=2, ensure_ascii=False)
    compatibility_examples: list[dict[str, Any]] = []
    functions: list[str] = []
    for _, schema, _ in loaded:
        base_name = schema.get("x-cera-javascript-name")
        version = schema.get("x-cera-schema-version")
        if not isinstance(base_name, str) or not isinstance(version, str):
            continue
        functions.append(
            f"""export function validate{base_name}(value) {{
  return validateSchemaVersion({json.dumps(version)}, value);
}}

export function normalize{base_name}(value) {{
  try {{
    return validate{base_name}(value);
  }} catch (error) {{
    if (error instanceof ProviderStageRetryContractError) return null;
    throw error;
  }}
}}"""
        )
        if version == "cera.provider_stage_retry_compatibility_adapter.v1":
            compatibility_examples.extend(copy.deepcopy(schema["examples"]))
    compatibility_literal = json.dumps(compatibility_examples, indent=2, ensure_ascii=False)
    return f"""// Generated by scripts/generate_provider_stage_retry_contracts.py. DO NOT EDIT.
export const CONTRACT_SCHEMAS = deepFreeze({schemas_literal});

export const SCHEMA_VERSION_TO_ID = deepFreeze({versions_literal});

export const COMPATIBILITY_ADAPTERS = deepFreeze({compatibility_literal});

{JAVASCRIPT_RUNTIME.strip()}

{("\n\n".join(functions))}
"""


def _camel_to_snake(value: str) -> str:
    output: list[str] = []
    for index, character in enumerate(value):
        if character.isupper() and index and (not value[index - 1].isupper()):
            output.append("_")
        output.append(character.lower())
    return "".join(output)


def _apply_fixture_patch(value: object, operations: object) -> object:
    if not isinstance(operations, list):
        raise ValueError("negative fixture patch must be a list")
    result = copy.deepcopy(value)
    for operation in operations:
        if not isinstance(operation, dict):
            raise ValueError("negative fixture patch operation must be an object")
        action = operation.get("op")
        pointer = operation.get("path")
        if action not in {"add", "replace", "remove"} or not isinstance(pointer, str):
            raise ValueError("negative fixture patch operation is invalid")
        parts = [
            part.replace("~1", "/").replace("~0", "~")
            for part in pointer.removeprefix("/").split("/")
            if part
        ]
        if not parts:
            raise ValueError("negative fixture cannot replace the root")
        parent = result
        for part in parts[:-1]:
            if isinstance(parent, dict):
                parent = parent[part]
            elif isinstance(parent, list):
                parent = parent[int(part)]
            else:
                raise ValueError(f"negative fixture path is invalid: {pointer}")
        leaf = parts[-1]
        if isinstance(parent, dict):
            if action == "replace" and leaf not in parent:
                raise ValueError(f"negative fixture replace target is absent: {pointer}")
            if action == "remove":
                del parent[leaf]
            else:
                parent[leaf] = copy.deepcopy(operation.get("value"))
        elif isinstance(parent, list):
            index = int(leaf)
            if action == "remove":
                parent.pop(index)
            elif action == "add":
                parent.insert(index, copy.deepcopy(operation.get("value")))
            else:
                parent[index] = copy.deepcopy(operation.get("value"))
        else:
            raise ValueError(f"negative fixture parent is invalid: {pointer}")
    return result


def _fixtures(
    loaded: list[tuple[Path, dict[str, Any], bytes]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    source_hashes = {path.name: _sha256(raw) for path, _, raw in loaded}
    positive_cases: list[dict[str, Any]] = []
    negative_cases: list[dict[str, Any]] = []
    for path, schema, _ in loaded:
        version = schema.get("x-cera-schema-version")
        if not isinstance(version, str):
            continue
        examples = schema["examples"]
        assert isinstance(examples, list)
        for index, value in enumerate(examples, start=1):
            positive_cases.append(
                {
                    "case_id": f"{path.stem}.positive.{index:02d}",
                    "contract_schema_version": version,
                    "value": copy.deepcopy(value),
                }
            )
        negative_specs = schema["x-cera-negative-fixtures"]
        assert isinstance(negative_specs, list)
        for spec in negative_specs:
            if not isinstance(spec, dict):
                raise ValueError(f"{path} negative fixture spec is not an object")
            name = spec.get("name")
            base_index = spec.get("base_example")
            if not isinstance(name, str) or type(base_index) is not int:
                raise ValueError(f"{path} negative fixture identity is invalid")
            try:
                base = examples[base_index]
            except IndexError as exc:
                raise ValueError(f"{path} negative fixture base is invalid") from exc
            negative_cases.append(
                {
                    "case_id": f"{path.stem}.negative.{name}",
                    "contract_schema_version": version,
                    "value": _apply_fixture_patch(base, spec.get("patch")),
                }
            )
    common = {
        "schema_version": "cera.provider_stage_retry_fixture_set.v1",
        "generated_from_sha256": source_hashes,
    }
    return (
        {**common, "expected_valid": True, "cases": positive_cases},
        {**common, "expected_valid": False, "cases": negative_cases},
    )


def _constraint_summary(schema: dict[str, Any]) -> str:
    parts: list[str] = []
    if "$ref" in schema:
        parts.append(str(schema["$ref"]).split("/")[-1])
    if "const" in schema:
        parts.append(f"const `{json.dumps(schema['const'], ensure_ascii=False)}`")
    if "enum" in schema:
        parts.append("enum " + ", ".join(f"`{item}`" for item in schema["enum"]))
    if "minimum" in schema or "maximum" in schema:
        parts.append(f"range {schema.get('minimum', '-∞')}..{schema.get('maximum', '∞')}")
    if "pattern" in schema:
        parts.append(f"pattern `{schema['pattern']}`")
    if "anyOf" in schema:
        parts.append("closed union")
    return "; ".join(parts) or "closed by schema"


def _render_docs(loaded: list[tuple[Path, dict[str, Any], bytes]]) -> str:
    lines = [
        "# Provider-stage Retry generated contracts V1",
        "",
        "> Generated by `scripts/generate_provider_stage_retry_contracts.py`. Do not edit.",
        "",
        "The canonical JSON Schemas are the only hand-edited contract source. Provider Retry,",
        "semantic Regenerate, and Replan remain separate identities and DTO families.",
        "",
        "## Canonical sources",
        "",
        "| Source | Published DTO | SHA-256 |",
        "|---|---|---|",
    ]
    for path, schema, raw in loaded:
        version = schema.get("x-cera-schema-version", "support schema")
        lines.append(f"| `{path.relative_to(ROOT).as_posix()}` | `{version}` | `{_sha256(raw)}` |")
    for _, schema, _ in loaded:
        version = schema.get("x-cera-schema-version")
        if not isinstance(version, str):
            continue
        lines.extend(
            [
                "",
                f"## `{version}`",
                "",
                str(schema.get("description", "")),
                "",
                "| Field | Required | Constraints | Meaning |",
                "|---|---:|---|---|",
            ]
        )
        required = set(schema.get("required", []))
        properties = schema.get("properties", {})
        assert isinstance(properties, dict)
        for field_name, field_schema in properties.items():
            assert isinstance(field_schema, dict)
            description = str(field_schema.get("description", "See canonical schema."))
            lines.append(
                f"| `{field_name}` | {'yes' if field_name in required else 'no'} | "
                f"{_constraint_summary(field_schema)} | {description} |"
            )
    lines.extend(
        [
            "",
            "## Generated integration surfaces",
            "",
            "- Python: `src/cera/generated/provider_stage_retry_contracts_v1.py`",
            "- JavaScript: `integrations/sillytavern/generated/provider-stage-retry-contracts-v1.mjs`",
            "- Positive fixtures: `tests/fixtures/generated/provider_stage_retry_v1_positive.json`",
            "- Negative fixtures: `tests/fixtures/generated/provider_stage_retry_v1_negative.json`",
            "",
            "The legacy Planner transport status is not sufficient by itself. Its compatibility",
            "metadata requires backend enrichment from the authoritative stage chain and never",
            "authorizes provider dispatch.",
            "",
        ]
    )
    return "\n".join(lines)


def _verify_generated_python(
    rendered: str,
    positive_fixture: dict[str, Any],
    negative_fixture: dict[str, Any],
) -> None:
    source_root = str(ROOT / "src")
    if source_root not in sys.path:
        sys.path.insert(0, source_root)
    namespace: dict[str, Any] = {"__name__": "_generated_provider_stage_retry_check"}
    exec(compile(rendered, str(PYTHON_TARGET), "exec"), namespace)
    validate = namespace["validate_schema_version"]
    contract_error = namespace["ProviderStageRetryContractError"]
    for case in positive_fixture["cases"]:
        validate(case["contract_schema_version"], case["value"])
    for case in negative_fixture["cases"]:
        try:
            validate(case["contract_schema_version"], case["value"])
        except contract_error:
            continue
        raise ValueError(f"negative fixture unexpectedly validates: {case['case_id']}")


def _build_outputs() -> dict[Path, bytes]:
    loaded = _load_schemas()
    runtime_schemas, version_to_id = _runtime_schemas(loaded)
    positive_fixture, negative_fixture = _fixtures(loaded)
    rendered_python = _render_python(loaded, runtime_schemas, version_to_id)
    _verify_generated_python(rendered_python, positive_fixture, negative_fixture)
    return {
        PYTHON_INIT_TARGET: (
            b'"""Generated contract projections. Do not edit generated modules by hand."""\n'
        ),
        PYTHON_TARGET: rendered_python.encode(),
        JAVASCRIPT_TARGET: _render_javascript(loaded, runtime_schemas, version_to_id).encode(),
        POSITIVE_FIXTURE_TARGET: _json_bytes(positive_fixture),
        NEGATIVE_FIXTURE_TARGET: _json_bytes(negative_fixture),
        DOC_TARGET: _render_docs(loaded).encode(),
    }


def _check(outputs: dict[Path, bytes]) -> int:
    drifted: list[str] = []
    for path, expected in outputs.items():
        if not path.is_file() or path.read_bytes() != expected:
            drifted.append(path.relative_to(ROOT).as_posix())
    if drifted:
        print("provider-stage Retry generated artifacts are stale:")
        for relative_path in drifted:
            print(f"  {relative_path}")
        print("run: python scripts/generate_provider_stage_retry_contracts.py")
        return 1
    print(f"provider-stage Retry generated artifacts are current ({len(outputs)} files)")
    return 0


def _write(outputs: dict[Path, bytes]) -> int:
    for path, content in outputs.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        print(f"generated {path.relative_to(ROOT).as_posix()}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail without writing when generated files differ from canonical schemas",
    )
    arguments = parser.parse_args()
    outputs = _build_outputs()
    return _check(outputs) if arguments.check else _write(outputs)


if __name__ == "__main__":
    raise SystemExit(main())
