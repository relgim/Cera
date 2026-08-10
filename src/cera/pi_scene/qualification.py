"""Frozen, fail-closed qualification contracts for the full-model CERA route.

The qualification runner deliberately owns no story logic and no automatic
provider retry.  It submits two ordered, retained-session campaigns to
``cera-alpha``, verifies each committed HTTP projection, and reconciles that
projection with the append-only Sol and DeepSeek ledgers. A closed, naturally
occurring ordinary-Planner transport failure may receive at most two
manifest-authorized manual Retry actions after authenticated GET eligibility,
for three total Planner attempts. Each distinct backend-issued Retry ID is
POSTed at most once, and every terminal result is reconciled only through
authenticated GET. One explicit creator Regenerate may replace a noncritical
rejected first pass; both earlier outcomes remain visible in evidence. Exact
adult prose is never copied into qualification evidence; only its response hash
and protected custody hashes are retained.
"""

from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from statistics import median
from typing import Any, Protocol, cast

from cera.errors import ContractValidationError, StateConflictError
from cera.serialization import (
    bytes_sha256,
    canonical_bytes,
    canonical_sha256,
    re_is_sha256,
    text_sha256,
)

from .http_contracts import PI_SCENE_AUTO_MODEL, PI_SCENE_PROFILE

QUALIFICATION_FIXTURE_SCHEMA = "cera.pi_scene.full_model_qualification_fixtures.v1"
QUALIFICATION_MANIFEST_SCHEMA = "cera.pi_scene.full_model_qualification_manifest.v3"
QUALIFICATION_RESULT_SCHEMA = "cera.pi_scene.full_model_qualification_result.v3"

SOL_FAMILY_CEILING = 60
DEEPSEEK_HTTP_OPERATION_CEILING = 480
DEEPSEEK_PER_INVOCATION_CEILING = 6
TERRA_CEILING = 0

# The first Planner operation on each physical thread hydrates world/context
# state and is reported separately, including a fresh thread after transport
# recovery. Later retained calls may finish within their hard transport bound,
# but three minutes is a diagnostic concern visible in qualification evidence.
RETAINED_PLANNER_LATENCY_CONCERN_MS = 180_000
QUALIFICATION_PLANNER_REASONING_EFFORT = "medium"
QUALIFICATION_PROVIDER_STAGE_HARD_TIMEOUT_SECONDS = 600
QUALIFICATION_MAX_SEQUENTIAL_PROVIDER_STAGES = 6
QUALIFICATION_HTTP_HARD_TIMEOUT_SECONDS = (
    QUALIFICATION_MAX_SEQUENTIAL_PROVIDER_STAGES + 1
) * QUALIFICATION_PROVIDER_STAGE_HARD_TIMEOUT_SECONDS
TRANSPORT_RETRY_STATUS_POLL_SECONDS = 0.25
# If the one POST disconnects immediately, the replacement can still be
# completing Planner, Writer, Luna, repair, and Recorder work. Read-only GET
# reconciliation therefore outlives the same complete outer HTTP boundary.
TRANSPORT_RETRY_STATUS_TIMEOUT_SECONDS = QUALIFICATION_HTTP_HARD_TIMEOUT_SECONDS + 15

MANUAL_PLANNER_TRANSPORT_RETRY_POLICY: Mapping[str, Any] = {
    "authorized": True,
    "logic_owner": "planner",
    "route": "ordinary",
    "maximum_actions_per_prompt": 2,
    "maximum_total_provider_attempts": 3,
    "automatic": False,
    "fallback": False,
    "request_body": {},
    "pre_dispatch_status": "authenticated_get_eligible",
    "post_dispatch_reconciliation": "authenticated_get_only",
    "terminal_failure_projection": "critical_provider_stage_error",
    "terminal_status_schema": "cera.pi_scene.transport_retry_status.v2",
    "terminal_status_state": "attempts_exhausted",
    "terminal_failure_schema": "cera.provider_stage_retry_exhausted.v1",
    "terminal_retry_action_enabled": False,
}

FINAL_PROVIDER_FAILURE_CLASSES = frozenset(
    {
        "transport_timeout",
        "provider_unavailable",
        "provider_process_failed",
        "provider_stream_incomplete",
        "provider_completion_incomplete",
        "provider_output_invalid",
    }
)

QUALIFICATION_ACTION_BUDGETS: Mapping[str, Any] = {
    "technical_provider_retry": {
        "authority_scope": "branch_generation_stage_occurrence",
        "maximum_stage_attempts": 3,
        "maximum_manual_retry_actions": 2,
        "automatic_provider_redispatch": False,
    },
    "semantic_regenerate": {
        "authority_scope": "review_candidate",
        "maximum_explicit_actions_per_rejected_first_pass": 1,
    },
    "replan": {
        "authority_scope": "accepted_generation",
        "maximum_actions_per_qualification_fixture": 0,
    },
    "recorder_repair": {
        "authority_scope": "accepted_turn_recording",
        "maximum_actions_per_qualification_fixture": 0,
        "separate_from_provider_retry": True,
    },
}

QUALIFICATION_COMPLETE_GENERATION_CEILINGS: Mapping[str, Any] = {
    "ordinary": {
        "maximum_stage_occurrences": {
            "planner": 1,
            "writer": 2,
            "semantic_validator": 2,
            "recorder": 1,
        },
        "maximum_codex_operations": 9,
        "maximum_deepseek_http_operations": 54,
    },
    "adult": {
        "maximum_stage_occurrences": {
            "planner_transition": 1,
            "adult_scene": 1,
            "adult_filter": 1,
        },
        "maximum_codex_operations": 3,
        "maximum_deepseek_http_operations": 36,
    },
    "deepseek_http_operations_per_stage_attempt": DEEPSEEK_PER_INVOCATION_CEILING,
}

QUALIFICATION_EXECUTION_POLICY: Mapping[str, Any] = {
    "one_sequential_session_per_phase": True,
    "backend_route_order": ["ordinary"] * 5 + ["adult"] * 5 + ["ordinary"] * 5 + ["adult"] * 5,
    "sillytavern_route_order": ["ordinary"] * 3 + ["adult"] * 3 + ["ordinary"] * 2 + ["adult"] * 2,
    "first_pass_outcome_preserved": True,
    "maximum_explicit_regenerates_per_prompt": 1,
    "action_budgets": deepcopy(QUALIFICATION_ACTION_BUDGETS),
    "complete_generation_ceilings": deepcopy(QUALIFICATION_COMPLETE_GENERATION_CEILINGS),
    "automatic_retry": False,
    "manual_planner_transport_retry": dict(MANUAL_PLANNER_TRANSPORT_RETRY_POLICY),
    "fallback": False,
    "model_substitution": False,
    "planner_reasoning_effort": QUALIFICATION_PLANNER_REASONING_EFFORT,
    "semantic_validator_reasoning_effort": "xhigh",
    "ordinary_semantic_pass_auto_accept_required": True,
    "adult_filter_pass_atomic_accept_required": True,
    "exact_adult_prose_in_qualification_evidence": False,
    "dynamic_loopback_only_cera_port": True,
    "installed_cera_port_5101_untouched": True,
    "frozen_isolated_sillytavern_tree_required": True,
    "phase_order": ["backend", "sillytavern"],
}

EXPECTED_PHASE_COUNTS: Mapping[str, Mapping[str, int]] = {
    "backend": {"ordinary": 10, "adult": 10},
    "sillytavern": {"ordinary": 5, "adult": 5},
}


class QualificationPhase(StrEnum):
    BACKEND = "backend"
    SILLYTAVERN = "sillytavern"


class QualificationRoute(StrEnum):
    ORDINARY = "ordinary"
    ADULT = "adult"


class _CriticalProviderStageError(StateConflictError):
    """Qualification reached the closed Planner provider-attempt ceiling."""


@dataclass(frozen=True, slots=True)
class QualificationFixtureV1:
    fixture_id: str
    phase: QualificationPhase
    initial_route: QualificationRoute
    expected_route: QualificationRoute
    expected_next_route: QualificationRoute
    adult_craft_mode: str
    user_source: str

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9-]{3,63}", self.fixture_id):
            raise ContractValidationError("qualification fixture identity is invalid")
        if self.adult_craft_mode not in {"off", "on", "ex"}:
            raise ContractValidationError("qualification adult-craft mode is invalid")
        if self.expected_route is QualificationRoute.ORDINARY:
            if (
                self.initial_route is not QualificationRoute.ORDINARY
                or self.expected_next_route is not QualificationRoute.ORDINARY
            ):
                raise ContractValidationError("ordinary qualification changed route ownership")
            if self.adult_craft_mode != "off":
                raise ContractValidationError("ordinary qualification enabled adult craft")
        elif self.adult_craft_mode == "off":
            raise ContractValidationError("adult qualification omitted adult craft retrieval")
        if len(self.user_source.strip()) < 40:
            raise ContractValidationError("qualification fixture source is too short")


@dataclass(frozen=True, slots=True)
class ClientResponseV1:
    """One observed HTTP operation without persisting response prose."""

    transport: str
    path: str
    status_code: int
    duration_ms: int
    body: Mapping[str, Any]

    def __post_init__(self) -> None:
        if not self.transport.strip() or not self.path.startswith("/"):
            raise ContractValidationError("qualification HTTP identity is invalid")
        if type(self.status_code) is not int or not 100 <= self.status_code <= 599:
            raise ContractValidationError("qualification HTTP status is invalid")
        if type(self.duration_ms) is not int or self.duration_ms < 0:
            raise ContractValidationError("qualification HTTP duration is invalid")


@dataclass(frozen=True, slots=True)
class RejectionReviewV1:
    """One noncritical first-pass rejection eligible for a user Regenerate."""

    review_id: str
    conflict_sha256: str
    provider_operations: Mapping[str, int]
    projection: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class TransportRetryFailureV1:
    """One exact, closed Planner transport failure eligible for manual Retry."""

    request_id: str
    retry_id: str
    effect_proof_sha256: str
    provider_operation_submitted: bool | None
    response_sha256: str | None

    @property
    def request_id_sha256(self) -> str:
        return text_sha256(self.request_id)

    @property
    def retry_id_sha256(self) -> str:
        return text_sha256(self.retry_id)


@dataclass(frozen=True, slots=True)
class TransportRetryResolutionV1:
    """One bounded successor chain, before final whole-turn ledger parity."""

    failure: TransportRetryFailureV1
    failures: tuple[TransportRetryFailureV1, ...]
    failed_calls: tuple[Mapping[str, Any], ...]
    completion_response: ClientResponseV1 | None
    actions: tuple[Mapping[str, Any], ...]
    critical_failure: Mapping[str, Any] | None
    retry_action_count: int
    duration_ms: int


class QualificationClient(Protocol):
    def complete(
        self,
        *,
        fixture: QualificationFixtureV1,
        session_id: str,
        payload: Mapping[str, Any],
    ) -> ClientResponseV1: ...

    def regenerate(
        self,
        *,
        fixture: QualificationFixtureV1,
        review_id: str,
    ) -> ClientResponseV1: ...

    def transport_retry_status(self, *, retry_id: str) -> ClientResponseV1: ...

    def retry_transport(self, *, retry_id: str) -> ClientResponseV1: ...


@dataclass(frozen=True, slots=True)
class ProviderLedgerSnapshotV1:
    sol_events: tuple[Mapping[str, Any], ...]
    deepseek_events: tuple[Mapping[str, Any], ...]

    @classmethod
    def load(cls, runtime_root: Path) -> ProviderLedgerSnapshotV1:
        root = runtime_root.resolve()
        return cls(
            sol_events=_load_sol_events(root / "SOL_PROVIDER_CALLS.jsonl"),
            deepseek_events=_load_deepseek_events(root / "DEEPSEEK_PROVIDER_OPERATIONS.jsonl"),
        )

    @property
    def sol_transport_operations(self) -> int:
        return sum(value.get("state") == "transport_invoked" for value in self.sol_events)

    @property
    def sol_charged_operations(self) -> int:
        return _sol_charged_operation_count(self.sol_events)

    @property
    def deepseek_started_operations(self) -> int:
        return sum(
            value.get("event") == "provider_operation_started" for value in self.deepseek_events
        )

    def delta_from(self, prior: ProviderLedgerSnapshotV1) -> ProviderLedgerDeltaV1:
        if self.sol_events[: len(prior.sol_events)] != prior.sol_events:
            raise StateConflictError("Sol provider ledger changed its existing prefix")
        if self.deepseek_events[: len(prior.deepseek_events)] != prior.deepseek_events:
            raise StateConflictError("DeepSeek provider ledger changed its existing prefix")
        return ProviderLedgerDeltaV1(
            sol_events=self.sol_events[len(prior.sol_events) :],
            deepseek_events=self.deepseek_events[len(prior.deepseek_events) :],
        )


@dataclass(frozen=True, slots=True)
class ProviderLedgerDeltaV1:
    sol_events: tuple[Mapping[str, Any], ...]
    deepseek_events: tuple[Mapping[str, Any], ...]

    @property
    def sol_transport_operations(self) -> int:
        return sum(value.get("state") == "transport_invoked" for value in self.sol_events)

    @property
    def sol_charged_operations(self) -> int:
        return _sol_charged_operation_count(self.sol_events)

    @property
    def deepseek_started_operations(self) -> int:
        return sum(
            value.get("event") == "provider_operation_started" for value in self.deepseek_events
        )

    @property
    def deepseek_completed_operations(self) -> int:
        return sum(
            value.get("event") == "provider_operation_completed" for value in self.deepseek_events
        )

    @property
    def deepseek_cached_input_tokens(self) -> int:
        return sum(
            _nonnegative_int(value.get("cached_input_tokens"), default=0)
            for value in self.deepseek_events
            if value.get("event") == "provider_operation_completed"
        )

    @property
    def deepseek_input_tokens(self) -> int:
        return sum(
            _nonnegative_int(value.get("input_tokens"), default=0)
            for value in self.deepseek_events
            if value.get("event") == "provider_operation_completed"
        )


class QualificationEvidenceStore:
    """Append concise hash-only evidence before returning each fixture result."""

    def __init__(self, root: Path) -> None:
        if not root.is_absolute():
            raise ContractValidationError("qualification evidence root must be absolute")
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.events_path = self.root / "QUALIFICATION_EVENTS.jsonl"

    def append(self, payload: Mapping[str, Any]) -> None:
        value = {
            **dict(payload),
            "recorded_at_utc": datetime.now(UTC).isoformat(timespec="microseconds"),
        }
        with self.events_path.open("ab") as stream:
            stream.write(canonical_bytes(value) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())

    def publish_result(self, phase: QualificationPhase, payload: Mapping[str, Any]) -> Path:
        path = self.root / f"{phase.value.upper()}_RESULT.json"
        if path.exists():
            raise StateConflictError("qualification phase result is already published")
        _atomic_write_json(path, payload)
        return path


class FullModelQualificationRunner:
    """Run one fixed phase through a supplied direct or SillyTavern client."""

    def __init__(
        self,
        *,
        manifest: Mapping[str, Any],
        runtime_root: Path,
        evidence_root: Path,
    ) -> None:
        validate_qualification_manifest(manifest)
        self.manifest = dict(manifest)
        self.runtime_root = runtime_root.resolve()
        self.evidence = QualificationEvidenceStore(evidence_root.resolve())

    def run_phase(
        self,
        phase: QualificationPhase,
        fixtures: Sequence[QualificationFixtureV1],
        client: QualificationClient,
    ) -> dict[str, Any]:
        campaign = self.start_phase(phase, fixtures)
        campaign.run_segment(
            client=client,
            runtime_root=self.runtime_root,
            turn_count=len(campaign.fixtures),
        )
        return campaign.finish()

    def start_phase(
        self,
        phase: QualificationPhase,
        fixtures: Sequence[QualificationFixtureV1],
    ) -> QualificationCampaignRun:
        return QualificationCampaignRun(parent=self, phase=phase, fixtures=fixtures)


