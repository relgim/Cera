"""Sequence-first projection onto the existing atomic continuous-world store."""

from __future__ import annotations

import copy
from dataclasses import dataclass
import json
from pathlib import Path
from tempfile import mkdtemp
from typing import ClassVar

from cera.errors import ContractValidationError, StateConflictError
from cera.serialization import canonical_sha256, text_sha256, to_primitive
from cera.continuous.contracts import (
    PersistenceRecordClass,
    WorldEditOperationKind,
    WorldEditOperationV1,
)
from cera.continuous.record_policy import (
    validate_persistence_field_path,
    validate_post_edit_record,
)
from cera.continuous.world import (
    ContinuousWorldStore,
    _apply_json_operation,
    _identity_filename,
)

from .contracts import (
    SequenceDraftV1,
    SequenceFirstCandidateV1,
    TargetOperationKind,
    apply_presence_changes,
)


SEQUENCE_FIRST_ACCEPTANCE_JOURNAL = "cera.sequence_first.acceptance_journal.v1"


@dataclass(frozen=True, slots=True)
class SequenceFirstPromotionReceiptV1:
    SCHEMA_VERSION: ClassVar[str] = "cera.sequence_first.promotion_receipt.v1"

    world_id: str
    branch_id: str
    turn_id: str
    transaction_id: str
    candidate_sha256: str
    active_before_sha256: str
    active_after_sha256: str
    changed_files: tuple[str, ...]
    accepted: bool

    @property
    def receipt_sha256(self) -> str:
        return canonical_sha256(self)


@dataclass(frozen=True, slots=True)
class SequenceFirstAcceptedHeadV1:
    world_id: str
    branch_id: str
    scene_id: str
    accepted_turn_id: str | None
    active_head_sha256: str
    accepted_present_character_ids: tuple[str, ...]
    current_public_scene_state: str
    prior_realized_sequence: SequenceDraftV1 | None
    unresolved_threads: tuple[str, ...]


def _value_at_pointer(document, pointer: str):
    parts = tuple(
        part.replace("~1", "/").replace("~0", "~")
        for part in pointer[1:].split("/")
    )
    current = document
    for part in parts:
        if isinstance(current, dict) and part in current:
            current = current[part]
        elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
            current = current[int(part)]
        else:
            raise StateConflictError("persistence target field is unavailable")
    return current


