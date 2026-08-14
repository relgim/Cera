from __future__ import annotations

import json
import unittest
from collections.abc import Callable, Mapping
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from types import SimpleNamespace
from typing import Any, cast
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from cera.errors import ContractValidationError, StateConflictError
from cera.generated.provider_stage_retry_contracts_v1 import (
    ProviderStageRetryActionV1,
    ProviderStageRetryStatusEnvelopeV1,
    validate_provider_stage_retry_status_envelope_v1,
)
from cera.pi_scene.http import (
    PiSceneHttpAdapter,
    PiSceneServerConfigV1,
    build_pi_scene_server,
)
from cera.pi_scene.provider_stage_retry import ProviderStage, ProviderStageRetryPhase
from cera.pi_scene.provider_stage_retry_blob import TrustedLocalProtectedStageBlobStore
from cera.pi_scene.provider_stage_retry_http import (
    ProtectedProviderStageRetryHttpCursorStoreV1,
    ProviderStageRetryHttpContinuationPort,
    ProviderStageRetryHttpControllerV1,
    ProviderStageRetryHttpRegistrationV1,
    ProviderStageRetryHttpRequestIdentityV1,
    provider_stage_retry_action_identity,
    provider_stage_retry_chain_id,
)
from cera.pi_scene.provider_stage_retry_runtime import (
    ProviderStageRetryRuntimeServiceV1,
    ProviderStageRuntimeAdapterV1,
    backend_action_from_envelope,
)
from cera.pi_scene.request_journal import PiSceneRequestJournal, TransportRetryNotFoundError
from cera.pi_scene.transport_retry_http import TransportRetryHttpController
from cera.serialization import canonical_sha256, text_sha256
from cera.storage.sqlite_store import SQLiteAuthorityStore

# Reuse the repository's deterministic provider-free owner doubles.  These
# invoke only in-memory scripted outcomes and do not contact a provider.
from tests.test_provider_stage_retry_runtime import (
    _ambiguous,
    _Binder,
    _InvocationLedger,
    _non_retryable_failure,
    _OwnerFactory,
    _packet,
    _Reconciler,
    _retryable_failure,
    _scope,
    _success,
)


def _completion(content: str = "continued") -> dict[str, Any]:
    return {
        "id": "chatcmpl-provider-stage-http-test",
        "object": "chat.completion",
        "created": 1,
        "model": "cera-alpha",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        "cera": {
            "profile_id": "cera.pi_scene.lean.v1",
            "route_mode": "ordinary",
        },
    }


def _review(review_id: str) -> dict[str, Any]:
    return {
        "schema_version": "cera.pi_scene.review.v1",
        "review_id": review_id,
        "state": "regenerated",
        "provisional": False,
        "route": "ordinary",
        "story_text": "Visible reviewed story.",
        "candidate_id": "candidate:test",
        "candidate_sha256": "1" * 64,
        "primary_authority_kind": "codex_cognition_plan",
        "primary_authority_sha256": "2" * 64,
        "warnings": [],
        "warnings_block_accept": False,
        "recording_status": None,
        "story_state_committed": False,
        "canon_status": "unaccepted",
        "semantic_validation": None,
        "request_controls": None,
        "creator_guidance": None,
        "accept_enabled": False,
        "provisional_accept_enabled": False,
        "decline_enabled": False,
        "regenerate_enabled": False,
        "replan_enabled": False,
        "repair_recording_enabled": False,
        "provider_operations": {"planner": 1, "writer": 1, "recorder": 0},
    }


def _decision(review_id: str, *, action: str = "decline") -> dict[str, Any]:
    review = _review(review_id)
    review["state"] = "declined"
    return {
        "schema_version": "cera.pi_scene.review_decision.v1",
        "status": "review_transitioned",
        "creator_action": action,
        "story_state_committed": False,
        "retry_mode": "not_applicable",
        "review": review,
        "successor": None,
        "operational_warnings": [],
    }


def _backend_action(envelope: object) -> ProviderStageRetryActionV1:
    return backend_action_from_envelope(validate_provider_stage_retry_status_envelope_v1(envelope))


