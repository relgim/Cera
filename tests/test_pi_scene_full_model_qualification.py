from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import traceback
import unittest
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import call, patch

from cera.errors import StateConflictError
from cera.pi_scene.qualification import (
    DEEPSEEK_HTTP_OPERATION_CEILING,
    DEEPSEEK_PER_INVOCATION_CEILING,
    QUALIFICATION_EXECUTION_POLICY,
    QUALIFICATION_MANIFEST_SCHEMA,
    QUALIFICATION_PLANNER_REASONING_EFFORT,
    SOL_FAMILY_CEILING,
    TERRA_CEILING,
    TRANSPORT_RETRY_STATUS_TIMEOUT_SECONDS,
    ClientResponseV1,
    FullModelQualificationRunner,
    QualificationFixtureV1,
    QualificationPhase,
    QualificationRoute,
    build_qualification_manifest,
    load_qualification_fixtures,
    qualification_request_payload,
    validate_qualification_manifest,
    verify_qualification_artifacts,
)
from cera.pi_scene.qualification_isolation import (
    run_staged_sillytavern_node_suites,
    stage_qualification_sillytavern,
    verify_qualification_sillytavern,
)
from cera.serialization import canonical_bytes, canonical_sha256
from scripts import run_pi_scene_full_model_qualification as entrypoint

ROOT = Path(__file__).parents[1]
FIXTURES = ROOT / "evaluation" / "fixtures" / "pi_scene_full_model_qualification_v1.json"


def _manifest() -> dict[str, Any]:
    body = {
        "schema_version": QUALIFICATION_MANIFEST_SCHEMA,
        "qualification_id": "qualification-fake-20260809",
        "source_commit": "a" * 40,
        "source_tree": "b" * 40,
        "route_model": "cera-alpha",
        "profile_id": "cera.pi_scene.lean.v1",
        "fixture_set_sha256": "c" * 64,
        "fixture_counts": {
            "backend": {"ordinary": 10, "adult": 10},
            "sillytavern": {"ordinary": 5, "adult": 5},
        },
        "provider_ceilings": {
            "sol": SOL_FAMILY_CEILING,
            "deepseek_http_operations": DEEPSEEK_HTTP_OPERATION_CEILING,
            "deepseek_per_invocation": DEEPSEEK_PER_INVOCATION_CEILING,
            "terra": TERRA_CEILING,
        },
        "execution_policy": deepcopy(QUALIFICATION_EXECUTION_POLICY),
        "artifact_categories": {"test": []},
    }
    return {**body, "manifest_sha256": canonical_sha256(body)}


class _FakeQualificationClient:
    def __init__(
        self,
        runtime_root: Path,
        *,
        reject_first: bool = False,
        reject_fixture_id: str | None = None,
        automatic_repair_fixture_id: str | None = None,
        planner_durations_ms: tuple[int, ...] = (),
        planner_session_identities: tuple[str, ...] = (),
    ) -> None:
        self.runtime_root = runtime_root
        self.runtime_root.mkdir(parents=True, exist_ok=True)
        self.reject_first = reject_first
        self.reject_fixture_id = reject_fixture_id
        self.automatic_repair_fixture_id = automatic_repair_fixture_id
        self.planner_durations_ms = planner_durations_ms
        self.planner_session_identities = planner_session_identities
        self.planner_duration_index = 0
        self.rejected = False
        self.calls = 0
        self.regenerates = 0
        self.session_id: str | None = None

    def complete(
        self,
        *,
        fixture: QualificationFixtureV1,
        session_id: str,
        payload: dict[str, Any] | Any,
    ) -> ClientResponseV1:
        value = dict(payload)
        if self.session_id is None:
            self.session_id = session_id
        self.assert_equal(self.session_id, session_id)
        self.assert_equal(len(value["messages"]), self.calls * 2 + 1)
        self.assert_equal(value["model"], "cera-alpha")
        self.assert_equal(value["cera_reasoning_effort"], "medium")
        self.calls += 1
        planner = (
            1
            if fixture.expected_route is QualificationRoute.ORDINARY
            or fixture.initial_route is QualificationRoute.ORDINARY
            else 0
        )
        reject_now = (
            self.reject_first and not self.rejected
        ) or self.reject_fixture_id == fixture.fixture_id
        if self.automatic_repair_fixture_id == fixture.fixture_id:
            if fixture.expected_route is not QualificationRoute.ORDINARY:
                raise AssertionError("automatic repair fake must use the ordinary route")
            self._append_sol("planner")
            for _ in range(2):
                self._append_deepseek("writer")
                self._append_sol("validator")
            self._append_deepseek("recorder")
            body = self._accepted_body(
                fixture,
                planner=1,
                writer=2,
                validator=2,
            )
        elif reject_now and not self.rejected:
            self.rejected = True
            self._append_attempt(fixture, planner=planner, accepted=False)
            body = self._rejected_body(fixture, planner=planner)
        else:
            self._append_attempt(fixture, planner=planner, accepted=True)
            body = self._accepted_body(fixture, planner=planner)
        return ClientResponseV1(
            transport="fake",
            path="/v1/chat/completions",
            status_code=200,
            duration_ms=10,
            body=body,
        )

    def regenerate(
        self,
        *,
        fixture: QualificationFixtureV1,
        review_id: str,
    ) -> ClientResponseV1:
        self.assert_equal(review_id, "review-0123456789abcdef0123456789ab")
        self.regenerates += 1
        self._append_attempt(fixture, planner=0, accepted=True)
        return ClientResponseV1(
            transport="fake-review",
            path=f"/v1/cera/reviews/{review_id}/decision",
            status_code=200,
            duration_ms=8,
            body={"successor": self._accepted_body(fixture, planner=0)},
        )

    def transport_retry_status(self, *, retry_id: str) -> ClientResponseV1:
        raise AssertionError(f"unexpected transport Retry status read: {retry_id}")

    def retry_transport(self, *, retry_id: str) -> ClientResponseV1:
        raise AssertionError(f"unexpected transport Retry action: {retry_id}")

    def _append_attempt(
        self,
        fixture: QualificationFixtureV1,
        *,
        planner: int,
        accepted: bool,
    ) -> None:
        if planner:
            self._append_sol("planner")
        if fixture.expected_route.value == "ordinary":
            self._append_sol("validator")
            self._append_deepseek("writer")
            if accepted:
                self._append_deepseek("recorder")
        else:
            self._append_deepseek("adult-scene")
            self._append_deepseek("adult-filter")

    def _append_sol(self, owner: str) -> None:
        path = self.runtime_root / "SOL_PROVIDER_CALLS.jsonl"
        existing = _jsonl(path)
        call_id = f"call-{len(existing) + 1}-{owner}"
        started = datetime.now(UTC)
        duration_ms = 0
        stored_thread_sha256 = "d" * 64
        if owner == "planner":
            planner_index = self.planner_duration_index
            if planner_index < len(self.planner_durations_ms):
                duration_ms = self.planner_durations_ms[planner_index]
            if planner_index < len(self.planner_session_identities):
                stored_thread_sha256 = self.planner_session_identities[planner_index]
            self.planner_duration_index += 1
        for state in ("transport_invoked", "provider_completed", "typed_accepted"):
            timestamp = (
                started
                if state == "transport_invoked"
                else started + timedelta(milliseconds=duration_ms)
            ).isoformat(timespec="microseconds")
            event = {
                "event_index": len(existing) + 1,
                "call_id": call_id,
                "owner": owner,
                "operation": owner,
                "state": state,
                "route": "fake",
                "model": "gpt-5.6-luna" if owner == "validator" else "gpt-5.6-sol",
                "stored_thread_sha256": stored_thread_sha256,
                "provider_receipt_sha256": "e" * 64,
                "recorded_at_utc": timestamp,
            }
            _append_jsonl(path, event)
            existing.append(event)

    def _append_deepseek(self, purpose: str) -> None:
        path = self.runtime_root / "DEEPSEEK_PROVIDER_OPERATIONS.jsonl"
        existing = _jsonl(path)
        global_index = (
            sum(value.get("event") == "provider_operation_started" for value in existing) + 1
        )
        invocation_id = f"piop-{global_index:04d}-{purpose}"
        timestamp = datetime.now(UTC).isoformat(timespec="microseconds")
        _append_jsonl(
            path,
            {
                "event": "provider_operation_started",
                "invocation_id": invocation_id,
                "operation_index": 1,
                "global_operation_index": global_index,
                "recorded_at_utc": timestamp,
            },
        )
        _append_jsonl(
            path,
            {
                "event": "provider_operation_completed",
                "invocation_id": invocation_id,
                "operation_index": 1,
                "input_tokens": 3000,
                "cached_input_tokens": 2400,
                "output_tokens": 300,
                "reasoning_tokens": 0,
                "finish_status": "stop",
                "recorded_at_utc": timestamp,
            },
        )

    def _accepted_body(
        self,
        fixture: QualificationFixtureV1,
        *,
        planner: int,
        writer: int = 1,
        validator: int = 1,
    ) -> dict[str, Any]:
        content = (
            f"Accepted {fixture.fixture_id} story continuation with enough text for validation."
        )
        common: dict[str, Any] = {
            "profile_id": "cera.pi_scene.lean.v1",
            "route_mode": fixture.expected_route.value,
            "status": "accepted",
            "provisional": False,
            "story_state_committed": True,
            "canon_status": "accepted",
            "accepted_turn_id": f"turn-{self.calls:04d}",
            "accepted_receipt_sha256": "f" * 64,
            "operational_warnings": [],
        }
        if fixture.expected_route.value == "ordinary":
            if validator == 1:
                provider_attempts = [
                    {
                        "attempt_number": 1,
                        "candidate_id": f"candidate:{fixture.fixture_id}:one",
                        "disposition": "semantic_pass",
                        "provider_operations": {
                            "planner": planner,
                            "writer": writer,
                            "validator": validator,
                        },
                    }
                ]
            else:
                provider_attempts = [
                    {
                        "attempt_number": 1,
                        "candidate_id": f"candidate:{fixture.fixture_id}:one",
                        "disposition": "semantic_rejected",
                        "provider_operations": {
                            "planner": planner,
                            "writer": 1,
                            "validator": 1,
                        },
                    },
                    {
                        "attempt_number": 2,
                        "candidate_id": f"candidate:{fixture.fixture_id}:two",
                        "disposition": "semantic_pass",
                        "provider_operations": {
                            "planner": 0,
                            "writer": writer - 1,
                            "validator": validator - 1,
                        },
                    },
                ]
            common.update(
                {
                    "semantic_validation": {"verdict": "pass"},
                    "recording_status": "complete",
                    "route_transition": None,
                    "creator_trace": {
                        "logic_owner": "codex_cognition",
                        "route_transition": None,
                    },
                    "provider_attempts": provider_attempts,
                    "provider_operations": {
                        "planner": planner,
                        "writer": writer,
                        "validator": validator,
                        "recorder": 1,
                    },
                }
            )
        else:
            expected_return = fixture.expected_next_route is QualificationRoute.ORDINARY
            transition = {
                "to_route": fixture.expected_next_route.value,
                "reason": "fake route transition",
            }
            common.update(
                {
                    "recording_status": "complete_preaccept_filter",
                    "recorder_required": False,
                    "adult_filter": {"verdict": "pass"},
                    "candidate_id": "candidate:adult:" + "a" * 32,
                    "operation_sha256": "3" * 64,
                    "protected_full_record_sha256": "1" * 64,
                    "codex_projection_sha256": "2" * 64,
                    "route_transition": transition,
                    "current_logic_route": fixture.expected_next_route.value,
                    "return_to_codex": expected_return,
                    "creator_trace": {
                        "logic_owner": "deepseek_adult_scene",
                        "route_transition": {
                            **transition,
                            "return_to_codex": expected_return,
                        },
                        "recording": {
                            "status": "complete_preaccept_filter",
                            "recorder_required": False,
                            "projection_status": "complete",
                            "protected_record_status": "complete",
                        },
                    },
                    "repair_attempts": [],
                    "provider_operations": {
                        "planner": planner,
                        "adult_scene": 1,
                        "adult_filter": 1,
                        "recorder": 0,
                    },
                }
            )
        return {
            "choices": [
                {
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ],
            "cera": common,
        }

    def _rejected_body(
        self,
        fixture: QualificationFixtureV1,
        *,
        planner: int,
    ) -> dict[str, Any]:
        operations = (
            {
                "planner": planner,
                "writer": 1,
                "validator": 1,
                "recorder": 0,
            }
            if fixture.expected_route.value == "ordinary"
            else {
                "planner": planner,
                "adult_scene": 1,
                "adult_filter": 1,
                "recorder": 0,
            }
        )
        conflict = {
            "conflict_class": (
                "severe_incompleteness"
                if fixture.expected_route is QualificationRoute.ORDINARY
                else "logic_not_realized"
            ),
            "summary": "candidate drift",
        }
        if fixture.expected_route is QualificationRoute.ORDINARY:
            route_validation = {
                "semantic_validation": {"verdict": "reject", "conflict": conflict},
                "provisional_review_id": "review-0123456789abcdef0123456789ab",
                "route_transition": None,
                "creator_trace": {
                    "logic_owner": "codex_cognition",
                    "route_transition": None,
                },
                "provider_attempts": [
                    {
                        "attempt_number": 1,
                        "candidate_id": f"candidate:{fixture.fixture_id}:rejected",
                        "disposition": "semantic_rejected",
                        "provider_operations": {
                            "planner": planner,
                            "writer": 1,
                            "validator": 1,
                        },
                    }
                ],
            }
        else:
            transition = {
                "to_route": fixture.expected_next_route.value,
                "reason": "fake route transition",
            }
            route_validation = {
                "adult_filter": {"verdict": "reject", "conflict": conflict},
                "review_id": "review-0123456789abcdef0123456789ab",
                "provisional_review_id": "review-0123456789abcdef0123456789ab",
                "route_transition": transition,
                "current_logic_route": fixture.initial_route.value,
                "creator_trace": {
                    "logic_owner": "deepseek_adult_scene",
                    "route_transition": {
                        **transition,
                        "return_to_codex": False,
                    },
                },
            }
        return {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": (
                            "Rejected first-pass continuation retained only for "
                            "inspection and testing."
                        ),
                    },
                    "finish_reason": "stop",
                }
            ],
            "cera": {
                "profile_id": "cera.pi_scene.lean.v1",
                "route_mode": fixture.expected_route.value,
                "status": "validation_rejected",
                "provisional": True,
                "story_state_committed": False,
                "regenerate_enabled": True,
                "provider_operations": operations,
                **(
                    {"repair_attempts": []}
                    if fixture.expected_route is QualificationRoute.ADULT
                    else {}
                ),
                **route_validation,
            },
        }

    def assert_equal(self, left: object, right: object) -> None:
        if left != right:
            raise AssertionError(f"{left!r} != {right!r}")


