from __future__ import annotations

import json
import threading
import unittest
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, cast

from cera.errors import StateConflictError
from cera.pi_scene.contracts import (
    LeanAcceptedTurnReceiptV1,
    PiWriterReceiptV1,
    RecordingStatus,
    SceneRoute,
)
from cera.pi_scene.http_contracts import LeanSceneRequestControlsV2
from cera.pi_scene.pi_adapter import (
    PiSceneAdapter,
    PiSceneInvocationResultV1,
    PiSceneInvocationV1,
)
from cera.pi_scene.provider_stage_retry import (
    ProviderStage,
    ProviderStageAttemptV1,
    ProviderStageFailureClass,
    ProviderStageRetryChainV1,
)
from cera.pi_scene.provider_stage_retry_blob import TrustedLocalProtectedStageBlobStore
from cera.pi_scene.provider_stage_retry_executor import (
    ProviderStageAttemptMetricsV1,
    ProviderStageAttemptOutcomeV1,
    ProviderStageClosedFailureV1,
    ProviderStageDispatchAmbiguousV1,
    ProviderStageSemanticDisposition,
    ProviderStageSuccessfulResultV1,
)
from cera.pi_scene.provider_stage_retry_ordinary import (
    OrdinaryProviderStageRetryRuntimeV1,
    OrdinaryStageRetryRequestContextV1,
    PlannerFrozenRetrievalV1,
    deserialize_pi_result,
    planner_request_from_frozen_input,
    recorder_invocation_from_frozen_input,
    semantic_validation_disposition,
    serialize_pi_result,
    serialize_planner_result,
    serialize_semantic_validation_result,
    writer_invocation_from_frozen_input,
)
from cera.pi_scene.provider_stage_retry_ordinary_custody import (
    ProtectedOrdinaryStageRetryCustodyStoreV1,
)
from cera.pi_scene.provider_stage_retry_packets import (
    ImmutableRetrievalSnapshotIdentityV1,
    ProviderStageConfigurationV1,
    ProviderStageFrozenPacketV1,
)
from cera.pi_scene.provider_stage_retry_runtime import (
    ProviderStageRetryRuntimeServiceV1,
    ProviderStageRuntimeAdapterV1,
    backend_action_from_envelope,
)
from cera.pi_scene.provider_stage_retry_scope import ProviderStageRetryOccurrenceScopeV1
from cera.pi_scene.request_binding import build_request_binding
from cera.pi_scene.review_store import LeanDecisionResultV1, LeanSceneTurnInputV1
from cera.pi_scene.runtime import (
    LeanPiSceneCoordinator,
    PlannerTurnInputV1,
    PlannerTurnOutputV1,
    ProviderStageRetryPendingError,
)
from cera.pi_scene.store import LeanSceneStore
from cera.pi_scene.writer_view import WriterViewInputV1, WriterViewMaterializer
from cera.semantic_validation import (
    SemanticVerdict,
)
from cera.serialization import bytes_sha256, canonical_sha256, text_sha256
from cera.storage.sqlite_store import SQLiteAuthorityStore

from .test_pi_scene_lean_v1 import FakePi, FakePlanner, sequence
from .test_pi_scene_semantic_acceptance import _validation
from .test_pi_scene_store_quality import _candidate


def _sha(label: str) -> str:
    return text_sha256(label)


def _metrics(label: str) -> ProviderStageAttemptMetricsV1:
    return ProviderStageAttemptMetricsV1(
        ledger_prefix_after_sha256=_sha(f"ledger:{label}"),
        provider_operations_observed=1,
        provider_operations_conservative=1,
        duration_ms=11,
    )


def _success(
    label: str,
    exact_result: bytes,
    *,
    disposition: ProviderStageSemanticDisposition = (
        ProviderStageSemanticDisposition.NOT_APPLICABLE
    ),
) -> ProviderStageSuccessfulResultV1:
    return ProviderStageSuccessfulResultV1(
        exact_result=exact_result,
        result_evidence_sha256=_sha(f"result:{label}"),
        metrics=_metrics(label),
        semantic_disposition=disposition,
    )


def _failure(label: str) -> ProviderStageClosedFailureV1:
    return ProviderStageClosedFailureV1(
        failure_class=ProviderStageFailureClass.PROVIDER_UNAVAILABLE,
        failure_evidence_sha256=_sha(f"failure:{label}"),
        metrics=_metrics(label),
    )


class _InvocationCounts:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.by_stage: dict[ProviderStage, int] = {stage: 0 for stage in ProviderStage}

    def record(self, stage: ProviderStage) -> None:
        with self._lock:
            self.by_stage[stage] += 1


