from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from typing import cast

from cera.generated.provider_stage_retry_contracts_v1 import (
    COMPATIBILITY_ADAPTERS,
    ProviderStageRetryContractError,
    normalize_provider_stage_retry_action_v1,
    validate_schema_version,
)

ROOT = Path(__file__).resolve().parents[1]
GENERATOR = ROOT / "scripts" / "generate_provider_stage_retry_contracts.py"
POSITIVE_FIXTURES = (
    ROOT / "tests" / "fixtures" / "generated" / "provider_stage_retry_v1_positive.json"
)
NEGATIVE_FIXTURES = (
    ROOT / "tests" / "fixtures" / "generated" / "provider_stage_retry_v1_negative.json"
)
JAVASCRIPT_CONTRACTS = (
    ROOT / "integrations" / "sillytavern" / "generated" / "provider-stage-retry-contracts-v1.mjs"
)


def _fixture_cases(path: Path) -> list[dict[str, object]]:
    payload = json.loads(path.read_bytes())
    assert isinstance(payload, dict)
    cases = payload["cases"]
    assert isinstance(cases, list)
    return cases


class ProviderStageRetrySchemaGenerationTests(unittest.TestCase):
    def test_generated_artifacts_are_current(self) -> None:
        environment = dict(os.environ)
        environment["PYTHONPATH"] = os.pathsep.join((str(ROOT / "src"), str(ROOT)))
        completed = subprocess.run(
            [sys.executable, str(GENERATOR), "--check"],
            cwd=ROOT,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)

    def test_python_projection_accepts_every_positive_fixture(self) -> None:
        for case in _fixture_cases(POSITIVE_FIXTURES):
            with self.subTest(case=case["case_id"]):
                value = case["value"]
                result = validate_schema_version(str(case["contract_schema_version"]), value)
                self.assertEqual(result, value)
                self.assertIsNot(result, value)

    def test_python_projection_rejects_every_negative_fixture(self) -> None:
        for case in _fixture_cases(NEGATIVE_FIXTURES):
            with self.subTest(case=case["case_id"]):
                with self.assertRaises(ProviderStageRetryContractError):
                    validate_schema_version(
                        str(case["contract_schema_version"]),
                        case["value"],
                    )

    def test_shared_status_encodes_the_approved_distinctions(self) -> None:
        status_cases: list[dict[str, object]] = [
            cast(dict[str, object], copy.deepcopy(case["value"]))
            for case in _fixture_cases(POSITIVE_FIXTURES)
            if case["contract_schema_version"] == "cera.provider_stage_retry_status.v1"
        ]
        by_state = {cast(str, value["state"]): value for value in status_cases}
        self.assertEqual(
            set(by_state),
            {
                "eligible",
                "in_progress",
                "succeeded",
                "blocked_ambiguous",
                "attempts_exhausted",
                "recording_repair_required",
                "recovery_required",
            },
        )

        blocked = by_state["blocked_ambiguous"]
        self.assertEqual(blocked["available_actions"], ["check_status"])
        self.assertLess(
            cast(int, blocked["provider_operations_observed_total"]),
            cast(int, blocked["provider_operations_conservative_total"]),
        )

        exhausted = by_state["attempts_exhausted"]
        self.assertEqual(exhausted["stage_attempts_total"], 3)
        self.assertEqual(exhausted["retry_actions_accepted"], 2)
        self.assertNotEqual(exhausted["stage"], "recorder")

        repair_cases = [
            value for value in status_cases if value["state"] == "recording_repair_required"
        ]
        self.assertEqual(
            {tuple(cast(list[str], value["available_actions"])) for value in repair_cases},
            {(), ("repair_recording",)},
        )
        for repair in repair_cases:
            self.assertEqual(repair["stage"], "recorder")
            self.assertIs(repair["story_state_committed"], True)

        in_progress_cases = [value for value in status_cases if value["state"] == "in_progress"]
        self.assertEqual(
            {tuple(cast(list[str], value["available_actions"])) for value in in_progress_cases},
            {(), ("resume_prepared",)},
        )

        recovery_cases = [value for value in status_cases if value["state"] == "recovery_required"]
        self.assertEqual(
            {value["failure_category"] for value in recovery_cases},
            {"provider_failure_not_retryable", "authority_changed"},
        )
        self.assertEqual(
            {value["stage_attempts_total"] for value in recovery_cases},
            {0, 1},
        )
        for recovery in recovery_cases:
            self.assertEqual(recovery["available_actions"], [])
            self.assertEqual(recovery["provider_operations_observed_total"], 0)
            self.assertEqual(recovery["provider_operations_conservative_total"], 0)

    def test_retry_action_is_not_regenerate_or_replan(self) -> None:
        action_cases: list[dict[str, object]] = [
            cast(dict[str, object], case["value"])
            for case in _fixture_cases(POSITIVE_FIXTURES)
            if case["contract_schema_version"] == "cera.provider_stage_retry_action.v1"
        ]
        provider_retry = next(
            value for value in action_cases if cast(str, value["action_kind"]) == "provider_retry"
        )
        self.assertIs(provider_retry["automatic"], False)
        self.assertIs(provider_retry["provider_dispatch_authorized"], True)
        self.assertIn(provider_retry["retry_action_ordinal"], {1, 2})

        for semantic_action in ("regenerate", "replan"):
            invalid = copy.deepcopy(provider_retry)
            invalid["action_kind"] = semantic_action
            self.assertIsNone(normalize_provider_stage_retry_action_v1(invalid))
            with self.assertRaises(ProviderStageRetryContractError):
                validate_schema_version("cera.provider_stage_retry_action.v1", invalid)

    def test_legacy_planner_adapter_requires_backend_enrichment(self) -> None:
        self.assertEqual(len(COMPATIBILITY_ADAPTERS), 1)
        adapter = COMPATIBILITY_ADAPTERS[0]
        self.assertEqual(adapter["source_stage"], "planner")
        self.assertIs(adapter["requires_authoritative_enrichment"], True)
        self.assertIs(adapter["provider_dispatch_authorized"], False)
        self.assertEqual(adapter["counter_source"], "authoritative_provider_stage_chain_only")
        self.assertEqual(adapter["unmapped_source_states"], ["blocked", "superseded"])

    @unittest.skipUnless(shutil.which("node"), "Node.js is unavailable")
    def test_javascript_projection_matches_generated_fixtures(self) -> None:
        script = f"""
import fs from 'node:fs';
import * as contracts from {json.dumps(JAVASCRIPT_CONTRACTS.resolve().as_uri())};
const positive = JSON.parse(fs.readFileSync({json.dumps(str(POSITIVE_FIXTURES))}, 'utf8'));
const negative = JSON.parse(fs.readFileSync({json.dumps(str(NEGATIVE_FIXTURES))}, 'utf8'));
for (const item of positive.cases) {{
  contracts.validateSchemaVersion(item.contract_schema_version, item.value);
}}
for (const item of negative.cases) {{
  let rejected = false;
  try {{
    contracts.validateSchemaVersion(item.contract_schema_version, item.value);
  }} catch (error) {{
    if (error instanceof contracts.ProviderStageRetryContractError) rejected = true;
    else throw error;
  }}
  if (!rejected) throw new Error(`negative fixture accepted: ${{item.case_id}}`);
}}
"""
        completed = subprocess.run(
            [str(shutil.which("node")), "--input-type=module", "--eval", script],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)


if __name__ == "__main__":
    unittest.main()
