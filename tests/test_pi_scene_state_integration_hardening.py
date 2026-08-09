from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from cera.errors import ContractValidationError, StateConflictError
from cera.pi_scene.branch_state import (
    BranchStateReducerV1,
    DurableBranchChangeV1,
    GenesisBranchStateV1,
    ProvisionalCanonLineageEntryV1,
)
from cera.pi_scene.codex_planner import _durable_change_evidence
from cera.pi_scene.contracts import (
    AdultCodexProjectionV2,
    AdultProjectionDurableChangeV1,
    AdultProjectionItemV1,
    AdultProjectionPresenceChangeV1,
    SceneRoute,
)
from cera.pi_scene.store import LeanSceneStore, adult_projection_from_mapping
from cera.sequence_first.contracts import Visibility
from cera.serialization import canonical_json, canonical_sha256, text_sha256, to_primitive

from tests.test_pi_scene_branch_state_reducer import (
    ROOT_BRANCH,
    _genesis,
    _ordinary_payload,
)
from tests.test_pi_scene_store_quality import (
    _adult_records,
    _candidate,
    _ordinary_record,
    _turn_dir,
)


def _complete_ordinary(
    store: LeanSceneStore,
    *,
    generation: int,
    parent_turn_id: str | None,
    parent_head_sha256: str | None,
):
    accepted = store.accept(
        _candidate(
            generation=generation,
            parent_turn_id=parent_turn_id,
            parent_head_sha256=parent_head_sha256,
        )
    )
    store.attach_ordinary_record(
        accepted,
        _ordinary_record(accepted),
        recorder_request_sha256=text_sha256(f"record-request-{generation}"),
        recorder_output_sha256=text_sha256(f"record-output-{generation}"),
        provider_operations=1,
    )
    return accepted


