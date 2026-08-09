"""Typed protected-custody contracts for adult operation review."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar, Protocol

from cera.errors import ContractValidationError, StateConflictError
from cera.serialization import re_is_sha256

from .adult_orchestration import (
    AdultRouteOperationOutcomeV1,
    PassedAdultRouteOperationV1,
    PreparedAdultRouteOperationV1,
    RejectedAdultRouteOperationV1,
)

_IDENTITY = re.compile(r"[a-z][a-z0-9_.:-]{0,191}\Z")


class AdultOperationReviewState(StrEnum):
    PREPARED = "prepared"
    EXECUTED_REJECTED = "executed-rejected"
    EXECUTED_PASSED = "executed-passed"
    ACCEPTED = "accepted"
    DECLINED = "declined"
    REPAIRED = "repaired"


class AdultOperationDecisionAction(StrEnum):
    ACCEPT = "accept"
    DECLINE = "decline"


class AdultOperationDispatchUncertainError(StateConflictError):
    """A durable prepared phase exists but provider completion is unknown."""

    def __init__(self, *, request_id: str, candidate_id: str) -> None:
        super().__init__(
            "adult operation has prepared custody without a durable execution; "
            "automatic redispatch is blocked"
        )
        self.request_id = request_id
        self.candidate_id = candidate_id


@dataclass(frozen=True, slots=True)
class AdultOperationDecisionV1:
    """Hash-only review decision; accepted content remains in its own store."""

    SCHEMA_VERSION: ClassVar[str] = "cera.pi_scene.adult_operation_decision.v1"

    schema_version: str
    action: AdultOperationDecisionAction
    decision_id: str
    decision_request_sha256: str
    accepted_binding_sha256: str | None

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("adult operation decision schema changed")
        _identity(self.decision_id, "adult operation decision identity")
        _sha(self.decision_request_sha256, "adult operation decision request")
        if self.action is AdultOperationDecisionAction.ACCEPT:
            if self.accepted_binding_sha256 is None:
                raise ContractValidationError(
                    "adult accept decision requires the atomic accepted binding"
                )
            _sha(self.accepted_binding_sha256, "adult accepted binding")
        elif self.accepted_binding_sha256 is not None:
            raise ContractValidationError("adult decline cannot contain an accepted binding")


@dataclass(frozen=True, slots=True)
class AdultOperationRepairLinkV1:
    """One complete fresh successor; no partial-prose or merge representation."""

    SCHEMA_VERSION: ClassVar[str] = "cera.pi_scene.adult_operation_repair_link.v1"

    schema_version: str
    repair_request_sha256: str
    predecessor_operation_id: str
    predecessor_operation_sha256: str
    successor_operation_id: str
    successor_operation_sha256: str

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("adult operation repair-link schema changed")
        _sha(self.repair_request_sha256, "adult repair request")
        for field_name in ("predecessor_operation_id", "successor_operation_id"):
            _identity(getattr(self, field_name), f"adult repair {field_name}")
        for field_name in (
            "predecessor_operation_sha256",
            "successor_operation_sha256",
        ):
            _sha(getattr(self, field_name), f"adult repair {field_name}")
        if self.predecessor_operation_id == self.successor_operation_id:
            raise ContractValidationError("adult repair cannot link an operation to itself")


@dataclass(frozen=True, slots=True)
class ProtectedAdultOperationRecordV1:
    """Fully decoded protected record; never use as an ordinary diagnostic."""

    PROTECTION_SCOPE: ClassVar[str] = "protected_runtime_only"

    state: AdultOperationReviewState
    prepared: PreparedAdultRouteOperationV1
    outcome: AdultRouteOperationOutcomeV1 | None
    decision: AdultOperationDecisionV1 | None
    repair: AdultOperationRepairLinkV1 | None
    predecessor_repair: AdultOperationRepairLinkV1 | None

    def __post_init__(self) -> None:
        passed = isinstance(self.outcome, PassedAdultRouteOperationV1)
        rejected = isinstance(self.outcome, RejectedAdultRouteOperationV1)
        if self.outcome is not None and self.outcome.prepared != self.prepared:
            raise ContractValidationError("adult operation outcome cites another preparation")
        if self.state is AdultOperationReviewState.PREPARED:
            if self.outcome is not None or self.decision is not None or self.repair is not None:
                raise ContractValidationError("prepared adult operation has terminal artifacts")
        elif self.state is AdultOperationReviewState.EXECUTED_REJECTED:
            if not rejected or self.decision is not None or self.repair is not None:
                raise ContractValidationError("adult rejected operation state is inconsistent")
        elif self.state is AdultOperationReviewState.EXECUTED_PASSED:
            if not passed or self.decision is not None or self.repair is not None:
                raise ContractValidationError("adult passed operation state is inconsistent")
        elif self.state is AdultOperationReviewState.ACCEPTED:
            if (
                not passed
                or self.decision is None
                or self.decision.action is not AdultOperationDecisionAction.ACCEPT
                or self.repair is not None
            ):
                raise ContractValidationError("adult accepted operation state is inconsistent")
        elif self.state is AdultOperationReviewState.DECLINED:
            if (
                self.outcome is None
                or self.decision is None
                or self.decision.action is not AdultOperationDecisionAction.DECLINE
                or self.repair is not None
            ):
                raise ContractValidationError("adult declined operation state is inconsistent")
        elif not rejected or self.decision is not None or self.repair is None:
            raise ContractValidationError("adult repaired operation state is inconsistent")

        if self.repair is not None:
            if (
                self.repair.predecessor_operation_id != self.prepared.operation_id
                or self.repair.predecessor_operation_sha256 != self.prepared.operation_sha256
            ):
                raise ContractValidationError("adult repair predecessor binding changed")
        if self.predecessor_repair is not None:
            if (
                self.predecessor_repair.successor_operation_id != self.prepared.operation_id
                or self.predecessor_repair.successor_operation_sha256
                != self.prepared.operation_sha256
            ):
                raise ContractValidationError("adult repair successor binding changed")

    @property
    def protected_exact_story_prose_sha256(self) -> str | None:
        if self.outcome is None:
            return None
        return self.outcome.protected_exact_story_prose_sha256


@dataclass(frozen=True, slots=True)
class AdultOperationSafeSummaryV1:
    """Privacy-safe HTTP/debug projection with no exact protected content."""

    SCHEMA_VERSION: ClassVar[str] = "cera.pi_scene.adult_operation_safe_summary.v1"

    schema_version: str
    request_id: str
    candidate_id: str
    operation_id: str
    operation_sha256: str
    state: AdultOperationReviewState
    outcome_sha256: str | None
    protected_exact_story_prose_sha256: str | None
    decision_request_sha256: str | None
    accepted_binding_sha256: str | None
    predecessor_operation_id: str | None
    successor_operation_id: str | None

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("adult operation safe-summary schema changed")
        for field_name in ("request_id", "candidate_id", "operation_id"):
            _identity(getattr(self, field_name), f"adult summary {field_name}")
        _sha(self.operation_sha256, "adult summary operation")
        for field_name in (
            "outcome_sha256",
            "protected_exact_story_prose_sha256",
            "decision_request_sha256",
            "accepted_binding_sha256",
        ):
            value = getattr(self, field_name)
            if value is not None:
                _sha(value, f"adult summary {field_name}")
        for field_name in ("predecessor_operation_id", "successor_operation_id"):
            value = getattr(self, field_name)
            if value is not None:
                _identity(value, f"adult summary {field_name}")


@dataclass(frozen=True, slots=True)
class AdultOperationBeginV1:
    """A begin/recovery result whose ``created`` bit gates fresh dispatch."""

    PROTECTION_SCOPE: ClassVar[str] = "protected_runtime_only"

    created: bool
    record: ProtectedAdultOperationRecordV1

    def __post_init__(self) -> None:
        if type(self.created) is not bool:
            raise ContractValidationError("adult operation created flag must be boolean")
        if self.created and self.record.state is not AdultOperationReviewState.PREPARED:
            raise ContractValidationError("new adult operation is not in prepared state")


@dataclass(frozen=True, slots=True)
class AdultOperationExecutionResolutionV1:
    """Protected completed/replayed result for the HTTP controller."""

    PROTECTION_SCOPE: ClassVar[str] = "protected_runtime_only"

    replayed: bool
    record: ProtectedAdultOperationRecordV1

    def __post_init__(self) -> None:
        if type(self.replayed) is not bool:
            raise ContractValidationError("adult execution replay flag must be boolean")
        if self.record.outcome is None:
            raise ContractValidationError("adult execution resolution lacks an outcome")


class AdultOperationCustodyPort(Protocol):
    def begin(self, prepared: PreparedAdultRouteOperationV1) -> AdultOperationBeginV1: ...

    def record_execution(
        self,
        outcome: AdultRouteOperationOutcomeV1,
    ) -> ProtectedAdultOperationRecordV1: ...

    def lookup(
        self,
        *,
        request_id: str,
        candidate_id: str,
    ) -> ProtectedAdultOperationRecordV1: ...


def _identity(value: str, field: str) -> None:
    if type(value) is not str or _IDENTITY.fullmatch(value) is None:
        raise ContractValidationError(f"{field} must be a stable identity")


def _sha(value: str, field: str) -> None:
    if type(value) is not str or not re_is_sha256(value):
        raise ContractValidationError(f"{field} must be SHA-256")
