"""Provider-neutral contracts for ordinary severe-quality validation.

The active Pi route owns candidate and branch custody here.  The narrow issue
and verdict semantics are reused from the qualified sequence-first Reader, but
callers import them only through :mod:`cera.reader_validation`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import ClassVar

from cera.cognition.contracts import CognitionPlanV1
from cera.errors import ContractValidationError
from cera.sequence_first.contracts import (
    ReaderIssueV1,
    ReaderStatus,
    ReaderVerdictV1,
    RetryFeedbackScope,
)
from cera.serialization import canonical_sha256, re_is_sha256, text_sha256

_IDENTITY = re.compile(r"[a-z][a-z0-9_.:-]{0,191}\Z")
_DEPTHS = frozenset({"off", "short", "auto", "medium", "long", "epic"})


def _text(value: str, field: str, *, maximum: int) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ContractValidationError(f"{field} must be non-empty and bounded")


def _optional_text(value: str | None, field: str, *, maximum: int) -> None:
    if value is not None:
        _text(value, field, maximum=maximum)


def _identity(value: str, field: str) -> None:
    if not isinstance(value, str) or _IDENTITY.fullmatch(value) is None:
        raise ContractValidationError(f"{field} must be a stable identity")


def _sha(value: str, field: str) -> None:
    if not re_is_sha256(value):
        raise ContractValidationError(f"{field} must be SHA-256")


def _unique(values: tuple[str, ...], field: str) -> None:
    if len(values) != len(set(values)):
        raise ContractValidationError(f"{field} contains duplicates")


@dataclass(frozen=True, slots=True)
class ReaderCharacterContextV1:
    """Small ordinary-safe character/voice projection for quality judgment."""

    character_id: str
    display_name: str
    role: str | None = None
    character_logic: str | None = None
    voice: str | None = None
    voice_guidance: str | None = None

    def __post_init__(self) -> None:
        _identity(self.character_id, "reader_character.character_id")
        if not self.character_id.startswith("character:"):
            raise ContractValidationError("Reader character context requires a character ID")
        _text(self.display_name, "reader_character.display_name", maximum=500)
        for field_name in ("role", "character_logic", "voice", "voice_guidance"):
            _optional_text(
                getattr(self, field_name),
                f"reader_character.{field_name}",
                maximum=4_000,
            )


@dataclass(frozen=True, slots=True)
class ReaderRelationshipContextV1:
    relationship_id: str
    concise_context: str

    def __post_init__(self) -> None:
        _identity(self.relationship_id, "reader_relationship.relationship_id")
        _text(
            self.concise_context,
            "reader_relationship.concise_context",
            maximum=4_000,
        )


@dataclass(frozen=True, slots=True)
class ReaderValidationRequestV1:
    """Exact candidate package visible to the isolated ordinary Reader.

    There is intentionally no Luna result, review state, branch hash, provider
    receipt, or acceptance decision in this provider-visible object.
    """

    SCHEMA_VERSION: ClassVar[str] = "cera.reader_validation.request.v1"

    schema_version: str
    cognition_plan: CognitionPlanV1
    exact_candidate_prose: str
    immediate_prior_accepted_prose: str | None
    current_public_state: str
    scene_depth: str
    character_context: tuple[ReaderCharacterContextV1, ...]
    relationship_context: tuple[ReaderRelationshipContextV1, ...] = ()

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("Reader validation request schema changed")
        _text(
            self.exact_candidate_prose,
            "reader_validation.exact_candidate_prose",
            maximum=100_000,
        )
        _optional_text(
            self.immediate_prior_accepted_prose,
            "reader_validation.immediate_prior_accepted_prose",
            maximum=100_000,
        )
        _text(
            self.current_public_state,
            "reader_validation.current_public_state",
            maximum=20_000,
        )
        if self.scene_depth not in _DEPTHS:
            raise ContractValidationError("Reader validation scene depth is invalid")
        if not self.character_context:
            raise ContractValidationError("Reader validation requires character context")
        if len(self.character_context) > 64 or len(self.relationship_context) > 128:
            raise ContractValidationError("Reader validation context exceeds its bound")
        _unique(
            tuple(value.character_id for value in self.character_context),
            "reader_validation.character_context",
        )
        _unique(
            tuple(value.relationship_id for value in self.relationship_context),
            "reader_validation.relationship_context",
        )

    @property
    def current_plan_item_keys(self) -> tuple[str, ...]:
        return tuple(item.item_key for item in self.cognition_plan.sequence.items)


@dataclass(frozen=True, slots=True)
class ReaderValidationCustodyV1:
    """Python-only identity and byte binding; never sent to the Reader."""

    SCHEMA_VERSION: ClassVar[str] = "cera.reader_validation.custody.v1"

    schema_version: str
    request_id: str
    candidate_id: str
    world_id: str
    branch_id: str
    accepted_head_sha256: str | None
    candidate_sha256: str
    cognition_plan_sha256: str
    candidate_prose_sha256: str
    immediate_prior_accepted_prose_sha256: str | None
    validation_request_sha256: str

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("Reader validation custody schema changed")
        for field_name in ("request_id", "candidate_id", "world_id", "branch_id"):
            _identity(getattr(self, field_name), f"reader_validation_custody.{field_name}")
        for field_name in (
            "candidate_sha256",
            "cognition_plan_sha256",
            "candidate_prose_sha256",
            "validation_request_sha256",
        ):
            _sha(getattr(self, field_name), f"reader_validation_custody.{field_name}")
        for field_name in (
            "accepted_head_sha256",
            "immediate_prior_accepted_prose_sha256",
        ):
            value = getattr(self, field_name)
            if value is not None:
                _sha(value, f"reader_validation_custody.{field_name}")


@dataclass(frozen=True, slots=True)
class BoundReaderValidationV1:
    request: ReaderValidationRequestV1
    custody: ReaderValidationCustodyV1
    verdict: ReaderVerdictV1

    def __post_init__(self) -> None:
        if canonical_sha256(self.request.cognition_plan) != self.custody.cognition_plan_sha256:
            raise ContractValidationError("Reader custody lost the cognition plan")
        if text_sha256(self.request.exact_candidate_prose) != self.custody.candidate_prose_sha256:
            raise ContractValidationError("Reader custody lost the frozen candidate prose")
        prior = self.request.immediate_prior_accepted_prose
        prior_sha256 = None if prior is None else text_sha256(prior)
        if prior_sha256 != self.custody.immediate_prior_accepted_prose_sha256:
            raise ContractValidationError("Reader custody lost immediate prior prose")
        if canonical_sha256(self.request) != self.custody.validation_request_sha256:
            raise ContractValidationError("Reader custody lost the exact request")
        plan_item_keys = set(self.request.current_plan_item_keys)
        for issue in self.verdict.issues:
            if issue.exact_quote is not None and issue.exact_quote not in (
                self.request.exact_candidate_prose
            ):
                raise ContractValidationError("Reader issue quote is not exact candidate prose")
            if (
                issue.omitted_planner_item_key is not None
                and issue.omitted_planner_item_key not in plan_item_keys
            ):
                raise ContractValidationError("Reader issue cites an unknown current plan item")

    @property
    def passed(self) -> bool:
        return self.verdict.status is ReaderStatus.ACCEPTED

    @property
    def concise_failures(self) -> tuple[str, ...]:
        return tuple(issue.concise_explanation for issue in self.verdict.issues)

    @property
    def binding_sha256(self) -> str:
        return canonical_sha256(self)


__all__ = [
    "BoundReaderValidationV1",
    "ReaderCharacterContextV1",
    "ReaderIssueV1",
    "ReaderRelationshipContextV1",
    "ReaderStatus",
    "ReaderValidationCustodyV1",
    "ReaderValidationRequestV1",
    "ReaderVerdictV1",
    "RetryFeedbackScope",
]
