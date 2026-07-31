"""Shared contract records."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from cera.errors import ErrorCode, RetryMode
from cera.ids import IdKind, TypedId
from cera.schema import require_schema

from ._validation import kind, non_empty


@dataclass(frozen=True, slots=True)
class ErrorEnvelope:
    SCHEMA_VERSION: ClassVar[str] = "cera.error.v1"

    schema_version: str
    error_code: ErrorCode
    message: str
    trace_id: TypedId
    request_id: TypedId
    branch_id: TypedId
    generation_id: TypedId
    stage: str
    story_state_committed: bool
    retry_mode: RetryMode
    details: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        require_schema(self.schema_version, self.SCHEMA_VERSION, type(self).__name__)
        kind(self.trace_id, IdKind.TRACE, "trace_id")
        kind(self.request_id, IdKind.REQUEST, "request_id")
        kind(self.branch_id, IdKind.BRANCH, "branch_id")
        kind(self.generation_id, IdKind.GENERATION, "generation_id")
        non_empty(self.message, "message")
        non_empty(self.stage, "stage")
        if self.story_state_committed:
            raise ValueError("error envelopes cannot claim story state was committed")

