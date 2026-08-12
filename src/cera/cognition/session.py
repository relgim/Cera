"""Persistent-session policy for one branch-bound cognition owner."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from cera.errors import ContractValidationError, StateConflictError
from cera.provider_dispatch_guard import is_external_provider_boundary
from cera.sequence_first.contracts import ProviderReferenceScopeV1
from cera.serialization import canonical_sha256, re_is_sha256, text_sha256

from .citations import (
    CognitionProviderCompletedTurnV1,
    CognitionProviderTurnHandoffV1,
)
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
    ) -> CognitionProviderCompletedTurnV1: ...

    def is_resumable(self, thread_id: str) -> bool: ...

    def clear_cognition_transient_state(self) -> None: ...


class PersistentCognitionPlannerSession:
    """Install stable cognition instructions once, then send turn deltas."""

    def __init__(
        self,
        backend: CognitionPlannerThreadBackendPort,
        *,
        restored_thread_id: str | None = None,
        persist_thread_id: Callable[[str], None] | None = None,
        archive_interrupted_thread: Callable[[str], None] | None = None,
        verify_interrupted_thread: Callable[[str], bool] | None = None,
        archive_completed_uncommitted_thread: Callable[[str], None] | None = None,
        verify_completed_uncommitted_thread: Callable[[str], bool] | None = None,
    ) -> None:
        if restored_thread_id is not None and not restored_thread_id.strip():
            raise ContractValidationError("restored cognition thread identity is empty")
        self._backend = backend
        self._thread_id = restored_thread_id
        self._persist_thread_id = persist_thread_id
        self._archive_interrupted_thread = archive_interrupted_thread
        self._verify_interrupted_thread = verify_interrupted_thread
        self._archive_completed_uncommitted_thread = archive_completed_uncommitted_thread
        self._verify_completed_uncommitted_thread = verify_completed_uncommitted_thread
        self._last_available_evidence_refs: tuple[str, ...] = ()
        self._last_provider_turn_handoff: CognitionProviderTurnHandoffV1 | None = None

    @property
    def thread_id(self) -> str | None:
        return self._thread_id

    @property
    def external_provider_boundary(self) -> bool:
        return is_external_provider_boundary(self._backend)

    @property
    def last_available_evidence_refs(self) -> tuple[str, ...]:
        return self._last_available_evidence_refs

    @property
    def last_provider_turn_handoff(self) -> CognitionProviderTurnHandoffV1 | None:
        return self._last_provider_turn_handoff

    def take_provider_turn_handoff(self) -> CognitionProviderTurnHandoffV1:
        handoff = self._last_provider_turn_handoff
        self._last_provider_turn_handoff = None
        self._last_available_evidence_refs = ()
        if handoff is None:
            raise StateConflictError("cognition provider-turn handoff is unavailable")
        return handoff

    @property
    def active_thread_sha256(self) -> str | None:
        """Return the exact active retained-thread identity without provider work."""

        return None if self._thread_id is None else text_sha256(self._thread_id)

    def plan(self, context: CognitionTurnContextV1) -> CognitionPlanV1:
        self._clear_transient_result()
        if self._thread_id is None:
            self.prepare_fresh_thread()
        elif not self._backend.is_resumable(self._thread_id):
            raise StateConflictError("persistent cognition thread is not resumable")
        assert self._thread_id is not None
        reference_scope = ProviderReferenceScopeV1.from_turn(context.turn)
        completed = self._backend.run_cognition_turn(
            thread_id=self._thread_id,
            prompt=cognition_turn_prompt(context),
            context=context,
            reference_scope=reference_scope,
        )
        if not isinstance(completed, CognitionProviderCompletedTurnV1):
            raise StateConflictError("cognition backend completion changed shape")
        plan = completed.plan
        handoff = completed.handoff
        dynamic = tuple(
            dict.fromkeys(
                (
                    *reference_scope.evidence_keys,
                    *(value.evidence_ref for value in handoff.selected_evidence),
                )
            )
        )
        if dynamic[: len(reference_scope.evidence_keys)] != reference_scope.evidence_keys:
            raise StateConflictError("cognition backend evidence scope is not append-only")
        if (
            handoff.turn_semantic_sha256 != canonical_sha256(context.turn)
            or handoff.plan_semantic_sha256 != plan.semantic_sha256
            or handoff.provider_thread_sha256 != text_sha256(self._thread_id)
        ):
            raise StateConflictError("cognition provider-turn handoff changed request custody")
        self._last_available_evidence_refs = dynamic
        self._last_provider_turn_handoff = handoff
        return plan

    def reset_after_transport_failure(self, expected_thread_sha256: str) -> None:
        """Archive an interrupted soft thread before a manual retry.

        This method performs no provider operation.  The next ``plan`` call
        creates a fresh thread and rehydrates from Python-owned branch state.
        """

        self._clear_transient_result()
        if not re_is_sha256(expected_thread_sha256):
            raise ContractValidationError("cognition interrupted thread hash is invalid")
        thread_id = self._thread_id
        if thread_id is None:
            verifier = self._verify_interrupted_thread
            if verifier is None or not verifier(expected_thread_sha256):
                raise StateConflictError("cognition interrupted thread lacks archived proof")
            return
        if text_sha256(thread_id) != expected_thread_sha256:
            raise StateConflictError(
                "cognition active thread differs from failed provider evidence"
            )
        if self._archive_interrupted_thread is None:
            raise StateConflictError("cognition transport retry lacks interrupted-thread archival")
        self._archive_interrupted_thread(thread_id)
        self._thread_id = None

    def prepare_fresh_thread(self) -> str:
        """Create and persist one empty retained thread without a model turn.

        The caller uses this only after the interrupted thread has been
        durably archived. Starting the provider lifecycle thread may perform
        external lifecycle transport, but it runs no model turn and appends no
        Sol model-operation ledger event. The later ``plan`` call owns the one
        charged manual provider dispatch.
        """

        self._clear_transient_result()
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
        return text_sha256(self._thread_id)

    def abandon_completed_uncommitted(self, expected_thread_sha256: str) -> None:
        """Retire a completed Planner thread whose result never became durable."""

        self._clear_transient_result()
        if not re_is_sha256(expected_thread_sha256):
            raise ContractValidationError("cognition completed thread hash is invalid")
        thread_id = self._thread_id
        if thread_id is None:
            verifier = self._verify_completed_uncommitted_thread
            if verifier is None or not verifier(expected_thread_sha256):
                raise StateConflictError("cognition completed thread lacks archived proof")
            return
        if text_sha256(thread_id) != expected_thread_sha256:
            raise StateConflictError("cognition active thread differs from completed evidence")
        archive = self._archive_completed_uncommitted_thread
        if archive is None:
            raise StateConflictError("cognition completed thread lacks retirement custody")
        archive(thread_id)
        self._thread_id = None

    def _clear_transient_result(self) -> None:
        self._last_available_evidence_refs = ()
        self._last_provider_turn_handoff = None
        clear_backend = getattr(self._backend, "clear_cognition_transient_state", None)
        if not callable(clear_backend):
            raise StateConflictError("cognition backend lacks transient clearing")
        clear_backend()
