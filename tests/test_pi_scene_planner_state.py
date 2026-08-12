from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from cera.errors import ContractValidationError, StateConflictError
from cera.pi_scene.planner_state import (
    PlannerThreadStateStore,
    PlannerThreadStateV1,
)
from cera.serialization import canonical_bytes, text_sha256

COMPATIBILITY = "a" * 64
NEW_COMPATIBILITY = "b" * 64
THIRD_COMPATIBILITY = "c" * 64


class _Backend:
    def __init__(self) -> None:
        self.started = 0
        self.turns = 0
        self.resumable = True

    def start_stored_thread(self, **_kwargs) -> str:
        self.started += 1
        return "thread-new"

    def run_planner_turn(self, **_kwargs):
        self.turns += 1
        return {"ok": True}

    def is_resumable(self, _thread_id: str) -> bool:
        return self.resumable


class PlannerThreadStateStoreTests(unittest.TestCase):
    @staticmethod
    def _plan_once(session) -> None:
        with (
            patch(
                "cera.sequence_first.sessions.planner_turn_prompt",
                return_value="prompt",
            ),
            patch(
                "cera.sequence_first.sessions.ProviderReferenceScopeV1.from_turn",
                return_value="scope",
            ),
        ):
            session.plan(object())  # type: ignore[arg-type]

    @staticmethod
    def _retire_kind(
        store: PlannerThreadStateStore,
        state: PlannerThreadStateV1,
        archive_kind: str,
    ) -> Path:
        if archive_kind == "incompatible":
            if not store.retire_incompatible_thread(
                session_id=state.session_id,
                reasoning_effort=state.reasoning_effort,
                prior_compatibility_sha256=state.compatibility_sha256,
                new_compatibility_sha256=NEW_COMPATIBILITY,
            ):
                raise AssertionError("expected incompatible retirement")
            directory = "INCOMPATIBLE_THREADS"
        elif archive_kind == "interrupted":
            store.archive_interrupted_transport_thread(state)
            directory = "INTERRUPTED_TRANSPORT_THREADS"
        elif archive_kind == "completed":
            store.archive_completed_uncommitted_thread(state)
            directory = "COMPLETED_UNCOMMITTED_THREADS"
        else:
            raise AssertionError("unknown archive kind")
        return next(store.root.rglob(directory))

    def test_new_thread_is_persisted_and_reused_after_restart(self) -> None:
        with TemporaryDirectory() as directory:
            store = PlannerThreadStateStore(Path(directory))
            backend = _Backend()
            session = store.session_factory(compatibility_sha256=COMPATIBILITY)(
                "chat-one", "medium", backend
            )
            with (
                patch(
                    "cera.sequence_first.sessions.planner_turn_prompt",
                    return_value="prompt",
                ),
                patch(
                    "cera.sequence_first.sessions.ProviderReferenceScopeV1.from_turn",
                    return_value="scope",
                ),
            ):
                self.assertEqual(session.plan(object()), {"ok": True})  # type: ignore[arg-type]
            self.assertEqual(backend.started, 1)
            restored = store.session_factory(compatibility_sha256=COMPATIBILITY)(
                "chat-one", "medium", backend
            )
            self.assertEqual(restored.thread_id, "thread-new")

    def test_effort_and_chat_have_isolated_state(self) -> None:
        with TemporaryDirectory() as directory:
            store = PlannerThreadStateStore(Path(directory))
            store.persist(PlannerThreadStateV1("chat-one", "medium", COMPATIBILITY, "thread-a"))
            self.assertIsNone(
                store.load(
                    session_id="chat-two",
                    reasoning_effort="medium",
                    compatibility_sha256=COMPATIBILITY,
                )
            )
            self.assertIsNone(
                store.load(
                    session_id="chat-one",
                    reasoning_effort="high",
                    compatibility_sha256=COMPATIBILITY,
                )
            )

    def test_existing_binding_cannot_be_replaced(self) -> None:
        with TemporaryDirectory() as directory:
            store = PlannerThreadStateStore(Path(directory))
            store.persist(PlannerThreadStateV1("chat-one", "medium", COMPATIBILITY, "thread-a"))
            with self.assertRaises(StateConflictError):
                store.persist(PlannerThreadStateV1("chat-one", "medium", COMPATIBILITY, "thread-b"))

    def test_unknown_field_and_hash_tampering_fail_closed(self) -> None:
        with TemporaryDirectory() as directory:
            store = PlannerThreadStateStore(Path(directory))
            state = PlannerThreadStateV1("chat-one", "medium", COMPATIBILITY, "thread-a")
            store.persist(state)
            path = next(Path(directory).rglob("PLANNER_THREAD_STATE.json"))
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["unexpected"] = True
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(StateConflictError):
                store.load(
                    session_id="chat-one",
                    reasoning_effort="medium",
                    compatibility_sha256=COMPATIBILITY,
                )

    def test_state_hash_tampering_fails_closed(self) -> None:
        with TemporaryDirectory() as directory:
            store = PlannerThreadStateStore(Path(directory))
            state = PlannerThreadStateV1("chat-one", "medium", COMPATIBILITY, "thread-a")
            store.persist(state)
            path = next(Path(directory).rglob("PLANNER_THREAD_STATE.json"))
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["thread_id"] = "thread-tampered"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(StateConflictError, "hash verification"):
                store.load(
                    session_id="chat-one",
                    reasoning_effort="medium",
                    compatibility_sha256=COMPATIBILITY,
                )

    def test_compatibility_mismatch_does_not_reuse_thread(self) -> None:
        with TemporaryDirectory() as directory:
            store = PlannerThreadStateStore(Path(directory))
            store.persist(PlannerThreadStateV1("chat-one", "medium", COMPATIBILITY, "thread-a"))
            with self.assertRaises(StateConflictError):
                store.load(
                    session_id="chat-one",
                    reasoning_effort="medium",
                    compatibility_sha256=NEW_COMPATIBILITY,
                )

    def test_incompatible_thread_is_retired_exactly_once_and_never_reused(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            store = PlannerThreadStateStore(root)
            old_state = PlannerThreadStateV1("chat-one", "medium", COMPATIBILITY, "thread-old")
            store.persist(old_state)
            active_path = next(root.rglob("PLANNER_THREAD_STATE.json"))
            original_bytes = active_path.read_bytes()

            self.assertTrue(
                store.retire_incompatible_thread(
                    session_id="chat-one",
                    reasoning_effort="medium",
                    prior_compatibility_sha256=COMPATIBILITY,
                    new_compatibility_sha256=NEW_COMPATIBILITY,
                )
            )
            archive = next(root.rglob("INCOMPATIBLE_THREADS/thread-*.json"))
            receipt_path = next(root.rglob("INCOMPATIBLE_THREADS/thread-*.receipt.json"))
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            self.assertEqual(archive.read_bytes(), original_bytes)
            self.assertEqual(receipt["prior_compatibility_sha256"], COMPATIBILITY)
            self.assertEqual(receipt["new_compatibility_sha256"], NEW_COMPATIBILITY)
            self.assertFalse(active_path.exists())
            self.assertFalse(
                store.retire_incompatible_thread(
                    session_id="chat-one",
                    reasoning_effort="medium",
                    prior_compatibility_sha256=COMPATIBILITY,
                    new_compatibility_sha256=NEW_COMPATIBILITY,
                )
            )
            self.assertIsNone(
                PlannerThreadStateStore(root).load(
                    session_id="chat-one",
                    reasoning_effort="medium",
                    compatibility_sha256=NEW_COMPATIBILITY,
                )
            )
            with self.assertRaisesRegex(StateConflictError, "incompatible provider"):
                store.persist(
                    PlannerThreadStateV1(
                        "chat-one",
                        "medium",
                        NEW_COMPATIBILITY,
                        "thread-old",
                    )
                )
            replacement = PlannerThreadStateV1(
                "chat-one", "medium", NEW_COMPATIBILITY, "thread-new"
            )
            store.persist(replacement)
            restored = PlannerThreadStateStore(root).load(
                session_id="chat-one",
                reasoning_effort="medium",
                compatibility_sha256=NEW_COMPATIBILITY,
            )
            self.assertEqual(restored, replacement)
            self.assertFalse(
                store.retire_incompatible_thread(
                    session_id="chat-one",
                    reasoning_effort="medium",
                    prior_compatibility_sha256=COMPATIBILITY,
                    new_compatibility_sha256=NEW_COMPATIBILITY,
                )
            )

    def test_incompatible_retirement_reconciles_receipt_first_crash(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            store = PlannerThreadStateStore(root)
            state = PlannerThreadStateV1("chat-one", "medium", COMPATIBILITY, "thread-old")
            store.persist(state)
            active_path = next(root.rglob("PLANNER_THREAD_STATE.json"))
            real_rename = os.rename

            def fail_active_move(
                source: str | os.PathLike[str],
                target: str | os.PathLike[str],
            ) -> None:
                if Path(source) == active_path:
                    raise OSError("simulated interruption")
                real_rename(source, target)

            with patch(
                "cera.pi_scene.planner_state.os.rename",
                side_effect=fail_active_move,
            ):
                with self.assertRaisesRegex(OSError, "simulated interruption"):
                    store.retire_incompatible_thread(
                        session_id="chat-one",
                        reasoning_effort="medium",
                        prior_compatibility_sha256=COMPATIBILITY,
                        new_compatibility_sha256=NEW_COMPATIBILITY,
                    )

            restarted = PlannerThreadStateStore(root)
            self.assertIsNone(
                restarted.active_compatibility_sha256(
                    session_id="chat-one",
                    reasoning_effort="medium",
                )
            )
            self.assertFalse(active_path.exists())
            self.assertEqual(
                next(root.rglob("INCOMPATIBLE_THREADS/thread-*.json")).read_bytes(),
                json.dumps(state.to_payload(), sort_keys=True, separators=(",", ":")).encode(
                    "utf-8"
                ),
            )

    def test_incompatible_retirement_refuses_same_contract_and_tampering(self) -> None:
        with TemporaryDirectory() as directory:
            store = PlannerThreadStateStore(Path(directory))
            store.persist(PlannerThreadStateV1("chat-one", "medium", COMPATIBILITY, "thread-old"))
            with self.assertRaisesRegex(ContractValidationError, "mismatch"):
                store.retire_incompatible_thread(
                    session_id="chat-one",
                    reasoning_effort="medium",
                    prior_compatibility_sha256=COMPATIBILITY,
                    new_compatibility_sha256=COMPATIBILITY,
                )

        for target in ("receipt", "archive"):
            with self.subTest(target=target), TemporaryDirectory() as directory:
                root = Path(directory)
                store = PlannerThreadStateStore(root)
                store.persist(
                    PlannerThreadStateV1("chat-one", "medium", COMPATIBILITY, "thread-old")
                )
                store.retire_incompatible_thread(
                    session_id="chat-one",
                    reasoning_effort="medium",
                    prior_compatibility_sha256=COMPATIBILITY,
                    new_compatibility_sha256=NEW_COMPATIBILITY,
                )
                path = next(
                    root.rglob(
                        "INCOMPATIBLE_THREADS/thread-*.receipt.json"
                        if target == "receipt"
                        else "INCOMPATIBLE_THREADS/thread-*.json"
                    )
                )
                payload = json.loads(path.read_text(encoding="utf-8"))
                payload["disposition" if target == "receipt" else "thread_id"] = "tampered"
                path.write_text(json.dumps(payload), encoding="utf-8")
                with self.assertRaises(StateConflictError):
                    PlannerThreadStateStore(root).active_compatibility_sha256(
                        session_id="chat-one",
                        reasoning_effort="medium",
                    )

    def test_historical_never_resume_archives_do_not_block_new_compatibility(self) -> None:
        archive_kinds = (
            (
                "archive_interrupted_transport_thread",
                "has_interrupted_thread_hash",
            ),
            (
                "archive_completed_uncommitted_thread",
                "has_completed_uncommitted_thread_hash",
            ),
        )
        for archive_name, has_name in archive_kinds:
            with self.subTest(archive=archive_name), TemporaryDirectory() as directory:
                root = Path(directory)
                store = PlannerThreadStateStore(root)
                historical = PlannerThreadStateV1("chat-one", "medium", COMPATIBILITY, "thread-old")
                store.persist(historical)
                getattr(store, archive_name)(historical)
                archive_path = next(
                    value
                    for value in root.rglob("thread-*.json")
                    if not value.name.endswith(".receipt.json")
                )
                receipt_path = archive_path.with_name(
                    archive_path.name.removesuffix(".json") + ".receipt.json"
                )
                frozen_archive = archive_path.read_bytes()
                frozen_receipt = receipt_path.read_bytes()

                restarted = PlannerThreadStateStore(root)
                self.assertIsNone(
                    restarted.load(
                        session_id="chat-one",
                        reasoning_effort="medium",
                        compatibility_sha256=NEW_COMPATIBILITY,
                    )
                )
                backend = _Backend()
                session = restarted.session_factory(compatibility_sha256=NEW_COMPATIBILITY)(
                    "chat-one", "medium", backend
                )
                self._plan_once(session)

                restored = PlannerThreadStateStore(root).load(
                    session_id="chat-one",
                    reasoning_effort="medium",
                    compatibility_sha256=NEW_COMPATIBILITY,
                )
                self.assertIsNotNone(restored)
                self.assertEqual(restored.thread_id, "thread-new")
                self.assertEqual(archive_path.read_bytes(), frozen_archive)
                self.assertEqual(receipt_path.read_bytes(), frozen_receipt)
                self.assertTrue(
                    getattr(restarted, has_name)(
                        session_id="chat-one",
                        reasoning_effort="medium",
                        compatibility_sha256=COMPATIBILITY,
                        thread_id_sha256=text_sha256(historical.thread_id),
                    )
                )
                self.assertFalse(
                    getattr(restarted, has_name)(
                        session_id="chat-one",
                        reasoning_effort="medium",
                        compatibility_sha256=NEW_COMPATIBILITY,
                        thread_id_sha256=text_sha256(historical.thread_id),
                    )
                )

    def test_current_compatibility_never_resume_archive_stays_retired(self) -> None:
        archive_kinds = (
            (
                "archive_interrupted_transport_thread",
                "has_interrupted_thread_hash",
            ),
            (
                "archive_completed_uncommitted_thread",
                "has_completed_uncommitted_thread_hash",
            ),
        )
        for archive_name, has_name in archive_kinds:
            with self.subTest(archive=archive_name), TemporaryDirectory() as directory:
                root = Path(directory)
                store = PlannerThreadStateStore(root)
                retired = PlannerThreadStateV1(
                    "chat-one", "medium", NEW_COMPATIBILITY, "thread-old"
                )
                store.persist(retired)
                getattr(store, archive_name)(retired)

                restarted = PlannerThreadStateStore(root)
                backend = _Backend()
                session = restarted.session_factory(compatibility_sha256=NEW_COMPATIBILITY)(
                    "chat-one", "medium", backend
                )
                self._plan_once(session)
                restored = PlannerThreadStateStore(root).load(
                    session_id="chat-one",
                    reasoning_effort="medium",
                    compatibility_sha256=NEW_COMPATIBILITY,
                )
                self.assertIsNotNone(restored)
                self.assertEqual(restored.thread_id, "thread-new")
                self.assertTrue(
                    getattr(restarted, has_name)(
                        session_id="chat-one",
                        reasoning_effort="medium",
                        compatibility_sha256=NEW_COMPATIBILITY,
                        thread_id_sha256=text_sha256(retired.thread_id),
                    )
                )
                with self.assertRaisesRegex(StateConflictError, "interrupted|completed"):
                    restarted.persist(retired)

    def test_historical_never_resume_archive_is_idempotent_and_tamper_evident(
        self,
    ) -> None:
        archive_kinds = (
            "archive_interrupted_transport_thread",
            "archive_completed_uncommitted_thread",
        )
        for archive_name in archive_kinds:
            with self.subTest(archive=archive_name), TemporaryDirectory() as directory:
                root = Path(directory)
                store = PlannerThreadStateStore(root)
                historical = PlannerThreadStateV1("chat-one", "medium", COMPATIBILITY, "thread-old")
                store.persist(historical)
                archive = getattr(store, archive_name)
                archive(historical)
                archive_path = next(
                    value
                    for value in root.rglob("thread-*.json")
                    if not value.name.endswith(".receipt.json")
                )
                receipt_path = archive_path.with_name(
                    archive_path.name.removesuffix(".json") + ".receipt.json"
                )
                frozen_archive = archive_path.read_bytes()
                frozen_receipt = receipt_path.read_bytes()
                archive(historical)
                self.assertEqual(archive_path.read_bytes(), frozen_archive)
                self.assertEqual(receipt_path.read_bytes(), frozen_receipt)

            for target in ("archive", "receipt"):
                with (
                    self.subTest(archive=archive_name, target=target),
                    TemporaryDirectory() as directory,
                ):
                    root = Path(directory)
                    store = PlannerThreadStateStore(root)
                    historical = PlannerThreadStateV1(
                        "chat-one", "medium", COMPATIBILITY, "thread-old"
                    )
                    store.persist(historical)
                    getattr(store, archive_name)(historical)
                    archive_path = next(
                        value
                        for value in root.rglob("thread-*.json")
                        if not value.name.endswith(".receipt.json")
                    )
                    receipt_path = archive_path.with_name(
                        archive_path.name.removesuffix(".json") + ".receipt.json"
                    )
                    tampered_path = archive_path if target == "archive" else receipt_path
                    payload = json.loads(tampered_path.read_text(encoding="utf-8"))
                    payload["compatibility_sha256"] = NEW_COMPATIBILITY
                    tampered_path.write_text(json.dumps(payload), encoding="utf-8")
                    with self.assertRaises(StateConflictError):
                        PlannerThreadStateStore(root).load(
                            session_id="chat-one",
                            reasoning_effort="medium",
                            compatibility_sha256=NEW_COMPATIBILITY,
                        )

    def test_never_resume_archive_stems_and_receipt_custody_fail_closed(self) -> None:
        archive_kinds = (
            "archive_interrupted_transport_thread",
            "archive_completed_uncommitted_thread",
        )
        for archive_name in archive_kinds:
            with (
                self.subTest(archive=archive_name, case="receipt-only"),
                TemporaryDirectory() as directory,
            ):
                root = Path(directory)
                store = PlannerThreadStateStore(root)
                old = PlannerThreadStateV1(
                    "chat-one", "medium", COMPATIBILITY, "thread-old"
                )
                store.persist(old)
                getattr(store, archive_name)(old)
                archive_path = next(
                    value
                    for value in root.rglob("thread-*.json")
                    if not value.name.endswith(".receipt.json")
                )
                archive_path.unlink()
                with self.assertRaisesRegex(StateConflictError, "lacks its archive"):
                    store.load(
                        session_id="chat-one",
                        reasoning_effort="medium",
                        compatibility_sha256=NEW_COMPATIBILITY,
                    )
                with self.assertRaisesRegex(StateConflictError, "lacks its archive"):
                    store.persist(
                        PlannerThreadStateV1(
                            "chat-one",
                            "medium",
                            NEW_COMPATIBILITY,
                            old.thread_id,
                        )
                    )

            with (
                self.subTest(archive=archive_name, case="renamed-pair"),
                TemporaryDirectory() as directory,
            ):
                root = Path(directory)
                store = PlannerThreadStateStore(root)
                old = PlannerThreadStateV1(
                    "chat-one", "medium", COMPATIBILITY, "thread-old"
                )
                store.persist(old)
                getattr(store, archive_name)(old)
                archive_path = next(
                    value
                    for value in root.rglob("thread-*.json")
                    if not value.name.endswith(".receipt.json")
                )
                receipt_path = archive_path.with_name(
                    archive_path.name.removesuffix(".json") + ".receipt.json"
                )
                forged_archive = archive_path.with_name("thread-forged.json")
                forged_receipt = archive_path.with_name(
                    "thread-forged.receipt.json"
                )
                archive_path.rename(forged_archive)
                receipt_path.rename(forged_receipt)
                with self.assertRaisesRegex(
                    StateConflictError, "entry name changed|filename changed"
                ):
                    store.load(
                        session_id="chat-one",
                        reasoning_effort="medium",
                        compatibility_sha256=NEW_COMPATIBILITY,
                    )

            with (
                self.subTest(archive=archive_name, case="archive-only-recovery"),
                TemporaryDirectory() as directory,
            ):
                root = Path(directory)
                store = PlannerThreadStateStore(root)
                old = PlannerThreadStateV1(
                    "chat-one", "medium", COMPATIBILITY, "thread-old"
                )
                store.persist(old)
                getattr(store, archive_name)(old)
                archive_path = next(
                    value
                    for value in root.rglob("thread-*.json")
                    if not value.name.endswith(".receipt.json")
                )
                receipt_path = archive_path.with_name(
                    archive_path.name.removesuffix(".json") + ".receipt.json"
                )
                exact_receipt = receipt_path.read_bytes()
                receipt_path.unlink()
                self.assertIsNone(
                    PlannerThreadStateStore(root).load(
                        session_id="chat-one",
                        reasoning_effort="medium",
                        compatibility_sha256=NEW_COMPATIBILITY,
                    )
                )
                self.assertEqual(receipt_path.read_bytes(), exact_receipt)
                with self.assertRaisesRegex(StateConflictError, "interrupted|completed"):
                    PlannerThreadStateStore(root).persist(
                        PlannerThreadStateV1(
                            "chat-one",
                            "medium",
                            NEW_COMPATIBILITY,
                            old.thread_id,
                        )
                    )

    def test_never_resume_identity_census_spans_both_archive_kinds(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            store = PlannerThreadStateStore(root)
            state = PlannerThreadStateV1(
                "chat-one", "medium", COMPATIBILITY, "thread-duplicate"
            )
            store.persist(state)
            store.archive_interrupted_transport_thread(state)

            staging = PlannerThreadStateStore(root / "staging")
            staging.persist(state)
            staging.archive_completed_uncommitted_thread(state)
            staged_root = next(
                (root / "staging").rglob("COMPLETED_UNCOMMITTED_THREADS")
            )
            destination = next(root.glob("session-*")) / "COMPLETED_UNCOMMITTED_THREADS"
            destination.mkdir()
            for source in staged_root.iterdir():
                (destination / source.name).write_bytes(source.read_bytes())

            with self.assertRaisesRegex(StateConflictError, "identity is ambiguous"):
                store.load(
                    session_id="chat-one",
                    reasoning_effort="medium",
                    compatibility_sha256=NEW_COMPATIBILITY,
                )
            with self.assertRaisesRegex(StateConflictError, "identity is ambiguous"):
                store.persist(
                    PlannerThreadStateV1(
                        "chat-one", "medium", NEW_COMPATIBILITY, "thread-fresh"
                    )
                )

    def test_distinct_never_resume_identities_reconcile_together(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            store = PlannerThreadStateStore(root)
            incompatible = PlannerThreadStateV1(
                "chat-one", "medium", COMPATIBILITY, "thread-incompatible"
            )
            interrupted = PlannerThreadStateV1(
                "chat-one", "medium", COMPATIBILITY, "thread-interrupted"
            )
            completed = PlannerThreadStateV1(
                "chat-one", "medium", COMPATIBILITY, "thread-completed"
            )
            store.persist(incompatible)
            self.assertTrue(
                store.retire_incompatible_thread(
                    session_id="chat-one",
                    reasoning_effort="medium",
                    prior_compatibility_sha256=COMPATIBILITY,
                    new_compatibility_sha256=NEW_COMPATIBILITY,
                )
            )
            store.persist(interrupted)
            store.archive_interrupted_transport_thread(interrupted)
            store.persist(completed)
            store.archive_completed_uncommitted_thread(completed)

            receipt_paths = tuple(
                path
                for directory_name in (
                    "INTERRUPTED_TRANSPORT_THREADS",
                    "COMPLETED_UNCOMMITTED_THREADS",
                )
                for path in root.rglob(f"{directory_name}/thread-*.receipt.json")
            )
            exact_receipts = {path: path.read_bytes() for path in receipt_paths}
            for path in receipt_paths:
                path.unlink()
            self.assertIsNone(
                PlannerThreadStateStore(root).load(
                    session_id="chat-one",
                    reasoning_effort="medium",
                    compatibility_sha256=NEW_COMPATIBILITY,
                )
            )
            self.assertEqual(
                {path: path.read_bytes() for path in receipt_paths}, exact_receipts
            )
            for retired in (incompatible, interrupted, completed):
                with self.subTest(thread_id=retired.thread_id):
                    with self.assertRaisesRegex(
                        StateConflictError, "interrupted|completed|incompatible"
                    ):
                        PlannerThreadStateStore(root).persist(
                            PlannerThreadStateV1(
                                "chat-one",
                                "medium",
                                NEW_COMPATIBILITY,
                                retired.thread_id,
                            )
                        )

    def test_retired_identity_census_includes_incompatible_archives(self) -> None:
        cases = (
            ("identical-state-cross-disposition", COMPATIBILITY, "interrupted"),
            ("same-thread-different-state", THIRD_COMPATIBILITY, "completed"),
        )
        for case, duplicate_compatibility, archive_kind in cases:
            with self.subTest(case=case), TemporaryDirectory() as directory:
                root = Path(directory)
                state = PlannerThreadStateV1(
                    "chat-one", "medium", COMPATIBILITY, "thread-shared"
                )
                store = PlannerThreadStateStore(root)
                store.persist(state)
                self.assertTrue(
                    store.retire_incompatible_thread(
                        session_id="chat-one",
                        reasoning_effort="medium",
                        prior_compatibility_sha256=COMPATIBILITY,
                        new_compatibility_sha256=NEW_COMPATIBILITY,
                    )
                )

                staging_root = root / "staging"
                staging = PlannerThreadStateStore(staging_root)
                duplicate = PlannerThreadStateV1(
                    "chat-one", "medium", duplicate_compatibility, state.thread_id
                )
                staging.persist(duplicate)
                if archive_kind == "interrupted":
                    staging.archive_interrupted_transport_thread(duplicate)
                    directory_name = "INTERRUPTED_TRANSPORT_THREADS"
                else:
                    staging.archive_completed_uncommitted_thread(duplicate)
                    directory_name = "COMPLETED_UNCOMMITTED_THREADS"
                source = next(staging_root.rglob(directory_name))
                destination = next(root.glob("session-*")) / directory_name
                destination.mkdir()
                for path in source.iterdir():
                    (destination / path.name).write_bytes(path.read_bytes())

                with self.assertRaisesRegex(
                    StateConflictError, "identity is ambiguous"
                ):
                    store.load(
                        session_id="chat-one",
                        reasoning_effort="medium",
                        compatibility_sha256=NEW_COMPATIBILITY,
                    )

        with TemporaryDirectory() as directory:
            root = Path(directory)
            state = PlannerThreadStateV1(
                "chat-one", "medium", COMPATIBILITY, "thread-shared"
            )
            store = PlannerThreadStateStore(root)
            store.persist(state)
            self.assertTrue(
                store.retire_incompatible_thread(
                    session_id="chat-one",
                    reasoning_effort="medium",
                    prior_compatibility_sha256=COMPATIBILITY,
                    new_compatibility_sha256=NEW_COMPATIBILITY,
                )
            )
            staging_root = root / "staging"
            staging = PlannerThreadStateStore(staging_root)
            staging.persist(state)
            self.assertTrue(
                staging.retire_incompatible_thread(
                    session_id="chat-one",
                    reasoning_effort="medium",
                    prior_compatibility_sha256=COMPATIBILITY,
                    new_compatibility_sha256=THIRD_COMPATIBILITY,
                )
            )
            source = next(staging_root.rglob("INCOMPATIBLE_THREADS"))
            destination = next(root.glob("session-*")) / "INCOMPATIBLE_THREADS"
            for path in source.iterdir():
                (destination / path.name).write_bytes(path.read_bytes())
            with self.assertRaisesRegex(
                StateConflictError, "incompatible archive identity is ambiguous"
            ):
                store.load(
                    session_id="chat-one",
                    reasoning_effort="medium",
                    compatibility_sha256=NEW_COMPATIBILITY,
                )

    def test_active_state_cannot_collide_with_any_never_resume_archive(self) -> None:
        archive_kinds = (
            "incompatible",
            "interrupted",
            "completed",
        )
        for archive_kind in archive_kinds:
            for collision in ("exact-state", "same-thread"):
                with (
                    self.subTest(archive=archive_kind, collision=collision),
                    TemporaryDirectory() as directory,
                ):
                    root = Path(directory)
                    store = PlannerThreadStateStore(root)
                    retired = PlannerThreadStateV1(
                        "chat-one", "medium", COMPATIBILITY, "thread-retired"
                    )
                    store.persist(retired)
                    if archive_kind == "incompatible":
                        self.assertTrue(
                            store.retire_incompatible_thread(
                                session_id="chat-one",
                                reasoning_effort="medium",
                                prior_compatibility_sha256=COMPATIBILITY,
                                new_compatibility_sha256=NEW_COMPATIBILITY,
                            )
                        )
                    elif archive_kind == "interrupted":
                        store.archive_interrupted_transport_thread(retired)
                    else:
                        store.archive_completed_uncommitted_thread(retired)

                    active_path = next(root.glob("session-*")) / (
                        "PLANNER_THREAD_STATE.json"
                    )
                    active = (
                        retired
                        if collision == "exact-state"
                        else PlannerThreadStateV1(
                            "chat-one",
                            "medium",
                            NEW_COMPATIBILITY,
                            retired.thread_id,
                        )
                    )
                    active_path.write_bytes(canonical_bytes(active.to_payload()))
                    with self.assertRaisesRegex(
                        StateConflictError, "active thread collides"
                    ):
                        store.load(
                            session_id="chat-one",
                            reasoning_effort="medium",
                            compatibility_sha256=active.compatibility_sha256,
                        )
                    with self.assertRaisesRegex(
                        StateConflictError, "active thread collides"
                    ):
                        store.persist(active)

    def test_distinct_active_state_is_valid_beside_each_archive_kind(self) -> None:
        for archive_kind in ("incompatible", "interrupted", "completed"):
            with self.subTest(archive=archive_kind), TemporaryDirectory() as directory:
                root = Path(directory)
                store = PlannerThreadStateStore(root)
                retired = PlannerThreadStateV1(
                    "chat-one", "medium", COMPATIBILITY, "thread-retired"
                )
                store.persist(retired)
                if archive_kind == "incompatible":
                    self.assertTrue(
                        store.retire_incompatible_thread(
                            session_id="chat-one",
                            reasoning_effort="medium",
                            prior_compatibility_sha256=COMPATIBILITY,
                            new_compatibility_sha256=NEW_COMPATIBILITY,
                        )
                    )
                elif archive_kind == "interrupted":
                    store.archive_interrupted_transport_thread(retired)
                else:
                    store.archive_completed_uncommitted_thread(retired)

                active = PlannerThreadStateV1(
                    "chat-one", "medium", NEW_COMPATIBILITY, "thread-active"
                )
                store.persist(active)
                self.assertEqual(
                    PlannerThreadStateStore(root).load(
                        session_id="chat-one",
                        reasoning_effort="medium",
                        compatibility_sha256=NEW_COMPATIBILITY,
                    ),
                    active,
                )

    def test_archive_directory_census_rejects_outside_grammar_renames(self) -> None:
        for archive_kind in ("incompatible", "interrupted", "completed"):
            with self.subTest(archive=archive_kind), TemporaryDirectory() as directory:
                root = Path(directory)
                store = PlannerThreadStateStore(root)
                retired = PlannerThreadStateV1(
                    "chat-one", "medium", COMPATIBILITY, "thread-retired"
                )
                store.persist(retired)
                archive_root = self._retire_kind(store, retired, archive_kind)
                archive_path = next(
                    path
                    for path in archive_root.glob("*.json")
                    if not path.name.endswith(".receipt.json")
                )
                receipt_path = next(archive_root.glob("*.receipt.json"))
                archive_path.rename(archive_root / "forged.json")
                receipt_path.rename(archive_root / "forged.receipt.json")

                with self.assertRaisesRegex(StateConflictError, "entry name changed"):
                    store.load(
                        session_id="chat-one",
                        reasoning_effort="medium",
                        compatibility_sha256=NEW_COMPATIBILITY,
                    )
                with self.assertRaisesRegex(StateConflictError, "entry name changed"):
                    store.persist(
                        PlannerThreadStateV1(
                            "chat-one", "medium", NEW_COMPATIBILITY, retired.thread_id
                        )
                    )

    def test_archive_directory_census_rejects_unknown_and_unsafe_entries(self) -> None:
        for archive_kind in ("incompatible", "interrupted", "completed"):
            for entry_kind in ("unknown", "hidden", "directory", "atomic-junk"):
                with (
                    self.subTest(archive=archive_kind, entry=entry_kind),
                    TemporaryDirectory() as directory,
                ):
                    root = Path(directory)
                    store = PlannerThreadStateStore(root)
                    retired = PlannerThreadStateV1(
                        "chat-one", "medium", COMPATIBILITY, "thread-retired"
                    )
                    store.persist(retired)
                    archive_root = self._retire_kind(store, retired, archive_kind)
                    if entry_kind == "directory":
                        (archive_root / "unknown").mkdir()
                        expected = "unsafe entry"
                    elif entry_kind == "atomic-junk":
                        (archive_root / f".planner-state-{'1' * 32}.tmp").write_bytes(
                            b"not-a-receipt"
                        )
                        expected = "unreadable|changed custody"
                    else:
                        name = "unknown.bin" if entry_kind == "unknown" else ".hidden"
                        (archive_root / name).write_bytes(b"unknown")
                        expected = "unknown entry"
                    with self.assertRaisesRegex(StateConflictError, expected):
                        store.load(
                            session_id="chat-one",
                            reasoning_effort="medium",
                            compatibility_sha256=NEW_COMPATIBILITY,
                        )

    def test_archive_directory_census_rejects_symlinks_where_supported(self) -> None:
        supported = False
        for archive_kind in ("incompatible", "interrupted", "completed"):
            with self.subTest(archive=archive_kind), TemporaryDirectory() as directory:
                root = Path(directory)
                store = PlannerThreadStateStore(root)
                retired = PlannerThreadStateV1(
                    "chat-one", "medium", COMPATIBILITY, "thread-retired"
                )
                store.persist(retired)
                archive_root = self._retire_kind(store, retired, archive_kind)
                target = next(archive_root.glob("*.json"))
                link = archive_root / "unknown-link"
                try:
                    link.symlink_to(target)
                except OSError:
                    continue
                supported = True
                with self.assertRaisesRegex(StateConflictError, "unsafe entry"):
                    store.load(
                        session_id="chat-one",
                        reasoning_effort="medium",
                        compatibility_sha256=NEW_COMPATIBILITY,
                    )
        if not supported:
            self.skipTest("file symlinks are unavailable on this host")

    def test_legitimate_atomic_receipt_crash_windows_reconcile(self) -> None:
        for archive_kind in ("incompatible", "interrupted", "completed"):
            with self.subTest(archive=archive_kind), TemporaryDirectory() as directory:
                root = Path(directory)
                store = PlannerThreadStateStore(root)
                retired = PlannerThreadStateV1(
                    "chat-one", "medium", COMPATIBILITY, "thread-retired"
                )
                store.persist(retired)
                archive_root = self._retire_kind(store, retired, archive_kind)
                archive_path = next(
                    path
                    for path in archive_root.glob("*.json")
                    if not path.name.endswith(".receipt.json")
                )
                receipt_path = next(archive_root.glob("*.receipt.json"))
                exact_archive = archive_path.read_bytes()
                exact_receipt = receipt_path.read_bytes()
                temporary = archive_root / f".planner-state-{'1' * 32}.tmp"
                receipt_path.rename(temporary)
                if archive_kind == "incompatible":
                    active_path = archive_root.parent / "PLANNER_THREAD_STATE.json"
                    archive_path.rename(active_path)

                self.assertIsNone(
                    PlannerThreadStateStore(root).load(
                        session_id="chat-one",
                        reasoning_effort="medium",
                        compatibility_sha256=NEW_COMPATIBILITY,
                    )
                )
                self.assertEqual(archive_path.read_bytes(), exact_archive)
                self.assertEqual(receipt_path.read_bytes(), exact_receipt)
                self.assertFalse(temporary.exists())


if __name__ == "__main__":
    unittest.main()
