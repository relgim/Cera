"""Creator-control feedback that is never story source or character knowledge."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar

from cera.errors import ContractValidationError
from cera.schema import require_schema
from cera.serialization import domain_sha256, re_is_sha256, text_sha256


class CreatorRevisionMode(StrEnum):
    COMPOSER_REWRITE = "composer_rewrite"
    REASONER_REPLAN = "reasoner_replan"
    CORRECTION_ADJUSTMENT = "correction_adjustment"


@dataclass(frozen=True, slots=True)
class CreatorRevisionDirective:
    """Ephemeral creator guidance bound to the rejected plan and candidate."""

    SCHEMA_VERSION: ClassVar[str] = "cera.creator_revision_directive.v1"

    schema_version: str
    mode: CreatorRevisionMode
    original_sequence_plan_sha256: str
    original_sequence_beats: tuple[str, ...]
    rejected_candidate_sha256: str
    rejected_candidate_text: str
    assessment_sha256: str
    reason_codes: tuple[str, ...]
    creator_feedback: str
    creator_feedback_sha256: str

    def __post_init__(self) -> None:
        require_schema(self.schema_version, self.SCHEMA_VERSION, type(self).__name__)
        for value in (
            self.original_sequence_plan_sha256,
            self.rejected_candidate_sha256,
            self.assessment_sha256,
            self.creator_feedback_sha256,
        ):
            if not re_is_sha256(value):
                raise ContractValidationError("creator revision hash is invalid")
        if not self.original_sequence_beats or any(
            not value.strip() for value in self.original_sequence_beats
        ):
            raise ContractValidationError(
                "creator revision requires the exact prior sequence beats"
            )
        if (
            not self.rejected_candidate_text.strip()
            or len(self.rejected_candidate_text) > 100_000
            or not self.creator_feedback.strip()
            or len(self.creator_feedback) > 8_000
            or text_sha256(self.creator_feedback) != self.creator_feedback_sha256
        ):
            raise ContractValidationError("creator revision text is invalid")
        if len(self.reason_codes) != len(set(self.reason_codes)) or any(
            not value.strip() for value in self.reason_codes
        ):
            raise ContractValidationError("creator revision reason codes are invalid")

    @property
    def directive_sha256(self) -> str:
        return domain_sha256(self.SCHEMA_VERSION, self)