class QualificationCampaignRun:
    """One sequential accepted branch that may cross a deliberate restart."""

    def __init__(
        self,
        *,
        parent: FullModelQualificationRunner,
        phase: QualificationPhase,
        fixtures: Sequence[QualificationFixtureV1],
    ) -> None:
        selected = _ordered_phase_fixtures(phase, fixtures)
        _validate_phase_fixture_counts(phase, selected)
        _validate_phase_route_lineage(selected)
        self.parent = parent
        self.phase = phase
        self.fixtures = selected
        self.session_id = _session_id(parent.manifest, phase)
        self.next_index = 0
        self.history: list[dict[str, str]] = []
        results: list[dict[str, Any]] = []
        self.results = results
        self.failure: BaseException | None = None
        self.sol_operations = 0
        self.deepseek_operations = 0
        self.deepseek_cached_input_tokens = 0
        self.deepseek_input_tokens = 0
        self.planner_operation_count = 0
        self.planner_thread_operation_counts: dict[str, int] = {}
        self.provider_operation_records: list[dict[str, Any]] = []
        self.transport_retry_actions = 0
        self.transport_retry_chains = 0
        self.transport_retry_terminal_critical_failures = 0
        self.restart_count = 0

    def run_segment(
        self,
        *,
        client: QualificationClient,
        runtime_root: Path,
        turn_count: int,
        restarted: bool = False,
    ) -> None:
        if self.failure is not None:
            raise StateConflictError("failed qualification campaign cannot continue")
        if type(turn_count) is not int or turn_count < 1:
            raise ContractValidationError("qualification segment turn count is invalid")
        stop = self.next_index + turn_count
        if stop > len(self.fixtures):
            raise ContractValidationError("qualification segment exceeds its fixture set")
        if restarted:
            self.restart_count += 1
        segment_root = runtime_root.resolve()
        for offset in range(self.next_index, stop):
            fixture = self.fixtures[offset]
            turn_index = offset + 1
            before = ProviderLedgerSnapshotV1.load(segment_root)
            request_messages = [*self.history, {"role": "user", "content": fixture.user_source}]
            payload = qualification_request_payload(
                fixture,
                session_id=self.session_id,
                messages=request_messages,
            )
            planner_latency: list[dict[str, Any]] | None = None
            retry_resolution: TransportRetryResolutionV1 | None = None
            try:
                initial_response = client.complete(
                    fixture=fixture,
                    session_id=self.session_id,
                    payload=payload,
                )
                self.parent.evidence.append(
                    {
                        "schema_version": "cera.pi_scene.qualification_http_event.v1",
                        "event": "client_http_first_pass_completed",
                        "fixture_id": fixture.fixture_id,
                        "phase": self.phase.value,
                        "turn_index": turn_index,
                        "transport": initial_response.transport,
                        "path": initial_response.path,
                        "status_code": initial_response.status_code,
                        "duration_ms": initial_response.duration_ms,
                        "response_sha256": canonical_sha256(initial_response.body),
                    }
                )
                retry_resolution = self._resolve_manual_transport_retry(
                    fixture=fixture,
                    turn_index=turn_index,
                    client=client,
                    runtime_root=segment_root,
                    before=before,
                    response=initial_response,
                )
                if retry_resolution is not None and retry_resolution.completion_response is None:
                    self._record_critical_transport_retry_failure(
                        fixture=fixture,
                        turn_index=turn_index,
                        runtime_root=segment_root,
                        before=before,
                        initial_response=initial_response,
                        resolution=retry_resolution,
                    )
                    break
                response = initial_response
                if retry_resolution is not None:
                    assert retry_resolution.completion_response is not None
                    response = retry_resolution.completion_response
                first = _classify_completion_response(fixture, response)
                first_pass = isinstance(first, Mapping) and first["first_pass_accepted"] is True
                regeneration: ClientResponseV1 | None = None
                projections: list[Mapping[str, Any]] = []
                projection: Mapping[str, Any]
                if isinstance(first, RejectionReviewV1):
                    self.parent.evidence.append(
                        {
                            "schema_version": (
                                "cera.pi_scene.qualification_first_pass_rejection.v1"
                            ),
                            "event": "first_pass_rejected",
                            "fixture_id": fixture.fixture_id,
                            "phase": self.phase.value,
                            "turn_index": turn_index,
                            "review_id_sha256": text_sha256(first.review_id),
                            "conflict_sha256": first.conflict_sha256,
                            "provider_operations": first.provider_operations,
                        }
                    )
                    regeneration = client.regenerate(
                        fixture=fixture,
                        review_id=first.review_id,
                    )
                    self.parent.evidence.append(
                        {
                            "schema_version": "cera.pi_scene.qualification_http_event.v1",
                            "event": "explicit_user_regenerate_completed",
                            "fixture_id": fixture.fixture_id,
                            "phase": self.phase.value,
                            "turn_index": turn_index,
                            "transport": regeneration.transport,
                            "path": regeneration.path,
                            "status_code": regeneration.status_code,
                            "duration_ms": regeneration.duration_ms,
                            "response_sha256": canonical_sha256(regeneration.body),
                        }
                    )
                    successor = _successor_completion(regeneration)
                    projection = _validate_completion_response(
                        fixture,
                        successor,
                        regenerated=True,
                    )
                    projections.extend((first.projection, projection))
                else:
                    projection = first
                    projections.append(projection)
                accepted_response = (
                    response if regeneration is None else _successor_completion(regeneration)
                )
                accepted_prose = _visible_prose(accepted_response.body)
                self.history.extend(
                    (
                        {"role": "user", "content": fixture.user_source},
                        {"role": "assistant", "content": accepted_prose},
                    )
                )
                after = ProviderLedgerSnapshotV1.load(segment_root)
                delta = after.delta_from(before)
                operation_records = _provider_operation_records(delta)
                self.provider_operation_records.extend(operation_records)
                retry_chains: list[dict[str, Any]] = []
                if retry_resolution is not None:
                    retry_chain = _finalize_transport_retry_chain(
                        retry_resolution,
                        operation_records=operation_records,
                    )
                    retry_chains.append(retry_chain)
                    self.transport_retry_chains += 1
                    self.parent.evidence.append(
                        {
                            "schema_version": (
                                "cera.pi_scene.qualification_transport_retry_chain.v1"
                            ),
                            "event": "manual_planner_transport_retry_succeeded",
                            "fixture_id": fixture.fixture_id,
                            "phase": self.phase.value,
                            "turn_index": turn_index,
                            **retry_chain,
                        }
                    )
                telemetry = _validate_provider_delta(
                    fixture,
                    projections,
                    delta,
                    retry_chains=retry_chains,
                )
                planner_latency = self._planner_latency_observations(operation_records)
                total_http_latency_ms = (
                    initial_response.duration_ms
                    + (0 if retry_resolution is None else retry_resolution.duration_ms)
                    + (0 if regeneration is None else regeneration.duration_ms)
                )
                provider_transport_duration_ms = sum(
                    cast(int, operation["duration_ms"])
                    for operation in operation_records
                    if type(operation.get("duration_ms")) is int
                )
                for operation in operation_records:
                    self.parent.evidence.append(
                        {
                            "schema_version": ("cera.pi_scene.qualification_provider_operation.v1"),
                            "event": "provider_http_operation",
                            "fixture_id": fixture.fixture_id,
                            "phase": self.phase.value,
                            "turn_index": turn_index,
                            **operation,
                        }
                    )
                result = {
                    "fixture_id": fixture.fixture_id,
                    "phase": self.phase.value,
                    "turn_index": turn_index,
                    "initial_route": fixture.initial_route.value,
                    "expected_route": fixture.expected_route.value,
                    "expected_next_route": fixture.expected_next_route.value,
                    "observed_route": projection["observed_route"],
                    "observed_next_route": projection["observed_next_route"],
                    "session_id_sha256": text_sha256(self.session_id),
                    "source_sha256": text_sha256(fixture.user_source),
                    "status": "passed",
                    "first_pass_accepted": first_pass,
                    "explicit_regenerate_actions": (0 if regeneration is None else 1),
                    "transport_retry_actions": (
                        0 if retry_resolution is None else retry_resolution.retry_action_count
                    ),
                    "transport_retry_chains": retry_chains,
                    "automatic_repair_actions": projection["automatic_repair_actions"],
                    "initial_http_response_sha256": canonical_sha256(initial_response.body),
                    "first_pass_response_sha256": canonical_sha256(response.body),
                    "response_sha256": canonical_sha256(accepted_response.body),
                    "visible_prose_sha256": projection["visible_prose_sha256"],
                    "accepted_turn_id": projection["accepted_turn_id"],
                    "accepted_receipt_sha256": projection["accepted_receipt_sha256"],
                    "provider_operations": projection["provider_operations"],
                    "latency_ms": total_http_latency_ms,
                    "provider_transport_duration_ms": provider_transport_duration_ms,
                    "non_provider_http_duration_ms": max(
                        0,
                        total_http_latency_ms - provider_transport_duration_ms,
                    ),
                    "planner_latency": planner_latency,
                    **telemetry,
                }
                self.results.append(result)
                self.sol_operations += delta.sol_charged_operations
                self.deepseek_operations += delta.deepseek_started_operations
                self.deepseek_cached_input_tokens += delta.deepseek_cached_input_tokens
                self.deepseek_input_tokens += delta.deepseek_input_tokens
                self.parent.evidence.append(
                    {
                        "schema_version": "cera.pi_scene.qualification_fixture_result.v1",
                        "event": "fixture_passed",
                        **result,
                    }
                )
            except BaseException as exc:
                self.failure = exc
                after = ProviderLedgerSnapshotV1.load(segment_root)
                delta = after.delta_from(before)
                failed_operation_records = _provider_operation_records(delta)
                self.provider_operation_records.extend(failed_operation_records)
                if planner_latency is None:
                    planner_latency = self._planner_latency_observations(failed_operation_records)
                failed = {
                    "fixture_id": fixture.fixture_id,
                    "phase": self.phase.value,
                    "turn_index": turn_index,
                    "initial_route": fixture.initial_route.value,
                    "expected_route": fixture.expected_route.value,
                    "expected_next_route": fixture.expected_next_route.value,
                    "session_id_sha256": text_sha256(self.session_id),
                    "source_sha256": text_sha256(fixture.user_source),
                    "status": "failed",
                    **_closed_failure_projection(exc),
                    "sol_operations_observed": delta.sol_transport_operations,
                    "sol_charged_operations_observed": delta.sol_charged_operations,
                    "deepseek_operations_observed": delta.deepseek_started_operations,
                    "planner_latency": planner_latency,
                }
                self.results.append(failed)
                self.sol_operations += delta.sol_charged_operations
                self.deepseek_operations += delta.deepseek_started_operations
                self.parent.evidence.append(
                    {
                        "schema_version": "cera.pi_scene.qualification_fixture_result.v1",
                        "event": "fixture_failed",
                        **failed,
                    }
                )
                break
            self.next_index = turn_index
        if self.sol_operations > int(
            cast(Mapping[str, int], self.parent.manifest["provider_ceilings"])["sol"]
        ):
            raise StateConflictError("qualification exceeded its Sol ceiling")
        if self.deepseek_operations > int(
            cast(Mapping[str, int], self.parent.manifest["provider_ceilings"])[
                "deepseek_http_operations"
            ]
        ):
            raise StateConflictError("qualification exceeded its DeepSeek ceiling")

    def _resolve_manual_transport_retry(
        self,
        *,
        fixture: QualificationFixtureV1,
        turn_index: int,
        client: QualificationClient,
        runtime_root: Path,
        before: ProviderLedgerSnapshotV1,
        response: ClientResponseV1,
    ) -> TransportRetryResolutionV1 | None:
        failure = _closed_transport_retry_failure(response)
        if failure is None:
            return None
        policy = self.parent.manifest.get("execution_policy")
        retry_policy = (
            policy.get("manual_planner_transport_retry") if isinstance(policy, Mapping) else None
        )
        if retry_policy != MANUAL_PLANNER_TRANSPORT_RETRY_POLICY:
            raise StateConflictError("qualification manual transport Retry is not authorized")
        if fixture.expected_route is not QualificationRoute.ORDINARY:
            raise StateConflictError("qualification transport Retry is ordinary Planner-only")
        status_reader = getattr(client, "transport_retry_status", None)
        retry_writer = getattr(client, "retry_transport", None)
        if not callable(status_reader) or not callable(retry_writer):
            raise StateConflictError("qualification client lacks manual transport Retry support")

        failed_snapshot = ProviderLedgerSnapshotV1.load(runtime_root)
        failed_delta = failed_snapshot.delta_from(before)
        failed_call = _closed_failed_planner_call(failure, failed_delta)
        failures = [failure]
        failed_calls = [failed_call]
        actions: list[Mapping[str, Any]] = []
        total_duration_ms = 0
        self._append_transport_failure_evidence(
            fixture=fixture,
            turn_index=turn_index,
            failure=failure,
            failed_call=failed_call,
            attempt_number=1,
        )

        for action_index in range(1, 3):
            current = failures[-1]
            pre_status_response = status_reader(retry_id=current.retry_id)
            pre_status = _validate_transport_retry_status(
                pre_status_response,
                failure=current,
            )
            if pre_status["state"] != "eligible":
                raise StateConflictError(
                    "qualification transport Retry was not stably eligible before dispatch"
                )

            # Each backend-issued identity is written at most once. The action
            # counter advances first, so response loss can lead only to GET
            # reconciliation, never to a repeated POST for the same identity.
            self.transport_retry_actions += 1
            action_ledger_before = ProviderLedgerSnapshotV1.load(runtime_root)
            self.parent.evidence.append(
                {
                    "schema_version": "cera.pi_scene.qualification_transport_retry_action.v2",
                    "event": "manual_planner_transport_retry_dispatched",
                    "fixture_id": fixture.fixture_id,
                    "phase": self.phase.value,
                    "turn_index": turn_index,
                    "request_id_sha256": current.request_id_sha256,
                    "retry_id_sha256": current.retry_id_sha256,
                    "effect_proof_sha256": current.effect_proof_sha256,
                    "retry_action_index": action_index,
                    "maximum_retry_actions": 2,
                    "request_body_sha256": canonical_sha256({}),
                    "pre_status_sha256": canonical_sha256(pre_status_response.body),
                }
            )
            post_response: ClientResponseV1 | None = None
            post_failure: dict[str, str] | None = None
            post_started_ns = time.perf_counter_ns()
            try:
                post_response = retry_writer(retry_id=current.retry_id)
            except Exception as exc:
                post_failure = _closed_failure_projection(exc)
            post_dispatch_duration_ms = max(
                0,
                (time.perf_counter_ns() - post_started_ns) // 1_000_000,
            )

            post_status_response, post_status, status_duration_ms = (
                _poll_terminal_transport_retry_status(
                    client,
                    failure=current,
                )
            )
            total_duration_ms += (
                pre_status_response.duration_ms + post_dispatch_duration_ms + status_duration_ms
            )
            action = _transport_retry_action_receipt(
                current,
                action_index=action_index,
                pre_status_response=pre_status_response,
                post_response=post_response,
                post_failure=post_failure,
                post_dispatch_duration_ms=post_dispatch_duration_ms,
                status_response=post_status_response,
                status=post_status,
                status_reconciliation_duration_ms=status_duration_ms,
            )
            actions.append(action)
            self.parent.evidence.append(
                {
                    "schema_version": "cera.pi_scene.qualification_transport_retry_action.v2",
                    "event": "manual_planner_transport_retry_terminal_observed",
                    "fixture_id": fixture.fixture_id,
                    "phase": self.phase.value,
                    "turn_index": turn_index,
                    **action,
                }
            )

            state = post_status["state"]
            if state == "succeeded":
                completion = cast(Mapping[str, Any], post_status["completion"])
                completion_sha256 = cast(str, post_status["completion_sha256"])
                if post_response is not None and post_response.status_code == 200:
                    if canonical_sha256(post_response.body) != completion_sha256:
                        raise StateConflictError(
                            "qualification Retry POST differs from authenticated GET completion"
                        )
                self.parent.evidence.append(
                    {
                        "schema_version": "cera.pi_scene.qualification_transport_retry_action.v2",
                        "event": "manual_planner_transport_retry_reconciled",
                        "fixture_id": fixture.fixture_id,
                        "phase": self.phase.value,
                        "turn_index": turn_index,
                        **action,
                    }
                )
                return TransportRetryResolutionV1(
                    failure=failure,
                    failures=tuple(failures),
                    failed_calls=tuple(failed_calls),
                    completion_response=ClientResponseV1(
                        transport="authenticated_transport_retry_status",
                        path=f"/v1/cera/transport-retries/{current.retry_id}",
                        status_code=200,
                        duration_ms=0,
                        body=completion,
                    ),
                    actions=tuple(actions),
                    critical_failure=None,
                    retry_action_count=len(actions),
                    duration_ms=total_duration_ms,
                )
            if state == "attempts_exhausted":
                if action_index != 2:
                    raise StateConflictError(
                        "qualification exhausted Planner attempts before the policy ceiling"
                    )
                backend_critical = cast(
                    Mapping[str, Any],
                    post_status["critical_provider_stage_failure"],
                )
                post_provider_operation_submitted = _validate_attempts_exhausted_post(
                    post_response,
                    failure=current,
                    critical=backend_critical,
                )
                action_ledger_after = ProviderLedgerSnapshotV1.load(runtime_root)
                terminal_call = _closed_exhausted_planner_call(
                    backend_critical,
                    action_ledger_after.delta_from(action_ledger_before),
                    prior_failed_calls=failed_calls,
                    post_provider_operation_submitted=(post_provider_operation_submitted),
                )
                if terminal_call["call_id"] in {
                    value["call_id"] for value in failed_calls
                } or terminal_call["stored_thread_sha256"] in {
                    value["stored_thread_sha256"] for value in failed_calls
                }:
                    raise StateConflictError(
                        "qualification final Planner attempt did not use a distinct thread"
                    )
                failed_calls.append(terminal_call)
                critical = _critical_provider_stage_failure(
                    backend=backend_critical,
                    failures=failures,
                    failed_calls=failed_calls,
                    actions=actions,
                )
                self._append_exhausted_transport_failure_evidence(
                    fixture=fixture,
                    turn_index=turn_index,
                    dispatch_failure=failures[-1],
                    failed_call=terminal_call,
                    critical=critical,
                )
                self.parent.evidence.append(
                    {
                        "schema_version": (
                            "cera.pi_scene.qualification_critical_provider_stage_failure.v1"
                        ),
                        "event": "planner_provider_attempt_limit_exhausted",
                        "fixture_id": fixture.fixture_id,
                        "phase": self.phase.value,
                        "turn_index": turn_index,
                        "critical_provider_stage_failure": dict(critical),
                        "critical_failure_sha256": canonical_sha256(critical),
                    }
                )
                return TransportRetryResolutionV1(
                    failure=failure,
                    failures=tuple(failures),
                    failed_calls=tuple(failed_calls),
                    completion_response=None,
                    actions=tuple(actions),
                    critical_failure=critical,
                    retry_action_count=len(actions),
                    duration_ms=total_duration_ms,
                )
            if state != "superseded":
                raise StateConflictError(
                    "qualification manual transport Retry did not reach a usable terminal status"
                )
            if action_index == 2:
                raise StateConflictError(
                    "qualification backend exposed a fourth Planner provider attempt"
                )

            successor = _superseding_transport_failure(
                current,
                status=post_status,
                post_response=post_response,
            )
            if successor.retry_id in {value.retry_id for value in failures}:
                raise StateConflictError("qualification Retry successor identity cycled")
            action_ledger_after = ProviderLedgerSnapshotV1.load(runtime_root)
            successor_call = _closed_failed_planner_call(
                successor,
                action_ledger_after.delta_from(action_ledger_before),
            )
            successor = replace(
                successor,
                provider_operation_submitted=cast(bool, successor_call["submitted"]),
            )
            if successor_call["call_id"] in {
                value["call_id"] for value in failed_calls
            } or successor_call["stored_thread_sha256"] in {
                value["stored_thread_sha256"] for value in failed_calls
            }:
                raise StateConflictError(
                    "qualification Retry successor did not use a distinct Planner attempt"
                )
            failures.append(successor)
            failed_calls.append(successor_call)
            self._append_transport_failure_evidence(
                fixture=fixture,
                turn_index=turn_index,
                failure=successor,
                failed_call=successor_call,
                attempt_number=len(failures),
            )
        raise StateConflictError("qualification transport Retry action bound changed")

    def _record_critical_transport_retry_failure(
        self,
        *,
        fixture: QualificationFixtureV1,
        turn_index: int,
        runtime_root: Path,
        before: ProviderLedgerSnapshotV1,
        initial_response: ClientResponseV1,
        resolution: TransportRetryResolutionV1,
    ) -> None:
        critical = resolution.critical_failure
        if critical is None or resolution.completion_response is not None:
            raise StateConflictError("qualification critical Retry resolution changed")
        after = ProviderLedgerSnapshotV1.load(runtime_root)
        delta = after.delta_from(before)
        operation_records = _provider_operation_records(delta)
        chain = _finalize_transport_retry_exhaustion(
            resolution,
            operation_records=operation_records,
        )
        telemetry = _validate_exhausted_transport_retry_delta(
            delta,
            failed_calls=resolution.failed_calls,
        )
        planner_latency = self._planner_latency_observations(operation_records)
        for operation in operation_records:
            self.parent.evidence.append(
                {
                    "schema_version": "cera.pi_scene.qualification_provider_operation.v1",
                    "event": "provider_http_operation",
                    "fixture_id": fixture.fixture_id,
                    "phase": self.phase.value,
                    "turn_index": turn_index,
                    **operation,
                }
            )
        self.transport_retry_chains += 1
        self.transport_retry_terminal_critical_failures += 1
        failure_identity = {
            "failure_type": "CriticalProviderStageError",
            "failure_category": "provider_stage_attempts_exhausted",
        }
        failed = {
            "fixture_id": fixture.fixture_id,
            "phase": self.phase.value,
            "turn_index": turn_index,
            "initial_route": fixture.initial_route.value,
            "expected_route": fixture.expected_route.value,
            "expected_next_route": fixture.expected_next_route.value,
            "session_id_sha256": text_sha256(self.session_id),
            "source_sha256": text_sha256(fixture.user_source),
            "status": "failed",
            **failure_identity,
            "failure_sha256": canonical_sha256(failure_identity),
            "critical_provider_stage_failure": dict(critical),
            "transport_retry_actions": resolution.retry_action_count,
            "transport_retry_chains": [chain],
            "automatic_transport_retry_actions": 0,
            "fallback_used": False,
            "initial_http_response_sha256": canonical_sha256(initial_response.body),
            "latency_ms": initial_response.duration_ms + resolution.duration_ms,
            "planner_latency": planner_latency,
            **telemetry,
        }
        self.results.append(failed)
        self.sol_operations += delta.sol_charged_operations
        self.deepseek_operations += delta.deepseek_started_operations
        self.parent.evidence.append(
            {
                "schema_version": "cera.pi_scene.qualification_fixture_result.v2",
                "event": "fixture_failed_critical_provider_stage",
                **failed,
            }
        )
        self.failure = _CriticalProviderStageError(
            "qualification critical Planner provider attempt limit exhausted"
        )

    def _append_transport_failure_evidence(
        self,
        *,
        fixture: QualificationFixtureV1,
        turn_index: int,
        failure: TransportRetryFailureV1,
        failed_call: Mapping[str, Any],
        attempt_number: int,
    ) -> None:
        self.parent.evidence.append(
            {
                "schema_version": "cera.pi_scene.qualification_transport_failure.v2",
                "event": "eligible_planner_transport_failure_observed",
                "fixture_id": fixture.fixture_id,
                "phase": self.phase.value,
                "turn_index": turn_index,
                "attempt_number": attempt_number,
                "request_id_sha256": failure.request_id_sha256,
                "retry_id_sha256": failure.retry_id_sha256,
                "effect_proof_sha256": failure.effect_proof_sha256,
                "provider_operation_submitted": failure.provider_operation_submitted,
                "failure_response_sha256": failure.response_sha256,
                "failed_call": dict(failed_call),
            }
        )

    def _append_exhausted_transport_failure_evidence(
        self,
        *,
        fixture: QualificationFixtureV1,
        turn_index: int,
        dispatch_failure: TransportRetryFailureV1,
        failed_call: Mapping[str, Any],
        critical: Mapping[str, Any],
    ) -> None:
        self.parent.evidence.append(
            {
                "schema_version": "cera.pi_scene.qualification_transport_failure.v2",
                "event": "terminal_planner_transport_failure_observed",
                "fixture_id": fixture.fixture_id,
                "phase": self.phase.value,
                "turn_index": turn_index,
                "attempt_number": 3,
                "request_id_sha256": dispatch_failure.request_id_sha256,
                "dispatch_retry_id_sha256": dispatch_failure.retry_id_sha256,
                "dispatch_effect_proof_sha256": dispatch_failure.effect_proof_sha256,
                "critical_failure_sha256": canonical_sha256(critical),
                "failed_call": dict(failed_call),
            }
        )

    def _planner_latency_observations(
        self,
        operation_records: Sequence[Mapping[str, Any]],
    ) -> list[dict[str, Any]]:
        planner_operations = [
            operation
            for operation in operation_records
            if operation.get("provider_family") == "sol" and operation.get("owner") == "planner"
        ]
        for operation in planner_operations:
            session_identity_sha256 = operation.get("session_identity_sha256")
            duration_ms = operation.get("duration_ms")
            if not isinstance(session_identity_sha256, str) or not re_is_sha256(
                session_identity_sha256
            ):
                raise StateConflictError("qualification Planner session identity is invalid")
            if duration_ms is not None and (type(duration_ms) is not int or duration_ms < 0):
                raise StateConflictError("qualification Planner duration is invalid")
            if type(operation.get("submitted")) is not bool:
                raise StateConflictError("qualification Planner submission state is invalid")

        observations: list[dict[str, Any]] = []
        for operation in planner_operations:
            session_identity_sha256 = cast(str, operation["session_identity_sha256"])
            if operation["submitted"] is False:
                # A proven local pretransport failure is not a model call and
                # consumes no grant. Retain the physical thread identity so a
                # later fresh replacement is still classified as rehydration.
                self.planner_thread_operation_counts.setdefault(session_identity_sha256, 0)
                continue
            self.planner_operation_count += 1
            planner_thread_call_index = (
                self.planner_thread_operation_counts.get(session_identity_sha256, 0) + 1
            )
            rehydrated_after_thread_rotation = planner_thread_call_index == 1 and bool(
                self.planner_thread_operation_counts
            )
            self.planner_thread_operation_counts[session_identity_sha256] = (
                planner_thread_call_index
            )
            duration_ms = operation.get("duration_ms")
            cold_start = planner_thread_call_index == 1
            retained_concern = (
                not cold_start
                and type(duration_ms) is int
                and duration_ms >= RETAINED_PLANNER_LATENCY_CONCERN_MS
            )
            observations.append(
                {
                    "planner_call_index": self.planner_operation_count,
                    "planner_thread_call_index": planner_thread_call_index,
                    "operation_id": operation["operation_id"],
                    "session_identity_sha256": session_identity_sha256,
                    "duration_ms": duration_ms,
                    "latency_class": (
                        ("cold_rehydration" if rehydrated_after_thread_rotation else "cold_start")
                        if cold_start
                        else (
                            "retained_latency_concern"
                            if retained_concern
                            else "retained_within_target"
                        )
                    ),
                    "cold_start": cold_start,
                    "rehydrated_after_thread_rotation": (rehydrated_after_thread_rotation),
                    "retained_latency_concern": retained_concern,
                    "retained_concern_threshold_ms": (RETAINED_PLANNER_LATENCY_CONCERN_MS),
                }
            )
        return observations

    def finish(self) -> dict[str, Any]:
        required = len(self.fixtures)
        planner_latency = [
            observation
            for result in self.results
            for observation in cast(Sequence[Mapping[str, Any]], result["planner_latency"])
        ]
        cold_start_latency = next(
            (
                observation["duration_ms"]
                for observation in planner_latency
                if observation["cold_start"] is True
            ),
            None,
        )
        retained_latencies = [
            observation["duration_ms"]
            for observation in planner_latency
            if observation["cold_start"] is False and type(observation["duration_ms"]) is int
        ]
        rehydration_latencies = [
            observation["duration_ms"]
            for observation in planner_latency
            if observation["rehydrated_after_thread_rotation"] is True
            and type(observation["duration_ms"]) is int
        ]
        passed = (
            self.failure is None and self.next_index == required and len(self.results) == required
        )
        result_payload = {
            "schema_version": QUALIFICATION_RESULT_SCHEMA,
            "qualification_id": self.parent.manifest["qualification_id"],
            "manifest_sha256": self.parent.manifest["manifest_sha256"],
            "phase": self.phase.value,
            "status": "passed" if passed else "failed",
            "required_fixtures": required,
            "passed_fixtures": sum(value["status"] == "passed" for value in self.results),
            "first_pass_accepted": sum(
                value.get("first_pass_accepted") is True for value in self.results
            ),
            "explicit_regenerate_actions": sum(
                int(value.get("explicit_regenerate_actions", 0)) for value in self.results
            ),
            "transport_retry_actions": self.transport_retry_actions,
            "transport_retry_chains": self.transport_retry_chains,
            "transport_retry_chain_actions": sum(
                int(chain.get("retry_action_count", 0))
                for result in self.results
                for chain in cast(
                    Sequence[Mapping[str, Any]],
                    result.get("transport_retry_chains", ()),
                )
            ),
            "transport_retry_terminal_critical_failures": (
                self.transport_retry_terminal_critical_failures
            ),
            "automatic_transport_retry_actions": 0,
            "automatic_repair_actions": sum(
                int(value.get("automatic_repair_actions", 0)) for value in self.results
            ),
            "sequential_session_id_sha256": text_sha256(self.session_id),
            "retained_conversation_messages": len(self.history),
            "restart_count": self.restart_count,
            "sol_operations": self.sol_operations,
            "sol_submitted_operations": sum(
                int(value.get("sol_http_operations", value.get("sol_operations_observed", 0)))
                for value in self.results
            ),
            "sol_charged_operations": self.sol_operations,
            "deepseek_http_operations": self.deepseek_operations,
            "deepseek_cached_input_tokens": self.deepseek_cached_input_tokens,
            "deepseek_input_tokens": self.deepseek_input_tokens,
            "planner_latency_summary": {
                "cold_start_latency_ms": cold_start_latency,
                "retained_concern_threshold_ms": (RETAINED_PLANNER_LATENCY_CONCERN_MS),
                "cold_rehydration_calls": sum(
                    observation["rehydrated_after_thread_rotation"] is True
                    for observation in planner_latency
                ),
                "maximum_cold_rehydration_latency_ms": (
                    max(rehydration_latencies) if rehydration_latencies else None
                ),
                "retained_planner_calls": sum(
                    observation["cold_start"] is False for observation in planner_latency
                ),
                "retained_latency_concern_count": sum(
                    observation["retained_latency_concern"] is True
                    for observation in planner_latency
                ),
                "maximum_retained_latency_ms": (
                    max(retained_latencies) if retained_latencies else None
                ),
                "average_retained_latency_ms": (
                    round(sum(retained_latencies) / len(retained_latencies))
                    if retained_latencies
                    else None
                ),
            },
            "provider_stage_latency_summary": _provider_stage_latency_summary(
                self.provider_operation_records,
                planner_latency,
            ),
            "http_latency_summary": _latency_summary(
                [
                    cast(int, value["latency_ms"])
                    for value in self.results
                    if type(value.get("latency_ms")) is int
                ],
                failures=sum(value["status"] == "failed" for value in self.results),
            ),
            "non_provider_http_latency_summary": _latency_summary(
                [
                    cast(int, value["non_provider_http_duration_ms"])
                    for value in self.results
                    if type(value.get("non_provider_http_duration_ms")) is int
                ],
                failures=sum(value["status"] == "failed" for value in self.results),
            ),
            "results": self.results,
        }
        result_payload["result_sha256"] = canonical_sha256(result_payload)
        self.parent.evidence.publish_result(self.phase, result_payload)
        if self.failure is not None:
            failure = _closed_failure_projection(self.failure)
            raise StateConflictError(
                "qualification failed at "
                f"{self.results[-1]['fixture_id']}: {failure['failure_category']}"
            ) from None
        if not passed:
            raise StateConflictError("qualification campaign ended before all accepted turns")
        return result_payload


