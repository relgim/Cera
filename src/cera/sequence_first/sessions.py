"""Provider-neutral session policies for the sequence-first Codex roles."""

from __future__ import annotations

from typing import Callable, Protocol

from cera.errors import ContractValidationError, StateConflictError

from .contracts import (
    ProviderReferenceScopeV1,
    SequenceDraftV1,
    SequenceFirstTurnSemanticInputV1,
    SequenceFirstValidatorInputV1,
    ValidatorDecisionV1,
)
from .prompting import (
    PLANNER_BASE_INSTRUCTIONS,
    PLANNER_PROFILE,
    VALIDATOR_BASE_INSTRUCTIONS,
    VALIDATOR_PROFILE,
    planner_turn_prompt,
    validator_candidate_prompt,
)


class PlannerThreadBackendPort(Protocol):
    def start_stored_thread(self, *, base_instructions: str, profile: str) -> str: ...

    def run_planner_turn(
        self,
        *,
        thread_id: str,
        prompt: str,
        reference_scope: ProviderReferenceScopeV1,
    ) -> SequenceDraftV1: ...

    def is_resumable(self, thread_id: str) -> bool: ...


class PersistentPlannerSession:
    """Install stable Planner instructions once, then submit only turn deltas."""

    def __init__(
        self,
        backend: PlannerThreadBackendPort,
        *,
        restored_thread_id: str | None = None,
        persist_thread_id: Callable[[str], None] | None = None,
    ) -> None:
        if restored_thread_id is not None and not restored_thread_id.strip():
            raise ContractValidationError("restored Planner thread identity is empty")
        self._backend = backend
        self._thread_id = restored_thread_id
        self._persist_thread_id = persist_thread_id

    @property
    def thread_id(self) -> str | None:
        return self._thread_id

    def plan(self, semantic_input: SequenceFirstTurnSemanticInputV1) -> SequenceDraftV1:
        if self._thread_id is None:
            thread_id = self._backend.start_stored_thread(
                base_instructions=PLANNER_BASE_INSTRUCTIONS,
                profile=PLANNER_PROFILE,
            )
            if not isinstance(thread_id, str) or not thread_id.strip():
                raise ContractValidationError(
                    "persistent Planner backend returned an empty thread identity"
                )
            if self._persist_thread_id is not None:
                self._persist_thread_id(thread_id)
            self._thread_id = thread_id
        elif not self._backend.is_resumable(self._thread_id):
            raise StateConflictError("persistent Planner thread is not resumable")
        return self._backend.run_planner_turn(
            thread_id=self._thread_id,
            prompt=planner_turn_prompt(semantic_input),
            reference_scope=ProviderReferenceScopeV1.from_turn(semantic_input),
        )


class ValidatorThreadBackendPort(Protocol):
    def start_fresh_thread(self, *, base_instructions: str, profile: str) -> str: ...

    def run_validator_once(
        self,
        *,
        thread_id: str,
        prompt: str,
        reference_scope: ProviderReferenceScopeV1,
        intended_sequence: SequenceDraftV1,
    ) -> ValidatorDecisionV1: ...

    def archive(self, thread_id: str) -> None: ...

    def is_resumable(self, thread_id: str) -> bool: ...


class FreshCandidateValidatorFactory:
    """Create exactly one fresh sequence-first Validator for each candidate."""

    def __init__(self, backend: ValidatorThreadBackendPort) -> None:
        self._backend = backend

    def create_sequence_first_validator(self) -> "FreshCandidateValidatorSession":
        thread_id = self._backend.start_fresh_thread(
            base_instructions=VALIDATOR_BASE_INSTRUCTIONS,
            profile=VALIDATOR_PROFILE,
        )
        return FreshCandidateValidatorSession(self._backend, thread_id)


class FreshCandidateValidatorSession:
    def __init__(self, backend: ValidatorThreadBackendPort, thread_id: str) -> None:
        self._backend = backend
        self._thread_id = thread_id
        self._used = False
        self._archived = False

    def validate(
        self,
        request: SequenceFirstValidatorInputV1,
    ) -> ValidatorDecisionV1:
        if self._used or self._archived:
            raise StateConflictError("candidate Validator session is single-use")
        self._used = True
        return self._backend.run_validator_once(
            thread_id=self._thread_id,
            prompt=validator_candidate_prompt(request),
            reference_scope=request.reference_scope,
            intended_sequence=request.intended_sequence,
        )

    def archive_and_prove_nonresumable(self) -> None:
        if self._archived:
            raise StateConflictError("candidate Validator was already archived")
        self._backend.archive(self._thread_id)
        self._archived = True
        if self._backend.is_resumable(self._thread_id):
            raise ContractValidationError(
                "archived candidate Validator remains resumable"
            )
