"""Protected exact-input packet builders for all Retry-governed stages."""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from typing import Any, ClassVar

from cera.errors import ContractValidationError
from cera.serialization import (
    bytes_sha256,
    canonical_bytes,
    re_is_sha256,
    to_primitive,
)

from .provider_stage_retry import ProviderFamily, ProviderModelFamily, ProviderStage
from .provider_stage_retry_scope import provider_stage_owner


def _require_text(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise ContractValidationError(f"provider-stage packet {field_name} is invalid")


def _decode_canonical_object(exact_bytes: bytes, field_name: str) -> dict[str, Any]:
    try:
        decoded: object = json.loads(exact_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractValidationError(
            f"provider-stage {field_name} is not canonical UTF-8 JSON"
        ) from exc
    if not isinstance(decoded, dict) or canonical_bytes(decoded) != exact_bytes:
        raise ContractValidationError(
            f"provider-stage {field_name} is not one canonical JSON object"
        )
    return decoded


@dataclass(frozen=True, slots=True)
class ImmutableRetrievalSnapshotIdentityV1:
    """Identity whose storage contract guarantees equivalent Planner retrieval."""

    SCHEMA_VERSION: ClassVar[str] = "cera.immutable_retrieval_snapshot_identity.v1"

    schema_version: str
    snapshot_id: str
    snapshot_sha256: str
    immutability_evidence_sha256: str

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("retrieval snapshot identity schema changed")
        _require_text(self.snapshot_id, "retrieval snapshot ID")
        for value, field_name in (
            (self.snapshot_sha256, "retrieval snapshot"),
            (self.immutability_evidence_sha256, "retrieval immutability evidence"),
        ):
            if not re_is_sha256(value):
                raise ContractValidationError(f"provider-stage {field_name} must be SHA-256")

    def to_payload(self) -> dict[str, Any]:
        payload = to_primitive(self)
        assert isinstance(payload, dict)
        return payload


@dataclass(frozen=True, slots=True)
class ProviderStageConfigurationV1:
    """Frozen provider/model/routing/configuration portion of semantic input."""

    SCHEMA_VERSION: ClassVar[str] = "cera.provider_stage_configuration.v1"

    schema_version: str
    stage: ProviderStage
    provider: ProviderFamily
    model_family: ProviderModelFamily
    exact_bytes: bytes = field(repr=False)
    configuration_sha256: str

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("provider-stage configuration schema changed")
        if (
            type(self.stage) is not ProviderStage
            or type(self.provider) is not ProviderFamily
            or type(self.model_family) is not ProviderModelFamily
        ):
            raise ContractValidationError("provider-stage configuration owner is not closed")
        if (self.provider, self.model_family) != provider_stage_owner(self.stage):
            raise ContractValidationError("provider-stage configuration owner changed")
        if not isinstance(self.exact_bytes, bytes) or not self.exact_bytes:
            raise ContractValidationError("provider-stage configuration bytes are empty")
        if self.configuration_sha256 != bytes_sha256(self.exact_bytes):
            raise ContractValidationError("provider-stage configuration hash changed")
        payload = _decode_canonical_object(self.exact_bytes, "configuration")
        if (
            payload.get("schema_version") != self.SCHEMA_VERSION
            or payload.get("stage") != self.stage.value
            or payload.get("provider") != self.provider.value
            or payload.get("model_family") != self.model_family.value
        ):
            raise ContractValidationError("provider-stage configuration envelope changed")

    @classmethod
    def create(
        cls,
        *,
        stage: ProviderStage,
        model_id: str,
        reasoning_mode: str,
        routing: object,
        content_policy_route: str,
        stage_configuration: object,
    ) -> ProviderStageConfigurationV1:
        provider, model_family = provider_stage_owner(stage)
        _require_text(model_id, "model ID")
        _require_text(reasoning_mode, "reasoning mode")
        _require_text(content_policy_route, "content-policy route")
        payload = {
            "schema_version": cls.SCHEMA_VERSION,
            "stage": stage.value,
            "provider": provider.value,
            "model_family": model_family.value,
            "model_id": model_id,
            "reasoning_mode": reasoning_mode,
            "routing": to_primitive(routing),
            "content_policy_route": content_policy_route,
            "stage_configuration": to_primitive(stage_configuration),
        }
        exact_bytes = canonical_bytes(payload)
        return cls(
            schema_version=cls.SCHEMA_VERSION,
            stage=stage,
            provider=provider,
            model_family=model_family,
            exact_bytes=exact_bytes,
            configuration_sha256=bytes_sha256(exact_bytes),
        )

    def to_payload(self) -> dict[str, Any]:
        return _decode_canonical_object(self.exact_bytes, "configuration")


@dataclass(frozen=True, slots=True)
class FrozenProviderStageResultV1:
    """Exact protected bytes frozen from an upstream provider stage."""

    SCHEMA_VERSION: ClassVar[str] = "cera.frozen_provider_stage_result.v1"

    schema_version: str
    source_stage: ProviderStage
    exact_result: bytes = field(repr=False)
    result_sha256: str

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("frozen provider-stage result schema changed")
        if type(self.source_stage) is not ProviderStage:
            raise ContractValidationError("frozen provider-stage result owner is not closed")
        if not isinstance(self.exact_result, bytes) or not self.exact_result:
            raise ContractValidationError("frozen provider-stage result must be non-empty bytes")
        if self.result_sha256 != bytes_sha256(self.exact_result):
            raise ContractValidationError("frozen provider-stage result hash changed")

    @classmethod
    def create(
        cls,
        *,
        source_stage: ProviderStage,
        exact_result: bytes,
    ) -> FrozenProviderStageResultV1:
        return cls(
            schema_version=cls.SCHEMA_VERSION,
            source_stage=source_stage,
            exact_result=exact_result,
            result_sha256=bytes_sha256(exact_result),
        )

    def to_packet_binding(self) -> dict[str, object]:
        """Return a lossless protected JSON binding for a downstream packet."""

        return {
            "schema_version": self.SCHEMA_VERSION,
            "source_stage": self.source_stage.value,
            "result_sha256": self.result_sha256,
            "exact_result_base64": base64.b64encode(self.exact_result).decode("ascii"),
        }


@dataclass(frozen=True, slots=True)
class ProviderStageFrozenPacketV1:
    """Canonical protected bytes replayed unchanged on every stage attempt."""

    SCHEMA_VERSION: ClassVar[str] = "cera.provider_stage_frozen_packet.v1"

    schema_version: str
    stage: ProviderStage
    packet_kind: str
    exact_bytes: bytes = field(repr=False)
    stage_input_sha256: str

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("provider-stage frozen packet schema changed")
        if type(self.stage) is not ProviderStage:
            raise ContractValidationError("provider-stage frozen packet stage is not closed")
        _require_text(self.packet_kind, "packet kind")
        if not isinstance(self.exact_bytes, bytes) or not self.exact_bytes:
            raise ContractValidationError("provider-stage frozen packet bytes are empty")
        if self.stage_input_sha256 != bytes_sha256(self.exact_bytes):
            raise ContractValidationError("provider-stage frozen packet hash changed")
        payload = _decode_canonical_object(self.exact_bytes, "frozen packet")
        if (
            payload.get("schema_version") != self.SCHEMA_VERSION
            or payload.get("stage") != self.stage.value
            or payload.get("packet_kind") != self.packet_kind
        ):
            raise ContractValidationError("provider-stage frozen packet envelope changed")

    def to_payload(self) -> dict[str, Any]:
        """Decode protected content; callers must retain protected custody."""

        return _decode_canonical_object(self.exact_bytes, "frozen packet")


def _freeze_packet(
    *,
    stage: ProviderStage,
    packet_kind: str,
    semantic_input: object,
    configuration: ProviderStageConfigurationV1,
) -> ProviderStageFrozenPacketV1:
    if type(configuration) is not ProviderStageConfigurationV1:
        raise ContractValidationError("provider-stage configuration contract changed")
    if configuration.stage is not stage:
        raise ContractValidationError("provider-stage packet configuration changed stages")
    payload = {
        "schema_version": ProviderStageFrozenPacketV1.SCHEMA_VERSION,
        "stage": stage.value,
        "packet_kind": packet_kind,
        "provider_configuration": configuration.to_payload(),
        "semantic_input": to_primitive(semantic_input),
    }
    exact_bytes = canonical_bytes(payload)
    return ProviderStageFrozenPacketV1(
        schema_version=ProviderStageFrozenPacketV1.SCHEMA_VERSION,
        stage=stage,
        packet_kind=packet_kind,
        exact_bytes=exact_bytes,
        stage_input_sha256=bytes_sha256(exact_bytes),
    )


def freeze_planner_stage_packet(
    *,
    normalized_messages: object,
    accepted_state: object,
    retrieval_snapshot: ImmutableRetrievalSnapshotIdentityV1,
    tool_result_bundle: object,
    configuration: ProviderStageConfigurationV1,
) -> ProviderStageFrozenPacketV1:
    """Freeze Planner input with immutable retrieval and exact tool results."""

    if type(retrieval_snapshot) is not ImmutableRetrievalSnapshotIdentityV1:
        raise ContractValidationError("Planner retrieval snapshot contract changed")
    return _freeze_packet(
        stage=ProviderStage.PLANNER,
        packet_kind="planner",
        semantic_input={
            "normalized_messages": normalized_messages,
            "accepted_state": accepted_state,
            "retrieval_snapshot": retrieval_snapshot.to_payload(),
            "tool_result_bundle": tool_result_bundle,
        },
        configuration=configuration,
    )


def freeze_writer_stage_packet(
    *,
    sequence_plan: object,
    realization_context: object,
    configuration: ProviderStageConfigurationV1,
) -> ProviderStageFrozenPacketV1:
    return _freeze_packet(
        stage=ProviderStage.WRITER,
        packet_kind="writer",
        semantic_input={
            "sequence_plan": sequence_plan,
            "realization_context": realization_context,
        },
        configuration=configuration,
    )


def freeze_semantic_validator_stage_packet(
    *,
    sequence_plan: object,
    candidate: object,
    validation_context: object,
    configuration: ProviderStageConfigurationV1,
) -> ProviderStageFrozenPacketV1:
    return _freeze_packet(
        stage=ProviderStage.SEMANTIC_VALIDATOR,
        packet_kind="semantic_validator",
        semantic_input={
            "sequence_plan": sequence_plan,
            "candidate": candidate,
            "validation_context": validation_context,
        },
        configuration=configuration,
    )


def freeze_reader_stage_packet(
    *,
    reader_request: object,
    reader_custody: object,
    configuration: ProviderStageConfigurationV1,
) -> ProviderStageFrozenPacketV1:
    """Freeze one Reader request without any Luna verdict dependency."""

    return _freeze_packet(
        stage=ProviderStage.READER,
        packet_kind="reader",
        semantic_input={
            "reader_request": reader_request,
            "reader_custody": reader_custody,
        },
        configuration=configuration,
    )


def freeze_recorder_stage_packet(
    *,
    accepted_story: object,
    accepted_story_receipt: object,
    recording_context: object,
    configuration: ProviderStageConfigurationV1,
) -> ProviderStageFrozenPacketV1:
    return _freeze_packet(
        stage=ProviderStage.RECORDER,
        packet_kind="recorder",
        semantic_input={
            "accepted_story": accepted_story,
            "accepted_story_receipt": accepted_story_receipt,
            "recording_context": recording_context,
        },
        configuration=configuration,
    )


def freeze_adult_scene_stage_packet(
    *,
    scene_request: object,
    evidence_bundle: object,
    configuration: ProviderStageConfigurationV1,
) -> ProviderStageFrozenPacketV1:
    return _freeze_packet(
        stage=ProviderStage.ADULT_SCENE,
        packet_kind="adult_scene",
        semantic_input={
            "scene_request": scene_request,
            "evidence_bundle": evidence_bundle,
        },
        configuration=configuration,
    )


def freeze_adult_filter_stage_packet(
    *,
    scene_result: FrozenProviderStageResultV1,
    filter_context: object,
    configuration: ProviderStageConfigurationV1,
) -> ProviderStageFrozenPacketV1:
    """Freeze Filter input around the already-frozen Adult Scene result."""

    if (
        type(scene_result) is not FrozenProviderStageResultV1
        or scene_result.source_stage is not ProviderStage.ADULT_SCENE
    ):
        raise ContractValidationError("Adult Filter requires a frozen Adult Scene result")
    return _freeze_packet(
        stage=ProviderStage.ADULT_FILTER,
        packet_kind="adult_filter",
        semantic_input={
            "frozen_scene_result": scene_result.to_packet_binding(),
            "filter_context": filter_context,
        },
        configuration=configuration,
    )


__all__ = [
    "FrozenProviderStageResultV1",
    "ImmutableRetrievalSnapshotIdentityV1",
    "ProviderStageConfigurationV1",
    "ProviderStageFrozenPacketV1",
    "freeze_adult_filter_stage_packet",
    "freeze_adult_scene_stage_packet",
    "freeze_planner_stage_packet",
    "freeze_reader_stage_packet",
    "freeze_recorder_stage_packet",
    "freeze_semantic_validator_stage_packet",
    "freeze_writer_stage_packet",
]
