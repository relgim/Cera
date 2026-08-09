"""Python-owned custody for a semantic cognition result."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import ClassVar

from cera.errors import ContractValidationError

_IDENTITY = re.compile(r"[a-z][a-z0-9_.:-]{0,191}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


@dataclass(frozen=True, slots=True)
class CognitionCustodyEnvelopeV1:
    """Never submitted for model authorship."""

    SCHEMA_VERSION: ClassVar[str] = "cera.cognition.custody.v1"

    request_id: str
    world_id: str
    branch_id: str
    turn_id: str
    candidate_id: str
    parent_accepted_turn_id: str | None
    exact_source_sha256: str
    accepted_head_sha256: str | None
    context_revision_sha256: str
    provider_operation_id: str

    def __post_init__(self) -> None:
        for field in (
            "request_id",
            "world_id",
            "branch_id",
            "turn_id",
            "candidate_id",
            "provider_operation_id",
        ):
            value = getattr(self, field)
            if not isinstance(value, str) or _IDENTITY.fullmatch(value) is None:
                raise ContractValidationError(f"cognition_custody.{field} is invalid")
        if (
            self.parent_accepted_turn_id is not None
            and _IDENTITY.fullmatch(self.parent_accepted_turn_id) is None
        ):
            raise ContractValidationError("cognition_custody.parent_accepted_turn_id is invalid")
        for field in (
            "exact_source_sha256",
            "accepted_head_sha256",
            "context_revision_sha256",
        ):
            value = getattr(self, field)
            if value is not None and (
                not isinstance(value, str) or _SHA256.fullmatch(value) is None
            ):
                raise ContractValidationError(f"cognition_custody.{field} is invalid")
