"""Raw-turn ingress facade preserving the prepared application seam."""

from __future__ import annotations

from dataclasses import dataclass

from cera.errors import ContractValidationError
from cera.evidence import ExactEvidence
from cera.ids import IdKind, deterministic_id
from cera.kernel import (
    IntakeSourceUnit,
    RequestedContentClass,
    TurnIntakeCommand,
    TurnKernel,
    TurnRoute,
)
from cera.reasoner import (
    ReasonerSourceMode,
    ReasonerSourceUnit,
    ReasonerSourceView,
    SceneReasonerRequest,
    SeedDossierAssembler,
    SeedDossierAssemblyRequest,
    SeedDossierAssemblyResult,
)
from cera.serialization import domain_sha256

from .models import (
    IntentInterpretationDraft,
    IntentInterpretationReceipt,
    IntentInterpreterPort,
    RawTurnEnvelope,
)
from .projection import DeterministicRawTurnProjectionPort


@dataclass(frozen=True, slots=True)
class PreparedIngressTurn:
    """New ingress result; ``reasoner_request`` feeds the existing seam."""

    interpretation: IntentInterpretationDraft
    interpretation_receipt: IntentInterpretationReceipt
    seed_assembly: SeedDossierAssemblyResult
    reasoner_request: SceneReasonerRequest

    @property
    def ready_for_reasoner(self) -> bool:
        return self.seed_assembly.ready


class RawTurnIngressFacade:
    def __init__(
        self,
        *,
        turn_kernel: TurnKernel,
        seed_assembler: SeedDossierAssembler,
        intent_interpreter: IntentInterpreterPort | None = None,
    ) -> None:
        self.turn_kernel = turn_kernel
        self.intent_interpreter = (
            intent_interpreter or DeterministicRawTurnProjectionPort()
        )
        self.seed_assembler = seed_assembler

    def prepare(
        self,
        envelope: RawTurnEnvelope,
        *,
        preexpanded_exact_evidence: tuple[ExactEvidence, ...] = (),
    ) -> PreparedIngressTurn:
        interpretation = self.intent_interpreter.interpret(envelope)
        self._validate_interpretation(envelope, interpretation)
        source_units = tuple(
            IntakeSourceUnit(
                classification=value.classification,
                text=envelope.raw_message[value.start : value.end],
            )
            for value in interpretation.source_spans
        )
        command = TurnIntakeCommand(
            world_id=envelope.world_id,
            request_id=envelope.request_id,
            session_id=envelope.session_id,
            branch_id=envelope.branch_id,
            expected_generation=envelope.expected_generation,
            expected_parent_artifact_id=envelope.expected_parent_artifact_id,
            genesis_revision_id=envelope.genesis_revision_id,
            protected_user_id=envelope.protected_user_id,
            present_character_ids=envelope.present_character_ids,
            requested_responding_npc_ids=interpretation.requested_responder_ids,
            source_units=source_units,
            requested_route_hints=interpretation.requested_route_hints,
            idempotency_key=envelope.idempotency_key,
            preflight_authority=envelope.preflight_authority,
            world_mode=envelope.world_mode,
        )
        prepared = self.turn_kernel.prepare_turn(
            command,
            access_scope=envelope.access_scope,
        )
        seed = self.seed_assembler.assemble(
            SeedDossierAssemblyRequest(
                snapshot=prepared.evidence_snapshot,
                aware_character_ids=interpretation.requested_responder_ids,
                preexpanded_exact_evidence=preexpanded_exact_evidence,
                evidence_obligations=interpretation.evidence_obligations,
                scene_anchors=interpretation.scene_anchors,
                explicit_unknowns=interpretation.explicit_unknowns,
                prohibited_inferences=interpretation.prohibited_inferences,
            )
        )
        source_view = ReasonerSourceView(
            mode=(
                ReasonerSourceMode.ADULT_NON_GRAPHIC_LEDGER
                if prepared.route is TurnRoute.CONSENT_VALID_ADULT
                else ReasonerSourceMode.ORDINARY_EXACT
            ),
            source_sha256=prepared.request.source_sha256,
            units=tuple(
                ReasonerSourceUnit(
                    source_unit_id=prepared.request.source_units[index].source_unit_id,
                    classification=span.classification,
                    safe_text=span.reasoner_safe_text,
                )
                for index, span in enumerate(interpretation.source_spans)
            ),
            contains_exact_protected_adult_prose=False,
        )
        reasoner_request = SceneReasonerRequest(
            schema_version=SceneReasonerRequest.SCHEMA_VERSION,
            prepared_turn=prepared,
            source_view=source_view,
            seed_dossier=seed.dossier,
            hard_boundaries=envelope.hard_boundaries,
            scene_depth_mode=envelope.scene_depth_mode,
            behavioral_controls=envelope.behavioral_controls,
        )
        envelope_sha256 = domain_sha256(RawTurnEnvelope.SCHEMA_VERSION, envelope)
        receipt = IntentInterpretationReceipt(
            schema_version=IntentInterpretationReceipt.SCHEMA_VERSION,
            receipt_id=deterministic_id(
                IdKind.VALIDATION,
                "cera.intent_interpretation_receipt.v1",
                f"{envelope_sha256}|{interpretation.draft_sha256}",
            ),
            envelope_sha256=envelope_sha256,
            interpretation_sha256=interpretation.draft_sha256,
            adapter_version=self.intent_interpreter.adapter_version,
            external_provider_calls=self.intent_interpreter.external_provider_calls,
            authoritative_store_writes=0,
        )
        return PreparedIngressTurn(
            interpretation=interpretation,
            interpretation_receipt=receipt,
            seed_assembly=seed,
            reasoner_request=reasoner_request,
        )

    @staticmethod
    def _validate_interpretation(
        envelope: RawTurnEnvelope,
        interpretation: IntentInterpretationDraft,
    ) -> None:
        spans = interpretation.source_spans
        if spans[0].start != 0 or spans[-1].end != len(envelope.raw_message):
            raise ContractValidationError(
                "intent source spans must cover the complete raw message"
            )
        for previous, current in zip(spans, spans[1:]):
            if previous.end != current.start:
                raise ContractValidationError(
                    "intent source spans must be ordered and contiguous"
                )
        expected_class = envelope.preflight_authority.requested_content_class.value
        if interpretation.requested_content_class != expected_class:
            raise ContractValidationError(
                "intent content-class advisory conflicts with Python preflight authority"
            )
        responders = set(interpretation.requested_responder_ids)
        if (
            not responders
            or not responders.issubset(envelope.eligible_responder_ids)
            or envelope.protected_user_id in responders
        ):
            raise ContractValidationError(
                "intent responder selection exceeds Python-authorized cast"
            )
        if expected_class == RequestedContentClass.ORDINARY.value:
            for span in spans:
                exact = envelope.raw_message[span.start : span.end]
                if span.reasoner_safe_text != exact:
                    raise ContractValidationError(
                        "ordinary intent interpretation cannot rewrite source text"
                    )
