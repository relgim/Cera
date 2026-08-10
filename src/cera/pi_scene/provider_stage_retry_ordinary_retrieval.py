"""Immutable ordinary Planner retrieval custody for generic stage Retry.

The Planner may read accepted ``ACTIVE`` and deterministic ``DERIVED`` files
while it owns a provider call.  This module captures a complete content
manifest before the first dispatch and wraps every Planner attempt owner so a
retry cannot run after those readable bytes drift.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from cera.errors import ContractValidationError, StateConflictError
from cera.serialization import canonical_sha256, domain_sha256

from ._world_workspace_files import file_manifest, lean_scene_branch_root
from .provider_stage_retry import (
    ProviderStage,
    ProviderStageAttemptV1,
    ProviderStageFailureClass,
    ProviderStageRetryChainV1,
)
from .provider_stage_retry_executor import (
    PreparedProviderStageDispatchPort,
    ProviderStageAttemptMetricsV1,
    ProviderStageAttemptOutcomeV1,
    ProviderStageAttemptOwnerPort,
    ProviderStageNonRetryableFailureV1,
    ProviderStagePreparationV1,
    ProviderStagePretransportFailureV1,
    ProviderStagePretransportNonRetryableFailureV1,
)
from .provider_stage_retry_ordinary import PlannerFrozenRetrievalV1
from .provider_stage_retry_ordinary_custody import (
    ProtectedOrdinaryPendingRequestV1,
    ProtectedOrdinaryStageRetryCustodyStoreV1,
)
from .provider_stage_retry_packets import (
    ImmutableRetrievalSnapshotIdentityV1,
    ProviderStageFrozenPacketV1,
)
from .provider_stage_retry_runtime import ProviderStageAttemptOwnerFactoryPort
from .provider_stage_retry_scope import ProviderStageRetryOccurrenceScopeV1
from .request_binding import PiSceneRequestBindingV1

_EMPTY_MANIFEST_TOOL_BUNDLE: Mapping[str, Any] = {
    "schema_version": "cera.planner_manifest_equivalent_tool_reads.v1",
    "mode": "active_derived_manifest",
    "tool_results": [],
}


class OrdinaryPlannerRetrievalSnapshotPort(Protocol):
    """Capture and revalidate all accepted bytes readable by Planner tools."""

    def capture(
        self,
        binding: PiSceneRequestBindingV1,
        *,
        tool_result_bundle: object | None = None,
    ) -> PlannerFrozenRetrievalV1: ...

    def revalidate(
        self,
        binding: PiSceneRequestBindingV1,
        snapshot: ImmutableRetrievalSnapshotIdentityV1,
    ) -> None: ...


class ActiveDerivedPlannerRetrievalManifestPortV1:
    """Hash a branch's complete ACTIVE+DERIVED file manifest provider-free."""

    SNAPSHOT_SCHEMA_VERSION = "cera.ordinary_planner_active_derived_manifest.v1"

    def __init__(self, runtime_root: Path) -> None:
        if not runtime_root.is_absolute():
            raise ContractValidationError("Planner retrieval runtime root must be absolute")
        self.runtime_root = runtime_root.resolve()

    def capture(
        self,
        binding: PiSceneRequestBindingV1,
        *,
        tool_result_bundle: object | None = None,
    ) -> PlannerFrozenRetrievalV1:
        if type(binding) is not PiSceneRequestBindingV1:
            raise ContractValidationError("Planner retrieval request binding changed")
        identity = self._identity(binding)
        bundle = (
            dict(_EMPTY_MANIFEST_TOOL_BUNDLE) if tool_result_bundle is None else tool_result_bundle
        )
        # Canonical hashing here rejects a non-serializable pre-dispatch bundle.
        canonical_sha256(bundle)
        return PlannerFrozenRetrievalV1(
            retrieval_snapshot=identity,
            tool_result_bundle=bundle,
        )

    def revalidate(
        self,
        binding: PiSceneRequestBindingV1,
        snapshot: ImmutableRetrievalSnapshotIdentityV1,
    ) -> None:
        if (
            type(binding) is not PiSceneRequestBindingV1
            or type(snapshot) is not ImmutableRetrievalSnapshotIdentityV1
        ):
            raise ContractValidationError("Planner retrieval snapshot custody changed")
        if self._identity(binding) != snapshot:
            raise StateConflictError(
                "Planner ACTIVE+DERIVED retrieval snapshot drifted; dispatch is forbidden"
            )

    def _identity(
        self,
        binding: PiSceneRequestBindingV1,
    ) -> ImmutableRetrievalSnapshotIdentityV1:
        branch_root = lean_scene_branch_root(
            self.runtime_root,
            binding.world_id,
            binding.branch_id,
        )
        manifest = {
            "schema_version": self.SNAPSHOT_SCHEMA_VERSION,
            "world_id": binding.world_id,
            "branch_id": binding.branch_id,
            "roots": {name: file_manifest(branch_root / name) for name in ("ACTIVE", "DERIVED")},
        }
        snapshot_sha256 = canonical_sha256(manifest)
        return ImmutableRetrievalSnapshotIdentityV1(
            schema_version=ImmutableRetrievalSnapshotIdentityV1.SCHEMA_VERSION,
            snapshot_id=f"active-derived-{snapshot_sha256[:32]}",
            snapshot_sha256=snapshot_sha256,
            immutability_evidence_sha256=domain_sha256(
                "cera.ordinary_planner_retrieval_immutability.v1",
                {
                    "request_binding_sha256": canonical_sha256(binding.to_payload()),
                    "manifest_schema_version": self.SNAPSHOT_SCHEMA_VERSION,
                    "snapshot_sha256": snapshot_sha256,
                },
            ),
        )