class _Continuation:
    def __init__(self) -> None:
        self.latest: dict[str, str] = {}
        self.terminal: dict[str, dict[str, Any]] = {}
        self.resume_calls = 0
        self.fail_resume_once = False
        self.terminalized: dict[str, str] = {}
        self.result: dict[str, Any] = _completion()
        self.epochs: dict[str, dict[str, Any]] = {}
        self.recording_repair_allowed: dict[str, bool] = {}
        self.recording_repair_calls = 0
        self.recording_repair_executor: Callable[[object], object] | None = None
        self.validation_lanes: dict[str, dict[str, str]] = {}
        self.validation_reviews: dict[str, dict[str, Any]] = {}

    def latest_chain_for_chain(self, chain_id: str) -> str:
        return self.latest.get(chain_id, chain_id)

    def continuation_epoch_for_chain(self, chain_id: str) -> object | None:
        return self.epochs.get(chain_id)

    def recording_repair_action_allowed(self, chain_id: str) -> bool:
        return self.recording_repair_allowed.get(chain_id, True)

    def validation_lane_binding_for_chain(self, chain_id: str) -> dict[str, str] | None:
        return self.validation_lanes.get(chain_id)

    def review_payload_for_validation_chain(self, chain_id: str) -> dict[str, Any]:
        return dict(self.validation_reviews[chain_id])

    def execute_recording_repair(self, action: object) -> object:
        self.recording_repair_calls += 1
        if self.recording_repair_executor is None:
            raise AssertionError("test Recorder repair executor is unavailable")
        return self.recording_repair_executor(action)

    def load_terminal_completion(self, chain_id: str) -> dict[str, Any] | None:
        return self.terminal.get(chain_id)

    def resume_succeeded_chain(self, chain_id: str) -> dict[str, Any]:
        self.resume_calls += 1
        if self.fail_resume_once:
            self.fail_resume_once = False
            raise RuntimeError("simulated crash after stage success")
        return dict(self.result)

    def project_terminal_completion(
        self,
        *,
        chain_id: str,
        result: object,
    ) -> dict[str, Any]:
        del chain_id
        if not isinstance(result, dict):
            raise AssertionError("test continuation result changed")
        return dict(result)

    def bind_terminal_completion(
        self,
        *,
        chain_id: str,
        completion: Mapping[str, Any],
    ) -> dict[str, Any]:
        prior = self.terminal.setdefault(chain_id, dict(completion))
        if prior != completion:
            raise AssertionError("test terminal completion changed")
        return dict(prior)

    def terminalize_failure(
        self,
        *,
        chain_id: str,
        envelope: Mapping[str, Any],
    ) -> None:
        state = envelope["status"]["state"]
        prior = self.terminalized.setdefault(chain_id, state)
        if prior != state:
            raise AssertionError("test terminal failure changed")


class ProviderStageRetryHttpTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.invocations = _InvocationLedger()
        self.writer_factory = _OwnerFactory(
            stage=ProviderStage.WRITER,
            outcomes={
                1: _retryable_failure("initial"),
                2: _success("retry-1"),
                3: _success("retry-2"),
            },
            invocation_ledger=self.invocations,
        )
        self.writer_reconciler = _Reconciler()
        self.factories: dict[ProviderStage, _OwnerFactory] = {}
        self.stage_invocations: dict[ProviderStage, _InvocationLedger] = {
            ProviderStage.WRITER: self.invocations
        }
        registrations: list[ProviderStageRuntimeAdapterV1] = []
        for stage in ProviderStage:
            if stage is ProviderStage.WRITER:
                factory = self.writer_factory
                reconciler = self.writer_reconciler
            else:
                stage_invocations = _InvocationLedger()
                self.stage_invocations[stage] = stage_invocations
                factory = _OwnerFactory(
                    stage=stage,
                    outcomes={1: _success(stage.value)},
                    invocation_ledger=stage_invocations,
                )
                reconciler = _Reconciler()
            self.factories[stage] = factory
            registrations.append(
                ProviderStageRuntimeAdapterV1(
                    stage=stage,
                    owner_factory=factory,
                    ambiguity_reconciler=reconciler,
                    downstream_binder=_Binder(),
                )
            )
        self.service = ProviderStageRetryRuntimeServiceV1(
            authority_store=SQLiteAuthorityStore(self.root / "authority.sqlite3"),
            protected_blob_store=TrustedLocalProtectedStageBlobStore(self.root / "protected"),
            registrations=tuple(registrations),
        )
        self.packet = _packet(ProviderStage.WRITER)
        self.scope = _scope(self.packet, occurrence="http-test")
        self.continuation = _Continuation()
        self.cursor_root = self.root / "http-cursor"
        self.controller = self._controller()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _controller(self) -> ProviderStageRetryHttpControllerV1:
        return ProviderStageRetryHttpControllerV1(
            runtime=self.service,
            cursor_store=ProtectedProviderStageRetryHttpCursorStoreV1(self.cursor_root),
            registrations=tuple(
                ProviderStageRetryHttpRegistrationV1(
                    stage=stage,
                    continuation=self.continuation,
                )
                for stage in ProviderStage
            ),
        )

    def _begin_failed_chain(self) -> tuple[str, ProviderStageRetryStatusEnvelopeV1]:
        chain = self.service.start_initial(scope=self.scope, packet=self.packet)
        envelope = self.service.canonical_status(chain_id=chain.chain_id)
        self.controller.begin_request(
            request_id=self.scope.request_id,
            world_id=self.scope.world_id,
            branch_id=self.scope.branch_id,
        )
        self.assertEqual(self.controller.capture_pending(envelope), envelope)
        return chain.chain_id, envelope

    def test_exact_retry_continues_once_and_reloads_durable_completion(self) -> None:
        chain_id, envelope = self._begin_failed_chain()
        action = _backend_action(envelope)

        result = self.controller.post(
            chain_id=chain_id,
            action_id=action["action_id"],
            body=action,
        )

        self.assertEqual(result["object"], "chat.completion")
        self.assertEqual(result["cera"]["request_id"], self.scope.request_id)
        self.assertEqual(
            result["cera"]["provider_stage_request_sha256"],
            self.scope.request_sha256,
        )
        self.assertEqual(self.invocations.calls, 2)
        self.assertEqual(self.continuation.resume_calls, 1)
        self.assertEqual(self.controller.get(chain_id), result)
        self.assertEqual(
            self.controller.post(
                chain_id=chain_id,
                action_id=action["action_id"],
                body=action,
            ),
            result,
        )
        self.assertEqual(self.invocations.calls, 2)
        self.assertEqual(self.continuation.resume_calls, 1)

    def test_consumed_action_is_manual_continue_after_post_crash(self) -> None:
        chain_id, envelope = self._begin_failed_chain()
        action = _backend_action(envelope)
        self.continuation.fail_resume_once = True

        with self.assertRaisesRegex(RuntimeError, "after stage success"):
            self.controller.post(
                chain_id=chain_id,
                action_id=action["action_id"],
                body=action,
            )

        status = self.controller.get(chain_id)
        self.assertEqual(status["status"]["state"], "succeeded")
        self.assertEqual(self.invocations.calls, 2)
        self.assertEqual(self.continuation.resume_calls, 1)

        completion = self.controller.post(
            chain_id=chain_id,
            action_id=action["action_id"],
            body=action,
        )
        self.assertEqual(completion["object"], "chat.completion")
        self.assertEqual(self.invocations.calls, 2)
        self.assertEqual(self.continuation.resume_calls, 2)

    def test_prepared_attempt_requires_backend_issued_manual_resume_after_restart(self) -> None:
        chain_id, envelope = self._begin_failed_chain()
        action = _backend_action(envelope)
        ProtectedProviderStageRetryHttpCursorStoreV1(self.cursor_root).remember_action(
            request_sha256=self.scope.request_sha256,
            action=action,
        )
        chain = self.service.read_chain(chain_id)
        owner = self.writer_factory.create_retry_owner(
            chain=chain,
            exact_input=self.service.store.load_input(chain_id),
        )
        prepared = self.service.store.accept_retry(
            chain_id,
            retry_action_sha256=canonical_sha256(action),
            session_scope_sha256=owner.session_scope_sha256,
            ledger_prefix_before_sha256=owner.ledger_prefix_before_sha256,
        )
        self.assertIs(prepared.phase, ProviderStageRetryPhase.ATTEMPT_PREPARED)
        self.assertEqual(self.invocations.calls, 1)

        restarted = self._controller()
        status = restarted.get(chain_id)
        self.assertEqual(status["status"]["state"], "in_progress")
        resume_action = _backend_action(status)
        self.assertEqual(resume_action["action_kind"], "resume_prepared")
        self.assertIs(resume_action["consumes_retry_action"], False)
        self.assertEqual(self.invocations.calls, 1)

        completion = restarted.post(
            chain_id=chain_id,
            action_id=resume_action["action_id"],
            body=resume_action,
        )
        self.assertEqual(completion["object"], "chat.completion")
        self.assertEqual(self.invocations.calls, 2)
        self.assertEqual(self.continuation.resume_calls, 1)
        self.assertEqual(
            self._controller().post(
                chain_id=chain_id,
                action_id=resume_action["action_id"],
                body=resume_action,
            ),
            completion,
        )
        self.assertEqual(self.invocations.calls, 2)
        self.assertEqual(self.continuation.resume_calls, 1)

    def test_recorder_repair_binds_one_successor_and_replays_terminal_result(self) -> None:
        recorder_factory = self.factories[ProviderStage.RECORDER]
        recorder_factory.outcomes = {
            1: _retryable_failure("recorder-1"),
            2: _retryable_failure("recorder-2"),
            3: _retryable_failure("recorder-3"),
        }
        packet = _packet(ProviderStage.RECORDER)
        scope = _scope(packet, occurrence="http-recorder-repair")
        parent = self.service.start_initial(scope=scope, packet=packet)
        envelope = self.service.canonical_status(chain_id=parent.chain_id)
        self.controller.begin_request(
            request_id=scope.request_id,
            world_id=scope.world_id,
            branch_id=scope.branch_id,
        )
        self.controller.capture_pending(envelope)
        first_action = _backend_action(envelope)
        second = self.controller.post(
            chain_id=parent.chain_id,
            action_id=first_action["action_id"],
            body=first_action,
        )
        second_action = _backend_action(second)
        exhausted = self.controller.post(
            chain_id=parent.chain_id,
            action_id=second_action["action_id"],
            body=second_action,
        )
        repair_action = _backend_action(exhausted)
        self.assertEqual(repair_action["action_kind"], "repair_recording")
        self.assertEqual(self.service.read_chain(parent.chain_id).retries_consumed, 2)

        def execute_repair(action: object) -> object:
            self.assertEqual(action, repair_action)
            recorder_factory.outcomes[1] = _success("recorder-repair")
            successor_scope = type(scope).create(
                world_id=scope.world_id,
                branch_id=scope.branch_id,
                request_id=scope.request_id,
                generation_id=scope.generation_id,
                stage=ProviderStage.RECORDER,
                stage_ordinal=2,
                accepted_state_sha256=scope.accepted_state_sha256,
                exact_input=packet.exact_bytes,
                authority_binding={"repair_parent": parent.chain_id},
            )
            successor = self.service.start_initial(scope=successor_scope, packet=packet)
            self.service.finalize_result_once(successor.chain_id)
            successor = self.service.read_chain(successor.chain_id)
            self.continuation.latest[parent.chain_id] = successor.chain_id
            self.continuation.recording_repair_allowed[successor.chain_id] = False
            return SimpleNamespace(
                chain_id=successor.chain_id,
                stage=ProviderStage.RECORDER,
                result_ready=True,
                envelope=self.service.canonical_status(
                    chain_id=successor.chain_id,
                    recording_repair_action_allowed=False,
                ),
            )

        self.continuation.recording_repair_executor = execute_repair
        completion = self.controller.post(
            chain_id=parent.chain_id,
            action_id=repair_action["action_id"],
            body=repair_action,
        )

        self.assertEqual(completion["object"], "chat.completion")
        self.assertEqual(self.continuation.recording_repair_calls, 1)
        self.assertEqual(self.stage_invocations[ProviderStage.RECORDER].calls, 4)
        successor_id = self.continuation.latest[parent.chain_id]
        self.assertEqual(self.service.read_chain(successor_id).retries_consumed, 0)
        self.assertEqual(self._controller().get(parent.chain_id), completion)
        self.assertEqual(
            self._controller().post(
                chain_id=parent.chain_id,
                action_id=repair_action["action_id"],
                body=repair_action,
            ),
            completion,
        )
        self.assertEqual(self.continuation.recording_repair_calls, 1)
        self.assertEqual(self.stage_invocations[ProviderStage.RECORDER].calls, 4)

    def test_provisional_terminal_completion_keeps_request_barrier(self) -> None:
        self.continuation.result = _completion("provisional story")
        self.continuation.result["cera"].update(
            {
                "provisional": True,
                "provisional_review_id": "review-" + "a" * 28,
                "candidate_id": "candidate:provisional",
            }
        )
        chain_id, envelope = self._begin_failed_chain()
        action = _backend_action(envelope)
        result = self.controller.post(
            chain_id=chain_id,
            action_id=action["action_id"],
            body=action,
        )
        self.assertIs(result["cera"]["provisional"], True)
        with self.assertRaisesRegex(StateConflictError, "unresolved protected request"):
            self.controller.begin_request(
                request_id="request-" + "9" * 64,
                world_id=self.scope.world_id,
                branch_id=self.scope.branch_id,
            )

    def test_provisional_terminal_keeps_both_validation_lanes_controllable(self) -> None:
        review_id = "review-" + "a" * 28
        provisional = _completion("provisional story")
        provisional["cera"].update(
            {
                "provisional": True,
                "provisional_review_id": review_id,
                "candidate_id": "candidate:provisional",
            }
        )
        self.continuation.result = provisional
        source_chain_id, source_envelope = self._begin_failed_chain()
        source_action = _backend_action(source_envelope)
        self.controller.post(
            chain_id=source_chain_id,
            action_id=source_action["action_id"],
            body=source_action,
        )

        prepared_chains: dict[str, str] = {}
        for lane, stage in (
            ("luna", ProviderStage.SEMANTIC_VALIDATOR),
            ("reader", ProviderStage.READER),
        ):
            packet = _packet(stage)
            scope = type(self.scope).create(
                world_id=self.scope.world_id,
                branch_id=self.scope.branch_id,
                request_id=self.scope.request_id,
                generation_id=self.scope.generation_id,
                stage=stage,
                stage_ordinal=1,
                accepted_state_sha256=self.scope.accepted_state_sha256,
                exact_input=packet.exact_bytes,
                authority_binding={"head": "accepted-head"},
            )
            prepared = self.service.prepare_initial(scope=scope, packet=packet)
            self.assertIs(prepared.chain.phase, ProviderStageRetryPhase.ATTEMPT_PREPARED)
            prepared_chains[lane] = prepared.chain.chain_id

        binding = {
            "schema_version": "cera.pi_scene.review_validation_lanes.v1",
            "review_id": review_id,
            "luna_chain_id": prepared_chains["luna"],
            "reader_chain_id": prepared_chains["reader"],
        }
        current_review = _review(review_id)
        for lane, chain_id in prepared_chains.items():
            self.continuation.validation_lanes[chain_id] = {
                **binding,
                "lane": lane,
                "chain_id": chain_id,
            }
            self.continuation.validation_reviews[chain_id] = current_review

        self.continuation.result = current_review
        for lane in ("luna", "reader"):
            chain_id = prepared_chains[lane]
            status = self.controller.get(chain_id)
            action = _backend_action(status)
            self.assertEqual(action["action_kind"], "resume_prepared")
            completion = self.controller.post(
                chain_id=chain_id,
                action_id=action["action_id"],
                body=action,
            )
            self.assertEqual(completion["review_id"], review_id)

    def test_protected_review_action_opens_new_terminal_epoch(self) -> None:
        review_id = "review-" + "a" * 28
        provisional = _completion("provisional story")
        provisional["cera"].update(
            {
                "provisional": True,
                "provisional_review_id": review_id,
            }
        )
        self.continuation.result = provisional
        first_chain_id, first_envelope = self._begin_failed_chain()
        first_action = _backend_action(first_envelope)
        self.controller.post(
            chain_id=first_chain_id,
            action_id=first_action["action_id"],
            body=first_action,
        )

        second_scope = type(self.scope).create(
            world_id=self.scope.world_id,
            branch_id=self.scope.branch_id,
            request_id=self.scope.request_id,
            generation_id=self.scope.generation_id,
            stage=ProviderStage.WRITER,
            stage_ordinal=2,
            accepted_state_sha256=self.scope.accepted_state_sha256,
            exact_input=self.packet.exact_bytes,
            authority_binding={"test": "review-action"},
        )
        second_chain = self.service.start_initial(
            scope=second_scope,
            packet=self.packet,
        )
        second_envelope = self.service.canonical_status(chain_id=second_chain.chain_id)
        self.continuation.epochs[second_chain.chain_id] = {
            "action_id": "review-action-" + "b" * 64,
            "review_id": review_id,
            "request_id": self.scope.request_id,
            "world_id": self.scope.world_id,
            "branch_id": self.scope.branch_id,
            "context_sha256": "c" * 64,
            "normalized_action_sha256": "d" * 64,
            "stage_occurrence_counts_sha256": "e" * 64,
        }

        self.assertEqual(self.controller.capture_pending(second_envelope), second_envelope)
        self.continuation.result = _decision(review_id)
        second_action = _backend_action(second_envelope)
        terminal = self.controller.post(
            chain_id=second_chain.chain_id,
            action_id=second_action["action_id"],
            body=second_action,
        )

        self.assertEqual(terminal["schema_version"], "cera.pi_scene.review_decision.v1")
        self.assertEqual(terminal["creator_action"], "decline")
        self.assertEqual(self.controller.get(first_chain_id), terminal)
        self.assertIsNone(
            self.controller.active_request_for_branch(
                world_id=self.scope.world_id,
                branch_id=self.scope.branch_id,
            )
        )

    def test_review_action_epoch_ignores_authenticated_chain_specific_fields(self) -> None:
        identity = ProviderStageRetryHttpRequestIdentityV1(
            request_id=self.scope.request_id,
            request_sha256=self.scope.request_sha256,
            world_id=self.scope.world_id,
            branch_id=self.scope.branch_id,
        )
        first_chain_id = "stage-retry-" + text_sha256("action-scene")
        second_chain_id = "stage-retry-" + text_sha256("action-filter")
        stable = {
            "action_id": "adult-review-action-" + "a" * 64,
            "review_id": "review-" + "b" * 28,
            "request_id": identity.request_id,
            "world_id": identity.world_id,
            "branch_id": identity.branch_id,
            "normalized_action_sha256": "c" * 64,
        }
        self.continuation.epochs[first_chain_id] = {
            **stable,
            "chain_id": first_chain_id,
            "stage": "adult_scene",
            "request_sha256": "d" * 64,
            "chain_binding_sha256": "e" * 64,
        }
        self.continuation.epochs[second_chain_id] = {
            **stable,
            "chain_id": second_chain_id,
            "stage": "adult_filter",
            "request_sha256": "f" * 64,
            "chain_binding_sha256": "1" * 64,
        }

        first_epoch = self.controller._continuation_epoch_sha256(
            chain_id=first_chain_id,
            identity=identity,
            port=self.continuation,
        )
        second_epoch = self.controller._continuation_epoch_sha256(
            chain_id=second_chain_id,
            identity=identity,
            port=self.continuation,
        )

        self.assertEqual(first_epoch, second_epoch)
        self.continuation.epochs[second_chain_id]["nested"] = {"debug_log_path": "D:\\private.txt"}
        with self.assertRaisesRegex(StateConflictError, "protected action"):
            self.controller._continuation_epoch_sha256(
                chain_id=second_chain_id,
                identity=identity,
                port=self.continuation,
            )

    def test_review_action_continuation_returns_closed_decision_dto(self) -> None:
        self.continuation.result = {
            "schema_version": "cera.pi_scene.review_decision.v1",
            "status": "review_transitioned",
            "creator_action": "regenerate",
            "story_state_committed": False,
            "retry_mode": "not_applicable",
            "review": _review("review-" + "a" * 28),
            "successor": None,
            "operational_warnings": [],
        }
        chain_id, envelope = self._begin_failed_chain()
        action = _backend_action(envelope)

        decision = self.controller.post(
            chain_id=chain_id,
            action_id=action["action_id"],
            body=action,
        )

        self.assertEqual(
            decision["schema_version"],
            "cera.pi_scene.review_decision.v1",
        )
        self.assertNotIn("provider_stage_request_sha256", decision)
        self.assertEqual(self._controller().get(chain_id), decision)
        self.assertEqual(self.invocations.calls, 2)

    def test_terminal_projection_rejects_extra_or_malformed_route_content(self) -> None:
        chain_id, envelope = self._begin_failed_chain()
        action = _backend_action(envelope)

        completion_with_raw = _completion()
        completion_with_raw["raw_provider_output"] = "private"
        self.continuation.result = completion_with_raw
        with self.assertRaisesRegex(ContractValidationError, "completion shape"):
            self.controller.post(
                chain_id=chain_id,
                action_id=action["action_id"],
                body=action,
            )

        completion_with_nested_raw = _completion()
        completion_with_nested_raw["cera"]["raw_provider_output"] = "private"
        self.continuation.result = completion_with_nested_raw
        with self.assertRaisesRegex(ContractValidationError, "not an allowed response"):
            self.controller.post(
                chain_id=chain_id,
                action_id=action["action_id"],
                body=action,
            )

        decision = {
            "schema_version": "cera.pi_scene.review_decision.v1",
            "status": "review_transitioned",
            "creator_action": "regenerate",
            "story_state_committed": False,
            "retry_mode": "not_applicable",
            "review": _review("review-" + "a" * 28),
            "successor": None,
            "operational_warnings": [],
        }
        self.continuation.result = {**decision, "debug_log_path": "D:\\private.txt"}
        with self.assertRaisesRegex(ContractValidationError, "not authoritative"):
            self.controller.post(
                chain_id=chain_id,
                action_id=action["action_id"],
                body=action,
            )

        self.continuation.result = {
            **decision,
            "review": {"review_id": "review-" + "a" * 28, "raw": "private"},
        }
        with self.assertRaisesRegex(ContractValidationError, "review payload shape"):
            self.controller.post(
                chain_id=chain_id,
                action_id=action["action_id"],
                body=action,
            )

        self.continuation.result = {**decision, "successor": {"raw": "private"}}
        with self.assertRaisesRegex(ContractValidationError, "completion shape"):
            self.controller.post(
                chain_id=chain_id,
                action_id=action["action_id"],
                body=action,
            )
        self.assertEqual(self.invocations.calls, 2)

    def test_get_reconciles_ambiguity_without_provider_dispatch(self) -> None:
        self.writer_factory.outcomes[1] = _ambiguous("ambiguous")
        chain = self.service.start_initial(scope=self.scope, packet=self.packet)
        envelope = self.service.canonical_status(chain_id=chain.chain_id)
        self.controller.begin_request(
            request_id=self.scope.request_id,
            world_id=self.scope.world_id,
            branch_id=self.scope.branch_id,
        )
        self.controller.capture_pending(envelope)

        first = self.controller.get(chain.chain_id)
        self.assertEqual(first["status"]["state"], "blocked_ambiguous")
        self.assertEqual(self.invocations.calls, 1)
        self.assertEqual(self.writer_reconciler.calls, 1)

        self.writer_reconciler.resolution = None
        restarted = self._controller()
        second = restarted.get(chain.chain_id)
        self.assertEqual(second, first)
        self.assertEqual(self.invocations.calls, 1)
        self.assertEqual(self.writer_reconciler.calls, 2)

    def test_third_failure_terminalizes_before_post_and_restart_response(self) -> None:
        self.writer_factory.outcomes = {
            1: _retryable_failure("attempt-1"),
            2: _retryable_failure("attempt-2"),
            3: _retryable_failure("attempt-3"),
        }
        chain_id, envelope = self._begin_failed_chain()
        first_action = _backend_action(envelope)
        second = self.controller.post(
            chain_id=chain_id,
            action_id=first_action["action_id"],
            body=first_action,
        )
        second_action = _backend_action(second)
        terminal = self.controller.post(
            chain_id=chain_id,
            action_id=second_action["action_id"],
            body=second_action,
        )

        self.assertEqual(terminal["status"]["state"], "attempts_exhausted")
        self.assertEqual(
            self.continuation.terminalized,
            {chain_id: "attempts_exhausted"},
        )
        self.assertEqual(self._controller().get(chain_id), terminal)
        self.assertEqual(self.invocations.calls, 3)

    def test_known_non_retry_terminalizes_during_initial_capture(self) -> None:
        self.writer_factory.outcomes[1] = _non_retryable_failure("auth")
        chain = self.service.start_initial(scope=self.scope, packet=self.packet)
        envelope = self.service.canonical_status(chain_id=chain.chain_id)
        self.controller.begin_request(
            request_id=self.scope.request_id,
            world_id=self.scope.world_id,
            branch_id=self.scope.branch_id,
        )

        projected = self.controller.capture_pending(envelope)

        self.assertEqual(projected["status"]["state"], "recovery_required")
        self.assertEqual(
            self.continuation.terminalized,
            {chain.chain_id: "recovery_required"},
        )
        self.assertEqual(self._controller().get(chain.chain_id), projected)
        self.assertEqual(self.invocations.calls, 1)

    def test_branch_barrier_allows_only_same_request_until_completion(self) -> None:
        self.controller.begin_request(
            request_id="request-one",
            world_id="world-one",
            branch_id="branch-one",
        )
        self.controller.begin_request(
            request_id="request-one",
            world_id="world-one",
            branch_id="branch-one",
        )
        with self.assertRaisesRegex(StateConflictError, "unresolved protected request"):
            self.controller.begin_request(
                request_id="request-two",
                world_id="world-one",
                branch_id="branch-one",
            )
        self.controller.complete_request(
            request_id="request-one",
            world_id="world-one",
            branch_id="branch-one",
        )
        self.controller.begin_request(
            request_id="request-two",
            world_id="world-one",
            branch_id="branch-one",
        )

    def test_registration_rejects_incomplete_terminal_privacy_port(self) -> None:
        class _Incomplete:
            def latest_chain_for_chain(self, chain_id: str) -> str:
                return chain_id

            def load_terminal_completion(self, chain_id: str) -> None:
                del chain_id
                return None

            def resume_succeeded_chain(self, chain_id: str) -> None:
                del chain_id
                return None

            def project_terminal_completion(
                self,
                *,
                chain_id: str,
                result: object,
            ) -> Mapping[str, Any]:
                del chain_id, result
                return {}

            def bind_terminal_completion(
                self,
                *,
                chain_id: str,
                completion: Mapping[str, Any],
            ) -> Mapping[str, Any]:
                del chain_id, completion
                return {}

        with self.assertRaisesRegex(ContractValidationError, "incomplete"):
            ProviderStageRetryHttpRegistrationV1(
                stage=ProviderStage.PLANNER,
                continuation=cast(ProviderStageRetryHttpContinuationPort, _Incomplete()),
            )

    def test_paths_accept_only_exact_backend_identities(self) -> None:
        chain_id = "stage-retry-" + text_sha256("chain")
        action_id = "stage-action-" + text_sha256("action")
        self.assertEqual(
            provider_stage_retry_chain_id(f"/v1/cera/provider-stage-retries/{chain_id}"),
            chain_id,
        )
        self.assertEqual(
            provider_stage_retry_action_identity(
                f"/v1/cera/provider-stage-retries/{chain_id}/actions/{action_id}"
            ),
            (chain_id, action_id),
        )
        self.assertIsNone(
            provider_stage_retry_chain_id(
                f"/v1/cera/provider-stage-retries/{chain_id}/actions/{action_id}"
            )
        )
        self.assertIsNone(
            provider_stage_retry_action_identity(
                f"/v1/cera/provider-stage-retries/{chain_id}/actions/check_status"
            )
        )

    def test_generic_authority_disables_stale_legacy_transport_retry_routes(self) -> None:
        class _GenericAdapter(PiSceneHttpAdapter):
            def __init__(
                self,
                provider_stage_retry_http: ProviderStageRetryHttpControllerV1,
            ) -> None:
                self.provider_stage_retry_http = provider_stage_retry_http
                self.readable_debug = None
                self.legacy_calls = 0

            def _retry_transport_locked(self, retry_id: str) -> dict[str, Any]:
                del retry_id
                self.legacy_calls += 1
                raise AssertionError("legacy transport Retry dispatched")

            def _transport_retry_controller(
                self,
                journal: PiSceneRequestJournal | None = None,
            ) -> TransportRetryHttpController:
                del journal
                self.legacy_calls += 1
                raise AssertionError("legacy transport Retry status was projected")

        adapter = _GenericAdapter(self.controller)
        stale_retry_id = "retry-" + text_sha256("stale-legacy-budget")
        with self.assertRaises(TransportRetryNotFoundError):
            adapter.transport_retry_status(stale_retry_id)
        with self.assertRaises(TransportRetryNotFoundError):
            adapter.retry_transport(stale_retry_id)
        self.assertEqual(adapter.legacy_calls, 0)

        token = "generic-authority-token-is-long-enough"
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
        root = f"http://127.0.0.1:{server.server_address[1]}"

        def request(method: str, body: object | None = None) -> tuple[int, dict[str, Any]]:
            call = Request(
                f"{root}/v1/cera/transport-retries/{stale_retry_id}",
                data=None if body is None else json.dumps(body).encode(),
                headers={
                    "Authorization": f"Bearer {token}",
                    **({} if body is None else {"Content-Type": "application/json"}),
                },
                method=method,
            )
            try:
                with urlopen(call, timeout=10) as response:
                    return response.status, json.loads(response.read())
            except HTTPError as exc:
                return exc.code, json.loads(exc.read())

        try:
            get_status, get_payload = request("GET")
            post_status, post_payload = request("POST", {})
            self.assertEqual((get_status, post_status), (404, 404))
            self.assertEqual(
                get_payload["error"]["error_code"],
                "CERA_TRANSPORT_RETRY_NOT_FOUND",
            )
            self.assertEqual(
                post_payload["error"]["error_code"],
                "CERA_TRANSPORT_RETRY_NOT_FOUND",
            )
            self.assertEqual(adapter.legacy_calls, 0)
            self.assertEqual(self.invocations.calls, 0)
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=5)

    def test_authenticated_server_get_is_read_only_and_post_forwards_exact_action(self) -> None:
        chain_id = "stage-retry-" + text_sha256("server-chain")
        action_id = "stage-action-" + text_sha256("server-action")
        action = {
            "schema_version": "cera.provider_stage_retry_action.v1",
            "action_id": action_id,
            "chain_id": chain_id,
            "action_family": "provider_stage_control",
            "action_kind": "provider_retry",
            "automatic": False,
            "provider_dispatch_authorized": True,
            "consumes_retry_action": True,
            "retry_action_ordinal": 1,
            "whole_request_replay_authorized": False,
            "provider_substitution_authorized": False,
            "expected_chain_sha256": "1" * 64,
        }

        class _Adapter:
            readable_debug = None
            status: dict[str, Any] = {}

            def __init__(self) -> None:
                self.get_calls: list[str] = []
                self.post_calls: list[tuple[str, str, dict[str, Any]]] = []

            def provider_stage_retry_status(self, value: str) -> dict[str, Any]:
                self.get_calls.append(value)
                return {"kind": "read_only", "chain_id": value}

            def provider_stage_retry_action(
                self,
                *,
                chain_id: str,
                action_id: str,
                body: dict[str, Any],
            ) -> dict[str, Any]:
                self.post_calls.append((chain_id, action_id, body))
                return {"kind": "manual_post", "chain_id": chain_id}

        adapter = _Adapter()
        token = "stage-http-token-that-is-long-enough"
        server = build_pi_scene_server(
            cast(PiSceneHttpAdapter, adapter),
            PiSceneServerConfigV1(
                host="127.0.0.1",
                port=0,
                authorization_token=token,
                approved_origins=("http://127.0.0.1:8000",),
            ),
        )
        worker = Thread(target=server.serve_forever, daemon=True)
        worker.start()
        root = f"http://127.0.0.1:{server.server_address[1]}"

        def request(path: str, *, method: str, body: object | None = None) -> tuple[int, Any]:
            call = Request(
                root + path,
                data=None if body is None else json.dumps(body).encode(),
                headers={
                    "Authorization": f"Bearer {token}",
                    **({} if body is None else {"Content-Type": "application/json"}),
                },
                method=method,
            )
            try:
                with urlopen(call, timeout=10) as response:
                    return response.status, json.loads(response.read())
            except HTTPError as exc:
                return exc.code, json.loads(exc.read())

        try:
            status, read_only = request(
                f"/v1/cera/provider-stage-retries/{chain_id}",
                method="GET",
            )
            self.assertEqual(status, 200)
            self.assertEqual(read_only["kind"], "read_only")
            self.assertEqual(adapter.get_calls, [chain_id])
            self.assertEqual(adapter.post_calls, [])

            status, posted = request(
                f"/v1/cera/provider-stage-retries/{chain_id}/actions/{action_id}",
                method="POST",
                body=action,
            )
            self.assertEqual(status, 200)
            self.assertEqual(posted["kind"], "manual_post")
            self.assertEqual(adapter.post_calls, [(chain_id, action_id, action)])

            invalid_status, _ = request(
                f"/v1/cera/provider-stage-retries/{chain_id}/actions/check_status",
                method="POST",
                body={},
            )
            self.assertEqual(invalid_status, 404)
            self.assertEqual(len(adapter.post_calls), 1)

            unauthorized = Request(
                root + f"/v1/cera/provider-stage-retries/{chain_id}",
                method="GET",
            )
            with self.assertRaises(HTTPError) as caught:
                urlopen(unauthorized, timeout=10)
            self.assertEqual(caught.exception.code, 401)
            self.assertEqual(adapter.get_calls, [chain_id])
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
