from __future__ import annotations

import unittest
from collections.abc import Callable
from pathlib import Path
from tempfile import TemporaryDirectory

from cera.errors import ContractValidationError, ErrorCode, StateConflictError
from cera.evaluation import EvaluationRole
from cera.ids import IdKind, TypedId
from cera.pi_scene.contracts import PiWriterReceiptV1, SceneRoute
from cera.pi_scene.pi_adapter import PiOutputLimitError, PiSceneInvocationResultV1
from cera.pi_scene.provider_stage_retry import (
    ProviderStage,
    ProviderStageFailureClass,
    ProviderStageRetryPhase,
)
from cera.pi_scene.provider_stage_retry_adapters import (
    CallableProviderStageAttemptOwnerV1,
    ProviderStageAttemptOwnerBindingV1,
    ProviderStageBoundaryKind,
    ProviderStageLedgerSnapshotV1,
    ProviderStageNonRetryableClassifierPort,
    ProviderStageOwnerRetirementV1,
    ProviderStageReceiptMetricsV1,
    codex_provider_result_receipt_metrics,
    pi_provider_result_receipt_metrics,
)
from cera.pi_scene.provider_stage_retry_assembly import _classify_pi_non_retryable_failure
from cera.pi_scene.provider_stage_retry_blob import TrustedLocalProtectedStageBlobStore
from cera.pi_scene.provider_stage_retry_executor import (
    ProviderStageAttemptOutcomeV1,
    ProviderStageClosedFailureV1,
    ProviderStageDispatchAmbiguousV1,
    ProviderStageNonRetryableFailureV1,
    ProviderStagePretransportFailureV1,
    ProviderStagePretransportNonRetryableFailureV1,
    ProviderStageRetryExecutorV1,
    ProviderStageSemanticDisposition,
    ProviderStageSuccessfulResultV1,
)
from cera.pi_scene.provider_stage_retry_packets import (
    ProviderStageConfigurationV1,
    freeze_writer_stage_packet,
)
from cera.pi_scene.provider_stage_retry_scope import (
    ProviderStageRetryOccurrenceScopeV1,
)
from cera.providers.codex_observability import (
    CodexOperationTelemetryV1,
    CodexUsageStepV1,
)
from cera.providers.models import (
    LiveProviderCallReceipt,
    ModelIdentitySource,
    ProviderCallResult,
    ProviderName,
    ProviderRetryableFailureCategory,
    ProviderTransportError,
)
from cera.serialization import bytes_sha256, domain_sha256, text_sha256
from cera.storage.provider_stage_retry_store import SQLiteProviderStageRetryStore
from cera.storage.sqlite_store import SQLiteAuthorityStore


def _sha(label: str) -> str:
    return text_sha256(label)


_CHAIN_ID = "stage-retry-" + _sha("adapter-chain")
_EXACT_INPUT = b"exact frozen provider-stage packet"


def _codex_receipt(
    *,
    request_sha256: str,
    output_text: str,
) -> LiveProviderCallReceipt:
    return LiveProviderCallReceipt(
        schema_version=LiveProviderCallReceipt.SCHEMA_VERSION,
        provider_receipt_id=TypedId(IdKind.PROVIDER_RECEIPT, "adapter-test"),
        route_sha256=_sha("codex-route"),
        provider=ProviderName.OPENAI_CODEX,
        role=EvaluationRole.SCENE_REASONER,
        requested_model="gpt-5.6-sol",
        returned_model="gpt-5.6-sol",
        model_revision="provider-unverified",
        model_identity_source=ModelIdentitySource.EXPLICIT_REQUEST,
        model_identity_verified=False,
        request_sha256=request_sha256,
        output_sha256=text_sha256(output_text),
        provider_request_id_sha256=_sha("provider-request"),
        system_fingerprint_sha256=None,
        duration_ms=12,
        input_tokens=100,
        cached_input_tokens=80,
        output_tokens=10,
        reasoning_output_tokens=4,
        cost_microusd=0,
        cost_is_estimate=False,
        quota_metered=True,
        external_provider_calls=1,
        automatic_retry_count=0,
        story_authority_writes=0,
        retains_raw_source=False,
        retains_story_prose=False,
        retains_private_evidence=False,
        retains_prompt=False,
        retains_secret=False,
    )


class _Boundary:
    def __init__(self) -> None:
        self.session = _sha("session-1")
        self.snapshot = ProviderStageLedgerSnapshotV1(_sha("ledger-0"), 0)
        self.provider_calls = 0
        self.retirements: list[ProviderStageOwnerRetirementV1] = []

    def advance(self, operations: int) -> None:
        total = self.snapshot.provider_operations_total + operations
        self.snapshot = ProviderStageLedgerSnapshotV1(_sha(f"ledger-{total}"), total)


class _StaticNonRetryableClassifier:
    def __init__(self, failure_class: ProviderStageFailureClass) -> None:
        self.failure_class = failure_class

    def __call__(
        self,
        failure: ProviderTransportError,
    ) -> ProviderStageFailureClass:
        del failure
        return self.failure_class


