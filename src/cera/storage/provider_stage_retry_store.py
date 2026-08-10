"""SQLite/WAL authority for bounded provider-stage Retry chains.

Metadata transitions use short ``BEGIN IMMEDIATE`` transactions in CERA's
existing authority database.  Exact input/result bytes remain behind the
protected blob port and are represented here only by opaque hashes and sizes.
No method in this module dispatches a provider.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import replace
from typing import Any, Protocol

from cera.errors import ContractValidationError, StateConflictError, TransactionError
from cera.pi_scene.provider_stage_retry import (
    MAXIMUM_PROVIDER_STAGE_ATTEMPTS,
    NON_RETRYABLE_PROVIDER_STAGE_FAILURES,
    PRETRANSPORT_PROVIDER_STAGE_FAILURES,
    RETRYABLE_PROVIDER_STAGE_FAILURES,
    ProviderStage,
    ProviderStageAttemptPhase,
    ProviderStageAttemptV1,
    ProviderStageBlockReason,
    ProviderStageCheckpointKind,
    ProviderStageFailureClass,
    ProviderStageProtectedCheckpointV1,
    ProviderStageRecoveryRequiredV1,
    ProviderStageRetryBlockedV1,
    ProviderStageRetryChainV1,
    ProviderStageRetryExhaustedV1,
    ProviderStageRetryIdentityV1,
    ProviderStageRetryPhase,
    ProviderStageRetryTerminalV1,
    provider_stage_checkpoint_from_payload,
    provider_stage_retry_identity_from_payload,
)
from cera.pi_scene.provider_stage_retry_blob import (
    ProtectedStageBlobPort,
    ProtectedStageBlobReceiptV1,
)
from cera.schema import from_mapping
from cera.serialization import (
    bytes_sha256,
    canonical_json,
    canonical_sha256,
    domain_sha256,
    re_is_sha256,
)


class _SQLiteAuthorityOwner(Protocol):
    def _connect(self) -> AbstractContextManager[sqlite3.Connection]: ...

    @staticmethod
    def _begin(connection: sqlite3.Connection) -> None: ...

    @staticmethod
    def _now() -> str: ...


class SQLiteProviderStageRetryStore:
    """Retry-specific adapter over CERA's existing SQLite authority store."""

    def __init__(
        self,
        authority_store: _SQLiteAuthorityOwner,
        blob_store: ProtectedStageBlobPort,
    ) -> None:
        self.authority_store = authority_store
        self.blob_store = blob_store

    def freeze_input(
        self,
        identity: ProviderStageRetryIdentityV1,
        exact_input: bytes,
    ) -> ProviderStageProtectedCheckpointV1:
        chain = self.begin(identity, exact_input)
        with self.authority_store._connect() as connection:
            return self._input_checkpoint(connection, chain.chain_id)[0]

    def begin(
        self,
        identity: ProviderStageRetryIdentityV1,
        exact_input: bytes,
    ) -> ProviderStageRetryChainV1:
        self._require_identity(identity)
        if not isinstance(exact_input, bytes) or not exact_input:
            raise ContractValidationError("provider-stage exact input must be non-empty bytes")
        if bytes_sha256(exact_input) != identity.stage_input_sha256:
            raise ContractValidationError("provider-stage exact input hash changed")
        blob = self.blob_store.freeze(
            chain_id=identity.chain_id,
            checkpoint_kind=ProviderStageCheckpointKind.INPUT,
            attempt_number=None,
            exact_bytes=exact_input,
        )
        checkpoint = ProviderStageProtectedCheckpointV1.create(
            checkpoint_kind=ProviderStageCheckpointKind.INPUT,
            chain_id=identity.chain_id,
            attempt_number=None,
            content_sha256=blob.content_sha256,
            size_bytes=blob.size_bytes,
            evidence_sha256=identity.authority_sha256,
        )
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM provider_stage_retry_chains WHERE logical_key_sha256 = ?",
                (identity.logical_key_sha256,),
            ).fetchone()
            if row is not None:
                chain = self._chain_from_row(connection, row)
                existing_checkpoint, existing_blob = self._input_checkpoint(
                    connection,
                    chain.chain_id,
                )
                if chain.identity != identity:
                    changed = (
                        "input"
                        if chain.identity.stage_input_sha256 != identity.stage_input_sha256
                        else "authority"
                    )
                    raise StateConflictError(f"provider-stage retry {changed} changed")
                if existing_checkpoint != checkpoint or existing_blob != blob:
                    raise StateConflictError("provider-stage protected input changed")
                return chain
            now = self.authority_store._now()
            identity_json = canonical_json(identity.to_payload())
            connection.execute(
                "INSERT INTO provider_stage_retry_chains("
                "chain_id, logical_key_sha256, provider, model_family, stage, "
                "request_occurrence_sha256, request_sha256, stage_input_sha256, "
                "authority_sha256, story_state_committed, identity_json, identity_sha256, "
                "phase, input_checkpoint_sha256, result_checkpoint_sha256, "
                "downstream_intent_sha256, downstream_evidence_sha256, block_reason, "
                "block_evidence_sha256, state_version, created_at, updated_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'input_frozen', ?, "
                "NULL, NULL, NULL, NULL, NULL, 1, ?, ?)",
                (
                    identity.chain_id,
                    identity.logical_key_sha256,
                    identity.provider.value,
                    identity.model_family.value,
                    identity.stage.value,
                    identity.request_occurrence_sha256,
                    identity.request_sha256,
                    identity.stage_input_sha256,
                    identity.authority_sha256,
                    int(identity.story_state_committed),
                    identity_json,
                    canonical_sha256(identity.to_payload()),
                    checkpoint.checkpoint_sha256,
                    now,
                    now,
                ),
            )
            self._insert_checkpoint(connection, checkpoint, blob)
            self._append_event(
                connection,
                identity.chain_id,
                "input_frozen",
                {
                    "identity_sha256": canonical_sha256(identity.to_payload()),
                    "checkpoint_sha256": checkpoint.checkpoint_sha256,
                },
            )
            return self._chain(connection, identity.chain_id)

    def read(self, chain_id: str) -> ProviderStageRetryChainV1:
        self._require_chain_id(chain_id)
        with self.authority_store._connect() as connection:
            return self._chain(connection, chain_id)

    def load_input(self, chain_id: str) -> bytes:
        self._require_chain_id(chain_id)
        with self.authority_store._connect() as connection:
            chain = self._chain(connection, chain_id)
            checkpoint, blob = self._input_checkpoint(connection, chain_id)
        exact_input = self.blob_store.load(blob)
        if (
            checkpoint.content_sha256 != chain.identity.stage_input_sha256
            or bytes_sha256(exact_input) != checkpoint.content_sha256
        ):
            raise StateConflictError("provider-stage protected input identity changed")
        return exact_input

    def prepare_attempt(
        self,
        chain_id: str,
        *,
        session_scope_sha256: str,
        ledger_prefix_before_sha256: str,
    ) -> ProviderStageRetryChainV1:
        self._require_sha(session_scope_sha256, "session scope")
        self._require_sha(ledger_prefix_before_sha256, "ledger prefix before")
        with self._transaction() as connection:
            row = self._chain_row(connection, chain_id)
            chain = self._chain_from_row(connection, row)
            if chain.phase is ProviderStageRetryPhase.ATTEMPT_PREPARED:
                current = chain.attempts[-1]
                if (
                    current.attempt_number == 1
                    and current.session_scope_sha256 == session_scope_sha256
                    and current.ledger_prefix_before_sha256 == ledger_prefix_before_sha256
                ):
                    return chain
                raise StateConflictError("provider-stage prepared attempt identity changed")
            if chain.phase is not ProviderStageRetryPhase.INPUT_FROZEN or chain.attempts:
                if chain.phase is ProviderStageRetryPhase.OWNER_RETIRED:
                    raise StateConflictError(
                        "provider-stage retry requires a backend-accepted manual Retry action"
                    )
                raise StateConflictError("provider-stage chain cannot prepare its initial attempt")
            attempt = self._prepared_attempt(
                attempt_number=1,
                session_scope_sha256=session_scope_sha256,
                ledger_prefix_before_sha256=ledger_prefix_before_sha256,
            )
            self._insert_attempt(
                connection,
                chain_id,
                attempt,
                retry_action_sha256=None,
            )
            updated = replace(
                chain,
                phase=ProviderStageRetryPhase.ATTEMPT_PREPARED,
                attempts=(attempt,),
            )
            return self._write_transition(connection, row, updated, "attempt_prepared")

    def accept_retry(
        self,
        chain_id: str,
        *,
        retry_action_sha256: str,
        session_scope_sha256: str,
        ledger_prefix_before_sha256: str,
    ) -> ProviderStageRetryChainV1:
        self._require_chain_id(chain_id)
        self._require_sha(retry_action_sha256, "Retry action")
        self._require_sha(session_scope_sha256, "session scope")
        self._require_sha(ledger_prefix_before_sha256, "ledger prefix before")
        with self._transaction() as connection:
            accepted = connection.execute(
                "SELECT * FROM provider_stage_retry_actions WHERE retry_action_sha256 = ?",
                (retry_action_sha256,),
            ).fetchone()
            if accepted is not None:
                if (
                    accepted["chain_id"] != chain_id
                    or accepted["session_scope_sha256"] != session_scope_sha256
                    or accepted["ledger_prefix_before_sha256"] != ledger_prefix_before_sha256
                ):
                    raise StateConflictError("provider-stage Retry action was reused or changed")
                return self._chain(connection, chain_id)
            row = self._chain_row(connection, chain_id)
            chain = self._chain_from_row(connection, row)
            if chain.phase is not ProviderStageRetryPhase.OWNER_RETIRED:
                raise StateConflictError("provider-stage chain is not eligible for Retry")
            prior = chain.attempts[-1]
            if prior.failure_class is ProviderStageFailureClass.DISPATCH_AMBIGUOUS:
                raise StateConflictError("ambiguous provider-stage dispatch cannot Retry")
            attempt_number = len(chain.attempts) + 1
            if attempt_number > MAXIMUM_PROVIDER_STAGE_ATTEMPTS:
                raise StateConflictError("provider-stage Retry attempt ceiling is exhausted")
            if prior.ledger_prefix_after_sha256 != ledger_prefix_before_sha256:
                raise StateConflictError("provider-stage retry ledger prefix changed")
            if session_scope_sha256 in {attempt.session_scope_sha256 for attempt in chain.attempts}:
                raise StateConflictError("provider-stage Retry reused a session scope")
            now = self.authority_store._now()
            connection.execute(
                "INSERT INTO provider_stage_retry_actions("
                "retry_action_sha256, chain_id, action_kind, prior_attempt_number, "
                "resulting_attempt_number, session_scope_sha256, "
                "ledger_prefix_before_sha256, created_at"
                ") VALUES (?, ?, 'provider_retry', ?, ?, ?, ?, ?)",
                (
                    retry_action_sha256,
                    chain_id,
                    prior.attempt_number,
                    attempt_number,
                    session_scope_sha256,
                    ledger_prefix_before_sha256,
                    now,
                ),
            )
            attempt = self._prepared_attempt(
                attempt_number=attempt_number,
                session_scope_sha256=session_scope_sha256,
                ledger_prefix_before_sha256=ledger_prefix_before_sha256,
            )
            self._insert_attempt(
                connection,
                chain_id,
                attempt,
                retry_action_sha256=retry_action_sha256,
            )
            updated = replace(
                chain,
                phase=ProviderStageRetryPhase.ATTEMPT_PREPARED,
                attempts=(*chain.attempts, attempt),
            )
            return self._write_transition(
                connection,
                row,
                updated,
                "retry_action_accepted",
                extra={"retry_action_sha256": retry_action_sha256},
            )

    def retry_actions_accepted(self, chain_id: str) -> int:
        self._require_chain_id(chain_id)
        with self.authority_store._connect() as connection:
            self._chain_row(connection, chain_id)
            return int(
                connection.execute(
                    "SELECT COUNT(*) FROM provider_stage_retry_actions WHERE chain_id = ?",
                    (chain_id,),
                ).fetchone()[0]
            )

    def mark_dispatch_started(
        self,
        chain_id: str,
        *,
        attempt_number: int,
        dispatch_evidence_sha256: str,
        maximum_provider_operations: int,
    ) -> ProviderStageRetryChainV1:
        self._require_sha(dispatch_evidence_sha256, "dispatch evidence")
        if type(maximum_provider_operations) is not int or maximum_provider_operations < 1:
            raise ContractValidationError(
                "provider-stage maximum provider operations must be positive"
            )
        with self._transaction() as connection:
            row = self._chain_row(connection, chain_id)
            chain = self._chain_from_row(connection, row)
            current = self._current_attempt(chain, attempt_number)
            if chain.phase is ProviderStageRetryPhase.DISPATCH_STARTED:
                if (
                    current.dispatch_evidence_sha256 == dispatch_evidence_sha256
                    and current.provider_operations_conservative == maximum_provider_operations
                ):
                    return chain
                raise StateConflictError("provider-stage dispatch reservation changed")
            if chain.phase is not ProviderStageRetryPhase.ATTEMPT_PREPARED:
                raise StateConflictError("provider-stage attempt is not prepared")
            updated_attempt = replace(
                current,
                phase=ProviderStageAttemptPhase.DISPATCH_STARTED,
                dispatch_evidence_sha256=dispatch_evidence_sha256,
                provider_operations_conservative=maximum_provider_operations,
            )
            self._update_attempt(connection, chain_id, updated_attempt)
            updated = replace(
                chain,
                phase=ProviderStageRetryPhase.DISPATCH_STARTED,
                attempts=(*chain.attempts[:-1], updated_attempt),
            )
            return self._write_transition(
                connection,
                row,
                updated,
                "invocation_reserved",
            )

    def mark_pretransport_failed(
        self,
        chain_id: str,
        *,
        attempt_number: int,
        failure_class: ProviderStageFailureClass,
        failure_evidence_sha256: str,
        ledger_prefix_after_sha256: str,
        duration_ms: int,
    ) -> ProviderStageRetryChainV1:
        if failure_class not in PRETRANSPORT_PROVIDER_STAGE_FAILURES:
            raise ContractValidationError(
                "provider-stage failure cannot occur before provider transport"
            )
        return self._mark_pretransport_failure(
            chain_id,
            attempt_number=attempt_number,
            failure_class=failure_class,
            failure_evidence_sha256=failure_evidence_sha256,
            ledger_prefix_after_sha256=ledger_prefix_after_sha256,
            duration_ms=duration_ms,
            event_kind="pretransport_failure_closed",
        )

    def mark_pretransport_non_retryable_failed(
        self,
        chain_id: str,
        *,
        attempt_number: int,
        failure_class: ProviderStageFailureClass,
        failure_evidence_sha256: str,
        ledger_prefix_after_sha256: str,
        duration_ms: int,
    ) -> ProviderStageRetryChainV1:
        if failure_class not in NON_RETRYABLE_PROVIDER_STAGE_FAILURES:
            raise ContractValidationError("pretransport failure is not a known non-Retry terminal")
        return self._mark_pretransport_failure(
            chain_id,
            attempt_number=attempt_number,
            failure_class=failure_class,
            failure_evidence_sha256=failure_evidence_sha256,
            ledger_prefix_after_sha256=ledger_prefix_after_sha256,
            duration_ms=duration_ms,
            event_kind="pretransport_non_retryable_failure_closed",
        )

    def _mark_pretransport_failure(
        self,
        chain_id: str,
        *,
        attempt_number: int,
        failure_class: ProviderStageFailureClass,
        failure_evidence_sha256: str,
        ledger_prefix_after_sha256: str,
        duration_ms: int,
        event_kind: str,
    ) -> ProviderStageRetryChainV1:
        self._require_sha(failure_evidence_sha256, "failure evidence")
        self._require_sha(ledger_prefix_after_sha256, "ledger prefix after")
        with self._transaction() as connection:
            row = self._chain_row(connection, chain_id)
            chain = self._chain_from_row(connection, row)
            current = self._current_attempt(chain, attempt_number)
            if current.phase in {
                ProviderStageAttemptPhase.FAILED,
                ProviderStageAttemptPhase.OWNER_RETIRED,
            }:
                self._require_failure_match(
                    current,
                    failure_class=failure_class,
                    failure_evidence_sha256=failure_evidence_sha256,
                    ledger_prefix_after_sha256=ledger_prefix_after_sha256,
                    provider_operations_observed=0,
                    provider_operations_conservative=0,
                    duration_ms=duration_ms,
                    input_tokens=None,
                    cached_input_tokens=None,
                    output_tokens=None,
                    reasoning_tokens=None,
                )
                return chain
            if chain.phase is not ProviderStageRetryPhase.ATTEMPT_PREPARED:
                raise StateConflictError("provider-stage pretransport failure has no attempt")
            failed = ProviderStageAttemptV1(
                schema_version=ProviderStageAttemptV1.SCHEMA_VERSION,
                attempt_number=current.attempt_number,
                phase=ProviderStageAttemptPhase.FAILED,
                session_scope_sha256=current.session_scope_sha256,
                ledger_prefix_before_sha256=current.ledger_prefix_before_sha256,
                dispatch_evidence_sha256=None,
                ledger_prefix_after_sha256=ledger_prefix_after_sha256,
                provider_operations_observed=0,
                provider_operations_conservative=0,
                duration_ms=duration_ms,
                input_tokens=None,
                cached_input_tokens=None,
                output_tokens=None,
                reasoning_tokens=None,
                failure_class=failure_class,
                failure_evidence_sha256=failure_evidence_sha256,
                owner_retirement_evidence_sha256=None,
                result_checkpoint_sha256=None,
            )
            self._update_attempt(connection, chain_id, failed)
            updated = replace(
                chain,
                phase=ProviderStageRetryPhase.AWAITING_OWNER_RETIREMENT,
                attempts=(*chain.attempts[:-1], failed),
            )
            return self._write_transition(
                connection,
                row,
                updated,
                event_kind,
            )

    def mark_attempt_failed(
        self,
        chain_id: str,
        *,
        attempt_number: int,
        failure_class: ProviderStageFailureClass,
        failure_evidence_sha256: str,
        ledger_prefix_after_sha256: str,
        provider_operations_observed: int,
        provider_operations_conservative: int,
        duration_ms: int,
        input_tokens: int | None = None,
        cached_input_tokens: int | None = None,
        output_tokens: int | None = None,
        reasoning_tokens: int | None = None,
    ) -> ProviderStageRetryChainV1:
        if failure_class not in RETRYABLE_PROVIDER_STAGE_FAILURES | {
            ProviderStageFailureClass.DISPATCH_AMBIGUOUS
        }:
            raise ContractValidationError("provider-stage failure is not Retry-eligible")
        return self._mark_submitted_failure(
            chain_id,
            attempt_number=attempt_number,
            failure_class=failure_class,
            failure_evidence_sha256=failure_evidence_sha256,
            ledger_prefix_after_sha256=ledger_prefix_after_sha256,
            provider_operations_observed=provider_operations_observed,
            provider_operations_conservative=provider_operations_conservative,
            duration_ms=duration_ms,
            input_tokens=input_tokens,
            cached_input_tokens=cached_input_tokens,
            output_tokens=output_tokens,
            reasoning_tokens=reasoning_tokens,
            event_kind="attempt_failure_closed",
        )

    def mark_non_retryable_failed(
        self,
        chain_id: str,
        *,
        attempt_number: int,
        failure_class: ProviderStageFailureClass,
        failure_evidence_sha256: str,
        ledger_prefix_after_sha256: str,
        provider_operations_observed: int,
        provider_operations_conservative: int,
        duration_ms: int,
        input_tokens: int | None = None,
        cached_input_tokens: int | None = None,
        output_tokens: int | None = None,
        reasoning_tokens: int | None = None,
    ) -> ProviderStageRetryChainV1:
        if failure_class not in NON_RETRYABLE_PROVIDER_STAGE_FAILURES:
            raise ContractValidationError(
                "provider-stage failure is not a known non-Retry terminal"
            )
        if provider_operations_conservative != provider_operations_observed:
            raise ContractValidationError(
                "non-Retry failure lacks closed provider-operation accounting"
            )
        return self._mark_submitted_failure(
            chain_id,
            attempt_number=attempt_number,
            failure_class=failure_class,
            failure_evidence_sha256=failure_evidence_sha256,
            ledger_prefix_after_sha256=ledger_prefix_after_sha256,
            provider_operations_observed=provider_operations_observed,
            provider_operations_conservative=provider_operations_conservative,
            duration_ms=duration_ms,
            input_tokens=input_tokens,
            cached_input_tokens=cached_input_tokens,
            output_tokens=output_tokens,
            reasoning_tokens=reasoning_tokens,
            event_kind="non_retryable_failure_closed",
        )

    def _mark_submitted_failure(
        self,
        chain_id: str,
        *,
        attempt_number: int,
        failure_class: ProviderStageFailureClass,
        failure_evidence_sha256: str,
        ledger_prefix_after_sha256: str,
        provider_operations_observed: int,
        provider_operations_conservative: int,
        duration_ms: int,
        input_tokens: int | None,
        cached_input_tokens: int | None,
        output_tokens: int | None,
        reasoning_tokens: int | None,
        event_kind: str,
    ) -> ProviderStageRetryChainV1:
        self._require_sha(failure_evidence_sha256, "failure evidence")
        self._require_sha(ledger_prefix_after_sha256, "ledger prefix after")
        with self._transaction() as connection:
            row = self._chain_row(connection, chain_id)
            chain = self._chain_from_row(connection, row)
            current = self._current_attempt(chain, attempt_number)
            if current.phase in {
                ProviderStageAttemptPhase.FAILED,
                ProviderStageAttemptPhase.OWNER_RETIRED,
            }:
                self._require_failure_match(
                    current,
                    failure_class=failure_class,
                    failure_evidence_sha256=failure_evidence_sha256,
                    ledger_prefix_after_sha256=ledger_prefix_after_sha256,
                    provider_operations_observed=provider_operations_observed,
                    provider_operations_conservative=provider_operations_conservative,
                    duration_ms=duration_ms,
                    input_tokens=input_tokens,
                    cached_input_tokens=cached_input_tokens,
                    output_tokens=output_tokens,
                    reasoning_tokens=reasoning_tokens,
                )
                return chain
            if chain.phase is not ProviderStageRetryPhase.DISPATCH_STARTED:
                raise StateConflictError("provider-stage failure has no invocation reservation")
            failed = ProviderStageAttemptV1(
                schema_version=ProviderStageAttemptV1.SCHEMA_VERSION,
                attempt_number=current.attempt_number,
                phase=ProviderStageAttemptPhase.FAILED,
                session_scope_sha256=current.session_scope_sha256,
                ledger_prefix_before_sha256=current.ledger_prefix_before_sha256,
                dispatch_evidence_sha256=current.dispatch_evidence_sha256,
                ledger_prefix_after_sha256=ledger_prefix_after_sha256,
                provider_operations_observed=provider_operations_observed,
                provider_operations_conservative=provider_operations_conservative,
                duration_ms=duration_ms,
                input_tokens=input_tokens,
                cached_input_tokens=cached_input_tokens,
                output_tokens=output_tokens,
                reasoning_tokens=reasoning_tokens,
                failure_class=failure_class,
                failure_evidence_sha256=failure_evidence_sha256,
                owner_retirement_evidence_sha256=None,
                result_checkpoint_sha256=None,
            )
            self._update_attempt(connection, chain_id, failed)
            updated = replace(
                chain,
                phase=ProviderStageRetryPhase.AWAITING_OWNER_RETIREMENT,
                attempts=(*chain.attempts[:-1], failed),
            )
            return self._write_transition(connection, row, updated, event_kind)

    def mark_owner_retired(
        self,
        chain_id: str,
        *,
        attempt_number: int,
        retirement_evidence_sha256: str,
    ) -> ProviderStageRetryChainV1:
        self._require_sha(retirement_evidence_sha256, "owner retirement evidence")
        with self._transaction() as connection:
            row = self._chain_row(connection, chain_id)
            chain = self._chain_from_row(connection, row)
            current = self._current_attempt(chain, attempt_number)
            if current.phase is ProviderStageAttemptPhase.OWNER_RETIRED:
                if current.owner_retirement_evidence_sha256 == retirement_evidence_sha256:
                    return chain
                raise StateConflictError("provider-stage owner-retirement evidence changed")
            if chain.phase is not ProviderStageRetryPhase.AWAITING_OWNER_RETIREMENT:
                raise StateConflictError("provider-stage owner is not awaiting retirement")
            retired = replace(
                current,
                phase=ProviderStageAttemptPhase.OWNER_RETIRED,
                owner_retirement_evidence_sha256=retirement_evidence_sha256,
            )
            self._update_attempt(connection, chain_id, retired)
            if current.failure_class is ProviderStageFailureClass.DISPATCH_AMBIGUOUS:
                phase = ProviderStageRetryPhase.BLOCKED_AMBIGUOUS
                block_reason = ProviderStageBlockReason.DISPATCH_CUSTODY_AMBIGUOUS
                block_evidence = domain_sha256(
                    "cera.provider_stage_retry_dispatch_ambiguity_closed.v1",
                    {
                        "failure_evidence_sha256": current.failure_evidence_sha256,
                        "retirement_evidence_sha256": retirement_evidence_sha256,
                    },
                )
            elif current.failure_class in NON_RETRYABLE_PROVIDER_STAGE_FAILURES:
                phase = ProviderStageRetryPhase.RECOVERY_REQUIRED
                block_reason = None
                block_evidence = None
            elif attempt_number == MAXIMUM_PROVIDER_STAGE_ATTEMPTS:
                phase = (
                    ProviderStageRetryPhase.RECORDING_REPAIR_REQUIRED
                    if chain.identity.stage is ProviderStage.RECORDER
                    else ProviderStageRetryPhase.EXHAUSTED
                )
                block_reason = None
                block_evidence = None
            else:
                phase = ProviderStageRetryPhase.OWNER_RETIRED
                block_reason = None
                block_evidence = None
            updated = replace(
                chain,
                phase=phase,
                attempts=(*chain.attempts[:-1], retired),
                block_reason=block_reason,
                block_evidence_sha256=block_evidence,
            )
            return self._write_transition(connection, row, updated, "owner_retired")

    def freeze_result(
        self,
        chain_id: str,
        *,
        attempt_number: int,
        exact_result: bytes,
        result_evidence_sha256: str,
        ledger_prefix_after_sha256: str,
        provider_operations_observed: int,
        provider_operations_conservative: int,
        duration_ms: int,
        input_tokens: int | None = None,
        cached_input_tokens: int | None = None,
        output_tokens: int | None = None,
        reasoning_tokens: int | None = None,
    ) -> ProviderStageRetryChainV1:
        return self._freeze_result(
            chain_id,
            attempt_number=attempt_number,
            exact_result=exact_result,
            result_evidence_sha256=result_evidence_sha256,
            ledger_prefix_after_sha256=ledger_prefix_after_sha256,
            provider_operations_observed=provider_operations_observed,
            provider_operations_conservative=provider_operations_conservative,
            duration_ms=duration_ms,
            input_tokens=input_tokens,
            cached_input_tokens=cached_input_tokens,
            output_tokens=output_tokens,
            reasoning_tokens=reasoning_tokens,
            blocked_resolution_evidence_sha256=None,
        )

    def freeze_downstream_intent(
        self,
        chain_id: str,
        *,
        downstream_intent_sha256: str,
    ) -> ProviderStageRetryChainV1:
        self._require_sha(downstream_intent_sha256, "downstream intent")
        with self._transaction() as connection:
            row = self._chain_row(connection, chain_id)
            chain = self._chain_from_row(connection, row)
            if chain.phase in {
                ProviderStageRetryPhase.DOWNSTREAM_INTENT_FROZEN,
                ProviderStageRetryPhase.DOWNSTREAM_BOUND,
                ProviderStageRetryPhase.SUCCEEDED,
            }:
                if chain.downstream_intent_sha256 == downstream_intent_sha256:
                    return chain
                raise StateConflictError("provider-stage downstream intent changed")
            if chain.phase is not ProviderStageRetryPhase.RESULT_FROZEN:
                raise StateConflictError("provider-stage result is not ready for downstream")
            updated = replace(
                chain,
                phase=ProviderStageRetryPhase.DOWNSTREAM_INTENT_FROZEN,
                downstream_intent_sha256=downstream_intent_sha256,
            )
            return self._write_transition(connection, row, updated, "downstream_intent_frozen")

    def bind_downstream(
        self,
        chain_id: str,
        *,
        downstream_evidence_sha256: str,
    ) -> ProviderStageRetryChainV1:
        self._require_sha(downstream_evidence_sha256, "downstream evidence")
        with self._transaction() as connection:
            row = self._chain_row(connection, chain_id)
            chain = self._chain_from_row(connection, row)
            if chain.phase in {
                ProviderStageRetryPhase.DOWNSTREAM_BOUND,
                ProviderStageRetryPhase.SUCCEEDED,
            }:
                if chain.downstream_evidence_sha256 == downstream_evidence_sha256:
                    return chain
                raise StateConflictError("provider-stage downstream completion changed")
            if chain.phase is not ProviderStageRetryPhase.DOWNSTREAM_INTENT_FROZEN:
                raise StateConflictError("provider-stage downstream intent is unavailable")
            updated = replace(
                chain,
                phase=ProviderStageRetryPhase.DOWNSTREAM_BOUND,
                downstream_evidence_sha256=downstream_evidence_sha256,
            )
            return self._write_transition(connection, row, updated, "downstream_bound")

    def mark_succeeded(self, chain_id: str) -> ProviderStageRetryChainV1:
        with self._transaction() as connection:
            row = self._chain_row(connection, chain_id)
            chain = self._chain_from_row(connection, row)
            if chain.phase is ProviderStageRetryPhase.SUCCEEDED:
                return chain
            if chain.phase is not ProviderStageRetryPhase.DOWNSTREAM_BOUND:
                raise StateConflictError("provider-stage downstream completion is not bound")
            return self._write_transition(
                connection,
                row,
                replace(chain, phase=ProviderStageRetryPhase.SUCCEEDED),
                "succeeded",
            )

    def block_ambiguous(
        self,
        chain_id: str,
        *,
        reason: ProviderStageBlockReason,
        evidence_sha256: str,
    ) -> ProviderStageRetryChainV1:
        if type(reason) is not ProviderStageBlockReason:
            raise ContractValidationError("provider-stage block reason is not closed")
        self._require_sha(evidence_sha256, "block evidence")
        with self._transaction() as connection:
            row = self._chain_row(connection, chain_id)
            chain = self._chain_from_row(connection, row)
            target_phase = (
                ProviderStageRetryPhase.BLOCKED_AMBIGUOUS
                if reason is ProviderStageBlockReason.DISPATCH_CUSTODY_AMBIGUOUS
                else ProviderStageRetryPhase.RECOVERY_REQUIRED
            )
            if chain.phase is target_phase:
                if chain.block_reason is reason and chain.block_evidence_sha256 == evidence_sha256:
                    return chain
                raise StateConflictError("provider-stage block evidence changed")
            if chain.phase in {
                ProviderStageRetryPhase.DISPATCH_STARTED,
                ProviderStageRetryPhase.DOWNSTREAM_INTENT_FROZEN,
                ProviderStageRetryPhase.DOWNSTREAM_BOUND,
                ProviderStageRetryPhase.SUCCEEDED,
                ProviderStageRetryPhase.EXHAUSTED,
                ProviderStageRetryPhase.BLOCKED_AMBIGUOUS,
                ProviderStageRetryPhase.RECORDING_REPAIR_REQUIRED,
                ProviderStageRetryPhase.RECOVERY_REQUIRED,
            }:
                raise StateConflictError("provider-stage boundary cannot become an ambiguous block")
            updated = replace(
                chain,
                phase=target_phase,
                block_reason=reason,
                block_evidence_sha256=evidence_sha256,
            )
            return self._write_transition(
                connection,
                row,
                updated,
                (
                    "blocked_ambiguous"
                    if target_phase is ProviderStageRetryPhase.BLOCKED_AMBIGUOUS
                    else "recovery_required"
                ),
            )

    def resolve_blocked_failure(
        self,
        chain_id: str,
        *,
        resolution_evidence_sha256: str,
        failure_class: ProviderStageFailureClass,
        failure_evidence_sha256: str,
        ledger_prefix_after_sha256: str,
        provider_operations_observed: int,
        provider_operations_conservative: int,
        duration_ms: int,
        input_tokens: int | None = None,
        cached_input_tokens: int | None = None,
        output_tokens: int | None = None,
        reasoning_tokens: int | None = None,
    ) -> ProviderStageRetryChainV1:
        self._require_sha(resolution_evidence_sha256, "blocked resolution evidence")
        self._require_sha(failure_evidence_sha256, "failure evidence")
        self._require_sha(ledger_prefix_after_sha256, "ledger prefix after")
        if failure_class not in RETRYABLE_PROVIDER_STAGE_FAILURES:
            raise ContractValidationError("blocked failure resolution is not Retry-eligible")
        with self._transaction() as connection:
            row = self._chain_row(connection, chain_id)
            chain = self._require_dispatch_block(self._chain_from_row(connection, row))
            current = chain.attempts[-1]
            assert current.owner_retirement_evidence_sha256 is not None
            resolved = ProviderStageAttemptV1(
                schema_version=ProviderStageAttemptV1.SCHEMA_VERSION,
                attempt_number=current.attempt_number,
                phase=ProviderStageAttemptPhase.OWNER_RETIRED,
                session_scope_sha256=current.session_scope_sha256,
                ledger_prefix_before_sha256=current.ledger_prefix_before_sha256,
                dispatch_evidence_sha256=current.dispatch_evidence_sha256,
                ledger_prefix_after_sha256=ledger_prefix_after_sha256,
                provider_operations_observed=provider_operations_observed,
                provider_operations_conservative=provider_operations_conservative,
                duration_ms=duration_ms,
                input_tokens=input_tokens,
                cached_input_tokens=cached_input_tokens,
                output_tokens=output_tokens,
                reasoning_tokens=reasoning_tokens,
                failure_class=failure_class,
                failure_evidence_sha256=failure_evidence_sha256,
                owner_retirement_evidence_sha256=(current.owner_retirement_evidence_sha256),
                result_checkpoint_sha256=None,
            )
            self._update_attempt(connection, chain_id, resolved)
            if resolved.attempt_number == MAXIMUM_PROVIDER_STAGE_ATTEMPTS:
                phase = (
                    ProviderStageRetryPhase.RECORDING_REPAIR_REQUIRED
                    if chain.identity.stage is ProviderStage.RECORDER
                    else ProviderStageRetryPhase.EXHAUSTED
                )
            else:
                phase = ProviderStageRetryPhase.OWNER_RETIRED
            updated = replace(
                chain,
                phase=phase,
                attempts=(*chain.attempts[:-1], resolved),
                block_reason=None,
                block_evidence_sha256=None,
            )
            return self._write_transition(
                connection,
                row,
                updated,
                "blocked_resolved_failure",
                extra={"resolution_evidence_sha256": resolution_evidence_sha256},
            )

    def resolve_blocked_non_retryable_failure(
        self,
        chain_id: str,
        *,
        resolution_evidence_sha256: str,
        failure_class: ProviderStageFailureClass,
        failure_evidence_sha256: str,
        ledger_prefix_after_sha256: str,
        provider_operations_observed: int,
        provider_operations_conservative: int,
        duration_ms: int,
        input_tokens: int | None = None,
        cached_input_tokens: int | None = None,
        output_tokens: int | None = None,
        reasoning_tokens: int | None = None,
    ) -> ProviderStageRetryChainV1:
        self._require_sha(resolution_evidence_sha256, "blocked resolution evidence")
        self._require_sha(failure_evidence_sha256, "failure evidence")
        self._require_sha(ledger_prefix_after_sha256, "ledger prefix after")
        if failure_class not in NON_RETRYABLE_PROVIDER_STAGE_FAILURES:
            raise ContractValidationError("blocked resolution is not a known non-Retry failure")
        if provider_operations_conservative != provider_operations_observed:
            raise ContractValidationError(
                "non-Retry resolution lacks closed provider-operation accounting"
            )
        with self._transaction() as connection:
            row = self._chain_row(connection, chain_id)
            chain = self._require_dispatch_block(self._chain_from_row(connection, row))
            current = chain.attempts[-1]
            assert current.owner_retirement_evidence_sha256 is not None
            resolved = ProviderStageAttemptV1(
                schema_version=ProviderStageAttemptV1.SCHEMA_VERSION,
                attempt_number=current.attempt_number,
                phase=ProviderStageAttemptPhase.OWNER_RETIRED,
                session_scope_sha256=current.session_scope_sha256,
                ledger_prefix_before_sha256=current.ledger_prefix_before_sha256,
                dispatch_evidence_sha256=current.dispatch_evidence_sha256,
                ledger_prefix_after_sha256=ledger_prefix_after_sha256,
                provider_operations_observed=provider_operations_observed,
                provider_operations_conservative=provider_operations_conservative,
                duration_ms=duration_ms,
                input_tokens=input_tokens,
                cached_input_tokens=cached_input_tokens,
                output_tokens=output_tokens,
                reasoning_tokens=reasoning_tokens,
                failure_class=failure_class,
                failure_evidence_sha256=failure_evidence_sha256,
                owner_retirement_evidence_sha256=current.owner_retirement_evidence_sha256,
                result_checkpoint_sha256=None,
            )
            self._update_attempt(connection, chain_id, resolved)
            updated = replace(
                chain,
                phase=ProviderStageRetryPhase.RECOVERY_REQUIRED,
                attempts=(*chain.attempts[:-1], resolved),
                block_reason=None,
                block_evidence_sha256=None,
            )
            return self._write_transition(
                connection,
                row,
                updated,
                "blocked_resolved_non_retryable_failure",
                extra={"resolution_evidence_sha256": resolution_evidence_sha256},
            )

    def resolve_blocked_result(
        self,
        chain_id: str,
        *,
        resolution_evidence_sha256: str,
        exact_result: bytes,
        result_evidence_sha256: str,
        ledger_prefix_after_sha256: str,
        provider_operations_observed: int,
        provider_operations_conservative: int,
        duration_ms: int,
        input_tokens: int | None = None,
        cached_input_tokens: int | None = None,
        output_tokens: int | None = None,
        reasoning_tokens: int | None = None,
    ) -> ProviderStageRetryChainV1:
        self._require_sha(resolution_evidence_sha256, "blocked resolution evidence")
        chain = self.read(chain_id)
        blocked = self._require_dispatch_block(chain)
        return self._freeze_result(
            chain_id,
            attempt_number=blocked.attempts[-1].attempt_number,
            exact_result=exact_result,
            result_evidence_sha256=result_evidence_sha256,
            ledger_prefix_after_sha256=ledger_prefix_after_sha256,
            provider_operations_observed=provider_operations_observed,
            provider_operations_conservative=provider_operations_conservative,
            duration_ms=duration_ms,
            input_tokens=input_tokens,
            cached_input_tokens=cached_input_tokens,
            output_tokens=output_tokens,
            reasoning_tokens=reasoning_tokens,
            blocked_resolution_evidence_sha256=resolution_evidence_sha256,
        )

    def resolve_blocked_recording_repair(
        self,
        chain_id: str,
        *,
        resolution_evidence_sha256: str,
        failure_class: ProviderStageFailureClass,
        failure_evidence_sha256: str,
        ledger_prefix_after_sha256: str,
        provider_operations_observed: int,
        provider_operations_conservative: int,
        duration_ms: int,
        input_tokens: int | None = None,
        cached_input_tokens: int | None = None,
        output_tokens: int | None = None,
        reasoning_tokens: int | None = None,
    ) -> ProviderStageRetryChainV1:
        chain = self._require_dispatch_block(self.read(chain_id))
        if (
            chain.identity.stage is not ProviderStage.RECORDER
            or chain.attempts[-1].attempt_number != MAXIMUM_PROVIDER_STAGE_ATTEMPTS
        ):
            raise StateConflictError("recording repair requires Recorder's third fenced attempt")
        repaired = self.resolve_blocked_failure(
            chain_id,
            resolution_evidence_sha256=resolution_evidence_sha256,
            failure_class=failure_class,
            failure_evidence_sha256=failure_evidence_sha256,
            ledger_prefix_after_sha256=ledger_prefix_after_sha256,
            provider_operations_observed=provider_operations_observed,
            provider_operations_conservative=provider_operations_conservative,
            duration_ms=duration_ms,
            input_tokens=input_tokens,
            cached_input_tokens=cached_input_tokens,
            output_tokens=output_tokens,
            reasoning_tokens=reasoning_tokens,
        )
        if repaired.phase is not ProviderStageRetryPhase.RECORDING_REPAIR_REQUIRED:
            raise TransactionError("Recorder repair resolution lost its terminal seam")
        return repaired

    def load_result(self, chain_id: str) -> bytes:
        self._require_chain_id(chain_id)
        with self.authority_store._connect() as connection:
            chain = self._chain(connection, chain_id)
            if chain.result_checkpoint is None:
                raise StateConflictError("provider-stage result checkpoint is unavailable")
            checkpoint, blob = self._checkpoint(
                connection,
                chain.result_checkpoint.checkpoint_sha256,
            )
        exact_result = self.blob_store.load(blob)
        if checkpoint != chain.result_checkpoint:
            raise StateConflictError("provider-stage result checkpoint changed")
        return exact_result

    def terminal(self, chain_id: str) -> ProviderStageRetryTerminalV1 | None:
        chain = self.read(chain_id)
        if chain.phase is ProviderStageRetryPhase.EXHAUSTED:
            return self._exhausted_terminal(chain)
        if chain.phase is ProviderStageRetryPhase.BLOCKED_AMBIGUOUS:
            return self._blocked_terminal(chain)
        if chain.phase is ProviderStageRetryPhase.RECOVERY_REQUIRED:
            return self._recovery_required_terminal(chain)
        return None

    def _freeze_result(
        self,
        chain_id: str,
        *,
        attempt_number: int,
        exact_result: bytes,
        result_evidence_sha256: str,
        ledger_prefix_after_sha256: str,
        provider_operations_observed: int,
        provider_operations_conservative: int,
        duration_ms: int,
        input_tokens: int | None,
        cached_input_tokens: int | None,
        output_tokens: int | None,
        reasoning_tokens: int | None,
        blocked_resolution_evidence_sha256: str | None,
    ) -> ProviderStageRetryChainV1:
        self._require_sha(result_evidence_sha256, "result evidence")
        self._require_sha(ledger_prefix_after_sha256, "ledger prefix after")
        if not isinstance(exact_result, bytes) or not exact_result:
            raise ContractValidationError("provider-stage exact result must be non-empty bytes")
        blob = self.blob_store.freeze(
            chain_id=chain_id,
            checkpoint_kind=ProviderStageCheckpointKind.RESULT,
            attempt_number=attempt_number,
            exact_bytes=exact_result,
        )
        checkpoint = ProviderStageProtectedCheckpointV1.create(
            checkpoint_kind=ProviderStageCheckpointKind.RESULT,
            chain_id=chain_id,
            attempt_number=attempt_number,
            content_sha256=blob.content_sha256,
            size_bytes=blob.size_bytes,
            evidence_sha256=result_evidence_sha256,
        )
        self._after_result_blob_frozen(blob)
        with self._transaction() as connection:
            row = self._chain_row(connection, chain_id)
            chain = self._chain_from_row(connection, row)
            current = self._current_attempt(chain, attempt_number)
            result_phases = {
                ProviderStageRetryPhase.RESULT_FROZEN,
                ProviderStageRetryPhase.DOWNSTREAM_INTENT_FROZEN,
                ProviderStageRetryPhase.DOWNSTREAM_BOUND,
                ProviderStageRetryPhase.SUCCEEDED,
            }
            if chain.phase in result_phases:
                if chain.result_checkpoint != checkpoint:
                    raise StateConflictError("provider-stage result changed on replay")
                self._require_result_match(
                    current,
                    checkpoint=checkpoint,
                    ledger_prefix_after_sha256=ledger_prefix_after_sha256,
                    provider_operations_observed=provider_operations_observed,
                    provider_operations_conservative=provider_operations_conservative,
                    duration_ms=duration_ms,
                    input_tokens=input_tokens,
                    cached_input_tokens=cached_input_tokens,
                    output_tokens=output_tokens,
                    reasoning_tokens=reasoning_tokens,
                )
                return chain
            if blocked_resolution_evidence_sha256 is None:
                if chain.phase is not ProviderStageRetryPhase.DISPATCH_STARTED:
                    raise StateConflictError("provider-stage result has no active dispatch")
                owner_retirement = None
            else:
                chain = self._require_dispatch_block(chain)
                current = self._current_attempt(chain, attempt_number)
                owner_retirement = current.owner_retirement_evidence_sha256
                assert owner_retirement is not None
            result_attempt = ProviderStageAttemptV1(
                schema_version=ProviderStageAttemptV1.SCHEMA_VERSION,
                attempt_number=current.attempt_number,
                phase=ProviderStageAttemptPhase.RESULT_FROZEN,
                session_scope_sha256=current.session_scope_sha256,
                ledger_prefix_before_sha256=current.ledger_prefix_before_sha256,
                dispatch_evidence_sha256=current.dispatch_evidence_sha256,
                ledger_prefix_after_sha256=ledger_prefix_after_sha256,
                provider_operations_observed=provider_operations_observed,
                provider_operations_conservative=provider_operations_conservative,
                duration_ms=duration_ms,
                input_tokens=input_tokens,
                cached_input_tokens=cached_input_tokens,
                output_tokens=output_tokens,
                reasoning_tokens=reasoning_tokens,
                failure_class=None,
                failure_evidence_sha256=None,
                owner_retirement_evidence_sha256=None,
                result_checkpoint_sha256=checkpoint.checkpoint_sha256,
            )
            self._insert_checkpoint(connection, checkpoint, blob)
            self._update_attempt(connection, chain_id, result_attempt)
            updated = replace(
                chain,
                phase=ProviderStageRetryPhase.RESULT_FROZEN,
                attempts=(*chain.attempts[:-1], result_attempt),
                result_checkpoint=checkpoint,
                block_reason=None,
                block_evidence_sha256=None,
            )
            extra: dict[str, Any] = {"checkpoint_sha256": checkpoint.checkpoint_sha256}
            if blocked_resolution_evidence_sha256 is not None:
                extra["resolution_evidence_sha256"] = blocked_resolution_evidence_sha256
                extra["retired_owner_evidence_sha256"] = owner_retirement
            return self._write_transition(
                connection,
                row,
                updated,
                (
                    "blocked_resolved_result"
                    if blocked_resolution_evidence_sha256 is not None
                    else "result_frozen"
                ),
                extra=extra,
            )

    def _after_result_blob_frozen(self, receipt: ProtectedStageBlobReceiptV1) -> None:
        """Test seam for a crash after durable blob freeze and before SQLite bind."""

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self.authority_store._connect() as connection:
            self.authority_store._begin(connection)
            try:
                yield connection
                connection.commit()
            except (ContractValidationError, StateConflictError, TransactionError):
                connection.rollback()
                raise
            except sqlite3.Error as exc:
                connection.rollback()
                raise TransactionError("provider-stage Retry transaction failed") from exc
            except BaseException:
                connection.rollback()
                raise

    def _chain(self, connection: sqlite3.Connection, chain_id: str) -> ProviderStageRetryChainV1:
        return self._chain_from_row(connection, self._chain_row(connection, chain_id))

    def _chain_row(self, connection: sqlite3.Connection, chain_id: str) -> sqlite3.Row:
        self._require_chain_id(chain_id)
        row: sqlite3.Row | None = connection.execute(
            "SELECT * FROM provider_stage_retry_chains WHERE chain_id = ?",
            (chain_id,),
        ).fetchone()
        if row is None:
            raise StateConflictError("provider-stage Retry chain does not exist")
        return row

    def _chain_from_row(
        self,
        connection: sqlite3.Connection,
        row: sqlite3.Row,
    ) -> ProviderStageRetryChainV1:
        try:
            identity_payload = json.loads(row["identity_json"])
            identity = provider_stage_retry_identity_from_payload(identity_payload)
        except (json.JSONDecodeError, ContractValidationError) as exc:
            raise TransactionError("provider-stage Retry identity is unreadable") from exc
        if (
            row["chain_id"] != identity.chain_id
            or row["logical_key_sha256"] != identity.logical_key_sha256
            or row["identity_sha256"] != canonical_sha256(identity.to_payload())
            or row["provider"] != identity.provider.value
            or row["model_family"] != identity.model_family.value
            or row["stage"] != identity.stage.value
            or row["request_occurrence_sha256"] != identity.request_occurrence_sha256
            or row["request_sha256"] != identity.request_sha256
            or row["stage_input_sha256"] != identity.stage_input_sha256
            or row["authority_sha256"] != identity.authority_sha256
            or bool(row["story_state_committed"]) is not identity.story_state_committed
        ):
            raise TransactionError("provider-stage Retry identity columns changed")
        attempt_rows = connection.execute(
            "SELECT * FROM provider_stage_retry_attempts WHERE chain_id = ? "
            "ORDER BY attempt_number",
            (identity.chain_id,),
        ).fetchall()
        attempts = tuple(self._attempt_from_row(value) for value in attempt_rows)
        action_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM provider_stage_retry_actions WHERE chain_id = ?",
                (identity.chain_id,),
            ).fetchone()[0]
        )
        if action_count != max(0, len(attempts) - 1):
            raise TransactionError("provider-stage Retry action accounting changed")
        result_checkpoint = None
        if row["result_checkpoint_sha256"] is not None:
            result_checkpoint, _ = self._checkpoint(
                connection,
                row["result_checkpoint_sha256"],
            )
        try:
            phase = ProviderStageRetryPhase(row["phase"])
            block_reason = (
                None
                if row["block_reason"] is None
                else ProviderStageBlockReason(row["block_reason"])
            )
            chain = ProviderStageRetryChainV1(
                schema_version=ProviderStageRetryChainV1.SCHEMA_VERSION,
                identity=identity,
                phase=phase,
                attempts=attempts,
                result_checkpoint=result_checkpoint,
                downstream_intent_sha256=row["downstream_intent_sha256"],
                downstream_evidence_sha256=row["downstream_evidence_sha256"],
                block_reason=block_reason,
                block_evidence_sha256=row["block_evidence_sha256"],
            )
        except (ValueError, ContractValidationError) as exc:
            raise TransactionError("provider-stage Retry state is invalid") from exc
        input_checkpoint, _ = self._input_checkpoint(connection, identity.chain_id)
        if (
            row["input_checkpoint_sha256"] != input_checkpoint.checkpoint_sha256
            or input_checkpoint.content_sha256 != identity.stage_input_sha256
        ):
            raise TransactionError("provider-stage Retry input checkpoint changed")
        return chain

    def _input_checkpoint(
        self,
        connection: sqlite3.Connection,
        chain_id: str,
    ) -> tuple[ProviderStageProtectedCheckpointV1, ProtectedStageBlobReceiptV1]:
        row = connection.execute(
            "SELECT * FROM provider_stage_retry_checkpoints "
            "WHERE chain_id = ? AND checkpoint_kind = 'input'",
            (chain_id,),
        ).fetchone()
        if row is None:
            raise TransactionError("provider-stage input checkpoint is missing")
        return self._checkpoint_from_row(row)

    def _checkpoint(
        self,
        connection: sqlite3.Connection,
        checkpoint_sha256: str,
    ) -> tuple[ProviderStageProtectedCheckpointV1, ProtectedStageBlobReceiptV1]:
        row = connection.execute(
            "SELECT * FROM provider_stage_retry_checkpoints WHERE checkpoint_sha256 = ?",
            (checkpoint_sha256,),
        ).fetchone()
        if row is None:
            raise TransactionError("provider-stage checkpoint is missing")
        return self._checkpoint_from_row(row)

    @staticmethod
    def _checkpoint_from_row(
        row: sqlite3.Row,
    ) -> tuple[ProviderStageProtectedCheckpointV1, ProtectedStageBlobReceiptV1]:
        try:
            checkpoint = provider_stage_checkpoint_from_payload(json.loads(row["checkpoint_json"]))
            kind = ProviderStageCheckpointKind(row["checkpoint_kind"])
            blob_body = {
                "schema_version": ProtectedStageBlobReceiptV1.SCHEMA_VERSION,
                "blob_id_sha256": row["blob_id_sha256"],
                "chain_id": row["chain_id"],
                "checkpoint_kind": kind.value,
                "attempt_number": row["attempt_number"],
                "content_sha256": row["content_sha256"],
                "size_bytes": int(row["size_bytes"]),
            }
            blob = ProtectedStageBlobReceiptV1(
                schema_version=ProtectedStageBlobReceiptV1.SCHEMA_VERSION,
                blob_id_sha256=row["blob_id_sha256"],
                chain_id=row["chain_id"],
                checkpoint_kind=kind,
                attempt_number=row["attempt_number"],
                content_sha256=row["content_sha256"],
                size_bytes=int(row["size_bytes"]),
                custody_sha256=row["blob_custody_sha256"],
            )
        except (json.JSONDecodeError, ValueError, ContractValidationError) as exc:
            raise TransactionError("provider-stage checkpoint is unreadable") from exc
        if (
            row["checkpoint_sha256"] != checkpoint.checkpoint_sha256
            or checkpoint.chain_id != row["chain_id"]
            or checkpoint.checkpoint_kind is not kind
            or checkpoint.attempt_number != row["attempt_number"]
            or checkpoint.content_sha256 != row["content_sha256"]
            or checkpoint.size_bytes != int(row["size_bytes"])
            or checkpoint.evidence_sha256 != row["evidence_sha256"]
            or blob.custody_sha256 != canonical_sha256(blob_body)
        ):
            raise TransactionError("provider-stage checkpoint columns changed")
        return checkpoint, blob

    def _insert_checkpoint(
        self,
        connection: sqlite3.Connection,
        checkpoint: ProviderStageProtectedCheckpointV1,
        blob: ProtectedStageBlobReceiptV1,
    ) -> None:
        if (
            checkpoint.chain_id != blob.chain_id
            or checkpoint.checkpoint_kind is not blob.checkpoint_kind
            or checkpoint.attempt_number != blob.attempt_number
            or checkpoint.content_sha256 != blob.content_sha256
            or checkpoint.size_bytes != blob.size_bytes
        ):
            raise ContractValidationError("provider-stage checkpoint blob binding changed")
        connection.execute(
            "INSERT INTO provider_stage_retry_checkpoints("
            "checkpoint_sha256, chain_id, checkpoint_kind, attempt_number, "
            "blob_id_sha256, blob_custody_sha256, content_sha256, size_bytes, "
            "evidence_sha256, checkpoint_json, created_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                checkpoint.checkpoint_sha256,
                checkpoint.chain_id,
                checkpoint.checkpoint_kind.value,
                checkpoint.attempt_number,
                blob.blob_id_sha256,
                blob.custody_sha256,
                checkpoint.content_sha256,
                checkpoint.size_bytes,
                checkpoint.evidence_sha256,
                canonical_json(checkpoint.to_payload()),
                self.authority_store._now(),
            ),
        )

    def _attempt_from_row(self, row: sqlite3.Row) -> ProviderStageAttemptV1:
        try:
            payload = json.loads(row["attempt_json"])
            attempt = from_mapping(ProviderStageAttemptV1, payload)
            assert isinstance(attempt, ProviderStageAttemptV1)
        except (json.JSONDecodeError, ContractValidationError) as exc:
            raise TransactionError("provider-stage attempt is unreadable") from exc
        if (
            row["attempt_sha256"] != canonical_sha256(attempt.to_payload())
            or int(row["attempt_number"]) != attempt.attempt_number
            or row["phase"] != attempt.phase.value
            or row["session_scope_sha256"] != attempt.session_scope_sha256
            or row["ledger_prefix_before_sha256"] != attempt.ledger_prefix_before_sha256
            or row["dispatch_evidence_sha256"] != attempt.dispatch_evidence_sha256
            or row["ledger_prefix_after_sha256"] != attempt.ledger_prefix_after_sha256
            or int(row["provider_operations_observed"]) != attempt.provider_operations_observed
            or int(row["provider_operations_conservative"])
            != attempt.provider_operations_conservative
            or row["duration_ms"] != attempt.duration_ms
            or row["input_tokens"] != attempt.input_tokens
            or row["cached_input_tokens"] != attempt.cached_input_tokens
            or row["output_tokens"] != attempt.output_tokens
            or row["reasoning_tokens"] != attempt.reasoning_tokens
            or row["failure_class"]
            != (None if attempt.failure_class is None else attempt.failure_class.value)
            or row["failure_evidence_sha256"] != attempt.failure_evidence_sha256
            or row["owner_retirement_evidence_sha256"] != attempt.owner_retirement_evidence_sha256
            or row["result_checkpoint_sha256"] != attempt.result_checkpoint_sha256
            or bool(row["invocation_reserved"])
            is not (attempt.dispatch_evidence_sha256 is not None)
        ):
            raise TransactionError("provider-stage attempt columns changed")
        return attempt

    def _insert_attempt(
        self,
        connection: sqlite3.Connection,
        chain_id: str,
        attempt: ProviderStageAttemptV1,
        *,
        retry_action_sha256: str | None,
    ) -> None:
        now = self.authority_store._now()
        payload = attempt.to_payload()
        connection.execute(
            "INSERT INTO provider_stage_retry_attempts("
            "chain_id, attempt_number, retry_action_sha256, phase, "
            "session_scope_sha256, ledger_prefix_before_sha256, invocation_reserved, "
            "dispatch_evidence_sha256, ledger_prefix_after_sha256, "
            "provider_operations_observed, provider_operations_conservative, duration_ms, "
            "input_tokens, cached_input_tokens, output_tokens, reasoning_tokens, "
            "failure_class, failure_evidence_sha256, owner_retirement_evidence_sha256, "
            "result_checkpoint_sha256, attempt_json, attempt_sha256, created_at, updated_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                chain_id,
                attempt.attempt_number,
                retry_action_sha256,
                attempt.phase.value,
                attempt.session_scope_sha256,
                attempt.ledger_prefix_before_sha256,
                int(attempt.dispatch_evidence_sha256 is not None),
                attempt.dispatch_evidence_sha256,
                attempt.ledger_prefix_after_sha256,
                attempt.provider_operations_observed,
                attempt.provider_operations_conservative,
                attempt.duration_ms,
                attempt.input_tokens,
                attempt.cached_input_tokens,
                attempt.output_tokens,
                attempt.reasoning_tokens,
                None if attempt.failure_class is None else attempt.failure_class.value,
                attempt.failure_evidence_sha256,
                attempt.owner_retirement_evidence_sha256,
                attempt.result_checkpoint_sha256,
                canonical_json(payload),
                canonical_sha256(payload),
                now,
                now,
            ),
        )

    def _update_attempt(
        self,
        connection: sqlite3.Connection,
        chain_id: str,
        attempt: ProviderStageAttemptV1,
    ) -> None:
        payload = attempt.to_payload()
        cursor = connection.execute(
            "UPDATE provider_stage_retry_attempts SET phase = ?, invocation_reserved = ?, "
            "dispatch_evidence_sha256 = ?, ledger_prefix_after_sha256 = ?, "
            "provider_operations_observed = ?, provider_operations_conservative = ?, "
            "duration_ms = ?, input_tokens = ?, cached_input_tokens = ?, output_tokens = ?, "
            "reasoning_tokens = ?, failure_class = ?, failure_evidence_sha256 = ?, "
            "owner_retirement_evidence_sha256 = ?, result_checkpoint_sha256 = ?, "
            "attempt_json = ?, attempt_sha256 = ?, updated_at = ? "
            "WHERE chain_id = ? AND attempt_number = ?",
            (
                attempt.phase.value,
                int(attempt.dispatch_evidence_sha256 is not None),
                attempt.dispatch_evidence_sha256,
                attempt.ledger_prefix_after_sha256,
                attempt.provider_operations_observed,
                attempt.provider_operations_conservative,
                attempt.duration_ms,
                attempt.input_tokens,
                attempt.cached_input_tokens,
                attempt.output_tokens,
                attempt.reasoning_tokens,
                None if attempt.failure_class is None else attempt.failure_class.value,
                attempt.failure_evidence_sha256,
                attempt.owner_retirement_evidence_sha256,
                attempt.result_checkpoint_sha256,
                canonical_json(payload),
                canonical_sha256(payload),
                self.authority_store._now(),
                chain_id,
                attempt.attempt_number,
            ),
        )
        if cursor.rowcount != 1:
            raise StateConflictError("provider-stage attempt update lost authority")

    def _write_transition(
        self,
        connection: sqlite3.Connection,
        previous_row: sqlite3.Row,
        updated: ProviderStageRetryChainV1,
        event_kind: str,
        *,
        extra: dict[str, Any] | None = None,
    ) -> ProviderStageRetryChainV1:
        if previous_row["chain_id"] != updated.chain_id or previous_row[
            "identity_sha256"
        ] != canonical_sha256(updated.identity.to_payload()):
            raise StateConflictError("provider-stage transition changed immutable identity")
        now = self.authority_store._now()
        cursor = connection.execute(
            "UPDATE provider_stage_retry_chains SET phase = ?, "
            "result_checkpoint_sha256 = ?, downstream_intent_sha256 = ?, "
            "downstream_evidence_sha256 = ?, block_reason = ?, "
            "block_evidence_sha256 = ?, state_version = state_version + 1, updated_at = ? "
            "WHERE chain_id = ? AND state_version = ?",
            (
                updated.phase.value,
                (
                    None
                    if updated.result_checkpoint is None
                    else updated.result_checkpoint.checkpoint_sha256
                ),
                updated.downstream_intent_sha256,
                updated.downstream_evidence_sha256,
                None if updated.block_reason is None else updated.block_reason.value,
                updated.block_evidence_sha256,
                now,
                updated.chain_id,
                int(previous_row["state_version"]),
            ),
        )
        if cursor.rowcount != 1:
            raise StateConflictError("provider-stage transition lost optimistic authority")
        event = {
            "from_phase": str(previous_row["phase"]),
            "to_phase": updated.phase.value,
            "chain_sha256": updated.chain_sha256,
            "attempt_number": (
                None if not updated.attempts else updated.attempts[-1].attempt_number
            ),
        }
        if extra:
            event.update(extra)
        self._append_event(connection, updated.chain_id, event_kind, event)
        return self._chain(connection, updated.chain_id)

    def _append_event(
        self,
        connection: sqlite3.Connection,
        chain_id: str,
        event_kind: str,
        payload: dict[str, Any],
    ) -> None:
        sequence = int(
            connection.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 "
                "FROM provider_stage_retry_events WHERE chain_id = ?",
                (chain_id,),
            ).fetchone()[0]
        )
        body = {
            "schema_version": "cera.provider_stage_retry_event.v1",
            "chain_id": chain_id,
            "sequence": sequence,
            "event_kind": event_kind,
            **payload,
        }
        connection.execute(
            "INSERT INTO provider_stage_retry_events("
            "chain_id, sequence, event_kind, event_json, event_sha256, created_at"
            ") VALUES (?, ?, ?, ?, ?, ?)",
            (
                chain_id,
                sequence,
                event_kind,
                canonical_json(body),
                canonical_sha256(body),
                self.authority_store._now(),
            ),
        )

    @staticmethod
    def _prepared_attempt(
        *,
        attempt_number: int,
        session_scope_sha256: str,
        ledger_prefix_before_sha256: str,
    ) -> ProviderStageAttemptV1:
        return ProviderStageAttemptV1(
            schema_version=ProviderStageAttemptV1.SCHEMA_VERSION,
            attempt_number=attempt_number,
            phase=ProviderStageAttemptPhase.PREPARED,
            session_scope_sha256=session_scope_sha256,
            ledger_prefix_before_sha256=ledger_prefix_before_sha256,
            dispatch_evidence_sha256=None,
            ledger_prefix_after_sha256=None,
            provider_operations_observed=0,
            provider_operations_conservative=0,
            duration_ms=None,
            input_tokens=None,
            cached_input_tokens=None,
            output_tokens=None,
            reasoning_tokens=None,
            failure_class=None,
            failure_evidence_sha256=None,
            owner_retirement_evidence_sha256=None,
            result_checkpoint_sha256=None,
        )

    @staticmethod
    def _current_attempt(
        chain: ProviderStageRetryChainV1,
        attempt_number: int,
    ) -> ProviderStageAttemptV1:
        if (
            type(attempt_number) is not int
            or not chain.attempts
            or chain.attempts[-1].attempt_number != attempt_number
        ):
            raise StateConflictError("provider-stage current attempt changed")
        return chain.attempts[-1]

    @staticmethod
    def _require_failure_match(
        current: ProviderStageAttemptV1,
        **expected: Any,
    ) -> None:
        for name, value in expected.items():
            if getattr(current, name) != value:
                raise StateConflictError("provider-stage failure changed on replay")

    @staticmethod
    def _require_result_match(
        current: ProviderStageAttemptV1,
        *,
        checkpoint: ProviderStageProtectedCheckpointV1,
        **expected: Any,
    ) -> None:
        if current.result_checkpoint_sha256 != checkpoint.checkpoint_sha256:
            raise StateConflictError("provider-stage result checkpoint changed on replay")
        for name, value in expected.items():
            if getattr(current, name) != value:
                raise StateConflictError("provider-stage result metrics changed on replay")

    @staticmethod
    def _require_dispatch_block(
        chain: ProviderStageRetryChainV1,
    ) -> ProviderStageRetryChainV1:
        if (
            chain.phase is not ProviderStageRetryPhase.BLOCKED_AMBIGUOUS
            or chain.block_reason is not ProviderStageBlockReason.DISPATCH_CUSTODY_AMBIGUOUS
            or not chain.attempts
            or chain.attempts[-1].phase is not ProviderStageAttemptPhase.OWNER_RETIRED
            or chain.attempts[-1].failure_class is not ProviderStageFailureClass.DISPATCH_AMBIGUOUS
        ):
            raise StateConflictError("provider-stage block is not dispatch-reconcilable")
        return chain

    @staticmethod
    def _require_identity(identity: ProviderStageRetryIdentityV1) -> None:
        if type(identity) is not ProviderStageRetryIdentityV1:
            raise ContractValidationError("provider-stage retry identity contract changed")

    @staticmethod
    def _require_chain_id(chain_id: str) -> None:
        if not isinstance(chain_id, str) or not chain_id.startswith("stage-retry-"):
            raise ContractValidationError("provider-stage Retry chain ID is invalid")
        if not re_is_sha256(chain_id.removeprefix("stage-retry-")):
            raise ContractValidationError("provider-stage Retry chain ID is invalid")

    @staticmethod
    def _require_sha(value: str, field_name: str) -> None:
        if not re_is_sha256(value):
            raise ContractValidationError(f"provider-stage {field_name} must be SHA-256")

    @staticmethod
    def _exhausted_terminal(
        chain: ProviderStageRetryChainV1,
    ) -> ProviderStageRetryExhaustedV1:
        final = chain.attempts[-1]
        if final.failure_class not in RETRYABLE_PROVIDER_STAGE_FAILURES:
            raise TransactionError("non-Retry provider-stage chain cannot be exhausted")
        evidence = domain_sha256(
            "cera.provider_stage_retry_exhausted_evidence.v1",
            {
                "chain_sha256": chain.chain_sha256,
                "attempt_chain_sha256": chain.attempt_chain_sha256,
                "final_failure_class": final.failure_class.value,
            },
        )
        return ProviderStageRetryExhaustedV1(
            schema_version=ProviderStageRetryExhaustedV1.SCHEMA_VERSION,
            severity="critical",
            provider=chain.identity.provider,
            model_family=chain.identity.model_family,
            stage=chain.identity.stage,
            maximum_attempts=MAXIMUM_PROVIDER_STAGE_ATTEMPTS,
            attempts_total=chain.attempts_total,
            retries_consumed=chain.retries_consumed,
            story_state_committed=chain.identity.story_state_committed,
            failed_stage_effect_committed=False,
            provider_operations_observed_total=(chain.provider_operations_observed_total),
            provider_operations_conservative_total=(chain.provider_operations_conservative_total),
            final_failure_class=final.failure_class,
            request_sha256=chain.identity.request_sha256,
            stage_input_sha256=chain.identity.stage_input_sha256,
            attempt_chain_sha256=chain.attempt_chain_sha256,
            terminal_evidence_sha256=evidence,
        )

    @staticmethod
    def _blocked_terminal(
        chain: ProviderStageRetryChainV1,
    ) -> ProviderStageRetryBlockedV1:
        assert chain.block_reason is not None
        evidence = domain_sha256(
            "cera.provider_stage_retry_blocked_evidence.v1",
            {
                "chain_sha256": chain.chain_sha256,
                "attempt_chain_sha256": chain.attempt_chain_sha256,
                "block_reason": chain.block_reason.value,
            },
        )
        return ProviderStageRetryBlockedV1(
            schema_version=ProviderStageRetryBlockedV1.SCHEMA_VERSION,
            severity="critical",
            provider=chain.identity.provider,
            model_family=chain.identity.model_family,
            stage=chain.identity.stage,
            maximum_attempts=MAXIMUM_PROVIDER_STAGE_ATTEMPTS,
            attempts_total=chain.attempts_total,
            retries_consumed=chain.retries_consumed,
            story_state_committed=chain.identity.story_state_committed,
            failed_stage_effect_committed=False,
            provider_operations_observed_total=(chain.provider_operations_observed_total),
            provider_operations_conservative_total=(chain.provider_operations_conservative_total),
            block_reason=chain.block_reason,
            request_sha256=chain.identity.request_sha256,
            stage_input_sha256=chain.identity.stage_input_sha256,
            attempt_chain_sha256=chain.attempt_chain_sha256,
            terminal_evidence_sha256=evidence,
        )

    @staticmethod
    def _recovery_required_terminal(
        chain: ProviderStageRetryChainV1,
    ) -> ProviderStageRecoveryRequiredV1:
        final_failure = None if not chain.attempts else chain.attempts[-1].failure_class
        if chain.block_reason is None:
            if final_failure not in NON_RETRYABLE_PROVIDER_STAGE_FAILURES:
                raise TransactionError("provider-stage recovery lacks a non-Retry failure")
        elif chain.block_reason is ProviderStageBlockReason.DISPATCH_CUSTODY_AMBIGUOUS:
            raise TransactionError("dispatch ambiguity cannot require operator recovery")
        evidence = domain_sha256(
            "cera.provider_stage_retry_recovery_required_evidence.v1",
            {
                "chain_sha256": chain.chain_sha256,
                "attempt_chain_sha256": chain.attempt_chain_sha256,
                "final_failure_class": (None if final_failure is None else final_failure.value),
                "block_reason": (None if chain.block_reason is None else chain.block_reason.value),
            },
        )
        return ProviderStageRecoveryRequiredV1(
            schema_version=ProviderStageRecoveryRequiredV1.SCHEMA_VERSION,
            severity="critical",
            provider=chain.identity.provider,
            model_family=chain.identity.model_family,
            stage=chain.identity.stage,
            maximum_attempts=MAXIMUM_PROVIDER_STAGE_ATTEMPTS,
            attempts_total=chain.attempts_total,
            retries_consumed=chain.retries_consumed,
            story_state_committed=chain.identity.story_state_committed,
            failed_stage_effect_committed=False,
            provider_operations_observed_total=chain.provider_operations_observed_total,
            provider_operations_conservative_total=(chain.provider_operations_conservative_total),
            final_failure_class=(
                final_failure if final_failure in NON_RETRYABLE_PROVIDER_STAGE_FAILURES else None
            ),
            block_reason=chain.block_reason,
            request_sha256=chain.identity.request_sha256,
            stage_input_sha256=chain.identity.stage_input_sha256,
            attempt_chain_sha256=chain.attempt_chain_sha256,
            terminal_evidence_sha256=evidence,
        )