class PiSceneStateIntegrationHardeningTests(unittest.TestCase):
    def test_new_bundle_manifest_binds_python_accepted_custody(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = LeanSceneStore(root)
            accepted = _complete_ordinary(
                store,
                generation=1,
                parent_turn_id=None,
                parent_head_sha256=None,
            )
            manifest_path = next(root.rglob("BUNDLE_MANIFEST.json"))
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(
                manifest["schema_version"],
                "cera.pi_scene.recording_bundle_manifest.v2",
            )
            self.assertEqual(manifest["accepted_turn_id"], accepted.accepted_turn_id)
            self.assertEqual(
                manifest["accepted_receipt_sha256"], accepted.receipt_sha256
            )
            self.assertEqual(
                manifest["exact_accepted_prose_sha256"],
                accepted.exact_accepted_prose_sha256,
            )
            self.assertEqual(
                manifest["primary_authority_sha256"],
                accepted.primary_authority_sha256,
            )

            manifest["exact_accepted_prose_sha256"] = "f" * 64
            manifest_path.write_text(canonical_json(manifest), encoding="utf-8")
            with self.assertRaisesRegex(StateConflictError, "accepted custody"):
                LeanSceneStore(root).load_recording_attempt(accepted)

    def test_head_cache_rejects_coherent_receipt_rewrite(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = LeanSceneStore(root)
            store.accept(_candidate())
            receipt_path = next(root.rglob("ACCEPTED_RECEIPT.json"))
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            receipt["exact_accepted_prose"] = "Coherently rewritten prose."
            rewritten_sha = text_sha256(receipt["exact_accepted_prose"])
            receipt["exact_accepted_prose_sha256"] = rewritten_sha
            receipt["writer_receipt"]["output_sha256"] = rewritten_sha
            receipt_path.write_text(canonical_json(receipt), encoding="utf-8")

            with self.assertRaisesRegex(StateConflictError, "head cache conflicts"):
                LeanSceneStore(root).load_head(
                    world_id="world-test",
                    branch_id="branch-main",
                )

    def test_head_cache_recovers_only_from_exact_ancestor(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = LeanSceneStore(root)
            first = _complete_ordinary(
                store,
                generation=1,
                parent_turn_id=None,
                parent_head_sha256=None,
            )
            second = _complete_ordinary(
                store,
                generation=2,
                parent_turn_id=first.accepted_turn_id,
                parent_head_sha256=first.receipt_sha256,
            )
            cache_path = next(root.rglob("BRANCH_HEAD_CACHE.json"))
            cache_path.write_text(
                canonical_json(
                    {
                        "schema_version": "cera.pi_scene.branch_head_cache.v1",
                        "generation": first.generation,
                        "accepted_turn_id": first.accepted_turn_id,
                        "accepted_head_sha256": first.receipt_sha256,
                    }
                ),
                encoding="utf-8",
            )
            head = LeanSceneStore(root).load_head(
                world_id="world-test",
                branch_id="branch-main",
            )
            self.assertEqual(head.accepted_head_sha256, second.receipt_sha256)
            recovered = json.loads(cache_path.read_text(encoding="utf-8"))
            self.assertEqual(recovered["accepted_head_sha256"], second.receipt_sha256)

    def test_same_identity_pending_completion_replays_without_new_lineage(self) -> None:
        reducer = BranchStateReducerV1(_genesis())
        pending = _ordinary_payload(
            1,
            parent_turn_id=None,
            parent_receipt_sha256=None,
            pending=True,
        )
        pending_sha = str(pending["accepted_receipt_sha256"])
        second = _ordinary_payload(
            2,
            parent_turn_id=str(pending["receipt"]["accepted_turn_id"]),  # type: ignore[index]
            parent_receipt_sha256=pending_sha,
        )
        checkpoint = reducer.reduce(
            branch_id=ROOT_BRANCH,
            payloads=(pending, second),
        )
        checkpoint = reducer.append_provisional_canon(
            checkpoint,
            entry=ProvisionalCanonLineageEntryV1(
                lineage_entry_id="provisional-lineage-1",
                provisional_canon_id="provisional-1",
                parent_lineage_entry_id=None,
                status="unresolved",
                authority_id="creator-review-1",
            ),
        )
        forked = reducer.fork(checkpoint, child_branch_id="branch:child")
        complete = _ordinary_payload(
            1,
            parent_turn_id=None,
            parent_receipt_sha256=None,
            pending=False,
        )
        reconciled = reducer.reconcile_recording_completions(
            forked,
            payloads=(complete, second),
        )
        self.assertEqual(reconciled.accepted_lineage, checkpoint.accepted_lineage)
        self.assertEqual(reconciled.branch_lineage, forked.branch_lineage)
        self.assertEqual(
            reconciled.provisional_canon_lineage,
            checkpoint.provisional_canon_lineage,
        )
        self.assertEqual(reconciled.pending_ordinary_recording_turn_ids, ())
        self.assertEqual(reconciled.branch_id, "branch:child")

    def test_recent_context_retains_pending_turn_outside_six_turn_tail(self) -> None:
        with TemporaryDirectory() as temporary:
            store = LeanSceneStore(Path(temporary))
            first = store.accept(_candidate(generation=1))
            parent = first
            for generation in range(2, 9):
                parent = _complete_ordinary(
                    store,
                    generation=generation,
                    parent_turn_id=parent.accepted_turn_id,
                    parent_head_sha256=parent.receipt_sha256,
                )
            payloads = store.recent_ordinary_context_payloads(
                world_id="world-test",
                branch_id="branch-main",
                limit=6,
            )
            turn_ids = [value["receipt"]["accepted_turn_id"] for value in payloads]
            self.assertEqual(turn_ids, ["turn-1", *[f"turn-{n}" for n in range(3, 9)]])
            self.assertEqual(payloads[0]["recording_status"], "projection_pending")

    def test_old_pending_adult_projection_still_blocks_ordinary_context(self) -> None:
        with TemporaryDirectory() as temporary:
            store = LeanSceneStore(Path(temporary))
            first = store.accept(_candidate(route=SceneRoute.ADULT, generation=1))
            parent = first
            for generation in range(2, 9):
                parent = _complete_ordinary(
                    store,
                    generation=generation,
                    parent_turn_id=parent.accepted_turn_id,
                    parent_head_sha256=parent.receipt_sha256,
                )
            with self.assertRaisesRegex(
                StateConflictError,
                "pending adult projection",
            ):
                store.recent_ordinary_context_payloads(
                    world_id="world-test",
                    branch_id="branch-main",
                    limit=6,
                )

    def test_durable_evidence_preserves_order_owner_and_privacy(self) -> None:
        values = (
            DurableBranchChangeV1(
                change_key="public-1",
                kind="material",
                subject_ids=("character:hana",),
                concise_change="The cup is cracked.",
                target_key="object:cup",
                visibility="public",
                knowledge_owner_id=None,
                accepted_turn_id="turn-1",
                source_kind="ordinary_primary_authority",
            ),
            DurableBranchChangeV1(
                change_key="private-2",
                kind="knowledge",
                subject_ids=("character:hana",),
                concise_change="Hana privately knows the code.",
                target_key="memory:hana-code",
                visibility="character_private",
                knowledge_owner_id="character:hana",
                accepted_turn_id="turn-2",
                source_kind="ordinary_primary_authority",
            ),
            DurableBranchChangeV1(
                change_key="internal-3",
                kind="recorded_durable",
                subject_ids=(),
                concise_change="Unscoped Recorder suggestion.",
                target_key="internal:3",
                visibility="branch_internal_unspecified",
                knowledge_owner_id=None,
                accepted_turn_id="turn-3",
                source_kind="ordinary_recorder_projection",
            ),
        )
        evidence = _durable_change_evidence(
            tuple(to_primitive(value) for value in values)
        )
        self.assertEqual(len(evidence), 2)
        self.assertIn("public-1", evidence[0].exact_content)
        self.assertIn("private-2", evidence[1].exact_content)
        self.assertIs(evidence[0].visibility, Visibility.PUBLIC)
        self.assertIs(evidence[1].visibility, Visibility.CHARACTER_PRIVATE)
        self.assertEqual(evidence[1].knowledge_owner_id, "character:hana")
        self.assertNotIn("internal-3", "".join(value.exact_content for value in evidence))

    def test_adult_v2_projection_carries_scoped_state_without_full_record(self) -> None:
        with TemporaryDirectory() as temporary:
            store = LeanSceneStore(Path(temporary))
            accepted = store.accept(_candidate(route=SceneRoute.ADULT))
            full, _ = _adult_records(accepted)
            projection = AdultCodexProjectionV2(
                schema_version=AdultCodexProjectionV2.SCHEMA_VERSION,
                adult_full_record_sha256=canonical_sha256(full),
                decision_path_summary=("The adults made one bounded choice.",),
                items=(
                    AdultProjectionItemV1(
                        event_key="event-1",
                        non_explicit_summary="A private adult interaction concluded.",
                        lasting_story_meaning="Hana needs a quiet conversation afterward.",
                    ),
                ),
                presence_changes=(
                    AdultProjectionPresenceChangeV1(
                        character_id="character:mia",
                        direction="enter",
                        effective_after_event_key="event-1",
                    ),
                ),
                durable_effects=(
                    AdultProjectionDurableChangeV1(
                        change_key="hana-private-knowledge",
                        kind="knowledge",
                        subject_ids=("character:hana",),
                        non_explicit_change="Hana privately knows a boundary changed.",
                        target_key="memory:hana-private-boundary",
                        visibility="character_private",
                        knowledge_owner_id="character:hana",
                    ),
                ),
                resulting_public_state="Hana and Mia are now present in the room.",
                unresolved_threads=("Hana may choose whether to speak.",),
            )
            store.attach_adult_records(
                accepted,
                full,
                projection,
                recorder_request_sha256=text_sha256("adult-record-request"),
                recorder_output_sha256=text_sha256("adult-record-output"),
                provider_operations=1,
            )
            payloads = store.accepted_branch_payloads(
                world_id="world-test",
                branch_id="branch-main",
            )
            serialized = canonical_json(payloads[0])
            self.assertNotIn('"decision_path":', serialized)
            self.assertNotIn("thoughts_and_feelings", serialized)
            genesis = GenesisBranchStateV1.from_mappings(
                world_id="world-test",
                genesis_revision="genesis:test:adult-v2",
                scene_id="scene-kitchen",
                accepted_present_character_ids=("character:ted", "character:hana"),
                resulting_public_state="Ted and Hana are in the kitchen.",
                characters={"character:hana": {"name": "Hana"}},
                relationships={},
                memories={},
            )
            checkpoint = BranchStateReducerV1(genesis).reduce(
                branch_id="branch-main",
                payloads=payloads,
            )
            self.assertIn("character:mia", checkpoint.accepted_present_character_ids)
            private = next(
                value
                for value in checkpoint.durable_changes
                if value.change_key == "hana-private-knowledge"
            )
            self.assertEqual(private.visibility, "character_private")
            self.assertEqual(private.knowledge_owner_id, "character:hana")

            payload = to_primitive(projection)
            payload["unexpected"] = True
            with self.assertRaises(ContractValidationError):
                adult_projection_from_mapping(payload)


if __name__ == "__main__":
    unittest.main()
