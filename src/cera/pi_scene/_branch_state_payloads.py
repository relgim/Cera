"""Decode sanitized accepted-store payloads into prose-free branch events."""

from __future__ import annotations

import json
from typing import Any, Mapping

from cera.errors import ContractValidationError, StateConflictError
from cera.schema import from_mapping
from cera.serialization import text_sha256

from .contracts import AdultCodexProjectionV1, AdultCodexProjectionV2

from ._branch_state_models import (
    RECORDING_STATUSES,
    ROUTES,
    AcceptedBranchEventV1,
    AdultPublicContinuityV1,
    DurableBranchChangeV1,
    PresenceChangeV1,
    require_sha,
)


_ALLOWED_PAYLOAD_FIELDS = frozenset(
    {
        "receipt",
        "accepted_receipt_sha256",
        "recording_status",
        "ordinary_record",
        "adult_projection",
    }
)


def accepted_event_from_payload(payload: Mapping[str, Any]) -> AcceptedBranchEventV1:
    """Project one sanitized store payload without reading accepted prose."""

    if not isinstance(payload, Mapping):
        raise ContractValidationError("accepted branch payload must be an object")
    unknown = set(payload) - _ALLOWED_PAYLOAD_FIELDS
    if unknown:
        raise ContractValidationError("accepted branch payload contains unsupported fields")
    receipt = payload.get("receipt")
    if not isinstance(receipt, Mapping):
        raise ContractValidationError("accepted branch payload omitted its receipt")
    recording_status = payload.get("recording_status")
    if recording_status not in RECORDING_STATUSES:
        raise ContractValidationError("accepted recording status is invalid")
    receipt_sha256 = payload.get("accepted_receipt_sha256")
    require_sha(receipt_sha256, "accepted outer receipt hash")
    if not isinstance(receipt_sha256, str):  # narrowed by require_sha at runtime
        raise AssertionError("accepted outer receipt hash stopped being text")

    common = _common_fields(receipt, receipt_sha256=receipt_sha256)
    if common["route"] == "ordinary":
        return _ordinary_event(
            payload,
            receipt=receipt,
            recording_status=str(recording_status),
            common=common,
        )
    return _adult_event(
        payload,
        recording_status=str(recording_status),
        common=common,
    )


def _common_fields(
    receipt: Mapping[str, Any],
    *,
    receipt_sha256: str,
) -> dict[str, Any]:
    route = _mapping_text(receipt, "route", "accepted receipt")
    if route not in ROUTES:
        raise ContractValidationError("accepted receipt route is invalid")
    accepted_order = receipt.get("generation")
    if type(accepted_order) is not int:
        raise ContractValidationError("accepted receipt generation is invalid")
    parent_turn = receipt.get("parent_accepted_turn_id")
    parent_hash = receipt.get("parent_accepted_head_sha256")
    if parent_turn is not None and not isinstance(parent_turn, str):
        raise ContractValidationError("accepted receipt parent turn is invalid")
    if parent_hash is not None and not isinstance(parent_hash, str):
        raise ContractValidationError("accepted receipt parent hash is invalid")
    return {
        "world_id": _mapping_text(receipt, "world_id", "accepted receipt"),
        "branch_id": _mapping_text(receipt, "branch_id", "accepted receipt"),
        "scene_id": _mapping_text(receipt, "scene_id", "accepted receipt"),
        "accepted_order": accepted_order,
        "accepted_turn_id": _mapping_text(
            receipt,
            "accepted_turn_id",
            "accepted receipt",
        ),
        "parent_accepted_turn_id": parent_turn,
        "parent_accepted_head_sha256": parent_hash,
        "receipt_sha256": receipt_sha256,
        "route": route,
        "candidate_sha256": _mapping_text(
            receipt,
            "candidate_sha256",
            "accepted receipt",
        ),
    }


