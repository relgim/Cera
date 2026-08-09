from __future__ import annotations

import json
from typing import Any, Mapping, Sequence
import unittest

from cera.errors import StateConflictError
from cera.pi_scene.branch_state import AcceptedBranchContextSource
from cera.pi_scene.codex_planner import RetainedCodexPlannerAdapter
from cera.pi_scene.context import AcceptedBranchContextProvider, PiSceneContextSeedV1
from cera.pi_scene.contracts import SceneRoute
from cera.pi_scene.runtime import PlannerTurnInputV1
from cera.serialization import canonical_json, text_sha256, to_primitive
from cera.sequence_first.contracts import ItemKind, SequenceDraftV1, SequenceItemV1


WORLD_ID = "world:retrieval-test"
BRANCH_ID = "branch:retrieval-main"
ADULT_PROTECTED_SENTINEL = "PROTECTED-ADULT-FULL-TEXT-MUST-NOT-REACH-CODEX"


class _ContextSource(AcceptedBranchContextSource):
    def __init__(
        self,
        *,
        reducer_payloads: Sequence[Mapping[str, Any]],
        ordinary_payloads: Sequence[Mapping[str, Any]],
        adult_payloads: Sequence[Mapping[str, Any]],
    ) -> None:
        self.reducer_payloads = tuple(reducer_payloads)
        self.ordinary_payloads = tuple(ordinary_payloads)
        self.adult_payloads = tuple(adult_payloads)

    def accepted_branch_payloads(
        self,
        *,
        world_id: str,
        branch_id: str,
    ) -> Sequence[Mapping[str, Any]]:
        if (world_id, branch_id) != (WORLD_ID, BRANCH_ID):
            return ()
        return self.reducer_payloads

    def recent_ordinary_context_payloads(
        self,
        *,
        world_id: str,
        branch_id: str,
        limit: int = 6,
    ) -> Sequence[Mapping[str, Any]]:
        del world_id, branch_id
        for value in self.reducer_payloads[-limit:]:
            receipt = value["receipt"]
            assert isinstance(receipt, Mapping)
            if receipt["route"] == "adult" and "adult_projection" not in value:
                raise StateConflictError(
                    "ordinary continuity requires the pending adult projection"
                )
        return self.ordinary_payloads[-limit:]

    def recent_adult_context_payloads(
        self,
        *,
        world_id: str,
        branch_id: str,
        limit: int = 6,
    ) -> Sequence[Mapping[str, Any]]:
        del world_id, branch_id
        return self.adult_payloads[-limit:]


class _CapturingPlannerSession:
    external_provider_boundary = False

    def __init__(self) -> None:
        self.semantic_input = None

    def plan(self, semantic_input):
        self.semantic_input = semantic_input
        return SequenceDraftV1(
            items=(
                SequenceItemV1(
                    item_key="hana_continues",
                    kind=ItemKind.DIALOGUE_INTENT,
                    concise_meaning="Hana continues and returns the floor.",
                    owner_response_semantics="Hana continues and returns the floor.",
                    owner_id="character:hana",
                    evidence_keys=("source:current",),
                ),
            ),
            durable_changes=(),
            presence_changes=(),
            resulting_public_state="Hana has replied and the floor is open.",
            unresolved_threads=("Ted may respond.",),
            stopping_boundary="Stop after Hana returns the floor.",
        )


def _seed() -> PiSceneContextSeedV1:
    return PiSceneContextSeedV1(
        world_id=WORLD_ID,
        branch_id=BRANCH_ID,
        scene_id="scene:room",
        accepted_present_character_ids=("character:ted", "character:hana"),
        public_scene_state="Ted and Hana are speaking in the room.",
        characters={
            "character:hana": {
                "name": "Hana",
                "voice": "gentle",
            }
        },
        relationships={
            "relationship:ted-hana": {
                "participants": ["character:ted", "character:hana"],
                "summary": "They are speaking privately.",
            }
        },
        relevant_memories={},
        voice_examples={"hana": {"guidance": "Speak gently."}},
        ordinary_craft_index={"approved_material": ["dialogue"]},
        adult_craft_index={"approved_material": ["adult continuity"]},
        adult_handoff={
            "schema_version": "cera.pi_scene.adult_handoff.v1",
            "all_participants_adults": True,
            "consent_boundary": "The adults remain able to pause.",
        },
        genesis_revision="genesis:retrieval:v1",
    )


