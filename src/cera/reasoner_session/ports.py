"""Provider-neutral session lifecycle boundary."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .models import (
    AcceptedTurnReceipt,
    ContextAuthorityDelta,
    ProviderThreadCustodyDescriptor,
    ProviderSessionHandle,
    ReasonerSessionCompatibility,
    RejectedCandidateReceipt,
    SessionReconstructionBundle,
)


@runtime_checkable
class ReasonerSessionPort(Protocol):
    """Manages provider context only; it cannot publish or validate story state."""

    def create_session(
        self,
        compatibility: ReasonerSessionCompatibility,
        reconstruction: SessionReconstructionBundle,
    ) -> ProviderSessionHandle: ...

    def fork_candidate(
        self,
        parent: ProviderSessionHandle,
        delta: ContextAuthorityDelta,
    ) -> ProviderSessionHandle: ...

    def fork_branch(
        self,
        parent: ProviderSessionHandle,
        compatibility: ReasonerSessionCompatibility,
        reconstruction: SessionReconstructionBundle,
    ) -> ProviderSessionHandle: ...

    def inject_accepted_receipt(
        self,
        candidate: ProviderSessionHandle,
        receipt: AcceptedTurnReceipt,
    ) -> ProviderSessionHandle: ...

    def describe_thread(
        self, handle: ProviderSessionHandle
    ) -> ProviderThreadCustodyDescriptor: ...

    def mark_rejected(
        self,
        candidate: ProviderSessionHandle,
        receipt: RejectedCandidateReceipt,
    ) -> None: ...

    def discard_failed_candidate(
        self, handle: ProviderSessionHandle, reason: str
    ) -> None: ...

    def archive_lineage(
        self, handle: ProviderSessionHandle, reason: str
    ) -> None: ...

    def invalidate(self, handle: ProviderSessionHandle, reason: str) -> None: ...

    def try_resume(self, handle: ProviderSessionHandle) -> ProviderSessionHandle | None: ...
