"""Shadow-only SillyTavern-compatible entry into continuous request custody.

This module does not alter the installed client or the active D-180 adapter.
It proves that a production-shaped request can reach the continuous route only
after raw ingress preparation, closed classification, durable receipt issue,
and restart-safe receipt resolution.
"""

from __future__ import annotations

from dataclasses import dataclass
from cera.errors import StateConflictError
from cera.evidence import ExactEvidence
from cera.ingress import RawTurnEnvelope, RawTurnIngressFacade
from cera.sillytavern.models import SillyTavernChatRequest
from cera.serialization import text_sha256

from .contracts import CharacterSummaryEnvelopeV1
from .ingress import PreparedContinuousIngressBridge
from .runtime import ContinuousTurnRequestV1


@dataclass(frozen=True, slots=True)
class ContinuousShadowIngressResultV1:
    """Prepared shadow request and the exact durable custody reference used."""

    request: ContinuousTurnRequestV1
    ingress_receipt_id: str
    ingress_receipt_sha256: str


class ContinuousSillyTavernShadowRequestBridge:
    """Build a continuous request from the real ingress seam, never raw hashes."""

    def __init__(
        self,
        *,
        raw_ingress: RawTurnIngressFacade,
        prepared_ingress: PreparedContinuousIngressBridge,
    ) -> None:
        self.raw_ingress = raw_ingress
        self.prepared_ingress = prepared_ingress

    def prepare(
        self,
        *,
        chat_request: SillyTavernChatRequest,
        envelope: RawTurnEnvelope,
        scene_id: str,
        turn_id: str,
        character_summaries: tuple[CharacterSummaryEnvelopeV1, ...] = (),
        preexpanded_exact_evidence: tuple[ExactEvidence, ...] = (),
    ) -> ContinuousShadowIngressResultV1:
        if chat_request.latest_user_content != envelope.raw_message:
            raise StateConflictError(
                "SillyTavern shadow request changed the raw ingress message"
            )
        prepared = self.raw_ingress.prepare(
            envelope,
            preexpanded_exact_evidence=preexpanded_exact_evidence,
        )
        issued = self.prepared_ingress.issue(
            envelope=envelope,
            prepared=prepared,
            turn_id=turn_id,
        )
        resolved = self.prepared_ingress.authority.resolve(
            receipt_id=issued.receipt_id,
            receipt_sha256=issued.receipt_sha256,
        )
        if (
            resolved.raw_source_sha256 != text_sha256(chat_request.latest_user_content)
            or resolved.world_id != envelope.world_id.value
            or resolved.branch_id != envelope.branch_id.value
            or resolved.session_id != str(envelope.session_id)
            or resolved.request_id != str(envelope.request_id)
            or resolved.turn_id != turn_id
        ):
            raise StateConflictError(
                "resolved continuous ingress receipt changed request authority"
            )
        request = ContinuousTurnRequestV1(
            world_id=resolved.world_id,
            branch_id=resolved.branch_id,
            session_id=resolved.session_id,
            request_id=resolved.request_id,
            idempotency_key_sha256=resolved.idempotency_key_sha256,
            scene_id=scene_id,
            turn_id=resolved.turn_id,
            user_message=chat_request.latest_user_content,
            ingress_receipt_id=resolved.receipt_id,
            ingress_receipt_sha256=resolved.receipt_sha256,
            character_summaries=character_summaries,
            cera_scene_change=chat_request.cera_scene_change,
        )
        return ContinuousShadowIngressResultV1(
            request=request,
            ingress_receipt_id=resolved.receipt_id,
            ingress_receipt_sha256=resolved.receipt_sha256,
        )
