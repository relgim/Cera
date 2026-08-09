"""Candidate-scoped construction for the protected full-model adult route.

This module is deliberately a construction seam.  It does not choose the
route, dispatch a provider, or persist accepted story state.  It separates the
ordinary-safe accepted projection from protected adult continuity before it
constructs the existing Adult Scene + Filter pipeline.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, cast

from cera.adult_pipeline.contracts import (
    AdultContextFactV1,
    AdultNextRoute,
    AdultRouteStateSnapshotV1,
)
from cera.adult_pipeline.craft_catalog import CatalogAdultCraftRetrieval
from cera.adult_pipeline.integration import build_pi_adult_pipeline_integration
from cera.adult_pipeline.pi_roles import AdultRoleViewContextV1
from cera.adult_pipeline.preparation import build_adult_turn_preparation_builder
from cera.errors import ContractValidationError, StateConflictError
from cera.serialization import canonical_json, canonical_sha256, domain_sha256, text_sha256

from .adult_orchestration import AutomaticAdultRouteOrchestrator
from .full_model_controller import AdultExecutionContextV1
from .pi_adapter import PiSceneAdapter
from .review_store import LeanSceneTurnInputV1
from .store import AcceptedPiSessionV1

_IDENTITY = re.compile(r"[a-z][a-z0-9_.:-]{0,191}\Z")
_MAX_SAFE_PROJECTION_CHARS = 40_000
_MAX_PROTECTED_CONTINUITY_CHARS = 100_000
_MAX_FACT_CHARS = 4_000
_FACT_FRAGMENT_CHARS = 2_400
_STORY_STATE_FACT_KEYS = frozenset(
    {
        "accepted_present_character_ids",
        "adult_public_continuity",
        "durable_changes",
        "provisional_canon_lineage",
        "public_scene_state",
        "unresolved_threads",
    }
)
_PRIVATE_VISIBILITIES = frozenset(
    {
        "adult_role_private",
        "branch_internal_unspecified",
        "character_private",
        "creator_private",
        "creator-only",
    }
)


class FullModelAdultRuntimeStore(Protocol):
    """Read-only construction surface plus the route-state port."""

    def current_logic_route(
        self,
        *,
        world_id: str,
        branch_id: str,
    ) -> AdultRouteStateSnapshotV1: ...

    def recent_ordinary_context_payloads(
        self,
        *,
        world_id: str,
        branch_id: str,
        limit: int = 6,
    ) -> tuple[dict[str, Any], ...]: ...

    def recent_adult_context_payloads(
        self,
        *,
        world_id: str,
        branch_id: str,
        limit: int = 6,
    ) -> tuple[dict[str, Any], ...]: ...

    def load_accepted_pi_session(
        self,
        *,
        world_id: str,
        branch_id: str,
    ) -> AcceptedPiSessionV1 | None: ...


@dataclass(frozen=True, slots=True)
class AdultCandidateRuntimeScopeV1:
    """Deterministic protected path binding for one exact candidate request."""

    request_id: str
    candidate_id: str
    turn_sha256: str
    route_state_sha256: str
    scope_sha256: str
    protected_root: Path


class FullModelAdultRuntimeFactory:
    """Build protected candidate integrations without selecting the route."""

    def __init__(
        self,
        *,
        store: FullModelAdultRuntimeStore,
        pi_adapter: PiSceneAdapter,
        catalog_root: Path,
        protected_runtime_root: Path,
    ) -> None:
        self.store = store
        self.pi_adapter = pi_adapter
        self.catalog_root = catalog_root.resolve()
        self.protected_runtime_root = protected_runtime_root.resolve()
        if not self.protected_runtime_root.is_absolute():
            raise ContractValidationError("adult protected runtime root must be absolute")

        # Loading this adapter verifies the manifest and every fragment before
        # a controller can be exposed.  Candidate integrations verify the same
        # immutable catalog again and must retain this exact manifest identity.
        verified = CatalogAdultCraftRetrieval(self.catalog_root)
        self.catalog_manifest_sha256 = verified.catalog.manifest.manifest_sha256

    def execution_context(
        self,
        turn: LeanSceneTurnInputV1,
        route_state: AdultRouteStateSnapshotV1,
    ) -> AdultExecutionContextV1:
        """Separate ordinary-safe and protected accepted continuity exactly."""

        _validate_turn_route_identity(turn, route_state)
        safe_records = tuple(
            self.store.recent_ordinary_context_payloads(
                world_id=turn.world_id,
                branch_id=turn.branch_id,
                limit=6,
            )
        )
        protected_records = tuple(
            self.store.recent_adult_context_payloads(
                world_id=turn.world_id,
                branch_id=turn.branch_id,
                limit=6,
            )
        )
        protected_adult_records = tuple(
            value for value in protected_records if _accepted_route(value) == "adult"
        )

        safe_projection = canonical_json(
            {
                "schema_version": "cera.pi_scene.adult_safe_projection.v1",
                "world_id": turn.world_id,
                "branch_id": turn.branch_id,
                "scene_id": turn.scene_id,
                "accepted_head_sha256": route_state.accepted_head_sha256,
                "current_state": turn.current_state,
                "accepted_records": safe_records,
            }
        )
        if len(safe_projection) > _MAX_SAFE_PROJECTION_CHARS:
            raise ContractValidationError("adult safe projection exceeds its bounded input")

        protected_continuity = (
            None
            if not protected_adult_records
            else canonical_json(
                {
                    "schema_version": "cera.pi_scene.protected_adult_continuity.v1",
                    "world_id": turn.world_id,
                    "branch_id": turn.branch_id,
                    "accepted_head_sha256": route_state.accepted_head_sha256,
                    "accepted_adult_records": protected_adult_records,
                }
            )
        )
        if (
            protected_continuity is not None
            and len(protected_continuity) > _MAX_PROTECTED_CONTINUITY_CHARS
        ):
            raise ContractValidationError("protected adult continuity exceeds its bounded input")
        if (
            route_state.current_logic_route is AdultNextRoute.ADULT
            and protected_continuity is None
        ):
            raise StateConflictError("accepted adult route has no protected adult continuity")

        facts = _current_facts(turn)
        boundaries = _product_story_boundaries(turn.current_state)
        _assert_no_protected_prose_leak(
            protected_adult_records,
            safe_projection=safe_projection,
            facts=facts,
            boundaries=boundaries,
        )
        if self.store.current_logic_route(
            world_id=turn.world_id,
            branch_id=turn.branch_id,
        ) != route_state:
            raise StateConflictError("accepted adult context changed during construction")
        return AdultExecutionContextV1(
            accepted_safe_projection=safe_projection,
            protected_adult_continuity=protected_continuity,
            current_facts=facts,
            product_story_boundaries=boundaries,
        )

    def candidate_scope(
        self,
        turn: LeanSceneTurnInputV1,
        request_id: str,
        candidate_id: str,
    ) -> AdultCandidateRuntimeScopeV1:
        """Bind the protected path to exact request, turn, route, and candidate."""

        _stable_identity(request_id, "adult request_id")
        _stable_identity(candidate_id, "adult candidate_id")
        route_state = self.store.current_logic_route(
            world_id=turn.world_id,
            branch_id=turn.branch_id,
        )
        _validate_turn_route_identity(turn, route_state)
        turn_sha256 = canonical_sha256(turn)
        route_state_sha256 = canonical_sha256(route_state)
        scope_sha256 = domain_sha256(
            "cera.pi_scene.full_model_adult_runtime_scope.v1",
            {
                "request_id": request_id,
                "candidate_id": candidate_id,
                "world_id": turn.world_id,
                "branch_id": turn.branch_id,
                "scene_id": turn.scene_id,
                "exact_user_source_sha256": text_sha256(turn.exact_user_source),
                "turn_sha256": turn_sha256,
                "route_state_sha256": route_state_sha256,
            },
        )
        return AdultCandidateRuntimeScopeV1(
            request_id=request_id,
            candidate_id=candidate_id,
            turn_sha256=turn_sha256,
            route_state_sha256=route_state_sha256,
            scope_sha256=scope_sha256,
            protected_root=self.protected_runtime_root / "candidates" / scope_sha256,
        )

    def orchestrator(
        self,
        turn: LeanSceneTurnInputV1,
        request_id: str,
        candidate_id: str,
    ) -> AutomaticAdultRouteOrchestrator:
        """Construct one candidate-scoped Scene+Filter integration, without dispatch."""

        scope = self.candidate_scope(turn, request_id, candidate_id)
        protected_records = tuple(
            self.store.recent_adult_context_payloads(
                world_id=turn.world_id,
                branch_id=turn.branch_id,
                limit=6,
            )
        )
        current_route_state = self.store.current_logic_route(
            world_id=turn.world_id,
            branch_id=turn.branch_id,
        )
        if canonical_sha256(current_route_state) != scope.route_state_sha256:
            raise StateConflictError("accepted adult route changed during candidate construction")
        accepted_parent_session = self.store.load_accepted_pi_session(
            world_id=turn.world_id,
            branch_id=turn.branch_id,
        )
        if accepted_parent_session is not None and (
            accepted_parent_session.accepted_receipt_sha256
            != current_route_state.accepted_head_sha256
        ):
            raise StateConflictError("adult parent Scene session belongs to another head")
        if (
            current_route_state.current_logic_route is AdultNextRoute.ADULT
            and accepted_parent_session is None
        ):
            raise StateConflictError("accepted adult route lost its parent Scene session")
        role_context = AdultRoleViewContextV1(
            world_id=turn.world_id,
            branch_id=turn.branch_id,
            scene_id=turn.scene_id,
            turn_id=f"turn:adult:{scope.scope_sha256[:32]}",
            candidate_id=candidate_id,
            current_state=dict(turn.current_state),
            characters={key: dict(value) for key, value in turn.characters.items()},
            relationships={key: dict(value) for key, value in turn.relationships.items()},
            recent_prose=_accepted_prose(protected_records),
            relevant_memories={
                key: dict(value) for key, value in turn.relevant_memories.items()
            },
            voice_examples=dict(turn.voice_examples),
            accepted_records=protected_records,
        )
        integration = build_pi_adult_pipeline_integration(
            pi_adapter=self.pi_adapter,
            catalog_root=self.catalog_root,
            protected_runtime_root=scope.protected_root,
            context=role_context,
            accepted_parent_session=accepted_parent_session,
        )
        if (
            integration.craft_retrieval.catalog.manifest.manifest_sha256
            != self.catalog_manifest_sha256
        ):
            raise StateConflictError("adult candidate changed the verified craft catalog")
        return AutomaticAdultRouteOrchestrator(
            route_state=self.store,
            preparation_builder=build_adult_turn_preparation_builder(
                integration.craft_retrieval
            ),
            pipeline=integration,
        )


def _validate_turn_route_identity(
    turn: LeanSceneTurnInputV1,
    route_state: AdultRouteStateSnapshotV1,
) -> None:
    if (route_state.world_id, route_state.branch_id) != (turn.world_id, turn.branch_id):
        raise StateConflictError("adult runtime route state belongs to another branch")


def _accepted_route(value: Mapping[str, Any]) -> str:
    receipt = value.get("receipt")
    route = receipt.get("route") if isinstance(receipt, Mapping) else None
    if route not in {"ordinary", "adult"}:
        raise StateConflictError("accepted adult context contains an invalid route")
    return cast(str, route)


def _accepted_prose(
    values: Sequence[Mapping[str, Any]],
) -> tuple[Mapping[str, Any] | str, ...]:
    output: list[Mapping[str, Any] | str] = []
    for value in values:
        receipt = value.get("receipt")
        if not isinstance(receipt, Mapping):
            raise StateConflictError("accepted adult context omitted its receipt")
        prose = receipt.get("exact_accepted_prose")
        if not isinstance(prose, str) or not prose.strip():
            raise StateConflictError("protected accepted context omitted exact prose")
        output.append(prose)
    return tuple(output)


def _product_story_boundaries(current_state: Mapping[str, Any]) -> tuple[str, ...]:
    raw = current_state.get("hard_boundaries", ())
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise ContractValidationError("adult product story boundaries are invalid")
    boundaries = tuple(raw)
    if any(not isinstance(value, str) or not value.strip() for value in boundaries):
        raise ContractValidationError("adult product story boundary is invalid")
    return cast(tuple[str, ...], boundaries)


def _current_facts(turn: LeanSceneTurnInputV1) -> tuple[AdultContextFactV1, ...]:
    facts: list[AdultContextFactV1] = []
    selected_state = {
        key: value
        for key, value in turn.current_state.items()
        if key in _STORY_STATE_FACT_KEYS
    }
    facts.extend(
        _facts_for_record(
            "state",
            "current_scene",
            selected_state,
            default_visibility="public",
        )
    )
    for namespace, records in (
        ("character", turn.characters),
        ("relationship", turn.relationships),
        ("memory", turn.relevant_memories),
    ):
        for record_id in sorted(records):
            facts.extend(_facts_for_record(namespace, record_id, records[record_id]))
    if not facts:
        raise ContractValidationError("adult runtime has no authoritative current facts")
    return tuple(facts)


def _facts_for_record(
    namespace: str,
    record_id: str,
    value: Mapping[str, Any],
    *,
    default_visibility: str = "adult_role_private",
) -> tuple[AdultContextFactV1, ...]:
    subject_id, visibility = _fact_scope(
        namespace,
        record_id,
        value,
        inherited=(
            _default_subject_id(namespace, record_id),
            default_visibility,
        ),
    )
    fragments = tuple(_fact_fragments(value, path=()))
    if not fragments:
        fragments = (((), canonical_json({})),)
    output: list[AdultContextFactV1] = []
    for path, fragment in fragments:
        nested_subject, nested_visibility = _scope_at_path(
            namespace,
            record_id,
            value,
            path,
            initial=(subject_id, visibility),
        )
        evidence_sha256 = domain_sha256(
            "cera.pi_scene.adult_context_fact.v1",
            {
                "namespace": namespace,
                "record_id": record_id,
                "path": path,
                "fragment": fragment,
                "subject_id": nested_subject,
                "visibility": nested_visibility,
            },
        )
        output.append(
            AdultContextFactV1(
                evidence_ref=f"evidence:adult_context:{evidence_sha256[:40]}",
                subject_id=nested_subject,
                authoritative_fact=fragment,
                visibility=nested_visibility,
            )
        )
    return tuple(output)


def _fact_fragments(
    value: Any,
    *,
    path: tuple[str, ...],
) -> Sequence[tuple[tuple[str, ...], str]]:
    if isinstance(value, Mapping):
        output: list[tuple[tuple[str, ...], str]] = []
        for key in sorted(value, key=str):
            output.extend(_fact_fragments(value[key], path=(*path, str(key))))
        return output
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        output = []
        for index, item in enumerate(value):
            output.extend(_fact_fragments(item, path=(*path, str(index))))
        return output

    base = {"path": list(path), "value": value}
    encoded = canonical_json(base)
    if len(encoded) <= _MAX_FACT_CHARS:
        return ((path, encoded),)
    if not isinstance(value, str):
        raise ContractValidationError("adult context fact exceeds its bounded input")
    pieces = tuple(
        value[index : index + _FACT_FRAGMENT_CHARS]
        for index in range(0, len(value), _FACT_FRAGMENT_CHARS)
    )
    output = []
    for index, piece in enumerate(pieces, start=1):
        fragment = canonical_json(
            {
                "path": list(path),
                "part_index": index,
                "part_count": len(pieces),
                "value_fragment": piece,
            }
        )
        if len(fragment) > _MAX_FACT_CHARS:
            raise ContractValidationError("adult context fact fragment exceeds its bound")
        output.append(((*path, f"part_{index}"), fragment))
    return tuple(output)


def _scope_at_path(
    namespace: str,
    record_id: str,
    value: Mapping[str, Any],
    path: tuple[str, ...],
    *,
    initial: tuple[str, str],
) -> tuple[str, str]:
    current: Any = value
    scope = initial
    for part in path:
        if part.startswith("part_"):
            break
        if isinstance(current, Mapping):
            current = current.get(part)
        elif isinstance(current, Sequence) and not isinstance(current, (str, bytes)):
            current = current[int(part)]
        else:
            break
        if isinstance(current, Mapping):
            scope = _fact_scope(
                namespace,
                record_id,
                current,
                inherited=scope,
            )
    return scope


def _fact_scope(
    namespace: str,
    record_id: str,
    value: Mapping[str, Any],
    *,
    inherited: tuple[str, str],
) -> tuple[str, str]:
    raw_visibility = value.get("visibility")
    owner = value.get("knowledge_owner_id")
    if raw_visibility is not None and not isinstance(raw_visibility, str):
        raise ContractValidationError("adult context visibility is not text")
    if owner is not None and not isinstance(owner, str):
        raise ContractValidationError("adult context knowledge owner is invalid")
    if raw_visibility == "public":
        if owner is not None:
            raise ContractValidationError("public adult context unexpectedly names an owner")
        visibility = "public"
    elif raw_visibility in _PRIVATE_VISIBILITIES:
        visibility = "adult_role_private"
    elif raw_visibility is None:
        visibility = inherited[1]
    else:
        raise ContractValidationError("adult context contains an unknown visibility")

    if owner is not None:
        subject_id = owner
    else:
        subject_id = inherited[0]
    _stable_identity(subject_id, "adult context subject_id")
    return subject_id, visibility


def _default_subject_id(namespace: str, record_id: str) -> str:
    if _is_stable_identity(record_id):
        return record_id
    return f"record:{namespace}:{text_sha256(record_id)[:32]}"


def _assert_no_protected_prose_leak(
    protected_records: Sequence[Mapping[str, Any]],
    *,
    safe_projection: str,
    facts: tuple[AdultContextFactV1, ...],
    boundaries: tuple[str, ...],
) -> None:
    safe_values = (
        safe_projection,
        *(value.authoritative_fact for value in facts),
        *boundaries,
    )
    for value in protected_records:
        receipt = value.get("receipt")
        exact_prose = (
            receipt.get("exact_accepted_prose") if isinstance(receipt, Mapping) else None
        )
        if not isinstance(exact_prose, str) or not exact_prose.strip():
            raise StateConflictError("protected adult record omitted exact prose")
        if any(exact_prose in safe_value for safe_value in safe_values):
            raise StateConflictError("exact adult prose escaped into ordinary-safe context")


def _is_stable_identity(value: str) -> bool:
    return _IDENTITY.fullmatch(value) is not None


def _stable_identity(value: str, field: str) -> None:
    if not _is_stable_identity(value):
        raise ContractValidationError(f"{field} must be a stable identity")
