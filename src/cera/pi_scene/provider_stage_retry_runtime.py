"""Runtime assembly for exact, stage-local provider Retry custody.

The service in this module is deliberately orchestration-only.  Construction,
status reads, and ambiguity reconciliation never contact a provider.  Provider
transport is reachable only through the three explicitly named methods that
start an initial attempt, accept a backend-issued manual Retry action, or
resume a durably prepared owner.

Stage integrations supply provider-free owner factories, provider-free
ambiguity reconcilers, and idempotent downstream binders.  This keeps provider
and pipeline details outside the generic SQLite/WAL authority while preserving
one closed registration for each of the six governed stages.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from threading import Lock, RLock
from typing import ClassVar, Protocol
from weakref import WeakValueDictionary

from cera.errors import ContractValidationError, StateConflictError
from cera.generated.provider_stage_retry_contracts_v1 import (
    ProviderStageRetryActionV1,
    ProviderStageRetryStatusEnvelopeV1,
    validate_provider_stage_retry_action_v1,
)
from cera.schema import from_mapping
from cera.serialization import canonical_json, canonical_sha256, re_is_sha256
from cera.storage.provider_stage_retry_store import SQLiteProviderStageRetryStore
from cera.storage.sqlite_store import SQLiteAuthorityStore

from .provider_stage_retry import (
    ProviderStage,
    ProviderStageAttemptV1,
    ProviderStageRetryChainV1,
    ProviderStageRetryPhase,
)
from .provider_stage_retry_blob import TrustedLocalProtectedStageBlobStore
from .provider_stage_retry_executor import (
    ProviderStageAmbiguityResolutionV1,
    ProviderStageAttemptOwnerPort,
    ProviderStageRetryExecutorV1,
)
from .provider_stage_retry_packets import ProviderStageFrozenPacketV1
from .provider_stage_retry_scope import ProviderStageRetryOccurrenceScopeV1


class ProviderStageAttemptOwnerFactoryPort(Protocol):
    """Provider-free construction and reconstruction of exact attempt owners.

    Every method is strictly provider-free and lazy. Creating an owner may
    allocate inert local bookkeeping, but it must not start a Codex lifecycle,
    call a model, launch a Pi process, or otherwise cross a provider/process
    boundary. Concurrent loser construction is expected. Only the prepared
    dispatch's executor-owned ``invoke`` call may cross that boundary.

    ``reconstruct_owner`` must reproduce the persisted session and ledger
    bindings exactly; the executor rejects a changed binding before transport.
    """

    def create_initial_owner(
        self,
        *,
        scope: ProviderStageRetryOccurrenceScopeV1,
        packet: ProviderStageFrozenPacketV1,
    ) -> ProviderStageAttemptOwnerPort: ...

    def create_retry_owner(
        self,
        *,
        chain: ProviderStageRetryChainV1,
        exact_input: bytes,
    ) -> ProviderStageAttemptOwnerPort: ...

    def reconstruct_owner(
        self,
        *,
        chain: ProviderStageRetryChainV1,
        attempt: ProviderStageAttemptV1,
        exact_input: bytes,
    ) -> ProviderStageAttemptOwnerPort: ...


class ProviderStageAmbiguityReconcilerPort(Protocol):
    """Read-only/provider-free reconciliation for a Check Status action."""

    def check_status(
        self,
        *,
        chain: ProviderStageRetryChainV1,
    ) -> ProviderStageAmbiguityResolutionV1 | None: ...


class ProviderStageDownstreamBinderPort(Protocol):
    """Idempotent stage-specific publication of one frozen result.

    ``downstream_intent_sha256`` must be deterministic for the exact chain and
    result.  ``bind_once`` must use that intent as a durable idempotency key
    across threads *and processes*. The service lock is process-local, and a
    process may stop after the external effect but before SQLite records its
    evidence.
    """

    def downstream_intent_sha256(
        self,
        *,
        chain: ProviderStageRetryChainV1,
        exact_result: bytes,
    ) -> str: ...

    def bind_once(
        self,
        *,
        chain: ProviderStageRetryChainV1,
        exact_result: bytes,
        downstream_intent_sha256: str,
    ) -> str: ...


class ProviderStageOccurrenceScopeRegistryPort(Protocol):
    """Reconstruction seam for the full scope omitted from hash-only SQLite."""

    def remember(self, scope: ProviderStageRetryOccurrenceScopeV1) -> None: ...

    def find(self, chain_id: str) -> ProviderStageRetryOccurrenceScopeV1 | None: ...


class InMemoryProviderStageOccurrenceScopeRegistryV1:
    """Trusted-process scope registry suitable until a durable owner is wired."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._scopes: dict[str, ProviderStageRetryOccurrenceScopeV1] = {}

    def remember(self, scope: ProviderStageRetryOccurrenceScopeV1) -> None:
        if type(scope) is not ProviderStageRetryOccurrenceScopeV1:
            raise ContractValidationError("provider-stage occurrence scope contract changed")
        chain_id = scope.identity.chain_id
        with self._lock:
            prior = self._scopes.get(chain_id)
            if prior is not None and prior != scope:
                raise StateConflictError("provider-stage occurrence scope changed")
            self._scopes[chain_id] = scope

    def find(self, chain_id: str) -> ProviderStageRetryOccurrenceScopeV1 | None:
        with self._lock:
            return self._scopes.get(chain_id)


