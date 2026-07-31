from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import runpy
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class _Receipt:
    output_sha256: str = "a" * 64
    external_provider_calls: int = 1


@dataclass(frozen=True)
class _Result:
    parsed_json: dict[str, object]
    receipt: _Receipt = _Receipt()


class _FakeRunner:
    instances: list["_FakeRunner"] = []

    def __init__(self) -> None:
        self.process_launch_count = 1
        self.request_submission_count = 0
        self.closed = False
        self.instances.append(self)

    def close(self) -> None:
        self.closed = True


class _PassingTransport:
    workspaces: list[Path] = []

    def __init__(self, route, *, workspace: Path, runner: _FakeRunner) -> None:
        del route
        self.workspace = workspace
        self.runner = runner
        self.workspaces.append(workspace)

    def invoke(self, prompt: str, *, output_schema: dict[str, object]) -> _Result:
        del prompt, output_schema
        self.runner.request_submission_count += 1
        return _Result({"status": "ok", "tools_used": 0})


class _WrongPayloadTransport(_PassingTransport):
    def invoke(self, prompt: str, *, output_schema: dict[str, object]) -> _Result:
        del prompt, output_schema
        self.runner.request_submission_count += 1
        return _Result({"status": "wrong", "tools_used": 0})


class PersistentCodexTransportProbeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        scripts = str(ROOT / "scripts")
        sys.path.insert(0, scripts)
        try:
            cls.namespace = runpy.run_path(
                str(ROOT / "scripts" / "run_persistent_codex_transport_probe.py")
            )
        finally:
            sys.path.remove(scripts)
        cls.run_role = staticmethod(cls.namespace["_run_role"])

    def setUp(self) -> None:
        _FakeRunner.instances.clear()
        _PassingTransport.workspaces.clear()

    @staticmethod
    def _summary() -> dict[str, object]:
        return {
            "status": "running",
            "calls": [],
            "role_sessions": {},
        }

    def test_role_probe_uses_one_runner_for_two_fresh_workspaces(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary = self._summary()
            with patch.dict(
                self.run_role.__globals__,
                {
                    "PersistentNoMcpCodexRunner": _FakeRunner,
                    "CodexSDKTransport": _PassingTransport,
                },
            ):
                passed = self.run_role(
                    role="scene_reasoner",
                    route=object(),
                    qualification_id="provider-free-probe",
                    work_root=root / "work",
                    output_path=root / "summary.json",
                    summary=summary,
                )
        self.assertTrue(passed)
        self.assertEqual(len(_FakeRunner.instances), 1)
        self.assertTrue(_FakeRunner.instances[0].closed)
        self.assertEqual(
            summary["role_sessions"]["scene_reasoner"],
            {"process_launch_count": 1, "request_submission_count": 2},
        )
        self.assertEqual(
            [value["status"] for value in summary["calls"]],
            ["passed", "passed"],
        )
        self.assertEqual(len(set(_PassingTransport.workspaces)), 2)
        self.assertTrue(all(not path.exists() for path in _PassingTransport.workspaces))

    def test_post_call_payload_failure_is_terminal_and_retains_safe_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary = self._summary()
            with patch.dict(
                self.run_role.__globals__,
                {
                    "PersistentNoMcpCodexRunner": _FakeRunner,
                    "CodexSDKTransport": _WrongPayloadTransport,
                },
            ):
                passed = self.run_role(
                    role="scene_realization_verifier",
                    route=object(),
                    qualification_id="provider-free-probe-failure",
                    work_root=root / "work",
                    output_path=root / "summary.json",
                    summary=summary,
                )
        self.assertFalse(passed)
        self.assertEqual(len(summary["calls"]), 1)
        self.assertEqual(summary["failed_call_index"], 1)
        self.assertEqual(summary["successful_calls"], 0)
        self.assertEqual(summary["sol_dispatches"], 1)
        self.assertEqual(summary["calls"][0]["external_provider_calls_observed"], 1)
        self.assertEqual(
            summary["calls"][0]["provider_receipt"],
            {"output_sha256": "a" * 64, "external_provider_calls": 1},
        )
        self.assertTrue(_FakeRunner.instances[0].closed)


if __name__ == "__main__":
    unittest.main()
