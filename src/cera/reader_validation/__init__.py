"""Ordinary Pi Reader contracts, prompt, session, and provider adapter."""

from .bridge import build_reader_validation_input
from .contracts import (
    BoundReaderValidationV1,
    ReaderCharacterContextV1,
    ReaderIssueV1,
    ReaderRelationshipContextV1,
    ReaderStatus,
    ReaderValidationCustodyV1,
    ReaderValidationRequestV1,
    ReaderVerdictV1,
    RetryFeedbackScope,
)
from .prompting import (
    SOL_READER_BASE_INSTRUCTIONS,
    SOL_READER_PROFILE,
    build_reader_validation_prompt,
)
from .provider import CodexSolReaderBackend, sol_reader_route
from .schema import reader_verdict_json_schema
from .session import FreshSolReaderFactory, FreshSolReaderSession, ReaderBackendPort

__all__ = [
    "BoundReaderValidationV1",
    "CodexSolReaderBackend",
    "FreshSolReaderFactory",
    "FreshSolReaderSession",
    "ReaderBackendPort",
    "ReaderCharacterContextV1",
    "ReaderIssueV1",
    "ReaderRelationshipContextV1",
    "ReaderStatus",
    "ReaderValidationCustodyV1",
    "ReaderValidationRequestV1",
    "ReaderVerdictV1",
    "RetryFeedbackScope",
    "SOL_READER_BASE_INSTRUCTIONS",
    "SOL_READER_PROFILE",
    "build_reader_validation_input",
    "build_reader_validation_prompt",
    "reader_verdict_json_schema",
    "sol_reader_route",
]