def load_qualification_fixtures(path: Path) -> tuple[QualificationFixtureV1, ...]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractValidationError("qualification fixture file is unreadable") from exc
    if not isinstance(raw, Mapping) or set(raw) != {"schema_version", "fixtures"}:
        raise ContractValidationError("qualification fixture envelope changed")
    if raw["schema_version"] != QUALIFICATION_FIXTURE_SCHEMA:
        raise ContractValidationError("qualification fixture schema changed")
    rows = raw["fixtures"]
    if not isinstance(rows, list):
        raise ContractValidationError("qualification fixtures are not a list")
    fixtures: list[QualificationFixtureV1] = []
    for value in rows:
        if not isinstance(value, Mapping) or set(value) != {
            "fixture_id",
            "phase",
            "initial_route",
            "expected_route",
            "expected_next_route",
            "adult_craft_mode",
            "user_source",
        }:
            raise ContractValidationError("qualification fixture shape changed")
        try:
            fixture = QualificationFixtureV1(
                fixture_id=str(value["fixture_id"]),
                phase=QualificationPhase(str(value["phase"])),
                initial_route=QualificationRoute(str(value["initial_route"])),
                expected_route=QualificationRoute(str(value["expected_route"])),
                expected_next_route=QualificationRoute(str(value["expected_next_route"])),
                adult_craft_mode=str(value["adult_craft_mode"]),
                user_source=str(value["user_source"]),
            )
        except ValueError as exc:
            raise ContractValidationError("qualification fixture enum changed") from exc
        fixtures.append(fixture)
    ids = [value.fixture_id for value in fixtures]
    if len(set(ids)) != len(ids):
        raise ContractValidationError("qualification fixture identity is duplicated")
    for phase in QualificationPhase:
        selected = _ordered_phase_fixtures(phase, fixtures)
        _validate_phase_fixture_counts(phase, selected)
        _validate_phase_route_lineage(selected)
    return tuple(fixtures)


