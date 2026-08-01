"""Mechanical continuous-world candidate, promotion, and scene-change store."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
import json
import os
from pathlib import Path
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
    SceneSummaryV1,
    ValidatorFinalizationPackageV1,
    WorldEditOperationKind,
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
        lowered = value.casefold()
        if lowered.startswith(("bearer ", "sk-")):
            return "[REDACTED]"
    return value


@dataclass(frozen=True, slots=True)
class CandidateWorldViewV1:
    turn_id: str
    root: Path
    source_active_sha256: str


@dataclass(frozen=True, slots=True)
class WorldPromotionReceiptV1:
    SCHEMA_VERSION: ClassVar[str] = "cera.continuous_world_promotion_receipt.v1"

    schema_version: str
    world_id: str
    branch_id: str
    turn_id: str
    creator_action: CreatorReviewAction
    package_sha256: str
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


class ContinuousWorldStore:
    """One repository-local shadow world with atomic directory promotion."""

    def __init__(self, runtime_root: Path) -> None:
        if not runtime_root.is_absolute():
            raise ContractValidationError("continuous world runtime root must be absolute")
        self.runtime_root = runtime_root.resolve()
        self._lock = RLock()

    def branch_root(self, world_id: str, branch_id: str) -> Path:
        return self.runtime_root / _slug(world_id, "world_id") / _slug(branch_id, "branch_id")

    def initialize(self, world_id: str, branch_id: str) -> Path:
        root = self.branch_root(world_id, branch_id)
        active = root / "ACTIVE"
        for directory in _ACTIVE_DIRS:
            (active / directory).mkdir(parents=True, exist_ok=True)
        for directory in ("CANDIDATES", "DEBUG", "PLANNER_SESSION", "VALIDATOR_SESSION"):
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
        return text_sha256(str(root.resolve()).casefold())

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
    ) -> Path:
        root = self.branch_root(world_id, branch_id)
        if view.root.parent != root / "CANDIDATES":
            raise StateConflictError("candidate view belongs to another branch")
        path = view.root / "VALIDATOR_PACKAGE.json"
        self._write_json(path, to_primitive(package))
        return path

    def apply_creator_action(
        self,
        *,
        world_id: str,
        branch_id: str,
        turn_id: str,
        action: CreatorReviewAction,
        package: ValidatorFinalizationPackageV1,
        accepted_pair: AcceptedTurnPairV1 | None = None,
    ) -> WorldPromotionReceiptV1:
        root = self.initialize(world_id, branch_id)
        candidate_root = root / "CANDIDATES" / _slug(turn_id, "turn_id")
        view = candidate_root / "ACTIVE_VIEW"
        if not view.is_dir():
            raise StateConflictError("continuous candidate view is unavailable")
        before = self.tree_sha256(root / "ACTIVE")
        if action not in {CreatorReviewAction.ACCEPT, CreatorReviewAction.FALSE_POSITIVE}:
            receipt = WorldPromotionReceiptV1(
                schema_version=WorldPromotionReceiptV1.SCHEMA_VERSION,
                world_id=world_id,
                branch_id=branch_id,
                turn_id=turn_id,
                creator_action=action,
                package_sha256=package.package_sha256,
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
        if accepted_pair is not None and (
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
        if accepted_pair is not None:
            self._write_json(
                view
                / "Events"
                / _identity_filename(
                    accepted_pair.accepted_turn_id, ".accepted_pair.json"
                ),
                to_primitive(accepted_pair),
            )
        self._rebuild_index(view)
        self._increment_world_state(view, turn_id)
        after = self.tree_sha256(view)
        with self._lock:
            if self.tree_sha256(root / "ACTIVE") != before:
                raise StateConflictError("ACTIVE changed during candidate validation")
            self._promote_directory(root, view, turn_id)
        receipt = WorldPromotionReceiptV1(
            schema_version=WorldPromotionReceiptV1.SCHEMA_VERSION,
            world_id=world_id,
            branch_id=branch_id,
            turn_id=turn_id,
            creator_action=action,
            package_sha256=package.package_sha256,
            active_before_sha256=before,
            active_after_sha256=after,
            changed_files=tuple(sorted(changed)),
            created_fields=tuple(
                sorted(f"{value.target_file}{value.field_path}" for value in package.created_field_log)
            ),
            accepted=True,
            planner_append_required=True,
            recorded_at_utc=_utc_now(),
        )
        self._write_json(candidate_root / "PROMOTION_RECEIPT.json", to_primitive(receipt))
        self._append_timeline(root, receipt)
        return receipt

    def save_scene_summary(
        self,
        world_id: str,
        branch_id: str,
        summary: SceneSummaryV1,
    ) -> Path:
        root = self.initialize(world_id, branch_id)
        path = (
            root
            / "ACTIVE"
            / "Scenes"
            / _identity_filename(summary.completed_scene_id, ".summary.json")
        )
        if path.exists():
            prior = json.loads(path.read_text(encoding="utf-8"))
            if canonical_sha256(prior) != canonical_sha256(to_primitive(summary)):
                raise StateConflictError("scene summary changed after acceptance")
            return path
        self._write_json(path, {"_cera_revision": 1, **to_primitive(summary)})
        self._rebuild_index(root / "ACTIVE")
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

    def _apply_operations(
        self, active_view: Path, package: ValidatorFinalizationPackageV1
    ) -> set[str]:
        changed: set[str] = set()
        loaded: dict[str, Any] = {}
        revisions: dict[str, int | None] = {}
        for operation in package.world_edit_operations:
            target = (active_view / operation.target_file).resolve()
            if active_view.resolve() not in target.parents:
                raise PermissionError("world edit escaped candidate ACTIVE view")
            key = operation.target_file.replace("\\", "/")
            if key not in loaded:
                if target.exists():
                    if target.suffix.casefold() != ".json":
                        raise ContractValidationError("V1 semantic edits require JSON targets")
                    loaded[key] = json.loads(target.read_text(encoding="utf-8"))
                    revision = loaded[key].get("_cera_revision") if isinstance(loaded[key], dict) else None
                    revisions[key] = revision
                else:
                    loaded[key] = None
                    revisions[key] = None
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
        for key in sorted(changed):
            document = loaded[key]
            if isinstance(document, dict):
                prior = revisions[key] or 0
                document["_cera_revision"] = prior + 1
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

    def _promote_directory(self, branch_root: Path, candidate_active: Path, turn_id: str) -> None:
        active = branch_root / "ACTIVE"
        transaction_root = Path(mkdtemp(prefix=f".promotion-{turn_id}-", dir=branch_root))
        prepared = transaction_root / "PREPARED_ACTIVE"
        shutil.copytree(candidate_active, prepared)
        backup = transaction_root / "PRIOR_ACTIVE"
        journal = transaction_root / "JOURNAL.json"
        self._write_json(
            journal,
            {
                "schema_version": "cera.continuous_world_promotion_journal.v1",
                "state": "prepared",
                "turn_id": turn_id,
                "prior_sha256": self.tree_sha256(active),
                "prepared_sha256": self.tree_sha256(prepared),
            },
        )
        os.replace(active, backup)
        try:
            os.replace(prepared, active)
        except BaseException:
            os.replace(backup, active)
            raise
        shutil.rmtree(backup)
        self._write_json(journal, {"schema_version": "cera.continuous_world_promotion_journal.v1", "state": "committed", "turn_id": turn_id})

    def _append_timeline(self, root: Path, receipt: WorldPromotionReceiptV1) -> None:
        path = root / "DEBUG" / "TIMELINE.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
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

    @staticmethod
    def _write_json(path: Path, value: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(canonical_bytes(value) + b"\n")


class ContinuousDebugRecorder:
    """Ignored local raw diagnostics with mandatory credential redaction."""

    REQUIRED_ARTIFACTS: ClassVar[tuple[str, ...]] = (
        "planner_prompt_components.json",
        "planner_raw_prompt.txt",
        "planner_output.json",
        "planner_tools.json",
        "deepseek_request.json",
        "deepseek_output.json",
        "validator_request.json",
        "validator_output.json",
        "validator_tools.json",
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
    )

    def __init__(self, branch_root: Path, scene_id: str, turn_id: str) -> None:
        self.root = branch_root / "DEBUG" / _slug(scene_id, "scene_id") / _slug(turn_id, "turn_id")
        self.root.mkdir(parents=True, exist_ok=True)

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
    first_user_message_of_new_scene: str

    def __post_init__(self) -> None:
        if not self.first_user_message_of_new_scene.strip():
            raise ContractValidationError("scene change requires the first new-scene prompt")
        if self.first_user_message_of_new_scene in self.previous_scene_summary.shortest_complete_summary:
            raise ContractValidationError("new-scene prompt leaked into old-scene summary")

    def render_for_planner(self) -> str:
        pairs = "\n".join(
            "[USER MESSAGE]\n"
            + value.user_message
            + "\n[COMPLETE FINAL SEQUENCE]\n"
            + json.dumps(to_primitive(value.complete_final_sequence), ensure_ascii=False, sort_keys=True)
            for value in self.previous_scene_summary.last_five_exact_pairs
        )
        return (
            "[SCENE CHANGE]\n[PREVIOUS SCENE SUMMARY]\n"
            + self.previous_scene_summary.shortest_complete_summary
            + "\n[LAST FIVE EXACT ACCEPTED PAIRS]\n"
            + pairs
            + "\n[FIRST USER MESSAGE OF NEW SCENE]\n"
            + self.first_user_message_of_new_scene
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
        self.world.save_scene_summary(world_id, branch_id, summary)
        return SceneChangeEnvelopeV1(
            previous_scene_summary=summary,
            first_user_message_of_new_scene=first_new_scene_prompt,
        )