class _Prepared:
    def __init__(
        self,
        *,
        stage: ProviderStage,
        outcome: ProviderStageAttemptOutcomeV1,
        counts: _InvocationCounts,
        label: str,
    ) -> None:
        self._stage = stage
        self._outcome = outcome
        self._counts = counts
        self._dispatch_evidence_sha256 = _sha(f"dispatch:{label}")

    @property
    def dispatch_evidence_sha256(self) -> str:
        return self._dispatch_evidence_sha256

    def invoke(self) -> ProviderStageAttemptOutcomeV1:
        self._counts.record(self._stage)
        return self._outcome


class _Owner:
    def __init__(
        self,
        *,
        stage: ProviderStage,
        attempt_number: int,
        exact_input: bytes,
        session_scope_sha256: str,
        ledger_prefix_before_sha256: str,
        outcome: ProviderStageAttemptOutcomeV1,
        counts: _InvocationCounts,
    ) -> None:
        self.stage = stage
        self.attempt_number = attempt_number
        self.exact_input = exact_input
        self._session_scope_sha256 = session_scope_sha256
        self._ledger_prefix_before_sha256 = ledger_prefix_before_sha256
        self.outcome = outcome
        self.counts = counts

    @property
    def session_scope_sha256(self) -> str:
        return self._session_scope_sha256

    @property
    def ledger_prefix_before_sha256(self) -> str:
        return self._ledger_prefix_before_sha256

    @property
    def maximum_provider_operations(self) -> int:
        return 1

    def prepare(
        self,
        *,
        chain_id: str,
        attempt_number: int,
        exact_input: bytes,
    ) -> _Prepared:
        if attempt_number != self.attempt_number or exact_input != self.exact_input:
            raise AssertionError("ordinary integration changed the exact attempt")
        return _Prepared(
            stage=self.stage,
            outcome=self.outcome,
            counts=self.counts,
            label=f"{chain_id}:{attempt_number}",
        )

    def retire(
        self,
        *,
        chain_id: str,
        attempt_number: int,
        failure_class: ProviderStageFailureClass,
    ) -> str:
        return _sha(f"retire:{chain_id}:{attempt_number}:{failure_class.value}")


class _OwnerFactory:
    def __init__(
        self,
        stage: ProviderStage,
        outcomes: dict[int, ProviderStageAttemptOutcomeV1],
        counts: _InvocationCounts,
    ) -> None:
        self.stage = stage
        self.outcomes = outcomes
        self.initial_outcomes_by_ordinal: dict[int, ProviderStageAttemptOutcomeV1] = {}
        self.initial_outcome_from_input: Callable[[bytes], ProviderStageAttemptOutcomeV1] | None = (
            None
        )
        self.counts = counts

    def _owner(
        self,
        *,
        attempt_number: int,
        exact_input: bytes,
        session_scope_sha256: str,
        ledger_prefix_before_sha256: str,
        outcome: ProviderStageAttemptOutcomeV1 | None = None,
    ) -> _Owner:
        return _Owner(
            stage=self.stage,
            attempt_number=attempt_number,
            exact_input=exact_input,
            session_scope_sha256=session_scope_sha256,
            ledger_prefix_before_sha256=ledger_prefix_before_sha256,
            outcome=(self.outcomes[attempt_number] if outcome is None else outcome),
            counts=self.counts,
        )

    def create_initial_owner(
        self,
        *,
        scope: ProviderStageRetryOccurrenceScopeV1,
        packet: ProviderStageFrozenPacketV1,
    ) -> _Owner:
        if scope.stage is not self.stage:
            raise AssertionError("ordinary integration selected the wrong stage owner")
        exact_input = packet.exact_bytes
        return self._owner(
            attempt_number=1,
            exact_input=exact_input,
            session_scope_sha256=_sha(f"session:{self.stage.value}:{scope.identity.chain_id}:1"),
            ledger_prefix_before_sha256=_sha(
                f"ledger-before:{self.stage.value}:{scope.identity.chain_id}:1"
            ),
            outcome=(
                self.initial_outcome_from_input(exact_input)
                if self.initial_outcome_from_input is not None
                else self.initial_outcomes_by_ordinal.get(
                    scope.stage_ordinal,
                    self.outcomes[1],
                )
            ),
        )

    def create_retry_owner(
        self,
        *,
        chain: ProviderStageRetryChainV1,
        exact_input: bytes,
    ) -> _Owner:
        attempt_number = len(chain.attempts) + 1
        ledger_before = chain.attempts[-1].ledger_prefix_after_sha256
        if ledger_before is None:
            raise AssertionError("ordinary Retry owner lost the prior ledger")
        return self._owner(
            attempt_number=attempt_number,
            exact_input=exact_input,
            session_scope_sha256=_sha(
                f"session:{self.stage.value}:{chain.chain_id}:{attempt_number}"
            ),
            ledger_prefix_before_sha256=ledger_before,
        )

    def reconstruct_owner(
        self,
        *,
        chain: ProviderStageRetryChainV1,
        attempt: ProviderStageAttemptV1,
        exact_input: bytes,
    ) -> _Owner:
        del chain
        return self._owner(
            attempt_number=attempt.attempt_number,
            exact_input=exact_input,
            session_scope_sha256=attempt.session_scope_sha256,
            ledger_prefix_before_sha256=attempt.ledger_prefix_before_sha256,
        )


