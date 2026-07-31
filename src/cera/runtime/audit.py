"""Append-only pre-publication stage-journal coordinator."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from cera.errors import ErrorCode
from cera.ids import IdKind, TypedId, deterministic_id
from cera.kernel import PreparedTurn
from cera.serialization import domain_sha256

from .failure import (
    FailureEvidenceHandle,
    PrivacySafeReceiptPayload,
    TurnFailureEvidenceBundle,
    TurnFailureEvidenceBundleV2,
    TurnFailureEvidenceBundleV3,
    TurnStageAuditEntry,
    TurnStageStatus,
)


class StageAuditStorePort(Protocol):
    def append_turn_failure_evidence(
        self,
        bundle: (
            TurnFailureEvidenceBundle
            | TurnFailureEvidenceBundleV2
            | TurnFailureEvidenceBundleV3
        ),
    ) -> None: ...

    def append_turn_stage_entry(self, entry: TurnStageAuditEntry) -> None: ...

    def turn_stage_entries(
        self,
        request_id: TypedId,
    ) -> tuple[TurnStageAuditEntry, ...]: ...


@dataclass(frozen=True, slots=True)
class TurnRestartAudit:
    request_id: TypedId
    entries: tuple[TurnStageAuditEntry, ...]
    completed_stages: tuple[str, ...]
    last_status: TurnStageStatus | None
    automatic_resume_permitted: bool


class TurnStageAuditJournal:
    def __init__(self, store: StageAuditStorePort) -> None:
        self.store = store

    def started(
        self,
        prepared: PreparedTurn,
        stage: str,
        *,
        input_sha256: str | None,
        external_provider_calls_observed: int,
    ) -> TurnStageAuditEntry:
        return self._append(
            prepared,
            stage,
            TurnStageStatus.STARTED,
            input_sha256=input_sha256,
            output_sha256=None,
            failure_bundle_id=None,
            external_provider_calls_observed=external_provider_calls_observed,
        )

    def completed(
        self,
        prepared: PreparedTurn,
        stage: str,
        *,
        input_sha256: str | None,
        output_sha256: str,
        external_provider_calls_observed: int,
    ) -> TurnStageAuditEntry:
        return self._append(
            prepared,
            stage,
            TurnStageStatus.COMPLETED,
            input_sha256=input_sha256,
            output_sha256=output_sha256,
            failure_bundle_id=None,
            external_provider_calls_observed=external_provider_calls_observed,
        )

    def failed(
        self,
        prepared: PreparedTurn,
        stage: str,
        error_code: ErrorCode,
        safe_message: str,
        *,
        input_sha256: str | None,
        output_sha256: str | None,
        retained_evidence: tuple[FailureEvidenceHandle, ...] = (),
        external_provider_calls_observed: int,
        retained_receipt_payloads: tuple[PrivacySafeReceiptPayload, ...] = (),
        safe_diagnostic_codes: tuple[str, ...] = (),
    ) -> TurnFailureEvidenceBundleV3:
        core = {
            "request_id": str(prepared.request.request_id),
            "branch_id": str(prepared.request.branch_id),
            "generation_id": str(prepared.request.generation_id),
            "stage": stage,
            "error_code": error_code.value,
            "input_sha256": input_sha256,
            "output_sha256": output_sha256,
            "retained_evidence": retained_evidence,
            "retained_receipt_payloads": retained_receipt_payloads,
            "safe_diagnostic_codes": safe_diagnostic_codes,
            "external_provider_calls_observed": external_provider_calls_observed,
        }
        bundle_id = deterministic_id(
            IdKind.FAILURE_BUNDLE,
            "cera.turn_failure_evidence_bundle.v3",
            domain_sha256("cera.turn_failure_evidence.binding.v3", core),
        )
        bundle = TurnFailureEvidenceBundleV3(
            schema_version=TurnFailureEvidenceBundleV3.SCHEMA_VERSION,
            failure_bundle_id=bundle_id,
            request_id=prepared.request.request_id,
            branch_id=prepared.request.branch_id,
            generation_id=prepared.request.generation_id,
            stage=stage,
            error_code=error_code,
            safe_message=safe_message,
            input_sha256=input_sha256,
            output_sha256=output_sha256,
            retained_evidence=retained_evidence,
            retained_receipt_payloads=retained_receipt_payloads,
            safe_diagnostic_codes=safe_diagnostic_codes,
            external_provider_calls_observed=external_provider_calls_observed,
            story_state_committed=False,
            authoritative_store_writes=0,
            retains_raw_source=False,
            retains_story_prose=False,
            retains_private_evidence=False,
            retains_prompt=False,
            retains_secret=False,
        )
        self.store.append_turn_failure_evidence(bundle)
        self._append(
            prepared,
            stage,
            TurnStageStatus.FAILED,
            input_sha256=input_sha256,
            output_sha256=output_sha256,
            failure_bundle_id=bundle_id,
            external_provider_calls_observed=external_provider_calls_observed,
        )
        return bundle

    def inspect_restart(self, request_id: TypedId) -> TurnRestartAudit:
        entries = self.store.turn_stage_entries(request_id)
        completed = tuple(
            dict.fromkeys(
                value.stage
                for value in entries
                if value.status is TurnStageStatus.COMPLETED
            )
        )
        return TurnRestartAudit(
            request_id=request_id,
            entries=entries,
            completed_stages=completed,
            last_status=(entries[-1].status if entries else None),
            automatic_resume_permitted=False,
        )

    def _append(
        self,
        prepared: PreparedTurn,
        stage: str,
        status: TurnStageStatus,
        *,
        input_sha256: str | None,
        output_sha256: str | None,
        failure_bundle_id: TypedId | None,
        external_provider_calls_observed: int,
    ) -> TurnStageAuditEntry:
        sequence = len(
            self.store.turn_stage_entries(prepared.request.request_id)
        ) + 1
        identity = (
            f"{prepared.request.request_id}|{sequence}|{stage}|{status.value}|"
            f"{input_sha256}|{output_sha256}|{failure_bundle_id}"
        )
        entry = TurnStageAuditEntry(
            schema_version=TurnStageAuditEntry.SCHEMA_VERSION,
            journal_id=deterministic_id(
                IdKind.STAGE_JOURNAL,
                "cera.turn_stage_audit_entry.v1",
                identity,
            ),
            request_id=prepared.request.request_id,
            branch_id=prepared.request.branch_id,
            generation_id=prepared.request.generation_id,
            sequence=sequence,
            stage=stage,
            status=status,
            input_sha256=input_sha256,
            output_sha256=output_sha256,
            failure_bundle_id=failure_bundle_id,
            external_provider_calls_observed=external_provider_calls_observed,
            story_state_committed=False,
        )
        self.store.append_turn_stage_entry(entry)
        return entry
