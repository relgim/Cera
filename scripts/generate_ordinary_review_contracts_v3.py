"""Generate the ordinary provisional-review contract family from JSON Schema.

The readable schemas under ``schemas/pi_scene/ordinary_review/v3`` are the
only hand-edited field, enum, invariant-selection, and fixture source.  This
generator deliberately reuses the mature provider-stage schema engine instead
of maintaining a second handwritten JSON-Schema interpreter.  Ordinary-only
cross-field rules and the detached terminal-decision hash are emitted here for
both Python and JavaScript from the schema-selected invariant operations.

``--check`` performs no writes and fails on generated drift.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, cast

import generate_provider_stage_retry_contracts as schema_engine  # type: ignore[import-not-found]

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_ROOT = ROOT / "schemas" / "pi_scene" / "ordinary_review" / "v3"
PYTHON_TARGET = ROOT / "src" / "cera" / "generated" / "ordinary_review_contracts_v3.py"
JAVASCRIPT_TARGET = (
    ROOT / "integrations" / "sillytavern" / "generated" / "ordinary-review-contracts-v3.mjs"
)
JAVASCRIPT_STAGED_TARGETS = (
    ROOT
    / "integrations"
    / "sillytavern"
    / "creator-review-extension"
    / "generated"
    / "ordinary-review-contracts-v3.mjs",
    ROOT
    / "integrations"
    / "sillytavern"
    / "cera-review-proxy-plugin"
    / "generated"
    / "ordinary-review-contracts-v3.mjs",
)
POSITIVE_FIXTURE_TARGET = (
    ROOT / "tests" / "fixtures" / "generated" / "ordinary_review_v3_positive.json"
)
STAGED_POSITIVE_FIXTURE_TARGET = (
    ROOT
    / "integrations"
    / "sillytavern"
    / "creator-review-extension"
    / "generated"
    / "ordinary_review_v3_positive.json"
)
NEGATIVE_FIXTURE_TARGET = (
    ROOT / "tests" / "fixtures" / "generated" / "ordinary_review_v3_negative.json"
)
DOC_TARGET = ROOT / "docs" / "generated" / "ORDINARY_REVIEW_CONTRACTS_V3.md"
JSON_SCHEMA_DIALECT = schema_engine.JSON_SCHEMA_DIALECT
PROVIDER_STATUS_ENVELOPE_VERSION = "cera.provider_stage_retry_status_envelope.v1"
DECISION_VERSION = "cera.pi_scene.review_decision.v3"
SUCCESSOR_FIXTURE_VERSION = "cera.pi_scene.ordinary_successor_fixture.v1"
DETACHED_HASH_SENTINEL = "__CERA_DETACHED_DECISION_SHA256__"


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode()


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical_sha256(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _load_raw_schemas(
    root: Path,
) -> list[tuple[Path, dict[str, Any], bytes]]:
    loaded: list[tuple[Path, dict[str, Any], bytes]] = []
    ids: set[str] = set()
    for path in sorted(root.glob("*.schema.json")):
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
        loaded.append((path, value, raw))
    if not loaded:
        raise ValueError(f"no schemas found under {root}")
    return loaded


def _validate_ordinary_schema_headers(
    loaded: list[tuple[Path, dict[str, Any], bytes]],
) -> None:
    versions: set[str] = set()
    for path, schema, _ in loaded:
        version = schema.get("x-cera-schema-version")
        if schema.get("x-cera-support-schema") is True:
            if version is not None:
                raise ValueError(f"{path} support schema cannot publish a DTO version")
            continue
        if not isinstance(version, str) or version in versions:
            raise ValueError(f"{path} has a missing or duplicate contract version")
        versions.add(version)
        if schema.get("additionalProperties") is not False and not isinstance(
            schema.get("oneOf"), list
        ):
            raise ValueError(f"{path} published root must be closed")
        if schema.get("properties", {}).get("schema_version", {}).get("const") not in {
            version,
            None,
        }:
            raise ValueError(f"{path} schema_version const differs from its version")
        if not schema.get("examples"):
            raise ValueError(f"{path} must provide a positive example")
        if not schema.get("x-cera-negative-fixtures"):
            raise ValueError(f"{path} must provide negative fixtures")


def _apply_patch(value: object, operations: object) -> object:
    return schema_engine._apply_fixture_patch(value, operations)  # noqa: SLF001


def _expand_positive_fixtures(
    loaded: list[tuple[Path, dict[str, Any], bytes]],
) -> None:
    for path, schema, _ in loaded:
        examples = schema.get("examples")
        if not isinstance(examples, list):
            continue
        base_examples = copy.deepcopy(examples)
        source_names = schema.get("x-cera-example-names")
        specifications = schema.get("x-cera-positive-fixtures")
        if source_names is None and specifications is None:
            continue
        if source_names is None:
            names = [f"base_{index + 1:02d}" for index in range(len(base_examples))]
        elif (
            not isinstance(source_names, list)
            or len(source_names) != len(base_examples)
            or not all(isinstance(name, str) and name for name in source_names)
            or len(set(source_names)) != len(source_names)
        ):
            raise ValueError(f"{path} example names changed shape")
        else:
            names = list(source_names)
        if specifications is None:
            schema["x-cera-materialized-example-names"] = names
            continue
        if not isinstance(specifications, list):
            raise ValueError(f"{path} positive fixtures changed shape")
        for specification in specifications:
            if not isinstance(specification, dict):
                raise ValueError(f"{path} positive fixture is not an object")
            name = specification.get("name")
            base_index = specification.get("base_example")
            if not isinstance(name, str) or type(base_index) is not int:
                raise ValueError(f"{path} positive fixture identity is invalid")
            try:
                base = examples[base_index]
            except IndexError as exc:
                raise ValueError(f"{path} positive fixture base is invalid") from exc
            examples.append(_apply_patch(base, specification.get("patch")))
            names.append(name)
        schema["x-cera-materialized-example-names"] = names


def _provider_external_examples(
    provider_loaded: list[tuple[Path, dict[str, Any], bytes]],
) -> dict[str, dict[str, Any]]:
    status_schema = next(
        schema
        for _, schema, _ in provider_loaded
        if schema.get("x-cera-schema-version") == PROVIDER_STATUS_ENVELOPE_VERSION
    )
    examples = status_schema.get("examples")
    if not isinstance(examples, list):
        raise ValueError("provider-stage status-envelope examples are unavailable")

    def select(state: str) -> dict[str, Any]:
        return next(
            copy.deepcopy(example)
            for example in examples
            if isinstance(example, dict)
            and isinstance(example.get("status"), dict)
            and example["status"].get("state") == state
            and (state != "in_progress" or example.get("actions") == [])
        )

    def bind(source: dict[str, Any], *, stage: str, model_family: str) -> dict[str, Any]:
        value = copy.deepcopy(source)
        status = value["status"]
        assert isinstance(status, dict)
        status["provider"] = "codex"
        status["model_family"] = model_family
        status["stage"] = stage
        return value

    output: dict[str, dict[str, Any]] = {}
    for state, fixture_name in (
        ("eligible", "manual_retry"),
        ("succeeded", "succeeded"),
        ("blocked_ambiguous", "blocked"),
        ("attempts_exhausted", "exhausted"),
    ):
        source = select(state)
        output[f"luna_{fixture_name}"] = bind(
            source,
            stage="semantic_validator",
            model_family="luna",
        )
        output[f"reader_{fixture_name}"] = bind(
            source,
            stage="reader",
            model_family="sol",
        )
    return output


def _resolve_examples(
    loaded: list[tuple[Path, dict[str, Any], bytes]],
    *,
    external_examples: dict[str, dict[str, Any]],
) -> None:
    by_version = {
        schema["x-cera-schema-version"]: schema
        for _, schema, _ in loaded
        if isinstance(schema.get("x-cera-schema-version"), str)
    }

    def resolve(value: object, stack: tuple[str, ...] = ()) -> object:
        if isinstance(value, dict):
            if set(value) == {"$cera-external-example"}:
                name = value["$cera-external-example"]
                if not isinstance(name, str) or name not in external_examples:
                    raise ValueError(f"unknown external example {name!r}")
                return copy.deepcopy(external_examples[name])
            if set(value) == {"$cera-example-ref"}:
                reference = value["$cera-example-ref"]
                if not isinstance(reference, dict):
                    raise ValueError("ordinary example reference is invalid")
                version = reference.get("schema_version")
                index = reference.get("index")
                if not isinstance(version, str) or type(index) is not int:
                    raise ValueError("ordinary example reference identity is invalid")
                marker = f"{version}:{index}"
                if marker in stack:
                    raise ValueError(f"ordinary example reference cycle: {marker}")
                target = by_version.get(version)
                examples = None if target is None else target.get("examples")
                if not isinstance(examples, list):
                    raise ValueError(f"ordinary example reference is unknown: {marker}")
                try:
                    selected = copy.deepcopy(examples[index])
                except IndexError as exc:
                    raise ValueError(f"ordinary example reference is invalid: {marker}") from exc
                patch = reference.get("patch")
                if patch is not None:
                    selected = _apply_patch(selected, patch)
                return resolve(selected, (*stack, marker))
            return {str(key): resolve(item, stack) for key, item in value.items()}
        if isinstance(value, list):
            return [resolve(item, stack) for item in value]
        return value

    for _, schema, _ in loaded:
        examples = schema.get("examples")
        if not isinstance(examples, list):
            continue
        schema["examples"] = [resolve(example) for example in examples]


def _materialize_detached_decision_hashes(
    loaded: list[tuple[Path, dict[str, Any], bytes]],
) -> None:
    decision_schema = next(
        schema for _, schema, _ in loaded if schema.get("x-cera-schema-version") == DECISION_VERSION
    )
    examples = decision_schema.get("examples")
    if not isinstance(examples, list):
        raise ValueError("ordinary decision examples are unavailable")
    for example in examples:
        if not isinstance(example, dict):
            raise ValueError("ordinary decision example is not an object")
        review = example.get("review")
        terminal = review.get("terminal_decision") if isinstance(review, dict) else None
        if not isinstance(terminal, dict):
            raise ValueError("ordinary decision example lacks its terminal link")
        if terminal.get("decision_sha256") != DETACHED_HASH_SENTINEL:
            raise ValueError("ordinary decision example lacks detached-hash sentinel")
        detached = copy.deepcopy(example)
        detached_review = detached["review"]
        assert isinstance(detached_review, dict)
        detached_review["terminal_decision"] = None
        terminal["decision_sha256"] = _canonical_sha256(detached)


def _load_schemas() -> tuple[
    list[tuple[Path, dict[str, Any], bytes]],
    list[tuple[Path, dict[str, Any], bytes]],
]:
    ordinary = _load_raw_schemas(SCHEMA_ROOT)
    _validate_ordinary_schema_headers(ordinary)
    _expand_positive_fixtures(ordinary)
    provider = schema_engine._load_schemas()  # noqa: SLF001
    _resolve_examples(
        ordinary,
        external_examples=_provider_external_examples(provider),
    )
    _materialize_detached_decision_hashes(ordinary)
    schema_engine._verify_references([*ordinary, *provider])  # noqa: SLF001
    return ordinary, provider


def _runtime_schemas(
    ordinary: list[tuple[Path, dict[str, Any], bytes]],
    provider: list[tuple[Path, dict[str, Any], bytes]],
) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    schemas, _ = schema_engine._runtime_schemas([*ordinary, *provider])  # noqa: SLF001
    for schema in schemas.values():
        schema.pop("x-cera-positive-fixtures", None)
        schema.pop("x-cera-example-names", None)
        schema.pop("x-cera-materialized-example-names", None)
    versions = {
        schema["x-cera-schema-version"]: schema["$id"]
        for _, schema, _ in ordinary
        if isinstance(schema.get("x-cera-schema-version"), str)
    }
    return schemas, versions


def _negative_value(
    *,
    base: object,
    specification: dict[str, Any],
) -> object:
    transform = specification.get("transform")
    if transform is None:
        return _apply_patch(base, specification.get("patch"))
    if transform != "terminal_hash_over_linked_payload":
        raise ValueError(f"unknown ordinary negative transform {transform!r}")
    value = copy.deepcopy(base)
    if not isinstance(value, dict):
        raise ValueError("linked terminal-hash transform requires a decision object")
    review = value.get("review")
    terminal = review.get("terminal_decision") if isinstance(review, dict) else None
    if not isinstance(terminal, dict):
        raise ValueError("linked terminal-hash transform lacks a terminal link")
    terminal["decision_sha256"] = _canonical_sha256(value)
    return value


def _fixtures(
    ordinary: list[tuple[Path, dict[str, Any], bytes]],
    provider: list[tuple[Path, dict[str, Any], bytes]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    source_hashes = {path.name: _sha256(raw) for path, _, raw in ordinary}
    external_hashes = {path.name: _sha256(raw) for path, _, raw in provider}
    positive_cases: list[dict[str, Any]] = []
    negative_cases: list[dict[str, Any]] = []
    for path, schema, _ in ordinary:
        version = schema.get("x-cera-schema-version")
        if not isinstance(version, str):
            continue
        examples = schema.get("examples")
        names = schema.get("x-cera-materialized-example-names")
        if not isinstance(examples, list):
            raise ValueError(f"{path} lacks materialized examples")
        if not isinstance(names, list):
            names = [f"positive_{index + 1:02d}" for index in range(len(examples))]
        if len(names) != len(examples):
            raise ValueError(f"{path} example names changed count")
        for name, value in zip(names, examples, strict=True):
            positive_cases.append(
                {
                    "case_id": f"{path.stem}.positive.{name}",
                    "contract_schema_version": version,
                    "value": copy.deepcopy(value),
                }
            )
        negative_specs = schema.get("x-cera-negative-fixtures")
        if not isinstance(negative_specs, list):
            raise ValueError(f"{path} lacks negative fixtures")
        for specification in negative_specs:
            if not isinstance(specification, dict):
                raise ValueError(f"{path} negative fixture is not an object")
            name = specification.get("name")
            base_index = specification.get("base_example")
            if not isinstance(name, str) or type(base_index) is not int:
                raise ValueError(f"{path} negative fixture identity is invalid")
            try:
                base = examples[base_index]
            except IndexError as exc:
                raise ValueError(f"{path} negative fixture base is invalid") from exc
            value = _negative_value(base=base, specification=specification)
            _resolve_negative_sentinels(value, ordinary, provider)
            negative_cases.append(
                {
                    "case_id": f"{path.stem}.negative.{name}",
                    "contract_schema_version": version,
                    "value": value,
                }
            )
    common = {
        "schema_version": "cera.pi_scene.ordinary_review_fixture_set.v2",
        "generated_from_sha256": source_hashes,
        "external_provider_stage_schema_sha256": external_hashes,
    }
    return (
        {**common, "expected_valid": True, "cases": positive_cases},
        {**common, "expected_valid": False, "cases": negative_cases},
    )


def _resolve_negative_sentinels(
    value: object,
    ordinary: list[tuple[Path, dict[str, Any], bytes]],
    provider: list[tuple[Path, dict[str, Any], bytes]],
) -> None:
    """Resolve sentinels introduced by a negative patch in place."""

    externals = _provider_external_examples(provider)
    by_version = {
        schema["x-cera-schema-version"]: schema
        for _, schema, _ in ordinary
        if isinstance(schema.get("x-cera-schema-version"), str)
    }

    def replace(current: object, stack: tuple[str, ...] = ()) -> object:
        if isinstance(current, dict):
            if set(current) == {"$cera-external-example"}:
                name = current["$cera-external-example"]
                if not isinstance(name, str) or name not in externals:
                    raise ValueError(f"unknown negative external example {name!r}")
                return copy.deepcopy(externals[name])
            if set(current) == {"$cera-example-ref"}:
                reference = current["$cera-example-ref"]
                if not isinstance(reference, dict):
                    raise ValueError("negative ordinary example reference is invalid")
                version = reference.get("schema_version")
                index = reference.get("index")
                if not isinstance(version, str) or type(index) is not int:
                    raise ValueError("negative ordinary example identity is invalid")
                marker = f"{version}:{index}"
                if marker in stack:
                    raise ValueError(f"negative ordinary example cycle: {marker}")
                target = by_version.get(version)
                examples = None if target is None else target.get("examples")
                if not isinstance(examples, list):
                    raise ValueError(f"negative ordinary example is unknown: {marker}")
                try:
                    selected = copy.deepcopy(examples[index])
                except IndexError as exc:
                    raise ValueError(f"negative ordinary example is invalid: {marker}") from exc
                patch = reference.get("patch")
                if patch is not None:
                    selected = _apply_patch(selected, patch)
                return replace(selected, (*stack, marker))
            return {str(key): replace(item, stack) for key, item in current.items()}
        if isinstance(current, list):
            return [replace(item, stack) for item in current]
        return current

    resolved = replace(value)
    if isinstance(value, dict) and isinstance(resolved, dict):
        value.clear()
        value.update(resolved)
    elif isinstance(value, list) and isinstance(resolved, list):
        value[:] = resolved
    elif resolved != value:
        raise ValueError("negative root sentinel changed primitive shape")


PYTHON_REVIEW_RUNTIME = r"""
ExternalValidator = Callable[[object], object]