def _ordinary_event(
    payload: Mapping[str, Any],
    *,
    receipt: Mapping[str, Any],
    recording_status: str,
    common: Mapping[str, Any],
) -> AcceptedBranchEventV1:
    if "adult_projection" in payload:
        raise ContractValidationError("ordinary event contains adult projection")
    ordinary = payload.get("ordinary_record")
    if ordinary is None:
        if recording_status == "complete":
            raise StateConflictError("complete ordinary recording omitted its projection")
        return _pending_event(common)
    if not isinstance(ordinary, Mapping):
        raise ContractValidationError("ordinary Recorder projection is invalid")
    if recording_status != "complete":
        raise StateConflictError("pending ordinary recording exposed derived projection")

    authority = _primary_authority(receipt)
    accepted_turn_id = str(common["accepted_turn_id"])
    accepted_order = int(common["accepted_order"])
    primary_durable = _primary_durable_changes(
        authority,
        accepted_turn_id=accepted_turn_id,
    )
    recorded_durable = tuple(
        DurableBranchChangeV1(
            change_key=f"recorded-{accepted_order:08d}-{index:04d}",
            kind="recorded_durable",
            subject_ids=(),
            concise_change=value,
            target_key=f"accepted:{accepted_turn_id}:durable:{index:04d}",
            visibility="branch_internal_unspecified",
            knowledge_owner_id=None,
            accepted_turn_id=accepted_turn_id,
            source_kind="ordinary_recorder_projection",
        )
        for index, value in enumerate(
            _mapping_text_array(ordinary, "durable_changes", "ordinary record"),
            start=1,
        )
    )
    return AcceptedBranchEventV1(
        **common,
        presence_changes=_presence_changes(authority),
        durable_changes=primary_durable + recorded_durable,
        secondary_canon=_mapping_text_array(
            ordinary,
            "secondary_canon",
            "ordinary record",
        ),
        relationship_changes=_mapping_text_array(
            ordinary,
            "relationship_changes",
            "ordinary record",
        ),
        knowledge_changes=_mapping_text_array(
            ordinary,
            "knowledge_changes",
            "ordinary record",
        ),
        resulting_public_state=_mapping_text(
            ordinary,
            "resulting_public_state",
            "ordinary record",
        ),
        unresolved_threads=_mapping_text_array(
            ordinary,
            "unresolved_threads",
            "ordinary record",
        ),
        adult_public_continuity=(),
        derived_state_complete=True,
    )


