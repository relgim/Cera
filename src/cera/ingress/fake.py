"""Scripted provider-free intent interpreter."""

from __future__ import annotations

from cera.config import Environment
from cera.errors import ConfigurationError, ContractValidationError

from .models import (
    IntentInterpretationDraft,
    IntentInterpreterPort,
    RawTurnEnvelope,
)


class ScriptedIntentInterpreterPort(IntentInterpreterPort):
    adapter_version = "cera.scripted_intent_interpreter.v1"
    external_provider_calls = 0
    production_prohibited = True

    def __init__(
        self,
        draft: IntentInterpretationDraft,
        *,
        environment: Environment = Environment.TEST,
    ) -> None:
        if environment is Environment.PRODUCTION:
            raise ConfigurationError(
                "ScriptedIntentInterpreterPort is prohibited in production"
            )
        self.draft = draft
        self.invocation_count = 0

    def interpret(self, envelope: RawTurnEnvelope) -> IntentInterpretationDraft:
        if not isinstance(envelope, RawTurnEnvelope):
            raise ContractValidationError(
                "intent interpreter requires a raw-turn envelope"
            )
        self.invocation_count += 1
        return self.draft
