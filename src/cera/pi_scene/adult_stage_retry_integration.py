"""Protected stage-local Retry integration for Adult Scene and Adult Filter.

This module keeps the two DeepSeek roles as distinct provider-stage
occurrences.  Adult Scene bytes are durably frozen before Filter input is
created, and replaying or retrying Filter can never invoke Scene again.
"""

from __future__ import annotations

import base64
import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Any, ClassVar, cast
from uuid import uuid4

from cera.adult_pipeline.acceptance import (
    AdultIntegratedExecutionV1,
)
from cera.adult_pipeline.contracts import (
    AdultFilterRequestV1,
    AdultFilterVerdict,
    AdultPipelineResultV1,
    AdultSceneRequestV1,
)
from cera.adult_pipeline.pi_roles import (
    AdultFilterRoleExecutionV1,
    AdultPiOutputLimitError,
    AdultRoleViewContextV1,
    AdultSceneRoleExecutionV1,
    LazyProtectedWriterViewMaterializer,
    PiDeepSeekAdultFilterPort,
    PiDeepSeekAdultScenePort,
    PiStructuredAdultRoleTransport,
)
from cera.adult_pipeline.pipeline import (
    AdultPipelineInputV1,
    bind_adult_filter_result,
    bind_adult_scene_candidate,
)
from cera.errors import ContractValidationError, ErrorCode, StateConflictError
from cera.generated.provider_stage_retry_contracts_v1 import (
    ProviderStageRetryStatusEnvelopeV1,
    validate_provider_stage_retry_action_v1,
)
from cera.provider_dispatch_guard import assert_provider_dispatch_allowed
from cera.providers.models import (
    ProviderRetryableFailureCategory,
    ProviderTransportError,
)
from cera.schema import from_mapping
from cera.serialization import (
    bytes_sha256,
    canonical_bytes,
    canonical_sha256,
    domain_sha256,
    re_is_sha256,
    text_sha256,
)

from .adult_operation_contracts import ProtectedAdultOperationRecordV1
from .adult_operation_store import ProtectedAdultOperationStore
from .adult_orchestration import (
    PreparedAdultRouteOperationV1,
    classify_prepared_adult_execution,
)
from .operation_ledger import PiProviderInvocationMetricsV1
from .pi_adapter import PiSceneAdapter
from .provider_stage_retry import (
    NON_RETRYABLE_PROVIDER_STAGE_FAILURES,
    ProviderStage,
    ProviderStageAttemptV1,
    ProviderStageFailureClass,
    ProviderStageRetryChainV1,
    ProviderStageRetryPhase,
)
from .provider_stage_retry_adapters import (
    CallableProviderStageAttemptOwnerV1,
    ProviderStageAttemptOwnerBindingV1,
    ProviderStageBoundaryKind,
    ProviderStageLedgerSnapshotV1,
    ProviderStageOwnerRetirementV1,
    ProviderStagePreparedCallableDispatchV1,
    ProviderStageReceiptMetricsV1,
)
from .provider_stage_retry_executor import (
    PreparedProviderStageDispatchPort,
    ProviderStageAmbiguityResolutionV1,
    ProviderStageAttemptMetricsV1,
    ProviderStageAttemptOutcomeV1,
    ProviderStageAttemptOwnerPort,
    ProviderStageClosedFailureV1,
    ProviderStageDispatchAmbiguousV1,
    ProviderStageNonRetryableFailureV1,
    ProviderStagePreparationV1,
    ProviderStageSemanticDisposition,
    ProviderStageSuccessfulResultV1,
)
from .provider_stage_retry_packets import (
    FrozenProviderStageResultV1,
    ProviderStageConfigurationV1,
    ProviderStageFrozenPacketV1,
    freeze_adult_filter_stage_packet,
    freeze_adult_scene_stage_packet,
)
from .provider_stage_retry_runtime import (
    ProviderStageRetryRuntimeServiceV1,
    ProviderStageRuntimeAdapterV1,
)
from .provider_stage_retry_scope import ProviderStageRetryOccurrenceScopeV1
from .store import AcceptedPiSessionV1

_ADULT_STAGES = frozenset({ProviderStage.ADULT_SCENE, ProviderStage.ADULT_FILTER})
_RESULT_READY_PHASES = frozenset(
    {
        ProviderStageRetryPhase.RESULT_FROZEN,
        ProviderStageRetryPhase.DOWNSTREAM_INTENT_FROZEN,
        ProviderStageRetryPhase.DOWNSTREAM_BOUND,
        ProviderStageRetryPhase.SUCCEEDED,
    }
)
_RETRYABLE_CLASS_BY_CATEGORY = {
    ProviderRetryableFailureCategory.TRANSPORT_TIMEOUT.value: (
        ProviderStageFailureClass.TRANSPORT_TIMEOUT
    ),
    ProviderRetryableFailureCategory.PROVIDER_UNAVAILABLE.value: (
        ProviderStageFailureClass.PROVIDER_UNAVAILABLE
    ),
    ProviderRetryableFailureCategory.PROVIDER_PROCESS_FAILED.value: (
        ProviderStageFailureClass.PROVIDER_PROCESS_FAILED
    ),
    ProviderRetryableFailureCategory.PROVIDER_STREAM_INCOMPLETE.value: (
        ProviderStageFailureClass.PROVIDER_STREAM_INCOMPLETE
    ),
    ProviderRetryableFailureCategory.PROVIDER_COMPLETION_INCOMPLETE.value: (
        ProviderStageFailureClass.PROVIDER_COMPLETION_INCOMPLETE
    ),
    ProviderRetryableFailureCategory.PROVIDER_OUTPUT_INVALID.value: (
        ProviderStageFailureClass.PROVIDER_OUTPUT_INVALID
    ),
}
_CATEGORY_BY_RETRYABLE_CLASS = {
    failure_class: category for category, failure_class in _RETRYABLE_CLASS_BY_CATEGORY.items()
}


@dataclass(frozen=True, slots=True)
class AdultStageRetryExecutionInputV1:
    """Complete protected authority needed to reproduce both stage packets."""

    generation_id: str
    prepared: AdultPipelineInputV1
    role_context: AdultRoleViewContextV1
    accepted_parent_session: AcceptedPiSessionV1 | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.generation_id, str) or not self.generation_id.strip():
            raise ContractValidationError("adult provider-stage generation ID is empty")
        if type(self.prepared) is not AdultPipelineInputV1:
            raise ContractValidationError("adult provider-stage prepared input changed")
        if type(self.role_context) is not AdultRoleViewContextV1:
            raise ContractValidationError("adult provider-stage role context changed")
        if (
            self.role_context.world_id != self.prepared.world_id
            or self.role_context.branch_id != self.prepared.branch_id
            or self.role_context.candidate_id != self.prepared.candidate_id
        ):
            raise StateConflictError("adult provider-stage role context changed custody")
        if self.accepted_parent_session is not None and (
            type(self.accepted_parent_session) is not AcceptedPiSessionV1
        ):
            raise ContractValidationError("adult provider-stage parent session changed")


@dataclass(frozen=True, slots=True)
class AdultStageRetryProgressV1:
    """Either a complete adult execution or the one stage awaiting recovery."""

    scene_chain_id: str
    filter_chain_id: str | None
    pending_stage: ProviderStage | None
    pending_chain_id: str | None
    execution: AdultIntegratedExecutionV1 | None

    def __post_init__(self) -> None:
        if not self.scene_chain_id.startswith("stage-retry-"):
            raise ContractValidationError("adult provider-stage Scene chain ID is invalid")
        complete = self.execution is not None
        pending = self.pending_stage is not None or self.pending_chain_id is not None
        if complete == pending:
            raise ContractValidationError("adult provider-stage progress is not closed")
        if complete and self.filter_chain_id is None:
            raise ContractValidationError("adult provider-stage completion lost Filter chain")
        if pending and (self.pending_stage not in _ADULT_STAGES or self.pending_chain_id is None):
            raise ContractValidationError("adult provider-stage pending identity is invalid")

    @property
    def completed(self) -> bool:
        return self.execution is not None


@dataclass(frozen=True, slots=True)
class _AdultSceneAttemptRequestV1:
    scene_request: AdultSceneRequestV1
    role_context: AdultRoleViewContextV1
    accepted_parent_session: AcceptedPiSessionV1 | None


@dataclass(frozen=True, slots=True)
class _AdultFilterAttemptRequestV1:
    filter_request: AdultFilterRequestV1
    role_context: AdultRoleViewContextV1


