"""Provider-free manual transport Retry lifecycle tests."""

from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from threading import Event, Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from cera.continuous.call_ledger import ContinuousProviderCallLedger
from cera.errors import ContractValidationError, ErrorCode, StateConflictError
from cera.pi_scene.http import (
    PiSceneHttpAdapter,
    PiSceneManualTransportRetryError,
    PiSceneServerConfigV1,
    build_pi_scene_server,
)
from cera.pi_scene.planner_state import PlannerThreadStateStore, PlannerThreadStateV1
from cera.pi_scene.request_journal import (
    PiSceneRequestJournal,
    PlannerResultUnavailableError,
    RequestReplayPendingError,
)
from cera.pi_scene.transport_retry import (
    PiSceneProviderLedgerSnapshotV1,
    PiSceneTransportEffectSnapshotV1,
    PiSceneZeroEffectProofV1,
    build_provider_failure_evidence,
    classify_planner_call_events,
    provider_failure_detail_sha256,
    provider_ledger_dispatched_call_count,
)
from cera.providers.models import ProviderTransportError
from cera.semantic_validation import SemanticVerdict
from cera.serialization import canonical_sha256
from scripts.run_pi_scene_lean_server import _PiScenePlannerRegistry

from .test_pi_scene_http_session_review import controls
from .test_pi_scene_lean_v1 import turn
from .test_pi_scene_request_journal import _binding, _payload, _progress
from .test_pi_scene_semantic_runtime import _runtime, _SemanticValidator


class _FakeThreadRotation:
    """Provider-free proof that Retry archives A and dispatches only fresh B."""

    def __init__(self) -> None:
        self.active = "a" * 64
        self.rotations: list[str] = []
        self.completed_abandonments: list[str] = []
        self._next = 11

    def reinitialize(self, *_args: object) -> None:
        expected = _args[-1]
        if expected != self.active:
            raise StateConflictError("fake interrupted thread changed")
        self.rotations.append(self.active)
        self.active = ""

    def initialize(self, *_args: object) -> str:
        if not self.active:
            self._next += 1
            self.active = f"{self._next:064x}"
        return self.active

    def abandon_completed(self, *_args: object) -> None:
        expected = _args[-1]
        if expected != self.active:
            raise StateConflictError("fake completed thread changed")
        self.completed_abandonments.append(self.active)
        self.active = ""

    def snapshot(self, *_args: object) -> str | None:
        return self.active or None

    def adapter_kwargs(self) -> dict[str, object]:
        return {
            "transport_retry_reinitializer": self.reinitialize,
            "transport_retry_fresh_thread_initializer": self.initialize,
            "transport_retry_active_thread_snapshot": self.snapshot,
            "transport_completed_planner_abandoner": self.abandon_completed,
        }


class _InjectedProcessCrash(BaseException):
    """Simulate process loss without running the adapter's exception recovery."""


def _run_direct_staged_ordinary(
    adapter: PiSceneHttpAdapter,
    payload: dict[str, object],
    *,
    crash_point: str | None,
):
    """Run the ordinary coordinator while preserving an injected crash seam."""

    request, request_turn, binding = adapter._prepare_bound_request(payload)
    journal = adapter._durable_request_journal()
    journal.begin(binding)
    effect_before = adapter._transport_effect_snapshot(request_turn)

    def stage() -> None:
        ledger_before = adapter._provider_ledger_snapshot()
        snapshot = adapter.transport_retry_active_thread_snapshot
        journal.stage_transport_dispatch(
            binding,
            normalized_request=payload,
            logic_owner="planner",
            resolved_route=request.route.value,
            turn_context_sha256=canonical_sha256(request_turn),
            effect_before=effect_before,
            provider_ledger_before=ledger_before,
            planner_thread_sha256=(
                None if snapshot is None else snapshot(binding, request_turn, "planner")
            ),
        )

    def close() -> None:
        if crash_point == "before_completion_marker":
            raise _InjectedProcessCrash()
        adapter._transport_retry_controller(journal).close_staged_planner_dispatch(
            binding=binding,
            turn=request_turn,
        )
        if crash_point == "after_completion_marker":
            raise _InjectedProcessCrash()

    with adapter.coordinator.planner_transport_custody(
        on_start=stage,
        on_success=close,
    ):
        review = adapter.coordinator.start_ordinary(request_turn)
    return request, request_turn, binding, review


def _install_successful_ledger_planner(
    planner,
    ledger: ContinuousProviderCallLedger,
    rotation: _FakeThreadRotation,
) -> tuple[object, list[str]]:
    """Wrap the provider-free fixture Planner in the real Sol ledger graph."""

    original_plan = planner.plan
    used_threads: list[str] = []

    def ledger_plan(request):
        if not rotation.active:
            rotation.initialize()
        used_threads.append(rotation.active)
        return ledger.execute(
            owner="planner",
            operation=f"plan_{len(used_threads):04d}",
            route="test",
            model="gpt-5.6-sol",
            effort="xhigh",
            dispatch=lambda: original_plan(request),
            finalize=lambda value: value,
            stored_thread_sha256=rotation.active,
        )

    planner.plan = ledger_plan
    return original_plan, used_threads


def _ledger_snapshot(call_count: int) -> PiSceneProviderLedgerSnapshotV1:
    events: list[dict[str, object]] = []
    for number in range(1, call_count + 1):
        call_id = f"call_{number:024d}"
        for state in ("prepared_not_invoked", "transport_invoked", "provider_failed"):
            events.append(
                {
                    "schema_version": "cera.continuous_provider_call_ledger_event.v3",
                    "call_id": call_id,
                    "event_index": len(events) + 1,
                    "owner": "planner",
                    "operation": f"plan_{number:04d}",
                    "state": state,
                    "route": "test",
                    "model": "gpt-5.6-sol",
                    "effort": "xhigh",
                    "provider_receipt_sha256": None,
                    "failure_receipt_sha256": None,
                    "operation_telemetry_sha256": None,
                    "tool_sequence_sha256": None,
                    "stored_thread_sha256": f"{number:064x}",
                    "failure_type": (
                        "ProviderTransportError" if state == "provider_failed" else None
                    ),
                    "recorded_at_utc": f"2026-08-09T00:00:0{number}+00:00",
                }
            )
    return PiSceneProviderLedgerSnapshotV1(
        schema_version=PiSceneProviderLedgerSnapshotV1.SCHEMA_VERSION,
        dispatched_call_count=call_count,
        events=tuple(events),
    )


def _append_ledger_call(
    before: PiSceneProviderLedgerSnapshotV1,
    *,
    call_number: int,
    thread_sha256: str,
    states: tuple[str, ...] = (
        "prepared_not_invoked",
        "transport_invoked",
        "provider_failed",
    ),
) -> PiSceneProviderLedgerSnapshotV1:
    events = [dict(value) for value in before.events]
    call_id = f"call_{call_number:024d}"
    for state in states:
        events.append(
            {
                "schema_version": "cera.continuous_provider_call_ledger_event.v3",
                "call_id": call_id,
                "event_index": len(events) + 1,
                "owner": "planner",
                "operation": f"plan_{call_number:04d}",
                "state": state,
                "route": "test",
                "model": "gpt-5.6-sol",
                "effort": "xhigh",
                "provider_receipt_sha256": None,
                "failure_receipt_sha256": None,
                "operation_telemetry_sha256": None,
                "tool_sequence_sha256": None,
                "stored_thread_sha256": thread_sha256,
                "failure_type": (
                    "ProviderTransportError"
                    if state in {"provider_failed", "pretransport_failed"}
                    else (
                        "ContractValidationError"
                        if state == "provider_completed_post_validation_failed"
                        else None
                    )
                ),
                "recorded_at_utc": (f"2026-08-09T00:10:{len(events) % 60:02d}+00:00"),
            }
        )
    return PiSceneProviderLedgerSnapshotV1(
        schema_version=PiSceneProviderLedgerSnapshotV1.SCHEMA_VERSION,
        dispatched_call_count=provider_ledger_dispatched_call_count(events),
        events=tuple(events),
    )


def _proof_for_span(
    request_id: str,
    *,
    before: PiSceneProviderLedgerSnapshotV1,
    after: PiSceneProviderLedgerSnapshotV1,
    effect: PiSceneTransportEffectSnapshotV1 | None = None,
) -> PiSceneZeroEffectProofV1:
    selected_effect = effect or _proof(request_id).before_snapshot
    evidence = build_provider_failure_evidence(
        before=before,
        after=after,
        provider_operation_submitted=True,
        provider_failure_detail_sha256=provider_failure_detail_sha256(
            None,
            call_events=after.events[before.event_count :],
        ),
    )
    return PiSceneZeroEffectProofV1(
        schema_version=PiSceneZeroEffectProofV1.SCHEMA_VERSION,
        request_id=request_id,
        logic_owner="planner",
        resolved_route="ordinary",
        turn_context_sha256=canonical_sha256({"turn": "alpha"}),
        before_snapshot=selected_effect,
        after_snapshot=selected_effect,
        provider_failure_evidence=evidence,
        candidate_effect_absent=True,
        review_effect_absent=True,
        accepted_effect_absent=True,
        recording_effect_absent=True,
        branch_head_effect_absent=True,
    )


def _proof(request_id: str, *, call_number: int = 1) -> PiSceneZeroEffectProofV1:
    effect = PiSceneTransportEffectSnapshotV1(
        schema_version=PiSceneTransportEffectSnapshotV1.SCHEMA_VERSION,
        world_id="world-alpha",
        branch_id="branch-alpha",
        accepted_turn_id=None,
        accepted_receipt_sha256=None,
        accepted_head_sha256=canonical_sha256({"generation": 0}),
        unresolved_review_sha256=None,
        recording_attempt_sha256=None,
    )
    before = _ledger_snapshot(call_number - 1)
    after = _ledger_snapshot(call_number)
    evidence = build_provider_failure_evidence(
        before=before,
        after=after,
        provider_operation_submitted=True,
        provider_failure_detail_sha256=provider_failure_detail_sha256(
            None,
            call_events=after.events[before.event_count :],
        ),
    )
    return PiSceneZeroEffectProofV1(
        schema_version=PiSceneZeroEffectProofV1.SCHEMA_VERSION,
        request_id=request_id,
        logic_owner="planner",
        resolved_route="ordinary",
        turn_context_sha256=canonical_sha256({"turn": "alpha"}),
        before_snapshot=effect,
        after_snapshot=effect,
        provider_failure_evidence=evidence,
        candidate_effect_absent=True,
        review_effect_absent=True,
        accepted_effect_absent=True,
        recording_effect_absent=True,
        branch_head_effect_absent=True,
    )


def _live_ledger_snapshot(
    ledger: ContinuousProviderCallLedger,
) -> PiSceneProviderLedgerSnapshotV1:
    return PiSceneProviderLedgerSnapshotV1(
        schema_version=PiSceneProviderLedgerSnapshotV1.SCHEMA_VERSION,
        dispatched_call_count=ledger.dispatched_call_count,
        events=tuple(ledger.events),
    )


