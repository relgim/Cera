"""Deterministic child process that proves progress-sidecar observation."""

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
    progress = Path(request["progress_path"])
    _write(progress, "request_decode")
    time.sleep(0.03)
    _write(progress, "thread_run")
    time.sleep(0.05)
    print(
        "codex qualification worker failed: fixture post-submit failure",
        file=sys.stderr,
        flush=True,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