@dataclass(frozen=True, slots=True)
class _AdultAttemptDispositionV1:
    SCHEMA_VERSION: ClassVar[str] = "cera.adult_provider_stage_disposition.v1"

    schema_version: str
    chain_id: str
    attempt_number: int
    stage: ProviderStage
    disposition: str
    provider_invocation_id: str | None
    provider_calls_observed: int
    provider_operations_observed: int
    provider_operations_conservative: int
    ledger_prefix_after_sha256: str
    duration_ms: int
    input_tokens: int | None
    cached_input_tokens: int | None
    output_tokens: int | None
    reasoning_tokens: int | None
    failure_category: str | None
    failure_class: ProviderStageFailureClass | None
    failure_evidence_sha256: str | None
    exact_result_sha256: str | None
    result_evidence_sha256: str | None
    semantic_disposition: ProviderStageSemanticDisposition | None

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("adult provider-stage disposition schema changed")
        if self.stage not in _ADULT_STAGES:
            raise ContractValidationError("adult provider-stage disposition owner changed")
        if self.disposition not in {
            "success",
            "retryable_failure",
            "non_retryable_failure",
        }:
            raise ContractValidationError("adult provider-stage disposition is open")
        if type(self.attempt_number) is not int or not 1 <= self.attempt_number <= 3:
            raise ContractValidationError("adult provider-stage disposition attempt is invalid")
        if type(self.provider_calls_observed) is not int or (
            self.provider_calls_observed not in {0, 1}
        ):
            raise ContractValidationError("adult provider-stage call observation is invalid")
        metrics = ProviderStageAttemptMetricsV1(
            ledger_prefix_after_sha256=self.ledger_prefix_after_sha256,
            provider_operations_observed=self.provider_operations_observed,
            provider_operations_conservative=self.provider_operations_conservative,
            duration_ms=self.duration_ms,
            input_tokens=self.input_tokens,
            cached_input_tokens=self.cached_input_tokens,
            output_tokens=self.output_tokens,
            reasoning_tokens=self.reasoning_tokens,
        )
        if (self.provider_calls_observed == 0) != (metrics.provider_operations_observed == 0):
            raise ContractValidationError(
                "adult provider-stage call and operation observations differ"
            )
        if self.disposition == "success":
            if (
                self.exact_result_sha256 is None
                or self.result_evidence_sha256 is None
                or self.semantic_disposition is None
                or self.failure_category is not None
                or self.failure_class is not None
                or self.failure_evidence_sha256 is not None
                or metrics.provider_operations_observed < 1
                or metrics.provider_operations_conservative != metrics.provider_operations_observed
            ):
                raise ContractValidationError("adult provider-stage success disposition changed")
        elif self.disposition == "retryable_failure":
            if (
                self.failure_category not in _RETRYABLE_CLASS_BY_CATEGORY
                or self.failure_class is not _RETRYABLE_CLASS_BY_CATEGORY[self.failure_category]
                or self.failure_evidence_sha256 is None
                or self.exact_result_sha256 is not None
                or self.result_evidence_sha256 is not None
                or self.semantic_disposition is not None
                or metrics.provider_operations_conservative != metrics.provider_operations_observed
            ):
                raise ContractValidationError("adult provider-stage retryable disposition changed")
        elif (
            self.failure_category is not None
            or self.failure_class not in NON_RETRYABLE_PROVIDER_STAGE_FAILURES
            or self.failure_evidence_sha256 is None
            or self.exact_result_sha256 is not None
            or self.result_evidence_sha256 is not None
            or self.semantic_disposition is not None
            or metrics.provider_operations_conservative != metrics.provider_operations_observed
        ):
            raise ContractValidationError("adult provider-stage non-Retry disposition changed")
        for value, field_name in (
            (self.exact_result_sha256, "result hash"),
            (self.result_evidence_sha256, "result evidence"),
            (self.failure_evidence_sha256, "failure evidence"),
        ):
            if value is not None and not re_is_sha256(value):
                raise ContractValidationError(f"adult provider-stage {field_name} is invalid")


class _AdultPreparedDispatchV1:
    """Persist a safe closed disposition before the executor can checkpoint it."""

    def __init__(
        self,
        inner: PreparedProviderStageDispatchPort,
        record_outcome: Callable[[ProviderStageAttemptOutcomeV1], ProviderStageAttemptOutcomeV1],
    ) -> None:
        self._inner = inner
        self._record_outcome = record_outcome

    @property
    def dispatch_evidence_sha256(self) -> str:
        return self._inner.dispatch_evidence_sha256

    def invoke(self) -> ProviderStageAttemptOutcomeV1:
        return self._record_outcome(self._inner.invoke())


class _AdultOutcomeRecordingOwnerV1:
    """Adult owner wrapper that closes the executor crash window provider-free."""

    def __init__(
        self,
        inner: ProviderStageAttemptOwnerPort,
        record_outcome: Callable[[ProviderStageAttemptOutcomeV1], ProviderStageAttemptOutcomeV1],
    ) -> None:
        self._inner = inner
        self._record_outcome = record_outcome

    @property
    def session_scope_sha256(self) -> str:
        return self._inner.session_scope_sha256

    @property
    def ledger_prefix_before_sha256(self) -> str:
        return self._inner.ledger_prefix_before_sha256

    @property
    def maximum_provider_operations(self) -> int:
        return self._inner.maximum_provider_operations

    def prepare(
        self,
        *,
        chain_id: str,
        attempt_number: int,
        exact_input: bytes,
    ) -> ProviderStagePreparationV1:
        preparation = self._inner.prepare(
            chain_id=chain_id,
            attempt_number=attempt_number,
            exact_input=exact_input,
        )
        if isinstance(preparation, ProviderStagePreparedCallableDispatchV1):
            return _AdultPreparedDispatchV1(preparation, self._record_outcome)
        return preparation

    def retire(
        self,
        *,
        chain_id: str,
        attempt_number: int,
        failure_class: ProviderStageFailureClass,
    ) -> str:
        return self._inner.retire(
            chain_id=chain_id,
            attempt_number=attempt_number,
            failure_class=failure_class,
        )


@dataclass(frozen=True, slots=True)
class _AdultContinuationChainBindingV1:
    """Content-free protected reference from one stage chain to its operation."""

    SCHEMA_VERSION: ClassVar[str] = "cera.adult_stage_retry_chain_binding.v1"

    schema_version: str
    chain_id: str
    stage: ProviderStage
    source_scene_chain_id: str
    operation_id: str
    operation_sha256: str
    request_id: str
    candidate_id: str

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("adult continuation chain schema changed")
        if self.stage not in _ADULT_STAGES:
            raise ContractValidationError("adult continuation chain changed stage")
        _require_stage_chain_id(self.chain_id)
        _require_stage_chain_id(self.source_scene_chain_id)
        if self.stage is ProviderStage.ADULT_SCENE:
            if self.chain_id != self.source_scene_chain_id:
                raise ContractValidationError("adult Scene continuation changed its source")
        elif self.chain_id == self.source_scene_chain_id:
            raise ContractValidationError("adult Filter continuation lost its predecessor")
        if not re_is_sha256(self.operation_sha256):
            raise ContractValidationError("adult continuation operation hash is invalid")
        for value, label in (
            (self.operation_id, "operation"),
            (self.request_id, "request"),
            (self.candidate_id, "candidate"),
        ):
            if not isinstance(value, str) or not value.strip() or "\x00" in value:
                raise ContractValidationError(f"adult continuation {label} identity is invalid")


@dataclass(frozen=True, slots=True)
class _AdultContinuationSuccessorV1:
    SCHEMA_VERSION: ClassVar[str] = "cera.adult_stage_retry_successor.v1"

    schema_version: str
    source_scene_chain_id: str
    successor_filter_chain_id: str
    operation_sha256: str

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("adult continuation successor schema changed")
        _require_stage_chain_id(self.source_scene_chain_id)
        _require_stage_chain_id(self.successor_filter_chain_id)
        if self.source_scene_chain_id == self.successor_filter_chain_id:
            raise ContractValidationError("adult continuation successor did not advance")
        if not re_is_sha256(self.operation_sha256):
            raise ContractValidationError("adult continuation successor operation is invalid")


class AdultStageRetryPacketFactoryV1:
    """Create deterministic protected Scene then Filter packets and scopes."""

    def __init__(
        self,
        *,
        scene_configuration: ProviderStageConfigurationV1,
        filter_configuration: ProviderStageConfigurationV1,
    ) -> None:
        if scene_configuration.stage is not ProviderStage.ADULT_SCENE:
            raise ContractValidationError("adult Scene Retry configuration changed stage")
        if filter_configuration.stage is not ProviderStage.ADULT_FILTER:
            raise ContractValidationError("adult Filter Retry configuration changed stage")
        self.scene_configuration = scene_configuration
        self.filter_configuration = filter_configuration

    def scene_packet(
        self,
        source: AdultStageRetryExecutionInputV1,
    ) -> ProviderStageFrozenPacketV1:
        return freeze_adult_scene_stage_packet(
            scene_request=source.prepared.scene_request,
            evidence_bundle={
                "role_context": source.role_context,
                "accepted_parent_session": source.accepted_parent_session,
            },
            configuration=self.scene_configuration,
        )

    def filter_packet(
        self,
        source: AdultStageRetryExecutionInputV1,
        scene_result: FrozenProviderStageResultV1,
    ) -> ProviderStageFrozenPacketV1:
        scene_execution = _decode_scene_execution(scene_result.exact_result)
        if scene_execution.invocation.receipt.request_sha256 != canonical_sha256(
            source.prepared.scene_request
        ):
            raise StateConflictError("adult Filter packet cites another Scene request")
        return freeze_adult_filter_stage_packet(
            scene_result=scene_result,
            filter_context={
                "scene_request": source.prepared.scene_request,
                "role_context": source.role_context,
            },
            configuration=self.filter_configuration,
        )

    def scope(
        self,
        source: AdultStageRetryExecutionInputV1,
        packet: ProviderStageFrozenPacketV1,
    ) -> ProviderStageRetryOccurrenceScopeV1:
        accepted_state_sha256 = source.prepared.accepted_head_sha256 or domain_sha256(
            "cera.adult_provider_stage_unaccepted_root.v1",
            {
                "world_id": source.prepared.world_id,
                "branch_id": source.prepared.branch_id,
            },
        )
        return ProviderStageRetryOccurrenceScopeV1.create(
            world_id=source.prepared.world_id,
            branch_id=source.prepared.branch_id,
            request_id=source.prepared.request_id,
            generation_id=source.generation_id,
            stage=packet.stage,
            stage_ordinal=1,
            accepted_state_sha256=accepted_state_sha256,
            exact_input=packet.exact_bytes,
            authority_binding={
                "candidate_id": source.prepared.candidate_id,
                "accepted_head_sha256": source.prepared.accepted_head_sha256,
                "scene_request_sha256": canonical_sha256(source.prepared.scene_request),
            },
        )


