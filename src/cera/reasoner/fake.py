"""Scripted development/test adapter for the provider-neutral reasoner port."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from cera.config import Environment
from cera.errors import ConfigurationError, ContractValidationError
from cera.evidence import (
    CharacterSectionsRequest,
    ContinuityRequest,
    EvidenceFetchRequest,
    EvidenceQueryPlan,
    EvidenceSearchRequest,
)
from cera.ids import IdKind, TypedId, deterministic_id, require_kind
from cera.serialization import domain_sha256

from .models import (
    ReasonerAdapterRole,
    ReasonerOutcome,
    SceneReasonerAdapterCall,
    SceneReasonerRequest,
)


class SceneReasonerPort(Protocol):
    def reason(
        self, request: SceneReasonerRequest, tools: "ReasonerEvidenceToolPort"
    ) -> SceneReasonerAdapterCall: ...


class ReasonerEvidenceToolPort(Protocol):
    def evidence_budget_status(self) -> dict[str, int]: ...
    def get_turn_snapshot(self): ...
    def resolve_entities(self, request: "ResolveEntitiesRequest"): ...
    def search_evidence(self, request: EvidenceSearchRequest): ...
    def search_query_plan(self, request: EvidenceQueryPlan): ...
    def fetch_evidence(self, request: EvidenceFetchRequest): ...
    def get_character_sections(self, request: CharacterSectionsRequest): ...
    def get_continuity(self, request: ContinuityRequest): ...


@dataclass(frozen=True, slots=True)
class ResolveEntitiesRequest:
    entity_ids: tuple[TypedId, ...]
    limit: int = 8

    def __post_init__(self) -> None:
        if not self.entity_ids:
            raise ContractValidationError("entity resolution requires IDs")
        if self.limit < 1:
            raise ContractValidationError("entity resolution limit must be positive")
        for entity_id in self.entity_ids:
            if entity_id.kind not in {
                IdKind.CHARACTER,
                IdKind.WORLD,
                IdKind.RELATIONSHIP,
                IdKind.MATERIAL,
            }:
                raise ContractValidationError("unsupported entity resolution ID")


class FakeToolOperation(StrEnum):
    GET_TURN_SNAPSHOT = "get_turn_snapshot"
    RESOLVE_ENTITIES = "resolve_entities"
    SEARCH_EVIDENCE = "search_evidence"
    SEARCH_QUERY_PLAN = "search_query_plan"
    FETCH_EVIDENCE = "fetch_evidence"
    GET_CHARACTER_SECTIONS = "get_character_sections"
    GET_CONTINUITY = "get_continuity"


@dataclass(frozen=True, slots=True)
class FakeToolCall:
    operation: FakeToolOperation
    request: (
        ResolveEntitiesRequest
        | EvidenceSearchRequest
        | EvidenceQueryPlan
        | EvidenceFetchRequest
        | CharacterSectionsRequest
        | ContinuityRequest
        | None
    )

    def __post_init__(self) -> None:
        expected = {
            FakeToolOperation.GET_TURN_SNAPSHOT: type(None),
            FakeToolOperation.RESOLVE_ENTITIES: ResolveEntitiesRequest,
            FakeToolOperation.SEARCH_EVIDENCE: EvidenceSearchRequest,
            FakeToolOperation.SEARCH_QUERY_PLAN: EvidenceQueryPlan,
            FakeToolOperation.FETCH_EVIDENCE: EvidenceFetchRequest,
            FakeToolOperation.GET_CHARACTER_SECTIONS: CharacterSectionsRequest,
            FakeToolOperation.GET_CONTINUITY: ContinuityRequest,
        }[self.operation]
        if not isinstance(self.request, expected):
            raise ContractValidationError("fake tool call request does not match operation")


@dataclass(frozen=True, slots=True)
class FakeReasonerFixture:
    fixture_id: str
    tool_calls: tuple[FakeToolCall, ...]
    outcome: ReasonerOutcome

    def __post_init__(self) -> None:
        if not self.fixture_id.strip():
            raise ContractValidationError("fake fixture ID must be non-empty")

    @property
    def fixture_sha256(self) -> str:
        return domain_sha256("cera.fake_reasoner_fixture.v1", self)


class SceneReasonerUnavailable(Exception):
    pass


class SceneReasonerPortFailure(Exception):
    def __init__(
        self,
        code,
        message: str,
        stage: str = "reasoner_dispatch",
        *,
        safe_diagnostics: tuple[str, ...] = (),
        external_provider_calls_observed: int = 0,
    ) -> None:
        self.code = code
        self.stage = stage
        self.safe_diagnostics = tuple(safe_diagnostics)
        if (
            type(external_provider_calls_observed) is not int
            or external_provider_calls_observed not in {0, 1}
        ):
            raise ValueError("reasoner call observation must be zero or one")
        self.external_provider_calls_observed = external_provider_calls_observed
        self.provider_call_receipt = None
        self.mcp_bridge_receipt = None
        self.operation_telemetry = None
        super().__init__(message)


class FakeSceneReasonerPort:
    """Executes a declarative script; contains no character reasoning rules."""

    def __init__(
        self,
        fixture: FakeReasonerFixture,
        *,
        environment: Environment = Environment.TEST,
        available: bool = True,
    ) -> None:
        if environment is Environment.PRODUCTION:
            raise ConfigurationError("FakeSceneReasonerPort is prohibited in production")
        self.fixture = fixture
        self.available = available
        self.invocation_count = 0

    def reason(
        self, request: SceneReasonerRequest, tools: ReasonerEvidenceToolPort
    ) -> SceneReasonerAdapterCall:
        if not self.available:
            raise SceneReasonerUnavailable("scripted reasoner is unavailable")
        self.invocation_count += 1
        for call in self.fixture.tool_calls:
            if call.operation is FakeToolOperation.GET_TURN_SNAPSHOT:
                tools.get_turn_snapshot()
            elif call.operation is FakeToolOperation.RESOLVE_ENTITIES:
                assert isinstance(call.request, ResolveEntitiesRequest)
                tools.resolve_entities(call.request)
            elif call.operation is FakeToolOperation.SEARCH_EVIDENCE:
                assert isinstance(call.request, EvidenceSearchRequest)
                tools.search_evidence(call.request)
            elif call.operation is FakeToolOperation.SEARCH_QUERY_PLAN:
                assert isinstance(call.request, EvidenceQueryPlan)
                tools.search_query_plan(call.request)
            elif call.operation is FakeToolOperation.FETCH_EVIDENCE:
                assert isinstance(call.request, EvidenceFetchRequest)
                tools.fetch_evidence(call.request)
            elif call.operation is FakeToolOperation.GET_CHARACTER_SECTIONS:
                assert isinstance(call.request, CharacterSectionsRequest)
                tools.get_character_sections(call.request)
            else:
                assert isinstance(call.request, ContinuityRequest)
                tools.get_continuity(call.request)
        outcome = self.fixture.outcome
        provider_receipt_id = deterministic_id(
            IdKind.PROVIDER_RECEIPT,
            "cera.fake_reasoner.receipt.v1",
            f"{request.request_sha256}|{self.fixture.fixture_sha256}|{outcome.outcome_sha256}",
        )
        return SceneReasonerAdapterCall(
            outcome=outcome,
            adapter_role=ReasonerAdapterRole.SCRIPTED_FAKE,
            adapter_version="cera.fake_scene_reasoner.v1",
            adapter_evidence_id=self.fixture.fixture_id,
            adapter_evidence_sha256=self.fixture.fixture_sha256,
            provider_receipt_id=provider_receipt_id,
            provider_receipt_sha256=domain_sha256(
                "cera.fake_reasoner.receipt.v1",
                {
                    "provider_receipt_id": str(provider_receipt_id),
                    "request_sha256": request.request_sha256,
                    "fixture_sha256": self.fixture.fixture_sha256,
                    "outcome_sha256": outcome.outcome_sha256,
                },
            ),
            bridge_receipt_id=None,
            bridge_receipt_sha256=None,
            external_provider_calls=0,
        )
