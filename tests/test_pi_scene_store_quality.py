from __future__ import annotations

import json
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from cera.errors import StateConflictError
from cera.pi_scene.contracts import (
    AdultCodexProjectionV1,
    AdultFullRecordV1,
    AdultProjectionItemV1,
    AdultRecordEventV1,
    LeanCandidateV1,
    OrdinarySceneRecordV1,
    PiWriterReceiptV1,
    RecordingStatus,
    SceneRoute,
)
from cera.pi_scene.store import LeanSceneStore
import cera.pi_scene.store as store_module
from cera.serialization import canonical_json, canonical_sha256, text_sha256


def _authority(route: SceneRoute, label: str) -> dict[str, object]:
    if route is SceneRoute.ORDINARY:
        return {
            "items": [
                {
                    "item_key": label,
                    "owner_id": "character:hana",
                    "kind": "dialogue_intent",
                    "summary": "Hana answers and returns the floor.",
                }
            ],
            "durable_changes": [],
            "presence_changes": [],
            "resulting_public_state": "The conversation remains open.",
            "unresolved_threads": ["Ted may respond."],
            "stopping_boundary": "Stop with the floor returned to Ted.",
        }
    return {
        "schema_version": "cera.pi_scene.adult_handoff.v1",
        "handoff_key": label,
        "all_participants_adults": True,
        "consent_boundary": "The supplied scene remains consensual.",
        "causal_direction": "The adults make one choice and return the floor.",
    }


def _candidate(
    *,
    route: SceneRoute = SceneRoute.ORDINARY,
    generation: int = 1,
    parent_turn_id: str | None = None,
    parent_head_sha256: str | None = None,
) -> LeanCandidateV1:
    label = f"beat-{generation}"
    prose = f"Accepted prose {generation}."
    authority_json = canonical_json(_authority(route, label))
    writer_receipt = PiWriterReceiptV1(
        schema_version=PiWriterReceiptV1.SCHEMA_VERSION,
        route=route,
        provider="test-provider",
        model="test-model",
        pi_version="test-pi",
        session_id_sha256=text_sha256(f"session-{generation}"),
        parent_session_id_sha256=None,
        request_sha256=text_sha256(f"request-{generation}"),
        output_sha256=text_sha256(prose),
        provider_operations=1,
        tool_call_count=0,
        failed_tool_call_count=0,
        input_tokens=10,
        cached_input_tokens=0,
        output_tokens=4,
        reasoning_tokens=0,
        duration_ms=2,
        finish_status="stop",
        rehydrated=False,
    )
    return LeanCandidateV1(
        schema_version=LeanCandidateV1.SCHEMA_VERSION,
        request_id=f"request-{generation}",
        candidate_id=f"candidate-{generation}",
        turn_id=f"turn-{generation}",
        world_id="world-test",
        branch_id="branch-main",
        scene_id="scene-kitchen",
        generation=generation,
        parent_accepted_turn_id=parent_turn_id,
        accepted_head_before_sha256=parent_head_sha256,
        exact_user_source=f"Continue {generation}.",
        exact_user_source_sha256=text_sha256(f"Continue {generation}."),
        route=route,
        primary_authority_kind=(
            "codex_sequence" if route is SceneRoute.ORDINARY else "adult_handoff"
        ),
        primary_authority_json=authority_json,
        primary_authority_sha256=text_sha256(authority_json),
        writer_view_manifest_sha256=text_sha256(f"view-{generation}"),
        story_text=prose,
        story_text_sha256=text_sha256(prose),
        writer_receipt=writer_receipt,
    )


def _ordinary_record(accepted) -> OrdinarySceneRecordV1:
    return OrdinarySceneRecordV1(
        schema_version=OrdinarySceneRecordV1.SCHEMA_VERSION,
        primary_sequence_sha256=accepted.primary_authority_sha256,
        realized_item_keys=(f"beat-{accepted.generation}",),
        secondary_canon=(),
        resulting_public_state="The conversation remains open.",
        relationship_changes=(),
        knowledge_changes=(),
        durable_changes=(),
        unresolved_threads=("Ted may respond.",),
    )


