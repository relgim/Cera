from __future__ import annotations

import unittest

from cera.pi_scene.branch_state import (
    BranchStateCheckpointV1,
    BranchStateReducerV1,
    GenesisBranchStateV1,
    ProvisionalCanonLineageEntryV1,
)
from cera.serialization import canonical_json, text_sha256

WORLD_ID = "world:branch-state-test"
ROOT_BRANCH = "branch:main"


def _genesis() -> GenesisBranchStateV1:
    return GenesisBranchStateV1.from_mappings(
        world_id=WORLD_ID,
        genesis_revision="genesis:test:v1",
        scene_id="scene:kitchen",
        accepted_present_character_ids=("character:ted", "character:hana"),
        resulting_public_state="Ted and Hana are in the kitchen.",
        characters={
            "character:hana": {
                "name": "Hana",
                "voice": "gentle",
            },
            "character:sakura": {
                "name": "Sakura",
                "voice": "precise",
            },
        },
        relationships={
            "relationship:ted-hana": {
                "participants": ["character:ted", "character:hana"],
                "summary": "They are getting acquainted.",
            }
        },
        memories={},
    )


def _ordinary_payload(
    order: int,
    *,
    branch_id: str = ROOT_BRANCH,
    parent_turn_id: str | None,
    parent_receipt_sha256: str | None,
    public_state: str | None = None,
    unresolved: tuple[str, ...] | None = None,
    presence_changes: tuple[dict[str, object], ...] = (),
    durable_changes: tuple[dict[str, object], ...] = (),
    relationship_changes: tuple[str, ...] = (),
    knowledge_changes: tuple[str, ...] = (),
    secondary_canon: tuple[str, ...] = (),
    recorded_durable: tuple[str, ...] = (),
    pending: bool = False,
) -> dict[str, object]:
    turn_id = f"turn:{branch_id}:{order:04d}"
    item_keys = [f"item:{order:04d}:a", f"item:{order:04d}:b"]
    authority = {
        "items": [
            {"item_key": item_keys[0], "kind": "dialogue_intent"},
            {"item_key": item_keys[1], "kind": "stopping_boundary"},
        ],
        "durable_changes": list(durable_changes),
        "presence_changes": list(presence_changes),
        "resulting_public_state": public_state or f"Accepted public state {order}.",
        "unresolved_threads": list(unresolved or (f"thread:{order}",)),
        "stopping_boundary": "Return the floor to Ted.",
    }
    authority_json = canonical_json(authority)
    receipt_sha256 = text_sha256(f"accepted-receipt:{branch_id}:{order}")
    receipt = {
        "accepted_turn_id": turn_id,
        "parent_accepted_turn_id": parent_turn_id,
        "parent_accepted_head_sha256": parent_receipt_sha256,
        "world_id": WORLD_ID,
        "branch_id": branch_id,
        "scene_id": "scene:kitchen",
        "generation": order,
        "route": "ordinary",
        "primary_authority_json": authority_json,
        "primary_authority_sha256": text_sha256(authority_json),
        "candidate_sha256": text_sha256(f"candidate:{branch_id}:{order}"),
    }
    payload: dict[str, object] = {
        "receipt": receipt,
        "accepted_receipt_sha256": receipt_sha256,
        "recording_status": "pending_repair" if pending else "complete",
    }
    if not pending:
        payload["ordinary_record"] = {
            "schema_version": "cera.pi_scene.ordinary_record.v1",
            "primary_sequence_sha256": text_sha256(authority_json),
            "realized_item_keys": item_keys,
            "secondary_canon": list(secondary_canon),
            "resulting_public_state": public_state or f"Accepted public state {order}.",
            "relationship_changes": list(relationship_changes),
            "knowledge_changes": list(knowledge_changes),
            "durable_changes": list(recorded_durable),
            "unresolved_threads": list(unresolved or (f"thread:{order}",)),
        }
    return payload


