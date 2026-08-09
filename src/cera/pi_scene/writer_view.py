"""Materialize the minimum branch-scoped, read-only Pi Writer view."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import shutil
from typing import Any, Mapping, Sequence
from uuid import uuid4

from cera.errors import ContractValidationError, StateConflictError
from cera.serialization import canonical_json, canonical_sha256, text_sha256

from .contracts import SceneRoute


_FORBIDDEN_KEY_PARTS = (
    "api_key",
    "apikey",
    "authorization",
    "access_token",
    "auth_token",
    "bearer",
    "credential",
    "password",
    "refresh_token",
    "secret",
    "session_token",
)

_FUTURE_RELIANCE_TEST = (
    "An invented detail requires accepted authority when deleting it would change "
    "causality, identity, relationship development, accepted knowledge, presence "
    "or location authority, ownership or provenance, physical or private condition, "
    "recurring practice, or anything a later turn could reasonably rely on."
)


@dataclass(frozen=True, slots=True)
class WriterViewInputV1:
    world_id: str
    branch_id: str
    scene_id: str
    turn_id: str
    candidate_id: str
    route: SceneRoute
    user_prompt: str
    primary_authority: Mapping[str, Any]
    current_state: Mapping[str, Any]
    characters: Mapping[str, Mapping[str, Any]]
    relationships: Mapping[str, Mapping[str, Any]]
    recent_prose: Sequence[Mapping[str, Any] | str]
    relevant_memories: Mapping[str, Mapping[str, Any]]
    voice_examples: Mapping[str, Mapping[str, Any] | str]
    craft_index: Mapping[str, Any]
    accepted_records: Sequence[Mapping[str, Any]]
    purpose: str = "writer"

    def __post_init__(self) -> None:
        for field_name in (
            "world_id",
            "branch_id",
            "scene_id",
            "turn_id",
            "candidate_id",
            "user_prompt",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ContractValidationError(f"Writer-view {field_name} is empty")
        if not self.primary_authority:
            raise ContractValidationError("Writer view requires primary authority")
        if self.purpose not in {"writer", "recorder"}:
            raise ContractValidationError("Writer-view purpose is invalid")
        if not self.current_state:
            raise ContractValidationError("Writer view requires current state")
        _reject_secret_keys(self.primary_authority)
        _reject_secret_keys(self.current_state)
        _reject_secret_keys(self.characters)
        _reject_secret_keys(self.relationships)
        _reject_secret_keys(self.relevant_memories)
        _reject_secret_keys(self.craft_index)


@dataclass(frozen=True, slots=True)
class MaterializedWriterViewV1:
    root: Path
    manifest_path: Path
    manifest_sha256: str
    file_count: int
    purpose: str


class WriterViewMaterializer:
    """Create an immutable candidate view without linking source repositories."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def materialize(self, source: WriterViewInputV1) -> MaterializedWriterViewV1:
        final = (
            self.root
            / f"world-{text_sha256(source.world_id)[:16]}"
            / f"branch-{text_sha256(source.branch_id)[:16]}"
            / f"candidate-{text_sha256(source.candidate_id)[:20]}"
        ).resolve()
        if not final.is_relative_to(self.root):
            raise ContractValidationError("Writer view escaped its configured root")
        visible = final / "visible"
        if final.exists():
            return verify_writer_view(visible)

        final.parent.mkdir(parents=True, exist_ok=True)
        stage = final.parent / f".{final.name}.{uuid4().hex}.tmp"
        stage.mkdir(parents=False, exist_ok=False)
        try:
            self._write_view(stage / "visible", stage / "custody", source)
            os.replace(stage, final)
        except Exception:
            if stage.exists():
                shutil.rmtree(stage)
            raise
        return verify_writer_view(visible)

    @staticmethod
    def _write_view(root: Path, custody_root: Path, source: WriterViewInputV1) -> None:
        custody_bindings: dict[str, str] = {}
        if source.route is SceneRoute.ORDINARY and source.purpose == "writer":
            realization_scope, response_sequence, provenance = (
                _ordinary_authority_projection(source.primary_authority)
            )
            _write_json(
                custody_root / "CANONICAL_SEQUENCE.json",
                source.primary_authority,
            )
            _write_json(
                custody_root / "PROJECTION_PROVENANCE.json",
                provenance,
            )
            custody_bindings = {
                "canonical_primary_sha256": canonical_sha256(source.primary_authority),
                "response_projection_sha256": canonical_sha256(response_sequence),
                "projection_provenance_sha256": canonical_sha256(provenance),
            }
        elif source.route is SceneRoute.ORDINARY:
            realization_scope = {
                "recording_phase": "post_accept_only",
                "primary_authority_path": "PRIMARY_SEQUENCE.json",
            }
            response_sequence = None
        else:
            realization_scope = {
                "render_user_prompt": False,
                "source_contribution_status": "already_supplied_context_only",
                "response_authority_path": "ADULT_HANDOFF.json",
            }
            response_sequence = None
        _write_text(root / "USER_PROMPT.txt", source.user_prompt)
        _write_json(
            root / "TURN.json",
            {
                "schema_version": "cera.pi_scene.writer_view_turn.v2",
                "world_id": source.world_id,
                "branch_id": source.branch_id,
                "scene_id": source.scene_id,
                "turn_id": source.turn_id,
                "candidate_id": source.candidate_id,
                "purpose": source.purpose,
            },
        )
        _write_json(
            root / "ROUTE.json",
            {
                "schema_version": "cera.pi_scene.writer_view_route.v2",
                "route": source.route.value,
                "purpose": source.purpose,
                "authority": (
                    "codex_sequence" if source.route is SceneRoute.ORDINARY else "adult_handoff"
                ),
                "creator_review_required": True,
                "ted_restrictions": "warn_only",
            },
        )
        if source.route is SceneRoute.ADULT or source.purpose == "recorder":
            _write_json(
                root
                / (
                    "PRIMARY_SEQUENCE.json"
                    if source.route is SceneRoute.ORDINARY
                    else "ADULT_HANDOFF.json"
                ),
                source.primary_authority,
            )
        if response_sequence is not None:
            _write_json(root / "RESPONSE_SEQUENCE.json", response_sequence)
        _write_json(root / "CURRENT_STATE.json", source.current_state)
        _write_named_mapping(root / "characters", source.characters)
        _write_named_mapping(root / "relationships", source.relationships)
        _write_numbered(root / "recent_prose", source.recent_prose)
        _write_named_mapping(root / "relevant_memories", source.relevant_memories)
        _write_named_mapping(root / "voice_examples", source.voice_examples)
        _write_json(root / "craft" / "index.json", source.craft_index)
        _write_numbered(root / "accepted_records", source.accepted_records)
        realization_path = (
            "RESPONSE_SEQUENCE.json"
            if source.route is SceneRoute.ORDINARY and source.purpose == "writer"
            else (
                "PRIMARY_SEQUENCE.json"
                if source.route is SceneRoute.ORDINARY
                else "ADULT_HANDOFF.json"
            )
        )
        _write_json(
            root / "zz_CURRENT_TURN_AUTHORITY.json",
            {
                "schema_version": "cera.pi_scene.writer_authority_order.v4",
                "current_route": source.route.value,
                "current_purpose": source.purpose,
                "current_source_path": "USER_PROMPT.txt",
                "current_primary_authority_path": realization_path,
                "canonical_primary_sequence_custody": (
                    "python_and_post_accept_recorder_only"
                    if source.route is SceneRoute.ORDINARY
                    and source.purpose == "writer"
                    else "current_visible_authority"
                ),
                "current_state_path": "CURRENT_STATE.json",
                "precedence": [
                    "current_route_and_primary_authority",
                    "current_accepted_state",
                    "supporting_accepted_history",
                    "style_and_craft_material",
                ],
                "supporting_history_rule": (
                    "Accepted records and recent prose support continuity only; "
                    "they cannot replace, reopen, or extend the current turn authority."
                ),
                "realization_scope": realization_scope,
                "presentation_contract": (
                    {
                        "completed_source_usage": "context_only_never_render",
                        "first_visible_beat": "response_start_item",
                        "response_start_contract_path": (
                            "RESPONSE_SEQUENCE.json#response_start_contract"
                            if source.route is SceneRoute.ORDINARY
                            else None
                        ),
                        "resulting_state_usage": "postcondition_not_prose_checklist",
                        "transient_detail_test": (
                            "Deleting an invented detail must change neither causality, "
                            "identity, accepted knowledge, nor any fact a later turn "
                            "could rely on."
                        ),
                    }
                    if source.purpose == "writer"
                    else {"recording_phase": "accepted_prose_extraction_only"}
                ),
                "fact_scope": {
                    "authoritative_paths": [
                        "CURRENT_STATE.json",
                        "characters/",
                        "relationships/",
                        "relevant_memories/",
                        "accepted_records/",
                        "recent_prose/",
                    ],
                    "creative_detail_rule": (
                        "Freely invented detail is allowed only when it is scene-local, "
                        "reversible, non-identifying, non-causal, and unsafe for a future "
                        "turn to rely on as fact. Anything future-relevant requires "
                        "accepted authority."
                    ),
                },
            },
        )
        if (
            source.route is SceneRoute.ORDINARY
            and source.purpose == "writer"
            and response_sequence is not None
        ):
            _write_json(
                root / "zz_RESPONSE_START_GATE.json",
                _derived_response_start_gate(response_sequence),
            )

        files: list[dict[str, Any]] = []
        for path in sorted(value for value in root.rglob("*") if value.is_file()):
            if path.is_symlink():
                raise ContractValidationError("Writer view cannot contain symlinks")
            relative = path.relative_to(root).as_posix()
            data = path.read_bytes()
            files.append(
                {
                    "path": relative,
                    "sha256": text_sha256(data.decode("utf-8")),
                    "bytes": len(data),
                }
            )
        manifest = {
            "schema_version": "cera.pi_scene.writer_view_manifest.v2",
            "scope": {
                "world_id_sha256": text_sha256(source.world_id),
                "branch_id_sha256": text_sha256(source.branch_id),
                "turn_id_sha256": text_sha256(source.turn_id),
                "candidate_id_sha256": text_sha256(source.candidate_id),
                "route": source.route.value,
                "purpose": source.purpose,
            },
            "allowed_tools": ["context"],
            "custody_bindings": custody_bindings,
            "files": files,
        }
        _write_json(root / "MANIFEST.json", manifest)


