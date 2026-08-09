"""Provider-free orchestration around the Adult Scene and Filter ports."""

from __future__ import annotations

from dataclasses import dataclass

from cera.provider_dispatch_guard import (
    assert_provider_dispatch_allowed,
    is_external_provider_boundary,
)
from cera.serialization import canonical_sha256, text_sha256

from .contracts import (
    AdultFilterCustodyV1,
    AdultFilterRequestV1,
    AdultPipelineResultV1,
    AdultSceneCustodyV1,
    AdultSceneRequestV1,
    BoundAdultFilterResultV1,
    BoundAdultSceneCandidateV1,
)
from .ports import AdultFilterPort, AdultScenePort


@dataclass(frozen=True, slots=True)
class AdultPipelineInputV1:
    """Python custody plus the model-visible Scene request."""

    request_id: str
    candidate_id: str
    world_id: str
    branch_id: str
    accepted_head_sha256: str | None
    scene_request: AdultSceneRequestV1


class AdultPipeline:
    """Run exactly one Adult Scene owner and one isolated Filter operation."""

    def __init__(self, *, scene: AdultScenePort, filter_port: AdultFilterPort) -> None:
        self._scene = scene
        self._filter = filter_port

    def run(self, prepared: AdultPipelineInputV1) -> AdultPipelineResultV1:
        scene_request_sha256 = canonical_sha256(prepared.scene_request)
        scene_custody = AdultSceneCustodyV1(
            schema_version=AdultSceneCustodyV1.SCHEMA_VERSION,
            request_id=prepared.request_id,
            candidate_id=prepared.candidate_id,
            world_id=prepared.world_id,
            branch_id=prepared.branch_id,
            accepted_head_sha256=prepared.accepted_head_sha256,
            exact_source_sha256=text_sha256(prepared.scene_request.exact_current_source),
            scene_request_sha256=scene_request_sha256,
        )
        assert_provider_dispatch_allowed(
            "adult_pipeline.scene.generate",
            external_provider_boundary=is_external_provider_boundary(self._scene),
        )
        scene = BoundAdultSceneCandidateV1(
            request=prepared.scene_request,
            custody=scene_custody,
            invocation=self._scene.generate_adult_scene(prepared.scene_request),
        )
        filter_request = AdultFilterRequestV1(
            schema_version=AdultFilterRequestV1.SCHEMA_VERSION,
            scene_request=prepared.scene_request,
            scene_output=scene.invocation.output,
        )
        filter_request_sha256 = canonical_sha256(filter_request)
        filter_custody = AdultFilterCustodyV1(
            schema_version=AdultFilterCustodyV1.SCHEMA_VERSION,
            request_id=prepared.request_id,
            candidate_id=prepared.candidate_id,
            world_id=prepared.world_id,
            branch_id=prepared.branch_id,
            accepted_head_sha256=prepared.accepted_head_sha256,
            scene_candidate_sha256=scene.candidate_sha256,
            filter_request_sha256=filter_request_sha256,
        )
        assert_provider_dispatch_allowed(
            "adult_pipeline.filter.validate_and_stage",
            external_provider_boundary=is_external_provider_boundary(self._filter),
        )
        filtered = BoundAdultFilterResultV1(
            request=filter_request,
            custody=filter_custody,
            invocation=self._filter.validate_and_stage(filter_request),
        )
        return AdultPipelineResultV1(scene=scene, filtered=filtered)


AdultSceneFilterPipeline = AdultPipeline