class SQLiteProviderStageOccurrenceScopeRegistryV1:
    """Durable immutable full-scope registry beside the hash-only chain row."""

    def __init__(self, authority_store: SQLiteAuthorityStore) -> None:
        if type(authority_store) is not SQLiteAuthorityStore:
            raise ContractValidationError("provider-stage scope registry must use SQLite")
        self._authority_store = authority_store

    def remember(self, scope: ProviderStageRetryOccurrenceScopeV1) -> None:
        if type(scope) is not ProviderStageRetryOccurrenceScopeV1:
            raise ContractValidationError("provider-stage occurrence scope contract changed")
        chain_id = scope.identity.chain_id
        payload = scope.to_payload()
        scope_json = canonical_json(payload)
        scope_sha256 = canonical_sha256(payload)
        with self._authority_store._connect() as connection:
            self._authority_store._begin(connection)
            try:
                chain_row = connection.execute(
                    "SELECT identity_json FROM provider_stage_retry_chains WHERE chain_id = ?",
                    (chain_id,),
                ).fetchone()
                if chain_row is None:
                    raise StateConflictError(
                        "provider-stage chain must be frozen before its full scope"
                    )
                chain_identity = json.loads(str(chain_row["identity_json"]))
                if canonical_sha256(chain_identity) != canonical_sha256(
                    scope.identity.to_payload()
                ):
                    raise StateConflictError("provider-stage scope changed chain identity")
                prior = connection.execute(
                    "SELECT scope_json, scope_sha256 "
                    "FROM provider_stage_retry_occurrence_scopes WHERE chain_id = ?",
                    (chain_id,),
                ).fetchone()
                if prior is not None:
                    if (
                        str(prior["scope_json"]) != scope_json
                        or str(prior["scope_sha256"]) != scope_sha256
                    ):
                        raise StateConflictError("provider-stage occurrence scope changed")
                    connection.commit()
                    return
                connection.execute(
                    "INSERT INTO provider_stage_retry_occurrence_scopes("
                    "chain_id, scope_json, scope_sha256, created_at"
                    ") VALUES (?, ?, ?, ?)",
                    (chain_id, scope_json, scope_sha256, self._authority_store._now()),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def find(self, chain_id: str) -> ProviderStageRetryOccurrenceScopeV1 | None:
        with self._authority_store._connect() as connection:
            row = connection.execute(
                "SELECT s.scope_json, s.scope_sha256, c.identity_json "
                "FROM provider_stage_retry_occurrence_scopes AS s "
                "JOIN provider_stage_retry_chains AS c ON c.chain_id = s.chain_id "
                "WHERE s.chain_id = ?",
                (chain_id,),
            ).fetchone()
        if row is None:
            return None
        try:
            payload = json.loads(str(row["scope_json"]))
            chain_identity = json.loads(str(row["identity_json"]))
        except json.JSONDecodeError as exc:
            raise ContractValidationError(
                "provider-stage durable occurrence scope is not JSON"
            ) from exc
        if canonical_json(payload) != str(row["scope_json"]) or canonical_sha256(payload) != str(
            row["scope_sha256"]
        ):
            raise ContractValidationError("provider-stage durable occurrence scope changed")
        decoded = from_mapping(ProviderStageRetryOccurrenceScopeV1, payload)
        assert isinstance(decoded, ProviderStageRetryOccurrenceScopeV1)
        if decoded.identity.chain_id != chain_id or canonical_sha256(
            decoded.identity.to_payload()
        ) != canonical_sha256(chain_identity):
            raise StateConflictError("provider-stage durable scope changed chain identity")
        return decoded


@dataclass(frozen=True, slots=True)
class ProviderStageRuntimeAdapterV1:
    """Closed runtime integration for exactly one governed provider stage."""

    stage: ProviderStage
    owner_factory: ProviderStageAttemptOwnerFactoryPort
    ambiguity_reconciler: ProviderStageAmbiguityReconcilerPort
    downstream_binder: ProviderStageDownstreamBinderPort

    def __post_init__(self) -> None:
        if type(self.stage) is not ProviderStage:
            raise ContractValidationError("provider-stage runtime adapter stage is not closed")
        if (
            self.owner_factory is None
            or self.ambiguity_reconciler is None
            or self.downstream_binder is None
        ):
            raise ContractValidationError("provider-stage runtime adapter is incomplete")
        required_methods = (
            (
                self.owner_factory,
                ("create_initial_owner", "create_retry_owner", "reconstruct_owner"),
            ),
            (self.ambiguity_reconciler, ("check_status",)),
            (
                self.downstream_binder,
                ("downstream_intent_sha256", "bind_once"),
            ),
        )
        if any(
            not callable(getattr(component, method_name, None))
            for component, method_names in required_methods
            for method_name in method_names
        ):
            raise ContractValidationError("provider-stage runtime adapter port is incomplete")


class ProviderStageRuntimeAdapterRegistryV1:
    """Immutable, exhaustive registry for the six policy-owned stages."""

    def __init__(self, registrations: Iterable[ProviderStageRuntimeAdapterV1]) -> None:
        by_stage: dict[ProviderStage, ProviderStageRuntimeAdapterV1] = {}
        for registration in registrations:
            if type(registration) is not ProviderStageRuntimeAdapterV1:
                raise ContractValidationError("provider-stage runtime registration changed")
            if registration.stage in by_stage:
                raise ContractValidationError(
                    f"provider-stage runtime adapter duplicated {registration.stage.value}"
                )
            by_stage[registration.stage] = registration
        expected = frozenset(ProviderStage)
        actual = frozenset(by_stage)
        if actual != expected:
            missing = ", ".join(sorted(stage.value for stage in expected - actual))
            extra = ", ".join(sorted(stage.value for stage in actual - expected))
            details = "; ".join(
                part
                for part in (
                    f"missing: {missing}" if missing else "",
                    f"extra: {extra}" if extra else "",
                )
                if part
            )
            raise ContractValidationError(
                f"provider-stage runtime registry is not exhaustive ({details})"
            )
        self._by_stage = by_stage

    def for_stage(self, stage: ProviderStage) -> ProviderStageRuntimeAdapterV1:
        if type(stage) is not ProviderStage:
            raise ContractValidationError("provider-stage runtime lookup is not closed")
        return self._by_stage[stage]


class ProviderStageRetryRuntimeServiceV1:
    """SQLite-backed runtime facade with no automatic provider redispatch."""

    _chain_locks_guard: ClassVar[Lock] = Lock()
    # Active callers retain the strong references. Completed occurrences fall
    # out automatically, avoiding an occurrence-lifetime lock-map leak.
    _chain_locks: ClassVar[WeakValueDictionary[str, Lock]] = WeakValueDictionary()

    def __init__(
        self,
        *,
        authority_store: SQLiteAuthorityStore,
        protected_blob_store: TrustedLocalProtectedStageBlobStore,
        registrations: Iterable[ProviderStageRuntimeAdapterV1],
        scope_registry: ProviderStageOccurrenceScopeRegistryPort | None = None,
    ) -> None:
        if type(authority_store) is not SQLiteAuthorityStore:
            raise ContractValidationError("provider-stage runtime authority must be SQLite")
        if type(protected_blob_store) is not TrustedLocalProtectedStageBlobStore:
            raise ContractValidationError("provider-stage runtime protected custody changed")
        self._store = SQLiteProviderStageRetryStore(authority_store, protected_blob_store)
        self._executor = ProviderStageRetryExecutorV1(self._store)
        self._adapters = ProviderStageRuntimeAdapterRegistryV1(registrations)
        self._scopes = (
            SQLiteProviderStageOccurrenceScopeRegistryV1(authority_store)
            if scope_registry is None
            else scope_registry
        )

    @property
    def store(self) -> SQLiteProviderStageRetryStore:
        """Expose the concrete authority for recovery/audit assembly only."""

        return self._store

    def remember_scope(self, scope: ProviderStageRetryOccurrenceScopeV1) -> None:
        """Register an externally reconstructed full scope without dispatching."""

        self._scopes.remember(scope)

    def read_chain(self, chain_id: str) -> ProviderStageRetryChainV1:
        """Read durable state without constructing or dispatching an owner."""

        return self._store.read(chain_id)

    def start_initial(
        self,
        *,
        scope: ProviderStageRetryOccurrenceScopeV1,
        packet: ProviderStageFrozenPacketV1,
    ) -> ProviderStageRetryChainV1:
        """Explicitly authorize the first attempt for one exact frozen packet."""

        self._require_scope_packet(scope, packet)
        # The hash-only chain and protected input are durable before the full
        # reconstructable scope, and both are durable before owner creation.
        # Owner creation therefore cannot contact a provider before restart
        # status can be authenticated from SQLite.
        with self._chain_lock(scope.identity.chain_id):
            chain = self._store.begin(scope.identity, packet.exact_bytes)
            self._scopes.remember(scope)
        if chain.phase is not ProviderStageRetryPhase.INPUT_FROZEN:
            # A duplicate whole-request call can observe durable state but may
            # not resume a prepared owner. Only ``resume_prepared`` names and
            # authorizes that provider dispatch explicitly.
            return chain
        registration = self._adapters.for_stage(scope.stage)
        owner = registration.owner_factory.create_initial_owner(scope=scope, packet=packet)
        return self._executor.execute_initial(scope=scope, packet=packet, owner=owner)

    def execute_manual_retry(
        self,
        action: object,
    ) -> ProviderStageRetryChainV1:
        """Accept one exact backend-issued manual Retry action and no other action."""

        validated = validate_provider_stage_retry_action_v1(action)
        if validated["action_kind"] != "provider_retry":
            raise StateConflictError("provider-stage runtime action is not manual Retry")
        chain = self._store.read(validated["chain_id"])
        registration = self._adapters.for_stage(chain.identity.stage)
        exact_input = self._store.load_input(chain.chain_id)
        ordinal = validated["retry_action_ordinal"]
        assert isinstance(ordinal, int)
        retry_actions = self._store.retry_actions_accepted(chain.chain_id)
        if ordinal <= retry_actions:
            if len(chain.attempts) <= ordinal:
                raise StateConflictError("provider-stage accepted Retry owner is unavailable")
            owner = registration.owner_factory.reconstruct_owner(
                chain=chain,
                attempt=chain.attempts[ordinal],
                exact_input=exact_input,
            )
        else:
            owner = registration.owner_factory.create_retry_owner(
                chain=chain,
                exact_input=exact_input,
            )
        return self._executor.execute_manual_retry(action=validated, owner=owner)

    def resume_prepared(self, chain_id: str) -> ProviderStageRetryChainV1:
        """Explicitly resume one persisted owner that never won a dispatch claim.

        This method may contact the provider.  It is never called by service
        construction, status reads, or provider-free reconciliation.
        """

        chain = self._store.read(chain_id)
        if chain.phase is not ProviderStageRetryPhase.ATTEMPT_PREPARED:
            raise StateConflictError("provider-stage owner is not durably prepared")
        attempt = chain.attempts[-1]
        exact_input = self._store.load_input(chain.chain_id)
        registration = self._adapters.for_stage(chain.identity.stage)
        owner = registration.owner_factory.reconstruct_owner(
            chain=chain,
            attempt=attempt,
            exact_input=exact_input,
        )
        return self._executor.resume_prepared_attempt(chain_id=chain.chain_id, owner=owner)

    def check_status(self, action: object) -> ProviderStageRetryChainV1:
        """Run the exact Check Status action using provider-free evidence only."""

        validated = validate_provider_stage_retry_action_v1(action)
        if validated["action_kind"] != "check_status":
            raise StateConflictError("provider-stage runtime action is not Check Status")
        chain_id = validated["chain_id"]
        with self._chain_lock(chain_id):
            chain = self._store.read(chain_id)
            if chain.phase is not ProviderStageRetryPhase.BLOCKED_AMBIGUOUS:
                return self._executor.reconcile_ambiguous(
                    action=validated,
                    resolution=None,
                )
            # Authenticate the backend-issued action against the exact durable
            # chain before invoking even a provider-free stage reconciler.
            chain = self._executor.reconcile_ambiguous(
                action=validated,
                resolution=None,
            )
            registration = self._adapters.for_stage(chain.identity.stage)
            resolution = registration.ambiguity_reconciler.check_status(chain=chain)
            return self._executor.reconcile_ambiguous(
                action=validated,
                resolution=resolution,
            )

    def canonical_status(
        self,
        *,
        chain_id: str,
        scope: ProviderStageRetryOccurrenceScopeV1 | None = None,
    ) -> ProviderStageRetryStatusEnvelopeV1:
        """Return the generated canonical status envelope without dispatching."""

        if scope is not None:
            if scope.identity.chain_id != chain_id:
                raise StateConflictError("provider-stage status chain changed scope")
            self._scopes.remember(scope)
        resolved = scope if scope is not None else self._scopes.find(chain_id)
        if resolved is None:
            raise StateConflictError(
                "provider-stage full occurrence scope must be reconstructed for status"
            )
        return self._executor.status_envelope(resolved)

    def load_exact_result(self, chain_id: str) -> bytes:
        """Load protected result bytes after a provider-free custody check."""

        return self._executor.load_exact_result(chain_id)

    def finalize_result_once(self, chain_id: str) -> bytes:
        """Idempotently bind and finalize one already-frozen exact result.

        The downstream intent is durable before the binder is called.  A
        binder replay after process failure is safe only because every
        registered binder promises idempotency on that exact intent hash.
        """

        with self._chain_lock(chain_id):
            chain = self._store.read(chain_id)
            exact_result = self._store.load_result(chain_id)
            if chain.phase is ProviderStageRetryPhase.SUCCEEDED:
                return exact_result
            if chain.phase is ProviderStageRetryPhase.DOWNSTREAM_BOUND:
                self._store.mark_succeeded(chain_id)
                return exact_result
            if chain.phase not in {
                ProviderStageRetryPhase.RESULT_FROZEN,
                ProviderStageRetryPhase.DOWNSTREAM_INTENT_FROZEN,
            }:
                raise StateConflictError("provider-stage result is not ready for finalization")

            binder = self._adapters.for_stage(chain.identity.stage).downstream_binder
            intent_sha256 = binder.downstream_intent_sha256(
                chain=chain,
                exact_result=exact_result,
            )
            self._require_sha256(intent_sha256, "downstream intent")
            if (
                chain.downstream_intent_sha256 is not None
                and chain.downstream_intent_sha256 != intent_sha256
            ):
                raise StateConflictError("provider-stage downstream intent changed")
            chain = self._store.freeze_downstream_intent(
                chain_id,
                downstream_intent_sha256=intent_sha256,
            )
            downstream_evidence_sha256 = binder.bind_once(
                chain=chain,
                exact_result=exact_result,
                downstream_intent_sha256=intent_sha256,
            )
            self._require_sha256(downstream_evidence_sha256, "downstream evidence")
            self._executor.bind_downstream_once(
                chain_id=chain_id,
                downstream_intent_sha256=intent_sha256,
                downstream_evidence_sha256=downstream_evidence_sha256,
            )
            return exact_result

    @staticmethod
    def _require_sha256(value: str, field_name: str) -> None:
        if not re_is_sha256(value):
            raise ContractValidationError(f"provider-stage runtime {field_name} must be SHA-256")

    @staticmethod
    def _require_scope_packet(
        scope: ProviderStageRetryOccurrenceScopeV1,
        packet: ProviderStageFrozenPacketV1,
    ) -> None:
        if (
            type(scope) is not ProviderStageRetryOccurrenceScopeV1
            or type(packet) is not ProviderStageFrozenPacketV1
            or scope.stage is not packet.stage
            or scope.stage_input_sha256 != packet.stage_input_sha256
        ):
            raise StateConflictError("provider-stage frozen packet changed occurrence scope")

    @classmethod
    def _chain_lock(cls, chain_id: str) -> Lock:
        with cls._chain_locks_guard:
            return cls._chain_locks.setdefault(chain_id, Lock())


def backend_action_from_envelope(
    envelope: ProviderStageRetryStatusEnvelopeV1,
) -> ProviderStageRetryActionV1:
    """Extract the sole backend-issued action without synthesizing identity."""

    actions = envelope["actions"]
    if len(actions) != 1:
        raise StateConflictError("provider-stage status has no sole backend action")
    return validate_provider_stage_retry_action_v1(actions[0])


__all__ = [
    "InMemoryProviderStageOccurrenceScopeRegistryV1",
    "ProviderStageAmbiguityReconcilerPort",
    "ProviderStageAttemptOwnerFactoryPort",
    "ProviderStageDownstreamBinderPort",
    "ProviderStageOccurrenceScopeRegistryPort",
    "ProviderStageRetryRuntimeServiceV1",
    "ProviderStageRuntimeAdapterRegistryV1",
    "ProviderStageRuntimeAdapterV1",
    "SQLiteProviderStageOccurrenceScopeRegistryV1",
    "backend_action_from_envelope",
]