def _owner[ResultT](
    boundary: _Boundary,
    invoke_provider: Callable[[bytes], ResultT],
    *,
    stage: ProviderStage = ProviderStage.WRITER,
    chain_id: str = _CHAIN_ID,
    stage_input_sha256: str | None = None,
    build_request: Callable[[bytes], bytes] | None = None,
    serialize_result: Callable[[ResultT], bytes] | None = None,
    result_receipt_metrics: (Callable[[ResultT], ProviderStageReceiptMetricsV1] | None) = None,
    semantic_disposition: (Callable[[ResultT], ProviderStageSemanticDisposition] | None) = None,
    classify_non_retryable: (ProviderStageNonRetryableClassifierPort | None) = None,
    read_session_scope_sha256: Callable[[], str] | None = None,
    read_ledger_snapshot: Callable[[], ProviderStageLedgerSnapshotV1] | None = None,
    clock_ns: Callable[[], int] | None = None,
    maximum_provider_operations: int = 1,
) -> CallableProviderStageAttemptOwnerV1[bytes, ResultT]:
    def retire(request: ProviderStageOwnerRetirementV1) -> str:
        boundary.retirements.append(request)
        return _sha("injected-retirement-proof")

    return CallableProviderStageAttemptOwnerV1(
        binding=ProviderStageAttemptOwnerBindingV1(
            chain_id=chain_id,
            attempt_number=1,
            stage=stage,
            boundary_kind=(
                ProviderStageBoundaryKind.CODEX
                if stage
                in {
                    ProviderStage.PLANNER,
                    ProviderStage.SEMANTIC_VALIDATOR,
                    ProviderStage.READER,
                }
                else ProviderStageBoundaryKind.PI_DEEPSEEK
            ),
            stage_input_sha256=stage_input_sha256 or bytes_sha256(_EXACT_INPUT),
            session_scope_sha256=boundary.session,
            ledger_before=boundary.snapshot,
            maximum_provider_operations=maximum_provider_operations,
        ),
        read_session_scope_sha256=(read_session_scope_sha256 or (lambda: boundary.session)),
        read_ledger_snapshot=read_ledger_snapshot or (lambda: boundary.snapshot),
        build_request=build_request or (lambda exact: exact),
        invoke_provider=invoke_provider,
        serialize_result=serialize_result or (lambda result: str(result).encode("utf-8")),
        result_receipt_metrics=(
            result_receipt_metrics
            or (
                lambda _result: ProviderStageReceiptMetricsV1(
                    receipt_evidence_sha256=_sha("fake-receipt"),
                    provider_operations=1,
                    duration_ms=12,
                    input_tokens=10,
                    cached_input_tokens=4,
                    output_tokens=3,
                    reasoning_tokens=2,
                )
            )
        ),
        semantic_disposition=(
            semantic_disposition or (lambda _result: ProviderStageSemanticDisposition.ACCEPTED)
        ),
        retire_owner=retire,
        classify_non_retryable=classify_non_retryable,
        clock_ns=clock_ns,
    )


def _invoke_prepared[ResultT](
    owner: CallableProviderStageAttemptOwnerV1[bytes, ResultT],
) -> (
    ProviderStageAttemptOutcomeV1
    | ProviderStagePretransportFailureV1
    | ProviderStagePretransportNonRetryableFailureV1
):
    prepared = owner.prepare(
        chain_id=owner.binding.chain_id,
        attempt_number=1,
        exact_input=_EXACT_INPUT,
    )
    if isinstance(
        prepared,
        (
            ProviderStagePretransportFailureV1,
            ProviderStagePretransportNonRetryableFailureV1,
        ),
    ):
        return prepared
    return prepared.invoke()