class PiSceneTransportRetryJournalTests(unittest.TestCase):
    def test_inspection_cannot_repair_authority_during_another_entry_transition(
        self,
    ) -> None:
        class PausingJournal(PiSceneRequestJournal):
            authority_written = Event()
            resume = Event()

            def _write_transport_authority(self, *args, **kwargs):
                value = super()._write_transport_authority(*args, **kwargs)
                ledger = kwargs.get("transport_ledger")
                if isinstance(ledger, dict) and ledger["actions"][-1]["phase"] == "authorized":
                    self.authority_written.set()
                    self.resume.wait(timeout=5)
                return value

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            journal_root = root / "journal"
            protected_root = root / "protected"
            initial = PiSceneRequestJournal(
                journal_root,
                protected_retry_root=protected_root,
            )
            binding = _binding()
            initial.begin(binding)
            receipt = initial.record_transport_failure(
                binding,
                normalized_request=_payload(binding.session_id),
                logic_owner="planner",
                provider_error_code=ErrorCode.REASONER_UNAVAILABLE.value,
                provider_operations_observed=1,
                effect_proof=_proof(binding.request_id),
                provider_ledger_before=_ledger_snapshot(0),
            )
            writer = PausingJournal(
                journal_root,
                protected_retry_root=protected_root,
            )
            outcomes: list[object] = []

            def authorize() -> None:
                try:
                    outcomes.append(writer.authorize_transport_retry(receipt.retry_id))
                except Exception as exc:  # pragma: no cover - asserted below
                    outcomes.append(exc)

            thread = Thread(target=authorize)
            thread.start()
            self.assertTrue(writer.authority_written.wait(timeout=5))
            reader = PiSceneRequestJournal(
                journal_root,
                protected_retry_root=protected_root,
            )
            with self.assertRaises(RequestReplayPendingError):
                reader.inspect_transport_retry(binding)
            writer.resume.set()
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive())
            self.assertEqual(len(outcomes), 1)
            self.assertFalse(isinstance(outcomes[0], Exception))
            self.assertEqual(
                reader.inspect_transport_retry(binding)["actions"][-1]["phase"],
                "authorized",
            )

    def test_terminal_entry_crash_repairs_action_redacts_and_unblocks_branch(
        self,
    ) -> None:
        class CrashBeforeActionSuccessJournal(PiSceneRequestJournal):
            crash_once = True

            def _mark_transport_retry_succeeded_locked(self, *args, **kwargs):
                if self.crash_once:
                    self.crash_once = False
                    raise RuntimeError("injected terminal-before-action crash")
                return super()._mark_transport_retry_succeeded_locked(*args, **kwargs)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            journal_root = root / "journal"
            protected_root = root / "protected"
            journal = CrashBeforeActionSuccessJournal(
                journal_root,
                protected_retry_root=protected_root,
            )
            binding = _binding()
            journal.begin(binding)
            receipt = journal.record_transport_failure(
                binding,
                normalized_request=_payload(binding.session_id),
                logic_owner="planner",
                provider_error_code=ErrorCode.REASONER_UNAVAILABLE.value,
                provider_operations_observed=1,
                effect_proof=_proof(binding.request_id),
                provider_ledger_before=_ledger_snapshot(0),
            )
            journal.authorize_transport_retry(receipt.retry_id)
            journal.mark_transport_owner_rotated(receipt.retry_id)
            journal.bind_transport_fresh_thread(receipt.retry_id, "b" * 64)
            journal.mark_transport_dispatch_started(receipt.retry_id, _ledger_snapshot(1))
            journal.bind_progress(binding, _progress(binding))
            with self.assertRaisesRegex(RuntimeError, "terminal-before-action"):
                journal.complete(binding, {"cera": {"status": "accepted"}})

            restarted = PiSceneRequestJournal(
                journal_root,
                protected_retry_root=protected_root,
            )
            self.assertIsNone(
                restarted.active_transport_dispatch_for_scope(
                    session_id=binding.session_id,
                    world_id=binding.world_id,
                    branch_id=binding.branch_id,
                )
            )
            recovered = restarted.prepare_transport_retry(receipt.retry_id)
            self.assertEqual(recovered.action_phase, "succeeded")
            self.assertIsNotNone(recovered.replayed_terminal_response)
            self.assertIsNone(
                restarted.actionable_transport_failure_for_scope(
                    session_id=binding.session_id,
                    world_id=binding.world_id,
                    branch_id=binding.branch_id,
                )
            )
            authority = next(
                (protected_root / "DISPATCH_AUTHORITY").glob("request-*.json")
            ).read_text(encoding="utf-8")
            self.assertNotIn("messages", authority)
            self.assertTrue(restarted.begin(binding).replayed)

    def test_block_publication_crash_is_redacted_by_next_scope_inspection(self) -> None:
        class CrashBeforeBlockRedactionJournal(PiSceneRequestJournal):
            crash_once = True

            def _finalize_transport_authority_locked(self, *args, **kwargs):
                if self.crash_once:
                    self.crash_once = False
                    raise RuntimeError("injected block-before-redaction crash")
                return super()._finalize_transport_authority_locked(*args, **kwargs)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            journal_root = root / "journal"
            protected_root = root / "protected"
            journal = CrashBeforeBlockRedactionJournal(
                journal_root,
                protected_retry_root=protected_root,
            )
            binding = _binding()
            journal.begin(binding)
            receipt = journal.record_transport_failure(
                binding,
                normalized_request=_payload(binding.session_id),
                logic_owner="planner",
                provider_error_code=ErrorCode.REASONER_UNAVAILABLE.value,
                provider_operations_observed=1,
                effect_proof=_proof(binding.request_id),
                provider_ledger_before=_ledger_snapshot(0),
            )
            with self.assertRaisesRegex(RuntimeError, "block-before-redaction"):
                journal.block_transport_retry(receipt.retry_id, "effect_state_changed")

            raw_authority = next((protected_root / "DISPATCH_AUTHORITY").glob("request-*.json"))
            self.assertIn("messages", raw_authority.read_text(encoding="utf-8"))
            restarted = PiSceneRequestJournal(
                journal_root,
                protected_retry_root=protected_root,
            )
            self.assertIsNone(
                restarted.active_transport_dispatch_for_scope(
                    session_id=binding.session_id,
                    world_id=binding.world_id,
                    branch_id=binding.branch_id,
                )
            )
            self.assertNotIn("messages", raw_authority.read_text(encoding="utf-8"))
            self.assertEqual(
                restarted.inspect_transport_retry(binding)["actions"][-1]["phase"],
                "blocked",
            )

    def test_protected_authority_repairs_stale_safe_projection_at_every_phase(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            journal_root = root / "journal"
            protected_root = root / "protected"
            journal = PiSceneRequestJournal(
                journal_root,
                protected_retry_root=protected_root,
            )
            binding = _binding()
            journal.begin(binding)
            receipt = journal.record_transport_failure(
                binding,
                normalized_request=_payload(binding.session_id),
                logic_owner="planner",
                provider_error_code=ErrorCode.REASONER_UNAVAILABLE.value,
                provider_operations_observed=1,
                effect_proof=_proof(binding.request_id),
                provider_ledger_before=_ledger_snapshot(0),
            )
            safe_path = journal.entry_path(binding).with_suffix(".transport.json")

            def assert_authority_ahead_rebuild(
                previous_safe: str,
                expected_phase: str,
            ) -> None:
                safe_path.write_text(previous_safe, encoding="utf-8")
                restarted = PiSceneRequestJournal(
                    journal_root,
                    protected_retry_root=protected_root,
                )
                self.assertEqual(
                    restarted.prepare_transport_retry(receipt.retry_id).action_phase,
                    expected_phase,
                )
                self.assertNotEqual(
                    safe_path.read_text(encoding="utf-8"),
                    previous_safe,
                )

            eligible = safe_path.read_text(encoding="utf-8")
            journal.authorize_transport_retry(receipt.retry_id)
            assert_authority_ahead_rebuild(eligible, "authorized")
            authorized = safe_path.read_text(encoding="utf-8")
            journal.mark_transport_owner_rotated(receipt.retry_id)
            assert_authority_ahead_rebuild(authorized, "owner_rotated")
            journal.bind_transport_fresh_thread(receipt.retry_id, "b" * 64)
            owner_rotated = safe_path.read_text(encoding="utf-8")
            journal.mark_transport_dispatch_started(
                receipt.retry_id,
                _ledger_snapshot(1),
            )
            assert_authority_ahead_rebuild(owner_rotated, "dispatch_started")
            dispatch_started = safe_path.read_text(encoding="utf-8")
            journal.bind_progress(binding, _progress(binding))
            response = journal.complete(binding, {"cera": {"status": "accepted"}})
            safe_path.write_text(dispatch_started, encoding="utf-8")
            terminal = PiSceneRequestJournal(
                journal_root,
                protected_retry_root=protected_root,
            ).prepare_transport_retry(receipt.retry_id)
            self.assertEqual(terminal.action_phase, "succeeded")
            self.assertEqual(terminal.replayed_terminal_response, response)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            journal = PiSceneRequestJournal(
                root / "journal",
                protected_retry_root=root / "protected",
            )
            binding = _binding()
            journal.begin(binding)
            receipt = journal.record_transport_failure(
                binding,
                normalized_request=_payload(binding.session_id),
                logic_owner="planner",
                provider_error_code=ErrorCode.REASONER_UNAVAILABLE.value,
                provider_operations_observed=1,
                effect_proof=_proof(binding.request_id),
                provider_ledger_before=_ledger_snapshot(0),
            )
            safe_path = journal.entry_path(binding).with_suffix(".transport.json")
            eligible = safe_path.read_text(encoding="utf-8")
            journal.block_transport_retry(receipt.retry_id, "effect_state_changed")
            safe_path.write_text(eligible, encoding="utf-8")
            blocked = PiSceneRequestJournal(
                root / "journal",
                protected_retry_root=root / "protected",
            ).prepare_transport_retry(receipt.retry_id)
            self.assertEqual(blocked.action_phase, "blocked")
            authority_text = next(
                (root / "protected" / "DISPATCH_AUTHORITY").glob("request-*.json")
            ).read_text(encoding="utf-8")
            self.assertNotIn("messages", authority_text)

    def test_retry_chain_allows_verified_unrelated_sol_prefix_gap(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            journal = PiSceneRequestJournal(
                root / "journal",
                protected_retry_root=root / "protected",
            )
            binding = _binding()
            payload = _payload(binding.session_id)
            journal.begin(binding)
            empty = _ledger_snapshot(0)
            failed_a = _append_ledger_call(
                empty,
                call_number=1,
                thread_sha256="a" * 64,
            )
            first = journal.record_transport_failure(
                binding,
                normalized_request=payload,
                logic_owner="planner",
                provider_error_code=ErrorCode.REASONER_UNAVAILABLE.value,
                provider_operations_observed=1,
                effect_proof=_proof_for_span(
                    binding.request_id,
                    before=empty,
                    after=failed_a,
                ),
                provider_ledger_before=empty,
            )
            journal.authorize_transport_retry(first.retry_id)
            journal.mark_transport_owner_rotated(first.retry_id)
            unrelated = _append_ledger_call(
                failed_a,
                call_number=2,
                thread_sha256="c" * 64,
            )
            journal.bind_transport_fresh_thread(first.retry_id, "b" * 64)
            journal.mark_transport_dispatch_started(first.retry_id, unrelated)
            failed_b = _append_ledger_call(
                unrelated,
                call_number=3,
                thread_sha256="b" * 64,
            )
            second = journal.record_transport_failure(
                binding,
                normalized_request=payload,
                logic_owner="planner",
                provider_error_code=ErrorCode.REASONER_UNAVAILABLE.value,
                provider_operations_observed=1,
                effect_proof=_proof_for_span(
                    binding.request_id,
                    before=unrelated,
                    after=failed_b,
                ),
                provider_ledger_before=unrelated,
            )
            recovered = journal.prepare_transport_retry(first.retry_id)
            self.assertEqual(recovered.superseding_failure, second)
            evidence = journal.inspect_transport_retry(binding)
            bridge = evidence["failures"][1]["provider_prefix_bridge"]
            self.assertEqual(len(bridge["intervening_events"]), 3)
            self.assertEqual(
                [
                    attempt["provider_operations_observed"]
                    for attempt in (
                        first.to_payload(),
                        second.to_payload(),
                    )
                ],
                [1, 1],
            )

    def test_retry_cannot_bind_a_previously_failed_thread(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            journal = PiSceneRequestJournal(
                root / "journal",
                protected_retry_root=root / "protected",
            )
            binding = _binding()
            journal.begin(binding)
            receipt = journal.record_transport_failure(
                binding,
                normalized_request=_payload(binding.session_id),
                logic_owner="planner",
                provider_error_code=ErrorCode.REASONER_UNAVAILABLE.value,
                provider_operations_observed=1,
                effect_proof=_proof(binding.request_id),
                provider_ledger_before=_ledger_snapshot(0),
            )
            journal.authorize_transport_retry(receipt.retry_id)
            journal.mark_transport_owner_rotated(receipt.retry_id)
            with self.assertRaisesRegex(
                StateConflictError,
                "repeats a failed thread",
            ):
                journal.bind_transport_fresh_thread(receipt.retry_id, f"{1:064x}")

    def test_retry_chain_is_manual_restart_safe_and_never_redispatches_old_action(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            journal_root = root / "journal"
            protected_root = root / "protected"
            journal = PiSceneRequestJournal(
                journal_root,
                protected_retry_root=protected_root,
            )
            binding = _binding()
            payload = _payload(binding.session_id)
            journal.begin(binding)
            first = journal.record_transport_failure(
                binding,
                normalized_request=payload,
                logic_owner="planner",
                provider_error_code=ErrorCode.REASONER_UNAVAILABLE.value,
                provider_operations_observed=1,
                effect_proof=_proof(binding.request_id),
                provider_ledger_before=_ledger_snapshot(0),
            )

            authorized = journal.authorize_transport_retry(first.retry_id)
            self.assertEqual(authorized.binding, binding)
            self.assertEqual(authorized.normalized_request, payload)
            self.assertIsNone(authorized.superseding_failure)
            self.assertEqual(authorized.action_phase, "authorized")
            journal = PiSceneRequestJournal(
                journal_root,
                protected_retry_root=protected_root,
            )
            resumed = journal.authorize_transport_retry(first.retry_id)
            self.assertEqual(resumed.action_phase, "authorized")
            journal.mark_transport_owner_rotated(first.retry_id)
            journal.bind_transport_fresh_thread(first.retry_id, f"{2:064x}")
            journal.mark_transport_dispatch_started(
                first.retry_id,
                _ledger_snapshot(1),
            )

            second = journal.record_transport_failure(
                binding,
                normalized_request=payload,
                logic_owner="planner",
                provider_error_code=ErrorCode.REASONER_UNAVAILABLE.value,
                provider_operations_observed=1,
                effect_proof=_proof(binding.request_id, call_number=2),
                provider_ledger_before=_ledger_snapshot(1),
            )
            self.assertNotEqual(second.retry_id, first.retry_id)
            successor_index = journal_root / "TRANSPORT_RETRY_INDEX" / f"{second.retry_id}.json"
            successor_index.unlink()
            recovered = PiSceneRequestJournal(
                journal_root,
                protected_retry_root=protected_root,
            ).authorize_transport_retry(first.retry_id)
            self.assertEqual(recovered.superseding_failure, second)
            self.assertTrue(successor_index.is_file())

            journal.authorize_transport_retry(second.retry_id)
            journal.mark_transport_owner_rotated(second.retry_id)
            journal.bind_transport_fresh_thread(second.retry_id, f"{3:064x}")
            journal.mark_transport_dispatch_started(
                second.retry_id,
                _ledger_snapshot(2),
            )
            response = {"cera": {"status": "accepted"}}
            journal.bind_progress(binding, _progress(binding))
            response = journal.complete(binding, response)
            self.assertEqual(
                response["cera"]["transport_retry_summary"],
                {
                    "schema_version": "cera.pi_scene.transport_retry_summary.v1",
                    "completed_after_manual_transport_retry": True,
                    "manual_retry_count": 2,
                    "prior_failed_provider_operations": 2,
                    "prior_failed_operations_by_owner": {"planner": 2},
                },
            )
            terminal = PiSceneRequestJournal(
                journal_root,
                protected_retry_root=protected_root,
            ).authorize_transport_retry(second.retry_id)
            self.assertEqual(terminal.replayed_terminal_response, response)

    def test_old_v1_pending_entry_has_no_retry_authority(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            journal = PiSceneRequestJournal(
                root / "journal",
                protected_retry_root=root / "protected",
            )
            binding = _binding()
            journal.begin(binding)
            self.assertIsNone(journal.active_transport_failure(binding))
            with self.assertRaises(RequestReplayPendingError):
                journal.begin(binding)

    def test_dispatch_started_crash_rearms_only_with_unchanged_provider_ledger(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            journal_root = root / "journal"
            protected_root = root / "protected"
            journal = PiSceneRequestJournal(
                journal_root,
                protected_retry_root=protected_root,
            )
            binding = _binding()
            journal.begin(binding)
            receipt = journal.record_transport_failure(
                binding,
                normalized_request=_payload(binding.session_id),
                logic_owner="planner",
                provider_error_code=ErrorCode.REASONER_UNAVAILABLE.value,
                provider_operations_observed=1,
                effect_proof=_proof(binding.request_id),
                provider_ledger_before=_ledger_snapshot(0),
            )
            journal.authorize_transport_retry(receipt.retry_id)
            journal.mark_transport_owner_rotated(receipt.retry_id)
            journal.bind_transport_fresh_thread(receipt.retry_id, "b" * 64)
            baseline = _ledger_snapshot(1)
            journal.mark_transport_dispatch_started(receipt.retry_id, baseline)

            restarted = PiSceneRequestJournal(
                journal_root,
                protected_retry_root=protected_root,
            )
            restarted.rearm_transport_retry_after_zero_dispatch(
                receipt.retry_id,
                baseline,
            )
            recovered = restarted.prepare_transport_retry(receipt.retry_id)
            self.assertEqual(recovered.action_phase, "owner_rotated")
            self.assertEqual(recovered.request_entry_status, "pending")

    def test_provider_failure_proof_rejects_mixed_call_identity(self) -> None:
        before = _ledger_snapshot(0)
        valid = _ledger_snapshot(1)
        malformed_events = [dict(value) for value in valid.events]
        malformed_events[1]["operation"] = "different-operation"
        malformed = PiSceneProviderLedgerSnapshotV1(
            schema_version=PiSceneProviderLedgerSnapshotV1.SCHEMA_VERSION,
            dispatched_call_count=1,
            events=tuple(malformed_events),
        )
        with self.assertRaises(ContractValidationError):
            build_provider_failure_evidence(
                before=before,
                after=malformed,
                provider_operation_submitted=True,
                provider_failure_detail_sha256=canonical_sha256({"safe": "failure"}),
            )

    def test_protected_failure_bundle_rebuilds_safe_ledger_and_index(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            journal_root = root / "journal"
            protected_root = root / "protected"
            journal = PiSceneRequestJournal(
                journal_root,
                protected_retry_root=protected_root,
            )
            binding = _binding()
            journal.begin(binding)
            receipt = journal.record_transport_failure(
                binding,
                normalized_request=_payload(binding.session_id),
                logic_owner="planner",
                provider_error_code=ErrorCode.REASONER_UNAVAILABLE.value,
                provider_operations_observed=1,
                effect_proof=_proof(binding.request_id),
                provider_ledger_before=_ledger_snapshot(0),
            )
            journal.entry_path(binding).with_suffix(".transport.json").unlink()
            (journal_root / "TRANSPORT_RETRY_INDEX" / f"{receipt.retry_id}.json").unlink()

            restarted = PiSceneRequestJournal(
                journal_root,
                protected_retry_root=protected_root,
            )
            recovered = restarted.prepare_transport_retry(receipt.retry_id)
            self.assertEqual(recovered.receipt, receipt)
            self.assertTrue(journal.entry_path(binding).with_suffix(".transport.json").is_file())
            self.assertTrue(
                (journal_root / "TRANSPORT_RETRY_INDEX" / f"{receipt.retry_id}.json").is_file()
            )


class PiSceneTransportRetryAdapterTests(unittest.TestCase):
    def test_staged_zero_event_crash_rearms_exact_request_without_double_call(self) -> None:
        short_root = Path("D:/Cera/tmp")
        short_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=short_root) as temporary:
            root = Path(temporary)
            coordinator, _, planner = _runtime(
                root,
                _SemanticValidator(SemanticVerdict.PASS),
            )
            planner_calls = 0
            original_plan = planner.plan

            def counted_plan(request):
                nonlocal planner_calls
                planner_calls += 1
                return original_plan(request)

            planner.plan = counted_plan
            journal = PiSceneRequestJournal(
                root / "journal",
                protected_retry_root=root / "protected",
            )
            adapter = PiSceneHttpAdapter(
                coordinator=coordinator,
                session_id="chat-alpha",
                context_provider=lambda *_args: turn(),
                request_journal=journal,
                transport_provider_ledger_snapshot=lambda: _ledger_snapshot(0),
                transport_retry_active_thread_snapshot=lambda *_args: None,
            )
            payload = _payload("chat-alpha")
            request, retry_turn, binding = adapter._prepare_bound_request(payload)
            journal.begin(binding)
            journal.stage_transport_dispatch(
                binding,
                normalized_request=payload,
                logic_owner="planner",
                resolved_route=request.route.value,
                turn_context_sha256=canonical_sha256(retry_turn),
                effect_before=adapter._transport_effect_snapshot(retry_turn),
                provider_ledger_before=_ledger_snapshot(0),
                planner_thread_sha256=None,
            )
            response = adapter.complete(payload)
            self.assertEqual(response["cera"]["status"], "accepted")
            self.assertEqual(planner_calls, 1)
            self.assertEqual(tuple(root.rglob("DISPATCH_AUTHORITY/*.json")), ())
            self.assertEqual(journal.inspect(binding)["status"], "terminal")

    def test_restart_recovers_crash_after_zero_call_rearm_publication(self) -> None:
        class CrashAfterRearmJournal(PiSceneRequestJournal):
            crash_once = True

            def _resolve_existing(self, path, binding):
                entry = self._read_entry(path, expected=binding)
                if entry["status"] == "rearmed" and self.crash_once:
                    self.crash_once = False
                    raise RuntimeError("injected crash after zero-call rearm")
                return super()._resolve_existing(path, binding)

        short_root = Path("D:/Cera/tmp")
        short_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=short_root) as temporary:
            root = Path(temporary)
            coordinator, _, planner = _runtime(
                root,
                _SemanticValidator(SemanticVerdict.PASS),
            )
            planner_calls = 0
            original_plan = planner.plan

            def counted_plan(request):
                nonlocal planner_calls
                planner_calls += 1
                return original_plan(request)

            planner.plan = counted_plan
            journal_root = root / "journal"
            protected_root = root / "protected"
            crashing_journal = CrashAfterRearmJournal(
                journal_root,
                protected_retry_root=protected_root,
            )
            crashing = PiSceneHttpAdapter(
                coordinator=coordinator,
                session_id="chat-alpha",
                context_provider=lambda *_args: turn(),
                request_journal=crashing_journal,
                transport_provider_ledger_snapshot=lambda: _ledger_snapshot(0),
                transport_retry_active_thread_snapshot=lambda *_args: None,
            )
            payload = _payload("chat-alpha")
            request, retry_turn, binding = crashing._prepare_bound_request(payload)
            crashing_journal.begin(binding)
            crashing_journal.stage_transport_dispatch(
                binding,
                normalized_request=payload,
                logic_owner="planner",
                resolved_route=request.route.value,
                turn_context_sha256=canonical_sha256(retry_turn),
                effect_before=crashing._transport_effect_snapshot(retry_turn),
                provider_ledger_before=_ledger_snapshot(0),
                planner_thread_sha256=None,
            )
            with self.assertRaisesRegex(RuntimeError, "zero-call rearm"):
                crashing.complete(payload)
            self.assertEqual(planner_calls, 0)
            self.assertEqual(crashing_journal.inspect(binding)["status"], "rearmed")

            restarted = PiSceneHttpAdapter(
                coordinator=coordinator,
                session_id="chat-alpha",
                context_provider=lambda *_args: turn(),
                request_journal=PiSceneRequestJournal(
                    journal_root,
                    protected_retry_root=protected_root,
                ),
                transport_provider_ledger_snapshot=lambda: _ledger_snapshot(0),
                transport_retry_active_thread_snapshot=lambda *_args: None,
            )
            response = restarted.complete(payload)
            self.assertEqual(response["cera"]["status"], "accepted")
            self.assertEqual(planner_calls, 1)
            self.assertEqual(tuple(root.rglob("DISPATCH_AUTHORITY/*.json")), ())

    def test_staged_failure_recovery_selects_exact_thread_after_unrelated_call(self) -> None:
        short_root = Path("D:/Cera/tmp")
        short_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=short_root) as temporary:
            root = Path(temporary)
            coordinator, _, planner = _runtime(
                root,
                _SemanticValidator(SemanticVerdict.PASS),
            )
            planner_calls = 0
            original_plan = planner.plan

            def counted_plan(request):
                nonlocal planner_calls
                planner_calls += 1
                return original_plan(request)

            planner.plan = counted_plan
            current_ledger = {"value": _ledger_snapshot(0)}
            journal = PiSceneRequestJournal(
                root / "journal",
                protected_retry_root=root / "protected",
            )
            adapter = PiSceneHttpAdapter(
                coordinator=coordinator,
                session_id="chat-alpha",
                context_provider=lambda _route, source, _messages: turn(source=source),
                request_journal=journal,
                transport_provider_ledger_snapshot=lambda: current_ledger["value"],
                transport_retry_active_thread_snapshot=lambda *_args: "a" * 64,
            )
            payload = _payload("chat-alpha")
            request, retry_turn, binding = adapter._prepare_bound_request(payload)
            journal.begin(binding)
            journal.stage_transport_dispatch(
                binding,
                normalized_request=payload,
                logic_owner="planner",
                resolved_route=request.route.value,
                turn_context_sha256=canonical_sha256(retry_turn),
                effect_before=adapter._transport_effect_snapshot(retry_turn),
                provider_ledger_before=current_ledger["value"],
                planner_thread_sha256="a" * 64,
            )
            unrelated = _append_ledger_call(
                current_ledger["value"],
                call_number=1,
                thread_sha256="c" * 64,
            )
            current_ledger["value"] = _append_ledger_call(
                unrelated,
                call_number=2,
                thread_sha256="a" * 64,
            )
            with self.assertRaises(PiSceneManualTransportRetryError) as recovered:
                adapter.complete(payload)
            self.assertEqual(planner_calls, 0)
            authorization = journal.prepare_transport_retry(recovered.exception.receipt.retry_id)
            evidence = authorization.effect_proof.provider_failure_evidence
            self.assertEqual(evidence.stored_thread_sha256, "a" * 64)
            self.assertEqual(evidence.before_event_count, 3)
            self.assertEqual(evidence.after_event_count, 6)

    def test_replan_cannot_interleave_with_global_provider_dispatch_lease(self) -> None:
        short_root = Path("D:/Cera/tmp")
        short_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=short_root) as temporary:
            root = Path(temporary)
            coordinator, _, planner = _runtime(
                root,
                _SemanticValidator(SemanticVerdict.REJECT),
            )
            plan_calls = 0
            original_plan = planner.plan

            def counted_plan(request):
                nonlocal plan_calls
                plan_calls += 1
                return original_plan(request)

            planner.plan = counted_plan
            journal = PiSceneRequestJournal(
                root / "journal",
                protected_retry_root=root / "protected",
            )
            adapter = PiSceneHttpAdapter(
                coordinator=coordinator,
                session_id="chat-alpha",
                context_provider=lambda *_args: turn(),
                request_journal=journal,
            )
            response = adapter.complete(_payload("chat-alpha"))
            review_id = response["cera"]["provisional_review_id"]
            self.assertIsInstance(review_id, str)
            self.assertEqual(plan_calls, 1)
            lease_held = Event()
            release_lease = Event()

            def hold_provider_lease() -> None:
                with journal.provider_dispatch_claim():
                    lease_held.set()
                    release_lease.wait(timeout=10)

            worker = Thread(target=hold_provider_lease)
            worker.start()
            self.assertTrue(lease_held.wait(timeout=10))
            try:
                with self.assertRaises(RequestReplayPendingError):
                    adapter.decide(
                        review_id,
                        {"action": "replan", "feedback": "Try another logic path."},
                    )
            finally:
                release_lease.set()
                worker.join(timeout=10)
            self.assertFalse(worker.is_alive())
            self.assertEqual(plan_calls, 1)

    def test_same_branch_staged_dispatch_blocks_replan_before_any_sol_call(self) -> None:
        short_root = Path("D:/Cera/tmp")
        short_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=short_root) as temporary:
            root = Path(temporary)
            coordinator, _, planner = _runtime(
                root,
                _SemanticValidator(SemanticVerdict.REJECT),
            )
            plan_calls = 0
            original_plan = planner.plan

            def counted_plan(request):
                nonlocal plan_calls
                plan_calls += 1
                return original_plan(request)

            planner.plan = counted_plan
            journal = PiSceneRequestJournal(
                root / "journal",
                protected_retry_root=root / "protected",
            )
            adapter = PiSceneHttpAdapter(
                coordinator=coordinator,
                session_id="chat-alpha",
                context_provider=lambda _route, source, _messages: turn(source=source),
                request_journal=journal,
                transport_provider_ledger_snapshot=lambda: _ledger_snapshot(0),
                transport_retry_active_thread_snapshot=lambda *_args: "a" * 64,
            )
            response = adapter.complete(_payload("chat-alpha"))
            review_id = response["cera"]["provisional_review_id"]
            self.assertEqual(plan_calls, 1)
            staged_payload = _payload("chat-alpha")
            staged_payload["messages"] = [{"role": "user", "content": "A staged later request."}]
            request, retry_turn, binding = adapter._prepare_bound_request(staged_payload)
            journal.begin(binding)
            journal.stage_transport_dispatch(
                binding,
                normalized_request=staged_payload,
                logic_owner="planner",
                resolved_route=request.route.value,
                turn_context_sha256=canonical_sha256(retry_turn),
                effect_before=adapter._transport_effect_snapshot(retry_turn),
                provider_ledger_before=_ledger_snapshot(0),
                planner_thread_sha256="a" * 64,
            )
            with self.assertRaises(RequestReplayPendingError) as blocked:
                adapter.decide(
                    review_id,
                    {"action": "replan", "feedback": "Try another logic path."},
                )
            self.assertEqual(blocked.exception.request_id, binding.request_id)
            self.assertEqual(plan_calls, 1)

    def test_raw_retry_request_exists_only_in_protected_authority_and_is_redacted(
        self,
    ) -> None:
        short_root = Path("D:/Cera/tmp")
        short_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=short_root) as temporary:
            root = Path(temporary)
            coordinator, _, _ = _runtime(
                root,
                _SemanticValidator(SemanticVerdict.PASS),
            )
            journal = PiSceneRequestJournal(
                root / "journal",
                protected_retry_root=root / "protected",
            )
            adapter = PiSceneHttpAdapter(
                coordinator=coordinator,
                session_id="chat-alpha",
                context_provider=lambda _route, source, _messages: turn(source=source),
                request_journal=journal,
                transport_provider_ledger_snapshot=lambda: _ledger_snapshot(0),
                transport_retry_active_thread_snapshot=lambda *_args: "a" * 64,
            )
            sentinel = "CERA-RAW-RETRY-SENTINEL-DO-NOT-PROJECT"
            payload = _payload("chat-alpha")
            payload["messages"] = [{"role": "user", "content": sentinel}]
            request, retry_turn, binding = adapter._prepare_bound_request(payload)
            journal.begin(binding)
            effect = adapter._transport_effect_snapshot(retry_turn)
            before = _ledger_snapshot(0)
            journal.stage_transport_dispatch(
                binding,
                normalized_request=payload,
                logic_owner="planner",
                resolved_route=request.route.value,
                turn_context_sha256=canonical_sha256(retry_turn),
                effect_before=effect,
                provider_ledger_before=before,
                planner_thread_sha256="a" * 64,
            )

            def containing_paths() -> tuple[Path, ...]:
                found: list[Path] = []
                for path in root.rglob("*"):
                    if not path.is_file():
                        continue
                    try:
                        if sentinel in path.read_text(encoding="utf-8"):
                            found.append(path)
                    except (OSError, UnicodeDecodeError):
                        continue
                return tuple(found)

            authority = root / "protected" / "DISPATCH_AUTHORITY" / (f"{binding.request_id}.json")
            self.assertEqual(containing_paths(), (authority,))
            after = _append_ledger_call(
                before,
                call_number=1,
                thread_sha256="a" * 64,
            )
            evidence = build_provider_failure_evidence(
                before=before,
                after=after,
                provider_operation_submitted=True,
                provider_failure_detail_sha256=provider_failure_detail_sha256(
                    None,
                    call_events=after.events,
                ),
            )
            proof = PiSceneZeroEffectProofV1(
                schema_version=PiSceneZeroEffectProofV1.SCHEMA_VERSION,
                request_id=binding.request_id,
                logic_owner="planner",
                resolved_route=request.route.value,
                turn_context_sha256=canonical_sha256(retry_turn),
                before_snapshot=effect,
                after_snapshot=effect,
                provider_failure_evidence=evidence,
                candidate_effect_absent=True,
                review_effect_absent=True,
                accepted_effect_absent=True,
                recording_effect_absent=True,
                branch_head_effect_absent=True,
            )
            receipt = journal.record_transport_failure(
                binding,
                normalized_request=payload,
                logic_owner="planner",
                provider_error_code=ErrorCode.REASONER_UNAVAILABLE.value,
                provider_operations_observed=1,
                effect_proof=proof,
                provider_ledger_before=before,
            )
            self.assertEqual(containing_paths(), (authority,))
            journal.block_transport_retry(
                receipt.retry_id,
                "route_or_context_changed",
            )
            self.assertEqual(containing_paths(), ())
            self.assertTrue(authority.is_file())

    def test_protected_staged_authority_blocks_different_same_branch_prompt(self) -> None:
        short_root = Path("D:/Cera/tmp")
        short_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=short_root) as temporary:
            root = Path(temporary)
            coordinator, _, planner = _runtime(
                root,
                _SemanticValidator(SemanticVerdict.PASS),
            )
            plan_calls = 0
            original_plan = planner.plan

            def counted_plan(request):
                nonlocal plan_calls
                plan_calls += 1
                return original_plan(request)

            planner.plan = counted_plan
            journal = PiSceneRequestJournal(
                root / "journal",
                protected_retry_root=root / "protected",
            )
            adapter = PiSceneHttpAdapter(
                coordinator=coordinator,
                session_id="chat-alpha",
                context_provider=lambda _route, source, _messages: turn(source=source),
                request_journal=journal,
                transport_provider_ledger_snapshot=lambda: _ledger_snapshot(0),
                transport_retry_active_thread_snapshot=lambda *_args: "a" * 64,
            )
            first_payload = _payload("chat-alpha")
            first_payload["messages"] = [{"role": "user", "content": "Interrupted first prompt."}]
            first_request, first_turn, first_binding = adapter._prepare_bound_request(first_payload)
            journal.begin(first_binding)
            journal.stage_transport_dispatch(
                first_binding,
                normalized_request=first_payload,
                logic_owner="planner",
                resolved_route=first_request.route.value,
                turn_context_sha256=canonical_sha256(first_turn),
                effect_before=adapter._transport_effect_snapshot(first_turn),
                provider_ledger_before=_ledger_snapshot(0),
                planner_thread_sha256="a" * 64,
            )
            self.assertEqual(tuple((root / "journal").rglob("*.transport.json")), ())

            second_payload = _payload("chat-alpha")
            second_payload["messages"] = [
                {"role": "user", "content": "Different prompt on same branch."}
            ]
            with self.assertRaises(RequestReplayPendingError) as blocked:
                adapter.complete(second_payload)
            self.assertEqual(blocked.exception.request_id, first_binding.request_id)
            self.assertEqual(plan_calls, 0)
            self.assertEqual(
                journal.active_transport_dispatch_for_scope(
                    session_id=first_binding.session_id,
                    world_id=first_binding.world_id,
                    branch_id=first_binding.branch_id,
                ),
                first_binding,
            )

    def test_fresh_thread_persisted_before_any_sol_event_rearms_without_rearchive(
        self,
    ) -> None:
        short_root = Path("D:/Cera/tmp")
        short_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=short_root) as temporary:
            root = Path(temporary)
            coordinator, _, planner = _runtime(
                root,
                _SemanticValidator(SemanticVerdict.PASS),
            )
            original_plan = planner.plan
            ledger = ContinuousProviderCallLedger((root / "sol.jsonl").resolve())
            rotation = _FakeThreadRotation()
            calls = 0

            def fail_once(request):
                nonlocal calls
                calls += 1

                def dispatch():
                    if calls == 1:
                        raise ProviderTransportError(
                            ErrorCode.REASONER_UNAVAILABLE,
                            "initial A failure",
                            external_provider_calls_observed=1,
                        )
                    return original_plan(request)

                return ledger.execute(
                    owner="planner",
                    operation=f"plan_{calls}",
                    route="test",
                    model="gpt-5.6-sol",
                    effort="xhigh",
                    dispatch=dispatch,
                    finalize=lambda value: value,
                    stored_thread_sha256=rotation.active,
                )

            planner.plan = fail_once
            journal_root = root / "journal"
            protected_root = root / "protected"
            journal = PiSceneRequestJournal(
                journal_root,
                protected_retry_root=protected_root,
            )
            adapter = PiSceneHttpAdapter(
                coordinator=coordinator,
                session_id="chat-alpha",
                context_provider=lambda *_args: turn(),
                request_journal=journal,
                transport_provider_ledger_snapshot=lambda: _live_ledger_snapshot(ledger),
                **rotation.adapter_kwargs(),
            )
            with self.assertRaises(PiSceneManualTransportRetryError) as first:
                adapter.complete(_payload("chat-alpha"))
            retry_id = first.exception.receipt.retry_id
            authorization = journal.authorize_transport_retry(retry_id)
            rotation.reinitialize(
                authorization.binding,
                turn(),
                "planner",
                authorization.effect_proof.provider_failure_evidence.stored_thread_sha256,
            )
            journal.mark_transport_owner_rotated(retry_id)
            fresh_b = rotation.initialize(authorization.binding, turn(), "planner")
            journal.bind_transport_fresh_thread(retry_id, fresh_b)
            journal.mark_transport_dispatch_started(
                retry_id,
                _live_ledger_snapshot(ledger),
            )

            restarted = PiSceneHttpAdapter(
                coordinator=coordinator,
                session_id="chat-alpha",
                context_provider=lambda *_args: turn(),
                request_journal=PiSceneRequestJournal(
                    journal_root,
                    protected_retry_root=protected_root,
                ),
                transport_provider_ledger_snapshot=lambda: _live_ledger_snapshot(ledger),
                **rotation.adapter_kwargs(),
            )
            status = restarted.transport_retry_status(retry_id)
            self.assertEqual(status["state"], "eligible")
            self.assertEqual(calls, 1)
            response = restarted.retry_transport(retry_id)
            self.assertIsInstance(response, dict)
            self.assertEqual(calls, 2)
            self.assertEqual(rotation.rotations, ["a" * 64])

    def test_restart_attributes_failed_retry_to_fresh_thread_with_later_unrelated_call(
        self,
    ) -> None:
        short_root = Path("D:/Cera/tmp")
        short_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=short_root) as temporary:
            root = Path(temporary)
            coordinator, _, planner = _runtime(
                root,
                _SemanticValidator(SemanticVerdict.PASS),
            )
            ledger = ContinuousProviderCallLedger((root / "sol.jsonl").resolve())
            active = {"thread": "a" * 64}

            def fail_initial(_request):
                def dispatch():
                    raise ProviderTransportError(
                        ErrorCode.REASONER_UNAVAILABLE,
                        "initial A failure",
                        external_provider_calls_observed=1,
                    )

                return ledger.execute(
                    owner="planner",
                    operation="plan_a",
                    route="test",
                    model="gpt-5.6-sol",
                    effort="xhigh",
                    dispatch=dispatch,
                    finalize=lambda value: value,
                    stored_thread_sha256=active["thread"],
                )

            planner.plan = fail_initial
            adapter = PiSceneHttpAdapter(
                coordinator=coordinator,
                session_id="chat-alpha",
                context_provider=lambda *_args: turn(),
                transport_provider_ledger_snapshot=lambda: _live_ledger_snapshot(ledger),
                transport_retry_active_thread_snapshot=lambda *_args: active["thread"],
            )
            with self.assertRaises(PiSceneManualTransportRetryError) as first:
                adapter.complete(_payload("chat-alpha"))
            retry_id = first.exception.receipt.retry_id
            journal = adapter._durable_request_journal()
            journal.authorize_transport_retry(retry_id)
            journal.mark_transport_owner_rotated(retry_id)
            active["thread"] = "b" * 64
            journal.bind_transport_fresh_thread(retry_id, active["thread"])
            journal.mark_transport_dispatch_started(
                retry_id,
                _live_ledger_snapshot(ledger),
            )

            def append_failure(operation: str, thread_sha256: str) -> None:
                with self.assertRaises(ProviderTransportError):
                    ledger.execute(
                        owner="planner",
                        operation=operation,
                        route="test",
                        model="gpt-5.6-sol",
                        effort="xhigh",
                        dispatch=lambda: (_ for _ in ()).throw(
                            ProviderTransportError(
                                ErrorCode.REASONER_UNAVAILABLE,
                                operation,
                                external_provider_calls_observed=1,
                            )
                        ),
                        finalize=lambda value: value,
                        stored_thread_sha256=thread_sha256,
                    )

            append_failure("plan_b", "b" * 64)
            append_failure("unrelated_plan_c", "c" * 64)
            before_get_count = ledger.dispatched_call_count
            status = adapter.transport_retry_status(retry_id)
            self.assertEqual(status["state"], "superseded")
            self.assertEqual(ledger.dispatched_call_count, before_get_count)
            successor = journal.prepare_transport_retry(status["superseded_by_retry_id"])
            self.assertEqual(
                successor.effect_proof.provider_failure_evidence.stored_thread_sha256,
                "b" * 64,
            )
            self.assertEqual(
                successor.effect_proof.provider_failure_evidence.after_event_count,
                before_get_count * 3 - 3,
            )

    def test_restart_never_misattributed_unrelated_failure_when_fresh_thread_unused(
        self,
    ) -> None:
        short_root = Path("D:/Cera/tmp")
        short_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=short_root) as temporary:
            root = Path(temporary)
            coordinator, _, planner = _runtime(
                root,
                _SemanticValidator(SemanticVerdict.PASS),
            )
            ledger = ContinuousProviderCallLedger((root / "sol.jsonl").resolve())
            active = {"thread": "a" * 64}

            def fail_initial(_request):
                return ledger.execute(
                    owner="planner",
                    operation="plan_a",
                    route="test",
                    model="gpt-5.6-sol",
                    effort="xhigh",
                    dispatch=lambda: (_ for _ in ()).throw(
                        ProviderTransportError(
                            ErrorCode.REASONER_UNAVAILABLE,
                            "initial A failure",
                            external_provider_calls_observed=1,
                        )
                    ),
                    finalize=lambda value: value,
                    stored_thread_sha256=active["thread"],
                )

            planner.plan = fail_initial
            adapter = PiSceneHttpAdapter(
                coordinator=coordinator,
                session_id="chat-alpha",
                context_provider=lambda *_args: turn(),
                transport_provider_ledger_snapshot=lambda: _live_ledger_snapshot(ledger),
                transport_retry_active_thread_snapshot=lambda *_args: active["thread"],
            )
            with self.assertRaises(PiSceneManualTransportRetryError) as first:
                adapter.complete(_payload("chat-alpha"))
            retry_id = first.exception.receipt.retry_id
            journal = adapter._durable_request_journal()
            journal.authorize_transport_retry(retry_id)
            journal.mark_transport_owner_rotated(retry_id)
            active["thread"] = "b" * 64
            journal.bind_transport_fresh_thread(retry_id, active["thread"])
            journal.mark_transport_dispatch_started(
                retry_id,
                _live_ledger_snapshot(ledger),
            )
            with self.assertRaises(ProviderTransportError):
                ledger.execute(
                    owner="planner",
                    operation="unrelated_plan_c",
                    route="test",
                    model="gpt-5.6-sol",
                    effort="xhigh",
                    dispatch=lambda: (_ for _ in ()).throw(
                        ProviderTransportError(
                            ErrorCode.REASONER_UNAVAILABLE,
                            "unrelated C failure",
                            external_provider_calls_observed=1,
                        )
                    ),
                    finalize=lambda value: value,
                    stored_thread_sha256="c" * 64,
                )
            before_get_count = ledger.dispatched_call_count
            status = adapter.transport_retry_status(retry_id)
            self.assertEqual(status["state"], "eligible")
            self.assertEqual(status["transport_retry"]["retry_id"], retry_id)
            self.assertEqual(ledger.dispatched_call_count, before_get_count)
            authorization = journal.prepare_transport_retry(retry_id)
            self.assertEqual(authorization.action_phase, "owner_rotated")
            self.assertEqual(authorization.fresh_thread_sha256, "b" * 64)

    def test_planner_timeout_exposes_one_manual_retry_and_replays_success(self) -> None:
        short_root = Path("D:/Cera/tmp")
        short_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=short_root) as temporary:
            root = Path(temporary)
            validator = _SemanticValidator(SemanticVerdict.PASS)
            coordinator, store, planner = _runtime(root, validator)
            original_plan = planner.plan
            sol_ledger = ContinuousProviderCallLedger((root / "sol.jsonl").resolve())
            calls = 0
            rotation = _FakeThreadRotation()

            def fail_once(request):
                nonlocal calls
                calls += 1

                def dispatch():
                    if calls == 1:
                        raise ProviderTransportError(
                            ErrorCode.REASONER_UNAVAILABLE,
                            "injected timeout",
                            safe_diagnostics=("transport:timeout",),
                            external_provider_calls_observed=1,
                        )
                    return original_plan(request)

                return sol_ledger.execute(
                    owner="planner",
                    operation=f"plan_{calls:04d}",
                    route="test",
                    model="gpt-5.6-sol",
                    effort="xhigh",
                    dispatch=dispatch,
                    finalize=lambda value: value,
                    stored_thread_sha256=rotation.active,
                )

            planner.plan = fail_once
            reinitialized: list[tuple[str, str, str, str]] = []
            original_reinitialize = rotation.reinitialize

            def record_reinitialize(binding, retry_turn, owner, thread) -> None:
                reinitialized.append(
                    (
                        binding.session_id,
                        retry_turn.world_id,
                        retry_turn.branch_id,
                        owner,
                    )
                )
                original_reinitialize(binding, retry_turn, owner, thread)

            adapter = PiSceneHttpAdapter(
                coordinator=coordinator,
                session_id="chat-alpha",
                context_provider=lambda *_args: turn(),
                transport_retry_reinitializer=record_reinitialize,
                transport_retry_fresh_thread_initializer=rotation.initialize,
                transport_retry_active_thread_snapshot=rotation.snapshot,
                transport_provider_ledger_snapshot=lambda: _live_ledger_snapshot(sol_ledger),
            )
            request = _payload("chat-alpha")
            with self.assertRaises(PiSceneManualTransportRetryError) as raised:
                adapter.complete(request)
            receipt = raised.exception.receipt
            self.assertEqual(receipt.logic_owner, "planner")
            self.assertEqual(receipt.provider_operations_observed, 1)
            retry_custody = tuple(root.rglob("DISPATCH_AUTHORITY/*.json"))
            self.assertEqual(len(retry_custody), 1)
            self.assertIn("protected_transport_retry", retry_custody[0].parts)
            self.assertFalse(retry_custody[0].is_relative_to(store.root))
            self.assertIsNone(
                coordinator.unresolved_review(
                    world_id=turn().world_id,
                    branch_id=turn().branch_id,
                )
            )
            self.assertIsNone(
                store.load_head(
                    world_id=turn().world_id,
                    branch_id=turn().branch_id,
                ).receipt
            )

            # A lost HTTP failure response is reconstructed from its stable ID.
            with self.assertRaises(PiSceneManualTransportRetryError) as replayed:
                adapter.complete(request)
            self.assertEqual(replayed.exception.receipt, receipt)
            self.assertEqual(calls, 1)

            response = adapter.retry_transport(receipt.retry_id)
            self.assertTrue(response["cera"]["story_state_committed"])
            self.assertEqual(
                response["cera"]["transport_retry_summary"]["prior_failed_provider_operations"],
                1,
            )
            self.assertEqual(
                response["cera"]["provider_operations"]["planner"],
                1,
            )
            self.assertEqual(calls, 2)
            self.assertEqual(len(reinitialized), 1)
            self.assertEqual(reinitialized[0][-1], "planner")
            self.assertEqual(adapter.retry_transport(receipt.retry_id), response)
            self.assertEqual(calls, 2)

    def test_writer_transport_failure_does_not_expose_retry_without_candidate_proof(
        self,
    ) -> None:
        short_root = Path("D:/Cera/tmp")
        short_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=short_root) as temporary:
            root = Path(temporary)
            coordinator, _, _ = _runtime(
                root,
                _SemanticValidator(SemanticVerdict.PASS),
            )

            def writer_failure(_request):
                raise ProviderTransportError(
                    ErrorCode.COMPOSER_UNAVAILABLE,
                    "injected writer timeout",
                    external_provider_calls_observed=1,
                )

            coordinator.pi.invoke = writer_failure
            adapter = PiSceneHttpAdapter(
                coordinator=coordinator,
                session_id="chat-alpha",
                context_provider=lambda *_args: turn(),
                transport_provider_ledger_snapshot=lambda: _ledger_snapshot(0),
            )
            with self.assertRaisesRegex(Exception, "writer provider transport failed") as raised:
                adapter.complete(_payload("chat-alpha"))
            self.assertNotIsInstance(raised.exception, PiSceneManualTransportRetryError)
            self.assertEqual(tuple(root.rglob("DISPATCH_AUTHORITY/*.json")), ())

    def test_planner_process_start_failure_is_retryable_but_not_submitted(self) -> None:
        short_root = Path("D:/Cera/tmp")
        short_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=short_root) as temporary:
            root = Path(temporary)
            coordinator, _, planner = _runtime(
                root,
                _SemanticValidator(SemanticVerdict.PASS),
            )
            sol_ledger = ContinuousProviderCallLedger((root / "sol.jsonl").resolve())

            def fail_before_transport(_request):
                def dispatch():
                    raise ProviderTransportError(
                        ErrorCode.REASONER_UNAVAILABLE,
                        "injected local Pi process-start failure",
                        external_provider_calls_observed=0,
                    )

                return sol_ledger.execute(
                    owner="planner",
                    operation="plan_pretransport",
                    route="test",
                    model="gpt-5.6-sol",
                    effort="xhigh",
                    dispatch=dispatch,
                    finalize=lambda value: value,
                    stored_thread_sha256="a" * 64,
                )

            planner.plan = fail_before_transport
            adapter = PiSceneHttpAdapter(
                coordinator=coordinator,
                session_id="chat-alpha",
                context_provider=lambda *_args: turn(),
                transport_provider_ledger_snapshot=lambda: _live_ledger_snapshot(sol_ledger),
            )
            with self.assertRaises(PiSceneManualTransportRetryError) as raised:
                adapter.complete(_payload("chat-alpha"))
            self.assertEqual(raised.exception.receipt.provider_operations_observed, 0)
            self.assertEqual(sol_ledger.dispatched_call_count, 0)
            status = adapter.transport_retry_status(raised.exception.receipt.retry_id)
            self.assertEqual(status["state"], "eligible")

    def test_next_prompt_failure_binds_after_prior_review_auto_resolution(self) -> None:
        short_root = Path("D:/Cera/tmp")
        short_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=short_root) as temporary:
            root = Path(temporary)
            coordinator, _, planner = _runtime(
                root,
                _SemanticValidator(SemanticVerdict.REJECT),
            )
            sol_ledger = ContinuousProviderCallLedger((root / "sol.jsonl").resolve())
            adapter = PiSceneHttpAdapter(
                coordinator=coordinator,
                session_id="chat-alpha",
                context_provider=lambda _route, source, _messages: turn(source=source),
                transport_provider_ledger_snapshot=lambda: _live_ledger_snapshot(sol_ledger),
            )
            first_payload = _payload("chat-alpha")
            first_payload["messages"] = [{"role": "user", "content": "First unresolved prompt."}]
            first_response = adapter.complete(first_payload)
            first_review_id = first_response["cera"]["provisional_review_id"]
            self.assertIsNotNone(first_review_id)

            def fail_next_plan(_request):
                def dispatch():
                    raise ProviderTransportError(
                        ErrorCode.REASONER_UNAVAILABLE,
                        "injected next-turn Planner timeout",
                        external_provider_calls_observed=1,
                    )

                return sol_ledger.execute(
                    owner="planner",
                    operation="plan_next_turn",
                    route="test",
                    model="gpt-5.6-sol",
                    effort="xhigh",
                    dispatch=dispatch,
                    finalize=lambda value: value,
                    stored_thread_sha256="a" * 64,
                )

            planner.plan = fail_next_plan
            next_payload = _payload("chat-alpha")
            next_payload["messages"] = [{"role": "user", "content": "A genuinely new prompt."}]
            with self.assertRaises(PiSceneManualTransportRetryError) as failure:
                adapter.complete(next_payload)
            status = adapter.transport_retry_status(failure.exception.receipt.retry_id)
            self.assertEqual(status["state"], "eligible")
            proof = (
                adapter._durable_request_journal()
                .prepare_transport_retry(failure.exception.receipt.retry_id)
                .effect_proof
            )
            self.assertEqual(proof.before_snapshot, proof.after_snapshot)
            self.assertIsNone(proof.after_snapshot.unresolved_review_sha256)
            self.assertEqual(
                str(coordinator.get_review(first_review_id).state),
                "declined",
            )

    def test_restart_promotes_staged_terminal_failure_without_redispatch(self) -> None:
        class CrashBeforeFailurePublicationJournal(PiSceneRequestJournal):
            crash_once = True

            def record_transport_failure(self, *args, **kwargs):
                if self.crash_once:
                    self.crash_once = False
                    raise RuntimeError("injected crash before failure publication")
                return super().record_transport_failure(*args, **kwargs)

        short_root = Path("D:/Cera/tmp")
        short_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=short_root) as temporary:
            root = Path(temporary)
            coordinator, _, planner = _runtime(
                root,
                _SemanticValidator(SemanticVerdict.PASS),
            )
            sol_ledger = ContinuousProviderCallLedger((root / "sol.jsonl").resolve())
            calls = 0

            def fail_plan(_request):
                nonlocal calls
                calls += 1

                def dispatch():
                    raise ProviderTransportError(
                        ErrorCode.REASONER_UNAVAILABLE,
                        "injected Planner timeout",
                        external_provider_calls_observed=1,
                    )

                return sol_ledger.execute(
                    owner="planner",
                    operation="plan_crash_publication",
                    route="test",
                    model="gpt-5.6-sol",
                    effort="xhigh",
                    dispatch=dispatch,
                    finalize=lambda value: value,
                    stored_thread_sha256="a" * 64,
                )

            planner.plan = fail_plan
            journal_root = root / "journal"
            protected_root = root / "protected"
            crashing = CrashBeforeFailurePublicationJournal(
                journal_root,
                protected_retry_root=protected_root,
            )
            first_adapter = PiSceneHttpAdapter(
                coordinator=coordinator,
                session_id="chat-alpha",
                context_provider=lambda *_args: turn(),
                request_journal=crashing,
                transport_provider_ledger_snapshot=lambda: _live_ledger_snapshot(sol_ledger),
                transport_retry_active_thread_snapshot=lambda *_args: "a" * 64,
            )
            payload = _payload("chat-alpha")
            with self.assertRaisesRegex(RuntimeError, "failure publication"):
                first_adapter.complete(payload)
            self.assertEqual(calls, 1)
            self.assertEqual(tuple(journal_root.rglob("*.transport.json")), ())
            self.assertEqual(
                len(tuple(protected_root.rglob("DISPATCH_AUTHORITY/*.json"))),
                1,
            )

            restarted = PiSceneHttpAdapter(
                coordinator=coordinator,
                session_id="chat-alpha",
                context_provider=lambda *_args: turn(),
                request_journal=PiSceneRequestJournal(
                    journal_root,
                    protected_retry_root=protected_root,
                ),
                transport_provider_ledger_snapshot=lambda: _live_ledger_snapshot(sol_ledger),
                transport_retry_active_thread_snapshot=lambda *_args: "a" * 64,
            )
            with self.assertRaises(PiSceneManualTransportRetryError) as recovered:
                restarted.complete(payload)
            self.assertEqual(calls, 1)
            self.assertEqual(
                recovered.exception.receipt.provider_operations_observed,
                1,
            )
            status = restarted.transport_retry_status(recovered.exception.receipt.retry_id)
            self.assertEqual(status["state"], "eligible")

    def test_authority_ahead_of_safe_projection_blocks_new_same_branch_dispatch(
        self,
    ) -> None:
        class CrashAfterAuthorityJournal(PiSceneRequestJournal):
            crash_once = True

            def _write_transport_authority(self, *args, **kwargs):
                value = super()._write_transport_authority(*args, **kwargs)
                if kwargs.get("phase") == "failure_eligible" and self.crash_once:
                    self.crash_once = False
                    raise RuntimeError("injected crash after protected authority")
                return value

        short_root = Path("D:/Cera/tmp")
        short_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=short_root) as temporary:
            root = Path(temporary)
            coordinator, _, planner = _runtime(
                root,
                _SemanticValidator(SemanticVerdict.PASS),
            )
            ledger = ContinuousProviderCallLedger((root / "sol.jsonl").resolve())
            calls = 0

            def fail_plan(_request):
                nonlocal calls
                calls += 1
                return ledger.execute(
                    owner="planner",
                    operation="plan_authority_ahead",
                    route="test",
                    model="gpt-5.6-sol",
                    effort="xhigh",
                    dispatch=lambda: (_ for _ in ()).throw(
                        ProviderTransportError(
                            ErrorCode.REASONER_UNAVAILABLE,
                            "authority ahead failure",
                            external_provider_calls_observed=1,
                        )
                    ),
                    finalize=lambda value: value,
                    stored_thread_sha256="a" * 64,
                )

            planner.plan = fail_plan
            journal_root = root / "journal"
            protected_root = root / "protected"
            crashing = PiSceneHttpAdapter(
                coordinator=coordinator,
                session_id="chat-alpha",
                context_provider=lambda _route, source, _messages: turn(source=source),
                request_journal=CrashAfterAuthorityJournal(
                    journal_root,
                    protected_retry_root=protected_root,
                ),
                transport_provider_ledger_snapshot=lambda: _live_ledger_snapshot(ledger),
                transport_retry_active_thread_snapshot=lambda *_args: "a" * 64,
            )
            first_payload = _payload("chat-alpha")
            first_payload["messages"] = [{"role": "user", "content": "Failed request."}]
            with self.assertRaisesRegex(RuntimeError, "protected authority"):
                crashing.complete(first_payload)
            self.assertEqual(calls, 1)
            self.assertEqual(tuple(journal_root.rglob("*.transport.json")), ())

            restarted = PiSceneHttpAdapter(
                coordinator=coordinator,
                session_id="chat-alpha",
                context_provider=lambda _route, source, _messages: turn(source=source),
                request_journal=PiSceneRequestJournal(
                    journal_root,
                    protected_retry_root=protected_root,
                ),
                transport_provider_ledger_snapshot=lambda: _live_ledger_snapshot(ledger),
                transport_retry_active_thread_snapshot=lambda *_args: "a" * 64,
            )
            second_payload = _payload("chat-alpha")
            second_payload["messages"] = [{"role": "user", "content": "Different request."}]
            with self.assertRaises(RequestReplayPendingError) as blocked:
                restarted.complete(second_payload)
            self.assertEqual(calls, 1)
            self.assertTrue(tuple(journal_root.rglob("*.transport.json")))
            retry_id = next((journal_root / "TRANSPORT_RETRY_INDEX").glob("retry-*.json")).stem
            self.assertEqual(
                restarted.transport_retry_status(retry_id)["state"],
                "eligible",
            )
            self.assertEqual(blocked.exception.request_id[:8], "request-")

    def test_success_does_not_duplicate_full_request_into_retry_custody(self) -> None:
        short_root = Path("D:/Cera/tmp")
        short_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=short_root) as temporary:
            root = Path(temporary)
            coordinator, _, _ = _runtime(
                root,
                _SemanticValidator(SemanticVerdict.PASS),
            )
            PiSceneHttpAdapter(
                coordinator=coordinator,
                session_id="chat-alpha",
                context_provider=lambda *_args: turn(),
                transport_provider_ledger_snapshot=lambda: _ledger_snapshot(0),
            ).complete(_payload("chat-alpha"))
            self.assertEqual(tuple(root.rglob("*.request.json")), ())
            self.assertEqual(tuple(root.rglob("DISPATCH_AUTHORITY/*.json")), ())

    def test_durable_effect_change_blocks_retry_custody(self) -> None:
        short_root = Path("D:/Cera/tmp")
        short_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=short_root) as temporary:
            root = Path(temporary)
            coordinator, _, planner = _runtime(
                root,
                _SemanticValidator(SemanticVerdict.PASS),
            )
            original_plan = planner.plan
            sol_ledger = ContinuousProviderCallLedger((root / "sol.jsonl").resolve())

            def planner_failure(_request):
                def dispatch():
                    planner.plan = original_plan
                    coordinator.start_ordinary(turn(source="Injected accepted effect."))
                    raise ProviderTransportError(
                        ErrorCode.REASONER_UNAVAILABLE,
                        "injected timeout after accepted-head mutation",
                        external_provider_calls_observed=1,
                    )

                return sol_ledger.execute(
                    owner="planner",
                    operation="plan_effect_mutation",
                    route="test",
                    model="gpt-5.6-sol",
                    effort="xhigh",
                    dispatch=dispatch,
                    finalize=lambda value: value,
                    stored_thread_sha256="a" * 64,
                )

            planner.plan = planner_failure
            adapter = PiSceneHttpAdapter(
                coordinator=coordinator,
                session_id="chat-alpha",
                context_provider=lambda *_args: turn(source="Second source."),
                transport_provider_ledger_snapshot=lambda: _live_ledger_snapshot(sol_ledger),
            )
            request = _payload("chat-alpha")
            request["messages"] = [{"role": "user", "content": "Second source."}]
            with self.assertRaises(Exception) as raised:
                adapter.complete(request)
            self.assertNotIsInstance(raised.exception, PiSceneManualTransportRetryError)
            self.assertEqual(tuple(root.rglob("*.request.json")), ())

    def test_retry_planner_success_then_writer_failure_is_terminally_blocked(
        self,
    ) -> None:
        short_root = Path("D:/Cera/tmp")
        short_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=short_root) as temporary:
            root = Path(temporary)
            coordinator, _, planner = _runtime(
                root,
                _SemanticValidator(SemanticVerdict.PASS),
            )
            original_plan = planner.plan
            sol_ledger = ContinuousProviderCallLedger((root / "sol.jsonl").resolve())
            calls = 0
            rotation = _FakeThreadRotation()
            original_writer = coordinator.pi.invoke

            def fail_first_plan(request):
                nonlocal calls
                if not rotation.active:
                    rotation.initialize()
                calls += 1

                def dispatch():
                    if calls == 1:
                        raise ProviderTransportError(
                            ErrorCode.REASONER_UNAVAILABLE,
                            "injected Planner timeout",
                            external_provider_calls_observed=1,
                        )
                    return original_plan(request)

                return sol_ledger.execute(
                    owner="planner",
                    operation=f"plan_{calls:04d}",
                    route="test",
                    model="gpt-5.6-sol",
                    effort="xhigh",
                    dispatch=dispatch,
                    finalize=lambda value: value,
                    stored_thread_sha256=rotation.active,
                )

            planner.plan = fail_first_plan
            adapter = PiSceneHttpAdapter(
                coordinator=coordinator,
                session_id="chat-alpha",
                context_provider=lambda _route, source, _messages: turn(source=source),
                **rotation.adapter_kwargs(),
                transport_provider_ledger_snapshot=lambda: _live_ledger_snapshot(sol_ledger),
            )
            payload = _payload("chat-alpha")
            with self.assertRaises(PiSceneManualTransportRetryError) as first:
                adapter.complete(payload)

            def fail_writer(_request):
                raise ProviderTransportError(
                    ErrorCode.COMPOSER_UNAVAILABLE,
                    "injected Writer timeout",
                    external_provider_calls_observed=1,
                )

            coordinator.pi.invoke = fail_writer
            with self.assertRaises(Exception) as second:
                adapter.retry_transport(first.exception.receipt.retry_id)
            self.assertNotIsInstance(second.exception, PiSceneManualTransportRetryError)
            status = adapter.transport_retry_status(first.exception.receipt.retry_id)
            self.assertEqual(status["state"], "blocked")
            self.assertEqual(status["blocked_reason_code"], "planner_result_unavailable")
            self.assertEqual(len(rotation.completed_abandonments), 1)
            self.assertNotEqual(rotation.completed_abandonments[0], rotation.rotations[0])
            abandoned_retry_thread = rotation.completed_abandonments[0]
            self.assertEqual(tuple(root.rglob("*.request.json")), ())
            with self.assertRaises(StateConflictError):
                adapter.retry_transport(first.exception.receipt.retry_id)
            with self.assertRaises(PlannerResultUnavailableError):
                adapter.complete(payload)
            self.assertEqual(calls, 2)
            coordinator.pi.invoke = original_writer
            different = _payload("chat-alpha")
            different["messages"] = [
                {"role": "user", "content": "Different prompt after lost Retry result."}
            ]
            self.assertIsInstance(adapter.complete(different), dict)
            self.assertEqual(calls, 3)
            self.assertNotEqual(rotation.active, abandoned_retry_thread)
            self.assertNotIn(rotation.active, rotation.completed_abandonments)

    def test_unavailable_entry_repairs_retry_action_after_restart_crash(self) -> None:
        class CrashAfterUnavailableEntryJournal(PiSceneRequestJournal):
            crash_once = True

            def _reconcile_planner_result_unavailable_locked(self, *args, **kwargs):
                if self.crash_once:
                    self.crash_once = False
                    raise _InjectedProcessCrash()
                return super()._reconcile_planner_result_unavailable_locked(
                    *args,
                    **kwargs,
                )

        short_root = Path("D:/Cera/tmp")
        short_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=short_root) as temporary:
            root = Path(temporary)
            coordinator, _, planner = _runtime(
                root,
                _SemanticValidator(SemanticVerdict.PASS),
            )
            original_plan = planner.plan
            original_writer = coordinator.pi.invoke
            ledger = ContinuousProviderCallLedger((root / "sol.jsonl").resolve())
            rotation = _FakeThreadRotation()
            calls = 0

            def fail_first_plan(request):
                nonlocal calls
                if not rotation.active:
                    rotation.initialize()
                calls += 1

                def dispatch():
                    if calls == 1:
                        raise ProviderTransportError(
                            ErrorCode.REASONER_UNAVAILABLE,
                            "injected initial Planner timeout",
                            external_provider_calls_observed=1,
                        )
                    return original_plan(request)

                return ledger.execute(
                    owner="planner",
                    operation=f"plan_{calls:04d}",
                    route="test",
                    model="gpt-5.6-sol",
                    effort="xhigh",
                    dispatch=dispatch,
                    finalize=lambda value: value,
                    stored_thread_sha256=rotation.active,
                )

            planner.plan = fail_first_plan
            journal_root = root / "journal"
            protected_root = root / "protected"
            adapter = PiSceneHttpAdapter(
                coordinator=coordinator,
                session_id="chat-alpha",
                context_provider=lambda _route, source, _messages: turn(source=source),
                request_journal=CrashAfterUnavailableEntryJournal(
                    journal_root,
                    protected_retry_root=protected_root,
                ),
                transport_provider_ledger_snapshot=lambda: _live_ledger_snapshot(ledger),
                **rotation.adapter_kwargs(),
            )
            payload = _payload("chat-alpha")
            with self.assertRaises(PiSceneManualTransportRetryError) as first:
                adapter.complete(payload)

            def fail_writer(_request):
                raise ProviderTransportError(
                    ErrorCode.COMPOSER_UNAVAILABLE,
                    "injected Writer timeout after retry Planner success",
                    external_provider_calls_observed=1,
                )

            coordinator.pi.invoke = fail_writer
            with self.assertRaises(_InjectedProcessCrash):
                adapter.retry_transport(first.exception.receipt.retry_id)
            self.assertEqual(calls, 2)
            self.assertEqual(len(rotation.completed_abandonments), 1)
            abandoned_retry_thread = rotation.completed_abandonments[0]

            restarted = PiSceneHttpAdapter(
                coordinator=coordinator,
                session_id="chat-alpha",
                context_provider=lambda _route, source, _messages: turn(source=source),
                request_journal=PiSceneRequestJournal(
                    journal_root,
                    protected_retry_root=protected_root,
                ),
                transport_provider_ledger_snapshot=lambda: _live_ledger_snapshot(ledger),
                **rotation.adapter_kwargs(),
            )
            status = restarted.transport_retry_status(first.exception.receipt.retry_id)
            self.assertEqual(status["state"], "blocked")
            self.assertEqual(status["blocked_reason_code"], "planner_result_unavailable")
            self.assertEqual(calls, 2)
            with self.assertRaises(PlannerResultUnavailableError):
                restarted.complete(payload)
            self.assertEqual(tuple(root.rglob("*.request.json")), ())
            coordinator.pi.invoke = original_writer
            different = _payload("chat-alpha")
            different["messages"] = [
                {"role": "user", "content": "Cold prompt after action repair."}
            ]
            self.assertIsInstance(restarted.complete(different), dict)
            self.assertEqual(calls, 3)
            self.assertNotEqual(rotation.active, abandoned_retry_thread)

    def test_get_during_active_retry_is_nonterminal_and_never_dispatches(
        self,
    ) -> None:
        short_root = Path("D:/Cera/tmp")
        short_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=short_root) as temporary:
            root = Path(temporary)
            coordinator, _, planner = _runtime(
                root,
                _SemanticValidator(SemanticVerdict.PASS),
            )
            original_plan = planner.plan
            sol_ledger = ContinuousProviderCallLedger((root / "sol.jsonl").resolve())
            retry_entered = Event()
            release_retry = Event()
            calls = 0
            rotation = _FakeThreadRotation()

            def controlled_plan(request):
                nonlocal calls
                calls += 1

                def dispatch():
                    if calls == 1:
                        raise ProviderTransportError(
                            ErrorCode.REASONER_UNAVAILABLE,
                            "injected Planner timeout",
                            external_provider_calls_observed=1,
                        )
                    retry_entered.set()
                    if not release_retry.wait(timeout=10):
                        raise AssertionError("test did not release retry Planner")
                    return original_plan(request)

                return sol_ledger.execute(
                    owner="planner",
                    operation=f"plan_{calls:04d}",
                    route="test",
                    model="gpt-5.6-sol",
                    effort="xhigh",
                    dispatch=dispatch,
                    finalize=lambda value: value,
                    stored_thread_sha256=rotation.active,
                )

            planner.plan = controlled_plan
            adapter = PiSceneHttpAdapter(
                coordinator=coordinator,
                session_id="chat-alpha",
                context_provider=lambda *_args: turn(),
                **rotation.adapter_kwargs(),
                transport_provider_ledger_snapshot=lambda: _live_ledger_snapshot(sol_ledger),
            )
            with self.assertRaises(PiSceneManualTransportRetryError) as first:
                adapter.complete(_payload("chat-alpha"))
            retry_id = first.exception.receipt.retry_id
            outcomes: list[object] = []

            def run_retry() -> None:
                try:
                    outcomes.append(adapter.retry_transport(retry_id))
                except BaseException as exc:  # pragma: no cover - asserted below
                    outcomes.append(exc)

            worker = Thread(target=run_retry)
            worker.start()
            self.assertTrue(retry_entered.wait(timeout=10))
            provider_count_before_get = calls
            status = adapter.transport_retry_status(retry_id)
            self.assertEqual(status["state"], "in_progress")
            self.assertEqual(status["phase"], "dispatch_started")
            self.assertEqual(calls, provider_count_before_get)
            release_retry.set()
            worker.join(timeout=10)
            self.assertFalse(worker.is_alive())
            self.assertEqual(len(outcomes), 1)
            self.assertIsInstance(outcomes[0], dict)
            self.assertEqual(calls, 2)

    def test_two_adapter_retry_posts_cannot_double_dispatch(self) -> None:
        short_root = Path("D:/Cera/tmp")
        short_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=short_root) as temporary:
            root = Path(temporary)
            coordinator, _, planner = _runtime(
                root,
                _SemanticValidator(SemanticVerdict.PASS),
            )
            original_plan = planner.plan
            sol_ledger = ContinuousProviderCallLedger((root / "sol.jsonl").resolve())
            retry_entered = Event()
            release_retry = Event()
            calls = 0
            rotation = _FakeThreadRotation()

            def controlled_plan(request):
                nonlocal calls
                calls += 1

                def dispatch():
                    if calls == 1:
                        raise ProviderTransportError(
                            ErrorCode.REASONER_UNAVAILABLE,
                            "injected Planner timeout",
                            external_provider_calls_observed=1,
                        )
                    retry_entered.set()
                    if not release_retry.wait(timeout=10):
                        raise AssertionError("test did not release retry Planner")
                    return original_plan(request)

                return sol_ledger.execute(
                    owner="planner",
                    operation=f"plan_{calls:04d}",
                    route="test",
                    model="gpt-5.6-sol",
                    effort="xhigh",
                    dispatch=dispatch,
                    finalize=lambda value: value,
                    stored_thread_sha256=rotation.active,
                )

            planner.plan = controlled_plan
            journal = PiSceneRequestJournal(
                root / "journal",
                protected_retry_root=root / "protected",
            )

            def build_adapter() -> PiSceneHttpAdapter:
                return PiSceneHttpAdapter(
                    coordinator=coordinator,
                    session_id="chat-alpha",
                    context_provider=lambda *_args: turn(),
                    request_journal=journal,
                    **rotation.adapter_kwargs(),
                    transport_provider_ledger_snapshot=lambda: _live_ledger_snapshot(sol_ledger),
                )

            first_adapter = build_adapter()
            second_adapter = build_adapter()
            with self.assertRaises(PiSceneManualTransportRetryError) as first:
                first_adapter.complete(_payload("chat-alpha"))
            retry_id = first.exception.receipt.retry_id
            outcomes: list[object] = []

            def run(adapter: PiSceneHttpAdapter) -> None:
                try:
                    outcomes.append(adapter.retry_transport(retry_id))
                except BaseException as exc:
                    outcomes.append(exc)

            worker_one = Thread(target=run, args=(first_adapter,))
            worker_two = Thread(target=run, args=(second_adapter,))
            worker_one.start()
            self.assertTrue(retry_entered.wait(timeout=10))
            worker_two.start()
            worker_two.join(timeout=2)
            release_retry.set()
            worker_one.join(timeout=10)
            worker_two.join(timeout=10)
            self.assertEqual(calls, 2)
            self.assertEqual(sum(isinstance(value, dict) for value in outcomes), 1)
            self.assertEqual(
                sum(isinstance(value, RequestReplayPendingError) for value in outcomes),
                1,
            )
            self.assertIsInstance(first_adapter.retry_transport(retry_id), dict)
            self.assertEqual(calls, 2)

    def test_retry_progressed_before_terminal_response_recovers_without_dispatch(
        self,
    ) -> None:
        class FailingCompletionJournal(PiSceneRequestJournal):
            fail_terminalization = False

            def complete(self, binding, response):
                if self.fail_terminalization:
                    self.fail_terminalization = False
                    raise RuntimeError("injected response-terminalization crash")
                return super().complete(binding, response)

        short_root = Path("D:/Cera/tmp")
        short_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=short_root) as temporary:
            root = Path(temporary)
            coordinator, _, planner = _runtime(
                root,
                _SemanticValidator(SemanticVerdict.PASS),
            )
            original_plan = planner.plan
            sol_ledger = ContinuousProviderCallLedger((root / "sol.jsonl").resolve())
            calls = 0
            rotation = _FakeThreadRotation()

            def fail_first_plan(request):
                nonlocal calls
                calls += 1

                def dispatch():
                    if calls == 1:
                        raise ProviderTransportError(
                            ErrorCode.REASONER_UNAVAILABLE,
                            "injected Planner timeout",
                            external_provider_calls_observed=1,
                        )
                    return original_plan(request)

                return sol_ledger.execute(
                    owner="planner",
                    operation=f"plan_{calls:04d}",
                    route="test",
                    model="gpt-5.6-sol",
                    effort="xhigh",
                    dispatch=dispatch,
                    finalize=lambda value: value,
                    stored_thread_sha256=rotation.active,
                )

            planner.plan = fail_first_plan
            journal = FailingCompletionJournal(
                root / "journal",
                protected_retry_root=root / "protected",
            )
            adapter = PiSceneHttpAdapter(
                coordinator=coordinator,
                session_id="chat-alpha",
                context_provider=lambda *_args: turn(),
                request_journal=journal,
                **rotation.adapter_kwargs(),
                transport_provider_ledger_snapshot=lambda: _live_ledger_snapshot(sol_ledger),
            )
            with self.assertRaises(PiSceneManualTransportRetryError) as first:
                adapter.complete(_payload("chat-alpha"))
            journal.fail_terminalization = True
            with self.assertRaisesRegex(
                RuntimeError,
                "could not terminalize its request replay journal",
            ):
                adapter.retry_transport(first.exception.receipt.retry_id)
            status = adapter.transport_retry_status(first.exception.receipt.retry_id)
            self.assertEqual(status["state"], "succeeded")
            self.assertEqual(
                status["completion_sha256"],
                canonical_sha256(status["completion"]),
            )
            self.assertEqual(
                adapter.retry_transport(first.exception.receipt.retry_id),
                status["completion"],
            )
            self.assertEqual(calls, 2)

    def test_transient_context_failure_does_not_destroy_retry_action(self) -> None:
        short_root = Path("D:/Cera/tmp")
        short_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=short_root) as temporary:
            root = Path(temporary)
            coordinator, _, planner = _runtime(
                root,
                _SemanticValidator(SemanticVerdict.PASS),
            )
            sol_ledger = ContinuousProviderCallLedger((root / "sol.jsonl").resolve())
            context_available = True

            def fail_plan(_request):
                def dispatch():
                    raise ProviderTransportError(
                        ErrorCode.REASONER_UNAVAILABLE,
                        "injected Planner timeout",
                        external_provider_calls_observed=1,
                    )

                return sol_ledger.execute(
                    owner="planner",
                    operation="plan_0001",
                    route="test",
                    model="gpt-5.6-sol",
                    effort="xhigh",
                    dispatch=dispatch,
                    finalize=lambda value: value,
                    stored_thread_sha256="a" * 64,
                )

            def context(*_args):
                if not context_available:
                    raise RuntimeError("injected transient context I/O")
                return turn()

            planner.plan = fail_plan
            adapter = PiSceneHttpAdapter(
                coordinator=coordinator,
                session_id="chat-alpha",
                context_provider=context,
                transport_retry_reinitializer=lambda *_args: None,
                transport_provider_ledger_snapshot=lambda: _live_ledger_snapshot(sol_ledger),
            )
            with self.assertRaises(PiSceneManualTransportRetryError) as first:
                adapter.complete(_payload("chat-alpha"))
            retry_id = first.exception.receipt.retry_id
            context_available = False
            with self.assertRaisesRegex(RuntimeError, "transient context"):
                adapter.transport_retry_status(retry_id)
            context_available = True
            status = adapter.transport_retry_status(retry_id)
            self.assertEqual(status["state"], "eligible")
            self.assertIs(status["retry_transport_enabled"], True)
            self.assertEqual(sol_ledger.dispatched_call_count, 1)

    def test_accepted_head_drift_permanently_blocks_old_retry(self) -> None:
        short_root = Path("D:/Cera/tmp")
        short_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=short_root) as temporary:
            root = Path(temporary)
            coordinator, _, planner = _runtime(
                root,
                _SemanticValidator(SemanticVerdict.PASS),
            )
            original_plan = planner.plan
            sol_ledger = ContinuousProviderCallLedger((root / "sol.jsonl").resolve())

            def fail_plan(_request):
                def dispatch():
                    raise ProviderTransportError(
                        ErrorCode.REASONER_UNAVAILABLE,
                        "injected Planner timeout",
                        external_provider_calls_observed=1,
                    )

                return sol_ledger.execute(
                    owner="planner",
                    operation="plan_0001",
                    route="test",
                    model="gpt-5.6-sol",
                    effort="xhigh",
                    dispatch=dispatch,
                    finalize=lambda value: value,
                    stored_thread_sha256="a" * 64,
                )

            planner.plan = fail_plan
            adapter = PiSceneHttpAdapter(
                coordinator=coordinator,
                session_id="chat-alpha",
                context_provider=lambda *_args: turn(),
                transport_retry_reinitializer=lambda *_args: None,
                transport_provider_ledger_snapshot=lambda: _live_ledger_snapshot(sol_ledger),
            )
            with self.assertRaises(PiSceneManualTransportRetryError) as first:
                adapter.complete(_payload("chat-alpha"))
            planner.plan = original_plan
            coordinator.start_ordinary(turn(source="A different accepted turn."))
            status = adapter.transport_retry_status(first.exception.receipt.retry_id)
            self.assertEqual(status["state"], "blocked")
            self.assertEqual(status["blocked_reason_code"], "effect_state_changed")

    def test_get_does_not_reconcile_while_any_provider_ledger_lease_is_live(self) -> None:
        short_root = Path("D:/Cera/tmp")
        short_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=short_root) as temporary:
            root = Path(temporary)
            coordinator, _, planner = _runtime(
                root,
                _SemanticValidator(SemanticVerdict.PASS),
            )
            sol_ledger = ContinuousProviderCallLedger((root / "sol.jsonl").resolve())

            def fail_plan(_request):
                def dispatch():
                    raise ProviderTransportError(
                        ErrorCode.REASONER_UNAVAILABLE,
                        "injected Planner timeout",
                        external_provider_calls_observed=1,
                    )

                return sol_ledger.execute(
                    owner="planner",
                    operation="plan_0001",
                    route="test",
                    model="gpt-5.6-sol",
                    effort="xhigh",
                    dispatch=dispatch,
                    finalize=lambda value: value,
                    stored_thread_sha256="a" * 64,
                )

            planner.plan = fail_plan
            journal = PiSceneRequestJournal(
                root / "journal",
                protected_retry_root=root / "protected",
            )
            adapter = PiSceneHttpAdapter(
                coordinator=coordinator,
                session_id="chat-alpha",
                context_provider=lambda *_args: turn(),
                request_journal=journal,
                transport_retry_reinitializer=lambda *_args: None,
                transport_retry_active_thread_snapshot=lambda *_args: "b" * 64,
                transport_provider_ledger_snapshot=lambda: _live_ledger_snapshot(sol_ledger),
            )
            with self.assertRaises(PiSceneManualTransportRetryError) as first:
                adapter.complete(_payload("chat-alpha"))
            retry_id = first.exception.receipt.retry_id
            journal.authorize_transport_retry(retry_id)
            journal.mark_transport_owner_rotated(retry_id)
            journal.bind_transport_fresh_thread(retry_id, "b" * 64)
            journal.mark_transport_dispatch_started(
                retry_id,
                _live_ledger_snapshot(sol_ledger),
            )
            lease_held = Event()
            release_lease = Event()

            def hold_global_provider_lease() -> None:
                with journal.provider_dispatch_claim():
                    lease_held.set()
                    release_lease.wait(timeout=10)

            worker = Thread(target=hold_global_provider_lease)
            worker.start()
            self.assertTrue(lease_held.wait(timeout=10))
            status = adapter.transport_retry_status(retry_id)
            self.assertEqual(status["state"], "in_progress")
            self.assertEqual(status["phase"], "dispatch_started")
            self.assertEqual(sol_ledger.dispatched_call_count, 1)
            release_lease.set()
            worker.join(timeout=10)
            self.assertFalse(worker.is_alive())
            recovered = adapter.transport_retry_status(retry_id)
            self.assertEqual(recovered["state"], "eligible")
            self.assertEqual(sol_ledger.dispatched_call_count, 1)

    def test_http_error_retry_post_and_replay_use_closed_public_schema(self) -> None:
        short_root = Path("D:/Cera/tmp")
        short_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=short_root) as temporary:
            root = Path(temporary)
            coordinator, _, planner = _runtime(
                root,
                _SemanticValidator(SemanticVerdict.PASS),
            )
            original_plan = planner.plan
            sol_ledger = ContinuousProviderCallLedger((root / "sol.jsonl").resolve())
            calls = 0
            rotation = _FakeThreadRotation()

            def fail_once(request):
                nonlocal calls
                calls += 1

                def dispatch():
                    if calls == 1:
                        raise ProviderTransportError(
                            ErrorCode.REASONER_UNAVAILABLE,
                            "injected timeout",
                            safe_diagnostics=("transport:timeout",),
                            external_provider_calls_observed=1,
                        )
                    return original_plan(request)

                return sol_ledger.execute(
                    owner="planner",
                    operation=f"plan_{calls:04d}",
                    route="test",
                    model="gpt-5.6-sol",
                    effort="xhigh",
                    dispatch=dispatch,
                    finalize=lambda value: value,
                    stored_thread_sha256=rotation.active,
                )

            planner.plan = fail_once
            adapter = PiSceneHttpAdapter(
                coordinator=coordinator,
                session_id="chat-alpha",
                context_provider=lambda *_args: turn(),
                **rotation.adapter_kwargs(),
                transport_provider_ledger_snapshot=lambda: _live_ledger_snapshot(sol_ledger),
            )
            token = "t" * 32
            server = build_pi_scene_server(
                adapter,
                PiSceneServerConfigV1(
                    host="127.0.0.1",
                    port=0,
                    authorization_token=token,
                    approved_origins=("http://127.0.0.1:8000",),
                ),
            )
            worker = Thread(target=server.serve_forever, daemon=True)
            worker.start()
            endpoint = f"http://127.0.0.1:{server.server_address[1]}"

            def post(path: str, body: dict[str, object]) -> tuple[int, dict]:
                request = Request(
                    endpoint + path,
                    data=json.dumps(body).encode("utf-8"),
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/json",
                    },
                    method="POST",
                )
                try:
                    with urlopen(request, timeout=10) as response:
                        return response.status, json.loads(response.read())
                except HTTPError as exc:
                    return exc.code, json.loads(exc.read())

            def get(path: str) -> tuple[int, dict]:
                request = Request(
                    endpoint + path,
                    headers={"Authorization": f"Bearer {token}"},
                    method="GET",
                )
                try:
                    with urlopen(request, timeout=10) as response:
                        return response.status, json.loads(response.read())
                except HTTPError as exc:
                    return exc.code, json.loads(exc.read())

            try:
                status, failed = post("/v1/chat/completions", _payload("chat-alpha"))
                self.assertEqual(status, 500)
                error = failed["error"]
                self.assertEqual(error["error_code"], "CERA_PROVIDER_TRANSPORT_FAILED")
                self.assertEqual(error["retry_mode"], "manual_transport")
                self.assertIs(error["retry_transport_enabled"], True)
                self.assertIs(error["provider_operation_submitted"], True)
                retry = error["transport_retry"]
                self.assertEqual(
                    set(retry),
                    {
                        "schema_version",
                        "retry_id",
                        "retry_url",
                        "method",
                        "eligible",
                        "automatic",
                        "effect_proof_sha256",
                    },
                )
                self.assertEqual(
                    retry["retry_url"], f"/v1/cera/transport-retries/{retry['retry_id']}"
                )

                get_status, eligible = get(retry["retry_url"])
                self.assertEqual(get_status, 200)
                self.assertEqual(
                    set(eligible),
                    {
                        "schema_version",
                        "retry_id",
                        "request_id",
                        "state",
                        "effect_proof_sha256",
                        "retry_transport_enabled",
                        "transport_retry",
                    },
                )
                self.assertEqual(eligible["state"], "eligible")
                self.assertIs(eligible["retry_transport_enabled"], True)
                self.assertEqual(eligible["transport_retry"], retry)

                invalid_status, invalid = post(retry["retry_url"], {"extra": True})
                self.assertEqual(invalid_status, 422)
                self.assertIs(invalid["error"]["retry_transport_enabled"], False)
                self.assertNotIn("transport_retry", invalid["error"])
                self.assertEqual(calls, 1)

                success_status, success = post(retry["retry_url"], {})
                self.assertEqual(success_status, 200)
                self.assertEqual(success["cera"]["request_id"], error["request_id"])
                self.assertEqual(
                    success["cera"]["transport_retry_summary"]["prior_failed_provider_operations"],
                    1,
                )
                replay_status, replay = post(retry["retry_url"], {})
                self.assertEqual(replay_status, 200)
                self.assertEqual(replay, success)
                self.assertEqual(calls, 2)

                done_status, done = get(retry["retry_url"])
                self.assertEqual(done_status, 200)
                self.assertEqual(done["state"], "succeeded")
                self.assertEqual(done["completion"], success)
                self.assertEqual(done["request_id"], done["completion"]["cera"]["request_id"])
                self.assertEqual(done["completion_sha256"], canonical_sha256(success))
                missing_status, missing = get("/v1/cera/transport-retries/retry-" + "f" * 64)
                self.assertEqual(missing_status, 404)
                self.assertEqual(
                    missing["error"]["error_code"],
                    "CERA_TRANSPORT_RETRY_NOT_FOUND",
                )
                self.assertNotIn("transport_retry", missing["error"])
                malformed_status, malformed = get("/v1/cera/transport-retries/not-a-retry-id")
                self.assertEqual(malformed_status, 404)
                self.assertEqual(
                    malformed["error"]["error_code"],
                    "CERA_TRANSPORT_RETRY_NOT_FOUND",
                )
                self.assertNotIn("transport_retry", malformed["error"])
            finally:
                server.shutdown()
                server.server_close()
                worker.join(timeout=5)

    def test_crash_before_completion_marker_retires_lost_planner_thread(self) -> None:
        short_root = Path("D:/Cera/tmp")
        short_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=short_root) as temporary:
            root = Path(temporary)
            coordinator, _, planner = _runtime(
                root,
                _SemanticValidator(SemanticVerdict.PASS),
            )
            ledger = ContinuousProviderCallLedger((root / "sol.jsonl").resolve())
            rotation = _FakeThreadRotation()
            _, used_threads = _install_successful_ledger_planner(
                planner,
                ledger,
                rotation,
            )
            journal_root = root / "journal"
            protected_root = root / "protected"
            adapter = PiSceneHttpAdapter(
                coordinator=coordinator,
                session_id="chat-alpha",
                context_provider=lambda _route, source, _messages: turn(source=source),
                request_journal=PiSceneRequestJournal(
                    journal_root,
                    protected_retry_root=protected_root,
                ),
                transport_provider_ledger_snapshot=lambda: _live_ledger_snapshot(ledger),
                **rotation.adapter_kwargs(),
            )
            payload = _payload("chat-alpha")
            payload["messages"] = [
                {"role": "user", "content": "SENTINEL crash before completion marker."}
            ]
            with self.assertRaises(_InjectedProcessCrash):
                _run_direct_staged_ordinary(
                    adapter,
                    payload,
                    crash_point="before_completion_marker",
                )
            self.assertEqual(ledger.dispatched_call_count, 1)
            self.assertEqual(used_threads, ["a" * 64])
            authority = tuple(protected_root.rglob("DISPATCH_AUTHORITY/*.json"))
            self.assertEqual(len(authority), 1)
            self.assertIn("SENTINEL crash before", authority[0].read_text(encoding="utf-8"))

            restarted = PiSceneHttpAdapter(
                coordinator=coordinator,
                session_id="chat-alpha",
                context_provider=lambda _route, source, _messages: turn(source=source),
                request_journal=PiSceneRequestJournal(
                    journal_root,
                    protected_retry_root=protected_root,
                ),
                transport_provider_ledger_snapshot=lambda: _live_ledger_snapshot(ledger),
                **rotation.adapter_kwargs(),
            )
            with self.assertRaises(PlannerResultUnavailableError) as unavailable:
                restarted.complete(payload)
            self.assertEqual(
                unavailable.exception.disposition.thread_disposition,
                "completed_uncommitted_never_resume",
            )
            self.assertEqual(ledger.dispatched_call_count, 1)
            self.assertEqual(rotation.completed_abandonments, ["a" * 64])
            self.assertFalse(
                any(
                    "SENTINEL crash before" in path.read_text(encoding="utf-8")
                    for path in root.rglob("*")
                    if path.is_file()
                )
            )
            with self.assertRaises(PlannerResultUnavailableError):
                restarted.complete(payload)
            different = _payload("chat-alpha")
            different["messages"] = [
                {"role": "user", "content": "A different prompt after recovery."}
            ]
            response = restarted.complete(different)
            self.assertIsInstance(response, dict)
            self.assertEqual(ledger.dispatched_call_count, 2)
            self.assertEqual(len(used_threads), 2)
            self.assertNotEqual(used_threads[1], used_threads[0])

    def test_crash_after_completion_marker_redacts_raw_then_retires_thread(self) -> None:
        short_root = Path("D:/Cera/tmp")
        short_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=short_root) as temporary:
            root = Path(temporary)
            coordinator, _, planner = _runtime(
                root,
                _SemanticValidator(SemanticVerdict.PASS),
            )
            ledger = ContinuousProviderCallLedger((root / "sol.jsonl").resolve())
            rotation = _FakeThreadRotation()
            _install_successful_ledger_planner(planner, ledger, rotation)
            journal_root = root / "journal"
            protected_root = root / "protected"

            adapter = PiSceneHttpAdapter(
                coordinator=coordinator,
                session_id="chat-alpha",
                context_provider=lambda _route, source, _messages: turn(source=source),
                request_journal=PiSceneRequestJournal(
                    journal_root,
                    protected_retry_root=protected_root,
                ),
                transport_provider_ledger_snapshot=lambda: _live_ledger_snapshot(ledger),
                **rotation.adapter_kwargs(),
            )
            payload = _payload("chat-alpha")
            payload["messages"] = [
                {"role": "user", "content": "SENTINEL crash after completion marker."}
            ]
            with self.assertRaises(_InjectedProcessCrash):
                _run_direct_staged_ordinary(
                    adapter,
                    payload,
                    crash_point="after_completion_marker",
                )
            staged = adapter._durable_request_journal().staged_transport_dispatch(
                adapter._prepare_bound_request(payload)[2]
            )
            self.assertIsNotNone(staged)
            assert staged is not None
            self.assertEqual(staged["phase"], "planner_completed_pending_progress")
            self.assertIsNone(staged["normalized_request"])
            self.assertFalse(
                any(
                    "SENTINEL crash after" in path.read_text(encoding="utf-8")
                    for path in root.rglob("*")
                    if path.is_file()
                )
            )

            restarted = PiSceneHttpAdapter(
                coordinator=coordinator,
                session_id="chat-alpha",
                context_provider=lambda _route, source, _messages: turn(source=source),
                request_journal=PiSceneRequestJournal(
                    journal_root,
                    protected_retry_root=protected_root,
                ),
                transport_provider_ledger_snapshot=lambda: _live_ledger_snapshot(ledger),
                **rotation.adapter_kwargs(),
            )
            with self.assertRaises(PlannerResultUnavailableError):
                restarted.complete(payload)
            self.assertEqual(ledger.dispatched_call_count, 1)
            self.assertEqual(rotation.completed_abandonments, ["a" * 64])

    def test_durable_review_before_http_progress_recovers_without_thread_retirement(
        self,
    ) -> None:
        short_root = Path("D:/Cera/tmp")
        short_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=short_root) as temporary:
            root = Path(temporary)
            coordinator, _, planner = _runtime(
                root,
                _SemanticValidator(SemanticVerdict.PASS),
            )
            ledger = ContinuousProviderCallLedger((root / "sol.jsonl").resolve())
            rotation = _FakeThreadRotation()
            _install_successful_ledger_planner(planner, ledger, rotation)
            journal_root = root / "journal"
            protected_root = root / "protected"

            def stateful_context(_route, source, _messages):
                value = turn(source=source)
                head = coordinator.store.load_head(
                    world_id=value.world_id,
                    branch_id=value.branch_id,
                )
                if head.receipt is None:
                    return value
                return replace(
                    value,
                    current_state={
                        **value.current_state,
                        "accepted_generation": head.receipt.generation,
                    },
                    recent_prose=(*value.recent_prose, "Accepted state advanced."),
                )

            adapter = PiSceneHttpAdapter(
                coordinator=coordinator,
                session_id="chat-alpha",
                context_provider=stateful_context,
                request_journal=PiSceneRequestJournal(
                    journal_root,
                    protected_retry_root=protected_root,
                ),
                transport_provider_ledger_snapshot=lambda: _live_ledger_snapshot(ledger),
                **rotation.adapter_kwargs(),
            )
            payload = _payload("chat-alpha")
            payload["messages"] = [
                {"role": "user", "content": "Durable review before HTTP progress."}
            ]
            _, _, binding, review = _run_direct_staged_ordinary(
                adapter,
                payload,
                crash_point=None,
            )
            self.assertIsNotNone(review.accepted_receipt)
            staged = adapter._durable_request_journal().staged_transport_dispatch(binding)
            self.assertIsNotNone(staged)
            assert staged is not None
            self.assertEqual(staged["phase"], "planner_completed_pending_progress")

            restarted = PiSceneHttpAdapter(
                coordinator=coordinator,
                session_id="chat-alpha",
                context_provider=stateful_context,
                request_journal=PiSceneRequestJournal(
                    journal_root,
                    protected_retry_root=protected_root,
                ),
                transport_provider_ledger_snapshot=lambda: _live_ledger_snapshot(ledger),
                **rotation.adapter_kwargs(),
            )
            response = restarted.complete(payload)
            rebuilt_turn = restarted._prepare_bound_request(payload)[1]
            self.assertNotEqual(canonical_sha256(rebuilt_turn), canonical_sha256(review.turn_input))
            self.assertTrue(response["cera"]["story_state_committed"])
            self.assertEqual(ledger.dispatched_call_count, 1)
            self.assertEqual(rotation.completed_abandonments, [])
            self.assertEqual(rotation.active, "a" * 64)
            self.assertEqual(tuple(protected_root.rglob("DISPATCH_AUTHORITY/*.json")), ())

    def test_unrecognized_durable_effect_after_completed_plan_blocks_branch(self) -> None:
        short_root = Path("D:/Cera/tmp")
        short_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=short_root) as temporary:
            root = Path(temporary)
            coordinator, _, planner = _runtime(
                root,
                _SemanticValidator(SemanticVerdict.PASS),
            )
            ledger = ContinuousProviderCallLedger((root / "sol.jsonl").resolve())
            rotation = _FakeThreadRotation()
            _install_successful_ledger_planner(planner, ledger, rotation)
            adapter = PiSceneHttpAdapter(
                coordinator=coordinator,
                session_id="chat-alpha",
                context_provider=lambda _route, source, _messages: turn(source=source),
                transport_provider_ledger_snapshot=lambda: _live_ledger_snapshot(ledger),
                **rotation.adapter_kwargs(),
            )
            payload = _payload("chat-alpha")
            payload["messages"] = [
                {"role": "user", "content": "Completed plan with no durable review."}
            ]
            with self.assertRaises(_InjectedProcessCrash):
                _run_direct_staged_ordinary(
                    adapter,
                    payload,
                    crash_point="after_completion_marker",
                )
            # Simulate an unrecognized same-branch effect. It is deliberately
            # not the persisted result for the staged request.
            coordinator.start_ordinary(turn(source="Unrelated durable effect."))
            with self.assertRaises(PlannerResultUnavailableError) as unavailable:
                adapter.complete(payload)
            disposition = unavailable.exception.disposition
            self.assertEqual(
                disposition.thread_disposition,
                "durable_progress_unrecognized",
            )
            self.assertTrue(disposition.branch_dispatch_blocked)
            self.assertFalse(disposition.thread_retired)
            self.assertEqual(rotation.completed_abandonments, [])
            self.assertEqual(tuple(root.rglob("*.request.json")), ())
            different = _payload("chat-alpha")
            different["messages"] = [
                {"role": "user", "content": "Different prompt remains branch-blocked."}
            ]
            with self.assertRaises(RequestReplayPendingError):
                adapter.complete(different)

    def test_local_pretransport_failure_keeps_thread_and_discards_raw_custody(self) -> None:
        short_root = Path("D:/Cera/tmp")
        short_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=short_root) as temporary:
            root = Path(temporary)
            coordinator, _, planner = _runtime(
                root,
                _SemanticValidator(SemanticVerdict.PASS),
            )
            original_plan = planner.plan
            ledger = ContinuousProviderCallLedger((root / "sol.jsonl").resolve())
            rotation = _FakeThreadRotation()

            def fail_locally(_request):
                return ledger.execute(
                    owner="planner",
                    operation="plan_local_failure",
                    route="test",
                    model="gpt-5.6-sol",
                    effort="xhigh",
                    dispatch=lambda: (_ for _ in ()).throw(
                        ContractValidationError("injected local validation failure")
                    ),
                    finalize=lambda value: value,
                    stored_thread_sha256=rotation.active,
                )

            planner.plan = fail_locally
            adapter = PiSceneHttpAdapter(
                coordinator=coordinator,
                session_id="chat-alpha",
                context_provider=lambda _route, source, _messages: turn(source=source),
                transport_provider_ledger_snapshot=lambda: _live_ledger_snapshot(ledger),
                **rotation.adapter_kwargs(),
            )
            payload = _payload("chat-alpha")
            payload["messages"] = [
                {"role": "user", "content": "SENTINEL local pretransport failure."}
            ]
            with self.assertRaisesRegex(ContractValidationError, "local validation"):
                adapter.complete(payload)
            self.assertEqual(ledger.dispatched_call_count, 0)
            self.assertEqual(rotation.completed_abandonments, [])
            self.assertEqual(rotation.active, "a" * 64)
            self.assertEqual(tuple(root.rglob("DISPATCH_AUTHORITY/*.json")), ())
            planner.plan = original_plan
            different = _payload("chat-alpha")
            different["messages"] = [
                {"role": "user", "content": "Different prompt after local failure."}
            ]
            self.assertIsInstance(adapter.complete(different), dict)

    def test_post_validation_failure_retires_consumed_thread_and_unblocks_branch(
        self,
    ) -> None:
        short_root = Path("D:/Cera/tmp")
        short_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=short_root) as temporary:
            root = Path(temporary)
            coordinator, _, planner = _runtime(
                root,
                _SemanticValidator(SemanticVerdict.PASS),
            )
            original_plan = planner.plan
            ledger = ContinuousProviderCallLedger((root / "sol.jsonl").resolve())
            rotation = _FakeThreadRotation()
            used_threads: list[str] = []

            def validate_after_provider(request):
                if not rotation.active:
                    rotation.initialize()
                used_threads.append(rotation.active)
                attempt = len(used_threads)

                def finalize(value):
                    if attempt == 1:
                        raise ContractValidationError("injected post-validation failure")
                    return value

                return ledger.execute(
                    owner="planner",
                    operation=f"plan_{attempt:04d}",
                    route="test",
                    model="gpt-5.6-sol",
                    effort="xhigh",
                    dispatch=lambda: original_plan(request),
                    finalize=finalize,
                    stored_thread_sha256=rotation.active,
                )

            planner.plan = validate_after_provider
            adapter = PiSceneHttpAdapter(
                coordinator=coordinator,
                session_id="chat-alpha",
                context_provider=lambda _route, source, _messages: turn(source=source),
                transport_provider_ledger_snapshot=lambda: _live_ledger_snapshot(ledger),
                **rotation.adapter_kwargs(),
            )
            payload = _payload("chat-alpha")
            payload["messages"] = [{"role": "user", "content": "Post-validation result is lost."}]
            with self.assertRaises(PlannerResultUnavailableError) as unavailable:
                adapter.complete(payload)
            self.assertEqual(
                unavailable.exception.disposition.completion.terminal_state,
                "provider_completed_post_validation_failed",
            )
            self.assertEqual(rotation.completed_abandonments, ["a" * 64])
            self.assertEqual(ledger.dispatched_call_count, 1)
            self.assertEqual(tuple(root.rglob("*.request.json")), ())
            different = _payload("chat-alpha")
            different["messages"] = [
                {"role": "user", "content": "Different prompt after validation failure."}
            ]
            self.assertIsInstance(adapter.complete(different), dict)
            self.assertEqual(ledger.dispatched_call_count, 2)
            self.assertEqual(len(used_threads), 2)
            self.assertNotEqual(used_threads[0], used_threads[1])

    def test_planner_call_classifier_rejects_malformed_same_thread_evidence(self) -> None:
        valid = _append_ledger_call(
            _ledger_snapshot(0),
            call_number=1,
            thread_sha256="a" * 64,
            states=(
                "prepared_not_invoked",
                "transport_invoked",
                "provider_completed",
                "typed_accepted",
            ),
        )
        self.assertEqual(
            classify_planner_call_events(valid.events),
            "typed_accepted",
        )
        malformed: list[tuple[str, tuple[dict[str, object], ...]]] = []
        wrong_owner = [dict(value) for value in valid.events]
        wrong_owner[-1]["owner"] = "validator"
        malformed.append(("owner", tuple(wrong_owner)))
        changed_operation = [dict(value) for value in valid.events]
        changed_operation[-1]["operation"] = "other_plan"
        malformed.append(("operation", tuple(changed_operation)))
        wrong_schema = [dict(value) for value in valid.events]
        wrong_schema[0]["schema_version"] = "cera.continuous_provider_call_ledger_event.v2"
        malformed.append(("schema", tuple(wrong_schema)))
        invalid_graph = _append_ledger_call(
            _ledger_snapshot(0),
            call_number=2,
            thread_sha256="b" * 64,
            states=(
                "prepared_not_invoked",
                "worker_started_not_invoked",
                "transport_invoked",
                "provider_completed",
                "typed_accepted",
            ),
        )
        malformed.append(("graph", tuple(dict(value) for value in invalid_graph.events)))
        for label, events in malformed:
            with self.subTest(label=label):
                with self.assertRaises(ContractValidationError):
                    classify_planner_call_events(events)

    def test_malformed_completed_planner_evidence_retires_thread_before_unblocking(
        self,
    ) -> None:
        short_root = Path("D:/Cera/tmp")
        short_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=short_root) as temporary:
            root = Path(temporary)
            coordinator, _, planner = _runtime(
                root,
                _SemanticValidator(SemanticVerdict.PASS),
            )
            ledger = ContinuousProviderCallLedger((root / "sol.jsonl").resolve())
            rotation = _FakeThreadRotation()
            _, used_threads = _install_successful_ledger_planner(planner, ledger, rotation)
            tamper = {"enabled": True}

            def ledger_snapshot() -> PiSceneProviderLedgerSnapshotV1:
                snapshot = _live_ledger_snapshot(ledger)
                if not tamper["enabled"] or not snapshot.events:
                    return snapshot
                events = [dict(value) for value in snapshot.events]
                for value in events:
                    value["stored_thread_sha256"] = "b" * 64
                return PiSceneProviderLedgerSnapshotV1(
                    schema_version=PiSceneProviderLedgerSnapshotV1.SCHEMA_VERSION,
                    dispatched_call_count=provider_ledger_dispatched_call_count(events),
                    events=tuple(events),
                )

            adapter = PiSceneHttpAdapter(
                coordinator=coordinator,
                session_id="chat-alpha",
                context_provider=lambda _route, source, _messages: turn(source=source),
                transport_provider_ledger_snapshot=ledger_snapshot,
                **rotation.adapter_kwargs(),
            )
            payload = _payload("chat-alpha")
            payload["messages"] = [
                {
                    "role": "user",
                    "content": "SENTINEL malformed completed Planner evidence.",
                }
            ]
            with self.assertRaises(PlannerResultUnavailableError) as unavailable:
                _run_direct_staged_ordinary(
                    adapter,
                    payload,
                    crash_point=None,
                )
            self.assertEqual(
                unavailable.exception.disposition.thread_disposition,
                "planner_evidence_invalid_never_resume",
            )
            self.assertFalse(unavailable.exception.disposition.branch_dispatch_blocked)
            self.assertEqual(rotation.completed_abandonments, ["a" * 64])
            self.assertEqual(used_threads, ["a" * 64])
            self.assertFalse(
                any(
                    "SENTINEL malformed" in path.read_text(encoding="utf-8")
                    for path in root.rglob("*")
                    if path.is_file()
                )
            )

            tamper["enabled"] = False
            with self.assertRaises(PlannerResultUnavailableError):
                adapter.complete(payload)
            different = _payload("chat-alpha")
            different["messages"] = [
                {"role": "user", "content": "Different prompt after invalid evidence."}
            ]
            self.assertIsInstance(adapter.complete(different), dict)
            self.assertEqual(ledger.dispatched_call_count, 2)
            self.assertEqual(len(used_threads), 2)
            self.assertNotEqual(used_threads[1], used_threads[0])

    def test_foreign_thread_hash_on_completed_call_never_reuses_active_thread(self) -> None:
        short_root = Path("D:/Cera/tmp")
        short_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=short_root) as temporary:
            root = Path(temporary)
            coordinator, _, planner = _runtime(
                root,
                _SemanticValidator(SemanticVerdict.PASS),
            )
            ledger = ContinuousProviderCallLedger((root / "sol.jsonl").resolve())
            rotation = _FakeThreadRotation()
            _, used_threads = _install_successful_ledger_planner(planner, ledger, rotation)
            tamper = {"enabled": True}

            def ledger_snapshot() -> PiSceneProviderLedgerSnapshotV1:
                snapshot = _live_ledger_snapshot(ledger)
                if not tamper["enabled"] or not snapshot.events:
                    return snapshot
                events = [dict(value) for value in snapshot.events]
                for value in events:
                    value["stored_thread_sha256"] = "b" * 64
                return PiSceneProviderLedgerSnapshotV1(
                    schema_version=PiSceneProviderLedgerSnapshotV1.SCHEMA_VERSION,
                    dispatched_call_count=provider_ledger_dispatched_call_count(events),
                    events=tuple(events),
                )

            adapter = PiSceneHttpAdapter(
                coordinator=coordinator,
                session_id="chat-alpha",
                context_provider=lambda _route, source, _messages: turn(source=source),
                transport_provider_ledger_snapshot=ledger_snapshot,
                **rotation.adapter_kwargs(),
            )
            payload = _payload("chat-alpha")
            payload["messages"] = [
                {
                    "role": "user",
                    "content": "SENTINEL foreign Planner thread evidence.",
                }
            ]
            with self.assertRaises(_InjectedProcessCrash):
                _run_direct_staged_ordinary(
                    adapter,
                    payload,
                    crash_point="before_completion_marker",
                )
            self.assertEqual(rotation.completed_abandonments, [])
            with self.assertRaises(PlannerResultUnavailableError) as unavailable:
                adapter.complete(payload)
            self.assertEqual(
                unavailable.exception.disposition.thread_disposition,
                "planner_evidence_invalid_never_resume",
            )
            self.assertEqual(rotation.completed_abandonments, ["a" * 64])
            self.assertEqual(used_threads, ["a" * 64])
            self.assertFalse(
                any(
                    "SENTINEL foreign" in path.read_text(encoding="utf-8")
                    for path in root.rglob("*")
                    if path.is_file()
                )
            )

            tamper["enabled"] = False
            with self.assertRaises(PlannerResultUnavailableError):
                adapter.complete(payload)
            different = _payload("chat-alpha")
            different["messages"] = [
                {"role": "user", "content": "Different prompt after foreign evidence."}
            ]
            self.assertIsInstance(adapter.complete(different), dict)
            self.assertEqual(ledger.dispatched_call_count, 2)
            self.assertEqual(len(used_threads), 2)
            self.assertNotEqual(used_threads[1], "a" * 64)

    def test_restart_retires_shifted_active_thread_before_branch_unblocks(self) -> None:
        short_root = Path("D:/Cera/tmp")
        short_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=short_root) as temporary:
            root = Path(temporary)
            coordinator, _, planner = _runtime(
                root,
                _SemanticValidator(SemanticVerdict.PASS),
            )
            original_plan = planner.plan
            ledger = ContinuousProviderCallLedger((root / "sol.jsonl").resolve())
            rotation = _FakeThreadRotation()
            used_threads: list[str] = []

            def shifted_plan(request):
                if not used_threads:
                    rotation.active = "b" * 64
                elif not rotation.active:
                    rotation.initialize()
                used_threads.append(rotation.active)
                return ledger.execute(
                    owner="planner",
                    operation=f"plan_{len(used_threads):04d}",
                    route="test",
                    model="gpt-5.6-sol",
                    effort="xhigh",
                    dispatch=lambda: original_plan(request),
                    finalize=lambda value: value,
                    stored_thread_sha256=rotation.active,
                )

            planner.plan = shifted_plan
            adapter = PiSceneHttpAdapter(
                coordinator=coordinator,
                session_id="chat-alpha",
                context_provider=lambda _route, source, _messages: turn(source=source),
                transport_provider_ledger_snapshot=lambda: _live_ledger_snapshot(ledger),
                **rotation.adapter_kwargs(),
            )
            payload = _payload("chat-alpha")
            payload["messages"] = [
                {"role": "user", "content": "Planner shifts from staged A to consumed B."}
            ]
            with self.assertRaises(_InjectedProcessCrash):
                _run_direct_staged_ordinary(
                    adapter,
                    payload,
                    crash_point="before_completion_marker",
                )
            self.assertEqual(used_threads, ["b" * 64])
            self.assertEqual(rotation.active, "b" * 64)

            with self.assertRaises(PlannerResultUnavailableError) as unavailable:
                adapter.complete(payload)
            self.assertEqual(
                unavailable.exception.disposition.thread_disposition,
                "planner_evidence_invalid_never_resume",
            )
            self.assertEqual(rotation.completed_abandonments, ["b" * 64])
            self.assertEqual(ledger.dispatched_call_count, 1)
            with self.assertRaises(PlannerResultUnavailableError):
                adapter.complete(payload)

            different = _payload("chat-alpha")
            different["messages"] = [
                {"role": "user", "content": "Different prompt after shifted thread cleanup."}
            ]
            self.assertIsInstance(adapter.complete(different), dict)
            self.assertEqual(ledger.dispatched_call_count, 2)
            self.assertEqual(len(used_threads), 2)
            self.assertNotEqual(used_threads[1], "b" * 64)

    def test_restart_prefix_mismatch_redacts_and_blocks_branch(self) -> None:
        short_root = Path("D:/Cera/tmp")
        short_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=short_root) as temporary:
            root = Path(temporary)
            coordinator, _, planner = _runtime(
                root,
                _SemanticValidator(SemanticVerdict.PASS),
            )
            ledger = ContinuousProviderCallLedger((root / "sol.jsonl").resolve())
            rotation = _FakeThreadRotation()
            ledger.execute(
                owner="planner",
                operation="prior_plan",
                route="test",
                model="gpt-5.6-sol",
                effort="xhigh",
                dispatch=lambda: {"prior": True},
                finalize=lambda value: value,
                stored_thread_sha256=rotation.active,
            )
            _install_successful_ledger_planner(planner, ledger, rotation)
            tamper = {"enabled": False}

            def ledger_snapshot() -> PiSceneProviderLedgerSnapshotV1:
                snapshot = _live_ledger_snapshot(ledger)
                if not tamper["enabled"]:
                    return snapshot
                events = [dict(value) for value in snapshot.events]
                events[0]["operation"] = "tampered_prior_plan"
                return PiSceneProviderLedgerSnapshotV1(
                    schema_version=PiSceneProviderLedgerSnapshotV1.SCHEMA_VERSION,
                    dispatched_call_count=provider_ledger_dispatched_call_count(events),
                    events=tuple(events),
                )

            adapter = PiSceneHttpAdapter(
                coordinator=coordinator,
                session_id="chat-alpha",
                context_provider=lambda _route, source, _messages: turn(source=source),
                transport_provider_ledger_snapshot=ledger_snapshot,
                **rotation.adapter_kwargs(),
            )
            payload = _payload("chat-alpha")
            payload["messages"] = [
                {"role": "user", "content": "Prefix mismatch after consumed Planner result."}
            ]
            with self.assertRaises(_InjectedProcessCrash):
                _run_direct_staged_ordinary(
                    adapter,
                    payload,
                    crash_point="before_completion_marker",
                )
            tamper["enabled"] = True
            with self.assertRaises(PlannerResultUnavailableError) as unavailable:
                adapter.complete(payload)
            self.assertEqual(
                unavailable.exception.disposition.thread_disposition,
                "planner_evidence_invalid_blocked",
            )
            self.assertTrue(unavailable.exception.disposition.thread_retired)
            self.assertTrue(unavailable.exception.disposition.branch_dispatch_blocked)
            self.assertEqual(rotation.completed_abandonments, ["a" * 64])
            self.assertEqual(tuple(root.rglob("*.request.json")), ())

            tamper["enabled"] = False
            different = _payload("chat-alpha")
            different["messages"] = [
                {"role": "user", "content": "Different prompt on blocked branch."}
            ]
            with self.assertRaises(RequestReplayPendingError):
                adapter.complete(different)
            self.assertEqual(ledger.dispatched_call_count, 2)


