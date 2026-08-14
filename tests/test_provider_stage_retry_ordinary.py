from __future__ import annotations

import json
import threading
import unittest
from collections.abc import Callable, Mapping
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, cast
from unittest.mock import patch

from cera.errors import StateConflictError
from cera.generated.provider_stage_retry_contracts_v1 import ProviderStageRetryActionV1
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
    ProviderStageRetryPhase,
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
    deserialize_reader_validation_result,
    planner_request_from_frozen_input,
    reader_validation_disposition,
    reader_validation_input_from_frozen_input,
    recorder_invocation_from_frozen_input,
    semantic_validation_disposition,
    semantic_validation_input_from_frozen_input,
    serialize_pi_result,
    serialize_planner_result,
    serialize_reader_validation_result,
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
from cera.reader_validation import (
    BoundReaderValidationV1,
    ReaderCharacterContextV1,
    ReaderStatus,
    ReaderValidationCustodyV1,
    ReaderValidationRequestV1,
    ReaderVerdictV1,
)
from cera.semantic_validation import (
    BoundSemanticValidationV1,
    SemanticVerdict,
)
from cera.serialization import (
    bytes_sha256,
    canonical_bytes,
    canonical_sha256,
    domain_sha256,
    text_sha256,
    to_primitive,
)
from cera.storage.sqlite_store import SQLiteAuthorityStore

from .test_cognition_contracts import _plan
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


