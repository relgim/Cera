"""Fresh candidate-isolated lifecycle for ordinary Reader validation."""

from __future__ import annotations

from typing import Protocol

from cera.errors import ContractValidationError, StateConflictError
from cera.provider_dispatch_guard import (
    assert_provider_dispatch_allowed,
    is_external_provider_boundary,
)

from .contracts import (
    BoundReaderValidationV1,
    ReaderValidationCustodyV1,
    ReaderValidationRequestV1,
    ReaderVerdictV1,
)
from .prompting import SOL_READER_BASE_INSTRUCTIONS, SOL_READER_PROFILE


class ReaderBackendPort(Protocol):
    def start_fresh_thread(self, *, base_instructions: str, profile: str) -> str: ...

    def run_reader_once(
        self,
        *,
        thread_id: str,
        request: ReaderValidationRequestV1,
    ) -> ReaderVerdictV1: ...

    def archive(self, thread_id: str) -> None: ...

    def is_resumable(self, thread_id: str) -> bool: ...


class FreshSolReaderSession:
    def __init__(self, backend: ReaderBackendPort, thread_id: str) -> None:
        self._backend = backend
        self._thread_id = thread_id
        self._used = False
        self._archived = False

    def validate(
        self,
        request: ReaderValidationRequestV1,
        custody: ReaderValidationCustodyV1,
    ) -> BoundReaderValidationV1:
        assert_provider_dispatch_allowed(
            "reader_validation.session.validate",
            external_provider_boundary=is_external_provider_boundary(self._backend),
        )
        if self._used or self._archived:
            raise StateConflictError("candidate Sol Reader session is single-use")
        self._used = True
        verdict = self._backend.run_reader_once(
            thread_id=self._thread_id,
            request=request,
        )
        return BoundReaderValidationV1(
            request=request,
            custody=custody,
            verdict=verdict,
        )

    def archive_and_prove_nonresumable(self) -> None:
        if self._archived:
            raise StateConflictError("candidate Sol Reader was already archived")
        self._backend.archive(self._thread_id)
        self._archived = True
        if self._backend.is_resumable(self._thread_id):
            raise ContractValidationError("archived Sol Reader remains resumable")


class FreshSolReaderFactory:
    """Low-level one-shot lifecycle used only inside a claimed attempt owner.

    Constructing this factory is provider-free.  Active Pi runtime code must
    not call :meth:`validate` directly; the generic provider-stage Retry owner
    calls it only after its durable attempt and dispatch claims are established.
    """

    def __init__(self, backend: ReaderBackendPort) -> None:
        self._backend = backend

    def validate(
        self,
        request: ReaderValidationRequestV1,
        custody: ReaderValidationCustodyV1,
    ) -> BoundReaderValidationV1:
        thread_id = self._backend.start_fresh_thread(
            base_instructions=SOL_READER_BASE_INSTRUCTIONS,
            profile=SOL_READER_PROFILE,
        )
        session = FreshSolReaderSession(self._backend, thread_id)
        try:
            result = session.validate(request, custody)
        except BaseException as primary:
            try:
                session.archive_and_prove_nonresumable()
            except BaseException as cleanup:
                primary.add_note(
                    f"Sol Reader terminalization also failed: {type(cleanup).__name__}: {cleanup}"
                )
                raise primary from cleanup
            raise
        session.archive_and_prove_nonresumable()
        return result


__all__ = [
    "FreshSolReaderFactory",
    "FreshSolReaderSession",
    "ReaderBackendPort",
]
