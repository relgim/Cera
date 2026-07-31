"""Scripted provider-free adapter for deferred consolidation tests."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from cera.config import Environment
from cera.errors import ConfigurationError, ContractValidationError
from cera.serialization import domain_sha256
from cera.ids import IdKind, deterministic_id

from .models import (
    ConsolidationExecutionResult,
    ConsolidationProposal,
    ConsolidationReasonerReceipt,
    DerivedConsolidationRequest,
)


class DerivedConsolidatorPort(Protocol):
    def consolidate(
        self, request: DerivedConsolidationRequest
    ) -> ConsolidationExecutionResult: ...


class DerivedConsolidatorUnavailable(Exception):
    pass


@dataclass(frozen=True, slots=True)
class FakeConsolidationFixture:
    fixture_id: str
    proposal: ConsolidationProposal

    def __post_init__(self) -> None:
        if not isinstance(self.fixture_id, str) or not self.fixture_id.strip():
            raise ContractValidationError("fake consolidation fixture ID must be non-empty")

    @property
    def fixture_sha256(self) -> str:
        return domain_sha256("cera.fake_consolidation_fixture.v1", self)


class FakeDerivedConsolidatorPort:
    """Returns one declared proposal and contains no character or memory rules."""

    def __init__(
        self,
        fixture: FakeConsolidationFixture,
        *,
        environment: Environment = Environment.TEST,
        available: bool = True,
    ) -> None:
        if environment is Environment.PRODUCTION:
            raise ConfigurationError("FakeDerivedConsolidatorPort is prohibited in production")
        self.fixture = fixture
        self.available = available
        self.invocation_count = 0

    def consolidate(
        self, request: DerivedConsolidationRequest
    ) -> ConsolidationExecutionResult:
        if not self.available:
            raise DerivedConsolidatorUnavailable("scripted consolidator is unavailable")
        self.invocation_count += 1
        proposal = self.fixture.proposal
        receipt = ConsolidationReasonerReceipt(
            schema_version=ConsolidationReasonerReceipt.SCHEMA_VERSION,
            provider_receipt_id=deterministic_id(
                IdKind.PROVIDER_RECEIPT,
                "cera.consolidation.fake.receipt.v1",
                f"{request.request_sha256}|{self.fixture.fixture_sha256}",
            ),
            request_sha256=request.request_sha256,
            snapshot_token=request.snapshot.snapshot_token,
            snapshot_binding_sha256=request.snapshot.binding_sha256,
            fixture_id=self.fixture.fixture_id,
            fixture_sha256=self.fixture.fixture_sha256,
            proposal_sha256=proposal.proposal_sha256,
            source_evidence_ids=proposal.source_evidence_ids,
            lookup_receipt_ids=request.lookup_receipt_ids,
            external_provider_calls=0,
        )
        return ConsolidationExecutionResult(proposal=proposal, receipt=receipt)
