"""Closed semantic write policy for continuous Character/Relationship records."""

from __future__ import annotations

from typing import Any

from cera.errors import ContractValidationError, StateConflictError
from cera.serialization import canonical_sha256


CONTINUOUS_PERSISTENCE_POLICY_VERSION = "cera.continuous_persistence_policy.v2"

_WRITABLE_ROOTS: dict[str, frozenset[str]] = {
    "character": frozenset(
        {
            "reasoning_summary",
            "latest_accepted_changes",
            "turn_claims",
            "accepted_facts",
            "development",
            "state",
        }
    ),
    "relationship": frozenset(
        {
            "observations",
            "accepted_facts",
            "development",
            "relationship_state",
            "state",
        }
    ),
}

_ROOT_TYPES: dict[str, dict[str, tuple[type, ...]]] = {
    "character": {
        "reasoning_summary": (str,),
        "latest_accepted_changes": (list,),
        "turn_claims": (dict,),
        "accepted_facts": (dict,),
        "development": (dict,),
        "state": (dict,),
    },
    "relationship": {
        "observations": (dict,),
        "accepted_facts": (dict,),
        "development": (dict,),
        "relationship_state": (dict,),
        "state": (dict,),
    },
}

PERSISTENCE_POLICY_SHA256 = canonical_sha256(
    {
        "version": CONTINUOUS_PERSISTENCE_POLICY_VERSION,
        "writable_roots": {
            key: tuple(sorted(value)) for key, value in sorted(_WRITABLE_ROOTS.items())
        },
        "invariants": (
            "all_non_writable_top_level_fields_are_immutable",
            "identity_schema_revision_owner_participant_and_provenance_are_immutable",
            "json_pointer_segments_are_nonempty_nonrelative_and_cannot_decode_slashes",
            "post_edit_record_is_revalidated_before_candidate_publication",
        ),
    }
)


def _record_class_value(record_class: Any) -> str:
    value = getattr(record_class, "value", record_class)
    if value not in _WRITABLE_ROOTS:
        raise ContractValidationError("record class has no enabled persistence policy")
    return str(value)


def decode_safe_json_pointer(pointer: str) -> tuple[str, ...]:
    if not isinstance(pointer, str) or not pointer.startswith("/") or pointer == "/":
        raise ContractValidationError(
            "persistence directive field path is not an editable JSON pointer"
        )
    parts: list[str] = []
    for encoded in pointer[1:].split("/"):
        if not encoded or encoded in {".", ".."}:
            raise ContractValidationError("persistence field path contains an unsafe segment")
        index = 0
        while index < len(encoded):
            if encoded[index] == "~":
                if index + 1 >= len(encoded) or encoded[index + 1] not in {"0", "1"}:
                    raise ContractValidationError(
                        "persistence field path contains an invalid JSON pointer escape"
                    )
                index += 2
            else:
                index += 1
        decoded = encoded.replace("~1", "/").replace("~0", "~")
        if decoded in {"", ".", ".."} or "/" in decoded or "\\" in decoded:
            raise ContractValidationError(
                "persistence field path attempts a nested-path escape"
            )
        parts.append(decoded)
    return tuple(parts)


def validate_persistence_field_path(record_class: Any, pointer: str) -> tuple[str, ...]:
    record_class_value = _record_class_value(record_class)
    parts = decode_safe_json_pointer(pointer)
    if parts[0] not in _WRITABLE_ROOTS[record_class_value]:
        raise ContractValidationError(
            f"{record_class_value} persistence field is immutable or unsupported"
        )
    return parts


def validate_post_edit_record(
    *,
    record_class: Any,
    before: Any,
    after: Any,
    expected_record_id: str,
    expected_subject_ids: tuple[str, ...],
    expected_revision: int,
) -> None:
    record_class_value = _record_class_value(record_class)
    if not isinstance(before, dict) or not isinstance(after, dict):
        raise StateConflictError("persistence target is not a mutable semantic record")
    identity_field = {
        "character": "character_id",
        "relationship": "relationship_id",
    }[record_class_value]
    if (
        after.get(identity_field) != expected_record_id
        or before.get(identity_field) != expected_record_id
    ):
        raise StateConflictError("post-edit record identity changed")
    if not isinstance(after.get("schema_version"), str) or not after["schema_version"]:
        raise StateConflictError("post-edit record schema identity is invalid")
    if after.get("_cera_revision") != expected_revision:
        raise StateConflictError("post-edit record revision is invalid")
    if record_class_value == "character":
        if tuple(expected_subject_ids) != (expected_record_id,):
            raise StateConflictError("post-edit character subjects changed")
    else:
        participants = after.get("participant_ids")
        if (
            not isinstance(participants, list)
            or len(participants) != 2
            or len(set(participants)) != 2
            or tuple(participants) != tuple(expected_subject_ids)
        ):
            raise StateConflictError("post-edit relationship participants changed")

    writable = _WRITABLE_ROOTS[record_class_value]
    for key in set(before).union(after):
        if key == "_cera_revision":
            continue
        if key not in writable and before.get(key) != after.get(key):
            raise StateConflictError(
                f"post-edit {record_class_value} immutable metadata changed"
            )
    for root, expected_types in _ROOT_TYPES[record_class_value].items():
        if root in after and type(after[root]) not in expected_types:
            raise StateConflictError(
                f"post-edit {record_class_value} semantic field type is invalid"
            )
    if record_class_value == "character" and "latest_accepted_changes" in after:
        if any(not isinstance(value, str) for value in after["latest_accepted_changes"]):
            raise StateConflictError(
                "post-edit character accepted changes must remain text"
            )