class _Reconciler:
    def check_status(self, *, chain: ProviderStageRetryChainV1) -> None:
        del chain
        return None

    def recover_interrupted_dispatch(
        self,
        *,
        chain: ProviderStageRetryChainV1,
    ) -> ProviderStageDispatchAmbiguousV1:
        raise AssertionError(f"unexpected interrupted dispatch recovery for {chain.chain_id}")


class _Binder:
    def downstream_intent_sha256(
        self,
        *,
        chain: ProviderStageRetryChainV1,
        exact_result: bytes,
    ) -> str:
        return _sha(f"intent:{chain.chain_id}:{bytes_sha256(exact_result)}")

    def bind_once(
        self,
        *,
        chain: ProviderStageRetryChainV1,
        exact_result: bytes,
        downstream_intent_sha256: str,
    ) -> str:
        return _sha(
            f"bound:{chain.chain_id}:{bytes_sha256(exact_result)}:{downstream_intent_sha256}"
        )


def _controls() -> LeanSceneRequestControlsV2:
    return LeanSceneRequestControlsV2(
        schema_version=LeanSceneRequestControlsV2.SCHEMA_VERSION,
        session_id="ordinary-retry-session",
        reasoning_effort="medium",
    )


def _planner_request() -> PlannerTurnInputV1:
    return PlannerTurnInputV1(
        world_id="world-ordinary-retry",
        branch_id="branch-main",
        scene_id="scene-test",
        exact_user_source="Continue the ordinary scene.",
        current_state={"public_scene_state": "The conversation remains open."},
        characters={"character:hana": {"name": "Hana"}},
        relationships={},
        relevant_memories={},
        accepted_records=(),
        request_controls=_controls(),
    )


def _payload() -> dict[str, object]:
    controls = _controls()
    return {
        "model": "cera-alpha",
        "messages": [{"role": "user", "content": "Continue the ordinary scene."}],
        "cera_profile_id": "cera-pi-scene-isolated-v1",
        "cera_session_id": controls.session_id,
        "stream": False,
    }


def _context(
    generation: int = 1,
    *,
    world_id: str = "world-ordinary-retry",
    branch_id: str = "branch-main",
    payload: dict[str, object] | None = None,
    turn_input: LeanSceneTurnInputV1 | None = None,
) -> OrdinaryStageRetryRequestContextV1:
    controls = _controls()
    binding = build_request_binding(
        payload=_payload() if payload is None else payload,
        session_id=controls.session_id,
        world_id=world_id,
        branch_id=branch_id,
        route=SceneRoute.ORDINARY,
        controls=controls,
    )
    return OrdinaryStageRetryRequestContextV1(
        binding=binding,
        generation=generation,
        turn_input=(
            LeanSceneTurnInputV1(
                world_id=world_id,
                branch_id=branch_id,
                scene_id="scene-test",
                exact_user_source="Continue the ordinary scene.",
                current_state={"public_scene_state": "The conversation remains open."},
                characters={},
                relationships={},
                recent_prose=(),
                relevant_memories={},
                voice_examples={},
                craft_index={},
                request_controls=controls,
            )
            if turn_input is None
            else turn_input
        ),
        planner_retrieval=PlannerFrozenRetrievalV1(
            retrieval_snapshot=ImmutableRetrievalSnapshotIdentityV1(
                schema_version=ImmutableRetrievalSnapshotIdentityV1.SCHEMA_VERSION,
                snapshot_id="ordinary-retry-snapshot",
                snapshot_sha256=_sha("snapshot"),
                immutability_evidence_sha256=_sha("immutable"),
            ),
            tool_result_bundle={"evidence": []},
        ),
    )


def _configuration(stage: ProviderStage) -> ProviderStageConfigurationV1:
    return ProviderStageConfigurationV1.create(
        stage=stage,
        model_id=(
            "gpt-5.6-sol"
            if stage is ProviderStage.PLANNER
            else (
                "gpt-5.6-luna" if stage is ProviderStage.SEMANTIC_VALIDATOR else "deepseek-v4-flash"
            )
        ),
        reasoning_mode=(
            "medium"
            if stage is ProviderStage.PLANNER
            else "xhigh"
            if stage is ProviderStage.SEMANTIC_VALIDATOR
            else "off"
        ),
        routing={"automatic_retry": False, "fallback": False},
        content_policy_route="ordinary",
        stage_configuration={"maximum_output_tokens": 12_288},
    )


