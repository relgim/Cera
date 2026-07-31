"""SceneReasonerPort tool gateway, deterministic validation, and receipts."""

from __future__ import annotations

from dataclasses import dataclass

from cera.contracts import DecisionRoute, ErrorEnvelope, Visibility
from cera.errors import ErrorCode, EvidenceServiceError, RetryMode
from cera.evidence import (
    CharacterSectionsRequest,
    ContinuityRequest,
    EvidenceBatch,
    EvidenceFetchRequest,
    ExactEvidence,
    EvidenceMetadata,
    EvidenceQueryPlan,
    EvidenceSearchRequest,
    EvidenceService,
)
from cera.ids import IdKind, TypedId, deterministic_id
from cera.kernel import TurnKernel, TurnKernelFailure, TurnRoute

from .fake import (
    FakeReasonerFixture,
    ReasonerEvidenceToolPort,
    ResolveEntitiesRequest,
    SceneReasonerPort,
    SceneReasonerPortFailure,
    SceneReasonerUnavailable,
)
from .models import (
    ParticipationRole,
    ReasonerExecutionResult,
    ReasonerOutcome,
    ReasonerOutcomeStatus,
    SceneReasonerAdapterCall,
    SceneReasonerReceipt,
    SceneReasonerRequest,
)


class ReasonerExecutionFailure(Exception):
    def __init__(
        self,
        envelope: ErrorEnvelope,
        *,
        provider_call_receipt=None,
        mcp_bridge_receipt=None,
        lookup_receipts=(),
        external_provider_calls_observed: int = 0,
    ) -> None:
        self.envelope = envelope
        self.provider_call_receipt = provider_call_receipt
        self.mcp_bridge_receipt = mcp_bridge_receipt
        self.lookup_receipts = tuple(lookup_receipts)
        if (
            type(external_provider_calls_observed) is not int
            or external_provider_calls_observed not in {0, 1}
        ):
            raise ValueError("reasoner failure call observation must be zero or one")
        self.external_provider_calls_observed = external_provider_calls_observed
        self.failure_evidence_bundle = None
        self.retained_evidence_handles = ()
        super().__init__(envelope.message)


class ReasonerEvidenceTools(ReasonerEvidenceToolPort):
    """One invocation, one immutable snapshot, one local evidence budget."""

    def __init__(self, service: EvidenceService, request: SceneReasonerRequest) -> None:
        # Each invocation receives fresh usage accounting while preserving the
        # exact store, policy, limits, and request-bound snapshot.
        self.service = EvidenceService(
            service.store,
            limits=service.limits,
            visibility_policy_version=service.visibility_policy_version,
        )
        self.request = request
        self.snapshot = request.prepared_turn.evidence_snapshot
        self.tool_call_count = 0
        self.lookup_receipt_ids: list[TypedId] = []
        self.lookup_receipts = []
        self.cumulative_returned_bytes = 0
        self.followup_search_count = 0
        self.exact_evidence: dict[TypedId, tuple[EvidenceMetadata, tuple[TypedId, ...]]] = {}
        self.exact_records: dict[TypedId, ExactEvidence] = {}

    def evidence_budget_status(self) -> dict[str, int]:
        """Return privacy-safe request-local retrieval counters.

        This is transport guidance only.  The evidence service remains the
        enforcing authority and its lookup receipts remain the audit source.
        """

        maximum = self.service.limits.maximum_followup_searches
        return {
            "maximum_followup_searches": maximum,
            "used_followup_searches": self.followup_search_count,
            "remaining_followup_searches": max(
                0, maximum - self.followup_search_count
            ),
        }

    def get_turn_snapshot(self):
        self.service._validate_snapshot(self.snapshot)
        self.tool_call_count += 1
        return self.snapshot

    def resolve_entities(self, request: ResolveEntitiesRequest) -> EvidenceBatch:
        return self.search_evidence(
            EvidenceSearchRequest(entity_ids=request.entity_ids, limit=request.limit)
        )

    def search_evidence(self, request: EvidenceSearchRequest) -> EvidenceBatch:
        return self._record(self.service.search_evidence(self.snapshot, request))

    def search_query_plan(self, request: EvidenceQueryPlan) -> EvidenceBatch:
        """Execute Codex-owned bounded paraphrase expansion as one search."""

        return self._record(self.service.search_query_plan(self.snapshot, request))

    def fetch_evidence(self, request: EvidenceFetchRequest) -> EvidenceBatch:
        batch = self._record(self.service.fetch_evidence(self.snapshot, request))
        for record in batch.exact_records:
            self.exact_evidence[record.evidence_id] = (record.metadata, record.subject_ids)
            self.exact_records[record.evidence_id] = record
        return batch

    def get_character_sections(self, request: CharacterSectionsRequest) -> EvidenceBatch:
        batch = self._record(self.service.get_character_sections(self.snapshot, request))
        for record in batch.exact_records:
            self.exact_evidence[record.evidence_id] = (record.metadata, record.subject_ids)
            self.exact_records[record.evidence_id] = record
        return batch

    def get_continuity(self, request: ContinuityRequest) -> EvidenceBatch:
        batch = self._record(self.service.get_continuity(self.snapshot, request))
        for record in batch.exact_records:
            self.exact_evidence[record.evidence_id] = (record.metadata, record.subject_ids)
            self.exact_records[record.evidence_id] = record
        return batch

    def _record(self, batch: EvidenceBatch) -> EvidenceBatch:
        if batch.snapshot_token != self.snapshot.snapshot_token:
            raise EvidenceServiceError(
                ErrorCode.EVIDENCE_SNAPSHOT_STALE,
                "reasoner tool returned a mismatched snapshot token",
            )
        self.tool_call_count += 1
        self.lookup_receipt_ids.append(batch.receipt.lookup_receipt_id)
        self.lookup_receipts.append(batch.receipt)
        self.cumulative_returned_bytes += batch.receipt.returned_bytes
        self.followup_search_count = max(
            self.followup_search_count,
            batch.receipt.followup_search_count,
        )
        return batch