def qualification_request_payload(
    fixture: QualificationFixtureV1,
    *,
    session_id: str,
    messages: Sequence[Mapping[str, str]] | None = None,
) -> dict[str, Any]:
    visible_messages = (
        [{"role": "user", "content": fixture.user_source}]
        if messages is None
        else [dict(value) for value in messages]
    )
    if not visible_messages or visible_messages[-1] != {
        "role": "user",
        "content": fixture.user_source,
    }:
        raise ContractValidationError("qualification conversation floor changed")
    return {
        "model": PI_SCENE_AUTO_MODEL,
        "messages": visible_messages,
        "stream": False,
        "cera_session_id": session_id,
        "cera_profile_id": PI_SCENE_PROFILE,
        "cera_character_autonomy": "both",
        "cera_adult_craft_mode": fixture.adult_craft_mode,
        "cera_prompt_handling": "adjustment",
        "cera_reasoning_effort": QUALIFICATION_PLANNER_REASONING_EFFORT,
        "cera_scene_depth": "auto",
    }


def build_qualification_manifest(
    *,
    repository_root: Path,
    qualification_id: str,
    source_commit: str,
    source_tree: str,
    fixture_path: Path,
    repository_artifacts: Mapping[str, Sequence[Path]],
    external_artifacts: Mapping[str, Sequence[Path]],
) -> dict[str, Any]:
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{7,95}", qualification_id):
        raise ContractValidationError("qualification identity is invalid")
    if not re.fullmatch(r"[0-9a-f]{40}", source_commit) or not re.fullmatch(
        r"[0-9a-f]{40}", source_tree
    ):
        raise ContractValidationError("qualification Git identity is invalid")
    fixtures = load_qualification_fixtures(fixture_path)
    root = repository_root.resolve()
    categories: dict[str, list[dict[str, Any]]] = {}
    for category, paths in repository_artifacts.items():
        categories[category] = _artifact_entries(root, paths, external=False)
    for category, paths in external_artifacts.items():
        if category in categories:
            raise ContractValidationError("qualification artifact category is duplicated")
        categories[category] = _artifact_entries(root, paths, external=True)
    if any(not values for values in categories.values()):
        raise ContractValidationError("qualification artifact category is empty")
    fixture_counts = {
        phase.value: {
            route.value: sum(
                value.phase is phase and value.expected_route is route for value in fixtures
            )
            for route in QualificationRoute
        }
        for phase in QualificationPhase
    }
    body: dict[str, Any] = {
        "schema_version": QUALIFICATION_MANIFEST_SCHEMA,
        "qualification_id": qualification_id,
        "source_commit": source_commit,
        "source_tree": source_tree,
        "route_model": PI_SCENE_AUTO_MODEL,
        "profile_id": PI_SCENE_PROFILE,
        "fixture_set_sha256": bytes_sha256(fixture_path.read_bytes()),
        "fixture_counts": fixture_counts,
        "provider_ceilings": {
            "sol": SOL_FAMILY_CEILING,
            "deepseek_http_operations": DEEPSEEK_HTTP_OPERATION_CEILING,
            "deepseek_per_invocation": DEEPSEEK_PER_INVOCATION_CEILING,
            "terra": TERRA_CEILING,
        },
        "execution_policy": deepcopy(QUALIFICATION_EXECUTION_POLICY),
        "artifact_categories": categories,
    }
    return {**body, "manifest_sha256": canonical_sha256(body)}


def validate_qualification_manifest(manifest: Mapping[str, Any]) -> None:
    required = {
        "schema_version",
        "qualification_id",
        "source_commit",
        "source_tree",
        "route_model",
        "profile_id",
        "fixture_set_sha256",
        "fixture_counts",
        "provider_ceilings",
        "execution_policy",
        "artifact_categories",
        "manifest_sha256",
    }
    if set(manifest) != required:
        raise ContractValidationError("qualification manifest shape changed")
    if manifest["schema_version"] != QUALIFICATION_MANIFEST_SCHEMA:
        raise ContractValidationError("qualification manifest schema changed")
    unsigned = {key: value for key, value in manifest.items() if key != "manifest_sha256"}
    if manifest["manifest_sha256"] != canonical_sha256(unsigned):
        raise StateConflictError("qualification manifest binding changed")
    if manifest["route_model"] != PI_SCENE_AUTO_MODEL:
        raise StateConflictError("qualification route model changed")
    if manifest["profile_id"] != PI_SCENE_PROFILE:
        raise StateConflictError("qualification profile changed")
    expected_counts = {phase: dict(routes) for phase, routes in EXPECTED_PHASE_COUNTS.items()}
    if manifest["fixture_counts"] != expected_counts:
        raise StateConflictError("qualification fixture counts changed")
    if manifest["provider_ceilings"] != {
        "sol": SOL_FAMILY_CEILING,
        "deepseek_http_operations": DEEPSEEK_HTTP_OPERATION_CEILING,
        "deepseek_per_invocation": DEEPSEEK_PER_INVOCATION_CEILING,
        "terra": TERRA_CEILING,
    }:
        raise StateConflictError("qualification provider ceilings changed")
    if manifest["execution_policy"] != QUALIFICATION_EXECUTION_POLICY:
        raise StateConflictError("qualification execution policy changed")


def verify_qualification_artifacts(
    manifest: Mapping[str, Any],
    *,
    repository_root: Path,
) -> None:
    validate_qualification_manifest(manifest)
    root = repository_root.resolve()
    categories = manifest["artifact_categories"]
    if not isinstance(categories, Mapping):
        raise StateConflictError("qualification artifact categories are invalid")
    for raw_entries in categories.values():
        if not isinstance(raw_entries, list) or not raw_entries:
            raise StateConflictError("qualification artifact category is invalid")
        for entry in raw_entries:
            if not isinstance(entry, Mapping) or set(entry) != {
                "path",
                "location",
                "bytes",
                "sha256",
            }:
                raise StateConflictError("qualification artifact entry changed")
            location = entry["location"]
            if location == "repository":
                path = (root / str(entry["path"])).resolve()
                if not path.is_relative_to(root):
                    raise StateConflictError("qualification artifact escaped repository")
            elif location == "external":
                path = Path(str(entry["path"])).resolve()
            else:
                raise StateConflictError("qualification artifact location changed")
            if path.is_symlink() or not path.is_file():
                raise StateConflictError("qualification artifact is unavailable")
            data = path.read_bytes()
            if len(data) != entry["bytes"] or bytes_sha256(data) != entry["sha256"]:
                raise StateConflictError(f"qualification artifact changed: {entry['path']}")


