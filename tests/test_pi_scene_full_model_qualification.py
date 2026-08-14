from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from collections.abc import Callable
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import call, patch

from cera.errors import ContractValidationError, StateConflictError
from cera.pi_scene.ordinary_rejection_policy import (
    build_ordinary_policy_acceptance_audit_from_bindings,
    ordinary_policy_acceptance_projection,
)
from cera.pi_scene.qualification import (
    DEEPSEEK_HTTP_OPERATION_CEILING,
    DEEPSEEK_PER_INVOCATION_CEILING,
    PROVIDER_STAGE_RETRY_STATUS_TIMEOUT_SECONDS,
    QUALIFICATION_ACTION_BUDGETS,
    QUALIFICATION_COMPLETE_GENERATION_CEILINGS,
    QUALIFICATION_EXECUTION_POLICY,
    QUALIFICATION_HTTP_HARD_TIMEOUT_SECONDS,
    QUALIFICATION_MANIFEST_SCHEMA,
    QUALIFICATION_MANIFEST_SCHEMA_V5,
    QUALIFICATION_MANIFEST_SCHEMA_V6,
    QUALIFICATION_MANIFEST_SCHEMA_V7,
    QUALIFICATION_MANIFEST_SCHEMA_V8,
    QUALIFICATION_MANIFEST_SCHEMA_V9,
    QUALIFICATION_MANIFEST_SCHEMA_V10,
    QUALIFICATION_MANIFEST_SCHEMA_V11,
    QUALIFICATION_MANIFEST_SCHEMA_V12,
    QUALIFICATION_MANIFEST_SCHEMA_V13,
    QUALIFICATION_MANIFEST_SCHEMA_V14,
    QUALIFICATION_MANIFEST_SCHEMA_V15,
    QUALIFICATION_MANIFEST_SCHEMA_V16,
    QUALIFICATION_MANIFEST_SCHEMA_V17,
    QUALIFICATION_MANIFEST_SCHEMA_V18,
    QUALIFICATION_MANIFEST_SCHEMA_V19,
    QUALIFICATION_MANIFEST_SCHEMA_V20,
    QUALIFICATION_MANIFEST_SCHEMA_V21,
    QUALIFICATION_MANIFEST_SCHEMA_V22,
    QUALIFICATION_MANIFEST_SCHEMA_V23,
    QUALIFICATION_MANIFEST_SCHEMA_V24,
    QUALIFICATION_MANIFEST_SCHEMA_V25,
    QUALIFICATION_MANIFEST_SCHEMA_V26,
    QUALIFICATION_MANIFEST_SCHEMA_V27,
    QUALIFICATION_MANIFEST_SCHEMA_V28,
    QUALIFICATION_MAX_SEQUENTIAL_PROVIDER_STAGES,
    QUALIFICATION_PLANNER_REASONING_EFFORT,
    QUALIFICATION_PROVIDER_STAGE_HARD_TIMEOUT_SECONDS,
    QUALIFICATION_RESULT_SCHEMA,
    SOL_FAMILY_CEILING,
    TERRA_CEILING,
    USER_AUTHORIZED_CODEX_OPERATION_CEILING,
    USER_AUTHORIZED_DEEPSEEK_OPERATION_CEILING,
    ClientResponseV1,
    ManualActionRequiredError,
    ProviderLedgerDeltaV1,
    QualificationFixtureV1,
    QualificationFixtureV2,
    QualificationFixtureV3,
    QualificationFixtureV4,
    QualificationFixtureV5,
    QualificationFixtureV6,
    QualificationFixtureV7,
    QualificationFixtureV8,
    QualificationFixtureV9,
    QualificationFixtureV10,
    QualificationFixtureV11,
    QualificationFixtureV12,
    QualificationFixtureV13,
    QualificationFixtureV14,
    QualificationFixtureV15,
    QualificationFixtureV16,
    QualificationFixtureV17,
    QualificationFixtureV18,
    QualificationFixtureV19,
    QualificationFixtureV20,
    QualificationFixtureV21,
    QualificationFixtureV22,
    QualificationFixtureV23,
    QualificationFixtureV24,
    QualificationFixtureV25,
    QualificationManualActionAuthorizationV1,
    QualificationManualActionRequestV1,
    QualificationPhase,
    QualificationRoute,
    _provider_operation_records,
    build_qualification_manifest,
    load_qualification_fixtures,
    qualification_fixture_manifest_metadata,
    qualification_request_payload,
    validate_qualification_manifest,
    verify_qualification_artifacts,
)
from cera.pi_scene.qualification import (
    FullModelQualificationRunner as _ProductionFullModelQualificationRunner,
)
from cera.pi_scene.qualification_isolation import (
    run_staged_sillytavern_node_suites,
    stage_qualification_sillytavern,
    verify_qualification_sillytavern,
)
from cera.pi_scene.world_runtime import DEFAULT_INITIAL_PRESENT_CHARACTER_IDS
from cera.serialization import bytes_sha256, canonical_bytes, canonical_sha256, text_sha256
from scripts import run_pi_scene_full_model_qualification as entrypoint

ROOT = Path(__file__).parents[1]
LEGACY_FIXTURES = ROOT / "evaluation" / "fixtures" / "pi_scene_full_model_qualification_v1.json"
HISTORICAL_FIXTURES_V2 = (
    ROOT / "evaluation" / "fixtures" / "pi_scene_full_model_qualification_v2.json"
)
HISTORICAL_FIXTURES_V3 = (
    ROOT / "evaluation" / "fixtures" / "pi_scene_full_model_qualification_v3.json"
)
HISTORICAL_FIXTURES_V4 = (
    ROOT / "evaluation" / "fixtures" / "pi_scene_full_model_qualification_v4.json"
)
HISTORICAL_FIXTURES_V5 = (
    ROOT / "evaluation" / "fixtures" / "pi_scene_full_model_qualification_v5.json"
)
HISTORICAL_FIXTURES_V6 = (
    ROOT / "evaluation" / "fixtures" / "pi_scene_full_model_qualification_v6.json"
)
HISTORICAL_FIXTURES_V7 = (
    ROOT / "evaluation" / "fixtures" / "pi_scene_full_model_qualification_v7.json"
)
HISTORICAL_FIXTURES_V8 = (
    ROOT / "evaluation" / "fixtures" / "pi_scene_full_model_qualification_v8.json"
)
HISTORICAL_FIXTURES_V9 = (
    ROOT / "evaluation" / "fixtures" / "pi_scene_full_model_qualification_v9.json"
)
HISTORICAL_FIXTURES_V10 = (
    ROOT / "evaluation" / "fixtures" / "pi_scene_full_model_qualification_v10.json"
)
HISTORICAL_FIXTURES_V11 = (
    ROOT / "evaluation" / "fixtures" / "pi_scene_full_model_qualification_v11.json"
)
HISTORICAL_FIXTURES_V12 = (
    ROOT / "evaluation" / "fixtures" / "pi_scene_full_model_qualification_v12.json"
)
HISTORICAL_FIXTURES_V13 = (
    ROOT / "evaluation" / "fixtures" / "pi_scene_full_model_qualification_v13.json"
)
HISTORICAL_FIXTURES_V14 = (
    ROOT / "evaluation" / "fixtures" / "pi_scene_full_model_qualification_v14.json"
)
HISTORICAL_FIXTURES_V15 = (
    ROOT / "evaluation" / "fixtures" / "pi_scene_full_model_qualification_v15.json"
)
HISTORICAL_FIXTURES_V16 = (
    ROOT / "evaluation" / "fixtures" / "pi_scene_full_model_qualification_v16.json"
)
HISTORICAL_FIXTURES_V17 = (
    ROOT / "evaluation" / "fixtures" / "pi_scene_full_model_qualification_v17.json"
)
HISTORICAL_FIXTURES_V18 = (
    ROOT / "evaluation" / "fixtures" / "pi_scene_full_model_qualification_v18.json"
)
HISTORICAL_FIXTURES_V19 = (
    ROOT / "evaluation" / "fixtures" / "pi_scene_full_model_qualification_v19.json"
)
HISTORICAL_FIXTURES_V20 = (
    ROOT / "evaluation" / "fixtures" / "pi_scene_full_model_qualification_v20.json"
)
HISTORICAL_FIXTURES_V21 = (
    ROOT / "evaluation" / "fixtures" / "pi_scene_full_model_qualification_v21.json"
)
HISTORICAL_FIXTURES_V22 = (
    ROOT / "evaluation" / "fixtures" / "pi_scene_full_model_qualification_v22.json"
)
HISTORICAL_FIXTURES_V23 = (
    ROOT / "evaluation" / "fixtures" / "pi_scene_full_model_qualification_v23.json"
)
HISTORICAL_FIXTURES_V24 = (
    ROOT / "evaluation" / "fixtures" / "pi_scene_full_model_qualification_v24.json"
)
FIXTURES = ROOT / "evaluation" / "fixtures" / "pi_scene_full_model_qualification_v25.json"
_ORIGINAL_PROVIDER_DISPATCH_DISABLED = os.environ.get("CERA_PROVIDER_DISPATCH_DISABLED")


def setUpModule() -> None:
    os.environ["CERA_PROVIDER_DISPATCH_DISABLED"] = "1"


def tearDownModule() -> None:
    if _ORIGINAL_PROVIDER_DISPATCH_DISABLED is None:
        os.environ.pop("CERA_PROVIDER_DISPATCH_DISABLED", None)
    else:
        os.environ["CERA_PROVIDER_DISPATCH_DISABLED"] = _ORIGINAL_PROVIDER_DISPATCH_DISABLED


def _manifest() -> dict[str, Any]:
    fixture_metadata = qualification_fixture_manifest_metadata(
        load_qualification_fixtures(FIXTURES)
    )
    body = {
        "schema_version": QUALIFICATION_MANIFEST_SCHEMA,
        "qualification_id": "qualification-fake-20260809",
        "source_commit": "a" * 40,
        "source_tree": "b" * 40,
        "route_model": "cera-alpha",
        "profile_id": "cera.pi_scene.lean.v1",
        "fixture_set_sha256": "c" * 64,
        **fixture_metadata,
        "spent_manifest_sha256s": [],
        "spent_fixture_set_sha256s": [],
        "spent_novelty_ids": [],
        "spent_source_sha256s": [],
        "fixture_counts": {
            "backend": {"ordinary": 10, "adult": 10},
            "sillytavern": {"ordinary": 5, "adult": 5},
        },
        "provider_ceilings": {
            "sol": SOL_FAMILY_CEILING,
            "deepseek_http_operations": DEEPSEEK_HTTP_OPERATION_CEILING,
            "deepseek_per_invocation": DEEPSEEK_PER_INVOCATION_CEILING,
            "terra": TERRA_CEILING,
            "user_authorized_codex_operations": (USER_AUTHORIZED_CODEX_OPERATION_CEILING),
            "user_authorized_deepseek_operations": (USER_AUTHORIZED_DEEPSEEK_OPERATION_CEILING),
        },
        "execution_policy": deepcopy(QUALIFICATION_EXECUTION_POLICY),
        "artifact_categories": {"test": []},
    }
    return {**body, "manifest_sha256": canonical_sha256(body)}


class _ProviderFreeSimulatedManualActionAuthorizer:
    def __init__(self) -> None:
        self.requests: list[QualificationManualActionRequestV1] = []
        self.consumed_checkpoints: list[str] = []

    def authorize(
        self,
        request: QualificationManualActionRequestV1,
    ) -> QualificationManualActionAuthorizationV1:
        self.requests.append(request)
        return QualificationManualActionAuthorizationV1.approve(
            request,
            authorization_source="provider_free_simulation",
            authorization_id=f"provider-free-simulation-{len(self.requests)}",
        )

    def mark_consumed(
        self,
        request: QualificationManualActionRequestV1,
        authorization: QualificationManualActionAuthorizationV1,
    ) -> None:
        if request.checkpoint_sha256 in self.consumed_checkpoints:
            raise AssertionError("provider-free authorization was consumed twice")
        self.consumed_checkpoints.append(request.checkpoint_sha256)


def FullModelQualificationRunner(**kwargs: Any) -> _ProductionFullModelQualificationRunner:
    """Existing fake campaigns opt into an explicitly provider-free authorizer."""

    if "manual_action_authorizer" in kwargs:
        raise AssertionError("provider-free runner helper received an explicit authorization seam")
    return _ProductionFullModelQualificationRunner(
        **kwargs,
        manual_action_authorizer=_ProviderFreeSimulatedManualActionAuthorizer(),
    )