class AdultStageRetryCoordinatorV1:
    """Advance Scene then Filter without ever replaying an accepted stage."""

    def __init__(
        self,
        *,
        runtime: ProviderStageRetryRuntimeServiceV1,
        packets: AdultStageRetryPacketFactoryV1,
    ) -> None:
        self.runtime = runtime
        self.packets = packets

    def execute_or_continue(
        self,
        source: AdultStageRetryExecutionInputV1,
        *,
        before_stage_dispatch: Callable[
            [ProviderStageRetryOccurrenceScopeV1, ProviderStageFrozenPacketV1],
            None,
        ]
        | None = None,
    ) -> AdultStageRetryProgressV1:
        """Start missing initial stages or continue provider-free from frozen results."""

        scene_packet = self.packets.scene_packet(source)
        scene_scope = self.packets.scope(source, scene_packet)
        if before_stage_dispatch is not None:
            before_stage_dispatch(scene_scope, scene_packet)
        scene_chain = self.runtime.start_initial(scope=scene_scope, packet=scene_packet)
        if scene_chain.phase not in _RESULT_READY_PHASES:
            return AdultStageRetryProgressV1(
                scene_chain_id=scene_chain.chain_id,
                filter_chain_id=None,
                pending_stage=ProviderStage.ADULT_SCENE,
                pending_chain_id=scene_chain.chain_id,
                execution=None,
            )
        scene_exact = self.runtime.finalize_result_once(scene_chain.chain_id)
        scene_frozen = FrozenProviderStageResultV1.create(
            source_stage=ProviderStage.ADULT_SCENE,
            exact_result=scene_exact,
        )
        scene_execution = _decode_scene_execution(scene_exact)
        scene = bind_adult_scene_candidate(
            source.prepared,
            scene_execution.invocation,
        )

        filter_packet = self.packets.filter_packet(source, scene_frozen)
        filter_scope = self.packets.scope(source, filter_packet)
        if before_stage_dispatch is not None:
            before_stage_dispatch(filter_scope, filter_packet)
        filter_chain = self.runtime.start_initial(scope=filter_scope, packet=filter_packet)
        if filter_chain.phase not in _RESULT_READY_PHASES:
            return AdultStageRetryProgressV1(
                scene_chain_id=scene_chain.chain_id,
                filter_chain_id=filter_chain.chain_id,
                pending_stage=ProviderStage.ADULT_FILTER,
                pending_chain_id=filter_chain.chain_id,
                execution=None,
            )
        filter_exact = self.runtime.finalize_result_once(filter_chain.chain_id)
        filter_execution = _decode_filter_execution(filter_exact)
        filtered = bind_adult_filter_result(
            source.prepared,
            scene,
            filter_execution.invocation,
        )
        integrated = AdultIntegratedExecutionV1(
            result=AdultPipelineResultV1(scene=scene, filtered=filtered),
            scene_session=scene_execution.session_binding,
            filter_execution=filter_execution.execution_binding,
        )
        return AdultStageRetryProgressV1(
            scene_chain_id=scene_chain.chain_id,
            filter_chain_id=filter_chain.chain_id,
            pending_stage=None,
            pending_chain_id=None,
            execution=integrated,
        )


