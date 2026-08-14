"""Materialize the minimum branch-scoped, read-only Pi Writer view."""

from __future__ import annotations

import json
import os
import re
import shutil
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from cera.errors import ContractValidationError, StateConflictError
from cera.sequence_first.contracts import SequenceItemV1
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
_INTERNAL_CAUSAL_GUIDANCE_KINDS = frozenset(
    kind.value for kind in SequenceItemV1.INTERNAL_CAUSAL_GUIDANCE_KINDS
)
_SURFACE_REALIZATION_KINDS = frozenset(
    kind.value for kind in SequenceItemV1.SURFACE_REALIZATION_KINDS
)
_GENESIS_PROJECTION_SCHEMA = "cera.pi_scene_genesis_record_projection.v1"
_WRITER_GENESIS_BUNDLE_SCHEMA = "cera.pi_scene.writer_genesis_claim_bundle.v2"
_WRITER_NO_GENESIS_CHARACTER_SCHEMA = "cera.pi_scene.writer_no_genesis_character.v1"
_WRITER_CONTEXT_PROJECTION_SCHEMA = "cera.pi_scene.writer_context_projection.v2"
_LEGACY_CONTEXT_GENESIS_REVISION = "cera.pi_scene.context_seed.v1"
_GENESIS_PROJECTION_FIELDS = frozenset(
    {
        "schema_version",
        "_cera_revision",
        "genesis_revision_id",
        "source_relative_path",
        "source_content_sha256",
        "visibility",
        "knowledge_owner_id",
        "record",
    }
)
_GENESIS_RECORD_FIELDS = frozenset(
    {
        "schema_version",
        "record_id",
        "record_version",
        "record_type",
        "epistemic_layer",
        "truth_status",
        "claim",
        "authority",
        "subject_ids",
        "owner_id",
        "knowledge_owner_ids",
        "visibility",
        "knowledge_route",
        "certainty",
        "content_class",
        "adult_eligibility",
        "story_start_presence",
        "relationship_from_id",
        "relationship_to_id",
        "source_refs",
        "valid_from",
        "valid_to",
        "supersedes",
        "tags",
        "expandable_sections",
        "payload_json",
    }
)
_WRITER_GENESIS_RECORD_FIELDS = (
    "record_id",
    "record_version",
    "record_type",
    "claim",
    "authority",
    "epistemic_layer",
    "truth_status",
    "certainty",
    "owner_id",
    "subject_ids",
    "knowledge_owner_ids",
    "visibility",
    "knowledge_route",
    "content_class",
    "adult_eligibility",
    "story_start_presence",
    "relationship_from_id",
    "relationship_to_id",
    "source_refs",
    "valid_from",
    "valid_to",
    "supersedes",
)
_BRANCH_CHANGE_FIELDS = frozenset(
    {
        "accepted_turn_id",
        "change_key",
        "kind",
        "subject_ids",
        "concise_change",
        "target_key",
        "visibility",
        "knowledge_owner_id",
        "source_kind",
    }
)
_BRANCH_CHANGE_KINDS = frozenset({"character_development", "relationship", "knowledge"})
_BRANCH_CHANGE_VISIBILITIES = frozenset(
    {"public", "character_private", "branch_internal_unspecified"}
)
_RELATIONSHIP_OVERLAY_FIELDS = frozenset({"target_key", "participants", "accepted_branch_changes"})
_RECORDER_PROJECTION_FIELDS = frozenset(
    {"accepted_turn_id", "source_kind", "visibility", "concise_change"}
)
_ADULT_PUBLIC_CONTINUITY_FIELDS = frozenset(
    {
        "accepted_turn_id",
        "event_key",
        "authority",
        "visibility",
        "non_explicit_summary",
        "lasting_story_meaning",
    }
)
_GENESIS_VISIBLE_WITHOUT_OWNER = frozenset({"public", "shared", "system_private"})


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
            realization_scope, response_sequence, provenance = _ordinary_authority_projection(
                source.primary_authority
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
            decision_projection = _ordinary_decision_projection(source.primary_authority)
        elif source.route is SceneRoute.ORDINARY:
            realization_scope = {
                "recording_phase": "post_accept_only",
                "primary_authority_path": "PRIMARY_SEQUENCE.json",
            }
            response_sequence = None
            decision_projection = None
        else:
            realization_scope = {
                "render_user_prompt": "writer_selected_under_handoff",
                "source_contribution_status": "handoff_adjudicated_story_material",
                "response_authority_path": "ADULT_HANDOFF.json",
            }
            response_sequence = None
            decision_projection = None
        if source.purpose == "writer":
            (
                writer_characters,
                writer_relationships,
                writer_memories,
                context_projection,
            ) = _project_writer_semantic_context(source)
        else:
            writer_characters = dict(source.characters)
            writer_relationships = dict(source.relationships)
            writer_memories = dict(source.relevant_memories)
            context_projection = None
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
        if decision_projection is not None:
            _write_json(root / "DECISION_BUNDLE.json", decision_projection)
        _write_json(root / "CURRENT_STATE.json", source.current_state)
        _write_named_mapping(root / "characters", writer_characters)
        _write_named_mapping(root / "relationships", writer_relationships)
        _write_numbered(root / "recent_prose", source.recent_prose)
        _write_named_mapping(root / "relevant_memories", writer_memories)
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
                "schema_version": (
                    "cera.pi_scene.writer_authority_order.v15"
                    if source.purpose == "writer"
                    else "cera.pi_scene.writer_authority_order.v12"
                ),
                "current_route": source.route.value,
                "current_purpose": source.purpose,
                "current_source_path": "USER_PROMPT.txt",
                "current_primary_authority_path": realization_path,
                "canonical_primary_sequence_custody": (
                    "python_and_post_accept_recorder_only"
                    if source.route is SceneRoute.ORDINARY and source.purpose == "writer"
                    else "current_visible_authority"
                ),
                "current_state_path": "CURRENT_STATE.json",
                "decision_context_path": (
                    "DECISION_BUNDLE.json" if decision_projection is not None else None
                ),
                "precedence": [
                    "current_accepted_state_baseline",
                    "current_primary_authority_changes_and_postconditions",
                    "supporting_accepted_history",
                    "style_and_craft_material",
                ],
                "supporting_history_rule": (
                    "Accepted records and recent prose preserve established events and "
                    "continuity. Compatible additions may extend an established event as "
                    "provisional continuity, but cannot replace locked current authority."
                ),
                "realization_scope": realization_scope,
                "presentation_contract": (
                    {
                        "source_usage": (
                            "Adjudicated story material may be reordered, revisited, "
                            "framed, or dramatized within the factual precedence above."
                        ),
                        "opening_location": "writer_selected",
                        "presentation_chronology": "writer_selected_within_causal_authority",
                        "interiority": "explicit_or_implicit_writer_choice",
                        "resulting_state_usage": (
                            "exact_at_end_with_only_planned_intermediate_transitions"
                        ),
                        "transient_detail_test": (
                            "A compatible addition may survive in accepted prose as "
                            "provisional continuity, but it must not change immutable "
                            "identity, adult age, kinship, accepted-event existence, "
                            "consent, withdrawal, or a planned major consequence."
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
                        "Compatible additions around established events are allowed and "
                        "may be recalled by later turns as provisional continuity. They "
                        "cannot override locked identity, adult age, kinship, consent, "
                        "withdrawal, accepted-event existence, or planned major consequences; "
                        "explicit user correction supersedes them."
                    ),
                },
                "context_projection": context_projection,
            },
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
    if purpose == "writer":
        try:
            authority_order = json.loads(
                (root / "zz_CURRENT_TURN_AUTHORITY.json").read_text(encoding="utf-8")
            )
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise StateConflictError("Writer context projection authority is unreadable") from exc
        context_projection = (
            authority_order.get("context_projection") if isinstance(authority_order, dict) else None
        )
        if (
            not isinstance(authority_order, dict)
            or authority_order.get("schema_version") != "cera.pi_scene.writer_authority_order.v15"
            or not isinstance(context_projection, dict)
            or context_projection.get("schema_version") != _WRITER_CONTEXT_PROJECTION_SCHEMA
        ):
            raise StateConflictError("Writer context projection authority is stale")
        for relative in listed:
            if not relative.startswith(
                ("characters/", "relationships/", "relevant_memories/")
            ) or not relative.endswith(".json"):
                continue
            try:
                value = json.loads((root / relative).read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise StateConflictError("Writer semantic context is unreadable") from exc
            if (
                isinstance(value, dict)
                and str(value.get("schema_version", "")).startswith(
                    "cera.pi_scene.writer_genesis_claim_bundle."
                )
                and value.get("schema_version") != _WRITER_GENESIS_BUNDLE_SCHEMA
            ):
                raise StateConflictError("Writer Genesis projection is stale")
    response_sequence_path = root / "RESPONSE_SEQUENCE.json"
    if purpose == "writer" and route == SceneRoute.ORDINARY.value:
        try:
            json.loads(response_sequence_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise StateConflictError(
                "ordinary Writer response authority is unavailable or invalid"
            ) from exc
        if (root / "zz_RESPONSE_START_GATE.json").exists():
            raise StateConflictError("rigid response-start gate is not permitted")
    elif (root / "zz_RESPONSE_START_GATE.json").exists() or response_sequence_path.exists():
        raise StateConflictError(
            "ordinary response authority exists outside ordinary Writer purpose"
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
        response_value = json.loads((root / "RESPONSE_SEQUENCE.json").read_text(encoding="utf-8"))
        if canonical_sha256(canonical_value) != custody_bindings["canonical_primary_sha256"]:
            raise StateConflictError("canonical Planner custody hash changed")
        if canonical_sha256(response_value) != custody_bindings["response_projection_sha256"]:
            raise StateConflictError("response projection custody hash changed")
        if canonical_sha256(provenance_value) != custody_bindings["projection_provenance_sha256"]:
            raise StateConflictError("projection provenance custody hash changed")
        if (
            provenance_value.get("canonical_primary_sha256")
            != custody_bindings["canonical_primary_sha256"]
            or provenance_value.get("response_projection_sha256")
            != custody_bindings["response_projection_sha256"]
        ):
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


def _project_writer_semantic_context(
    source: WriterViewInputV1,
) -> tuple[
    dict[str, Mapping[str, Any]],
    dict[str, Mapping[str, Any]],
    dict[str, Mapping[str, Any]],
    dict[str, Any],
]:
    """Expose exact active-actor claims while retaining source bytes by hash."""

    active_character_ids = frozenset(source.characters)
    production_genesis_mode = _production_genesis_mode(source)
    source_context = {
        "characters": dict(source.characters),
        "relationships": dict(source.relationships),
        "relevant_memories": dict(source.relevant_memories),
    }
    scoped_relationships, excluded_relationships = _active_writer_buckets(
        source.relationships,
        active_character_ids=active_character_ids,
        production_genesis_mode=production_genesis_mode,
    )
    scoped_memories, excluded_memories = _active_writer_buckets(
        source.relevant_memories,
        active_character_ids=active_character_ids,
        production_genesis_mode=production_genesis_mode,
    )
    projected_characters: dict[str, Mapping[str, Any]] = {}
    projected_relationships: dict[str, Mapping[str, Any]] = {}
    projected_memories: dict[str, Mapping[str, Any]] = {}
    omitted_unauthorized_genesis_records = 0
    omitted_unauthorized_branch_changes = 0
    for key, value in source.characters.items():
        projected, omitted_records, omitted_changes = _project_writer_mapping_value(
            key,
            value,
            mapping_kind="character",
            active_character_ids=active_character_ids,
            production_genesis_mode=production_genesis_mode,
        )
        projected_characters[key] = projected
        omitted_unauthorized_genesis_records += omitted_records
        omitted_unauthorized_branch_changes += omitted_changes
    for key, value in scoped_relationships.items():
        projected, omitted_records, omitted_changes = _project_writer_mapping_value(
            key,
            value,
            mapping_kind="relationship",
            active_character_ids=active_character_ids,
            production_genesis_mode=production_genesis_mode,
        )
        projected_relationships[key] = projected
        omitted_unauthorized_genesis_records += omitted_records
        omitted_unauthorized_branch_changes += omitted_changes
    for key, value in scoped_memories.items():
        projected, omitted_records, omitted_changes = _project_writer_mapping_value(
            key,
            value,
            mapping_kind="memory",
            active_character_ids=active_character_ids,
            production_genesis_mode=production_genesis_mode,
        )
        projected_memories[key] = projected
        omitted_unauthorized_genesis_records += omitted_records
        omitted_unauthorized_branch_changes += omitted_changes
    projected_context = {
        "characters": projected_characters,
        "relationships": projected_relationships,
        "relevant_memories": projected_memories,
    }
    projection_receipt = {
        "schema_version": _WRITER_CONTEXT_PROJECTION_SCHEMA,
        "active_character_ids": sorted(active_character_ids),
        "character_context_mode": (
            "production_genesis" if production_genesis_mode else "legacy_unpinned"
        ),
        "actor_bucket_rule": ("character_owned_buckets_require_an_exact_active_character_key"),
        "legacy_unowned_bucket_rule": (
            "an_exact_active_character_identity_and_authorized_visibility_are_required"
        ),
        "genesis_record_rule": ("exact_active_authorized_claims_with_private_omissions_hash_bound"),
        "source_semantic_context_sha256": canonical_sha256(source_context),
        "projected_semantic_context_sha256": canonical_sha256(projected_context),
        "excluded_relationship_actor_buckets": excluded_relationships,
        "excluded_memory_actor_buckets": excluded_memories,
        "omitted_unauthorized_genesis_records": omitted_unauthorized_genesis_records,
        "omitted_unauthorized_branch_changes": omitted_unauthorized_branch_changes,
    }
    return (
        projected_characters,
        projected_relationships,
        projected_memories,
        projection_receipt,
    )


def _active_writer_buckets(
    values: Mapping[str, Mapping[str, Any]],
    *,
    active_character_ids: frozenset[str],
    production_genesis_mode: bool,
) -> tuple[dict[str, Mapping[str, Any]], int]:
    selected: dict[str, Mapping[str, Any]] = {}
    for key, value in values.items():
        if key.startswith("character:"):
            relevant = key in active_character_ids
        else:
            relevant = any(
                _contains_exact_identity(value, character_id)
                for character_id in active_character_ids
            )
        if relevant and _raw_mapping_visibility_allows(
            value,
            active_character_ids=active_character_ids,
            production_genesis_mode=production_genesis_mode,
        ):
            selected[key] = value
    return selected, len(values) - len(selected)


def _contains_exact_identity(value: object, identity: str) -> bool:
    if value == identity:
        return True
    if isinstance(value, Mapping):
        return any(_contains_exact_identity(item, identity) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains_exact_identity(item, identity) for item in value)
    return False


def _project_writer_mapping_value(
    mapping_key: str,
    value: Mapping[str, Any],
    *,
    mapping_kind: str,
    active_character_ids: frozenset[str],
    production_genesis_mode: bool,
) -> tuple[dict[str, Any], int, int]:
    if "genesis_record_projections" in value:
        return _project_genesis_bundle(
            mapping_key,
            value,
            active_character_ids=active_character_ids,
        )
    if mapping_kind == "character" and production_genesis_mode:
        return _project_no_genesis_character(
            mapping_key,
            value,
            active_character_ids=active_character_ids,
        )
    if mapping_kind == "character":
        return dict(value), 0, 0
    projected, omitted_changes = _project_raw_branch_overlay(
        value,
        mapping_kind=mapping_kind,
        active_character_ids=active_character_ids,
        production_genesis_mode=production_genesis_mode,
    )
    return projected, 0, omitted_changes


def _production_genesis_mode(source: WriterViewInputV1) -> bool:
    revision: str | None = None
    if "genesis_revision" in source.current_state:
        raw_revision = source.current_state["genesis_revision"]
        if not isinstance(raw_revision, str) or not raw_revision.strip():
            raise ContractValidationError("Writer Genesis revision custody changed")
        revision = raw_revision
    if any(
        "genesis_record_projections" in value
        for values in (source.characters, source.relationships, source.relevant_memories)
        for value in values.values()
    ):
        return True
    return revision is not None and revision != _LEGACY_CONTEXT_GENESIS_REVISION


def _project_genesis_bundle(
    mapping_key: str,
    value: Mapping[str, Any],
    *,
    active_character_ids: frozenset[str],
) -> tuple[dict[str, Any], int, int]:
    expected_fields = {"character_id", "genesis_record_projections"}
    if "accepted_branch_changes" in value:
        expected_fields.add("accepted_branch_changes")
    if set(value) != expected_fields:
        raise ContractValidationError("Genesis Writer bundle fields changed")
    if value.get("character_id") != mapping_key:
        raise ContractValidationError("Genesis Writer bundle character changed")
    projections = value["genesis_record_projections"]
    if not isinstance(projections, (list, tuple)) or not projections:
        raise ContractValidationError("Genesis Writer bundle records are invalid")
    accepted_changes, omitted_changes = _project_branch_changes(
        value.get("accepted_branch_changes", ()),
        active_character_ids=active_character_ids,
    )
    claims: list[dict[str, Any]] = []
    omitted_record_sha256s: list[str] = []
    for projection in projections:
        if not isinstance(projection, Mapping) or set(projection) != _GENESIS_PROJECTION_FIELDS:
            raise ContractValidationError("Genesis Writer projection fields changed")
        if projection.get("schema_version") != _GENESIS_PROJECTION_SCHEMA:
            raise ContractValidationError("Genesis Writer projection schema changed")
        record = projection.get("record")
        if not isinstance(record, Mapping) or set(record) != _GENESIS_RECORD_FIELDS:
            raise ContractValidationError("Genesis Writer record fields changed")
        if record.get("schema_version") != "cera.genesis_record.v1":
            raise ContractValidationError("Genesis Writer record schema changed")
        _validate_genesis_projection_custody(projection, record)
        payload_json = record.get("payload_json")
        if not isinstance(payload_json, str):
            raise ContractValidationError("Genesis Writer payload custody changed")
        try:
            payload = json.loads(payload_json)
        except json.JSONDecodeError as exc:
            raise ContractValidationError("Genesis Writer payload custody changed") from exc
        if canonical_json(payload) != payload_json:
            raise ContractValidationError("Genesis Writer payload custody changed")
        record_sha256 = canonical_sha256(record)
        if not _genesis_record_is_writer_visible(
            record,
            active_character_ids=active_character_ids,
        ):
            omitted_record_sha256s.append(record_sha256)
            continue
        claims.append(
            {
                "source_schema_version": record["schema_version"],
                "source_record_sha256": record_sha256,
                **{field: record[field] for field in _WRITER_GENESIS_RECORD_FIELDS},
            }
        )
    return (
        {
            "schema_version": _WRITER_GENESIS_BUNDLE_SCHEMA,
            "character_id": mapping_key,
            "source_bundle_sha256": canonical_sha256(value),
            "source_record_count": len(projections),
            "claims": claims,
            "omitted_record_count": len(omitted_record_sha256s),
            "omitted_source_record_sha256s": omitted_record_sha256s,
            "source_accepted_branch_change_count": len(value.get("accepted_branch_changes", ())),
            "accepted_branch_changes": accepted_changes,
            "omitted_branch_change_count": omitted_changes,
        },
        len(omitted_record_sha256s),
        omitted_changes,
    )


def _validate_genesis_projection_custody(
    projection: Mapping[str, Any],
    record: Mapping[str, Any],
) -> None:
    visibility = record.get("visibility")
    if not isinstance(visibility, str) or projection.get("visibility") != visibility:
        raise ContractValidationError("Genesis Writer projection visibility changed")
    owner_id = record.get("owner_id")
    if owner_id is not None and not isinstance(owner_id, str):
        raise ContractValidationError("Genesis Writer record owner changed")
    knowledge_owner_ids = record.get("knowledge_owner_ids")
    if not isinstance(knowledge_owner_ids, list) or any(
        not isinstance(owner, str) for owner in knowledge_owner_ids
    ):
        raise ContractValidationError("Genesis Writer knowledge owners changed")
    expected_owner = owner_id
    if expected_owner is None and len(knowledge_owner_ids) == 1:
        expected_owner = knowledge_owner_ids[0]
    if projection.get("knowledge_owner_id") != expected_owner:
        raise ContractValidationError("Genesis Writer projection owner changed")


def _genesis_record_is_writer_visible(
    record: Mapping[str, Any],
    *,
    active_character_ids: frozenset[str],
) -> bool:
    visibility = record["visibility"]
    if not isinstance(visibility, str):
        raise ContractValidationError("Genesis Writer record visibility changed")
    owner_id = record["owner_id"]
    if owner_id is not None and not isinstance(owner_id, str):
        raise ContractValidationError("Genesis Writer record owner changed")
    knowledge_owner_ids = record["knowledge_owner_ids"]
    if not isinstance(knowledge_owner_ids, list) or any(
        not isinstance(owner, str) or not owner.startswith("character:")
        for owner in knowledge_owner_ids
    ):
        raise ContractValidationError("Genesis Writer knowledge owners changed")
    if len(knowledge_owner_ids) != len(set(knowledge_owner_ids)):
        raise ContractValidationError("Genesis Writer knowledge owners contain duplicates")
    subject_ids = record["subject_ids"]
    if not isinstance(subject_ids, list) or any(
        not isinstance(subject, str) for subject in subject_ids
    ):
        raise ContractValidationError("Genesis Writer record subjects changed")
    if len(subject_ids) != len(set(subject_ids)):
        raise ContractValidationError("Genesis Writer record subjects contain duplicates")
    if visibility == "owner_private":
        if not isinstance(owner_id, str) or not owner_id.startswith("character:"):
            raise ContractValidationError("Genesis Writer private owner changed")
        if owner_id not in active_character_ids:
            return False
        return not knowledge_owner_ids or bool(set(knowledge_owner_ids) & active_character_ids)
    if visibility not in _GENESIS_VISIBLE_WITHOUT_OWNER:
        raise ContractValidationError("Genesis Writer record visibility changed")
    if knowledge_owner_ids and not (set(knowledge_owner_ids) & active_character_ids):
        return False
    if visibility == "system_private":
        return bool(set(subject_ids) & active_character_ids)
    relevance_ids = set(subject_ids)
    for field in ("relationship_from_id", "relationship_to_id"):
        endpoint = record[field]
        if endpoint is not None:
            if not isinstance(endpoint, str):
                raise ContractValidationError("Genesis Writer relationship endpoint changed")
            relevance_ids.add(endpoint)
    if isinstance(owner_id, str):
        relevance_ids.add(owner_id)
    return bool(relevance_ids & active_character_ids)


def _project_no_genesis_character(
    mapping_key: str,
    value: Mapping[str, Any],
    *,
    active_character_ids: frozenset[str],
) -> tuple[dict[str, Any], int, int]:
    if set(value) != {
        "character_id",
        "genesis_record_available",
        "accepted_branch_changes",
    }:
        raise ContractValidationError("no-Genesis Writer character fields changed")
    if (
        value.get("character_id") != mapping_key
        or value.get("genesis_record_available") is not False
    ):
        raise ContractValidationError("no-Genesis Writer character identity changed")
    changes, omitted_changes = _project_branch_changes(
        value["accepted_branch_changes"],
        active_character_ids=active_character_ids,
    )
    return (
        {
            "schema_version": _WRITER_NO_GENESIS_CHARACTER_SCHEMA,
            "character_id": mapping_key,
            "genesis_record_available": False,
            "source_character_sha256": canonical_sha256(value),
            "source_accepted_branch_change_count": len(value["accepted_branch_changes"]),
            "accepted_branch_changes": changes,
            "omitted_branch_change_count": omitted_changes,
        },
        0,
        omitted_changes,
    )


def _project_raw_branch_overlay(
    value: Mapping[str, Any],
    *,
    mapping_kind: str,
    active_character_ids: frozenset[str],
    production_genesis_mode: bool,
) -> tuple[dict[str, Any], int]:
    if production_genesis_mode:
        _validate_production_raw_overlay(value, mapping_kind=mapping_kind)
    if "accepted_branch_changes" not in value:
        return dict(value), 0
    changes, omitted_changes = _project_branch_changes(
        value["accepted_branch_changes"],
        active_character_ids=active_character_ids,
    )
    projected = dict(value)
    projected["accepted_branch_changes"] = changes
    projected["writer_source_overlay_sha256"] = canonical_sha256(value)
    projected["writer_source_accepted_branch_change_count"] = len(value["accepted_branch_changes"])
    projected["writer_omitted_branch_change_count"] = omitted_changes
    return projected, omitted_changes


def _validate_production_raw_overlay(
    value: Mapping[str, Any],
    *,
    mapping_kind: str,
) -> None:
    fields = set(value)
    if mapping_kind == "relationship" and fields == _RELATIONSHIP_OVERLAY_FIELDS:
        target_key = value["target_key"]
        participants = value["participants"]
        if not isinstance(target_key, str) or not target_key.strip():
            raise ContractValidationError("Writer relationship overlay target changed")
        if not isinstance(participants, list) or any(
            not isinstance(participant, str) or not participant.strip()
            for participant in participants
        ):
            raise ContractValidationError("Writer relationship participants changed")
        if len(participants) != len(set(participants)):
            raise ContractValidationError("Writer relationship participants contain duplicates")
        return
    if fields == _BRANCH_CHANGE_FIELDS:
        _project_branch_changes(value=[value], active_character_ids=frozenset())
        return
    if fields == _RECORDER_PROJECTION_FIELDS:
        for field in ("accepted_turn_id", "source_kind", "concise_change"):
            item = value[field]
            if not isinstance(item, str) or not item.strip():
                raise ContractValidationError("Writer Recorder projection text changed")
        if value["visibility"] != "branch_internal_unspecified":
            raise ContractValidationError("Writer Recorder projection visibility changed")
        if value["source_kind"] not in {
            "ordinary_recorder_projection",
            "ordinary_secondary_canon",
        }:
            raise ContractValidationError("Writer Recorder projection source changed")
        return
    if mapping_kind == "memory" and fields == _ADULT_PUBLIC_CONTINUITY_FIELDS:
        for field in (
            "accepted_turn_id",
            "event_key",
            "non_explicit_summary",
            "lasting_story_meaning",
        ):
            item = value[field]
            if not isinstance(item, str) or not item.strip():
                raise ContractValidationError("Writer adult continuity text changed")
        if (
            value["authority"] != "accepted_adult_filtered_projection"
            or value["visibility"] != "public"
        ):
            raise ContractValidationError("Writer adult continuity authority changed")
        return
    raise ContractValidationError("Writer production overlay fields changed")


def _project_branch_changes(
    value: object,
    *,
    active_character_ids: frozenset[str],
) -> tuple[list[dict[str, Any]], int]:
    if not isinstance(value, (list, tuple)):
        raise ContractValidationError("Writer accepted branch changes are invalid")
    selected: list[dict[str, Any]] = []
    omitted = 0
    for change in value:
        if not isinstance(change, Mapping) or set(change) != _BRANCH_CHANGE_FIELDS:
            raise ContractValidationError("Writer accepted branch change fields changed")
        for field in (
            "accepted_turn_id",
            "change_key",
            "concise_change",
            "target_key",
            "source_kind",
        ):
            item = change[field]
            if not isinstance(item, str) or not item.strip():
                raise ContractValidationError("Writer accepted branch change text changed")
        if change["kind"] not in _BRANCH_CHANGE_KINDS:
            raise ContractValidationError("Writer accepted branch change kind changed")
        visibility = change["visibility"]
        if visibility not in _BRANCH_CHANGE_VISIBILITIES:
            raise ContractValidationError("Writer accepted branch change visibility changed")
        subject_ids = change["subject_ids"]
        if not isinstance(subject_ids, list) or any(
            not isinstance(subject, str) or not subject.strip() for subject in subject_ids
        ):
            raise ContractValidationError("Writer accepted branch change subjects changed")
        if len(subject_ids) != len(set(subject_ids)):
            raise ContractValidationError(
                "Writer accepted branch change subjects contain duplicates"
            )
        owner = change["knowledge_owner_id"]
        if visibility == "character_private":
            if (
                not isinstance(owner, str)
                or not owner.startswith("character:")
                or owner not in subject_ids
            ):
                raise ContractValidationError("Writer private branch change owner changed")
            include = owner in active_character_ids
        else:
            if owner is not None:
                raise ContractValidationError("Writer public branch change owner changed")
            include = bool(set(subject_ids) & active_character_ids)
        if include:
            selected.append(dict(change))
        else:
            omitted += 1
    return selected, omitted


def _raw_mapping_visibility_allows(
    value: Mapping[str, Any],
    *,
    active_character_ids: frozenset[str],
    production_genesis_mode: bool,
) -> bool:
    if "genesis_record_projections" in value or "visibility" not in value:
        return True
    visibility = value["visibility"]
    if not isinstance(visibility, str):
        raise ContractValidationError("Writer overlay visibility changed")
    owners = _raw_mapping_privacy_owners(value)
    if visibility == "character_private":
        if not owners:
            raise ContractValidationError("Writer private overlay has no owner")
        return bool(owners & active_character_ids)
    allowed_non_private = {"public", "branch_internal_unspecified"}
    if not production_genesis_mode:
        allowed_non_private.add("system_private")
    if visibility not in allowed_non_private:
        raise ContractValidationError("Writer overlay visibility changed")
    if owners:
        raise ContractValidationError("Writer non-private overlay names an owner")
    if visibility == "system_private":
        subjects = value.get("subject_ids")
        if not isinstance(subjects, list) or any(
            not isinstance(subject, str) for subject in subjects
        ):
            raise ContractValidationError("Writer system-private overlay subjects changed")
        return bool(set(subjects) & active_character_ids)
    return True


def _raw_mapping_privacy_owners(value: Mapping[str, Any]) -> set[str]:
    owners: set[str] = set()
    if "knowledge_owner_id" in value:
        owner = value["knowledge_owner_id"]
        if owner is not None:
            if not isinstance(owner, str) or not owner.startswith("character:"):
                raise ContractValidationError("Writer overlay owner changed")
            owners.add(owner)
    if "knowledge_owner_ids" in value:
        raw_owners = value["knowledge_owner_ids"]
        if not isinstance(raw_owners, (list, tuple)) or any(
            not isinstance(owner, str) or not owner.startswith("character:") for owner in raw_owners
        ):
            raise ContractValidationError("Writer overlay owners changed")
        if len(raw_owners) != len(set(raw_owners)):
            raise ContractValidationError("Writer overlay owners contain duplicates")
        owners.update(raw_owners)
    return owners


def _ordinary_authority_projection(
    primary_authority: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    sequence_authority = _ordinary_sequence_authority(primary_authority)
    items = sequence_authority.get("items")
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
                raise ContractValidationError("ordinary Writer durable-change binding is invalid")
            response_change_keys.update(durable_change_keys)
    if not response:
        raise ContractValidationError("ordinary Writer authority has no response scope")
    internal_guidance = [
        item_key
        for item_key in response
        if item_by_key[item_key].get("kind") in _INTERNAL_CAUSAL_GUIDANCE_KINDS
    ]
    surface_response = [
        item_key
        for item_key in response
        if item_by_key[item_key].get("kind") in _SURFACE_REALIZATION_KINDS
    ]
    classified = set(internal_guidance) | set(surface_response)
    unclassified = [item_key for item_key in response if item_key not in classified]
    if unclassified:
        raise ContractValidationError(
            "ordinary Writer authority has an unclassified response item kind"
        )
    supplied_set = set(supplied)
    projected_by_key: dict[str, dict[str, Any]] = {}
    response_mappings: list[dict[str, Any]] = []
    source_anchors: dict[str, str] = {}

    def _canonical_ancestors(item_key: str) -> tuple[str, ...]:
        ancestors: list[str] = []
        seen_ancestors: set[str] = set()
        current = item_by_key[item_key].get("causal_parent_item_key")
        while isinstance(current, str):
            if current in seen_ancestors:
                raise ContractValidationError("ordinary Writer causal graph contains a cycle")
            seen_ancestors.add(current)
            ancestors.append(current)
            parent = item_by_key.get(current)
            if parent is None:
                raise ContractValidationError(
                    "ordinary Writer causal graph references an unknown item"
                )
            current = parent.get("causal_parent_item_key")
        return tuple(ancestors)

    ancestor_keys = {item_key: _canonical_ancestors(item_key) for item_key in response}
    guidance_to_surface: dict[str, str] = {}
    for guidance_key in internal_guidance:
        linked_surface = next(
            (
                surface_key
                for surface_key in surface_response
                if guidance_key in ancestor_keys[surface_key]
            ),
            None,
        )
        if linked_surface is not None:
            guidance_to_surface[guidance_key] = linked_surface

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
        if item_key in internal_guidance:
            projected["projection_role"] = "internal_causal_guidance"
            if item_key in guidance_to_surface:
                projected["guides_surface_item_key"] = guidance_to_surface[item_key]
        else:
            projected["projection_role"] = "surface_realization"
            projected["response_scope"] = (
                "character" if item.get("owner_id") is not None else "world"
            )
            projected["guided_by_item_keys"] = [
                guidance_key
                for guidance_key in internal_guidance
                if guidance_to_surface[guidance_key] == item_key
            ]
        canonical_parent = item.get("causal_parent_item_key")
        source_anchor_key = None
        source_ancestor = next(
            (
                ancestor_key
                for ancestor_key in ancestor_keys[item_key]
                if ancestor_key in supplied_set
            ),
            None,
        )
        if source_ancestor is not None:
            source_anchor_key = source_anchors.setdefault(
                source_ancestor,
                "source-anchor-" + canonical_sha256(item_by_key[source_ancestor])[:20],
            )
            projected["source_anchor_key"] = source_anchor_key
        if canonical_parent in supplied_set:
            projected["causal_parent_item_key"] = None
        projected_by_key[item_key] = projected
        response_mappings.append(
            {
                "canonical_item_key": item_key,
                "canonical_causal_parent_item_key": canonical_parent,
                "projected_causal_parent_item_key": projected.get("causal_parent_item_key"),
                "source_anchor_key": source_anchor_key,
                "response_semantics_source": semantics_source,
            }
        )
    durable_changes = sequence_authority.get("durable_changes", [])
    presence_changes = sequence_authority.get("presence_changes", [])
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
    internal_guidance_items = [projected_by_key[item_key] for item_key in internal_guidance]
    surface_realization_items = [projected_by_key[item_key] for item_key in surface_response]
    scope = {
        "render_user_prompt": "writer_selected_under_planner_adjudication",
        "source_contribution_status": "planner_adjudicated_story_material",
        "response_item_keys": response,
        "internal_guidance_item_keys": internal_guidance,
        "surface_realization_item_keys": surface_response,
        "response_authority_path": "RESPONSE_SEQUENCE.json",
        "postcondition_authority_path": "RESPONSE_SEQUENCE.json#postconditions",
        "presentation_chronology": "writer_selected_within_causal_authority",
    }
    postconditions: dict[str, object] = {
        "resulting_public_state_must_be_true": sequence_authority.get("resulting_public_state"),
        "remain_open": sequence_authority.get("unresolved_threads"),
        "termination_constraint": sequence_authority.get("stopping_boundary"),
    }
    response_projection = {
        "schema_version": "cera.pi_scene.response_sequence.v8",
        "internal_causal_guidance": internal_guidance_items,
        "surface_realization_items": surface_realization_items,
        "durable_changes": response_durable_changes,
        "presence_changes": response_presence_changes,
        "postconditions": postconditions,
    }
    for field_name, value in postconditions.items():
        if value is None:
            raise ContractValidationError(f"ordinary Writer authority omits {field_name}")
    provenance = {
        "schema_version": "cera.pi_scene.response_projection_provenance.v3",
        "canonical_primary_sha256": canonical_sha256(primary_authority),
        "response_projection_sha256": canonical_sha256(response_projection),
        "excluded_source_item_keys": supplied,
        "constraint_item_keys": constraint,
        "source_anchor_bindings": [
            {
                "canonical_source_item_key": source_key,
                "source_anchor_key": anchor_key,
            }
            for source_key, anchor_key in source_anchors.items()
        ],
        "response_item_mappings": response_mappings,
    }
    return scope, response_projection, provenance


def _ordinary_sequence_authority(
    primary_authority: Mapping[str, Any],
) -> Mapping[str, Any]:
    nested = primary_authority.get("sequence")
    if nested is None:
        return primary_authority
    if not isinstance(nested, Mapping):
        raise ContractValidationError("ordinary cognition sequence is invalid")
    return nested


def _ordinary_decision_projection(
    primary_authority: Mapping[str, Any],
) -> dict[str, Any] | None:
    records = primary_authority.get("decision_records")
    links = primary_authority.get("decision_item_links")
    if records is None and links is None:
        return None
    if not isinstance(records, list) or not isinstance(links, list):
        raise ContractValidationError("ordinary cognition decision bundle is invalid")
    projected_records: list[dict[str, Any]] = []
    allowed = (
        "decision_key",
        "owner_id",
        "perceived_event_meaning",
        "personal_and_social_meaning",
        "response_layers",
        "selected_intent",
        "concise_decision_basis",
        "material_pressures",
        "autonomy_application",
        "anticipated_immediate_effect",
        "close_alternative",
        "uncertainty",
    )
    for value in records:
        if not isinstance(value, Mapping):
            raise ContractValidationError("ordinary cognition decision is invalid")
        projected_records.append({key: value[key] for key in allowed if key in value})
    provisional = primary_authority.get("provisional_dependencies", [])
    route_transition = primary_authority.get("route_transition")
    if not isinstance(provisional, list):
        raise ContractValidationError("ordinary cognition provisional scope is invalid")
    return {
        "schema_version": "cera.pi_scene.writer_decision_projection.v1",
        "decision_records": projected_records,
        "decision_item_links": links,
        "provisional_dependencies": provisional,
        "route_transition": route_transition,
    }


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