class SequenceFirstWorldTransaction:
    """Mechanically project approved target keys through existing atomic promotion."""

    def __init__(self, store: ContinuousWorldStore) -> None:
        self.store = store

    def commit(
        self,
        candidate: SequenceFirstCandidateV1,
        *,
        expected_parent_accepted_turn_id: str | None,
        creator_accepted: bool,
    ) -> str:
        if not creator_accepted:
            raise StateConflictError("creator acceptance is required")
        custody = candidate.custody
        if custody.parent_accepted_turn_id != expected_parent_accepted_turn_id:
            raise StateConflictError("candidate parent custody changed")
        root = self.store.initialize(custody.world_id, custody.branch_id)
        active = root / "ACTIVE"
        before = self.store.tree_sha256(active)
        if custody.accepted_head_sha256 is not None and before != custody.accepted_head_sha256:
            raise StateConflictError("accepted ACTIVE head changed before commit")
        state = json.loads((active / "WORLD_STATE.json").read_text(encoding="utf-8"))
        accepted_turns = tuple(state.get("accepted_turn_ids", ()))
        actual_parent = accepted_turns[-1] if accepted_turns else None
        if actual_parent != expected_parent_accepted_turn_id:
            raise StateConflictError("accepted turn parent changed before commit")

        view = self.store.create_candidate(
            custody.world_id,
            custody.branch_id,
            custody.turn_id,
        )
        target_by_key = {
            target.target_key: target for target in custody.persistence_targets
        }
        changed_files: set[str] = set()
        loaded: dict[str, dict] = {}
        originals: dict[str, dict] = {}

        for change in candidate.realized_sequence.semantic.durable_changes:
            target = target_by_key.get(change.target_key)
            if target is None:
                raise StateConflictError("realized change lacks private target custody")
            if tuple(change.subject_ids) != tuple(target.target_subject_ids):
                raise StateConflictError("durable change subjects changed target custody")
            record_class = PersistenceRecordClass(target.record_class)
            validate_persistence_field_path(record_class, target.field_path)
            path = view.root / "ACTIVE_VIEW" / target.target_file.replace("\\", "/")
            if target.target_file not in loaded:
                if not path.is_file():
                    raise StateConflictError("approved persistence target is absent")
                document = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(document, dict):
                    raise StateConflictError("approved persistence target is not an object")
                loaded[target.target_file] = document
                originals[target.target_file] = copy.deepcopy(document)
            document = loaded[target.target_file]
            if document.get("_cera_revision") != target.expected_file_revision:
                raise StateConflictError("persistence target revision precondition failed")
            if target.operation is TargetOperationKind.REPLACE:
                prior = _value_at_pointer(document, target.field_path)
                if canonical_sha256(prior) != target.expected_prior_value_sha256:
                    raise StateConflictError("persistence target prior value changed")
            source_items = tuple(
                item.item_key
                for item in candidate.realized_sequence.semantic.items
                if change.change_key in item.durable_change_keys
            )
            if len(source_items) != 1:
                raise ContractValidationError(
                    "durable change must be referenced by exactly one realized item"
                )
            operation = WorldEditOperationV1(
                operation_key=change.change_key,
                target_file=target.target_file,
                expected_file_revision=target.expected_file_revision,
                operation=WorldEditOperationKind(target.operation.value),
                field_path=target.field_path,
                value=change.concise_change,
                reason=f"sequence-first durable change {change.change_key}",
                source_final_sequence_item=source_items[0],
            )
            _apply_json_operation(document, operation)
            changed_files.add(target.target_file)

        for target_file in sorted(changed_files):
            document = loaded[target_file]
            targets = tuple(
                target
                for target in custody.persistence_targets
                if target.target_file == target_file
            )
            first = targets[0]
            if any(
                (
                    target.record_class,
                    target.target_record_id,
                    target.target_subject_ids,
                    target.expected_file_revision,
                )
                != (
                    first.record_class,
                    first.target_record_id,
                    first.target_subject_ids,
                    first.expected_file_revision,
                )
                for target in targets[1:]
            ):
                raise StateConflictError("one file has conflicting target custody")
            document["_cera_revision"] = first.expected_file_revision + 1
            validate_post_edit_record(
                record_class=PersistenceRecordClass(first.record_class),
                before=originals[target_file],
                after=document,
                expected_record_id=first.target_record_id,
                expected_subject_ids=first.target_subject_ids,
                expected_revision=first.expected_file_revision + 1,
            )
            self.store._write_json(
                view.root / "ACTIVE_VIEW" / target_file.replace("\\", "/"),
                document,
            )

        event_path = (
            view.root
            / "ACTIVE_VIEW"
            / "Events"
            / _identity_filename(custody.turn_id, ".sequence_first.json")
        )
        resulting_presence = apply_presence_changes(
            candidate.accepted_present_character_ids,
            candidate.realized_sequence.semantic,
        )
        self.store._write_json(
            event_path,
            {
                "schema_version": "cera.sequence_first.accepted_story_artifact.v1",
                "_cera_revision": 1,
                "world_id": custody.world_id,
                "branch_id": custody.branch_id,
                "scene_id": custody.scene_id,
                "accepted_turn_id": custody.turn_id,
                "parent_accepted_turn_id": custody.parent_accepted_turn_id,
                "story_text": candidate.writer_response.story_text,
                "realized_sequence": to_primitive(candidate.realized_sequence.semantic),
                "resulting_present_character_ids": resulting_presence,
                "candidate_sha256": candidate.candidate_sha256,
            },
        )
        self.store._increment_world_state(view.root / "ACTIVE_VIEW", custody.turn_id)
        world_state_path = view.root / "ACTIVE_VIEW" / "WORLD_STATE.json"
        world_state = json.loads(world_state_path.read_text(encoding="utf-8"))
        world_state["current_scene_id"] = custody.scene_id
        world_state["present_character_ids"] = list(resulting_presence)
        self.store._write_json(world_state_path, world_state)
        self.store._rebuild_index(view.root / "ACTIVE_VIEW")
        after = self.store.tree_sha256(view.root / "ACTIVE_VIEW")
        receipt = SequenceFirstPromotionReceiptV1(
            world_id=custody.world_id,
            branch_id=custody.branch_id,
            turn_id=custody.turn_id,
            transaction_id=custody.transaction_id,
            candidate_sha256=candidate.candidate_sha256,
            active_before_sha256=before,
            active_after_sha256=after,
            changed_files=tuple(sorted(changed_files)),
            accepted=True,
        )
        transaction_root = Path(
            mkdtemp(
                prefix=f".acceptance-sequence-first-{custody.turn_id}-",
                dir=root,
            )
        )
        event_relative = event_path.relative_to(view.root / "ACTIVE_VIEW").as_posix()
        journal_base = {
            "schema_version": SEQUENCE_FIRST_ACCEPTANCE_JOURNAL,
            "turn_id": custody.turn_id,
            "prior_sha256": before,
            "prepared_sha256": after,
            "accepted_event_relative_path": event_relative,
            "accepted_event_sha256": text_sha256(event_path.read_text(encoding="utf-8")),
            "promotion_receipt_payload": to_primitive(receipt),
        }
        with self.store._lock:
            if self.store.tree_sha256(active) != before:
                raise StateConflictError("ACTIVE changed during sequence-first validation")
            self.store._promote_directory(
                root,
                view.root / "ACTIVE_VIEW",
                custody.turn_id,
                transaction_root=transaction_root,
                journal_base=journal_base,
            )
        finish_sequence_first_acceptance(self.store, root, transaction_root)
        return custody.turn_id

    def load_accepted_head(
        self,
        *,
        world_id: str,
        branch_id: str,
    ) -> SequenceFirstAcceptedHeadV1:
        """Restart reconstruction from accepted artifacts, never provider history."""

        from cera.schema import from_mapping

        root = self.store.initialize(world_id, branch_id)
        active = root / "ACTIVE"
        state = json.loads((active / "WORLD_STATE.json").read_text(encoding="utf-8"))
        accepted_turns = tuple(str(value) for value in state.get("accepted_turn_ids", ()))
        turn_id = accepted_turns[-1] if accepted_turns else None
        if turn_id is None:
            return SequenceFirstAcceptedHeadV1(
                world_id=world_id,
                branch_id=branch_id,
                scene_id=str(state["current_scene_id"]),
                accepted_turn_id=None,
                active_head_sha256=self.store.tree_sha256(active),
                accepted_present_character_ids=tuple(
                    str(value) for value in state.get("present_character_ids", ())
                ),
                current_public_scene_state="No sequence-first turn has been accepted.",
                prior_realized_sequence=None,
                unresolved_threads=(),
            )
        event_path = active / "Events" / _identity_filename(
            turn_id,
            ".sequence_first.json",
        )
        if not event_path.is_file():
            raise StateConflictError(
                "accepted sequence-first turn lacks its immutable story artifact"
            )
        event = json.loads(event_path.read_text(encoding="utf-8"))
        if (
            event.get("world_id") != world_id
            or event.get("branch_id") != branch_id
            or event.get("accepted_turn_id") != turn_id
        ):
            raise StateConflictError("accepted sequence-first artifact changed scope")
        realized = from_mapping(SequenceDraftV1, event["realized_sequence"])
        event_presence = tuple(
            str(value) for value in event.get("resulting_present_character_ids", ())
        )
        state_presence = tuple(
            str(value) for value in state.get("present_character_ids", ())
        )
        if event_presence != state_presence:
            raise StateConflictError("accepted presence and world state diverged")
        return SequenceFirstAcceptedHeadV1(
            world_id=world_id,
            branch_id=branch_id,
            scene_id=str(event["scene_id"]),
            accepted_turn_id=turn_id,
            active_head_sha256=self.store.tree_sha256(active),
            accepted_present_character_ids=event_presence,
            current_public_scene_state=realized.resulting_public_state,
            prior_realized_sequence=realized,
            unresolved_threads=realized.unresolved_threads,
        )


