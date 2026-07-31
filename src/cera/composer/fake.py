"""Declarative fake adapter for the provider-neutral Scene Composer port."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from cera.config import Environment
from cera.errors import ConfigurationError, ContractValidationError
from cera.ids import IdKind, deterministic_id
from cera.serialization import domain_sha256

from .models import (
    ComposerAdapterRole,
    ComposerSubmission,
    SceneComposerAdapterCall,
    SceneComposerRequest,
)


class SceneComposerPort(Protocol):
    def compose(self, request: SceneComposerRequest) -> SceneComposerAdapterCall: ...


@dataclass(frozen=True, slots=True)
class FakeComposerFixture:
    fixture_id: str
    submission: ComposerSubmission

    def __post_init__(self) -> None:
        if not isinstance(self.fixture_id, str) or not self.fixture_id.strip():
            raise ContractValidationError("fake composer fixture ID must be non-empty")

    @property
    def fixture_sha256(self) -> str:
        return domain_sha256("cera.fake_composer_fixture.v1", self)


class SceneComposerUnavailable(Exception):
    pass


class SceneComposerPortFailure(Exception):
    def __init__(
        self,
        code,
        message: str,
        stage: str = "composer_dispatch",
        *,
        safe_diagnostics: tuple[str, ...] = (),
    ) -> None:
        if (
            not isinstance(safe_diagnostics, tuple)
            or any(
                not isinstance(value, str) or not value.strip()
                for value in safe_diagnostics
            )
        ):
            raise ContractValidationError(
                "composer port diagnostics must be non-empty strings"
            )
        self.code = code
        self.stage = stage
        self.provider_call_receipt = None
        self.external_provider_calls_observed = 0
        self.safe_diagnostics = tuple(safe_diagnostics)
        super().__init__(message)


class FakeSceneComposerPort:
    """Returns one declared submission; it contains no writing or routing rules."""

    def __init__(
        self,
        fixture: FakeComposerFixture,
        *,
        environment: Environment = Environment.TEST,
        available: bool = True,
    ) -> None:
        if environment is Environment.PRODUCTION:
            raise ConfigurationError("FakeSceneComposerPort is prohibited in production")
        self.fixture = fixture
        self.available = available
        self.invocation_count = 0

    def compose(self, request: SceneComposerRequest) -> SceneComposerAdapterCall:
        if not self.available:
            raise SceneComposerUnavailable("scripted composer is unavailable")
        self.invocation_count += 1
        submission = self.fixture.submission
        provider_receipt_id = deterministic_id(
            IdKind.PROVIDER_RECEIPT,
            "cera.fake_composer.receipt.v1",
            (
                f"{request.request_sha256}|{self.fixture.fixture_sha256}|"
                f"{submission.candidate.candidate_sha256}|"
                f"{submission.manifest.manifest_sha256}"
            ),
        )
        return SceneComposerAdapterCall(
            submission=submission,
            adapter_role=ComposerAdapterRole.SCRIPTED_FAKE,
            adapter_version="cera.fake_scene_composer.v1",
            adapter_evidence_id=self.fixture.fixture_id,
            adapter_evidence_sha256=self.fixture.fixture_sha256,
            provider_receipt_id=provider_receipt_id,
            provider_receipt_sha256=domain_sha256(
                "cera.fake_composer.receipt.v1",
                {
                    "provider_receipt_id": str(provider_receipt_id),
                    "request_sha256": request.request_sha256,
                    "fixture_sha256": self.fixture.fixture_sha256,
                    "candidate_sha256": submission.candidate.candidate_sha256,
                    "manifest_sha256": submission.manifest.manifest_sha256,
                },
            ),
            external_provider_calls=0,
            provider_call_receipt=None,
        )
