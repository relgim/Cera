"""Manual Planner transport-Retry controller and closed HTTP projections.

The controller receives every runtime dependency explicitly.  It never imports
the HTTP adapter, owns no server authentication or provider mutex, and assumes
the adapter has acquired that mutex before a POST dispatch.  Durable claims and
transitions remain owned by :class:`PiSceneRequestJournal`.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, NoReturn, Protocol
from urllib.parse import unquote

from cera.errors import ContractValidationError, ErrorCode, StateConflictError
from cera.serialization import canonical_sha256, re_is_sha256, to_primitive

from .http_contracts import PiSceneChatRequestV1
from .request_binding import PiSceneRequestBindingV1
from .request_journal import (
    PiSceneRequestJournal,
    PlannerResultUnavailableError,
    RequestJournalResolutionV1,
    RequestReplayPendingError,
)
from .review_store import LeanSceneTurnInputV1
from .transport_retry import (
    PiScenePlannerCompletionMarkerV1,
    PiScenePlannerResultUnavailableV1,
    PiSceneProviderLedgerSnapshotV1,
    PiSceneTransportEffectSnapshotV1,
    PiSceneZeroEffectProofV1,
    TransportFailureReceiptV1,
    build_provider_failure_evidence,
    classify_planner_call_events,
    effect_snapshot_from_payload,
    planner_completion_marker_from_payload,
    provider_failure_detail_sha256,
    provider_ledger_dispatched_call_count,
)
from .transport_retry_store import (
    TransportRetryAuthorizationV1,
    TransportRetryNotFoundError,
)

TransportRetryReinitializer = Callable[
    [PiSceneRequestBindingV1, LeanSceneTurnInputV1, str, str],
    None,
]
TransportProviderLedgerSnapshot = Callable[[], PiSceneProviderLedgerSnapshotV1]
TransportRetryActiveThreadSnapshot = Callable[
    [PiSceneRequestBindingV1, LeanSceneTurnInputV1, str],
    str | None,
]
TransportRetryFreshThreadInitializer = Callable[
    [PiSceneRequestBindingV1, LeanSceneTurnInputV1, str],
    str,
]
TransportCompletedPlannerAbandoner = Callable[
    [PiSceneRequestBindingV1, LeanSceneTurnInputV1, str, str],
    None,
]
RecoverCompletedPlannerProgress = Callable[
    [
        PiSceneRequestBindingV1,
        PiSceneChatRequestV1,
        LeanSceneTurnInputV1,
        PiScenePlannerCompletionMarkerV1,
    ],
    dict[str, Any] | None,
]
RecoverProgressedRequest = Callable[
    [PiSceneRequestBindingV1, Mapping[str, Any]],
    dict[str, Any],
]
PrepareBoundRequest = Callable[
    [Mapping[str, Any]],
    tuple[PiSceneChatRequestV1, LeanSceneTurnInputV1, PiSceneRequestBindingV1],
]
TransportEffectSnapshot = Callable[
    [LeanSceneTurnInputV1],
    PiSceneTransportEffectSnapshotV1,
]


class RunBoundRequest(Protocol):
    def __call__(
        self,
        *,
        payload: Mapping[str, Any],
        request: PiSceneChatRequestV1,
        turn: LeanSceneTurnInputV1,
        binding: PiSceneRequestBindingV1,
        resolution: RequestJournalResolutionV1 | None,
        provider_ledger_before: PiSceneProviderLedgerSnapshotV1 | None = None,
    ) -> dict[str, Any]: ...


class CompleteBoundRequest(Protocol):
    def __call__(
        self,
        *,
        payload: Mapping[str, Any],
        request: PiSceneChatRequestV1,
        turn: LeanSceneTurnInputV1,
        binding: PiSceneRequestBindingV1,
        resolution: RequestJournalResolutionV1 | None,
    ) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class TransportRetryHttpDependencies:
    """Explicit adapter seams used by the route-neutral HTTP controller."""

    journal: PiSceneRequestJournal
    prepare_bound_request: PrepareBoundRequest
    run_bound_request: RunBoundRequest
    complete_bound_request: CompleteBoundRequest
    effect_snapshot: TransportEffectSnapshot
    provider_ledger_snapshot: TransportProviderLedgerSnapshot
    reinitializer: TransportRetryReinitializer | None
    active_thread_snapshot: TransportRetryActiveThreadSnapshot | None
    fresh_thread_initializer: TransportRetryFreshThreadInitializer | None
    completed_planner_abandoner: TransportCompletedPlannerAbandoner | None
    recover_completed_planner_progress: RecoverCompletedPlannerProgress
    recover_progressed_request: RecoverProgressedRequest


class PiSceneManualTransportRetryError(RuntimeError):
    """Safe public projection of one durable, zero-effect provider failure."""

    def __init__(self, receipt: TransportFailureReceiptV1) -> None:
        self.receipt = receipt
        super().__init__("CERA provider transport failed with zero story-state effect")


class TransportRetryHttpController:
    """Coordinate one Planner-only Retry through injected adapter seams."""

    def __init__(self, dependencies: TransportRetryHttpDependencies) -> None:
        self.dependencies = dependencies

    def retry_claimed(self, retry_id: str) -> dict[str, Any]:
        """Dispatch after the adapter holds provider and journal claim scopes."""

        journal = self.dependencies.journal
        prepared = journal.prepare_transport_retry(retry_id)
        terminal = terminal_or_superseding_retry(prepared)
        if terminal is not None:
            return terminal
        if prepared.action_phase == "blocked":
            raise StateConflictError("Pi Scene transport retry is permanently blocked")
        if prepared.request_entry_status == "progressed":
            return self._recover_progressed(prepared)
        if prepared.action_phase == "dispatch_started":
            successor = self._reconcile_interrupted_dispatch(prepared)
            if successor is not None:
                raise PiSceneManualTransportRetryError(successor) from None
            refreshed = journal.prepare_transport_retry(retry_id)
            if refreshed.action_phase in {"eligible", "owner_rotated"}:
                prepared = refreshed
            else:
                raise StateConflictError("Pi Scene interrupted retry dispatch is blocked")

        self._validate_context(prepared)
        authorization = journal.authorize_transport_retry(retry_id)
        terminal = terminal_or_superseding_retry(authorization)
        if terminal is not None:
            return terminal
        payload, request, turn, binding, _ = self._validate_context(authorization)
        reinitializer = self.dependencies.reinitializer
        if reinitializer is None:
            journal.block_transport_retry(retry_id, "owner_rotation_failed")
            raise StateConflictError("Pi Scene transport retry reinitializer is unavailable")
        if authorization.action_phase == "authorized":
            try:
                reinitializer(
                    binding,
                    turn,
                    authorization.logic_owner,
                    authorization.effect_proof.provider_failure_evidence.stored_thread_sha256,
                )
            except Exception:
                journal.block_transport_retry(retry_id, "owner_rotation_failed")
                raise
            journal.mark_transport_owner_rotated(retry_id)
        elif authorization.action_phase != "owner_rotated":
            raise StateConflictError("Pi Scene transport retry phase is not dispatchable")
        rotated = journal.prepare_transport_retry(retry_id)
        fresh_thread_sha256 = rotated.fresh_thread_sha256
        if fresh_thread_sha256 is None:
            initializer = self.dependencies.fresh_thread_initializer
            if initializer is None:
                journal.block_transport_retry(retry_id, "owner_rotation_failed")
                raise StateConflictError(
                    "Pi Scene transport retry lacks a fresh-thread initializer"
                )
            # Starting an empty retained thread may use provider lifecycle
            # transport, but it performs no Sol/model turn and appends no
            # provider-call-ledger operation.
            fresh_thread_sha256 = initializer(
                binding,
                turn,
                authorization.logic_owner,
            )
            journal.bind_transport_fresh_thread(retry_id, fresh_thread_sha256)
        snapshot = self.dependencies.active_thread_snapshot
        if (
            snapshot is None
            or snapshot(binding, turn, authorization.logic_owner) != fresh_thread_sha256
        ):
            journal.block_transport_retry(retry_id, "owner_rotation_failed")
            raise StateConflictError("Pi Scene fresh Planner thread custody could not be verified")
        provider_ledger_before = self.dependencies.provider_ledger_snapshot()
        journal.mark_transport_dispatch_started(retry_id, provider_ledger_before)
        try:
            return self.dependencies.run_bound_request(
                payload=payload,
                request=request,
                turn=turn,
                binding=binding,
                resolution=RequestJournalResolutionV1(
                    request_id=binding.request_id,
                    replayed=False,
                    terminal_response=None,
                    review_progress=None,
                ),
                provider_ledger_before=provider_ledger_before,
            )
        except PiSceneManualTransportRetryError:
            # The bound request has already frozen the successor failure and
            # terminalized this predecessor action.
            raise
        except Exception as exc:
            self._block_interrupted(
                retry_id=retry_id,
                binding=binding,
                turn=turn,
                failure=exc,
            )
            raise

    def status(self, retry_id: str) -> dict[str, Any]:
        """Reconcile and project one authenticated Retry without dispatching."""

        journal = self.dependencies.journal
        authorization = journal.prepare_transport_retry(retry_id)
        if authorization.replayed_terminal_response is not None:
            return succeeded_retry_status(authorization)
        if authorization.superseding_failure is not None:
            return superseded_retry_status(
                authorization,
                authorization.superseding_failure,
            )
        if authorization.action_phase == "blocked":
            return blocked_retry_status(authorization)
        if authorization.action_phase in {
            "authorized",
            "owner_rotated",
            "dispatch_started",
        } and journal.transport_dispatch_in_progress(retry_id):
            return in_progress_retry_status(
                authorization,
                phase=authorization.action_phase,
            )
        if authorization.request_entry_status == "progressed":
            progress_changed = False
            completion: dict[str, Any] | None = None
            try:
                with journal.transport_dispatch_claim(retry_id):
                    with journal.provider_dispatch_claim():
                        refreshed_progress = journal.prepare_transport_retry(retry_id)
                        if refreshed_progress.request_entry_status != "progressed":
                            progress_changed = True
                        else:
                            completion = self._recover_progressed(refreshed_progress)
            except RequestReplayPendingError:
                return in_progress_retry_status(
                    authorization,
                    phase="dispatch_started",
                )
            if progress_changed:
                return self.status(retry_id)
            assert completion is not None
            return succeeded_retry_status(authorization, completion=completion)
        if authorization.action_phase == "dispatch_started":
            dispatch_changed = False
            successor: TransportFailureReceiptV1 | None = None
            try:
                with journal.transport_dispatch_claim(retry_id):
                    with journal.provider_dispatch_claim():
                        refreshed = journal.prepare_transport_retry(retry_id)
                        if refreshed.action_phase != "dispatch_started":
                            dispatch_changed = True
                        else:
                            successor = self._reconcile_interrupted_dispatch(refreshed)
            except RequestReplayPendingError:
                return in_progress_retry_status(
                    authorization,
                    phase=authorization.action_phase,
                )
            if dispatch_changed:
                return self.status(retry_id)
            refreshed = journal.prepare_transport_retry(retry_id)
            if successor is not None or refreshed.superseding_failure is not None:
                next_receipt = successor or refreshed.superseding_failure
                assert next_receipt is not None
                return superseded_retry_status(authorization, next_receipt)
            return self.status(retry_id)
        try:
            self._validate_context(authorization)
        except StateConflictError:
            refreshed = journal.prepare_transport_retry(retry_id)
            if refreshed.action_phase != "blocked":
                raise
            return blocked_retry_status(
                refreshed,
                reason_code=(refreshed.blocked_reason_code or "route_or_context_changed"),
            )
        return eligible_retry_status(authorization)

    def close_staged_planner_dispatch(
        self,
        *,
        binding: PiSceneRequestBindingV1,
        turn: LeanSceneTurnInputV1,
    ) -> None:
        """Classify the exact Planner ledger terminal before downstream work."""

        journal = self.dependencies.journal
        staged = journal.staged_transport_dispatch(binding)
        if staged is None or staged["phase"] == "planner_completed_pending_progress":
            return
        capsule = staged["dispatch_capsule"]
        before = journal.provider_ledger_snapshot_from_capsule(capsule)
        current = self.dependencies.provider_ledger_snapshot()
        try:
            thread_sha256 = self._resolved_staged_thread(
                binding=binding,
                turn=turn,
                capsule=capsule,
                ledger=current,
                baseline_event_count=before.event_count,
            )
        except StateConflictError:
            thread_sha256 = self._invalid_evidence_retirement_thread(
                binding=binding,
                turn=turn,
                capsule=capsule,
                ledger=current,
                baseline_event_count=before.event_count,
            )
            if thread_sha256 is not None:
                self._retire_invalid_planner_evidence(
                    binding=binding,
                    turn=turn,
                    capsule=capsule,
                    before=before,
                    after=current,
                    stored_thread_sha256=thread_sha256,
                )
            raise
        if thread_sha256 is None:
            if current.event_count == before.event_count:
                journal.discard_staged_transport_dispatch(binding)
                return
            thread_sha256 = self._invalid_evidence_retirement_thread(
                binding=binding,
                turn=turn,
                capsule=capsule,
                ledger=current,
                baseline_event_count=before.event_count,
            )
            if thread_sha256 is None:
                raise StateConflictError("Pi Scene Planner call lacks retained-thread identity")
            self._retire_invalid_planner_evidence(
                binding=binding,
                turn=turn,
                capsule=capsule,
                before=before,
                after=current,
                stored_thread_sha256=thread_sha256,
            )
        try:
            selected = self._provider_call_span_for_thread(
                current,
                baseline_event_count=before.event_count,
                stored_thread_sha256=thread_sha256,
            )
        except StateConflictError:
            self._retire_invalid_planner_evidence(
                binding=binding,
                turn=turn,
                capsule=capsule,
                before=before,
                after=current,
                stored_thread_sha256=thread_sha256,
            )
        if selected is None:
            if current.event_count == before.event_count:
                journal.discard_staged_transport_dispatch(binding)
                return
            self._retire_invalid_planner_evidence(
                binding=binding,
                turn=turn,
                capsule=capsule,
                before=before,
                after=current,
                stored_thread_sha256=thread_sha256,
            )
        exact_before, exact_after, appended = selected
        try:
            disposition = classify_planner_call_events(
                appended,
                stored_thread_sha256=thread_sha256,
            )
        except ContractValidationError:
            self._retire_invalid_planner_evidence(
                binding=binding,
                turn=turn,
                capsule=capsule,
                before=exact_before,
                after=exact_after,
                stored_thread_sha256=thread_sha256,
            )
        if disposition == "pretransport_failed":
            # A closed local failure proves that no model operation ran.  The
            # original exception remains authoritative and the retained
            # Planner thread is safe to keep.
            journal.discard_staged_transport_dispatch(binding)
            return
        marker = self._completion_marker(
            binding=binding,
            capsule=capsule,
            before=exact_before,
            after=exact_after,
            call_events=appended,
        )
        if disposition == "typed_accepted":
            journal.mark_planner_completed_pending_progress(binding, marker)
            return
        self._abandon_completed_result(
            binding=binding,
            turn=turn,
            completion=marker,
        )

    def _resolved_staged_thread(
        self,
        *,
        binding: PiSceneRequestBindingV1,
        turn: LeanSceneTurnInputV1,
        capsule: Mapping[str, Any],
        ledger: PiSceneProviderLedgerSnapshotV1,
        baseline_event_count: int,
    ) -> str | None:
        expected = capsule["planner_thread_sha256"]
        if expected is not None and not isinstance(expected, str):
            raise StateConflictError("Pi Scene staged Planner thread custody changed")
        snapshot = self.dependencies.active_thread_snapshot
        active = None if snapshot is None else snapshot(binding, turn, "planner")
        if expected is not None:
            if active is not None and active != expected:
                raise StateConflictError("Pi Scene staged Planner thread custody changed")
            return expected
        if active is not None:
            return active
        suffix = ledger.events[baseline_event_count:]
        threads = {
            value.get("stored_thread_sha256")
            for value in suffix
            if value.get("owner") == "planner"
            and isinstance(value.get("stored_thread_sha256"), str)
        }
        if len(threads) == 1:
            value = next(iter(threads))
            assert isinstance(value, str)
            return value
        return None

    def _invalid_evidence_retirement_thread(
        self,
        *,
        binding: PiSceneRequestBindingV1,
        turn: LeanSceneTurnInputV1,
        capsule: Mapping[str, Any],
        ledger: PiSceneProviderLedgerSnapshotV1,
        baseline_event_count: int,
    ) -> str | None:
        """Select only a locally known retained thread for fail-closed retirement."""

        snapshot = self.dependencies.active_thread_snapshot
        active = None if snapshot is None else snapshot(binding, turn, "planner")
        if isinstance(active, str) and re_is_sha256(active):
            return active
        expected = capsule.get("planner_thread_sha256")
        if isinstance(expected, str) and re_is_sha256(expected):
            return expected
        suffix_threads = {
            value.get("stored_thread_sha256")
            for value in ledger.events[baseline_event_count:]
            if isinstance(value.get("stored_thread_sha256"), str)
            and re_is_sha256(str(value["stored_thread_sha256"]))
        }
        if len(suffix_threads) == 1:
            selected = next(iter(suffix_threads))
            assert isinstance(selected, str)
            return selected
        return None

    @staticmethod
    def _completion_marker(
        *,
        binding: PiSceneRequestBindingV1,
        capsule: Mapping[str, Any],
        before: PiSceneProviderLedgerSnapshotV1,
        after: PiSceneProviderLedgerSnapshotV1,
        call_events: tuple[Mapping[str, Any], ...],
    ) -> PiScenePlannerCompletionMarkerV1:
        if not call_events or (
            after.event_count != before.event_count + len(call_events)
            or tuple(after.events[: before.event_count]) != before.events
            or tuple(after.events[before.event_count :]) != call_events
            or before.dispatched_call_count != provider_ledger_dispatched_call_count(before.events)
            or after.dispatched_call_count != provider_ledger_dispatched_call_count(after.events)
        ):
            raise StateConflictError("Pi Scene Planner completion ledger prefix changed")
        call_id = call_events[0].get("call_id")
        thread_sha256 = call_events[0].get("stored_thread_sha256")
        if not isinstance(call_id, str) or not isinstance(thread_sha256, str):
            raise StateConflictError("Pi Scene Planner completion call identity changed")
        try:
            classified = classify_planner_call_events(
                call_events,
                call_id=call_id,
                stored_thread_sha256=thread_sha256,
            )
        except ContractValidationError as exc:
            raise StateConflictError("Pi Scene Planner completion evidence changed") from exc
        if classified == "pretransport_failed":
            raise StateConflictError("Pi Scene local Planner failure is not a completed result")
        terminal = "dispatch_outcome_ambiguous" if classified == "provider_failed" else classified
        return PiScenePlannerCompletionMarkerV1(
            schema_version=PiScenePlannerCompletionMarkerV1.SCHEMA_VERSION,
            request_id=binding.request_id,
            resolved_route=str(capsule["resolved_route"]),
            turn_context_sha256=str(capsule["turn_context_sha256"]),
            effect_before=effect_snapshot_from_payload(capsule["effect_before"]),
            call_id=call_id,
            stored_thread_sha256=thread_sha256,
            terminal_state=str(terminal),
            before_event_count=before.event_count,
            before_events_sha256=before.events_sha256,
            after_event_count=after.event_count,
            after_events_sha256=after.events_sha256,
            dispatched_calls_before=before.dispatched_call_count,
            dispatched_calls_after=after.dispatched_call_count,
            call_events_sha256=canonical_sha256(tuple(dict(value) for value in call_events)),
        )

    @staticmethod
    def _validate_completion_marker(
        completion: PiScenePlannerCompletionMarkerV1,
        ledger: PiSceneProviderLedgerSnapshotV1,
    ) -> None:
        if (
            completion.after_event_count > ledger.event_count
            or completion.before_event_count >= completion.after_event_count
        ):
            raise StateConflictError("Pi Scene Planner completion span left the ledger")
        before_events = tuple(ledger.events[: completion.before_event_count])
        after_events = tuple(ledger.events[: completion.after_event_count])
        call_events = tuple(
            ledger.events[completion.before_event_count : completion.after_event_count]
        )
        if (
            canonical_sha256(tuple(dict(value) for value in before_events))
            != completion.before_events_sha256
            or canonical_sha256(tuple(dict(value) for value in after_events))
            != completion.after_events_sha256
            or canonical_sha256(tuple(dict(value) for value in call_events))
            != completion.call_events_sha256
            or provider_ledger_dispatched_call_count(before_events)
            != completion.dispatched_calls_before
            or provider_ledger_dispatched_call_count(after_events)
            != completion.dispatched_calls_after
            or any(
                value.get("call_id") == completion.call_id
                for value in ledger.events[completion.after_event_count :]
            )
        ):
            raise StateConflictError("Pi Scene Planner completion prefix changed")
        try:
            classified = classify_planner_call_events(
                call_events,
                call_id=completion.call_id,
                stored_thread_sha256=completion.stored_thread_sha256,
            )
        except ContractValidationError as exc:
            raise StateConflictError("Pi Scene Planner completion call changed") from exc
        expected_terminal = (
            "dispatch_outcome_ambiguous" if classified == "provider_failed" else classified
        )
        if expected_terminal != completion.terminal_state:
            raise StateConflictError("Pi Scene Planner completion terminal changed")

    @staticmethod
    def _invalid_evidence_marker(
        *,
        binding: PiSceneRequestBindingV1,
        capsule: Mapping[str, Any],
        before: PiSceneProviderLedgerSnapshotV1,
        after: PiSceneProviderLedgerSnapshotV1,
        stored_thread_sha256: str,
    ) -> PiScenePlannerCompletionMarkerV1:
        """Hash-bind malformed consumed-call evidence without trusting its graph."""

        if (
            canonical_sha256(tuple(dict(value) for value in before.events)) != before.events_sha256
            or before.dispatched_call_count != provider_ledger_dispatched_call_count(before.events)
        ):
            raise StateConflictError("Pi Scene invalid Planner evidence baseline is invalid")
        prefix_matches = (
            before.event_count <= after.event_count
            and tuple(after.events[: before.event_count]) == before.events
        )
        observed_suffix = tuple(
            after.events[before.event_count :] if prefix_matches else after.events
        )
        projected_events = tuple(
            {**dict(value), "event_index": index}
            for index, value in enumerate(before.events + observed_suffix, start=1)
        )
        selected_after: PiSceneProviderLedgerSnapshotV1 | None = None
        for end in range(before.event_count + 1, len(projected_events) + 1):
            events = tuple(projected_events[:end])
            suffix = events[before.event_count :]
            if (
                suffix
                and provider_ledger_dispatched_call_count(events)
                == before.dispatched_call_count + 1
            ):
                selected_after = PiSceneProviderLedgerSnapshotV1(
                    schema_version=PiSceneProviderLedgerSnapshotV1.SCHEMA_VERSION,
                    dispatched_call_count=before.dispatched_call_count + 1,
                    events=events,
                )
        if selected_after is None:
            raise StateConflictError("Pi Scene invalid Planner evidence cannot be bounded")
        call_events = tuple(selected_after.events[before.event_count :])
        if not call_events or not re_is_sha256(stored_thread_sha256):
            raise StateConflictError("Pi Scene invalid Planner evidence cannot be bounded")
        call_ids = {
            value.get("call_id")
            for value in call_events
            if value.get("stored_thread_sha256") == stored_thread_sha256
            and isinstance(value.get("call_id"), str)
        }
        evidence_hash = canonical_sha256(
            {
                "schema_version": "cera.pi_scene.invalid_planner_evidence.v1",
                "prefix_matches": prefix_matches,
                "before": {
                    "schema_version": before.schema_version,
                    "dispatched_call_count": before.dispatched_call_count,
                    "events": tuple(dict(value) for value in before.events),
                },
                "observed_after": {
                    "schema_version": after.schema_version,
                    "dispatched_call_count": after.dispatched_call_count,
                    "events": tuple(dict(value) for value in after.events),
                },
                "projected_call_events": tuple(dict(value) for value in call_events),
            }
        )
        call_id = (
            next(iter(call_ids)) if len(call_ids) == 1 else "invalid_call_" + evidence_hash[:24]
        )
        assert isinstance(call_id, str)
        return PiScenePlannerCompletionMarkerV1(
            schema_version=PiScenePlannerCompletionMarkerV1.SCHEMA_VERSION,
            request_id=binding.request_id,
            resolved_route=str(capsule["resolved_route"]),
            turn_context_sha256=str(capsule["turn_context_sha256"]),
            effect_before=effect_snapshot_from_payload(capsule["effect_before"]),
            call_id=call_id,
            stored_thread_sha256=stored_thread_sha256,
            terminal_state="dispatch_outcome_ambiguous",
            before_event_count=before.event_count,
            before_events_sha256=before.events_sha256,
            after_event_count=selected_after.event_count,
            after_events_sha256=selected_after.events_sha256,
            dispatched_calls_before=before.dispatched_call_count,
            dispatched_calls_after=selected_after.dispatched_call_count,
            call_events_sha256=evidence_hash,
        )

    def _retire_invalid_planner_evidence(
        self,
        *,
        binding: PiSceneRequestBindingV1,
        turn: LeanSceneTurnInputV1,
        capsule: Mapping[str, Any],
        before: PiSceneProviderLedgerSnapshotV1,
        after: PiSceneProviderLedgerSnapshotV1,
        stored_thread_sha256: str,
    ) -> NoReturn:
        """Retire a potentially consumed thread when its call graph is malformed."""

        marker = self._invalid_evidence_marker(
            binding=binding,
            capsule=capsule,
            before=before,
            after=after,
            stored_thread_sha256=stored_thread_sha256,
        )
        self._abandon_completed_result(
            binding=binding,
            turn=turn,
            completion=marker,
            invalid_evidence=True,
        )
        raise AssertionError("Planner evidence retirement must terminate the request")

    def _block_invalid_planner_evidence(
        self,
        *,
        binding: PiSceneRequestBindingV1,
        turn: LeanSceneTurnInputV1,
        capsule: Mapping[str, Any],
        before: PiSceneProviderLedgerSnapshotV1,
        after: PiSceneProviderLedgerSnapshotV1,
        stored_thread_sha256: str,
    ) -> NoReturn:
        """Redact and block when corrupted evidence cannot authorize branch progress."""

        marker = self._invalid_evidence_marker(
            binding=binding,
            capsule=capsule,
            before=before,
            after=after,
            stored_thread_sha256=stored_thread_sha256,
        )
        retired = False
        abandon = self.dependencies.completed_planner_abandoner
        try:
            if abandon is None:
                raise StateConflictError("Pi Scene completed Planner abandoner is unavailable")
            abandon(binding, turn, "planner", stored_thread_sha256)
            snapshot = self.dependencies.active_thread_snapshot
            if snapshot is not None and snapshot(binding, turn, "planner") == stored_thread_sha256:
                raise StateConflictError("Pi Scene invalid Planner thread remained active")
            retired = True
        except Exception:
            retired = False
        disposition = PiScenePlannerResultUnavailableV1(
            schema_version=PiScenePlannerResultUnavailableV1.SCHEMA_VERSION,
            completion=marker,
            thread_disposition="planner_evidence_invalid_blocked",
            thread_retired=retired,
            branch_dispatch_blocked=True,
        )
        self.dependencies.journal.mark_planner_result_unavailable(binding, disposition)
        raise PlannerResultUnavailableError(disposition)

    def _abandon_completed_result(
        self,
        *,
        binding: PiSceneRequestBindingV1,
        turn: LeanSceneTurnInputV1,
        completion: PiScenePlannerCompletionMarkerV1,
        invalid_evidence: bool = False,
    ) -> None:
        journal = self.dependencies.journal
        retired = False
        abandon = self.dependencies.completed_planner_abandoner
        try:
            if abandon is None:
                raise StateConflictError("Pi Scene completed Planner abandoner is unavailable")
            abandon(
                binding,
                turn,
                "planner",
                completion.stored_thread_sha256,
            )
            snapshot = self.dependencies.active_thread_snapshot
            if snapshot is not None and snapshot(binding, turn, "planner") == (
                completion.stored_thread_sha256
            ):
                raise StateConflictError("Pi Scene completed Planner thread remained active")
            retired = True
        except Exception:
            retired = False
        disposition = PiScenePlannerResultUnavailableV1(
            schema_version=PiScenePlannerResultUnavailableV1.SCHEMA_VERSION,
            completion=completion,
            thread_disposition=(
                (
                    "planner_evidence_invalid_never_resume"
                    if invalid_evidence
                    else "completed_uncommitted_never_resume"
                )
                if retired
                else (
                    "planner_evidence_invalid_retirement_failed"
                    if invalid_evidence
                    else "completed_uncommitted_retirement_failed"
                )
            ),
            thread_retired=retired,
            branch_dispatch_blocked=not retired,
        )
        journal.mark_planner_result_unavailable(binding, disposition)
        raise PlannerResultUnavailableError(disposition)

    def _block_unrecognized_completed_progress(
        self,
        *,
        binding: PiSceneRequestBindingV1,
        completion: PiScenePlannerCompletionMarkerV1,
    ) -> None:
        """Redact raw custody while preserving an unrecognized durable effect."""

        disposition = PiScenePlannerResultUnavailableV1(
            schema_version=PiScenePlannerResultUnavailableV1.SCHEMA_VERSION,
            completion=completion,
            thread_disposition="durable_progress_unrecognized",
            thread_retired=False,
            branch_dispatch_blocked=True,
        )
        self.dependencies.journal.mark_planner_result_unavailable(binding, disposition)
        raise PlannerResultUnavailableError(disposition)

    def _recover_or_dispose_completed_result(
        self,
        *,
        binding: PiSceneRequestBindingV1,
        request: PiSceneChatRequestV1,
        turn: LeanSceneTurnInputV1,
        completion: PiScenePlannerCompletionMarkerV1,
    ) -> dict[str, Any] | None:
        """Recover exact durable progress before considering thread retirement."""

        recovered = self.dependencies.recover_completed_planner_progress(
            binding,
            request,
            turn,
            completion,
        )
        if recovered is not None:
            return recovered
        if (
            request.route.value != completion.resolved_route
            or canonical_sha256(turn) != completion.turn_context_sha256
            or self.dependencies.effect_snapshot(turn) != completion.effect_before
        ):
            self._block_unrecognized_completed_progress(
                binding=binding,
                completion=completion,
            )
        self._abandon_completed_result(
            binding=binding,
            turn=turn,
            completion=completion,
        )
        return None

    def recover_staged_planner_failure(
        self,
        *,
        binding: PiSceneRequestBindingV1,
        payload: Mapping[str, Any],
        request: PiSceneChatRequestV1,
        turn: LeanSceneTurnInputV1,
    ) -> TransportFailureReceiptV1 | RequestJournalResolutionV1 | dict[str, Any] | None:
        """Promote a crash-surviving Planner capsule without redispatch."""

        journal = self.dependencies.journal
        staged = journal.staged_transport_dispatch(binding)
        if staged is None:
            return None
        capsule = staged["dispatch_capsule"]
        if staged["phase"] == "planner_completed_pending_progress":
            completion = planner_completion_marker_from_payload(capsule)
            if completion.request_id != binding.request_id:
                raise StateConflictError("Pi Scene completed Planner marker changed custody")
            self._validate_completion_marker(
                completion,
                self.dependencies.provider_ledger_snapshot(),
            )
            return self._recover_or_dispose_completed_result(
                binding=binding,
                request=request,
                turn=turn,
                completion=completion,
            )
        if staged["normalized_request"] != to_primitive(payload):
            raise StateConflictError("Pi Scene staged Planner dispatch changed request custody")
        before_effect = effect_snapshot_from_payload(capsule["effect_before"])
        before_ledger = journal.provider_ledger_snapshot_from_capsule(capsule)
        after_ledger = self.dependencies.provider_ledger_snapshot()
        if (
            after_ledger.event_count < before_ledger.event_count
            or tuple(after_ledger.events[: before_ledger.event_count]) != before_ledger.events
            or before_ledger.dispatched_call_count
            != provider_ledger_dispatched_call_count(before_ledger.events)
            or after_ledger.dispatched_call_count
            != provider_ledger_dispatched_call_count(after_ledger.events)
        ):
            invalid_thread = self._invalid_evidence_retirement_thread(
                binding=binding,
                turn=turn,
                capsule=capsule,
                ledger=after_ledger,
                baseline_event_count=before_ledger.event_count,
            )
            if invalid_thread is not None:
                self._block_invalid_planner_evidence(
                    binding=binding,
                    turn=turn,
                    capsule=capsule,
                    before=before_ledger,
                    after=after_ledger,
                    stored_thread_sha256=invalid_thread,
                )
            raise StateConflictError("Pi Scene staged Planner dispatch lost its Sol ledger prefix")
        try:
            planner_thread_sha256 = self._resolved_staged_thread(
                binding=binding,
                turn=turn,
                capsule=capsule,
                ledger=after_ledger,
                baseline_event_count=before_ledger.event_count,
            )
        except StateConflictError:
            planner_thread_sha256 = self._invalid_evidence_retirement_thread(
                binding=binding,
                turn=turn,
                capsule=capsule,
                ledger=after_ledger,
                baseline_event_count=before_ledger.event_count,
            )
            if planner_thread_sha256 is not None:
                self._retire_invalid_planner_evidence(
                    binding=binding,
                    turn=turn,
                    capsule=capsule,
                    before=before_ledger,
                    after=after_ledger,
                    stored_thread_sha256=planner_thread_sha256,
                )
            raise
        if planner_thread_sha256 is None:
            if after_ledger.event_count == before_ledger.event_count:
                return journal.rearm_staged_transport_dispatch_after_zero_call(
                    binding,
                    ledger_current=after_ledger,
                    planner_thread_sha256=None,
                )
            journal.discard_staged_transport_dispatch(binding)
            raise StateConflictError("Pi Scene staged Planner call cannot be attributed")
        try:
            selected = self._provider_call_span_for_thread(
                after_ledger,
                baseline_event_count=before_ledger.event_count,
                stored_thread_sha256=planner_thread_sha256,
            )
        except StateConflictError:
            self._retire_invalid_planner_evidence(
                binding=binding,
                turn=turn,
                capsule=capsule,
                before=before_ledger,
                after=after_ledger,
                stored_thread_sha256=planner_thread_sha256,
            )
        if selected is None:
            if after_ledger.event_count > before_ledger.event_count:
                self._retire_invalid_planner_evidence(
                    binding=binding,
                    turn=turn,
                    capsule=capsule,
                    before=before_ledger,
                    after=after_ledger,
                    stored_thread_sha256=planner_thread_sha256,
                )
            if (
                capsule["resolved_route"] != request.route.value
                or capsule["turn_context_sha256"] != canonical_sha256(turn)
                or self.dependencies.effect_snapshot(turn) != before_effect
            ):
                raise StateConflictError(
                    "Pi Scene staged Planner dispatch changed story-state custody"
                )
            return journal.rearm_staged_transport_dispatch_after_zero_call(
                binding,
                ledger_current=after_ledger,
                planner_thread_sha256=planner_thread_sha256,
            )
        exact_before, exact_after, appended = selected
        try:
            call_disposition = classify_planner_call_events(
                appended,
                stored_thread_sha256=planner_thread_sha256,
            )
        except ContractValidationError:
            self._retire_invalid_planner_evidence(
                binding=binding,
                turn=turn,
                capsule=capsule,
                before=exact_before,
                after=exact_after,
                stored_thread_sha256=planner_thread_sha256,
            )
        if call_disposition not in {"provider_failed", "pretransport_failed"}:
            completion = self._completion_marker(
                binding=binding,
                capsule=capsule,
                before=exact_before,
                after=exact_after,
                call_events=appended,
            )
            return self._recover_or_dispose_completed_result(
                binding=binding,
                request=request,
                turn=turn,
                completion=completion,
            )
        if (
            call_disposition == "pretransport_failed"
            and appended[-1].get("failure_type") != "ProviderTransportError"
        ):
            journal.discard_staged_transport_dispatch(binding)
            raise StateConflictError("Pi Scene Planner failed before provider transport")
        after_effect = self.dependencies.effect_snapshot(turn)
        if (
            capsule["resolved_route"] != request.route.value
            or capsule["turn_context_sha256"] != canonical_sha256(turn)
            or after_effect != before_effect
        ):
            if call_disposition == "provider_failed":
                completion = self._completion_marker(
                    binding=binding,
                    capsule=capsule,
                    before=exact_before,
                    after=exact_after,
                    call_events=appended,
                )
                self._block_unrecognized_completed_progress(
                    binding=binding,
                    completion=completion,
                )
            journal.discard_staged_transport_dispatch(binding)
            raise StateConflictError("Pi Scene staged Planner dispatch changed story-state custody")
        submitted = call_disposition == "provider_failed"
        evidence = build_provider_failure_evidence(
            before=exact_before,
            after=exact_after,
            provider_operation_submitted=submitted,
            provider_failure_detail_sha256=provider_failure_detail_sha256(
                None,
                call_events=appended,
            ),
        )
        proof = PiSceneZeroEffectProofV1(
            schema_version=PiSceneZeroEffectProofV1.SCHEMA_VERSION,
            request_id=binding.request_id,
            logic_owner="planner",
            resolved_route=request.route.value,
            turn_context_sha256=canonical_sha256(turn),
            before_snapshot=before_effect,
            after_snapshot=after_effect,
            provider_failure_evidence=evidence,
            candidate_effect_absent=True,
            review_effect_absent=True,
            accepted_effect_absent=True,
            recording_effect_absent=True,
            branch_head_effect_absent=True,
        )
        return journal.record_transport_failure(
            binding,
            normalized_request=payload,
            logic_owner="planner",
            provider_error_code=ErrorCode.PROVIDER_TRANSPORT_FAILED.value,
            provider_operations_observed=int(submitted),
            effect_proof=proof,
            provider_ledger_before=exact_before,
        )

    def _recover_progressed(
        self,
        authorization: TransportRetryAuthorizationV1,
    ) -> dict[str, Any]:
        binding = authorization.binding
        resolution = self.dependencies.journal.begin(binding)
        if resolution.review_progress is None or resolution.replayed:
            raise StateConflictError("Pi Scene progressed retry lacks recoverable response custody")
        return self.dependencies.recover_progressed_request(
            binding,
            resolution.review_progress,
        )

    def _block_interrupted(
        self,
        *,
        retry_id: str,
        binding: PiSceneRequestBindingV1,
        turn: LeanSceneTurnInputV1,
        failure: Exception,
    ) -> None:
        journal = self.dependencies.journal
        entry_status = "unavailable"
        try:
            entry_status = str(journal.inspect(binding)["status"])
        except (KeyError, StateConflictError):
            pass
        if entry_status == "progressed":
            return
        effect_payload: Mapping[str, Any] | None = None
        reason = "dispatch_state_ambiguous"
        try:
            effect = self.dependencies.effect_snapshot(turn)
            effect_payload = effect.to_payload()
            authorization = journal.prepare_transport_retry(retry_id)
            if effect != authorization.effect_proof.after_snapshot:
                reason = "effect_state_changed"
        except (StateConflictError, TransportRetryNotFoundError):
            pass
        ledger_payload: Mapping[str, Any] | None = None
        try:
            ledger = self.dependencies.provider_ledger_snapshot()
            ledger_payload = {
                "event_count": ledger.event_count,
                "events_sha256": ledger.events_sha256,
                "dispatched_call_count": ledger.dispatched_call_count,
            }
        except StateConflictError:
            pass
        evidence = canonical_sha256(
            {
                "schema_version": "cera.pi_scene.transport_retry_block_evidence.v1",
                "retry_id": retry_id,
                "request_id": binding.request_id,
                "reason_code": reason,
                "request_entry_status": entry_status,
                "effect": effect_payload,
                "provider_ledger": ledger_payload,
                "failure_type": type(failure).__name__,
            }
        )
        journal.block_transport_retry(retry_id, reason, evidence_sha256=evidence)
        journal.redact_transport_authority(binding)

    def _validate_context(
        self,
        authorization: TransportRetryAuthorizationV1,
    ) -> tuple[
        dict[str, Any],
        PiSceneChatRequestV1,
        LeanSceneTurnInputV1,
        PiSceneRequestBindingV1,
        PiSceneProviderLedgerSnapshotV1,
    ]:
        journal = self.dependencies.journal
        payload = dict(authorization.normalized_request)
        request, turn, binding = self.dependencies.prepare_bound_request(payload)
        proof = authorization.effect_proof
        if (
            binding != authorization.binding
            or request.route.value != proof.resolved_route
            or canonical_sha256(turn) != proof.turn_context_sha256
        ):
            journal.block_transport_retry(
                authorization.retry_id,
                "route_or_context_changed",
            )
            raise StateConflictError(
                "Pi Scene transport retry changed route, request, or turn custody"
            )
        current_effect = self.dependencies.effect_snapshot(turn)
        if current_effect != proof.after_snapshot:
            journal.block_transport_retry(
                authorization.retry_id,
                "effect_state_changed",
            )
            raise StateConflictError("Pi Scene transport retry story state changed")
        current_ledger = self.dependencies.provider_ledger_snapshot()
        failure = proof.provider_failure_evidence
        prefix = current_ledger.events[: failure.after_event_count]
        if (
            len(prefix) != failure.after_event_count
            or canonical_sha256(tuple(dict(value) for value in prefix))
            != failure.after_events_sha256
            or current_ledger.dispatched_call_count < failure.dispatched_calls_after
        ):
            journal.block_transport_retry(
                authorization.retry_id,
                "provider_ledger_changed",
            )
            raise StateConflictError("Pi Scene transport retry Sol ledger changed")
        return payload, request, turn, binding, current_ledger

    def _reconcile_interrupted_dispatch(
        self,
        authorization: TransportRetryAuthorizationV1,
    ) -> TransportFailureReceiptV1 | None:
        journal = self.dependencies.journal
        try:
            payload, request, turn, binding, current = self._validate_context(authorization)
        except StateConflictError:
            return None
        baseline = authorization.dispatch_ledger_before
        if baseline is None:
            journal.block_transport_retry(
                authorization.retry_id,
                "dispatch_state_ambiguous",
            )
            return None
        count = baseline["event_count"]
        prefix = current.events[:count]
        if (
            len(prefix) != count
            or canonical_sha256(tuple(dict(value) for value in prefix)) != baseline["events_sha256"]
        ):
            journal.block_transport_retry(
                authorization.retry_id,
                "provider_ledger_changed",
            )
            return None
        thread_snapshot = self.dependencies.active_thread_snapshot
        if thread_snapshot is None:
            journal.block_transport_retry(
                authorization.retry_id,
                "owner_rotation_failed",
            )
            return None
        active_thread_sha256 = thread_snapshot(
            binding,
            turn,
            authorization.logic_owner,
        )
        fresh_thread_sha256 = authorization.fresh_thread_sha256
        if fresh_thread_sha256 is None:
            if active_thread_sha256 is None:
                journal.block_transport_retry(
                    authorization.retry_id,
                    "owner_rotation_failed",
                )
                return None
            try:
                journal.bind_transport_fresh_thread(
                    authorization.retry_id,
                    active_thread_sha256,
                )
            except (ContractValidationError, StateConflictError):
                journal.block_transport_retry(
                    authorization.retry_id,
                    "owner_rotation_failed",
                )
                return None
            fresh_thread_sha256 = active_thread_sha256
        elif active_thread_sha256 != fresh_thread_sha256:
            journal.block_transport_retry(
                authorization.retry_id,
                "owner_rotation_failed",
            )
            return None
        try:
            selected = self._provider_call_span_for_thread(
                current,
                baseline_event_count=count,
                stored_thread_sha256=fresh_thread_sha256,
            )
        except StateConflictError:
            journal.block_transport_retry(
                authorization.retry_id,
                "provider_ledger_changed",
            )
            return None
        if selected is None:
            journal.rearm_transport_retry_after_zero_dispatch(
                authorization.retry_id,
                PiSceneProviderLedgerSnapshotV1(
                    schema_version=PiSceneProviderLedgerSnapshotV1.SCHEMA_VERSION,
                    dispatched_call_count=baseline["dispatched_call_count"],
                    events=tuple(prefix),
                ),
            )
            return None
        before, after, appended = selected
        states = tuple(value.get("state") for value in appended)
        submitted = states[-1] == "provider_failed"
        if states[-1] not in {"provider_failed", "pretransport_failed"}:
            journal.block_transport_retry(
                authorization.retry_id,
                "dispatch_state_ambiguous",
            )
            return None
        try:
            evidence = build_provider_failure_evidence(
                before=before,
                after=after,
                provider_operation_submitted=submitted,
                provider_failure_detail_sha256=provider_failure_detail_sha256(
                    None,
                    call_events=appended,
                ),
            )
        except (ContractValidationError, StateConflictError):
            journal.block_transport_retry(
                authorization.retry_id,
                "provider_ledger_changed",
            )
            return None
        effect = self.dependencies.effect_snapshot(turn)
        proof = PiSceneZeroEffectProofV1(
            schema_version=PiSceneZeroEffectProofV1.SCHEMA_VERSION,
            request_id=binding.request_id,
            logic_owner="planner",
            resolved_route=request.route.value,
            turn_context_sha256=canonical_sha256(turn),
            before_snapshot=effect,
            after_snapshot=effect,
            provider_failure_evidence=evidence,
            candidate_effect_absent=True,
            review_effect_absent=True,
            accepted_effect_absent=True,
            recording_effect_absent=True,
            branch_head_effect_absent=True,
        )
        return journal.record_transport_failure(
            binding,
            normalized_request=payload,
            logic_owner="planner",
            provider_error_code=ErrorCode.PROVIDER_TRANSPORT_FAILED.value,
            provider_operations_observed=int(submitted),
            effect_proof=proof,
            provider_ledger_before=before,
        )

    @staticmethod
    def _provider_call_span_for_thread(
        ledger: PiSceneProviderLedgerSnapshotV1,
        *,
        baseline_event_count: int,
        stored_thread_sha256: str,
    ) -> (
        tuple[
            PiSceneProviderLedgerSnapshotV1,
            PiSceneProviderLedgerSnapshotV1,
            tuple[Mapping[str, Any], ...],
        ]
        | None
    ):
        suffix = ledger.events[baseline_event_count:]
        matching = tuple(
            value for value in suffix if value.get("stored_thread_sha256") == stored_thread_sha256
        )
        if not matching:
            return None
        call_ids = {value.get("call_id") for value in matching}
        if len(call_ids) != 1 or not all(isinstance(value, str) for value in call_ids):
            raise StateConflictError("Pi Scene retry fresh thread has multiple provider calls")
        call_id = next(iter(call_ids))
        assert isinstance(call_id, str)
        indices = tuple(
            index for index, value in enumerate(ledger.events) if value.get("call_id") == call_id
        )
        if not indices or indices[0] < baseline_event_count:
            raise StateConflictError("Pi Scene retry provider call escaped its baseline")
        first = indices[0]
        last = indices[-1]
        call_events = tuple(ledger.events[first : last + 1])
        if any(
            value.get("call_id") != call_id
            or value.get("stored_thread_sha256") != stored_thread_sha256
            for value in call_events
        ):
            raise StateConflictError("Pi Scene retry provider call interleaved")
        before_events = tuple(ledger.events[:first])
        after_events = tuple(ledger.events[: last + 1])
        before = PiSceneProviderLedgerSnapshotV1(
            schema_version=PiSceneProviderLedgerSnapshotV1.SCHEMA_VERSION,
            dispatched_call_count=provider_ledger_dispatched_call_count(before_events),
            events=before_events,
        )
        after = PiSceneProviderLedgerSnapshotV1(
            schema_version=PiSceneProviderLedgerSnapshotV1.SCHEMA_VERSION,
            dispatched_call_count=provider_ledger_dispatched_call_count(after_events),
            events=after_events,
        )
        return before, after, call_events


def transport_retry_action(receipt: TransportFailureReceiptV1) -> dict[str, Any]:
    """Project the only action accepted by the manual Retry endpoint."""

    return {
        "schema_version": "cera.pi_scene.transport_retry.v1",
        "retry_id": receipt.retry_id,
        "retry_url": "/v1/cera/transport-retries/" + receipt.retry_id,
        "method": "POST",
        "eligible": True,
        "automatic": False,
        "effect_proof_sha256": receipt.effect_proof_sha256,
    }


def retry_status_common(
    authorization: TransportRetryAuthorizationV1,
) -> dict[str, Any]:
    """Build identity fields shared by every authenticated GET status."""

    return {
        "schema_version": "cera.pi_scene.transport_retry_status.v1",
        "retry_id": authorization.retry_id,
        "request_id": authorization.binding.request_id,
        "state": "eligible",
        "effect_proof_sha256": authorization.effect_proof.proof_sha256,
        "retry_transport_enabled": False,
    }


def eligible_retry_status(
    authorization: TransportRetryAuthorizationV1,
) -> dict[str, Any]:
    return {
        **retry_status_common(authorization),
        "retry_transport_enabled": True,
        "transport_retry": transport_retry_action(authorization.receipt),
    }


def in_progress_retry_status(
    authorization: TransportRetryAuthorizationV1,
    *,
    phase: str,
) -> dict[str, Any]:
    return {
        **retry_status_common(authorization),
        "state": "in_progress",
        "phase": phase,
    }


def blocked_retry_status(
    authorization: TransportRetryAuthorizationV1,
    *,
    reason_code: str | None = None,
) -> dict[str, Any]:
    return {
        **retry_status_common(authorization),
        "state": "blocked",
        "blocked_reason_code": reason_code or authorization.blocked_reason_code,
    }


def superseded_retry_status(
    authorization: TransportRetryAuthorizationV1,
    successor: TransportFailureReceiptV1,
) -> dict[str, Any]:
    return {
        **retry_status_common(authorization),
        "state": "superseded",
        "retry_transport_enabled": True,
        "superseded_by_retry_id": successor.retry_id,
        "transport_retry": transport_retry_action(successor),
    }


def validated_retry_completion(
    authorization: TransportRetryAuthorizationV1,
) -> dict[str, Any]:
    """Return a terminal completion only when it retains request identity."""

    response = authorization.replayed_terminal_response
    if response is None:
        raise StateConflictError("Pi Scene transport retry completion is unavailable")
    completion = dict(response)
    cera = completion.get("cera")
    if not isinstance(cera, dict) or cera.get("request_id") != authorization.binding.request_id:
        raise StateConflictError("Pi Scene transport retry completion changed request identity")
    return completion


def succeeded_retry_status(
    authorization: TransportRetryAuthorizationV1,
    *,
    completion: dict[str, Any] | None = None,
) -> dict[str, Any]:
    terminal = completion or validated_retry_completion(authorization)
    return {
        **retry_status_common(authorization),
        "state": "succeeded",
        "completion": terminal,
        "completion_sha256": canonical_sha256(terminal),
    }


def terminal_or_superseding_retry(
    authorization: TransportRetryAuthorizationV1,
) -> dict[str, Any] | None:
    """Project durable terminal state or raise the exact successor action."""

    if authorization.superseding_failure is not None:
        raise PiSceneManualTransportRetryError(authorization.superseding_failure)
    if authorization.replayed_terminal_response is not None:
        return validated_retry_completion(authorization)
    return None


def transport_retry_id(path: str) -> str | None:
    """Decode one exact Retry endpoint path without leaking ID existence."""

    prefix = "/v1/cera/transport-retries/"
    if not path.startswith(prefix):
        return None
    value = unquote(path[len(prefix) :])
    if re.fullmatch(r"retry-[a-f0-9]{64}", value) is None:
        return None
    return value
