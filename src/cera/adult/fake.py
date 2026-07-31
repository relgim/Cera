"""Declarative fake for non-authoritative adult mechanics enrichment."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from cera.config import Environment
from cera.errors import ConfigurationError, ContractValidationError
from cera.serialization import domain_sha256

from .models import AdultMechanicsProposal, AdultMechanicsRequest


class AdultMechanicsPort(Protocol):
    def enrich(self, request: AdultMechanicsRequest) -> AdultMechanicsProposal: ...


@dataclass(frozen=True, slots=True)
class FakeAdultMechanicsFixture:
    fixture_id: str
    proposal: AdultMechanicsProposal

    def __post_init__(self) -> None:
        if not isinstance(self.fixture_id, str) or not self.fixture_id.strip():
            raise ContractValidationError("fake adult mechanics fixture ID must be non-empty")

    @property
    def fixture_sha256(self) -> str:
        return domain_sha256("cera.fake_adult_mechanics_fixture.v1", self)


class AdultMechanicsUnavailable(Exception):
    pass


class FakeAdultMechanicsPort:
    """Returns one operational fixture; it cannot decide psychology or write prose."""

    def __init__(
        self,
        fixture: FakeAdultMechanicsFixture,
        *,
        environment: Environment = Environment.TEST,
        available: bool = True,
    ) -> None:
        if environment is Environment.PRODUCTION:
            raise ConfigurationError("FakeAdultMechanicsPort is prohibited in production")
        self.fixture = fixture
        self.available = available
        self.invocation_count = 0

    def enrich(self, request: AdultMechanicsRequest) -> AdultMechanicsProposal:
        if not self.available:
            raise AdultMechanicsUnavailable("scripted adult mechanics adapter is unavailable")
        self.invocation_count += 1
        return self.fixture.proposal