def finish_sequence_first_acceptance(
    store: ContinuousWorldStore,
    branch_root: Path,
    transaction_root: Path,
) -> None:
    """Finish or recover the receipt after the atomic ACTIVE swap."""

    journal_path = transaction_root / "JOURNAL.json"
    payload = json.loads(journal_path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != SEQUENCE_FIRST_ACCEPTANCE_JOURNAL:
        raise StateConflictError("sequence-first acceptance journal changed")
    active = branch_root / "ACTIVE"
    if store.tree_sha256(active) != payload.get("prepared_sha256"):
        raise StateConflictError("sequence-first accepted ACTIVE hash changed")
    event_path = active / str(payload["accepted_event_relative_path"])
    if text_sha256(event_path.read_text(encoding="utf-8")) != payload.get(
        "accepted_event_sha256"
    ):
        raise StateConflictError("sequence-first accepted event changed")
    receipt_payload = payload.get("promotion_receipt_payload")
    if not isinstance(receipt_payload, dict):
        raise StateConflictError("sequence-first journal lacks receipt")
    turn_id = str(payload["turn_id"])
    receipt_path = branch_root / "CANDIDATES" / turn_id / "PROMOTION_RECEIPT.json"
    store._write_idempotent_json(
        receipt_path,
        receipt_payload,
        "sequence-first promotion receipt",
    )
    store._write_json(
        journal_path,
        {
            **payload,
            "state": "finalized",
            "active_sha256": store.tree_sha256(active),
        },
    )
