"""Stable and candidate-local instructions for the ordinary Reader."""

from __future__ import annotations

from cera.sequence_first.prompting import (
    READER_BASE_INSTRUCTIONS as _QUALIFIED_READER_BASE_INSTRUCTIONS,
)
from cera.serialization import canonical_json

from .contracts import ReaderValidationRequestV1

SOL_READER_PROFILE = "cera.reader_validation.sol_medium.v1"
SOL_READER_BASE_INSTRUCTIONS = (
    _QUALIFIED_READER_BASE_INSTRUCTIONS
    + " The supplied cognition plan, immediate prior accepted prose, depth, and "
    "ordinary-safe character context exist only to judge the frozen candidate's "
    "reader-facing quality. Do not perform Luna's semantic-validation role, infer "
    "another validator's verdict, decide acceptance, or broaden the severe-quality "
    "floor into ordinary style preference."
)


def build_reader_validation_prompt(request: ReaderValidationRequestV1) -> str:
    return "Judge this exact frozen ordinary candidate package:\n" + canonical_json(request)


__all__ = [
    "SOL_READER_BASE_INSTRUCTIONS",
    "SOL_READER_PROFILE",
    "build_reader_validation_prompt",
]