def _adult_event(
    payload: Mapping[str, Any],
    *,
    recording_status: str,
    common: Mapping[str, Any],
) -> AcceptedBranchEventV1:
    if "ordinary_record" in payload:
        raise ContractValidationError("adult event contains ordinary record")
    projection = payload.get("adult_projection")
    if projection is None:
        if recording_status == "complete":
            raise StateConflictError("complete adult recording omitted its projection")
        return _pending_event(common)
    if not isinstance(projection, Mapping):
        raise ContractValidationError("adult filtered projection is invalid")
    if recording_status != "complete":
        raise StateConflictError("pending adult recording exposed derived projection")

    version = projection.get("schema_version")
    decoded: AdultCodexProjectionV1 | AdultCodexProjectionV2 | None = None
    if version is None:
        # Historical reducer fixtures predate the durable projection envelope.
        # They remain readable, but only the closed V1/V2 DTOs can carry new
        # state effects.
        legacy_items = projection.get("items")
        if not isinstance(legacy_items, list) or not legacy_items:
            raise ContractValidationError("adult projection items are invalid")
        item_values = tuple(
            (
                _mapping_text(value, "event_key", "adult projection item"),
                _mapping_text(
                    value,
                    "non_explicit_summary",
                    "adult projection item",
                ),
                _mapping_text(
                    value,
                    "lasting_story_meaning",
                    "adult projection item",
                ),
            )
            for value in legacy_items
            if isinstance(value, Mapping)
        )
        if len(item_values) != len(legacy_items):
            raise ContractValidationError("adult projection item is invalid")
        resulting_public_state = _mapping_text(
            projection,
            "resulting_public_state",
            "adult projection",
        )
        unresolved_threads = _mapping_text_array(
            projection,
            "unresolved_threads",
            "adult projection",
        )
    else:
        try:
            if version == AdultCodexProjectionV1.SCHEMA_VERSION:
                decoded = from_mapping(AdultCodexProjectionV1, projection)
            elif version == AdultCodexProjectionV2.SCHEMA_VERSION:
                decoded = from_mapping(AdultCodexProjectionV2, projection)
            else:
                raise ContractValidationError("adult projection version is unsupported")
        except (KeyError, TypeError, ValueError) as exc:
            raise ContractValidationError("adult filtered projection is invalid") from exc
        item_values = tuple(
            (
                value.event_key,
                value.non_explicit_summary,
                value.lasting_story_meaning,
            )
            for value in decoded.items
        )
        resulting_public_state = decoded.resulting_public_state
        unresolved_threads = decoded.unresolved_threads
    accepted_turn_id = str(common["accepted_turn_id"])
    accepted_order = int(common["accepted_order"])
    public_items: list[AdultPublicContinuityV1] = []
    durable: list[DurableBranchChangeV1] = []
    for index, (event_key, summary, meaning) in enumerate(item_values, start=1):
        public_items.append(
            AdultPublicContinuityV1(
                accepted_turn_id=accepted_turn_id,
                event_key=event_key,
                non_explicit_summary=summary,
                lasting_story_meaning=meaning,
            )
        )
        durable.append(
            DurableBranchChangeV1(
                change_key=f"adult-{accepted_order:08d}-{index:04d}-{event_key}",
                kind="adult_public_continuity",
                subject_ids=(),
                concise_change=meaning,
                target_key=f"accepted:{accepted_turn_id}:adult:{event_key}",
                visibility="public",
                knowledge_owner_id=None,
                accepted_turn_id=accepted_turn_id,
                source_kind="adult_codex_projection",
            )
        )
    presence = ()
    if isinstance(decoded, AdultCodexProjectionV2):
        event_order = {
            value.event_key: index for index, value in enumerate(decoded.items)
        }
        presence = tuple(
            PresenceChangeV1(
                character_id=value.character_id,
                direction=value.direction,
                effective_after_item_key=value.effective_after_event_key,
            )
            for value in sorted(
                decoded.presence_changes,
                key=lambda change: event_order[change.effective_after_event_key],
            )
        )
        durable.extend(
            DurableBranchChangeV1(
                change_key=value.change_key,
                kind=value.kind,
                subject_ids=value.subject_ids,
                concise_change=value.non_explicit_change,
                target_key=value.target_key,
                visibility=value.visibility,
                knowledge_owner_id=value.knowledge_owner_id,
                accepted_turn_id=accepted_turn_id,
                source_kind="adult_codex_projection_v2",
            )
            for value in decoded.durable_effects
        )
    return AcceptedBranchEventV1(
        **common,
        presence_changes=presence,
        durable_changes=tuple(durable),
        secondary_canon=(),
        relationship_changes=(),
        knowledge_changes=(),
        resulting_public_state=resulting_public_state,
        unresolved_threads=unresolved_threads,
        adult_public_continuity=tuple(public_items),
        derived_state_complete=True,
    )


def _pending_event(common: Mapping[str, Any]) -> AcceptedBranchEventV1:
    return AcceptedBranchEventV1(
        **common,
        presence_changes=(),
        durable_changes=(),
        secondary_canon=(),
        relationship_changes=(),
        knowledge_changes=(),
        resulting_public_state=None,
        unresolved_threads=(),
        adult_public_continuity=(),
        derived_state_complete=False,
    )


def _primary_authority(receipt: Mapping[str, Any]) -> Mapping[str, Any]:
    authority_json = _mapping_text(receipt, "primary_authority_json", "accepted receipt")
    authority_sha = _mapping_text(
        receipt,
        "primary_authority_sha256",
        "accepted receipt",
    )
    require_sha(authority_sha, "accepted primary authority hash")
    if text_sha256(authority_json) != authority_sha:
        raise StateConflictError("accepted primary authority hash changed")
    try:
        authority = json.loads(authority_json)
    except json.JSONDecodeError as exc:
        raise ContractValidationError("accepted primary authority is invalid JSON") from exc
    if not isinstance(authority, Mapping):
        raise ContractValidationError("accepted primary authority must be an object")
    return authority