class AdultStageRetryContinuationServiceV1:
    """Protected restart facade joining adult operation and stage Retry custody."""

    def __init__(
        self,
        *,
        runtime: ProviderStageRetryRuntimeServiceV1,
        packets: AdultStageRetryPacketFactoryV1,
        pi_adapter: PiSceneAdapter,
        operation_store: ProtectedAdultOperationStore,
        protected_mapping_root: Path,
    ) -> None:
        if type(runtime) is not ProviderStageRetryRuntimeServiceV1:
            raise ContractValidationError("adult continuation runtime changed")
        if type(packets) is not AdultStageRetryPacketFactoryV1:
            raise ContractValidationError("adult continuation packet factory changed")
        if type(pi_adapter) is not PiSceneAdapter:
            raise ContractValidationError("adult continuation Pi adapter changed")
        if type(operation_store) is not ProtectedAdultOperationStore:
            raise ContractValidationError("adult continuation operation custody changed")
        root = protected_mapping_root.resolve()
        if not root.is_absolute():
            raise ContractValidationError("adult continuation mapping root is not absolute")
        self.runtime = runtime
        self.packets = packets
        self.pi_adapter = pi_adapter
        self.operation_store = operation_store
        self.root = root
        self._chains_root = root / "CHAINS"
        self._successors_root = root / "SUCCESSORS"
        for path in (self._chains_root, self._successors_root):
            path.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._coordinator = AdultStageRetryCoordinatorV1(
            runtime=runtime,
            packets=packets,
        )

    def execute_or_continue(
        self,
        *,
        prepared: PreparedAdultRouteOperationV1,
        source: AdultStageRetryExecutionInputV1,
        before_stage_dispatch: Callable[
            [ProviderStageRetryOccurrenceScopeV1, ProviderStageFrozenPacketV1],
            None,
        ]
        | None = None,
    ) -> AdultStageRetryProgressV1:
        """Freeze operation/source custody, then explicitly advance the pipeline."""

        with self._lock:
            self._validate_source(prepared, source)
            self.operation_store.begin(prepared)
            return self._continue(
                prepared,
                source,
                before_stage_dispatch=before_stage_dispatch,
            )

    def execute_action(
        self,
        action: object,
        *,
        before_stage_dispatch: Callable[
            [ProviderStageRetryOccurrenceScopeV1, ProviderStageFrozenPacketV1],
            None,
        ]
        | None = None,
    ) -> AdultStageRetryProgressV1:
        """Execute one exact manual provider Retry, then continue from protected source."""

        validated = validate_provider_stage_retry_action_v1(action)
        if validated["action_kind"] != "provider_retry":
            raise StateConflictError("adult continuation action is not provider Retry")
        chain_id = validated["chain_id"]
        with self._lock:
            source = self.source_for_chain(chain_id)
            prepared = self._prepared_for_chain(chain_id)
            self.runtime.execute_manual_retry(validated)
            return self._continue(
                prepared,
                source,
                before_stage_dispatch=before_stage_dispatch,
            )

    def resume_succeeded_chain(
        self,
        chain_id: str,
        *,
        before_stage_dispatch: Callable[
            [ProviderStageRetryOccurrenceScopeV1, ProviderStageFrozenPacketV1],
            None,
        ]
        | None = None,
    ) -> AdultStageRetryProgressV1:
        """Explicitly continue forward progress; never accept another Retry attempt."""

        latest = self.latest_chain_for_chain(chain_id)
        with self._lock:
            self.runtime.recover_incomplete(latest)
            source = self.source_for_chain(latest)
            prepared = self._prepared_for_chain(latest)
            return self._continue(
                prepared,
                source,
                before_stage_dispatch=before_stage_dispatch,
            )

    def status(self, chain_id: str) -> ProviderStageRetryStatusEnvelopeV1:
        """Provider-free GET seam, including interrupted-dispatch reconciliation."""

        self._chain_binding(chain_id)
        self.runtime.recover_incomplete(chain_id)
        return self.runtime.canonical_status(chain_id=chain_id)

    def latest_chain_for_chain(self, chain_id: str) -> str:
        """Return the durable latest stage cursor without reading adult content."""

        binding = self._chain_binding(chain_id)
        successor = self._successor_optional(binding.source_scene_chain_id)
        if successor is None:
            return binding.source_scene_chain_id
        if successor.operation_sha256 != binding.operation_sha256:
            raise StateConflictError("adult continuation successor changed operation")
        return successor.successor_filter_chain_id

    def source_for_chain(self, chain_id: str) -> AdultStageRetryExecutionInputV1:
        """Reconstruct exact adult source from the protected Scene packet and operation."""

        binding = self._chain_binding(chain_id)
        scene_binding = self._chain_binding(binding.source_scene_chain_id)
        if (
            scene_binding.stage is not ProviderStage.ADULT_SCENE
            or scene_binding.operation_sha256 != binding.operation_sha256
        ):
            raise StateConflictError("adult continuation source-chain binding changed")
        prepared = self._prepared_for_binding(scene_binding)
        scope = self.runtime.scope_for_chain(scene_binding.chain_id)
        if (
            scope.stage is not ProviderStage.ADULT_SCENE
            or scope.request_id != prepared.request_id
            or scope.world_id != prepared.route_state.world_id
            or scope.branch_id != prepared.route_state.branch_id
        ):
            raise StateConflictError("adult continuation scope changed prepared operation")
        exact_input = self.runtime.store.load_input(scene_binding.chain_id)
        request = _decode_scene_attempt_request(exact_input, self.pi_adapter)
        source = AdultStageRetryExecutionInputV1(
            generation_id=scope.generation_id,
            prepared=AdultPipelineInputV1(
                request_id=prepared.request_id,
                candidate_id=prepared.candidate_id,
                world_id=prepared.route_state.world_id,
                branch_id=prepared.route_state.branch_id,
                accepted_head_sha256=prepared.route_state.accepted_head_sha256,
                scene_request=prepared.scene_request,
            ),
            role_context=request.role_context,
            accepted_parent_session=request.accepted_parent_session,
        )
        self._validate_source(prepared, source)
        return source

    def load_terminal_for_chain(
        self,
        chain_id: str,
    ) -> AdultIntegratedExecutionV1 | None:
        binding = self._chain_binding(chain_id)
        return self._load_terminal(binding)

    def load_terminal_for_request(
        self,
        *,
        request_id: str,
        candidate_id: str,
    ) -> AdultIntegratedExecutionV1 | None:
        record = self.operation_store.lookup(
            request_id=request_id,
            candidate_id=candidate_id,
        )
        binding = self._scene_binding_for_operation(record.prepared.operation_sha256)
        if binding is None:
            return None
        return self._load_terminal(binding)

    def _continue(
        self,
        prepared: PreparedAdultRouteOperationV1,
        source: AdultStageRetryExecutionInputV1,
        *,
        before_stage_dispatch: Callable[
            [ProviderStageRetryOccurrenceScopeV1, ProviderStageFrozenPacketV1],
            None,
        ]
        | None = None,
    ) -> AdultStageRetryProgressV1:
        scene_packet = self.packets.scene_packet(source)
        scene_scope = self.packets.scope(source, scene_packet)
        scene_chain_id = scene_scope.identity.chain_id

        def freeze_and_bind(
            scope: ProviderStageRetryOccurrenceScopeV1,
            packet: ProviderStageFrozenPacketV1,
        ) -> None:
            self.runtime.store.begin(scope.identity, packet.exact_bytes)
            self.runtime.remember_scope(scope)
            if scope.stage is ProviderStage.ADULT_SCENE:
                self._bind_chain(
                    prepared,
                    chain_id=scope.identity.chain_id,
                    stage=ProviderStage.ADULT_SCENE,
                    source_scene_chain_id=scope.identity.chain_id,
                )
                return
            self._bind_chain(
                prepared,
                chain_id=scope.identity.chain_id,
                stage=ProviderStage.ADULT_FILTER,
                source_scene_chain_id=scene_chain_id,
            )
            self._bind_successor(
                operation_sha256=prepared.operation_sha256,
                source_scene_chain_id=scene_chain_id,
                successor_filter_chain_id=scope.identity.chain_id,
            )

        def freeze_bind_and_notify(
            scope: ProviderStageRetryOccurrenceScopeV1,
            packet: ProviderStageFrozenPacketV1,
        ) -> None:
            freeze_and_bind(scope, packet)
            if before_stage_dispatch is not None:
                before_stage_dispatch(scope, packet)

        progress = self._coordinator.execute_or_continue(
            source,
            before_stage_dispatch=freeze_bind_and_notify,
        )
        if progress.execution is not None:
            self._bind_terminal(prepared, progress)
        return progress

    def _bind_chain(
        self,
        prepared: PreparedAdultRouteOperationV1,
        *,
        chain_id: str,
        stage: ProviderStage,
        source_scene_chain_id: str,
    ) -> None:
        binding = _AdultContinuationChainBindingV1(
            schema_version=_AdultContinuationChainBindingV1.SCHEMA_VERSION,
            chain_id=chain_id,
            stage=stage,
            source_scene_chain_id=source_scene_chain_id,
            operation_id=prepared.operation_id,
            operation_sha256=prepared.operation_sha256,
            request_id=prepared.request_id,
            candidate_id=prepared.candidate_id,
        )
        if stage is ProviderStage.ADULT_SCENE:
            existing = self._scene_binding_for_operation(prepared.operation_sha256)
            if existing is not None and existing != binding:
                raise StateConflictError("adult operation already maps to another Scene occurrence")
        _write_protected_once(self._chain_path(chain_id), canonical_bytes(binding))

    def _bind_successor(
        self,
        *,
        operation_sha256: str,
        source_scene_chain_id: str,
        successor_filter_chain_id: str,
    ) -> None:
        successor = _AdultContinuationSuccessorV1(
            schema_version=_AdultContinuationSuccessorV1.SCHEMA_VERSION,
            source_scene_chain_id=source_scene_chain_id,
            successor_filter_chain_id=successor_filter_chain_id,
            operation_sha256=operation_sha256,
        )
        _write_protected_once(
            self._successor_path(source_scene_chain_id),
            canonical_bytes(successor),
        )

    def _bind_terminal(
        self,
        prepared: PreparedAdultRouteOperationV1,
        progress: AdultStageRetryProgressV1,
    ) -> None:
        execution = progress.execution
        if execution is None or progress.filter_chain_id is None:
            raise StateConflictError("adult continuation terminal is incomplete")
        outcome = classify_prepared_adult_execution(prepared, execution)
        record = self.operation_store.record_execution(outcome)
        if record.outcome is None or record.outcome.outcome_sha256 != outcome.outcome_sha256:
            raise StateConflictError("adult continuation terminal changed operation outcome")

    def _load_terminal(
        self,
        binding: _AdultContinuationChainBindingV1,
    ) -> AdultIntegratedExecutionV1 | None:
        record = self._prepared_record_for_binding(binding)
        outcome = record.outcome
        if outcome is None:
            return None
        latest = self.latest_chain_for_chain(binding.chain_id)
        if self.runtime.read_chain(latest).phase is not ProviderStageRetryPhase.SUCCEEDED:
            raise StateConflictError("adult continuation outcome lacks a succeeded terminal chain")
        return outcome.protected_execution

    def _validate_source(
        self,
        prepared: PreparedAdultRouteOperationV1,
        source: AdultStageRetryExecutionInputV1,
    ) -> None:
        if (
            prepared.request_id != source.prepared.request_id
            or prepared.candidate_id != source.prepared.candidate_id
            or prepared.route_state.world_id != source.prepared.world_id
            or prepared.route_state.branch_id != source.prepared.branch_id
            or prepared.route_state.accepted_head_sha256 != source.prepared.accepted_head_sha256
            or prepared.scene_request != source.prepared.scene_request
        ):
            raise StateConflictError("adult continuation source changed prepared operation")

    def _prepared_for_chain(self, chain_id: str) -> PreparedAdultRouteOperationV1:
        return self._prepared_for_binding(self._chain_binding(chain_id))

    def _prepared_for_binding(
        self,
        binding: _AdultContinuationChainBindingV1,
    ) -> PreparedAdultRouteOperationV1:
        return self._prepared_record_for_binding(binding).prepared

    def _prepared_record_for_binding(
        self,
        binding: _AdultContinuationChainBindingV1,
    ) -> ProtectedAdultOperationRecordV1:
        record = self.operation_store.lookup(
            request_id=binding.request_id,
            candidate_id=binding.candidate_id,
        )
        if (
            record.prepared.operation_id != binding.operation_id
            or record.prepared.operation_sha256 != binding.operation_sha256
        ):
            raise StateConflictError("adult continuation changed operation reference")
        return record

    def _chain_binding(self, chain_id: str) -> _AdultContinuationChainBindingV1:
        binding = _read_typed_artifact(
            self._chain_path(chain_id),
            _AdultContinuationChainBindingV1,
            "adult continuation chain binding",
        )
        scope = self.runtime.scope_for_chain(chain_id)
        if scope.stage is not binding.stage or scope.request_id != binding.request_id:
            raise StateConflictError("adult continuation chain changed durable scope")
        return binding

    def _scene_binding_for_operation(
        self,
        operation_sha256: str,
    ) -> _AdultContinuationChainBindingV1 | None:
        matches = tuple(
            binding
            for path in sorted(self._chains_root.glob("*.json"))
            if (
                binding := _read_typed_artifact(
                    path,
                    _AdultContinuationChainBindingV1,
                    "adult continuation chain binding",
                )
            ).operation_sha256
            == operation_sha256
            and binding.stage is ProviderStage.ADULT_SCENE
        )
        if len(matches) > 1:
            raise StateConflictError("adult operation maps to multiple Scene chains")
        return matches[0] if matches else None

    def _successor_optional(
        self,
        source_scene_chain_id: str,
    ) -> _AdultContinuationSuccessorV1 | None:
        path = self._successor_path(source_scene_chain_id)
        if not path.is_file():
            return None
        return _read_typed_artifact(
            path,
            _AdultContinuationSuccessorV1,
            "adult continuation successor",
        )

    def _chain_path(self, chain_id: str) -> Path:
        _require_stage_chain_id(chain_id)
        return self._chains_root / f"{chain_id.removeprefix('stage-retry-')}.json"

    def _successor_path(self, source_scene_chain_id: str) -> Path:
        _require_stage_chain_id(source_scene_chain_id)
        return self._successors_root / (
            f"{source_scene_chain_id.removeprefix('stage-retry-')}.json"
        )


