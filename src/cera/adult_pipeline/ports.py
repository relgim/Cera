"""Provider-neutral Adult Scene and Adult Filter ports."""

from __future__ import annotations

from typing import Protocol

from cera.errors import ContractValidationError
from cera.serialization import canonical_sha256

from .contracts import (
    AdultCraftQueryV1,
    AdultCraftSelectionV1,
    AdultFilterInvocationV1,
    AdultFilterRequestV1,
    AdultPipelineResultV1,
    AdultPromotionBundleV1,
    AdultRouteStateSnapshotV1,
    AdultSceneInvocationV1,
    AdultSceneRequestV1,
    BoundAdultPromotionV1,
)


class AdultScenePort(Protocol):
    """Sole adult logic and full-prose owner for one visible candidate."""

    def generate_adult_scene(self, request: AdultSceneRequestV1) -> AdultSceneInvocationV1: ...


class AdultCraftRetrievalPort(Protocol):
    """Select a bounded branch-pinned craft result from exact query keys."""

    def retrieve_adult_craft(self, query: AdultCraftQueryV1) -> AdultCraftSelectionV1: ...


def retrieve_bounded_adult_craft(
    query: AdultCraftQueryV1,
    retrieval: AdultCraftRetrievalPort,
) -> AdultCraftSelectionV1:
    """Bind one deterministic retrieval result to its exact query and mode."""

    selection = retrieval.retrieve_adult_craft(query)
    if selection.query_sha256 != canonical_sha256(query):
        raise ContractValidationError("adult craft retrieval cites another query")
    if selection.mode is not query.mode:
        raise ContractValidationError("adult craft retrieval changed mode")
    return selection


class AdultFilterPort(Protocol):
    """Independent validator and pre-accept record/projector."""

    def validate_and_stage(
        self,
        request: AdultFilterRequestV1,
    ) -> AdultFilterInvocationV1: ...


class AdultRouteStatePort(Protocol):
    """Read accepted route state reconstructed from branch authority."""

    def current_logic_route(
        self,
        *,
        world_id: str,
        branch_id: str,
    ) -> AdultRouteStateSnapshotV1: ...


class AdultAtomicPromotionPort(AdultRouteStatePort, Protocol):
    """Atomically promote prose, both records, and the next logic route."""

    def promote_adult_acceptance(
        self,
        bundle: AdultPromotionBundleV1,
    ) -> BoundAdultPromotionV1: ...


def promote_passed_adult_candidate(
    result: AdultPipelineResultV1,
    store: AdultAtomicPromotionPort,
) -> BoundAdultPromotionV1:
    """Narrow integration seam: a rejection cannot reach the transaction port."""

    return store.promote_adult_acceptance(result.promotion_bundle())
