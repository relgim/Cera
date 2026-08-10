"""Frozen, fail-closed qualification contracts for the full-model CERA route.

The qualification runner deliberately owns no story logic and no automatic
provider retry.  It submits two ordered, retained-session campaigns to
``cera-alpha``, verifies each committed HTTP projection, and reconciles that
projection with the append-only Sol and DeepSeek ledgers.  One explicit creator
Regenerate may replace a noncritical rejected first pass; the rejected outcome
remains visible in evidence.  Exact adult prose is never copied into
qualification evidence; only its response hash and protected custody hashes
are retained.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
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
QUALIFICATION_MANIFEST_SCHEMA = "cera.pi_scene.full_model_qualification_manifest.v1"
QUALIFICATION_RESULT_SCHEMA = "cera.pi_scene.full_model_qualification_result.v1"

SOL_FAMILY_CEILING = 60
DEEPSEEK_HTTP_OPERATION_CEILING = 480
DEEPSEEK_PER_INVOCATION_CEILING = 6
TERRA_CEILING = 0

# The first Planner operation on each physical thread hydrates world/context
# state and is reported separately, including a fresh thread after transport
# recovery. Later retained calls may finish within their hard transport bound,
# but three minutes is a diagnostic concern visible in qualification evidence.
RETAINED_PLANNER_LATENCY_CONCERN_MS = 180_000

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
            try:
                response = client.complete(
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
                        "transport": response.transport,
                        "path": response.path,
                        "status_code": response.status_code,
                        "duration_ms": response.duration_ms,
                        "response_sha256": canonical_sha256(response.body),
                    }
                )
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
                telemetry = _validate_provider_delta(fixture, projections, delta)
                operation_records = _provider_operation_records(delta)
                planner_latency = self._planner_latency_observations(operation_records)
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
                    "automatic_repair_actions": projection["automatic_repair_actions"],
                    "first_pass_response_sha256": canonical_sha256(response.body),
                    "response_sha256": canonical_sha256(accepted_response.body),
                    "visible_prose_sha256": projection["visible_prose_sha256"],
                    "accepted_turn_id": projection["accepted_turn_id"],
                    "accepted_receipt_sha256": projection["accepted_receipt_sha256"],
                    "provider_operations": projection["provider_operations"],
                    "latency_ms": response.duration_ms
                    + (0 if regeneration is None else regeneration.duration_ms),
                    "planner_latency": planner_latency,
                    **telemetry,
                }
                self.results.append(result)
                self.sol_operations += delta.sol_transport_operations
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
                if planner_latency is None:
                    planner_latency = self._planner_latency_observations(
                        _provider_operation_records(delta)
                    )
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
                    "failure_type": type(exc).__name__,
                    "failure_message": str(exc),
                    "sol_operations_observed": delta.sol_transport_operations,
                    "deepseek_operations_observed": delta.deepseek_started_operations,
                    "planner_latency": planner_latency,
                }
                self.results.append(failed)
                self.sol_operations += delta.sol_transport_operations
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

        observations: list[dict[str, Any]] = []
        for operation in planner_operations:
            session_identity_sha256 = cast(str, operation["session_identity_sha256"])
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
            "automatic_repair_actions": sum(
                int(value.get("automatic_repair_actions", 0)) for value in self.results
            ),
            "sequential_session_id_sha256": text_sha256(self.session_id),
            "retained_conversation_messages": len(self.history),
            "restart_count": self.restart_count,
            "sol_operations": self.sol_operations,
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
            },
            "results": self.results,
        }
        result_payload["result_sha256"] = canonical_sha256(result_payload)
        self.parent.evidence.publish_result(self.phase, result_payload)
        if self.failure is not None:
            raise StateConflictError(
                f"qualification failed at {self.results[-1]['fixture_id']}: {self.failure}"
            ) from self.failure
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
        "cera_reasoning_effort": "xhigh",
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
        "execution_policy": {
            "one_sequential_session_per_phase": True,
            "backend_route_order": ["ordinary"] * 5
            + ["adult"] * 5
            + ["ordinary"] * 5
            + ["adult"] * 5,
            "sillytavern_route_order": ["ordinary"] * 3
            + ["adult"] * 3
            + ["ordinary"] * 2
            + ["adult"] * 2,
            "first_pass_outcome_preserved": True,
            "maximum_explicit_regenerates_per_prompt": 1,
            "automatic_retry": False,
            "fallback": False,
            "model_substitution": False,
            "ordinary_semantic_pass_auto_accept_required": True,
            "adult_filter_pass_atomic_accept_required": True,
            "exact_adult_prose_in_qualification_evidence": False,
            "dynamic_loopback_only_cera_port": True,
            "installed_cera_port_5101_untouched": True,
            "frozen_isolated_sillytavern_tree_required": True,
            "phase_order": [phase.value for phase in QualificationPhase],
        },
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


def _validate_provider_delta(
    fixture: QualificationFixtureV1,
    projections: Sequence[Mapping[str, Any]],
    delta: ProviderLedgerDeltaV1,
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
    if delta.sol_transport_operations != expected_sol:
        raise StateConflictError("qualification Sol ledger differs from HTTP projection")
    if delta.deepseek_started_operations != expected_deepseek:
        raise StateConflictError("qualification DeepSeek ledger differs from HTTP projection")
    if delta.deepseek_completed_operations != delta.deepseek_started_operations:
        raise StateConflictError("qualification DeepSeek operation did not complete")
    if any(
        value.get("state")
        in {
            "provider_failed",
            "provider_completed_post_validation_failed",
            "pretransport_failed",
        }
        for value in delta.sol_events
    ):
        raise StateConflictError("qualification Sol ledger contains a failure")
    if any(
        value.get("event") in {"invocation_failed", "forbidden_automatic_operation_observed"}
        for value in delta.deepseek_events
    ):
        raise StateConflictError("qualification DeepSeek ledger contains a failure")
    input_tokens = delta.deepseek_input_tokens
    cached_tokens = delta.deepseek_cached_input_tokens
    return {
        "sol_http_operations": delta.sol_transport_operations,
        "deepseek_http_operations": delta.deepseek_started_operations,
        "deepseek_input_tokens": input_tokens,
        "deepseek_cached_input_tokens": cached_tokens,
        "deepseek_cache_ratio": (
            0.0 if input_tokens == 0 else round(cached_tokens / input_tokens, 6)
        ),
        "provider_operation_evidence_sha256": canonical_sha256(_provider_operation_records(delta)),
    }


def _provider_operation_records(delta: ProviderLedgerDeltaV1) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    sol_by_call: dict[str, list[Mapping[str, Any]]] = {}
    for event in delta.sol_events:
        call_id = event.get("call_id")
        if isinstance(call_id, str):
            sol_by_call.setdefault(call_id, []).append(event)
    for call_id, events in sol_by_call.items():
        invoked = next(
            (value for value in events if value.get("state") == "transport_invoked"),
            None,
        )
        if invoked is None:
            continue
        terminal = next(
            (
                value
                for value in reversed(events)
                if value.get("state") in {"provider_completed", "typed_accepted", "provider_failed"}
            ),
            None,
        )
        records.append(
            {
                "provider_family": "sol",
                "operation_id": call_id,
                "owner": invoked.get("owner"),
                "route": invoked.get("route"),
                "model": invoked.get("model"),
                "session_identity_sha256": invoked.get("stored_thread_sha256"),
                "started_at_utc": invoked.get("recorded_at_utc"),
                "completed_at_utc": None if terminal is None else terminal.get("recorded_at_utc"),
                "duration_ms": _duration_ms(invoked, terminal),
                "provider_receipt_sha256": (
                    None if terminal is None else terminal.get("provider_receipt_sha256")
                ),
            }
        )

    deepseek_started: dict[tuple[str, int], Mapping[str, Any]] = {}
    deepseek_completed: dict[tuple[str, int], Mapping[str, Any]] = {}
    for event in delta.deepseek_events:
        invocation_id = event.get("invocation_id")
        operation_index = event.get("operation_index")
        if not isinstance(invocation_id, str) or type(operation_index) is not int:
            continue
        key = (invocation_id, operation_index)
        if event.get("event") == "provider_operation_started":
            deepseek_started[key] = event
        elif event.get("event") == "provider_operation_completed":
            deepseek_completed[key] = event
    for key, started in deepseek_started.items():
        completed = deepseek_completed.get(key)
        records.append(
            {
                "provider_family": "deepseek",
                "operation_id": f"{key[0]}:{key[1]}",
                "owner": None,
                "route": None,
                "model": "deepseek-via-confined-pi",
                "session_identity_sha256": text_sha256(key[0]),
                "started_at_utc": started.get("recorded_at_utc"),
                "completed_at_utc": (
                    None if completed is None else completed.get("recorded_at_utc")
                ),
                "duration_ms": _duration_ms(started, completed),
                "input_tokens": None if completed is None else completed.get("input_tokens"),
                "cached_input_tokens": (
                    None if completed is None else completed.get("cached_input_tokens")
                ),
                "output_tokens": None if completed is None else completed.get("output_tokens"),
                "reasoning_tokens": (
                    None if completed is None else completed.get("reasoning_tokens")
                ),
                "finish_status": None if completed is None else completed.get("finish_status"),
            }
        )
    return records


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
