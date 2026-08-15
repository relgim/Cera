from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import cera.pi_scene.store as store_module
from cera.errors import StateConflictError
from cera.pi_scene.contracts import RecordingStatus
from cera.pi_scene.store import LeanSceneStore
from cera.serialization import canonical_json, text_sha256
from tests.test_pi_scene_store_quality import _candidate, _ordinary_record


def _turn_dir(root: Path) -> Path:
    return next(root.rglob("ACCEPTED_RECEIPT.json")).parent


def _leave_orphan_failure(
    store: LeanSceneStore,
    accepted,
    *,
    request: str = "failed-request",
) -> None:
    real_atomic_write = store_module._atomic_write_json

    def crash_before_head(path: Path, value) -> None:
        if path.name == "RECORDING_HEAD.json":
            raise OSError("injected crash after immutable attempt publication")
        real_atomic_write(path, value)

    with patch.object(
        store_module,
        "_atomic_write_json",
        side_effect=crash_before_head,
    ):
        with unittest.TestCase().assertRaises(OSError):
            store.mark_recording_failure(
                accepted,
                recorder_request_sha256=text_sha256(request),
                recorder_output_sha256=None,
                provider_operations=1,
                failure_code="transport_interrupted",
            )


class PiSceneStoreRecoveryQualityTests(unittest.TestCase):
    def test_atomic_recording_staging_path_keeps_bounded_overhead(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = LeanSceneStore(root)
            accepted = store.accept(_candidate())
            turn_dir = _turn_dir(root)
            observed: list[Path] = []
            real_write_new_json = store_module._write_new_json

            def capture(path: Path, value) -> None:
                observed.append(path)
                real_write_new_json(path, value)

            with patch.object(store_module, "_write_new_json", side_effect=capture):
                store.attach_ordinary_record(
                    accepted,
                    _ordinary_record(accepted),
                    recorder_request_sha256=text_sha256("record-request"),
                    recorder_output_sha256=text_sha256("record-output"),
                    provider_operations=1,
                )

            staging_paths = [
                path for path in observed if path.parent.name.startswith(".rb.")
            ]
            self.assertTrue(staging_paths)
            self.assertLessEqual(
                max(len(str(path)) - len(str(turn_dir)) for path in staging_paths),
                64,
            )

    def test_stale_session_is_soft_and_old_repair_cannot_downgrade_head(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = LeanSceneStore(root)
            first = store.accept(_candidate())
            first_session = store.promote_pi_session(
                first,
                session_id="session-1",
                session_path=root / "session-1",
            )
            self.assertIsNotNone(first_session)

            second = store.accept(
                _candidate(
                    generation=2,
                    parent_turn_id=first.accepted_turn_id,
                    parent_head_sha256=first.receipt_sha256,
                )
            )
            self.assertIsNone(
                store.load_accepted_pi_session(
                    world_id="world-test",
                    branch_id="branch-main",
                )
            )
            self.assertIsNone(
                store.promote_pi_session(
                    first,
                    session_id="session-1",
                    session_path=root / "session-1",
                )
            )

            current = store.promote_pi_session(
                second,
                session_id="session-2",
                session_path=root / "session-2",
            )
            self.assertIsNotNone(current)
            stale_result = store.promote_pi_session(
                first,
                session_id="session-1",
                session_path=root / "session-1",
            )
            self.assertEqual(stale_result, current)
            self.assertEqual(
                store.load_accepted_pi_session(
                    world_id="world-test",
                    branch_id="branch-main",
                ),
                current,
            )

    def test_one_orphan_failure_is_recovered_and_idempotent(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = LeanSceneStore(root)
            accepted = store.accept(_candidate())
            _leave_orphan_failure(store, accepted)

            restarted = LeanSceneStore(root)
            recovered = restarted.load_recording_attempt(accepted)
            self.assertEqual(recovered.attempt_number, 1)
            self.assertIs(recovered.status, RecordingStatus.PENDING_REPAIR)
            repeated = restarted.mark_recording_failure(
                accepted,
                recorder_request_sha256=text_sha256("failed-request"),
                recorder_output_sha256=None,
                provider_operations=1,
                failure_code="transport_interrupted",
            )
            self.assertEqual(repeated, recovered)

            completed = restarted.attach_ordinary_record(
                accepted,
                _ordinary_record(accepted),
                recorder_request_sha256=text_sha256("repair-request"),
                recorder_output_sha256=text_sha256("repair-output"),
                provider_operations=1,
            )
            self.assertEqual(completed.attempt_number, 2)

    def test_orphan_with_changed_identity_fails_closed(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = LeanSceneStore(root)
            accepted = store.accept(_candidate())
            _leave_orphan_failure(store, accepted)
            path = _turn_dir(root) / "RECORDING_ATTEMPT_0001.json"
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["accepted_turn_id"] = "turn-other"
            path.write_text(canonical_json(payload), encoding="utf-8")

            with self.assertRaisesRegex(StateConflictError, "changed its identity"):
                LeanSceneStore(root).load_recording_attempt(accepted)

    def test_multiple_orphan_attempts_fail_as_ambiguous(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = LeanSceneStore(root)
            accepted = store.accept(_candidate())
            _leave_orphan_failure(store, accepted)
            turn_dir = _turn_dir(root)
            payload = json.loads(
                (turn_dir / "RECORDING_ATTEMPT_0001.json").read_text(
                    encoding="utf-8"
                )
            )
            payload["attempt_number"] = 2
            (turn_dir / "RECORDING_ATTEMPT_0002.json").write_text(
                canonical_json(payload),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(StateConflictError, "ambiguous orphan"):
                LeanSceneStore(root).load_recording_attempt(accepted)


if __name__ == "__main__":
    unittest.main()