class _FakeQualificationClient:
    INITIAL_COMPLETION_DURATION_MS = 10
    REVIEW_POLL_DURATION_MS = 2
    TERMINAL_DECISION_DURATION_MS = 1

    def __init__(
        self,
        runtime_root: Path,
        *,
        reject_first: bool = False,
        reject_fixture_id: str | None = None,
        standing_policy_fixture_id: str | None = None,
        automatic_repair_fixture_id: str | None = None,
        recording_repair_fixture_id: str | None = None,
        planner_durations_ms: tuple[int, ...] = (),
        planner_session_identities: tuple[str, ...] = (),
    ) -> None:
        self.runtime_root = runtime_root
        self.runtime_root.mkdir(parents=True, exist_ok=True)
        self.reject_first = reject_first
        self.reject_fixture_id = reject_fixture_id
        self.standing_policy_fixture_id = standing_policy_fixture_id
        self.automatic_repair_fixture_id = automatic_repair_fixture_id
        self.recording_repair_fixture_id = recording_repair_fixture_id
        self.planner_durations_ms = planner_durations_ms
        self.planner_session_identities = planner_session_identities
        self.planner_duration_index = 0
        self.rejected = False
        self.calls = 0
        self.regenerates = 0
        self.session_id: str | None = None
        self.reviews: dict[str, dict[str, Any]] = {}
        self.terminal_decisions: dict[str, dict[str, Any]] = {}
        self.review_actions: list[tuple[str, dict[str, Any]]] = []
        self.review_reads: list[str] = []
        self.terminal_decision_reads: list[str] = []

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
        self.assert_equal(value["cera_review_mode"], "automatic")
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
        if fixture.expected_route is QualificationRoute.ORDINARY:
            rejected = (reject_now or self.automatic_repair_fixture_id == fixture.fixture_id) and (
                not self.rejected
            )
            standing_policy = self.standing_policy_fixture_id == fixture.fixture_id and not rejected
            recording_repair = (
                self.recording_repair_fixture_id == fixture.fixture_id
                and not rejected
                and not standing_policy
            )
            self.rejected = self.rejected or rejected
            self._append_ordinary_attempt(
                planner=planner,
                accepted=(not rejected and not recording_repair),
            )
            review_id = "review-" + text_sha256(f"{fixture.fixture_id}:{self.calls}:initial")[:28]
            story = self._ordinary_story(fixture, suffix="initial")
            review = self._ordinary_review(
                fixture,
                review_id=review_id,
                story=story,
                planner=planner,
                accepted=not rejected and not standing_policy,
                standing_policy=standing_policy,
                attempt_number=1,
                attempts=None,
            )
            if recording_repair:
                review["recording_status"] = "pending_repair"
                review["actions"] = self._ordinary_actions(repair_recording=True)
                review["provider_operations"]["recorder"] = 0
            self.reviews[review_id] = review
            if not rejected:
                self._bind_terminal_decision(
                    review_id,
                    creator_action=(
                        "standing_policy_accept_provisional"
                        if standing_policy
                        else "automatic_accept"
                    ),
                )
            body = self._ordinary_provisional_body(
                fixture,
                review_id=review_id,
                story=story,
                planner=planner,
            )
        elif reject_now and not self.rejected:
            self.rejected = True
            self._append_attempt(fixture, planner=planner, accepted=False)
            body = self._rejected_body(fixture, planner=planner)
            if fixture.expected_route is QualificationRoute.ADULT:
                cera = body["cera"]
                review_id = cera["review_id"]
                self.reviews[review_id] = self._adult_rejection_review(cera)
        else:
            self._append_attempt(fixture, planner=planner, accepted=True)
            body = self._accepted_body(fixture, planner=planner)
        return ClientResponseV1(
            transport="fake",
            path="/v1/chat/completions",
            status_code=200,
            duration_ms=self.INITIAL_COMPLETION_DURATION_MS,
            body=body,
        )

    def regenerate(
        self,
        *,
        fixture: QualificationFixtureV1,
        review_id: str,
    ) -> ClientResponseV1:
        if fixture.expected_route is QualificationRoute.ADULT:
            self.assert_equal(review_id, "review-0123456789abcdef0123456789ab")
            self.regenerates += 1
            self._append_attempt(fixture, planner=0, accepted=True)
            transitioned = deepcopy(self.reviews[review_id])
            transitioned["state"] = "regenerated"
            transitioned["regenerate_enabled"] = False
            self.reviews[review_id] = transitioned
            return ClientResponseV1(
                transport="fake-review",
                path=f"/v1/cera/reviews/{review_id}/decision",
                status_code=200,
                duration_ms=8,
                body={"successor": self._accepted_body(fixture, planner=0)},
            )
        predecessor = self.reviews[review_id]
        self.regenerates += 1
        self._append_ordinary_attempt(planner=0, accepted=True)
        successor_review_id = (
            "review-" + text_sha256(f"{fixture.fixture_id}:{self.calls}:regenerate")[:28]
        )
        story = self._ordinary_story(fixture, suffix="regenerated")
        predecessor_attempts = deepcopy(predecessor["provider_attempts"])
        successor = self._ordinary_review(
            fixture,
            review_id=successor_review_id,
            story=story,
            planner=0,
            accepted=True,
            attempt_number=2,
            attempts=predecessor_attempts,
        )
        self.reviews[successor_review_id] = successor
        self._bind_terminal_decision(
            successor_review_id,
            creator_action="automatic_accept",
        )
        predecessor_transitioned = deepcopy(predecessor)
        predecessor_transitioned["state"] = "regenerated"
        predecessor_transitioned["actions"] = self._ordinary_actions()
        predecessor_transitioned["terminal_decision"] = None
        provisional = self._ordinary_provisional_body(
            fixture,
            review_id=successor_review_id,
            story=story,
            planner=0,
        )
        decision = self._decision_with_terminal_pointer(
            {
                "schema_version": "cera.pi_scene.review_decision.v3",
                "status": "review_transitioned",
                "creator_action": "regenerate",
                "story_state_committed": False,
                "retry_mode": "not_applicable",
                "review": predecessor_transitioned,
                "successor": provisional,
                "operational_warnings": [],
            },
            review_id=review_id,
        )
        predecessor_transitioned = deepcopy(decision["review"])
        self.reviews[review_id] = predecessor_transitioned
        self.terminal_decisions[review_id] = decision
        return ClientResponseV1(
            transport="fake-review",
            path=f"/v1/cera/reviews/{review_id}/decision",
            status_code=200,
            duration_ms=8,
            body=decision,
        )

    def review(self, *, review_id: str) -> ClientResponseV1:
        self.review_reads.append(review_id)
        return ClientResponseV1(
            transport="fake-review-get",
            path=f"/v1/cera/reviews/{review_id}",
            status_code=200,
            duration_ms=self.REVIEW_POLL_DURATION_MS,
            body=deepcopy(self.reviews[review_id]),
        )

    def review_action(
        self,
        *,
        fixture: QualificationFixtureV1,
        review_id: str,
        action: dict[str, Any] | Any,
    ) -> ClientResponseV1:
        exact = dict(action)
        self.review_actions.append((review_id, exact))
        if exact == {"action": "regenerate"}:
            return self.regenerate(fixture=fixture, review_id=review_id)
        if exact != {"action": "repair_recording"}:
            raise AssertionError(f"unexpected fake review action: {exact!r}")
        review = deepcopy(self.reviews[review_id])
        review["recording_status"] = "complete"
        review["actions"] = self._ordinary_actions()
        operations = dict(review["provider_operations"])
        operations["recorder"] = 1
        review["provider_operations"] = operations
        self._append_deepseek("recorder")
        self.reviews[review_id] = review
        self._bind_terminal_decision(review_id, creator_action="repair_recording")
        return ClientResponseV1(
            transport="fake-review-action",
            path=f"/v1/cera/reviews/{review_id}/decision",
            status_code=200,
            duration_ms=3,
            body=deepcopy(self.terminal_decisions[review_id]),
        )

    def terminal_review_decision(self, *, review_id: str) -> ClientResponseV1:
        self.terminal_decision_reads.append(review_id)
        return ClientResponseV1(
            transport="fake-terminal-decision",
            path=f"/v1/cera/reviews/{review_id}/terminal-decision",
            status_code=200,
            duration_ms=self.TERMINAL_DECISION_DURATION_MS,
            body=deepcopy(self.terminal_decisions[review_id]),
        )

    def provider_stage_retry_status(self, *, chain_id: str) -> ClientResponseV1:
        raise AssertionError(f"unexpected provider-stage status read: {chain_id}")

    def provider_stage_retry_action(
        self,
        *,
        chain_id: str,
        action: dict[str, Any] | Any,
    ) -> ClientResponseV1:
        raise AssertionError(f"unexpected provider-stage action: {chain_id} {action!r}")

    def _append_ordinary_attempt(self, *, planner: int, accepted: bool) -> None:
        if planner:
            self._append_sol("planner")
        self._append_deepseek("writer")
        self._append_sol("validator")
        self._append_sol("reader")
        if accepted:
            self._append_deepseek("recorder")

    @staticmethod
    def _ordinary_actions(
        *,
        rejected: bool = False,
        repair_recording: bool = False,
    ) -> dict[str, Any]:
        return {
            "accept_enabled": False,
            "regenerate_enabled": rejected,
            "decline_enabled": rejected,
            "replan_enabled": False,
            "auditable_override_enabled": rejected,
            "auditable_override_action": "accept_provisional" if rejected else None,
            "repair_recording_enabled": repair_recording,
        }

    @staticmethod
    def _ordinary_controls() -> dict[str, Any]:
        return {
            "schema_version": "cera.pi_scene.request_controls.v3",
            "session_id": "ordinary-review-fixture",
            "scene_depth": "auto",
            "regeneration_key": None,
            "character_autonomy": "both",
            "prompt_handling": "adjustment",
            "reasoning_effort": "medium",
            "scene_change": False,
            "adult_craft_mode": "off",
            "review_mode": "automatic",
        }

    @staticmethod
    def _ordinary_story(
        fixture: QualificationFixtureV1,
        *,
        suffix: str,
    ) -> str:
        return (
            f"Ordinary {fixture.fixture_id} {suffix} continuation preserves the exact "
            "scene floor and leaves the next meaningful choice open."
        )

    @staticmethod
    def _pending_checks() -> dict[str, Any]:
        return {
            "schema_version": "cera.pi_scene.review_checks.v2",
            "luna": {
                "role": "luna_semantic_validator",
                "required": True,
                "status": "pending",
                "verdict_sha256": None,
                "failures": [],
                "provider_stage_retry_status": None,
            },
            "reader": {
                "role": "codex_reader_severe_quality",
                "required": True,
                "status": "pending",
                "verdict_sha256": None,
                "failures": [],
                "provider_stage_retry_status": None,
            },
            "adult_filter": {
                "role": "protected_adult_filter",
                "required": False,
                "status": "not_applicable",
                "verdict_sha256": None,
                "failures": [],
                "provider_stage_retry_status": None,
            },
            "python": {
                "role": "python_deterministic_custody_privacy",
                "required": True,
                "status": "pending",
                "verdict_sha256": None,
                "failures": [],
                "provider_stage_retry_status": None,
            },
        }

    @classmethod
    def _joined_checks(
        cls,
        *,
        accepted: bool,
        standing_policy: bool = False,
    ) -> dict[str, Any]:
        checks = cls._pending_checks()
        checks["luna"] = {
            "role": "luna_semantic_validator",
            "required": True,
            "status": "pass" if accepted else "reject",
            "verdict_sha256": "1" * 64,
            "failures": (
                []
                if accepted
                else [
                    {
                        "code": (
                            "omitted_decision" if standing_policy else "severe_incompleteness"
                        ),
                        "concise_explanation": (
                            "The candidate omitted one planned beat."
                            if standing_policy
                            else "The candidate omitted one required decision."
                        ),
                        "source_kind": "verdict_conflict",
                        "feedback_scope": None,
                    }
                ]
            ),
            "provider_stage_retry_status": None,
        }
        checks["reader"] = {
            "role": "codex_reader_severe_quality",
            "required": True,
            "status": "pass",
            "verdict_sha256": "2" * 64,
            "failures": [],
            "provider_stage_retry_status": None,
        }
        checks["python"] = {
            "role": "python_deterministic_custody_privacy",
            "required": True,
            "status": "pass",
            "verdict_sha256": "3" * 64,
            "failures": [],
            "provider_stage_retry_status": None,
        }
        return checks

    def _ordinary_review(
        self,
        fixture: QualificationFixtureV1,
        *,
        review_id: str,
        story: str,
        planner: int,
        accepted: bool,
        standing_policy: bool = False,
        attempt_number: int,
        attempts: list[dict[str, Any]] | None,
    ) -> dict[str, Any]:
        candidate_id = (
            "candidate-" + text_sha256(f"{fixture.fixture_id}:{attempt_number}:{story}")[:28]
        )
        current_attempt = {
            "attempt_number": attempt_number,
            "candidate_id": candidate_id,
            "disposition": "checks_passed" if accepted else "luna_rejected",
            "provider_operations": {
                "planner": planner,
                "writer": 1,
                "validator": 1,
                "reader": 1,
            },
        }
        provider_attempts = [] if attempts is None else deepcopy(attempts)
        provider_attempts.append(current_attempt)
        operations = {
            role: sum(int(value["provider_operations"][role]) for value in provider_attempts)
            for role in ("planner", "writer", "validator", "reader")
        }
        committed = accepted or standing_policy
        operations["recorder"] = 1 if committed else 0
        candidate_sha256 = text_sha256(f"candidate:{candidate_id}")
        acceptance: dict[str, Any] | None = None
        if standing_policy:
            audit = build_ordinary_policy_acceptance_audit_from_bindings(
                candidate_sha256=candidate_sha256,
                semantic_validation_sha256="1" * 64,
                reader_validation_sha256="2" * 64,
                python_qualification_sha256="3" * 64,
                tolerated_reason_codes=("luna:omitted_decision",),
            )
            acceptance = {
                "mode": "standing_policy",
                "accepted_turn_id": f"turn-{text_sha256(review_id)[:16]}",
                "accepted_receipt_sha256": text_sha256(f"receipt:{review_id}"),
                "canon_status": "provisional",
                "standing_policy": ordinary_policy_acceptance_projection(audit),
            }
        elif accepted:
            acceptance = {
                "mode": "automatic",
                "accepted_turn_id": f"turn-{text_sha256(review_id)[:16]}",
                "accepted_receipt_sha256": text_sha256(f"receipt:{review_id}"),
                "canon_status": "accepted",
                "standing_policy": None,
            }
        return {
            "schema_version": "cera.pi_scene.review.v3",
            "review_id": review_id,
            "state": "accepted" if committed else "review_ready",
            "review_mode": "automatic",
            "route": "ordinary",
            "story_text": story,
            "candidate_id": candidate_id,
            "candidate_sha256": candidate_sha256,
            "primary_authority_kind": "codex_cognition_plan",
            "primary_authority_sha256": text_sha256(f"authority:{candidate_id}"),
            "warnings": [],
            "recording_status": "complete" if committed else None,
            "gate_status": "pass" if accepted else "reject",
            "checks": self._joined_checks(
                accepted=accepted,
                standing_policy=standing_policy,
            ),
            "acceptance": acceptance,
            "actions": self._ordinary_actions(rejected=not committed),
            "request_controls": self._ordinary_controls(),
            "creator_guidance": None,
            "provider_attempts": provider_attempts,
            "provider_operations": operations,
            "terminal_decision": None,
        }

    def _ordinary_provisional_body(
        self,
        fixture: QualificationFixtureV1,
        *,
        review_id: str,
        story: str,
        planner: int,
    ) -> dict[str, Any]:
        review = self.reviews[review_id]
        pending_checks = self._pending_checks()
        actions = self._ordinary_actions()
        lifecycle = {
            "schema_version": "cera.pi_scene.review_lifecycle.v2",
            "review_id": review_id,
            "review_url": f"/v1/cera/reviews/{review_id}",
            "review_mode": "automatic",
            "state": "checks_pending",
            "gate_status": "pending",
            "checks": pending_checks,
            "acceptance": None,
            "actions": actions,
            "terminal_decision": None,
        }
        return {
            "choices": [
                {
                    "message": {"role": "assistant", "content": story},
                    "finish_reason": "stop",
                }
            ],
            "cera": {
                "profile_id": "cera.pi_scene.lean.v1",
                "route_mode": "ordinary",
                "status": "review_ready",
                "provisional": True,
                "story_state_committed": False,
                "canon_status": None,
                "provisional_review_id": review_id,
                "review_url": f"/v1/cera/reviews/{review_id}",
                "candidate_id": review["candidate_id"],
                "candidate_sha256": review["candidate_sha256"],
                "request_controls": self._ordinary_controls(),
                "operational_warnings": [],
                "route_transition": None,
                "creator_trace": {
                    "logic_owner": "codex_cognition",
                    "route_transition": None,
                },
                "provider_attempts": [
                    {
                        "attempt_number": 1,
                        "candidate_id": review["candidate_id"],
                        "disposition": "checks_pending",
                        "provider_operations": {
                            "planner": planner,
                            "writer": 1,
                            "validator": 0,
                            "reader": 0,
                        },
                    }
                ],
                "provider_operations": {
                    "planner": planner,
                    "writer": 1,
                    "validator": 0,
                    "reader": 0,
                    "recorder": 0,
                },
                "review_lifecycle": lifecycle,
            },
        }

    @staticmethod
    def _decision_with_terminal_pointer(
        value: dict[str, Any],
        *,
        review_id: str,
    ) -> dict[str, Any]:
        decision = deepcopy(value)
        basis = deepcopy(decision)
        basis["review"]["terminal_decision"] = None
        decision_sha256 = canonical_sha256(basis)
        decision["review"]["terminal_decision"] = {
            "decision_sha256": decision_sha256,
            "url": f"/v1/cera/reviews/{review_id}/terminal-decision",
        }
        return decision

    def _bind_terminal_decision(self, review_id: str, *, creator_action: str) -> None:
        review = self.reviews[review_id]
        acceptance = review["acceptance"]
        if not isinstance(acceptance, dict):
            raise AssertionError("terminal decision requires accepted review")
        decision = self._decision_with_terminal_pointer(
            {
                "schema_version": "cera.pi_scene.review_decision.v3",
                "status": "story_committed",
                "creator_action": creator_action,
                "story_state_committed": True,
                "retry_mode": "not_applicable",
                "review": deepcopy(review),
                "successor": None,
                "operational_warnings": [],
                "accepted_receipt_sha256": acceptance["accepted_receipt_sha256"],
                "accepted_turn_id": acceptance["accepted_turn_id"],
            },
            review_id=review_id,
        )
        self.reviews[review_id] = deepcopy(decision["review"])
        self.terminal_decisions[review_id] = decision

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
                "schema_version": "cera.pi_scene.provider_operation_ledger.v1",
                "event": "invocation_prepared",
                "invocation_id": invocation_id,
                "candidate_id_sha256": "a" * 64,
                "purpose": purpose,
                "route": "adult" if purpose.startswith("adult-") else "ordinary",
                "request_sha256": "b" * 64,
                "reserved_operations": DEEPSEEK_PER_INVOCATION_CEILING,
                "recorded_at_utc": timestamp,
            },
        )
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
        _append_jsonl(
            path,
            {
                "schema_version": "cera.pi_scene.provider_operation_ledger.v1",
                "event": "invocation_completed",
                "invocation_id": invocation_id,
                "operations_used": 1,
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
        route_validation: dict[str, Any]
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
                "candidate_id": "candidate:adult:" + "b" * 32,
                "operation_sha256": "4" * 64,
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

    @staticmethod
    def _adult_rejection_review(cera: dict[str, Any]) -> dict[str, Any]:
        return {
            "schema_version": "cera.pi_scene.review.v1",
            "review_id": cera["review_id"],
            "state": "review_ready",
            "provisional": True,
            "route": "adult",
            "story_text": None,
            "story_state_committed": False,
            "candidate_id": cera["candidate_id"],
            "candidate_sha256": text_sha256(cera["candidate_id"]),
            "primary_authority_sha256": cera["operation_sha256"],
            "regenerate_enabled": True,
            "accept_enabled": False,
            "provisional_accept_enabled": False,
            "replan_enabled": False,
            "repair_recording_enabled": False,
            "operation_state": "executed-rejected",
            "semantic_validation": {"verdict": "reject"},
        }

    def assert_equal(self, left: object, right: object) -> None:
        if left != right:
            raise AssertionError(f"{left!r} != {right!r}")


class _FakeProviderStageRetryClient(_FakeQualificationClient):
    _STAGE_OWNER = {
        "planner": "planner",
        "semantic_validator": "validator",
        "reader": "reader",
        "writer": "writer",
        "recorder": "recorder",
        "adult_scene": "adult-scene",
        "adult_filter": "adult-filter",
    }

    def __init__(
        self,
        runtime_root: Path,
        *,
        failure_fixture_id: str,
        stage: str = "planner",
        failures_before_success: int = 1,
        later_stage: str | None = None,
        terminal_state: str | None = None,
        ambiguous_post: bool = False,
        failure_on_regenerate: bool = False,
        prepared_resume: bool = False,
        repair_successor_exhausts: bool = False,
        deepseek_success_operations: int = 1,
        private_sentinel: str = "PRIVATE QUALIFICATION STORY SENTINEL",
    ) -> None:
        super().__init__(runtime_root)
        if stage not in self._STAGE_OWNER or later_stage not in {None, *self._STAGE_OWNER}:
            raise AssertionError("fake provider stage is invalid")
        if failures_before_success not in {1, 2}:
            raise AssertionError("fake failure count is invalid")
        self.failure_fixture_id = failure_fixture_id
        self.failure_stage = stage
        self.failures_before_success = failures_before_success
        self.later_stage = later_stage
        self.terminal_state = terminal_state
        self.ambiguous_post = ambiguous_post
        self.failure_on_regenerate = failure_on_regenerate
        self.prepared_resume = prepared_resume
        self.repair_successor_exhausts = repair_successor_exhausts
        if not 1 <= deepseek_success_operations <= DEEPSEEK_PER_INVOCATION_CEILING:
            raise AssertionError("fake DeepSeek success operation count is invalid")
        self.deepseek_success_operations = deepseek_success_operations
        self.private_sentinel = private_sentinel
        self.failed_fixture: QualificationFixtureV1 | None = None
        self.status_by_chain: dict[str, dict[str, Any]] = {}
        self.action_by_chain: dict[str, dict[str, Any]] = {}
        self.latest_by_chain: dict[str, str] = {}
        self.completion_by_chain: dict[str, dict[str, Any]] = {}
        self.status_reads: list[str] = []
        self.action_posts: list[tuple[str, dict[str, Any]]] = []
        self._ambiguous_raised = False
        self._completion_planner_override: int | None = None
        self.repair_successor_chains: set[str] = set()
        if failure_on_regenerate:
            self.reject_first = True

    def complete(
        self,
        *,
        fixture: QualificationFixtureV1,
        session_id: str,
        payload: dict[str, Any] | Any,
    ) -> ClientResponseV1:
        if fixture.fixture_id != self.failure_fixture_id or self.failure_on_regenerate:
            return super().complete(fixture=fixture, session_id=session_id, payload=payload)
        value = dict(payload)
        if self.session_id is None:
            self.session_id = session_id
        self.assert_equal(self.session_id, session_id)
        self.assert_equal(len(value["messages"]), self.calls * 2 + 1)
        self.calls += 1
        self.failed_fixture = fixture
        if self.prepared_resume:
            envelope = self._envelope(
                self.failure_stage,
                attempts=1,
                retries=0,
                state="in_progress",
                control_action="resume_prepared",
                operations_observed=0,
            )
            return ClientResponseV1(
                transport="fake-provider-stage-prepared",
                path="/v1/chat/completions",
                status_code=409,
                duration_ms=11,
                body=envelope,
            )
        self._append_before_failure(fixture, self.failure_stage)
        self._append_stage_failure(self.failure_stage)
        initial_state = (
            self.terminal_state
            if self.terminal_state in {"recovery_required", "blocked_ambiguous"}
            else "eligible"
        )
        envelope = self._envelope(
            self.failure_stage,
            attempts=1,
            retries=0,
            state=(initial_state or "eligible"),
        )
        return ClientResponseV1(
            transport="fake-provider-stage-initial",
            path="/v1/chat/completions",
            status_code=409,
            duration_ms=11,
            body=envelope,
        )

    def regenerate(
        self,
        *,
        fixture: QualificationFixtureV1,
        review_id: str,
    ) -> ClientResponseV1:
        if not self.failure_on_regenerate or fixture.fixture_id != self.failure_fixture_id:
            return super().regenerate(fixture=fixture, review_id=review_id)
        if review_id not in self.reviews:
            raise AssertionError("provider Retry Regenerate changed review identity")
        self.regenerates += 1
        self.failed_fixture = fixture
        self._completion_planner_override = 0
        if self.failure_stage in {"semantic_validator", "recorder"}:
            self._append_deepseek("writer")
        if self.failure_stage == "recorder":
            self._append_sol("validator")
        self._append_stage_failure(self.failure_stage)
        envelope = self._envelope(
            self.failure_stage,
            attempts=1,
            retries=0,
            state="eligible",
        )
        return ClientResponseV1(
            transport="fake-provider-stage-regenerate",
            path=f"/v1/cera/reviews/{review_id}/decision",
            status_code=409,
            duration_ms=12,
            body=envelope,
        )

    def provider_stage_retry_status(self, *, chain_id: str) -> ClientResponseV1:
        self.status_reads.append(chain_id)
        current = self.latest_by_chain.get(chain_id, chain_id)
        completion = self.completion_by_chain.get(current)
        if completion is not None:
            return ClientResponseV1(
                transport="fake-provider-stage-get",
                path=f"/v1/cera/provider-stage-retries/{chain_id}",
                status_code=200,
                duration_ms=7,
                body=completion,
            )
        return ClientResponseV1(
            transport="fake-provider-stage-get",
            path=f"/v1/cera/provider-stage-retries/{chain_id}",
            status_code=200,
            duration_ms=7,
            body=self.status_by_chain[current],
        )

    def provider_stage_retry_action(
        self,
        *,
        chain_id: str,
        action: dict[str, Any] | Any,
    ) -> ClientResponseV1:
        exact = dict(action)
        current = self.latest_by_chain.get(chain_id, chain_id)
        if exact != self.action_by_chain[current]:
            raise AssertionError("qualification changed the backend-issued action")
        if any(posted[1]["action_id"] == exact["action_id"] for posted in self.action_posts):
            raise AssertionError("qualification POSTed one action twice")
        self.action_posts.append((current, exact))
        status = self.status_by_chain[current]["status"]
        stage = str(status["stage"])
        action_kind = str(exact["action_kind"])
        if action_kind == "resume_prepared":
            self._append_stage_success(stage)
            self._append_after_success(stage)
            assert self.failed_fixture is not None
            completion = self._successful_completion(stage=stage, observed=1)
            self.completion_by_chain[current] = completion
            return ClientResponseV1(
                transport="fake-provider-stage-action",
                path=(f"/v1/cera/provider-stage-retries/{current}/actions/{exact['action_id']}"),
                status_code=200,
                duration_ms=13,
                body=completion,
            )
        if action_kind == "repair_recording":
            self._append_stage_failure("recorder")
            successor = self._envelope(
                "recorder",
                attempts=1,
                retries=0,
                state="eligible",
            )
            successor_id = str(successor["status"]["chain_id"])
            self.repair_successor_chains.add(successor_id)
            self.latest_by_chain[current] = successor_id
            return ClientResponseV1(
                transport="fake-provider-stage-action",
                path=(f"/v1/cera/provider-stage-retries/{current}/actions/{exact['action_id']}"),
                status_code=200,
                duration_ms=13,
                body=successor,
            )
        if action_kind != "provider_retry":
            raise AssertionError("qualification fake received an unknown stage control")
        accepted = int(status["retry_actions_accepted"]) + 1
        attempts = int(status["stage_attempts_total"]) + 1
        if current in self.repair_successor_chains and not self.repair_successor_exhausts:
            self._append_stage_success(stage)
            assert self.failed_fixture is not None
            completion = self._successful_completion(stage=stage, observed=attempts)
            self.completion_by_chain[current] = completion
            body = completion
        elif accepted < self.failures_before_success:
            self._append_stage_failure(stage)
            body = self._envelope(
                stage,
                chain_id=current,
                attempts=attempts,
                retries=accepted,
                state="eligible",
            )
        elif self.terminal_state in {
            "attempts_exhausted",
            "recording_repair_required",
        }:
            self._append_stage_failure(stage)
            body = self._envelope(
                stage,
                chain_id=current,
                attempts=3,
                retries=2,
                state=self.terminal_state,
                allow_repair_action=current not in self.repair_successor_chains,
            )
        else:
            self._append_stage_success(stage)
            if self.later_stage is not None and stage == self.failure_stage:
                self._append_between_stages(stage, self.later_stage)
                self._append_stage_failure(self.later_stage)
                successor = self._envelope(
                    self.later_stage,
                    attempts=1,
                    retries=0,
                    state="eligible",
                )
                successor_id = str(successor["status"]["chain_id"])
                self.latest_by_chain[current] = successor_id
                body = successor
            else:
                self._append_after_success(stage)
                assert self.failed_fixture is not None
                completion = self._successful_completion(stage=stage, observed=attempts)
                self.completion_by_chain[current] = completion
                body = completion
        response = ClientResponseV1(
            transport="fake-provider-stage-action",
            path=(f"/v1/cera/provider-stage-retries/{current}/actions/{exact['action_id']}"),
            status_code=200,
            duration_ms=13,
            body=body,
        )
        if self.ambiguous_post and not self._ambiguous_raised:
            self._ambiguous_raised = True
            raise OSError(self.private_sentinel)
        return response

    def _successful_completion(self, *, stage: str, observed: int) -> dict[str, Any]:
        assert self.failed_fixture is not None
        fixture = self.failed_fixture
        planner = (
            self._completion_planner_override
            if self._completion_planner_override is not None
            else 1
            if fixture.initial_route is QualificationRoute.ORDINARY
            else 0
        )
        if fixture.expected_route is QualificationRoute.ADULT:
            return self._accepted_body(fixture, planner=planner)
        review_id = "review-" + text_sha256(f"{fixture.fixture_id}:{self.calls}:retry-success")[:28]
        story = self._ordinary_story(fixture, suffix="retry-success")
        predecessor_id: str | None = None
        predecessor: dict[str, Any] | None = None
        if self.failure_on_regenerate:
            predecessor_id, predecessor = next(
                (identity, value)
                for identity, value in self.reviews.items()
                if value["state"] == "review_ready" and value["gate_status"] == "reject"
            )
        review = self._ordinary_review(
            fixture,
            review_id=review_id,
            story=story,
            planner=planner,
            accepted=True,
            attempt_number=2 if predecessor is not None else 1,
            attempts=(None if predecessor is None else deepcopy(predecessor["provider_attempts"])),
        )
        if stage in {"semantic_validator", "reader"}:
            role = "validator" if stage == "semantic_validator" else "reader"
            review["provider_attempts"][0]["provider_operations"][role] = observed
            review["provider_operations"][role] = observed
        elif stage == "writer":
            review["provider_attempts"][-1]["provider_operations"]["writer"] = (
                self.deepseek_success_operations
            )
            review["provider_operations"]["writer"] = sum(
                int(value["provider_operations"]["writer"]) for value in review["provider_attempts"]
            )
        elif stage == "recorder":
            review["provider_operations"]["recorder"] = self.deepseek_success_operations
        self.reviews[review_id] = review
        self._bind_terminal_decision(review_id, creator_action="automatic_accept")
        provisional = self._ordinary_provisional_body(
            fixture,
            review_id=review_id,
            story=story,
            planner=planner,
        )
        if predecessor is None or predecessor_id is None:
            return provisional
        transitioned = deepcopy(predecessor)
        transitioned["state"] = "regenerated"
        transitioned["actions"] = self._ordinary_actions()
        decision = self._decision_with_terminal_pointer(
            {
                "schema_version": "cera.pi_scene.review_decision.v3",
                "status": "review_transitioned",
                "creator_action": "regenerate",
                "story_state_committed": False,
                "retry_mode": "not_applicable",
                "review": transitioned,
                "successor": provisional,
                "operational_warnings": [],
            },
            review_id=predecessor_id,
        )
        self.reviews[predecessor_id] = deepcopy(decision["review"])
        self.terminal_decisions[predecessor_id] = decision
        return decision

    def _envelope(
        self,
        stage: str,
        *,
        attempts: int,
        retries: int,
        state: str,
        chain_id: str | None = None,
        control_action: str | None = None,
        operations_observed: int | None = None,
        allow_repair_action: bool = True,
    ) -> dict[str, Any]:
        provider = "codex" if stage in {"planner", "semantic_validator", "reader"} else "deepseek"
        model_family = (
            "sol"
            if stage in {"planner", "reader"}
            else "luna"
            if stage == "semantic_validator"
            else "deepseek_v4"
        )
        identity = chain_id or f"stage-retry-{text_sha256(f'{stage}:{len(self.status_by_chain)}')}"
        chain_sha256 = text_sha256(f"{identity}:{attempts}:{retries}:{state}")
        failure_category: str | None = "transport_timeout"
        available_actions: list[str] = []
        actions: list[dict[str, Any]] = []
        if state == "eligible":
            available_actions = ["provider_retry"]
            action = {
                "schema_version": "cera.provider_stage_retry_action.v1",
                "action_id": f"stage-action-{text_sha256(f'{identity}:{retries + 1}')}",
                "chain_id": identity,
                "action_family": "provider_stage_control",
                "action_kind": "provider_retry",
                "automatic": False,
                "provider_dispatch_authorized": True,
                "consumes_retry_action": True,
                "retry_action_ordinal": retries + 1,
                "whole_request_replay_authorized": False,
                "provider_substitution_authorized": False,
                "expected_chain_sha256": chain_sha256,
            }
            actions = [action]
            self.action_by_chain[identity] = action
        elif state == "in_progress" and control_action == "resume_prepared":
            failure_category = None
            available_actions = ["resume_prepared"]
            actions = [
                {
                    "schema_version": "cera.provider_stage_retry_action.v1",
                    "action_id": f"stage-action-{text_sha256(f'{identity}:resume')}",
                    "chain_id": identity,
                    "action_family": "provider_stage_control",
                    "action_kind": "resume_prepared",
                    "automatic": False,
                    "provider_dispatch_authorized": True,
                    "consumes_retry_action": False,
                    "retry_action_ordinal": None,
                    "whole_request_replay_authorized": False,
                    "provider_substitution_authorized": False,
                    "expected_chain_sha256": chain_sha256,
                }
            ]
            self.action_by_chain[identity] = actions[0]
        elif state == "blocked_ambiguous":
            failure_category = "dispatch_ambiguous"
            available_actions = ["check_status"]
            actions = [
                {
                    "schema_version": "cera.provider_stage_retry_action.v1",
                    "action_id": f"stage-action-{text_sha256(f'{identity}:check')}",
                    "chain_id": identity,
                    "action_family": "provider_stage_control",
                    "action_kind": "check_status",
                    "automatic": False,
                    "provider_dispatch_authorized": False,
                    "consumes_retry_action": False,
                    "retry_action_ordinal": None,
                    "whole_request_replay_authorized": False,
                    "provider_substitution_authorized": False,
                    "expected_chain_sha256": chain_sha256,
                }
            ]
        elif state == "recording_repair_required" and allow_repair_action:
            available_actions = ["repair_recording"]
            actions = [
                {
                    "schema_version": "cera.provider_stage_retry_action.v1",
                    "action_id": f"stage-action-{text_sha256(f'{identity}:repair')}",
                    "chain_id": identity,
                    "action_family": "provider_stage_control",
                    "action_kind": "repair_recording",
                    "automatic": False,
                    "provider_dispatch_authorized": True,
                    "consumes_retry_action": False,
                    "retry_action_ordinal": None,
                    "whole_request_replay_authorized": False,
                    "provider_substitution_authorized": False,
                    "expected_chain_sha256": chain_sha256,
                }
            ]
            self.action_by_chain[identity] = actions[0]
        elif state == "recovery_required":
            failure_category = "configuration_failed"
        elif state in {"in_progress", "succeeded"}:
            failure_category = None
        status = {
            "schema_version": "cera.provider_stage_retry_status.v1",
            "chain_id": identity,
            "provider": provider,
            "model_family": model_family,
            "stage": stage,
            "state": state,
            "maximum_attempts": 3,
            "stage_attempts_total": attempts,
            "retry_actions_accepted": retries,
            "provider_operations_observed_total": (
                attempts if operations_observed is None else operations_observed
            ),
            "provider_operations_conservative_total": (
                attempts if operations_observed is None else operations_observed
            ),
            "story_state_committed": stage == "recorder",
            "branch_preserved_at_last_accepted_head": True,
            "failure_category": failure_category,
            "available_actions": available_actions,
            "technical_details": {
                "schema_version": "cera.provider_stage_retry_technical_details.v1",
                "request_occurrence_sha256": "1" * 64,
                "request_sha256": "2" * 64,
                "stage_input_sha256": text_sha256(f"input:{stage}"),
                "accepted_state_sha256": "3" * 64,
                "chain_sha256": chain_sha256,
            },
        }
        envelope = {
            "schema_version": "cera.provider_stage_retry_status_envelope.v1",
            "status": status,
            "actions": actions,
        }
        self.status_by_chain[identity] = envelope
        self.latest_by_chain.setdefault(identity, identity)
        return envelope

    def _append_before_failure(self, fixture: QualificationFixtureV1, stage: str) -> None:
        if stage != "planner" and fixture.initial_route is QualificationRoute.ORDINARY:
            self._append_sol("planner")
        if stage in {"semantic_validator", "reader", "recorder"}:
            self._append_deepseek("writer")
        if stage in {"reader", "recorder"}:
            self._append_sol("validator")
        if stage == "recorder":
            self._append_sol("reader")
        if stage == "adult_filter":
            self._append_deepseek("adult-scene")

    def _append_between_stages(self, source: str, target: str) -> None:
        if source == "writer" and target == "semantic_validator":
            return
        raise AssertionError("unsupported fake later-stage transition")

    def _append_after_success(self, stage: str) -> None:
        if stage == "planner":
            self._append_deepseek("writer")
            self._append_sol("validator")
            self._append_sol("reader")
            self._append_deepseek("recorder")
        elif stage == "writer":
            self._append_sol("validator")
            self._append_sol("reader")
            self._append_deepseek("recorder")
        elif stage == "semantic_validator":
            self._append_sol("reader")
            self._append_deepseek("recorder")
        elif stage == "reader":
            self._append_deepseek("recorder")
        elif stage == "adult_scene":
            self._append_deepseek("adult-filter")

    def _append_stage_success(self, stage: str) -> None:
        owner = self._STAGE_OWNER[stage]
        if stage in {"planner", "semantic_validator", "reader"}:
            self._append_sol(owner)
        else:
            for _ in range(self.deepseek_success_operations):
                self._append_deepseek(owner)

    def _append_stage_failure(self, stage: str) -> None:
        owner = self._STAGE_OWNER[stage]
        if stage in {"planner", "semantic_validator", "reader"}:
            self._append_sol_failure(owner, submitted=True, terminal_state="provider_failed")
            return
        path = self.runtime_root / "DEEPSEEK_PROVIDER_OPERATIONS.jsonl"
        existing = _jsonl(path)
        global_index = (
            sum(value.get("event") == "provider_operation_started" for value in existing) + 1
        )
        invocation_id = f"piop-{global_index:04d}-{owner}-failed"
        timestamp = datetime.now(UTC).isoformat(timespec="microseconds")
        _append_jsonl(
            path,
            {
                "schema_version": "cera.pi_scene.provider_operation_ledger.v1",
                "event": "invocation_prepared",
                "invocation_id": invocation_id,
                "candidate_id_sha256": "a" * 64,
                "purpose": owner,
                "route": "adult" if owner.startswith("adult-") else "ordinary",
                "request_sha256": "b" * 64,
                "reserved_operations": DEEPSEEK_PER_INVOCATION_CEILING,
                "recorded_at_utc": timestamp,
            },
        )
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
                "event": "invocation_failed",
                "invocation_id": invocation_id,
                "failure_receipt_sha256": "9" * 64,
                "recorded_at_utc": timestamp,
            },
        )

    def _append_sol_failure(
        self,
        owner: str,
        *,
        submitted: bool,
        terminal_state: str,
    ) -> None:
        if submitted is not True or terminal_state != "provider_failed":
            raise AssertionError("generic fake only models submitted provider failure")
        path = self.runtime_root / "SOL_PROVIDER_CALLS.jsonl"
        existing = _jsonl(path)
        call_id = f"call-{len(existing) + 1}-{owner}-failed"
        timestamp = datetime.now(UTC).isoformat(timespec="microseconds")
        for state in ("transport_invoked", "provider_failed"):
            event = {
                "event_index": len(existing) + 1,
                "call_id": call_id,
                "owner": owner,
                "operation": owner,
                "state": state,
                "route": "fake",
                "model": "gpt-5.6-luna" if owner == "validator" else "gpt-5.6-sol",
                "stored_thread_sha256": text_sha256(call_id),
                "failure_receipt_sha256": "9" * 64,
                "recorded_at_utc": timestamp,
            }
            _append_jsonl(path, event)
            existing.append(event)


