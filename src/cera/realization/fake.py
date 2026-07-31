"""Scripted provider-free scene-realization verifier."""

from __future__ import annotations

from cera.config import Environment
from cera.errors import ConfigurationError
from cera.serialization import domain_sha256

from .models import (
    RealizationVerifierAdapterRole,
    RealizationVerificationStatus,
    SceneRealizationVerificationDraft,
    SceneRealizationVerificationRequest,
    SceneRealizationVerifierCall,
    SceneRealizationVerifierPort,
)


class ScriptedSceneRealizationVerifierPort(SceneRealizationVerifierPort):
    adapter_version = "cera.scripted_scene_realization_verifier.v2"
    qualification_eligible = False

    def __init__(
        self,
        draft: SceneRealizationVerificationDraft,
        *,
        environment: Environment = Environment.TEST,
    ) -> None:
        if environment is Environment.PRODUCTION:
            raise ConfigurationError(
                "ScriptedSceneRealizationVerifierPort is prohibited in production"
            )
        self.draft = draft
        self.invocation_count = 0

    def verify(
        self,
        request: SceneRealizationVerificationRequest,
    ) -> SceneRealizationVerifierCall:
        self.invocation_count += 1
        return _fake_call(self, self.draft)


class EchoAcceptingSceneRealizationVerifierPort(SceneRealizationVerifierPort):
    """Test-harness fake only; reflects request obligations as accepted.

    It proves pipeline wiring, not semantic quality. Adversarial tests must use
    the scripted port with explicit rejection/inconclusive fixtures.
    """

    adapter_version = "cera.echo_scene_realization_verifier.v2"
    qualification_eligible = False

    def __init__(self, *, environment: Environment = Environment.TEST) -> None:
        if environment is Environment.PRODUCTION:
            raise ConfigurationError(
                "EchoAcceptingSceneRealizationVerifierPort is prohibited in production"
            )
        self.invocation_count = 0

    def verify(
        self,
        request: SceneRealizationVerificationRequest,
    ) -> SceneRealizationVerifierCall:
        self.invocation_count += 1
        return _fake_call(
            self,
            SceneRealizationVerificationDraft(
                status=RealizationVerificationStatus.ACCEPTED,
                verified_beat_ids=request.expected_beat_ids,
                verified_participant_ids=request.selected_participant_ids,
                violation_codes=(),
                verified_boundary_checks=request.required_boundary_checks,
                verified_development_atom_ids=tuple(
                    value.atom_id for value in request.development_expectations
                ),
            ),
        )


def _fake_call(port, draft: SceneRealizationVerificationDraft) -> SceneRealizationVerifierCall:
    return SceneRealizationVerifierCall(
        draft=draft,
        adapter_role=RealizationVerifierAdapterRole.SCRIPTED_FAKE,
        adapter_version=port.adapter_version,
        adapter_evidence_id=port.adapter_version,
        adapter_evidence_sha256=domain_sha256(
            "cera.scripted_scene_realization_verifier.evidence.v1",
            {"adapter_version": port.adapter_version, "draft": draft},
        ),
        qualification_eligible=False,
        external_provider_calls=0,
    )
