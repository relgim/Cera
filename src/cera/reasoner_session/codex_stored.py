"""Stored Codex app-server implementation of the session lifecycle port.

This module performs conversation lifecycle operations only.  Starting,
resuming, forking, or archiving a local app-server rollout is not a
model call and cannot publish story state.  The ordinary Reasoner transport is
bound separately to the candidate thread selected by Python.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib.metadata import version
from typing import Any, Protocol, runtime_checkable
from uuid import uuid4

from cera.errors import ContractValidationError, StateConflictError
from cera.provider_dispatch_guard import (
    assert_provider_dispatch_allowed,
    is_external_provider_boundary,
)
from cera.providers.codex_runtime_policy import (
    require_qualified_codex_model,
    runtime_config_and_environment,
    start_codex_thread_without_environments,
    validate_codex_app_server_configuration,
    validate_codex_mcp_server_status,
)
from cera.serialization import text_sha256

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


def _stored_runtime_config() -> dict[str, object]:
    """Return the no-files/no-network baseline for a stored Reasoner thread."""

    config, environment = runtime_config_and_environment(None)
    if environment:
        raise ContractValidationError(
            "stored Codex runtime unexpectedly retained request credentials"
        )
    permissions = config["permissions"]
    if not isinstance(permissions, dict):
        raise ContractValidationError("stored Codex permission profile is invalid")
    profile = permissions.get("cera-no-files")
    if not isinstance(profile, dict):
        raise ContractValidationError("stored Codex permission profile is missing")
    profile["description"] = "CERA stored Reasoner without filesystem or network"
    return config


@runtime_checkable
class StoredThreadBackend(Protocol):
    """Small seam over Codex app-server stored-thread methods."""

    @property
    def session_epoch_id(self) -> str: ...

    def start_stored_thread(self) -> str: ...

    def fork_stored_thread(self, parent_thread_id: str) -> str: ...

    def resume_stored_thread(self, thread_id: str) -> bool: ...

    def append_model_visible_context(self, thread_id: str, text: str) -> None: ...

    def archive_stored_thread(self, thread_id: str) -> None: ...

    def archive_stored_leaf(self, thread_id: str) -> None: ...

    def stored_thread_is_selectable(self, thread_id: str) -> bool: ...


@dataclass(slots=True)
class OpenAICodexStoredThreadBackend:
    """Version-isolated wrapper around the installed ``openai_codex`` SDK.

    An empty stored thread is materialized with ``thread/name/set`` before the
    app-server process can close.  The installed app-server otherwise returns
    an ID whose empty rollout cannot be resumed from a later process.
    """

    external_provider_boundary = True

    codex: Any
    model: str
    cwd: str
    base_instructions: str
    service_tier: str | None = None
    service_name: str = "cera_stored_scene_reasoner"
    transport_version: str = "0.144.4"
    _session_epoch_id: str = ""

    def __post_init__(self) -> None:
        require_qualified_codex_model(self.model)
        for value, field in (
            (self.model, "model"),
            (self.cwd, "cwd"),
            (self.base_instructions, "base_instructions"),
            (self.service_name, "service_name"),
            (self.transport_version, "transport_version"),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ContractValidationError(f"stored Codex {field} is required")
        if self.service_tier not in {None, "priority"}:
            raise ContractValidationError("stored Codex service tier must be priority or omitted")
        if version("openai-codex") != self.transport_version:
            raise StateConflictError(
                "installed Codex SDK does not match stored-thread compatibility"
            )
        assert_provider_dispatch_allowed(
            "reasoner_session.codex_stored.session_identity",
            external_provider_boundary=self._external_provider_boundary(),
        )
        if not self._session_epoch_id:
            self._session_epoch_id = f"codex-app-server:{uuid4()}"

    @property
    def session_epoch_id(self) -> str:
        return self._session_epoch_id

    def _external_provider_boundary(self) -> bool:
        return is_external_provider_boundary(self)

    def start_stored_thread(self) -> str:
        assert_provider_dispatch_allowed(
            "reasoner_session.codex_stored.thread_start",
            external_provider_boundary=self._external_provider_boundary(),
        )
        validate_codex_app_server_configuration(
            self.codex,
            cwd=self.cwd,
        )
        thread = start_codex_thread_without_environments(
            self.codex,
            model=self.model,
            cwd=self.cwd,
            ephemeral=False,
            base_instructions=self.base_instructions,
            config=_stored_runtime_config(),
            service_name=self.service_name,
            service_tier=self.service_tier,
        )
        thread_id = self._thread_id(thread)
        validate_codex_mcp_server_status(self.codex, thread_id=thread_id)
        self._materialize_thread(thread_id, role="root")
        return thread_id

    def fork_stored_thread(self, parent_thread_id: str) -> str:
        assert_provider_dispatch_allowed(
            "reasoner_session.codex_stored.thread_fork",
            external_provider_boundary=self._external_provider_boundary(),
        )
        from openai_codex.api import ApprovalMode

        validate_codex_app_server_configuration(
            self.codex,
            cwd=self.cwd,
        )
        thread = self.codex.thread_fork(
            parent_thread_id,
            model=self.model,
            cwd=self.cwd,
            ephemeral=False,
            base_instructions=self.base_instructions,
            config=_stored_runtime_config(),
            service_tier=self.service_tier,
            approval_mode=ApprovalMode.deny_all,
        )
        thread_id = self._thread_id(thread)
        validate_codex_mcp_server_status(self.codex, thread_id=thread_id)
        self._materialize_thread(thread_id, role="candidate")
        return thread_id

    def resume_stored_thread(self, thread_id: str) -> bool:
        assert_provider_dispatch_allowed(
            "reasoner_session.codex_stored.thread_resume",
            external_provider_boundary=self._external_provider_boundary(),
        )
        from openai_codex.api import ApprovalMode

        try:
            validate_codex_app_server_configuration(
                self.codex,
                cwd=self.cwd,
            )
            thread = self.codex.thread_resume(
                thread_id,
                model=self.model,
                cwd=self.cwd,
                base_instructions=self.base_instructions,
                config=_stored_runtime_config(),
                service_tier=self.service_tier,
                approval_mode=ApprovalMode.deny_all,
            )
            resumed_thread_id = self._thread_id(thread)
            if resumed_thread_id != thread_id:
                return False
            validate_codex_mcp_server_status(
                self.codex,
                thread_id=resumed_thread_id,
            )
            return True
        except Exception as exc:
            diagnostic = " ".join(str(exc).lower().split())
            if any(
                marker in diagnostic
                for marker in (
                    "no rollout found",
                    "thread not found",
                    "does not exist",
                    "unknown thread",
                    "archived",
                )
            ):
                return False
            raise

    def append_model_visible_context(self, thread_id: str, text: str) -> None:
        """Append one non-generating user item to stored model-visible history.

        ``thread/inject_items`` changes only local thread custody.  It does not
        start a model turn and therefore does not consume a provider call.  The
        continuous shadow route uses it for accepted-final synchronization;
        core story authority remains in Python's hash-bound ledger.
        """

        if not isinstance(text, str) or not text.strip():
            raise ContractValidationError("stored Codex injected context is empty")
        assert_provider_dispatch_allowed(
            "reasoner_session.codex_stored.thread_inject_items",
            external_provider_boundary=self._external_provider_boundary(),
        )
        client = getattr(self.codex, "_client", None)
        request = getattr(client, "request", None)
        if not callable(request):
            raise StateConflictError("installed Codex SDK cannot inject stored-thread context")
        from openai_codex.generated.v2_all import ThreadInjectItemsResponse

        request(
            "thread/inject_items",
            {
                "threadId": thread_id,
                "items": [
                    {
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": text}],
                    }
                ],
            },
            response_model=ThreadInjectItemsResponse,
        )

    def archive_stored_thread(self, thread_id: str) -> None:
        assert_provider_dispatch_allowed(
            "reasoner_session.codex_stored.thread_archive",
            external_provider_boundary=self._external_provider_boundary(),
        )
        self.codex.thread_archive(thread_id)

    def archive_stored_leaf(self, thread_id: str) -> None:
        # The installed app-server's thread/delete path is not transactionally
        # reliable with its current local state database.  Archival is the
        # supported terminal isolation operation: archived leaves cannot be
        # resumed or selected as accepted ancestry.
        self.archive_stored_thread(thread_id)

    def stored_thread_is_selectable(self, thread_id: str) -> bool:
        """Return whether the app server still exposes a thread as active.

        The scan uses the supported state-database listing contract and walks
        every page.  Any transport or decoding problem is propagated so an
        unknown outcome cannot be presented as verified archival.
        """

        assert_provider_dispatch_allowed(
            "reasoner_session.codex_stored.thread_list",
            external_provider_boundary=self._external_provider_boundary(),
        )
        cursor: str | None = None
        while True:
            response = self.codex.thread_list(
                archived=False,
                cursor=cursor,
                limit=100,
                use_state_db_only=True,
            )
            for thread in response.data:
                if self._thread_id(thread) == thread_id:
                    return True
            cursor = response.next_cursor
            if cursor is None:
                return False

    def _materialize_thread(self, thread_id: str, *, role: str) -> None:
        assert_provider_dispatch_allowed(
            "reasoner_session.codex_stored.thread_set_name",
            external_provider_boundary=self._external_provider_boundary(),
        )
        client = getattr(self.codex, "_client", None)
        set_name = getattr(client, "thread_set_name", None)
        if not callable(set_name):
            raise StateConflictError("installed Codex SDK cannot materialize a stored thread")
        set_name(
            thread_id,
            f"CERA {role} {text_sha256(thread_id)[:12]}",
        )

    @staticmethod
    def _thread_id(thread: Any) -> str:
        value = getattr(thread, "id", None)
        if not isinstance(value, str) or not value.strip():
            raise StateConflictError("Codex app-server omitted its stored thread ID")
        return value


class CodexStoredThreadSessionPort:
    """Native stored-thread lifecycle with one immutable thread per checkpoint."""

    def __init__(self, backend: StoredThreadBackend, *, base_instruction_sha256: str) -> None:
        if len(base_instruction_sha256) != 64 or any(
            value not in "0123456789abcdef" for value in base_instruction_sha256
        ):
            raise ContractValidationError("stored-thread base instruction hash is invalid")
        self.backend = backend
        self.base_instruction_sha256 = base_instruction_sha256
        self.provider_calls = 0
        self.operations: list[tuple[str, str]] = []
        self._descriptors: dict[str, ProviderThreadCustodyDescriptor] = {}

    def create_session(
        self,
        compatibility: ReasonerSessionCompatibility,
        reconstruction: SessionReconstructionBundle,
    ) -> ProviderSessionHandle:
        self._assert_compatible(compatibility, reconstruction)
        thread_id = self.backend.start_stored_thread()
        handle = self._handle(thread_id)
        self._remember(handle, parent=None, role=ProviderThreadRole.ROOT)
        self.operations.append(("thread/start:stored", text_sha256(thread_id)))
        return handle

    def fork_candidate(
        self,
        parent: ProviderSessionHandle,
        delta: ContextAuthorityDelta,
    ) -> ProviderSessionHandle:
        if not self.backend.resume_stored_thread(parent.provider_thread_id):
            raise StateConflictError("accepted provider checkpoint is unavailable")
        thread_id = self.backend.fork_stored_thread(parent.provider_thread_id)
        handle = self._handle(thread_id)
        self._remember(handle, parent=parent, role=ProviderThreadRole.CANDIDATE)
        self.operations.append(("thread/fork:candidate", text_sha256(thread_id)))
        return handle

    def fork_branch(
        self,
        parent: ProviderSessionHandle,
        compatibility: ReasonerSessionCompatibility,
        reconstruction: SessionReconstructionBundle,
    ) -> ProviderSessionHandle:
        self._assert_compatible(compatibility, reconstruction)
        if not self.backend.resume_stored_thread(parent.provider_thread_id):
            raise StateConflictError("branch source provider checkpoint is unavailable")
        thread_id = self.backend.fork_stored_thread(parent.provider_thread_id)
        handle = self._handle(thread_id)
        self._remember(handle, parent=parent, role=ProviderThreadRole.BRANCH_ROOT)
        self.operations.append(("thread/fork:branch", text_sha256(thread_id)))
        return handle

    def inject_accepted_receipt(
        self,
        candidate: ProviderSessionHandle,
        receipt: AcceptedTurnReceipt,
    ) -> ProviderSessionHandle:
        # Acceptance is Python-owned and is reasserted in the next canonical
        # packet.  Injecting it with a model turn would consume a provider call;
        # injecting an undocumented raw item would weaken the transport
        # contract.  The candidate thread itself already ends at the accepted
        # Reasoner turn, so the handle remains the exact checkpoint.
        if not self.backend.resume_stored_thread(candidate.provider_thread_id):
            raise StateConflictError("accepted candidate thread is unavailable")
        self.operations.append(("accept:python_receipt_only", candidate.handle_sha256))
        return candidate

    def mark_rejected(
        self,
        candidate: ProviderSessionHandle,
        receipt: RejectedCandidateReceipt,
    ) -> None:
        self.backend.archive_stored_leaf(candidate.provider_thread_id)
        self._remember_archived(candidate, role=ProviderThreadRole.CANDIDATE)
        self.operations.append(("thread/archive:rejected_leaf", candidate.handle_sha256))

    def discard_failed_candidate(self, handle: ProviderSessionHandle, reason: str) -> None:
        self.backend.archive_stored_leaf(handle.provider_thread_id)
        self._remember_archived(handle, role=ProviderThreadRole.CANDIDATE)
        self.operations.append(("thread/archive:failed_leaf", handle.handle_sha256))

    def archive_lineage(self, handle: ProviderSessionHandle, reason: str) -> None:
        self.backend.archive_stored_thread(handle.provider_thread_id)
        descriptor = self._descriptor_or_default(handle)
        self._descriptors[handle.provider_thread_id] = ProviderThreadCustodyDescriptor(
            schema_version=ProviderThreadCustodyDescriptor.SCHEMA_VERSION,
            provider_thread_id_sha256=descriptor.provider_thread_id_sha256,
            parent_provider_thread_id_sha256=(descriptor.parent_provider_thread_id_sha256),
            storage_mode=ProviderThreadStorageMode.STORED_LOCAL,
            role=descriptor.role,
            raw_context_retained=True,
            provider_context_is_story_authority=False,
        )
        self.operations.append(("thread/archive", handle.handle_sha256))

    def invalidate(self, handle: ProviderSessionHandle, reason: str) -> None:
        """Compatibility alias for older callers.

        Candidate-specific coordinator paths use ``discard_failed_candidate``;
        accepted lineage invalidation archives rather than risking cascading
        deletion of valid descendants.
        """

        descriptor = self._descriptors.get(handle.provider_thread_id)
        if descriptor is not None and descriptor.role is ProviderThreadRole.CANDIDATE:
            self.discard_failed_candidate(handle, reason)
            return
        self.archive_lineage(handle, reason)

    def try_resume(self, handle: ProviderSessionHandle) -> ProviderSessionHandle | None:
        if not self.backend.resume_stored_thread(handle.provider_thread_id):
            prior = self._descriptors.get(handle.provider_thread_id)
            self._descriptors[handle.provider_thread_id] = ProviderThreadCustodyDescriptor(
                schema_version=ProviderThreadCustodyDescriptor.SCHEMA_VERSION,
                provider_thread_id_sha256=text_sha256(handle.provider_thread_id),
                parent_provider_thread_id_sha256=(
                    prior.parent_provider_thread_id_sha256 if prior is not None else None
                ),
                storage_mode=ProviderThreadStorageMode.STORED_LOCAL,
                role=prior.role if prior is not None else ProviderThreadRole.ROOT,
                raw_context_retained=False,
                provider_context_is_story_authority=False,
            )
            self.operations.append(("thread/resume:missing", handle.handle_sha256))
            return None
        self._descriptors.setdefault(
            handle.provider_thread_id,
            ProviderThreadCustodyDescriptor(
                schema_version=ProviderThreadCustodyDescriptor.SCHEMA_VERSION,
                provider_thread_id_sha256=text_sha256(handle.provider_thread_id),
                parent_provider_thread_id_sha256=None,
                storage_mode=ProviderThreadStorageMode.STORED_LOCAL,
                role=ProviderThreadRole.ROOT,
                raw_context_retained=True,
                provider_context_is_story_authority=False,
            ),
        )
        self.operations.append(("thread/resume:stored", handle.handle_sha256))
        return handle

    def describe_thread(self, handle: ProviderSessionHandle) -> ProviderThreadCustodyDescriptor:
        return self._descriptor_or_default(handle)

    def restore_descriptor(
        self,
        handle: ProviderSessionHandle,
        descriptor: ProviderThreadCustodyDescriptor,
    ) -> None:
        """Restore privacy-safe custody metadata after a CERA process restart."""

        if descriptor.provider_thread_id_sha256 != text_sha256(handle.provider_thread_id):
            raise StateConflictError("stored-thread descriptor changed provider identity")
        self._descriptors[handle.provider_thread_id] = descriptor

    def _handle(self, thread_id: str) -> ProviderSessionHandle:
        return ProviderSessionHandle(
            schema_version=ProviderSessionHandle.SCHEMA_VERSION,
            provider_session_id=self.backend.session_epoch_id,
            provider_thread_id=thread_id,
            provider_turn_id=None,
        )

    def _remember(
        self,
        handle: ProviderSessionHandle,
        *,
        parent: ProviderSessionHandle | None,
        role: ProviderThreadRole,
    ) -> None:
        self._descriptors[handle.provider_thread_id] = ProviderThreadCustodyDescriptor(
            schema_version=ProviderThreadCustodyDescriptor.SCHEMA_VERSION,
            provider_thread_id_sha256=text_sha256(handle.provider_thread_id),
            parent_provider_thread_id_sha256=(
                text_sha256(parent.provider_thread_id) if parent is not None else None
            ),
            storage_mode=ProviderThreadStorageMode.STORED_LOCAL,
            role=role,
            raw_context_retained=True,
            provider_context_is_story_authority=False,
        )

    def _remember_archived(
        self, handle: ProviderSessionHandle, *, role: ProviderThreadRole
    ) -> None:
        prior = self._descriptors.get(handle.provider_thread_id)
        self._descriptors[handle.provider_thread_id] = ProviderThreadCustodyDescriptor(
            schema_version=ProviderThreadCustodyDescriptor.SCHEMA_VERSION,
            provider_thread_id_sha256=text_sha256(handle.provider_thread_id),
            parent_provider_thread_id_sha256=(
                prior.parent_provider_thread_id_sha256 if prior is not None else None
            ),
            storage_mode=ProviderThreadStorageMode.STORED_LOCAL,
            role=role,
            raw_context_retained=True,
            provider_context_is_story_authority=False,
        )

    def _descriptor_or_default(
        self, handle: ProviderSessionHandle
    ) -> ProviderThreadCustodyDescriptor:
        descriptor = self._descriptors.get(handle.provider_thread_id)
        if descriptor is None:
            raise StateConflictError("provider thread custody is not registered")
        return descriptor

    def _assert_compatible(
        self,
        compatibility: ReasonerSessionCompatibility,
        reconstruction: SessionReconstructionBundle,
    ) -> None:
        if reconstruction.compatibility_sha256 != compatibility.compatibility_sha256:
            raise StateConflictError("stored-thread reconstruction is incompatible")
        if compatibility.base_instruction_sha256 != self.base_instruction_sha256:
            raise StateConflictError("stored-thread base instructions changed")
