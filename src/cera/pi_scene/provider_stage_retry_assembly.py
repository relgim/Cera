"""Production assembly for the stage-local provider Retry owners.

Construction in this module is deliberately provider-free.  Provider
lifecycle creation and model/Pi dispatch remain reachable only from an
executor-authorized attempt's ``invoke`` method.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, ClassVar, cast
from uuid import uuid4

from cera.adult_pipeline.integration import AdultPipelineIntegrationV1
from cera.adult_pipeline.pi_roles import AdultRoleViewContextV1
from cera.adult_pipeline.pipeline import AdultPipelineInputV1
from cera.continuous.call_ledger import ContinuousProviderCallLedger, ProviderCallState
from cera.continuous.provider import ContinuousProviderResultV1
from cera.errors import ContractValidationError, StateConflictError
from cera.providers.models import ProviderTransportError
from cera.reader_validation import BoundReaderValidationV1
from cera.semantic_validation import BoundSemanticValidationV1
from cera.serialization import (
    bytes_sha256,
    canonical_bytes,
    canonical_sha256,
    domain_sha256,
    re_is_sha256,
    text_sha256,
    to_primitive,
)
from cera.storage.sqlite_store import SQLiteAuthorityStore

from .adult_operation_store import ProtectedAdultOperationStore
from .adult_orchestration import (
    AdultRouteOperationOutcomeV1,
    AutomaticAdultRouteOrchestrator,
    PreparedAdultRouteOperationV1,
    classify_prepared_adult_execution,
)
from .adult_stage_retry_integration import (
    AdultStageRetryContinuationServiceV1,
    AdultStageRetryExecutionInputV1,
    AdultStageRetryPacketFactoryV1,
    adult_stage_retry_runtime_adapters,
)
from .full_model_controller import FullModelSceneController
from .http import PiSceneHttpAdapter
from .operation_ledger import PiProviderOperationLedger
from .pi_adapter import PiOutputLimitError, PiSceneAdapter, PiSceneInvocationResultV1
from .provider_stage_retry import (
    NON_RETRYABLE_PROVIDER_STAGE_FAILURES,
    RETRYABLE_PROVIDER_STAGE_FAILURES,
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
    ProviderStageNonRetryableClassifierPort,
    ProviderStageOwnerRetirementV1,
    ProviderStagePreparedCallableDispatchV1,
    ProviderStageReceiptMetricsV1,
    pi_provider_result_receipt_metrics,
)
from .provider_stage_retry_adult_actions import (
    AdultProviderStageReviewActionResultV1,
    AdultProviderStageReviewActionRuntimeV1,
)
from .provider_stage_retry_blob import TrustedLocalProtectedStageBlobStore
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
from .provider_stage_retry_http import (
    ProtectedProviderStageRetryHttpCursorStoreV1,
    ProviderStageRetryHttpControllerV1,
    ProviderStageRetryHttpRegistrationV1,
)
from .provider_stage_retry_ordinary import (
    OrdinaryProviderStageRetryRuntimeV1,
    deserialize_pi_result,
    deserialize_planner_result,
    deserialize_reader_validation_result,
    deserialize_semantic_validation_result,
    ordinary_nonsemantic_disposition,
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
from .provider_stage_retry_ordinary_custody import (
    ProtectedOrdinaryStageRetryCustodyStoreV1,
)
from .provider_stage_retry_ordinary_retrieval import (
    ActiveDerivedPlannerRetrievalManifestPortV1,
    PlannerManifestRevalidatingOwnerFactoryV1,
)
from .provider_stage_retry_packets import (
    ProviderStageConfigurationV1,
    ProviderStageFrozenPacketV1,
)
from .provider_stage_retry_runtime import (
    ProviderStageRetryRuntimeServiceV1,
    ProviderStageRuntimeAdapterV1,
)
from .provider_stage_retry_scope import ProviderStageRetryOccurrenceScopeV1
from .review_store import LeanSceneTurnInputV1
from .runtime import (
    OrdinaryReaderValidatorPort,
    OrdinarySemanticValidatorPort,
    ProviderStageRetryPendingError,
)
from .store import LeanSceneStore

_ORDINARY_STAGES = frozenset(
    {
        ProviderStage.PLANNER,
        ProviderStage.WRITER,
        ProviderStage.SEMANTIC_VALIDATOR,
        ProviderStage.READER,
        ProviderStage.RECORDER,
    }
)
_OPERATOR_TERMINAL_PHASES = frozenset(
    {ProviderStageRetryPhase.EXHAUSTED, ProviderStageRetryPhase.RECOVERY_REQUIRED}
)


class ProviderStageOwnerLifecycleClass(StrEnum):
    """Physical lifecycle contract for a registered stage owner."""

    RETAINED_INITIAL_FRESH_MANUAL_RETRY = "retained_initial_fresh_manual_retry"
    FRESH_SINGLE_USE = "fresh_single_use"
    ONE_SHOT_PROCESS = "one_shot_process"


@dataclass(frozen=True, slots=True)
class ProviderStageAssemblyRegistrationV1:
    stage: ProviderStage
    lifecycle: ProviderStageOwnerLifecycleClass
    maximum_provider_operations: int

    def __post_init__(self) -> None:
        if type(self.stage) is not ProviderStage:
            raise ContractValidationError("assembled provider stage is not closed")
        if type(self.lifecycle) is not ProviderStageOwnerLifecycleClass:
            raise ContractValidationError("assembled provider lifecycle is not closed")
        if type(self.maximum_provider_operations) is not int or (
            self.maximum_provider_operations < 1
        ):
            raise ContractValidationError("assembled provider operation ceiling is invalid")


@dataclass(frozen=True, slots=True)
class OrdinaryStageProviderPortsV1:
    """Provider callables injected by the launcher without invoking them."""

    resolve_planner: Callable[[LeanSceneTurnInputV1], object]
    planner_active_thread_sha256: Callable[[LeanSceneTurnInputV1], str | None]
    retire_planner_thread: Callable[[LeanSceneTurnInputV1, str], None]
    planner_provider_result: Callable[[LeanSceneTurnInputV1], ContinuousProviderResultV1]
    semantic_validator: OrdinarySemanticValidatorPort
    luna_provider_result: Callable[[], ContinuousProviderResultV1]
    reader_validator: OrdinaryReaderValidatorPort
    reader_provider_result: Callable[[], ContinuousProviderResultV1]

    def __post_init__(self) -> None:
        for value, label in (
            (self.resolve_planner, "Planner resolver"),
            (self.planner_active_thread_sha256, "Planner thread reader"),
            (self.retire_planner_thread, "Planner retiree"),
            (self.planner_provider_result, "Planner receipt reader"),
            (self.luna_provider_result, "Luna receipt reader"),
            (self.reader_provider_result, "Reader receipt reader"),
        ):
            if not callable(value):
                raise ContractValidationError(f"ordinary Retry {label} is unavailable")
        if not callable(getattr(self.semantic_validator, "validate", None)):
            raise ContractValidationError("ordinary Retry Luna validator is unavailable")
        if not callable(getattr(self.reader_validator, "validate", None)):
            raise ContractValidationError("ordinary Retry Reader validator is unavailable")


@dataclass(frozen=True, slots=True)
class _OrdinaryAttemptDispositionV1:
    SCHEMA_VERSION: ClassVar[str] = "cera.ordinary_provider_stage_disposition.v1"

    schema_version: str
    chain_id: str
    attempt_number: int
    stage: ProviderStage
    outcome_kind: str
    result_sha256: str | None
    result_evidence_sha256: str | None
    semantic_disposition: ProviderStageSemanticDisposition | None
    failure_class: ProviderStageFailureClass | None
    failure_evidence_sha256: str | None
    metrics: ProviderStageAttemptMetricsV1
    disposition_sha256: str

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("ordinary stage disposition schema changed")
        if self.stage not in _ORDINARY_STAGES:
            raise ContractValidationError("ordinary stage disposition changed owner")
        if not self.chain_id.startswith("stage-retry-") or not re_is_sha256(
            self.chain_id.removeprefix("stage-retry-")
        ):
            raise ContractValidationError("ordinary stage disposition chain is invalid")
        if type(self.attempt_number) is not int or not 1 <= self.attempt_number <= 3:
            raise ContractValidationError("ordinary stage disposition attempt is invalid")
        if type(self.metrics) is not ProviderStageAttemptMetricsV1:
            raise ContractValidationError("ordinary stage disposition metrics changed")
        if self.outcome_kind == "success":
            if (
                self.result_sha256 is None
                or self.result_evidence_sha256 is None
                or self.semantic_disposition is None
                or self.failure_class is not None
                or self.failure_evidence_sha256 is not None
            ):
                raise ContractValidationError("ordinary success disposition is incomplete")
        elif self.outcome_kind in {"retryable_failure", "non_retryable_failure"}:
            expected = (
                RETRYABLE_PROVIDER_STAGE_FAILURES
                if self.outcome_kind == "retryable_failure"
                else NON_RETRYABLE_PROVIDER_STAGE_FAILURES
            )
            if (
                self.failure_class not in expected
                or self.failure_evidence_sha256 is None
                or self.result_sha256 is not None
                or self.result_evidence_sha256 is not None
                or self.semantic_disposition is not None
            ):
                raise ContractValidationError("ordinary failure disposition is incomplete")
        else:
            raise ContractValidationError("ordinary stage disposition is open")
        for value, label in (
            (self.result_sha256, "result"),
            (self.result_evidence_sha256, "result evidence"),
            (self.failure_evidence_sha256, "failure evidence"),
            (self.disposition_sha256, "disposition"),
        ):
            if value is not None and not re_is_sha256(value):
                raise ContractValidationError(f"ordinary stage {label} is invalid")
        if self.disposition_sha256 != _ordinary_disposition_sha256(self):
            raise ContractValidationError("ordinary stage disposition binding changed")


class _OutcomeRecordingDispatchV1:
    def __init__(
        self,
        wrapped: PreparedProviderStageDispatchPort,
        record: Callable[[ProviderStageAttemptOutcomeV1], ProviderStageAttemptOutcomeV1],
    ) -> None:
        self._wrapped = wrapped
        self._record = record

    @property
    def dispatch_evidence_sha256(self) -> str:
        return self._wrapped.dispatch_evidence_sha256

    def invoke(self) -> ProviderStageAttemptOutcomeV1:
        return self._record(self._wrapped.invoke())


class _OutcomeRecordingOwnerV1:
    def __init__(
        self,
        wrapped: ProviderStageAttemptOwnerPort,
        record: Callable[[ProviderStageAttemptOutcomeV1], ProviderStageAttemptOutcomeV1],
    ) -> None:
        self._wrapped = wrapped
        self._record = record

    @property
    def session_scope_sha256(self) -> str:
        return self._wrapped.session_scope_sha256

    @property
    def ledger_prefix_before_sha256(self) -> str:
        return self._wrapped.ledger_prefix_before_sha256

    @property
    def maximum_provider_operations(self) -> int:
        return self._wrapped.maximum_provider_operations

    def prepare(
        self,
        *,
        chain_id: str,
        attempt_number: int,
        exact_input: bytes,
    ) -> ProviderStagePreparationV1:
        prepared = self._wrapped.prepare(
            chain_id=chain_id,
            attempt_number=attempt_number,
            exact_input=exact_input,
        )
        if isinstance(prepared, ProviderStagePreparedCallableDispatchV1):
            return _OutcomeRecordingDispatchV1(prepared, self._record)
        return prepared

    def retire(
        self,
        *,
        chain_id: str,
        attempt_number: int,
        failure_class: ProviderStageFailureClass,
    ) -> str:
        return self._wrapped.retire(
            chain_id=chain_id,
            attempt_number=attempt_number,
            failure_class=failure_class,
        )


class OrdinaryProviderStageAttemptOwnerFactoryV1:
    """Durable lazy owner factory shared by the ordinary stages."""

    def __init__(
        self,
        *,
        stage: ProviderStage,
        lifecycle: ProviderStageOwnerLifecycleClass,
        boundary_kind: ProviderStageBoundaryKind,
        maximum_provider_operations: int,
        protected_root: Path,
        read_current_ledger: Callable[[], ProviderStageLedgerSnapshotV1],
        read_ledger_prefix: Callable[[str], ProviderStageLedgerSnapshotV1],
        build_request: Callable[[bytes], object],
        invoke_provider: Callable[[object, str, int], object],
        serialize_result: Callable[[object], bytes],
        result_receipt_metrics: Callable[[object], ProviderStageReceiptMetricsV1],
        semantic_disposition: Callable[[object], ProviderStageSemanticDisposition],
        retire_failed_owner: Callable[[ProviderStageOwnerRetirementV1, Path], str],
        classify_non_retryable: ProviderStageNonRetryableClassifierPort | None = None,
    ) -> None:
        if stage not in _ORDINARY_STAGES:
            raise ContractValidationError("ordinary owner factory changed stage")
        self.stage = stage
        self.lifecycle = lifecycle
        self.boundary_kind = boundary_kind
        self.maximum_provider_operations = maximum_provider_operations
        self.protected_root = protected_root.resolve()
        self._read_current_ledger = read_current_ledger
        self._read_ledger_prefix = read_ledger_prefix
        self._build_request = build_request
        self._invoke_provider = invoke_provider
        self._serialize_result = serialize_result
        self._result_receipt_metrics = result_receipt_metrics
        self._semantic_disposition = semantic_disposition
        self._retire_failed_owner = retire_failed_owner
        self._classify_non_retryable = classify_non_retryable

    def create_initial_owner(
        self,
        *,
        scope: ProviderStageRetryOccurrenceScopeV1,
        packet: ProviderStageFrozenPacketV1,
    ) -> ProviderStageAttemptOwnerPort:
        if scope.stage is not self.stage or packet.stage is not self.stage:
            raise StateConflictError("ordinary initial owner changed stage")
        return self._owner(
            chain_id=scope.identity.chain_id,
            attempt_number=1,
            exact_input=packet.exact_bytes,
            ledger_before=self._read_current_ledger(),
        )

    def create_retry_owner(
        self,
        *,
        chain: ProviderStageRetryChainV1,
        exact_input: bytes,
    ) -> ProviderStageAttemptOwnerPort:
        self._require_chain(chain)
        return self._owner(
            chain_id=chain.chain_id,
            attempt_number=len(chain.attempts) + 1,
            exact_input=exact_input,
            ledger_before=self._read_current_ledger(),
        )

    def reconstruct_owner(
        self,
        *,
        chain: ProviderStageRetryChainV1,
        attempt: ProviderStageAttemptV1,
        exact_input: bytes,
    ) -> ProviderStageAttemptOwnerPort:
        self._require_chain(chain)
        if attempt not in chain.attempts:
            raise StateConflictError("ordinary reconstructed owner changed attempt")
        owner = self._owner(
            chain_id=chain.chain_id,
            attempt_number=attempt.attempt_number,
            exact_input=exact_input,
            ledger_before=self._read_ledger_prefix(attempt.ledger_prefix_before_sha256),
        )
        if owner.session_scope_sha256 != attempt.session_scope_sha256:
            raise StateConflictError("ordinary reconstructed owner changed session scope")
        return owner

    def check_status(
        self,
        *,
        chain: ProviderStageRetryChainV1,
    ) -> ProviderStageAmbiguityResolutionV1 | None:
        outcome = self._closed_outcome(chain)
        if outcome is None:
            return None
        return ProviderStageAmbiguityResolutionV1(
            resolution_evidence_sha256=domain_sha256(
                "cera.ordinary_provider_stage_reconciliation.v1",
                {
                    "chain_id": chain.chain_id,
                    "stage": self.stage.value,
                    "attempt_number": chain.attempts[-1].attempt_number,
                    "ledger_prefix_after_sha256": outcome.metrics.ledger_prefix_after_sha256,
                },
            ),
            disposition=outcome,
        )

    def recover_interrupted_dispatch(
        self,
        *,
        chain: ProviderStageRetryChainV1,
    ) -> ProviderStageAttemptOutcomeV1:
        self._require_chain(chain)
        if chain.phase is not ProviderStageRetryPhase.DISPATCH_STARTED or not chain.attempts:
            raise StateConflictError("ordinary interrupted dispatch changed custody")
        outcome = self._closed_outcome(chain)
        if outcome is not None:
            return outcome
        attempt = chain.attempts[-1]
        try:
            before = self._read_ledger_prefix(attempt.ledger_prefix_before_sha256)
            after = self._read_current_ledger()
            observed = after.provider_operations_total - before.provider_operations_total
            if observed < 0:
                raise StateConflictError("ordinary provider ledger moved backwards")
            prefix_after = after.prefix_sha256
        except StateConflictError:
            observed = 0
            prefix_after = attempt.ledger_prefix_before_sha256
        return ProviderStageDispatchAmbiguousV1(
            failure_evidence_sha256=domain_sha256(
                "cera.ordinary_provider_stage_interrupted_dispatch.v1",
                {
                    "chain_id": chain.chain_id,
                    "stage": self.stage.value,
                    "attempt_number": attempt.attempt_number,
                    "ledger_prefix_before_sha256": attempt.ledger_prefix_before_sha256,
                    "ledger_prefix_after_sha256": prefix_after,
                    "provider_operations_observed": observed,
                    "maximum_provider_operations": self.maximum_provider_operations,
                },
            ),
            metrics=ProviderStageAttemptMetricsV1(
                ledger_prefix_after_sha256=prefix_after,
                provider_operations_observed=observed,
                provider_operations_conservative=max(observed, self.maximum_provider_operations),
                duration_ms=0,
            ),
        )

    def _owner(
        self,
        *,
        chain_id: str,
        attempt_number: int,
        exact_input: bytes,
        ledger_before: ProviderStageLedgerSnapshotV1,
    ) -> ProviderStageAttemptOwnerPort:
        attempt_root = self._attempt_root(chain_id, attempt_number)
        lifecycle_scope = (
            "retained_initial"
            if self.stage is ProviderStage.PLANNER and attempt_number == 1
            else (
                "fresh_manual_retry"
                if self.stage is ProviderStage.PLANNER
                else self.lifecycle.value
            )
        )
        session_scope_sha256 = domain_sha256(
            "cera.ordinary_provider_stage_owner_scope.v1",
            {
                "chain_id": chain_id,
                "attempt_number": attempt_number,
                "stage": self.stage.value,
                "stage_input_sha256": bytes_sha256(exact_input),
                "lifecycle": lifecycle_scope,
                "protected_attempt_root": str(attempt_root),
            },
        )

        owner = CallableProviderStageAttemptOwnerV1(
            binding=ProviderStageAttemptOwnerBindingV1(
                chain_id=chain_id,
                attempt_number=attempt_number,
                stage=self.stage,
                boundary_kind=self.boundary_kind,
                stage_input_sha256=bytes_sha256(exact_input),
                session_scope_sha256=session_scope_sha256,
                ledger_before=ledger_before,
                maximum_provider_operations=self.maximum_provider_operations,
            ),
            read_session_scope_sha256=lambda: session_scope_sha256,
            read_ledger_snapshot=self._read_current_ledger,
            build_request=self._build_request,
            invoke_provider=lambda request: self._invoke_provider(
                request, chain_id, attempt_number
            ),
            serialize_result=self._serialize_result,
            result_receipt_metrics=self._result_receipt_metrics,
            semantic_disposition=self._semantic_disposition,
            retire_owner=lambda retirement: self._retire_failed_owner(retirement, attempt_root),
            classify_non_retryable=self._classify_non_retryable,
        )
        return _OutcomeRecordingOwnerV1(
            owner,
            lambda outcome: self._record_outcome(
                outcome,
                chain_id=chain_id,
                attempt_number=attempt_number,
                attempt_root=attempt_root,
            ),
        )

    def _record_outcome(
        self,
        outcome: ProviderStageAttemptOutcomeV1,
        *,
        chain_id: str,
        attempt_number: int,
        attempt_root: Path,
    ) -> ProviderStageAttemptOutcomeV1:
        if isinstance(outcome, ProviderStageDispatchAmbiguousV1):
            return outcome
        if isinstance(outcome, ProviderStageSuccessfulResultV1):
            _write_once(attempt_root / "RESULT.json", outcome.exact_result)
            kind = "success"
            result_sha256 = bytes_sha256(outcome.exact_result)
            result_evidence = outcome.result_evidence_sha256
            semantic = outcome.semantic_disposition
            failure_class = None
            failure_evidence = None
        elif isinstance(outcome, ProviderStageClosedFailureV1):
            kind = "retryable_failure"
            result_sha256 = None
            result_evidence = None
            semantic = None
            failure_class = outcome.failure_class
            failure_evidence = outcome.failure_evidence_sha256
        elif isinstance(outcome, ProviderStageNonRetryableFailureV1):
            kind = "non_retryable_failure"
            result_sha256 = None
            result_evidence = None
            semantic = None
            failure_class = outcome.failure_class
            failure_evidence = outcome.failure_evidence_sha256
        else:
            raise ContractValidationError("ordinary stage outcome is not closed")
        draft = {
            "schema_version": _OrdinaryAttemptDispositionV1.SCHEMA_VERSION,
            "chain_id": chain_id,
            "attempt_number": attempt_number,
            "stage": self.stage.value,
            "outcome_kind": kind,
            "result_sha256": result_sha256,
            "result_evidence_sha256": result_evidence,
            "semantic_disposition": None if semantic is None else semantic.value,
            "failure_class": None if failure_class is None else failure_class.value,
            "failure_evidence_sha256": failure_evidence,
            "metrics": to_primitive(outcome.metrics),
        }
        _write_once(
            attempt_root / "DISPOSITION.json",
            canonical_bytes({**draft, "disposition_sha256": canonical_sha256(draft)}),
        )
        return outcome

    def _closed_outcome(
        self,
        chain: ProviderStageRetryChainV1,
    ) -> (
        ProviderStageSuccessfulResultV1
        | ProviderStageClosedFailureV1
        | ProviderStageNonRetryableFailureV1
        | None
    ):
        self._require_chain(chain)
        if not chain.attempts:
            return None
        attempt = chain.attempts[-1]
        disposition = _read_ordinary_disposition(
            self._attempt_root(chain.chain_id, attempt.attempt_number) / "DISPOSITION.json"
        )
        if disposition is None:
            return None
        if (
            disposition.chain_id != chain.chain_id
            or disposition.attempt_number != attempt.attempt_number
            or disposition.stage is not self.stage
        ):
            raise StateConflictError("ordinary stage disposition changed attempt")
        try:
            before = self._read_ledger_prefix(attempt.ledger_prefix_before_sha256)
            after = self._read_ledger_prefix(disposition.metrics.ledger_prefix_after_sha256)
        except StateConflictError:
            return None
        if (
            after.provider_operations_total - before.provider_operations_total
            != disposition.metrics.provider_operations_observed
        ):
            return None
        if disposition.outcome_kind == "success":
            path = self._attempt_root(chain.chain_id, attempt.attempt_number) / "RESULT.json"
            try:
                result = path.read_bytes()
            except OSError:
                return None
            if bytes_sha256(result) != disposition.result_sha256:
                return None
            semantic = self._semantic_disposition(self._decode_result(result))
            if semantic is not disposition.semantic_disposition:
                return None
            assert disposition.result_evidence_sha256 is not None
            return ProviderStageSuccessfulResultV1(
                exact_result=result,
                result_evidence_sha256=disposition.result_evidence_sha256,
                metrics=disposition.metrics,
                semantic_disposition=semantic,
            )
        assert disposition.failure_class is not None
        assert disposition.failure_evidence_sha256 is not None
        if disposition.outcome_kind == "retryable_failure":
            return ProviderStageClosedFailureV1(
                failure_class=disposition.failure_class,
                failure_evidence_sha256=disposition.failure_evidence_sha256,
                metrics=disposition.metrics,
            )
        return ProviderStageNonRetryableFailureV1(
            failure_class=disposition.failure_class,
            failure_evidence_sha256=disposition.failure_evidence_sha256,
            metrics=disposition.metrics,
        )

    def _decode_result(self, exact_result: bytes) -> object:
        if self.stage is ProviderStage.PLANNER:
            return deserialize_planner_result(exact_result)
        if self.stage is ProviderStage.SEMANTIC_VALIDATOR:
            return deserialize_semantic_validation_result(exact_result)
        if self.stage is ProviderStage.READER:
            return deserialize_reader_validation_result(exact_result)
        return deserialize_pi_result(exact_result)

    def _attempt_root(self, chain_id: str, attempt_number: int) -> Path:
        if not chain_id.startswith("stage-retry-") or not 1 <= attempt_number <= 3:
            raise ContractValidationError("ordinary owner attempt identity is invalid")
        root = (
            self.protected_root
            / self.stage.value
            / chain_id.removeprefix("stage-retry-")[:24]
            / f"a{attempt_number}"
        ).resolve()
        if not root.is_relative_to(self.protected_root):
            raise ContractValidationError("ordinary owner attempt escaped custody")
        return root

    def _require_chain(self, chain: ProviderStageRetryChainV1) -> None:
        if type(chain) is not ProviderStageRetryChainV1 or chain.identity.stage is not self.stage:
            raise StateConflictError("ordinary owner factory changed chain stage")


class OrdinaryStageResultBinderV1:
    """Idempotent protected-continuation binder with no story publication."""

    def __init__(self, stage: ProviderStage) -> None:
        if stage not in _ORDINARY_STAGES:
            raise ContractValidationError("ordinary result binder changed stage")
        self.stage = stage

    def downstream_intent_sha256(
        self,
        *,
        chain: ProviderStageRetryChainV1,
        exact_result: bytes,
    ) -> str:
        return domain_sha256(
            "cera.ordinary_provider_stage_downstream_intent.v1",
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
        if downstream_intent_sha256 != self.downstream_intent_sha256(
            chain=chain, exact_result=exact_result
        ):
            raise StateConflictError("ordinary stage downstream intent changed")
        return domain_sha256(
            "cera.ordinary_provider_stage_downstream_bound.v1",
            {
                "chain_id": chain.chain_id,
                "stage": self.stage.value,
                "downstream_intent_sha256": downstream_intent_sha256,
            },
        )


class _AdultStageRetryOrchestratorV1:
    """Preserve adult preparation while replacing only Scene/Filter execution."""

    def __init__(
        self,
        *,
        wrapped: AutomaticAdultRouteOrchestrator,
        continuation: AdultStageRetryContinuationServiceV1,
        turn: LeanSceneTurnInputV1,
        turn_store: _ProtectedAdultRetryTurnStoreV1,
    ) -> None:
        self._wrapped = wrapped
        self._continuation = continuation
        self._turn = turn
        self._turn_store = turn_store
        self.route_state = wrapped.route_state
        self.preparation_builder = wrapped.preparation_builder
        self.pipeline = wrapped.pipeline

    def prepare(self, **kwargs: Any) -> PreparedAdultRouteOperationV1:
        prepared = self._wrapped.prepare(**kwargs)
        self._turn_store.bind(prepared, self._turn)
        return prepared

    def execute_prepared(
        self,
        prepared: PreparedAdultRouteOperationV1,
    ) -> AdultRouteOperationOutcomeV1:
        current = self.route_state.current_logic_route(
            world_id=prepared.route_state.world_id,
            branch_id=prepared.route_state.branch_id,
        )
        if current != prepared.route_state:
            raise StateConflictError("accepted adult route changed after preparation")
        self._turn_store.bind(prepared, self._turn)
        integration = cast(AdultPipelineIntegrationV1, self.pipeline)
        source = AdultStageRetryExecutionInputV1(
            generation_id=f"adult-operation:{prepared.operation_sha256}",
            prepared=_adult_pipeline_input(prepared),
            role_context=integration.scene_port.context,
            accepted_parent_session=integration.scene_port.accepted_parent_session,
        )
        return _execute_adult_stage_retry(
            continuation=self._continuation,
            prepared=prepared,
            source=source,
        )


class _ProtectedAdultRetryTurnStoreV1:
    """Exact turn custody used by provider-free adult HTTP continuation."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def bind(
        self,
        prepared: PreparedAdultRouteOperationV1,
        turn: LeanSceneTurnInputV1,
    ) -> None:
        if (
            prepared.route_state.world_id != turn.world_id
            or prepared.route_state.branch_id != turn.branch_id
            or prepared.scene_request.exact_current_source != turn.exact_user_source
        ):
            raise StateConflictError("adult Retry continuation changed exact turn")
        body = {
            "schema_version": "cera.adult_stage_retry_turn_binding.v1",
            "operation_sha256": prepared.operation_sha256,
            "turn": to_primitive(turn),
            "turn_sha256": canonical_sha256(turn),
        }
        _write_once(
            self.root / f"{prepared.operation_sha256}.json",
            canonical_bytes({**body, "binding_sha256": canonical_sha256(body)}),
        )

    def load(self, prepared: PreparedAdultRouteOperationV1) -> LeanSceneTurnInputV1:
        from cera.schema import from_mapping

        try:
            payload = json.loads(
                (self.root / f"{prepared.operation_sha256}.json").read_text(encoding="utf-8")
            )
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise StateConflictError("adult Retry turn custody is unavailable") from exc
        if not isinstance(payload, dict):
            raise StateConflictError("adult Retry turn custody changed shape")
        body = {key: value for key, value in payload.items() if key != "binding_sha256"}
        if (
            set(payload)
            != {
                "schema_version",
                "operation_sha256",
                "turn",
                "turn_sha256",
                "binding_sha256",
            }
            or body["schema_version"] != "cera.adult_stage_retry_turn_binding.v1"
            or body["operation_sha256"] != prepared.operation_sha256
            or canonical_sha256(body) != payload["binding_sha256"]
        ):
            raise StateConflictError("adult Retry turn binding changed")
        turn = from_mapping(LeanSceneTurnInputV1, cast(Mapping[str, Any], body["turn"]))
        assert isinstance(turn, LeanSceneTurnInputV1)
        if canonical_sha256(turn) != body["turn_sha256"]:
            raise StateConflictError("adult Retry exact turn changed")
        return turn