def _adult_records(
    accepted,
) -> tuple[AdultFullRecordV1, AdultCodexProjectionV1]:
    event = AdultRecordEventV1(
        event_key=f"event-{accepted.generation}",
        summary="The authorized choice occurs.",
        motive="Mutual interest.",
        alternatives_considered=("Pause and return the floor.",),
        consent_or_boundary_transition="Consent remains active.",
        thoughts_and_feelings=("Both remain attentive.",),
        durable_effects=("Their shared trust may develop.",),
        knowledge_scope=("Only the present adults know the details.",),
    )
    full = AdultFullRecordV1(
        schema_version=AdultFullRecordV1.SCHEMA_VERSION,
        adult_handoff_sha256=accepted.primary_authority_sha256,
        decision_path=("Both adults make the authorized choice.",),
        events=(event,),
        resulting_public_state="The adults remain together afterward.",
        unresolved_threads=("Their next conversation remains open.",),
    )
    projection = AdultCodexProjectionV1(
        schema_version=AdultCodexProjectionV1.SCHEMA_VERSION,
        adult_full_record_sha256=canonical_sha256(full),
        decision_path_summary=("The adults made a mutual choice.",),
        items=(
            AdultProjectionItemV1(
                event_key=event.event_key,
                non_explicit_summary="A consensual adult interaction occurred.",
                lasting_story_meaning="Their shared trust may develop.",
            ),
        ),
        resulting_public_state=full.resulting_public_state,
        unresolved_threads=full.unresolved_threads,
    )
    return full, projection


def _turn_dir(root: Path) -> Path:
    return next(root.rglob("ACCEPTED_RECEIPT.json")).parent


def _rewrite_json(path: Path, mutate) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    mutate(payload)
    path.write_text(canonical_json(payload), encoding="utf-8")


