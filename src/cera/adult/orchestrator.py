"""Validation for non-authoritative adult mechanics and Composer binding."""

from __future__ import annotations

from cera.composer import AdultComposerBinding
from cera.contracts import ErrorEnvelope
from cera.errors import ErrorCode, RetryMode
from cera.ids import IdKind, deterministic_id
from cera.serialization import canonical_json, canonical_sha256, domain_sha256

from .fake import (
    AdultMechanicsPort,
    AdultMechanicsUnavailable,
    FakeAdultMechanicsFixture,
)
from .models import (
    AdultMechanicsReceipt,
    AdultMechanicsRequest,
    AdultMechanicsResult,
    AdultRoutePreparation,
)


class AdultMechanicsFailure(Exception):
    def __init__(self, envelope: ErrorEnvelope) -> None:
        self.envelope = envelope
        super().__init__(envelope.message)


class AdultMechanicsCoordinator:
    def execute(
        self,
        preparation: AdultRoutePreparation,
        request: AdultMechanicsRequest,
        port: AdultMechanicsPort,
        *,
        fixture: FakeAdultMechanicsFixture,
    ) -> AdultMechanicsResult:
        if getattr(port, "fixture", None) != fixture:
            raise self._failure(request, "fake adult mechanics fixture mismatch")
        try:
            proposal = port.enrich(request)
            self._validate(preparation, request, proposal)
        except AdultMechanicsUnavailable as exc:
            raise self._failure(
                request,
                str(exc),
                code=ErrorCode.ADULT_PLANNER_UNAVAILABLE,
                stage="adult_mechanics_dispatch",
            ) from exc
        except AdultMechanicsFailure:
            raise
        except Exception as exc:
            raise self._failure(request, f"adult mechanics result failed validation: {exc}") from exc
        retained = tuple(value for value in proposal.advisory_metadata if self._valid_advisory(value))
        quarantined = tuple(
            value for value in proposal.advisory_metadata if not self._valid_advisory(value)
        )
        receipt = AdultMechanicsReceipt(
            schema_version=AdultMechanicsReceipt.SCHEMA_VERSION,
            provider_receipt_id=deterministic_id(
                IdKind.PROVIDER_RECEIPT,
                "cera.fake_adult_mechanics.receipt.v1",
                f"{request.request_sha256}|{fixture.fixture_sha256}|{proposal.proposal_sha256}",
            ),
            adapter_role="fake_adult_mechanics",
            fixture_id=fixture.fixture_id,
            fixture_sha256=fixture.fixture_sha256,
            request_sha256=request.request_sha256,
            proposal_sha256=proposal.proposal_sha256,
            authority_sha256=preparation.authority.authority_sha256,
            ledger_sha256=preparation.ledger.ledger_sha256,
            context_sha256=preparation.context.context_sha256,
            retained_advisory_count=len(retained),
            quarantined_advisory_sha256=tuple(
                canonical_sha256(value) for value in quarantined
            ),
            external_provider_calls=0,
            story_state_committed=False,
        )
        return AdultMechanicsResult(proposal, receipt, retained, quarantined)

    def _validate(self, preparation, request, proposal) -> None:
        decision = request.reasoner_outcome.decision
        assert decision is not None
        expected_decision = domain_sha256("cera.scene_decision.v1", decision)
        expected_sequence = domain_sha256(
            "cera.sequence_plan.v1", (decision.current_segment, decision.future_segments)
        )
        if (
            request.adult_authority_id != preparation.authority.adult_authority_id
            or request.authority_sha256 != preparation.authority.authority_sha256
            or request.ledger_sha256 != preparation.ledger.ledger_sha256
            or request.context_sha256 != preparation.context.context_sha256
        ):
            self._invalid(request, "adult mechanics request does not match route preparation")
        if (
            proposal.authority_sha256 != request.authority_sha256
            or proposal.ledger_sha256 != request.ledger_sha256
            or proposal.context_sha256 != request.context_sha256
            or proposal.decision_sha256 != expected_decision
            or proposal.sequence_plan_sha256 != expected_sequence
        ):
            self._invalid(request, "adult mechanics proposal changed authority or reasoner decision")
        serialized_proposal = canonical_json(proposal)
        exact_envelope = preparation.composer_source_packet.protected_envelope
        assert exact_envelope is not None
        if any(value.exact_text in serialized_proposal for value in exact_envelope.units):
            self._invalid(request, "adult mechanics proposal leaked exact protected source text")
        expected_units = tuple(value.source_unit_id for value in preparation.ledger.units)
        if request.unit_ids != expected_units:
            self._invalid(request, "adult mechanics request omitted or reordered a unit")
        treatments = proposal.unit_treatments
        if tuple(value.source_unit_id for value in treatments) != expected_units:
            self._invalid(request, "adult mechanics proposal omitted or reordered a unit")
        selected_craft = set(preparation.context.craft_reference_ids)
        for treatment, ledger_unit in zip(treatments, preparation.ledger.units, strict=True):
            if treatment.progression_state is not ledger_unit.progression_state:
                self._invalid(request, "adult mechanics changed source progression")
            if treatment.participant_ids != ledger_unit.participant_ids:
                self._invalid(request, "adult mechanics changed source participants")
            if not set(treatment.craft_reference_ids).issubset(selected_craft):
                self._invalid(request, "adult mechanics selected unapproved craft context")

    @staticmethod
    def _valid_advisory(value: object) -> bool:
        if not isinstance(value, dict) or set(value) != {"kind", "summary"}:
            return False
        return all(isinstance(item, str) and item.strip() for item in value.values())

    def _invalid(self, request: AdultMechanicsRequest, message: str) -> None:
        raise self._failure(request, message)

    @staticmethod
    def _failure(
        request: AdultMechanicsRequest,
        message: str,
        *,
        code: ErrorCode = ErrorCode.ADULT_MECHANICS_CONTRACT_INVALID,
        stage: str = "adult_mechanics_validation",
    ) -> AdultMechanicsFailure:
        prepared = request.prepared_turn
        return AdultMechanicsFailure(
            ErrorEnvelope(
                schema_version=ErrorEnvelope.SCHEMA_VERSION,
                error_code=code,
                message=message,
                trace_id=deterministic_id(
                    IdKind.TRACE,
                    "cera.adult_mechanics.failure.v1",
                    f"{prepared.request.request_id}|{stage}|{code.value}",
                ),
                request_id=prepared.request.request_id,
                branch_id=prepared.request.branch_id,
                generation_id=prepared.request.generation_id,
                stage=stage,
                story_state_committed=False,
                retry_mode=RetryMode.MANUAL_AFTER_REVIEW,
                details=(),
            )
        )


def build_adult_composer_binding(
    preparation: AdultRoutePreparation,
    mechanics: AdultMechanicsResult,
) -> AdultComposerBinding:
    return AdultComposerBinding(
        adult_authority_id=preparation.authority.adult_authority_id,
        dual_representation_receipt_id=preparation.receipt.validation_receipt_id,
        mechanics_receipt_id=mechanics.receipt.provider_receipt_id,
        authority_sha256=preparation.authority.authority_sha256,
        dual_representation_sha256=preparation.receipt.synchronization_sha256,
        safe_source_view_sha256=preparation.receipt.safe_source_view_sha256,
        protected_envelope_sha256=preparation.receipt.exact_envelope_sha256,
        adult_context_sha256=preparation.context.context_sha256,
        mechanics_proposal_sha256=mechanics.proposal.proposal_sha256,
        mechanics_receipt_sha256=mechanics.receipt.receipt_sha256,
        selected_craft_reference_ids=preparation.context.craft_reference_ids,
    )
