"""Deterministic child protocol fixture for Codex worker stage accounting."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import time


def _write(path: Path, stage: str) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": "cera.codex_worker_progress.v1",
                "stage": stage,
            },
            sort_keys=True,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )


def main() -> int:
    request = json.loads(sys.stdin.read())
    control = json.loads(request["prompt"])
    progress = Path(request["progress_path"])
    mode = control["mode"]
    stage = control.get("stage", "request_decode")
    if stage != "worker_launch":
        _write(progress, stage)
    if mode == "fail":
        print(
            f"codex qualification worker failed: fixture {stage}",
            file=sys.stderr,
            flush=True,
        )
        return 1
    if mode == "timeout":
        time.sleep(5)
        return 1
    if mode == "malformed":
        _write(progress, "response_encode")
        print("{", flush=True)
        return 0
    if mode != "success":
        raise ValueError("unknown fixture mode")
    for value in (
        "request_decode",
        "sdk_import",
        "account_check",
        "thread_resume",
        "thread_run",
        "thread_read",
        "result_check",
        "response_encode",
    ):
        _write(progress, value)
    print(
        json.dumps(
            {
                "output_text": '{"ok":true}',
                "provider_request_id": "fixture-request",
                "returned_model": request["model"],
                "duration_ms": 1,
                "input_tokens": 1,
                "cached_input_tokens": 0,
                "output_tokens": 1,
                "reasoning_output_tokens": 0,
                "transport_version": request["transport_version"],
                "mcp_server_names": [],
                "mcp_tool_names": [],
                "mcp_tool_call_count": 0,
                "mcp_failed_tool_call_count": 0,
                "pre_registered_turn_count": 1,
            },
            sort_keys=True,
            separators=(",", ":"),
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