class PiSceneStoreQualityTests(unittest.TestCase):
    def test_durable_receipt_decode_is_closed_and_does_not_coerce_primitives(self) -> None:
        mutations = (
            lambda value: value.update({"unknown_field": "no"}),
            lambda value: value.update({"generation": True}),
            lambda value: value["writer_receipt"].update({"rehydrated": "false"}),
            lambda value: value["writer_receipt"].update({"provider": 7}),
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation), TemporaryDirectory() as temporary:
                root = Path(temporary)
                store = LeanSceneStore(root)
                store.accept(_candidate())
                _rewrite_json(_turn_dir(root) / "ACCEPTED_RECEIPT.json", mutation)
                with self.assertRaises(StateConflictError):
                    LeanSceneStore(root).load_head(
                        world_id="world-test",
                        branch_id="branch-main",
                    )

    def test_phase_one_attempt_zero_is_restart_repairable_and_explicitly_pending(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = LeanSceneStore(root)
            accepted = store.accept(_candidate())

            phase_one = store.load_recording_attempt(accepted)
            self.assertEqual(phase_one.attempt_number, 0)
            self.assertIs(phase_one.status, RecordingStatus.PROJECTION_PENDING)
            with self.assertRaises(StateConflictError):
                store.recent_accepted_payloads(
                    world_id="world-test",
                    branch_id="branch-main",
                )
            pending_recent = store.recent_accepted_payloads(
                world_id="world-test",
                branch_id="branch-main",
                allow_pending=True,
            )[0]
            pending_branch = store.accepted_branch_payloads(
                world_id="world-test",
                branch_id="branch-main",
            )[0]
            for payload in (pending_recent, pending_branch):
                self.assertEqual(
                    payload["recording_status"],
                    RecordingStatus.PROJECTION_PENDING.value,
                )
                self.assertNotIn("ordinary_record", payload)
                self.assertNotIn("adult_projection", payload)

            restarted = LeanSceneStore(root)
            record = _ordinary_record(accepted)
            attempt = restarted.attach_ordinary_record(
                accepted,
                record,
                recorder_request_sha256=text_sha256("record request"),
                recorder_output_sha256=text_sha256("record output"),
                provider_operations=1,
            )
            self.assertEqual(attempt.attempt_number, 1)
            payload = restarted.recent_accepted_payloads(
                world_id="world-test",
                branch_id="branch-main",
            )[0]
            self.assertEqual(payload["receipt"]["exact_accepted_prose"], "Accepted prose 1.")

    def test_pending_adult_branch_payload_never_exposes_a_full_record(self) -> None:
        with TemporaryDirectory() as temporary:
            store = LeanSceneStore(Path(temporary))
            store.accept(_candidate(route=SceneRoute.ADULT))
            payload = store.accepted_branch_payloads(
                world_id="world-test",
                branch_id="branch-main",
            )[0]
            self.assertEqual(
                payload["recording_status"],
                RecordingStatus.PROJECTION_PENDING.value,
            )
            self.assertNotIn("adult_full_record", payload)
            self.assertNotIn("adult_projection", payload)

    def test_complete_bundle_detects_root_and_bundle_tampering_on_read(self) -> None:
        for target in ("root", "bundle"):
            with self.subTest(target=target), TemporaryDirectory() as temporary:
                root = Path(temporary)
                store = LeanSceneStore(root)
                accepted = store.accept(_candidate())
                store.attach_ordinary_record(
                    accepted,
                    _ordinary_record(accepted),
                    recorder_request_sha256=text_sha256("record request"),
                    recorder_output_sha256=text_sha256("record output"),
                    provider_operations=1,
                )
                turn_dir = _turn_dir(root)
                record_path = (
                    turn_dir / "ORDINARY_RECORD.json"
                    if target == "root"
                    else turn_dir / "RECORDING_BUNDLE_0001" / "ORDINARY_RECORD.json"
                )
                _rewrite_json(
                    record_path,
                    lambda value: value.update(
                        {"resulting_public_state": "Tampered state."}
                    ),
                )
                with self.assertRaises(StateConflictError):
                    LeanSceneStore(root).load_head(
                        world_id="world-test",
                        branch_id="branch-main",
                    )

    def test_adult_projection_hash_is_verified_in_both_published_views(self) -> None:
        for target in ("root", "bundle"):
            with self.subTest(target=target), TemporaryDirectory() as temporary:
                root = Path(temporary)
                store = LeanSceneStore(root)
                accepted = store.accept(_candidate(route=SceneRoute.ADULT))
                full, projection = _adult_records(accepted)
                store.attach_adult_records(
                    accepted,
                    full,
                    projection,
                    recorder_request_sha256=text_sha256("record request"),
                    recorder_output_sha256=text_sha256("record output"),
                    provider_operations=1,
                )
                turn_dir = _turn_dir(root)
                projection_path = (
                    turn_dir / "ADULT_CODEX_PROJECTION.json"
                    if target == "root"
                    else (
                        turn_dir
                        / "RECORDING_BUNDLE_0001"
                        / "ADULT_CODEX_PROJECTION.json"
                    )
                )
                _rewrite_json(
                    projection_path,
                    lambda value: value.update(
                        {"resulting_public_state": "Tampered projection."}
                    ),
                )
                with self.assertRaises(StateConflictError):
                    LeanSceneStore(root).recent_accepted_payloads(
                        world_id="world-test",
                        branch_id="branch-main",
                    )

    def test_crash_after_adult_bundle_publish_is_reconciled_without_partial_route(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = LeanSceneStore(root)
            accepted = store.accept(_candidate(route=SceneRoute.ADULT))
            full, projection = _adult_records(accepted)
            with patch.object(
                store_module,
                "_materialize_recording_bundle_compatibility",
                side_effect=OSError("injected crash after bundle publish"),
            ):
                with self.assertRaises(OSError):
                    store.attach_adult_records(
                        accepted,
                        full,
                        projection,
                        recorder_request_sha256=text_sha256("record request"),
                        recorder_output_sha256=text_sha256("record output"),
                        provider_operations=1,
                    )

            turn_dir = _turn_dir(root)
            self.assertTrue((turn_dir / "RECORDING_BUNDLE_0001").is_dir())
            raw_head = json.loads(
                (turn_dir / "RECORDING_HEAD.json").read_text(encoding="utf-8")
            )
            self.assertEqual(raw_head["status"], RecordingStatus.PROJECTION_PENDING.value)

            restarted = LeanSceneStore(root)
            head = restarted.load_head(
                world_id="world-test",
                branch_id="branch-main",
            )
            self.assertIs(head.recording_status, RecordingStatus.COMPLETE)
            payload = restarted.recent_accepted_payloads(
                world_id="world-test",
                branch_id="branch-main",
                adult_full=True,
            )[0]
            self.assertIn("adult_full_record", payload)
            self.assertIn("adult_projection", payload)

    def test_crash_before_bundle_publish_leaves_phase_one_retryable(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = LeanSceneStore(root)
            accepted = store.accept(_candidate())
            record = _ordinary_record(accepted)
            real_replace = store_module.os.replace

            def crash_on_bundle_publish(source, destination):
                if Path(source).name.startswith(".RECORDING_BUNDLE_"):
                    raise OSError("injected crash before bundle publish")
                return real_replace(source, destination)

            with patch.object(store_module.os, "replace", side_effect=crash_on_bundle_publish):
                with self.assertRaises(OSError):
                    store.attach_ordinary_record(
                        accepted,
                        record,
                        recorder_request_sha256=text_sha256("record request"),
                        recorder_output_sha256=text_sha256("record output"),
                        provider_operations=1,
                    )

            turn_dir = _turn_dir(root)
            self.assertFalse((turn_dir / "RECORDING_BUNDLE_0001").exists())
            restarted = LeanSceneStore(root)
            self.assertEqual(restarted.load_recording_attempt(accepted).attempt_number, 0)
            attempt = restarted.attach_ordinary_record(
                accepted,
                record,
                recorder_request_sha256=text_sha256("record request"),
                recorder_output_sha256=text_sha256("record output"),
                provider_operations=1,
            )
            self.assertEqual(attempt.attempt_number, 1)

    def test_historical_complete_layout_remains_readable_without_rewrite(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = LeanSceneStore(root)
            accepted = store.accept(_candidate())
            store.attach_ordinary_record(
                accepted,
                _ordinary_record(accepted),
                recorder_request_sha256=text_sha256("record request"),
                recorder_output_sha256=text_sha256("record output"),
                provider_operations=1,
            )
            bundle_path = _turn_dir(root) / "RECORDING_BUNDLE_0001"
            shutil.rmtree(bundle_path)

            payload = LeanSceneStore(root).recent_accepted_payloads(
                world_id="world-test",
                branch_id="branch-main",
            )[0]
            self.assertEqual(payload["receipt"]["exact_accepted_prose"], "Accepted prose 1.")
            self.assertIn("ordinary_record", payload)
            self.assertFalse(bundle_path.exists())

    def test_unbounded_branch_reader_is_ordered_verified_and_projection_only(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = LeanSceneStore(root)
            first = store.accept(_candidate())
            store.attach_ordinary_record(
                first,
                _ordinary_record(first),
                recorder_request_sha256=text_sha256("ordinary request"),
                recorder_output_sha256=text_sha256("ordinary output"),
                provider_operations=1,
            )
            second = store.accept(
                _candidate(
                    route=SceneRoute.ADULT,
                    generation=2,
                    parent_turn_id=first.accepted_turn_id,
                    parent_head_sha256=first.receipt_sha256,
                )
            )
            full, projection = _adult_records(second)
            store.attach_adult_records(
                second,
                full,
                projection,
                recorder_request_sha256=text_sha256("adult request"),
                recorder_output_sha256=text_sha256("adult output"),
                provider_operations=1,
            )

            payloads = store.accepted_branch_payloads(
                world_id="world-test",
                branch_id="branch-main",
            )
            self.assertEqual(
                [value["receipt"]["generation"] for value in payloads],
                [1, 2],
            )
            self.assertIn("ordinary_record", payloads[0])
            self.assertIn("adult_projection", payloads[1])
            self.assertNotIn("adult_full_record", payloads[1])


if __name__ == "__main__":
    unittest.main()
