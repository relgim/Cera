"""Mechanical continuous-world candidate, promotion, and scene-change store."""

from __future__ import annotations

from contextlib import ExitStack, contextmanager
import copy
from dataclasses import dataclass, fields as dataclass_fields
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
from tempfile import mkdtemp
from threading import RLock
import traceback
from typing import Any, Callable, ClassVar, Iterable

from cera.creator_review.models import CreatorReviewAction
from cera.errors import ContractValidationError, StateConflictError
from cera.serialization import canonical_bytes, canonical_sha256, text_sha256, to_primitive

from .contracts import (
    AcceptedFinalSequenceEnvelopeV1,
    AcceptedTurnPairV1,
    FinalInformationVisibility,
    PersistenceRecordClass,
    SceneSummaryDerivedViewV1,
    SceneSummaryTurnProvenanceV2,
    SceneSummaryV1,
    ValidatorFinalizationPackageV1,
    WorldEditOperationKind,
)
from .record_policy import (
    PERSISTENCE_POLICY_SHA256,
    validate_persistence_field_path,
    validate_post_edit_record,
)
from .packets import LeanSceneChangeContextV1
from .path_policy import preflight_windows_legacy_paths
from .path_custody import (
    NO_FOLLOW_CUSTODY_POLICY_SHA256,
    NoFollowTargetCustodyV1,
    capture_target_custody,
    ensure_parent_chain,
    inspect_leaf,
    lexical_absolute,
    lexical_target,
    locked_directory_chain,
    safe_create_new_bytes,
    safe_read_bytes,
    safe_replace,
    safe_replace_directory,
    unlink_if_identity,
    validate_tree_no_follow,
    verify_target_custody,
)


_ACTIVE_DIRS = (
    "Characters",
    "Relationships",
    "Rules",
    "Locations",
    "Events",
    "Scenes",
)
_SECRET_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "bearer",
        "credential",
        "credentials",
        "password",
        "secret",
        "token",
    }
)
_EMBEDDED_SECRET_PATTERNS = (
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{6,}"),
    re.compile(r"(?i)\bsk-[A-Za-z0-9_-]{8,}"),
    re.compile(
        r"(?i)(\b(?:api[_-]?key|authorization|access[_-]?token|auth[_-]?token)\b\s*[:=]\s*[\"']?)[^\s\"'&,}]{6,}"
    ),
    re.compile(r"(?i)([?&](?:api[_-]?key|access[_-]?token|token)=)[^&#\s]{6,}"),
)


CONTINUOUS_BRANCH_MATERIALIZATION_PATH_POLICY_SHA256 = canonical_sha256(
    {
        "policy_version": "cera.continuous_branch_materialization_path_policy.v1",
        "no_follow_custody_policy_sha256": NO_FOLLOW_CUSTODY_POLICY_SHA256,
        "maximum_resolved_path_characters": 248,
        "receipt_locator": "branch_cutoff_full_sha256",
        "temporary_name_rule": "same_directory_append_dot_tmp",
        "staging_rule": "exclusive_sibling_directory_then_atomic_replace",
        "full_hashes_are_authority": True,
    }
)


def _preflight_branch_materialization_receipt_paths(root: Path) -> tuple[int, int]:
    """Prove the full-SHA receipt and its same-directory temp are writable."""

    receipt = (
        lexical_absolute(root)
        / "BRANCH_MATERIALIZATION"
        / (("0" * 64) + ".json")
    )
    return preflight_windows_legacy_paths(
        receipt,
        receipt.with_name(receipt.name + ".tmp"),
        label="continuous branch materialization receipt",
    )


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


def _slug(value: str, field: str) -> str:
    import re

    if not isinstance(value, str) or re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,95}", value) is None:
        raise ContractValidationError(f"{field} must be a bounded filesystem-safe slug")
    return value


def _identity_filename(value: str, suffix: str) -> str:
    """Map typed record identities to portable, collision-resistant filenames."""

    import re

    normalized = re.sub(r"[^a-z0-9_-]+", "-", value.casefold()).strip("-_")
    stem = (normalized or "record")[:80]
    return f"{stem}-{text_sha256(value)[:12]}{suffix}"


def _json_pointer_parts(pointer: str) -> tuple[str, ...]:
    if not pointer.startswith("/"):
        raise ContractValidationError("JSON pointer must start with slash")
    if pointer == "/":
        return ()
    return tuple(part.replace("~1", "/").replace("~0", "~") for part in pointer[1:].split("/"))


def _resolve_parent(document: Any, pointer: str) -> tuple[Any, str]:
    parts = _json_pointer_parts(pointer)
    if not parts:
        raise ContractValidationError("root replacement is not a field edit")
    current = document
    for part in parts[:-1]:
        if isinstance(current, dict) and part in current:
            current = current[part]
        elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
            current = current[int(part)]
        else:
            raise StateConflictError("world edit parent path is unavailable")
    return current, parts[-1]


def _apply_json_operation(document: Any, operation) -> None:
    parent, key = _resolve_parent(document, operation.field_path)
    if isinstance(parent, dict):
        exists = key in parent
        current = parent.get(key)
        setter = lambda value: parent.__setitem__(key, value)
        remover = lambda: parent.pop(key)
    elif isinstance(parent, list) and key.isdigit():
        index = int(key)
        exists = index < len(parent)
        current = parent[index] if exists else None
        setter = lambda value: parent.__setitem__(index, value)
        remover = lambda: parent.pop(index)
    else:
        raise StateConflictError("world edit target path is invalid")
    if operation.operation is WorldEditOperationKind.ADD:
        if exists:
            raise StateConflictError("add operation target already exists")
        setter(operation.value)
    elif operation.operation is WorldEditOperationKind.REPLACE:
        if not exists:
            raise StateConflictError("replace operation target is absent")
        setter(operation.value)
    elif operation.operation is WorldEditOperationKind.REMOVE:
        if not exists:
            raise StateConflictError("remove operation target is absent")
        remover()
    elif operation.operation is WorldEditOperationKind.APPEND_UNIQUE:
        if not exists or not isinstance(current, list):
            raise StateConflictError("append_unique target must be an existing array")
        if operation.value not in current:
            current.append(operation.value)
    elif operation.operation is WorldEditOperationKind.INCREMENT:
        if not exists or isinstance(current, bool) or not isinstance(current, (int, float)):
            raise StateConflictError("increment target must be numeric")
        if isinstance(operation.value, bool) or not isinstance(operation.value, (int, float)):
            raise ContractValidationError("increment value must be numeric")
        setter(current + operation.value)
    else:
        raise ContractValidationError("create_file cannot edit an existing document")


def redact_secrets(value: Any) -> Any:
    """Remove credential-like fields while retaining useful local diagnostics."""

    if isinstance(value, dict):
        result = {}
        for key, child in value.items():
            normalized = str(key).casefold().replace("-", "_")
            if normalized in _SECRET_KEYS or any(
                marker in normalized for marker in ("api_key", "auth_header", "bearer_token")
            ):
                result[str(key)] = "[REDACTED]"
            else:
                result[str(key)] = redact_secrets(child)
        return result
    if isinstance(value, list):
        return [redact_secrets(child) for child in value]
    if isinstance(value, tuple):
        return [redact_secrets(child) for child in value]
    if isinstance(value, str):
        redacted = value
        redacted = _EMBEDDED_SECRET_PATTERNS[0].sub("Bearer [REDACTED]", redacted)
        redacted = _EMBEDDED_SECRET_PATTERNS[1].sub("[REDACTED]", redacted)
        redacted = _EMBEDDED_SECRET_PATTERNS[2].sub(r"\1[REDACTED]", redacted)
        redacted = _EMBEDDED_SECRET_PATTERNS[3].sub(r"\1[REDACTED]", redacted)
        return redacted
    return value


@dataclass(frozen=True, slots=True)
class CandidateWorldViewV1:
    turn_id: str
    root: Path
    source_active_sha256: str


@dataclass(frozen=True, slots=True)
class WorldPromotionReceiptV1:
    SCHEMA_VERSION: ClassVar[str] = "cera.continuous_world_promotion_receipt.v2"

    schema_version: str
    world_id: str
    branch_id: str
    turn_id: str
    creator_action: CreatorReviewAction
    package_sha256: str
    candidate_sha256: str
    authority_context_sha256: str
    active_before_sha256: str
    active_after_sha256: str
    changed_files: tuple[str, ...]
    created_fields: tuple[str, ...]
    accepted: bool
    planner_append_required: bool
    recorded_at_utc: str

    @property
    def receipt_sha256(self) -> str:
        return canonical_sha256(to_primitive(self))


@dataclass(frozen=True, slots=True)
class ContinuousActiveManifestEntryV1:
    """One byte-exact file in an accepted branch ACTIVE tree."""

    relative_path: str
    record_category: str
    content_sha256: str
    size_bytes: int
    revision: int | None

    def __post_init__(self) -> None:
        normalized = self.relative_path.replace("\\", "/")
        if (
            normalized != self.relative_path
            or not normalized
            or normalized.startswith("/")
            or ".." in Path(normalized).parts
        ):
            raise ContractValidationError("branch materialization manifest path is invalid")
        if self.record_category not in {
            "characters",
            "relationships",
            "rules",
            "locations",
            "events",
            "scenes",
            "world_state",
            "world_index",
            "accepted_checkpoint_artifact",
        }:
            raise ContractValidationError(
                "branch materialization manifest category is invalid"
            )
        if not re.fullmatch(r"[0-9a-f]{64}", self.content_sha256):
            raise ContractValidationError(
                "branch materialization manifest hash is invalid"
            )
        if type(self.size_bytes) is not int or self.size_bytes < 0:
            raise ContractValidationError(
                "branch materialization manifest byte count is invalid"
            )
        if self.revision is not None and (
            type(self.revision) is not int or self.revision < 1
        ):
            raise ContractValidationError(
                "branch materialization manifest revision is invalid"
            )


@dataclass(frozen=True, slots=True)
class ContinuousInheritedSummarySourceV1:
    """Exact child-side authority required before a summary may be suppressed."""

    character_id: str
    source_path_or_record_id: str
    source_revision: int
    source_sha256: str
    source_authority_classification: str
    envelope_sha256: str

    def __post_init__(self) -> None:
        if not self.character_id.strip():
            raise ContractValidationError("branch summary owner is invalid")
        normalized = self.source_path_or_record_id.replace("\\", "/")
        if (
            normalized != self.source_path_or_record_id
            or not normalized.startswith("ACTIVE/Characters/")
            or ".." in Path(normalized).parts
        ):
            raise ContractValidationError("branch summary source path is invalid")
        if type(self.source_revision) is not int or self.source_revision < 1:
            raise ContractValidationError("branch summary source revision is invalid")
        if self.source_authority_classification != "active_authoritative_record_fields":
            raise ContractValidationError("branch summary source authority is invalid")
        for value in (self.source_sha256, self.envelope_sha256):
            if not re.fullmatch(r"[0-9a-f]{64}", value):
                raise ContractValidationError("branch summary source hash is invalid")


@dataclass(frozen=True, slots=True)
class ContinuousBranchMaterializationPathPlanV1:
    """Lexical no-follow plan for one accepted-checkpoint branch publication."""

    SCHEMA_VERSION: ClassVar[str] = (
        "cera.continuous_branch_materialization_path_plan.v1"
    )

    schema_version: str
    path_policy_sha256: str
    parent_branch_relative_path: str
    child_branch_relative_path: str
    staging_relative_path: str
    receipt_relative_path: str
    receipt_temporary_relative_path: str
    child_resolved_path_characters: int
    staging_resolved_path_characters: int
    receipt_resolved_path_characters: int
    receipt_temporary_resolved_path_characters: int
    parent_branch_custody: NoFollowTargetCustodyV1
    child_branch_custody: NoFollowTargetCustodyV1
    staging_custody: NoFollowTargetCustodyV1
    receipt_custody: NoFollowTargetCustodyV1
    receipt_temporary_custody: NoFollowTargetCustodyV1
    parent_branch_directory_identity_sha256: str
    staging_directory_identity_sha256: str
    verification_generation_sha256: str
    plan_sha256: str

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError(
                "continuous branch materialization path-plan schema changed"
            )
        if self.path_policy_sha256 != CONTINUOUS_BRANCH_MATERIALIZATION_PATH_POLICY_SHA256:
            raise ContractValidationError(
                "continuous branch materialization path policy changed"
            )
        for field_name in (
            "parent_branch_directory_identity_sha256",
            "staging_directory_identity_sha256",
            "verification_generation_sha256",
            "plan_sha256",
        ):
            if re.fullmatch(r"[0-9a-f]{64}", getattr(self, field_name)) is None:
                raise ContractValidationError(
                    f"branch materialization {field_name} is invalid"
                )
        paths = (
            self.parent_branch_relative_path,
            self.child_branch_relative_path,
            self.staging_relative_path,
        )
        if any(
            not isinstance(value, str)
            or not value
            or value.startswith(("/", "\\"))
            or ".." in Path(value).parts
            for value in paths
        ):
            raise ContractValidationError(
                "branch materialization lexical branch path is invalid"
            )
        if re.fullmatch(
            r"BRANCH_MATERIALIZATION/[0-9a-f]{64}\.json",
            self.receipt_relative_path,
        ) is None or self.receipt_temporary_relative_path != (
            self.receipt_relative_path + ".tmp"
        ):
            raise ContractValidationError(
                "branch materialization receipt locator changed"
            )
        lengths = (
            self.child_resolved_path_characters,
            self.staging_resolved_path_characters,
            self.receipt_resolved_path_characters,
            self.receipt_temporary_resolved_path_characters,
        )
        if any(type(value) is not int or value < 1 or value > 248 for value in lengths):
            raise ContractValidationError(
                "branch materialization path exceeds legacy budget"
            )
        if (
            self.parent_branch_custody.lexical_relative_path
            != self.parent_branch_relative_path
            or self.child_branch_custody.lexical_relative_path
            != self.child_branch_relative_path
            or self.staging_custody.lexical_relative_path
            != self.staging_relative_path
            or self.receipt_custody.lexical_relative_path
            != self.receipt_relative_path
            or self.receipt_temporary_custody.lexical_relative_path
            != self.receipt_temporary_relative_path
        ):
            raise ContractValidationError(
                "branch materialization custody locator changed"
            )
        expected_generation = canonical_sha256(
            {
                "path_policy_sha256": self.path_policy_sha256,
                "custody_sha256s": (
                    self.parent_branch_custody.custody_sha256,
                    self.child_branch_custody.custody_sha256,
                    self.staging_custody.custody_sha256,
                    self.receipt_custody.custody_sha256,
                    self.receipt_temporary_custody.custody_sha256,
                ),
                "parent_branch_directory_identity_sha256": (
                    self.parent_branch_directory_identity_sha256
                ),
                "staging_directory_identity_sha256": (
                    self.staging_directory_identity_sha256
                ),
            }
        )
        if self.verification_generation_sha256 != expected_generation:
            raise ContractValidationError(
                "branch materialization verification generation changed"
            )
        payload = to_primitive(self)
        payload.pop("plan_sha256")
        if self.plan_sha256 != canonical_sha256(payload):
            raise ContractValidationError(
                "branch materialization path-plan binding changed"
            )