def _review_error(message: str) -> NoReturn:
    raise OrdinaryReviewContractError(message)


def _object(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        _review_error(f"{label} must be an object")
    return cast(dict[str, object], value)


def _array(value: object, label: str) -> list[object]:
    if not isinstance(value, list):
        _review_error(f"{label} must be an array")
    return value


def _contains_forbidden_public_key(value: object) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            name = str(key).lower()
            if (
                name == "raw"
                or name.startswith("raw_")
                or name.startswith("exact_")
                or name.endswith("_path")
                or name
                in {
                    "prompt",
                    "feedback",
                    "provider_output",
                    "protected_full_record",
                    "semantic_packet",
                    "reader_packet",
                }
                or _contains_forbidden_public_key(item)
            ):
                return True
    elif isinstance(value, list):
        return any(_contains_forbidden_public_key(item) for item in value)
    return False


def _required_lanes(checks: dict[str, object]) -> tuple[dict[str, object], ...]:
    return tuple(
        _object(checks[name], f"checks.{name}")
        for name in ("luna", "reader", "python")
    )


def _derived_gate(checks: dict[str, object]) -> str:
    lanes = _required_lanes(checks)
    if any(lane["provider_stage_retry_status"] is not None for lane in lanes):
        return "blocked"
    statuses = {lane["status"] for lane in lanes}
    if "inconclusive" in statuses:
        return "blocked"
    if "reject" in statuses:
        return "reject"
    if "pending" in statuses:
        return "pending"
    if statuses == {"pass"}:
        return "pass"
    _review_error("ordinary review checks cannot derive one gate")


def _validate_failure_roles(checks: dict[str, object]) -> None:
    for name in ("luna", "reader", "python"):
        lane = _object(checks[name], f"checks.{name}")
        failures = _array(lane["failures"], f"checks.{name}.failures")
        status = lane["status"]
        sources: list[object] = []
        for index, value in enumerate(failures):
            failure = _object(value, f"checks.{name}.failures[{index}]")
            source = failure["source_kind"]
            scope = failure["feedback_scope"]
            sources.append(source)
            if name == "luna":
                if source not in {"runtime_failure", "verdict_conflict", "review_flag"}:
                    _review_error("Luna failure changed source role")
                if scope is not None:
                    _review_error("Luna failure exposed Reader feedback scope")
            elif name == "reader":
                if status == "reject":
                    if source != "reader_issue" or scope not in {
                        "exact_quote",
                        "omitted_planner_item",
                        "whole_candidate_quality",
                    }:
                        _review_error("Reader rejection changed issue provenance")
                elif source != "runtime_failure" or scope is not None:
                    _review_error("Reader runtime failure changed source role")
            elif source != "runtime_failure" or scope is not None:
                _review_error("Python failure changed source role")
        if name == "luna":
            if status == "reject":
                if sources.count("verdict_conflict") != 1 or any(
                    source not in {"verdict_conflict", "review_flag"}
                    for source in sources
                ):
                    _review_error("Luna rejection lacks one primary conflict")
            elif any(source != "runtime_failure" for source in sources):
                _review_error("non-rejecting Luna lane exposed verdict evidence")


def _all_actions_false(actions: dict[str, object]) -> bool:
    return (
        all(
            actions[name] is False
            for name in (
                "accept_enabled",
                "regenerate_enabled",
                "decline_enabled",
                "replan_enabled",
                "auditable_override_enabled",
                "repair_recording_enabled",
            )
        )
        and actions["auditable_override_action"] is None
    )


def _validate_acceptance(
    acceptance: dict[str, object],
    *,
    review_mode: object,
    gate_status: object,
) -> None:
    mode = acceptance["mode"]
    canon = acceptance["canon_status"]
    standing_policy = acceptance.get("standing_policy")
    if mode == "standing_policy":
        if (
            canon != "provisional"
            or gate_status != "reject"
            or not isinstance(standing_policy, dict)
            or standing_policy.get("authority_kind") != "standing_creator_policy"
            or standing_policy.get("policy_id") != "ordinary_provisional_continuity"
            or standing_policy.get("policy_version") != 1
            or standing_policy.get("policy_sha256")
            != "47729a4fc27046e8da768c8e0f1bc6670be48e576e606f193339de30b3bf3b23"
        ):
            _review_error("standing policy changed provisional rejected custody")
        audit_basis = {
            key: value for key, value in standing_policy.items() if key != "audit_sha256"
        }
        expected_audit_sha256 = hashlib.sha256(
            json.dumps(
                audit_basis,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        ).hexdigest()
        if standing_policy.get("audit_sha256") != expected_audit_sha256:
            _review_error("standing policy changed its audit binding")
        return
    if standing_policy is not None:
        _review_error("non-policy acceptance exposed standing-policy authority")
    if mode == "auditable_override":
        if canon != "provisional" or gate_status != "reject":
            _review_error("auditable override changed provisional rejected custody")
        return
    if canon != "accepted" or gate_status != "pass" or mode != review_mode:
        _review_error("ordinary accepted mode, canon, review mode, and gate differ")


def _validate_standing_policy_review(
    review: dict[str, object],
    acceptance: dict[str, object],
) -> None:
    if acceptance["mode"] != "standing_policy":
        return
    authority = _object(acceptance["standing_policy"], "acceptance.standing_policy")
    checks = _object(review["checks"], "review.checks")
    luna = _object(checks["luna"], "checks.luna")
    reader = _object(checks["reader"], "checks.reader")
    python_lane = _object(checks["python"], "checks.python")
    expected_reasons: set[str] = set()
    if luna["status"] == "reject":
        conflict = next(
            _object(value, "checks.luna.failure")
            for value in _array(luna["failures"], "checks.luna.failures")
            if _object(value, "checks.luna.failure")["source_kind"]
            == "verdict_conflict"
        )
        code = conflict["code"]
        if code not in {
            "omitted_decision",
            "presence_violation",
            "stopping_boundary",
            "capability_restriction",
        }:
            _review_error("standing policy accepted a hard Luna conflict")
        expected_reasons.add(f"luna:{code}")
    if reader["status"] == "reject":
        for value in _array(reader["failures"], "checks.reader.failures"):
            failure = _object(value, "checks.reader.failure")
            scope = failure["feedback_scope"]
            if scope not in {"exact_quote", "omitted_planner_item"}:
                _review_error("standing policy accepted a hard Reader issue")
            expected_reasons.add(f"reader:{scope}")
    reasons = _array(
        authority["tolerated_reason_codes"],
        "acceptance.standing_policy.tolerated_reason_codes",
    )
    if reasons != sorted(expected_reasons):
        _review_error("standing policy reasons differ from rejected lanes")
    if (
        authority["candidate_sha256"] != review["candidate_sha256"]
        or authority["semantic_validation_sha256"] != luna["verdict_sha256"]
        or authority["reader_validation_sha256"] != reader["verdict_sha256"]
        or authority["python_qualification_sha256"] != python_lane["verdict_sha256"]
    ):
        _review_error("standing policy audit differs from validator custody")


def _validate_actions(review: dict[str, object]) -> None:
    actions = _object(review["actions"], "review.actions")
    state = review["state"]
    gate = review["gate_status"]
    mode = review["review_mode"]
    recording = review["recording_status"]
    semantic_names = (
        "accept_enabled",
        "regenerate_enabled",
        "decline_enabled",
        "replan_enabled",
        "auditable_override_enabled",
    )
    if state == "review_ready" and gate == "pass":
        if mode != "manual":
            _review_error("automatic pass cannot await creator Accept")
        expected = {
            "accept_enabled": True,
            "regenerate_enabled": True,
            "decline_enabled": True,
            "replan_enabled": False,
            "auditable_override_enabled": False,
        }
        if any(actions[name] is not expected[name] for name in semantic_names):
            _review_error("manual pass actions changed")
        if actions["auditable_override_action"] is not None:
            _review_error("manual pass exposed an override action")
        if actions["repair_recording_enabled"] is not False:
            _review_error("unaccepted review exposed Recorder repair")
        return
    if state == "review_ready" and gate == "reject":
        expected = {
            "accept_enabled": False,
            "regenerate_enabled": True,
            "decline_enabled": True,
            "replan_enabled": False,
            "auditable_override_enabled": True,
        }
        if any(actions[name] is not expected[name] for name in semantic_names):
            _review_error("rejected review actions changed")
        if actions["auditable_override_action"] != "accept_provisional":
            _review_error("rejected review lost its auditable override identity")
        if actions["repair_recording_enabled"] is not False:
            _review_error("unaccepted review exposed Recorder repair")
        return
    if state == "accepted":
        if any(actions[name] is not False for name in semantic_names):
            _review_error("accepted review exposed a semantic decision action")
        if actions["auditable_override_action"] is not None:
            _review_error("accepted review retained override authority")
        repair = actions["repair_recording_enabled"]
        if repair is True and recording not in {"projection_pending", "pending_repair"}:
            _review_error("Recorder repair lacks pending recording custody")
        if recording in {None, "complete"} and repair is not False:
            _review_error("completed or absent recording exposed repair")
        return
    if not _all_actions_false(actions):
        _review_error("non-actionable review exposed an action")


def _expected_disposition(checks: dict[str, object], gate: object) -> str:
    if gate == "pending":
        return "checks_pending"
    if gate == "blocked":
        return "checks_blocked"
    if gate == "pass":
        return "checks_passed"
    luna = _object(checks["luna"], "checks.luna")["status"] == "reject"
    reader = _object(checks["reader"], "checks.reader")["status"] == "reject"
    if luna and reader:
        return "luna_reader_rejected"
    if luna:
        return "luna_rejected"
    if reader:
        return "reader_rejected"
    _review_error("rejected gate lacks a rejecting validator")


def _validate_provider_accounting(review: dict[str, object]) -> None:
    attempts = _array(review["provider_attempts"], "review.provider_attempts")
    operations = _object(review["provider_operations"], "review.provider_operations")
    roles = ("planner", "writer", "validator", "reader")
    totals = {role: 0 for role in roles}
    for index, item in enumerate(attempts, start=1):
        attempt = _object(item, f"review.provider_attempts[{index - 1}]")
        if attempt["attempt_number"] != index:
            _review_error("ordinary provider attempt numbers are not contiguous")
        attempt_operations = _object(
            attempt["provider_operations"],
            f"review.provider_attempts[{index - 1}].provider_operations",
        )
        for role in roles:
            totals[role] += cast(int, attempt_operations[role])
            if totals[role] > 9_007_199_254_740_991:
                _review_error("ordinary provider accounting exceeds the JS-safe ceiling")
    for role in roles:
        if operations[role] != totals[role]:
            _review_error("ordinary provider operation totals differ from attempts")
    terminal = _object(attempts[-1], "review.provider_attempts[-1]")
    if terminal["candidate_id"] != review["candidate_id"]:
        _review_error("terminal provider attempt changed frozen candidate")
    expected = _expected_disposition(
        _object(review["checks"], "review.checks"),
        review["gate_status"],
    )
    if terminal["disposition"] != expected:
        _review_error("terminal provider attempt disposition differs from checks")


def _validate_terminal_link(review: dict[str, object]) -> None:
    state = review["state"]
    resolved = state in {"accepted", "declined", "regenerated", "replanned"}
    terminal = review["terminal_decision"]
    if resolved != (terminal is not None):
        _review_error("ordinary review resolution and terminal link differ")
    if terminal is None:
        return
    link = _object(terminal, "review.terminal_decision")
    expected_url = f"/v1/cera/reviews/{review['review_id']}/terminal-decision"
    if link["url"] != expected_url:
        _review_error("ordinary terminal decision URL changed review identity")


def _validate_review_mode_controls(review: dict[str, object]) -> None:
    controls = _object(review["request_controls"], "review.request_controls")
    if controls["schema_version"] != "cera.pi_scene.request_controls.v3":
        _review_error("ordinary review v3 requires request-controls v3")
    if review["review_mode"] != controls["review_mode"]:
        _review_error("ordinary review mode differs from its request controls")


def _validate_review(review: dict[str, object]) -> None:
    if _contains_forbidden_public_key(review):
        _review_error("ordinary review contains a forbidden public key")
    checks = _object(review["checks"], "review.checks")
    _validate_failure_roles(checks)
    gate = _derived_gate(checks)
    if review["gate_status"] != gate:
        _review_error("ordinary review gate differs from independent checks")
    state = review["state"]
    acceptance_value = review["acceptance"]
    acceptance = None if acceptance_value is None else _object(acceptance_value, "acceptance")
    recording = review["recording_status"]
    if state == "checks_pending":
        if gate not in {"pending", "blocked", "reject"}:
            _review_error("checks-pending review has a terminal semantic gate")
    elif state == "review_ready":
        if gate not in {"pass", "reject"}:
            _review_error("review-ready candidate lacks a creator decision gate")
    elif state == "accepted":
        if gate not in {"pass", "reject"}:
            _review_error("accepted review lacks a qualified or override gate")
    elif state in {"declined", "regenerated"}:
        if gate not in {"pass", "reject"}:
            _review_error("resolved creator choice changed its completed validator gate")
    elif state == "replanned":
        if gate != "reject":
            _review_error("resolved Replan changed its rejected validator gate")
    else:
        _review_error("ordinary review state is not closed")
    if (state == "accepted") != (acceptance is not None):
        _review_error("ordinary accepted state and acceptance receipt differ")
    if acceptance is not None:
        _validate_acceptance(
            acceptance,
            review_mode=review["review_mode"],
            gate_status=gate,
        )
        _validate_standing_policy_review(review, acceptance)
    if state != "accepted" and recording is not None:
        _review_error("unaccepted review contains a recording status")
    _validate_actions(review)
    _validate_terminal_link(review)
    _validate_provider_accounting(review)
    _validate_review_mode_controls(review)


def _validate_initial_lifecycle(value: dict[str, object]) -> None:
    if _contains_forbidden_public_key(value):
        _review_error("ordinary lifecycle contains a forbidden public key")
    expected_url = f"/v1/cera/reviews/{value['review_id']}"
    if value["review_url"] != expected_url:
        _review_error("ordinary lifecycle URL changed review identity")
    checks = _object(value["checks"], "review_lifecycle.checks")
    _validate_failure_roles(checks)
    for name in ("luna", "reader", "python"):
        lane = _object(checks[name], f"review_lifecycle.checks.{name}")
        if (
            lane["status"] != "pending"
            or lane["verdict_sha256"] is not None
            or lane["failures"] != []
            or lane["provider_stage_retry_status"] is not None
        ):
            _review_error("initial review lifecycle contains completed or blocked work")
    if not _all_actions_false(_object(value["actions"], "review_lifecycle.actions")):
        _review_error("initial review lifecycle exposes an action")


def _validate_decision(value: dict[str, object]) -> None:
    if _contains_forbidden_public_key(value):
        _review_error("ordinary review decision contains a forbidden public key")
    review = _object(value["review"], "review_decision.review")
    _validate_review(review)
    action = value["creator_action"]
    state = review["state"]
    acceptance_value = review["acceptance"]
    acceptance = None if acceptance_value is None else _object(acceptance_value, "acceptance")
    expected_state = {
        "automatic_accept": "accepted",
        "accept": "accepted",
        "accept_provisional": "accepted",
        "standing_policy_accept_provisional": "accepted",
        "repair_recording": "accepted",
        "decline": "declined",
        "regenerate": "regenerated",
        "replan": "replanned",
    }[cast(str, action)]
    if state != expected_state:
        _review_error("ordinary decision action differs from terminal review state")
    if action in {
        "automatic_accept",
        "accept",
        "accept_provisional",
        "standing_policy_accept_provisional",
    }:
        assert acceptance is not None
        expected_mode = {
            "automatic_accept": "automatic",
            "accept": "manual",
            "accept_provisional": "auditable_override",
            "standing_policy_accept_provisional": "standing_policy",
        }[action]
        if acceptance["mode"] != expected_mode:
            _review_error("ordinary decision action differs from acceptance mode")
    if action == "repair_recording" and review["recording_status"] != "complete":
        _review_error("Recorder-repair decision is not complete")
    committed = value["story_state_committed"] is True
    if committed:
        assert acceptance is not None
        if (
            value["accepted_receipt_sha256"] != acceptance["accepted_receipt_sha256"]
            or value["accepted_turn_id"] != acceptance["accepted_turn_id"]
        ):
            _review_error("ordinary decision changed its accepted receipt identity")
    successor = value["successor"]
    if action in {"regenerate", "replan"}:
        if successor is None:
            _review_error("ordinary successor decision omitted its exact completion")
    elif successor is not None:
        _review_error("ordinary non-successor decision contains a completion")
    terminal = _object(review["terminal_decision"], "review.terminal_decision")
    detached = deepcopy(value)
    detached_review = _object(detached["review"], "detached.review")
    detached_review["terminal_decision"] = None
    expected_hash = hashlib.sha256(
        json.dumps(
            detached,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    if terminal["decision_sha256"] != expected_hash:
        _review_error("ordinary terminal link differs from detached decision bytes")


def _apply_external_validators(
    schema_version: str,
    value: dict[str, object],
    external_validators: Mapping[str, ExternalValidator] | None,
) -> dict[str, object]:
    schema_id = SCHEMA_VERSION_TO_ID[schema_version]
    specifications = SCHEMAS_BY_ID[schema_id].get("x-cera-external-validators")
    if not isinstance(specifications, dict):
        return value
    validators = {} if external_validators is None else external_validators
    for name, raw_specification in specifications.items():
        specification = _object(raw_specification, f"external validator {name}")
        field = specification.get("field")
        if not isinstance(field, str) or field not in value:
            _review_error("ordinary external-validator field changed")
        source = value[field]
        if source is None:
            continue
        validator = validators.get(name)
        if validator is None:
            _review_error(f"ordinary review requires external validator {name}")
        try:
            normalized = validator(deepcopy(source))
        except Exception as exc:
            raise OrdinaryReviewContractError(
                f"ordinary external validator {name} rejected its value"
            ) from exc
        if not isinstance(normalized, dict):
            _review_error(f"ordinary external validator {name} returned a non-object")
        value[field] = deepcopy(normalized)
    return value


def _validate_review_invariants(schema_version: str, value: dict[str, object]) -> None:
    schema_id = SCHEMA_VERSION_TO_ID[schema_version]
    raw_invariants = SCHEMAS_BY_ID[schema_id].get("x-cera-review-invariants")
    if not isinstance(raw_invariants, list):
        return
    operations = {
        invariant.get("op")
        for invariant in raw_invariants
        if isinstance(invariant, dict)
    }
    if "forbidden_public_keys" in operations and _contains_forbidden_public_key(value):
        _review_error("ordinary public contract contains a forbidden key")
    if schema_version == "cera.pi_scene.review_checks.v2":
        _validate_failure_roles(value)
        return
    if schema_version == "cera.pi_scene.review.v3":
        _validate_review(value)
        return
    if schema_version == "cera.pi_scene.review_lifecycle.v2":
        _validate_initial_lifecycle(value)
        return
    if schema_version == "cera.pi_scene.review_decision.v3":
        _validate_decision(value)
        return
    _review_error(f"ordinary invariant runtime lacks {schema_version}")


def validate_schema_version(
    schema_version: str,
    value: object,
    *,
    external_validators: Mapping[str, ExternalValidator] | None = None,
) -> dict[str, object]:
    result = _validate_schema_version_base(schema_version, value)
    result = _apply_external_validators(schema_version, result, external_validators)
    _validate_review_invariants(schema_version, result)
    return result
"""


JAVASCRIPT_REVIEW_RUNTIME = r"""
const SHA256_K = Object.freeze([
  0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1,
  0x923f82a4, 0xab1c5ed5, 0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3,
  0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174, 0xe49b69c1, 0xefbe4786,
  0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
  0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147,
  0x06ca6351, 0x14292967, 0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13,
  0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85, 0xa2bfe8a1, 0xa81a664b,
  0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
  0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a,
  0x5b9cca4f, 0x682e6ff3, 0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208,
  0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
]);

function reviewError(message) {
  throw new OrdinaryReviewContractError(message);
}

function objectValue(value, label) {
  if (!plainObject(value)) reviewError(`${label} must be an object`);
  return value;
}

function arrayValue(value, label) {
  if (!Array.isArray(value)) reviewError(`${label} must be an array`);
  return value;
}

function containsForbiddenPublicKey(value) {
  if (Array.isArray(value)) return value.some(containsForbiddenPublicKey);
  if (!plainObject(value)) return false;
  return Object.entries(value).some(([key, item]) => {
    const name = String(key).toLowerCase();
    return name === 'raw'
      || name.startsWith('raw_')
      || name.startsWith('exact_')
      || name.endsWith('_path')
      || [
        'prompt',
        'feedback',
        'provider_output',
        'protected_full_record',
        'semantic_packet',
        'reader_packet',
      ].includes(name)
      || containsForbiddenPublicKey(item);
  });
}

function requiredLanes(checks) {
  return ['luna', 'reader', 'python'].map(name => objectValue(checks[name], `checks.${name}`));
}

function derivedGate(checks) {
  const lanes = requiredLanes(checks);
  if (lanes.some(lane => lane.provider_stage_retry_status !== null)) return 'blocked';
  const statuses = new Set(lanes.map(lane => lane.status));
  if (statuses.has('inconclusive')) return 'blocked';
  if (statuses.has('reject')) return 'reject';
  if (statuses.has('pending')) return 'pending';
  if (statuses.size === 1 && statuses.has('pass')) return 'pass';
  reviewError('ordinary review checks cannot derive one gate');
}

function validateFailureRoles(checks) {
  ['luna', 'reader', 'python'].forEach(name => {
    const lane = objectValue(checks[name], `checks.${name}`);
    const failures = arrayValue(lane.failures, `checks.${name}.failures`);
    const sources = failures.map((value, index) => {
      const failure = objectValue(value, `checks.${name}.failures[${index}]`);
      const source = failure.source_kind;
      const scope = failure.feedback_scope;
      if (name === 'luna') {
        if (!['runtime_failure', 'verdict_conflict', 'review_flag'].includes(source)) {
          reviewError('Luna failure changed source role');
        }
        if (scope !== null) reviewError('Luna failure exposed Reader feedback scope');
      } else if (name === 'reader') {
        if (lane.status === 'reject') {
          if (
            source !== 'reader_issue'
            || !['exact_quote', 'omitted_planner_item', 'whole_candidate_quality'].includes(scope)
          ) reviewError('Reader rejection changed issue provenance');
        } else if (source !== 'runtime_failure' || scope !== null) {
          reviewError('Reader runtime failure changed source role');
        }
      } else if (source !== 'runtime_failure' || scope !== null) {
        reviewError('Python failure changed source role');
      }
      return source;
    });
    if (name === 'luna') {
      if (lane.status === 'reject') {
        if (
          sources.filter(source => source === 'verdict_conflict').length !== 1
          || sources.some(source => !['verdict_conflict', 'review_flag'].includes(source))
        ) reviewError('Luna rejection lacks one primary conflict');
      } else if (sources.some(source => source !== 'runtime_failure')) {
        reviewError('non-rejecting Luna lane exposed verdict evidence');
      }
    }
  });
}

function allActionsFalse(actions) {
  return [
    'accept_enabled',
    'regenerate_enabled',
    'decline_enabled',
    'replan_enabled',
    'auditable_override_enabled',
    'repair_recording_enabled',
  ].every(name => actions[name] === false) && actions.auditable_override_action === null;
}

function validateAcceptance(acceptance, reviewMode, gateStatus) {
  if (acceptance.mode === 'standing_policy') {
    if (
      acceptance.canon_status !== 'provisional'
      || gateStatus !== 'reject'
      || acceptance.standing_policy === null
      || typeof acceptance.standing_policy !== 'object'
      || Array.isArray(acceptance.standing_policy)
      || acceptance.standing_policy.authority_kind !== 'standing_creator_policy'
      || acceptance.standing_policy.policy_id !== 'ordinary_provisional_continuity'
      || acceptance.standing_policy.policy_version !== 1
      || acceptance.standing_policy.policy_sha256
        !== '47729a4fc27046e8da768c8e0f1bc6670be48e576e606f193339de30b3bf3b23'
    ) {
      reviewError('standing policy changed provisional rejected custody');
    }
    const auditBasis = Object.fromEntries(
      Object.entries(acceptance.standing_policy)
        .filter(([key]) => key !== 'audit_sha256'),
    );
    if (acceptance.standing_policy.audit_sha256 !== sha256Utf8(canonical(auditBasis))) {
      reviewError('standing policy changed its audit binding');
    }
    return;
  }
  if (acceptance.standing_policy !== undefined && acceptance.standing_policy !== null) {
    reviewError('non-policy acceptance exposed standing-policy authority');
  }
  if (acceptance.mode === 'auditable_override') {
    if (acceptance.canon_status !== 'provisional' || gateStatus !== 'reject') {
      reviewError('auditable override changed provisional rejected custody');
    }
    return;
  }
  if (
    acceptance.canon_status !== 'accepted'
    || gateStatus !== 'pass'
    || acceptance.mode !== reviewMode
  ) {
    reviewError('ordinary accepted mode, canon, review mode, and gate differ');
  }
}

function validateStandingPolicyReview(review, acceptance) {
  if (acceptance.mode !== 'standing_policy') return;
  const authority = objectValue(acceptance.standing_policy, 'acceptance.standing_policy');
  const checks = objectValue(review.checks, 'review.checks');
  const luna = objectValue(checks.luna, 'checks.luna');
  const reader = objectValue(checks.reader, 'checks.reader');
  const pythonLane = objectValue(checks.python, 'checks.python');
  const expectedReasons = new Set();
  if (luna.status === 'reject') {
    const conflict = arrayValue(luna.failures, 'checks.luna.failures')
      .map(value => objectValue(value, 'checks.luna.failure'))
      .find(value => value.source_kind === 'verdict_conflict');
    if (![
      'omitted_decision',
      'presence_violation',
      'stopping_boundary',
      'capability_restriction',
    ].includes(conflict.code)) reviewError('standing policy accepted a hard Luna conflict');
    expectedReasons.add(`luna:${conflict.code}`);
  }
  if (reader.status === 'reject') {
    arrayValue(reader.failures, 'checks.reader.failures').forEach(value => {
      const failure = objectValue(value, 'checks.reader.failure');
      if (!['exact_quote', 'omitted_planner_item'].includes(failure.feedback_scope)) {
        reviewError('standing policy accepted a hard Reader issue');
      }
      expectedReasons.add(`reader:${failure.feedback_scope}`);
    });
  }
  const reasons = arrayValue(
    authority.tolerated_reason_codes,
    'acceptance.standing_policy.tolerated_reason_codes',
  );
  if (canonical(reasons) !== canonical([...expectedReasons].sort())) {
    reviewError('standing policy reasons differ from rejected lanes');
  }
  if (
    authority.candidate_sha256 !== review.candidate_sha256
    || authority.semantic_validation_sha256 !== luna.verdict_sha256
    || authority.reader_validation_sha256 !== reader.verdict_sha256
    || authority.python_qualification_sha256 !== pythonLane.verdict_sha256
  ) reviewError('standing policy audit differs from validator custody');
}

function validateActions(review) {
  const actions = objectValue(review.actions, 'review.actions');
  const semanticNames = [
    'accept_enabled',
    'regenerate_enabled',
    'decline_enabled',
    'replan_enabled',
    'auditable_override_enabled',
  ];
  if (review.state === 'review_ready' && review.gate_status === 'pass') {
    if (review.review_mode !== 'manual') reviewError('automatic pass cannot await creator Accept');
    const expected = {
      accept_enabled: true,
      regenerate_enabled: true,
      decline_enabled: true,
      replan_enabled: false,
      auditable_override_enabled: false,
    };
    if (semanticNames.some(name => actions[name] !== expected[name])) {
      reviewError('manual pass actions changed');
    }
    if (actions.auditable_override_action !== null) reviewError('manual pass exposed an override action');
    if (actions.repair_recording_enabled !== false) reviewError('unaccepted review exposed Recorder repair');
    return;
  }
  if (review.state === 'review_ready' && review.gate_status === 'reject') {
    const expected = {
      accept_enabled: false,
      regenerate_enabled: true,
      decline_enabled: true,
      replan_enabled: false,
      auditable_override_enabled: true,
    };
    if (semanticNames.some(name => actions[name] !== expected[name])) {
      reviewError('rejected review actions changed');
    }
    if (actions.auditable_override_action !== 'accept_provisional') {
      reviewError('rejected review lost its auditable override identity');
    }
    if (actions.repair_recording_enabled !== false) reviewError('unaccepted review exposed Recorder repair');
    return;
  }
  if (review.state === 'accepted') {
    if (semanticNames.some(name => actions[name] !== false)) {
      reviewError('accepted review exposed a semantic decision action');
    }
    if (actions.auditable_override_action !== null) reviewError('accepted review retained override authority');
    if (
      actions.repair_recording_enabled === true
      && !['projection_pending', 'pending_repair'].includes(review.recording_status)
    ) {
      reviewError('Recorder repair lacks pending recording custody');
    }
    if (
      [null, 'complete'].includes(review.recording_status)
      && actions.repair_recording_enabled !== false
    ) {
      reviewError('completed or absent recording exposed repair');
    }
    return;
  }
  if (!allActionsFalse(actions)) reviewError('non-actionable review exposed an action');
}

function expectedDisposition(checks, gate) {
  if (gate === 'pending') return 'checks_pending';
  if (gate === 'blocked') return 'checks_blocked';
  if (gate === 'pass') return 'checks_passed';
  const luna = objectValue(checks.luna, 'checks.luna').status === 'reject';
  const reader = objectValue(checks.reader, 'checks.reader').status === 'reject';
  if (luna && reader) return 'luna_reader_rejected';
  if (luna) return 'luna_rejected';
  if (reader) return 'reader_rejected';
  reviewError('rejected gate lacks a rejecting validator');
}

function validateProviderAccounting(review) {
  const attempts = arrayValue(review.provider_attempts, 'review.provider_attempts');
  const operations = objectValue(review.provider_operations, 'review.provider_operations');
  const roles = ['planner', 'writer', 'validator', 'reader'];
  const totals = Object.fromEntries(roles.map(role => [role, 0]));
  attempts.forEach((item, offset) => {
    const attempt = objectValue(item, `review.provider_attempts[${offset}]`);
    if (attempt.attempt_number !== offset + 1) {
      reviewError('ordinary provider attempt numbers are not contiguous');
    }
    const attemptOperations = objectValue(
      attempt.provider_operations,
      `review.provider_attempts[${offset}].provider_operations`,
    );
    roles.forEach(role => {
      totals[role] += attemptOperations[role];
      if (!Number.isSafeInteger(totals[role])) {
        reviewError('ordinary provider accounting exceeds the JS-safe ceiling');
      }
    });
  });
  roles.forEach(role => {
    if (operations[role] !== totals[role]) {
      reviewError('ordinary provider operation totals differ from attempts');
    }
  });
  const terminal = objectValue(attempts.at(-1), 'review.provider_attempts[-1]');
  if (terminal.candidate_id !== review.candidate_id) {
    reviewError('terminal provider attempt changed frozen candidate');
  }
  if (terminal.disposition !== expectedDisposition(objectValue(review.checks, 'review.checks'), review.gate_status)) {
    reviewError('terminal provider attempt disposition differs from checks');
  }
}

function validateTerminalLink(review) {
  const resolved = ['accepted', 'declined', 'regenerated', 'replanned'].includes(review.state);
  if (resolved !== (review.terminal_decision !== null)) {
    reviewError('ordinary review resolution and terminal link differ');
  }
  if (review.terminal_decision === null) return;
  const terminal = objectValue(review.terminal_decision, 'review.terminal_decision');
  const expectedUrl = `/v1/cera/reviews/${review.review_id}/terminal-decision`;
  if (terminal.url !== expectedUrl) reviewError('ordinary terminal decision URL changed review identity');
}

function validateReviewModeControls(review) {
  const controls = objectValue(review.request_controls, 'review.request_controls');
  if (controls.schema_version !== 'cera.pi_scene.request_controls.v3') {
    reviewError('ordinary review v3 requires request-controls v3');
  }
  if (review.review_mode !== controls.review_mode) {
    reviewError('ordinary review mode differs from its request controls');
  }
}

function validateReview(review) {
  if (containsForbiddenPublicKey(review)) reviewError('ordinary review contains a forbidden public key');
  const checks = objectValue(review.checks, 'review.checks');
  validateFailureRoles(checks);
  const gate = derivedGate(checks);
  if (review.gate_status !== gate) reviewError('ordinary review gate differs from independent checks');
  const acceptance = review.acceptance === null ? null : objectValue(review.acceptance, 'acceptance');
  if (review.state === 'checks_pending') {
    if (!['pending', 'blocked', 'reject'].includes(gate)) reviewError('checks-pending review has a terminal semantic gate');
  } else if (review.state === 'review_ready') {
    if (!['pass', 'reject'].includes(gate)) reviewError('review-ready candidate lacks a creator decision gate');
  } else if (review.state === 'accepted') {
    if (!['pass', 'reject'].includes(gate)) reviewError('accepted review lacks a qualified or override gate');
  } else if (['declined', 'regenerated'].includes(review.state)) {
    if (!['pass', 'reject'].includes(gate)) reviewError('resolved creator choice changed its completed validator gate');
  } else if (review.state === 'replanned') {
    if (gate !== 'reject') reviewError('resolved Replan changed its rejected validator gate');
  } else {
    reviewError('ordinary review state is not closed');
  }
  if ((review.state === 'accepted') !== (acceptance !== null)) {
    reviewError('ordinary accepted state and acceptance receipt differ');
  }
  if (acceptance !== null) {
    validateAcceptance(acceptance, review.review_mode, gate);
    validateStandingPolicyReview(review, acceptance);
  }
  if (review.state !== 'accepted' && review.recording_status !== null) {
    reviewError('unaccepted review contains a recording status');
  }
  validateActions(review);
  validateTerminalLink(review);
  validateProviderAccounting(review);
  validateReviewModeControls(review);
}

function validateInitialLifecycle(value) {
  if (containsForbiddenPublicKey(value)) reviewError('ordinary lifecycle contains a forbidden public key');
  if (value.review_url !== `/v1/cera/reviews/${value.review_id}`) {
    reviewError('ordinary lifecycle URL changed review identity');
  }
  const checks = objectValue(value.checks, 'review_lifecycle.checks');
  validateFailureRoles(checks);
  ['luna', 'reader', 'python'].forEach(name => {
    const lane = objectValue(checks[name], `review_lifecycle.checks.${name}`);
    if (
      lane.status !== 'pending'
      || lane.verdict_sha256 !== null
      || !jsonEqual(lane.failures, [])
      || lane.provider_stage_retry_status !== null
    ) {
      reviewError('initial review lifecycle contains completed or blocked work');
    }
  });
  if (!allActionsFalse(objectValue(value.actions, 'review_lifecycle.actions'))) {
    reviewError('initial review lifecycle exposes an action');
  }
}

function rotateRight(value, bits) {
  return (value >>> bits) | (value << (32 - bits));
}

function sha256Utf8(value) {
  const source = new TextEncoder().encode(value);
  const paddedLength = Math.ceil((source.length + 9) / 64) * 64;
  const bytes = new Uint8Array(paddedLength);
  bytes.set(source);
  bytes[source.length] = 0x80;
  const bitLength = source.length * 8;
  const view = new DataView(bytes.buffer);
  view.setUint32(paddedLength - 8, Math.floor(bitLength / 0x100000000));
  view.setUint32(paddedLength - 4, bitLength >>> 0);
  const hash = new Uint32Array([
    0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a,
    0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19,
  ]);
  const words = new Uint32Array(64);
  for (let offset = 0; offset < bytes.length; offset += 64) {
    for (let index = 0; index < 16; index += 1) words[index] = view.getUint32(offset + index * 4);
    for (let index = 16; index < 64; index += 1) {
      const prior15 = words[index - 15];
      const prior2 = words[index - 2];
      const sigma0 = rotateRight(prior15, 7) ^ rotateRight(prior15, 18) ^ (prior15 >>> 3);
      const sigma1 = rotateRight(prior2, 17) ^ rotateRight(prior2, 19) ^ (prior2 >>> 10);
      words[index] = (words[index - 16] + sigma0 + words[index - 7] + sigma1) >>> 0;
    }
    let [a, b, c, d, e, f, g, h] = hash;
    for (let index = 0; index < 64; index += 1) {
      const sum1 = rotateRight(e, 6) ^ rotateRight(e, 11) ^ rotateRight(e, 25);
      const choice = (e & f) ^ (~e & g);
      const temporary1 = (h + sum1 + choice + SHA256_K[index] + words[index]) >>> 0;
      const sum0 = rotateRight(a, 2) ^ rotateRight(a, 13) ^ rotateRight(a, 22);
      const majority = (a & b) ^ (a & c) ^ (b & c);
      const temporary2 = (sum0 + majority) >>> 0;
      h = g;
      g = f;
      f = e;
      e = (d + temporary1) >>> 0;
      d = c;
      c = b;
      b = a;
      a = (temporary1 + temporary2) >>> 0;
    }
    hash[0] = (hash[0] + a) >>> 0;
    hash[1] = (hash[1] + b) >>> 0;
    hash[2] = (hash[2] + c) >>> 0;
    hash[3] = (hash[3] + d) >>> 0;
    hash[4] = (hash[4] + e) >>> 0;
    hash[5] = (hash[5] + f) >>> 0;
    hash[6] = (hash[6] + g) >>> 0;
    hash[7] = (hash[7] + h) >>> 0;
  }
  return [...hash].map(word => word.toString(16).padStart(8, '0')).join('');
}

function validateDecision(value) {
  if (containsForbiddenPublicKey(value)) reviewError('ordinary review decision contains a forbidden public key');
  const review = objectValue(value.review, 'review_decision.review');
  validateReview(review);
  const expectedState = {
    automatic_accept: 'accepted',
    accept: 'accepted',
    accept_provisional: 'accepted',
    standing_policy_accept_provisional: 'accepted',
    repair_recording: 'accepted',
    decline: 'declined',
    regenerate: 'regenerated',
    replan: 'replanned',
  }[value.creator_action];
  if (review.state !== expectedState) reviewError('ordinary decision action differs from terminal review state');
  const acceptance = review.acceptance === null ? null : objectValue(review.acceptance, 'acceptance');
  if ([
    'automatic_accept',
    'accept',
    'accept_provisional',
    'standing_policy_accept_provisional',
  ].includes(value.creator_action)) {
    const expectedMode = {
      automatic_accept: 'automatic',
      accept: 'manual',
      accept_provisional: 'auditable_override',
      standing_policy_accept_provisional: 'standing_policy',
    }[value.creator_action];
    if (acceptance === null || acceptance.mode !== expectedMode) {
      reviewError('ordinary decision action differs from acceptance mode');
    }
  }
  if (value.creator_action === 'repair_recording' && review.recording_status !== 'complete') {
    reviewError('Recorder-repair decision is not complete');
  }
  if (value.story_state_committed === true) {
    if (
      acceptance === null
      || value.accepted_receipt_sha256 !== acceptance.accepted_receipt_sha256
      || value.accepted_turn_id !== acceptance.accepted_turn_id
    ) {
      reviewError('ordinary decision changed its accepted receipt identity');
    }
  }
  if (['regenerate', 'replan'].includes(value.creator_action)) {
    if (value.successor === null) reviewError('ordinary successor decision omitted its exact completion');
  } else if (value.successor !== null) {
    reviewError('ordinary non-successor decision contains a completion');
  }
  const terminal = objectValue(review.terminal_decision, 'review.terminal_decision');
  const detached = deepClone(value);
  detached.review.terminal_decision = null;
  if (terminal.decision_sha256 !== sha256Utf8(canonical(detached))) {
    reviewError('ordinary terminal link differs from detached decision bytes');
  }
}

function applyExternalValidators(schemaVersion, value, externalValidators) {
  const schemaId = SCHEMA_VERSION_TO_ID[schemaVersion];
  const specifications = CONTRACT_SCHEMAS[schemaId]['x-cera-external-validators'];
  if (!plainObject(specifications)) return value;
  for (const [name, rawSpecification] of Object.entries(specifications)) {
    const specification = objectValue(rawSpecification, `external validator ${name}`);
    if (typeof specification.field !== 'string' || !(specification.field in value)) {
      reviewError('ordinary external-validator field changed');
    }
    const source = value[specification.field];
    if (source === null) continue;
    const validator = externalValidators[name];
    if (typeof validator !== 'function') reviewError(`ordinary review requires external validator ${name}`);
    let normalized;
    try {
      normalized = validator(deepClone(source));
    } catch (error) {
      throw new OrdinaryReviewContractError(
        `ordinary external validator ${name} rejected its value`,
        { cause: error },
      );
    }
    if (!plainObject(normalized)) reviewError(`ordinary external validator ${name} returned a non-object`);
    value[specification.field] = deepClone(normalized);
  }
  return value;
}

function validateReviewInvariants(schemaVersion, value) {
  const schemaId = SCHEMA_VERSION_TO_ID[schemaVersion];
  const rawInvariants = CONTRACT_SCHEMAS[schemaId]['x-cera-review-invariants'];
  if (!Array.isArray(rawInvariants)) return;
  const operations = new Set(rawInvariants.filter(plainObject).map(item => item.op));
  if (operations.has('forbidden_public_keys') && containsForbiddenPublicKey(value)) {
    reviewError('ordinary public contract contains a forbidden key');
  }
  if (schemaVersion === 'cera.pi_scene.review_checks.v2') return validateFailureRoles(value);
  if (schemaVersion === 'cera.pi_scene.review.v3') return validateReview(value);
  if (schemaVersion === 'cera.pi_scene.review_lifecycle.v2') return validateInitialLifecycle(value);
  if (schemaVersion === 'cera.pi_scene.review_decision.v3') return validateDecision(value);
  reviewError(`ordinary invariant runtime lacks ${schemaVersion}`);
}

export function validateSchemaVersion(schemaVersion, value, { externalValidators = {} } = {}) {
  let result = validateSchemaVersionBase(schemaVersion, value);
  result = applyExternalValidators(schemaVersion, result, externalValidators);
  validateReviewInvariants(schemaVersion, result);
  return result;
}
"""


def _python_runtime() -> str:
    runtime = cast(str, schema_engine.PYTHON_RUNTIME)
    return (
        runtime.replace(
            "ProviderStageRetryContractError",
            "OrdinaryReviewContractError",
        )
        .replace("provider-stage", "ordinary review")
        .replace(
            "def validate_schema_version(schema_version: str, value: object)",
            "def _validate_schema_version_base(schema_version: str, value: object)",
        )
    )


def _javascript_runtime() -> str:
    runtime = cast(str, schema_engine.JAVASCRIPT_RUNTIME)
    return (
        runtime.replace(
            "ProviderStageRetryContractError",
            "OrdinaryReviewContractError",
        )
        .replace("provider-stage", "ordinary review")
        .replace(
            "export function validateSchemaVersion(schemaVersion, value)",
            "function validateSchemaVersionBase(schemaVersion, value)",
        )
    )


def _ordinary_typed_dicts(
    loaded: list[tuple[Path, dict[str, Any], bytes]],
    runtime_schemas: dict[str, dict[str, Any]],
) -> str:
    """Render useful root projections without weakening runtime validation.

    The shared provider engine predates nested local references reached through
    an external support-schema reference.  Keep using its type projector where
    it can resolve a field, and conservatively emit ``object`` for that static-
    typing edge.  The generated runtime still validates the complete schema.
    """

    blocks: list[str] = []
    for _, schema, _ in loaded:
        class_name = schema.get("x-cera-python-name")
        properties = schema.get("properties")
        if not isinstance(class_name, str) or not isinstance(properties, dict):
            continue
        if set(schema.get("required", [])) != set(properties):
            raise ValueError(f"{schema['$id']} generated root must have exact required keys")
        lines = [f"class {class_name}(TypedDict):"]
        for field_name, field_schema in properties.items():
            if not isinstance(field_schema, dict):
                raise ValueError(f"{schema['$id']} field {field_name} is not a schema")
            try:
                type_name = schema_engine._python_type(  # noqa: SLF001
                    field_schema,
                    schema["$id"],
                    runtime_schemas,
                )
            except KeyError:
                type_name = "object"
            lines.append(f"    {field_name}: {type_name}")
        blocks.append("\n".join(lines))
    return "\n\n\n".join(blocks)


def _render_python(
    ordinary: list[tuple[Path, dict[str, Any], bytes]],
    runtime_schemas: dict[str, dict[str, Any]],
    version_to_id: dict[str, str],
) -> str:
    schemas_literal = json.dumps(
        runtime_schemas,
        indent=2,
        ensure_ascii=False,
        sort_keys=True,
    )
    versions_literal = json.dumps(
        version_to_id,
        indent=2,
        ensure_ascii=False,
        sort_keys=True,
    )
    typed_sources = [
        item
        for item in ordinary
        if isinstance(item[1].get("properties"), dict)
        and isinstance(item[1].get("x-cera-python-name"), str)
    ]
    typed_dicts = _ordinary_typed_dicts(
        typed_sources,
        runtime_schemas,
    )
    typed_dicts = (
        f"{typed_dicts}\n\n\n" if typed_dicts else ""
    ) + "OrdinaryReviewDecisionV3: TypeAlias = dict[str, object]"
    functions: list[str] = []
    exported = [
        "OrdinaryReviewContractError",
        "SCHEMAS_BY_ID",
        "SCHEMA_VERSION_TO_ID",
        "validate_schema_version",
    ]
    for _, schema, _ in ordinary:
        class_name = schema.get("x-cera-python-name")
        version = schema.get("x-cera-schema-version")
        if not isinstance(class_name, str) or not isinstance(version, str):
            continue
        snake_name = schema_engine._camel_to_snake(class_name)  # noqa: SLF001
        if version == DECISION_VERSION:
            functions.append(
                f'''def validate_{snake_name}(
    value: object,
    *,
    successor_validator: ExternalValidator | None = None,
) -> {class_name}:
    """Validate and copy ``{version}``, including its exact successor."""

    validators = (
        {{}}
        if successor_validator is None
        else {{"ordinary_successor": successor_validator}}
    )
    return validate_schema_version(
        {version!r},
        value,
        external_validators=validators,
    )


def normalize_{snake_name}(
    value: object,
    *,
    successor_validator: ExternalValidator | None = None,
) -> {class_name} | None:
    """Return a validated decision copy, or ``None`` for an invalid value."""

    try:
        return validate_{snake_name}(
            value,
            successor_validator=successor_validator,
        )
    except OrdinaryReviewContractError:
        return None'''
            )
        else:
            functions.append(
                f'''def validate_{snake_name}(value: object) -> {class_name}:
    """Validate and copy ``{version}``."""

    return cast({class_name}, validate_schema_version({version!r}, value))


def normalize_{snake_name}(value: object) -> {class_name} | None:
    """Return a validated copy, or ``None`` for an invalid boundary value."""

    try:
        return validate_{snake_name}(value)
    except OrdinaryReviewContractError:
        return None'''
            )
        exported.extend(
            [
                class_name,
                f"normalize_{snake_name}",
                f"validate_{snake_name}",
            ]
        )
    return f"""# Generated by scripts/generate_ordinary_review_contracts_v3.py. DO NOT EDIT.
# ruff: noqa
# fmt: off
from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from typing import Callable, Final, Literal, Mapping, NoReturn, TypeAlias, TypedDict, cast

from cera.errors import ContractValidationError

SCHEMAS_BY_ID: Final[dict[str, dict[str, object]]] = cast(
    dict[str, dict[str, object]], json.loads({schemas_literal!r})
)

SCHEMA_VERSION_TO_ID: Final[dict[str, str]] = cast(
    dict[str, str], json.loads({versions_literal!r})
)


{typed_dicts}


{_python_runtime().strip()}


{PYTHON_REVIEW_RUNTIME.strip()}


{("\n\n\n".join(functions))}


__all__ = {repr(sorted(exported))}
# fmt: on
"""


def _render_javascript(
    ordinary: list[tuple[Path, dict[str, Any], bytes]],
    runtime_schemas: dict[str, dict[str, Any]],
    version_to_id: dict[str, str],
) -> str:
    schemas_literal = json.dumps(runtime_schemas, indent=2, ensure_ascii=False)
    versions_literal = json.dumps(version_to_id, indent=2, ensure_ascii=False)
    functions: list[str] = []
    for _, schema, _ in ordinary:
        base_name = schema.get("x-cera-javascript-name")
        version = schema.get("x-cera-schema-version")
        if not isinstance(base_name, str) or not isinstance(version, str):
            continue
        if version == DECISION_VERSION:
            functions.append(
                f"""export function validate{base_name}(
  value,
  {{ successorValidator = null }} = {{}},
) {{
  const externalValidators = successorValidator === null
    ? {{}}
    : {{ ordinary_successor: successorValidator }};
  return validateSchemaVersion(
    {json.dumps(version)},
    value,
    {{ externalValidators }},
  );
}}

export function normalize{base_name}(
  value,
  {{ successorValidator = null }} = {{}},
) {{
  try {{
    return validate{base_name}(value, {{ successorValidator }});
  }} catch (error) {{
    if (error instanceof OrdinaryReviewContractError) return null;
    throw error;
  }}
}}"""
            )
        else:
            functions.append(
                f"""export function validate{base_name}(value) {{
  return validateSchemaVersion({json.dumps(version)}, value);
}}

export function normalize{base_name}(value) {{
  try {{
    return validate{base_name}(value);
  }} catch (error) {{
    if (error instanceof OrdinaryReviewContractError) return null;
    throw error;
  }}
}}"""
            )
    return f"""// Generated by scripts/generate_ordinary_review_contracts_v3.py. DO NOT EDIT.
export const CONTRACT_SCHEMAS = deepFreeze({schemas_literal});

export const SCHEMA_VERSION_TO_ID = deepFreeze({versions_literal});

{_javascript_runtime().strip()}

{JAVASCRIPT_REVIEW_RUNTIME.strip()}

{("\n\n".join(functions))}
"""


def _constraint_summary(schema: dict[str, Any]) -> str:
    return cast(str, schema_engine._constraint_summary(schema))  # noqa: SLF001


def _render_docs(
    ordinary: list[tuple[Path, dict[str, Any], bytes]],
) -> str:
    lines = [
        "# Ordinary provisional-review generated contracts V3",
        "",
        "> Generated by `scripts/generate_ordinary_review_contracts_v3.py`. Do not edit.",
        "",
        "Readable JSON Schema is the only hand-edited contract source. The generated",
        "Python and JavaScript normalizers enforce the same closed shape, privacy rules,",
        "cross-field lifecycle rules, provider-stage Retry envelopes, and terminal link.",
        "",
        "`automatic_accept` is an output-only terminal decision identity. Clients never",
        "submit it as a creator action. A non-null successor requires the application's",
        "existing exact completion validator; no open successor object is accepted.",
        "",
        "## Canonical sources",
        "",
        "| Source | Published DTO | Root keys | SHA-256 |",
        "|---|---|---:|---|",
    ]
    for path, schema, raw in ordinary:
        version = schema.get("x-cera-schema-version", "support schema")
        properties = schema.get("properties")
        count = len(properties) if isinstance(properties, dict) else "branch-specific"
        lines.append(
            f"| `{path.relative_to(ROOT).as_posix()}` | `{version}` | {count} | `{_sha256(raw)}` |"
        )
    for _, schema, _ in ordinary:
        version = schema.get("x-cera-schema-version")
        if not isinstance(version, str):
            continue
        lines.extend(["", f"## `{version}`", "", str(schema.get("description", ""))])
        properties = schema.get("properties")
        if not isinstance(properties, dict):
            lines.extend(
                [
                    "",
                    "This closed union has branch-specific exact keys; see the canonical schema.",
                ]
            )
            continue
        lines.extend(
            [
                "",
                "| Field | Required | Constraints |",
                "|---|---:|---|",
            ]
        )
        required = set(schema.get("required", []))
        for field_name, field_schema in properties.items():
            assert isinstance(field_schema, dict)
            lines.append(
                f"| `{field_name}` | {'yes' if field_name in required else 'no'} | "
                f"{_constraint_summary(field_schema)} |"
            )
    lines.extend(
        [
            "",
            "## Generated APIs",
            "",
            "- Python: `validate_ordinary_review_v3`, `validate_ordinary_review_checks_v2`,",
            "  `validate_ordinary_review_lifecycle_v2`, and",
            "  `validate_ordinary_review_decision_v3(..., successor_validator=...)`; each",
            "  has a matching `normalize_...` helper returning `None` on rejection.",
            "- JavaScript: `validateOrdinaryReviewV3`, `validateOrdinaryReviewChecksV2`,",
            "  `validateOrdinaryReviewLifecycleV2`, and",
            "  `validateOrdinaryReviewDecisionV3(value, { successorValidator })`; each",
            "  has a matching `normalize...` helper returning `null` on rejection.",
            "- Generic Python and JavaScript `validate_schema_version` /",
            "  `validateSchemaVersion` APIs accept named exact external validators.",
            "",
            "The detached terminal hash is computed after exact successor normalization by",
            "replacing only `review.terminal_decision` with `null` in the normalized decision.",
            "",
            "## Generated integration surfaces",
            "",
            "- Python: `src/cera/generated/ordinary_review_contracts_v3.py`",
            "- JavaScript: `integrations/sillytavern/generated/ordinary-review-contracts-v3.mjs`",
            "- Staged JavaScript copies under both SillyTavern extension/plugin `generated/` roots",
            "- Cross-runtime fixtures: `tests/fixtures/generated/ordinary_review_v3_{positive,negative}.json`",
            "",
        ]
    )
    return "\n".join(lines)


def _successor_fixture_validator(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != {
        "schema_version",
        "completion_sha256",
    }:
        raise ValueError("successor fixture must be an exact object")
    if value.get("schema_version") != SUCCESSOR_FIXTURE_VERSION:
        raise ValueError("successor fixture version changed")
    digest = value.get("completion_sha256")
    if not isinstance(digest, str) or re.fullmatch(r"[a-f0-9]{64}", digest) is None:
        raise ValueError("successor fixture digest changed")
    return copy.deepcopy(value)


def _verify_generated_python(
    rendered: str,
    positive_fixture: dict[str, Any],
    negative_fixture: dict[str, Any],
) -> None:
    source_root = str(ROOT / "src")
    if source_root not in sys.path:
        sys.path.insert(0, source_root)
    namespace: dict[str, Any] = {"__name__": "_generated_ordinary_review_check"}
    exec(compile(rendered, str(PYTHON_TARGET), "exec"), namespace)
    validate = namespace["validate_schema_version"]
    contract_error = namespace["OrdinaryReviewContractError"]
    for case in positive_fixture["cases"]:
        external = (
            {"ordinary_successor": _successor_fixture_validator}
            if case["contract_schema_version"] == DECISION_VERSION
            else None
        )
        validate(
            case["contract_schema_version"],
            case["value"],
            external_validators=external,
        )
    for case in negative_fixture["cases"]:
        external = (
            {"ordinary_successor": _successor_fixture_validator}
            if case["contract_schema_version"] == DECISION_VERSION
            else None
        )
        try:
            validate(
                case["contract_schema_version"],
                case["value"],
                external_validators=external,
            )
        except contract_error:
            continue
        raise ValueError(f"negative fixture unexpectedly validates: {case['case_id']}")


def _build_outputs() -> dict[Path, bytes]:
    ordinary, provider = _load_schemas()
    runtime_schemas, version_to_id = _runtime_schemas(ordinary, provider)
    positive_fixture, negative_fixture = _fixtures(ordinary, provider)
    rendered_python = _render_python(ordinary, runtime_schemas, version_to_id)
    _verify_generated_python(rendered_python, positive_fixture, negative_fixture)
    rendered_javascript = _render_javascript(
        ordinary,
        runtime_schemas,
        version_to_id,
    ).encode()
    positive_fixture_bytes = _json_bytes(positive_fixture)
    outputs = {
        PYTHON_TARGET: rendered_python.encode(),
        JAVASCRIPT_TARGET: rendered_javascript,
        POSITIVE_FIXTURE_TARGET: positive_fixture_bytes,
        STAGED_POSITIVE_FIXTURE_TARGET: positive_fixture_bytes,
        NEGATIVE_FIXTURE_TARGET: _json_bytes(negative_fixture),
        DOC_TARGET: _render_docs(ordinary).encode(),
    }
    outputs.update({target: rendered_javascript for target in JAVASCRIPT_STAGED_TARGETS})
    return outputs


def _check(outputs: dict[Path, bytes]) -> int:
    drifted = [
        path.relative_to(ROOT).as_posix()
        for path, expected in outputs.items()
        if not path.is_file() or path.read_bytes() != expected
    ]
    if drifted:
        print("ordinary-review generated artifacts are stale:")
        for relative_path in drifted:
            print(f"  {relative_path}")
        print("run: python scripts/generate_ordinary_review_contracts_v3.py")
        return 1
    print(f"ordinary-review generated artifacts are current ({len(outputs)} files)")
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
