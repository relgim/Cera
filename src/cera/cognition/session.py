"""Persistent-session policy for one branch-bound cognition owner."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from cera.errors import ContractValidationError, StateConflictError
from cera.provider_dispatch_guard import is_external_provider_boundary
from cera.sequence_first.contracts import ProviderReferenceScopeV1

from .contracts import CognitionPlanV1, CognitionTurnContextV1
from .prompting import (
    COGNITION_PLANNER_BASE_INSTRUCTIONS,
    COGNITION_PLANNER_PROFILE,
    cognition_turn_prompt,
)


class CognitionPlannerThreadBackendPort(Protocol):
    def start_stored_thread(self, *, base_instructions: str, profile: str) -> str: ...

    def run_cognition_turn(
        self,
        *,
        thread_id: str,
        prompt: str,
        context: CognitionTurnContextV1,
        reference_scope: ProviderReferenceScopeV1,
    ) -> CognitionPlanV1: ...

    def is_resumable(self, thread_id: str) -> bool: ...


class PersistentCognitionPlannerSession:
    """Install stable cognition instructions once, then send turn deltas."""

    def __init__(
        self,
        backend: CognitionPlannerThreadBackendPort,
        *,
        restored_thread_id: str | None = None,
        persist_thread_id: Callable[[str], None] | None = None,
    ) -> None:
        if restored_thread_id is not None and not restored_thread_id.strip():
            raise ContractValidationError("restored cognition thread identity is empty")
        self._backend = backend
        self._thread_id = restored_thread_id
        self._persist_thread_id = persist_thread_id
        self._last_available_evidence_refs: tuple[str, ...] = ()

    @property
    def thread_id(self) -> str | None:
        return self._thread_id

    @property
    def external_provider_boundary(self) -> bool:
        return is_external_provider_boundary(self._backend)

    @property
    def last_available_evidence_refs(self) -> tuple[str, ...]:
        return self._last_available_evidence_refs

    def plan(self, context: CognitionTurnContextV1) -> CognitionPlanV1:
        if self._thread_id is None:
            thread_id = self._backend.start_stored_thread(
                base_instructions=COGNITION_PLANNER_BASE_INSTRUCTIONS,
                profile=COGNITION_PLANNER_PROFILE,
            )
            if not isinstance(thread_id, str) or not thread_id.strip():
                raise ContractValidationError("cognition backend returned an empty thread identity")
            if self._persist_thread_id is not None:
                self._persist_thread_id(thread_id)
            self._thread_id = thread_id
        elif not self._backend.is_resumable(self._thread_id):
            raise StateConflictError("persistent cognition thread is not resumable")
        reference_scope = ProviderReferenceScopeV1.from_turn(context.turn)
        plan = self._backend.run_cognition_turn(
            thread_id=self._thread_id,
            prompt=cognition_turn_prompt(context),
            context=context,
            reference_scope=reference_scope,
        )
        dynamic = getattr(
            self._backend,
            "last_available_evidence_refs",
            reference_scope.evidence_keys,
        )
        if not isinstance(dynamic, tuple) or any(not isinstance(value, str) for value in dynamic):
            raise StateConflictError("cognition backend evidence scope changed shape")
        if not dynamic:
            dynamic = reference_scope.evidence_keys
        if dynamic[: len(reference_scope.evidence_keys)] != reference_scope.evidence_keys:
            raise StateConflictError("cognition backend evidence scope is not append-only")
        self._last_available_evidence_refs = dynamic
        return plan