def verify_writer_view(root: Path) -> MaterializedWriterViewV1:
    root = root.resolve()
    manifest_path = root / "MANIFEST.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StateConflictError("Writer-view manifest is unreadable") from exc
    if not isinstance(manifest, dict) or manifest.get("schema_version") != (
        "cera.pi_scene.writer_view_manifest.v2"
    ):
        raise StateConflictError("Writer-view manifest identity changed")
    if manifest.get("allowed_tools") != ["context"]:
        raise StateConflictError("Writer-view tool boundary changed")
    scope = manifest.get("scope")
    purpose = scope.get("purpose") if isinstance(scope, dict) else None
    route = scope.get("route") if isinstance(scope, dict) else None
    if purpose not in {"writer", "recorder"}:
        raise StateConflictError("Writer-view purpose binding changed")
    if route not in {SceneRoute.ORDINARY.value, SceneRoute.ADULT.value}:
        raise StateConflictError("Writer-view route binding changed")
    entries = manifest.get("files")
    if not isinstance(entries, list):
        raise StateConflictError("Writer-view manifest file list is invalid")
    listed: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            raise StateConflictError("Writer-view file entry is invalid")
        relative = entry["path"]
        path = resolve_confined_path(root, relative, require_file=True)
        if path.is_symlink():
            raise StateConflictError("Writer view contains a symlink")
        data = path.read_text(encoding="utf-8")
        if text_sha256(data) != entry.get("sha256"):
            raise StateConflictError("Writer-view file hash changed")
        if len(data.encode("utf-8")) != entry.get("bytes"):
            raise StateConflictError("Writer-view file size changed")
        listed.add(relative)
    actual = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path != manifest_path
    }
    if actual != listed:
        raise StateConflictError("Writer-view manifest occupancy changed")
    start_gate_path = root / "zz_RESPONSE_START_GATE.json"
    response_sequence_path = root / "RESPONSE_SEQUENCE.json"
    if purpose == "writer" and route == SceneRoute.ORDINARY.value:
        try:
            response_sequence = json.loads(
                response_sequence_path.read_text(encoding="utf-8")
            )
            start_gate = json.loads(start_gate_path.read_text(encoding="utf-8"))
            expected_start_gate = _derived_response_start_gate(response_sequence)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ContractValidationError) as exc:
            raise StateConflictError(
                "ordinary Writer response-start gate is unavailable or invalid"
            ) from exc
        if start_gate != expected_start_gate:
            raise StateConflictError(
                "derived response-start gate differs from RESPONSE_SEQUENCE"
            )
        if start_gate_path.read_text(encoding="utf-8") != canonical_json(
            expected_start_gate
        ):
            raise StateConflictError("derived response-start gate is not canonical")
    elif start_gate_path.exists() or response_sequence_path.exists():
        raise StateConflictError(
            "response-start execution focus exists outside ordinary Writer purpose"
        )
    custody_bindings = manifest.get("custody_bindings")
    if not isinstance(custody_bindings, dict):
        raise StateConflictError("Writer-view custody binding is invalid")
    if custody_bindings:
        expected_keys = {
            "canonical_primary_sha256",
            "response_projection_sha256",
            "projection_provenance_sha256",
        }
        if purpose != "writer" or set(custody_bindings) != expected_keys:
            raise StateConflictError("Writer-view custody binding scope changed")
        if not all(
            isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value)
            for value in custody_bindings.values()
        ):
            raise StateConflictError("Writer-view custody hash is invalid")
        custody_root = root.parent / "custody"
        canonical_path = custody_root / "CANONICAL_SEQUENCE.json"
        provenance_path = custody_root / "PROJECTION_PROVENANCE.json"
        if (
            not canonical_path.is_file()
            or not provenance_path.is_file()
            or any(path.is_symlink() for path in custody_root.rglob("*"))
        ):
            raise StateConflictError("Writer-view custody artifact is unavailable")
        custody_files = {
            path.relative_to(custody_root).as_posix()
            for path in custody_root.rglob("*")
            if path.is_file()
        }
        if custody_files != {
            "CANONICAL_SEQUENCE.json",
            "PROJECTION_PROVENANCE.json",
        }:
            raise StateConflictError("Writer-view custody occupancy changed")
        canonical_value = json.loads(canonical_path.read_text(encoding="utf-8"))
        provenance_value = json.loads(provenance_path.read_text(encoding="utf-8"))
        response_value = json.loads(
            (root / "RESPONSE_SEQUENCE.json").read_text(encoding="utf-8")
        )
        if canonical_sha256(canonical_value) != custody_bindings[
            "canonical_primary_sha256"
        ]:
            raise StateConflictError("canonical Planner custody hash changed")
        if canonical_sha256(response_value) != custody_bindings[
            "response_projection_sha256"
        ]:
            raise StateConflictError("response projection custody hash changed")
        if canonical_sha256(provenance_value) != custody_bindings[
            "projection_provenance_sha256"
        ]:
            raise StateConflictError("projection provenance custody hash changed")
        if provenance_value.get("canonical_primary_sha256") != custody_bindings[
            "canonical_primary_sha256"
        ] or provenance_value.get("response_projection_sha256") != custody_bindings[
            "response_projection_sha256"
        ]:
            raise StateConflictError("projection provenance binding changed")
    elif (root.parent / "custody").exists():
        raise StateConflictError("non-Writer view unexpectedly contains custody artifacts")
    manifest_text = canonical_json(manifest)
    if manifest_path.read_text(encoding="utf-8") != manifest_text:
        raise StateConflictError("Writer-view manifest is not canonical")
    return MaterializedWriterViewV1(
        root=root,
        manifest_path=manifest_path,
        manifest_sha256=text_sha256(manifest_text),
        file_count=len(entries) + 1,
        purpose=purpose,
    )