def _reader_validation() -> tuple[
    ReaderValidationRequestV1,
    ReaderValidationCustodyV1,
    BoundReaderValidationV1,
]:
    plan = _plan()
    prose = "Sakura keeps the door closed and asks who is outside."
    request = ReaderValidationRequestV1(
        schema_version=ReaderValidationRequestV1.SCHEMA_VERSION,
        cognition_plan=plan,
        exact_candidate_prose=prose,
        immediate_prior_accepted_prose=None,
        current_public_state="The door remains closed while Sakura verifies.",
        scene_depth="medium",
        character_context=(
            ReaderCharacterContextV1(
                character_id="character:sakura_hanezawa",
                display_name="Sakura",
                role="resident",
                voice="Direct, grounded, and concise.",
            ),
        ),
    )
    custody = ReaderValidationCustodyV1(
        schema_version=ReaderValidationCustodyV1.SCHEMA_VERSION,
        request_id="request-reader-retry",
        candidate_id="candidate-reader-retry",
        world_id="world-ordinary-retry",
        branch_id="branch-main",
        accepted_head_sha256=None,
        candidate_sha256=_sha("reader-candidate"),
        cognition_plan_sha256=canonical_sha256(plan),
        candidate_prose_sha256=text_sha256(prose),
        immediate_prior_accepted_prose_sha256=None,
        validation_request_sha256=canonical_sha256(request),
    )
    result = BoundReaderValidationV1(
        request=request,
        custody=custody,
        verdict=ReaderVerdictV1(status=ReaderStatus.ACCEPTED),
    )
    return request, custody, result


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
            if stage in {ProviderStage.PLANNER, ProviderStage.READER}
            else (
                "gpt-5.6-luna" if stage is ProviderStage.SEMANTIC_VALIDATOR else "deepseek-v4-flash"
            )
        ),
        reasoning_mode=(
            "medium"
            if stage is ProviderStage.PLANNER
            else "xhigh"
            if stage is ProviderStage.READER
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


class _UninvokedReaderValidator:
    def validate(
        self,
        request: ReaderValidationRequestV1,
        custody: ReaderValidationCustodyV1,
    ) -> BoundReaderValidationV1:
        del request, custody
        raise AssertionError("prepared Reader lane must remain uninvoked")


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
        self.custody = ProtectedOrdinaryStageRetryCustodyStoreV1(
            self.root / "ordinary-protected-custody"
        )
        self.integration = OrdinaryProviderStageRetryRuntimeV1(
            service=self.service,
            custody_store=self.custody,
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

    def _exhaust_accepted_recorder(
        self,
        label: str,
    ) -> tuple[
        OrdinaryStageRetryRequestContextV1,
        LeanSceneStore,
        Path,
        WriterViewMaterializer,
        LeanPiSceneCoordinator,
        str,
        ProviderStageRetryActionV1,
    ]:
        writer_factory = self.factories[ProviderStage.WRITER]
        writer_factory.initial_outcome_from_input = lambda exact_input: _success(
            f"{label}:writer",
            serialize_pi_result(
                _pi_result(
                    writer_invocation_from_frozen_input(exact_input),
                    f"{label}:writer",
                )
            ),
        )
        recorder_factory = self.factories[ProviderStage.RECORDER]
        recorder_factory.initial_outcome_from_input = None
        recorder_factory.outcomes = {
            1: _failure(f"{label}:recorder:1"),
            2: _failure(f"{label}:recorder:2"),
            3: _failure(f"{label}:recorder:3"),
        }
        context = _context()
        self._freeze(context)
        scene_store = LeanSceneStore(self.root / f"{label}-world")
        review_root = self.root / f"{label}-reviews"
        views = WriterViewMaterializer(self.root / f"{label}-views")
        coordinator = LeanPiSceneCoordinator(
            store=scene_store,
            planner=FakePlanner(),
            writer_views=views,
            pi=cast(PiSceneAdapter, FakePi()),
            session_root=review_root,
            ordinary_stage_retry=self.integration,
        )
        with self.integration.bind_request(context):
            review = coordinator.start_ordinary(context.turn_input)
            self.integration.bind_review_request(review)
            with self.assertRaises(ProviderStageRetryPendingError) as first:
                coordinator.accept(review.review_id)
        second = self.integration.execute_action(
            backend_action_from_envelope(first.exception.envelope)
        )
        terminal = self.integration.execute_action(backend_action_from_envelope(second.envelope))
        self.assertEqual(terminal.envelope["status"]["state"], "recording_repair_required")
        self.assertEqual(self.counts.by_stage[ProviderStage.RECORDER], 3)
        return (
            context,
            scene_store,
            review_root,
            views,
            coordinator,
            review.review_id,
            backend_action_from_envelope(terminal.envelope),
        )

    def _set_recorder_success(self, chain_id: str, label: str) -> None:
        invocation = recorder_invocation_from_frozen_input(self.service.store.load_input(chain_id))
        recorder_output = json.dumps(
            {
                "secondary_canon": [],
                "resulting_public_state": "The accepted conversation remains open.",
                "relationship_changes": [],
                "knowledge_changes": [],
                "durable_changes": [],
                "unresolved_threads": ["Ted may respond."],
            },
            separators=(",", ":"),
        )
        base = _pi_result(invocation, label)
        result = replace(
            base,
            output_text=recorder_output,
            writer_receipt=replace(
                base.writer_receipt,
                output_sha256=text_sha256(recorder_output),
            ),
        )
        self.factories[ProviderStage.RECORDER].outcomes[1] = _success(
            label,
            serialize_pi_result(result),
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

    def test_reader_retry_and_restart_are_lane_local_and_provider_free(self) -> None:
        request, custody, expected = _reader_validation()
        reader_factory = self.factories[ProviderStage.READER]
        reader_factory.outcomes = {
            1: _failure("reader:1"),
            2: _success(
                "reader:2",
                serialize_reader_validation_result(expected),
                disposition=reader_validation_disposition(expected),
            ),
        }
        context = _context()
        self._freeze(context)
        unchanged_stages = (
            ProviderStage.PLANNER,
            ProviderStage.WRITER,
            ProviderStage.SEMANTIC_VALIDATOR,
        )
        before = {stage: self.counts.by_stage[stage] for stage in unchanged_stages}

        with self.integration.bind_request(context):
            with self.assertRaises(ProviderStageRetryPendingError) as captured:
                self.integration.run_reader(
                    request,
                    custody,
                    accepted_state_sha256=_sha("reader-accepted-state"),
                    authority_binding={
                        "candidate_id": custody.candidate_id,
                        "candidate_sha256": custody.candidate_sha256,
                        "reader_request_sha256": canonical_sha256(request),
                    },
                )
        chain_id = captured.exception.chain_id
        self.assertEqual(self.counts.by_stage[ProviderStage.READER], 1)
        self.assertEqual(
            {stage: self.counts.by_stage[stage] for stage in unchanged_stages},
            before,
        )
        frozen = self.service.store.load_input(chain_id)
        self.assertEqual(reader_validation_input_from_frozen_input(frozen), (request, custody))

        status_before_restart = self.integration.status(chain_id)
        self.assertEqual(status_before_restart["status"]["state"], "eligible")
        calls_before_restart = dict(self.counts.by_stage)
        self._restart_runtime()
        self.assertEqual(self.integration.status(chain_id), status_before_restart)
        self.assertEqual(self.counts.by_stage, calls_before_restart)

        action = backend_action_from_envelope(status_before_restart)
        projection = self.integration.execute_action(action)
        self.assertTrue(projection.result_ready)
        self.assertEqual(projection.envelope["status"]["state"], "succeeded")
        self.assertEqual(
            projection.envelope["status"]["provider_operations_observed_total"],
            2,
        )
        self.assertEqual(self.counts.by_stage[ProviderStage.READER], 2)
        self.assertEqual(
            {stage: self.counts.by_stage[stage] for stage in unchanged_stages},
            before,
        )
        self.assertEqual(self.integration.reconcile_reader(chain_id), expected)
        self.assertEqual(
            deserialize_reader_validation_result(self.service.store.load_result(chain_id)),
            expected,
        )
        self.assertEqual(self.counts.by_stage[ProviderStage.READER], 2)

    def test_reader_prepared_restart_requires_exact_manual_resume(self) -> None:
        request, custody, expected = _reader_validation()
        self.factories[ProviderStage.READER].outcomes = {
            1: _success(
                "reader:prepared",
                serialize_reader_validation_result(expected),
                disposition=reader_validation_disposition(expected),
            )
        }
        context = _context()
        self._freeze(context)
        with self.integration.bind_request(context):
            prepared = self.integration.prepare_reader(
                request,
                custody,
                accepted_state_sha256=_sha("reader-accepted-state"),
                authority_binding={
                    "candidate_id": custody.candidate_id,
                    "candidate_sha256": custody.candidate_sha256,
                    "reader_request_sha256": canonical_sha256(request),
                },
            )
        chain_id = prepared.scope.identity.chain_id
        self.assertEqual(self.counts.by_stage[ProviderStage.READER], 0)
        self._restart_runtime()
        envelope = self.integration.status(chain_id)
        self.assertEqual(envelope["status"]["state"], "in_progress")
        self.assertEqual(envelope["status"]["available_actions"], ["resume_prepared"])
        resumed = self.integration.execute_action(backend_action_from_envelope(envelope))
        self.assertTrue(resumed.result_ready)
        self.assertEqual(self.integration.reconcile_reader(chain_id), expected)
        self.assertEqual(self.counts.by_stage[ProviderStage.READER], 1)

    def test_chain_input_and_scope_precede_secondary_custody_publication(self) -> None:
        context = _context()
        self._freeze(context)
        request = _planner_request()
        captured_scope: ProviderStageRetryOccurrenceScopeV1 | None = None

        def crash_before_occurrence_publication(
            *,
            scope: ProviderStageRetryOccurrenceScopeV1,
            context_sha256: str,
        ) -> None:
            nonlocal captured_scope
            del context_sha256
            captured_scope = scope
            raise OSError("simulated crash before secondary custody publication")

        with patch.object(
            self.custody,
            "bind_occurrence",
            side_effect=crash_before_occurrence_publication,
        ):
            with self.integration.bind_request(context):
                with self.assertRaisesRegex(OSError, "secondary custody"):
                    self.integration.run_planner(
                        request,
                        accepted_state_sha256=_sha("accepted-state"),
                        authority_binding={"accepted_head_sha256": None},
                    )

        self.assertIsNotNone(captured_scope)
        assert captured_scope is not None
        chain_id = captured_scope.identity.chain_id
        chain = self.service.read_chain(chain_id)
        self.assertIs(chain.phase, ProviderStageRetryPhase.INPUT_FROZEN)
        self.assertEqual(self.service.scope_for_chain(chain_id), captured_scope)
        self.assertEqual(
            planner_request_from_frozen_input(self.service.store.load_input(chain_id)),
            request,
        )
        self.assertEqual(self.counts.by_stage[ProviderStage.PLANNER], 0)
        with self.assertRaisesRegex(StateConflictError, "custody is unreadable"):
            self.custody.chain_context(chain_id)

        # Only this explicit continuation reaches start_initial/provider dispatch.
        with self.integration.bind_request(context):
            result = self.integration.run_planner(
                request,
                accepted_state_sha256=_sha("accepted-state"),
                authority_binding={"accepted_head_sha256": None},
            )
        self.assertEqual(result, PlannerTurnOutputV1(sequence=sequence(), provider_operations=1))
        self.assertEqual(self.counts.by_stage[ProviderStage.PLANNER], 1)

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
            with self.assertRaisesRegex(
                StateConflictError,
                "provider-stage retry authority changed",
            ):
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
            with self.assertRaisesRegex(
                StateConflictError,
                "provider-stage retry input changed",
            ):
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

    def test_semantic_output_invalid_exposes_only_one_manual_provider_retry(self) -> None:
        candidate = _candidate()
        accepted = _validation(candidate)
        factory = self.factories[ProviderStage.SEMANTIC_VALIDATOR]
        factory.outcomes = {
            1: ProviderStageClosedFailureV1(
                failure_class=ProviderStageFailureClass.PROVIDER_OUTPUT_INVALID,
                failure_evidence_sha256=_sha("semantic:output-invalid"),
                metrics=_metrics("semantic:output-invalid"),
            ),
            2: _success(
                "semantic:retry-success",
                serialize_semantic_validation_result(accepted),
                disposition=semantic_validation_disposition(accepted),
            ),
        }
        context = _context(
            world_id=candidate.world_id,
            branch_id=candidate.branch_id,
        )
        self._freeze(context)

        with self.integration.bind_request(context):
            with self.assertRaises(ProviderStageRetryPendingError) as pending:
                self.integration.run_semantic_validator(
                    accepted.request,
                    accepted.custody,
                    accepted_state_sha256=_sha("accepted-state"),
                    authority_binding={"candidate_id": accepted.custody.candidate_id},
                )

        envelope = pending.exception.envelope
        status = envelope["status"]
        self.assertEqual(status["state"], "eligible")
        self.assertEqual(status["failure_category"], "provider_output_invalid")
        self.assertEqual(status["available_actions"], ["provider_retry"])
        self.assertEqual(len(envelope["actions"]), 1)
        action = backend_action_from_envelope(envelope)
        self.assertEqual(action["action_kind"], "provider_retry")
        self.assertFalse(action["automatic"])
        self.assertTrue(action["provider_dispatch_authorized"])
        self.assertEqual(action["retry_action_ordinal"], 1)

        projection = self.integration.execute_action(action)
        self.assertTrue(projection.result_ready)
        self.assertEqual(projection.envelope["status"]["state"], "succeeded")
        self.assertEqual(projection.envelope["actions"], [])
        self.assertEqual(
            self.integration.reconcile_semantic_validator(projection.chain_id),
            accepted,
        )
        self.assertEqual(
            self.counts.by_stage[ProviderStage.SEMANTIC_VALIDATOR],
            2,
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

    def test_recorder_repair_is_one_fresh_successor_and_replays_original_route(self) -> None:
        (
            context,
            scene_store,
            review_root,
            views,
            coordinator,
            review_id,
            repair_action,
        ) = self._exhaust_accepted_recorder("repair-success")
        parent_chain_id = repair_action["chain_id"]
        parent_before = self.service.read_chain(parent_chain_id)
        self._set_recorder_success(parent_chain_id, "repair-success:successor:1")

        projection = self.integration.execute_recording_repair(
            repair_action,
            continuation=coordinator,
        )
        self.assertTrue(projection.result_ready)
        self.assertNotEqual(projection.chain_id, parent_chain_id)
        self.assertEqual(projection.envelope["status"]["state"], "succeeded")
        successor = self.service.read_chain(projection.chain_id)
        self.assertEqual(len(successor.attempts), 1)
        self.assertEqual(successor.retries_consumed, 0)
        self.assertEqual(len(parent_before.attempts), 3)
        self.assertEqual(parent_before.retries_consumed, 2)
        self.assertEqual(self.service.read_chain(parent_chain_id), parent_before)
        self.assertEqual(self.counts.by_stage[ProviderStage.RECORDER], 4)
        self.assertFalse(self.integration.recording_repair_action_allowed(projection.chain_id))

        self._restart_runtime()
        restarted = LeanPiSceneCoordinator(
            store=scene_store,
            planner=FakePlanner(),
            writer_views=views,
            pi=cast(PiSceneAdapter, FakePi()),
            session_root=review_root,
            ordinary_stage_retry=self.integration,
        )
        replay = self.integration.execute_recording_repair(
            repair_action,
            continuation=restarted,
        )
        self.assertEqual(replay.chain_id, projection.chain_id)
        self.assertEqual(replay.envelope, projection.envelope)
        self.assertEqual(self.counts.by_stage[ProviderStage.RECORDER], 4)

        completion = {
            "id": "completion-repair-success",
            "choices": [{"message": {"role": "assistant", "content": "Accepted."}}],
        }

        class _Continuation:
            def durable_result_for_request(self, **kwargs: Any) -> Any:
                return restarted.durable_result_for_request(**kwargs)

            def repair_recording(self, requested_review_id: str) -> LeanDecisionResultV1:
                return restarted.repair_recording(requested_review_id)

            def resume_original_request(
                self,
                *,
                normalized_request: Mapping[str, Any],
                turn_input: LeanSceneTurnInputV1,
            ) -> dict[str, Any]:
                self_outer.assertEqual(normalized_request, _payload())
                self_outer.assertEqual(
                    canonical_sha256(turn_input), canonical_sha256(context.turn_input)
                )
                return completion

        self_outer = self
        response = self.integration.resume_succeeded_chain(
            projection.chain_id,
            continuation=_Continuation(),
        )
        self.assertEqual(response, completion)
        repaired_review = restarted.get_review(review_id)
        self.assertIsNotNone(repaired_review.recording_attempt)
        assert repaired_review.recording_attempt is not None
        self.assertIs(
            repaired_review.recording_attempt.status,
            RecordingStatus.COMPLETE,
            repaired_review.recording_attempt,
        )
        self.assertEqual(self.counts.by_stage[ProviderStage.RECORDER], 4)

    def test_review_recorder_authority_allows_one_initial_start_when_no_chain_exists(
        self,
    ) -> None:
        writer_factory = self.factories[ProviderStage.WRITER]
        writer_factory.initial_outcome_from_input = lambda exact_input: _success(
            "recorder-authority-writer",
            serialize_pi_result(
                _pi_result(
                    writer_invocation_from_frozen_input(exact_input),
                    "recorder-authority-writer",
                )
            ),
        )
        context = _context()
        self._freeze(context)
        coordinator = LeanPiSceneCoordinator(
            store=LeanSceneStore(self.root / "recorder-authority-world"),
            planner=FakePlanner(),
            writer_views=WriterViewMaterializer(self.root / "recorder-authority-views"),
            pi=cast(PiSceneAdapter, FakePi()),
            session_root=self.root / "recorder-authority-reviews",
            ordinary_stage_retry=self.integration,
        )
        with self.integration.bind_request(context):
            review = coordinator.start_ordinary(context.turn_input)
            self.integration.bind_review_request(review)
            accepted = coordinator.accept(
                review.review_id,
                dispatch_recorder=False,
            ).review
        assert accepted.accepted_receipt is not None
        no_chain = self.integration.recorder_review_resolution(
            review_id=review.review_id,
            accepted=accepted.accepted_receipt,
        )
        self.assertEqual(no_chain.kind, "initial_start")
        self.assertEqual(self.counts.by_stage[ProviderStage.RECORDER], 0)

    def test_review_recorder_authority_exposes_only_the_parent_bound_repair(self) -> None:
        (
            _,
            _,
            _,
            _,
            exhausted_coordinator,
            exhausted_review_id,
            repair_action,
        ) = self._exhaust_accepted_recorder("recorder-authority-exhausted")
        exhausted = exhausted_coordinator.get_review(exhausted_review_id)
        assert exhausted.accepted_receipt is not None
        repair = self.integration.recorder_review_resolution(
            review_id=exhausted_review_id,
            accepted=exhausted.accepted_receipt,
        )
        self.assertEqual(repair.kind, "repair_required")
        self.assertEqual(repair.repair_action, repair_action)
        successor = self.integration.execute_recording_repair(
            repair_action,
            continuation=exhausted_coordinator,
        )
        self.assertFalse(successor.result_ready)
        after_successor = self.integration.recorder_review_resolution(
            review_id=exhausted_review_id,
            accepted=exhausted.accepted_receipt,
        )
        self.assertEqual(after_successor.kind, "chain_active")
        self.assertEqual(after_successor.current_chain_id, successor.chain_id)
        self.assertIsNone(after_successor.repair_action)

    def test_active_recorder_chain_never_authorizes_an_early_review_repair(self) -> None:
        self.factories[ProviderStage.WRITER].initial_outcome_from_input = lambda exact_input: (
            _success(
                "recorder-active-writer",
                serialize_pi_result(
                    _pi_result(
                        writer_invocation_from_frozen_input(exact_input),
                        "recorder-active-writer",
                    )
                ),
            )
        )
        self.factories[ProviderStage.RECORDER].initial_outcome_from_input = lambda _exact_input: (
            _failure("recorder-active:first")
        )
        context = _context()
        self._freeze(context)
        coordinator = LeanPiSceneCoordinator(
            store=LeanSceneStore(self.root / "recorder-active-world"),
            planner=FakePlanner(),
            writer_views=WriterViewMaterializer(self.root / "recorder-active-views"),
            pi=cast(PiSceneAdapter, FakePi()),
            session_root=self.root / "recorder-active-reviews",
            ordinary_stage_retry=self.integration,
        )
        with self.integration.bind_request(context):
            review = coordinator.start_ordinary(context.turn_input)
            self.integration.bind_review_request(review)
            with self.assertRaises(ProviderStageRetryPendingError):
                coordinator.accept(review.review_id)
        accepted = coordinator.get_review(review.review_id)
        assert accepted.accepted_receipt is not None
        resolution = self.integration.recorder_review_resolution(
            review_id=review.review_id,
            accepted=accepted.accepted_receipt,
        )
        self.assertEqual(resolution.kind, "chain_active")
        self.assertIsNone(resolution.repair_action)
        self.assertEqual(self.counts.by_stage[ProviderStage.RECORDER], 1)

    def test_recorder_repair_crash_before_initial_dispatch_reuses_parent_action(self) -> None:
        (
            _,
            scene_store,
            review_root,
            views,
            coordinator,
            _,
            repair_action,
        ) = self._exhaust_accepted_recorder("repair-crash")
        parent_chain_id = repair_action["chain_id"]
        self._set_recorder_success(parent_chain_id, "repair-crash:successor:1")

        with patch.object(self.service, "start_initial", side_effect=OSError("simulated stop")):
            with self.assertRaisesRegex(OSError, "simulated stop"):
                self.integration.execute_recording_repair(
                    repair_action,
                    continuation=coordinator,
                )
        authority = self.custody.load_recorder_repair_successor(parent_chain_id)
        self.assertEqual(
            self.service.read_chain(authority.successor_chain_id).phase,
            ProviderStageRetryPhase.INPUT_FROZEN,
        )
        self.assertEqual(
            self.integration.latest_chain_for_chain(parent_chain_id).chain_id,
            parent_chain_id,
        )
        self.assertEqual(self.counts.by_stage[ProviderStage.RECORDER], 3)

        self._restart_runtime()
        restarted = LeanPiSceneCoordinator(
            store=scene_store,
            planner=FakePlanner(),
            writer_views=views,
            pi=cast(PiSceneAdapter, FakePi()),
            session_root=review_root,
            ordinary_stage_retry=self.integration,
        )
        resumed = self.integration.execute_recording_repair(
            repair_action,
            continuation=restarted,
        )
        self.assertEqual(resumed.chain_id, authority.successor_chain_id)
        self.assertTrue(resumed.result_ready)
        self.assertEqual(self.counts.by_stage[ProviderStage.RECORDER], 4)
        duplicate = self.integration.execute_recording_repair(
            repair_action,
            continuation=restarted,
        )
        self.assertEqual(duplicate.envelope, resumed.envelope)
        self.assertEqual(self.counts.by_stage[ProviderStage.RECORDER], 4)

    def test_exhausted_recorder_repair_successor_is_read_only_and_cannot_recurse(self) -> None:
        (
            _,
            _,
            _,
            _,
            coordinator,
            _,
            repair_action,
        ) = self._exhaust_accepted_recorder("repair-exhausted")
        parent_chain_id = repair_action["chain_id"]
        successor_first = self.integration.execute_recording_repair(
            repair_action,
            continuation=coordinator,
        )
        self.assertFalse(successor_first.result_ready)
        self.assertNotEqual(successor_first.chain_id, parent_chain_id)
        successor_second = self.integration.execute_action(
            backend_action_from_envelope(successor_first.envelope)
        )
        successor_terminal = self.integration.execute_action(
            backend_action_from_envelope(successor_second.envelope)
        )
        self.assertEqual(
            successor_terminal.envelope["status"]["state"],
            "recording_repair_required",
        )
        self.assertEqual(successor_terminal.envelope["actions"], [])
        self.assertEqual(self.integration.status(successor_first.chain_id)["actions"], [])
        self.assertEqual(self.counts.by_stage[ProviderStage.RECORDER], 6)

        unsafe = self.service.canonical_status(
            chain_id=successor_first.chain_id,
            recording_repair_action_allowed=True,
        )
        recursive_action = backend_action_from_envelope(unsafe)
        self.assertEqual(recursive_action["action_kind"], "repair_recording")
        with self.assertRaisesRegex(StateConflictError, "cannot recurse"):
            self.integration.execute_recording_repair(
                recursive_action,
                continuation=coordinator,
            )
        replay = self.integration.execute_recording_repair(
            repair_action,
            continuation=coordinator,
        )
        self.assertEqual(replay.chain_id, successor_first.chain_id)
        self.assertEqual(replay.envelope["actions"], [])
        self.assertEqual(self.counts.by_stage[ProviderStage.RECORDER], 6)

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
        recovered_request, recovered_context = self.integration.pending_request_for_chain(chain_id)
        self.assertEqual(
            canonical_sha256(recovered_context.turn_input),
            canonical_sha256(context.turn_input),
        )
        completion = {
            "id": "completion-recorder-restart",
            "choices": [{"message": {"role": "assistant", "content": "Recovered."}}],
        }

        class _Continuation:
            def durable_result_for_request(self, **kwargs: Any) -> Any:
                return restarted_coordinator.durable_result_for_request(**kwargs)

            def repair_recording(self, review_id: str) -> LeanDecisionResultV1:
                return restarted_coordinator.repair_recording(review_id)

            def resume_original_request(
                self,
                *,
                normalized_request: Mapping[str, Any],
                turn_input: LeanSceneTurnInputV1,
            ) -> dict[str, Any]:
                self_outer.assertEqual(normalized_request, recovered_request)
                self_outer.assertEqual(
                    canonical_sha256(turn_input),
                    canonical_sha256(context.turn_input),
                )
                return completion

        self_outer = self
        recovered = self.integration.resume_succeeded_chain(
            chain_id,
            continuation=_Continuation(),
        )
        self.assertEqual(recovered, completion)
        decision = restarted_coordinator.get_review(review.review_id)
        self.assertIsNotNone(decision.recording_attempt)
        assert decision.recording_attempt is not None
        self.assertIs(
            decision.recording_attempt.status,
            RecordingStatus.COMPLETE,
            decision.recording_attempt,
        )
        self.assertEqual(
            self.counts.by_stage[ProviderStage.RECORDER],
            calls_after_retry,
        )

    def test_review_request_restart_rebinds_replan_and_regenerate_ordinals(self) -> None:
        with self.assertRaises(FileNotFoundError):
            self.integration.pending_request_for_review("review-missing")
        self.factories[ProviderStage.WRITER].initial_outcome_from_input = lambda exact_input: (
            _success(
                "review-custody-writer",
                serialize_pi_result(
                    _pi_result(
                        writer_invocation_from_frozen_input(exact_input),
                        "review-custody-writer",
                    )
                ),
            )
        )
        context = _context()
        self._freeze(context)
        scene_store = LeanSceneStore(self.root / "review-custody-world")
        review_root = self.root / "review-custody-state"
        views = WriterViewMaterializer(self.root / "review-custody-views")
        coordinator = LeanPiSceneCoordinator(
            store=scene_store,
            planner=FakePlanner(),
            writer_views=views,
            pi=cast(PiSceneAdapter, FakePi()),
            session_root=review_root,
            ordinary_stage_retry=self.integration,
        )
        with self.integration.bind_request(context):
            initial = coordinator.start_ordinary(context.turn_input)
            initial_identity = self.integration.bind_review_request(initial)
        self.assertEqual(initial_identity.request_id, context.binding.request_id)

        self._restart_runtime()
        coordinator = LeanPiSceneCoordinator(
            store=scene_store,
            planner=FakePlanner(),
            writer_views=views,
            pi=cast(PiSceneAdapter, FakePi()),
            session_root=review_root,
            ordinary_stage_retry=self.integration,
        )
        payload, recovered_context, initial_counts = self.integration.pending_request_for_review(
            initial.review_id
        )
        self.assertEqual(payload, _payload())
        self.assertEqual(
            initial_counts,
            {
                ProviderStage.PLANNER: 1,
                ProviderStage.WRITER: 1,
                ProviderStage.SEMANTIC_VALIDATOR: 0,
                ProviderStage.READER: 0,
                ProviderStage.RECORDER: 0,
            },
        )
        with self.integration.bind_request(
            recovered_context,
            prior_stage_occurrences=initial_counts,
        ):
            replanned = coordinator.replan(initial.review_id, feedback="Try another plan.")
            self.assertIsNotNone(replanned.successor)
            assert replanned.successor is not None
            replanned_identity = self.integration.bind_review_request(replanned.successor)
        active = self.integration.retire_review_request(
            initial.review_id,
            terminal_evidence_sha256=text_sha256("initial-review-replanned"),
        )
        self.assertEqual(active.disposition, "active")
        self.assertTrue(active.branch_barrier_active)

        _, replanned_context, replanned_counts = self.integration.pending_request_for_review(
            replanned_identity.review_id
        )
        self.assertEqual(replanned_counts[ProviderStage.PLANNER], 2)
        self.assertEqual(replanned_counts[ProviderStage.WRITER], 2)
        with self.integration.bind_request(
            replanned_context,
            prior_stage_occurrences=replanned_counts,
        ):
            regenerated = coordinator.regenerate(replanned_identity.review_id)
            self.assertIsNotNone(regenerated.successor)
            assert regenerated.successor is not None
            regenerated_identity = self.integration.bind_review_request(regenerated.successor)
        still_active = self.integration.retire_review_request(
            replanned_identity.review_id,
            terminal_evidence_sha256=text_sha256("replanned-review-regenerated"),
        )
        self.assertEqual(still_active.disposition, "active")

        other_payload = {
            **_payload(),
            "messages": [{"role": "user", "content": "Request B must preflight."}],
        }
        other_context = _context(payload=other_payload)
        with self.assertRaisesRegex(StateConflictError, "unresolved request barrier"):
            self.integration.freeze_pending_request(
                normalized_request=other_payload,
                context=other_context,
            )

        _, final_context, final_counts = self.integration.pending_request_for_review(
            regenerated_identity.review_id
        )
        self.assertEqual(final_counts[ProviderStage.PLANNER], 2)
        self.assertEqual(final_counts[ProviderStage.WRITER], 3)
        with self.integration.bind_request(
            final_context,
            prior_stage_occurrences=final_counts,
        ):
            coordinator.decline(regenerated_identity.review_id)
        completed = self.integration.retire_review_request(
            regenerated_identity.review_id,
            terminal_evidence_sha256=text_sha256("regenerated-review-declined"),
        )
        self.assertEqual(completed.disposition, "completed")
        self.assertFalse(completed.branch_barrier_active)
        replayed_retirement = self.integration.retire_review_request(
            initial.review_id,
            terminal_evidence_sha256=text_sha256("initial-review-replanned"),
        )
        self.assertEqual(replayed_retirement, completed)

    def test_pre_reader_v1_review_and_action_counts_upgrade_to_reader_zero(self) -> None:
        self.factories[ProviderStage.WRITER].initial_outcome_from_input = lambda exact_input: (
            _success(
                "legacy-counts-writer",
                serialize_pi_result(
                    _pi_result(
                        writer_invocation_from_frozen_input(exact_input),
                        "legacy-counts-writer",
                    )
                ),
            )
        )
        context = _context()
        self._freeze(context)
        coordinator = LeanPiSceneCoordinator(
            store=LeanSceneStore(self.root / "legacy-counts-world"),
            planner=FakePlanner(),
            writer_views=WriterViewMaterializer(self.root / "legacy-counts-views"),
            pi=cast(PiSceneAdapter, FakePi()),
            session_root=self.root / "legacy-counts-reviews",
            ordinary_stage_retry=self.integration,
        )
        exact_action = {"action": "regenerate", "force_rehydrate": False}
        with self.integration.bind_request(context):
            review = coordinator.start_ordinary(context.turn_input)
            self.integration.bind_review_request(review)

        review_path = self.custody._review_path(review.review_id)
        review_payload = json.loads(review_path.read_text(encoding="utf-8"))
        legacy_counts = dict(review_payload["stage_occurrence_counts"])
        legacy_counts.pop(ProviderStage.READER.value)
        review_payload["stage_occurrence_counts"] = legacy_counts
        review_payload["stage_occurrence_counts_sha256"] = canonical_sha256(legacy_counts)
        review_unsigned = {
            key: value for key, value in review_payload.items() if key != "review_request_sha256"
        }
        review_payload["review_request_sha256"] = canonical_sha256(review_unsigned)
        review_path.write_text(
            json.dumps(review_payload, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )

        self._restart_runtime()
        _, recovered_context, recovered_counts = self.integration.pending_request_for_review(
            review.review_id
        )
        self.assertEqual(recovered_counts[ProviderStage.READER], 0)
        with self.integration.bind_request(
            recovered_context,
            prior_stage_occurrences=recovered_counts,
        ):
            rebound = self.integration.bind_review_request(review)
        self.assertEqual(rebound.review_id, review.review_id)

        with self.integration.bind_review_action(
            review_id=review.review_id,
            normalized_action=exact_action,
        ) as current_action:
            pass
        current_path = self.custody._review_action_path(current_action.action_id)
        action_payload = json.loads(current_path.read_text(encoding="utf-8"))
        action_payload["stage_occurrence_counts"] = legacy_counts
        legacy_counts_sha256 = canonical_sha256(legacy_counts)
        action_payload["stage_occurrence_counts_sha256"] = legacy_counts_sha256
        legacy_action_id = "review-action-" + domain_sha256(
            "cera.ordinary_stage_retry_review_action_identity.v1",
            {
                "review_id": review.review_id,
                "request_id": rebound.request_id,
                "context_sha256": rebound.context_sha256,
                "normalized_action_sha256": canonical_sha256(exact_action),
                "stage_occurrence_counts_sha256": legacy_counts_sha256,
            },
        )
        action_payload["action_id"] = legacy_action_id
        action_unsigned = {
            key: value for key, value in action_payload.items() if key != "review_action_sha256"
        }
        action_payload["review_action_sha256"] = canonical_sha256(action_unsigned)
        legacy_action_path = self.custody._review_action_path(legacy_action_id)
        legacy_action_path.write_text(
            json.dumps(action_payload, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        current_path.unlink()

        self._restart_runtime()
        with self.integration.bind_review_action(
            review_id=review.review_id,
            normalized_action=exact_action,
        ) as upgraded_action:
            self.assertEqual(upgraded_action.action_id, legacy_action_id)
            self.assertEqual(
                upgraded_action.stage_occurrence_counts_sha256,
                canonical_sha256(
                    {
                        **legacy_counts,
                        ProviderStage.READER.value: 0,
                    }
                ),
            )

    def test_replan_planner_retry_resumes_exact_action_without_chat_replay(self) -> None:
        self.factories[ProviderStage.WRITER].initial_outcome_from_input = lambda exact_input: (
            _success(
                "action-replan-writer",
                serialize_pi_result(
                    _pi_result(
                        writer_invocation_from_frozen_input(exact_input),
                        "action-replan-writer",
                    )
                ),
            )
        )
        context = _context()
        self._freeze(context)
        scene_store = LeanSceneStore(self.root / "action-replan-world")
        review_root = self.root / "action-replan-reviews"
        views = WriterViewMaterializer(self.root / "action-replan-views")
        coordinator = LeanPiSceneCoordinator(
            store=scene_store,
            planner=FakePlanner(),
            writer_views=views,
            pi=cast(PiSceneAdapter, FakePi()),
            session_root=review_root,
            ordinary_stage_retry=self.integration,
        )
        with self.integration.bind_request(context):
            review = coordinator.start_ordinary(context.turn_input)
            self.integration.bind_review_request(review)

        planner_result = PlannerTurnOutputV1(
            sequence=sequence("action_replan_retry"),
            provider_operations=1,
        )
        self.factories[ProviderStage.PLANNER].initial_outcomes_by_ordinal[2] = _failure(
            "action-replan-planner:1"
        )
        self.factories[ProviderStage.PLANNER].outcomes[2] = _success(
            "action-replan-planner:2",
            serialize_planner_result(planner_result),
        )
        feedback_sentinel = "ACTION-FEEDBACK-MUST-STAY-PROTECTED-7f9e"
        exact_action = {"action": "replan", "feedback": feedback_sentinel}
        self.assertIsNone(
            self.integration.reconcile_finalized_review_action_response_for_review_optional(
                review.review_id
            )
        )
        with self.integration.bind_review_action(
            review_id=review.review_id,
            normalized_action=exact_action,
        ) as action_identity:
            with self.assertRaises(ProviderStageRetryPendingError) as captured:
                coordinator.replan(
                    review.review_id,
                    feedback=feedback_sentinel,
                    allow_replay=True,
                )
        chain_id = captured.exception.chain_id
        self.assertEqual(
            self.integration.review_action_for_chain(chain_id),
            action_identity,
        )
        action_chain = self.integration.review_action_chain_identity(chain_id)
        self.assertIsNotNone(action_chain)
        assert action_chain is not None
        self.assertEqual(
            action_chain.request_sha256,
            self.service.scope_for_chain(chain_id).request_sha256,
        )
        sentinel = feedback_sentinel.encode()
        action_path = (
            self.root
            / "ordinary-protected-custody"
            / "review_actions"
            / f"{action_identity.action_id.removeprefix('review-action-')}.json"
        )
        self.assertIn(sentinel, action_path.read_bytes())
        self.assertNotIn(sentinel, canonical_bytes(captured.exception.envelope))
        private_free_paths = list(self.root.glob("authority.sqlite3*"))
        private_free_paths.extend(
            (self.root / "ordinary-protected-custody" / "review_action_chains").glob("*.json")
        )
        for path in private_free_paths:
            if path.exists():
                self.assertNotIn(sentinel, path.read_bytes(), path)
        planner_calls_before_retry = self.counts.by_stage[ProviderStage.PLANNER]

        self._restart_runtime()
        coordinator = LeanPiSceneCoordinator(
            store=scene_store,
            planner=FakePlanner(),
            writer_views=views,
            pi=cast(PiSceneAdapter, FakePi()),
            session_root=review_root,
            ordinary_stage_retry=self.integration,
        )
        with self.assertRaisesRegex(StateConflictError, "different protected action"):
            with self.integration.bind_review_action(
                review_id=review.review_id,
                normalized_action={"action": "decline"},
            ):
                pass
        action = backend_action_from_envelope(captured.exception.envelope)
        self.integration.execute_action(action)
        self.assertEqual(
            self.counts.by_stage[ProviderStage.PLANNER],
            planner_calls_before_retry + 1,
        )

        integration = self.integration

        class _Continuation:
            chat_replays = 0
            action_replays = 0

            def resume_original_request(self, **_kwargs: Any) -> object:
                self.chat_replays += 1
                raise AssertionError("review Retry replayed the original chat")

            def resume_review_action(
                self,
                *,
                action_identity: Any,
                review_id: str,
                normalized_action: Mapping[str, Any],
            ) -> LeanDecisionResultV1:
                self.action_replays += 1
                if action_identity != action_identity_outer or normalized_action != exact_action:
                    raise AssertionError("review Retry changed the exact action")
                result = coordinator.replan(
                    review_id,
                    feedback=cast(str, normalized_action["feedback"]),
                    allow_replay=True,
                )
                assert result.successor is not None
                integration.bind_review_request(result.successor)
                return result

        action_identity_outer = action_identity
        continuation = _Continuation()
        decision = self.integration.resume_succeeded_chain(
            chain_id,
            continuation=continuation,
        )
        self.assertIsInstance(decision, LeanDecisionResultV1)
        self.assertEqual(continuation.action_replays, 1)
        self.assertEqual(continuation.chat_replays, 0)
        self.assertEqual(
            self.counts.by_stage[ProviderStage.PLANNER],
            planner_calls_before_retry + 1,
        )
        latest = self.integration.latest_chain_for_chain(chain_id)
        self.assertEqual(
            self.integration.review_action_for_chain(latest.chain_id),
            action_identity,
        )

        response = {
            "schema_version": "cera.pi_scene.review_decision.v1",
            "review_id": review.review_id,
            "creator_action": "replan",
        }
        receipt = self.integration.bind_review_action_response(
            action_id=action_identity.action_id,
            response=response,
        )
        crash_window_receipt = self.integration.retire_review_request(
            review.review_id,
            terminal_evidence_sha256=receipt.response_sha256,
        )
        self.assertEqual(crash_window_receipt.disposition, "active")
        self._restart_runtime()
        finalized, request_receipt = self.integration.finalize_review_action(
            action_identity.action_id
        )
        self.assertEqual(finalized, action_identity)
        self.assertEqual(request_receipt.disposition, "active")
        # A successor stage may be prepared by the action, then reach its
        # first durable chain binding only after the action is tombstoned.
        # Recreate that crash window and require the content-free tombstone
        # identity to remain sufficient for the exact chain binding.
        action_chain_path = self.custody._review_action_chain_path(chain_id)
        action_chain_path.unlink()
        chain_context = self.custody.chain_context(chain_id)
        chain_scope = self.service.scope_for_chain(chain_id)
        self.assertEqual(
            self.custody.bind_review_action_chain(
                chain_id=chain_id,
                action_id=action_identity.action_id,
                request_id=chain_context.request_id,
                request_sha256=chain_scope.request_sha256,
                context_sha256=chain_context.context_sha256,
            ),
            action_identity,
        )
        self.assertEqual(
            self.integration.finalize_review_action(action_identity.action_id),
            (finalized, request_receipt),
        )
        self.assertNotIn(sentinel, action_path.read_bytes())
        self._restart_runtime()
        self.assertEqual(
            self.integration.load_review_action_response_for_chain(chain_id),
            (response, receipt),
        )
        self.assertEqual(
            self.integration.reconcile_finalized_review_action_response_for_review_optional(
                review.review_id
            ),
            (response, receipt),
        )
        response_path = self.custody._review_action_response_path(action_identity.action_id)
        exact_response_bytes = response_path.read_bytes()
        response_path.write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(StateConflictError, "response is unavailable"):
            self.integration.reconcile_finalized_review_action_response_for_review_optional(
                review.review_id
            )
        response_path.write_bytes(exact_response_bytes)
        response_path.unlink()
        with self.assertRaisesRegex(StateConflictError, "response is unavailable"):
            self.integration.reconcile_finalized_review_action_response_for_review_optional(
                review.review_id
            )
        with self.assertRaisesRegex(StateConflictError, "retired"):
            with self.integration.bind_review_action(
                review_id=review.review_id,
                normalized_action=exact_action,
            ):
                pass

    def test_regenerate_writer_retry_preserves_prior_planner_and_action_identity(self) -> None:
        self.factories[ProviderStage.WRITER].initial_outcome_from_input = lambda exact_input: (
            _success(
                "action-regenerate-writer",
                serialize_pi_result(
                    _pi_result(
                        writer_invocation_from_frozen_input(exact_input),
                        "action-regenerate-writer",
                    )
                ),
            )
        )
        context = _context()
        self._freeze(context)
        scene_store = LeanSceneStore(self.root / "action-regenerate-world")
        review_root = self.root / "action-regenerate-reviews"
        views = WriterViewMaterializer(self.root / "action-regenerate-views")
        coordinator = LeanPiSceneCoordinator(
            store=scene_store,
            planner=FakePlanner(),
            writer_views=views,
            pi=cast(PiSceneAdapter, FakePi()),
            session_root=review_root,
            ordinary_stage_retry=self.integration,
        )
        with self.integration.bind_request(context):
            review = coordinator.start_ordinary(context.turn_input)
            self.integration.bind_review_request(review)
        self.factories[ProviderStage.WRITER].initial_outcome_from_input = lambda _exact_input: (
            _failure("action-regenerate-writer:1")
        )
        exact_action = {"action": "regenerate", "force_rehydrate": False}
        planner_calls_before_action = self.counts.by_stage[ProviderStage.PLANNER]
        with self.integration.bind_review_action(
            review_id=review.review_id,
            normalized_action=exact_action,
        ) as action_identity:
            with self.assertRaises(ProviderStageRetryPendingError) as captured:
                coordinator.regenerate(
                    review.review_id,
                    force_rehydrate=False,
                    allow_replay=True,
                )
        chain_id = captured.exception.chain_id
        planner_calls_after_pending = self.counts.by_stage[ProviderStage.PLANNER]
        self.assertEqual(planner_calls_after_pending, planner_calls_before_action)
        action = backend_action_from_envelope(captured.exception.envelope)
        # The retry result must be generated from the exact frozen Writer input.
        frozen = self.service.store.load_input(chain_id)
        invocation = writer_invocation_from_frozen_input(frozen)
        self.factories[ProviderStage.WRITER].outcomes[2] = _success(
            "action-regenerate-writer:2",
            serialize_pi_result(_pi_result(invocation, "action-regenerate-writer:2")),
        )
        self.integration.execute_action(action)

        integration = self.integration

        class _Continuation:
            def resume_review_action(
                self,
                *,
                action_identity: Any,
                review_id: str,
                normalized_action: Mapping[str, Any],
            ) -> LeanDecisionResultV1:
                if action_identity != action_identity_outer or normalized_action != exact_action:
                    raise AssertionError("Regenerate action changed")
                result = coordinator.regenerate(
                    review_id,
                    force_rehydrate=cast(bool, normalized_action["force_rehydrate"]),
                    allow_replay=True,
                )
                assert result.successor is not None
                integration.bind_review_request(result.successor)
                return result

        action_identity_outer = action_identity
        decision = self.integration.resume_succeeded_chain(
            chain_id,
            continuation=_Continuation(),
        )
        self.assertIsInstance(decision, LeanDecisionResultV1)
        self.assertEqual(
            self.counts.by_stage[ProviderStage.PLANNER],
            planner_calls_after_pending,
        )
        self.assertEqual(
            self.integration.review_action_for_chain(chain_id),
            action_identity,
        )

    def test_review_action_restart_and_request_review_count_drift_fail_closed(self) -> None:
        self.factories[ProviderStage.WRITER].initial_outcome_from_input = lambda exact_input: (
            _success(
                "action-drift-writer",
                serialize_pi_result(
                    _pi_result(
                        writer_invocation_from_frozen_input(exact_input),
                        "action-drift-writer",
                    )
                ),
            )
        )
        context = _context()
        self._freeze(context)
        coordinator = LeanPiSceneCoordinator(
            store=LeanSceneStore(self.root / "action-drift-world"),
            planner=FakePlanner(),
            writer_views=WriterViewMaterializer(self.root / "action-drift-views"),
            pi=cast(PiSceneAdapter, FakePi()),
            session_root=self.root / "action-drift-reviews",
            ordinary_stage_retry=self.integration,
        )
        with self.integration.bind_request(context):
            review = coordinator.start_ordinary(context.turn_input)
            self.integration.bind_review_request(review)
        exact_action = {
            "action": "replan",
            "feedback": "DRIFT-SENTINEL-PROTECTED-f83a",
        }
        with self.integration.bind_review_action(
            review_id=review.review_id,
            normalized_action=exact_action,
        ) as identity:
            pass
        calls_before_restart = dict(self.counts.by_stage)
        self._restart_runtime()
        with self.integration.bind_review_action(
            review_id=review.review_id,
            normalized_action=exact_action,
        ) as restarted_identity:
            self.assertEqual(restarted_identity, identity)
        self.assertEqual(self.counts.by_stage, calls_before_restart)

        action_path = (
            self.root
            / "ordinary-protected-custody"
            / "review_actions"
            / f"{identity.action_id.removeprefix('review-action-')}.json"
        )
        original = action_path.read_bytes()
        for field_name, changed in (
            ("review_id", "review-drifted"),
            ("request_id", "request-" + "9" * 64),
            ("stage_occurrence_counts", None),
        ):
            payload = cast(dict[str, Any], json.loads(original))
            if field_name == "stage_occurrence_counts":
                counts = cast(dict[str, int], payload[field_name])
                counts[ProviderStage.PLANNER.value] += 1
            else:
                payload[field_name] = changed
            unsigned = {
                key: value for key, value in payload.items() if key != "review_action_sha256"
            }
            payload["review_action_sha256"] = canonical_sha256(unsigned)
            action_path.write_bytes(canonical_bytes(payload))
            try:
                with self.assertRaises(StateConflictError, msg=field_name):
                    self.custody.load_review_action(identity.action_id)
            finally:
                action_path.write_bytes(original)

    def test_review_action_chain_is_bound_before_cursor_crash_and_dispatch(self) -> None:
        self.factories[ProviderStage.WRITER].initial_outcome_from_input = lambda exact_input: (
            _success(
                "action-cursor-crash-writer",
                serialize_pi_result(
                    _pi_result(
                        writer_invocation_from_frozen_input(exact_input),
                        "action-cursor-crash-writer",
                    )
                ),
            )
        )
        context = _context()
        self._freeze(context)
        coordinator = LeanPiSceneCoordinator(
            store=LeanSceneStore(self.root / "action-cursor-crash-world"),
            planner=FakePlanner(),
            writer_views=WriterViewMaterializer(self.root / "action-cursor-crash-views"),
            pi=cast(PiSceneAdapter, FakePi()),
            session_root=self.root / "action-cursor-crash-reviews",
            ordinary_stage_retry=self.integration,
        )
        with self.integration.bind_request(context):
            review = coordinator.start_ordinary(context.turn_input)
            self.integration.bind_review_request(review)
        exact_action = {"action": "replan", "feedback": "Crash before cursor."}
        calls_before = dict(self.counts.by_stage)
        with self.integration.bind_review_action(
            review_id=review.review_id,
            normalized_action=exact_action,
        ) as action_identity:
            with patch.object(
                self.custody,
                "advance_latest_chain",
                side_effect=OSError("simulated cursor publication crash"),
            ):
                with self.assertRaisesRegex(OSError, "cursor publication crash"):
                    coordinator.replan(
                        review.review_id,
                        feedback="Crash before cursor.",
                        allow_replay=True,
                    )
        self.assertEqual(self.counts.by_stage, calls_before)
        mappings = list(
            (self.root / "ordinary-protected-custody" / "review_action_chains").glob("*.json")
        )
        self.assertGreaterEqual(len(mappings), 1)
        payload = cast(dict[str, Any], json.loads(mappings[-1].read_bytes()))
        chain_id = cast(str, payload["chain_id"])
        self.assertEqual(
            self.integration.review_action_for_chain(chain_id),
            action_identity,
        )
        self.assertGreater(len(self.service.store.load_input(chain_id)), 0)
        self.assertEqual(self.service.scope_for_chain(chain_id).identity.chain_id, chain_id)
        self._restart_runtime()
        self.assertEqual(
            self.integration.review_action_for_chain(chain_id),
            action_identity,
        )
        self.assertEqual(self.counts.by_stage, calls_before)

    def test_explicit_accept_recorder_uses_original_review_request_after_restart(self) -> None:
        self.factories[ProviderStage.WRITER].initial_outcome_from_input = lambda exact_input: (
            _success(
                "accept-context-writer",
                serialize_pi_result(
                    _pi_result(
                        writer_invocation_from_frozen_input(exact_input),
                        "accept-context-writer",
                    )
                ),
            )
        )

        def recorder_success(exact_input: bytes) -> ProviderStageSuccessfulResultV1:
            invocation = recorder_invocation_from_frozen_input(exact_input)
            output = json.dumps(
                {
                    "secondary_canon": [],
                    "resulting_public_state": (
                        "Hana has answered and the conversation remains open."
                    ),
                    "relationship_changes": [],
                    "knowledge_changes": [],
                    "durable_changes": [],
                    "unresolved_threads": ["Ted may respond."],
                },
                separators=(",", ":"),
            )
            base = _pi_result(invocation, "accept-context-recorder")
            result = replace(
                base,
                output_text=output,
                writer_receipt=replace(
                    base.writer_receipt,
                    output_sha256=text_sha256(output),
                ),
            )
            return _success(
                "accept-context-recorder",
                serialize_pi_result(result),
            )

        self.factories[ProviderStage.RECORDER].initial_outcome_from_input = recorder_success
        context = _context()
        self._freeze(context)
        scene_store = LeanSceneStore(self.root / "accept-context-world")
        review_root = self.root / "accept-context-review-state"
        views = WriterViewMaterializer(self.root / "accept-context-views")
        coordinator = LeanPiSceneCoordinator(
            store=scene_store,
            planner=FakePlanner(),
            writer_views=views,
            pi=cast(PiSceneAdapter, FakePi()),
            session_root=review_root,
            ordinary_stage_retry=self.integration,
        )
        with self.integration.bind_request(context):
            review = coordinator.start_ordinary(context.turn_input)
            self.integration.bind_review_request(review)

        self._restart_runtime()
        coordinator = LeanPiSceneCoordinator(
            store=scene_store,
            planner=FakePlanner(),
            writer_views=views,
            pi=cast(PiSceneAdapter, FakePi()),
            session_root=review_root,
            ordinary_stage_retry=self.integration,
        )
        _, recovered_context, counts = self.integration.pending_request_for_review(review.review_id)
        self.assertEqual(
            canonical_sha256(recovered_context.turn_input),
            canonical_sha256(context.turn_input),
        )
        with self.integration.bind_request(
            recovered_context,
            prior_stage_occurrences=counts,
        ):
            accepted = coordinator.accept(review.review_id)
        self.assertIsNotNone(accepted.review.recording_attempt)
        assert accepted.review.recording_attempt is not None
        self.assertIs(
            accepted.review.recording_attempt.status,
            RecordingStatus.COMPLETE,
        )
        completion = {
            "id": "completion-explicit-accept",
            "choices": [{"message": {"role": "assistant", "content": "Accepted."}}],
        }
        self.assertIsNone(
            self.integration.load_terminal_response_optional(context.binding.request_id)
        )
        terminal = self.integration.bind_terminal_response(
            request_id=context.binding.request_id,
            response=completion,
        )
        self.assertEqual(
            self.integration.load_terminal_response_optional(context.binding.request_id),
            (completion, terminal),
        )
        retired = self.integration.retire_review_request(
            review.review_id,
            terminal_evidence_sha256=terminal.response_sha256,
        )
        self.assertEqual(retired.disposition, "completed")
        self.assertIsNone(
            self.integration.branch_barrier(
                world_id=context.binding.world_id,
                branch_id=context.binding.branch_id,
            )
        )
        with self.assertRaisesRegex(StateConflictError, "mapping is retired"):
            self.integration.pending_request_for_review(review.review_id)

    def test_accept_recorder_retry_repairs_once_then_returns_exact_accept_decision(self) -> None:
        self.factories[ProviderStage.WRITER].initial_outcome_from_input = lambda exact_input: (
            _success(
                "accept-action-writer",
                serialize_pi_result(
                    _pi_result(
                        writer_invocation_from_frozen_input(exact_input),
                        "accept-action-writer",
                    )
                ),
            )
        )
        self.factories[ProviderStage.RECORDER].initial_outcome_from_input = lambda _exact_input: (
            _failure("accept-action-recorder:1")
        )
        context = _context()
        self._freeze(context)
        scene_store = LeanSceneStore(self.root / "accept-action-world")
        review_root = self.root / "accept-action-reviews"
        views = WriterViewMaterializer(self.root / "accept-action-views")
        coordinator = LeanPiSceneCoordinator(
            store=scene_store,
            planner=FakePlanner(),
            writer_views=views,
            pi=cast(PiSceneAdapter, FakePi()),
            session_root=review_root,
            ordinary_stage_retry=self.integration,
        )
        with self.integration.bind_request(context):
            review = coordinator.start_ordinary(context.turn_input)
            self.integration.bind_review_request(review)

        exact_action = {"action": "accept"}
        with self.integration.bind_review_action(
            review_id=review.review_id,
            normalized_action=exact_action,
        ) as action_identity:
            with self.assertRaises(ProviderStageRetryPendingError) as captured:
                coordinator.accept(review.review_id, allow_replay=True)
        chain_id = captured.exception.chain_id
        accepted_before = coordinator.get_review(review.review_id)
        self.assertIsNotNone(accepted_before.accepted_receipt)
        assert accepted_before.accepted_receipt is not None
        accepted_receipt_sha256 = accepted_before.accepted_receipt.receipt_sha256
        head_before = scene_store.load_head(
            world_id=context.binding.world_id,
            branch_id=context.binding.branch_id,
        )
        self.assertIs(head_before.recording_status, RecordingStatus.PROJECTION_PENDING)
        self.assertEqual(self.counts.by_stage[ProviderStage.RECORDER], 1)
        self.assertEqual(
            self.integration.review_action_for_chain(chain_id),
            action_identity,
        )

        frozen = self.service.store.load_input(chain_id)
        invocation = recorder_invocation_from_frozen_input(frozen)
        output = json.dumps(
            {
                "secondary_canon": [],
                "resulting_public_state": "The accepted conversation remains open.",
                "relationship_changes": [],
                "knowledge_changes": [],
                "durable_changes": [],
                "unresolved_threads": ["Ted may respond."],
            },
            separators=(",", ":"),
        )
        base = _pi_result(invocation, "accept-action-recorder:2")
        recorder_result = replace(
            base,
            output_text=output,
            writer_receipt=replace(
                base.writer_receipt,
                output_sha256=text_sha256(output),
            ),
        )
        self.factories[ProviderStage.RECORDER].outcomes[2] = _success(
            "accept-action-recorder:2",
            serialize_pi_result(recorder_result),
        )
        self._restart_runtime()
        coordinator = LeanPiSceneCoordinator(
            store=scene_store,
            planner=FakePlanner(),
            writer_views=views,
            pi=cast(PiSceneAdapter, FakePi()),
            session_root=review_root,
            ordinary_stage_retry=self.integration,
        )
        self.integration.execute_action(backend_action_from_envelope(captured.exception.envelope))
        self.assertEqual(self.counts.by_stage[ProviderStage.RECORDER], 2)

        class _Continuation:
            def durable_result_for_request(self, **kwargs: Any) -> Any:
                return coordinator.durable_result_for_request(**kwargs)

            def repair_recording(self, review_id: str) -> LeanDecisionResultV1:
                return coordinator.repair_recording(review_id)

            def resume_review_action(
                self,
                *,
                action_identity: Any,
                review_id: str,
                normalized_action: Mapping[str, Any],
            ) -> dict[str, Any]:
                if action_identity != action_identity_outer or normalized_action != exact_action:
                    raise AssertionError("Accept action changed")
                decision = coordinator.accept(review_id, allow_replay=True)
                return {
                    "schema_version": "cera.pi_scene.review_decision.v1",
                    "creator_action": "accept",
                    "review_id": decision.review.review_id,
                    "recording_status": (
                        None
                        if decision.review.recording_attempt is None
                        else decision.review.recording_attempt.status.value
                    ),
                    "accepted_receipt_sha256": (
                        None
                        if decision.review.accepted_receipt is None
                        else decision.review.accepted_receipt.receipt_sha256
                    ),
                }

        action_identity_outer = action_identity
        response = self.integration.resume_succeeded_chain(
            chain_id,
            continuation=_Continuation(),
        )
        self.assertEqual(
            response,
            {
                "schema_version": "cera.pi_scene.review_decision.v1",
                "creator_action": "accept",
                "review_id": review.review_id,
                "recording_status": RecordingStatus.COMPLETE.value,
                "accepted_receipt_sha256": accepted_receipt_sha256,
            },
        )
        self.assertEqual(self.counts.by_stage[ProviderStage.RECORDER], 2)
        accepted_after = coordinator.get_review(review.review_id)
        self.assertIsNotNone(accepted_after.accepted_receipt)
        assert accepted_after.accepted_receipt is not None
        self.assertEqual(
            accepted_after.accepted_receipt.receipt_sha256,
            accepted_receipt_sha256,
        )
        head_after = scene_store.load_head(
            world_id=context.binding.world_id,
            branch_id=context.binding.branch_id,
        )
        self.assertEqual(head_after.generation, head_before.generation)
        self.assertIs(head_after.recording_status, RecordingStatus.COMPLETE)

        response_receipt = self.integration.bind_review_action_response(
            action_id=action_identity.action_id,
            response=cast(dict[str, Any], response),
        )
        finalized, request_receipt = self.integration.finalize_review_action(
            action_identity.action_id
        )
        self.assertEqual(finalized, action_identity)
        self.assertEqual(request_receipt.disposition, "completed")
        self.assertEqual(
            self.integration.load_review_action_response_for_chain(chain_id),
            (response, response_receipt),
        )

    def test_semantic_reject_keeps_request_until_later_decline(self) -> None:
        plan = to_primitive(_plan())
        planner_result = PlannerTurnOutputV1(
            sequence=plan["sequence"],
            decision_bundle=plan,
            provider_operations=1,
        )
        self.factories[ProviderStage.PLANNER].initial_outcome_from_input = lambda _exact_input: (
            _success(
                "reject-custody-planner",
                serialize_planner_result(planner_result),
            )
        )
        self.factories[ProviderStage.WRITER].initial_outcome_from_input = lambda exact_input: (
            _success(
                "reject-custody-writer",
                serialize_pi_result(
                    _pi_result(
                        writer_invocation_from_frozen_input(exact_input),
                        "reject-custody-writer",
                    )
                ),
            )
        )
        reject_verdict = _validation(
            _candidate(),
            verdict=SemanticVerdict.REJECT,
        ).verdict

        def semantic_reject(exact_input: bytes) -> ProviderStageSuccessfulResultV1:
            request, custody = semantic_validation_input_from_frozen_input(exact_input)
            result = BoundSemanticValidationV1(
                request=request,
                custody=custody,
                verdict=reject_verdict,
            )
            return _success(
                "reject-custody-semantic",
                serialize_semantic_validation_result(result),
                disposition=semantic_validation_disposition(result),
            )

        self.factories[
            ProviderStage.SEMANTIC_VALIDATOR
        ].initial_outcome_from_input = semantic_reject
        context = _context()
        self._freeze(context)
        scene_store = LeanSceneStore(self.root / "reject-custody-world")
        review_root = self.root / "reject-custody-review-state"
        views = WriterViewMaterializer(self.root / "reject-custody-views")
        coordinator = LeanPiSceneCoordinator(
            store=scene_store,
            planner=FakePlanner(),
            writer_views=views,
            pi=cast(PiSceneAdapter, FakePi()),
            session_root=review_root,
            ordinary_stage_retry=self.integration,
        )
        with self.integration.bind_request(context):
            rejected = coordinator.start_ordinary(context.turn_input)
            self.assertIsNotNone(rejected.semantic_validation)
            assert rejected.semantic_validation is not None
            self.assertIs(
                rejected.semantic_validation.verdict.verdict,
                SemanticVerdict.REJECT,
            )
            self.integration.bind_review_request(rejected)

        self._restart_runtime()
        coordinator = LeanPiSceneCoordinator(
            store=scene_store,
            planner=FakePlanner(),
            writer_views=views,
            pi=cast(PiSceneAdapter, FakePi()),
            session_root=review_root,
            ordinary_stage_retry=self.integration,
        )
        _, recovered_context, counts = self.integration.pending_request_for_review(
            rejected.review_id
        )
        self.assertEqual(counts[ProviderStage.PLANNER], 1)
        self.assertEqual(counts[ProviderStage.WRITER], 2)
        self.assertEqual(counts[ProviderStage.SEMANTIC_VALIDATOR], 2)
        barrier = self.integration.branch_barrier(
            world_id=context.binding.world_id,
            branch_id=context.binding.branch_id,
        )
        self.assertIsNotNone(barrier)
        assert barrier is not None
        self.assertEqual(barrier.disposition, "active")

        with self.integration.bind_request(
            recovered_context,
            prior_stage_occurrences=counts,
        ):
            declined = coordinator.decline(rejected.review_id)
        retired = self.integration.retire_review_request(
            rejected.review_id,
            terminal_evidence_sha256=text_sha256("semantic-reject-auto-declined"),
        )
        self.assertEqual(retired.disposition, "completed")
        self.assertEqual(declined.review.state, "declined")

    def test_writer_frozen_review_binds_prepared_uninvoked_sibling_lanes(self) -> None:
        typed_plan = _plan()
        character_ids = typed_plan.sequence.responding_character_ids or tuple(
            dict.fromkeys(decision.owner_id for decision in typed_plan.decision_records)
        )
        base_context = _context()
        context = _context(
            turn_input=replace(
                base_context.turn_input,
                characters={
                    character_id: {"name": character_id.partition(":")[2]}
                    for character_id in character_ids
                },
            )
        )
        plan = to_primitive(typed_plan)
        planner_result = PlannerTurnOutputV1(
            sequence=plan["sequence"],
            decision_bundle=plan,
            provider_operations=1,
        )
        self.factories[ProviderStage.PLANNER].initial_outcome_from_input = lambda _exact_input: (
            _success(
                "prepared-lanes-planner",
                serialize_planner_result(planner_result),
            )
        )
        self.factories[ProviderStage.WRITER].initial_outcome_from_input = lambda exact_input: (
            _success(
                "prepared-lanes-writer",
                serialize_pi_result(
                    _pi_result(
                        writer_invocation_from_frozen_input(exact_input),
                        "prepared-lanes-writer",
                    )
                ),
            )
        )
        self._freeze(context)
        scene_store = LeanSceneStore(self.root / "prepared-lanes-world")
        review_root = self.root / "prepared-lanes-reviews"
        views = WriterViewMaterializer(self.root / "prepared-lanes-views")
        reader_validator = _UninvokedReaderValidator()
        coordinator = LeanPiSceneCoordinator(
            store=scene_store,
            planner=FakePlanner(),
            writer_views=views,
            pi=cast(PiSceneAdapter, FakePi()),
            session_root=review_root,
            reader_validator=reader_validator,
            ordinary_stage_retry=self.integration,
        )

        with self.integration.bind_request(context):
            review = coordinator.start_ordinary(context.turn_input)
            review_identity = self.integration.bind_review_request(review)

        self.assertEqual(review.review_phase.value, "writer_frozen")
        binding = review.validation_input_binding
        self.assertIsNotNone(binding)
        assert binding is not None
        self.assertIsNotNone(binding.luna_chain_id)
        self.assertIsNotNone(binding.reader_chain_id)
        assert binding.luna_chain_id is not None
        assert binding.reader_chain_id is not None
        self.assertEqual(review_identity.request_id, context.binding.request_id)
        _, recovered_context, counts = self.integration.pending_request_for_review(review.review_id)
        self.assertEqual(recovered_context.binding.request_id, context.binding.request_id)
        self.assertEqual(
            counts,
            {
                ProviderStage.PLANNER: 1,
                ProviderStage.WRITER: 1,
                ProviderStage.SEMANTIC_VALIDATOR: 1,
                ProviderStage.READER: 1,
                ProviderStage.RECORDER: 0,
            },
        )
        self.assertEqual(self.counts.by_stage[ProviderStage.PLANNER], 1)
        self.assertEqual(self.counts.by_stage[ProviderStage.WRITER], 1)
        self.assertEqual(self.counts.by_stage[ProviderStage.SEMANTIC_VALIDATOR], 0)
        self.assertEqual(self.counts.by_stage[ProviderStage.READER], 0)
        cursor_path = (
            self.custody.latest_chains_root
            / f"{context.binding.request_id.removeprefix('request-')}.json"
        )
        cursor = json.loads(cursor_path.read_text(encoding="utf-8"))
        self.assertEqual(
            [entry["stage"] for entry in cursor["entries"]],
            [ProviderStage.PLANNER.value, ProviderStage.WRITER.value],
        )
        planner_chain_id = cursor["entries"][0]["chain_id"]
        writer_chain_id = cursor["entries"][1]["chain_id"]
        self.assertNotIn(
            binding.luna_chain_id,
            {entry["chain_id"] for entry in cursor["entries"]},
        )
        self.assertNotIn(
            binding.reader_chain_id,
            {entry["chain_id"] for entry in cursor["entries"]},
        )
        latest = self.custody.latest_chain_for_chain(planner_chain_id)
        self.assertEqual(latest.chain_id, writer_chain_id)
        for chain_id in (binding.luna_chain_id, binding.reader_chain_id):
            envelope = self.integration.status(chain_id)
            self.assertEqual(envelope["status"]["state"], "in_progress")
            self.assertEqual(
                envelope["status"]["available_actions"],
                ["resume_prepared"],
            )
            self.assertEqual(envelope["status"]["stage_attempts_total"], 1)
            self.assertEqual(
                envelope["status"]["provider_operations_observed_total"],
                0,
            )
            self.assertEqual(
                envelope["status"]["provider_operations_conservative_total"],
                0,
            )

        calls_before_restart = dict(self.counts.by_stage)
        self._restart_runtime()
        restarted = LeanPiSceneCoordinator(
            store=scene_store,
            planner=FakePlanner(),
            writer_views=views,
            pi=cast(PiSceneAdapter, FakePi()),
            session_root=review_root,
            reader_validator=reader_validator,
            ordinary_stage_retry=self.integration,
        )
        recovered_review = restarted.get_review(review.review_id, reconcile=False)
        self.assertEqual(recovered_review.validation_input_binding, binding)
        self.assertEqual(recovered_review.review_phase.value, "writer_frozen")
        _, restarted_context, restarted_counts = self.integration.pending_request_for_review(
            review.review_id
        )
        self.assertEqual(restarted_context.binding.request_id, context.binding.request_id)
        self.assertEqual(restarted_counts, counts)
        self.assertEqual(self.counts.by_stage, calls_before_restart)
        restarted_latest = self.custody.latest_chain_for_chain(planner_chain_id)
        self.assertEqual(restarted_latest.chain_id, latest.chain_id)
        for chain_id in (binding.luna_chain_id, binding.reader_chain_id):
            envelope = self.integration.status(chain_id)
            self.assertEqual(envelope["status"]["state"], "in_progress")
            self.assertEqual(
                envelope["status"]["available_actions"],
                ["resume_prepared"],
            )
            self.assertEqual(
                envelope["status"]["provider_operations_observed_total"],
                0,
            )

    def test_pi_result_codec_preserves_path_and_receipt(self) -> None:
        invocation = _writer_invocation(self.root, "candidate-codec")
        result = _pi_result(invocation, "codec")
        self.assertEqual(deserialize_pi_result(serialize_pi_result(result)), result)


if __name__ == "__main__":
    unittest.main()