class _FakePlannerTransportRetryClient(_FakeQualificationClient):
    def __init__(
        self,
        runtime_root: Path,
        *,
        failure_fixture_id: str,
        ambiguous_post: bool = False,
        in_progress_polls: int = 0,
        failure_owner: str = "planner",
        failure_submitted: bool = True,
        failure_terminal_state: str | None = None,
        retry_failures_before_success: int = 0,
    ) -> None:
        if retry_failures_before_success not in {0, 1, 2}:
            raise ValueError("fake Retry failure count is outside the qualification bound")
        self.initial_thread = "a" * 64
        self.retry_threads = ("b" * 64, "c" * 64)
        self.replacement_thread = self.retry_threads[
            min(retry_failures_before_success, len(self.retry_threads) - 1)
        ]
        attempted_durations = (240_000, 300_000, 360_000)[: retry_failures_before_success + 2]
        planner_durations = (*attempted_durations, 180_000)
        attempted_threads = (self.initial_thread, *self.retry_threads)[
            : retry_failures_before_success + 2
        ]
        planner_threads = (
            *attempted_threads,
            *(self.replacement_thread for _ in range(20)),
        )
        super().__init__(
            runtime_root,
            planner_durations_ms=planner_durations,
            planner_session_identities=planner_threads,
        )
        self.failure_fixture_id = failure_fixture_id
        self.ambiguous_post = ambiguous_post
        self.in_progress_polls = in_progress_polls
        self.failure_owner = failure_owner
        self.failure_submitted = failure_submitted
        self.failure_terminal_state = failure_terminal_state or (
            "provider_failed" if failure_submitted else "pretransport_failed"
        )
        self.retry_failures_before_success = retry_failures_before_success
        self.failed = False
        self.retry_actions = 0
        self.retry_status_reads = 0
        self.retry_status_bodies: list[dict[str, Any]] = []
        self.retry_ids = tuple(f"retry-{character * 64}" for character in "de")
        self.effect_proof_sha256s = tuple(character * 64 for character in "12")
        self.retry_id = self.retry_ids[0]
        self.request_id = "request-" + "a" * 64
        self.effect_proof_sha256 = self.effect_proof_sha256s[0]
        self.retry_post_ids: list[str] = []
        self.retry_outcomes: dict[str, str] = {}
        self.retry_fixture: QualificationFixtureV1 | None = None
        self.retry_completions: dict[str, dict[str, Any]] = {}
        self.terminal_critical: dict[str, Any] | None = None

    def complete(
        self,
        *,
        fixture: QualificationFixtureV1,
        session_id: str,
        payload: dict[str, Any] | Any,
    ) -> ClientResponseV1:
        if fixture.fixture_id != self.failure_fixture_id or self.failed:
            return super().complete(fixture=fixture, session_id=session_id, payload=payload)
        value = dict(payload)
        if self.session_id is None:
            self.session_id = session_id
        self.assert_equal(self.session_id, session_id)
        self.assert_equal(len(value["messages"]), self.calls * 2 + 1)
        self.calls += 1
        self.failed = True
        self.retry_fixture = fixture
        self._append_sol_failure(
            self.failure_owner,
            submitted=self.failure_submitted,
            terminal_state=self.failure_terminal_state,
        )
        return self._failure_response(
            action_index=0,
            submitted=self.failure_submitted,
            duration_ms=240_000,
            transport="fake",
        )

    def transport_retry_status(self, *, retry_id: str) -> ClientResponseV1:
        try:
            retry_index = self.retry_ids.index(retry_id)
        except ValueError as exc:
            raise AssertionError(f"unexpected Retry identity: {retry_id}") from exc
        self.retry_status_reads += 1
        common = {
            "schema_version": "cera.pi_scene.transport_retry_status.v1",
            "retry_id": retry_id,
            "request_id": self.request_id,
            "effect_proof_sha256": self.effect_proof_sha256s[retry_index],
        }
        outcome = self.retry_outcomes.get(retry_id, "eligible")
        if outcome == "eligible":
            body = {
                **common,
                "state": "eligible",
                "retry_transport_enabled": True,
                "transport_retry": self._retry_action(retry_index),
            }
        elif self.in_progress_polls > 0:
            self.in_progress_polls -= 1
            body = {
                **common,
                "state": "in_progress",
                "retry_transport_enabled": False,
                "phase": "dispatch_started",
            }
        elif outcome == "superseded":
            successor_index = retry_index + 1
            body = {
                **common,
                "state": "superseded",
                "retry_transport_enabled": True,
                "superseded_by_retry_id": self.retry_ids[successor_index],
                "transport_retry": self._retry_action(successor_index),
            }
        elif outcome == "succeeded":
            completion = self.retry_completions[retry_id]
            body = {
                **common,
                "state": "succeeded",
                "retry_transport_enabled": False,
                "completion": completion,
                "completion_sha256": canonical_sha256(completion),
            }
        elif outcome == "attempts_exhausted":
            if self.terminal_critical is None:
                raise AssertionError("fake exhausted Retry lacks its critical projection")
            body = {
                **common,
                "schema_version": "cera.pi_scene.transport_retry_status.v2",
                "state": "attempts_exhausted",
                "retry_transport_enabled": False,
                "critical_provider_stage_failure": self.terminal_critical,
            }
        else:
            raise AssertionError(f"unexpected fake Retry outcome: {outcome}")
        self.retry_status_bodies.append(body)
        return ClientResponseV1(
            transport="fake-retry-status",
            path=f"/v1/cera/transport-retries/{retry_id}",
            status_code=200,
            duration_ms=1,
            body=body,
        )

    def retry_transport(self, *, retry_id: str) -> ClientResponseV1:
        if self.retry_fixture is None:
            raise AssertionError("manual Retry lacks its fixture")
        if self.retry_actions >= 2:
            raise AssertionError("qualification dispatched more than two Retry actions")
        expected_retry_id = self.retry_ids[self.retry_actions]
        self.assert_equal(retry_id, expected_retry_id)
        if retry_id in self.retry_post_ids:
            raise AssertionError("manual Retry repeated one backend-issued identity")
        self.retry_actions += 1
        self.retry_post_ids.append(retry_id)
        action_index = self.retry_actions
        if action_index <= self.retry_failures_before_success:
            self._append_sol_failure(
                "planner",
                submitted=True,
                terminal_state="provider_failed",
            )
            if action_index == 2:
                self.terminal_critical = self._terminal_critical_projection()
                self.retry_outcomes[retry_id] = "attempts_exhausted"
                response = self._exhausted_response(
                    retry_id=retry_id,
                    critical=self.terminal_critical,
                )
            else:
                self.retry_outcomes[retry_id] = "superseded"
                response = self._failure_response(
                    action_index=action_index,
                    submitted=True,
                    duration_ms=self.planner_durations_ms[action_index],
                    transport="fake-retry-action",
                )
        else:
            self._append_attempt(self.retry_fixture, planner=1, accepted=True)
            completion = self._accepted_body(self.retry_fixture, planner=1)
            completion["cera"]["request_id"] = self.request_id
            self.retry_completions[retry_id] = completion
            self.retry_outcomes[retry_id] = "succeeded"
            response = ClientResponseV1(
                transport="fake-retry-action",
                path=f"/v1/cera/transport-retries/{retry_id}",
                status_code=200,
                duration_ms=self.planner_durations_ms[action_index],
                body=completion,
            )
        if self.ambiguous_post and action_index == 1:
            raise TimeoutError("simulated response delivery ambiguity")
        return response

    def _retry_action(self, retry_index: int) -> dict[str, Any]:
        retry_id = self.retry_ids[retry_index]
        return {
            "schema_version": "cera.pi_scene.transport_retry.v1",
            "retry_id": retry_id,
            "retry_url": f"/v1/cera/transport-retries/{retry_id}",
            "method": "POST",
            "eligible": True,
            "automatic": False,
            "effect_proof_sha256": self.effect_proof_sha256s[retry_index],
        }

    def _failure_response(
        self,
        *,
        action_index: int,
        submitted: bool,
        duration_ms: int,
        transport: str,
    ) -> ClientResponseV1:
        action = self._retry_action(action_index)
        return ClientResponseV1(
            transport=transport,
            path=(
                "/v1/chat/completions"
                if action_index == 0
                else f"/v1/cera/transport-retries/{self.retry_ids[action_index - 1]}"
            ),
            status_code=500,
            duration_ms=duration_ms,
            body={
                "status": "error",
                "story_state_committed": False,
                "error": {
                    "schema_version": "cera.error.v1",
                    "error_code": "CERA_PROVIDER_TRANSPORT_FAILED",
                    "message": (
                        "A provider transport failed with no candidate or story-state effect."
                    ),
                    "request_id": self.request_id,
                    "story_state_committed": False,
                    "retry_mode": "manual_transport",
                    "provider_operation_submitted": submitted,
                    "accepted_state_changed": False,
                    "fallback_used": False,
                    "next_action": "use_transport_retry",
                    "retry_transport_enabled": True,
                    "transport_retry": action,
                },
            },
        )

    def _terminal_critical_projection(self) -> dict[str, Any]:
        observed = (1 if self.failure_submitted else 0) + 2
        return {
            "schema_version": "cera.provider_stage_retry_exhausted.v1",
            "severity": "critical",
            "provider": "codex",
            "model_family": "sol",
            "stage": "planner",
            "maximum_attempts": 3,
            "attempts_total": 3,
            "retries_consumed": 2,
            "story_state_committed": False,
            "failed_stage_effect_committed": False,
            "provider_operations_observed_total": observed,
            "provider_operations_conservative_total": observed,
            "final_failure_class": "provider_unavailable",
            "request_sha256": "4" * 64,
            "stage_input_sha256": "5" * 64,
            "attempt_chain_sha256": "6" * 64,
            "terminal_evidence_sha256": "7" * 64,
        }

    def _exhausted_response(
        self,
        *,
        retry_id: str,
        critical: dict[str, Any],
    ) -> ClientResponseV1:
        return ClientResponseV1(
            transport="fake-retry-action",
            path=f"/v1/cera/transport-retries/{retry_id}",
            status_code=503,
            duration_ms=self.planner_durations_ms[2],
            body={
                "status": "error",
                "story_state_committed": False,
                "error": {
                    "schema_version": "cera.error.v1",
                    "error_code": "CERA_PROVIDER_STAGE_RETRY_EXHAUSTED",
                    "message": "RAW PROVIDER FAILURE PROSE",
                    "trace_id": "trace:" + "8" * 32,
                    "request_id": self.request_id,
                    "branch_id": None,
                    "generation_id": None,
                    "stage": "pi_scene_http",
                    "story_state_committed": False,
                    "retry_mode": "exhausted",
                    "details": ["PRIVATE PROVIDER OUTPUT"],
                    "fallback_used": False,
                    "provider_operation_submitted": True,
                    "accepted_state_changed": False,
                    "next_action": "report_critical_provider_failure",
                    "debug_log_path": r"D:\private\provider-output.md",
                    "retry_transport_enabled": False,
                    "critical_provider_stage_failure": critical,
                },
            },
        )

    def _append_sol_failure(
        self,
        owner: str,
        *,
        submitted: bool,
        terminal_state: str,
    ) -> None:
        if submitted is (terminal_state == "pretransport_failed"):
            raise AssertionError("fake failure submission and terminal state disagree")
        path = self.runtime_root / "SOL_PROVIDER_CALLS.jsonl"
        existing = _jsonl(path)
        call_id = f"call-{len(existing) + 1}-{owner}-failed"
        started = datetime.now(UTC)
        planner_index = self.planner_duration_index
        duration_ms = (
            self.planner_durations_ms[planner_index]
            if planner_index < len(self.planner_durations_ms)
            else 1_000
        )
        stored_thread_sha256 = (
            self.planner_session_identities[planner_index]
            if planner_index < len(self.planner_session_identities)
            else self.replacement_thread
        )
        self.planner_duration_index += 1
        if terminal_state == "pretransport_failed":
            states = ("prepared_not_invoked", "pretransport_failed")
        elif terminal_state == "provider_completed_post_validation_failed":
            states = (
                "transport_invoked",
                "provider_completed",
                "provider_completed_post_validation_failed",
            )
        else:
            states = ("transport_invoked", "provider_failed")
        for state in states:
            timestamp = (
                started
                if state in {"prepared_not_invoked", "transport_invoked"}
                else started + timedelta(milliseconds=duration_ms)
            ).isoformat(timespec="microseconds")
            event = {
                "event_index": len(existing) + 1,
                "call_id": call_id,
                "owner": owner,
                "operation": owner,
                "state": state,
                "route": "fake",
                "model": "gpt-5.6-luna" if owner == "validator" else "gpt-5.6-sol",
                "stored_thread_sha256": stored_thread_sha256,
                "failure_receipt_sha256": str((planner_index + 1) % 10) * 64,
                "recorded_at_utc": timestamp,
            }
            _append_jsonl(path, event)
            existing.append(event)