def _derived_response_start_gate(
    response_sequence: Mapping[str, Any],
) -> dict[str, Any]:
    """Copy the ordinary response start into a compact, noncanonical recency cue."""

    items = response_sequence.get("items")
    contract = response_sequence.get("response_start_contract")
    if not isinstance(items, list) or not items or not isinstance(items[0], Mapping):
        raise ContractValidationError("response-start gate requires a first response item")
    if not isinstance(contract, Mapping):
        raise ContractValidationError("response-start gate requires its typed contract")
    first_item = items[0]
    copied_fields = {
        "response_start_item_key": contract.get("response_start_item_key"),
        "response_start_owner_id": contract.get("response_start_owner_id"),
        "response_start_kind": contract.get("response_start_kind"),
        "realization_mode": contract.get("realization_mode"),
        "completed_source_rendering": contract.get("completed_source_rendering"),
        "pre_response_narration": contract.get("pre_response_narration"),
    }
    if any(value is None for value in copied_fields.values()):
        raise ContractValidationError("response-start contract omits a copied gate field")
    if (
        response_sequence.get("response_start_item_key")
        != copied_fields["response_start_item_key"]
        or first_item.get("item_key") != copied_fields["response_start_item_key"]
        or first_item.get("owner_id") != copied_fields["response_start_owner_id"]
        or first_item.get("kind") != copied_fields["response_start_kind"]
    ):
        raise ContractValidationError("response-start contract differs from its first item")
    owner_response_semantics = first_item.get("owner_response_semantics")
    if (
        not isinstance(owner_response_semantics, str)
        or not owner_response_semantics.strip()
    ):
        raise ContractValidationError("response-start item omits owner response semantics")
    return {
        "schema_version": "cera.pi_scene.response_start_gate.v1",
        "authority_class": "derived_noncanonical_execution_focus",
        "canonical_authority_path": "RESPONSE_SEQUENCE.json",
        **copied_fields,
        "owner_response_semantics": owner_response_semantics,
        "detail_boundary": {
            "before_response_start": "none",
            "after_response_start": "compatible_transient_only",
            "future_reliance_test": _FUTURE_RELIANCE_TEST,
        },
    }


