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
    QUALIFIED_MAX_REQUESTS_PER_PROCESS = 2
    instances: list["_FakeRunner"] = []

    def __init__(self) -> None:
        self.process_launch_count = 0
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
        if self.runner.request_submission_count % 2 == 0:
            self.runner.process_launch_count += 1
        self.runner.request_submission_count += 1
        return _Result({"status": "ok", "tools_used": 0})


class PersistentCodexRotationProbeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        scripts = str(ROOT / "scripts")
        sys.path.insert(0, scripts)
        try:
            cls.namespace = runpy.run_path(
                str(ROOT / "scripts" / "run_persistent_codex_rotation_probe.py")
            )
        finally:
            sys.path.remove(scripts)
        cls.run_calls = staticmethod(cls.namespace["_run_calls"])

    def setUp(self) -> None:
        _FakeRunner.instances.clear()
        _PassingTransport.workspaces.clear()

    def test_four_calls_use_two_bounded_process_epochs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary: dict[str, object] = {
                "status": "running",
                "calls": [],
            }
            with patch.dict(
                self.run_calls.__globals__,
                {
                    "PersistentNoMcpCodexRunner": _FakeRunner,
                    "CodexSDKTransport": _PassingTransport,
                    "codex_realization_verifier_candidate": lambda **_kwargs: object(),
                },
            ):
                passed = self.run_calls(
                    qualification_id="provider-free-rotation-probe",
                    work_root=root / "work",
                    output_path=root / "summary.json",
                    summary=summary,
                )
        self.assertTrue(passed)
        self.assertEqual(summary["process_launch_count"], 2)
        self.assertEqual(summary["request_submission_count"], 4)
        self.assertEqual(
            [
                value["process_launch_count_after_call"]
                for value in summary["calls"]
            ],
            [1, 1, 2, 2],
        )
        self.assertTrue(_FakeRunner.instances[0].closed)
        self.assertEqual(len(set(_PassingTransport.workspaces)), 4)
        self.assertTrue(
            all(not path.exists() for path in _PassingTransport.workspaces)
        )


if __name__ == "__main__":
    unittest.main()
