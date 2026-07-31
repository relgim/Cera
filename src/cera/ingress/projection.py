"""Deterministic raw-turn projection before runtime Codex reasoning.

This adapter deliberately performs no semantic interpretation.  It preserves
the exact ordinary user message, exposes the complete Python-authorized
candidate cast, and leaves participant selection, intent, query formulation,
and consequence reasoning to the Scene Reasoner.
"""

from __future__ import annotations

from cera.contracts import SourceUnitClassification
from cera.errors import ContractValidationError
from cera.kernel import RequestedContentClass

from .models import (
    IntentInterpretationDraft,
    IntentInterpreterPort,
    InterpretedSourceSpan,
    RawTurnEnvelope,
)


class DeterministicRawTurnProjectionPort(IntentInterpreterPort):
    """Provider-free exact-source projection; never a character reasoner."""

    adapter_version = "cera.deterministic_raw_turn_projection.v1"
    external_provider_calls = 0
    production_prohibited = False

    def interpret(self, envelope: RawTurnEnvelope) -> IntentInterpretationDraft:
        content_class = envelope.preflight_authority.requested_content_class
        if content_class is RequestedContentClass.ADULT:
            raise ContractValidationError(
                "adult raw ingress requires an authority-bound non-graphic reasoner ledger; "
                "deterministic projection cannot rewrite protected source semantics"
            )
        return IntentInterpretationDraft(
            schema_version=IntentInterpretationDraft.SCHEMA_VERSION,
            source_spans=(
                InterpretedSourceSpan(
                    start=0,
                    end=len(envelope.raw_message),
                    classification=SourceUnitClassification.MESSAGE,
                    reasoner_safe_text=envelope.raw_message,
                ),
            ),
            requested_content_class=content_class.value,
            # This is candidate-cast authorization, not semantic selection.
            # Runtime Codex chooses the actual responders in SceneDecision.
            requested_responder_ids=envelope.eligible_responder_ids,
            requested_route_hints=(),
            evidence_obligations=(),
            scene_anchors=(),
            explicit_unknowns=(
                "Responder selection and any unstated protected-user choice remain unresolved.",
            ),
            prohibited_inferences=(
                "Do not author the protected user's unsupplied action, dialogue, thought, emotion, motive, consent, refusal, destination, or commitment.",
                "Do not transfer owner-private or character-limited knowledge between characters.",
            ),
        )