class _FakeNestedValidationRetryClient(_FakeProviderStageRetryClient):
    """Production-shaped Luna/Reader failure nested in polled review.v3."""

    def __init__(self, runtime_root: Path, **kwargs: Any) -> None:
        super().__init__(runtime_root, **kwargs)
        if self.failure_stage not in {"semantic_validator", "reader"}:
            raise AssertionError("nested validation fake requires Luna or Reader")
        self.nested_review_id: str | None = None

    def complete(
        self,
        *,
        fixture: QualificationFixtureV1,
        session_id: str,
        payload: dict[str, Any] | Any,
    ) -> ClientResponseV1:
        if fixture.fixture_id != self.failure_fixture_id:
            return _FakeQualificationClient.complete(
                self,
                fixture=fixture,
                session_id=session_id,
                payload=payload,
            )
        value = dict(payload)
        if self.session_id is None:
            self.session_id = session_id
        self.assert_equal(self.session_id, session_id)
        self.assert_equal(len(value["messages"]), self.calls * 2 + 1)
        self.calls += 1
        self.failed_fixture = fixture
        self._append_sol("planner")
        self._append_deepseek("writer")
        peer_owner = "reader" if self.failure_stage == "semantic_validator" else "validator"
        self._append_sol(peer_owner)
        if not self.prepared_resume:
            self._append_stage_failure(self.failure_stage)
        initial_state = (
            self.terminal_state
            if self.terminal_state in {"recovery_required", "blocked_ambiguous"}
            else "eligible"
        )
        envelope = self._envelope(
            self.failure_stage,
            attempts=1,
            retries=0,
            state=("in_progress" if self.prepared_resume else initial_state),
            control_action=("resume_prepared" if self.prepared_resume else None),
            operations_observed=0 if self.prepared_resume else 1,
        )
        review_id = (
            "review-" + text_sha256(f"{fixture.fixture_id}:nested:{self.failure_stage}")[:28]
        )
        self.nested_review_id = review_id
        story = self._ordinary_story(fixture, suffix="nested-validation")
        review = self._ordinary_review(
            fixture,
            review_id=review_id,
            story=story,
            planner=1,
            accepted=False,
            attempt_number=1,
            attempts=None,
        )
        checks = self._joined_checks(accepted=True)
        lane_name = "luna" if self.failure_stage == "semantic_validator" else "reader"
        lane = dict(checks[lane_name])
        lane.update(
            {
                "status": "inconclusive",
                "verdict_sha256": None,
                "failures": [
                    {
                        "code": "provider_transport_unavailable",
                        "concise_explanation": "The validation provider did not return a verdict.",
                        "source_kind": "runtime_failure",
                        "feedback_scope": None,
                    }
                ],
                "provider_stage_retry_status": envelope,
            }
        )
        checks[lane_name] = lane
        review.update(
            {
                "state": "checks_pending",
                "gate_status": "blocked",
                "checks": checks,
                "actions": self._ordinary_actions(),
                "provider_attempts": [
                    {
                        **review["provider_attempts"][0],
                        "disposition": "checks_blocked",
                    }
                ],
            }
        )
        failed_role = "validator" if self.failure_stage == "semantic_validator" else "reader"
        review["provider_attempts"][0]["provider_operations"][failed_role] = (
            0 if self.prepared_resume else 1
        )
        review["provider_operations"][failed_role] = 0 if self.prepared_resume else 1
        self.reviews[review_id] = review
        body = self._ordinary_provisional_body(
            fixture,
            review_id=review_id,
            story=story,
            planner=1,
        )
        return ClientResponseV1(
            transport="fake-provisional-with-nested-retry",
            path="/v1/chat/completions",
            status_code=200,
            duration_ms=10,
            body=body,
        )

    def _append_after_success(self, stage: str) -> None:
        if stage not in {"semantic_validator", "reader"}:
            raise AssertionError("nested validation fake changed stage")
        self._append_deepseek("recorder")

    def _successful_completion(self, *, stage: str, observed: int) -> dict[str, Any]:
        if self.nested_review_id is None:
            raise AssertionError("nested validation fake lost its review")
        review = deepcopy(self.reviews[self.nested_review_id])
        checks = deepcopy(review["checks"])
        lane_name = "luna" if stage == "semantic_validator" else "reader"
        checks[lane_name] = {
            **checks[lane_name],
            "status": "pass",
            "verdict_sha256": "4" * 64,
            "failures": [],
            "provider_stage_retry_status": None,
        }
        role = "validator" if stage == "semantic_validator" else "reader"
        review["provider_attempts"][0]["provider_operations"][role] = observed
        review["provider_attempts"][0]["disposition"] = "checks_passed"
        review["provider_operations"][role] = observed
        review["provider_operations"]["recorder"] = 1
        acceptance = {
            "mode": "automatic",
            "accepted_turn_id": f"turn-{text_sha256(self.nested_review_id)[:16]}",
            "accepted_receipt_sha256": text_sha256(f"receipt:{self.nested_review_id}"),
            "canon_status": "accepted",
        }
        review.update(
            {
                "state": "accepted",
                "gate_status": "pass",
                "checks": checks,
                "acceptance": acceptance,
                "recording_status": "complete",
                "actions": self._ordinary_actions(),
            }
        )
        self.reviews[self.nested_review_id] = review
        self._bind_terminal_decision(
            self.nested_review_id,
            creator_action="automatic_accept",
        )
        return deepcopy(self.reviews[self.nested_review_id])


class _InitialProviderRecordingRepairClient(_FakeProviderStageRetryClient):
    def complete(
        self,
        *,
        fixture: QualificationFixtureV1,
        session_id: str,
        payload: dict[str, Any] | Any,
    ) -> ClientResponseV1:
        if fixture.fixture_id != self.failure_fixture_id:
            return super().complete(fixture=fixture, session_id=session_id, payload=payload)
        value = dict(payload)
        if self.session_id is None:
            self.session_id = session_id
        self.assert_equal(self.session_id, session_id)
        self.assert_equal(len(value["messages"]), self.calls * 2 + 1)
        self.calls += 1
        self.failed_fixture = fixture
        envelope = self._envelope(
            "recorder",
            attempts=3,
            retries=2,
            state="recording_repair_required",
        )
        return ClientResponseV1(
            transport="fake-provider-stage-recording-repair",
            path="/v1/chat/completions",
            status_code=409,
            duration_ms=11,
            body=envelope,
        )


class _StaleProviderStageAuthorityClient(_FakeProviderStageRetryClient):
    def provider_stage_retry_status(self, *, chain_id: str) -> ClientResponseV1:
        response = super().provider_stage_retry_status(chain_id=chain_id)
        if len(self.status_reads) != 2 or response.body.get("schema_version") != (
            "cera.provider_stage_retry_status_envelope.v1"
        ):
            return response
        changed = cast(dict[str, Any], deepcopy(response.body))
        changed["status"]["technical_details"]["accepted_state_sha256"] = "e" * 64
        return ClientResponseV1(
            transport=response.transport,
            path=response.path,
            status_code=response.status_code,
            duration_ms=response.duration_ms,
            body=changed,
        )


class _StaleReviewAuthorityClient(_FakeQualificationClient):
    def review(self, *, review_id: str) -> ClientResponseV1:
        response = super().review(review_id=review_id)
        if self.review_reads.count(review_id) != 2:
            return response
        changed = cast(dict[str, Any], deepcopy(response.body))
        if changed.get("schema_version") == "cera.pi_scene.review.v3":
            changed["primary_authority_sha256"] = "e" * 64
        else:
            changed["candidate_sha256"] = "e" * 64
        return ClientResponseV1(
            transport=response.transport,
            path=response.path,
            status_code=response.status_code,
            duration_ms=response.duration_ms,
            body=changed,
        )


class _LostOrdinaryRegenerateResponseClient(_FakeQualificationClient):
    def regenerate(
        self,
        *,
        fixture: QualificationFixtureV1,
        review_id: str,
    ) -> ClientResponseV1:
        super().regenerate(fixture=fixture, review_id=review_id)
        raise OSError("simulated lost ordinary Regenerate response")


class _LostAdultRegenerateResponseClient(_FakeQualificationClient):
    def regenerate(
        self,
        *,
        fixture: QualificationFixtureV1,
        review_id: str,
    ) -> ClientResponseV1:
        super().regenerate(fixture=fixture, review_id=review_id)
        raise OSError("simulated lost adult Regenerate response")


class _DelayedLostOrdinaryRegenerateResponseClient(_LostOrdinaryRegenerateResponseClient):
    def __init__(self, runtime_root: Path, **kwargs: Any) -> None:
        super().__init__(runtime_root, **kwargs)
        self.delayed_terminal_reads = 0

    def terminal_review_decision(self, *, review_id: str) -> ClientResponseV1:
        if self.delayed_terminal_reads == 0:
            self.delayed_terminal_reads += 1
            self.terminal_decision_reads.append(review_id)
            return ClientResponseV1(
                transport="fake-terminal-decision-pending",
                path=f"/v1/cera/reviews/{review_id}/terminal-decision",
                status_code=404,
                duration_ms=1,
                body={"error": "not durable yet"},
            )
        return super().terminal_review_decision(review_id=review_id)


class _LostReviewRecordingRepairResponseClient(_FakeQualificationClient):
    def review_action(
        self,
        *,
        fixture: QualificationFixtureV1,
        review_id: str,
        action: dict[str, Any] | Any,
    ) -> ClientResponseV1:
        super().review_action(
            fixture=fixture,
            review_id=review_id,
            action=action,
        )
        raise OSError("simulated lost review Recorder repair response")


