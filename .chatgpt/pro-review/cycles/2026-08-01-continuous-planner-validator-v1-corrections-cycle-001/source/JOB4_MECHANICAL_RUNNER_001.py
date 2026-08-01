"""Cycle-local mechanical wrapper for the frozen provider-free Job 4 runner."""

from __future__ import annotations

import hashlib
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[5]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_continuous_corrections_job4 as frozen_runner


def corrected_sqlite_check(source: Path) -> dict[str, object]:
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    with tempfile.TemporaryDirectory(prefix="cera-corrections-job4-") as directory:
        copied = Path(directory) / "disposable.sqlite3"
        shutil.copy2(source, copied)
        copy_before = hashlib.sha256(copied.read_bytes()).hexdigest()
        connection = sqlite3.connect(f"file:{copied.as_posix()}?mode=ro", uri=True)
        try:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
            foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
        finally:
            connection.close()
        copy_after = hashlib.sha256(copied.read_bytes()).hexdigest()
    after = hashlib.sha256(source.read_bytes()).hexdigest()
    return {
        "source_path": str(source),
        "source_sha256_before": before,
        "source_sha256_after": after,
        "source_unchanged": before == after,
        "disposable_sha256_before": copy_before,
        "disposable_sha256_after": copy_after,
        "disposable_unchanged": copy_before == copy_after,
        "integrity_check": integrity,
        "foreign_key_findings": len(foreign_keys),
    }


if __name__ == "__main__":
    frozen_runner.sqlite_check = corrected_sqlite_check
    raise SystemExit(frozen_runner.main())