def resolve_confined_path(
    root: Path,
    relative: str,
    *,
    require_file: bool = False,
) -> Path:
    if not isinstance(relative, str) or not relative.strip():
        raise ContractValidationError("Writer-view relative path is empty")
    normalized = relative.removeprefix("@").replace("\\", "/")
    if (
        normalized.startswith("/")
        or re.match(r"^[A-Za-z]:", normalized)
        or normalized.startswith("//")
        or "\x00" in normalized
        or any(part in {"", ".", ".."} for part in normalized.split("/"))
    ):
        raise ContractValidationError("Writer-view path is not a safe relative path")
    root = root.resolve(strict=True)
    candidate = root
    for part in normalized.split("/"):
        candidate = candidate / part
        if candidate.is_symlink():
            raise ContractValidationError("Writer-view path contains a symbolic link")
    candidate = candidate.resolve(strict=True)
    if not candidate.is_relative_to(root):
        raise ContractValidationError("Writer-view path escaped its root")
    if require_file and not candidate.is_file():
        raise ContractValidationError("Writer-view path is not a file")
    return candidate


def _ordinary_authority_projection(
    primary_authority: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    items = primary_authority.get("items")
    if not isinstance(items, list) or not items:
        raise ContractValidationError("ordinary Writer authority requires ordered items")
    supplied: list[str] = []
    response: list[str] = []
    constraint: list[str] = []
    response_change_keys: set[str] = set()
    item_by_key: dict[str, Mapping[str, Any]] = {}
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, Mapping):
            raise ContractValidationError("ordinary Writer authority item is invalid")
        item_key = item.get("item_key")
        if not isinstance(item_key, str) or not item_key.strip() or item_key in seen:
            raise ContractValidationError("ordinary Writer authority item key is invalid")
        seen.add(item_key)
        item_by_key[item_key] = item
        claim_keys = item.get("protected_user_claim_keys", [])
        exact_quotes = item.get("protected_user_exact_quotes", [])
        if not isinstance(claim_keys, list) or not isinstance(exact_quotes, list):
            raise ContractValidationError("ordinary Writer source binding is invalid")
        if claim_keys or exact_quotes:
            supplied.append(item_key)
        elif item.get("kind") == "stopping_boundary":
            constraint.append(item_key)
        else:
            response.append(item_key)
            durable_change_keys = item.get("durable_change_keys", [])
            if not isinstance(durable_change_keys, list):
                raise ContractValidationError(
                    "ordinary Writer durable-change binding is invalid"
                )
            response_change_keys.update(durable_change_keys)
    if not response:
        raise ContractValidationError("ordinary Writer authority has no response scope")
    supplied_set = set(supplied)
    response_items: list[dict[str, Any]] = []
    response_mappings: list[dict[str, Any]] = []
    source_anchors: dict[str, str] = {}
    for item_key in response:
        item = item_by_key[item_key]
        owner_response_semantics = item.get("owner_response_semantics")
        semantics_source = "owner_response_semantics"
        if not isinstance(owner_response_semantics, str) or not owner_response_semantics.strip():
            owner_response_semantics = item.get("concise_meaning", item.get("summary"))
            semantics_source = "legacy_concise_meaning"
        if not isinstance(owner_response_semantics, str) or not owner_response_semantics.strip():
            raise ContractValidationError("ordinary response item omits owner semantics")
        projected = {
            key: item[key]
            for key in (
                "item_key",
                "kind",
                "owner_id",
                "causal_parent_item_key",
                "durable_change_keys",
            )
            if key in item
        }
        projected["owner_response_semantics"] = owner_response_semantics
        canonical_parent = item.get("causal_parent_item_key")
        completed_source_anchor_key = None
        if canonical_parent in supplied_set:
            completed_source_anchor_key = source_anchors.setdefault(
                str(canonical_parent),
                "completed-source-"
                + canonical_sha256(item_by_key[str(canonical_parent)])[:20],
            )
            projected["causal_parent_item_key"] = None
            projected["completed_source_anchor_key"] = completed_source_anchor_key
        response_items.append(projected)
        response_mappings.append(
            {
                "canonical_item_key": item_key,
                "canonical_causal_parent_item_key": canonical_parent,
                "projected_causal_parent_item_key": projected.get(
                    "causal_parent_item_key"
                ),
                "completed_source_anchor_key": completed_source_anchor_key,
                "response_semantics_source": semantics_source,
            }
        )
    durable_changes = primary_authority.get("durable_changes", [])
    presence_changes = primary_authority.get("presence_changes", [])
    if not isinstance(durable_changes, list) or not isinstance(presence_changes, list):
        raise ContractValidationError("ordinary Writer sequence change list is invalid")
    response_presence_changes = []
    for change in presence_changes:
        if not isinstance(change, Mapping):
            raise ContractValidationError("ordinary Writer presence change is invalid")
        if change.get("effective_after_item_key") in response:
            response_presence_changes.append(dict(change))
    response_durable_changes = []
    for change in durable_changes:
        if not isinstance(change, Mapping):
            raise ContractValidationError("ordinary Writer durable change is invalid")
        change_key = change.get("change_key")
        if change_key in response_change_keys:
            response_durable_changes.append(dict(change))
    scope = {
        "render_user_prompt": False,
        "source_contribution_status": "already_supplied_context_only",
        "response_item_keys": response,
        "response_start_item_key": response[0],
        "response_start_contract_path": (
            "RESPONSE_SEQUENCE.json#response_start_contract"
        ),
        "response_authority_path": "RESPONSE_SEQUENCE.json",
        "postcondition_authority_path": "RESPONSE_SEQUENCE.json#postconditions",
    }
    response_projection = {
        "schema_version": "cera.pi_scene.response_sequence.v5",
        "response_start_item_key": response[0],
        "response_start_contract": {
            "response_start_item_key": response[0],
            "response_start_owner_id": response_items[0].get("owner_id"),
            "response_start_kind": response_items[0].get("kind"),
            "completed_source_anchor_key": response_items[0].get(
                "completed_source_anchor_key"
            ),
            "realization_mode": "owner_response_after_completed_source",
            "completed_source_rendering": "implicit_cause_only",
            "pre_response_narration": "forbidden",
        },
        "items": response_items,
        "durable_changes": response_durable_changes,
        "presence_changes": response_presence_changes,
        "postconditions": {
            "resulting_public_state_must_be_true": primary_authority.get(
                "resulting_public_state"
            ),
            "remain_open": primary_authority.get("unresolved_threads"),
            "termination_constraint": primary_authority.get("stopping_boundary"),
        },
    }
    for field_name, value in response_projection["postconditions"].items():
        if value is None:
            raise ContractValidationError(
                f"ordinary Writer authority omits {field_name}"
            )
    provenance = {
        "schema_version": "cera.pi_scene.response_projection_provenance.v1",
        "canonical_primary_sha256": canonical_sha256(primary_authority),
        "response_projection_sha256": canonical_sha256(response_projection),
        "excluded_source_item_keys": supplied,
        "constraint_item_keys": constraint,
        "source_anchor_bindings": [
            {
                "canonical_source_item_key": source_key,
                "completed_source_anchor_key": anchor_key,
            }
            for source_key, anchor_key in source_anchors.items()
        ],
        "response_item_mappings": response_mappings,
    }
    return scope, response_projection, provenance


