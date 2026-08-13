"""OpenAI structured-output schema for the Luna semantic verdict."""

from __future__ import annotations

from typing import Any

from .contracts import SemanticConflictClass, SemanticValidationVerdictV1


def _object(properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": list(properties),
        "properties": properties,
    }


def semantic_verdict_json_schema(*, decision_keys: tuple[str, ...]) -> dict[str, Any]:
    decision_key: dict[str, Any] = {"type": "null"}
    if decision_keys:
        decision_key = {
            "oneOf": [
                {"type": "string", "enum": list(decision_keys)},
                {"type": "null"},
            ]
        }
    conflict = _object(
        {
            "conflict_class": {
                "type": "string",
                "enum": [value.value for value in SemanticConflictClass],
            },
            "concise_explanation": {"type": "string", "minLength": 1},
            "exact_quote": {
                "description": (
                    "When non-null, copy one shortest verbatim contiguous substring "
                    "byte-for-byte from exact_candidate_prose. Never paraphrase, join "
                    "fragments, normalize, abridge, or add ellipses; use null when a "
                    "valid decision_key alone truthfully anchors the conflict."
                ),
                "oneOf": [{"type": "string", "minLength": 1}, {"type": "null"}],
            },
            "decision_key": decision_key,
        }
    )
    flag = _object(
        {
            "flag_code": {
                "type": "string",
                "pattern": "^[a-z][a-z0-9_]{0,95}$",
            },
            "concise_explanation": {"type": "string", "minLength": 1},
        }
    )
    pass_branch = _object(
        {
            "schema_version": {
                "type": "string",
                "const": SemanticValidationVerdictV1.SCHEMA_VERSION,
            },
            "verdict": {"type": "string", "const": "pass"},
            "conflict": {"type": "null"},
            "review_flags": {"type": "array", "items": flag},
        }
    )
    reject_branch = _object(
        {
            "schema_version": {
                "type": "string",
                "const": SemanticValidationVerdictV1.SCHEMA_VERSION,
            },
            "verdict": {"type": "string", "const": "reject"},
            "conflict": conflict,
            "review_flags": {"type": "array", "items": flag},
        }
    )
    # The SDK accepts one non-union root object more reliably than a root oneOf.
    return _object({"result": {"oneOf": [pass_branch, reject_branch]}})
