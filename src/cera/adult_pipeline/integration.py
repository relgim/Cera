"""Thin construction seam for the protected adult runtime."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from cera.errors import ContractValidationError
from cera.pi_scene.pi_adapter import PiSceneAdapter
from cera.pi_scene.store import AcceptedPiSessionV1

from .acceptance import AdultIntegratedExecutionV1
from .contracts import (
    AdultContextFactV1,
    AdultCraftMode,
    AdultCraftQueryV1,
    AdultEntryReason,
    AdultSceneRequestV1,
)
from .craft_catalog import CatalogAdultCraftRetrieval
from .pi_roles import (
    AdultRoleViewContextV1,
    LazyProtectedWriterViewMaterializer,
    PiDeepSeekAdultFilterPort,
    PiDeepSeekAdultScenePort,
    PiStructuredAdultRoleTransport,
)
from .pipeline import AdultPipeline, AdultPipelineInputV1
from .ports import retrieve_bounded_adult_craft


@dataclass(frozen=True, slots=True)
class AdultScenePreparationV1:
    entry_reason: AdultEntryReason
    adult_handoff: str | None
    exact_current_source: str
    accepted_safe_continuity: str
    accepted_protected_continuity: str | None
    autonomy_mode: str
    depth_mode: str
    current_context: tuple[AdultContextFactV1, ...]
    craft_mode: AdultCraftMode
    craft_concept_keys: tuple[str, ...]
    craft_keyword_keys: tuple[str, ...]
    hard_boundaries: tuple[str, ...]


@dataclass(slots=True)
class AdultPipelineIntegrationV1:
    """Runtime-facing service; no candidate registry or hidden route state."""

    pipeline: AdultPipeline
    scene_port: PiDeepSeekAdultScenePort
    filter_port: PiDeepSeekAdultFilterPort
    craft_retrieval: CatalogAdultCraftRetrieval

    def prepare_scene_request(self, source: AdultScenePreparationV1) -> AdultSceneRequestV1:
        query = AdultCraftQueryV1(
            schema_version=AdultCraftQueryV1.SCHEMA_VERSION,
            mode=source.craft_mode,
            concept_keys=source.craft_concept_keys,
            keyword_keys=source.craft_keyword_keys,
        )
        craft = retrieve_bounded_adult_craft(query, self.craft_retrieval)
        return AdultSceneRequestV1(
            schema_version=AdultSceneRequestV1.SCHEMA_VERSION,
            entry_reason=source.entry_reason,
            adult_handoff=source.adult_handoff,
            exact_current_source=source.exact_current_source,
            accepted_safe_continuity=source.accepted_safe_continuity,
            accepted_protected_continuity=source.accepted_protected_continuity,
            autonomy_mode=source.autonomy_mode,
            depth_mode=source.depth_mode,
            current_context=source.current_context,
            retrieved_craft=craft,
            hard_boundaries=source.hard_boundaries,
        )

    def execute(
        self,
        *,
        request_id: str,
        candidate_id: str,
        world_id: str,
        branch_id: str,
        accepted_head_sha256: str | None,
        scene_request: AdultSceneRequestV1,
    ) -> AdultIntegratedExecutionV1:
        result = self.pipeline.run(
            AdultPipelineInputV1(
                request_id=request_id,
                candidate_id=candidate_id,
                world_id=world_id,
                branch_id=branch_id,
                accepted_head_sha256=accepted_head_sha256,
                scene_request=scene_request,
            )
        )
        return AdultIntegratedExecutionV1(
            result=result,
            scene_session=self.scene_port.scene_session_binding(),
            filter_execution=self.filter_port.filter_execution_binding(),
        )


def build_pi_adult_pipeline_integration(
    *,
    pi_adapter: PiSceneAdapter,
    catalog_root: Path,
    protected_runtime_root: Path,
    context: AdultRoleViewContextV1,
    accepted_parent_session: AcceptedPiSessionV1 | None = None,
) -> AdultPipelineIntegrationV1:
    """Build one branch/candidate-scoped adult route over protected paths."""

    root = protected_runtime_root.resolve()
    if not root.is_absolute():
        raise ContractValidationError("adult protected runtime root must be absolute")
    view_root = root / "WRITER_VIEWS"
    session_root = root / "SESSIONS"
    materializer = LazyProtectedWriterViewMaterializer(view_root)
    transport = PiStructuredAdultRoleTransport(pi_adapter)
    scene = PiDeepSeekAdultScenePort(
        transport=transport,
        materializer=materializer,
        context=context,
        session_root=session_root,
        accepted_parent_session=accepted_parent_session,
    )
    filter_port = PiDeepSeekAdultFilterPort(
        transport=transport,
        materializer=materializer,
        context=context,
        session_root=session_root,
    )
    return AdultPipelineIntegrationV1(
        pipeline=AdultPipeline(scene=scene, filter_port=filter_port),
        scene_port=scene,
        filter_port=filter_port,
        craft_retrieval=CatalogAdultCraftRetrieval(catalog_root),
    )