class AdultPiStageAttemptOwnerFactoryV1:
    """Reconstruct lazy one-shot Pi owners from protected exact packets."""

    def __init__(
        self,
        *,
        stage: ProviderStage,
        pi_adapter: PiSceneAdapter,
        protected_runtime_root: Path,
    ) -> None:
        if stage not in _ADULT_STAGES:
            raise ContractValidationError("adult provider-stage owner changed stage")
        if type(pi_adapter) is not PiSceneAdapter:
            raise ContractValidationError("adult provider-stage Pi adapter changed")
        root = protected_runtime_root.resolve()
        if not root.is_absolute():
            raise ContractValidationError("adult provider-stage protected root is not absolute")
        self.stage = stage
        self.pi_adapter = pi_adapter
        self.protected_runtime_root = root

    def create_initial_owner(
        self,
        *,
        scope: ProviderStageRetryOccurrenceScopeV1,
        packet: ProviderStageFrozenPacketV1,
    ) -> ProviderStageAttemptOwnerPort:
        if scope.stage is not self.stage or packet.stage is not self.stage:
            raise StateConflictError("adult provider-stage initial owner changed stage")
        return self._owner(
            chain_id=scope.identity.chain_id,
            attempt_number=1,
            exact_input=packet.exact_bytes,
            ledger_before=self._current_ledger_snapshot(),
        )

    def create_retry_owner(
        self,
        *,
        chain: ProviderStageRetryChainV1,
        exact_input: bytes,
    ) -> ProviderStageAttemptOwnerPort:
        if chain.identity.stage is not self.stage:
            raise StateConflictError("adult provider-stage Retry owner changed stage")
        return self._owner(
            chain_id=chain.chain_id,
            attempt_number=len(chain.attempts) + 1,
            exact_input=exact_input,
            ledger_before=self._current_ledger_snapshot(),
        )

    def reconstruct_owner(
        self,
        *,
        chain: ProviderStageRetryChainV1,
        attempt: ProviderStageAttemptV1,
        exact_input: bytes,
    ) -> ProviderStageAttemptOwnerPort:
        if chain.identity.stage is not self.stage or attempt not in chain.attempts:
            raise StateConflictError("adult provider-stage reconstructed owner changed attempt")
        owner = self._owner(
            chain_id=chain.chain_id,
            attempt_number=attempt.attempt_number,
            exact_input=exact_input,
            ledger_before=self._ledger_snapshot_for_prefix(attempt.ledger_prefix_before_sha256),
        )
        if owner.session_scope_sha256 != attempt.session_scope_sha256:
            raise StateConflictError("adult provider-stage reconstructed session changed")
        return owner

    def check_status(
        self,
        *,
        chain: ProviderStageRetryChainV1,
    ) -> ProviderStageAmbiguityResolutionV1 | None:
        """Resolve only a protected, exact, closed attempt disposition."""

        if chain.identity.stage is not self.stage or not chain.attempts:
            raise StateConflictError("adult provider-stage reconciliation changed stage")
        resolved = self._closed_attempt_outcome(chain)
        if resolved is None:
            return None
        disposition = _read_disposition(
            self._attempt_root(chain.chain_id, chain.attempts[-1].attempt_number)
            / "DISPOSITION.json"
        )
        assert disposition is not None
        return ProviderStageAmbiguityResolutionV1(
            resolution_evidence_sha256=domain_sha256(
                "cera.adult_provider_stage_reconciliation.v1",
                {
                    "chain_id": chain.chain_id,
                    "attempt_number": chain.attempts[-1].attempt_number,
                    "disposition_sha256": canonical_sha256(disposition),
                    "ledger_prefix_after_sha256": (resolved.metrics.ledger_prefix_after_sha256),
                },
            ),
            disposition=resolved,
        )

    def recover_interrupted_dispatch(
        self,
        *,
        chain: ProviderStageRetryChainV1,
    ) -> ProviderStageAttemptOutcomeV1:
        """Recover a closed disposition or fence an unresolved dispatch claim."""

        if (
            chain.identity.stage is not self.stage
            or chain.phase is not ProviderStageRetryPhase.DISPATCH_STARTED
            or not chain.attempts
        ):
            raise StateConflictError("adult interrupted dispatch changed custody")
        resolved = self._closed_attempt_outcome(chain)
        if resolved is not None:
            return resolved
        attempt = chain.attempts[-1]
        try:
            before = self._ledger_snapshot_for_prefix(attempt.ledger_prefix_before_sha256)
            after = self._current_ledger_snapshot()
            observed = after.provider_operations_total - before.provider_operations_total
            if observed < 0:
                raise StateConflictError("adult interrupted ledger moved backwards")
            ledger_after_sha256 = after.prefix_sha256
        except StateConflictError:
            observed = 0
            ledger_after_sha256 = attempt.ledger_prefix_before_sha256
        maximum = self.pi_adapter.operation_ledger.maximum_operations_per_invocation
        return ProviderStageDispatchAmbiguousV1(
            failure_evidence_sha256=domain_sha256(
                "cera.adult_provider_stage_interrupted_dispatch.v1",
                {
                    "chain_id": chain.chain_id,
                    "attempt_number": attempt.attempt_number,
                    "stage": self.stage.value,
                    "ledger_prefix_before_sha256": (attempt.ledger_prefix_before_sha256),
                    "ledger_prefix_after_sha256": ledger_after_sha256,
                    "provider_operations_observed": observed,
                    "maximum_provider_operations": maximum,
                },
            ),
            metrics=ProviderStageAttemptMetricsV1(
                ledger_prefix_after_sha256=ledger_after_sha256,
                provider_operations_observed=observed,
                provider_operations_conservative=max(observed, maximum),
                duration_ms=0,
            ),
        )

    def _closed_attempt_outcome(
        self,
        chain: ProviderStageRetryChainV1,
    ) -> (
        ProviderStageSuccessfulResultV1
        | ProviderStageClosedFailureV1
        | ProviderStageNonRetryableFailureV1
        | None
    ):
        if chain.identity.stage is not self.stage or not chain.attempts:
            raise StateConflictError("adult provider-stage reconciliation changed stage")
        attempt = chain.attempts[-1]
        root = self._attempt_root(chain.chain_id, attempt.attempt_number)
        disposition = _read_disposition(root / "DISPOSITION.json")
        if disposition is None or (
            disposition.chain_id != chain.chain_id
            or disposition.attempt_number != attempt.attempt_number
            or disposition.stage is not self.stage
        ):
            return None
        metrics = self._reconciled_metrics(disposition, attempt)
        if metrics is None:
            return None
        if disposition.disposition == "success":
            result_path = root / "PROVIDER_STAGE_RESULT.json"
            try:
                exact_result = result_path.read_bytes()
                semantic = _semantic_disposition_for_result(self.stage, exact_result)
            except (OSError, ContractValidationError, StateConflictError):
                return None
            if (
                bytes_sha256(exact_result) != disposition.exact_result_sha256
                or semantic is not disposition.semantic_disposition
                or disposition.result_evidence_sha256 is None
            ):
                return None
            return ProviderStageSuccessfulResultV1(
                exact_result=exact_result,
                result_evidence_sha256=disposition.result_evidence_sha256,
                metrics=metrics,
                semantic_disposition=semantic,
            )
        if disposition.failure_class is None or disposition.failure_evidence_sha256 is None:
            return None
        if disposition.disposition == "retryable_failure":
            return ProviderStageClosedFailureV1(
                failure_class=disposition.failure_class,
                failure_evidence_sha256=disposition.failure_evidence_sha256,
                metrics=metrics,
            )
        return ProviderStageNonRetryableFailureV1(
            failure_class=disposition.failure_class,
            failure_evidence_sha256=disposition.failure_evidence_sha256,
            metrics=metrics,
        )

    def _reconciled_metrics(
        self,
        disposition: _AdultAttemptDispositionV1,
        attempt: ProviderStageAttemptV1,
    ) -> ProviderStageAttemptMetricsV1 | None:
        stored = _attempt_metrics_from_disposition(disposition)
        invocation_id = disposition.provider_invocation_id
        if invocation_id is None:
            if (
                stored.provider_operations_observed != 0
                or stored.provider_operations_conservative != 0
                or stored.ledger_prefix_after_sha256 != attempt.ledger_prefix_before_sha256
                or any(
                    value is not None
                    for value in (
                        stored.input_tokens,
                        stored.cached_input_tokens,
                        stored.output_tokens,
                        stored.reasoning_tokens,
                    )
                )
            ):
                return None
            return stored
        try:
            ledger_metrics = self.pi_adapter.operation_ledger.invocation_metrics(invocation_id)
            exact = _attempt_metrics_from_ledger(ledger_metrics)
        except (ContractValidationError, StateConflictError):
            return None
        if exact != stored or not self._invocation_is_attempt_local(
            invocation_id,
            attempt.ledger_prefix_before_sha256,
            exact.ledger_prefix_after_sha256,
        ):
            return None
        if (
            disposition.disposition == "retryable_failure"
            and ledger_metrics.failure_category is not None
            and ledger_metrics.failure_category != disposition.failure_category
        ):
            return None
        return exact

    def _invocation_is_attempt_local(
        self,
        invocation_id: str,
        prefix_before_sha256: str,
        prefix_after_sha256: str,
    ) -> bool:
        try:
            before = self._ledger_prefix_length(prefix_before_sha256)
            after = self._ledger_prefix_length(prefix_after_sha256)
        except StateConflictError:
            return False
        if after < before:
            return False
        events = self.pi_adapter.operation_ledger.events[before:after]
        prepared = tuple(
            event
            for event in events
            if event.get("event") == "invocation_prepared"
            and event.get("purpose") == self.stage.value
        )
        return len(prepared) == 1 and prepared[0].get("invocation_id") == invocation_id

    def _owner(
        self,
        *,
        chain_id: str,
        attempt_number: int,
        exact_input: bytes,
        ledger_before: ProviderStageLedgerSnapshotV1,
    ) -> ProviderStageAttemptOwnerPort:
        attempt_root = self._attempt_root(chain_id, attempt_number)
        session_scope_sha256 = domain_sha256(
            "cera.adult_provider_stage_owner_scope.v1",
            {
                "chain_id": chain_id,
                "attempt_number": attempt_number,
                "stage": self.stage.value,
                "stage_input_sha256": bytes_sha256(exact_input),
                "attempt_root": str(attempt_root),
                "provider": self.pi_adapter.provider,
                "model": self.pi_adapter.model,
            },
        )

        def build_request(
            value: bytes,
        ) -> _AdultSceneAttemptRequestV1 | _AdultFilterAttemptRequestV1:
            assert_provider_dispatch_allowed(
                f"pi_scene.provider_stage_retry.{self.stage.value}",
                external_provider_boundary=True,
            )
            self._assert_budget_available()
            return (
                _decode_scene_attempt_request(value, self.pi_adapter)
                if self.stage is ProviderStage.ADULT_SCENE
                else _decode_filter_attempt_request(value, self.pi_adapter)
            )

        provider_calls_observed = 0

        def invoke_provider(
            request: _AdultSceneAttemptRequestV1 | _AdultFilterAttemptRequestV1,
        ) -> AdultSceneRoleExecutionV1 | AdultFilterRoleExecutionV1:
            nonlocal provider_calls_observed
            try:
                if self.stage is ProviderStage.ADULT_SCENE:
                    if not isinstance(request, _AdultSceneAttemptRequestV1):
                        raise ContractValidationError("adult Scene attempt request changed")
                    result: AdultSceneRoleExecutionV1 | AdultFilterRoleExecutionV1 = (
                        self._invoke_scene(request, attempt_root)
                    )
                else:
                    if not isinstance(request, _AdultFilterAttemptRequestV1):
                        raise ContractValidationError("adult Filter attempt request changed")
                    result = self._invoke_filter(request, attempt_root)
            except ProviderTransportError as exc:
                provider_calls_observed = exc.external_provider_calls_observed
                raise
            provider_calls_observed = 1
            exact_result = canonical_bytes(result)
            _write_protected_once(
                attempt_root / "PROVIDER_STAGE_RESULT.json",
                exact_result,
            )
            return result

        def serialize_result(
            result: AdultSceneRoleExecutionV1 | AdultFilterRoleExecutionV1,
        ) -> bytes:
            return canonical_bytes(result)

        def receipt_metrics(
            result: AdultSceneRoleExecutionV1 | AdultFilterRoleExecutionV1,
        ) -> ProviderStageReceiptMetricsV1:
            return self._receipt_metrics(result)

        def semantic_disposition(
            result: AdultSceneRoleExecutionV1 | AdultFilterRoleExecutionV1,
        ) -> ProviderStageSemanticDisposition:
            if isinstance(result, AdultSceneRoleExecutionV1):
                return ProviderStageSemanticDisposition.NOT_APPLICABLE
            return (
                ProviderStageSemanticDisposition.ACCEPTED
                if result.invocation.decision.verdict is AdultFilterVerdict.PASS
                else ProviderStageSemanticDisposition.REJECTED
            )

        def retire_owner(request: ProviderStageOwnerRetirementV1) -> str:
            evidence = domain_sha256(
                "cera.adult_provider_stage_one_shot_owner_retired.v1",
                {
                    "chain_id": request.chain_id,
                    "attempt_number": request.attempt_number,
                    "stage": request.stage.value,
                    "session_scope_sha256": request.session_scope_sha256,
                    "failure_class": request.failure_class.value,
                    "one_shot_process_has_no_live_handle": True,
                },
            )
            _write_protected_once(
                attempt_root / "OWNER_RETIRED.json",
                canonical_bytes({"retirement_evidence_sha256": evidence}),
            )
            return evidence

        owner = CallableProviderStageAttemptOwnerV1(
            binding=ProviderStageAttemptOwnerBindingV1(
                chain_id=chain_id,
                attempt_number=attempt_number,
                stage=self.stage,
                boundary_kind=ProviderStageBoundaryKind.PI_DEEPSEEK,
                stage_input_sha256=bytes_sha256(exact_input),
                session_scope_sha256=session_scope_sha256,
                ledger_before=ledger_before,
                maximum_provider_operations=(
                    self.pi_adapter.operation_ledger.maximum_operations_per_invocation
                ),
            ),
            read_session_scope_sha256=lambda: session_scope_sha256,
            read_ledger_snapshot=self._current_ledger_snapshot,
            build_request=build_request,
            invoke_provider=invoke_provider,
            serialize_result=serialize_result,
            result_receipt_metrics=receipt_metrics,
            semantic_disposition=semantic_disposition,
            retire_owner=retire_owner,
            classify_non_retryable=_classify_adult_non_retryable,
        )
        return _AdultOutcomeRecordingOwnerV1(
            owner,
            lambda outcome: self._record_attempt_outcome(
                outcome,
                chain_id=chain_id,
                attempt_number=attempt_number,
                attempt_root=attempt_root,
                ledger_before=ledger_before,
                provider_calls_observed=provider_calls_observed,
            ),
        )

    def _record_attempt_outcome(
        self,
        outcome: ProviderStageAttemptOutcomeV1,
        *,
        chain_id: str,
        attempt_number: int,
        attempt_root: Path,
        ledger_before: ProviderStageLedgerSnapshotV1,
        provider_calls_observed: int,
    ) -> ProviderStageAttemptOutcomeV1:
        normalized, invocation_id = self._normalize_attempt_outcome(
            outcome,
            attempt_root=attempt_root,
            ledger_before=ledger_before,
        )
        if isinstance(normalized, ProviderStageDispatchAmbiguousV1):
            return normalized
        metrics = normalized.metrics
        if isinstance(normalized, ProviderStageSuccessfulResultV1):
            disposition_name = "success"
            failure_category = None
            failure_class = None
            failure_evidence_sha256 = None
            exact_result_sha256 = bytes_sha256(normalized.exact_result)
            result_evidence_sha256 = normalized.result_evidence_sha256
            semantic_disposition = normalized.semantic_disposition
        elif isinstance(normalized, ProviderStageClosedFailureV1):
            disposition_name = "retryable_failure"
            failure_category = _CATEGORY_BY_RETRYABLE_CLASS[normalized.failure_class]
            failure_class = normalized.failure_class
            failure_evidence_sha256 = normalized.failure_evidence_sha256
            exact_result_sha256 = None
            result_evidence_sha256 = None
            semantic_disposition = None
        else:
            disposition_name = "non_retryable_failure"
            failure_category = None
            failure_class = normalized.failure_class
            failure_evidence_sha256 = normalized.failure_evidence_sha256
            exact_result_sha256 = None
            result_evidence_sha256 = None
            semantic_disposition = None
        disposition = _AdultAttemptDispositionV1(
            schema_version=_AdultAttemptDispositionV1.SCHEMA_VERSION,
            chain_id=chain_id,
            attempt_number=attempt_number,
            stage=self.stage,
            disposition=disposition_name,
            provider_invocation_id=invocation_id,
            provider_calls_observed=provider_calls_observed,
            provider_operations_observed=metrics.provider_operations_observed,
            provider_operations_conservative=metrics.provider_operations_conservative,
            ledger_prefix_after_sha256=metrics.ledger_prefix_after_sha256,
            duration_ms=metrics.duration_ms,
            input_tokens=metrics.input_tokens,
            cached_input_tokens=metrics.cached_input_tokens,
            output_tokens=metrics.output_tokens,
            reasoning_tokens=metrics.reasoning_tokens,
            failure_category=failure_category,
            failure_class=failure_class,
            failure_evidence_sha256=failure_evidence_sha256,
            exact_result_sha256=exact_result_sha256,
            result_evidence_sha256=result_evidence_sha256,
            semantic_disposition=semantic_disposition,
        )
        _write_disposition(attempt_root / "DISPOSITION.json", disposition)
        return normalized

    def _normalize_attempt_outcome(
        self,
        outcome: ProviderStageAttemptOutcomeV1,
        *,
        attempt_root: Path,
        ledger_before: ProviderStageLedgerSnapshotV1,
    ) -> tuple[ProviderStageAttemptOutcomeV1, str | None]:
        if isinstance(outcome, ProviderStageDispatchAmbiguousV1):
            return outcome, None
        invocation_id = self._new_invocation_id(ledger_before, stage=self.stage)
        if invocation_id is None:
            metrics = outcome.metrics
            if (
                isinstance(outcome, ProviderStageSuccessfulResultV1)
                or metrics.provider_operations_observed != 0
                or metrics.provider_operations_conservative != 0
                or metrics.ledger_prefix_after_sha256 != ledger_before.prefix_sha256
            ):
                return self._outcome_metrics_ambiguity(
                    ledger_before,
                    reason="missing_invocation_identity",
                ), None
            return outcome, None
        try:
            ledger = self.pi_adapter.operation_ledger.invocation_metrics(invocation_id)
            exact_metrics = _attempt_metrics_from_ledger(ledger)
            if not self._invocation_is_attempt_local(
                invocation_id,
                ledger_before.prefix_sha256,
                exact_metrics.ledger_prefix_after_sha256,
            ):
                raise StateConflictError(
                    "adult provider-stage invocation escaped attempt ledger span"
                )
        except (ContractValidationError, StateConflictError):
            return self._outcome_metrics_ambiguity(
                ledger_before,
                reason="invocation_metrics_unavailable",
            ), None
        if isinstance(outcome, ProviderStageSuccessfulResultV1):
            try:
                exact_result = (attempt_root / "PROVIDER_STAGE_RESULT.json").read_bytes()
                role_invocation_id = (
                    _decode_scene_execution(exact_result).provider_invocation_id
                    if self.stage is ProviderStage.ADULT_SCENE
                    else _decode_filter_execution(exact_result).provider_invocation_id
                )
            except (OSError, ContractValidationError, StateConflictError):
                return self._outcome_metrics_ambiguity(
                    ledger_before,
                    reason="protected_result_unavailable",
                ), None
            if (
                exact_result != outcome.exact_result
                or role_invocation_id != invocation_id
                or ledger.status != "completed"
                or ledger.provider_operations_started != ledger.provider_operations_completed
            ):
                return self._outcome_metrics_ambiguity(
                    ledger_before,
                    reason="result_ledger_mismatch",
                ), None
            return (
                ProviderStageSuccessfulResultV1(
                    exact_result=exact_result,
                    result_evidence_sha256=outcome.result_evidence_sha256,
                    metrics=exact_metrics,
                    semantic_disposition=outcome.semantic_disposition,
                ),
                invocation_id,
            )
        if isinstance(outcome, ProviderStageClosedFailureV1):
            category = _CATEGORY_BY_RETRYABLE_CLASS[outcome.failure_class]
            if ledger.failure_category is not None and ledger.failure_category != category:
                return self._outcome_metrics_ambiguity(
                    ledger_before,
                    reason="failure_ledger_mismatch",
                ), None
            return (
                ProviderStageClosedFailureV1(
                    failure_class=outcome.failure_class,
                    failure_evidence_sha256=outcome.failure_evidence_sha256,
                    metrics=exact_metrics,
                ),
                invocation_id,
            )
        return (
            ProviderStageNonRetryableFailureV1(
                failure_class=outcome.failure_class,
                failure_evidence_sha256=outcome.failure_evidence_sha256,
                metrics=exact_metrics,
            ),
            invocation_id,
        )

    def _outcome_metrics_ambiguity(
        self,
        ledger_before: ProviderStageLedgerSnapshotV1,
        *,
        reason: str,
    ) -> ProviderStageDispatchAmbiguousV1:
        try:
            after = self._current_ledger_snapshot()
            observed = after.provider_operations_total - ledger_before.provider_operations_total
            if observed < 0:
                raise StateConflictError("adult provider-stage ledger moved backwards")
            prefix_after = after.prefix_sha256
        except StateConflictError:
            observed = 0
            prefix_after = ledger_before.prefix_sha256
        maximum = self.pi_adapter.operation_ledger.maximum_operations_per_invocation
        return ProviderStageDispatchAmbiguousV1(
            failure_evidence_sha256=domain_sha256(
                "cera.adult_provider_stage_metrics_ambiguity.v1",
                {
                    "stage": self.stage.value,
                    "ledger_prefix_before_sha256": ledger_before.prefix_sha256,
                    "ledger_prefix_after_sha256": prefix_after,
                    "provider_operations_observed": observed,
                    "maximum_provider_operations": maximum,
                    "reason": reason,
                },
            ),
            metrics=ProviderStageAttemptMetricsV1(
                ledger_prefix_after_sha256=prefix_after,
                provider_operations_observed=observed,
                provider_operations_conservative=max(observed, maximum),
                duration_ms=0,
            ),
        )

    def _invoke_scene(
        self,
        request: _AdultSceneAttemptRequestV1,
        attempt_root: Path,
    ) -> AdultSceneRoleExecutionV1:
        port = PiDeepSeekAdultScenePort(
            transport=PiStructuredAdultRoleTransport(self.pi_adapter),
            # The immutable candidate view is shared across fresh attempt
            # owners. Keeping this root shallow also avoids Win32 path loss.
            materializer=LazyProtectedWriterViewMaterializer(self.protected_runtime_root / "v"),
            context=request.role_context,
            session_root=attempt_root / "s",
            accepted_parent_session=request.accepted_parent_session,
        )
        return port.execute_adult_scene(request.scene_request)

    def _invoke_filter(
        self,
        request: _AdultFilterAttemptRequestV1,
        attempt_root: Path,
    ) -> AdultFilterRoleExecutionV1:
        port = PiDeepSeekAdultFilterPort(
            transport=PiStructuredAdultRoleTransport(self.pi_adapter),
            materializer=LazyProtectedWriterViewMaterializer(self.protected_runtime_root / "v"),
            context=request.role_context,
            session_root=attempt_root / "s",
        )
        return port.execute_adult_filter(request.filter_request)

    def _receipt_metrics(
        self,
        result: AdultSceneRoleExecutionV1 | AdultFilterRoleExecutionV1,
    ) -> ProviderStageReceiptMetricsV1:
        metrics = self.pi_adapter.operation_ledger.invocation_metrics(result.provider_invocation_id)
        receipt = result.invocation.receipt
        request_binding_sha256 = (
            result.session_binding.transport_request_binding_sha256
            if isinstance(result, AdultSceneRoleExecutionV1)
            else result.execution_binding.transport_request_binding_sha256
        )
        if (
            metrics.status != "completed"
            or metrics.request_sha256 != request_binding_sha256
            or metrics.provider_operations_started != receipt.provider_operations
            or metrics.provider_operations_completed != receipt.provider_operations
        ):
            raise StateConflictError("adult provider-stage receipt differs from Pi ledger")
        return ProviderStageReceiptMetricsV1(
            receipt_evidence_sha256=domain_sha256(
                "cera.adult_provider_stage_pi_receipt.v1",
                {
                    "provider_invocation_id_sha256": text_sha256(result.provider_invocation_id),
                    "provider_receipt_sha256": canonical_sha256(receipt),
                    "ledger_span_sha256": metrics.span_sha256,
                },
            ),
            provider_operations=metrics.provider_operations_started,
            duration_ms=metrics.duration_ms,
            input_tokens=metrics.input_tokens,
            cached_input_tokens=metrics.cached_input_tokens,
            output_tokens=metrics.output_tokens,
            reasoning_tokens=metrics.reasoning_tokens,
        )

    def _assert_budget_available(self) -> None:
        ledger = self.pi_adapter.operation_ledger
        if (
            ledger.operation_count + ledger.maximum_operations_per_invocation
            > ledger.maximum_operations
        ):
            raise ProviderTransportError(
                ErrorCode.PROVIDER_BUDGET_EXCEEDED,
                "Pi adult role provider-operation budget is exhausted",
                external_provider_calls_observed=0,
            )

    def _current_ledger_snapshot(self) -> ProviderStageLedgerSnapshotV1:
        events = self.pi_adapter.operation_ledger.events
        return ProviderStageLedgerSnapshotV1(
            prefix_sha256=canonical_sha256(events),
            provider_operations_total=sum(
                event.get("event") == "provider_operation_started" for event in events
            ),
        )

    def _ledger_snapshot_for_prefix(
        self,
        prefix_sha256: str,
    ) -> ProviderStageLedgerSnapshotV1:
        events = self.pi_adapter.operation_ledger.events
        length = self._ledger_prefix_length(prefix_sha256)
        prefix = events[:length]
        return ProviderStageLedgerSnapshotV1(
            prefix_sha256=prefix_sha256,
            provider_operations_total=sum(
                event.get("event") == "provider_operation_started" for event in prefix
            ),
        )

    def _new_invocation_id(
        self,
        ledger_before: ProviderStageLedgerSnapshotV1,
        *,
        stage: ProviderStage,
    ) -> str | None:
        try:
            prefix_length = self._ledger_prefix_length(ledger_before.prefix_sha256)
        except StateConflictError:
            return None
        events = self.pi_adapter.operation_ledger.events[prefix_length:]
        matching = tuple(
            cast(str, event["invocation_id"])
            for event in events
            if event.get("event") == "invocation_prepared"
            and event.get("purpose") == stage.value
            and isinstance(event.get("invocation_id"), str)
        )
        return matching[0] if len(matching) == 1 else None

    def _ledger_prefix_length(self, prefix_sha256: str) -> int:
        events = self.pi_adapter.operation_ledger.events
        for length in range(len(events) + 1):
            if canonical_sha256(events[:length]) == prefix_sha256:
                return length
        raise StateConflictError("adult provider-stage ledger prefix is unavailable")

    def _attempt_root(self, chain_id: str, attempt_number: int) -> Path:
        if not chain_id.startswith("stage-retry-") or not 1 <= attempt_number <= 3:
            raise ContractValidationError("adult provider-stage attempt identity is invalid")
        chain_sha256 = chain_id.removeprefix("stage-retry-")
        chain_root = (self.protected_runtime_root / "psr" / chain_sha256[:24]).resolve()
        if not chain_root.is_relative_to(self.protected_runtime_root):
            raise ContractValidationError("adult provider-stage chain escaped custody")
        _write_protected_once(
            chain_root / "CHAIN.json",
            canonical_bytes({"chain_id": chain_id}),
        )
        root = (chain_root / f"a{attempt_number}").resolve()
        if not root.is_relative_to(self.protected_runtime_root):
            raise ContractValidationError("adult provider-stage attempt escaped custody")
        return root