class ProviderStageRetryAdapterTests(unittest.TestCase):
    def test_reader_is_closed_to_the_codex_boundary(self) -> None:
        binding = _owner(
            _Boundary(),
            lambda exact: exact,
            stage=ProviderStage.READER,
        ).binding

        self.assertIs(binding.boundary_kind, ProviderStageBoundaryKind.CODEX)
        with self.assertRaisesRegex(
            ContractValidationError,
            "owner substitution detected",
        ):
            ProviderStageAttemptOwnerBindingV1(
                chain_id=binding.chain_id,
                attempt_number=binding.attempt_number,
                stage=binding.stage,
                boundary_kind=ProviderStageBoundaryKind.PI_DEEPSEEK,
                stage_input_sha256=binding.stage_input_sha256,
                session_scope_sha256=binding.session_scope_sha256,
                ledger_before=binding.ledger_before,
                maximum_provider_operations=binding.maximum_provider_operations,
            )

    def test_all_six_retry_categories_map_exactly_without_message_parsing(self) -> None:
        expected = {
            ProviderRetryableFailureCategory.TRANSPORT_TIMEOUT: (
                ProviderStageFailureClass.TRANSPORT_TIMEOUT
            ),
            ProviderRetryableFailureCategory.PROVIDER_UNAVAILABLE: (
                ProviderStageFailureClass.PROVIDER_UNAVAILABLE
            ),
            ProviderRetryableFailureCategory.PROVIDER_PROCESS_FAILED: (
                ProviderStageFailureClass.PROVIDER_PROCESS_FAILED
            ),
            ProviderRetryableFailureCategory.PROVIDER_STREAM_INCOMPLETE: (
                ProviderStageFailureClass.PROVIDER_STREAM_INCOMPLETE
            ),
            ProviderRetryableFailureCategory.PROVIDER_COMPLETION_INCOMPLETE: (
                ProviderStageFailureClass.PROVIDER_COMPLETION_INCOMPLETE
            ),
            ProviderRetryableFailureCategory.PROVIDER_OUTPUT_INVALID: (
                ProviderStageFailureClass.PROVIDER_OUTPUT_INVALID
            ),
        }
        for category, failure_class in expected.items():
            with self.subTest(category=category):
                boundary = _Boundary()

                def invoke(
                    _request: bytes,
                    *,
                    category: ProviderRetryableFailureCategory = category,
                    boundary: _Boundary = boundary,
                ) -> object:
                    boundary.provider_calls += 1
                    boundary.advance(1)
                    raise ProviderTransportError(
                        ErrorCode.PROVIDER_TRANSPORT_FAILED,
                        "misleading authentication context truncation wording",
                        external_provider_calls_observed=1,
                        retryable_failure_category=category,
                    )

                outcome = _invoke_prepared(_owner(boundary, invoke))

                self.assertIsInstance(outcome, ProviderStageClosedFailureV1)
                assert isinstance(outcome, ProviderStageClosedFailureV1)
                self.assertIs(outcome.failure_class, failure_class)
                self.assertEqual(outcome.metrics.provider_operations_observed, 1)
                self.assertEqual(boundary.provider_calls, 1)

    def test_closed_pi_multi_operation_failure_preserves_exact_count(self) -> None:
        boundary = _Boundary()

        def invoke(_request: bytes) -> object:
            boundary.provider_calls += 1
            boundary.advance(2)
            raise ProviderTransportError(
                ErrorCode.PROVIDER_TRANSPORT_FAILED,
                "closed Pi completion did not produce accepted output",
                external_provider_calls_observed=1,
                retryable_failure_category=(
                    ProviderRetryableFailureCategory.PROVIDER_COMPLETION_INCOMPLETE
                ),
            )

        outcome = _invoke_prepared(
            _owner(
                boundary,
                invoke,
                maximum_provider_operations=6,
            )
        )

        self.assertIsInstance(outcome, ProviderStageClosedFailureV1)
        assert isinstance(outcome, ProviderStageClosedFailureV1)
        self.assertIs(
            outcome.failure_class,
            ProviderStageFailureClass.PROVIDER_COMPLETION_INCOMPLETE,
        )
        self.assertEqual(outcome.metrics.provider_operations_observed, 2)
        self.assertEqual(outcome.metrics.provider_operations_conservative, 2)
        self.assertEqual(boundary.provider_calls, 1)

    def test_untyped_pi_multi_operation_failure_remains_ambiguous(self) -> None:
        boundary = _Boundary()

        def invoke(_request: bytes) -> object:
            boundary.provider_calls += 1
            boundary.advance(2)
            raise RuntimeError("provider disposition unavailable")

        outcome = _invoke_prepared(
            _owner(
                boundary,
                invoke,
                maximum_provider_operations=6,
            )
        )

        self.assertIsInstance(outcome, ProviderStageDispatchAmbiguousV1)
        assert isinstance(outcome, ProviderStageDispatchAmbiguousV1)
        self.assertEqual(outcome.metrics.provider_operations_observed, 2)
        self.assertEqual(outcome.metrics.provider_operations_conservative, 6)
        self.assertEqual(boundary.provider_calls, 1)

    def test_exact_non_retryable_conditions_and_generic_fallback_never_retry(self) -> None:
        classes = (
            ProviderStageFailureClass.AUTHENTICATION_FAILED,
            ProviderStageFailureClass.CONFIGURATION_FAILED,
            ProviderStageFailureClass.CONTEXT_SIZE_EXCEEDED,
            ProviderStageFailureClass.OUTPUT_LIMIT_TRUNCATED,
            ProviderStageFailureClass.BUDGET_EXHAUSTED,
        )
        for failure_class in classes:
            with self.subTest(failure_class=failure_class):
                boundary = _Boundary()

                def invoke(
                    _request: bytes,
                    *,
                    boundary: _Boundary = boundary,
                ) -> object:
                    boundary.provider_calls += 1
                    raise ProviderTransportError(
                        ErrorCode.PROVIDER_TRANSPORT_FAILED,
                        "text is deliberately irrelevant",
                        external_provider_calls_observed=0,
                    )

                outcome = _invoke_prepared(
                    _owner(
                        boundary,
                        invoke,
                        classify_non_retryable=_StaticNonRetryableClassifier(failure_class),
                    )
                )

                self.assertIsInstance(outcome, ProviderStageNonRetryableFailureV1)
                assert isinstance(outcome, ProviderStageNonRetryableFailureV1)
                self.assertIs(outcome.failure_class, failure_class)
                self.assertEqual(outcome.metrics.provider_operations_observed, 0)
                self.assertEqual(outcome.metrics.provider_operations_conservative, 0)

        boundary = _Boundary()

        def generic_failure(_request: bytes) -> object:
            raise ProviderTransportError(
                ErrorCode.PROVIDER_CONFIG_INVALID,
                "no exact upstream non-Retry class",
                external_provider_calls_observed=0,
            )

        generic = _invoke_prepared(_owner(boundary, generic_failure))
        self.assertIsInstance(generic, ProviderStageNonRetryableFailureV1)
        assert isinstance(generic, ProviderStageNonRetryableFailureV1)
        self.assertIs(
            generic.failure_class,
            ProviderStageFailureClass.PROVIDER_FAILURE_NOT_RETRYABLE,
        )

    def test_ordinary_pi_output_limit_has_exact_non_retryable_class(self) -> None:
        boundary = _Boundary()

        def invoke(_request: bytes) -> object:
            boundary.provider_calls += 1
            boundary.advance(1)
            raise PiOutputLimitError(
                ErrorCode.PROVIDER_TRANSPORT_FAILED,
                "closed output-limit completion",
                external_provider_calls_observed=1,
            )

        outcome = _invoke_prepared(
            _owner(
                boundary,
                invoke,
                classify_non_retryable=_classify_pi_non_retryable_failure,
            )
        )

        self.assertIsInstance(outcome, ProviderStageNonRetryableFailureV1)
        assert isinstance(outcome, ProviderStageNonRetryableFailureV1)
        self.assertIs(
            outcome.failure_class,
            ProviderStageFailureClass.OUTPUT_LIMIT_TRUNCATED,
        )
        self.assertEqual(outcome.metrics.provider_operations_observed, 1)
        self.assertEqual(outcome.metrics.provider_operations_conservative, 1)
        self.assertEqual(boundary.provider_calls, 1)

    def test_post_operation_retry_classes_require_one_observed_operation(self) -> None:
        for category in (
            ProviderRetryableFailureCategory.PROVIDER_STREAM_INCOMPLETE,
            ProviderRetryableFailureCategory.PROVIDER_COMPLETION_INCOMPLETE,
            ProviderRetryableFailureCategory.PROVIDER_OUTPUT_INVALID,
        ):
            with self.subTest(category=category):
                boundary = _Boundary()

                def invoke(
                    _request: bytes,
                    *,
                    category: ProviderRetryableFailureCategory = category,
                ) -> object:
                    raise ProviderTransportError(
                        ErrorCode.PROVIDER_TRANSPORT_FAILED,
                        "typed post-operation claim with no operation",
                        external_provider_calls_observed=0,
                        retryable_failure_category=category,
                    )

                outcome = _invoke_prepared(_owner(boundary, invoke))
                self.assertIsInstance(outcome, ProviderStageNonRetryableFailureV1)
                assert isinstance(outcome, ProviderStageNonRetryableFailureV1)
                self.assertIs(
                    outcome.failure_class,
                    ProviderStageFailureClass.PROVIDER_FAILURE_NOT_RETRYABLE,
                )
                self.assertEqual(outcome.metrics.provider_operations_observed, 0)
                self.assertEqual(outcome.metrics.provider_operations_conservative, 0)

    def test_untyped_or_unresolved_failure_is_dispatch_ambiguity(self) -> None:
        boundary = _Boundary()

        def invoke(_request: bytes) -> object:
            boundary.provider_calls += 1
            raise RuntimeError("never parsed")

        outcome = _invoke_prepared(_owner(boundary, invoke))

        self.assertIsInstance(outcome, ProviderStageDispatchAmbiguousV1)
        assert isinstance(outcome, ProviderStageDispatchAmbiguousV1)
        self.assertEqual(outcome.metrics.provider_operations_observed, 0)
        self.assertEqual(outcome.metrics.provider_operations_conservative, 1)

        clock_boundary = _Boundary()
        clock_reads = 0

        def clock() -> int:
            nonlocal clock_reads
            clock_reads += 1
            if clock_reads == 3:
                raise RuntimeError("clock failed after provider return")
            return clock_reads * 1_000_000

        def completed_provider(_request: bytes) -> str:
            clock_boundary.provider_calls += 1
            clock_boundary.advance(1)
            return "completed before local clock failure"

        clock_outcome = _invoke_prepared(_owner(clock_boundary, completed_provider, clock_ns=clock))
        self.assertIsInstance(clock_outcome, ProviderStageNonRetryableFailureV1)
        assert isinstance(clock_outcome, ProviderStageNonRetryableFailureV1)
        self.assertEqual(clock_boundary.provider_calls, 1)
        self.assertEqual(clock_outcome.metrics.provider_operations_observed, 1)

    def test_pi_ambiguity_reserves_the_configured_multi_operation_ceiling(self) -> None:
        boundary = _Boundary()

        def invoke(_request: bytes) -> object:
            boundary.provider_calls += 1
            raise RuntimeError("provider disposition unavailable")

        outcome = _invoke_prepared(
            _owner(
                boundary,
                invoke,
                maximum_provider_operations=4,
            )
        )

        self.assertIsInstance(outcome, ProviderStageDispatchAmbiguousV1)
        assert isinstance(outcome, ProviderStageDispatchAmbiguousV1)
        self.assertEqual(outcome.metrics.provider_operations_observed, 0)
        self.assertEqual(outcome.metrics.provider_operations_conservative, 4)

        with self.assertRaisesRegex(ContractValidationError, "Codex.*exactly one"):
            _owner(
                _Boundary(),
                lambda _request: "unused",
                stage=ProviderStage.PLANNER,
                maximum_provider_operations=2,
            )

    def test_zero_operation_or_malformed_failure_receipt_uses_durable_ambiguity(
        self,
    ) -> None:
        receipt = _codex_receipt(
            request_sha256=_sha("failure-request"),
            output_text="rejected provider output",
        )
        zero_boundary = _Boundary()

        def zero_operation_failure(_request: bytes) -> object:
            raise ProviderTransportError(
                ErrorCode.PROVIDER_TRANSPORT_FAILED,
                "receipt claims an operation absent from the ledger",
                external_provider_calls_observed=1,
                provider_call_receipt=receipt,
                retryable_failure_category=(ProviderRetryableFailureCategory.PROVIDER_UNAVAILABLE),
            )

        zero_outcome = _invoke_prepared(_owner(zero_boundary, zero_operation_failure))
        self.assertIsInstance(zero_outcome, ProviderStageDispatchAmbiguousV1)
        assert isinstance(zero_outcome, ProviderStageDispatchAmbiguousV1)
        self.assertEqual(zero_outcome.metrics.provider_operations_observed, 0)
        self.assertEqual(zero_outcome.metrics.provider_operations_conservative, 1)
        self.assertEqual(
            zero_outcome.metrics.ledger_prefix_after_sha256,
            zero_boundary.snapshot.prefix_sha256,
        )

        malformed_boundary = _Boundary()

        def malformed_failure(_request: bytes) -> object:
            malformed_boundary.advance(1)
            failure = ProviderTransportError(
                ErrorCode.PROVIDER_TRANSPORT_FAILED,
                "malformed receipt type",
                external_provider_calls_observed=1,
                retryable_failure_category=(ProviderRetryableFailureCategory.PROVIDER_UNAVAILABLE),
            )
            failure.__dict__["provider_call_receipt"] = object()
            raise failure

        malformed_outcome = _invoke_prepared(_owner(malformed_boundary, malformed_failure))
        self.assertIsInstance(malformed_outcome, ProviderStageNonRetryableFailureV1)
        assert isinstance(malformed_outcome, ProviderStageNonRetryableFailureV1)
        self.assertIs(
            malformed_outcome.failure_class,
            ProviderStageFailureClass.PROVIDER_FAILURE_NOT_RETRYABLE,
        )
        self.assertEqual(malformed_outcome.metrics.provider_operations_observed, 1)
        self.assertEqual(
            malformed_outcome.metrics.ledger_prefix_after_sha256,
            malformed_boundary.snapshot.prefix_sha256,
        )

    def test_semantic_rejection_is_successful_provider_result(self) -> None:
        boundary = _Boundary()

        def invoke(_request: bytes) -> str:
            boundary.provider_calls += 1
            boundary.advance(1)
            return "semantic rejection payload"

        outcome = _invoke_prepared(
            _owner(
                boundary,
                invoke,
                semantic_disposition=lambda _result: ProviderStageSemanticDisposition.REJECTED,
            )
        )

        self.assertIsInstance(outcome, ProviderStageSuccessfulResultV1)
        assert isinstance(outcome, ProviderStageSuccessfulResultV1)
        self.assertIs(
            outcome.semantic_disposition,
            ProviderStageSemanticDisposition.REJECTED,
        )
        self.assertEqual(outcome.exact_result, b"semantic rejection payload")

    def test_deterministic_post_result_processing_failures_are_known_non_retry(self) -> None:
        def fail_receipt(_value: str) -> ProviderStageReceiptMetricsV1:
            raise RuntimeError("deterministic local processing failure")

        def fail_serialize(_value: str) -> bytes:
            raise RuntimeError("deterministic local processing failure")

        def fail_semantic(_value: str) -> ProviderStageSemanticDisposition:
            raise RuntimeError("deterministic local processing failure")

        for failing_boundary in ("receipt", "serialize", "semantic"):
            with self.subTest(failing_boundary=failing_boundary):
                boundary = _Boundary()

                def invoke(
                    _request: bytes,
                    *,
                    boundary: _Boundary = boundary,
                ) -> str:
                    boundary.advance(1)
                    return "provider result already received"

                owner = _owner(
                    boundary,
                    invoke,
                    result_receipt_metrics=(
                        fail_receipt if failing_boundary == "receipt" else None
                    ),
                    serialize_result=(fail_serialize if failing_boundary == "serialize" else None),
                    semantic_disposition=(
                        fail_semantic if failing_boundary == "semantic" else None
                    ),
                )
                outcome = _invoke_prepared(owner)

                self.assertIsInstance(outcome, ProviderStageNonRetryableFailureV1)
                assert isinstance(outcome, ProviderStageNonRetryableFailureV1)
                self.assertIs(
                    outcome.failure_class,
                    ProviderStageFailureClass.PROVIDER_FAILURE_NOT_RETRYABLE,
                )
                self.assertEqual(outcome.metrics.provider_operations_observed, 1)
                self.assertEqual(outcome.metrics.provider_operations_conservative, 1)

    def test_post_dispatch_session_substitution_requires_recovery(self) -> None:
        boundary = _Boundary()

        def invoke(_request: bytes) -> str:
            boundary.provider_calls += 1
            boundary.advance(1)
            boundary.session = _sha("unexpected-session")
            return "result from wrong owner"

        outcome = _invoke_prepared(_owner(boundary, invoke))

        self.assertIsInstance(outcome, ProviderStageNonRetryableFailureV1)
        assert isinstance(outcome, ProviderStageNonRetryableFailureV1)
        self.assertIs(
            outcome.failure_class,
            ProviderStageFailureClass.CUSTODY_FAILED,
        )
        self.assertEqual(outcome.metrics.provider_operations_observed, 1)
        self.assertEqual(boundary.provider_calls, 1)

    def test_pi_receipt_preserves_multi_operation_and_token_totals(self) -> None:
        boundary = _Boundary()
        receipt = PiWriterReceiptV1(
            schema_version=PiWriterReceiptV1.SCHEMA_VERSION,
            route=SceneRoute.ORDINARY,
            provider="deepseek",
            model="deepseek-v4-flash",
            pi_version="0.50.2",
            session_id_sha256=_sha("pi-session"),
            parent_session_id_sha256=None,
            request_sha256=_sha("pi-request"),
            output_sha256=text_sha256("visible result"),
            provider_operations=3,
            tool_call_count=1,
            failed_tool_call_count=0,
            input_tokens=900,
            cached_input_tokens=600,
            output_tokens=120,
            reasoning_tokens=40,
            duration_ms=2222,
            finish_status="stop",
            rehydrated=True,
        )
        result = PiSceneInvocationResultV1(
            output_text="visible result",
            session_id="private-session-id",
            session_dir=Path("C:/cera-test/pi-session"),
            writer_receipt=receipt,
            raw_event_count=9,
        )

        def invoke(_request: bytes) -> PiSceneInvocationResultV1:
            boundary.provider_calls += 1
            boundary.advance(3)
            return result

        owner = _owner(
            boundary,
            invoke,
            serialize_result=lambda value: value.output_text.encode("utf-8"),
            result_receipt_metrics=pi_provider_result_receipt_metrics,
            maximum_provider_operations=3,
        )
        outcome = _invoke_prepared(owner)

        self.assertIsInstance(outcome, ProviderStageSuccessfulResultV1)
        assert isinstance(outcome, ProviderStageSuccessfulResultV1)
        self.assertEqual(outcome.metrics.provider_operations_observed, 3)
        self.assertEqual(outcome.metrics.input_tokens, 900)
        self.assertEqual(outcome.metrics.cached_input_tokens, 600)
        self.assertEqual(outcome.metrics.output_tokens, 120)
        self.assertEqual(outcome.metrics.reasoning_tokens, 40)
        self.assertEqual(outcome.metrics.duration_ms, 2222)

    def test_operation_count_above_frozen_ceiling_requires_recovery(self) -> None:
        boundary = _Boundary()

        def invoke(_request: bytes) -> str:
            boundary.provider_calls += 1
            boundary.advance(3)
            return "provider crossed the frozen ceiling"

        outcome = _invoke_prepared(
            _owner(
                boundary,
                invoke,
                maximum_provider_operations=2,
            )
        )

        self.assertIsInstance(outcome, ProviderStageNonRetryableFailureV1)
        assert isinstance(outcome, ProviderStageNonRetryableFailureV1)
        self.assertIs(outcome.failure_class, ProviderStageFailureClass.CUSTODY_FAILED)
        self.assertEqual(outcome.metrics.provider_operations_observed, 3)
        self.assertEqual(outcome.metrics.provider_operations_conservative, 3)

        unresolved_boundary = _Boundary()

        def unresolved(_request: bytes) -> str:
            unresolved_boundary.provider_calls += 1
            unresolved_boundary.advance(3)
            raise RuntimeError("provider disposition unavailable after ceiling overrun")

        unresolved_outcome = _invoke_prepared(
            _owner(
                unresolved_boundary,
                unresolved,
                maximum_provider_operations=2,
            )
        )
        self.assertIsInstance(unresolved_outcome, ProviderStageNonRetryableFailureV1)
        assert isinstance(unresolved_outcome, ProviderStageNonRetryableFailureV1)
        self.assertIs(
            unresolved_outcome.failure_class,
            ProviderStageFailureClass.CUSTODY_FAILED,
        )
        self.assertEqual(unresolved_outcome.metrics.provider_operations_observed, 3)

    def test_codex_receipt_and_operation_telemetry_are_preserved(self) -> None:
        request_sha = _sha("codex-request")
        output = '{"ok":true}'
        usage = CodexUsageStepV1(
            sequence=1,
            observed_at_unix_us=110,
            input_tokens=100,
            cached_input_tokens=80,
            uncached_input_tokens=20,
            output_tokens=10,
            reasoning_tokens=4,
            thread_total_input_tokens=100,
            thread_total_cached_input_tokens=80,
            thread_total_output_tokens=10,
            thread_total_reasoning_tokens=4,
        )
        telemetry = CodexOperationTelemetryV1(
            schema_version=CodexOperationTelemetryV1.SCHEMA_VERSION,
            request_sha256=request_sha,
            provider_operation_id_sha256=_sha("codex-operation"),
            provider_thread_id_sha256=_sha("codex-thread"),
            provider_root_thread_id_sha256=None,
            accepted_parent_checkpoint_id_sha256=None,
            candidate_checkpoint_id_sha256=None,
            model="gpt-5.6-sol",
            reasoning_effort="medium",
            fast_mode_enabled=False,
            packet_ready_unix_us=None,
            request_start_unix_us=100,
            first_reasoning_item_unix_us=101,
            first_structured_output_item_unix_us=105,
            final_evidence_result_unix_us=108,
            provider_completion_unix_us=112,
            python_parse_start_unix_us=None,
            python_parse_completion_unix_us=None,
            python_validation_start_unix_us=None,
            python_validation_completion_unix_us=None,
            usage_steps=(usage,),
            cumulative_input_tokens=100,
            cumulative_cached_input_tokens=80,
            cumulative_uncached_input_tokens=20,
            cumulative_output_tokens=10,
            cumulative_reasoning_tokens=4,
            tool_timings=(),
            tool_call_count=0,
            provider_attempt_count=1,
            finish_status="completed",
            transport_error=None,
            unsupported_fields=(),
            retains_prompt=False,
            retains_output=False,
            retains_reasoning=False,
            retains_tool_arguments=False,
        )
        receipt = _codex_receipt(request_sha256=request_sha, output_text=output)
        result = ProviderCallResult(
            output_text=output,
            parsed_json={"ok": True},
            receipt=receipt,
            operation_telemetry=telemetry,
        )

        metrics = codex_provider_result_receipt_metrics(result)

        self.assertEqual(metrics.provider_operations, 1)
        self.assertEqual(metrics.input_tokens, 100)
        self.assertEqual(metrics.cached_input_tokens, 80)
        self.assertEqual(metrics.output_tokens, 10)
        self.assertEqual(metrics.reasoning_tokens, 4)
        self.assertTrue(metrics.receipt_evidence_sha256)

        boundary = _Boundary()

        def invoke(_request: bytes) -> ProviderCallResult:
            boundary.provider_calls += 1
            boundary.advance(1)
            return result

        owner = _owner(
            boundary,
            invoke,
            stage=ProviderStage.PLANNER,
            serialize_result=lambda value: value.output_text.encode("utf-8"),
            result_receipt_metrics=codex_provider_result_receipt_metrics,
        )
        outcome = _invoke_prepared(owner)
        self.assertIsInstance(outcome, ProviderStageSuccessfulResultV1)
        assert isinstance(outcome, ProviderStageSuccessfulResultV1)
        self.assertEqual(outcome.metrics.input_tokens, 100)
        self.assertEqual(outcome.metrics.reasoning_tokens, 4)
        self.assertEqual(boundary.provider_calls, 1)

    def test_pretransport_typed_start_failure_preserves_zero_operations(self) -> None:
        boundary = _Boundary()
        provider_calls = 0

        def build_request(_exact: bytes) -> bytes:
            raise ProviderTransportError(
                ErrorCode.PROVIDER_TRANSPORT_FAILED,
                "process could not start",
                external_provider_calls_observed=0,
                retryable_failure_category=(
                    ProviderRetryableFailureCategory.PROVIDER_PROCESS_FAILED
                ),
            )

        owner = _owner(
            boundary,
            lambda _request: None,
            build_request=build_request,
        )
        preparation = owner.prepare(
            chain_id=_CHAIN_ID,
            attempt_number=1,
            exact_input=_EXACT_INPUT,
        )

        self.assertIsInstance(preparation, ProviderStagePretransportFailureV1)
        assert isinstance(preparation, ProviderStagePretransportFailureV1)
        self.assertIs(
            preparation.failure_class,
            ProviderStageFailureClass.PROVIDER_PROCESS_FAILED,
        )
        self.assertEqual(boundary.snapshot.provider_operations_total, 0)
        self.assertEqual(provider_calls, 0)

        invoked_boundary = _Boundary()
        invoked_calls = 0

        def fail_at_process_start(_request: bytes) -> object:
            nonlocal invoked_calls
            invoked_calls += 1
            raise ProviderTransportError(
                ErrorCode.PROVIDER_TRANSPORT_FAILED,
                "provider process start failed before an operation",
                external_provider_calls_observed=0,
                retryable_failure_category=(
                    ProviderRetryableFailureCategory.PROVIDER_PROCESS_FAILED
                ),
            )

        invoked_outcome = _invoke_prepared(_owner(invoked_boundary, fail_at_process_start))
        self.assertIsInstance(invoked_outcome, ProviderStageClosedFailureV1)
        assert isinstance(invoked_outcome, ProviderStageClosedFailureV1)
        self.assertEqual(invoked_calls, 1)
        self.assertEqual(invoked_outcome.metrics.provider_operations_observed, 0)
        self.assertEqual(invoked_outcome.metrics.provider_operations_conservative, 0)

    def test_construction_and_preparation_are_provider_free_and_lazy(self) -> None:
        boundary = _Boundary()
        provider_calls = 0
        build_calls = 0
        session_reads = 0
        ledger_reads = 0

        def build_request(exact: bytes) -> bytes:
            nonlocal build_calls
            build_calls += 1
            return exact

        def invoke(_request: bytes) -> str:
            nonlocal provider_calls
            provider_calls += 1
            boundary.advance(1)
            return "ok"

        def read_session() -> str:
            nonlocal session_reads
            session_reads += 1
            return boundary.session

        def read_ledger() -> ProviderStageLedgerSnapshotV1:
            nonlocal ledger_reads
            ledger_reads += 1
            return boundary.snapshot

        owner = _owner(
            boundary,
            invoke,
            build_request=build_request,
            read_session_scope_sha256=read_session,
            read_ledger_snapshot=read_ledger,
        )
        self.assertEqual(build_calls, 0)
        self.assertEqual(provider_calls, 0)
        self.assertEqual(session_reads, 0)
        self.assertEqual(ledger_reads, 0)

        prepared = owner.prepare(
            chain_id=_CHAIN_ID,
            attempt_number=1,
            exact_input=_EXACT_INPUT,
        )

        self.assertEqual(build_calls, 1)
        self.assertEqual(provider_calls, 0)
        self.assertEqual(session_reads, 0)
        self.assertEqual(ledger_reads, 0)
        self.assertNotIsInstance(prepared, ProviderStagePretransportFailureV1)

    def test_unknown_local_build_failure_is_zero_operation_non_retryable(self) -> None:
        boundary = _Boundary()
        provider_calls = 0

        def build_request(_exact: bytes) -> bytes:
            raise RuntimeError("local decoder failed")

        def invoke(_request: bytes) -> object:
            nonlocal provider_calls
            provider_calls += 1
            return object()

        owner = _owner(
            boundary,
            invoke,
            build_request=build_request,
        )
        preparation = owner.prepare(
            chain_id=_CHAIN_ID,
            attempt_number=1,
            exact_input=_EXACT_INPUT,
        )

        self.assertIsInstance(
            preparation,
            ProviderStagePretransportNonRetryableFailureV1,
        )
        assert isinstance(preparation, ProviderStagePretransportNonRetryableFailureV1)
        self.assertIs(
            preparation.failure_class,
            ProviderStageFailureClass.PROVIDER_FAILURE_NOT_RETRYABLE,
        )
        self.assertEqual(preparation.ledger_prefix_after_sha256, _sha("ledger-0"))
        self.assertEqual(provider_calls, 0)

    def test_preparation_clock_failure_is_zero_operation_non_retryable(self) -> None:
        provider_calls = 0

        def failed_clock() -> int:
            raise RuntimeError("local clock unavailable")

        def invoke(_request: bytes) -> object:
            nonlocal provider_calls
            provider_calls += 1
            return object()

        preparation = _owner(
            _Boundary(),
            invoke,
            clock_ns=failed_clock,
        ).prepare(
            chain_id=_CHAIN_ID,
            attempt_number=1,
            exact_input=_EXACT_INPUT,
        )

        self.assertIsInstance(
            preparation,
            ProviderStagePretransportNonRetryableFailureV1,
        )
        assert isinstance(preparation, ProviderStagePretransportNonRetryableFailureV1)
        self.assertIs(
            preparation.failure_class,
            ProviderStageFailureClass.CONFIGURATION_FAILED,
        )
        self.assertEqual(provider_calls, 0)

    def test_executor_reserves_dispatch_before_provider_invoke_and_retires_exact_owner(
        self,
    ) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = SQLiteProviderStageRetryStore(
                SQLiteAuthorityStore(root / "authority.sqlite3"),
                TrustedLocalProtectedStageBlobStore(root / "protected"),
            )
            executor = ProviderStageRetryExecutorV1(store)
            configuration = ProviderStageConfigurationV1.create(
                stage=ProviderStage.WRITER,
                model_id="deepseek-v4-flash",
                reasoning_mode="non_thinking",
                routing={"route": "ordinary"},
                content_policy_route="ordinary",
                stage_configuration={"maximum_output_tokens": 4096},
            )
            packet = freeze_writer_stage_packet(
                sequence_plan={"beats": ["one"]},
                realization_context={"cast": ["npc-1"]},
                configuration=configuration,
            )
            scope = ProviderStageRetryOccurrenceScopeV1.create(
                world_id="world-1",
                branch_id="branch-1",
                request_id="request-1",
                generation_id="generation-1",
                stage=ProviderStage.WRITER,
                stage_ordinal=1,
                accepted_state_sha256=_sha("accepted-head"),
                exact_input=packet.exact_bytes,
                authority_binding={"branch_state_version": 4},
            )
            boundary = _Boundary()

            def invoke(_request: bytes) -> object:
                self.assertIs(
                    store.read(scope.identity.chain_id).phase,
                    ProviderStageRetryPhase.DISPATCH_STARTED,
                )
                boundary.provider_calls += 1
                boundary.advance(1)
                raise ProviderTransportError(
                    ErrorCode.PROVIDER_TRANSPORT_FAILED,
                    "unavailable",
                    external_provider_calls_observed=1,
                    retryable_failure_category=(
                        ProviderRetryableFailureCategory.PROVIDER_UNAVAILABLE
                    ),
                )

            owner = _owner(
                boundary,
                invoke,
                chain_id=scope.identity.chain_id,
                stage_input_sha256=packet.stage_input_sha256,
            )
            chain = executor.execute_initial(scope=scope, packet=packet, owner=owner)

            self.assertEqual(boundary.provider_calls, 1)
            self.assertIs(chain.phase, ProviderStageRetryPhase.OWNER_RETIRED)
            self.assertEqual(len(boundary.retirements), 1)
            retirement = boundary.retirements[0]
            self.assertEqual(retirement.chain_id, scope.identity.chain_id)
            self.assertEqual(retirement.attempt_number, 1)
            self.assertIs(retirement.stage, ProviderStage.WRITER)
            self.assertEqual(retirement.session_scope_sha256, boundary.session)
            expected_evidence = domain_sha256(
                "cera.provider_stage_owner_retirement.v1",
                {
                    "request": retirement,
                    "injected_evidence_sha256": _sha("injected-retirement-proof"),
                },
            )
            self.assertEqual(
                chain.attempts[-1].owner_retirement_evidence_sha256,
                expected_evidence,
            )
            with self.assertRaises(StateConflictError):
                owner.retire(
                    chain_id=scope.identity.chain_id,
                    attempt_number=2,
                    failure_class=ProviderStageFailureClass.PROVIDER_UNAVAILABLE,
                )

            session_read_calls = 0

            def unreadable_session() -> str:
                nonlocal session_read_calls
                session_read_calls += 1
                raise RuntimeError("session registry temporarily unreadable")

            ambiguous_scope = ProviderStageRetryOccurrenceScopeV1.create(
                world_id="world-1",
                branch_id="branch-1",
                request_id="request-session-read-failure",
                generation_id="generation-1",
                stage=ProviderStage.WRITER,
                stage_ordinal=1,
                accepted_state_sha256=_sha("accepted-head"),
                exact_input=packet.exact_bytes,
                authority_binding={"branch_state_version": 4},
            )
            ambiguous_boundary = _Boundary()
            ambiguous_owner = _owner(
                ambiguous_boundary,
                lambda _request: "must not run",
                chain_id=ambiguous_scope.identity.chain_id,
                stage_input_sha256=packet.stage_input_sha256,
                read_session_scope_sha256=unreadable_session,
            )
            ambiguous = executor.execute_initial(
                scope=ambiguous_scope,
                packet=packet,
                owner=ambiguous_owner,
            )

            self.assertEqual(session_read_calls, 1)
            self.assertIs(ambiguous.phase, ProviderStageRetryPhase.RECOVERY_REQUIRED)
            self.assertEqual(
                ambiguous.attempts[-1].provider_operations_observed,
                0,
            )
            self.assertEqual(
                ambiguous.attempts[-1].provider_operations_conservative,
                0,
            )

            build_failure_scope = ProviderStageRetryOccurrenceScopeV1.create(
                world_id="world-1",
                branch_id="branch-1",
                request_id="request-build-failure",
                generation_id="generation-1",
                stage=ProviderStage.WRITER,
                stage_ordinal=1,
                accepted_state_sha256=_sha("accepted-head"),
                exact_input=packet.exact_bytes,
                authority_binding={"branch_state_version": 4},
            )
            build_failure_boundary = _Boundary()
            provider_calls_after_build_failure = 0

            def fail_build(_exact: bytes) -> bytes:
                raise RuntimeError("local packet construction failed")

            def must_not_invoke(_request: bytes) -> object:
                nonlocal provider_calls_after_build_failure
                provider_calls_after_build_failure += 1
                return object()

            build_failure_owner = _owner(
                build_failure_boundary,
                must_not_invoke,
                chain_id=build_failure_scope.identity.chain_id,
                stage_input_sha256=packet.stage_input_sha256,
                build_request=fail_build,
            )
            recovery_required = executor.execute_initial(
                scope=build_failure_scope,
                packet=packet,
                owner=build_failure_owner,
            )

            self.assertIs(
                recovery_required.phase,
                ProviderStageRetryPhase.RECOVERY_REQUIRED,
            )
            self.assertEqual(provider_calls_after_build_failure, 0)
            self.assertIsNone(recovery_required.attempts[-1].dispatch_evidence_sha256)
            self.assertEqual(
                recovery_required.attempts[-1].provider_operations_observed,
                0,
            )


if __name__ == "__main__":
    unittest.main()
