from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from cera.continuous.world import ContinuousDebugRecorder
from cera.errors import ContractValidationError


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = (
    ROOT / "tests" / "fixtures" / "runtime_model_v3_stage4_harness_v2.json"
)
HISTORICAL_RUNNER_PATH = (
    ROOT
    / ".chatgpt"
    / "operations"
    / "provider-campaigns"
    / "2026-08-03-cera-runtime-model-v3-live-qualification-v1"
    / "run_stage4.py"
)


class RuntimeModelV3Stage4HarnessIdentityContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

    def test_fixture_binds_active_lineage_and_filesystem_safe_ids(self) -> None:
        self.assertEqual(
            self.fixture["schema_version"],
            "cera.runtime_model_v3_stage4_harness_fixture.v2",
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
        self.assertEqual(
            self.fixture["request_identity"],
            {"scene_id": "scene-001", "turn_id": "turn-001"},
        )

    def test_active_ids_create_exact_debug_path(self) -> None:
        identity = self.fixture["request_identity"]
        with TemporaryDirectory() as temporary:
            recorder = ContinuousDebugRecorder(
                Path(temporary), identity["scene_id"], identity["turn_id"]
            )
            self.assertEqual(
                recorder.root,
                Path(temporary) / "DEBUG" / "scene-001" / "turn-001",
            )

    def test_obsolete_ids_remain_rejected_by_production_contract(self) -> None:
        obsolete = self.fixture["obsolete_identifiers"]
        with TemporaryDirectory() as temporary:
            for scene_id, turn_id in (
                (obsolete["scene_id"], "turn-001"),
                ("scene-001", obsolete["turn_id"]),
            ):
                with self.subTest(scene_id=scene_id, turn_id=turn_id), self.assertRaises(
                    ContractValidationError
                ):
                    ContinuousDebugRecorder(Path(temporary), scene_id, turn_id)

    def test_historical_runner_remains_immutable_evidence(self) -> None:
        historical = HISTORICAL_RUNNER_PATH.read_text(encoding="utf-8")
        self.assertIn('scene_id="scene:001"', historical)
        self.assertGreaterEqual(historical.count('turn_id="turn:001"'), 2)
        self.assertIn('harness._active_turn_id = "turn:001"', historical)


if __name__ == "__main__":
    unittest.main()