class _ProtectedTerminalCompletionStoreV1:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def load(self, chain_id: str) -> dict[str, Any] | None:
        path = self._path(chain_id)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise StateConflictError("provider-stage terminal completion is unreadable") from exc
        if not isinstance(payload, dict):
            raise StateConflictError("provider-stage terminal completion changed shape")
        body = {key: value for key, value in payload.items() if key != "binding_sha256"}
        completion = body.get("completion")
        if (
            set(payload)
            != {"schema_version", "chain_id", "completion", "completion_sha256", "binding_sha256"}
            or body["schema_version"] != "cera.provider_stage_terminal_completion.v1"
            or body["chain_id"] != chain_id
            or not isinstance(completion, dict)
            or canonical_sha256(completion) != body["completion_sha256"]
            or canonical_sha256(body) != payload["binding_sha256"]
        ):
            raise StateConflictError("provider-stage terminal completion binding changed")
        return dict(completion)

    def bind(self, chain_id: str, completion: Mapping[str, Any]) -> dict[str, Any]:
        normalized = to_primitive(completion)
        if not isinstance(normalized, dict):
            raise ContractValidationError("provider-stage completion must be an object")
        body = {
            "schema_version": "cera.provider_stage_terminal_completion.v1",
            "chain_id": chain_id,
            "completion": normalized,
            "completion_sha256": canonical_sha256(normalized),
        }
        _write_once(
            self._path(chain_id),
            canonical_bytes({**body, "binding_sha256": canonical_sha256(body)}),
        )
        durable = self.load(chain_id)
        assert durable is not None
        return durable

    def _path(self, chain_id: str) -> Path:
        if not chain_id.startswith("stage-retry-") or not re_is_sha256(
            chain_id.removeprefix("stage-retry-")
        ):
            raise ContractValidationError("provider-stage terminal chain is invalid")
        return self.root / f"{chain_id.removeprefix('stage-retry-')}.json"


