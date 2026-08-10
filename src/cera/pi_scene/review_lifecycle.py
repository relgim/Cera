"""Closed durable state for ordinary provisional validation and qualification.

The Writer candidate itself remains owned by :mod:`cera.pi_scene.contracts` and
the protected review store.  This module carries only safe hashes, closed
states, and deterministic Python qualification receipts; provider prompts and
packets stay in their existing protected custody.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar

from cera.errors import ContractValidationError
from cera.serialization import canonical_sha256


def _sha256(value: str, field_name: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[a-f0-9]{64}", value) is None:
        raise ContractValidationError(f"{field_name} is not a SHA-256 digest")
    return value


class OrdinaryReviewMode(StrEnum):
    """Per-chat creator review behavior after every hard check passes."""

    AUTOMATIC = "automatic"
    MANUAL = "manual"


class OrdinaryReviewPhase(StrEnum):
    """Durable ordinary candidate lifecycle before immutable acceptance."""

    LEGACY = "legacy"
    WRITER_FROZEN = "writer_frozen"
    VALIDATING = "validating"
    VALIDATION_BLOCKED = "validation_blocked"
    VALIDATION_REJECTED = "validation_rejected"
    AWAITING_MANUAL_ACCEPT = "awaiting_manual_accept"
    QUALIFIED = "qualified"


class OrdinaryValidationOwner(StrEnum):
    LUNA = "luna"
    READER = "reader"
    PYTHON = "python"


@dataclass(frozen=True, slots=True)
class OrdinaryValidationInputBindingV1:
    """Content-free proof that both validators received one frozen candidate."""

    SCHEMA_VERSION: ClassVar[str] = "cera.pi_scene.validation_input_binding.v1"

    schema_version: str
    candidate_sha256: str
    semantic_request_sha256: str
    semantic_custody_sha256: str
    reader_request_sha256: str
    reader_custody_sha256: str
    luna_chain_id: str | None
    reader_chain_id: str | None
    binding_sha256: str

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("ordinary validation-input schema changed")
        for field_name in (
            "candidate_sha256",
            "semantic_request_sha256",
            "semantic_custody_sha256",
            "reader_request_sha256",
            "reader_custody_sha256",
        ):
            _sha256(getattr(self, field_name), field_name)
        for field_name in ("luna_chain_id", "reader_chain_id"):
            value = getattr(self, field_name)
            if value is not None and (
                not isinstance(value, str)
                or not value.strip()
                or len(value) > 512
                or "\x00" in value
            ):
                raise ContractValidationError(f"{field_name} is invalid")
        expected = canonical_sha256(
            {
                "schema_version": self.schema_version,
                "candidate_sha256": self.candidate_sha256,
                "semantic_request_sha256": self.semantic_request_sha256,
                "semantic_custody_sha256": self.semantic_custody_sha256,
                "reader_request_sha256": self.reader_request_sha256,
                "reader_custody_sha256": self.reader_custody_sha256,
                "luna_chain_id": self.luna_chain_id,
                "reader_chain_id": self.reader_chain_id,
            }
        )
        if _sha256(self.binding_sha256, "binding_sha256") != expected:
            raise ContractValidationError("ordinary validation-input binding changed")

    @classmethod
    def create(
        cls,
        *,
        candidate_sha256: str,
        semantic_request_sha256: str,
        semantic_custody_sha256: str,
        reader_request_sha256: str,
        reader_custody_sha256: str,
        luna_chain_id: str | None = None,
        reader_chain_id: str | None = None,
    ) -> OrdinaryValidationInputBindingV1:
        body = {
            "schema_version": cls.SCHEMA_VERSION,
            "candidate_sha256": candidate_sha256,
            "semantic_request_sha256": semantic_request_sha256,
            "semantic_custody_sha256": semantic_custody_sha256,
            "reader_request_sha256": reader_request_sha256,
            "reader_custody_sha256": reader_custody_sha256,
            "luna_chain_id": luna_chain_id,
            "reader_chain_id": reader_chain_id,
        }
        return cls(
            schema_version=cls.SCHEMA_VERSION,
            candidate_sha256=candidate_sha256,
            semantic_request_sha256=semantic_request_sha256,
            semantic_custody_sha256=semantic_custody_sha256,
            reader_request_sha256=reader_request_sha256,
            reader_custody_sha256=reader_custody_sha256,
            luna_chain_id=luna_chain_id,
            reader_chain_id=reader_chain_id,
            binding_sha256=canonical_sha256(body),
        )


@dataclass(frozen=True, slots=True)
class OrdinaryValidationFailureV1:
    """Privacy-safe provider/runtime block attached to one validator owner."""

    SCHEMA_VERSION: ClassVar[str] = "cera.pi_scene.validation_failure.v1"

    schema_version: str
    owner: OrdinaryValidationOwner
    failure_code: str
    concise_explanation: str
    chain_id: str | None = None

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("ordinary validation-failure schema changed")
        if type(self.owner) is not OrdinaryValidationOwner:
            raise ContractValidationError("ordinary validation-failure owner changed")
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,95}", self.failure_code):
            raise ContractValidationError("ordinary validation failure code is invalid")
        if (
            not isinstance(self.concise_explanation, str)
            or not self.concise_explanation.strip()
            or len(self.concise_explanation) > 500
        ):
            raise ContractValidationError("ordinary validation failure explanation is invalid")
        if self.chain_id is not None and (
            not isinstance(self.chain_id, str)
            or not self.chain_id.strip()
            or len(self.chain_id) > 512
            or "\x00" in self.chain_id
        ):
            raise ContractValidationError("ordinary validation failure chain is invalid")


@dataclass(frozen=True, slots=True)
class OrdinaryPythonQualificationV1:
    """Deterministic authorization for ordinary automatic/manual acceptance."""

    SCHEMA_VERSION: ClassVar[str] = "cera.pi_scene.python_qualification.v1"

    schema_version: str
    review_id: str
    candidate_sha256: str
    validation_input_binding_sha256: str
    semantic_validation_sha256: str
    reader_validation_sha256: str
    accepted_head_before_sha256: str
    deterministic_passed: bool
    identity_passed: bool
    custody_passed: bool
    privacy_passed: bool
    qualification_sha256: str

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("ordinary Python-qualification schema changed")
        if re.fullmatch(r"review-[a-f0-9]{28}", self.review_id) is None:
            raise ContractValidationError("ordinary Python qualification review is invalid")
        for field_name in (
            "candidate_sha256",
            "validation_input_binding_sha256",
            "semantic_validation_sha256",
            "reader_validation_sha256",
            "accepted_head_before_sha256",
        ):
            _sha256(getattr(self, field_name), field_name)
        if any(
            type(value) is not bool or not value
            for value in (
                self.deterministic_passed,
                self.identity_passed,
                self.custody_passed,
                self.privacy_passed,
            )
        ):
            raise ContractValidationError("ordinary Python qualification did not pass")
        body = {
            "schema_version": self.schema_version,
            "review_id": self.review_id,
            "candidate_sha256": self.candidate_sha256,
            "validation_input_binding_sha256": self.validation_input_binding_sha256,
            "semantic_validation_sha256": self.semantic_validation_sha256,
            "reader_validation_sha256": self.reader_validation_sha256,
            "accepted_head_before_sha256": self.accepted_head_before_sha256,
            "deterministic_passed": self.deterministic_passed,
            "identity_passed": self.identity_passed,
            "custody_passed": self.custody_passed,
            "privacy_passed": self.privacy_passed,
        }
        if _sha256(self.qualification_sha256, "qualification_sha256") != canonical_sha256(body):
            raise ContractValidationError("ordinary Python qualification binding changed")

    @classmethod
    def create(
        cls,
        *,
        review_id: str,
        candidate_sha256: str,
        validation_input_binding_sha256: str,
        semantic_validation_sha256: str,
        reader_validation_sha256: str,
        accepted_head_before_sha256: str,
    ) -> OrdinaryPythonQualificationV1:
        body = {
            "schema_version": cls.SCHEMA_VERSION,
            "review_id": review_id,
            "candidate_sha256": candidate_sha256,
            "validation_input_binding_sha256": validation_input_binding_sha256,
            "semantic_validation_sha256": semantic_validation_sha256,
            "reader_validation_sha256": reader_validation_sha256,
            "accepted_head_before_sha256": accepted_head_before_sha256,
            "deterministic_passed": True,
            "identity_passed": True,
            "custody_passed": True,
            "privacy_passed": True,
        }
        return cls(
            schema_version=cls.SCHEMA_VERSION,
            review_id=review_id,
            candidate_sha256=candidate_sha256,
            validation_input_binding_sha256=validation_input_binding_sha256,
            semantic_validation_sha256=semantic_validation_sha256,
            reader_validation_sha256=reader_validation_sha256,
            accepted_head_before_sha256=accepted_head_before_sha256,
            deterministic_passed=True,
            identity_passed=True,
            custody_passed=True,
            privacy_passed=True,
            qualification_sha256=canonical_sha256(body),
        )
