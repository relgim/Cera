"""Python-owned adult authority, context selection, and dual-view synchronization."""

from __future__ import annotations

from dataclasses import dataclass

from cera.composer import (
    ComposerSourcePacket,
    CompositionMode,
    ProtectedSourceEnvelope,
)
from cera.contracts import ErrorEnvelope
from cera.contracts import TruthStatus
from cera.errors import ErrorCode, EvidenceServiceError, RetryMode
from cera.evidence import EvidenceFetchRequest, EvidenceService
from cera.ids import IdKind, TypedId, deterministic_id
from cera.kernel import (
    AdultCapacityStatus,
    AdultConsentStatus,
    AdultFreedomToStop,
    AdultPressureStatus,
    TurnRoute,
)
from cera.reasoner import (
    ReasonerSourceMode,
    ReasonerSourceUnit,
    ReasonerSourceView,
)
from cera.serialization import domain_sha256, text_sha256

from .models import (
    AdultAuthorityDecision,
    AdultCausalLedger,
    AdultCausalLedgerUnit,
    AdultContentFamily,
    AdultDualRepresentationReceipt,
    AdultParticipantDecision,
    AdultRouteContext,
    AdultRouteInput,
    AdultRoutePreparation,
    AdultScenarioKind,
    ContentActivationAuthority,
    ProviderCapabilityStatus,
    SceneEligibilityStatus,
)


class AdultRouteFailure(Exception):
    def __init__(self, envelope: ErrorEnvelope) -> None:
        self.envelope = envelope
        super().__init__(envelope.message)


@dataclass(frozen=True, slots=True)
class AdultContextSelector:
    allowed_families: tuple[AdultContentFamily, ...] = tuple(AdultContentFamily)
    profile_version: str = "synthetic-placeholder-v1"

    def select(self, route_input: AdultRouteInput) -> AdultRouteContext:
        selected: list[AdultContentFamily] = []
        rejected: list[str] = []
        allowed = set(self.allowed_families)
        current_units = {value.source_unit_id for value in route_input.source_units}
        for trigger in route_input.content_triggers:
            if trigger.family not in allowed:
                raise ValueError(f"unsupported adult content family: {trigger.family.value}")
            if trigger.authority is ContentActivationAuthority.CURRENT_EXPLICIT_SOURCE:
                if trigger.source_unit_id not in current_units:
                    raise ValueError("adult context trigger cites a non-current source unit")
                selected.append(trigger.family)
            elif trigger.authority is ContentActivationAuthority.CURRENT_OBJECTIVE_SCENE:
                selected.append(trigger.family)
            else:
                rejected.append(f"{trigger.family.value}:{trigger.authority.value}")
        selected = list(dict.fromkeys(selected))
        references = tuple(
            deterministic_id(
                IdKind.CRAFT_REFERENCE,
                "cera.synthetic_adult_craft_reference.v1",
                f"{self.profile_version}|{family.value}",
            )
            for family in selected
        )
        request = route_input.prepared_turn.request
        key = "|".join(
            (
                str(request.request_id),
                request.source_sha256,
                *(value.value for value in selected),
                *rejected,
            )
        )
        return AdultRouteContext(
            schema_version=AdultRouteContext.SCHEMA_VERSION,
            context_id=deterministic_id(IdKind.ADULT_CONTEXT, "cera.adult_context.v1", key),
            request_id=request.request_id,
            snapshot_token=route_input.prepared_turn.evidence_snapshot.snapshot_token,
            active=True,
            selected_families=tuple(selected),
            craft_reference_ids=references,
            rejected_triggers=tuple(rejected),
            profile_version=self.profile_version,
            actual_examples_imported=False,
            contains_exact_protected_prose=False,
            inherited_provider_conversation_state=False,
        )

    def select_ordinary(self, prepared_turn) -> AdultRouteContext:
        if prepared_turn.route is not TurnRoute.ORDINARY:
            raise ValueError("empty adult context is only for an ordinary turn")
        request = prepared_turn.request
        return AdultRouteContext(
            schema_version=AdultRouteContext.SCHEMA_VERSION,
            context_id=deterministic_id(
                IdKind.ADULT_CONTEXT,
                "cera.adult_context.empty.v1",
                str(request.request_id),
            ),
            request_id=request.request_id,
            snapshot_token=prepared_turn.evidence_snapshot.snapshot_token,
            active=False,
            selected_families=(),
            craft_reference_ids=(),
            rejected_triggers=(),
            profile_version=self.profile_version,
            actual_examples_imported=False,
            contains_exact_protected_prose=False,
            inherited_provider_conversation_state=False,
        )