@dataclass(frozen=True, slots=True)
class _ManifestRevalidatingDispatch:
    wrapped: PreparedProviderStageDispatchPort
    require_snapshot: Callable[[], None]
    failure_evidence: Callable[[str], str]
    ledger_prefix_before_sha256: str

    @property
    def dispatch_evidence_sha256(self) -> str:
        return self.wrapped.dispatch_evidence_sha256

    def invoke(self) -> ProviderStageAttemptOutcomeV1:
        try:
            self.require_snapshot()
        except Exception:
            return ProviderStageNonRetryableFailureV1(
                failure_class=ProviderStageFailureClass.CUSTODY_FAILED,
                failure_evidence_sha256=self.failure_evidence("pre_invoke_drift"),
                metrics=ProviderStageAttemptMetricsV1(
                    ledger_prefix_after_sha256=self.ledger_prefix_before_sha256,
                    provider_operations_observed=0,
                    provider_operations_conservative=0,
                    duration_ms=0,
                ),
            )
        return self.wrapped.invoke()


class _ManifestRevalidatingOwner:
    def __init__(
        self,
        *,
        wrapped: ProviderStageAttemptOwnerPort,
        binding: PiSceneRequestBindingV1,
        snapshot: ImmutableRetrievalSnapshotIdentityV1,
        snapshot_port: OrdinaryPlannerRetrievalSnapshotPort,
    ) -> None:
        self._wrapped = wrapped
        self._binding = binding
        self._snapshot = snapshot
        self._snapshot_port = snapshot_port

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
        try:
            self._require_snapshot()
        except Exception:
            return ProviderStagePretransportNonRetryableFailureV1(
                failure_class=ProviderStageFailureClass.CUSTODY_FAILED,
                failure_evidence_sha256=self._failure_evidence(
                    chain_id,
                    attempt_number,
                    "preparation_drift",
                ),
                ledger_prefix_after_sha256=self.ledger_prefix_before_sha256,
                duration_ms=0,
            )
        prepared = self._wrapped.prepare(
            chain_id=chain_id,
            attempt_number=attempt_number,
            exact_input=exact_input,
        )
        if isinstance(
            prepared,
            (
                ProviderStagePretransportFailureV1,
                ProviderStagePretransportNonRetryableFailureV1,
            ),
        ):
            return prepared
        return _ManifestRevalidatingDispatch(
            wrapped=prepared,
            require_snapshot=self._require_snapshot,
            failure_evidence=lambda phase: self._failure_evidence(
                chain_id,
                attempt_number,
                phase,
            ),
            ledger_prefix_before_sha256=self.ledger_prefix_before_sha256,
        )

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

    def _require_snapshot(self) -> None:
        self._snapshot_port.revalidate(self._binding, self._snapshot)

    def _failure_evidence(
        self,
        chain_id: str,
        attempt_number: int,
        phase: str,
    ) -> str:
        return domain_sha256(
            "cera.ordinary_planner_retrieval_drift.v1",
            {
                "chain_id": chain_id,
                "attempt_number": attempt_number,
                "phase": phase,
                "request_binding_sha256": canonical_sha256(self._binding.to_payload()),
                "expected_snapshot": self._snapshot.to_payload(),
            },
        )


