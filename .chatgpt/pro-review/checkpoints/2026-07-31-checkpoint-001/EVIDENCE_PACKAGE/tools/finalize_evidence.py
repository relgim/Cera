"""Finalize the Checkpoint 001 evidence manifest and deterministic ZIP."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import zipfile
from pathlib import Path


CHECKPOINT = "248dfbc969a2961338d8f9b35c61bda4f4e6010b"
ROOT = Path(__file__).resolve().parents[6]
PACKAGE = Path(__file__).resolve().parents[1]
ZIP_PATH = PACKAGE.parent / "CERA_CHECKPOINT_001_EVIDENCE.zip"
ZIP_SHA_PATH = PACKAGE.parent / "CERA_CHECKPOINT_001_EVIDENCE.zip.sha256"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_test_result(path: Path) -> dict[str, object]:
    text = path.read_text(encoding="utf-8", errors="replace")
    match = re.search(r"Ran (\d+) tests? in ([0-9.]+)s", text)
    status = "passed" if re.search(r"\nOK\s*$", text) else "failed"
    return {
        "path": str(path.relative_to(PACKAGE)).replace("\\", "/"),
        "status": status,
        "tests": int(match.group(1)) if match else None,
        "seconds": float(match.group(2)) if match else None,
    }


def main() -> None:
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout.strip()
    tracked_diff = subprocess.run(
        ["git", "diff", "--name-only", "HEAD"], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout.splitlines()
    summary = {
        "schema_version": "cera.checkpoint_evidence_summary.v1",
        "checkpoint_sha": CHECKPOINT,
        "observed_head_sha": head,
        "checkpoint_head_matches": head == CHECKPOINT,
        "tracked_worktree_changes": tracked_diff,
        "provider_calls": 0,
        "database_or_story_writes": 0,
        "runtime_implementation_changes": 0,
        "full_suite": read_test_result(PACKAGE / "tests" / "full_suite.stderr.log"),
        "preserved_failed_attempt": read_test_result(PACKAGE / "tests" / "full_suite_attempt_1.stderr.log"),
    }
    (PACKAGE / "PACKAGE_SUMMARY.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    manifest_path = PACKAGE / "MANIFEST.sha256"
    files = sorted(
        path
        for path in PACKAGE.rglob("*")
        if path.is_file() and path != manifest_path and "__pycache__" not in path.parts
    )
    lines = [
        f"{sha256(path)}  {str(path.relative_to(PACKAGE)).replace(chr(92), '/')}"
        for path in files
    ]
    manifest_path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")

    if ZIP_PATH.exists():
        ZIP_PATH.unlink()
    with zipfile.ZipFile(ZIP_PATH, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(path for path in PACKAGE.rglob("*") if path.is_file() and "__pycache__" not in path.parts):
            relative = str(path.relative_to(PACKAGE)).replace("\\", "/")
            info = zipfile.ZipInfo(relative, date_time=(2026, 7, 31, 12, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, path.read_bytes(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    ZIP_SHA_PATH.write_text(
        f"{sha256(ZIP_PATH)}  {ZIP_PATH.name}\n", encoding="utf-8", newline="\n"
    )
    print(json.dumps({"zip": str(ZIP_PATH), "sha256": sha256(ZIP_PATH), "files": len(files) + 1}, indent=2))


if __name__ == "__main__":
    main()