@dataclass(frozen=True, slots=True)
class ContinuousBranchMaterializationReceiptV1:
    """Immutable Python custody for a complete child ACTIVE snapshot."""

    SCHEMA_VERSION: ClassVar[str] = "cera.continuous_branch_materialization_receipt.v1"

    schema_version: str
    world_id: str
    parent_branch_id: str
    child_branch_id: str
    accepted_checkpoint_turn_id: str
    ordered_accepted_turn_ids: tuple[str, ...]
    accepted_ancestry_sha256: str
    branch_cutoff_sha256: str
    parent_provider_thread_sha256: str
    parent_world_directory_identity_sha256: str
    child_world_directory_identity_sha256: str
    parent_active_tree_sha256: str
    child_initial_active_tree_sha256: str
    parent_active_manifest: tuple[ContinuousActiveManifestEntryV1, ...]
    child_initial_active_manifest: tuple[ContinuousActiveManifestEntryV1, ...]
    parent_accepted_checkpoint_manifest: tuple[ContinuousActiveManifestEntryV1, ...]
    child_accepted_checkpoint_manifest: tuple[ContinuousActiveManifestEntryV1, ...]
    parent_world_state_sha256: str
    child_world_state_sha256: str
    world_state_revision: int
    current_scene_id: str
    accepted_head_turn_id: str
    authority_policy_version: str
    privacy_policy_version: str
    protected_user_policy_version: str
    session_policy_version: str
    persistence_policy_sha256: str
    inherited_summary_sources: tuple[ContinuousInheritedSummarySourceV1, ...]
    receipt_sha256: str

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError(
                "continuous branch materialization schema changed"
            )
        for field_name in (
            "world_id",
            "parent_branch_id",
            "child_branch_id",
            "accepted_checkpoint_turn_id",
            "current_scene_id",
            "accepted_head_turn_id",
            "authority_policy_version",
            "privacy_policy_version",
            "protected_user_policy_version",
            "session_policy_version",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ContractValidationError(
                    f"branch materialization {field_name} is invalid"
                )
        if self.parent_branch_id == self.child_branch_id:
            raise ContractValidationError("branch materialization did not create a child")
        if (
            not self.ordered_accepted_turn_ids
            or len(self.ordered_accepted_turn_ids)
            != len(set(self.ordered_accepted_turn_ids))
            or self.ordered_accepted_turn_ids[-1] != self.accepted_checkpoint_turn_id
            or self.accepted_head_turn_id != self.accepted_checkpoint_turn_id
        ):
            raise ContractValidationError(
                "branch materialization accepted-turn order is invalid"
            )
        if type(self.world_state_revision) is not int or self.world_state_revision < 1:
            raise ContractValidationError("branch materialization revision is invalid")
        for field_name in (
            "accepted_ancestry_sha256",
            "branch_cutoff_sha256",
            "parent_provider_thread_sha256",
            "parent_world_directory_identity_sha256",
            "child_world_directory_identity_sha256",
            "parent_active_tree_sha256",
            "child_initial_active_tree_sha256",
            "parent_world_state_sha256",
            "child_world_state_sha256",
            "persistence_policy_sha256",
            "receipt_sha256",
        ):
            if not re.fullmatch(r"[0-9a-f]{64}", getattr(self, field_name)):
                raise ContractValidationError(
                    f"branch materialization {field_name} is invalid"
                )
        for manifest in (
            self.parent_active_manifest,
            self.child_initial_active_manifest,
        ):
            paths = tuple(value.relative_path for value in manifest)
            if (
                not paths
                or paths != tuple(sorted(paths))
                or len(paths) != len(set(paths))
                or "WORLD_STATE.json" not in paths
                or "WORLD_INDEX.jsonl" not in paths
            ):
                raise ContractValidationError(
                    "branch materialization manifest is incomplete or unordered"
                )
        for manifest in (
            self.parent_accepted_checkpoint_manifest,
            self.child_accepted_checkpoint_manifest,
        ):
            paths = tuple(value.relative_path for value in manifest)
            if (
                len(paths) != len(self.ordered_accepted_turn_ids)
                or paths != tuple(sorted(paths))
                or len(paths) != len(set(paths))
            ):
                raise ContractValidationError(
                    "branch materialization checkpoint-artifact manifest is incomplete"
                )
        summary_keys = tuple(
            (value.character_id, value.source_path_or_record_id)
            for value in self.inherited_summary_sources
        )
        if summary_keys != tuple(sorted(summary_keys)) or len(summary_keys) != len(
            set(summary_keys)
        ):
            raise ContractValidationError(
                "branch materialization summary sources are duplicated or unordered"
            )
        expected_cutoff = canonical_sha256(
            {
                "world_id": self.world_id,
                "parent_branch_id": self.parent_branch_id,
                "child_branch_id": self.child_branch_id,
                "accepted_checkpoint_turn_id": self.accepted_checkpoint_turn_id,
                "ordered_accepted_turn_ids": self.ordered_accepted_turn_ids,
                "accepted_ancestry_sha256": self.accepted_ancestry_sha256,
                "parent_provider_thread_sha256": self.parent_provider_thread_sha256,
                "parent_active_tree_sha256": self.parent_active_tree_sha256,
                "parent_world_state_sha256": self.parent_world_state_sha256,
            }
        )
        if self.branch_cutoff_sha256 != expected_cutoff:
            raise ContractValidationError("branch materialization cutoff changed")
        payload = to_primitive(self)
        payload.pop("receipt_sha256")
        if self.receipt_sha256 != canonical_sha256(payload):
            raise ContractValidationError("branch materialization receipt changed")


@dataclass(frozen=True, slots=True)
class ContinuousBranchMaterializationReceiptV2(
    ContinuousBranchMaterializationReceiptV1
):
    """Accepted-checkpoint receipt bound to lexical no-follow path custody."""

    SCHEMA_VERSION: ClassVar[str] = "cera.continuous_branch_materialization_receipt.v2"

    path_plan: ContinuousBranchMaterializationPathPlanV1

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError(
                "continuous branch materialization schema changed"
            )
        # Reuse every V1 semantic/manifest/cutoff invariant with a synthetic
        # historical envelope.  Only the additive V2 path custody and final
        # receipt hash are validated below.
        base_names = tuple(
            value.name for value in dataclass_fields(ContinuousBranchMaterializationReceiptV1)
        )
        base_payload = {
            name: getattr(self, name)
            for name in base_names
            if name != "receipt_sha256"
        }
        base_payload["schema_version"] = ContinuousBranchMaterializationReceiptV1.SCHEMA_VERSION
        ContinuousBranchMaterializationReceiptV1(
            **base_payload,
            receipt_sha256=canonical_sha256(to_primitive(base_payload)),
        )
        expected_parent = f"{self.world_id}/{self.parent_branch_id}"
        expected_child = f"{self.world_id}/{self.child_branch_id}"
        if (
            self.path_plan.parent_branch_relative_path != expected_parent
            or self.path_plan.child_branch_relative_path != expected_child
            or self.path_plan.receipt_relative_path
            != f"BRANCH_MATERIALIZATION/{self.branch_cutoff_sha256}.json"
        ):
            raise ContractValidationError(
                "branch materialization path plan changed receipt scope"
            )
        payload = to_primitive(self)
        payload.pop("receipt_sha256")
        if self.receipt_sha256 != canonical_sha256(payload):
            raise ContractValidationError("branch materialization receipt changed")


class ContinuousWorldStore:
    """One repository-local shadow world with atomic directory promotion."""

    def __init__(
        self,
        runtime_root: Path,
        *,
        promotion_failpoint: Callable[[str], None] | None = None,
    ) -> None:
        if not runtime_root.is_absolute():
            raise ContractValidationError("continuous world runtime root must be absolute")
        self.runtime_root = lexical_absolute(runtime_root)
        self._lock = RLock()
        self._promotion_failpoint = promotion_failpoint

    def branch_root(self, world_id: str, branch_id: str) -> Path:
        return self.runtime_root / _slug(world_id, "world_id") / _slug(branch_id, "branch_id")

    def initialize(self, world_id: str, branch_id: str) -> Path:
        root = self.branch_root(world_id, branch_id)
        root.mkdir(parents=True, exist_ok=True)
        self.recover_pending_promotions(world_id, branch_id)
        active = root / "ACTIVE"
        for directory in _ACTIVE_DIRS:
            (active / directory).mkdir(parents=True, exist_ok=True)
        for directory in (
            "CANDIDATES",
            "DEBUG",
            "DERIVED/Scenes",
            "PLANNER_SESSION",
            "VALIDATOR_SESSION",
            "VALIDATOR_DIAGNOSTICS",
        ):
            (root / directory).mkdir(parents=True, exist_ok=True)
        index = active / "WORLD_INDEX.jsonl"
        if not index.exists():
            index.write_text("", encoding="utf-8", newline="\n")
        state = active / "WORLD_STATE.json"
        if not state.exists():
            self._write_json(
                state,
                {
                    "schema_version": "cera.continuous_world_state.v1",
                    "_cera_revision": 1,
                    "world_id": world_id,
                    "branch_id": branch_id,
                    "current_scene_id": "scene-001",
                    "accepted_turn_ids": [],
                },
            )
        return root

    def world_identity_sha256(self, world_id: str, branch_id: str) -> str:
        root = self.initialize(world_id, branch_id)
        return text_sha256(str(lexical_absolute(root)).casefold())

    def branch_directory_identity_sha256(
        self, world_id: str, branch_id: str
    ) -> str:
        """Hash the actual branch path without creating or normalizing the target."""

        return text_sha256(
            str(lexical_absolute(self.branch_root(world_id, branch_id))).casefold()
        )

    def _branch_materialization_path_plan(
        self,
        *,
        world_id: str,
        parent_branch_id: str,
        child_branch_id: str,
        staging_root: Path,
        branch_cutoff_sha256: str,
    ) -> ContinuousBranchMaterializationPathPlanV1:
        parent_relative = f"{world_id}/{parent_branch_id}"
        child_relative = f"{world_id}/{child_branch_id}"
        staging_relative = staging_root.relative_to(self.runtime_root).as_posix()
        receipt_relative = (
            f"BRANCH_MATERIALIZATION/{branch_cutoff_sha256}.json"
        )
        receipt_temporary_relative = receipt_relative + ".tmp"
        child_path = lexical_target(self.runtime_root, child_relative)
        staging_path = lexical_target(self.runtime_root, staging_relative)
        receipt_path = lexical_target(staging_root, receipt_relative)
        receipt_temporary_path = lexical_target(
            staging_root, receipt_temporary_relative
        )
        lengths = preflight_windows_legacy_paths(
            child_path,
            staging_path,
            receipt_path,
            receipt_temporary_path,
            label="continuous branch materialization",
        )
        ensure_parent_chain(staging_root, receipt_relative)
        parent_custody = capture_target_custody(
            self.runtime_root, parent_relative
        )
        child_custody = capture_target_custody(
            self.runtime_root, child_relative
        )
        staging_custody = capture_target_custody(
            self.runtime_root, staging_relative
        )
        receipt_custody = capture_target_custody(
            staging_root, receipt_relative
        )
        receipt_temporary_custody = capture_target_custody(
            staging_root, receipt_temporary_relative
        )
        parent_leaf = inspect_leaf(
            self.runtime_root, parent_relative, expected_kind="directory"
        )
        staging_leaf = inspect_leaf(
            self.runtime_root, staging_relative, expected_kind="directory"
        )
        if parent_leaf.identity is None or staging_leaf.identity is None:
            raise StateConflictError(
                "branch materialization directory identity is unavailable"
            )
        if inspect_leaf(
            self.runtime_root, child_relative, expected_kind="directory"
        ).identity is not None:
            raise StateConflictError("branch materialization child path is occupied")
        if inspect_leaf(staging_root, receipt_relative).identity is not None:
            raise StateConflictError(
                "branch materialization receipt path is occupied"
            )
        if inspect_leaf(
            staging_root, receipt_temporary_relative
        ).identity is not None:
            raise StateConflictError(
                "branch materialization receipt temporary path is occupied"
            )
        custodies = (
            parent_custody,
            child_custody,
            staging_custody,
            receipt_custody,
            receipt_temporary_custody,
        )
        generation = canonical_sha256(
            {
                "path_policy_sha256": (
                    CONTINUOUS_BRANCH_MATERIALIZATION_PATH_POLICY_SHA256
                ),
                "custody_sha256s": tuple(value.custody_sha256 for value in custodies),
                "parent_branch_directory_identity_sha256": (
                    parent_leaf.identity.object_identity_sha256
                ),
                "staging_directory_identity_sha256": (
                    staging_leaf.identity.object_identity_sha256
                ),
            }
        )
        payload = {
            "schema_version": ContinuousBranchMaterializationPathPlanV1.SCHEMA_VERSION,
            "path_policy_sha256": (
                CONTINUOUS_BRANCH_MATERIALIZATION_PATH_POLICY_SHA256
            ),
            "parent_branch_relative_path": parent_relative,
            "child_branch_relative_path": child_relative,
            "staging_relative_path": staging_relative,
            "receipt_relative_path": receipt_relative,
            "receipt_temporary_relative_path": receipt_temporary_relative,
            "child_resolved_path_characters": lengths[0],
            "staging_resolved_path_characters": lengths[1],
            "receipt_resolved_path_characters": lengths[2],
            "receipt_temporary_resolved_path_characters": lengths[3],
            "parent_branch_custody": parent_custody,
            "child_branch_custody": child_custody,
            "staging_custody": staging_custody,
            "receipt_custody": receipt_custody,
            "receipt_temporary_custody": receipt_temporary_custody,
            "parent_branch_directory_identity_sha256": (
                parent_leaf.identity.object_identity_sha256
            ),
            "staging_directory_identity_sha256": (
                staging_leaf.identity.object_identity_sha256
            ),
            "verification_generation_sha256": generation,
        }
        primitive = {
            **payload,
            "parent_branch_custody": to_primitive(parent_custody),
            "child_branch_custody": to_primitive(child_custody),
            "staging_custody": to_primitive(staging_custody),
            "receipt_custody": to_primitive(receipt_custody),
            "receipt_temporary_custody": to_primitive(
                receipt_temporary_custody
            ),
        }
        return ContinuousBranchMaterializationPathPlanV1(
            **payload,
            plan_sha256=canonical_sha256(primitive),
        )

    def materialize_branch_from_checkpoint(
        self,
        *,
        world_id: str,
        parent_branch_id: str,
        child_branch_id: str,
        accepted_checkpoint_turn_id: str,
        ordered_accepted_turn_ids: tuple[str, ...],
        accepted_ancestry_sha256: str,
        parent_provider_thread_sha256: str,
        parent_world_directory_identity_sha256: str,
        child_world_directory_identity_sha256: str,
        authority_policy_version: str,
        privacy_policy_version: str,
        protected_user_policy_version: str,
        session_policy_version: str,
        persistence_policy_sha256: str,
        inherited_summary_sources: tuple[ContinuousInheritedSummarySourceV1, ...] = (),
    ) -> ContinuousBranchMaterializationReceiptV2:
        """Atomically materialize one child branch before provider transport."""

        parent_root = self.branch_root(world_id, parent_branch_id)
        child_root = self.branch_root(world_id, child_branch_id)
        if parent_branch_id == child_branch_id:
            raise StateConflictError("branch materialization target is the parent")
        parent_relative = f"{_slug(world_id, 'world_id')}/{_slug(parent_branch_id, 'parent_branch_id')}"
        child_relative = f"{_slug(world_id, 'world_id')}/{_slug(child_branch_id, 'child_branch_id')}"
        parent_leaf = inspect_leaf(
            self.runtime_root, parent_relative, expected_kind="directory"
        )
        if parent_leaf.identity is None:
            raise StateConflictError("branch materialization parent is unavailable")
        parent_active_relative = parent_relative + "/ACTIVE"
        if inspect_leaf(
            self.runtime_root, parent_active_relative, expected_kind="directory"
        ).identity is None:
            raise StateConflictError("branch materialization parent is unavailable")
        actual_parent_identity = self.branch_directory_identity_sha256(
            world_id, parent_branch_id
        )
        actual_child_identity = self.branch_directory_identity_sha256(
            world_id, child_branch_id
        )
        if (
            parent_world_directory_identity_sha256 != actual_parent_identity
            or child_world_directory_identity_sha256 != actual_child_identity
        ):
            raise StateConflictError(
                "branch materialization directory identity does not match physical custody"
            )
        if inspect_leaf(
            self.runtime_root, child_relative, expected_kind="directory"
        ).identity is not None:
            raise StateConflictError(
                "branch materialization requires a previously nonexistent child"
            )
        if (
            not ordered_accepted_turn_ids
            or ordered_accepted_turn_ids[-1] != accepted_checkpoint_turn_id
        ):
            raise StateConflictError("branch materialization checkpoint order changed")
        _preflight_branch_materialization_receipt_paths(child_root)

        staging_root: Path | None = None
        staging_relative: str | None = None
        staging_directory_identity_sha256: str | None = None
        staging_lock_context = None
        path_plan: ContinuousBranchMaterializationPathPlanV1 | None = None
        with self._lock, ExitStack() as path_locks:
            path_locks.enter_context(
                locked_directory_chain(
                    self.runtime_root,
                    _slug(world_id, "world_id"),
                    create_missing=False,
                )
            )
            if inspect_leaf(
                self.runtime_root, child_relative, expected_kind="directory"
            ).identity is not None:
                raise StateConflictError(
                    "branch materialization child appeared during validation"
                )
            parent_active = parent_root / "ACTIVE"
            parent_no_follow_tree_sha256 = validate_tree_no_follow(
                self.runtime_root, parent_relative
            )
            parent_state_path = parent_active / "WORLD_STATE.json"
            if not parent_state_path.is_file():
                raise StateConflictError("branch materialization lacks parent WORLD_STATE")
            parent_state_text = parent_state_path.read_text(encoding="utf-8")
            parent_state = json.loads(parent_state_text)
            if (
                not isinstance(parent_state, dict)
                or parent_state.get("world_id") != world_id
                or parent_state.get("branch_id") != parent_branch_id
                or tuple(parent_state.get("accepted_turn_ids", ()))
                != ordered_accepted_turn_ids
            ):
                raise StateConflictError(
                    "branch materialization parent state changed branch or cutoff"
                )
            world_state_revision = parent_state.get("_cera_revision")
            current_scene_id = parent_state.get("current_scene_id")
            if (
                type(world_state_revision) is not int
                or world_state_revision < 1
                or not isinstance(current_scene_id, str)
                or not current_scene_id.strip()
            ):
                raise ContractValidationError(
                    "branch materialization parent WORLD_STATE is invalid"
                )
            parent_manifest = self._active_manifest_for_path(parent_active)
            parent_tree_sha256 = self.tree_sha256(parent_active)
            parent_world_state_sha256 = hashlib.sha256(
                parent_state_path.read_bytes()
            ).hexdigest()
            branch_cutoff_sha256 = canonical_sha256(
                {
                    "world_id": world_id,
                    "parent_branch_id": parent_branch_id,
                    "child_branch_id": child_branch_id,
                    "accepted_checkpoint_turn_id": accepted_checkpoint_turn_id,
                    "ordered_accepted_turn_ids": ordered_accepted_turn_ids,
                    "accepted_ancestry_sha256": accepted_ancestry_sha256,
                    "parent_provider_thread_sha256": parent_provider_thread_sha256,
                    "parent_active_tree_sha256": parent_tree_sha256,
                    "parent_world_state_sha256": parent_world_state_sha256,
                }
            )
            try:
                staging_root = lexical_absolute(
                    Path(mkdtemp(prefix=".m-", dir=child_root.parent))
                )
                staging_relative = staging_root.relative_to(
                    self.runtime_root
                ).as_posix()
                staging_leaf = inspect_leaf(
                    self.runtime_root,
                    staging_relative,
                    expected_kind="directory",
                )
                if staging_leaf.identity is None:
                    raise StateConflictError(
                        "branch materialization staging identity is unavailable"
                    )
                staging_directory_identity_sha256 = (
                    staging_leaf.identity.object_identity_sha256
                )
                _preflight_branch_materialization_receipt_paths(staging_root)
                path_plan = self._branch_materialization_path_plan(
                    world_id=world_id,
                    parent_branch_id=parent_branch_id,
                    child_branch_id=child_branch_id,
                    staging_root=staging_root,
                    branch_cutoff_sha256=branch_cutoff_sha256,
                )
                staging_lock_context = locked_directory_chain(
                    self.runtime_root,
                    staging_relative,
                    create_missing=False,
                )
                staging_lock_context.__enter__()
                child_active = staging_root / "ACTIVE"
                shutil.copytree(parent_active, child_active, symlinks=True)
                child_state_path = child_active / "WORLD_STATE.json"
                child_state = json.loads(child_state_path.read_text(encoding="utf-8"))
                child_state["branch_id"] = child_branch_id
                self._write_json(child_state_path, child_state)
                self._rebuild_index(child_active)
                for directory in (
                    "CANDIDATES",
                    "DEBUG",
                    "DERIVED/Scenes",
                    "PLANNER_SESSION",
                    "VALIDATOR_SESSION",
                    "VALIDATOR_DIAGNOSTICS",
                ):
                    (staging_root / directory).mkdir(parents=True, exist_ok=True)
                for turn_id in ordered_accepted_turn_ids:
                    source_receipt = (
                        parent_root
                        / "CANDIDATES"
                        / _slug(turn_id, "turn_id")
                        / "PROMOTION_RECEIPT.json"
                    )
                    if not source_receipt.is_file():
                        raise StateConflictError(
                            "branch materialization accepted checkpoint artifact is absent"
                        )
                    target_receipt = (
                        staging_root
                        / "CANDIDATES"
                        / turn_id
                        / "PROMOTION_RECEIPT.json"
                    )
                    target_receipt.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source_receipt, target_receipt)
                child_manifest = self._active_manifest_for_path(child_active)
                child_tree_sha256 = self.tree_sha256(child_active)
                child_world_state_sha256 = hashlib.sha256(
                    child_state_path.read_bytes()
                ).hexdigest()
                self._validate_inherited_summary_sources(
                    parent_root=parent_root,
                    child_root=staging_root,
                    sources=inherited_summary_sources,
                )
                if validate_tree_no_follow(
                    self.runtime_root, parent_relative
                ) != parent_no_follow_tree_sha256:
                    raise StateConflictError(
                        "branch materialization parent path identity changed during copy"
                    )
                validate_tree_no_follow(
                    self.runtime_root, staging_relative
                )
                payload = {
                    "schema_version": ContinuousBranchMaterializationReceiptV2.SCHEMA_VERSION,
                    "world_id": world_id,
                    "parent_branch_id": parent_branch_id,
                    "child_branch_id": child_branch_id,
                    "accepted_checkpoint_turn_id": accepted_checkpoint_turn_id,
                    "ordered_accepted_turn_ids": ordered_accepted_turn_ids,
                    "accepted_ancestry_sha256": accepted_ancestry_sha256,
                    "branch_cutoff_sha256": branch_cutoff_sha256,
                    "parent_provider_thread_sha256": parent_provider_thread_sha256,
                    "parent_world_directory_identity_sha256": actual_parent_identity,
                    "child_world_directory_identity_sha256": actual_child_identity,
                    "parent_active_tree_sha256": parent_tree_sha256,
                    "child_initial_active_tree_sha256": child_tree_sha256,
                    "parent_active_manifest": parent_manifest,
                    "child_initial_active_manifest": child_manifest,
                    "parent_accepted_checkpoint_manifest": (
                        self._accepted_checkpoint_manifest(
                            parent_root, ordered_accepted_turn_ids
                        )
                    ),
                    "child_accepted_checkpoint_manifest": (
                        self._accepted_checkpoint_manifest(
                            staging_root, ordered_accepted_turn_ids
                        )
                    ),
                    "parent_world_state_sha256": parent_world_state_sha256,
                    "child_world_state_sha256": child_world_state_sha256,
                    "world_state_revision": world_state_revision,
                    "current_scene_id": current_scene_id,
                    "accepted_head_turn_id": accepted_checkpoint_turn_id,
                    "authority_policy_version": authority_policy_version,
                    "privacy_policy_version": privacy_policy_version,
                    "protected_user_policy_version": protected_user_policy_version,
                    "session_policy_version": session_policy_version,
                    "persistence_policy_sha256": persistence_policy_sha256,
                    "inherited_summary_sources": tuple(
                        sorted(
                            inherited_summary_sources,
                            key=lambda value: (
                                value.character_id,
                                value.source_path_or_record_id,
                            ),
                        )
                    ),
                    "path_plan": path_plan,
                }
                primitive_payload = {
                    **payload,
                    "parent_active_manifest": to_primitive(parent_manifest),
                    "child_initial_active_manifest": to_primitive(child_manifest),
                    "parent_accepted_checkpoint_manifest": to_primitive(
                        payload["parent_accepted_checkpoint_manifest"]
                    ),
                    "child_accepted_checkpoint_manifest": to_primitive(
                        payload["child_accepted_checkpoint_manifest"]
                    ),
                    "inherited_summary_sources": to_primitive(
                        payload["inherited_summary_sources"]
                    ),
                    "path_plan": to_primitive(path_plan),
                }
                receipt = ContinuousBranchMaterializationReceiptV2(
                    **payload,
                    receipt_sha256=canonical_sha256(primitive_payload),
                )
                receipt_encoded = canonical_bytes(to_primitive(receipt)) + b"\n"
                with locked_directory_chain(
                    staging_root,
                    "BRANCH_MATERIALIZATION",
                    create_missing=False,
                ):
                    verify_target_custody(staging_root, path_plan.receipt_custody)
                    verify_target_custody(
                        staging_root, path_plan.receipt_temporary_custody
                    )
                    temporary_identity = safe_create_new_bytes(
                        staging_root,
                        path_plan.receipt_temporary_relative_path,
                        receipt_encoded,
                    )
                    safe_replace(
                        staging_root,
                        temporary_relative_path=(
                            path_plan.receipt_temporary_relative_path
                        ),
                        final_relative_path=path_plan.receipt_relative_path,
                        expected_temporary_identity=temporary_identity,
                    )
                validate_tree_no_follow(
                    self.runtime_root, staging_relative
                )
                if staging_lock_context is not None:
                    staging_lock_context.__exit__(None, None, None)
                    staging_lock_context = None
                self._failpoint("before_branch_materialization_replace")
                verify_target_custody(
                    self.runtime_root, path_plan.parent_branch_custody
                )
                verify_target_custody(
                    self.runtime_root, path_plan.child_branch_custody
                )
                verify_target_custody(
                    self.runtime_root, path_plan.staging_custody
                )
                if validate_tree_no_follow(
                    self.runtime_root, parent_relative
                ) != parent_no_follow_tree_sha256:
                    raise StateConflictError(
                        "branch materialization parent changed before replacement"
                    )
                validate_tree_no_follow(
                    self.runtime_root, path_plan.staging_relative_path
                )
                if inspect_leaf(
                    self.runtime_root,
                    path_plan.child_branch_relative_path,
                    expected_kind="directory",
                ).identity is not None:
                    raise StateConflictError(
                        "branch materialization child appeared before replacement"
                    )
                final_staging = inspect_leaf(
                    self.runtime_root,
                    path_plan.staging_relative_path,
                    expected_kind="directory",
                )
                if (
                    final_staging.identity is None
                    or final_staging.identity.object_identity_sha256
                    != path_plan.staging_directory_identity_sha256
                ):
                    raise StateConflictError(
                        "branch materialization staging identity changed"
                    )
                os.replace(staging_root, child_root)
                promoted_child = inspect_leaf(
                    self.runtime_root,
                    path_plan.child_branch_relative_path,
                    expected_kind="directory",
                )
                if (
                    promoted_child.identity is None
                    or promoted_child.identity.object_identity_sha256
                    != path_plan.staging_directory_identity_sha256
                ):
                    raise StateConflictError(
                        "branch materialization promoted identity changed"
                    )
                staging_root = None
            finally:
                if staging_lock_context is not None:
                    staging_lock_context.__exit__(None, None, None)
                    staging_lock_context = None
                if (
                    staging_root is not None
                    and staging_relative is not None
                    and staging_directory_identity_sha256 is not None
                ):
                    try:
                        remaining = inspect_leaf(
                            self.runtime_root,
                            staging_relative,
                            expected_kind="directory",
                        )
                        if (
                            remaining.identity is not None
                            and remaining.identity.object_identity_sha256
                            == staging_directory_identity_sha256
                        ):
                            shutil.rmtree(staging_root)
                    except StateConflictError:
                        pass
        self.validate_branch_materialization(receipt)
        return receipt

    def validate_branch_materialization(
        self,
        receipt: ContinuousBranchMaterializationReceiptV1
        | ContinuousBranchMaterializationReceiptV2,
    ) -> Path:
        """Revalidate immutable parent/child bytes without creating either branch."""

        parent_root = self.branch_root(receipt.world_id, receipt.parent_branch_id)
        child_root = self.branch_root(receipt.world_id, receipt.child_branch_id)
        parent_relative = f"{receipt.world_id}/{receipt.parent_branch_id}"
        child_relative = f"{receipt.world_id}/{receipt.child_branch_id}"
        parent_leaf = inspect_leaf(
            self.runtime_root, parent_relative, expected_kind="directory"
        )
        child_leaf = inspect_leaf(
            self.runtime_root, child_relative, expected_kind="directory"
        )
        if parent_leaf.identity is None or child_leaf.identity is None:
            raise StateConflictError("branch materialization scope is unavailable")
        validate_tree_no_follow(self.runtime_root, parent_relative)
        validate_tree_no_follow(self.runtime_root, child_relative)
        if (
            self.branch_directory_identity_sha256(
                receipt.world_id, receipt.parent_branch_id
            )
            != receipt.parent_world_directory_identity_sha256
            or self.branch_directory_identity_sha256(
                receipt.world_id, receipt.child_branch_id
            )
            != receipt.child_world_directory_identity_sha256
        ):
            raise StateConflictError("branch materialization physical directory changed")
        if isinstance(receipt, ContinuousBranchMaterializationReceiptV2):
            plan = receipt.path_plan
            verify_target_custody(self.runtime_root, plan.parent_branch_custody)
            verify_target_custody(self.runtime_root, plan.child_branch_custody)
            if (
                parent_leaf.identity.object_identity_sha256
                != plan.parent_branch_directory_identity_sha256
                or child_leaf.identity.object_identity_sha256
                != plan.staging_directory_identity_sha256
            ):
                raise StateConflictError(
                    "branch materialization directory path identity changed"
                )
            verify_target_custody(child_root, plan.receipt_custody)
            verify_target_custody(child_root, plan.receipt_temporary_custody)
            receipt_relative = plan.receipt_relative_path
            if inspect_leaf(
                child_root, plan.receipt_temporary_relative_path
            ).identity is not None:
                raise StateConflictError(
                    "branch materialization receipt temporary path is occupied"
                )
        else:
            receipt_relative = (
                "BRANCH_MATERIALIZATION/" + receipt.receipt_sha256 + ".json"
            )
        encoded_receipt, _receipt_identity = safe_read_bytes(
            child_root, receipt_relative
        )
        receipt_path = lexical_target(child_root, receipt_relative)
        if json.loads(encoded_receipt.decode("utf-8")) != to_primitive(receipt):
            raise StateConflictError("branch materialization receipt is absent or changed")
        parent_active = parent_root / "ACTIVE"
        child_active = child_root / "ACTIVE"
        if (
            self.tree_sha256(parent_active) != receipt.parent_active_tree_sha256
            or self.tree_sha256(child_active)
            != receipt.child_initial_active_tree_sha256
            or self._active_manifest_for_path(parent_active)
            != receipt.parent_active_manifest
            or self._active_manifest_for_path(child_active)
            != receipt.child_initial_active_manifest
            or self._accepted_checkpoint_manifest(
                parent_root, receipt.ordered_accepted_turn_ids
            )
            != receipt.parent_accepted_checkpoint_manifest
            or self._accepted_checkpoint_manifest(
                child_root, receipt.ordered_accepted_turn_ids
            )
            != receipt.child_accepted_checkpoint_manifest
            or receipt.parent_accepted_checkpoint_manifest
            != receipt.child_accepted_checkpoint_manifest
        ):
            raise StateConflictError("branch materialization ACTIVE snapshot changed")
        parent_state_path = parent_active / "WORLD_STATE.json"
        child_state_path = child_active / "WORLD_STATE.json"
        if (
            hashlib.sha256(parent_state_path.read_bytes()).hexdigest()
            != receipt.parent_world_state_sha256
            or hashlib.sha256(child_state_path.read_bytes()).hexdigest()
            != receipt.child_world_state_sha256
        ):
            raise StateConflictError("branch materialization WORLD_STATE changed")
        parent_state = json.loads(parent_state_path.read_text(encoding="utf-8"))
        child_state = json.loads(child_state_path.read_text(encoding="utf-8"))
        if (
            parent_state.get("world_id") != receipt.world_id
            or child_state.get("world_id") != receipt.world_id
            or parent_state.get("branch_id") != receipt.parent_branch_id
            or child_state.get("branch_id") != receipt.child_branch_id
            or parent_state.get("_cera_revision") != receipt.world_state_revision
            or child_state.get("_cera_revision") != receipt.world_state_revision
            or parent_state.get("current_scene_id") != receipt.current_scene_id
            or child_state.get("current_scene_id") != receipt.current_scene_id
            or tuple(parent_state.get("accepted_turn_ids", ()))
            != receipt.ordered_accepted_turn_ids
            or tuple(child_state.get("accepted_turn_ids", ()))
            != receipt.ordered_accepted_turn_ids
        ):
            raise StateConflictError("branch materialization semantic state changed")
        expected_child_state = copy.deepcopy(parent_state)
        expected_child_state["branch_id"] = receipt.child_branch_id
        if child_state != expected_child_state:
            raise StateConflictError(
                "branch materialization child state diverged from its parent cutoff"
            )
        self._validate_materialized_active_equivalence(
            parent_active=parent_active,
            child_active=child_active,
        )
        self._validate_inherited_summary_sources(
            parent_root=parent_root,
            child_root=child_root,
            sources=receipt.inherited_summary_sources,
        )
        return receipt_path

    def _active_manifest_for_path(
        self, active_root: Path
    ) -> tuple[ContinuousActiveManifestEntryV1, ...]:
        entries: list[ContinuousActiveManifestEntryV1] = []
        for path in sorted(value for value in active_root.rglob("*") if value.is_file()):
            relative = path.relative_to(active_root).as_posix()
            if relative == "WORLD_STATE.json":
                category = "world_state"
            elif relative == "WORLD_INDEX.jsonl":
                category = "world_index"
            else:
                category = relative.split("/", 1)[0].casefold()
            revision = None
            raw = path.read_bytes()
            if path.suffix.casefold() == ".json":
                value = json.loads(raw.decode("utf-8"))
                if isinstance(value, dict):
                    revision = value.get("_cera_revision")
            entries.append(
                ContinuousActiveManifestEntryV1(
                    relative_path=relative,
                    record_category=category,
                    content_sha256=hashlib.sha256(raw).hexdigest(),
                    size_bytes=len(raw),
                    revision=revision,
                )
            )
        return tuple(entries)

    def _accepted_checkpoint_manifest(
        self, branch_root: Path, accepted_turn_ids: tuple[str, ...]
    ) -> tuple[ContinuousActiveManifestEntryV1, ...]:
        entries = []
        for turn_id in accepted_turn_ids:
            path = (
                branch_root
                / "CANDIDATES"
                / _slug(turn_id, "turn_id")
                / "PROMOTION_RECEIPT.json"
            )
            if not path.is_file():
                raise StateConflictError(
                    "branch materialization checkpoint artifact is unavailable"
                )
            raw = path.read_bytes()
            payload = json.loads(raw.decode("utf-8"))
            revision = payload.get("_cera_revision") if isinstance(payload, dict) else None
            entries.append(
                ContinuousActiveManifestEntryV1(
                    relative_path=path.relative_to(branch_root).as_posix(),
                    record_category="accepted_checkpoint_artifact",
                    content_sha256=hashlib.sha256(raw).hexdigest(),
                    size_bytes=len(raw),
                    revision=revision,
                )
            )
        return tuple(sorted(entries, key=lambda value: value.relative_path))

    def _validate_materialized_active_equivalence(
        self, *, parent_active: Path, child_active: Path
    ) -> None:
        parent_paths = {
            value.relative_to(parent_active).as_posix()
            for value in parent_active.rglob("*")
            if value.is_file()
        }
        child_paths = {
            value.relative_to(child_active).as_posix()
            for value in child_active.rglob("*")
            if value.is_file()
        }
        if parent_paths != child_paths:
            raise StateConflictError("branch materialization file set diverged")
        for relative in sorted(parent_paths - {"WORLD_STATE.json", "WORLD_INDEX.jsonl"}):
            if (parent_active / relative).read_bytes() != (child_active / relative).read_bytes():
                raise StateConflictError(
                    "branch materialization authoritative record diverged"
                )
        expected_index_root = Path(
            mkdtemp(prefix=".branch-index-check-", dir=child_active.parent)
        )
        try:
            shutil.copytree(parent_active, expected_index_root / "ACTIVE")
            expected_state_path = expected_index_root / "ACTIVE" / "WORLD_STATE.json"
            expected_state = json.loads(expected_state_path.read_text(encoding="utf-8"))
            expected_state["branch_id"] = json.loads(
                (child_active / "WORLD_STATE.json").read_text(encoding="utf-8")
            )["branch_id"]
            self._write_json(expected_state_path, expected_state)
            self._rebuild_index(expected_index_root / "ACTIVE")
            if (
                (expected_index_root / "ACTIVE" / "WORLD_INDEX.jsonl").read_bytes()
                != (child_active / "WORLD_INDEX.jsonl").read_bytes()
            ):
                raise StateConflictError(
                    "branch materialization index is not the deterministic child index"
                )
        finally:
            shutil.rmtree(expected_index_root)

    @staticmethod
    def _validate_inherited_summary_sources(
        *,
        parent_root: Path,
        child_root: Path,
        sources: tuple[ContinuousInheritedSummarySourceV1, ...],
    ) -> None:
        from .evidence import build_character_summary_envelope

        for source in sources:
            for root in (parent_root, child_root):
                envelope = build_character_summary_envelope(
                    branch_root=root,
                    source_path=source.source_path_or_record_id,
                    character_id=source.character_id,
                )
                if (
                    envelope.source_revision != source.source_revision
                    or envelope.source_sha256 != source.source_sha256
                    or envelope.source_authority_classification
                    != source.source_authority_classification
                    or envelope.envelope_sha256 != source.envelope_sha256
                ):
                    raise StateConflictError(
                        "branch materialization inherited summary source changed"
                    )

    def create_candidate(self, world_id: str, branch_id: str, turn_id: str) -> CandidateWorldViewV1:
        _slug(turn_id, "turn_id")
        root = self.initialize(world_id, branch_id)
        candidate_root = root / "CANDIDATES" / turn_id
        if candidate_root.exists():
            raise StateConflictError("continuous candidate already exists")
        view = candidate_root / "ACTIVE_VIEW"
        shutil.copytree(root / "ACTIVE", view)
        return CandidateWorldViewV1(
            turn_id=turn_id,
            root=candidate_root,
            source_active_sha256=self.tree_sha256(root / "ACTIVE"),
        )

    def record_candidate_package(
        self,
        world_id: str,
        branch_id: str,
        view: CandidateWorldViewV1,
        package: ValidatorFinalizationPackageV1,
        *,
        candidate_sha256: str,
        authority_context_sha256: str,
    ) -> Path:
        root = self.branch_root(world_id, branch_id)
        if view.root.parent != root / "CANDIDATES":
            raise StateConflictError("candidate view belongs to another branch")
        path = view.root / "VALIDATOR_PACKAGE.json"
        self._write_json(path, to_primitive(package))
        self._write_json(
            view.root / "CANDIDATE_AUTHORITY.json",
            {
                "schema_version": "cera.continuous_candidate_authority.v1",
                "candidate_sha256": candidate_sha256,
                "authority_context_sha256": authority_context_sha256,
                "package_sha256": package.package_sha256,
            },
        )
        return path

    def apply_creator_action(
        self,
        *,
        world_id: str,
        branch_id: str,
        turn_id: str,
        action: CreatorReviewAction,
        package: ValidatorFinalizationPackageV1,
        candidate_sha256: str,
        authority_context_sha256: str,
        accepted_pair: AcceptedTurnPairV1 | None = None,
    ) -> WorldPromotionReceiptV1:
        root = self.initialize(world_id, branch_id)
        candidate_root = root / "CANDIDATES" / _slug(turn_id, "turn_id")
        view = candidate_root / "ACTIVE_VIEW"
        if not view.is_dir():
            raise StateConflictError("continuous candidate view is unavailable")
        before = self.tree_sha256(root / "ACTIVE")
        authority_path = candidate_root / "CANDIDATE_AUTHORITY.json"
        package_path = candidate_root / "VALIDATOR_PACKAGE.json"
        if not authority_path.is_file() or not package_path.is_file():
            raise StateConflictError("continuous candidate authority is unavailable")
        authority = json.loads(authority_path.read_text(encoding="utf-8"))
        expected_authority = {
            "schema_version": "cera.continuous_candidate_authority.v1",
            "candidate_sha256": authority.get("candidate_sha256"),
            "authority_context_sha256": authority.get("authority_context_sha256"),
            "package_sha256": package.package_sha256,
        }
        if (
            authority != expected_authority
            or authority["candidate_sha256"] != candidate_sha256
            or authority["authority_context_sha256"] != authority_context_sha256
            or json.loads(package_path.read_text(encoding="utf-8")) != to_primitive(package)
        ):
            raise StateConflictError("continuous candidate authority changed")
        if action not in {CreatorReviewAction.ACCEPT, CreatorReviewAction.FALSE_POSITIVE}:
            receipt = WorldPromotionReceiptV1(
                schema_version=WorldPromotionReceiptV1.SCHEMA_VERSION,
                world_id=world_id,
                branch_id=branch_id,
                turn_id=turn_id,
                creator_action=action,
                package_sha256=package.package_sha256,
                candidate_sha256=candidate_sha256,
                authority_context_sha256=authority_context_sha256,
                active_before_sha256=before,
                active_after_sha256=before,
                changed_files=(),
                created_fields=(),
                accepted=False,
                planner_append_required=False,
                recorded_at_utc=_utc_now(),
            )
            self._write_json(candidate_root / "CREATOR_ACTION.json", to_primitive(receipt))
            self._append_timeline(root, receipt)
            return receipt
        if not package.permits_disposable_acceptance(action):
            raise StateConflictError("Validator package does not permit acceptance")
        if package.world_id != world_id or package.branch_id != branch_id:
            raise StateConflictError("Validator package belongs to another world or branch")
        if accepted_pair is None:
            raise StateConflictError(
                "accepted creator action requires the exact accepted turn pair"
            )
        if (
            package.complete_final_sequence is None
            or accepted_pair.complete_final_sequence.sequence_sha256
            != package.complete_final_sequence.sequence_sha256
            or accepted_pair.accepted_turn_id
            != package.complete_final_sequence.accepted_turn_id
        ):
            raise StateConflictError("accepted exact pair changed Validator final sequence")
        if self.tree_sha256(root / "ACTIVE") != self.tree_sha256(view):
            raise StateConflictError("candidate base no longer matches ACTIVE")
        changed = self._apply_operations(view, package)
        self._save_event(view, package)
        self._write_json(
            view
            / "Events"
            / _identity_filename(
                accepted_pair.accepted_turn_id, ".accepted_pair.json"
            ),
            to_primitive(accepted_pair),
        )
        accepted_pair_path = (
            view
            / "Events"
            / _identity_filename(
                accepted_pair.accepted_turn_id, ".accepted_pair.json"
            )
        )
        accepted_event_path = (
            view
            / "Events"
            / _identity_filename(package.event_record.event_id, ".json")
        )
        self._rebuild_index(view)
        self._increment_world_state(view, turn_id)
        after = self.tree_sha256(view)
        recorded_at = _utc_now()
        receipt = WorldPromotionReceiptV1(
            schema_version=WorldPromotionReceiptV1.SCHEMA_VERSION,
            world_id=world_id,
            branch_id=branch_id,
            turn_id=turn_id,
            creator_action=action,
            package_sha256=package.package_sha256,
            candidate_sha256=candidate_sha256,
            authority_context_sha256=authority_context_sha256,
            active_before_sha256=before,
            active_after_sha256=after,
            changed_files=tuple(sorted(changed)),
            created_fields=tuple(
                sorted(f"{value.target_file}{value.field_path}" for value in package.created_field_log)
            ),
            accepted=True,
            planner_append_required=True,
            recorded_at_utc=recorded_at,
        )
        diagnostic_payload = (
            self._false_positive_diagnostic_payload(
                turn_id=turn_id,
                package=package,
                recorded_at=recorded_at,
            )
            if action is CreatorReviewAction.FALSE_POSITIVE
            else None
        )
        transaction_root = root / f".acceptance-{_slug(turn_id, 'turn_id')}"
        transaction_relative = transaction_root.relative_to(
            self.runtime_root
        ).as_posix()
        if inspect_leaf(
            self.runtime_root,
            transaction_relative,
            expected_kind="directory",
        ).identity is not None:
            raise StateConflictError("creator acceptance journal already exists")
        ensure_parent_chain(
            self.runtime_root, transaction_relative + "/JOURNAL.json"
        )
        journal_base = {
            "schema_version": "cera.continuous_acceptance_journal.v6",
            "world_id": world_id,
            "branch_id": branch_id,
            "turn_id": turn_id,
            "creator_action": action.value,
            "package_sha256": package.package_sha256,
            "candidate_sha256": candidate_sha256,
            "authority_context_sha256": authority_context_sha256,
            "accepted_pair_sha256": text_sha256(
                accepted_pair_path.read_text(encoding="utf-8")
            ),
            "accepted_pair_relative_path": accepted_pair_path.relative_to(view).as_posix(),
            "accepted_event_sha256": text_sha256(
                accepted_event_path.read_text(encoding="utf-8")
            ),
            "accepted_event_relative_path": accepted_event_path.relative_to(view).as_posix(),
            "prior_sha256": before,
            "prepared_sha256": after,
            "promotion_receipt_payload": to_primitive(receipt),
            "false_positive_diagnostic_payload": diagnostic_payload,
            "planner_ledger_state": "pending",
            "model_injection_state": "pending",
            "planner_snapshot_state": "pending",
            "stable_reference_state": "pending",
        }
        self._write_json(
            transaction_root / "JOURNAL.json",
            {**journal_base, "state": "acceptance_prepared"},
        )
        self._failpoint("acceptance_journal_created")
        with self._lock:
            if self.tree_sha256(root / "ACTIVE") != before:
                raise StateConflictError("ACTIVE changed during candidate validation")
            self._promote_directory(
                root,
                view,
                turn_id,
                transaction_root=transaction_root,
                journal_base=journal_base,
            )
        self._finish_local_acceptance(root, transaction_root)
        return receipt

    def save_scene_summary(
        self,
        world_id: str,
        branch_id: str,
        summary: SceneSummaryV1,
    ) -> Path:
        root = self.initialize(world_id, branch_id)
        path = root / "DERIVED" / "Scenes" / _identity_filename(
            summary.completed_scene_id, ".summary.json"
        )
        pairs = self.accepted_turn_pairs(
            world_id,
            branch_id,
            summary.accepted_turn_ids,
        )
        pair_hashes = tuple(canonical_sha256(to_primitive(value)) for value in pairs)
        event_hashes_by_turn = self._accepted_event_hashes_by_turn(
            root / "ACTIVE", summary.accepted_turn_ids
        )
        provenance = tuple(
            SceneSummaryTurnProvenanceV2(
                accepted_turn_id=turn_id,
                accepted_pair_sha256=pair_sha256,
                accepted_event_sha256=event_hashes_by_turn.get(turn_id, ()),
                authority_basis="exact_accepted_pair",
            )
            for turn_id, pair_sha256 in zip(
                summary.accepted_turn_ids, pair_hashes, strict=True
            )
        )
        revision = 1
        if path.exists():
            prior = json.loads(path.read_text(encoding="utf-8"))
            prior_summary = prior.get("summary") if isinstance(prior, dict) else None
            prior_sources = tuple(prior.get("source_turn_provenance", ())) if isinstance(prior, dict) else ()
            if prior_summary == to_primitive(summary) and prior_sources == tuple(
                to_primitive(value) for value in provenance
            ):
                return path
            revision = int(prior.get("summary_revision", 0)) + 1
        regeneration_identity = canonical_sha256(
            {
                "completed_scene_id": summary.completed_scene_id,
                "source_turn_provenance": provenance,
                "summary": summary,
            }
        )
        derived = SceneSummaryDerivedViewV1(
            schema_version=SceneSummaryDerivedViewV1.SCHEMA_VERSION,
            authority_classification="non_authoritative_derived_view",
            summary_revision=revision,
            regeneration_identity_sha256=regeneration_identity,
            source_accepted_turn_ids=summary.accepted_turn_ids,
            source_turn_provenance=provenance,
            summary=summary,
        )
        self._write_json(path, to_primitive(derived))
        return path

    def accepted_turn_pairs(
        self,
        world_id: str,
        branch_id: str,
        accepted_turn_ids: tuple[str, ...],
    ) -> tuple[AcceptedTurnPairV1, ...]:
        root = self.initialize(world_id, branch_id)
        result = []
        for turn_id in accepted_turn_ids:
            path = (
                root
                / "ACTIVE"
                / "Events"
                / _identity_filename(turn_id, ".accepted_pair.json")
            )
            if not path.is_file():
                raise StateConflictError("accepted scene turn has no exact pair")
            from cera.schema import from_mapping

            result.append(from_mapping(AcceptedTurnPairV1, json.loads(path.read_text(encoding="utf-8"))))
        return tuple(result)

    def write_accepted_pair(
        self,
        world_id: str,
        branch_id: str,
        pair: AcceptedTurnPairV1,
    ) -> Path:
        root = self.initialize(world_id, branch_id)
        path = (
            root
            / "ACTIVE"
            / "Events"
            / _identity_filename(pair.accepted_turn_id, ".accepted_pair.json")
        )
        if path.exists() and canonical_sha256(json.loads(path.read_text(encoding="utf-8"))) != canonical_sha256(to_primitive(pair)):
            raise StateConflictError("accepted pair changed after publication")
        self._write_json(path, to_primitive(pair))
        return path

    def accepted_final_envelope(
        self, world_id: str, branch_id: str, turn_id: str
    ) -> AcceptedFinalSequenceEnvelopeV1:
        pair = self.accepted_turn_pairs(world_id, branch_id, (turn_id,))[0]
        root = self.initialize(world_id, branch_id)
        receipt_path = root / "CANDIDATES" / _slug(turn_id, "turn_id") / "PROMOTION_RECEIPT.json"
        if not receipt_path.is_file():
            raise StateConflictError("accepted turn has no promotion receipt")
        from cera.schema import from_mapping

        receipt = from_mapping(
            WorldPromotionReceiptV1,
            json.loads(receipt_path.read_text(encoding="utf-8")),
        )
        if not receipt.accepted or not receipt.planner_append_required:
            raise StateConflictError("accepted turn promotion receipt is not synchronizable")
        return AcceptedFinalSequenceEnvelopeV1(
            schema_version=AcceptedFinalSequenceEnvelopeV1.SCHEMA_VERSION,
            accepted_turn_id=turn_id,
            user_message=pair.user_message,
            complete_final_sequence=pair.complete_final_sequence,
            acceptance_receipt_sha256=receipt.receipt_sha256,
        )

    def tree_sha256(self, root: Path) -> str:
        entries = []
        if root.exists():
            for path in sorted(value for value in root.rglob("*") if value.is_file()):
                relative = path.relative_to(root).as_posix()
                entries.append((relative, text_sha256(path.read_text(encoding="utf-8"))))
        return canonical_sha256(entries)

    def active_manifest(
        self, world_id: str, branch_id: str
    ) -> tuple[dict[str, Any], ...]:
        """Return the bounded revision/hash manifest visible to the Validator."""

        root = self.initialize(world_id, branch_id) / "ACTIVE"
        rows: list[dict[str, Any]] = []
        for path in sorted(value for value in root.rglob("*.json") if value.is_file()):
            relative = path.relative_to(root).as_posix()
            payload = json.loads(path.read_text(encoding="utf-8"))
            rows.append(
                {
                    "path": relative,
                    "revision": (
                        payload.get("_cera_revision")
                        if isinstance(payload, dict)
                        else None
                    ),
                    "content_sha256": text_sha256(path.read_text(encoding="utf-8")),
                }
            )
        return tuple(rows)

    def seed_active_json(
        self,
        world_id: str,
        branch_id: str,
        relative_path: str,
        payload: dict[str, Any],
    ) -> Path:
        """Install a disposable/initial authority record before any accepted turn."""

        root = self.initialize(world_id, branch_id)
        state = json.loads((root / "ACTIVE" / "WORLD_STATE.json").read_text(encoding="utf-8"))
        if state.get("accepted_turn_ids"):
            raise StateConflictError("continuous world cannot seed after an accepted turn")
        # Reuse the same path/category rules as Validator create-file operations.
        from .contracts import WorldEditOperationV1

        WorldEditOperationV1(
            operation_key="genesis_seed",
            target_file=relative_path,
            expected_file_revision=None,
            operation=WorldEditOperationKind.CREATE_FILE,
            field_path="/",
            value=payload,
            reason="Creator-authorized disposable Genesis seed.",
            source_final_sequence_item="genesis_seed",
        )
        target = root / "ACTIVE" / relative_path
        if target.exists():
            raise StateConflictError("continuous Genesis seed target already exists")
        self._write_json(target, {"_cera_revision": 1, **payload})
        self._rebuild_index(root / "ACTIVE")
        return target

    def _record_false_positive_diagnostic(
        self,
        *,
        root: Path,
        turn_id: str,
        package: ValidatorFinalizationPackageV1,
    ) -> Path:
        if package.creator_review is None:
            raise StateConflictError("false-positive action lacks Validator assessment")
        path = root / "VALIDATOR_DIAGNOSTICS" / _identity_filename(
            turn_id, ".false_positive.json"
        )
        payload = self._false_positive_diagnostic_payload(
            turn_id=turn_id,
            package=package,
            recorded_at=_utc_now(),
        )
        return self._write_false_positive_diagnostic(root, payload)

    def _false_positive_diagnostic_payload(
        self,
        *,
        turn_id: str,
        package: ValidatorFinalizationPackageV1,
        recorded_at: str,
    ) -> dict[str, Any]:
        if package.creator_review is None:
            raise StateConflictError("false-positive action lacks Validator assessment")
        return {
            "schema_version": "cera.continuous_validator_false_positive_diagnostic.v1",
            "authority_classification": "validator_owned_non_story_diagnostic",
            "turn_id": turn_id,
            "package_sha256": package.package_sha256,
            "assessment_sha256": package.creator_review.assessment_sha256,
            "original_severity": package.creator_review.severity.value,
            "reason_codes": package.creator_review.reason_codes,
            "creator_action": CreatorReviewAction.FALSE_POSITIVE.value,
            "creates_story_constraint": False,
            "recorded_at_utc": recorded_at,
        }

    def _write_false_positive_diagnostic(
        self, root: Path, payload: dict[str, Any]
    ) -> Path:
        turn_id = str(payload["turn_id"])
        path = root / "VALIDATOR_DIAGNOSTICS" / _identity_filename(
            turn_id, ".false_positive.json"
        )
        if path.exists():
            prior = json.loads(path.read_text(encoding="utf-8"))
            if prior != payload:
                raise StateConflictError("false-positive diagnostic changed")
            return path
        self._write_json(path, payload)
        return path

    def _accepted_event_hashes_by_turn(
        self,
        active_root: Path,
        accepted_turn_ids: tuple[str, ...],
    ) -> dict[str, tuple[str, ...]]:
        wanted = set(accepted_turn_ids)
        rows: list[tuple[str, str]] = []
        for path in sorted((active_root / "Events").glob("*.json")):
            if path.name.endswith(".accepted_pair.json"):
                continue
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, dict) and payload.get("accepted_turn_id") in wanted:
                rows.append(
                    (
                        str(payload["accepted_turn_id"]),
                        text_sha256(path.read_text(encoding="utf-8")),
                    )
                )
        result: dict[str, list[str]] = {turn_id: [] for turn_id in accepted_turn_ids}
        for turn_id, value in sorted(rows):
            result[turn_id].append(value)
        return {turn_id: tuple(values) for turn_id, values in result.items()}

    def _apply_operations(
        self, active_view: Path, package: ValidatorFinalizationPackageV1
    ) -> set[str]:
        changed: set[str] = set()
        loaded: dict[str, Any] = {}
        originals: dict[str, Any] = {}
        revisions: dict[str, int | None] = {}
        directive_by_key = {}
        if package.complete_final_sequence is not None:
            for item in package.complete_final_sequence.items:
                for scope in item.field_scopes:
                    for directive in scope.persistence_directives:
                        directive_by_key[directive.directive_key] = (directive, scope)
        directives_by_file: dict[str, list[Any]] = {}
        for operation in package.world_edit_operations:
            if operation.persistence_directive_key is None:
                raise ContractValidationError(
                    "continuous semantic edit lacks a typed persistence directive"
                )
            directive_entry = directive_by_key.get(operation.persistence_directive_key)
            if directive_entry is None:
                raise StateConflictError(
                    "continuous semantic edit cites an unknown persistence directive"
                )
            directive, scope = directive_entry
            if directive.persistence_policy_sha256 != PERSISTENCE_POLICY_SHA256:
                raise StateConflictError(
                    "continuous semantic edit uses a stale writable-path policy"
                )
            validate_persistence_field_path(
                directive.target_record_class,
                operation.field_path,
            )
            if directive.target_record_class is PersistenceRecordClass.RELATIONSHIP:
                subjects = set(directive.target_subject_ids)
                if len(subjects) != 2 or not subjects.issubset(
                    set(scope.roles.involved_ids)
                ):
                    raise StateConflictError(
                        "relationship persistence participants are not justified by the final field"
                    )
                if (
                    scope.visibility is FinalInformationVisibility.CHARACTER_PRIVATE
                    and scope.knowledge_owner_id not in subjects
                ):
                    raise StateConflictError(
                        "private relationship persistence changed owner scope"
                    )
            key = operation.target_file.replace("\\", "/")
            active_relative = lexical_absolute(active_view).relative_to(
                self.runtime_root
            ).as_posix()
            target_relative = active_relative + "/" + key
            target = lexical_target(self.runtime_root, target_relative)
            if key not in loaded:
                target_leaf = inspect_leaf(self.runtime_root, target_relative)
                if target_leaf.identity is not None:
                    if target.suffix.casefold() != ".json":
                        raise ContractValidationError("V1 semantic edits require JSON targets")
                    target_bytes, _target_identity = safe_read_bytes(
                        self.runtime_root, target_relative
                    )
                    loaded[key] = json.loads(target_bytes.decode("utf-8"))
                    revision = loaded[key].get("_cera_revision") if isinstance(loaded[key], dict) else None
                    revisions[key] = revision
                    originals[key] = copy.deepcopy(loaded[key])
                else:
                    loaded[key] = None
                    revisions[key] = None
                    originals[key] = None
            if revisions[key] != operation.expected_file_revision:
                raise StateConflictError("world edit file revision precondition failed")
            if operation.operation is WorldEditOperationKind.CREATE_FILE:
                if loaded[key] is not None:
                    raise StateConflictError("create_file target already exists")
                loaded[key] = operation.value
            else:
                if loaded[key] is None:
                    raise StateConflictError("world edit target file is absent")
                _apply_json_operation(loaded[key], operation)
            changed.add(key)
            directives_by_file.setdefault(key, []).append(directive)
        for key in sorted(changed):
            document = loaded[key]
            if isinstance(document, dict):
                prior = revisions[key] or 0
                document["_cera_revision"] = prior + 1
            file_directives = directives_by_file[key]
            first = file_directives[0]
            if any(
                (
                    value.target_record_class,
                    value.target_record_id,
                    value.target_subject_ids,
                    value.expected_file_revision,
                )
                != (
                    first.target_record_class,
                    first.target_record_id,
                    first.target_subject_ids,
                    first.expected_file_revision,
                )
                for value in file_directives[1:]
            ):
                raise StateConflictError(
                    "one candidate file has conflicting persistence authorities"
                )
            validate_post_edit_record(
                record_class=first.target_record_class,
                before=originals[key],
                after=document,
                expected_record_id=first.target_record_id,
                expected_subject_ids=first.target_subject_ids,
                expected_revision=first.expected_file_revision + 1,
            )
            self._write_json(active_view / key, document)
        return changed

    def _save_event(self, active_view: Path, package: ValidatorFinalizationPackageV1) -> None:
        assert package.event_record is not None
        path = (
            active_view
            / "Events"
            / _identity_filename(package.event_record.event_id, ".json")
        )
        if path.exists():
            raise StateConflictError("accepted event identity already exists")
        self._write_json(path, {"_cera_revision": 1, **to_primitive(package.event_record)})

    def _increment_world_state(self, active_view: Path, turn_id: str) -> None:
        path = active_view / "WORLD_STATE.json"
        state = json.loads(path.read_text(encoding="utf-8"))
        state["_cera_revision"] = int(state["_cera_revision"]) + 1
        if turn_id in state["accepted_turn_ids"]:
            raise StateConflictError("world state already contains accepted turn")
        state["accepted_turn_ids"].append(turn_id)
        self._write_json(path, state)

    def _rebuild_index(self, active_view: Path) -> None:
        rows = []
        for path in sorted(
            value
            for value in active_view.rglob("*")
            if value.is_file() and value.name != "WORLD_INDEX.jsonl"
        ):
            relative = path.relative_to(active_view).as_posix()
            record_type = relative.split("/", 1)[0].casefold()
            revision = None
            subjects: list[str] = []
            tags: list[str] = []
            if path.suffix.casefold() == ".json":
                payload = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(payload, dict):
                    revision = payload.get("_cera_revision")
                    subjects = [str(value) for value in payload.get("participant_ids", [])]
                    character_id = payload.get("character_id")
                    if isinstance(character_id, str) and character_id not in subjects:
                        subjects.append(character_id)
                    tags = [str(value) for value in payload.get("tags", [])]
            rows.append(
                {
                    "record_id": f"world:{text_sha256(relative)[:24]}",
                    "path": relative,
                    "revision": revision,
                    "subjects": subjects,
                    "concepts": tags,
                    "record_type": record_type,
                    "content_sha256": text_sha256(path.read_text(encoding="utf-8")),
                }
            )
        index = active_view / "WORLD_INDEX.jsonl"
        index.write_text(
            "".join(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n" for row in rows),
            encoding="utf-8",
            newline="\n",
        )

    def recover_pending_promotions(
        self, world_id: str, branch_id: str
    ) -> tuple[str, ...]:
        """Recover exact directory-swap transactions without provider work."""

        root = self.branch_root(world_id, branch_id)
        if not os.path.lexists(root):
            return ()
        branch_relative = root.relative_to(self.runtime_root).as_posix()
        if inspect_leaf(
            self.runtime_root, branch_relative, expected_kind="directory"
        ).identity is None:
            return ()
        recovered: list[str] = []
        with locked_directory_chain(
            self.runtime_root, branch_relative, create_missing=False
        ):
            with os.scandir(root) as entries:
                transaction_names = tuple(
                    sorted(
                        entry.name
                        for entry in entries
                        if entry.name.startswith((".promotion-", ".acceptance-"))
                    )
                )
        transaction_roots = tuple(root / name for name in transaction_names)
        for transaction_root in transaction_roots:
            transaction_relative = transaction_root.relative_to(
                self.runtime_root
            ).as_posix()
            transaction_leaf = inspect_leaf(
                self.runtime_root,
                transaction_relative,
                expected_kind="directory",
            )
            if transaction_leaf.identity is None:
                continue
            with ExitStack() as path_locks:
                path_locks.enter_context(
                    locked_directory_chain(
                        self.runtime_root,
                        branch_relative,
                        create_missing=False,
                    )
                )
                path_locks.enter_context(
                    locked_directory_chain(
                        self.runtime_root,
                        transaction_relative,
                        create_missing=False,
                    )
                )
                journal = transaction_root / "JOURNAL.json"
                journal_relative = transaction_relative + "/JOURNAL.json"
                journal_bytes, _journal_identity = safe_read_bytes(
                    self.runtime_root, journal_relative
                )
                payload = json.loads(journal_bytes.decode("utf-8"))
                state = payload.get("state")
                if state in {
                    "finalized",
                    "local_acceptance_complete",
                    "rolled_back",
                }:
                    continue
                prior_hash = payload.get("prior_sha256")
                prepared_hash = payload.get("prepared_sha256")
                if not isinstance(prior_hash, str) or not isinstance(
                    prepared_hash, str
                ):
                    raise StateConflictError("promotion journal lacks tree hashes")
                active = root / "ACTIVE"
                prepared = transaction_root / "PREPARED_ACTIVE"
                backup = transaction_root / "PRIOR_ACTIVE"
                active_relative = branch_relative + "/ACTIVE"
                prepared_relative = transaction_relative + "/PREPARED_ACTIVE"
                backup_relative = transaction_relative + "/PRIOR_ACTIVE"

                def tree_state(relative: str, path: Path):
                    leaf = inspect_leaf(
                        self.runtime_root, relative, expected_kind="directory"
                    )
                    if leaf.identity is None:
                        return None, None
                    validate_tree_no_follow(self.runtime_root, relative)
                    return self.tree_sha256(path), leaf.identity

                active_hash, active_identity = tree_state(active_relative, active)
                prepared_actual, prepared_identity = tree_state(
                    prepared_relative, prepared
                )
                backup_actual, backup_identity = tree_state(
                    backup_relative, backup
                )
                for actual, expected, label in (
                    (prepared_actual, prepared_hash, "prepared"),
                    (backup_actual, prior_hash, "backup"),
                ):
                    if actual is not None and actual != expected:
                        raise StateConflictError(
                            f"promotion {label} tree hash changed"
                        )
                if active_hash == prepared_hash:
                    if backup_identity is not None:
                        shutil.rmtree(backup)
                    if prepared_identity is not None:
                        shutil.rmtree(prepared)
                    terminal = "active_installed"
                elif (
                    active_hash is None
                    and prepared_actual == prepared_hash
                    and backup_actual == prior_hash
                    and prepared_identity is not None
                ):
                    safe_replace_directory(
                        self.runtime_root,
                        source_relative_path=prepared_relative,
                        target_relative_path=active_relative,
                        expected_source_identity=prepared_identity,
                    )
                    if backup_identity is None:
                        raise StateConflictError(
                            "promotion recovery backup identity is unavailable"
                        )
                    shutil.rmtree(backup)
                    terminal = "active_installed"
                elif active_hash == prior_hash:
                    if prepared_identity is not None:
                        shutil.rmtree(prepared)
                    if backup_identity is not None:
                        shutil.rmtree(backup)
                    terminal = "rolled_back"
                elif (
                    active_hash is None
                    and backup_actual == prior_hash
                    and prepared_actual is None
                    and backup_identity is not None
                ):
                    safe_replace_directory(
                        self.runtime_root,
                        source_relative_path=backup_relative,
                        target_relative_path=active_relative,
                        expected_source_identity=backup_identity,
                    )
                    terminal = "rolled_back"
                else:
                    raise StateConflictError(
                        "promotion recovery cannot verify an exact tree"
                    )
                validate_tree_no_follow(self.runtime_root, active_relative)
                recovered_payload = {
                    **payload,
                    "state": terminal,
                    "recovered_at_utc": _utc_now(),
                    "active_sha256": self.tree_sha256(active),
                }
                self._write_json(journal, recovered_payload)
                if (
                    terminal == "active_installed"
                    and payload.get("schema_version")
                    in {
                        "cera.continuous_acceptance_journal.v2",
                        "cera.continuous_acceptance_journal.v3",
                        "cera.continuous_acceptance_journal.v4",
                        "cera.continuous_acceptance_journal.v5",
                        "cera.continuous_acceptance_journal.v6",
                    }
                ):
                    self._finish_local_acceptance(root, transaction_root)
                elif (
                    terminal == "active_installed"
                    and payload.get("schema_version")
                    == "cera.sequence_first.acceptance_journal.v1"
                ):
                    from cera.sequence_first.world import (
                        finish_sequence_first_acceptance,
                    )

                    finish_sequence_first_acceptance(
                        self,
                        root,
                        transaction_root,
                    )
                recovered.append(
                    str(payload.get("turn_id", transaction_root.name))
                )
        return tuple(recovered)

    def _promote_directory(
        self,
        branch_root: Path,
        candidate_active: Path,
        turn_id: str,
        *,
        transaction_root: Path | None = None,
        journal_base: dict[str, Any] | None = None,
    ) -> None:
        branch_root = lexical_absolute(branch_root)
        branch_relative = branch_root.relative_to(self.runtime_root).as_posix()
        active = branch_root / "ACTIVE"
        active_relative = branch_relative + "/ACTIVE"
        candidate_relative = lexical_absolute(candidate_active).relative_to(
            self.runtime_root
        ).as_posix()
        validate_tree_no_follow(self.runtime_root, candidate_relative)
        with locked_directory_chain(
            self.runtime_root, branch_relative, create_missing=False
        ):
            transaction_root = lexical_absolute(
                transaction_root
                or Path(mkdtemp(prefix=f".promotion-{turn_id}-", dir=branch_root))
            )
        transaction_relative = transaction_root.relative_to(
            self.runtime_root
        ).as_posix()
        transaction_leaf = inspect_leaf(
            self.runtime_root,
            transaction_relative,
            expected_kind="directory",
        )
        if transaction_leaf.identity is None:
            raise StateConflictError("promotion transaction identity is unavailable")
        prepared = transaction_root / "PREPARED_ACTIVE"
        backup = transaction_root / "PRIOR_ACTIVE"
        journal = transaction_root / "JOURNAL.json"
        prepared_relative = transaction_relative + "/PREPARED_ACTIVE"
        backup_relative = transaction_relative + "/PRIOR_ACTIVE"
        with ExitStack() as path_locks:
            path_locks.enter_context(
                locked_directory_chain(
                    self.runtime_root, branch_relative, create_missing=False
                )
            )
            path_locks.enter_context(
                locked_directory_chain(
                    self.runtime_root, transaction_relative, create_missing=False
                )
            )
            if inspect_leaf(
                self.runtime_root,
                prepared_relative,
                expected_kind="directory",
            ).identity is not None or inspect_leaf(
                self.runtime_root,
                backup_relative,
                expected_kind="directory",
            ).identity is not None:
                raise StateConflictError("promotion staging path is occupied")
            shutil.copytree(candidate_active, prepared, symlinks=True)
            validate_tree_no_follow(self.runtime_root, prepared_relative)
            validate_tree_no_follow(self.runtime_root, active_relative)
            base = journal_base or {
                "schema_version": "cera.continuous_world_promotion_journal.v2",
                "turn_id": turn_id,
                "prior_sha256": self.tree_sha256(active),
                "prepared_sha256": self.tree_sha256(prepared),
            }
            self._write_json(journal, {**base, "state": "prepared"})
            self._failpoint("journal_created")
            active_identity = inspect_leaf(
                self.runtime_root, active_relative, expected_kind="directory"
            ).identity
            if active_identity is None:
                raise StateConflictError("promotion ACTIVE identity is unavailable")
            safe_replace_directory(
                self.runtime_root,
                source_relative_path=active_relative,
                target_relative_path=backup_relative,
                expected_source_identity=active_identity,
            )
            self._write_json(journal, {**base, "state": "active_moved_to_backup"})
            self._failpoint("active_moved_to_backup")
            prepared_identity = inspect_leaf(
                self.runtime_root, prepared_relative, expected_kind="directory"
            ).identity
            if prepared_identity is None:
                raise StateConflictError("promotion prepared identity is unavailable")
            try:
                safe_replace_directory(
                    self.runtime_root,
                    source_relative_path=prepared_relative,
                    target_relative_path=active_relative,
                    expected_source_identity=prepared_identity,
                )
            except Exception:
                backup_identity = inspect_leaf(
                    self.runtime_root,
                    backup_relative,
                    expected_kind="directory",
                ).identity
                if backup_identity is None:
                    raise StateConflictError(
                        "promotion rollback backup identity is unavailable"
                    )
                safe_replace_directory(
                    self.runtime_root,
                    source_relative_path=backup_relative,
                    target_relative_path=active_relative,
                    expected_source_identity=backup_identity,
                )
                self._write_json(journal, {**base, "state": "rolled_back"})
                raise
            self._write_json(
                journal, {**base, "state": "prepared_active_installed"}
            )
            self._failpoint("prepared_active_installed")
            backup_identity = inspect_leaf(
                self.runtime_root, backup_relative, expected_kind="directory"
            ).identity
            if backup_identity is None:
                raise StateConflictError("promotion backup identity is unavailable")
            validate_tree_no_follow(self.runtime_root, backup_relative)
            shutil.rmtree(backup)
            if inspect_leaf(
                self.runtime_root,
                backup_relative,
                expected_kind="directory",
            ).identity is not None:
                raise StateConflictError("promotion backup removal failed")
            self._write_json(journal, {**base, "state": "prior_backup_removed"})
            self._failpoint("prior_backup_removed")
            self._write_json(journal, {**base, "state": "committed"})
            self._failpoint("committed")
            validate_tree_no_follow(self.runtime_root, active_relative)
            self._write_json(
                journal,
                {
                    **base,
                    "state": "active_installed",
                    "active_sha256": self.tree_sha256(active),
                },
            )

    def _finish_local_acceptance(
        self, branch_root: Path, transaction_root: Path
    ) -> None:
        journal_path = transaction_root / "JOURNAL.json"
        payload = json.loads(journal_path.read_text(encoding="utf-8"))
        if payload.get("schema_version") not in {
            "cera.continuous_acceptance_journal.v2",
            "cera.continuous_acceptance_journal.v3",
            "cera.continuous_acceptance_journal.v4",
            "cera.continuous_acceptance_journal.v5",
            "cera.continuous_acceptance_journal.v6",
        }:
            return
        active = branch_root / "ACTIVE"
        active_relative = active.relative_to(self.runtime_root).as_posix()
        if self.tree_sha256(active) != payload.get("prepared_sha256"):
            raise StateConflictError("acceptance journal ACTIVE hash changed")
        for label in ("accepted_pair", "accepted_event"):
            relative = payload.get(f"{label}_relative_path")
            expected = payload.get(f"{label}_sha256")
            if not isinstance(relative, str) or not isinstance(expected, str):
                raise StateConflictError(f"acceptance journal lacks {label} identity")
            target_relative = active_relative + "/" + relative.replace("\\", "/")
            encoded, _identity = safe_read_bytes(
                self.runtime_root, target_relative
            )
            if text_sha256(encoded.decode("utf-8")) != expected:
                raise StateConflictError(f"acceptance journal {label} changed")
        receipt_payload = payload.get("promotion_receipt_payload")
        if not isinstance(receipt_payload, dict):
            raise StateConflictError("acceptance journal lacks promotion receipt payload")
        turn_id = str(payload["turn_id"])
        candidate_root = branch_root / "CANDIDATES" / _slug(turn_id, "turn_id")
        receipt_path = candidate_root / "PROMOTION_RECEIPT.json"
        self._write_idempotent_json(receipt_path, receipt_payload, "promotion receipt")
        self._write_json(journal_path, {**payload, "state": "promotion_receipt_written"})
        self._failpoint("promotion_receipt_written")
        diagnostic = payload.get("false_positive_diagnostic_payload")
        if diagnostic is not None:
            if not isinstance(diagnostic, dict):
                raise StateConflictError("acceptance journal diagnostic is invalid")
            self._write_false_positive_diagnostic(branch_root, diagnostic)
        self._write_json(journal_path, {**payload, "state": "diagnostic_written"})
        self._failpoint("false_positive_diagnostic_written")
        from cera.schema import from_mapping

        receipt = from_mapping(WorldPromotionReceiptV1, receipt_payload)
        if (
            receipt.turn_id != payload.get("turn_id")
            or receipt.package_sha256 != payload.get("package_sha256")
            or receipt.candidate_sha256 != payload.get("candidate_sha256")
            or receipt.authority_context_sha256
            != payload.get("authority_context_sha256")
            or receipt.creator_action.value != payload.get("creator_action")
            or receipt.active_before_sha256 != payload.get("prior_sha256")
            or receipt.active_after_sha256 != payload.get("prepared_sha256")
            or not receipt.accepted
            or not receipt.planner_append_required
        ):
            raise StateConflictError("acceptance journal receipt binding changed")
        self._append_timeline(branch_root, receipt)
        self._write_json(journal_path, {**payload, "state": "timeline_written"})
        self._failpoint("timeline_written")
        self._write_json(
            journal_path,
            {
                **payload,
                "state": "local_acceptance_complete",
                "active_sha256": self.tree_sha256(active),
                "local_acceptance_completed_at_utc": _utc_now(),
            },
        )

    def pending_acceptance_synchronization(
        self, world_id: str, branch_id: str
    ) -> tuple[str, ...]:
        root = self.initialize(world_id, branch_id)
        pending = []
        for transaction_root in sorted(root.glob(".acceptance-*")):
            path = transaction_root / "JOURNAL.json"
            payload = json.loads(path.read_text(encoding="utf-8"))
            if payload.get("state") != "local_acceptance_complete":
                continue
            if payload.get("model_injection_state") != "synchronized":
                pending.append(str(payload["turn_id"]))
        return tuple(pending)

    def mark_acceptance_planner_ledger_appended(
        self,
        world_id: str,
        branch_id: str,
        turn_id: str,
        envelope_sha256: str,
        provider_thread_sha256: str | None = None,
    ) -> None:
        if provider_thread_sha256 is None or re.fullmatch(r"[0-9a-f]{64}", provider_thread_sha256) is None:
            raise ContractValidationError("Planner ledger append lacks stored-thread hash")
        self._update_acceptance_sync_state(
            world_id,
            branch_id,
            turn_id,
            planner_ledger_state="appended",
            accepted_final_envelope_sha256=envelope_sha256,
            planner_provider_thread_sha256=provider_thread_sha256,
        )

    def mark_acceptance_injection_returned(
        self,
        world_id: str,
        branch_id: str,
        turn_id: str,
        *,
        envelope_sha256: str,
        provider_thread_sha256: str,
        injection_operation_receipt_sha256: str,
    ) -> None:
        if any(
            re.fullmatch(r"[0-9a-f]{64}", value) is None
            for value in (
                envelope_sha256,
                provider_thread_sha256,
                injection_operation_receipt_sha256,
            )
        ):
            raise ContractValidationError("acceptance injection receipt is invalid")
        self._update_acceptance_sync_state(
            world_id,
            branch_id,
            turn_id,
            planner_ledger_state="appended",
            model_injection_state="returned",
            accepted_final_envelope_sha256=envelope_sha256,
            planner_provider_thread_sha256=provider_thread_sha256,
            injection_operation_receipt_sha256=injection_operation_receipt_sha256,
        )

    def mark_acceptance_session_snapshot_persisted(
        self,
        world_id: str,
        branch_id: str,
        turn_id: str,
        *,
        snapshot_receipt: Any,
        injection_receipt: Any,
    ) -> None:
        from .sessions import (
            ContinuousContextInjectionReceiptV1,
            ContinuousSessionSnapshotReceiptV1,
            ContinuousSessionSnapshotReceiptV2,
            ContinuousSessionSnapshotReceiptV3,
            ContinuousSessionSnapshotStore,
        )
        if not isinstance(
            snapshot_receipt,
            (
                ContinuousSessionSnapshotReceiptV1,
                ContinuousSessionSnapshotReceiptV2,
                ContinuousSessionSnapshotReceiptV3,
            ),
        ):
            raise ContractValidationError("acceptance snapshot receipt type changed")
        if not isinstance(injection_receipt, ContinuousContextInjectionReceiptV1):
            raise ContractValidationError("acceptance injection receipt type changed")
        if (
            snapshot_receipt.world_id != world_id
            or snapshot_receipt.branch_id != branch_id
            or snapshot_receipt.accepted_turn_id != turn_id
            or injection_receipt.accepted_turn_id != turn_id
            or snapshot_receipt.injection_operation_receipt_sha256
            != injection_receipt.operation_receipt_sha256
        ):
            raise StateConflictError("acceptance snapshot receipt scope changed")
        root = self.branch_root(world_id, branch_id)
        snapshot = ContinuousSessionSnapshotStore(root).load_immutable(snapshot_receipt)
        if (
            turn_id not in snapshot.accepted_turn_ids
            or snapshot_receipt.accepted_envelope_sha256
            != injection_receipt.accepted_envelope_sha256
            or snapshot_receipt.provider_thread_sha256
            != injection_receipt.provider_thread_sha256
        ):
            raise StateConflictError("acceptance Planner snapshot identity changed")
        self._update_acceptance_sync_state(
            world_id,
            branch_id,
            turn_id,
            planner_snapshot_state="persisted",
            accepted_final_envelope_sha256=snapshot_receipt.accepted_envelope_sha256,
            planner_provider_thread_sha256=snapshot_receipt.provider_thread_sha256,
            planner_session_snapshot_sha256=snapshot_receipt.snapshot_sha256,
            planner_session_snapshot_relative_path=snapshot_receipt.immutable_relative_path,
            planner_session_snapshot_receipt=to_primitive(snapshot_receipt),
        )

    def mark_acceptance_model_synchronized(
        self,
        world_id: str,
        branch_id: str,
        turn_id: str,
        envelope_sha256: str,
    ) -> None:
        payload = self.acceptance_synchronization_record(world_id, branch_id, turn_id)
        if payload.get("stable_reference_state") != "persisted":
            raise StateConflictError("stable accepted references are not persisted")
        synchronization_receipt_sha256 = (
            self.expected_acceptance_synchronization_receipt_sha256(
                world_id,
                branch_id,
                turn_id,
                envelope_sha256,
            )
        )
        self._update_acceptance_sync_state(
            world_id,
            branch_id,
            turn_id,
            planner_ledger_state="appended",
            model_injection_state="synchronized",
            accepted_final_envelope_sha256=envelope_sha256,
            synchronization_receipt_sha256=synchronization_receipt_sha256,
        )

    def expected_acceptance_synchronization_receipt_sha256(
        self,
        world_id: str,
        branch_id: str,
        turn_id: str,
        envelope_sha256: str,
    ) -> str:
        payload = self.acceptance_synchronization_record(world_id, branch_id, turn_id)
        if payload.get("model_injection_state") != "returned":
            raise StateConflictError("model injection has not returned")
        if payload.get("planner_snapshot_state") != "persisted":
            raise StateConflictError("Planner session snapshot is not persisted")
        if payload.get("accepted_final_envelope_sha256") != envelope_sha256:
            raise StateConflictError("accepted-final synchronization hash changed")
        return canonical_sha256(
            {
                "turn_id": turn_id,
                "accepted_final_envelope_sha256": envelope_sha256,
                "planner_provider_thread_sha256": payload.get(
                    "planner_provider_thread_sha256"
                ),
                "injection_operation_receipt_sha256": payload.get(
                    "injection_operation_receipt_sha256"
                ),
                "planner_session_snapshot_sha256": payload.get(
                    "planner_session_snapshot_sha256"
                ),
            }
        )

    def mark_acceptance_stable_references_persisted(
        self,
        world_id: str,
        branch_id: str,
        turn_id: str,
        *,
        reference_path: Path,
        compact_head_receipt_sha256: str,
    ) -> None:
        from .evidence import StableAcceptedContextReferenceStore

        payload = self.acceptance_synchronization_record(world_id, branch_id, turn_id)
        provider_thread_sha256 = str(payload.get("planner_provider_thread_sha256", ""))
        if re.fullmatch(r"[0-9a-f]{64}", compact_head_receipt_sha256) is None:
            raise ContractValidationError("compact accepted-head receipt hash is invalid")
        store = StableAcceptedContextReferenceStore(
            self.branch_root(world_id, branch_id)
        )
        expected_path = lexical_absolute(
            store.path_for(turn_id, provider_thread_sha256)
        )
        supplied_path = lexical_absolute(reference_path)
        if supplied_path != expected_path:
            raise StateConflictError("stable accepted-reference custody path changed")
        branch_root = lexical_absolute(self.branch_root(world_id, branch_id))
        supplied_relative = supplied_path.relative_to(branch_root).as_posix()
        supplied_bytes, _supplied_identity = safe_read_bytes(
            branch_root, supplied_relative
        )
        receipt, _references = store.load(
            turn_id,
            provider_thread_sha256=provider_thread_sha256,
        )
        if (
            receipt.receipt_sha256 != compact_head_receipt_sha256
            or receipt.world_id != world_id
            or receipt.branch_id != branch_id
            or receipt.accepted_envelope_sha256
            != payload.get("accepted_final_envelope_sha256")
            or receipt.accepted_pair_sha256 != payload.get("accepted_pair_sha256")
            or receipt.accepted_event_sha256 != payload.get("accepted_event_sha256")
            or receipt.injection_receipt_sha256
            != payload.get("injection_operation_receipt_sha256")
            or receipt.session_snapshot_sha256
            != payload.get("planner_session_snapshot_sha256")
        ):
            raise StateConflictError("stable accepted-reference binding changed")
        self._update_acceptance_sync_state(
            world_id,
            branch_id,
            turn_id,
            stable_reference_state="persisted",
            compact_accepted_head_receipt_sha256=compact_head_receipt_sha256,
            stable_reference_relative_path=supplied_path.relative_to(
                branch_root
            ).as_posix(),
            stable_reference_set_sha256=text_sha256(supplied_bytes.decode("utf-8")),
        )

    def acceptance_synchronization_record(
        self, world_id: str, branch_id: str, turn_id: str
    ) -> dict[str, Any]:
        root = self.branch_root(world_id, branch_id)
        journal = root / f".acceptance-{_slug(turn_id, 'turn_id')}" / "JOURNAL.json"
        if not journal.is_file():
            raise StateConflictError("acceptance synchronization journal is unavailable")
        payload = json.loads(journal.read_text(encoding="utf-8"))
        if payload.get("state") != "local_acceptance_complete":
            raise StateConflictError("local acceptance evidence is incomplete")
        return payload

    def _update_acceptance_sync_state(
        self, world_id: str, branch_id: str, turn_id: str, **changes: Any
    ) -> None:
        root = self.branch_root(world_id, branch_id)
        journal = root / f".acceptance-{_slug(turn_id, 'turn_id')}" / "JOURNAL.json"
        if not journal.is_file():
            raise StateConflictError("acceptance synchronization journal is unavailable")
        payload = self.acceptance_synchronization_record(world_id, branch_id, turn_id)
        prior_hash = payload.get("accepted_final_envelope_sha256")
        supplied_hash = changes.get("accepted_final_envelope_sha256")
        if prior_hash is not None and supplied_hash is not None and prior_hash != supplied_hash:
            raise StateConflictError("accepted-final synchronization hash changed")
        prior_thread = payload.get("planner_provider_thread_sha256")
        supplied_thread = changes.get("planner_provider_thread_sha256")
        if prior_thread is not None and supplied_thread is not None and prior_thread != supplied_thread:
            raise StateConflictError("accepted-final Planner thread changed")
        self._write_json(journal, {**payload, **changes})

    def _failpoint(self, stage: str) -> None:
        if self._promotion_failpoint is not None:
            self._promotion_failpoint(stage)

    def _append_timeline(self, root: Path, receipt: WorldPromotionReceiptV1) -> None:
        path = root / "DEBUG" / "TIMELINE.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_file():
            for line in path.read_text(encoding="utf-8").splitlines():
                if line and json.loads(line).get("receipt_sha256") == receipt.receipt_sha256:
                    return
        with path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(
                json.dumps(
                    {
                        "turn_id": receipt.turn_id,
                        "action": receipt.creator_action.value,
                        "accepted": receipt.accepted,
                        "package_sha256": receipt.package_sha256,
                        "receipt_sha256": receipt.receipt_sha256,
                        "recorded_at_utc": receipt.recorded_at_utc,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n"
            )

    def _write_idempotent_json(
        self, path: Path, value: Any, label: str
    ) -> None:
        path = lexical_absolute(path)
        relative = path.relative_to(self.runtime_root).as_posix()
        existing = inspect_leaf(self.runtime_root, relative)
        if existing.identity is not None:
            encoded, _identity = safe_read_bytes(self.runtime_root, relative)
            if json.loads(encoded.decode("utf-8")) != to_primitive(value):
                raise StateConflictError(f"{label} changed during acceptance recovery")
            return
        self._write_json(path, value)

    def _write_json(self, path: Path, value: Any) -> None:
        """Write repository-world JSON under lexical no-follow custody."""

        path = lexical_absolute(path)
        try:
            relative = path.relative_to(self.runtime_root).as_posix()
        except ValueError as exc:
            raise StateConflictError(
                "continuous world write escaped its trusted runtime root"
            ) from exc
        temporary_relative = relative + ".tmp"
        ensure_parent_chain(self.runtime_root, relative)
        final_custody = capture_target_custody(self.runtime_root, relative)
        temporary_custody = capture_target_custody(
            self.runtime_root, temporary_relative
        )
        encoded = canonical_bytes(value) + b"\n"
        temporary_identity = None
        with locked_directory_chain(
            self.runtime_root,
            final_custody.verified_parent_relative_path,
            create_missing=False,
        ):
            verify_target_custody(self.runtime_root, final_custody)
            verify_target_custody(self.runtime_root, temporary_custody)
            initial_final = inspect_leaf(self.runtime_root, relative)
            temporary = inspect_leaf(self.runtime_root, temporary_relative)
            try:
                if temporary.identity is not None:
                    temporary_bytes, temporary_identity = safe_read_bytes(
                        self.runtime_root, temporary_relative
                    )
                    if temporary_bytes != encoded:
                        raise StateConflictError(
                            "continuous world temporary JSON custody changed"
                        )
                else:
                    temporary_identity = safe_create_new_bytes(
                        self.runtime_root, temporary_relative, encoded
                    )
                verify_target_custody(self.runtime_root, final_custody)
                verify_target_custody(self.runtime_root, temporary_custody)
                if inspect_leaf(
                    self.runtime_root, relative
                ).identity != initial_final.identity:
                    raise StateConflictError(
                        "continuous world final JSON changed before replacement"
                    )
                safe_replace(
                    self.runtime_root,
                    temporary_relative_path=temporary_relative,
                    final_relative_path=relative,
                    expected_temporary_identity=temporary_identity,
                )
                temporary_identity = None
            finally:
                if temporary_identity is not None:
                    unlink_if_identity(
                        self.runtime_root,
                        temporary_relative,
                        temporary_identity,
                    )


class ContinuousDebugRecorder:
    """Ignored local raw diagnostics with mandatory credential redaction."""

    REQUIRED_ARTIFACTS: ClassVar[tuple[str, ...]] = (
        "planner_authority_packet.json",
        "planner_prompt_components.json",
        "planner_raw_prompt.txt",
        "planner_output.json",
        "planner_tools.json",
        "deepseek_request.json",
        "deepseek_output.json",
        "writer_mechanical_envelope.json",
        "validator_request.json",
        "validator_cited_accepted_evidence.json",
        "validator_output.json",
        "presentation_realization_segments.json",
        "source_grounded_public_state_receipts.json",
        "validator_tools.json",
        "reader_request.json",
        "reader_output.json",
        "reader_tools.json",
        "candidate_before.json",
        "candidate_after.json",
        "exact_diff.json",
        "edit_package.json",
        "new_field_log.json",
        "creator_action.json",
        "promotion_or_discard_receipt.json",
        "provider_routes.json",
        "usage.json",
        "stage_timings.json",
        "errors.json",
        "replay_input.json",
    )
    OPTIONAL_ARTIFACTS: ClassVar[tuple[str, ...]] = (
        "scene_change_request.json",
        "scene_change_output.json",
        "scene_change_tools.json",
        "scene_change_timing.json",
        "writer_recall_input.json",
        "writer_recall_directive.json",
    )

    def __init__(self, branch_root: Path, scene_id: str, turn_id: str) -> None:
        self.root = branch_root / "DEBUG" / _slug(scene_id, "scene_id") / _slug(turn_id, "turn_id")
        self.root.mkdir(parents=True, exist_ok=True)

    def for_writer_attempt(self, attempt_number: int) -> "ContinuousDebugRecorder":
        """Return immutable debug custody for one bounded Writer attempt.

        Attempt one retains the historical turn-level layout.  Later attempts
        receive complete, isolated skeletons below that root so rejected
        candidates and their Validator/Reader evidence are never overwritten.
        """

        if type(attempt_number) is not int or not 1 <= attempt_number <= 3:
            raise ContractValidationError("Writer attempt number must be between 1 and 3")
        if attempt_number == 1:
            return self
        root = self.root / "WRITER_ATTEMPTS" / f"attempt-{attempt_number:03d}"
        if root.exists():
            raise StateConflictError("Writer attempt debug custody already exists")
        recorder = object.__new__(type(self))
        recorder.root = root
        recorder.root.mkdir(parents=True, exist_ok=False)
        recorder.initialize()
        return recorder

    def write_json(self, name: str, value: Any) -> Path:
        if name not in self.REQUIRED_ARTIFACTS and name not in self.OPTIONAL_ARTIFACTS:
            raise ContractValidationError("unknown continuous debug artifact")
        path = self.root / name
        path.write_bytes(canonical_bytes(redact_secrets(value)) + b"\n")
        return path

    def write_text(self, name: str, value: str) -> Path:
        if name != "planner_raw_prompt.txt":
            raise ContractValidationError("unknown continuous debug text artifact")
        redacted = redact_secrets(value)
        path = self.root / name
        path.write_text(str(redacted), encoding="utf-8", newline="\n")
        return path

    def initialize(self) -> None:
        """Create a complete diagnostic skeleton before any provider dispatch."""

        self.write_text("planner_raw_prompt.txt", "")
        for name in self.REQUIRED_ARTIFACTS:
            if name == "planner_raw_prompt.txt":
                continue
            self.write_json(name, {"state": "not_reached"})

    def record_failure(self, stage: str, error: BaseException) -> None:
        self.write_json(
            "errors.json",
            [
                {
                    "stage": stage,
                    "error_type": type(error).__name__,
                    "message": "stage failed; raw exception text omitted to protect secrets",
                    "stack_frames": [
                        {
                            "file": value.filename,
                            "line": value.lineno,
                            "function": value.name,
                        }
                        for value in traceback.extract_tb(error.__traceback__)
                    ],
                }
            ],
        )

    def validate_complete(self) -> tuple[str, ...]:
        return tuple(name for name in self.REQUIRED_ARTIFACTS if not (self.root / name).is_file())

    def replay_input(self) -> dict[str, Any]:
        path = self.root / "replay_input.json"
        if not path.is_file():
            raise StateConflictError("continuous replay input is missing")
        return json.loads(path.read_text(encoding="utf-8"))


@dataclass(frozen=True, slots=True)
class SceneChangeEnvelopeV1:
    previous_scene_summary: SceneSummaryV1
    previous_scene_summary_view: SceneSummaryDerivedViewV1
    first_user_message_of_new_scene: str

    def __post_init__(self) -> None:
        if not self.first_user_message_of_new_scene.strip():
            raise ContractValidationError("scene change requires the first new-scene prompt")
        if self.first_user_message_of_new_scene in self.previous_scene_summary.shortest_complete_summary:
            raise ContractValidationError("new-scene prompt leaked into old-scene summary")
        if self.previous_scene_summary_view.summary != self.previous_scene_summary:
            raise ContractValidationError("scene-change envelope changed its derived summary")

    def render_for_planner(self) -> str:
        pairs = "\n".join(
            "[USER MESSAGE]\n"
            + value.user_message
            + "\n[COMPLETE FINAL SEQUENCE]\n"
            + json.dumps(to_primitive(value.complete_final_sequence), ensure_ascii=False, sort_keys=True)
            for value in self.previous_scene_summary.last_five_exact_pairs
        )
        return (
            "[SCENE CHANGE]\n[PREVIOUS SCENE SUMMARY - NON-AUTHORITATIVE DERIVED VIEW]\n"
            + json.dumps(
                {
                    "authority_classification": self.previous_scene_summary_view.authority_classification,
                    "source_accepted_turn_ids": self.previous_scene_summary_view.source_accepted_turn_ids,
                    "source_turn_provenance": to_primitive(
                        self.previous_scene_summary_view.source_turn_provenance
                    ),
                    "summary_revision": self.previous_scene_summary_view.summary_revision,
                    "regeneration_identity_sha256": self.previous_scene_summary_view.regeneration_identity_sha256,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
            + "\n"
            + self.previous_scene_summary.shortest_complete_summary
            + "\n[LAST FIVE EXACT ACCEPTED PAIRS]\n"
            + pairs
            + "\n[FIRST USER MESSAGE OF NEW SCENE]\n"
            + self.first_user_message_of_new_scene
        )

    def lean_planner_context(self) -> LeanSceneChangeContextV1:
        """Current-scene handoff without replaying prior accepted pairs."""

        return LeanSceneChangeContextV1(
            schema_version=LeanSceneChangeContextV1.SCHEMA_VERSION,
            completed_scene_id=self.previous_scene_summary.completed_scene_id,
            accepted_turn_ids=self.previous_scene_summary.accepted_turn_ids,
            shortest_complete_summary=(
                self.previous_scene_summary.shortest_complete_summary
            ),
            ending_state=self.previous_scene_summary.ending_state,
            transition_context=self.previous_scene_summary.transition_context,
            summary_authority_classification=(
                self.previous_scene_summary_view.authority_classification
            ),
            summary_revision=self.previous_scene_summary_view.summary_revision,
            regeneration_identity_sha256=(
                self.previous_scene_summary_view.regeneration_identity_sha256
            ),
            first_user_message_of_new_scene=(
                self.first_user_message_of_new_scene
            ),
            exact_prior_pairs_included=False,
        )


class SceneChangeCoordinator:
    """Hold the new prompt while the explicit prior-scene summary is made."""

    def __init__(self, world: ContinuousWorldStore) -> None:
        self.world = world

    def process(
        self,
        *,
        world_id: str,
        branch_id: str,
        completed_scene_id: str,
        first_new_scene_prompt: str,
        accepted_turn_ids: tuple[str, ...],
        summarize: Callable[[tuple[AcceptedTurnPairV1, ...]], SceneSummaryV1],
    ) -> SceneChangeEnvelopeV1:
        pairs = self.world.accepted_turn_pairs(world_id, branch_id, accepted_turn_ids)
        summary = summarize(pairs)
        if summary.accepted_turn_ids != accepted_turn_ids:
            raise StateConflictError("Validator scene summary changed the accepted-turn allow-list")
        if summary.completed_scene_id != completed_scene_id:
            raise StateConflictError("Validator scene summary changed scene identity")
        if summary.last_five_exact_pairs != pairs[-5:]:
            raise StateConflictError("scene summary exact-pair tail changed")
        if first_new_scene_prompt in summary.shortest_complete_summary:
            raise StateConflictError("first new-scene prompt entered old-scene summary")
        summary_path = self.world.save_scene_summary(world_id, branch_id, summary)
        from cera.schema import from_mapping

        derived_view = from_mapping(
            SceneSummaryDerivedViewV1,
            json.loads(summary_path.read_text(encoding="utf-8")),
        )
        return SceneChangeEnvelopeV1(
            previous_scene_summary=summary,
            previous_scene_summary_view=derived_view,
            first_user_message_of_new_scene=first_new_scene_prompt,
        )