class AdultStageResultBinderV1:
    """Idempotent no-publication binder for one protected adult stage result."""

    def __init__(self, stage: ProviderStage) -> None:
        if stage not in _ADULT_STAGES:
            raise ContractValidationError("adult result binder changed stage")
        self.stage = stage

    def downstream_intent_sha256(
        self,
        *,
        chain: ProviderStageRetryChainV1,
        exact_result: bytes,
    ) -> str:
        _semantic_disposition_for_result(self.stage, exact_result)
        return domain_sha256(
            "cera.adult_provider_stage_downstream_intent.v1",
            {
                "chain_id": chain.chain_id,
                "stage": self.stage.value,
                "result_sha256": bytes_sha256(exact_result),
                "publication": "protected_pipeline_continuation_only",
            },
        )

    def bind_once(
        self,
        *,
        chain: ProviderStageRetryChainV1,
        exact_result: bytes,
        downstream_intent_sha256: str,
    ) -> str:
        expected = self.downstream_intent_sha256(chain=chain, exact_result=exact_result)
        if downstream_intent_sha256 != expected:
            raise StateConflictError("adult provider-stage downstream intent changed")
        return domain_sha256(
            "cera.adult_provider_stage_downstream_bound.v1",
            {
                "chain_id": chain.chain_id,
                "stage": self.stage.value,
                "downstream_intent_sha256": downstream_intent_sha256,
            },
        )


