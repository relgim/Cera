from __future__ import annotations

import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier, Event, Lock

from cera.continuous.call_ledger import (
    ContinuousProviderCallLedger,
    ProviderCallState,
)
from cera.errors import StateConflictError


class ContinuousProviderCallLedgerConcurrencyTests(unittest.TestCase):
    def test_concurrent_roles_reserve_unique_calls_then_dispatch_in_parallel(self) -> None:
        with TemporaryDirectory() as directory:
            ledger = ContinuousProviderCallLedger(
                Path(directory).resolve() / "calls.jsonl",
                maximum_calls=2,
            )
            dispatch_barrier = Barrier(2)

            def run(owner: str) -> str:
                def dispatch() -> str:
                    dispatch_barrier.wait(timeout=5)
                    return owner

                return ledger.execute(
                    owner=owner,
                    operation="validate_candidate",
                    route=f"route-{owner}",
                    model="gpt-5.6-sol",
                    effort="medium",
                    dispatch=dispatch,
                    finalize=lambda _raw: owner,
                    stored_thread_sha256=("a" if owner == "validator" else "b") * 64,
                )

            with ThreadPoolExecutor(max_workers=2) as pool:
                validator = pool.submit(run, "validator")
                reader = pool.submit(run, "reader")
                results = {
                    validator.result(timeout=10),
                    reader.result(timeout=10),
                }

            self.assertEqual(results, {"validator", "reader"})
            events = ledger.events
            self.assertEqual(
                [event["event_index"] for event in events],
                list(range(1, len(events) + 1)),
            )
            prepared = [
                event for event in events if event["state"] == ProviderCallState.PREPARED.value
            ]
            self.assertEqual(len(prepared), 2)
            self.assertEqual(len({event["call_id"] for event in prepared}), 2)
            self.assertEqual(ledger.dispatched_call_count, 2)

    def test_concurrent_ceiling_reservation_allows_exactly_one_dispatch(self) -> None:
        with TemporaryDirectory() as directory:
            ledger = ContinuousProviderCallLedger(
                Path(directory).resolve() / "calls.jsonl",
                maximum_calls=1,
            )
            dispatch_entered = Event()
            release_dispatch = Event()
            dispatch_count = 0
            count_lock = Lock()

            def dispatch() -> str:
                nonlocal dispatch_count
                with count_lock:
                    dispatch_count += 1
                dispatch_entered.set()
                if not release_dispatch.wait(timeout=5):
                    raise AssertionError("test did not release provider dispatch")
                return "ok"

            def run(owner: str) -> str:
                return ledger.execute(
                    owner=owner,
                    operation="validate_candidate",
                    route=f"route-{owner}",
                    model="gpt-5.6-sol",
                    effort="medium",
                    dispatch=dispatch,
                    finalize=lambda raw: raw,
                    stored_thread_sha256=("a" if owner == "validator" else "b") * 64,
                )

            with ThreadPoolExecutor(max_workers=2) as pool:
                first = pool.submit(run, "validator")
                self.assertTrue(dispatch_entered.wait(timeout=5))
                second = pool.submit(run, "reader")
                with self.assertRaises(StateConflictError):
                    second.result(timeout=5)
                release_dispatch.set()
                self.assertEqual(first.result(timeout=5), "ok")

            self.assertEqual(dispatch_count, 1)
            self.assertEqual(ledger.dispatched_call_count, 1)


if __name__ == "__main__":
    unittest.main()
