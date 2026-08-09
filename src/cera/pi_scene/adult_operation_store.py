"""Protected, restart-safe custody for adult route operations and review.

Every state transition is an immutable artifact.  The prepared operation is
published before Adult Scene/Filter dispatch; a completed execution and its
classified outcome are published immediately afterwards.  A prepared-only
operation is deliberately ambiguous after restart and cannot be redispatched
silently.

This module stores protected provider content only beneath the dedicated
``PROTECTED_ADULT_OPERATIONS`` subtree.  Its safe summary contains hashes and
state only, never exact source, protected continuity, role output, or prose.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable, Mapping
from pathlib import Path
from threading import RLock
from typing import Any, cast
from uuid import uuid4

from cera.adult_pipeline.acceptance import AdultIntegratedExecutionV1
from cera.errors import ContractValidationError, StateConflictError
from cera.schema import from_mapping
from cera.serialization import (
    canonical_json,
    canonical_sha256,
    re_is_sha256,
    text_sha256,
    to_primitive,
)

from .adult_operation_contracts import (
    AdultOperationBeginV1,
    AdultOperationDecisionAction,
    AdultOperationDecisionV1,
    AdultOperationDispatchUncertainError,
    AdultOperationExecutionResolutionV1,
    AdultOperationRepairLinkV1,
    AdultOperationReviewState,
    AdultOperationSafeSummaryV1,
    ProtectedAdultOperationRecordV1,
)
from .adult_orchestration import (
    AdultRouteOperationOutcomeV1,
    PassedAdultRouteOperationV1,
    PreparedAdultRouteOperationV1,
    RejectedAdultRouteOperationV1,
    classify_prepared_adult_execution,
)

_IDENTITY = re.compile(r"[a-z][a-z0-9_.:-]{0,191}\Z")
_PROTECTED_DIRECTORY = "PROTECTED_ADULT_OPERATIONS"
_ARTIFACT_SCHEMA = "cera.pi_scene.protected_adult_operation_artifact.v1"


class ProtectedAdultOperationStore:
    """Immutable-artifact adult operation store beneath one protected subtree."""

    def __init__(self, runtime_root: Path) -> None:
        if not runtime_root.is_absolute():
            raise ContractValidationError("adult operation runtime root must be absolute")
        self.runtime_root = runtime_root.resolve()
        self.root = (self.runtime_root / _PROTECTED_DIRECTORY).resolve()
        if not self.root.is_relative_to(self.runtime_root):
            raise ContractValidationError("adult operation store escaped its runtime root")
        self.records_root = self.root / "RECORDS"
        self.records_root.mkdir(parents=True, exist_ok=True)
        self._claims_root = self.root / "CLAIMS"
        self._claims_root.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()

    def begin(self, prepared: PreparedAdultRouteOperationV1) -> AdultOperationBeginV1:
        """Publish exact prepared bytes before any Scene/Filter dispatch."""

        with self._lock, self._claim_for_pair(prepared.request_id, prepared.candidate_id):
            existing = self.lookup_optional(
                request_id=prepared.request_id,
                candidate_id=prepared.candidate_id,
            )
            if existing is not None:
                if existing.prepared != prepared:
                    raise StateConflictError(
                        "adult request/candidate claim resolves to different prepared bytes"
                    )
                return AdultOperationBeginV1(created=False, record=existing)

            final = self._operation_dir(prepared.operation_sha256)
            if final.exists():
                raise StateConflictError("adult operation identity is already occupied")
            temporary = self.records_root / f".tmp-{uuid4().hex}"
            temporary.mkdir(parents=False, exist_ok=False)
            try:
                _write_artifact(
                    temporary / "PREPARED.json",
                    kind="prepared",
                    value=prepared,
                )
                os.replace(temporary, final)
            except BaseException:
                _remove_empty_or_partial_directory(temporary)
                raise
            recovered = self._load_operation(final)
            if recovered.prepared != prepared:
                raise StateConflictError("adult prepared read-back binding changed")
            return AdultOperationBeginV1(created=True, record=recovered)

    def record_execution(
        self,
        outcome: AdultRouteOperationOutcomeV1,
    ) -> ProtectedAdultOperationRecordV1:
        """Publish exact execution and outcome bytes without prose rewriting."""

        prepared = outcome.prepared
        with self._lock, self._claim_for_operation(prepared.operation_sha256):
            record = self.lookup(
                request_id=prepared.request_id,
                candidate_id=prepared.candidate_id,
            )
            if record.prepared != prepared:
                raise StateConflictError("adult execution cites another prepared operation")
            expected = classify_prepared_adult_execution(
                prepared,
                outcome.protected_execution,
            )
            if expected != outcome:
                raise ContractValidationError("adult execution outcome classification changed")
            path = self._operation_dir(prepared.operation_sha256) / "EXECUTION.json"
            payload = {
                "outcome_kind": _outcome_kind(outcome),
                "protected_execution": to_primitive(outcome.protected_execution),
                "outcome": to_primitive(outcome),
            }
            if path.exists():
                recovered_outcome = self._read_execution(path, prepared=prepared)
                if recovered_outcome != outcome:
                    raise StateConflictError("adult operation execution changed on replay")
                return self.lookup(
                    request_id=prepared.request_id,
                    candidate_id=prepared.candidate_id,
                )
            if record.decision is not None or record.repair is not None:
                raise StateConflictError("terminal adult operation cannot receive execution")
            _write_artifact(path, kind="execution", value=payload)
            recovered_record = self.lookup(
                request_id=prepared.request_id,
                candidate_id=prepared.candidate_id,
            )
            if recovered_record.outcome != outcome:
                raise StateConflictError("adult execution read-back binding changed")
            return recovered_record

    def mark_accepted(
        self,
        *,
        request_id: str,
        candidate_id: str,
        decision: AdultOperationDecisionV1,
    ) -> ProtectedAdultOperationRecordV1:
        if decision.action is not AdultOperationDecisionAction.ACCEPT:
            raise ContractValidationError("adult accept requires an accept decision")
        return self._record_decision(
            request_id=request_id,
            candidate_id=candidate_id,
            decision=decision,
        )

    def mark_declined(
        self,
        *,
        request_id: str,
        candidate_id: str,
        decision: AdultOperationDecisionV1,
    ) -> ProtectedAdultOperationRecordV1:
        if decision.action is not AdultOperationDecisionAction.DECLINE:
            raise ContractValidationError("adult decline requires a decline decision")
        return self._record_decision(
            request_id=request_id,
            candidate_id=candidate_id,
            decision=decision,
        )

    def begin_repair(
        self,
        *,
        request_id: str,
        candidate_id: str,
        successor: PreparedAdultRouteOperationV1,
        repair_request_sha256: str,
    ) -> AdultOperationBeginV1:
        """Link one rejected operation to one complete fresh candidate."""

        _sha(repair_request_sha256, "adult repair request")
        with self._lock:
            predecessor = self.lookup(request_id=request_id, candidate_id=candidate_id)
            if predecessor.state not in {
                AdultOperationReviewState.EXECUTED_REJECTED,
                AdultOperationReviewState.REPAIRED,
            }:
                raise StateConflictError("adult repair requires an executed rejection")
            if predecessor.predecessor_repair is not None:
                raise StateConflictError("adult repair successor cannot be repaired again")
            _validate_complete_repair(predecessor.prepared, successor)
            link = AdultOperationRepairLinkV1(
                schema_version=AdultOperationRepairLinkV1.SCHEMA_VERSION,
                repair_request_sha256=repair_request_sha256,
                predecessor_operation_id=predecessor.prepared.operation_id,
                predecessor_operation_sha256=predecessor.prepared.operation_sha256,
                successor_operation_id=successor.operation_id,
                successor_operation_sha256=successor.operation_sha256,
            )
            if predecessor.repair is not None and predecessor.repair != link:
                raise StateConflictError("adult operation already has another repair")

            resolution = self.begin(successor)
            repair_path = self._operation_dir(predecessor.prepared.operation_sha256) / "REPAIR.json"
            with self._claim_for_operation(predecessor.prepared.operation_sha256):
                if repair_path.exists():
                    existing = _decode_artifact(
                        repair_path,
                        kind="repair",
                        model=AdultOperationRepairLinkV1,
                    )
                    if existing != link:
                        raise StateConflictError("adult operation already has another repair")
                else:
                    _write_artifact(repair_path, kind="repair", value=link)

            repaired = self.lookup(request_id=request_id, candidate_id=candidate_id)
            if repaired.state is not AdultOperationReviewState.REPAIRED:
                raise StateConflictError("adult repair link did not become durable")
            successor_record = self.lookup(
                request_id=successor.request_id,
                candidate_id=successor.candidate_id,
            )
            return AdultOperationBeginV1(
                created=resolution.created,
                record=successor_record,
            )

    def lookup(
        self,
        *,
        request_id: str,
        candidate_id: str,
    ) -> ProtectedAdultOperationRecordV1:
        result = self.lookup_optional(request_id=request_id, candidate_id=candidate_id)
        if result is None:
            raise StateConflictError("adult operation claim does not exist")
        return result

    def lookup_optional(
        self,
        *,
        request_id: str,
        candidate_id: str,
    ) -> ProtectedAdultOperationRecordV1 | None:
        _identity(request_id, "adult operation request identity")
        _identity(candidate_id, "adult operation candidate identity")
        matches: list[ProtectedAdultOperationRecordV1] = []
        for path in sorted(self.records_root.glob("op-*")):
            if not path.is_dir():
                raise StateConflictError("adult operation record path is invalid")
            record = self._load_operation(path)
            if (
                record.prepared.request_id == request_id
                and record.prepared.candidate_id == candidate_id
            ):
                matches.append(record)
        if len(matches) > 1:
            raise StateConflictError("adult request/candidate claim is ambiguous")
        return matches[0] if matches else None

    def safe_summary(
        self,
        *,
        request_id: str,
        candidate_id: str,
    ) -> AdultOperationSafeSummaryV1:
        record = self.lookup(request_id=request_id, candidate_id=candidate_id)
        decision = record.decision
        return AdultOperationSafeSummaryV1(
            schema_version=AdultOperationSafeSummaryV1.SCHEMA_VERSION,
            request_id=record.prepared.request_id,
            candidate_id=record.prepared.candidate_id,
            operation_id=record.prepared.operation_id,
            operation_sha256=record.prepared.operation_sha256,
            state=record.state,
            outcome_sha256=(None if record.outcome is None else record.outcome.outcome_sha256),
            protected_exact_story_prose_sha256=(record.protected_exact_story_prose_sha256),
            decision_request_sha256=(
                None if decision is None else decision.decision_request_sha256
            ),
            accepted_binding_sha256=(
                None if decision is None else decision.accepted_binding_sha256
            ),
            predecessor_operation_id=(
                None
                if record.predecessor_repair is None
                else record.predecessor_repair.predecessor_operation_id
            ),
            successor_operation_id=(
                None if record.repair is None else record.repair.successor_operation_id
            ),
        )

    def _record_decision(
        self,
        *,
        request_id: str,
        candidate_id: str,
        decision: AdultOperationDecisionV1,
    ) -> ProtectedAdultOperationRecordV1:
        record = self.lookup(request_id=request_id, candidate_id=candidate_id)
        operation_sha256 = record.prepared.operation_sha256
        with self._lock, self._claim_for_operation(operation_sha256):
            record = self.lookup(request_id=request_id, candidate_id=candidate_id)
            if record.repair is not None:
                raise StateConflictError("repaired adult operation cannot receive a decision")
            if record.outcome is None:
                raise StateConflictError("prepared adult operation cannot receive a decision")
            if decision.action is AdultOperationDecisionAction.ACCEPT and not isinstance(
                record.outcome, PassedAdultRouteOperationV1
            ):
                raise StateConflictError("rejected adult operation cannot be accepted")
            path = self._operation_dir(operation_sha256) / "DECISION.json"
            if path.exists():
                existing = _decode_artifact(
                    path,
                    kind="decision",
                    model=AdultOperationDecisionV1,
                )
                if existing != decision:
                    raise StateConflictError("adult operation decision changed on replay")
            else:
                _write_artifact(path, kind="decision", value=decision)
            return self.lookup(request_id=request_id, candidate_id=candidate_id)

    def _load_operation(self, path: Path) -> ProtectedAdultOperationRecordV1:
        path = path.resolve()
        if not path.is_relative_to(self.records_root) or path.parent != self.records_root:
            raise StateConflictError("adult operation path escaped protected records")
        allowed_names = {
            "PREPARED.json",
            "EXECUTION.json",
            "DECISION.json",
            "REPAIR.json",
        }
        try:
            children = tuple(path.iterdir())
        except OSError as exc:
            raise StateConflictError("adult operation record is unreadable") from exc
        if any(not child.is_file() or child.name not in allowed_names for child in children):
            raise StateConflictError("adult operation record contains an unknown artifact")
        prepared = _decode_artifact(
            path / "PREPARED.json",
            kind="prepared",
            model=PreparedAdultRouteOperationV1,
        )
        if path.name != f"op-{prepared.operation_sha256}":
            raise StateConflictError("adult operation directory binding changed")
        execution_path = path / "EXECUTION.json"
        outcome = (
            self._read_execution(execution_path, prepared=prepared)
            if execution_path.exists()
            else None
        )
        decision_path = path / "DECISION.json"
        decision = (
            _decode_artifact(
                decision_path,
                kind="decision",
                model=AdultOperationDecisionV1,
            )
            if decision_path.exists()
            else None
        )
        repair_path = path / "REPAIR.json"
        repair = (
            _decode_artifact(
                repair_path,
                kind="repair",
                model=AdultOperationRepairLinkV1,
            )
            if repair_path.exists()
            else None
        )
        if repair is not None:
            self._validate_repair_endpoints(repair)
        predecessor_repair = self._find_predecessor_repair(prepared)
        state = _derive_state(outcome=outcome, decision=decision, repair=repair)
        return ProtectedAdultOperationRecordV1(
            state=state,
            prepared=prepared,
            outcome=outcome,
            decision=decision,
            repair=repair,
            predecessor_repair=predecessor_repair,
        )

    def _read_execution(
        self,
        path: Path,
        *,
        prepared: PreparedAdultRouteOperationV1,
    ) -> AdultRouteOperationOutcomeV1:
        value = _read_artifact(path, kind="execution")
        required = {"outcome_kind", "protected_execution", "outcome"}
        if not isinstance(value, dict) or set(value) != required:
            raise StateConflictError("adult execution artifact shape changed")
        try:
            execution = from_mapping(
                AdultIntegratedExecutionV1,
                _mapping(value["protected_execution"], "adult protected execution"),
            )
            kind = value["outcome_kind"]
            outcome_type: type[PassedAdultRouteOperationV1] | type[RejectedAdultRouteOperationV1]
            if kind == "passed":
                outcome_type = PassedAdultRouteOperationV1
            elif kind == "rejected":
                outcome_type = RejectedAdultRouteOperationV1
            else:
                raise ContractValidationError("adult outcome kind changed")
            outcome = cast(
                AdultRouteOperationOutcomeV1,
                from_mapping(
                    outcome_type,
                    _mapping(value["outcome"], "adult protected outcome"),
                ),
            )
            expected = classify_prepared_adult_execution(prepared, execution)
        except (ContractValidationError, StateConflictError, TypeError, ValueError) as exc:
            raise StateConflictError("adult execution artifact is invalid") from exc
        if outcome != expected or outcome.prepared != prepared:
            raise StateConflictError("adult execution/outcome binding changed")
        return outcome

    def _find_predecessor_repair(
        self,
        prepared: PreparedAdultRouteOperationV1,
    ) -> AdultOperationRepairLinkV1 | None:
        matches: list[AdultOperationRepairLinkV1] = []
        for repair_path in sorted(self.records_root.glob("op-*/REPAIR.json")):
            resolved = repair_path.resolve()
            if not resolved.is_relative_to(self.records_root):
                raise StateConflictError("adult repair artifact escaped protected records")
            link = _decode_artifact(
                resolved,
                kind="repair",
                model=AdultOperationRepairLinkV1,
            )
            self._validate_repair_endpoints(link)
            if (
                link.successor_operation_id == prepared.operation_id
                and link.successor_operation_sha256 == prepared.operation_sha256
            ):
                matches.append(link)
        if len(matches) > 1:
            raise StateConflictError("adult operation has ambiguous repair predecessors")
        return matches[0] if matches else None

    def _validate_repair_endpoints(self, link: AdultOperationRepairLinkV1) -> None:
        for role, operation_id, operation_sha256 in (
            (
                "predecessor",
                link.predecessor_operation_id,
                link.predecessor_operation_sha256,
            ),
            (
                "successor",
                link.successor_operation_id,
                link.successor_operation_sha256,
            ),
        ):
            path = self._operation_dir(operation_sha256) / "PREPARED.json"
            if not path.is_file():
                raise StateConflictError(f"adult repair {role} operation is missing")
            prepared = _decode_artifact(
                path,
                kind="prepared",
                model=PreparedAdultRouteOperationV1,
            )
            if (
                prepared.operation_id != operation_id
                or prepared.operation_sha256 != operation_sha256
            ):
                raise StateConflictError(f"adult repair {role} binding changed")

    def _operation_dir(self, operation_sha256: str) -> Path:
        _sha(operation_sha256, "adult operation path hash")
        path = (self.records_root / f"op-{operation_sha256}").resolve()
        if not path.is_relative_to(self.records_root):
            raise ContractValidationError("adult operation path escaped protected records")
        return path

    def _claim_for_pair(self, request_id: str, candidate_id: str) -> _ExclusiveClaim:
        key = text_sha256(f"{request_id}\0{candidate_id}")
        return _ExclusiveClaim(self._claims_root / f"pair-{key}.claim")

    def _claim_for_operation(self, operation_sha256: str) -> _ExclusiveClaim:
        return _ExclusiveClaim(self._claims_root / f"op-{operation_sha256}.claim")


class ProtectedAdultOperationController:
    """Recover-first seam around orchestrator preparation and execution."""

    def __init__(self, store: ProtectedAdultOperationStore) -> None:
        self.store = store

    def recover_or_prepare(
        self,
        *,
        request_id: str,
        candidate_id: str,
        prepare: Callable[[], PreparedAdultRouteOperationV1],
    ) -> AdultOperationBeginV1:
        existing = self.store.lookup_optional(
            request_id=request_id,
            candidate_id=candidate_id,
        )
        if existing is not None:
            return AdultOperationBeginV1(created=False, record=existing)
        prepared = prepare()
        if prepared.request_id != request_id or prepared.candidate_id != candidate_id:
            raise StateConflictError("adult prepare callback changed request/candidate custody")
        return self.store.begin(prepared)

    def execute_new(
        self,
        begin: AdultOperationBeginV1,
        *,
        execute: Callable[[PreparedAdultRouteOperationV1], AdultRouteOperationOutcomeV1],
    ) -> AdultOperationExecutionResolutionV1:
        if not begin.created:
            if begin.record.outcome is None:
                raise AdultOperationDispatchUncertainError(
                    request_id=begin.record.prepared.request_id,
                    candidate_id=begin.record.prepared.candidate_id,
                )
            return AdultOperationExecutionResolutionV1(
                replayed=True,
                record=begin.record,
            )
        outcome = execute(begin.record.prepared)
        record = self.store.record_execution(outcome)
        return AdultOperationExecutionResolutionV1(replayed=False, record=record)


class _ExclusiveClaim:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.acquired = False

    def __enter__(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = os.open(
                self.path,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o600,
            )
        except FileExistsError as exc:
            raise StateConflictError("adult operation custody claim is already active") from exc
        try:
            os.write(descriptor, b"active\n")
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        self.acquired = True

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        del exc_type, exc, traceback
        if self.acquired:
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass


def _derive_state(
    *,
    outcome: AdultRouteOperationOutcomeV1 | None,
    decision: AdultOperationDecisionV1 | None,
    repair: AdultOperationRepairLinkV1 | None,
) -> AdultOperationReviewState:
    if decision is not None and repair is not None:
        raise StateConflictError("adult operation has both decision and repair artifacts")
    if repair is not None:
        if not isinstance(outcome, RejectedAdultRouteOperationV1):
            raise StateConflictError("adult repair does not follow a rejection")
        return AdultOperationReviewState.REPAIRED
    if decision is not None:
        if outcome is None:
            raise StateConflictError("adult decision lacks an execution")
        if decision.action is AdultOperationDecisionAction.ACCEPT:
            if not isinstance(outcome, PassedAdultRouteOperationV1):
                raise StateConflictError("adult accepted decision follows a rejection")
            return AdultOperationReviewState.ACCEPTED
        return AdultOperationReviewState.DECLINED
    if isinstance(outcome, PassedAdultRouteOperationV1):
        return AdultOperationReviewState.EXECUTED_PASSED
    if isinstance(outcome, RejectedAdultRouteOperationV1):
        return AdultOperationReviewState.EXECUTED_REJECTED
    return AdultOperationReviewState.PREPARED


def _validate_complete_repair(
    predecessor: PreparedAdultRouteOperationV1,
    successor: PreparedAdultRouteOperationV1,
) -> None:
    if predecessor.request_id != successor.request_id:
        raise ContractValidationError("adult repair changed the exact request identity")
    if predecessor.candidate_id == successor.candidate_id:
        raise ContractValidationError("adult repair requires a fresh candidate identity")
    if predecessor.route_state != successor.route_state:
        raise ContractValidationError("adult repair changed accepted route custody")
    if predecessor.scene_request != successor.scene_request:
        raise ContractValidationError(
            "adult repair must use one complete byte-identical frozen Scene request"
        )


def _outcome_kind(outcome: AdultRouteOperationOutcomeV1) -> str:
    return "passed" if isinstance(outcome, PassedAdultRouteOperationV1) else "rejected"


def _write_artifact(path: Path, *, kind: str, value: object) -> None:
    primitive = to_primitive(value)
    body = {
        "schema_version": _ARTIFACT_SCHEMA,
        "kind": kind,
        "value": primitive,
        "value_sha256": canonical_sha256(primitive),
    }
    payload = {**body, "artifact_sha256": canonical_sha256(body)}
    _atomic_write_text(path, canonical_json(payload) + "\n")


def _read_artifact(path: Path, *, kind: str) -> object:
    try:
        text = path.read_text(encoding="utf-8")
        payload = json.loads(text)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StateConflictError("adult protected artifact is unreadable") from exc
    required = {
        "schema_version",
        "kind",
        "value",
        "value_sha256",
        "artifact_sha256",
    }
    if not isinstance(payload, dict) or set(payload) != required:
        raise StateConflictError("adult protected artifact shape changed")
    if text != canonical_json(payload) + "\n":
        raise StateConflictError("adult protected artifact is not canonical")
    if payload["schema_version"] != _ARTIFACT_SCHEMA or payload["kind"] != kind:
        raise StateConflictError("adult protected artifact identity changed")
    body = {key: payload[key] for key in required if key != "artifact_sha256"}
    if payload["artifact_sha256"] != canonical_sha256(body):
        raise StateConflictError("adult protected artifact integrity changed")
    if payload["value_sha256"] != canonical_sha256(payload["value"]):
        raise StateConflictError("adult protected artifact value binding changed")
    return payload["value"]


def _decode_artifact(path: Path, *, kind: str, model: type[Any]) -> Any:
    value = _read_artifact(path, kind=kind)
    try:
        return from_mapping(model, _mapping(value, f"adult {kind} artifact"))
    except (ContractValidationError, TypeError, ValueError) as exc:
        raise StateConflictError(f"adult {kind} artifact is invalid") from exc


def _mapping(value: object, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ContractValidationError(f"{field} must be an object")
    return value


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _remove_empty_or_partial_directory(path: Path) -> None:
    if not path.exists():
        return
    for child in path.iterdir():
        if child.is_file():
            child.unlink()
    try:
        path.rmdir()
    except OSError:
        pass


def _identity(value: str, field: str) -> None:
    if type(value) is not str or _IDENTITY.fullmatch(value) is None:
        raise ContractValidationError(f"{field} must be a stable identity")


def _sha(value: str, field: str) -> None:
    if type(value) is not str or not re_is_sha256(value):
        raise ContractValidationError(f"{field} must be SHA-256")