class _LateBoundOrdinaryHttpContinuationV1:
    def __init__(
        self,
        *,
        ordinary: OrdinaryProviderStageRetryRuntimeV1,
        completions: _ProtectedTerminalCompletionStoreV1,
    ) -> None:
        self._ordinary = ordinary
        self._completions = completions
        self._adapter: PiSceneHttpAdapter | None = None

    def bind_adapter(self, adapter: PiSceneHttpAdapter) -> None:
        if self._adapter not in {None, adapter}:
            raise StateConflictError("ordinary HTTP continuation adapter changed")
        self._adapter = adapter

    def latest_chain_for_chain(self, chain_id: str) -> object:
        return self._ordinary.latest_chain_for_chain(chain_id)

    def continuation_epoch_for_chain(self, chain_id: str) -> object | None:
        return self._ordinary.review_action_for_chain(chain_id)

    def validation_lane_binding_for_chain(
        self,
        chain_id: str,
    ) -> Mapping[str, str] | None:
        if self._adapter is None:
            return None
        return self._adapter.validation_lane_binding_for_chain(chain_id)

    def review_payload_for_validation_chain(self, chain_id: str) -> Mapping[str, Any]:
        return self._require_adapter().review_payload_for_validation_chain(chain_id)

    def recording_repair_action_allowed(self, chain_id: str) -> bool:
        return self._ordinary.recording_repair_action_allowed(chain_id)

    def execute_recording_repair(self, action: object) -> object:
        return self._ordinary.execute_recording_repair(
            action,
            continuation=self._require_adapter(),
        )

    def load_terminal_completion(self, chain_id: str) -> Mapping[str, Any] | None:
        if self.validation_lane_binding_for_chain(chain_id) is not None:
            # Lane success is not request-terminal.  Always project the live
            # durable review through ``review_payload_for_validation_chain``.
            return None
        action = self._ordinary.review_action_for_chain(chain_id)
        if action is not None:
            response = self._ordinary.load_review_action_response_optional(action.action_id)
            if response is None:
                return None
            completion, receipt = response
            finalized, _ = self._ordinary.finalize_review_action(action.action_id)
            if finalized != action or receipt.response_sha256 != canonical_sha256(completion):
                raise StateConflictError(
                    "ordinary review action terminal reconciliation changed custody"
                )
            return completion
        return self._completions.load(chain_id)

    def resume_succeeded_chain(self, chain_id: str) -> object:
        return self._ordinary.resume_succeeded_chain(
            chain_id,
            continuation=self._require_adapter(),
        )

    def project_terminal_completion(self, *, chain_id: str, result: object) -> Mapping[str, Any]:
        del chain_id
        if not isinstance(result, Mapping):
            raise StateConflictError("ordinary HTTP continuation returned no completion")
        return result

    def bind_terminal_completion(
        self,
        *,
        chain_id: str,
        completion: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        if self.validation_lane_binding_for_chain(chain_id) is not None:
            # A recovered Luna/Reader lane joins only its durable review.  It
            # is never an immutable terminal completion: the peer may still
            # join and automatic acceptance may change the review afterward.
            return self.review_payload_for_validation_chain(chain_id)
        action = self._ordinary.review_action_for_chain(chain_id)
        if action is not None:
            durable, receipt = self._ordinary.load_review_action_response_for_chain(chain_id)
            finalized, _ = self._ordinary.finalize_review_action(action.action_id)
            if (
                durable != dict(completion)
                or receipt.response_sha256 != canonical_sha256(completion)
                or finalized != action
            ):
                raise StateConflictError(
                    "ordinary review action continuation changed terminal response"
                )
            return durable
        identity = self._ordinary.chain_request_identity(chain_id)
        cera = completion.get("cera")
        retains_review_custody = isinstance(cera, Mapping) and cera.get("provisional") is True
        if retains_review_custody:
            assert isinstance(cera, Mapping)
            review_id = cera.get("provisional_review_id")
            if not isinstance(review_id, str) or not review_id.strip():
                raise StateConflictError("ordinary provisional completion lost its review identity")
            _, review_context, _ = self._ordinary.pending_request_for_review(review_id)
            if (
                review_context.binding.request_id != identity.request_id
                or canonical_sha256(review_context.context_payload()) != identity.context_sha256
            ):
                raise StateConflictError("ordinary provisional completion changed request custody")
        terminal_receipt = self._ordinary.bind_terminal_response_for_chain(
            chain_id=chain_id,
            response=completion,
        )
        if not retains_review_custody:
            self._ordinary.redact_pending_request(
                identity.request_id,
                disposition="completed",
                terminal_evidence_sha256=terminal_receipt.response_sha256,
            )
        return self._completions.bind(chain_id, completion)

    def terminalize_failure(self, chain_id: str, envelope: object) -> None:
        if not isinstance(envelope, Mapping):
            raise ContractValidationError("ordinary terminal Retry envelope changed")
        status = envelope.get("status")
        state = status.get("state") if isinstance(status, Mapping) else None
        if state not in {
            "attempts_exhausted",
            "recording_repair_required",
            "recovery_required",
        }:
            raise StateConflictError("ordinary Retry failure is not terminal")
        if state == "recording_repair_required":
            # The accepted story is durable, but the exact request/review
            # context remains necessary to create and terminalize the sole
            # bounded Recorder repair successor.
            return
        if self.validation_lane_binding_for_chain(chain_id) is not None:
            # The exact candidate and peer validation lane remain frozen for
            # explicit, stage-local recovery.
            return
        action = self._ordinary.review_action_for_chain(chain_id)
        if action is not None:
            # The exact creator action remains protected for explicit recovery.
            # A stage-local terminal failure must not retire Request A or claim
            # that the semantic review action completed.
            return
        identity = self._ordinary.chain_request_identity(chain_id)
        self._ordinary.redact_pending_request(
            identity.request_id,
            disposition=cast(str, state),
            terminal_evidence_sha256=canonical_sha256(envelope),
        )

    def _require_adapter(self) -> PiSceneHttpAdapter:
        if self._adapter is None:
            raise StateConflictError("ordinary HTTP continuation adapter is not bound")
        return self._adapter


class _LateBoundAdultHttpContinuationV1:
    def __init__(
        self,
        *,
        continuation: AdultStageRetryContinuationServiceV1,
        actions: AdultProviderStageReviewActionRuntimeV1,
        runtime: ProviderStageRetryRuntimeServiceV1,
        turn_store: _ProtectedAdultRetryTurnStoreV1,
        completions: _ProtectedTerminalCompletionStoreV1,
    ) -> None:
        self._continuation = continuation
        self._actions = actions
        self._runtime = runtime
        self._turn_store = turn_store
        self._completions = completions
        self._adapter: PiSceneHttpAdapter | None = None
        self._controller: FullModelSceneController | None = None

    def bind_adapter(self, adapter: PiSceneHttpAdapter) -> None:
        if self._adapter not in {None, adapter}:
            raise StateConflictError("adult HTTP continuation adapter changed")
        self._adapter = adapter

    def bind_controller(self, controller: FullModelSceneController) -> None:
        if self._controller not in {None, controller}:
            raise StateConflictError("adult HTTP continuation controller changed")
        self._controller = controller

    def latest_chain_for_chain(self, chain_id: str) -> object:
        return self._continuation.latest_chain_for_chain(chain_id)

    def continuation_epoch_for_chain(self, chain_id: str) -> object | None:
        return self._actions.review_action_identity_for_chain(chain_id)

    def recording_repair_action_allowed(self, chain_id: str) -> bool:
        del chain_id
        return False

    def execute_recording_repair(self, action: object) -> object:
        del action
        raise StateConflictError("Recorder repair is unavailable for Adult stages")

    def load_terminal_completion(self, chain_id: str) -> Mapping[str, Any] | None:
        action = self._actions.review_action_for_chain(chain_id)
        if action is not None:
            response = self._actions.load_review_action_response_for_chain_optional(chain_id)
            if response is None:
                return None
            completion, receipt = response
            finalized = self._actions.finalize_review_action(action.action_id)
            if (
                finalized.action_id != action.action_id
                or receipt.response_sha256 != canonical_sha256(completion)
            ):
                raise StateConflictError(
                    "adult review action terminal reconciliation changed custody"
                )
            return completion
        return self._completions.load(chain_id)

    def resume_succeeded_chain(self, chain_id: str) -> object:
        action = self._actions.review_action_for_chain(chain_id)
        callback: (
            Callable[
                [ProviderStageRetryOccurrenceScopeV1, ProviderStageFrozenPacketV1],
                None,
            ]
            | None
        ) = None
        if action is not None:

            def bind_stage_dispatch(
                scope: ProviderStageRetryOccurrenceScopeV1,
                packet: ProviderStageFrozenPacketV1,
            ) -> None:
                self._actions.bind_stage_dispatch(
                    scope=scope,
                    packet=packet,
                    source_chain_id=chain_id,
                )

            callback = bind_stage_dispatch
        progress = self._continuation.resume_succeeded_chain(
            chain_id,
            before_stage_dispatch=callback,
        )
        if not progress.completed:
            assert progress.pending_chain_id is not None
            raise ProviderStageRetryPendingError(
                self._runtime.canonical_status(chain_id=progress.pending_chain_id)
            )
        if action is not None:
            return self._actions.resume_review_action(
                chain_id=chain_id,
                controller=self._require_controller(),
            )
        source = self._continuation.source_for_chain(chain_id)
        prepared = self._continuation.operation_store.lookup(
            request_id=source.prepared.request_id,
            candidate_id=source.prepared.candidate_id,
        ).prepared
        if _adult_pipeline_input(prepared) != source.prepared:
            raise StateConflictError("adult Retry operation custody changed")
        turn = self._turn_store.load(prepared)
        controller = self._require_controller()
        outcome = controller.recover_completed_adult_operation(
            request_id=prepared.request_id,
            turn=turn,
        )
        if outcome is None:
            raise StateConflictError("adult Retry completion lost its operation outcome")
        return outcome

    def project_terminal_completion(self, *, chain_id: str, result: object) -> Mapping[str, Any]:
        adapter = self._require_adapter()
        if isinstance(result, AdultProviderStageReviewActionResultV1):
            project_action = getattr(
                adapter,
                "project_recovered_adult_review_action",
                None,
            )
            if not callable(project_action):
                raise StateConflictError("adult review action HTTP projection is unavailable")
            return cast(
                Mapping[str, Any],
                project_action(
                    chain_id=chain_id,
                    review_id=result.identity.review_id,
                    normalized_action=result.normalized_action,
                    outcome=result.outcome,
                ),
            )
        project = getattr(adapter, "project_recovered_adult_stage_retry", None)
        if not callable(project):
            raise StateConflictError("adult HTTP continuation projection is unavailable")
        return cast(Mapping[str, Any], project(chain_id=chain_id, outcome=result))

    def bind_terminal_completion(
        self,
        *,
        chain_id: str,
        completion: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        action = self._actions.review_action_for_chain(chain_id)
        if action is not None:
            receipt = self._actions.bind_review_action_response_for_chain(
                chain_id=chain_id,
                response=completion,
            )
            finalized = self._actions.finalize_review_action(action.action_id)
            durable, durable_receipt = self._actions.load_review_action_response(action.action_id)
            if (
                durable != dict(completion)
                or durable_receipt != receipt
                or finalized.action_id != action.action_id
            ):
                raise StateConflictError(
                    "adult review action continuation changed terminal response"
                )
            return durable
        return self._completions.bind(chain_id, completion)

    def terminalize_failure(self, chain_id: str, envelope: object) -> None:
        if not isinstance(envelope, Mapping):
            raise ContractValidationError("adult terminal Retry envelope changed")
        status = envelope.get("status")
        state = status.get("state") if isinstance(status, Mapping) else None
        chain = self._runtime.read_chain(chain_id)
        if chain.identity.stage not in {
            ProviderStage.ADULT_SCENE,
            ProviderStage.ADULT_FILTER,
        } or state not in {"attempts_exhausted", "recovery_required"}:
            raise StateConflictError("adult Retry failure is not terminal")
        # Adult source, result, and operation custody intentionally remain
        # protected for explicit recovery.  No public/story mutation occurs.
        self._continuation.source_for_chain(chain_id)

    def _require_adapter(self) -> PiSceneHttpAdapter:
        if self._adapter is None:
            raise StateConflictError("adult HTTP continuation adapter is not bound")
        return self._adapter

    def _require_controller(self) -> FullModelSceneController:
        if self._controller is None:
            raise StateConflictError("adult HTTP continuation controller is not bound")
        return self._controller


@dataclass(frozen=True, slots=True)
class ProviderStageOperatorRecoveryReceiptV1:
    chain_id: str
    request_id: str
    terminal_phase: ProviderStageRetryPhase
    accepted_state_sha256: str
    acknowledgment_sha256: str


class ProviderStageOperatorRecoveryV1:
    """Acknowledge terminal custody without changing the terminal chain."""

    def __init__(
        self,
        *,
        runtime: ProviderStageRetryRuntimeServiceV1,
        scene_store: LeanSceneStore,
        ordinary: OrdinaryProviderStageRetryRuntimeV1,
        root: Path,
    ) -> None:
        self._runtime = runtime
        self._scene_store = scene_store
        self._ordinary = ordinary
        self._root = root.resolve()
        self._root.mkdir(parents=True, exist_ok=True)
        self._http: ProviderStageRetryHttpControllerV1 | None = None

    def bind_http(self, controller: ProviderStageRetryHttpControllerV1) -> None:
        if self._http not in {None, controller}:
            raise StateConflictError("operator recovery HTTP controller changed")
        self._http = controller

    def acknowledge_terminal(
        self,
        *,
        chain_id: str,
        operator_reason: str,
    ) -> ProviderStageOperatorRecoveryReceiptV1:
        if not isinstance(operator_reason, str) or not operator_reason.strip():
            raise ContractValidationError("operator recovery reason is empty")
        chain = self._runtime.read_chain(chain_id)
        if chain.phase not in _OPERATOR_TERMINAL_PHASES:
            raise StateConflictError("provider-stage chain is not operator-terminal")
        if chain.identity.stage is ProviderStage.RECORDER:
            raise StateConflictError("Recorder uses recording repair, not operator recovery")
        scope = self._runtime.scope_for_chain(chain_id)
        head = self._scene_store.load_head(world_id=scope.world_id, branch_id=scope.branch_id)
        if chain.identity.stage in _ORDINARY_STAGES:
            current: str = canonical_sha256(
                {
                    "schema_version": "cera.provider_stage_accepted_head_state.v1",
                    "world_id": head.world_id,
                    "branch_id": head.branch_id,
                    "generation": head.generation,
                    "accepted_turn_id": head.accepted_turn_id,
                    "accepted_head_sha256": head.accepted_head_sha256,
                    "recording_status": (
                        None if head.recording_status is None else head.recording_status.value
                    ),
                }
            )
        else:
            adult_current = head.accepted_head_sha256
            if adult_current is None:
                adult_current = domain_sha256(
                    "cera.adult_provider_stage_unaccepted_root.v1",
                    {"world_id": scope.world_id, "branch_id": scope.branch_id},
                )
            current = adult_current
        if current != scope.accepted_state_sha256:
            raise StateConflictError("operator recovery accepted head changed")
        body = {
            "schema_version": "cera.provider_stage_operator_recovery.v1",
            "chain_id": chain_id,
            "chain_sha256": chain.chain_sha256,
            "request_id": scope.request_id,
            "world_id": scope.world_id,
            "branch_id": scope.branch_id,
            "terminal_phase": chain.phase.value,
            "accepted_state_sha256": scope.accepted_state_sha256,
            "operator_reason_sha256": text_sha256(operator_reason.strip()),
            "chain_remains_terminal": True,
        }
        acknowledgment = canonical_sha256(body)
        _write_once(
            self._root / f"{chain_id.removeprefix('stage-retry-')}.json",
            canonical_bytes({**body, "acknowledgment_sha256": acknowledgment}),
        )
        if chain.identity.stage in _ORDINARY_STAGES:
            self._ordinary.release_branch_barrier(
                scope.request_id,
                recovery_evidence_sha256=acknowledgment,
            )
        if self._http is None:
            raise StateConflictError("operator recovery HTTP barrier is unavailable")
        self._http.complete_request(
            request_id=scope.request_id,
            world_id=scope.world_id,
            branch_id=scope.branch_id,
        )
        if self._runtime.read_chain(chain_id) != chain:
            raise StateConflictError("operator recovery changed terminal chain")
        return ProviderStageOperatorRecoveryReceiptV1(
            chain_id=chain_id,
            request_id=scope.request_id,
            terminal_phase=chain.phase,
            accepted_state_sha256=scope.accepted_state_sha256,
            acknowledgment_sha256=acknowledgment,
        )


class ProviderStageRetryProductionAssemblyV1:
    """One provider-free composition root for all seven stage registrations."""

    def __init__(
        self,
        *,
        runtime: ProviderStageRetryRuntimeServiceV1,
        ordinary: OrdinaryProviderStageRetryRuntimeV1,
        adult: AdultStageRetryContinuationServiceV1,
        adult_stage_retry_actions: AdultProviderStageReviewActionRuntimeV1,
        retrieval: ActiveDerivedPlannerRetrievalManifestPortV1,
        http: ProviderStageRetryHttpControllerV1,
        ordinary_http: _LateBoundOrdinaryHttpContinuationV1,
        adult_http: _LateBoundAdultHttpContinuationV1,
        adult_turns: _ProtectedAdultRetryTurnStoreV1,
        operator_recovery: ProviderStageOperatorRecoveryV1,
        registrations: tuple[ProviderStageAssemblyRegistrationV1, ...],
    ) -> None:
        if {value.stage for value in registrations} != set(ProviderStage):
            raise ContractValidationError("production Retry assembly is not exhaustive")
        self.runtime = runtime
        self.ordinary = ordinary
        self.adult = adult
        self.adult_stage_retry_actions = adult_stage_retry_actions
        self.retrieval = retrieval
        self.http = http
        self.operator_recovery = operator_recovery
        self.registrations = registrations
        self._ordinary_http = ordinary_http
        self._adult_http = adult_http
        self._adult_turns = adult_turns

    def bind_full_model_controller(self, controller: FullModelSceneController) -> None:
        self._adult_http.bind_controller(controller)

    def bind_http_adapter(self, adapter: PiSceneHttpAdapter) -> None:
        self._ordinary_http.bind_adapter(adapter)
        self._adult_http.bind_adapter(adapter)

    def http_adapter_kwargs(self) -> dict[str, object]:
        return {
            "provider_stage_retry_http": self.http,
            "ordinary_stage_retry_runtime": self.ordinary,
            "ordinary_planner_retrieval": self.retrieval,
            "adult_stage_retry_actions": self.adult_stage_retry_actions,
        }

    def wrap_adult_orchestrator(
        self,
        wrapped: AutomaticAdultRouteOrchestrator,
        turn: LeanSceneTurnInputV1,
    ) -> object:
        return _AdultStageRetryOrchestratorV1(
            wrapped=wrapped,
            continuation=self.adult,
            turn=turn,
            turn_store=self._adult_turns,
        )

    def execute_adult_regeneration(
        self,
        *,
        prepared: PreparedAdultRouteOperationV1,
        frozen_turn: LeanSceneTurnInputV1,
    ) -> AdultRouteOperationOutcomeV1:
        if (
            prepared.route_state.world_id != frozen_turn.world_id
            or prepared.route_state.branch_id != frozen_turn.branch_id
            or prepared.scene_request.exact_current_source != frozen_turn.exact_user_source
        ):
            raise StateConflictError("adult Regenerate changed frozen turn authority")
        scope_sha256 = domain_sha256(
            "cera.pi_scene.full_model_adult_regenerate_scope.v1",
            {
                "operation_sha256": prepared.operation_sha256,
                "turn_sha256": canonical_sha256(frozen_turn),
                "scene_request_sha256": canonical_sha256(prepared.scene_request),
            },
        )
        context = AdultRoleViewContextV1(
            world_id=frozen_turn.world_id,
            branch_id=frozen_turn.branch_id,
            scene_id=frozen_turn.scene_id,
            turn_id=f"turn:adult-regenerate:{scope_sha256[:24]}",
            candidate_id=prepared.candidate_id,
            current_state=dict(frozen_turn.current_state),
            characters={key: dict(value) for key, value in frozen_turn.characters.items()},
            relationships={key: dict(value) for key, value in frozen_turn.relationships.items()},
            recent_prose=tuple(frozen_turn.recent_prose),
            relevant_memories={
                key: dict(value) for key, value in frozen_turn.relevant_memories.items()
            },
            voice_examples=dict(frozen_turn.voice_examples),
            accepted_records=(),
        )
        self._adult_turns.bind(prepared, frozen_turn)
        action_identity = self.adult_stage_retry_actions.bind_successor_for_current_action(
            prepared=prepared,
            frozen_turn=frozen_turn,
        )
        source = AdultStageRetryExecutionInputV1(
            generation_id=f"adult-operation:{prepared.operation_sha256}",
            prepared=_adult_pipeline_input(prepared),
            role_context=context,
            accepted_parent_session=None,
        )
        before_stage_dispatch: (
            Callable[
                [ProviderStageRetryOccurrenceScopeV1, ProviderStageFrozenPacketV1],
                None,
            ]
            | None
        ) = None
        if action_identity is not None:

            def bind_stage_dispatch(
                scope: ProviderStageRetryOccurrenceScopeV1,
                packet: ProviderStageFrozenPacketV1,
            ) -> None:
                self.adult_stage_retry_actions.bind_stage_dispatch(
                    scope=scope,
                    packet=packet,
                )

            before_stage_dispatch = bind_stage_dispatch
        return _execute_adult_stage_retry(
            continuation=self.adult,
            prepared=prepared,
            source=source,
            before_stage_dispatch=before_stage_dispatch,
        )


def build_provider_stage_retry_production_assembly(
    *,
    runtime_root: Path,
    scene_store: LeanSceneStore,
    sol_ledger: ContinuousProviderCallLedger,
    pi_adapter: PiSceneAdapter,
    ordinary_ports: OrdinaryStageProviderPortsV1,
    semantic_validator_maximum_output_tokens: int = 4_096,
) -> ProviderStageRetryProductionAssemblyV1:
    """Construct all Retry custody and adapters without provider/lifecycle calls."""

    root = (runtime_root / "provider_stage_retry").resolve()
    owner_root = root / "protected" / "ordinary_owners"
    custody = ProtectedOrdinaryStageRetryCustodyStoreV1(root / "protected" / "ordinary_requests")
    retrieval = ActiveDerivedPlannerRetrievalManifestPortV1(scene_store.root)
    planner_sol_current, planner_sol_prefix = _sol_ledger_ports(
        sol_ledger,
        owner="planner",
    )
    luna_sol_current, luna_sol_prefix = _sol_ledger_ports(
        sol_ledger,
        owner="validator",
    )
    reader_sol_current, reader_sol_prefix = _sol_ledger_ports(
        sol_ledger,
        owner="reader",
    )
    pi_current, pi_prefix = _pi_ledger_ports(pi_adapter.operation_ledger)

    planner_receipts: dict[int, ProviderStageReceiptMetricsV1] = {}
    planner_factory = OrdinaryProviderStageAttemptOwnerFactoryV1(
        stage=ProviderStage.PLANNER,
        lifecycle=ProviderStageOwnerLifecycleClass.RETAINED_INITIAL_FRESH_MANUAL_RETRY,
        boundary_kind=ProviderStageBoundaryKind.CODEX,
        maximum_provider_operations=1,
        protected_root=owner_root,
        read_current_ledger=planner_sol_current,
        read_ledger_prefix=planner_sol_prefix,
        build_request=planner_request_from_frozen_input,
        invoke_provider=lambda request, chain_id, attempt: _invoke_planner_and_bind_receipt(
            request=request,
            chain_id=chain_id,
            attempt_number=attempt,
            custody=custody,
            ports=ordinary_ports,
            receipts=planner_receipts,
        ),
        serialize_result=cast(Callable[[object], bytes], serialize_planner_result),
        result_receipt_metrics=lambda result: _pop_result_receipt(
            planner_receipts,
            result,
            ProviderStage.PLANNER,
        ),
        semantic_disposition=ordinary_nonsemantic_disposition,
        retire_failed_owner=lambda retirement, attempt_root: _retire_planner_owner(
            retirement=retirement,
            attempt_root=attempt_root,
            custody=custody,
            ports=ordinary_ports,
        ),
    )

    writer_factory = _pi_ordinary_factory(
        stage=ProviderStage.WRITER,
        pi_adapter=pi_adapter,
        owner_root=owner_root,
        current=pi_current,
        prefix=pi_prefix,
    )
    recorder_factory = _pi_ordinary_factory(
        stage=ProviderStage.RECORDER,
        pi_adapter=pi_adapter,
        owner_root=owner_root,
        current=pi_current,
        prefix=pi_prefix,
    )
    luna_receipts: dict[int, ProviderStageReceiptMetricsV1] = {}
    luna_factory = OrdinaryProviderStageAttemptOwnerFactoryV1(
        stage=ProviderStage.SEMANTIC_VALIDATOR,
        lifecycle=ProviderStageOwnerLifecycleClass.FRESH_SINGLE_USE,
        boundary_kind=ProviderStageBoundaryKind.CODEX,
        maximum_provider_operations=1,
        protected_root=owner_root,
        read_current_ledger=luna_sol_current,
        read_ledger_prefix=luna_sol_prefix,
        build_request=semantic_validation_input_from_frozen_input,
        invoke_provider=lambda request, chain_id, attempt: _invoke_luna_and_bind_receipt(
            request=request,
            chain_id=chain_id,
            attempt_number=attempt,
            ports=ordinary_ports,
            receipts=luna_receipts,
        ),
        serialize_result=cast(Callable[[object], bytes], serialize_semantic_validation_result),
        result_receipt_metrics=lambda result: _pop_result_receipt(
            luna_receipts, result, ProviderStage.SEMANTIC_VALIDATOR
        ),
        semantic_disposition=cast(
            Callable[[object], ProviderStageSemanticDisposition],
            semantic_validation_disposition,
        ),
        retire_failed_owner=lambda retirement, attempt_root: _retire_one_shot_owner(
            retirement, attempt_root, "fresh_luna_archived_by_validator_factory"
        ),
    )
    reader_receipts: dict[int, ProviderStageReceiptMetricsV1] = {}
    reader_factory = OrdinaryProviderStageAttemptOwnerFactoryV1(
        stage=ProviderStage.READER,
        lifecycle=ProviderStageOwnerLifecycleClass.FRESH_SINGLE_USE,
        boundary_kind=ProviderStageBoundaryKind.CODEX,
        maximum_provider_operations=1,
        protected_root=owner_root,
        read_current_ledger=reader_sol_current,
        read_ledger_prefix=reader_sol_prefix,
        build_request=reader_validation_input_from_frozen_input,
        invoke_provider=lambda request, chain_id, attempt: _invoke_reader_and_bind_receipt(
            request=request,
            chain_id=chain_id,
            attempt_number=attempt,
            ports=ordinary_ports,
            receipts=reader_receipts,
        ),
        serialize_result=cast(Callable[[object], bytes], serialize_reader_validation_result),
        result_receipt_metrics=lambda result: _pop_result_receipt(
            reader_receipts,
            result,
            ProviderStage.READER,
        ),
        semantic_disposition=cast(
            Callable[[object], ProviderStageSemanticDisposition],
            reader_validation_disposition,
        ),
        retire_failed_owner=lambda retirement, attempt_root: _retire_one_shot_owner(
            retirement, attempt_root, "fresh_reader_archived_by_validator_factory"
        ),
    )

    factories = {
        ProviderStage.PLANNER: planner_factory,
        ProviderStage.WRITER: writer_factory,
        ProviderStage.SEMANTIC_VALIDATOR: luna_factory,
        ProviderStage.READER: reader_factory,
        ProviderStage.RECORDER: recorder_factory,
    }
    ordinary_adapters: list[ProviderStageRuntimeAdapterV1] = []
    for stage in (
        ProviderStage.PLANNER,
        ProviderStage.WRITER,
        ProviderStage.SEMANTIC_VALIDATOR,
        ProviderStage.READER,
        ProviderStage.RECORDER,
    ):
        factory = factories[stage]
        owner_factory: object = factory
        if stage is ProviderStage.PLANNER:
            owner_factory = PlannerManifestRevalidatingOwnerFactoryV1(
                wrapped=factory,
                custody_store=custody,
                snapshot_port=retrieval,
            )
        ordinary_adapters.append(
            ProviderStageRuntimeAdapterV1(
                stage=stage,
                owner_factory=cast(Any, owner_factory),
                ambiguity_reconciler=factory,
                downstream_binder=OrdinaryStageResultBinderV1(stage),
            )
        )

    adult_adapters = adult_stage_retry_runtime_adapters(
        pi_adapter=pi_adapter,
        protected_runtime_root=root / "protected" / "adult_owners",
    )
    authority = SQLiteAuthorityStore(root / "authority.sqlite3")
    runtime = ProviderStageRetryRuntimeServiceV1(
        authority_store=authority,
        protected_blob_store=TrustedLocalProtectedStageBlobStore(root / "protected" / "blobs"),
        registrations=(*ordinary_adapters, *adult_adapters),
    )
    configurations = production_provider_stage_configurations(
        pi_adapter,
        semantic_validator_maximum_output_tokens=(semantic_validator_maximum_output_tokens),
    )
    ordinary = OrdinaryProviderStageRetryRuntimeV1(
        service=runtime,
        custody_store=custody,
        configurations=configurations,
    )
    packets = AdultStageRetryPacketFactoryV1(
        scene_configuration=configurations[ProviderStage.ADULT_SCENE],
        filter_configuration=configurations[ProviderStage.ADULT_FILTER],
    )
    adult_operation_store = ProtectedAdultOperationStore(runtime_root / "protected_adult")
    adult = AdultStageRetryContinuationServiceV1(
        runtime=runtime,
        packets=packets,
        pi_adapter=pi_adapter,
        operation_store=adult_operation_store,
        protected_mapping_root=root / "protected" / "adult_continuation",
    )
    adult_stage_retry_actions = AdultProviderStageReviewActionRuntimeV1(
        root=root / "protected" / "adult_review_actions",
        operation_store=adult_operation_store,
    )
    completions = _ProtectedTerminalCompletionStoreV1(root / "protected" / "terminal_completions")
    adult_turns = _ProtectedAdultRetryTurnStoreV1(root / "protected" / "adult_turns")
    ordinary_http = _LateBoundOrdinaryHttpContinuationV1(
        ordinary=ordinary,
        completions=completions,
    )
    adult_http = _LateBoundAdultHttpContinuationV1(
        continuation=adult,
        actions=adult_stage_retry_actions,
        runtime=runtime,
        turn_store=adult_turns,
        completions=completions,
    )
    http = ProviderStageRetryHttpControllerV1(
        runtime=runtime,
        cursor_store=ProtectedProviderStageRetryHttpCursorStoreV1(root / "protected" / "http"),
        registrations=tuple(
            ProviderStageRetryHttpRegistrationV1(
                stage=stage,
                continuation=(ordinary_http if stage in _ORDINARY_STAGES else adult_http),
            )
            for stage in ProviderStage
        ),
    )
    operator_recovery = ProviderStageOperatorRecoveryV1(
        runtime=runtime,
        scene_store=scene_store,
        ordinary=ordinary,
        root=root / "protected" / "operator_recovery",
    )
    operator_recovery.bind_http(http)
    pi_maximum = pi_adapter.operation_ledger.maximum_operations_per_invocation
    registrations = tuple(
        ProviderStageAssemblyRegistrationV1(
            stage=stage,
            lifecycle=(
                ProviderStageOwnerLifecycleClass.RETAINED_INITIAL_FRESH_MANUAL_RETRY
                if stage is ProviderStage.PLANNER
                else (
                    ProviderStageOwnerLifecycleClass.FRESH_SINGLE_USE
                    if stage in {ProviderStage.SEMANTIC_VALIDATOR, ProviderStage.READER}
                    else ProviderStageOwnerLifecycleClass.ONE_SHOT_PROCESS
                )
            ),
            maximum_provider_operations=(
                1
                if stage
                in {
                    ProviderStage.PLANNER,
                    ProviderStage.SEMANTIC_VALIDATOR,
                    ProviderStage.READER,
                }
                else pi_maximum
            ),
        )
        for stage in ProviderStage
    )
    return ProviderStageRetryProductionAssemblyV1(
        runtime=runtime,
        ordinary=ordinary,
        adult=adult,
        adult_stage_retry_actions=adult_stage_retry_actions,
        retrieval=retrieval,
        http=http,
        ordinary_http=ordinary_http,
        adult_http=adult_http,
        adult_turns=adult_turns,
        operator_recovery=operator_recovery,
        registrations=registrations,
    )


def production_provider_stage_configurations(
    pi_adapter: PiSceneAdapter,
    *,
    semantic_validator_maximum_output_tokens: int = 4_096,
) -> dict[ProviderStage, ProviderStageConfigurationV1]:
    if (
        type(semantic_validator_maximum_output_tokens) is not int
        or not 1 <= semantic_validator_maximum_output_tokens <= 131_072
    ):
        raise ContractValidationError("semantic Validator output-token budget is invalid")
    pi_stage = {
        "automatic_retry": False,
        "fallback": False,
        "maximum_provider_operations": (
            pi_adapter.operation_ledger.maximum_operations_per_invocation
        ),
    }
    output: dict[ProviderStage, ProviderStageConfigurationV1] = {}
    for stage in ProviderStage:
        codex = stage in {
            ProviderStage.PLANNER,
            ProviderStage.SEMANTIC_VALIDATOR,
            ProviderStage.READER,
        }
        output[stage] = ProviderStageConfigurationV1.create(
            stage=stage,
            model_id=(
                "gpt-5.6-sol"
                if stage in {ProviderStage.PLANNER, ProviderStage.READER}
                else (
                    "gpt-5.6-luna"
                    if stage is ProviderStage.SEMANTIC_VALIDATOR
                    else pi_adapter.model
                )
            ),
            reasoning_mode=(
                "medium"
                if stage in {ProviderStage.PLANNER, ProviderStage.READER}
                else "xhigh"
                if stage is ProviderStage.SEMANTIC_VALIDATOR
                else "off"
            ),
            routing={"automatic_retry": False, "fallback": False},
            content_policy_route=(
                "protected_adult"
                if stage in {ProviderStage.ADULT_SCENE, ProviderStage.ADULT_FILTER}
                else "ordinary"
            ),
            stage_configuration=(
                {
                    "maximum_output_tokens": (
                        12_288
                        if stage is ProviderStage.PLANNER
                        else semantic_validator_maximum_output_tokens
                        if stage is ProviderStage.SEMANTIC_VALIDATOR
                        else 4_096
                    ),
                    "single_provider_operation": True,
                }
                if codex
                else pi_stage
            ),
        )
    return output


def _pi_ordinary_factory(
    *,
    stage: ProviderStage,
    pi_adapter: PiSceneAdapter,
    owner_root: Path,
    current: Callable[[], ProviderStageLedgerSnapshotV1],
    prefix: Callable[[str], ProviderStageLedgerSnapshotV1],
) -> OrdinaryProviderStageAttemptOwnerFactoryV1:
    if stage not in {ProviderStage.WRITER, ProviderStage.RECORDER}:
        raise ContractValidationError("Pi ordinary factory changed stage")
    decoder = (
        writer_invocation_from_frozen_input
        if stage is ProviderStage.WRITER
        else recorder_invocation_from_frozen_input
    )
    return OrdinaryProviderStageAttemptOwnerFactoryV1(
        stage=stage,
        lifecycle=ProviderStageOwnerLifecycleClass.ONE_SHOT_PROCESS,
        boundary_kind=ProviderStageBoundaryKind.PI_DEEPSEEK,
        maximum_provider_operations=(pi_adapter.operation_ledger.maximum_operations_per_invocation),
        protected_root=owner_root,
        read_current_ledger=current,
        read_ledger_prefix=prefix,
        build_request=decoder,
        invoke_provider=lambda request, _chain, _attempt: pi_adapter.invoke(cast(Any, request)),
        serialize_result=cast(Callable[[object], bytes], serialize_pi_result),
        result_receipt_metrics=lambda result: pi_provider_result_receipt_metrics(
            cast(PiSceneInvocationResultV1, result)
        ),
        semantic_disposition=ordinary_nonsemantic_disposition,
        retire_failed_owner=lambda retirement, attempt_root: _retire_one_shot_owner(
            retirement, attempt_root, "pi_process_has_no_live_handle"
        ),
        classify_non_retryable=_classify_pi_non_retryable_failure,
    )


def _classify_pi_non_retryable_failure(
    failure: ProviderTransportError,
) -> ProviderStageFailureClass:
    if isinstance(failure, PiOutputLimitError):
        return ProviderStageFailureClass.OUTPUT_LIMIT_TRUNCATED
    return ProviderStageFailureClass.PROVIDER_FAILURE_NOT_RETRYABLE


def _invoke_planner(
    *,
    request: object,
    chain_id: str,
    attempt_number: int,
    custody: ProtectedOrdinaryStageRetryCustodyStoreV1,
    ports: OrdinaryStageProviderPortsV1,
) -> object:
    del attempt_number
    pending = custody.load_request_for_chain(chain_id)
    planner = ports.resolve_planner(pending.turn_input)
    plan = getattr(planner, "plan", None)
    if not callable(plan):
        raise StateConflictError("ordinary Retry Planner is unavailable")
    return plan(request)


def _invoke_planner_and_bind_receipt(
    *,
    request: object,
    chain_id: str,
    attempt_number: int,
    custody: ProtectedOrdinaryStageRetryCustodyStoreV1,
    ports: OrdinaryStageProviderPortsV1,
    receipts: dict[int, ProviderStageReceiptMetricsV1],
) -> object:
    result = _invoke_planner(
        request=request,
        chain_id=chain_id,
        attempt_number=attempt_number,
        custody=custody,
        ports=ports,
    )
    pending = custody.load_request_for_chain(chain_id)
    receipts[id(result)] = _continuous_receipt_metrics(
        ports.planner_provider_result(pending.turn_input)
    )
    return result


def _invoke_luna_and_bind_receipt(
    *,
    request: object,
    chain_id: str,
    attempt_number: int,
    ports: OrdinaryStageProviderPortsV1,
    receipts: dict[int, ProviderStageReceiptMetricsV1],
) -> BoundSemanticValidationV1:
    if not isinstance(request, tuple) or len(request) != 2:
        raise ContractValidationError("Luna Retry request changed shape")
    result = ports.semantic_validator.validate(request[0], request[1])
    receipts[id(result)] = _continuous_receipt_metrics(ports.luna_provider_result())
    return result


def _invoke_reader_and_bind_receipt(
    *,
    request: object,
    chain_id: str,
    attempt_number: int,
    ports: OrdinaryStageProviderPortsV1,
    receipts: dict[int, ProviderStageReceiptMetricsV1],
) -> BoundReaderValidationV1:
    del chain_id, attempt_number
    if not isinstance(request, tuple) or len(request) != 2:
        raise ContractValidationError("Reader Retry request changed shape")
    result = ports.reader_validator.validate(request[0], request[1])
    receipts[id(result)] = _continuous_receipt_metrics(ports.reader_provider_result())
    return result


def _pop_result_receipt(
    receipts: dict[int, ProviderStageReceiptMetricsV1],
    result: object,
    stage: ProviderStage,
) -> ProviderStageReceiptMetricsV1:
    try:
        return receipts.pop(id(result))
    except KeyError as exc:
        raise StateConflictError(f"{stage.value} Retry receipt is unavailable") from exc


def _continuous_receipt_metrics(
    result: ContinuousProviderResultV1,
) -> ProviderStageReceiptMetricsV1:
    if type(result) is not ContinuousProviderResultV1:
        raise ContractValidationError("Codex Retry provider result changed")
    receipt = result.provider_receipt
    required = (
        "receipt_sha256",
        "external_provider_calls",
        "duration_ms",
        "input_tokens",
        "cached_input_tokens",
        "output_tokens",
        "reasoning_output_tokens",
    )
    if any(not hasattr(receipt, name) for name in required):
        raise ContractValidationError("Codex Retry receipt is incomplete")
    return ProviderStageReceiptMetricsV1(
        receipt_evidence_sha256=domain_sha256(
            "cera.provider_stage_codex_receipt.v1",
            {
                "receipt_sha256": receipt.receipt_sha256,
                "operation_telemetry_sha256": (
                    None
                    if result.operation_telemetry is None
                    else result.operation_telemetry.telemetry_sha256
                ),
            },
        ),
        provider_operations=receipt.external_provider_calls,
        duration_ms=receipt.duration_ms,
        input_tokens=receipt.input_tokens,
        cached_input_tokens=receipt.cached_input_tokens,
        output_tokens=receipt.output_tokens,
        reasoning_tokens=receipt.reasoning_output_tokens,
    )


def _retire_planner_owner(
    *,
    retirement: ProviderStageOwnerRetirementV1,
    attempt_root: Path,
    custody: ProtectedOrdinaryStageRetryCustodyStoreV1,
    ports: OrdinaryStageProviderPortsV1,
) -> str:
    pending = custody.load_request_for_chain(retirement.chain_id)
    intent_path = attempt_root / "RETIREMENT_INTENT.json"
    if intent_path.exists():
        value = json.loads(intent_path.read_text(encoding="utf-8"))
        expected = value.get("physical_thread_sha256")
    else:
        expected = ports.planner_active_thread_sha256(pending.turn_input)
        _write_once(
            intent_path,
            canonical_bytes(
                {
                    "schema_version": "cera.planner_retry_retirement_intent.v1",
                    "chain_id": retirement.chain_id,
                    "attempt_number": retirement.attempt_number,
                    "physical_thread_sha256": expected,
                }
            ),
        )
    if expected is not None:
        if not isinstance(expected, str) or not re_is_sha256(expected):
            raise StateConflictError("Planner Retry retirement thread is invalid")
        ports.retire_planner_thread(pending.turn_input, expected)
    evidence = domain_sha256(
        "cera.planner_retry_owner_retired.v1",
        {
            "chain_id": retirement.chain_id,
            "attempt_number": retirement.attempt_number,
            "session_scope_sha256": retirement.session_scope_sha256,
            "failure_class": retirement.failure_class.value,
            "physical_thread_sha256": expected,
            "retained_owner_fenced": True,
        },
    )
    _write_once(
        attempt_root / "OWNER_RETIRED.json",
        canonical_bytes({"retirement_evidence_sha256": evidence}),
    )
    return evidence


def _retire_one_shot_owner(
    retirement: ProviderStageOwnerRetirementV1,
    attempt_root: Path,
    proof: str,
) -> str:
    evidence = domain_sha256(
        "cera.ordinary_one_shot_owner_retired.v1",
        {
            "chain_id": retirement.chain_id,
            "attempt_number": retirement.attempt_number,
            "stage": retirement.stage.value,
            "failure_class": retirement.failure_class.value,
            "proof": proof,
        },
    )
    _write_once(
        attempt_root / "OWNER_RETIRED.json",
        canonical_bytes({"retirement_evidence_sha256": evidence}),
    )
    return evidence


def _execute_adult_stage_retry(
    *,
    continuation: AdultStageRetryContinuationServiceV1,
    prepared: PreparedAdultRouteOperationV1,
    source: AdultStageRetryExecutionInputV1,
    before_stage_dispatch: Callable[
        [ProviderStageRetryOccurrenceScopeV1, ProviderStageFrozenPacketV1],
        None,
    ]
    | None = None,
) -> AdultRouteOperationOutcomeV1:
    progress = continuation.execute_or_continue(
        prepared=prepared,
        source=source,
        before_stage_dispatch=before_stage_dispatch,
    )
    if not progress.completed:
        assert progress.pending_chain_id is not None
        raise ProviderStageRetryPendingError(
            continuation.runtime.canonical_status(chain_id=progress.pending_chain_id)
        )
    assert progress.execution is not None
    return classify_prepared_adult_execution(prepared, progress.execution)


def _adult_pipeline_input(prepared: PreparedAdultRouteOperationV1) -> AdultPipelineInputV1:
    return AdultPipelineInputV1(
        request_id=prepared.request_id,
        candidate_id=prepared.candidate_id,
        world_id=prepared.route_state.world_id,
        branch_id=prepared.route_state.branch_id,
        accepted_head_sha256=prepared.route_state.accepted_head_sha256,
        scene_request=prepared.scene_request,
    )


def _sol_ledger_ports(
    ledger: ContinuousProviderCallLedger,
    *,
    owner: str,
) -> tuple[
    Callable[[], ProviderStageLedgerSnapshotV1],
    Callable[[str], ProviderStageLedgerSnapshotV1],
]:
    if owner not in {"planner", "validator", "reader"}:
        raise ContractValidationError("Sol provider-stage ledger owner changed")

    def owner_events() -> tuple[dict[str, Any], ...]:
        return tuple(event for event in ledger.events if event.get("owner") == owner)

    def conservative_operations(events: tuple[dict[str, Any], ...]) -> int:
        invoked = {
            cast(str, event["call_id"])
            for event in events
            if event.get("state") == ProviderCallState.TRANSPORT_INVOKED.value
        }
        last_state: dict[str, str] = {}
        for event in events:
            call_id = event.get("call_id")
            state = event.get("state")
            if isinstance(call_id, str) and isinstance(state, str):
                last_state[call_id] = state
        unresolved = {
            call_id
            for call_id, state in last_state.items()
            if state
            in {
                ProviderCallState.PREPARED.value,
                ProviderCallState.WORKER_STARTED.value,
                ProviderCallState.WORKER_PREFLIGHT.value,
            }
        }
        return len(invoked | unresolved)

    def snapshot(events: tuple[dict[str, Any], ...]) -> ProviderStageLedgerSnapshotV1:
        return ProviderStageLedgerSnapshotV1(
            prefix_sha256=canonical_sha256(events),
            provider_operations_total=conservative_operations(events),
        )

    def current() -> ProviderStageLedgerSnapshotV1:
        return snapshot(owner_events())

    def prefix(prefix_sha256: str) -> ProviderStageLedgerSnapshotV1:
        events = owner_events()
        for length in range(len(events) + 1):
            candidate = events[:length]
            if canonical_sha256(candidate) == prefix_sha256:
                return snapshot(candidate)
        raise StateConflictError("Sol provider-stage ledger prefix is unavailable")

    return current, prefix


def _pi_ledger_ports(
    ledger: PiProviderOperationLedger,
) -> tuple[
    Callable[[], ProviderStageLedgerSnapshotV1],
    Callable[[str], ProviderStageLedgerSnapshotV1],
]:
    def snapshot(events: tuple[dict[str, Any], ...]) -> ProviderStageLedgerSnapshotV1:
        return ProviderStageLedgerSnapshotV1(
            prefix_sha256=canonical_sha256(events),
            provider_operations_total=sum(
                event.get("event") == "provider_operation_started" for event in events
            ),
        )

    def current() -> ProviderStageLedgerSnapshotV1:
        return snapshot(tuple(ledger.events))

    def prefix(prefix_sha256: str) -> ProviderStageLedgerSnapshotV1:
        events = tuple(ledger.events)
        for length in range(len(events) + 1):
            candidate = events[:length]
            if canonical_sha256(candidate) == prefix_sha256:
                return snapshot(candidate)
        raise StateConflictError("Pi provider-stage ledger prefix is unavailable")

    return current, prefix


def _ordinary_disposition_sha256(value: _OrdinaryAttemptDispositionV1) -> str:
    payload = to_primitive(value)
    assert isinstance(payload, dict)
    payload.pop("disposition_sha256")
    return canonical_sha256(payload)


def _read_ordinary_disposition(path: Path) -> _OrdinaryAttemptDispositionV1 | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StateConflictError("ordinary stage disposition is unreadable") from exc
    if not isinstance(payload, dict):
        raise StateConflictError("ordinary stage disposition changed shape")
    try:
        raw_metrics = cast(Mapping[str, Any], payload["metrics"])
        return _OrdinaryAttemptDispositionV1(
            schema_version=payload["schema_version"],
            chain_id=payload["chain_id"],
            attempt_number=payload["attempt_number"],
            stage=ProviderStage(payload["stage"]),
            outcome_kind=payload["outcome_kind"],
            result_sha256=payload["result_sha256"],
            result_evidence_sha256=payload["result_evidence_sha256"],
            semantic_disposition=(
                None
                if payload["semantic_disposition"] is None
                else ProviderStageSemanticDisposition(payload["semantic_disposition"])
            ),
            failure_class=(
                None
                if payload["failure_class"] is None
                else ProviderStageFailureClass(payload["failure_class"])
            ),
            failure_evidence_sha256=payload["failure_evidence_sha256"],
            metrics=ProviderStageAttemptMetricsV1(**raw_metrics),
            disposition_sha256=payload["disposition_sha256"],
        )
    except (KeyError, TypeError, ValueError, ContractValidationError) as exc:
        raise StateConflictError("ordinary stage disposition is invalid") from exc


def _write_once(path: Path, exact_bytes: bytes) -> None:
    if path.exists():
        if path.read_bytes() != exact_bytes:
            raise StateConflictError("protected provider-stage artifact changed")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{uuid4().hex}.tmp"
    try:
        with temporary.open("xb") as handle:
            handle.write(exact_bytes)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()
    if path.read_bytes() != exact_bytes:
        raise StateConflictError("protected provider-stage artifact readback changed")


__all__ = [
    "OrdinaryStageProviderPortsV1",
    "ProviderStageAssemblyRegistrationV1",
    "ProviderStageOperatorRecoveryReceiptV1",
    "ProviderStageOperatorRecoveryV1",
    "ProviderStageOwnerLifecycleClass",
    "ProviderStageRetryProductionAssemblyV1",
    "build_provider_stage_retry_production_assembly",
    "production_provider_stage_configurations",
]