class AdultRouteCoordinator:
    """Builds the allowed dual route; never makes a character decision."""

    def __init__(
        self,
        evidence_service: EvidenceService,
        *,
        context_selector: AdultContextSelector | None = None,
    ) -> None:
        self.evidence_service = evidence_service
        self.context_selector = context_selector or AdultContextSelector()

    def prepare(
        self,
        route_input: AdultRouteInput,
        protected_envelope: ProtectedSourceEnvelope,
    ) -> AdultRoutePreparation:
        try:
            authority = self._authorize(route_input)
            try:
                context = self.context_selector.select(route_input)
            except ValueError as exc:
                raise self._failure(
                    route_input,
                    ErrorCode.ADULT_CONTEXT_INVALID,
                    str(exc),
                    "adult_context_selection",
                ) from exc
            return self._synchronize(route_input, authority, context, protected_envelope)
        except AdultRouteFailure:
            raise
        except EvidenceServiceError as exc:
            raise self._failure(route_input, exc.code, str(exc), "adult_authority") from exc
        except Exception as exc:
            raise self._failure(
                route_input,
                ErrorCode.ADULT_ROUTE_SYNC_FAILED,
                f"adult route preparation failed: {exc}",
                "adult_route_sync",
            ) from exc

    def _authorize(self, route_input: AdultRouteInput) -> AdultAuthorityDecision:
        prepared = route_input.prepared_turn
        if prepared.route is not TurnRoute.CONSENT_VALID_ADULT:
            raise self._failure(
                route_input,
                ErrorCode.ADULT_AUTHORITY_UNRESOLVED,
                "adult route cannot be prepared from an ordinary turn",
                "adult_authority",
            )
        if not route_input.synthetic_fixture:
            raise self._failure(
                route_input,
                ErrorCode.CREATOR_AUTHORITY_UNAPPROVED,
                "real adult authority is not authorized in Phase 7",
                "adult_authority",
            )
        if route_input.scenario_kind is AdultScenarioKind.ACTUAL_NONCONSENSUAL_CONDUCT:
            raise self._failure(
                route_input,
                ErrorCode.BLOCKED_NONCONSENSUAL_EVENT,
                "actual non-consensual conduct is outside the adult route",
                "adult_authority",
            )
        if route_input.scenario_kind is AdultScenarioKind.WITHDRAWN_CONSENT:
            raise self._failure(
                route_input,
                ErrorCode.ADULT_AUTHORITY_UNRESOLVED,
                "adult scenario consent was withdrawn",
                "adult_authority",
            )
        if route_input.scenario_kind is AdultScenarioKind.UNCERTAIN_OR_ABSENT_CONSENT:
            raise self._failure(
                route_input,
                ErrorCode.ADULT_AUTHORITY_UNRESOLVED,
                "adult scenario consent is absent or uncertain",
                "adult_authority",
            )
        authority_by_id = {value.participant_id: value for value in prepared.adult_authority}
        if set(authority_by_id) != set(route_input.participant_ids):
            raise self._failure(
                route_input,
                ErrorCode.ADULT_AUTHORITY_UNRESOLVED,
                "every adult participant requires one exact authority record",
                "adult_authority",
            )
        present = set(prepared.present_character_ids)
        participant_decisions: list[AdultParticipantDecision] = []
        all_evidence: list[TypedId] = []
        for participant_id in route_input.participant_ids:
            value = authority_by_id[participant_id]
            eligibility = (
                SceneEligibilityStatus.ELIGIBLE_AND_PRESENT
                if participant_id in present
                else SceneEligibilityStatus.ABSENT
            )
            if not value.consent_valid or eligibility is not SceneEligibilityStatus.ELIGIBLE_AND_PRESENT:
                raise self._failure(
                    route_input,
                    ErrorCode.ADULT_AUTHORITY_UNRESOLVED,
                    "adult participant identity, presence, consent, capacity, pressure, or freedom is unresolved",
                    "adult_authority",
                )
            all_evidence.extend(value.evidence_ids)
            participant_decisions.append(
                AdultParticipantDecision(
                    participant_id=participant_id,
                    identity_status=value.identity_status,
                    scene_eligibility=eligibility,
                    consent_status=value.consent_status,
                    capacity_status=value.capacity_status,
                    pressure_status=value.pressure_status,
                    freedom_to_stop=value.freedom_to_stop,
                    evidence_ids=value.evidence_ids,
                )
            )
        all_evidence.extend(
            evidence_id
            for assertion in route_input.semantic_assertions
            for evidence_id in assertion.evidence_ids
        )
        all_evidence.extend(
            trigger.evidence_id
            for trigger in route_input.content_triggers
            if trigger.evidence_id is not None
        )
        # Re-fetch exact identity/authority evidence under the same immutable snapshot.
        service = EvidenceService(
            self.evidence_service.store,
            limits=self.evidence_service.limits,
            visibility_policy_version=self.evidence_service.visibility_policy_version,
        )
        batch = service.fetch_evidence(
            prepared.evidence_snapshot,
            EvidenceFetchRequest(tuple(dict.fromkeys(all_evidence)), ("claim", "provenance")),
        )
        exact_by_id = {value.evidence_id: value for value in batch.exact_records}
        for participant in participant_decisions:
            for evidence_id in participant.evidence_ids:
                record = exact_by_id.get(evidence_id)
                if record is None or participant.participant_id not in record.subject_ids:
                    raise self._failure(
                        route_input,
                        ErrorCode.ADULT_AUTHORITY_UNRESOLVED,
                        "adult identity evidence does not authorize its participant",
                        "adult_authority",
                    )
        for assertion in route_input.semantic_assertions:
            for evidence_id in assertion.evidence_ids:
                record = exact_by_id.get(evidence_id)
                if record is None or assertion.subject_id not in record.subject_ids:
                    raise self._failure(
                        route_input,
                        ErrorCode.ADULT_AUTHORITY_UNRESOLVED,
                        "semantic assertion evidence does not authorize its subject",
                        "adult_authority",
                    )
        for trigger in route_input.content_triggers:
            if trigger.authority is ContentActivationAuthority.CURRENT_OBJECTIVE_SCENE:
                record = exact_by_id.get(trigger.evidence_id)
                if record is None or record.metadata.truth_status is not TruthStatus.OBJECTIVE:
                    raise self._failure(
                        route_input,
                        ErrorCode.ADULT_CONTEXT_INVALID,
                        "adult craft activation requires current objective evidence",
                        "adult_context_selection",
                    )
        prepared_units = tuple(prepared.request.source_units)
        if tuple(value.source_unit_id for value in route_input.source_units) != tuple(
            value.source_unit_id for value in prepared_units
        ):
            raise self._failure(
                route_input,
                ErrorCode.ADULT_ROUTE_SYNC_FAILED,
                "safe adult ledger omitted or reordered source units",
                "adult_route_sync",
            )
        for supplied, expected in zip(route_input.source_units, prepared_units, strict=True):
            if supplied.source_unit_sha256 != expected.sha256:
                raise self._failure(
                    route_input,
                    ErrorCode.ADULT_ROUTE_SYNC_FAILED,
                    "safe adult ledger source hash mismatch",
                    "adult_route_sync",
                )
            if not set(supplied.participant_ids).issubset(route_input.participant_ids):
                raise self._failure(
                    route_input,
                    ErrorCode.ADULT_AUTHORITY_UNRESOLVED,
                    "safe adult ledger contains an unauthorized participant",
                    "adult_authority",
                )
            if (
                supplied.consent_status is not AdultConsentStatus.GRANTED
                or supplied.capacity_status is not AdultCapacityStatus.CLEAR
                or supplied.pressure_status is not AdultPressureStatus.NONE
                or supplied.freedom_to_stop is not AdultFreedomToStop.PRESENT
            ):
                raise self._failure(
                    route_input,
                    ErrorCode.ADULT_AUTHORITY_UNRESOLVED,
                    "safe adult source unit is outside current consent authority",
                    "adult_authority",
                )
        allowed_units = {value.source_unit_id for value in route_input.source_units}
        if not set(route_input.scenario_source_unit_ids).issubset(allowed_units):
            raise self._failure(
                route_input,
                ErrorCode.ADULT_ROUTE_SYNC_FAILED,
                "adult scenario classification lacks current source authority",
                "adult_route_sync",
            )
        for assertion in route_input.semantic_assertions:
            if assertion.subject_id not in route_input.participant_ids:
                raise self._failure(
                    route_input,
                    ErrorCode.ADULT_AUTHORITY_UNRESOLVED,
                    "semantic assertion belongs to a nonparticipant",
                    "adult_authority",
                )
            if not set(assertion.source_unit_ids).issubset(allowed_units):
                raise self._failure(
                    route_input,
                    ErrorCode.ADULT_ROUTE_SYNC_FAILED,
                    "semantic assertion cites a non-current source unit",
                    "adult_route_sync",
                )
        key = f"{prepared.request.request_id}|{prepared.evidence_snapshot.binding_sha256}|{domain_sha256('cera.adult_route_input.v1', route_input)}"
        return AdultAuthorityDecision(
            schema_version=AdultAuthorityDecision.SCHEMA_VERSION,
            adult_authority_id=deterministic_id(
                IdKind.ADULT_AUTHORITY, "cera.adult_authority.v1", key
            ),
            request_id=prepared.request.request_id,
            snapshot_token=prepared.evidence_snapshot.snapshot_token,
            scenario_kind=route_input.scenario_kind,
            participants=tuple(participant_decisions),
            semantic_assertions=route_input.semantic_assertions,
            source_units=route_input.source_units,
            provider_capability=route_input.provider_capability,
            route_permitted=True,
            authority="synthetic_fixture",
            truth_status="noncanonical",
            synthetic_fixture=True,
            story_state_committed=False,
        )

    def _synchronize(
        self,
        route_input: AdultRouteInput,
        authority: AdultAuthorityDecision,
        context: AdultRouteContext,
        protected_envelope: ProtectedSourceEnvelope,
    ) -> AdultRoutePreparation:
        prepared = route_input.prepared_turn
        if protected_envelope.source_sha256 != prepared.request.source_sha256:
            raise ValueError("exact protected envelope source hash mismatch")
        if protected_envelope.protected_source_id != prepared.request.raw_source_ref:
            raise ValueError("exact protected envelope source identity mismatch")
        exact_units = protected_envelope.units
        safe_units = route_input.source_units
        if tuple(value.source_unit_id for value in exact_units) != tuple(
            value.source_unit_id for value in safe_units
        ):
            raise ValueError("safe and exact adult units differ in identity or order")
        ledger_units: list[AdultCausalLedgerUnit] = []
        for index, (exact, safe) in enumerate(zip(exact_units, safe_units, strict=True)):
            if text_sha256(exact.exact_text) != safe.source_unit_sha256:
                raise ValueError("exact adult envelope unit hash mismatch")
            if exact.classification is not prepared.request.source_units[index].classification:
                raise ValueError("exact adult envelope unit classification mismatch")
            if exact.required_state is not safe.progression_state:
                raise ValueError("safe and exact adult unit progression differs")
            if exact.participant_ids != safe.participant_ids:
                raise ValueError("safe and exact adult unit participants differ")
            ledger_units.append(
                AdultCausalLedgerUnit(
                    source_unit_id=safe.source_unit_id,
                    source_unit_sha256=safe.source_unit_sha256,
                    ordinal=safe.ordinal,
                    progression_state=safe.progression_state,
                    participant_ids=safe.participant_ids,
                    observable_code=safe.observable_code,
                    private_state_owner_ids=safe.private_state_owner_ids,
                    consent_status=safe.consent_status,
                    capacity_status=safe.capacity_status,
                    pressure_status=safe.pressure_status,
                    freedom_to_stop=safe.freedom_to_stop,
                )
            )
        if tuple(value.ordinal for value in ledger_units) != tuple(range(len(ledger_units))):
            raise ValueError("adult source unit ordinals are not contiguous")
        ledger = AdultCausalLedger(
            schema_version=AdultCausalLedger.SCHEMA_VERSION,
            adult_plan_id=deterministic_id(
                IdKind.ADULT_PLAN,
                "cera.adult_causal_ledger.v1",
                f"{authority.authority_sha256}|{prepared.request.source_sha256}",
            ),
            request_id=prepared.request.request_id,
            snapshot_token=prepared.evidence_snapshot.snapshot_token,
            source_sha256=prepared.request.source_sha256,
            authority_sha256=authority.authority_sha256,
            scenario_kind=authority.scenario_kind,
            units=tuple(ledger_units),
            prohibited_inferences=(
                "Bodily response does not establish desire, pleasure, attraction, consent, or preference.",
                "Roleplay surface does not convert actual consent state or authorize adjacent acts.",
                "Exact source outcomes do not establish adjacent psychology or relationship meaning.",
                "Do not author the protected user's unsupplied choice or private state.",
            ),
            contains_exact_protected_prose=False,
        )
        reasoner_units = tuple(
            ReasonerSourceUnit(
                source_unit_id=value.source_unit_id,
                classification=prepared.request.source_units[index].classification,
                safe_text=(
                    f"unit={value.ordinal}; state={value.progression_state.value}; "
                    f"participants={','.join(str(item) for item in value.participant_ids)}; "
                    f"observable={value.observable_code.value}; consent={value.consent_status.value}; "
                    f"capacity={value.capacity_status.value}; pressure={value.pressure_status.value}; "
                    f"freedom={value.freedom_to_stop.value}; "
                    f"scenario={authority.scenario_kind.value}; "
                    f"active_content_families={','.join(item.value for item in context.selected_families)}"
                ),
            )
            for index, value in enumerate(ledger.units)
        )
        source_view = ReasonerSourceView(
            mode=ReasonerSourceMode.ADULT_NON_GRAPHIC_LEDGER,
            source_sha256=prepared.request.source_sha256,
            units=reasoner_units,
            contains_exact_protected_adult_prose=False,
        )
        composer_packet = ComposerSourcePacket(
            mode=CompositionMode.PROTECTED_ADULT,
            source_sha256=prepared.request.source_sha256,
            ordinary_units=(),
            protected_envelope=protected_envelope,
            reasoner_safe_ledger_sha256=source_view.source_view_sha256,
        )
        synchronization_sha256 = domain_sha256(
            "cera.adult_dual_representation.v1",
            {
                "authority_sha256": authority.authority_sha256,
                "ledger_sha256": ledger.ledger_sha256,
                "safe_source_view_sha256": source_view.source_view_sha256,
                "exact_envelope_sha256": protected_envelope.envelope_sha256,
                "unit_ids": tuple(str(value.source_unit_id) for value in ledger.units),
                "unit_states": tuple(value.progression_state.value for value in ledger.units),
                "participants": tuple(
                    tuple(str(item) for item in value.participant_ids) for value in ledger.units
                ),
            },
        )
        receipt = AdultDualRepresentationReceipt(
            schema_version=AdultDualRepresentationReceipt.SCHEMA_VERSION,
            validation_receipt_id=deterministic_id(
                IdKind.VALIDATION,
                "cera.adult_dual_representation.receipt.v1",
                synchronization_sha256,
            ),
            request_id=prepared.request.request_id,
            snapshot_token=prepared.evidence_snapshot.snapshot_token,
            authority_sha256=authority.authority_sha256,
            safe_ledger_sha256=ledger.ledger_sha256,
            safe_source_view_sha256=source_view.source_view_sha256,
            exact_envelope_sha256=protected_envelope.envelope_sha256,
            synchronization_sha256=synchronization_sha256,
            status="synchronized",
            exact_protected_text_stored=False,
            external_provider_calls=0,
            story_state_committed=False,
        )
        return AdultRoutePreparation(
            authority=authority,
            ledger=ledger,
            context=context,
            reasoner_source_view=source_view,
            composer_source_packet=composer_packet,
            receipt=receipt,
        )

    @staticmethod
    def _failure(
        route_input: AdultRouteInput,
        code: ErrorCode,
        message: str,
        stage: str,
    ) -> AdultRouteFailure:
        prepared = route_input.prepared_turn
        return AdultRouteFailure(
            ErrorEnvelope(
                schema_version=ErrorEnvelope.SCHEMA_VERSION,
                error_code=code,
                message=message,
                trace_id=deterministic_id(
                    IdKind.TRACE,
                    "cera.adult_route.failure.v1",
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
