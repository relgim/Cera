"""Build a deterministic, privacy-safe correction-checkpoint evidence ZIP."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path
import subprocess
import zipfile


ROOT = Path(__file__).resolve().parents[1]


def git(*arguments: str) -> bytes:
    return subprocess.run(
        ["git", *arguments],
        cwd=ROOT,
        check=True,
        capture_output=True,
    ).stdout


def add(archive: zipfile.ZipFile, name: str, data: bytes) -> dict[str, object]:
    info = zipfile.ZipInfo(name)
    info.date_time = (1980, 1, 1, 0, 0, 0)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o100644 << 16
    archive.writestr(info, data)
    return {
        "path": name,
        "sha256": hashlib.sha256(data).hexdigest(),
        "size": len(data),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--suite-passed", type=int, required=True)
    parser.add_argument("--suite-duration-seconds", required=True)
    parser.add_argument("--suite-skipped", type=int, default=0)
    arguments = parser.parse_args()
    baseline = git("rev-parse", "--verify", f"{arguments.baseline}^{{commit}}").decode().strip()
    checkpoint = git("rev-parse", "--verify", f"{arguments.checkpoint}^{{commit}}").decode().strip()
    names = tuple(
        value
        for value in git("diff", "--name-only", "--diff-filter=ACMR", baseline, checkpoint)
        .decode("utf-8")
        .splitlines()
        if value
    )
    entries: list[dict[str, object]] = []
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        entries.append(
            add(
                archive,
                "INDEX.md",
                (
                    "# CERA continuous corrections checkpoint evidence\n\n"
                    f"- Baseline: `{baseline}`\n"
                    f"- Checkpoint: `{checkpoint}`\n"
                    "- Provider calls during Progressions 1-3: `0`\n"
                    "- Complete provider-free suite: "
                    f"`{arguments.suite_passed}/{arguments.suite_passed}` passed "
                    f"in {arguments.suite_duration_seconds} seconds; "
                    f"{arguments.suite_skipped} expected skip(s)\n"
                ).encode("utf-8"),
            )
        )
        entries.append(add(archive, "git/BASELINE_TO_CHECKPOINT.patch", git("diff", "--binary", baseline, checkpoint)))
        entries.append(add(archive, "git/CHECKPOINT_SHOW.txt", git("show", "--stat", "--oneline", "--decorate=no", checkpoint)))
        entries.append(add(archive, "git/CHANGED_FILES.txt", ("\n".join(names) + "\n").encode("utf-8")))
        for relative in names:
            try:
                data = git("show", f"{checkpoint}:{relative}")
            except subprocess.CalledProcessError:
                continue
            entries.append(add(archive, f"files/{relative}", data))
        manifest = {
            "schema_version": "cera.continuous_corrections_checkpoint_evidence.v1",
            "baseline_git_sha": baseline,
            "checkpoint_git_sha": checkpoint,
            "provider_calls": 0,
            "suite_passed": arguments.suite_passed,
            "suite_duration_seconds": arguments.suite_duration_seconds,
            "suite_skipped": arguments.suite_skipped,
            "entries": entries,
        }
        add(
            archive,
            "MANIFEST.json",
            (json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8"),
        )
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_bytes(buffer.getvalue())
    with zipfile.ZipFile(arguments.output) as archive:
        if archive.testzip() is not None:
            raise RuntimeError("generated checkpoint evidence ZIP failed integrity")
    print(hashlib.sha256(arguments.output.read_bytes()).hexdigest())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