class FullModelQualificationTests(unittest.TestCase):
    def test_outer_http_timeout_exceeds_every_bounded_provider_stage(self) -> None:
        self.assertEqual(entrypoint.QUALIFICATION_HTTP_HARD_TIMEOUT_SECONDS, 4_200)
        self.assertGreater(
            entrypoint.QUALIFICATION_HTTP_HARD_TIMEOUT_SECONDS,
            entrypoint.QUALIFICATION_MAX_SEQUENTIAL_PROVIDER_STAGES
            * entrypoint.QUALIFICATION_PROVIDER_STAGE_HARD_TIMEOUT_SECONDS,
        )
        self.assertEqual(
            TRANSPORT_RETRY_STATUS_TIMEOUT_SECONDS,
            entrypoint.QUALIFICATION_HTTP_HARD_TIMEOUT_SECONDS + 15,
        )

    def test_qualification_service_passes_exact_runtime_retry_seams_provider_free(self) -> None:
        seams = {
            "transport_retry_reinitializer": lambda *_args: None,
            "transport_provider_ledger_snapshot": lambda: None,
            "transport_retry_active_thread_snapshot": lambda *_args: None,
            "transport_retry_fresh_thread_initializer": lambda *_args: "f" * 64,
            "transport_completed_planner_abandoner": lambda *_args: None,
        }
        closed: list[bool] = []
        runtime = SimpleNamespace(
            coordinator=object(),
            store=object(),
            readable_debug=object(),
            full_model_controller=object(),
            world_resolver=object(),
            close=lambda: closed.append(True),
            **seams,
        )

        class FakeServer:
            def serve_forever(self) -> None:
                return

            def shutdown(self) -> None:
                return

            def server_close(self) -> None:
                return

        with (
            patch.object(entrypoint, "build_live_runtime", return_value=runtime),
            patch.object(entrypoint, "build_session_context_provider", return_value=object()),
            patch.object(entrypoint, "PiSceneHttpAdapter", return_value=object()) as adapter,
            patch.object(entrypoint, "build_pi_scene_server", return_value=FakeServer()),
        ):
            service = entrypoint._start_cera_service(
                runtime_root=Path("D:/Cera/provider-free-construction"),
                token="t" * 32,
                approved_origin="http://127.0.0.1:32123",
                port=32124,
                sol_ceiling=60,
                deepseek_ceiling=480,
            )
            service.close()

        kwargs = adapter.call_args.kwargs
        for name, seam in seams.items():
            self.assertIs(kwargs[name], seam)
        self.assertEqual(closed, [True])

    def test_sillytavern_startup_retries_and_discards_console_content(self) -> None:
        class ReadyResponse:
            status = 200

            def __enter__(self) -> ReadyResponse:
                return self

            def __exit__(self, *_args: object) -> None:
                return None

        process = SimpleNamespace(poll=lambda: None)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with (
                patch.object(
                    entrypoint,
                    "qualification_sillytavern_command",
                    return_value=("node", "server.js"),
                ),
                patch.object(
                    entrypoint.subprocess,
                    "Popen",
                    return_value=process,
                ) as popen,
                patch.object(
                    entrypoint,
                    "urlopen",
                    side_effect=(OSError("transient readiness miss"), ReadyResponse()),
                ) as ready,
                patch.object(entrypoint.time, "monotonic", side_effect=(0.0, 0.0, 0.1)),
                patch.object(entrypoint.time, "sleep"),
                patch.object(entrypoint, "_stop_process") as stop,
            ):
                observed = entrypoint._start_qualification_sillytavern(
                    root,
                    port=32123,
                    cera_port=32124,
                )

        self.assertIs(observed, process)
        self.assertEqual(ready.call_count, 2)
        self.assertEqual(popen.call_args.kwargs["stdout"], entrypoint.subprocess.DEVNULL)
        self.assertEqual(popen.call_args.kwargs["stderr"], entrypoint.subprocess.DEVNULL)
        raw_console_sentinel = (
            "adult-prose-requestBody-sentinel | /private/error/path | raw-response-body"
        )
        persisted = "\n".join(
            path.read_text(encoding="utf-8", errors="replace")
            for path in root.rglob("*")
            if path.is_file()
        )
        self.assertNotIn(raw_console_sentinel, persisted)
        self.assertFalse((root / "logs").exists())
        stop.assert_not_called()

    def test_manifest_v3_freezes_every_execution_policy_field(self) -> None:
        mutations = {
            "one_sequential_session_per_phase": False,
            "backend_route_order": ["adult"] * 10 + ["ordinary"] * 10,
            "sillytavern_route_order": ["adult"] * 5 + ["ordinary"] * 5,
            "first_pass_outcome_preserved": False,
            "maximum_explicit_regenerates_per_prompt": 2,
            "exact_adult_prose_in_qualification_evidence": True,
            "phase_order": ["sillytavern", "backend"],
            "planner_reasoning_effort": "xhigh",
        }
        for key, changed in mutations.items():
            with self.subTest(key=key):
                manifest = _manifest()
                manifest["execution_policy"][key] = changed
                unsigned = {
                    name: value for name, value in manifest.items() if name != "manifest_sha256"
                }
                manifest["manifest_sha256"] = canonical_sha256(unsigned)
                with self.assertRaisesRegex(StateConflictError, "execution policy changed"):
                    validate_qualification_manifest(manifest)
        manifest = _manifest()
        manifest["execution_policy"]["manual_planner_transport_retry"]["terminal_status_state"] = (
            "superseded"
        )
        unsigned = {name: value for name, value in manifest.items() if name != "manifest_sha256"}
        manifest["manifest_sha256"] = canonical_sha256(unsigned)
        with self.assertRaisesRegex(StateConflictError, "execution policy changed"):
            validate_qualification_manifest(manifest)

    def test_representative_canary_uses_medium_planner_effort(self) -> None:
        fixture = load_qualification_fixtures(FIXTURES)[0]
        payload = qualification_request_payload(
            fixture,
            session_id="qualification-medium-canary",
        )
        self.assertEqual(QUALIFICATION_PLANNER_REASONING_EFFORT, "medium")
        self.assertEqual(payload["cera_reasoning_effort"], "medium")
        self.assertEqual(
            QUALIFICATION_EXECUTION_POLICY["semantic_validator_reasoning_effort"],
            "xhigh",
        )

    def test_fixture_set_is_two_ordered_sequential_campaigns(self) -> None:
        fixtures = load_qualification_fixtures(FIXTURES)
        self.assertEqual(len(fixtures), 30)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime = root / "runtime"
            runner = FullModelQualificationRunner(
                manifest=_manifest(),
                runtime_root=runtime,
                evidence_root=root / "evidence",
            )
            client = _FakeQualificationClient(runtime)
            backend = runner.run_phase(QualificationPhase.BACKEND, fixtures, client)
            self.assertEqual(backend["passed_fixtures"], 20)
            self.assertEqual(backend["first_pass_accepted"], 20)
            self.assertEqual(backend["retained_conversation_messages"], 40)
            self.assertEqual(client.calls, 20)
            routes = [value["expected_route"] for value in backend["results"]]
            self.assertEqual(
                routes, ["ordinary"] * 5 + ["adult"] * 5 + ["ordinary"] * 5 + ["adult"] * 5
            )
            boundaries = {
                value["fixture_id"]: (
                    value["initial_route"],
                    value["observed_route"],
                    value["observed_next_route"],
                )
                for value in backend["results"]
                if value["fixture_id"]
                in {
                    "backend-ordinary-05",
                    "backend-adult-05",
                    "backend-ordinary-10",
                    "backend-adult-10",
                }
            }
            self.assertEqual(
                boundaries,
                {
                    "backend-ordinary-05": ("ordinary", "ordinary", "ordinary"),
                    "backend-adult-05": ("adult", "adult", "ordinary"),
                    "backend-ordinary-10": ("ordinary", "ordinary", "ordinary"),
                    "backend-adult-10": ("adult", "adult", "ordinary"),
                },
            )
            evidence = (root / "evidence" / "QUALIFICATION_EVENTS.jsonl").read_text(
                encoding="utf-8"
            )
            self.assertNotIn("Accepted backend", evidence)
            self.assertNotIn(fixtures[0].user_source, evidence)

            st_runtime = root / "st-runtime"
            st_runner = FullModelQualificationRunner(
                manifest=_manifest(),
                runtime_root=st_runtime,
                evidence_root=root / "st-evidence",
            )
            st_result = st_runner.run_phase(
                QualificationPhase.SILLYTAVERN,
                fixtures,
                _FakeQualificationClient(st_runtime),
            )
            st_boundaries = {
                value["fixture_id"]: (
                    value["initial_route"],
                    value["observed_route"],
                    value["observed_next_route"],
                )
                for value in st_result["results"]
                if value["fixture_id"]
                in {
                    "sillytavern-ordinary-03",
                    "sillytavern-adult-03",
                    "sillytavern-ordinary-05",
                    "sillytavern-adult-05",
                }
            }
            self.assertEqual(
                st_boundaries,
                {
                    "sillytavern-ordinary-03": ("ordinary", "ordinary", "ordinary"),
                    "sillytavern-adult-03": ("adult", "adult", "ordinary"),
                    "sillytavern-ordinary-05": ("ordinary", "ordinary", "ordinary"),
                    "sillytavern-adult-05": ("adult", "adult", "ordinary"),
                },
            )

    def test_planner_latency_separates_cold_start_from_retained_concern(self) -> None:
        fixtures = load_qualification_fixtures(FIXTURES)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime = root / "runtime"
            result = FullModelQualificationRunner(
                manifest=_manifest(),
                runtime_root=runtime,
                evidence_root=root / "evidence",
            ).run_phase(
                QualificationPhase.BACKEND,
                fixtures,
                _FakeQualificationClient(
                    runtime,
                    planner_durations_ms=(420_000, 180_000, 179_999),
                ),
            )

            first = result["results"][0]["planner_latency"][0]
            second = result["results"][1]["planner_latency"][0]
            third = result["results"][2]["planner_latency"][0]
            self.assertEqual(first["latency_class"], "cold_start")
            self.assertFalse(first["retained_latency_concern"])
            self.assertEqual(second["latency_class"], "retained_latency_concern")
            self.assertTrue(second["retained_latency_concern"])
            self.assertEqual(third["latency_class"], "retained_within_target")
            self.assertEqual(
                result["planner_latency_summary"],
                {
                    "cold_start_latency_ms": 420_000,
                    "retained_concern_threshold_ms": 180_000,
                    "cold_rehydration_calls": 0,
                    "maximum_cold_rehydration_latency_ms": None,
                    "retained_planner_calls": 11,
                    "retained_latency_concern_count": 1,
                    "maximum_retained_latency_ms": 180_000,
                    "average_retained_latency_ms": 32_727,
                },
            )

    def test_planner_thread_rotation_is_cold_then_retained_at_inclusive_threshold(
        self,
    ) -> None:
        fixtures = load_qualification_fixtures(FIXTURES)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime = root / "runtime"
            initial_thread = "d" * 64
            replacement_thread = "f" * 64
            result = FullModelQualificationRunner(
                manifest=_manifest(),
                runtime_root=runtime,
                evidence_root=root / "evidence",
            ).run_phase(
                QualificationPhase.BACKEND,
                fixtures,
                _FakeQualificationClient(
                    runtime,
                    planner_durations_ms=(420_000, 179_999, 240_000, 180_000),
                    planner_session_identities=(
                        initial_thread,
                        initial_thread,
                        *(replacement_thread for _ in range(10)),
                    ),
                ),
            )

            observations = [
                observation for row in result["results"] for observation in row["planner_latency"]
            ]
            self.assertEqual(
                [value["latency_class"] for value in observations[:4]],
                [
                    "cold_start",
                    "retained_within_target",
                    "cold_rehydration",
                    "retained_latency_concern",
                ],
            )
            self.assertEqual(
                [value["planner_thread_call_index"] for value in observations[:4]],
                [1, 2, 1, 2],
            )
            self.assertTrue(observations[2]["cold_start"])
            self.assertTrue(observations[2]["rehydrated_after_thread_rotation"])
            self.assertFalse(observations[2]["retained_latency_concern"])
            self.assertTrue(observations[3]["retained_latency_concern"])
            self.assertEqual(result["planner_latency_summary"]["cold_rehydration_calls"], 1)
            self.assertEqual(
                result["planner_latency_summary"]["maximum_cold_rehydration_latency_ms"],
                240_000,
            )
            self.assertEqual(result["planner_latency_summary"]["retained_planner_calls"], 10)
            self.assertEqual(
                result["planner_latency_summary"]["average_retained_latency_ms"],
                36_000,
            )

    def test_manual_planner_retry_chain_is_hash_safe_and_latency_complete(self) -> None:
        fixtures = load_qualification_fixtures(FIXTURES)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime = root / "runtime"
            client = _FakePlannerTransportRetryClient(
                runtime,
                failure_fixture_id="backend-ordinary-01",
                ambiguous_post=True,
            )
            result = FullModelQualificationRunner(
                manifest=_manifest(),
                runtime_root=runtime,
                evidence_root=root / "evidence",
            ).run_phase(QualificationPhase.BACKEND, fixtures, client)

            first = result["results"][0]
            second = result["results"][1]
            chain = first["transport_retry_chains"][0]
            action = chain["actions"][0]
            self.assertEqual(first["transport_retry_actions"], 1)
            self.assertEqual(result["transport_retry_actions"], 1)
            self.assertEqual(result["transport_retry_chains"], 1)
            self.assertEqual(result["automatic_transport_retry_actions"], 0)
            self.assertEqual(client.retry_actions, 1)
            self.assertEqual(client.retry_status_reads, 2)
            self.assertFalse(action["post_response_observed"])
            self.assertIsNone(action["post_response_sha256"])
            self.assertEqual(action["post_failure"]["failure_type"], "TimeoutError")
            self.assertEqual(action["post_failure"]["failure_category"], "transport_timeout")
            self.assertRegex(action["post_failure"]["failure_sha256"], r"^[a-f0-9]{64}$")
            self.assertEqual(chain["failed_call"]["terminal_state"], "provider_failed")
            self.assertTrue(chain["failed_call"]["submitted"])
            self.assertTrue(chain["failed_call"]["charged"])
            self.assertEqual(chain["replacement_call"]["terminal_state"], "typed_accepted")
            self.assertNotEqual(
                chain["failed_call"]["call_id"],
                chain["replacement_call"]["call_id"],
            )
            self.assertEqual(
                chain["failed_call"]["stored_thread_sha256"],
                client.initial_thread,
            )
            self.assertEqual(
                chain["replacement_call"]["stored_thread_sha256"],
                client.replacement_thread,
            )
            self.assertEqual(
                chain["chain_sha256"],
                canonical_sha256(
                    {key: value for key, value in chain.items() if key != "chain_sha256"}
                ),
            )
            self.assertEqual(first["sol_http_operations"], 3)
            self.assertEqual(first["sol_charged_operations"], 3)
            self.assertEqual(
                result["sol_submitted_operations"],
                result["sol_charged_operations"],
            )
            observations = [*first["planner_latency"], *second["planner_latency"]]
            self.assertEqual(
                [value["latency_class"] for value in observations],
                ["cold_start", "cold_rehydration", "retained_latency_concern"],
            )
            self.assertEqual(
                [value["duration_ms"] for value in observations],
                [240_000, 300_000, 180_000],
            )
            self.assertEqual(
                [value["session_identity_sha256"] for value in observations],
                [client.initial_thread, client.replacement_thread, client.replacement_thread],
            )

    def test_manual_planner_retry_follows_one_successor_then_succeeds(self) -> None:
        fixtures = load_qualification_fixtures(FIXTURES)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime = root / "runtime"
            client = _FakePlannerTransportRetryClient(
                runtime,
                failure_fixture_id="backend-ordinary-01",
                retry_failures_before_success=1,
                ambiguous_post=True,
            )
            result = FullModelQualificationRunner(
                manifest=_manifest(),
                runtime_root=runtime,
                evidence_root=root / "evidence",
            ).run_phase(QualificationPhase.BACKEND, fixtures, client)

            first = result["results"][0]
            second = result["results"][1]
            chain = first["transport_retry_chains"][0]
            self.assertEqual(client.retry_post_ids, list(client.retry_ids[:2]))
            self.assertEqual(client.retry_status_reads, 4)
            terminal_status = client.retry_status_bodies[-1]
            self.assertEqual(terminal_status["state"], "succeeded")
            self.assertFalse(terminal_status["retry_transport_enabled"])
            self.assertNotIn("transport_retry", terminal_status)
            self.assertNotIn("superseded_by_retry_id", terminal_status)
            self.assertEqual(result["transport_retry_actions"], 2)
            self.assertEqual(result["transport_retry_chain_actions"], 2)
            self.assertEqual(result["transport_retry_chains"], 1)
            self.assertEqual(result["transport_retry_terminal_critical_failures"], 0)
            self.assertEqual(chain["retry_action_count"], 2)
            self.assertEqual(chain["provider_attempt_count"], 3)
            self.assertEqual(
                [action["post_status_state"] for action in chain["actions"]],
                ["superseded", "succeeded"],
            )
            self.assertFalse(chain["actions"][0]["post_response_observed"])
            self.assertEqual(
                chain["actions"][0]["post_failure"]["failure_category"],
                "transport_timeout",
            )
            self.assertEqual(len(chain["failed_calls"]), 2)
            self.assertEqual(len(chain["failure_chain"]), 2)
            self.assertEqual(
                [value["attempt_number"] for value in chain["failure_chain"]],
                [1, 2],
            )
            call_ids = {
                *(value["call_id"] for value in chain["failed_calls"]),
                chain["replacement_call"]["call_id"],
            }
            thread_ids = {
                *(value["stored_thread_sha256"] for value in chain["failed_calls"]),
                chain["replacement_call"]["stored_thread_sha256"],
            }
            self.assertEqual(len(call_ids), 3)
            self.assertEqual(len(thread_ids), 3)
            self.assertEqual(first["sol_http_operations"], 4)
            self.assertEqual(first["sol_charged_operations"], 4)
            self.assertEqual(
                result["sol_submitted_operations"],
                result["sol_charged_operations"],
            )
            self.assertEqual(
                [
                    value["latency_class"]
                    for value in [*first["planner_latency"], *second["planner_latency"]]
                ],
                [
                    "cold_start",
                    "cold_rehydration",
                    "cold_rehydration",
                    "retained_latency_concern",
                ],
            )
            self.assertEqual(
                chain["chain_sha256"],
                canonical_sha256(
                    {key: value for key, value in chain.items() if key != "chain_sha256"}
                ),
            )
            self.assertTrue(
                all(
                    action["action_sha256"]
                    == canonical_sha256(
                        {key: value for key, value in action.items() if key != "action_sha256"}
                    )
                    for action in chain["actions"]
                )
            )

    def test_third_failed_planner_attempt_stops_with_critical_projection(self) -> None:
        fixtures = load_qualification_fixtures(FIXTURES)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime = root / "runtime"
            evidence = root / "evidence"
            client = _FakePlannerTransportRetryClient(
                runtime,
                failure_fixture_id="backend-ordinary-01",
                retry_failures_before_success=2,
            )
            runner = FullModelQualificationRunner(
                manifest=_manifest(),
                runtime_root=runtime,
                evidence_root=evidence,
            )
            with self.assertRaisesRegex(
                StateConflictError,
                "provider_stage_attempts_exhausted",
            ):
                runner.run_phase(QualificationPhase.BACKEND, fixtures, client)

            result = json.loads((evidence / "BACKEND_RESULT.json").read_text(encoding="utf-8"))
            first = result["results"][0]
            critical = first["critical_provider_stage_failure"]
            chain = first["transport_retry_chains"][0]
            self.assertEqual(client.retry_post_ids, list(client.retry_ids[:2]))
            self.assertEqual(client.retry_status_reads, 4)
            terminal_status = client.retry_status_bodies[-1]
            self.assertEqual(
                set(terminal_status),
                {
                    "schema_version",
                    "retry_id",
                    "request_id",
                    "effect_proof_sha256",
                    "state",
                    "retry_transport_enabled",
                    "critical_provider_stage_failure",
                },
            )
            self.assertEqual(terminal_status["state"], "attempts_exhausted")
            self.assertFalse(terminal_status["retry_transport_enabled"])
            self.assertNotIn("transport_retry", terminal_status)
            self.assertNotIn("superseded_by_retry_id", terminal_status)
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["transport_retry_actions"], 2)
            self.assertEqual(result["transport_retry_chain_actions"], 2)
            self.assertEqual(result["transport_retry_chains"], 1)
            self.assertEqual(result["transport_retry_terminal_critical_failures"], 1)
            self.assertEqual(result["sol_submitted_operations"], 3)
            self.assertEqual(result["sol_charged_operations"], 3)
            self.assertEqual(first["sol_operations_observed"], 3)
            self.assertEqual(first["sol_charged_operations_observed"], 3)
            self.assertEqual(first["deepseek_operations_observed"], 0)
            self.assertEqual(critical["severity"], "critical")
            self.assertEqual(critical["provider"], "codex")
            self.assertEqual(critical["model_family"], "sol")
            self.assertEqual(critical["stage"], "planner")
            self.assertEqual(critical["maximum_attempts"], 3)
            self.assertEqual(critical["attempts_total"], 3)
            self.assertEqual(critical["retries_consumed"], 2)
            self.assertEqual(critical["provider_operations_observed_total"], 3)
            self.assertEqual(critical["provider_operations_conservative_total"], 3)
            self.assertEqual(critical["final_failure_class"], "provider_unavailable")
            self.assertEqual(len(chain["failed_calls"]), 3)
            self.assertEqual(len(chain["failure_chain"]), 3)
            self.assertIsNone(chain["replacement_call"])
            self.assertIsNone(chain["completion_sha256"])
            self.assertEqual(
                len({value["call_id"] for value in chain["failed_calls"]}),
                3,
            )
            self.assertEqual(
                len({value["stored_thread_sha256"] for value in chain["failed_calls"]}),
                3,
            )
            self.assertEqual(
                [value["latency_class"] for value in first["planner_latency"]],
                ["cold_start", "cold_rehydration", "cold_rehydration"],
            )
            self.assertEqual(chain["critical_failure_sha256"], canonical_sha256(critical))
            self.assertEqual(
                [action["post_status_state"] for action in chain["actions"]],
                ["superseded", "attempts_exhausted"],
            )
            self.assertEqual(
                chain["chain_sha256"],
                canonical_sha256(
                    {key: value for key, value in chain.items() if key != "chain_sha256"}
                ),
            )
            artifact_text = "\n".join(
                path.read_text(encoding="utf-8") for path in evidence.rglob("*") if path.is_file()
            )
            for raw in (
                *client.retry_ids,
                client.request_id,
                "RAW PROVIDER FAILURE PROSE",
                "PRIVATE PROVIDER OUTPUT",
                r"D:\private\provider-output.md",
            ):
                self.assertNotIn(raw, artifact_text)

    def test_reduced_direct_exhaustion_envelope_is_rejected(self) -> None:
        fixtures = load_qualification_fixtures(FIXTURES)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime = root / "runtime"
            evidence = root / "evidence"
            client = _FakePlannerTransportRetryClient(
                runtime,
                failure_fixture_id="backend-ordinary-01",
                retry_failures_before_success=2,
            )
            critical = client._terminal_critical_projection()
            reduced = ClientResponseV1(
                transport="fake-retry-action",
                path=f"/v1/cera/transport-retries/{client.retry_ids[1]}",
                status_code=503,
                duration_ms=360_000,
                body={
                    "status": "error",
                    "story_state_committed": False,
                    "error": {
                        "schema_version": "cera.error.v1",
                        "error_code": "CERA_PROVIDER_STAGE_RETRY_EXHAUSTED",
                        "message": "REDUCED RAW ERROR",
                        "retry_mode": "exhausted",
                        "next_action": "report_critical_provider_failure",
                        "retry_transport_enabled": False,
                        "critical_provider_stage_failure": critical,
                    },
                },
            )
            with (
                patch.object(client, "_exhausted_response", return_value=reduced),
                self.assertRaisesRegex(StateConflictError, "state_conflict"),
            ):
                FullModelQualificationRunner(
                    manifest=_manifest(),
                    runtime_root=runtime,
                    evidence_root=evidence,
                ).run_phase(QualificationPhase.BACKEND, fixtures, client)

            result = json.loads((evidence / "BACKEND_RESULT.json").read_text(encoding="utf-8"))
            self.assertEqual(client.retry_actions, 2)
            self.assertEqual(client.retry_status_reads, 4)
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["transport_retry_terminal_critical_failures"], 0)
            self.assertNotIn(
                "REDUCED RAW ERROR",
                "\n".join(
                    path.read_text(encoding="utf-8")
                    for path in evidence.rglob("*")
                    if path.is_file()
                ),
            )

    def test_ambiguous_retry_timing_includes_post_and_poll_wall_clock(self) -> None:
        fixtures = load_qualification_fixtures(FIXTURES)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime = root / "runtime"
            client = _FakePlannerTransportRetryClient(
                runtime,
                failure_fixture_id="backend-ordinary-01",
                ambiguous_post=True,
                in_progress_polls=1,
            )
            post_end_ns = 4_200_000_000_000
            poll_end_ns = post_end_ns + 500_000_000
            with (
                patch(
                    "cera.pi_scene.qualification.time.perf_counter_ns",
                    side_effect=(0, post_end_ns, post_end_ns, poll_end_ns),
                ),
                patch("cera.pi_scene.qualification.time.sleep") as poll_sleep,
            ):
                result = FullModelQualificationRunner(
                    manifest=_manifest(),
                    runtime_root=runtime,
                    evidence_root=root / "evidence",
                ).run_phase(QualificationPhase.BACKEND, fixtures, client)

            first = result["results"][0]
            chain = first["transport_retry_chains"][0]
            action = chain["actions"][0]
            self.assertEqual(action["post_dispatch_duration_ms"], 4_200_000)
            self.assertEqual(action["status_reconciliation_duration_ms"], 500)
            self.assertEqual(first["latency_ms"], 4_440_501)
            self.assertEqual(client.retry_actions, 1)
            self.assertEqual(client.retry_status_reads, 3)
            poll_sleep.assert_called_once_with(0.25)

    def test_retry_exception_evidence_excludes_raw_identity_content(self) -> None:
        fixtures = load_qualification_fixtures(FIXTURES)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime = root / "runtime"
            evidence = root / "evidence"
            client = _FakePlannerTransportRetryClient(
                runtime,
                failure_fixture_id="backend-ordinary-01",
            )
            sentinels = (
                client.retry_id,
                f"/v1/cera/transport-retries/{client.retry_id}",
                "adult-prose-sentinel-Mia-Hana",
                r"C:\private\qualification\sentinel.log",
                '{"error":{"message":"raw-error-body-sentinel"}}',
            )
            with patch.object(
                client,
                "transport_retry_status",
                side_effect=OSError(" | ".join(sentinels)),
            ):
                with self.assertRaisesRegex(StateConflictError, "transport_io") as failure:
                    FullModelQualificationRunner(
                        manifest=_manifest(),
                        runtime_root=runtime,
                        evidence_root=evidence,
                    ).run_phase(QualificationPhase.BACKEND, fixtures, client)

            artifact_text = "\n".join(
                path.read_text(encoding="utf-8") for path in evidence.rglob("*") if path.is_file()
            )
            self.assertNotIn("failure_message", artifact_text)
            console_text = "".join(traceback.format_exception(failure.exception))
            self.assertNotIn("raw-error-body-sentinel", console_text)
            for sentinel in sentinels:
                self.assertNotIn(sentinel, artifact_text)
                self.assertNotIn(sentinel, console_text)
            result = json.loads((evidence / "BACKEND_RESULT.json").read_text(encoding="utf-8"))
            failed = result["results"][0]
            self.assertEqual(failed["failure_type"], "OSError")
            self.assertEqual(failed["failure_category"], "transport_io")
            self.assertRegex(failed["failure_sha256"], r"^[a-f0-9]{64}$")

    def test_manual_transport_retry_never_dispatches_for_adult_route(self) -> None:
        fixtures = load_qualification_fixtures(FIXTURES)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime = root / "runtime"
            client = _FakePlannerTransportRetryClient(
                runtime,
                failure_fixture_id="backend-adult-01",
            )
            with self.assertRaisesRegex(StateConflictError, "state_conflict"):
                FullModelQualificationRunner(
                    manifest=_manifest(),
                    runtime_root=runtime,
                    evidence_root=root / "evidence",
                ).run_phase(QualificationPhase.BACKEND, fixtures, client)
            self.assertEqual(client.retry_actions, 0)
            self.assertEqual(client.retry_status_reads, 0)

    def test_manual_transport_retry_rejects_nonplanner_failure_before_get(self) -> None:
        fixtures = load_qualification_fixtures(FIXTURES)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime = root / "runtime"
            client = _FakePlannerTransportRetryClient(
                runtime,
                failure_fixture_id="backend-ordinary-01",
                failure_owner="validator",
            )
            with self.assertRaisesRegex(StateConflictError, "state_conflict"):
                FullModelQualificationRunner(
                    manifest=_manifest(),
                    runtime_root=runtime,
                    evidence_root=root / "evidence",
                ).run_phase(QualificationPhase.BACKEND, fixtures, client)
            self.assertEqual(client.retry_actions, 0)
            self.assertEqual(client.retry_status_reads, 0)

    def test_pretransport_failure_is_evidenced_but_not_submitted_or_charged(self) -> None:
        fixtures = load_qualification_fixtures(FIXTURES)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime = root / "runtime"
            client = _FakePlannerTransportRetryClient(
                runtime,
                failure_fixture_id="backend-ordinary-01",
                failure_submitted=False,
            )
            result = FullModelQualificationRunner(
                manifest=_manifest(),
                runtime_root=runtime,
                evidence_root=root / "evidence",
            ).run_phase(QualificationPhase.BACKEND, fixtures, client)

            first = result["results"][0]
            chain = first["transport_retry_chains"][0]
            failed = chain["failed_call"]
            replacement = chain["replacement_call"]
            self.assertEqual(failed["terminal_state"], "pretransport_failed")
            self.assertFalse(failed["submitted"])
            self.assertFalse(failed["charged"])
            self.assertTrue(replacement["submitted"])
            self.assertTrue(replacement["charged"])
            self.assertNotEqual(
                failed["stored_thread_sha256"],
                replacement["stored_thread_sha256"],
            )
            self.assertEqual(first["sol_http_operations"], 2)
            self.assertEqual(first["sol_charged_operations"], 2)
            self.assertEqual(first["transport_retry_actions"], 1)
            self.assertEqual(client.retry_actions, 1)
            self.assertEqual(first["planner_latency"][0]["latency_class"], "cold_rehydration")
            self.assertEqual(first["planner_latency"][0]["planner_call_index"], 1)

    def test_post_validation_planner_failure_never_gets_transport_retry(self) -> None:
        fixtures = load_qualification_fixtures(FIXTURES)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime = root / "runtime"
            client = _FakePlannerTransportRetryClient(
                runtime,
                failure_fixture_id="backend-ordinary-01",
                failure_terminal_state="provider_completed_post_validation_failed",
            )
            with self.assertRaisesRegex(StateConflictError, "state_conflict"):
                FullModelQualificationRunner(
                    manifest=_manifest(),
                    runtime_root=runtime,
                    evidence_root=root / "evidence",
                ).run_phase(QualificationPhase.BACKEND, fixtures, client)
            self.assertEqual(client.retry_actions, 0)
            self.assertEqual(client.retry_status_reads, 0)

    def test_one_noncritical_rejection_allows_one_explicit_regenerate(self) -> None:
        fixtures = load_qualification_fixtures(FIXTURES)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime = root / "runtime"
            runner = FullModelQualificationRunner(
                manifest=_manifest(),
                runtime_root=runtime,
                evidence_root=root / "evidence",
            )
            client = _FakeQualificationClient(runtime, reject_first=True)
            result = runner.run_phase(QualificationPhase.BACKEND, fixtures, client)
            self.assertEqual(result["passed_fixtures"], 20)
            self.assertEqual(result["first_pass_accepted"], 19)
            self.assertEqual(result["explicit_regenerate_actions"], 1)
            self.assertEqual(client.regenerates, 1)

    def test_adult_continuation_uses_no_planner_and_its_review_id_regenerates(self) -> None:
        fixtures = load_qualification_fixtures(FIXTURES)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime = root / "runtime"
            runner = FullModelQualificationRunner(
                manifest=_manifest(),
                runtime_root=runtime,
                evidence_root=root / "evidence",
            )
            client = _FakeQualificationClient(
                runtime,
                reject_fixture_id="backend-adult-02",
            )
            result = runner.run_phase(QualificationPhase.BACKEND, fixtures, client)
            adult_two = next(
                value for value in result["results"] if value["fixture_id"] == "backend-adult-02"
            )
            self.assertEqual(adult_two["provider_operations"]["planner"], 0)
            self.assertFalse(adult_two["first_pass_accepted"])
            self.assertEqual(adult_two["explicit_regenerate_actions"], 1)
            self.assertEqual(client.regenerates, 1)

    def test_automatic_repair_uses_explicit_validator_ledger_parity(self) -> None:
        fixtures = load_qualification_fixtures(FIXTURES)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime = root / "runtime"
            runner = FullModelQualificationRunner(
                manifest=_manifest(),
                runtime_root=runtime,
                evidence_root=root / "evidence",
            )
            result = runner.run_phase(
                QualificationPhase.BACKEND,
                fixtures,
                _FakeQualificationClient(
                    runtime,
                    automatic_repair_fixture_id="backend-ordinary-02",
                ),
            )
            repaired = next(
                value for value in result["results"] if value["fixture_id"] == "backend-ordinary-02"
            )
            self.assertEqual(repaired["provider_operations"]["planner"], 1)
            self.assertEqual(repaired["provider_operations"]["writer"], 2)
            self.assertEqual(
                repaired["provider_operations"]["validator"],
                2,
            )
            self.assertEqual(repaired["provider_operations"]["recorder"], 1)
            self.assertEqual(repaired["sol_http_operations"], 3)
            self.assertEqual(repaired["deepseek_http_operations"], 3)
            self.assertFalse(repaired["first_pass_accepted"])
            self.assertEqual(repaired["automatic_repair_actions"], 1)
            self.assertEqual(result["first_pass_accepted"], 19)
            self.assertEqual(result["automatic_repair_actions"], 1)
            self.assertEqual(result["explicit_regenerate_actions"], 0)

    def test_sillytavern_campaign_restart_retains_session_and_ledger_prefix(self) -> None:
        fixtures = load_qualification_fixtures(FIXTURES)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime_a = root / "runtime-a"
            runtime_b = root / "runtime-b"
            runner = FullModelQualificationRunner(
                manifest=_manifest(),
                runtime_root=runtime_a,
                evidence_root=root / "evidence",
            )
            client = _FakeQualificationClient(
                runtime_a,
                planner_durations_ms=(420_000, 180_000, 120_000, 240_000, 60_000),
            )
            campaign = runner.start_phase(QualificationPhase.SILLYTAVERN, fixtures)
            campaign.run_segment(
                client=client,
                runtime_root=runtime_a,
                turn_count=5,
            )
            runtime_b.mkdir()
            for name in (
                "SOL_PROVIDER_CALLS.jsonl",
                "DEEPSEEK_PROVIDER_OPERATIONS.jsonl",
            ):
                shutil.copy2(runtime_a / name, runtime_b / name)
            client.runtime_root = runtime_b
            campaign.run_segment(
                client=client,
                runtime_root=runtime_b,
                turn_count=5,
                restarted=True,
            )
            result = campaign.finish()
            self.assertEqual(result["passed_fixtures"], 10)
            self.assertEqual(result["restart_count"], 1)
            self.assertEqual(result["retained_conversation_messages"], 20)
            self.assertEqual(client.calls, 10)
            post_restart = [
                observation
                for row in result["results"]
                if row["turn_index"] > 5
                for observation in row["planner_latency"]
            ]
            self.assertTrue(post_restart)
            self.assertTrue(all(value["cold_start"] is False for value in post_restart))
            self.assertTrue(
                all(value["rehydrated_after_thread_rotation"] is False for value in post_restart)
            )
            self.assertEqual(post_restart[0]["planner_call_index"], 5)
            self.assertEqual(post_restart[0]["planner_thread_call_index"], 5)
            retained_samples = [
                observation["duration_ms"]
                for row in result["results"]
                for observation in row["planner_latency"]
                if observation["cold_start"] is False
            ]
            self.assertEqual(
                result["planner_latency_summary"]["average_retained_latency_ms"],
                round(sum(retained_samples) / len(retained_samples)),
            )
            self.assertEqual(
                result["planner_latency_summary"]["retained_planner_calls"],
                len(retained_samples),
            )

    def test_manifest_verification_fails_closed_after_artifact_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            artifact = root / "artifact.txt"
            artifact.write_text("one", encoding="utf-8")
            fixture = root / "fixtures.json"
            fixture.write_bytes(FIXTURES.read_bytes())
            manifest = build_qualification_manifest(
                repository_root=root,
                qualification_id="qualification-test-20260809",
                source_commit="a" * 40,
                source_tree="b" * 40,
                fixture_path=fixture,
                repository_artifacts={"test": (Path("artifact.txt"), Path("fixtures.json"))},
                external_artifacts={"external": (artifact,)},
            )
            verify_qualification_artifacts(manifest, repository_root=root)
            artifact.write_text("two", encoding="utf-8")
            with self.assertRaisesRegex(Exception, "artifact changed"):
                verify_qualification_artifacts(manifest, repository_root=root)

    def test_external_artifacts_bind_exact_codex_runtime_packages(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            isolated = root / "isolated"
            isolated.mkdir()
            (isolated / "CERA_QUALIFICATION_ISOLATED_COPY_MANIFEST.json").write_text(
                "{}",
                encoding="utf-8",
            )
            package_pairs: list[tuple[Path, Path]] = []
            for index in range(3):
                package_root = root / f"package-{index}"
                metadata_root = root / f"package-{index}.dist-info"
                package_root.mkdir()
                metadata_root.mkdir()
                package_pairs.append((package_root, metadata_root))
            with (
                patch.object(
                    entrypoint,
                    "_installed_distribution_artifacts",
                    side_effect=package_pairs,
                ) as installed,
                patch(
                    "scripts.run_pi_scene_full_model_qualification.shutil.which",
                    return_value=str(root / "node.exe"),
                ),
            ):
                artifacts = entrypoint._external_artifacts(isolated)

            self.assertEqual(
                artifacts["frozen_python_interpreter"],
                (Path(sys.executable).resolve(),),
            )
            runtime_paths = artifacts["provider_runtime_tools"]
            for package_root, metadata_root in package_pairs:
                self.assertIn(package_root, runtime_paths)
                self.assertIn(metadata_root, runtime_paths)
            self.assertEqual(
                installed.call_args_list,
                [
                    call("openai_codex", "openai-codex"),
                    call("codex_cli_bin", "openai-codex-cli-bin"),
                    call("mcp", "mcp"),
                ],
            )

    def test_live_interpreter_must_match_frozen_manifest_binding(self) -> None:
        current = Path(sys.executable).resolve()
        manifest = {
            "artifact_categories": {
                "frozen_python_interpreter": [
                    {
                        "path": str(current),
                        "location": "external",
                        "bytes": 1,
                        "sha256": "a" * 64,
                    }
                ]
            }
        }
        entrypoint._assert_frozen_python_interpreter(manifest)
        manifest["artifact_categories"]["frozen_python_interpreter"][0]["path"] = str(
            current.with_name("other-python.exe")
        )
        with self.assertRaisesRegex(Exception, "not using the frozen Python interpreter"):
            entrypoint._assert_frozen_python_interpreter(manifest)

    def test_live_checks_frozen_interpreter_before_artifact_verification(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = {
                "source_commit": "a" * 40,
                "source_tree": "b" * 40,
            }
            with (
                patch.object(entrypoint, "load_qualification_manifest", return_value=manifest),
                patch.object(entrypoint, "_assert_clean_exact_repository"),
                patch.object(
                    entrypoint,
                    "_assert_frozen_python_interpreter",
                    side_effect=RuntimeError("interpreter blocked"),
                ),
                patch.object(entrypoint, "verify_qualification_artifacts") as verify,
            ):
                with self.assertRaisesRegex(RuntimeError, "interpreter blocked"):
                    entrypoint.live(output_root=root, fixture_path=FIXTURES)
            verify.assert_not_called()

    def test_freeze_binds_exact_disposable_tree_before_live(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = _fake_sillytavern_source(root)
            output = root / "qualification"
            with (
                patch.object(entrypoint, "_preflight_runtime_path_budget"),
                patch.object(entrypoint, "_assert_clean_exact_repository"),
                patch.object(
                    entrypoint,
                    "_git",
                    side_effect=("a" * 40, "b" * 40),
                ),
                patch.object(
                    entrypoint,
                    "_repository_artifacts",
                    return_value={
                        "fixture": (FIXTURES.relative_to(ROOT),),
                    },
                ),
                patch.object(
                    entrypoint,
                    "_external_artifacts",
                    side_effect=lambda isolated: {
                        "provider_runtime_tools": (Path(shutil.which("node") or "missing-node"),),
                        "sillytavern_executable_tree_binding": (
                            isolated / "CERA_QUALIFICATION_ISOLATED_COPY_MANIFEST.json",
                        ),
                    },
                ),
            ):
                manifest = entrypoint.freeze(
                    output_root=output,
                    fixture_path=FIXTURES,
                    qualification_id="qualification-freeze-20260809",
                    sillytavern_source=source,
                )
            verify_qualification_artifacts(manifest, repository_root=ROOT)
            isolated = output / "isolated_sillytavern"
            frozen = verify_qualification_sillytavern(isolated)
            self.assertEqual(
                frozen["metadata_bridge_contract"],
                "cera.full_model.capture_function.v1",
            )
            self.assertEqual(
                frozen["transport_retry_server_bridge_contract"],
                "cera.transport_retry.closed_error_projection.v1",
            )
            self.assertEqual(
                frozen["transport_retry_terminal_ui_contract"],
                {
                    "schema_version": ("cera.pi_scene.qualification_critical_provider_stage_ui.v1"),
                    "projection_key": "critical_provider_stage_failure",
                    "severity": "critical",
                    "provider_required": True,
                    "stage_required": True,
                    "maximum_total_provider_attempts": 3,
                    "maximum_retry_actions": 2,
                    "retry_action_enabled": False,
                    "collapsible": True,
                    "display_fields": [
                        "severity",
                        "provider",
                        "model_family",
                        "stage",
                        "maximum_attempts",
                        "attempts_total",
                        "retries_consumed",
                        "final_failure_class",
                    ],
                    "hash_safe_only": True,
                    "raw_provider_or_story_prose_allowed": False,
                },
            )
            check = entrypoint.provider_free_check(
                fixture_path=FIXTURES,
                manifest_path=output / "QUALIFICATION_MANIFEST.json",
            )
            self.assertEqual(
                check["isolated_sillytavern_manifest_sha256"],
                frozen["manifest_sha256"],
            )
            self.assertRegex(check["staged_node_suite_proof_sha256"], r"^[a-f0-9]{64}$")
            (isolated / "public/scripts/openai.js").write_text(
                "tampered",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(Exception, "metadata bridge changed"):
                verify_qualification_sillytavern(isolated)

    def test_freeze_rejects_an_overlong_runtime_root_before_mutation(self) -> None:
        entrypoint._preflight_runtime_path_budget(Path("D:/Cera/qv5"))
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / ("q" * 160)
            with (
                patch.object(entrypoint, "_assert_clean_exact_repository") as clean,
                patch.object(entrypoint, "stage_qualification_sillytavern") as stage,
            ):
                with self.assertRaisesRegex(StateConflictError, "legacy path budget"):
                    entrypoint.freeze(
                        output_root=output,
                        fixture_path=FIXTURES,
                        qualification_id="qualification-overlong-root",
                        sillytavern_source=Path(temporary) / "sillytavern",
                    )
            clean.assert_not_called()
            stage.assert_not_called()
            self.assertFalse(output.exists())

    def test_qualification_isolation_installs_only_repository_cera_integrations(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = _fake_sillytavern_source(root)
            proxy = root / "proxy"
            extension = root / "extension"
            proxy.mkdir()
            extension.mkdir()
            (proxy / "index.js").write_text(
                "router.get('/v1/cera/transport-retries/:retryId');"
                "cera.pi_scene.transport_retry_status.v1",
                encoding="utf-8",
            )
            (proxy / "package.json").write_text("{}", encoding="utf-8")
            (proxy / "test.mjs").write_text(
                "import test from 'node:test'; test('proxy', () => {});",
                encoding="utf-8",
            )
            (extension / "index.js").write_text(
                "cera_transport_retry_receipts_v1;"
                "restoreTransportRetryForCurrentChat({ reconcile: true });"
                "transport_retry_completion",
                encoding="utf-8",
            )
            (extension / "metadata-panel.test.mjs").write_text(
                "import test from 'node:test'; test('extension', () => {});",
                encoding="utf-8",
            )
            target = root / "target"
            manifest = stage_qualification_sillytavern(
                source,
                target,
                repository_proxy_root=proxy,
                repository_extension_root=extension,
            )
            verify_qualification_sillytavern(target)
            self.assertTrue(manifest["only_repository_cera_integrations"])
            self.assertEqual(
                manifest["review_loopback_override_env"],
                "CERA_REVIEW_LOOPBACK_ROOT",
            )
            self.assertTrue(manifest["installed_default_port_5101_untouched"])
            self.assertFalse((target / "data" / "private.json").exists())
            self.assertFalse(
                (target / "public/scripts/extensions/third-party/unapproved/index.js").exists()
            )
            self.assertTrue((target / "plugins/cera-review-proxy/index.js").is_file())
            staged_proxy = (target / "plugins/cera-review-proxy/index.js").read_text(
                encoding="utf-8"
            )
            self.assertIn("/v1/cera/transport-retries/:retryId", staged_proxy)
            self.assertIn("cera.pi_scene.transport_retry_status.v1", staged_proxy)
            staged_extension = (
                target / "public/scripts/extensions/third-party/cera-creator-review/index.js"
            ).read_text(encoding="utf-8")
            self.assertIn("cera_transport_retry_receipts_v1", staged_extension)
            self.assertIn("restoreTransportRetryForCurrentChat", staged_extension)
            self.assertIn("transport_retry_completion", staged_extension)
            openai = (target / "public/scripts/openai.js").read_text(encoding="utf-8")
            self.assertIn("window.ceraCaptureCompletionMetadata(data.cera)", openai)
            self.assertEqual(openai.count("window.ceraCaptureTransportFailure(data)"), 1)
            self.assertNotIn("data?.cera?.provisional", openai)
            backend = (target / "src/endpoints/backends/chat-completions.js").read_text(
                encoding="utf-8"
            )
            self.assertIn("CERA_PROVIDER_TRANSPORT_FAILED", backend)
            self.assertIn("ceraTransportRetryError", backend)
            self.assertNotIn("debug_log_path", backend)
            manifest_path = target / "CERA_QUALIFICATION_ISOLATED_COPY_MANIFEST.json"
            changed_manifest = deepcopy(manifest)
            changed_manifest["transport_retry_terminal_ui_contract"]["maximum_retry_actions"] = 3
            unsigned = {
                key: value for key, value in changed_manifest.items() if key != "manifest_sha256"
            }
            changed_manifest["manifest_sha256"] = canonical_sha256(unsigned)
            manifest_path.write_bytes(canonical_bytes(changed_manifest) + b"\n")
            with self.assertRaisesRegex(StateConflictError, "transport retry bridge changed"):
                verify_qualification_sillytavern(target)
            manifest_path.write_bytes(canonical_bytes(manifest) + b"\n")
            (target / "src/endpoints/backends/chat-completions.js").write_text(
                "tampered",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(StateConflictError, "transport retry bridge changed"):
                verify_qualification_sillytavern(target)

    def test_staged_repository_node_suites_execute_from_hash_bound_copy(self) -> None:
        node = shutil.which("node")
        self.assertIsNotNone(node)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "target"
            manifest = stage_qualification_sillytavern(
                _fake_sillytavern_source(root),
                target,
                repository_proxy_root=(ROOT / "integrations/sillytavern/cera-review-proxy-plugin"),
                repository_extension_root=(
                    ROOT / "integrations/sillytavern/creator-review-extension"
                ),
            )
            proof = run_staged_sillytavern_node_suites(
                target,
                node_executable=Path(str(node)),
            )
            self.assertEqual(proof["suite_count"], 2)
            self.assertEqual(proof["provider_calls"], 0)
            self.assertEqual(proof["return_code"], 0)
            self.assertEqual(
                proof["suite_source_sha256"],
                manifest["staged_node_suite_source_sha256"],
            )
            self.assertEqual(
                proof["proof_sha256"],
                canonical_sha256(
                    {key: value for key, value in proof.items() if key != "proof_sha256"}
                ),
            )

    def test_staged_proxy_projects_full_exhaustion_for_qualification(self) -> None:
        node = shutil.which("node")
        self.assertIsNotNone(node)
        fixtures = load_qualification_fixtures(FIXTURES)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "target"
            stage_qualification_sillytavern(
                _fake_sillytavern_source(root),
                target,
                repository_proxy_root=(ROOT / "integrations/sillytavern/cera-review-proxy-plugin"),
                repository_extension_root=(
                    ROOT / "integrations/sillytavern/creator-review-extension"
                ),
            )
            runtime = root / "runtime"
            client = _FakePlannerTransportRetryClient(
                runtime,
                failure_fixture_id="backend-ordinary-01",
                retry_failures_before_success=2,
            )
            critical = client._terminal_critical_projection()
            full = client._exhausted_response(
                retry_id=client.retry_ids[1],
                critical=critical,
            )
            module_url = (target / "plugins" / "cera-review-proxy" / "index.js").resolve().as_uri()
            source = (
                f"import {{ projectTransportRetryPayload }} from {json.dumps(module_url)};"
                "const value = JSON.parse(process.argv[1]);"
                "process.stdout.write(JSON.stringify(projectTransportRetryPayload(value)));"
            )
            completed = subprocess.run(
                (str(node), "--input-type=module", "--eval", source, json.dumps(full.body)),
                cwd=target,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            projected = json.loads(completed.stdout)
            self.assertEqual(
                set(projected["error"]),
                {
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
                },
            )
            self.assertEqual(
                projected["error"]["message"],
                "CERA stopped after three failed attempts at one provider stage.",
            )
            self.assertEqual(projected["error"]["critical_provider_stage_failure"], critical)
            projected_text = json.dumps(projected, sort_keys=True)
            for sentinel in (
                "RAW PROVIDER FAILURE PROSE",
                "PRIVATE PROVIDER OUTPUT",
                "provider-output.md",
                client.request_id,
                "trace:" + "8" * 32,
            ):
                self.assertNotIn(sentinel, projected_text)

            relay_response = ClientResponseV1(
                transport="isolated-sillytavern-proxy",
                path=f"/v1/cera/transport-retries/{client.retry_ids[1]}",
                status_code=503,
                duration_ms=360_000,
                body=projected,
            )
            with (
                patch.object(client, "_exhausted_response", return_value=relay_response),
                self.assertRaisesRegex(
                    StateConflictError,
                    "provider_stage_attempts_exhausted",
                ),
            ):
                FullModelQualificationRunner(
                    manifest=_manifest(),
                    runtime_root=runtime,
                    evidence_root=root / "evidence",
                ).run_phase(QualificationPhase.BACKEND, fixtures, client)

    def test_fake_retry_relay_upstream_requires_auth_and_two_empty_posts(self) -> None:
        token = "relay-token-" + "x" * 32
        retry_id = "retry-" + "1" * 64
        request_id = "request-" + "2" * 64
        effect_sha256 = "3" * 64
        port = entrypoint._available_port_excluding(5101)
        upstream = entrypoint._FakeRelayUpstream(
            port=port,
            decline_review_id="review-0123456789abcdef0123456789ab",
            regenerate_review_id="review-fedcba9876543210fedcba987654",
            authorization_token=token,
            retry_id=retry_id,
            retry_request_id=request_id,
            retry_effect_proof_sha256=effect_sha256,
        )
        upstream.start()
        try:
            client = entrypoint.DirectCeraQualificationClient(
                base_url=f"http://127.0.0.1:{port}",
                token=token,
            )
            retry_1_eligible = client.transport_retry_status(retry_id=retry_id)
            retry_1_action = client.retry_transport(retry_id=retry_id)
            retry_1_terminal = client.transport_retry_status(retry_id=retry_id)
            successor_id = retry_1_terminal.body["superseded_by_retry_id"]
            retry_2_eligible = client.transport_retry_status(retry_id=successor_id)
            retry_2_action = client.retry_transport(retry_id=successor_id)
            retry_2_terminal = client.transport_retry_status(retry_id=successor_id)
            proof = upstream.retry_proof()
        finally:
            upstream.close()
        self.assertEqual(retry_1_eligible.body["state"], "eligible")
        self.assertEqual(retry_1_action.status_code, 500)
        self.assertEqual(retry_1_terminal.body["state"], "superseded")
        self.assertEqual(retry_2_eligible.body["state"], "eligible")
        self.assertEqual(retry_2_action.status_code, 503)
        self.assertEqual(
            set(retry_2_action.body["error"]),
            {
                "schema_version",
                "error_code",
                "message",
                "trace_id",
                "request_id",
                "branch_id",
                "generation_id",
                "stage",
                "story_state_committed",
                "retry_mode",
                "details",
                "fallback_used",
                "provider_operation_submitted",
                "accepted_state_changed",
                "next_action",
                "debug_log_path",
                "retry_transport_enabled",
                "critical_provider_stage_failure",
            },
        )
        self.assertEqual(retry_2_action.body["error"]["request_id"], request_id)
        self.assertEqual(retry_2_terminal.body["state"], "attempts_exhausted")
        self.assertFalse(retry_2_terminal.body["retry_transport_enabled"])
        self.assertNotIn("transport_retry", retry_2_terminal.body)
        self.assertEqual(proof["authenticated_gets"], 4)
        self.assertEqual(proof["authenticated_posts"], 2)
        self.assertEqual(proof["exact_empty_posts"], 2)
        self.assertEqual(proof["provider_calls"], 0)

    def test_provider_free_entrypoint_reports_exact_campaign_counts(self) -> None:
        result = entrypoint.provider_free_check(fixture_path=FIXTURES)
        self.assertEqual(result["provider_calls"], 0)
        self.assertEqual(result["backend"], 20)
        self.assertEqual(result["sillytavern"], 10)
        self.assertEqual(result["maximum_manual_transport_retry_actions_per_prompt"], 2)
        self.assertEqual(result["maximum_total_planner_provider_attempts"], 3)
        self.assertEqual(result["automatic_transport_retry_actions"], 0)


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    with path.open("ab") as stream:
        stream.write(canonical_bytes(payload) + b"\n")


def _jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _fake_sillytavern_source(root: Path) -> Path:
    source = root / "source"
    for directory in (
        "default",
        "node_modules",
        "public/scripts/extensions/third-party/unapproved",
        "src/endpoints/backends",
        "data",
        "plugins/unapproved",
    ):
        (source / directory).mkdir(parents=True, exist_ok=True)
    for relative, content in {
        "server.js": "server",
        "package.json": "{}",
        "package-lock.json": "{}",
        "default/config.yaml": "enableServerPlugins: false\n",
        "node_modules/module.js": "module",
        "public/index.html": "index",
        "public/scripts/openai.js": (
            "function tryParseStreamingError(response, data, quiet) {\n"
            "        if (data.error) {\n"
            "            !quiet && toastr.error(data.error.message || response.statusText, "
            "'Chat Completion API');\n"
            "        }\n"
            "}\n"
            "async function request() {\n"
            "        if (data?.cera?.provisional && "
            "data.cera.provisional_review_id) {\n"
            "            const queue = "
            "Array.isArray(window.ceraCompletionMetadataQueue)\n"
            "                ? window.ceraCompletionMetadataQueue\n"
            "                : (window.ceraCompletionMetadataQueue = []);\n"
            "            queue.push(structuredClone(data.cera));\n"
            "            if (queue.length > 8) "
            "queue.splice(0, queue.length - 8);\n"
            "            window.dispatchEvent(new "
            "CustomEvent('cera:completion-metadata', {\n"
            "                detail: data.cera,\n"
            "            }));\n"
            "        }\n"
            "        if (data.error) {\n"
            "            const message = data.error.message || response.statusText || "
            "t`Unknown error`;\n"
            "        }\n"
            "}\n"
        ),
        "public/scripts/extensions/third-party/unapproved/index.js": "bad",
        "src/app.js": "app",
        "src/endpoints/backends/chat-completions.js": (
            "async function send(request, response) {\n"
            "            const message = fetchResponse.statusText || "
            "'Unknown error occurred';\n"
            "            const quota_error = fetchResponse.status === 429 && "
            "errorData?.error?.type === 'insufficient_quota';\n"
            "            console.error('Chat completion request error: ', message, "
            "responseText);\n\n"
            "            if (!response.headersSent) {\n"
            "                response.send({ error: { message }, quota_error: quota_error });\n"
            "            } else if (!response.writableEnded) {\n"
            "                response.write(responseText);\n"
            "            }\n"
            "}\n"
        ),
        "data/private.json": "private",
        "plugins/unapproved/index.js": "bad",
    }.items():
        (source / relative).write_text(content, encoding="utf-8")
    return source


if __name__ == "__main__":
    unittest.main()