def adult_stage_retry_runtime_adapters(
    *,
    pi_adapter: PiSceneAdapter,
    protected_runtime_root: Path,
) -> tuple[ProviderStageRuntimeAdapterV1, ProviderStageRuntimeAdapterV1]:
    """Build the two registrations merged into the global six-stage registry."""

    registrations: list[ProviderStageRuntimeAdapterV1] = []
    for stage in (ProviderStage.ADULT_SCENE, ProviderStage.ADULT_FILTER):
        factory = AdultPiStageAttemptOwnerFactoryV1(
            stage=stage,
            pi_adapter=pi_adapter,
            protected_runtime_root=protected_runtime_root,
        )
        registrations.append(
            ProviderStageRuntimeAdapterV1(
                stage=stage,
                owner_factory=factory,
                ambiguity_reconciler=factory,
                downstream_binder=AdultStageResultBinderV1(stage),
            )
        )
    return cast(
        tuple[ProviderStageRuntimeAdapterV1, ProviderStageRuntimeAdapterV1],
        tuple(registrations),
    )


def _decode_scene_attempt_request(
    exact_input: bytes,
    pi_adapter: PiSceneAdapter,
) -> _AdultSceneAttemptRequestV1:
    semantic = _semantic_input(
        exact_input,
        stage=ProviderStage.ADULT_SCENE,
        pi_adapter=pi_adapter,
    )
    scene_request = _decode_dataclass(
        AdultSceneRequestV1,
        semantic.get("scene_request"),
        "Adult Scene request",
    )
    evidence = _object(semantic.get("evidence_bundle"), "Adult Scene evidence bundle")
    role_context = _decode_dataclass(
        AdultRoleViewContextV1,
        evidence.get("role_context"),
        "Adult Scene role context",
    )
    parent_payload = evidence.get("accepted_parent_session")
    parent = (
        None
        if parent_payload is None
        else _decode_dataclass(
            AcceptedPiSessionV1,
            parent_payload,
            "Adult Scene accepted parent session",
        )
    )
    return _AdultSceneAttemptRequestV1(
        scene_request=scene_request,
        role_context=role_context,
        accepted_parent_session=parent,
    )


