"""Pure cumulative reducer for accepted Pi Scene branch events."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Any

from cera.errors import StateConflictError

from ._branch_state_models import (
    AcceptedBranchEventV1,
    BranchLineageHopV1,
    BranchStateCheckpointV1,
    DurableBranchChangeV1,
    GenesisBranchStateV1,
    ProvisionalCanonLineageEntryV1,
    records_from_mapping,
    required_text,
)
from ._branch_state_payloads import accepted_event_from_payload


class BranchStateReducerV1:
    """Reduce immutable Genesis plus the complete ordered accepted event stream."""

    def __init__(self, genesis: GenesisBranchStateV1) -> None:
        self.genesis = genesis

    def new_root(
        self,
        *,
        branch_id: str,
        scene_id: str | None = None,
    ) -> BranchStateCheckpointV1:
        """Create an isolated new-chat root from Genesis, never another chat."""

        required_text(branch_id, "root branch_id")
        if scene_id is not None:
            required_text(scene_id, "root scene_id")
        return BranchStateCheckpointV1(
            schema_version=BranchStateCheckpointV1.SCHEMA_VERSION,
            world_id=self.genesis.world_id,
            branch_id=branch_id,
            genesis_revision=self.genesis.genesis_revision,
            genesis_sha256=self.genesis.genesis_sha256,
            scene_id=scene_id or self.genesis.scene_id,
            accepted_order=0,
            last_accepted_turn_id=None,
            last_accepted_receipt_sha256=None,
            accepted_present_character_ids=self.genesis.accepted_present_character_ids,
            characters=self.genesis.characters,
            relationships=self.genesis.relationships,
            memories=self.genesis.memories,
            durable_changes=(),
            resulting_public_state=self.genesis.resulting_public_state,
            unresolved_threads=(),
            adult_public_continuity=(),
            accepted_lineage=(),
            provisional_canon_lineage=(),
            pending_ordinary_recording_turn_ids=(),
            pending_adult_projection_turn_ids=(),
            branch_lineage=(),
        )

    def reduce(
        self,
        *,
        branch_id: str,
        payloads: Sequence[Mapping[str, Any]],
        scene_id: str | None = None,
    ) -> BranchStateCheckpointV1:
        return self.advance(
            self.new_root(branch_id=branch_id, scene_id=scene_id),
            payloads=payloads,
        )

    def advance(
        self,
        checkpoint: BranchStateCheckpointV1,
        *,
        payloads: Sequence[Mapping[str, Any]],
    ) -> BranchStateCheckpointV1:
        self._validate_checkpoint(checkpoint)
        current = checkpoint
        for payload in payloads:
            current = self._apply(current, accepted_event_from_payload(payload))
        return current

    def reconcile_recording_completions(
        self,
        checkpoint: BranchStateCheckpointV1,
        *,
        payloads: Sequence[Mapping[str, Any]],
    ) -> BranchStateCheckpointV1:
        """Replace pending derived state by replaying the same accepted lineage.

        Recorder completion does not append a new accepted event.  Replaying
        from Genesis is therefore required so an older pending turn's effects
        are applied in their original order rather than after newer turns.
        Only pending-to-complete movement is allowed; accepted ancestry and the
        branch's explicit fork/provisional custody remain byte-for-byte equal.
        """

        self._validate_checkpoint(checkpoint)
        replay_branch_id = (
            checkpoint.accepted_lineage[0].branch_id
            if checkpoint.accepted_lineage
            else checkpoint.branch_id
        )
        rebuilt = self.reduce(
            branch_id=replay_branch_id,
            payloads=payloads,
            scene_id=self.genesis.scene_id,
        )
        if rebuilt.accepted_lineage != checkpoint.accepted_lineage:
            raise StateConflictError(
                "recording reconciliation changed accepted branch lineage"
            )
        if (
            rebuilt.accepted_order != checkpoint.accepted_order
            or rebuilt.last_accepted_turn_id != checkpoint.last_accepted_turn_id
            or rebuilt.last_accepted_receipt_sha256
            != checkpoint.last_accepted_receipt_sha256
        ):
            raise StateConflictError("recording reconciliation changed accepted head")
        if not set(rebuilt.pending_ordinary_recording_turn_ids).issubset(
            checkpoint.pending_ordinary_recording_turn_ids
        ) or not set(rebuilt.pending_adult_projection_turn_ids).issubset(
            checkpoint.pending_adult_projection_turn_ids
        ):
            raise StateConflictError(
                "recording reconciliation introduced a new pending turn"
            )
        if checkpoint.provisional_canon_lineage[
            : len(rebuilt.provisional_canon_lineage)
        ] != rebuilt.provisional_canon_lineage:
            raise StateConflictError(
                "recording reconciliation changed provisional-canon lineage"
            )
        return replace(
            rebuilt,
            branch_id=checkpoint.branch_id,
            provisional_canon_lineage=checkpoint.provisional_canon_lineage,
            branch_lineage=checkpoint.branch_lineage,
        )

    def fork(
        self,
        checkpoint: BranchStateCheckpointV1,
        *,
        child_branch_id: str,
    ) -> BranchStateCheckpointV1:
        """Fork an accepted checkpoint, including pending/provisional lineage."""

        self._validate_checkpoint(checkpoint)
        required_text(child_branch_id, "child branch_id")
        if checkpoint.last_accepted_turn_id is None:
            raise StateConflictError("branch fork requires an accepted checkpoint")
        hop = BranchLineageHopV1(
            parent_branch_id=checkpoint.branch_id,
            child_branch_id=child_branch_id,
            forked_at_accepted_order=checkpoint.accepted_order,
            forked_at_accepted_turn_id=checkpoint.last_accepted_turn_id,
            parent_checkpoint_sha256=checkpoint.checkpoint_sha256,
        )
        return replace(
            checkpoint,
            branch_id=child_branch_id,
            branch_lineage=checkpoint.branch_lineage + (hop,),
        )

    def append_provisional_canon(
        self,
        checkpoint: BranchStateCheckpointV1,
        *,
        entry: ProvisionalCanonLineageEntryV1,
    ) -> BranchStateCheckpointV1:
        """Append an explicit lower-confidence lineage event by identity only."""

        self._validate_checkpoint(checkpoint)
        existing = {
            value.lineage_entry_id: value
            for value in checkpoint.provisional_canon_lineage
        }
        if entry.lineage_entry_id in existing:
            raise StateConflictError("provisional-canon lineage identity was reused")
        if entry.parent_lineage_entry_id is None:
            if any(
                value.provisional_canon_id == entry.provisional_canon_id
                for value in existing.values()
            ):
                raise StateConflictError("existing provisional canon requires a parent")
            if entry.status != "unresolved":
                raise StateConflictError("new provisional canon must begin unresolved")
        else:
            parent = existing.get(entry.parent_lineage_entry_id)
            if parent is None:
                raise StateConflictError("provisional-canon parent is unknown")
            if parent.provisional_canon_id != entry.provisional_canon_id:
                raise StateConflictError("provisional-canon identity changed in lineage")
            if parent.status != "unresolved":
                raise StateConflictError("resolved provisional canon cannot advance")
            if entry.status == "unresolved":
                raise StateConflictError("provisional-canon successor must resolve")
        return replace(
            checkpoint,
            provisional_canon_lineage=checkpoint.provisional_canon_lineage + (entry,),
        )

    def _validate_checkpoint(self, checkpoint: BranchStateCheckpointV1) -> None:
        if checkpoint.world_id != self.genesis.world_id:
            raise StateConflictError("checkpoint belongs to another world")
        if checkpoint.genesis_revision != self.genesis.genesis_revision:
            raise StateConflictError("checkpoint Genesis revision changed")
        if checkpoint.genesis_sha256 != self.genesis.genesis_sha256:
            raise StateConflictError("checkpoint Genesis baseline changed")

    def _apply(
        self,
        checkpoint: BranchStateCheckpointV1,
        event: AcceptedBranchEventV1,
    ) -> BranchStateCheckpointV1:
        self._validate_event_parent(checkpoint, event)
        if not event.derived_state_complete:
            return self._apply_pending(checkpoint, event)

        present = self._apply_presence(checkpoint, event)
        characters = checkpoint.character_mapping()
        relationships = checkpoint.relationship_mapping()
        memories = checkpoint.memory_mapping()
        self._ensure_present_characters(characters, present)
        self._apply_durable(event, characters, relationships, memories)
        self._apply_recorder_views(event, relationships, memories)
        if event.resulting_public_state is None:  # guarded by event validation
            raise AssertionError("complete accepted event lost its public state")
        return BranchStateCheckpointV1(
            schema_version=BranchStateCheckpointV1.SCHEMA_VERSION,
            world_id=checkpoint.world_id,
            branch_id=checkpoint.branch_id,
            genesis_revision=checkpoint.genesis_revision,
            genesis_sha256=checkpoint.genesis_sha256,
            scene_id=event.scene_id,
            accepted_order=event.accepted_order,
            last_accepted_turn_id=event.accepted_turn_id,
            last_accepted_receipt_sha256=event.receipt_sha256,
            accepted_present_character_ids=present,
            characters=records_from_mapping(characters),
            relationships=records_from_mapping(relationships),
            memories=records_from_mapping(memories),
            durable_changes=checkpoint.durable_changes + event.durable_changes,
            resulting_public_state=event.resulting_public_state,
            unresolved_threads=event.unresolved_threads,
            adult_public_continuity=(
                checkpoint.adult_public_continuity + event.adult_public_continuity
            ),
            accepted_lineage=checkpoint.accepted_lineage + (event.accepted_lineage_entry,),
            provisional_canon_lineage=_append_event_provisional(
                checkpoint.provisional_canon_lineage,
                event,
            ),
            pending_ordinary_recording_turn_ids=(
                checkpoint.pending_ordinary_recording_turn_ids
            ),
            pending_adult_projection_turn_ids=(
                checkpoint.pending_adult_projection_turn_ids
            ),
            branch_lineage=checkpoint.branch_lineage,
        )

    @staticmethod
    def _validate_event_parent(
        checkpoint: BranchStateCheckpointV1,
        event: AcceptedBranchEventV1,
    ) -> None:
        if event.world_id != checkpoint.world_id:
            raise StateConflictError("accepted event belongs to another world")
        if event.branch_id != checkpoint.branch_id:
            raise StateConflictError("accepted event belongs to another branch")
        if event.accepted_order != checkpoint.accepted_order + 1:
            raise StateConflictError("accepted branch order is not contiguous")
        if event.parent_accepted_turn_id != checkpoint.last_accepted_turn_id:
            raise StateConflictError("accepted event parent differs from checkpoint")
        if event.parent_accepted_head_sha256 != checkpoint.last_accepted_receipt_sha256:
            raise StateConflictError("accepted event parent hash differs from checkpoint")

    @staticmethod
    def _apply_pending(
        checkpoint: BranchStateCheckpointV1,
        event: AcceptedBranchEventV1,
    ) -> BranchStateCheckpointV1:
        pending_ordinary = checkpoint.pending_ordinary_recording_turn_ids
        pending_adult = checkpoint.pending_adult_projection_turn_ids
        if event.route == "ordinary":
            pending_ordinary += (event.accepted_turn_id,)
        else:
            pending_adult += (event.accepted_turn_id,)
        return replace(
            checkpoint,
            accepted_order=event.accepted_order,
            last_accepted_turn_id=event.accepted_turn_id,
            last_accepted_receipt_sha256=event.receipt_sha256,
            accepted_lineage=checkpoint.accepted_lineage + (event.accepted_lineage_entry,),
            provisional_canon_lineage=_append_event_provisional(
                checkpoint.provisional_canon_lineage,
                event,
            ),
            pending_ordinary_recording_turn_ids=pending_ordinary,
            pending_adult_projection_turn_ids=pending_adult,
        )

    @staticmethod
    def _apply_presence(
        checkpoint: BranchStateCheckpointV1,
        event: AcceptedBranchEventV1,
    ) -> tuple[str, ...]:
        present = list(checkpoint.accepted_present_character_ids)
        for change in event.presence_changes:
            # Presence is set state; repeated provider edges are idempotent.
            if change.direction == "enter":
                if change.character_id in present:
                    continue
                present.append(change.character_id)
            else:
                if change.character_id not in present:
                    continue
                present.remove(change.character_id)
        return tuple(present)

    @staticmethod
    def _ensure_present_characters(
        characters: dict[str, dict[str, Any]],
        present: Sequence[str],
    ) -> None:
        for character in present:
            if character == "character:ted" or character in characters:
                continue
            characters[character] = {
                "character_id": character,
                "genesis_record_available": False,
                "accepted_branch_changes": [],
            }

    @staticmethod
    def _apply_durable(
        event: AcceptedBranchEventV1,
        characters: dict[str, dict[str, Any]],
        relationships: dict[str, dict[str, Any]],
        memories: dict[str, dict[str, Any]],
    ) -> None:
        for change in event.durable_changes:
            change_value = _durable_change_mapping(change)
            if change.kind == "character_development":
                for character in change.subject_ids:
                    _append_overlay(
                        characters,
                        character,
                        "accepted_branch_changes",
                        change_value,
                        fallback={
                            "character_id": character,
                            "genesis_record_available": False,
                        },
                    )
            elif change.kind == "relationship":
                _append_overlay(
                    relationships,
                    change.target_key,
                    "accepted_branch_changes",
                    change_value,
                    fallback={
                        "target_key": change.target_key,
                        "participants": list(change.subject_ids),
                        "visibility": change.visibility,
                        "knowledge_owner_id": change.knowledge_owner_id,
                    },
                )
            elif change.kind == "knowledge":
                memories[f"knowledge:{event.accepted_order:08d}:{change.change_key}"] = (
                    change_value
                )

    @staticmethod
    def _apply_recorder_views(
        event: AcceptedBranchEventV1,
        relationships: dict[str, dict[str, Any]],
        memories: dict[str, dict[str, Any]],
    ) -> None:
        for index, value in enumerate(event.relationship_changes, start=1):
            relationships[f"accepted:{event.accepted_order:08d}:relationship:{index:04d}"] = {
                "accepted_turn_id": event.accepted_turn_id,
                "source_kind": "ordinary_recorder_projection",
                "visibility": "branch_internal_unspecified",
                "concise_change": value,
            }
        for index, value in enumerate(event.knowledge_changes, start=1):
            memories[f"accepted:{event.accepted_order:08d}:knowledge:{index:04d}"] = {
                "accepted_turn_id": event.accepted_turn_id,
                "source_kind": "ordinary_recorder_projection",
                "visibility": "branch_internal_unspecified",
                "concise_change": value,
            }
        for index, value in enumerate(event.secondary_canon, start=1):
            memories[f"accepted:{event.accepted_order:08d}:canon:{index:04d}"] = {
                "accepted_turn_id": event.accepted_turn_id,
                "source_kind": "ordinary_secondary_canon",
                "visibility": "branch_internal_unspecified",
                "concise_change": value,
            }
        for continuity in event.adult_public_continuity:
            memories[f"adult:{event.accepted_order:08d}:{continuity.event_key}"] = {
                "accepted_turn_id": continuity.accepted_turn_id,
                "event_key": continuity.event_key,
                "authority": "accepted_adult_filtered_projection",
                "visibility": "public",
                "non_explicit_summary": continuity.non_explicit_summary,
                "lasting_story_meaning": continuity.lasting_story_meaning,
            }


def _append_event_provisional(
    lineage: tuple[ProvisionalCanonLineageEntryV1, ...],
    event: AcceptedBranchEventV1,
) -> tuple[ProvisionalCanonLineageEntryV1, ...]:
    entry = event.provisional_canon_entry
    if entry is None:
        return lineage
    if any(
        value.lineage_entry_id == entry.lineage_entry_id
        or value.provisional_canon_id == entry.provisional_canon_id
        for value in lineage
    ):
        raise StateConflictError("accepted provisional-canon identity was reused")
    return lineage + (entry,)


def _durable_change_mapping(value: DurableBranchChangeV1) -> dict[str, Any]:
    return {
        "accepted_turn_id": value.accepted_turn_id,
        "change_key": value.change_key,
        "kind": value.kind,
        "subject_ids": list(value.subject_ids),
        "concise_change": value.concise_change,
        "target_key": value.target_key,
        "visibility": value.visibility,
        "knowledge_owner_id": value.knowledge_owner_id,
        "source_kind": value.source_kind,
    }


def _append_overlay(
    records: dict[str, dict[str, Any]],
    record_id: str,
    field_name: str,
    value: Mapping[str, Any],
    *,
    fallback: Mapping[str, Any],
) -> None:
    current = dict(records.get(record_id, fallback))
    existing = current.get(field_name, [])
    if not isinstance(existing, list):
        raise StateConflictError(f"branch-state overlay {field_name} is not an array")
    current[field_name] = [*existing, dict(value)]
    records[record_id] = current