class ReasonerCoordinator:
    def __init__(self, evidence_service: EvidenceService, turn_kernel: TurnKernel) -> None:
        self.evidence_service = evidence_service
        self.turn_kernel = turn_kernel

    def execute(
        self,
        request: SceneReasonerRequest,
        port: SceneReasonerPort,
        *,
        fixture: FakeReasonerFixture | None = None,
    ) -> ReasonerExecutionResult:
        port_fixture = getattr(port, "fixture", None)
        if (port_fixture is None) != (fixture is None) or (
            fixture is not None and port_fixture != fixture
        ):
            raise self._failure(
                request,
                ErrorCode.REASONER_CONTRACT_INVALID,
                "fake adapter fixture does not match receipt fixture",
                "reasoner_dispatch",
            )
        tools = ReasonerEvidenceTools(self.evidence_service, request)
        adapter_call = None
        try:
            adapter_call = port.reason(request, tools)
            if not isinstance(adapter_call, SceneReasonerAdapterCall):
                raise TypeError("reasoner port omitted adapter-call evidence")
            outcome = adapter_call.outcome
            self._validate_outcome(request, outcome, tools)
            state_receipt = None
            state_receipt_id = None
            if outcome.advisory_state_deltas:
                state_receipt = self.turn_kernel.validate_state_delta(
                    request.prepared_turn,
                    outcome.advisory_state_deltas,
                    authorized_evidence_ids=tuple(
                        {
                            *tools.exact_evidence,
                            *(
                                value.evidence_id
                                for value in request.seed_dossier.exact_seed_evidence
                            ),
                        }
                    ),
                )
                state_receipt_id = state_receipt.validation_receipt_id
        except SceneReasonerUnavailable as exc:
            raise self._failure(
                request, ErrorCode.REASONER_UNAVAILABLE, str(exc), "reasoner_dispatch"
            ) from exc
        except SceneReasonerPortFailure as exc:
            failure = self._failure(
                request,
                exc.code,
                str(exc),
                exc.stage,
                details=exc.safe_diagnostics,
            )
            failure.provider_call_receipt = exc.provider_call_receipt
            failure.mcp_bridge_receipt = exc.mcp_bridge_receipt
            failure.lookup_receipts = tuple(tools.lookup_receipts)
            failure.external_provider_calls_observed = (
                1
                if exc.provider_call_receipt is not None
                else exc.external_provider_calls_observed
            )
            raise failure from exc
        except EvidenceServiceError as exc:
            raise self._failure(request, exc.code, str(exc), "reasoner_evidence") from exc
        except TurnKernelFailure as exc:
            raise ReasonerExecutionFailure(exc.envelope) from exc
        except ReasonerExecutionFailure as exc:
            if adapter_call is not None:
                exc.provider_call_receipt = adapter_call.provider_call_receipt
                exc.mcp_bridge_receipt = adapter_call.mcp_bridge_receipt
                exc.external_provider_calls_observed = (
                    adapter_call.external_provider_calls
                )
            exc.lookup_receipts = tuple(tools.lookup_receipts)
            raise
        except Exception as exc:
            raise self._failure(
                request,
                ErrorCode.REASONER_CONTRACT_INVALID,
                f"reasoner result failed validation: {exc}",
                "reasoner_validation",
            ) from exc
        receipt = SceneReasonerReceipt(
            schema_version=SceneReasonerReceipt.SCHEMA_VERSION,
            provider_receipt_id=adapter_call.provider_receipt_id,
            reasoner_request_sha256=request.request_sha256,
            snapshot_token=request.prepared_turn.evidence_snapshot.snapshot_token,
            source_sha256=request.source_view.source_sha256,
            source_view_sha256=request.source_view.source_view_sha256,
            adapter_role=adapter_call.adapter_role,
            adapter_version=adapter_call.adapter_version,
            adapter_evidence_id=adapter_call.adapter_evidence_id,
            adapter_evidence_sha256=adapter_call.adapter_evidence_sha256,
            provider_receipt_sha256=adapter_call.provider_receipt_sha256,
            bridge_receipt_id=adapter_call.bridge_receipt_id,
            bridge_receipt_sha256=adapter_call.bridge_receipt_sha256,
            outcome_sha256=outcome.outcome_sha256,
            evidence_lookup_receipt_ids=tuple(tools.lookup_receipt_ids),
            tool_call_count=tools.tool_call_count,
            cumulative_evidence_bytes=tools.cumulative_returned_bytes,
            outcome_status=outcome.status,
            external_provider_calls=adapter_call.external_provider_calls,
        )
        return ReasonerExecutionResult(
            outcome,
            receipt,
            state_receipt_id,
            adapter_call.provider_call_receipt,
            adapter_call.mcp_bridge_receipt,
            tuple(
                {
                    **{
                        value.evidence_id: value
                        for value in request.seed_dossier.exact_seed_evidence
                    },
                    **tools.exact_records,
                }.values()
            ),
            tuple(tools.lookup_receipts),
            state_receipt,
        )

    def _validate_outcome(
        self,
        request: SceneReasonerRequest,
        outcome: ReasonerOutcome,
        tools: ReasonerEvidenceTools,
    ) -> None:
        if outcome.status is not ReasonerOutcomeStatus.DECISION_READY:
            return
        decision = outcome.decision
        assert decision is not None
        prepared = request.prepared_turn
        expected_route = (
            DecisionRoute.ORDINARY
            if prepared.route is TurnRoute.ORDINARY
            else DecisionRoute.CONSENT_VALID_ADULT
        )
        if decision.route is not expected_route:
            self._invalid(request, "reasoner decision route changed Python preflight")
        if outcome.adult_craft_need is not None:
            need = outcome.adult_craft_need
            if need.request_id != prepared.request.request_id:
                self._invalid(request, "adult craft need changed request identity")
            craft_beats = {value.beat_id for value in need.beat_needs}
            current_beats = {
                value.beat_id for value in decision.current_segment.ordered_beats
            }
            if not craft_beats or not craft_beats.issubset(current_beats):
                self._invalid(
                    request,
                    "adult craft need cites no current beat or cites a non-current beat",
                )
        selected = set(decision.responding_npc_ids)
        eligible = set(prepared.eligible_responding_npc_ids)
        aware = set(request.seed_dossier.aware_character_ids)
        if not selected or not selected.issubset(eligible) or not selected.issubset(aware):
            self._invalid(request, "reasoner selected absent, ineligible, or unaware participant")
        if prepared.request.protected_user_id in selected:
            self._invalid(request, "reasoner selected protected user as responder")
        participation_ids = {value.character_id for value in outcome.participation}
        if participation_ids != selected:
            self._invalid(request, "participation selection does not match responders")
        leads = [value for value in outcome.participation if value.role is ParticipationRole.LEAD]
        if len(leads) != 1 or decision.floor_owner_id != leads[0].character_id:
            self._invalid(request, "reasoner floor must have one matching lead")
        for beat in decision.current_segment.ordered_beats:
            if beat.actor_id == prepared.request.protected_user_id:
                self._invalid(request, "reasoner authored a protected-user action")
            if beat.actor_id.kind is IdKind.CHARACTER and beat.actor_id not in selected:
                self._invalid(request, "reasoner beat actor is not a selected participant")
            if "\n" in beat.neutral_event or len(beat.neutral_event) > 500:
                self._invalid(request, "reasoner returned prose instead of an operational beat")
        if "\n" in decision.scene_intent or len(decision.scene_intent) > 500:
            self._invalid(request, "reasoner returned prose instead of operational intent")
        seed = {
            value.evidence_id: (value.metadata, value.subject_ids)
            for value in request.seed_dossier.exact_seed_evidence
        }
        available = {**seed, **tools.exact_evidence}
        citations = {value.evidence_id: value for value in outcome.hard_citations}
        used = {
            evidence_id
            for move in decision.character_moves
            for evidence_id in move.evidence_ids
        }
        used.update(
            evidence_id
            for beat in decision.current_segment.ordered_beats
            for evidence_id in beat.evidence_ids
        )
        used.update(
            evidence_id
            for participant in outcome.participation
            for evidence_id in participant.evidence_ids
        )
        if not used.issubset(citations):
            self._invalid(request, "hard decision evidence lacks versioned citations")
        for evidence_id in used:
            resolved = available.get(evidence_id)
            citation = citations[evidence_id]
            if resolved is None:
                self._invalid(request, "hard decision evidence was never exactly authorized")
            metadata, _ = resolved
            if (
                citation.record_id != metadata.record_id
                or citation.record_version != metadata.record_version
            ):
                self._invalid(request, "hard evidence citation is unknown or stale")
        for move in decision.character_moves:
            for evidence_id in move.evidence_ids:
                metadata, subject_ids = available[evidence_id]
                if not _character_may_use_evidence(
                    metadata, subject_ids, move.character_id
                ):
                    self._invalid(
                        request,
                        "private evidence was transferred to another character",
                        details=("character_moves.evidence_ids:private_owner_mismatch",),
                    )
        for participant in outcome.participation:
            for evidence_id in participant.evidence_ids:
                metadata, subject_ids = available[evidence_id]
                if not _character_may_use_evidence(
                    metadata, subject_ids, participant.character_id
                ):
                    self._invalid(
                        request,
                        "private evidence cannot justify another participant",
                        details=("participation.evidence_ids:private_owner_mismatch",),
                    )
        for beat in decision.current_segment.ordered_beats:
            for evidence_id in beat.evidence_ids:
                metadata, subject_ids = available[evidence_id]
                if beat.actor_id.kind is IdKind.CHARACTER and not _character_may_use_evidence(
                    metadata, subject_ids, beat.actor_id
                ):
                    self._invalid(
                        request,
                        "private evidence cannot drive another beat actor",
                        details=("event_blocks.evidence_ids:private_owner_mismatch",),
                    )

    def _invalid(
        self,
        request: SceneReasonerRequest,
        message: str,
        *,
        details: tuple[str, ...] = (),
    ) -> None:
        raise self._failure(
            request,
            ErrorCode.REASONER_CONTRACT_INVALID,
            message,
            "reasoner_validation",
            details=details,
        )

    @staticmethod
    def _failure(
        request: SceneReasonerRequest,
        code: ErrorCode,
        message: str,
        stage: str,
        *,
        details: tuple[str, ...] = (),
    ) -> ReasonerExecutionFailure:
        prepared = request.prepared_turn
        return ReasonerExecutionFailure(
            ErrorEnvelope(
                schema_version=ErrorEnvelope.SCHEMA_VERSION,
                error_code=code,
                message=message,
                trace_id=deterministic_id(
                    IdKind.TRACE,
                    "cera.reasoner.failure.v1",
                    f"{prepared.request.request_id}|{stage}|{code.value}",
                ),
                request_id=prepared.request.request_id,
                branch_id=prepared.request.branch_id,
                generation_id=prepared.request.generation_id,
                stage=stage,
                story_state_committed=False,
                retry_mode=RetryMode.MANUAL_AFTER_REVIEW,
                details=details,
            )
        )


def _character_may_use_evidence(
    metadata: EvidenceMetadata,
    subject_ids: tuple[TypedId, ...],
    character_id: TypedId,
) -> bool:
    """Enforce record knowledge when a system-scoped reasoner selects a character move."""

    if metadata.visibility is Visibility.OWNER_PRIVATE:
        return metadata.owner_id == character_id
    if metadata.knowledge_owner_ids:
        return character_id in metadata.knowledge_owner_ids
    if metadata.visibility is Visibility.SYSTEM_PRIVATE:
        return character_id in subject_ids
    return True