def _decode_filter_attempt_request(
    exact_input: bytes,
    pi_adapter: PiSceneAdapter,
) -> _AdultFilterAttemptRequestV1:
    semantic = _semantic_input(
        exact_input,
        stage=ProviderStage.ADULT_FILTER,
        pi_adapter=pi_adapter,
    )
    frozen = _object(
        semantic.get("frozen_scene_result"),
        "Adult Filter frozen Scene result",
    )
    if (
        frozen.get("schema_version") != FrozenProviderStageResultV1.SCHEMA_VERSION
        or frozen.get("source_stage") != ProviderStage.ADULT_SCENE.value
        or not isinstance(frozen.get("result_sha256"), str)
        or not isinstance(frozen.get("exact_result_base64"), str)
    ):
        raise ContractValidationError("Adult Filter frozen Scene result changed")
    try:
        scene_exact = base64.b64decode(
            cast(str, frozen["exact_result_base64"]),
            validate=True,
        )
    except (ValueError, TypeError) as exc:
        raise ContractValidationError("Adult Filter frozen Scene bytes are invalid") from exc
    if bytes_sha256(scene_exact) != frozen["result_sha256"]:
        raise StateConflictError("Adult Filter frozen Scene result hash changed")
    scene_execution = _decode_scene_execution(scene_exact)
    context = _object(semantic.get("filter_context"), "Adult Filter context")
    scene_request = _decode_dataclass(
        AdultSceneRequestV1,
        context.get("scene_request"),
        "Adult Filter Scene request",
    )
    if scene_execution.invocation.receipt.request_sha256 != canonical_sha256(scene_request):
        raise StateConflictError("Adult Filter Scene request changed frozen result custody")
    role_context = _decode_dataclass(
        AdultRoleViewContextV1,
        context.get("role_context"),
        "Adult Filter role context",
    )
    filter_request = AdultFilterRequestV1(
        schema_version=AdultFilterRequestV1.SCHEMA_VERSION,
        scene_request=scene_request,
        scene_output=scene_execution.invocation.output,
    )
    return _AdultFilterAttemptRequestV1(
        filter_request=filter_request,
        role_context=role_context,
    )


def _semantic_input(
    exact_input: bytes,
    *,
    stage: ProviderStage,
    pi_adapter: PiSceneAdapter,
) -> dict[str, Any]:
    payload = _canonical_object(exact_input, f"{stage.value} packet")
    if (
        payload.get("schema_version") != ProviderStageFrozenPacketV1.SCHEMA_VERSION
        or payload.get("stage") != stage.value
        or payload.get("packet_kind") != stage.value
    ):
        raise ContractValidationError("adult provider-stage packet envelope changed")
    configuration = _object(
        payload.get("provider_configuration"),
        "adult provider-stage configuration",
    )
    if (
        configuration.get("stage") != stage.value
        or configuration.get("provider") != "deepseek"
        or configuration.get("model_family") != "deepseek_v4"
        or configuration.get("model_id") != pi_adapter.model
        or configuration.get("reasoning_mode") != "off"
    ):
        raise ContractValidationError("adult provider-stage frozen configuration changed")
    return _object(payload.get("semantic_input"), "adult provider-stage semantic input")


def _decode_scene_execution(exact_result: bytes) -> AdultSceneRoleExecutionV1:
    return _decode_dataclass(
        AdultSceneRoleExecutionV1,
        _canonical_object(exact_result, "Adult Scene stage result"),
        "Adult Scene stage result",
    )


def _decode_filter_execution(exact_result: bytes) -> AdultFilterRoleExecutionV1:
    return _decode_dataclass(
        AdultFilterRoleExecutionV1,
        _canonical_object(exact_result, "Adult Filter stage result"),
        "Adult Filter stage result",
    )


def _semantic_disposition_for_result(
    stage: ProviderStage,
    exact_result: bytes,
) -> ProviderStageSemanticDisposition:
    if stage is ProviderStage.ADULT_SCENE:
        _decode_scene_execution(exact_result)
        return ProviderStageSemanticDisposition.NOT_APPLICABLE
    if stage is ProviderStage.ADULT_FILTER:
        result = _decode_filter_execution(exact_result)
        return (
            ProviderStageSemanticDisposition.ACCEPTED
            if result.invocation.decision.verdict is AdultFilterVerdict.PASS
            else ProviderStageSemanticDisposition.REJECTED
        )
    raise ContractValidationError("adult provider-stage result changed stage")


def _attempt_metrics_from_ledger(
    metrics: PiProviderInvocationMetricsV1,
) -> ProviderStageAttemptMetricsV1:
    return ProviderStageAttemptMetricsV1(
        ledger_prefix_after_sha256=metrics.ledger_prefix_after_sha256,
        provider_operations_observed=metrics.provider_operations_started,
        provider_operations_conservative=metrics.provider_operations_started,
        duration_ms=metrics.duration_ms,
        input_tokens=metrics.input_tokens,
        cached_input_tokens=metrics.cached_input_tokens,
        output_tokens=metrics.output_tokens,
        reasoning_tokens=metrics.reasoning_tokens,
    )


def _attempt_metrics_from_disposition(
    disposition: _AdultAttemptDispositionV1,
) -> ProviderStageAttemptMetricsV1:
    return ProviderStageAttemptMetricsV1(
        ledger_prefix_after_sha256=disposition.ledger_prefix_after_sha256,
        provider_operations_observed=disposition.provider_operations_observed,
        provider_operations_conservative=(disposition.provider_operations_conservative),
        duration_ms=disposition.duration_ms,
        input_tokens=disposition.input_tokens,
        cached_input_tokens=disposition.cached_input_tokens,
        output_tokens=disposition.output_tokens,
        reasoning_tokens=disposition.reasoning_tokens,
    )


def _classify_adult_non_retryable(
    failure: ProviderTransportError,
) -> ProviderStageFailureClass | None:
    if isinstance(failure, AdultPiOutputLimitError):
        return ProviderStageFailureClass.OUTPUT_LIMIT_TRUNCATED
    if failure.code is ErrorCode.PROVIDER_CONFIG_INVALID:
        return ProviderStageFailureClass.CONFIGURATION_FAILED
    if failure.code is ErrorCode.PROVIDER_BUDGET_EXCEEDED:
        return ProviderStageFailureClass.BUDGET_EXHAUSTED
    return None


def _canonical_object(exact: bytes, label: str) -> dict[str, Any]:
    try:
        value: object = json.loads(exact.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractValidationError(f"{label} is not canonical UTF-8 JSON") from exc
    if not isinstance(value, dict) or canonical_bytes(value) != exact:
        raise ContractValidationError(f"{label} is not one canonical JSON object")
    return cast(dict[str, Any], value)


def _read_typed_artifact[ValueT](
    path: Path,
    target: type[ValueT],
    label: str,
) -> ValueT:
    try:
        payload = _canonical_object(path.read_bytes(), label)
    except OSError as exc:
        raise StateConflictError(f"{label} is unavailable") from exc
    return cast(ValueT, from_mapping(target, payload))


def _require_stage_chain_id(chain_id: str) -> None:
    if (
        not isinstance(chain_id, str)
        or not chain_id.startswith("stage-retry-")
        or not re_is_sha256(chain_id.removeprefix("stage-retry-"))
    ):
        raise ContractValidationError("adult continuation stage chain ID is invalid")


def _object(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ContractValidationError(f"{label} must be an object")
    return cast(dict[str, Any], value)


def _decode_dataclass[ValueT](
    target: type[ValueT],
    value: object,
    label: str,
) -> ValueT:
    if not isinstance(value, dict):
        raise ContractValidationError(f"{label} must be an object")
    return cast(ValueT, from_mapping(target, value))


def _write_disposition(path: Path, disposition: _AdultAttemptDispositionV1) -> None:
    _write_protected_once(path, canonical_bytes(disposition))


def _read_disposition(path: Path) -> _AdultAttemptDispositionV1 | None:
    if not path.is_file():
        return None
    try:
        payload = _canonical_object(path.read_bytes(), "adult provider-stage disposition")
        return cast(
            _AdultAttemptDispositionV1,
            from_mapping(_AdultAttemptDispositionV1, payload),
        )
    except (OSError, ContractValidationError):
        return None


def _write_protected_once(path: Path, exact_bytes: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        if path.read_bytes() != exact_bytes:
            raise StateConflictError("adult protected provider-stage artifact changed")
        return
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(exact_bytes)
            stream.flush()
            os.fsync(stream.fileno())
        if path.is_file():
            if path.read_bytes() != exact_bytes:
                raise StateConflictError("adult protected provider-stage artifact changed")
            return
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


__all__ = [
    "AdultPiStageAttemptOwnerFactoryV1",
    "AdultStageResultBinderV1",
    "AdultStageRetryContinuationServiceV1",
    "AdultStageRetryCoordinatorV1",
    "AdultStageRetryExecutionInputV1",
    "AdultStageRetryPacketFactoryV1",
    "AdultStageRetryProgressV1",
    "adult_stage_retry_runtime_adapters",
]
