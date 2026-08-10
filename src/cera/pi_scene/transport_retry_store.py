"""Lock-free protected custody and derived retry sidecar storage.

Only :class:`PiSceneRequestJournal` exposes public transitions and owns entry
claims/OS leases. This mixin performs deterministic decoding and sidecar I/O;
callers must already hold the facade's request-entry claim for every mutation.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from cera.errors import ContractValidationError, StateConflictError
from cera.serialization import (
    bytes_sha256,
    canonical_bytes,
    canonical_sha256,
    re_is_sha256,
    to_primitive,
)

from .request_binding import PiSceneRequestBindingV1, _binding_from_payload
from .transport_retry import (
    TRANSPORT_RETRY_BLOCK_REASONS,
    TRANSPORT_RETRY_OWNER,
    TRANSPORT_RETRY_PHASES,
    PiSceneProviderLedgerSnapshotV1,
    PiSceneZeroEffectProofV1,
    TransportFailureReceiptV1,
    effect_snapshot_from_payload,
    planner_completion_marker_from_payload,
    transport_failure_from_payload,
    zero_effect_proof_from_payload,
)


class TransportRetryNotFoundError(StateConflictError):
    """A malformed or unknown public retry identity, without existence detail."""


@dataclass(frozen=True, slots=True)
class TransportRetryAuthorizationV1:
    """Exact normalized request released by one durable manual action."""

    retry_id: str
    receipt: TransportFailureReceiptV1
    binding: PiSceneRequestBindingV1
    normalized_request: Mapping[str, Any]
    logic_owner: str
    effect_proof: PiSceneZeroEffectProofV1
    action_phase: str
    action_process_id: str | None
    dispatch_ledger_before: Mapping[str, Any] | None = None
    fresh_thread_sha256: str | None = None
    blocked_reason_code: str | None = None
    request_entry_status: str = "pending"
    replayed_terminal_response: Mapping[str, Any] | None = None
    superseding_failure: TransportFailureReceiptV1 | None = None


class TransportRetryStoreMixin:
    """Internal storage implementation mixed into the locking journal facade."""

    root: Path
    protected_retry_root: Path
    process_instance_id: str

    def _entry_path(self, binding: PiSceneRequestBindingV1) -> Path:
        raise NotImplementedError

    def _read_entry(
        self,
        path: Path,
        *,
        expected: PiSceneRequestBindingV1,
    ) -> dict[str, Any]:
        raise NotImplementedError

    def _request_custody_path(self, binding: PiSceneRequestBindingV1) -> Path:
        """Legacy pre-capsule custody path retained read-only."""

        path = (
            self.protected_retry_root / "REQUEST_CUSTODY" / f"{binding.request_id}.request.json"
        ).resolve()
        if not path.is_relative_to(self.protected_retry_root):
            raise ContractValidationError("Pi Scene protected request custody escaped its root")
        return path

    def _transport_authority_path(self, binding: PiSceneRequestBindingV1) -> Path:
        path = (
            self.protected_retry_root / "DISPATCH_AUTHORITY" / f"{binding.request_id}.json"
        ).resolve()
        if not path.is_relative_to(self.protected_retry_root):
            raise ContractValidationError("Pi Scene transport authority escaped its root")
        return path

    def _transport_ledger_path(self, binding: PiSceneRequestBindingV1) -> Path:
        return self._entry_path(binding).with_suffix(".transport.json")

    def _ensure_request_custody(
        self,
        binding: PiSceneRequestBindingV1,
        normalized_request: Mapping[str, Any],
    ) -> dict[str, Any]:
        value = to_primitive(normalized_request)
        if not isinstance(value, dict):
            raise ContractValidationError("Pi Scene normalized retry request must be an object")
        request_bytes = canonical_bytes(value)
        if (
            bytes_sha256(request_bytes) != binding.normalized_request_sha256
            or len(request_bytes) != binding.normalized_request_size_bytes
        ):
            raise ContractValidationError("Pi Scene normalized retry request changed")
        body = {
            "schema_version": "cera.pi_scene.normalized_request_custody.v1",
            "binding": binding.to_payload(),
            "normalized_request": value,
            "normalized_request_sha256": binding.normalized_request_sha256,
            "normalized_request_size_bytes": binding.normalized_request_size_bytes,
        }
        payload = {**body, "custody_sha256": canonical_sha256(body)}
        return payload

    def _request_custody(self, binding: PiSceneRequestBindingV1) -> dict[str, Any]:
        authority_path = self._transport_authority_path(binding)
        if authority_path.exists():
            authority = self._read_transport_authority(binding, required=True)
            assert authority is not None
            normalized = authority["normalized_request"]
            if not isinstance(normalized, dict):
                raise StateConflictError(
                    "Pi Scene terminal transport authority no longer retains request bytes"
                )
            payload = {
                "schema_version": "cera.pi_scene.normalized_request_custody.v1",
                "binding": authority["binding"],
                "normalized_request": normalized,
                "normalized_request_sha256": authority["normalized_request_sha256"],
                "normalized_request_size_bytes": authority["normalized_request_size_bytes"],
                "custody_sha256": authority["request_custody_sha256"],
            }
            return self._validate_request_custody(binding, payload)
        path = self._request_custody_path(binding)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise StateConflictError("Pi Scene normalized request custody is unavailable") from exc
        return self._validate_request_custody(binding, payload)

    @staticmethod
    def _validate_request_custody(
        binding: PiSceneRequestBindingV1,
        payload: Any,
    ) -> dict[str, Any]:
        required = {
            "schema_version",
            "binding",
            "normalized_request",
            "normalized_request_sha256",
            "normalized_request_size_bytes",
            "custody_sha256",
        }
        if not isinstance(payload, dict) or set(payload) != required:
            raise StateConflictError("Pi Scene normalized request custody shape changed")
        body = {key: payload[key] for key in required if key != "custody_sha256"}
        if (
            payload["schema_version"] != "cera.pi_scene.normalized_request_custody.v1"
            or payload["custody_sha256"] != canonical_sha256(body)
            or _binding_from_payload(payload["binding"]) != binding
            or payload["normalized_request_sha256"] != binding.normalized_request_sha256
            or payload["normalized_request_size_bytes"] != binding.normalized_request_size_bytes
            or not isinstance(payload["normalized_request"], dict)
        ):
            raise StateConflictError("Pi Scene normalized request custody changed")
        request_bytes = canonical_bytes(payload["normalized_request"])
        if (
            bytes_sha256(request_bytes) != binding.normalized_request_sha256
            or len(request_bytes) != binding.normalized_request_size_bytes
        ):
            raise StateConflictError("Pi Scene normalized request bytes changed")
        return payload

    def _read_transport_authority(
        self,
        binding: PiSceneRequestBindingV1,
        *,
        required: bool,
    ) -> dict[str, Any] | None:
        path = self._transport_authority_path(binding)
        if not path.exists():
            if required:
                raise StateConflictError("Pi Scene protected transport authority is unavailable")
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise StateConflictError(
                "Pi Scene protected transport authority is unreadable"
            ) from exc
        required_fields = {
            "schema_version",
            "binding",
            "normalized_request",
            "normalized_request_sha256",
            "normalized_request_size_bytes",
            "request_custody_sha256",
            "phase",
            "dispatch_capsule",
            "transport_ledger",
            "authority_sha256",
        }
        if not isinstance(payload, dict) or set(payload) != required_fields:
            raise StateConflictError("Pi Scene protected transport authority shape changed")
        body = {key: payload[key] for key in required_fields if key != "authority_sha256"}
        recovered = _binding_from_payload(payload["binding"])
        if (
            payload["schema_version"] != "cera.pi_scene.transport_dispatch_authority.v1"
            or recovered != binding
            or payload["authority_sha256"] != canonical_sha256(body)
            or payload["normalized_request_sha256"] != binding.normalized_request_sha256
            or payload["normalized_request_size_bytes"] != binding.normalized_request_size_bytes
            or not re_is_sha256(payload["request_custody_sha256"] or "")
            or payload["phase"]
            not in {
                "dispatch_staged",
                "planner_completed_pending_progress",
                "failure_eligible",
                "terminal_redacted",
            }
            or (
                payload["phase"] in {"planner_completed_pending_progress", "terminal_redacted"}
                and payload["normalized_request"] is not None
            )
            or (
                payload["phase"] not in {"planner_completed_pending_progress", "terminal_redacted"}
                and not isinstance(payload["normalized_request"], dict)
            )
        ):
            raise StateConflictError("Pi Scene protected transport authority changed")
        if payload["normalized_request"] is not None:
            custody = {
                "schema_version": "cera.pi_scene.normalized_request_custody.v1",
                "binding": payload["binding"],
                "normalized_request": payload["normalized_request"],
                "normalized_request_sha256": payload["normalized_request_sha256"],
                "normalized_request_size_bytes": payload["normalized_request_size_bytes"],
                "custody_sha256": payload["request_custody_sha256"],
            }
            self._validate_request_custody(binding, custody)
        capsule = payload["dispatch_capsule"]
        if payload["phase"] == "dispatch_staged":
            self._validate_dispatch_capsule(binding, capsule)
        elif payload["phase"] == "planner_completed_pending_progress":
            marker = planner_completion_marker_from_payload(capsule)
            if marker.request_id != binding.request_id:
                raise StateConflictError("Pi Scene Planner completion changed request custody")
        elif capsule is not None:
            raise StateConflictError("Pi Scene terminal transport capsule was retained")
        ledger = payload["transport_ledger"]
        if payload["phase"] == "failure_eligible" and not isinstance(ledger, dict):
            raise StateConflictError("Pi Scene failure authority lacks retry ledger")
        if ledger is not None:
            self._validate_transport_ledger_payload(
                binding,
                ledger,
                expected_custody_sha256=payload["request_custody_sha256"],
            )
        return payload

    def _write_transport_authority(
        self,
        binding: PiSceneRequestBindingV1,
        *,
        custody: Mapping[str, Any] | None,
        phase: str,
        dispatch_capsule: Mapping[str, Any] | None,
        transport_ledger: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        existing = self._read_transport_authority(binding, required=False)
        if custody is None:
            if existing is None:
                raise StateConflictError("Pi Scene transport authority lacks request custody")
            binding_payload = existing["binding"]
            normalized_request = existing["normalized_request"]
            request_sha256 = existing["normalized_request_sha256"]
            request_size = existing["normalized_request_size_bytes"]
            custody_sha256 = existing["request_custody_sha256"]
        else:
            binding_payload = custody["binding"]
            normalized_request = custody["normalized_request"]
            request_sha256 = custody["normalized_request_sha256"]
            request_size = custody["normalized_request_size_bytes"]
            custody_sha256 = custody["custody_sha256"]
            if existing is not None and (
                existing["binding"] != binding_payload
                or existing["normalized_request_sha256"] != request_sha256
                or existing["normalized_request_size_bytes"] != request_size
                or existing["request_custody_sha256"] != custody_sha256
                or (
                    existing["normalized_request"] is not None
                    and existing["normalized_request"] != normalized_request
                )
            ):
                raise StateConflictError("Pi Scene protected request custody changed")
        if phase in {"planner_completed_pending_progress", "terminal_redacted"}:
            normalized_request = None
        if phase == "terminal_redacted":
            dispatch_capsule = None
        body = {
            "schema_version": "cera.pi_scene.transport_dispatch_authority.v1",
            "binding": binding_payload,
            "normalized_request": normalized_request,
            "normalized_request_sha256": request_sha256,
            "normalized_request_size_bytes": request_size,
            "request_custody_sha256": custody_sha256,
            "phase": phase,
            "dispatch_capsule": (None if dispatch_capsule is None else dict(dispatch_capsule)),
            "transport_ledger": (None if transport_ledger is None else dict(transport_ledger)),
        }
        payload = {**body, "authority_sha256": canonical_sha256(body)}
        _atomic_write_json(self._transport_authority_path(binding), payload)
        recovered = self._read_transport_authority(binding, required=True)
        assert recovered is not None
        return recovered

    def _finalize_transport_authority_locked(
        self,
        binding: PiSceneRequestBindingV1,
    ) -> None:
        """Remove raw transient custody after exact terminal response storage."""

        authority = self._read_transport_authority(binding, required=False)
        if authority is None:
            return
        ledger = authority["transport_ledger"]
        if ledger is None:
            if authority["phase"] == "terminal_redacted":
                return
            self._transport_authority_path(binding).unlink(missing_ok=True)
            return
        self._write_transport_authority(
            binding,
            custody=None,
            phase="terminal_redacted",
            dispatch_capsule=None,
            transport_ledger=ledger,
        )

    def _redact_unavailable_planner_authority_locked(
        self,
        binding: PiSceneRequestBindingV1,
    ) -> None:
        """Retain only a hash-bound terminal marker for an unavailable result."""

        authority = self._read_transport_authority(binding, required=False)
        if authority is None:
            return
        self._write_transport_authority(
            binding,
            custody=None,
            phase="terminal_redacted",
            dispatch_capsule=None,
            transport_ledger=authority["transport_ledger"],
        )

    @staticmethod
    def _validate_dispatch_capsule(
        binding: PiSceneRequestBindingV1,
        value: Any,
    ) -> None:
        required = {
            "schema_version",
            "request_id",
            "logic_owner",
            "resolved_route",
            "turn_context_sha256",
            "effect_before",
            "planner_thread_sha256",
            "provider_ledger_before",
            "capsule_sha256",
        }
        if not isinstance(value, dict) or set(value) != required:
            raise StateConflictError("Pi Scene transport dispatch capsule shape changed")
        body = {key: value[key] for key in required if key != "capsule_sha256"}
        ledger = value["provider_ledger_before"]
        if (
            value["schema_version"] != "cera.pi_scene.transport_dispatch_capsule.v1"
            or value["request_id"] != binding.request_id
            or value["logic_owner"] != TRANSPORT_RETRY_OWNER
            or value["resolved_route"] not in {"ordinary", "adult"}
            or not re_is_sha256(value["turn_context_sha256"] or "")
            or (
                value["planner_thread_sha256"] is not None
                and not re_is_sha256(value["planner_thread_sha256"])
            )
            or value["capsule_sha256"] != canonical_sha256(body)
        ):
            raise StateConflictError("Pi Scene transport dispatch capsule changed")
        effect_snapshot_from_payload(value["effect_before"])
        TransportRetryStoreMixin._provider_ledger_snapshot_from_payload(ledger)

    @staticmethod
    def _provider_ledger_snapshot_payload(
        value: PiSceneProviderLedgerSnapshotV1,
    ) -> dict[str, Any]:
        events = [dict(event) for event in value.events]
        body = {
            "schema_version": PiSceneProviderLedgerSnapshotV1.SCHEMA_VERSION,
            "dispatched_call_count": value.dispatched_call_count,
            "events": events,
        }
        return {**body, "events_sha256": canonical_sha256(tuple(events))}

    @staticmethod
    def _provider_ledger_snapshot_from_payload(
        value: Any,
    ) -> PiSceneProviderLedgerSnapshotV1:
        required = {
            "schema_version",
            "dispatched_call_count",
            "events",
            "events_sha256",
        }
        if (
            not isinstance(value, dict)
            or set(value) != required
            or value["schema_version"] != PiSceneProviderLedgerSnapshotV1.SCHEMA_VERSION
            or not isinstance(value["events"], list)
            or value["events_sha256"] != canonical_sha256(tuple(value["events"]))
        ):
            raise StateConflictError("Pi Scene dispatch-capsule provider ledger changed")
        try:
            return PiSceneProviderLedgerSnapshotV1(
                schema_version=value["schema_version"],
                dispatched_call_count=value["dispatched_call_count"],
                events=tuple(value["events"]),
            )
        except ContractValidationError as exc:
            raise StateConflictError(
                "Pi Scene dispatch-capsule provider ledger is invalid"
            ) from exc

    def _read_transport_ledger(
        self,
        binding: PiSceneRequestBindingV1,
        *,
        required: bool,
    ) -> dict[str, Any] | None:
        path = self._transport_ledger_path(binding)
        authority = self._read_transport_authority(binding, required=False)
        authority_ledger = None if authority is None else authority["transport_ledger"]
        if authority_ledger is not None:
            assert authority is not None
            if path.exists():
                try:
                    safe_payload = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                    safe_payload = None
                if safe_payload != authority_ledger:
                    _atomic_write_json(path, authority_ledger)
            else:
                _atomic_write_json(path, authority_ledger)
            return self._validate_transport_ledger_payload(
                binding,
                authority_ledger,
                expected_custody_sha256=authority["request_custody_sha256"],
            )
        if not path.exists():
            if required:
                raise StateConflictError("Pi Scene transport retry ledger is unavailable")
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise StateConflictError("Pi Scene transport retry ledger is unreadable") from exc
        legacy_custody = self._request_custody(binding)
        return self._validate_transport_ledger_payload(
            binding,
            payload,
            expected_custody_sha256=legacy_custody["custody_sha256"],
        )

    def _validate_transport_ledger_payload(
        self,
        binding: PiSceneRequestBindingV1,
        payload: Any,
        *,
        expected_custody_sha256: str,
    ) -> dict[str, Any]:
        required_fields = {
            "schema_version",
            "request_id",
            "request_custody_sha256",
            "failures",
            "actions",
            "active_retry_id",
            "ledger_sha256",
        }
        if not isinstance(payload, dict) or set(payload) != required_fields:
            raise StateConflictError("Pi Scene transport retry ledger shape changed")
        body = {key: payload[key] for key in required_fields if key != "ledger_sha256"}
        if (
            payload["schema_version"] != "cera.pi_scene.transport_retry_ledger.v2"
            or payload["request_id"] != binding.request_id
            or payload["ledger_sha256"] != canonical_sha256(body)
            or payload["request_custody_sha256"] != expected_custody_sha256
            or not isinstance(payload["failures"], list)
            or not payload["failures"]
            or not isinstance(payload["actions"], list)
            or len(payload["actions"]) != len(payload["failures"])
        ):
            raise StateConflictError("Pi Scene transport retry ledger changed")
        decoded_failures = tuple(self._decode_failure_entry(value) for value in payload["failures"])
        failures = tuple(value[0] for value in decoded_failures)
        proofs = tuple(value[1] for value in decoded_failures)
        if tuple(value.failure_number for value in failures) != tuple(range(1, len(failures) + 1)):
            raise StateConflictError("Pi Scene transport failure sequence changed")
        first_proof = proofs[0]
        for index, (receipt, proof) in enumerate(decoded_failures):
            expected_predecessor = None if index == 0 else failures[index - 1].retry_id
            if (
                receipt.predecessor_retry_id != expected_predecessor
                or proof.request_id != binding.request_id
                or proof.resolved_route != first_proof.resolved_route
                or proof.turn_context_sha256 != first_proof.turn_context_sha256
                or proof.before_snapshot != first_proof.before_snapshot
                or proof.after_snapshot != first_proof.after_snapshot
            ):
                raise StateConflictError(
                    "Pi Scene transport retry proof chain changed request custody"
                )
            if index:
                prior = proofs[index - 1].provider_failure_evidence
                current = proof.provider_failure_evidence
                bridge = payload["failures"][index].get("provider_prefix_bridge")
                if bridge is None:
                    if (
                        current.before_event_count != prior.after_event_count
                        or current.before_events_sha256 != prior.after_events_sha256
                        or current.dispatched_calls_before != prior.dispatched_calls_after
                    ):
                        raise StateConflictError(
                            "Pi Scene transport retry provider proof gap is unbound"
                        )
                else:
                    self._validate_provider_prefix_bridge(
                        bridge,
                        prior_proof=proofs[index - 1],
                        current_proof=proof,
                    )
                if (
                    current.call_events[0].get("route") != prior.call_events[0].get("route")
                    or current.call_events[0].get("model") != prior.call_events[0].get("model")
                    or current.call_events[0].get("effort") != prior.call_events[0].get("effort")
                ):
                    raise StateConflictError(
                        "Pi Scene transport retry provider proof chain changed"
                    )
        thread_hashes = tuple(
            proof.provider_failure_evidence.stored_thread_sha256 for proof in proofs
        )
        if len(thread_hashes) != len(set(thread_hashes)):
            raise StateConflictError("Pi Scene transport retry reused a failed Planner thread")
        if payload["active_retry_id"] != failures[-1].retry_id:
            raise StateConflictError("Pi Scene active transport retry changed")
        actions = tuple(self._validate_retry_action(value) for value in payload["actions"])
        if tuple(value["retry_id"] for value in actions) != tuple(
            value.retry_id for value in failures
        ):
            raise StateConflictError("Pi Scene transport retry actions changed order")
        for index, action in enumerate(actions[:-1]):
            if (
                action["phase"] != "terminal_failed"
                or action["successor_retry_id"] != failures[index + 1].retry_id
            ):
                raise StateConflictError("Pi Scene prior retry did not bind its successor")
        if actions[-1]["phase"] == "terminal_failed":
            raise StateConflictError("Pi Scene active retry cannot be terminal-failed")
        return payload

    def _attach_transport_retry_trace(
        self,
        binding: PiSceneRequestBindingV1,
        response: dict[str, Any],
    ) -> dict[str, Any]:
        ledger = self._read_transport_ledger(binding, required=False)
        if ledger is None:
            return response
        if ledger["actions"][-1]["phase"] not in {"dispatch_started", "succeeded"}:
            raise StateConflictError(
                "Pi Scene terminal response lacks an authorized transport retry"
            )
        failures = tuple(self._decode_failure_entry(value)[0] for value in ledger["failures"])
        cera = response.get("cera")
        if not isinstance(cera, dict):
            raise StateConflictError("Pi Scene transport retry response lacks its CERA envelope")
        response_request_id = cera.get("request_id")
        if response_request_id not in {None, binding.request_id}:
            raise StateConflictError("Pi Scene transport retry response changed request identity")
        attempts = [
            {
                "failure_number": value.failure_number,
                "retry_id": value.retry_id,
                "logic_owner": value.logic_owner,
                "provider_operation_submitted": (value.provider_operations_observed == 1),
                "provider_operations_observed": value.provider_operations_observed,
                "effect_proof_sha256": value.effect_proof_sha256,
                "failure_receipt_sha256": value.failure_receipt_sha256,
            }
            for value in failures
        ]
        owner_totals = {
            TRANSPORT_RETRY_OWNER: sum(value.provider_operations_observed for value in failures)
        }
        summary = {
            "schema_version": "cera.pi_scene.transport_retry_summary.v1",
            "completed_after_manual_transport_retry": True,
            "manual_retry_count": len(failures),
            "prior_failed_provider_operations": sum(
                value.provider_operations_observed for value in failures
            ),
            "prior_failed_operations_by_owner": owner_totals,
        }
        if "transport_retry_attempts" in cera or "transport_retry_summary" in cera:
            if (
                cera.get("request_id") != binding.request_id
                or cera.get("transport_retry_attempts") != attempts
                or cera.get("transport_retry_summary") != summary
            ):
                raise StateConflictError("Pi Scene transport retry trace changed")
            return response
        return {
            **response,
            "cera": {
                **cera,
                "request_id": binding.request_id,
                "transport_retry_attempts": attempts,
                "transport_retry_summary": summary,
            },
        }

    def _retry_authorization(
        self,
        *,
        retry_id: str,
        binding: PiSceneRequestBindingV1,
        entry: Mapping[str, Any],
        ledger: Mapping[str, Any],
    ) -> TransportRetryAuthorizationV1:
        decoded = tuple(self._decode_failure_entry(value) for value in ledger["failures"])
        matches = tuple(value for value in decoded if value[0].retry_id == retry_id)
        if len(matches) != 1:
            raise StateConflictError("Pi Scene transport retry identity is not active")
        receipt, proof = matches[0]
        action = next(value for value in ledger["actions"] if value["retry_id"] == retry_id)
        if entry["status"] == "terminal":
            return TransportRetryAuthorizationV1(
                retry_id=retry_id,
                receipt=receipt,
                binding=binding,
                normalized_request={},
                logic_owner=receipt.logic_owner,
                effect_proof=proof,
                action_phase="succeeded",
                action_process_id=action["process_instance_id"],
                dispatch_ledger_before=action["dispatch_ledger_before"],
                fresh_thread_sha256=action["fresh_thread_sha256"],
                blocked_reason_code=action["blocked_reason_code"],
                request_entry_status="terminal",
                replayed_terminal_response=dict(entry["terminal_response"]),
            )
        if action["phase"] == "blocked":
            return TransportRetryAuthorizationV1(
                retry_id=retry_id,
                receipt=receipt,
                binding=binding,
                normalized_request={},
                logic_owner=receipt.logic_owner,
                effect_proof=proof,
                action_phase="blocked",
                action_process_id=action["process_instance_id"],
                dispatch_ledger_before=action["dispatch_ledger_before"],
                fresh_thread_sha256=action["fresh_thread_sha256"],
                blocked_reason_code=action["blocked_reason_code"],
                request_entry_status=str(entry["status"]),
            )
        if entry["status"] == "progressed":
            return TransportRetryAuthorizationV1(
                retry_id=retry_id,
                receipt=receipt,
                binding=binding,
                normalized_request={},
                logic_owner=receipt.logic_owner,
                effect_proof=proof,
                action_phase=action["phase"],
                action_process_id=action["process_instance_id"],
                dispatch_ledger_before=action["dispatch_ledger_before"],
                fresh_thread_sha256=action["fresh_thread_sha256"],
                blocked_reason_code=action["blocked_reason_code"],
                request_entry_status="progressed",
            )
        custody = self._request_custody(binding)
        if ledger["active_retry_id"] != retry_id:
            successor_id = action["successor_retry_id"]
            successors = tuple(value[0] for value in decoded if value[0].retry_id == successor_id)
            if action["phase"] != "terminal_failed" or len(successors) != 1:
                raise StateConflictError("Pi Scene transport retry was superseded")
            # Reconcile a crash after the append-only ledger write but before
            # the derived lookup index was published. Never expose a successor
            # action that cannot itself be resolved.
            self._write_retry_index(binding, successors[0])
            return TransportRetryAuthorizationV1(
                retry_id=retry_id,
                receipt=receipt,
                binding=binding,
                normalized_request=dict(custody["normalized_request"]),
                logic_owner=receipt.logic_owner,
                effect_proof=proof,
                action_phase="terminal_failed",
                action_process_id=action["process_instance_id"],
                dispatch_ledger_before=action["dispatch_ledger_before"],
                fresh_thread_sha256=action["fresh_thread_sha256"],
                blocked_reason_code=action["blocked_reason_code"],
                request_entry_status=str(entry["status"]),
                superseding_failure=successors[0],
            )
        return TransportRetryAuthorizationV1(
            retry_id=retry_id,
            receipt=receipt,
            binding=binding,
            normalized_request=dict(custody["normalized_request"]),
            logic_owner=receipt.logic_owner,
            effect_proof=proof,
            action_phase=action["phase"],
            action_process_id=action["process_instance_id"],
            dispatch_ledger_before=action["dispatch_ledger_before"],
            fresh_thread_sha256=action["fresh_thread_sha256"],
            blocked_reason_code=action["blocked_reason_code"],
            request_entry_status=str(entry["status"]),
        )

    def _decode_failure_entry(
        self,
        value: Any,
    ) -> tuple[TransportFailureReceiptV1, PiSceneZeroEffectProofV1]:
        if not isinstance(value, dict) or frozenset(value) not in {
            frozenset({"receipt", "effect_proof"}),
            frozenset({"receipt", "effect_proof", "provider_prefix_bridge"}),
        }:
            raise StateConflictError("Pi Scene transport failure entry shape changed")
        receipt = transport_failure_from_payload(value["receipt"])
        proof = zero_effect_proof_from_payload(value["effect_proof"])
        if (
            proof.request_id != receipt.request_id
            or proof.logic_owner != receipt.logic_owner
            or proof.proof_sha256 != receipt.effect_proof_sha256
            or int(proof.provider_failure_evidence.provider_operation_submitted)
            != receipt.provider_operations_observed
        ):
            raise StateConflictError("Pi Scene failure receipt lost its full proof")
        return receipt, proof

    @staticmethod
    def _provider_prefix_bridge(
        prior_proof: PiSceneZeroEffectProofV1,
        current_before: PiSceneProviderLedgerSnapshotV1,
    ) -> dict[str, Any]:
        """Bind legitimate append-only Sol events between retry attempts."""

        prior = prior_proof.provider_failure_evidence
        if current_before.event_count < prior.after_event_count:
            raise StateConflictError("Pi Scene retry Sol prefix moved backwards")
        prior_prefix = current_before.events[: prior.after_event_count]
        if (
            canonical_sha256(tuple(dict(value) for value in prior_prefix))
            != prior.after_events_sha256
            or _transport_invocation_count(prior_prefix) != prior.dispatched_calls_after
        ):
            raise StateConflictError("Pi Scene retry Sol prefix lost its predecessor")
        intervening = tuple(current_before.events[prior.after_event_count :])
        body = {
            "schema_version": "cera.pi_scene.provider_prefix_bridge.v1",
            "prior_after_event_count": prior.after_event_count,
            "prior_after_events_sha256": prior.after_events_sha256,
            "prior_dispatched_calls_after": prior.dispatched_calls_after,
            "current_before_event_count": current_before.event_count,
            "current_before_events_sha256": current_before.events_sha256,
            "current_dispatched_calls_before": current_before.dispatched_call_count,
            "intervening_events": [dict(value) for value in intervening],
        }
        return {**body, "bridge_sha256": canonical_sha256(body)}

    @staticmethod
    def _validate_provider_prefix_bridge(
        value: Any,
        *,
        prior_proof: PiSceneZeroEffectProofV1,
        current_proof: PiSceneZeroEffectProofV1,
    ) -> None:
        required = {
            "schema_version",
            "prior_after_event_count",
            "prior_after_events_sha256",
            "prior_dispatched_calls_after",
            "current_before_event_count",
            "current_before_events_sha256",
            "current_dispatched_calls_before",
            "intervening_events",
            "bridge_sha256",
        }
        if not isinstance(value, dict) or set(value) != required:
            raise StateConflictError("Pi Scene provider prefix bridge shape changed")
        body = {key: value[key] for key in required if key != "bridge_sha256"}
        prior = prior_proof.provider_failure_evidence
        current = current_proof.provider_failure_evidence
        intervening = value["intervening_events"]
        if (
            value["schema_version"] != "cera.pi_scene.provider_prefix_bridge.v1"
            or value["bridge_sha256"] != canonical_sha256(body)
            or value["prior_after_event_count"] != prior.after_event_count
            or value["prior_after_events_sha256"] != prior.after_events_sha256
            or value["prior_dispatched_calls_after"] != prior.dispatched_calls_after
            or value["current_before_event_count"] != current.before_event_count
            or value["current_before_events_sha256"] != current.before_events_sha256
            or value["current_dispatched_calls_before"] != current.dispatched_calls_before
            or not isinstance(intervening, list)
            or any(not isinstance(event, dict) for event in intervening)
            or len(intervening) != current.before_event_count - prior.after_event_count
            or current.before_event_count < prior.after_event_count
            or current.dispatched_calls_before
            != prior.dispatched_calls_after + _transport_invocation_count(intervening)
        ):
            raise StateConflictError("Pi Scene provider prefix bridge changed")
        if intervening:
            expected_indices = tuple(
                range(prior.after_event_count + 1, current.before_event_count + 1)
            )
            if tuple(event.get("event_index") for event in intervening) != expected_indices:
                raise StateConflictError("Pi Scene provider prefix bridge order changed")

    @staticmethod
    def _new_retry_action(retry_id: str) -> dict[str, Any]:
        return TransportRetryStoreMixin._signed_retry_action(
            {
                "schema_version": "cera.pi_scene.transport_retry_action.v2",
                "retry_id": retry_id,
                "phase": "eligible",
                "process_instance_id": None,
                "authorized_sha256": None,
                "owner_rotated_sha256": None,
                "dispatch_started_sha256": None,
                "zero_dispatch_rearm_sha256": None,
                "dispatch_ledger_before": None,
                "fresh_thread_sha256": None,
                "successor_retry_id": None,
                "terminal_response_sha256": None,
                "blocked_reason_code": None,
                "blocked_evidence_sha256": None,
            }
        )

    @staticmethod
    def _signed_retry_action(value: Mapping[str, Any]) -> dict[str, Any]:
        body = {key: value[key] for key in value if key != "action_sha256"}
        return {**body, "action_sha256": canonical_sha256(body)}

    @staticmethod
    def _validate_retry_action(value: Any) -> dict[str, Any]:
        required = {
            "schema_version",
            "retry_id",
            "phase",
            "process_instance_id",
            "authorized_sha256",
            "owner_rotated_sha256",
            "dispatch_started_sha256",
            "zero_dispatch_rearm_sha256",
            "dispatch_ledger_before",
            "fresh_thread_sha256",
            "successor_retry_id",
            "terminal_response_sha256",
            "blocked_reason_code",
            "blocked_evidence_sha256",
            "action_sha256",
        }
        if not isinstance(value, dict) or set(value) != required:
            raise StateConflictError("Pi Scene transport retry action shape changed")
        body = {key: value[key] for key in required if key != "action_sha256"}
        if (
            value["schema_version"] != "cera.pi_scene.transport_retry_action.v2"
            or re.fullmatch(r"retry-[a-f0-9]{64}", value["retry_id"] or "") is None
            or value["phase"] not in TRANSPORT_RETRY_PHASES
            or value["action_sha256"] != canonical_sha256(body)
        ):
            raise StateConflictError("Pi Scene transport retry action changed")
        for field in (
            "authorized_sha256",
            "owner_rotated_sha256",
            "dispatch_started_sha256",
            "zero_dispatch_rearm_sha256",
            "terminal_response_sha256",
            "blocked_evidence_sha256",
            "fresh_thread_sha256",
        ):
            if value[field] is not None and not re_is_sha256(value[field]):
                raise StateConflictError("Pi Scene transport retry evidence hash changed")
        process_id = value["process_instance_id"]
        if process_id is not None and re.fullmatch(r"process-[a-f0-9]{32}", process_id) is None:
            raise StateConflictError("Pi Scene transport retry process identity changed")
        if (
            value["successor_retry_id"] is not None
            and re.fullmatch(r"retry-[a-f0-9]{64}", value["successor_retry_id"]) is None
        ):
            raise StateConflictError("Pi Scene transport retry successor changed")
        if (
            value["blocked_reason_code"] is not None
            and value["blocked_reason_code"] not in TRANSPORT_RETRY_BLOCK_REASONS
        ):
            raise StateConflictError("Pi Scene transport retry block reason changed")
        baseline = value["dispatch_ledger_before"]
        if baseline is not None:
            TransportRetryStoreMixin._validate_provider_ledger_prefix_payload(baseline)
        return value

    @staticmethod
    def _provider_ledger_prefix_payload(
        value: PiSceneProviderLedgerSnapshotV1,
    ) -> dict[str, Any]:
        return {
            "schema_version": "cera.pi_scene.provider_ledger_prefix.v1",
            "event_count": value.event_count,
            "events_sha256": value.events_sha256,
            "dispatched_call_count": value.dispatched_call_count,
        }

    @staticmethod
    def _validate_provider_ledger_prefix_payload(value: Any) -> None:
        required = {
            "schema_version",
            "event_count",
            "events_sha256",
            "dispatched_call_count",
        }
        if (
            not isinstance(value, dict)
            or set(value) != required
            or value["schema_version"] != "cera.pi_scene.provider_ledger_prefix.v1"
            or type(value["event_count"]) is not int
            or value["event_count"] < 0
            or type(value["dispatched_call_count"]) is not int
            or value["dispatched_call_count"] < 0
            or not re_is_sha256(value["events_sha256"] or "")
        ):
            raise StateConflictError("Pi Scene provider-ledger prefix custody changed")

    def _write_transport_ledger(
        self,
        binding: PiSceneRequestBindingV1,
        current: Mapping[str, Any],
        *,
        actions: list[Mapping[str, Any]],
    ) -> dict[str, Any]:
        body = {
            "schema_version": current["schema_version"],
            "request_id": current["request_id"],
            "request_custody_sha256": current["request_custody_sha256"],
            "failures": list(current["failures"]),
            "actions": [dict(value) for value in actions],
            "active_retry_id": current["active_retry_id"],
        }
        payload = {**body, "ledger_sha256": canonical_sha256(body)}
        authority = self._read_transport_authority(binding, required=True)
        assert authority is not None
        redacted = authority["normalized_request"] is None
        self._write_transport_authority(
            binding,
            custody=None,
            phase="terminal_redacted" if redacted else "failure_eligible",
            dispatch_capsule=None,
            transport_ledger=payload,
        )
        _atomic_write_json(self._transport_ledger_path(binding), payload)
        verified = self._read_transport_ledger(binding, required=True)
        assert verified is not None
        return verified

    def _write_retry_index(
        self,
        binding: PiSceneRequestBindingV1,
        receipt: TransportFailureReceiptV1,
    ) -> None:
        entry_path = self._entry_path(binding)
        relative = entry_path.relative_to(self.root).as_posix()
        body = {
            "schema_version": "cera.pi_scene.transport_retry_index.v1",
            "retry_id": receipt.retry_id,
            "request_id": binding.request_id,
            "binding": binding.to_payload(),
            "entry_relative_path": relative,
            "failure_receipt_sha256": receipt.failure_receipt_sha256,
        }
        payload = {**body, "index_sha256": canonical_sha256(body)}
        path = self._retry_index_path(receipt.retry_id)
        if path.exists():
            try:
                existing = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise StateConflictError("Pi Scene transport retry index is unreadable") from exc
            if existing != payload:
                raise StateConflictError("Pi Scene transport retry index changed")
            return
        _atomic_write_json(path, payload)

    def _binding_for_retry_id(
        self,
        retry_id: str,
    ) -> tuple[PiSceneRequestBindingV1, Path]:
        if re.fullmatch(r"retry-[a-f0-9]{64}", retry_id or "") is None:
            raise TransportRetryNotFoundError("Pi Scene transport retry identity is unavailable")
        path = self._retry_index_path(retry_id)
        if not path.exists():
            recovered = self._retry_binding_from_protected_authority(retry_id)
            if recovered is None:
                raise TransportRetryNotFoundError(
                    "Pi Scene transport retry identity is unavailable"
                )
            binding, _ = recovered
            return binding, self._entry_path(binding)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise TransportRetryNotFoundError(
                "Pi Scene transport retry identity is unavailable"
            ) from exc
        required = {
            "schema_version",
            "retry_id",
            "request_id",
            "binding",
            "entry_relative_path",
            "failure_receipt_sha256",
            "index_sha256",
        }
        if not isinstance(payload, dict) or set(payload) != required:
            raise StateConflictError("Pi Scene transport retry index shape changed")
        body = {key: payload[key] for key in required if key != "index_sha256"}
        binding = _binding_from_payload(payload["binding"])
        entry_path = (self.root / str(payload["entry_relative_path"])).resolve()
        if (
            payload["schema_version"] != "cera.pi_scene.transport_retry_index.v1"
            or payload["retry_id"] != retry_id
            or payload["request_id"] != binding.request_id
            or payload["index_sha256"] != canonical_sha256(body)
            or not re_is_sha256(payload["failure_receipt_sha256"] or "")
            or not entry_path.is_relative_to(self.root)
            or entry_path != self._entry_path(binding)
        ):
            raise StateConflictError("Pi Scene transport retry index changed")
        return binding, entry_path

    def _retry_binding_from_protected_authority(
        self,
        retry_id: str,
    ) -> tuple[PiSceneRequestBindingV1, TransportFailureReceiptV1] | None:
        """Resolve protected authority without writing any derived sidecar."""

        authority_root = self.protected_retry_root / "DISPATCH_AUTHORITY"
        if not authority_root.is_dir():
            return None
        matches: list[tuple[PiSceneRequestBindingV1, TransportFailureReceiptV1]] = []
        for authority_path in sorted(authority_root.glob("request-*.json")):
            try:
                raw = json.loads(authority_path.read_text(encoding="utf-8"))
                binding = _binding_from_payload(raw.get("binding"))
                authority = self._read_transport_authority(binding, required=True)
                assert authority is not None
                ledger = authority["transport_ledger"]
                if ledger is None:
                    continue
                for failure in ledger["failures"]:
                    receipt, _ = self._decode_failure_entry(failure)
                    if receipt.retry_id == retry_id:
                        matches.append((binding, receipt))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                continue
        if len(matches) > 1:
            raise StateConflictError("Pi Scene transport retry identity is ambiguous")
        return None if not matches else matches[0]

    def _retry_index_path(self, retry_id: str) -> Path:
        path = (self.root / "TRANSPORT_RETRY_INDEX" / f"{retry_id}.json").resolve()
        if not path.is_relative_to(self.root):
            raise ContractValidationError("Pi Scene transport retry index escaped its root")
        return path

    def _transport_dispatch_lock_path(self, retry_id: str) -> Path:
        path = (self.root / "TRANSPORT_DISPATCH_LOCKS" / f"{retry_id}.lock").resolve()
        if not path.is_relative_to(self.root):
            raise ContractValidationError("Pi Scene transport dispatch lock escaped its root")
        return path


def _transport_invocation_count(events: Any) -> int:
    if isinstance(events, (str, bytes)) or not isinstance(events, (list, tuple)):
        raise StateConflictError("Pi Scene provider event sequence changed shape")
    if any(not isinstance(value, Mapping) for value in events):
        raise StateConflictError("Pi Scene provider event sequence is invalid")
    return sum(value.get("state") == "transport_invoked" for value in events)


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".transport-retry-{uuid4().hex}.tmp"
    data = canonical_bytes(payload) + b"\n"
    try:
        with temporary.open("xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
