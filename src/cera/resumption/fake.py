"""Declarative fake adapters for Phase 8 projection and aftermath reasoning."""

from __future__ import annotations

from dataclasses import dataclass

from cera.config import Environment
from cera.contracts import AftermathDecision, BlockedTurnCheckpoint, TemporaryAftermathProjection
from cera.errors import ConfigurationError, ContractValidationError
from cera.serialization import domain_sha256

from .models import AftermathReasonerRequest


class AftermathReasonerUnavailable(Exception):
    pass


@dataclass(frozen=True, slots=True)
class FakeAftermathReasonerFixture:
    fixture_id: str
    decision: AftermathDecision

    def __post_init__(self) -> None:
        if not self.fixture_id:
            raise ContractValidationError("fake aftermath fixture ID is required")

    @property
    def fixture_sha256(self) -> str:
        return domain_sha256("cera.fake_aftermath_reasoner_fixture.v1", self)


@dataclass(frozen=True, slots=True)
class FakeProjectionFixture:
    fixture_id: str
    projection: TemporaryAftermathProjection

    def __post_init__(self) -> None:
        if not self.fixture_id:
            raise ContractValidationError("fake projection fixture ID is required")


class FakeSceneReasonerResumptionPort:
    """Scripted fixture carrier; it contains no scenario or psychology rules."""

    def __init__(
        self,
        *,
        aftermath_fixture: FakeAftermathReasonerFixture | None = None,
        projection_fixture: FakeProjectionFixture | None = None,
        environment: Environment = Environment.TEST,
        available: bool = True,
    ) -> None:
        if environment is Environment.PRODUCTION:
            raise ConfigurationError("fake resumption reasoner is prohibited in production")
        self.aftermath_fixture = aftermath_fixture
        self.projection_fixture = projection_fixture
        self.available = available
        self.projection_invocation_count = 0
        self.aftermath_invocation_count = 0

    def project_temporary_aftermath(
        self, checkpoint: BlockedTurnCheckpoint
    ) -> TemporaryAftermathProjection:
        if not self.available:
            raise AftermathReasonerUnavailable("scene reasoner is unavailable")
        if self.projection_fixture is None:
            raise AftermathReasonerUnavailable("no projection fixture was supplied")
        self.projection_invocation_count += 1
        return self.projection_fixture.projection

    def resume_after_external_event(
        self, request: AftermathReasonerRequest
    ) -> AftermathDecision:
        if not self.available:
            raise AftermathReasonerUnavailable("scene reasoner is unavailable")
        if self.aftermath_fixture is None:
            raise AftermathReasonerUnavailable("no aftermath fixture was supplied")
        self.aftermath_invocation_count += 1
        return self.aftermath_fixture.decision