class FullModelQualificationTests(unittest.TestCase):
    def test_sol_ledger_owner_and_model_mapping_is_closed(self) -> None:
        def delta(*, owner: str, model: str) -> ProviderLedgerDeltaV1:
            events = tuple(
                {
                    "call_id": "call-closed-owner",
                    "owner": owner,
                    "model": model,
                    "state": state,
                    "route": "ordinary",
                    "recorded_at_utc": "2026-08-10T00:00:00+00:00",
                }
                for state in ("transport_invoked", "provider_completed", "typed_accepted")
            )
            return ProviderLedgerDeltaV1(sol_events=events, deepseek_events=())

        self.assertEqual(
            _provider_operation_records(delta(owner="reader", model="gpt-5.6-sol"))[0]["stage"],
            "reader",
        )
        with self.assertRaisesRegex(StateConflictError, "owner changed"):
            _provider_operation_records(delta(owner="future_role", model="gpt-5.6-sol"))
        with self.assertRaisesRegex(StateConflictError, "model family changed"):
            _provider_operation_records(delta(owner="reader", model="gpt-5.6-luna"))

    def test_outer_http_timeout_exceeds_every_bounded_provider_stage(self) -> None:
        self.assertEqual(QUALIFICATION_HTTP_HARD_TIMEOUT_SECONDS, 6_000)
        self.assertGreater(
            QUALIFICATION_HTTP_HARD_TIMEOUT_SECONDS,
            QUALIFICATION_MAX_SEQUENTIAL_PROVIDER_STAGES
            * QUALIFICATION_PROVIDER_STAGE_HARD_TIMEOUT_SECONDS,
        )
        self.assertEqual(
            PROVIDER_STAGE_RETRY_STATUS_TIMEOUT_SECONDS,
            QUALIFICATION_HTTP_HARD_TIMEOUT_SECONDS + 15,
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

        class FakeProviderStageRetry:
            def __init__(self) -> None:
                self.adapter: object | None = None
                self.status_reader = object()

            def http_adapter_kwargs(self) -> dict[str, object]:
                return {"provider_stage_retry_status_reader": self.status_reader}

            def bind_http_adapter(self, adapter: object) -> None:
                self.adapter = adapter

        provider_stage_retry = FakeProviderStageRetry()
        runtime = SimpleNamespace(
            coordinator=object(),
            store=object(),
            readable_debug=object(),
            full_model_controller=object(),
            provider_stage_retry=provider_stage_retry,
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
            patch.object(
                entrypoint,
                "build_live_runtime",
                return_value=runtime,
            ) as build_runtime,
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
        runtime_kwargs = build_runtime.call_args.kwargs
        self.assertEqual(
            runtime_kwargs["luna_route"],
            entrypoint.full_model_qualification_luna_validator_route(),
        )
        self.assertEqual(runtime_kwargs["luna_route"].maximum_output_tokens, 128_000)
        for name, seam in seams.items():
            self.assertIs(kwargs[name], seam)
        self.assertIs(
            kwargs["provider_stage_retry_status_reader"],
            provider_stage_retry.status_reader,
        )
        self.assertIs(provider_stage_retry.adapter, adapter.return_value)
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
                    subprocess,
                    "Popen",
                    return_value=process,
                ) as popen,
                patch.object(
                    entrypoint,
                    "urlopen",
                    side_effect=(OSError("transient readiness miss"), ReadyResponse()),
                ) as ready,
                patch(
                    "scripts.run_pi_scene_full_model_qualification.time.monotonic",
                    side_effect=(0.0, 0.0, 0.1),
                ),
                patch("scripts.run_pi_scene_full_model_qualification.time.sleep"),
                patch.object(entrypoint, "_stop_process") as stop,
            ):
                observed = entrypoint._start_qualification_sillytavern(
                    root,
                    port=32123,
                    cera_port=32124,
                )

        self.assertIs(observed, process)
        self.assertEqual(ready.call_count, 2)
        self.assertEqual(popen.call_args.kwargs["stdout"], subprocess.DEVNULL)
        self.assertEqual(popen.call_args.kwargs["stderr"], subprocess.DEVNULL)
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

    def test_manifest_v29_freezes_every_execution_policy_field(self) -> None:
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
        manifest["execution_policy"]["manual_provider_stage_retry"]["maximum_actions_per_chain"] = 3
        unsigned = {name: value for name, value in manifest.items() if name != "manifest_sha256"}
        manifest["manifest_sha256"] = canonical_sha256(unsigned)
        with self.assertRaisesRegex(StateConflictError, "execution policy changed"):
            validate_qualification_manifest(manifest)
        manifest = _manifest()
        manifest["fixture_ancestry"][0]["sha256"] = "0" * 64
        unsigned = {name: value for name, value in manifest.items() if name != "manifest_sha256"}
        manifest["manifest_sha256"] = canonical_sha256(unsigned)
        with self.assertRaisesRegex(StateConflictError, "fixture ancestry changed"):
            validate_qualification_manifest(manifest)

    def test_historical_manifest_v22_remains_readable_without_standing_policy(self) -> None:
        manifest = _manifest()
        manifest.update(
            qualification_fixture_manifest_metadata(
                load_qualification_fixtures(HISTORICAL_FIXTURES_V19)
            )
        )
        manifest["fixture_set_sha256"] = bytes_sha256(HISTORICAL_FIXTURES_V19.read_bytes())
        manifest["schema_version"] = QUALIFICATION_MANIFEST_SCHEMA_V22
        del manifest["execution_policy"]["ordinary_standing_creator_policy"]
        unsigned = {name: value for name, value in manifest.items() if name != "manifest_sha256"}
        manifest["manifest_sha256"] = canonical_sha256(unsigned)
        validate_qualification_manifest(manifest)

        manifest["schema_version"] = QUALIFICATION_MANIFEST_SCHEMA_V23
        manifest["execution_policy"] = deepcopy(QUALIFICATION_EXECUTION_POLICY)
        unsigned = {name: value for name, value in manifest.items() if name != "manifest_sha256"}
        manifest["manifest_sha256"] = canonical_sha256(unsigned)
        validate_qualification_manifest(manifest)

        manifest.update(
            qualification_fixture_manifest_metadata(
                load_qualification_fixtures(HISTORICAL_FIXTURES_V20)
            )
        )
        manifest["fixture_set_sha256"] = bytes_sha256(HISTORICAL_FIXTURES_V20.read_bytes())
        manifest["schema_version"] = QUALIFICATION_MANIFEST_SCHEMA_V24
        unsigned = {name: value for name, value in manifest.items() if name != "manifest_sha256"}
        manifest["manifest_sha256"] = canonical_sha256(unsigned)
        validate_qualification_manifest(manifest)

        manifest.update(
            qualification_fixture_manifest_metadata(
                load_qualification_fixtures(HISTORICAL_FIXTURES_V21)
            )
        )
        manifest["fixture_set_sha256"] = bytes_sha256(HISTORICAL_FIXTURES_V21.read_bytes())
        manifest["schema_version"] = QUALIFICATION_MANIFEST_SCHEMA_V25
        unsigned = {name: value for name, value in manifest.items() if name != "manifest_sha256"}
        manifest["manifest_sha256"] = canonical_sha256(unsigned)
        validate_qualification_manifest(manifest)

        manifest.update(
            qualification_fixture_manifest_metadata(
                load_qualification_fixtures(HISTORICAL_FIXTURES_V22)
            )
        )
        manifest["fixture_set_sha256"] = bytes_sha256(HISTORICAL_FIXTURES_V22.read_bytes())
        manifest["schema_version"] = QUALIFICATION_MANIFEST_SCHEMA_V26
        unsigned = {name: value for name, value in manifest.items() if name != "manifest_sha256"}
        manifest["manifest_sha256"] = canonical_sha256(unsigned)
        validate_qualification_manifest(manifest)

        manifest.update(
            qualification_fixture_manifest_metadata(
                load_qualification_fixtures(HISTORICAL_FIXTURES_V23)
            )
        )
        manifest["fixture_set_sha256"] = bytes_sha256(HISTORICAL_FIXTURES_V23.read_bytes())
        manifest["schema_version"] = QUALIFICATION_MANIFEST_SCHEMA_V27
        unsigned = {name: value for name, value in manifest.items() if name != "manifest_sha256"}
        manifest["manifest_sha256"] = canonical_sha256(unsigned)
        validate_qualification_manifest(manifest)

        manifest.update(
            qualification_fixture_manifest_metadata(
                load_qualification_fixtures(HISTORICAL_FIXTURES_V24)
            )
        )
        manifest["fixture_set_sha256"] = bytes_sha256(HISTORICAL_FIXTURES_V24.read_bytes())
        manifest["schema_version"] = QUALIFICATION_MANIFEST_SCHEMA_V28
        unsigned = {name: value for name, value in manifest.items() if name != "manifest_sha256"}
        manifest["manifest_sha256"] = canonical_sha256(unsigned)
        validate_qualification_manifest(manifest)

    def test_v25_practical_fixtures_are_cumulatively_novel_and_history_remains_readable(
        self,
    ) -> None:
        legacy = load_qualification_fixtures(LEGACY_FIXTURES)
        historical_v2 = load_qualification_fixtures(HISTORICAL_FIXTURES_V2)
        historical_v3 = load_qualification_fixtures(HISTORICAL_FIXTURES_V3)
        historical_v4 = load_qualification_fixtures(HISTORICAL_FIXTURES_V4)
        historical_v5 = load_qualification_fixtures(HISTORICAL_FIXTURES_V5)
        historical_v6 = load_qualification_fixtures(HISTORICAL_FIXTURES_V6)
        historical_v7 = load_qualification_fixtures(HISTORICAL_FIXTURES_V7)
        historical_v8 = load_qualification_fixtures(HISTORICAL_FIXTURES_V8)
        historical_v9 = load_qualification_fixtures(HISTORICAL_FIXTURES_V9)
        historical_v10 = load_qualification_fixtures(HISTORICAL_FIXTURES_V10)
        historical_v11 = load_qualification_fixtures(HISTORICAL_FIXTURES_V11)
        historical_v12 = load_qualification_fixtures(HISTORICAL_FIXTURES_V12)
        historical_v13 = load_qualification_fixtures(HISTORICAL_FIXTURES_V13)
        historical_v14 = load_qualification_fixtures(HISTORICAL_FIXTURES_V14)
        historical_v15 = load_qualification_fixtures(HISTORICAL_FIXTURES_V15)
        historical_v16 = load_qualification_fixtures(HISTORICAL_FIXTURES_V16)
        historical_v17 = load_qualification_fixtures(HISTORICAL_FIXTURES_V17)
        historical_v18 = load_qualification_fixtures(HISTORICAL_FIXTURES_V18)
        historical_v19 = load_qualification_fixtures(HISTORICAL_FIXTURES_V19)
        historical_v20 = load_qualification_fixtures(HISTORICAL_FIXTURES_V20)
        historical_v21 = load_qualification_fixtures(HISTORICAL_FIXTURES_V21)
        historical_v22 = load_qualification_fixtures(HISTORICAL_FIXTURES_V22)
        historical_v23 = load_qualification_fixtures(HISTORICAL_FIXTURES_V23)
        historical_v24 = load_qualification_fixtures(HISTORICAL_FIXTURES_V24)
        stress = load_qualification_fixtures(FIXTURES)
        self.assertEqual(len(legacy), 30)
        self.assertEqual(len(historical_v2), 30)
        self.assertEqual(len(historical_v3), 30)
        self.assertEqual(len(historical_v4), 30)
        self.assertEqual(len(historical_v5), 30)
        self.assertEqual(len(historical_v6), 30)
        self.assertEqual(len(historical_v7), 30)
        self.assertEqual(len(historical_v8), 30)
        self.assertEqual(len(historical_v9), 30)
        self.assertEqual(len(historical_v10), 30)
        self.assertEqual(len(historical_v11), 30)
        self.assertEqual(len(historical_v12), 30)
        self.assertEqual(len(historical_v13), 30)
        self.assertEqual(len(historical_v14), 30)
        self.assertEqual(len(historical_v15), 30)
        self.assertEqual(len(historical_v16), 30)
        self.assertEqual(len(historical_v17), 30)
        self.assertEqual(len(historical_v18), 30)
        self.assertEqual(len(historical_v19), 30)
        self.assertEqual(len(historical_v20), 30)
        self.assertEqual(len(historical_v21), 30)
        self.assertEqual(len(historical_v22), 30)
        self.assertEqual(len(historical_v23), 30)
        self.assertEqual(len(historical_v24), 30)
        self.assertEqual(len(stress), 30)
        self.assertTrue(all(type(value) is QualificationFixtureV1 for value in legacy))
        self.assertTrue(all(type(value) is QualificationFixtureV2 for value in historical_v2))
        self.assertTrue(all(type(value) is QualificationFixtureV3 for value in historical_v3))
        self.assertTrue(all(type(value) is QualificationFixtureV4 for value in historical_v4))
        self.assertTrue(all(type(value) is QualificationFixtureV5 for value in historical_v5))
        self.assertTrue(all(type(value) is QualificationFixtureV6 for value in historical_v6))
        self.assertTrue(all(type(value) is QualificationFixtureV7 for value in historical_v7))
        self.assertTrue(all(type(value) is QualificationFixtureV8 for value in historical_v8))
        self.assertTrue(all(type(value) is QualificationFixtureV9 for value in historical_v9))
        self.assertTrue(all(type(value) is QualificationFixtureV10 for value in historical_v10))
        self.assertTrue(all(type(value) is QualificationFixtureV11 for value in historical_v11))
        self.assertTrue(all(type(value) is QualificationFixtureV12 for value in historical_v12))
        self.assertTrue(all(type(value) is QualificationFixtureV13 for value in historical_v13))
        self.assertTrue(all(type(value) is QualificationFixtureV14 for value in historical_v14))
        self.assertTrue(all(type(value) is QualificationFixtureV15 for value in historical_v15))
        self.assertTrue(all(type(value) is QualificationFixtureV16 for value in historical_v16))
        self.assertTrue(all(type(value) is QualificationFixtureV17 for value in historical_v17))
        self.assertTrue(all(type(value) is QualificationFixtureV18 for value in historical_v18))
        self.assertTrue(all(type(value) is QualificationFixtureV19 for value in historical_v19))
        self.assertTrue(all(type(value) is QualificationFixtureV20 for value in historical_v20))
        self.assertTrue(all(type(value) is QualificationFixtureV21 for value in historical_v21))
        self.assertTrue(all(type(value) is QualificationFixtureV22 for value in historical_v22))
        self.assertTrue(all(type(value) is QualificationFixtureV23 for value in historical_v23))
        self.assertTrue(all(type(value) is QualificationFixtureV24 for value in historical_v24))
        self.assertTrue(all(type(value) is QualificationFixtureV25 for value in stress))
        self.assertEqual(
            [value.fixture_id for value in stress],
            [
                *(f"backend-ordinary-{index:02d}" for index in range(1, 6)),
                *(f"backend-adult-{index:02d}" for index in range(1, 6)),
                *(f"backend-ordinary-{index:02d}" for index in range(6, 11)),
                *(f"backend-adult-{index:02d}" for index in range(6, 11)),
                *(f"sillytavern-ordinary-{index:02d}" for index in range(1, 4)),
                *(f"sillytavern-adult-{index:02d}" for index in range(1, 4)),
                *(f"sillytavern-ordinary-{index:02d}" for index in range(4, 6)),
                *(f"sillytavern-adult-{index:02d}" for index in range(4, 6)),
            ],
        )
        self.assertEqual(
            [value.novelty_id for value in stress],
            [
                "stress:v25_be_practical_philately_badge_registry",
                "stress:v25_be_practical_rail_signal_contradictory_work_order",
                "stress:v25_be_practical_textiles_absent_conservator_note",
                "stress:v25_be_practical_aviation_recording_custody",
                "stress:v25_be_practical_sculpture_scan_not_choice",
                "stress:v25_be_practical_adult_aquarium_adjacent_script",
                "stress:v25_be_practical_adult_skin_temperature_not_consent",
                "stress:v25_be_practical_adult_exhibition_booking_not_consent",
                "stress:v25_be_practical_adult_water_pressure_alarm_withdrawal",
                "stress:v25_be_practical_adult_aquarium_audio_inventory_close",
                "stress:v25_be_practical_railway_crossauthority_credential",
                "stress:v25_be_practical_photogrammetry_remote_crate_scan",
                "stress:v25_be_practical_textile_archive_absent_registrar",
                "stress:v25_be_practical_future_flight_recording",
                "stress:v25_be_practical_generated_voice_costume_release",
                "stress:v25_be_practical_adult_aviation_exhibit_script",
                "stress:v25_be_practical_adult_respiration_monitor_not_consent",
                "stress:v25_be_practical_adult_aviation_permit_window",
                "stress:v25_be_practical_adult_hangar_alarm_withdrawal",
                "stress:v25_be_practical_adult_aviation_recording_close",
                "stress:v25_st_practical_costume_ticket_injection",
                "stress:v25_st_practical_costume_scan_not_choice",
                "stress:v25_st_practical_costume_archive_false_history",
                "stress:v25_st_practical_adult_wardrobe_script",
                "stress:v25_st_practical_adult_vibration_sensor_not_consent",
                "stress:v25_st_practical_adult_smoke_alarm_withdrawal",
                "stress:v25_st_practical_census_identity_age_forgery",
                "stress:v25_st_practical_genealogy_relation_remote_label",
                "stress:v25_st_practical_adult_theater_deadline_pressure",
                "stress:v25_st_practical_adult_air_quality_alarm_withdrawal",
            ],
        )
        self.assertEqual(
            [value.stress_tags for value in stress],
            [value.stress_tags for value in historical_v24],
        )
        self.assertEqual(
            [
                (
                    value.phase.value,
                    value.initial_route.value,
                    value.expected_route.value,
                    value.expected_next_route.value,
                    value.adult_craft_mode,
                )
                for value in stress
            ],
            [
                *(("backend", "ordinary", "ordinary", "ordinary", "off") for _ in range(5)),
                ("backend", "ordinary", "adult", "adult", "ex"),
                *(("backend", "adult", "adult", "adult", "ex") for _ in range(3)),
                ("backend", "adult", "adult", "ordinary", "ex"),
                *(("backend", "ordinary", "ordinary", "ordinary", "off") for _ in range(5)),
                ("backend", "ordinary", "adult", "adult", "ex"),
                *(("backend", "adult", "adult", "adult", "ex") for _ in range(3)),
                ("backend", "adult", "adult", "ordinary", "ex"),
                *(("sillytavern", "ordinary", "ordinary", "ordinary", "off") for _ in range(3)),
                ("sillytavern", "ordinary", "adult", "adult", "ex"),
                *(("sillytavern", "adult", "adult", "adult", "ex") for _ in range(1)),
                ("sillytavern", "adult", "adult", "ordinary", "ex"),
                *(("sillytavern", "ordinary", "ordinary", "ordinary", "off") for _ in range(2)),
                ("sillytavern", "ordinary", "adult", "adult", "ex"),
                ("sillytavern", "adult", "adult", "ordinary", "ex"),
            ],
        )
        self.assertEqual(len({value.user_source for value in stress}), 30)
        self.assertEqual(len({value.novelty_id for value in stress}), 30)
        current_sources = {value.user_source for value in stress}
        self.assertFalse({value.user_source for value in legacy} & current_sources)
        self.assertFalse({value.user_source for value in historical_v2} & current_sources)
        self.assertFalse({value.user_source for value in historical_v3} & current_sources)
        self.assertFalse({value.user_source for value in historical_v4} & current_sources)
        self.assertFalse({value.user_source for value in historical_v5} & current_sources)
        self.assertFalse({value.user_source for value in historical_v6} & current_sources)
        self.assertFalse({value.user_source for value in historical_v7} & current_sources)
        self.assertFalse({value.user_source for value in historical_v8} & current_sources)
        self.assertFalse({value.user_source for value in historical_v9} & current_sources)
        self.assertFalse({value.user_source for value in historical_v10} & current_sources)
        self.assertFalse({value.user_source for value in historical_v11} & current_sources)
        self.assertFalse({value.user_source for value in historical_v12} & current_sources)
        self.assertFalse({value.user_source for value in historical_v13} & current_sources)
        self.assertFalse({value.user_source for value in historical_v14} & current_sources)
        self.assertFalse({value.user_source for value in historical_v15} & current_sources)
        self.assertFalse({value.user_source for value in historical_v16} & current_sources)
        self.assertFalse({value.user_source for value in historical_v17} & current_sources)
        self.assertFalse({value.user_source for value in historical_v18} & current_sources)
        self.assertFalse({value.user_source for value in historical_v19} & current_sources)
        self.assertFalse({value.user_source for value in historical_v20} & current_sources)
        self.assertFalse({value.user_source for value in historical_v21} & current_sources)
        self.assertFalse({value.user_source for value in historical_v22} & current_sources)
        self.assertFalse({value.user_source for value in historical_v23} & current_sources)
        self.assertFalse({value.user_source for value in historical_v24} & current_sources)
        self.assertFalse(
            {value.novelty_id for value in historical_v2} & {value.novelty_id for value in stress}
        )
        self.assertFalse(
            {value.novelty_id for value in historical_v3} & {value.novelty_id for value in stress}
        )
        self.assertFalse(
            {value.novelty_id for value in historical_v4} & {value.novelty_id for value in stress}
        )
        self.assertFalse(
            {value.novelty_id for value in historical_v5} & {value.novelty_id for value in stress}
        )
        self.assertFalse(
            {value.novelty_id for value in historical_v6} & {value.novelty_id for value in stress}
        )
        self.assertFalse(
            {value.novelty_id for value in historical_v7} & {value.novelty_id for value in stress}
        )
        self.assertFalse(
            {value.novelty_id for value in historical_v8} & {value.novelty_id for value in stress}
        )
        self.assertFalse(
            {value.novelty_id for value in historical_v9} & {value.novelty_id for value in stress}
        )
        self.assertFalse(
            {value.novelty_id for value in historical_v10} & {value.novelty_id for value in stress}
        )
        self.assertFalse(
            {value.novelty_id for value in historical_v11} & {value.novelty_id for value in stress}
        )
        self.assertFalse(
            {value.novelty_id for value in historical_v12} & {value.novelty_id for value in stress}
        )
        self.assertFalse(
            {value.novelty_id for value in historical_v13} & {value.novelty_id for value in stress}
        )
        self.assertFalse(
            {value.novelty_id for value in historical_v14} & {value.novelty_id for value in stress}
        )
        self.assertFalse(
            {value.novelty_id for value in historical_v15} & {value.novelty_id for value in stress}
        )
        self.assertFalse(
            {value.novelty_id for value in historical_v16} & {value.novelty_id for value in stress}
        )
        self.assertFalse(
            {value.novelty_id for value in historical_v17} & {value.novelty_id for value in stress}
        )
        self.assertFalse(
            {value.novelty_id for value in historical_v18} & {value.novelty_id for value in stress}
        )
        self.assertFalse(
            {value.novelty_id for value in historical_v19} & {value.novelty_id for value in stress}
        )
        self.assertFalse(
            {value.novelty_id for value in historical_v20} & {value.novelty_id for value in stress}
        )
        self.assertFalse(
            {value.novelty_id for value in historical_v21} & {value.novelty_id for value in stress}
        )
        self.assertFalse(
            {value.novelty_id for value in historical_v22} & {value.novelty_id for value in stress}
        )
        self.assertFalse(
            {value.novelty_id for value in historical_v23} & {value.novelty_id for value in stress}
        )
        self.assertFalse(
            {value.novelty_id for value in historical_v24} & {value.novelty_id for value in stress}
        )
        self.assertEqual(
            DEFAULT_INITIAL_PRESENT_CHARACTER_IDS,
            ("character:sakura_hanezawa",),
        )
        self.assertTrue(all("Sakura" in value.user_source for value in stress))
        adult_sources = [
            value.user_source.lower()
            for value in stress
            if value.expected_route is QualificationRoute.ADULT
        ]
        self.assertEqual(len(adult_sources), 15)
        self.assertTrue(all("adult" in value for value in adult_sources))
        self.assertTrue(all("current capacity" in value for value in adult_sources))
        self.assertTrue(all("current explicit consent" in value for value in adult_sources))
        self.assertTrue(all("withdrawal is immediate" in value for value in adult_sources))
        self.assertTrue(
            all(
                re.search(r"both (?:adults )?(?:are|remain) free and able to stop", value)
                is not None
                for value in adult_sources
            )
        )
        self.assertTrue(all("sakura" in value for value in adult_sources))
        self.assertTrue(
            all("plausible immediate physical actions" in value for value in adult_sources)
        )
        self.assertTrue(all("limited in-scene dialogue" in value for value in adult_sources))
        self.assertTrue(
            all("bodily response does not establish consent" in value for value in adult_sources)
        )
        self.assertTrue(
            all(
                "current consent never authorizes an adjacent act" in value
                for value in adult_sources
            )
        )
        self.assertTrue(all("provisional continuity" in value for value in adult_sources))
        ordinary_sources = [
            value.user_source.lower()
            for value in stress
            if value.expected_route is QualificationRoute.ORDINARY
        ]
        self.assertTrue(all("do not invent ted's dialogue" in value for value in ordinary_sources))
        self.assertTrue(
            all(
                "thoughts, feelings, memories, or private state" in value
                for value in ordinary_sources
            )
        )
        self.assertTrue(all("provisional continuity" in value for value in ordinary_sources))
        self.assertTrue(
            all("conversational floor available to ted" in value for value in ordinary_sources)
        )
        stress_sources = [value.user_source.lower() for value in stress]
        self.assertTrue(
            all("fully closed hanezawa front door" in value for value in stress_sources)
        )
        self.assertTrue(all("compatible claimant activity" in value for value in stress_sources))
        self.assertTrue(all("short after" in value for value in stress_sources))
        forbidden_prompt_markers = ("skill://", "skill.md", "app://", "mcp://")
        self.assertTrue(
            all(
                marker not in value.user_source.lower()
                for value in stress
                for marker in forbidden_prompt_markers
            )
        )
        self.assertTrue(all("$" not in value.user_source for value in stress))
        ancestral_sources = [
            value.user_source
            for generation in (
                legacy,
                historical_v2,
                historical_v3,
                historical_v4,
                historical_v5,
                historical_v6,
                historical_v7,
                historical_v8,
                historical_v9,
                historical_v10,
                historical_v11,
                historical_v12,
                historical_v13,
                historical_v14,
                historical_v15,
                historical_v16,
                historical_v17,
                historical_v18,
                historical_v19,
                historical_v20,
                historical_v21,
                historical_v22,
                historical_v23,
                historical_v24,
            )
            for value in generation
        ]
        ancestral_source_hashes = {text_sha256(value) for value in ancestral_sources}
        current_source_hashes = {text_sha256(value.user_source) for value in stress}
        self.assertEqual(len(ancestral_source_hashes), 720)
        self.assertEqual(len(current_source_hashes), 30)
        self.assertFalse(ancestral_source_hashes & current_source_hashes)

        metadata = qualification_fixture_manifest_metadata(stress)
        self.assertEqual(
            metadata["novelty_set_sha256"],
            "fc1279492c2b83f1ed5cf0a3a1280950dc58c844f2cdcf4219670e71d07ed184",
        )
        self.assertEqual(
            bytes_sha256(FIXTURES.read_bytes()),
            "9a8557f6ec56fac0ce16d21670d880852e467d0f161e5edea49b6d80b90605c1",
        )
        self.assertEqual(
            metadata["fixture_baseline"]["path"],
            HISTORICAL_FIXTURES_V24.name,
        )
        self.assertEqual(
            metadata["stress_coverage"],
            {
                "authority_spoof": 3,
                "character_autonomy": 10,
                "coercive_pressure": 1,
                "consent_ambiguity": 9,
                "consent_withdrawal": 4,
                "external_interruption": 4,
                "false_continuity": 10,
                "identity_conflict": 3,
                "instruction_injection": 1,
                "nonverbal_consent": 5,
                "offscreen_cast": 2,
                "privacy_boundary": 13,
                "protected_user_custody": 14,
                "recording_custody": 7,
                "roleplay_canon": 1,
                "route_transition": 8,
                "synthetic_media": 3,
                "temporal_conflict": 4,
                "untrusted_metadata": 18,
            },
        )
        self.assertEqual(
            [value["path"] for value in metadata["fixture_ancestry"]],
            [
                LEGACY_FIXTURES.name,
                HISTORICAL_FIXTURES_V2.name,
                HISTORICAL_FIXTURES_V3.name,
                HISTORICAL_FIXTURES_V4.name,
                HISTORICAL_FIXTURES_V5.name,
                HISTORICAL_FIXTURES_V6.name,
                HISTORICAL_FIXTURES_V7.name,
                HISTORICAL_FIXTURES_V8.name,
                HISTORICAL_FIXTURES_V9.name,
                HISTORICAL_FIXTURES_V10.name,
                HISTORICAL_FIXTURES_V11.name,
                HISTORICAL_FIXTURES_V12.name,
                HISTORICAL_FIXTURES_V13.name,
                HISTORICAL_FIXTURES_V14.name,
                HISTORICAL_FIXTURES_V15.name,
                HISTORICAL_FIXTURES_V16.name,
                HISTORICAL_FIXTURES_V17.name,
                HISTORICAL_FIXTURES_V18.name,
                HISTORICAL_FIXTURES_V19.name,
                HISTORICAL_FIXTURES_V20.name,
                HISTORICAL_FIXTURES_V21.name,
                HISTORICAL_FIXTURES_V22.name,
                HISTORICAL_FIXTURES_V23.name,
                HISTORICAL_FIXTURES_V24.name,
            ],
        )
        self.assertEqual(entrypoint.DEFAULT_FIXTURES, FIXTURES)

    def test_v2_stress_fixture_contract_fails_closed(self) -> None:
        source = json.loads(HISTORICAL_FIXTURES_V2.read_text(encoding="utf-8"))
        cases: dict[str, Callable[[dict[str, Any]], None]] = {
            "baseline hash": lambda value: value.__setitem__("baseline_fixture_sha256", "0" * 64),
            "novelty identity": lambda value: value["fixtures"][1].__setitem__(
                "novelty_id", value["fixtures"][0]["novelty_id"]
            ),
            "source": lambda value: value["fixtures"][1].__setitem__(
                "user_source", value["fixtures"][0]["user_source"]
            ),
            "stress tags": lambda value: value["fixtures"][0].__setitem__(
                "stress_tags", ["privacy_boundary", "privacy_boundary"]
            ),
            "unknown tag": lambda value: value["fixtures"][0].__setitem__(
                "stress_tags", ["privacy_boundary", "unknown_stress"]
            ),
            "missing boundary": lambda value: value["fixtures"][0].__setitem__(
                "stress_tags", ["authority_spoof", "synthetic_media"]
            ),
            "unsupported cast": lambda value: value["fixtures"][0].__setitem__(
                "user_source", value["fixtures"][0]["user_source"].replace("Sakura", "Enne")
            ),
            "missing bound actor": lambda value: value["fixtures"][0].__setitem__(
                "user_source", value["fixtures"][0]["user_source"].replace("Sakura", "the host")
            ),
        }
        for label, mutate in cases.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                shutil.copy2(LEGACY_FIXTURES, root / LEGACY_FIXTURES.name)
                changed = deepcopy(source)
                mutate(changed)
                candidate = root / HISTORICAL_FIXTURES_V2.name
                candidate.write_text(json.dumps(changed), encoding="utf-8")
                with self.assertRaises((ContractValidationError, StateConflictError)):
                    load_qualification_fixtures(candidate)

    def test_v2_stress_fixture_rejects_a_self_consistent_noncanonical_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            legacy = json.loads(LEGACY_FIXTURES.read_text(encoding="utf-8"))
            legacy["fixtures"][0]["user_source"] += " Altered baseline marker."
            legacy_path = root / LEGACY_FIXTURES.name
            legacy_path.write_text(json.dumps(legacy), encoding="utf-8")
            stress = json.loads(HISTORICAL_FIXTURES_V2.read_text(encoding="utf-8"))
            stress["baseline_fixture_sha256"] = bytes_sha256(legacy_path.read_bytes())
            stress_path = root / HISTORICAL_FIXTURES_V2.name
            stress_path.write_text(json.dumps(stress), encoding="utf-8")
            with self.assertRaisesRegex(ContractValidationError, "baseline hash"):
                load_qualification_fixtures(stress_path)

    def test_v3_stress_fixture_contract_and_cumulative_ancestry_fail_closed(self) -> None:
        source = json.loads(HISTORICAL_FIXTURES_V3.read_text(encoding="utf-8"))
        historical = json.loads(HISTORICAL_FIXTURES_V2.read_text(encoding="utf-8"))
        cases: dict[str, Callable[[dict[str, Any]], None]] = {
            "baseline hash": lambda value: value.__setitem__("baseline_fixture_sha256", "0" * 64),
            "ancestry hash": lambda value: value["baseline_ancestry"][0].__setitem__(
                "sha256", "0" * 64
            ),
            "ancestry order": lambda value: value.__setitem__(
                "baseline_ancestry", list(reversed(value["baseline_ancestry"]))
            ),
            "novelty identity": lambda value: value["fixtures"][1].__setitem__(
                "novelty_id", value["fixtures"][0]["novelty_id"]
            ),
            "source": lambda value: value["fixtures"][1].__setitem__(
                "user_source", value["fixtures"][0]["user_source"]
            ),
            "stress tags": lambda value: value["fixtures"][0].__setitem__(
                "stress_tags", ["privacy_boundary", "privacy_boundary"]
            ),
            "unsupported cast": lambda value: value["fixtures"][0].__setitem__(
                "user_source", value["fixtures"][0]["user_source"].replace("Sakura", "Enne")
            ),
            "missing bound actor": lambda value: value["fixtures"][0].__setitem__(
                "user_source", value["fixtures"][0]["user_source"].replace("Sakura", "the host")
            ),
            "v2 source reuse": lambda value: value["fixtures"][0].__setitem__(
                "user_source", historical["fixtures"][0]["user_source"]
            ),
            "v2 novelty reuse": lambda value: value["fixtures"][0].__setitem__(
                "novelty_id", historical["fixtures"][0]["novelty_id"]
            ),
        }
        for label, mutate in cases.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                shutil.copy2(LEGACY_FIXTURES, root / LEGACY_FIXTURES.name)
                shutil.copy2(
                    HISTORICAL_FIXTURES_V2,
                    root / HISTORICAL_FIXTURES_V2.name,
                )
                changed = deepcopy(source)
                mutate(changed)
                candidate = root / HISTORICAL_FIXTURES_V3.name
                candidate.write_text(json.dumps(changed), encoding="utf-8")
                with self.assertRaises((ContractValidationError, StateConflictError)):
                    load_qualification_fixtures(candidate)

    def test_v3_rejects_a_self_consistent_noncanonical_v2_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            shutil.copy2(LEGACY_FIXTURES, root / LEGACY_FIXTURES.name)
            historical = json.loads(HISTORICAL_FIXTURES_V2.read_text(encoding="utf-8"))
            historical["fixtures"][0]["user_source"] += " Altered V2 marker."
            historical_path = root / HISTORICAL_FIXTURES_V2.name
            historical_path.write_text(json.dumps(historical), encoding="utf-8")
            changed_hash = bytes_sha256(historical_path.read_bytes())
            current = json.loads(HISTORICAL_FIXTURES_V3.read_text(encoding="utf-8"))
            current["baseline_fixture_sha256"] = changed_hash
            current["baseline_ancestry"][1]["sha256"] = changed_hash
            current_path = root / HISTORICAL_FIXTURES_V3.name
            current_path.write_text(json.dumps(current), encoding="utf-8")
            with self.assertRaisesRegex(ContractValidationError, "baseline hash"):
                load_qualification_fixtures(current_path)

    def test_v4_stress_fixture_contract_and_cumulative_ancestry_fail_closed(self) -> None:
        source = json.loads(HISTORICAL_FIXTURES_V4.read_text(encoding="utf-8"))
        historical_v3 = json.loads(HISTORICAL_FIXTURES_V3.read_text(encoding="utf-8"))
        cases: dict[str, Callable[[dict[str, Any]], None]] = {
            "baseline hash": lambda value: value.__setitem__("baseline_fixture_sha256", "0" * 64),
            "ancestry hash": lambda value: value["baseline_ancestry"][2].__setitem__(
                "sha256", "0" * 64
            ),
            "ancestry order": lambda value: value.__setitem__(
                "baseline_ancestry", list(reversed(value["baseline_ancestry"]))
            ),
            "novelty identity": lambda value: value["fixtures"][1].__setitem__(
                "novelty_id", value["fixtures"][0]["novelty_id"]
            ),
            "source": lambda value: value["fixtures"][1].__setitem__(
                "user_source", value["fixtures"][0]["user_source"]
            ),
            "unsupported cast": lambda value: value["fixtures"][0].__setitem__(
                "user_source", value["fixtures"][0]["user_source"].replace("Sakura", "Enne")
            ),
            "v3 source reuse": lambda value: value["fixtures"][0].__setitem__(
                "user_source", historical_v3["fixtures"][0]["user_source"]
            ),
            "v3 novelty reuse": lambda value: value["fixtures"][0].__setitem__(
                "novelty_id", historical_v3["fixtures"][0]["novelty_id"]
            ),
        }
        for label, mutate in cases.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                for ancestor in (
                    LEGACY_FIXTURES,
                    HISTORICAL_FIXTURES_V2,
                    HISTORICAL_FIXTURES_V3,
                ):
                    shutil.copy2(ancestor, root / ancestor.name)
                changed = deepcopy(source)
                mutate(changed)
                candidate = root / HISTORICAL_FIXTURES_V4.name
                candidate.write_text(json.dumps(changed), encoding="utf-8")
                with self.assertRaises((ContractValidationError, StateConflictError)):
                    load_qualification_fixtures(candidate)

    def test_v4_rejects_a_self_consistent_noncanonical_v3_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            shutil.copy2(LEGACY_FIXTURES, root / LEGACY_FIXTURES.name)
            shutil.copy2(HISTORICAL_FIXTURES_V2, root / HISTORICAL_FIXTURES_V2.name)
            historical = json.loads(HISTORICAL_FIXTURES_V3.read_text(encoding="utf-8"))
            historical["fixtures"][0]["user_source"] += " Altered V3 marker."
            historical_path = root / HISTORICAL_FIXTURES_V3.name
            historical_path.write_text(json.dumps(historical), encoding="utf-8")
            changed_hash = bytes_sha256(historical_path.read_bytes())
            current = json.loads(HISTORICAL_FIXTURES_V4.read_text(encoding="utf-8"))
            current["baseline_fixture_sha256"] = changed_hash
            current["baseline_ancestry"][2]["sha256"] = changed_hash
            current_path = root / HISTORICAL_FIXTURES_V4.name
            current_path.write_text(json.dumps(current), encoding="utf-8")
            with self.assertRaisesRegex(ContractValidationError, "baseline hash"):
                load_qualification_fixtures(current_path)

    def test_v5_stress_fixture_contract_and_cumulative_ancestry_fail_closed(self) -> None:
        source = json.loads(HISTORICAL_FIXTURES_V5.read_text(encoding="utf-8"))
        historical_v4 = json.loads(HISTORICAL_FIXTURES_V4.read_text(encoding="utf-8"))
        cases: dict[str, Callable[[dict[str, Any]], None]] = {
            "baseline hash": lambda value: value.__setitem__("baseline_fixture_sha256", "0" * 64),
            "ancestry hash": lambda value: value["baseline_ancestry"][3].__setitem__(
                "sha256", "0" * 64
            ),
            "ancestry order": lambda value: value.__setitem__(
                "baseline_ancestry", list(reversed(value["baseline_ancestry"]))
            ),
            "novelty identity": lambda value: value["fixtures"][1].__setitem__(
                "novelty_id", value["fixtures"][0]["novelty_id"]
            ),
            "source": lambda value: value["fixtures"][1].__setitem__(
                "user_source", value["fixtures"][0]["user_source"]
            ),
            "unsupported cast": lambda value: value["fixtures"][0].__setitem__(
                "user_source", value["fixtures"][0]["user_source"].replace("Sakura", "Enne")
            ),
            "v4 source reuse": lambda value: value["fixtures"][0].__setitem__(
                "user_source", historical_v4["fixtures"][0]["user_source"]
            ),
            "v4 novelty reuse": lambda value: value["fixtures"][0].__setitem__(
                "novelty_id", historical_v4["fixtures"][0]["novelty_id"]
            ),
        }
        for label, mutate in cases.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                for ancestor in (
                    LEGACY_FIXTURES,
                    HISTORICAL_FIXTURES_V2,
                    HISTORICAL_FIXTURES_V3,
                    HISTORICAL_FIXTURES_V4,
                ):
                    shutil.copy2(ancestor, root / ancestor.name)
                changed = deepcopy(source)
                mutate(changed)
                candidate = root / HISTORICAL_FIXTURES_V5.name
                candidate.write_text(json.dumps(changed), encoding="utf-8")
                with self.assertRaises((ContractValidationError, StateConflictError)):
                    load_qualification_fixtures(candidate)

    def test_v5_rejects_a_self_consistent_noncanonical_v4_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            shutil.copy2(LEGACY_FIXTURES, root / LEGACY_FIXTURES.name)
            shutil.copy2(HISTORICAL_FIXTURES_V2, root / HISTORICAL_FIXTURES_V2.name)
            shutil.copy2(HISTORICAL_FIXTURES_V3, root / HISTORICAL_FIXTURES_V3.name)
            historical = json.loads(HISTORICAL_FIXTURES_V4.read_text(encoding="utf-8"))
            historical["fixtures"][0]["user_source"] += " Altered V4 marker."
            historical_path = root / HISTORICAL_FIXTURES_V4.name
            historical_path.write_text(json.dumps(historical), encoding="utf-8")
            changed_hash = bytes_sha256(historical_path.read_bytes())
            current = json.loads(HISTORICAL_FIXTURES_V5.read_text(encoding="utf-8"))
            current["baseline_fixture_sha256"] = changed_hash
            current["baseline_ancestry"][3]["sha256"] = changed_hash
            current_path = root / HISTORICAL_FIXTURES_V5.name
            current_path.write_text(json.dumps(current), encoding="utf-8")
            with self.assertRaisesRegex(ContractValidationError, "baseline hash"):
                load_qualification_fixtures(current_path)

    def test_v25_practical_fixture_contract_and_cumulative_ancestry_fail_closed(self) -> None:
        source = json.loads(FIXTURES.read_text(encoding="utf-8"))
        historical_v24 = json.loads(HISTORICAL_FIXTURES_V24.read_text(encoding="utf-8"))
        cases: dict[str, Callable[[dict[str, Any]], None]] = {
            "baseline hash": lambda value: value.__setitem__("baseline_fixture_sha256", "0" * 64),
            "ancestry hash": lambda value: value["baseline_ancestry"][23].__setitem__(
                "sha256", "0" * 64
            ),
            "ancestry order": lambda value: value.__setitem__(
                "baseline_ancestry", list(reversed(value["baseline_ancestry"]))
            ),
            "novelty identity": lambda value: value["fixtures"][1].__setitem__(
                "novelty_id", value["fixtures"][0]["novelty_id"]
            ),
            "source": lambda value: value["fixtures"][1].__setitem__(
                "user_source", value["fixtures"][0]["user_source"]
            ),
            "unsupported cast": lambda value: value["fixtures"][0].__setitem__(
                "user_source", value["fixtures"][0]["user_source"].replace("Sakura", "Enne")
            ),
            "v24 source reuse": lambda value: value["fixtures"][0].__setitem__(
                "user_source", historical_v24["fixtures"][0]["user_source"]
            ),
            "v24 novelty reuse": lambda value: value["fixtures"][0].__setitem__(
                "novelty_id", historical_v24["fixtures"][0]["novelty_id"]
            ),
        }
        for label, mutate in cases.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                for ancestor in (
                    LEGACY_FIXTURES,
                    HISTORICAL_FIXTURES_V2,
                    HISTORICAL_FIXTURES_V3,
                    HISTORICAL_FIXTURES_V4,
                    HISTORICAL_FIXTURES_V5,
                    HISTORICAL_FIXTURES_V6,
                    HISTORICAL_FIXTURES_V7,
                    HISTORICAL_FIXTURES_V8,
                    HISTORICAL_FIXTURES_V9,
                    HISTORICAL_FIXTURES_V10,
                    HISTORICAL_FIXTURES_V11,
                    HISTORICAL_FIXTURES_V12,
                    HISTORICAL_FIXTURES_V13,
                    HISTORICAL_FIXTURES_V14,
                    HISTORICAL_FIXTURES_V15,
                    HISTORICAL_FIXTURES_V16,
                    HISTORICAL_FIXTURES_V17,
                    HISTORICAL_FIXTURES_V18,
                    HISTORICAL_FIXTURES_V19,
                    HISTORICAL_FIXTURES_V20,
                    HISTORICAL_FIXTURES_V21,
                    HISTORICAL_FIXTURES_V22,
                    HISTORICAL_FIXTURES_V23,
                    HISTORICAL_FIXTURES_V24,
                ):
                    shutil.copy2(ancestor, root / ancestor.name)
                changed = deepcopy(source)
                mutate(changed)
                candidate = root / FIXTURES.name
                candidate.write_text(json.dumps(changed), encoding="utf-8")
                with self.assertRaises((ContractValidationError, StateConflictError)):
                    load_qualification_fixtures(candidate)

    def test_v25_rejects_a_self_consistent_noncanonical_v24_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for ancestor in (
                LEGACY_FIXTURES,
                HISTORICAL_FIXTURES_V2,
                HISTORICAL_FIXTURES_V3,
                HISTORICAL_FIXTURES_V4,
                HISTORICAL_FIXTURES_V5,
                HISTORICAL_FIXTURES_V6,
                HISTORICAL_FIXTURES_V7,
                HISTORICAL_FIXTURES_V8,
                HISTORICAL_FIXTURES_V9,
                HISTORICAL_FIXTURES_V10,
                HISTORICAL_FIXTURES_V11,
                HISTORICAL_FIXTURES_V12,
                HISTORICAL_FIXTURES_V13,
                HISTORICAL_FIXTURES_V14,
                HISTORICAL_FIXTURES_V15,
                HISTORICAL_FIXTURES_V16,
                HISTORICAL_FIXTURES_V17,
                HISTORICAL_FIXTURES_V18,
                HISTORICAL_FIXTURES_V19,
                HISTORICAL_FIXTURES_V20,
                HISTORICAL_FIXTURES_V21,
                HISTORICAL_FIXTURES_V22,
                HISTORICAL_FIXTURES_V23,
            ):
                shutil.copy2(ancestor, root / ancestor.name)
            historical = json.loads(HISTORICAL_FIXTURES_V24.read_text(encoding="utf-8"))
            historical["fixtures"][0]["user_source"] += " Altered V24 marker."
            historical_path = root / HISTORICAL_FIXTURES_V24.name
            historical_path.write_text(json.dumps(historical), encoding="utf-8")
            changed_hash = bytes_sha256(historical_path.read_bytes())
            current = json.loads(FIXTURES.read_text(encoding="utf-8"))
            current["baseline_fixture_sha256"] = changed_hash
            current["baseline_ancestry"][23]["sha256"] = changed_hash
            current_path = root / FIXTURES.name
            current_path.write_text(json.dumps(current), encoding="utf-8")
            with self.assertRaisesRegex(ContractValidationError, "baseline hash"):
                load_qualification_fixtures(current_path)

    def test_spent_v25_fixture_manifest_rejects_fixture_or_novelty_reuse(self) -> None:
        prior = build_qualification_manifest(
            repository_root=ROOT,
            qualification_id="qualification-prior-stress-20260811",
            source_commit="a" * 40,
            source_tree="b" * 40,
            fixture_path=FIXTURES,
            repository_artifacts={"fixture": (FIXTURES,)},
            external_artifacts={"external_fixture": (FIXTURES,)},
        )
        with self.assertRaisesRegex(StateConflictError, "already frozen"):
            build_qualification_manifest(
                repository_root=ROOT,
                qualification_id="qualification-next-stress-20260811",
                source_commit="a" * 40,
                source_tree="b" * 40,
                fixture_path=FIXTURES,
                repository_artifacts={"fixture": (FIXTURES,)},
                external_artifacts={"external_fixture": (FIXTURES,)},
                spent_manifests=(prior,),
            )
        prior_with_different_set = deepcopy(prior)
        prior_with_different_set["fixture_set_sha256"] = "d" * 64
        unsigned = {
            key: value
            for key, value in prior_with_different_set.items()
            if key != "manifest_sha256"
        }
        prior_with_different_set["manifest_sha256"] = canonical_sha256(unsigned)
        with self.assertRaisesRegex(StateConflictError, "novelty identity"):
            build_qualification_manifest(
                repository_root=ROOT,
                qualification_id="qualification-next-novelty-20260811",
                source_commit="a" * 40,
                source_tree="b" * 40,
                fixture_path=FIXTURES,
                repository_artifacts={"fixture": (FIXTURES,)},
                external_artifacts={"external_fixture": (FIXTURES,)},
                spent_manifests=(prior_with_different_set,),
            )

    def test_spent_v25_fixture_manifest_rejects_renamed_sources_and_preserves_closure(self) -> None:
        prior = build_qualification_manifest(
            repository_root=ROOT,
            qualification_id="qualification-prior-source-20260811",
            source_commit="a" * 40,
            source_tree="b" * 40,
            fixture_path=FIXTURES,
            repository_artifacts={"fixture": (FIXTURES,)},
            external_artifacts={"external_fixture": (FIXTURES,)},
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            shutil.copy2(LEGACY_FIXTURES, root / LEGACY_FIXTURES.name)
            shutil.copy2(HISTORICAL_FIXTURES_V2, root / HISTORICAL_FIXTURES_V2.name)
            shutil.copy2(HISTORICAL_FIXTURES_V3, root / HISTORICAL_FIXTURES_V3.name)
            shutil.copy2(HISTORICAL_FIXTURES_V4, root / HISTORICAL_FIXTURES_V4.name)
            shutil.copy2(HISTORICAL_FIXTURES_V5, root / HISTORICAL_FIXTURES_V5.name)
            shutil.copy2(HISTORICAL_FIXTURES_V6, root / HISTORICAL_FIXTURES_V6.name)
            shutil.copy2(HISTORICAL_FIXTURES_V7, root / HISTORICAL_FIXTURES_V7.name)
            shutil.copy2(HISTORICAL_FIXTURES_V8, root / HISTORICAL_FIXTURES_V8.name)
            shutil.copy2(HISTORICAL_FIXTURES_V9, root / HISTORICAL_FIXTURES_V9.name)
            shutil.copy2(HISTORICAL_FIXTURES_V10, root / HISTORICAL_FIXTURES_V10.name)
            shutil.copy2(HISTORICAL_FIXTURES_V11, root / HISTORICAL_FIXTURES_V11.name)
            shutil.copy2(HISTORICAL_FIXTURES_V12, root / HISTORICAL_FIXTURES_V12.name)
            shutil.copy2(HISTORICAL_FIXTURES_V13, root / HISTORICAL_FIXTURES_V13.name)
            shutil.copy2(HISTORICAL_FIXTURES_V14, root / HISTORICAL_FIXTURES_V14.name)
            shutil.copy2(HISTORICAL_FIXTURES_V15, root / HISTORICAL_FIXTURES_V15.name)
            shutil.copy2(HISTORICAL_FIXTURES_V16, root / HISTORICAL_FIXTURES_V16.name)
            shutil.copy2(HISTORICAL_FIXTURES_V17, root / HISTORICAL_FIXTURES_V17.name)
            shutil.copy2(HISTORICAL_FIXTURES_V18, root / HISTORICAL_FIXTURES_V18.name)
            shutil.copy2(HISTORICAL_FIXTURES_V19, root / HISTORICAL_FIXTURES_V19.name)
            shutil.copy2(HISTORICAL_FIXTURES_V20, root / HISTORICAL_FIXTURES_V20.name)
            shutil.copy2(HISTORICAL_FIXTURES_V21, root / HISTORICAL_FIXTURES_V21.name)
            shutil.copy2(HISTORICAL_FIXTURES_V22, root / HISTORICAL_FIXTURES_V22.name)
            shutil.copy2(HISTORICAL_FIXTURES_V23, root / HISTORICAL_FIXTURES_V23.name)
            shutil.copy2(HISTORICAL_FIXTURES_V24, root / HISTORICAL_FIXTURES_V24.name)
            raw = json.loads(FIXTURES.read_text(encoding="utf-8"))
            renamed = deepcopy(raw)
            for index, row in enumerate(renamed["fixtures"], start=1):
                row["novelty_id"] = f"stress:renamed_source_case_{index:02d}"
            renamed_path = root / "renamed.json"
            renamed_path.write_text(json.dumps(renamed), encoding="utf-8")
            with self.assertRaisesRegex(StateConflictError, "fixture source"):
                build_qualification_manifest(
                    repository_root=ROOT,
                    qualification_id="qualification-renamed-source-20260811",
                    source_commit="a" * 40,
                    source_tree="b" * 40,
                    fixture_path=renamed_path,
                    repository_artifacts={"fixture": (FIXTURES,)},
                    external_artifacts={"external_fixture": (FIXTURES,)},
                    spent_manifests=(prior,),
                )

            second = deepcopy(raw)
            for index, row in enumerate(second["fixtures"], start=1):
                row["novelty_id"] = f"stress:second_generation_case_{index:02d}"
                row["user_source"] += f" Distinct second-generation stress marker {index}."
            second_path = root / "second.json"
            second_path.write_text(json.dumps(second), encoding="utf-8")
            prior_second = build_qualification_manifest(
                repository_root=ROOT,
                qualification_id="qualification-second-source-20260811",
                source_commit="a" * 40,
                source_tree="b" * 40,
                fixture_path=second_path,
                repository_artifacts={"fixture": (FIXTURES,)},
                external_artifacts={"external_fixture": (FIXTURES,)},
                spent_manifests=(prior,),
            )
            self.assertIn(prior["manifest_sha256"], prior_second["spent_manifest_sha256s"])
            self.assertIn(prior["fixture_set_sha256"], prior_second["spent_fixture_set_sha256s"])
            self.assertEqual(
                set(prior_second["spent_source_sha256s"]),
                {value["source_sha256"] for value in prior["fixture_novelty"]},
            )
            with self.assertRaisesRegex(StateConflictError, "fixture source"):
                build_qualification_manifest(
                    repository_root=ROOT,
                    qualification_id="qualification-transitive-source-20260811",
                    source_commit="a" * 40,
                    source_tree="b" * 40,
                    fixture_path=renamed_path,
                    repository_artifacts={"fixture": (FIXTURES,)},
                    external_artifacts={"external_fixture": (FIXTURES,)},
                    spent_manifests=(prior_second,),
                )

    def test_v29_closure_ingests_root_g_v5_through_ac_v28(self) -> None:
        manifest_v5 = build_qualification_manifest(
            repository_root=ROOT,
            qualification_id="qualification-historical-source-20260811",
            source_commit="a" * 40,
            source_tree="b" * 40,
            fixture_path=HISTORICAL_FIXTURES_V2,
            repository_artifacts={"fixture": (HISTORICAL_FIXTURES_V2,)},
            external_artifacts={"external_fixture": (HISTORICAL_FIXTURES_V2,)},
        )
        manifest_v5["schema_version"] = QUALIFICATION_MANIFEST_SCHEMA_V5
        manifest_v5["execution_policy"].pop("ordinary_standing_creator_policy")
        del manifest_v5["fixture_ancestry"]
        unsigned_v5 = {key: value for key, value in manifest_v5.items() if key != "manifest_sha256"}
        manifest_v5["manifest_sha256"] = canonical_sha256(unsigned_v5)
        validate_qualification_manifest(manifest_v5)
        expanded_v5 = deepcopy(manifest_v5)
        expanded_v5["fixture_ancestry"] = []
        expanded_v5["manifest_sha256"] = canonical_sha256(
            {key: value for key, value in expanded_v5.items() if key != "manifest_sha256"}
        )
        with self.assertRaisesRegex(ContractValidationError, "manifest shape changed"):
            validate_qualification_manifest(expanded_v5)
        manifest_v6 = build_qualification_manifest(
            repository_root=ROOT,
            qualification_id="qualification-root-h-source-20260811",
            source_commit="a" * 40,
            source_tree="b" * 40,
            fixture_path=HISTORICAL_FIXTURES_V3,
            repository_artifacts={"fixture": (HISTORICAL_FIXTURES_V3,)},
            external_artifacts={"external_fixture": (HISTORICAL_FIXTURES_V3,)},
            spent_manifests=(manifest_v5,),
        )
        manifest_v6["schema_version"] = QUALIFICATION_MANIFEST_SCHEMA_V6
        manifest_v6["execution_policy"].pop("ordinary_standing_creator_policy")
        manifest_v6["manifest_sha256"] = canonical_sha256(
            {key: value for key, value in manifest_v6.items() if key != "manifest_sha256"}
        )
        validate_qualification_manifest(manifest_v6)
        self.assertIn(manifest_v5["manifest_sha256"], manifest_v6["spent_manifest_sha256s"])
        manifest_v7 = build_qualification_manifest(
            repository_root=ROOT,
            qualification_id="qualification-root-i-source-20260811",
            source_commit="a" * 40,
            source_tree="b" * 40,
            fixture_path=HISTORICAL_FIXTURES_V4,
            repository_artifacts={"fixture": (HISTORICAL_FIXTURES_V4,)},
            external_artifacts={"external_fixture": (HISTORICAL_FIXTURES_V4,)},
            spent_manifests=(manifest_v5, manifest_v6),
        )
        manifest_v7["schema_version"] = QUALIFICATION_MANIFEST_SCHEMA_V7
        manifest_v7["execution_policy"].pop("ordinary_standing_creator_policy")
        manifest_v7["manifest_sha256"] = canonical_sha256(
            {key: value for key, value in manifest_v7.items() if key != "manifest_sha256"}
        )
        validate_qualification_manifest(manifest_v7)
        manifest_v8 = build_qualification_manifest(
            repository_root=ROOT,
            qualification_id="qualification-root-j-source-20260811",
            source_commit="a" * 40,
            source_tree="b" * 40,
            fixture_path=HISTORICAL_FIXTURES_V5,
            repository_artifacts={"fixture": (HISTORICAL_FIXTURES_V5,)},
            external_artifacts={"external_fixture": (HISTORICAL_FIXTURES_V5,)},
            spent_manifests=(manifest_v7,),
        )
        manifest_v8["schema_version"] = QUALIFICATION_MANIFEST_SCHEMA_V8
        manifest_v8["execution_policy"].pop("ordinary_standing_creator_policy")
        manifest_v8["manifest_sha256"] = canonical_sha256(
            {key: value for key, value in manifest_v8.items() if key != "manifest_sha256"}
        )
        validate_qualification_manifest(manifest_v8)
        manifest_v9 = build_qualification_manifest(
            repository_root=ROOT,
            qualification_id="qualification-root-k-source-20260811",
            source_commit="a" * 40,
            source_tree="b" * 40,
            fixture_path=HISTORICAL_FIXTURES_V6,
            repository_artifacts={"fixture": (HISTORICAL_FIXTURES_V6,)},
            external_artifacts={"external_fixture": (HISTORICAL_FIXTURES_V6,)},
            spent_manifests=(manifest_v8,),
        )
        manifest_v9["schema_version"] = QUALIFICATION_MANIFEST_SCHEMA_V9
        manifest_v9["execution_policy"].pop("ordinary_standing_creator_policy")
        manifest_v9["manifest_sha256"] = canonical_sha256(
            {key: value for key, value in manifest_v9.items() if key != "manifest_sha256"}
        )
        validate_qualification_manifest(manifest_v9)
        manifest_v10 = build_qualification_manifest(
            repository_root=ROOT,
            qualification_id="qualification-root-l-source-20260811",
            source_commit="a" * 40,
            source_tree="b" * 40,
            fixture_path=HISTORICAL_FIXTURES_V7,
            repository_artifacts={"fixture": (HISTORICAL_FIXTURES_V7,)},
            external_artifacts={"external_fixture": (HISTORICAL_FIXTURES_V7,)},
            spent_manifests=(manifest_v9,),
        )
        manifest_v10["schema_version"] = QUALIFICATION_MANIFEST_SCHEMA_V10
        manifest_v10["execution_policy"].pop("ordinary_standing_creator_policy")
        manifest_v10["manifest_sha256"] = canonical_sha256(
            {key: value for key, value in manifest_v10.items() if key != "manifest_sha256"}
        )
        validate_qualification_manifest(manifest_v10)
        manifest_v11 = build_qualification_manifest(
            repository_root=ROOT,
            qualification_id="qualification-root-m-source-20260811",
            source_commit="a" * 40,
            source_tree="b" * 40,
            fixture_path=HISTORICAL_FIXTURES_V8,
            repository_artifacts={"fixture": (HISTORICAL_FIXTURES_V8,)},
            external_artifacts={"external_fixture": (HISTORICAL_FIXTURES_V8,)},
            spent_manifests=(manifest_v10,),
        )
        manifest_v11["schema_version"] = QUALIFICATION_MANIFEST_SCHEMA_V11
        manifest_v11["execution_policy"].pop("ordinary_standing_creator_policy")
        manifest_v11["manifest_sha256"] = canonical_sha256(
            {key: value for key, value in manifest_v11.items() if key != "manifest_sha256"}
        )
        validate_qualification_manifest(manifest_v11)
        manifest_v12 = build_qualification_manifest(
            repository_root=ROOT,
            qualification_id="qualification-root-n-source-20260812",
            source_commit="a" * 40,
            source_tree="b" * 40,
            fixture_path=HISTORICAL_FIXTURES_V9,
            repository_artifacts={"fixture": (HISTORICAL_FIXTURES_V9,)},
            external_artifacts={"external_fixture": (HISTORICAL_FIXTURES_V9,)},
            spent_manifests=(manifest_v11,),
        )
        manifest_v12["schema_version"] = QUALIFICATION_MANIFEST_SCHEMA_V12
        manifest_v12["execution_policy"].pop("ordinary_standing_creator_policy")
        manifest_v12["manifest_sha256"] = canonical_sha256(
            {key: value for key, value in manifest_v12.items() if key != "manifest_sha256"}
        )
        validate_qualification_manifest(manifest_v12)
        manifest_v13 = build_qualification_manifest(
            repository_root=ROOT,
            qualification_id="qualification-root-o-source-20260812",
            source_commit="a" * 40,
            source_tree="b" * 40,
            fixture_path=HISTORICAL_FIXTURES_V10,
            repository_artifacts={"fixture": (HISTORICAL_FIXTURES_V10,)},
            external_artifacts={"external_fixture": (HISTORICAL_FIXTURES_V10,)},
            spent_manifests=(manifest_v12,),
        )
        manifest_v13["schema_version"] = QUALIFICATION_MANIFEST_SCHEMA_V13
        manifest_v13["execution_policy"].pop("ordinary_standing_creator_policy")
        manifest_v13["manifest_sha256"] = canonical_sha256(
            {key: value for key, value in manifest_v13.items() if key != "manifest_sha256"}
        )
        validate_qualification_manifest(manifest_v13)
        manifest_v14 = build_qualification_manifest(
            repository_root=ROOT,
            qualification_id="qualification-root-p-source-20260812",
            source_commit="a" * 40,
            source_tree="b" * 40,
            fixture_path=HISTORICAL_FIXTURES_V11,
            repository_artifacts={"fixture": (HISTORICAL_FIXTURES_V11,)},
            external_artifacts={"external_fixture": (HISTORICAL_FIXTURES_V11,)},
            spent_manifests=(manifest_v13,),
        )
        manifest_v14["schema_version"] = QUALIFICATION_MANIFEST_SCHEMA_V14
        manifest_v14["execution_policy"].pop("ordinary_standing_creator_policy")
        manifest_v14["manifest_sha256"] = canonical_sha256(
            {key: value for key, value in manifest_v14.items() if key != "manifest_sha256"}
        )
        validate_qualification_manifest(manifest_v14)
        manifest_v15 = build_qualification_manifest(
            repository_root=ROOT,
            qualification_id="qualification-root-q-source-20260812",
            source_commit="a" * 40,
            source_tree="b" * 40,
            fixture_path=HISTORICAL_FIXTURES_V12,
            repository_artifacts={"fixture": (HISTORICAL_FIXTURES_V12,)},
            external_artifacts={"external_fixture": (HISTORICAL_FIXTURES_V12,)},
            spent_manifests=(manifest_v14,),
        )
        manifest_v15["schema_version"] = QUALIFICATION_MANIFEST_SCHEMA_V15
        manifest_v15["execution_policy"].pop("ordinary_standing_creator_policy")
        manifest_v15["manifest_sha256"] = canonical_sha256(
            {key: value for key, value in manifest_v15.items() if key != "manifest_sha256"}
        )
        validate_qualification_manifest(manifest_v15)
        manifest_v16 = build_qualification_manifest(
            repository_root=ROOT,
            qualification_id="qualification-root-r-source-20260812",
            source_commit="a" * 40,
            source_tree="b" * 40,
            fixture_path=HISTORICAL_FIXTURES_V13,
            repository_artifacts={"fixture": (HISTORICAL_FIXTURES_V13,)},
            external_artifacts={"external_fixture": (HISTORICAL_FIXTURES_V13,)},
            spent_manifests=(manifest_v15,),
        )
        manifest_v16["schema_version"] = QUALIFICATION_MANIFEST_SCHEMA_V16
        manifest_v16["execution_policy"].pop("ordinary_standing_creator_policy")
        manifest_v16["manifest_sha256"] = canonical_sha256(
            {key: value for key, value in manifest_v16.items() if key != "manifest_sha256"}
        )
        validate_qualification_manifest(manifest_v16)
        manifest_v17 = build_qualification_manifest(
            repository_root=ROOT,
            qualification_id="qualification-root-s-source-20260812",
            source_commit="a" * 40,
            source_tree="b" * 40,
            fixture_path=HISTORICAL_FIXTURES_V14,
            repository_artifacts={"fixture": (HISTORICAL_FIXTURES_V14,)},
            external_artifacts={"external_fixture": (HISTORICAL_FIXTURES_V14,)},
            spent_manifests=(manifest_v16,),
        )
        manifest_v17["schema_version"] = QUALIFICATION_MANIFEST_SCHEMA_V17
        manifest_v17["execution_policy"].pop("ordinary_standing_creator_policy")
        manifest_v17["manifest_sha256"] = canonical_sha256(
            {key: value for key, value in manifest_v17.items() if key != "manifest_sha256"}
        )
        validate_qualification_manifest(manifest_v17)
        manifest_v18 = build_qualification_manifest(
            repository_root=ROOT,
            qualification_id="qualification-root-t-source-20260812",
            source_commit="a" * 40,
            source_tree="b" * 40,
            fixture_path=HISTORICAL_FIXTURES_V15,
            repository_artifacts={"fixture": (HISTORICAL_FIXTURES_V15,)},
            external_artifacts={"external_fixture": (HISTORICAL_FIXTURES_V15,)},
            spent_manifests=(manifest_v17,),
        )
        manifest_v18["schema_version"] = QUALIFICATION_MANIFEST_SCHEMA_V18
        manifest_v18["execution_policy"].pop("ordinary_standing_creator_policy")
        manifest_v18["manifest_sha256"] = canonical_sha256(
            {key: value for key, value in manifest_v18.items() if key != "manifest_sha256"}
        )
        validate_qualification_manifest(manifest_v18)
        manifest_v19 = build_qualification_manifest(
            repository_root=ROOT,
            qualification_id="qualification-root-u-source-20260812",
            source_commit="a" * 40,
            source_tree="b" * 40,
            fixture_path=HISTORICAL_FIXTURES_V16,
            repository_artifacts={"fixture": (HISTORICAL_FIXTURES_V16,)},
            external_artifacts={"external_fixture": (HISTORICAL_FIXTURES_V16,)},
            spent_manifests=(manifest_v18,),
        )
        manifest_v19["schema_version"] = QUALIFICATION_MANIFEST_SCHEMA_V19
        manifest_v19["execution_policy"].pop("ordinary_standing_creator_policy")
        manifest_v19["manifest_sha256"] = canonical_sha256(
            {key: value for key, value in manifest_v19.items() if key != "manifest_sha256"}
        )
        validate_qualification_manifest(manifest_v19)
        manifest_v20 = build_qualification_manifest(
            repository_root=ROOT,
            qualification_id="qualification-root-v-source-20260813",
            source_commit="a" * 40,
            source_tree="b" * 40,
            fixture_path=HISTORICAL_FIXTURES_V17,
            repository_artifacts={"fixture": (HISTORICAL_FIXTURES_V17,)},
            external_artifacts={"external_fixture": (HISTORICAL_FIXTURES_V17,)},
            spent_manifests=(manifest_v19,),
        )
        manifest_v20["schema_version"] = QUALIFICATION_MANIFEST_SCHEMA_V20
        manifest_v20["execution_policy"].pop("ordinary_standing_creator_policy")
        manifest_v20["manifest_sha256"] = canonical_sha256(
            {key: value for key, value in manifest_v20.items() if key != "manifest_sha256"}
        )
        validate_qualification_manifest(manifest_v20)
        manifest_v21 = build_qualification_manifest(
            repository_root=ROOT,
            qualification_id="qualification-root-w-source-20260813",
            source_commit="a" * 40,
            source_tree="b" * 40,
            fixture_path=HISTORICAL_FIXTURES_V18,
            repository_artifacts={"fixture": (HISTORICAL_FIXTURES_V18,)},
            external_artifacts={"external_fixture": (HISTORICAL_FIXTURES_V18,)},
            spent_manifests=(manifest_v20,),
        )
        manifest_v21["schema_version"] = QUALIFICATION_MANIFEST_SCHEMA_V21
        manifest_v21["execution_policy"].pop("ordinary_standing_creator_policy")
        manifest_v21["manifest_sha256"] = canonical_sha256(
            {key: value for key, value in manifest_v21.items() if key != "manifest_sha256"}
        )
        validate_qualification_manifest(manifest_v21)
        manifest_v22 = build_qualification_manifest(
            repository_root=ROOT,
            qualification_id="qualification-root-x-source-20260813",
            source_commit="a" * 40,
            source_tree="b" * 40,
            fixture_path=HISTORICAL_FIXTURES_V19,
            repository_artifacts={"fixture": (HISTORICAL_FIXTURES_V19,)},
            external_artifacts={"external_fixture": (HISTORICAL_FIXTURES_V19,)},
            spent_manifests=(manifest_v21,),
        )
        manifest_v22["schema_version"] = QUALIFICATION_MANIFEST_SCHEMA_V22
        manifest_v22["execution_policy"].pop("ordinary_standing_creator_policy")
        manifest_v22["manifest_sha256"] = canonical_sha256(
            {key: value for key, value in manifest_v22.items() if key != "manifest_sha256"}
        )
        validate_qualification_manifest(manifest_v22)
        manifest_v24 = build_qualification_manifest(
            repository_root=ROOT,
            qualification_id="qualification-root-y-source-20260813",
            source_commit="a" * 40,
            source_tree="b" * 40,
            fixture_path=HISTORICAL_FIXTURES_V20,
            repository_artifacts={"fixture": (HISTORICAL_FIXTURES_V20,)},
            external_artifacts={"external_fixture": (HISTORICAL_FIXTURES_V20,)},
            spent_manifests=(manifest_v22,),
        )
        manifest_v24["schema_version"] = QUALIFICATION_MANIFEST_SCHEMA_V24
        manifest_v24["manifest_sha256"] = canonical_sha256(
            {key: value for key, value in manifest_v24.items() if key != "manifest_sha256"}
        )
        validate_qualification_manifest(manifest_v24)
        manifest_v25 = build_qualification_manifest(
            repository_root=ROOT,
            qualification_id="qualification-root-z-source-20260813",
            source_commit="a" * 40,
            source_tree="b" * 40,
            fixture_path=HISTORICAL_FIXTURES_V21,
            repository_artifacts={"fixture": (HISTORICAL_FIXTURES_V21,)},
            external_artifacts={"external_fixture": (HISTORICAL_FIXTURES_V21,)},
            spent_manifests=(manifest_v24,),
        )
        manifest_v25["schema_version"] = QUALIFICATION_MANIFEST_SCHEMA_V25
        manifest_v25["manifest_sha256"] = canonical_sha256(
            {key: value for key, value in manifest_v25.items() if key != "manifest_sha256"}
        )
        validate_qualification_manifest(manifest_v25)
        manifest_v26 = build_qualification_manifest(
            repository_root=ROOT,
            qualification_id="qualification-root-aa-source-20260813",
            source_commit="a" * 40,
            source_tree="b" * 40,
            fixture_path=HISTORICAL_FIXTURES_V22,
            repository_artifacts={"fixture": (HISTORICAL_FIXTURES_V22,)},
            external_artifacts={"external_fixture": (HISTORICAL_FIXTURES_V22,)},
            spent_manifests=(manifest_v25,),
        )
        manifest_v26["schema_version"] = QUALIFICATION_MANIFEST_SCHEMA_V26
        manifest_v26["manifest_sha256"] = canonical_sha256(
            {key: value for key, value in manifest_v26.items() if key != "manifest_sha256"}
        )
        validate_qualification_manifest(manifest_v26)
        manifest_v27 = build_qualification_manifest(
            repository_root=ROOT,
            qualification_id="qualification-root-ab-source-20260814",
            source_commit="a" * 40,
            source_tree="b" * 40,
            fixture_path=HISTORICAL_FIXTURES_V23,
            repository_artifacts={"fixture": (HISTORICAL_FIXTURES_V23,)},
            external_artifacts={"external_fixture": (HISTORICAL_FIXTURES_V23,)},
            spent_manifests=(manifest_v26,),
        )
        manifest_v27["schema_version"] = QUALIFICATION_MANIFEST_SCHEMA_V27
        manifest_v27["manifest_sha256"] = canonical_sha256(
            {key: value for key, value in manifest_v27.items() if key != "manifest_sha256"}
        )
        validate_qualification_manifest(manifest_v27)
        manifest_v28 = build_qualification_manifest(
            repository_root=ROOT,
            qualification_id="qualification-root-ac-source-20260814",
            source_commit="a" * 40,
            source_tree="b" * 40,
            fixture_path=HISTORICAL_FIXTURES_V24,
            repository_artifacts={"fixture": (HISTORICAL_FIXTURES_V24,)},
            external_artifacts={"external_fixture": (HISTORICAL_FIXTURES_V24,)},
            spent_manifests=(manifest_v27,),
        )
        manifest_v28["schema_version"] = QUALIFICATION_MANIFEST_SCHEMA_V28
        manifest_v28["manifest_sha256"] = canonical_sha256(
            {key: value for key, value in manifest_v28.items() if key != "manifest_sha256"}
        )
        validate_qualification_manifest(manifest_v28)
        manifest_v29 = build_qualification_manifest(
            repository_root=ROOT,
            qualification_id="qualification-next-source-20260814",
            source_commit="a" * 40,
            source_tree="b" * 40,
            fixture_path=FIXTURES,
            repository_artifacts={"fixture": (FIXTURES,)},
            external_artifacts={"external_fixture": (FIXTURES,)},
            spent_manifests=(manifest_v28,),
        )
        self.assertEqual(manifest_v29["schema_version"], QUALIFICATION_MANIFEST_SCHEMA)
        self.assertEqual(
            set(manifest_v29["spent_manifest_sha256s"]),
            {
                manifest_v5["manifest_sha256"],
                manifest_v6["manifest_sha256"],
                manifest_v7["manifest_sha256"],
                manifest_v8["manifest_sha256"],
                manifest_v9["manifest_sha256"],
                manifest_v10["manifest_sha256"],
                manifest_v11["manifest_sha256"],
                manifest_v12["manifest_sha256"],
                manifest_v13["manifest_sha256"],
                manifest_v14["manifest_sha256"],
                manifest_v15["manifest_sha256"],
                manifest_v16["manifest_sha256"],
                manifest_v17["manifest_sha256"],
                manifest_v18["manifest_sha256"],
                manifest_v19["manifest_sha256"],
                manifest_v20["manifest_sha256"],
                manifest_v21["manifest_sha256"],
                manifest_v22["manifest_sha256"],
                manifest_v24["manifest_sha256"],
                manifest_v25["manifest_sha256"],
                manifest_v26["manifest_sha256"],
                manifest_v27["manifest_sha256"],
                manifest_v28["manifest_sha256"],
            },
        )
        self.assertEqual(
            set(manifest_v29["spent_fixture_set_sha256s"]),
            {
                manifest_v5["fixture_set_sha256"],
                manifest_v6["fixture_set_sha256"],
                manifest_v7["fixture_set_sha256"],
                manifest_v8["fixture_set_sha256"],
                manifest_v9["fixture_set_sha256"],
                manifest_v10["fixture_set_sha256"],
                manifest_v11["fixture_set_sha256"],
                manifest_v12["fixture_set_sha256"],
                manifest_v13["fixture_set_sha256"],
                manifest_v14["fixture_set_sha256"],
                manifest_v15["fixture_set_sha256"],
                manifest_v16["fixture_set_sha256"],
                manifest_v17["fixture_set_sha256"],
                manifest_v18["fixture_set_sha256"],
                manifest_v19["fixture_set_sha256"],
                manifest_v20["fixture_set_sha256"],
                manifest_v21["fixture_set_sha256"],
                manifest_v22["fixture_set_sha256"],
                manifest_v24["fixture_set_sha256"],
                manifest_v25["fixture_set_sha256"],
                manifest_v26["fixture_set_sha256"],
                manifest_v27["fixture_set_sha256"],
                manifest_v28["fixture_set_sha256"],
            },
        )
        self.assertEqual(len(manifest_v29["spent_manifest_sha256s"]), 23)
        self.assertEqual(len(manifest_v29["spent_fixture_set_sha256s"]), 23)
        expected_novelty = {
            value["novelty_id"]
            for manifest in (
                manifest_v5,
                manifest_v6,
                manifest_v7,
                manifest_v8,
                manifest_v9,
                manifest_v10,
                manifest_v11,
                manifest_v12,
                manifest_v13,
                manifest_v14,
                manifest_v15,
                manifest_v16,
                manifest_v17,
                manifest_v18,
                manifest_v19,
                manifest_v20,
                manifest_v21,
                manifest_v22,
                manifest_v24,
                manifest_v25,
                manifest_v26,
                manifest_v27,
                manifest_v28,
            )
            for value in manifest["fixture_novelty"]
        }
        expected_sources = {
            value["source_sha256"]
            for manifest in (
                manifest_v5,
                manifest_v6,
                manifest_v7,
                manifest_v8,
                manifest_v9,
                manifest_v10,
                manifest_v11,
                manifest_v12,
                manifest_v13,
                manifest_v14,
                manifest_v15,
                manifest_v16,
                manifest_v17,
                manifest_v18,
                manifest_v19,
                manifest_v20,
                manifest_v21,
                manifest_v22,
                manifest_v24,
                manifest_v25,
                manifest_v26,
                manifest_v27,
                manifest_v28,
            )
            for value in manifest["fixture_novelty"]
        }
        self.assertEqual(set(manifest_v29["spent_novelty_ids"]), expected_novelty)
        self.assertEqual(set(manifest_v29["spent_source_sha256s"]), expected_sources)
        self.assertEqual(len(manifest_v29["spent_novelty_ids"]), 690)
        self.assertEqual(len(manifest_v29["spent_source_sha256s"]), 690)
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            sibling_v5 = parent / "root-g-v5" / "QUALIFICATION_MANIFEST.json"
            sibling_v5.parent.mkdir()
            sibling_v5.write_bytes(canonical_bytes(manifest_v5) + b"\n")
            sibling_v6 = parent / "root-h-v6" / "QUALIFICATION_MANIFEST.json"
            sibling_v6.parent.mkdir()
            sibling_v6.write_bytes(canonical_bytes(manifest_v6) + b"\n")
            sibling_v7 = parent / "root-i-v7" / "QUALIFICATION_MANIFEST.json"
            sibling_v7.parent.mkdir()
            sibling_v7.write_bytes(canonical_bytes(manifest_v7) + b"\n")
            sibling_v8 = parent / "root-j-v8" / "QUALIFICATION_MANIFEST.json"
            sibling_v8.parent.mkdir()
            sibling_v8.write_bytes(canonical_bytes(manifest_v8) + b"\n")
            sibling_v9 = parent / "root-k-v9" / "QUALIFICATION_MANIFEST.json"
            sibling_v9.parent.mkdir()
            sibling_v9.write_bytes(canonical_bytes(manifest_v9) + b"\n")
            sibling_v10 = parent / "root-l-v10" / "QUALIFICATION_MANIFEST.json"
            sibling_v10.parent.mkdir()
            sibling_v10.write_bytes(canonical_bytes(manifest_v10) + b"\n")
            sibling_v11 = parent / "root-m-v11" / "QUALIFICATION_MANIFEST.json"
            sibling_v11.parent.mkdir()
            sibling_v11.write_bytes(canonical_bytes(manifest_v11) + b"\n")
            sibling_v12 = parent / "root-n-v12" / "QUALIFICATION_MANIFEST.json"
            sibling_v12.parent.mkdir()
            sibling_v12.write_bytes(canonical_bytes(manifest_v12) + b"\n")
            sibling_v13 = parent / "root-o-v13" / "QUALIFICATION_MANIFEST.json"
            sibling_v13.parent.mkdir()
            sibling_v13.write_bytes(canonical_bytes(manifest_v13) + b"\n")
            sibling_v14 = parent / "root-p-v14" / "QUALIFICATION_MANIFEST.json"
            sibling_v14.parent.mkdir()
            sibling_v14.write_bytes(canonical_bytes(manifest_v14) + b"\n")
            sibling_v15 = parent / "root-q-v15" / "QUALIFICATION_MANIFEST.json"
            sibling_v15.parent.mkdir()
            sibling_v15.write_bytes(canonical_bytes(manifest_v15) + b"\n")
            sibling_v16 = parent / "root-r-v16" / "QUALIFICATION_MANIFEST.json"
            sibling_v16.parent.mkdir()
            sibling_v16.write_bytes(canonical_bytes(manifest_v16) + b"\n")
            sibling_v17 = parent / "root-s-v17" / "QUALIFICATION_MANIFEST.json"
            sibling_v17.parent.mkdir()
            sibling_v17.write_bytes(canonical_bytes(manifest_v17) + b"\n")
            sibling_v18 = parent / "root-t-v18" / "QUALIFICATION_MANIFEST.json"
            sibling_v18.parent.mkdir()
            sibling_v18.write_bytes(canonical_bytes(manifest_v18) + b"\n")
            sibling_v19 = parent / "root-u-v19" / "QUALIFICATION_MANIFEST.json"
            sibling_v19.parent.mkdir()
            sibling_v19.write_bytes(canonical_bytes(manifest_v19) + b"\n")
            sibling_v20 = parent / "root-v-v20" / "QUALIFICATION_MANIFEST.json"
            sibling_v20.parent.mkdir()
            sibling_v20.write_bytes(canonical_bytes(manifest_v20) + b"\n")
            sibling_v21 = parent / "root-w-v21" / "QUALIFICATION_MANIFEST.json"
            sibling_v21.parent.mkdir()
            sibling_v21.write_bytes(canonical_bytes(manifest_v21) + b"\n")
            sibling_v22 = parent / "root-x-v22" / "QUALIFICATION_MANIFEST.json"
            sibling_v22.parent.mkdir()
            sibling_v22.write_bytes(canonical_bytes(manifest_v22) + b"\n")
            sibling_v24 = parent / "root-y-v24" / "QUALIFICATION_MANIFEST.json"
            sibling_v24.parent.mkdir()
            sibling_v24.write_bytes(canonical_bytes(manifest_v24) + b"\n")
            sibling_v25 = parent / "root-z-v25" / "QUALIFICATION_MANIFEST.json"
            sibling_v25.parent.mkdir()
            sibling_v25.write_bytes(canonical_bytes(manifest_v25) + b"\n")
            sibling_v26 = parent / "root-zz-aa-v26" / "QUALIFICATION_MANIFEST.json"
            sibling_v26.parent.mkdir()
            sibling_v26.write_bytes(canonical_bytes(manifest_v26) + b"\n")
            sibling_v27 = parent / "root-zz-ab-v27" / "QUALIFICATION_MANIFEST.json"
            sibling_v27.parent.mkdir()
            sibling_v27.write_bytes(canonical_bytes(manifest_v27) + b"\n")
            sibling_v28 = parent / "root-zz-ac-v28" / "QUALIFICATION_MANIFEST.json"
            sibling_v28.parent.mkdir()
            sibling_v28.write_bytes(canonical_bytes(manifest_v28) + b"\n")
            discovered = entrypoint._load_spent_qualification_manifests(
                output_root=parent / "next",
                explicit_paths=(),
            )
        self.assertEqual(
            discovered,
            (
                manifest_v5,
                manifest_v6,
                manifest_v7,
                manifest_v8,
                manifest_v9,
                manifest_v10,
                manifest_v11,
                manifest_v12,
                manifest_v13,
                manifest_v14,
                manifest_v15,
                manifest_v16,
                manifest_v17,
                manifest_v18,
                manifest_v19,
                manifest_v20,
                manifest_v21,
                manifest_v22,
                manifest_v24,
                manifest_v25,
                manifest_v26,
                manifest_v27,
                manifest_v28,
            ),
        )
        with patch.object(Path, "is_symlink", return_value=True):
            with self.assertRaisesRegex(StateConflictError, "explicit spent manifest is linked"):
                entrypoint._load_spent_qualification_manifests(
                    output_root=Path("unused-output-root"),
                    explicit_paths=(Path("linked-manifest.json"),),
                )

    def test_v29_manifest_rejects_v25_schema_or_baseline_tampering(self) -> None:
        manifest = _manifest()
        self.assertEqual(manifest["schema_version"], QUALIFICATION_MANIFEST_SCHEMA)

        downgraded = deepcopy(manifest)
        downgraded["schema_version"] = QUALIFICATION_MANIFEST_SCHEMA_V28
        downgraded["manifest_sha256"] = canonical_sha256(
            {key: value for key, value in downgraded.items() if key != "manifest_sha256"}
        )
        with self.assertRaisesRegex(ContractValidationError, "manifest schema changed"):
            validate_qualification_manifest(downgraded)

        rebound = deepcopy(manifest)
        rebound["fixture_baseline"] = {
            "path": HISTORICAL_FIXTURES_V23.name,
            "sha256": bytes_sha256(HISTORICAL_FIXTURES_V23.read_bytes()),
        }
        rebound["manifest_sha256"] = canonical_sha256(
            {key: value for key, value in rebound.items() if key != "manifest_sha256"}
        )
        with self.assertRaisesRegex(StateConflictError, "fixture baseline changed"):
            validate_qualification_manifest(rebound)

    def test_representative_canary_uses_medium_planner_effort(self) -> None:
        fixture = load_qualification_fixtures(FIXTURES)[0]
        payload = qualification_request_payload(
            fixture,
            session_id="qualification-medium-canary",
        )
        self.assertEqual(QUALIFICATION_PLANNER_REASONING_EFFORT, "medium")
        self.assertEqual(payload["cera_reasoning_effort"], "medium")
        self.assertEqual(payload["cera_review_mode"], "automatic")
        self.assertEqual(
            QUALIFICATION_EXECUTION_POLICY["semantic_validator_reasoning_effort"],
            "xhigh",
        )
        self.assertEqual(
            QUALIFICATION_EXECUTION_POLICY["reader_reasoning_effort"],
            "medium",
        )
        profile = json.loads(
            (ROOT / "integrations/sillytavern/pi_scene_lean_v1_profile.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(profile["ordinary_reader"], "fresh_codex_sol")
        self.assertEqual(profile["ordinary_reader_reasoning_effort"], "medium")

    def test_retry_regenerate_replan_and_recorder_repair_budgets_are_separate(self) -> None:
        technical = QUALIFICATION_ACTION_BUDGETS["technical_provider_retry"]
        self.assertEqual(technical["maximum_stage_attempts"], 3)
        self.assertEqual(technical["maximum_manual_retry_actions"], 2)
        self.assertFalse(technical["automatic_provider_redispatch"])
        prepared = QUALIFICATION_ACTION_BUDGETS["prepared_dispatch_resume"]
        self.assertEqual(prepared["maximum_actions_per_stage_occurrence"], 1)
        self.assertFalse(prepared["consumes_retry_action"])
        self.assertFalse(prepared["automatic"])
        self.assertEqual(
            QUALIFICATION_ACTION_BUDGETS["semantic_regenerate"][
                "maximum_explicit_actions_per_rejected_first_pass"
            ],
            1,
        )
        self.assertEqual(
            QUALIFICATION_ACTION_BUDGETS["replan"]["maximum_actions_per_qualification_fixture"],
            0,
        )
        self.assertEqual(
            QUALIFICATION_ACTION_BUDGETS["recorder_repair"][
                "maximum_actions_per_qualification_fixture"
            ],
            1,
        )
        self.assertFalse(QUALIFICATION_ACTION_BUDGETS["recorder_repair"]["recursive_repair"])
        self.assertEqual(
            QUALIFICATION_COMPLETE_GENERATION_CEILINGS["ordinary"]["maximum_codex_operations"],
            15,
        )
        self.assertEqual(
            QUALIFICATION_COMPLETE_GENERATION_CEILINGS["ordinary"][
                "maximum_deepseek_http_operations"
            ],
            72,
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
            self.assertEqual(backend["schema_version"], QUALIFICATION_RESULT_SCHEMA)
            self.assertEqual(backend["passed_fixtures"], 20)
            self.assertEqual(backend["first_pass_accepted"], 20)
            self.assertEqual(backend["retained_conversation_messages"], 40)
            self.assertEqual(client.calls, 20)
            for fixture, result in zip(
                (value for value in fixtures if value.phase is QualificationPhase.BACKEND),
                backend["results"],
                strict=True,
            ):
                self.assertEqual(
                    result["fixture_schema_version"],
                    "cera.pi_scene.full_model_qualification_fixtures.v25",
                )
                self.assertEqual(result["novelty_id"], fixture.novelty_id)
                self.assertEqual(result["stress_tags"], list(fixture.stress_tags))
            fixture_events = [
                json.loads(line)
                for line in (root / "evidence" / "QUALIFICATION_EVENTS.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
                if '"event":"fixture_passed"' in line
            ]
            self.assertEqual(len(fixture_events), 20)
            self.assertTrue(
                all(
                    event["schema_version"] == "cera.pi_scene.qualification_fixture_result.v2"
                    for event in fixture_events
                )
            )
            stage_latency = backend["provider_stage_latency_summary"]
            self.assertEqual(stage_latency["writer"]["operations_total"], 10)
            self.assertEqual(stage_latency["semantic_validator"]["operations_total"], 10)
            self.assertEqual(stage_latency["reader"]["operations_total"], 10)
            self.assertEqual(stage_latency["recorder"]["operations_total"], 10)
            self.assertEqual(stage_latency["adult_scene"]["operations_total"], 10)
            self.assertEqual(stage_latency["adult_filter"]["operations_total"], 10)
            for adult_result in (
                value for value in backend["results"] if value["expected_route"] == "adult"
            ):
                self.assertNotIn("reader", adult_result["provider_operations"])
            self.assertEqual(stage_latency["writer"]["average_duration_ms"], 0)
            self.assertEqual(stage_latency["writer"]["n"], 10)
            self.assertEqual(stage_latency["writer"]["p95_duration_ms"], 0)
            self.assertEqual(
                stage_latency["writer"]["prepared_to_terminal"]["average_duration_ms"],
                0,
            )
            self.assertEqual(
                stage_latency["writer"]["session_classes"]["fresh_rehydration"]["measured_samples"],
                10,
            )
            self.assertEqual(backend["http_latency_summary"]["measured_samples"], 20)
            self.assertEqual(
                backend["non_provider_http_latency_summary"]["measured_samples"],
                20,
            )
            self.assertEqual(
                backend["phase_timing_evidence"]["http_total"]["total_duration_ms"],
                230,
            )
            self.assertEqual(
                backend["phase_timing_evidence"]["sillytavern_overhead"]["availability"],
                "not_applicable",
            )
            provider_latency = backend["provider_transport_latency_evidence"]
            self.assertEqual(
                provider_latency["attempt_number_dimension"],
                {
                    "availability": "unavailable",
                    "unavailable_operations": 72,
                    "reason": ("provider_ledgers_do_not_bind_stage_occurrence_attempt_ordinals"),
                    "status_counters_preserved_in": (
                        "provider_stage_retry_chains.status_observations"
                    ),
                },
            )
            writer_group = next(
                value
                for value in provider_latency["groups"]
                if value["provider"] == "deepseek"
                and value["model"] == "deepseek-v4-flash"
                and value["stage"] == "writer"
                and value["outcome"] == "success"
            )
            self.assertIsNone(writer_group["attempt_number"])
            self.assertEqual(writer_group["n"], 10)
            self.assertEqual(writer_group["mean_duration_ms"], 0)
            self.assertEqual(writer_group["median_duration_ms"], 0)
            self.assertEqual(writer_group["p95_duration_ms"], 0)
            self.assertEqual(writer_group["maximum_duration_ms"], 0)
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
            expected_st_http_duration_ms = sum(
                _FakeQualificationClient.INITIAL_COMPLETION_DURATION_MS
                + (
                    _FakeQualificationClient.REVIEW_POLL_DURATION_MS
                    + _FakeQualificationClient.TERMINAL_DECISION_DURATION_MS
                    if value["expected_route"] == QualificationRoute.ORDINARY.value
                    else 0
                )
                for value in st_result["results"]
            )
            self.assertEqual(
                st_result["phase_timing_evidence"]["http_total"]["total_duration_ms"],
                expected_st_http_duration_ms,
            )
            self.assertEqual(
                st_result["phase_timing_evidence"]["sillytavern_overhead"],
                {
                    "availability": "unavailable",
                    "total_duration_ms": None,
                    "reason": (
                        "end_to_end_timer_does_not_isolate_sillytavern_from_backend_processing"
                    ),
                    "estimated": False,
                },
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
            planner_sessions = result["planner_session_latency_summary"]
            self.assertEqual(planner_sessions["categories"]["cold"]["n"], 1)
            self.assertEqual(
                planner_sessions["categories"]["cold"]["p95_duration_ms"],
                420_000,
            )
            self.assertEqual(planner_sessions["categories"]["rehydrated"]["n"], 0)
            self.assertEqual(planner_sessions["categories"]["retained"]["n"], 11)
            self.assertEqual(
                planner_sessions["categories"]["retained"]["mean_duration_ms"],
                32_727,
            )
            self.assertEqual(
                planner_sessions["retained_transport_concern"]["scope"],
                "retained_planner_provider_transport_duration_ms_only",
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

    def test_manual_provider_stage_retry_uses_exact_action_and_get_authority(self) -> None:
        fixtures = load_qualification_fixtures(FIXTURES)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            client = _FakeProviderStageRetryClient(
                root / "runtime",
                failure_fixture_id="backend-ordinary-01",
                stage="writer",
            )
            result = FullModelQualificationRunner(
                manifest=_manifest(),
                runtime_root=root / "runtime",
                evidence_root=root / "evidence",
            ).run_phase(QualificationPhase.BACKEND, fixtures, client)
            self.assertEqual(result["provider_stage_retry_actions"], 1)
            self.assertEqual(result["provider_stage_retry_chains"], 1)
            self.assertEqual(len(client.action_posts), 1)
            posted_chain, posted_action = client.action_posts[0]
            self.assertEqual(posted_action, client.action_by_chain[posted_chain])
            self.assertGreaterEqual(client.status_reads.count(posted_chain), 2)
            first = result["results"][0]
            chain = first["provider_stage_retry_chains"][0]
            self.assertEqual(chain["retry_action_count"], 1)
            self.assertEqual(chain["terminal_state"], "eligible")
            self.assertNotIn(client.private_sentinel, json.dumps(result, sort_keys=True))

    def test_manual_provider_stage_retry_covers_all_seven_live_stages(self) -> None:
        fixtures = load_qualification_fixtures(FIXTURES)
        cases = (
            ("planner", "backend-ordinary-01"),
            ("semantic_validator", "backend-ordinary-01"),
            ("reader", "backend-ordinary-01"),
            ("writer", "backend-ordinary-01"),
            ("recorder", "backend-ordinary-01"),
            ("adult_scene", "backend-adult-01"),
            ("adult_filter", "backend-adult-01"),
        )
        for stage, fixture_id in cases:
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                client_type = (
                    _FakeNestedValidationRetryClient
                    if stage in {"semantic_validator", "reader"}
                    else _FakeProviderStageRetryClient
                )
                client = client_type(
                    root / "runtime",
                    failure_fixture_id=fixture_id,
                    stage=stage,
                )
                result = FullModelQualificationRunner(
                    manifest=_manifest(),
                    runtime_root=root / "runtime",
                    evidence_root=root / "evidence",
                ).run_phase(QualificationPhase.BACKEND, fixtures, client)
                self.assertEqual(result["provider_stage_retry_actions"], 1)
                status = result["results"][0 if "ordinary" in fixture_id else 5][
                    "provider_stage_retry_chains"
                ][0]["status_observations"][0]["status"]
                self.assertEqual(status["stage"], stage)
                self.assertIn(status["provider"], {"codex", "deepseek"})

    def test_deepseek_retry_uses_exact_multi_operation_success_accounting(self) -> None:
        fixtures = load_qualification_fixtures(FIXTURES)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result = FullModelQualificationRunner(
                manifest=_manifest(),
                runtime_root=root / "runtime",
                evidence_root=root / "evidence",
            ).run_phase(
                QualificationPhase.BACKEND,
                fixtures,
                _FakeProviderStageRetryClient(
                    root / "runtime",
                    failure_fixture_id="backend-ordinary-01",
                    stage="writer",
                    deepseek_success_operations=3,
                ),
            )
            first = result["results"][0]
            self.assertEqual(first["provider_operations"]["writer"], 3)
            self.assertEqual(first["deepseek_http_operations"], 5)
            self.assertEqual(first["provider_stage_retry_actions"], 1)

    def test_manual_resume_prepared_is_separate_from_retry_budget(self) -> None:
        fixtures = load_qualification_fixtures(FIXTURES)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            client = _FakeProviderStageRetryClient(
                root / "runtime",
                failure_fixture_id="backend-ordinary-01",
                stage="planner",
                prepared_resume=True,
            )
            result = FullModelQualificationRunner(
                manifest=_manifest(),
                runtime_root=root / "runtime",
                evidence_root=root / "evidence",
            ).run_phase(QualificationPhase.BACKEND, fixtures, client)
            chain = result["results"][0]["provider_stage_retry_chains"][0]
            self.assertEqual(chain["retry_action_count"], 0)
            self.assertEqual(chain["resume_prepared_action_count"], 1)
            self.assertEqual(chain["control_action_count"], 1)
            self.assertEqual(result["provider_stage_retry_actions"], 0)
            self.assertEqual(result["provider_stage_resume_prepared_actions"], 1)
            self.assertEqual(
                [value[1]["action_kind"] for value in client.action_posts],
                ["resume_prepared"],
            )

    def test_each_validation_lane_prepared_resume_joins_review_provider_free(self) -> None:
        fixtures = load_qualification_fixtures(FIXTURES)
        for stage in ("semantic_validator", "reader"):
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                result = FullModelQualificationRunner(
                    manifest=_manifest(),
                    runtime_root=root / "runtime",
                    evidence_root=root / "evidence",
                ).run_phase(
                    QualificationPhase.BACKEND,
                    fixtures,
                    _FakeNestedValidationRetryClient(
                        root / "runtime",
                        failure_fixture_id="backend-ordinary-01",
                        stage=stage,
                        prepared_resume=True,
                    ),
                )
                first = result["results"][0]
                self.assertEqual(first["provider_stage_resume_prepared_actions"], 1)
                self.assertEqual(first["provider_stage_retry_actions"], 0)
                self.assertEqual(first["provider_operations"]["reader"], 1)

    def test_each_validation_lane_exhausts_after_two_actions_with_no_fourth(
        self,
    ) -> None:
        fixtures = load_qualification_fixtures(FIXTURES)
        for stage in ("semantic_validator", "reader"):
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                client = _FakeNestedValidationRetryClient(
                    root / "runtime",
                    failure_fixture_id="backend-ordinary-01",
                    stage=stage,
                    failures_before_success=2,
                    terminal_state="attempts_exhausted",
                )
                with self.assertRaisesRegex(
                    StateConflictError,
                    "provider_stage_attempts_exhausted",
                ):
                    FullModelQualificationRunner(
                        manifest=_manifest(),
                        runtime_root=root / "runtime",
                        evidence_root=root / "evidence",
                    ).run_phase(QualificationPhase.BACKEND, fixtures, client)
                self.assertEqual(
                    [value[1]["retry_action_ordinal"] for value in client.action_posts],
                    [1, 2],
                )
                result = json.loads((root / "evidence" / "BACKEND_RESULT.json").read_text())
                critical = result["results"][0]["critical_provider_stage_failure"]
                self.assertEqual(critical["stage"], stage)
                self.assertEqual(critical["state"], "attempts_exhausted")

    def test_each_validation_lane_blocked_ambiguity_is_get_only(self) -> None:
        fixtures = load_qualification_fixtures(FIXTURES)
        for stage in ("semantic_validator", "reader"):
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                client = _FakeNestedValidationRetryClient(
                    root / "runtime",
                    failure_fixture_id="backend-ordinary-01",
                    stage=stage,
                    terminal_state="blocked_ambiguous",
                )
                with (
                    patch(
                        "cera.pi_scene.qualification.time.monotonic",
                        side_effect=(
                            0.0,
                            0.0,
                            0.0,
                            float(PROVIDER_STAGE_RETRY_STATUS_TIMEOUT_SECONDS + 1),
                        ),
                    ),
                    self.assertRaisesRegex(StateConflictError, "blocked_ambiguous"),
                ):
                    FullModelQualificationRunner(
                        manifest=_manifest(),
                        runtime_root=root / "runtime",
                        evidence_root=root / "evidence",
                    ).run_phase(QualificationPhase.BACKEND, fixtures, client)
                self.assertEqual(client.action_posts, [])
                result = json.loads((root / "evidence" / "BACKEND_RESULT.json").read_text())
                critical = result["results"][0]["critical_provider_stage_failure"]
                self.assertEqual(critical["stage"], stage)
                self.assertEqual(critical["state"], "blocked_ambiguous")

    def test_later_stage_successor_has_distinct_chain_and_budget(self) -> None:
        fixtures = load_qualification_fixtures(FIXTURES)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            client = _FakeProviderStageRetryClient(
                root / "runtime",
                failure_fixture_id="backend-ordinary-01",
                stage="writer",
                later_stage="semantic_validator",
            )
            result = FullModelQualificationRunner(
                manifest=_manifest(),
                runtime_root=root / "runtime",
                evidence_root=root / "evidence",
            ).run_phase(QualificationPhase.BACKEND, fixtures, client)
            self.assertEqual(result["provider_stage_retry_actions"], 2)
            self.assertEqual(result["provider_stage_retry_chains"], 2)
            chain = result["results"][0]["provider_stage_retry_chains"][0]
            self.assertEqual(len(chain["chain_ids"]), 2)
            self.assertEqual(len(set(chain["chain_ids"])), 2)
            self.assertEqual(
                {observation["status"]["stage"] for observation in chain["status_observations"]},
                {"writer", "semantic_validator"},
            )
            self.assertEqual(
                [post[1]["retry_action_ordinal"] for post in client.action_posts], [1, 1]
            )

    def test_regenerate_writer_uses_its_own_provider_retry_continuation(self) -> None:
        fixtures = load_qualification_fixtures(FIXTURES)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            client = _FakeProviderStageRetryClient(
                root / "runtime",
                failure_fixture_id="backend-ordinary-01",
                stage="writer",
                failure_on_regenerate=True,
            )
            result = FullModelQualificationRunner(
                manifest=_manifest(),
                runtime_root=root / "runtime",
                evidence_root=root / "evidence",
            ).run_phase(QualificationPhase.BACKEND, fixtures, client)
            first = result["results"][0]
            self.assertFalse(first["first_pass_accepted"])
            self.assertEqual(first["explicit_regenerate_actions"], 1)
            self.assertEqual(first["provider_stage_retry_actions"], 1)
            self.assertEqual(client.regenerates, 1)
            self.assertEqual(len(client.action_posts), 1)
            self.assertEqual(first["provider_operations"]["planner"], 1)
            self.assertEqual(first["provider_operations"]["writer"], 2)
            self.assertEqual(first["provider_operations"]["reader"], 2)

    def test_ambiguous_post_reconciles_by_get_without_reposting_or_leaking(self) -> None:
        fixtures = load_qualification_fixtures(FIXTURES)
        sentinel = "PRIVATE FEEDBACK STORY SENTINEL"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            client = _FakeProviderStageRetryClient(
                root / "runtime",
                failure_fixture_id="backend-ordinary-01",
                stage="writer",
                ambiguous_post=True,
                private_sentinel=sentinel,
            )
            result = FullModelQualificationRunner(
                manifest=_manifest(),
                runtime_root=root / "runtime",
                evidence_root=root / "evidence",
            ).run_phase(QualificationPhase.BACKEND, fixtures, client)
            self.assertEqual(len(client.action_posts), 1)
            self.assertGreaterEqual(len(client.status_reads), 2)
            evidence = (root / "evidence" / "QUALIFICATION_EVENTS.jsonl").read_text(
                encoding="utf-8"
            )
            self.assertNotIn(sentinel, evidence)
            self.assertNotIn(sentinel, json.dumps(result, sort_keys=True))
            receipt = result["results"][0]["provider_stage_retry_chains"][0]["actions"][0]
            self.assertEqual(receipt["post_failure"]["failure_category"], "transport_io")

    def test_attempt_exhaustion_stops_after_two_exact_actions_with_no_fourth(self) -> None:
        fixtures = load_qualification_fixtures(FIXTURES)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            client = _FakeProviderStageRetryClient(
                root / "runtime",
                failure_fixture_id="backend-ordinary-01",
                stage="planner",
                failures_before_success=2,
                terminal_state="attempts_exhausted",
            )
            with self.assertRaisesRegex(
                StateConflictError,
                "provider_stage_attempts_exhausted",
            ):
                FullModelQualificationRunner(
                    manifest=_manifest(),
                    runtime_root=root / "runtime",
                    evidence_root=root / "evidence",
                ).run_phase(QualificationPhase.BACKEND, fixtures, client)
            self.assertEqual(len(client.action_posts), 2)
            self.assertEqual(
                [post[1]["retry_action_ordinal"] for post in client.action_posts],
                [1, 2],
            )
            result = json.loads((root / "evidence" / "BACKEND_RESULT.json").read_text())
            failure = result["results"][0]
            self.assertEqual(failure["failure_category"], "provider_stage_attempts_exhausted")
            self.assertEqual(
                failure["critical_provider_stage_failure"]["stage_attempts_total"],
                3,
            )

    def test_nonretry_terminal_never_dispatches(self) -> None:
        fixtures = load_qualification_fixtures(FIXTURES)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            client = _FakeProviderStageRetryClient(
                root / "runtime",
                failure_fixture_id="backend-ordinary-01",
                stage="planner",
                terminal_state="recovery_required",
            )
            with self.assertRaisesRegex(StateConflictError, "recovery_required"):
                FullModelQualificationRunner(
                    manifest=_manifest(),
                    runtime_root=root / "runtime",
                    evidence_root=root / "evidence",
                ).run_phase(QualificationPhase.BACKEND, fixtures, client)
            self.assertEqual(client.action_posts, [])

    def test_recorder_repair_uses_one_fresh_successor_chain(self) -> None:
        fixtures = load_qualification_fixtures(FIXTURES)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            client = _FakeProviderStageRetryClient(
                root / "runtime",
                failure_fixture_id="backend-ordinary-01",
                stage="recorder",
                failures_before_success=2,
                terminal_state="recording_repair_required",
            )
            result = FullModelQualificationRunner(
                manifest=_manifest(),
                runtime_root=root / "runtime",
                evidence_root=root / "evidence",
            ).run_phase(QualificationPhase.BACKEND, fixtures, client)
            first = result["results"][0]
            chain = first["provider_stage_retry_chains"][0]
            self.assertEqual(chain["retry_action_count"], 3)
            self.assertEqual(chain["repair_recording_action_count"], 1)
            self.assertEqual(chain["control_action_count"], 4)
            self.assertEqual(len(chain["chain_ids"]), 2)
            self.assertEqual(
                [action[1]["action_kind"] for action in client.action_posts],
                ["provider_retry", "provider_retry", "repair_recording", "provider_retry"],
            )
            self.assertEqual(
                [
                    action[1]["retry_action_ordinal"]
                    for action in client.action_posts
                    if action[1]["action_kind"] == "provider_retry"
                ],
                [1, 2, 1],
            )
            self.assertEqual(result["provider_stage_repair_recording_actions"], 1)
            self.assertEqual(result["automatic_provider_stage_control_actions"], 0)

    def test_recorder_repair_successor_exhaustion_never_repairs_recursively(self) -> None:
        fixtures = load_qualification_fixtures(FIXTURES)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            client = _FakeProviderStageRetryClient(
                root / "runtime",
                failure_fixture_id="backend-ordinary-01",
                stage="recorder",
                failures_before_success=2,
                terminal_state="recording_repair_required",
                repair_successor_exhausts=True,
            )
            with self.assertRaisesRegex(StateConflictError, "recording_repair_required"):
                FullModelQualificationRunner(
                    manifest=_manifest(),
                    runtime_root=root / "runtime",
                    evidence_root=root / "evidence",
                ).run_phase(QualificationPhase.BACKEND, fixtures, client)
            action_kinds = [action[1]["action_kind"] for action in client.action_posts]
            self.assertEqual(action_kinds.count("repair_recording"), 1)
            self.assertEqual(action_kinds.count("provider_retry"), 4)
            result = json.loads((root / "evidence" / "BACKEND_RESULT.json").read_text())
            failure = result["results"][0]
            self.assertEqual(
                failure["critical_provider_stage_failure"]["state"],
                "recording_repair_required",
            )
            self.assertEqual(failure["provider_stage_repair_recording_actions"], 1)

    def test_blocked_ambiguity_timeout_is_get_only_and_critical(self) -> None:
        fixtures = load_qualification_fixtures(FIXTURES)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            client = _FakeProviderStageRetryClient(
                root / "runtime",
                failure_fixture_id="backend-ordinary-01",
                stage="writer",
                terminal_state="blocked_ambiguous",
            )
            with (
                patch(
                    "cera.pi_scene.qualification.time.monotonic",
                    side_effect=(
                        0.0,
                        0.0,
                        float(PROVIDER_STAGE_RETRY_STATUS_TIMEOUT_SECONDS + 1),
                    ),
                ),
                self.assertRaisesRegex(StateConflictError, "blocked_ambiguous"),
            ):
                FullModelQualificationRunner(
                    manifest=_manifest(),
                    runtime_root=root / "runtime",
                    evidence_root=root / "evidence",
                ).run_phase(QualificationPhase.BACKEND, fixtures, client)
            self.assertEqual(client.action_posts, [])
            self.assertEqual(client.status_reads, [])
            result = json.loads((root / "evidence" / "BACKEND_RESULT.json").read_text())
            critical = result["results"][0]["critical_provider_stage_failure"]
            self.assertEqual(critical["state"], "blocked_ambiguous")
            self.assertEqual(critical["stop_reason"], "blocked_ambiguous_timeout")

    def test_direct_generic_relay_preflight_is_exact_reload_safe_and_provider_free(self) -> None:
        token = "relay-token-" + "x" * 32
        port = entrypoint._available_port_excluding(5101)
        upstream = entrypoint._GenericFakeRelayUpstreamV1(
            port=port,
            decline_review_id="review-0123456789abcdef0123456789ab",
            regenerate_review_id="review-fedcba9876543210fedcba987654",
            authorization_token=token,
            chain_id="stage-retry-" + "1" * 64,
        )
        upstream.start()
        try:
            client = entrypoint.DirectCeraQualificationClient(
                base_url=f"http://127.0.0.1:{port}",
                token=token,
            )
            first = client.provider_stage_retry_status(chain_id=upstream.exhaustion_chain_id)
            action_1 = first.body["actions"][0]
            ambiguous = client.provider_stage_retry_action(
                chain_id=upstream.exhaustion_chain_id,
                action=action_1,
            )
            reloaded = entrypoint.DirectCeraQualificationClient(
                base_url=f"http://127.0.0.1:{port}",
                token=token,
            )
            second = reloaded.provider_stage_retry_status(chain_id=upstream.exhaustion_chain_id)
            action_2 = second.body["actions"][0]
            reloaded.provider_stage_retry_action(
                chain_id=upstream.exhaustion_chain_id,
                action=action_2,
            )
            exhausted = reloaded.provider_stage_retry_status(chain_id=upstream.exhaustion_chain_id)
            source = client.provider_stage_retry_status(chain_id=upstream.successor_source_chain_id)
            client.provider_stage_retry_action(
                chain_id=upstream.successor_source_chain_id,
                action=source.body["actions"][0],
            )
            later = client.provider_stage_retry_status(chain_id=upstream.successor_source_chain_id)
            self.assertEqual(
                later.body["status"]["chain_id"],
                upstream.successor_later_chain_id,
            )
            self.assertEqual(later.body["status"]["stage"], "reader")
            self.assertEqual(later.body["status"]["model_family"], "sol")
            client.provider_stage_retry_action(
                chain_id=upstream.successor_later_chain_id,
                action=later.body["actions"][0],
            )
            completion = client.provider_stage_retry_status(
                chain_id=upstream.successor_source_chain_id
            )
            recovery = client.provider_stage_retry_status(chain_id=upstream.recovery_chain_id)
            blocked = client.provider_stage_retry_status(chain_id=upstream.blocked_chain_id)
            prepared = client.provider_stage_retry_status(
                chain_id=upstream.resume_prepared_chain_id
            )
            prepared_action = prepared.body["actions"][0]
            self.assertEqual(prepared.body["status"]["stage"], "reader")
            self.assertEqual(prepared.body["status"]["model_family"], "sol")
            client.provider_stage_retry_action(
                chain_id=upstream.resume_prepared_chain_id,
                action=prepared_action,
            )
            prepared_completion = client.provider_stage_retry_status(
                chain_id=upstream.resume_prepared_chain_id
            )
            repair = client.provider_stage_retry_status(chain_id=upstream.repair_source_chain_id)
            repair_action = repair.body["actions"][0]
            client.provider_stage_retry_action(
                chain_id=upstream.repair_source_chain_id,
                action=repair_action,
            )
            repair_successor = client.provider_stage_retry_status(
                chain_id=upstream.repair_source_chain_id
            )
            client.provider_stage_retry_action(
                chain_id=upstream.repair_successor_chain_id,
                action=repair_successor.body["actions"][0],
            )
            repair_completion = client.provider_stage_retry_status(
                chain_id=upstream.repair_source_chain_id
            )
            repair_terminal = client.provider_stage_retry_status(
                chain_id=upstream.repair_terminal_chain_id
            )
            proof = upstream.retry_proof()
        finally:
            upstream.close()
        self.assertEqual(ambiguous.status_code, 504)
        self.assertEqual(action_1["retry_action_ordinal"], 1)
        self.assertEqual(action_2["retry_action_ordinal"], 2)
        self.assertEqual(exhausted.body["status"]["state"], "attempts_exhausted")
        self.assertEqual(exhausted.body["actions"], [])
        self.assertEqual(completion.body["schema_version"], "cera.pi_scene.review_decision.v3")
        self.assertEqual(completion.body["creator_action"], "automatic_accept")
        self.assertEqual(recovery.body["status"]["state"], "recovery_required")
        self.assertEqual(blocked.body["status"]["state"], "blocked_ambiguous")
        self.assertEqual(prepared_action["action_kind"], "resume_prepared")
        self.assertFalse(prepared_action["consumes_retry_action"])
        self.assertEqual(
            prepared_completion.body["schema_version"],
            "cera.pi_scene.review_decision.v3",
        )
        self.assertEqual(repair_action["action_kind"], "repair_recording")
        self.assertFalse(repair_action["consumes_retry_action"])
        self.assertEqual(
            repair_successor.body["status"]["chain_id"],
            upstream.repair_successor_chain_id,
        )
        self.assertEqual(
            repair_completion.body["schema_version"],
            "cera.pi_scene.review_decision.v3",
        )
        self.assertEqual(repair_completion.body["creator_action"], "repair_recording")
        self.assertEqual(repair_terminal.body["actions"], [])
        self.assertEqual(proof["exact_action_posts"], 7)
        self.assertEqual(proof["provider_calls"], 0)

    def test_isolated_probe_validates_v3_reader_successor_and_terminal_reloads(
        self,
    ) -> None:
        token = "relay-token-" + "y" * 32
        port = entrypoint._available_port_excluding(5101)
        upstream = entrypoint._GenericFakeRelayUpstreamV1(
            port=port,
            decline_review_id="review-0123456789abcdef0123456789ab",
            regenerate_review_id="review-fedcba9876543210fedcba987654",
            authorization_token=token,
            chain_id="stage-retry-" + "1" * 64,
        )
        upstream.start()
        isolated = object.__new__(entrypoint.IsolatedSillyTavernQualificationClient)
        base_url = f"http://127.0.0.1:{port}"

        def relay(
            *,
            path: str,
            method: str,
            payload: dict[str, Any] | None = None,
        ) -> ClientResponseV1:
            upstream_path = path.removeprefix("/api/plugins/cera-review")
            if method == "GET":
                return entrypoint._get_json_response(
                    base_url + upstream_path,
                    token=token,
                    transport="fake-isolated-relay",
                    path=path,
                )
            if method == "POST" and payload is not None:
                return entrypoint._post_json(
                    base_url + upstream_path,
                    token=token,
                    payload=payload,
                    transport="fake-isolated-relay",
                    path=path,
                )
            raise AssertionError("isolated relay probe changed HTTP method")

        try:
            with patch.object(isolated, "_relay", side_effect=relay):
                proof = isolated.probe_provider_stage_relay(
                    decline_review_id="review-0123456789abcdef0123456789ab",
                    regenerate_review_id="review-fedcba9876543210fedcba987654",
                    exhaustion_chain_id=upstream.exhaustion_chain_id,
                    successor_source_chain_id=upstream.successor_source_chain_id,
                    successor_later_chain_id=upstream.successor_later_chain_id,
                    recovery_chain_id=upstream.recovery_chain_id,
                    blocked_chain_id=upstream.blocked_chain_id,
                    resume_prepared_chain_id=upstream.resume_prepared_chain_id,
                    repair_source_chain_id=upstream.repair_source_chain_id,
                    repair_successor_chain_id=upstream.repair_successor_chain_id,
                    repair_terminal_chain_id=upstream.repair_terminal_chain_id,
                )
            upstream_proof = upstream.retry_proof()
        finally:
            upstream.close()
        self.assertEqual(proof["provider_calls"], 0)
        self.assertEqual(len(proof["exact_action_sha256s"]), 7)
        for key in (
            "regenerate_sha256",
            "regenerate_terminal_sha256",
            "terminal_completion_sha256",
            "accepted_terminal_reload_sha256",
            "repair_completion_sha256",
            "repair_completion_reload_sha256",
        ):
            self.assertRegex(str(proof[key]), r"^[a-f0-9]{64}$")
        self.assertEqual(upstream_proof["provider_calls"], 0)
        self.assertTrue(upstream_proof["private_sentinel_absent"])

    def test_isolated_client_relays_exact_generic_action_without_invention(self) -> None:
        chain_id = "stage-retry-" + "a" * 64
        action = {
            "schema_version": "cera.provider_stage_retry_action.v1",
            "action_id": "stage-action-" + "b" * 64,
            "chain_id": chain_id,
            "action_family": "provider_stage_control",
            "action_kind": "provider_retry",
            "automatic": False,
            "provider_dispatch_authorized": True,
            "consumes_retry_action": True,
            "retry_action_ordinal": 1,
            "whole_request_replay_authorized": False,
            "provider_substitution_authorized": False,
            "expected_chain_sha256": "c" * 64,
        }
        client = object.__new__(entrypoint.IsolatedSillyTavernQualificationClient)
        response = ClientResponseV1(
            transport="fake",
            path="/relay",
            status_code=200,
            duration_ms=0,
            body={"ok": True},
        )
        with patch.object(client, "_relay", return_value=response) as relay:
            observed = client.provider_stage_retry_action(
                chain_id=chain_id,
                action=action,
            )
        self.assertIs(observed, response)
        relay.assert_called_once_with(
            path=(
                "/api/plugins/cera-review/v1/cera/provider-stage-retries/"
                f"{chain_id}/actions/{action['action_id']}"
            ),
            method="POST",
            payload=action,
        )

    def test_isolated_client_relays_exact_manual_control_actions(self) -> None:
        client = object.__new__(entrypoint.IsolatedSillyTavernQualificationClient)
        response = ClientResponseV1(
            transport="fake",
            path="/relay",
            status_code=200,
            duration_ms=0,
            body={"ok": True},
        )
        for index, action_kind in enumerate(
            ("resume_prepared", "repair_recording"),
            start=1,
        ):
            with self.subTest(action_kind=action_kind):
                chain_id = f"stage-retry-{index:064x}"
                action = {
                    "schema_version": "cera.provider_stage_retry_action.v1",
                    "action_id": f"stage-action-{index + 2:064x}",
                    "chain_id": chain_id,
                    "action_family": "provider_stage_control",
                    "action_kind": action_kind,
                    "automatic": False,
                    "provider_dispatch_authorized": True,
                    "consumes_retry_action": False,
                    "retry_action_ordinal": None,
                    "whole_request_replay_authorized": False,
                    "provider_substitution_authorized": False,
                    "expected_chain_sha256": f"{index + 4:064x}",
                }
                with patch.object(client, "_relay", return_value=response) as relay:
                    observed = client.provider_stage_retry_action(
                        chain_id=chain_id,
                        action=action,
                    )
                self.assertIs(observed, response)
                relay.assert_called_once_with(
                    path=(
                        "/api/plugins/cera-review/v1/cera/provider-stage-retries/"
                        f"{chain_id}/actions/{action['action_id']}"
                    ),
                    method="POST",
                    payload=action,
                )

    def test_soft_ordinary_rejection_uses_one_writer_and_standing_policy_only(self) -> None:
        fixtures = load_qualification_fixtures(FIXTURES)
        fixture_id = "backend-ordinary-01"
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
                standing_policy_fixture_id=fixture_id,
            )
            campaign = runner.start_phase(QualificationPhase.BACKEND, fixtures)
            campaign.run_segment(client=client, runtime_root=runtime, turn_count=1)

            self.assertIsNone(campaign.failure)
            result = campaign.results[0]
            self.assertEqual(result["fixture_id"], fixture_id)
            self.assertFalse(result["first_pass_accepted"])
            self.assertTrue(result["first_pass_policy_provisional"])
            self.assertEqual(result["standing_policy_provisional_acceptances"], 1)
            self.assertEqual(result["explicit_regenerate_actions"], 0)
            self.assertEqual(result["externally_authorized_manual_actions"], 0)
            self.assertEqual(result["automatic_manual_actions"], 0)
            self.assertEqual(result["provider_operations"]["writer"], 1)
            self.assertEqual(client.calls, 1)
            self.assertEqual(client.regenerates, 0)

            review = next(iter(client.reviews.values()))
            self.assertEqual(review["schema_version"], "cera.pi_scene.review.v3")
            self.assertEqual(review["state"], "accepted")
            self.assertEqual(review["gate_status"], "reject")
            self.assertEqual(review["acceptance"]["mode"], "standing_policy")
            self.assertEqual(review["acceptance"]["canon_status"], "provisional")
            self.assertEqual(len(review["provider_attempts"]), 1)
            self.assertEqual(review["provider_attempts"][0]["disposition"], "luna_rejected")
            self.assertEqual(
                review["checks"]["luna"]["failures"],
                [
                    {
                        "code": "omitted_decision",
                        "concise_explanation": "The candidate omitted one planned beat.",
                        "source_kind": "verdict_conflict",
                        "feedback_scope": None,
                    }
                ],
            )
            self.assertFalse(any(review["actions"].values()))

            evidence = _jsonl(root / "evidence" / "QUALIFICATION_EVENTS.jsonl")
            policy_events = [
                value for value in evidence if value.get("event") == "first_pass_policy_provisional"
            ]
            self.assertEqual(len(policy_events), 1)
            self.assertEqual(
                policy_events[0]["tolerated_reason_codes"],
                ["luna:omitted_decision"],
            )
            self.assertRegex(str(policy_events[0]["audit_sha256"]), r"^[a-f0-9]{64}$")
            self.assertFalse(any(value.get("event") == "first_pass_rejected" for value in evidence))
            self.assertFalse(
                any(value.get("event") == "manual_action_required" for value in evidence)
            )

    def test_hard_ordinary_rejection_remains_one_explicit_regenerate(self) -> None:
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
            campaign = runner.start_phase(QualificationPhase.BACKEND, fixtures)
            campaign.run_segment(client=client, runtime_root=runtime, turn_count=1)
            self.assertIsNone(campaign.failure)
            result = campaign.results[0]
            self.assertFalse(result["first_pass_accepted"])
            self.assertFalse(result["first_pass_policy_provisional"])
            self.assertEqual(result["standing_policy_provisional_acceptances"], 0)
            self.assertEqual(result["explicit_regenerate_actions"], 1)
            self.assertEqual(result["externally_authorized_manual_actions"], 1)
            self.assertEqual(result["provider_operations"]["writer"], 2)
            self.assertEqual(client.regenerates, 1)
            predecessor = next(iter(client.reviews.values()))
            self.assertEqual(predecessor["gate_status"], "reject")
            self.assertEqual(
                predecessor["checks"]["luna"]["failures"][0]["code"],
                "severe_incompleteness",
            )
            evidence = _jsonl(root / "evidence" / "QUALIFICATION_EVENTS.jsonl")
            self.assertEqual(
                sum(value.get("event") == "first_pass_rejected" for value in evidence),
                1,
            )
            self.assertEqual(
                sum(value.get("event") == "manual_action_required" for value in evidence),
                1,
            )
            self.assertFalse(
                any(value.get("event") == "first_pass_policy_provisional" for value in evidence)
            )

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
            campaign = runner.start_phase(QualificationPhase.BACKEND, fixtures)
            campaign.run_segment(client=client, runtime_root=runtime, turn_count=7)
            self.assertIsNone(campaign.failure)
            adult_two = next(
                value for value in campaign.results if value["fixture_id"] == "backend-adult-02"
            )
            self.assertEqual(adult_two["provider_operations"]["planner"], 0)
            self.assertFalse(adult_two["first_pass_accepted"])
            self.assertFalse(adult_two["first_pass_policy_provisional"])
            self.assertEqual(adult_two["standing_policy_provisional_acceptances"], 0)
            self.assertEqual(adult_two["explicit_regenerate_actions"], 1)
            self.assertEqual(
                sum(
                    int(value["standing_policy_provisional_acceptances"])
                    for value in campaign.results
                ),
                0,
            )
            self.assertEqual(client.regenerates, 1)

    def test_joined_rejection_regenerate_accounts_luna_and_reader_exactly(self) -> None:
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
            self.assertEqual(repaired["provider_operations"]["reader"], 2)
            self.assertEqual(repaired["provider_operations"]["recorder"], 1)
            self.assertEqual(repaired["sol_http_operations"], 5)
            self.assertEqual(repaired["deepseek_http_operations"], 3)
            self.assertFalse(repaired["first_pass_accepted"])
            self.assertEqual(repaired["automatic_repair_actions"], 0)
            self.assertEqual(repaired["explicit_regenerate_actions"], 1)
            self.assertEqual(result["first_pass_accepted"], 19)
            self.assertEqual(result["automatic_repair_actions"], 0)
            self.assertEqual(result["explicit_regenerate_actions"], 1)

    def test_review_authorized_recorder_repair_posts_once_and_reloads_terminal(
        self,
    ) -> None:
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
                recording_repair_fixture_id="backend-ordinary-01",
            )
            campaign = runner.start_phase(QualificationPhase.BACKEND, fixtures)
            campaign.run_segment(
                client=client,
                runtime_root=runtime,
                turn_count=1,
            )
            result = campaign.results[0]
            self.assertEqual(len(client.review_actions), 1)
            repair_review_id, repair_action = client.review_actions[0]
            self.assertEqual(repair_review_id, next(iter(client.reviews)))
            self.assertEqual(repair_action, {"action": "repair_recording"})
            self.assertEqual(client.terminal_decision_reads, [repair_review_id])
            self.assertEqual(
                client.review_reads,
                [repair_review_id, repair_review_id, repair_review_id],
            )
            self.assertEqual(result["provider_operations"]["recorder"], 1)
            self.assertEqual(result["provider_stage_repair_recording_actions"], 0)
            self.assertEqual(result["review_recording_repair_actions"], 1)
            self.assertEqual(result["externally_authorized_manual_actions"], 1)
            self.assertEqual(result["automatic_manual_actions"], 0)
            self.assertTrue(result["first_pass_accepted"])
            self.assertRegex(
                str(result["terminal_review_decision_sha256"]),
                r"^[a-f0-9]{64}$",
            )

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
            baseline = root / LEGACY_FIXTURES.name
            baseline.write_bytes(LEGACY_FIXTURES.read_bytes())
            historical = root / HISTORICAL_FIXTURES_V2.name
            historical.write_bytes(HISTORICAL_FIXTURES_V2.read_bytes())
            historical_v3 = root / HISTORICAL_FIXTURES_V3.name
            historical_v3.write_bytes(HISTORICAL_FIXTURES_V3.read_bytes())
            historical_v4 = root / HISTORICAL_FIXTURES_V4.name
            historical_v4.write_bytes(HISTORICAL_FIXTURES_V4.read_bytes())
            historical_v5 = root / HISTORICAL_FIXTURES_V5.name
            historical_v5.write_bytes(HISTORICAL_FIXTURES_V5.read_bytes())
            historical_v6 = root / HISTORICAL_FIXTURES_V6.name
            historical_v6.write_bytes(HISTORICAL_FIXTURES_V6.read_bytes())
            historical_v7 = root / HISTORICAL_FIXTURES_V7.name
            historical_v7.write_bytes(HISTORICAL_FIXTURES_V7.read_bytes())
            historical_v8 = root / HISTORICAL_FIXTURES_V8.name
            historical_v8.write_bytes(HISTORICAL_FIXTURES_V8.read_bytes())
            historical_v9 = root / HISTORICAL_FIXTURES_V9.name
            historical_v9.write_bytes(HISTORICAL_FIXTURES_V9.read_bytes())
            historical_v10 = root / HISTORICAL_FIXTURES_V10.name
            historical_v10.write_bytes(HISTORICAL_FIXTURES_V10.read_bytes())
            historical_v11 = root / HISTORICAL_FIXTURES_V11.name
            historical_v11.write_bytes(HISTORICAL_FIXTURES_V11.read_bytes())
            historical_v12 = root / HISTORICAL_FIXTURES_V12.name
            historical_v12.write_bytes(HISTORICAL_FIXTURES_V12.read_bytes())
            historical_v13 = root / HISTORICAL_FIXTURES_V13.name
            historical_v13.write_bytes(HISTORICAL_FIXTURES_V13.read_bytes())
            historical_v14 = root / HISTORICAL_FIXTURES_V14.name
            historical_v14.write_bytes(HISTORICAL_FIXTURES_V14.read_bytes())
            historical_v15 = root / HISTORICAL_FIXTURES_V15.name
            historical_v15.write_bytes(HISTORICAL_FIXTURES_V15.read_bytes())
            historical_v16 = root / HISTORICAL_FIXTURES_V16.name
            historical_v16.write_bytes(HISTORICAL_FIXTURES_V16.read_bytes())
            historical_v17 = root / HISTORICAL_FIXTURES_V17.name
            historical_v17.write_bytes(HISTORICAL_FIXTURES_V17.read_bytes())
            historical_v18 = root / HISTORICAL_FIXTURES_V18.name
            historical_v18.write_bytes(HISTORICAL_FIXTURES_V18.read_bytes())
            historical_v19 = root / HISTORICAL_FIXTURES_V19.name
            historical_v19.write_bytes(HISTORICAL_FIXTURES_V19.read_bytes())
            historical_v20 = root / HISTORICAL_FIXTURES_V20.name
            historical_v20.write_bytes(HISTORICAL_FIXTURES_V20.read_bytes())
            historical_v21 = root / HISTORICAL_FIXTURES_V21.name
            historical_v21.write_bytes(HISTORICAL_FIXTURES_V21.read_bytes())
            historical_v22 = root / HISTORICAL_FIXTURES_V22.name
            historical_v22.write_bytes(HISTORICAL_FIXTURES_V22.read_bytes())
            historical_v23 = root / HISTORICAL_FIXTURES_V23.name
            historical_v23.write_bytes(HISTORICAL_FIXTURES_V23.read_bytes())
            historical_v24 = root / HISTORICAL_FIXTURES_V24.name
            historical_v24.write_bytes(HISTORICAL_FIXTURES_V24.read_bytes())
            manifest = build_qualification_manifest(
                repository_root=root,
                qualification_id="qualification-test-20260809",
                source_commit="a" * 40,
                source_tree="b" * 40,
                fixture_path=fixture,
                repository_artifacts={
                    "test": (
                        Path("artifact.txt"),
                        Path("fixtures.json"),
                        Path(LEGACY_FIXTURES.name),
                        Path(HISTORICAL_FIXTURES_V2.name),
                        Path(HISTORICAL_FIXTURES_V3.name),
                        Path(HISTORICAL_FIXTURES_V4.name),
                        Path(HISTORICAL_FIXTURES_V5.name),
                        Path(HISTORICAL_FIXTURES_V6.name),
                        Path(HISTORICAL_FIXTURES_V7.name),
                        Path(HISTORICAL_FIXTURES_V8.name),
                        Path(HISTORICAL_FIXTURES_V9.name),
                        Path(HISTORICAL_FIXTURES_V10.name),
                        Path(HISTORICAL_FIXTURES_V11.name),
                        Path(HISTORICAL_FIXTURES_V12.name),
                        Path(HISTORICAL_FIXTURES_V13.name),
                        Path(HISTORICAL_FIXTURES_V14.name),
                        Path(HISTORICAL_FIXTURES_V15.name),
                        Path(HISTORICAL_FIXTURES_V16.name),
                        Path(HISTORICAL_FIXTURES_V17.name),
                        Path(HISTORICAL_FIXTURES_V18.name),
                        Path(HISTORICAL_FIXTURES_V19.name),
                        Path(HISTORICAL_FIXTURES_V20.name),
                        Path(HISTORICAL_FIXTURES_V21.name),
                        Path(HISTORICAL_FIXTURES_V22.name),
                        Path(HISTORICAL_FIXTURES_V23.name),
                        Path(HISTORICAL_FIXTURES_V24.name),
                    )
                },
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
                frozen["provider_stage_retry_server_bridge_contract"],
                "cera.provider_stage_retry.status_envelope_forward.v1",
            )
            self.assertEqual(
                frozen["provider_stage_retry_terminal_ui_contract"],
                {
                    "schema_version": ("cera.pi_scene.qualification_critical_provider_stage_ui.v2"),
                    "projection_key": "critical_provider_stage_failure",
                    "severity": "critical",
                    "provider_required": True,
                    "stage_required": True,
                    "maximum_attempts_per_stage_occurrence": 3,
                    "maximum_retry_actions_per_stage_occurrence": 2,
                    "maximum_resume_prepared_actions_per_stage_occurrence": 1,
                    "maximum_recording_repair_actions_per_request": 1,
                    "maximum_recording_repair_successor_attempts": 3,
                    "maximum_recording_repair_successor_retry_actions": 2,
                    "manual_resume_prepared_enabled_when_backend_issued": True,
                    "manual_recording_repair_enabled_when_backend_issued": True,
                    "recursive_recording_repair": False,
                    "recording_repair_required_terminal_only_without_action": True,
                    "terminal_states": [
                        "attempts_exhausted",
                        "recording_repair_required",
                        "recovery_required",
                        "blocked_ambiguous_timeout",
                    ],
                    "terminal_provider_retry_enabled": False,
                    "collapsible": True,
                    "display_fields": [
                        "severity",
                        "provider",
                        "model_family",
                        "stage",
                        "state",
                        "maximum_attempts",
                        "stage_attempts_total",
                        "retry_actions_accepted",
                        "failure_category",
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
                "router.get('/v1/cera/provider-stage-retries/:chainId');"
                "cera.provider_stage_retry_status_envelope.v1",
                encoding="utf-8",
            )
            (proxy / "package.json").write_text("{}", encoding="utf-8")
            (proxy / "test.mjs").write_text(
                "import test from 'node:test'; test('proxy', () => {});",
                encoding="utf-8",
            )
            (extension / "index.js").write_text(
                "cera_provider_stage_retry_receipts_v1;"
                "restoreProviderStageRetryForCurrentChat({ reconcile: true });"
                "provider_stage_retry_completion",
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
            self.assertIn("/v1/cera/provider-stage-retries/:chainId", staged_proxy)
            self.assertIn("cera.provider_stage_retry_status_envelope.v1", staged_proxy)
            staged_extension = (
                target / "public/scripts/extensions/third-party/cera-creator-review/index.js"
            ).read_text(encoding="utf-8")
            self.assertIn("cera_provider_stage_retry_receipts_v1", staged_extension)
            self.assertIn("restoreProviderStageRetryForCurrentChat", staged_extension)
            self.assertIn("provider_stage_retry_completion", staged_extension)
            openai = (target / "public/scripts/openai.js").read_text(encoding="utf-8")
            self.assertIn("window.ceraCaptureCompletionMetadata(data.cera)", openai)
            self.assertEqual(openai.count("window.ceraCaptureProviderStageRetryStatus(data)"), 1)
            self.assertEqual(openai.count("window.ceraCaptureTransportFailure(data)"), 1)
            self.assertNotIn("data?.cera?.provisional", openai)
            backend = (target / "src/endpoints/backends/chat-completions.js").read_text(
                encoding="utf-8"
            )
            self.assertIn("cera.provider_stage_retry_status_envelope.v1", backend)
            self.assertIn("ceraProviderStageRetryEnvelope", backend)
            self.assertNotIn("debug_log_path", backend)
            manifest_path = target / "CERA_QUALIFICATION_ISOLATED_COPY_MANIFEST.json"
            changed_manifest = deepcopy(manifest)
            changed_manifest["provider_stage_retry_terminal_ui_contract"][
                "maximum_retry_actions_per_stage_occurrence"
            ] = 3
            unsigned = {
                key: value for key, value in changed_manifest.items() if key != "manifest_sha256"
            }
            changed_manifest["manifest_sha256"] = canonical_sha256(unsigned)
            manifest_path.write_bytes(canonical_bytes(changed_manifest) + b"\n")
            with self.assertRaisesRegex(StateConflictError, "provider-stage retry bridge changed"):
                verify_qualification_sillytavern(target)
            manifest_path.write_bytes(canonical_bytes(manifest) + b"\n")
            (target / "src/endpoints/backends/chat-completions.js").write_text(
                "tampered",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(StateConflictError, "provider-stage retry bridge changed"):
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

    def test_manual_action_request_closes_body_kind_and_identity(self) -> None:
        review_id = "review-0123456789abcdef0123456789ab"
        candidate_sha256 = "c" * 64
        action_id = "review-action-" + text_sha256(f"{review_id}:{candidate_sha256}:regenerate")
        mutable_action = {"action": "regenerate"}
        valid = QualificationManualActionRequestV1(
            qualification_id="qualification-binding-test",
            phase=QualificationPhase.BACKEND,
            turn_index=1,
            fixture_id="backend-ordinary-01",
            action_family="semantic_regenerate",
            action_kind="regenerate",
            action_id=action_id,
            exact_action=mutable_action,
            authority_sha256="a" * 64,
            review_id=review_id,
            candidate_sha256=candidate_sha256,
        )
        mutable_action["action"] = "repair_recording"
        self.assertEqual(dict(valid.exact_action), {"action": "regenerate"})
        with self.assertRaisesRegex(ContractValidationError, "body changed"):
            QualificationManualActionRequestV1(
                qualification_id=valid.qualification_id,
                phase=valid.phase,
                turn_index=valid.turn_index,
                fixture_id=valid.fixture_id,
                action_family=valid.action_family,
                action_kind=valid.action_kind,
                action_id=valid.action_id,
                exact_action={"action": "repair_recording"},
                authority_sha256=valid.authority_sha256,
                review_id=valid.review_id,
                candidate_sha256=valid.candidate_sha256,
            )
        with self.assertRaisesRegex(ContractValidationError, "identity changed"):
            QualificationManualActionRequestV1(
                qualification_id=valid.qualification_id,
                phase=valid.phase,
                turn_index=valid.turn_index,
                fixture_id=valid.fixture_id,
                action_family=valid.action_family,
                action_kind=valid.action_kind,
                action_id="review-action-" + "d" * 64,
                exact_action={"action": "regenerate"},
                authority_sha256=valid.authority_sha256,
                review_id=valid.review_id,
                candidate_sha256=valid.candidate_sha256,
            )

        with tempfile.TemporaryDirectory() as temporary:
            fake = _FakeProviderStageRetryClient(
                Path(temporary),
                failure_fixture_id="backend-ordinary-01",
            )
            envelope = fake._envelope("planner", attempts=1, retries=0, state="eligible")
            status = envelope["status"]
            action = envelope["actions"][0]
            with self.assertRaisesRegex(ContractValidationError, "bound identity"):
                QualificationManualActionRequestV1(
                    qualification_id="qualification-binding-test",
                    phase=QualificationPhase.BACKEND,
                    turn_index=1,
                    fixture_id="backend-ordinary-01",
                    action_family="provider_stage_control",
                    action_kind="provider_retry",
                    action_id="stage-action-" + "f" * 64,
                    exact_action=action,
                    authority_sha256=canonical_sha256(envelope),
                    chain_id=status["chain_id"],
                    stage=status["stage"],
                    provider=status["provider"],
                    model_family=status["model_family"],
                    retry_action_ordinal=1,
                )

    def test_default_runner_posts_no_manual_action_for_every_action_kind(self) -> None:
        fixtures = load_qualification_fixtures(FIXTURES)

        def assert_manual_stop(campaign: Any) -> None:
            self.assertIsInstance(campaign.failure, ManualActionRequiredError)
            failed = campaign.results[-1]
            self.assertEqual(failed["failure_category"], "manual_action_required")
            self.assertRegex(failed["manual_action_checkpoint_sha256"], r"^[a-f0-9]{64}$")
            self.assertEqual(failed["externally_authorized_manual_actions"], 0)
            self.assertEqual(failed["automatic_manual_actions"], 0)

        provider_cases: dict[
            str,
            Callable[[Path], _FakeProviderStageRetryClient],
        ] = {
            "provider_retry": lambda runtime: _FakeProviderStageRetryClient(
                runtime,
                failure_fixture_id="backend-ordinary-01",
            ),
            "resume_prepared": lambda runtime: _FakeProviderStageRetryClient(
                runtime,
                failure_fixture_id="backend-ordinary-01",
                prepared_resume=True,
            ),
            "provider_recording_repair": lambda runtime: _InitialProviderRecordingRepairClient(
                runtime,
                failure_fixture_id="backend-ordinary-01",
                stage="recorder",
            ),
        }
        for label, provider_factory in provider_cases.items():
            with self.subTest(action_kind=label), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                runtime = root / "runtime"
                provider_client = provider_factory(runtime)
                campaign = _ProductionFullModelQualificationRunner(
                    manifest=_manifest(),
                    runtime_root=runtime,
                    evidence_root=root / "evidence",
                ).start_phase(QualificationPhase.BACKEND, fixtures)
                campaign.run_segment(
                    client=provider_client,
                    runtime_root=runtime,
                    turn_count=1,
                )
                assert_manual_stop(campaign)
                self.assertEqual(provider_client.action_posts, [])

        review_cases: dict[
            str,
            tuple[int, Callable[[Path], _FakeQualificationClient], str],
        ] = {
            "ordinary_regenerate": (
                1,
                lambda runtime: _FakeQualificationClient(runtime, reject_first=True),
                "regenerates",
            ),
            "adult_regenerate": (
                6,
                lambda runtime: _FakeQualificationClient(
                    runtime,
                    reject_fixture_id="backend-adult-01",
                ),
                "regenerates",
            ),
            "review_recording_repair": (
                1,
                lambda runtime: _FakeQualificationClient(
                    runtime,
                    recording_repair_fixture_id="backend-ordinary-01",
                ),
                "review_actions",
            ),
        }
        for label, (turn_count, review_factory, post_attribute) in review_cases.items():
            with self.subTest(action_kind=label), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                runtime = root / "runtime"
                review_client = review_factory(runtime)
                campaign = _ProductionFullModelQualificationRunner(
                    manifest=_manifest(),
                    runtime_root=runtime,
                    evidence_root=root / "evidence",
                ).start_phase(QualificationPhase.BACKEND, fixtures)
                campaign.run_segment(
                    client=review_client,
                    runtime_root=runtime,
                    turn_count=turn_count,
                )
                assert_manual_stop(campaign)
                self.assertFalse(getattr(review_client, post_attribute))

    def test_automatic_accept_remains_backend_owned_and_ungated(self) -> None:
        fixtures = load_qualification_fixtures(FIXTURES)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime = root / "runtime"
            client = _FakeQualificationClient(runtime)
            campaign = _ProductionFullModelQualificationRunner(
                manifest=_manifest(),
                runtime_root=runtime,
                evidence_root=root / "evidence",
            ).start_phase(QualificationPhase.BACKEND, fixtures)
            campaign.run_segment(client=client, runtime_root=runtime, turn_count=1)
            self.assertIsNone(campaign.failure)
            self.assertEqual(campaign.results[0]["status"], "passed")
            self.assertEqual(campaign.results[0]["automatic_manual_actions"], 0)
            self.assertEqual(client.regenerates, 0)
            self.assertEqual(client.review_actions, [])

    def test_stale_authority_never_posts_or_counts_a_dispatched_action(self) -> None:
        fixtures = load_qualification_fixtures(FIXTURES)
        cases: dict[
            str,
            tuple[int, Callable[[Path], _FakeQualificationClient], str],
        ] = {
            "provider_retry": (
                1,
                lambda runtime: _StaleProviderStageAuthorityClient(
                    runtime,
                    failure_fixture_id="backend-ordinary-01",
                ),
                "action_posts",
            ),
            "ordinary_regenerate": (
                1,
                lambda runtime: _StaleReviewAuthorityClient(runtime, reject_first=True),
                "regenerates",
            ),
            "adult_regenerate": (
                6,
                lambda runtime: _StaleReviewAuthorityClient(
                    runtime,
                    reject_fixture_id="backend-adult-01",
                ),
                "regenerates",
            ),
            "review_recording_repair": (
                1,
                lambda runtime: _StaleReviewAuthorityClient(
                    runtime,
                    recording_repair_fixture_id="backend-ordinary-01",
                ),
                "review_actions",
            ),
        }
        for label, (turn_count, stale_factory, post_attribute) in cases.items():
            with self.subTest(action_kind=label), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                runtime = root / "runtime"
                client = stale_factory(runtime)
                campaign = FullModelQualificationRunner(
                    manifest=_manifest(),
                    runtime_root=runtime,
                    evidence_root=root / "evidence",
                ).start_phase(QualificationPhase.BACKEND, fixtures)
                campaign.run_segment(
                    client=client,
                    runtime_root=runtime,
                    turn_count=turn_count,
                )
                self.assertIsInstance(campaign.failure, StateConflictError)
                self.assertFalse(getattr(client, post_attribute))
                self.assertEqual(campaign.externally_authorized_manual_actions, 0)
                self.assertEqual(
                    campaign.results[-1]["externally_authorized_manual_actions"],
                    0,
                )

    def test_lost_action_responses_never_repost_and_reconcile_where_authoritative(self) -> None:
        fixtures = load_qualification_fixtures(FIXTURES)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime = root / "ordinary-runtime"
            ordinary = _LostOrdinaryRegenerateResponseClient(runtime, reject_first=True)
            ordinary_campaign = FullModelQualificationRunner(
                manifest=_manifest(),
                runtime_root=runtime,
                evidence_root=root / "ordinary-evidence",
            ).start_phase(QualificationPhase.BACKEND, fixtures)
            ordinary_campaign.run_segment(
                client=ordinary,
                runtime_root=runtime,
                turn_count=1,
            )
            self.assertIsNone(ordinary_campaign.failure)
            self.assertEqual(ordinary.regenerates, 1)
            self.assertEqual(ordinary_campaign.results[0]["explicit_regenerate_actions"], 1)
            self.assertEqual(
                ordinary.terminal_decision_reads.count(
                    "review-" + text_sha256("backend-ordinary-01:1:initial")[:28]
                ),
                2,
            )

            delayed_runtime = root / "ordinary-delayed-runtime"
            delayed = _DelayedLostOrdinaryRegenerateResponseClient(
                delayed_runtime,
                reject_first=True,
            )
            delayed_campaign = FullModelQualificationRunner(
                manifest=_manifest(),
                runtime_root=delayed_runtime,
                evidence_root=root / "ordinary-delayed-evidence",
            ).start_phase(QualificationPhase.BACKEND, fixtures)
            with patch(
                "cera.pi_scene.qualification.PROVIDER_STAGE_RETRY_STATUS_POLL_SECONDS",
                0,
            ):
                delayed_campaign.run_segment(
                    client=delayed,
                    runtime_root=delayed_runtime,
                    turn_count=1,
                )
            self.assertIsNone(delayed_campaign.failure)
            self.assertEqual(delayed.regenerates, 1)
            self.assertEqual(delayed.delayed_terminal_reads, 1)

            adult_runtime = root / "adult-runtime"
            adult = _LostAdultRegenerateResponseClient(
                adult_runtime,
                reject_fixture_id="backend-adult-01",
            )
            adult_campaign = FullModelQualificationRunner(
                manifest=_manifest(),
                runtime_root=adult_runtime,
                evidence_root=root / "adult-evidence",
            ).start_phase(QualificationPhase.BACKEND, fixtures)
            adult_campaign.run_segment(
                client=adult,
                runtime_root=adult_runtime,
                turn_count=6,
            )
            self.assertIsInstance(adult_campaign.failure, StateConflictError)
            self.assertEqual(adult.regenerates, 1)
            self.assertEqual(adult_campaign.results[-1]["explicit_regenerate_actions"], 1)
            self.assertEqual(
                adult_campaign.results[-1]["externally_authorized_manual_actions"],
                1,
            )

            repair_runtime = root / "repair-runtime"
            repair = _LostReviewRecordingRepairResponseClient(
                repair_runtime,
                recording_repair_fixture_id="backend-ordinary-01",
            )
            repair_campaign = FullModelQualificationRunner(
                manifest=_manifest(),
                runtime_root=repair_runtime,
                evidence_root=root / "repair-evidence",
            ).start_phase(QualificationPhase.BACKEND, fixtures)
            repair_campaign.run_segment(
                client=repair,
                runtime_root=repair_runtime,
                turn_count=1,
            )
            self.assertIsNone(repair_campaign.failure)
            self.assertEqual(len(repair.review_actions), 1)
            self.assertEqual(repair_campaign.results[0]["review_recording_repair_actions"], 1)
            self.assertEqual(
                repair_campaign.results[0]["externally_authorized_manual_actions"],
                1,
            )

            retry_runtime = root / "retry-runtime"
            retry = _FakeProviderStageRetryClient(
                retry_runtime,
                failure_fixture_id="backend-ordinary-01",
                ambiguous_post=True,
            )
            retry_campaign = FullModelQualificationRunner(
                manifest=_manifest(),
                runtime_root=retry_runtime,
                evidence_root=root / "retry-evidence",
            ).start_phase(QualificationPhase.BACKEND, fixtures)
            retry_campaign.run_segment(
                client=retry,
                runtime_root=retry_runtime,
                turn_count=1,
            )
            self.assertIsNone(retry_campaign.failure)
            self.assertEqual(len(retry.action_posts), 1)
            self.assertEqual(
                retry_campaign.results[0]["externally_authorized_manual_actions"],
                1,
            )

    def test_hash_only_manual_receipts_and_approval_cli_are_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fake = _FakeProviderStageRetryClient(
                root / "runtime",
                failure_fixture_id="backend-ordinary-01",
            )
            envelope = fake._envelope("planner", attempts=1, retries=0, state="eligible")
            status = envelope["status"]
            action = envelope["actions"][0]
            request = QualificationManualActionRequestV1(
                qualification_id="qualification-raw-secret",
                phase=QualificationPhase.BACKEND,
                turn_index=1,
                fixture_id="fixture-raw-secret",
                action_family="provider_stage_control",
                action_kind="provider_retry",
                action_id=action["action_id"],
                exact_action=action,
                authority_sha256=canonical_sha256(envelope),
                chain_id=status["chain_id"],
                stage=status["stage"],
                provider=status["provider"],
                model_family=status["model_family"],
                retry_action_ordinal=1,
            )
            authorizer = entrypoint.HashOnlyFileManualActionAuthorizer(
                output_root=root,
                timeout_seconds=0,
            )
            with self.assertRaises(ManualActionRequiredError):
                authorizer.authorize(request)
            pending_path, approval_path, consumed_path = entrypoint._manual_receipt_paths(
                root,
                request.checkpoint_sha256,
            )
            pending = json.loads(pending_path.read_text(encoding="utf-8"))
            self.assertEqual(pending["stage"], "planner")
            self.assertEqual(pending["provider"], "codex")
            self.assertEqual(pending["model_family"], "sol")
            self.assertEqual(pending["retry_action_ordinal"], 1)

            with patch("builtins.print") as printed:
                self.assertEqual(
                    entrypoint.main(
                        [
                            "approve-manual-action",
                            "--output-root",
                            str(root),
                            "--checkpoint-sha256",
                            request.checkpoint_sha256,
                        ]
                    ),
                    0,
                )
            approval_result = json.loads(printed.call_args.args[0])
            self.assertEqual(approval_result["checkpoint_sha256"], request.checkpoint_sha256)
            exact_approval = json.loads(approval_path.read_text(encoding="utf-8"))
            changed_approval = deepcopy(exact_approval)
            changed_approval["exact_action_sha256"] = "f" * 64
            approval_path.write_bytes(canonical_bytes(changed_approval) + b"\n")
            with self.assertRaisesRegex(StateConflictError, "approval receipt changed"):
                authorizer.authorize(request)
            approval_path.write_bytes(canonical_bytes(exact_approval) + b"\n")
            authorization = authorizer.authorize(request)
            assert authorization is not None
            authorizer.mark_consumed(request, authorization)
            self.assertTrue(approval_path.is_file())
            self.assertTrue(consumed_path.is_file())

            forbidden_keys = {
                "qualification_id",
                "fixture_id",
                "action_id",
                "chain_id",
                "review_id",
                "exact_action",
                "token",
                "prose",
                "provider_output",
                "creator_feedback",
                "path",
            }
            raw_values = {
                request.qualification_id,
                request.fixture_id,
                request.action_id,
                str(request.chain_id),
            }
            for receipt_path in (pending_path, approval_path, consumed_path):
                receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
                self.assertTrue(forbidden_keys.isdisjoint(receipt))
                encoded = json.dumps(receipt, sort_keys=True)
                self.assertTrue(all(value not in encoded for value in raw_values))
            with self.assertRaisesRegex(StateConflictError, "already approved or consumed"):
                entrypoint.approve_manual_action(
                    output_root=root,
                    checkpoint_sha256=request.checkpoint_sha256,
                )
            with self.assertRaisesRegex(StateConflictError, "already consumed"):
                authorizer.mark_consumed(request, authorization)

    def test_simulated_authorization_requires_live_process_guard_at_both_seams(self) -> None:
        review_id = "review-0123456789abcdef0123456789ab"
        candidate_sha256 = "c" * 64
        request = QualificationManualActionRequestV1(
            qualification_id="qualification-guard-test",
            phase=QualificationPhase.BACKEND,
            turn_index=1,
            fixture_id="backend-ordinary-01",
            action_family="semantic_regenerate",
            action_kind="regenerate",
            action_id="review-action-" + text_sha256(f"{review_id}:{candidate_sha256}:regenerate"),
            exact_action={"action": "regenerate"},
            authority_sha256="a" * 64,
            review_id=review_id,
            candidate_sha256=candidate_sha256,
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            simulated = _ProviderFreeSimulatedManualActionAuthorizer()
            runner = _ProductionFullModelQualificationRunner(
                manifest=_manifest(),
                runtime_root=root / "runtime",
                evidence_root=root / "evidence",
                manual_action_authorizer=simulated,
            )
            with patch.dict(os.environ, {}, clear=True):
                with self.assertRaisesRegex(StateConflictError, "requires provider dispatch"):
                    runner.authorize_manual_action(request)

            authorization = runner.authorize_manual_action(request)
            with patch.dict(os.environ, {}, clear=True):
                with self.assertRaisesRegex(StateConflictError, "lost the provider-dispatch"):
                    runner.consume_manual_action(request, authorization)
            self.assertEqual(simulated.consumed_checkpoints, [])

    def test_manual_action_wait_timeout_must_be_finite_and_nonnegative(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for invalid in (-1.0, float("nan"), float("inf"), True):
                with (
                    self.subTest(timeout=invalid),
                    self.assertRaisesRegex(
                        ContractValidationError,
                        "not bounded",
                    ),
                ):
                    entrypoint.HashOnlyFileManualActionAuthorizer(
                        output_root=root,
                        timeout_seconds=invalid,
                    )

    def test_provider_free_entrypoint_reports_exact_campaign_counts(self) -> None:
        result = entrypoint.provider_free_check(fixture_path=FIXTURES)
        self.assertEqual(result["provider_calls"], 0)
        self.assertEqual(result["backend"], 20)
        self.assertEqual(result["sillytavern"], 10)
        self.assertEqual(result["semantic_validator_model"], "gpt-5.6-luna")
        self.assertEqual(result["semantic_validator_reasoning_effort"], "xhigh")
        self.assertEqual(result["semantic_validator_maximum_output_tokens"], 128_000)
        self.assertEqual(
            result["semantic_validator_route_sha256"],
            entrypoint.full_model_qualification_luna_validator_route().route_sha256,
        )
        self.assertEqual(
            result["fixture_schema_version"],
            "cera.pi_scene.full_model_qualification_fixtures.v25",
        )
        self.assertRegex(str(result["novelty_set_sha256"]), r"^[a-f0-9]{64}$")
        self.assertGreater(len(result["stress_coverage"]), 10)
        self.assertTrue(result["manual_provider_stage_retry_authorized"])
        self.assertEqual(result["maximum_manual_retry_actions_per_stage_occurrence"], 2)
        self.assertEqual(result["maximum_provider_attempts_per_stage_occurrence"], 3)
        self.assertEqual(
            result["maximum_manual_resume_prepared_actions_per_stage_occurrence"],
            1,
        )
        self.assertEqual(result["maximum_manual_recording_repair_actions_per_request"], 1)
        self.assertFalse(result["recursive_recording_repair"])
        self.assertEqual(result["automatic_provider_stage_retry_actions"], 0)
        self.assertEqual(result["automatic_provider_stage_control_actions"], 0)


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