def load_qualification_manifest(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractValidationError("qualification manifest is unreadable") from exc
    if not isinstance(value, dict):
        raise ContractValidationError("qualification manifest is not an object")
    validate_qualification_manifest(value)
    return value


def write_qualification_manifest(path: Path, manifest: Mapping[str, Any]) -> None:
    validate_qualification_manifest(manifest)
    if path.exists():
        raise StateConflictError("qualification manifest already exists")
    _atomic_write_json(path, manifest)


def _validate_accepted_route_projection(
    fixture: QualificationFixtureV1,
    cera: Mapping[str, Any],
) -> None:
    creator_trace = cera.get("creator_trace")
    if not isinstance(creator_trace, Mapping):
        raise StateConflictError("qualification completion omitted its creator trace")
    transition = cera.get("route_transition")
    trace_transition = creator_trace.get("route_transition")
    if fixture.expected_route is QualificationRoute.ORDINARY:
        if (
            creator_trace.get("logic_owner") != "codex_cognition"
            or transition is not None
            or trace_transition is not None
        ):
            raise StateConflictError("ordinary qualification route projection changed")
        current_logic_route = cera.get("current_logic_route")
        if current_logic_route not in (None, QualificationRoute.ORDINARY.value):
            raise StateConflictError("ordinary qualification next route changed")
        return

    if not isinstance(transition, Mapping) or not isinstance(trace_transition, Mapping):
        raise StateConflictError("adult qualification route transition is missing")
    expected_next = fixture.expected_next_route.value
    if (
        transition.get("to_route") != expected_next
        or trace_transition.get("to_route") != expected_next
        or cera.get("current_logic_route") != expected_next
    ):
        raise StateConflictError("adult qualification next route changed")
    expected_return = fixture.expected_next_route is QualificationRoute.ORDINARY
    if (
        cera.get("return_to_codex") is not expected_return
        or trace_transition.get("return_to_codex") is not expected_return
    ):
        raise StateConflictError("adult qualification return-route binding changed")


def _validate_rejected_route_projection(
    fixture: QualificationFixtureV1,
    cera: Mapping[str, Any],
) -> None:
    creator_trace = cera.get("creator_trace")
    if not isinstance(creator_trace, Mapping):
        raise StateConflictError("rejected qualification omitted its creator trace")
    transition = cera.get("route_transition")
    trace_transition = creator_trace.get("route_transition")
    if fixture.expected_route is QualificationRoute.ORDINARY:
        if transition is not None or trace_transition is not None:
            raise StateConflictError("ordinary rejection changed route ownership")
        current_logic_route = cera.get("current_logic_route")
        if current_logic_route not in (None, fixture.initial_route.value):
            raise StateConflictError("ordinary rejection changed accepted route")
        return

    if not isinstance(transition, Mapping) or not isinstance(trace_transition, Mapping):
        raise StateConflictError("adult rejection route transition is missing")
    if (
        transition.get("to_route") != fixture.expected_next_route.value
        or trace_transition.get("to_route") != fixture.expected_next_route.value
        or cera.get("current_logic_route") != fixture.initial_route.value
    ):
        raise StateConflictError("adult rejection route custody changed")


def _adult_repair_count(cera: Mapping[str, Any]) -> int:
    attempts = cera.get("repair_attempts")
    if not isinstance(attempts, list) or len(attempts) > 1:
        raise StateConflictError("adult qualification repair trace changed")
    expected_fields = {
        "public_review_id",
        "conflict_class",
        "operation_sha256",
        "outcome_sha256",
        "planner_provider_operations",
        "adult_scene_provider_operations",
        "adult_filter_provider_operations",
    }
    critical_classes = {
        "logic_contradiction",
        "current_data_conflict",
        "knowledge_or_privacy_conflict",
        "unsupported_durable_effect",
        "route_transition_conflict",
    }
    for attempt in attempts:
        if not isinstance(attempt, Mapping) or set(attempt) != expected_fields:
            raise StateConflictError("adult qualification repair trace shape changed")
        if (
            re.fullmatch(r"review-[a-f0-9]{28}", str(attempt["public_review_id"])) is None
            or attempt["conflict_class"] not in critical_classes
            or not re_is_sha256(str(attempt["operation_sha256"]))
            or not re_is_sha256(str(attempt["outcome_sha256"]))
        ):
            raise StateConflictError("adult qualification repair trace is invalid")
        for field_name in (
            "planner_provider_operations",
            "adult_scene_provider_operations",
            "adult_filter_provider_operations",
        ):
            value = attempt[field_name]
            if type(value) is not int or value < 0:
                raise StateConflictError("adult qualification repair operation count is invalid")
    return len(attempts)


def _ordinary_attempt_trace(
    cera: Mapping[str, Any],
    *,
    operations: Mapping[str, int],
    regenerated: bool,
    accepted: bool,
) -> tuple[bool, int]:
    attempts = cera.get("provider_attempts")
    if not isinstance(attempts, list) or not 1 <= len(attempts) <= 2:
        raise StateConflictError("ordinary qualification attempt trace changed")
    if accepted:
        expected_dispositions = (
            ("semantic_pass",)
            if len(attempts) == 1
            else (
                "semantic_rejected",
                "semantic_pass",
            )
        )
    else:
        expected_dispositions = ("semantic_rejected",)
    if len(attempts) != len(expected_dispositions):
        raise StateConflictError("ordinary qualification attempt disposition changed")
    candidate_ids: list[str] = []
    totals = {"planner": 0, "writer": 0, "validator": 0}
    for index, (attempt, disposition) in enumerate(
        zip(attempts, expected_dispositions, strict=True),
        start=1,
    ):
        if not isinstance(attempt, Mapping) or set(attempt) != {
            "attempt_number",
            "candidate_id",
            "disposition",
            "provider_operations",
        }:
            raise StateConflictError("ordinary qualification attempt shape changed")
        candidate_id = attempt["candidate_id"]
        attempt_operations = attempt["provider_operations"]
        if (
            attempt["attempt_number"] != index
            or attempt["disposition"] != disposition
            or not isinstance(candidate_id, str)
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{2,239}", candidate_id) is None
            or not isinstance(attempt_operations, Mapping)
            or set(attempt_operations) != {"planner", "writer", "validator"}
        ):
            raise StateConflictError("ordinary qualification attempt binding changed")
        expected_planner = 0 if regenerated or index > 1 else 1
        if (
            attempt_operations["planner"] != expected_planner
            or type(attempt_operations["writer"]) is not int
            or attempt_operations["writer"] < 1
            or attempt_operations["validator"] != 1
        ):
            raise StateConflictError("ordinary qualification attempt accounting changed")
        candidate_ids.append(candidate_id)
        for key in totals:
            totals[key] += int(attempt_operations[key])
    if len(candidate_ids) != len(set(candidate_ids)):
        raise StateConflictError("ordinary qualification repair reused its candidate")
    if any(operations[key] != value for key, value in totals.items()):
        raise StateConflictError("ordinary qualification attempt totals changed")
    return expected_dispositions[0] == "semantic_pass", len(attempts) - 1


def _validate_completion_response(
    fixture: QualificationFixtureV1,
    response: ClientResponseV1,
    *,
    regenerated: bool = False,
) -> dict[str, Any]:
    if response.status_code != 200:
        raise StateConflictError(f"qualification API returned HTTP {response.status_code}")
    body = response.body
    if body.get("error") is not None or body.get("status") == "error":
        raise StateConflictError("qualification API returned a typed error")
    choices = body.get("choices")
    if not isinstance(choices, list) or len(choices) != 1:
        raise StateConflictError("qualification completion choices changed")
    choice = choices[0]
    if not isinstance(choice, Mapping) or choice.get("finish_reason") != "stop":
        raise StateConflictError("qualification completion did not stop normally")
    message = choice.get("message")
    if not isinstance(message, Mapping) or message.get("role") != "assistant":
        raise StateConflictError("qualification completion message changed")
    prose = message.get("content")
    if not isinstance(prose, str) or len(prose.strip()) < 40:
        raise StateConflictError("qualification completion prose is unusable")
    lowered = prose.casefold()
    if any(
        marker in lowered
        for marker in (
            "i can't help with that",
            "i cannot help with that",
            "unable to comply",
        )
    ):
        raise StateConflictError("qualification completion is a refusal")
    cera = body.get("cera")
    if not isinstance(cera, Mapping):
        raise StateConflictError("qualification completion omitted CERA metadata")
    if cera.get("profile_id") != PI_SCENE_PROFILE:
        raise StateConflictError("qualification completion profile changed")
    if cera.get("route_mode") != fixture.expected_route.value:
        raise StateConflictError("qualification completion used the wrong logic route")
    _validate_accepted_route_projection(fixture, cera)
    if (
        cera.get("status") != "accepted"
        or cera.get("provisional") is not False
        or cera.get("story_state_committed") is not True
        or cera.get("canon_status") != "accepted"
    ):
        raise StateConflictError("qualification completion was not atomically accepted")
    if cera.get("operational_warnings") not in (None, []):
        raise StateConflictError("qualification completion has an operational warning")
    accepted_turn_id = cera.get("accepted_turn_id")
    accepted_receipt_sha256 = cera.get("accepted_receipt_sha256")
    if not isinstance(accepted_turn_id, str) or not accepted_turn_id.strip():
        raise StateConflictError("qualification accepted turn identity is missing")
    if not isinstance(accepted_receipt_sha256, str) or not re_is_sha256(accepted_receipt_sha256):
        raise StateConflictError("qualification accepted receipt binding is missing")
    raw_operations = cera.get("provider_operations")
    if not isinstance(raw_operations, Mapping):
        raise StateConflictError("qualification provider operation projection is missing")
    operations = {str(key): value for key, value in raw_operations.items()}
    if any(type(value) is not int or value < 0 for value in operations.values()):
        raise StateConflictError("qualification provider operation count is invalid")
    if fixture.expected_route is QualificationRoute.ORDINARY:
        semantic = cera.get("semantic_validation")
        if not isinstance(semantic, Mapping) or semantic.get("verdict") != "pass":
            raise StateConflictError("ordinary qualification did not pass Luna validation")
        if cera.get("recording_status") != "complete":
            raise StateConflictError("ordinary qualification did not record on first invocation")
        required = {"planner", "writer", "validator", "recorder"}
        if (
            set(operations) != required
            or operations["writer"] < 1
            or operations["validator"] < 1
            or operations["recorder"] < 1
        ):
            raise StateConflictError("ordinary qualification operation projection changed")
        first_pass_accepted, automatic_repair_actions = _ordinary_attempt_trace(
            cera,
            operations=operations,
            regenerated=regenerated,
            accepted=True,
        )
    else:
        adult_filter = cera.get("adult_filter")
        if not isinstance(adult_filter, Mapping) or adult_filter.get("verdict") != "pass":
            raise StateConflictError("adult qualification did not pass its Filter")
        if (
            cera.get("recorder_required") is not False
            or cera.get("recording_status") != "complete_preaccept_filter"
        ):
            raise StateConflictError("adult qualification retained a Recorder dependency")
        required = {"planner", "adult_scene", "adult_filter", "recorder"}
        if set(operations) != required:
            raise StateConflictError("adult qualification operation projection changed")
        expected_planner = (
            0 if regenerated or fixture.initial_route is QualificationRoute.ADULT else 1
        )
        if (
            operations["planner"] != expected_planner
            or operations["adult_scene"] < 1
            or operations["adult_filter"] < 1
            or operations["recorder"] != 0
        ):
            raise StateConflictError("adult qualification operation count changed")
        for field_name in ("protected_full_record_sha256", "codex_projection_sha256"):
            value = cera.get(field_name)
            if not isinstance(value, str) or not re_is_sha256(value):
                raise StateConflictError("adult qualification custody binding is missing")
        operation_sha256 = cera.get("operation_sha256")
        candidate_id = cera.get("candidate_id")
        if not isinstance(operation_sha256, str) or not re_is_sha256(operation_sha256):
            raise StateConflictError("adult qualification operation binding is missing")
        if not isinstance(candidate_id, str) or not re.fullmatch(
            r"candidate:adult:(?:[a-f0-9]{32})|candidate:adult-regenerate:[a-f0-9]{24,32}",
            candidate_id,
        ):
            raise StateConflictError("adult qualification candidate binding is missing")
        creator_trace = cera.get("creator_trace")
        recording = creator_trace.get("recording") if isinstance(creator_trace, Mapping) else None
        if not isinstance(recording, Mapping) or dict(recording) != {
            "status": "complete_preaccept_filter",
            "recorder_required": False,
            "projection_status": "complete",
            "protected_record_status": "complete",
        }:
            raise StateConflictError("adult qualification promotion custody is incomplete")
        automatic_repair_actions = _adult_repair_count(cera)
        first_pass_accepted = automatic_repair_actions == 0
    return {
        "visible_prose_sha256": text_sha256(prose),
        "accepted_turn_id": accepted_turn_id,
        "accepted_receipt_sha256": accepted_receipt_sha256,
        "observed_route": fixture.expected_route.value,
        "observed_next_route": fixture.expected_next_route.value,
        "first_pass_accepted": first_pass_accepted,
        "automatic_repair_actions": automatic_repair_actions,
        "provider_operations": operations,
    }


def _classify_completion_response(
    fixture: QualificationFixtureV1,
    response: ClientResponseV1,
) -> Mapping[str, Any] | RejectionReviewV1:
    try:
        return _validate_completion_response(fixture, response)
    except StateConflictError as exc:
        body = response.body
        cera = body.get("cera") if isinstance(body, Mapping) else None
        if not isinstance(cera, Mapping) or cera.get("status") != "validation_rejected":
            raise
        if cera.get("route_mode") != fixture.expected_route.value:
            raise
        _validate_rejected_route_projection(fixture, cera)
        if cera.get("story_state_committed") is not False:
            raise StateConflictError("rejected qualification changed accepted state") from exc
        if cera.get("regenerate_enabled") is not True:
            raise StateConflictError("rejected qualification cannot be regenerated") from exc
        ordinary_review_id = cera.get("provisional_review_id")
        adult_review_id = cera.get("review_id")
        if fixture.expected_route is QualificationRoute.ORDINARY:
            if adult_review_id is not None:
                raise StateConflictError(
                    "ordinary rejection exposed an adult review identity"
                ) from exc
            review_id = ordinary_review_id
        else:
            if ordinary_review_id != adult_review_id:
                raise StateConflictError("adult rejection review aliases disagree") from exc
            review_id = adult_review_id
        if not isinstance(review_id, str) or not re.fullmatch(
            r"(?:[a-z][a-z0-9_]{0,31}:[A-Za-z0-9._-]{1,160}|review-[a-f0-9]{28})",
            review_id,
        ):
            raise StateConflictError("rejected qualification review identity is invalid") from exc
        conflict = _rejection_conflict(cera, fixture.expected_route)
        conflict_class = conflict.get("conflict_class")
        allowed_classes = (
            {"omitted_decision", "severe_incompleteness"}
            if fixture.expected_route is QualificationRoute.ORDINARY
            else {"logic_not_realized", "severe_incompleteness"}
        )
        if conflict_class not in allowed_classes:
            raise StateConflictError(
                "qualification rejection is not an isolated quality miss"
            ) from exc
        encoded = json.dumps(conflict, sort_keys=True, separators=(",", ":")).casefold()
        forbidden = (
            "critical",
            "custody",
            "schema",
            "route",
            "structural",
            "identity",
            "transaction",
            "api_error",
        )
        if any(token in encoded for token in forbidden):
            raise StateConflictError(
                "critical or structural qualification rejection cannot regenerate"
            ) from exc
        raw_operations = cera.get("provider_operations")
        if not isinstance(raw_operations, Mapping):
            raise StateConflictError("rejected qualification omitted provider accounting") from exc
        operations = {str(key): value for key, value in raw_operations.items()}
        if any(type(value) is not int or value < 0 for value in operations.values()):
            raise StateConflictError(
                "rejected qualification provider accounting is invalid"
            ) from exc
        if fixture.expected_route is QualificationRoute.ORDINARY:
            if (
                set(operations) != {"planner", "writer", "validator", "recorder"}
                or operations["writer"] < 1
                or operations["validator"] != 1
                or operations["recorder"] != 0
            ):
                raise StateConflictError(
                    "ordinary rejection is not one isolated first-pass miss"
                ) from exc
            _ordinary_attempt_trace(
                cera,
                operations=operations,
                regenerated=False,
                accepted=False,
            )
        else:
            expected_planner = 1 if fixture.initial_route is QualificationRoute.ORDINARY else 0
            if (
                set(operations) != {"planner", "adult_scene", "adult_filter", "recorder"}
                or operations["planner"] != expected_planner
                or operations["adult_scene"] < 1
                or operations["adult_filter"] < 1
                or operations["recorder"] != 0
            ):
                raise StateConflictError(
                    "adult rejection is not one isolated first-pass miss"
                ) from exc
            if cera.get("repair_attempts") != []:
                raise StateConflictError(
                    "adult rejection already consumed its automatic repair"
                ) from exc
        projection = {
            "provider_operations": operations,
            "accepted_turn_id": None,
            "accepted_receipt_sha256": None,
            "visible_prose_sha256": text_sha256(_visible_prose(body)),
            "observed_route": fixture.expected_route.value,
            "observed_next_route": fixture.expected_next_route.value,
            "first_pass_accepted": False,
            "automatic_repair_actions": 0,
        }
        return RejectionReviewV1(
            review_id=review_id,
            conflict_sha256=canonical_sha256(conflict),
            provider_operations=cast(Mapping[str, int], operations),
            projection=projection,
        )


def _rejection_conflict(
    cera: Mapping[str, Any],
    route: QualificationRoute,
) -> Mapping[str, Any]:
    if route is QualificationRoute.ORDINARY:
        validation = cera.get("semantic_validation")
        conflict = validation.get("conflict") if isinstance(validation, Mapping) else None
    else:
        adult_filter = cera.get("adult_filter")
        conflict = adult_filter.get("conflict") if isinstance(adult_filter, Mapping) else None
    if not isinstance(conflict, Mapping) or not conflict:
        raise StateConflictError("qualification rejection omitted typed conflict evidence")
    return conflict


def _successor_completion(response: ClientResponseV1) -> ClientResponseV1:
    if "choices" in response.body:
        return response
    successor = response.body.get("successor")
    if not isinstance(successor, Mapping):
        raise StateConflictError("Regenerate did not return one successor completion")
    return ClientResponseV1(
        transport=response.transport,
        path=response.path,
        status_code=response.status_code,
        duration_ms=response.duration_ms,
        body=successor,
    )


def _visible_prose(body: Mapping[str, Any]) -> str:
    choices = body.get("choices")
    if not isinstance(choices, list) or len(choices) != 1:
        raise StateConflictError("qualification response omitted its visible prose")
    choice = choices[0]
    message = choice.get("message") if isinstance(choice, Mapping) else None
    prose = message.get("content") if isinstance(message, Mapping) else None
    if not isinstance(prose, str) or not prose.strip():
        raise StateConflictError("qualification response visible prose is invalid")
    return prose


def _closed_transport_retry_failure(
    response: ClientResponseV1,
) -> TransportRetryFailureV1 | None:
    """Recognize only the two closed direct/relay Planner-failure envelopes."""

    body = response.body
    if (
        response.status_code != 500
        or set(body) != {"status", "story_state_committed", "error"}
        or body.get("status") != "error"
        or body.get("story_state_committed") is not False
    ):
        return None
    error = body.get("error")
    if not isinstance(error, Mapping):
        return None
    common_keys = {
        "schema_version",
        "error_code",
        "message",
        "request_id",
        "story_state_committed",
        "retry_mode",
        "provider_operation_submitted",
        "accepted_state_changed",
        "fallback_used",
        "next_action",
        "retry_transport_enabled",
        "transport_retry",
    }
    direct_only = {
        "trace_id",
        "branch_id",
        "generation_id",
        "stage",
        "details",
        "debug_log_path",
    }
    if frozenset(error) not in {
        frozenset(common_keys),
        frozenset(common_keys | direct_only),
    }:
        return None
    request_id = error.get("request_id")
    submitted = error.get("provider_operation_submitted")
    if (
        error.get("schema_version") != "cera.error.v1"
        or error.get("error_code") != "CERA_PROVIDER_TRANSPORT_FAILED"
        or error.get("message")
        not in {
            "A provider transport failed with no candidate or story-state effect.",
            "CERA provider transport failed before any candidate or accepted effect.",
        }
        or not isinstance(request_id, str)
        or re.fullmatch(r"request-[a-f0-9]{64}", request_id) is None
        or error.get("story_state_committed") is not False
        or error.get("retry_mode") != "manual_transport"
        or type(submitted) is not bool
        or error.get("accepted_state_changed") is not False
        or error.get("fallback_used") is not False
        or error.get("next_action") != "use_transport_retry"
        or error.get("retry_transport_enabled") is not True
    ):
        return None
    if direct_only.issubset(error):
        debug_log_path = error.get("debug_log_path")
        if (
            not isinstance(error.get("trace_id"), str)
            or re.fullmatch(r"trace:[a-f0-9]{32}", cast(str, error["trace_id"])) is None
            or error.get("branch_id") is not None
            or error.get("generation_id") is not None
            or error.get("stage") != "pi_scene_http"
            or error.get("details") != []
            or debug_log_path is not None
            and (
                not isinstance(debug_log_path, str)
                or len(debug_log_path) > 2_000
                or re.search(r"[\x00-\x1f\x7f]", debug_log_path) is not None
            )
        ):
            return None
    action = _validate_transport_retry_action(error.get("transport_retry"))
    if action is None:
        return None
    return TransportRetryFailureV1(
        request_id=request_id,
        retry_id=cast(str, action["retry_id"]),
        effect_proof_sha256=cast(str, action["effect_proof_sha256"]),
        provider_operation_submitted=submitted,
        response_sha256=canonical_sha256(body),
    )


def _validate_transport_retry_action(value: object) -> Mapping[str, Any] | None:
    if not isinstance(value, Mapping) or set(value) != {
        "schema_version",
        "retry_id",
        "retry_url",
        "method",
        "eligible",
        "automatic",
        "effect_proof_sha256",
    }:
        return None
    retry_id = value.get("retry_id")
    effect_sha256 = value.get("effect_proof_sha256")
    if (
        value.get("schema_version") != "cera.pi_scene.transport_retry.v1"
        or not isinstance(retry_id, str)
        or re.fullmatch(r"retry-[a-f0-9]{64}", retry_id) is None
        or value.get("retry_url") != f"/v1/cera/transport-retries/{retry_id}"
        or value.get("method") != "POST"
        or value.get("eligible") is not True
        or value.get("automatic") is not False
        or not isinstance(effect_sha256, str)
        or not re_is_sha256(effect_sha256)
    ):
        return None
    return value


def _validate_transport_retry_status(
    response: ClientResponseV1,
    *,
    failure: TransportRetryFailureV1,
) -> Mapping[str, Any]:
    if response.status_code != 200:
        raise StateConflictError(
            f"qualification transport Retry status returned HTTP {response.status_code}"
        )
    value = response.body
    common = {
        "schema_version",
        "retry_id",
        "request_id",
        "state",
        "effect_proof_sha256",
        "retry_transport_enabled",
    }
    state = value.get("state")
    expected_schema = (
        "cera.pi_scene.transport_retry_status.v2"
        if state == "attempts_exhausted"
        else "cera.pi_scene.transport_retry_status.v1"
    )
    if (
        value.get("schema_version") != expected_schema
        or value.get("retry_id") != failure.retry_id
        or value.get("request_id") != failure.request_id
        or value.get("effect_proof_sha256") != failure.effect_proof_sha256
        or type(value.get("retry_transport_enabled")) is not bool
    ):
        raise StateConflictError("qualification transport Retry status identity changed")
    if state == "eligible":
        action = _validate_transport_retry_action(value.get("transport_retry"))
        if (
            set(value) != common | {"transport_retry"}
            or value.get("retry_transport_enabled") is not True
            or action is None
            or action.get("retry_id") != failure.retry_id
            or action.get("effect_proof_sha256") != failure.effect_proof_sha256
        ):
            raise StateConflictError("qualification eligible Retry status changed")
    elif state == "in_progress":
        if (
            set(value) != common | {"phase"}
            or value.get("retry_transport_enabled") is not False
            or value.get("phase") not in {"authorized", "owner_rotated", "dispatch_started"}
        ):
            raise StateConflictError("qualification in-progress Retry status changed")
    elif state == "succeeded":
        completion = value.get("completion")
        completion_sha256 = value.get("completion_sha256")
        cera = completion.get("cera") if isinstance(completion, Mapping) else None
        if (
            set(value) != common | {"completion", "completion_sha256"}
            or value.get("retry_transport_enabled") is not False
            or not isinstance(completion, Mapping)
            or not isinstance(cera, Mapping)
            or cera.get("request_id") != failure.request_id
            or not isinstance(completion_sha256, str)
            or not re_is_sha256(completion_sha256)
            or canonical_sha256(completion) != completion_sha256
        ):
            raise StateConflictError("qualification succeeded Retry status changed")
    elif state == "superseded":
        successor = value.get("superseded_by_retry_id")
        action = _validate_transport_retry_action(value.get("transport_retry"))
        if (
            set(value) != common | {"superseded_by_retry_id", "transport_retry"}
            or value.get("retry_transport_enabled") is not True
            or not isinstance(successor, str)
            or re.fullmatch(r"retry-[a-f0-9]{64}", successor) is None
            or successor == failure.retry_id
            or action is None
            or action.get("retry_id") != successor
            or action.get("effect_proof_sha256") == failure.effect_proof_sha256
        ):
            raise StateConflictError("qualification superseded Retry status changed")
    elif state == "attempts_exhausted":
        critical = _validate_critical_provider_stage_failure(
            value.get("critical_provider_stage_failure")
        )
        if (
            set(value) != common | {"critical_provider_stage_failure"}
            or value.get("retry_transport_enabled") is not False
            or critical is None
        ):
            raise StateConflictError("qualification exhausted Retry status changed")
    elif state == "blocked":
        if (
            set(value) != common | {"blocked_reason_code"}
            or value.get("retry_transport_enabled") is not False
            or value.get("blocked_reason_code")
            not in {
                "effect_state_changed",
                "route_or_context_changed",
                "provider_ledger_changed",
                "owner_rotation_failed",
                "dispatch_state_ambiguous",
                "durable_request_progressed",
            }
        ):
            raise StateConflictError("qualification blocked Retry status changed")
    else:
        raise StateConflictError("qualification transport Retry status state changed")
    return value


def _validate_critical_provider_stage_failure(
    value: object,
) -> Mapping[str, Any] | None:
    if not isinstance(value, Mapping) or set(value) != {
        "schema_version",
        "severity",
        "provider",
        "model_family",
        "stage",
        "maximum_attempts",
        "attempts_total",
        "retries_consumed",
        "story_state_committed",
        "failed_stage_effect_committed",
        "provider_operations_observed_total",
        "provider_operations_conservative_total",
        "final_failure_class",
        "request_sha256",
        "stage_input_sha256",
        "attempt_chain_sha256",
        "terminal_evidence_sha256",
    }:
        return None
    observed = value.get("provider_operations_observed_total")
    conservative = value.get("provider_operations_conservative_total")
    hashes = (
        value.get("request_sha256"),
        value.get("stage_input_sha256"),
        value.get("attempt_chain_sha256"),
        value.get("terminal_evidence_sha256"),
    )
    if (
        value.get("schema_version") != "cera.provider_stage_retry_exhausted.v1"
        or value.get("severity") != "critical"
        or value.get("provider") != "codex"
        or value.get("model_family") != "sol"
        or value.get("stage") != "planner"
        or value.get("maximum_attempts") != 3
        or value.get("attempts_total") != 3
        or value.get("retries_consumed") != 2
        or value.get("story_state_committed") is not False
        or value.get("failed_stage_effect_committed") is not False
        or type(observed) is not int
        or not 0 <= observed <= 3
        or type(conservative) is not int
        or not observed <= conservative <= 3
        or value.get("final_failure_class") not in FINAL_PROVIDER_FAILURE_CLASSES
        or any(not isinstance(item, str) or not re_is_sha256(item) for item in hashes)
    ):
        return None
    return value


def _poll_terminal_transport_retry_status(
    client: QualificationClient,
    *,
    failure: TransportRetryFailureV1,
) -> tuple[ClientResponseV1, Mapping[str, Any], int]:
    started_ns = time.perf_counter_ns()
    deadline = time.monotonic() + TRANSPORT_RETRY_STATUS_TIMEOUT_SECONDS
    while True:
        response = client.transport_retry_status(retry_id=failure.retry_id)
        status = _validate_transport_retry_status(response, failure=failure)
        if status["state"] != "in_progress":
            duration_ms = max(0, (time.perf_counter_ns() - started_ns) // 1_000_000)
            return response, status, duration_ms
        if time.monotonic() >= deadline:
            raise StateConflictError("qualification transport Retry status remained in progress")
        time.sleep(TRANSPORT_RETRY_STATUS_POLL_SECONDS)


def _transport_retry_action_receipt(
    failure: TransportRetryFailureV1,
    *,
    action_index: int,
    pre_status_response: ClientResponseV1,
    post_response: ClientResponseV1 | None,
    post_failure: Mapping[str, str] | None,
    post_dispatch_duration_ms: int,
    status_response: ClientResponseV1,
    status: Mapping[str, Any],
    status_reconciliation_duration_ms: int,
) -> dict[str, Any]:
    state = status.get("state")
    body: dict[str, Any] = {
        "request_id_sha256": failure.request_id_sha256,
        "retry_id_sha256": failure.retry_id_sha256,
        "effect_proof_sha256": failure.effect_proof_sha256,
        "retry_action_index": action_index,
        "retry_action_count": action_index,
        "maximum_retry_actions": 2,
        "request_body_sha256": canonical_sha256({}),
        "post_response_observed": post_response is not None,
        "post_response_sha256": (
            None if post_response is None else canonical_sha256(post_response.body)
        ),
        "post_failure": None if post_failure is None else dict(post_failure),
        "post_dispatch_duration_ms": post_dispatch_duration_ms,
        "status_reconciliation_duration_ms": status_reconciliation_duration_ms,
        "pre_status_sha256": canonical_sha256(pre_status_response.body),
        "post_status_state": state,
        "post_status_sha256": canonical_sha256(status_response.body),
    }
    if state == "succeeded":
        body["completion_sha256"] = status["completion_sha256"]
    elif state == "superseded":
        action = cast(Mapping[str, Any], status["transport_retry"])
        body["successor_retry_id_sha256"] = text_sha256(cast(str, action["retry_id"]))
        body["successor_effect_proof_sha256"] = action["effect_proof_sha256"]
    elif state == "attempts_exhausted":
        body["critical_failure_sha256"] = canonical_sha256(
            cast(Mapping[str, Any], status["critical_provider_stage_failure"])
        )
    return {**body, "action_sha256": canonical_sha256(body)}


def _validate_attempts_exhausted_post(
    response: ClientResponseV1 | None,
    *,
    failure: TransportRetryFailureV1,
    critical: Mapping[str, Any],
) -> bool | None:
    """Treat the POST as an observation; the authenticated GET is authority."""

    if response is None:
        return None
    body = response.body
    error = body.get("error") if isinstance(body, Mapping) else None
    outer_keys = {"status", "story_state_committed", "error"}
    common_error_keys = {
        "schema_version",
        "error_code",
        "message",
        "story_state_committed",
        "retry_mode",
        "provider_operation_submitted",
        "accepted_state_changed",
        "fallback_used",
        "next_action",
        "retry_transport_enabled",
        "critical_provider_stage_failure",
    }
    direct_only_keys = {
        "trace_id",
        "request_id",
        "branch_id",
        "generation_id",
        "stage",
        "details",
        "debug_log_path",
    }
    error_keys = set(error) if isinstance(error, Mapping) else set()
    if (
        response.status_code != 503
        or set(body) != outer_keys
        or body.get("status") != "error"
        or body.get("story_state_committed") is not False
        or not isinstance(error, Mapping)
        or error_keys != common_error_keys
        and error_keys != common_error_keys | direct_only_keys
        or error.get("schema_version") != "cera.error.v1"
        or error.get("error_code") != "CERA_PROVIDER_STAGE_RETRY_EXHAUSTED"
        or not isinstance(error.get("message"), str)
        or not 1 <= len(cast(str, error["message"])) <= 500
        or error.get("story_state_committed") is not False
        or error.get("retry_mode") != "exhausted"
        or type(error.get("provider_operation_submitted")) is not bool
        or error.get("accepted_state_changed") is not False
        or error.get("fallback_used") is not False
        or error.get("next_action") != "report_critical_provider_failure"
        or error.get("retry_transport_enabled") is not False
        or error.get("critical_provider_stage_failure") != critical
    ):
        raise StateConflictError("qualification Retry POST contradicted authenticated terminal GET")
    if error_keys == common_error_keys:
        if error.get("message") != (
            "CERA stopped after three failed attempts at one provider stage."
        ):
            raise StateConflictError("qualification Retry relay terminal projection changed")
    else:
        trace_id = error.get("trace_id")
        details = error.get("details")
        debug_log_path = error.get("debug_log_path")
        if (
            not isinstance(trace_id, str)
            or re.fullmatch(r"trace:[a-f0-9]{32}", trace_id) is None
            or error.get("request_id") != failure.request_id
            or error.get("branch_id") is not None
            or error.get("generation_id") is not None
            or error.get("stage") != "pi_scene_http"
            or not isinstance(details, list)
            or any(not isinstance(item, str) or len(item) > 500 for item in details)
            or debug_log_path is not None
            and (
                not isinstance(debug_log_path, str)
                or len(debug_log_path) > 2_000
                or re.search(r"[\x00-\x1f\x7f]", debug_log_path) is not None
            )
        ):
            raise StateConflictError("qualification Retry direct terminal projection changed")
    return cast(bool, error["provider_operation_submitted"])


def _superseding_transport_failure(
    prior: TransportRetryFailureV1,
    *,
    status: Mapping[str, Any],
    post_response: ClientResponseV1 | None,
) -> TransportRetryFailureV1:
    if status.get("state") != "superseded":
        raise StateConflictError("qualification Retry did not provide a successor failure")
    action = _validate_transport_retry_action(status.get("transport_retry"))
    if action is None:
        raise StateConflictError("qualification Retry successor action changed")
    successor = TransportRetryFailureV1(
        request_id=prior.request_id,
        retry_id=cast(str, action["retry_id"]),
        effect_proof_sha256=cast(str, action["effect_proof_sha256"]),
        provider_operation_submitted=None,
        response_sha256=None,
    )
    if post_response is None:
        return successor
    if post_response.status_code == 200:
        raise StateConflictError("qualification successful Retry POST was superseded")
    if post_response.status_code != 500:
        return successor
    observed = _closed_transport_retry_failure(post_response)
    if (
        observed is None
        or observed.request_id != successor.request_id
        or observed.retry_id != successor.retry_id
        or observed.effect_proof_sha256 != successor.effect_proof_sha256
    ):
        raise StateConflictError("qualification Retry POST successor failure changed")
    return observed


def _critical_provider_stage_failure(
    *,
    backend: Mapping[str, Any],
    failures: Sequence[TransportRetryFailureV1],
    failed_calls: Sequence[Mapping[str, Any]],
    actions: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    validated = _validate_critical_provider_stage_failure(backend)
    if validated is None or len(failures) != 2 or len(failed_calls) != 3 or len(actions) != 2:
        raise StateConflictError("qualification critical Retry attempt count changed")
    if len({value.request_id for value in failures}) != 1:
        raise StateConflictError("qualification critical Retry request identity changed")
    if [value.get("post_status_state") for value in actions] != [
        "superseded",
        "attempts_exhausted",
    ]:
        raise StateConflictError("qualification critical Retry terminal chain changed")
    return dict(validated)


def _closed_exhausted_planner_call(
    critical: Mapping[str, Any],
    delta: ProviderLedgerDeltaV1,
    *,
    prior_failed_calls: Sequence[Mapping[str, Any]],
    post_provider_operation_submitted: bool | None,
) -> dict[str, Any]:
    if _validate_critical_provider_stage_failure(critical) is None:
        raise StateConflictError("qualification critical provider-stage projection changed")
    if delta.deepseek_started_operations != 0:
        raise StateConflictError("qualification exhausted Retry dispatched DeepSeek")
    calls = [
        value
        for value in _provider_operation_records(delta)
        if value.get("provider_family") == "sol"
    ]
    if len(calls) != 1:
        raise StateConflictError("qualification exhausted Retry ledger span changed")
    call = calls[0]
    terminal_state = call.get("terminal_state")
    if (
        call.get("owner") != "planner"
        or terminal_state not in {"provider_failed", "pretransport_failed"}
        or type(call.get("submitted")) is not bool
        or type(call.get("charged")) is not bool
        or call.get("charged") is not call.get("submitted")
        or post_provider_operation_submitted is not None
        and call.get("submitted") is not post_provider_operation_submitted
        or not isinstance(call.get("operation_id"), str)
        or not re_is_sha256(str(call.get("session_identity_sha256", "")))
        or not re_is_sha256(str(call.get("call_events_sha256", "")))
        or (
            terminal_state == "pretransport_failed"
            and critical.get("final_failure_class") != "provider_unavailable"
        )
    ):
        raise StateConflictError("qualification exhausted Retry is not a Planner failure")
    projected = _safe_sol_call_projection(call)
    all_calls = [*prior_failed_calls, projected]
    if critical.get("provider_operations_observed_total") != sum(
        value.get("submitted") is True for value in all_calls
    ) or critical.get("provider_operations_conservative_total") != sum(
        value.get("charged") is True for value in all_calls
    ):
        raise StateConflictError("qualification exhausted Retry accounting changed")
    return projected


def _closed_failed_planner_call(
    failure: TransportRetryFailureV1,
    delta: ProviderLedgerDeltaV1,
) -> dict[str, Any]:
    if delta.deepseek_started_operations != 0:
        raise StateConflictError("qualification Retry failure occurred after DeepSeek dispatch")
    sol_calls = [
        value
        for value in _provider_operation_records(delta)
        if value.get("provider_family") == "sol"
    ]
    if len(sol_calls) != 1:
        raise StateConflictError("qualification Retry failure ledger span changed")
    call = sol_calls[0]
    submitted = call.get("submitted")
    charged = call.get("charged")
    thread_sha256 = call.get("session_identity_sha256")
    terminal_state = call.get("terminal_state")
    expected_terminal_state = (
        None
        if failure.provider_operation_submitted is None
        else ("provider_failed" if failure.provider_operation_submitted else "pretransport_failed")
    )
    if (
        call.get("owner") != "planner"
        or (
            failure.provider_operation_submitted is not None
            and submitted is not failure.provider_operation_submitted
        )
        or type(charged) is not bool
        or charged is not submitted
        or terminal_state not in {"provider_failed", "pretransport_failed"}
        or expected_terminal_state is not None
        and terminal_state != expected_terminal_state
        or not isinstance(thread_sha256, str)
        or not re_is_sha256(thread_sha256)
        or not isinstance(call.get("operation_id"), str)
        or not re_is_sha256(str(call.get("call_events_sha256", "")))
    ):
        raise StateConflictError("qualification Retry is not an exact Planner failure")
    return _safe_sol_call_projection(call)


def _safe_sol_call_projection(call: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "call_id": call["operation_id"],
        "stored_thread_sha256": call["session_identity_sha256"],
        "submitted": call["submitted"],
        "charged": call["charged"],
        "terminal_state": call["terminal_state"],
        "duration_ms": call["duration_ms"],
        "provider_receipt_sha256": call.get("provider_receipt_sha256"),
        "failure_receipt_sha256": call.get("failure_receipt_sha256"),
        "call_events_sha256": call["call_events_sha256"],
    }


def _finalize_transport_retry_chain(
    resolution: TransportRetryResolutionV1,
    *,
    operation_records: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    if resolution.completion_response is None or resolution.critical_failure is not None:
        raise StateConflictError("qualification successful Retry resolution changed")
    planner_calls = [
        value
        for value in operation_records
        if value.get("provider_family") == "sol" and value.get("owner") == "planner"
    ]
    failed_call_ids = {str(value["call_id"]) for value in resolution.failed_calls}
    failed = [value for value in planner_calls if value.get("operation_id") in failed_call_ids]
    replacements = [
        value for value in planner_calls if value.get("operation_id") not in failed_call_ids
    ]
    if (
        len(resolution.failures) != resolution.retry_action_count
        or len(resolution.failed_calls) != resolution.retry_action_count
        or len(failed) != len(resolution.failed_calls)
        or len(replacements) != 1
    ):
        raise StateConflictError("qualification Retry Planner chain changed")
    if [_safe_sol_call_projection(value) for value in failed] != [
        dict(value) for value in resolution.failed_calls
    ]:
        raise StateConflictError("qualification Retry failed-call chain changed")
    replacement = replacements[0]
    replacement_thread = replacement.get("session_identity_sha256")
    if (
        replacement.get("submitted") is not True
        or replacement.get("charged") is not True
        or replacement.get("terminal_state") != "typed_accepted"
        or not isinstance(replacement_thread, str)
        or not re_is_sha256(replacement_thread)
        or replacement_thread
        in {value["stored_thread_sha256"] for value in resolution.failed_calls}
    ):
        raise StateConflictError("qualification Retry did not use one fresh successful Planner")
    completion_sha256 = canonical_sha256(resolution.completion_response.body)
    failure_chain = [
        _transport_failure_chain_entry(failure, call, attempt_number=index)
        for index, (failure, call) in enumerate(
            zip(resolution.failures, resolution.failed_calls, strict=True),
            start=1,
        )
    ]
    body = {
        "request_id_sha256": resolution.failure.request_id_sha256,
        "retry_id_sha256": resolution.failure.retry_id_sha256,
        "effect_proof_sha256": resolution.failure.effect_proof_sha256,
        "failure_response_sha256": resolution.failure.response_sha256,
        "failed_call": dict(resolution.failed_calls[0]),
        "failed_calls": [dict(value) for value in resolution.failed_calls],
        "failure_chain": failure_chain,
        "replacement_call": _safe_sol_call_projection(replacement),
        "retry_action_count": resolution.retry_action_count,
        "maximum_retry_actions": 2,
        "provider_attempt_count": len(resolution.failed_calls) + 1,
        "actions": [dict(value) for value in resolution.actions],
        "completion_sha256": completion_sha256,
    }
    return {**body, "chain_sha256": canonical_sha256(body)}


def _finalize_transport_retry_exhaustion(
    resolution: TransportRetryResolutionV1,
    *,
    operation_records: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    critical = resolution.critical_failure
    if resolution.completion_response is not None or critical is None:
        raise StateConflictError("qualification exhausted Retry resolution changed")
    planner_calls = [
        value
        for value in operation_records
        if value.get("provider_family") == "sol" and value.get("owner") == "planner"
    ]
    if (
        len(operation_records) != 3
        or len(planner_calls) != 3
        or len(resolution.failures) != 2
        or len(resolution.failed_calls) != 3
        or len(resolution.actions) != 2
        or [_safe_sol_call_projection(value) for value in planner_calls]
        != [dict(value) for value in resolution.failed_calls]
    ):
        raise StateConflictError("qualification exhausted Retry ledger chain changed")
    failure_chain = [
        _transport_failure_chain_entry(failure, call, attempt_number=index)
        for index, (failure, call) in enumerate(
            zip(resolution.failures, resolution.failed_calls[:2], strict=True),
            start=1,
        )
    ]
    terminal_dispatch = resolution.failures[-1]
    failure_chain.append(
        {
            "attempt_number": 3,
            "request_id_sha256": terminal_dispatch.request_id_sha256,
            "dispatch_retry_id_sha256": terminal_dispatch.retry_id_sha256,
            "dispatch_effect_proof_sha256": terminal_dispatch.effect_proof_sha256,
            "request_sha256": critical["request_sha256"],
            "stage_input_sha256": critical["stage_input_sha256"],
            "attempt_chain_sha256": critical["attempt_chain_sha256"],
            "terminal_evidence_sha256": critical["terminal_evidence_sha256"],
            "final_failure_class": critical["final_failure_class"],
            "failed_call": dict(resolution.failed_calls[-1]),
        }
    )
    body = {
        "outcome": "critical_provider_stage_failure",
        "request_id_sha256": resolution.failure.request_id_sha256,
        "retry_id_sha256": resolution.failure.retry_id_sha256,
        "effect_proof_sha256": resolution.failure.effect_proof_sha256,
        "failure_response_sha256": resolution.failure.response_sha256,
        "failed_call": dict(resolution.failed_calls[0]),
        "failed_calls": [dict(value) for value in resolution.failed_calls],
        "failure_chain": failure_chain,
        "replacement_call": None,
        "retry_action_count": resolution.retry_action_count,
        "maximum_retry_actions": 2,
        "provider_attempt_count": len(resolution.failed_calls),
        "actions": [dict(value) for value in resolution.actions],
        "critical_provider_stage_failure": dict(critical),
        "critical_failure_sha256": canonical_sha256(critical),
        "completion_sha256": None,
    }
    return {**body, "chain_sha256": canonical_sha256(body)}


def _transport_failure_chain_entry(
    failure: TransportRetryFailureV1,
    call: Mapping[str, Any],
    *,
    attempt_number: int,
) -> dict[str, Any]:
    return {
        "attempt_number": attempt_number,
        "request_id_sha256": failure.request_id_sha256,
        "retry_id_sha256": failure.retry_id_sha256,
        "effect_proof_sha256": failure.effect_proof_sha256,
        "failure_response_sha256": failure.response_sha256,
        "failed_call": dict(call),
    }


def _sol_charged_operation_count(events: Sequence[Mapping[str, Any]]) -> int:
    by_call: dict[str, list[Mapping[str, Any]]] = {}
    for event in events:
        call_id = event.get("call_id")
        if isinstance(call_id, str):
            by_call.setdefault(call_id, []).append(event)
    charged = 0
    for values in by_call.values():
        states = {value.get("state") for value in values}
        if "transport_invoked" in states or values[-1].get("state") in {
            "prepared_not_invoked",
            "worker_started_not_invoked",
            "worker_preflight_not_invoked",
        }:
            charged += 1
    return charged


def _validate_provider_delta(
    fixture: QualificationFixtureV1,
    projections: Sequence[Mapping[str, Any]],
    delta: ProviderLedgerDeltaV1,
    *,
    retry_chains: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    if not projections or len(projections) > 2:
        raise StateConflictError("qualification attempt accounting is invalid")
    operation_sets = [
        cast(Mapping[str, int], projection["provider_operations"]) for projection in projections
    ]
    if fixture.expected_route is QualificationRoute.ORDINARY:
        expected_sol = sum(value["planner"] + value["validator"] for value in operation_sets)
        expected_deepseek = sum(value["writer"] + value["recorder"] for value in operation_sets)
    else:
        expected_sol = sum(value["planner"] for value in operation_sets)
        expected_deepseek = sum(
            value["adult_scene"] + value["adult_filter"] for value in operation_sets
        )
    failed_calls = [
        failed
        for chain in retry_chains
        for failed in cast(Sequence[Mapping[str, Any]], chain["failed_calls"])
    ]
    expected_submitted_sol = expected_sol + sum(
        value.get("submitted") is True for value in failed_calls
    )
    expected_charged_sol = expected_sol + sum(
        value.get("charged") is True for value in failed_calls
    )
    if delta.sol_transport_operations != expected_submitted_sol:
        raise StateConflictError("qualification Sol ledger differs from HTTP projection")
    if delta.sol_charged_operations != expected_charged_sol:
        raise StateConflictError("qualification charged Sol ledger differs from HTTP projection")
    if delta.deepseek_started_operations != expected_deepseek:
        raise StateConflictError("qualification DeepSeek ledger differs from HTTP projection")
    if delta.deepseek_completed_operations != delta.deepseek_started_operations:
        raise StateConflictError("qualification DeepSeek operation did not complete")
    allowed_failed_call_ids = {
        str(value["call_id"]) for value in failed_calls if isinstance(value.get("call_id"), str)
    }
    for value in delta.sol_events:
        state = value.get("state")
        if state in {
            "provider_completed_post_validation_failed",
            "pretransport_failed",
            "provider_failed",
        }:
            if value.get("call_id") not in allowed_failed_call_ids or state not in {
                "pretransport_failed",
                "provider_failed",
            }:
                raise StateConflictError("qualification Sol ledger contains an unrelated failure")
    if any(
        value.get("event") in {"invocation_failed", "forbidden_automatic_operation_observed"}
        for value in delta.deepseek_events
    ):
        raise StateConflictError("qualification DeepSeek ledger contains a failure")
    input_tokens = delta.deepseek_input_tokens
    cached_tokens = delta.deepseek_cached_input_tokens
    return {
        "sol_http_operations": delta.sol_transport_operations,
        "sol_charged_operations": delta.sol_charged_operations,
        "deepseek_http_operations": delta.deepseek_started_operations,
        "deepseek_input_tokens": input_tokens,
        "deepseek_cached_input_tokens": cached_tokens,
        "deepseek_cache_ratio": (
            0.0 if input_tokens == 0 else round(cached_tokens / input_tokens, 6)
        ),
        "provider_operation_evidence_sha256": canonical_sha256(_provider_operation_records(delta)),
    }


def _validate_exhausted_transport_retry_delta(
    delta: ProviderLedgerDeltaV1,
    *,
    failed_calls: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    if len(failed_calls) != 3 or delta.deepseek_started_operations != 0:
        raise StateConflictError("qualification exhausted Retry provider span changed")
    expected_submitted = sum(value.get("submitted") is True for value in failed_calls)
    expected_charged = sum(value.get("charged") is True for value in failed_calls)
    if (
        delta.sol_transport_operations != expected_submitted
        or delta.sol_charged_operations != expected_charged
    ):
        raise StateConflictError("qualification exhausted Retry Sol accounting changed")
    allowed_call_ids = {str(value["call_id"]) for value in failed_calls}
    if any(value.get("call_id") not in allowed_call_ids for value in delta.sol_events):
        raise StateConflictError("qualification exhausted Retry contains an unrelated Sol call")
    if any(
        value.get("state")
        in {
            "provider_completed_post_validation_failed",
        }
        or (
            value.get("state") in {"pretransport_failed", "provider_failed"}
            and value.get("call_id") not in allowed_call_ids
        )
        for value in delta.sol_events
    ):
        raise StateConflictError("qualification exhausted Retry terminal state changed")
    return {
        "sol_operations_observed": delta.sol_transport_operations,
        "sol_charged_operations_observed": delta.sol_charged_operations,
        "deepseek_operations_observed": delta.deepseek_started_operations,
        "provider_operation_evidence_sha256": canonical_sha256(_provider_operation_records(delta)),
    }


def _provider_stage_latency_summary(
    records: Sequence[Mapping[str, Any]],
    planner_observations: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Summarize measured provider transport time without inventing percentiles."""

    planner_classes: dict[str, str] = {}
    for value in planner_observations:
        operation_id = value.get("operation_id")
        latency_class = value.get("latency_class")
        if isinstance(operation_id, str) and isinstance(latency_class, str):
            planner_classes[operation_id] = latency_class
    stage_names = (
        "planner",
        "semantic_validator",
        "writer",
        "recorder",
        "adult_scene",
        "adult_filter",
    )
    result: dict[str, Any] = {}
    for stage in stage_names:
        selected = [value for value in records if value.get("stage") == stage]
        durations = [
            cast(int, value["duration_ms"])
            for value in selected
            if type(value.get("duration_ms")) is int
        ]
        prepared_durations = [
            cast(int, value["prepared_to_terminal_duration_ms"])
            for value in selected
            if type(value.get("prepared_to_terminal_duration_ms")) is int
        ]
        failures = sum(_provider_operation_failed(value) for value in selected)
        classes: dict[str, list[int]] = {}
        for value in selected:
            duration_ms = value.get("duration_ms")
            if type(duration_ms) is not int:
                continue
            if stage == "planner":
                operation_id = value.get("operation_id")
                session_class = (
                    planner_classes.get(operation_id, "unclassified")
                    if isinstance(operation_id, str)
                    else "unclassified"
                )
            elif stage == "writer":
                session_class = "fresh_rehydration"
            else:
                session_class = "fresh_single_use"
            classes.setdefault(session_class, []).append(duration_ms)
        retained_violations: list[dict[str, Any]] = []
        if stage == "planner":
            for value in selected:
                operation_id = value.get("operation_id")
                duration_ms = value.get("duration_ms")
                if (
                    isinstance(operation_id, str)
                    and type(duration_ms) is int
                    and planner_classes.get(operation_id) == "retained_latency_concern"
                ):
                    retained_violations.append(
                        {
                            "operation_id_sha256": text_sha256(operation_id),
                            "duration_ms": duration_ms,
                        }
                    )
        result[stage] = {
            "operations_total": len(selected),
            **_latency_summary(durations, failures=failures),
            "prepared_to_terminal": _latency_summary(
                prepared_durations,
                failures=failures,
            ),
            "session_classes": {
                name: _latency_summary(values, failures=0)
                for name, values in sorted(classes.items())
            },
            "retained_latency_violations": retained_violations,
        }
    return result


def _latency_summary(values: Sequence[int], *, failures: int) -> dict[str, int | None]:
    if any(type(value) is not int or value < 0 for value in values):
        raise StateConflictError("qualification latency sample is invalid")
    if type(failures) is not int or failures < 0:
        raise StateConflictError("qualification latency failure count is invalid")
    if not values:
        return {
            "measured_samples": 0,
            "average_duration_ms": None,
            "median_duration_ms": None,
            "minimum_duration_ms": None,
            "maximum_duration_ms": None,
            "failures": failures,
        }
    return {
        "measured_samples": len(values),
        "average_duration_ms": round(sum(values) / len(values)),
        "median_duration_ms": round(median(values)),
        "minimum_duration_ms": min(values),
        "maximum_duration_ms": max(values),
        "failures": failures,
    }


def _provider_operation_failed(value: Mapping[str, Any]) -> bool:
    terminal = value.get("terminal_state")
    return isinstance(terminal, str) and (
        terminal.endswith("failed") or terminal in {"provider_failed", "pretransport_failed"}
    )


def _provider_operation_records(delta: ProviderLedgerDeltaV1) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    sol_by_call: dict[str, list[Mapping[str, Any]]] = {}
    for event in delta.sol_events:
        call_id = event.get("call_id")
        if isinstance(call_id, str):
            sol_by_call.setdefault(call_id, []).append(event)
    for call_id, events in sol_by_call.items():
        identity = events[0]
        owner = identity.get("owner")
        model = identity.get("model")
        stage = "semantic_validator" if owner == "validator" else "planner"
        provider_family = (
            "luna"
            if stage == "semantic_validator" or (isinstance(model, str) and "luna" in model.lower())
            else "sol"
        )
        invoked = next(
            (value for value in events if value.get("state") == "transport_invoked"),
            None,
        )
        terminal = events[-1]
        terminal_state = terminal.get("state")
        charged = invoked is not None or terminal_state in {
            "prepared_not_invoked",
            "worker_started_not_invoked",
            "worker_preflight_not_invoked",
        }
        timing_start = identity if invoked is None else invoked
        records.append(
            {
                "provider_family": provider_family,
                "operation_id": call_id,
                "owner": owner,
                "stage": stage,
                "route": identity.get("route"),
                "model": model,
                "session_identity_sha256": identity.get("stored_thread_sha256"),
                "submitted": invoked is not None,
                "charged": charged,
                "terminal_state": terminal_state,
                "started_at_utc": timing_start.get("recorded_at_utc"),
                "prepared_at_utc": identity.get("recorded_at_utc"),
                "completed_at_utc": terminal.get("recorded_at_utc"),
                "duration_ms": _duration_ms(timing_start, terminal),
                "prepared_to_terminal_duration_ms": _duration_ms(identity, terminal),
                "provider_receipt_sha256": terminal.get("provider_receipt_sha256"),
                "failure_receipt_sha256": terminal.get("failure_receipt_sha256"),
                "call_events_sha256": canonical_sha256(events),
            }
        )

    deepseek_prepared: dict[str, Mapping[str, Any]] = {}
    deepseek_started: dict[tuple[str, int], Mapping[str, Any]] = {}
    deepseek_completed: dict[tuple[str, int], Mapping[str, Any]] = {}
    deepseek_terminal: dict[str, Mapping[str, Any]] = {}
    for event in delta.deepseek_events:
        invocation_id = event.get("invocation_id")
        if not isinstance(invocation_id, str):
            continue
        if event.get("event") == "invocation_prepared":
            deepseek_prepared[invocation_id] = event
            continue
        if event.get("event") in {"invocation_completed", "invocation_failed"}:
            deepseek_terminal[invocation_id] = event
        operation_index = event.get("operation_index")
        if type(operation_index) is not int:
            continue
        key = (invocation_id, operation_index)
        if event.get("event") == "provider_operation_started":
            deepseek_started[key] = event
        elif event.get("event") == "provider_operation_completed":
            deepseek_completed[key] = event
    for key, started in deepseek_started.items():
        completed = deepseek_completed.get(key)
        invocation_id = key[0]
        prepared = deepseek_prepared.get(invocation_id, {})
        invocation_terminal = deepseek_terminal.get(invocation_id)
        purpose = prepared.get("purpose")
        stage = _deepseek_stage(purpose)
        timing_terminal = completed if completed is not None else invocation_terminal
        records.append(
            {
                "provider_family": "deepseek",
                "operation_id": f"{key[0]}:{key[1]}",
                "owner": purpose,
                "stage": stage,
                "route": prepared.get("route"),
                "model": "deepseek-via-confined-pi",
                "session_identity_sha256": text_sha256(key[0]),
                "prepared_at_utc": prepared.get("recorded_at_utc"),
                "started_at_utc": started.get("recorded_at_utc"),
                "completed_at_utc": (
                    None if timing_terminal is None else timing_terminal.get("recorded_at_utc")
                ),
                "duration_ms": _duration_ms(started, timing_terminal),
                "prepared_to_terminal_duration_ms": _duration_ms(
                    prepared,
                    timing_terminal,
                ),
                "input_tokens": None if completed is None else completed.get("input_tokens"),
                "cached_input_tokens": (
                    None if completed is None else completed.get("cached_input_tokens")
                ),
                "output_tokens": None if completed is None else completed.get("output_tokens"),
                "reasoning_tokens": (
                    None if completed is None else completed.get("reasoning_tokens")
                ),
                "finish_status": None if completed is None else completed.get("finish_status"),
                "terminal_state": (
                    None if invocation_terminal is None else invocation_terminal.get("event")
                ),
            }
        )
    return records


def _deepseek_stage(purpose: object) -> str:
    mapping = {
        "writer": "writer",
        "recorder": "recorder",
        "adult_scene": "adult_scene",
        "adult-scene": "adult_scene",
        "adult_filter": "adult_filter",
        "adult-filter": "adult_filter",
    }
    if not isinstance(purpose, str) or purpose not in mapping:
        raise StateConflictError("qualification DeepSeek operation purpose is unbound")
    return mapping[purpose]


def _load_sol_events(path: Path) -> tuple[Mapping[str, Any], ...]:
    events = _read_jsonl(path)
    for index, value in enumerate(events, start=1):
        if value.get("event_index") != index:
            raise StateConflictError("Sol provider ledger event order changed")
        if not isinstance(value.get("call_id"), str) or not isinstance(value.get("state"), str):
            raise StateConflictError("Sol provider ledger event is invalid")
    return events


def _load_deepseek_events(path: Path) -> tuple[Mapping[str, Any], ...]:
    events = _read_jsonl(path)
    expected_global = 0
    for value in events:
        if not isinstance(value.get("event"), str) or not isinstance(
            value.get("invocation_id"), str
        ):
            raise StateConflictError("DeepSeek provider ledger event is invalid")
        if value.get("event") == "provider_operation_started":
            expected_global += 1
            if value.get("global_operation_index") != expected_global:
                raise StateConflictError("DeepSeek provider ledger operation order changed")
    return events


def _read_jsonl(path: Path) -> tuple[Mapping[str, Any], ...]:
    if not path.is_file():
        return ()
    values: list[Mapping[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise StateConflictError("qualification provider ledger is unreadable") from exc
    for line in lines:
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise StateConflictError("qualification provider ledger is malformed") from exc
        if not isinstance(value, Mapping):
            raise StateConflictError("qualification provider ledger contains a non-object")
        values.append(value)
    return tuple(values)


def _artifact_entries(
    repository_root: Path,
    paths: Sequence[Path],
    *,
    external: bool,
) -> list[dict[str, Any]]:
    discovered: dict[str, Path] = {}
    for raw in paths:
        path = raw.resolve() if external else (repository_root / raw).resolve()
        if not external and not path.is_relative_to(repository_root):
            raise ContractValidationError("qualification artifact escaped repository")
        if path.is_symlink() or not path.exists():
            raise ContractValidationError(f"qualification artifact is missing: {path}")
        candidates = [path] if path.is_file() else sorted(path.rglob("*"))
        for candidate in candidates:
            if candidate.is_symlink():
                raise ContractValidationError("qualification artifact tree contains a symlink")
            if not candidate.is_file() or "__pycache__" in candidate.parts:
                continue
            key = (
                str(candidate.resolve())
                if external
                else candidate.relative_to(repository_root).as_posix()
            )
            discovered[key] = candidate
    entries: list[dict[str, Any]] = []
    for key, path in sorted(discovered.items(), key=lambda item: item[0].casefold()):
        data = path.read_bytes()
        entries.append(
            {
                "path": key,
                "location": "external" if external else "repository",
                "bytes": len(data),
                "sha256": bytes_sha256(data),
            }
        )
    return entries


def _validate_phase_fixture_counts(
    phase: QualificationPhase,
    fixtures: Sequence[QualificationFixtureV1],
) -> None:
    actual = {
        route.value: sum(value.expected_route is route for value in fixtures)
        for route in QualificationRoute
    }
    if actual != dict(EXPECTED_PHASE_COUNTS[phase.value]):
        raise ContractValidationError(f"{phase.value} qualification fixture count changed")


def _validate_phase_route_lineage(
    fixtures: Sequence[QualificationFixtureV1],
) -> None:
    current = QualificationRoute.ORDINARY
    for fixture in fixtures:
        if fixture.initial_route is not current:
            raise ContractValidationError(
                f"qualification route lineage changed before {fixture.fixture_id}"
            )
        current = fixture.expected_next_route
    if current is not QualificationRoute.ORDINARY:
        raise ContractValidationError("qualification campaign did not return to ordinary")


def _session_id(manifest: Mapping[str, Any], phase: QualificationPhase) -> str:
    prefix = "be" if phase is QualificationPhase.BACKEND else "st"
    return f"q{str(manifest['manifest_sha256'])[:16]}-{prefix}"


def _ordered_phase_fixtures(
    phase: QualificationPhase,
    fixtures: Sequence[QualificationFixtureV1],
) -> tuple[QualificationFixtureV1, ...]:
    available = {value.fixture_id: value for value in fixtures if value.phase is phase}
    if phase is QualificationPhase.BACKEND:
        order = (
            *(f"backend-ordinary-{index:02d}" for index in range(1, 6)),
            *(f"backend-adult-{index:02d}" for index in range(1, 6)),
            *(f"backend-ordinary-{index:02d}" for index in range(6, 11)),
            *(f"backend-adult-{index:02d}" for index in range(6, 11)),
        )
    else:
        order = (
            *(f"sillytavern-ordinary-{index:02d}" for index in range(1, 4)),
            *(f"sillytavern-adult-{index:02d}" for index in range(1, 4)),
            *(f"sillytavern-ordinary-{index:02d}" for index in range(4, 6)),
            *(f"sillytavern-adult-{index:02d}" for index in range(4, 6)),
        )
    if set(order) != set(available):
        raise ContractValidationError("qualification campaign turn identities changed")
    return tuple(available[value] for value in order)


def _duration_ms(
    started: Mapping[str, Any],
    completed: Mapping[str, Any] | None,
) -> int | None:
    if completed is None:
        return None
    try:
        start = datetime.fromisoformat(str(started["recorded_at_utc"]))
        end = datetime.fromisoformat(str(completed["recorded_at_utc"]))
    except (KeyError, TypeError, ValueError):
        return None
    return max(0, int((end - start).total_seconds() * 1000))


def _closed_failure_projection(exc: BaseException) -> dict[str, str]:
    """Return an identity-free failure receipt safe for durable evidence."""

    if isinstance(exc, _CriticalProviderStageError):
        failure_type = "CriticalProviderStageError"
        failure_category = "provider_stage_attempts_exhausted"
    elif isinstance(exc, ContractValidationError):
        failure_type = "ContractValidationError"
        failure_category = "contract_validation"
    elif isinstance(exc, StateConflictError):
        failure_type = "StateConflictError"
        failure_category = "state_conflict"
    elif isinstance(exc, TimeoutError):
        failure_type = "TimeoutError"
        failure_category = "transport_timeout"
    elif isinstance(exc, OSError):
        failure_type = "OSError"
        failure_category = "transport_io"
    elif isinstance(exc, (KeyboardInterrupt, SystemExit)):
        failure_type = "Interrupted"
        failure_category = "interrupted"
    else:
        failure_type = "UnexpectedError"
        failure_category = "unexpected"
    identity = {
        "failure_type": failure_type,
        "failure_category": failure_category,
    }
    return {**identity, "failure_sha256": canonical_sha256(identity)}


def _nonnegative_int(value: object, *, default: int) -> int:
    return value if type(value) is int and value >= 0 else default


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("xb") as stream:
        stream.write(canonical_bytes(payload) + b"\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


__all__ = [
    "ClientResponseV1",
    "DEEPSEEK_HTTP_OPERATION_CEILING",
    "DEEPSEEK_PER_INVOCATION_CEILING",
    "FullModelQualificationRunner",
    "QualificationClient",
    "QualificationFixtureV1",
    "QualificationPhase",
    "QualificationRoute",
    "QUALIFICATION_EXECUTION_POLICY",
    "RETAINED_PLANNER_LATENCY_CONCERN_MS",
    "SOL_FAMILY_CEILING",
    "TERRA_CEILING",
    "build_qualification_manifest",
    "load_qualification_fixtures",
    "load_qualification_manifest",
    "qualification_request_payload",
    "validate_qualification_manifest",
    "verify_qualification_artifacts",
    "write_qualification_manifest",
]
