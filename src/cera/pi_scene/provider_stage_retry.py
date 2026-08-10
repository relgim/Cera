"""Provider-neutral contracts for bounded stage-local provider retries.

The contracts in this module are deliberately independent from Pi Scene's
manual Planner transport-Retry protocol.  They describe one logical provider
stage, its immutable protected input identity, at most three fresh attempts,
and privacy-safe terminal projections.  Exact input and result bytes belong to
the protected store; none of the DTOs below contains provider prose or paths.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, ClassVar

from cera.errors import ContractValidationError
from cera.schema import from_mapping
from cera.serialization import canonical_sha256, domain_sha256, re_is_sha256, to_primitive

MAXIMUM_PROVIDER_STAGE_ATTEMPTS = 3
MAXIMUM_SAFE_INTEGER = 9_007_199_254_740_991


class ProviderFamily(StrEnum):
    CODEX = "codex"
    DEEPSEEK = "deepseek"


class ProviderModelFamily(StrEnum):
    SOL = "sol"
    LUNA = "luna"
    DEEPSEEK_V4 = "deepseek_v4"


class ProviderStage(StrEnum):
    PLANNER = "planner"
    SEMANTIC_VALIDATOR = "semantic_validator"
    WRITER = "writer"
    RECORDER = "recorder"
    ADULT_SCENE = "adult_scene"
    ADULT_FILTER = "adult_filter"


class ProviderStageFailureClass(StrEnum):
    TRANSPORT_TIMEOUT = "transport_timeout"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    PROVIDER_PROCESS_FAILED = "provider_process_failed"
    PROVIDER_STREAM_INCOMPLETE = "provider_stream_incomplete"
    PROVIDER_COMPLETION_INCOMPLETE = "provider_completion_incomplete"
    PROVIDER_OUTPUT_INVALID = "provider_output_invalid"
    DISPATCH_AMBIGUOUS = "dispatch_ambiguous"
    AUTHENTICATION_FAILED = "authentication_failed"
    INVALID_REQUEST = "invalid_request"
    CONTEXT_SIZE_EXCEEDED = "context_size_exceeded"
    UNSUPPORTED_PARAMETER = "unsupported_parameter"
    OUTPUT_LIMIT_TRUNCATED = "output_limit_truncated"
    CUSTODY_FAILED = "custody_failed"
    CONFIGURATION_FAILED = "configuration_failed"
    BUDGET_EXHAUSTED = "budget_exhausted"
    PROVIDER_FAILURE_NOT_RETRYABLE = "provider_failure_not_retryable"


RETRYABLE_PROVIDER_STAGE_FAILURES = frozenset(
    {
        ProviderStageFailureClass.TRANSPORT_TIMEOUT,
        ProviderStageFailureClass.PROVIDER_UNAVAILABLE,
        ProviderStageFailureClass.PROVIDER_PROCESS_FAILED,
        ProviderStageFailureClass.PROVIDER_STREAM_INCOMPLETE,
        ProviderStageFailureClass.PROVIDER_COMPLETION_INCOMPLETE,
        ProviderStageFailureClass.PROVIDER_OUTPUT_INVALID,
    }
)

PRETRANSPORT_PROVIDER_STAGE_FAILURES = frozenset(
    {
        ProviderStageFailureClass.TRANSPORT_TIMEOUT,
        ProviderStageFailureClass.PROVIDER_UNAVAILABLE,
        ProviderStageFailureClass.PROVIDER_PROCESS_FAILED,
    }
)

NON_RETRYABLE_PROVIDER_STAGE_FAILURES = frozenset(
    {
        ProviderStageFailureClass.AUTHENTICATION_FAILED,
        ProviderStageFailureClass.INVALID_REQUEST,
        ProviderStageFailureClass.CONTEXT_SIZE_EXCEEDED,
        ProviderStageFailureClass.UNSUPPORTED_PARAMETER,
        ProviderStageFailureClass.OUTPUT_LIMIT_TRUNCATED,
        ProviderStageFailureClass.CUSTODY_FAILED,
        ProviderStageFailureClass.CONFIGURATION_FAILED,
        ProviderStageFailureClass.BUDGET_EXHAUSTED,
        ProviderStageFailureClass.PROVIDER_FAILURE_NOT_RETRYABLE,
    }
)


class ProviderStageBlockReason(StrEnum):
    INPUT_CHANGED = "input_changed"
    AUTHORITY_CHANGED = "authority_changed"
    LEDGER_PREFIX_CHANGED = "ledger_prefix_changed"
    OWNER_RETIREMENT_UNPROVEN = "owner_retirement_unproven"
    RESULT_CHECKPOINT_CONFLICT = "result_checkpoint_conflict"
    DISPATCH_CUSTODY_AMBIGUOUS = "dispatch_custody_ambiguous"


class ProviderStageRetryPhase(StrEnum):
    INPUT_FROZEN = "input_frozen"
    ATTEMPT_PREPARED = "attempt_prepared"
    DISPATCH_STARTED = "dispatch_started"
    AWAITING_OWNER_RETIREMENT = "awaiting_owner_retirement"
    OWNER_RETIRED = "owner_retired"
    RESULT_FROZEN = "result_frozen"
    DOWNSTREAM_INTENT_FROZEN = "downstream_intent_frozen"
    DOWNSTREAM_BOUND = "downstream_bound"
    SUCCEEDED = "succeeded"
    EXHAUSTED = "exhausted"
    BLOCKED_AMBIGUOUS = "blocked_ambiguous"
    RECORDING_REPAIR_REQUIRED = "recording_repair_required"
    RECOVERY_REQUIRED = "recovery_required"


class ProviderStageAttemptPhase(StrEnum):
    PREPARED = "prepared"
    DISPATCH_STARTED = "dispatch_started"
    FAILED = "failed"
    OWNER_RETIRED = "owner_retired"
    RESULT_FROZEN = "result_frozen"


class ProviderStageCheckpointKind(StrEnum):
    INPUT = "input"
    RESULT = "result"


class ProviderStageRecoveryAction(StrEnum):
    PREPARE_ATTEMPT = "prepare_attempt"
    AWAIT_MANUAL_RETRY = "await_manual_retry"
    DISPATCH_PREPARED_ATTEMPT = "dispatch_prepared_attempt"
    RESOLVE_AMBIGUOUS_DISPATCH = "resolve_ambiguous_dispatch"
    RETIRE_FAILED_OWNER = "retire_failed_owner"
    FREEZE_DOWNSTREAM_INTENT = "freeze_downstream_intent"
    RECONCILE_DOWNSTREAM = "reconcile_downstream"
    FINALIZE_SUCCESS = "finalize_success"
    REPLAY_SUCCESS = "replay_success"
    REPORT_EXHAUSTED = "report_exhausted"
    REPORT_BLOCKED_AMBIGUOUS = "report_blocked_ambiguous"
    REPORT_RECORDING_REPAIR_REQUIRED = "report_recording_repair_required"
    REPORT_RECOVERY_REQUIRED = "report_recovery_required"


class ProviderStageTerminalDisposition(StrEnum):
    EXHAUSTED = "exhausted"
    BLOCKED_AMBIGUOUS = "blocked_ambiguous"
    RECOVERY_REQUIRED = "recovery_required"


_STAGE_OWNER = {
    ProviderStage.PLANNER: (ProviderFamily.CODEX, ProviderModelFamily.SOL),
    ProviderStage.SEMANTIC_VALIDATOR: (ProviderFamily.CODEX, ProviderModelFamily.LUNA),
    ProviderStage.WRITER: (ProviderFamily.DEEPSEEK, ProviderModelFamily.DEEPSEEK_V4),
    ProviderStage.RECORDER: (ProviderFamily.DEEPSEEK, ProviderModelFamily.DEEPSEEK_V4),
    ProviderStage.ADULT_SCENE: (ProviderFamily.DEEPSEEK, ProviderModelFamily.DEEPSEEK_V4),
    ProviderStage.ADULT_FILTER: (ProviderFamily.DEEPSEEK, ProviderModelFamily.DEEPSEEK_V4),
}


def _require_sha256(value: str, field_name: str) -> None:
    if not re_is_sha256(value):
        raise ContractValidationError(f"{field_name} must be SHA-256")


def _require_nonnegative_int(value: int, field_name: str) -> None:
    if type(value) is not int or not 0 <= value <= MAXIMUM_SAFE_INTEGER:
        raise ContractValidationError(f"{field_name} must be a non-negative safe integer")


def _payload(value: object) -> dict[str, Any]:
    primitive = to_primitive(value)
    if not isinstance(primitive, dict):
        raise ContractValidationError("provider-stage contract did not encode as an object")
    return primitive


@dataclass(frozen=True, slots=True)
class ProviderStageRetryIdentityV1:
    """Hash-only identity for one unique durable provider-stage occurrence.

    ``request_occurrence_sha256`` must bind world, branch, request identity,
    and a generation or operation ordinal.  Repeatable request content alone
    is not a safe occurrence identity.
    """

    SCHEMA_VERSION: ClassVar[str] = "cera.provider_stage_retry_identity.v1"

    schema_version: str
    provider: ProviderFamily
    model_family: ProviderModelFamily
    stage: ProviderStage
    request_occurrence_sha256: str
    request_sha256: str
    stage_input_sha256: str
    authority_sha256: str
    story_state_committed: bool

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("provider-stage retry identity schema changed")
        if (
            type(self.provider) is not ProviderFamily
            or type(self.model_family) is not ProviderModelFamily
            or type(self.stage) is not ProviderStage
        ):
            raise ContractValidationError("provider-stage owner enums are not closed")
        expected_provider, expected_model = _STAGE_OWNER[self.stage]
        if (self.provider, self.model_family) != (expected_provider, expected_model):
            raise ContractValidationError("provider-stage owner mapping changed")
        for field_name in (
            "request_occurrence_sha256",
            "request_sha256",
            "stage_input_sha256",
            "authority_sha256",
        ):
            _require_sha256(getattr(self, field_name), f"provider_stage_retry.{field_name}")
        expected_committed = self.stage is ProviderStage.RECORDER
        if self.story_state_committed is not expected_committed:
            raise ContractValidationError("provider-stage story-commit boundary changed")

    @property
    def logical_key_sha256(self) -> str:
        return domain_sha256(
            "cera.provider_stage_retry_logical_key.v1",
            {
                "stage": self.stage.value,
                "request_occurrence_sha256": self.request_occurrence_sha256,
            },
        )

    @property
    def chain_id(self) -> str:
        return "stage-retry-" + self.logical_key_sha256

    def to_payload(self) -> dict[str, Any]:
        return _payload(self)


@dataclass(frozen=True, slots=True)
class ProviderStageProtectedCheckpointV1:
    """Hash-and-size binding for exact bytes retained only by the protected store."""

    SCHEMA_VERSION: ClassVar[str] = "cera.provider_stage_protected_checkpoint.v1"

    schema_version: str
    checkpoint_kind: ProviderStageCheckpointKind
    chain_id: str
    attempt_number: int | None
    content_sha256: str
    size_bytes: int
    evidence_sha256: str
    checkpoint_sha256: str

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("provider-stage checkpoint schema changed")
        if type(self.checkpoint_kind) is not ProviderStageCheckpointKind:
            raise ContractValidationError("provider-stage checkpoint kind is not closed")
        _require_chain_id(self.chain_id)
        if self.checkpoint_kind is ProviderStageCheckpointKind.INPUT:
            if self.attempt_number is not None:
                raise ContractValidationError("provider-stage input checkpoint has an attempt")
        elif (
            type(self.attempt_number) is not int
            or self.attempt_number < 1
            or self.attempt_number > MAXIMUM_PROVIDER_STAGE_ATTEMPTS
        ):
            raise ContractValidationError("provider-stage result checkpoint attempt is invalid")
        _require_sha256(self.content_sha256, "provider-stage checkpoint content")
        _require_sha256(self.evidence_sha256, "provider-stage checkpoint evidence")
        if type(self.size_bytes) is not int or not 1 <= self.size_bytes <= MAXIMUM_SAFE_INTEGER:
            raise ContractValidationError("provider-stage checkpoint size is invalid")
        body = self.to_payload()
        body.pop("checkpoint_sha256")
        if self.checkpoint_sha256 != canonical_sha256(body):
            raise ContractValidationError("provider-stage checkpoint hash changed")

    @classmethod
    def create(
        cls,
        *,
        checkpoint_kind: ProviderStageCheckpointKind,
        chain_id: str,
        attempt_number: int | None,
        content_sha256: str,
        size_bytes: int,
        evidence_sha256: str,
    ) -> ProviderStageProtectedCheckpointV1:
        if type(checkpoint_kind) is not ProviderStageCheckpointKind:
            raise ContractValidationError("provider-stage checkpoint kind is not closed")
        body = {
            "schema_version": cls.SCHEMA_VERSION,
            "checkpoint_kind": checkpoint_kind.value,
            "chain_id": chain_id,
            "attempt_number": attempt_number,
            "content_sha256": content_sha256,
            "size_bytes": size_bytes,
            "evidence_sha256": evidence_sha256,
        }
        return cls(
            schema_version=cls.SCHEMA_VERSION,
            checkpoint_kind=checkpoint_kind,
            chain_id=chain_id,
            attempt_number=attempt_number,
            content_sha256=content_sha256,
            size_bytes=size_bytes,
            evidence_sha256=evidence_sha256,
            checkpoint_sha256=canonical_sha256(body),
        )

    def to_payload(self) -> dict[str, Any]:
        return _payload(self)


@dataclass(frozen=True, slots=True)
class ProviderStageAttemptV1:
    """Privacy-safe durable state for one fresh stage attempt."""

    SCHEMA_VERSION: ClassVar[str] = "cera.provider_stage_attempt.v1"

    schema_version: str
    attempt_number: int
    phase: ProviderStageAttemptPhase
    session_scope_sha256: str
    ledger_prefix_before_sha256: str
    dispatch_evidence_sha256: str | None
    ledger_prefix_after_sha256: str | None
    provider_operations_observed: int
    provider_operations_conservative: int
    duration_ms: int | None
    input_tokens: int | None
    cached_input_tokens: int | None
    output_tokens: int | None
    reasoning_tokens: int | None
    failure_class: ProviderStageFailureClass | None
    failure_evidence_sha256: str | None
    owner_retirement_evidence_sha256: str | None
    result_checkpoint_sha256: str | None

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("provider-stage attempt schema changed")
        if type(self.phase) is not ProviderStageAttemptPhase:
            raise ContractValidationError("provider-stage attempt phase is not closed")
        if (
            type(self.attempt_number) is not int
            or self.attempt_number < 1
            or self.attempt_number > MAXIMUM_PROVIDER_STAGE_ATTEMPTS
        ):
            raise ContractValidationError("provider-stage attempt number is invalid")
        _require_sha256(self.session_scope_sha256, "provider-stage session scope")
        _require_sha256(self.ledger_prefix_before_sha256, "provider-stage ledger prefix before")
        _require_nonnegative_int(
            self.provider_operations_observed,
            "provider-stage observed operations",
        )
        _require_nonnegative_int(
            self.provider_operations_conservative,
            "provider-stage conservative operations",
        )
        if self.provider_operations_conservative < self.provider_operations_observed:
            raise ContractValidationError("provider-stage conservative accounting undercounts")
        if (
            self.failure_class is not None
            and type(self.failure_class) is not ProviderStageFailureClass
        ):
            raise ContractValidationError("provider-stage failure class is not closed")
        for token_value, field_name in (
            (self.input_tokens, "input tokens"),
            (self.cached_input_tokens, "cached input tokens"),
            (self.output_tokens, "output tokens"),
            (self.reasoning_tokens, "reasoning tokens"),
        ):
            if token_value is not None:
                _require_nonnegative_int(token_value, f"provider-stage {field_name}")
        if self.cached_input_tokens is not None and (
            self.input_tokens is None or self.cached_input_tokens > self.input_tokens
        ):
            raise ContractValidationError("provider-stage cached input exceeds total input")
        if self.provider_operations_observed == 0 and any(
            value not in {None, 0}
            for value in (
                self.input_tokens,
                self.cached_input_tokens,
                self.output_tokens,
                self.reasoning_tokens,
            )
        ):
            raise ContractValidationError(
                "provider-stage token accounting lacks an observed provider operation"
            )
        for hash_value, field_name in (
            (self.dispatch_evidence_sha256, "dispatch evidence"),
            (self.ledger_prefix_after_sha256, "ledger prefix after"),
            (self.failure_evidence_sha256, "failure evidence"),
            (self.owner_retirement_evidence_sha256, "owner-retirement evidence"),
            (self.result_checkpoint_sha256, "result checkpoint"),
        ):
            if hash_value is not None:
                _require_sha256(hash_value, f"provider-stage {field_name}")
        if self.phase is ProviderStageAttemptPhase.PREPARED:
            self._require_pre_dispatch()
        elif self.phase is ProviderStageAttemptPhase.DISPATCH_STARTED:
            if self.dispatch_evidence_sha256 is None:
                raise ContractValidationError("provider-stage dispatch lacks evidence")
            self._require_unfinished()
        elif self.phase in {
            ProviderStageAttemptPhase.FAILED,
            ProviderStageAttemptPhase.OWNER_RETIRED,
        }:
            self._require_failed()
            if (
                self.phase is ProviderStageAttemptPhase.FAILED
                and self.owner_retirement_evidence_sha256 is not None
            ):
                raise ContractValidationError("failed provider-stage attempt is already retired")
            if (
                self.phase is ProviderStageAttemptPhase.OWNER_RETIRED
                and self.owner_retirement_evidence_sha256 is None
            ):
                raise ContractValidationError("provider-stage owner retirement lacks evidence")
        else:
            self._require_result()

    def _require_pre_dispatch(self) -> None:
        if (
            any(
                value is not None
                for value in (
                    self.dispatch_evidence_sha256,
                    self.ledger_prefix_after_sha256,
                    self.duration_ms,
                    self.input_tokens,
                    self.cached_input_tokens,
                    self.output_tokens,
                    self.reasoning_tokens,
                    self.failure_class,
                    self.failure_evidence_sha256,
                    self.owner_retirement_evidence_sha256,
                    self.result_checkpoint_sha256,
                )
            )
            or self.provider_operations_observed != 0
            or self.provider_operations_conservative != 0
        ):
            raise ContractValidationError("prepared provider-stage attempt contains terminal state")

    def _require_unfinished(self) -> None:
        if (
            any(
                value is not None
                for value in (
                    self.ledger_prefix_after_sha256,
                    self.duration_ms,
                    self.input_tokens,
                    self.cached_input_tokens,
                    self.output_tokens,
                    self.reasoning_tokens,
                    self.failure_class,
                    self.failure_evidence_sha256,
                    self.owner_retirement_evidence_sha256,
                    self.result_checkpoint_sha256,
                )
            )
            or self.provider_operations_observed != 0
            or self.provider_operations_conservative < 1
        ):
            raise ContractValidationError(
                "dispatched provider-stage attempt lacks a conservative reservation"
            )

    def _require_terminal_common(self, *, require_dispatch: bool = True) -> None:
        if require_dispatch and self.dispatch_evidence_sha256 is None:
            raise ContractValidationError("terminal provider-stage attempt lacks dispatch custody")
        if self.ledger_prefix_after_sha256 is None:
            raise ContractValidationError("terminal provider-stage attempt lacks ledger custody")
        if self.duration_ms is None:
            raise ContractValidationError("provider-stage attempt duration is unavailable")
        _require_nonnegative_int(self.duration_ms, "provider-stage attempt duration")

    def _require_failed(self) -> None:
        pretransport = self.dispatch_evidence_sha256 is None
        self._require_terminal_common(require_dispatch=not pretransport)
        if (
            self.failure_class is None
            or self.failure_evidence_sha256 is None
            or self.result_checkpoint_sha256 is not None
        ):
            raise ContractValidationError("failed provider-stage attempt has invalid disposition")
        if pretransport and (
            self.failure_class
            not in PRETRANSPORT_PROVIDER_STAGE_FAILURES | NON_RETRYABLE_PROVIDER_STAGE_FAILURES
            or self.provider_operations_observed != 0
            or self.provider_operations_conservative != 0
            or any(
                value not in {None, 0}
                for value in (
                    self.input_tokens,
                    self.cached_input_tokens,
                    self.output_tokens,
                    self.reasoning_tokens,
                )
            )
        ):
            raise ContractValidationError(
                "pretransport provider-stage failure contains provider effects"
            )
        if (
            self.failure_class is ProviderStageFailureClass.DISPATCH_AMBIGUOUS
            and self.provider_operations_conservative < 1
        ):
            raise ContractValidationError(
                "ambiguous provider-stage dispatch lacks conservative accounting"
            )
        if (
            self.failure_class is not ProviderStageFailureClass.DISPATCH_AMBIGUOUS
            and self.provider_operations_conservative != self.provider_operations_observed
        ):
            raise ContractValidationError(
                "closed provider-stage failure has unresolved operation accounting"
            )

    def _require_result(self) -> None:
        self._require_terminal_common()
        if (
            self.failure_class is not None
            or self.failure_evidence_sha256 is not None
            or self.owner_retirement_evidence_sha256 is not None
            or self.result_checkpoint_sha256 is None
        ):
            raise ContractValidationError(
                "successful provider-stage attempt has invalid disposition"
            )
        if self.provider_operations_observed < 1:
            raise ContractValidationError(
                "successful provider-stage dispatch lacks observed accounting"
            )
        if self.provider_operations_conservative != self.provider_operations_observed:
            raise ContractValidationError(
                "successful provider-stage result has unresolved operation accounting"
            )

    def to_payload(self) -> dict[str, Any]:
        return _payload(self)


@dataclass(frozen=True, slots=True)
class ProviderStageRetryChainV1:
    """Safe state-machine projection; exact protected bytes are referenced by hash."""

    SCHEMA_VERSION: ClassVar[str] = "cera.provider_stage_retry_chain.v1"

    schema_version: str
    identity: ProviderStageRetryIdentityV1
    phase: ProviderStageRetryPhase
    attempts: tuple[ProviderStageAttemptV1, ...]
    result_checkpoint: ProviderStageProtectedCheckpointV1 | None
    downstream_intent_sha256: str | None
    downstream_evidence_sha256: str | None
    block_reason: ProviderStageBlockReason | None
    block_evidence_sha256: str | None

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("provider-stage retry chain schema changed")
        if (
            type(self.identity) is not ProviderStageRetryIdentityV1
            or type(self.phase) is not ProviderStageRetryPhase
            or type(self.attempts) is not tuple
            or any(type(value) is not ProviderStageAttemptV1 for value in self.attempts)
            or (
                self.result_checkpoint is not None
                and type(self.result_checkpoint) is not ProviderStageProtectedCheckpointV1
            )
        ):
            raise ContractValidationError("provider-stage retry-chain enums or contracts changed")
        if len(self.attempts) > MAXIMUM_PROVIDER_STAGE_ATTEMPTS:
            raise ContractValidationError("provider-stage retry chain exceeded its attempt ceiling")
        numbers = tuple(value.attempt_number for value in self.attempts)
        if numbers != tuple(range(1, len(self.attempts) + 1)):
            raise ContractValidationError("provider-stage attempts are not contiguous")
        _require_nonnegative_int(
            sum(value.provider_operations_observed for value in self.attempts),
            "provider-stage cumulative observed operations",
        )
        _require_nonnegative_int(
            sum(value.provider_operations_conservative for value in self.attempts),
            "provider-stage cumulative conservative operations",
        )
        sessions = tuple(value.session_scope_sha256 for value in self.attempts)
        if len(set(sessions)) != len(sessions):
            raise ContractValidationError("provider-stage retry reused a session scope")
        for previous, current in zip(self.attempts, self.attempts[1:], strict=False):
            if previous.phase is not ProviderStageAttemptPhase.OWNER_RETIRED:
                raise ContractValidationError(
                    "provider-stage retry advanced before owner retirement"
                )
            if current.ledger_prefix_before_sha256 != previous.ledger_prefix_after_sha256:
                raise ContractValidationError("provider-stage retry lost its ledger prefix")
        if self.result_checkpoint is not None:
            if (
                self.result_checkpoint.checkpoint_kind is not ProviderStageCheckpointKind.RESULT
                or self.result_checkpoint.chain_id != self.chain_id
                or not self.attempts
                or self.result_checkpoint.attempt_number != self.attempts[-1].attempt_number
                or self.result_checkpoint.checkpoint_sha256
                != self.attempts[-1].result_checkpoint_sha256
            ):
                raise ContractValidationError("provider-stage result checkpoint lost chain custody")
        if self.downstream_intent_sha256 is not None:
            _require_sha256(self.downstream_intent_sha256, "provider-stage downstream intent")
        if self.downstream_evidence_sha256 is not None:
            _require_sha256(self.downstream_evidence_sha256, "provider-stage downstream evidence")
        if self.block_evidence_sha256 is not None:
            _require_sha256(self.block_evidence_sha256, "provider-stage block evidence")
        if self.phase is ProviderStageRetryPhase.BLOCKED_AMBIGUOUS:
            if (
                self.block_reason is not ProviderStageBlockReason.DISPATCH_CUSTODY_AMBIGUOUS
                or self.block_evidence_sha256 is None
            ):
                raise ContractValidationError(
                    "ambiguous provider-stage chain lacks dispatch-custody evidence"
                )
        elif self.phase is ProviderStageRetryPhase.RECOVERY_REQUIRED and (
            self.block_reason is not None or self.block_evidence_sha256 is not None
        ):
            if (
                type(self.block_reason) is not ProviderStageBlockReason
                or self.block_reason is ProviderStageBlockReason.DISPATCH_CUSTODY_AMBIGUOUS
                or self.block_evidence_sha256 is None
            ):
                raise ContractValidationError(
                    "provider-stage recovery block lacks closed non-dispatch evidence"
                )
        elif self.block_reason is not None or self.block_evidence_sha256 is not None:
            raise ContractValidationError(
                "active provider-stage chain contains a block disposition"
            )
        self._require_phase_shape()

    @property
    def chain_id(self) -> str:
        return self.identity.chain_id

    @property
    def attempts_total(self) -> int:
        return len(self.attempts)

    @property
    def retries_consumed(self) -> int:
        return max(0, len(self.attempts) - 1)

    @property
    def attempts_remaining(self) -> int:
        return MAXIMUM_PROVIDER_STAGE_ATTEMPTS - len(self.attempts)

    @property
    def provider_operations_observed_total(self) -> int:
        return sum(value.provider_operations_observed for value in self.attempts)

    @property
    def provider_operations_conservative_total(self) -> int:
        return sum(value.provider_operations_conservative for value in self.attempts)

    @property
    def attempt_chain_sha256(self) -> str:
        return domain_sha256(
            "cera.provider_stage_attempt_chain.v1",
            tuple(value.to_payload() for value in self.attempts),
        )

    @property
    def chain_sha256(self) -> str:
        return canonical_sha256(self.to_payload())

    def _require_phase_shape(self) -> None:
        if self.phase is ProviderStageRetryPhase.BLOCKED_AMBIGUOUS or (
            self.phase is ProviderStageRetryPhase.RECOVERY_REQUIRED
            and self.block_reason is not None
        ):
            if (
                self.downstream_intent_sha256 is not None
                or self.downstream_evidence_sha256 is not None
            ):
                raise ContractValidationError(
                    "blocked provider-stage chain crossed a downstream effect boundary"
                )
            return
        if not self.attempts:
            if (
                self.phase is not ProviderStageRetryPhase.INPUT_FROZEN
                or self.result_checkpoint is not None
                or self.downstream_intent_sha256 is not None
                or self.downstream_evidence_sha256 is not None
            ):
                raise ContractValidationError("empty provider-stage chain has invalid phase")
            return
        last = self.attempts[-1]
        pre_result = self.phase in {
            ProviderStageRetryPhase.ATTEMPT_PREPARED,
            ProviderStageRetryPhase.DISPATCH_STARTED,
            ProviderStageRetryPhase.AWAITING_OWNER_RETIREMENT,
            ProviderStageRetryPhase.OWNER_RETIRED,
            ProviderStageRetryPhase.EXHAUSTED,
            ProviderStageRetryPhase.RECORDING_REPAIR_REQUIRED,
            ProviderStageRetryPhase.RECOVERY_REQUIRED,
        }
        if pre_result and (
            self.result_checkpoint is not None
            or self.downstream_intent_sha256 is not None
            or self.downstream_evidence_sha256 is not None
        ):
            raise ContractValidationError(
                "provider-stage pre-result state contains downstream custody"
            )
        if self.phase is ProviderStageRetryPhase.ATTEMPT_PREPARED:
            valid = last.phase is ProviderStageAttemptPhase.PREPARED
        elif self.phase is ProviderStageRetryPhase.DISPATCH_STARTED:
            valid = last.phase is ProviderStageAttemptPhase.DISPATCH_STARTED
        elif self.phase is ProviderStageRetryPhase.AWAITING_OWNER_RETIREMENT:
            valid = last.phase is ProviderStageAttemptPhase.FAILED
        elif self.phase is ProviderStageRetryPhase.OWNER_RETIRED:
            valid = (
                last.phase is ProviderStageAttemptPhase.OWNER_RETIRED
                and len(self.attempts) < MAXIMUM_PROVIDER_STAGE_ATTEMPTS
                and last.failure_class in RETRYABLE_PROVIDER_STAGE_FAILURES
            )
        elif self.phase is ProviderStageRetryPhase.RESULT_FROZEN:
            valid = (
                last.phase is ProviderStageAttemptPhase.RESULT_FROZEN
                and self.result_checkpoint is not None
                and self.downstream_intent_sha256 is None
                and self.downstream_evidence_sha256 is None
            )
        elif self.phase is ProviderStageRetryPhase.DOWNSTREAM_INTENT_FROZEN:
            valid = (
                last.phase is ProviderStageAttemptPhase.RESULT_FROZEN
                and self.result_checkpoint is not None
                and self.downstream_intent_sha256 is not None
                and self.downstream_evidence_sha256 is None
            )
        elif self.phase in {
            ProviderStageRetryPhase.DOWNSTREAM_BOUND,
            ProviderStageRetryPhase.SUCCEEDED,
        }:
            valid = (
                last.phase is ProviderStageAttemptPhase.RESULT_FROZEN
                and self.result_checkpoint is not None
                and self.downstream_intent_sha256 is not None
                and self.downstream_evidence_sha256 is not None
            )
        elif self.phase is ProviderStageRetryPhase.EXHAUSTED:
            valid = (
                len(self.attempts) == MAXIMUM_PROVIDER_STAGE_ATTEMPTS
                and self.identity.stage is not ProviderStage.RECORDER
                and last.phase is ProviderStageAttemptPhase.OWNER_RETIRED
                and last.failure_class in RETRYABLE_PROVIDER_STAGE_FAILURES
                and self.result_checkpoint is None
                and self.downstream_intent_sha256 is None
                and self.downstream_evidence_sha256 is None
            )
        elif self.phase is ProviderStageRetryPhase.RECORDING_REPAIR_REQUIRED:
            valid = (
                self.identity.stage is ProviderStage.RECORDER
                and len(self.attempts) == MAXIMUM_PROVIDER_STAGE_ATTEMPTS
                and last.phase is ProviderStageAttemptPhase.OWNER_RETIRED
                and last.failure_class in RETRYABLE_PROVIDER_STAGE_FAILURES
                and self.result_checkpoint is None
                and self.downstream_intent_sha256 is None
                and self.downstream_evidence_sha256 is None
            )
        elif self.phase is ProviderStageRetryPhase.RECOVERY_REQUIRED:
            valid = (
                self.block_reason is None
                and last.phase is ProviderStageAttemptPhase.OWNER_RETIRED
                and last.failure_class in NON_RETRYABLE_PROVIDER_STAGE_FAILURES
                and self.result_checkpoint is None
                and self.downstream_intent_sha256 is None
                and self.downstream_evidence_sha256 is None
            )
        else:
            valid = False
        if not valid:
            raise ContractValidationError("provider-stage retry phase does not match its custody")

    def to_payload(self) -> dict[str, Any]:
        return _payload(self)


@dataclass(frozen=True, slots=True)
class ProviderStageRetryExhaustedV1:
    """Shared privacy-safe terminal DTO after three failed stage attempts."""

    SCHEMA_VERSION: ClassVar[str] = "cera.provider_stage_retry_exhausted.v1"

    schema_version: str
    severity: str
    provider: ProviderFamily
    model_family: ProviderModelFamily
    stage: ProviderStage
    maximum_attempts: int
    attempts_total: int
    retries_consumed: int
    story_state_committed: bool
    failed_stage_effect_committed: bool
    provider_operations_observed_total: int
    provider_operations_conservative_total: int
    final_failure_class: ProviderStageFailureClass
    request_sha256: str
    stage_input_sha256: str
    attempt_chain_sha256: str
    terminal_evidence_sha256: str

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION or self.severity != "critical":
            raise ContractValidationError("provider-stage exhausted terminal schema changed")
        if (
            type(self.provider) is not ProviderFamily
            or type(self.model_family) is not ProviderModelFamily
            or type(self.stage) is not ProviderStage
            or type(self.final_failure_class) is not ProviderStageFailureClass
        ):
            raise ContractValidationError("provider-stage exhausted enums are not closed")
        expected_provider, expected_model = _STAGE_OWNER[self.stage]
        if (self.provider, self.model_family) != (expected_provider, expected_model):
            raise ContractValidationError("provider-stage exhausted owner changed")
        if (
            self.stage is ProviderStage.RECORDER
            or self.final_failure_class not in RETRYABLE_PROVIDER_STAGE_FAILURES
        ):
            raise ContractValidationError(
                "Recorder repair, ambiguity, or non-Retry failure cannot be exhaustion"
            )
        for count_value, field_name in (
            (self.maximum_attempts, "maximum attempts"),
            (self.attempts_total, "attempts total"),
            (self.retries_consumed, "retries consumed"),
        ):
            _require_nonnegative_int(count_value, f"provider-stage exhausted {field_name}")
        if (
            self.maximum_attempts != MAXIMUM_PROVIDER_STAGE_ATTEMPTS
            or self.attempts_total != MAXIMUM_PROVIDER_STAGE_ATTEMPTS
            or self.retries_consumed != MAXIMUM_PROVIDER_STAGE_ATTEMPTS - 1
        ):
            raise ContractValidationError("provider-stage exhausted attempt accounting changed")
        if self.story_state_committed is not False:
            raise ContractValidationError("provider-stage exhausted story boundary changed")
        if self.failed_stage_effect_committed is not False:
            raise ContractValidationError("failed provider stage cannot report a committed effect")
        _require_nonnegative_int(
            self.provider_operations_observed_total,
            "provider-stage exhausted observed operations",
        )
        _require_nonnegative_int(
            self.provider_operations_conservative_total,
            "provider-stage exhausted conservative operations",
        )
        if self.provider_operations_conservative_total < self.provider_operations_observed_total:
            raise ContractValidationError("provider-stage exhausted accounting undercounts")
        for hash_value, field_name in (
            (self.request_sha256, "request"),
            (self.stage_input_sha256, "stage input"),
            (self.attempt_chain_sha256, "attempt chain"),
            (self.terminal_evidence_sha256, "terminal evidence"),
        ):
            _require_sha256(hash_value, f"provider-stage exhausted {field_name}")

    def to_payload(self) -> dict[str, Any]:
        return _payload(self)


@dataclass(frozen=True, slots=True)
class ProviderStageRetryBlockedV1:
    """Safe terminal DTO when uncertain custody forbids another provider attempt."""

    SCHEMA_VERSION: ClassVar[str] = "cera.provider_stage_retry_blocked_ambiguous.v1"

    schema_version: str
    severity: str
    provider: ProviderFamily
    model_family: ProviderModelFamily
    stage: ProviderStage
    maximum_attempts: int
    attempts_total: int
    retries_consumed: int
    story_state_committed: bool
    failed_stage_effect_committed: bool
    provider_operations_observed_total: int
    provider_operations_conservative_total: int
    block_reason: ProviderStageBlockReason
    request_sha256: str
    stage_input_sha256: str
    attempt_chain_sha256: str
    terminal_evidence_sha256: str

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION or self.severity != "critical":
            raise ContractValidationError("provider-stage blocked terminal schema changed")
        if (
            type(self.provider) is not ProviderFamily
            or type(self.model_family) is not ProviderModelFamily
            or type(self.stage) is not ProviderStage
            or type(self.block_reason) is not ProviderStageBlockReason
        ):
            raise ContractValidationError("provider-stage blocked enums are not closed")
        expected_provider, expected_model = _STAGE_OWNER[self.stage]
        if (self.provider, self.model_family) != (expected_provider, expected_model):
            raise ContractValidationError("provider-stage blocked owner changed")
        if self.block_reason is not ProviderStageBlockReason.DISPATCH_CUSTODY_AMBIGUOUS:
            raise ContractValidationError(
                "blocked_ambiguous is reserved for dispatch-custody ambiguity"
            )
        for count_value, field_name in (
            (self.maximum_attempts, "maximum attempts"),
            (self.attempts_total, "attempts total"),
            (self.retries_consumed, "retries consumed"),
        ):
            _require_nonnegative_int(count_value, f"provider-stage blocked {field_name}")
        if (
            self.maximum_attempts != MAXIMUM_PROVIDER_STAGE_ATTEMPTS
            or not 0 <= self.attempts_total <= MAXIMUM_PROVIDER_STAGE_ATTEMPTS
            or self.retries_consumed != max(0, self.attempts_total - 1)
        ):
            raise ContractValidationError("provider-stage blocked attempt accounting changed")
        if self.story_state_committed is not (self.stage is ProviderStage.RECORDER):
            raise ContractValidationError("provider-stage blocked story boundary changed")
        if self.failed_stage_effect_committed is not False:
            raise ContractValidationError("blocked provider stage cannot report a committed effect")
        _require_nonnegative_int(
            self.provider_operations_observed_total,
            "provider-stage blocked observed operations",
        )
        _require_nonnegative_int(
            self.provider_operations_conservative_total,
            "provider-stage blocked conservative operations",
        )
        if self.provider_operations_conservative_total < self.provider_operations_observed_total:
            raise ContractValidationError("provider-stage blocked accounting undercounts")
        for hash_value, field_name in (
            (self.request_sha256, "request"),
            (self.stage_input_sha256, "stage input"),
            (self.attempt_chain_sha256, "attempt chain"),
            (self.terminal_evidence_sha256, "terminal evidence"),
        ):
            _require_sha256(hash_value, f"provider-stage blocked {field_name}")

    def to_payload(self) -> dict[str, Any]:
        return _payload(self)


@dataclass(frozen=True, slots=True)
class ProviderStageRecoveryRequiredV1:
    """Hash-only terminal requiring an explicit operator recovery decision."""

    SCHEMA_VERSION: ClassVar[str] = "cera.provider_stage_retry_recovery_required.v1"

    schema_version: str
    severity: str
    provider: ProviderFamily
    model_family: ProviderModelFamily
    stage: ProviderStage
    maximum_attempts: int
    attempts_total: int
    retries_consumed: int
    story_state_committed: bool
    failed_stage_effect_committed: bool
    provider_operations_observed_total: int
    provider_operations_conservative_total: int
    final_failure_class: ProviderStageFailureClass | None
    block_reason: ProviderStageBlockReason | None
    request_sha256: str
    stage_input_sha256: str
    attempt_chain_sha256: str
    terminal_evidence_sha256: str

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION or self.severity != "critical":
            raise ContractValidationError("provider-stage recovery terminal schema changed")
        if (
            type(self.provider) is not ProviderFamily
            or type(self.model_family) is not ProviderModelFamily
            or type(self.stage) is not ProviderStage
        ):
            raise ContractValidationError("provider-stage recovery owner enums are not closed")
        expected_provider, expected_model = _STAGE_OWNER[self.stage]
        if (self.provider, self.model_family) != (expected_provider, expected_model):
            raise ContractValidationError("provider-stage recovery owner changed")
        has_failure = self.final_failure_class is not None
        has_block = self.block_reason is not None
        if has_failure is has_block:
            raise ContractValidationError(
                "provider-stage recovery requires exactly one closed reason"
            )
        if has_failure and self.final_failure_class not in NON_RETRYABLE_PROVIDER_STAGE_FAILURES:
            raise ContractValidationError(
                "provider-stage recovery failure is not a known non-Retry terminal"
            )
        if has_block and (
            type(self.block_reason) is not ProviderStageBlockReason
            or self.block_reason is ProviderStageBlockReason.DISPATCH_CUSTODY_AMBIGUOUS
        ):
            raise ContractValidationError(
                "dispatch ambiguity cannot be projected as operator recovery"
            )
        for count_value, field_name in (
            (self.maximum_attempts, "maximum attempts"),
            (self.attempts_total, "attempts total"),
            (self.retries_consumed, "retries consumed"),
        ):
            _require_nonnegative_int(count_value, f"provider-stage recovery {field_name}")
        if (
            self.maximum_attempts != MAXIMUM_PROVIDER_STAGE_ATTEMPTS
            or not 0 <= self.attempts_total <= MAXIMUM_PROVIDER_STAGE_ATTEMPTS
            or self.retries_consumed != max(0, self.attempts_total - 1)
        ):
            raise ContractValidationError("provider-stage recovery attempt accounting changed")
        if self.story_state_committed is not (self.stage is ProviderStage.RECORDER):
            raise ContractValidationError("provider-stage recovery story boundary changed")
        if self.failed_stage_effect_committed is not False:
            raise ContractValidationError("failed provider stage cannot report a committed effect")
        _require_nonnegative_int(
            self.provider_operations_observed_total,
            "provider-stage recovery observed operations",
        )
        _require_nonnegative_int(
            self.provider_operations_conservative_total,
            "provider-stage recovery conservative operations",
        )
        if self.provider_operations_conservative_total < self.provider_operations_observed_total:
            raise ContractValidationError("provider-stage recovery accounting undercounts")
        for hash_value, field_name in (
            (self.request_sha256, "request"),
            (self.stage_input_sha256, "stage input"),
            (self.attempt_chain_sha256, "attempt chain"),
            (self.terminal_evidence_sha256, "terminal evidence"),
        ):
            _require_sha256(hash_value, f"provider-stage recovery {field_name}")

    def to_payload(self) -> dict[str, Any]:
        return _payload(self)


ProviderStageRetryTerminalV1 = (
    ProviderStageRetryExhaustedV1 | ProviderStageRetryBlockedV1 | ProviderStageRecoveryRequiredV1
)


@dataclass(frozen=True, slots=True)
class ProviderStageRetryRecoveryV1:
    """Provider-free instruction for deterministic restart reconciliation."""

    SCHEMA_VERSION: ClassVar[str] = "cera.provider_stage_retry_recovery.v1"

    schema_version: str
    chain_id: str
    phase: ProviderStageRetryPhase
    action: ProviderStageRecoveryAction
    attempt_number: int | None
    attempts_remaining: int
    chain_sha256: str

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("provider-stage recovery schema changed")
        if (
            type(self.phase) is not ProviderStageRetryPhase
            or type(self.action) is not ProviderStageRecoveryAction
        ):
            raise ContractValidationError("provider-stage recovery enums are not closed")
        _require_chain_id(self.chain_id)
        if self.attempt_number is not None and (
            type(self.attempt_number) is not int
            or not 1 <= self.attempt_number <= MAXIMUM_PROVIDER_STAGE_ATTEMPTS
        ):
            raise ContractValidationError("provider-stage recovery attempt is invalid")
        if (
            type(self.attempts_remaining) is not int
            or not 0 <= self.attempts_remaining <= MAXIMUM_PROVIDER_STAGE_ATTEMPTS
        ):
            raise ContractValidationError("provider-stage recovery runway is invalid")
        _require_sha256(self.chain_sha256, "provider-stage recovery chain")

    def to_payload(self) -> dict[str, Any]:
        return _payload(self)


def provider_stage_retry_identity_from_payload(
    value: Any,
) -> ProviderStageRetryIdentityV1:
    if not isinstance(value, dict):
        raise ContractValidationError("provider-stage retry identity payload is invalid")
    decoded = from_mapping(ProviderStageRetryIdentityV1, value)
    assert isinstance(decoded, ProviderStageRetryIdentityV1)
    return decoded


def provider_stage_checkpoint_from_payload(
    value: Any,
) -> ProviderStageProtectedCheckpointV1:
    if not isinstance(value, dict):
        raise ContractValidationError("provider-stage checkpoint payload is invalid")
    decoded = from_mapping(ProviderStageProtectedCheckpointV1, value)
    assert isinstance(decoded, ProviderStageProtectedCheckpointV1)
    return decoded


def provider_stage_chain_from_payload(value: Any) -> ProviderStageRetryChainV1:
    if not isinstance(value, dict):
        raise ContractValidationError("provider-stage retry-chain payload is invalid")
    decoded = from_mapping(ProviderStageRetryChainV1, value)
    assert isinstance(decoded, ProviderStageRetryChainV1)
    return decoded


def provider_stage_terminal_from_payload(value: Any) -> ProviderStageRetryTerminalV1:
    if not isinstance(value, dict):
        raise ContractValidationError("provider-stage terminal payload is invalid")
    schema_version = value.get("schema_version")
    if schema_version == ProviderStageRetryExhaustedV1.SCHEMA_VERSION:
        decoded = from_mapping(ProviderStageRetryExhaustedV1, value)
        assert isinstance(decoded, ProviderStageRetryExhaustedV1)
        return decoded
    if schema_version == ProviderStageRetryBlockedV1.SCHEMA_VERSION:
        decoded = from_mapping(ProviderStageRetryBlockedV1, value)
        assert isinstance(decoded, ProviderStageRetryBlockedV1)
        return decoded
    if schema_version == ProviderStageRecoveryRequiredV1.SCHEMA_VERSION:
        decoded = from_mapping(ProviderStageRecoveryRequiredV1, value)
        assert isinstance(decoded, ProviderStageRecoveryRequiredV1)
        return decoded
    raise ContractValidationError("provider-stage terminal schema is unknown")


def _require_chain_id(value: str) -> None:
    prefix = "stage-retry-"
    if (
        not isinstance(value, str)
        or not value.startswith(prefix)
        or not re_is_sha256(value.removeprefix(prefix))
    ):
        raise ContractValidationError("provider-stage retry chain identity is invalid")
