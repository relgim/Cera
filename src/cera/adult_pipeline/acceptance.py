"""Restart-safe Python custody for atomic adult acceptance.

The protected pipeline bundle intentionally contains story artifacts rather
than branch-turn bookkeeping.  This module supplies the missing Python-owned
accepted-turn envelope without asking either model to author identity, hashes,
generation, parentage, session lineage, or transaction fields.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

from cera.errors import ContractValidationError, StateConflictError
from cera.schema import from_mapping
from cera.serialization import canonical_json, canonical_sha256, re_is_sha256, text_sha256

from .contracts import (
    AdultFilterCustodyV1,
    AdultFilterInvocationV1,
    AdultFilterRequestV1,
    AdultPipelineResultV1,
    AdultPromotionBundleV1,
    AdultProviderReceiptV2,
    AdultSceneCustodyV1,
    AdultSceneInvocationV1,
    AdultSceneRequestV1,
    BoundAdultFilterResultV1,
    BoundAdultSceneCandidateV1,
)


def _required(value: str, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ContractValidationError(f"{field} is required")


def _sha(value: str, field: str) -> None:
    if not isinstance(value, str) or not re_is_sha256(value):
        raise ContractValidationError(f"{field} must be SHA-256")


@dataclass(frozen=True, slots=True)
class AdultSceneSessionBindingV1:
    """Durable soft-session evidence; accepted state remains authoritative."""

    SCHEMA_VERSION: ClassVar[str] = "cera.adult_pipeline.scene_session_binding.v1"

    schema_version: str
    scene_request_sha256: str
    writer_view_manifest_sha256: str
    provider_receipt_sha256: str
    transport_request_binding_sha256: str
    session_id: str
    session_id_sha256: str
    session_path: str

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("adult Scene session binding schema changed")
        for field in (
            "scene_request_sha256",
            "writer_view_manifest_sha256",
            "provider_receipt_sha256",
            "transport_request_binding_sha256",
            "session_id_sha256",
        ):
            _sha(getattr(self, field), f"adult_scene_session.{field}")
        _required(self.session_id, "adult_scene_session.session_id")
        _required(self.session_path, "adult_scene_session.session_path")
        if text_sha256(self.session_id) != self.session_id_sha256:
            raise ContractValidationError("adult Scene session identity changed")
        if not Path(self.session_path).is_absolute():
            raise ContractValidationError("adult Scene session path must be absolute")


@dataclass(frozen=True, slots=True)
class AdultSceneSessionBindingV2(AdultSceneSessionBindingV1):
    """Fresh Scene-session custody with exact accepted-parent provenance."""

    SCHEMA_VERSION: ClassVar[str] = "cera.adult_pipeline.scene_session_binding.v2"

    parent_session_id_sha256: str | None
    rehydrated: bool

    def __post_init__(self) -> None:
        AdultSceneSessionBindingV1.__post_init__(self)
        if self.parent_session_id_sha256 is not None:
            _sha(
                self.parent_session_id_sha256,
                "adult_scene_session.parent_session_id_sha256",
            )
        if self.rehydrated is not True:
            raise ContractValidationError("adult Scene session must prove rehydration")


@dataclass(frozen=True, slots=True)
class AdultFilterExecutionBindingV1:
    """Durable proof that the isolated Filter used its exact protected view."""

    SCHEMA_VERSION: ClassVar[str] = "cera.adult_pipeline.filter_execution_binding.v1"

    schema_version: str
    filter_request_sha256: str
    writer_view_manifest_sha256: str
    provider_receipt_sha256: str
    transport_request_binding_sha256: str
    session_id_sha256: str
    session_path: str
    session_terminalized: bool

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("adult Filter execution binding schema changed")
        for field in (
            "filter_request_sha256",
            "writer_view_manifest_sha256",
            "provider_receipt_sha256",
            "transport_request_binding_sha256",
            "session_id_sha256",
        ):
            _sha(getattr(self, field), f"adult_filter_execution.{field}")
        _required(self.session_path, "adult_filter_execution.session_path")
        if not Path(self.session_path).is_absolute():
            raise ContractValidationError("adult Filter session path must be absolute")
        if self.session_terminalized is not True:
            raise ContractValidationError("adult Filter session must be terminalized")


@dataclass(frozen=True, slots=True)
class AdultIntegratedExecutionV1:
    """Complete candidate result plus its durable protected transport custody."""

    result: AdultPipelineResultV1
    scene_session: AdultSceneSessionBindingV1 | AdultSceneSessionBindingV2
    filter_execution: AdultFilterExecutionBindingV1

    def __post_init__(self) -> None:
        if self.scene_session.scene_request_sha256 != canonical_sha256(self.result.scene.request):
            raise ContractValidationError("adult Scene session cites another request")
        if self.scene_session.provider_receipt_sha256 != canonical_sha256(
            self.result.scene.invocation.receipt
        ):
            raise ContractValidationError("adult Scene session cites another receipt")
        if self.filter_execution.filter_request_sha256 != canonical_sha256(
            self.result.filtered.request
        ):
            raise ContractValidationError("adult Filter execution cites another request")
        if self.filter_execution.provider_receipt_sha256 != canonical_sha256(
            self.result.filtered.invocation.receipt
        ):
            raise ContractValidationError("adult Filter execution cites another receipt")
        if self.scene_session.session_id_sha256 != (
            self.result.scene.invocation.receipt.session_id_sha256
        ):
            raise ContractValidationError("adult Scene session hash changed")
        scene_receipt = self.result.scene.invocation.receipt
        if isinstance(scene_receipt, AdultProviderReceiptV2):
            if not isinstance(self.scene_session, AdultSceneSessionBindingV2):
                raise ContractValidationError("adult Scene receipt lost v2 session custody")
            if (
                self.scene_session.parent_session_id_sha256
                != scene_receipt.parent_session_id_sha256
                or self.scene_session.rehydrated != scene_receipt.rehydrated
            ):
                raise ContractValidationError("adult Scene rehydration custody changed")
        elif isinstance(self.scene_session, AdultSceneSessionBindingV2):
            raise ContractValidationError("adult Scene session lost its v2 receipt")
        if self.filter_execution.session_id_sha256 != (
            self.result.filtered.invocation.receipt.session_id_sha256
        ):
            raise ContractValidationError("adult Filter session hash changed")

    @property
    def execution_sha256(self) -> str:
        return canonical_sha256(self)

    def acceptance_envelope(
        self,
        *,
        accepted_turn_id: str,
        parent_accepted_turn_id: str | None,
        scene_id: str,
        generation: int,
        creator_action: str = "automatic_accept",
    ) -> AdultAcceptedTurnEnvelopeV1:
        if not self.result.eligible_for_atomic_acceptance:
            raise StateConflictError("rejected adult execution cannot be accepted")
        promotion = self.result.promotion_bundle()
        return AdultAcceptedTurnEnvelopeV1(
            schema_version=AdultAcceptedTurnEnvelopeV1.SCHEMA_VERSION,
            accepted_turn_id=accepted_turn_id,
            turn_id=accepted_turn_id,
            parent_accepted_turn_id=parent_accepted_turn_id,
            parent_accepted_head_sha256=promotion.accepted_head_before_sha256,
            world_id=promotion.world_id,
            branch_id=promotion.branch_id,
            scene_id=scene_id,
            generation=generation,
            request_id=promotion.request_id,
            candidate_id=promotion.candidate_id,
            exact_current_source=self.result.scene.request.exact_current_source,
            exact_current_source_sha256=text_sha256(
                self.result.scene.request.exact_current_source
            ),
            primary_handoff_kind="deepseek_adult_scene_request",
            primary_handoff_json=canonical_json(self.result.scene.request),
            primary_handoff_sha256=text_sha256(canonical_json(self.result.scene.request)),
            scene_writer_view_manifest_sha256=self.scene_session.writer_view_manifest_sha256,
            filter_writer_view_manifest_sha256=(
                self.filter_execution.writer_view_manifest_sha256
            ),
            scene_invocation=self.result.scene.invocation,
            filter_invocation=self.result.filtered.invocation,
            scene_session=self.scene_session,
            filter_execution=self.filter_execution,
            promotion_bundle=promotion,
            pipeline_execution_sha256=self.execution_sha256,
            creator_action=creator_action,
            recording_status="complete_preaccept_filter",
        )


@dataclass(frozen=True, slots=True)
class AdultAcceptedTurnEnvelopeV1:
    """All immutable fields required for one atomic accepted adult turn."""

    SCHEMA_VERSION: ClassVar[str] = "cera.adult_pipeline.accepted_turn_envelope.v1"

    schema_version: str
    accepted_turn_id: str
    turn_id: str
    parent_accepted_turn_id: str | None
    parent_accepted_head_sha256: str | None
    world_id: str
    branch_id: str
    scene_id: str
    generation: int
    request_id: str
    candidate_id: str
    exact_current_source: str
    exact_current_source_sha256: str
    primary_handoff_kind: str
    primary_handoff_json: str
    primary_handoff_sha256: str
    scene_writer_view_manifest_sha256: str
    filter_writer_view_manifest_sha256: str
    scene_invocation: AdultSceneInvocationV1
    filter_invocation: AdultFilterInvocationV1
    scene_session: AdultSceneSessionBindingV1 | AdultSceneSessionBindingV2
    filter_execution: AdultFilterExecutionBindingV1
    promotion_bundle: AdultPromotionBundleV1
    pipeline_execution_sha256: str
    creator_action: str
    recording_status: str

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("adult accepted-turn envelope schema changed")
        for field in (
            "accepted_turn_id",
            "turn_id",
            "world_id",
            "branch_id",
            "scene_id",
            "request_id",
            "candidate_id",
            "exact_current_source",
            "primary_handoff_kind",
            "primary_handoff_json",
        ):
            _required(getattr(self, field), f"adult_acceptance.{field}")
        if type(self.generation) is not int or self.generation < 1:
            raise ContractValidationError("adult accepted generation must be positive")
        if (self.parent_accepted_turn_id is None) != (
            self.parent_accepted_head_sha256 is None
        ):
            raise ContractValidationError("adult accepted parent identity and head disagree")
        if self.accepted_turn_id != self.turn_id:
            raise ContractValidationError("adult accepted turn identity changed")
        if (self.parent_accepted_turn_id is None) != (self.generation == 1):
            raise ContractValidationError("adult accepted generation and parent disagree")
        for field in (
            "exact_current_source_sha256",
            "primary_handoff_sha256",
            "scene_writer_view_manifest_sha256",
            "filter_writer_view_manifest_sha256",
            "pipeline_execution_sha256",
        ):
            _sha(getattr(self, field), f"adult_acceptance.{field}")
        if self.parent_accepted_head_sha256 is not None:
            _sha(self.parent_accepted_head_sha256, "adult_acceptance.parent_head")
        if text_sha256(self.exact_current_source) != self.exact_current_source_sha256:
            raise ContractValidationError("adult accepted source binding changed")
        if text_sha256(self.primary_handoff_json) != self.primary_handoff_sha256:
            raise ContractValidationError("adult accepted handoff binding changed")
        try:
            handoff = json.loads(self.primary_handoff_json)
        except json.JSONDecodeError as exc:
            raise ContractValidationError("adult accepted handoff is not JSON") from exc
        if canonical_json(handoff) != self.primary_handoff_json:
            raise ContractValidationError("adult accepted handoff is not canonical")
        bundle = self.promotion_bundle
        for field in ("world_id", "branch_id", "request_id", "candidate_id"):
            if getattr(self, field) != getattr(bundle, field):
                raise ContractValidationError(f"adult accepted {field} changed")
        if self.parent_accepted_head_sha256 != bundle.accepted_head_before_sha256:
            raise ContractValidationError("adult accepted parent head changed")
        request = from_mapping(AdultSceneRequestV1, handoff)
        if canonical_sha256(request) != self.scene_invocation.receipt.request_sha256:
            raise ContractValidationError("adult accepted handoff differs from Scene request")
        if request.exact_current_source != self.exact_current_source:
            raise ContractValidationError("adult accepted source differs from Scene request")
        scene_custody = AdultSceneCustodyV1(
            schema_version=AdultSceneCustodyV1.SCHEMA_VERSION,
            request_id=self.request_id,
            candidate_id=self.candidate_id,
            world_id=self.world_id,
            branch_id=self.branch_id,
            accepted_head_sha256=self.parent_accepted_head_sha256,
            exact_source_sha256=self.exact_current_source_sha256,
            scene_request_sha256=canonical_sha256(request),
        )
        scene = BoundAdultSceneCandidateV1(
            request=request,
            custody=scene_custody,
            invocation=self.scene_invocation,
        )
        filter_request = AdultFilterRequestV1(
            schema_version=AdultFilterRequestV1.SCHEMA_VERSION,
            scene_request=request,
            scene_output=self.scene_invocation.output,
        )
        filter_custody = AdultFilterCustodyV1(
            schema_version=AdultFilterCustodyV1.SCHEMA_VERSION,
            request_id=self.request_id,
            candidate_id=self.candidate_id,
            world_id=self.world_id,
            branch_id=self.branch_id,
            accepted_head_sha256=self.parent_accepted_head_sha256,
            scene_candidate_sha256=scene.candidate_sha256,
            filter_request_sha256=canonical_sha256(filter_request),
        )
        filtered = BoundAdultFilterResultV1(
            request=filter_request,
            custody=filter_custody,
            invocation=self.filter_invocation,
        )
        result = AdultPipelineResultV1(scene=scene, filtered=filtered)
        integrated = AdultIntegratedExecutionV1(
            result=result,
            scene_session=self.scene_session,
            filter_execution=self.filter_execution,
        )
        if integrated.execution_sha256 != self.pipeline_execution_sha256:
            raise ContractValidationError("adult accepted execution binding changed")
        if result.promotion_bundle() != self.promotion_bundle:
            raise ContractValidationError("adult accepted promotion bundle changed")
        if self.scene_invocation.output.exact_story_prose != bundle.exact_story_prose:
            raise ContractValidationError("adult accepted prose differs from Scene output")
        if canonical_sha256(self.scene_invocation.receipt) != (
            self.scene_session.provider_receipt_sha256
        ):
            raise ContractValidationError("adult accepted Scene receipt binding changed")
        if self.scene_writer_view_manifest_sha256 != (
            self.scene_session.writer_view_manifest_sha256
        ):
            raise ContractValidationError("adult accepted Scene view binding changed")
        if canonical_sha256(self.filter_invocation.receipt) != (
            self.filter_execution.provider_receipt_sha256
        ):
            raise ContractValidationError("adult accepted Filter receipt binding changed")
        if self.filter_writer_view_manifest_sha256 != (
            self.filter_execution.writer_view_manifest_sha256
        ):
            raise ContractValidationError("adult accepted Filter view binding changed")
        if self.creator_action not in {"accept", "automatic_accept", "provisional_accept"}:
            raise ContractValidationError("adult accepted creator action is invalid")
        if self.recording_status != "complete_preaccept_filter":
            raise ContractValidationError("adult accepted record status is invalid")

    @property
    def envelope_sha256(self) -> str:
        return canonical_sha256(self)
