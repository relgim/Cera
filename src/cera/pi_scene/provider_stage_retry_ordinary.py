"""Ordinary-pipeline integration for stage-local provider Retry custody.

This module binds an exact HTTP request occurrence to the Planner, Writer,
Semantic Validator, and Recorder stages.  It never owns a retry budget: the
injected :class:`ProviderStageRetryRuntimeServiceV1` is the sole authority for
the three-attempt/two-action policy.

The public result codecs are also the assembly seam for lazy provider owners.
They reconstruct requests only from protected frozen packets and serialize
successful results before any downstream pipeline work is resumed.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from cera.errors import ContractValidationError, StateConflictError
from cera.generated.provider_stage_retry_contracts_v1 import (
    ProviderStageRetryStatusEnvelopeV1,
    validate_provider_stage_retry_action_v1,
)
from cera.schema import from_mapping
from cera.semantic_validation import (
    BoundSemanticValidationV1,
    SemanticValidationCustodyV1,
    SemanticValidationRequestV1,
    SemanticVerdict,
)
from cera.serialization import (
    canonical_bytes,
    canonical_sha256,
    domain_sha256,
    re_is_sha256,
    to_primitive,
)

from .contracts import LeanAcceptedTurnReceiptV1, PiWriterReceiptV1, SceneRoute
from .http_contracts import LeanSceneRequestControlsV1, LeanSceneRequestControlsV2
from .pi_adapter import PiSceneInvocationResultV1, PiSceneInvocationV1
from .provider_stage_retry import (
    ProviderStage,
    ProviderStageRetryPhase,
)
from .provider_stage_retry_executor import ProviderStageSemanticDisposition
from .provider_stage_retry_ordinary_custody import (
    ProtectedOrdinaryChainRequestIdentityV1,
    ProtectedOrdinaryCustodyReceiptV1,
    ProtectedOrdinaryReviewRequestIdentityV1,
    ProtectedOrdinaryStageRetryCustodyStoreV1,
    ProtectedOrdinaryTerminalResponseReceiptV1,
    ProtectedRecorderContinuationV1,
)
from .provider_stage_retry_packets import (
    ImmutableRetrievalSnapshotIdentityV1,
    ProviderStageConfigurationV1,
    ProviderStageFrozenPacketV1,
    freeze_planner_stage_packet,
    freeze_recorder_stage_packet,
    freeze_semantic_validator_stage_packet,
    freeze_writer_stage_packet,
)
from .provider_stage_retry_runtime import ProviderStageRetryRuntimeServiceV1
from .provider_stage_retry_scope import ProviderStageRetryOccurrenceScopeV1
from .request_binding import PiSceneRequestBindingV1
from .review_store import CreatorGuidanceV1, LeanReviewRecordV1, LeanSceneTurnInputV1
from .runtime import (
    OrdinaryWriterCandidateOccurrenceV1,
    PlannerTurnInputV1,
    PlannerTurnOutputV1,
    ProviderStageRetryPendingError,
)
from .store import AcceptedPiSessionV1
from .writer_view import verify_writer_view

_ORDINARY_STAGES = frozenset(
    {
        ProviderStage.PLANNER,
        ProviderStage.WRITER,
        ProviderStage.SEMANTIC_VALIDATOR,
        ProviderStage.RECORDER,
    }
)
_RESULT_READY_PHASES = frozenset(
    {
        ProviderStageRetryPhase.RESULT_FROZEN,
        ProviderStageRetryPhase.DOWNSTREAM_INTENT_FROZEN,
        ProviderStageRetryPhase.DOWNSTREAM_BOUND,
        ProviderStageRetryPhase.SUCCEEDED,
    }
)


def _require_sha256(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not re_is_sha256(value):
        raise ContractValidationError(f"ordinary provider-stage Retry {field_name} must be SHA-256")


def _canonical_object(exact_bytes: bytes, field_name: str) -> dict[str, Any]:
    try:
        value: object = json.loads(exact_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractValidationError(
            f"ordinary provider-stage {field_name} is not canonical JSON"
        ) from exc
    if not isinstance(value, dict) or canonical_bytes(value) != exact_bytes:
        raise ContractValidationError(
            f"ordinary provider-stage {field_name} changed canonical bytes"
        )
    return value


def _exact_keys(value: Mapping[str, Any], expected: set[str], field_name: str) -> None:
    if set(value) != expected:
        raise ContractValidationError(
            f"ordinary provider-stage {field_name} changed its closed shape"
        )


def _mapping(value: object, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ContractValidationError(f"ordinary provider-stage {field_name} must be an object")
    return value


def _packet_semantic_input(
    exact_input: bytes,
    *,
    stage: ProviderStage,
    packet_kind: str,
) -> Mapping[str, Any]:
    packet = _canonical_object(exact_input, f"{packet_kind} packet")
    _exact_keys(
        packet,
        {
            "schema_version",
            "stage",
            "packet_kind",
            "provider_configuration",
            "semantic_input",
        },
        f"{packet_kind} packet",
    )
    if (
        packet["schema_version"] != ProviderStageFrozenPacketV1.SCHEMA_VERSION
        or packet["stage"] != stage.value
        or packet["packet_kind"] != packet_kind
    ):
        raise ContractValidationError(
            f"ordinary provider-stage {packet_kind} packet identity changed"
        )
    return _mapping(packet["semantic_input"], f"{packet_kind} semantic input")


@dataclass(frozen=True, slots=True)
class PlannerFrozenRetrievalV1:
    """Immutable Planner retrieval identity and already-bound tool evidence."""

    retrieval_snapshot: ImmutableRetrievalSnapshotIdentityV1
    tool_result_bundle: object

    def __post_init__(self) -> None:
        if type(self.retrieval_snapshot) is not ImmutableRetrievalSnapshotIdentityV1:
            raise ContractValidationError("Planner frozen retrieval identity changed")
        # Reject objects that cannot be preserved in an exact canonical packet.
        canonical_sha256(self.tool_result_bundle)


@dataclass(frozen=True, slots=True)
class OrdinaryStageRetryRequestContextV1:
    """Exact request/generation binding shared by one ordinary pipeline run."""

    binding: PiSceneRequestBindingV1
    generation: int
    turn_input: LeanSceneTurnInputV1
    planner_retrieval: PlannerFrozenRetrievalV1

    def __post_init__(self) -> None:
        if type(self.binding) is not PiSceneRequestBindingV1:
            raise ContractValidationError("ordinary Retry HTTP request binding changed")
        if type(self.generation) is not int or self.generation < 1:
            raise ContractValidationError("ordinary Retry generation is invalid")
        if type(self.turn_input) is not LeanSceneTurnInputV1:
            raise ContractValidationError("ordinary Retry turn input changed")
        if (self.turn_input.world_id, self.turn_input.branch_id) != (
            self.binding.world_id,
            self.binding.branch_id,
        ) or canonical_sha256(
            to_primitive(self.turn_input.request_controls)
        ) != self.binding.controls_sha256:
            raise StateConflictError("ordinary Retry turn changed HTTP custody")
        if type(self.planner_retrieval) is not PlannerFrozenRetrievalV1:
            raise ContractValidationError("ordinary Retry Planner retrieval binding changed")

    @property
    def generation_id(self) -> str:
        return f"generation-{self.generation:08d}"

    @property
    def binding_sha256(self) -> str:
        return canonical_sha256(self.binding.to_payload())

    @property
    def turn_context_sha256(self) -> str:
        return canonical_sha256(self.turn_input)

    def context_payload(self) -> dict[str, Any]:
        return {
            "schema_version": "cera.ordinary_stage_retry_request_context.v1",
            "request_binding_sha256": self.binding_sha256,
            "generation": self.generation,
            "turn_context": to_primitive(self.turn_input),
            "turn_context_sha256": self.turn_context_sha256,
            "retrieval_snapshot": self.planner_retrieval.retrieval_snapshot.to_payload(),
            "tool_result_bundle": to_primitive(self.planner_retrieval.tool_result_bundle),
        }


@dataclass(slots=True)
class _BoundRequestState:
    context: OrdinaryStageRetryRequestContextV1
    stage_occurrences: dict[ProviderStage, int] = field(default_factory=dict)
    semantic_action_rebind: bool = False
    semantic_action_boundary_consumed: bool = False

    def next_stage_ordinal(self, stage: ProviderStage) -> int:
        ordinal = self.stage_occurrences.get(stage, 0) + 1
        self.stage_occurrences[stage] = ordinal
        return ordinal

    def consume_semantic_action_boundary(self) -> bool:
        if not self.semantic_action_rebind or self.semantic_action_boundary_consumed:
            return False
        self.semantic_action_boundary_consumed = True
        return True


@dataclass(frozen=True, slots=True)
class ProviderStageActionProjectionV1:
    """Safe result of one authenticated generic Retry UI action."""

    chain_id: str
    stage: ProviderStage
    result_ready: bool
    envelope: ProviderStageRetryStatusEnvelopeV1


@runtime_checkable
class OrdinaryPipelineReplayPort(Protocol):
    """Provider-free replay of a protected ordinary HTTP request."""

    def resume_original_request(
        self,
        *,
        normalized_request: Mapping[str, Any],
        turn_input: LeanSceneTurnInputV1,
    ) -> object: ...


@runtime_checkable
class OrdinaryRecorderContinuationPort(Protocol):
    """Recorder-only recovery against an already accepted durable review."""

    def durable_result_for_request(
        self,
        *,
        world_id: str,
        branch_id: str,
        turn_context_sha256: str,
        exact_user_source: str,
        request_controls: LeanSceneRequestControlsV1 | LeanSceneRequestControlsV2,
    ) -> LeanReviewRecordV1 | None: ...

    def repair_recording(self, review_id: str) -> object: ...


class OrdinaryPipelineContinuationPort(
    OrdinaryPipelineReplayPort,
    OrdinaryRecorderContinuationPort,
    Protocol,
):
    """Full continuation seam implemented by the future HTTP adapter."""


class OrdinaryProviderStageRetryRuntimeV1:
    """Apply the sole generic Retry authority to the four ordinary stages."""

    def __init__(
        self,
        *,
        service: ProviderStageRetryRuntimeServiceV1,
        custody_store: ProtectedOrdinaryStageRetryCustodyStoreV1,
        configurations: Mapping[ProviderStage, ProviderStageConfigurationV1],
    ) -> None:
        if type(service) is not ProviderStageRetryRuntimeServiceV1:
            raise ContractValidationError("ordinary provider-stage Retry service changed")
        if type(custody_store) is not ProtectedOrdinaryStageRetryCustodyStoreV1:
            raise ContractValidationError("ordinary protected request custody changed")
        selected: dict[ProviderStage, ProviderStageConfigurationV1] = {}
        for stage in _ORDINARY_STAGES:
            configuration = configurations.get(stage)
            if (
                type(configuration) is not ProviderStageConfigurationV1
                or configuration.stage is not stage
            ):
                raise ContractValidationError(
                    f"ordinary provider-stage Retry lacks {stage.value} configuration"
                )
            selected[stage] = configuration
        self._service = service
        self._custody_store = custody_store
        self._configurations = selected
        self._bound_request: ContextVar[_BoundRequestState | None] = ContextVar(
            f"cera_ordinary_stage_retry_{id(self):x}",
            default=None,
        )

    @contextmanager
    def bind_request(
        self,
        context: OrdinaryStageRetryRequestContextV1,
        *,
        prior_stage_occurrences: Mapping[ProviderStage, int] | None = None,
    ) -> Iterator[None]:
        """Bind one exact HTTP request while no provider work is performed."""

        if type(context) is not OrdinaryStageRetryRequestContextV1:
            raise ContractValidationError("ordinary Retry request context changed")
        if self._bound_request.get() is not None:
            raise StateConflictError("ordinary Retry request context is already occupied")
        occurrences: dict[ProviderStage, int] = {}
        if prior_stage_occurrences is not None:
            if set(prior_stage_occurrences) != _ORDINARY_STAGES:
                raise ContractValidationError(
                    "ordinary Retry prior stage occurrences changed shape"
                )
            for stage, count in prior_stage_occurrences.items():
                if (
                    stage not in _ORDINARY_STAGES
                    or type(stage) is not ProviderStage
                    or type(count) is not int
                    or count < 0
                ):
                    raise ContractValidationError(
                        "ordinary Retry prior stage occurrence is invalid"
                    )
                occurrences[stage] = count
            verified = self._custody_store.require_review_stage_occurrences(
                request_id=context.binding.request_id,
                stage_occurrence_counts={
                    stage.value: occurrences[stage] for stage in _ORDINARY_STAGES
                },
            )
            if verified != {stage.value: occurrences[stage] for stage in _ORDINARY_STAGES}:
                raise StateConflictError("ordinary Retry prior stage occurrences changed custody")
        token = self._bound_request.set(
            _BoundRequestState(
                context=context,
                stage_occurrences=occurrences,
                semantic_action_rebind=prior_stage_occurrences is not None,
            )
        )
        try:
            yield
        finally:
            self._bound_request.reset(token)

    def freeze_pending_request(
        self,
        *,
        normalized_request: Mapping[str, Any],
        context: OrdinaryStageRetryRequestContextV1,
    ) -> ProtectedOrdinaryCustodyReceiptV1:
        """Freeze exact protected HTTP/context material before any stage call."""

        if type(context) is not OrdinaryStageRetryRequestContextV1:
            raise ContractValidationError("ordinary Retry request context changed")
        return self._custody_store.freeze_request(
            normalized_request=normalized_request,
            binding=context.binding,
            generation=context.generation,
            turn_input=context.turn_input,
            retrieval_snapshot=context.planner_retrieval.retrieval_snapshot,
            tool_result_bundle=context.planner_retrieval.tool_result_bundle,
        )

    def load_pending_request_for_scope(
        self,
        scope: ProviderStageRetryOccurrenceScopeV1,
    ) -> tuple[dict[str, Any], OrdinaryStageRetryRequestContextV1]:
        """Recover exact protected local replay material after restart."""

        pending = self._custody_store.load_for_scope(scope)
        context = OrdinaryStageRetryRequestContextV1(
            binding=pending.binding,
            generation=pending.generation,
            turn_input=pending.turn_input,
            planner_retrieval=PlannerFrozenRetrievalV1(
                retrieval_snapshot=pending.retrieval_snapshot,
                tool_result_bundle=pending.tool_result_bundle,
            ),
        )
        return dict(pending.normalized_request), context

    def redact_pending_request(
        self,
        request_id: str,
        *,
        disposition: str,
        terminal_evidence_sha256: str,
    ) -> ProtectedOrdinaryCustodyReceiptV1:
        return self._custody_store.redact_request(
            request_id,
            disposition=disposition,
            terminal_evidence_sha256=terminal_evidence_sha256,
        )

    def pending_request_for_chain(
        self,
        chain_id: str,
    ) -> tuple[dict[str, Any], OrdinaryStageRetryRequestContextV1]:
        pending = self._custody_store.load_request_for_chain(chain_id)
        return dict(pending.normalized_request), OrdinaryStageRetryRequestContextV1(
            binding=pending.binding,
            generation=pending.generation,
            turn_input=pending.turn_input,
            planner_retrieval=PlannerFrozenRetrievalV1(
                retrieval_snapshot=pending.retrieval_snapshot,
                tool_result_bundle=pending.tool_result_bundle,
            ),
        )

    def bind_review_request(
        self,
        review: LeanReviewRecordV1,
    ) -> ProtectedOrdinaryReviewRequestIdentityV1:
        """Bind one unresolved review to the exact current protected request."""

        state = self._bound_request.get()
        if state is None:
            raise StateConflictError("ordinary review lacks bound request custody")
        counts = {stage.value: state.stage_occurrences.get(stage, 0) for stage in _ORDINARY_STAGES}
        return self._custody_store.bind_review_request(
            review=review,
            request_id=state.context.binding.request_id,
            context_sha256=canonical_sha256(state.context.context_payload()),
            stage_occurrence_counts=counts,
        )

    def pending_request_for_review(
        self,
        review_id: str,
    ) -> tuple[
        dict[str, Any],
        OrdinaryStageRetryRequestContextV1,
        Mapping[ProviderStage, int],
    ]:
        """Recover Request A and its exact action-stage ordinal base."""

        identity, counts = self._custody_store.load_review_request(review_id)
        pending = self._custody_store.load_request(identity.request_id)
        if pending.context_sha256 != identity.context_sha256:
            raise StateConflictError("ordinary review changed protected request context")
        context = OrdinaryStageRetryRequestContextV1(
            binding=pending.binding,
            generation=pending.generation,
            turn_input=pending.turn_input,
            planner_retrieval=PlannerFrozenRetrievalV1(
                retrieval_snapshot=pending.retrieval_snapshot,
                tool_result_bundle=pending.tool_result_bundle,
            ),
        )
        return (
            dict(pending.normalized_request),
            context,
            {ProviderStage(stage): count for stage, count in counts.items()},
        )

    def retire_review_request(
        self,
        review_id: str,
        *,
        terminal_evidence_sha256: str,
    ) -> ProtectedOrdinaryCustodyReceiptV1:
        return self._custody_store.retire_review_request(
            review_id,
            terminal_evidence_sha256=terminal_evidence_sha256,
        )

    def latest_chain_for_chain(
        self,
        source_chain_id: str,
    ) -> ProtectedOrdinaryChainRequestIdentityV1:
        return self._custody_store.latest_chain_for_chain(source_chain_id)

    def chain_request_identity(
        self,
        chain_id: str,
    ) -> ProtectedOrdinaryChainRequestIdentityV1:
        return self._custody_store.chain_request_identity(chain_id)

    def branch_barrier(
        self,
        *,
        world_id: str,
        branch_id: str,
    ) -> ProtectedOrdinaryCustodyReceiptV1 | None:
        return self._custody_store.branch_barrier(
            world_id=world_id,
            branch_id=branch_id,
        )

    def release_branch_barrier(
        self,
        request_id: str,
        *,
        recovery_evidence_sha256: str,
    ) -> ProtectedOrdinaryCustodyReceiptV1:
        return self._custody_store.release_branch_barrier(
            request_id,
            recovery_evidence_sha256=recovery_evidence_sha256,
        )

    def bind_terminal_response(
        self,
        *,
        request_id: str,
        response: Mapping[str, Any],
    ) -> ProtectedOrdinaryTerminalResponseReceiptV1:
        return self._custody_store.bind_terminal_response(
            request_id=request_id,
            response=response,
        )

    def load_terminal_response(
        self,
        request_id: str,
    ) -> tuple[dict[str, Any], ProtectedOrdinaryTerminalResponseReceiptV1]:
        return self._custody_store.load_terminal_response(request_id)

    def load_terminal_response_optional(
        self,
        request_id: str,
    ) -> tuple[dict[str, Any], ProtectedOrdinaryTerminalResponseReceiptV1] | None:
        return self._custody_store.load_terminal_response_optional(request_id)

    def load_terminal_response_for_chain(
        self,
        chain_id: str,
    ) -> tuple[dict[str, Any], ProtectedOrdinaryTerminalResponseReceiptV1]:
        return self._custody_store.load_terminal_response_for_chain(chain_id)

    def bind_terminal_response_for_chain(
        self,
        *,
        chain_id: str,
        response: Mapping[str, Any],
    ) -> ProtectedOrdinaryTerminalResponseReceiptV1:
        return self._custody_store.bind_terminal_response_for_chain(
            chain_id=chain_id,
            response=response,
        )

    def resume_succeeded_chain(
        self,
        chain_id: str,
        *,
        continuation: OrdinaryPipelineReplayPort | OrdinaryRecorderContinuationPort,
    ) -> object:
        """Resume only after a manual action has produced a frozen stage result."""

        chain = self._service.read_chain(chain_id)
        if chain.identity.stage not in _ORDINARY_STAGES or chain.phase not in (
            _RESULT_READY_PHASES
        ):
            raise StateConflictError("ordinary provider-stage result is not resumable")
        normalized_request, context = self.pending_request_for_chain(chain_id)
        if chain.identity.stage is not ProviderStage.RECORDER:
            if not isinstance(continuation, OrdinaryPipelineReplayPort):
                raise StateConflictError("ordinary stage continuation lacks request replay")
            with self.bind_request(context):
                return continuation.resume_original_request(
                    normalized_request=normalized_request,
                    turn_input=context.turn_input,
                )

        if not isinstance(continuation, OrdinaryRecorderContinuationPort):
            raise StateConflictError("Recorder continuation port is unavailable")
        controls = context.turn_input.request_controls
        if not isinstance(controls, (LeanSceneRequestControlsV1, LeanSceneRequestControlsV2)):
            raise StateConflictError("Recorder continuation lost request controls")
        review = continuation.durable_result_for_request(
            world_id=context.binding.world_id,
            branch_id=context.binding.branch_id,
            turn_context_sha256=context.turn_context_sha256,
            exact_user_source=context.turn_input.exact_user_source,
            request_controls=controls,
        )
        if review is None or review.accepted_receipt is None:
            raise StateConflictError("Recorder continuation lacks its accepted review")
        # A process may stop after the accepted review commit and before the
        # advisory chain->review receipt is written. The frozen turn and
        # durable selected review remain the recovery authority.
        bound = self._custody_store.find_recorder_continuation(chain_id)
        if bound is not None and (
            bound.review_id != review.review_id
            or bound.accepted_turn_id != review.accepted_receipt.accepted_turn_id
            or bound.accepted_receipt_sha256 != review.accepted_receipt.receipt_sha256
        ):
            raise StateConflictError("Recorder continuation changed accepted review")
        with self.bind_request(context):
            return continuation.repair_recording(review.review_id)

    def bind_recorder_pending(
        self,
        *,
        chain_id: str,
        review_id: str,
        accepted: LeanAcceptedTurnReceiptV1,
    ) -> None:
        """Bind a committed accepted review to Recorder-only continuation."""

        state = self._bound_request.get()
        if state is None:
            raise StateConflictError("Recorder continuation lacks HTTP request custody")
        if (
            accepted.world_id != state.context.binding.world_id
            or accepted.branch_id != state.context.binding.branch_id
        ):
            raise StateConflictError("Recorder continuation changed accepted branch")
        self._custody_store.bind_recorder_continuation(
            ProtectedRecorderContinuationV1(
                chain_id=chain_id,
                request_id=state.context.binding.request_id,
                review_id=review_id,
                accepted_turn_id=accepted.accepted_turn_id,
                accepted_receipt_sha256=accepted.receipt_sha256,
            )
        )

    def recorder_continuation(self, chain_id: str) -> ProtectedRecorderContinuationV1:
        return self._custody_store.load_recorder_continuation(chain_id)

    def find_recorder_continuation(
        self,
        chain_id: str,
    ) -> ProtectedRecorderContinuationV1 | None:
        return self._custody_store.find_recorder_continuation(chain_id)

    def run_planner(
        self,
        request: PlannerTurnInputV1,
        *,
        accepted_state_sha256: str,
        authority_binding: object,
    ) -> PlannerTurnOutputV1:
        state = self._state_for(
            world_id=request.world_id,
            branch_id=request.branch_id,
        )
        packet = freeze_planner_stage_packet(
            normalized_messages=to_primitive(request),
            accepted_state={"accepted_state_sha256": accepted_state_sha256},
            retrieval_snapshot=state.context.planner_retrieval.retrieval_snapshot,
            tool_result_bundle=state.context.planner_retrieval.tool_result_bundle,
            configuration=self._configurations[ProviderStage.PLANNER],
        )
        exact_result = self._run_stage(
            state=state,
            packet=packet,
            accepted_state_sha256=accepted_state_sha256,
            authority_binding=authority_binding,
        )
        return deserialize_planner_result(exact_result)

    def run_writer(
        self,
        invocation: PiSceneInvocationV1,
        *,
        candidate_occurrence: OrdinaryWriterCandidateOccurrenceV1,
        world_id: str,
        branch_id: str,
        accepted_state_sha256: str,
        authority_binding: object,
    ) -> PiSceneInvocationResultV1:
        state = self._state_for(world_id=world_id, branch_id=branch_id)
        self._require_writer_candidate_occurrence(state, candidate_occurrence)
        if invocation.route is not SceneRoute.ORDINARY or invocation.purpose != "writer":
            raise StateConflictError("ordinary Writer invocation changed its owner")
        packet = freeze_writer_stage_packet(
            sequence_plan=authority_binding,
            realization_context={"invocation": pi_invocation_to_payload(invocation)},
            configuration=self._configurations[ProviderStage.WRITER],
        )
        exact_result = self._run_stage(
            state=state,
            packet=packet,
            accepted_state_sha256=accepted_state_sha256,
            authority_binding=authority_binding,
            stage_ordinal=candidate_occurrence.stage_ordinal,
        )
        return deserialize_pi_result(exact_result)

    def run_semantic_validator(
        self,
        request: SemanticValidationRequestV1,
        custody: SemanticValidationCustodyV1,
        *,
        accepted_state_sha256: str,
        authority_binding: object,
    ) -> BoundSemanticValidationV1:
        state = self._state_for(
            world_id=custody.world_id,
            branch_id=custody.branch_id,
        )
        packet = freeze_semantic_validator_stage_packet(
            sequence_plan=request.cognition_plan,
            candidate={
                "candidate_id": custody.candidate_id,
                "candidate_prose_sha256": custody.candidate_prose_sha256,
            },
            validation_context={
                "request": to_primitive(request),
                "custody": to_primitive(custody),
            },
            configuration=self._configurations[ProviderStage.SEMANTIC_VALIDATOR],
        )
        exact_result = self._run_stage(
            state=state,
            packet=packet,
            accepted_state_sha256=accepted_state_sha256,
            authority_binding=authority_binding,
        )
        return deserialize_semantic_validation_result(exact_result)

    def run_recorder(
        self,
        invocation: PiSceneInvocationV1,
        *,
        accepted: LeanAcceptedTurnReceiptV1,
        accepted_state_sha256: str,
        authority_binding: object,
    ) -> PiSceneInvocationResultV1:
        state = self._state_for(
            world_id=accepted.world_id,
            branch_id=accepted.branch_id,
        )
        if invocation.route is not SceneRoute.ORDINARY or invocation.purpose != "recorder":
            raise StateConflictError("ordinary Recorder invocation changed its owner")
        packet = freeze_recorder_stage_packet(
            accepted_story=accepted.exact_accepted_prose,
            accepted_story_receipt=accepted,
            recording_context={"invocation": pi_invocation_to_payload(invocation)},
            configuration=self._configurations[ProviderStage.RECORDER],
        )
        exact_result = self._run_stage(
            state=state,
            packet=packet,
            accepted_state_sha256=accepted_state_sha256,
            authority_binding=authority_binding,
        )
        return deserialize_pi_result(exact_result)

    def execute_action(self, action: object) -> ProviderStageActionProjectionV1:
        """Execute only Provider Retry or Check Status under generic authority."""

        validated = validate_provider_stage_retry_action_v1(action)
        chain = self._service.read_chain(validated["chain_id"])
        if chain.identity.stage not in _ORDINARY_STAGES:
            raise StateConflictError("provider-stage action does not belong to ordinary runtime")
        kind = validated["action_kind"]
        if kind == "provider_retry":
            chain = self._service.execute_manual_retry(validated)
        elif kind == "check_status":
            chain = self._service.check_status(validated)
        else:
            raise StateConflictError("provider-stage action is not Provider Retry or Check Status")
        result_ready = chain.phase in _RESULT_READY_PHASES
        if result_ready:
            self._service.finalize_result_once(chain.chain_id)
            chain = self._service.read_chain(chain.chain_id)
        envelope = self._service.canonical_status(chain_id=chain.chain_id)
        return ProviderStageActionProjectionV1(
            chain_id=chain.chain_id,
            stage=chain.identity.stage,
            result_ready=result_ready,
            envelope=envelope,
        )

    def status(self, chain_id: str) -> ProviderStageRetryStatusEnvelopeV1:
        """Provider-free canonical status projection for the future HTTP GET."""

        chain = self._service.read_chain(chain_id)
        if chain.identity.stage not in _ORDINARY_STAGES:
            raise StateConflictError("provider-stage status does not belong to ordinary runtime")
        return self._service.canonical_status(chain_id=chain_id)

    def _run_stage(
        self,
        *,
        state: _BoundRequestState,
        packet: ProviderStageFrozenPacketV1,
        accepted_state_sha256: str,
        authority_binding: object,
        stage_ordinal: int | None = None,
    ) -> bytes:
        _require_sha256(accepted_state_sha256, "accepted state")
        context = state.context
        scope = ProviderStageRetryOccurrenceScopeV1.create(
            world_id=context.binding.world_id,
            branch_id=context.binding.branch_id,
            request_id=context.binding.request_id,
            generation_id=context.generation_id,
            stage=packet.stage,
            stage_ordinal=(
                state.next_stage_ordinal(packet.stage) if stage_ordinal is None else stage_ordinal
            ),
            accepted_state_sha256=accepted_state_sha256,
            exact_input=packet.exact_bytes,
            authority_binding={
                "http_request_binding_sha256": context.binding_sha256,
                "stage_authority": to_primitive(authority_binding),
            },
        )
        pending = self._custody_store.load_for_scope(scope)
        if (
            pending.binding != context.binding
            or pending.generation != context.generation
            or pending.context_sha256 != canonical_sha256(context.context_payload())
        ):
            raise StateConflictError("ordinary protected request context changed")
        # Persist the authoritative chain, exact protected input, and full
        # reconstructable scope before publishing any secondary custody
        # pointer. A crash in those projections can therefore be recovered
        # without inventing a chain or dispatching a provider automatically.
        self._service.store.begin(scope.identity, packet.exact_bytes)
        self._service.remember_scope(scope)
        self._custody_store.bind_occurrence(
            scope=scope,
            context_sha256=pending.context_sha256,
        )
        self._custody_store.bind_chain(
            chain_id=scope.identity.chain_id,
            request_id=context.binding.request_id,
            context_sha256=pending.context_sha256,
        )
        self._custody_store.advance_latest_chain(
            scope=scope,
            context_sha256=pending.context_sha256,
            semantic_action_boundary=(
                packet.stage in {ProviderStage.PLANNER, ProviderStage.WRITER}
                and state.consume_semantic_action_boundary()
            ),
        )
        chain = self._service.start_initial(scope=scope, packet=packet)
        if chain.phase in _RESULT_READY_PHASES:
            return self._service.finalize_result_once(chain.chain_id)
        envelope = self._service.canonical_status(
            chain_id=chain.chain_id,
            scope=scope,
        )
        raise ProviderStageRetryPendingError(envelope)

    def reserve_writer_candidate_occurrence(
        self,
        *,
        world_id: str,
        branch_id: str,
        generation: int,
    ) -> OrdinaryWriterCandidateOccurrenceV1:
        """Reserve a content-free Writer ordinal from exact request custody."""

        state = self._state_for(world_id=world_id, branch_id=branch_id)
        if type(generation) is not int or generation != state.context.generation:
            raise StateConflictError("ordinary Writer candidate generation changed")
        ordinal = state.next_stage_ordinal(ProviderStage.WRITER)
        occurrence_sha256 = domain_sha256(
            "cera.ordinary_writer_candidate_occurrence.v1",
            {
                "http_request_id": state.context.binding.request_id,
                "http_request_binding_sha256": state.context.binding_sha256,
                "generation_id": state.context.generation_id,
                "stage": ProviderStage.WRITER.value,
                "stage_ordinal": ordinal,
            },
        )
        return OrdinaryWriterCandidateOccurrenceV1(
            stage_ordinal=ordinal,
            occurrence_sha256=occurrence_sha256,
        )

    @staticmethod
    def _require_writer_candidate_occurrence(
        state: _BoundRequestState,
        occurrence: OrdinaryWriterCandidateOccurrenceV1,
    ) -> None:
        if type(occurrence) is not OrdinaryWriterCandidateOccurrenceV1:
            raise ContractValidationError("ordinary Writer candidate occurrence changed")
        expected = domain_sha256(
            "cera.ordinary_writer_candidate_occurrence.v1",
            {
                "http_request_id": state.context.binding.request_id,
                "http_request_binding_sha256": state.context.binding_sha256,
                "generation_id": state.context.generation_id,
                "stage": ProviderStage.WRITER.value,
                "stage_ordinal": occurrence.stage_ordinal,
            },
        )
        if (
            occurrence.occurrence_sha256 != expected
            or state.stage_occurrences.get(ProviderStage.WRITER) != occurrence.stage_ordinal
        ):
            raise StateConflictError("ordinary Writer candidate occurrence changed custody")

    def _state_for(self, *, world_id: str, branch_id: str) -> _BoundRequestState:
        state = self._bound_request.get()
        if state is None:
            raise StateConflictError("ordinary provider-stage Retry lacks HTTP request custody")
        binding = state.context.binding
        if (binding.world_id, binding.branch_id) != (world_id, branch_id):
            raise StateConflictError("ordinary provider-stage Retry changed branch custody")
        return state


def serialize_planner_result(result: PlannerTurnOutputV1) -> bytes:
    if type(result) is not PlannerTurnOutputV1:
        raise ContractValidationError("Planner provider-stage result type changed")
    return canonical_bytes(to_primitive(result))


def deserialize_planner_result(exact_result: bytes) -> PlannerTurnOutputV1:
    payload = _canonical_object(exact_result, "Planner result")
    decoded = from_mapping(PlannerTurnOutputV1, payload)
    assert isinstance(decoded, PlannerTurnOutputV1)
    return decoded


def serialize_semantic_validation_result(result: BoundSemanticValidationV1) -> bytes:
    if type(result) is not BoundSemanticValidationV1:
        raise ContractValidationError("Semantic Validator result type changed")
    return canonical_bytes(to_primitive(result))


def deserialize_semantic_validation_result(
    exact_result: bytes,
) -> BoundSemanticValidationV1:
    payload = _canonical_object(exact_result, "Semantic Validator result")
    decoded = from_mapping(BoundSemanticValidationV1, payload)
    assert isinstance(decoded, BoundSemanticValidationV1)
    return decoded


def semantic_validation_disposition(
    result: BoundSemanticValidationV1,
) -> ProviderStageSemanticDisposition:
    if type(result) is not BoundSemanticValidationV1:
        raise ContractValidationError("Semantic Validator result type changed")
    return (
        ProviderStageSemanticDisposition.ACCEPTED
        if result.verdict.verdict is SemanticVerdict.PASS
        else ProviderStageSemanticDisposition.REJECTED
    )


def ordinary_nonsemantic_disposition(_result: object) -> ProviderStageSemanticDisposition:
    return ProviderStageSemanticDisposition.NOT_APPLICABLE


def pi_invocation_to_payload(invocation: PiSceneInvocationV1) -> dict[str, Any]:
    if type(invocation) is not PiSceneInvocationV1:
        raise ContractValidationError("Pi provider-stage invocation type changed")
    view = verify_writer_view(invocation.view.root)
    if view != invocation.view:
        raise StateConflictError("Pi provider-stage Writer view changed before freeze")
    return {
        "route": invocation.route.value,
        "purpose": invocation.purpose,
        "view_root": str(invocation.view.root.resolve()),
        "view_manifest_sha256": invocation.view.manifest_sha256,
        "view_file_count": invocation.view.file_count,
        "prompt": invocation.prompt,
        "candidate_id": invocation.candidate_id,
        "session_dir": str(invocation.session_dir.resolve()),
        "accepted_parent_session": (
            None
            if invocation.accepted_parent_session is None
            else to_primitive(invocation.accepted_parent_session)
        ),
        "force_rehydrate": invocation.force_rehydrate,
    }


def _pi_invocation_from_payload(value: object) -> PiSceneInvocationV1:
    payload = _mapping(value, "Pi invocation")
    _exact_keys(
        payload,
        {
            "route",
            "purpose",
            "view_root",
            "view_manifest_sha256",
            "view_file_count",
            "prompt",
            "candidate_id",
            "session_dir",
            "accepted_parent_session",
            "force_rehydrate",
        },
        "Pi invocation",
    )
    try:
        route = SceneRoute(payload["route"])
    except (TypeError, ValueError) as exc:
        raise ContractValidationError("Pi invocation route changed") from exc
    view_root = payload["view_root"]
    if not isinstance(view_root, str):
        raise ContractValidationError("Pi invocation Writer-view path changed")
    view = verify_writer_view(Path(view_root))
    if (
        view.manifest_sha256 != payload["view_manifest_sha256"]
        or view.file_count != payload["view_file_count"]
        or view.purpose != payload["purpose"]
    ):
        raise StateConflictError("Pi invocation Writer-view binding changed")
    accepted_payload = payload["accepted_parent_session"]
    accepted = None
    if accepted_payload is not None:
        accepted = from_mapping(
            AcceptedPiSessionV1,
            _mapping(accepted_payload, "accepted Pi parent session"),
        )
        assert isinstance(accepted, AcceptedPiSessionV1)
    for field_name in ("prompt", "candidate_id", "session_dir", "purpose"):
        if not isinstance(payload[field_name], str):
            raise ContractValidationError(f"Pi invocation {field_name} changed")
    if type(payload["force_rehydrate"]) is not bool:
        raise ContractValidationError("Pi invocation rehydrate flag changed")
    return PiSceneInvocationV1(
        route=route,
        purpose=payload["purpose"],
        view=view,
        prompt=payload["prompt"],
        candidate_id=payload["candidate_id"],
        session_dir=Path(payload["session_dir"]),
        accepted_parent_session=accepted,
        force_rehydrate=payload["force_rehydrate"],
    )


def planner_request_from_frozen_input(exact_input: bytes) -> PlannerTurnInputV1:
    semantic = _packet_semantic_input(
        exact_input,
        stage=ProviderStage.PLANNER,
        packet_kind="planner",
    )
    _exact_keys(
        semantic,
        {
            "normalized_messages",
            "accepted_state",
            "retrieval_snapshot",
            "tool_result_bundle",
        },
        "Planner semantic input",
    )
    payload = _mapping(semantic["normalized_messages"], "Planner request")
    _exact_keys(
        payload,
        {
            "world_id",
            "branch_id",
            "scene_id",
            "exact_user_source",
            "current_state",
            "characters",
            "relationships",
            "relevant_memories",
            "accepted_records",
            "request_controls",
            "creator_guidance",
        },
        "Planner request",
    )
    controls_payload = payload["request_controls"]
    controls: LeanSceneRequestControlsV1 | None
    if controls_payload is None:
        controls = None
    else:
        controls_mapping = _mapping(controls_payload, "Planner request controls")
        schema_version = controls_mapping.get("schema_version")
        model = (
            LeanSceneRequestControlsV2
            if schema_version == LeanSceneRequestControlsV2.SCHEMA_VERSION
            else LeanSceneRequestControlsV1
        )
        controls = from_mapping(model, controls_mapping)
        assert isinstance(controls, LeanSceneRequestControlsV1)
    guidance_payload = payload["creator_guidance"]
    guidance = None
    if guidance_payload is not None:
        guidance = from_mapping(
            CreatorGuidanceV1,
            _mapping(guidance_payload, "Planner creator guidance"),
        )
        assert isinstance(guidance, CreatorGuidanceV1)
    accepted_records = payload["accepted_records"]
    if not isinstance(accepted_records, list) or any(
        not isinstance(value, Mapping) for value in accepted_records
    ):
        raise ContractValidationError("Planner accepted records changed shape")
    required_text = ("world_id", "branch_id", "scene_id", "exact_user_source")
    if any(not isinstance(payload[name], str) for name in required_text):
        raise ContractValidationError("Planner request identity changed shape")
    for name in (
        "current_state",
        "characters",
        "relationships",
        "relevant_memories",
    ):
        _mapping(payload[name], f"Planner {name}")
    return PlannerTurnInputV1(
        world_id=payload["world_id"],
        branch_id=payload["branch_id"],
        scene_id=payload["scene_id"],
        exact_user_source=payload["exact_user_source"],
        current_state=dict(payload["current_state"]),
        characters=dict(payload["characters"]),
        relationships=dict(payload["relationships"]),
        relevant_memories=dict(payload["relevant_memories"]),
        accepted_records=tuple(dict(value) for value in accepted_records),
        request_controls=controls,
        creator_guidance=guidance,
    )


def writer_invocation_from_frozen_input(exact_input: bytes) -> PiSceneInvocationV1:
    semantic = _packet_semantic_input(
        exact_input,
        stage=ProviderStage.WRITER,
        packet_kind="writer",
    )
    _exact_keys(
        semantic,
        {"sequence_plan", "realization_context"},
        "Writer semantic input",
    )
    context = _mapping(semantic["realization_context"], "Writer realization context")
    _exact_keys(context, {"invocation"}, "Writer realization context")
    return _pi_invocation_from_payload(context["invocation"])


def semantic_validation_input_from_frozen_input(
    exact_input: bytes,
) -> tuple[SemanticValidationRequestV1, SemanticValidationCustodyV1]:
    semantic = _packet_semantic_input(
        exact_input,
        stage=ProviderStage.SEMANTIC_VALIDATOR,
        packet_kind="semantic_validator",
    )
    _exact_keys(
        semantic,
        {"sequence_plan", "candidate", "validation_context"},
        "Semantic Validator semantic input",
    )
    context = _mapping(semantic["validation_context"], "Semantic Validator context")
    _exact_keys(context, {"request", "custody"}, "Semantic Validator context")
    request = from_mapping(
        SemanticValidationRequestV1,
        _mapping(context["request"], "Semantic Validator request"),
    )
    custody = from_mapping(
        SemanticValidationCustodyV1,
        _mapping(context["custody"], "Semantic Validator custody"),
    )
    assert isinstance(request, SemanticValidationRequestV1)
    assert isinstance(custody, SemanticValidationCustodyV1)
    return request, custody


def recorder_invocation_from_frozen_input(exact_input: bytes) -> PiSceneInvocationV1:
    semantic = _packet_semantic_input(
        exact_input,
        stage=ProviderStage.RECORDER,
        packet_kind="recorder",
    )
    _exact_keys(
        semantic,
        {"accepted_story", "accepted_story_receipt", "recording_context"},
        "Recorder semantic input",
    )
    context = _mapping(semantic["recording_context"], "Recorder context")
    _exact_keys(context, {"invocation"}, "Recorder context")
    return _pi_invocation_from_payload(context["invocation"])


def serialize_pi_result(result: PiSceneInvocationResultV1) -> bytes:
    if type(result) is not PiSceneInvocationResultV1:
        raise ContractValidationError("Pi provider-stage result type changed")
    return canonical_bytes(
        {
            "output_text": result.output_text,
            "session_id": result.session_id,
            "session_dir": str(result.session_dir.resolve()),
            "writer_receipt": to_primitive(result.writer_receipt),
            "raw_event_count": result.raw_event_count,
        }
    )


def deserialize_pi_result(exact_result: bytes) -> PiSceneInvocationResultV1:
    payload = _canonical_object(exact_result, "Pi result")
    _exact_keys(
        payload,
        {
            "output_text",
            "session_id",
            "session_dir",
            "writer_receipt",
            "raw_event_count",
        },
        "Pi result",
    )
    for field_name in ("output_text", "session_id", "session_dir"):
        if not isinstance(payload[field_name], str):
            raise ContractValidationError(f"Pi result {field_name} changed")
    if type(payload["raw_event_count"]) is not int or payload["raw_event_count"] < 0:
        raise ContractValidationError("Pi result event count changed")
    receipt = from_mapping(
        PiWriterReceiptV1,
        _mapping(payload["writer_receipt"], "Pi Writer receipt"),
    )
    assert isinstance(receipt, PiWriterReceiptV1)
    return PiSceneInvocationResultV1(
        output_text=payload["output_text"],
        session_id=payload["session_id"],
        session_dir=Path(payload["session_dir"]),
        writer_receipt=receipt,
        raw_event_count=payload["raw_event_count"],
    )


__all__ = [
    "OrdinaryProviderStageRetryRuntimeV1",
    "OrdinaryPipelineContinuationPort",
    "OrdinaryPipelineReplayPort",
    "OrdinaryRecorderContinuationPort",
    "OrdinaryStageRetryRequestContextV1",
    "PlannerFrozenRetrievalV1",
    "ProviderStageActionProjectionV1",
    "deserialize_pi_result",
    "deserialize_planner_result",
    "deserialize_semantic_validation_result",
    "ordinary_nonsemantic_disposition",
    "pi_invocation_to_payload",
    "planner_request_from_frozen_input",
    "recorder_invocation_from_frozen_input",
    "semantic_validation_disposition",
    "semantic_validation_input_from_frozen_input",
    "serialize_pi_result",
    "serialize_planner_result",
    "serialize_semantic_validation_result",
    "writer_invocation_from_frozen_input",
]
