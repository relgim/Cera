from __future__ import annotations

import json
from pathlib import Path
import unittest

from cera.continuous.thread_lineage import (
    ContinuousThreadLifecycleEventV1,
    ContinuousThreadLineageEntryV1,
)
from cera.errors import ContractValidationError


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = (
    ROOT / "tests" / "fixtures" / "runtime_model_v3_stage4_harness_v1.json"
)
HISTORICAL_RUNNER_PATH = (
    ROOT
    / ".chatgpt"
    / "operations"
    / "provider-campaigns"
    / "2026-08-03-cera-runtime-model-v3-live-qualification-v1"
    / "run_stage4.py"
)


def _entry(*, role: str, purpose: str) -> ContinuousThreadLineageEntryV1:
    return ContinuousThreadLineageEntryV1(
        role=role,
        purpose=purpose,
        world_id="world:qualification",
        branch_id="branch:main",
        session_compatibility_sha256="0" * 64,
        provider_thread_sha256="1" * 64,
        parent_provider_thread_sha256=None,
        creation_operation="create",
        lifecycle_events=(
            ContinuousThreadLifecycleEventV1(event_type="created"),
            ContinuousThreadLifecycleEventV1(event_type="authorized_active"),
        ),
        terminal_disposition="authorized_active",
        archive_evidence=None,
    )


class RuntimeModelV3Stage4HarnessContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

    def test_harness_fixture_uses_exact_active_lineage_contract(self) -> None:
        self.assertEqual(
            set(self.fixture),
            {
                "schema_version",
                "fixture_id",
                "thread_lineage",
                "obsolete_purposes",
                "scope",
            },
        )
        self.assertEqual(
            self.fixture["schema_version"],
            "cera.runtime_model_v3_stage4_harness_fixture.v1",
        )
        self.assertEqual(
            self.fixture["thread_lineage"],
            {
                "planner": {
                    "role": "planner",
                    "purpose": "primary_planner",
                    "creation_operation": "create",
                },
                "validator": {
                    "role": "validator",
                    "purpose": "primary_validator",
                    "creation_operation": "create",
                },
            },
        )
        for value in self.fixture["thread_lineage"].values():
            entry = _entry(role=value["role"], purpose=value["purpose"])
            self.assertEqual(entry.creation_operation, value["creation_operation"])

    def test_obsolete_qualification_labels_remain_rejected(self) -> None:
        for role, purpose in (
            ("planner", "stage4_planner"),
            ("validator", "stage4_validator"),
        ):
            with self.subTest(role=role), self.assertRaises(
                ContractValidationError
            ):
                _entry(role=role, purpose=purpose)

    def test_historical_runner_is_preserved_and_identifies_drift(self) -> None:
        historical = HISTORICAL_RUNNER_PATH.read_text(encoding="utf-8")
        self.assertIn('purpose="stage4_planner"', historical)
        self.assertIn('purpose="stage4_validator"', historical)
        self.assertNotIn('purpose="primary_planner"', historical)
        self.assertNotIn('purpose="primary_validator"', historical)


if __name__ == "__main__":
    unittest.main()
