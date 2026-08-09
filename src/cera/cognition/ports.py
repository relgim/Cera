"""Provider-neutral role ports for full-model CERA."""

from __future__ import annotations

from typing import Protocol, TypeVar

from .contracts import CognitionPlanV1

TurnInput = TypeVar("TurnInput", contravariant=True)
ValidationInput = TypeVar("ValidationInput", contravariant=True)
ValidationOutput = TypeVar("ValidationOutput", covariant=True)
AdultInput = TypeVar("AdultInput", contravariant=True)
AdultOutput = TypeVar("AdultOutput", covariant=True)


class CharacterLogicPort(Protocol[TurnInput]):
    def plan(self, request: TurnInput) -> CognitionPlanV1: ...


class SemanticValidatorPort(Protocol[ValidationInput, ValidationOutput]):
    def validate(self, request: ValidationInput) -> ValidationOutput: ...


class AdultLogicPort(Protocol[AdultInput, AdultOutput]):
    def realize(self, request: AdultInput) -> AdultOutput: ...


class AdultFilterPort(Protocol[AdultInput, AdultOutput]):
    def filter(self, request: AdultInput) -> AdultOutput: ...
