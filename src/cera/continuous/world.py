"""Mechanical continuous-world candidate, promotion, and scene-change store."""

from __future__ import annotations

from contextlib import contextmanager
import copy
from dataclasses import dataclass
from datetime import UTC, datetime
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
        self.runtime_root = runtime_root.resolve()
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
        if transaction_root.exists():
            raise StateConflictError("creator acceptance journal already exists")
        transaction_root.mkdir(parents=False)
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
        if not root.exists():
            return ()
        recovered: list[str] = []
        transaction_roots = tuple(sorted(root.glob(".promotion-*"))) + tuple(
            sorted(root.glob(".acceptance-*"))
        )
        for transaction_root in transaction_roots:
            if not transaction_root.is_dir():
                continue
            journal = transaction_root / "JOURNAL.json"
            if not journal.is_file():
                raise StateConflictError("promotion transaction lacks a journal")
            payload = json.loads(journal.read_text(encoding="utf-8"))
            state = payload.get("state")
            if state in {"finalized", "local_acceptance_complete", "rolled_back"}:
                continue
            prior_hash = payload.get("prior_sha256")
            prepared_hash = payload.get("prepared_sha256")
            if not isinstance(prior_hash, str) or not isinstance(prepared_hash, str):
                raise StateConflictError("promotion journal lacks tree hashes")
            active = root / "ACTIVE"
            prepared = transaction_root / "PREPARED_ACTIVE"
            backup = transaction_root / "PRIOR_ACTIVE"
            active_hash = self.tree_sha256(active) if active.is_dir() else None
            prepared_actual = self.tree_sha256(prepared) if prepared.is_dir() else None
            backup_actual = self.tree_sha256(backup) if backup.is_dir() else None
            for actual, expected, label in (
                (prepared_actual, prepared_hash, "prepared"),
                (backup_actual, prior_hash, "backup"),
            ):
                if actual is not None and actual != expected:
                    raise StateConflictError(f"promotion {label} tree hash changed")
            if active_hash == prepared_hash:
                if backup.is_dir():
                    shutil.rmtree(backup)
                if prepared.is_dir():
                    shutil.rmtree(prepared)
                terminal = "active_installed"
            elif active_hash is None and prepared_actual == prepared_hash and backup_actual == prior_hash:
                os.replace(prepared, active)
                shutil.rmtree(backup)
                terminal = "active_installed"
            elif active_hash == prior_hash:
                if prepared.is_dir():
                    shutil.rmtree(prepared)
                if backup.is_dir():
                    shutil.rmtree(backup)
                terminal = "rolled_back"
            elif active_hash is None and backup_actual == prior_hash and prepared_actual is None:
                os.replace(backup, active)
                terminal = "rolled_back"
            else:
                raise StateConflictError("promotion recovery cannot verify an exact tree")
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
            recovered.append(str(payload.get("turn_id", transaction_root.name)))
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
        active = branch_root / "ACTIVE"
        transaction_root = transaction_root or Path(
            mkdtemp(prefix=f".promotion-{turn_id}-", dir=branch_root)
        )
        prepared = transaction_root / "PREPARED_ACTIVE"
        shutil.copytree(candidate_active, prepared)
        backup = transaction_root / "PRIOR_ACTIVE"
        journal = transaction_root / "JOURNAL.json"
        base = journal_base or {
            "schema_version": "cera.continuous_world_promotion_journal.v2",
            "turn_id": turn_id,
            "prior_sha256": self.tree_sha256(active),
            "prepared_sha256": self.tree_sha256(prepared),
        }
        self._write_json(journal, {**base, "state": "prepared"})
        self._failpoint("journal_created")
        os.replace(active, backup)
        self._write_json(journal, {**base, "state": "active_moved_to_backup"})
        self._failpoint("active_moved_to_backup")
        try:
            os.replace(prepared, active)
        except Exception:
            os.replace(backup, active)
            self._write_json(journal, {**base, "state": "rolled_back"})
            raise
        self._write_json(journal, {**base, "state": "prepared_active_installed"})
        self._failpoint("prepared_active_installed")
        shutil.rmtree(backup)
        self._write_json(journal, {**base, "state": "prior_backup_removed"})
        self._failpoint("prior_backup_removed")
        self._write_json(journal, {**base, "state": "committed"})
        self._failpoint("committed")
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
        if self.tree_sha256(active) != payload.get("prepared_sha256"):
            raise StateConflictError("acceptance journal ACTIVE hash changed")
        for label in ("accepted_pair", "accepted_event"):
            relative = payload.get(f"{label}_relative_path")
            expected = payload.get(f"{label}_sha256")
            if not isinstance(relative, str) or not isinstance(expected, str):
                raise StateConflictError(f"acceptance journal lacks {label} identity")
            target = (active / relative).resolve()
            if (
                active.resolve() not in target.parents
                or not target.is_file()
                or target.is_symlink()
                or text_sha256(target.read_text(encoding="utf-8")) != expected
            ):
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
            ContinuousSessionSnapshotStore,
        )
        if not isinstance(snapshot_receipt, ContinuousSessionSnapshotReceiptV1):
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
        expected_path = store.path_for(turn_id, provider_thread_sha256).resolve()
        if reference_path.is_symlink():
            raise StateConflictError(
                "stable accepted-reference custody path is a symlink"
            )
        supplied_path = reference_path.resolve()
        if supplied_path != expected_path or not supplied_path.is_file():
            raise StateConflictError("stable accepted-reference custody path changed")
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
                self.branch_root(world_id, branch_id).resolve()
            ).as_posix(),
            stable_reference_set_sha256=text_sha256(
                supplied_path.read_text(encoding="utf-8")
            ),
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
        if path.is_file():
            if json.loads(path.read_text(encoding="utf-8")) != to_primitive(value):
                raise StateConflictError(f"{label} changed during acceptance recovery")
            return
        self._write_json(path, value)

    @staticmethod
    def _write_json(path: Path, value: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".tmp")
        with temporary.open("wb") as stream:
            stream.write(canonical_bytes(value) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)


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
        "validator_cited_accepted_evidence.json",
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

    def lean_planner_context(self) -> dict[str, Any]:
        """Current-scene handoff without replaying prior accepted pairs."""

        return {
            "schema_version": "cera.lean_scene_change_context.v1",
            "completed_scene_id": self.previous_scene_summary.completed_scene_id,
            "accepted_turn_ids": self.previous_scene_summary.accepted_turn_ids,
            "shortest_complete_summary": (
                self.previous_scene_summary.shortest_complete_summary
            ),
            "ending_state": self.previous_scene_summary.ending_state,
            "transition_context": self.previous_scene_summary.transition_context,
            "summary_authority_classification": (
                self.previous_scene_summary_view.authority_classification
            ),
            "summary_revision": self.previous_scene_summary_view.summary_revision,
            "regeneration_identity_sha256": (
                self.previous_scene_summary_view.regeneration_identity_sha256
            ),
            "first_user_message_of_new_scene": (
                self.first_user_message_of_new_scene
            ),
            "exact_prior_pairs_included": False,
        }


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
