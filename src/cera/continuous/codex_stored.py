"""Adapter from CERA's existing stored Codex backend to continuous sessions."""

from __future__ import annotations

from cera.errors import StateConflictError
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

    def create(
        self, compatibility: ContinuousSessionCompatibilityV1
    ) -> ContinuousSessionHandleV1:
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

    def _handle(self, thread_id: str) -> ContinuousSessionHandleV1:
        return ContinuousSessionHandleV1(
            schema_version=ContinuousSessionHandleV1.SCHEMA_VERSION,
            provider_session_id=self.backend.session_epoch_id,
            provider_thread_id=thread_id,
            provider_thread_id_sha256=text_sha256(thread_id),
        )