def _presence_changes(authority: Mapping[str, Any]) -> tuple[PresenceChangeV1, ...]:
    values = authority.get("presence_changes", [])
    if not isinstance(values, list):
        raise ContractValidationError("accepted presence_changes must be an array")
    items = authority.get("items")
    if not isinstance(items, list) or not items:
        raise ContractValidationError("accepted ordinary authority omitted ordered items")
    item_keys: list[str] = []
    for item in items:
        if not isinstance(item, Mapping):
            raise ContractValidationError("accepted ordinary item is invalid")
        item_keys.append(_mapping_text(item, "item_key", "accepted ordinary item"))
    if len(item_keys) != len(set(item_keys)):
        raise ContractValidationError("accepted ordinary item keys contain duplicates")
    output: list[PresenceChangeV1] = []
    for value in values:
        if not isinstance(value, Mapping):
            raise ContractValidationError("accepted presence change is invalid")
        change = PresenceChangeV1(
            character_id=_mapping_text(value, "character_id", "presence change"),
            direction=_mapping_text(value, "direction", "presence change"),
            effective_after_item_key=_mapping_text(
                value,
                "effective_after_item_key",
                "presence change",
            ),
        )
        if change.effective_after_item_key not in item_keys:
            raise ContractValidationError("presence change cites an unknown item")
        output.append(change)
    item_order = {item_key: index for index, item_key in enumerate(item_keys)}
    return tuple(
        sorted(output, key=lambda value: item_order[value.effective_after_item_key])
    )


def _primary_durable_changes(
    authority: Mapping[str, Any],
    *,
    accepted_turn_id: str,
) -> tuple[DurableBranchChangeV1, ...]:
    values = authority.get("durable_changes", [])
    if not isinstance(values, list):
        raise ContractValidationError("accepted durable_changes must be an array")
    output: list[DurableBranchChangeV1] = []
    for value in values:
        if not isinstance(value, Mapping):
            raise ContractValidationError("accepted durable change is invalid")
        subjects = value.get("subject_ids")
        if not isinstance(subjects, list) or not subjects or not all(
            isinstance(subject, str) and subject.strip() for subject in subjects
        ):
            raise ContractValidationError("accepted durable change subjects are invalid")
        owner = value.get("knowledge_owner_id")
        if owner is not None and not isinstance(owner, str):
            raise ContractValidationError("accepted durable change owner is invalid")
        output.append(
            DurableBranchChangeV1(
                change_key=_mapping_text(value, "change_key", "durable change"),
                kind=_mapping_text(value, "kind", "durable change"),
                subject_ids=tuple(subjects),
                concise_change=_mapping_text(value, "concise_change", "durable change"),
                target_key=_mapping_text(value, "target_key", "durable change"),
                visibility=str(value.get("visibility", "public")),
                knowledge_owner_id=owner,
                accepted_turn_id=accepted_turn_id,
                source_kind="ordinary_primary_authority",
            )
        )
    if len({value.change_key for value in output}) != len(output):
        raise ContractValidationError("accepted durable change keys contain duplicates")
    return tuple(output)


def _mapping_text(value: Mapping[str, Any], key: str, owner: str) -> str:
    result = value.get(key)
    if not isinstance(result, str) or not result.strip():
        raise ContractValidationError(f"{owner} {key} is invalid")
    return result


def _mapping_text_array(
    value: Mapping[str, Any],
    key: str,
    owner: str,
) -> tuple[str, ...]:
    result = value.get(key)
    if not isinstance(result, list) or not all(
        isinstance(item, str) and item.strip() for item in result
    ):
        raise ContractValidationError(f"{owner} {key} is invalid")
    if len(result) != len(set(result)):
        raise ContractValidationError(f"{owner} {key} contains duplicates")
    return tuple(result)
