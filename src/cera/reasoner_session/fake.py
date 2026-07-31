"""Provider-free session adapter used by deterministic qualification."""

from __future__ import annotations

from dataclasses import dataclass, field

from cera.errors import StateConflictError
from cera.serialization import canonical_sha256, text_sha256

from .models import (
    AcceptedTurnReceipt,
    ContextAuthorityDelta,
    ProviderSessionHandle,
    ProviderThreadCustodyDescriptor,
    ProviderThreadRole,
    ProviderThreadStorageMode,
    ReasonerSessionCompatibility,
    RejectedCandidateReceipt,
    SessionReconstructionBundle,
)


@dataclass(slots=True)
class _FakeThread:
    handle: ProviderSessionHandle
    parent_thread_id: str | None
    compatibility_sha256: str
    role: ProviderThreadRole
    items: list[tuple[str, str]] = field(default_factory=list)
    valid: bool = True
    raw_context_retained: bool = True
    archived: bool = False


class InMemoryReasonerSessionPort:
    """Models app-server create/fork/inject/resume without a provider call."""

    def __init__(self) -> None:
        self._sequence = 0
        self._threads: dict[str, _FakeThread] = {}
        self.provider_calls = 0
        self.operations: list[tuple[str, str]] = []

    def _handle(self, *, session_id: str, parent: str | None = None) -> ProviderSessionHandle:
        self._sequence += 1
        thread_id = f"fake-thread-{self._sequence}"
        return ProviderSessionHandle(
            schema_version=ProviderSessionHandle.SCHEMA_VERSION,
            provider_session_id=session_id,
            provider_thread_id=thread_id,
            provider_turn_id=None,
        )

    def create_session(
        self,
        compatibility: ReasonerSessionCompatibility,
        reconstruction: SessionReconstructionBundle,
    ) -> ProviderSessionHandle:
        if reconstruction.compatibility_sha256 != compatibility.compatibility_sha256:
            raise StateConflictError("reconstruction does not match session compatibility")
        handle = self._handle(session_id=f"fake-session-{self._sequence + 1}")
        self._threads[handle.provider_thread_id] = _FakeThread(
            handle=handle,
            parent_thread_id=None,
            compatibility_sha256=compatibility.compatibility_sha256,
            role=ProviderThreadRole.ROOT,
            items=[("reconstruction", reconstruction.bundle_sha256)],
        )
        self.operations.append(("create", handle.provider_thread_id))
        return handle

    def fork_candidate(
        self,
        parent: ProviderSessionHandle,
        delta: ContextAuthorityDelta,
    ) -> ProviderSessionHandle:
        parent_thread = self._require_valid(parent)
        handle = self._handle(session_id=parent.provider_session_id)
        self._threads[handle.provider_thread_id] = _FakeThread(
            handle=handle,
            parent_thread_id=parent.provider_thread_id,
            compatibility_sha256=parent_thread.compatibility_sha256,
            role=ProviderThreadRole.CANDIDATE,
            items=[*parent_thread.items, ("authority_delta", delta.delta_sha256)],
        )
        self.operations.append(("fork_candidate", handle.provider_thread_id))
        return handle

    def fork_branch(
        self,
        parent: ProviderSessionHandle,
        compatibility: ReasonerSessionCompatibility,
        reconstruction: SessionReconstructionBundle,
    ) -> ProviderSessionHandle:
        self._require_valid(parent)
        if reconstruction.compatibility_sha256 != compatibility.compatibility_sha256:
            raise StateConflictError("branch reconstruction is incompatible")
        handle = self._handle(session_id=f"fake-session-{self._sequence + 1}")
        self._threads[handle.provider_thread_id] = _FakeThread(
            handle=handle,
            parent_thread_id=parent.provider_thread_id,
            compatibility_sha256=compatibility.compatibility_sha256,
            role=ProviderThreadRole.BRANCH_ROOT,
            items=[("reconstruction", reconstruction.bundle_sha256)],
        )
        self.operations.append(("fork_branch", handle.provider_thread_id))
        return handle

    def inject_accepted_receipt(
        self,
        candidate: ProviderSessionHandle,
        receipt: AcceptedTurnReceipt,
    ) -> ProviderSessionHandle:
        thread = self._require_valid(candidate)
        thread.items.append(("accepted_receipt", receipt.receipt_sha256))
        self._sequence += 1
        updated = ProviderSessionHandle(
            schema_version=ProviderSessionHandle.SCHEMA_VERSION,
            provider_session_id=candidate.provider_session_id,
            provider_thread_id=candidate.provider_thread_id,
            provider_turn_id=f"fake-injected-turn-{self._sequence}",
        )
        thread.handle = updated
        self.operations.append(("inject_accepted", candidate.provider_thread_id))
        return updated

    def mark_rejected(
        self,
        candidate: ProviderSessionHandle,
        receipt: RejectedCandidateReceipt,
    ) -> None:
        thread = self._require_valid(candidate)
        if thread.role is not ProviderThreadRole.CANDIDATE:
            raise StateConflictError("only a candidate provider thread can be rejected")
        thread.valid = False
        thread.archived = True
        thread.items.append(("rejected_archived", receipt.receipt_sha256))
        self.operations.append(("mark_rejected", candidate.provider_thread_id))

    def discard_failed_candidate(
        self, handle: ProviderSessionHandle, reason: str
    ) -> None:
        thread = self._require_valid(handle)
        if thread.role is not ProviderThreadRole.CANDIDATE:
            raise StateConflictError("only a candidate provider thread can be discarded")
        thread.valid = False
        thread.archived = True
        thread.items.append(("failed_archived", canonical_sha256({"reason": reason})))
        self.operations.append(("discard_failed", handle.provider_thread_id))

    def archive_lineage(self, handle: ProviderSessionHandle, reason: str) -> None:
        thread = self._require_valid(handle)
        thread.valid = False
        thread.archived = True
        thread.items.append(("archived", canonical_sha256({"reason": reason})))
        self.operations.append(("archive_lineage", handle.provider_thread_id))

    def describe_thread(
        self, handle: ProviderSessionHandle
    ) -> ProviderThreadCustodyDescriptor:
        thread = self._threads.get(handle.provider_thread_id)
        if thread is None:
            raise StateConflictError("provider session handle is unavailable")
        return ProviderThreadCustodyDescriptor(
            schema_version=ProviderThreadCustodyDescriptor.SCHEMA_VERSION,
            provider_thread_id_sha256=text_sha256(handle.provider_thread_id),
            parent_provider_thread_id_sha256=(
                text_sha256(thread.parent_thread_id)
                if thread.parent_thread_id is not None
                else None
            ),
            storage_mode=ProviderThreadStorageMode.STORED_LOCAL,
            role=thread.role,
            raw_context_retained=thread.raw_context_retained,
            provider_context_is_story_authority=False,
        )

    def invalidate(self, handle: ProviderSessionHandle, reason: str) -> None:
        thread = self._threads.get(handle.provider_thread_id)
        if thread is None:
            return
        thread.valid = False
        thread.raw_context_retained = False
        thread.items.clear()
        self.operations.append(("invalidate", handle.provider_thread_id))

    def try_resume(self, handle: ProviderSessionHandle) -> ProviderSessionHandle | None:
        thread = self._threads.get(handle.provider_thread_id)
        self.operations.append(("try_resume", handle.provider_thread_id))
        if thread is None or not thread.valid:
            return None
        return thread.handle

    def thread_items(self, handle: ProviderSessionHandle) -> tuple[tuple[str, str], ...]:
        return tuple(self._require_valid(handle).items)

    def parent_thread_id(self, handle: ProviderSessionHandle) -> str | None:
        return self._require_valid(handle).parent_thread_id

    def _require_valid(self, handle: ProviderSessionHandle) -> _FakeThread:
        thread = self._threads.get(handle.provider_thread_id)
        if thread is None or not thread.valid:
            raise StateConflictError("provider session handle is unavailable")
        if thread.handle.provider_session_id != handle.provider_session_id:
            raise StateConflictError("provider session handle changed identity")
        return thread