def _write_named_mapping(root: Path, values: Mapping[str, Any]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    for key, value in sorted(values.items()):
        filename = f"{_slug(key)}-{text_sha256(key)[:10]}.json"
        payload = value if isinstance(value, Mapping) else {"text": str(value)}
        _write_json(root / filename, payload)


def _write_numbered(root: Path, values: Sequence[Mapping[str, Any] | str]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    for index, value in enumerate(values, start=1):
        if isinstance(value, Mapping):
            _write_json(root / f"{index:04d}.json", value)
        else:
            _write_text(root / f"{index:04d}.txt", str(value))


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    _reject_secret_keys(value)
    _write_text(path, canonical_json(dict(value)))


def _write_text(path: Path, value: str) -> None:
    if not isinstance(value, str):
        raise ContractValidationError("Writer-view text value is invalid")
    data = value.encode("utf-8")
    if len(data) > 2_000_000:
        raise ContractValidationError("one Writer-view file exceeds two megabytes")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="") as handle:
        handle.write(value)


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")[:40]
    return slug or "item"


def _reject_secret_keys(value: Any, path: str = "root") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = str(key).lower().replace("-", "_")
            if any(part in normalized for part in _FORBIDDEN_KEY_PARTS):
                raise ContractValidationError(f"Writer view contains forbidden key at {path}")
            _reject_secret_keys(item, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _reject_secret_keys(item, f"{path}[{index}]")
