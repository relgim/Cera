from __future__ import annotations

import unittest
from dataclasses import replace

from cera.errors import ContractValidationError
from cera.pi_scene.provider_stage_retry import ProviderStage
from cera.pi_scene.provider_stage_retry_scope import (
    ProviderStageRetryOccurrenceScopeV1,
)
from cera.serialization import text_sha256


def _scope(
    *,
    world_id: str = "world-1",
    branch_id: str = "branch-1",
    request_id: str = "request-1",
    generation_id: str = "generation-1",
    stage: ProviderStage = ProviderStage.PLANNER,
    stage_ordinal: int = 1,
    accepted_state_sha256: str | None = None,
    exact_input: bytes = b"exact-input-1",
) -> ProviderStageRetryOccurrenceScopeV1:
    return ProviderStageRetryOccurrenceScopeV1.create(
        world_id=world_id,
        branch_id=branch_id,
        request_id=request_id,
        generation_id=generation_id,
        stage=stage,
        stage_ordinal=stage_ordinal,
        accepted_state_sha256=accepted_state_sha256 or text_sha256("accepted-head-1"),
        exact_input=exact_input,
        authority_binding={"branch_state_version": 7, "route": "ordinary"},
    )


class ProviderStageRetryOccurrenceScopeTests(unittest.TestCase):
    def test_occurrence_binds_every_counter_scope_component(self) -> None:
        baseline = _scope()
        variants = (
            _scope(world_id="world-2"),
            _scope(branch_id="branch-2"),
            _scope(request_id="request-2"),
            _scope(generation_id="generation-2"),
            _scope(stage=ProviderStage.WRITER),
            _scope(stage_ordinal=2),
            _scope(accepted_state_sha256=text_sha256("accepted-head-2")),
            _scope(exact_input=b"exact-input-2"),
        )

        self.assertEqual(
            len(
                {
                    baseline.request_occurrence_sha256,
                    *(v.request_occurrence_sha256 for v in variants),
                }
            ),
            9,
        )
        self.assertEqual(baseline.identity.stage_input_sha256, baseline.stage_input_sha256)
        self.assertEqual(
            baseline.identity.request_occurrence_sha256, baseline.request_occurrence_sha256
        )

    def test_scope_projects_closed_owner_and_recorder_story_boundary(self) -> None:
        planner = _scope(stage=ProviderStage.PLANNER)
        recorder = _scope(stage=ProviderStage.RECORDER)

        self.assertEqual(planner.identity.provider.value, "codex")
        self.assertEqual(planner.identity.model_family.value, "sol")
        self.assertFalse(planner.identity.story_state_committed)
        self.assertEqual(recorder.identity.provider.value, "deepseek")
        self.assertEqual(recorder.identity.model_family.value, "deepseek_v4")
        self.assertTrue(recorder.identity.story_state_committed)

    def test_derived_hashes_cannot_be_rewritten(self) -> None:
        scope = _scope()

        with self.assertRaises(ContractValidationError):
            replace(scope, request_occurrence_sha256=text_sha256("forged"))
        with self.assertRaises(ContractValidationError):
            replace(scope, authority_sha256=text_sha256("forged-authority"))


if __name__ == "__main__":
    unittest.main()