class PlannerManifestRevalidatingOwnerFactoryV1:
    """Wrap a lazy Planner owner factory without owning any Retry budget."""

    def __init__(
        self,
        *,
        wrapped: ProviderStageAttemptOwnerFactoryPort,
        custody_store: ProtectedOrdinaryStageRetryCustodyStoreV1,
        snapshot_port: OrdinaryPlannerRetrievalSnapshotPort,
    ) -> None:
        self._wrapped = wrapped
        self._custody_store = custody_store
        self._snapshot_port = snapshot_port

    def create_initial_owner(
        self,
        *,
        scope: ProviderStageRetryOccurrenceScopeV1,
        packet: ProviderStageFrozenPacketV1,
    ) -> ProviderStageAttemptOwnerPort:
        self._require_planner(scope.stage)
        pending = self._custody_store.load_for_scope(scope)
        return self._wrap(
            self._wrapped.create_initial_owner(scope=scope, packet=packet),
            pending,
        )

    def create_retry_owner(
        self,
        *,
        chain: ProviderStageRetryChainV1,
        exact_input: bytes,
    ) -> ProviderStageAttemptOwnerPort:
        self._require_planner(chain.identity.stage)
        pending = self._custody_store.load_request_for_chain(chain.chain_id)
        return self._wrap(
            self._wrapped.create_retry_owner(chain=chain, exact_input=exact_input),
            pending,
        )

    def reconstruct_owner(
        self,
        *,
        chain: ProviderStageRetryChainV1,
        attempt: ProviderStageAttemptV1,
        exact_input: bytes,
    ) -> ProviderStageAttemptOwnerPort:
        self._require_planner(chain.identity.stage)
        pending = self._custody_store.load_request_for_chain(chain.chain_id)
        return self._wrap(
            self._wrapped.reconstruct_owner(
                chain=chain,
                attempt=attempt,
                exact_input=exact_input,
            ),
            pending,
        )

    def _wrap(
        self,
        owner: ProviderStageAttemptOwnerPort,
        pending: ProtectedOrdinaryPendingRequestV1,
    ) -> ProviderStageAttemptOwnerPort:
        return _ManifestRevalidatingOwner(
            wrapped=owner,
            binding=pending.binding,
            snapshot=pending.retrieval_snapshot,
            snapshot_port=self._snapshot_port,
        )

    @staticmethod
    def _require_planner(stage: ProviderStage) -> None:
        if stage is not ProviderStage.PLANNER:
            raise StateConflictError("Planner retrieval wrapper received another stage")


__all__ = [
    "ActiveDerivedPlannerRetrievalManifestPortV1",
    "OrdinaryPlannerRetrievalSnapshotPort",
    "PlannerManifestRevalidatingOwnerFactoryV1",
]
