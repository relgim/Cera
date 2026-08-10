"""Durable pre-dispatch custody for Pi Scene chat-completion requests.

The journal is deliberately transport-only.  It binds canonical request bytes
to the Python-owned session, world, branch, route, and control envelope without
interpreting story meaning.  A terminal entry can be replayed byte-for-byte at
the JSON value level; an incomplete entry always fails closed so a process
restart cannot silently submit the same provider operation twice.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Any
from uuid import uuid4

from cera.errors import ContractValidationError, StateConflictError
from cera.serialization import (
    canonical_bytes,
    canonical_sha256,
    domain_sha256,
    re_is_sha256,
    text_sha256,
    to_primitive,
)

from .contracts import SceneRoute
from .request_binding import (
    PiSceneRequestBindingV1,
    _binding_from_payload,
)
from .request_binding import (
    build_request_binding as build_request_binding,
)
from .request_progress import validate_request_progress
from .transport_retry import (
    TRANSPORT_RETRY_BLOCK_REASONS,
    TRANSPORT_RETRY_OWNER,
    PiScenePlannerCompletionMarkerV1,
    PiScenePlannerResultUnavailableV1,
    PiSceneProviderLedgerSnapshotV1,
    PiSceneTransportEffectSnapshotV1,
    PiSceneZeroEffectProofV1,
    TransportFailureReceiptV1,
    effect_snapshot_from_payload,
    planner_completion_marker_from_payload,
    planner_result_unavailable_from_payload,
)
from .transport_retry_store import (
    TransportRetryAuthorizationV1,
    TransportRetryNotFoundError,
    TransportRetryStoreMixin,
)

__all__ = (
    "PiSceneRequestBindingV1",
    "PiSceneRequestJournal",
    "PlannerResultUnavailableError",
    "RequestJournalResolutionV1",
    "RequestReplayPendingError",
    "TransportRetryAuthorizationV1",
    "TransportRetryNotFoundError",
    "build_request_binding",
)


class RequestReplayPendingError(StateConflictError):
    """An identical request has non-terminal custody and cannot be dispatched."""

    def __init__(self, message: str, *, request_id: str) -> None:
        super().__init__(message)
        self.request_id = request_id


class PlannerResultUnavailableError(RequestReplayPendingError):
    """A charged Planner result was lost before it became durable story progress."""

    def __init__(self, disposition: PiScenePlannerResultUnavailableV1) -> None:
        self.disposition = disposition
        super().__init__(
            "Pi Scene Planner completed, but its result was lost before durable progress; "
            "the exact request cannot be redispatched",
            request_id=disposition.completion.request_id,
        )


@dataclass(frozen=True, slots=True)
class RequestJournalResolutionV1:
    request_id: str
    replayed: bool
    terminal_response: Mapping[str, Any] | None
    review_progress: Mapping[str, Any] | None


class PiSceneRequestJournal(TransportRetryStoreMixin):
    """Atomic, restart-safe request replay journal under one runtime root."""

    SCHEMA_VERSION = "cera.pi_scene.http_request_journal.v1"

    def __init__(
        self,
        root: Path,
        *,
        protected_retry_root: Path | None = None,
    ) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.protected_retry_root = (
            (self.root.parent / f"{self.root.name}.protected-transport-retry")
            if protected_retry_root is None
            else protected_retry_root.resolve()
        )
        if self.protected_retry_root == self.root or self.protected_retry_root.is_relative_to(
            self.root
        ):
            raise ContractValidationError(
                "Pi Scene protected retry custody must be outside the safe journal root"
            )
        self.process_instance_id = "process-" + uuid4().hex
        self._lock = RLock()

    def begin(
        self,
        binding: PiSceneRequestBindingV1,
    ) -> RequestJournalResolutionV1:
        """Create durable pending custody or return the exact terminal response."""

        path = self._entry_path(binding)
        with self._lock, self._claim(path):
            if path.exists():
                return self._resolve_existing(path, binding)
            _atomic_write_json(path, self._pending_entry(binding))
            # Read back the exact durable bytes before any provider is allowed.
            self._read_entry(path, expected=binding)
            return RequestJournalResolutionV1(
                request_id=binding.request_id,
                replayed=False,
                terminal_response=None,
                review_progress=None,
            )

    def bind_progress(
        self,
        binding: PiSceneRequestBindingV1,
        review_progress: Mapping[str, Any],
    ) -> None:
        """Persist exact durable operation custody before response rendering."""

        progress = to_primitive(review_progress)
        if not isinstance(progress, dict):
            raise ContractValidationError("Pi Scene review progress must be an object")
        validate_request_progress(progress, binding)
        path = self._entry_path(binding)
        with self._lock, self._claim(path):
            if not path.exists():
                raise StateConflictError("Pi Scene review progress lacks pending custody")
            current = self._read_entry(path, expected=binding)
            if current["status"] in {"progressed", "terminal"}:
                if current["review_progress"] != progress:
                    raise StateConflictError("Pi Scene request review progress changed")
                self._finalize_transport_authority_locked(binding)
                return
            body = {
                "schema_version": self.SCHEMA_VERSION,
                "binding": binding.to_payload(),
                "status": "progressed",
                "pre_dispatch_journal_sha256": self._pending_entry(binding)["journal_sha256"],
                "review_progress": progress,
                "terminal_response": None,
                "terminal_response_sha256": None,
            }
            progressed = {**body, "journal_sha256": canonical_sha256(body)}
            _atomic_write_json(path, progressed)
            self._read_entry(path, expected=binding)
            self._finalize_transport_authority_locked(binding)

    def bind_review(
        self,
        binding: PiSceneRequestBindingV1,
        review_progress: Mapping[str, Any],
    ) -> None:
        """Historical ordinary-review spelling retained for compatibility."""

        self.bind_progress(binding, review_progress)

    def complete(
        self,
        binding: PiSceneRequestBindingV1,
        response: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Atomically attach one immutable terminal response to pending custody."""

        response_payload = to_primitive(response)
        if not isinstance(response_payload, dict):
            raise ContractValidationError("Pi Scene terminal response must be an object")
        path = self._entry_path(binding)
        with self._lock, self._claim(path):
            if not path.exists():
                raise StateConflictError("Pi Scene request terminalization lacks pending custody")
            current = self._read_entry(path, expected=binding)
            response_payload = self._attach_transport_retry_trace(
                binding,
                response_payload,
            )
            if current["status"] == "terminal":
                if current["terminal_response"] != response_payload:
                    raise StateConflictError("Pi Scene terminal request response changed")
                self._mark_transport_retry_succeeded_locked(
                    binding,
                    canonical_sha256(response_payload),
                )
                self._finalize_transport_authority_locked(binding)
                return dict(response_payload)
            if current["status"] != "progressed":
                raise StateConflictError("Pi Scene terminal response lacks durable review progress")
            body = {
                "schema_version": self.SCHEMA_VERSION,
                "binding": binding.to_payload(),
                "status": "terminal",
                "pre_dispatch_journal_sha256": self._pending_entry(binding)["journal_sha256"],
                "review_progress": current["review_progress"],
                "terminal_response": response_payload,
                "terminal_response_sha256": canonical_sha256(response_payload),
            }
            terminal = {**body, "journal_sha256": canonical_sha256(body)}
            _atomic_write_json(path, terminal)
            self._read_entry(path, expected=binding)
            self._mark_transport_retry_succeeded_locked(
                binding,
                canonical_sha256(response_payload),
            )
            self._finalize_transport_authority_locked(binding)
            return dict(response_payload)

    def record_transport_failure(
        self,
        binding: PiSceneRequestBindingV1,
        *,
        normalized_request: Mapping[str, Any],
        logic_owner: str,
        provider_error_code: str,
        provider_operations_observed: int,
        effect_proof: PiSceneZeroEffectProofV1,
        provider_ledger_before: PiSceneProviderLedgerSnapshotV1,
    ) -> TransportFailureReceiptV1:
        """Freeze one exact Planner failure and expose one manual action."""

        if logic_owner != TRANSPORT_RETRY_OWNER:
            raise ContractValidationError("Pi Scene transport retry is Planner-only")
        if type(provider_operations_observed) is not int or (
            provider_operations_observed not in {0, 1}
        ):
            raise ContractValidationError("Pi Scene transport operation count is invalid")
        if effect_proof.request_id != binding.request_id or effect_proof.logic_owner != logic_owner:
            raise ContractValidationError("Pi Scene transport effect proof changed custody")
        if provider_operations_observed != int(
            effect_proof.provider_failure_evidence.provider_operation_submitted
        ):
            raise ContractValidationError("Pi Scene transport receipt differs from Sol ledger")
        failure_evidence = effect_proof.provider_failure_evidence
        if (
            provider_ledger_before.event_count != failure_evidence.before_event_count
            or provider_ledger_before.events_sha256 != failure_evidence.before_events_sha256
            or provider_ledger_before.dispatched_call_count
            != failure_evidence.dispatched_calls_before
        ):
            raise ContractValidationError(
                "Pi Scene transport failure lost its exact pre-call Sol prefix"
            )
        entry_path = self._entry_path(binding)
        ledger_path = self._transport_ledger_path(binding)
        with self._lock, self._claim(entry_path):
            entry = self._read_entry(entry_path, expected=binding)
            if entry["status"] != "pending":
                raise StateConflictError(
                    "Pi Scene transport failure is ineligible after durable progress"
                )
            custody = self._ensure_request_custody(binding, normalized_request)
            current = self._read_transport_ledger(binding, required=False)
            failures = [] if current is None else list(current["failures"])
            actions = [] if current is None else list(current["actions"])
            predecessor_retry_id = None if current is None else current["active_retry_id"]
            if current is not None:
                active_action = actions[-1]
                active_receipt, active_proof = self._decode_failure_entry(failures[-1])
                if active_action["phase"] == "eligible":
                    same_failure = (
                        active_receipt.logic_owner == logic_owner
                        and active_receipt.provider_error_code == provider_error_code
                        and active_receipt.provider_operations_observed
                        == provider_operations_observed
                        and active_proof == effect_proof
                    )
                    if not same_failure:
                        raise StateConflictError(
                            "Pi Scene transport failure changed before its manual retry"
                        )
                    self._write_retry_index(binding, active_receipt)
                    return active_receipt
                if active_action["phase"] != "dispatch_started":
                    raise StateConflictError(
                        "Pi Scene transport failure lacks dispatch-started custody"
                    )
            failure_number = len(failures) + 1
            unsigned = {
                "schema_version": "cera.pi_scene.transport_failure_receipt.v1",
                "failure_number": failure_number,
                "request_id": binding.request_id,
                "logic_owner": logic_owner,
                "provider_error_code": provider_error_code,
                "provider_operations_observed": provider_operations_observed,
                "effect_proof_sha256": effect_proof.proof_sha256,
                "predecessor_retry_id": predecessor_retry_id,
            }
            failure_sha256 = domain_sha256(
                "cera.pi_scene.transport_failure_receipt.v1",
                unsigned,
            )
            retry_id = "retry-" + domain_sha256(
                "cera.pi_scene.transport_retry.v1",
                {
                    "request_id": binding.request_id,
                    "failure_receipt_sha256": failure_sha256,
                    "failure_number": failure_number,
                },
            )
            receipt = TransportFailureReceiptV1(
                failure_number=failure_number,
                request_id=binding.request_id,
                logic_owner=logic_owner,
                provider_error_code=provider_error_code,
                provider_operations_observed=provider_operations_observed,
                effect_proof_sha256=effect_proof.proof_sha256,
                predecessor_retry_id=predecessor_retry_id,
                failure_receipt_sha256=failure_sha256,
                retry_id=retry_id,
            )
            if current is not None:
                failed_thread_sha256 = effect_proof.provider_failure_evidence.stored_thread_sha256
                prior_thread_hashes = {
                    self._decode_failure_entry(value)[
                        1
                    ].provider_failure_evidence.stored_thread_sha256
                    for value in failures
                }
                if failed_thread_sha256 in prior_thread_hashes:
                    raise StateConflictError(
                        "Pi Scene retry resumed a previously failed Planner thread"
                    )
                prior = dict(actions[-1])
                bound_fresh_thread = prior["fresh_thread_sha256"]
                if bound_fresh_thread not in {None, failed_thread_sha256}:
                    raise StateConflictError(
                        "Pi Scene retry failure differs from its fresh Planner thread"
                    )
                prior.update(
                    {
                        "phase": "terminal_failed",
                        "fresh_thread_sha256": failed_thread_sha256,
                        "successor_retry_id": retry_id,
                    }
                )
                actions[-1] = self._signed_retry_action(prior)
            bridge = (
                None
                if current is None
                else self._provider_prefix_bridge(
                    self._decode_failure_entry(failures[-1])[1],
                    provider_ledger_before,
                )
            )
            failures.append(
                {
                    "receipt": receipt.to_payload(),
                    "effect_proof": effect_proof.to_payload(),
                    "provider_prefix_bridge": bridge,
                }
            )
            actions.append(self._new_retry_action(receipt.retry_id))
            body = {
                "schema_version": "cera.pi_scene.transport_retry_ledger.v2",
                "request_id": binding.request_id,
                "request_custody_sha256": custody["custody_sha256"],
                "failures": failures,
                "actions": actions,
                "active_retry_id": retry_id,
            }
            ledger_payload = {**body, "ledger_sha256": canonical_sha256(body)}
            self._write_transport_authority(
                binding,
                custody=custody,
                phase="failure_eligible",
                dispatch_capsule=None,
                transport_ledger=ledger_payload,
            )
            _atomic_write_json(ledger_path, ledger_payload)
            self._write_retry_index(binding, receipt)
            self._read_transport_ledger(binding, required=True)
            return receipt

    def bind_transport_fresh_thread(
        self,
        retry_id: str,
        fresh_thread_sha256: str,
    ) -> None:
        """Bind the exact post-rotation Planner thread before reconciliation."""

        if not re_is_sha256(fresh_thread_sha256):
            raise ContractValidationError("Pi Scene fresh Planner thread hash is invalid")
        binding, entry_path = self._binding_for_retry_id(retry_id)
        with self._lock, self._claim(entry_path):
            ledger = self._read_transport_ledger(binding, required=True)
            assert ledger is not None
            actions = list(ledger["actions"])
            active = dict(actions[-1])
            if active["retry_id"] != retry_id or active["phase"] not in {
                "owner_rotated",
                "dispatch_started",
            }:
                raise StateConflictError("Pi Scene fresh thread lacks active retry custody")
            prior_hashes = {
                self._decode_failure_entry(value)[1].provider_failure_evidence.stored_thread_sha256
                for value in ledger["failures"]
            }
            if fresh_thread_sha256 in prior_hashes:
                raise StateConflictError("Pi Scene fresh Planner thread repeats a failed thread")
            existing = active["fresh_thread_sha256"]
            if existing is not None and existing != fresh_thread_sha256:
                raise StateConflictError("Pi Scene fresh Planner thread binding changed")
            if existing == fresh_thread_sha256:
                return
            active["fresh_thread_sha256"] = fresh_thread_sha256
            actions[-1] = self._signed_retry_action(active)
            self._write_transport_ledger(binding, ledger, actions=actions)

    def stage_transport_dispatch(
        self,
        binding: PiSceneRequestBindingV1,
        *,
        normalized_request: Mapping[str, Any],
        logic_owner: str,
        resolved_route: str,
        turn_context_sha256: str,
        effect_before: PiSceneTransportEffectSnapshotV1,
        provider_ledger_before: PiSceneProviderLedgerSnapshotV1,
        planner_thread_sha256: str | None,
    ) -> None:
        """Freeze exact protected Planner-dispatch custody before provider work."""

        if logic_owner != TRANSPORT_RETRY_OWNER:
            raise ContractValidationError("Pi Scene dispatch capsule is Planner-only")
        if resolved_route not in {"ordinary", "adult"}:
            raise ContractValidationError("Pi Scene dispatch capsule route is invalid")
        if not re_is_sha256(turn_context_sha256):
            raise ContractValidationError("Pi Scene dispatch capsule turn hash is invalid")
        if planner_thread_sha256 is not None and not re_is_sha256(planner_thread_sha256):
            raise ContractValidationError(
                "Pi Scene dispatch capsule Planner thread hash is invalid"
            )
        entry_path = self._entry_path(binding)
        with self._lock, self._claim(entry_path):
            entry = self._read_entry(entry_path, expected=binding)
            if entry["status"] != "pending":
                raise StateConflictError(
                    "Pi Scene provider dispatch cannot start after durable progress"
                )
            custody = self._ensure_request_custody(binding, normalized_request)
            ledger = self._read_transport_ledger(binding, required=False)
            capsule_body = {
                "schema_version": "cera.pi_scene.transport_dispatch_capsule.v1",
                "request_id": binding.request_id,
                "logic_owner": logic_owner,
                "resolved_route": resolved_route,
                "turn_context_sha256": turn_context_sha256,
                "effect_before": effect_before.to_payload(),
                "planner_thread_sha256": planner_thread_sha256,
                "provider_ledger_before": self._provider_ledger_snapshot_payload(
                    provider_ledger_before
                ),
            }
            capsule = {
                **capsule_body,
                "capsule_sha256": canonical_sha256(capsule_body),
            }
            self._write_transport_authority(
                binding,
                custody=custody,
                phase="dispatch_staged",
                dispatch_capsule=capsule,
                transport_ledger=ledger,
            )

    def staged_transport_dispatch(
        self,
        binding: PiSceneRequestBindingV1,
    ) -> Mapping[str, Any] | None:
        """Return validated protected dispatch custody for restart reconciliation."""

        entry_path = self._entry_path(binding)
        with self._lock, self._claim(entry_path):
            authority = self._read_transport_authority(binding, required=False)
            if authority is None or authority["phase"] not in {
                "dispatch_staged",
                "planner_completed_pending_progress",
            }:
                return None
            return {
                "phase": authority["phase"],
                "normalized_request": (
                    None
                    if authority["normalized_request"] is None
                    else dict(authority["normalized_request"])
                ),
                "dispatch_capsule": dict(authority["dispatch_capsule"]),
            }

    def mark_planner_completed_pending_progress(
        self,
        binding: PiSceneRequestBindingV1,
        completion: PiScenePlannerCompletionMarkerV1,
    ) -> None:
        """Redact raw request bytes while a completed plan awaits durable progress."""

        if completion.request_id != binding.request_id:
            raise ContractValidationError("Pi Scene Planner completion changed request custody")
        entry_path = self._entry_path(binding)
        with self._lock, self._claim(entry_path):
            entry = self._read_entry(entry_path, expected=binding)
            if entry["status"] in {"progressed", "terminal"}:
                self._finalize_transport_authority_locked(binding)
                return
            if entry["status"] != "pending":
                raise StateConflictError("Pi Scene Planner completion lacks pending custody")
            authority = self._read_transport_authority(binding, required=True)
            assert authority is not None
            if authority["phase"] == "planner_completed_pending_progress":
                existing = planner_completion_marker_from_payload(authority["dispatch_capsule"])
                if existing != completion:
                    raise StateConflictError("Pi Scene Planner completion marker changed")
                return
            if authority["phase"] != "dispatch_staged":
                raise StateConflictError("Pi Scene Planner completion lacks staged custody")
            capsule = authority["dispatch_capsule"]
            if (
                capsule["resolved_route"] != completion.resolved_route
                or capsule["turn_context_sha256"] != completion.turn_context_sha256
                or effect_snapshot_from_payload(capsule["effect_before"])
                != completion.effect_before
                or (
                    capsule["planner_thread_sha256"] is not None
                    and capsule["planner_thread_sha256"] != completion.stored_thread_sha256
                )
            ):
                raise StateConflictError("Pi Scene Planner completion marker changed custody")
            self._write_transport_authority(
                binding,
                custody=None,
                phase="planner_completed_pending_progress",
                dispatch_capsule=completion.to_payload(),
                transport_ledger=authority["transport_ledger"],
            )

    def mark_planner_result_unavailable(
        self,
        binding: PiSceneRequestBindingV1,
        disposition: PiScenePlannerResultUnavailableV1,
    ) -> None:
        """Close a consumed, uncommitted Planner result without Retry authority."""

        if disposition.completion.request_id != binding.request_id:
            raise ContractValidationError("Pi Scene Planner result changed request custody")
        entry_path = self._entry_path(binding)
        with self._lock, self._claim(entry_path):
            current = self._read_entry(entry_path, expected=binding)
            if current["status"] == "planner_result_unavailable":
                existing = planner_result_unavailable_from_payload(current["review_progress"])
                if existing != disposition:
                    raise StateConflictError("Pi Scene Planner result disposition changed")
                self._reconcile_planner_result_unavailable_locked(
                    binding,
                    disposition,
                )
                return
            if current["status"] != "pending":
                raise StateConflictError("Pi Scene Planner result lacks pending custody")
            body = {
                "schema_version": self.SCHEMA_VERSION,
                "binding": binding.to_payload(),
                "status": "planner_result_unavailable",
                "pre_dispatch_journal_sha256": self._pending_entry(binding)["journal_sha256"],
                "review_progress": disposition.to_payload(),
                "terminal_response": None,
                "terminal_response_sha256": None,
            }
            _atomic_write_json(
                entry_path,
                {**body, "journal_sha256": canonical_sha256(body)},
            )
            self._read_entry(entry_path, expected=binding)
            self._reconcile_planner_result_unavailable_locked(
                binding,
                disposition,
            )

    def _reconcile_planner_result_unavailable_locked(
        self,
        binding: PiSceneRequestBindingV1,
        disposition: PiScenePlannerResultUnavailableV1,
    ) -> None:
        """Repair Retry action/redaction from the journal-owned disposition."""

        ledger = self._read_transport_ledger(binding, required=False)
        if ledger is not None:
            actions = list(ledger["actions"])
            active = dict(actions[-1])
            if active["phase"] == "blocked":
                if (
                    active["blocked_reason_code"] != "planner_result_unavailable"
                    or active["blocked_evidence_sha256"] != disposition.disposition_sha256
                ):
                    raise StateConflictError("Pi Scene unavailable Planner Retry block changed")
            elif active["phase"] in {"terminal_failed", "succeeded"}:
                raise StateConflictError(
                    "Pi Scene unavailable Planner result conflicts with terminal Retry"
                )
            else:
                active.update(
                    {
                        "phase": "blocked",
                        "blocked_reason_code": "planner_result_unavailable",
                        "blocked_evidence_sha256": disposition.disposition_sha256,
                    }
                )
                actions[-1] = self._signed_retry_action(active)
                self._write_transport_ledger(binding, ledger, actions=actions)
        self._redact_unavailable_planner_authority_locked(binding)

    def discard_staged_transport_dispatch(
        self,
        binding: PiSceneRequestBindingV1,
    ) -> None:
        """Close a staged capsule after Planner success or an ineligible failure."""

        entry_path = self._entry_path(binding)
        with self._lock, self._claim(entry_path):
            authority = self._read_transport_authority(binding, required=False)
            if authority is None or authority["phase"] != "dispatch_staged":
                return
            ledger = authority["transport_ledger"]
            if ledger is None:
                self._transport_authority_path(binding).unlink(missing_ok=True)
                return
            self._write_transport_authority(
                binding,
                custody=None,
                phase="failure_eligible",
                dispatch_capsule=None,
                transport_ledger=ledger,
            )

    def rearm_staged_transport_dispatch_after_zero_call(
        self,
        binding: PiSceneRequestBindingV1,
        *,
        ledger_current: PiSceneProviderLedgerSnapshotV1,
        planner_thread_sha256: str | None,
    ) -> RequestJournalResolutionV1:
        """Resume an exact request only when its staged Planner made no call."""

        entry_path = self._entry_path(binding)
        with self._lock, self._claim(entry_path):
            entry = self._read_entry(entry_path, expected=binding)
            if entry["status"] == "rearmed":
                return self._resolve_existing(entry_path, binding)
            if entry["status"] != "pending":
                raise StateConflictError("Pi Scene zero-call rearm lacks pending request custody")
            authority = self._read_transport_authority(binding, required=True)
            assert authority is not None
            if authority["phase"] != "dispatch_staged":
                raise StateConflictError("Pi Scene zero-call rearm lacks a staged Planner dispatch")
            capsule = authority["dispatch_capsule"]
            baseline = self._provider_ledger_snapshot_from_payload(
                capsule["provider_ledger_before"]
            )
            if (
                ledger_current.event_count < baseline.event_count
                or tuple(ledger_current.events[: baseline.event_count]) != baseline.events
            ):
                raise StateConflictError("Pi Scene zero-call rearm lost its provider-ledger prefix")
            suffix = ledger_current.events[baseline.event_count :]
            if planner_thread_sha256 is None:
                if suffix:
                    raise StateConflictError(
                        "Pi Scene zero-call rearm has no Planner thread identity"
                    )
            elif any(
                value.get("stored_thread_sha256") == planner_thread_sha256 for value in suffix
            ):
                raise StateConflictError("Pi Scene zero-call rearm found a Planner ledger event")
            rearm_body = {
                "schema_version": "cera.pi_scene.zero_call_rearm.v1",
                "request_id": binding.request_id,
                "capsule_sha256": capsule["capsule_sha256"],
                "planner_thread_sha256": planner_thread_sha256,
                "provider_ledger": self._provider_ledger_prefix_payload(ledger_current),
            }
            rearm = {**rearm_body, "rearm_sha256": canonical_sha256(rearm_body)}
            body = {
                "schema_version": self.SCHEMA_VERSION,
                "binding": binding.to_payload(),
                "status": "rearmed",
                "pre_dispatch_journal_sha256": self._pending_entry(binding)["journal_sha256"],
                "review_progress": rearm,
                "terminal_response": None,
                "terminal_response_sha256": None,
            }
            _atomic_write_json(
                entry_path,
                {**body, "journal_sha256": canonical_sha256(body)},
            )
            # A crash here is reconciled by ``begin`` from the rearmed entry;
            # raw protected request bytes never need to be published elsewhere.
            return self._resolve_existing(entry_path, binding)

    def redact_transport_authority(
        self,
        binding: PiSceneRequestBindingV1,
    ) -> None:
        """Remove protected request bytes once no manual dispatch can use them."""

        entry_path = self._entry_path(binding)
        with self._lock, self._claim(entry_path):
            self._finalize_transport_authority_locked(binding)

    @staticmethod
    def provider_ledger_snapshot_from_capsule(
        capsule: Mapping[str, Any],
    ) -> PiSceneProviderLedgerSnapshotV1:
        return PiSceneRequestJournal._provider_ledger_snapshot_from_payload(
            capsule["provider_ledger_before"]
        )

    def prepare_transport_retry(self, retry_id: str) -> TransportRetryAuthorizationV1:
        """Read exact retry custody without consuming or dispatching it."""

        binding, entry_path = self._binding_for_retry_id(retry_id)
        with self._lock, self._claim(entry_path):
            entry = self._read_entry(entry_path, expected=binding)
            self._reconcile_terminal_transport_authority_locked(binding, entry)
            ledger = self._read_transport_ledger(binding, required=True)
            assert ledger is not None
            authorization = self._retry_authorization(
                retry_id=retry_id,
                binding=binding,
                entry=entry,
                ledger=ledger,
            )
            self._write_retry_index(binding, authorization.receipt)
            if (
                authorization.action_phase == "blocked"
                or authorization.request_entry_status == "terminal"
            ):
                self._finalize_transport_authority_locked(binding)
            return authorization

    def transport_dispatch_claim(self, retry_id: str) -> PiSceneRequestJournal._Claim:
        """Hold one OS-released exclusive lease across owner rotation and dispatch."""

        if re.fullmatch(r"retry-[a-f0-9]{64}", retry_id or "") is None:
            raise TransportRetryNotFoundError("Pi Scene transport retry identity is unavailable")
        return self._Claim(self._transport_dispatch_lock_path(retry_id), retry_id)

    def provider_dispatch_claim(self) -> PiSceneRequestJournal._Claim:
        """Serialize exact global Sol-ledger attribution across adapters/processes."""

        return self._Claim(
            self.root / "PROVIDER_LEDGER_DISPATCH.lock",
            "provider-ledger-dispatch",
        )

    def transport_dispatch_in_progress(self, retry_id: str) -> bool:
        """Observe another live POST lease without changing any retry state."""

        claim = self.transport_dispatch_claim(retry_id)
        try:
            claim.__enter__()
        except RequestReplayPendingError:
            return True
        claim.__exit__(None, None, None)
        return False

    def authorize_transport_retry(self, retry_id: str) -> TransportRetryAuthorizationV1:
        """Consume one manual action after the caller revalidates live custody."""

        binding, entry_path = self._binding_for_retry_id(retry_id)
        with self._lock, self._claim(entry_path):
            entry = self._read_entry(entry_path, expected=binding)
            ledger = self._read_transport_ledger(binding, required=True)
            assert ledger is not None
            authorization = self._retry_authorization(
                retry_id=retry_id,
                binding=binding,
                entry=entry,
                ledger=ledger,
            )
            if (
                authorization.replayed_terminal_response is not None
                or authorization.superseding_failure is not None
            ):
                return authorization
            phase = authorization.action_phase
            process_id = authorization.action_process_id
            if phase == "dispatch_started":
                raise RequestReplayPendingError(
                    "Pi Scene transport retry dispatch already has durable custody",
                    request_id=binding.request_id,
                )
            if phase not in {"eligible", "authorized", "owner_rotated"}:
                raise StateConflictError("Pi Scene transport retry is not actionable")
            if phase == "eligible" or process_id != self.process_instance_id:
                actions = list(ledger["actions"])
                active = dict(actions[-1])
                active.update(
                    {
                        "phase": "authorized" if phase == "eligible" else phase,
                        "process_instance_id": self.process_instance_id,
                        "authorized_sha256": domain_sha256(
                            "cera.pi_scene.transport_retry_authorized.v1",
                            {
                                "retry_id": retry_id,
                                "process_instance_id": self.process_instance_id,
                                "prior_phase": phase,
                            },
                        ),
                    }
                )
                actions[-1] = self._signed_retry_action(active)
                ledger = self._write_transport_ledger(binding, ledger, actions=actions)
            return self._retry_authorization(
                retry_id=retry_id,
                binding=binding,
                entry=entry,
                ledger=ledger,
            )

    def mark_transport_owner_rotated(self, retry_id: str) -> None:
        self._transition_retry_action(
            retry_id,
            expected_phases={"authorized", "owner_rotated"},
            target_phase="owner_rotated",
            evidence_field="owner_rotated_sha256",
        )

    def mark_transport_dispatch_started(
        self,
        retry_id: str,
        ledger_before: PiSceneProviderLedgerSnapshotV1,
    ) -> None:
        binding, entry_path = self._binding_for_retry_id(retry_id)
        with self._lock, self._claim(entry_path):
            ledger = self._read_transport_ledger(binding, required=True)
            assert ledger is not None
            if ledger["active_retry_id"] != retry_id:
                raise StateConflictError("Pi Scene transport retry was superseded")
            actions = list(ledger["actions"])
            active = dict(actions[-1])
            if active["phase"] == "dispatch_started":
                existing = active["dispatch_ledger_before"]
                if existing != self._provider_ledger_prefix_payload(ledger_before):
                    raise StateConflictError("Pi Scene retry dispatch ledger baseline changed")
                return
            if active["phase"] != "owner_rotated":
                raise StateConflictError("Pi Scene retry owner was not rotated before dispatch")
            if active["fresh_thread_sha256"] is None:
                raise StateConflictError("Pi Scene retry dispatch lacks its fresh Planner thread")
            baseline = self._provider_ledger_prefix_payload(ledger_before)
            active.update(
                {
                    "phase": "dispatch_started",
                    "dispatch_started_sha256": domain_sha256(
                        "cera.pi_scene.transport_retry_dispatch_started.v1",
                        {"retry_id": retry_id, "provider_ledger_before": baseline},
                    ),
                    "dispatch_ledger_before": baseline,
                }
            )
            actions[-1] = self._signed_retry_action(active)
            self._write_transport_ledger(binding, ledger, actions=actions)

    def block_transport_retry(
        self,
        retry_id: str,
        reason_code: str,
        *,
        evidence_sha256: str | None = None,
    ) -> None:
        if reason_code not in TRANSPORT_RETRY_BLOCK_REASONS:
            raise ContractValidationError("Pi Scene transport block reason is invalid")
        evidence = evidence_sha256 or domain_sha256(
            "cera.pi_scene.transport_retry_block.v1",
            {"retry_id": retry_id, "reason_code": reason_code},
        )
        if not re_is_sha256(evidence):
            raise ContractValidationError("Pi Scene transport block evidence is invalid")
        binding, entry_path = self._binding_for_retry_id(retry_id)
        with self._lock, self._claim(entry_path):
            ledger = self._read_transport_ledger(binding, required=True)
            assert ledger is not None
            if ledger["active_retry_id"] != retry_id:
                return
            actions = list(ledger["actions"])
            active = dict(actions[-1])
            if active["phase"] == "blocked":
                if (
                    active["blocked_reason_code"] != reason_code
                    or active["blocked_evidence_sha256"] != evidence
                ):
                    raise StateConflictError("Pi Scene transport block reason changed")
                self._finalize_transport_authority_locked(binding)
                return
            if active["phase"] in {"terminal_failed", "succeeded"}:
                raise StateConflictError("Pi Scene terminal retry cannot become blocked")
            active.update(
                {
                    "phase": "blocked",
                    "blocked_reason_code": reason_code,
                    "blocked_evidence_sha256": evidence,
                }
            )
            actions[-1] = self._signed_retry_action(active)
            self._write_transport_ledger(binding, ledger, actions=actions)
            self._finalize_transport_authority_locked(binding)

    def rearm_transport_retry_after_zero_dispatch(
        self,
        retry_id: str,
        ledger_current: PiSceneProviderLedgerSnapshotV1,
    ) -> None:
        """Re-expose an interrupted action only when the Sol ledger is unchanged."""

        binding, entry_path = self._binding_for_retry_id(retry_id)
        with self._lock, self._claim(entry_path):
            ledger = self._read_transport_ledger(binding, required=True)
            assert ledger is not None
            if ledger["active_retry_id"] != retry_id:
                raise StateConflictError("Pi Scene transport retry was superseded")
            actions = list(ledger["actions"])
            active = dict(actions[-1])
            if active["phase"] == "eligible":
                return
            if active["phase"] != "dispatch_started":
                raise StateConflictError("Pi Scene retry is not awaiting dispatch recovery")
            baseline = active["dispatch_ledger_before"]
            current = self._provider_ledger_prefix_payload(ledger_current)
            if baseline != current:
                raise StateConflictError("Pi Scene zero-dispatch ledger proof changed")
            active.update(
                {
                    # The failed thread was already archived before
                    # `dispatch_started`.  A zero-ledger crash may also have
                    # persisted an unused fresh thread.  Resume from the
                    # owner-rotated boundary so the original failed hash is
                    # never archived a second time against that fresh thread.
                    "phase": "owner_rotated",
                    "process_instance_id": self.process_instance_id,
                    "zero_dispatch_rearm_sha256": domain_sha256(
                        "cera.pi_scene.transport_retry_zero_dispatch_rearm.v1",
                        {"retry_id": retry_id, "provider_ledger": current},
                    ),
                }
            )
            actions[-1] = self._signed_retry_action(active)
            self._write_transport_ledger(binding, ledger, actions=actions)

    def _reconcile_terminal_transport_authority_locked(
        self,
        binding: PiSceneRequestBindingV1,
        entry: Mapping[str, Any],
    ) -> bool:
        """Repair terminal action/redaction while the exact entry claim is held."""

        if entry["status"] == "planner_result_unavailable":
            disposition = planner_result_unavailable_from_payload(entry["review_progress"])
            self._reconcile_planner_result_unavailable_locked(
                binding,
                disposition,
            )
            return not disposition.branch_dispatch_blocked
        ledger = self._read_transport_ledger(binding, required=False)
        if ledger is None:
            return False
        phase = ledger["actions"][-1]["phase"]
        if entry["status"] == "terminal":
            response_sha256 = entry["terminal_response_sha256"]
            if not re_is_sha256(response_sha256 or ""):
                raise StateConflictError("Pi Scene terminal retry response identity is invalid")
            self._mark_transport_retry_succeeded_locked(
                binding,
                response_sha256,
            )
            self._finalize_transport_authority_locked(binding)
            return True
        if phase in {"blocked", "succeeded"}:
            self._finalize_transport_authority_locked(binding)
            return True
        return False

    def inspect_transport_retry(self, binding: PiSceneRequestBindingV1) -> Mapping[str, Any]:
        """Return validated privacy-safe transport evidence for local diagnostics."""

        entry_path = self._entry_path(binding)
        with self._lock, self._claim(entry_path):
            entry = self._read_entry(entry_path, expected=binding)
            self._reconcile_terminal_transport_authority_locked(binding, entry)
            ledger = self._read_transport_ledger(binding, required=True)
            assert ledger is not None
            return dict(ledger)

    def active_transport_failure(
        self,
        binding: PiSceneRequestBindingV1,
    ) -> TransportFailureReceiptV1 | None:
        """Recover the exact visible failure after an HTTP response is lost."""

        entry_path = self._entry_path(binding)
        with self._lock, self._claim(entry_path):
            entry = self._read_entry(entry_path, expected=binding)
            self._reconcile_terminal_transport_authority_locked(binding, entry)
            ledger = self._read_transport_ledger(binding, required=False)
            if ledger is None or ledger["actions"][-1]["phase"] != "eligible":
                return None
            receipt, _ = self._decode_failure_entry(ledger["failures"][-1])
            self._write_retry_index(binding, receipt)
            return receipt

    def actionable_transport_failure_for_scope(
        self,
        *,
        session_id: str,
        world_id: str,
        branch_id: str,
    ) -> TransportFailureReceiptV1 | None:
        """Find one unresolved manual action before another branch dispatch."""

        with self._lock:
            self._reconcile_protected_retry_projections()
            found: list[TransportFailureReceiptV1] = []
            for ledger_path in self.root.rglob("*.transport.json"):
                entry_path = ledger_path.with_name(
                    ledger_path.name.removesuffix(".transport.json") + ".json"
                )
                try:
                    raw = json.loads(entry_path.read_text(encoding="utf-8"))
                    binding = _binding_from_payload(raw["binding"])
                except (OSError, UnicodeError, json.JSONDecodeError, KeyError) as exc:
                    raise StateConflictError("Pi Scene retry scope index is unreadable") from exc
                if (binding.session_id, binding.world_id, binding.branch_id) != (
                    session_id,
                    world_id,
                    branch_id,
                ):
                    continue
                with self._claim(entry_path):
                    entry = self._read_entry(entry_path, expected=binding)
                    self._reconcile_terminal_transport_authority_locked(binding, entry)
                    ledger = self._read_transport_ledger(binding, required=True)
                    assert ledger is not None
                    phase = ledger["actions"][-1]["phase"]
                    if entry["status"] == "pending" and phase in {
                        "eligible",
                        "authorized",
                        "owner_rotated",
                        "dispatch_started",
                    }:
                        receipt, _ = self._decode_failure_entry(ledger["failures"][-1])
                        self._write_retry_index(binding, receipt)
                        found.append(receipt)
            if len(found) > 1:
                raise StateConflictError(
                    "Pi Scene branch has multiple actionable transport retries"
                )
            return None if not found else found[0]

    def active_transport_dispatch_intent(self) -> TransportFailureReceiptV1 | None:
        """Return the sole crash-surviving manual dispatch barrier, if any."""

        with self._lock:
            self._reconcile_protected_retry_projections()
            found: list[TransportFailureReceiptV1] = []
            for ledger_path in sorted(self.root.rglob("*.transport.json")):
                entry_path = ledger_path.with_name(
                    ledger_path.name.removesuffix(".transport.json") + ".json"
                )
                try:
                    raw = json.loads(entry_path.read_text(encoding="utf-8"))
                    binding = _binding_from_payload(raw["binding"])
                except (OSError, UnicodeError, json.JSONDecodeError, KeyError) as exc:
                    raise StateConflictError(
                        "Pi Scene provider-dispatch intent is unreadable"
                    ) from exc
                with self._claim(entry_path):
                    entry = self._read_entry(entry_path, expected=binding)
                    self._reconcile_terminal_transport_authority_locked(binding, entry)
                    ledger = self._read_transport_ledger(binding, required=True)
                    assert ledger is not None
                    if (
                        entry["status"] == "pending"
                        and ledger["actions"][-1]["phase"] == "dispatch_started"
                    ):
                        receipt, _ = self._decode_failure_entry(ledger["failures"][-1])
                        found.append(receipt)
            if len(found) > 1:
                raise StateConflictError("Pi Scene has multiple active manual provider dispatches")
            return None if not found else found[0]

    def active_transport_dispatch_for_scope(
        self,
        *,
        world_id: str,
        branch_id: str,
        session_id: str | None = None,
    ) -> PiSceneRequestBindingV1 | None:
        """Find a staged or retried Planner dispatch for one branch only.

        Other worlds remain usable after a process crash. Exact retained-thread
        attribution prevents their later Sol events from becoming evidence for
        this branch's interrupted dispatch.
        """

        with self._lock:
            authority_root = self.protected_retry_root / "DISPATCH_AUTHORITY"
            if not authority_root.is_dir():
                return None
            found: list[PiSceneRequestBindingV1] = []
            for authority_path in sorted(authority_root.glob("request-*.json")):
                try:
                    raw = json.loads(authority_path.read_text(encoding="utf-8"))
                except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise StateConflictError(
                        "Pi Scene protected dispatch scope is unreadable"
                    ) from exc
                if not isinstance(raw, dict):
                    raise StateConflictError("Pi Scene protected dispatch scope changed shape")
                binding = _binding_from_payload(raw.get("binding"))
                if (
                    binding.world_id != world_id
                    or binding.branch_id != branch_id
                    or (session_id is not None and binding.session_id != session_id)
                ):
                    continue
                entry_path = self._entry_path(binding)
                with self._claim(entry_path):
                    entry = self._read_entry(entry_path, expected=binding)
                    if entry["status"] == "planner_result_unavailable":
                        disposition = planner_result_unavailable_from_payload(
                            entry["review_progress"]
                        )
                        self._reconcile_terminal_transport_authority_locked(
                            binding,
                            entry,
                        )
                        if disposition.branch_dispatch_blocked:
                            found.append(binding)
                        continue
                    if self._reconcile_terminal_transport_authority_locked(
                        binding,
                        entry,
                    ):
                        continue
                    authority = self._read_transport_authority(
                        binding,
                        required=True,
                    )
                    assert authority is not None
                    if authority["phase"] in {
                        "dispatch_staged",
                        "planner_completed_pending_progress",
                    }:
                        found.append(binding)
                        continue
                    ledger = authority["transport_ledger"]
                    if (
                        isinstance(ledger, dict)
                        and ledger["actions"][-1]["phase"] == "dispatch_started"
                    ):
                        found.append(binding)
            unique = {value.request_id: value for value in found}
            if len(unique) > 1:
                raise StateConflictError(
                    "Pi Scene branch has multiple interrupted provider dispatches"
                )
            return None if not unique else next(iter(unique.values()))

    def _reconcile_protected_retry_projections(self) -> None:
        authority_root = self.protected_retry_root / "DISPATCH_AUTHORITY"
        if not authority_root.is_dir():
            return
        for authority_path in sorted(authority_root.glob("request-*.json")):
            try:
                raw = json.loads(authority_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise StateConflictError(
                    "Pi Scene protected transport authority is unreadable"
                ) from exc
            if not isinstance(raw, dict):
                raise StateConflictError("Pi Scene protected transport authority shape changed")
            binding = _binding_from_payload(raw.get("binding"))
            entry_path = self._entry_path(binding)
            with self._claim(entry_path):
                entry = self._read_entry(entry_path, expected=binding)
                self._reconcile_terminal_transport_authority_locked(binding, entry)
                authority = self._read_transport_authority(binding, required=True)
                assert authority is not None
                ledger = authority["transport_ledger"]
                if ledger is None:
                    continue
                _atomic_write_json(self._transport_ledger_path(binding), ledger)
                for failure in ledger["failures"]:
                    receipt, _ = self._decode_failure_entry(failure)
                    self._write_retry_index(binding, receipt)

    def inspect(self, binding: PiSceneRequestBindingV1) -> Mapping[str, Any]:
        """Return a validated copy for diagnostics and provider-free tests."""

        path = self._entry_path(binding)
        with self._lock:
            if not path.exists():
                raise StateConflictError("Pi Scene request journal entry does not exist")
            return dict(self._read_entry(path, expected=binding))

    def entry_path(self, binding: PiSceneRequestBindingV1) -> Path:
        """Expose the deterministic local path for operational evidence only."""

        return self._entry_path(binding)

    def _pending_entry(self, binding: PiSceneRequestBindingV1) -> dict[str, Any]:
        body = {
            "schema_version": self.SCHEMA_VERSION,
            "binding": binding.to_payload(),
            "status": "pending",
            "pre_dispatch_journal_sha256": None,
            "review_progress": None,
            "terminal_response": None,
            "terminal_response_sha256": None,
        }
        return {**body, "journal_sha256": canonical_sha256(body)}

    def _resolve_existing(
        self,
        path: Path,
        binding: PiSceneRequestBindingV1,
    ) -> RequestJournalResolutionV1:
        entry = self._read_entry(path, expected=binding)
        if entry["status"] == "planner_result_unavailable":
            disposition = planner_result_unavailable_from_payload(entry["review_progress"])
            self._reconcile_planner_result_unavailable_locked(binding, disposition)
            raise PlannerResultUnavailableError(disposition)
        if entry["status"] == "rearmed":
            authority = self._read_transport_authority(binding, required=False)
            if authority is not None and authority["phase"] != "dispatch_staged":
                raise StateConflictError("Pi Scene rearmed request retained incompatible authority")
            self._transport_authority_path(binding).unlink(missing_ok=True)
            _atomic_write_json(path, self._pending_entry(binding))
            self._read_entry(path, expected=binding)
            return RequestJournalResolutionV1(
                request_id=binding.request_id,
                replayed=False,
                terminal_response=None,
                review_progress=None,
            )
        if entry["status"] == "terminal":
            return RequestJournalResolutionV1(
                request_id=binding.request_id,
                replayed=True,
                terminal_response=dict(entry["terminal_response"]),
                review_progress=dict(entry["review_progress"]),
            )
        if entry["status"] == "progressed":
            return RequestJournalResolutionV1(
                request_id=binding.request_id,
                replayed=False,
                terminal_response=None,
                review_progress=dict(entry["review_progress"]),
            )
        raise RequestReplayPendingError(
            "Pi Scene identical request has pending pre-dispatch custody; "
            "provider redispatch is blocked",
            request_id=binding.request_id,
        )

    def _entry_path(self, binding: PiSceneRequestBindingV1) -> Path:
        protected = (
            "PROTECTED_ADULT"
            if binding.route is SceneRoute.ADULT
            else "PROTECTED_AUTO"
            if binding.route_intent == "automatic"
            else "ORDINARY"
        )
        path = (
            self.root
            / protected
            / f"s-{text_sha256(binding.session_id)[:16]}"
            / f"w-{text_sha256(binding.world_id)[:16]}"
            / f"b-{text_sha256(binding.branch_id)[:16]}"
            / f"{binding.request_id}.json"
        ).resolve()
        if not path.is_relative_to(self.root):
            raise ContractValidationError("Pi Scene request journal escaped its root")
        return path

    def _transition_retry_action(
        self,
        retry_id: str,
        *,
        expected_phases: set[str],
        target_phase: str,
        evidence_field: str,
    ) -> None:
        binding, entry_path = self._binding_for_retry_id(retry_id)
        with self._lock, self._claim(entry_path):
            ledger = self._read_transport_ledger(binding, required=True)
            assert ledger is not None
            if ledger["active_retry_id"] != retry_id:
                raise StateConflictError("Pi Scene transport retry was superseded")
            actions = list(ledger["actions"])
            active = dict(actions[-1])
            if active["phase"] not in expected_phases:
                raise StateConflictError("Pi Scene transport retry phase changed")
            if active["process_instance_id"] != self.process_instance_id:
                raise StateConflictError("Pi Scene transport retry process custody changed")
            if active["phase"] == target_phase:
                return
            active.update(
                {
                    "phase": target_phase,
                    evidence_field: domain_sha256(
                        f"cera.pi_scene.transport_retry_{target_phase}.v1",
                        {"retry_id": retry_id, "process_instance_id": self.process_instance_id},
                    ),
                }
            )
            actions[-1] = self._signed_retry_action(active)
            self._write_transport_ledger(binding, ledger, actions=actions)

    def _mark_transport_retry_succeeded_locked(
        self,
        binding: PiSceneRequestBindingV1,
        terminal_response_sha256: str,
    ) -> None:
        ledger = self._read_transport_ledger(binding, required=False)
        if ledger is None:
            return
        actions = list(ledger["actions"])
        active = dict(actions[-1])
        if active["phase"] == "succeeded":
            if active["terminal_response_sha256"] != terminal_response_sha256:
                raise StateConflictError("Pi Scene retry terminal response changed")
            return
        if active["phase"] != "dispatch_started":
            raise StateConflictError("Pi Scene retry succeeded outside dispatch custody")
        active.update(
            {
                "phase": "succeeded",
                "terminal_response_sha256": terminal_response_sha256,
            }
        )
        actions[-1] = self._signed_retry_action(active)
        self._write_transport_ledger(binding, ledger, actions=actions)

    def _read_entry(
        self,
        path: Path,
        *,
        expected: PiSceneRequestBindingV1,
    ) -> dict[str, Any]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise StateConflictError("Pi Scene request journal is unreadable") from exc
        required = {
            "schema_version",
            "binding",
            "status",
            "pre_dispatch_journal_sha256",
            "review_progress",
            "terminal_response",
            "terminal_response_sha256",
            "journal_sha256",
        }
        if not isinstance(payload, dict) or set(payload) != required:
            raise StateConflictError("Pi Scene request journal shape changed")
        if payload["schema_version"] != self.SCHEMA_VERSION:
            raise StateConflictError("Pi Scene request journal schema changed")
        body = {key: payload[key] for key in required if key != "journal_sha256"}
        if payload["journal_sha256"] != canonical_sha256(body):
            raise StateConflictError("Pi Scene request journal integrity changed")
        recovered = _binding_from_payload(payload["binding"])
        if recovered != expected:
            raise StateConflictError("Pi Scene request journal binding changed")
        status = payload["status"]
        pre_dispatch_sha = payload["pre_dispatch_journal_sha256"]
        review_progress = payload["review_progress"]
        response = payload["terminal_response"]
        response_sha = payload["terminal_response_sha256"]
        if status == "pending":
            if (
                pre_dispatch_sha is not None
                or review_progress is not None
                or response is not None
                or response_sha is not None
            ):
                raise StateConflictError("Pi Scene pending journal contains a response")
        elif status == "rearmed":
            expected_pending_sha = self._pending_entry(expected)["journal_sha256"]
            if pre_dispatch_sha != expected_pending_sha:
                raise StateConflictError("Pi Scene zero-call rearm lost pending custody")
            _validate_zero_call_rearm(review_progress, expected)
            if response is not None or response_sha is not None:
                raise StateConflictError("Pi Scene rearmed journal contains a response")
        elif status == "progressed":
            expected_pending_sha = self._pending_entry(expected)["journal_sha256"]
            if pre_dispatch_sha != expected_pending_sha:
                raise StateConflictError("Pi Scene pre-dispatch custody binding changed")
            validate_request_progress(review_progress, expected)
            if response is not None or response_sha is not None:
                raise StateConflictError("Pi Scene progressed journal contains a response")
        elif status == "terminal":
            expected_pending_sha = self._pending_entry(expected)["journal_sha256"]
            if pre_dispatch_sha != expected_pending_sha:
                raise StateConflictError("Pi Scene pre-dispatch custody binding changed")
            validate_request_progress(review_progress, expected)
            if not isinstance(response, dict) or not re_is_sha256(response_sha or ""):
                raise StateConflictError("Pi Scene terminal journal response is invalid")
            if canonical_sha256(response) != response_sha:
                raise StateConflictError("Pi Scene terminal response binding changed")
        elif status == "planner_result_unavailable":
            expected_pending_sha = self._pending_entry(expected)["journal_sha256"]
            if pre_dispatch_sha != expected_pending_sha:
                raise StateConflictError("Pi Scene unavailable Planner result lost custody")
            disposition = planner_result_unavailable_from_payload(review_progress)
            if disposition.completion.request_id != expected.request_id:
                raise StateConflictError("Pi Scene unavailable Planner result changed request")
            if response is not None or response_sha is not None:
                raise StateConflictError("Pi Scene unavailable Planner result contains response")
        else:
            raise StateConflictError("Pi Scene request journal status changed")
        return payload

    class _Claim:
        def __init__(self, path: Path, request_id: str) -> None:
            self.path = path
            self.request_id = request_id
            self.descriptor: int | None = None

        def __enter__(self) -> None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            try:
                descriptor = os.open(
                    self.path,
                    os.O_CREAT | os.O_RDWR | getattr(os, "O_BINARY", 0),
                    0o600,
                )
                if os.fstat(descriptor).st_size == 0:
                    os.write(descriptor, b"\0")
                    os.fsync(descriptor)
                os.lseek(descriptor, 0, os.SEEK_SET)
                _lock_claim_descriptor(descriptor)
            except OSError as exc:
                try:
                    os.close(descriptor)
                except (UnboundLocalError, OSError):
                    pass
                raise RequestReplayPendingError(
                    "Pi Scene request journal claim already exists; redispatch is blocked",
                    request_id=self.request_id,
                ) from exc
            try:
                os.lseek(descriptor, 0, os.SEEK_SET)
                current = os.read(descriptor, 32)
                if current not in {b"\0", b"lock-v2\n"}:
                    raise RequestReplayPendingError(
                        "Pi Scene legacy request claim cannot be recovered automatically",
                        request_id=self.request_id,
                    )
                if current == b"\0":
                    os.lseek(descriptor, 0, os.SEEK_SET)
                    os.write(descriptor, b"lock-v2\n")
                    os.ftruncate(descriptor, len(b"lock-v2\n"))
                    os.fsync(descriptor)
            except BaseException:
                _unlock_claim_descriptor(descriptor)
                os.close(descriptor)
                raise
            self.descriptor = descriptor

        def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
            del exc_type, exc, traceback
            descriptor = self.descriptor
            if descriptor is not None:
                _unlock_claim_descriptor(descriptor)
                os.close(descriptor)
                self.descriptor = None

    def _claim(self, entry_path: Path) -> PiSceneRequestJournal._Claim:
        return self._Claim(entry_path.with_suffix(".claim"), entry_path.stem)


def _validate_zero_call_rearm(
    value: Any,
    binding: PiSceneRequestBindingV1,
) -> None:
    required = {
        "schema_version",
        "request_id",
        "capsule_sha256",
        "planner_thread_sha256",
        "provider_ledger",
        "rearm_sha256",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise StateConflictError("Pi Scene zero-call rearm shape changed")
    body = {key: value[key] for key in required if key != "rearm_sha256"}
    planner_thread = value["planner_thread_sha256"]
    if (
        value["schema_version"] != "cera.pi_scene.zero_call_rearm.v1"
        or value["request_id"] != binding.request_id
        or not re_is_sha256(value["capsule_sha256"] or "")
        or (planner_thread is not None and not re_is_sha256(planner_thread))
        or value["rearm_sha256"] != canonical_sha256(body)
    ):
        raise StateConflictError("Pi Scene zero-call rearm changed")
    PiSceneRequestJournal._validate_provider_ledger_prefix_payload(value["provider_ledger"])


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # The journal target already contains the complete request digest. Repeating
    # that long target name in the staging file can cross the legacy Windows
    # path boundary even when the durable target itself is valid. Keep the
    # collision-resistant staging identity independent from the target name.
    temporary = path.parent / f".request-journal-{uuid4().hex}.tmp"
    data = canonical_bytes(payload) + b"\n"
    try:
        with temporary.open("xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _lock_claim_descriptor(descriptor: int) -> None:
    """Acquire a crash-released non-blocking OS lock for one journal entry."""

    if os.name == "nt":
        import msvcrt

        try:
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        except OSError:
            raise
        return
    fcntl = __import__("fcntl")
    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock_claim_descriptor(descriptor: int) -> None:
    if os.name == "nt":
        import msvcrt

        os.lseek(descriptor, 0, os.SEEK_SET)
        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        return
    fcntl = __import__("fcntl")
    fcntl.flock(descriptor, fcntl.LOCK_UN)