def _writer_invocation(root: Path, candidate_id: str) -> PiSceneInvocationV1:
    view = WriterViewMaterializer(root / "views").materialize(
        WriterViewInputV1(
            world_id="world-ordinary-retry",
            branch_id="branch-main",
            scene_id="scene-test",
            turn_id="turn-0001",
            candidate_id=candidate_id,
            route=SceneRoute.ORDINARY,
            user_prompt="Continue the ordinary scene.",
            primary_authority=sequence(),
            current_state={"public_scene_state": "The conversation remains open."},
            characters={},
            relationships={},
            recent_prose=(),
            relevant_memories={},
            voice_examples={},
            craft_index={},
            accepted_records=(),
        )
    )
    return PiSceneInvocationV1(
        route=SceneRoute.ORDINARY,
        purpose="writer",
        view=view,
        prompt="Write the exact planned scene.",
        candidate_id=candidate_id,
        session_dir=root / "sessions",
    )


def _pi_result(invocation: PiSceneInvocationV1, label: str) -> PiSceneInvocationResultV1:
    output = f"Hana answers and returns the floor to Ted ({label})."
    session_id = f"pi-session-{label}"
    return PiSceneInvocationResultV1(
        output_text=output,
        session_id=session_id,
        session_dir=invocation.session_dir,
        writer_receipt=PiWriterReceiptV1(
            schema_version=PiWriterReceiptV1.SCHEMA_VERSION,
            route=SceneRoute.ORDINARY,
            provider="deepseek",
            model="deepseek-v4-flash",
            pi_version="test",
            session_id_sha256=text_sha256(session_id),
            parent_session_id_sha256=None,
            request_sha256=text_sha256(invocation.prompt),
            output_sha256=text_sha256(output),
            provider_operations=1,
            tool_call_count=1,
            failed_tool_call_count=0,
            input_tokens=100,
            cached_input_tokens=0,
            output_tokens=30,
            reasoning_tokens=0,
            duration_ms=9,
            finish_status="stop",
            rehydrated=True,
        ),
        raw_event_count=5,
    )


class _RecorderBindingCrashPort:
    """Simulate process loss immediately after accepted-review persistence."""

    def __init__(self, wrapped: OrdinaryProviderStageRetryRuntimeV1) -> None:
        self.wrapped = wrapped
        self.chain_id: str | None = None

    def __getattr__(self, name: str) -> Any:
        return getattr(self.wrapped, name)

    def bind_recorder_pending(
        self,
        *,
        chain_id: str,
        review_id: str,
        accepted: LeanAcceptedTurnReceiptV1,
    ) -> None:
        del review_id, accepted
        self.chain_id = chain_id
        raise OSError("simulated crash before Recorder continuation receipt")


class OrdinaryProviderStageRetryIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.counts = _InvocationCounts()
        planner_result = PlannerTurnOutputV1(
            sequence=sequence(),
            provider_operations=1,
        )
        self.factories: dict[ProviderStage, _OwnerFactory] = {}
        for stage in ProviderStage:
            exact_result = (
                serialize_planner_result(planner_result)
                if stage is ProviderStage.PLANNER
                else f"unused:{stage.value}".encode()
            )
            self.factories[stage] = _OwnerFactory(
                stage,
                {1: _success(f"{stage.value}:1", exact_result)},
                self.counts,
            )
        self._restart_runtime()

    def _restart_runtime(self) -> None:
        registrations = tuple(
            ProviderStageRuntimeAdapterV1(
                stage=stage,
                owner_factory=self.factories[stage],
                ambiguity_reconciler=_Reconciler(),
                downstream_binder=_Binder(),
            )
            for stage in ProviderStage
        )
        self.service = ProviderStageRetryRuntimeServiceV1(
            authority_store=SQLiteAuthorityStore(self.root / "authority.sqlite3"),
            protected_blob_store=TrustedLocalProtectedStageBlobStore(self.root / "protected"),
            registrations=registrations,
        )
        self.integration = OrdinaryProviderStageRetryRuntimeV1(
            service=self.service,
            custody_store=ProtectedOrdinaryStageRetryCustodyStoreV1(
                self.root / "ordinary-protected-custody"
            ),
            configurations={stage: _configuration(stage) for stage in ProviderStage},
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _freeze(
        self,
        context: OrdinaryStageRetryRequestContextV1,
        payload: dict[str, object] | None = None,
    ) -> None:
        self.integration.freeze_pending_request(
            normalized_request=_payload() if payload is None else payload,
            context=context,
        )

    def test_planner_retry_is_manual_and_cached_request_replay_is_provider_free(self) -> None:
        planner_result = PlannerTurnOutputV1(
            sequence=sequence("after_retry"),
            provider_operations=1,
        )
        self.factories[ProviderStage.PLANNER].outcomes = {
            1: _failure("planner:1"),
            2: _success("planner:2", serialize_planner_result(planner_result)),
        }
        request = _planner_request()
        accepted_state_sha256 = _sha("accepted-state")
        context = _context()
        self._freeze(context)

        with self.integration.bind_request(context):
            with self.assertRaises(ProviderStageRetryPendingError) as captured:
                self.integration.run_planner(
                    request,
                    accepted_state_sha256=accepted_state_sha256,
                    authority_binding={"accepted_head_sha256": None},
                )
        self.assertEqual(self.counts.by_stage[ProviderStage.PLANNER], 1)
        self.integration.status(captured.exception.chain_id)
        self.assertEqual(self.counts.by_stage[ProviderStage.PLANNER], 1)

        action = backend_action_from_envelope(captured.exception.envelope)
        projection = self.integration.execute_action(action)
        self.assertTrue(projection.result_ready)
        self.assertEqual(projection.envelope["status"]["state"], "succeeded")
        self.assertEqual(self.counts.by_stage[ProviderStage.PLANNER], 2)
        duplicate_projection = self.integration.execute_action(action)
        self.assertEqual(duplicate_projection, projection)
        self.assertEqual(self.counts.by_stage[ProviderStage.PLANNER], 2)

        with self.integration.bind_request(context):
            replay = self.integration.run_planner(
                request,
                accepted_state_sha256=accepted_state_sha256,
                authority_binding={"accepted_head_sha256": None},
            )
        self.assertEqual(replay, planner_result)
        self.assertEqual(self.counts.by_stage[ProviderStage.PLANNER], 2)

        frozen_input = self.service.store.load_input(captured.exception.chain_id)
        self.assertEqual(planner_request_from_frozen_input(frozen_input), request)
        self.assertNotIn(
            request.exact_user_source.encode("utf-8"),
            (self.root / "authority.sqlite3").read_bytes(),
        )

    def test_second_writer_occurrence_retries_without_replaying_prior_provider_stages(
        self,
    ) -> None:
        context = _context()
        self._freeze(context)
        accepted_state_sha256 = _sha("accepted-state")
        planner_request = _planner_request()
        first_invocation = _writer_invocation(self.root, "candidate-first")
        second_invocation = _writer_invocation(self.root, "candidate-repair")
        first_result = _pi_result(first_invocation, "first")
        second_result = _pi_result(second_invocation, "repair")
        rejected_candidate = replace(
            _candidate(),
            world_id=planner_request.world_id,
            branch_id=planner_request.branch_id,
        )
        rejected = _validation(rejected_candidate, verdict=SemanticVerdict.REJECT)
        self.factories[ProviderStage.SEMANTIC_VALIDATOR].outcomes = {
            1: _success(
                "semantic:repair-eligible",
                serialize_semantic_validation_result(rejected),
                disposition=semantic_validation_disposition(rejected),
            )
        }
        self.factories[ProviderStage.WRITER].outcomes = {
            1: _success("writer:default", serialize_pi_result(first_result)),
            2: _failure("writer:occurrence-2:attempt-1"),
        }
        self.factories[ProviderStage.WRITER].initial_outcomes_by_ordinal = {
            1: _success("writer:occurrence-1", serialize_pi_result(first_result)),
            2: _failure("writer:occurrence-2:attempt-1"),
        }

        with self.integration.bind_request(context):
            self.integration.run_planner(
                planner_request,
                accepted_state_sha256=accepted_state_sha256,
                authority_binding={"head": None},
            )
            first_occurrence = self.integration.reserve_writer_candidate_occurrence(
                world_id=planner_request.world_id,
                branch_id=planner_request.branch_id,
                generation=context.generation,
            )
            self.assertEqual(
                self.integration.run_writer(
                    first_invocation,
                    candidate_occurrence=first_occurrence,
                    world_id=planner_request.world_id,
                    branch_id=planner_request.branch_id,
                    accepted_state_sha256=accepted_state_sha256,
                    authority_binding={"candidate_id": "candidate-first"},
                ),
                first_result,
            )
            self.integration.run_semantic_validator(
                rejected.request,
                rejected.custody,
                accepted_state_sha256=accepted_state_sha256,
                authority_binding={"candidate_id": "candidate-first"},
            )
            with self.assertRaises(ProviderStageRetryPendingError) as captured:
                second_occurrence = self.integration.reserve_writer_candidate_occurrence(
                    world_id=planner_request.world_id,
                    branch_id=planner_request.branch_id,
                    generation=context.generation,
                )
                self.integration.run_writer(
                    second_invocation,
                    candidate_occurrence=second_occurrence,
                    world_id=planner_request.world_id,
                    branch_id=planner_request.branch_id,
                    accepted_state_sha256=accepted_state_sha256,
                    authority_binding={"candidate_id": "candidate-repair"},
                )
            self.assertNotEqual(first_occurrence, second_occurrence)

        self.factories[ProviderStage.WRITER].outcomes[2] = _success(
            "writer:occurrence-2:attempt-2",
            serialize_pi_result(second_result),
        )
        action = backend_action_from_envelope(captured.exception.envelope)
        self.integration.execute_action(action)
        before_replay = dict(self.counts.by_stage)

        self._restart_runtime()
        with self.integration.bind_request(context):
            self.integration.run_planner(
                planner_request,
                accepted_state_sha256=accepted_state_sha256,
                authority_binding={"head": None},
            )
            first_occurrence = self.integration.reserve_writer_candidate_occurrence(
                world_id=planner_request.world_id,
                branch_id=planner_request.branch_id,
                generation=context.generation,
            )
            self.integration.run_writer(
                first_invocation,
                candidate_occurrence=first_occurrence,
                world_id=planner_request.world_id,
                branch_id=planner_request.branch_id,
                accepted_state_sha256=accepted_state_sha256,
                authority_binding={"candidate_id": "candidate-first"},
            )
            self.integration.run_semantic_validator(
                rejected.request,
                rejected.custody,
                accepted_state_sha256=accepted_state_sha256,
                authority_binding={"candidate_id": "candidate-first"},
            )
            second_occurrence = self.integration.reserve_writer_candidate_occurrence(
                world_id=planner_request.world_id,
                branch_id=planner_request.branch_id,
                generation=context.generation,
            )
            replay = self.integration.run_writer(
                second_invocation,
                candidate_occurrence=second_occurrence,
                world_id=planner_request.world_id,
                branch_id=planner_request.branch_id,
                accepted_state_sha256=accepted_state_sha256,
                authority_binding={"candidate_id": "candidate-repair"},
            )

        self.assertEqual(replay, second_result)
        self.assertEqual(self.counts.by_stage, before_replay)
        self.assertEqual(self.counts.by_stage[ProviderStage.PLANNER], 1)
        self.assertEqual(self.counts.by_stage[ProviderStage.WRITER], 3)

    def test_writer_occurrence_rejects_head_and_authority_drift(self) -> None:
        context = _context()
        self._freeze(context)
        invocation = _writer_invocation(self.root, "candidate-drift")
        result = _pi_result(invocation, "drift")
        self.factories[ProviderStage.WRITER].outcomes = {
            1: _success("writer:drift-base", serialize_pi_result(result))
        }
        accepted_state_sha256 = _sha("accepted-state")
        authority = {
            "candidate_id": "candidate-drift",
            "primary_authority_sha256": _sha("primary-authority"),
            "replacement_base_sha256": None,
        }
        with self.integration.bind_request(context):
            occurrence = self.integration.reserve_writer_candidate_occurrence(
                world_id=context.binding.world_id,
                branch_id=context.binding.branch_id,
                generation=context.generation,
            )
            self.assertEqual(
                self.integration.run_writer(
                    invocation,
                    candidate_occurrence=occurrence,
                    world_id=context.binding.world_id,
                    branch_id=context.binding.branch_id,
                    accepted_state_sha256=accepted_state_sha256,
                    authority_binding=authority,
                ),
                result,
            )
        calls = self.counts.by_stage[ProviderStage.WRITER]

        with self.integration.bind_request(context):
            same_occurrence = self.integration.reserve_writer_candidate_occurrence(
                world_id=context.binding.world_id,
                branch_id=context.binding.branch_id,
                generation=context.generation,
            )
            self.assertEqual(same_occurrence, occurrence)
            with self.assertRaisesRegex(StateConflictError, "changed accepted head"):
                self.integration.run_writer(
                    invocation,
                    candidate_occurrence=same_occurrence,
                    world_id=context.binding.world_id,
                    branch_id=context.binding.branch_id,
                    accepted_state_sha256=_sha("different-accepted-state"),
                    authority_binding=authority,
                )

        with self.integration.bind_request(context):
            same_occurrence = self.integration.reserve_writer_candidate_occurrence(
                world_id=context.binding.world_id,
                branch_id=context.binding.branch_id,
                generation=context.generation,
            )
            with self.assertRaisesRegex(StateConflictError, "changed accepted head"):
                self.integration.run_writer(
                    invocation,
                    candidate_occurrence=same_occurrence,
                    world_id=context.binding.world_id,
                    branch_id=context.binding.branch_id,
                    accepted_state_sha256=accepted_state_sha256,
                    authority_binding={**authority, "replacement_base_sha256": _sha("other")},
                )
        self.assertEqual(self.counts.by_stage[ProviderStage.WRITER], calls)

    def test_semantic_rejection_is_a_successful_provider_result(self) -> None:
        candidate = _candidate()
        rejected = _validation(candidate, verdict=SemanticVerdict.REJECT)
        self.factories[ProviderStage.SEMANTIC_VALIDATOR].outcomes = {
            1: _success(
                "semantic:reject",
                serialize_semantic_validation_result(rejected),
                disposition=semantic_validation_disposition(rejected),
            )
        }
        context = _context(
            world_id=candidate.world_id,
            branch_id=candidate.branch_id,
            turn_input=LeanSceneTurnInputV1(
                world_id=candidate.world_id,
                branch_id=candidate.branch_id,
                scene_id=candidate.scene_id,
                exact_user_source=candidate.exact_user_source,
                current_state={"public_scene_state": "The conversation remains open."},
                characters={},
                relationships={},
                recent_prose=(),
                relevant_memories={},
                voice_examples={},
                craft_index={},
                request_controls=_controls(),
            ),
        )
        self._freeze(context)
        with self.integration.bind_request(context):
            result = self.integration.run_semantic_validator(
                rejected.request,
                rejected.custody,
                accepted_state_sha256=_sha("accepted-state"),
                authority_binding={"candidate_id": rejected.custody.candidate_id},
            )
        self.assertEqual(result, rejected)
        self.assertEqual(result.verdict.verdict, SemanticVerdict.REJECT)
        self.assertEqual(
            self.counts.by_stage[ProviderStage.SEMANTIC_VALIDATOR],
            1,
        )

    def test_recorder_exhaustion_keeps_accepted_story_and_enters_repair_state(self) -> None:
        store = LeanSceneStore(self.root / "accepted-world")
        accepted = store.accept(_candidate())
        authority = json.loads(accepted.primary_authority_json)
        view = WriterViewMaterializer(self.root / "recorder-views").materialize(
            WriterViewInputV1(
                world_id=accepted.world_id,
                branch_id=accepted.branch_id,
                scene_id=accepted.scene_id,
                turn_id=accepted.accepted_turn_id,
                candidate_id="record-test",
                route=SceneRoute.ORDINARY,
                user_prompt=accepted.exact_user_source,
                primary_authority=authority,
                current_state={"recording_phase": "post_accept"},
                characters={},
                relationships={},
                recent_prose=(accepted.exact_accepted_prose,),
                relevant_memories={},
                voice_examples={},
                craft_index={},
                accepted_records=(),
                purpose="recorder",
            )
        )
        invocation = PiSceneInvocationV1(
            route=SceneRoute.ORDINARY,
            purpose="recorder",
            view=view,
            prompt="Record the exact accepted prose.",
            candidate_id="recorder-test",
            session_dir=self.root / "recorder-sessions",
        )
        self.factories[ProviderStage.RECORDER].outcomes = {
            1: _failure("recorder:1"),
            2: _failure("recorder:2"),
            3: _failure("recorder:3"),
        }
        recorder_payload = {"model": "cera-alpha", "messages": [], "stream": False}
        context = _context(
            generation=accepted.generation,
            world_id=accepted.world_id,
            branch_id=accepted.branch_id,
            payload=recorder_payload,
            turn_input=LeanSceneTurnInputV1(
                world_id=accepted.world_id,
                branch_id=accepted.branch_id,
                scene_id=accepted.scene_id,
                exact_user_source=accepted.exact_user_source,
                current_state={"public_scene_state": "The conversation remains open."},
                characters={},
                relationships={},
                recent_prose=(),
                relevant_memories={},
                voice_examples={},
                craft_index={},
                request_controls=_controls(),
            ),
        )
        # The fixture candidate uses the same branch but a different world;
        # bind an exact request identity for that accepted branch.
        context = OrdinaryStageRetryRequestContextV1(
            binding=build_request_binding(
                payload=recorder_payload,
                session_id=context.binding.session_id,
                world_id=accepted.world_id,
                branch_id=accepted.branch_id,
                route=SceneRoute.ORDINARY,
                controls=_controls(),
            ),
            generation=accepted.generation,
            turn_input=context.turn_input,
            planner_retrieval=context.planner_retrieval,
        )
        accepted_state_sha256 = canonical_sha256(
            {"accepted_receipt_sha256": accepted.receipt_sha256}
        )
        self._freeze(context, recorder_payload)
        with self.integration.bind_request(context):
            with self.assertRaises(ProviderStageRetryPendingError) as first:
                self.integration.run_recorder(
                    invocation,
                    accepted=accepted,
                    accepted_state_sha256=accepted_state_sha256,
                    authority_binding={"accepted_receipt_sha256": accepted.receipt_sha256},
                )
        second_action = backend_action_from_envelope(first.exception.envelope)
        second = self.integration.execute_action(second_action)
        third_action = backend_action_from_envelope(second.envelope)
        terminal = self.integration.execute_action(third_action)

        self.assertFalse(terminal.result_ready)
        self.assertEqual(
            terminal.envelope["status"]["state"],
            "recording_repair_required",
        )
        self.assertTrue(terminal.envelope["status"]["story_state_committed"])
        head = store.load_head(world_id=accepted.world_id, branch_id=accepted.branch_id)
        self.assertEqual(head.accepted_head_sha256, accepted.receipt_sha256)
        self.assertEqual(self.counts.by_stage[ProviderStage.RECORDER], 3)

    def test_recorder_restart_recovers_after_accept_before_mapping(self) -> None:
        writer_factory = self.factories[ProviderStage.WRITER]
        writer_factory.initial_outcome_from_input = lambda exact_input: _success(
            "coordinator-writer",
            serialize_pi_result(
                _pi_result(
                    writer_invocation_from_frozen_input(exact_input),
                    "coordinator-writer",
                )
            ),
        )

        self.factories[ProviderStage.RECORDER].outcomes = {
            1: _failure("coordinator-recorder:first")
        }

        context = _context()
        self._freeze(context)
        scene_store = LeanSceneStore(self.root / "coordinator-world")
        crash_port = _RecorderBindingCrashPort(self.integration)
        review_root = self.root / "coordinator-review-state"
        coordinator = LeanPiSceneCoordinator(
            store=scene_store,
            planner=FakePlanner(),
            writer_views=WriterViewMaterializer(self.root / "coordinator-views"),
            pi=cast(PiSceneAdapter, FakePi()),
            session_root=review_root,
            ordinary_stage_retry=crash_port,
        )
        with self.integration.bind_request(context):
            review = coordinator.start_ordinary(context.turn_input)
            with self.assertRaisesRegex(
                StateConflictError,
                "accepted story retained but Recorder continuation custody failed",
            ):
                coordinator.accept(review.review_id)

        chain_id = crash_port.chain_id
        self.assertIsNotNone(chain_id)
        assert chain_id is not None
        self.assertIsNone(self.integration.find_recorder_continuation(chain_id))
        advanced_head = scene_store.load_head(
            world_id=context.binding.world_id,
            branch_id=context.binding.branch_id,
        )
        self.assertIsNotNone(advanced_head.receipt)
        self.assertEqual(advanced_head.generation, 1)

        recorder_invocation = recorder_invocation_from_frozen_input(
            self.service.store.load_input(chain_id)
        )
        recorder_output = json.dumps(
            {
                "secondary_canon": [],
                "resulting_public_state": ("Hana has answered and the conversation remains open."),
                "relationship_changes": [],
                "knowledge_changes": [],
                "durable_changes": [],
                "unresolved_threads": ["Ted may respond."],
            },
            separators=(",", ":"),
        )
        base_result = _pi_result(recorder_invocation, "coordinator-recorder-retry")
        recorder_result = replace(
            base_result,
            output_text=recorder_output,
            writer_receipt=replace(
                base_result.writer_receipt,
                output_sha256=text_sha256(recorder_output),
            ),
        )
        self.factories[ProviderStage.RECORDER].outcomes[2] = _success(
            "coordinator-recorder:retry",
            serialize_pi_result(recorder_result),
        )
        action = backend_action_from_envelope(self.integration.status(chain_id))
        projection = self.integration.execute_action(action)
        self.assertTrue(projection.result_ready)
        calls_after_retry = self.counts.by_stage[ProviderStage.RECORDER]

        self._restart_runtime()
        restarted_coordinator = LeanPiSceneCoordinator(
            store=scene_store,
            planner=FakePlanner(),
            writer_views=WriterViewMaterializer(self.root / "coordinator-views"),
            pi=cast(PiSceneAdapter, FakePi()),
            session_root=review_root,
            ordinary_stage_retry=self.integration,
        )
        _, recovered_context = self.integration.pending_request_for_chain(chain_id)
        self.assertEqual(
            canonical_sha256(recovered_context.turn_input),
            canonical_sha256(context.turn_input),
        )
        decision = self.integration.resume_succeeded_chain(
            chain_id,
            continuation=restarted_coordinator,
        )
        self.assertIsInstance(decision, LeanDecisionResultV1)
        assert isinstance(decision, LeanDecisionResultV1)
        self.assertIsNotNone(decision.review.recording_attempt)
        assert decision.review.recording_attempt is not None
        self.assertIs(
            decision.review.recording_attempt.status,
            RecordingStatus.COMPLETE,
            decision.review.recording_attempt,
        )
        self.assertEqual(
            self.counts.by_stage[ProviderStage.RECORDER],
            calls_after_retry,
        )

    def test_pi_result_codec_preserves_path_and_receipt(self) -> None:
        invocation = _writer_invocation(self.root, "candidate-codec")
        result = _pi_result(invocation, "codec")
        self.assertEqual(deserialize_pi_result(serialize_pi_result(result)), result)


if __name__ == "__main__":
    unittest.main()