class PiSceneTransportRetryReinitializationTests(unittest.TestCase):
    def test_interrupted_thread_is_archived_and_new_binding_can_be_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = PlannerThreadStateStore(Path(temporary))
            prior = PlannerThreadStateV1(
                "chat-alpha",
                "xhigh",
                "a" * 64,
                "thread-interrupted",
            )
            store.persist(prior)
            store.archive_interrupted_transport_thread(prior)
            self.assertIsNone(
                store.load(
                    session_id="chat-alpha",
                    reasoning_effort="xhigh",
                    compatibility_sha256="a" * 64,
                )
            )
            archive = tuple(
                value
                for value in Path(temporary).rglob("INTERRUPTED_TRANSPORT_THREADS/thread-*.json")
                if not value.name.endswith(".receipt.json")
            )
            self.assertEqual(len(archive), 1)
            receipt_path = archive[0].with_name(
                archive[0].name.removesuffix(".json") + ".receipt.json"
            )
            receipt_path.unlink()
            self.assertIsNone(
                store.load(
                    session_id="chat-alpha",
                    reasoning_effort="xhigh",
                    compatibility_sha256="a" * 64,
                )
            )
            self.assertTrue(receipt_path.is_file())
            with self.assertRaisesRegex(StateConflictError, "interrupted"):
                store.persist(prior)
            replacement = PlannerThreadStateV1(
                "chat-alpha",
                "xhigh",
                "a" * 64,
                "thread-fresh",
            )
            store.persist(replacement)
            self.assertEqual(
                store.load(
                    session_id="chat-alpha",
                    reasoning_effort="xhigh",
                    compatibility_sha256="a" * 64,
                ),
                replacement,
            )

    def test_launcher_registry_reset_is_local_and_does_not_plan(self) -> None:
        class Planner:
            def __init__(self) -> None:
                self.reset_calls = 0
                self.plan_calls = 0

            def plan(self, _request):
                self.plan_calls += 1
                raise AssertionError("provider work is prohibited during reset")

            def reset_provider_thread_after_transport_failure(
                self,
                _expected_thread_sha256: str,
            ) -> None:
                self.reset_calls += 1

        planner = Planner()
        registry = _PiScenePlannerRegistry(lambda _session, _effort, _turn: planner)
        retry_turn = replace(
            turn(),
            request_controls=controls("chat-alpha", effort="xhigh"),
        )
        registry.reset_after_transport_failure(retry_turn, "a" * 64)
        self.assertEqual(planner.reset_calls, 1)
        self.assertEqual(planner.plan_calls, 0)


if __name__ == "__main__":
    unittest.main()