def _receipt(
    order: int,
    *,
    route: str,
    parent_turn_id: str | None,
    parent_receipt_sha256: str | None,
) -> tuple[dict[str, Any], str]:
    turn_id = f"turn:{order:04d}"
    receipt: dict[str, Any] = {
        "accepted_turn_id": turn_id,
        "parent_accepted_turn_id": parent_turn_id,
        "parent_accepted_head_sha256": parent_receipt_sha256,
        "world_id": WORLD_ID,
        "branch_id": BRANCH_ID,
        "scene_id": "scene:room",
        "generation": order,
        "route": route,
        "candidate_sha256": text_sha256(f"candidate:{order}"),
    }
    if route == "ordinary":
        authority = {
            "items": [{"item_key": f"item:{order:04d}", "kind": "dialogue_intent"}],
            "durable_changes": [],
            "presence_changes": [],
            "resulting_public_state": f"Ordinary planned state {order}.",
            "unresolved_threads": [f"ordinary-thread:{order}"],
            "stopping_boundary": "Return the floor.",
        }
        authority_json = canonical_json(authority)
        receipt["primary_authority_json"] = authority_json
        receipt["primary_authority_sha256"] = text_sha256(authority_json)
    return receipt, text_sha256(f"accepted-receipt:{order}")


def _ordinary_event(
    order: int,
    *,
    parent_turn_id: str | None,
    parent_receipt_sha256: str | None,
    pending: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    receipt, receipt_sha = _receipt(
        order,
        route="ordinary",
        parent_turn_id=parent_turn_id,
        parent_receipt_sha256=parent_receipt_sha256,
    )
    sanitized: dict[str, Any] = {
        "receipt": receipt,
        "accepted_receipt_sha256": receipt_sha,
        "recording_status": "pending_repair" if pending else "complete",
    }
    if not pending:
        sanitized["ordinary_record"] = {
            "secondary_canon": [],
            "resulting_public_state": f"Ordinary accepted state {order}.",
            "relationship_changes": [],
            "knowledge_changes": [],
            "durable_changes": [],
            "unresolved_threads": [f"ordinary-thread:{order}"],
        }
    raw_receipt = {
        **receipt,
        "exact_accepted_prose": f"Ordinary accepted prose {order}.",
    }
    raw = {**sanitized, "receipt": raw_receipt}
    return sanitized, raw


def _adult_event(
    order: int,
    *,
    parent_turn_id: str,
    parent_receipt_sha256: str,
    pending: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    receipt, receipt_sha = _receipt(
        order,
        route="adult",
        parent_turn_id=parent_turn_id,
        parent_receipt_sha256=parent_receipt_sha256,
    )
    sanitized: dict[str, Any] = {
        "receipt": receipt,
        "accepted_receipt_sha256": receipt_sha,
        "recording_status": "projection_pending" if pending else "complete",
    }
    if not pending:
        sanitized["adult_projection"] = {
            "decision_path_summary": ["The adults made one mutual choice."],
            "items": [
                {
                    "event_key": "adult-event:mutual-choice",
                    "non_explicit_summary": "A consensual adult interaction occurred.",
                    "lasting_story_meaning": "Their mutual trust now has shared history.",
                }
            ],
            "resulting_public_state": "The adults remain together afterward.",
            "unresolved_threads": ["Their next conversation remains open."],
        }
    raw_receipt = {
        **receipt,
        "exact_accepted_prose": ADULT_PROTECTED_SENTINEL,
    }
    raw: dict[str, Any] = {**sanitized, "receipt": raw_receipt}
    if not pending:
        raw["adult_full_record"] = {
            "events": [{"protected_detail": ADULT_PROTECTED_SENTINEL}]
        }
    return sanitized, raw


def _planner_request(turn) -> PlannerTurnInputV1:
    return PlannerTurnInputV1(
        world_id=turn.world_id,
        branch_id=turn.branch_id,
        scene_id=turn.scene_id,
        exact_user_source=turn.exact_user_source,
        current_state=turn.current_state,
        characters=turn.characters,
        relationships=turn.relationships,
        relevant_memories=turn.relevant_memories,
        accepted_records=(),
    )


class BranchStateRetrievalTests(unittest.TestCase):
    def test_adult_projection_returns_to_codex_without_protected_full_text(self) -> None:
        sanitized: list[dict[str, Any]] = []
        ordinary_recent: list[dict[str, Any]] = []
        adult_recent: list[dict[str, Any]] = []
        parent_turn: str | None = None
        parent_sha: str | None = None
        for order in range(1, 8):
            reduced, raw = _ordinary_event(
                order,
                parent_turn_id=parent_turn,
                parent_receipt_sha256=parent_sha,
            )
            sanitized.append(reduced)
            ordinary_recent.append(raw)
            adult_recent.append(raw)
            parent_turn = str(reduced["receipt"]["accepted_turn_id"])
            parent_sha = str(reduced["accepted_receipt_sha256"])
        assert parent_turn is not None and parent_sha is not None
        adult, adult_raw = _adult_event(
            8,
            parent_turn_id=parent_turn,
            parent_receipt_sha256=parent_sha,
            pending=False,
        )
        sanitized.append(adult)
        ordinary_recent.append(adult)
        adult_recent.append(adult_raw)
        source = _ContextSource(
            reducer_payloads=sanitized,
            ordinary_payloads=ordinary_recent,
            adult_payloads=adult_recent,
        )

        turn = AcceptedBranchContextProvider(source, _seed())(
            SceneRoute.ORDINARY,
            "What does Hana say next?",
            (),
        )
        adult_memory = turn.relevant_memories[
            "adult:00000008:adult-event:mutual-choice"
        ]
        self.assertEqual(
            adult_memory["authority"],
            "accepted_adult_filtered_projection",
        )
        self.assertIn("mutual trust", adult_memory["lasting_story_meaning"])
        self.assertNotIn(ADULT_PROTECTED_SENTINEL, canonical_json(to_primitive(turn)))
        self.assertNotIn(ADULT_PROTECTED_SENTINEL, "\n".join(turn.recent_prose))

        session = _CapturingPlannerSession()
        adapter = RetainedCodexPlannerAdapter(session)
        adapter.plan(_planner_request(turn))
        assert session.semantic_input is not None
        codex_evidence = "\n".join(
            value.exact_content for value in session.semantic_input.evidence_records
        )
        self.assertIn("accepted_adult_filtered_projection", codex_evidence)
        self.assertIn("mutual trust", codex_evidence)
        self.assertNotIn(ADULT_PROTECTED_SENTINEL, codex_evidence)

    def test_pending_ordinary_prose_continues_over_prior_complete_checkpoint(self) -> None:
        first, first_raw = _ordinary_event(
            1,
            parent_turn_id=None,
            parent_receipt_sha256=None,
        )
        first_turn = str(first["receipt"]["accepted_turn_id"])
        first_sha = str(first["accepted_receipt_sha256"])
        pending, pending_raw = _ordinary_event(
            2,
            parent_turn_id=first_turn,
            parent_receipt_sha256=first_sha,
            pending=True,
        )
        source = _ContextSource(
            reducer_payloads=(first, pending),
            ordinary_payloads=(first_raw, pending_raw),
            adult_payloads=(first_raw, pending_raw),
        )

        turn = AcceptedBranchContextProvider(source, _seed())(
            SceneRoute.ORDINARY,
            "Continue.",
            (),
        )

        self.assertEqual(
            turn.current_state["public_scene_state"],
            "Ordinary accepted state 1.",
        )
        self.assertEqual(turn.current_state["accepted_state_order"], 2)
        self.assertEqual(
            turn.current_state["pending_ordinary_recording_turn_ids"],
            ["turn:0002"],
        )
        self.assertEqual(len(turn.current_state["accepted_lineage"]["accepted_entries"]), 2)
        self.assertIn("Ordinary accepted prose 2.", turn.recent_prose)

    def test_pending_adult_blocks_codex_but_remains_in_protected_writer_view(self) -> None:
        first, first_raw = _ordinary_event(
            1,
            parent_turn_id=None,
            parent_receipt_sha256=None,
        )
        adult, adult_raw = _adult_event(
            2,
            parent_turn_id=str(first["receipt"]["accepted_turn_id"]),
            parent_receipt_sha256=str(first["accepted_receipt_sha256"]),
            pending=True,
        )
        source = _ContextSource(
            reducer_payloads=(first, adult),
            ordinary_payloads=(first_raw, adult),
            adult_payloads=(first_raw, adult_raw),
        )
        provider = AcceptedBranchContextProvider(source, _seed())

        with self.assertRaisesRegex(
            StateConflictError,
            "pending adult projection",
        ):
            provider(SceneRoute.ORDINARY, "Return to ordinary planning.", ())

        protected_turn = provider(SceneRoute.ADULT, "Continue the adult route.", ())
        self.assertIn(ADULT_PROTECTED_SENTINEL, protected_turn.recent_prose)
        safe_state = canonical_json(
            {
                "current_state": protected_turn.current_state,
                "characters": protected_turn.characters,
                "relationships": protected_turn.relationships,
                "relevant_memories": protected_turn.relevant_memories,
            }
        )
        self.assertNotIn(ADULT_PROTECTED_SENTINEL, safe_state)
        self.assertEqual(
            protected_turn.current_state["pending_adult_projection_turn_ids"],
            ["turn:0002"],
        )


if __name__ == "__main__":
    unittest.main()
