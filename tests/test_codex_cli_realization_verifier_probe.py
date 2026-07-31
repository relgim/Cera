from __future__ import annotations

import json
from pathlib import Path
import runpy
import sys
import tempfile
import unittest
from unittest.mock import patch

from cera.realization import (
    RealizationBoundaryCheck,
    RealizationVerificationStatus,
)


ROOT = Path(__file__).resolve().parents[1]


class CodexCliRealizationVerifierProbeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        scripts = str(ROOT / "scripts")
        sys.path.insert(0, scripts)
        try:
            cls.namespace = runpy.run_path(
                str(
                    ROOT
                    / "scripts"
                    / "run_codex_cli_realization_verifier_probe.py"
                )
            )
        finally:
            sys.path.remove(scripts)
        cls.cases = cls.namespace["CASES"]
        cls.build_probe_request = staticmethod(
            cls.namespace["build_probe_request"]
        )
        cls.main = staticmethod(cls.namespace["main"])

    def test_cases_cover_realistic_accept_reject_and_multi_character_packets(self) -> None:
        requests = [
            self.build_probe_request(case, index)
            for index, case in enumerate(self.cases, start=1)
        ]
        self.assertEqual(len(requests), 4)
        self.assertEqual(len({value.request_sha256 for value in requests}), 4)
        self.assertEqual(len({value.candidate_sha256 for value in requests}), 4)
        self.assertEqual(
            [case.expected_status for case in self.cases],
            [
                RealizationVerificationStatus.ACCEPTED,
                RealizationVerificationStatus.REJECTED,
                RealizationVerificationStatus.ACCEPTED,
                RealizationVerificationStatus.ACCEPTED,
            ],
        )
        self.assertGreaterEqual(
            max(len(value.selected_participant_ids) for value in requests),
            3,
        )
        self.assertGreaterEqual(
            max(len(value.expected_beats) for value in requests),
            3,
        )
        for request in requests:
            self.assertEqual(
                request.required_boundary_checks,
                (
                    RealizationBoundaryCheck
                    .PROTECTED_USER_NO_UNSUPPLIED_REALIZATION,
                ),
            )

    def test_main_dispatches_each_case_once_and_retains_no_candidate_prose(self) -> None:
        calls: list[int] = []

        def execute_case(*, case, index, route, work_root):
            del case, route, work_root
            calls.append(index)
            return True, {
                "index": index,
                "case_name": f"case-{index}",
                "evidence_identity": f"evaluation:{index}",
                "request_id": f"request:{index}",
                "request_sha256": str(index) * 64,
                "candidate_sha256": str(index) * 64,
                "expected_status": "accepted",
                "expected_violation": None,
                "selected_participant_count": 2,
                "expected_beat_count": 2,
                "status": "passed",
                "dispatch_started": True,
                "external_provider_calls_observed": 1,
                "automatic_retry_count": 0,
                "fallback_enabled": False,
                "story_authority_writes": 0,
                "retains_prompt": False,
                "retains_raw_output": False,
                "retains_story_prose": False,
                "workspace_empty_after_call": True,
            }

        budget = {
            "remaining": {"sol": 400, "deepseek": 400},
            "limits": {"sol": 500, "deepseek": 500},
        }
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "fresh-evidence"
            argv = [
                "run_codex_cli_realization_verifier_probe.py",
                "--confirm-live",
                "--output",
                str(output),
            ]
            with patch.dict(
                self.main.__globals__,
                {
                    "_execute_case": execute_case,
                    "audit_live_call_budget": lambda **_kwargs: budget,
                },
            ), patch.object(sys, "argv", argv):
                exit_code = self.main()
            payload = json.loads(
                (output / "summary.json").read_text(encoding="utf-8")
            )
        self.assertEqual(exit_code, 0)
        self.assertEqual(calls, [1, 2, 3, 4])
        self.assertEqual(payload["status"], "passed")
        self.assertEqual(payload["passed_case_count"], 4)
        self.assertEqual(payload["external_provider_calls_observed"], 4)
        self.assertTrue(payload["all_workspaces_empty"])
        serialized = json.dumps(payload)
        for case in self.cases:
            self.assertNotIn(case.story_text, serialized)


if __name__ == "__main__":
    unittest.main()
