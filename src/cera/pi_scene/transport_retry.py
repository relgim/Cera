"""Closed contracts for manual retry of zero-effect Planner transport failures.

Retry authority is deliberately narrower than transport-failure annotation.  A
Writer, Validator, adult owner, or Recorder failure can still be identified by
the runtime, but only the Planner currently has complete provider-ledger,
thread-rotation, and pre-candidate effect custody.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, ClassVar

from cera.continuous.call_ledger import ProviderCallLedgerEventV1
from cera.errors import ContractValidationError, StateConflictError
from cera.providers.models import ProviderTransportError
from cera.schema import from_mapping
from cera.serialization import canonical_sha256, domain_sha256, re_is_sha256

PROVIDER_TRANSPORT_FAILURE_OWNERS = frozenset(
    {"planner", "writer", "validator", "adult_scene", "adult_filter", "recorder"}
)
TRANSPORT_RETRY_OWNER = "planner"
TRANSPORT_RETRY_OWNERS = frozenset({TRANSPORT_RETRY_OWNER})
TRANSPORT_RETRY_PHASES = frozenset(
    {
        "eligible",
        "authorized",
        "owner_rotated",
        "dispatch_started",
        "terminal_failed",
        "succeeded",
        "blocked",
    }
)
TRANSPORT_RETRY_BLOCK_REASONS = frozenset(
    {
        "effect_state_changed",
        "route_or_context_changed",
        "provider_ledger_changed",
        "owner_rotation_failed",
        "dispatch_state_ambiguous",
        "durable_request_progressed",
        "planner_result_unavailable",
    }
)

_PROVIDER_LEDGER_EVENT_SCHEMA = "cera.continuous_provider_call_ledger_event.v3"
_PLANNER_CALL_PREFIXES = (
    ("prepared_not_invoked",),
    ("prepared_not_invoked", "worker_started_not_invoked"),
    (
        "prepared_not_invoked",
        "worker_started_not_invoked",
        "worker_preflight_not_invoked",
    ),
)
_PLANNER_CALL_FAILURE_STATES = frozenset(
    {
        "pretransport_failed",
        "provider_failed",
        "provider_completed_post_validation_failed",
    }
)


class PiSceneProviderTransportFailure(Exception):
    """Provider transport failure annotated at its exact Pi Scene owner."""

    def __init__(self, *, logic_owner: str, failure: ProviderTransportError) -> None:
        if logic_owner not in PROVIDER_TRANSPORT_FAILURE_OWNERS:
            raise ContractValidationError("Pi Scene transport-failure owner is invalid")
        if not isinstance(failure, ProviderTransportError):
            raise ContractValidationError("Pi Scene transport failure is invalid")
        self.logic_owner = logic_owner
        self.failure = failure
        # The HTTP owner may rebind this immediately before the provider stage
        # after completing deterministic pre-provider state transitions (for
        # example auto-resolving the prior review).  It is never serialized.
        self.effect_snapshot_before: PiSceneTransportEffectSnapshotV1 | None = None
        super().__init__(f"{logic_owner} provider transport failed")


@dataclass(frozen=True, slots=True)
class PiSceneTransportEffectSnapshotV1:
    """Privacy-safe identities of every durable story-effect surface."""

    SCHEMA_VERSION: ClassVar[str] = "cera.pi_scene.transport_effect_snapshot.v1"

    schema_version: str
    world_id: str
    branch_id: str
    accepted_turn_id: str | None
    accepted_receipt_sha256: str | None
    accepted_head_sha256: str
    unresolved_review_sha256: str | None
    recording_attempt_sha256: str | None

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("Pi Scene effect-snapshot schema changed")
        if not self.world_id.strip() or not self.branch_id.strip():
            raise ContractValidationError("Pi Scene effect-snapshot branch is invalid")
        if (self.accepted_turn_id is None) != (self.accepted_receipt_sha256 is None):
            raise ContractValidationError("Pi Scene accepted-head identity is partial")
        if self.accepted_receipt_sha256 is not None and not re_is_sha256(
            self.accepted_receipt_sha256
        ):
            raise ContractValidationError("Pi Scene accepted receipt hash is invalid")
        if not re_is_sha256(self.accepted_head_sha256):
            raise ContractValidationError("Pi Scene accepted-head snapshot is invalid")
        for value in (self.unresolved_review_sha256, self.recording_attempt_sha256):
            if value is not None and not re_is_sha256(value):
                raise ContractValidationError("Pi Scene optional effect snapshot is invalid")

    @property
    def snapshot_sha256(self) -> str:
        return canonical_sha256(self.to_payload())

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "world_id": self.world_id,
            "branch_id": self.branch_id,
            "accepted_turn_id": self.accepted_turn_id,
            "accepted_receipt_sha256": self.accepted_receipt_sha256,
            "accepted_head_sha256": self.accepted_head_sha256,
            "unresolved_review_sha256": self.unresolved_review_sha256,
            "recording_attempt_sha256": self.recording_attempt_sha256,
        }


@dataclass(frozen=True, slots=True)
class PiSceneProviderLedgerSnapshotV1:
    """In-memory exact prefix of the append-only Sol provider ledger."""

    SCHEMA_VERSION: ClassVar[str] = "cera.pi_scene.provider_ledger_snapshot.v1"

    schema_version: str
    dispatched_call_count: int
    events: tuple[Mapping[str, Any], ...]

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("Pi Scene provider-ledger snapshot schema changed")
        if type(self.dispatched_call_count) is not int or self.dispatched_call_count < 0:
            raise ContractValidationError("Pi Scene provider-ledger call count is invalid")
        if any(not isinstance(value, Mapping) for value in self.events):
            raise ContractValidationError("Pi Scene provider-ledger event is invalid")
        indices = tuple(value.get("event_index") for value in self.events)
        if indices != tuple(range(1, len(self.events) + 1)):
            raise ContractValidationError("Pi Scene provider-ledger order changed")

    @property
    def event_count(self) -> int:
        return len(self.events)

    @property
    def events_sha256(self) -> str:
        return canonical_sha256(tuple(dict(value) for value in self.events))


@dataclass(frozen=True, slots=True)
class PiScenePlannerCompletionMarkerV1:
    """Hash-only custody for a completed Planner call awaiting durable progress."""

    SCHEMA_VERSION: ClassVar[str] = "cera.pi_scene.planner_completed_pending_progress.v1"

    schema_version: str
    request_id: str
    resolved_route: str
    turn_context_sha256: str
    effect_before: PiSceneTransportEffectSnapshotV1
    call_id: str
    stored_thread_sha256: str
    terminal_state: str
    before_event_count: int
    before_events_sha256: str
    after_event_count: int
    after_events_sha256: str
    dispatched_calls_before: int
    dispatched_calls_after: int
    call_events_sha256: str

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("Pi Scene Planner completion-marker schema changed")
        if not self.request_id.startswith("request-"):
            raise ContractValidationError("Pi Scene Planner completion request is invalid")
        if self.resolved_route not in {"ordinary", "adult"}:
            raise ContractValidationError("Pi Scene Planner completion route is invalid")
        if not re_is_sha256(self.turn_context_sha256):
            raise ContractValidationError("Pi Scene Planner completion turn hash is invalid")
        if not self.call_id.strip() or not re_is_sha256(self.stored_thread_sha256):
            raise ContractValidationError("Pi Scene Planner completion call identity is invalid")
        if self.terminal_state not in {
            "provider_completed",
            "provider_completed_post_validation_failed",
            "typed_accepted",
            "dispatch_outcome_ambiguous",
        }:
            raise ContractValidationError("Pi Scene Planner completion terminal state is invalid")
        if (
            type(self.before_event_count) is not int
            or type(self.after_event_count) is not int
            or self.before_event_count < 0
            or self.after_event_count <= self.before_event_count
            or type(self.dispatched_calls_before) is not int
            or type(self.dispatched_calls_after) is not int
            or self.dispatched_calls_before < 0
            or self.dispatched_calls_after != self.dispatched_calls_before + 1
        ):
            raise ContractValidationError("Pi Scene Planner completion accounting is invalid")
        for value in (
            self.before_events_sha256,
            self.after_events_sha256,
            self.call_events_sha256,
        ):
            if not re_is_sha256(value):
                raise ContractValidationError("Pi Scene Planner completion evidence is invalid")

    @property
    def marker_sha256(self) -> str:
        return canonical_sha256(self.to_payload())

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "request_id": self.request_id,
            "resolved_route": self.resolved_route,
            "turn_context_sha256": self.turn_context_sha256,
            "effect_before": self.effect_before.to_payload(),
            "call_id": self.call_id,
            "stored_thread_sha256": self.stored_thread_sha256,
            "terminal_state": self.terminal_state,
            "before_event_count": self.before_event_count,
            "before_events_sha256": self.before_events_sha256,
            "after_event_count": self.after_event_count,
            "after_events_sha256": self.after_events_sha256,
            "dispatched_calls_before": self.dispatched_calls_before,
            "dispatched_calls_after": self.dispatched_calls_after,
            "call_events_sha256": self.call_events_sha256,
        }


@dataclass(frozen=True, slots=True)
class PiScenePlannerResultUnavailableV1:
    """Durable non-Retry disposition for a charged Planner result that was lost."""

    SCHEMA_VERSION: ClassVar[str] = "cera.pi_scene.planner_result_unavailable.v1"

    schema_version: str
    completion: PiScenePlannerCompletionMarkerV1
    thread_disposition: str
    thread_retired: bool
    branch_dispatch_blocked: bool

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("Pi Scene Planner result disposition schema changed")
        allowed = {
            "completed_uncommitted_never_resume",
            "completed_uncommitted_retirement_failed",
            "durable_progress_unrecognized",
            "planner_evidence_invalid_never_resume",
            "planner_evidence_invalid_retirement_failed",
            "planner_evidence_invalid_blocked",
        }
        if self.thread_disposition not in allowed:
            raise ContractValidationError("Pi Scene Planner result disposition is invalid")
        if type(self.thread_retired) is not bool or type(self.branch_dispatch_blocked) is not bool:
            raise ContractValidationError("Pi Scene Planner result disposition flags are invalid")
        if self.thread_disposition == "planner_evidence_invalid_blocked":
            if not self.branch_dispatch_blocked:
                raise ContractValidationError("Pi Scene invalid Planner evidence must block branch")
            return
        if self.thread_retired == self.branch_dispatch_blocked:
            raise ContractValidationError("Pi Scene Planner result disposition is inconsistent")
        retiring_dispositions = {
            "completed_uncommitted_never_resume",
            "planner_evidence_invalid_never_resume",
        }
        if self.thread_retired != (self.thread_disposition in retiring_dispositions):
            raise ContractValidationError("Pi Scene Planner thread retirement changed")

    @property
    def disposition_sha256(self) -> str:
        return canonical_sha256(self.to_payload())

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "completion": self.completion.to_payload(),
            "thread_disposition": self.thread_disposition,
            "thread_retired": self.thread_retired,
            "branch_dispatch_blocked": self.branch_dispatch_blocked,
        }


@dataclass(frozen=True, slots=True)
class PiSceneProviderFailureEvidenceV1:
    """Exact append-only ledger evidence for one terminal Planner failure."""

    SCHEMA_VERSION: ClassVar[str] = "cera.pi_scene.provider_failure_evidence.v1"

    schema_version: str
    call_id: str
    stored_thread_sha256: str
    before_event_count: int
    before_events_sha256: str
    after_event_count: int
    after_events_sha256: str
    dispatched_calls_before: int
    dispatched_calls_after: int
    call_events: tuple[Mapping[str, Any], ...]
    provider_operation_submitted: bool
    provider_failure_detail_sha256: str

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("Pi Scene provider-failure evidence schema changed")
        if not self.call_id.strip():
            raise ContractValidationError("Pi Scene provider-failure call identity is empty")
        if not re_is_sha256(self.stored_thread_sha256):
            raise ContractValidationError("Pi Scene provider-failure thread identity is invalid")
        if (
            type(self.before_event_count) is not int
            or type(self.after_event_count) is not int
            or self.before_event_count < 0
            or self.after_event_count <= self.before_event_count
            or self.after_event_count - self.before_event_count != len(self.call_events)
        ):
            raise ContractValidationError("Pi Scene provider-failure event span is invalid")
        if not re_is_sha256(self.before_events_sha256) or not re_is_sha256(
            self.after_events_sha256
        ):
            raise ContractValidationError("Pi Scene provider-failure prefix hash is invalid")
        if (
            type(self.dispatched_calls_before) is not int
            or type(self.dispatched_calls_after) is not int
            or self.dispatched_calls_before < 0
        ):
            raise ContractValidationError("Pi Scene provider-failure count is invalid")
        if type(self.provider_operation_submitted) is not bool:
            raise ContractValidationError("Pi Scene provider submission state is invalid")
        expected_after = self.dispatched_calls_before + int(self.provider_operation_submitted)
        if self.dispatched_calls_after != expected_after:
            raise ContractValidationError("Pi Scene provider-failure accounting changed")
        if not re_is_sha256(self.provider_failure_detail_sha256):
            raise ContractValidationError("Pi Scene provider-failure detail hash is invalid")
        _validate_failure_events(
            self.call_events,
            call_id=self.call_id,
            submitted=self.provider_operation_submitted,
        )

    @property
    def evidence_sha256(self) -> str:
        return canonical_sha256(self.to_payload())

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "call_id": self.call_id,
            "stored_thread_sha256": self.stored_thread_sha256,
            "before_event_count": self.before_event_count,
            "before_events_sha256": self.before_events_sha256,
            "after_event_count": self.after_event_count,
            "after_events_sha256": self.after_events_sha256,
            "dispatched_calls_before": self.dispatched_calls_before,
            "dispatched_calls_after": self.dispatched_calls_after,
            "call_events": [dict(value) for value in self.call_events],
            "provider_operation_submitted": self.provider_operation_submitted,
            "provider_failure_detail_sha256": self.provider_failure_detail_sha256,
        }


@dataclass(frozen=True, slots=True)
class PiSceneZeroEffectProofV1:
    """Closed proof that a failed Planner call changed no story authority."""

    SCHEMA_VERSION: ClassVar[str] = "cera.pi_scene.transport_zero_effect_proof.v2"

    schema_version: str
    request_id: str
    logic_owner: str
    resolved_route: str
    turn_context_sha256: str
    before_snapshot: PiSceneTransportEffectSnapshotV1
    after_snapshot: PiSceneTransportEffectSnapshotV1
    provider_failure_evidence: PiSceneProviderFailureEvidenceV1
    candidate_effect_absent: bool
    review_effect_absent: bool
    accepted_effect_absent: bool
    recording_effect_absent: bool
    branch_head_effect_absent: bool

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("Pi Scene zero-effect proof schema changed")
        if self.logic_owner != TRANSPORT_RETRY_OWNER:
            raise ContractValidationError("Pi Scene zero-effect proof owner is unsupported")
        if not self.request_id.startswith("request-"):
            raise ContractValidationError("Pi Scene zero-effect request identity is invalid")
        if self.resolved_route not in {"ordinary", "adult"}:
            raise ContractValidationError("Pi Scene zero-effect route is invalid")
        if not re_is_sha256(self.turn_context_sha256):
            raise ContractValidationError("Pi Scene zero-effect turn binding is invalid")
        booleans = (
            self.candidate_effect_absent,
            self.review_effect_absent,
            self.accepted_effect_absent,
            self.recording_effect_absent,
            self.branch_head_effect_absent,
        )
        if any(type(value) is not bool for value in booleans):
            raise ContractValidationError("Pi Scene zero-effect flags must be booleans")
        if not all(booleans) or self.before_snapshot != self.after_snapshot:
            raise ContractValidationError("Pi Scene transport retry requires exact zero effect")

    @property
    def before_snapshot_sha256(self) -> str:
        return self.before_snapshot.snapshot_sha256

    @property
    def after_snapshot_sha256(self) -> str:
        return self.after_snapshot.snapshot_sha256

    @property
    def proof_sha256(self) -> str:
        return canonical_sha256(self.to_payload())

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "request_id": self.request_id,
            "logic_owner": self.logic_owner,
            "resolved_route": self.resolved_route,
            "turn_context_sha256": self.turn_context_sha256,
            "before_snapshot": self.before_snapshot.to_payload(),
            "after_snapshot": self.after_snapshot.to_payload(),
            "provider_failure_evidence": self.provider_failure_evidence.to_payload(),
            "candidate_effect_absent": self.candidate_effect_absent,
            "review_effect_absent": self.review_effect_absent,
            "accepted_effect_absent": self.accepted_effect_absent,
            "recording_effect_absent": self.recording_effect_absent,
            "branch_head_effect_absent": self.branch_head_effect_absent,
        }


@dataclass(frozen=True, slots=True)
class TransportFailureReceiptV1:
    """Privacy-safe terminal failure and its one explicit retry action."""

    failure_number: int
    request_id: str
    logic_owner: str
    provider_error_code: str
    provider_operations_observed: int
    effect_proof_sha256: str
    predecessor_retry_id: str | None
    failure_receipt_sha256: str
    retry_id: str

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema_version": "cera.pi_scene.transport_failure_receipt.v1",
            "failure_number": self.failure_number,
            "request_id": self.request_id,
            "logic_owner": self.logic_owner,
            "provider_error_code": self.provider_error_code,
            "provider_operations_observed": self.provider_operations_observed,
            "effect_proof_sha256": self.effect_proof_sha256,
            "predecessor_retry_id": self.predecessor_retry_id,
            "failure_receipt_sha256": self.failure_receipt_sha256,
            "retry_id": self.retry_id,
        }


def build_provider_failure_evidence(
    *,
    before: PiSceneProviderLedgerSnapshotV1,
    after: PiSceneProviderLedgerSnapshotV1,
    provider_operation_submitted: bool,
    provider_failure_detail_sha256: str,
) -> PiSceneProviderFailureEvidenceV1:
    """Prove one exact terminal Planner ledger append."""

    if after.events[: before.event_count] != before.events:
        raise StateConflictError("Pi Scene provider ledger changed its existing prefix")
    call_events = after.events[before.event_count :]
    if not call_events:
        raise StateConflictError("Pi Scene provider failure lacks a ledger append")
    call_ids = {value.get("call_id") for value in call_events}
    if len(call_ids) != 1 or not all(isinstance(value, str) for value in call_ids):
        raise StateConflictError("Pi Scene provider failure spans multiple calls")
    call_id = next(iter(call_ids))
    assert isinstance(call_id, str)
    thread_ids = {value.get("stored_thread_sha256") for value in call_events}
    if len(thread_ids) != 1 or not all(
        isinstance(value, str) and re_is_sha256(value) for value in thread_ids
    ):
        raise StateConflictError("Pi Scene provider failure changed stored thread")
    stored_thread_sha256 = next(iter(thread_ids))
    assert isinstance(stored_thread_sha256, str)
    return PiSceneProviderFailureEvidenceV1(
        schema_version=PiSceneProviderFailureEvidenceV1.SCHEMA_VERSION,
        call_id=call_id,
        stored_thread_sha256=stored_thread_sha256,
        before_event_count=before.event_count,
        before_events_sha256=before.events_sha256,
        after_event_count=after.event_count,
        after_events_sha256=after.events_sha256,
        dispatched_calls_before=before.dispatched_call_count,
        dispatched_calls_after=after.dispatched_call_count,
        call_events=tuple(dict(value) for value in call_events),
        provider_operation_submitted=provider_operation_submitted,
        provider_failure_detail_sha256=provider_failure_detail_sha256,
    )


def provider_failure_detail_sha256(
    failure: ProviderTransportError | None,
    *,
    call_events: Sequence[Mapping[str, Any]] = (),
) -> str:
    """Hash only safe typed failure evidence, never exception prose."""

    if failure is None:
        return domain_sha256(
            "cera.pi_scene.reconciled_provider_failure.v1",
            {"call_events": [dict(value) for value in call_events]},
        )
    return domain_sha256(
        "cera.pi_scene.provider_failure_detail.v1",
        {
            "error_code": failure.code.value,
            "safe_diagnostics": list(failure.safe_diagnostics),
            "provider_call_receipt_sha256": _safe_object_hash(failure.provider_call_receipt),
            "operation_telemetry_sha256": _safe_object_hash(failure.operation_telemetry),
        },
    )


def effect_snapshot_from_payload(payload: Any) -> PiSceneTransportEffectSnapshotV1:
    required = {
        "schema_version",
        "world_id",
        "branch_id",
        "accepted_turn_id",
        "accepted_receipt_sha256",
        "accepted_head_sha256",
        "unresolved_review_sha256",
        "recording_attempt_sha256",
    }
    if not isinstance(payload, dict) or set(payload) != required:
        raise StateConflictError("Pi Scene transport effect snapshot shape changed")
    try:
        return PiSceneTransportEffectSnapshotV1(**payload)
    except (ContractValidationError, TypeError) as exc:
        raise StateConflictError("Pi Scene transport effect snapshot is invalid") from exc


def planner_completion_marker_from_payload(
    payload: Any,
) -> PiScenePlannerCompletionMarkerV1:
    required = {
        "schema_version",
        "request_id",
        "resolved_route",
        "turn_context_sha256",
        "effect_before",
        "call_id",
        "stored_thread_sha256",
        "terminal_state",
        "before_event_count",
        "before_events_sha256",
        "after_event_count",
        "after_events_sha256",
        "dispatched_calls_before",
        "dispatched_calls_after",
        "call_events_sha256",
    }
    if not isinstance(payload, dict) or set(payload) != required:
        raise StateConflictError("Pi Scene Planner completion-marker shape changed")
    try:
        return PiScenePlannerCompletionMarkerV1(
            schema_version=payload["schema_version"],
            request_id=payload["request_id"],
            resolved_route=payload["resolved_route"],
            turn_context_sha256=payload["turn_context_sha256"],
            effect_before=effect_snapshot_from_payload(payload["effect_before"]),
            call_id=payload["call_id"],
            stored_thread_sha256=payload["stored_thread_sha256"],
            terminal_state=payload["terminal_state"],
            before_event_count=payload["before_event_count"],
            before_events_sha256=payload["before_events_sha256"],
            after_event_count=payload["after_event_count"],
            after_events_sha256=payload["after_events_sha256"],
            dispatched_calls_before=payload["dispatched_calls_before"],
            dispatched_calls_after=payload["dispatched_calls_after"],
            call_events_sha256=payload["call_events_sha256"],
        )
    except (ContractValidationError, TypeError) as exc:
        raise StateConflictError("Pi Scene Planner completion-marker is invalid") from exc


def planner_result_unavailable_from_payload(
    payload: Any,
) -> PiScenePlannerResultUnavailableV1:
    required = {
        "schema_version",
        "completion",
        "thread_disposition",
        "thread_retired",
        "branch_dispatch_blocked",
    }
    if not isinstance(payload, dict) or set(payload) != required:
        raise StateConflictError("Pi Scene Planner result disposition shape changed")
    try:
        return PiScenePlannerResultUnavailableV1(
            schema_version=payload["schema_version"],
            completion=planner_completion_marker_from_payload(payload["completion"]),
            thread_disposition=payload["thread_disposition"],
            thread_retired=payload["thread_retired"],
            branch_dispatch_blocked=payload["branch_dispatch_blocked"],
        )
    except (ContractValidationError, TypeError) as exc:
        raise StateConflictError("Pi Scene Planner result disposition is invalid") from exc


def provider_failure_evidence_from_payload(
    payload: Any,
) -> PiSceneProviderFailureEvidenceV1:
    required = {
        "schema_version",
        "call_id",
        "stored_thread_sha256",
        "before_event_count",
        "before_events_sha256",
        "after_event_count",
        "after_events_sha256",
        "dispatched_calls_before",
        "dispatched_calls_after",
        "call_events",
        "provider_operation_submitted",
        "provider_failure_detail_sha256",
    }
    if not isinstance(payload, dict) or set(payload) != required:
        raise StateConflictError("Pi Scene provider-failure evidence shape changed")
    if not isinstance(payload["call_events"], list):
        raise StateConflictError("Pi Scene provider-failure events changed shape")
    try:
        return PiSceneProviderFailureEvidenceV1(
            **{**payload, "call_events": tuple(payload["call_events"])}
        )
    except (ContractValidationError, TypeError) as exc:
        raise StateConflictError("Pi Scene provider-failure evidence is invalid") from exc


def zero_effect_proof_from_payload(payload: Any) -> PiSceneZeroEffectProofV1:
    required = {
        "schema_version",
        "request_id",
        "logic_owner",
        "resolved_route",
        "turn_context_sha256",
        "before_snapshot",
        "after_snapshot",
        "provider_failure_evidence",
        "candidate_effect_absent",
        "review_effect_absent",
        "accepted_effect_absent",
        "recording_effect_absent",
        "branch_head_effect_absent",
    }
    if not isinstance(payload, dict) or set(payload) != required:
        raise StateConflictError("Pi Scene zero-effect proof shape changed")
    try:
        return PiSceneZeroEffectProofV1(
            schema_version=payload["schema_version"],
            request_id=payload["request_id"],
            logic_owner=payload["logic_owner"],
            resolved_route=payload["resolved_route"],
            turn_context_sha256=payload["turn_context_sha256"],
            before_snapshot=effect_snapshot_from_payload(payload["before_snapshot"]),
            after_snapshot=effect_snapshot_from_payload(payload["after_snapshot"]),
            provider_failure_evidence=provider_failure_evidence_from_payload(
                payload["provider_failure_evidence"]
            ),
            candidate_effect_absent=payload["candidate_effect_absent"],
            review_effect_absent=payload["review_effect_absent"],
            accepted_effect_absent=payload["accepted_effect_absent"],
            recording_effect_absent=payload["recording_effect_absent"],
            branch_head_effect_absent=payload["branch_head_effect_absent"],
        )
    except (ContractValidationError, TypeError) as exc:
        raise StateConflictError("Pi Scene zero-effect proof is invalid") from exc


def transport_failure_from_payload(payload: Any) -> TransportFailureReceiptV1:
    required = {
        "schema_version",
        "failure_number",
        "request_id",
        "logic_owner",
        "provider_error_code",
        "provider_operations_observed",
        "effect_proof_sha256",
        "predecessor_retry_id",
        "failure_receipt_sha256",
        "retry_id",
    }
    if not isinstance(payload, dict) or set(payload) != required:
        raise StateConflictError("Pi Scene transport failure receipt shape changed")
    if (
        payload["schema_version"] != "cera.pi_scene.transport_failure_receipt.v1"
        or type(payload["failure_number"]) is not int
        or payload["failure_number"] < 1
        or not isinstance(payload["request_id"], str)
        or payload["logic_owner"] != TRANSPORT_RETRY_OWNER
        or not isinstance(payload["provider_error_code"], str)
        or not payload["provider_error_code"].strip()
        or payload["provider_operations_observed"] not in {0, 1}
        or not re_is_sha256(payload["effect_proof_sha256"] or "")
        or (
            payload["predecessor_retry_id"] is not None
            and not _valid_retry_id(payload["predecessor_retry_id"])
        )
        or not re_is_sha256(payload["failure_receipt_sha256"] or "")
        or not _valid_retry_id(payload["retry_id"])
    ):
        raise StateConflictError("Pi Scene transport failure receipt is invalid")
    unsigned = {
        key: payload[key]
        for key in (
            "schema_version",
            "failure_number",
            "request_id",
            "logic_owner",
            "provider_error_code",
            "provider_operations_observed",
            "effect_proof_sha256",
            "predecessor_retry_id",
        )
    }
    failure_sha256 = domain_sha256(
        "cera.pi_scene.transport_failure_receipt.v1",
        unsigned,
    )
    retry_id = "retry-" + domain_sha256(
        "cera.pi_scene.transport_retry.v1",
        {
            "request_id": payload["request_id"],
            "failure_receipt_sha256": failure_sha256,
            "failure_number": payload["failure_number"],
        },
    )
    if payload["failure_receipt_sha256"] != failure_sha256 or payload["retry_id"] != retry_id:
        raise StateConflictError("Pi Scene transport failure receipt binding changed")
    return TransportFailureReceiptV1(
        failure_number=payload["failure_number"],
        request_id=payload["request_id"],
        logic_owner=payload["logic_owner"],
        provider_error_code=payload["provider_error_code"],
        provider_operations_observed=payload["provider_operations_observed"],
        effect_proof_sha256=payload["effect_proof_sha256"],
        predecessor_retry_id=payload["predecessor_retry_id"],
        failure_receipt_sha256=payload["failure_receipt_sha256"],
        retry_id=payload["retry_id"],
    )


def _validate_failure_events(
    events: Sequence[Mapping[str, Any]],
    *,
    call_id: str,
    submitted: bool,
) -> None:
    if not events or any(value.get("call_id") != call_id for value in events):
        raise ContractValidationError("Pi Scene provider-failure call events changed")
    if any(value.get("owner") != TRANSPORT_RETRY_OWNER for value in events):
        raise ContractValidationError("Pi Scene provider-failure owner changed")
    identity_fields = (
        "schema_version",
        "call_id",
        "owner",
        "operation",
        "route",
        "model",
        "effort",
        "stored_thread_sha256",
    )
    identity = tuple(events[0].get(field) for field in identity_fields)
    if (
        identity[0] != "cera.continuous_provider_call_ledger_event.v3"
        or any(tuple(value.get(field) for field in identity_fields) != identity for value in events)
        or not all(
            isinstance(events[0].get(field), str) and bool(str(events[0].get(field)).strip())
            for field in ("operation", "route", "model")
        )
        or not re_is_sha256(str(events[0].get("stored_thread_sha256", "")))
    ):
        raise ContractValidationError("Pi Scene provider-failure call identity changed")
    states = tuple(value.get("state") for value in events)
    prefix_options = (
        ("prepared_not_invoked",),
        ("prepared_not_invoked", "worker_started_not_invoked"),
        (
            "prepared_not_invoked",
            "worker_started_not_invoked",
            "worker_preflight_not_invoked",
        ),
    )
    expected = tuple(
        (*prefix, "transport_invoked", "provider_failed")
        if submitted
        else (*prefix, "pretransport_failed")
        for prefix in prefix_options
    )
    if states not in expected:
        raise ContractValidationError("Pi Scene provider-failure terminal state changed")
    if (
        not isinstance(events[-1].get("failure_type"), str)
        or not str(events[-1]["failure_type"]).strip()
    ):
        raise ContractValidationError("Pi Scene provider-failure type is absent")
    classified = classify_planner_call_events(
        events,
        call_id=call_id,
        stored_thread_sha256=str(events[0].get("stored_thread_sha256", "")),
    )
    expected_disposition = "provider_failed" if submitted else "pretransport_failed"
    if classified != expected_disposition:
        raise ContractValidationError("Pi Scene provider-failure classification changed")


def classify_planner_call_events(
    events: Sequence[Mapping[str, Any]],
    *,
    call_id: str | None = None,
    stored_thread_sha256: str | None = None,
) -> str:
    """Validate and classify one exact v3 Planner provider-call span.

    The append-only call ledger intentionally charges an unresolved prepared
    prefix conservatively.  Such a prefix, and a prefix ending only at
    ``transport_invoked``, are therefore classified as ambiguous rather than
    proven zero-dispatch.  Only a closed ``pretransport_failed`` event proves
    that the provider transport was not invoked.
    """

    if not events:
        raise ContractValidationError("Pi Scene Planner call span is empty")
    first = events[0]
    decoded = tuple(from_mapping(ProviderCallLedgerEventV1, dict(value)) for value in events)
    resolved_call_id = first.get("call_id")
    resolved_thread = first.get("stored_thread_sha256")
    if (
        not isinstance(resolved_call_id, str)
        or not resolved_call_id.strip()
        or not isinstance(resolved_thread, str)
        or not re_is_sha256(resolved_thread)
        or (call_id is not None and resolved_call_id != call_id)
        or (stored_thread_sha256 is not None and resolved_thread != stored_thread_sha256)
    ):
        raise ContractValidationError("Pi Scene Planner call identity changed")
    identity_fields = (
        "schema_version",
        "call_id",
        "owner",
        "operation",
        "route",
        "model",
        "effort",
        "stored_thread_sha256",
    )
    identity = tuple(first.get(field) for field in identity_fields)
    if (
        identity[0] != _PROVIDER_LEDGER_EVENT_SCHEMA
        or identity[2] != TRANSPORT_RETRY_OWNER
        or any(tuple(value.get(field) for field in identity_fields) != identity for value in events)
        or not all(
            isinstance(first.get(field), str) and bool(str(first.get(field)).strip())
            for field in ("operation", "route", "model")
        )
        or (
            first.get("effort") is not None
            and (not isinstance(first.get("effort"), str) or not str(first.get("effort")).strip())
        )
    ):
        raise ContractValidationError("Pi Scene Planner call authority changed")
    indices = tuple(value.get("event_index") for value in events)
    first_index = indices[0]
    if (
        type(first_index) is not int
        or first_index < 1
        or indices != tuple(range(first_index, first_index + len(events)))
    ):
        raise ContractValidationError("Pi Scene Planner call event order changed")
    for value in events:
        for field in (
            "provider_receipt_sha256",
            "failure_receipt_sha256",
            "operation_telemetry_sha256",
            "tool_sequence_sha256",
        ):
            candidate = value.get(field)
            if candidate is not None and (
                not isinstance(candidate, str) or not re_is_sha256(candidate)
            ):
                raise ContractValidationError("Pi Scene Planner call evidence is invalid")
        if (
            not isinstance(value.get("recorded_at_utc"), str)
            or not str(value["recorded_at_utc"]).strip()
        ):
            raise ContractValidationError("Pi Scene Planner call timestamp is invalid")
        state = value.get("state")
        failure_type = value.get("failure_type")
        if state in _PLANNER_CALL_FAILURE_STATES:
            if not isinstance(failure_type, str) or not failure_type.strip():
                raise ContractValidationError("Pi Scene Planner failure type is absent")
        elif failure_type is not None:
            raise ContractValidationError("Pi Scene Planner failure type escaped its terminal")
        if state in {
            "prepared_not_invoked",
            "worker_started_not_invoked",
            "worker_preflight_not_invoked",
            "transport_invoked",
            "pretransport_failed",
        } and any(
            value.get(field) is not None
            for field in (
                "provider_receipt_sha256",
                "failure_receipt_sha256",
                "operation_telemetry_sha256",
                "tool_sequence_sha256",
            )
        ):
            raise ContractValidationError(
                "Pi Scene Planner pre-completion evidence escaped its state"
            )
        if state == "provider_failed" and (
            value.get("provider_receipt_sha256") is not None
            or value.get("tool_sequence_sha256") is not None
        ):
            raise ContractValidationError("Pi Scene Planner failure evidence changed shape")
        if (
            state
            in {
                "provider_completed",
                "provider_completed_post_validation_failed",
                "typed_accepted",
            }
            and value.get("failure_receipt_sha256") is not None
        ):
            raise ContractValidationError("Pi Scene Planner completion retained failure evidence")

    raw_states = tuple(value.get("state") for value in events)
    states: tuple[str, ...] = tuple(value.state.value for value in decoded)
    if raw_states != states:
        raise ContractValidationError("Pi Scene Planner call state decoding changed")
    valid_sequences: dict[tuple[str, ...], str] = {}
    for prefix in _PLANNER_CALL_PREFIXES:
        valid_sequences[prefix] = "dispatch_outcome_ambiguous"
        valid_sequences[(*prefix, "pretransport_failed")] = "pretransport_failed"
    for prefix in (_PLANNER_CALL_PREFIXES[0], _PLANNER_CALL_PREFIXES[2]):
        valid_sequences[(*prefix, "transport_invoked")] = "dispatch_outcome_ambiguous"
        valid_sequences[(*prefix, "transport_invoked", "provider_failed")] = "provider_failed"
        valid_sequences[(*prefix, "transport_invoked", "provider_completed")] = "provider_completed"
        valid_sequences[
            (
                *prefix,
                "transport_invoked",
                "provider_completed",
                "provider_completed_post_validation_failed",
            )
        ] = "provider_completed_post_validation_failed"
        valid_sequences[(*prefix, "transport_invoked", "provider_completed", "typed_accepted")] = (
            "typed_accepted"
        )
    disposition = valid_sequences.get(states)
    if disposition is None:
        raise ContractValidationError("Pi Scene Planner call state graph changed")
    if "provider_completed" in states:
        completed = events[states.index("provider_completed")]
        terminal = events[-1]
        for field in (
            "provider_receipt_sha256",
            "operation_telemetry_sha256",
            "tool_sequence_sha256",
        ):
            if terminal.get(field) != completed.get(field):
                raise ContractValidationError(
                    "Pi Scene Planner completion evidence changed after decoding"
                )
    # Ensure strict decoding was not optimized away and that enum decoding
    # agrees with the raw state graph used for durable hashes.
    return disposition


def provider_ledger_dispatched_call_count(
    events: Sequence[Mapping[str, Any]],
) -> int:
    """Mirror the conservative durable provider-call accounting policy."""

    terminal_by_call: dict[str, str] = {}
    invoked: set[str] = set()
    for value in events:
        call_id = value.get("call_id")
        state = value.get("state")
        if not isinstance(call_id, str) or not isinstance(state, str):
            raise ContractValidationError("Pi Scene provider ledger event is invalid")
        terminal_by_call[call_id] = state
        if state == "transport_invoked":
            invoked.add(call_id)
    unresolved = {
        call_id
        for call_id, state in terminal_by_call.items()
        if state
        in {
            "prepared_not_invoked",
            "worker_started_not_invoked",
            "worker_preflight_not_invoked",
        }
    }
    return len(invoked | unresolved)


def _safe_object_hash(value: Any) -> str | None:
    if value is None:
        return None
    try:
        return canonical_sha256(value)
    except Exception:
        return canonical_sha256({"type": type(value).__name__})


def _valid_retry_id(value: object) -> bool:
    return (
        isinstance(value, str)
        and value.startswith("retry-")
        and len(value) == 70
        and all(character in "0123456789abcdef" for character in value[6:])
    )
