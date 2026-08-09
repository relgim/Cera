"""Adapter from CERA's existing stored Codex backend to continuous sessions."""

from __future__ import annotations

from cera.errors import StateConflictError
from cera.provider_dispatch_guard import is_external_provider_boundary
from cera.reasoner_session.codex_stored import StoredThreadBackend
from cera.serialization import text_sha256

from .sessions import (
    ContinuousSessionCompatibilityV1,
    ContinuousSessionHandleV1,
)


class CodexContinuousStoredSessionPort:
    """Use one mutable stored role thread per branch in the shadow experiment.

    This reuses the qualified start/resume/fork/archive backend.  It differs
    from the active immutable-checkpoint path only in experimental context
    policy: accepted final sequences supersede provisional Planner output in
    the same stored thread.
    """

    def __init__(self, backend: StoredThreadBackend) -> None:
        self.backend = backend
        self.provider_calls = 0
        self.operations: list[tuple[str, str]] = []

    @property
    def external_provider_boundary(self) -> bool:
        return is_external_provider_boundary(self.backend)

    def create(
        self,
        compatibility: ContinuousSessionCompatibilityV1,
        *,
        base_instructions: str = "",
    ) -> ContinuousSessionHandleV1:
        backend_base = getattr(self.backend, "base_instructions", base_instructions)
        if backend_base != base_instructions:
            raise StateConflictError(
                "continuous stored backend base instructions changed"
            )
        thread_id = self.backend.start_stored_thread()
        self.operations.append(("thread/start:continuous", text_sha256(thread_id)))
        return self._handle(thread_id)

    def resume(self, handle: ContinuousSessionHandleV1) -> bool:
        resumed = self.backend.resume_stored_thread(handle.provider_thread_id)
        self.operations.append(
            (
                "thread/resume:continuous" if resumed else "thread/resume:missing",
                handle.provider_thread_id_sha256,
            )
        )
        return resumed

    def fork_branch(
        self,
        parent: ContinuousSessionHandleV1,
        compatibility: ContinuousSessionCompatibilityV1,
    ) -> ContinuousSessionHandleV1:
        if not self.resume(parent):
            raise StateConflictError("continuous branch parent cannot be resumed")
        thread_id = self.backend.fork_stored_thread(parent.provider_thread_id)
        self.operations.append(("thread/fork:continuous_branch", text_sha256(thread_id)))
        return self._handle(thread_id)

    def append_context(self, handle: ContinuousSessionHandleV1, text: str) -> None:
        self.backend.append_model_visible_context(handle.provider_thread_id, text)
        self.operations.append(("thread/inject_items:continuous", text_sha256(text)))

    def archive(self, handle: ContinuousSessionHandleV1, reason: str) -> None:
        self.backend.archive_stored_thread(handle.provider_thread_id)
        self.operations.append(("thread/archive:continuous", handle.provider_thread_id_sha256))

    def selectable_as_active_or_accepted_ancestry(
        self, handle: ContinuousSessionHandleV1
    ) -> bool:
        selectable = self.backend.stored_thread_is_selectable(
            handle.provider_thread_id
        )
        self.operations.append(
            (
                (
                    "thread/selectable:continuous"
                    if selectable
                    else "thread/not_selectable:continuous"
                ),
                handle.provider_thread_id_sha256,
            )
        )
        return selectable

    def _handle(self, thread_id: str) -> ContinuousSessionHandleV1:
        return ContinuousSessionHandleV1(
            schema_version=ContinuousSessionHandleV1.SCHEMA_VERSION,
            provider_session_id=self.backend.session_epoch_id,
            provider_thread_id=thread_id,
            provider_thread_id_sha256=text_sha256(thread_id),
        )