def _chain_payloads(count: int, *, branch_id: str = ROOT_BRANCH) -> list[dict[str, object]]:
    payloads: list[dict[str, object]] = []
    parent_turn_id: str | None = None
    parent_sha256: str | None = None
    for order in range(1, count + 1):
        presence: tuple[dict[str, object], ...] = ()
        durable: tuple[dict[str, object], ...] = ()
        relationship: tuple[str, ...] = ()
        knowledge: tuple[str, ...] = ()
        secondary: tuple[str, ...] = ()
        if order == 1:
            durable = (
                {
                    "change_key": "hana-learns-code",
                    "kind": "knowledge",
                    "subject_ids": ["character:hana"],
                    "concise_change": "Hana knows the entry code changed.",
                    "target_key": "memory:hana-entry-code",
                    "visibility": "character_private",
                    "knowledge_owner_id": "character:hana",
                },
            )
            knowledge = ("Hana remembers that the entry code changed.",)
        elif order == 2:
            presence = (
                {
                    "character_id": "character:sakura",
                    "direction": "enter",
                    "effective_after_item_key": "item:0002:a",
                },
            )
        elif order == 3:
            durable = (
                {
                    "change_key": "ted-hana-trust",
                    "kind": "relationship",
                    "subject_ids": ["character:ted", "character:hana"],
                    "concise_change": "Their working trust increases slightly.",
                    "target_key": "relationship:ted-hana",
                    "visibility": "public",
                    "knowledge_owner_id": None,
                },
            )
            relationship = ("Ted and Hana now share a small practical trust.",)
        elif order == 4:
            durable = (
                {
                    "change_key": "hana-speaks-directly",
                    "kind": "character_development",
                    "subject_ids": ["character:hana"],
                    "concise_change": "Hana states one boundary directly.",
                    "target_key": "character:hana",
                    "visibility": "public",
                    "knowledge_owner_id": None,
                },
            )
            secondary = ("Hana kept the conversational floor long enough to clarify.",)
        elif order == 7:
            presence = (
                {
                    "character_id": "character:hana",
                    "direction": "leave",
                    "effective_after_item_key": "item:0007:b",
                },
            )
        payload = _ordinary_payload(
            order,
            branch_id=branch_id,
            parent_turn_id=parent_turn_id,
            parent_receipt_sha256=parent_sha256,
            public_state=f"Accepted public state {order}.",
            unresolved=(f"thread:{order}",),
            presence_changes=presence,
            durable_changes=durable,
            relationship_changes=relationship,
            knowledge_changes=knowledge,
            secondary_canon=secondary,
            recorded_durable=(f"Recorder durable {order}.",),
        )
        payloads.append(payload)
        receipt = payload["receipt"]
        assert isinstance(receipt, dict)
        parent_turn_id = str(receipt["accepted_turn_id"])
        parent_sha256 = str(payload["accepted_receipt_sha256"])
    return payloads


