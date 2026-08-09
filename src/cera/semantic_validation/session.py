"""Fresh candidate-isolated lifecycle for ordinary semantic validation."""

from __future__ import annotations

from typing import Protocol

from cera.errors import ContractValidationError, StateConflictError
from cera.provider_dispatch_guard import (
    assert_provider_dispatch_allowed,
    is_external_provider_boundary,
)

from .contracts import (
    BoundSemanticValidationV1,
    SemanticValidationCustodyV1,
    SemanticValidationRequestV1,
    SemanticValidationVerdictV1,
)
from .prompting import LUNA_VALIDATOR_BASE_INSTRUCTIONS, LUNA_VALIDATOR_PROFILE


class LunaValidatorBackendPort(Protocol):
    def start_fresh_thread(self, *, base_instructions: str, profile: str) -> str: ...

    def run_validator_once(
        self,
        *,
        thread_id: str,
        request: SemanticValidationRequestV1,
    ) -> SemanticValidationVerdictV1: ...

    def archive(self, thread_id: str) -> None: ...

    def is_resumable(self, thread_id: str) -> bool: ...


class FreshLunaValidatorSession:
    def __init__(self, backend: LunaValidatorBackendPort, thread_id: str) -> None:
        self._backend = backend
        self._thread_id = thread_id
        self._used = False
        self._archived = False

    def validate(
        self,
        request: SemanticValidationRequestV1,
        custody: SemanticValidationCustodyV1,
    ) -> BoundSemanticValidationV1:
        assert_provider_dispatch_allowed(
            "semantic_validation.session.validate",
            external_provider_boundary=is_external_provider_boundary(self._backend),
        )
        if self._used or self._archived:
            raise StateConflictError("candidate Luna Validator session is single-use")
        self._used = True
        verdict = self._backend.run_validator_once(
            thread_id=self._thread_id,
            request=request,
        )
        return BoundSemanticValidationV1(
            request=request,
            custody=custody,
            verdict=verdict,
        )

    def archive_and_prove_nonresumable(self) -> None:
        if self._archived:
            raise StateConflictError("candidate Luna Validator was already archived")
        self._backend.archive(self._thread_id)
        self._archived = True
        if self._backend.is_resumable(self._thread_id):
            raise ContractValidationError("archived Luna Validator remains resumable")


class FreshLunaValidatorFactory:
    """Run one isolated validation and terminalize its provider history."""

    def __init__(self, backend: LunaValidatorBackendPort) -> None:
        self._backend = backend

    def validate(
        self,
        request: SemanticValidationRequestV1,
        custody: SemanticValidationCustodyV1,
    ) -> BoundSemanticValidationV1:
        thread_id = self._backend.start_fresh_thread(
            base_instructions=LUNA_VALIDATOR_BASE_INSTRUCTIONS,
            profile=LUNA_VALIDATOR_PROFILE,
        )
        session = FreshLunaValidatorSession(self._backend, thread_id)
        try:
            result = session.validate(request, custody)
        except BaseException as primary:
            try:
                session.archive_and_prove_nonresumable()
            except BaseException as cleanup:
                primary.add_note(
                    "Luna Validator terminalization also failed: "
                    f"{type(cleanup).__name__}: {cleanup}"
                )
                raise primary from cleanup
            raise
        session.archive_and_prove_nonresumable()
        return result
