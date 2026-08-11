from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import runpy
import sys
import tempfile
import unittest
from unittest.mock import patch

from cera.providers.codex_sdk_compat import (
    CODEX_SDK_COMPATIBILITY_ID,
    CODEX_SDK_COMPATIBILITY_SOURCE_SHA256,
    EXPECTED_ROUTE_NOTIFICATION_SHA256,
)
from cera.realization import RealizationBoundaryCheck


ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class _ProviderReceipt:
    output_sha256: str = "a" * 64
    external_provider_calls: int = 1


@dataclass(frozen=True)
class _VerificationReceipt:
    status: str = "accepted"
    external_provider_calls: int = 1


@dataclass(frozen=True)
class _Result:
    provider_call_receipt: _ProviderReceipt = _ProviderReceipt()
    receipt: _VerificationReceipt = _VerificationReceipt()


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


class _FakeTransport:
    workspaces: list[Path] = []

    def __init__(self, route, *, workspace: Path, runner: _FakeRunner) -> None:
        del route
        self.workspace = workspace
        self.runner = runner
        self.workspaces.append(workspace)


class _FakePort:
    def __init__(self, transport: _FakeTransport) -> None:
        self.transport = transport


class _FakeCoordinator:
    requests = []

    def execute(self, request, port: _FakePort) -> _Result:
        self.requests.append(request)
        runner = port.transport.runner
        if runner.request_submission_count % 2 == 0:
            runner.process_launch_count += 1
        runner.request_submission_count += 1
        return _Result()


class CodexRealizationVerifierEpochProbeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        scripts = str(ROOT / "scripts")
        sys.path.insert(0, scripts)
        try:
            cls.namespace = runpy.run_path(
                str(
                    ROOT
                    / "scripts"
                    / "run_codex_realization_verifier_epoch_probe.py"
                )
            )
        finally:
            sys.path.remove(scripts)
        cls.run_calls = staticmethod(cls.namespace["_run_calls"])
        cls.build_request = staticmethod(cls.namespace["build_probe_request"])
        cls.sdk_compatibility_metadata = staticmethod(
            cls.namespace["_sdk_compatibility_metadata"]
        )
        cls.main = staticmethod(cls.namespace["main"])

    def setUp(self) -> None:
        _FakeRunner.instances.clear()
        _FakeTransport.workspaces.clear()
        _FakeCoordinator.requests.clear()

    def test_requests_are_unique_and_contain_no_protected_user_realization(self) -> None:
        requests = [
            self.build_request("provider-free-verifier-epoch", sequence)
            for sequence in range(1, 11)
        ]
        self.assertEqual(len({value.request_sha256 for value in requests}), 10)
        self.assertEqual(len({value.candidate_sha256 for value in requests}), 10)
        for request in requests:
            self.assertEqual(len(request.expected_beats), 1)
            self.assertEqual(len(request.selected_participant_ids), 1)
            self.assertEqual(request.protected_user_authorities, ())
            self.assertNotIn("Ted", request.story_text)
            self.assertEqual(
                request.required_boundary_checks,
                (
                    RealizationBoundaryCheck
                    .PROTECTED_USER_NO_UNSUPPLIED_REALIZATION,
                ),
            )

    def test_summary_metadata_binds_current_composite_compatibility_source(
        self,
    ) -> None:
        metadata = self.sdk_compatibility_metadata()
        self.assertIn(
            "_sdk_compatibility_metadata",
            self.main.__code__.co_names,
        )
        self.assertEqual(
            metadata["compatibility_id"],
            CODEX_SDK_COMPATIBILITY_ID,
        )
        self.assertEqual(
            metadata["route_notification_source_sha256"],
            CODEX_SDK_COMPATIBILITY_SOURCE_SHA256,
        )
        self.assertNotEqual(
            metadata["route_notification_source_sha256"],
            EXPECTED_ROUTE_NOTIFICATION_SHA256,
        )

    def test_ten_calls_use_five_bounded_process_epochs(self) -> None:
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
                    "CodexSDKTransport": _FakeTransport,
                    "CodexSceneRealizationVerifierPort": _FakePort,
                    "SceneRealizationVerificationCoordinator": (
                        _FakeCoordinator
                    ),
                },
            ):
                passed = self.run_calls(
                    route=object(),
                    qualification_id="provider-free-verifier-epoch",
                    work_root=root / "work",
                    output_path=root / "summary.json",
                    summary=summary,
                )
        self.assertTrue(passed)
        self.assertEqual(summary["process_launch_count"], 5)
        self.assertEqual(summary["request_submission_count"], 10)
        self.assertEqual(
            [
                value["process_launch_count_after_call"]
                for value in summary["calls"]
            ],
            [1, 1, 2, 2, 3, 3, 4, 4, 5, 5],
        )
        self.assertTrue(
            all(
                value["transport_compatibility_activation_validated"]
                for value in summary["calls"]
            )
        )
        self.assertEqual(
            len(
                {
                    value["evidence_identity"]
                    for value in summary["calls"]
                }
            ),
            10,
        )
        self.assertTrue(_FakeRunner.instances[0].closed)
        self.assertEqual(len(set(_FakeTransport.workspaces)), 10)
        self.assertTrue(
            all(not path.exists() for path in _FakeTransport.workspaces)
        )


if __name__ == "__main__":
    unittest.main()