class BranchStateReducerTests(unittest.TestCase):
    def test_new_relationship_overlays_preserve_public_and_private_scope(self) -> None:
        payload = _ordinary_payload(
            1,
            branch_id=ROOT_BRANCH,
            parent_turn_id=None,
            parent_receipt_sha256=None,
            durable_changes=(
                {
                    "change_key": "public-relationship-change",
                    "kind": "relationship",
                    "subject_ids": ["character:ted", "character:hana"],
                    "concise_change": "A public relationship change.",
                    "target_key": "relationship:new-public",
                    "visibility": "public",
                    "knowledge_owner_id": None,
                },
                {
                    "change_key": "private-relationship-change",
                    "kind": "relationship",
                    "subject_ids": ["character:hana"],
                    "concise_change": "A private relationship change.",
                    "target_key": "relationship:new-private",
                    "visibility": "character_private",
                    "knowledge_owner_id": "character:hana",
                },
            ),
        )
        checkpoint = BranchStateReducerV1(_genesis()).reduce(
            branch_id=ROOT_BRANCH,
            payloads=[payload],
        )

        public = checkpoint.relationship_mapping()["relationship:new-public"]
        private = checkpoint.relationship_mapping()["relationship:new-private"]
        self.assertEqual(public["visibility"], "public")
        self.assertIsNone(public["knowledge_owner_id"])
        self.assertEqual(private["visibility"], "character_private")
        self.assertEqual(private["knowledge_owner_id"], "character:hana")

    def test_more_than_six_turns_reduce_cumulatively_without_mutating_genesis(self) -> None:
        genesis = _genesis()
        genesis_before = genesis.genesis_sha256
        payloads = _chain_payloads(8)
        checkpoint = BranchStateReducerV1(genesis).reduce(
            branch_id=ROOT_BRANCH,
            payloads=payloads,
        )

        self.assertEqual(checkpoint.accepted_order, 8)
        self.assertEqual(
            checkpoint.last_accepted_receipt_sha256,
            payloads[-1]["accepted_receipt_sha256"],
        )
        self.assertEqual(checkpoint.resulting_public_state, "Accepted public state 8.")
        self.assertEqual(checkpoint.unresolved_threads, ("thread:8",))
        self.assertEqual(len(checkpoint.accepted_lineage), 8)
        self.assertIn("character:sakura", checkpoint.accepted_present_character_ids)
        self.assertNotIn("character:hana", checkpoint.accepted_present_character_ids)
        self.assertIn("knowledge:00000001:hana-learns-code", checkpoint.memory_mapping())
        self.assertIn(
            "accepted:00000001:knowledge:0001",
            checkpoint.memory_mapping(),
        )
        hana = checkpoint.character_mapping()["character:hana"]
        self.assertEqual(hana["accepted_branch_changes"][0]["change_key"], "hana-speaks-directly")
        relationship = checkpoint.relationship_mapping()["relationship:ted-hana"]
        self.assertEqual(
            relationship["accepted_branch_changes"][0]["change_key"],
            "ted-hana-trust",
        )
        self.assertEqual(genesis.genesis_sha256, genesis_before)
        self.assertNotIn(
            "accepted_branch_changes",
            genesis.characters[0].as_mapping(),
        )

    def test_restart_rebuild_and_checkpoint_decode_are_exact(self) -> None:
        payloads = _chain_payloads(8)
        first = BranchStateReducerV1(_genesis()).reduce(
            branch_id=ROOT_BRANCH,
            payloads=payloads,
        )
        restarted = BranchStateReducerV1(_genesis()).reduce(
            branch_id=ROOT_BRANCH,
            payloads=payloads,
        )
        decoded = BranchStateCheckpointV1.from_mapping(first.to_mapping())

        self.assertEqual(restarted.checkpoint_sha256, first.checkpoint_sha256)
        self.assertEqual(decoded, first)
        self.assertEqual(decoded.checkpoint_sha256, first.checkpoint_sha256)

    def test_fork_copies_exact_checkpoint_then_diverges_from_parent(self) -> None:
        reducer = BranchStateReducerV1(_genesis())
        parent_payloads = _chain_payloads(3)
        parent_at_fork = reducer.reduce(
            branch_id=ROOT_BRANCH,
            payloads=parent_payloads,
        )
        child = reducer.fork(parent_at_fork, child_branch_id="branch:child")
        parent_head = parent_payloads[-1]
        parent_receipt = parent_head["receipt"]
        assert isinstance(parent_receipt, dict)
        child_payload = _ordinary_payload(
            4,
            branch_id="branch:child",
            parent_turn_id=str(parent_receipt["accepted_turn_id"]),
            parent_receipt_sha256=str(parent_head["accepted_receipt_sha256"]),
            public_state="Child-only state.",
        )
        parent_payload = _ordinary_payload(
            4,
            branch_id=ROOT_BRANCH,
            parent_turn_id=str(parent_receipt["accepted_turn_id"]),
            parent_receipt_sha256=str(parent_head["accepted_receipt_sha256"]),
            public_state="Parent-only state.",
        )

        child = reducer.advance(child, payloads=(child_payload,))
        parent = reducer.advance(parent_at_fork, payloads=(parent_payload,))

        self.assertEqual(child.resulting_public_state, "Child-only state.")
        self.assertEqual(parent.resulting_public_state, "Parent-only state.")
        self.assertEqual(len(child.branch_lineage), 1)
        self.assertEqual(child.branch_lineage[0].parent_branch_id, ROOT_BRANCH)
        self.assertNotEqual(child.checkpoint_sha256, parent.checkpoint_sha256)

    def test_fork_preserves_pending_and_explicit_provisional_canon_lineage(self) -> None:
        reducer = BranchStateReducerV1(_genesis())
        first = _chain_payloads(1)[0]
        first_receipt = first["receipt"]
        assert isinstance(first_receipt, dict)
        pending = _ordinary_payload(
            2,
            parent_turn_id=str(first_receipt["accepted_turn_id"]),
            parent_receipt_sha256=str(first["accepted_receipt_sha256"]),
            pending=True,
        )
        checkpoint = reducer.reduce(
            branch_id=ROOT_BRANCH,
            payloads=(first, pending),
        )
        checkpoint = reducer.append_provisional_canon(
            checkpoint,
            entry=ProvisionalCanonLineageEntryV1(
                lineage_entry_id="provisional-lineage:001",
                provisional_canon_id="provisional-canon:door-claim",
                parent_lineage_entry_id=None,
                status="unresolved",
                authority_id="creator-note:001",
            ),
        )
        child = reducer.fork(checkpoint, child_branch_id="branch:child-pending")

        self.assertEqual(
            child.pending_ordinary_recording_turn_ids,
            checkpoint.pending_ordinary_recording_turn_ids,
        )
        self.assertEqual(
            child.provisional_canon_lineage,
            checkpoint.provisional_canon_lineage,
        )
        self.assertEqual(child.resulting_public_state, "Accepted public state 1.")

    def test_new_chat_root_never_inherits_another_chat(self) -> None:
        reducer = BranchStateReducerV1(_genesis())
        first_chat = reducer.reduce(
            branch_id=ROOT_BRANCH,
            payloads=_chain_payloads(3),
        )
        new_chat = reducer.new_root(branch_id="branch:new-chat")

        self.assertEqual(first_chat.accepted_order, 3)
        self.assertEqual(new_chat.accepted_order, 0)
        self.assertEqual(new_chat.accepted_lineage, ())
        self.assertEqual(new_chat.provisional_canon_lineage, ())
        self.assertEqual(new_chat.pending_ordinary_recording_turn_ids, ())
        self.assertEqual(new_chat.resulting_public_state, _genesis().resulting_public_state)
        self.assertNotIn("accepted_branch_changes", new_chat.character_mapping()["character:hana"])


if __name__ == "__main__":
    unittest.main()
