"""Raw-turn ingress and provider-neutral interpretation contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import ClassVar, Protocol

from cera.contracts import (
    BehavioralTurnControls,
    SceneDepthMode,
    SourceUnitClassification,
)
from cera.evidence import (
    EvidenceAccessScope,
    EvidenceObligation,
    EvidenceWorldMode,
)
from cera.errors import ContractValidationError
from cera.ids import IdKind, TypedId, require_kind
from cera.kernel import PreflightAuthority
from cera.schema import require_schema
from cera.serialization import domain_sha256, re_is_sha256


@dataclass(frozen=True, slots=True)
class RawTurnEnvelope:
    SCHEMA_VERSION: ClassVar[str] = "cera.raw_turn_envelope.v3"

    schema_version: str
    world_id: TypedId
    request_id: TypedId
    session_id: TypedId
    branch_id: TypedId
    expected_generation: int
    expected_parent_artifact_id: TypedId | None
    genesis_revision_id: TypedId
    protected_user_id: TypedId
    present_character_ids: tuple[TypedId, ...]
    eligible_responder_ids: tuple[TypedId, ...]
    raw_message: str
    idempotency_key: str
    world_mode: EvidenceWorldMode
    access_scope: EvidenceAccessScope
    preflight_authority: PreflightAuthority
    hard_boundaries: tuple[str, ...]
    scene_depth_mode: SceneDepthMode = SceneDepthMode.AUTO
    behavioral_controls: BehavioralTurnControls = field(
        default_factory=BehavioralTurnControls.creator_default
    )

    def __post_init__(self) -> None:
        require_schema(self.schema_version, self.SCHEMA_VERSION, type(self).__name__)
        require_kind(self.world_id, IdKind.WORLD, "world_id")
        require_kind(self.request_id, IdKind.REQUEST, "request_id")
        require_kind(self.session_id, IdKind.SESSION, "session_id")
        require_kind(self.branch_id, IdKind.BRANCH, "branch_id")
        require_kind(
            self.genesis_revision_id,
            IdKind.GENESIS_REVISION,
            "genesis_revision_id",
        )
        require_kind(self.protected_user_id, IdKind.CHARACTER, "protected_user_id")
        if self.expected_parent_artifact_id is not None:
            require_kind(
                self.expected_parent_artifact_id,
                IdKind.ARTIFACT,
                "expected_parent_artifact_id",
            )
        if self.expected_generation < 0:
            raise ContractValidationError("expected generation cannot be negative")
        if not self.raw_message:
            raise ContractValidationError("raw turn message cannot be empty")
        if not self.idempotency_key.strip() or not self.hard_boundaries:
            raise ContractValidationError(
                "raw turn requires idempotency key and hard boundaries"
            )
        present = set(self.present_character_ids)
        if self.protected_user_id not in present:
            raise ContractValidationError("protected user must be in raw-turn cast")
        if not set(self.eligible_responder_ids).issubset(present):
            raise ContractValidationError(
                "raw-turn eligible responders must be present"
            )
        if self.protected_user_id in self.eligible_responder_ids:
            raise ContractValidationError(
                "protected user cannot be an eligible NPC responder"
            )


@dataclass(frozen=True, slots=True)
class InterpretedSourceSpan:
    start: int
    end: int
    classification: SourceUnitClassification
    reasoner_safe_text: str

    def __post_init__(self) -> None:
        if self.start < 0 or self.end <= self.start:
            raise ContractValidationError("interpreted source span is invalid")
        if not self.reasoner_safe_text.strip():
            raise ContractValidationError("reasoner-safe source text is required")


@dataclass(frozen=True, slots=True)
class IntentInterpretationDraft:
    SCHEMA_VERSION: ClassVar[str] = "cera.intent_interpretation_draft.v1"

    schema_version: str
    source_spans: tuple[InterpretedSourceSpan, ...]
    requested_content_class: str
    requested_responder_ids: tuple[TypedId, ...]
    requested_route_hints: tuple[str, ...]
    evidence_obligations: tuple[EvidenceObligation, ...]
    scene_anchors: tuple[str, ...]
    explicit_unknowns: tuple[str, ...]
    prohibited_inferences: tuple[str, ...]

    def __post_init__(self) -> None:
        require_schema(self.schema_version, self.SCHEMA_VERSION, type(self).__name__)
        if not self.source_spans:
            raise ContractValidationError("intent interpretation requires source spans")
        if self.requested_content_class not in {"ordinary", "adult"}:
            raise ContractValidationError(
                "intent interpretation content class is invalid"
            )
        for character_id in self.requested_responder_ids:
            require_kind(character_id, IdKind.CHARACTER, "requested_responder_ids")
        _unique(self.requested_responder_ids, "interpreted responders")
        _unique(
            (value.obligation_id for value in self.evidence_obligations),
            "interpreted obligations",
        )

    @property
    def draft_sha256(self) -> str:
        return domain_sha256("cera.intent_interpretation_draft.v1", self)


class IntentInterpreterPort(Protocol):
    adapter_version: str
    external_provider_calls: int

    def interpret(self, envelope: RawTurnEnvelope) -> IntentInterpretationDraft: ...


@dataclass(frozen=True, slots=True)
class IntentInterpretationReceipt:
    SCHEMA_VERSION: ClassVar[str] = "cera.intent_interpretation_receipt.v1"

    schema_version: str
    receipt_id: TypedId
    envelope_sha256: str
    interpretation_sha256: str
    adapter_version: str
    external_provider_calls: int
    authoritative_store_writes: int

    def __post_init__(self) -> None:
        require_schema(self.schema_version, self.SCHEMA_VERSION, type(self).__name__)
        require_kind(self.receipt_id, IdKind.VALIDATION, "receipt_id")
        if not re_is_sha256(self.envelope_sha256) or not re_is_sha256(
            self.interpretation_sha256
        ):
            raise ContractValidationError(
                "intent interpretation receipt hashes are invalid"
            )
        if not self.adapter_version.strip():
            raise ContractValidationError("intent adapter version is required")
        if self.external_provider_calls != 0 or self.authoritative_store_writes != 0:
            raise ContractValidationError(
                "provider-free intent interpretation cannot call or write"
            )


def _unique(values, field_name: str) -> None:
    items = list(values)
    if len(items) != len(set(items)):
        raise ContractValidationError(f"{field_name} must not contain duplicates")
