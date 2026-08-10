"""Canonical identity scope for one provider-stage Retry occurrence."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar

from cera.errors import ContractValidationError
from cera.serialization import bytes_sha256, domain_sha256, re_is_sha256, to_primitive

from .provider_stage_retry import (
    ProviderFamily,
    ProviderModelFamily,
    ProviderStage,
    ProviderStageRetryIdentityV1,
)

_STAGE_OWNERS: dict[ProviderStage, tuple[ProviderFamily, ProviderModelFamily]] = {
    ProviderStage.PLANNER: (ProviderFamily.CODEX, ProviderModelFamily.SOL),
    ProviderStage.SEMANTIC_VALIDATOR: (ProviderFamily.CODEX, ProviderModelFamily.LUNA),
    ProviderStage.WRITER: (ProviderFamily.DEEPSEEK, ProviderModelFamily.DEEPSEEK_V4),
    ProviderStage.RECORDER: (ProviderFamily.DEEPSEEK, ProviderModelFamily.DEEPSEEK_V4),
    ProviderStage.ADULT_SCENE: (ProviderFamily.DEEPSEEK, ProviderModelFamily.DEEPSEEK_V4),
    ProviderStage.ADULT_FILTER: (ProviderFamily.DEEPSEEK, ProviderModelFamily.DEEPSEEK_V4),
}


def provider_stage_owner(
    stage: ProviderStage,
) -> tuple[ProviderFamily, ProviderModelFamily]:
    """Return the policy-owned provider and model family for ``stage``."""

    if type(stage) is not ProviderStage:
        raise ContractValidationError("provider-stage occurrence stage is not closed")
    return _STAGE_OWNERS[stage]


def _require_identifier(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise ContractValidationError(f"provider-stage occurrence {field_name} is invalid")


def _request_sha256(
    *,
    world_id: str,
    branch_id: str,
    request_id: str,
    generation_id: str,
) -> str:
    return domain_sha256(
        "cera.provider_stage_retry_request_identity.v1",
        {
            "world_id": world_id,
            "branch_id": branch_id,
            "request_id": request_id,
            "generation_id": generation_id,
        },
    )


def _authority_sha256(
    *,
    world_id: str,
    branch_id: str,
    generation_id: str,
    accepted_state_sha256: str,
    authority_binding_sha256: str,
) -> str:
    return domain_sha256(
        "cera.provider_stage_retry_accepted_authority.v1",
        {
            "world_id": world_id,
            "branch_id": branch_id,
            "generation_id": generation_id,
            "accepted_state_sha256": accepted_state_sha256,
            "authority_binding_sha256": authority_binding_sha256,
        },
    )


def _request_occurrence_sha256(
    *,
    world_id: str,
    branch_id: str,
    request_id: str,
    generation_id: str,
    request_sha256: str,
    stage: ProviderStage,
    stage_ordinal: int,
    accepted_state_sha256: str,
    stage_input_sha256: str,
) -> str:
    return domain_sha256(
        "cera.provider_stage_retry_occurrence.v1",
        {
            "world_id": world_id,
            "branch_id": branch_id,
            "request_id": request_id,
            "generation_id": generation_id,
            "request_sha256": request_sha256,
            "stage": stage.value,
            "stage_ordinal": stage_ordinal,
            "accepted_state_sha256": accepted_state_sha256,
            "stage_input_sha256": stage_input_sha256,
        },
    )


@dataclass(frozen=True, slots=True)
class ProviderStageRetryOccurrenceScopeV1:
    """Full logical scope for one unique stage occurrence.

    Human-readable identifiers remain inside Python authority.  The shared
    retry identity receives only deterministic hashes, while the occurrence
    hash binds every field that must prevent a chat-lifetime Retry counter.
    """

    SCHEMA_VERSION: ClassVar[str] = "cera.provider_stage_retry_occurrence_scope.v1"

    schema_version: str
    world_id: str
    branch_id: str
    request_id: str
    generation_id: str
    stage: ProviderStage
    stage_ordinal: int
    accepted_state_sha256: str
    stage_input_sha256: str
    authority_binding_sha256: str
    request_sha256: str
    authority_sha256: str
    request_occurrence_sha256: str

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("provider-stage occurrence schema changed")
        for value, field_name in (
            (self.world_id, "world ID"),
            (self.branch_id, "branch ID"),
            (self.request_id, "request ID"),
            (self.generation_id, "generation ID"),
        ):
            _require_identifier(value, field_name)
        if type(self.stage) is not ProviderStage:
            raise ContractValidationError("provider-stage occurrence stage is not closed")
        if type(self.stage_ordinal) is not int or self.stage_ordinal < 1:
            raise ContractValidationError("provider-stage occurrence ordinal is invalid")
        for value, field_name in (
            (self.accepted_state_sha256, "accepted state"),
            (self.stage_input_sha256, "stage input"),
            (self.authority_binding_sha256, "authority binding"),
            (self.request_sha256, "request"),
            (self.authority_sha256, "authority"),
            (self.request_occurrence_sha256, "request occurrence"),
        ):
            if not re_is_sha256(value):
                raise ContractValidationError(
                    f"provider-stage occurrence {field_name} must be SHA-256"
                )
        expected_request = _request_sha256(
            world_id=self.world_id,
            branch_id=self.branch_id,
            request_id=self.request_id,
            generation_id=self.generation_id,
        )
        expected_authority = _authority_sha256(
            world_id=self.world_id,
            branch_id=self.branch_id,
            generation_id=self.generation_id,
            accepted_state_sha256=self.accepted_state_sha256,
            authority_binding_sha256=self.authority_binding_sha256,
        )
        expected_occurrence = _request_occurrence_sha256(
            world_id=self.world_id,
            branch_id=self.branch_id,
            request_id=self.request_id,
            generation_id=self.generation_id,
            request_sha256=self.request_sha256,
            stage=self.stage,
            stage_ordinal=self.stage_ordinal,
            accepted_state_sha256=self.accepted_state_sha256,
            stage_input_sha256=self.stage_input_sha256,
        )
        if self.request_sha256 != expected_request:
            raise ContractValidationError("provider-stage request identity changed")
        if self.authority_sha256 != expected_authority:
            raise ContractValidationError("provider-stage accepted authority changed")
        if self.request_occurrence_sha256 != expected_occurrence:
            raise ContractValidationError("provider-stage occurrence identity changed")

    @classmethod
    def create(
        cls,
        *,
        world_id: str,
        branch_id: str,
        request_id: str,
        generation_id: str,
        stage: ProviderStage,
        stage_ordinal: int,
        accepted_state_sha256: str,
        exact_input: bytes,
        authority_binding: object,
    ) -> ProviderStageRetryOccurrenceScopeV1:
        """Create a scope after the exact semantic packet has been frozen."""

        for value, field_name in (
            (world_id, "world ID"),
            (branch_id, "branch ID"),
            (request_id, "request ID"),
            (generation_id, "generation ID"),
        ):
            _require_identifier(value, field_name)
        provider_stage_owner(stage)
        if type(stage_ordinal) is not int or stage_ordinal < 1:
            raise ContractValidationError("provider-stage occurrence ordinal is invalid")
        if not re_is_sha256(accepted_state_sha256):
            raise ContractValidationError("provider-stage accepted state must be SHA-256")
        if not isinstance(exact_input, bytes) or not exact_input:
            raise ContractValidationError("provider-stage exact input must be non-empty bytes")
        # Canonicalization here is deliberate: it rejects mutable or opaque
        # authority objects that cannot be bound deterministically.
        authority_binding_sha256 = domain_sha256(
            "cera.provider_stage_retry_authority_binding.v1",
            to_primitive(authority_binding),
        )
        stage_input_sha256 = bytes_sha256(exact_input)
        request_sha256 = _request_sha256(
            world_id=world_id,
            branch_id=branch_id,
            request_id=request_id,
            generation_id=generation_id,
        )
        authority_sha256 = _authority_sha256(
            world_id=world_id,
            branch_id=branch_id,
            generation_id=generation_id,
            accepted_state_sha256=accepted_state_sha256,
            authority_binding_sha256=authority_binding_sha256,
        )
        return cls(
            schema_version=cls.SCHEMA_VERSION,
            world_id=world_id,
            branch_id=branch_id,
            request_id=request_id,
            generation_id=generation_id,
            stage=stage,
            stage_ordinal=stage_ordinal,
            accepted_state_sha256=accepted_state_sha256,
            stage_input_sha256=stage_input_sha256,
            authority_binding_sha256=authority_binding_sha256,
            request_sha256=request_sha256,
            authority_sha256=authority_sha256,
            request_occurrence_sha256=_request_occurrence_sha256(
                world_id=world_id,
                branch_id=branch_id,
                request_id=request_id,
                generation_id=generation_id,
                request_sha256=request_sha256,
                stage=stage,
                stage_ordinal=stage_ordinal,
                accepted_state_sha256=accepted_state_sha256,
                stage_input_sha256=stage_input_sha256,
            ),
        )

    @property
    def identity(self) -> ProviderStageRetryIdentityV1:
        """Project the full occurrence scope into the hash-only store DTO."""

        provider, model_family = provider_stage_owner(self.stage)
        return ProviderStageRetryIdentityV1(
            schema_version=ProviderStageRetryIdentityV1.SCHEMA_VERSION,
            provider=provider,
            model_family=model_family,
            stage=self.stage,
            request_occurrence_sha256=self.request_occurrence_sha256,
            request_sha256=self.request_sha256,
            stage_input_sha256=self.stage_input_sha256,
            authority_sha256=self.authority_sha256,
            story_state_committed=self.stage is ProviderStage.RECORDER,
        )

    def to_payload(self) -> dict[str, Any]:
        payload = to_primitive(self)
        if not isinstance(payload, dict):
            raise ContractValidationError("provider-stage occurrence did not encode as an object")
        return payload


__all__ = ["ProviderStageRetryOccurrenceScopeV1", "provider_stage_owner"]
