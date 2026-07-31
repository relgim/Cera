from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import json
import tempfile
import unittest

from cera.providers.codex_session_worker import _run_request, _validate_request


class FakeThread:
    def __init__(self, transport_version: str) -> None:
        self.transport_version = transport_version
        self.run_calls = 0

    def run(self, prompt, *, effort, output_schema):
        self.run_calls += 1
        return SimpleNamespace(
            final_response='{"probe":"ok"}',
            usage=SimpleNamespace(
                last=SimpleNamespace(
                    input_tokens=10,
                    cached_input_tokens=0,
                    output_tokens=4,
                    reasoning_output_tokens=2,
                )
            ),
            id=f"turn-{self.run_calls}",
            duration_ms=5,
            items=[],
        )

    def read(self):
        return SimpleNamespace(
            thread=SimpleNamespace(
                model_provider="openai",
                cli_version=self.transport_version,
            )
        )


class FakeCodex:
    def __init__(self) -> None:
        self.start_calls = []

    def thread_start(self, **kwargs):
        self.start_calls.append(kwargs)
        return FakeThread("0.144.4")


def request_for(workspace: Path, prompt: str) -> dict:
    progress_path = workspace / ".cera_codex_worker_progress.json"
    progress_path.write_text(
        json.dumps(
            {
                "schema_version": "cera.codex_worker_progress.v1",
                "stage": "worker_launch",
            }
        ),
        encoding="utf-8",
    )
    return {
        "protocol_version": "cera.codex_persistent_no_mcp.v1",
        "model": "gpt-5.6-sol",
        "effort": "medium",
        "role": "scene_realization_verifier",
        "prompt": prompt,
        "output_schema": {"type": "object"},
        "workspace": str(workspace),
        "progress_path": str(progress_path),
        "transport_version": "0.144.4",
        "mcp_binding": None,
    }


class CodexPersistentSessionWorkerTests(unittest.TestCase):
    def test_reused_process_creates_fresh_ephemeral_thread_for_each_request(self) -> None:
        codex = FakeCodex()
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            first_request = request_for(Path(first), "first")
            second_request = request_for(Path(second), "second")
            compatibility = SimpleNamespace(
                compatibility_id="cera.codex_sdk_completion_registration.v2",
                source_sha256=(
                    "8fd316aa949d03812e935b0928e3767d0faa1d79701f599d97e66c0e46c679d1"
                ),
                buffered_early_completion_count=0,
                pre_registered_turn_count=2,
            )
            first_result = _run_request(
                codex,
                first_request,
                sequence=1,
                compatibility_state=compatibility,
            )
            second_result = _run_request(
                codex,
                second_request,
                sequence=2,
                compatibility_state=compatibility,
            )
        self.assertEqual(len(codex.start_calls), 2)
        self.assertTrue(all(value["ephemeral"] for value in codex.start_calls))
        self.assertNotEqual(
            codex.start_calls[0]["cwd"],
            codex.start_calls[1]["cwd"],
        )
        self.assertEqual(first_result["output_text"], '{"probe":"ok"}')
        self.assertEqual(second_result["output_text"], '{"probe":"ok"}')
        self.assertEqual(first_result["mcp_tool_call_count"], 0)
        self.assertEqual(second_result["mcp_tool_call_count"], 0)
        self.assertEqual(first_result["pre_registered_turn_count"], 2)
        self.assertEqual(second_result["pre_registered_turn_count"], 2)

    def test_request_validation_rejects_mcp_or_extra_workspace_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            request = request_for(workspace, "probe")
            request["mcp_binding"] = {"unexpected": True}
            with self.assertRaisesRegex(ValueError, "cannot receive MCP"):
                _validate_request(request)
            request["mcp_binding"] = None
            (workspace / "unexpected.txt").write_text("x", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "not isolated"):
                _validate_request(request)


if __name__ == "__main__":
    unittest.main()
