"""Generate deterministic, provider-free review evidence for Checkpoint 001."""

from __future__ import annotations

import json
import shutil
import subprocess
import urllib.request
from pathlib import Path

from cera.active_runtime_validation import active_runtime_status
from cera.providers.routes import (
    codex_cli_realization_verifier_candidate,
    codex_reasoner_candidate,
    deepseek_composer_candidate,
)
from cera.serialization import to_primitive


BASELINE = "fb3eb586f0b68fb65ea5bea5eb93a93d4f83cfd4"
CHECKPOINT = "248dfbc969a2961338d8f9b35c61bda4f4e6010b"
ROOT = Path(__file__).resolve().parents[6]
OUT = Path(__file__).resolve().parents[1]
BACKUP = Path(r"D:\AIChatBot\Cera_Backups\2026-07-31-checkpoint-001-pre-stabilization")


def run_git(*args: str, binary: bool = False) -> bytes | str:
    completed = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    if binary:
        return completed.stdout
    return completed.stdout.decode("utf-8", errors="strict")


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8", newline="\n")


def write_json(path: Path, value: object) -> None:
    write_text(path, json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def main() -> None:
    for directory in (
        "request",
        "git",
        "source",
        "profile",
        "runtime",
        "baseline",
    ):
        (OUT / directory).mkdir(parents=True, exist_ok=True)

    checkpoint_root = OUT.parent
    shutil.copy2(checkpoint_root / "REQUEST.md", OUT / "request" / "REQUEST.md")
    shutil.copy2(checkpoint_root / "PRO_RESPONSE.md", OUT / "request" / "PRO_RESPONSE.md")

    write_text(OUT / "git" / "BASELINE_TO_CHECKPOINT.patch", run_git("diff", "--binary", BASELINE, CHECKPOINT))
    write_text(OUT / "git" / "CHANGED_FILES.name-status.txt", run_git("diff", "--name-status", BASELINE, CHECKPOINT))
    write_text(OUT / "git" / "DIFF_STAT.txt", run_git("diff", "--stat", BASELINE, CHECKPOINT))
    write_text(OUT / "git" / "CHECKPOINT_SHOW.txt", run_git("show", "--summary", "--stat", CHECKPOINT))
    write_text(OUT / "git" / "CURRENT_STATUS.txt", run_git("status", "--short", "--branch"))
    write_text(OUT / "git" / "REMOTES.txt", run_git("remote", "-v"))
    write_text(OUT / "git" / "HISTORICAL_EVIDENCE_DIFF.txt", run_git("diff", "--name-status", BASELINE, CHECKPOINT, "--", "evaluation/evidence"))

    changed = [
        line for line in run_git("diff", "--name-only", BASELINE, CHECKPOINT).splitlines() if line
    ]
    archive_path = OUT / "source" / "CHANGED_FILES_AT_CHECKPOINT.zip"
    subprocess.run(
        ["git", "archive", "--format=zip", f"--output={archive_path}", CHECKPOINT, *changed],
        cwd=ROOT,
        check=True,
    )

    profile = active_runtime_status()
    write_json(OUT / "profile" / "ACTIVE_RUNTIME_PROFILE.json", profile)
    write_text(OUT / "profile" / "ACTIVE_RUNTIME_PROFILE.sha256", f"{profile['profile_sha256']}\n")

    routes = {}
    for name, route in (
        ("reasoner", codex_reasoner_candidate()),
        ("composer", deepseek_composer_candidate()),
        ("verifier", codex_cli_realization_verifier_candidate()),
    ):
        routes[name] = {
            "route": to_primitive(route),
            "route_sha256": route.route_sha256,
        }
    write_json(OUT / "runtime" / "ROUTE_SAMPLES.json", routes)

    try:
        with urllib.request.urlopen("http://127.0.0.1:5101/health", timeout=5) as response:
            health = json.loads(response.read().decode("utf-8"))
    except Exception as exc:  # evidence must preserve explicit unavailability
        health = {"status": "unavailable", "error_type": type(exc).__name__, "message": str(exc)}
    write_json(OUT / "runtime" / "LOOPBACK_HEALTH.json", health)
    if isinstance(health, dict) and "reasoner_session" in health:
        write_json(OUT / "runtime" / "REASONER_SESSION_STATUS.json", health["reasoner_session"])

    historical_path = ROOT / "evaluation" / "evidence" / "native_stored_reasoner_live_five_2026-07-31_v4" / "five_run.json"
    historical = json.loads(historical_path.read_text(encoding="utf-8"))
    first_turn = historical["turns"][0]
    receipt_sample = {
        "sample_kind": "privacy_safe_historical_receipt",
        "source_relative_path": str(historical_path.relative_to(ROOT)).replace("\\", "/"),
        "source_file_sha256_note": "See MANIFEST.sha256 for copied sample hash; source is unchanged historical evidence.",
        "new_provider_calls_for_export": 0,
        "provider_receipt": first_turn["provider_receipt"],
        "session_receipt_context": {
            "candidate_thread_sha256": first_turn["candidate_thread_sha256"],
            "parent_thread_sha256": first_turn["parent_thread_sha256"],
            "benchmark_parent_promoted": first_turn["benchmark_parent_promoted"],
            "status": first_turn["status"],
        },
    }
    write_json(OUT / "runtime" / "HISTORICAL_RECEIPT_SAMPLE.json", receipt_sample)

    shutil.copy2(ROOT / "docs" / "operations" / "REPOSITORY_BASELINE_INVENTORY_2026-07-31.md", OUT / "baseline" / "REPOSITORY_BASELINE_INVENTORY_2026-07-31.md")
    write_text(OUT / "baseline" / "BASELINE.gitignore", run_git("show", f"{BASELINE}:.gitignore"))
    write_text(OUT / "baseline" / "CHECKPOINT.gitignore", run_git("show", f"{CHECKPOINT}:.gitignore"))
    write_text(OUT / "baseline" / "SOURCE_INVENTORY_DIFF.patch", run_git("diff", BASELINE, CHECKPOINT, "--", ".gitignore", "src/cera/source_inventory.py", "tests/test_documentation.py"))

    backup_files = [path for path in BACKUP.rglob("*") if path.is_file()]
    backup_observation = {
        "backup_root": str(BACKUP),
        "exists": BACKUP.is_dir(),
        "observed_file_count": len(backup_files),
        "observed_total_bytes": sum(path.stat().st_size for path in backup_files),
        "inventory_expected_file_count": 5044,
        "inventory_expected_total_bytes": 565_577_272,
        "observed_counts_match_inventory": (
            len(backup_files) == 5044
            and sum(path.stat().st_size for path in backup_files) == 565_577_272
        ),
        "inventory_manifest_sha256": "43ac1d1056b38cec8356a0c53bca37233094bd9b3252ccf3b74daf2e7c0f0dbd",
        "note": "The inventory records the original 5044-of-5044 content-hash comparison. This export rechecks backup presence, count, and size without mutating it.",
    }
    write_json(OUT / "baseline" / "BACKUP_OBSERVATION.json", backup_observation)

    authority_lines = []
    for relative_path, wanted in (
        ("AGENTS.md", range(70, 89)),
        ("docs/authority/CODEX_PROGRESS_REVIEW_PROTOCOL.md", range(94, 121)),
        ("docs/authority/CODEX_PROGRESS_REVIEW_PROTOCOL.md", range(198, 220)),
    ):
        lines = (ROOT / relative_path).read_text(encoding="utf-8").splitlines()
        authority_lines.append(f"--- {relative_path} ---")
        authority_lines.extend(
            f"{number:04d}: {lines[number - 1]}"
            for number in wanted
            if number <= len(lines)
        )
    write_text(OUT / "review" / "AUTHORITY_SNIPPETS.txt", "\n".join(authority_lines) + "\n")


if __name__ == "__main__":
    main()
